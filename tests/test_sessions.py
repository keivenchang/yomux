import json
import os
import subprocess
from pathlib import Path

from yolomux_lib import sessions
from yolomux_lib.common import AgentInfo, PaneInfo, ProcessInfo
from yolomux_lib.sessions import list_processes
from yolomux_lib.sessions import pane_process_label
from yolomux_lib.server_logs import SERVER_LOGS


def clear_transcript_lookup_cache():
    # the lookup cache is now a shared TtlCache (was a hand-rolled dict + lock).
    sessions._TRANSCRIPT_LOOKUP_CACHE.clear()
    sessions._PROCESS_LSOF_PATH_CACHE.clear()


def test_list_processes_uses_bsd_ps_command_keyword(monkeypatch):
    calls = []

    def fake_run_cmd(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="10 1 python3 yolomux.py\n", stderr="")

    monkeypatch.setattr(sessions, "run_cmd", fake_run_cmd)

    processes, error = sessions.list_processes()

    assert error is None
    assert calls == [(["ps", "-eww", "-o", "pid=,ppid=,command="], 8.0)]
    assert processes[10] == ProcessInfo(pid=10, ppid=1, command="python3 yolomux.py")


def test_classify_agent_requires_an_agent_entry_point():
    assert sessions.classify_agent("/Users/me/.local/bin/claude --resume abc") == "claude"
    assert sessions.classify_agent("node /home/me/.local/bin/codex resume abc") == "codex"
    assert sessions.classify_agent("python3 tools/claude.py --mock") == "claude"
    assert sessions.classify_agent("python3 tools/codex.py --mock") == "codex"
    assert sessions.classify_agent("rg -n claude yolomux_lib tests") is None
    assert sessions.classify_agent("python3 -m pytest -k codex") is None
    assert sessions.classify_agent("git commit -m 'fix claude notifications'") is None


def test_find_recent_codex_transcript_matches_session_meta_header(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-header.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    lines = [json.dumps({"type": "session_meta", "payload": {"cwd": cwd}})]
    lines.extend(json.dumps({"type": "event", "message": f"line {index}"}) for index in range(400))
    transcript.write_text("\n".join(lines), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_codex_transcript_family_paths_includes_spawned_descendants_only(tmp_path):
    def rollout(name, thread_id, parent_thread_id=""):
        path = tmp_path / f"rollout-{name}.jsonl"
        payload = {"id": thread_id}
        if parent_thread_id:
            payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n", encoding="utf-8")
        return path

    parent = rollout("parent", "parent")
    child = rollout("child", "child", "parent")
    grandchild = rollout("grandchild", "grandchild", "child")
    unrelated = rollout("unrelated", "unrelated", "other")

    assert sessions.codex_transcript_family_paths(parent, [unrelated, child, grandchild]) == [parent, child, grandchild]

    missing_meta = tmp_path / "rollout-no-meta.jsonl"
    missing_meta.write_text('{"type":"event"}\n', encoding="utf-8")
    assert sessions.codex_transcript_family_paths(missing_meta, [child]) == [missing_meta]


def test_find_recent_codex_transcript_keeps_structured_tail_fallback(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-tail.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text("\n".join([json.dumps({"type": "session_meta", "payload": {"cwd": "/other"}}), json.dumps({"cwd": cwd})]), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript


def test_find_recent_codex_transcript_ignores_plain_tail_mentions(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-tail.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/other"}}),
        json.dumps({"message": f"the user mentioned {cwd}, but no tool ran there"}),
    ]), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(cwd, root=root) is None


def test_find_recent_codex_transcript_refuses_global_newest_without_cwd(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-newest.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/repo/other"}}), encoding="utf-8")

    assert sessions.find_recent_codex_transcript(None, root=root) is None
    assert sessions.find_recent_codex_transcript("", root=root) is None
    assert sessions.find_recent_codex_transcript("/repo/project", root=root) is None


def test_find_recent_codex_transcript_caches_cwd_lookup(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-header.jsonl"
    transcript.parent.mkdir(parents=True)
    cwd = "/repo/project"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}), encoding="utf-8")
    calls = []
    original_header = sessions.codex_transcript_header_cwd
    monkeypatch.setattr(sessions, "codex_transcript_header_cwd", lambda path: calls.append(path) or original_header(path))

    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript
    assert sessions.find_recent_codex_transcript(cwd, root=root) == transcript

    assert calls == [transcript]


def test_find_recent_codex_transcript_orders_by_name_without_stat_storm(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "06" / "01"
    day.mkdir(parents=True)
    cwd = "/repo/project"
    older = day / "rollout-2026-06-01T08-00-00-aaaa.jsonl"
    newer = day / "rollout-2026-06-01T10-00-00-bbbb.jsonl"
    older.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/repo/other"}}), encoding="utf-8")
    newer.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}), encoding="utf-8")

    stat_calls: list[Path] = []
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda self, *args, **kwargs: (stat_calls.append(self) or original_stat(self, *args, **kwargs)))

    # A single matching rollout still avoids per-rollout-file stat() calls.
    assert sessions.find_recent_codex_transcript(cwd, root=root) == newer
    assert [path for path in stat_calls if path.name.startswith("rollout-")] == []


def test_find_recent_codex_transcript_prefers_newest_mtime_among_same_cwd_matches(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    cwd = "/repo/resumed"
    old_day = root / "2026" / "06" / "08"
    new_day = root / "2026" / "06" / "12"
    old_day.mkdir(parents=True)
    new_day.mkdir(parents=True)
    resumed = old_day / "rollout-2026-06-08T08-00-00-old.jsonl"
    stale = new_day / "rollout-2026-06-12T10-00-00-new.jsonl"
    for path in (resumed, stale):
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}), encoding="utf-8")
    os.utime(stale, (1000, 1000))
    os.utime(resumed, (5000, 5000))

    assert sessions.find_recent_codex_transcript(cwd, root=root) == resumed


def test_find_recent_codex_transcript_falls_back_to_mtime_for_resumed_old_file(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    cwd = "/repo/resumed"
    old_day = root / "2026" / "06" / "08"
    old_day.mkdir(parents=True)
    resumed = old_day / "rollout-2026-06-08T08-00-00-old.jsonl"
    resumed.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/repo/other"}}),
        json.dumps({"payload": {"arguments": json.dumps({"workdir": cwd})}}),
    ]), encoding="utf-8")
    new_day = root / "2026" / "06" / "12"
    new_day.mkdir(parents=True)
    for index in range(sessions.CODEX_TRANSCRIPT_SCAN_LIMIT + 5):
        path = new_day / f"rollout-2026-06-12T10-{index:02d}-new.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/repo/new"}}), encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    os.utime(resumed, (5000, 5000))

    assert sessions.find_recent_codex_transcript(cwd, root=root) == resumed


def test_find_transcript_by_session_id_caches_glob(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "claude" / "projects"
    transcript = root / "repo" / "session-123.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    glob_calls = []
    original_glob = Path.glob

    def counted_glob(self, pattern):
        if self == root:
            glob_calls.append(pattern)
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counted_glob)

    assert sessions.find_transcript_by_session_id(root, "session-123") == transcript
    assert sessions.find_transcript_by_session_id(root, "session-123") == transcript

    # The lookup resolves through the per-directory catalog now; the recursive
    # glob of the whole projects tree is retired.
    assert glob_calls == []


def test_read_claude_agent_prefers_latest_transcript_cwd(tmp_path):
    clear_transcript_lookup_cache()
    claude_root = tmp_path / ".claude"
    sessions_root = claude_root / "sessions"
    projects_root = claude_root / "projects"
    project_root = projects_root / "-home-user"
    sessions_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    transcript = project_root / "session-120.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"type": "user", "cwd": str(home), "sessionId": "session-120"}),
            json.dumps({
                "type": "assistant",
                "cwd": str(repo),
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]},
                "sessionId": "session-120",
            }),
        ]),
        encoding="utf-8",
    )
    (sessions_root / "101.json").write_text(json.dumps({
        "pid": 101,
        "sessionId": "session-120",
        "cwd": str(home),
        "status": "busy",
    }), encoding="utf-8")

    agent = sessions.read_claude_agent(
        "120",
        _pane(101),
        ProcessInfo(pid=101, ppid=100, command="claude"),
        sessions_root=sessions_root,
        projects_root=projects_root,
    )

    assert agent.transcript == str(transcript)
    assert agent.cwd == str(repo)


def test_lightweight_status_discovery_skips_transcript_and_process_path_enrichment(monkeypatch, tmp_path):
    pane = _pane(100)
    processes = {
        100: ProcessInfo(pid=100, ppid=1, command="bash"),
        101: ProcessInfo(pid=101, ppid=100, command="claude"),
    }
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    (sessions_root / "101.json").write_text(json.dumps({"sessionId": "session-101", "cwd": "/repo", "status": "busy"}), encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(sessions, "list_tmux_panes", lambda: ([pane], None))
    monkeypatch.setattr(sessions, "list_processes", lambda: (processes, None))
    monkeypatch.setattr(sessions, "find_transcript_by_session_id", lambda *_args: calls.append("transcript") or None)
    monkeypatch.setattr(sessions, "claude_transcript_latest_cwd", lambda *_args: calls.append("tail") or None)
    original_select = sessions.select_claude_agent
    monkeypatch.setattr(
        sessions,
        "select_claude_agent",
        lambda session, item, candidates, *, enrich_paths=True: original_select(
            session, item, candidates, sessions_root=sessions_root, projects_root=tmp_path / "projects", enrich_paths=enrich_paths,
        ),
    )

    discovered, errors = sessions.discover_status_sessions(["1"])

    assert errors == []
    agent = discovered["1"].agents[0]
    assert (agent.kind, agent.pid, agent.pane_target, agent.model, agent.session_id) == ("claude", 101, pane.target, None, "session-101")
    assert agent.cwd == "/repo"
    assert calls == []


def test_select_claude_agent_follows_daemon_delegated_session(tmp_path):
    clear_transcript_lookup_cache()
    claude_root = tmp_path / ".claude"
    sessions_root = claude_root / "sessions"
    projects_root = claude_root / "projects"
    project_root = projects_root / "-repo"
    sessions_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    old_transcript = project_root / "old-session.jsonl"
    current_transcript = project_root / "current-session.jsonl"
    old_transcript.write_text("{}\n", encoding="utf-8")
    current_transcript.write_text("{}\n", encoding="utf-8")
    os.utime(old_transcript, (1000, 1000))
    os.utime(current_transcript, (2000, 2000))
    (sessions_root / "101.json").write_text(json.dumps({
        "pid": 101,
        "sessionId": "old-session",
        "cwd": "/repo",
        "kind": "interactive",
        "status": "busy",
        "startedAt": 900_000,
        "updatedAt": 1000_000,
    }), encoding="utf-8")
    (sessions_root / "104.json").write_text(json.dumps({
        "pid": 104,
        "sessionId": "current-session",
        "cwd": "/repo",
        "kind": "bg",
        "status": "busy",
        "startedAt": 1900_000,
        "updatedAt": 2000_000,
    }), encoding="utf-8")
    candidates = [
        ProcessInfo(pid=101, ppid=100, command="claude --dangerously-skip-permissions"),
        ProcessInfo(pid=102, ppid=101, command="claude daemon run"),
        ProcessInfo(pid=103, ppid=102, command="2.1.198 --bg-pty-host current.sock"),
        ProcessInfo(pid=104, ppid=103, command="2.1.198 --session-id current-session --resume old-session.jsonl"),
    ]

    agent = sessions.select_claude_agent(
        "1",
        _pane(100),
        candidates,
        sessions_root=sessions_root,
        projects_root=projects_root,
    )

    assert agent is not None
    assert agent.pid == 104
    assert agent.session_id == "current-session"
    assert agent.transcript == str(current_transcript)


def test_discover_sessions_emits_one_claude_agent_for_launcher_and_daemon_descendant(monkeypatch):
    pane = _pane(100)
    processes = {
        100: ProcessInfo(pid=100, ppid=1, command="bash"),
        101: ProcessInfo(pid=101, ppid=100, command="claude --dangerously-skip-permissions"),
        102: ProcessInfo(pid=102, ppid=101, command="claude daemon run"),
        103: ProcessInfo(pid=103, ppid=102, command="2.1.198 --session-id current-session"),
    }
    selected = AgentInfo(
        session="1",
        kind="claude",
        pid=103,
        pane_target=pane.target,
        command=processes[103].command,
        cwd="/repo",
        status="busy",
        session_id="current-session",
        transcript="/repo/current-session.jsonl",
        error=None,
    )
    monkeypatch.setattr(sessions, "list_tmux_panes", lambda: ([pane], None))
    monkeypatch.setattr(sessions, "list_processes", lambda: (processes, None))
    monkeypatch.setattr(sessions, "select_claude_agent", lambda _session, _pane, _processes: selected)

    discovered, errors = sessions.discover_sessions(["1"])

    assert errors == []
    assert discovered["1"].agents == [selected]


def test_discover_sessions_uses_one_canonical_agent_for_pane(monkeypatch):
    pane = _pane(100)
    processes = {
        100: ProcessInfo(pid=100, ppid=1, command="bash"),
        101: ProcessInfo(pid=101, ppid=100, command="node /home/me/.local/bin/codex resume abc"),
        102: ProcessInfo(pid=102, ppid=101, command="/opt/codex resume abc"),
        103: ProcessInfo(pid=103, ppid=102, command="rg -n claude yolomux_lib tests"),
        104: ProcessInfo(pid=104, ppid=102, command="python3 -m pytest -k codex"),
        105: ProcessInfo(pid=105, ppid=102, command="git commit -m 'fix claude notifications'"),
    }
    selected = AgentInfo(
        session="1",
        kind="codex",
        pid=102,
        pane_target=pane.target,
        command=processes[102].command,
        cwd="/repo",
        status=None,
        session_id="codex-session",
        transcript="/repo/rollout.jsonl",
        error=None,
    )
    selected_pids = []
    monkeypatch.setattr(sessions, "list_tmux_panes", lambda: ([pane], None))
    monkeypatch.setattr(sessions, "list_processes", lambda: (processes, None))
    monkeypatch.setattr(
        sessions,
        "read_codex_agent",
        lambda _session, _pane, process: selected_pids.append(process.pid) or selected,
    )

    discovered, errors = sessions.discover_sessions(["1"])

    assert errors == []
    assert selected_pids == [102]
    assert discovered["1"].agents == [selected]


def test_claude_transcript_family_paths_include_nested_subagents(tmp_path):
    transcript = tmp_path / "session-id.jsonl"
    direct = tmp_path / "session-id" / "subagents" / "agent-direct.jsonl"
    nested = tmp_path / "session-id" / "subagents" / "nested" / "agent-nested.jsonl"
    unrelated = tmp_path / "other.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    direct.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    direct.write_text("{}\n", encoding="utf-8")
    nested.write_text("{}\n", encoding="utf-8")
    unrelated.write_text("{}\n", encoding="utf-8")

    assert sessions.claude_transcript_family_paths(transcript) == [transcript, direct, nested]


def test_codex_transcript_from_process_fd_prefers_open_rollout(tmp_path):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-owned.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside" / "rollout-other.jsonl"
    outside.parent.mkdir()
    outside.write_text("{}\n", encoding="utf-8")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    (fd_dir / "3").symlink_to(outside)
    (fd_dir / "4").symlink_to(transcript)
    (fd_dir / "5").symlink_to(root / "2026" / "05" / "notes.txt")

    assert sessions.codex_transcript_from_process_fd(123, root=root, fd_dir=fd_dir) == transcript


def test_codex_transcript_from_process_fd_accepts_deleted_suffix(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-owned.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    entry = fd_dir / "3"
    entry.write_text("", encoding="utf-8")

    def fake_readlink(path):
        assert path == entry
        return f"{transcript} (deleted)"

    monkeypatch.setattr(sessions.os, "readlink", fake_readlink)

    assert sessions.codex_transcript_from_process_fd(123, root=root, fd_dir=fd_dir) == transcript


def test_codex_transcript_from_process_fd_uses_darwin_libproc_without_lsof(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    old = root / "2026" / "05" / "rollout-old.jsonl"
    live = root / "2026" / "06" / "rollout-live.jsonl"
    outside = tmp_path / "outside" / "rollout-other.jsonl"
    for path in (old, live, outside):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(live, (5000, 5000))
    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sessions, "_darwin_process_open_paths", lambda pid: [str(old), str(outside), str(live)])

    def unexpected_lsof(*_args, **_kwargs):
        raise AssertionError("Darwin transcript lookup must not call lsof when libproc succeeds")

    assert sessions.codex_transcript_from_process_fd(123, root=root, lsof_runner=unexpected_lsof) == live


def test_codex_transcript_from_process_fd_falls_back_to_lsof_with_deduped_warning(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    SERVER_LOGS.clear()
    root = tmp_path / "codex" / "sessions"
    live = root / "2026" / "06" / "rollout-live.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text("{}\n", encoding="utf-8")
    calls = []

    def lsof_runner(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, f"p123\nf6\nn{live}\n", "")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sessions, "_darwin_process_open_paths", lambda pid: None)

    assert sessions.codex_transcript_from_process_fd(123, root=root, lsof_runner=lsof_runner) == live
    assert sessions.codex_transcript_from_process_fd(123, root=root, lsof_runner=lsof_runner) == live
    assert calls == [(["lsof", "-p", "123", "-a", "-d", sessions.CODEX_LSOF_TRANSCRIPT_DESCRIPTOR_FILTER, "-Fn"], sessions.CODEX_LSOF_TIMEOUT_SECONDS)]
    warnings = [entry for entry in SERVER_LOGS.payload()["logs"] if entry["level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["source"] == "sessions"
    assert "transcript" in warnings[0]["message"] and "pid 123" in warnings[0]["message"]


def test_codex_transcript_from_process_fd_keeps_linux_proc_path(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / "codex" / "sessions"
    transcript = root / "2026" / "05" / "rollout-owned.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    (fd_dir / "4").symlink_to(transcript)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")

    def unexpected_lsof(*_args, **_kwargs):
        raise AssertionError("Linux /proc lookup must not call lsof")

    assert sessions.codex_transcript_from_process_fd(123, root=root, fd_dir=fd_dir, lsof_runner=unexpected_lsof) == transcript


def test_process_cwd_uses_darwin_libproc_before_lsof(tmp_path, monkeypatch):
    expected = tmp_path / "repo"
    expected.mkdir()

    def unexpected_lsof(*_args, **_kwargs):
        raise AssertionError("Darwin process_cwd must not call lsof when libproc succeeds")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sessions, "_darwin_process_cwd", lambda pid: str(expected))

    assert sessions.process_cwd(123, lsof_runner=unexpected_lsof) == str(expected)


def test_process_cwd_falls_back_to_darwin_lsof(tmp_path, monkeypatch):
    SERVER_LOGS.clear()
    expected = tmp_path / "repo"
    expected.mkdir()
    calls = []

    def lsof_runner(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, f"p123\nfwd\nn{expected}\n", "")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sessions, "_darwin_process_cwd", lambda pid: None)

    assert sessions.process_cwd(123, lsof_runner=lsof_runner) == str(expected)
    assert sessions.process_cwd(123, lsof_runner=lsof_runner) == str(expected)
    assert calls == [(["lsof", "-p", "123", "-a", "-d", "cwd", "-Fn"], sessions.CODEX_LSOF_TIMEOUT_SECONDS)]
    warnings = [entry for entry in SERVER_LOGS.payload()["logs"] if entry["level"] == "warning"]
    assert len(warnings) == 1
    assert "cwd" in warnings[0]["message"] and "pid 123" in warnings[0]["message"]


def test_discover_sessions_uses_darwin_libproc_for_each_codex_process(tmp_path, monkeypatch):
    clear_transcript_lookup_cache()
    root = tmp_path / ".codex" / "sessions"
    first = root / "2026" / "07" / "rollout-first.jsonl"
    second = root / "2026" / "07" / "rollout-second.jsonl"
    for path, session_id in ((first, "codex-first"), (second, "codex-second")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": session_id}}) + "\n", encoding="utf-8")

    panes = [
        PaneInfo(session="1", window="0", pane="0", pane_id="%1", target="%1", current_path="/repo/one", command="bash", active=True, window_active=True, title="", pid=100),
        PaneInfo(session="2", window="0", pane="0", pane_id="%2", target="%2", current_path="/repo/two", command="bash", active=True, window_active=True, title="", pid=200),
    ]
    processes = {
        100: ProcessInfo(pid=100, ppid=1, command="bash"),
        101: ProcessInfo(pid=101, ppid=100, command="codex"),
        200: ProcessInfo(pid=200, ppid=1, command="bash"),
        201: ProcessInfo(pid=201, ppid=200, command="codex"),
    }
    calls = []

    def fake_open_paths(pid):
        calls.append(pid)
        return {101: [str(first)], 201: [str(second)]}[pid]

    def unexpected_lsof(*_args, **_kwargs):
        raise AssertionError("Darwin discovery must not call lsof when each libproc lookup succeeds")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sessions, "list_tmux_panes", lambda: (panes, None))
    monkeypatch.setattr(sessions, "list_processes", lambda: (processes, None))
    monkeypatch.setattr(sessions, "_darwin_process_cwd", lambda pid: "/repo")
    monkeypatch.setattr(sessions, "_darwin_process_open_paths", fake_open_paths)
    monkeypatch.setattr(sessions, "lsof_paths_for_process", unexpected_lsof)

    discovered, errors = sessions.discover_sessions(["1", "2"])

    assert errors == []
    assert calls == [101, 201]
    assert [agent.session_id for agent in discovered["1"].agents + discovered["2"].agents] == ["codex-first", "codex-second"]


def test_codex_transcript_session_id_reads_session_meta_payload_id(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"type": "session_meta", "payload": {"id": "codex-session-123", "cwd": "/repo"}}),
            json.dumps({"type": "event", "payload": {"id": "not-the-session"}}),
        ]),
        encoding="utf-8",
    )

    assert sessions.codex_transcript_session_id(transcript) == "codex-session-123"


def test_read_codex_agent_uses_transcript_session_id(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"id": "codex-session-456", "cwd": "/repo"}}), encoding="utf-8")
    monkeypatch.setattr(sessions, "process_cwd", lambda pid: "/repo")
    monkeypatch.setattr(sessions, "codex_transcript_from_process_fd", lambda pid: transcript)

    agent = sessions.read_codex_agent("1", _pane(100), ProcessInfo(pid=100, ppid=1, command="codex"))

    assert agent.session_id == "codex-session-456"
    assert agent.transcript == str(transcript)


def _pane(pid=100):
    return PaneInfo(
        session="1",
        window="0",
        pane="0",
        pane_id="%1",
        target="1:0.0",
        current_path="/repo",
        command="bash",
        active=True,
        window_active=True,
        title="",
        pid=pid,
    )


def test_active_window_for_panes_supports_typed_and_cached_payloads():
    inactive = _pane(100)
    inactive = PaneInfo(**{**inactive.__dict__, "window": "0", "window_active": False})
    active = _pane(101)
    active = PaneInfo(**{**active.__dict__, "window": "2", "window_active": True})
    assert sessions.active_window_for_panes([inactive, active]) == "2"
    assert sessions.active_window_for_panes([
        {"window": "0", "window_active": False},
        {"window": "3", "window_active": True},
    ]) == "3"


def test_pane_process_label_returns_displayed_process_pid():
    label, pid = pane_process_label(
        _pane(100),
        [
            ProcessInfo(pid=100, ppid=1, command="bash"),
            ProcessInfo(pid=321, ppid=100, command="python3 mock_server.py"),
        ],
    )

    assert label == "mock_server.py"
    assert pid == 321


def test_pane_process_label_recognizes_merged_mock_entrypoints():
    label, pid = pane_process_label(
        _pane(100),
        [
            ProcessInfo(pid=100, ppid=1, command="bash"),
            ProcessInfo(pid=321, ppid=100, command="python3 tools/codex.py --mock"),
        ],
    )
    assert label == "codex"
    assert pid == 321

    label, pid = pane_process_label(
        _pane(100),
        [
            ProcessInfo(pid=100, ppid=1, command="bash"),
            ProcessInfo(pid=654, ppid=100, command="python3 tools/claude.py --mock"),
        ],
    )
    assert label == "claude"
    assert pid == 654


def test_pane_process_label_falls_back_to_pane_pid_for_shell():
    label, pid = pane_process_label(_pane(100), [ProcessInfo(pid=100, ppid=1, command="bash")])

    assert label == "bash"
    assert pid == 100


def test_list_processes_uses_portable_command_field_for_macos_wrappers(monkeypatch):
    calls = []

    def fake_run_cmd(args, timeout=0):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "100 1 -zsh\n321 100 /Users/me/.local/bin/claude --dangerously-skip-permissions\n", ""
        )

    monkeypatch.setattr(sessions, "run_cmd", fake_run_cmd)

    processes, error = list_processes()

    assert error is None
    assert calls == [["ps", "-eww", "-o", "pid=,ppid=,command="]]
    assert processes[100] == ProcessInfo(pid=100, ppid=1, command="-zsh")
    assert processes[321] == ProcessInfo(
        pid=321,
        ppid=100,
        command="/Users/me/.local/bin/claude --dangerously-skip-permissions",
    )


def test_rollout_catalog_relists_only_changed_directories(tmp_path, monkeypatch):
    """A warm candidate scan must not re-list every dated directory: unchanged
    directories are served from the per-directory mtime cache, and a new rollout
    appears because only ITS day directory re-lists."""
    root = tmp_path / "sessions"
    day_one = root / "2026" / "07" / "15"
    day_two = root / "2026" / "07" / "16"
    day_one.mkdir(parents=True)
    day_two.mkdir(parents=True)
    (day_one / "rollout-2026-07-15T01-old.jsonl").write_text("{}\n", encoding="utf-8")
    (day_two / "rollout-2026-07-16T01-new.jsonl").write_text("{}\n", encoding="utf-8")

    sessions._TRANSCRIPT_DIR_CATALOG.clear()
    scans = []
    real_scandir = os.scandir

    def counting_scandir(path):
        scans.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(sessions.os, "scandir", counting_scandir)
    first = {path.name for path in sessions._codex_rollout_files(root)}
    assert first == {"rollout-2026-07-15T01-old.jsonl", "rollout-2026-07-16T01-new.jsonl"}
    cold_scans = len(scans)
    assert cold_scans >= 5  # root + year + month + two day dirs

    # Warm, unchanged tree: zero directory listings (stats only).
    scans.clear()
    assert {path.name for path in sessions._codex_rollout_files(root)} == first
    assert scans == []

    # A new rollout bumps only its day directory's mtime -> exactly one re-list.
    (day_two / "rollout-2026-07-16T02-newer.jsonl").write_text("{}\n", encoding="utf-8")
    scans.clear()
    warm = {path.name for path in sessions._codex_rollout_files(root)}
    assert "rollout-2026-07-16T02-newer.jsonl" in warm
    assert scans == [str(day_two)]


def test_recent_claude_candidates_are_top_level_bounded_and_mtime_aware(tmp_path):
    project = tmp_path / "-Users-someone-repo"
    project.mkdir()
    files = {name: project / f"{name}.jsonl" for name in ("a", "b", "c", "d")}
    for index, path in enumerate(files.values(), 1):
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (index, index))
    subagent = project / "a" / "subagents" / "agent-newer.jsonl"
    subagent.parent.mkdir(parents=True)
    subagent.write_text("{}\n", encoding="utf-8")
    os.utime(subagent, (100, 100))
    sessions._TRANSCRIPT_DIR_CATALOG.clear()

    candidates = sessions.recent_claude_transcript_candidates(project, limit=2)

    assert [path.name for path in candidates] == ["d.jsonl", "c.jsonl"]
    assert subagent not in candidates
    files["a"].write_text("{}\n{}\n", encoding="utf-8")
    os.utime(files["a"], (200, 200))
    assert [path.name for path in sessions.recent_claude_transcript_candidates(project, limit=2)] == [
        "d.jsonl", "c.jsonl", "a.jsonl",
    ]


def test_recent_codex_candidate_order_is_unchanged_by_shared_selector(tmp_path):
    root = tmp_path / "sessions"
    day = root / "2026" / "07" / "16"
    day.mkdir(parents=True)
    files = {
        name: day / f"rollout-{name}.jsonl"
        for name in ("a", "b", "c", "d")
    }
    for index, path in enumerate(files.values(), 1):
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (index, index))
    os.utime(files["a"], (100, 100))
    sessions._TRANSCRIPT_DIR_CATALOG.clear()

    assert [path.name for path in sessions.recent_codex_transcript_candidates(root, limit=2)] == [
        "rollout-d.jsonl", "rollout-c.jsonl", "rollout-a.jsonl",
    ]


def test_claude_session_id_lookup_uses_catalog_not_recursive_glob(tmp_path, monkeypatch):
    """find_transcript_by_session_id must resolve through the shared per-directory
    catalog: a warm repeat lookup re-lists no directories, and the recursive
    Path.glob is never used."""
    projects = tmp_path / "projects"
    slug = projects / "-Users-someone-repo"
    slug.mkdir(parents=True)
    (slug / "abc-123.jsonl").write_text("{}\n", encoding="utf-8")

    sessions._TRANSCRIPT_DIR_CATALOG.clear()
    sessions._TRANSCRIPT_LOOKUP_CACHE.clear()
    monkeypatch.setattr(Path, "glob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("recursive glob is retired for claude session-id lookup")))

    found = sessions.find_transcript_by_session_id(projects, "abc-123")
    assert found == slug / "abc-123.jsonl"
    assert sessions.find_transcript_by_session_id(projects, "missing") is None

    # Warm catalog: after the 2s lookup cache expires, the re-resolve lists nothing.
    sessions._TRANSCRIPT_LOOKUP_CACHE.clear()
    scans = []
    real_scandir = os.scandir

    def counting_scandir(path):
        scans.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(sessions.os, "scandir", counting_scandir)
    assert sessions.find_transcript_by_session_id(projects, "abc-123") == slug / "abc-123.jsonl"
    assert scans == []


def test_codex_transcript_meta_caches_by_identity_but_not_empty_meta(tmp_path):
    """session_meta never changes after it is written, so it is cached by file
    identity (no re-read per stats pass) — but an empty result (meta line not
    yet flushed) must keep re-reading until the thread id appears."""
    rollout = tmp_path / "rollout-2026-07-16T01-abc.jsonl"
    rollout.write_text("", encoding="utf-8")
    sessions._CODEX_TRANSCRIPT_META_CACHE.clear()
    assert sessions.codex_transcript_meta(rollout) == ("", "")

    meta_line = json.dumps({"type": "session_meta", "payload": {"id": "thread-1", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent-9"}}}}})
    rollout.write_text(meta_line + "\n", encoding="utf-8")
    assert sessions.codex_transcript_meta(rollout) == ("thread-1", "parent-9")

    # Cached by identity: further appends read zero header bytes.
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "turn"}) + "\n")
    reads = []
    original_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self == rollout:
            reads.append(args)
        return original_open(self, *args, **kwargs)

    Path.open = counting_open
    try:
        assert sessions.codex_transcript_meta(rollout) == ("thread-1", "parent-9")
    finally:
        Path.open = original_open
    assert reads == []

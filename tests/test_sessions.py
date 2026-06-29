import json
import os
import subprocess
from pathlib import Path

from yolomux_lib import sessions
from yolomux_lib.common import PaneInfo, ProcessInfo
from yolomux_lib.sessions import list_processes
from yolomux_lib.sessions import pane_process_label


def clear_transcript_lookup_cache():
    # the lookup cache is now a shared TtlCache (was a hand-rolled dict + lock).
    sessions._TRANSCRIPT_LOOKUP_CACHE.clear()


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

    assert glob_calls == ["**/session-123.jsonl"]


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


def test_codex_transcript_from_process_fd_uses_darwin_lsof_and_caches_result(tmp_path, monkeypatch):
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
    calls = []

    def lsof_runner(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, f"p123\nf4\nn{old}\nf5\nn{outside}\nf6\nn{live}\n", "")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")

    assert sessions.codex_transcript_from_process_fd(123, root=root, lsof_runner=lsof_runner) == live
    assert sessions.codex_transcript_from_process_fd(123, root=root, lsof_runner=lsof_runner) == live
    assert calls == [(["lsof", "-p", "123", "-Fn"], sessions.CODEX_LSOF_TIMEOUT_SECONDS)]


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


def test_process_cwd_uses_darwin_lsof(tmp_path, monkeypatch):
    expected = tmp_path / "repo"
    expected.mkdir()
    calls = []

    def lsof_runner(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, f"p123\nfwd\nn{expected}\n", "")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Darwin")

    assert sessions.process_cwd(123, lsof_runner=lsof_runner) == str(expected)
    assert calls == [(["lsof", "-p", "123", "-a", "-d", "cwd", "-Fn"], sessions.CODEX_LSOF_TIMEOUT_SECONDS)]


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

import json
import os
import time
from http import HTTPStatus
from typing import get_args
from typing import get_origin
from typing import get_type_hints


from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib import session_files
from yolomux_lib.types import RepoPayload
from yolomux_lib.types import SessionFileEntry
from yolomux_lib.types import SessionFilesPayload

from _git_helpers import git


def agent(kind, transcript, cwd, session="s1"):
    return AgentInfo(
        session=session,
        kind=kind,
        pid=1,
        pane_target="%1",
        command=kind,
        cwd=str(cwd),
        status=None,
        session_id=None,
        transcript=str(transcript),
        error=None,
    )


def tuple_return_args(value):
    assert get_origin(value) is tuple
    return get_args(value)


def dict_return_args(value):
    assert get_origin(value) is dict
    return get_args(value)


def test_session_files_payload_types_cover_builder_shapes_and_annotations():
    assert {
        "session",
        "agent",
        "agent_windows",
        "abs_path",
        "size",
        "missing",
        "source",
        "added",
        "removed",
        "diff_tracked",
        "uploaded",
    } <= set(SessionFileEntry.__annotations__)
    assert {"from_ref", "to_ref", "error", "ahead", "behind"} <= set(RepoPayload.__annotations__)
    assert {"hours", "warnings", "cache", "error", "refreshing_elsewhere"} <= set(SessionFilesPayload.__annotations__)

    assert get_type_hints(session_files.session_file_entry)["return"] is SessionFileEntry
    assert get_type_hints(session_files.refreshing_session_files_payload_for_info)["return"] is SessionFilesPayload
    assert get_type_hints(session_files.session_files_payload_for_info)["return"] is SessionFilesPayload
    assert tuple_return_args(get_type_hints(session_files.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)

    assert get_type_hints(TmuxWebtermApp.cached_session_files_payload_for_info)["return"] is SessionFilesPayload
    assert dict_return_args(get_type_hints(TmuxWebtermApp.cached_session_files_payloads_for_infos)["return"]) == (str, SessionFilesPayload)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload_for_infos)["return"]) == (SessionFilesPayload, HTTPStatus)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)


def test_scans_claude_and_codex_tool_changes(tmp_path):
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}},
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/new.md"}},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text(
        '{"msg":"*** Begin Patch\\n*** Add File: src/new.py\\n*** Update File: src/app.py\\n*** Delete File: old.py\\n"}\n',
        encoding="utf-8",
    )

    claude = session_files.scan_claude_transcript(claude_path, str(tmp_path))
    codex = session_files.scan_codex_transcript(codex_path, str(tmp_path))

    assert claude[str(tmp_path / "src" / "app.py")] == {"M"}
    assert claude["/tmp/new.md"] == {"A"}
    assert codex[str(tmp_path / "src" / "new.py")] == {"A"}
    assert codex[str(tmp_path / "src" / "app.py")] == {"M"}
    assert codex[str(tmp_path / "old.py")] == {"D"}


def test_transcript_scans_collect_generated_usage_with_changes(tmp_path):
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 11},
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 500,
                        "output_tokens": 17,
                        "reasoning_output_tokens": 3,
                        "total_tokens": 1520,
                    }
                }
            },
        }) + "\n",
        encoding="utf-8",
    )

    claude_details = session_files.scan_claude_transcript_details(claude_path, str(tmp_path))
    codex_details = session_files.scan_codex_transcript_details(codex_path, str(tmp_path))

    assert claude_details["changes"][str(tmp_path / "src" / "app.py")] == {"M"}
    assert claude_details["usage"]["generated_tokens"] == 11
    assert codex_details["usage"]["generated_tokens"] == 20
    assert session_files.transcript_generated_tokens(claude_path, "claude", str(tmp_path)) == 11
    assert session_files.transcript_generated_tokens(codex_path, "codex", str(tmp_path)) == 20


def test_codex_transcript_scan_uses_incremental_append_cache(tmp_path, monkeypatch):
    session_files._CODEX_TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    first_line = line("a.py", 5)
    second_line = line("b.py", 7)
    transcript.write_text(first_line, encoding="utf-8")
    first_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)

    first = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert first["changes"] == {str(tmp_path / "a.py"): {"M"}}
    assert first["usage"]["generated_tokens"] == 5

    transcript.write_text(first_line + second_line, encoding="utf-8")
    second_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    second = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    parsed_top_level_lines = [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")]

    assert first_key == second_key
    assert parsed_top_level_lines == [second_line]
    assert second["changes"] == {str(tmp_path / "a.py"): {"M"}, str(tmp_path / "b.py"): {"M"}}
    assert second["usage"]["generated_tokens"] == 12


def test_codex_transcript_scan_restarts_after_truncation(tmp_path):
    session_files._CODEX_TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    original_line = line("very-long-original-name.py", 5)
    replacement_line = line("b.py", 3)
    assert len(replacement_line) < len(original_line)
    transcript.write_text(original_line, encoding="utf-8")
    assert session_files.scan_codex_transcript_details(transcript, str(tmp_path))["changes"] == {str(tmp_path / "very-long-original-name.py"): {"M"}}

    transcript.write_text(replacement_line, encoding="utf-8")
    refreshed = session_files.scan_codex_transcript_details(transcript, str(tmp_path))

    assert refreshed["changes"] == {str(tmp_path / "b.py"): {"M"}}
    assert refreshed["usage"]["generated_tokens"] == 3


def test_codex_transcript_scan_restarts_when_existing_bytes_change(tmp_path):
    session_files._CODEX_TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"total_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    original_line = line("a.py", 5)
    replacement_line = line("b.py", 3000)
    assert len(replacement_line) >= len(original_line)
    transcript.write_text(original_line, encoding="utf-8")
    assert session_files.scan_codex_transcript_details(transcript, str(tmp_path))["changes"] == {str(tmp_path / "a.py"): {"M"}}

    transcript.write_text(replacement_line, encoding="utf-8")
    refreshed = session_files.scan_codex_transcript_details(transcript, str(tmp_path))

    assert refreshed["changes"] == {str(tmp_path / "b.py"): {"M"}}
    assert refreshed["usage"]["generated_tokens"] == 3000


def test_session_touched_dirs_collects_edited_dirs(tmp_path):
    # session_touched_dirs returns the unique containing dirs of files the agents EDITED (not read),
    # so repo detection can find the real repo even when the live cwd is a non-repo.
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tmp_path / "repo" / "a" / "x.py")}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": str(tmp_path / "repo" / "a" / "y.py")}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tmp_path / "repo" / "b" / "z.py")}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(tmp_path / "other" / "r.py")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, tmp_path)])
    dirs = set(session_files.session_touched_dirs(info))
    # dir 'a' deduped across two edited files; 'b' included; the Read-only 'other' dir is excluded.
    assert dirs == {str(tmp_path / "repo" / "a"), str(tmp_path / "repo" / "b")}


def test_session_files_hours_controls_transcript_cutoff(tmp_path):
    touched = tmp_path / "older.py"
    touched.write_text("print('old edit')\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(touched)}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    transcript_mtime = 10_000
    now = transcript_mtime + 2 * 3600
    os.utime(transcript, (transcript_mtime, transcript_mtime))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, tmp_path)])

    one_hour = session_files.session_files_payload_for_info(info, hours=1, now=now)
    four_hours = session_files.session_files_payload_for_info(info, hours=4, now=now)

    assert [item["path"] for item in one_hour["files"]] == []
    assert [item["path"] for item in four_hours["files"]] == [str(touched)]


def test_session_files_payload_keeps_boundary_touched_repo_stable_for_grace(tmp_path):
    primary = tmp_path / "yolomux.dev8001"
    secondary = tmp_path / "ai-config"
    for repo in (primary, secondary):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "base")
    transcript = tmp_path / "claude.jsonl"
    touched = secondary / "tracked.txt"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(touched)}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    transcript_mtime = 10_000.0
    os.utime(transcript, (transcript_mtime, transcript_mtime))
    touched.write_text("changed\n", encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, primary)])

    before_boundary = session_files.session_files_payload_for_info(info, hours=1, now=transcript_mtime + 3600 - 0.5)
    after_boundary = session_files.session_files_payload_for_info(info, hours=1, now=transcript_mtime + 3600 + 0.5)

    before_repos = {item["repo"] for item in before_boundary["repos"]}
    after_repos = {item["repo"] for item in after_boundary["repos"]}
    assert before_repos == after_repos == {str(secondary)}
    assert str(primary) not in after_repos


def test_session_files_payload_includes_zero_change_live_pane_repos_from_rendered_repo_set(tmp_path):
    primary = tmp_path / "yolomux.dev8001"
    sibling = tmp_path / "yolomux.dev8002"
    changed = tmp_path / "ai-config"
    for repo in (primary, sibling, changed):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "base")
    (changed / "tracked.txt").write_text("changed\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(primary / "tracked.txt")}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(changed / "tracked.txt")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
    panes = [
        PaneInfo(session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0", current_path=str(primary), command="zsh", active=True, window_active=True, title="", pid=11),
        PaneInfo(session="s1", window="0", pane="1", pane_id="%2", target="s1:0.1", current_path=str(sibling), command="zsh", active=False, window_active=True, title="", pid=12),
    ]
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[agent("claude", transcript, primary)])

    samples = [session_files.session_files_payload_for_info(info, hours=24, now=1600 + index) for index in range(3)]

    assert [[repo["repo"] for repo in sample["repos"]] for sample in samples] == [
        [str(changed), str(primary), str(sibling)],
        [str(changed), str(primary), str(sibling)],
        [str(changed), str(primary), str(sibling)],
    ]
    for payload in samples:
        rendered_repos = {item["repo"] for item in payload["files"] if item["status"] != "T" and item["repo"]}
        assert rendered_repos == {str(changed)}
        by_repo = {item["repo"]: item for item in payload["repos"]}
        assert by_repo[str(changed)]["count"] == sum(1 for item in payload["files"] if item["status"] != "T" and item["repo"] == str(changed))
        assert by_repo[str(primary)]["count"] == 0
        assert by_repo[str(primary)]["touched_count"] == 1
        assert by_repo[str(sibling)]["count"] == 0
        assert by_repo[str(sibling)]["touched_count"] == 0
        assert any(item["repo"] == str(primary) and item["status"] == "T" for item in payload["files"])


def test_session_files_payload_includes_clean_numbered_workdir_repo_when_pane_is_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "yolomux.dev8002"
    home.mkdir()
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    pane = PaneInfo(
        session="8002",
        window="0",
        pane="0",
        pane_id="%1",
        target="8002:0.0",
        current_path=str(home),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="8002", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(session_files, "session_workdir", lambda session: repo if session == "8002" else home)

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert payload["files"] == []
    assert payload["repos"] == [{
        "repo": str(repo),
        "count": 0,
        "touched_count": 0,
        "added": 0,
        "removed": 0,
        "from_ref": "default",
        "to_ref": "base",
        "error": "",
    }]


def test_session_files_payload_carries_agent_window_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    touched = repo / "app.py"
    touched.write_text("print('hi')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "base")

    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        '{"msg":"*** Begin Patch\\n*** Update File: app.py\\n"}\n',
        encoding="utf-8",
    )
    os.utime(transcript, (time.time(), time.time()))
    panes = [
        PaneInfo(session="s1", window="0", pane="0", pane_id="%10", target="s1:0.0", current_path=str(repo), command="codex", active=True, window_active=True, title="", pid=10, process_label="codex"),
        PaneInfo(session="s1", window="1", pane="0", pane_id="%11", target="s1:1.0", current_path=str(tmp_path), command="bash", active=True, window_active=False, title="", pid=11, process_label="bash"),
    ]
    info = SessionInfo(
        session="s1",
        panes=panes,
        selected_pane=panes[0],
        agents=[AgentInfo("s1", "codex", 10, "s1:0.0", "codex", str(repo), "running", "sid", str(transcript), None)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    item = next(row for row in payload["files"] if row["path"] == "app.py")

    assert item["agent_windows"] == [{"kind": "codex", "window": "0", "window_index": 0, "pane": "0", "pane_target": "s1:0.0"}]


def test_scan_claude_transcript_incrementally_scans_complete_appends_and_reuses_raw_parse_for_cwds(tmp_path, monkeypatch):
    session_files._CLAUDE_TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "c.jsonl"
    first_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 5}, "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]}}) + "\n"
    second_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 7}, "content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}}]}}) + "\n"
    transcript.write_text(first_line, encoding="utf-8")
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    first = session_files.scan_claude_transcript_details(transcript, str(root_a))
    assert first["changes"] == {str(root_a / "a.py"): {"M"}}
    assert first["usage"]["generated_tokens"] == 5

    transcript.write_text(first_line + second_line, encoding="utf-8")
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    second = session_files.scan_claude_transcript_details(transcript, str(root_a))
    same_raw_parse = session_files.scan_claude_transcript_details(transcript, str(root_b))

    assert [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")] == [second_line]
    assert second["changes"] == {str(root_a / "a.py"): {"M"}, str(root_a / "b.py"): {"A"}}
    assert same_raw_parse["changes"] == {str(root_b / "a.py"): {"M"}, str(root_b / "b.py"): {"A"}}
    assert second["usage"]["generated_tokens"] == 12


def test_scan_claude_transcript_waits_for_partial_lines_and_resets_after_replacement(tmp_path):
    session_files._CLAUDE_TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "c.jsonl"
    partial_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 5}, "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/a.py"}}]}})
    transcript.write_text(partial_line, encoding="utf-8")
    assert session_files.scan_claude_transcript_details(transcript)["changes"] == {}

    transcript.write_text(partial_line + "\n", encoding="utf-8")
    complete = session_files.scan_claude_transcript_details(transcript)
    assert complete["changes"] == {"/tmp/a.py": {"M"}}
    assert complete["usage"]["generated_tokens"] == 5

    replacement_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 3}, "content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/b.py"}}]}}) + "\n"
    transcript.write_text(replacement_line, encoding="utf-8")
    replacement = session_files.scan_claude_transcript_details(transcript)
    assert replacement["changes"] == {"/tmp/b.py": {"A"}}
    assert replacement["usage"]["generated_tokens"] == 3


def test_session_files_payload_merges_tool_attribution_with_git_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    untracked = repo / "new.txt"
    untracked.write_text("new\n", encoding="utf-8")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: new.txt\\n"}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["repo"] == str(repo)
    assert by_path["tracked.txt"]["agent"] == "codex"
    assert by_path["tracked.txt"]["agents"] == ["codex"]  # C5: full agent list, scalar `agent` is an alias
    assert by_path["tracked.txt"]["size"] == (repo / "tracked.txt").stat().st_size  # C5: size for image-preview gating
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    assert by_path["tracked.txt"]["diff_tracked"] is True
    # new.txt is untracked (never `git add`ed) -> "?", distinct from a staged/committed add "A".
    assert by_path["new.txt"]["status"] == "?"
    assert by_path["new.txt"]["added"] == 1
    assert by_path["new.txt"]["removed"] == 0
    assert by_path["new.txt"]["diff_tracked"] is False
    assert payload["repos"] == [{"repo": str(repo), "count": 2, "touched_count": 2, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_keeps_transcript_paths_when_branch_is_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("merged\n", encoding="utf-8")
    git(repo, "commit", "-am", "merged change")
    os.utime(tracked, (1400, 1400))

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    os.utime(rollout, (1500, 1500))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=2000)

    assert len(payload["files"]) == 1
    item = payload["files"][0]
    assert item["session"] == "s1"
    assert item["agents"] == ["codex"]
    assert item["agent"] == "codex"
    assert item["status"] == "T"
    assert item["repo"] == str(repo)
    assert item["path"] == "tracked.txt"
    assert item["abs_path"] == str(tracked)
    assert item["mtime"] == 1400
    assert item["source"] == "transcript"
    assert item["added"] is None
    assert item["removed"] is None
    assert item["uploaded"] is False

    assert payload["repos"] == []


def test_scan_codex_transcript_uses_exec_command_workdir_for_git_add(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({
                    "cmd": "git add rust/Cargo.toml -- vllm/envs.py",
                    "workdir": str(repo),
                }),
            },
        }) + "\n",
        encoding="utf-8",
    )

    changes = session_files.scan_codex_transcript(transcript, cwd="/elsewhere")

    assert changes[str(repo / "rust" / "Cargo.toml")] == {"M"}
    assert changes[str(repo / "vllm" / "envs.py")] == {"M"}


def test_scan_shell_command_changes_stops_git_add_at_shell_separator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    changes = session_files.scan_shell_command_changes("git add tracked.txt && git commit -m done", str(repo))

    assert changes == {str(repo / "tracked.txt"): {"M"}}


def test_scan_shell_command_changes_tracks_cd_before_git_add(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    changes = session_files.scan_shell_command_changes("bash -lc 'cd repo && git add src/app.py'", str(tmp_path))

    assert changes == {str(repo / "src" / "app.py"): {"M"}}


def test_session_files_payload_uses_historical_codex_transcript_for_clean_pane_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    os.utime(tracked, (1400, 1400))
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({
                    "cmd": "git add tracked.txt",
                    "workdir": str(repo),
                }),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda cwd: transcript if cwd == str(repo) else None)

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert len(payload["files"]) == 1
    item = payload["files"][0]
    assert item["status"] == "T"
    assert item["source"] == "transcript"
    assert item["repo"] == str(repo)
    assert item["path"] == "tracked.txt"
    assert item["agents"] == ["codex"]
    assert payload["repos"] == [{
        "repo": str(repo),
        "count": 0,
        "touched_count": 1,
        "added": 0,
        "removed": 0,
        "from_ref": "default",
        "to_ref": "base",
        "error": "",
    }]


def test_historical_codex_transcript_prefers_recent_transcript_with_repo_changes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mentioned = tmp_path / "mentioned.jsonl"
    mentioned.write_text(json.dumps({"message": f"look at {repo}"}) + "\n", encoding="utf-8")
    changed = tmp_path / "changed.jsonl"
    changed.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "git add changed.txt", "workdir": str(repo)}),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(mentioned, (2000, 2000))
    os.utime(changed, (1900, 1900))
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda cwd: mentioned)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [mentioned, changed])

    assert session_files.historical_codex_transcript_for_cwd(str(repo), cutoff=0) == changed


def test_historical_codex_candidates_ignore_home_cwd_that_contains_other_repos(tmp_path, monkeypatch):
    home = tmp_path / "home"
    other = home / "yolomux.dev8003"
    home.mkdir()
    other.mkdir()
    git(other, "init")
    git(other, "config", "user.email", "test@example.com")
    git(other, "config", "user.name", "Test User")
    tracked = other / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(other, "add", "tracked.txt")
    git(other, "commit", "-m", "base")
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "git add tracked.txt", "workdir": str(other)}),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
    pane = PaneInfo(
        session="8002",
        window="0",
        pane="0",
        pane_id="%1",
        target="8002:0.0",
        current_path=str(home),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="8002", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(session_files, "session_workdir", lambda _session: home)
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda _cwd: None)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [transcript])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert session_files.historical_codex_candidate_cwds(info) == []
    assert payload["files"] == []
    assert payload["repos"] == []


def test_file_mtime_or_fallback_preserves_epoch_mtime(tmp_path):
    path = tmp_path / "epoch.txt"
    path.write_text("old\n", encoding="utf-8")
    os.utime(path, (0, 0))

    assert session_files.file_mtime_or_fallback(path, fallback=1234) == 0


def test_file_mtime_or_fallback_uses_fallback_for_missing_path(tmp_path):
    assert session_files.file_mtime_or_fallback(tmp_path / "missing.txt", fallback=1234) == 1234


def test_session_files_payload_marks_statless_touched_path_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "README.md"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"msg":"*** Begin Patch\\n*** Update File: docs/GUI_SPECS.md\\n"}\n', encoding="utf-8")
    os.utime(transcript, (2000, 2000))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", transcript, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=2500)

    item = next(file for file in payload["files"] if file["path"] == "docs/GUI_SPECS.md")
    assert item["missing"] is True
    assert item["source"] == "transcript"


def test_session_files_payload_collects_multiple_agents_for_one_file(tmp_path):
    # C5: when both Claude and Codex touch the same file, the entry lists BOTH (no overwrite), so the UI
    # can render two agent icons.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tracked)}},
        ]}}) + "\n",
        encoding="utf-8",
    )
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(
        session="s1", panes=[], selected_pane=None,
        agents=[agent("claude", claude_path, repo), agent("codex", rollout, repo)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert sorted(by_path["tracked.txt"]["agents"]) == ["claude", "codex"]
    assert by_path["tracked.txt"]["agent"] in {"claude", "codex"}  # scalar alias is just the first


def test_selected_session_rows_include_cross_session_agent_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tracked)}},
        ]}}) + "\n",
        encoding="utf-8",
    )
    info1 = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", codex_path, repo, session="s1")])
    info2 = SessionInfo(session="s2", panes=[], selected_pane=None, agents=[agent("claude", claude_path, repo, session="s2")])

    payload, status = session_files.session_files_payload("s1", {"s1": info1, "s2": info2}, hours=24)

    assert status == 200
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["tracked.txt"]["session"] == "s1"
    assert sorted(by_path["tracked.txt"]["agents"]) == ["claude", "codex"]


def test_session_files_payload_includes_non_repo_transcript_files_without_counting_them(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    tmp_artifact = tmp_path / "scratch.txt"
    tmp_artifact.write_text("scratch\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        f'{{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: {tmp_artifact}\\n"}}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    by_abs = {item["abs_path"]: item for item in payload["files"]}
    assert str(tracked) in by_abs
    assert str(tmp_artifact) in by_abs
    assert by_abs[str(tmp_artifact)]["repo"] == ""
    assert by_abs[str(tmp_artifact)]["path"] == str(tmp_artifact)
    assert by_abs[str(tmp_artifact)]["added"] == 1
    assert by_abs[str(tmp_artifact)]["removed"] == 0
    assert by_abs[str(tmp_artifact)]["diff_tracked"] is False
    by_repo = {item["repo"]: item for item in payload["repos"]}
    assert by_repo[str(repo)]["added"] == 1
    assert by_repo[str(repo)]["removed"] == 1
    assert by_repo[""]["added"] == 0
    assert by_repo[""]["removed"] == 0


def test_session_files_payload_demotes_missing_transcript_to_per_agent_warning(tmp_path):
    # D2: a multi-agent session where ONE Codex pane has no discoverable transcript (AgentInfo.error set,
    # e.g. an inactive background pane) must NOT read as a session-level Differ failure. The valid agent's
    # changed file/repo must still render, and the missing-transcript message must be demoted to a
    # non-blocking per-agent warning (out of the blocking `errors` list the Differ renders as red rows).
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")

    valid_codex = agent("codex", rollout, repo)
    missing_codex = AgentInfo(
        session="s1",
        kind="codex",
        pid=2,
        pane_target="%2",
        command="codex",
        cwd=str(tmp_path / "vllm-0.22.0"),
        status=None,
        session_id=None,
        transcript=None,
        error="codex transcript not found by process fd or cwd",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[valid_codex, missing_codex])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    # The valid agent's changed file and its repo are still present.
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["tracked.txt"]["repo"] == str(repo)
    assert by_path["tracked.txt"]["status"] == "M"
    assert str(repo) in {repo_summary["repo"] for repo_summary in payload["repos"]}

    # The missing-transcript error is NOT a blocking/session-level error: the Differ renders payload["errors"]
    # as red failure rows, so the message must be absent there.
    assert "codex transcript not found by process fd or cwd" not in payload["errors"]
    assert payload["errors"] == []

    # It is surfaced as a non-blocking, per-agent warning instead.
    assert payload["warnings"] == ["codex transcript not found by process fd or cwd"]


def test_git_status_parses_renames_and_tab_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    old_name = "old\tname.txt"
    new_name = "new\tname.txt"
    (repo / old_name).write_text("one\n", encoding="utf-8")
    git(repo, "add", old_name)
    git(repo, "commit", "-m", "base")
    git(repo, "mv", old_name, new_name)

    statuses, error = session_files.git_name_status(repo, "HEAD")
    counts = session_files.git_numstat(repo, "HEAD")

    assert error == ""
    assert old_name not in statuses
    assert statuses[new_name] == "R"
    assert counts[new_name] == {"added": 0, "removed": 0}


def test_git_status_labels_untracked_question_distinct_from_staged_add_A(tmp_path):
    # An untracked working-tree file must read as "?" (git's own untracked marker), while a genuinely
    # staged add reads as "A", so the changes pane can tell "git is tracking this add" apart from
    # "this file isn't tracked yet".
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "loose.txt").write_text("loose\n", encoding="utf-8")  # untracked, never added

    statuses, error = session_files.git_name_status(repo, "HEAD")

    assert error == ""
    assert statuses["staged.txt"] == "A"
    assert statuses["loose.txt"] == "?"


def test_session_files_payload_counts_staged_added_file_as_tracked_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    staged = repo / "staged.txt"
    staged.write_text("one\ntwo\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Add File: staged.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    item = {entry["path"]: entry for entry in payload["files"]}["staged.txt"]

    assert item["status"] == "A"
    assert item["added"] == 2
    assert item["removed"] == 0
    assert item["diff_tracked"] is True
    assert payload["repos"][0]["added"] == 2
    assert payload["repos"][0]["removed"] == 0


def test_session_files_payload_preserves_untracked_symlink_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    target = repo / "lib" / "parsers" / "REASONING_CASES.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Reasoning\n", encoding="utf-8")
    git(repo, "add", "lib/parsers/REASONING_CASES.md")
    git(repo, "commit", "-m", "base")
    staged_link = repo / ".stage-v2" / "lib" / "parsers" / "REASONING_CASES.md"
    staged_link.parent.mkdir(parents=True)
    staged_link.symlink_to(target)
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert "lib/parsers/REASONING_CASES.md" not in by_path
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["status"] == "?"
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["abs_path"] == str(staged_link)


def test_git_numstat_parses_paths_with_tabs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    name = "tab\tpath.txt"
    (repo / name).write_text("one\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", "base")
    (repo / name).write_text("one\ntwo\n", encoding="utf-8")

    counts = session_files.git_numstat(repo, "HEAD")

    assert counts[name] == {"added": 1, "removed": 0}


def test_session_files_payload_marks_generated_upload_names(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    upload = repo / "20260531-001-diagram.png"
    upload.write_bytes(b"png")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "20260531-001-diagram.png"
    assert payload["files"][0]["uploaded"] is True


def test_session_files_payload_counts_branch_commits_since_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature")
    tracked.write_text("feature\n", encoding="utf-8")
    git(repo, "commit", "-am", "feature change")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    assert payload["repos"] == [{"repo": str(repo), "count": 1, "touched_count": 1, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_accepts_explicit_commit_refs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref=newer)

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["added"] == 1
    assert payload["files"][0]["removed"] == 1
    assert payload["from_ref"] == older
    assert payload["to_ref"] == newer
    assert payload["repos"][0]["behind"] == 0
    assert payload["repos"][0]["ahead"] == 1


def test_session_files_payload_explicit_current_ref_includes_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    untracked = repo / "lib" / "llm" / "src" / "protocols" / "openai" / "chat_completions" / "qwen3_coder_v2.rs"
    untracked.parent.mkdir(parents=True)
    untracked.write_text("one\ntwo\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '{"msg":"*** Begin Patch\\n*** Add File: lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs\\n"}\n',
        encoding="utf-8",
    )
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref="current")

    by_path = {item["path"]: item for item in payload["files"]}
    qwen_path = "lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs"
    assert set(by_path) == {qwen_path, "tracked.txt"}
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 0
    assert by_path["tracked.txt"]["diff_tracked"] is True
    assert by_path[qwen_path]["status"] == "?"
    assert by_path[qwen_path]["added"] == 2
    assert by_path[qwen_path]["removed"] == 0
    assert by_path[qwen_path]["diff_tracked"] is False
    assert payload["repos"][0]["count"] == 2
    assert payload["repos"][0]["added"] == 1
    assert payload["repos"][0]["removed"] == 0


def test_git_numstat_does_not_use_copy_detection_for_plain_diff_counts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    source = repo / "source.txt"
    source.write_text("a\nb\nc\nd\ne\nf\ng\nh\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "base")
    copied = repo / "copied.txt"
    copied.write_text("a\nb\nc\nd\ne\nf\ng\nchanged\nnew\n", encoding="utf-8")
    git(repo, "add", "copied.txt")

    counts = session_files.git_numstat(repo, "HEAD")

    assert counts["copied.txt"] == {"added": 9, "removed": 0}


def test_session_files_payload_falls_back_when_requested_ref_is_unknown_in_repo(tmp_path):
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1",
                window="0",
                pane=str(index),
                pane_id=f"%{index}",
                target=f"s1:0.{index}",
                current_path=str(repo),
                command="zsh",
                active=index == 0,
                window_active=True,
                title="",
                pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=repo1_from, to_ref="current")

    assert payload["errors"] == []
    assert {item["repo"] for item in payload["files"]} == {str(repo1), str(repo2)}
    assert all(item["path"] == "tracked.txt" for item in payload["files"])


def test_session_files_payload_applies_per_repo_refs_independently(tmp_path):
    # C6: a FROM/TO override scoped to repo1 must NOT change repo2's comparison — each repo reports its
    # own effective refs.
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "two")
        tracked.write_text("three\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD~1").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1", window="0", pane=str(index), pane_id=f"%{index}",
                target=f"s1:0.{index}", current_path=str(repo), command="zsh",
                active=index == 0, window_active=True, title="", pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(
        info, hours=24, now=time.time(),
        repo_refs={str(repo1): {"from": repo1_from, "to": "current"}},
    )
    by_repo = {item["repo"]: item for item in payload["repos"]}

    assert by_repo[str(repo1)]["from_ref"] == repo1_from
    assert by_repo[str(repo1)]["to_ref"] == "current"
    assert by_repo[str(repo1)]["error"] == ""
    # repo2 had no override, so it stays on the default comparison and is not affected by repo1's SHA.
    assert by_repo[str(repo2)]["from_ref"] == "default"
    assert by_repo[str(repo2)]["to_ref"] == "base"
    assert payload["errors"] == []


def test_git_recent_refs_exposes_more_than_twenty_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    for index in range(25):
        tracked.write_text(f"{index}\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", f"commit {index}")
    git(repo, "branch", "main")
    git(repo, "branch", "same-head")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/topic", "HEAD")

    refs = session_files.git_recent_refs(repo)
    head_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    head_short = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()

    assert refs[0]["ref"] == "HEAD"
    assert refs[0]["commit"] == head_commit
    assert refs[0]["short"].startswith(f"{head_short}/HEAD")
    assert "origin/main" in refs[0]["short"]
    assert "same-head" in refs[0]["short"]
    assert refs[0]["aliases"][0] == "HEAD"
    assert {"origin/main", "origin/topic", "main", "same-head"}.issubset(set(refs[0]["aliases"]))
    head_commit_ref = next(item for item in refs if item["ref"] == head_commit)
    assert head_commit_ref["short"].startswith(f"{head_short}/origin/main")
    assert {"origin/main", "origin/topic", "main", "same-head"}.issubset(set(head_commit_ref["aliases"]))
    assert refs[1]["ref"] == "current"
    assert len(refs) >= 27
    assert any(item["subject"] == "commit 0" for item in refs)


def test_session_files_payload_reports_invalid_ref_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=newer, to_ref=older)

    assert payload["files"] == []
    assert payload["errors"] == []


def test_session_files_payload_uses_session_repo_without_ai_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["source"] == "git"
    assert payload["repos"] == [{"repo": str(repo), "count": 1, "touched_count": 0, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_does_not_invent_agent_for_repo_only_change(tmp_path):
    # C5: a git change with NO transcript attribution (the rollout never mentions this file) must render
    # zero agent icons — earlier the code invented a fallback to the session's agent, falsely implying
    # the agent touched a file the user changed by hand.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"no patch path here"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["agents"] == []
    assert payload["files"][0]["agent"] == ""
    assert payload["files"][0]["source"] == "git"

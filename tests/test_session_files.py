import json
import os
import time
import tracemalloc
from http import HTTPStatus
from pathlib import Path
from typing import get_args
from typing import get_origin
from typing import get_type_hints


import threading as threading_module

import yolomux_lib.app as app_module
from yolomux_lib import common as common_module
from yolomux_lib import sessions as sessions_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.state_services import ClientEventWatcherRecord
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib import session_files
from yolomux_lib.sessions import CODEX_TRANSCRIPT_SCAN_LIMIT
from yolomux_lib.types import RepoPayload
from yolomux_lib.types import SessionFileEntry
from yolomux_lib.types import SessionFilesPayload

from _git_helpers import git
from _git_helpers import init_repo


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
    assert {"from_ref", "to_ref", "error", "error_message", "ahead", "behind"} <= set(RepoPayload.__annotations__)
    assert {"hours", "warnings", "cache", "error", "refreshing_elsewhere"} <= set(SessionFilesPayload.__annotations__)

    assert get_type_hints(session_files.session_file_entry)["return"] is SessionFileEntry
    assert get_type_hints(session_files.refreshing_session_files_payload_for_info)["return"] is SessionFilesPayload
    assert get_type_hints(session_files.session_files_payload_for_info)["return"] is SessionFilesPayload
    assert tuple_return_args(get_type_hints(session_files.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)

    assert get_type_hints(TmuxWebtermApp.cached_session_files_payload_for_info)["return"] is SessionFilesPayload
    assert dict_return_args(get_type_hints(TmuxWebtermApp.cached_session_files_payloads_for_infos)["return"]) == (str, SessionFilesPayload)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload_for_infos)["return"]) == (SessionFilesPayload, HTTPStatus)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)


def test_shared_git_snapshot_reuses_one_worktree_build_and_invalidates_every_state_input(no_control_socket, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "one.py").write_text("one = 1\n", encoding="utf-8")
    (repo / "two.py").write_text("two = 1\n", encoding="utf-8")
    git(repo, "add", "one.py", "two.py")
    git(repo, "commit", "-m", "base")
    (repo / "one.py").write_text("one = 2\n", encoding="utf-8")
    (repo / "two.py").write_text("two = 2\n", encoding="utf-8")

    transcript_one = tmp_path / "one.jsonl"
    transcript_two = tmp_path / "two.jsonl"
    transcript_one.write_text('{"msg":"*** Begin Patch\\n*** Update File: one.py\\n"}\n', encoding="utf-8")
    transcript_two.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "two.py"}}]}}) + "\n",
        encoding="utf-8",
    )
    info_one = SessionInfo("one", [], None, [agent("codex", transcript_one, repo, session="one")])
    info_two = SessionInfo("two", [], None, [agent("claude", transcript_two, repo, session="two")])

    real_build = session_files.build_git_snapshot
    builds = []

    def counted_build(path, from_ref=None, to_ref=None):
        builds.append((str(path), from_ref, to_ref))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files, "build_git_snapshot", counted_build)
    webapp = TmuxWebtermApp(["one", "two"])
    try:
        payload_one = webapp.compute_session_files_payload_for_info(info_one, 24.0, None, None, None)
        payload_two = webapp.compute_session_files_payload_for_info(info_two, 24.0, None, None, None)
        assert len(builds) == 1
        one_files = {item["path"]: item for item in payload_one["files"]}
        two_files = {item["path"]: item for item in payload_two["files"]}
        assert one_files["one.py"]["agents"] == ["codex"]
        assert one_files["two.py"]["agents"] == []
        assert two_files["one.py"]["agents"] == []
        assert two_files["two.py"]["agents"] == ["claude"]

        cache_key_before = webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, None, None, None)
        (repo / "untracked.py").write_text("new = True\n", encoding="utf-8")
        cache_key_untracked = webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, None, None, None)
        assert cache_key_untracked != cache_key_before
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 2
        git(repo, "add", "one.py")
        cache_key_index = webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, None, None, None)
        assert cache_key_index != cache_key_untracked
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 3
        git(repo, "add", "two.py", "untracked.py")
        git(repo, "commit", "-m", "next")
        cache_key_head = webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, None, None, None)
        assert cache_key_head != cache_key_index
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 4
        assert webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, "HEAD~1", "HEAD", None) != cache_key_head
        webapp.shared_session_files_git_snapshot(repo, "HEAD~1", "HEAD")
        assert len(builds) == 5

        other = tmp_path / "other-worktree"
        git(repo, "worktree", "add", "-b", "other", str(other))
        (other / "worktree-only.py").write_text("other = True\n", encoding="utf-8")
        webapp.shared_session_files_git_snapshot(other, None, None)
        assert len(builds) == 6

        key = ("phase-fixture",)
        webapp.compute_session_files_cache_entry(key, lambda: (payload_one, HTTPStatus.OK))
        webapp.compute_session_files_cache_entry(key, lambda: (_ for _ in ()).throw(AssertionError("fresh cache must win")))
        webapp.record_session_files_phase("bounded-details", 1.0, {"repo": "x" * 1000, "nested": {"drop": True}})
        recent = webapp.performance_metrics_payload()["recent"]
    finally:
        webapp.control_server.stop()

    phase_names = {item["surface"] for item in recent if item["role"] == "session-files"}
    assert {"phase:transcript-attribution", "phase:repository-discovery", "phase:git-snapshot", "phase:session-merge-render", "phase:cache-serialization"} <= phase_names
    hit_rows = [item for item in recent if item["surface"] == "phase:git-snapshot" and item["cache_status"] == "hit:fresh"]
    assert hit_rows and all(item["compute_ms"] == 0 for item in hit_rows)
    bounded = next(item for item in reversed(recent) if item["surface"] == "phase:bounded-details")
    assert len(bounded["details"]["repo"]) <= 512
    assert "nested" not in bounded["details"]


def test_newer_session_files_generation_cannot_be_overwritten_by_delayed_old_work(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    old_started = threading_module.Event()
    release_old = threading_module.Event()
    results = {}
    logical = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "s1", 24.0, "", "", ())
    old_key = (*logical, (("s1", "old-info"),), (("repo", "old-repo"),))
    new_key = (*logical, (("s1", "new-info"),), (("repo", "new-repo"),))

    def old_compute():
        old_started.set()
        assert release_old.wait(timeout=5)
        return {"files": [{"path": "old.py"}], "repos": [], "errors": []}, HTTPStatus.OK

    def run_old():
        results["old"] = webapp.compute_session_files_cache_entry(old_key, old_compute)

    def run_new():
        results["new"] = webapp.compute_session_files_cache_entry(
            new_key,
            lambda: ({"files": [{"path": "new.py"}], "repos": [], "errors": []}, HTTPStatus.OK),
        )

    old_thread = threading_module.Thread(target=run_old)
    new_thread = threading_module.Thread(target=run_new)
    try:
        old_thread.start()
        assert old_started.wait(timeout=5)
        new_thread.start()
        release_old.set()
        old_thread.join(timeout=5)
        new_thread.join(timeout=5)
        assert not old_thread.is_alive()
        assert not new_thread.is_alive()
        path, _signature = webapp.session_files_disk_cache_path(new_key)
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["source_generation"] == webapp.session_files_source_generation(new_key)
        assert record["payload"]["files"] == [{"path": "new.py"}]
        assert old_key not in webapp.session_files_service.cache
        assert new_key in webapp.session_files_service.cache
        assert results["old"][0]["files"] == [{"path": "old.py"}]
        assert results["new"][0]["files"] == [{"path": "new.py"}]
    finally:
        release_old.set()
        old_thread.join(timeout=1)
        new_thread.join(timeout=1)
        webapp.control_server.stop()


def test_background_reservation_order_not_delayed_worker_start_controls_stable_cache(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    logical = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "s1", 24.0, "", "", ())
    old_key = (*logical, (("s1", "old-info"),), (("repo", "old-repo"),))
    new_key = (*logical, (("s1", "new-info"),), (("repo", "new-repo"),))
    old_path, stable_signature = webapp.session_files_disk_cache_path(old_key)
    try:
        old_record = webapp.session_files_service.reserve_work(old_key, stable_signature)
        assert old_record is not None
        assert old_record.stable_generation > 0

        new_payload = {"files": [{"path": "new.py"}], "repos": [], "errors": []}
        webapp.compute_session_files_cache_entry(new_key, lambda: (new_payload, HTTPStatus.OK))
        old_payload = {"files": [{"path": "old.py"}], "repos": [], "errors": []}
        old_result = webapp.compute_session_files_cache_entry(old_key, lambda: (old_payload, HTTPStatus.OK), reserved=True)

        record = json.loads(old_path.read_text(encoding="utf-8"))
        assert record["payload"]["files"] == [{"path": "new.py"}]
        assert record["source_generation"] == webapp.session_files_source_generation(new_key)
        assert old_result[0]["files"] == [{"path": "old.py"}]
        assert old_key not in webapp.session_files_service.cache
    finally:
        webapp.control_server.stop()


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
                        "total_tokens": 1017,
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
    assert codex_details["usage"]["generated_tokens"] == 17
    assert session_files.transcript_generated_tokens(claude_path, "claude", str(tmp_path)) == 11
    assert session_files.transcript_generated_tokens(codex_path, "codex", str(tmp_path)) == 17


def test_generated_usage_tokens_treats_reasoning_as_output_subset():
    assert session_files.generated_usage_tokens({"output_tokens": 17, "reasoning_output_tokens": 3}) == 17
    assert session_files.generated_usage_tokens({"reasoning_output_tokens": 3}) == 3
    assert session_files.generated_usage_tokens({"outputTokens": 17, "completion_tokens": 17}) == 17


def test_codex_generated_tokens_reads_latest_cumulative_usage_from_the_tail(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout.jsonl"

    def usage_line(tokens):
        return json.dumps({"payload": {"info": {"total_token_usage": {"output_tokens": tokens}}}}) + "\n"

    transcript.write_text(
        usage_line(11)
        + json.dumps({"payload": "x" * (session_files._TRANSCRIPT_REVERSE_SCAN_BYTES * 3)}) + "\n"
        + usage_line(17)
        + '{"partial":',
        encoding="utf-8",
    )
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)

    assert session_files.transcript_generated_tokens(transcript, "codex") == 17
    # Family discovery reads the rollout metadata once before preserving the tail-only fast path.
    assert len(parsed) <= 5


def test_codex_generated_tokens_sum_spawned_rollouts_into_the_parent(tmp_path, monkeypatch):
    parent = tmp_path / "rollout-parent.jsonl"
    child = tmp_path / "rollout-child.jsonl"
    grandchild = tmp_path / "rollout-grandchild.jsonl"
    unrelated = tmp_path / "rollout-unrelated.jsonl"

    def lines(thread_id, totals, model, parent_thread_id=""):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        records = [{"type": "session_meta", "payload": meta}, {"type": "turn_context", "payload": {"model": model}}]
        records.extend({"timestamp": index + 1, "payload": {"info": {"total_token_usage": {"output_tokens": total}}}} for index, total in enumerate(totals))
        return "".join(json.dumps(record) + "\n" for record in records)

    parent.write_text(lines("parent", [100, 160], "gpt-5.5"), encoding="utf-8")
    child.write_text(lines("child", [20, 50], "gpt-5.4-mini", "parent"), encoding="utf-8")
    grandchild.write_text(lines("grandchild", [5, 11], "gpt-5.5", "child"), encoding="utf-8")
    unrelated.write_text(lines("unrelated", [999], "gpt-unrelated", "other"), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda path: [parent, child, grandchild] if path == parent else [path])

    assert session_files.transcript_generated_tokens(parent, "codex") == 221
    assert session_files.transcript_generated_tokens_by_model(parent, "codex") == {"gpt-5.5": 171, "gpt-5.4-mini": 50}
    assert {event.source for event in session_files.transcript_generated_token_events(parent, "codex")} == {str(parent), str(child), str(grandchild)}


def test_transcript_usage_identity_changes_after_in_place_replacement(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"session":"first"}\n', encoding="utf-8")
    first = session_files.transcript_usage_identity(transcript, "codex")

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write('{"event":"append"}\n')
    appended = session_files.transcript_usage_identity(transcript, "codex")

    transcript.write_text('{"session":"replacement"}\n', encoding="utf-8")
    replacement = session_files.transcript_usage_identity(transcript, "codex")

    assert first
    assert appended == first
    assert replacement
    assert replacement != first


def test_claude_transcript_usage_deduplicates_repeated_message_ids(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "claude.jsonl"

    def line(message_id, output_tokens):
        return json.dumps({
            "type": "assistant",
            "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    transcript.write_text(line("msg-1", 11), encoding="utf-8")
    first = session_files.scan_claude_transcript_details(transcript)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(line("msg-1", 11) + line("msg-1", 13) + line("msg-2", 7))

    details = session_files.scan_claude_transcript_details(transcript)

    assert first["usage"]["generated_tokens"] == 11
    assert details["usage"]["generated_tokens"] == 20


def test_claude_generated_tokens_include_subagent_transcript_family(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "session-id.jsonl"
    subagent = tmp_path / "session-id" / "subagents" / "agent-a.jsonl"
    nested_subagent = tmp_path / "session-id" / "subagents" / "nested" / "agent-b.jsonl"
    subagent.parent.mkdir(parents=True)
    nested_subagent.parent.mkdir(parents=True)

    def line(message_id, output_tokens):
        return json.dumps({
            "type": "assistant",
            "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    transcript.write_text(line("parent", 11), encoding="utf-8")
    subagent.write_text(line("child-a", 17), encoding="utf-8")
    nested_subagent.write_text(line("child-b", 23), encoding="utf-8")

    identity = session_files.transcript_usage_identity(transcript, "claude")
    assert session_files.transcript_generated_tokens(transcript, "claude") == 51

    with subagent.open("a", encoding="utf-8") as handle:
        handle.write(line("child-a-more", 7))

    assert session_files.transcript_usage_identity(transcript, "claude") == identity
    assert session_files.transcript_generated_tokens(transcript, "claude") == 58


def test_transcript_generated_token_events_preserve_codex_counters_and_claude_subagents(tmp_path):
    codex = tmp_path / "rollout.jsonl"
    claude = tmp_path / "session.jsonl"
    subagent = tmp_path / "session" / "subagents" / "agent.jsonl"
    subagent.parent.mkdir(parents=True)

    def codex_line(timestamp, total):
        return json.dumps({"timestamp": timestamp, "payload": {"info": {"total_token_usage": {"output_tokens": total}}}}) + "\n"

    def claude_line(timestamp, message_id, output_tokens):
        return json.dumps({"timestamp": timestamp, "type": "assistant", "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []}}) + "\n"

    codex.write_text(codex_line(100, 11) + codex_line(160, 31), encoding="utf-8")
    claude.write_text(claude_line(100, "parent", 7) + claude_line(160, "parent", 9), encoding="utf-8")
    subagent.write_text(claude_line(130, "child", 13), encoding="utf-8")

    codex_events = session_files.transcript_generated_token_events(codex, "codex")
    claude_events = session_files.transcript_generated_token_events(claude, "claude")

    assert [(event.timestamp, event.tokens) for event in codex_events] == [(100.0, 11.0), (160.0, 20.0)]
    assert sorted((event.timestamp, event.tokens) for event in claude_events) == [(100.0, 7.0), (130.0, 13.0), (160.0, 2.0)]
    assert len({event.source for event in claude_events}) == 2


def test_normalized_codex_usage_atoms_subtract_cached_input_and_keep_effort_with_following_usage(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "low"}},
        {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10, "reasoning_output_tokens": 4}}}},
        {"timestamp": 3, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
        {"timestamp": 4, "payload": {"info": {"total_token_usage": {"input_tokens": 150, "cached_input_tokens": 70, "output_tokens": 25, "reasoning_output_tokens": 9}}}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")
    by_time = {
        timestamp: {(atom.direction, atom.cache_role): atom for atom in atoms if atom.timestamp == timestamp}
        for timestamp in {atom.timestamp for atom in atoms}
    }

    assert {(key, atom.quantity) for key, atom in by_time[2.0].items()} == {
        (("input", "none"), 60.0), (("input", "read"), 40.0), (("output", "none"), 10.0),
    }
    assert {(key, atom.quantity) for key, atom in by_time[4.0].items()} == {
        (("input", "none"), 20.0), (("input", "read"), 30.0), (("output", "none"), 15.0),
    }
    assert {atom.effort for atom in by_time[2.0].values()} == {"low"}
    assert {atom.effort for atom in by_time[4.0].values()} == {"high"}
    assert sum(atom.quantity for atom in atoms if atom.direction == "output") == 25.0


def test_codex_thread_settings_attribute_token_count_before_first_turn_context(tmp_path):
    transcript = tmp_path / "rollout-thread-settings.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in [
        {"type": "session_meta", "timestamp": 1, "payload": {"id": "thread-settings"}},
        {"type": "response_item", "timestamp": 2, "payload": {"text": "pretend model gpt-prose"}},
        {
            "type": "event_msg",
            "timestamp": 3,
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "model": "gpt-explicit",
                    "reasoning_effort": "xhigh",
                    "service_tier": "default",
                },
            },
        },
        {
            "type": "event_msg",
            "timestamp": 4,
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}},
            },
        },
        {"type": "turn_context", "timestamp": 5, "payload": {"model": "gpt-later", "effort": "low"}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")

    assert {atom.model for atom in atoms} == {"gpt-explicit"}
    assert {atom.model_evidence for atom in atoms} == {
        "thread_settings_applied.thread_settings.model",
    }
    assert {atom.effort for atom in atoms} == {"xhigh"}
    assert {atom.service_tier for atom in atoms} == {"default"}


def test_codex_usage_atom_iterator_yields_before_reading_the_rest_of_one_large_file(monkeypatch, tmp_path):
    def records(*_args, **_kwargs):
        yield {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}}
        yield {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}}
        raise AssertionError("iterator materialized the rest of the transcript before yielding")

    monkeypatch.setattr(session_files, "transcript_json_records", records)
    atoms = session_files.iter_codex_transcript_usage_atoms(tmp_path / "large.jsonl")

    first = next(atoms)
    assert first.timestamp == 2
    assert first.model == "gpt-5.6"


def test_codex_usage_atom_iterator_keeps_one_large_real_file_memory_bounded(tmp_path):
    transcript = tmp_path / "large-rollout.jsonl"
    records = [{"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6"}}]
    records.extend(
        {"timestamp": index + 2, "payload": {"info": {"total_token_usage": {"input_tokens": index + 1, "output_tokens": index + 1}}}}
        for index in range(20_000)
    )
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    del records

    tracemalloc.start()
    count = sum(1 for _atom in session_files.iter_codex_transcript_usage_atoms(transcript))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert count == 40_000
    assert peak < 8 * 1024 * 1024


def test_codex_usage_atoms_keep_explicit_pricing_profile_and_service_tier(tmp_path):
    transcript = tmp_path / "codex-pricing-context.jsonl"
    transcript.write_text("\n".join(json.dumps(item) for item in [
        {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high", "pricing_profile": "batch", "service_tier": "flex"}},
        {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")

    assert {atom.pricing_profile for atom in atoms} == {"batch"}
    assert {atom.service_tier for atom in atoms} == {"flex"}


def test_usage_component_delta_resets_all_reported_components_and_leaves_missing_unknown():
    previous = {
        ("input", "text", "none", "tokens"): 100.0,
        ("input", "text", "read", "tokens"): 40.0,
        ("output", "text", "none", "tokens"): 20.0,
    }
    current = {
        ("input", "text", "none", "tokens"): 9.0,
        ("input", "text", "read", "tokens"): None,
        ("output", "text", "none", "tokens"): 4.0,
    }

    # A single counter rollback is a provider rollover, not a negative delta;
    # both reported classes restart together and an omitted class stays absent.
    assert session_files.usage_component_delta(current, previous) == {
        ("input", "text", "none", "tokens"): 9.0,
        ("output", "text", "none", "tokens"): 4.0,
    }


def test_normalized_claude_usage_atoms_dedupe_components_and_preserve_cache_write_duration(tmp_path):
    transcript = tmp_path / "claude.jsonl"

    def record(timestamp, input_tokens, output_tokens, write_5m, write_1h):
        return {
            "timestamp": timestamp,
            "type": "assistant",
            "message": {
                "id": "message-1",
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": write_5m,
                    "cache_creation_input_tokens_1h": write_1h,
                    "output_tokens": output_tokens,
                },
            },
        }

    transcript.write_text("\n".join(json.dumps(item) for item in [
        record(1, 10, 5, 30, 40), record(2, 12, 7, 35, 44),
    ]) + "\n", encoding="utf-8")
    atoms = session_files.transcript_usage_atoms(transcript, "claude")
    quantities = {}
    for atom in atoms:
        key = (atom.direction, atom.cache_role)
        quantities[key] = quantities.get(key, 0.0) + atom.quantity

    assert quantities == {
        ("input", "none"): 12.0,
        ("input", "read"): 20.0,
        ("input", "write_5m"): 35.0,
        ("input", "write_1h"): 44.0,
        ("output", "none"): 7.0,
    }


def test_claude_usage_atoms_prefer_nested_cache_creation_duration_split(tmp_path):
    transcript = tmp_path / "claude-nested-cache.jsonl"
    transcript.write_text(json.dumps({
        "timestamp": 1,
        "type": "assistant",
        "message": {
            "id": "message-1",
            "model": "claude-opus-4-8",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 999,
                "cache_creation": {"ephemeral_5m_input_tokens": 30, "ephemeral_1h_input_tokens": 40},
                "output_tokens": 5,
            },
        },
    }) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "claude")
    quantities = {(atom.direction, atom.cache_role): atom.quantity for atom in atoms}

    assert quantities[("input", "write_5m")] == 30
    assert quantities[("input", "write_1h")] == 40
    assert sum(atom.quantity for atom in atoms if atom.cache_role.startswith("write")) == 70
    assert {atom.model for atom in atoms} == {"claude-opus-4-8"}


def test_normalized_usage_atoms_keep_codex_subagents_structurally_separate(tmp_path, monkeypatch):
    parent = tmp_path / "parent.jsonl"
    child = tmp_path / "child.jsonl"

    def rollout(thread_id, parent_thread_id, model, effort, output):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(item) for item in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": model, "effort": effort}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": output}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", "gpt-parent", "med", 5), encoding="utf-8")
    child.write_text(rollout("child", "parent", "gpt-child", "high", 7), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [parent, child])

    atoms = session_files.transcript_usage_atoms(parent, "codex")
    output_atoms = [atom for atom in atoms if atom.direction == "output"]

    assert {(atom.model, atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth, atom.quantity) for atom in output_atoms} == {
        ("gpt-parent", "parent", "parent", "", 0, 5.0),
        ("gpt-child", "parent", "child", "parent", 1, 7.0),
    }
    # Provider transcript usage is self-only for each rollout. If a provider
    # starts reporting parent counters cumulatively including child work, this
    # invariant must be revisited before cost atoms can remain exact.
    assert sum(atom.quantity for atom in output_atoms) == 12.0


def test_usage_atom_family_event_ids_are_stable_and_distinct_across_subagents(tmp_path, monkeypatch):
    claude = tmp_path / "session.jsonl"
    claude_child = tmp_path / "session" / "subagents" / "agent.jsonl"
    claude_child.parent.mkdir(parents=True)
    codex = tmp_path / "rollout-parent.jsonl"
    codex_child = tmp_path / "rollout-child.jsonl"

    def claude_line(output_tokens):
        return json.dumps({
            "timestamp": 100,
            "type": "assistant",
            "message": {"id": "provider-message-1", "model": "claude-opus-4-8", "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    def codex_lines(thread_id, parent_thread_id, output_tokens):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 100, "type": "turn_context", "payload": {"model": "gpt-5.6"}},
            {"timestamp": 101, "payload": {"info": {"total_token_usage": {"output_tokens": output_tokens}}}},
        ]) + "\n"

    claude.write_text(claude_line(11), encoding="utf-8")
    claude_child.write_text(claude_line(13), encoding="utf-8")
    codex.write_text(codex_lines("root", "", 17), encoding="utf-8")
    codex_child.write_text(codex_lines("child", "root", 19), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [codex, codex_child])

    first = session_files.transcript_usage_atoms(claude, "claude") + session_files.transcript_usage_atoms(codex, "codex")
    second = session_files.transcript_usage_atoms(claude, "claude") + session_files.transcript_usage_atoms(codex, "codex")

    assert [(atom.event_id, atom.direction, atom.cache_role, atom.quantity) for atom in first] == [
        (atom.event_id, atom.direction, atom.cache_role, atom.quantity) for atom in second
    ]
    identities = [(atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit) for atom in first]
    assert len(identities) == len(set(identities))
    assert sum(atom.quantity for atom in first if atom.direction == "output") == 60.0


def test_codex_child_outside_recent_candidate_window_is_documented_under_count(tmp_path):
    parent = tmp_path / "rollout-parent.jsonl"
    child = tmp_path / "rollout-child.jsonl"

    def rollout(thread_id, parent_thread_id, output_tokens):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6"}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"output_tokens": output_tokens}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", 5), encoding="utf-8")
    child.write_text(rollout("child", "parent", 7), encoding="utf-8")

    family = session_files.codex_transcript_family_paths(parent, candidates=[parent])
    atoms = session_files.transcript_usage_atoms(parent, "codex", family_paths=family)

    assert family == [parent.resolve()]
    assert sum(atom.quantity for atom in atoms if atom.direction == "output") == 5.0


def test_normalized_usage_atoms_keep_parent_child_grandchild_model_efforts_separate(tmp_path, monkeypatch):
    parent, child, grandchild = (tmp_path / name for name in ("parent.jsonl", "child.jsonl", "grandchild.jsonl"))

    def rollout(thread_id, parent_id, effort, output):
        meta = {"id": thread_id}
        if parent_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-shared", "effort": effort}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": output}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", "low", 3), encoding="utf-8")
    child.write_text(rollout("child", "parent", "high", 5), encoding="utf-8")
    grandchild.write_text(rollout("grandchild", "child", "xhigh", 7), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [parent, child, grandchild])

    output = [atom for atom in session_files.transcript_usage_atoms(parent, "codex") if atom.direction == "output"]
    assert {(atom.model, atom.effort, atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth, atom.quantity) for atom in output} == {
        ("gpt-shared", "low", "parent", "parent", "", 0, 3.0),
        ("gpt-shared", "high", "parent", "child", "parent", 1, 5.0),
        ("gpt-shared", "xhigh", "parent", "grandchild", "child", 2, 7.0),
    }


def test_direct_image_usage_atoms_require_a_correlated_request_model_and_do_not_add_total_tokens():
    response = {
        "id": "img-response",
        "usage": {
            "total_tokens": 100,
            "input_tokens": 50,
            "output_tokens": 50,
            "input_tokens_details": {"text_tokens": 10, "image_tokens": 40},
        },
    }
    atoms = session_files.direct_image_usage_atoms(
        request={"model": "gpt-image-2"}, response=response, timestamp=100, source="direct-image", request_id="request-1", root_thread_id="root", agent_thread_id="child", parent_thread_id="root", depth=1,
    )

    assert {(atom.direction, atom.modality, atom.quantity) for atom in atoms} == {
        ("input", "text", 10.0), ("input", "image", 40.0), ("output", "image", 50.0),
    }
    assert all(atom.model == "gpt-image-2" for atom in atoms)
    assert {(atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth) for atom in atoms} == {("root", "child", "root", 1)}
    assert session_files.direct_image_usage_atoms(request={}, response=response, timestamp=100, source="direct-image") == []


def test_opaque_responses_image_tool_is_visible_but_has_no_invented_model_or_token_usage():
    atoms = session_files.opaque_responses_image_tool_atoms(timestamp=100, source="responses", call_id="call-1", root_thread_id="root", agent_thread_id="child")

    assert len(atoms) == 1
    atom = atoms[0]
    assert (atom.provider, atom.model, atom.modality, atom.unit, atom.quantity) == ("openai", "unknown", "image", "requests", 1)
    assert atom.tool_name == "image_generation_call"
    assert atom.telemetry_complete is False
    assert session_files.opaque_responses_image_tool_atoms(timestamp=100, source="responses") == []


def test_codex_transcript_scan_uses_incremental_append_cache(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
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


def test_codex_transcript_raw_scan_is_shared_across_cwds_and_derives_paths(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    line = json.dumps({
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "git add relative.py"}),
        },
    }) + "\n"
    transcript.write_text(line, encoding="utf-8")
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    first = session_files.scan_codex_transcript_details(transcript, str(first_cwd), include_patch_text=False)
    second = session_files.scan_codex_transcript_details(transcript, str(second_cwd), include_patch_text=False)

    assert session_files.codex_transcript_scan_cache_key(transcript, str(first_cwd), False) == session_files.codex_transcript_scan_cache_key(transcript, str(second_cwd), True)
    assert first["changes"] == {str(first_cwd / "relative.py"): {"M"}}
    assert second["changes"] == {str(second_cwd / "relative.py"): {"M"}}
    assert [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")] == [line]


def test_historical_codex_index_reuses_warm_raw_candidates_without_decoding(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    session_files._HISTORICAL_CODEX_TRANSCRIPT_INDEX.clear()
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "second"
    first_repo.mkdir()
    second_repo.mkdir()

    def transcript(path, repo, name):
        path.write_text(json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {name}", "workdir": str(repo)}),
            },
        }) + "\n", encoding="utf-8")

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    transcript(first, first_repo, "one.py")
    transcript(second, second_repo, "two.py")
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda _cwd: None)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [first, second])

    assert session_files.historical_codex_transcript_for_cwd(str(first_repo), cutoff=0) == first
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    monkeypatch.setattr(session_files.json, "loads", lambda _value: (_ for _ in ()).throw(AssertionError("warm historical lookup must use the index")))
    assert session_files.historical_codex_transcript_for_cwd(str(second_repo), cutoff=0) == second


def test_codex_transcript_scan_cache_holds_full_recent_candidate_window(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    line = json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path)}}) + "\n"
    transcripts = []
    for index in range(CODEX_TRANSCRIPT_SCAN_LIMIT * 2):
        transcript = tmp_path / f"rollout-{index:03d}.jsonl"
        transcript.write_text(line, encoding="utf-8")
        transcripts.append(transcript)

    for transcript in transcripts:
        session_files.scan_codex_transcript_details(transcript, str(tmp_path), include_patch_text=False)

    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    for transcript in transcripts:
        session_files.scan_codex_transcript_details(transcript, str(tmp_path), include_patch_text=False)

    assert parsed_lines == []
    session_files._TRANSCRIPT_SCAN_CACHE.clear()


def test_transcript_scan_store_survives_cold_reload_and_resumes_append(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_APPEND_BYTES", 0)
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens, secret):
        return json.dumps({
            "type": "response_item",
            "secret_blob": secret,
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    first_line = line("a.py", 5, "RAW_SECRET_FIRST")
    second_line = line("b.py", 7, "RAW_SECRET_SECOND")
    third_line = line("c.py", 11, "RAW_SECRET_THIRD")
    transcript.write_text(first_line + second_line, encoding="utf-8")
    first = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    cache_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)
    assert cache_key is not None
    cache_path = session_files.transcript_scan_store_path(cache_key)
    assert cache_path.exists()
    persisted = cache_path.read_text(encoding="utf-8")
    assert "RAW_SECRET" not in persisted
    assert "git add" not in persisted
    assert oct(cache_path.stat().st_mode & 0o777) == "0o600"

    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)
    cold = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert [value for value in parsed if isinstance(value, str) and value.endswith("\n")] == []
    assert cold == first

    transcript.write_text(first_line + second_line + third_line, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    parsed.clear()
    appended = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert [value for value in parsed if isinstance(value, str) and value.endswith("\n")] == [third_line]
    assert appended["changes"] == {
        str(tmp_path / "a.py"): {"M"},
        str(tmp_path / "b.py"): {"M"},
        str(tmp_path / "c.py"): {"M"},
    }
    assert appended["usage"]["generated_tokens"] == 23


def test_transcript_scan_store_rejects_schema_tail_and_same_inode_prefix_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "claude.jsonl"

    def line(path_name, padding):
        return json.dumps({
            "type": "assistant",
            "padding": padding,
            "message": {"usage": {"output_tokens": 5}, "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": path_name}}]},
        }) + "\n"

    original = line("a.py", "x" * 5000)
    replacement = line("b.py", "x" * 5000)
    stable_tail = line("tail.py", "y" * 1000)
    assert len(original) == len(replacement)
    transcript.write_text(original + stable_tail, encoding="utf-8")
    assert str(tmp_path / "a.py") in session_files.scan_claude_transcript_details(transcript, str(tmp_path))["changes"]
    cache_key = session_files.claude_transcript_scan_cache_key(transcript)
    assert cache_key is not None
    cache_path = session_files.transcript_scan_store_path(cache_key)

    record = json.loads(cache_path.read_text(encoding="utf-8"))
    record["schema_version"] = 999
    cache_path.write_text(json.dumps(record), encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    reparsed = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "a.py") in reparsed["changes"]

    transcript.write_text(replacement + stable_tail, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    replaced = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "a.py") not in replaced["changes"]
    assert str(tmp_path / "b.py") in replaced["changes"]

    transcript.write_text(replacement, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    truncated = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "tail.py") not in truncated["changes"]


def test_transcript_scan_store_is_bounded_and_atomic_failure_is_nonfatal(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)
    for index in range(4):
        path = store_dir / f"{index}.json"
        path.write_text("x" * 10, encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))
    session_files.prune_transcript_scan_store(max_entries=2, max_bytes=100)
    assert sorted(path.name for path in store_dir.glob("*.json")) == ["2.json", "3.json"]
    session_files.prune_transcript_scan_store(max_entries=2, max_bytes=10)
    assert len(list(store_dir.glob("*.json"))) == 1

    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    monkeypatch.setattr(session_files, "atomic_write_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        details = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert details["changes"] == {}
    assert "failed to persist transcript scan cache" in caplog.text


def test_transcript_scan_cache_has_one_owner_and_bounds_claude_message_ids():
    state = session_files.new_claude_transcript_scan_state()
    for index in range(session_files._TRANSCRIPT_SCAN_MESSAGE_ID_MAX + 3):
        session_files.update_claude_transcript_scan_state(state, json.dumps({
            "type": "assistant",
            "message": {"id": f"message-{index}", "usage": {"output_tokens": 1}, "content": []},
        }))
    assert len(state["usage_tokens_by_message_id"]) == session_files._TRANSCRIPT_SCAN_MESSAGE_ID_MAX
    assert "message-0" not in state["usage_tokens_by_message_id"]
    source = Path(session_files.__file__).read_text(encoding="utf-8")
    assert "_CODEX_TRANSCRIPT_SCAN_CACHE" not in source
    assert "_CLAUDE_TRANSCRIPT_SCAN_CACHE" not in source
    assert source.count("_TRANSCRIPT_SCAN_CACHE: dict") == 1


def test_codex_transcript_scan_restarts_after_truncation(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
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
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
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
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
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


def test_transcript_scan_streams_complete_lines_without_reading_the_full_file(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("".join(
        json.dumps({
            "payload": {"info": {"total_token_usage": {"output_tokens": index + 1}}},
            "padding": "x" * 4096,
        }) + "\n"
        for index in range(256)
    ), encoding="utf-8")
    real_open = session_files.Path.open
    readline_calls = 0

    class TrackingFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def seek(self, *args):
            return self.handle.seek(*args)

        def readline(self, *args):
            nonlocal readline_calls
            readline_calls += 1
            return self.handle.readline(*args)

        def read(self, size=-1):
            assert size >= 0, "transcript scans must not materialize the full unread file"
            return self.handle.read(size)

    def tracking_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return TrackingFile(handle) if path == transcript and args and args[0] == "rb" else handle

    monkeypatch.setattr(session_files.Path, "open", tracking_open)
    details = session_files.scan_codex_transcript_details(transcript)

    assert details["usage"]["generated_tokens"] == 256
    assert readline_calls == 258  # one bounded prefix-identity read plus 256 records and EOF


def test_transcript_scanners_skip_json_for_records_without_usage_or_changes(monkeypatch):
    def unexpected_loads(_value):
        raise AssertionError("irrelevant transcript records must not be decoded into object trees")

    monkeypatch.setattr(session_files.json, "loads", unexpected_loads)
    claude_state = session_files.new_claude_transcript_scan_state()
    codex_state = session_files.new_codex_transcript_scan_state()

    session_files.update_claude_transcript_scan_state(claude_state, json.dumps({"type": "user", "message": "x" * 1000}))
    session_files.update_codex_transcript_scan_state(codex_state, json.dumps({"type": "response_item", "payload": {"text": "please run git add big-file " + ("x" * 1000)}}), None, False)

    assert claude_state["generated_tokens"] == 0
    assert codex_state["last_token_total"] is None


def test_codex_change_scan_does_not_parse_usage_only_records(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    usage_line = json.dumps({"payload": {"info": {"total_token_usage": {"output_tokens": 17}}}})
    shell_line = json.dumps({
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "git add tracked.txt", "workdir": str(tmp_path)}),
        },
    })
    transcript.write_text(usage_line + "\n" + shell_line + "\n", encoding="utf-8")
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)

    assert session_files.scan_codex_transcript(transcript, str(tmp_path), include_patch_text=False) == {str(tmp_path / "tracked.txt"): {"M"}}
    assert all("total_token_usage" not in str(value) for value in parsed)


def test_scan_claude_transcript_waits_for_partial_lines_and_resets_after_replacement(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
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
    assert payload["warnings"] == [{
        "key": "diff.warning.agentDiscovery",
        "params": {"error": "codex transcript not found by process fd or cwd"},
        "fallback": "codex transcript not found by process fd or cwd",
    }]


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
    assert payload["repos"][0]["error_message"] == {
        "key": "diff.warning.refsFallback",
        "params": {"repo": "repo"},
        "fallback": "requested refs not found in this repo; showing default",
    }

    aggregate, status = session_files.session_files_payload(
        None,
        {"s1": info, "s2": info},
        hours=24,
        from_ref=newer,
        to_ref=older,
    )
    assert status == HTTPStatus.OK
    assert aggregate["repos"][0]["error_message"] == payload["repos"][0]["error_message"]


def test_diff_ref_issue_uses_one_structured_classifier():
    assert session_files.diff_ref_issue("unknown FROM ref: missing", "missing", "current") == {
        "key": "common.unknownFromRef",
        "params": {"ref": "missing"},
        "fallback": "unknown FROM ref: missing",
    }
    assert session_files.diff_ref_issue("unknown TO ref: future", "HEAD", "future") == {
        "key": "common.unknownToRef",
        "params": {"ref": "future"},
        "fallback": "unknown TO ref: future",
    }


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


def test_untracked_line_counts_cache_by_identity_and_invalidate_on_change(tmp_path, monkeypatch):
    """Repeated payload assembly must not re-read unchanged untracked files; a
    changed file (size/mtime) is re-read and re-counted."""
    target = tmp_path / "notes.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    session_files._UNTRACKED_LINE_COUNT_CACHE.clear()

    reads = []
    real_read_bytes = Path.read_bytes

    def counting_read_bytes(self):
        reads.append(str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    assert session_files.untracked_added_line_count(target) == 3
    assert session_files.untracked_added_line_count(target) == 3
    assert len(reads) == 1  # second lookup served from the identity cache

    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert session_files.untracked_added_line_count(target) == 4  # identity changed -> re-read
    assert len(reads) == 2


def test_concurrent_views_share_one_git_identity_run(tmp_path, monkeypatch):
    """Six concurrent session-files views of one repo must pay the expensive
    `git status --untracked-files=all` signature ONCE (in-flight single-flight),
    while sequential calls still recompute so freshness is never delayed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")

    real_identity = session_files.git_snapshot_identity
    calls = []
    owner_entered = threading_module.Event()
    allow_finish = threading_module.Event()

    def gated_counted_identity(path, from_ref=None, to_ref=None):
        calls.append(str(path))
        owner_entered.set()
        # Hold the in-flight window open until the test has parked every other
        # caller on the shared future, making the coalesce deterministic.
        assert allow_finish.wait(timeout=10)
        return real_identity(path, from_ref, to_ref)

    monkeypatch.setattr(session_files, "git_snapshot_identity", gated_counted_identity)
    webapp = TmuxWebtermApp(["one"])
    try:
        results = []

        errors = []
        lined_up = threading_module.Barrier(6, timeout=10)

        def view():
            try:
                lined_up.wait()  # all six are running before any calls: no startup latency race
                results.append(webapp.shared_session_files_git_snapshot(repo, None, None))
            except BaseException as error:  # surface thread failures in the assertion
                errors.append(repr(error))

        threads = [threading_module.Thread(target=view) for _ in range(6)]
        for thread in threads:
            thread.start()
        assert owner_entered.wait(timeout=10)
        time.sleep(1.0)  # let the five coalescers park on the shared future
        allow_finish.set()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == [], errors
        assert len(results) == 6 and all(isinstance(item, dict) for item in results)
        # Exactly TWO identity runs for a six-view cold burst: one pre-build
        # signature shared by all six callers (the single-flight under test) plus
        # the snapshot owner's post-build freshness re-validation. Before the
        # single-flight this burst paid seven (six pre + one post).
        assert len(calls) == 2, f"six concurrent views ran {len(calls)} identity computations"

        # A sequential follow-up recomputes its own pre-build signature (no
        # staleness window) and hits the cached snapshot record (no post run).
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(calls) == 3
    finally:
        webapp.close() if hasattr(webapp, "close") else None


def test_session_files_runtime_counters_cover_the_bounded_accounting_dimensions(tmp_path):
    """The accounting snapshot exposes cumulative (monotonic) work counters for
    git spawns per verb, transcript-catalog traversal, append bytes parsed, and
    untracked stat/line-count work, without a second profiler."""

    before = session_files.session_files_runtime_counters()
    for key in ("append_bytes_parsed", "untracked_line_count_hits", "untracked_line_count_reads", "git_commands", "transcript_catalog"):
        assert key in before

    # untracked read then identity hit
    target = tmp_path / "untracked.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    assert session_files.untracked_added_line_count(target) == 2
    assert session_files.untracked_added_line_count(target) == 2
    # append bytes
    state: dict[str, object] = {}
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"a":1}\n', encoding="utf-8")
    session_files.scan_transcript_append(transcript, 0, lambda line: None, state)
    # one git spawn and one catalog traversal
    common_module.git(["version"], cwd=str(tmp_path))
    sessions_module._cataloged_jsonl_files(tmp_path)

    after = session_files.session_files_runtime_counters()
    assert after["untracked_line_count_reads"] == before["untracked_line_count_reads"] + 1
    assert after["untracked_line_count_hits"] == before["untracked_line_count_hits"] + 1
    assert after["append_bytes_parsed"] == before["append_bytes_parsed"] + len('{"a":1}\n')
    assert after["git_commands"].get("version", 0) == before["git_commands"].get("version", 0) + 1
    assert after["transcript_catalog"]["calls"] == before["transcript_catalog"]["calls"] + 1
    assert after["transcript_catalog"]["dirs_statted"] > before["transcript_catalog"]["dirs_statted"]


def test_repo_state_record_warm_hit_runs_zero_git_commands_and_dirty_event_recomputes(tmp_path, monkeypatch):
    """Repository-state record (native-watcher backed): with the watcher healthy
    and no event for the repo, a warm identity request runs ZERO Git commands;
    a worktree or .git-metadata event bumps the dirty generation and exactly the
    next request recomputes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")

    monkeypatch.setattr(TmuxWebtermApp, "discover_and_start", lambda self: None, raising=False)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = TmuxWebtermApp(["1"])
    try:
        resolved_repo = repo.resolve()
        # Simulate a healthy native watcher covering this repo.
        record = webapp.client_watch_service.event_watcher_record
        record.filesystem_healthy = True
        record.filesystem_roots = (str(tmp_path.resolve()),)
        record.filesystem_watch_paths = (str(tmp_path.resolve()),)

        identity_one, status_one = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_one == "computed"

        # Warm: identical result, zero additional git spawns.
        spawns_before = dict(common_module.GIT_COMMAND_COUNTS)
        identity_two, status_two = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_two == "watcher-cached"
        assert identity_two == identity_one
        assert dict(common_module.GIT_COMMAND_COUNTS) == spawns_before

        # A worktree event dirties the record; the next request recomputes and
        # sees the change immediately.
        (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
        webapp.mark_repo_state_dirty([resolved_repo / "one.py"])
        identity_three, status_three = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_three == "computed"
        assert identity_three != identity_one
        # One dirty event causes AT MOST one follow-up: the next request is
        # served from the record again with zero Git commands.
        spawns_after_recompute = dict(common_module.GIT_COMMAND_COUNTS)
        _identity, status_again = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_again == "watcher-cached"
        assert dict(common_module.GIT_COMMAND_COUNTS) == spawns_after_recompute

        # A pure commit (only .git metadata changes) also dirties the record.
        git(repo, "add", "one.py")
        git(repo, "commit", "-m", "second")
        webapp.mark_repo_state_dirty([resolved_repo / ".git" / "HEAD"])
        identity_four, status_four = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_four == "computed"
        assert identity_four != identity_three

        # Watcher unhealthy -> fail open: always compute.
        record.filesystem_healthy = False
        _identity, status_five = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_five == "computed"
    finally:
        webapp.control_server.stop()


def test_git_metadata_event_filter_admits_identity_inputs_only(tmp_path):
    """The watch filter admits exactly the .git paths that change
    git_snapshot_identity (HEAD, index, refs, packed-refs, MERGE_HEAD) and keeps
    objects/ and logs/ churn out."""

    webapp = object.__new__(TmuxWebtermApp)
    record = ClientEventWatcherRecord()
    record.filesystem_watch_paths = (str(tmp_path),)
    git_dir = tmp_path / "repo" / ".git"
    assert webapp.git_metadata_event_allowed(git_dir / "HEAD", record) is True
    assert webapp.git_metadata_event_allowed(git_dir / "index", record) is True
    assert webapp.git_metadata_event_allowed(git_dir / "packed-refs", record) is True
    assert webapp.git_metadata_event_allowed(git_dir / "MERGE_HEAD", record) is True
    assert webapp.git_metadata_event_allowed(git_dir / "refs" / "heads" / "main", record) is True
    assert webapp.git_metadata_event_allowed(git_dir / "objects" / "ab" / "cdef", record) is False
    assert webapp.git_metadata_event_allowed(git_dir / "logs" / "HEAD", record) is False
    assert webapp.git_metadata_event_allowed(Path("/outside/.git/HEAD"), record) is False
    # The general filter routes .git paths through this policy.
    assert webapp.native_filesystem_event_allowed(git_dir / "objects" / "pack" / "p.idx", record) is False

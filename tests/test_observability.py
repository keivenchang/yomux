import json
import time
from http import HTTPStatus

import pytest

from yolomux_lib import events as events_module
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.events import EventLog
from yolomux_lib.events import RunHistoryStore
from yolomux_lib.metadata import empty_work_graph
from yolomux_lib.transcripts import format_transcript_item
from yolomux_lib.transcripts import transcript_items_from_raw_line


def write_event_lines(path, events):
    path.write_text(
        "\n".join(json.dumps(event) if isinstance(event, dict) else str(event) for event in events) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def make_app(make_tmux_webterm_app, tmp_path):
    def factory(sessions=("s1",)):
        app = make_tmux_webterm_app(sessions)
        app.event_log = EventLog(tmp_path / "events.jsonl")
        app.run_history_store = RunHistoryStore(tmp_path / "run-history.json")
        app.refresh_sessions = lambda *args, **kwargs: []
        # Observability unit tests replace the durable event-storage slice; production writes
        # additionally publish their invalidation through the full app event broker.
        app.publish_background_client_event = lambda *_args, **_kwargs: {}
        return app

    return factory


def test_event_log_search_filters_session_and_details(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("s1", "state_changed", "Needs approval", {"prompt": "run tests"})
    log.append("s2", "state_changed", "Other event", {"prompt": "deploy"})

    matches = log.search("tests", session="s1")

    assert len(matches) == 1
    assert matches[0]["session"] == "s1"
    assert matches[0]["details"]["prompt"] == "run tests"
    assert log.search("deploy", session="s1") == []


def test_event_log_persists_structured_message_with_raw_fallback(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")

    event = log.append(
        "s1",
        "yolo_enabled",
        "YOLO enabled",
        {"persist": True},
        message_key="events.message.yolo.enabled",
        message_params={"unused": "safe"},
    )

    assert event["message"] == "YOLO enabled"
    assert event["message_key"] == "events.message.yolo.enabled"
    assert event["message_params"] == {"unused": "safe"}
    assert log.tail(session="s1") == [event]


def test_event_search_result_preserves_structured_title_and_snippet(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(
        "s1",
        "yolo_enabled",
        "YOLO enabled",
        {"persist": True},
        message_key="events.message.yolo.enabled",
    )

    result = log.search_results("enabled", session="s1")[0]

    assert result["title"] == "YOLO enabled"
    assert result["title_key"] == "events.message.yolo.enabled"
    assert result["title_params"] == {}
    assert result["snippet_key"] == "events.message.yolo.enabled"
    assert result["snippet_params"] == {}


def test_persisted_event_keeps_nested_chrome_descriptor_and_raw_diagnostic(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    reason = {
        "key": "yoagent.error.targetSessionMissing",
        "params": {"session": "s9"},
        "fallback": "target session s9 is missing",
    }
    log.append(
        "s9",
        "yoagent_job_failed",
        "YO!agent job failed: target session s9 is missing",
        {"diagnostic": "tmux target s9 disappeared"},
        message_key="yoagent.job.notification.failed",
        message_params={"id": "yj_9", "reason": reason},
    )

    persisted = EventLog(path).tail(session="s9")[0]
    result = EventLog(path).search_results("disappeared", session="s9")[0]

    assert persisted["details"]["diagnostic"] == "tmux target s9 disappeared"
    assert persisted["message_params"]["reason"] == reason
    assert result["title"] == "YO!agent job failed: target session s9 is missing"
    assert result["title_key"] == "yoagent.job.notification.failed"
    assert result["title_params"] == {"id": "yj_9", "reason": reason}


def test_event_log_legacy_message_keeps_compatible_raw_payload(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")

    event = log.append("s1", "legacy", "legacy raw message", {})

    assert event["message"] == "legacy raw message"
    assert event["message_key"] == ""
    assert event["message_params"] == {}


def test_auto_event_descriptor_is_promoted_out_of_diagnostic_details(tmp_path, make_app):
    app = make_app()

    app.log_auto_event(
        "s1",
        "approval_approved",
        "approved bash: make test",
        {
            "message_key": "yolo.status.approvedBash",
            "message_params": {"description": "make test"},
            "prompt_type": "bash",
        },
    )

    event = app.event_log.tail(session="s1")[0]
    assert event["message_key"] == "yolo.status.approvedBash"
    assert event["message_params"] == {"description": "make test"}
    assert event["details"] == {"prompt_type": "bash"}


def test_event_log_append_uses_process_lock(monkeypatch, tmp_path):
    calls = []

    def fake_flock(_fd, operation):
        calls.append(operation)

    monkeypatch.setattr(events_module.fcntl, "flock", fake_flock)
    log = EventLog(tmp_path / "events.jsonl")

    log.append("s1", "state_changed", "Needs approval", {})

    assert calls == [events_module.fcntl.LOCK_EX, events_module.fcntl.LOCK_UN]
    assert (tmp_path / ".events.jsonl.lock").exists()


def test_event_log_tail_preserves_session_global_filtering_and_bounds(tmp_path):
    path = tmp_path / "events.jsonl"
    write_event_lines(path, [
        {"time": "1", "session": "s1", "type": "note", "message": "s1 old"},
        "{not json",
        {"time": "2", "session": "", "type": "note", "message": "global old"},
        ["not", "an", "event"],
        {"time": "3", "session": "s2", "type": "note", "message": "s2 event"},
        {"time": "4", "session": "s1", "type": "note", "message": "s1 new"},
        {"time": "5", "session": "", "type": "note", "message": "global new"},
        {"time": "6", "session": "s1", "type": "note", "message": "s1 final"},
    ])
    log = EventLog(path)

    assert [event["message"] for event in log.tail(session="s1", limit=3)] == ["s1 new", "global new", "s1 final"]
    assert [event["message"] for event in log.tail(session="s2", limit=2)] == ["s2 event", "global new"]
    assert [event["message"] for event in log.tail(limit=3)] == ["s1 new", "global new", "s1 final"]
    assert [event["message"] for event in log.tail(session="s1", limit=0)] == ["s1 final"]


def test_event_log_tail_timing_regression_stops_before_old_history(monkeypatch, tmp_path):
    path = tmp_path / "events.jsonl"
    old_events = [
        {"time": str(index), "session": "s1", "type": "note", "message": f"slow old {index}"}
        for index in range(200)
    ]
    recent_events = [
        {"time": "201", "session": "s1", "type": "note", "message": "recent 1"},
        {"time": "202", "session": "s1", "type": "note", "message": "recent 2"},
        {"time": "203", "session": "s1", "type": "note", "message": "recent 3"},
    ]
    write_event_lines(path, [*old_events, *recent_events])
    log = EventLog(path)
    real_loads = events_module.json.loads

    def timing_loads(raw):
        if "slow old" in str(raw):
            time.sleep(0.003)
        return real_loads(raw)

    monkeypatch.setattr(events_module.json, "loads", timing_loads)

    started = time.perf_counter()
    messages = [event["message"] for event in log.tail(session="s1", limit=3)]
    elapsed = time.perf_counter() - started

    assert messages == ["recent 1", "recent 2", "recent 3"]
    assert elapsed < 0.25


def test_event_log_tail_many_matches_per_session_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    write_event_lines(path, [
        {"time": "1", "session": "s1", "type": "note", "message": "s1 old"},
        {"time": "2", "session": "", "type": "note", "message": "global old"},
        {"time": "3", "session": "s2", "type": "note", "message": "s2 old"},
        {"time": "4", "session": "s1", "type": "note", "message": "s1 new"},
        {"time": "5", "session": "s2", "type": "note", "message": "s2 new"},
        {"time": "6", "session": "", "type": "note", "message": "global new"},
    ])
    log = EventLog(path)

    tails = log.tail_many(["s1", "s2"], limit=3)

    assert tails["s1"] == log.tail(session="s1", limit=3)
    assert tails["s2"] == log.tail(session="s2", limit=3)
    assert [event["message"] for event in tails["s1"]] == ["global old", "s1 new", "global new"]
    assert [event["message"] for event in tails["s2"]] == ["s2 old", "s2 new", "global new"]


def test_search_payload_combines_events_and_current_summaries(tmp_path, make_app):
    app = make_app()
    app.event_log.append("s1", "note", "alpha event", {"detail": "beta event detail"})
    app.summary = lambda session: ({"text": f"{session} beta summary"}, HTTPStatus.OK)

    payload, status = app.search_payload("beta")

    assert status == HTTPStatus.OK
    assert payload["strategy"] == "scan-on-query"
    assert payload["result_shape"] == ["session", "timestamp", "kind", "snippet", "target"]
    assert payload["events"][0]["session"] == "s1"
    assert payload["summaries"][0]["session"] == "s1"
    assert "beta summary" in payload["summaries"][0]["text"]
    assert {result["source"] for result in payload["results"]} == {"event", "session_summary"}
    event_result = next(result for result in payload["results"] if result["source"] == "event")
    summary_result = next(result for result in payload["results"] if result["source"] == "session_summary")
    assert event_result["snippet"]
    assert event_result["target"] == {
        "type": "events",
        "session": "s1",
        "timestamp": event_result["timestamp"],
        "tab": "events",
    }
    assert summary_result["snippet"] == "s1 beta summary"
    assert summary_result["title_key"] == "searchHistory.result.sessionSummary"
    assert summary_result["title_params"] == {"session": "s1"}
    assert summary_result["target"]["tab"] == "summary"


def test_transcript_items_keep_structured_role_timestamp_and_cwd():
    raw = json.dumps({
        "timestamp": "2026-07-02T01:02:03Z",
        "cwd": "/repo/demo",
        "message": {"role": "assistant", "content": "done"},
    })

    item = transcript_items_from_raw_line(raw)[0]

    assert item["role"] == "assistant"
    assert item["timestamp"] == "2026-07-02T01:02:03Z"
    assert item["cwd"] == "/repo/demo"
    assert item["text"] == "done"
    assert "header" not in item
    assert format_transcript_item(item) == "assistant (2026-07-02T01:02:03Z, /repo/demo)\ndone"


def test_run_history_payload_summarizes_and_persists_live_session(monkeypatch, tmp_path, make_app):
    app = make_app()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "please ship beta rollout"}],
                },
            }),
            json.dumps({
                "timestamp": "2026-01-01T00:05:00Z",
                "type": "task_complete",
                "payload": {"type": "task_complete"},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path=str(tmp_path),
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
        process_label="codex",
    )
    agent = AgentInfo(
        session="s1",
        kind="codex",
        pid=123,
        pane_target="%1",
        command="codex",
        cwd=str(tmp_path),
        status=None,
        session_id=None,
        transcript=str(transcript),
        error=None,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[agent])
    monkeypatch.setattr("yolomux_lib.app.discover_sessions", lambda _sessions: ({"s1": info}, ["tmux discovery diagnostic"]))
    graph = empty_work_graph()
    graph["git_worktrees"]["worktree:repo"] = {
        "id": "worktree:repo",
        "root": str(tmp_path),
        "git_dir": str(tmp_path / ".git"),
        "kind": "primary",
        "local_repository_id": "local:repo",
        "current_branch_id": "branch:repo:feature/search",
        "branch_activity_ids": [],
        "path_observation_ids": [],
        "activity_priority": 0,
        "activity_ts": 0,
        "activity_source": "",
        "has_current_pull_request": True,
        "git": {"root": str(tmp_path), "branch": "feature/search"},
    }
    graph["local_repositories"]["local:repo"] = {"id": "local:repo", "common_git_dir": str(tmp_path / ".git"), "local_branch_ids": ["branch:repo:feature/search"]}
    graph["hosted_repositories"]["hosted:repo"] = {"id": "hosted:repo", "url": "https://example.test/repo"}
    graph["local_branches"]["branch:repo:feature/search"] = {"id": "branch:repo:feature/search", "local_repository_id": "local:repo", "name": "feature/search", "pull_request_ids": ["pr:42"], "linear_issue_ids": []}
    graph["pull_requests"]["pr:42"] = {"id": "pr:42", "hosted_repository_id": "hosted:repo", "number": 42, "title": "Add search", "url": "https://example.test/pull/42", "state": "open", "draft": False, "description": "not stored in compact history", "linear_issue_ids": []}
    monkeypatch.setattr("yolomux_lib.app.session_work_graph", lambda _info, _cache, allow_network=False: graph)
    app.yoagent_session_summaries = {
        "s1": {
            "rolling_summary": "beta rollout finished",
            "updated_ts": 1760000000,
            "state": "done",
        },
    }
    app.event_log.append("s1", "state_changed", "ready", {})

    payload, status = app.run_history_payload()

    assert status == HTTPStatus.OK
    row = payload["runs"][0]
    assert row["id"] == f"codex:{transcript}"
    assert row["session"] == "s1"
    assert row["agent"]["kind"] == "codex"
    assert row["prompt"] == "please ship beta rollout"
    assert row["cwd"] == str(tmp_path)
    assert row["started_at"] == "2026-01-01T00:00:00+00:00"
    assert row["ended_at"] == "2026-01-01T00:05:00+00:00"
    assert row["final_state"] == "done"
    assert row["pr"] == {
        "number": 42,
        "title": "Add search",
        "url": "https://example.test/pull/42",
        "state": "open",
        "draft": False,
    }
    assert row["latest_summary"] == "beta rollout finished"
    assert row["latest_summary_updated_ts"] == 1760000000
    assert row["recent_events"][0]["message"] == "ready"
    assert payload["errors"] == [{
        "message": "tmux discovery diagnostic",
        "message_key": "searchHistory.error.discovery",
        "message_params": {"error": "tmux discovery diagnostic"},
    }]
    assert (tmp_path / "run-history.json").exists()

    app.sessions = []
    stored_payload, stored_status = app.run_history_payload("s1")

    assert stored_status == HTTPStatus.OK
    assert stored_payload["runs"][0]["prompt"] == "please ship beta rollout"

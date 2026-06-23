import json
import threading
from http import HTTPStatus

from yolomux_lib import events as events_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.events import EventLog
from yolomux_lib.events import RunHistoryStore
from yolomux_lib.metadata import MetadataCache


def make_app(tmp_path, sessions=("s1",)):
    app = TmuxWebtermApp.__new__(TmuxWebtermApp)
    app.sessions = list(sessions)
    app.event_log = EventLog(tmp_path / "events.jsonl")
    app.run_history_store = RunHistoryStore(tmp_path / "run-history.json")
    app.metadata_cache = MetadataCache()
    app.metadata_warm_lock = threading.Lock()
    app.metadata_warm_running = False
    app.yoagent_session_summary_lock = threading.RLock()
    app.yoagent_session_summaries = {}
    app.refresh_sessions = lambda *args, **kwargs: []
    return app


def test_event_log_search_filters_session_and_details(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("s1", "state_changed", "Needs approval", {"prompt": "run tests"})
    log.append("s2", "state_changed", "Other event", {"prompt": "deploy"})

    matches = log.search("tests", session="s1")

    assert len(matches) == 1
    assert matches[0]["session"] == "s1"
    assert matches[0]["details"]["prompt"] == "run tests"
    assert log.search("deploy", session="s1") == []


def test_event_log_append_uses_process_lock(monkeypatch, tmp_path):
    calls = []

    def fake_flock(_fd, operation):
        calls.append(operation)

    monkeypatch.setattr(events_module.fcntl, "flock", fake_flock)
    log = EventLog(tmp_path / "events.jsonl")

    log.append("s1", "state_changed", "Needs approval", {})

    assert calls == [events_module.fcntl.LOCK_EX, events_module.fcntl.LOCK_UN]
    assert (tmp_path / ".events.jsonl.lock").exists()


def test_search_payload_combines_events_and_current_summaries(tmp_path):
    app = make_app(tmp_path)
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
    assert summary_result["target"]["tab"] == "summary"


def test_run_history_payload_summarizes_and_persists_live_session(monkeypatch, tmp_path):
    app = make_app(tmp_path)
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
    monkeypatch.setattr("yolomux_lib.app.discover_sessions", lambda _sessions: ({"s1": info}, []))
    monkeypatch.setattr(
        "yolomux_lib.app.session_project_metadata",
        lambda _info, _cache, allow_network=False: {
            "git": {"root": str(tmp_path), "branch": "feature/search"},
            "pull_request": {
                "number": 42,
                "title": "Add search",
                "url": "https://example.test/pull/42",
                "state": "open",
                "draft": False,
                "description": "not stored in compact history",
            },
            "linear": [],
        },
    )
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
    assert (tmp_path / "run-history.json").exists()

    app.sessions = []
    stored_payload, stored_status = app.run_history_payload("s1")

    assert stored_status == HTTPStatus.OK
    assert stored_payload["runs"][0]["prompt"] == "please ship beta rollout"

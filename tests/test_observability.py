import threading
from http import HTTPStatus

from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.events import EventLog
from yolomux_lib.metadata import MetadataCache


def make_app(tmp_path, sessions=("s1",)):
    app = TmuxWebtermApp.__new__(TmuxWebtermApp)
    app.sessions = list(sessions)
    app.event_log = EventLog(tmp_path / "events.jsonl")
    app.metadata_cache = MetadataCache()
    app.metadata_warm_lock = threading.Lock()
    app.metadata_warm_running = False
    app.refresh_sessions = lambda: []
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


def test_search_payload_combines_events_and_current_summaries(tmp_path):
    app = make_app(tmp_path)
    app.event_log.append("s1", "note", "alpha event", {})
    app.summary = lambda session: ({"text": f"{session} beta summary"}, HTTPStatus.OK)

    payload, status = app.search_payload("beta")

    assert status == HTTPStatus.OK
    assert payload["events"] == []
    assert payload["summaries"][0]["session"] == "s1"
    assert "beta summary" in payload["summaries"][0]["text"]


def test_run_history_payload_summarizes_live_session(monkeypatch, tmp_path):
    app = make_app(tmp_path)
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
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
    app.event_log.append("s1", "state_changed", "ready", {})

    payload, status = app.run_history_payload()

    assert status == HTTPStatus.OK
    assert payload["runs"][0]["session"] == "s1"
    assert payload["runs"][0]["agent"]["kind"] == "codex"
    assert payload["runs"][0]["recent_events"][0]["message"] == "ready"

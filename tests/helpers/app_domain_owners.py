from __future__ import annotations

from yolomux_lib import app as app_module


def assert_composed_owners_preserve_facade_overrides(monkeypatch) -> None:
    webapp = app_module.TmuxWebtermApp([])
    calls = []
    monkeypatch.setattr(webapp, "stop_client_event_watcher", lambda: calls.append("watch-stop"))
    try:
        assert isinstance(webapp._watch_bridge.state, app_module.ClientWatchService)
        assert webapp.client_watch_service is webapp._watch_bridge.state
        assert webapp.session_files_service is webapp._session_files_coordinator.state
        assert webapp.activity_transcript_service is webapp._activity_cache.state
        assert webapp.backend_health_store is webapp._system_status_projector.backend_health_store
        webapp._watch_bridge.stop()
        assert calls == ["watch-stop"]
        sentinel = [{"owner": "watch-bridge"}]
        monkeypatch.setattr(webapp._watch_bridge, "client_event_recurring_work_snapshot", lambda app, record, now=None: sentinel)
        assert webapp.client_event_recurring_work_snapshot(webapp.client_watch_service.event_watcher_record) is sentinel
    finally:
        webapp.control_server.stop()

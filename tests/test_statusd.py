from __future__ import annotations

import json
import types
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from threading import Thread

from yolomux_lib import sessions as sessions_mod
from yolomux_lib import statusd
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.statusd_client import StatusClient
from yolomux_lib.statusd_protocol import validate_inventory


class FakeStatusApp:
    builds = 0
    fail = False

    def __init__(self, sessions, **_kwargs):
        self.sessions = list(sessions)

    def build_auto_approve_status(self, *, timings, sync_workers):
        assert sync_workers is False
        FakeStatusApp.builds += 1
        if FakeStatusApp.fail:
            raise RuntimeError("unavailable")
        timings["discover_sessions"] = 0.0
        return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200


def test_statusd_reuses_one_encoded_snapshot_and_retains_stale_bytes(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": 1, "sessions": ["1"]}

    first, first_bytes = service.handle(request)
    second, second_bytes = service.handle(request)

    assert first["generation"] == second["generation"] == 1
    assert first_bytes == second_bytes
    assert FakeStatusApp.builds == 1
    service.handle({"action": "invalidate", "protocol_version": 1, "reason": "settings"})
    FakeStatusApp.fail = True
    stale, stale_bytes = service.handle(request)
    assert stale["stale"] is True
    assert stale_bytes == first_bytes
    assert service.status()["build_count"] == 1


def test_statusd_rejects_invalid_session_input_without_building(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(Path(tmp_path / "statusd.sock"))

    response, body = service.handle({"action": "snapshot", "protocol_version": 1, "sessions": ["1", 2]})

    assert response["ok"] is False
    assert body == b""
    assert FakeStatusApp.builds == 0


def test_statusd_concurrent_demand_builds_one_shared_generation(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": 1, "sessions": ["1"]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(service.handle, [request, request]))

    assert FakeStatusApp.builds == 1
    assert results[0][0]["generation"] == results[1][0]["generation"] == 1
    assert results[0][1] == results[1][1]


def test_two_clients_share_one_statusd_pid_and_encoded_generation(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    first = StatusClient(socket_path)
    second = StatusClient(socket_path)
    try:
        assert first.ensure_started() is True
        assert second.ensure_started() is True
        first_response, first_body = first.snapshot(["1"])
        second_response, second_body = second.snapshot(["1"])
        first_pid = first.request({"action": "status"}).get("pid")
        second_pid = second.request({"action": "status"}).get("pid")
    finally:
        first.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert first_response["generation"] == second_response["generation"] == 1
    assert first_body == second_body
    assert first_pid == second_pid
    assert FakeStatusApp.builds == 1
    assert thread.is_alive() is False


def test_two_web_apps_forward_one_shared_statusd_snapshot_without_local_build(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    first_app = TmuxWebtermApp(["1"])
    second_app = TmuxWebtermApp(["1"])
    first_app.status_client = StatusClient(socket_path)
    second_app.status_client = StatusClient(socket_path)
    monkeypatch.setattr(first_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("first web app built status")))
    monkeypatch.setattr(second_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("second web app built status")))
    try:
        first_body, first_status = first_app.auto_approve_status_bytes()
        second_body, second_status = second_app.auto_approve_status_bytes()
    finally:
        first_app.control_server.stop()
        second_app.control_server.stop()
        first_app.status_client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert first_status == second_status == 200
    assert first_body == second_body
    assert FakeStatusApp.builds == 1
    assert thread.is_alive() is False


def _fake_status_info(session, cwd, kind="claude"):
    pane = types.SimpleNamespace(target=f"{session}:0.0", window="0", pane="0", current_path=cwd, active=True)
    agent = types.SimpleNamespace(kind=kind, pane_target=f"{session}:0.0")
    return types.SimpleNamespace(session=session, panes=[pane], agents=[agent])


def test_statusd_inventory_discovers_daemon_roster_and_bumps_generation_only_on_change(monkeypatch, tmp_path):
    # The daemon owns the roster: even though the web hint says ["ignored"], the
    # inventory must reflect the tmux-enumerated roster the daemon discovers itself.
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: (["alpha"], None))
    state = {"cwd": "/repoA"}

    def fake_discover(names, enrich_paths=True):
        assert names == ["alpha"], "daemon roster must win over the web hint"
        return ({"alpha": _fake_status_info("alpha", state["cwd"])}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    meta1, body1 = service.handle({"action": "inventory", "protocol_version": 1, "sessions": ["ignored"]})
    payload1 = json.loads(body1)
    assert payload1["roster"] == ["alpha"] and payload1["roster_source"] == "daemon"
    assert payload1["sessions"]["alpha"]["source_signature"]
    validate_inventory(meta1, body1)
    gen1 = meta1["inventory_generation"]

    # Unchanged topology reuses the same generation.
    meta2, _ = service.handle({"action": "inventory", "protocol_version": 1})
    assert meta2["inventory_generation"] == gen1

    # A pane cwd change bumps the source signature and the inventory generation.
    state["cwd"] = "/repoB"
    meta3, _ = service.handle({"action": "inventory", "protocol_version": 1})
    assert meta3["inventory_generation"] == gen1 + 1


def test_statusd_inventory_uses_lightweight_discovery_without_path_enrichment(monkeypatch, tmp_path):
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: (["alpha"], None))
    enrich_calls = []

    def fake_discover(names, enrich_paths=True):
        enrich_calls.append(enrich_paths)
        return ({"alpha": _fake_status_info("alpha", "/repoA")}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    meta, _ = service.handle({"action": "inventory", "protocol_version": 1})

    assert meta["ok"] is True
    # The status/inventory path must never trigger heavy path enrichment.
    assert enrich_calls == [False]


def test_statusd_inventory_falls_back_to_web_hint_when_tmux_enumeration_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: ([], "tmux error"))

    def fake_discover(names, enrich_paths=True):
        assert names == ["hinted"]
        return ({"hinted": _fake_status_info("hinted", "/repoA")}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    _meta, body = service.handle({"action": "inventory", "protocol_version": 1, "sessions": ["hinted"]})
    payload = json.loads(body)

    assert payload["roster"] == ["hinted"] and payload["roster_source"] == "hint"


def test_web_status_byte_forwarder_never_calls_in_process_status_builder(monkeypatch):
    app = TmuxWebtermApp(["1"])
    encoded = b'{"session_order":["1"],"sessions":{}}'
    monkeypatch.setattr(app.status_client, "snapshot", lambda sessions, session=None, timeout=1.0: ({"ok": True, "protocol_version": 1, "generation": 7, "status": 200, "stale": False, "built_at": 1.0}, encoded))
    monkeypatch.setattr(app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status")))
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert body is encoded
    assert status == 200


def test_web_read_returns_service_unavailable_without_building_when_statusd_down(monkeypatch):
    # Case A: statusd reports unavailable. The web read must forward a structured 503 and never fall
    # back to building status in the web process.
    app = TmuxWebtermApp(["1"])
    monkeypatch.setattr(app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(app.status_client, "snapshot", lambda sessions, session=None, timeout=1.0: ({"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""))
    monkeypatch.setattr(app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status when statusd is down")))
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert body == b'{"error":"status service unavailable"}'


def test_web_read_forwards_stale_bytes_when_statusd_build_fails_after_invalidation(monkeypatch, tmp_path):
    # Case B: a real statusd builds once, is invalidated, then its next build fails. The web read must
    # forward the retained stale bytes (stale=True) without the web process building anything, and
    # statusd's successful build_count must stay at 1.
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    web_app = TmuxWebtermApp(["1"])
    web_app.status_client = StatusClient(socket_path)
    monkeypatch.setattr(web_app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(web_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status")))
    try:
        fresh_body, fresh_status = web_app.auto_approve_status_bytes()
        assert fresh_status == 200
        assert service.status()["build_count"] == 1

        web_app.status_client.invalidate("auto_approve")
        FakeStatusApp.fail = True

        response, raw_body = web_app.status_client.snapshot(["1"])
        stale_body, stale_status = web_app.auto_approve_status_bytes()
    finally:
        web_app.control_server.stop()
        web_app.status_client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert response["ok"] is True
    assert response["stale"] is True
    assert raw_body == fresh_body
    assert stale_status == 200
    assert stale_body == fresh_body
    assert service.status()["build_count"] == 1
    assert thread.is_alive() is False

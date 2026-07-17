import logging
from types import SimpleNamespace

import pytest

from yolomux_lib import http_routes
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.server_logs import install_server_log_handler
from yolomux_lib.server_logs import ServerLogRing


def test_server_log_ring_preserves_levels_order_and_metadata():
    ring = ServerLogRing(capacity=8)

    for level in ("info", "warning", "debug", "error"):
        ring.emit(level, "tests", f"{level} message", category="diagnostic")

    payload = ring.payload()
    assert payload["ok"] is True
    assert [entry["level"] for entry in payload["logs"]] == ["info", "warning", "debug", "error"]
    assert [entry["id"] for entry in payload["logs"]] == [1, 2, 3, 4]
    assert all(entry["source"] == "tests" and entry["category"] == "diagnostic" for entry in payload["logs"])
    assert payload["sequence"] == 4
    assert payload["capacity"] == 8


def test_server_log_ring_is_bounded_and_deduplicates_for_a_window(monkeypatch):
    clock = iter([10.0, 10.0, 10.0, 10.0, 11.0, 11.0, 30.0, 30.0])
    monkeypatch.setattr("yolomux_lib.server_logs.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("yolomux_lib.server_logs.time.time", lambda: next(clock))
    ring = ServerLogRing(capacity=2)

    assert ring.emit("warning", "sessions", "fallback", dedupe_key="pid:1", dedupe_seconds=15) is not None
    assert ring.emit("warning", "sessions", "duplicate", dedupe_key="pid:1", dedupe_seconds=15) is None
    assert ring.emit("info", "server", "later") is not None
    assert ring.emit("error", "server", "newest") is not None

    assert [entry["message"] for entry in ring.payload()["logs"]] == ["later", "newest"]
    with pytest.raises(ValueError):
        ring.emit("verbose", "tests", "unsupported")


def test_logs_route_reads_the_shared_bounded_ring():
    SERVER_LOGS.clear()
    SERVER_LOGS.emit("info", "server", "ready")
    writes = []
    request = SimpleNamespace(write_json=lambda payload: writes.append(payload))

    http_routes.get_server_logs(request, SimpleNamespace(query=""), None)

    assert writes[0]["capacity"] == SERVER_LOGS.capacity
    assert writes[0]["logs"][-1]["message"] == "ready"
    route = next(route for route in http_routes.CORE_ROUTES if route.path == "/api/logs")
    assert route.method == "GET" and route.role == "readonly"
    SERVER_LOGS.clear()


def test_installed_handler_captures_process_warnings_once():
    SERVER_LOGS.clear()
    root = logging.getLogger()
    handler = install_server_log_handler()
    try:
        assert install_server_log_handler() is handler
        logging.getLogger("yolomux_lib.test").warning("collector unavailable")

        payload = SERVER_LOGS.payload()
        assert len(payload["logs"]) == 1
        assert payload["logs"][0]["level"] == "warning"
        assert payload["logs"][0]["source"] == "yolomux_lib.test"
        assert payload["logs"][0]["message"] == "collector unavailable"
    finally:
        root.removeHandler(handler)
        SERVER_LOGS.clear()

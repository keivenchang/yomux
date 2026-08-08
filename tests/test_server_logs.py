from datetime import datetime
from datetime import timezone
import json
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
    assert isinstance(payload["epoch"], str) and len(payload["epoch"]) == 32
    assert [entry["level"] for entry in payload["logs"]] == ["info", "warning", "debug", "error"]
    assert [entry["id"] for entry in payload["logs"]] == [1, 2, 3, 4]
    assert all(entry["source"] == "tests" and entry["category"] == "diagnostic" for entry in payload["logs"])
    assert payload["sequence"] == 4
    assert payload["capacity"] == 8

    first_epoch = payload["epoch"]
    ring.clear()
    assert ring.payload()["epoch"] != first_epoch


def test_server_log_ring_redacts_recognized_fields_before_retention_and_emits_pt_wall_time(monkeypatch):
    secret = "fixture-share-token-never-log"
    tokenized = lambda label: f"{label}?token={secret}"
    fixed_now = datetime(2026, 1, 15, 12, 34, 56, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr("yolomux_lib.server_logs.time.time", lambda: fixed_now)
    ring = ServerLogRing(capacity=8)

    ring.emit("error", "browser/api", tokenized("GET /api/fs/raw failed"), category="browser")
    assert secret not in json.dumps(ring.payload(), sort_keys=True)

    entry = ring.emit(
        "error",
        "browser/api",
        tokenized("GET /api/fs/raw failed"),
        category="browser",
        request_id=tokenized("request-1"),
        route=tokenized("/api/fs/raw"),
        event=tokenized("request-failed"),
        delivery=tokenized("failed"),
    )
    assert entry is not None
    retained = ring.payload()["logs"][-1]
    assert retained == entry
    assert retained["wallTime"] == "2026-01-15 04:34:56 PST"
    assert retained["level"] == "error"
    assert retained["source"] == "browser/api"
    assert retained["category"] == "browser"
    for field in ("message", "requestId", "route", "event", "delivery"):
        assert retained[field].endswith("?token=[redacted-share-token]")
    assert secret not in json.dumps(ring.payload(), sort_keys=True)


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
    assert ring.payload()["dropped"] == {
        "count": 1,
        "first_id": 1,
        "last_id": 1,
        "by_level": {"warning": 1},
    }
    with pytest.raises(ValueError):
        ring.emit("verbose", "tests", "unsupported")


def test_logs_route_reads_the_shared_bounded_ring():
    SERVER_LOGS.clear()
    SERVER_LOGS.emit("info", "server", "ready")
    writes = []
    request = SimpleNamespace(write_json=lambda payload: writes.append(payload))

    http_routes.get_server_logs(request, SimpleNamespace(query=""), None)

    assert writes[0]["capacity"] == SERVER_LOGS.capacity
    assert writes[0]["epoch"] == SERVER_LOGS.payload()["epoch"]
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

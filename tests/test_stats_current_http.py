# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the current-only YO!stats HTTP forwarding boundary."""

from http import HTTPStatus
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from yolomux_lib import http_routes
from yolomux_lib import server
from yolomux_lib.stats_current import http, protocol, resolution as stats_resolution


class FakeClient:
    def __init__(self, metadata: dict[str, object], body: bytes = b""):
        self.metadata = metadata
        self.body = body
        self.requests: list[protocol.SnapshotRequest] = []
        self.delta_requests: list[protocol.DeltaRequest] = []
        self.started = True
        self.retry_calls = 0

    def ensure_started(self):
        return self.started

    def status(self):
        return self.metadata

    def retry(self):
        self.retry_calls += 1
        return self.started

    def snapshot(self, request):
        self.requests.append(request)
        return self.metadata, self.body

    def delta(self, request):
        self.delta_requests.append(request)
        return self.metadata, self.body


def forwarder(metadata: dict[str, object], body: bytes = b"") -> tuple[http.StatsHttpForwarder, FakeClient]:
    client = FakeClient(metadata, body)
    return http.StatsHttpForwarder(client, client_binding_secret=b"s" * 32), client


def test_success_forwards_the_preencoded_body_and_binds_private_client_identity():
    adapter, client = forwarder(
        {"ok": True, "content_type": "application/json", "cache_generation": 9},
        b'{"exact":"statsd bytes"}',
    )

    result = adapter.snapshot(
        "range_seconds=300&resolution=AUTO&client_id=browser-secret&since_generation=8",
        authenticated_username="alice",
    )

    assert result == http.SnapshotHttpResult(HTTPStatus.OK, b'{"exact":"statsd bytes"}')
    assert client.requests == [
        protocol.SnapshotRequest(
            range_seconds=300,
            resolution="AUTO",
            resolution_seconds=1,
            client_id=http.bound_client_id(b"s" * 32, "alice", "browser-secret"),
            since_generation=8,
        )
    ]
    assert "alice" not in client.requests[0].client_id
    assert "browser-secret" not in client.requests[0].client_id
    assert http.bound_client_id(b"s" * 32, "alice", "browser-secret") != http.bound_client_id(
        b"s" * 32, "bob", "browser-secret"
    )
    assert http.bound_client_id(b"s" * 32, "alice", " browser-secret ") == http.bound_client_id(
        b"s" * 32, "alice", "browser-secret"
    )


def test_capabilities_are_serialized_only_by_the_canonical_server_policy():
    adapter, _client = forwarder({"ok": True})

    assert adapter.capabilities() == stats_resolution.wire_capabilities()
    assert adapter.capabilities()["resolution_choices"] == [1, 10, 60, 300]
    assert {
        row["range_seconds"]: row["auto_resolution_seconds"]
        for row in adapter.capabilities()["ranges"]
    } == {
        value: stats_resolution.auto_resolution(value)
        for value in stats_resolution.RANGE_SECONDS
    }


@pytest.mark.parametrize(
    "query",
    (
        "range_seconds=900&resolution=1&client_id=browser-a",
        "range_seconds=300&resolution=1&client_id=browser-a&history=1",
        "range_seconds=300&resolution=1&client_id=browser-a&client_id=browser-b",
        "range_seconds=300&resolution=1&client_id=",
        "range_seconds=300&resolution=1&client_id=%ZZ",
        "range_seconds=300&resolution=1&client_id=browser-a&unknown=",
    ),
)
def test_invalid_retired_blank_malformed_and_duplicate_queries_are_exact_unsupported(query):
    adapter, client = forwarder({"ok": True}, b"must not be returned")

    result = adapter.snapshot(query, authenticated_username="alice")

    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.body == b""
    assert result.payload is not None
    assert result.payload["status"] == "unsupported"
    assert result.payload["protocol_version"] == protocol.WIRE_PROTOCOL_VERSION
    assert result.payload["valid_resolutions"]
    assert client.requests == []


def test_query_size_and_client_binding_inputs_are_bounded():
    with pytest.raises(protocol.UnsupportedRequest, match="too large"):
        http.parse_http_snapshot_query(
            "range_seconds=300&resolution=1&client_id=" + "a" * http.MAX_QUERY_BYTES
        )
    with pytest.raises(ValueError, match="at least 16 bytes"):
        http.bound_client_id(b"short", "alice", "browser-a")
    with pytest.raises(ValueError, match="username"):
        http.bound_client_id(b"s" * 32, "", "browser-a")


def test_delta_forwards_exact_preencoded_bytes_and_binds_the_same_client_identity():
    adapter, client = forwarder(
        {
            "ok": True,
            "content_type": "application/json",
            "base_cache_generation": 7,
            "cache_generation": 8,
            "revision": 4,
        },
        b'{"exact":"delta"}',
    )

    result = adapter.delta(
        "range_seconds=300&resolution_seconds=1&client_id=browser-a&after_cache_generation=7&after_revision=3",
        authenticated_username="alice",
    )

    assert result == http.SnapshotHttpResult(HTTPStatus.OK, b'{"exact":"delta"}')
    assert client.delta_requests == [protocol.DeltaRequest(
        300,
        1,
        http.bound_client_id(b"s" * 32, "alice", "browser-a"),
        7,
        3,
    )]


@pytest.mark.parametrize(
    ("metadata", "expected"),
    (
        ({"ok": True, "not_modified": True, "cache_generation": 8}, HTTPStatus.NOT_MODIFIED),
        ({"status": "repair_required", "cache_generation": 9}, HTTPStatus.CONFLICT),
        ({"status": "pending", "retry_after_seconds": 1}, HTTPStatus.SERVICE_UNAVAILABLE),
    ),
)
def test_delta_maps_not_modified_repair_and_pending_without_fallback(metadata, expected):
    adapter, _client = forwarder(metadata)
    result = adapter.delta(
        "range_seconds=300&resolution_seconds=1&client_id=browser-a&after_cache_generation=7&after_revision=0",
        authenticated_username="alice",
    )
    assert result.status == expected
    assert result.payload is metadata


@pytest.mark.parametrize(
    ("metadata", "body", "expected_status", "expected_state"),
    (
        (
            {
                "status": "pending",
                "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                "range_seconds": 300,
                "requested_resolution": "AUTO",
                "resolution_seconds": 1,
                "retry_after_seconds": 1,
                "reason": "materialization is not ready",
            },
            b"",
            HTTPStatus.SERVICE_UNAVAILABLE,
            "pending",
        ),
        (
            protocol.unsupported_response("unsupported exact key", 900),
            b"",
            HTTPStatus.BAD_REQUEST,
            "unsupported",
        ),
        (
            protocol.upgrade_required_response(24, 6, "2"),
            b"",
            HTTPStatus.UPGRADE_REQUIRED,
            "upgrade_required",
        ),
    ),
)
def test_pending_unsupported_and_upgrade_states_are_forwarded_exactly(
    metadata, body, expected_status, expected_state
):
    adapter, _client = forwarder(metadata, body)

    result = adapter.snapshot(
        "range_seconds=300&resolution=AUTO&client_id=browser-a",
        authenticated_username="alice",
    )

    assert result.status == expected_status
    assert result.body == b""
    assert result.payload is metadata
    assert result.payload["status"] == expected_state


def test_not_modified_has_no_body_and_transport_failures_are_sanitized():
    unchanged, _client = forwarder(
        {"ok": True, "not_modified": True, "cache_generation": 9}
    )
    unavailable, _client = forwarder(
        {"ok": False, "_transport_error": "rpc", "error": "/private/socket/path"}
    )
    query = "range_seconds=300&resolution=1&client_id=browser-a&since_generation=9"

    assert unchanged.snapshot(query, authenticated_username="alice") == http.SnapshotHttpResult(
        HTTPStatus.NOT_MODIFIED
    )
    failure = unavailable.snapshot(query, authenticated_username="alice")
    assert failure.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert failure.payload == {
        "status": "unavailable",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "reason": "statsd unavailable",
    }
    assert "/private/socket/path" not in str(failure.payload)


def test_startup_failure_reason_and_terminal_state_reach_http_503():
    adapter, client = forwarder({
        "ok": False,
        "status": "unavailable",
        "reason": "statsd exited (2): MigrationError: unsupported retired database",
        "terminal": True,
    })
    client.started = False

    result = adapter.snapshot(
        "range_seconds=300&resolution=1&client_id=browser-a",
        authenticated_username="alice",
    )

    assert result.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result.payload == {
        "status": "unavailable",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "reason": "statsd exited (2): MigrationError: unsupported retired database",
        "terminal": True,
    }
    assert client.requests == []


def test_explicit_retry_clears_terminal_startup_failure_and_reports_result():
    adapter, client = forwarder({
        "ok": False,
        "status": "unavailable",
        "reason": "statsd exited (2): MigrationError",
        "terminal": True,
    })
    client.started = False

    assert adapter.retry() == {
        "status": "unavailable",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "reason": "statsd exited (2): MigrationError",
        "terminal": True,
    }
    assert client.retry_calls == 1

    client.started = True
    assert adapter.retry() == {"ok": True, "status": "ready"}
    assert client.retry_calls == 2


@pytest.mark.parametrize(
    "result",
    (
        http.SnapshotHttpResult(HTTPStatus.OK, b'{"exact":true}'),
        http.SnapshotHttpResult(
            HTTPStatus.SERVICE_UNAVAILABLE,
            payload={"status": "pending", "retry_after_seconds": 1},
        ),
        http.SnapshotHttpResult(
            HTTPStatus.UPGRADE_REQUIRED,
            payload={"status": "upgrade_required", "required_protocol_version": 24},
        ),
    ),
)
def test_authenticated_route_passes_only_query_and_username_to_the_forwarder(result):
    calls = []
    writes = []
    adapter = SimpleNamespace(
        snapshot=lambda query, *, authenticated_username: calls.append(
            (query, authenticated_username)
        )
        or result
    )
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(stats_current_http=adapter)),
        auth_identity=lambda: SimpleNamespace(username="alice"),
        write_json=lambda payload, status=HTTPStatus.OK: writes.append(
            ("json", status, payload)
        ),
        write_json_bytes=lambda body, status=HTTPStatus.OK: writes.append(
            ("bytes", status, body)
        ),
    )

    http_routes.get_stats_snapshot(
        request,
        SimpleNamespace(query="range_seconds=300&resolution=1&client_id=browser-a"),
        None,
    )

    assert calls == [
        ("range_seconds=300&resolution=1&client_id=browser-a", "alice")
    ]
    if result.payload is None:
        assert writes == [("bytes", result.status, result.body)]
    else:
        assert writes == [("json", result.status, result.payload)]


def test_snapshot_route_is_current_authenticated_and_not_share_visible():
    route = http_routes.route_for_request("GET", "/api/stats-snapshot")

    assert route is not None
    assert route.handler is http_routes.get_stats_snapshot
    assert route.role == "readonly"
    assert route.share_access == http_routes.SHARE_ACCESS_NONE


def test_delta_route_is_current_authenticated_and_not_share_visible():
    route = http_routes.route_for_request("GET", "/api/stats-delta")

    assert route is not None
    assert route.handler is http_routes.get_stats_delta
    assert route.role == "readonly"
    assert route.share_access == http_routes.SHARE_ACCESS_NONE


def test_stream_route_is_authenticated_and_sse_forwards_validated_delta_bytes_exactly():
    route = http_routes.route_for_request("GET", "/api/stats-stream")
    assert route is not None
    assert route.handler is http_routes.get_stats_stream
    assert route.role == "readonly"
    assert route.share_access == http_routes.SHARE_ACCESS_NONE

    sink = io.BytesIO()
    request = SimpleNamespace(wfile=sink)
    server.Handler.write_sse_bytes(request, "delta", b'{"cache_generation":8}')
    assert sink.getvalue() == (
        b"event: delta\n"
        b'data: {"cache_generation":8}\n'
        b"\n"
    )


def test_stream_delta_checks_keep_absolute_cadence_when_rpc_work_takes_time(monkeypatch):
    current = [0.0]
    waits = []
    events = []

    class Waiter:
        def wait(self, delay):
            waits.append(delay)
            current[0] += delay
            return False

    class Forwarder:
        def __init__(self):
            self.calls = 0

        def delta_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            self.calls += 1
            if self.calls == 1:
                return http.DeltaStreamResult(
                    HTTPStatus.NOT_MODIFIED,
                    {"ok": True, "not_modified": True, "cache_generation": 10},
                )
            current[0] += 0.4
            if self.calls == 2:
                return http.DeltaStreamResult(
                    HTTPStatus.NOT_MODIFIED,
                    {"ok": True, "not_modified": True, "cache_generation": 10},
                )
            return http.DeltaStreamResult(
                HTTPStatus.CONFLICT,
                {"status": "repair_required", "cache_generation": 12},
            )

    monkeypatch.setattr(server.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(server.threading, "Event", Waiter)
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(stats_current_http=Forwarder())),
        send_response=lambda _status: None,
        send_header=lambda _name, _value: None,
        send_auth_cookie_if_needed=lambda: None,
        end_headers=lambda: None,
        write_json=lambda _payload, status: None,
        write_sse_bytes=lambda name, body: events.append((name, body)),
        write_sse_json=lambda name, payload: events.append((name, payload)),
    )

    server.Handler.stream_stats_current_delta(
        request,
        "range_seconds=300&resolution_seconds=1&client_id=browser-a&"
        "after_cache_generation=10&after_revision=0",
        authenticated_username="alice",
    )

    assert waits == pytest.approx([1.0, 0.6])
    assert events[-1] == (
        "repair", {"status": "repair_required", "cache_generation": 12},
    )


def test_capabilities_route_is_authenticated_and_uses_the_same_policy_owner():
    writes = []
    adapter = SimpleNamespace(capabilities=lambda: {"resolution_choices": [1, 10, 60, 300]})
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(stats_current_http=adapter)),
        write_json=lambda payload, status=HTTPStatus.OK: writes.append((status, payload)),
    )

    http_routes.get_stats_capabilities(request, None, None)

    assert writes == [(HTTPStatus.OK, {"resolution_choices": [1, 10, 60, 300]})]
    route = http_routes.route_for_request("GET", "/api/stats-capabilities")
    assert route is not None
    assert route.handler is http_routes.get_stats_capabilities
    assert route.role == "readonly"
    assert route.share_access == http_routes.SHARE_ACCESS_NONE


def test_retry_route_is_authenticated_and_returns_the_forwarder_result():
    writes = []
    adapter = SimpleNamespace(retry=lambda: {"ok": True, "status": "ready"})
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(stats_current_http=adapter)),
        write_json=lambda payload, status=HTTPStatus.OK: writes.append((status, payload)),
    )

    http_routes.post_stats_retry(request, None, None)

    assert writes == [(200, {"ok": True, "status": "ready"})]
    route = http_routes.route_for_request("POST", "/api/stats-retry")
    assert route is not None
    assert route.handler is http_routes.post_stats_retry
    assert route.role == "readonly"


def test_http_access_log_redacts_the_raw_stats_client_identity():
    request_line = '"GET /api/stats-snapshot?range_seconds=300&client_id=private-browser HTTP/1.1" 200 -'

    redacted = server.TOKEN_LOG_RE.sub(r"\1[redacted]", request_line)

    assert "private-browser" not in redacted
    assert "client_id=[redacted]" in redacted


def test_forwarder_source_has_no_storage_payload_transform_or_old_runtime_dependency():
    source = Path(http.__file__).read_text(encoding="utf-8")

    for retired in (
        "import sqlite3",
        "from sqlite3",
        "json.dumps",
        "json.loads",
        "statsd import",
        "StatsHistoryReader",
        "exact_resolution",
        "history_start",
        "max_points",
    ):
        assert retired not in source

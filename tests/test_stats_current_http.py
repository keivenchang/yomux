# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the current-only YO!stats HTTP forwarding boundary."""

from http import HTTPStatus
import hashlib
import io
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from yolomux_lib import http_routes
from yolomux_lib import server
from yolomux_lib.stats_current import http, protocol, resolution as stats_resolution
from tests.terminal_state_guard import assert_terminal_transition


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


def test_hour_chunk_cursor_reaches_the_same_bound_snapshot_client():
    adapter, client = forwarder(
        {"ok": True, "content_type": "application/json", "cache_generation": 41},
        b'{"chunk_index":7}',
    )

    result = adapter.snapshot(
        "range_seconds=28800&resolution=AUTO&client_id=browser-secret&since_generation=0&chunk_index=7&chunk_generation=41",
        authenticated_username="alice",
    )

    assert result.status == HTTPStatus.OK
    assert client.requests[0].chunk_index == 7
    assert client.requests[0].chunk_generation == 41
    assert client.requests[0].since_generation == 0


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
        (
            {"ok": True, "status": "queued", "ticket": "stats-ticket-12", "key": "stats:300:1"},
            HTTPStatus.ACCEPTED,
        ),
        ({"status": "pending", "retry_after_seconds": 1}, HTTPStatus.ACCEPTED),
        (
            {"ok": False, "status": "unavailable", "reason": "stats storage is unavailable"},
            HTTPStatus.FAILED_DEPENDENCY,
        ),
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


def test_delta_queued_acknowledgement_can_reach_terminal_body():
    queued = {
        "ok": True,
        "status": "queued",
        "ticket": "stats-ticket-12",
        "key": "stats:300:1",
    }
    terminal_metadata = {
        "ok": True,
        "content_type": "application/json",
        "base_cache_generation": 7,
        "cache_generation": 8,
        "revision": 4,
    }
    adapter, client = forwarder(queued)
    query = (
        "range_seconds=300&resolution_seconds=1&client_id=browser-a"
        "&after_cache_generation=7&after_revision=0"
    )

    pending = adapter.delta(query, authenticated_username="alice")
    client.metadata = terminal_metadata
    client.body = b'{"exact":"delta"}'
    terminal = adapter.delta(query, authenticated_username="alice")

    assert_terminal_transition(
        contract_id="stats-delta-queued-producer",
        pending_observed=(pending.status == HTTPStatus.ACCEPTED and pending.payload is queued),
        terminal_observed=(
            terminal.status == HTTPStatus.OK
            and terminal.body == b'{"exact":"delta"}'
        ),
        evidence={"pending": pending, "terminal": terminal},
    )


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
            HTTPStatus.ACCEPTED,
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
    assert failure.status == HTTPStatus.FAILED_DEPENDENCY
    assert failure.payload == {
        "status": "unavailable",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "reason": "statsd unavailable",
    }
    assert "/private/socket/path" not in str(failure.payload)


@pytest.mark.parametrize("method", ("snapshot", "delta"))
def test_transient_statsd_transport_failure_is_retryable_pending(method):
    adapter, _client = forwarder({
        "ok": False,
        "_transport_error": "rpc",
        "error": "response exceeded deadline",
    })
    query = (
        "range_seconds=300&resolution=1&client_id=browser-a&since_generation=9"
        if method == "snapshot"
        else "range_seconds=300&resolution_seconds=1&client_id=browser-a&after_cache_generation=9&after_revision=0"
    )

    result = getattr(adapter, method)(query, authenticated_username="alice")

    assert result.status == HTTPStatus.ACCEPTED
    expected = {
        "status": "pending",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "retry_after_seconds": 1,
        "reason": "statsd is refreshing",
    }
    if method == "snapshot":
        expected.update({
            "range_seconds": 300,
            "requested_resolution": 1,
            "resolution_seconds": 1,
        })
    assert result.payload == expected


def test_transient_statsd_startup_refreshing_is_retryable_pending():
    adapter, client = forwarder({
        "ok": False,
        "status": HTTPStatus.SERVICE_UNAVAILABLE,
        "error": "refreshing",
    })
    client.started = False

    result = adapter.snapshot(
        "range_seconds=86400&resolution=AUTO&client_id=browser-a&since_generation=9",
        authenticated_username="alice",
    )

    assert result.status == HTTPStatus.ACCEPTED
    assert result.payload == {
        "status": "pending",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "range_seconds": 86400,
        "requested_resolution": "AUTO",
        "resolution_seconds": 300,
        "retry_after_seconds": 1,
        "reason": "statsd is refreshing",
    }
    assert client.requests == []


def test_startup_failure_reason_and_terminal_state_reach_typed_dependency_error():
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

    assert result.status == HTTPStatus.FAILED_DEPENDENCY
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


def test_read_fence_retry_recovers_the_next_snapshot_without_a_writer_page_reload():
    class RecoveringClient(FakeClient):
        def __init__(self):
            super().__init__({
                "ok": False,
                "status": "upgrade_required",
                "required_protocol_version": 24,
                "required_schema_generation": 6,
                "required_build": "3",
            })
            self.started = False

        def retry(self):
            self.retry_calls += 1
            self.started = True
            self.metadata = {"ok": True, "content_type": "application/json"}
            self.body = b'{"recovered":true}'
            return True

    client = RecoveringClient()
    adapter = http.StatsHttpForwarder(client, client_binding_secret=b"s" * 32)
    query = "range_seconds=300&resolution=1&client_id=browser-a"

    initial = adapter.snapshot(query, authenticated_username="alice")
    assert initial.status == HTTPStatus.UPGRADE_REQUIRED
    assert initial.payload["required_schema_generation"] == 6
    assert adapter.retry() == {"ok": True, "status": "ready"}
    assert client.retry_calls == 1
    assert adapter.snapshot(query, authenticated_username="alice") == http.SnapshotHttpResult(
        HTTPStatus.OK, b'{"recovered":true}',
    )


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
        write_product_bytes=lambda body, product: writes.append(
            ("product", body, product)
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
        assert result.status == HTTPStatus.OK
        assert writes == [("product", result.body, {
            "format": "json",
            "content_type": "application/json; charset=utf-8",
            "length": len(result.body),
            "sha256": hashlib.sha256(result.body).hexdigest(),
            "disposition": "inline",
            "filename": "",
        })]
    else:
        assert writes == [("json", result.status, result.payload)]


def test_snapshot_route_is_current_and_authenticated():
    route = http_routes.route_for_request("GET", "/api/stats-snapshot")

    assert route is not None
    assert route.handler is http_routes.get_stats_snapshot
    assert route.role == "readonly"


def test_delta_route_is_current_and_authenticated():
    route = http_routes.route_for_request("GET", "/api/stats-delta")

    assert route is not None
    assert route.handler is http_routes.get_stats_delta
    assert route.role == "readonly"


def test_stream_route_is_authenticated_and_sse_forwards_validated_delta_bytes_exactly():
    route = http_routes.route_for_request("GET", "/api/stats-stream")
    assert route is not None
    assert route.handler is http_routes.get_stats_stream
    assert route.role == "readonly"

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

        def snapshot_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            return http.StatsStreamResult(
                HTTPStatus.OK,
                {"ok": True, "cache_generation": 10},
                b'{"snapshot":true}',
            )

        def delta_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            self.calls += 1
            current[0] += 0.4
            if self.calls == 1:
                return http.DeltaStreamResult(
                    HTTPStatus.NOT_MODIFIED,
                    {"ok": True, "not_modified": True, "cache_generation": 10},
                )
            return http.DeltaStreamResult(
                HTTPStatus.CONFLICT,
                {"status": "repair_required", "cache_generation": 12},
            )

    monkeypatch.setattr(server.time, "monotonic", lambda: current[0])
    request = SimpleNamespace(
        server=SimpleNamespace(
            app=SimpleNamespace(stats_current_http=Forwarder()),
            persistent_request_stop=Waiter(),
        ),
        send_response=lambda _status: None,
        send_header=lambda _name, _value: None,
        send_auth_cookie_if_needed=lambda: None,
        end_headers=lambda: None,
        write_json=lambda _payload, status: None,
        write_sse_bytes=lambda name, body: events.append((name, body)),
        write_sse_json=lambda name, payload: events.append((name, payload)),
    )

    server.Handler.stream_stats_current(
        request,
        "range_seconds=300&resolution=1&client_id=browser-a&since_generation=0",
        authenticated_username="alice",
    )

    assert waits == pytest.approx([1.0, 0.6])
    assert events[-1] == (
        "repair", {"status": "repair_required", "cache_generation": 12},
    )


def test_established_stream_keeps_cursor_open_across_transient_materialization_lag(monkeypatch):
    current = [0.0]
    events = []

    class Waiter:
        def wait(self, delay):
            current[0] += delay
            return False

    class Forwarder:
        def __init__(self):
            self.results = iter((
                http.DeltaStreamResult(
                    HTTPStatus.NOT_MODIFIED,
                    {"ok": True, "not_modified": True, "cache_generation": 10},
                ),
                http.DeltaStreamResult(
                    HTTPStatus.ACCEPTED,
                    {"status": "pending", "reason": "statsd is refreshing"},
                ),
                http.DeltaStreamResult(
                    HTTPStatus.NOT_MODIFIED,
                    {"ok": True, "not_modified": True, "cache_generation": 10},
                ),
                http.DeltaStreamResult(
                    HTTPStatus.CONFLICT,
                    {"status": "repair_required", "cache_generation": 12},
                ),
            ))

        def snapshot_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            return http.StatsStreamResult(
                HTTPStatus.OK,
                {"ok": True, "cache_generation": 10},
                b'{"snapshot":true}',
            )

        def delta_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            return next(self.results)

    monkeypatch.setattr(server.time, "monotonic", lambda: current[0])
    request = SimpleNamespace(
        server=SimpleNamespace(
            app=SimpleNamespace(stats_current_http=Forwarder()),
            persistent_request_stop=Waiter(),
        ),
        send_response=lambda _status: None,
        send_header=lambda _name, _value: None,
        send_auth_cookie_if_needed=lambda: None,
        end_headers=lambda: None,
        write_json=lambda _payload, status: None,
        write_sse_bytes=lambda name, body: events.append((name, body)),
        write_sse_json=lambda name, payload: events.append((name, payload)),
    )

    server.Handler.stream_stats_current(
        request,
        "range_seconds=300&resolution=1&client_id=browser-a&since_generation=0",
        authenticated_username="alice",
    )

    assert [name for name, _payload in events] == [
        "ack", "snapshot", "ready", "ready", "ready", "repair",
    ]
    assert not [event for event in events if event[0] == "unavailable"]


def test_stream_initial_snapshot_failure_is_a_typed_sse_terminal_not_bare_http():
    events = []
    writes = []

    class Forwarder:
        def snapshot_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            return http.StatsStreamResult(
                HTTPStatus.FAILED_DEPENDENCY,
                {
                    "status": "unavailable",
                    "reason": "statsd unavailable",
                },
            )

    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(stats_current_http=Forwarder())),
        send_response=lambda status: writes.append(("status", status)),
        send_header=lambda name, value: writes.append(("header", name, value)),
        send_auth_cookie_if_needed=lambda: writes.append(("cookie",)),
        end_headers=lambda: writes.append(("end",)),
        write_json=lambda payload, status: writes.append(("json", status, payload)),
        write_sse_bytes=lambda name, body: events.append((name, body)),
        write_sse_json=lambda name, payload: events.append((name, payload)),
    )

    server.Handler.stream_stats_current(
        request,
        "range_seconds=86400&resolution=300&client_id=browser-a&since_generation=0",
        authenticated_username="alice",
    )

    assert writes[0] == ("status", HTTPStatus.OK)
    assert ("header", "Connection", "close") in writes
    assert not [entry for entry in writes if entry[0] == "json"]
    assert events == [
        ("unavailable", {
            "status": "unavailable",
            "reason": "statsd unavailable",
        }),
    ]


def test_stream_initial_not_modified_opens_one_sse_and_emits_ack_then_ready():
    events = []
    writes = []

    class Waiter:
        def wait(self, _delay):
            raise BrokenPipeError

    class Forwarder:
        def __init__(self):
            self.stream_calls = 0
            self.snapshot_calls = 0

        def snapshot_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            self.snapshot_calls += 1
            return http.StatsStreamResult(
                HTTPStatus.NOT_MODIFIED,
                {"ok": True, "not_modified": True, "cache_generation": 10},
            )

        def delta_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            self.stream_calls += 1
            return http.DeltaStreamResult(
                HTTPStatus.NOT_MODIFIED,
                {"ok": True, "not_modified": True, "cache_generation": 10},
            )

    forwarder = Forwarder()
    request = SimpleNamespace(
        server=SimpleNamespace(
            app=SimpleNamespace(stats_current_http=forwarder),
            persistent_request_stop=Waiter(),
        ),
        send_response=lambda status: writes.append(("status", status)),
        send_header=lambda name, value: writes.append(("header", name, value)),
        send_auth_cookie_if_needed=lambda: writes.append(("cookie",)),
        end_headers=lambda: writes.append(("end",)),
        write_json=lambda payload, status: writes.append(("json", status, payload)),
        write_sse_bytes=lambda name, body: events.append((name, body)),
        write_sse_json=lambda name, payload: events.append((name, payload)),
    )

    server.Handler.stream_stats_current(
        request,
        "range_seconds=300&resolution=1&client_id=browser-a&since_generation=10",
        authenticated_username="alice",
    )

    assert [item for item in writes if item[0] == "status"] == [
        ("status", HTTPStatus.OK),
    ]
    assert not [item for item in writes if item[0] == "json"]
    assert events == [
        ("ack", {
            "cache_generation": 10,
            "chunk_count": 1,
            "not_modified": True,
            "range_seconds": 300,
                "requested_resolution": 1,
            "resolution_seconds": 1,
        }),
        ("ready", {"cache_generation": 10, "revision": 0}),
    ]
    assert forwarder.stream_calls == 0
    assert forwarder.snapshot_calls == 1


def test_server_shutdown_wakes_sixty_second_stats_stream_wait(monkeypatch):
    wait_started = threading.Event()
    stop_event = threading.Event()
    waits = []

    class ShutdownEvent:
        def set(self):
            stop_event.set()

        def wait(self, timeout):
            waits.append(timeout)
            wait_started.set()
            return stop_event.wait(timeout)

    class Forwarder:
        def __init__(self):
            self.calls = 0

        def snapshot_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            return http.StatsStreamResult(
                HTTPStatus.OK,
                {"ok": True, "cache_generation": 10},
                b'{"snapshot":true}',
            )

        def delta_stream(self, _query, *, authenticated_username):
            assert authenticated_username == "alice"
            self.calls += 1
            return http.DeltaStreamResult(
                HTTPStatus.NOT_MODIFIED,
                {"ok": True, "not_modified": True, "cache_generation": 10},
            )

    forwarder = Forwarder()
    live_server = object.__new__(server.TmuxWebtermHTTPServer)
    live_server.app = SimpleNamespace(stats_current_http=forwarder)
    live_server.persistent_request_stop = ShutdownEvent()
    parent_shutdown_calls = []
    monkeypatch.setattr(
        server.ThreadingHTTPServer,
        "shutdown",
        lambda _server: parent_shutdown_calls.append(stop_event.is_set()),
    )
    request = SimpleNamespace(
        server=live_server,
        send_response=lambda _status: None,
        send_header=lambda _name, _value: None,
        send_auth_cookie_if_needed=lambda: None,
        end_headers=lambda: None,
        write_json=lambda _payload, status: None,
        write_sse_bytes=lambda _name, _body: None,
        write_sse_json=lambda _name, _payload: None,
    )
    stream_thread = threading.Thread(
        target=server.Handler.stream_stats_current,
        args=(
            request,
            "range_seconds=86400&resolution=300&client_id=browser-a&since_generation=0",
        ),
        kwargs={"authenticated_username": "alice"},
        daemon=True,
    )
    stream_thread.start()
    assert wait_started.wait(timeout=1)

    live_server.shutdown()
    stream_thread.join(timeout=1)

    assert not stream_thread.is_alive()
    assert waits == pytest.approx([60.0], abs=0.1)
    assert forwarder.calls == 0
    assert parent_shutdown_calls == [True]


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

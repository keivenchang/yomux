# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current authenticated browser-observation write boundary."""

import json
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from yomux_lib import app as app_module
from yomux_lib import http_routes
from yomux_lib.stats_current import browser_family
from yomux_lib.stats_current import client as stats_client
from yomux_lib.stats_current import families
from yomux_lib.stats_current import observations, service, storage


def payload(**changes):
    value = {
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
        "client_id": "browser-private",
        "observations": [{
            "event_id": "request-1",
            "family": "browser",
            "source_id": "browser-private",
            "observed_at": 100.5,
            "epoch_id": "page-1",
            "payload": {"kind": "api", "latency_ms": 12, "bytes": 345},
        }],
    }
    value.update(changes)
    return value


def test_valid_batch_is_privacy_bound_stable_and_keeps_original_facts():
    first = observations.parse_browser_observations(
        payload(), client_binding_secret=b"s" * 32, authenticated_username="alice",
    )
    retry = observations.parse_browser_observations(
        payload(), client_binding_secret=b"s" * 32, authenticated_username="alice",
    )
    other_user = observations.parse_browser_observations(
        payload(), client_binding_secret=b"s" * 32, authenticated_username="bob",
    )

    assert first == retry
    assert len(first) == 1
    assert first[0].family == "browser"
    assert first[0].observed_at == 100.5
    assert dict(first[0].payload) == {"kind": "api", "latency_ms": 12, "bytes": 345}
    assert "browser-private" not in first[0].event_id + first[0].source_id + first[0].epoch_id
    assert first[0].source_id != other_user[0].source_id


def test_browser_failure_provenance_is_bounded_and_optional():
    failure = payload(observations=[{
        "event_id": "failure-1", "family": "browser", "source_id": "browser-private",
        "observed_at": 100.5, "epoch_id": "page-1",
        "payload": {
            "kind": "error", "signature": "jsf-provenance", "message": "confirmed issue",
            "source": "/static/yomux.js", "provenance": "confirmed_real",
        },
    }])
    parsed = observations.parse_browser_observations(
        failure, client_binding_secret=b"s" * 32, authenticated_username="alice",
    )

    assert dict(parsed[0].payload)["provenance"] == "confirmed_real"
    failure["observations"][0]["payload"]["provenance"] = "inferred_from_message"
    with pytest.raises(observations.BrowserObservationError, match="provenance"):
        observations.parse_browser_observations(
            failure, client_binding_secret=b"s" * 32, authenticated_username="alice",
        )


def test_browser_failure_correlation_fields_are_exact_bounded_facts():
    failure_payload = {
        "kind": "warning", "signature": "jsf-correlation", "message": "stream stalled",
        "source": "/api/stats-stream", "request_id": "r-stats-1", "route": "/api/stats-stream",
        "event_type": "stats-generation", "wall_time": "2026-08-05 05:00:00 PDT",
        "delivery_outcome": "stalled", "status": 503,
    }
    failure = payload(observations=[{
        "event_id": "failure-correlation-1", "family": "browser", "source_id": "browser-private",
        "observed_at": 100.5, "epoch_id": "page-1", "payload": failure_payload,
    }])
    parsed = observations.parse_browser_observations(
        failure, client_binding_secret=b"s" * 32, authenticated_username="alice",
    )
    assert dict(parsed[0].payload) == failure_payload

    invalid_values = (
        ("request_id", "not-a-request", "request_id"),
        ("route", "/api/stats-stream?token=private", "endpoint"),
        ("event_type", "stats generation", "event_type"),
        ("wall_time", "2026-08-05T05:00:00Z", "wall_time"),
        ("delivery_outcome", "maybe", "delivery_outcome"),
    )
    for field, invalid, message in invalid_values:
        rejected = json.loads(json.dumps(failure))
        rejected["observations"][0]["payload"][field] = invalid
        with pytest.raises(observations.BrowserObservationError, match=message):
            observations.parse_browser_observations(
                rejected, client_binding_secret=b"s" * 32, authenticated_username="alice",
            )


def test_statsd_browser_status_counts_accepted_authenticated_reports(tmp_path):
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / storage.DATABASE_FILENAME, clock=lambda: 140.0,
    )
    current_service.writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    try:
        accepted = current_service._browser_upload(
            {"authenticated_username": "alice"}, json.dumps(payload()).encode("utf-8"),
        )
        diagnostics, binary = current_service.handle_with_binary({
            "action": "browser_profiles",
            "protocol_version": storage.MIN_WRITER_PROTOCOL,
            "schema_generation": storage.SCHEMA_VERSION,
        })
    finally:
        current_service.writer.close()

    assert accepted["accepted"] == 1
    assert binary == b""
    status = diagnostics["observation_status"]
    assert status["accepted_reports"] == 1
    assert status["accepted_observations"] == 1
    assert status["last_accepted_at"] == 140.0
    assert status["last_accepted_age_seconds"] == 0.0
    assert status["receipt_scope"] == "statsd_process"
    assert status["receipt_scope_started_at"] == current_service.started_at


def test_statsd_browser_upload_returns_request_order_receipts_for_accept_and_duplicate(tmp_path):
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
    )
    current_service.writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    upload = payload(observations=[
        {**payload()["observations"][0], "event_id": "page-1:7"},
        {**payload()["observations"][0], "event_id": "page-1:8", "observed_at": 101.5},
    ])
    try:
        accepted = current_service._browser_upload(
            {"authenticated_username": "alice"}, json.dumps(upload).encode("utf-8"),
        )
        duplicate = current_service._browser_upload(
            {"authenticated_username": "alice"}, json.dumps(upload).encode("utf-8"),
        )
    finally:
        current_service.writer.close()

    assert accepted["observation_receipts"] == [
        {"event_id": "page-1:7", "disposition": "accepted"},
        {"event_id": "page-1:8", "disposition": "accepted"},
    ]
    assert duplicate["observation_receipts"] == [
        {"event_id": "page-1:7", "disposition": "duplicate"},
        {"event_id": "page-1:8", "disposition": "duplicate"},
    ]


def test_statsd_browser_upload_normalizes_whitespace_event_id_alias_once(tmp_path):
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
    )
    current_service.writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    canonical = payload(observations=[{**payload()["observations"][0], "event_id": "page-1:7"}])
    whitespace_alias = payload(observations=[{
        **payload()["observations"][0],
        "event_id": " page-1:7 ",
    }])
    try:
        accepted = current_service._browser_upload(
            {"authenticated_username": "alice"}, json.dumps(canonical).encode("utf-8"),
        )
        duplicate = current_service._browser_upload(
            {"authenticated_username": "alice"}, json.dumps(whitespace_alias).encode("utf-8"),
        )
    finally:
        current_service.writer.close()

    assert accepted["observation_receipts"] == [
        {"event_id": "page-1:7", "disposition": "accepted"},
    ]
    assert duplicate["observation_receipts"] == [
        {"event_id": "page-1:7", "disposition": "duplicate"},
    ]


def test_statsd_browser_upload_rejects_reused_event_identity_without_receipts(tmp_path):
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
    )
    current_service.writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    first = payload()
    conflict = payload()
    conflict["observations"][0]["observed_at"] = 101.5
    try:
        accepted, _binary = current_service.handle_with_binary(
            {
                "action": "browser_upload",
                "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "schema_generation": storage.SCHEMA_VERSION,
                "authenticated_username": "alice",
            },
            json.dumps(first).encode("utf-8"),
        )
        rejected, _binary = current_service.handle_with_binary(
            {
                "action": "browser_upload",
                "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "schema_generation": storage.SCHEMA_VERSION,
                "authenticated_username": "alice",
            },
            json.dumps(conflict).encode("utf-8"),
        )
    finally:
        current_service.writer.close()

    assert accepted["observation_receipts"] == [
        {"event_id": "request-1", "disposition": "accepted"},
    ]
    assert rejected["status"] == "unsupported"
    assert rejected["reason"] == "observation event identity conflicts with stored data"
    assert "observation_receipts" not in rejected


def test_statsd_browser_upload_rejects_duplicate_event_ids_inside_one_request(tmp_path):
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    current_service.writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    duplicate = payload(observations=[payload()["observations"][0]] * 2)
    try:
        response, _binary = current_service.handle_with_binary(
            {
                "action": "browser_upload",
                "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "schema_generation": storage.SCHEMA_VERSION,
                "authenticated_username": "alice",
            },
            json.dumps(duplicate).encode("utf-8"),
        )
    finally:
        current_service.writer.close()

    assert response["status"] == "unsupported"
    assert response["reason"] == "browser observation event IDs must be unique within one upload"
    assert "observation_receipts" not in response


@pytest.mark.parametrize(
    "event_payload",
    [
        {
            "kind": "api", "endpoint": "/api/session-metadata", "method": "GET",
            "request_id": "r-web-page-7", "status": 200, "latency_ms": 8553.6,
            "bytes": 1_900_044, "queue_ms": 1.5, "connect_ms": 2.5,
            "tls_ms": 1.25, "ttfb_ms": 8400, "download_ms": 100,
            "apply_render_ms": 49.35, "connection_protocol": "h2",
            "journey_id": "j-reload-7", "code_revision": "a25ff8ff3",
            "browser_family": "chromium",
        },
        {
            "kind": "page_load", "endpoint": "/", "navigation_ms": 4,
            "bundle_parse_eval_ms": 31, "first_paint_ms": 35,
            "first_contentful_paint_ms": 38, "first_api_ms": 42, "fanout_ms": 80,
            "interactive_ms": 240, "app_ready_ms": 240, "fanout_count": 9,
            "max_concurrency": 6, "journey_id": "j-reload-7",
            "code_revision": "a25ff8ff3", "browser_family": "chromium",
        },
        {
            "kind": "interaction", "latency_ms": 180, "input_delay_ms": 70,
            "processing_ms": 50, "presentation_delay_ms": 60,
            "interaction_type": "click", "journey_id": "j-action-7",
            "code_revision": "a25ff8ff3", "browser_family": "chromium",
        },
        {
            "kind": "operation_wait", "latency_ms": 3200,
            "operation_kind": "session_files", "outcome": "ready",
            "request_id": "r-web-7", "journey_id": "j-action-7",
            "code_revision": "a25ff8ff3", "browser_family": "chromium",
        },
        {
            "kind": "long_task", "latency_ms": 88.5, "journey_id": "j-reload-7",
            "code_revision": "a25ff8ff3", "browser_family": "chromium",
        },
        {
            "kind": "heartbeat", "latency_ms": 8, "bytes": 100,
            "upload_queue_depth": 17, "upload_drops": 2, "upload_retries": 3,
            "instrumentation_cost_ms": 0.42, "journey_id": "j-reload-7",
            "code_revision": "a25ff8ff3", "browser_family": "chromium",
        },
    ],
)
def test_api_and_page_load_profiling_facts_survive_privacy_binding(event_payload):
    value = payload()
    value["observations"][0]["payload"] = event_payload

    parsed = observations.parse_browser_observations(
        value, client_binding_secret=b"s" * 32, authenticated_username="alice",
    )

    assert dict(parsed[0].payload) == event_payload


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(protocol_version=storage.MIN_WRITER_PROTOCOL - 1), "not current"),
        (lambda value: value.update(extra=True), "fields"),
        (lambda value: value["observations"][0].update(family="cpu"), "family"),
        (lambda value: value["observations"][0].update(source_id="other"), "source_id"),
        (lambda value: value["observations"][0].update(payload={"kind": "api", "duration_ms": 1}), "duration_ms"),
        (lambda value: value["observations"][0].update(payload={"kind": "api", "endpoint": "/api/ping?secret=value"}), "query"),
        (lambda value: value.update(observations=[]), "1..1000"),
        (lambda value: value.update(observations=value["observations"] * 1_001), "1..1000"),
    ],
)
def test_invalid_or_stale_batches_fail_before_append(mutate, message):
    value = payload()
    mutate(value)
    error = observations.BrowserObservationUpgradeRequired if "not current" in message else observations.BrowserObservationError
    with pytest.raises(error, match=message):
        observations.parse_browser_observations(
            value, client_binding_secret=b"s" * 32, authenticated_username="alice",
        )


class FakeClient:
    def __init__(self):
        self.uploads = []
        self.response = {"ok": True, "source_generation": 8, "accepted": 1, "duplicates": 0, "observation_receipts": [{"event_id": "page:1", "disposition": "accepted"}], "counts": {"private": "not public"}}

    def ensure_started(self):
        return True

    def append(self, *, browser_upload, authenticated_username):
        body = browser_upload
        self.uploads.append((body, authenticated_username))
        return self.response

    def status(self):
        return {"ok": True}


def test_app_returns_only_current_acknowledgement_fields():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = FakeClient()

    body = b'{"unparsed":"browser upload"}'
    response, status = webapp.record_current_browser_observations(
        body, authenticated_username="alice",
    )

    assert status == HTTPStatus.OK
    assert response == {
        "ok": True,
        "source_generation": 8,
        "accepted": 1,
        "duplicates": 0,
        "observation_receipts": [{"event_id": "page:1", "disposition": "accepted"}],
    }
    assert webapp.stats_current_client.uploads == [(body, "alice")]


def test_app_projects_statsd_upgrade_response_without_parsing_the_upload():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = FakeClient()
    webapp.stats_current_client.response = {
        "ok": False,
        "status": "upgrade_required",
        "required_protocol_version": storage.MIN_WRITER_PROTOCOL,
    }
    stale = b'{"protocol_version":0}'

    response, status = webapp.record_current_browser_observations(
        stale, authenticated_username="alice",
    )

    assert status == HTTPStatus.UPGRADE_REQUIRED
    assert response["status"] == "upgrade_required"
    assert response["required_protocol_version"] == storage.MIN_WRITER_PROTOCOL
    assert webapp.stats_current_client.uploads == [(stale, "alice")]


def test_app_preserves_typed_identity_conflict_without_payload_echo():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = FakeClient()
    webapp.stats_current_client.response = {
        "ok": False,
        "status": "unsupported",
        "reason": "observation event identity conflicts with stored data",
    }

    response, status = webapp.record_current_browser_observations(
        b'{"private":"browser upload"}', authenticated_username="alice",
    )

    assert status == HTTPStatus.BAD_REQUEST
    assert response == {
        "ok": False,
        "status": "unsupported",
        "reason": "observation event identity conflicts with stored data",
    }
    assert "private" not in json.dumps(response)


def test_http_route_is_authenticated_bounded_and_passes_username(monkeypatch):
    calls = []
    writes = []
    value = b'{"raw":"bytes"}'
    app = SimpleNamespace(record_current_browser_observations=lambda body, *, authenticated_username: calls.append((body, authenticated_username)) or ({"ok": True}, HTTPStatus.OK))
    request = SimpleNamespace(
        server=SimpleNamespace(app=app),
        auth_identity=lambda: SimpleNamespace(username="alice"),
        read_request_body=lambda limit: (value, None, HTTPStatus.OK),
        write_json=lambda body, status=HTTPStatus.OK: writes.append((body, status)),
    )

    route = http_routes.route_for_request("POST", "/api/stats-observations")
    assert route is not None
    http_routes.post_stats_observations(request, None, route)

    assert calls == [(value, "alice")]
    assert writes == [({"ok": True}, HTTPStatus.OK)]
    assert route.handler is http_routes.post_stats_observations
    assert route.role == "readonly"
    assert route.body_limit == 128 * 1024


def test_stats_client_forwards_browser_upload_as_binary_without_decoding(monkeypatch, tmp_path):
    current = stats_client.StatsCurrentClient(tmp_path / "stats.sock", tmp_path / "stats.db")
    calls = []
    monkeypatch.setattr(current, "ensure_started", lambda: True)
    monkeypatch.setattr(
        current._transport,
        "dispatch",
        lambda action, payload, *, timeout, request_binary=b"": calls.append(
            (action, payload, timeout, request_binary)
        ) or ({"ok": True, "accepted": 1}, b""),
    )
    body = b'{"still":"raw"}'

    response = current.append(browser_upload=body, authenticated_username="alice")

    assert response == {"ok": True, "accepted": 1}
    assert calls == [("browser_upload", {"authenticated_username": "alice"}, 3.0, body)]


def test_statsd_decodes_validates_and_appends_browser_upload(tmp_path):
    database = tmp_path / storage.DATABASE_FILENAME
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", database,
    )
    body = json.dumps(payload()).encode("utf-8")
    with storage.Store.open(database) as store:
        current_service.writer = store
        response, binary = current_service.handle_with_binary(
            {
                "action": "browser_upload",
                "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "schema_generation": storage.SCHEMA_VERSION,
                "authenticated_username": "alice",
            },
            body,
        )
    current_service.writer = None

    assert binary == b""
    assert response["ok"] is True
    assert response["accepted"] == 1


def test_statsd_appends_accepted_browser_failure_to_durable_jsonl(tmp_path):
    database = tmp_path / storage.DATABASE_FILENAME
    current_service = service.StatsCurrentService(
        tmp_path / "stats.sock", database, clock=lambda: 100_000.0,
    )
    body = json.dumps(payload(observations=[{
        "event_id": "request-failure-1", "family": "browser", "source_id": "browser-private",
        "observed_at": 100.5, "epoch_id": "page-1",
        "payload": {"kind": "error", "signature": "jsf-abc123", "message": "real inline throw", "source": "/static/yomux.js", "line": 42, "column": 9, "stack": "Error: real inline throw", "provenance": "confirmed_real"},
    }])).encode("utf-8")
    with storage.Store.open(database) as store:
        current_service.writer = store
        response, _binary = current_service.handle_with_binary({
            "action": "browser_upload", "protocol_version": storage.MIN_WRITER_PROTOCOL,
            "schema_generation": storage.SCHEMA_VERSION, "authenticated_username": "alice",
        }, body)
        snapshot = store.read_snapshot()
    current_service.writer = None

    log_path = database.with_name(f"{database.stem}.browser-failures.jsonl")
    assert response["accepted"] == 1
    assert any(item.payload.get("kind") == "error" for item in snapshot.observations)
    line = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert line == {
        "timestamp": 100.5, "signature": "jsf-abc123", "source": "/static/yomux.js",
        "line": 42, "column": 9, "message": "real inline throw", "stack": "Error: real inline throw", "provenance": "confirmed_real",
    }
    assert "accepted_observation_ids" not in response["counts"]
    assert "accepted_original_timestamps" not in response["counts"]


def test_statsd_rotates_browser_failure_jsonl_at_its_bound(tmp_path, monkeypatch):
    database = tmp_path / storage.DATABASE_FILENAME
    current_service = service.StatsCurrentService(tmp_path / "stats.sock", database)
    monkeypatch.setattr(service, "BROWSER_FAILURE_LOG_MAX_BYTES", 250)

    with storage.Store.open(database) as store:
        current_service.writer = store
        for index in range(3):
            body = json.dumps(payload(observations=[{
                "event_id": f"request-failure-{index}", "family": "browser", "source_id": "browser-private",
                "observed_at": 100.5 + index, "epoch_id": "page-1",
                "payload": {
                    "kind": "error", "signature": f"jsf-abc12{index}", "message": f"failure {index}",
                    "source": "/static/yomux.js", "line": 42, "column": 9,
                },
            }])).encode("utf-8")
            response, _binary = current_service.handle_with_binary({
                "action": "browser_upload", "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "schema_generation": storage.SCHEMA_VERSION, "authenticated_username": "alice",
            }, body)
            assert response["accepted"] == 1
    current_service.writer = None

    log_path = current_service.browser_failure_log_path
    rotated_path = log_path.with_suffix(log_path.suffix + ".1")
    assert log_path.stat().st_size <= service.BROWSER_FAILURE_LOG_MAX_BYTES
    assert rotated_path.stat().st_size <= service.BROWSER_FAILURE_LOG_MAX_BYTES
    records = [
        json.loads(line)
        for path in (rotated_path, log_path)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert {record["message"] for record in records} == {"failure 0", "failure 1", "failure 2"}


def test_sanitize_retained_payload_redacts_then_rebounds_so_second_validation_passes():
    """W2: redaction markers expand text past the byte bound; sanitize must re-bound
    so the mandatory second validation passes instead of failing on raw-valid input."""
    raw = {
        "kind": "error",
        "signature": "sig",
        "message": ("token=a " * 63)[:500],
        "stack": ("token=a " * 500)[:4000],
        "source": "/api/diagnostic",
    }
    validated = families.validate_payload("browser", raw)
    sanitized = browser_family.sanitize_retained_payload(validated)

    assert "token=a" not in sanitized["message"]
    assert "token=a" not in sanitized["stack"]
    assert sanitized["source"] == "/api/diagnostic"
    assert len(sanitized["message"].encode("utf-8")) <= 500
    assert len(sanitized["stack"].encode("utf-8")) <= 4000
    # the mandatory second validation now succeeds
    families.validate_payload("browser", sanitized)

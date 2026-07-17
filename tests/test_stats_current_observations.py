# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current authenticated browser-observation write boundary."""

from http import HTTPStatus
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import http_routes
from yolomux_lib.stats_current import observations, storage


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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(protocol_version=storage.MIN_WRITER_PROTOCOL - 1), "not current"),
        (lambda value: value.update(extra=True), "fields"),
        (lambda value: value["observations"][0].update(family="cpu"), "family"),
        (lambda value: value["observations"][0].update(source_id="other"), "source_id"),
        (lambda value: value["observations"][0].update(payload={"kind": "api", "duration_ms": 1}), "duration_ms"),
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
        self.appended = []

    def ensure_started(self):
        return True

    def append(self, *, observations):
        self.appended.extend(observations)
        return {"ok": True, "source_generation": 8, "accepted": 1, "duplicates": 0, "counts": {"private": "not public"}}

    def status(self):
        return {"ok": True}


def test_app_returns_only_current_acknowledgement_fields():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = FakeClient()

    response, status = webapp.record_current_browser_observations(
        payload(), authenticated_username="alice",
    )

    assert status == HTTPStatus.OK
    assert response == {
        "ok": True,
        "source_generation": 8,
        "accepted": 1,
        "duplicates": 0,
    }
    assert len(webapp.stats_current_client.appended) == 1


def test_app_rejects_a_stale_browser_fence_before_start_or_append():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = FakeClient()
    stale = payload(protocol_version=storage.MIN_WRITER_PROTOCOL - 1)

    response, status = webapp.record_current_browser_observations(
        stale, authenticated_username="alice",
    )

    assert status == HTTPStatus.UPGRADE_REQUIRED
    assert response["status"] == "upgrade_required"
    assert response["required_protocol_version"] == storage.MIN_WRITER_PROTOCOL
    assert webapp.stats_current_client.appended == []


def test_http_route_is_authenticated_bounded_and_passes_username(monkeypatch):
    calls = []
    writes = []
    value = payload()
    monkeypatch.setattr(http_routes, "_json_body", lambda _request, _route: value)
    app = SimpleNamespace(record_current_browser_observations=lambda body, *, authenticated_username: calls.append((body, authenticated_username)) or ({"ok": True}, HTTPStatus.OK))
    request = SimpleNamespace(
        server=SimpleNamespace(app=app),
        auth_identity=lambda: SimpleNamespace(username="alice"),
        write_json=lambda body, status=HTTPStatus.OK: writes.append((body, status)),
    )

    http_routes.post_stats_observations(request, None, object())

    assert calls == [(value, "alice")]
    assert writes == [({"ok": True}, HTTPStatus.OK)]
    route = http_routes.route_for_request("POST", "/api/stats-observations")
    assert route is not None
    assert route.handler is http_routes.post_stats_observations
    assert route.role == "readonly"
    assert route.share_access == http_routes.SHARE_ACCESS_NONE
    assert route.body_limit == 128 * 1024

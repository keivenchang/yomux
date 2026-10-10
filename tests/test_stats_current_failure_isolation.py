# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Failure ownership regressions across the browser, client, and stats runtime."""

import json
import threading
from http import HTTPStatus

from yomux_lib import common
from yomux_lib.stats_current import client as client_module
from yomux_lib.stats_current import collectors, protocol, runtime, storage
from yomux_lib.stats_current import service as stats_current_service
from yomux_lib.local_services import client as local_service_client
from yomux_lib.observability.failure_severity import BROWSER_UPLOAD_OUTCOME_OWNER
from yomux_lib.observability.failure_severity import CALLER_OUTCOME_OWNER_FIELD
from yomux_lib.observability.failure_severity import expected_caller_outcome, failure_record_level

import pytest

# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
pytestmark = pytest.mark.deprecated_python_server


class _FutureServiceClient:
    def __init__(self):
        self.releases = []

    def acquire_lease(self):
        return {"ok": True, "lease_id": "lease-1"}

    def renew_lease(self, _lease_id):
        return {"ok": False, "status": "upgrade_required", "required_protocol_version": 25}

    def release_lease(self, lease_id, *, bypass_cached_upgrade=False):
        self.releases.append((lease_id, bypass_cached_upgrade))
        return {"ok": False, "status": "upgrade_required", "required_protocol_version": 25}

    def append(self, **_groups):
        return {"ok": True}

    def status(self):
        return {"ok": True}


def test_terminal_renewal_failure_survives_release_cleanup():
    client = _FutureServiceClient()
    current = runtime.StatsCurrentRuntime(
        client,
        {family: lambda _attempt: collectors.CollectorFacts() for family in runtime.WEB_COLLECTED_FAMILIES},
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.01,
        retry_max_seconds=0.02,
        owner_check_seconds=0.01,
    )
    try:
        assert current.start() is True
        supervisor = current._supervisor
        assert supervisor is not None
        supervisor.join(1)
        assert supervisor.is_alive() is False
        assert current.status()["supervisor"]["phase"] == "blocked"
        assert current.status()["supervisor"]["last_failure"] == "UpgradeRequired"
        assert client.releases == [("lease-1", True)]
    finally:
        current.stop()


def test_stats_release_timeout_keeps_retry_ownership_until_statsd_acknowledges(monkeypatch):
    releases = []
    settled = threading.Event()

    class Client:
        def release_lease(self, lease_id, *, bypass_cached_upgrade=False):
            releases.append((lease_id, bypass_cached_upgrade))
            if len(releases) == 1:
                return {"ok": False, "_transport_error": "timeout"}
            settled.set()
            return {"ok": True}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(local_service_client, "LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS", 0.01)
    current = runtime.StatsCurrentRuntime(
        Client(),
        {family: lambda _attempt: collectors.CollectorFacts() for family in runtime.WEB_COLLECTED_FAMILIES},
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )

    current._release("lease-1")

    assert settled.wait(1.0), "the retry owner forgot a timed-out statsd lease release"
    assert releases == [("lease-1", False), ("lease-1", False)]
    assert current.status()["supervisor"]["last_failure"] == "LeaseReleaseFailed"


def _stale_browser_upload_body() -> bytes:
    """One browser batch statsd's payload validator rejects as too old."""

    return json.dumps({
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION - 1,
        "client_id": "stale-browser",
        "observations": [],
    }).encode("utf-8")


def _typed_upgrade_failure(details: dict) -> dict:
    """Build the typed failure record the HTTP writer hands the severity owner for a 426."""

    return common.error_payload(
        "client or writer is too old",
        message_key="common.requestFailed",
        canonical=True,
        code="upgrade_required",
        origin="server.http",
        retryable=False,
        details=details,
        stack=[{
            "component": "server.http",
            "operation": "POST /api/stats-observations",
            "code": "upgrade_required",
        }],
        request_id="r-upgrade-1",
    )["error"]


def test_statsd_names_itself_when_it_rejects_a_stale_browser_payload(tmp_path):
    statsd = stats_current_service.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )

    response = statsd._browser_upload(
        {"authenticated_username": "alice"},
        _stale_browser_upload_body(),
    )

    assert response["status"] == "upgrade_required", response
    assert response[CALLER_OUTCOME_OWNER_FIELD] == BROWSER_UPLOAD_OUTCOME_OWNER, response
    # The daemon-wide protocol fence shares the route and must stay unnamed.
    fence, _binary = statsd.handle_with_binary(
        {"protocol_version": storage.MIN_WRITER_PROTOCOL - 1, "schema_generation": storage.SCHEMA_VERSION},
    )
    assert fence["status"] == "upgrade_required", fence
    assert CALLER_OUTCOME_OWNER_FIELD not in fence, fence


def test_stale_browser_payload_upgrade_does_not_poison_shared_stats_work(tmp_path, monkeypatch):
    calls = []
    upgrade = dict(protocol.upgrade_required_response(
        storage.MIN_WRITER_PROTOCOL,
        storage.SCHEMA_VERSION,
        str(storage.MIN_WRITER_BUILD),
        caller_outcome_owner=BROWSER_UPLOAD_OUTCOME_OWNER,
    ))
    upgrade["ok"] = False

    def dispatch(action, payload, *, timeout, request_binary=b""):
        calls.append((action, payload, timeout, request_binary))
        if action == "browser_upload":
            return upgrade, b""
        if action == "lease":
            return {"ok": True, "lease_id": "lease-1"}, b""
        return {"ok": True, "source_generation": 9}, b""

    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    monkeypatch.setattr(client, "ensure_started", lambda: True)
    monkeypatch.setattr(client._transport, "dispatch", dispatch)

    assert client.append(
        browser_upload=_stale_browser_upload_body(),
        authenticated_username="alice",
    ) == upgrade
    assert client._upgrade_required is None
    assert client.renew_lease("lease-1") == {"ok": True, "lease_id": "lease-1"}
    assert client.append(observations=[storage.Observation(
        "cpu-1", "cpu", "host", 1.0, "cpu:1", 1, {"value": 1},
    )]) == {"ok": True, "source_generation": 9}
    assert [call[0] for call in calls] == ["browser_upload", "lease", "append"]


def test_unmarked_browser_upload_upgrade_fences_shared_stats_work(tmp_path, monkeypatch):
    calls = []
    upgrade = {
        "ok": False,
        "status": "upgrade_required",
        "required_protocol_version": protocol.WIRE_PROTOCOL_VERSION + 1,
    }

    def dispatch(action, payload, *, timeout, request_binary=b""):
        calls.append((action, payload, timeout, request_binary))
        return upgrade, b""

    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    monkeypatch.setattr(client, "ensure_started", lambda: True)
    monkeypatch.setattr(client._transport, "dispatch", dispatch)

    assert client.append(
        browser_upload=_stale_browser_upload_body(),
        authenticated_username="alice",
    ) == upgrade
    assert client._upgrade_required == upgrade
    assert client.renew_lease("lease-1") == upgrade
    assert len(calls) == 1


def test_marked_browser_upload_rejection_is_an_ordinary_caller_outcome():
    error = _typed_upgrade_failure({CALLER_OUTCOME_OWNER_FIELD: BROWSER_UPLOAD_OUTCOME_OWNER})

    assert expected_caller_outcome(error, status=HTTPStatus.UPGRADE_REQUIRED) is True
    assert failure_record_level(error, status=HTTPStatus.UPGRADE_REQUIRED) == "info"


def test_unmarked_upgrade_fence_stays_an_operator_fault():
    error = _typed_upgrade_failure({"reason": "reader protocol is too old"})

    assert expected_caller_outcome(error, status=HTTPStatus.UPGRADE_REQUIRED) is False
    assert failure_record_level(error, status=HTTPStatus.UPGRADE_REQUIRED) == "error"


def test_unrecognized_or_misplaced_upgrade_marker_stays_an_operator_fault():
    foreign = _typed_upgrade_failure({CALLER_OUTCOME_OWNER_FIELD: "statsd.some_other_validator"})
    assert failure_record_level(foreign, status=HTTPStatus.UPGRADE_REQUIRED) == "error"

    wrong_status = _typed_upgrade_failure({CALLER_OUTCOME_OWNER_FIELD: BROWSER_UPLOAD_OUTCOME_OWNER})
    assert failure_record_level(wrong_status, status=HTTPStatus.CONFLICT) == "error"

    unstated_status = _typed_upgrade_failure({CALLER_OUTCOME_OWNER_FIELD: BROWSER_UPLOAD_OUTCOME_OWNER})
    assert failure_record_level(unstated_status) == "error"

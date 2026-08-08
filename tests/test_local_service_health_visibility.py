# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A down local service must be an attributable error, and an absent-by-design one must not.

The 0.7.0 QA incident: ``indexd`` was not running, Quick Open still answered from a
stale on-disk snapshot, and the Local-services row said only "Service did not report
healthy status" -- while the registry held the real, specific reason that
``_record_blocked_start`` had recorded. These tests pin both halves: the reason must
reach the row, and a demand-started service that nobody has asked for yet must NOT
raise an alarm.
"""

from typing import Any

from yolomux_lib import app as app_module
from yolomux_lib.local_services.runtime import local_service_failure_text
from yolomux_lib.search.search_indexer import SearchIndexerClient


def classify(row: dict[str, Any]) -> dict[str, Any]:
    """Run the one server-side owner that derives state/reason for a service row."""
    return app_module.TmuxWebtermApp.system_status_service(app_module.TmuxWebtermApp, row)


def test_blocked_indexd_start_reason_reaches_the_service_row(tmp_path, monkeypatch):
    """A start the registry refused must be readable, not flattened to a generic string.

    Measured on the host before this fix, driving a real refused start (an undeletable
    stale record) through SearchIndexerClient:

        registry.status()['failure_reason'] ==
            "indexd start blocked by remove_stale_record (record_pid=999999999,
             reason=missing_host_identity)"
        runtime_status()['last_failure']    == ''      <- dropped on the floor

    A down daemon answers no RPC, so its reason exists ONLY in the registry. This pins
    the mapping rather than the guard, because whether a given guard can be provoked
    depends on the filesystem's permission semantics (it is not reproducible as root).
    """
    reason = "indexd start blocked by remove_stale_record (record_pid=999999999, reason=missing_host_identity)"
    client = SearchIndexerClient(socket_path=tmp_path / "indexer.sock")
    monkeypatch.setattr(client.registry, "status", lambda: {
        "service": "indexd",
        "healthy": False,
        "failure_reason": reason,
        # A daemon that never started answers with a transport error, never a status
        # payload -- so nothing here can carry last_failure.
        "status": {"ok": False, "error_code": "service_unavailable"},
        "record": {},
    })

    row = client.runtime_status()
    assert row["last_failure"] == reason, "the registry reason must reach the service row"

    service = classify(row)
    assert service["state"] == "unavailable"
    assert service["reason"] == reason, "the row must name the guard, not a generic sentence"
    assert service["reason_code"] == "service_unavailable"
    assert service["essential"] is True
    assert service["alerting"] is True


def test_absent_watchd_is_not_an_alarm():
    """watchd is demand-started: absent with no client attached is correct, not an error.

    This is the negative control. Before the fix this row classified as
    'unavailable' / 'Service did not report healthy status' -- identical to a
    genuinely broken service -- so any indicator keyed on it would be always-on.
    """
    row = {
        "service": "watchd",
        "pid": 0,
        "healthy": False,
        "demand_started": True,
        "last_failure": "",
        "resources": {},
    }
    service = classify(row)
    assert service["state"] == "idle"
    assert service["reason_code"] == "not_started"
    assert service["alerting"] is False, "an absent demand-started service must not alarm"


def test_demand_started_service_with_a_real_failure_still_alarms():
    """Demand-started does not mean failures are excused."""
    row = {
        "service": "watchd",
        "pid": 0,
        "healthy": False,
        "demand_started": True,
        "last_failure": "watchd exited (1): boom",
        "resources": {},
    }
    service = classify(row)
    assert service["state"] == "unavailable"
    assert service["reason"] == "watchd exited (1): boom"
    assert service["alerting"] is True


def test_healthy_services_raise_no_alert():
    """Negative control: nothing degraded means no indicator anywhere."""
    rows = [
        {"service": name, "pid": 4321, "healthy": True, "last_failure": "", "resources": {}}
        for name in ("indexd", "statsd", "jobd", "statusd", "approvald")
    ]
    rows.append({"service": "watchd", "pid": 0, "healthy": False, "demand_started": True, "last_failure": "", "resources": {}})
    services = [classify(row) for row in rows]
    assert [service["state"] for service in services] == ["running"] * 5 + ["idle"]
    assert app_module.local_services_alert(services) == {}


def test_alert_names_the_service_and_its_reason():
    """The indicator must name the service and what is degraded, not just show a dot."""
    services = [
        classify({"service": "jobd", "pid": 1, "healthy": True, "last_failure": "", "resources": {}}),
        classify({"service": "indexd", "pid": 0, "healthy": False, "last_failure": "indexd exited (1): boom", "resources": {}}),
    ]
    alert = app_module.local_services_alert(services)
    assert alert["count"] == 1
    assert alert["services"] == [{
        "id": "indexd",
        "label": "Quick Open index",
        "state": "unavailable",
        "reason_code": "service_unavailable",
        "reason": "indexd exited (1): boom",
    }]


def test_failure_text_helper_prefers_the_live_payload_then_the_registry():
    """One shared expression for the five clients that each used to spell it differently."""
    assert local_service_failure_text({"failure_reason": "registry"}, {"last_error": "live"}) == "live"
    assert local_service_failure_text({"failure_reason": "registry"}, {"last_failure": "live"}) == "live"
    assert local_service_failure_text({"failure_reason": "registry"}, {}) == "registry"
    assert local_service_failure_text({}, {}) == ""

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

import os
import time
from typing import Any

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.local_services import registry as registry_mod
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
    """Negative control: nothing degraded means no row alarms anywhere."""
    rows = [
        {"service": name, "pid": 4321, "healthy": True, "last_failure": "", "resources": {}}
        for name in ("indexd", "statsd", "jobd", "statusd", "approvald")
    ]
    rows.append({"service": "watchd", "pid": 0, "healthy": False, "demand_started": True, "last_failure": "", "resources": {}})
    services = [classify(row) for row in rows]
    assert [service["state"] for service in services] == ["running"] * 5 + ["idle"]
    # No row is alarming: each consumer reads its own `alerting`, and none is set here.
    assert all(service["alerting"] is False for service in services)


def test_a_degraded_service_names_itself_and_its_reason():
    """A degraded row must name the service and what is degraded, not just flip a flag.

    The rolled-up `alert` summary is gone (W13): every consumer reads each row's own typed
    fields, so this pins those fields on the row itself.
    """
    services = [
        classify({"service": "jobd", "pid": 1, "healthy": True, "last_failure": "", "resources": {}}),
        classify({"service": "indexd", "pid": 0, "healthy": False, "last_failure": "indexd exited (1): boom", "resources": {}}),
    ]
    degraded = [service for service in services if service["alerting"] is True]
    assert len(degraded) == 1
    row = degraded[0]
    assert row["id"] == "indexd"
    assert row["label"] == "Quick Open index"
    assert row["state"] == "unavailable"
    assert row["reason_code"] == "service_unavailable"
    assert row["reason"] == "indexd exited (1): boom"


def test_failure_text_helper_prefers_the_live_payload_then_the_registry():
    """One shared expression for the five clients that each used to spell it differently."""
    assert local_service_failure_text({"failure_reason": "registry"}, {"last_error": "live"}) == "live"
    assert local_service_failure_text({"failure_reason": "registry"}, {"last_failure": "live"}) == "live"
    assert local_service_failure_text({"failure_reason": "registry"}, {}) == "registry"
    assert local_service_failure_text({}, {}) == ""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_idle_exited_demand_daemon_zombie_reads_as_idle_not_errored():
    """A demand daemon that idle-exits but is not yet reaped must classify idle, not "errored".

    The live 7771 defect: a demand daemon (watchd) adopted by a later supervisor generation
    idle-exits, nothing wait()s it, and it lingers as a zombie whose pid `os.kill(pid, 0)`
    still reports alive. The service record still names that pid, so `pid > 0` and
    `observed_health`'s pid-derived `running` both read it as running-but-unhealthy and the
    System row shows "issue" / "Service did not report healthy status" -- an "errored" alarm
    for a service that simply went idle. Forge that exact state with a real unreaped zombie and
    prove the row now reads its `/proc` State and classifies it idle/absent.
    """
    child = os.fork()
    if child == 0:  # pragma: no cover - child never returns
        os._exit(0)
    try:
        deadline = time.monotonic() + 2.0
        while registry_mod.process_state(child) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert registry_mod.process_state(child) == "Z", registry_mod.process_state(child)

        row = {
            "service": "watchd",
            "pid": child,
            "healthy": False,
            "demand_started": True,
            "last_failure": "",
            "resources": {},
        }
        service = classify(row)
        assert service["state"] == "idle", service
        assert service["reason_code"] == "not_started"
        assert service["reason"] == "Starts on demand"
        assert service["alerting"] is False, "an idle-exited demand daemon must not alarm"
    finally:
        os.waitpid(child, 0)


def test_live_but_unhealthy_daemon_still_reads_as_issue():
    """The distinction the zombie fix must preserve: a genuinely-running daemon still alarms.

    A daemon whose pid is a live, serving process (state R/S/D) that reports unhealthy is a
    real outage, not an idle exit. Its `/proc` State is not `Z`, so the zombie guard leaves it
    alone and it classifies "issue" as before.
    """
    row = {
        "service": "jobd",
        "pid": os.getpid(),
        "healthy": False,
        "last_failure": "",
        "resources": {},
    }
    assert registry_mod.process_state(os.getpid()) != "Z"
    service = classify(row)
    assert service["state"] == "issue", service
    assert service["alerting"] is True

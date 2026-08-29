# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A historical WORK-ITEM failure must never be published as a CURRENT daemon failure.

Measured on live 7771 (pid 2353047, commit 71ab4d6bc) on 2026-08-08: the retained health
document reported `batchd degraded / terminal_failure`, while a status RPC over that port's own
`batchd.sock` answered `ok=true` in 0.42-0.94ms with every lane idle and free capacity. The
daemon was healthy; the monitor was wrong.

The cause was one name carrying two meanings. `PersistentJobBroker.common_status()` derived
`last_failure` by scanning the bounded record ring for the most recent `failed`/`timed_out`
job. That is history: a later success does NOT clear it, and only eviction from the 256-entry
ring ever does. `BatchClient.runtime_status()` passed it through `local_service_failure_text()`,
which publishes exactly that name, and `observed_health()` reads any non-empty `last_failure`
on a live pid as CURRENT degradation. So two `session_files_view` jobs that timed out under
load pinned a healthy, serving batchd to `degraded`/`terminal_failure` indefinitely -- the
"always-on indicator is as useless as silence" failure the daemon-monitor DOIT names.

The split is at the PRODUCER, not in the reducer. Weakening `observed_health()` to ignore
`last_failure` on a live process would hide real current failures for the other five services,
so the reducer's contract is asserted here UNCHANGED and both directions are proven:
a stale job failure reduces to `ready`, a current daemon or registry failure still degrades.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yolomux_lib.backend_health.observer import PROBE_OK
from yolomux_lib.backend_health.observer import REASON_EXITED
from yolomux_lib.backend_health.observer import REASON_NONE
from yolomux_lib.backend_health.observer import REASON_SERVICE_UNHEALTHY
from yolomux_lib.backend_health.observer import observed_health
from yolomux_lib.infra.batchd import JobRecord
from yolomux_lib.infra.batchd import PersistentJobBroker
from yolomux_lib.local_services.runtime import local_service_failure_text


# The exact error text the live batchd was reporting when it was measured healthy.
LIVE_STALE_ERROR = "deadline exceeded while executing"


def _broker(tmp_path: Path) -> PersistentJobBroker:
    """Build the real producer. `__init__` only derives paths; nothing binds or spawns."""

    return PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=60.0, workers=1)


def _record(job_id: str, status: str, *, error: str = "", completed_at: float = 0.0) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        task="session_files_view",
        payload=b"",
        priority="normal",
        generation=1,
        coalesce_key="",
        submitted_at=1.0,
        status=status,
        error=error,
        completed_at=completed_at,
    )


# --- The producer: history travels under its own name ------------------------------------


@pytest.mark.parametrize("status", ["failed", "timed_out"])
def test_a_retained_job_failure_is_published_as_last_job_failure_not_last_failure(tmp_path, status):
    """Both terminal failure statuses are work-item history, so neither may claim the daemon."""

    broker = _broker(tmp_path)
    broker.records["j1"] = _record("j1", status, error=LIVE_STALE_ERROR)

    payload = broker.common_status()

    assert payload["last_job_failure"] == LIVE_STALE_ERROR
    # The name `observed_health` reads as CURRENT degradation must not carry job history.
    assert "last_failure" not in payload


def test_a_later_success_does_not_erase_the_job_failure_from_its_own_diagnostic(tmp_path):
    """The history stays readable. Moving the name must not cost the operator the evidence."""

    broker = _broker(tmp_path)
    broker.records["j1"] = _record("j1", "timed_out", error=LIVE_STALE_ERROR)
    broker.records["j2"] = _record("j2", "completed", completed_at=500.0)

    payload = broker.common_status()

    assert payload["last_job_failure"] == LIVE_STALE_ERROR
    assert payload["last_success"] == 500.0
    assert "last_failure" not in payload


def test_a_batchd_payload_carrying_only_job_history_yields_no_service_failure_text(tmp_path):
    """`local_service_failure_text` is the seam that names a service failure; history is not one."""

    broker = _broker(tmp_path)
    broker.records["j1"] = _record("j1", "failed", error=LIVE_STALE_ERROR)

    assert local_service_failure_text({}, broker.common_status()) == ""


def test_a_current_registry_failure_still_reaches_the_service_failure_text(tmp_path):
    """The daemon's own trouble travels as the registry's `failure_reason`, and must survive."""

    broker = _broker(tmp_path)
    broker.records["j1"] = _record("j1", "failed", error=LIVE_STALE_ERROR)

    text = local_service_failure_text({"failure_reason": "batchd exited (1)"}, broker.common_status())

    assert text == "batchd exited (1)"


# --- The reducer: unchanged contract, proven in both directions ---------------------------


def test_a_healthy_running_batchd_with_a_stale_job_failure_reduces_to_ready(tmp_path):
    """The live contradiction, end to end: healthy daemon + old failed job => ready."""

    broker = _broker(tmp_path)
    broker.records["j1"] = _record("j1", "timed_out", error=LIVE_STALE_ERROR)
    payload = broker.common_status()
    row = {
        "service": "batchd",
        "pid": 2353349,
        "healthy": True,
        "last_success": payload["last_success"],
        "last_failure": local_service_failure_text({}, payload),
        "last_job_failure": payload["last_job_failure"],
    }

    assert observed_health(row, PROBE_OK) == ("ready", REASON_NONE)
    # ...and the operator can still see what failed, under the name that means history.
    assert row["last_job_failure"] == LIVE_STALE_ERROR


def test_a_running_daemon_reporting_itself_unhealthy_still_degrades(tmp_path):
    """Negative control for the reducer: `healthy is False` must keep alarming.

    The STATE stays `degraded` (the warning is preserved); the reason is `service_unhealthy`,
    not `terminal_failure`, because a live pid is not the registry's latched permanent death.
    """

    row = {"service": "batchd", "pid": 2353349, "healthy": False, "last_failure": ""}

    assert observed_health(row, PROBE_OK) == ("degraded", REASON_SERVICE_UNHEALTHY)


def test_a_running_daemon_with_a_current_registry_failure_still_degrades():
    """Negative control: a real CURRENT failure reaching `last_failure` must keep alarming.

    A running process reporting a fault is `service_unhealthy` -- distinct from the not-running
    latched `terminal_failure` fence, which the absent-daemon control below still exercises.
    """

    row = {"service": "batchd", "pid": 2353349, "healthy": True, "last_failure": "batchd exited (1)"}

    assert observed_health(row, PROBE_OK) == ("degraded", REASON_SERVICE_UNHEALTHY)


def test_an_absent_daemon_with_a_current_failure_is_still_down():
    """Negative control: the not-running branch keeps reading `last_failure` as a real failure."""

    row = {"service": "batchd", "pid": 0, "healthy": False, "last_failure": "batchd exited (1)"}

    assert observed_health(row, PROBE_OK) == ("down", REASON_EXITED)


def test_the_reducer_still_ignores_the_diagnostic_name_entirely():
    """`last_job_failure` is diagnostic only. If the reducer ever reads it, this bug returns."""

    row = {"service": "batchd", "pid": 2353349, "healthy": True, "last_failure": "", "last_job_failure": LIVE_STALE_ERROR}

    assert observed_health(row, PROBE_OK) == ("ready", REASON_NONE)

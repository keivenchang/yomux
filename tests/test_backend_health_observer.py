# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M4 of DOIT.p0.daemon-monitor: the continuous observer and its lifecycle.

Everything here drives `BackendHealthObserver` with an injected monotonic clock, an injected
wall clock and an injected wait, so no test sleeps. The one exception is
`test_a_probe_that_never_answers_is_bounded_by_the_probe_timeout`, which measures the bound
itself: it waits on the 50ms deadline under test and asserts the cycle finished inside it.

Two things in this file are structural rather than behavioural, and they are the reason the
observer is allowed to exist at all:

  * The RECORDED SAMPLER DECISION. A periodic sampler already exists
    (`collect_current_stats_service_load`, app.py:2766, cadence families.py:153-159). The
    observer runs beside it, and `test_observer_and_service_load_share_one_row_producer_map`
    plus `test_observer_declares_no_second_inventory_or_row_shape` prove the two cannot
    disagree, because neither produces a row: both consume
    `local_services_row_producers()` through `LocalServicesCollector`.

  * ZERO DEMAND-SCOPED STARTS. `tests/test_backend_health_catalog.py` asserts no start
    primitive is named anywhere on the projection path; the observer sits on that same path
    and is held to the same rule, statically and at runtime.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers.backend_health_scenarios import BackendHealthHarness as Harness
from tests.helpers.backend_health_scenarios import FakeService
from tests.helpers.clock import FakeClock

from yolomux_lib import app as app_module
from yolomux_lib import cli as cli_module
from yolomux_lib import local_service_projection
from yolomux_lib.stats_current import collectors as stats_current_collectors
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_IMMEDIATE_STATES
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVE_SECONDS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVER_THREAD_PREFIX
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS
from yolomux_lib.backend_health.observer import PROBE_FAILED
from yolomux_lib.backend_health.observer import PROBE_OK
from yolomux_lib.backend_health.observer import PROBE_TIMEOUT
from yolomux_lib.backend_health.observer import REASON_PROBE_TIMEOUT
from yolomux_lib.backend_health.observer import REASON_SERVICE_UNHEALTHY
from yolomux_lib.backend_health.observer import REASON_TERMINAL_FAILURE
from yolomux_lib.backend_health.observer import BackendHealthObserver
from yolomux_lib.backend_health.observer import observed_health
from yolomux_lib.backend_health.observer import overall_health_state
from yolomux_lib.backend_health.store import BACKEND_HEALTH_REASON_CODES
from yolomux_lib.backend_health.store import BACKEND_HEALTH_STATES
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.stats_current.client import STATUS_TIMEOUT_SECONDS
from yolomux_lib.local_service_projection import LOCAL_SERVICE_INVENTORY
from yolomux_lib.local_services.rpc import LOCAL_SERVICE_TRAFFIC_PROBE
from yolomux_lib.local_services.rpc import LOCAL_SERVICE_TRAFFIC_WORK
from yolomux_lib.local_services.rpc import local_service_traffic_class
from yolomux_lib.local_services.rpc import local_service_traffic_ledger
from yolomux_lib.local_services.rpc import local_service_traffic_snapshot
from yolomux_lib.local_services.rpc import reset_local_service_traffic
from yolomux_lib.statusd_client import StatusClient
from yolomux_lib.infra.jobd import JobClient
from yolomux_lib.infra.jobd import JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE
from yolomux_lib.approval.approvald import ApprovalClient


REPO_ROOT = Path(__file__).resolve().parents[1]
OBSERVER_SOURCE = REPO_ROOT / "yolomux_lib" / "backend_health" / "observer.py"
CLI_SOURCE = REPO_ROOT / "yolomux_lib" / "cli.py"
APP_SOURCE = REPO_ROOT / "yolomux_lib" / "app.py"

PORT = 7799

# The two attribute names that may create or lease a process, copied from the rule
# `tests/test_backend_health_catalog.py` already enforces for the projection path.
START_PRIMITIVE_ATTRIBUTES = frozenset({"ensure_started", "acquire_lease"})


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    reset_local_service_traffic()
    built = Harness(tmp_path)
    yield built
    built.observer.stop()
    reset_local_service_traffic()


def test_shared_fake_clock_rejects_unmeasurable_values_but_allows_wall_clock_corrections():
    clock = FakeClock(10.0)
    assert clock.advance(-2.0) == 8.0
    with pytest.raises(ValueError, match="finite"):
        clock.advance(float("nan"))
    with pytest.raises(ValueError, match="finite"):
        FakeClock(float("inf"))


# -- the reducer -------------------------------------------------------------------------


def test_every_reduced_state_is_one_of_the_seven_typed_states():
    rows = [
        {"pid": 100, "healthy": True},
        {"pid": 100, "healthy": False},
        {"pid": 100, "transport_reason": "connection refused"},
        {"pid": 0, "demand_started": True},
        {"pid": 0, "last_failure": "worker exited"},
        {"pid": 0, "terminal_failure": True},
        {"pid": 0, "restart_backoff_seconds": 4.0},
        {"pid": 0},
        {"upgrade_required": {"required_protocol_version": 2}},
    ]
    for row in rows:
        state, reason = observed_health(row)
        assert state in BACKEND_HEALTH_STATES, row
        assert reason and reason.islower(), row
    assert observed_health({}, PROBE_TIMEOUT) == ("unknown", "probe_timeout")
    assert observed_health({}, PROBE_FAILED) == ("unknown", "probe_failed")


def test_a_demand_scoped_absent_service_is_not_a_failure():
    # The health contract is explicit: absence alone is not failure. `starting` is the one
    # non-serving, non-degraded state, so an idle watchd never reaches degraded_resources.
    assert observed_health({"pid": 0, "demand_started": True}) == ("starting", "service_absent")
    assert observed_health({"pid": 0, "last_failure": "start blocked"}) == ("down", "exited")
    # ...and a service that declares NEITHER excuse still reads as down. That is the safety
    # direction and it is deliberately the default: statsd and jobd-with-the-scheduler-lease
    # are absent only when they have really failed.
    assert observed_health({"pid": 0}) == ("down", "service_absent")


# -- per-service classification, pinned through the real row producers --------------------


def _absent_row(producer) -> dict[str, Any]:
    """One real `runtime_status()` row from a client whose service was never started."""
    row = producer()
    assert row["pid"] == 0, row
    assert not row.get("last_failure"), row
    return row


def test_statusd_absent_with_no_browser_subscriber_is_idle_not_an_alarm(tmp_path: Path):
    """statusd is demand-scoped: the SSE generation lease is the only thing that pins it.

    Built from the production client, not a hand-written dict, so this cannot pass while the
    row it actually publishes says something else.
    """

    row = _absent_row(StatusClient(tmp_path / "statusd.sock").runtime_status)
    assert row["demand_started"] is True, row
    assert observed_health(row) == ("starting", "service_absent")


def test_approvald_absent_with_no_auto_approve_target_is_idle_not_an_alarm(tmp_path: Path):
    """approvald is demand-scoped: only `start_worker` creates it, and it retires itself."""

    row = _absent_row(ApprovalClient(tmp_path / "approvald.sock").runtime_status)
    assert row["demand_started"] is True, row
    assert observed_health(row) == ("starting", "service_absent")


def test_jobd_absence_is_an_outage_only_while_this_process_owns_scheduling(tmp_path: Path):
    """The whole jobd decision in one test: the lease is what makes absence a failure.

    jobd is NOT demand-scoped -- `start_for_scheduler()` pins the broker with a registry lease
    and the broker refuses to idle out while any lease is held. So a scheduling owner that
    cannot see jobd is looking at a real outage and must alarm. The mirror case is the reason
    the typed field exists: before this process wins the election, or when it never does,
    nothing here is scheduling and jobd's absence is expected rather than broken.
    """

    client = JobClient(tmp_path / "jobd.sock")

    assert client.holds_scheduler_lease is False
    idle_row = _absent_row(client.runtime_status)
    assert idle_row["absence_expected_reason"] == JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE, idle_row
    assert "demand_started" not in idle_row, idle_row
    assert observed_health(idle_row) == ("starting", JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE)

    # Now this process owns background scheduling. Nothing else about the row changes.
    client._scheduler_lease_id = "lease-1"
    assert client.holds_scheduler_lease is True
    owning_row = _absent_row(client.runtime_status)
    assert owning_row["absence_expected_reason"] == "", owning_row
    assert observed_health(owning_row) == ("down", "service_absent")


def test_statsd_declares_neither_excuse_because_a_loop_keeps_it_hot():
    """statsd's row is built in app.py, so the classification is pinned on the shape it emits.

    `tests/test_backend_health_catalog.py` holds the static half -- that `statsd_runtime_status`
    spells neither field. This is the consequence: an absent statsd is a verified `down`,
    because a service a 1s collector loop appends to is not idle when it disappears.
    """
    statsd_row = {"service": "statsd", "pid": 0, "healthy": False, "last_failure": ""}
    assert observed_health(statsd_row) == ("down", "service_absent")


# -- negative controls for both directions ------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        {"pid": 0, "demand_started": True, "last_failure": "statusd exited (1)"},
        {"pid": 0, "absence_expected_reason": "scheduler_not_owned", "last_failure": "jobd exited (1)"},
        {"pid": 0, "demand_started": True, "terminal_failure": True},
        {"pid": 0, "absence_expected_reason": "scheduler_not_owned", "terminal_failure": True},
        {"pid": 0, "demand_started": True, "transport_reason": "connection refused"},
        {"pid": 0, "absence_expected_reason": "scheduler_not_owned", "transport_reason": "connection refused"},
    ],
)
def test_neither_absence_excuse_can_silence_a_recorded_failure(row: dict[str, Any]):
    """The safety direction, and the more important one.

    An excuse only explains an absence that has NO recorded cause. The moment a row carries a
    real failure -- a recorded exit, a latched terminal failure, a refused transport -- it is
    `down`, whichever excuse it declares. This is what stops "demand-scoped" from degrading
    into "permanently unable to report an outage".
    """
    state, reason = observed_health(row)
    assert state == "down", (row, state, reason)
    assert reason != "service_absent", (row, reason)


def test_a_row_claiming_both_absence_excuses_is_refused():
    """One absence, one excuse. Two owners each excusing it is a contract error, resolved down.

    Failing closed here is deliberate: a row that acquires a stray `demand_started` alongside a
    real keep-hot owner is exactly how a monitored service goes quiet, so the conflict has to be
    louder than either claim, not quieter.
    """
    conflicted = {"pid": 0, "demand_started": True, "absence_expected_reason": "scheduler_not_owned"}
    assert observed_health(conflicted) == ("down", "absence_contract_conflict")


@pytest.mark.parametrize(
    "value",
    ["Scheduler Not Owned", "scheduler not owned", "9lives", "x" * 49, 1, True, ["scheduler_not_owned"]],
)
def test_an_unreadable_absence_reason_cannot_excuse_an_absence(value: Any):
    """A token the retained store would reject must not be able to silence the indicator."""
    state, reason = observed_health({"pid": 0, "absence_expected_reason": value})
    assert (state, reason) == ("down", "absence_reason_invalid"), (value, state, reason)


def test_an_empty_absence_reason_is_readable_and_excuses_nothing():
    """"" is the resting value jobd publishes while it owns scheduling; it is not an error."""
    assert observed_health({"pid": 0, "absence_expected_reason": ""}) == ("down", "service_absent")
    assert observed_health({"pid": 0, "absence_expected_reason": "   "}) == ("down", "service_absent")


def _idle_machine(harness: Harness) -> None:
    """Shape the six rows the way a real idle host publishes them.

    statsd is up because a background loop keeps it hot. indexd, watchd, statusd and approvald
    are absent because nothing has asked for them. jobd is absent because this process has not
    won the background-owner election. None of that is a failure and none of it may alarm.
    """
    for name in ("indexd", "watchd", "statusd", "approvald"):
        harness.services[name].absent()
    # statsd declares neither excuse, exactly as `statsd_runtime_status` publishes it.
    harness.services["statsd"].row.pop("demand_started")
    jobd = harness.services["jobd"]
    jobd.absent()
    jobd.row.pop("demand_started")
    jobd.row["absence_expected_reason"] = "scheduler_not_owned"


def test_an_idle_machine_raises_no_alarm_at_all(harness: Harness):
    """The originating defect: four false alarms on a machine where nothing is wrong.

    An always-on indicator that is on when nothing is wrong is exactly as useless as the silence
    it replaced, so the whole cycle is asserted -- overall state, every per-resource state, and
    the `degraded_resources` list the topbar actually renders.
    """
    _idle_machine(harness)
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)

    assert harness.states() == {
        "indexd": "starting",
        "statsd": "ready",
        "jobd": "starting",
        "statusd": "starting",
        "watchd": "starting",
        "approvald": "starting",
    }
    payload = harness.published[-1][1]
    assert payload["overall_state"] == "starting", payload
    assert payload["degraded_resources"] == [], payload


def test_one_real_outage_on_that_same_idle_machine_still_alarms(harness: Harness):
    """The other half: the quiet machine must not have bought its quiet with deafness."""
    _idle_machine(harness)
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.published[-1][1]["degraded_resources"] == []

    # statsd dies. Nothing else about the machine changed.
    harness.services["statsd"].absent()
    harness.cycle()

    payload = harness.published[-1][1]
    assert payload["overall_state"] == "down", payload
    assert [item["id"] for item in payload["degraded_resources"]] == ["statsd"], payload
    assert payload["degraded_resources"][0]["reason_code"] == "service_absent", payload


def test_five_distinct_causes_do_not_collapse_into_one_reason():
    reasons = {
        observed_health({}, PROBE_TIMEOUT)[1],
        observed_health({}, PROBE_FAILED)[1],
        observed_health({"pid": 0, "demand_started": True})[1],
        observed_health({"pid": 0, "restart_backoff_seconds": 3.0})[1],
        observed_health({"upgrade_required": True})[1],
        observed_health({"pid": 0, "terminal_failure": True})[1],
    }
    assert len(reasons) == 6, reasons


def test_overall_state_reports_the_worst_state_held():
    assert overall_health_state({"a": "ready", "b": "ready"}) == "ready"
    assert overall_health_state({"a": "ready", "b": "starting"}) == "starting"
    assert overall_health_state({"a": "starting", "b": "unknown"}) == "unknown"
    assert overall_health_state({"a": "unknown", "b": "degraded"}) == "degraded"
    assert overall_health_state({"a": "degraded", "b": "down"}) == "down"
    assert overall_health_state({}) == "starting"


# -- typed state before the first observation --------------------------------------------


def test_observer_reports_typed_starting_before_the_first_observation(harness: Harness):
    state = harness.observer.state()
    assert state["observations"] == 0
    assert state["overall_state"] == "starting"
    assert set(state["resources"]) == set(LOCAL_SERVICE_INVENTORY)
    for row in state["resources"].values():
        assert row["state"] == "starting"
        assert row["reason_code"] == "none"
    assert state["revision"] == 0


# -- debounce ----------------------------------------------------------------------------


def test_recovery_to_ready_needs_two_consecutive_observations(harness: Harness):
    first = harness.cycle()
    # Everything is healthy from cycle one, but `ready` is a recovery and must debounce.
    assert all(state == "starting" for state in harness.states().values())
    assert first.published is True and first.revision == 1

    second = harness.cycle()
    assert all(state == "ready" for state in harness.states().values())
    assert second.published is True and second.revision == 2
    assert BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS == 2


def test_one_failed_probe_cannot_flicker_the_indicator(harness: Harness):
    harness.cycle(2)
    assert harness.states()["jobd"] == "ready"
    published_before = len(harness.published)

    harness.services["jobd"].error = RuntimeError("socket hiccup")
    cycle = harness.cycle()
    assert cycle.probe_outcomes["jobd"] == PROBE_FAILED
    # One bad probe is a candidate, not a transition: the UI does not move.
    assert harness.states()["jobd"] == "ready"
    assert cycle.published is False
    assert len(harness.published) == published_before

    harness.services["jobd"].error = None
    harness.cycle()
    assert harness.states()["jobd"] == "ready"
    assert len(harness.published) == published_before

    # Two consecutive failures do move it.
    harness.services["jobd"].error = RuntimeError("socket hiccup")
    harness.cycle(2)
    assert harness.states()["jobd"] == "unknown"
    assert len(harness.published) == published_before + 1


# -- item 4: honest classification of the two noisy producers ----------------------------
#
# Both are retained-history assertions driven through the real observer + store, so a green
# result proves what a caller reads off `STATE_DIR/backend-health/<port>.json`, not just what
# the reducer returns in isolation.


def _watchd_transitions(harness: Harness) -> list[dict[str, Any]]:
    """The retained transition rows for watchd, as they land on disk."""
    return harness.store.document()["resources"]["watchd"]["transitions"]


def test_a_running_service_reporting_a_fault_is_service_unhealthy_not_terminal_failure():
    """A live pid is not terminally failed.

    `terminal_failure` is the registry's latched PERMANENT start-failure fence; a running-
    degraded reconnect window (pid>0 with `healthy=False`, or a recorded `last_failure`) is a
    transient `service_unhealthy`. The STATE stays `degraded` either way -- nothing is hidden --
    but the machine-readable reason no longer borrows the permanent-death token.
    """
    assert observed_health({"service": "watchd", "pid": 100, "healthy": False}) == (
        "degraded",
        REASON_SERVICE_UNHEALTHY,
    )
    assert observed_health({"service": "watchd", "pid": 100, "last_failure": "reconnecting"}) == (
        "degraded",
        REASON_SERVICE_UNHEALTHY,
    )
    # The genuine not-running latched fence is untouched: still down/terminal_failure.
    assert observed_health({"service": "watchd", "pid": 0, "terminal_failure": True}) == (
        "down",
        REASON_TERMINAL_FAILURE,
    )
    # One vocabulary owner. `service_unhealthy` is a documented reason the store accepts, and it
    # is a distinct token from `terminal_failure`, so the store can never collapse them.
    assert REASON_SERVICE_UNHEALTHY in BACKEND_HEALTH_REASON_CODES
    assert REASON_SERVICE_UNHEALTHY != REASON_TERMINAL_FAILURE


def test_the_retained_watchd_reconnect_sequence_never_records_terminal_failure(harness: Harness):
    """The exact retained sequence `watchd: starting -> degraded(...) -> ready`.

    Its degraded step is a running-degraded reconnect (pid>0, `healthy=False`), the precise
    window the observer used to stamp `terminal_failure`. The retained history must carry
    `service_unhealthy` for that step and `terminal_failure` NOWHERE.
    """
    watchd = harness.services["watchd"]

    # starting -> ready for everything, so watchd has a `ready` baseline to degrade from.
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.states()["watchd"] == "ready"

    # A running-degraded reconnect window: the process is alive but reports itself unhealthy.
    watchd.row["pid"] = 100
    watchd.row["healthy"] = False
    watchd.row["last_failure"] = ""
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.states()["watchd"] == "degraded"

    # ...and the process recovers.
    watchd.up()
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.states()["watchd"] == "ready"

    sequence = [(row["previous_state"], row["new_state"], row["reason_code"]) for row in _watchd_transitions(harness)]
    assert sequence == [
        ("", "starting", "none"),
        ("starting", "ready", "none"),
        ("ready", "degraded", REASON_SERVICE_UNHEALTHY),
        ("degraded", "ready", "none"),
    ], sequence
    assert all(row["reason_code"] != REASON_TERMINAL_FAILURE for row in _watchd_transitions(harness))
    # The degraded step is still a WARNING -- the state is preserved, only the reason is precise.
    degraded_row = next(row for row in _watchd_transitions(harness) if row["new_state"] == "degraded")
    assert degraded_row["reason_code"] == REASON_SERVICE_UNHEALTHY


def test_the_statsd_probe_budget_is_tighter_than_statsds_own_status_rpc():
    """The bound mismatch that makes `probe_timeout` a load-induced MISS, not an outage.

    The observer guillotines every probe at 0.5s; statsd's own status RPC budget is 3.0s. An
    alive-but-loaded statsd that answers between those two bounds is cut off at 0.5s and honestly
    reduced to `unknown`/`probe_timeout` -- never `down`. This is CORRECT: raising the 0.5s budget
    or lengthening the debounce to quiet it is exactly what the DOIT forbids.
    """
    assert BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS < STATUS_TIMEOUT_SECONDS
    assert STATUS_TIMEOUT_SECONDS == 3.0
    assert BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS == 0.5
    # A timed-out probe is `unknown`/`probe_timeout`, a bounded typed miss -- not `down`.
    state, reason = observed_health({"service": "statsd"}, PROBE_TIMEOUT)
    assert (state, reason) == ("unknown", REASON_PROBE_TIMEOUT)


def test_a_single_statsd_probe_timeout_does_not_flap_and_two_are_retained_as_unknown(harness: Harness):
    """One 0.5s probe miss is swallowed by the 2-observation debounce; two are retained, never `down`.

    Drives the exact `statsd: ready -> unknown(probe_timeout) -> ready` sequence through a GENUINE
    probe timeout (the injected-clock probe pool spends the whole 0.5s budget and abandons the
    future), so the retained reason is `probe_timeout`, not a raised-error `probe_failed`. A load-
    induced miss is a live pid the observer could not reach in time -- not an outage -- so the
    retained history recovers cleanly and records `down` NOWHERE.
    """
    delays: dict[str, float] = {}
    harness.observer._executor = VirtualProbePool(harness.monotonic, delays)

    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.states()["statsd"] == "ready"
    published_before = len(harness.published)

    # ONE missed probe: statsd is alive but does not answer inside the 0.5s budget this cycle.
    delays["statsd"] = 10_000.0
    single = harness.cycle()
    assert single.probe_outcomes["statsd"] == PROBE_TIMEOUT
    assert harness.states()["statsd"] == "ready"  # a single miss cannot move the indicator
    assert single.published is False
    assert len(harness.published) == published_before

    # A SECOND consecutive miss crosses the debounce and is retained as unknown/probe_timeout.
    second = harness.cycle()
    assert second.probe_outcomes["statsd"] == PROBE_TIMEOUT
    assert harness.states()["statsd"] == "unknown"

    # ...and statsd answers again inside budget: clean recovery, debounced.
    delays.pop("statsd")
    harness.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
    assert harness.states()["statsd"] == "ready"

    transitions = harness.store.document()["resources"]["statsd"]["transitions"]
    sequence = [(row["previous_state"], row["new_state"], row["reason_code"]) for row in transitions]
    assert ("ready", "unknown", REASON_PROBE_TIMEOUT) in sequence, sequence
    assert ("unknown", "ready", "none") in sequence, sequence
    # The load-induced miss is NEVER promoted to a verified outage.
    assert all(row["new_state"] != "down" for row in transitions), transitions


def test_a_verified_exit_transitions_to_down_immediately(harness: Harness):
    harness.cycle(2)
    assert harness.states()["statsd"] == "ready"
    published_before = len(harness.published)

    harness.services["statsd"].down("statsd worker exited")
    cycle = harness.cycle()
    assert harness.states()["statsd"] == "down"
    assert cycle.published is True
    assert len(harness.published) == published_before + 1
    assert "down" in BACKEND_HEALTH_IMMEDIATE_STATES

    # ...and the recovery back out of it is debounced, so a flapping service cannot flicker.
    harness.services["statsd"].up()
    harness.cycle()
    assert harness.states()["statsd"] == "down"
    harness.cycle()
    assert harness.states()["statsd"] == "ready"


def test_upgrade_required_is_immediate(harness: Harness):
    harness.cycle(2)
    harness.services["indexd"].row["upgrade_required"] = {"required_protocol_version": 2}
    harness.cycle()
    assert harness.states()["indexd"] == "upgrade_required"
    assert "upgrade_required" in BACKEND_HEALTH_IMMEDIATE_STATES


# -- publication -------------------------------------------------------------------------


def test_no_event_is_published_when_the_stable_signature_is_unchanged(harness: Harness):
    harness.cycle(2)
    published = len(harness.published)
    revision = harness.observer.state()["revision"]

    for _ in range(5):
        cycle = harness.cycle()
        assert cycle.published is False

    assert len(harness.published) == published
    assert harness.observer.state()["revision"] == revision


def test_each_published_change_advances_exactly_one_store_revision(harness: Harness):
    harness.cycle(2)
    harness.services["statusd"].down("statusd exited")
    harness.cycle()
    harness.services["statusd"].up()
    harness.cycle(2)

    revisions = [payload["revision"] for _, payload in harness.published]
    assert revisions == list(range(1, len(revisions) + 1))
    document = harness.store.document()
    assert document["revision"] == revisions[-1]
    transitions = document["resources"]["statusd"]["transitions"]
    assert [row["new_state"] for row in transitions] == ["starting", "ready", "down", "ready"]


def test_persistence_failure_still_publishes_health_and_never_claims_success(tmp_path: Path):
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    harness = Harness(tmp_path)
    harness.store = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall, writer=refuse)
    harness.observer._store = harness.store
    try:
        harness.cycle(2)
        assert harness.published, "health must stay visible when the disk cannot be written"
        assert harness.observer.state()["persistence"]["state"] == "degraded"
        assert harness.store.document()["revision"] == 0
    finally:
        harness.observer.stop()


# -- the probe bound ---------------------------------------------------------------------


def test_a_probe_that_never_answers_is_bounded_by_the_probe_timeout(tmp_path: Path):
    reset_local_service_traffic()
    harness = Harness(tmp_path, probe_timeout_seconds=0.05)
    harness.arm_pool()
    gate = threading.Event()
    harness.services["watchd"].gate = gate
    try:
        started = time.monotonic()
        cycle = harness.observer.observe_once()
        elapsed = time.monotonic() - started
        assert cycle.probe_outcomes["watchd"] == PROBE_TIMEOUT
        assert cycle.states["watchd"][0] == "starting"  # debounced, not flickered
        assert all(cycle.probe_outcomes[name] == PROBE_OK for name in LOCAL_SERVICE_INVENTORY if name != "watchd")
        # One shared deadline for the whole fan-out, not one per service.
        assert elapsed < 0.05 * 5, elapsed
    finally:
        gate.set()
        harness.observer.stop()
        reset_local_service_traffic()


class VirtualProbePool:
    """The bounded probe pool with the real blocking replaced by the injected clock.

    The observer's own deadline arithmetic is what is under test (`_probe_all` computes
    ``deadline = monotonic() + probe_timeout_seconds`` once, then ``remaining = deadline -
    monotonic()`` per service), so this fake replaces only the part a test cannot own -- real
    waiting -- and leaves that arithmetic running against the injected clock. A producer declared
    slower than the remaining budget consumes the whole remaining budget on the clock and then
    raises `TimeoutError`, which is exactly what an abandoned future costs in wall time.
    """

    def __init__(self, clock: FakeClock, delays: dict[str, float]) -> None:
        self.clock = clock
        self.delays = delays
        self.shutdown_calls = 0

    def submit(self, worker, producer):
        # The observer submits `_probe_worker` with the bound row producer as its argument.
        return VirtualFuture(self.clock, self.delays.get(producer.__self__.name, 0.0), worker, producer)

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        self.shutdown_calls += 1


class VirtualFuture:
    """A future that spends INJECTED time instead of real time, with `Future`'s exact contract."""

    def __init__(self, clock: FakeClock, delay: float, worker, producer) -> None:
        self._clock = clock
        self._delay = float(delay)
        self._worker = worker
        self._producer = producer
        self._value: Any = None

    def exception(self, timeout: float | None = None):
        budget = float("inf") if timeout is None else float(timeout)
        if self._delay > budget:
            # The deadline is spent and the future is abandoned, unfinished.
            self._clock.advance(budget)
            raise TimeoutError
        self._clock.advance(self._delay)
        try:
            self._value = self._worker(self._producer)
        except Exception as error:  # `Future.exception` RETURNS the failure, it does not raise it
            return error
        return None

    def result(self, timeout: float | None = None):
        return self._value


def test_a_service_exit_is_detected_inside_the_composed_two_observe_plus_probe_timeout_bound(tmp_path: Path):
    """The 4.5 s bound itself: `2*BACKEND_HEALTH_OBSERVE_SECONDS + BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS`.

    Its three constituents each already have a test -- the constants
    (`test_observer_budget_constants_match_the_health_contract`), the immediate `down` transition
    (`test_a_verified_exit_transitions_to_down_immediately`), and the per-cycle probe deadline
    (`test_a_probe_that_never_answers_is_bounded_by_the_probe_timeout`) -- and no test ever added
    the three together. The number the health contract actually promises a user is the SUM, and
    changing any one constituent silently moves it while all three parts stay green.

    The worst case is built literally, because the bound only has to hold there: the exit lands
    immediately AFTER a cycle has already read the row, so that cycle cannot see it; the next
    cycle's probe never answers and burns the entire probe deadline, so that service reads
    `unknown` and the debounce correctly swallows it; only the cycle after that reads the exited
    row, where `down` is immediate and is accepted at once.

    Every second below is spent by the product asking for it -- `_await_next_cycle` asking to wait
    `interval_seconds`, and `_probe_all` spending `probe_timeout_seconds` on a probe that never
    answers. The test advances no clock of its own, so this is an arithmetic contract and not a
    measurement of the machine's scheduling luck.
    """

    reset_local_service_traffic()
    clock = FakeClock(500.0)
    wall = FakeClock(1_000_000.0)
    services = {name: FakeService(name) for name in LOCAL_SERVICE_INVENTORY}
    waits: list[float] = []

    def wait(timeout: float) -> bool:
        waits.append(timeout)
        clock.advance(timeout)
        return False

    delays: dict[str, float] = {}
    observer = BackendHealthObserver(
        row_producers=lambda: {name: service.runtime_status for name, service in services.items()},
        store=BackendHealthStore(PORT, state_dir=tmp_path, clock=wall),
        publish=lambda event_type, payload: {"type": event_type},
        monotonic=clock,
        wall_clock=wall,
        wait=wait,
        identity_source=lambda pid: f"proc:{pid}" if pid > 0 else "",
    )
    observer._executor = VirtualProbePool(clock, delays)

    # `_run` is a two-statement loop body, and the sequence driven below is exactly those two
    # statements in exactly that order. Pinned, so a reordered or extended loop cannot leave this
    # bound asserting a cadence the product no longer has.
    run_body = next(
        node for node in ast.walk(ast.parse(OBSERVER_SOURCE.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "_run"
    ).body[0].body
    assert [ast.unparse(node).splitlines()[0] for node in run_body] == ["try:", "self._await_next_cycle()"]

    try:
        # Two cycles to debounce every service up to `ready`; each is one loop iteration.
        for _ in range(2):
            observer.observe_once()
            observer._await_next_cycle()
        observer.observe_once()
        assert observer._accepted["statsd"] == ("ready", "none")

        # t0: the worker exits the instant after the cycle above read its row.
        exited_at = clock.value
        services["statsd"].down("statsd worker exited")
        delays["statsd"] = 10_000.0  # ...and its probe stops answering with it.

        observer._await_next_cycle()
        cycle = observer.observe_once()
        assert cycle.probe_outcomes["statsd"] == PROBE_TIMEOUT
        assert observer._accepted["statsd"][0] == "ready", "one timed-out probe must not flicker the state"

        del delays["statsd"]  # the exited row is now readable; only the exit itself is left to see
        observer._await_next_cycle()
        observer.observe_once()
        detected_at = clock.value
        assert observer._accepted["statsd"] == ("down", "exited")
    finally:
        observer.stop()
        reset_local_service_traffic()

    bound = BACKEND_HEALTH_OBSERVE_SECONDS * 2 + BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS
    assert bound == 4.5
    assert waits == [BACKEND_HEALTH_OBSERVE_SECONDS] * 4
    assert detected_at - exited_at == bound, (detected_at - exited_at, bound)


def test_the_pooled_and_inline_probe_paths_agree(tmp_path: Path):
    reset_local_service_traffic()
    inline = Harness(tmp_path / "inline")
    pooled = Harness(tmp_path / "pooled")
    pooled.arm_pool()
    try:
        for harness in (inline, pooled):
            harness.services["approvald"].down("approvald exited")
        inline_cycle = inline.observer.observe_once()
        pooled_cycle = pooled.observer.observe_once()
        assert dict(inline_cycle.states) == dict(pooled_cycle.states)
        assert dict(inline_cycle.probe_outcomes) == dict(pooled_cycle.probe_outcomes)
    finally:
        inline.observer.stop()
        pooled.observer.stop()
        reset_local_service_traffic()


# -- no starts, no leaks, no demand ------------------------------------------------------


def test_a_full_observation_cycle_starts_zero_demand_scoped_services(harness: Harness):
    harness.cycle(3)
    for service in harness.services.values():
        assert service.calls == 3
        assert service.registry.status_calls == 3
    # RefusingRegistry.ensure_started / acquire_lease would have raised; the assertion above
    # proves the producers ran, so the absence of a failure is a measurement, not a vacuum.


def test_the_observer_module_never_names_a_start_primitive():
    named = named_attributes(OBSERVER_SOURCE.read_text(encoding="utf-8"))
    assert not (START_PRIMITIVE_ATTRIBUTES & named), sorted(START_PRIMITIVE_ATTRIBUTES & named)
    for name, body in function_bodies(CLI_SOURCE, "start_backend_health_observer", "backend_health_label_source").items():
        assert not (START_PRIMITIVE_ATTRIBUTES & named_attributes(body)), name


def test_start_is_idempotent_and_stop_joins_without_leaking_threads(tmp_path: Path):
    reset_local_service_traffic()
    harness = Harness(tmp_path)
    harness.observer._monotonic = time.monotonic
    # The real event wait, so the loop blocks between cycles instead of spinning on a stub.
    harness.observer._wait = harness.observer._wake.wait
    before = {thread.ident for thread in threading.enumerate()}
    try:
        assert harness.observer.start() is True
        assert harness.observer.start() is False
        assert harness.observer.running is True
    finally:
        harness.observer.stop()
        reset_local_service_traffic()
    assert harness.observer.running is False
    assert harness.observer.start() is False, "start-once must stay latched after stop"
    leaked = [
        thread.name
        for thread in threading.enumerate()
        if thread.ident not in before and thread.name.startswith(BACKEND_HEALTH_OBSERVER_THREAD_PREFIX)
    ]
    assert leaked == [], leaked


def test_wake_cuts_the_interval_short(harness: Harness):
    harness.wake_result = True
    harness.observer._await_next_cycle()
    assert harness.waits == [pytest.approx(BACKEND_HEALTH_OBSERVE_SECONDS)]

    harness.waits.clear()
    harness.wake_result = False
    harness.observer._await_next_cycle()
    # Without a wake the loop waits the whole interval and returns when it elapses.
    assert sum(harness.waits) == pytest.approx(BACKEND_HEALTH_OBSERVE_SECONDS)


def test_observation_does_not_depend_on_a_browser_sse_subscriber_or_panel(harness: Harness):
    # Nothing here has a subscriber, an open panel, or an HTTP request. The observer still runs,
    # still transitions, and still publishes -- which is the whole point of the milestone.
    harness.cycle(2)
    harness.services["indexd"].down("indexd exited")
    cycle = harness.cycle()
    assert cycle.published is True
    assert harness.published[-1][0] == "backend_health_changed"


def test_a_failing_cycle_is_recorded_and_does_not_end_the_loop(harness: Harness):
    calls: list[int] = []

    def explode() -> dict[str, Any]:
        calls.append(1)
        raise RuntimeError("producers unavailable")

    def stop_after_one_pass(timeout: float) -> bool:
        harness.observer._stop.set()
        return True

    harness.observer._row_producers = explode
    harness.observer._wait = stop_after_one_pass
    harness.observer._run()

    assert calls == [1], "the loop body must have run and raised"
    state = harness.observer.state()
    # Recorded against the unit, not swallowed, and the loop returned normally rather than dying.
    assert state["cycle_failures"] == 1
    assert state["last_cycle_error"] == "RuntimeError"
    assert harness.published == []


# -- probe traffic attribution -----------------------------------------------------------


def test_observer_probes_never_enter_the_user_work_aggregate(harness: Harness):
    ledger = local_service_traffic_ledger("jobd")
    ledger.record_completion(LOCAL_SERVICE_TRAFFIC_WORK, client_elapsed_ms=12.0)
    work_before = local_service_traffic_snapshot()["jobd"][LOCAL_SERVICE_TRAFFIC_WORK]["accepted"]

    harness.cycle(2)

    snapshot = local_service_traffic_snapshot()["jobd"]
    assert snapshot[LOCAL_SERVICE_TRAFFIC_WORK]["accepted"] == work_before
    # Every producer saw itself classified as probe traffic, in every cycle, for every service.
    for service in harness.services.values():
        assert service.traffic_classes == [LOCAL_SERVICE_TRAFFIC_PROBE] * 2, service.name


def test_probe_scope_is_re_entered_inside_the_pool_thread(tmp_path: Path):
    reset_local_service_traffic()
    harness = Harness(tmp_path)
    harness.arm_pool()
    try:
        harness.observer.observe_once()
        for service in harness.services.values():
            # A context variable is per-thread: without the worker re-entering the scope, this
            # would read `work` and the observer's own probes would poison the user aggregate.
            assert service.traffic_classes == [LOCAL_SERVICE_TRAFFIC_PROBE], service.name
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


# -- the recorded sampler decision -------------------------------------------------------


def test_observer_and_service_load_share_one_row_producer_map(monkeypatch):
    """Both periodic consumers reach the same producer map, so neither can hold a stale copy.

    Proven by OBJECT IDENTITY at runtime, not by matching text in app.py and cli.py. One
    sentinel map is installed on a real app instance and every consumer that must reach it is
    then driven for real: the collector the snapshot owner builds, the stats sampler, the HTTP
    projection, and the observer `cli.start_backend_health_observer` constructs. A consumer
    holding a private copy of the six ids would be handed a different object and would return
    rows this map never produced, so it cannot pass by spelling the same string twice.
    """

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.backend_health_store = None

    def row_producers() -> dict[str, Any]:
        # The one map object under test. Empty on purpose: identity is the whole claim, and the
        # rows below are handed out by the sentinel snapshot instead.
        return {}

    webapp.local_services_row_producers = row_producers

    handed: list[Any] = []
    payload_calls: list[str] = []
    rows = tuple(
        SimpleNamespace(service=name, running=True, cpu_percent=float(index), rss_bytes=1024 * (index + 1))
        for index, name in enumerate(LOCAL_SERVICE_INVENTORY)
    )

    class SentinelSnapshot:
        """The only snapshot in this test, so any consumer that renders one rendered THIS one."""

        def __init__(self) -> None:
            self.rows = rows

        def payload(self, render_row: Any, *, health: Any) -> dict[str, Any]:
            payload_calls.append("payload")
            return {"services": ["sentinel"]}

    class RecordingCollector:
        def __init__(self, producers: Any, *, ledger: Any = None, recovery_events: Any = None) -> None:
            handed.append(producers)

        def collect(self, *, include_diagnostics: bool = True) -> SentinelSnapshot:
            return SentinelSnapshot()

    monkeypatch.setattr(local_service_projection, "LocalServicesCollector", RecordingCollector)

    # 1. The snapshot owner hands the collector THIS map object, not a rebuilt copy.
    snapshot = webapp.local_services_snapshot()
    assert isinstance(snapshot, SentinelSnapshot)
    assert handed == [row_producers] and handed[0] is row_producers

    # 2. The periodic stats sampler reads that one snapshot's typed rows and nothing else.
    facts = webapp.collect_current_stats_service_load(
        SimpleNamespace(
            epoch_id="epoch",
            epoch_started_at=1.0,
            scheduled_at=2.0,
            # Read from the family spec: the collector rejects any other cadence, and a literal
            # here would be a second copy of a number this family already owns.
            cadence_seconds=float(stats_current_collectors.FAMILY_BY_NAME["service_load"].active_cadence_seconds),
            owner_generation=1,
        )
    )
    assert [observation.source_id for observation in facts.observations] == list(LOCAL_SERVICE_INVENTORY)
    assert [observation.payload["cpu_percent"] for observation in facts.observations] == [
        float(index) for index in range(len(LOCAL_SERVICE_INVENTORY))
    ]
    assert handed == [row_producers, row_producers]

    # 3. The HTTP projection renders that same snapshot rather than collecting its own rows.
    assert webapp.runtime_local_services() == {"services": ["sentinel"]}
    assert payload_calls == ["payload"]
    assert handed == [row_producers] * 3 and all(producers is row_producers for producers in handed)

    # 4. The observer is constructed with the SAME map object, by identity, through the real
    #    `cli.start_backend_health_observer` -- the seam where a second copy would be introduced.
    constructed: dict[str, Any] = {}

    class RecordingObserver:
        def __init__(self, **kwargs: Any) -> None:
            constructed.update(kwargs)

        def start(self) -> None:
            constructed["started"] = True

        # The real observer is also the app's liveness reader; the double has to model that or
        # it stops proving anything about the seam it exists for.
        def liveness(self) -> dict[str, Any]:
            return {}

    monkeypatch.setenv(cli_module.BACKEND_HEALTH_OBSERVE_SECONDS_ENV, str(BACKEND_HEALTH_OBSERVE_SECONDS))
    monkeypatch.setattr(cli_module, "BackendHealthObserver", RecordingObserver)
    monkeypatch.setattr(cli_module, "BackendHealthStore", lambda port, on_diagnostic=None: SimpleNamespace(port=port))
    webapp.client_events = SimpleNamespace(publish=lambda event_type, payload: None)
    webapp.local_services_recovery_control = lambda: None
    webapp.system_status_service = lambda row: {"label": str(row.get("service") or "")}

    assert cli_module.start_backend_health_observer(PORT, webapp) is not None
    assert constructed["row_producers"] is row_producers
    assert constructed["started"] is True

    # 5. And there is exactly ONE construction of the collector in app.py, so the identity chain
    #    above is the only path to a row. By AST, so a mention in a comment or a docstring
    #    cannot satisfy it and a second real construction cannot hide behind one.
    constructions = [
        node
        for node in ast.walk(ast.parse(APP_SOURCE.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "local_service_projection.LocalServicesCollector"
    ]
    assert len(constructions) == 1, [node.lineno for node in constructions]


def test_observer_declares_no_second_inventory_or_row_shape():
    # Code only. The module docstring records the sampler decision and names those fields on
    # purpose; what must not exist is a second place that COMPUTES them.
    source = module_code(OBSERVER_SOURCE)
    for name in LOCAL_SERVICE_INVENTORY:
        assert f'"{name}"' not in source, f"observer.py hardcodes the service id {name!r}"
    # No second inventory literal, no second row derivation, no second label table.
    assert "LOCAL_SERVICE_INVENTORY" in source
    assert "LocalServicesCollector(" in source
    assert "LocalServiceRow(" not in source
    assert "uptime_seconds" not in source
    assert "cpu_percent" not in source
    assert "Quick Open index" not in source and "File watching" not in source


def test_observer_budget_constants_match_the_health_contract():
    assert BACKEND_HEALTH_OBSERVE_SECONDS == 2.0
    assert BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS == 0.5
    assert BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS == 2
    assert BACKEND_HEALTH_IMMEDIATE_STATES == frozenset({"down", "upgrade_required"})


def test_cli_starts_the_observer_after_the_port_lease_and_stops_it_before_clients_close():
    """The lifecycle bracket, and the M7 reorder inside it.

    `lease < start < stop < auto_approve < server_close` is unchanged and still the point: the
    retained history file is port-scoped, so the lease is what makes the observer a single
    writer, and the observer must be stopped before the backend clients it probes are torn down.

    The `start < owner` half is GONE, and deliberately, not because it became inconvenient.
    Arming before `start_background_owner()` returned meant the first cycle raced this process's
    own statsd pin and published a false `down` for statsd at every boot -- 4.025-4.033s on four
    isolated starts, an 8ms spread. The order is reversed now, and that reversal is frozen below.

    What the old assertion was really defending -- "the observer is not gated on winning the
    background-owner election" -- is not a source-order property at all, and source order was
    only ever a proxy for it. It is proven directly and behaviourally by
    `tests/test_app.py::test_the_health_observer_is_armed_after_the_election_and_never_depends_on_winning`,
    which drives `cli.main()` through BOTH election outcomes and asserts the observer is armed
    and stopped either way. That test is asserted to exist here, so the proof cannot be deleted
    while this file goes on claiming it. The structural half of the same property -- the arming
    is not nested under a conditional, and the election's outcome is discarded rather than
    branched on -- is checked here by AST, where source order cannot fake it.
    """
    source = CLI_SOURCE.read_text(encoding="utf-8")
    anchors = (
        "lease = acquire_server_port_lease(args.port)",
        "backend_health = start_backend_health_observer(args.port, app)",
        "backend_health.stop()",
        "app.stop_auto_approve_all()",
        "server.server_close()",
        "app.start_background_owner(",
    )
    # `str.index` returns the FIRST hit, so an ordering built on it is only as strong as the
    # anchors being unique. A second occurrence -- one comment, one docstring line, one `pass  #
    # backend_health.stop()` left behind by a revert -- and this test would go on passing while
    # the call it names had moved or gone. Measured while re-freezing: each anchor occurs once.
    duplicated = {anchor: source.count(anchor) for anchor in anchors if source.count(anchor) != 1}
    assert duplicated == {}, duplicated
    lease = source.index(anchors[0])
    start = source.index(anchors[1])
    stop = source.index(anchors[2])
    auto_approve = source.index(anchors[3])
    server_close = source.index(anchors[4])
    assert lease < start < stop < auto_approve < server_close
    # The measured M7 reorder: the election is decided before the first cycle can read a row.
    owner = source.index(anchors[5])
    assert owner < start, "arming before the election republishes the measured 4.03s false `down`"

    election, arming = _cli_main_statements(
        "app.start_background_owner", "start_backend_health_observer"
    )
    # Not gated: neither statement is nested under any conditional, and both sit in the SAME
    # block, so no branch can reach one without the other.
    assert election.enclosing_conditionals == [], election.enclosing_conditionals
    assert arming.enclosing_conditionals == [], arming.enclosing_conditionals
    assert election.block is arming.block
    # The election's outcome is discarded at the call site, so nothing in `main` can branch on
    # it. A `won = app.start_background_owner(...)` would be the first step toward gating.
    assert isinstance(election.statement, ast.Expr), ast.dump(election.statement)

# -- helpers -----------------------------------------------------------------------------


class _MainStatement:
    """One top-level-ish statement of `cli.main()`, with the block and branches around it."""

    def __init__(self, statement: ast.stmt, block: list[ast.stmt], enclosing_conditionals: list[str]) -> None:
        self.statement = statement
        self.block = block
        self.enclosing_conditionals = enclosing_conditionals


def _cli_main_statements(*needles: str) -> tuple[_MainStatement, ...]:
    """Locate statements in `cli.main()` by source text, with their enclosing block and ifs.

    Source order is a proxy; this is not. `enclosing_conditionals` is every `if`/`while` test
    between `main`'s body and the statement, so "the observer is armed unconditionally" is read
    off the tree rather than inferred from two string offsets.
    """

    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    )
    found: dict[str, _MainStatement] = {}

    def walk(body: list[ast.stmt], conditionals: list[str]) -> None:
        for statement in body:
            # Descend FIRST, so the INNERMOST statement containing the needle wins. Matching on
            # the way down instead binds every needle to the outermost `try:` of `main`, whose
            # unparsed source trivially contains all of them -- a hit that says nothing.
            deeper = conditionals + [ast.unparse(statement.test)] if isinstance(statement, (ast.If, ast.While)) else conditionals
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(statement, field, None)
                if isinstance(nested, list):
                    walk([child for child in nested if isinstance(child, ast.stmt)], deeper)
            for handler in getattr(statement, "handlers", []):
                walk(handler.body, conditionals)
            text = ast.unparse(statement)
            for needle in needles:
                if needle in text and needle not in found:
                    found[needle] = _MainStatement(statement, body, list(conditionals))

    walk(main.body, [])
    missing = [needle for needle in needles if needle not in found]
    assert not missing, f"not found in cli.main(): {missing}"
    return tuple(found[needle] for needle in needles)


def function_bodies(path: Path, *names: str) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = set(names)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            found[node.name] = ast.unparse(node)
    assert set(found) == wanted, f"missing {sorted(wanted - set(found))} in {path.name}"
    return found


def module_code(path: Path) -> str:
    """Return one module's source with every comment and docstring removed."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        head = body[0]
        if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant) and isinstance(head.value.value, str):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def named_attributes(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}

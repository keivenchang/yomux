# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M7 of DOIT.p0.daemon-monitor: bounded, non-destructive recovery of a down service.

Three properties are proven here, and the negative controls for each are recorded in the
milestone report:

  * ONE ATTEMPT PER BACKOFF BOUNDARY. The ladder is 1, 2, 4, then at most 8 seconds measured
    from the start of the outage, and the observation cadence is 2.0s -- so a ladder that
    retried per observation instead of per boundary would fire four times inside the 8s
    boundary. The tests assert the attempt TIMES, not just the attempt count, because a
    per-cycle ladder reaches the same total sooner and an attempt-count assertion would pass.

  * ZERO DESTRUCTIVE OPERATIONS, EVER. `TrapControl` defines exactly one method and records
    then raises on every other attribute, so a stop/restart/signal/unlink/reclaim/adopt call
    is a recorded fact rather than a code-review opinion. It is paired with an AST assertion
    over `_issue_retry`, the one place a control object is touched.

  * A SERVICE WHOSE ABSENCE IS EXPECTED IS NEVER RETRIED. statusd, approvald, indexd and
    watchd are demand-started and rest absent; jobd carries `scheduler_not_owned` when this
    process lost the background-owner election. Retrying either class would start daemons on
    an idle machine or fight the election. Both are fenced twice, by the row's own
    declarations and by the reduced state, and `test_the_row_fence_holds_on_its_own` proves
    the second fence still holds when the first is bypassed.

Everything is driven by the observer's injected monotonic clock. No test sleeps.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from test_backend_health_observer import FakeService
from test_backend_health_observer import Harness

from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVE_SECONDS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_RECOVERY_ARMING_SECONDS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
from yolomux_lib.backend_health.observer import BLOCKED_CONTROL_ERROR
from yolomux_lib.backend_health.observer import BLOCKED_DEMAND_STARTED_ABSENT
from yolomux_lib.backend_health.observer import BLOCKED_NO_CONTROL
from yolomux_lib.backend_health.observer import BLOCKED_OBSERVER_ARMING
from yolomux_lib.backend_health.observer import RECOVERY_ELIGIBLE_REASONS
from yolomux_lib.backend_health.observer import RECOVERY_EXHAUSTED
from yolomux_lib.backend_health.observer import RECOVERY_NONE
from yolomux_lib.backend_health.observer import RECOVERY_NOT_ATTEMPTED
from yolomux_lib.backend_health.observer import RECOVERY_RECOVERED
from yolomux_lib.backend_health.observer import RECOVERY_SCHEDULED
from yolomux_lib.backend_health.observer import REASON_IDENTITY_MISMATCH
from yolomux_lib.backend_health.observer import REASON_REVISION_MISMATCH
from yolomux_lib.backend_health.observer import ServiceRecoveryPlanner
from yolomux_lib.backend_health.observer import observed_health
from yolomux_lib.backend_health.observer import recovery_blocked_cause
from yolomux_lib.backend_health.observer import recovery_blocked_token
from yolomux_lib.backend_health.observer import recovery_row_fence
from yolomux_lib.backend_health.store import BACKEND_HEALTH_REASON_CODES
from yolomux_lib.backend_health.store import TRANSITION_ROW_FIELDS
from yolomux_lib.local_service_projection import LOCAL_SERVICE_INVENTORY
from yolomux_lib.local_services.rpc import reset_local_service_traffic
from yolomux_lib.infra.jobd import JobClient
from yolomux_lib.infra.jobd import JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE
from yolomux_lib import app as app_module


REPO_ROOT = Path(__file__).resolve().parents[1]
OBSERVER_SOURCE = REPO_ROOT / "yolomux_lib" / "backend_health" / "observer.py"

# The bounded-token alphabet the retained store accepts (`store.py`). A recovery outcome that
# does not match it is replaced by `recovery_invalid` and loses its cause.
STORE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

# Every operation that could touch a process, a socket, or another owner's state. None of these
# may be reachable from the recovery path, and `TrapControl` proves it at runtime.
DESTRUCTIVE_OPERATIONS = (
    "stop",
    "restart",
    "kill",
    "terminate",
    "signal",
    "send_signal",
    "shutdown",
    "close",
    "unlink",
    "remove",
    "rmtree",
    "reclaim",
    "adopt",
    "seal_starts",
    "ensure_started",
    "acquire_lease",
    "release_lease",
    "retire",
    "reset",
)


class TrapControl:
    """A service control that offers ``retry`` and treats every other attribute as a defect.

    ``forbidden`` is recorded BEFORE raising on purpose: the planner catches a raising control
    at its supervisor boundary, so an assertion raised from here would be swallowed into
    ``retry_blocked_control_error`` and the test would pass while the observer was calling
    ``stop()``. The recorded list is what the assertions read.
    """

    def __init__(self, *, clock: Any = None, result: bool = True, error: BaseException | None = None) -> None:
        self.calls: list[str] = []
        self.times: list[float] = []
        self.forbidden: list[str] = []
        self._clock = clock
        self._result = result
        self._error = error

    def retry(self, resource: str) -> bool:
        self.calls.append(resource)
        self.times.append(float(self._clock()) if self._clock is not None else 0.0)
        if self._error is not None:
            raise self._error
        return self._result

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define, i.e. everything but `retry`.
        self.__dict__.setdefault("forbidden", []).append(name)
        raise AssertionError(f"recovery touched a non-retry control attribute: {name}")


class RecoveryHarness(Harness):
    """`Harness` plus a monotonic tick, so backoff boundaries can be crossed without sleeping."""

    def __init__(self, tmp_path: Path, control: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("recovery_arming_seconds", 0.0)
        super().__init__(tmp_path, recovery_control=control, **kwargs)
        self.control = control

    def tick(self, count: int = 1, seconds: float = BACKEND_HEALTH_OBSERVE_SECONDS):
        """Advance BOTH clocks by one observation interval and observe."""

        result = None
        for _ in range(count):
            self.wall.advance(seconds)
            self.monotonic.advance(seconds)
            result = self.observer.observe_once()
        return result

    def outcome(self, resource: str) -> str:
        document = self.store.document()
        current = ((document.get("resources") or {}).get(resource) or {}).get("current") or {}
        return str(current.get("recovery_outcome") or "")


@pytest.fixture
def recovery(tmp_path: Path):
    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(tmp_path, control)
    control._clock = harness.monotonic
    yield harness
    harness.observer.stop()
    reset_local_service_traffic()


# -- the ladder --------------------------------------------------------------------------


def test_the_documented_ladder_is_one_two_four_then_at_most_eight():
    assert BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS == (1.0, 2.0, 4.0, 8.0)
    assert BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS == 4
    assert max(BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS) == 8.0
    # Only these three reduced causes may be retried. Everything else must name its own block.
    assert RECOVERY_ELIGIBLE_REASONS == frozenset({"service_absent", "exited", "probe_failed"})


def test_one_retry_per_backoff_boundary_and_never_one_per_observation(recovery: RecoveryHarness):
    """The attempt TIMES, not the attempt count: a per-cycle ladder reaches four attempts too.

    Cadence is 2.0s and the outage starts at t0, so boundaries fall at t0+1, +3, +7 and +15;
    the first cycle at or after each boundary is the one allowed attempt. Between t0+8 and
    t0+16 four observations run and exactly one retry is issued.
    """

    recovery.tick()
    control = recovery.control
    assert control.calls == []

    recovery.services["statsd"].down("statsd worker exited")
    recovery.tick()  # the outage is observed here; `down` is immediate, not debounced
    onset = recovery.monotonic.value
    assert recovery.observer._accepted["statsd"] == ("down", "exited")
    assert control.calls == [], "no retry may be issued in the same cycle the outage is seen"

    for _ in range(9):  # 18 seconds of 2.0s observations
        recovery.tick()

    assert control.calls == ["statsd"] * BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS, control.calls
    offsets = [round(moment - onset, 3) for moment in control.times]
    # 1s boundary -> first cycle at +2; 2s -> +4; 4s -> +8; 8s -> +16.
    assert offsets == [2.0, 4.0, 8.0, 16.0], offsets
    gaps = [round(later - earlier, 3) for earlier, later in zip(offsets, offsets[1:])]
    assert gaps == [2.0, 4.0, 8.0], gaps
    assert min(gaps) >= BACKEND_HEALTH_OBSERVE_SECONDS


def test_the_ladder_is_bounded_and_then_says_so(recovery: RecoveryHarness):
    recovery.tick()
    recovery.services["jobd"].down("jobd broker exited")
    recovery.tick(12)
    assert recovery.control.calls == ["jobd"] * BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
    assert recovery.observer.recovery.status(recovery.monotonic.value)["resources"]["jobd"]["outcome"] == (
        RECOVERY_EXHAUSTED
    )
    assert recovery.outcome("jobd") == RECOVERY_EXHAUSTED
    before = len(recovery.control.calls)
    recovery.tick(20)
    assert len(recovery.control.calls) == before, "an exhausted ladder must not resume by itself"


def test_a_recovered_service_reports_recovered_and_gets_a_fresh_ladder(recovery: RecoveryHarness):
    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.tick(3)
    assert recovery.control.calls == ["statsd", "statsd"]
    assert recovery.outcome("statsd") == RECOVERY_SCHEDULED

    recovery.services["statsd"].up()
    recovery.tick(2)  # recovery to `ready` is debounced, so it takes two observations
    assert recovery.observer._accepted["statsd"] == ("ready", "none")
    assert recovery.outcome("statsd") == RECOVERY_RECOVERED

    recovery.services["statsd"].down("statsd worker exited again")
    recovery.tick(12)
    assert recovery.control.calls.count("statsd") == 2 + BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS


def test_a_healthy_service_is_never_retried_and_reports_none(recovery: RecoveryHarness):
    recovery.tick(6)
    assert recovery.control.calls == []
    assert recovery.outcome("statsd") == RECOVERY_NONE


# -- zero destructive operations ----------------------------------------------------------


def test_zero_destructive_operations_reach_the_control_object(recovery: RecoveryHarness):
    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.services["jobd"].down("jobd broker exited")
    recovery.tick(12)
    recovery.services["statsd"].up()
    recovery.tick(4)

    assert recovery.control.forbidden == [], recovery.control.forbidden
    assert set(recovery.control.calls) == {"statsd", "jobd"}


def test_the_retry_choke_point_names_only_retry():
    """The one function allowed to touch a control object names exactly one attribute."""

    body = _function_source(OBSERVER_SOURCE, "_issue_retry")
    assert _named_attributes(body) == {"retry"}, _named_attributes(body)
    for operation in DESTRUCTIVE_OPERATIONS:
        assert f".{operation}(" not in body, operation


def test_the_recovery_path_names_no_destructive_operation():
    """No destructive operation is named anywhere the planner can reach."""

    planner = _class_source(OBSERVER_SOURCE, "ServiceRecoveryPlanner")
    named = _named_attributes(planner)
    forbidden = {operation for operation in DESTRUCTIVE_OPERATIONS} & named
    assert forbidden == set(), sorted(forbidden)
    # The planner reaches a control object through the choke point and through nothing else.
    assert "_issue_retry" in planner
    assert "self._control." not in planner


# -- a service whose absence is expected ---------------------------------------------------


@pytest.mark.parametrize("service", ["statusd", "approvald", "indexd", "watchd"])
def test_a_demand_started_absent_service_is_never_retried(tmp_path: Path, service: str):
    """Absence with an expected reason is not down, and recovery says why rather than acting."""

    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(tmp_path, control)
    try:
        harness.services[service] = FakeService(service, pid=0, demand_started=True)
        harness.services[service].absent()
        harness.tick(20)
        assert control.calls == [], control.calls
        assert harness.observer._accepted[service] == ("starting", "service_absent")
        assert harness.outcome(service) == recovery_blocked_token(BLOCKED_DEMAND_STARTED_ABSENT)
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_jobd_without_the_scheduler_lease_is_never_retried(tmp_path: Path):
    """`scheduler_not_owned` is the election, not an outage. Retrying it would fight the winner."""


    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(tmp_path, control)
    try:
        client = JobClient(tmp_path / "jobd.sock")
        assert client.holds_scheduler_lease is False
        row = client.runtime_status()
        assert row["absence_expected_reason"] == JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE
        assert row["pid"] == 0

        harness.services["jobd"].row = dict(row)
        harness.tick(20)
        assert control.calls == [], control.calls
        assert harness.outcome("jobd") == "retry_blocked_scheduler_not_owned"
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_the_row_fence_holds_on_its_own():
    """The row's own declarations block a retry even if the reduced state were wrong.

    This is the second fence. `recovery_blocked_cause` reads the reduced state; this one reads
    what the row declared. Both have to fail before a resting daemon can be started.
    """

    assert recovery_row_fence({"pid": 0, "demand_started": True}) == BLOCKED_DEMAND_STARTED_ABSENT
    assert recovery_row_fence({"pid": 0, "absence_expected_reason": "scheduler_not_owned"}) == "scheduler_not_owned"
    # A named owner that is not engaging the service blocks even when a failure was recorded:
    # who owns scheduling is not a question a restart can answer.
    assert recovery_row_fence(
        {"pid": 0, "absence_expected_reason": "scheduler_not_owned", "last_failure": "broker exited"}
    ) == "scheduler_not_owned"
    # A demand-started service that ran and exited is verified down, not legitimately absent.
    assert recovery_row_fence({"pid": 0, "demand_started": True, "last_failure": "worker exited"}) == ""
    # An unreadable excuse is not an excuse, and it is not a licence to act either.
    assert recovery_row_fence({"pid": 0, "absence_expected_reason": "NOT A TOKEN"}) == "absence_reason_invalid"
    # statsd declares neither, so nothing here excuses its absence.
    assert recovery_row_fence({"pid": 0}) == ""
    # A running service is not absent; the excuses do not apply to it at all.
    assert recovery_row_fence({"pid": 4242, "demand_started": True}) == ""


def test_a_full_recovery_cycle_starts_zero_services_through_any_other_path(recovery: RecoveryHarness):
    """`RefusingRegistry` raises on `ensure_started`/`acquire_lease` from any row producer."""

    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.tick(12)
    for name, service in recovery.services.items():
        assert service.registry.status_calls >= 12, name
    # The only start-capable call in the whole run went through the control, for the one
    # verified-down service.
    assert set(recovery.control.calls) == {"statsd"}


# -- blocked causes stay distinct ----------------------------------------------------------


def test_every_blocked_cause_keeps_its_own_token():
    causes = {
        "upgrade_required": recovery_blocked_cause("upgrade_required", "upgrade_required"),
        "terminal_failure": recovery_blocked_cause("down", "terminal_failure"),
        "identity_mismatch": recovery_blocked_cause("down", REASON_IDENTITY_MISMATCH),
        "revision_mismatch": recovery_blocked_cause("down", REASON_REVISION_MISMATCH),
        "demand_started_absent": recovery_blocked_cause("starting", "service_absent"),
        "scheduler_not_owned": recovery_blocked_cause("starting", "scheduler_not_owned"),
    }
    assert causes == {
        "upgrade_required": "upgrade_required",
        "terminal_failure": "terminal_failure",
        "identity_mismatch": "identity_mismatch",
        "revision_mismatch": "revision_mismatch",
        "demand_started_absent": BLOCKED_DEMAND_STARTED_ABSENT,
        "scheduler_not_owned": "scheduler_not_owned",
    }
    tokens = [recovery_blocked_token(cause) for cause in causes.values()]
    tokens += [
        recovery_blocked_token(BLOCKED_NO_CONTROL),
        recovery_blocked_token(BLOCKED_OBSERVER_ARMING),
        recovery_blocked_token(BLOCKED_CONTROL_ERROR),
    ]
    assert len(set(tokens)) == len(tokens), tokens
    for token in tokens:
        assert STORE_TOKEN.match(token), token
        assert token != "retry_blocked", "a generic blocked string is the collapse the contract forbids"
        assert token.startswith("retry_blocked_") and len(token) > len("retry_blocked_")


def test_a_fenced_transport_failure_keeps_its_typed_reason_instead_of_probe_failed():
    """identity and revision mismatch are fences, and a fence a restart cannot clear."""

    assert observed_health({"pid": 0, "transport_reason": REASON_IDENTITY_MISMATCH}) == (
        "down",
        REASON_IDENTITY_MISMATCH,
    )
    assert observed_health({"pid": 100, "transport_reason": REASON_REVISION_MISMATCH}) == (
        "degraded",
        REASON_REVISION_MISMATCH,
    )
    # Any other transport failure stays the generic probe failure it already was.
    assert observed_health({"pid": 0, "transport_reason": "connection refused"}) == ("down", "probe_failed")
    assert {REASON_IDENTITY_MISMATCH, REASON_REVISION_MISMATCH} <= BACKEND_HEALTH_REASON_CODES


@pytest.mark.parametrize(
    "state,reason,expected_cause",
    [
        ("upgrade_required", "upgrade_required", "upgrade_required"),
        ("down", "terminal_failure", "terminal_failure"),
        ("down", REASON_IDENTITY_MISMATCH, REASON_IDENTITY_MISMATCH),
        ("down", REASON_REVISION_MISMATCH, REASON_REVISION_MISMATCH),
        ("down", "absence_contract_conflict", "absence_contract_conflict"),
        ("down", "absence_reason_invalid", "absence_reason_invalid"),
    ],
)
def test_a_fenced_service_performs_zero_mutations_and_names_its_cause(
    tmp_path: Path,
    state: str,
    reason: str,
    expected_cause: str,
):
    reset_local_service_traffic()
    control = TrapControl()
    planner = ServiceRecoveryPlanner(control=control, arming_seconds=0.0)
    decisions = [planner.decide("statsd", state, reason, now=float(step)) for step in range(0, 40, 2)]
    reset_local_service_traffic()

    assert control.calls == []
    assert control.forbidden == []
    assert {decision.outcome for decision in decisions} == {recovery_blocked_token(expected_cause)}
    assert {decision.blocked_cause for decision in decisions} == {expected_cause}
    assert not any(decision.attempted for decision in decisions)


def test_no_reduced_reason_code_can_become_retryable_by_accident():
    """Every documented reason code is either eligible, blocked, or explicitly not attempted."""

    control = TrapControl()
    planner = ServiceRecoveryPlanner(control=control, arming_seconds=0.0)
    retried = set()
    for reason in sorted(BACKEND_HEALTH_REASON_CODES):
        for state in ("down", "starting", "degraded", "unknown", "backoff", "upgrade_required"):
            decision = planner.decide(f"probe-{state}-{reason}", state, reason, now=1000.0)
            if decision.attempted:
                retried.add((state, reason))
    assert retried == set(), sorted(retried)
    # Only a verified-down service on the allowlist may ever be attempted, and it takes a
    # boundary to get there -- which is why nothing above fired on its first decision.
    fresh = ServiceRecoveryPlanner(control=TrapControl(), arming_seconds=0.0)
    assert fresh.decide("statsd", "down", "exited", now=0.0).attempted is False
    assert fresh.decide("statsd", "down", "exited", now=1.0).attempted is True


# -- the startup flash ---------------------------------------------------------------------


def test_the_boot_flash_publishes_observer_arming_instead_of_a_retry_storm(tmp_path: Path):
    """statsd reads `down` at every boot; the arming fence, not the 1s boundary, covers it.

    Measured on this branch with three isolated boots (see the milestone report): statsd is
    verified-down from the observer's first cycle and reaches `ready` only after the
    background-owner election and the statsd start complete. The first ladder boundary is 1.0s
    and the observation cadence is 2.0s, so the first attempt would land ~2s into every boot --
    inside the flash, racing the supervisor that is already starting statsd. The fence is
    therefore a separate arming window, and the flash stays visible in history as
    `retry_blocked_observer_arming` rather than being silently swallowed.
    """

    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(
        tmp_path,
        control,
        recovery_arming_seconds=BACKEND_HEALTH_RECOVERY_ARMING_SECONDS,
    )
    try:
        harness.services["statsd"].down("statsd has not been leased yet")
        cycles = int(BACKEND_HEALTH_RECOVERY_ARMING_SECONDS / BACKEND_HEALTH_OBSERVE_SECONDS)
        harness.tick(cycles)
        assert control.calls == [], "a boot flash must not become a retry storm"
        assert harness.outcome("statsd") == recovery_blocked_token(BLOCKED_OBSERVER_ARMING)

        # A service that is STILL down when the fence lifts has a real outage, and the boundary
        # it missed is not re-armed: it is retried on the next observation.
        harness.tick()
        assert control.calls == ["statsd"], control.calls
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_the_statsd_pin_pending_excuse_is_the_first_fence_over_the_boot_flash(tmp_path: Path):
    """The measured flash reduces to `starting`, and recovery blocks on the row's own excuse.

    Two independent fences, proven separately: this one is the excuse the pin owner publishes
    (`app.statsd_pin_pending`), which recovery blocks on unconditionally; the arming window in
    the test above is the residual cover for the interval before that excuse exists.
    """


    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(tmp_path, control, recovery_arming_seconds=0.0)
    try:
        booting = harness.services["statsd"]
        booting.row["pid"] = 0
        booting.row["last_failure"] = ""
        booting.row["absence_expected_reason"] = app_module.STATSD_ABSENT_WHILE_PIN_PENDING
        booting.row["demand_started"] = False

        assert recovery_row_fence(booting.row) == app_module.STATSD_ABSENT_WHILE_PIN_PENDING
        harness.tick(20)  # 40 seconds, far past every ladder boundary, with NO arming window
        assert control.calls == [], control.calls
        assert harness.observer._accepted["statsd"] == ("starting", app_module.STATSD_ABSENT_WHILE_PIN_PENDING)
        assert harness.outcome("statsd") == recovery_blocked_token(app_module.STATSD_ABSENT_WHILE_PIN_PENDING)

        # And when the pin lands, statsd is ready and recovery still did nothing.
        booting.up()
        booting.row["absence_expected_reason"] = ""
        harness.tick(3)
        assert control.calls == []
        assert harness.outcome("statsd") == RECOVERY_NONE
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_the_boot_flash_ends_before_the_fence_lifts_and_nothing_is_retried(tmp_path: Path):
    """The real boot shape: statsd is down, the election completes, statsd comes up. Zero retries."""

    reset_local_service_traffic()
    control = TrapControl()
    harness = RecoveryHarness(
        tmp_path,
        control,
        recovery_arming_seconds=BACKEND_HEALTH_RECOVERY_ARMING_SECONDS,
    )
    try:
        harness.services["statsd"].down("statsd has not been leased yet")
        harness.tick(4)  # 8 seconds of flash, longer than every boot measured
        harness.services["statsd"].up()
        harness.tick(30)
        assert control.calls == [], control.calls
        assert harness.observer._accepted["statsd"] == ("ready", "none")
        assert harness.outcome("statsd") == RECOVERY_NONE
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


# -- what the retained store and the observer report ---------------------------------------


def test_the_recovery_outcome_reaches_the_retained_row_and_survives_the_store(recovery: RecoveryHarness):
    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.tick(12)
    recovery.services["statsd"].up()
    recovery.tick(2)

    document = recovery.store.document()
    resource = document["resources"]["statsd"]
    outcomes = [row["recovery_outcome"] for row in resource["transitions"]]
    assert set(TRANSITION_ROW_FIELDS) == set(resource["transitions"][0])
    for outcome in outcomes:
        assert STORE_TOKEN.match(outcome), outcome
        assert outcome != "recovery_invalid", outcomes
    # The retained history names what recovery did at each state change, not a generic string.
    assert outcomes[0] == RECOVERY_NONE  # starting -> ready, nothing to recover
    assert RECOVERY_RECOVERED in outcomes, outcomes
    assert resource["current"]["recovery_outcome"] == RECOVERY_RECOVERED


def test_every_outcome_the_observer_can_publish_is_a_bounded_store_token(recovery: RecoveryHarness):
    seen: set[str] = set()
    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.services["watchd"].absent()
    for _ in range(12):
        cycle = recovery.tick()
        seen.update(cycle.recovery.values())
    recovery.services["statsd"].up()
    for _ in range(3):
        cycle = recovery.tick()
        seen.update(cycle.recovery.values())

    assert {RECOVERY_NONE, RECOVERY_NOT_ATTEMPTED, RECOVERY_SCHEDULED, RECOVERY_RECOVERED} <= seen, sorted(seen)
    for token in seen:
        assert STORE_TOKEN.match(token), token


def test_an_unwired_process_says_no_control_rather_than_none(tmp_path: Path):
    """No control means no recovery -- and saying so, because `none` would read as healthy."""

    reset_local_service_traffic()
    harness = RecoveryHarness(tmp_path, control=None)
    try:
        harness.tick()
        harness.services["statsd"].down("statsd worker exited")
        harness.tick(12)
        assert harness.observer.recovery.enabled is False
        assert harness.outcome("statsd") == recovery_blocked_token(BLOCKED_NO_CONTROL)
        assert harness.observer.recovery.status(harness.monotonic.value)["attempts_total"] == 0
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_a_raising_control_consumes_its_boundary_and_is_recorded(tmp_path: Path):
    """A control that raises has been asked to act, so it must not be asked again for free."""

    reset_local_service_traffic()
    control = TrapControl(error=RuntimeError("registry busy"))
    harness = RecoveryHarness(tmp_path, control)
    control._clock = harness.monotonic
    try:
        harness.tick()
        harness.services["statsd"].down("statsd worker exited")
        harness.tick(3)
        assert control.calls == ["statsd", "statsd"], control.calls
        assert harness.outcome("statsd") == recovery_blocked_token(BLOCKED_CONTROL_ERROR)
        status = harness.observer.recovery.status(harness.monotonic.value)
        assert status["control_errors"] == 2
        assert status["last_control_error"] == "RuntimeError"
        # The loop kept observing: a raising control is one unit's failure, not the cycle's.
        assert harness.observer.state()["cycle_failures"] == 0
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_a_failed_retry_is_not_reported_as_a_recovery(tmp_path: Path):
    """`retry()` returning False is an attempt, not a recovery. Only an observation proves that."""

    reset_local_service_traffic()
    control = TrapControl(result=False)
    harness = RecoveryHarness(tmp_path, control)
    control._clock = harness.monotonic
    try:
        harness.tick()
        harness.services["statsd"].down("statsd worker exited")
        harness.tick(12)
        assert control.calls == ["statsd"] * BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
        assert harness.outcome("statsd") in {RECOVERY_SCHEDULED, RECOVERY_EXHAUSTED}
        assert harness.outcome("statsd") != RECOVERY_RECOVERED
        status = harness.observer.recovery.status(harness.monotonic.value)
        assert status["resources"]["statsd"]["last_control_result"] is False
    finally:
        harness.observer.stop()
        reset_local_service_traffic()


def test_observer_state_reports_bounded_recovery_status(recovery: RecoveryHarness):
    recovery.tick()
    recovery.services["statsd"].down("statsd worker exited")
    recovery.tick(4)
    status = recovery.observer.state()["recovery"]
    assert status["enabled"] is True
    assert status["armed"] is True
    assert status["max_attempts"] == BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
    assert status["backoff_seconds"] == list(BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS)
    assert status["attempts_total"] == len(recovery.control.calls)
    assert set(status["resources"]) == set(LOCAL_SERVICE_INVENTORY)
    row = status["resources"]["statsd"]
    assert row["attempts"] >= 1
    assert row["outcome"] == RECOVERY_SCHEDULED
    assert row["next_attempt_in_seconds"] >= 0.0


def test_a_retry_changes_the_published_signature_so_history_can_see_it(recovery: RecoveryHarness):
    recovery.tick()
    published_before = len(recovery.published)
    recovery.services["statsd"].down("statsd worker exited")
    cycle = recovery.tick()
    assert cycle.published is True
    assert cycle.recovery["statsd"] == RECOVERY_NOT_ATTEMPTED
    attempt = recovery.tick()
    assert attempt.retries_issued == ("statsd",)
    assert attempt.published is True, "an issued retry is a health fact, not a silent side effect"
    assert attempt.recovery["statsd"] == RECOVERY_SCHEDULED
    second = recovery.tick()  # the 2s boundary: a second attempt, same latched outcome
    assert second.retries_issued == ("statsd",)
    assert len(recovery.published) == published_before + 2
    # ...and a cycle inside a boundary changes nothing and publishes nothing, exactly as
    # before M7. Recovery adds bounded revisions, not one per observation.
    quiet = recovery.tick()
    assert quiet.retries_issued == ()
    assert quiet.published is False


# -- helpers -------------------------------------------------------------------------------


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} is missing from {path.name}")


def _class_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} is missing from {path.name}")


def _named_attributes(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}

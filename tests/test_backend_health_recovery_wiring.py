# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M9 of DOIT.p0.daemon-monitor: the recovery planner, connected to real services.

M7 built the bounded recovery ladder and proved it against an injected `TrapControl`. Nothing
constructed a control, so `cli.start_backend_health_observer` built an observer with
`recovery_control=None` and EVERY verified-down service on EVERY live server published
`retry_blocked_no_control` forever. The planner was finished, tested and correct, and no retry
was ever issued. This file is the wiring, and it is deliberately driven through the PRODUCTION
map and the PRODUCTION control rather than a second double of them:

  * `RecoveryClientApp` borrows `TmuxWebtermApp.local_services_recovery_entrypoints` and
    `local_services_recovery_control` as they are written in `app.py`. The clients under them
    are doubles; the map body, the dispatcher and the observer are the real ones. A test that
    re-declared the map would pass while production mapped statsd to nothing.

  * `RecordingClient` answers `retry()` and treats EVERY other attribute as a defect, recording
    it before raising, because the planner catches a raising control at its supervisor boundary
    and an assertion raised from a client would otherwise be laundered into
    `retry_blocked_control_error` while the observer really was calling `stop()`.

  * The zero-retry proof for an idle machine is paired with a positive control IN THE SAME TEST.
    A zero that no arrangement of the same harness can turn into a one is a vacuum, not a
    measurement, so `test_an_idle_machine_issues_zero_retries` ends by giving one service a
    recorded failure and watching exactly one retry appear.

Everything is driven by the observer's injected monotonic clock. No test sleeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.helpers.backend_health_scenarios import FakeService
from tests.helpers.backend_health_scenarios import RecoveryHarness

from yolomux_lib import cli as cli_module
from yolomux_lib.app import STATSD_ABSENT_WHILE_PIN_PENDING
from yolomux_lib.infra.jobd import JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE
from yolomux_lib.app import LocalServiceRecoveryControl
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
from yolomux_lib.backend_health.observer import BLOCKED_CONTROL_ERROR
from yolomux_lib.backend_health.observer import BLOCKED_DEMAND_STARTED_ABSENT
from yolomux_lib.backend_health.observer import BLOCKED_NO_CONTROL
from yolomux_lib.backend_health.observer import RECOVERY_EXHAUSTED
from yolomux_lib.backend_health.observer import RECOVERY_SCHEDULED
from yolomux_lib.backend_health.observer import recovery_blocked_token
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.local_service_projection import LOCAL_SERVICE_INVENTORY
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.local_services.rpc import reset_local_service_traffic


# Every operation that could touch a process, a socket, or another owner's state, copied from
# the M7 list so the wired control is held to exactly the rule the injected one was.
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

# The services the app's recovery map can reach. indexd is absent on purpose: `SearchIndexerClient`
# declares no retry wrapper, and reaching into its registry from the control would put a recovery
# entrypoint outside the wrapper set `tests/test_backend_health_catalog.py` pins.
RECOVERABLE = ("statsd", "jobd", "statusd", "watchd", "approvald")


class RecordingClient:
    """One local-service client double whose only permitted method is ``retry``."""

    def __init__(self, name: str, *, result: bool = True, clock: Any = None) -> None:
        self.name = name
        self.calls = 0
        self.times: list[float] = []
        self.forbidden: list[str] = []
        self.result = result
        self.clock = clock

    def retry(self) -> bool:
        self.calls += 1
        self.times.append(float(self.clock()) if self.clock is not None else 0.0)
        return self.result

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define. Recorded BEFORE raising: the
        # planner's supervisor boundary would otherwise swallow the assertion.
        self.__dict__.setdefault("forbidden", []).append(name)
        raise AssertionError(f"recovery touched a non-retry client attribute: {self.name}.{name}")


class RecoveryClientApp:
    """Just enough app to run the REAL recovery map and the REAL control on doubles."""

    # Borrowed from production, not re-declared. If `app.py` stops mapping a service, or maps it
    # to something that is not a client `retry`, every test below fails here rather than passing
    # against a copy.
    local_services_recovery_entrypoints = TmuxWebtermApp.local_services_recovery_entrypoints
    local_services_recovery_control = TmuxWebtermApp.local_services_recovery_control

    def __init__(self, clock: Any = None) -> None:
        self.clients = {name: RecordingClient(name, clock=clock) for name in RECOVERABLE}
        self.stats_current_client = self.clients["statsd"]
        self.job_client = self.clients["jobd"]
        self.status_client = self.clients["statusd"]
        self.watch_client = self.clients["watchd"]
        self.approval_client = self.clients["approvald"]
        # indexd's client is present and offers no retry at all, exactly like SearchIndexerClient.
        self.search_indexer = object()

    def set_clock(self, clock: Any) -> None:
        for client in self.clients.values():
            client.clock = clock

    def calls(self) -> dict[str, int]:
        return {name: client.calls for name, client in self.clients.items()}

    def forbidden(self) -> dict[str, list[str]]:
        return {name: client.forbidden for name, client in self.clients.items() if client.forbidden}


@pytest.fixture
def wired(tmp_path: Path):
    """The observer wired to the production control, with `arming_seconds=0`.

    The arming fence is deliberately disabled: it would block every retry for the first 30s and
    a zero-retry result would then prove only that the test ran inside the boot window. With it
    off, a zero is the ROW fence, which is the property under test.
    """

    reset_local_service_traffic()
    app = RecoveryClientApp()
    harness = RecoveryHarness(tmp_path, app.local_services_recovery_control())
    app.set_clock(harness.monotonic)
    harness.app = app
    yield harness
    harness.observer.stop()
    reset_local_service_traffic()


def idle_machine(harness: RecoveryHarness) -> None:
    """Shape all six rows the way a quiet, correctly-running host shapes them.

    Four demand-started services resting absent, jobd absent because another process won the
    background-owner election, and statsd absent inside its bounded pin window. Every reason is
    read from the production constant that spells it, so a renamed token fails here.
    """

    for name, service in harness.services.items():
        service.absent()
    for name in ("indexd", "statusd", "watchd", "approvald"):
        harness.services[name].row["demand_started"] = True
    jobd = harness.services["jobd"].row
    jobd["demand_started"] = False
    jobd["absence_expected_reason"] = JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE
    statsd = harness.services["statsd"].row
    statsd["demand_started"] = False
    statsd["absence_expected_reason"] = STATSD_ABSENT_WHILE_PIN_PENDING


# -- the wiring itself -------------------------------------------------------------------


def test_the_cli_hands_the_observer_the_app_recovery_control(tmp_path: Path, monkeypatch):
    """The one line M9 added, proven at the seam it was missing from.

    Before this, `start_backend_health_observer` built the observer with no control at all, so
    `observer.recovery.enabled` was False on every live server and the planner's very first
    branch published `retry_blocked_no_control`.
    """

    reset_local_service_traffic()
    app = RecoveryClientApp()
    published: list[tuple[str, dict[str, Any]]] = []

    class FakeEvents:
        @staticmethod
        def publish(event_type: str, payload: dict[str, Any]) -> None:
            published.append((event_type, payload))

    app.client_events = FakeEvents()
    app.attach_backend_health_store = lambda store: None
    # The app also receives the observer's liveness reader, beside its history store.
    app.attach_backend_health_observer = lambda observer: None
    app.local_services_row_producers = dict
    app.system_status_service = lambda payload: {"label": str(payload.get("service") or "")}

    monkeypatch.setattr(
        cli_module,
        "BackendHealthStore",
        lambda port, on_diagnostic=None: BackendHealthStore(
            port, state_dir=tmp_path, on_diagnostic=on_diagnostic
        ),
    )
    observer = cli_module.start_backend_health_observer(19771, app)
    try:
        assert observer is not None
        assert observer.recovery.enabled is True, "the observer was built without a control again"
        control = observer.recovery._control
        assert isinstance(control, LocalServiceRecoveryControl), control
        # And it is THIS app's control: a retry through it reaches this app's client.
        assert control.retry("jobd") is True
        assert app.calls() == {"statsd": 0, "jobd": 1, "statusd": 0, "watchd": 0, "approvald": 0}
        assert app.forbidden() == {}
    finally:
        observer.stop() if observer is not None else None
        reset_local_service_traffic()


def test_the_control_reaches_every_service_the_catalog_says_it_can_and_no_other():
    """The map is complete, has no extra id, and every value is that service's own client."""

    app = RecoveryClientApp()
    entrypoints = app.local_services_recovery_entrypoints()
    assert tuple(entrypoints) == RECOVERABLE, tuple(entrypoints)
    assert frozenset(entrypoints) | {"indexd"} == frozenset(LOCAL_SERVICE_INVENTORY)
    for name, entrypoint in entrypoints.items():
        assert entrypoint.__self__ is app.clients[name], name

    control = app.local_services_recovery_control()
    for name in RECOVERABLE:
        assert control.retry(name) is True, name
    assert app.calls() == {name: 1 for name in RECOVERABLE}
    # indexd is not reachable, and asking for it mutates nothing and touches no client.
    assert control.retry("indexd") is False
    assert control.retry("not-a-service") is False
    assert app.calls() == {name: 1 for name in RECOVERABLE}
    assert app.forbidden() == {}


def test_the_control_public_surface_is_exactly_retry():
    """A control that offered `stop` would make a destructive call one typo away."""

    public = frozenset(name for name in vars(LocalServiceRecoveryControl) if not name.startswith("_"))
    assert public == frozenset({"retry"}), sorted(public)
    for operation in DESTRUCTIVE_OPERATIONS:
        assert not hasattr(LocalServiceRecoveryControl, operation), operation


# -- what it does on a running system ----------------------------------------------------


def test_a_verified_down_service_is_retried_through_its_own_client_wrapper(wired: RecoveryHarness):
    """The behaviour the whole milestone exists for: a down service is actually retried."""

    app: RecoveryClientApp = wired.app
    wired.tick()
    assert app.calls()["jobd"] == 0

    wired.services["jobd"].down("jobd broker exited")
    wired.tick()
    assert wired.observer._accepted["jobd"] == ("down", "exited")
    wired.tick(9)

    assert app.calls() == {"statsd": 0, "jobd": BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS, "statusd": 0, "watchd": 0, "approvald": 0}
    assert app.forbidden() == {}
    # And the retry is what the retained health says happened, not just what the double counted.
    assert wired.outcome("jobd") in (RECOVERY_SCHEDULED, "retry_exhausted")
    assert wired.outcome("jobd") != recovery_blocked_token(BLOCKED_NO_CONTROL)


def test_one_retry_per_backoff_boundary_through_the_real_control(wired: RecoveryHarness):
    """The wiring must not add a ladder of its own.

    The TIMES, not the count: a control that retried once per observation reaches four attempts
    too, just sooner. Cadence is 2.0s and the outage starts at t0, so the ladder's 1/2/4/8s
    boundaries are first crossed at +2, +4, +8 and +16.
    """

    app: RecoveryClientApp = wired.app
    wired.tick()
    wired.services["statsd"].down("statsd worker exited")
    wired.tick()
    onset = wired.monotonic.value
    wired.tick(9)

    client = app.clients["statsd"]
    assert client.calls == BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS, client.calls
    offsets = [round(moment - onset, 3) for moment in client.times]
    assert offsets == [2.0, 4.0, 8.0, 16.0], offsets
    gaps = [round(later - earlier, 3) for earlier, later in zip(offsets, offsets[1:])]
    assert gaps == [2.0, 4.0, 8.0], gaps


def test_an_idle_machine_issues_zero_retries(wired: RecoveryHarness):
    """Constraint 3, measured: recovery wired, idle host, ZERO services touched.

    Four demand-started services resting, jobd's lost election and statsd's pin window are all
    fenced by the row itself, and the arming fence is off, so this zero is the row fence and not
    the boot window. The positive control at the end is what makes it a measurement: the same
    harness, one recorded failure, exactly one retry.
    """

    app: RecoveryClientApp = wired.app
    idle_machine(wired)
    wired.tick(30)  # 60 seconds of observations, four full ladders' worth

    assert app.calls() == {name: 0 for name in RECOVERABLE}
    assert app.forbidden() == {}
    # The zero has a stated cause per service, so a silently dead observer cannot produce it.
    assert wired.outcome("statusd") == recovery_blocked_token(BLOCKED_DEMAND_STARTED_ABSENT)
    assert wired.outcome("watchd") == recovery_blocked_token(BLOCKED_DEMAND_STARTED_ABSENT)
    assert wired.outcome("approvald") == recovery_blocked_token(BLOCKED_DEMAND_STARTED_ABSENT)
    assert wired.outcome("indexd") == recovery_blocked_token(BLOCKED_DEMAND_STARTED_ABSENT)
    assert wired.outcome("jobd") == recovery_blocked_token(JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE)
    assert wired.outcome("statsd") == recovery_blocked_token(STATSD_ABSENT_WHILE_PIN_PENDING)

    # Positive control: one of those same services records a failure, and recovery acts. Two
    # ticks, not three: the first observes the outage and the second crosses the 1s boundary,
    # so ONE retry is the whole expected budget at +2s.
    wired.services["approvald"].down("approvald exited")
    wired.tick(2)
    assert app.calls()["approvald"] == 1, app.calls()
    assert app.calls() == {"statsd": 0, "jobd": 0, "statusd": 0, "watchd": 0, "approvald": 1}


def test_a_demand_started_service_that_comes_back_is_never_retried_again(wired: RecoveryHarness):
    """A service that rests, fails, is retried, and rests again does not keep being started."""

    app: RecoveryClientApp = wired.app
    idle_machine(wired)
    wired.tick(3)
    assert app.calls()["statusd"] == 0

    wired.services["statusd"].down("statusd exited")
    wired.tick(2)
    assert app.calls()["statusd"] == 1

    # `ready` is debounced, so the outage is still accepted for one more cycle and the 2s
    # boundary can legitimately fall inside it. The count is frozen once statusd is accepted
    # `ready`, and THAT is what may not move afterwards.
    wired.services["statusd"].up()
    wired.tick(3)
    assert wired.observer._accepted["statusd"][0] == "ready"
    settled = app.calls()["statusd"]
    assert settled <= 2, settled

    wired.services["statusd"].absent()
    wired.services["statusd"].row["demand_started"] = True
    wired.tick(20)
    assert app.calls()["statusd"] == settled, "a resting service was retried after a recovery"
    assert app.forbidden() == {}


def test_zero_destructive_operations_reach_a_wired_client(wired: RecoveryHarness):
    """Whole-lifecycle proof: outage, ladder, recovery, second outage -- only `retry` is touched."""

    app: RecoveryClientApp = wired.app
    wired.tick()
    wired.services["statsd"].down("statsd worker exited")
    wired.services["watchd"].down("watchd exited")
    wired.tick(12)
    wired.services["statsd"].up()
    wired.tick(4)
    wired.services["statsd"].down("statsd worker exited again")
    wired.tick(6)

    assert app.forbidden() == {}
    assert app.calls()["statsd"] > 0 and app.calls()["watchd"] > 0
    assert app.calls()["jobd"] == 0 and app.calls()["statusd"] == 0 and app.calls()["approvald"] == 0


def test_a_client_that_raises_is_recorded_and_never_becomes_a_hot_loop(wired: RecoveryHarness):
    """One resource's failing retry is one recorded failure, not a retry storm."""

    app: RecoveryClientApp = wired.app

    def explode() -> bool:
        app.clients["jobd"].calls += 1
        app.clients["jobd"].times.append(wired.monotonic.value)
        raise RuntimeError("jobd socket refused")

    app.clients["jobd"].retry = explode
    wired.tick()
    wired.services["jobd"].down("jobd broker exited")
    wired.tick(2)

    # The failure is recorded against jobd with its own cause, not swallowed into `none`.
    assert app.clients["jobd"].calls == 1
    assert wired.outcome("jobd") == recovery_blocked_token(BLOCKED_CONTROL_ERROR)

    # And a control that keeps raising is still bounded by the same four attempts: the boundary
    # is consumed BEFORE the client is touched, so a raising client cannot become a hot loop.
    wired.tick(12)
    assert app.clients["jobd"].calls == BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
    assert wired.outcome("jobd") == RECOVERY_EXHAUSTED
    assert app.forbidden() == {}


def test_every_inventory_service_is_either_mapped_or_declares_no_wrapper():
    """No service may fall out of the recovery census by being forgotten in the map."""

    app = RecoveryClientApp()
    mapped = frozenset(app.local_services_recovery_entrypoints())
    unmapped = frozenset(LOCAL_SERVICE_INVENTORY) - mapped
    assert unmapped == frozenset({"indexd"}), sorted(unmapped)
    control = app.local_services_recovery_control()
    for name in sorted(unmapped):
        assert control.retry(name) is False, name
    assert app.calls() == {name: 0 for name in RECOVERABLE}


def test_the_fixtures_shape_the_same_six_rows_production_does():
    """The idle-machine shapes are the inventory, not a subset someone can quietly shrink."""

    assert tuple(LOCAL_SERVICE_INVENTORY) == ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald")
    assert frozenset(RECOVERABLE) < frozenset(LOCAL_SERVICE_INVENTORY)
    assert FakeService("statusd").row["demand_started"] is True


def test_a_failing_cycle_reaches_the_operator_log_through_the_cli_wiring(tmp_path: Path, monkeypatch):
    """The observer's supervisor boundary is wired to a reporter, at the seam it was missing from.

    THE REPRO. `_run` caught every cycle exception into counters that only `liveness()` reads, so
    a live observer whose every cycle threw wrote nothing, said nothing, and looked from outside
    exactly like a healthy quiet one. The only way it was ever actually diagnosed was a process
    dump of the running server. The store's persistence diagnostics already had a reporter here
    (`report` -> `emit_server_log`); the observer simply was not given it, so this test drives the
    PRODUCTION factory rather than an observer a test constructed with the argument already set --
    an observer built by hand would prove the parameter exists and not that `cli` passes it.

    One entry, not three: the loop's cadence is seconds, so a fault reported per occurrence would
    bury the operator log under the very failure it is announcing. And the entry carries the
    CAUSE -- an operator who reads "something threw" is no better off than one who reads nothing.
    """

    reset_local_service_traffic()
    app = RecoveryClientApp()

    class FakeEvents:
        @staticmethod
        def publish(event_type: str, payload: dict[str, Any]) -> None:
            del event_type, payload

    app.client_events = FakeEvents()
    app.attach_backend_health_store = lambda store: None
    app.attach_backend_health_observer = lambda observer: None
    # The full inventory, because an EMPTY producer map makes the collector reject every cycle --
    # which would leave this test measuring its own broken fixture instead of the reporting seam.
    services = {name: FakeService(name) for name in LOCAL_SERVICE_INVENTORY}
    app.local_services_row_producers = lambda: {
        name: service.runtime_status for name, service in services.items()
    }
    app.system_status_service = lambda payload: {"label": str(payload.get("service") or "")}

    monkeypatch.setattr(
        cli_module,
        "BackendHealthStore",
        lambda port, on_diagnostic=None: BackendHealthStore(
            port, state_dir=tmp_path, on_diagnostic=on_diagnostic
        ),
    )
    observer = cli_module.start_backend_health_observer(19772, app)
    assert observer is not None
    try:
        # Retire the factory's own thread first: this test owns the cycles from here.
        observer.stop()
        before = SERVER_LOGS.payload()["sequence"]

        def explode(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("recovery planning failed")

        observer.recovery.decide_all = explode
        _run_supervised(observer, 3)

        # `>=`, not `==`: the factory starts a real thread and this test cannot claim how many
        # cycles it got through before `stop()`. Three of these failures are this test's.
        assert observer.liveness()["consecutive_failures"] >= 3, observer.liveness()
        entries = [
            entry
            for entry in SERVER_LOGS.payload()["logs"]
            if entry["id"] > before and entry["source"] == "backend-health"
        ]
        assert len(entries) == 1, entries
        assert "backend_health_cycle_failed" in entries[0]["message"], entries[0]
        assert "(runtimeerror)" in entries[0]["message"], entries[0]
        assert "for port 19772" in entries[0]["message"], entries[0]
        assert "recovery planning failed" in entries[0]["message"], entries[0]
        assert "Traceback (most recent call last)" in entries[0]["message"], entries[0]
    finally:
        observer.stop()
        reset_local_service_traffic()


def _run_supervised(observer: Any, count: int) -> None:
    """Drive the observer's OWN supervised loop for exactly `count` cycles.

    The loop is what turns a raised cycle into a recorded, reported failure, so a test that
    caught the exception itself would be asserting against its own supervisor.
    """

    observer._stop.clear()
    real = observer.observe_once
    seen = {"cycles": 0}

    def counted() -> Any:
        seen["cycles"] += 1
        if seen["cycles"] >= count:
            observer._stop.set()
        return real()

    observer.observe_once = counted
    observer._wait = lambda timeout: True
    try:
        observer._run()
    finally:
        observer.observe_once = real

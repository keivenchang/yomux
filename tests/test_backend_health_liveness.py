# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
""""I looked and nothing changed" and "I stopped looking" are different facts.

THE DEFECT THIS FILE EXISTS FOR. The observer persists only when the service-state
SIGNATURE changes (`backend_health/observer.py:observe_once`). The projection aged that
last state-change write and published it as `age_seconds`
(`local_service_projection.py:RetainedHealth.payload`), and the panel called any age over
30 seconds "Backend health STOPPED UPDATING"
(`85_debug_panel.js:DEBUG_SYSTEM_HEALTH_STALE_SECONDS`). So a healthy, quiet six-service
system reported its own monitor as dead after 30 seconds. The quieter the machine, the
louder the lie -- and the one condition under which the banner is guaranteed to be wrong
is the condition it is most likely to be seen in.

Why the front-end test could not catch it: `tests/system_health_panel.test.js` handed the
panel `{age_seconds: 3700}` and asserted the panel formatted it as stale. That is a
formatter test. It cannot fail when the MODEL is wrong, because it never composes an
observer cycle -- it asserts that the panel renders the number it was given.

So these tests drive REAL observer cycles through the REAL store into the REAL projection,
and ask the question the reader actually has: is the monitor still looking?

The fix is a separate, explicitly bounded liveness fact rather than a raised threshold or
a suppressed banner -- both of those hide the defect instead of correcting the model.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import pytest

from tests.helpers.clock import FakeClock

from yolomux_lib import local_service_projection
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_CYCLE_FAILING
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_CYCLES_STALLED
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_LIVENESS_MISSED_CYCLES
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_NO_CYCLE_OBSERVED
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVE_SECONDS
from yolomux_lib.backend_health.observer import BackendHealthObserver
from yolomux_lib.backend_health.store import BACKEND_HEALTH_MAX_TRANSITIONS
from yolomux_lib.backend_health.store import DIAGNOSTIC_CYCLE_FAILED
from yolomux_lib.backend_health.store import _transition_totals
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.backend_health.store import HealthSnapshot
from yolomux_lib.backend_health.store import ResourceObservation
from yolomux_lib.local_service_projection import LOCAL_SERVICE_INVENTORY

PORT = 17999


class SteadyService:
    """A service whose reported state never changes -- the quiet healthy case."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cycles = 0

    def runtime_status(self) -> dict[str, Any]:
        self.cycles += 1
        return {
            "service": self.name,
            "pid": 4242,
            "started_at": 10.0,
            "healthy": True,
            "last_failure": "",
            "demand_started": False,
            "resources": {"cpu_percent": 1.0, "rss_bytes": 2048},
        }


class QuietHarness:
    """The observer, its store and the projection, driven one real cycle at a time."""

    def __init__(self, tmp_path: Path) -> None:
        self.services = {name: SteadyService(name) for name in LOCAL_SERVICE_INVENTORY}
        self.monotonic = FakeClock(500.0)
        self.wall = FakeClock(1_000_000.0)
        # Every diagnostic this port reports, from the store AND from the observer's supervisor
        # boundary, through the one reporter `cli.start_backend_health_observer` wires to the
        # operator log.
        self.reported: list[Any] = []
        self.store = BackendHealthStore(
            PORT, state_dir=tmp_path, clock=self.wall, on_diagnostic=self.reported.append
        )
        self.observer = BackendHealthObserver(
            row_producers=lambda: {name: service.runtime_status for name, service in self.services.items()},
            store=self.store,
            publish=lambda event_type, payload: {"type": event_type, "payload": payload},
            label_source=lambda service: service,
            monotonic=self.monotonic,
            wall_clock=self.wall,
            wait=lambda timeout: False,
            identity_source=lambda pid: f"proc:{pid}" if pid > 0 else "",
            on_diagnostic=self.reported.append,
        )

    def run_cycle(self) -> Any:
        """One real observation cycle at the real cadence."""
        cycle = self.observer.observe_once()
        self.monotonic.advance(BACKEND_HEALTH_OBSERVE_SECONDS)
        self.wall.advance(BACKEND_HEALTH_OBSERVE_SECONDS)
        return cycle

    def health(self) -> dict[str, Any]:
        """Exactly what `/api/system-status` publishes as the snapshot provenance block.

        Liveness arrives BESIDE the document, from the observer, exactly as `app.py` wires it.
        """
        return local_service_projection.RetainedHealth(
            document=self.store.status(),
            liveness=self.observer.liveness(),
            traffic={},
            now=self.wall.value,
            web_process_started_at=1_000_000.0,
        ).payload()


def test_a_quiet_healthy_observer_is_still_reported_as_looking(tmp_path):
    """THE REPRO. Nothing changes for well past the staleness threshold; the monitor is alive.

    The first cycle publishes (nothing -> six ready services is a signature change). Every
    cycle after it is a no-op publication by design. Before the fix the projection had only
    the state-change write to age, so `age_seconds` grew without bound and the panel
    declared the observer dead while it was probing every 2 seconds.
    """
    harness = QuietHarness(tmp_path)
    # Two cycles to settle: the debounce promotes the services on the second, so the signature
    # is established at cycle 1 and nothing after it may publish again.
    assert harness.run_cycle().published is True, "the first cycle establishes the signature"
    assert harness.run_cycle().published is True, "the debounce settles the accepted states"

    # 30 more cycles at 2.0s: 60 seconds of a perfectly healthy, perfectly quiet system.
    for _ in range(30):
        cycle = harness.run_cycle()
        assert cycle.published is False, "a quiet system must not republish an unchanged signature"

    health = harness.health()

    # The state-change age is genuinely large, and that is CORRECT -- nothing has changed.
    assert health["age_seconds"] > 60.0, health

    # Liveness is a different fact, and it is the one the banner must read. The observer
    # completed a cycle one interval ago, so it is emphatically not "stopped".
    assert health["observer_alive"] is True, health
    assert health["observer_cycle_age_seconds"] is not None, health
    assert health["observer_cycle_age_seconds"] <= BACKEND_HEALTH_OBSERVE_SECONDS + 0.001, health
    assert health["observer_cycles"] == 32, health

    # Every service really was probed on every cycle -- the liveness claim is backed by work.
    for name, service in harness.services.items():
        assert service.cycles == 32, (name, service.cycles)


def test_an_observer_that_stopped_looking_is_reported_as_stopped(tmp_path):
    """NEGATIVE CONTROL: liveness must still be able to say NO, or it is decoration.

    Raising the threshold or suppressing the banner would also make the quiet case green --
    and would make this case green too, which is exactly why neither is an acceptable fix.
    """
    harness = QuietHarness(tmp_path)
    harness.run_cycle()

    # The observer thread dies: no cycle runs while MONOTONIC time keeps moving. Monotonic is the
    # clock the loop schedules on, so it is the only one that can answer this.
    harness.monotonic.advance(600.0)
    health = harness.health()

    assert health["observer_alive"] is False, health
    assert health["observer_cycle_age_seconds"] > 600.0, health
    assert health["observer_liveness_reason_code"] == "observer_cycles_stalled", health


def test_liveness_does_not_move_when_the_wall_clock_steps(tmp_path):
    """A wall-clock step must not create or cure an outage.

    The first version of this fix aged liveness on `wall_clock_now`. A step backwards held
    "alive" forever and a step forwards produced a false red -- the same class of defect as the
    original bug, one layer in. The loop schedules on monotonic, so liveness is monotonic.
    """
    harness = QuietHarness(tmp_path)
    harness.run_cycle()
    harness.run_cycle()
    before = harness.observer.liveness()

    # An hour forward (NTP correction, DST, a VM resume) and then two hours back.
    harness.wall.advance(3600.0)
    forward = harness.observer.liveness()
    harness.wall.advance(-7200.0)
    backward = harness.observer.liveness()

    for label, after in (("forward", forward), ("backward", backward)):
        assert after["alive"] is before["alive"], (label, before, after)
        assert after["cycle_age_seconds"] == before["cycle_age_seconds"], (label, before, after)
        assert after["reason_code"] == before["reason_code"], (label, before, after)


def test_liveness_is_absent_not_zero_when_no_cycle_has_run(tmp_path):
    """An observer that has never completed a cycle reports absence, never a fresh zero.

    A `0.0` here would read as "probed this instant" -- the fabricated-zero defect the whole
    health contract exists to prevent.
    """
    harness = QuietHarness(tmp_path)
    health = local_service_projection.RetainedHealth(
        document=harness.store.status(),
        liveness=harness.observer.liveness(),
        traffic={},
        now=1_000_000.0,
        web_process_started_at=1_000_000.0,
    ).payload()
    assert health["observer_cycle_age_seconds"] is None, health
    # An observer IS attached here, so these two are real measurements and must stay concrete:
    # it exists, it is not yet alive, and it has completed exactly zero cycles. That is a
    # different fact from the unattached case, where nobody looked and both are absent.
    assert health["observer_alive"] is False, health
    assert health["observer_cycles"] == 0, health
    assert health["observer_liveness_reason_code"] == "no_observer_cycle_recorded", health


def test_an_unattached_observer_reports_absence_rather_than_death(tmp_path):
    """No observer at all is `observer_unattached`, which is not the same as one that stopped."""
    health = local_service_projection.RetainedHealth(now=1_000_000.0).payload()
    assert health["available"] is False, health
    assert health["reason_code"] == "observer_unattached", health
    assert health["observer_cycle_age_seconds"] is None, health
    # ABSENCE, not death -- which is this test's own name. `observer_alive: False` said "we looked
    # and it is not alive"; nobody looked. `available: False` plus the reason code above already
    # carry the absence, and a derived boolean beside them is a second, weaker copy that a reader
    # can mistake for an observation. Same rule as the cycle age directly above it.
    assert health["observer_alive"] is None, health
    assert health["observer_cycles"] is None, health


# -- the lifetime transition count, across eviction ----------------------------------------------
#
# `BACKEND_HEALTH_MAX_TRANSITIONS` is 128 and the store evicts from the head. The projection used
# to publish `"transitions_total": len(transitions)`, so the TOTAL was the retained window: after
# the 129th state change it reported 128 forever, and the panel's "N state changes recorded -- all
# of them are shown" became false at exactly the point `transitions_truncated` exists to flag.
#
# The front-end test could not see it: it fabricates `transitions_total: 42` and never composes a
# producer across eviction. These drive real state changes past the cap.


class FlappingService:
    """A service that alternates state on demand, so each cycle is a real transition."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.up = True

    def flip(self) -> None:
        self.up = not self.up

    def runtime_status(self) -> dict[str, Any]:
        return {
            "service": self.name,
            "pid": 4242 if self.up else 0,
            "started_at": 10.0,
            "healthy": self.up,
            "last_failure": "" if self.up else "worker exited",
            "demand_started": False,
            "resources": {"cpu_percent": 1.0, "rss_bytes": 2048},
        }


def _flapping_harness(tmp_path: Path) -> QuietHarness:
    harness = QuietHarness(tmp_path)
    harness.services = {name: FlappingService(name) for name in LOCAL_SERVICE_INVENTORY}
    harness.observer._row_producers = lambda: {
        name: service.runtime_status for name, service in harness.services.items()
    }
    return harness


def test_the_transition_total_keeps_climbing_after_the_retained_window_evicts(tmp_path):
    """THE REPRO. 300 real state changes; the window holds 128 and the total says 300.

    Driven through the real store with the real cap, so the eviction under test is the one that
    ships -- not a fabricated `transitions_total` handed to a renderer.
    """
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    changes = 0
    # Debounce means a flip needs a couple of cycles to be accepted; keep flipping until we are
    # well past the cap.
    while changes <= BACKEND_HEALTH_MAX_TRANSITIONS * 2:
        for service in harness.services.values():
            service.flip()
        for _ in range(3):
            harness.run_cycle()
        record = harness.store.status()["resources"]["statsd"]
        changes = int(record["transitions_total"])

    record = harness.store.status()["resources"]["statsd"]
    retained = len(record["transitions"])
    total = int(record["transitions_total"])
    assert retained == BACKEND_HEALTH_MAX_TRANSITIONS, retained
    assert total > BACKEND_HEALTH_MAX_TRANSITIONS, (total, retained)

    published = local_service_projection.RetainedHealth(
        document=harness.store.status(), traffic={}, now=harness.wall.value, web_process_started_at=1_000_000.0,
    ).service("statsd")
    # The published total is the LIFETIME count, not the retained window.
    assert published["transitions_total"] == total, (published["transitions_total"], total)
    assert published["transitions_total"] > BACKEND_HEALTH_MAX_TRANSITIONS, published["transitions_total"]
    # And the panel is told the list is incomplete, which is what makes the sentence honest.
    assert published["transitions_truncated"] is True, published
    assert len(published["transitions"]) < published["transitions_total"], published


def test_the_lifetime_total_survives_a_reload_past_the_cap(tmp_path):
    """A restart must not reset the total back to the retained window one boot later.

    This has to EXCEED the 128-row cap before reloading. An earlier version of this test flipped
    about six times -- below the cap -- so the retained list still held every row and the reload
    would have "passed" even if the count had been thrown away and re-derived from the list. It
    could not fail for the reason it exists.
    """
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    while int(harness.store.status()["resources"]["statsd"]["transitions_total"]) <= BACKEND_HEALTH_MAX_TRANSITIONS + 20:
        for service in harness.services.values():
            service.flip()
        for _ in range(3):
            harness.run_cycle()
    record = harness.store.status()["resources"]["statsd"]
    before = int(record["transitions_total"])
    retained = len(record["transitions"])
    assert retained == BACKEND_HEALTH_MAX_TRANSITIONS, retained
    assert before > retained, (before, retained)

    reloaded = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall)
    after = reloaded.status()["resources"]["statsd"]
    assert int(after["transitions_total"]) == before, (before, after["transitions_total"])
    # The reloaded total is still MORE than the list it can show, which is the property that
    # would silently vanish if the count were re-derived on load.
    assert int(after["transitions_total"]) > len(after["transitions"]), after
    assert after["transitions_total_exact"] is True, after


def test_a_pre_existing_capped_history_reports_a_floor_not_an_exact_total(tmp_path):
    """A document written before the counter existed can only yield a LOWER BOUND.

    `BACKEND_HEALTH_SCHEMA_VERSION` is still 1 and this field was added without a version bump,
    so an old capped document has its total inferred from an already-truncated list. Presenting
    that as exact is the same defect the counter was added to fix.
    """
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    while len(harness.store.status()["resources"]["statsd"]["transitions"]) < BACKEND_HEALTH_MAX_TRANSITIONS:
        for service in harness.services.values():
            service.flip()
        for _ in range(3):
            harness.run_cycle()

    # Age the document back to what a pre-counter build wrote: rows, no total, no exactness.
    path = harness.store.document_path
    document = json.loads(path.read_text())
    for record in document["resources"].values():
        record.pop("transitions_total", None)
        record.pop("transitions_total_exact", None)
    path.write_text(json.dumps(document))

    reloaded = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall)
    record = reloaded.status()["resources"]["statsd"]
    assert len(record["transitions"]) == BACKEND_HEALTH_MAX_TRANSITIONS, record
    assert record["transitions_total"] == BACKEND_HEALTH_MAX_TRANSITIONS, record
    # The number is a floor, and it says so rather than claiming to be the whole history.
    assert record["transitions_total_exact"] is False, record

    published = local_service_projection.RetainedHealth(
        document=reloaded.status(), traffic={}, now=harness.wall.value, web_process_started_at=1_000_000.0,
    ).service("statsd")
    assert published["transitions_total_exact"] is False, published
    assert published["transitions_truncated"] is True, published

    # And it STAYS a floor. Writing again must not launder the answer: the record path carries the
    # prior `False` forward, and a build that recomputed exactness from the retained length would
    # find a full window with a prior total and call it exact -- the pre-counter history would
    # silently become a complete one, one write later. Without this the carry-forward branch could
    # be deleted with every test still green.
    current_state = reloaded.status()["resources"]["statsd"]["current"]["state"]
    reloaded.record(
        HealthSnapshot(
            observed_at=harness.wall.value,
            resources=(
                ResourceObservation(
                    resource="statsd",
                    state="down" if current_state != "down" else "ready",
                    reason_code="none",
                    recovery_outcome="none",
                    pid=0,
                    process_start_identity="",
                    counters_available=False,
                ),
            ),
        )
    )
    rewritten = reloaded.status()["resources"]["statsd"]
    assert rewritten["transitions_total"] > BACKEND_HEALTH_MAX_TRANSITIONS, rewritten
    assert rewritten["transitions_total_exact"] is False, rewritten


def test_a_history_that_never_evicted_reports_its_exact_length(tmp_path):
    """No off-by-one at the small end: a short history's total is exactly what it shows."""
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    for service in harness.services.values():
        service.flip()
    for _ in range(3):
        harness.run_cycle()
    published = local_service_projection.RetainedHealth(
        document=harness.store.status(), traffic={}, now=harness.wall.value, web_process_started_at=1_000_000.0,
    ).service("statsd")
    assert published["transitions_total"] == len(published["transitions"]), published
    assert published["transitions_truncated"] is False, published


# -- the publication boundary: a cycle is COMPLETE only when its change is out the door ----------


def _supervised_cycles(harness: QuietHarness, count: int) -> None:
    """Drive the observer's OWN supervised loop for exactly `count` cycles.

    The loop is what turns a raised cycle into `cycle_failures` and `consecutive_failures`, so a
    test that catches the exception itself would be asserting against its own supervisor rather
    than the observer's. This wraps `observe_once` only to count and to stop; nothing about the
    failure accounting is reimplemented here.
    """

    harness.observer._stop.clear()
    real = harness.observer.observe_once
    seen = {"cycles": 0}

    def counted() -> Any:
        seen["cycles"] += 1
        if seen["cycles"] >= count:
            harness.observer._stop.set()
        try:
            return real()
        finally:
            harness.monotonic.advance(BACKEND_HEALTH_OBSERVE_SECONDS)
            harness.wall.advance(BACKEND_HEALTH_OBSERVE_SECONDS)

    harness.observer.observe_once = counted
    # The harness's `wait` never returns True, so the real inter-cycle wait would spin against a
    # frozen fake clock. A woken wait is the shape a real loop takes when a cycle is due.
    harness.observer._wait = lambda timeout: True
    harness.observer._run()
    harness.observer.observe_once = real


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("publication failed")


@pytest.mark.parametrize("failure_point", ["recovery", "store_record", "event_publish"])
def test_a_cycle_that_dies_during_publication_is_never_counted_as_completed(tmp_path, failure_point):
    """THE REPRO. Eight cycles, every one of them dying, and liveness said the monitor was fine.

    `observe_once` recorded completion -- the counter, the monotonic success stamp, the reset of
    `consecutive_failures` and of the typed error -- BEFORE `_publish_change` ran. Recording and
    publication are the two halves of one cycle, and everything after the record could still
    throw: `store.record` on a full or unwritable disk, and the event publish. So eight straight
    failures with zero events published reported `alive=true`, `cycles_completed=8`, a two-second
    cycle age and `consecutive_failures=1`, with an empty reason code. That is the exact shape of
    a monitor that is dead and says so nowhere.

    Parametrized over the three throw sites around the boundary, so what is pinned is the
    BOUNDARY and not one path: `recovery` sits before the record and was already correct, the
    other two sit after it and were not.
    """
    harness = QuietHarness(tmp_path)
    if failure_point == "recovery":
        harness.observer.recovery.decide_all = _boom
    elif failure_point == "store_record":
        harness.store.record = _boom
    else:
        harness.observer._publish = _boom

    _supervised_cycles(harness, 8)

    state = harness.observer.state()
    liveness = harness.observer.liveness()
    assert state["cycle_failures"] == 8, state
    assert state["published_events"] == 0, state
    assert liveness["cycles_attempted"] == 8, liveness
    # The four facts that were all wrong at once.
    assert liveness["cycles_completed"] == 0, liveness
    assert liveness["alive"] is False, liveness
    assert liveness["consecutive_failures"] == 8, liveness
    assert liveness["cycle_age_seconds"] is None, liveness
    assert liveness["reason_code"] == BACKEND_HEALTH_CYCLE_FAILING, liveness
    assert liveness["failure_type"] == "RuntimeError", liveness

    # And the panel is told the same thing through the real projection, not just the observer.
    health = harness.health()
    assert health["observer_alive"] is False, health
    assert health["observer_cycles"] == 0, health
    assert health["observer_cycle_age_seconds"] is None, health
    assert health["observer_liveness_reason_code"] == BACKEND_HEALTH_CYCLE_FAILING, health


def test_an_unchanged_cycle_still_records_completion_without_waiting_for_a_publication(tmp_path):
    """NEGATIVE CONTROL for the fix above: a quiet cycle publishes nothing and is still complete.

    Moving the completion record behind publication must not make a healthy quiet system -- which
    is the majority of cycles, by design -- look like it never finished one.
    """
    harness = QuietHarness(tmp_path)
    harness.run_cycle()
    harness.run_cycle()
    before = harness.observer.liveness()["cycles_completed"]
    cycle = harness.run_cycle()
    assert cycle.published is False, cycle
    liveness = harness.observer.liveness()
    assert liveness["cycles_completed"] == before + 1, liveness
    assert liveness["alive"] is True, liveness


# -- actively failing is not the same fact as stopped attempting ---------------------------------


def test_one_failure_then_silence_is_stalled_and_not_still_failing(tmp_path):
    """THE REPRO. One failed cycle stuck the reason on `observer_cycles_failing` forever.

    `consecutive_failures` only ever falls back to zero on a COMPLETED cycle, so an observer that
    failed once and then stopped being scheduled at all kept a positive count for as long as the
    process lived. `liveness()` computed `attempt_age_seconds` and then ignored it, so a monitor
    that had not attempted anything for ten minutes still reported "still attempting cycles but
    they are failing" -- a bug to go and fix -- when the truth was "stopped attempting", which is
    a thread to restart. Attempt freshness is what separates them, and it was already measured.
    """
    harness = QuietHarness(tmp_path)
    _supervised_cycles(harness, 1)
    assert harness.observer.liveness()["alive"] is True, harness.observer.liveness()

    # NEGATIVE CONTROL first, so the fix cannot be "always say stalled". Keep FAILING, at the real
    # cadence, until the last COMPLETED cycle is past the deadline. Attempts are fresh, so this is
    # the monitor that is throwing on every cycle -- and it must keep saying exactly that.
    harness.store.record = _boom
    harness.observer._published_signature = None  # force every following cycle to be a publishing one
    stale_after = harness.observer.liveness()["stale_after_seconds"]
    _supervised_cycles(harness, int(stale_after / BACKEND_HEALTH_OBSERVE_SECONDS) + 2)
    failing = harness.observer.liveness()
    assert failing["cycle_age_seconds"] > stale_after, failing
    assert failing["attempt_age_seconds"] <= stale_after, failing
    assert failing["consecutive_failures"] > 0, failing
    assert failing["alive"] is False, failing
    assert failing["reason_code"] == BACKEND_HEALTH_CYCLE_FAILING, failing

    # Then ten minutes of nothing at all: no attempt, no completion, a stale failure count. The
    # count is the only thing that has not changed, and it is what the old reason was reading.
    harness.monotonic.advance(600.0)
    harness.wall.advance(600.0)
    stalled = harness.observer.liveness()
    assert stalled["attempt_age_seconds"] >= 600.0, stalled
    assert stalled["cycle_age_seconds"] >= 600.0, stalled
    assert stalled["consecutive_failures"] == failing["consecutive_failures"], stalled
    assert stalled["alive"] is False, stalled
    assert stalled["reason_code"] == BACKEND_HEALTH_CYCLES_STALLED, stalled


def test_an_observer_that_never_attempted_anything_is_not_reported_as_failing(tmp_path):
    """The other side of the same line: no attempt at all is `no_observer_cycle_recorded`."""
    harness = QuietHarness(tmp_path)
    liveness = harness.observer.liveness()
    assert liveness["attempt_age_seconds"] is None, liveness
    assert liveness["consecutive_failures"] == 0, liveness
    assert liveness["reason_code"] == BACKEND_HEALTH_NO_CYCLE_OBSERVED, liveness


def test_a_first_cycle_failure_then_silence_is_stalled_not_a_benign_never_looked(tmp_path):
    """THE REPRO. An observer that died on its FIRST cycle decayed into the quiet not-yet state.

    This is the sibling of `test_one_failure_then_silence_is_stalled_and_not_still_failing`, and
    the attempt-freshness fix did not cover it: that fix distinguishes failing from stalled only
    once a cycle has previously SUCCEEDED. With `cycles_completed == 0` the classifier fell into
    the "no cycle observed" branch, and because the attempts had also stopped, the failure count
    could no longer keep it on `observer_cycles_failing`. So ten minutes after a monitor died on
    its first cycle, liveness reported `no_observer_cycle_recorded` -- "attached, has not looked
    yet" -- which is the benign startup state, and the projection published it beside zero
    retained resources. The panel then renders the quiet never-observed line and the outage is
    invisible on every surface.

    The invariant: `no_observer_cycle_recorded` is honest ONLY before any attempt, or while a
    first attempt that has not failed is still inside its deadline. Once the last ATTEMPT is
    older than the deadline, the loop is not running -- whether or not it ever completed a cycle.
    """
    harness = QuietHarness(tmp_path)
    harness.observer.recovery.decide_all = _boom
    _supervised_cycles(harness, 1)

    # NEGATIVE CONTROL first: while the attempt is fresh, actively-failing is the right answer and
    # must not be replaced by "stalled". The two reasons send an operator to different places.
    failing = harness.observer.liveness()
    assert failing["cycles_attempted"] == 1, failing
    assert failing["cycles_completed"] == 0, failing
    assert failing["failure_type"] == "RuntimeError", failing
    assert failing["reason_code"] == BACKEND_HEALTH_CYCLE_FAILING, failing

    harness.monotonic.advance(600.0)
    harness.wall.advance(600.0)
    stalled = harness.observer.liveness()
    assert stalled["cycles_completed"] == 0, stalled
    assert stalled["attempt_age_seconds"] >= 600.0, stalled
    assert stalled["consecutive_failures"] == 1, stalled
    assert stalled["alive"] is False, stalled
    assert stalled["reason_code"] == BACKEND_HEALTH_CYCLES_STALLED, stalled

    # The projection is where the benign reason actually reached a reader, so it is asserted here
    # rather than only at `liveness()`. Zero retained resources plus a benign reason is exactly the
    # combination that renders as a quiet, healthy, not-yet-started monitor.
    health = harness.health()
    assert health["observer_liveness_reason_code"] == BACKEND_HEALTH_CYCLES_STALLED, health
    assert health["observer_alive"] is False, health
    assert health["observer_cycles"] == 0, health
    assert health["observer_cycle_age_seconds"] is None, health
    assert health["resources"] == 0, health


def test_an_attached_observer_whose_first_cycle_is_still_running_is_not_reported_as_stalled(tmp_path):
    """NEGATIVE CONTROL for the ordering fix: the benign state must still be reachable.

    A monitor that has attempted its first cycle, has not failed, and is inside its deadline is
    genuinely "attached, has not completed a probe cycle yet". The ordering change puts the
    stalled check ahead of the failure check, so without this a fix could simply retire
    `no_observer_cycle_recorded` for anything with an attempt and stay green.

    The state is produced by a REAL cycle held open at a barrier, not by writing the observer's
    private counters. Hand-writing `_cycles_attempted` and `_last_attempt_monotonic` and then
    asserting on them tests this test's arithmetic, not the observer's: it cannot fail if
    `observe_once` stops recording the attempt at entry, which is the thing worth guarding.
    """
    harness = QuietHarness(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_producer(name: str):
        def probe() -> dict[str, Any]:
            entered.set()
            assert release.wait(timeout=10.0), "the barrier was never released"
            return harness.services[name].runtime_status()
        return probe

    harness.observer._row_producers = lambda: {name: blocking_producer(name) for name in harness.services}
    cycle = threading.Thread(target=harness.observer.observe_once, daemon=True)
    cycle.start()
    try:
        assert entered.wait(timeout=10.0), "the first probe never started"
        # Genuinely mid-cycle: the attempt is recorded, nothing has completed, nothing has failed.
        liveness = harness.observer.liveness()
        assert liveness["cycles_attempted"] == 1, liveness
        assert liveness["cycles_completed"] == 0, liveness
        assert liveness["consecutive_failures"] == 0, liveness
        assert liveness["attempt_age_seconds"] is not None, liveness
        assert liveness["alive"] is False, liveness
        assert liveness["reason_code"] == BACKEND_HEALTH_NO_CYCLE_OBSERVED, liveness
    finally:
        release.set()
        cycle.join(timeout=10.0)
    assert cycle.is_alive() is False, "the held cycle never finished"
    # And it completes normally once released, so the barrier proved a transient state and not a
    # broken one.
    assert harness.observer.liveness()["cycles_completed"] == 1, harness.observer.liveness()


def _three_row_history(tmp_path):
    """A store with at least three retained transitions, and the path to its document."""
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    for _ in range(4):
        if len(harness.store.status()["resources"]["statsd"]["transitions"]) >= 3:
            break
        for service in harness.services.values():
            service.flip()
        for _ in range(3):
            harness.run_cycle()
    retained = len(harness.store.status()["resources"]["statsd"]["transitions"])
    assert retained >= 3, retained
    return harness, harness.store.document_path, retained


@pytest.mark.parametrize("broken_total", [-1, True, "1", 1.5, None, [3]])
def test_a_present_but_malformed_total_costs_exactness_and_is_not_read_as_absent(tmp_path, broken_total):
    """THE SIBLING. A published-but-broken counter was laundered into an EXACT one.

    The load path branched on the value's TYPE, so a present `-1`, `True` or `"1"` failed the
    integer check and fell into the branch meant for documents written before the counter existed.
    That branch infers the total from the retained rows and, below the cap, calls it exact -- an
    inference that is only earned when the document said nothing at all. None of these documents
    said nothing: each published a malformed counter, which is a disagreement, and this module's
    rule is that a disagreement costs exactness and never history.

    Measured against the pre-fix tree, with three retained rows: `-1`, `True` and `'1'` each
    reloaded as `total 3, exact True`.
    """
    harness, path, retained = _three_row_history(tmp_path)
    document = json.loads(path.read_text())
    document["resources"]["statsd"]["transitions_total"] = broken_total
    document["resources"]["statsd"]["transitions_total_exact"] = True
    path.write_text(json.dumps(document))

    record = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
    assert record["transitions_total"] == retained, record
    assert record["transitions_total_exact"] is False, record
    # History survives a broken counter; only the claim about it does not.
    assert len(record["transitions"]) == retained, record


def test_a_genuinely_absent_total_below_the_cap_still_follows_the_documented_legacy_rule(tmp_path):
    """NEGATIVE CONTROL: case 1 and case 2 must not be the same case.

    Without this, flooring every malformed counter to `exact=False` would look identical to
    flooring every document that never carried one -- and the documented pre-counter inference,
    where a history that never evicted is exactly its own length, would silently disappear.
    """
    harness, path, retained = _three_row_history(tmp_path)
    assert retained < BACKEND_HEALTH_MAX_TRANSITIONS, retained
    document = json.loads(path.read_text())
    for key in ("transitions_total", "transitions_total_exact"):
        document["resources"]["statsd"].pop(key, None)
    path.write_text(json.dumps(document))

    record = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
    assert record["transitions_total"] == retained, record
    assert record["transitions_total_exact"] is True, record


@pytest.mark.parametrize(
    ("record", "retained", "expected"),
    [
        # Both fields absent: the pre-counter document, exact only while nothing evicted.
        ({}, 3, (3, True)),
        ({}, BACKEND_HEALTH_MAX_TRANSITIONS, (BACKEND_HEALTH_MAX_TRANSITIONS, False)),
        # Total present but unusable, whatever the flag says.
        ({"transitions_total": -1, "transitions_total_exact": True}, 3, (3, False)),
        ({"transitions_total": True, "transitions_total_exact": True}, 3, (3, False)),
        ({"transitions_total": "43", "transitions_total_exact": True}, 3, (3, False)),
        ({"transitions_total": None}, 3, (3, False)),
        # Total present and contradicted by its own rows.
        ({"transitions_total": 1, "transitions_total_exact": True}, 3, (3, False)),
        # Total usable, flag ABSENT: the intermediate-build document, exactness retained.
        ({"transitions_total": 43}, 3, (43, True)),
        ({"transitions_total": 168}, BACKEND_HEALTH_MAX_TRANSITIONS, (168, True)),
        # Total usable, flag PRESENT but not a boolean: nothing readable was asserted.
        ({"transitions_total": 43, "transitions_total_exact": "true"}, 3, (43, False)),
        ({"transitions_total": 43, "transitions_total_exact": 1}, 3, (43, False)),
        ({"transitions_total": 43, "transitions_total_exact": None}, 3, (43, False)),
        ({"transitions_total": 43, "transitions_total_exact": {}}, 3, (43, False)),
        # Real booleans win in both directions.
        ({"transitions_total": 43, "transitions_total_exact": True}, 3, (43, True)),
        ({"transitions_total": 43, "transitions_total_exact": False}, 3, (43, False)),
        # No prior record at all -- a resource seen for the first time.
        (None, 0, (0, True)),
    ],
)
def test_the_counter_owner_answers_presence_and_validity_separately(record, retained, expected):
    """The whole truth table of `_transition_totals`, in one place, for both call paths.

    The record path and the load path each used to carry their own copy of these rules, and the
    copies disagreed: a usable total beside a malformed flag reloaded as `False` and then rewrote
    itself as `True`. One owner, one table, and a fourth divergent copy cannot be written without
    this failing.
    """
    assert _transition_totals(record, retained) == expected


@pytest.mark.parametrize("evicted", [False, True])
def test_a_usable_total_whose_exactness_flag_is_absent_keeps_the_legacy_exact_inference(tmp_path, evicted):
    """NEGATIVE CONTROL for the flag: an ABSENT flag is not a MALFORMED one.

    `8ee3374ba` persisted `transitions_total` and had no `transitions_total_exact` at all, so
    documents written by any instance still running that build sit on disk right now with a real
    lifetime counter and no flag beside it. That counter is carried across eviction by its writer,
    so it is the exact count -- which is why the record path has always inferred `True` for it.

    The exactness branch added one commit ago asked only whether the flag was a `bool` and answered
    `False` for everything else, collapsing "said nothing" into "said something broken". Measured on
    `26bd644b2` with three retained rows, `transitions_total: 43` and the flag omitted:

        loaded_total 43  loaded_exact False

    That is an additive-schema-1 regression: a valid document written by an older build lost a claim
    it had earned. Retained length is deliberately parametrized -- the inference comes from the
    document's own counter, so it must not change when the retained window is at the cap.
    """
    harness, path, retained = _three_row_history(tmp_path)
    document = json.loads(path.read_text())
    resource = document["resources"]["statsd"]
    if evicted:
        row = dict(resource["transitions"][-1])
        resource["transitions"] = [dict(row) for _ in range(BACKEND_HEALTH_MAX_TRANSITIONS)]
        retained = BACKEND_HEALTH_MAX_TRANSITIONS
    resource["transitions_total"] = retained + 40
    resource.pop("transitions_total_exact", None)
    path.write_text(json.dumps(document))

    record = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
    assert record["transitions_total"] == retained + 40, record
    assert record["transitions_total_exact"] is True, record


@pytest.mark.parametrize("broken_flag", ["true", 1, None, {}])
def test_a_usable_total_whose_exactness_flag_is_malformed_does_not_claim_exactness(tmp_path, broken_flag):
    """The same audit, one field over: exactness is a CLAIM, and a broken flag does not make it.

    A consistent total beside a non-boolean flag used to be read as exact. Nothing in that document
    asserts completeness -- the flag is corrupt -- so nothing may be claimed on its behalf.
    """
    harness, path, retained = _three_row_history(tmp_path)
    document = json.loads(path.read_text())
    document["resources"]["statsd"]["transitions_total"] = retained + 40
    document["resources"]["statsd"]["transitions_total_exact"] = broken_flag
    path.write_text(json.dumps(document))

    record = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
    assert record["transitions_total"] == retained + 40, record
    assert record["transitions_total_exact"] is False, record

    # NEGATIVE CONTROL: a real boolean is still honoured in both directions.
    for asserted in (True, False):
        document["resources"]["statsd"]["transitions_total_exact"] = asserted
        path.write_text(json.dumps(document))
        honoured = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
        assert honoured["transitions_total_exact"] is asserted, (asserted, honoured)


def test_a_document_whose_total_contradicts_its_own_rows_is_floored_not_trusted(tmp_path):
    """THE REPRO. `transitions_total: 1` beside three retained rows reloaded as an EXACT 3.

    The load path validated the total's TYPE and never compared it against the rows sitting next
    to it. A document asserting a lifetime count smaller than the history it carries is internally
    inconsistent -- a partial write, a hand-edit, or an older writer -- and trusting its exactness
    flag republishes a number the document itself contradicts. The panel then prints "3 state
    changes recorded, all of them are shown" with the authority of an exact count.

    Flooring is the fix rather than rejection: rejecting resets real retained history over a
    counter disagreement, and this module's own rule is that a downgrade costs EXACTNESS, never
    history. So the total becomes the floor its rows prove, and it stops claiming to be exact.
    """
    harness = _flapping_harness(tmp_path)
    harness.run_cycle()
    # Bounded, and it flips then settles the debounce -- the same shape the other flapping tests
    # use. A single cycle per flip can never be accepted, so an unbounded wait on it spins forever.
    for _ in range(4):
        if len(harness.store.status()["resources"]["statsd"]["transitions"]) >= 3:
            break
        for service in harness.services.values():
            service.flip()
        for _ in range(3):
            harness.run_cycle()

    path = harness.store.document_path
    document = json.loads(path.read_text())
    retained = len(document["resources"]["statsd"]["transitions"])
    assert retained >= 3, retained
    document["resources"]["statsd"]["transitions_total"] = 1
    document["resources"]["statsd"]["transitions_total_exact"] = True
    path.write_text(json.dumps(document))

    reloaded = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall)
    record = reloaded.status()["resources"]["statsd"]
    assert record["transitions_total"] == retained, record
    assert record["transitions_total_exact"] is False, record
    # History is kept, not reset: the disagreement is about a counter, not about the rows.
    assert reloaded.status()["history_reset_reason"] == "", reloaded.status()
    assert len(record["transitions"]) == retained, record

    # NEGATIVE CONTROL: a document whose total AGREES with its rows keeps its exactness. Flooring
    # everything would be as wrong as trusting everything.
    consistent = json.loads(path.read_text())
    consistent["resources"]["statsd"]["transitions_total"] = retained
    consistent["resources"]["statsd"]["transitions_total_exact"] = True
    path.write_text(json.dumps(consistent))
    honest = BackendHealthStore(PORT, state_dir=tmp_path, clock=harness.wall).status()["resources"]["statsd"]
    assert honest["transitions_total"] == retained, honest
    assert honest["transitions_total_exact"] is True, honest


# -- a failing monitor has to say so where a person is already looking ----------------------------


def _cycle_failures(reported: list[Any]) -> list[Any]:
    return [entry for entry in reported if entry.code == DIAGNOSTIC_CYCLE_FAILED]


def test_a_cycle_that_throws_every_time_reports_its_cause_once_per_episode(tmp_path):
    """THE REPRO for the invisible monitor: 20 failing cycles produced ZERO operator-visible output.

    `_run` caught every cycle exception into `_cycle_failures`, `_consecutive_cycle_failures` and
    `type(error).__name__`, and dropped the traceback. Those counters are readable only through
    `liveness()`, i.e. only through an authenticated `/api/system-status` that somebody has to
    think to make -- so from outside, an observer whose every cycle threw was indistinguishable
    from an observer with nothing to report. The way the last one was actually diagnosed was a
    process dump of the running server, and a fault that needs a process dump to see is a fault
    that will not be seen.

    Three things are pinned, because a report that gets any one of them wrong is worse than none:
    the failure is reported at all; it is reported ONCE for the episode and not once per cycle
    (the cadence is seconds -- per-occurrence reporting would bury the log under the fault it
    announces); and it carries the CAUSE, the traceback, not just the exception's class name.
    """

    harness = QuietHarness(tmp_path)
    harness.store.record = _boom

    _supervised_cycles(harness, 20)

    assert harness.observer.liveness()["consecutive_failures"] == 20, harness.observer.liveness()
    failures = _cycle_failures(harness.reported)
    assert len(failures) == 1, [entry.code for entry in harness.reported]
    assert failures[0].detail_code == "runtimeerror", failures[0]
    assert failures[0].port == PORT, failures[0]
    # The cause, not just the name of the cause: the frame that raised has to be in there.
    assert "publication failed" in failures[0].detail_text, failures[0].detail_text
    assert "Traceback (most recent call last)" in failures[0].detail_text, failures[0].detail_text
    assert "_boom" in failures[0].detail_text, failures[0].detail_text


def test_a_fault_that_returns_after_a_good_cycle_is_reported_again(tmp_path):
    """NEGATIVE CONTROL for the dedup above: once-per-episode must not mean once-per-process.

    Reporting only the first occurrence would be correct for one continuous outage and wrong for
    an intermittent one -- the second, third and tenth outage would each be silent, and the log
    would say the monitor recovered and never say it broke again. A COMPLETED cycle is what ends
    an episode, so a fault on either side of a good cycle is two episodes and two reports.
    """

    harness = QuietHarness(tmp_path)
    # The fault is placed on the recovery decision, which every cycle runs, rather than on
    # `store.record`, which only a CHANGED cycle reaches. A quiet system's cycles are unchanged by
    # design, so a store-side fault would simply stop happening once the signature settled and
    # this test would be measuring the absence of a fault, not the reporting of one.
    real_decide_all = harness.observer.recovery.decide_all
    harness.observer.recovery.decide_all = _boom
    _supervised_cycles(harness, 3)
    assert harness.observer.liveness()["consecutive_failures"] == 3, harness.observer.liveness()
    assert len(_cycle_failures(harness.reported)) == 1, harness.reported

    harness.observer.recovery.decide_all = real_decide_all
    _supervised_cycles(harness, 1)
    assert harness.observer.liveness()["consecutive_failures"] == 0, harness.observer.liveness()

    harness.observer.recovery.decide_all = _boom
    _supervised_cycles(harness, 3)
    assert len(_cycle_failures(harness.reported)) == 2, harness.reported
    assert harness.reported[-1].detail_text != "", harness.reported[-1]

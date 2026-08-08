# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused contracts for independent current stats family scheduling."""

import threading
import time

import pytest

from yolomux_lib.stats_current import runtime, scheduler
from yolomux_lib.stats_current import storage


def test_manifest_and_web_scheduler_ownership_are_explicit():
    assert scheduler.COLLECTED_FAMILIES == {
        "cpu", "agent_status", "gpu", "service_load", "system_memory", "agent_tokens",
    }
    assert runtime.WEB_COLLECTED_FAMILIES == {
        "agent_status", "service_load", "system_memory", "agent_tokens",
    }


def test_blocked_cpu_does_not_delay_an_independent_gpu_worker():
    cpu_entered = threading.Event()
    release_cpu = threading.Event()
    gpu_finished = threading.Event()
    owner = 7

    def cpu_collect(attempt):
        attempt.assert_current()
        cpu_entered.set()
        assert release_cpu.wait(1)

    def gpu_collect(attempt):
        attempt.assert_current()
        gpu_finished.set()

    family_scheduler = scheduler.FamilyScheduler(
        (
            scheduler.CollectorJob("cpu", cpu_collect),
            scheduler.CollectorJob("gpu", gpu_collect),
        ),
        owner_generation=lambda: owner,
    )
    try:
        assert family_scheduler.start() is True
        assert cpu_entered.wait(1)
        assert gpu_finished.wait(1), "GPU waited behind a blocked CPU sample"
        assert family_scheduler.start() is False
    finally:
        release_cpu.set()
        family_scheduler.stop()

    status = family_scheduler.status()
    assert status["cpu"].attempts == status["gpu"].attempts == 1
    assert status["cpu"].successes == status["gpu"].successes == 1
    assert status["cpu"].alive is status["gpu"].alive is False


def test_retired_owner_context_prevents_a_late_collector_append():
    entered = threading.Event()
    release = threading.Event()
    generation = 1
    appended = []

    def collect(attempt):
        entered.set()
        assert release.wait(1)
        attempt.assert_current()
        appended.append(attempt.scheduled_at)

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", collect),),
        owner_generation=lambda: generation,
    )
    try:
        family_scheduler.start()
        assert entered.wait(1)
        generation = 2
        release.set()
        family_scheduler.stop()
    finally:
        release.set()
        family_scheduler.stop()

    assert appended == []
    assert family_scheduler.status()["cpu"].failures == 1
    assert "RetiredOwnerError" in family_scheduler.status()["cpu"].last_failure


def test_dynamic_cadence_is_read_from_one_job_owner_and_wake_does_not_overlap():
    entered = threading.Event()
    release = threading.Event()
    calls = []
    cadence = 10.0

    def collect(attempt):
        calls.append(attempt.cadence_seconds)
        entered.set()
        assert release.wait(1)

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: cadence),),
        owner_generation=lambda: 1,
    )
    try:
        family_scheduler.start()
        assert entered.wait(1)
        assert family_scheduler.wake("agent_tokens") is True
        assert family_scheduler.wake("agent_tokens") is True
        assert calls == [10.0]
    finally:
        release.set()
        family_scheduler.stop()


def _usage_atom(event_id, observed_at):
    return storage.UsageAtom(
        event_id,
        "input",
        "text",
        "none",
        "tokens",
        observed_at,
        {
            "quantity": 1,
            "provider": "test-provider",
            "model": "test-model",
            "agent_id": "test-agent",
            "telemetry_complete": True,
        },
    )


def test_demand_idle_demand_rotates_storage_epochs_and_keeps_atoms_landing(tmp_path):
    database_path = tmp_path / storage.DATABASE_FILENAME
    cadence = [0.03]
    attempts = []
    three_passes = threading.Event()

    def collect(attempt):
        index = len(attempts)
        with storage.Store.open(database_path) as store:
            store.append_batch(
                coverage_epochs=(storage.CoverageEpoch(
                    "agent_tokens",
                    "usage-scan",
                    attempt.epoch_id,
                    attempt.epoch_started_at,
                    attempt.scheduled_at + attempt.cadence_seconds,
                    attempt.cadence_seconds,
                    attempt.owner_generation,
                ),),
                usage_atoms=(_usage_atom(f"atom-{index}", attempt.scheduled_at),),
            )
        attempts.append(attempt)
        if index == 0:
            cadence[0] = 0.06
        elif index == 1:
            cadence[0] = 0.03
        elif index == 2:
            three_passes.set()

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: cadence[0]),),
        owner_generation=lambda: 9,
    )
    try:
        assert family_scheduler.start() is True
        assert three_passes.wait(1)
    finally:
        family_scheduler.stop()

    with storage.Store.open_reader(database_path) as store:
        snapshot = store.read_snapshot()
    first_three = attempts[:3]
    assert [attempt.cadence_seconds for attempt in first_three] == [0.03, 0.06, 0.03]
    assert [attempt.epoch_id for attempt in first_three] == [
        "9:agent_tokens:1",
        "9:agent_tokens:2",
        "9:agent_tokens:3",
    ]
    epochs = sorted(snapshot.coverage_epochs, key=lambda item: item.started_at)
    assert [epoch.native_cadence_seconds for epoch in epochs[:3]] == [0.03, 0.06, 0.03]
    assert epochs[0].ended_at == pytest.approx(epochs[1].started_at)
    assert epochs[1].ended_at == pytest.approx(epochs[2].started_at)
    assert all(left.ended_at <= right.started_at for left, right in zip(epochs, epochs[1:]))
    assert {atom.event_id for atom in snapshot.usage_atoms} >= {"atom-0", "atom-1", "atom-2"}
    status = family_scheduler.status()["agent_tokens"]
    assert status.failures == 0
    assert status.successes >= 3


def test_early_wake_keeps_current_epoch_until_the_next_natural_boundary(tmp_path):
    class Clock:
        monotonic_now = 0.0
        wall_now = 100.0

        def monotonic(self):
            return self.monotonic_now

        def wall(self):
            return self.wall_now + self.monotonic_now

    class Wake:
        def __init__(self, stop, clock):
            self.stop = stop
            self.clock = clock
            self.waits = 0

        def wait(self, _timeout):
            self.waits += 1
            if self.waits == 2:
                self.clock.monotonic_now = 0.02
                return True
            if self.waits == 3:
                self.clock.monotonic_now = 0.10
                return False
            if self.waits > 3:
                self.stop.set()
            return False

        def clear(self):
            pass

    database_path = tmp_path / storage.DATABASE_FILENAME
    clock = Clock()
    cadence = [0.08]
    attempts = []

    def collect(attempt):
        index = len(attempts)
        with storage.Store.open(database_path) as store:
            store.append_batch(
                coverage_epochs=(storage.CoverageEpoch(
                    "agent_tokens",
                    "usage-scan",
                    attempt.epoch_id,
                    attempt.epoch_started_at,
                    attempt.scheduled_at + attempt.cadence_seconds,
                    attempt.cadence_seconds,
                    attempt.owner_generation,
                ),),
                usage_atoms=(_usage_atom(f"wake-atom-{index}", attempt.scheduled_at),),
            )
        attempts.append(attempt)
        if index == 0:
            cadence[0] = 0.16

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: cadence[0]),),
        owner_generation=lambda: 5,
        wall_clock=clock.wall,
        monotonic=clock.monotonic,
    )
    family_scheduler._epochs["agent_tokens"] = 1
    worker = scheduler._Worker(
        family_scheduler._workers["agent_tokens"].job,
        Wake(family_scheduler._stop, clock),
    )

    family_scheduler._run_family(worker, 5, 1)

    with storage.Store.open_reader(database_path) as store:
        snapshot = store.read_snapshot()
    first_three = attempts[:3]
    assert [attempt.cadence_seconds for attempt in first_three] == [0.08, 0.08, 0.16]
    assert [attempt.epoch_id for attempt in first_three] == [
        "5:agent_tokens:1",
        "5:agent_tokens:1",
        "5:agent_tokens:2",
    ]
    epochs = sorted(snapshot.coverage_epochs, key=lambda item: item.started_at)
    assert len(epochs) == 2
    assert epochs[0].ended_at == pytest.approx(epochs[1].started_at)
    assert epochs[0].ended_at <= epochs[1].started_at
    assert {atom.event_id for atom in snapshot.usage_atoms} >= {
        "wake-atom-0", "wake-atom-1", "wake-atom-2",
    }
    status = family_scheduler.status()["agent_tokens"]
    assert status.failures == 0
    assert status.successes >= 3


def test_attempt_carries_the_wall_time_where_its_coverage_epoch_started():
    collected = []
    finished = threading.Event()

    def collect(attempt):
        collected.append(attempt)
        finished.set()

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", collect),),
        owner_generation=lambda: 4,
        wall_clock=lambda: 1234.5,
    )
    try:
        assert family_scheduler.start() is True
        assert finished.wait(1)
    finally:
        family_scheduler.stop()

    assert len(collected) == 1
    assert collected[0].epoch_started_at == 1234.5
    assert collected[0].scheduled_at == pytest.approx(1234.5)


def test_restart_under_the_same_owner_uses_a_new_coverage_epoch_identity():
    attempts = []
    finished = threading.Event()

    def collect(attempt):
        attempts.append(attempt)
        finished.set()

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", collect),),
        owner_generation=lambda: 4,
    )
    try:
        assert family_scheduler.start() is True
        assert finished.wait(1)
        family_scheduler.stop()
        finished.clear()
        assert family_scheduler.start() is True
        assert finished.wait(1)
    finally:
        family_scheduler.stop()

    assert len(attempts) == 2
    assert attempts[0].owner_generation == attempts[1].owner_generation == 4
    assert attempts[0].epoch_id == "4:cpu:1"
    assert attempts[1].epoch_id == "4:cpu:2"


def test_restart_after_cadence_rotation_does_not_reuse_an_epoch_identity():
    attempts = []
    cadence = [0.02]
    rotated = threading.Event()
    restarted = threading.Event()

    def collect(attempt):
        attempts.append(attempt)
        if len(attempts) == 1:
            cadence[0] = 0.04
        elif len(attempts) == 2:
            rotated.set()
        elif len(attempts) == 3:
            restarted.set()

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: cadence[0]),),
        owner_generation=lambda: 4,
    )
    try:
        assert family_scheduler.start() is True
        assert rotated.wait(1)
        family_scheduler.stop()
        assert family_scheduler.start() is True
        assert restarted.wait(1)
    finally:
        family_scheduler.stop()

    assert [attempt.epoch_id for attempt in attempts[:3]] == [
        "4:agent_tokens:1",
        "4:agent_tokens:2",
        "4:agent_tokens:3",
    ]


def test_late_deadline_reports_the_entire_skipped_window_and_rotates_epoch():
    class Clock:
        monotonic_now = 0.0
        wall_now = 100.0

        def monotonic(self):
            return self.monotonic_now

        def wall(self):
            return self.wall_now + self.monotonic_now

    class Wake:
        def __init__(self, stop, clock):
            self.stop = stop
            self.clock = clock
            self.waits = 0

        def wait(self, _timeout):
            self.waits += 1
            if self.waits == 2:
                self.clock.monotonic_now = 6.0
            elif self.waits > 2:
                self.stop.set()
            return False

        def clear(self):
            pass

    clock = Clock()
    missed = []
    attempts = []

    def collect(attempt):
        attempts.append(attempt)
        if len(attempts) == 1:
            clock.monotonic_now = 5.0

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", collect, lambda: 1.0, missed.append),),
        owner_generation=lambda: 7,
        wall_clock=clock.wall,
        monotonic=clock.monotonic,
    )
    family_scheduler._epochs["cpu"] = 1
    worker = scheduler._Worker(family_scheduler._workers["cpu"].job, Wake(family_scheduler._stop, clock))

    family_scheduler._run_family(worker, 7, 1)

    assert missed == [scheduler.CollectorMiss("cpu", "7:cpu:1", 101.0, 106.0, 1.0, 7)]
    assert [(item.epoch_id, item.epoch_started_at, item.scheduled_at) for item in attempts] == [
        ("7:cpu:1", 100.0, 100.0),
        ("7:cpu:2", 106.0, 106.0),
    ]
    assert family_scheduler.status()["cpu"].missed_cycles == 5


@pytest.mark.parametrize(
    "job",
    (
        scheduler.CollectorJob("browser", lambda _attempt: None),
        scheduler.CollectorJob("cost", lambda _attempt: None),
        scheduler.CollectorJob("mystery", lambda _attempt: None),
    ),
)
def test_event_driven_derived_and_unknown_families_cannot_gain_a_parallel_scheduler(job):
    with pytest.raises(scheduler.SchedulerError, match="unsupported scheduled families"):
        scheduler.FamilyScheduler((job,), owner_generation=lambda: 1)


def test_invalid_or_absent_owner_generation_does_not_start_threads():
    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", lambda _attempt: None),),
        owner_generation=lambda: None,
    )
    assert family_scheduler.start() is False
    assert family_scheduler.status()["cpu"].attempts == 0


def test_budget_follow_up_wake_cannot_run_a_family_faster_than_its_floor():
    """A self-issued backlog follow-up must not turn the worker into a spin loop.

    The live agent_tokens worker ran 41 cycles in 30s against a 10s cadence and
    burned 87% of a core: every budget-exhausted scan woke itself with no delay,
    so the collector's rate was set by how fast its own body ran.
    """

    floor_seconds = 0.3
    starts = []
    done = threading.Event()

    def collect(attempt):
        starts.append(time.monotonic())
        if len(starts) >= 3:
            done.set()
            return
        assert family_scheduler.wake(
            "agent_tokens", min_interval_seconds=floor_seconds,
        ) is True

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: 30.0),),
        owner_generation=lambda: 1,
    )
    try:
        assert family_scheduler.start() is True
        assert done.wait(5), "follow-up wakes never produced three attempts"
    finally:
        family_scheduler.stop()

    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    assert len(gaps) == 2
    assert all(gap >= floor_seconds for gap in gaps), gaps
    # The floor must not become a cadence: a refresh wake still runs at once.
    assert all(gap < 5.0 for gap in gaps), gaps


def test_refresh_wake_without_a_floor_still_runs_immediately():
    starts = []
    done = threading.Event()

    def collect(attempt):
        starts.append(time.monotonic())
        if len(starts) >= 3:
            done.set()
            return
        assert family_scheduler.wake("agent_tokens") is True

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: 30.0),),
        owner_generation=lambda: 1,
    )
    try:
        assert family_scheduler.start() is True
        assert done.wait(5), "an unfloored wake did not run immediately"
    finally:
        family_scheduler.stop()

    assert starts[-1] - starts[0] < 0.3


def test_wake_rejects_a_non_finite_minimum_interval():
    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", lambda attempt: None),),
        owner_generation=lambda: 1,
    )
    with pytest.raises(scheduler.SchedulerError, match="min_interval_seconds"):
        family_scheduler.wake("agent_tokens", min_interval_seconds=float("nan"))
    with pytest.raises(scheduler.SchedulerError, match="min_interval_seconds"):
        family_scheduler.wake("agent_tokens", min_interval_seconds=-1.0)

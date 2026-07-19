# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused contracts for independent current stats family scheduling."""

import threading

import pytest

from yolomux_lib.stats_current import scheduler
from yolomux_lib.stats_current import storage


def test_manifest_defines_the_complete_independently_scheduled_family_set():
    assert scheduler.COLLECTED_FAMILIES == {
        "cpu", "agent_status", "gpu", "service_load", "system_memory", "agent_tokens",
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
    database_path = tmp_path / storage.DATABASE_FILENAME
    cadence = [0.08]
    attempts = []
    first_pass = threading.Event()
    second_pass = threading.Event()
    third_pass = threading.Event()

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
        (first_pass, second_pass, third_pass)[min(index, 2)].set()

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("agent_tokens", collect, lambda: cadence[0]),),
        owner_generation=lambda: 5,
    )
    try:
        assert family_scheduler.start() is True
        assert first_pass.wait(1)
        cadence[0] = 0.16
        assert family_scheduler.wake("agent_tokens") is True
        assert family_scheduler.wake("agent_tokens") is True
        assert second_pass.wait(1)
        assert third_pass.wait(1)
    finally:
        family_scheduler.stop()

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

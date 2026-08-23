# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Independent deadline workers for current YO!stats native collectors."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from .families import CURRENT_FAMILIES, FAMILY_BY_NAME


LOGGER = logging.getLogger(__name__)
COLLECTED_FAMILIES = frozenset(
    spec.name
    for spec in CURRENT_FAMILIES
    if spec.active_cadence_seconds is not None and spec.coverage_family == spec.name
)
EXPECTED_COLLECTOR_ERRORS = (OSError, RuntimeError, ValueError)


class SchedulerError(RuntimeError):
    """A current collector scheduler contract is invalid."""


class RetiredOwnerError(SchedulerError):
    """A collector result belongs to a retired background-owner generation."""


@dataclass(frozen=True, slots=True)
class CollectorAttempt:
    family: str
    scheduled_at: float
    cadence_seconds: float
    epoch_id: str
    epoch_started_at: float
    owner_generation: int
    _owner_generation: Callable[[], int | None]

    def assert_current(self) -> None:
        if self._owner_generation() != self.owner_generation:
            raise RetiredOwnerError(
                f"{self.family} collector owner generation retired before append"
            )


@dataclass(frozen=True, slots=True)
class CollectorMiss:
    """A wall-clock interval a collector skipped after overrunning its deadline."""

    family: str
    epoch_id: str
    started_at: float
    ended_at: float
    cadence_seconds: float
    owner_generation: int


@dataclass(frozen=True, slots=True)
class CollectorJob:
    family: str
    collect: Callable[[CollectorAttempt], None]
    cadence_seconds: Callable[[], float] | None = None
    record_miss: Callable[[CollectorMiss], None] | None = None


@dataclass(frozen=True, slots=True)
class CollectorStatus:
    cadence_seconds: float = 0.0
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    late_cycles: int = 0
    missed_cycles: int = 0
    last_runtime_seconds: float = 0.0
    last_attempt_at: float = 0.0
    last_success_at: float = 0.0
    last_failure: str = ""
    alive: bool = False
    running: bool = False
    epoch: int = 1


@dataclass(slots=True)
class _Worker:
    job: CollectorJob
    wake: threading.Event
    thread: threading.Thread | None = None
    # Minimum seconds a pending wake must leave between the previous attempt's
    # start and this one.  Only a self-issued follow-up sets it; a viewer
    # refresh keeps the default and still runs immediately.
    wake_min_interval_seconds: float = 0.0


class FamilyScheduler:
    """Run each supplied native family on its own non-overlapping deadline."""

    def __init__(
        self,
        jobs: tuple[CollectorJob, ...],
        *,
        owner_generation: Callable[[], int | None],
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        families = tuple(job.family for job in jobs)
        if len(set(families)) != len(families):
            raise SchedulerError("collector families must be unique")
        unknown = set(families) - COLLECTED_FAMILIES
        if unknown:
            raise SchedulerError(f"unsupported scheduled families: {sorted(unknown)}")
        if any(not callable(job.collect) for job in jobs):
            raise SchedulerError("collector callbacks must be callable")
        if any(job.record_miss is not None and not callable(job.record_miss) for job in jobs):
            raise SchedulerError("collector missed-deadline callbacks must be callable")
        self._workers = {
            job.family: _Worker(job, threading.Event())
            for job in jobs
        }
        self._owner_generation = owner_generation
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._statuses = {family: CollectorStatus() for family in families}
        self._epochs = {family: 0 for family in families}

    def start(self) -> bool:
        generation = self._owner_generation()
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            return False
        with self._lock:
            if any(worker.thread is not None and worker.thread.is_alive() for worker in self._workers.values()):
                return False
            self._stop = threading.Event()
            for family, worker in self._workers.items():
                initial_epoch = self._new_epoch(family)
                worker.wake.clear()
                worker.thread = threading.Thread(
                    target=self._run_family,
                    args=(worker, generation, initial_epoch),
                    name=f"stats-current-{family.replace('_', '-')}",
                    daemon=True,
                )
                self._statuses[family] = CollectorStatus(
                    alive=True,
                    epoch=initial_epoch,
                )
                worker.thread.start()
        return True

    def stop(self, *, join_seconds: float = 1.0) -> None:
        if not math.isfinite(join_seconds) or join_seconds < 0:
            raise ValueError("join_seconds must be finite and non-negative")
        self._stop.set()
        with self._lock:
            workers = tuple(self._workers.values())
        for worker in workers:
            worker.wake.set()
        deadline = self._monotonic() + join_seconds
        for worker in workers:
            thread = worker.thread
            if thread is not None:
                thread.join(timeout=max(0.0, deadline - self._monotonic()))

    def wake(self, family: str, *, min_interval_seconds: float = 0.0) -> bool:
        """Run a family ahead of its deadline, no sooner than ``min_interval_seconds``.

        A collector that exhausted its scan budget wakes itself to drain the
        rest of its backlog.  That wake carried no delay, so a backlog that
        never empties made the worker free-run: the live ``agent_tokens``
        worker ran 41 cycles in 30s against a 10s cadence.  A follow-up passes
        its minimum interval here, which bounds wake-driven attempts to
        ``1 / min_interval_seconds`` per second no matter how large the backlog
        is.  A cadence deadline is never delayed by the floor.
        """

        if (
            isinstance(min_interval_seconds, bool)
            or not isinstance(min_interval_seconds, (int, float))
            or not math.isfinite(min_interval_seconds)
            or min_interval_seconds < 0
        ):
            raise SchedulerError("min_interval_seconds must be finite and non-negative")
        worker = self._workers.get(family)
        if worker is None or worker.thread is None or not worker.thread.is_alive():
            return False
        worker.wake_min_interval_seconds = float(min_interval_seconds)
        worker.wake.set()
        return True

    def status(self) -> Mapping[str, CollectorStatus]:
        with self._lock:
            return MappingProxyType(dict(self._statuses))

    def _cadence(self, job: CollectorJob) -> float:
        spec = FAMILY_BY_NAME[job.family]
        raw = (
            job.cadence_seconds()
            if job.cadence_seconds is not None
            else spec.cadence_seconds(watched=True)
        )
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SchedulerError(f"{job.family} cadence must be numeric")
        cadence = float(raw)
        if not math.isfinite(cadence) or cadence <= 0:
            raise SchedulerError(f"{job.family} cadence must be finite and positive")
        return cadence

    def _update(self, family: str, **values: object) -> CollectorStatus:
        with self._lock:
            current = self._statuses[family]
            updated = replace(current, **values)
            self._statuses[family] = updated
            return updated

    def _new_epoch(self, family: str) -> int:
        """Allocate one process-unique epoch sequence for a family."""

        with self._lock:
            self._epochs[family] += 1
            return self._epochs[family]

    def _run_family(
        self,
        worker: _Worker,
        generation: int,
        initial_epoch: int,
    ) -> None:
        family = worker.job.family
        next_deadline = self._monotonic()
        monotonic_anchor = next_deadline
        wall_anchor = self._wall_clock()
        epoch = initial_epoch
        epoch_started_at = wall_anchor
        epoch_cadence: float | None = None
        last_started = -math.inf
        while not self._stop.is_set() and self._owner_generation() == generation:
            epoch_started_monotonic = monotonic_anchor + (epoch_started_at - wall_anchor)
            before_wait = self._monotonic()
            woke = worker.wake.wait(max(0.0, next_deadline - before_wait))
            while woke and not self._stop.is_set():
                # A replacement epoch can begin after an overrun has returned. Hold
                # a queued wake until that epoch exists so the collector never labels
                # current state with a future timestamp. Outside a skipped window this
                # preserves the existing immediate/rate-limited wake behavior.
                floor = min(
                    max(
                        last_started + worker.wake_min_interval_seconds,
                        epoch_started_monotonic,
                    ),
                    next_deadline,
                )
                remaining = floor - self._monotonic()
                if remaining <= 0:
                    break
                worker.wake.clear()
                worker.wake.wait(remaining)
            worker.wake_min_interval_seconds = 0.0
            worker.wake.clear()
            if self._stop.is_set() or self._owner_generation() != generation:
                break
            now = self._monotonic()
            early_wake = woke and now < next_deadline
            # The wait floor above owns the delay. Keep this clamp as the timestamp
            # invariant at the boundary in case a custom wake returns fractionally
            # before its timeout expires.
            attempt_deadline = (
                max(now, epoch_started_monotonic) if early_wake else next_deadline
            )
            desired_cadence = self._cadence(worker.job)
            attempted_at = self._wall_clock()
            scheduled_at = wall_anchor + attempt_deadline - monotonic_anchor
            previous = self.status()[family]
            # The prior pass already claims coverage through its natural deadline.
            # An early wake stays in that cadence regime so a new epoch cannot overlap it.
            cadence = epoch_cadence if early_wake and epoch_cadence is not None else desired_cadence
            if previous.attempts and abs(attempted_at - scheduled_at) > max(1.0, cadence * 2):
                epoch = self._new_epoch(family)
                attempt_deadline = self._monotonic()
                monotonic_anchor = attempt_deadline
                wall_anchor = attempted_at
                scheduled_at = attempted_at
                epoch_started_at = scheduled_at
                cadence = desired_cadence
                epoch_cadence = cadence
            elif epoch_cadence is None:
                epoch_cadence = cadence
            elif not early_wake and desired_cadence != epoch_cadence:
                epoch = self._new_epoch(family)
                epoch_started_at = scheduled_at
                cadence = desired_cadence
                epoch_cadence = cadence
            attempt = CollectorAttempt(
                family,
                scheduled_at,
                cadence,
                f"{generation}:{family}:{epoch}",
                epoch_started_at,
                generation,
                self._owner_generation,
            )
            self._update(
                family,
                cadence_seconds=cadence,
                attempts=previous.attempts + 1,
                last_attempt_at=attempted_at,
                alive=True,
                running=True,
                epoch=epoch,
            )
            started = self._monotonic()
            last_started = started
            failure = ""
            try:
                worker.job.collect(attempt)
                attempt.assert_current()
            except EXPECTED_COLLECTOR_ERRORS as error:
                failure = f"{type(error).__name__}: {error}"[:500]
                LOGGER.warning("current stats %s collector failed: %s", family, failure)
            runtime = max(0.0, self._monotonic() - started)
            current = self.status()[family]
            self._update(
                family,
                successes=current.successes + (0 if failure else 1),
                failures=current.failures + (1 if failure else 0),
                last_runtime_seconds=runtime,
                last_success_at=current.last_success_at if failure else self._wall_clock(),
                last_failure=failure,
                running=False,
            )
            next_deadline = attempt_deadline + cadence
            now = self._monotonic()
            if now >= next_deadline:
                missed_deadline = next_deadline
                skipped = int((now - next_deadline) // cadence) + 1
                next_deadline += skipped * cadence
                if worker.job.record_miss is not None:
                    missed = CollectorMiss(
                        family,
                        attempt.epoch_id,
                        wall_anchor + missed_deadline - monotonic_anchor,
                        wall_anchor + next_deadline - monotonic_anchor,
                        cadence,
                        generation,
                    )
                    try:
                        worker.job.record_miss(missed)
                    except EXPECTED_COLLECTOR_ERRORS as error:
                        LOGGER.warning("current stats %s missed-deadline record failed: %s", family, error)
                    epoch = self._new_epoch(family)
                    epoch_started_at = missed.ended_at
                    epoch_cadence = cadence
                current = self.status()[family]
                self._update(
                    family,
                    late_cycles=current.late_cycles + 1,
                    missed_cycles=current.missed_cycles + skipped,
                )
        current = self.status()[family]
        self._update(family, alive=False, running=False, last_failure=current.last_failure)

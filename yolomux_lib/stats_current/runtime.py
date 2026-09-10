# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Background-owner lifecycle for current YO!stats collection."""

from __future__ import annotations

import math
import logging
import threading
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType

from ..local_services.client import release_local_service_lease_eventually
from . import collectors, scheduler, storage
from .client import StatsCurrentClient, iter_append_batches


class CurrentRuntimeError(RuntimeError):
    """The elected current stats collector runtime could not proceed."""


Collector = Callable[[scheduler.CollectorAttempt], collectors.CollectorFacts]
DEFAULT_RETRY_INITIAL_SECONDS = 5.0
DEFAULT_RETRY_MAX_SECONDS = 60.0
DEFAULT_OWNER_CHECK_SECONDS = 1.0
# A scan that spends its whole byte/record budget asks for an immediate
# follow-up cycle.  Honouring that with no delay made the agent_tokens worker
# free-run: 41 cycles in 30s against a 10s cadence, 87% of a core on the live
# 7771 web process.  Flooring the follow-up bounds a family to
# 1/cadence + 1/BUDGET_FOLLOW_UP_MIN_INTERVAL_SECONDS attempts per second at any
# backlog size, so its CPU cannot exceed body_cpu times that rate.  At the
# measured 0.62s body and the 10s watched token cadence that ceiling is 22% of a
# core, inside SERVER_CPU_BUDGET_PERCENT even if the body never gets faster.
BUDGET_FOLLOW_UP_MIN_INTERVAL_SECONDS = 4.0
SUPERVISOR_JOIN_SECONDS = 5.0
EXPECTED_SUPERVISOR_ERRORS = (OSError, RuntimeError, ValueError)
WEB_COLLECTED_FAMILIES = scheduler.COLLECTED_FAMILIES - frozenset({"cpu", "gpu"})
logger = logging.getLogger(__name__)


def _positive_seconds(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CurrentRuntimeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise CurrentRuntimeError(f"{name} must be finite and positive")
    return result


def _bounded_kind(value: object, fallback: str) -> str:
    text = str(value or "").partition(":")[0].strip()
    return text[:64] if text.replace("_", "").isalnum() else fallback


class StatsCurrentRuntime:
    """Tie one elected owner, one service lease, and independent collectors together."""

    def __init__(
        self,
        client: StatsCurrentClient,
        collectors_by_family: Mapping[str, Collector],
        *,
        owner_generation: Callable[[], int | None],
        token_cadence_seconds: Callable[[], float],
        collector_context: Callable[[], Mapping[str, object]] | None = None,
        family_cadence_seconds: Callable[[str], float] | None = None,
        retry_initial_seconds: float = DEFAULT_RETRY_INITIAL_SECONDS,
        retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS,
        owner_check_seconds: float = DEFAULT_OWNER_CHECK_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        supplied = set(collectors_by_family)
        if supplied != WEB_COLLECTED_FAMILIES:
            missing = sorted(WEB_COLLECTED_FAMILIES - supplied)
            extra = sorted(supplied - WEB_COLLECTED_FAMILIES)
            raise CurrentRuntimeError(
                f"current collector set mismatch; missing={missing}, extra={extra}"
            )
        self.client = client
        self._owner_generation = owner_generation
        self._collector_context = collector_context
        self._collectors = MappingProxyType(dict(collectors_by_family))
        self._family_cadence_seconds = family_cadence_seconds
        self._retry_initial_seconds = _positive_seconds(
            retry_initial_seconds,
            "retry_initial_seconds",
        )
        self._retry_max_seconds = _positive_seconds(
            retry_max_seconds,
            "retry_max_seconds",
        )
        if self._retry_max_seconds < self._retry_initial_seconds:
            raise CurrentRuntimeError(
                "retry_max_seconds must be greater than or equal to retry_initial_seconds"
            )
        self._owner_check_seconds = _positive_seconds(
            owner_check_seconds,
            "owner_check_seconds",
        )
        self._monotonic = monotonic
        self._lease_id = ""
        self._lock = threading.RLock()
        self._supervisor: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._phase = "stopped"
        self._failure_count = 0
        self._last_failure_kind = ""
        self._retry_delay_seconds = 0.0
        self._next_retry_at = 0.0
        self._family_sources: dict[str, set[str]] = {
            family: set() for family in WEB_COLLECTED_FAMILIES
        }
        jobs = tuple(
            scheduler.CollectorJob(
                family,
                self._collector_callback(family),
                (lambda family=family: self._family_cadence_seconds(family))
                if self._family_cadence_seconds is not None
                else (token_cadence_seconds if family == "agent_tokens" else None),
                self._missed_deadline_callback(family),
            )
            for family in sorted(WEB_COLLECTED_FAMILIES)
        )
        self.scheduler = scheduler.FamilyScheduler(
            jobs,
            owner_generation=owner_generation,
        )

    def _collector_callback(
        self,
        family: str,
    ) -> Callable[[scheduler.CollectorAttempt], None]:
        def collect(attempt: scheduler.CollectorAttempt) -> None:
            logger.warning("stats collector attempt family=%s epoch=%s", family, attempt.epoch_id)
            attempt.assert_current()
            facts = self._collectors[family](attempt)
            if not isinstance(facts, collectors.CollectorFacts):
                raise CurrentRuntimeError(
                    f"{family} collector did not return CollectorFacts"
                )
            try:
                attempt.assert_current()
            except Exception:
                if facts.receipt is not None:
                    facts.receipt.rollback()
                raise
            self._append_facts(family, facts)
            attempt.assert_current()

        return collect

    def _missed_deadline_callback(
        self,
        family: str,
    ) -> Callable[[scheduler.CollectorMiss], None]:
        def record(missed: scheduler.CollectorMiss) -> None:
            if missed.family != family:
                raise CurrentRuntimeError("scheduler reported a miss for the wrong family")
            with self._lock:
                sources = tuple(sorted(self._family_sources[family]))
            if not sources:
                return
            self._append_batch(
                family,
                unavailable_spans=tuple(
                    storage.UnavailableSpan(
                        family,
                        source_id,
                        missed.epoch_id,
                        missed.started_at,
                        missed.ended_at,
                        missed.cadence_seconds,
                        "scheduler_deadline_missed",
                        missed.owner_generation,
                    )
                    for source_id in sources
                ),
            )

        return record

    def _append_facts(
        self,
        family: str,
        facts: collectors.CollectorFacts,
    ) -> None:
        if facts.budget_exhausted_follow_up and facts.receipt is None:
            raise CurrentRuntimeError(
                "budget-exhausted follow-up requires an acknowledged receipt"
            )
        try:
            for observations, usage_atoms, usage_tombstones, coverage_epochs, unavailable_spans in iter_append_batches(
                observations=facts.observations,
                usage_atoms=facts.usage_atoms,
                usage_tombstones=facts.usage_tombstones,
                coverage_epochs=facts.coverage_epochs,
                unavailable_spans=facts.unavailable_spans,
            ):
                self._append_batch(
                    family,
                    observations=observations,
                    usage_atoms=usage_atoms,
                    usage_tombstones=usage_tombstones,
                    coverage_epochs=coverage_epochs,
                    unavailable_spans=unavailable_spans,
                )
            if facts.receipt is not None:
                facts.receipt.commit()
        except Exception:
            if facts.receipt is not None:
                facts.receipt.rollback()
            raise
        if facts.budget_exhausted_follow_up:
            self.scheduler.wake(
                family,
                min_interval_seconds=BUDGET_FOLLOW_UP_MIN_INTERVAL_SECONDS,
            )
        with self._lock:
            self._family_sources[family].update(
                epoch.source_id for epoch in facts.coverage_epochs if epoch.family == family
            )

    def _append_batch(
        self,
        family: str,
        *,
        observations: tuple[storage.Observation, ...] = (),
        usage_atoms: tuple[storage.UsageAtom, ...] = (),
        usage_tombstones: tuple[storage.UsageAtomTombstone, ...] = (),
        coverage_epochs: tuple[storage.CoverageEpoch, ...] = (),
        unavailable_spans: tuple[storage.UnavailableSpan, ...] = (),
    ) -> None:
        """Isolate hard usage conflicts while preserving every other append failure."""

        response = self.client.append(
            observations=observations,
            usage_atoms=usage_atoms,
            usage_tombstones=usage_tombstones,
            coverage_epochs=coverage_epochs,
            unavailable_spans=unavailable_spans,
        )
        if response.get("ok") is True:
            return
        if response.get("status") != storage.USAGE_IDENTITY_CONFLICT_STATUS:
            detail = (
                response.get("error")
                or response.get("reason")
                or response.get("status")
            )
            raise CurrentRuntimeError(str(detail or f"{family} append failed"))
        if not usage_atoms:
            raise CurrentRuntimeError(
                "statsd reported a usage identity conflict without a usage atom"
            )

        # The strict store rejected the whole transaction. Land fixed facts
        # exactly once, then bisect only atoms until each poison identity is a
        # singleton. Tombstones follow atoms, preserving Store.append_batch's
        # mutation order instead of deleting a row before its conflict retry.
        if observations or coverage_epochs or unavailable_spans:
            self._append_batch(
                family,
                observations=observations,
                coverage_epochs=coverage_epochs,
                unavailable_spans=unavailable_spans,
            )
        if len(usage_atoms) == 1:
            if usage_tombstones:
                self._append_batch(family, usage_tombstones=usage_tombstones)
            return
        middle = len(usage_atoms) // 2
        self._append_batch(family, usage_atoms=usage_atoms[:middle])
        self._append_batch(family, usage_atoms=usage_atoms[middle:])
        if usage_tombstones:
            self._append_batch(family, usage_tombstones=usage_tombstones)

    @staticmethod
    def _valid_generation(value: object) -> bool:
        return not isinstance(value, bool) and isinstance(value, int) and value >= 0

    @staticmethod
    def _terminal_lease_failure(response: Mapping[str, object]) -> bool:
        return (
            response.get("status") == "upgrade_required"
            or response.get("error_code") == "upgrade_required"
        )

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def _record_failure(self, kind: str, retry_delay: float = 0.0) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_kind = _bounded_kind(kind, "RuntimeFailure")
            self._retry_delay_seconds = retry_delay
            self._next_retry_at = (
                self._monotonic() + retry_delay
                if retry_delay > 0
                else 0.0
            )
            self._phase = "backoff" if retry_delay > 0 else "blocked"

    def _clear_retry(self) -> None:
        with self._lock:
            self._retry_delay_seconds = 0.0
            self._next_retry_at = 0.0

    def _record_running(self) -> None:
        with self._lock:
            self._clear_retry()
            self._last_failure_kind = ""
            self._phase = "running"

    def _release(self, lease_id: str, *, preserve_failure: bool = False) -> None:
        owner = release_local_service_lease_eventually(
            (
                lambda current_lease_id: self.client.release_lease(
                    current_lease_id,
                    bypass_cached_upgrade=True,
                )
            )
            if preserve_failure
            else self.client.release_lease,
            lease_id,
            expected_errors=EXPECTED_SUPERVISOR_ERRORS,
        )
        if not owner.completed.is_set() and not preserve_failure:
            self._record_failure("LeaseReleaseFailed")

    def _register_collector_context(self, generation: int) -> bool:
        if self._collector_context is None:
            return True
        context = self._collector_context()
        try:
            pid = int(context["pid"])
            port = int(context["port"])
            owner_generation = int(context["owner_generation"])
            # Where statsd pushes this process's own CPU sample. Missing or blank is a broken
            # handshake, not a degraded one: statsd would run its 1s sampler and drop every
            # sample, which is the failure this field exists to remove.
            control_socket = str(context["control_socket"]).strip()
        except (KeyError, TypeError, ValueError):
            self._record_failure("InvalidCollectorContext")
            return False
        if not control_socket:
            self._record_failure("InvalidCollectorContext")
            return False
        if owner_generation != generation:
            self._record_failure("StaleCollectorContext")
            return False
        try:
            response = self.client.register_collector_context(
                pid=pid, port=port, owner_generation=owner_generation, control_socket=control_socket,
            )
        except EXPECTED_SUPERVISOR_ERRORS as error:
            self._record_failure(type(error).__name__)
            return False
        if response.get("ok") is not True:
            self._record_failure("CollectorContextUnavailable")
            return False
        return True

    def _stop_scheduler_and_release(self, *, preserve_failure: bool = False) -> None:
        with self._lock:
            lease_id = self._lease_id
        self.scheduler.stop()
        while any(item.alive for item in self.scheduler.status().values()):
            self.scheduler.stop()
        if lease_id:
            # Cleanup cannot replace the failure that forced a terminal demotion. Once the client
            # is fenced, release itself returns that same cached refusal and has no newer cause.
            self._release(lease_id, preserve_failure=preserve_failure)
        with self._lock:
            if self._lease_id == lease_id:
                self._lease_id = ""

    def _wait_for_retry(self, delay: float) -> bool:
        return self._stop_event.wait(delay)

    def _supervise(self) -> None:
        retry_delay = self._retry_initial_seconds
        scheduler_active = False
        try:
            while not self._stop_event.is_set():
                generation = self._owner_generation()
                if not self._valid_generation(generation):
                    self._clear_retry()
                    self._set_phase("waiting_owner")
                    self._stop_event.wait(self._owner_check_seconds)
                    continue
                self._set_phase("acquiring_lease")
                try:
                    lease = self.client.acquire_lease()
                except EXPECTED_SUPERVISOR_ERRORS as error:
                    self._record_failure(type(error).__name__, retry_delay)
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
                    continue
                lease_id = lease.get("lease_id")
                if lease.get("ok") is not True or not isinstance(lease_id, str) or not lease_id:
                    if self._terminal_lease_failure(lease):
                        self._record_failure("UpgradeRequired")
                        return
                    self._record_failure("LeaseUnavailable", retry_delay)
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
                    continue
                if self._stop_event.is_set() or self._owner_generation() != generation:
                    self._release(lease_id)
                    continue
                if not self._register_collector_context(generation):
                    self._release(lease_id)
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
                    continue
                with self._lock:
                    self._lease_id = lease_id
                    self._phase = "starting_scheduler"
                try:
                    scheduler_active = self.scheduler.start()
                except EXPECTED_SUPERVISOR_ERRORS as error:
                    self._record_failure(type(error).__name__, retry_delay)
                    self._stop_scheduler_and_release()
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
                    continue
                if not scheduler_active:
                    self._stop_scheduler_and_release()
                    if self._owner_generation() != generation:
                        continue
                    self._record_failure("SchedulerStartFailed", retry_delay)
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
                    continue
                retry_delay = self._retry_initial_seconds
                self._record_running()
                lease_failed = False
                terminal_lease_failure = False
                while (
                    not self._stop_event.wait(self._owner_check_seconds)
                    and self._owner_generation() == generation
                ):
                    try:
                        renewed = self.client.renew_lease(lease_id)
                    except EXPECTED_SUPERVISOR_ERRORS as error:
                        self._record_failure(type(error).__name__, retry_delay)
                        lease_failed = True
                        break
                    renewed_id = renewed.get("lease_id")
                    if renewed.get("ok") is not True or not isinstance(renewed_id, str) or not renewed_id:
                        terminal_lease_failure = self._terminal_lease_failure(renewed)
                        self._record_failure(
                            "UpgradeRequired" if terminal_lease_failure else "LeaseUnavailable",
                            0.0 if terminal_lease_failure else retry_delay,
                        )
                        lease_failed = True
                        break
                    # A replacement daemon cannot know the old ID and atomically
                    # returns a new one, keeping the collector owner leased.
                    lease_id = renewed_id
                    with self._lock:
                        self._lease_id = lease_id
                self._set_phase(
                    "stopping"
                    if self._stop_event.is_set()
                    else ("blocked" if terminal_lease_failure else "demoting")
                )
                self._stop_scheduler_and_release(
                    preserve_failure=terminal_lease_failure,
                )
                scheduler_active = False
                if terminal_lease_failure:
                    return
                if (
                    lease_failed
                    and not self._stop_event.is_set()
                    and self._owner_generation() == generation
                ):
                    if self._wait_for_retry(retry_delay):
                        break
                    retry_delay = min(self._retry_max_seconds, retry_delay * 2)
        finally:
            if scheduler_active or self._lease_id:
                self._stop_scheduler_and_release()
            with self._lock:
                if self._stop_event.is_set():
                    self._phase = "stopped"

    def start(self) -> bool:
        """Start one non-blocking lease supervisor for the elected owner."""

        with self._lock:
            if self._phase == "blocked":
                return False
            if self._supervisor is not None and self._supervisor.is_alive():
                return False
            self._stop_event = threading.Event()
            self._phase = "starting"
            supervisor = threading.Thread(
                target=self._supervise,
                name="stats-current-supervisor",
                daemon=True,
            )
            self._supervisor = supervisor
            supervisor.start()
            return True

    def stop(self) -> None:
        """Stop appends before releasing the daemon lease."""

        with self._lock:
            supervisor = self._supervisor
            if supervisor is None:
                self._phase = "stopped"
                return
            self._phase = "stopping"
            self._stop_event.set()
        if supervisor is not threading.current_thread():
            supervisor.join(timeout=SUPERVISOR_JOIN_SECONDS)
        with self._lock:
            if not supervisor.is_alive() and self._supervisor is supervisor:
                self._supervisor = None
                self._phase = "stopped"

    def wake(self, family: str) -> bool:
        return self.scheduler.wake(family)

    def _service_status(self) -> dict[str, object]:
        try:
            response = self.client.status()
        except EXPECTED_SUPERVISOR_ERRORS:
            return {"ok": False, "status": "unavailable"}
        if response.get("ok") is True:
            return dict(response)
        safe: dict[str, object] = {"ok": False}
        for name in ("status", "error_code"):
            if response.get(name):
                safe[name] = _bounded_kind(response[name], "unavailable")
        for name in (
            "required_protocol_version",
            "required_schema_generation",
            "required_build",
        ):
            value = response.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                safe[name] = value
            elif isinstance(value, str) and value.isdigit():
                safe[name] = value
        safe.setdefault("status", "unavailable")
        return safe

    def status(self) -> dict[str, object]:
        with self._lock:
            leased = bool(self._lease_id)
            supervisor = self._supervisor
            phase = self._phase
            failure_count = self._failure_count
            last_failure_kind = self._last_failure_kind
            retry_delay = self._retry_delay_seconds
            retry_in = max(0.0, self._next_retry_at - self._monotonic()) if self._next_retry_at else 0.0
        family_status = {
            family: {
                "cadence_seconds": item.cadence_seconds,
                "attempts": item.attempts,
                "successes": item.successes,
                "failures": item.failures,
                "late_cycles": item.late_cycles,
                "missed_cycles": item.missed_cycles,
                "last_runtime_seconds": item.last_runtime_seconds,
                "last_attempt_at": item.last_attempt_at,
                "last_success_at": item.last_success_at,
                "last_failure": _bounded_kind(item.last_failure, "CollectorFailure") if item.last_failure else "",
                "alive": item.alive,
                "running": item.running,
                "epoch": item.epoch,
            }
            for family, item in self.scheduler.status().items()
        }
        return {
            "leased": leased,
            "supervisor": {
                "phase": phase,
                "alive": supervisor is not None and supervisor.is_alive(),
                "failure_count": failure_count,
                "last_failure": last_failure_kind,
                "retry_delay_seconds": retry_delay,
                "retry_in_seconds": round(retry_in, 3),
            },
            "families": family_status,
            "service": self._service_status(),
        }

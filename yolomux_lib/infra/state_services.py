# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small state owners extracted from the HTTP/tmux application coordinator."""

from __future__ import annotations

from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
import threading
from typing import Any
from typing import Callable

from .types import SessionFilesPayload


class ClientWatchRootValidationError(ValueError):
    """A versioned browser watch-root descriptor violates its wire contract."""


@dataclass
class ClientWatchDescriptor:
    """One browser's watch demand, owned by its client-event stream lifecycle."""

    expires_at: float
    descriptor_generation: int = 1
    roots: tuple[str, ...] = ()
    root_surfaces_version: int = 0
    root_surfaces: tuple[tuple[str, tuple[str, ...]], ...] = ()
    files: tuple[str, ...] = ()
    background_files: tuple[str, ...] = ()
    context_items: tuple[dict[str, Any], ...] = ()
    session_files: tuple[dict[str, Any], ...] = ()
    activity_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientEventWatcherRecord:
    worker: threading.Thread | None = None
    snapshot_worker: threading.Thread | None = None
    status_generation_worker: threading.Thread | None = None
    status_generation_stop_event: threading.Event = field(default_factory=threading.Event)
    status_generation_lease_id: str = ""
    status_generation: int = 0
    status_generation_retry_at: float = 0.0
    watchd_worker: threading.Thread | None = None
    watchd_stop_event: threading.Event = field(default_factory=threading.Event)
    watchd_lease_id: str = ""
    watchd_pid: int = 0
    watchd_epoch: str = ""
    watchd_revision: int = 0
    watchd_synced_generation: int = 0
    watchd_applied_generation: int = 0
    watchd_active_generation: int = 0
    watchd_failed_generation: int = 0
    watchd_descriptor_ids: set[str] = field(default_factory=set)
    watchd_descriptor_generations: dict[str, int] = field(default_factory=dict)
    watchd_state: str = "starting"
    watchd_next_failure_episode: int = 1
    watchd_failure_episode: int = 0
    watchd_failure_started_at: float = 0.0
    watchd_failure_count: int = 0
    watchd_failure_delivery: str = ""
    watchd_failure_action: str = ""
    watchd_failure_error_code: str = ""
    # Operator-actionable measurements belonging to the FIRST failure of an episode, captured with
    # the code rather than re-read at publish time: a later failure in the same episode can carry
    # different numbers, and the episode reports the one it opened on.
    watchd_failure_error_detail: str = ""
    watchd_failure_published: bool = False
    wake_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    filesystem_healthy: bool = False
    filesystem_roots: tuple[str, ...] = ()
    next_signature_poll_at: float = 0.0
    next_file_poll_at: float = 0.0
    next_background_file_poll_at: float = 0.0
    next_attention_ack_poll_at: float = 0.0
    next_tmux_signal_poll_at: float = 0.0
    tmux_signal_refresh_at: float = 0.0
    next_watched_pr_poll_at: float = 0.0
    next_yoagent_job_poll_at: float = 0.0
    next_search_progress_poll_at: float = 0.0
    # Fixed-vocabulary recurring-work diagnostics. Keys are supplied only by the
    # app's static catalog, so a caller can never create path/user/cardinality state.
    recurring_work: dict[str, dict[str, float | int]] = field(default_factory=dict)

    def watchd_rebuild_window_open(self) -> bool:
        """Whether watchd owes this client an activation for a generation it bumped.

        watchd activates a watch generation only after the native registration
        for it has completed, and that registration is one call which holds the
        daemon's interpreter lock and answers nothing while it runs. A synced
        generation the daemon has not yet activated is therefore exactly the
        interval in which any request will be answered late, and it is knowable
        from responses this client has already received: it does not have to
        wait to be told by a daemon that cannot currently reply.
        """
        return self.watchd_active_generation < self.watchd_synced_generation


@dataclass
class FilesystemWatchReadyProductRecord:
    """One latest watch product with every equivalent stable cache key."""

    keys: frozenset[str] = field(default_factory=frozenset)
    products: tuple[dict[str, Any], ...] = ()


@dataclass
class JobdOperationFlight:
    """One keyed completion reservation shared by equivalent accepted-operation callers."""

    lane: str
    key: str
    deadline_at: float
    future: Future[Any] = field(default_factory=Future)
    owner_ready: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    owner_operation_id: str = ""
    participants: int = 1

    def accept_owner(self, operation_id: str) -> None:
        self.owner_operation_id = str(operation_id)
        self.owner_ready.set()

    def cancel_owner(self) -> None:
        self.cancelled.set()
        self.owner_ready.set()

    def wait_for_owner(self) -> str:
        self.owner_ready.wait()
        return "" if self.cancelled.is_set() else self.owner_operation_id


@dataclass
class StatsSampleRecord:
    last_monotonic: float | None = None
    last_process_time: float | None = None
    last_system_cpu_times: tuple[float, float] | None = None
    cached_monotonic: float | None = None
    cached_payload: dict[str, Any] | None = None


@dataclass
class CpuBudgetRecord:
    """Own sustained server-CPU warning state for the one-second CPU sampler."""

    exceeded_since: float = 0.0
    last_warning_at: float = 0.0
    warning_emitted: bool = False
    current_percent: float = 0.0
    top_consumers: list[dict[str, Any]] = field(default_factory=list)
    # CPU actually consumed since exceeded_since, integrated from the same 1 Hz samples that
    # decide the breach. The warning needs it as the denominator for its own attribution: a
    # consumer list without one reads as an explanation even when it covers a fraction of a
    # percent of the CPU being reported.
    cpu_ms_since_exceeded: float = 0.0
    last_sample_at: float = 0.0


@dataclass
class TranscriptsPayloadCacheRecord:
    stored_at: float | None = None
    payload: dict[str, Any] | None = None
    generation: int = 0
    worker: object | None = None
    worker_started_at: float | None = None
    publish_requested: bool = False
    # One queued follow-up build for callers the in-flight build started too early to answer. It is
    # a single flag, not a queue: repeated forced refreshes during one build cost one extra build.
    rebuild_requested: bool = False
    rebuild_publish: bool = False

    def release_worker(self) -> None:
        """Release the single-flight build guard and every intent scoped to that worker.

        The three fields move together or not at all: ``worker_started_at`` decides whether a later
        caller may adopt the in-flight build, and ``publish_requested`` decides whether that build
        publishes. Leaving either behind when the worker goes away hands the next worker an intent
        belonging to a caller that has already been answered. Callers hold the cache lock.
        """

        self.worker = None
        self.worker_started_at = None
        self.publish_requested = False


@dataclass
class TabberActivityCacheRecord:
    stored_at: float | None = None
    payload: dict[str, Any] | None = None
    source_signature: str = ""
    # The background owner publishes a compact invalidation only after a new
    # cache generation is readable. Keep that one delivery watermark beside the
    # cache rather than a parallel app-level signature map.
    published_source_signature: str = ""
    refresh_worker: threading.Thread | None = None
    session_signatures: dict[str, str] = field(default_factory=dict)
    session_rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    inflight_by_key: dict[tuple[float, str], Future[dict[str, Any]]] = field(default_factory=dict)
    session_scope: str = ""
    session_file_hours: float = 0.0


@dataclass
class SessionFilesWorkRecord:
    future: Future[tuple[SessionFilesPayload, HTTPStatus, bool, float]] = field(default_factory=Future)
    owner_thread_id: int | None = None
    worker: threading.Thread | None = None
    stable_signature: str = ""
    stable_generation: int = 0


@dataclass
class SessionFilesGitSnapshotRecord:
    future: Future[tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=Future)
    snapshot: dict[str, Any] | None = None


@dataclass
class SessionFilesDiskPruneRecord:
    next_at: float = 0.0
    running: bool = False
    last_result: dict[str, Any] = field(default_factory=dict)
    worker: threading.Thread | None = None


@dataclass
class TabberActivityWarmerRecord:
    thread: threading.Thread | None = None
    running: bool = False
    consumer_until: float = 0.0
    refresh_due_at: float = 0.0
    refresh_triggers: set[str] = field(default_factory=set)
    # Set by a returning consumer (or teardown) to unpark a warmer that idled
    # out of demand; parking instead of exiting keeps one thread and zero
    # recurring work with no request-path thread creation.
    wake: threading.Event = field(default_factory=threading.Event)


@dataclass
class StatsCollectionState:
    """Own shared in-process state used by the current stats collectors."""

    sample_lock: threading.Lock = field(default_factory=threading.Lock)
    sample_record: StatsSampleRecord = field(default_factory=StatsSampleRecord)
    cpu_budget_record: CpuBudgetRecord = field(default_factory=CpuBudgetRecord)
    agent_activity_lock: threading.Lock = field(default_factory=threading.Lock)
    agent_activity_state: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SessionFilesService:
    """Own cache, work, and pruning records for the session-files payload pipeline."""

    cache_lock: threading.RLock = field(default_factory=threading.RLock)
    compute_slot_condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    active_compute_slots: int = 0
    peak_compute_slots: int = 0
    cache: dict[tuple[Any, ...], tuple[float, tuple[SessionFilesPayload, HTTPStatus]]] = field(default_factory=dict)
    work_records: dict[tuple[Any, ...], SessionFilesWorkRecord] = field(default_factory=dict)
    next_stable_generation: int = 0
    latest_stable_generations: dict[str, int] = field(default_factory=dict)
    git_snapshot_records: dict[tuple[Any, ...], SessionFilesGitSnapshotRecord] = field(default_factory=dict)
    # In-flight single-flight for git_snapshot_identity: the signature (a full
    # `git status --untracked-files=all` + index/refs read) ran once per caller
    # BEFORE the snapshot single-flight it protects, so a burst of concurrent
    # session/range/ref views paid it N times. Concurrent callers share ONE
    # computation via a future; the entry is removed when it completes, so
    # sequential calls still recompute and freshness is never delayed.
    git_identity_futures: dict[tuple[Any, ...], Future] = field(default_factory=dict)
    # Repository-state record (keyed by canonical Git root / identity key):
    # the native filesystem watcher bumps a repo's dirty generation on worktree
    # or Git-metadata events, and a warm request whose cached identity carries
    # the current generation reuses it WITHOUT running any Git command. Entries
    # also carry their compute time so a slow safety reconciliation bound
    # recomputes even if an event was missed.
    repo_dirty_generations: dict[str, int] = field(default_factory=dict)
    repo_identity_cache: dict[tuple[Any, ...], tuple[int, float, tuple[Any, ...]]] = field(default_factory=dict)
    disk_prune_lock: threading.Lock = field(default_factory=threading.Lock)
    disk_prune_record: SessionFilesDiskPruneRecord = field(default_factory=SessionFilesDiskPruneRecord)
    accepting_work: bool = True
    work_condition: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.work_condition = threading.Condition(self.cache_lock)

    def allow_work(self) -> None:
        with self.work_condition:
            self.accepting_work = True

    def reserve_work(self, key: tuple[Any, ...], stable_signature: str) -> SessionFilesWorkRecord | None:
        """Reserve background work without letting worker start order redefine freshness."""
        with self.work_condition:
            if not self.accepting_work:
                return None
            if key in self.work_records:
                return None
            record = SessionFilesWorkRecord(owner_thread_id=None)
            self.assign_stable_generation(record, stable_signature)
            self.work_records[key] = record
            return record

    def start_reserved_worker(
        self,
        key: tuple[Any, ...],
        record: SessionFilesWorkRecord,
        worker: threading.Thread,
    ) -> bool:
        """Bind and start the exact reserved worker before lifecycle teardown can observe it."""

        with self.work_condition:
            if not self.accepting_work or self.work_records.get(key) is not record:
                return False
            record.worker = worker
            try:
                worker.start()
            except RuntimeError:
                record.worker = None
                if self.work_records.get(key) is record:
                    self.work_records.pop(key, None)
                self.release_stable_generation(record)
                self.work_condition.notify_all()
                raise
            return True

    def claim_work(self, key: tuple[Any, ...], thread_id: int | None, *, reserved: bool = False, stable_signature: str = "") -> tuple[SessionFilesWorkRecord, bool]:
        with self.cache_lock:
            record = self.work_records.get(key)
            if record is None:
                record = SessionFilesWorkRecord(owner_thread_id=thread_id)
                self.assign_stable_generation(record, stable_signature)
                self.work_records[key] = record
                return record, True
            if reserved and record.owner_thread_id is None:
                record.owner_thread_id = thread_id
                return record, True
            return record, record.owner_thread_id == thread_id

    def assign_stable_generation(self, record: SessionFilesWorkRecord, signature: str) -> None:
        if not signature or record.stable_generation:
            return
        self.next_stable_generation += 1
        record.stable_signature = signature
        record.stable_generation = self.next_stable_generation
        self.latest_stable_generations[signature] = record.stable_generation

    def stable_generation_is_current(self, record: SessionFilesWorkRecord) -> bool:
        with self.cache_lock:
            return (
                not record.stable_signature
                or self.latest_stable_generations.get(record.stable_signature) == record.stable_generation
            )

    def release_stable_generation(self, record: SessionFilesWorkRecord) -> None:
        with self.cache_lock:
            if (
                record.stable_signature
                and self.latest_stable_generations.get(record.stable_signature) == record.stable_generation
            ):
                self.latest_stable_generations.pop(record.stable_signature, None)

    def finish_work(self, key: tuple[Any, ...], record: SessionFilesWorkRecord) -> None:
        with self.work_condition:
            if self.work_records.get(key) is record and record.worker is None:
                self.work_records.pop(key, None)
            self.release_stable_generation(record)
            self.work_condition.notify_all()

    def finish_reserved_worker(
        self,
        key: tuple[Any, ...],
        record: SessionFilesWorkRecord,
        worker: threading.Thread,
    ) -> None:
        with self.work_condition:
            if record.worker is worker:
                record.worker = None
            if self.work_records.get(key) is record:
                self.work_records.pop(key, None)
            self.release_stable_generation(record)
            self.work_condition.notify_all()

    def wait_for_idle(self, timeout: float) -> bool:
        """Wait until every service-owned background worker has returned."""

        with self.work_condition:
            return self.work_condition.wait_for(
                lambda: not any(record.worker is not None for record in self.work_records.values()),
                timeout=max(0.0, float(timeout)),
            )

    def cancel_all_work(self) -> None:
        """Fence new work and join former-owner workers before returning."""
        with self.work_condition:
            self.accepting_work = False
            self.latest_stable_generations.clear()
            workers = [
                record.worker
                for record in self.work_records.values()
                if record.worker is not None and record.worker is not threading.current_thread()
            ]
            self.work_records = {
                key: record for key, record in self.work_records.items() if record.worker is not None
            }
        for worker in workers:
            worker.join()
        with self.work_condition:
            self.work_records.clear()
            self.work_condition.notify_all()

    def acquire_compute_slot(self, limit: int) -> None:
        """Queue distinct cold rebuilds without defeating per-key single-flight."""
        maximum = max(1, int(limit))
        with self.compute_slot_condition:
            while self.active_compute_slots >= maximum:
                self.compute_slot_condition.wait()
            self.active_compute_slots += 1
            self.peak_compute_slots = max(self.peak_compute_slots, self.active_compute_slots)

    def release_compute_slot(self) -> None:
        with self.compute_slot_condition:
            if self.active_compute_slots <= 0:
                raise RuntimeError("session-files compute slot released without an owner")
            self.active_compute_slots -= 1
            self.compute_slot_condition.notify()


# The completion service runs the product-poll thread that waits for an accepted jobd operation
# to terminalize.  Its lanes mirror jobd's own executor lanes so an interactive point read or a
# bounded mutation completion never queues behind bulk completion polls: a bulk lane held at
# capacity (four fs-batch/watch-diff/session-files polls that have not finished) previously
# occupied every worker of the one shared pool, so the fifth submission -- a point read or a
# `mkdir` -- was accepted yet could not RUN until a bulk worker freed.  `point` and `mutation`
# get their own bounded workers and their own admission slots so cross-class isolation holds at
# the completion boundary too, not only inside jobd.  Everything that is neither a coalescable
# point read nor a bounded mutation (fs-batch, watch-diff, session-files, transcript/context,
# unbounded reads and writes) shares the `bulk` lane.
JOBD_OPERATION_LANES: tuple[str, ...] = ("point", "mutation", "bulk")


def jobd_operation_lane(priority: str) -> str:
    """Map one jobd submission priority onto its single completion-service lane.

    Priority is computed by the caller BEFORE it reserves, so a completion never lands in a lane
    that does not describe its work.  `point` and `mutation` are the two bounded interactive
    classes; every other priority (`interactive`, `freshness`, `maintenance`, or any bulk work)
    routes to the shared `bulk` lane.
    """
    if priority in JOBD_OPERATION_LANES and priority != "bulk":
        return priority
    return "bulk"


class JobdOperationReservation:
    """One held completion-lane slot with EXACTLY-ONCE release.

    A reservation is a handle, not a bare counter: the slot it took is released through this
    object, the release is idempotent under its own lock, and the completion callback and every
    error path route through the same `release()`.  A double release (manual cleanup racing the
    done-callback) is therefore a no-op rather than an over-release that would let the lane admit
    beyond its bound.
    """

    __slots__ = ("_service", "lane", "_lock", "_released")

    def __init__(self, service: JobdOperationService, lane: str) -> None:
        self._service = service
        self.lane = lane
        self._lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._service._release_lane_slot(self.lane)

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released


@dataclass
class JobdOperationService:
    """Bound accepted-operation completion work independently of HTTP handler threads.

    `worker_limit` and `operation_limit` are PER LANE, so each named lane owns its own bounded
    thread pool and its own admission slots.  A bulk lane saturated at capacity cannot occupy a
    single worker of the point or mutation lane.
    """

    worker_limit: int = 4
    operation_limit: int = 64
    flight_participant_limit: int = 64
    lock: threading.RLock = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    futures: set[Future[Any]] = field(default_factory=set)
    flights: dict[tuple[str, str], JobdOperationFlight] = field(default_factory=dict)
    _lane_slots: dict[str, threading.BoundedSemaphore] = field(init=False)
    _lane_executors: dict[str, ThreadPoolExecutor | None] = field(init=False)
    _idle_condition: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.worker_limit = max(1, int(self.worker_limit))
        self.operation_limit = max(self.worker_limit, int(self.operation_limit))
        self.flight_participant_limit = max(1, int(self.flight_participant_limit))
        self._lane_slots = {lane: threading.BoundedSemaphore(self.operation_limit) for lane in JOBD_OPERATION_LANES}
        self._lane_executors = {lane: None for lane in JOBD_OPERATION_LANES}
        self._idle_condition = threading.Condition(self.lock)

    def reserve(self, lane: str = "bulk") -> JobdOperationReservation | None:
        """Admit one completion into a named lane, returning its release-owning handle or None.

        Returns None when the service is stopping or the lane is already at its bound; the caller
        maps that to a `service_busy` refusal.  The lane is chosen before admission, never after.
        """
        if lane not in self._lane_slots:
            raise ValueError(f"unknown jobd completion lane {lane!r}")
        with self.lock:
            if self.stop_event.is_set():
                return None
            if not self._lane_slots[lane].acquire(blocking=False):
                return None
            return JobdOperationReservation(self, lane)

    def claim(
        self,
        lane: str,
        key: str,
        deadline_at: float,
    ) -> tuple[JobdOperationFlight | None, JobdOperationReservation | None, bool]:
        """Atomically join one keyed flight or reserve the lane for its first caller."""
        if lane not in self._lane_slots:
            raise ValueError(f"unknown jobd completion lane {lane!r}")
        flight_key = (str(lane), str(key))
        with self.lock:
            existing = self.flights.get(flight_key)
            if existing is not None:
                if existing.participants >= self.flight_participant_limit:
                    return None, None, False
                existing.participants += 1
                return existing, None, False
            if self.stop_event.is_set() or not self._lane_slots[lane].acquire(blocking=False):
                return None, None, False
            reservation = JobdOperationReservation(self, lane)
            flight = JobdOperationFlight(lane=lane, key=str(key), deadline_at=float(deadline_at))
            self.flights[flight_key] = flight
            return flight, reservation, True

    def release_flight_participant(self, flight: JobdOperationFlight) -> None:
        """Return a claim whose caller failed before it persisted a receipt."""
        with self.lock:
            if flight.participants > 0:
                flight.participants -= 1

    def release_flight(self, flight: JobdOperationFlight) -> None:
        """Release only the current owner for this exact lane/key generation."""
        with self.lock:
            flight_key = (flight.lane, flight.key)
            if self.flights.get(flight_key) is flight:
                self.flights.pop(flight_key, None)

    def _release_lane_slot(self, lane: str) -> None:
        self._lane_slots[lane].release()

    def submit_reserved(self, reservation: JobdOperationReservation, function: Callable[..., Any], *args: Any) -> bool:
        """Run one accepted completion on its reserved lane; release the handle if it cannot start."""
        with self.lock:
            if self.stop_event.is_set():
                reservation.release()
                return False
            executor = self._lane_executors[reservation.lane]
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=self.worker_limit,
                    thread_name_prefix=f"jobd-op-{reservation.lane}",
                )
                self._lane_executors[reservation.lane] = executor
            try:
                future = executor.submit(function, *args)
            except RuntimeError:
                reservation.release()
                return False
            self.futures.add(future)
            future.add_done_callback(lambda completed: self._finish(completed, reservation))
            return True

    def _finish(self, future: Future[Any], reservation: JobdOperationReservation) -> None:
        with self._idle_condition:
            self.futures.discard(future)
            reservation.release()
            self._idle_condition.notify_all()

    def wait_for_idle(self, timeout: float) -> bool:
        """Wait for accepted completion work without cancelling its producers."""

        with self._idle_condition:
            return self._idle_condition.wait_for(lambda: not self.futures, timeout=max(0.0, float(timeout)))

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            executors = [executor for executor in self._lane_executors.values() if executor is not None]
            self._lane_executors = {lane: None for lane in JOBD_OPERATION_LANES}
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            self.flights.clear()


@dataclass
class IndexedRepoDiscoveryRecord:
    """Last completed jobd repository discovery plus one in-flight job."""

    indexed_dirs: tuple[str, ...] = ()
    roots: list[str] = field(default_factory=list)
    job_id: str = ""
    worker: threading.Thread | None = None
    refreshed_at: float = 0.0
    retry_at: float = 0.0
    root_generations: dict[str, int] = field(default_factory=dict)
    completed_generation_signature: tuple[tuple[str, int], ...] = ()


@dataclass
class ActivityTranscriptService:
    """Own payload caches for activity, transcript, tail, and context-item views."""

    tabber_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    tabber_cache_record: TabberActivityCacheRecord = field(default_factory=TabberActivityCacheRecord)
    tabber_warmer_record: TabberActivityWarmerRecord = field(default_factory=TabberActivityWarmerRecord)
    activity_summary_lock: threading.RLock = field(default_factory=threading.RLock)
    activity_summary_compute_lock: threading.Lock = field(default_factory=threading.Lock)
    activity_summary_cache: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    activity_summary_futures: dict[tuple[Any, ...], Future[dict[str, Any]]] = field(default_factory=dict)
    transcripts_payload_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    transcripts_payload_cache_record: TranscriptsPayloadCacheRecord = field(default_factory=TranscriptsPayloadCacheRecord)
    # One graph per session, fenced by the complete SessionInfo identity, watched repository
    # generations, and provider-cache generation. Watch revisions may rebuild the outer payload,
    # but they do not rebuild this expensive graph unless one of those inputs changed.
    work_graph_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    work_graph_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=dict)
    work_graph_futures: dict[tuple[str, tuple[Any, ...]], Future[dict[str, Any]]] = field(default_factory=dict)
    transcript_tail_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    transcript_tail_cache: dict[tuple[Any, ...], tuple[float, str]] = field(default_factory=dict)
    context_items_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    context_items_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = field(default_factory=dict)
    transcript_job_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    # Parsed, bounded worker facts only.  Do not retain transcript text here:
    # large JSONL belongs in jobd, never in the HTTP process cache.
    transcript_job_cache: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)
    transcript_job_records: dict[tuple[Any, ...], str] = field(default_factory=dict)
    indexed_repo_lock: threading.RLock = field(default_factory=threading.RLock)
    indexed_repo_record: IndexedRepoDiscoveryRecord = field(default_factory=IndexedRepoDiscoveryRecord)


@dataclass
class ClientWatchService:
    """Own watcher snapshots, revisions, and lifecycle records independently of HTTP routes."""

    lock: threading.RLock = field(default_factory=threading.RLock)
    # Fixed, bounded, monotonic counts at the three expensive refresh-fanout boundaries. These
    # are process-lifetime diagnostics, not request metrics and never carry keys from callers.
    owner_invocations: dict[str, int] = field(default_factory=lambda: {
        "session_discovery": 0,
        "transcript_tail_scan": 0,
        "session_files_materialization": 0,
        "jobd_work_graph_rebuild": 0,
    })
    # Every descriptor is keyed by the browser's existing client-event client_id.
    # Keeping the demand here rather than in route-local globals prevents one tab's
    # Finder state from replacing another tab's watch set.
    descriptors: dict[str, ClientWatchDescriptor] = field(default_factory=dict)
    initialized: bool = False
    settings_signature: tuple[Any, ...] | None = None
    # The transcript set the watchd descriptors carry, with the tmux topology signature and time
    # it was derived from. Descriptor leases renew on every bridge long-poll, while expensive
    # session discovery runs only for a topology change or watchd's bounded reconciliation.
    watchd_transcripts: list[str] = field(default_factory=list)
    watchd_transcripts_signature: str = ""
    watchd_transcripts_at: float = 0.0
    transcripts_signature: tuple[Any, ...] | None = None
    transcript_content_signature: tuple[Any, ...] | None = None
    filesystem_signature: tuple[Any, ...] | None = None
    filesystem_event_generation: int = 0
    watchd_repo_generations: dict[str, int] = field(default_factory=dict)
    file_signature: tuple[Any, ...] | None = None
    background_file_signature: tuple[Any, ...] | None = None
    auto_approve_signature: str = ""
    auto_approve_payload: dict[str, Any] | None = None
    attention_ack_rev: int = -1
    tmux_signal_signature: str = ""
    tmux_signal_payload: dict[str, Any] | None = None
    tmux_signal_removal_event: dict[str, Any] = field(default_factory=dict)
    watched_prs_signature: str = ""
    context_item_payload_signatures: dict[str, str] = field(default_factory=dict)
    session_file_payload_signatures: dict[str, str] = field(default_factory=dict)
    transcripts_payload_signature: str = ""
    activity_summary_signature: str = ""
    filesystem_history: list[dict[str, Any]] = field(default_factory=list)
    filesystem_ready_product: FilesystemWatchReadyProductRecord = field(default_factory=FilesystemWatchReadyProductRecord)
    event_watcher_record: ClientEventWatcherRecord = field(default_factory=ClientEventWatcherRecord)
    # Bounded (one entry per trigger reason, not per event) count of jobd-product-backed refreshes
    # actually published by the server-side watch loop, keyed by the same `trigger` strings already
    # passed to publish_session_files_ready_events/publish_context_items_ready_events.
    invalidation_counts: dict[str, int] = field(default_factory=dict)

    def note_owner_invocation(self, owner: str) -> None:
        with self.lock:
            if owner not in self.owner_invocations:
                raise ValueError(f"unknown refresh-fanout owner {owner!r}")
            self.owner_invocations[owner] += 1

    def owner_invocation_snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(self.owner_invocations)

    def snapshot(self, now: float | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        with self.lock:
            expired = [client_id for client_id, descriptor in self.descriptors.items() if now is not None and descriptor.expires_at <= now]
            for client_id in expired:
                self.descriptors.pop(client_id, None)
            descriptors = [self.descriptors[client_id] for client_id in sorted(self.descriptors)]
            context_items: list[dict[str, Any]] = []
            session_files: list[dict[str, Any]] = []
            seen_context: set[tuple[str, int]] = set()
            seen_session_files: set[str] = set()
            activity_summaries = [dict(descriptor.activity_summary) for descriptor in descriptors if descriptor.activity_summary.get("visible") is True]
            for descriptor in descriptors:
                for item in descriptor.context_items:
                    key = (str(item.get("session") or ""), int(item.get("messages") or 0))
                    if not key[0] or key in seen_context:
                        continue
                    seen_context.add(key)
                    context_items.append(dict(item))
                for item in descriptor.session_files:
                    key = repr(sorted(item.items()))
                    if key in seen_session_files:
                        continue
                    seen_session_files.add(key)
                    session_files.append(dict(item))
            # The current activity payload has one broadcast locale/scope. Preserve that
            # existing contract deterministically while the descriptor collection prevents
            # unrelated clients from clearing visible demand.
            activity_summary = activity_summaries[0] if activity_summaries else {}
            return context_items, session_files, activity_summary

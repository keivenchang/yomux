# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small state owners extracted from the HTTP/tmux application coordinator."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from http import HTTPStatus
import threading
from typing import Any

from .types import SessionFilesPayload


@dataclass
class ClientWatchFileRecord:
    expires_at: float
    background: bool


@dataclass
class ClientEventWatcherRecord:
    worker: threading.Thread | None = None
    directory_poll_worker: threading.Thread | None = None
    filesystem_worker: threading.Thread | None = None
    snapshot_worker: threading.Thread | None = None
    wake_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    filesystem_stop_event: threading.Event = field(default_factory=threading.Event)
    filesystem_reconfigure_event: threading.Event = field(default_factory=threading.Event)
    filesystem_healthy: bool = False
    filesystem_roots: tuple[str, ...] = ()
    filesystem_watch_paths: tuple[str, ...] = ()
    filesystem_transcripts: tuple[str, ...] = ()
    filesystem_skip_dirs: frozenset[str] = field(default_factory=frozenset)
    next_filesystem_retry_at: float = 0.0
    next_signature_poll_at: float = 0.0
    next_file_poll_at: float = 0.0
    next_background_file_poll_at: float = 0.0
    next_auto_poll_at: float = 0.0
    next_attention_ack_poll_at: float = 0.0
    next_tmux_signal_poll_at: float = 0.0
    next_watched_pr_poll_at: float = 0.0
    next_yoagent_job_poll_at: float = 0.0


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


@dataclass
class TranscriptsPayloadCacheRecord:
    stored_at: float | None = None
    payload: dict[str, Any] | None = None
    generation: int = 0
    worker: object | None = None


@dataclass
class TabberActivityCacheRecord:
    stored_at: float | None = None
    payload: dict[str, Any] | None = None
    source_signature: str = ""
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


@dataclass
class StatsHistoryService:
    """Own the elected HTTP-process metric scheduler and sample caches."""

    sample_lock: threading.Lock = field(default_factory=threading.Lock)
    sample_record: StatsSampleRecord = field(default_factory=StatsSampleRecord)
    cpu_budget_record: CpuBudgetRecord = field(default_factory=CpuBudgetRecord)
    agent_token_lock: threading.Lock = field(default_factory=threading.Lock)
    agent_token_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_activity_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_token_next_sample_at: float = 0.0
    agent_token_consumer_until: float = 0.0
    agent_token_bootstrap_pending: bool = True
    agent_token_worker: threading.Thread | None = None
    scheduler_lock: threading.RLock = field(default_factory=threading.RLock)
    scheduler_stop_event: threading.Event = field(default_factory=threading.Event)
    scheduler_threads: dict[str, threading.Thread] = field(default_factory=dict)
    scheduler_family_locks: dict[str, threading.Lock] = field(default_factory=dict)
    scheduler_wake_events: dict[str, threading.Event] = field(default_factory=dict)
    scheduler_generation: int = 0
    scheduler_diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SessionFilesService:
    """Own cache, work, and pruning records for the session-files payload pipeline."""

    cache_lock: threading.RLock = field(default_factory=threading.RLock)
    compute_slot_condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    active_compute_slots: int = 0
    peak_compute_slots: int = 0
    cache: dict[tuple[Any, ...], tuple[float, tuple[SessionFilesPayload, HTTPStatus]]] = field(default_factory=dict)
    work_records: dict[tuple[Any, ...], SessionFilesWorkRecord] = field(default_factory=dict)
    git_snapshot_records: dict[tuple[Any, ...], SessionFilesGitSnapshotRecord] = field(default_factory=dict)
    disk_prune_lock: threading.Lock = field(default_factory=threading.Lock)
    disk_prune_record: SessionFilesDiskPruneRecord = field(default_factory=SessionFilesDiskPruneRecord)

    def claim_work(self, key: tuple[Any, ...], thread_id: int | None, *, reserved: bool = False) -> tuple[SessionFilesWorkRecord, bool]:
        with self.cache_lock:
            record = self.work_records.get(key)
            if record is None:
                record = SessionFilesWorkRecord(owner_thread_id=thread_id)
                self.work_records[key] = record
                return record, True
            if reserved and record.owner_thread_id is None:
                record.owner_thread_id = thread_id
                return record, True
            return record, record.owner_thread_id == thread_id

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


@dataclass
class IndexedRepoDiscoveryRecord:
    """Last completed jobd repository discovery plus one in-flight job."""

    indexed_dirs: tuple[str, ...] = ()
    roots: list[str] = field(default_factory=list)
    job_id: str = ""
    worker: threading.Thread | None = None
    refreshed_at: float = 0.0
    retry_at: float = 0.0


@dataclass
class ActivityTranscriptService:
    """Own payload caches for activity, transcript, tail, and context-item views."""

    tabber_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    tabber_cache_record: TabberActivityCacheRecord = field(default_factory=TabberActivityCacheRecord)
    tabber_warmer_record: TabberActivityWarmerRecord = field(default_factory=TabberActivityWarmerRecord)
    activity_summary_lock: threading.RLock = field(default_factory=threading.RLock)
    activity_summary_cache: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    transcripts_payload_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    transcripts_payload_cache_record: TranscriptsPayloadCacheRecord = field(default_factory=TranscriptsPayloadCacheRecord)
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
    file_records: dict[str, ClientWatchFileRecord] = field(default_factory=dict)
    context_items: list[dict[str, Any]] = field(default_factory=list)
    session_files: list[dict[str, Any]] = field(default_factory=list)
    activity_summary: dict[str, Any] = field(default_factory=dict)
    initialized: bool = False
    settings_signature: tuple[Any, ...] | None = None
    transcripts_signature: tuple[Any, ...] | None = None
    transcript_content_signature: tuple[Any, ...] | None = None
    filesystem_signature: tuple[Any, ...] | None = None
    file_signature: tuple[Any, ...] | None = None
    background_file_signature: tuple[Any, ...] | None = None
    auto_approve_signature: str = ""
    attention_ack_rev: int = -1
    tmux_signal_signature: str = ""
    tmux_signal_payload: dict[str, Any] | None = None
    tmux_signal_removal_event: dict[str, Any] = field(default_factory=dict)
    watched_prs_signature: str = ""
    context_item_payload_signatures: dict[str, str] = field(default_factory=dict)
    session_file_payload_signatures: dict[str, str] = field(default_factory=dict)
    transcripts_payload_signature: str = ""
    activity_summary_signature: str = ""
    filesystem_payload_signature: str = ""
    filesystem_history: list[dict[str, Any]] = field(default_factory=list)
    filesystem_last_full_at: float = 0.0
    event_watcher_record: ClientEventWatcherRecord = field(default_factory=ClientEventWatcherRecord)

    def snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in self.context_items], [dict(item) for item in self.session_files], dict(self.activity_summary)

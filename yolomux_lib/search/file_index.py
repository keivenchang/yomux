"""Persistent, stale-while-revalidate file index for quick-open search.

The live `search_files` walk re-walks a root on every keystroke and is capped
(MAX_SEARCH_DIRS / MAX_SEARCH_FILES), so a huge root like ~/GPU is both slow
and incomplete. This module builds a per-root index of every file once (in a
background thread, respecting the same skip dirs), keeps it in memory + on disk,
and serves quick-open queries from it instantly with no per-query walk and no
50k coverage cap. It is an ACCELERATOR: callers fall back to the live walk while
an index is still building or on any error, so search never depends on it.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
from dataclasses import dataclass
from dataclasses import field
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from yolomux_lib.filesystem.io_ops import read_json_file
from yolomux_lib.filesystem import paths as filesystem_paths
from typing import Callable

from ..common import STATE_DIR
from ..common import start_thread_with_rollback
from ..infra.filesystem_preflight import preflight_mutable_roots
from ..infra.host_identity import process_start_identity
from ..infra.host_partition import host_partitioned_state_dir


def default_index_dir(state_dir: Path | None = None) -> Path:
    """Return the host-private root for reconstructible search WAL indexes."""
    return host_partitioned_state_dir(state_dir or STATE_DIR) / "search_index"


INDEX_DIR = default_index_dir()


def _bounded_env_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, value))


# Settings normally supply these values per root; environment defaults remain
# useful for standalone module callers and recovery before settings are loaded.
MAX_INDEX_FILES = _bounded_env_int("YOLOMUX_SEARCH_INDEX_MAX_FILES", 100_000, 1_000, 1_000_000)
MAX_PERSISTED_INDEX_FILES = _bounded_env_int("YOLOMUX_SEARCH_INDEX_PERSIST_MAX_FILES", 100_000, 1_000, 1_000_000)
MAX_PERSISTED_INDEX_BYTES = _bounded_env_int("YOLOMUX_SEARCH_INDEX_PERSIST_MAX_MB", 64, 1, 1_024) * 1024 * 1024
# The persistent indexer batches dirty paths for two seconds. Its writes are
# row deltas, so this can stay responsive without rewriting an entire index.
PERSIST_DEBOUNCE_SECONDS = 2.0
# Serve from the index immediately; rebuild in the background once it is older
# than this (stale-while-revalidate), which also prunes deleted files.
# A stale index remains immediately searchable while the owner refreshes it.  A
# short TTL turns ordinary Quick Open use into a recurring whole-tree walk.
INDEX_TTL_SECONDS = 30.0 * 60.0
# C11: bump when the on-disk storage shape changes so old/incompatible indexes rebuild for a clear reason.
# v5 adds a per-row `generation` column plus `directory_coverage` and `frontier`
# tables so a progressive breadth-first build can publish one directory at a time,
# fence an abandoned generation from overwriting a newer one, and resume an
# incomplete crawl at the shallowest pending directory after a restart. A v4 flat
# `entries` snapshot migrates in place (its rows stay searchable as a stale
# generation) rather than being dropped, so results survive the format bump.
# v6 binds every published snapshot to the `(st_dev, st_ino)` identity of the
# authorized root descriptor that produced it. Identity-less v5 stores are not
# readable because a pathname can be replaced by a different directory while
# retaining the same policy and durable-index key.
INDEX_FORMAT_VERSION = 6
# Directory coverage / frontier lifecycle states. A directory is `pending` while
# it sits in the frontier, `scanning` while its one listing runs, and `complete`
# or `failed` once its transaction reaches a terminal state for its generation.
COVERAGE_PENDING = "pending"
COVERAGE_SCANNING = "scanning"
COVERAGE_COMPLETE = "complete"
COVERAGE_FAILED = "failed"
# M11 freshness proof.  A shape-matched snapshot says nothing about whether the
# single writer that produced it still exists, and age alone is wrong in both
# directions: a 40-minute-old snapshot from a healthy idle producer is current,
# while a 10-second-old snapshot whose producer died 5 seconds later is not.
# The producer therefore stamps its own `(pid, process start time)` epoch into
# the snapshot metadata and refreshes a per-root heartbeat without rebuilding;
# a reader proves custody from /proc, never with a per-query RPC to the writer.
PRODUCER_HEARTBEAT_INTERVAL_SECONDS = 5.0
# The heartbeat (or a newer build) must be this recent for a live producer to
# still be vouching for the root it owns.  It bounds only producer custody, not
# snapshot content age, so an idle producer's older snapshot stays authoritative.
PRODUCER_VOUCH_MAX_AGE_SECONDS = 120.0
# One /proc probe per epoch per interval.  Quick Open queries per keystroke, and
# on platforms without /proc the identity reader falls back to `ps`.
PRODUCER_LIVENESS_CACHE_SECONDS = 2.0
# How long an accepted owner refresh may be reported as still in flight.
REFRESH_INFLIGHT_MAX_SECONDS = 60.0

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_ORPHANED = "orphaned"
FRESHNESS_MISSING = "missing"

PRODUCER_RUNNING = "running"
PRODUCER_NOT_RUNNING = "not_running"
PRODUCER_UNRECORDED = "unrecorded"
_BACKGROUND_OWNER_CHECKER: Callable[[str], bool] | None = None
_BACKGROUND_OWNER_REFRESH_REQUESTER: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
_BACKGROUND_INDEX_SEARCH_REQUESTER: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_BACKGROUND_INDEX_SEARCH_REQUEST: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "background_index_search_request",
    default=None,
)
AUTHORIZED_ROOT_IDENTITY_FIELD = "authorized_root_identity"
_BACKGROUND_OWNER_BYTES_RECORDER: Callable[[int], None] | None = None
_BACKGROUND_OWNER_DONE_NOTIFIER: Callable[[str, dict[str, Any]], None] | None = None
# Streaming Quick Open (step 5): the signal-only progress notifier. `indexd` calls it after a
# directory publication commits a NEW journal revision; app.py registers a sink that publishes a
# redacted `{scope_id, generation, revision, coverage}` frame onto the shared background-client-
# events bus. It is deliberately a sibling of the done-notifier: the done event carries a filesystem
# root, but this bus is globally persisted + fanned out + latest-per-resource-retained, so NOTHING
# that could disclose filesystem data (query, path, name, match) may ride on it.
_SEARCH_PROGRESS_NOTIFIER: Callable[[dict[str, Any]], None] | None = None
# At most one progress signal per root per this window; the LATEST revision is always delivered
# (a trailing emit), which is safe because SQLite retains the ordered deltas the client pulls by
# cursor. Monkeypatchable so a test does not have to wait a real 500ms.
SEARCH_PROGRESS_COALESCE_SECONDS = 0.5
_SEARCH_PROGRESS_LOCK = threading.Lock()
_SEARCH_PROGRESS_IDLE = threading.Condition(_SEARCH_PROGRESS_LOCK)
_SEARCH_PROGRESS_LAST_EMIT: dict[str, float] = {}
_SEARCH_PROGRESS_PENDING: dict[str, dict[str, Any]] = {}
_SEARCH_PROGRESS_TIMERS: dict[str, threading.Timer] = {}
_SEARCH_PROGRESS_ACTIVE_CALLBACKS = 0
# The ONLY coverage fields that may cross the shared bus: numeric progress + terminal flags, never a
# path or a name. The frame is built fresh from this allowlist so a caller cannot leak an extra key.
_SEARCH_PROGRESS_COVERAGE_KEYS: tuple[str, ...] = (
    "published_depth",
    "frontier_depth",
    "frontier_size",
    "entry_count",
    "full_coverage",
    "truncated",
)
# The breadth-first, directory-at-a-time full-build runner. `bfs_index` registers itself here at
# import time so a configured-root full build lists the root first and publishes each directory
# independently, instead of the whole-tree DFS `_walk_root_with_metrics`. file_index cannot import
# bfs_index at module scope (bfs_index imports file_index), so the one owner is injected the same
# way as the background-owner checker rather than reached through a function-local import.
_BFS_FULL_BUILD_RUNNER: Callable[..., bool] | None = None
SEARCH_INDEX_ROLE = "search-index"
LOGGER = logging.getLogger(__name__)

# These reason strings and the user-visible priority number MUST equal the ones `bfs_index` owns.
# `file_index` cannot import `bfs_index` (it imports this module), so the shared values live here as
# plain literals and are pinned to their one owner by a parity test
# (tests/test_bfs_index.py::test_reason_priority_constants_match_file_index). A safety refresh and a
# user-visible-demand promotion both drive the SAME breadth-first frontier; these are only the
# precedence labels, not a second scheduler or queue.
SAFETY_REFRESH_REASON = "full-safety-refresh"
USER_VISIBLE_DEMAND_REASON = "user-visible-demand"
USER_VISIBLE_DEMAND_PRIORITY = 2
HOT_CHANGE_REASON = "hot-change"
HOT_CHANGE_PRIORITY = 1
# Item 6 hot-path fairness. A continuously-hot root always has a dirty subtree, so `schedule_refreshes`
# would take the bounded incremental repair branch on every tick and NEVER run the low-priority
# breadth / safety reconciliation -- starving deeper-layer coverage and missed-event repair. After
# this many consecutive hot (dirty) repairs the scheduler yields exactly one `full-safety-refresh`
# (the lowest-priority, resumable, breadth-first pass that re-lists the whole tree and so supersedes
# the pending dirty subtrees) before it resumes hot repairs. This is the tested starvation bound; it
# is a count, not a timer, so the yield does not wait for the 1800s TTL.
HOT_REPAIR_STARVATION_BOUND = 8
# Cap the heat score so a pathological event storm cannot grow an unbounded counter.
HOT_MAX_SCORE = 10_000
# Heat is bounded and decays: with no new change evidence for this long a root is cold again, so an
# old burst cannot keep it "hot" (and cannot keep consuming the starvation budget) forever.
HOT_INACTIVITY_SECONDS = 90.0
# Coalesce a burst of Quick Open queries for the same root into one promotion dispatch, so a fast
# typist cannot spawn a thread per keystroke.
_PROMOTION_DEBOUNCE_SECONDS = 2.0
_PROMOTION_LOCK = threading.Lock()
_PROMOTION_LAST_DISPATCH: dict[str, float] = {}


def _skip_signature(skip_dirs: set[str], exclude_signature: str = "") -> str:
    # C11: the set of skipped directories is part of what an index means; if it changes, the cached
    # index no longer matches the requested coverage and must rebuild.
    suffix = f"|exclude:{exclude_signature}" if exclude_signature else ""
    return ",".join(sorted(skip_dirs)) + suffix


def _resolved_index_dir() -> Path:
    return INDEX_DIR.expanduser().resolve(strict=False)


def _path_is_index_storage(path: Path) -> bool:
    # Index walks never follow symlinks, so a lexical comparison avoids an
    # expensive realpath syscall for every candidate in a large tree.
    candidate = path.expanduser()
    index_dir = _resolved_index_dir()
    return candidate == index_dir or _path_is_within(candidate, index_dir)


def _build_exclude_path(exclude_path: Callable[[Path], bool] | None) -> Callable[[Path], bool]:
    def excluded(path: Path) -> bool:
        return _path_is_index_storage(path) or bool(exclude_path is not None and exclude_path(path))

    return excluded


def _disk_skip_signature(root: Path, skip_dirs: set[str], exclude_signature: str = "") -> str:
    signature = _skip_signature(skip_dirs, exclude_signature)
    resolved_root = root.expanduser().resolve(strict=False)
    index_dir = _resolved_index_dir()
    try:
        relative_index_dir = index_dir.relative_to(resolved_root)
    except ValueError:
        return signature
    return f"{signature}|internal-index-dir:{relative_index_dir.as_posix()}"

# (path, name, relative_path, size, mtime)
IndexEntry = tuple[str, str, str, int, int]


class RootIndex:
    def __init__(self, root: Path):
        self.root = root
        self.root_fd: int | None = None
        self.root_fd_identity: tuple[int, int] | None = None
        self.entries: list[IndexEntry] = []
        # The list is the in-memory search snapshot.  The map makes a normal
        # file save O(log n) list maintenance instead of a full filter/sort of
        # every indexed row.
        self.entry_by_path: dict[str, IndexEntry] = {}
        self.entries_signature = ""
        self.pending_exact_deletes: set[str] = set()
        self.pending_subtree_deletes: set[str] = set()
        self.pending_upserts: dict[str, IndexEntry] = {}
        self.pending_full_replace = False
        self.built_at = 0.0
        self.last_full_build_at = 0.0
        self.ready = False
        # P0-1: the tombstone identity THIS in-memory snapshot was published with, frozen atomically
        # with `ready` (set from `captured_tombstone_identity` at the ready publication, or from a
        # persisted snapshot's stamp on adoption). In-memory validity is decided by comparing this
        # frozen identity against the CURRENT durable tombstone through the SAME `_snapshot_is_tombstoned`
        # verdict the disk read path uses (`_root_index_is_tombstoned`), so a build that started before a
        # newer cross-process unindex -- and therefore stamped the OLD identity -- can no longer keep
        # serving deleted rows from RAM. ``None`` means "no tombstone was current when this was built".
        self.published_tombstone_identity: str | None = None
        # The root descriptor generation that produced `entries`. It is frozen
        # atomically with `ready`; a different descriptor installed for the same
        # pathname invalidates the rows before any serving or adoption path can
        # use them.
        self.published_root_identity: tuple[int, int] | None = None
        self.building = False
        self.build_generation = 0
        self.active_generation = 0
        self.completed_generation = 0
        self.last_error = ""
        self.truncated = False
        self.too_large = False
        self.build_duration_ms = 0.0
        self.cache_bytes = 0
        self.persisted = False
        self.persist_enabled = True
        self.persist_max_files = MAX_PERSISTED_INDEX_FILES
        self.persist_max_bytes = MAX_PERSISTED_INDEX_BYTES
        self.persist_pending = False
        self.last_persisted_at = 0.0
        self.max_files = MAX_INDEX_FILES
        self.refresh_seconds = INDEX_TTL_SECONDS
        self.skip_dirs: set[str] = set()
        self.exclude_path: Callable[[Path], bool] | None = None
        self.exclude_signature = ""
        self.dirty_paths: set[Path] = set()
        # Item 6 hot-path heat. `hot_score` is a bounded, decaying measure of recent concrete change
        # evidence for this root; `last_hot_at` anchors its decay; `consecutive_hot_repairs` counts
        # the incremental repairs run since the last breadth/safety yield so a forever-hot root still
        # periodically reconciles deeper layers. All three live on this one per-root owner and are
        # decayed by the one scheduler (`schedule_refreshes`); there is no second heat map.
        self.hot_score = 0.0
        self.last_hot_at = 0.0
        self.consecutive_hot_repairs = 0
        self.dirty_mark_batches = 0
        self.dirty_mark_paths = 0
        self.last_dirty_batch_paths = 0
        self.last_dirty_before_coalesce = 0
        self.last_dirty_after_coalesce = 0
        self.max_dirty_before_coalesce = 0
        self.build_count = 0
        self.full_build_count = 0
        self.incremental_build_count = 0
        self.scanned_entries = 0
        self.ignored_entries = 0
        self.write_bytes = 0
        self.disk_metadata_ready = False
        self.disk_entry_count = 0
        self.signature = ""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        # The ONE immutable per-worker lease for the build currently assigned to this object (P1-5).
        # `_start_build` installs it under `lock` before the thread becomes visible; the exiting worker
        # and any retirement identify THIS worker by the frozen lease, never by re-reading the mutable
        # `thread`/`completion`/`active_generation` a successor may have replaced. None means no worker
        # is assigned.
        self.assignment: "_WorkerAssignment | None" = None
        # Retirement is an explicit worker-owned state, not a call-site branch. When the index is
        # retired while its build worker still holds the pinned root fd (`clear_memory_indexes` /
        # `unindex`), `retiring` is set under `lock` alongside the generation fence, and the fd is
        # closed by the EXITING worker's own `finally` (`_finalize_worker_exit`) -- a deferred close,
        # never under the live worker. A never-built or already-exited index is closed directly.
        self.retiring = False
        # When a build worker is assigned, it gets ONE completion event, created/cleared BEFORE
        # `self.thread` becomes visible (`_start_build`) and set exactly once by the exiting worker's
        # `_finalize_worker_exit`. A batch retirement waits on THIS event against one shared deadline
        # instead of joining N threads with N timeouts. It starts SET: an index with no build in
        # flight is already "complete" for retirement purposes.
        self.completion = threading.Event()
        self.completion.set()
        # When retirement was requested, and its age, so a late (still-running) retiree stays visible
        # in diagnostics rather than vanishing when the batch deadline expires.
        self.retirement_started_at = 0.0
        # P1: the canonical durable-store identity, precomputed ONCE here so `_maybe_execute_pending_drop`
        # compares every active/retiring owner by a stored key under `_REGISTRY_LOCK` instead of calling
        # `Path.resolve()` per owner on the global-lock hot path. `unindex`'s pending-drop intent is a
        # ROOT-LEVEL fact keyed by this same canonical path (there is no per-`RootIndex` drop flag: it
        # duplicated `_PENDING_DROPS` and lied for a cleared-then-unindexed root with no active owner).
        self.root_key = _canonical_root_key(root)
        self.lock = threading.Lock()

    def replace_root_fd(self, descriptor: int) -> None:
        replacement = os.dup(descriptor)
        replacement_identity = parse_root_identity(root_identity(os.fstat(replacement)))
        with self.lock:
            previous = self.root_fd
            self._replace_root_fd_locked(replacement, replacement_identity)
        if previous is not None:
            os.close(previous)

    def _replace_root_fd_locked(self, descriptor: int, identity: tuple[int, int]) -> None:
        """Install one descriptor generation while the caller holds ``self.lock``."""
        previous_identity = self.root_fd_identity
        root_generation_changed = (
            (previous_identity is not None and previous_identity != identity)
            or (
                self.published_root_identity is not None
                and self.published_root_identity != identity
            )
        )
        if root_generation_changed:
            # Fence a worker that still owns the prior descriptor generation.
            # Its publication checks the immutable assignment generation and
            # root identity before making rows ready.
            if self.building:
                self.build_generation += 1
                self.active_generation = self.build_generation
                self.stop_event.set()
            _clear_ready_fields_locked(self)
            self.signature = ""
        self.root_fd = descriptor
        self.root_fd_identity = identity

    def duplicate_root_fd(self) -> int:
        with self.lock:
            if self.root_fd is None:
                raise RuntimeError(f"search index root is not pinned: {self.root}")
            return os.dup(self.root_fd)

    def close_root_fd(self) -> None:
        with self.lock:
            descriptor = self.root_fd
            self.root_fd = None
            self.root_fd_identity = None
        if descriptor is not None:
            os.close(descriptor)


_REGISTRY: dict[str, RootIndex] = {}
_REGISTRY_LOCK = threading.Lock()
# Retiring indexes stay OBSERVABLE here (keyed by object identity, so two retirees of the same root
# never clobber one another) from the moment they leave `_REGISTRY` until their worker-owned fd is
# closed. A detached or timed-out worker is therefore never invisible -- it is tracked here until the
# deferred close runs. Guarded by `_REGISTRY_LOCK`.
_RETIRING: dict[int, RootIndex] = {}

# P0-4: the ONE root-level durable-drop owner, keyed by CANONICAL root path (not a per-worker flag).
# `unindex` records a pending drop here; only the LAST finalizer for that root -- when no active
# registry owner and no retiring owner still holds the store -- executes it, and a later successful
# generation SUPERSEDES it. A per-`RootIndex` flag could not express "keep the store until the last of
# several late retirees for this root has exited", which is exactly the unlink-underneath-siblings bug.
# P0-6: the value is an IDENTITY TOKEN, not a bare boolean/timestamp. `unindex` stores a fresh token
# per request; a build captures the token that existed when its assignment began; a successful
# publication may supersede ONLY that exact captured token, so a NEWER unindex requested after the
# build started (a different token) can never be erased by the older build's publication.
# Guarded by `_REGISTRY_LOCK`.
_PENDING_DROPS: dict[str, str] = {}
_PENDING_DROP_SEQ = 0
# P0-2: the observable set of in-flight deferred-drop retry owners, keyed by CANONICAL root. When
# `_maybe_execute_pending_drop` cannot take the cross-process build lock (another process is mid-build),
# it hands the physical delete to ONE background waiter that blocks for the lock OFF the request thread
# and completes the drop once the external writer releases -- so a deferred drop is never orphaned with
# no retry owner. Deduped by root + captured token; a superseded/absent token schedules nothing.
# Guarded by `_REGISTRY_LOCK`. Its `completion` event is the settle signal callers/tests wait on.
_PENDING_DROP_RETRIES: dict[str, "_PendingDropRetry"] = {}
# P0-2: a retry owner is NOT one-shot and NEVER gives up while its token is pending. When an attempt fails
# on a transient fault (an open/flock error, or an unlink `OSError` that left files on disk) it RE-ARMS a
# fresh attempt for the SAME token through a single scheduled timer with capped exponential backoff -- never
# a poll/sleep loop, and never a bounded attempt count that stops re-arming. So there is always a live path
# that completes the delete once the fault clears. The re-arm chain reuses the ORIGINAL completion event,
# which settles only when the drop finally resolves (unlinked, or superseded by a current-identity build).
# The token stays in `_PENDING_DROPS` until a CONFIRMED unlink, so the store is never orphaned -- "recorded"
# never means "lost". The chain stops ONLY when the token is no longer the pending intent (a later build
# superseded it, or a newer unindex replaced it -- which owns its own retry).
_PENDING_DROP_RETRY_BACKOFF_BASE_SECONDS = 0.02
# Cap the backoff interval so an indefinitely-faulting store retries at a steady low rate (never stops)
# rather than backing off toward never.
_PENDING_DROP_RETRY_BACKOFF_CAP_SECONDS = 60.0
# Monotonic counter feeding each tombstone's opaque identity, so two unindexes of the same root never
# collide even within one wall-clock tick. Guarded by `_REGISTRY_LOCK`, like `_PENDING_DROP_SEQ`.
_TOMBSTONE_SEQ = 0


@dataclass(frozen=True)
class _WorkerAssignment:
    """One immutable per-worker build lease (P1-5): the generation, thread, and completion event a
    single ``_start_build`` assigned together. Frozen so the exiting worker and any retirement identify
    THIS worker by the lease identity, never by re-reading mutable fields a successor may have changed.
    A worker whose lease is no longer the object's live assignment must not close the successor's fd or
    set the successor's completion -- it only sets its OWN frozen completion event."""

    generation: int
    thread: threading.Thread
    completion: threading.Event
    # P0-6: the pending-drop token that existed when THIS worker's assignment began. Its successful
    # publication may supersede only this exact token, never a newer unindex requested afterwards.
    pending_drop_token: str | None = None
    # The tombstone identity present when THIS worker's assignment began (``None`` if the root had no
    # tombstone). Its publication may clear the marker only when that identity is still current; a
    # newer unindex written mid-build carries a different identity this build must not erase.
    captured_tombstone_identity: str | None = None
    # The authorized root descriptor generation this worker owns. Publication
    # may proceed only while the RootIndex still names this exact identity.
    captured_root_identity: tuple[int, int] | None = None


@dataclass
class _PendingDropRetry:
    """One in-flight deferred-drop retry owner (P0-2): the exact pending token it will honor, the daemon
    thread blocking for the cross-process build lock, and the completion event that settles once the
    waiter has run its recheck+drop (or found the token superseded and became a no-op) and retired."""

    token: str
    thread: threading.Thread
    completion: threading.Event


def _canonical_root_key(root: Path) -> str:
    """The stable identity for a root's durable store, so a pending drop and every active/retiring
    owner are compared on the same canonicalized path regardless of how the root was spelled."""
    return str(Path(root).expanduser().resolve(strict=False))


def _request_pending_drop(root: Path) -> str:
    """Record a root-level intent to drop the durable store, executed by the last finalizer.

    Returns a FRESH identity token (P0-6). A later unindex overwrites the token, so a publication that
    captured an OLDER token can no longer supersede this newer request."""
    global _PENDING_DROP_SEQ
    with _REGISTRY_LOCK:
        _PENDING_DROP_SEQ += 1
        token = f"{time.time_ns()}-{_PENDING_DROP_SEQ}"
        _PENDING_DROPS[_canonical_root_key(root)] = token
    return token


def _current_pending_drop_token(root: Path) -> str | None:
    """The pending-drop token a build captures when its assignment begins, or ``None`` if none pending."""
    with _REGISTRY_LOCK:
        return _PENDING_DROPS.get(_canonical_root_key(root))


def _supersede_pending_drop(root: Path, token: str | None) -> None:
    """A later successful generation republished this root, so the pending drop it CAPTURED at build
    start is void -- but ONLY that exact captured token (P0-6). A newer unindex requested after this
    build began holds a DIFFERENT token this publication must not erase, so an ``unindex`` issued
    mid-build is never lost to a stale supersession."""
    if token is None:
        return
    key = _canonical_root_key(root)
    with _REGISTRY_LOCK:
        if _PENDING_DROPS.get(key) == token:
            del _PENDING_DROPS[key]


def _maybe_execute_pending_drop(root: Path) -> None:
    """Execute the durable drop for ``root`` iff one is pending AND no owner can still hold the store.

    Atomically observes the pending intent, every ACTIVE registry owner, and every RETIRING owner for
    this canonical root under one lock; only when none remains does it claim the intent and unlink the
    store (outside the lock, since it does file I/O). The last of several late retirees is therefore
    the one finalizer that drops -- a sibling that exits earlier finds another owner still present and
    defers, so the store is never unlinked underneath a worker that could still hold it."""
    key = _canonical_root_key(root)
    with _REGISTRY_LOCK:
        if key not in _PENDING_DROPS:
            return
        # P1: compare against each owner's PRECOMPUTED canonical key rather than resolving every
        # owner's path under the global lock on this hot path.
        active = any(ri.root_key == key for ri in _REGISTRY.values())
        retiring = any(ri.root_key == key for ri in _RETIRING.values())
        if active or retiring:
            return
    # P0-3: every IN-PROCESS owner is gone, but ANOTHER PROCESS (e.g. :7770 sharing STATE_DIR) may hold
    # the per-root build lock and an open connection, mid-publish to this exact store. Deleting the
    # sqlite/manifest/wal/shm underneath it recreates the deleted-database / disk-I/O-error class. Take
    # the cross-process build lock NON-BLOCKING first: only unlink when WE can hold it (no other process
    # is building). If it cannot be acquired, DEFER -- leave the pending drop queued so a later finalizer
    # or GC executes it once the other process releases. The tombstone marker is already durable, so
    # cross-process readers still fail closed while the physical file lingers.
    lock_fd = None
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(_build_lock_path(root)), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another process holds the build lock; defer the physical delete (pending drop stays queued)
            # and hand it to a background waiter (P0-2) that completes the drop OFF this request thread
            # once the external writer releases the lock. The request itself stays non-blocking.
            _schedule_pending_drop_retry(root)
            return
        # We hold the cross-process build lock: re-verify no in-process owner reappeared and CAPTURE the
        # token without deleting it yet. P0-2: the pending-drop token (and its retry owner) must survive
        # until the physical unlink actually COMPLETES, so a transient unlink failure can never orphan the
        # store on disk with no owner left to GC it.
        with _REGISTRY_LOCK:
            token = _PENDING_DROPS.get(key)
            if token is None:
                return
            active = any(ri.root_key == key for ri in _REGISTRY.values())
            retiring = any(ri.root_key == key for ri in _RETIRING.values())
            if active or retiring:
                return
        if _drop_persisted_index(Path(root)):
            # The store is physically gone: retire only THIS captured token, so a newer unindex requested
            # meanwhile (a different token) keeps owning its own pending drop.
            with _REGISTRY_LOCK:
                if _PENDING_DROPS.get(key) == token:
                    del _PENDING_DROPS[key]
        else:
            # Transient unlink failure (already logged): keep the token and hand it to a background retry
            # owner rather than dropping it, so a later attempt completes the delete once the fault clears.
            _schedule_pending_drop_retry(root)
    finally:
        if lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def _schedule_pending_drop_retry(root: Path) -> None:
    """Ensure ONE background waiter is blocking for the cross-process build lock so a deferred durable
    drop still completes after the external writer releases (P0-2).

    Non-blocking for the request thread. Deduped by canonical root + the currently pending token: a
    waiter already blocking for the SAME token is left alone, and a superseded/absent token schedules
    nothing (a later successful build that voided the token makes any waiter a no-op). A waiter for a
    now-stale token is replaced -- the old thread retires itself as a no-op when it finally acquires the
    lock and sees the token changed. The waiter is NOT one-shot: a transient failure re-arms a bounded
    backoff attempt (see `_rearm_pending_drop_retry`) instead of dropping the owner."""
    key = _canonical_root_key(root)
    with _REGISTRY_LOCK:
        token = _PENDING_DROPS.get(key)
        if token is None:
            return
        existing = _PENDING_DROP_RETRIES.get(key)
        if existing is not None and existing.token == token and existing.thread.is_alive():
            return
        completion = threading.Event()
        thread = threading.Thread(
            target=_pending_drop_retry_main,
            args=(Path(root), key, token, completion, 0),
            name=f"search-index-drop-retry-{Path(root).name}",
            daemon=True,
        )
        _PENDING_DROP_RETRIES[key] = _PendingDropRetry(token=token, thread=thread, completion=completion)
    thread.start()


def _pending_drop_retry_backoff_seconds(attempt: int) -> float:
    """Capped exponential backoff for a re-armed deferred-drop attempt -- a one-shot scheduled delay for a
    single ``threading.Timer``, never a poll/sleep loop. The exponent is clamped so a long-faulting chain
    settles at the interval cap (a steady low retry rate) rather than overflowing or backing off forever."""
    return min(_PENDING_DROP_RETRY_BACKOFF_CAP_SECONDS, _PENDING_DROP_RETRY_BACKOFF_BASE_SECONDS * (2 ** min(max(0, attempt), 32)))


def _rearm_pending_drop_retry(root: Path, key: str, token: str, completion: threading.Event, attempt: int) -> bool:
    """Re-arm a deferred-drop attempt after a transient failure, REUSING the original completion event so
    it settles only when the drop finally resolves (P0-2). Returns whether a successor was armed.

    The next attempt is scheduled through ONE `threading.Timer` (a single delayed fire, not a poll loop)
    with capped exponential backoff, and installs itself as the live retry owner for this root. The chain
    NEVER gives up on a bounded attempt count -- it keeps re-arming as long as the token is still the
    pending intent, so once the fault clears the delete always completes. Nothing is armed ONLY when the
    token is no longer pending (a current-identity build superseded it, or a newer unindex replaced it and
    owns its own retry); the caller then settles the completion. The token is NEVER removed here, so the
    store is never orphaned even mid-fault."""
    timer = threading.Timer(
        _pending_drop_retry_backoff_seconds(attempt),
        _pending_drop_retry_main,
        args=(Path(root), key, token, completion, attempt),
    )
    timer.name = f"search-index-drop-retry-{Path(root).name}"
    timer.daemon = True
    with _REGISTRY_LOCK:
        if _PENDING_DROPS.get(key) != token:
            return False
        _PENDING_DROP_RETRIES[key] = _PendingDropRetry(token=token, thread=timer, completion=completion)
    timer.start()
    return True


def _pending_drop_retry_main(root: Path, key: str, token: str, completion: threading.Event, attempt: int) -> None:
    """Background deferred-drop waiter unit (P0-2). BLOCKS for the cross-process build lock (a blocking
    ``flock``, not a poll), then under it rechecks the EXACT captured token plus every active/retiring
    owner before unlinking the store through the ONE deletion owner (`_drop_persisted_index`). A later
    build that superseded the token, a newer unindex that replaced it, or a reappeared in-process owner
    makes this a no-op.

    NOT one-shot: the pending token is retired ONLY after `_drop_persisted_index` confirms the physical
    unlink completed. A transient unlink failure (a ``False`` return) or an open/flock fault RE-ARMS a
    bounded-backoff successor for the same token rather than dropping the owner, so a failing attempt
    reschedules instead of orphaning the store on disk.

    Supervisor boundary: this is one independent background unit, so it catches broadly, records the
    failure with its backtrace, and either re-arms or settles -- it must never crash the process, silently
    lose the token, or leave the process without eventually settling its completion event."""
    lock_fd = None
    rearmed = False
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(_build_lock_path(root)), os.O_CREAT | os.O_RDWR, 0o644)
        # Blocking acquire OFF the request thread: it returns exactly when the external writer releases,
        # so there is no sleep/poll loop. The waiter then decides under BOTH locks.
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with _REGISTRY_LOCK:
            still_pending = _PENDING_DROPS.get(key) == token
            active = any(ri.root_key == key for ri in _REGISTRY.values())
            retiring = any(ri.root_key == key for ri in _RETIRING.values())
            proceed = still_pending and not active and not retiring
        if proceed:
            if _drop_persisted_index(root):
                # Retire the token ONLY now that the physical store is confirmed gone.
                with _REGISTRY_LOCK:
                    if _PENDING_DROPS.get(key) == token:
                        del _PENDING_DROPS[key]
            else:
                # Transient unlink failure (already logged in `_drop_persisted_index`): keep the token and
                # re-arm rather than dropping the owner, so a later attempt completes the delete.
                rearmed = _rearm_pending_drop_retry(root, key, token, completion, attempt + 1)
        # not proceed -> token superseded/absent or an owner reappeared: settle as a no-op.
    except Exception:
        # One identified supervisor boundary for this background unit: record the fault with its backtrace,
        # then RESCHEDULE (open/flock/mkdir fault) rather than orphaning the deferred drop.
        LOGGER.exception("deferred search-index drop retry attempt %d failed for %s", attempt, root)
        rearmed = _rearm_pending_drop_retry(root, key, token, completion, attempt + 1)
    finally:
        if lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if not rearmed:
            # No successor owns the token now, so this chain has resolved (a confirmed drop, or a no-op
            # because the token was superseded/replaced): retire this owner's registry entry and settle the
            # shared completion event. A transient fault always re-arms instead, so it never lands here.
            with _REGISTRY_LOCK:
                existing = _PENDING_DROP_RETRIES.get(key)
                if existing is not None and existing.thread is threading.current_thread():
                    del _PENDING_DROP_RETRIES[key]
            completion.set()


def _nofollow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise RuntimeError("safe no-follow search-index opens are unsupported on this platform")
    return flag


# ONE absolute deadline for the WHOLE retirement batch, never N * per-root -- so demoting N roots
# waits at most this long in total, not N times this.
CLEAR_WORKER_JOIN_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RetirementResult:
    """The aggregate outcome of one batch retirement (``clear_memory_indexes``).

    ``requested`` is every root asked to retire, ``completed`` retired (worker finalized) within the
    ONE shared deadline, and ``late`` did not -- their worker still holds its fd and will finalize on
    its eventual exit, so they stay observable in ``_RETIRING`` and in diagnostics rather than being
    dropped."""

    requested: list[Path] = field(default_factory=list)
    completed: list[Path] = field(default_factory=list)
    late: list[Path] = field(default_factory=list)


def _await_worker_finalized(index: RootIndex, thread: threading.Thread, deadline: float) -> bool:
    """Wait for one retiree's worker to finalize, bounded by the shared batch ``deadline``.

    Waits on the worker's completion EVENT (set by its own ``_finalize_worker_exit`` after it has
    closed the fd and performed any deferred drop), then joins the thread briefly so a caller that
    asserts on liveness sees a fully-unwound worker. Returns whether the worker finalized in time."""
    remaining = max(0.0, deadline - time.monotonic())
    completed = index.completion.wait(remaining)
    if completed and thread.ident is not None:
        thread.join(max(0.0, deadline - time.monotonic()))
    return index.completion.is_set() and not thread.is_alive()


def _signal_retirement(index: RootIndex) -> threading.Thread | None:
    """Enter the retiring state: advance the REAL generation fence AND raise the cancellation event,
    then register the index as observable. Advancing ``active_generation`` is what makes ``current()``
    return False for the running build, so a cooperative worker exits at its next ``current()`` check
    even if it never inspects ``stop_event``; ``stop_event`` additionally unblocks a worker parked in a
    cooperative wait. Returns the worker thread snapshot (or None).

    P0-3: the terminal mark AND the observable registration are ONE step under the global order
    ``_REGISTRY_LOCK -> ri.lock``. The old code released both locks between marking ``retiring`` and
    inserting ``_RETIRING``; a worker could finalize in that gap -- close its fd and pop nothing --
    and then be inserted DEAD into ``_RETIRING`` (a leaked fd + a stuck retiree). Holding
    ``_REGISTRY_LOCK`` across the mark and the insert serializes the worker's ``_finalize_worker_exit``
    (whose ``_RETIRING`` pop needs the same lock) AFTER the insert. And when a worker has ALREADY
    finalized by the time we register (its slot cleared and completion set), we do not leave a dead
    entry behind."""
    with _REGISTRY_LOCK:
        with index.lock:
            index.build_generation += 1
            index.active_generation = index.build_generation
            index.stop_event.set()
            index.retiring = True
            if not index.retirement_started_at:
                index.retirement_started_at = time.monotonic()
            thread = index.thread
            # A build worker is installed iff it holds an un-cleared lease with an un-set completion.
            # A never-worked index (no assignment, completion set by init) has no worker to finalize --
            # its caller (`clear`/`unindex`) finalizes it directly and it must stay observable meanwhile.
            had_live_worker = index.assignment is not None or thread is not None or not index.completion.is_set()
        _RETIRING[id(index)] = index
        # If this index HAD a live worker and that worker finalized against its lease (concurrently, or
        # reentrantly through the insert) it has cleared its slot, set its completion, and closed its
        # retiring fd. Registering it would strand a dead retiree, so drop it back out under the SAME
        # lock. A live worker (slot still installed, completion not yet set) stays observable until its
        # own finalizer pops it.
        if had_live_worker:
            with index.lock:
                worker_finalized = (
                    index.root_fd is None
                    and index.assignment is None
                    and index.thread is None
                    and index.completion.is_set()
                )
            if worker_finalized:
                _RETIRING.pop(id(index), None)
    return thread


def _finalize_worker_exit(index: RootIndex, assignment: "_WorkerAssignment | None" = None) -> None:
    """The ONE finalizer for an assigned worker's exit. Called from the build thread's ``finally``
    (``_build_thread_main``), from the start-rollback path, and directly by
    ``clear_memory_indexes``/``unindex`` for an index with no live worker.

    Identity comes from the IMMUTABLE lease (P1-5), not from re-reading mutable fields. When called
    with the worker's ``assignment``, this worker still owns the slot iff the object's live assignment
    IS that lease; a worker whose lease was already superseded only sets its OWN frozen completion
    event and returns -- it never closes the successor's fd, drops its store, or removes it from the
    observable set. When called with no assignment (a direct finalize for an index with no live worker,
    e.g. ``clear``/``unindex`` of a never-started build), the thread slot is the identity.

    For the owning exiting worker it: clears the assigned thread/lease, closes the pinned root fd,
    removes the retiree from the observable set, executes any root-level pending durable drop when this
    is the last owner of the store, and sets the frozen completion event. A NON-retiring index keeps
    its fd (a normal build), but its lease is still cleared and its completion set. Idempotent."""
    with index.lock:
        if assignment is not None:
            # A build worker exiting through `_build_thread_main`: it owns the slot iff the live
            # assignment is STILL the lease it was given. A superseded worker (its lease replaced) must
            # not close the successor's fd or drop its store -- it only sets its own frozen completion.
            is_my_slot = index.assignment is assignment
            completion = assignment.completion
        else:
            # An EXTERNAL finalize (clear/unindex, or the start rollback) of a terminal object with no
            # successor: the caller owns the finalization, so close/drop unconditionally. There is no
            # successor to protect because `retiring` is terminal -- no new worker installs on it.
            is_my_slot = True
            completion = index.completion
        if is_my_slot:
            # P0-2: the MATCHING finalizer is the SOLE owner that clears the slot. No caller (a start
            # rollback or the final-ownership refusal in `_start_build`) pre-clears `thread`,
            # `assignment`, `building`, or the completion -- pre-clearing `assignment` made this
            # finalizer read `is_my_slot` False, so it skipped closing the retiring fd and removing the
            # `_RETIRING` row (a leaked fd + a stuck retiree). Clearing `building` here is what lets a
            # never-started assignment (Thread.start failed, or ownership lost after install) release
            # the flag so the scheduler is not blocked on a build that never ran.
            index.thread = None
            index.assignment = None
            index.building = False
        retiring = index.retiring
    if not is_my_slot:
        # A successor owns the live slot: this worker's only remaining duty was to set its OWN frozen
        # completion event, so a retirement waiting on the lease it captured is released. A successor
        # holds a DIFFERENT event, so setting mine never touches the successor's.
        completion.set()
        return
    if retiring:
        index.close_root_fd()
        with _REGISTRY_LOCK:
            _RETIRING.pop(id(index), None)
        # P0-4: the durable drop is a ROOT-LEVEL decision. Now that this owner has closed its fd and
        # left the observable set, execute the pending drop only if no other active or retiring owner
        # remains for this canonical root -- so the store outlives every sibling retiree until the last
        # one exits.
        _maybe_execute_pending_drop(index.root)
    # Set the frozen completion event LAST, so a waiter released by it observes a FULLY finalized
    # worker: fd closed, `_RETIRING` row removed, and any deferred durable drop already executed. The
    # finalizer clears `index.thread` to None (above), so a caller keyed on `index.thread` cannot join
    # this worker to wait for the drop -- completing only after the drop is what makes the deferred
    # delete observable the instant the completion fires.
    completion.set()


def clear_memory_indexes() -> RetirementResult:
    """Retire every in-memory index for a demoted background owner
    (``app.py::demote_background_owner``). Returns a ``RetirementResult`` naming the roots requested,
    completed, and late; a late root's worker still owns its fd and finalizes on its own exit.

    History: closing the pinned root fd out from under a still-running build thread let a worker write
    through a closed descriptor and raised sqlite "unable to open database file". An earlier fix
    joined each root under a SEPARATE 5s timeout (so demotion cost N*5s) and, on timeout, had already
    deleted the index from every registry -- permanently leaking the fd and detaching an invisible
    worker. This design instead: (1) signals ALL indexes FIRST -- advancing the real generation fence
    and the cancel event -- so blocked siblings are already told to stop before we wait on any;
    (2) waits on each worker's COMPLETION EVENT against ONE absolute deadline shared across the batch
    (never N joins with N timeouts); (3) never closes an fd under a live or current worker -- the
    worker's own ``finally`` (``_finalize_worker_exit``) performs the deferred close when it exits, so
    a late worker still releases its fd and no descriptor is abandoned; (4) keeps every not-yet-closed
    retiree observable in ``_RETIRING`` and in diagnostics; (5) PRESERVES durable indexes -- a
    demotion sets no deferred drop, so no on-disk store is deleted; (6) returns the aggregate outcome
    instead of silently logging and dropping the late roots."""
    with _REGISTRY_LOCK:
        indexes = list(_REGISTRY.values())
        _REGISTRY.clear()
    # 1. Signal ALL indexes FIRST -- fence + cancel + fence-advance -- before waiting on any, so every
    #    blocked sibling is already told to stop while we wait on the first.
    threads = {id(index): _signal_retirement(index) for index in indexes}
    # 2. ONE absolute deadline for the WHOLE batch (never N * per-root timeout).
    deadline = time.monotonic() + CLEAR_WORKER_JOIN_TIMEOUT_SECONDS
    current = threading.current_thread()
    requested = [index.root for index in indexes]
    completed: list[Path] = []
    late: list[Path] = []
    for index in indexes:
        thread = threads[id(index)]
        if thread is None:
            # No worker ran or will run for this index: close the fd directly, now.
            _finalize_worker_exit(index)
            completed.append(index.root)
            continue
        if thread is current:
            # Retirement requested from WITHIN the worker thread. It must never self-join; it stays
            # incomplete and observable until its OWN ``finally`` runs the deferred close.
            late.append(index.root)
            continue
        if _await_worker_finalized(index, thread, deadline):
            # Finished within the deadline. If it exited BEFORE we set ``retiring`` its ``finally`` saw
            # ``retiring`` False and left the fd open, so close it now; otherwise this is idempotent.
            _finalize_worker_exit(index)
            completed.append(index.root)
        else:
            # Late: still running, or assigned-but-never-started. Leave it observable in ``_RETIRING``;
            # its worker ``finally`` (or the start rollback) performs the deferred close on exit.
            late.append(index.root)
    if late:
        LOGGER.error(
            "search index workers did not retire within %.1fs during clear: %s; their root fds close on worker exit (deferred)",
            CLEAR_WORKER_JOIN_TIMEOUT_SECONDS,
            ", ".join(str(root) for root in late),
        )
    return RetirementResult(requested=requested, completed=completed, late=late)


class FileIndexTestScope:
    """Idempotently own process-global file-index state for one test lifecycle.

    Production callbacks remain process-lifetime registrations. A pytest worker,
    however, creates and destroys many app/indexer owners in one process. This
    scope clears every callback, trailing progress delivery, and in-memory index
    both before and after its body so setup failures cannot leak into the next
    test. Detached indexer suppression intentionally remains at the fixture
    boundary because it is a process-launch policy, not file-index state.
    """

    CALLBACK_CLEAR_ORDER = (
        "background_owner_checker",
        "background_owner_refresh_requester",
        "background_index_search_requester",
        "background_owner_bytes_recorder",
        "background_owner_done_notifier",
        "search_progress_notifier",
    )

    def cleanup(self) -> RetirementResult:
        set_background_owner_checker(None)
        set_background_owner_refresh_requester(None)
        set_background_index_search_requester(None)
        set_background_owner_bytes_recorder(None)
        set_background_owner_done_notifier(None)
        set_search_progress_notifier(None)
        _reset_search_progress_coalescing()
        return clear_memory_indexes()

    def __enter__(self) -> FileIndexTestScope:
        try:
            self.cleanup()
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> bool:
        self.cleanup()
        return False


def set_background_owner_checker(checker: Callable[[str], bool] | None) -> None:
    global _BACKGROUND_OWNER_CHECKER
    _BACKGROUND_OWNER_CHECKER = checker


def set_bfs_full_build_runner(runner: Callable[..., bool] | None) -> None:
    """Register the breadth-first full-build runner (`bfs_index.build_root_into_index`).

    When set, `_run_build` routes every configured-root FULL build through it, so the filesystem
    open order is breadth-first (root listing first, one directory per work item) and each layer
    publishes independently. When unset -- a process that never imported `bfs_index`, such as a
    pure `file_index` unit test -- the legacy DFS `_walk_root_with_metrics` remains the fallback.
    """
    global _BFS_FULL_BUILD_RUNNER
    _BFS_FULL_BUILD_RUNNER = runner


def set_background_owner_refresh_requester(requester: Callable[[str, dict[str, Any]], dict[str, Any]] | None) -> None:
    global _BACKGROUND_OWNER_REFRESH_REQUESTER
    _BACKGROUND_OWNER_REFRESH_REQUESTER = requester


def set_background_index_search_requester(requester: Callable[[dict[str, Any]], dict[str, Any]] | None) -> None:
    global _BACKGROUND_INDEX_SEARCH_REQUESTER
    _BACKGROUND_INDEX_SEARCH_REQUESTER = requester


def request_background_index_search(payload: dict[str, Any]) -> dict[str, Any]:
    if _BACKGROUND_INDEX_SEARCH_REQUESTER is None:
        return {"ok": False, "error": "no persistent index search requester"}
    token = _BACKGROUND_INDEX_SEARCH_REQUEST.set(dict(payload))
    try:
        return _BACKGROUND_INDEX_SEARCH_REQUESTER(payload)
    finally:
        _BACKGROUND_INDEX_SEARCH_REQUEST.reset(token)


def background_index_search_request() -> dict[str, Any]:
    """Return the current request while an adapter forwards it to ``SearchIndexerClient``."""
    request = _BACKGROUND_INDEX_SEARCH_REQUEST.get()
    return dict(request) if request is not None else {}


def root_identity(stat_result: os.stat_result) -> list[int]:
    """Encode the one cross-process root-generation identity schema."""
    return [int(stat_result.st_dev), int(stat_result.st_ino)]


def parse_root_identity(value: Any) -> tuple[int, int]:
    """Parse the shared root-generation identity schema, refusing ambiguous values."""
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(part, bool) or not isinstance(part, int) or part < 0 for part in value)
    ):
        raise ValueError("authorized root identity is malformed")
    return int(value[0]), int(value[1])


def _root_identity_metadata_value(identity: tuple[int, int]) -> str:
    """Serialize the shared identity schema into SQLite's text metadata value."""
    return json.dumps([int(identity[0]), int(identity[1])], separators=(",", ":"))


def _metadata_root_identity(metadata: dict[str, Any] | None) -> tuple[int, int] | None:
    """Read one published root identity from SQLite or manifest metadata."""
    if not metadata or AUTHORIZED_ROOT_IDENTITY_FIELD not in metadata:
        return None
    value = metadata.get(AUTHORIZED_ROOT_IDENTITY_FIELD)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    try:
        return parse_root_identity(value)
    except ValueError:
        return None


def _root_identity_matches(metadata: dict[str, Any], expected: tuple[int, int]) -> bool:
    """The one publication/readability verdict for a root descriptor generation."""
    return _metadata_root_identity(metadata) == expected


def _current_root_identity(root: Path) -> tuple[int, int] | None:
    """Pin the current directory long enough to obtain its object-generation identity."""
    descriptor: int | None = None
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _nofollow_flag(),
        )
        return parse_root_identity(root_identity(os.fstat(descriptor)))
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def set_background_owner_bytes_recorder(recorder: Callable[[int], None] | None) -> None:
    global _BACKGROUND_OWNER_BYTES_RECORDER
    _BACKGROUND_OWNER_BYTES_RECORDER = recorder


def set_background_owner_done_notifier(notifier: Callable[[str, dict[str, Any]], None] | None) -> None:
    global _BACKGROUND_OWNER_DONE_NOTIFIER
    _BACKGROUND_OWNER_DONE_NOTIFIER = notifier


def set_search_progress_notifier(notifier: Callable[[dict[str, Any]], None] | None) -> None:
    global _SEARCH_PROGRESS_NOTIFIER
    with _SEARCH_PROGRESS_LOCK: _SEARCH_PROGRESS_NOTIFIER = notifier


def _root_scope_id(root: Path) -> str:
    """The opaque, stable digest that identifies a root ON THE SHARED BUS -- never the path itself.

    The background-client-events bus is globally persisted and fanned out to every client, so a
    filesystem path in a signal's resource name or payload would disclose one user's directory to
    another. A digest of the canonical root key gives per-root ordering + latest-retention with no
    path leak; it matches the same-digest keying used by the on-disk index files."""
    return hashlib.sha256(_canonical_root_key(root).encode("utf-8")).hexdigest()[:16]


def _redacted_progress_coverage(coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Build the ONLY coverage a progress signal may carry: numeric progress + terminal flags.

    Fail closed: the result is constructed FRESH from an allowlist, so even if the caller's coverage
    dict carries `root` (it does -- `_coverage_shape` includes the path) or any other field, none of
    it can reach the shared bus. Missing values coerce to 0/False, never to a leaked default."""
    source = coverage if isinstance(coverage, dict) else {}
    redacted: dict[str, Any] = {}
    for key in _SEARCH_PROGRESS_COVERAGE_KEYS:
        value = source.get(key)
        if key in ("full_coverage", "truncated"):
            redacted[key] = bool(value)
        else:
            redacted[key] = int(value or 0)
    return redacted


def notify_search_progress(root: Path, generation: int, revision: int, coverage: dict[str, Any] | None) -> None:
    """Emit a redacted, per-root-coalesced progress SIGNAL when a directory publication commits.

    The frame is EXACTLY `{scope_id, generation, revision, coverage}` -- an opaque scope digest, the
    published generation, the new journal high-water revision, and a numeric-only coverage summary.
    It NEVER carries a query, path, match, filename, or subscription id: any of those on this globally
    fanned-out bus would leak one client's filesystem data to another. Coalesced to at most one signal
    per root per `SEARCH_PROGRESS_COALESCE_SECONDS`, with the latest revision always delivered (a
    trailing emit) so a client never misses the final publication -- and the intervening revisions are
    safe to drop because the client pulls every ordered delta by cursor from committed SQLite."""
    scope_id = _root_scope_id(root)
    frame = {
        "scope_id": scope_id,
        "generation": int(generation),
        "revision": int(revision),
        "coverage": _redacted_progress_coverage(coverage),
    }
    with _SEARCH_PROGRESS_LOCK:
        notifier = _SEARCH_PROGRESS_NOTIFIER
        if notifier is None:
            return
        now = time.monotonic()
        last = _SEARCH_PROGRESS_LAST_EMIT.get(scope_id)
        window = SEARCH_PROGRESS_COALESCE_SECONDS
        if last is not None and now - last < window:
            # Inside the window: keep only the latest frame and ensure a single trailing timer fires
            # at the window boundary to deliver it. A newer frame overwrites an older pending one.
            _SEARCH_PROGRESS_PENDING[scope_id] = frame
            if scope_id not in _SEARCH_PROGRESS_TIMERS:
                timer = threading.Timer(max(0.0, window - (now - last)), _flush_search_progress, args=(scope_id,))
                timer.daemon = True
                _SEARCH_PROGRESS_TIMERS[scope_id] = timer
                timer.start()
            return
        _SEARCH_PROGRESS_LAST_EMIT[scope_id] = now
        _SEARCH_PROGRESS_PENDING.pop(scope_id, None)
    _deliver_search_progress(notifier, frame)


def _deliver_search_progress(notifier: Callable[[dict[str, Any]], None], frame: dict[str, Any]) -> None:
    """Linearize callback delivery with lifecycle teardown.

    Teardown first clears the registered identity, then waits for callbacks that
    already crossed this gate. A timer that wakes late therefore cannot publish,
    and once cleanup returns no captured callback remains in flight.
    """

    global _SEARCH_PROGRESS_ACTIVE_CALLBACKS
    with _SEARCH_PROGRESS_LOCK:
        if _SEARCH_PROGRESS_NOTIFIER is not notifier:
            return
        _SEARCH_PROGRESS_ACTIVE_CALLBACKS += 1
    try:
        notifier(frame)
    finally:
        with _SEARCH_PROGRESS_LOCK:
            _SEARCH_PROGRESS_ACTIVE_CALLBACKS -= 1
            if _SEARCH_PROGRESS_ACTIVE_CALLBACKS == 0:
                _SEARCH_PROGRESS_IDLE.notify_all()


def _flush_search_progress(scope_id: str) -> None:
    """Deliver the latest pending progress frame for a root at the coalescing-window boundary."""
    with _SEARCH_PROGRESS_LOCK:
        _SEARCH_PROGRESS_TIMERS.pop(scope_id, None)
        frame = _SEARCH_PROGRESS_PENDING.pop(scope_id, None)
        if frame is None:
            return
        _SEARCH_PROGRESS_LAST_EMIT[scope_id] = time.monotonic()
        notifier = _SEARCH_PROGRESS_NOTIFIER
    if notifier is not None:
        _deliver_search_progress(notifier, frame)


def _reset_search_progress_coalescing() -> None:
    """Test helper: cancel pending timers and clear per-root coalescing state."""
    with _SEARCH_PROGRESS_LOCK:
        for timer in _SEARCH_PROGRESS_TIMERS.values():
            timer.cancel()
        _SEARCH_PROGRESS_TIMERS.clear()
        _SEARCH_PROGRESS_PENDING.clear()
        _SEARCH_PROGRESS_LAST_EMIT.clear()
        while _SEARCH_PROGRESS_ACTIVE_CALLBACKS:
            _SEARCH_PROGRESS_IDLE.wait()


def background_owner_can_build() -> bool:
    if _BACKGROUND_OWNER_CHECKER is None:
        return True
    return bool(_BACKGROUND_OWNER_CHECKER(SEARCH_INDEX_ROLE))


def request_background_owner_refresh(payload: dict[str, Any]) -> dict[str, Any]:
    if _BACKGROUND_OWNER_REFRESH_REQUESTER is None:
        # No owner is wired at all, so nothing accepted this refresh. Callers used
        # to read the falsy `fallback` here as "someone else is refreshing".
        record_accepted_refresh(str(payload.get("root") or ""), False)
        return {"ok": False, "accepted": False, "fallback": False, "error": "no background owner refresh requester"}
    result = _BACKGROUND_OWNER_REFRESH_REQUESTER(SEARCH_INDEX_ROLE, payload)
    record_accepted_refresh(str(payload.get("root") or ""), bool(result.get("accepted")))
    return result


def promote_frontier(
    root: Path,
    *,
    to_priority: int = USER_VISIBLE_DEMAND_PRIORITY,
    to_reason: str = USER_VISIBLE_DEMAND_REASON,
    expected_root_identity: tuple[int, int] | None = None,
) -> int:
    """Raise the priority of a root's pending durable frontier rows to user-visible-demand.

    Item 5: a Quick Open query whose scope is not yet fully covered bumps that root's pending
    frontier so breadth expansion for it runs ahead of ordinary background work, WITHOUT launching
    a second crawl. This is one bounded UPDATE on the same durable ``frontier`` table the running
    build resumes from (not a new queue), and it only RAISES priority (a smaller number) so it can
    never demote a more urgent ``startup-depth-1`` or ``hot-change`` item. Returns the number of
    rows promoted; 0 when there is no pending frontier yet.
    """
    disk = _index_disk_path(root)
    if not disk.exists():
        return 0
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None:
        return 0
    try:
        conn = sqlite3.connect(disk, timeout=_COVERAGE_LIVE_READ_TIMEOUT_SECONDS)
        try:
            conn.execute(f"PRAGMA busy_timeout={int(_COVERAGE_LIVE_READ_TIMEOUT_SECONDS * 1000)}")
            _ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if not _root_identity_matches(metadata, expected_identity):
                return 0
            # Do not promote the frontier of a snapshot an explicit unindex invalidated: mutating a
            # deleted store's crawl queue is meaningless, and a fresh generation supersedes it anyway.
            if _snapshot_is_tombstoned(root, metadata):
                return 0
            cursor = conn.execute(
                "UPDATE frontier SET priority = ?, reason = ? WHERE state = 'pending' AND priority > ?",
                (int(to_priority), str(to_reason), int(to_priority)),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        return 0


def _dispatch_user_visible_promotion(payload: dict[str, Any]) -> None:
    """Send one non-blocking user-visible promotion to the elected owner's indexer.

    Runs on a short-lived daemon thread so a Quick Open query never waits on the RPC. This is an
    independent unit of work (a supervisor boundary per the error-handling policy): a transport
    failure is recorded and dropped, never allowed to kill the query that scheduled it. The
    breadth-first crawl still reaches the directory on its own cadence if the promotion is lost.
    """
    requester = _BACKGROUND_OWNER_REFRESH_REQUESTER
    if requester is None:
        return
    try:
        requester(SEARCH_INDEX_ROLE, payload)
    except Exception:
        LOGGER.debug("user-visible frontier promotion dispatch failed", exc_info=True)


def request_user_visible_promotion(root: str, directory: str = "") -> bool:
    """Fire-and-forget: ask the indexer to promote a root's frontier to user-visible-demand.

    Item 5: a Quick Open query for a not-yet-fully-covered scope promotes that root's frontier
    priority. It must NOT block the query, wait behind ``jobd``'s single interactive worker, or
    launch a second crawl -- so the request is dispatched on a daemon thread and its result is
    ignored, and repeated queries for the same root within a short window coalesce into one
    dispatch. Returns whether a dispatch was scheduled (False when debounced or no owner is wired).
    """
    if _BACKGROUND_OWNER_REFRESH_REQUESTER is None:
        return False
    key = str(root)
    expected_root_identity = _current_root_identity(Path(key))
    if expected_root_identity is None:
        return False
    now = time.monotonic()
    with _PROMOTION_LOCK:
        last = _PROMOTION_LAST_DISPATCH.get(key, 0.0)
        if now - last < _PROMOTION_DEBOUNCE_SECONDS:
            return False
        _PROMOTION_LAST_DISPATCH[key] = now
    payload = {
        "root": key,
        "operation": "promote",
        "reason": USER_VISIBLE_DEMAND_REASON,
        AUTHORIZED_ROOT_IDENTITY_FIELD: list(expected_root_identity),
    }
    if directory:
        payload["directory"] = str(directory)
    threading.Thread(
        target=_dispatch_user_visible_promotion,
        args=(payload,),
        name="qopen-promote",
        daemon=True,
    ).start()
    return True


def record_search_index_bytes_written(byte_count: int) -> None:
    if _BACKGROUND_OWNER_BYTES_RECORDER is not None:
        _BACKGROUND_OWNER_BYTES_RECORDER(byte_count)


def notify_background_owner_done(payload: dict[str, Any]) -> None:
    if _BACKGROUND_OWNER_DONE_NOTIFIER is not None:
        _BACKGROUND_OWNER_DONE_NOTIFIER(SEARCH_INDEX_ROLE, payload)


def _index_disk_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.sqlite3"


def persisted_index_roots_within(root: Path) -> list[Path]:
    """Return persisted child-index roots without trusting their metadata yet.

    Search validates every candidate's manifest/schema before reading rows.  A
    filename scan is only a bounded discovery step that lets a warming parent
    root serve exact files from an already-persisted child root.
    """
    try:
        manifests = list(INDEX_DIR.glob("*.manifest.json"))
    except OSError:
        return []
    roots: list[Path] = []
    for manifest_path in manifests:
        metadata = read_json_file(manifest_path, None)
        if metadata is None:
            continue
        try:
            candidate = Path(str(metadata.get("root") or "")).expanduser().resolve(strict=False)
        except (TypeError, ValueError):
            continue
        # An explicit unindex invalidates the child snapshot: do not offer a deleted root as a
        # persisted child a warming parent could serve exact files from.
        if isinstance(metadata, dict) and _snapshot_is_tombstoned(candidate, metadata):
            continue
        current_identity = _current_root_identity(candidate)
        if (
            not isinstance(metadata, dict)
            or current_identity is None
            or not _root_identity_matches(metadata, current_identity)
        ):
            continue
        if candidate != root and _path_is_within(candidate, root):
            roots.append(candidate)
    return sorted(set(roots), key=lambda candidate: (-len(str(candidate)), str(candidate)))


def _legacy_index_json_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.json"


def _index_manifest_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.manifest.json"


def _build_lock_path(root: Path) -> Path:
    # C11: a per-root file lock so two server processes (e.g. :7770 and :7771 sharing STATE_DIR) don't
    # duplicate the same expensive walk or delete while another build is persisting.
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.lock"


def _producer_heartbeat_path(root: Path) -> Path:
    # M11: the producer's live custody claim for one root. Written by the single
    # writer only, read by followers with a file read instead of an RPC.
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.producer.json"


def _tombstone_path(root: Path) -> Path:
    # C11: written on unindex so a SECOND server process (sharing STATE_DIR) that still holds a ready
    # in-memory copy drops it instead of serving deleted-file results indefinitely.
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.tomb"


# A tombstone marker that is present on disk but cannot be parsed into a valid ``(identity, time)`` is
# NOT the same as an absent marker. ``None`` means "no marker" (accept snapshots); this sentinel means
# "a marker exists but is corrupt", which is deletion authority that fails closed (reject every
# snapshot). Distinguishing the two is what keeps a garbled marker from silently re-serving deleted rows.
_TOMBSTONE_MALFORMED = object()


def _read_tombstone(root: Path) -> tuple[str, float] | object | None:
    """Return the parsed durable unindex tombstone with THREE distinct outcomes.

    * ``None`` -- the marker is ABSENT (no file, or an empty/whitespace-only file). None must mean
      "marker absent" so a reader accepts current snapshots.
    * ``(identity, deletion_time)`` -- a VALID marker. Current format is two lines (opaque generation
      identity, then deletion wall-clock time); a legacy single-line float carries identity ``""`` and
      is honored by time alone.
    * ``_TOMBSTONE_MALFORMED`` -- the marker is PRESENT but unparseable (a non-float time in the 2-line
      form, or a single non-float line). This is deletion authority, not absence: it fails closed.

    The tombstone is deletion AUTHORITY, not a timestamp hint. A build proves it started against THIS
    exact unindex by having frozen the same identity."""
    try:
        text = _tombstone_path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) >= 2:
        try:
            return lines[0], float(lines[1])
        except ValueError:
            return _TOMBSTONE_MALFORMED
    try:
        return "", float(lines[0])
    except ValueError:
        return _TOMBSTONE_MALFORMED


def _tombstone_time(root: Path) -> float:
    # The unindex timestamp recorded on disk (0.0 if absent OR malformed -- a malformed marker has no
    # usable time, and its rejection is handled by `_snapshot_is_tombstoned`'s fail-closed branch).
    tomb = _read_tombstone(root)
    if tomb is None or tomb is _TOMBSTONE_MALFORMED:
        return 0.0
    return tomb[1]


def _current_tombstone_identity(root: Path) -> str | None:
    """The opaque identity of the current tombstone, ``""`` for a legacy float marker, ``None`` if the
    root has no tombstone (absent) OR the marker is malformed. A build captures this AS its assignment
    begins so its publication stamps only the identity it actually superseded. A malformed marker
    returns ``None`` here, but `_snapshot_is_tombstoned` still rejects every snapshot for it."""
    tomb = _read_tombstone(root)
    if tomb is None or tomb is _TOMBSTONE_MALFORMED:
        return None
    return tomb[0]


def _write_tombstone(root: Path) -> str:
    """Write the durable unindex tombstone with a FRESH opaque identity and deletion time; return the
    identity. Atomic (temp + ``os.replace``) so a concurrent reader never sees a half-written marker.
    Called at the START of ``unindex`` -- before registry removal, worker wait, or drop work -- so the
    delete authority is durable before any teardown.

    FAIL-CLOSED: an ``OSError`` is NOT suppressed. A failed marker write must propagate so ``unindex``
    cannot silently claim success without durable cross-process deletion authority. The temp filename
    includes the identity and pid so two concurrent writers never collide on the same scratch path."""
    global _TOMBSTONE_SEQ
    with _REGISTRY_LOCK:
        _TOMBSTONE_SEQ += 1
        identity = f"{time.time_ns()}-{_TOMBSTONE_SEQ}"
    path = _tombstone_path(root)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{identity}.{os.getpid()}.tmp")
    tmp.write_text(f"{identity}\n{time.time()}\n", encoding="utf-8")
    os.replace(tmp, path)
    return identity


def _metadata_tombstone_identity(metadata: dict[str, Any] | None) -> str:
    """The tombstone identity a published snapshot was stamped with, ``""`` when unstamped/absent."""
    if not metadata:
        return ""
    return str(metadata.get("tombstone_identity") or "")


def _reconcile_manifest_tombstone_identity(root: Path, value: str) -> None:
    """Refresh the DERIVED manifest cache to match the authoritative sqlite identity (P1-3).

    The manifest is a best-effort atomic cache of the committed sqlite truth, never an independent
    record. A write failure here leaves the manifest stale but does NOT corrupt the authoritative
    store, and every manifest-first reader (`_raw_snapshot_metadata`, coverage, root discovery) falls
    back to sqlite -- so a stale manifest can never advertise a different identity than sqlite serves.
    The failure is recorded, not silently swallowed."""
    manifest_path = _index_manifest_path(root)
    raw = read_json_file(manifest_path, None)
    if not isinstance(raw, dict):
        return
    if str(raw.get("tombstone_identity") or "") == value:
        return
    raw["tombstone_identity"] = value
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_name(f"{manifest_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        os.replace(tmp, manifest_path)
    except OSError:
        LOGGER.exception("failed to reconcile derived manifest tombstone identity for %s", root)


def _stamp_snapshot_tombstone_identity(
    root: Path,
    identity: str | None,
    *,
    expected_root_identity: tuple[int, int] | None,
) -> bool:
    """Commit the frozen tombstone identity into the AUTHORITATIVE sqlite snapshot, then reconcile the
    DERIVED manifest cache from that SAME value; return whether the authoritative sqlite commit landed.

    P1-3: sqlite is the one committed truth. Its stamp must succeed for a publication to treat the
    deletion as superseded; a manifest write is only a derived cache refreshed from the same value.
    A build that superseded no tombstone (identity ``None``/``""``) stamps the empty string, which a
    valid non-empty marker never matches -- so its snapshot is rejected while that marker stands.

    Returns ``False`` (the commit did NOT land) when there is no durable store to stamp OR the sqlite
    metadata write fails. The failure is recorded, never suppressed and then reported as committed, so
    `_complete_publication` cannot supersede a durable-drop intent on a snapshot that never proved it
    superseded the marker."""
    value = str(identity or "")
    # Stamp only an EXISTING durable store. A build that persisted nothing (persistence disabled or
    # over budget) dropped its sqlite/manifest; recreating an empty database here to stamp it would
    # resurrect a store the build deliberately did not keep -- and there is no authoritative snapshot
    # to carry the identity, so the commit has not landed.
    if not _index_disk_path(root).exists():
        return False
    try:
        with _sqlite_index_connection(root) as conn:
            _ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if expected_root_identity is None or not _root_identity_matches(metadata, expected_root_identity):
                return False
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES ('tombstone_identity', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
    except (OSError, sqlite3.DatabaseError):
        LOGGER.exception("failed to commit authoritative snapshot tombstone identity for %s", root)
        return False
    _reconcile_manifest_tombstone_identity(root, value)
    return True


def _snapshot_is_tombstoned(root: Path, metadata: dict[str, Any] | None) -> bool:
    """Fail-closed deletion authority: does an explicit unindex tombstone invalidate THIS snapshot?

    The ONE verdict every persisted read/adopt/resume/coverage surface routes through, so a deleted
    root's snapshot can never be served, adopted, resumed, or counted as coverage anywhere.

    * ABSENT marker (`None`)          -> False (nothing to invalidate).
    * MALFORMED marker                -> True  (present but corrupt: reject every snapshot, fail closed).
    * VALID with a real identity      -> reject unless the snapshot's stamped `tombstone_identity`
                                         EQUALS the current marker identity. A build that stamped a
                                         different (or no) identity did not supersede THIS unindex.
    * VALID legacy identity-less form -> time fallback: reject if the snapshot's `built_at` is at or
                                         before the recorded deletion time.

    The marker is DURABLE -- publication never clears it. A live root keeps its marker and its snapshot
    is accepted only because the snapshot carries the matching identity stamp."""
    tomb = _read_tombstone(root)
    if tomb is None:
        return False
    if tomb is _TOMBSTONE_MALFORMED:
        return True
    identity, deletion_time = tomb
    if identity:
        return _metadata_tombstone_identity(metadata) != identity
    return _metadata_built_at(metadata) <= deletion_time


def _root_index_is_tombstoned(ri: RootIndex) -> bool:
    """In-memory deletion authority through the SAME verdict as disk (P0-1).

    The live serving path (`search_index`), own-index freshness, and registry-root discovery all trust
    an in-memory ``RootIndex``. Its validity must be judged by the same rule the disk read path uses, or
    a build that published AFTER a newer cross-process unindex marker keeps ``ready=True`` and serves
    deleted rows from RAM. This synthesizes the tombstone-relevant metadata of the owner's published
    snapshot (the frozen identity it was published with plus its ``built_at``) and routes it through the
    ONE `_snapshot_is_tombstoned` owner -- no second time comparison: absent marker is valid, a malformed
    marker is invalid, a real identity is valid only when the frozen identity matches, and a legacy
    identity-less marker falls back to the ``built_at`` time rule."""
    return _snapshot_is_tombstoned(
        ri.root,
        {
            "tombstone_identity": ri.published_tombstone_identity or "",
            "built_at": ri.built_at,
        },
    )


def _publication_lands_tombstoned(root: Path, published_identity: str | None, built_at: float) -> bool:
    """Whether a snapshot ABOUT to be published with ``published_identity``/``built_at`` is ALREADY invalid
    under the CURRENT durable marker (P0-1). Routed through the ONE `_snapshot_is_tombstoned` verdict, so a
    build whose frozen identity no longer matches a newer unindex (one that landed after the build froze
    its identity) lands EVICTED at the publication itself -- it never sets ``ready``/stamps a stale
    ``published_tombstone_identity`` into a servable owner. Called under ``ri.lock`` so the re-verify and
    the ready decision are atomic with the marker it just read."""
    return _snapshot_is_tombstoned(
        root,
        {
            "tombstone_identity": published_identity or "",
            "built_at": built_at,
        },
    )


def _clear_ready_fields_locked(ri: RootIndex) -> None:
    """Clear a ready in-memory snapshot's servable fields. The caller MUST already hold ``ri.lock``.

    The ONE mutation body every eviction (serve-time, ``ensure_index``, and a publication that re-verifies
    a now-stale identity) routes through, so no path grows a divergent copy of the field set that must be
    cleared for a tombstoned owner to stop serving deleted rows."""
    ri.entries = []
    ri.entry_by_path = {}
    ri.ready = False
    ri.built_at = 0.0
    ri.disk_metadata_ready = False
    ri.disk_entry_count = 0
    ri.published_tombstone_identity = None
    ri.published_root_identity = None


def _root_index_generation_matches(ri: RootIndex) -> bool:
    """Whether the in-memory rows belong to the currently installed root descriptor."""
    return (
        ri.root_fd_identity is not None
        and ri.published_root_identity is not None
        and ri.root_fd_identity == ri.published_root_identity
    )


def _evict_tombstoned_root_index(ri: RootIndex) -> bool:
    """Clear a ready in-memory snapshot the current durable tombstone invalidates; report the eviction.

    Called before serving/freshness/discovery decisions and by ``ensure_index``. When the owner's frozen
    published identity no longer matches the live marker (a newer unindex landed after this build froze
    its identity), its rows must not be served. P0-1: the verdict AND the field clear happen under ONE
    continuous ``ri.lock`` acquisition (the small tombstone-file read is done while the lock is held), so a
    stale build republishing its old identity cannot slip a ready snapshot in between the verdict and the
    clear. Callers that then read rows under the SAME lock hold (`_servable_snapshot`) are fully atomic."""
    with ri.lock:
        if not ri.ready or not _root_index_is_tombstoned(ri):
            return False
        _clear_ready_fields_locked(ri)
    return True


_LIVENESS_LOCK = threading.Lock()
_LIVENESS_CACHE: dict[str, tuple[float, bool]] = {}
_ACCEPTED_REFRESH_LOCK = threading.Lock()
_ACCEPTED_REFRESHES: dict[str, float] = {}
_HEARTBEAT_LOCK = threading.Lock()
_HEARTBEAT_WRITTEN_AT: dict[str, float] = {}
_SELF_PROCESS_EPOCH = ""


def process_epoch(pid: int) -> str:
    """Return one `(pid, process start time)` identity, or "" when unavailable.

    This is the same epoch identity the local-service registry fences persisted
    process records with; a bare PID is not an identity because PIDs are reused.
    """
    clean_pid = int(pid)
    identity = process_start_identity(clean_pid)
    return f"{clean_pid}:{identity}" if identity else ""


def self_process_epoch() -> str:
    """Return this process's epoch, resolved once (it cannot change while we run)."""
    global _SELF_PROCESS_EPOCH
    if not _SELF_PROCESS_EPOCH:
        _SELF_PROCESS_EPOCH = process_epoch(os.getpid())
    return _SELF_PROCESS_EPOCH


def _pid_exists(pid: int) -> bool:
    """Signal-0 existence probe, so a DEAD producer costs one syscall and no more.

    `process_start_identity` falls back to spawning `ps` when it cannot read the
    process table entry, which is exactly what happens for a PID that is gone.
    Quick Open asks this per query, so that fallback must never be reached for the
    common "producer exited" case.
    """
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # Present but not ours to signal; let the identity read decide.
        return True
    return True


def process_epoch_is_live(epoch: str, *, monotonic_now: float | None = None) -> bool:
    """Prove one recorded producer epoch is still the process running under that PID."""
    text = str(epoch or "")
    pid_text, separator, recorded_identity = text.partition(":")
    if not separator or not recorded_identity:
        return False
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    if pid <= 1:
        return False
    now = time.monotonic() if monotonic_now is None else float(monotonic_now)
    with _LIVENESS_LOCK:
        cached = _LIVENESS_CACHE.get(text)
        if cached is not None and now - cached[0] < PRODUCER_LIVENESS_CACHE_SECONDS:
            return cached[1]
    live = _pid_exists(pid)
    if live:
        # A live PID whose start identity differs is a REUSED pid, not our producer.
        observed = process_start_identity(pid)
        live = bool(observed) and str(observed) == recorded_identity
    with _LIVENESS_LOCK:
        if len(_LIVENESS_CACHE) > 64:
            _LIVENESS_CACHE.clear()
        _LIVENESS_CACHE[text] = (now, live)
    return live


def reset_producer_liveness_cache() -> None:
    with _LIVENESS_LOCK:
        _LIVENESS_CACHE.clear()


def touch_producer_heartbeat(root: Path, *, force: bool = False) -> None:
    """Record that this writer still owns `root`, without rebuilding anything.

    Only the process that may build calls this. It is one small atomic write at
    most every PRODUCER_HEARTBEAT_INTERVAL_SECONDS per root, which is what lets a
    reader tell "idle producer still watching" from "producer gone".
    """
    if not background_owner_can_build():
        return
    key = str(root)
    now = time.monotonic()
    with _HEARTBEAT_LOCK:
        written_at = _HEARTBEAT_WRITTEN_AT.get(key)
        if not force and written_at is not None and now - written_at < PRODUCER_HEARTBEAT_INTERVAL_SECONDS:
            return
        _HEARTBEAT_WRITTEN_AT[key] = now
    epoch = self_process_epoch()
    if not epoch:
        return
    path = _producer_heartbeat_path(root)
    payload = json.dumps({"producer_epoch": epoch, "at": float(time.time()), "root": key}, sort_keys=True, separators=(",", ":"))
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Retry on the next tick rather than silently claiming a fresh heartbeat.
        with _HEARTBEAT_LOCK:
            _HEARTBEAT_WRITTEN_AT.pop(key, None)


def _read_producer_heartbeat(root: Path) -> tuple[str, float]:
    payload = read_json_file(_producer_heartbeat_path(root), None)
    if not isinstance(payload, dict):
        return "", 0.0
    try:
        recorded_at = float(payload.get("at") or 0.0)
    except (TypeError, ValueError):
        recorded_at = 0.0
    return str(payload.get("producer_epoch") or ""), recorded_at


def record_accepted_refresh(root: str, accepted: bool) -> None:
    """Remember that an owner accepted a refresh for `root`, with its wall time."""
    key = str(root or "")
    if not key:
        return
    with _ACCEPTED_REFRESH_LOCK:
        if accepted:
            _ACCEPTED_REFRESHES[key] = time.time()
        else:
            _ACCEPTED_REFRESHES.pop(key, None)


def accepted_refresh_at(root: Path | str) -> float:
    with _ACCEPTED_REFRESH_LOCK:
        return float(_ACCEPTED_REFRESHES.get(str(root), 0.0))


def clear_accepted_refreshes() -> None:
    with _ACCEPTED_REFRESH_LOCK:
        _ACCEPTED_REFRESHES.clear()


def walk_root(
    root: Path,
    skip_dirs: set[str],
    stop_event: threading.Event | None = None,
    exclude_path: Callable[[Path], bool] | None = None,
    max_files: int | None = None,
    relative_root: Path | None = None,
    operation: str = "",
) -> tuple[list[IndexEntry], bool]:
    """Collect every regular file under root, skipping skip_dirs. Cancellable."""
    entries, truncated, _ignored = _walk_root_with_metrics(
        root,
        skip_dirs,
        stop_event,
        exclude_path,
        max_files=max_files,
        relative_root=relative_root,
        operation=operation,
    )
    return entries, truncated


def _walk_root_with_metrics(
    root: Path,
    skip_dirs: set[str],
    stop_event: threading.Event | None = None,
    exclude_path: Callable[[Path], bool] | None = None,
    *,
    max_files: int | None = None,
    relative_root: Path | None = None,
    entry_root: Path | None = None,
    root_fd: int | None = None,
    resolved_root: Path | None = None,
    operation: str = "",
) -> tuple[list[IndexEntry], bool, int]:
    entries: list[IndexEntry] = []
    ignored = 0
    limit = max(1, int(max_files if max_files is not None else MAX_INDEX_FILES))
    result_root = entry_root or (relative_root or root)
    relative_prefix = Path(".")
    if root_fd is None and relative_root is not None:
        try:
            relative_prefix = root.relative_to(relative_root)
        except ValueError:
            relative_prefix = Path(".")
    truncated = False
    owned_root_context: contextlib.AbstractContextManager[Any] | None = None
    if root_fd is None:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        owned_root_context = filesystem_paths.safe_path(
            str(root),
            flags=directory_flags,
            operation=operation or "index_walk",
        )
        root_handle = owned_root_context.__enter__()
        root_fd = root_handle.descriptor
        if resolved_root is None:
            resolved_root = root_handle.resolved

    def include_directory(relative: Path) -> bool:
        nonlocal ignored
        result_path = result_root / relative_prefix / relative
        included = relative.name not in skip_dirs and (exclude_path is None or not exclude_path(result_path))
        if not included:
            ignored += 1
        return included

    try:
        walker = filesystem_paths.walk_directory(
            root_fd,
            include_directory=include_directory,
            operation=operation,
            requested_root=result_root / relative_prefix,
            resolved_root=resolved_root or root,
        )
        with contextlib.closing(walker):
            for relative_directory, _directory_fd, _dirnames, file_rows in walker:
                if stop_event is not None and stop_event.is_set():
                    return entries, True, ignored
                logical_directory = relative_prefix / relative_directory
                result_current = result_root / logical_directory
                for name, st in file_rows:
                    if len(entries) >= limit:
                        return entries, True, ignored
                    result_path = result_current / name
                    if exclude_path is not None and exclude_path(result_path):
                        ignored += 1
                        continue
                    rel = (logical_directory / name).as_posix()
                    entries.append((str(result_root / rel), name, rel, int(st.st_size), int(st.st_mtime)))
    finally:
        if owned_root_context is not None:
            owned_root_context.__exit__(None, None, None)
    return entries, truncated, ignored


def _entries_signature(entries: list[IndexEntry]) -> str:
    digest = hashlib.sha256()
    for path_str, name, rel, size, mtime in entries:
        digest.update(str(path_str).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(name).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(rel).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(int(size)).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(int(mtime)).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _estimated_sqlite_bytes(entries: list[IndexEntry]) -> int:
    # Include table/index overhead conservatively so the cap is checked before
    # doing a recoverable cache write.
    payload = sum(len(path.encode("utf-8", errors="surrogateescape")) + len(name.encode("utf-8", errors="surrogateescape")) + len(rel.encode("utf-8", errors="surrogateescape")) + 64 for path, name, rel, _size, _mtime in entries)
    return max(4096, payload * 2)


def _sqlite_paths(root: Path) -> list[Path]:
    path = _index_disk_path(root)
    return [path, Path(f"{path}-wal"), Path(f"{path}-shm")]


def _sqlite_storage_size(root: Path) -> int:
    total = 0
    for path in _sqlite_paths(root):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _drop_persisted_index(root: Path) -> bool:
    """Unlink every durable-store file for ``root`` (sqlite DB/WAL/SHM, manifest, heartbeat).

    Returns whether the physical delete actually COMPLETED: ``True`` when every file is gone (removed or
    already absent), ``False`` when a real ``OSError`` prevented removal. P0-2: the failure is LOGGED,
    never swallowed silently, and surfaced to the caller -- a caller that gates a pending-drop-token
    delete on the physical unlink must keep the token (and its retry owner) alive when this returns
    ``False`` so the store is never orphaned on disk with no owner left to GC it."""
    ok = True
    for path in [*_sqlite_paths(root), _index_manifest_path(root), _producer_heartbeat_path(root)]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.exception("failed to unlink durable search-index file %s", path)
            ok = False
    with _HEARTBEAT_LOCK:
        _HEARTBEAT_WRITTEN_AT.pop(str(root), None)
    return ok


def _connect_sqlite_index(root: Path) -> sqlite3.Connection:
    # The connection is owned from `sqlite3.connect()` through the setup PRAGMAs: if a PRAGMA raises
    # (a locked/corrupt store, an injected fault), close the just-created connection before
    # re-raising so a failed setup can never leak the descriptor. Callers that never receive the
    # connection (the raise happens before their assignment) cannot close it themselves, so the
    # owner that created it must. The setup error is preserved unchanged; only the descriptor is
    # reclaimed.
    preflight_mutable_roots(wal_databases=[_index_disk_path(root)])
    conn = sqlite3.connect(_index_disk_path(root), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except BaseException:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def _sqlite_index_connection(root: Path) -> Iterator[sqlite3.Connection]:
    """The ONE writable-connection owner for a per-root index database.

    Item 5 of the BFS index lifecycle fix: a `sqlite3.Connection` used as a context manager commits
    or rolls back on exit but NEVER closes, so every `with _connect_sqlite_index(root) as conn` (and
    the breadth-first builder's `with self._connect() as conn`) leaked a file descriptor until GC --
    and the post-build unlink turned those into `(deleted)` FDs. This owner closes the connection in
    `finally` REGARDLESS of outcome, so no writable BFS connection can leak. It commits on a clean
    exit and rolls back on an exception, matching the `Connection` context-manager semantics callers
    relied on; callers that run their own explicit `BEGIN IMMEDIATE ... COMMIT` are unaffected (the
    trailing commit is a no-op when no transaction is open)."""
    conn = _connect_sqlite_index(root)
    try:
        yield conn
        conn.commit()
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Streaming Quick Open (DOIT.p0.search-interactivity steps 1-3): the committed
# change journal + opaque delta cursor. `indexd` is the SOLE writer/traverser;
# HTTP processes are authenticated SQLite readers only. The journal records every
# committed upsert/delete with a monotonic publication revision IN THE SAME
# transaction as the entries+coverage write, so a follower can read the bounded
# committed deltas since a cursor without ever traversing the tree.
# --------------------------------------------------------------------------
JOURNAL_OP_UPSERT = "upsert"
JOURNAL_OP_DELETE = "delete"
# Bounds from the DOIT resolved-questions (q7): one delta read scans at most this many committed
# journal changes and returns at most this many matches; `more=true` schedules another bounded read.
JOURNAL_SCAN_LIMIT = 5_000
JOURNAL_MATCH_LIMIT = 500
# Retention: keep at least this many revisions per root so an ordinarily-paced client never rebases;
# an older cursor (its next revision already pruned) returns a typed rebase_required. A module global
# (not a literal) so a test can force a retention gap without writing 10,000 rows.
JOURNAL_RETENTION_REVISIONS = 10_000
# The opaque cursor format version. A cursor whose version this reader does not recognize is treated as
# malformed (rebase_required), never silently reinterpreted.
_DELTA_CURSOR_VERSION = 2


@dataclass(frozen=True)
class DeltaRebaseRequired:
    """Typed EXPECTED-failure result for a delta read that cannot be served incrementally (step 3).

    This is a typed result carrying a machine-readable ``reason``, NOT a swallowed exception: the
    caller must perform ONE full-snapshot repair and open a fresh cursor. Reasons: ``cursor_malformed``
    (unparseable/unknown-version cursor), ``cross_root``/``cross_policy`` (a cursor pinned to a
    different root/policy identity -- these can never be crossed), ``no_snapshot`` (no committed,
    non-tombstoned snapshot to read), ``generation_superseded`` (a newer generation owns the store),
    ``tombstoned`` (an explicit unindex superseded the cursor's identity), and ``retention_gap`` (the
    cursor's next revision was pruned or a full rewrite discontinued the journal)."""

    reason: str


@dataclass(frozen=True)
class DeltaResult:
    """One bounded, fenced page of committed journal deltas since a cursor (step 3).

    ``changes`` are the matched committed upserts/deletes (each a dict with ``operation`` plus the
    match metadata); ``cursor`` is the opaque encoding to pass to the next request; ``more`` is True
    when another bounded request is needed to drain the remaining committed changes; ``coverage`` is
    the same breadth-first coverage shape the snapshot read reports."""

    changes: list[dict[str, Any]]
    cursor: str
    more: bool
    coverage: dict[str, Any]


def _encode_delta_cursor(
    *,
    root: Path | str,
    policy: str,
    generation: int,
    revision: int,
    tombstone_identity: str,
    root_generation: tuple[int, int] | None = None,
) -> str:
    """Encode the opaque delta cursor. It pins the {root/policy identity, generation, publication
    revision, published tombstone identity} so a decoded cursor can never be applied to a different
    root, policy, generation, or post-unindex identity than the one that produced it."""
    identity = root_generation or _current_root_identity(Path(root))
    if identity is None:
        raise ValueError("authorized root identity is unavailable")
    payload = {
        "v": _DELTA_CURSOR_VERSION,
        "root": _canonical_root_key(Path(root)),
        "policy": str(policy),
        "generation": int(generation),
        "revision": int(revision),
        "tombstone": str(tombstone_identity or ""),
        "root_generation": list(identity),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_delta_cursor(cursor: str) -> dict[str, Any] | None:
    """Decode + validate the opaque cursor; ``None`` for any malformed/unknown-version input.

    A malformed cursor is an EXPECTED outcome (the caller maps it to ``rebase_required``), not a
    silent default: a wrong-typed or unparseable field returns ``None`` rather than a coerced value."""
    try:
        raw = base64.urlsafe_b64decode(str(cursor).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != _DELTA_CURSOR_VERSION:
        return None
    try:
        return {
            "root": str(payload["root"]),
            "policy": str(payload["policy"]),
            "generation": int(payload["generation"]),
            "revision": int(payload["revision"]),
            "tombstone": str(payload.get("tombstone") or ""),
            "root_generation": parse_root_identity(payload["root_generation"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _current_journal_revision(conn: sqlite3.Connection) -> int:
    """The monotonic publication-revision high-water mark, stored in metadata so it never regresses
    even after retention prunes every surviving journal row."""
    row = conn.execute("SELECT value FROM metadata WHERE key = 'journal_revision'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def _set_journal_revision(conn: sqlite3.Connection, value: int) -> None:
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES ('journal_revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(int(value)),),
    )


def _prune_change_journal(conn: sqlite3.Connection) -> None:
    """Bound the journal to the last ``JOURNAL_RETENTION_REVISIONS`` revisions. The high-water metadata
    is untouched, so a pruned-past cursor is detected as a retention gap rather than silently reused."""
    floor = _current_journal_revision(conn) - JOURNAL_RETENTION_REVISIONS
    if floor > 0:
        conn.execute("DELETE FROM change_journal WHERE revision <= ?", (floor,))


def _append_change_journal(
    conn: sqlite3.Connection,
    generation: int,
    tombstone_identity: str,
    records: list[tuple[str, str, str, str, int, int]],
) -> int:
    """Append committed changes to the journal, assigning sequential monotonic revisions.

    MUST run inside the caller's write transaction so the journal rows commit (or roll back) ATOMICALLY
    with the entries + coverage write -- a rolled-back publication exposes neither rows nor journal
    entries. ``records`` is a list of ``(operation, path, name, relative_path, size, mtime)`` for rows
    that were ACTUALLY committed (a max_files-truncated directory passes only its trimmed rows). Returns
    the new high-water revision."""
    revision = _current_journal_revision(conn)
    rows: list[tuple[Any, ...]] = []
    for operation, path, name, relative_path, size, mtime in records:
        revision += 1
        rows.append(
            (
                revision,
                int(generation),
                str(tombstone_identity or ""),
                str(operation),
                str(path),
                str(name),
                str(relative_path),
                int(size),
                int(mtime),
            )
        )
    if not rows:
        return revision
    conn.executemany(
        "INSERT INTO change_journal(revision, generation, tombstone_identity, operation, path, name, relative_path, size, mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    _set_journal_revision(conn, revision)
    _prune_change_journal(conn)
    return revision


def _mark_journal_discontinuity(conn: sqlite3.Connection) -> None:
    """Record that the entries table was rewritten wholesale (a full DFS replace / schema mismatch),
    so the per-row journal cannot describe the change: clear the journal and advance the high-water by
    one. Any live cursor then observes a gap (its next revision is unavailable) and rebases, instead of
    being told it is caught up while every row silently changed."""
    _set_journal_revision(conn, _current_journal_revision(conn) + 1)
    conn.execute("DELETE FROM change_journal")


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current_version != INDEX_FORMAT_VERSION:
        # v6 cannot adopt v4/v5 rows: neither format records the descriptor
        # generation that produced them. Reusing those rows for a pathname now
        # naming another directory would cross the authorization boundary.
        conn.execute("DROP TABLE IF EXISTS entries")
        conn.execute("DROP TABLE IF EXISTS metadata")
        conn.execute("DROP TABLE IF EXISTS directory_coverage")
        conn.execute("DROP TABLE IF EXISTS frontier")
        conn.execute("DROP TABLE IF EXISTS change_journal")
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        "path TEXT PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "relative_path TEXT NOT NULL, "
        "size INTEGER NOT NULL, "
        "mtime INTEGER NOT NULL, "
        "generation INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS entries_name_idx ON entries(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS entries_relative_path_idx ON entries(relative_path)")
    # Per-directory coverage: the durable record of which directories have been
    # scanned for which generation, so a restart resumes breadth-first work at the
    # shallowest pending depth without a new recursive discovery pass.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS directory_coverage ("
        "directory TEXT PRIMARY KEY, "
        "depth INTEGER NOT NULL, "
        "generation INTEGER NOT NULL, "
        "state TEXT NOT NULL, "
        "scanned_at REAL NOT NULL DEFAULT 0, "
        "file_count INTEGER NOT NULL DEFAULT 0, "
        "truncated INTEGER NOT NULL DEFAULT 0, "
        "error TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS directory_coverage_depth_idx ON directory_coverage(generation, depth)")
    # The bounded typed frontier, durable so a crash resumes it. Queue identity is
    # (root, canonical directory, generation); the directory is the primary key
    # within one root database so repeated demand for a directory coalesces.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS frontier ("
        "directory TEXT PRIMARY KEY, "
        "root TEXT NOT NULL, "
        "depth INTEGER NOT NULL, "
        "generation INTEGER NOT NULL, "
        "reason TEXT NOT NULL, "
        "priority INTEGER NOT NULL, "
        "enqueued_at REAL NOT NULL, "
        "retries INTEGER NOT NULL DEFAULT 0, "
        "seq INTEGER NOT NULL DEFAULT 0, "
        "state TEXT NOT NULL DEFAULT 'pending')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS frontier_order_idx ON frontier(generation, priority, depth, seq)")
    # Streaming Quick Open (step 1): the bounded, monotonic-revision change journal. Added COMPATIBLY
    # to the v5 store (a `CREATE TABLE IF NOT EXISTS`, not a version bump) so an existing v5 database
    # keeps its rows and simply gains the table on the next open. `indexd` is the sole writer; a
    # follower reads committed deltas since a cursor and never traverses. `revision` is monotonic and
    # unique per committed change; `generation`/`tombstone_identity` carry the same fence the entries
    # rows carry so a delta read can never serve a superseded/deleted generation.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS change_journal ("
        "revision INTEGER PRIMARY KEY, "
        "generation INTEGER NOT NULL, "
        "tombstone_identity TEXT NOT NULL DEFAULT '', "
        "operation TEXT NOT NULL, "
        "path TEXT NOT NULL, "
        "name TEXT NOT NULL DEFAULT '', "
        "relative_path TEXT NOT NULL DEFAULT '', "
        "size INTEGER NOT NULL DEFAULT 0, "
        "mtime INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS change_journal_gen_idx ON change_journal(generation, revision)")
    conn.execute(f"PRAGMA user_version={INDEX_FORMAT_VERSION}")


def _replace_sqlite_metadata(conn: sqlite3.Connection, metadata: dict[str, str]) -> None:
    conn.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        sorted(metadata.items()),
    )


def _replace_sqlite_entries(conn: sqlite3.Connection, entries: list[IndexEntry]) -> None:
    conn.execute("DELETE FROM entries")
    conn.executemany(
        "INSERT INTO entries(path, name, relative_path, size, mtime) VALUES (?, ?, ?, ?, ?)",
        ((str(path_str), str(name), str(rel), int(size), int(mtime)) for path_str, name, rel, size, mtime in entries),
    )


# The row publication fields are owned here because both the reader predicate and the BFS writer's
# clean-generation invalidation depend on exactly this vocabulary. A startup-only claim carries none
# of these fields; the first completed directory transaction writes all of them atomically.
_SNAPSHOT_PUBLICATION_METADATA_KEYS = (
    "built_at",
    "truncated",
    "entry_count",
    "entries_signature",
)
_PROGRESSIVE_PUBLICATION_METADATA_KEYS = (
    "published_generation",
    "published_depth",
    "frontier_depth",
    "frontier_size",
    "full_coverage",
    "last_progress_at",
)
# The metadata keys `_write_manifest` reads as REQUIRED. A published-snapshot metadata shape carries
# all of them, so a manifest must never be built from a startup-only claim.
_PUBLISHED_SNAPSHOT_METADATA_KEYS = (
    "skip_signature",
    "root",
    AUTHORIZED_ROOT_IDENTITY_FIELD,
    *_SNAPSHOT_PUBLICATION_METADATA_KEYS,
    "producer_epoch",
)


def _invalidate_snapshot_publication(conn: sqlite3.Connection) -> None:
    """Atomically retire every field that can make cleared rows look published."""

    keys = (*_SNAPSHOT_PUBLICATION_METADATA_KEYS, *_PROGRESSIVE_PUBLICATION_METADATA_KEYS)
    placeholders = ", ".join("?" for _key in keys)
    conn.execute(f"DELETE FROM metadata WHERE key IN ({placeholders})", keys)


def _is_published_snapshot_metadata(metadata: dict[str, str], generation: int) -> bool:
    """Item 2: has THIS generation written a COMPLETE typed published-snapshot metadata shape yet?

    A breadth-first build stamps `entries_signature` as ``bfs:<generation>:<count>`` inside the SAME
    transaction that writes the full published shape (`_recompute_progress`). So a signature that
    names this generation, together with every required manifest key present, is the one true
    signal that this generation has published at least one directory -- as opposed to a build that
    was cancelled before its first publish (only startup metadata, or a PRIOR generation's snapshot
    whose manifest must be preserved, not overwritten)."""
    signature = str(metadata.get("entries_signature", ""))
    if not signature.startswith(f"bfs:{int(generation)}:"):
        return False
    return all(key in metadata for key in _PUBLISHED_SNAPSHOT_METADATA_KEYS)


def _write_manifest(root: Path, metadata: dict[str, str]) -> None:
    manifest = {
        "version": INDEX_FORMAT_VERSION,
        "storage": "sqlite",
        "skip_signature": metadata["skip_signature"],
        "root": metadata["root"],
        AUTHORIZED_ROOT_IDENTITY_FIELD: list(parse_root_identity(json.loads(metadata[AUTHORIZED_ROOT_IDENTITY_FIELD]))),
        "built_at": float(metadata["built_at"]),
        "truncated": metadata["truncated"] == "1",
        "entry_count": int(metadata["entry_count"]),
        "entries_signature": metadata["entries_signature"],
        "producer_epoch": metadata["producer_epoch"],
        # v5 progressive-build coverage fields. A whole-tree DFS `_persist` leaves
        # these at their published defaults; the breadth-first builder overwrites
        # them per generation so status can distinguish a partially covered
        # snapshot from a fully reconciled one.
        "active_generation": int(metadata.get("active_generation") or 0),
        "published_generation": int(metadata.get("published_generation") or 0),
        "published_depth": int(metadata.get("published_depth") or 0),
        "frontier_depth": int(metadata.get("frontier_depth") or 0),
        "frontier_size": int(metadata.get("frontier_size") or 0),
        "full_coverage": str(metadata.get("full_coverage") or "0") == "1",
        "last_progress_at": float(metadata.get("last_progress_at") or 0.0),
        # Protocol #2: carry the frozen tombstone identity so a manifest-reading tombstone check
        # (`_snapshot_is_tombstoned`) fails closed on the same identity rule the sqlite read path uses.
        "tombstone_identity": str(metadata.get("tombstone_identity") or ""),
    }
    manifest_tmp = _index_manifest_path(root).with_suffix(".manifest.json.tmp")
    manifest_tmp.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    manifest_tmp.replace(_index_manifest_path(root))


# A status/diagnostic read must NEVER wait for the indexer. A read-only snapshot of the SQLite
# metadata is normally lock-free under WAL, but a writer holding an exclusive lock (VACUUM, an
# atomic finalization) can block a reader. This tiny busy timeout fails fast to the atomic manifest
# fallback instead of turning `/api/system-status` into a multi-second stall per configured root.
_COVERAGE_LIVE_READ_TIMEOUT_SECONDS = 0.05


def _coverage_shape(source: dict[str, Any], root: Path, *, live: bool) -> dict[str, Any]:
    """One dict shape for breadth-first coverage, from SQLite-string OR manifest-JSON values."""
    def _flag(value: Any) -> bool:
        return value is True or str(value) == "1"

    built_at = float(source.get("built_at") or 0.0)
    return {
        "root": str(root),
        # "live" = read from the writer's current SQLite metadata; "manifest" = the last atomic
        # manifest snapshot, served when the live read was locked/unavailable (an explicit
        # stale/unavailable indicator so a caller never mistakes a fallback for the current state).
        "source": "live" if live else "manifest",
        "built_at": built_at,
        "snapshot_age_seconds": (time.time() - built_at) if built_at else None,
        "active_generation": int(source.get("active_generation") or 0),
        "published_generation": int(source.get("published_generation") or 0),
        "published_depth": int(source.get("published_depth") or 0),
        "frontier_depth": int(source.get("frontier_depth") or 0),
        "frontier_size": int(source.get("frontier_size") or 0),
        "full_coverage": _flag(source.get("full_coverage")),
        "entry_count": int(source.get("entry_count") or 0),
        "truncated": _flag(source.get("truncated")),
        "last_progress_at": float(source.get("last_progress_at") or 0.0),
    }


def _coverage_from_live_sqlite(
    root: Path,
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    """BOUNDED read of the LIVE coverage `_recompute_progress` writes to SQLite metadata.

    Read-only, query-only, with a tiny busy timeout: a locked, malformed, or missing database fails
    fast and returns ``None`` so the caller falls back to the atomic manifest. It never blocks a
    status projection on the indexer.
    """
    try:
        conn = sqlite3.connect(
            f"file:{_index_disk_path(root).as_posix()}?mode=ro",
            uri=True,
            timeout=_COVERAGE_LIVE_READ_TIMEOUT_SECONDS,
        )
        try:
            conn.execute(f"PRAGMA busy_timeout={int(_COVERAGE_LIVE_READ_TIMEOUT_SECONDS * 1000)}")
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        return None
    metadata = {str(key): str(value) for key, value in rows}
    if metadata.get("version") != str(INDEX_FORMAT_VERSION):
        return None
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None or not _root_identity_matches(metadata, expected_identity):
        return None
    # An explicit unindex invalidates the snapshot: status must not report a deleted root's coverage as
    # live. A rebuild superseding the delete bumps its built_at past the marker, so its progress reads
    # normally again once it is the authoritative store.
    if _snapshot_is_tombstoned(root, metadata):
        return None
    return _coverage_shape(metadata, root, live=True)


def read_index_coverage(root: Path) -> dict[str, Any] | None:
    """Read a root's breadth-first coverage for status projection, LIVE and BOUNDED.

    The ONE coverage owner shared by the Daemons roster, `/api/fs/index-status`, and
    `--print-runtime-report`. The CURRENT source is the SQLite metadata `_recompute_progress` updates
    per published directory, so status shows layer-1 and partial progress as it happens instead of
    jumping from nothing to complete when the manifest is written at the end of the crawl. That read
    is bounded (see `_coverage_from_live_sqlite`); if it is locked or unavailable the atomic manifest
    JSON is the FALLBACK, tagged `source: "manifest"`. Returns ``None`` when neither exists. It does
    not verify the exclusion signature: a status caller reports whatever snapshot exists, stale or
    partial.
    """
    expected_identity = _current_root_identity(root)
    if expected_identity is None:
        return None
    live = _coverage_from_live_sqlite(root, expected_root_identity=expected_identity)
    if live is not None:
        return live
    try:
        raw = _index_manifest_path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if not _root_identity_matches(manifest, expected_identity):
        return None
    if _snapshot_is_tombstoned(root, manifest):
        return None
    return _coverage_shape(manifest, root, live=False)


def _pending_delta_is_empty(ri: RootIndex) -> bool:
    return not ri.pending_full_replace and not ri.pending_exact_deletes and not ri.pending_subtree_deletes and not ri.pending_upserts


def _clear_pending_delta(ri: RootIndex) -> None:
    ri.pending_full_replace = False
    ri.pending_exact_deletes.clear()
    ri.pending_subtree_deletes.clear()
    ri.pending_upserts.clear()


def _relative_to_root_posix(path: str, root: Path) -> str:
    """The stored `relative_path` for a canonical `path` under `root`, falling back to the basename."""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).name


def _apply_sqlite_delta(conn: sqlite3.Connection, ri: RootIndex) -> None:
    """Apply one coalesced set of path/subtree mutations without table rewrite.

    Step 2: every committed upsert/delete is also recorded in the change journal, in the SAME
    transaction the caller (`_persist`) commits, so a follower streaming deltas sees exactly the rows
    this delta committed. Deletes capture their `name`/`relative_path` BEFORE the row is removed so the
    journal delete can still be matched against a query."""
    journal_records: list[tuple[str, str, str, str, int, int]] = []
    for path in sorted(ri.pending_exact_deletes):
        row = conn.execute("SELECT name, relative_path FROM entries WHERE path = ?", (path,)).fetchone()
        name = str(row[0]) if row else Path(path).name
        relative_path = str(row[1]) if row else _relative_to_root_posix(path, ri.root)
        conn.execute("DELETE FROM entries WHERE path = ?", (path,))
        journal_records.append((JOURNAL_OP_DELETE, path, name, relative_path, 0, 0))
    for subtree in sorted(ri.pending_subtree_deletes, key=lambda value: (len(value), value)):
        affected = conn.execute(
            "SELECT path, name, relative_path FROM entries WHERE path = ? OR path LIKE ?",
            (subtree, f"{subtree}/%"),
        ).fetchall()
        conn.execute("DELETE FROM entries WHERE path = ? OR path LIKE ?", (subtree, f"{subtree}/%"))
        for affected_path, affected_name, affected_rel in affected:
            journal_records.append((JOURNAL_OP_DELETE, str(affected_path), str(affected_name), str(affected_rel), 0, 0))
    if ri.pending_upserts:
        conn.executemany(
            "INSERT INTO entries(path, name, relative_path, size, mtime) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET name=excluded.name, relative_path=excluded.relative_path, size=excluded.size, mtime=excluded.mtime",
            (
                (str(path_str), str(name), str(rel), int(size), int(mtime))
                for path_str, name, rel, size, mtime in ri.pending_upserts.values()
            ),
        )
        for path_str, name, rel, size, mtime in ri.pending_upserts.values():
            journal_records.append((JOURNAL_OP_UPSERT, str(path_str), str(name), str(rel), int(size), int(mtime)))
    if journal_records:
        _append_change_journal(conn, int(ri.active_generation or 0), ri.published_tombstone_identity or "", journal_records)


def _record_pending_delta(ri: RootIndex, dirty_paths: list[Path], build_kind: str) -> None:
    """Record the SQLite mutations represented by the already-built snapshot.

    This runs only in the indexer process.  A full build is allowed to replace
    the table; ordinary native file notifications become primary-key upserts or
    deletes, and a directory notification affects only that subtree.
    """
    if build_kind == "full":
        ri.pending_full_replace = True
        ri.pending_exact_deletes.clear()
        ri.pending_subtree_deletes.clear()
        ri.pending_upserts.clear()
        ri.entries_signature = _entries_signature(ri.entries)
        return

    for dirty in dirty_paths:
        path = str(dirty)
        if dirty.is_dir():
            ri.pending_subtree_deletes.add(path)
            # A later directory event subsumes queued child mutations from an
            # earlier batch that is still inside the persistence debounce.
            for pending_path in tuple(ri.pending_exact_deletes):
                if _path_is_within(Path(pending_path), dirty):
                    ri.pending_exact_deletes.discard(pending_path)
            for pending_path in tuple(ri.pending_upserts):
                if _path_is_within(Path(pending_path), dirty):
                    ri.pending_upserts.pop(pending_path, None)
            for entry_path, entry in ri.entry_by_path.items():
                if _path_is_within(Path(entry_path), dirty):
                    ri.pending_upserts[entry_path] = entry
            continue

        entry = ri.entry_by_path.get(path)
        if entry is None:
            ri.pending_upserts.pop(path, None)
            ri.pending_exact_deletes.add(path)
        else:
            ri.pending_exact_deletes.discard(path)
            ri.pending_upserts[path] = entry

    # This is a revision token, not a content checksum.  The delta transaction
    # itself is authoritative; avoiding an O(n) hash here is important for a
    # large root receiving one-file saves every few seconds.
    ri.entries_signature = f"delta:{time.time_ns()}:{len(ri.entries)}"


def _persist(ri: RootIndex, skip_dirs: set[str], exclude_signature: str = "", *, force: bool = False) -> None:
    with ri.lock:
        entries = list(ri.entries)
        published_root_identity = ri.published_root_identity
        entries_signature = ri.entries_signature or _entries_signature(entries)
        estimated_bytes = _estimated_sqlite_bytes(entries)
        full_replace = ri.pending_full_replace
        has_delta = not _pending_delta_is_empty(ri)
    persistence_allowed = (
        ri.persist_enabled
        and published_root_identity is not None
        and not ri.too_large
        and len(entries) <= ri.persist_max_files
        and estimated_bytes <= ri.persist_max_bytes
    )
    if not persistence_allowed:
        # A partial or over-budget index stays available in bounded RAM but must
        # not survive a restart as if it were complete.
        _drop_persisted_index(ri.root)
        ri.persisted = False
        ri.persist_pending = False
        ri.cache_bytes = 0
        return
    now = time.monotonic()
    if ri.persisted and has_delta and not force and now - ri.last_persisted_at < PERSIST_DEBOUNCE_SECONDS:
        ri.persist_pending = True
        return
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        signature = _disk_skip_signature(ri.root, skip_dirs, exclude_signature)
        metadata = {
            "version": str(INDEX_FORMAT_VERSION),
            "storage": "sqlite",
            "skip_signature": signature,
            "root": str(ri.root),
            AUTHORIZED_ROOT_IDENTITY_FIELD: _root_identity_metadata_value(published_root_identity),
            "built_at": repr(float(ri.built_at)),
            "truncated": "1" if ri.truncated else "0",
            "entry_count": str(len(entries)),
            "entries_signature": entries_signature,
            # M11: the writer names itself in the same atomic metadata dict that
            # already reaches both sqlite and the manifest, so a reader can tell
            # whether the process that produced these rows still exists.
            "producer_epoch": self_process_epoch(),
        }
        before_size = _sqlite_storage_size(ri.root)
        with _sqlite_index_connection(ri.root) as conn:
            _ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            db_metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            schema_matches = (
                db_metadata.get("version") == str(INDEX_FORMAT_VERSION)
                and db_metadata.get("storage") == "sqlite"
                and db_metadata.get("skip_signature") == signature
                and db_metadata.get("root") == str(ri.root)
                and _root_identity_matches(db_metadata, published_root_identity)
            )
            _replace_sqlite_metadata(conn, metadata)
            if full_replace or not schema_matches:
                _replace_sqlite_entries(conn, entries)
                # A wholesale table rewrite cannot be described row-by-row in the journal, so mark a
                # discontinuity: a live delta cursor rebases instead of missing every changed row.
                _mark_journal_discontinuity(conn)
            elif has_delta:
                _apply_sqlite_delta(conn, ri)
            elif db_metadata.get("entries_signature") != entries_signature:
                # A legacy/incomplete in-memory state with no recorded delta
                # cannot safely be reconciled row-by-row.
                _replace_sqlite_entries(conn, entries)
                _mark_journal_discontinuity(conn)
        _write_manifest(ri.root, metadata)
        after_size = _sqlite_storage_size(ri.root)
        bytes_written = max(0, after_size - before_size) if schema_matches else after_size
        if has_delta:
            # SQLite can reuse already-allocated WAL pages, making a real row
            # transaction appear as zero growth. Keep diagnostics honest about
            # mutation activity without representing it as a full rewrite.
            bytes_written = max(1, bytes_written)
        ri.write_bytes += bytes_written
        record_search_index_bytes_written(bytes_written)
        if after_size > ri.persist_max_bytes:
            _drop_persisted_index(ri.root)
            ri.persisted = False
            ri.persist_pending = False
            ri.cache_bytes = 0
            return
        ri.persisted = True
        ri.persist_pending = False
        ri.last_persisted_at = now
        ri.cache_bytes = after_size
        _clear_pending_delta(ri)
        touch_producer_heartbeat(ri.root, force=True)
    except (OSError, sqlite3.DatabaseError):
        pass


def _load_disk_metadata(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    if not _index_disk_path(root).exists():
        return None
    raw = read_json_file(_index_manifest_path(root), None)
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("root") != str(root):
        return None
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None or not _root_identity_matches(raw, expected_identity):
        return None
    if raw.get("version") != INDEX_FORMAT_VERSION or raw.get("storage") != "sqlite" or raw.get("skip_signature") != _disk_skip_signature(root, skip_dirs, exclude_signature):
        return None
    return raw


def _load_disk(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    honor_tombstone: bool = True,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[list[IndexEntry], float, bool, str, tuple[int, int]] | None:
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None:
        return None
    try:
        with _sqlite_index_connection(root) as conn:
            _ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN")
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if not _sqlite_metadata_matches(
                metadata,
                root,
                skip_dirs,
                exclude_signature,
                expected_root_identity=expected_identity,
            ):
                return None
            if not _row_serving_snapshot_metadata(metadata):
                return None
            # Fail closed on an explicit unindex: a snapshot built at or before the deletion must not be
            # adopted/served, so a tombstoned root triggers a fresh crawl rather than re-loading the
            # deleted rows. `honor_tombstone=False` is for a build reading back the rows it just wrote --
            # its own fresh output is authoritative regardless of a marker it is in the act of superseding.
            if honor_tombstone and _snapshot_is_tombstoned(root, metadata):
                return None
            rows = conn.execute(
                "SELECT path, name, relative_path, size, mtime FROM entries "
                "ORDER BY lower(relative_path), path"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError):
        return None
    entries = [(str(path), str(name), str(rel), int(size), int(mtime)) for path, name, rel, size, mtime in rows]
    try:
        built_at = float(metadata.get("built_at") or 0.0)
    except ValueError:
        built_at = 0.0
    return (
        entries,
        built_at,
        metadata.get("truncated") == "1",
        str(metadata.get("entries_signature") or ""),
        expected_identity,
    )


def _sqlite_metadata_matches(
    metadata: dict[str, Any],
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> bool:
    """Shape only: can these rows be read as this root's index at all?

    This deliberately has no freshness term. It answers readability, and a stale
    snapshot must stay READABLE - refusing to answer is worse than answering with
    a label. Whether the rows may be called authoritative is a different question,
    answered by `index_freshness` below.
    """
    expected_identity = expected_root_identity or _current_root_identity(root)
    return (
        expected_identity is not None
        and _root_identity_matches(metadata, expected_identity)
        and metadata.get("root") == str(root)
        and metadata.get("version") == str(INDEX_FORMAT_VERSION)
        and metadata.get("storage") == "sqlite"
        and metadata.get("skip_signature") == _disk_skip_signature(root, skip_dirs, exclude_signature)
    )


def _row_serving_snapshot_metadata(metadata: dict[str, Any]) -> bool:
    """Whether metadata identifies rows published by at least one completed directory transaction."""

    signature = str(metadata.get("entries_signature") or "")
    if signature.startswith("bfs:"):
        try:
            generation = int(signature.split(":", 2)[1])
        except (IndexError, ValueError):
            return False
        return _is_published_snapshot_metadata(metadata, generation)
    # Shipped v4 snapshots use an opaque non-BFS signature and predate built_at/producer_epoch.
    # Their non-empty signature is the durable publication marker; startup claims never create one.
    return bool(signature)


@dataclass(frozen=True)
class SnapshotFreshness:
    """The one freshness verdict for one root: shape, producer custody, and age.

    Every `index_state`, `index_coverage` and `refreshing_elsewhere` value in the
    search payloads is derived from this record so the two files cannot grow
    divergent copies of the same judgement.
    """

    state: str
    reason: str
    built_at: float
    snapshot_age_seconds: float | None
    producer_epoch: str
    producer_state: str
    vouched_age_seconds: float | None
    shape_matches: bool
    refresh_accepted: bool

    @property
    def authoritative(self) -> bool:
        """Ready/full may be claimed only with BOTH proofs, never with one."""
        return self.state == FRESHNESS_FRESH

    @property
    def producer_alive(self) -> bool:
        return self.producer_state == PRODUCER_RUNNING

    @property
    def refreshing_elsewhere(self) -> bool:
        """A live producer AND an accepted refresh it has not yet completed."""
        return bool(self.producer_alive and self.refresh_accepted)

    def payload_fields(self) -> dict[str, Any]:
        return {
            "freshness": self.state,
            "freshness_reason": self.reason,
            "producer_state": self.producer_state,
            "snapshot_age_seconds": self.snapshot_age_seconds,
            "stale": self.state in {FRESHNESS_STALE, FRESHNESS_ORPHANED},
            "refresh_requested": self.refresh_accepted,
            "refreshing_elsewhere": self.refreshing_elsewhere,
        }


def _metadata_built_at(metadata: dict[str, Any] | None) -> float:
    if not metadata:
        return 0.0
    try:
        return float(metadata.get("built_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _authoritative_store_metadata(root: Path) -> dict[str, Any] | None:
    """The committed sqlite snapshot metadata read WITHOUT skip matching, for tombstone reconciliation
    only (P1-3): the tombstone verdict needs just the stamped identity and ``built_at``. ``None`` when
    no sqlite store is readable, so a manifest-only reader keeps its own verdict. SQLite is the one
    committed truth a stale derived manifest defers to."""
    try:
        conn = sqlite3.connect(f"file:{_index_disk_path(root).as_posix()}?mode=ro", uri=True, timeout=30.0)
        try:
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        return None
    return metadata or None


def _authoritative_snapshot_is_tombstoned(root: Path, manifest_metadata: dict[str, Any] | None) -> bool:
    """Reconciled tombstone verdict for a manifest-first reader (P1-3).

    The manifest is a DERIVED cache; the committed sqlite store is the one truth. When the authoritative
    sqlite metadata is readable, the verdict is PURELY sqlite's -- in BOTH directions:

    * manifest says tombstoned but sqlite carries the current identity (a stale manifest whose replace
      failed while the sqlite commit landed) -> NOT tombstoned; readers agree with what search serves.
    * manifest says accepted (its stamp matches the marker) but sqlite carries a DIFFERENT identity (the
      divergent residual: the derived manifest was stamped to the current marker while sqlite still holds
      the old identity) -> tombstoned; a manifest-first reader can never accept a store the sqlite search
      reader rejects.

    When sqlite is unreadable, the manifest verdict stands (fail closed)."""
    store = _authoritative_store_metadata(root)
    if store is None:
        return _snapshot_is_tombstoned(root, manifest_metadata)
    return _snapshot_is_tombstoned(root, store)


def _raw_snapshot_metadata(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    """Shape-valid snapshot metadata WITHOUT the tombstone filter, AUTHORITATIVE-sqlite reconciled.

    Freshness diagnosis reads this so it can tell a tombstoned snapshot from a genuinely missing one;
    every real read/adopt surface uses `_disk_snapshot_metadata`/`_read_sqlite_index`, which fail closed.

    P1-3: sqlite is the one committed truth and the manifest is only a derived cache, so the RETURNED
    metadata (and therefore the identity a caller filters on) must be the authoritative one. When a
    shape-valid sqlite store is readable its metadata is returned directly -- a divergent/stale derived
    manifest can then neither advertise a live snapshot the sqlite search reader rejects nor hide one it
    still serves, because the caller's tombstone verdict runs on the SAME committed identity search uses.
    Only when no sqlite store is readable does the derived manifest stand (fail closed to its verdict)."""
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None:
        return None
    opened = _open_sqlite_snapshot(
        root,
        skip_dirs,
        exclude_signature,
        honor_tombstone=False,
        expected_root_identity=expected_identity,
    )
    if opened is not None:
        conn, metadata = opened
        conn.close()
        return metadata
    return _load_disk_metadata(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_identity,
    )


def _disk_snapshot_metadata(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    """Read this root's snapshot metadata, manifest first, sqlite as the fallback -- rejecting (fail
    closed) a snapshot an explicit unindex has invalidated, for BOTH the manifest and sqlite forms."""
    raw = _raw_snapshot_metadata(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_root_identity,
    )
    if raw is None or _snapshot_is_tombstoned(root, raw):
        return None
    return raw


def index_freshness(
    index: RootIndex | None,
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    metadata: dict[str, Any] | None = None,
    now: float | None = None,
) -> SnapshotFreshness:
    """Return the single freshness record for `root`, using no RPC to the producer.

    Liveness comes from /proc via the recorded producer epoch, and custody comes
    from the per-root heartbeat the writer refreshes without rebuilding. A build
    owner is its own producer, so its ready in-memory index needs no disk proof.
    """
    wall_now = time.time() if now is None else float(now)
    owner_process = background_owner_can_build()
    accepted_at = accepted_refresh_at(root)
    # P0-1: an in-memory ready owner is trusted without disk proof ONLY while a newer cross-process
    # unindex has not invalidated it. A build that published after another process wrote a fresh marker
    # froze the OLD identity, so its ready snapshot is deleted -- route it through the SAME verdict as
    # disk and fall through to the tombstoned/disk path rather than reporting FRESH for a deleted root.
    if (
        index is not None
        and index.ready
        and owner_process
        and _root_index_generation_matches(index)
        and not _root_index_is_tombstoned(index)
    ):
        built_at = float(index.built_at or 0.0)
        return SnapshotFreshness(
                state=FRESHNESS_FRESH,
            reason="own_index",
            built_at=built_at,
            snapshot_age_seconds=max(0.0, wall_now - built_at) if built_at else None,
            producer_epoch=self_process_epoch(),
            producer_state=PRODUCER_RUNNING,
            vouched_age_seconds=0.0,
            shape_matches=True,
            # This process IS the producer, so its own build is here, not elsewhere.
            refresh_accepted=False,
        )
    if metadata is None:
        expected_root_identity = index.root_fd_identity if index is not None else None
        metadata = _disk_snapshot_metadata(
            root,
            skip_dirs,
            exclude_signature,
            expected_root_identity=expected_root_identity,
        )
    if metadata is not None and _snapshot_is_tombstoned(root, metadata):
        # A caller passed a snapshot an explicit unindex already invalidated; treat it as deleted.
        metadata = None
    if metadata is None:
        # Distinguish an explicit unindex (deletion authority) from a genuinely absent snapshot, so
        # status reports `snapshot_tombstoned` rather than `no_matching_snapshot` and callers do not
        # mistake a deleted root for one that was never indexed.
        raw = _raw_snapshot_metadata(
            root,
            skip_dirs,
            exclude_signature,
            expected_root_identity=(index.root_fd_identity if index is not None else None),
        )
        if raw is not None and _snapshot_is_tombstoned(root, raw):
            tombstoned_built_at = _metadata_built_at(raw)
            return SnapshotFreshness(
                state=FRESHNESS_MISSING,
                reason="snapshot_tombstoned",
                built_at=tombstoned_built_at,
                snapshot_age_seconds=max(0.0, wall_now - tombstoned_built_at) if tombstoned_built_at else None,
                producer_epoch="",
                producer_state=PRODUCER_UNRECORDED,
                vouched_age_seconds=None,
                shape_matches=False,
                refresh_accepted=False,
            )
    shape_matches = metadata is not None
    built_at = _metadata_built_at(metadata)
    heartbeat_epoch, heartbeat_at = _read_producer_heartbeat(root)
    metadata_epoch = str((metadata or {}).get("producer_epoch") or "")
    producer_epoch = ""
    producer_state = PRODUCER_UNRECORDED
    vouched_at = 0.0
    for candidate_epoch, candidate_at in ((heartbeat_epoch, heartbeat_at), (metadata_epoch, built_at)):
        if not candidate_epoch:
            continue
        if not producer_epoch:
            producer_epoch = candidate_epoch
        if process_epoch_is_live(candidate_epoch):
            producer_epoch = candidate_epoch
            producer_state = PRODUCER_RUNNING
            vouched_at = max(candidate_at, built_at)
            break
        producer_state = PRODUCER_NOT_RUNNING
    vouched_age = max(0.0, wall_now - vouched_at) if vouched_at else None
    if not shape_matches:
        state, reason = FRESHNESS_MISSING, "no_matching_snapshot"
    elif producer_state == PRODUCER_UNRECORDED:
        state, reason = FRESHNESS_ORPHANED, "producer_epoch_unrecorded"
    elif producer_state == PRODUCER_NOT_RUNNING:
        state, reason = FRESHNESS_ORPHANED, "producer_not_running"
    elif vouched_age is None or vouched_age > PRODUCER_VOUCH_MAX_AGE_SECONDS:
        state, reason = FRESHNESS_STALE, "producer_vouch_expired"
    else:
        state, reason = FRESHNESS_FRESH, ""
    return SnapshotFreshness(
        state=state,
        reason=reason,
        built_at=built_at,
        snapshot_age_seconds=max(0.0, wall_now - built_at) if built_at else None,
        producer_epoch=producer_epoch,
        producer_state=producer_state,
        vouched_age_seconds=vouched_age,
        shape_matches=shape_matches,
        # An owner's refresh runs in this process; only a follower's accepted
        # request is evidence that another process is refreshing this root.
        refresh_accepted=bool(
            not owner_process
            and accepted_at
            and wall_now - accepted_at <= REFRESH_INFLIGHT_MAX_SECONDS
            and built_at < accepted_at
        ),
    )


def _open_sqlite_snapshot(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    honor_tombstone: bool,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[sqlite3.Connection, dict[str, Any]] | None:
    """Open a read-only snapshot connection, shape-validated, optionally honoring the unindex tombstone.

    ``honor_tombstone=True`` (every real read path) rejects a snapshot invalidated by an explicit
    unindex; ``honor_tombstone=False`` (freshness diagnosis only) returns the shape-valid metadata so
    the caller can report WHY it was rejected (`snapshot_tombstoned` vs `no_matching_snapshot`)."""
    conn: sqlite3.Connection | None = None
    expected_identity = expected_root_identity or _current_root_identity(root)
    if expected_identity is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{_index_disk_path(root).as_posix()}?mode=ro", uri=True, timeout=30.0)
        conn.execute("BEGIN")
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        if not _sqlite_metadata_matches(
            metadata,
            root,
            skip_dirs,
            exclude_signature,
            expected_root_identity=expected_identity,
        ):
            conn.close()
            return None
        if not _row_serving_snapshot_metadata(metadata):
            conn.close()
            return None
        if honor_tombstone and _snapshot_is_tombstoned(root, metadata):
            conn.close()
            return None
        return conn, metadata
    except (OSError, sqlite3.DatabaseError):
        # A read/validation failure AFTER connect must not leak the descriptor: the metadata query or
        # the shape check can raise once the connection is open, so close it here before returning.
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        return None


def _read_sqlite_index(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[sqlite3.Connection, dict[str, Any]] | None:
    """The tombstone-honoring read-only opener shared by follower search/recent and freshness reads."""
    return _open_sqlite_snapshot(
        root,
        skip_dirs,
        exclude_signature,
        honor_tombstone=True,
        expected_root_identity=expected_root_identity,
    )


def _metadata_truncated(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("truncated") or "") == "1"


def _sqlite_subsequence_pattern(term: str) -> str:
    """Match the same punctuation-tolerant character order as the fuzzy ranker."""
    escaped = [char.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for char in str(term or "").lower()]
    return f"%{'%'.join(escaped)}%"


def _sqlite_search_candidates(
    conn: sqlite3.Connection,
    literal_terms: list[str] | None,
) -> sqlite3.Cursor:
    terms = [term for term in (literal_terms or []) if term]
    if not terms:
        return conn.execute("SELECT path, name, relative_path, size, mtime FROM entries ORDER BY lower(relative_path), path")
    clauses = []
    params = []
    for term in terms:
        pattern = _sqlite_subsequence_pattern(term)
        clauses.append("(lower(name) LIKE ? ESCAPE '\\' OR lower(relative_path) LIKE ? ESCAPE '\\' OR lower(path) LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    where = " AND ".join(clauses)
    return conn.execute(f"SELECT path, name, relative_path, size, mtime FROM entries WHERE {where} ORDER BY lower(relative_path), path", params)


def search_disk_index(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str,
    match: Callable[[str, str, str], Any],
    max_results: int,
    literal_terms: list[str] | None = None,
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[list[dict[str, Any]], bool] | None:
    """Search a persisted index without making a follower own/build or deserialize it wholesale."""
    opened = _read_sqlite_index(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_root_identity,
    )
    if opened is None:
        return None
    conn, metadata = opened
    try:
        results: list[dict[str, Any]] = []
        rows = _sqlite_search_candidates(conn, literal_terms)
        for path_str, name, rel, size, mtime in rows:
            entry = match(str(path_str), str(name), str(rel))
            if entry is None:
                continue
            entry["size"] = int(size)
            entry["mtime"] = int(mtime)
            results.append(entry)
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    truncated = _metadata_truncated(metadata)
    results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
    if len(results) > max_results:
        truncated = True
        results = results[:max_results]
    return results, truncated


def current_delta_cursor(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> str | None:
    """The baseline cursor for a committed snapshot: the {root/policy, generation, current publication
    revision, published tombstone identity} a client seeds its first delta request with.

    ``None`` when there is no committed, non-tombstoned snapshot to read (the caller then has nothing to
    subscribe to yet). Read-only: it never traverses and never builds."""
    opened = _read_sqlite_index(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_root_identity,
    )
    if opened is None:
        return None
    conn, metadata = opened
    try:
        revision = _current_journal_revision(conn)
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return _encode_delta_cursor(
        root=root,
        policy=str(exclude_signature),
        generation=int(metadata.get("published_generation") or 0),
        revision=revision,
        tombstone_identity=_metadata_tombstone_identity(metadata),
        root_generation=_metadata_root_identity(metadata),
    )


def search_disk_index_delta(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str,
    match: Callable[[str, str, str], Any],
    cursor: str,
    *,
    scan_limit: int | None = None,
    match_limit: int | None = None,
    expected_root_identity: tuple[int, int] | None = None,
) -> DeltaResult | DeltaRebaseRequired:
    """Return the bounded, fenced committed journal deltas since ``cursor`` (step 3).

    Reads ONLY committed SQLite -- no traversal in the HTTP process. Fails closed to a typed
    ``rebase_required`` whenever it cannot serve a clean incremental page: a malformed / cross-root /
    cross-policy cursor, a missing/tombstoned snapshot, a superseded generation, a changed tombstone
    identity, or a retention gap (the cursor's next revision was pruned or a full rewrite discontinued
    the journal). It never mixes rows from more than one generation and never serves a superseded one.
    The per-response bounds (scan ≤5,000 changes, return ≤500 matches) are the DOIT's; ``more=true``
    means another bounded request is needed."""
    scan_limit = JOURNAL_SCAN_LIMIT if scan_limit is None else int(scan_limit)
    match_limit = JOURNAL_MATCH_LIMIT if match_limit is None else int(match_limit)

    decoded = _decode_delta_cursor(cursor)
    if decoded is None:
        return DeltaRebaseRequired("cursor_malformed")
    # Root/policy identities can NEVER be crossed by a cursor.
    if decoded["root"] != _canonical_root_key(root):
        return DeltaRebaseRequired("cross_root")
    if decoded["policy"] != str(exclude_signature):
        return DeltaRebaseRequired("cross_policy")

    opened = _read_sqlite_index(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_root_identity,
    )
    if opened is None:
        # No committed, non-tombstoned, shape-matching snapshot to read (missing, tombstoned, or a
        # different policy than the store was built under): the caller must take a fresh snapshot.
        return DeltaRebaseRequired("no_snapshot")
    conn, metadata = opened
    try:
        current_generation = int(metadata.get("published_generation") or 0)
        if decoded["root_generation"] != _metadata_root_identity(metadata):
            return DeltaRebaseRequired("root_generation_superseded")
        if decoded["generation"] != current_generation:
            return DeltaRebaseRequired("generation_superseded")
        if decoded["tombstone"] != _metadata_tombstone_identity(metadata):
            return DeltaRebaseRequired("tombstoned")

        cursor_revision = int(decoded["revision"])
        high_water = _current_journal_revision(conn)
        if cursor_revision > high_water:
            # A cursor ahead of the store's own high-water cannot describe a real position in it.
            return DeltaRebaseRequired("retention_gap")

        next_row = conn.execute(
            "SELECT MIN(revision) FROM change_journal WHERE generation = ? AND revision > ?",
            (current_generation, cursor_revision),
        ).fetchone()
        next_available = next_row[0] if next_row else None
        if cursor_revision < high_water:
            # There ARE committed changes after the cursor. If the first one available is not exactly
            # the cursor's next revision, the intervening revisions were pruned (or a wholesale rewrite
            # discontinued the journal): the client missed committed changes and must rebase.
            if next_available is None or int(next_available) > cursor_revision + 1:
                return DeltaRebaseRequired("retention_gap")

        rows = conn.execute(
            "SELECT revision, operation, path, name, relative_path, size, mtime FROM change_journal "
            "WHERE generation = ? AND revision > ? ORDER BY revision ASC LIMIT ?",
            (current_generation, cursor_revision, scan_limit + 1),
        ).fetchall()
    except sqlite3.DatabaseError:
        return DeltaRebaseRequired("no_snapshot")
    finally:
        conn.close()

    scanned_more = len(rows) > scan_limit
    rows = rows[:scan_limit]
    changes: list[dict[str, Any]] = []
    new_revision = cursor_revision
    hit_match_cap = False
    for revision, operation, path_str, name, relative_path, size, mtime in rows:
        if len(changes) >= match_limit:
            hit_match_cap = True
            break
        new_revision = int(revision)
        matched = match(str(path_str), str(name), str(relative_path))
        if matched is None:
            # A change to a file that does not match this query is irrelevant to this result set; the
            # cursor still advances past it so the client is not asked to re-scan it.
            continue
        if str(operation) == JOURNAL_OP_DELETE:
            changes.append(
                {
                    "operation": JOURNAL_OP_DELETE,
                    "path": str(path_str),
                    "name": str(name),
                    "relative_path": str(relative_path),
                }
            )
        else:
            matched["operation"] = JOURNAL_OP_UPSERT
            matched["size"] = int(size)
            matched["mtime"] = int(mtime)
            changes.append(matched)
    # `more` is true only when a bound stopped us with committed changes still unread: the match cap
    # (we broke early) or the scan cap (there was an extra row beyond the page). When neither fired we
    # fetched every remaining change for this generation, so the client is caught up.
    more = hit_match_cap or scanned_more
    return DeltaResult(
        changes=changes,
        cursor=_encode_delta_cursor(
            root=root,
            policy=str(exclude_signature),
            generation=current_generation,
            revision=new_revision,
            tombstone_identity=decoded["tombstone"],
            root_generation=decoded["root_generation"],
        ),
        more=more,
        coverage=_coverage_shape(metadata, root, live=True),
    )


def recent_disk_entries(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str,
    max_results: int,
    make_entry: Callable[[str, str, str], dict[str, Any]],
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[list[dict[str, Any]], bool] | None:
    """Return recent entries from a persisted index without loading all rows into follower memory."""
    opened = _read_sqlite_index(
        root,
        skip_dirs,
        exclude_signature,
        expected_root_identity=expected_root_identity,
    )
    if opened is None:
        return None
    conn, metadata = opened
    try:
        rows = conn.execute(
            "SELECT path, name, relative_path, size, mtime FROM entries ORDER BY mtime DESC LIMIT ?",
            (max_results + 1,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    truncated = _metadata_truncated(metadata) or len(rows) > max_results
    results = []
    for path_str, name, rel, size, mtime in rows[:max_results]:
        entry = make_entry(str(path_str), str(name), str(rel))
        entry["size"] = int(size)
        entry["mtime"] = int(mtime)
        results.append(entry)
    return results, truncated


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _coalesced_paths(paths: set[Path]) -> list[Path]:
    result: list[Path] = []
    for path in sorted(paths, key=lambda item: (len(item.parts), str(item))):
        path_text = str(path)
        if any(filesystem_paths._normalized_absolute_text_is_within(path_text, str(parent)) for parent in result):
            continue
        result.append(path)
    return result


def _path_is_below_skipped_directory(path: Path, root: Path, skip_dirs: set[str]) -> bool:
    """Return whether a dirty path is inside a skipped directory below ``root``."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part in skip_dirs for part in relative.parts)


def _refresh_dirty_subtrees(
    ri: RootIndex,
    dirty_paths: list[Path],
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None,
    access_descriptor: int,
    operation: str,
) -> tuple[list[IndexEntry], bool, int, int]:
    with ri.lock:
        previous = list(ri.entries)
        previous_by_path = dict(ri.entry_by_path)
        previously_truncated = ri.truncated
    usable_dirty_paths: list[Path] = []
    ignored = 0
    for dirty in dirty_paths:
        if _path_is_below_skipped_directory(dirty, ri.root, skip_dirs):
            ignored += 1
            continue
        if exclude_path is not None and exclude_path(dirty):
            ignored += 1
            continue
        try:
            dirty.relative_to(ri.root)
        except ValueError:
            ignored += 1
            continue
        usable_dirty_paths.append(dirty)
    if not usable_dirty_paths:
        # Excluded work must be a true no-op. In particular, do not filter and
        # re-sort every retained row merely to ignore a .git/cache event.
        return previous, previously_truncated, 0, ignored

    with contextlib.ExitStack() as descriptors:
        opened: dict[Path, tuple[int | None, os.stat_result | None]] = {}
        for dirty in usable_dirty_paths:
            try:
                handle = descriptors.enter_context(
                    filesystem_paths.safe_descendant(
                        access_descriptor,
                        ri.root,
                        ri.root,
                        dirty.relative_to(ri.root),
                        flags=os.O_RDONLY,
                        operation=operation,
                    )
                )
                descriptor = handle.descriptor
            except (OSError, filesystem_paths.FilesystemError):
                opened[dirty] = (None, None)
                continue
            opened[dirty] = (descriptor, handle.stat_result)

        # Native backends overwhelmingly report a regular file rather than its
        # parent directory.  Do not turn that into an 80k-row comprehension and
        # sort: update the one list slot and preserve the existing sorted snapshot.
        if all(
            (st is None or not stat.S_ISDIR(st.st_mode))
            and (st is not None or str(dirty) in previous_by_path)
            for dirty, (_descriptor, st) in opened.items()
        ):
            entries = previous
            entry_by_path = previous_by_path
            refreshed_count = 0
            for dirty, (_descriptor, st) in opened.items():
                path = str(dirty)
                old = entry_by_path.pop(path, None)
                if old is not None:
                    try:
                        entries.remove(old)
                    except ValueError:
                        pass
                if st is None:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    ignored += 1
                    continue
                entry = (path, dirty.name, dirty.relative_to(ri.root).as_posix(), int(st.st_size), int(st.st_mtime))
                key = entry[2].lower()
                left, right = 0, len(entries)
                while left < right:
                    midpoint = (left + right) // 2
                    if entries[midpoint][2].lower() < key:
                        left = midpoint + 1
                    else:
                        right = midpoint
                entries.insert(left, entry)
                entry_by_path[path] = entry
                refreshed_count += 1
            return entries, previously_truncated, refreshed_count, ignored

        retained = [
            entry
            for entry in previous
            if not any(_path_is_within(Path(entry[0]), dirty) for dirty in usable_dirty_paths)
        ]
        refreshed: list[IndexEntry] = []
        truncated = previously_truncated
        for dirty, (descriptor, st) in opened.items():
            remaining = ri.max_files - len(retained) - len(refreshed)
            if remaining <= 0:
                truncated = True
                break
            if descriptor is not None and st is not None and stat.S_ISDIR(st.st_mode):
                access_dirty = filesystem_paths.descriptor_path(descriptor)
                entries, subtree_truncated, subtree_ignored = _walk_root_with_metrics(
                    access_dirty,
                    skip_dirs,
                    ri.stop_event,
                    exclude_path,
                    max_files=remaining,
                    relative_root=access_dirty,
                    entry_root=dirty,
                    root_fd=descriptor,
                    resolved_root=dirty,
                    operation=operation,
                )
                relative_prefix = dirty.relative_to(ri.root)
                entries = [
                    (path, name, (relative_prefix / relative).as_posix(), size, mtime)
                    for path, name, relative, size, mtime in entries
                ]
                refreshed.extend(entries)
                truncated = truncated or subtree_truncated
                ignored += subtree_ignored
                continue
            if st is None:
                continue
            if stat.S_ISREG(st.st_mode):
                refreshed.append((str(dirty), dirty.name, dirty.relative_to(ri.root).as_posix(), int(st.st_size), int(st.st_mtime)))
            else:
                ignored += 1
        entries = sorted([*retained, *refreshed], key=lambda entry: entry[2].lower())
        if len(entries) > ri.max_files:
            entries = entries[:ri.max_files]
            truncated = True
        return entries, truncated, len(refreshed), ignored


def mark_paths_dirty(
    paths: list[Path],
    include_root: Callable[[Path, Path], bool] | None = None,
    prepare_root: Callable[[Path], None] | None = None,
) -> dict[Path, set[Path]]:
    """Group invalidations by index and coalesce each dirty set once per batch."""
    targets = sorted({path.expanduser().resolve(strict=False) for path in paths}, key=str)
    paths_by_root: dict[Path, set[Path]] = {}
    for target in targets:
        for root in indexed_ancestor_roots(target):
            # A root-level filesystem notification has no bounded incremental
            # subtree. Native backends can emit it while merely registering or
            # reconciling a watch, so let the normal safety refresh handle it
            # rather than immediately rewalking the entire index.
            if target == root:
                continue
            if include_root is not None and not include_root(root, target):
                continue
            paths_by_root.setdefault(root, set()).add(target)

    def mark_indexes(indexes: dict[Path, RootIndex | None], already_marked: dict[Path, RootIndex]) -> None:
        for root, targets_for_root in paths_by_root.items():
            ri = indexes[root]
            if ri is None or ri is already_marked.get(root):
                continue
            with ri.lock:
                ri.dirty_paths.update(targets_for_root)
                before_coalesce = len(ri.dirty_paths)
                ri.dirty_paths = set(_coalesced_paths(ri.dirty_paths))
                # Item 6: this batch is concrete change evidence for the smallest coalesced subtrees,
                # so it heats the root. The score is bounded and decays in `schedule_refreshes`.
                ri.hot_score = min(ri.hot_score + len(targets_for_root), float(HOT_MAX_SCORE))
                ri.last_hot_at = time.time()
                ri.dirty_mark_batches += 1
                ri.dirty_mark_paths += len(targets_for_root)
                ri.last_dirty_batch_paths = len(targets_for_root)
                ri.last_dirty_before_coalesce = before_coalesce
                ri.last_dirty_after_coalesce = len(ri.dirty_paths)
                ri.max_dirty_before_coalesce = max(ri.max_dirty_before_coalesce, before_coalesce)
            already_marked[root] = ri

    with _REGISTRY_LOCK:
        indexes = {root: _REGISTRY.get(str(root)) for root in paths_by_root}
    marked_indexes: dict[Path, RootIndex] = {}
    mark_indexes(indexes, marked_indexes)
    if prepare_root is not None:
        for root in paths_by_root:
            prepare_root(root)
        with _REGISTRY_LOCK:
            prepared_indexes = {root: _REGISTRY.get(str(root)) for root in paths_by_root}
        mark_indexes(prepared_indexes, marked_indexes)
    return paths_by_root


def mark_path_dirty(
    path: Path,
    include_root: Callable[[Path, Path], bool] | None = None,
) -> list[Path]:
    """Compatibility wrapper for a one-path dirty batch."""
    return sorted(mark_paths_dirty([path], include_root=include_root), key=lambda root: (len(root.parts), str(root)))


def schedule_refreshes(now: float | None = None) -> int:
    """Start at most one refresh per dirty/stale root; queries never call this."""
    if not background_owner_can_build():
        return 0
    wall_now = time.time() if now is None else float(now)
    monotonic_now = time.monotonic()
    with _REGISTRY_LOCK:
        indexes = list(_REGISTRY.values())
    started = 0
    for ri in indexes:
        # M11: the owner's cheap custody claim. This is the tick that lets a
        # reader distinguish "idle producer still watching this root" from
        # "producer gone", and it rebuilds nothing.
        touch_producer_heartbeat(ri.root)
        with ri.lock:
            should_flush = ri.persist_pending and monotonic_now - ri.last_persisted_at >= PERSIST_DEBOUNCE_SECONDS
            freshness_anchor = ri.last_full_build_at or ri.built_at
            has_dirty = bool(ri.dirty_paths)
            ttl_stale = ri.ready and ri.refresh_seconds > 0 and wall_now - freshness_anchor >= ri.refresh_seconds
            building = ri.building
            skip_dirs = set(ri.skip_dirs)
            exclude_path = ri.exclude_path
            exclude_signature = ri.exclude_signature
            # Item 6 heat decay: with no new change evidence for HOT_INACTIVITY_SECONDS the root is
            # cold again, so an old burst neither counts as hot nor keeps spending the starvation
            # budget. Decay is evaluated on this one scheduler tick, not by a second timer thread.
            if ri.last_hot_at and wall_now - ri.last_hot_at >= HOT_INACTIVITY_SECONDS:
                ri.hot_score = 0.0
                ri.consecutive_hot_repairs = 0
            # Item 6 starvation bound: a forever-hot root always has a dirty subtree and would take
            # the incremental repair branch forever, starving the breadth/safety reconciliation.
            # After HOT_REPAIR_STARVATION_BOUND consecutive hot repairs, yield ONE full-safety-refresh
            # instead. The full re-list covers the pending dirty subtrees too, so clearing them here
            # loses no repair -- a change that arrives after the yield simply re-marks and re-heats.
            starving = has_dirty and not building and ri.consecutive_hot_repairs >= HOT_REPAIR_STARVATION_BOUND
            if starving:
                ri.dirty_paths.clear()
                has_dirty = False
                ttl_stale = True
                ri.consecutive_hot_repairs = 0
            should_refresh = has_dirty or ttl_stale
            # Item 7: a purely TTL-stale/yielded full refresh (no dirty subtrees) is the
            # LOWEST-priority safety reconciliation. It runs through the SAME breadth-first frontier
            # as startup, only with the `full-safety-refresh` precedence label, so it is resumable and
            # preemptible and never a second scheduler. A dirty batch is an incremental subtree
            # repair, not a safety refresh, so it keeps the default reason.
            build_reason = SAFETY_REFRESH_REASON if (ttl_stale and not has_dirty) else ""
            if should_refresh and not building:
                if has_dirty:
                    ri.consecutive_hot_repairs += 1
                else:
                    ri.consecutive_hot_repairs = 0
        # P0-3: revalidate snapshot identity before persisting or starting. A clear/unindex may have
        # retired this object (removed it from the registry) between the snapshot above and here; a
        # retired object must neither persist through its stale fd nor start a new worker.
        if should_flush and not building and _registry_owner_is(ri):
            _persist(ri, skip_dirs, exclude_signature, force=True)
        if should_refresh and not building:
            if _start_build(ri, skip_dirs, exclude_path=exclude_path, exclude_signature=exclude_signature, build_reason=build_reason):
                started += 1
    return started


def runtime_diagnostics() -> dict[str, Any]:
    with _REGISTRY_LOCK:
        indexes = list(_REGISTRY.values())
    roots = []
    for ri in indexes:
        with ri.lock:
            roots.append({
                "root": str(ri.root),
                "state": "building" if ri.building else ("too_large" if ri.too_large else ("ready" if ri.ready else "missing")),
                "entries": len(ri.entries) if ri.ready else ri.disk_entry_count,
                "build_count": ri.build_count,
                "full_build_count": ri.full_build_count,
                "incremental_build_count": ri.incremental_build_count,
                "last_duration_ms": round(ri.build_duration_ms, 3),
                "scanned_entries": ri.scanned_entries,
                "ignored_entries": ri.ignored_entries,
                "truncated": ri.truncated,
                "too_large": ri.too_large,
                "dirty_subtrees": len(ri.dirty_paths),
                "hot_score": round(ri.hot_score, 3),
                "last_hot_at": ri.last_hot_at,
                "consecutive_hot_repairs": ri.consecutive_hot_repairs,
                "dirty_mark_batches": ri.dirty_mark_batches,
                "dirty_mark_paths": ri.dirty_mark_paths,
                "last_dirty_batch_paths": ri.last_dirty_batch_paths,
                "last_dirty_before_coalesce": ri.last_dirty_before_coalesce,
                "last_dirty_after_coalesce": ri.last_dirty_after_coalesce,
                "max_dirty_before_coalesce": ri.max_dirty_before_coalesce,
                "cache_bytes": ri.cache_bytes,
                "write_bytes": ri.write_bytes,
                "persisted": ri.persisted,
                "persist_pending": ri.persist_pending,
                "max_files": ri.max_files,
                "refresh_seconds": ri.refresh_seconds,
                "persist_max_files": ri.persist_max_files,
                "persist_max_bytes": ri.persist_max_bytes,
            })
    roots.sort(key=lambda row: row["root"])
    # Late retirees stay VISIBLE here (item 4): a worker that outran the retirement deadline is not
    # dropped, so diagnostics still name its root, whether its worker is alive, how long ago it was
    # retired, and whether a durable drop is pending -- until its own `finally` closes the fd.
    with _REGISTRY_LOCK:
        retiring_indexes = list(_RETIRING.values())
        # P1: `deferred_drop` is derived from the ROOT-LEVEL pending-drop token under `_REGISTRY_LOCK`,
        # not a per-worker flag. The old flag lied: after clear-then-unindex with no active owner the
        # root has a pending drop but the retiring row reported `deferred_drop=False`.
        pending_drop_keys = set(_PENDING_DROPS)
    now = time.monotonic()
    retiring = []
    for ri in retiring_indexes:
        with ri.lock:
            thread = ri.thread
            started = ri.retirement_started_at
        deferred_drop = ri.root_key in pending_drop_keys
        retiring.append({
            "root": str(ri.root),
            "worker_alive": bool(thread is not None and thread.is_alive()),
            "worker_ident": thread.ident if thread is not None else None,
            "retirement_age_seconds": max(0.0, now - started) if started else None,
            "deferred_drop": deferred_drop,
            "completion_set": ri.completion.is_set(),
        })
    retiring.sort(key=lambda row: row["root"])
    return {
        "root_count": len(roots),
        "retiring": retiring,
        "retiring_count": len(retiring),
        "build_count": sum(int(row["build_count"]) for row in roots),
        "full_build_count": sum(int(row["full_build_count"]) for row in roots),
        "incremental_build_count": sum(int(row["incremental_build_count"]) for row in roots),
        "scanned_entries": sum(int(row["scanned_entries"]) for row in roots),
        "ignored_entries": sum(int(row["ignored_entries"]) for row in roots),
        "cache_bytes": sum(int(row["cache_bytes"]) for row in roots),
        "write_bytes": sum(int(row["write_bytes"]) for row in roots),
        "truncated_roots": sum(1 for row in roots if row["truncated"]),
        "roots": roots,
    }


def _next_bfs_generation(root: Path) -> int:
    """Return the next generation for a breadth-first build of ``root``.

    Generation fencing (bfs_index) requires each new build to carry a value strictly greater than
    the persisted ``active_generation``, so an abandoned older build cannot overwrite the newer
    one. The value is read outside the per-root build lock the crawl itself takes; the single
    writer means no other producer advances it in between.
    """
    try:
        with _sqlite_index_connection(root) as conn:
            _ensure_sqlite_schema(conn)
            row = conn.execute("SELECT value FROM metadata WHERE key = 'active_generation'").fetchone()
    except (OSError, sqlite3.DatabaseError):
        return 1
    active = int(row[0]) if row and str(row[0]).isdigit() else 0
    return active + 1


def _resumable_frontier_generation(
    root: Path,
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> int | None:
    """Return the persisted ``active_generation`` IFF it still has pending frontier rows to resume.

    P0-4: searchable state and crawl completion are SEPARATE facts. A compatible partial can load
    ``ready=True`` (its published layers are searchable) while a durable frontier of not-yet-listed
    directories remains for the generation that owns the store. In that case the crawl must RESUME
    that exact generation rather than allocate ``active+1`` and re-list the root; this returns the
    generation to resume, or ``None`` when the frontier is drained (allocate a fresh generation)."""
    try:
        with _sqlite_index_connection(root) as conn:
            _ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN")
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            expected_identity = expected_root_identity or _current_root_identity(root)
            if expected_identity is None or not _root_identity_matches(metadata, expected_identity):
                return None
            # An explicit unindex invalidates the whole snapshot: do NOT resume its frontier. Post-unindex
            # work must start a fresh generation that supersedes the delete, not continue crawling into a
            # store the user deleted.
            if _snapshot_is_tombstoned(root, metadata):
                return None
            active_value = metadata.get("active_generation")
            active = int(active_value) if active_value is not None and str(active_value).isdigit() else 0
            if active <= 0:
                return None
            pending = conn.execute(
                "SELECT 1 FROM frontier WHERE generation = ? AND state = 'pending' LIMIT 1",
                (active,),
            ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        return None
    return active if pending is not None else None


def _complete_publication(
    ri: RootIndex,
    *,
    captured_drop_token: str | None,
    captured_tombstone_identity: str | None = None,
    captured_root_identity: tuple[int, int] | None = None,
) -> None:
    """The ONE publication-completion owner for EVERY successful build (BFS full, DFS full, incremental).

    A publication has already committed this generation's in-memory ready fence; this owner then
    disposes of the shared cross-build facts so no producer path grows a divergent copy of them.

    It NEVER clears the durable unindex tombstone (protocol #1). Instead it STAMPS the identity the
    build FROZE at its start into the snapshot metadata: a reader then accepts this snapshot only when
    its stamp equals the current marker identity. A build that started before a NEWER unindex stamps the
    OLD identity (or none), so once the marker changes its snapshot is rejected -- the correctness the
    artificial ``built_at`` bump used to fake by wall-clock time. ``captured_tombstone_identity`` is an
    OPTIONAL keyword (default ``None``) so a direct caller may complete a publication without one.

    It then CONDITIONALLY supersedes the pending durable-drop token the build captured when it began
    (P0-5/P0-6): only that exact token, never a newer unindex requested after the build started -- and
    (P1-3) only AFTER the AUTHORITATIVE current-identity sqlite snapshot commits. If the authoritative
    stamp did not land (no durable store, or a failed metadata commit), the durable-drop intent stays
    queued rather than being voided on a snapshot that cannot prove it superseded the marker."""
    expected_root_identity = captured_root_identity
    if expected_root_identity is None:
        with ri.lock:
            expected_root_identity = ri.root_fd_identity
    committed = _stamp_snapshot_tombstone_identity(
        ri.root,
        captured_tombstone_identity,
        expected_root_identity=expected_root_identity,
    )
    if committed:
        _supersede_pending_drop(ri.root, captured_drop_token)


def _persisted_skip_signature(root: Path) -> str:
    """Return the exclusion signature the persisted snapshot was built under, or ``""``."""
    try:
        with _sqlite_index_connection(root) as conn:
            _ensure_sqlite_schema(conn)
            row = conn.execute("SELECT value FROM metadata WHERE key = 'skip_signature'").fetchone()
    except (OSError, sqlite3.DatabaseError):
        return ""
    return str(row[0]) if row and row[0] is not None else ""


def _persisted_tombstone_identity(root: Path) -> str:
    """The tombstone identity stamped into the persisted (authoritative sqlite) snapshot, ``""`` when
    unstamped or unreadable. A build that ADOPTS a compatible persisted snapshot inherits this as its
    in-memory published identity (P0-1), so a later marker change evicts it by the same rule that
    would reject the snapshot on disk."""
    return _metadata_tombstone_identity(_authoritative_store_metadata(root))


def _adopt_disk_snapshot(
    ri: RootIndex,
    disk: tuple[list[IndexEntry], float, bool, str, tuple[int, int]],
    expected_signature: str,
) -> None:
    """Adopt a compatible persisted snapshot into the in-memory index without re-walking.

    One owner for the handoff/restart fast path shared by the DFS and breadth-first full builds:
    another process (or a prior run of this one) already published a matching snapshot, so this
    build reads it instead of re-listing the tree. A second copy of this field set is exactly how
    a snapshot came to be reported ready while its fields disagreed.
    """
    # P0-1: read the snapshot's stamped identity OUTSIDE `ri.lock` (it does sqlite I/O) so the adopted
    # in-memory owner is judged by the same tombstone rule as the store it mirrors.
    published_identity = _persisted_tombstone_identity(ri.root)
    with ri.lock:
        ri.entries, ri.built_at, ri.truncated, ri.entries_signature, disk_root_identity = disk
        ri.entry_by_path = {entry[0]: entry for entry in ri.entries}
        ri.last_full_build_at = ri.built_at
        ri.too_large = ri.truncated
        ri.persisted = True
        ri.last_persisted_at = time.monotonic()
        ri.cache_bytes = _sqlite_storage_size(ri.root)
        ri.signature = expected_signature
        ri.disk_entry_count = len(ri.entries)
        ri.disk_metadata_ready = True
        # P0-1: re-verify under this same `ri.lock`. A snapshot loaded as un-tombstoned can be invalidated
        # by a newer unindex between the disk read and this adopt; adopting the stale disk stamp as ready
        # would serve deleted rows. When the marker has moved past this stamp, land EVICTED instead.
        if (
            ri.root_fd_identity != disk_root_identity
            or _publication_lands_tombstoned(ri.root, published_identity, ri.built_at)
        ):
            _clear_ready_fields_locked(ri)
        else:
            ri.published_tombstone_identity = published_identity
            ri.published_root_identity = disk_root_identity
            ri.ready = True
        ri.building = False


def _run_bfs_full_build(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None,
    exclude_signature: str,
    generation: int | None,
    operation: str,
    started: float,
    expected_signature: str,
    build_reason: str = "",
    pending_drop_token: str | None = None,
    captured_tombstone_identity: str | None = None,
    captured_root_identity: tuple[int, int] | None = None,
) -> None:
    """Full build for a configured root through the breadth-first, directory-at-a-time frontier.

    Replaces the DFS `_walk_root_with_metrics` full walk. The crawl publishes each directory's rows
    to the per-root SQLite as it lists them, so a follower search sees layer 1 before deep
    descendants finish. After the crawl (or a compatible-snapshot handoff) this seeds the in-memory
    index from the same committed SQLite so the owner's ready read path and the follower's disk read
    path serve identical rows.
    """
    def current() -> bool:
        with ri.lock:
            return (
                (generation is None or ri.active_generation == generation)
                and captured_root_identity is not None
                and ri.root_fd_identity == captured_root_identity
            )

    skip = set(skip_dirs)
    try:
        if not ri.ready:
            # Handoff / restart: adopt a compatible persisted snapshot rather than re-listing the
            # whole tree, matching the DFS build's fast path.
            disk = _load_disk(
                ri.root,
                skip,
                exclude_signature,
                expected_root_identity=captured_root_identity,
            )
            if disk is not None:
                _adopt_disk_snapshot(ri, disk, expected_signature)
                return
        if ri.stop_event.is_set() or not current():
            return
        if _BFS_FULL_BUILD_RUNNER is None:
            raise RuntimeError("breadth-first full-build runner is not registered")
        # A policy/exclusion-signature change starts a clean generation: rows the previous snapshot
        # built under a different coverage policy (now-excluded subtrees the breadth-first crawl
        # will never revisit to delete) must not survive as stale matches. Same-signature refreshes
        # keep their rows for stale-while-rebuild readability.
        expected_disk_signature = _disk_skip_signature(ri.root, skip, exclude_signature)
        if _persisted_skip_signature(ri.root) not in ("", expected_disk_signature):
            _drop_persisted_index(ri.root)
        # Item 1: the crawl publishes and fences on the SAME generation the in-memory owner allocated,
        # so `metadata.active_generation`, the worker's fence, and `ri.active_generation` are one
        # number end to end. A synchronous `build_now` (no owner generation) falls back to the
        # persisted+1 allocation, since there is no concurrent generation to fence against there.
        runner_generation = generation if generation is not None else _next_bfs_generation(ri.root)
        root_descriptor = ri.duplicate_root_fd()
        try:
            _BFS_FULL_BUILD_RUNNER(
                ri.root,
                skip,
                exclude_path=exclude_path,
                exclude_signature=exclude_signature,
                generation=runner_generation,
                operation=operation,
                max_entries=ri.max_files,
                max_total_entries=ri.max_files,
                reason=build_reason,
                stop_event=ri.stop_event,
                root_fd=root_descriptor,
                # Protocol #2/#3: the crawl stamps THIS frozen identity into every published directory's
                # metadata and, when it differs from the persisted snapshot's stamp, establishes a clean
                # generation in the claim transaction before publishing -- so a follower reading a partial
                # mid-crawl sees the correct stamp and no deleted-store rows survive under it.
                tombstone_identity=captured_tombstone_identity,
            )
        finally:
            os.close(root_descriptor)
        if ri.stop_event.is_set() or not current():
            return
        # Read back the rows THIS crawl just committed: `honor_tombstone=False` because a build's own
        # fresh output is authoritative regardless of a marker it is in the act of superseding.
        disk = _load_disk(
            ri.root,
            skip,
            exclude_signature,
            honor_tombstone=False,
            expected_root_identity=captured_root_identity,
        )
        entries = list(disk[0]) if disk is not None else []
        # ``built_at`` is REAL observation time again (protocol #1): the artificial bump past the
        # tombstone deletion time is retired. Supersession is proven by the identity stamp
        # `_complete_publication` writes, not by manufacturing a future build time.
        built_at = disk[1] if disk is not None else time.time()
        truncated = disk[2] if disk is not None else False
        entries_signature = disk[3] if disk is not None else ""
        cache_bytes = _sqlite_storage_size(ri.root)
        # Persist eligibility for a v5 typed partial snapshot (item 6). A breadth-first crawl that hits
        # the total-row cap publishes a TYPED partial store: `truncated=1`, `full_coverage=0`, every
        # published row still searchable by both the owner and a follower, and the durable frontier
        # recording exactly which directories remain. That is durable, valid coverage -- deleting it at
        # the cap (the old `not truncated` clause) blanked a large root's index on every build and left
        # it permanently "Indexing...". So truncation NO LONGER forces a drop. The INDEPENDENT budget
        # rejections remain: persistence disabled, more rows than `persist_max_files`, or more bytes
        # than `persist_max_bytes` still keep NO disk snapshot (proven in separate over-file/over-byte/
        # disabled tests). A partial that fits the budget stays on disk and reloads after a restart.
        persist_ok = (
            ri.persist_enabled
            and len(entries) <= ri.persist_max_files
            and cache_bytes <= ri.persist_max_bytes
        )
        if not persist_ok:
            _drop_persisted_index(ri.root)
            cache_bytes = 0
        with ri.lock:
            if (
                (generation is not None and ri.active_generation != generation)
                or captured_root_identity is None
                or ri.root_fd_identity != captured_root_identity
            ):
                return
            ri.entries = entries
            ri.entry_by_path = {entry[0]: entry for entry in entries}
            ri.entries_signature = entries_signature
            ri.truncated = truncated
            ri.too_large = truncated
            ri.built_at = built_at
            ri.last_full_build_at = built_at
            ri.build_duration_ms = (time.perf_counter() - started) * 1000
            ri.scanned_entries = len(entries)
            ri.ignored_entries = 0
            ri.build_count += 1
            ri.full_build_count += 1
            ri.signature = expected_signature
            ri.disk_entry_count = len(entries)
            ri.persisted = persist_ok
            ri.persist_pending = False
            ri.last_persisted_at = time.monotonic()
            ri.cache_bytes = cache_bytes
            ri.disk_metadata_ready = persist_ok
            # P0-1: freeze the published tombstone identity ATOMICALLY with readiness -- the identity
            # this build stamped on disk. RE-VERIFY under this same `ri.lock`: if a newer unindex changed
            # the marker after this build froze its identity, the stale identity would be tombstoned the
            # instant it went ready, so the build lands EVICTED here rather than republishing deleted rows
            # into a servable owner (the disk stamp still records what it built; the disk reader rejects it
            # by identity). Otherwise readiness and the frozen identity are published together.
            if _publication_lands_tombstoned(ri.root, captured_tombstone_identity, ri.built_at):
                _clear_ready_fields_locked(ri)
            else:
                ri.published_tombstone_identity = captured_tombstone_identity
                ri.published_root_identity = captured_root_identity
                ri.ready = True
            ri.completed_generation = ri.active_generation
        # P0-5: the breadth-first publication completes through the SAME owner as the DFS path -- it
        # stamps the frozen tombstone identity into the snapshot and supersedes a pending durable-drop
        # token instead of leaving a stale finalizer to unlink the freshly published store.
        _complete_publication(
            ri,
            captured_drop_token=pending_drop_token,
            captured_tombstone_identity=captured_tombstone_identity,
            captured_root_identity=captured_root_identity,
        )
        notify_background_owner_done({
            "root": str(ri.root),
            "entries": len(entries),
            "truncated": truncated,
            "too_large": truncated,
            "persisted": persist_ok,
            "cache_bytes": cache_bytes,
            "build_kind": "full",
            "scanned_entries": len(entries),
            "ignored_entries": 0,
            "state": "ready",
            "generation": generation or ri.completed_generation,
            "compute_ms": round(ri.build_duration_ms, 3),
        })
    except (OSError, RuntimeError, ValueError) as exc:
        if current():
            with ri.lock:
                if generation is None or ri.active_generation == generation:
                    ri.last_error = str(exc)
            notify_background_owner_done({"root": str(ri.root), "state": "error", "generation": generation or ri.active_generation, "error": str(exc)})
    finally:
        with ri.lock:
            if generation is None or ri.active_generation == generation:
                ri.building = False


def _run_build(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    generation: int | None = None,
    operation: str = "",
    build_reason: str = "",
    pending_drop_token: str | None = None,
    captured_tombstone_identity: str | None = None,
    captured_root_identity: tuple[int, int] | None = None,
) -> None:
    # C11: take a cross-process lock so a second server process does not duplicate the walk. If another
    # process holds it, leave whatever stale-but-ready disk copy we already loaded in place and bail.
    started = time.perf_counter()
    if captured_root_identity is None:
        with ri.lock:
            captured_root_identity = ri.root_fd_identity
    expected_signature = _skip_signature(skip_dirs, exclude_signature)
    effective_exclude_path = _build_exclude_path(exclude_path)
    with ri.lock:
        dirty_paths = _coalesced_paths(set(ri.dirty_paths)) if ri.ready else []

    def current() -> bool:
        with ri.lock:
            return (
                (generation is None or ri.active_generation == generation)
                and captured_root_identity is not None
                and ri.root_fd_identity == captured_root_identity
            )
    # A configured-root FULL build (no dirty subtrees) runs breadth-first, directory-at-a-time,
    # through the one owner registered by `bfs_index`. It manages its own per-root build lock, so it
    # runs BEFORE this function takes that lock (a second flock on the same file in this process
    # would deadlock). The DFS `_walk_root_with_metrics` full walk is retired for configured roots
    # and reached only as the fallback when no breadth-first runner is registered.
    if not dirty_paths and _BFS_FULL_BUILD_RUNNER is not None:
        _run_bfs_full_build(
            ri,
            skip_dirs,
            exclude_path,
            exclude_signature,
            generation,
            operation,
            started,
            expected_signature,
            build_reason,
            pending_drop_token,
            captured_tombstone_identity,
            captured_root_identity,
        )
        return
    lock_fd = None
    access_fd = None
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(_build_lock_path(ri.root)), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            with ri.lock:
                ri.building = False
            return
        # Another process may have just finished while we waited for the lock — adopt a fresh disk copy
        # instead of re-walking.
        disk = _load_disk(
            ri.root,
            skip_dirs,
            exclude_signature,
            expected_root_identity=captured_root_identity,
        )
        if not dirty_paths and not ri.ready and disk is not None:
            _adopt_disk_snapshot(ri, disk, expected_signature)
            return
        if dirty_paths:
            access_fd = ri.duplicate_root_fd()
            access_root = filesystem_paths.descriptor_path(access_fd)
            entries, truncated, scanned_entries, ignored_entries = _refresh_dirty_subtrees(
                ri,
                dirty_paths,
                skip_dirs,
                effective_exclude_path,
                access_fd,
                operation,
            )
            build_kind = "incremental"
        else:
            access_fd = ri.duplicate_root_fd()
            access_root = filesystem_paths.descriptor_path(access_fd)
            entries, truncated, ignored_entries = _walk_root_with_metrics(
                access_root,
                skip_dirs,
                ri.stop_event,
                effective_exclude_path,
                max_files=ri.max_files,
                relative_root=access_root,
                entry_root=ri.root,
                root_fd=access_fd,
                resolved_root=ri.root,
                operation=operation,
            )
            entries.sort(key=lambda entry: (entry[2].lower(), entry[0]))
            scanned_entries = len(entries)
            build_kind = "full"
        if ri.stop_event.is_set() or not current():
            with ri.lock:
                if generation is None or ri.active_generation == generation:
                    ri.building = False
            return
        with ri.lock:
            if (
                (generation is not None and ri.active_generation != generation)
                or captured_root_identity is None
                or ri.root_fd_identity != captured_root_identity
            ):
                return
            ri.entries = entries
            ri.entry_by_path = {entry[0]: entry for entry in entries}
            ri.truncated = truncated
            ri.too_large = truncated
            ri.built_at = time.time()
            if build_kind == "full":
                ri.last_full_build_at = ri.built_at
            ri.build_duration_ms = (time.perf_counter() - started) * 1000
            ri.scanned_entries = scanned_entries
            ri.ignored_entries = ignored_entries
            ri.build_count += 1
            if build_kind == "full":
                ri.full_build_count += 1
            else:
                ri.incremental_build_count += 1
            ri.dirty_paths.difference_update(dirty_paths)
            ri.signature = expected_signature
            ri.disk_entry_count = len(ri.entries)
            # P0-1: freeze the published tombstone identity ATOMICALLY with readiness (the identity this
            # build stamped on disk). RE-VERIFY under this same `ri.lock`: a build whose frozen identity no
            # longer matches a newer unindex lands EVICTED here rather than republishing deleted rows into a
            # servable owner; otherwise readiness and the frozen identity are published together.
            if _publication_lands_tombstoned(ri.root, captured_tombstone_identity, ri.built_at):
                _clear_ready_fields_locked(ri)
            else:
                ri.published_tombstone_identity = captured_tombstone_identity
                ri.published_root_identity = captured_root_identity
                ri.ready = True
            _record_pending_delta(ri, dirty_paths, build_kind)
        if not current():
            return
        _persist(ri, skip_dirs, exclude_signature)
        with ri.lock:
            if (
                (generation is not None and ri.active_generation != generation)
                or captured_root_identity is None
                or ri.root_fd_identity != captured_root_identity
            ):
                return
            ri.disk_metadata_ready = ri.persisted
            ri.building = False
            ri.completed_generation = ri.active_generation
        # C11 + P0-5/P0-6: a fresh build stamps the tombstone identity it froze at start and supersedes
        # the pending durable-drop token it captured, through the ONE publication-completion owner shared
        # with the breadth-first path -- so a rebuild landing after an unindex keeps its store and is
        # accepted by identity, without erasing a newer unindex requested after this build began.
        _complete_publication(
            ri,
            captured_drop_token=pending_drop_token,
            captured_tombstone_identity=captured_tombstone_identity,
            captured_root_identity=captured_root_identity,
        )
        notify_background_owner_done({
            "root": str(ri.root),
            "entries": len(ri.entries),
            "truncated": ri.truncated,
            "too_large": ri.too_large,
            "persisted": ri.persisted,
            "cache_bytes": ri.cache_bytes,
            "build_kind": build_kind,
            "scanned_entries": ri.scanned_entries,
            "ignored_entries": ri.ignored_entries,
            "state": "ready",
            "generation": generation or ri.completed_generation,
            "compute_ms": round(ri.build_duration_ms, 3),
        })
    except (OSError, RuntimeError, ValueError) as exc:
        if current():
            with ri.lock:
                if generation is None or ri.active_generation == generation:
                    ri.building = False
                    ri.last_error = str(exc)
            notify_background_owner_done({"root": str(ri.root), "state": "error", "generation": generation or ri.active_generation, "error": str(exc)})
    finally:
        with ri.lock:
            # Backstop: an off-list exception (e.g. a sqlite error from _persist, or a
            # MemoryError from a huge walk) must not leave `building` stuck True, which
            # would make schedule_refreshes skip this root forever. Clear only our own
            # generation's flag so a newer build that already took over is untouched.
            if generation is None or ri.active_generation == generation:
                ri.building = False
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        if access_fd is not None:
            os.close(access_fd)


def _build_thread_main(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None,
    exclude_signature: str,
    generation: int | None,
    operation: str,
    build_reason: str,
) -> None:
    """The ONE build-thread entry point, so there is ONE ``finally`` that owns the deferred fd close.

    If this index was retired while the build ran (``clear_memory_indexes``/``unindex`` set
    ``retiring`` and advanced the fence), the exiting worker -- and only the exiting worker -- closes
    its worker-owned root fd here. A build that was never retired leaves the fd untouched."""
    # Capture THIS worker's immutable lease while `building` is still True (no successor can have taken
    # the slot yet), so the finalizer identifies this worker by the frozen lease even after `_run_build`
    # clears `building` and a successor could install its own.
    with ri.lock:
        assignment = ri.assignment
    # P0-6: the token this worker captured when its assignment began. Only its OWN publication may
    # supersede this token; a newer unindex issued after this build started carries a different token.
    captured_drop_token = assignment.pending_drop_token if assignment is not None else None
    # The tombstone identity frozen with this assignment, so publication clears only the marker this
    # build actually superseded (P0 class 1).
    captured_tombstone_identity = assignment.captured_tombstone_identity if assignment is not None else None
    captured_root_identity = assignment.captured_root_identity if assignment is not None else None
    try:
        _run_build(
            ri,
            skip_dirs,
            exclude_path,
            exclude_signature,
            generation,
            operation,
            build_reason,
            captured_drop_token,
            captured_tombstone_identity,
            captured_root_identity,
        )
    finally:
        _finalize_worker_exit(ri, assignment)


def _registry_owner_is(ri: RootIndex) -> bool:
    """Whether ``ri`` is still the live registry owner for its key and is not retiring.

    P0-3: uses the ONE global order ``_REGISTRY_LOCK -> ri.lock`` (nested), the same order every
    lifecycle helper takes, so there is no second, reverse lock story to reason about. Retirement is
    terminal: an object that lost registry identity or entered ``retiring`` must not persist or start
    new work. Callers hold neither lock when they call this."""
    with _REGISTRY_LOCK:
        if _REGISTRY.get(str(ri.root)) is not ri:
            return False
        with ri.lock:
            return not ri.retiring


def _start_build(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    operation: str = "",
    build_reason: str = "",
) -> bool:
    """Assign one build worker to ``ri``. Returns whether a worker was actually installed.

    P0-3: ``retiring`` is a TERMINAL state. A retired object, a demoted background owner, or an object
    that is no longer the registry's owner for its key must never start work -- checked BEFORE the
    expensive generation I/O and AGAIN under the assignment lock before a worker is installed, since a
    clear/unindex can land during the SQLite read. Returning ``False`` (rather than reviving a retired
    store) is what keeps a cleared object from running the runner once and reporting itself ready."""
    key = str(ri.root)
    # Terminal-state, background-ownership, and registry-ownership gate BEFORE the expensive generation
    # read. A demoted background owner must not start work even on an object still in the registry.
    if not background_owner_can_build():
        return False
    if not _registry_owner_is(ri):
        return False
    with ri.lock:
        if ri.building:
            return False
    # ONE generation number, allocated ONCE, used for the in-memory fence (`ri.build_generation` /
    # `ri.active_generation`), the worker argument, AND the SQLite `metadata.active_generation` the
    # breadth-first crawl writes and fences on (item 1). It must be strictly greater than BOTH the
    # in-memory counter AND the persisted active generation, so a fresh RootIndex object (registry
    # cleared, then re-ensured) cannot allocate generation 1 while the persisted store already sits at
    # generation N -- the old split, where the equality fence never matched and the crawl could never
    # publish. Read the persisted value OUTSIDE `ri.lock` (single-writer indexd, so nothing advances
    # it between read and use) to avoid holding the lock across SQLite I/O.
    persisted_generation = _next_bfs_generation(ri.root)
    # P0-4: read the persisted generation PLAN outside the lock. A durable partial with pending frontier
    # rows must be RESUMED on its exact generation (not advanced to `active+1`, which re-lists the root
    # and orphans the pending directories); only a drained/absent frontier allocates a fresh generation.
    with ri.lock:
        expected_root_identity = ri.root_fd_identity
    resume_generation = _resumable_frontier_generation(
        ri.root,
        expected_root_identity=expected_root_identity,
    )
    # P0-6: capture the pending-drop token that exists AS the assignment begins. A newer unindex after
    # this point carries a different token that this build's publication may not supersede.
    pending_drop_token = _current_pending_drop_token(ri.root)
    # P0 class 1: capture the tombstone identity present AS the assignment begins, so a publication only
    # clears the marker it superseded and never one written by a newer unindex issued mid-build.
    captured_tombstone_identity = _current_tombstone_identity(ri.root)
    with ri.lock:
        # Re-check the terminal state AND the building flag AFTER the generation read, under the
        # assignment lock, before installing a worker: a clear/unindex that landed during the I/O wins.
        if ri.retiring or ri.building:
            return False
        ri.building = True
        if resume_generation is not None and resume_generation > ri.build_generation:
            # Resume the exact persisted generation that still owns the pending frontier.
            generation = resume_generation
        else:
            generation = max(ri.build_generation + 1, persisted_generation)
        ri.build_generation = generation
        ri.active_generation = generation
        ri.last_error = ""
        ri.stop_event = threading.Event()
        # ONE completion event per assigned worker, created and cleared BEFORE `ri.thread` becomes
        # visible, so a retirement that observes the thread also observes an un-set completion and
        # waits for the worker's own `_finalize_worker_exit` to set it -- never a stale set from a
        # prior build.
        ri.completion = threading.Event()
        build_context = contextvars.copy_context()
        thread = threading.Thread(
            target=build_context.run,
            args=(_build_thread_main, ri, set(skip_dirs), exclude_path, exclude_signature, generation, operation, build_reason),
            name=f"file-index-{ri.root.name}",
            daemon=True,
        )
        # One immutable per-worker lease (P1-5): the generation, thread, and completion event frozen
        # together, installed before the thread is visible so the finalizer and any retirement bind to
        # THIS worker by lease identity rather than by re-reading mutable fields.
        assignment = _WorkerAssignment(
            generation=generation,
            thread=thread,
            completion=ri.completion,
            pending_drop_token=pending_drop_token,
            captured_tombstone_identity=captured_tombstone_identity,
            captured_root_identity=ri.root_fd_identity,
        )
        ri.assignment = assignment
        ri.thread = thread
    # Final ownership re-check AFTER the generation I/O: a clear/unindex that popped this object from
    # the registry, or a demotion of the background owner, between the generation read and here must
    # not leave a worker running. P0-2: DO NOT pre-clear the slot -- the still-installed assignment is
    # what makes the MATCHING finalizer recognize this worker (`is_my_slot`) and become the sole owner
    # that clears thread/assignment/building, closes any retiring fd, removes `_RETIRING`, and completes
    # the frozen lease. Pre-clearing `assignment` here made the finalizer read `is_my_slot` False and
    # skip the retiring fd close (a leaked fd + a stuck `(11, True)` retiree).
    if not (background_owner_can_build() and _registry_owner_is(ri)):
        _finalize_worker_exit(ri, assignment)
        return False
    # A browser that already knows this root is building must not discover the
    # transition through its 1.5-second repair poll. The completion callback
    # publishes the matching ready state after the new index is readable.
    notify_background_owner_done({"root": str(ri.root), "state": "building", "generation": generation})
    # Claim custody before the first persist, so a reader of an older snapshot
    # sees a live producer as soon as this build starts rather than after it ends.
    touch_producer_heartbeat(ri.root, force=True)

    def rollback() -> None:
        # P0-2: a start that fails AFTER the ownership re-check (Thread.start raised) hands the
        # still-installed assignment to the MATCHING finalizer, the sole owner that clears the slot,
        # closes any retiring fd, removes `_RETIRING`, and completes the frozen lease. No pre-clear.
        _finalize_worker_exit(ri, assignment)

    start_thread_with_rollback(thread, rollback)
    return True


def _install_candidate_root_fd(ri: RootIndex, root_fd: int | None) -> bool:
    """Install a fresh pinned root fd on ``ri`` ONLY while it is still the live, non-retiring registry
    owner -- the ONE candidate-FD install owner (P0-1).

    The candidate descriptor is opened (or duplicated from the caller's) OUTSIDE every lifecycle lock,
    since fd I/O must not run under ``_REGISTRY_LOCK``. It is then swapped in under the ONE global
    order ``_REGISTRY_LOCK -> ri.lock`` only after re-verifying, atomically, that ``ri`` is STILL the
    registry owner for its key and is not retiring. The old code checked ownership, then a
    ``clear_memory_indexes()`` could retire/finalize ``ri``, then it installed the fd on an object
    absent from BOTH ``_REGISTRY`` and ``_RETIRING`` -- an orphaned descriptor no finalizer would ever
    close. When ownership is lost in the race the candidate is CLOSED instead of leaked. Returns
    whether the fd was installed. A previously pinned fd is closed only after the swap succeeds."""
    if root_fd is None:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        with filesystem_paths.safe_path(
            str(ri.root),
            flags=directory_flags,
            operation="index_root",
        ) as root_handle:
            candidate = os.dup(root_handle.descriptor)
    else:
        candidate = os.dup(root_fd)
    candidate_identity = parse_root_identity(root_identity(os.fstat(candidate)))
    previous: int | None = None
    installed = False
    with _REGISTRY_LOCK:
        if _REGISTRY.get(str(ri.root)) is ri:
            with ri.lock:
                if not ri.retiring:
                    previous = ri.root_fd
                    ri._replace_root_fd_locked(candidate, candidate_identity)
                    installed = True
    if not installed:
        os.close(candidate)
    elif previous is not None:
        os.close(previous)
    return installed


def ensure_index(
    root: Path,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    *,
    max_files: int | None = None,
    refresh_seconds: float = INDEX_TTL_SECONDS,
    persist_enabled: bool = True,
    persist_max_files: int = MAX_PERSISTED_INDEX_FILES,
    persist_max_bytes: int = MAX_PERSISTED_INDEX_BYTES,
    root_fd: int | None = None,
    operation: str = "",
) -> RootIndex:
    """Return the RootIndex for root, seeding from disk and kicking off a
    background (re)build when missing or stale. May return a not-yet-ready index."""
    key = str(root)
    expected_signature = _skip_signature(skip_dirs, exclude_signature)
    requested_root_identity = (
        parse_root_identity(root_identity(os.fstat(root_fd)))
        if root_fd is not None
        else _current_root_identity(root)
    )
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
            ri.max_files = max(1, int(MAX_INDEX_FILES if max_files is None else max_files))
            ri.refresh_seconds = max(0.0, float(refresh_seconds))
            ri.persist_enabled = bool(persist_enabled)
            ri.persist_max_files = max(1, int(persist_max_files))
            ri.persist_max_bytes = max(1, int(persist_max_bytes))
            ri.skip_dirs = set(skip_dirs)
            ri.exclude_path = exclude_path
            ri.exclude_signature = exclude_signature
            if background_owner_can_build() and ri.persist_enabled:
                disk = _load_disk(
                    root,
                    skip_dirs,
                    exclude_signature,
                    expected_root_identity=requested_root_identity,
                )
                if disk is not None:
                    (
                        ri.entries,
                        ri.built_at,
                        ri.truncated,
                        ri.entries_signature,
                        ri.published_root_identity,
                    ) = disk
                    ri.entry_by_path = {entry[0]: entry for entry in ri.entries}
                    ri.last_full_build_at = ri.built_at
                    ri.too_large = ri.truncated
                    ri.persisted = True
                    ri.last_persisted_at = time.monotonic()
                    ri.cache_bytes = _sqlite_storage_size(root)
                    ri.disk_entry_count = len(ri.entries)
                    ri.disk_metadata_ready = True
                    ri.signature = expected_signature
                    # P0-1: seed the in-memory published identity from the persisted snapshot's stamp so
                    # a later marker change evicts this owner by the same rule it would be rejected on disk.
                    ri.published_tombstone_identity = _persisted_tombstone_identity(root)
                    ri.ready = True
            elif not background_owner_can_build() and ri.persist_enabled:
                metadata = _load_disk_metadata(
                    root,
                    skip_dirs,
                    exclude_signature,
                    expected_root_identity=requested_root_identity,
                )
                if metadata is not None:
                    try:
                        ri.built_at = float(metadata.get("built_at") or 0.0)
                        ri.disk_entry_count = int(metadata.get("entry_count") or 0)
                    except (TypeError, ValueError):
                        ri.built_at = 0.0
                        ri.disk_entry_count = 0
                    ri.truncated = bool(metadata.get("truncated"))
                    ri.too_large = ri.truncated
                    ri.disk_metadata_ready = True
                    ri.signature = expected_signature
        elif not background_owner_can_build() and not ri.ready and ri.persist_enabled:
            metadata = _load_disk_metadata(
                root,
                skip_dirs,
                exclude_signature,
                expected_root_identity=requested_root_identity,
            )
            if metadata is not None:
                try:
                    ri.built_at = float(metadata.get("built_at") or 0.0)
                    ri.disk_entry_count = int(metadata.get("entry_count") or 0)
                except (TypeError, ValueError):
                    ri.built_at = 0.0
                    ri.disk_entry_count = 0
                ri.truncated = bool(metadata.get("truncated"))
                ri.too_large = ri.truncated
                ri.disk_metadata_ready = True
                ri.signature = expected_signature
        else:
            with ri.lock:
                ri.max_files = max(1, int(MAX_INDEX_FILES if max_files is None else max_files))
                ri.refresh_seconds = max(0.0, float(refresh_seconds))
                ri.persist_enabled = bool(persist_enabled)
                ri.persist_max_files = max(1, int(persist_max_files))
                ri.persist_max_bytes = max(1, int(persist_max_bytes))
                ri.skip_dirs = set(skip_dirs)
                ri.exclude_path = exclude_path
                ri.exclude_signature = exclude_signature
    # P0-3: a concurrent clear/unindex may have retired this object (removed it from the registry and
    # set `retiring`) between the registry insertion above and here. Reopening the pinned fd on it now
    # would leak a descriptor on an object absent from BOTH `_REGISTRY` and `_RETIRING`, and starting a
    # build would revive a retired store. If we lost registry identity, return the object as-is without
    # installing an fd or starting work; a later `ensure_index` creates a fresh owner.
    if not _registry_owner_is(ri):
        return ri
    # P0-1: open the candidate fd and install it through the ONE owner that re-verifies ownership
    # ATOMICALLY under `_REGISTRY_LOCK -> ri.lock`. A clear/unindex that retired `ri` between the gate
    # above and the swap makes the install refuse and CLOSE the candidate rather than orphan it on an
    # object absent from both `_REGISTRY` and `_RETIRING`.
    if not _install_candidate_root_fd(ri, root_fd):
        return ri
    with ri.lock:
        ri.max_files = max(1, int(MAX_INDEX_FILES if max_files is None else max_files))
        ri.refresh_seconds = max(0.0, float(refresh_seconds))
        ri.persist_enabled = bool(persist_enabled)
        ri.persist_max_files = max(1, int(persist_max_files))
        ri.persist_max_bytes = max(1, int(persist_max_bytes))
        ri.skip_dirs = set(skip_dirs)
        ri.exclude_path = exclude_path
        ri.exclude_signature = exclude_signature
    if background_owner_can_build() and not ri.persist_enabled:
        _drop_persisted_index(root)
        with ri.lock:
            ri.persisted = False
            ri.persist_pending = False
            ri.disk_metadata_ready = False
            ri.cache_bytes = 0
    with ri.lock:
        if ri.ready and ri.signature != expected_signature:
            _clear_ready_fields_locked(ri)
            ri.signature = ""
    # P0-1: if another process unindexed this root after our copy was built, drop the stale in-memory
    # index so we stop serving deleted-file results. This routes through the SAME `_snapshot_is_tombstoned`
    # verdict as disk (`_evict_tombstoned_root_index`) -- comparing the owner's FROZEN published identity
    # against the current marker -- so a build that published after the marker's time but stamped the OLD
    # identity is evicted here even though its `built_at` is newer than the deletion time. Eviction clears
    # readiness, so the scheduling below starts a fresh clean generation that freezes the CURRENT identity.
    _evict_tombstoned_root_index(ri)
    # P0-4: searchable state and crawl completion are SEPARATE facts. Start a build when either the
    # snapshot is not ready OR a compatible partial IS ready but still has a durable pending frontier
    # to resume. Without the second clause a restart that loads a partial (`ready=True`) would schedule
    # nothing -- `schedule_refreshes` sees a fresh TTL and no dirty subtree -- so the crawl stalls at
    # "Indexing..." until the 30-minute TTL elapses. `_start_build` resumes the exact persisted
    # generation of that frontier rather than re-listing the root.
    with ri.lock:
        current_root_identity = ri.root_fd_identity
    if background_owner_can_build() and (
        not ri.ready
        or _resumable_frontier_generation(
            root,
            expected_root_identity=current_root_identity,
        )
        is not None
    ):
        _start_build(
            ri,
            skip_dirs,
            exclude_path=exclude_path,
            exclude_signature=exclude_signature,
            operation=operation,
        )
    return ri


def build_now(
    root: Path,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    *,
    max_files: int | None = None,
    refresh_seconds: float = INDEX_TTL_SECONDS,
    persist_enabled: bool = True,
    persist_max_files: int = MAX_PERSISTED_INDEX_FILES,
    persist_max_bytes: int = MAX_PERSISTED_INDEX_BYTES,
    root_fd: int | None = None,
    operation: str = "",
) -> RootIndex:
    """Synchronously build (or rebuild) the index for root. Used at warm-up and in tests."""
    key = str(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
    # P0-1: install the pinned fd through the ONE candidate-FD owner, which re-verifies registry
    # ownership atomically and closes the candidate rather than orphaning it if `ri` was retired
    # concurrently. A refused install means this object is no longer buildable, so return it as-is.
    if not _install_candidate_root_fd(ri, root_fd):
        return ri
    with ri.lock:
        ri.max_files = max(1, int(MAX_INDEX_FILES if max_files is None else max_files))
        ri.refresh_seconds = max(0.0, float(refresh_seconds))
        ri.persist_enabled = bool(persist_enabled)
        ri.persist_max_files = max(1, int(persist_max_files))
        ri.persist_max_bytes = max(1, int(persist_max_bytes))
        ri.skip_dirs = set(skip_dirs)
        ri.exclude_path = exclude_path
        ri.exclude_signature = exclude_signature
    ri.stop_event = threading.Event()
    # P0-6: capture the pending-drop token as this synchronous build begins, so its publication
    # supersedes only that token and never a newer unindex requested after it started.
    pending_drop_token = _current_pending_drop_token(root)
    # P0 class 1: freeze the tombstone identity at synchronous-build start, so its publication clears
    # only the marker it superseded (a rebuild landing after this exact unindex), never a newer one.
    captured_tombstone_identity = _current_tombstone_identity(root)
    with ri.lock:
        captured_root_identity = ri.root_fd_identity
    _run_build(
        ri,
        set(skip_dirs),
        exclude_path=exclude_path,
        exclude_signature=exclude_signature,
        operation=operation,
        pending_drop_token=pending_drop_token,
        captured_tombstone_identity=captured_tombstone_identity,
        captured_root_identity=captured_root_identity,
    )
    return ri


def _servable_snapshot(ri: RootIndex) -> tuple[list[tuple[str, str, str, int, float]], bool]:
    """The ONE serving accessor for the in-memory index rows (P0-1 TOCTOU close).

    ``ensure_index`` applies the tombstone verdict and evicts, but the live serve reads the rows LATER
    (`_search_files_from_safe_root` checks ``index.ready`` and then calls `search_index`). A cross-process
    unindex landing in that gap must not serve deleted rows, so this re-applies the SAME
    `_evict_tombstoned_root_index` verdict at the MOMENT of the read and returns the row snapshot under
    ``ri.lock``. Both `search_index` and `recent_entries` (the only cached-row readers) route through
    here; an evicted (tombstoned) index yields an empty snapshot, so no stale row is ever served.

    P0-1 atomicity: the tombstone verdict, the eviction, and the row read all happen under ONE continuous
    ``ri.lock`` acquisition (never released and re-acquired). A stale build paused before publication and
    then republishing its OLD identity (`_run_bfs_full_build`/`_run_build`, which also take ``ri.lock``)
    cannot interleave between the verdict and the read, so deleted rows can never be served."""
    with ri.lock:
        if ri.ready and (not _root_index_generation_matches(ri) or _root_index_is_tombstoned(ri)):
            _clear_ready_fields_locked(ri)
        return list(ri.entries), ri.truncated


def search_index(ri: RootIndex, match: Callable[[str, str, str], Any], max_results: int) -> tuple[list[dict[str, Any]], bool]:
    """Filter+rank the in-memory index with `match(path, name, rel) -> entry|None`.
    Returns (sorted results capped at max_results, truncated)."""
    snapshot, index_truncated = _servable_snapshot(ri)
    results: list[dict[str, Any]] = []
    for path_str, name, rel, size, mtime in snapshot:
        entry = match(path_str, name, rel)
        if entry is None:
            continue
        entry["size"] = size
        entry["mtime"] = mtime
        results.append(entry)
    truncated = index_truncated
    results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
    if len(results) > max_results:
        truncated = True
        results = results[:max_results]
    return results, truncated


def recent_entries(ri: RootIndex, max_results: int, make_entry: Callable[[str, str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    # C11: a capped most-recently-modified slice of a READY index, for the empty quick-open query — so an
    # empty query is served instantly from the index instead of triggering a cold full-tree walk.
    # P0-1: route through the ONE serving accessor so a cross-process unindex landing between readiness
    # check and read evicts the tombstoned rows rather than serving them.
    snapshot, index_truncated = _servable_snapshot(ri)
    ordered = sorted(snapshot, key=lambda item: item[4], reverse=True)
    truncated = index_truncated or len(ordered) > max_results
    results = []
    for path_str, name, rel, size, mtime in ordered[:max_results]:
        entry = make_entry(path_str, name, rel)
        entry["size"] = size
        entry["mtime"] = mtime
        results.append(entry)
    return results, truncated


def unindex(root: Path) -> None:
    """Cancel any build and drop the index for root (in memory + on disk). Leaves a tombstone so other
    server processes sharing STATE_DIR drop their stale in-memory copy on next access (C11)."""
    key = str(root)
    # P0 class 1: write the tombstone (a FRESH opaque identity + deletion time) FIRST -- before registry
    # removal, worker wait, or drop work -- so the delete authority is durable the instant the unindex is
    # requested. A build already running froze the PRIOR identity (or none), so its publication cannot
    # clear THIS marker; a rebuild started after this point freezes this identity and may supersede it.
    # The marker is never re-written at the end (a later write could stamp a time newer than a rebuild
    # that already superseded the delete -- the stale-marker-after-rebuild bug).
    _write_tombstone(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.pop(key, None)
    # P0-4: record the durable-drop intent at the ROOT level BEFORE any finalizer can run, so the last
    # owner to exit executes exactly one drop. A per-worker flag was insufficient: after a prior
    # `clear` moved a blocked worker into `_RETIRING`, `unindex` sees no active registry owner and used
    # to unlink the store immediately -- deleting it underneath a late retiree that could still hold or
    # publish to it. The root-level owner keeps the store until no active AND no retiring owner remains.
    _request_pending_drop(root)
    if ri is not None:
        # Same retirement lifecycle as `clear_memory_indexes`, plus the root-level DEFERRED durable
        # delete. The store must NOT be unlinked while the worker can still publish to or hold it, so
        # the EXITING worker's `_finalize_worker_exit` executes the pending drop AFTER it has stopped --
        # closing the fd and unlinking DB/WAL/SHM/manifest/heartbeat exactly once, and only when it is
        # the last owner. The pending drop is a ROOT-LEVEL fact in `_PENDING_DROPS` (there is no
        # per-worker `deferred_drop` flag; diagnostics derive it from the root token under the lock).
        thread = _signal_retirement(ri)
        with ri.lock:
            ri.building = False
        current = threading.current_thread()
        if thread is None:
            # No worker: close now; the finalizer executes the pending drop when it is the last owner.
            _finalize_worker_exit(ri)
        elif thread is current:
            # Self-unindex from inside the worker: its own `finally` runs the deferred drop; never here.
            pass
        elif _await_worker_finalized(ri, thread, time.monotonic() + CLEAR_WORKER_JOIN_TIMEOUT_SECONDS):
            # Finished within the deadline (possibly before we set `retiring`): finalize now so the
            # pending drop runs; idempotent if the worker's own `finally` already ran it.
            _finalize_worker_exit(ri)
        # else: still running or assigned-but-not-started -- the durable delete is deferred to the
        # worker `finally` / start rollback; the DB stays present and valid until then.
    else:
        # No ACTIVE registry owner, but a RETIRING owner for this root (e.g. a prior clear's late
        # retiree) may still hold the store. Execute the pending drop only if none remains; otherwise
        # the last retiree's finalizer will.
        _maybe_execute_pending_drop(root)


def _iter_candidate_index_roots() -> Iterator[Path]:
    """Yield each active or persisted index root, validated, registry first.

    The ONE shared source for both `any_index_roots_exist` (stops at the first) and
    `indexed_ancestor_roots` (filters by ancestry), so a boolean "any root exists"
    can never disagree with "which roots contain this path": a corrupt, non-dict, or
    relative-root manifest yields nothing from either. Lazy so the boolean returns
    after the first valid root without parsing the remaining manifests, and the
    common registry-non-empty case never touches the manifest glob at all.
    """
    with _REGISTRY_LOCK:
        registry_owners = [(Path(root).expanduser().resolve(strict=False), ri) for root, ri in _REGISTRY.items()]
    for resolved, ri in registry_owners:
        # P0-1: do not advertise a registry root whose in-memory snapshot an explicit unindex has
        # invalidated -- a tombstoned owner is neither "any root exists" nor an indexed ancestor. The
        # verdict routes through the SAME `_snapshot_is_tombstoned` rule as disk; the tomb read is off the
        # registry lock. This skips BOTH a tombstoned READY owner and a tombstoned owner that landed EVICTED
        # at publication (ready=False, not building). A fresh clean generation still BUILDING (which froze
        # the current identity but has not stamped it yet) is not yet tombstoned-by-stamp mid-build, so it
        # is still yielded via `ri.building`.
        current_identity = _current_root_identity(resolved)
        with ri.lock:
            installed_identity = ri.root_fd_identity
            rows_match = not ri.ready or _root_index_generation_matches(ri)
        if current_identity is None or current_identity != installed_identity or not rows_match:
            continue
        if _root_index_is_tombstoned(ri) and not ri.building:
            continue
        yield resolved
    try:
        manifests = tuple(INDEX_DIR.glob("*.manifest.json"))
    except OSError:
        return
    for manifest_path in manifests:
        payload = read_json_file(manifest_path, None)
        root_text = payload.get("root") if isinstance(payload, dict) else None
        if not isinstance(root_text, str) or not root_text.startswith("/"):
            continue
        candidate = Path(root_text).resolve(strict=False)
        current_identity = _current_root_identity(candidate)
        if (
            not isinstance(payload, dict)
            or current_identity is None
            or not _root_identity_matches(payload, current_identity)
        ):
            continue
        # An explicit unindex invalidates the snapshot: a tombstoned root is neither "any root exists"
        # nor an indexed ancestor, so a deleted index can never keep serving or counting as coverage.
        # P1-3: the manifest is a derived cache -- reconcile the verdict against the AUTHORITATIVE sqlite
        # so a stale manifest cannot hide a root the sqlite search reader still serves (readers agree).
        if isinstance(payload, dict) and _authoritative_snapshot_is_tombstoned(candidate, payload):
            continue
        yield candidate


def indexed_ancestor_roots(path: Path) -> list[Path]:
    """Return every active or persisted index root that contains ``path``."""
    target = path.expanduser().resolve(strict=False)
    roots = []
    for root in set(_iter_candidate_index_roots()):
        try:
            target.relative_to(root)
        except ValueError:
            continue
        roots.append(root)
    return sorted(roots, key=lambda root: (len(root.parts), str(root)))


def any_index_roots_exist() -> bool:
    """Whether any active or persisted index root exists at all (item 6 guard).

    With zero configured/active roots a change-evidence batch can produce no dirty
    subtree, so the hot-path owner short-circuits BEFORE per-path `safe_parent`
    normalization rather than resolving every changed path only to drop it. Shares
    `_iter_candidate_index_roots` with `indexed_ancestor_roots` -- INCLUDING manifest
    parse/validation -- so a corrupt, non-dict, or relative-root manifest is never
    counted as a root and the two can never disagree about existence. Lazy: returns
    at the first valid root (the common registry-non-empty case never globs).
    """
    return next(_iter_candidate_index_roots(), None) is not None


def invalidate_path(path: Path) -> list[Path]:
    """Drop indexed ancestors through the existing cross-process unindex path."""
    roots = indexed_ancestor_roots(path)
    for root in roots:
        unindex(root)
    return roots

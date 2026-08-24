"""Breadth-first, directory-at-a-time Quick Open index builder.

This module implements the traversal mechanism required by
``docs/specs/FS_INTERACTIVITY.md``: a bounded, typed, generation-fenced
breadth-first frontier and a scanner that lists exactly one directory per work
item and never recurses. It is NOT a second daemon or a second SQLite writer.
The one existing owner (``search_indexer.py::PersistentSearchIndexer`` running as
``indexd``) drives it, and every write goes through the same
``file_index._connect_sqlite_index`` / ``file_index._ensure_sqlite_schema``
helpers and per-root build lock that the flat DFS build already uses.

The engine is deliberately kept behind the existing demand path. Wiring a
scheduler lease that enqueues ``startup-depth-1`` work for configured roots is a
separate queue item; this module supplies and proves the traversal, schema, and
recovery behavior that item will drive.

Why a separate frontier at all: the shipped ``file_index._walk_root_with_metrics``
opens every child directory before releasing its parent (a stack walk) and
publishes the whole tree in one atomic swap, so a deep or very wide subtree
delays shallow results. Here the filesystem OPEN ORDER is itself breadth-first:
layer 1 is one listing of the root, and each discovered child directory is a
separate work item ordered strictly by depth, with FIFO ties and round-robin
fairness across roots.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
import fcntl
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any
from typing import Callable

from . import file_index
from ..filesystem import paths as filesystem_paths


# Precedence classes from the target spec, lowest number = highest priority.
PRIORITY_STARTUP_DEPTH_1 = 0
PRIORITY_HOT_CHANGE = 1
PRIORITY_USER_VISIBLE_DEMAND = 2
PRIORITY_BREADTH_EXPANSION = 3
PRIORITY_FULL_SAFETY_REFRESH = 4

REASON_STARTUP = "startup-depth-1"
REASON_HOT = "hot-change"
REASON_USER_VISIBLE = "user-visible-demand"
REASON_BREADTH = "breadth-expansion"
REASON_SAFETY = "full-safety-refresh"

_REASON_PRIORITY = {
    REASON_STARTUP: PRIORITY_STARTUP_DEPTH_1,
    REASON_HOT: PRIORITY_HOT_CHANGE,
    REASON_USER_VISIBLE: PRIORITY_USER_VISIBLE_DEMAND,
    REASON_BREADTH: PRIORITY_BREADTH_EXPANSION,
    REASON_SAFETY: PRIORITY_FULL_SAFETY_REFRESH,
}

# Bounds. These cap the frontier and per-directory work so a pathological tree
# cannot exhaust memory or storage; truncation is reported explicitly rather than
# presented as full coverage.
DEFAULT_MAX_FRONTIER_ITEMS = 200_000
DEFAULT_MAX_DIR_ENTRIES = 50_000
DEFAULT_MAX_RETRIES = 3


def priority_for_reason(reason: str) -> int:
    return _REASON_PRIORITY.get(str(reason), PRIORITY_BREADTH_EXPANSION)


@dataclass(frozen=True)
class FrontierItem:
    """One bounded typed frontier record.

    Queue identity is ``(root, directory, generation)``. ``directory`` is an
    absolute canonical path that must stay beneath ``root``. ``depth`` is 1 for a
    configured root's own listing and ``parent_depth + 1`` for each discovered
    child.
    """

    root: str
    directory: str
    depth: int
    generation: int
    reason: str
    priority: int
    enqueued_at: float
    retries: int = 0
    seq: int = 0

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.root, self.directory, self.generation)


@dataclass
class ScanResult:
    """The outcome of listing exactly one directory, without descending."""

    files: list[file_index.IndexEntry] = field(default_factory=list)
    child_directories: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    missing: bool = False


class BfsFrontier:
    """A bounded, generation-fenced breadth-first queue with multi-root fairness.

    Ordering when popping a work item:
      1. lower ``priority`` first (startup before breadth before safety),
      2. then lower ``depth`` first (shallow before deep, globally across roots so
         one wide root cannot delay another root's shallow layers),
      3. then round-robin across roots (least-recently-served root first),
      4. then FIFO by enqueue sequence.
    """

    def __init__(self, *, max_items: int = DEFAULT_MAX_FRONTIER_ITEMS):
        self.max_items = max(1, int(max_items))
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str, int], FrontierItem] = {}
        self._seq = 0
        self._root_service: dict[str, int] = {}
        self.truncated = False

    def enqueue(self, item: FrontierItem) -> bool:
        """Insert one item, coalescing on identity. Returns False when rejected.

        Repeated demand for the same ``(root, directory, generation)`` coalesces
        into the higher-priority (lower number) record instead of creating a
        parallel crawl. A full frontier rejects new work and records truncation.
        """
        with self._lock:
            existing = self._items.get(item.identity)
            if existing is not None:
                if item.priority < existing.priority:
                    self._items[item.identity] = replace(existing, priority=item.priority, reason=item.reason)
                return True
            if len(self._items) >= self.max_items:
                self.truncated = True
                return False
            self._seq += 1
            self._items[item.identity] = replace(item, seq=self._seq)
            return True

    def _select_locked(self) -> FrontierItem | None:
        best: FrontierItem | None = None
        best_key: tuple[int, int, int, int] | None = None
        for candidate in self._items.values():
            served = self._root_service.get(candidate.root, 0)
            key = (candidate.priority, candidate.depth, served, candidate.seq)
            if best_key is None or key < best_key:
                best_key = key
                best = candidate
        return best

    def pop(self) -> FrontierItem | None:
        with self._lock:
            selected = self._select_locked()
            if selected is None:
                return None
            del self._items[selected.identity]
            self._root_service[selected.root] = self._root_service.get(selected.root, 0) + 1
            return selected

    def requeue(self, item: FrontierItem) -> FrontierItem | None:
        """Return a failed item to the queue with an incremented retry count.

        Returns the requeued item (with its incremented ``retries`` and fresh ``seq``) so the caller
        can persist the SAME record durably; a restart then resumes at the same retry rather than
        resetting to zero and retrying forever. Returns ``None`` once the retry bound is exceeded.
        """
        with self._lock:
            if item.retries + 1 > DEFAULT_MAX_RETRIES:
                return None
            self._seq += 1
            requeued = replace(item, retries=item.retries + 1, seq=self._seq)
            self._items[requeued.identity] = requeued
            return requeued

    def cancel_generation(self, root: str, generation: int) -> int:
        """Drop every pending item for one abandoned generation."""
        with self._lock:
            doomed = [key for key in self._items if key[0] == root and key[2] == generation]
            for key in doomed:
                del self._items[key]
            return len(doomed)

    def promote_root(
        self,
        root: str,
        generation: int,
        *,
        to_priority: int = PRIORITY_USER_VISIBLE_DEMAND,
        to_reason: str = REASON_USER_VISIBLE,
    ) -> int:
        """Raise the priority of a root's pending items so a user-visible demand runs sooner.

        Item 5 of ``DOIT.fs-interactivity``: a Quick Open query for a not-yet-covered scope bumps
        that root's pending frontier to ``user-visible-demand`` WITHOUT launching a second crawl. It
        only ever RAISES priority (a lower number), never lowers a ``startup-depth-1`` or
        ``hot-change`` item to demand, so a promotion cannot demote more urgent work. Returns the
        number of items whose priority actually changed. The in-memory queue and the durable
        ``frontier`` table are promoted through their own owners (this and
        ``file_index.promote_frontier``), not two divergent copies of the rule.
        """
        promoted = 0
        with self._lock:
            for identity, item in list(self._items.items()):
                if item.root != root or item.generation != generation:
                    continue
                if item.priority <= to_priority:
                    continue
                self._items[identity] = replace(item, priority=to_priority, reason=to_reason)
                promoted += 1
        return promoted

    def load(self, items: list[FrontierItem]) -> None:
        """Seed the in-memory queue from a durable checkpoint (crash resume)."""
        with self._lock:
            for item in items:
                self._seq += 1
                self._items[item.identity] = replace(item, seq=self._seq)

    def pending(self) -> list[FrontierItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda entry: (entry.priority, entry.depth, entry.seq))

    def shallowest_pending_depth(self, generation: int) -> int | None:
        with self._lock:
            depths = [item.depth for item in self._items.values() if item.generation == generation]
        return min(depths) if depths else None

    def size(self) -> int:
        with self._lock:
            return len(self._items)


def _direct_child_paths(conn: sqlite3.Connection, directory: str) -> list[str]:
    """Return the paths already indexed as direct children of ``directory``.

    A lexical primary-key range on ``path`` selects everything under the prefix
    without a table scan; the Python filter then keeps only direct children so a
    grandchild in a deeper layer is never deleted by its ancestor's scan.
    """
    prefix = directory.rstrip("/") + "/"
    upper = directory.rstrip("/") + "0"  # '0' is the byte after '/'
    rows = conn.execute(
        "SELECT path FROM entries WHERE path >= ? AND path < ?",
        (prefix, upper),
    ).fetchall()
    children: list[str] = []
    for (path_text,) in rows:
        remainder = str(path_text)[len(prefix):]
        if remainder and "/" not in remainder:
            children.append(str(path_text))
    return children


def scan_directory_once(
    root_fd: int,
    root: Path,
    directory: Path,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None,
    *,
    max_entries: int = DEFAULT_MAX_DIR_ENTRIES,
    operation: str = "",
) -> ScanResult:
    """List exactly one directory and return its direct files and child dirs.

    This is the breadth-first primitive: it opens ``directory`` relative to the
    pinned root descriptor without following symlinks, performs a single
    ``scandir``, and NEVER descends. Symlinked directories and files are dropped
    (the same no-follow / dev+ino identity check the recursive walk applies), so a
    discovered link can neither escape the configured root nor create a cycle.
    """
    result = ScanResult()
    relative = Path(".") if directory == root else directory.relative_to(root)
    directory_context = filesystem_paths.safe_descendant(
        root_fd,
        root,
        root,
        relative,
        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        operation=operation,
    )
    try:
        directory_handle = directory_context.__enter__()
    except FileNotFoundError:
        result.missing = True
        return result
    except (OSError, filesystem_paths.FilesystemError) as exc:
        result.error = f"open:{exc.__class__.__name__}"
        return result
    directory_fd = directory_handle.descriptor
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        with os.scandir(directory_fd) as entries:
            for entry in sorted(entries, key=lambda item: item.name.lower()):
                if len(result.files) + len(result.child_directories) >= max_entries:
                    result.truncated = True
                    break
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                child_relative = relative / entry.name
                result_path = root / relative / entry.name if directory != root else root / entry.name
                if stat.S_ISDIR(entry_stat.st_mode):
                    if entry.name in skip_dirs:
                        continue
                    if exclude_path is not None and exclude_path(result_path):
                        continue
                    try:
                        with filesystem_paths.safe_child(
                            directory_fd,
                            result_path,
                            result_path,
                            flags=directory_flags,
                            operation=operation,
                        ) as child:
                            child_stat = child.stat_result
                    except (OSError, filesystem_paths.FilesystemError):
                        continue
                    if (child_stat.st_dev, child_stat.st_ino) != (entry_stat.st_dev, entry_stat.st_ino):
                        continue
                    result.child_directories.append(entry.name)
                elif stat.S_ISREG(entry_stat.st_mode):
                    if exclude_path is not None and exclude_path(result_path):
                        continue
                    try:
                        with filesystem_paths.safe_child(
                            directory_fd,
                            result_path,
                            result_path,
                            flags=filesystem_paths.metadata_descriptor_flags(),
                            operation=operation,
                        ) as child:
                            child_stat = child.stat_result
                    except (OSError, filesystem_paths.FilesystemError):
                        continue
                    if not stat.S_ISREG(child_stat.st_mode) or (
                        child_stat.st_dev,
                        child_stat.st_ino,
                    ) != (entry_stat.st_dev, entry_stat.st_ino):
                        continue
                    rel_posix = (relative / entry.name).as_posix()
                    if rel_posix.startswith("./"):
                        rel_posix = rel_posix[2:]
                    result.files.append(
                        (str(result_path), entry.name, rel_posix, int(child_stat.st_size), int(child_stat.st_mtime))
                    )
    except OSError as exc:
        result.error = f"scandir:{exc.__class__.__name__}"
    finally:
        directory_context.__exit__(None, None, None)
    return result


class ProgressiveBuild:
    """Drives one root's generation-fenced breadth-first, directory-at-a-time build.

    One instance owns one ``(root, generation)`` crawl. It reuses the shipped
    per-root SQLite connection and build lock; nothing here is a second writer.
    The public surface is deliberately step-driven so tests can observe the actual
    directory OPEN ORDER rather than only final row equality.
    """

    def __init__(
        self,
        root: Path,
        skip_dirs: set[str],
        *,
        exclude_path: Callable[[Path], bool] | None = None,
        exclude_signature: str = "",
        generation: int,
        frontier: BfsFrontier | None = None,
        max_entries: int = DEFAULT_MAX_DIR_ENTRIES,
        max_total_entries: int | None = None,
        operation: str = "",
        tombstone_identity: str | None = None,
        root_fd: int | None = None,
    ):
        self.root = root.expanduser()
        self.skip_dirs = set(skip_dirs)
        self.exclude_path = file_index._build_exclude_path(exclude_path)
        self.exclude_signature = exclude_signature
        self.generation = int(generation)
        # Protocol #2/#3: the tombstone identity this build FROZE at its start. Every published
        # directory's metadata is stamped with it, and a claim whose persisted snapshot carries a
        # DIFFERENT stamp establishes a clean generation (drops the deleted store's rows) before
        # publishing. ``""`` means the build superseded no identity marker.
        self.tombstone_identity = str(tombstone_identity or "")
        self.frontier = frontier if frontier is not None else BfsFrontier()
        self.max_entries = int(max_entries)
        # Total published-row bound across the whole root (the DFS `max_files`/`index_max_files`
        # invariant). None means unbounded. When the cap is reached the crawl stops adding rows and
        # reports truncation, so a pathological tree cannot grow an unbounded index.
        self.max_total_entries = None if max_total_entries is None else max(0, int(max_total_entries))
        self.operation = operation
        self.open_order: list[str] = []
        self.errors: list[str] = []
        self.truncated = False
        self.published_depth = 0
        # Step 5: the last redacted progress `_recompute_progress` computed, read by the post-commit
        # signal so it never re-opens SQLite. Numeric/flag only -- no path ever enters this dict.
        self.last_published_coverage: dict[str, object] = {}
        self.scanned_directories = 0
        self.scanned_files = 0
        self._provided_root_fd = root_fd
        self._root_fd: int | None = None
        self.root_identity: tuple[int, int] | None = None
        self._root_context: contextlib.ExitStack | None = None
        self._lock_fd: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def _close_handles(self) -> None:
        """Idempotent closer for the per-root lock and root descriptors.

        Shared by `__exit__` and the `__enter__` failure path so a partially-entered build never
        leaks a descriptor. Close the root first, then unlock and close the lock, resetting each
        field so a second call (or a later `__exit__` after a failed `__enter__`) is a no-op."""
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None
        if self._root_context is not None:
            self._root_context.close()
            self._root_context = None
        if self._lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> "ProgressiveBuild":
        file_index.INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(str(file_index._build_lock_path(self.root)), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | file_index._nofollow_flag()
            if self._provided_root_fd is not None:
                self._root_fd = os.dup(self._provided_root_fd)
            else:
                # Direct builders still route through the one filesystem authorization owner.
                # Production index builds pass RootIndex.root_fd below, so neither path reopens an
                # already-authorized root name after a namespace replacement.
                root_context = contextlib.ExitStack()
                root_handle = root_context.enter_context(
                    filesystem_paths.safe_path(
                        str(self.root),
                        flags=directory_flags,
                        operation=self.operation or "index_build",
                    )
                )
                self._root_context = root_context
                self._root_fd = os.dup(root_handle.descriptor)
            self.root_identity = file_index.parse_root_identity(
                file_index.root_identity(os.fstat(self._root_fd))
            )
        except BaseException:
            # A failed flock or root open means `__exit__` will never run; reclaim the lock
            # descriptor (and any root descriptor) here before propagating the original error.
            self._close_handles()
            raise
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._close_handles()

    # -- queue seeding -----------------------------------------------------

    def enqueue_startup(
        self,
        now: float | None = None,
        *,
        reason: str = REASON_STARTUP,
        should_stop: Callable[[], bool] | None = None,
    ) -> bool:
        """Claim this ``(root, generation)`` and enqueue the single listing of the configured root.

        The generation claim is item 1's ONE atomic compare-and-set (a single ``BEGIN IMMEDIATE``
        transaction), not the unconditional metadata+frontier write it replaced. Reading the persisted
        ``active_generation`` and deciding whether to take ownership are one indivisible step so a
        stale worker can never move ownership backward between the read and the write:

          * missing metadata  -> INITIALIZE (this generation establishes the store),
          * EQUAL generation  -> RESUME (re-seed the root listing for the generation that owns it),
          * strictly NEWER    -> ADVANCE (this generation supersedes the persisted one),
          * strictly LOWER    -> REJECTED: leave ``active_generation`` and the frontier untouched.

        Returns ``True`` when the claim was taken (initialized/resumed/advanced) and the in-memory
        frontier now holds the root listing, ``False`` when the claim was REJECTED (a newer generation
        owns the store) or CANCELLED (``should_stop`` fired before the claim mutated anything). A
        rejected/cancelled claim writes NOTHING -- no metadata, no frontier row, no in-memory item --
        so ``build_root_progressively`` exits cleanly without publishing.

        ``reason`` selects the precedence class of the root listing: ``startup-depth-1`` for the
        first indexing work (highest priority) and ``full-safety-refresh`` for the lowest-priority
        periodic reconciliation (item 7). Both drive the SAME breadth-first frontier, so a safety
        refresh is resumable and preemptible through it, never a second scheduler or queue.
        """
        wall = time.time() if now is None else float(now)
        priority = priority_for_reason(reason)
        item = FrontierItem(
            root=str(self.root),
            directory=str(self.root),
            depth=1,
            generation=self.generation,
            reason=reason,
            priority=priority,
            enqueued_at=wall,
        )
        # Check cancellation BEFORE opening the claim transaction, so a demotion/unindex that lands
        # before we take the SQLite write lock cannot mutate metadata or the frontier at all.
        if should_stop is not None and should_stop():
            return False
        with self._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Re-check cancellation AFTER acquiring the build (SQLite write) lock but before any
                # mutation: a cancellation observed here rolls back with nothing written.
                if should_stop is not None and should_stop():
                    conn.execute("ROLLBACK")
                    return False
                row = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'active_generation'"
                ).fetchone()
                persisted = int(row[0]) if row and str(row[0]).isdigit() else None
                if persisted is not None and self.generation < persisted:
                    # A strictly LOWER generation may never take ownership backward: a newer generation
                    # already owns the store. Leave active_generation and the frontier untouched.
                    conn.execute("ROLLBACK")
                    return False
                # Protocol #3: a POST-UNINDEX generation establishes a CLEAN generation. When this
                # build's frozen tombstone identity differs from the persisted snapshot's stamped
                # identity, the persisted rows belong to a store the user deleted; clear entries,
                # coverage, and frontier in this same claim transaction BEFORE publishing rows stamped
                # with the current identity, so a truncated/partial post-unindex crawl can never leave
                # deleted-store rows readable under the new stamp. A same-identity refresh (or a build
                # that superseded no marker, ``tombstone_identity == ""``) keeps its rows for
                # stale-while-rebuild readability.
                stamp_row = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'tombstone_identity'"
                ).fetchone()
                persisted_identity = str(stamp_row[0]) if stamp_row and stamp_row[0] is not None else ""
                root_identity_row = conn.execute(
                    "SELECT value FROM metadata WHERE key = ?",
                    (file_index.AUTHORIZED_ROOT_IDENTITY_FIELD,),
                ).fetchone()
                persisted_root_identity = file_index._metadata_root_identity(
                    {
                        file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: (
                            root_identity_row[0] if root_identity_row is not None else None
                        )
                    }
                )
                root_generation_changed = (
                    self.root_identity is None or persisted_root_identity != self.root_identity
                )
                if root_generation_changed or (
                    self.tombstone_identity and self.tombstone_identity != persisted_identity
                ):
                    conn.execute("DELETE FROM entries")
                    conn.execute("DELETE FROM directory_coverage")
                    conn.execute("DELETE FROM frontier")
                    # A clean post-unindex generation must not leave the deleted store's journal deltas
                    # readable; the high-water metadata stays (monotonic), so revisions never reuse.
                    conn.execute("DELETE FROM change_journal")
                    # Invalidate the deleted store's publication proof in the SAME transaction that
                    # clears its rows. Until this generation publishes its first directory, readers
                    # must see startup-only metadata and fall back to the live filesystem walk.
                    file_index._invalidate_snapshot_publication(conn)
                # missing / equal / strictly-newer: initialize / resume / advance.
                self._write_active_metadata(conn)
                self._persist_frontier_item(conn, item)
                conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        # Only a claimed generation seeds the in-memory queue, so a rejected/cancelled claim leaves the
        # frontier empty and the crawl finds nothing to publish.
        self.frontier.enqueue(item)
        return True

    def resume(self) -> int:
        """Rebuild the in-memory queue from the durable frontier checkpoint.

        A restart resumes incomplete breadth-first work at the shallowest pending
        directory without a new recursive discovery pass: the pending directories
        are read straight from the ``frontier`` table, not rediscovered by walking
        the tree.
        """
        with self._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.commit()
            conn.execute("BEGIN")
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if self.root_identity is None or not file_index._root_identity_matches(
                metadata,
                self.root_identity,
            ):
                return 0
            rows = conn.execute(
                "SELECT root, directory, depth, generation, reason, priority, enqueued_at, retries, seq "
                "FROM frontier WHERE generation = ? AND state = 'pending'",
                (self.generation,),
            ).fetchall()
        items = [
            FrontierItem(
                root=str(row[0]),
                directory=str(row[1]),
                depth=int(row[2]),
                generation=int(row[3]),
                reason=str(row[4]),
                priority=int(row[5]),
                enqueued_at=float(row[6]),
                retries=int(row[7]),
                seq=int(row[8]),
            )
            for row in rows
        ]
        self.frontier.load(items)
        return len(items)

    # -- one step ----------------------------------------------------------

    def step(self, *, now: float | None = None) -> FrontierItem | None:
        """Scan exactly one frontier directory and publish it atomically.

        Returns the item processed, or ``None`` when the frontier is empty. The
        generation fence is read INSIDE the publish transaction, so a newer
        generation that has taken over aborts this write instead of overwriting
        newer rows.
        """
        item = self.frontier.pop()
        if item is None:
            return None
        if self._root_fd is None:
            raise RuntimeError("ProgressiveBuild.step requires the context manager to pin the root")
        wall = time.time() if now is None else float(now)
        scan = scan_directory_once(
            self._root_fd,
            self.root,
            Path(item.directory),
            self.skip_dirs,
            self.exclude_path,
            max_entries=self.max_entries,
            operation=self.operation,
        )
        self.open_order.append(item.directory)
        if scan.error:
            self._record_failure(item, scan, wall)
            return item
        self._publish_directory(item, scan, wall)
        return item

    def run(
        self,
        *,
        max_steps: int | None = None,
        now: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> int:
        """Drain the frontier breadth-first. Returns the number of directories scanned.

        ``should_stop`` lets an owning build thread cancel a long crawl (demotion, unindex, or a
        newer generation) between directories without abandoning the last-known-good snapshot: the
        already-published directory transactions stay on disk and searchable.
        """
        steps = 0
        while max_steps is None or steps < max_steps:
            if should_stop is not None and should_stop():
                break
            if not self._generation_is_current():
                break
            item = self.step(now=now)
            if item is None:
                break
            steps += 1
        return steps

    # -- persistence -------------------------------------------------------

    def _connect(self) -> Any:
        # Route every writable BFS connection through the ONE connection-context owner so it is closed
        # in `finally` (item 5). `_connect_sqlite_index` as a bare `with` target commits but never
        # closes, leaking a descriptor per directory that the post-build unlink turns into a
        # `(deleted)` FD; `_sqlite_index_connection` closes it regardless of outcome. Every call site
        # here either commits explicitly or runs its own `BEGIN IMMEDIATE ... COMMIT`, so the owner's
        # trailing commit is a harmless no-op.
        return file_index._sqlite_index_connection(self.root)

    def _active_generation_matches(self, conn: Any) -> bool:
        """Item 1 fence, read INSIDE an open transaction: only an active generation EQUAL to this
        worker's may publish. Shared by every success/retry/terminal/missing transaction so the
        equality rule lives in ONE place, not copied per write path. A missing metadata row (a store
        this generation is establishing) counts as current."""
        row = conn.execute("SELECT value FROM metadata WHERE key = 'active_generation'").fetchone()
        active = int(row[0]) if row and str(row[0]).isdigit() else self.generation
        return active == self.generation

    def _generation_is_current(self) -> bool:
        with self._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            row = conn.execute("SELECT value FROM metadata WHERE key = 'active_generation'").fetchone()
        # Item 1: ONLY equality is current. A persisted active generation BELOW this worker's is a
        # store this generation has not yet claimed (it writes its own active generation at
        # `enqueue_startup`), and one ABOVE means a newer generation has taken over; neither may
        # publish. A missing row (a store this generation is about to establish) counts as current.
        active = int(row[0]) if row and str(row[0]).isdigit() else self.generation
        return active == self.generation

    def _persist_frontier_item(self, conn: sqlite3.Connection, item: FrontierItem) -> None:
        conn.execute(
            "INSERT INTO frontier(directory, root, depth, generation, reason, priority, enqueued_at, retries, seq, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending') "
            "ON CONFLICT(directory) DO UPDATE SET "
            "root=excluded.root, depth=excluded.depth, generation=excluded.generation, reason=excluded.reason, "
            "priority=excluded.priority, enqueued_at=excluded.enqueued_at, retries=excluded.retries, "
            "seq=excluded.seq, state='pending'",
            (
                item.directory,
                item.root,
                item.depth,
                item.generation,
                item.reason,
                item.priority,
                item.enqueued_at,
                item.retries,
                item.seq,
            ),
        )

    def _write_active_metadata(self, conn: sqlite3.Connection) -> None:
        signature = file_index._disk_skip_signature(self.root, self.skip_dirs, self.exclude_signature)
        base = {
            "version": str(file_index.INDEX_FORMAT_VERSION),
            "storage": "sqlite",
            "skip_signature": signature,
            "root": str(self.root),
            file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index._root_identity_metadata_value(
                self.root_identity
            ),
            "producer_epoch": file_index.self_process_epoch(),
        }
        existing = dict(conn.execute("SELECT key, value FROM metadata"))
        base.setdefault("active_generation", str(self.generation))
        base["active_generation"] = str(self.generation)
        base.setdefault("published_generation", existing.get("published_generation", "0"))
        base.setdefault("built_at", existing.get("built_at", repr(0.0)))
        # Protocol #2: stamp the frozen tombstone identity so a follower reading THIS generation's
        # metadata accepts the snapshot only against the exact unindex this build superseded.
        base["tombstone_identity"] = self.tombstone_identity
        file_index._replace_sqlite_metadata(conn, base)

    def _publish_directory(self, item: FrontierItem, scan: ScanResult, wall: float) -> None:
        """Publish one directory's rows, deletions, and child frontier atomically."""
        directory = item.directory
        if scan.truncated:
            # A truncated directory listing means this generation cannot claim full
            # coverage; record it before computing progress inside the transaction.
            self.truncated = True
        with self._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Generation fence (item 1): a publish commits ONLY when the SQLite active generation
                # EQUALS this worker's generation. A newer generation (active > self.generation) has
                # taken over and its rows must not be overwritten; an older active means this store is
                # not the one this generation established, so it must not write entries/coverage/
                # frontier/metadata for it either. Only equality publishes.
                if not self._active_generation_matches(conn):
                    conn.execute("ROLLBACK")
                    self.frontier.cancel_generation(str(self.root), self.generation)
                    return

                # Total-entry bound (the DFS `index_max_files` invariant). Trim this directory's
                # rows to the remaining budget and stop expanding once the cap is reached, so a
                # very large tree cannot grow an unbounded index. Truncation is reported, never
                # presented as full coverage.
                cap_reached = False
                if self.max_total_entries is not None:
                    current_total = int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
                    remaining = self.max_total_entries - current_total
                    if len(scan.files) >= remaining:
                        scan.files = scan.files[: max(0, remaining)]
                        scan.truncated = True
                        self.truncated = True
                        cap_reached = True

                # Step 2: every committed upsert/delete is journaled in THIS SAME transaction as the
                # entries + coverage write, so a rolled-back publication exposes neither rows nor journal
                # entries, and a max_files-truncated directory journals only the rows it actually kept.
                journal_records: list[tuple[str, str, str, str, int, int]] = []
                committed_revision: int | None = None
                fresh_paths = {row_entry[0] for row_entry in scan.files}
                for stale_path in _direct_child_paths(conn, directory):
                    if stale_path not in fresh_paths:
                        conn.execute("DELETE FROM entries WHERE path = ?", (stale_path,))
                        journal_records.append(
                            (
                                file_index.JOURNAL_OP_DELETE,
                                stale_path,
                                Path(stale_path).name,
                                file_index._relative_to_root_posix(stale_path, self.root),
                                0,
                                0,
                            )
                        )
                if scan.files:
                    conn.executemany(
                        "INSERT INTO entries(path, name, relative_path, size, mtime, generation) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET name=excluded.name, relative_path=excluded.relative_path, "
                        "size=excluded.size, mtime=excluded.mtime, generation=excluded.generation",
                        (
                            (path, name, rel, int(size), int(mtime), self.generation)
                            for path, name, rel, size, mtime in scan.files
                        ),
                    )
                    for path, name, rel, size, mtime in scan.files:
                        journal_records.append((file_index.JOURNAL_OP_UPSERT, str(path), str(name), str(rel), int(size), int(mtime)))
                if journal_records:
                    committed_revision = file_index._append_change_journal(conn, self.generation, self.tombstone_identity, journal_records)

                conn.execute(
                    "INSERT INTO directory_coverage(directory, depth, generation, state, scanned_at, file_count, truncated, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, '') "
                    "ON CONFLICT(directory) DO UPDATE SET depth=excluded.depth, generation=excluded.generation, "
                    "state=excluded.state, scanned_at=excluded.scanned_at, file_count=excluded.file_count, "
                    "truncated=excluded.truncated, error=''",
                    (directory, item.depth, self.generation, file_index.COVERAGE_COMPLETE, wall, len(scan.files), 1 if scan.truncated else 0),
                )
                conn.execute("DELETE FROM frontier WHERE directory = ? AND generation = ?", (directory, self.generation))

                for child_name in ([] if cap_reached else scan.child_directories):
                    child_path = str(Path(directory) / child_name)
                    child_item = FrontierItem(
                        root=str(self.root),
                        directory=child_path,
                        depth=item.depth + 1,
                        generation=self.generation,
                        reason=REASON_BREADTH,
                        priority=PRIORITY_BREADTH_EXPANSION,
                        enqueued_at=wall,
                    )
                    if self.frontier.enqueue(child_item):
                        self._persist_frontier_item(conn, child_item)
                    else:
                        self.truncated = True

                self._recompute_progress(conn, wall)
                conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        # Post-commit accounting (observability only; not part of the transaction).
        self.scanned_directories += 1
        self.scanned_files += len(scan.files)
        if scan.truncated:
            self.truncated = True
        # Step 5: the transaction committed a new journal revision -> emit the redacted, coalesced
        # progress signal so a follower web process knows to pull committed deltas by cursor. Emitted
        # AFTER commit (never inside the transaction) so a rolled-back publication signals nothing.
        self._emit_progress_signal(committed_revision)

    def _record_failure(self, item: FrontierItem, scan: ScanResult, wall: float) -> None:
        """A per-directory failure never discards the last-known-good snapshot.

        A permission error, disappearing path, or transient I/O error is recorded
        against that one directory and, within the retry bound, requeued. It does
        not wedge the rest of the frontier or roll back other directories' rows.
        """
        if not scan.missing:
            requeued = self.frontier.requeue(item)
            if requeued is not None:
                # Persist the incremented retry count durably BEFORE the next attempt, so a crash or
                # restart resumes at the same retry and the bound still terminates it. The generation
                # fence (item 1) guards this write too: an abandoned generation must never rewrite the
                # frontier's retry counts for a store a newer generation now owns.
                with self._connect() as conn:
                    file_index._ensure_sqlite_schema(conn)
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        if not self._active_generation_matches(conn):
                            conn.execute("ROLLBACK")
                            self.frontier.cancel_generation(str(self.root), self.generation)
                            return
                        self._persist_frontier_item(conn, requeued)
                        conn.execute("COMMIT")
                    except BaseException:
                        with contextlib.suppress(sqlite3.Error):
                            conn.execute("ROLLBACK")
                        raise
                return
        with self._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Terminal-failure and missing-directory paths carry the SAME generation fence as a
                # successful publish: neither may rewrite failure coverage or delete a newer
                # generation's rows once a newer generation has taken over.
                if not self._active_generation_matches(conn):
                    conn.execute("ROLLBACK")
                    self.frontier.cancel_generation(str(self.root), self.generation)
                    return
                state = file_index.COVERAGE_COMPLETE if scan.missing else file_index.COVERAGE_FAILED
                conn.execute(
                    "INSERT INTO directory_coverage(directory, depth, generation, state, scanned_at, file_count, truncated, error) "
                    "VALUES (?, ?, ?, ?, ?, 0, 0, ?) "
                    "ON CONFLICT(directory) DO UPDATE SET depth=excluded.depth, generation=excluded.generation, "
                    "state=excluded.state, scanned_at=excluded.scanned_at, error=excluded.error",
                    (item.directory, item.depth, self.generation, state, wall, scan.error or ("missing" if scan.missing else "")),
                )
                committed_revision: int | None = None
                if scan.missing:
                    missing_deletes: list[tuple[str, str, str, str, int, int]] = []
                    for stale_path in _direct_child_paths(conn, item.directory):
                        conn.execute("DELETE FROM entries WHERE path = ?", (stale_path,))
                        missing_deletes.append(
                            (
                                file_index.JOURNAL_OP_DELETE,
                                stale_path,
                                Path(stale_path).name,
                                file_index._relative_to_root_posix(stale_path, self.root),
                                0,
                                0,
                            )
                        )
                    if missing_deletes:
                        committed_revision = file_index._append_change_journal(conn, self.generation, self.tombstone_identity, missing_deletes)
                conn.execute("DELETE FROM frontier WHERE directory = ? AND generation = ?", (item.directory, self.generation))
                self._recompute_progress(conn, wall)
                conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        # A missing-directory publication that removed rows commits a new journal revision too; route
        # it through the SAME signal so a delete is not a silent hole in the client's cursor stream.
        self._emit_progress_signal(committed_revision)
        if not scan.missing:
            self.errors.append(f"{item.directory}:{scan.error}")

    def _recompute_progress(self, conn: sqlite3.Connection, wall: float) -> None:
        """Advance ``published_depth`` only after a whole layer is terminal."""
        pending_row = conn.execute(
            "SELECT MIN(depth) FROM frontier WHERE generation = ? AND state = 'pending'",
            (self.generation,),
        ).fetchone()
        shallowest_pending = pending_row[0] if pending_row and pending_row[0] is not None else None
        completed_row = conn.execute(
            "SELECT MAX(depth) FROM directory_coverage WHERE generation = ? AND state IN ('complete', 'failed')",
            (self.generation,),
        ).fetchone()
        max_completed = int(completed_row[0]) if completed_row and completed_row[0] is not None else 0
        failed_row = conn.execute(
            "SELECT COUNT(*) FROM directory_coverage WHERE generation = ? AND state = 'failed'",
            (self.generation,),
        ).fetchone()
        failed = int(failed_row[0]) if failed_row else 0
        truncated_row = conn.execute(
            "SELECT COUNT(*) FROM directory_coverage WHERE generation = ? AND truncated = 1",
            (self.generation,),
        ).fetchone()
        any_truncated = self.truncated or (bool(truncated_row) and int(truncated_row[0]) > 0)
        if shallowest_pending is None:
            published_depth = max_completed
            full_coverage = failed == 0 and not any_truncated
            if full_coverage:
                # Finalize a clean, complete generation: purge rows from prior generations that this
                # generation never revisited. A vanished child DIRECTORY is not a file row and is
                # never re-enqueued, so its old descendant rows would otherwise survive forever.
                # Stale-while-build readability is preserved for cancelled, truncated, or failed
                # generations (this branch is unreachable for them) so a partial rebuild never blanks
                # the last-known-good snapshot before its replacement is complete.
                conn.execute("DELETE FROM entries WHERE generation != ?", (self.generation,))
                conn.execute("DELETE FROM directory_coverage WHERE generation != ?", (self.generation,))
                conn.execute("DELETE FROM frontier WHERE generation != ?", (self.generation,))
        else:
            # A layer is complete only when nothing shallower is still pending.
            published_depth = max(0, int(shallowest_pending) - 1)
            full_coverage = False
        self.published_depth = published_depth
        frontier_stats = conn.execute(
            "SELECT COUNT(*), MIN(depth) FROM frontier WHERE generation = ? AND state = 'pending'",
            (self.generation,),
        ).fetchone()
        frontier_size = int(frontier_stats[0]) if frontier_stats else 0
        frontier_depth = int(frontier_stats[1]) if frontier_stats and frontier_stats[1] is not None else 0
        entry_count_row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
        entry_count = int(entry_count_row[0]) if entry_count_row else 0
        metadata = {
            "version": str(file_index.INDEX_FORMAT_VERSION),
            "storage": "sqlite",
            "skip_signature": file_index._disk_skip_signature(self.root, self.skip_dirs, self.exclude_signature),
            "root": str(self.root),
            file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index._root_identity_metadata_value(
                self.root_identity
            ),
            "producer_epoch": file_index.self_process_epoch(),
            "built_at": repr(float(wall)),
            "truncated": "1" if self.truncated else "0",
            "entry_count": str(entry_count),
            "entries_signature": f"bfs:{self.generation}:{entry_count}",
            "active_generation": str(self.generation),
            # Layer 1 (the root's own listing) publishes this generation the moment
            # its transaction commits, so readers see a live published generation
            # before any deeper layer finishes.
            "published_generation": str(self.generation),
            "published_depth": str(published_depth),
            "frontier_depth": str(frontier_depth),
            "frontier_size": str(frontier_size),
            "full_coverage": "1" if full_coverage else "0",
            "last_progress_at": repr(float(wall)),
            # Protocol #2: every published directory carries the frozen tombstone identity, so a
            # follower reading a partial mid-crawl fails closed on the identity rule, not by time.
            "tombstone_identity": self.tombstone_identity,
        }
        file_index._replace_sqlite_metadata(conn, metadata)
        # Step 5: the numeric-only progress the post-commit signal carries. Captured here (inside the
        # writer's transaction, from the values just computed) so the emit needs no second SQLite read,
        # and holding ONLY digits/flags -- never a path -- so nothing on the shared bus can disclose
        # filesystem data.
        self.last_published_coverage = {
            "published_depth": published_depth,
            "frontier_depth": frontier_depth,
            "frontier_size": frontier_size,
            "entry_count": entry_count,
            "full_coverage": bool(full_coverage),
            "truncated": bool(any_truncated),
        }

    def _emit_progress_signal(self, revision: int | None) -> None:
        """Publish the redacted, coalesced progress signal for a committed journal revision.

        ONE emit path shared by the publish and the missing-directory delete branches, so a delete is
        signalled identically to an upsert. ``revision is None`` means the transaction committed no new
        journal rows (nothing changed), so there is nothing for a client to pull and no signal is sent.
        The redaction + per-root coalescing live in `file_index.notify_search_progress`."""
        if revision is None:
            return
        file_index.notify_search_progress(self.root, self.generation, revision, self.last_published_coverage)


def build_root_progressively(
    root: Path,
    skip_dirs: set[str],
    *,
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    generation: int,
    max_entries: int = DEFAULT_MAX_DIR_ENTRIES,
    max_total_entries: int | None = None,
    max_frontier_items: int = DEFAULT_MAX_FRONTIER_ITEMS,
    operation: str = "",
    reason: str = REASON_STARTUP,
    should_stop: Callable[[], bool] | None = None,
    tombstone_identity: str | None = None,
    root_fd: int | None = None,
) -> ProgressiveBuild:
    """Run one full breadth-first build of ``root`` and return its build record.

    This is the whole-tree convenience entry used by tests and by any future
    scheduler lease. It drives the same directory-at-a-time engine, so the
    filesystem open order stays breadth-first even for a single call. ``reason``
    selects the root listing's precedence class (startup vs safety refresh).
    ``tombstone_identity`` is the frozen unindex identity this build superseded (protocol #2/#3).
    """
    frontier = BfsFrontier(max_items=max_frontier_items)
    build = ProgressiveBuild(
        root,
        skip_dirs,
        exclude_path=exclude_path,
        exclude_signature=exclude_signature,
        generation=generation,
        frontier=frontier,
        max_entries=max_entries,
        max_total_entries=max_total_entries,
        operation=operation,
        tombstone_identity=tombstone_identity,
        root_fd=root_fd,
    )
    with build:
        # P0-4: resume a durable partial frontier FIRST. If this generation still has pending frontier
        # rows (an indexd restart mid-crawl), rebuild the in-memory queue straight from the ``frontier``
        # checkpoint and drain it -- do NOT re-list the root through `enqueue_startup`, which would
        # re-seed depth 1 and leave the deeper pending directories stranded. Only a generation with NO
        # durable frontier falls through to the atomic startup claim.
        if build.resume() == 0:
            # Item 1: an atomic generation claim. A stale/superseded generation is REJECTED here (a
            # newer generation owns the store) and a cancellation before the claim returns cleanly --
            # either way nothing is published and, critically, no manifest is written over the newer
            # snapshot.
            if not build.enqueue_startup(reason=reason, should_stop=should_stop):
                return build
        build.run(should_stop=should_stop)
        with build._connect() as conn:
            file_index._ensure_sqlite_schema(conn)
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        # Item 2: never publish a manifest until SQLite holds a COMPLETE typed published-snapshot
        # metadata shape FOR THIS generation. A build cancelled before its first directory publishes
        # leaves only startup metadata (no `entries_signature`/`entry_count`/`truncated`), so writing a
        # manifest from it would raise (KeyError) or, on a root with a PRIOR valid snapshot, replace a
        # good manifest with a startup-only one. The published-snapshot shape is only written by
        # `_recompute_progress` after a directory commits, and its `entries_signature` is stamped with
        # this generation, so this predicate is true only once this generation has actually published.
        if file_index._is_published_snapshot_metadata(metadata, build.generation):
            file_index._write_manifest(root, metadata)
    return build


def build_root_into_index(
    root: Path,
    skip_dirs: set[str],
    *,
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
    generation: int,
    operation: str = "",
    max_entries: int = DEFAULT_MAX_DIR_ENTRIES,
    max_total_entries: int | None = None,
    reason: str = "",
    stop_event: Any = None,
    tombstone_identity: str | None = None,
    root_fd: int | None = None,
) -> bool:
    """Run one breadth-first full build for a configured root, driven by ``file_index._run_build``.

    This is the cutover adapter registered through ``file_index.set_bfs_full_build_runner``: the
    persistent indexer's full build for a configured root reaches the directory-at-a-time frontier
    here instead of the DFS ``_walk_root_with_metrics``. It publishes each directory's rows to the
    same per-root SQLite the reader already opens, so a follower search sees layer 1 as soon as the
    root listing commits, without waiting for deep descendants. Returns ``True`` when the crawl
    finished, ``False`` when a stop signal cancelled it (the published rows remain readable).
    """
    should_stop = stop_event.is_set if stop_event is not None else None
    # An unset reason ("" from the file_index call site, which cannot import these constants) is the
    # default startup-depth-1 build; a caller that passes REASON_SAFETY runs the lowest-priority
    # periodic reconciliation through this same frontier.
    effective_reason = str(reason) or REASON_STARTUP
    build_root_progressively(
        root,
        set(skip_dirs),
        exclude_path=exclude_path,
        exclude_signature=exclude_signature,
        generation=int(generation),
        max_entries=int(max_entries),
        max_total_entries=None if max_total_entries is None else int(max_total_entries),
        operation=operation,
        reason=effective_reason,
        should_stop=should_stop,
        tombstone_identity=tombstone_identity,
        root_fd=root_fd,
    )
    return not (stop_event is not None and stop_event.is_set())

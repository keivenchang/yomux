"""Persistent, stale-while-revalidate file index for quick-open search.

The live `search_files` walk re-walks a root on every keystroke and is capped
(MAX_SEARCH_DIRS / MAX_SEARCH_FILES), so a huge root like ~/dynamo is both slow
and incomplete. This module builds a per-root index of every file once (in a
background thread, respecting the same skip dirs), keeps it in memory + on disk,
and serves quick-open queries from it instantly with no per-query walk and no
50k coverage cap. It is an ACCELERATOR: callers fall back to the live walk while
an index is still building or on any error, so search never depends on it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any
from typing import Callable

from .common import STATE_DIR
from .common import start_thread_with_rollback


INDEX_DIR = STATE_DIR / "search_index"
# Upper bound on indexed files per root, to bound memory on pathological trees.
MAX_INDEX_FILES = 400_000
# Serve from the index immediately; rebuild in the background once it is older
# than this (stale-while-revalidate), which also prunes deleted files.
INDEX_TTL_SECONDS = 120.0
# C11: bump when the on-disk storage shape changes so old/incompatible indexes rebuild for a clear reason.
INDEX_FORMAT_VERSION = 3
_BACKGROUND_OWNER_CHECKER: Callable[[str], bool] | None = None
_BACKGROUND_OWNER_REFRESH_REQUESTER: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
_BACKGROUND_OWNER_BYTES_RECORDER: Callable[[int], None] | None = None
_BACKGROUND_OWNER_DONE_NOTIFIER: Callable[[str, dict[str, Any]], None] | None = None
SEARCH_INDEX_ROLE = "search-index"


def _skip_signature(skip_dirs: set[str], exclude_signature: str = "") -> str:
    # C11: the set of skipped directories is part of what an index means; if it changes, the cached
    # index no longer matches the requested coverage and must rebuild.
    suffix = f"|exclude:{exclude_signature}" if exclude_signature else ""
    return ",".join(sorted(skip_dirs)) + suffix

# (path, name, relative_path, size, mtime)
IndexEntry = tuple[str, str, str, int, int]


class RootIndex:
    def __init__(self, root: Path):
        self.root = root
        self.entries: list[IndexEntry] = []
        self.built_at = 0.0
        self.ready = False
        self.building = False
        self.truncated = False
        self.disk_metadata_ready = False
        self.disk_entry_count = 0
        self.signature = ""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()


_REGISTRY: dict[str, RootIndex] = {}
_REGISTRY_LOCK = threading.Lock()


def clear_memory_indexes() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def set_background_owner_checker(checker: Callable[[str], bool] | None) -> None:
    global _BACKGROUND_OWNER_CHECKER
    _BACKGROUND_OWNER_CHECKER = checker


def set_background_owner_refresh_requester(requester: Callable[[str, dict[str, Any]], dict[str, Any]] | None) -> None:
    global _BACKGROUND_OWNER_REFRESH_REQUESTER
    _BACKGROUND_OWNER_REFRESH_REQUESTER = requester


def set_background_owner_bytes_recorder(recorder: Callable[[int], None] | None) -> None:
    global _BACKGROUND_OWNER_BYTES_RECORDER
    _BACKGROUND_OWNER_BYTES_RECORDER = recorder


def set_background_owner_done_notifier(notifier: Callable[[str, dict[str, Any]], None] | None) -> None:
    global _BACKGROUND_OWNER_DONE_NOTIFIER
    _BACKGROUND_OWNER_DONE_NOTIFIER = notifier


def background_owner_can_build() -> bool:
    if _BACKGROUND_OWNER_CHECKER is None:
        return True
    return bool(_BACKGROUND_OWNER_CHECKER(SEARCH_INDEX_ROLE))


def request_background_owner_refresh(payload: dict[str, Any]) -> dict[str, Any]:
    if _BACKGROUND_OWNER_REFRESH_REQUESTER is None:
        return {"ok": False, "accepted": False, "fallback": False, "error": "no background owner refresh requester"}
    return _BACKGROUND_OWNER_REFRESH_REQUESTER(SEARCH_INDEX_ROLE, payload)


def record_search_index_bytes_written(byte_count: int) -> None:
    if _BACKGROUND_OWNER_BYTES_RECORDER is not None:
        _BACKGROUND_OWNER_BYTES_RECORDER(byte_count)


def notify_background_owner_done(payload: dict[str, Any]) -> None:
    if _BACKGROUND_OWNER_DONE_NOTIFIER is not None:
        _BACKGROUND_OWNER_DONE_NOTIFIER(SEARCH_INDEX_ROLE, payload)


def _index_disk_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.sqlite3"


def _legacy_index_json_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.json"


def _index_manifest_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.manifest.json"


def _build_lock_path(root: Path) -> Path:
    # C11: a per-root file lock so two server processes (e.g. :7000 and :7001 sharing STATE_DIR) don't
    # duplicate the same expensive walk or delete while another build is persisting.
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.lock"


def _tombstone_path(root: Path) -> Path:
    # C11: written on unindex so a SECOND server process (sharing STATE_DIR) that still holds a ready
    # in-memory copy drops it instead of serving deleted-file results indefinitely.
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.tomb"


def _tombstone_time(root: Path) -> float:
    # The unindex timestamp recorded on disk (0.0 if none). An index built before this is stale.
    try:
        return float(_tombstone_path(root).read_text(encoding="utf-8").strip() or 0.0)
    except (OSError, ValueError):
        return 0.0


def _clear_tombstone(root: Path) -> None:
    try:
        _tombstone_path(root).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def walk_root(
    root: Path,
    skip_dirs: set[str],
    stop_event: threading.Event | None = None,
    exclude_path: Callable[[Path], bool] | None = None,
) -> tuple[list[IndexEntry], bool]:
    """Collect every regular file under root, skipping skip_dirs. Cancellable."""
    entries: list[IndexEntry] = []
    truncated = False
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        if stop_event is not None and stop_event.is_set():
            return entries, True
        current_path = Path(current)
        dirs[:] = sorted((name for name in dirs if name not in skip_dirs), key=str.lower)
        if exclude_path is not None:
            dirs[:] = [name for name in dirs if not exclude_path(current_path / name)]
        for name in files:
            if len(entries) >= MAX_INDEX_FILES:
                return entries, True
            path = current_path / name
            if exclude_path is not None and exclude_path(path):
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = name
            entries.append((str(path), name, rel, int(st.st_size), int(st.st_mtime)))
    return entries, truncated


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


def _connect_sqlite_index(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_index_disk_path(root), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        "ord INTEGER PRIMARY KEY, "
        "path TEXT NOT NULL, "
        "name TEXT NOT NULL, "
        "relative_path TEXT NOT NULL, "
        "size INTEGER NOT NULL, "
        "mtime INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS entries_name_idx ON entries(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS entries_relative_path_idx ON entries(relative_path)")
    conn.execute(f"PRAGMA user_version={INDEX_FORMAT_VERSION}")


def _replace_sqlite_metadata(conn: sqlite3.Connection, metadata: dict[str, str]) -> None:
    conn.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        sorted(metadata.items()),
    )


def _replace_sqlite_entries(conn: sqlite3.Connection, entries: list[IndexEntry]) -> None:
    conn.execute("DELETE FROM entries")
    conn.executemany(
        "INSERT INTO entries(ord, path, name, relative_path, size, mtime) VALUES (?, ?, ?, ?, ?, ?)",
        (
            (ordinal, str(path_str), str(name), str(rel), int(size), int(mtime))
            for ordinal, (path_str, name, rel, size, mtime) in enumerate(entries)
        ),
    )


def _write_manifest(root: Path, metadata: dict[str, str]) -> None:
    manifest = {
        "version": INDEX_FORMAT_VERSION,
        "storage": "sqlite",
        "skip_signature": metadata["skip_signature"],
        "root": metadata["root"],
        "built_at": float(metadata["built_at"]),
        "truncated": metadata["truncated"] == "1",
        "entry_count": int(metadata["entry_count"]),
        "entries_signature": metadata["entries_signature"],
    }
    manifest_tmp = _index_manifest_path(root).with_suffix(".manifest.json.tmp")
    manifest_tmp.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    manifest_tmp.replace(_index_manifest_path(root))


def _persist(ri: RootIndex, skip_dirs: set[str], exclude_signature: str = "") -> None:
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        signature = _skip_signature(skip_dirs, exclude_signature)
        entries_signature = _entries_signature(ri.entries)
        metadata = {
            "version": str(INDEX_FORMAT_VERSION),
            "storage": "sqlite",
            "skip_signature": signature,
            "root": str(ri.root),
            "built_at": repr(float(ri.built_at)),
            "truncated": "1" if ri.truncated else "0",
            "entry_count": str(len(ri.entries)),
            "entries_signature": entries_signature,
        }
        previous = _load_disk_metadata(ri.root, skip_dirs, exclude_signature)
        before_size = _sqlite_storage_size(ri.root)
        with _connect_sqlite_index(ri.root) as conn:
            _ensure_sqlite_schema(conn)
            db_metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            entries_unchanged = (
                previous is not None
                and db_metadata.get("version") == str(INDEX_FORMAT_VERSION)
                and db_metadata.get("storage") == "sqlite"
                and db_metadata.get("skip_signature") == signature
                and db_metadata.get("root") == str(ri.root)
                and db_metadata.get("entries_signature") == entries_signature
            )
            _replace_sqlite_metadata(conn, metadata)
            if not entries_unchanged:
                _replace_sqlite_entries(conn, ri.entries)
        _write_manifest(ri.root, metadata)
        after_size = _sqlite_storage_size(ri.root)
        record_search_index_bytes_written(max(0, after_size - before_size) if entries_unchanged else after_size)
    except (OSError, sqlite3.DatabaseError):
        pass


def _load_disk_metadata(root: Path, skip_dirs: set[str], exclude_signature: str = "") -> dict[str, Any] | None:
    if not _index_disk_path(root).exists():
        return None
    try:
        raw = json.loads(_index_manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("root") != str(root):
        return None
    if raw.get("version") != INDEX_FORMAT_VERSION or raw.get("storage") != "sqlite" or raw.get("skip_signature") != _skip_signature(skip_dirs, exclude_signature):
        return None
    return raw


def _load_disk(root: Path, skip_dirs: set[str], exclude_signature: str = "") -> tuple[list[IndexEntry], float, bool] | None:
    try:
        with sqlite3.connect(_index_disk_path(root), timeout=30.0) as conn:
            _ensure_sqlite_schema(conn)
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if not _sqlite_metadata_matches(metadata, root, skip_dirs, exclude_signature):
                return None
            rows = conn.execute("SELECT path, name, relative_path, size, mtime FROM entries ORDER BY ord").fetchall()
    except (OSError, sqlite3.DatabaseError):
        return None
    entries = [(str(path), str(name), str(rel), int(size), int(mtime)) for path, name, rel, size, mtime in rows]
    try:
        built_at = float(metadata.get("built_at") or 0.0)
    except ValueError:
        built_at = 0.0
    return entries, built_at, metadata.get("truncated") == "1"


def _sqlite_metadata_matches(metadata: dict[str, Any], root: Path, skip_dirs: set[str], exclude_signature: str = "") -> bool:
    return (
        metadata.get("root") == str(root)
        and metadata.get("version") == str(INDEX_FORMAT_VERSION)
        and metadata.get("storage") == "sqlite"
        and metadata.get("skip_signature") == _skip_signature(skip_dirs, exclude_signature)
    )


def _read_sqlite_index(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str = "",
) -> tuple[sqlite3.Connection, dict[str, Any]] | None:
    try:
        conn = sqlite3.connect(f"file:{_index_disk_path(root).as_posix()}?mode=ro", uri=True, timeout=30.0)
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        if not _sqlite_metadata_matches(metadata, root, skip_dirs, exclude_signature):
            conn.close()
            return None
        return conn, metadata
    except (OSError, sqlite3.DatabaseError):
        return None


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
        return conn.execute("SELECT path, name, relative_path, size, mtime FROM entries ORDER BY ord")
    clauses = []
    params = []
    for term in terms:
        pattern = _sqlite_subsequence_pattern(term)
        clauses.append("(lower(name) LIKE ? ESCAPE '\\' OR lower(relative_path) LIKE ? ESCAPE '\\' OR lower(path) LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    where = " AND ".join(clauses)
    return conn.execute(f"SELECT path, name, relative_path, size, mtime FROM entries WHERE {where} ORDER BY ord", params)


def search_disk_index(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str,
    match: Callable[[str, str, str], Any],
    max_results: int,
    literal_terms: list[str] | None = None,
) -> tuple[list[dict[str, Any]], bool] | None:
    """Search a persisted index without making a follower own/build or deserialize it wholesale."""
    opened = _read_sqlite_index(root, skip_dirs, exclude_signature)
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


def recent_disk_entries(
    root: Path,
    skip_dirs: set[str],
    exclude_signature: str,
    max_results: int,
    make_entry: Callable[[str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool] | None:
    """Return recent entries from a persisted index without loading all rows into follower memory."""
    opened = _read_sqlite_index(root, skip_dirs, exclude_signature)
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


def _run_build(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
) -> None:
    # C11: take a cross-process lock so a second server process does not duplicate the walk. If another
    # process holds it, leave whatever stale-but-ready disk copy we already loaded in place and bail.
    started = time.perf_counter()
    expected_signature = _skip_signature(skip_dirs, exclude_signature)
    lock_fd = None
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
        disk = _load_disk(ri.root, skip_dirs, exclude_signature)
        if disk is not None and (time.time() - disk[1]) <= INDEX_TTL_SECONDS:
            with ri.lock:
                ri.entries, ri.built_at, ri.truncated = disk
                ri.signature = expected_signature
                ri.disk_entry_count = len(ri.entries)
                ri.disk_metadata_ready = True
                ri.ready = True
                ri.building = False
            return
        entries, truncated = walk_root(ri.root, skip_dirs, ri.stop_event, exclude_path=exclude_path)
        if ri.stop_event.is_set():
            with ri.lock:
                ri.building = False
            return
        with ri.lock:
            ri.entries = entries
            ri.truncated = truncated
            ri.built_at = time.time()
            ri.signature = expected_signature
            ri.disk_entry_count = len(ri.entries)
            ri.disk_metadata_ready = True
            ri.ready = True
            ri.building = False
        _persist(ri, skip_dirs, exclude_signature)
        # C11: a fresh build supersedes any prior unindex, so clear the tombstone.
        _clear_tombstone(ri.root)
        notify_background_owner_done({
            "root": str(ri.root),
            "entries": len(ri.entries),
            "truncated": ri.truncated,
            "state": "ready",
            "compute_ms": round((time.perf_counter() - started) * 1000, 3),
        })
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


def _start_build(
    ri: RootIndex,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
) -> None:
    with ri.lock:
        if ri.building:
            return
        ri.building = True
        ri.stop_event = threading.Event()
        thread = threading.Thread(
            target=_run_build,
            args=(ri, set(skip_dirs), exclude_path, exclude_signature),
            name=f"file-index-{ri.root.name}",
            daemon=True,
        )
        ri.thread = thread

    def rollback() -> None:
        with ri.lock:
            if ri.thread is thread:
                ri.thread = None
                ri.building = False

    start_thread_with_rollback(thread, rollback)


def ensure_index(
    root: Path,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
) -> RootIndex:
    """Return the RootIndex for root, seeding from disk and kicking off a
    background (re)build when missing or stale. May return a not-yet-ready index."""
    key = str(root)
    expected_signature = _skip_signature(skip_dirs, exclude_signature)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
            if background_owner_can_build():
                disk = _load_disk(root, skip_dirs, exclude_signature)
                if disk is not None:
                    ri.entries, ri.built_at, ri.truncated = disk
                    ri.disk_entry_count = len(ri.entries)
                    ri.disk_metadata_ready = True
                    ri.signature = expected_signature
                    ri.ready = True
            else:
                metadata = _load_disk_metadata(root, skip_dirs, exclude_signature)
                if metadata is not None:
                    try:
                        ri.built_at = float(metadata.get("built_at") or 0.0)
                        ri.disk_entry_count = int(metadata.get("entry_count") or 0)
                    except (TypeError, ValueError):
                        ri.built_at = 0.0
                        ri.disk_entry_count = 0
                    ri.truncated = bool(metadata.get("truncated"))
                    ri.disk_metadata_ready = True
                    ri.signature = expected_signature
        elif not background_owner_can_build() and not ri.ready:
            metadata = _load_disk_metadata(root, skip_dirs, exclude_signature)
            if metadata is not None:
                try:
                    ri.built_at = float(metadata.get("built_at") or 0.0)
                    ri.disk_entry_count = int(metadata.get("entry_count") or 0)
                except (TypeError, ValueError):
                    ri.built_at = 0.0
                    ri.disk_entry_count = 0
                ri.truncated = bool(metadata.get("truncated"))
                ri.disk_metadata_ready = True
                ri.signature = expected_signature
    with ri.lock:
        if ri.ready and ri.signature != expected_signature:
            ri.entries = []
            ri.ready = False
            ri.built_at = 0.0
            ri.disk_metadata_ready = False
            ri.disk_entry_count = 0
            ri.signature = ""
    # C11: if another process unindexed this root after our copy was built, drop the stale in-memory
    # index so we stop serving deleted-file results (a later explicit access rebuilds and clears the tomb).
    tomb = _tombstone_time(root)
    if ri.ready and tomb and tomb > ri.built_at:
        with ri.lock:
            ri.entries = []
            ri.ready = False
            ri.built_at = 0.0
            ri.disk_metadata_ready = False
            ri.disk_entry_count = 0
    if background_owner_can_build() and (not ri.ready or (time.time() - ri.built_at) > INDEX_TTL_SECONDS):
        _start_build(ri, skip_dirs, exclude_path=exclude_path, exclude_signature=exclude_signature)
    return ri


def build_now(
    root: Path,
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None = None,
    exclude_signature: str = "",
) -> RootIndex:
    """Synchronously build (or rebuild) the index for root. Used at warm-up and in tests."""
    key = str(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
    ri.stop_event = threading.Event()
    _run_build(ri, set(skip_dirs), exclude_path=exclude_path, exclude_signature=exclude_signature)
    return ri


def search_index(ri: RootIndex, match: Callable[[str, str, str], Any], max_results: int) -> tuple[list[dict[str, Any]], bool]:
    """Filter+rank the in-memory index with `match(path, name, rel) -> entry|None`.
    Returns (sorted results capped at max_results, truncated)."""
    with ri.lock:
        snapshot = ri.entries
        index_truncated = ri.truncated
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
    with ri.lock:
        snapshot = ri.entries
        index_truncated = ri.truncated
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
    with _REGISTRY_LOCK:
        ri = _REGISTRY.pop(key, None)
    if ri is not None:
        ri.stop_event.set()
    try:
        _index_disk_path(root).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    for path in _sqlite_paths(root)[1:]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        _tombstone_path(root).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def indexed_ancestor_roots(path: Path) -> list[Path]:
    """Return every active or persisted index root that contains ``path``."""
    target = path.expanduser().resolve(strict=False)
    with _REGISTRY_LOCK:
        candidates = {Path(root).expanduser().resolve(strict=False) for root in _REGISTRY}
    try:
        manifests = tuple(INDEX_DIR.glob("*.manifest.json"))
    except OSError:
        manifests = ()
    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        root_text = payload.get("root") if isinstance(payload, dict) else None
        if not isinstance(root_text, str) or not root_text.startswith("/"):
            continue
        candidates.add(Path(root_text).resolve(strict=False))
    roots = []
    for root in candidates:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        roots.append(root)
    return sorted(roots, key=lambda root: (len(root.parts), str(root)))


def invalidate_path(path: Path) -> list[Path]:
    """Drop indexed ancestors through the existing cross-process unindex path."""
    roots = indexed_ancestor_roots(path)
    for root in roots:
        unindex(root)
    return roots

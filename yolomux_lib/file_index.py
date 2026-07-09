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
PERSIST_DEBOUNCE_SECONDS = 60.0
# Serve from the index immediately; rebuild in the background once it is older
# than this (stale-while-revalidate), which also prunes deleted files.
# A stale index remains immediately searchable while the owner refreshes it.  A
# short TTL turns ordinary Quick Open use into a recurring whole-tree walk.
INDEX_TTL_SECONDS = 30.0 * 60.0
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


def _resolved_index_dir() -> Path:
    return INDEX_DIR.expanduser().resolve(strict=False)


def _path_is_index_storage(path: Path) -> bool:
    candidate = path.expanduser().resolve(strict=False)
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
        self.entries: list[IndexEntry] = []
        self.built_at = 0.0
        self.last_full_build_at = 0.0
        self.ready = False
        self.building = False
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
    # C11: a per-root file lock so two server processes (e.g. :7770 and :7771 sharing STATE_DIR) don't
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
    max_files: int | None = None,
    relative_root: Path | None = None,
) -> tuple[list[IndexEntry], bool]:
    """Collect every regular file under root, skipping skip_dirs. Cancellable."""
    entries, truncated, _ignored = _walk_root_with_metrics(
        root,
        skip_dirs,
        stop_event,
        exclude_path,
        max_files=max_files,
        relative_root=relative_root,
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
) -> tuple[list[IndexEntry], bool, int]:
    entries: list[IndexEntry] = []
    ignored = 0
    limit = max(1, int(max_files if max_files is not None else MAX_INDEX_FILES))
    rel_root = relative_root or root
    truncated = False
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        if stop_event is not None and stop_event.is_set():
            return entries, True, ignored
        current_path = Path(current)
        kept_dirs = [name for name in dirs if name not in skip_dirs]
        ignored += len(dirs) - len(kept_dirs)
        dirs[:] = sorted(kept_dirs, key=str.lower)
        if exclude_path is not None:
            kept_dirs = [name for name in dirs if not exclude_path(current_path / name)]
            ignored += len(dirs) - len(kept_dirs)
            dirs[:] = kept_dirs
        for name in files:
            if len(entries) >= limit:
                return entries, True, ignored
            path = current_path / name
            if exclude_path is not None and exclude_path(path):
                ignored += 1
                continue
            try:
                st = path.lstat()
            except OSError:
                ignored += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                ignored += 1
                continue
            try:
                rel = path.relative_to(rel_root).as_posix()
            except ValueError:
                rel = name
            entries.append((str(path), name, rel, int(st.st_size), int(st.st_mtime)))
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


def _drop_persisted_index(root: Path) -> None:
    for path in [*_sqlite_paths(root), _index_manifest_path(root)]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


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


def _persist(ri: RootIndex, skip_dirs: set[str], exclude_signature: str = "", *, force: bool = False) -> None:
    estimated_bytes = _estimated_sqlite_bytes(ri.entries)
    persistence_allowed = (
        ri.persist_enabled
        and not ri.too_large
        and len(ri.entries) <= ri.persist_max_files
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
    if ri.persisted and not force and now - ri.last_persisted_at < PERSIST_DEBOUNCE_SECONDS:
        ri.persist_pending = True
        return
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        signature = _disk_skip_signature(ri.root, skip_dirs, exclude_signature)
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
        bytes_written = max(0, after_size - before_size) if entries_unchanged else after_size
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
    if raw.get("version") != INDEX_FORMAT_VERSION or raw.get("storage") != "sqlite" or raw.get("skip_signature") != _disk_skip_signature(root, skip_dirs, exclude_signature):
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
        and metadata.get("skip_signature") == _disk_skip_signature(root, skip_dirs, exclude_signature)
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


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _coalesced_paths(paths: set[Path]) -> list[Path]:
    result: list[Path] = []
    for path in sorted(paths, key=lambda item: (len(item.parts), str(item))):
        if any(_path_is_within(path, parent) for parent in result):
            continue
        result.append(path)
    return result


def _refresh_dirty_subtrees(
    ri: RootIndex,
    dirty_paths: list[Path],
    skip_dirs: set[str],
    exclude_path: Callable[[Path], bool] | None,
) -> tuple[list[IndexEntry], bool, int, int]:
    with ri.lock:
        previous = list(ri.entries)
        previously_truncated = ri.truncated
    retained = [
        entry
        for entry in previous
        if not any(_path_is_within(Path(entry[0]), dirty) for dirty in dirty_paths)
    ]
    refreshed: list[IndexEntry] = []
    ignored = 0
    truncated = previously_truncated
    for dirty in dirty_paths:
        remaining = ri.max_files - len(retained) - len(refreshed)
        if remaining <= 0:
            truncated = True
            break
        if exclude_path is not None and exclude_path(dirty):
            ignored += 1
            continue
        if dirty.name in skip_dirs:
            ignored += 1
            continue
        if dirty.is_dir():
            entries, subtree_truncated, subtree_ignored = _walk_root_with_metrics(
                dirty,
                skip_dirs,
                ri.stop_event,
                exclude_path,
                max_files=remaining,
                relative_root=ri.root,
            )
            refreshed.extend(entries)
            truncated = truncated or subtree_truncated
            ignored += subtree_ignored
            continue
        try:
            st = dirty.lstat()
        except OSError:
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


def mark_path_dirty(path: Path) -> list[Path]:
    """Coalesce one filesystem invalidation into every containing active index."""
    target = path.expanduser().resolve(strict=False)
    roots = indexed_ancestor_roots(target)
    with _REGISTRY_LOCK:
        indexes = [_REGISTRY.get(str(root)) for root in roots]
    for ri in indexes:
        if ri is None:
            continue
        with ri.lock:
            ri.dirty_paths.add(target)
            ri.dirty_paths = set(_coalesced_paths(ri.dirty_paths))
    return roots


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
        with ri.lock:
            should_flush = ri.persist_pending and monotonic_now - ri.last_persisted_at >= PERSIST_DEBOUNCE_SECONDS
            freshness_anchor = ri.last_full_build_at or ri.built_at
            should_refresh = bool(ri.dirty_paths) or (ri.ready and ri.refresh_seconds > 0 and wall_now - freshness_anchor >= ri.refresh_seconds)
            building = ri.building
            skip_dirs = set(ri.skip_dirs)
            exclude_path = ri.exclude_path
            exclude_signature = ri.exclude_signature
        if should_flush and not building:
            _persist(ri, skip_dirs, exclude_signature, force=True)
        if should_refresh and not building:
            _start_build(ri, skip_dirs, exclude_path=exclude_path, exclude_signature=exclude_signature)
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
    return {
        "root_count": len(roots),
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
    effective_exclude_path = _build_exclude_path(exclude_path)
    with ri.lock:
        dirty_paths = _coalesced_paths(set(ri.dirty_paths)) if ri.ready else []
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
        if not dirty_paths and not ri.ready and disk is not None:
            with ri.lock:
                ri.entries, ri.built_at, ri.truncated = disk
                ri.last_full_build_at = ri.built_at
                ri.too_large = ri.truncated
                ri.persisted = True
                ri.last_persisted_at = time.monotonic()
                ri.cache_bytes = _sqlite_storage_size(ri.root)
                ri.signature = expected_signature
                ri.disk_entry_count = len(ri.entries)
                ri.disk_metadata_ready = True
                ri.ready = True
                ri.building = False
            return
        if dirty_paths:
            entries, truncated, scanned_entries, ignored_entries = _refresh_dirty_subtrees(ri, dirty_paths, skip_dirs, effective_exclude_path)
            build_kind = "incremental"
        else:
            entries, truncated, ignored_entries = _walk_root_with_metrics(
                ri.root,
                skip_dirs,
                ri.stop_event,
                effective_exclude_path,
                max_files=ri.max_files,
            )
            scanned_entries = len(entries)
            build_kind = "full"
        if ri.stop_event.is_set():
            with ri.lock:
                ri.building = False
            return
        with ri.lock:
            ri.entries = entries
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
            ri.ready = True
        _persist(ri, skip_dirs, exclude_signature)
        with ri.lock:
            ri.disk_metadata_ready = ri.persisted
            ri.building = False
        # C11: a fresh build supersedes any prior unindex, so clear the tombstone.
        _clear_tombstone(ri.root)
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
            "compute_ms": round(ri.build_duration_ms, 3),
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
    *,
    max_files: int | None = None,
    refresh_seconds: float = INDEX_TTL_SECONDS,
    persist_enabled: bool = True,
    persist_max_files: int = MAX_PERSISTED_INDEX_FILES,
    persist_max_bytes: int = MAX_PERSISTED_INDEX_BYTES,
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
            ri.max_files = max(1, int(MAX_INDEX_FILES if max_files is None else max_files))
            ri.refresh_seconds = max(0.0, float(refresh_seconds))
            ri.persist_enabled = bool(persist_enabled)
            ri.persist_max_files = max(1, int(persist_max_files))
            ri.persist_max_bytes = max(1, int(persist_max_bytes))
            ri.skip_dirs = set(skip_dirs)
            ri.exclude_path = exclude_path
            ri.exclude_signature = exclude_signature
            if background_owner_can_build() and ri.persist_enabled:
                disk = _load_disk(root, skip_dirs, exclude_signature)
                if disk is not None:
                    ri.entries, ri.built_at, ri.truncated = disk
                    ri.last_full_build_at = ri.built_at
                    ri.too_large = ri.truncated
                    ri.persisted = True
                    ri.last_persisted_at = time.monotonic()
                    ri.cache_bytes = _sqlite_storage_size(root)
                    ri.disk_entry_count = len(ri.entries)
                    ri.disk_metadata_ready = True
                    ri.signature = expected_signature
                    ri.ready = True
            elif not background_owner_can_build() and ri.persist_enabled:
                metadata = _load_disk_metadata(root, skip_dirs, exclude_signature)
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
            metadata = _load_disk_metadata(root, skip_dirs, exclude_signature)
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
    if background_owner_can_build() and not ri.ready:
        _start_build(ri, skip_dirs, exclude_path=exclude_path, exclude_signature=exclude_signature)
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
) -> RootIndex:
    """Synchronously build (or rebuild) the index for root. Used at warm-up and in tests."""
    key = str(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
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
    _drop_persisted_index(root)
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

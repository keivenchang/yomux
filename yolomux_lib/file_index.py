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
import stat
import threading
import time
from pathlib import Path
from typing import Any
from typing import Callable

from .common import STATE_DIR


INDEX_DIR = STATE_DIR / "search_index"
# Upper bound on indexed files per root, to bound memory on pathological trees.
MAX_INDEX_FILES = 400_000
# Serve from the index immediately; rebuild in the background once it is older
# than this (stale-while-revalidate), which also prunes deleted files.
INDEX_TTL_SECONDS = 120.0
# C11: bump when the on-disk JSON shape changes so old/incompatible indexes rebuild for a clear reason.
INDEX_FORMAT_VERSION = 1


def _skip_signature(skip_dirs: set[str]) -> str:
    # C11: the set of skipped directories is part of what an index means; if it changes, the cached
    # index no longer matches the requested coverage and must rebuild.
    return ",".join(sorted(skip_dirs))

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
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()


_REGISTRY: dict[str, RootIndex] = {}
_REGISTRY_LOCK = threading.Lock()


def _index_disk_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return INDEX_DIR / f"{digest}.json"


def _build_lock_path(root: Path) -> Path:
    # C11: a per-root file lock so two server processes (e.g. :7777 and :7778 sharing STATE_DIR) don't
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


def walk_root(root: Path, skip_dirs: set[str], stop_event: threading.Event | None = None) -> tuple[list[IndexEntry], bool]:
    """Collect every regular file under root, skipping skip_dirs. Cancellable."""
    entries: list[IndexEntry] = []
    truncated = False
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        if stop_event is not None and stop_event.is_set():
            return entries, True
        dirs[:] = sorted((name for name in dirs if name not in skip_dirs), key=str.lower)
        current_path = Path(current)
        for name in files:
            if len(entries) >= MAX_INDEX_FILES:
                return entries, True
            path = current_path / name
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


def _persist(ri: RootIndex, skip_dirs: set[str]) -> None:
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_FORMAT_VERSION,
            "skip_signature": _skip_signature(skip_dirs),
            "root": str(ri.root),
            "built_at": ri.built_at,
            "truncated": ri.truncated,
            "entries": ri.entries,
        }
        tmp = _index_disk_path(ri.root).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_index_disk_path(ri.root))
    except OSError:
        pass


def _load_disk(root: Path, skip_dirs: set[str]) -> tuple[list[IndexEntry], float, bool] | None:
    try:
        raw = json.loads(_index_disk_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("root") != str(root):
        return None
    # C11: reject indexes from an older format or a different skip-dir set so they rebuild cleanly.
    if raw.get("version") != INDEX_FORMAT_VERSION or raw.get("skip_signature") != _skip_signature(skip_dirs):
        return None
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        return None
    entries = [tuple(item) for item in entries_raw if isinstance(item, list) and len(item) == 5]
    return entries, float(raw.get("built_at") or 0.0), bool(raw.get("truncated"))


def _run_build(ri: RootIndex, skip_dirs: set[str]) -> None:
    # C11: take a cross-process lock so a second server process does not duplicate the walk. If another
    # process holds it, leave whatever stale-but-ready disk copy we already loaded in place and bail.
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
        disk = _load_disk(ri.root, skip_dirs)
        if disk is not None and (time.time() - disk[1]) <= INDEX_TTL_SECONDS:
            with ri.lock:
                ri.entries, ri.built_at, ri.truncated = disk
                ri.ready = True
                ri.building = False
            return
        entries, truncated = walk_root(ri.root, skip_dirs, ri.stop_event)
        if ri.stop_event.is_set():
            with ri.lock:
                ri.building = False
            return
        with ri.lock:
            ri.entries = entries
            ri.truncated = truncated
            ri.built_at = time.time()
            ri.ready = True
            ri.building = False
        _persist(ri, skip_dirs)
        # C11: a fresh build supersedes any prior unindex, so clear the tombstone.
        _clear_tombstone(ri.root)
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


def _start_build(ri: RootIndex, skip_dirs: set[str]) -> None:
    with ri.lock:
        if ri.building:
            return
        ri.building = True
        ri.stop_event = threading.Event()
        thread = threading.Thread(target=_run_build, args=(ri, set(skip_dirs)), name=f"file-index-{ri.root.name}", daemon=True)
        ri.thread = thread
    thread.start()


def ensure_index(root: Path, skip_dirs: set[str]) -> RootIndex:
    """Return the RootIndex for root, seeding from disk and kicking off a
    background (re)build when missing or stale. May return a not-yet-ready index."""
    key = str(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
            disk = _load_disk(root, skip_dirs)
            if disk is not None:
                ri.entries, ri.built_at, ri.truncated = disk
                ri.ready = True
    # C11: if another process unindexed this root after our copy was built, drop the stale in-memory
    # index so we stop serving deleted-file results (a later explicit access rebuilds and clears the tomb).
    tomb = _tombstone_time(root)
    if ri.ready and tomb and tomb > ri.built_at:
        with ri.lock:
            ri.entries = []
            ri.ready = False
            ri.built_at = 0.0
    if not ri.ready or (time.time() - ri.built_at) > INDEX_TTL_SECONDS:
        _start_build(ri, skip_dirs)
    return ri


def build_now(root: Path, skip_dirs: set[str]) -> RootIndex:
    """Synchronously build (or rebuild) the index for root. Used at warm-up and in tests."""
    key = str(root)
    with _REGISTRY_LOCK:
        ri = _REGISTRY.get(key)
        if ri is None:
            ri = RootIndex(root)
            _REGISTRY[key] = ri
    ri.stop_event = threading.Event()
    _run_build(ri, set(skip_dirs))
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
    try:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        _tombstone_path(root).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

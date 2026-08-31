"""File search and persistent index entry points."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ..search import file_index
from ..infra.refresh_outcome import RefreshOutcome
from ..common import is_generated_upload_name
from ..settings import DEFAULT_INDEX_EXCLUDE_DIR_NAMES
from ..settings import settings_payload
from . import exclusions
from . import git_ops
from . import paths
from .errors import FilesystemError
from .listing import _directory_is_repo

SEARCH_SKIP_DIRS = set(DEFAULT_INDEX_EXCLUDE_DIR_NAMES)
SEARCH_SECRET_EXCLUDE_SIGNATURE = "fs-secret-v2"
# Re-exported from the shared exclusion owner; the prefixes are one definition, not two.
INDEX_EXCLUDE_GLOB_PREFIX = exclusions.INDEX_EXCLUDE_GLOB_PREFIX
INDEX_EXCLUDE_REGEX_PREFIX = exclusions.INDEX_EXCLUDE_REGEX_PREFIX
MAX_SEARCH_DIRS = 20_000
MAX_SEARCH_FILES = 50_000
MAX_SEARCH_LIMIT = 2_000
LOGGER = logging.getLogger(__name__)
_LOGGED_BLOCKED_REINDEX_PATHS: set[str] = set()
# Item 6 visible-path (Finder/Differ) promotion debounce state. Bounded and pruned so a long browsing
# session cannot grow it without limit.
_VISIBLE_PROMOTE_DEBOUNCE_SECONDS = 2.0
_VISIBLE_PROMOTE_MAX_TRACKED = 512
_VISIBLE_PROMOTE_LOCK = threading.Lock()
_VISIBLE_PROMOTE_LAST: dict[str, float] = {}


def _index_path_is_excluded(
    root: Path,
    path: Path,
    skip_dirs: set[str],
    exclude_path: Any,
) -> bool:
    """Apply one index root's complete exclusion policy to an event path.

    The rule itself lives in the shared exclusion owner so the index and the
    watch daemon cannot drift apart again; only the index's root scoping is
    applied here.
    """
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        return True
    try:
        resolved.relative_to(root)
    except ValueError:
        return True
    return exclusions.path_exclusion_verdict(
        path,
        skip_dirs=skip_dirs,
        resolved=resolved,
        exclude_path=exclude_path,
        relative_to=root,
    ).excluded


def _search_index_policy(root: Path) -> dict[str, Any]:
    settings = settings_payload().get("settings", {}).get("file_explorer", {})
    # The parsing, matching and directory-name rules live in the shared exclusion owner so the
    # index, the watch daemon and Differ cannot drift apart again. Only the index's own knobs
    # (walk/persist ceilings) and its lexical secret shortcut stay here.
    compiled = exclusions.ExclusionPolicy.from_settings(settings, DEFAULT_INDEX_EXCLUDE_DIR_NAMES).compiled_for(root)
    skip_dirs = set(compiled.skip_dirs)

    def exclude_path(path: Path) -> bool:
        # The index walk does not follow symlinks, so retain the lexical secret
        # policy without resolving every candidate in a large repository.
        if paths._path_is_secret(path, resolve=False):
            return True
        return compiled.matches_configured_rule(path)

    max_files = int(settings.get("index_max_files", file_index.MAX_INDEX_FILES))
    persist_max_files = int(settings.get("index_persist_max_files", file_index.MAX_PERSISTED_INDEX_FILES))
    persist_max_bytes = int(settings.get("index_persist_max_mb", file_index.MAX_PERSISTED_INDEX_BYTES // (1024 * 1024))) * 1024 * 1024
    refresh_seconds = float(settings.get("index_refresh_seconds", file_index.INDEX_TTL_SECONDS))
    rule_values = compiled.rule_values
    coverage_policy = {
        "excludes": rule_values,
        "skip_dirs": sorted(skip_dirs),
        "max_files": max_files,
    }
    policy_signature = SEARCH_SECRET_EXCLUDE_SIGNATURE
    if coverage_policy != {"excludes": [], "skip_dirs": sorted(DEFAULT_INDEX_EXCLUDE_DIR_NAMES), "max_files": file_index.MAX_INDEX_FILES}:
        digest = hashlib.sha256(json.dumps(coverage_policy, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        policy_signature = f"{SEARCH_SECRET_EXCLUDE_SIGNATURE}:{digest}"
    return {
        "skip_dirs": skip_dirs,
        "exclude_path": exclude_path,
        "exclude_signature": policy_signature,
        "max_files": max_files,
        "refresh_seconds": refresh_seconds,
        "persist_enabled": bool(settings.get("index_persist", True)),
        "persist_max_files": persist_max_files,
        "persist_max_bytes": persist_max_bytes,
        "excluded_paths": rule_values,
    }


def _ensure_search_index(
    root: Path,
    *,
    operation: str = "",
    root_fd: int | None = None,
) -> tuple[file_index.RootIndex, dict[str, Any]]:
    policy = _search_index_policy(root)
    if root_fd is not None:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise paths.FilesystemError.not_directory(root)
        index = file_index.ensure_index(
            root,
            policy["skip_dirs"],
            exclude_path=policy["exclude_path"],
            exclude_signature=policy["exclude_signature"],
            max_files=policy["max_files"],
            refresh_seconds=policy["refresh_seconds"],
            persist_enabled=policy["persist_enabled"],
            persist_max_files=policy["persist_max_files"],
            persist_max_bytes=policy["persist_max_bytes"],
            root_fd=root_fd,
            operation=operation,
        )
        return index, policy
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(root), flags=directory_flags, operation=operation) as handle:
        index = file_index.ensure_index(
            handle.resolved,
            policy["skip_dirs"],
            exclude_path=policy["exclude_path"],
            exclude_signature=policy["exclude_signature"],
            max_files=policy["max_files"],
            refresh_seconds=policy["refresh_seconds"],
            persist_enabled=policy["persist_enabled"],
            persist_max_files=policy["persist_max_files"],
            persist_max_bytes=policy["persist_max_bytes"],
            root_fd=handle.descriptor,
            operation=operation,
        )
    return index, policy


def _snapshot_freshness(
    index: file_index.RootIndex | None,
    root: Path,
    index_policy: dict[str, Any],
) -> file_index.SnapshotFreshness:
    """Adapt this module's policy dict to the one freshness owner in `file_index`.

    Every `index_state`, `index_coverage`, `ready_elsewhere` and
    `refreshing_elsewhere` value below is derived from the record this returns.
    No freshness rule is re-implemented here; a second copy of that judgement is
    exactly how a snapshot came to be reported ready while its producer was dead.
    """
    return file_index.index_freshness(
        index,
        root,
        index_policy["skip_dirs"],
        index_policy["exclude_signature"],
    )


def _progressive_payload_fields(root: Path) -> dict[str, Any]:
    """The measured breadth-first coverage attached to a full-tree Quick Open response (item 5).

    Reuses the ONE coverage owner (`file_index.read_index_coverage`) so the search payload, the
    Daemons roster, and `/api/fs/index-status` cannot grow divergent copies of the same metadata.
    Nested under `progressive_coverage` so it never collides with the freshness fields, plus a
    compact `snapshot_state`/`refresh_pending` the palette keys on to distinguish ready/current,
    ready/stale, and partial/warming WITHOUT hiding already-cached matches. Empty when no snapshot
    exists yet.
    """
    coverage = file_index.read_index_coverage(root)
    if not coverage:
        return {}
    published_depth = int(coverage.get("published_depth") or 0)
    frontier_size = int(coverage.get("frontier_size") or 0)
    full = bool(coverage.get("full_coverage"))
    if full:
        snapshot_state = "current"
    elif published_depth > 0:
        snapshot_state = "partial"
    else:
        snapshot_state = "warming"
    return {
        "progressive_coverage": coverage,
        "snapshot_state": snapshot_state,
        "refresh_pending": frontier_size > 0,
    }


def _promote_user_visible_scope(root: Path) -> None:
    """Asynchronously promote a not-yet-covered Quick Open scope's frontier (item 5).

    Fire-and-forget through `file_index.request_user_visible_promotion`: it dispatches on a daemon
    thread and debounces per root, so a partial/warming/stale query bumps that root's frontier
    priority without the query waiting on `batchd`, the crawler, or the RPC, and without launching a
    second crawl. Only call this when coverage is incomplete.
    """
    file_index.request_user_visible_promotion(str(root))


def promote_visible_path(raw_path: str) -> list[str]:
    """Item 6, visible Finder/Differ root: promote the frontier of every indexed root over ``path``.

    A directory a user is actively viewing in Finder, or a repo they opened in the Differ, is
    concrete visibility evidence that its layer should be covered soon. This routes that evidence to
    the SAME user-visible-demand promotion owner Quick Open uses (`request_user_visible_promotion`),
    which dispatches on a daemon thread and debounces per root -- so a directory listing or diff on
    the interactive worker never blocks on the crawler or the RPC, and never launches a second crawl.
    Returns the roots for which a promotion was dispatched (empty when none was, e.g. debounced or the
    path is under no indexed root).
    """
    try:
        target = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return []
    # Path-level debounce BEFORE the ancestor-root glob: a Finder is a stream of listings, so this
    # keeps the interactive worker off the filesystem scan except at most once per window per path.
    now = time.monotonic()
    with _VISIBLE_PROMOTE_LOCK:
        last = _VISIBLE_PROMOTE_LAST.get(str(target), 0.0)
        if now - last < _VISIBLE_PROMOTE_DEBOUNCE_SECONDS:
            return []
        _VISIBLE_PROMOTE_LAST[str(target)] = now
        if len(_VISIBLE_PROMOTE_LAST) > _VISIBLE_PROMOTE_MAX_TRACKED:
            for stale_key in [key for key, seen in _VISIBLE_PROMOTE_LAST.items() if now - seen >= _VISIBLE_PROMOTE_DEBOUNCE_SECONDS]:
                _VISIBLE_PROMOTE_LAST.pop(stale_key, None)
    dispatched: list[str] = []
    for root in file_index.indexed_ancestor_roots(target):
        if file_index.request_user_visible_promotion(str(root), str(target)):
            dispatched.append(str(root))
    return dispatched


def _fuzzy_subsequence_match(query: str, text: str) -> bool:
    return _fuzzy_subsequence_span(query, text) is not None


def _compact_search_text(value: str) -> str:
    return "".join(str(value or "").lower().split())


def _alnum_search_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _doit_search_token(value: str) -> str:
    needle = _alnum_search_text(value)
    return needle if needle.startswith("doit") and len(needle) >= 4 else ""


def _fuzzy_subsequence_span(query: str, text: str) -> int | None:
    needle = "".join(str(query or "").lower().split())
    if not needle:
        return 0
    position = 0
    haystack = str(text or "").lower()
    start = -1
    end = -1
    for char in needle:
        index = haystack.find(char, position)
        if index < 0:
            return None
        if start < 0:
            start = index
        end = index
        position = index + 1
    return end - start + 1


def _search_token_name_rank(token: str, name: str) -> int | None:
    needle = _compact_search_text(token)
    if not needle:
        return 0
    basename = _compact_search_text(name)
    stem = _compact_search_text(name.rsplit(".", 1)[0] if "." in name else name)
    doit_needle = _doit_search_token(token)
    if doit_needle:
        basename_alnum = _alnum_search_text(name)
        stem_alnum = _alnum_search_text(name.rsplit(".", 1)[0] if "." in name else name)
        if doit_needle in (stem_alnum, basename_alnum):
            return 0
        if stem_alnum.startswith(doit_needle) or basename_alnum.startswith(doit_needle):
            return 10
        if stem_alnum.find(doit_needle) >= 0:
            return 20 + stem_alnum.find(doit_needle)
        if basename_alnum.find(doit_needle) >= 0:
            return 30 + basename_alnum.find(doit_needle)
        return None

    if needle in (stem, basename):
        return 0
    if stem.startswith(needle) or basename.startswith(needle):
        return 10
    index = stem.find(needle)
    if index >= 0:
        return 20 + index
    index = basename.find(needle)
    if index >= 0:
        return 30 + index
    span = _fuzzy_subsequence_span(needle, stem)
    if span is not None:
        return 50 + span
    span = _fuzzy_subsequence_span(needle, basename)
    if span is not None:
        return 60 + span
    return None


def _search_token_rank(token: str, path: Path, rel: str) -> int | None:
    name_rank = _search_token_name_rank(token, path.name)
    if name_rank is not None:
        return name_rank
    needle = _compact_search_text(token)
    rel_text = _compact_search_text(rel)
    doit_needle = _doit_search_token(token)
    if doit_needle:
        index = _alnum_search_text(rel).find(doit_needle)
        return 90 + index if index >= 0 else None
    index = rel_text.find(needle)
    if index >= 0:
        return 90 + index
    span = _fuzzy_subsequence_span(needle, rel_text)
    return 130 + rel.count("/") * 4 + span if span is not None else None


def _direct_name_sort_key(name: str, tokens: list[str]) -> tuple[int, int, int, int, int, str] | None:
    """Rank one direct child by name without constructing a Path for every sibling."""
    ranks = [_search_token_name_rank(token, name) for token in tokens]
    if any(rank is None for rank in ranks):
        return None
    if not ranks:
        return (0, 0, 0, 0, len(name), name.lower())
    return (min(ranks), sum(ranks), -sum(rank < 90 for rank in ranks), 0, len(name), name.lower())


def _search_entry_sort_key(path: Path, rel: str, tokens: list[str]) -> tuple[int, int, int, int, int, str] | None:
    ranks = [_search_token_rank(token, path, rel) for token in tokens]
    if any(rank is None for rank in ranks):
        return None
    if not ranks:
        return (0, 0, 0, rel.count("/"), len(rel), rel.lower())
    basename_hits = sum(1 for rank in ranks if rank is not None and rank < 90)
    return (min(ranks), sum(ranks), -basename_hits, rel.count("/"), len(rel), rel.lower())


def _search_limit(raw_limit: int | str | None) -> int:
    try:
        limit = int(raw_limit or 400)
    except (TypeError, ValueError):
        limit = 400
    return max(1, min(limit, MAX_SEARCH_LIMIT))


def _search_file_entry(
    root: Path,
    path: Path,
    tokens: list[str],
    *,
    stat_result: os.stat_result,
    display_path: Path | None = None,
) -> dict[str, Any] | None:
    st = stat_result
    if not stat.S_ISREG(st.st_mode):
        return None
    result_path = display_path or path
    try:
        rel = result_path.relative_to(root).as_posix()
    except ValueError:
        rel = result_path.name
    sort_key = _search_entry_sort_key(result_path, rel, tokens)
    if sort_key is None:
        return None
    return {
        "name": result_path.name,
        "path": str(result_path),
        "relative_path": rel,
        "kind": "file",
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
        "uploaded": is_generated_upload_name(result_path),
        "_sort_key": sort_key,
        **paths._physical_file_identity(result_path, resolved=result_path, stat_result=st),
    }


def _direct_search_entry(
    root: Path,
    path: Path,
    *,
    kind: str,
    sort_key: tuple[int, int, int, int, int, str],
) -> dict[str, Any] | None:
    """Build a name-only direct-search row without reading child metadata."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    return {
        "name": path.name,
        "path": str(path),
        "relative_path": rel,
        "kind": kind,
        "_sort_key": sort_key,
    }


def _minimal_search_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry[key] for key in ("name", "path", "relative_path", "kind") if key in entry}


def _annotate_search_dedupe_fields(
    entry: dict[str, Any],
    *,
    root: Path,
    root_descriptor: int,
) -> bool:
    """Revalidate one indexed hit and replace every live field from its descriptor.

    A persisted snapshot may legitimately contain a file that has since been deleted.  Keep that
    stale row useful, but strip fields that would otherwise claim metadata from a live generation.
    Policy failures and namespace replacements still reject the complete row.
    """
    path_str = entry.get("path")
    if not isinstance(path_str, str):
        return False
    generation_fields = ("realpath", "size", "mtime", "file_id", "file_identity")
    try:
        requested = Path(path_str)
        relative = requested.relative_to(root)
        with paths.safe_descendant(
            root_descriptor,
            root,
            root,
            relative,
            flags=paths.metadata_descriptor_flags(),
            operation="search.annotate",
        ) as handle:
            if not stat.S_ISREG(handle.stat_result.st_mode):
                raise paths.FilesystemError.changed_on_disk(handle.requested)
            relative_path = handle.requested.relative_to(root).as_posix()
            identity = paths._physical_file_identity(
                handle.requested,
                resolved=handle.resolved,
                stat_result=handle.stat_result,
            )
    except ValueError:
        return False
    except paths.FilesystemError as error:
        if error.message_key != "common.pathNotFound":
            return False
        for field in generation_fields:
            entry.pop(field, None)
        return True
    entry.update({
        "name": handle.requested.name,
        "path": str(handle.requested),
        "relative_path": relative_path,
        "kind": "file",
        "realpath": str(handle.resolved),
        "size": int(handle.stat_result.st_size),
        "mtime": int(handle.stat_result.st_mtime),
        "uploaded": is_generated_upload_name(handle.requested),
        **identity,
    })
    return True


def _search_full_tree(
    root: Path,
    search_root: Path,
    tokens: list[str],
    results: list[dict[str, Any]],
    skip_dirs: set[str] | None = None,
    *,
    display_search_root: Path | None = None,
    resolved_search_root: Path,
    search_descriptor: int,
) -> tuple[int, int, bool]:
    effective_skip_dirs = SEARCH_SKIP_DIRS if skip_dirs is None else skip_dirs
    visited_dirs = 0
    visited_files = 0
    truncated = False
    result_search_root = display_search_root or root

    def include_directory(relative: Path) -> bool:
        return relative.name not in effective_skip_dirs and not paths._path_is_secret(result_search_root / relative)

    walker = paths.walk_directory(
        search_descriptor,
        include_directory=include_directory,
        operation="search_files",
        requested_root=result_search_root,
        resolved_root=resolved_search_root,
    )
    with contextlib.closing(walker):
        for relative_directory, _directory_fd, _dirnames, file_rows in walker:
            visited_dirs += 1
            if visited_dirs > MAX_SEARCH_DIRS:
                truncated = True
                break
            for name, file_stat in file_rows:
                visited_files += 1
                if visited_files > MAX_SEARCH_FILES:
                    truncated = True
                    break
                relative = relative_directory / name
                display_path = result_search_root / relative
                if paths._path_is_secret(display_path):
                    continue
                entry = _search_file_entry(
                    root,
                    relative,
                    tokens,
                    display_path=display_path,
                    stat_result=file_stat,
                )
                if entry is not None:
                    results.append(entry)
            if visited_files > MAX_SEARCH_FILES:
                break
    return visited_dirs, visited_files, truncated


def _make_search_match(tokens: list[str]):
    """One shared entry-projection/scoring builder for BOTH the snapshot and delta search paths.

    The snapshot walk and the cursor-delta read each need the same `match(path, name, rel)` callable
    that scores against the query tokens and projects the search-result row; keeping it in one owner
    stops the two paths from growing divergent copies of the sort-key + row shape."""
    def _match(path_str: str, name: str, rel: str) -> dict[str, Any] | None:
        sort_key = _search_entry_sort_key(Path(path_str), rel, tokens)
        if sort_key is None:
            return None
        return {
            "name": name,
            "path": path_str,
            "relative_path": rel,
            "kind": "file",
            "uploaded": is_generated_upload_name(Path(path_str)),
            "_sort_key": sort_key,
        }
    return _match


def initial_delta_cursor(root: Path, *, root_fd: int | None = None) -> str | None:
    """The baseline cursor for a root's committed snapshot, or ``None`` when nothing is indexed yet.

    A first Quick Open request serves the immediate snapshot and carries this cursor; the client then
    asks for committed deltas since it (step 3). Reuses the ONE search policy so the cursor is pinned to
    the exact policy identity the delta reads are validated against."""
    policy = _search_index_policy(root)
    expected_root_identity = (
        file_index.parse_root_identity(file_index.root_identity(os.fstat(root_fd)))
        if root_fd is not None
        else None
    )
    return file_index.current_delta_cursor(
        root,
        policy["skip_dirs"],
        policy["exclude_signature"],
        expected_root_identity=expected_root_identity,
    )


def _search_delta_payload(
    root: Path,
    query: str,
    max_results: int,
    cursor: str,
    *,
    expected_root_identity: tuple[int, int],
    root_descriptor: int,
) -> dict[str, Any]:
    """Serve one bounded page of committed journal deltas since ``cursor`` for ``root`` (step 3).

    Reuses the ONE search policy, the same ranking/`_match` construction the snapshot read uses, the
    shared exclusion + safe-root containment verdict, and the realpath dedupe annotation -- so a
    streamed match is filtered and annotated IDENTICALLY to a snapshot match, and a repointed symlink
    can never leak a blocked realpath through the delta path. Returns ``{changes, cursor, more,
    coverage}`` or a typed ``{rebase_required, reason}`` the client repairs with one full snapshot."""
    index_policy = _search_index_policy(root)
    skip_dirs = index_policy["skip_dirs"]
    exclude_path = index_policy["exclude_path"]
    tokens = [token for token in str(query or "").split() if token]

    _match = _make_search_match(tokens)

    result = file_index.search_disk_index_delta(
        root,
        skip_dirs,
        index_policy["exclude_signature"],
        _match,
        cursor,
        expected_root_identity=expected_root_identity,
    )
    if isinstance(result, file_index.DeltaRebaseRequired):
        return {
            "root": str(root),
            "root_realpath": str(root),
            "query": str(query or ""),
            "limit": max_results,
            "rebase_required": True,
            "reason": result.reason,
        }
    changes: list[dict[str, Any]] = []
    for change in result.changes:
        change.pop("_sort_key", None)
        path_text = str(change.get("path") or "")
        # A row whose path escaped its root or is now excluded must not expose even its cached name or
        # relative path. Upserts additionally have to revalidate and replace every live metadata field.
        if not path_text or _index_path_is_excluded(root, Path(path_text), skip_dirs, exclude_path):
            continue
        if change.get("operation") == file_index.JOURNAL_OP_UPSERT and not _annotate_search_dedupe_fields(
            change,
            root=root,
            root_descriptor=root_descriptor,
        ):
            continue
        changes.append(change)
    return {
        "root": str(root),
        "root_realpath": str(root),
        "query": str(query or ""),
        "limit": max_results,
        "changes": changes,
        "cursor": result.cursor,
        "more": bool(result.more),
        "coverage": result.coverage,
    }


def _search_files_from_safe_root(
    raw_root: str,
    query: str = "",
    limit: int | str | None = 400,
    recursive: bool = False,
    *,
    access_root: Path,
    access_descriptor: int,
    inside_repo: bool = False,
    cursor: str | None = None,
    direct_only: bool = False,
    indexed_only: bool = False,
    minimal: bool = False,
) -> dict[str, Any]:
    root = Path(raw_root)
    scan_root = access_root
    if not stat.S_ISDIR(os.fstat(access_descriptor).st_mode):
        raise paths.FilesystemError.not_directory(root)
    max_results = _search_limit(limit)
    if cursor:
        # Delta mode (step 3): the caller already holds a snapshot and a cursor. Serve committed
        # journal deltas since it, WITHOUT traversing -- the safe-root containment was already
        # established by `search_files` opening the authorized root above.
        return _search_delta_payload(
            root,
            str(query or ""),
            max_results,
            cursor,
            expected_root_identity=file_index.parse_root_identity(
                file_index.root_identity(os.fstat(access_descriptor))
            ),
            root_descriptor=access_descriptor,
        )
    tokens = [token for token in str(query or "").split() if token]
    index_policy = _search_index_policy(root)
    skip_dirs = index_policy["skip_dirs"]
    full_tree = bool(recursive) or (inside_repo and not direct_only)
    if full_tree:
        # Accelerate full-tree quick-open with the persistent index: it covers the
        # whole tree (no 20k/50k walk cap) and needs no per-query walk. Warm/refresh
        # it in the background; until it is ready we fall back to the live walk below
        # (stale-while-revalidate), so search never blocks on indexing.
        index, index_policy = _ensure_search_index(
            root,
            operation="search_files",
            root_fd=access_descriptor,
        )
        skip_dirs = index_policy["skip_dirs"]
        authorized_root_identity = file_index.parse_root_identity(
            file_index.root_identity(os.fstat(access_descriptor))
        )
        can_build_index = file_index.background_owner_can_build()
        if tokens:
            _match = _make_search_match(tokens)

            indexed_payload_state = ""
            indexed: tuple[list[dict[str, Any]], bool] | None = None
            if index.ready:
                if minimal:
                    indexed = file_index.search_disk_index(
                        root,
                        skip_dirs,
                        index_policy["exclude_signature"],
                        _match,
                        max_results,
                        [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                        expected_root_identity=authorized_root_identity,
                        include_metadata=False,
                    )
                if indexed is None and not minimal:
                    indexed = file_index.search_index(index, _match, max_results, include_metadata=not minimal)
            elif not can_build_index and index.disk_metadata_ready:
                indexed = file_index.search_disk_index(
                    root,
                    skip_dirs,
                    index_policy["exclude_signature"],
                    _match,
                    max_results,
                    [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                    expected_root_identity=authorized_root_identity,
                    include_metadata=not minimal,
                )
                indexed_payload_state = "follower-ready" if indexed is not None else ""
            if indexed is not None:
                indexed_results, indexed_truncated = indexed
                admitted_results = []
                for entry in indexed_results:
                    entry.pop("_sort_key", None)
                    # Annotate the (capped) results with realpath + size so the client can dedupe symlink
                    # overlaps and content-mirror copies. Bounded to <= max_results, so the stat is cheap.
                    if _annotate_search_dedupe_fields(entry, root=root, root_descriptor=access_descriptor):
                        admitted_results.append(_minimal_search_entry(entry) if minimal else entry)
                indexed_results = admitted_results
                freshness = _snapshot_freshness(index, root, index_policy)
                payload = {
                    "root": str(root),
                    "root_realpath": str(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": indexed_truncated,
                    "index_state": "too_large" if index.too_large else "ready",
                    "index_coverage": "partial" if index.too_large else "full",
                    "files": indexed_results,
                    **_progressive_payload_fields(root),
                    **freshness.payload_fields(),
                }
                if indexed_payload_state:
                    # A follower serves the snapshot either way, but may only call it
                    # ready/full when the freshness record vouches for it.
                    payload["index_state"] = "follower-ready" if freshness.authoritative else "follower-stale"
                    if not freshness.authoritative:
                        payload["index_coverage"] = "unverified"
                        # Item 5: a stale/unverified follower read promotes the owner's frontier for
                        # this scope without blocking, so the served matches stay while the owner
                        # advances coverage.
                        _promote_user_visible_scope(root)
                return payload
            if not index.ready and not can_build_index:
                # A follower can always read a persisted snapshot.  Ask the
                # writer only when that snapshot is missing; rolling worktrees
                # can otherwise use different local-RPC framing and turn an
                # exact filename lookup into a socket retry storm.
                fallback_indexed = file_index.search_disk_index(
                    root,
                    skip_dirs,
                    index_policy["exclude_signature"],
                    _match,
                    max_results,
                    [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                    expected_root_identity=authorized_root_identity,
                    include_metadata=not minimal,
                )
                if fallback_indexed is not None:
                    fallback_results, fallback_truncated = fallback_indexed
                    admitted_results = []
                    for entry in fallback_results:
                        entry.pop("_sort_key", None)
                        if _annotate_search_dedupe_fields(entry, root=root, root_descriptor=access_descriptor):
                            admitted_results.append(_minimal_search_entry(entry) if minimal else entry)
                    fallback_results = admitted_results
                    freshness = _snapshot_freshness(index, root, index_policy)
                    return {
                        "root": str(root),
                        "root_realpath": str(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": fallback_truncated,
                        "index_state": "follower-ready" if freshness.authoritative else "follower-stale",
                        "index_coverage": "full" if freshness.authoritative else "unverified",
                        "files": fallback_results,
                        **freshness.payload_fields(),
                    }
                persistent_response = file_index.request_background_index_search({
                    "root": str(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    paths.FS_ACCESS_POLICY_FIELD: paths.active_access_policy().descriptor(),
                    file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index.root_identity(os.fstat(access_descriptor)),
                })
                persistent_payload = persistent_response.get("payload")
                if persistent_response.get("ok") and isinstance(persistent_payload, dict):
                    return persistent_payload
                if persistent_response.get("status") == "unavailable":
                    raise FilesystemError(
                        "search index service unavailable",
                        status=HTTPStatus.FAILED_DEPENDENCY,
                        message_key="common.requestFailed",
                        diagnostic=persistent_response.get("reason"),
                    )
                refresh_result = file_index.request_background_owner_refresh({"root": str(root), "query": str(query or ""), "reason": "search-index-missing"})
                if not refresh_result.get("fallback"):
                    # `fallback` being false does NOT mean an owner took the work: with
                    # no requester wired at all the result is neither accepted nor a
                    # fallback. The freshness record carries the acceptance itself.
                    freshness = _snapshot_freshness(index, root, index_policy)
                    return {
                        "root": str(root),
                        "root_realpath": str(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": False,
                        "files": [],
                        "index_state": "follower",
                        **freshness.payload_fields(),
                    }
            if indexed_only:
                freshness = _snapshot_freshness(index, root, index_policy)
                return {
                    "root": str(root),
                    "root_realpath": str(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": False,
                    "files": [],
                    "index_state": "warming",
                    "index_coverage": "pending",
                    **freshness.payload_fields(),
                }
            if not index.ready and can_build_index:
                # The first query for a large root must not return an empty
                # palette while its dedicated writer warms.  Child indexes are
                # independent persisted snapshots, so use the deepest ones
                # already under this root before reporting the parent warm.
                child_indexes = []
                for candidate in file_index.persisted_index_roots_within(root):
                    if candidate == root:
                        continue
                    child_policy = _search_index_policy(candidate)
                    child_indexes.append((candidate, child_policy))
                child_results: list[dict[str, Any]] = []
                child_truncated = False
                # The breadth-first builder commits this root's layer-1 rows to its own SQLite as
                # soon as the root listing finishes, long before the whole crawl drains. Serve those
                # committed rows through the same SQLite read owner the follower uses, so Quick Open
                # returns direct files while the deeper crawl is still running instead of falling
                # through to the synchronous full-tree walk this feature exists to remove.
                own_indexed = file_index.search_disk_index(
                    root,
                    skip_dirs,
                    index_policy["exclude_signature"],
                    _match,
                    max_results,
                    [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                    expected_root_identity=authorized_root_identity,
                )
                # Availability is tracked separately from match count: once ANY progressive snapshot
                # exists for this root (its own committed layer, or a persisted child), the read path
                # answers from it -- even when this query matches nothing yet -- instead of falling
                # through to the synchronous full-tree walk. A name that exists only below the
                # published frontier must return an honest empty/warming result, not trigger a walk.
                snapshot_available = own_indexed is not None
                if own_indexed is not None:
                    own_rows, own_truncated = own_indexed
                    child_results.extend(own_rows)
                    child_truncated = child_truncated or own_truncated
                for candidate, child_policy in child_indexes:
                    child_indexed = file_index.search_disk_index(
                        candidate,
                        child_policy["skip_dirs"],
                        child_policy["exclude_signature"],
                        _match,
                        max_results,
                        [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                        include_metadata=not minimal,
                    )
                    if child_indexed is None:
                        continue
                    snapshot_available = True
                    rows, truncated = child_indexed
                    child_results.extend(rows)
                    child_truncated = child_truncated or truncated
                if snapshot_available:
                    child_results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
                    unique_rows: list[dict[str, Any]] = []
                    seen_paths: set[str] = set()
                    for entry in child_results:
                        path_text = str(entry.get("path") or "")
                        if not path_text or path_text in seen_paths:
                            continue
                        entry.pop("_sort_key", None)
                        if not _annotate_search_dedupe_fields(entry, root=root, root_descriptor=access_descriptor):
                            continue
                        if minimal:
                            entry = _minimal_search_entry(entry)
                        seen_paths.add(path_text)
                        unique_rows.append(entry)
                        if len(unique_rows) >= max_results:
                            child_truncated = True
                            break
                    # This process is the build owner: the only refresh that exists is
                    # its own warming build, which is here, not elsewhere.
                    freshness = _snapshot_freshness(index, root, index_policy)
                    # Item 5: a query for a scope that is not yet fully covered promotes this root's
                    # frontier to user-visible-demand without blocking (fire-and-forget), so a
                    # Cmd-P for a not-yet-indexed directory advances that root ahead of ordinary
                    # background breadth work instead of only waiting for the crawl's own cadence.
                    _promote_user_visible_scope(root)
                    return {
                        "root": str(root),
                        "root_realpath": str(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": child_truncated,
                        "index_state": "warming",
                        "index_coverage": "partial",
                        "files": unique_rows,
                        **_progressive_payload_fields(root),
                        **freshness.payload_fields(),
                    }
        if indexed_only and not index.ready:
            freshness = _snapshot_freshness(index, root, index_policy)
            return {
                "root": str(root),
                "root_realpath": str(root),
                "query": str(query or ""),
                "limit": max_results,
                "truncated": False,
                "files": [],
                "index_state": "warming",
                "index_coverage": "pending",
                **_progressive_payload_fields(root),
                **freshness.payload_fields(),
            }
        if not tokens:
            # C11: an EMPTY query on a full-tree root used to fall through to a cold recursive walk just to
            # return the first N files. When the index is ready, serve a capped most-recent slice from it
            # instantly; when it is still warming, return nothing (the client shows recent/open files)
            # rather than paying that cold walk.
            def _recent(path_str: str, name: str, rel: str) -> dict[str, Any]:
                return {
                    "name": name,
                    "path": path_str,
                    "relative_path": rel,
                    "kind": "file",
                    "uploaded": is_generated_upload_name(Path(path_str)),
                }
            recent: tuple[list[dict[str, Any]], bool] | None = None
            recent_payload_state = "ready"
            if index.ready:
                recent = file_index.recent_entries(index, max_results, _recent)
            elif not can_build_index and index.disk_metadata_ready:
                recent = file_index.recent_disk_entries(
                    root,
                    skip_dirs,
                    index_policy["exclude_signature"],
                    max_results,
                    _recent,
                    expected_root_identity=authorized_root_identity,
                )
                recent_payload_state = "follower-ready"
            if recent is not None:
                recent_results, recent_truncated = recent
                recent_results = [
                    entry
                    for entry in recent_results
                    if _annotate_search_dedupe_fields(entry, root=root, root_descriptor=access_descriptor)
                ]
                freshness = _snapshot_freshness(index, root, index_policy)
                if recent_payload_state == "follower-ready" and not freshness.authoritative:
                    recent_payload_state = "follower-stale"
                return {
                    "root": str(root),
                    "root_realpath": str(root),
                    "query": "",
                    "limit": max_results,
                    "truncated": recent_truncated,
                    "files": recent_results,
                    "index_state": recent_payload_state,
                    **freshness.payload_fields(),
                }
            if not index.ready and not can_build_index:
                freshness = _snapshot_freshness(index, root, index_policy)
                return {
                    "root": str(root),
                    "root_realpath": str(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": False,
                    "files": [],
                    "index_state": "follower-fallback-skipped",
                    **freshness.payload_fields(),
                }
            return {
                "root": str(root),
                "root_realpath": str(root),
                "query": "",
                "limit": max_results,
                "truncated": False,
                "files": [],
                "index_state": "warming",
            }
    results: list[dict[str, Any]] = []
    visited_dirs = 0
    visited_files = 0
    truncated = False
    if full_tree:
        visited_dirs, visited_files, truncated = _search_full_tree(
            root,
            scan_root,
            tokens,
            results,
            skip_dirs,
            display_search_root=root,
            resolved_search_root=root,
            search_descriptor=access_descriptor,
        )
    else:
        visited_dirs = 1
        scan_fd = access_descriptor
        if direct_only:
            # Path-mode Quick Open only needs names and entry kinds. Enumerate once with scandir,
            # match before opening children, and retain only bounded name-only rows. The parent
            # descriptor already establishes the authorized scope; opening/statting a child here
            # would duplicate work that file-open performs after the user selects a result.
            direct_entries: list[tuple[str, str, tuple[int, int, int, int, int, str]]] = []
            secret_policy = paths._compiled_secret_policy()
            configured_exclusions = bool(index_policy["excluded_paths"])
            try:
                with os.scandir(scan_fd) as entries:
                    for entry in entries:
                        name = entry.name
                        if name in skip_dirs:
                            continue
                        sort_key = _direct_name_sort_key(name, tokens)
                        if sort_key is None:
                            continue
                        if paths._candidate_is_secret(root / name, secret_policy):
                            continue
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            kind = "dir"
                        elif entry.is_file(follow_symlinks=False):
                            kind = "file"
                        else:
                            continue
                        direct_entries.append((name, kind, sort_key))
                        if not tokens and len(direct_entries) >= max_results:
                            break
            except OSError as error:
                raise error
            direct_entries.sort(key=lambda item: item[2])
            admitted = 0
            for name, kind, sort_key in direct_entries:
                path = root / name
                if configured_exclusions and index_policy["exclude_path"](path):
                    continue
                results.append(_direct_search_entry(root, path, kind=kind, sort_key=sort_key))
                admitted += 1
                if admitted >= max_results:
                    break
            truncated = len(direct_entries) > admitted
            direct_names = []
        else:
            direct_names = sorted(os.listdir(scan_fd), key=str.lower)
        for name in direct_names:
            display_path = root / name
            if name in skip_dirs:
                continue
            # Every child is opened RELATIVE to the pinned scan-root descriptor through the one
            # shared authorization owner.  This branch used to `os.open()` an ABSOLUTE child
            # path and wrap the raw descriptor in a bare `SafePathHandle`, so the child never
            # passed `_ensure_path_allowed` and its name was resolved a second time after the
            # scan root had already been authorized.  `safe_child` keeps `O_NOFOLLOW`, applies
            # the one root/secret policy, and pins the generation the scan then consumes.
            try:
                with paths.safe_child(
                    scan_fd,
                    display_path,
                    display_path,
                    flags=os.O_RDONLY,
                    operation="search_files",
                    observe_name=False,
                ) as child:
                    entry_stat = child.stat_result
                    if stat.S_ISDIR(entry_stat.st_mode):
                        if not _directory_is_repo(child.descriptor):
                            continue
                        child_dirs, child_files, child_truncated = _search_full_tree(
                            root,
                            child.descriptor_path(),
                            tokens,
                            results,
                            skip_dirs,
                            display_search_root=display_path,
                            resolved_search_root=child.resolved,
                            search_descriptor=child.descriptor,
                        )
                        visited_dirs += child_dirs
                        visited_files += child_files
                        truncated = truncated or child_truncated
                        if len(results) >= max_results or visited_files > MAX_SEARCH_FILES or visited_dirs > MAX_SEARCH_DIRS:
                            truncated = True
                            break
                        continue
                    visited_files += 1
                    if visited_files > MAX_SEARCH_FILES:
                        truncated = True
                        break
                    entry = _search_file_entry(
                        root,
                        display_path,
                        tokens,
                        display_path=display_path,
                        stat_result=entry_stat,
                    )
                    if entry is None:
                        continue
                    results.append(entry)
            except (paths.FilesystemError, OSError):
                continue
    results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
    if len(results) > max_results:
        truncated = True
        results = results[:max_results]
    for entry in results:
        entry.pop("_sort_key", None)
    # A capped walk is not a complete Quick Open answer.  In a large tree it can
    # return a few fuzzy early-directory hits while missing an exact basename that
    # appears later.  The persistent index is the complete source for full-tree
    # search; while it warms, report that state rather than presenting a false list.
    if full_tree and truncated:
        return {
            "root": str(root),
            "root_realpath": str(root),
            "query": str(query or ""),
            "limit": max_results,
            "truncated": True,
            "index_state": "warming",
            "index_coverage": "pending",
            "files": [],
        }
    payload = {
        "root": str(root),
        "root_realpath": str(root),
        "query": str(query or ""),
        "limit": max_results,
        "truncated": truncated,
        "files": results,
    }
    if full_tree:
        payload["index_state"] = "warming"
        payload["index_coverage"] = "pending"
    return payload


def _search_files_from_authorized_handle(
    handle: paths.SafePathHandle,
    query: str = "",
    limit: int | str | None = 400,
    recursive: bool = False,
    cursor: str | None = None,
    direct_only: bool = False,
    indexed_only: bool = False,
    minimal: bool = False,
) -> dict[str, Any]:
    inside_repo = False if direct_only else git_ops._pinned_repo_root(handle, operation="search_files") is not None
    payload = _search_files_from_safe_root(
        str(handle.resolved),
        query,
        limit,
        recursive,
        access_root=handle.descriptor_path(),
        access_descriptor=handle.descriptor,
        inside_repo=inside_repo,
        cursor=cursor,
        direct_only=direct_only,
        indexed_only=indexed_only,
        minimal=minimal,
    )
    if not cursor and not direct_only and isinstance(payload, dict) and "changes" not in payload and not payload.get("rebase_required"):
        # Step 4: the FIRST (snapshot) response carries the baseline cursor the client seeds its
        # subsequent delta reads with. `None` when nothing is committed yet -- the client then has no
        # cursor to pull with and repairs from the next snapshot once the writer publishes layer 1.
        # Pinned to the resolved authorized root and the ONE search policy the delta reads validate
        # against; a delta response already carries its own `cursor`, so this is snapshot-only.
        payload["initial_cursor"] = initial_delta_cursor(
            handle.resolved,
            root_fd=handle.descriptor,
        )
    return payload


def indexed_search_stream_payload(handle: paths.SafePathHandle, query: str, limit: int | str | None) -> dict[str, Any]:
    """Read one bounded indexed snapshot from an already-authorized root handle."""
    return _search_files_from_authorized_handle(
        handle,
        query=query,
        limit=limit,
        recursive=True,
        indexed_only=True,
        minimal=True,
    )


def iter_indexed_search_stream_payload(
    handle: paths.SafePathHandle,
    query: str,
    limit: int | str | None,
    *,
    skip_paths: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield minimal indexed Quick Open rows directly from the read-only SQLite cursor."""
    root = Path(handle.resolved)
    policy = _search_index_policy(root)
    tokens = [token for token in str(query or "").split() if token]
    match = _make_search_match(tokens)
    expected_identity = None
    descriptor = getattr(handle, "descriptor", None)
    if isinstance(descriptor, int):
        expected_identity = file_index.parse_root_identity(file_index.root_identity(os.fstat(descriptor)))
    for chunk in file_index.iter_disk_search_chunks(
        root,
        policy["skip_dirs"],
        policy["exclude_signature"],
        match,
        _search_limit(limit),
        [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
        expected_root_identity=expected_identity,
        include_metadata=False,
        skip_paths=skip_paths,
    ):
        files = []
        for entry in chunk.get("files", []):
            if descriptor is not None and not _annotate_search_dedupe_fields(entry, root=root, root_descriptor=descriptor):
                continue
            files.append(_minimal_search_entry(entry))
        coverage = file_index.read_index_coverage(root) or {}
        yield {
            "files": files,
            "truncated": bool(chunk.get("truncated")),
            "complete": bool(chunk.get("complete")),
            "available": bool(chunk.get("available")),
            "index_state": "ready" if coverage.get("full_coverage") else "warming",
            "refresh_pending": bool(coverage.get("frontier_size")),
            "progressive_coverage": coverage,
        }


def search_files(
    raw_root: str,
    query: str = "",
    limit: int | str | None = 400,
    recursive: bool = False,
    cursor: str | None = None,
    direct_only: bool = False,
    indexed_only: bool = False,
    minimal: bool = False,
) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags, operation="search_files") as handle:
        return _search_files_from_authorized_handle(handle, query, limit, recursive, cursor, direct_only, indexed_only, minimal)


def _index_status_from_safe_root(raw_root: str, *, root_fd: int | None = None) -> dict[str, Any]:
    """Warm the persistent quick-open index for a root and report its build state."""
    root = Path(raw_root)
    index, policy = _ensure_search_index(root, operation="index_status", root_fd=root_fd)
    # HTTP servers are read-only consumers. Asking for index status is still an
    # explicit Quick Open demand, so queue the persistent indexer when no
    # committed snapshot exists yet.
    if not index.ready and not index.disk_metadata_ready:
        file_index.request_background_owner_refresh({"root": str(root), "reason": "index-status"})
    with index.lock:
        ready = bool(index.ready)
        building = bool(index.building)
        built_at = float(index.built_at or 0.0)
        metadata_ready = bool(index.disk_metadata_ready)
        count = len(index.entries) if ready else int(index.disk_entry_count)
        truncated = bool(index.truncated)
        too_large = bool(index.too_large)
        build_duration_ms = float(index.build_duration_ms)
        cache_bytes = int(index.cache_bytes)
        persisted = bool(index.persisted)
        build_count = int(index.build_count)
        full_build_count = int(index.full_build_count)
        incremental_build_count = int(index.incremental_build_count)
        scanned_entries = int(index.scanned_entries)
        ignored_entries = int(index.ignored_entries)
        write_bytes = int(index.write_bytes)
        dirty_subtrees = len(index.dirty_paths)
        build_generation = int(index.active_generation)
        completed_generation = int(index.completed_generation)
        last_error = str(index.last_error)
    # C11: report the real state so the Finder badge shows indexing/indexed honestly instead of guessing
    # (which made the badge flicker). `state` is the single field the UI keys on.
    state = "too_large" if ready and too_large else ("ready" if ready else ("building" if building else ("error" if last_error else "missing")))
    if not ready and not building and not file_index.background_owner_can_build():
        state = "follower"
    # `state` is a role/build predicate. Whether another process is refreshing,
    # and whether this snapshot may be called ready, are freshness questions and
    # come from the one freshness record - not from "I am not the owner".
    freshness = _snapshot_freshness(index, root, policy)
    return {
        "root": str(root),
        "root_realpath": str(root),
        "building": building,
        "ready": ready,
        "count": count,
        "built_at": built_at,
        "age": (time.time() - built_at) if built_at else None,
        "truncated": truncated,
        "too_large": too_large,
        "coverage": "partial" if too_large else "full",
        "build_duration_ms": build_duration_ms,
        "cache_bytes": cache_bytes,
        "persisted": persisted,
        "build_count": build_count,
        "full_build_count": full_build_count,
        "incremental_build_count": incremental_build_count,
        "scanned_entries": scanned_entries,
        "ignored_entries": ignored_entries,
        "write_bytes": write_bytes,
        "dirty_subtrees": dirty_subtrees,
        "generation": build_generation,
        "completed_generation": completed_generation,
        "error": last_error,
        "refresh_seconds": policy["refresh_seconds"],
        "max_files": policy["max_files"],
        "persist_max_files": policy["persist_max_files"],
        "persist_max_bytes": policy["persist_max_bytes"],
        "excluded_paths": policy["excluded_paths"],
        "state": state,
        "ready_elsewhere": state == "follower" and metadata_ready and freshness.authoritative,
        # Item 8: the measured breadth-first coverage for this root (published depth, frontier
        # depth/size, generations, snapshot age, full-coverage). Empty until the progressive
        # builder has published a manifest; nested so it cannot collide with freshness fields.
        "progressive_coverage": file_index.read_index_coverage(root) or {},
        **freshness.payload_fields(),
    }


def index_status(raw_root: str) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags, operation="index_status") as handle:
        return _index_status_from_safe_root(str(handle.resolved), root_fd=handle.descriptor)


def _unindex_safe_root(raw_root: str) -> dict[str, Any]:
    """Drop the persistent quick-open index for a root (cancel any build, free memory + on-disk)."""
    root = Path(raw_root)
    if file_index.background_owner_can_build():
        file_index.unindex(root)
        return {"root": str(root), "ok": True}
    result = file_index.request_background_owner_refresh({
        "root": str(root),
        "operation": "unindex",
        "reason": "unindex",
    })
    # This branch runs only when THIS process cannot build, so any acceptance is a
    # remote owner's -- derive both fields from the one control-outcome verdict
    # rather than reading the raw `accepted` boolean twice.
    outcome = RefreshOutcome.from_result(result)
    return {
        "root": str(root),
        "ok": outcome.accepted,
        "refreshing_elsewhere": outcome.refreshing_elsewhere,
    }


def unindex_root(raw_root: str) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags) as handle:
        return _unindex_safe_root(str(handle.resolved))


def reindex_roots_for_path(raw_path: str, reason: str = "filesystem-change") -> list[str]:
    return reindex_roots_for_paths([raw_path], reason=reason)


def reindex_roots_for_paths(raw_paths: list[str], reason: str = "filesystem-change") -> list[str]:
    """Coalesce changed subtrees and hand one incremental refresh to the owner."""
    # Item 6 guard + finding #2 prefilter: materialize the ONE shared validated candidate-root set
    # (registry + parsed manifests) once. With no roots at all -- OR when every changed path is
    # excluded by every containing root's shared policy (an all-ignored batch: .git, node_modules,
    # ...) -- this batch produces no dirty subtree, so short-circuit BEFORE the expensive per-path
    # `safe_parent` authorization. An empty-config OR all-ignored batch therefore does zero
    # safe_parent, dirty-mark, promotion, scheduling, and indexd RPC work, through the one exclusion
    # owner (no second ignore list).
    candidate_roots = list(file_index._iter_candidate_index_roots())
    if not candidate_roots:
        return []
    policies: dict[Path, dict[str, Any]] = {}

    def _root_policy(root: Path) -> dict[str, Any]:
        return policies.setdefault(root, _search_index_policy(root))

    def _admits(path: Path) -> bool:
        # At least one indexed ANCESTOR root must admit the path under that root's shared exclusion
        # policy. The LEXICAL path is judged (not a pre-resolved one): resolving first collapsed an
        # ignored/secret alias (root/.git/link -> root/src/file) to a clean target, so the shared
        # verdict never saw the ignored producer and the all-ignored zero-work short-circuit leaked
        # into safe_parent. The shared owner judges lexical AND resolved, so a lexical alias inside
        # an ignored dir is excluded here and an escape (resolved outside the root) is excluded too.
        for root in candidate_roots:
            if root == path:
                continue
            try:
                path.relative_to(root)
            except ValueError:
                continue
            policy = _root_policy(root)
            if not _index_path_is_excluded(root, path, policy["skip_dirs"], policy["exclude_path"]):
                return True
        return False

    normalized_paths: list[Path] = []
    for raw_path in raw_paths:
        if not _admits(Path(raw_path).expanduser()):
            continue
        try:
            with paths.safe_parent(raw_path) as handle:
                normalized_paths.append(handle.resolved_target)
        except FilesystemError as error:
            # Expected per-path change-evidence outcomes, NOT failures to propagate: a disappearing
            # path (parent already gone -> 404), a credential-blocked path, and a path whose parent is
            # outside the authorized roots (403 -- e.g. an indexed root's own top, which
            # `mark_paths_dirty` skips anyway). watchd, a delete, and a rename all legitimately
            # reference such paths. Skipping one must never wedge the rest of the batch or kill the
            # watchd revision handler that fed it (the spec's disappearing-path/permission rule). Any
            # other error is unexpected and still propagates.
            if error.message_key != "fs.error.credentialBlocked" and error.status not in (403, 404):
                raise
            blocked = str(raw_path)
            if blocked not in _LOGGED_BLOCKED_REINDEX_PATHS:
                _LOGGED_BLOCKED_REINDEX_PATHS.add(blocked)
                LOGGER.info("Skipping unindexable filesystem change path (%s): %s", error.message_key or error.status, blocked)
    if not normalized_paths:
        return []

    def include_root(root: Path, path: Path) -> bool:
        policy = _root_policy(root)
        return not _index_path_is_excluded(root, path, policy["skip_dirs"], policy["exclude_path"])

    owner_can_build = file_index.background_owner_can_build()

    def prepare_root(root: Path) -> None:
        if root.is_dir():
            _ensure_search_index(root, operation="reindex")

    roots_by_path = file_index.mark_paths_dirty(
        normalized_paths,
        include_root=include_root,
        prepare_root=prepare_root if owner_can_build else None,
    )
    if owner_can_build:
        # Item 6, promote branch: for a root whose breadth-first crawl has NOT yet reached the
        # changed subtree, raise that root's pending durable frontier to `hot-change` priority so the
        # crawl reaches the changed area ahead of ordinary breadth work, instead of enqueuing a
        # competing task for the same directory. This reuses item 5's one bounded `promote_frontier`
        # UPDATE (only ever RAISING priority); the incremental dirty repair below is the other,
        # already-covered branch. No second repair path is added.
        for root in roots_by_path:
            file_index.promote_frontier(
                root,
                to_priority=file_index.HOT_CHANGE_PRIORITY,
                to_reason=file_index.HOT_CHANGE_REASON,
            )
        file_index.schedule_refreshes()
    else:
        for root, changed_paths in roots_by_path.items():
            file_index.request_background_owner_refresh({
                "root": str(root),
                "paths": [str(path) for path in sorted(changed_paths, key=str)],
                "path": str(sorted(changed_paths, key=str)[0]),
                "reason": reason,
            })
    return [str(root) for root in sorted(roots_by_path, key=str)]

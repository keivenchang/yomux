"""File search and persistent index entry points."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import stat
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ..search import file_index
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
) -> tuple[file_index.RootIndex, dict[str, Any]]:
    policy = _search_index_policy(root)
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


def _search_token_rank(token: str, path: Path, rel: str) -> int | None:
    needle = _compact_search_text(token)
    if not needle:
        return 0
    basename = _compact_search_text(path.name)
    stem = _compact_search_text(path.stem)
    rel_text = _compact_search_text(rel)
    doit_needle = _doit_search_token(token)
    if doit_needle:
        basename_alnum = _alnum_search_text(path.name)
        stem_alnum = _alnum_search_text(path.stem)
        rel_alnum = _alnum_search_text(rel)
        if doit_needle in (stem_alnum, basename_alnum):
            return 0
        if stem_alnum.startswith(doit_needle) or basename_alnum.startswith(doit_needle):
            return 10
        if stem_alnum.find(doit_needle) >= 0:
            return 20 + stem_alnum.find(doit_needle)
        if basename_alnum.find(doit_needle) >= 0:
            return 30 + basename_alnum.find(doit_needle)
        if rel_alnum.find(doit_needle) >= 0:
            return 90 + rel_alnum.find(doit_needle)
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
    index = rel_text.find(needle)
    if index >= 0:
        return 90 + index
    span = _fuzzy_subsequence_span(needle, rel_text)
    if span is not None:
        return 130 + rel.count("/") * 4 + span
    return None


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
    display_path: Path | None = None,
    stat_result: os.stat_result | None = None,
) -> dict[str, Any] | None:
    if stat_result is None:
        try:
            st = path.lstat()
        except OSError:
            return None
    else:
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


def _annotate_search_dedupe_fields(entry: dict[str, Any]) -> None:
    """Add realpath + size to an indexed search hit so the client can fold mirror/symlink copies."""
    path_str = entry.get("path")
    if not isinstance(path_str, str):
        return
    try:
        with paths.safe_path(
            path_str,
            flags=getattr(os, "O_PATH", os.O_RDONLY),
            operation="search.annotate",
        ) as handle:
            entry["realpath"] = str(handle.resolved)
            entry["size"] = int(handle.stat_result.st_size)
            identity = paths._physical_file_identity(
                handle.requested,
                resolved=handle.resolved,
                stat_result=handle.stat_result,
            )
    except paths.FilesystemError:
        for key in ("realpath", "size", "file_id", "file_identity"):
            entry.pop(key, None)
        return
    entry.update(identity)


def _search_full_tree(
    root: Path,
    search_root: Path,
    tokens: list[str],
    results: list[dict[str, Any]],
    skip_dirs: set[str] | None = None,
    *,
    display_search_root: Path | None = None,
    search_descriptor: int | None = None,
) -> tuple[int, int, bool]:
    effective_skip_dirs = SEARCH_SKIP_DIRS if skip_dirs is None else skip_dirs
    visited_dirs = 0
    visited_files = 0
    truncated = False
    result_search_root = display_search_root or root
    owned_descriptor = None
    if search_descriptor is None:
        owned_descriptor = os.open(search_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        search_descriptor = owned_descriptor

    def include_directory(relative: Path) -> bool:
        return relative.name not in effective_skip_dirs and not paths._path_is_secret(result_search_root / relative)

    try:
        walker = paths.walk_directory(
            search_descriptor,
            include_directory=include_directory,
            operation="search_files",
            requested_root=result_search_root,
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
    finally:
        if owned_descriptor is not None:
            os.close(owned_descriptor)
    return visited_dirs, visited_files, truncated


def _search_files_from_safe_root(
    raw_root: str,
    query: str = "",
    limit: int | str | None = 400,
    recursive: bool = False,
    *,
    access_root: Path | None = None,
    access_descriptor: int | None = None,
    inside_repo: bool = False,
) -> dict[str, Any]:
    root = Path(raw_root)
    scan_root = access_root or root
    if not scan_root.exists():
        raise paths.FilesystemError.path_not_found(root)
    if not scan_root.is_dir():
        raise paths.FilesystemError.not_directory(root)
    max_results = _search_limit(limit)
    tokens = [token for token in str(query or "").split() if token]
    index_policy = _search_index_policy(root)
    skip_dirs = index_policy["skip_dirs"]
    full_tree = bool(recursive) or inside_repo
    if full_tree:
        # Accelerate full-tree quick-open with the persistent index: it covers the
        # whole tree (no 20k/50k walk cap) and needs no per-query walk. Warm/refresh
        # it in the background; until it is ready we fall back to the live walk below
        # (stale-while-revalidate), so search never blocks on indexing.
        index, index_policy = _ensure_search_index(root, operation="search_files")
        skip_dirs = index_policy["skip_dirs"]
        can_build_index = file_index.background_owner_can_build()
        if tokens:
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

            indexed_payload_state = ""
            indexed: tuple[list[dict[str, Any]], bool] | None = None
            if index.ready:
                indexed = file_index.search_index(index, _match, max_results)
            elif not can_build_index and index.disk_metadata_ready:
                indexed = file_index.search_disk_index(
                    root,
                    skip_dirs,
                    index_policy["exclude_signature"],
                    _match,
                    max_results,
                    [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                )
                indexed_payload_state = "follower-ready" if indexed is not None else ""
            if indexed is not None:
                indexed_results, indexed_truncated = indexed
                for entry in indexed_results:
                    entry.pop("_sort_key", None)
                    # Annotate the (capped) results with realpath + size so the client can dedupe symlink
                    # overlaps and content-mirror copies. Bounded to <= max_results, so the stat is cheap.
                    _annotate_search_dedupe_fields(entry)
                freshness = _snapshot_freshness(index, root, index_policy)
                payload = {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": indexed_truncated,
                    "index_state": "too_large" if index.too_large else "ready",
                    "index_coverage": "partial" if index.too_large else "full",
                    "files": indexed_results,
                    **freshness.payload_fields(),
                }
                if indexed_payload_state:
                    # A follower serves the snapshot either way, but may only call it
                    # ready/full when the freshness record vouches for it.
                    payload["index_state"] = "follower-ready" if freshness.authoritative else "follower-stale"
                    if not freshness.authoritative:
                        payload["index_coverage"] = "unverified"
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
                )
                if fallback_indexed is not None:
                    fallback_results, fallback_truncated = fallback_indexed
                    for entry in fallback_results:
                        entry.pop("_sort_key", None)
                        _annotate_search_dedupe_fields(entry)
                    freshness = _snapshot_freshness(index, root, index_policy)
                    return {
                        "root": str(root),
                        "root_realpath": os.path.realpath(root),
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
                        "root_realpath": os.path.realpath(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": False,
                        "files": [],
                        "index_state": "follower",
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
                for candidate, child_policy in child_indexes:
                    child_indexed = file_index.search_disk_index(
                        candidate,
                        child_policy["skip_dirs"],
                        child_policy["exclude_signature"],
                        _match,
                        max_results,
                        [_compact_search_text(token) for token in tokens if len(_compact_search_text(token)) >= 3],
                    )
                    if child_indexed is None:
                        continue
                    rows, truncated = child_indexed
                    child_results.extend(rows)
                    child_truncated = child_truncated or truncated
                if child_results:
                    child_results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
                    unique_rows: list[dict[str, Any]] = []
                    seen_paths: set[str] = set()
                    for entry in child_results:
                        path_text = str(entry.get("path") or "")
                        if not path_text or path_text in seen_paths:
                            continue
                        seen_paths.add(path_text)
                        entry.pop("_sort_key", None)
                        _annotate_search_dedupe_fields(entry)
                        unique_rows.append(entry)
                        if len(unique_rows) >= max_results:
                            child_truncated = True
                            break
                    # This process is the build owner: the only refresh that exists is
                    # its own warming build, which is here, not elsewhere.
                    freshness = _snapshot_freshness(index, root, index_policy)
                    return {
                        "root": str(root),
                        "root_realpath": os.path.realpath(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": child_truncated,
                        "index_state": "warming",
                        "index_coverage": "partial",
                        "files": unique_rows,
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
                recent = file_index.recent_disk_entries(root, skip_dirs, index_policy["exclude_signature"], max_results, _recent)
                recent_payload_state = "follower-ready"
            if recent is not None:
                recent_results, recent_truncated = recent
                for entry in recent_results:
                    _annotate_search_dedupe_fields(entry)
                freshness = _snapshot_freshness(index, root, index_policy)
                if recent_payload_state == "follower-ready" and not freshness.authoritative:
                    recent_payload_state = "follower-stale"
                return {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
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
                    "root_realpath": os.path.realpath(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": False,
                    "files": [],
                    "index_state": "follower-fallback-skipped",
                    **freshness.payload_fields(),
                }
            return {
                "root": str(root),
                "root_realpath": os.path.realpath(root),
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
            search_descriptor=access_descriptor,
        )
    else:
        visited_dirs = 1
        direct_names = sorted(os.listdir(scan_root), key=str.lower)
        for name in direct_names:
            path = scan_root / name
            display_path = root / name
            if name in skip_dirs or paths._path_is_secret(display_path):
                continue
            try:
                entry_fd = os.open(path, os.O_RDONLY | paths.nofollow_flag())
            except OSError:
                continue
            try:
                entry_stat = os.fstat(entry_fd)
            finally:
                os.close(entry_fd)
            if stat.S_ISDIR(entry_stat.st_mode):
                try:
                    child_fd = os.open(
                        path,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | paths.nofollow_flag(),
                    )
                except OSError:
                    continue
                try:
                    child_path = paths.SafePathHandle(display_path, display_path, child_fd).descriptor_path()
                    if not _directory_is_repo(child_path):
                        continue
                    child_dirs, child_files, child_truncated = _search_full_tree(
                        root,
                        child_path,
                        tokens,
                        results,
                        skip_dirs,
                        display_search_root=display_path,
                        search_descriptor=child_fd,
                    )
                finally:
                    os.close(child_fd)
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
                path,
                tokens,
                display_path=display_path,
                stat_result=entry_stat,
            )
            if entry is None:
                continue
            results.append(entry)
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
            "root_realpath": os.path.realpath(root),
            "query": str(query or ""),
            "limit": max_results,
            "truncated": True,
            "index_state": "warming",
            "index_coverage": "pending",
            "files": [],
        }
    return {
        "root": str(root),
        "root_realpath": os.path.realpath(root),
        "query": str(query or ""),
        "limit": max_results,
        "truncated": truncated,
        "files": results,
    }


def search_files(raw_root: str, query: str = "", limit: int | str | None = 400, recursive: bool = False) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags, operation="search_files") as handle:
        inside_repo = git_ops._pinned_repo_root(handle, operation="search_files") is not None
        return _search_files_from_safe_root(
            str(handle.resolved),
            query,
            limit,
            recursive,
            access_root=handle.descriptor_path(),
            access_descriptor=handle.descriptor,
            inside_repo=inside_repo,
        )


def _index_status_from_safe_root(raw_root: str) -> dict[str, Any]:
    """Warm the persistent quick-open index for a root and report its build state."""
    root = Path(raw_root)
    if not root.is_dir():
        raise paths.FilesystemError.not_directory(root)
    index, policy = _ensure_search_index(root, operation="index_status")
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
        "root_realpath": os.path.realpath(root),
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
        **freshness.payload_fields(),
    }


def index_status(raw_root: str) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags, operation="index_status") as handle:
        return _index_status_from_safe_root(str(handle.resolved))


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
    return {
        "root": str(root),
        "ok": bool(result.get("accepted")),
        "refreshing_elsewhere": bool(result.get("accepted")),
    }


def unindex_root(raw_root: str) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_root, flags=directory_flags) as handle:
        return _unindex_safe_root(str(handle.resolved))


def reindex_roots_for_path(raw_path: str, reason: str = "filesystem-change") -> list[str]:
    return reindex_roots_for_paths([raw_path], reason=reason)


def reindex_roots_for_paths(raw_paths: list[str], reason: str = "filesystem-change") -> list[str]:
    """Coalesce changed subtrees and hand one incremental refresh to the owner."""
    normalized_paths: list[Path] = []
    for raw_path in raw_paths:
        try:
            with paths.safe_parent(raw_path) as handle:
                normalized_paths.append(handle.resolved_target)
        except FilesystemError as error:
            if error.message_key != "fs.error.credentialBlocked":
                raise
            blocked = str(raw_path)
            if blocked not in _LOGGED_BLOCKED_REINDEX_PATHS:
                _LOGGED_BLOCKED_REINDEX_PATHS.add(blocked)
                LOGGER.warning("Skipping blocked filesystem watch path: %s", blocked)
    policies: dict[Path, dict[str, Any]] = {}

    def include_root(root: Path, path: Path) -> bool:
        policy = policies.setdefault(root, _search_index_policy(root))
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

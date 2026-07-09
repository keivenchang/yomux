"""File search and persistent index entry points."""

from __future__ import annotations

import hashlib
import json
import os
import re
from fnmatch import fnmatchcase
import stat
import time
from pathlib import Path
from typing import Any

from .. import file_index
from ..common import is_generated_upload_name
from ..settings import settings_payload
from . import paths
from .errors import raise_os_error
from .git_ops import git_root_for_path
from .listing import _directory_is_repo

SEARCH_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".jj", ".cache", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", "target",
}
SEARCH_SECRET_EXCLUDE_SIGNATURE = "fs-secret-v2"
INDEX_EXCLUDE_GLOB_PREFIX = "glob:"
INDEX_EXCLUDE_REGEX_PREFIX = "regex:"
MAX_SEARCH_DIRS = 20_000
MAX_SEARCH_FILES = 50_000
MAX_SEARCH_LIMIT = 2_000


def _index_exclude_rule(raw_rule: str, root: Path) -> tuple[str, str, Path | re.Pattern[str]] | None:
    value = str(raw_rule or "").strip()
    if not value:
        return None
    if value.startswith(INDEX_EXCLUDE_GLOB_PREFIX):
        pattern = value.removeprefix(INDEX_EXCLUDE_GLOB_PREFIX).strip().replace("\\", "/").lstrip("/")
        return ("glob", pattern, Path(".")) if pattern else None
    if value.startswith(INDEX_EXCLUDE_REGEX_PREFIX):
        pattern = value.removeprefix(INDEX_EXCLUDE_REGEX_PREFIX).strip()
        if not pattern:
            return None
        try:
            return "regex", pattern, re.compile(pattern)
        except re.error:
            return None
    candidate = Path(value).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return "path", str(candidate), candidate


def _index_exclude_rule_matches(rule: tuple[str, str, Path | re.Pattern[str]], path: Path, root: Path) -> bool:
    kind, _value, matcher = rule
    try:
        relative_path = path.expanduser().resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return False
    if kind == "path":
        assert isinstance(matcher, Path)
        try:
            path.expanduser().resolve(strict=False).relative_to(matcher)
            return True
        except ValueError:
            return False
    if kind == "glob":
        pattern = _value
        # Try the directory form too: a familiar rule such as `glob:**/.uploads/**`
        # must prune `.uploads` itself, not merely reject files after walking it.
        candidates = (relative_path, f"_/{relative_path}", f"{relative_path}/", f"_/{relative_path}/")
        return any(fnmatchcase(candidate, pattern) for candidate in candidates)
    assert isinstance(matcher, re.Pattern)
    return matcher.search(relative_path) is not None


def _search_index_policy(root: Path) -> dict[str, Any]:
    settings = settings_payload().get("settings", {}).get("file_explorer", {})
    configured_rules = [rule for raw_path in settings.get("index_exclude_paths", []) if isinstance(raw_path, str) if (rule := _index_exclude_rule(raw_path, root)) is not None]
    configured_rules.sort(key=lambda rule: (rule[0], rule[1]))

    def exclude_path(path: Path) -> bool:
        if paths._path_is_secret(path):
            return True
        return any(_index_exclude_rule_matches(rule, path, root) for rule in configured_rules)

    max_files = int(settings.get("index_max_files", file_index.MAX_INDEX_FILES))
    persist_max_files = int(settings.get("index_persist_max_files", file_index.MAX_PERSISTED_INDEX_FILES))
    persist_max_bytes = int(settings.get("index_persist_max_mb", file_index.MAX_PERSISTED_INDEX_BYTES // (1024 * 1024))) * 1024 * 1024
    refresh_seconds = float(settings.get("index_refresh_seconds", file_index.INDEX_TTL_SECONDS))
    rule_values = [f"{kind}:{value}" for kind, value, _matcher in configured_rules]
    coverage_policy = {
        "excludes": rule_values,
        "max_files": max_files,
    }
    policy_signature = SEARCH_SECRET_EXCLUDE_SIGNATURE
    if coverage_policy != {"excludes": [], "max_files": file_index.MAX_INDEX_FILES}:
        digest = hashlib.sha256(json.dumps(coverage_policy, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        policy_signature = f"{SEARCH_SECRET_EXCLUDE_SIGNATURE}:{digest}"
    return {
        "exclude_path": exclude_path,
        "exclude_signature": policy_signature,
        "max_files": max_files,
        "refresh_seconds": refresh_seconds,
        "persist_enabled": bool(settings.get("index_persist", True)),
        "persist_max_files": persist_max_files,
        "persist_max_bytes": persist_max_bytes,
        "excluded_paths": rule_values,
    }


def _ensure_search_index(root: Path) -> tuple[file_index.RootIndex, dict[str, Any]]:
    policy = _search_index_policy(root)
    index = file_index.ensure_index(
        root,
        SEARCH_SKIP_DIRS,
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        max_files=policy["max_files"],
        refresh_seconds=policy["refresh_seconds"],
        persist_enabled=policy["persist_enabled"],
        persist_max_files=policy["persist_max_files"],
        persist_max_bytes=policy["persist_max_bytes"],
    )
    return index, policy


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


def _search_file_entry(root: Path, path: Path, tokens: list[str]) -> dict[str, Any] | None:
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    sort_key = _search_entry_sort_key(path, rel, tokens)
    if sort_key is None:
        return None
    return {
        "name": path.name,
        "path": str(path),
        "relative_path": rel,
        "kind": "file",
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
        "uploaded": is_generated_upload_name(path),
        "_sort_key": sort_key,
        **paths._physical_file_identity(path),
    }


def _annotate_search_dedupe_fields(entry: dict[str, Any]) -> None:
    """Add realpath + size to an indexed search hit so the client can fold mirror/symlink copies."""
    path_str = entry.get("path")
    if not isinstance(path_str, str):
        return
    entry.setdefault("realpath", os.path.realpath(path_str))
    if "size" not in entry:
        try:
            entry["size"] = int(os.stat(path_str).st_size)
        except OSError:
            entry["size"] = None
    entry.update({key: value for key, value in paths._physical_file_identity(Path(path_str)).items() if key not in entry})


def _search_full_tree(root: Path, search_root: Path, tokens: list[str], results: list[dict[str, Any]]) -> tuple[int, int, bool]:
    visited_dirs = 0
    visited_files = 0
    truncated = False
    walker = os.walk(search_root, topdown=True, onerror=raise_os_error, followlinks=False)
    for current, dirs, files in walker:
        visited_dirs += 1
        if visited_dirs > MAX_SEARCH_DIRS:
            truncated = True
            dirs[:] = []
            break
        dirs[:] = sorted(
            [name for name in dirs if name not in SEARCH_SKIP_DIRS and not paths._path_is_secret(Path(current) / name)],
            key=str.lower,
        )
        for name in sorted(files, key=str.lower):
            visited_files += 1
            if visited_files > MAX_SEARCH_FILES:
                truncated = True
                dirs[:] = []
                break
            path = Path(current) / name
            if paths._path_is_secret(path):
                continue
            entry = _search_file_entry(root, path, tokens)
            if entry is not None:
                results.append(entry)
        if visited_files > MAX_SEARCH_FILES:
            break
    return visited_dirs, visited_files, truncated


def search_files(raw_root: str, query: str = "", limit: int | str | None = 400, recursive: bool = False) -> dict[str, Any]:
    root = paths._canonical_root(paths._validated_path(raw_root))
    if not root.exists():
        raise paths.FilesystemError.path_not_found(root)
    if not root.is_dir():
        raise paths.FilesystemError.not_directory(root)
    max_results = _search_limit(limit)
    tokens = [token for token in str(query or "").split() if token]
    full_tree = bool(recursive) or bool(git_root_for_path(root))
    if full_tree:
        # Accelerate full-tree quick-open with the persistent index: it covers the
        # whole tree (no 20k/50k walk cap) and needs no per-query walk. Warm/refresh
        # it in the background; until it is ready we fall back to the live walk below
        # (stale-while-revalidate), so search never blocks on indexing.
        index, index_policy = _ensure_search_index(root)
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
                    SEARCH_SKIP_DIRS,
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
                payload = {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": indexed_truncated,
                    "index_state": "too_large" if index.too_large else "ready",
                    "index_coverage": "partial" if index.too_large else "full",
                    "files": indexed_results,
                }
                if indexed_payload_state:
                    payload["index_state"] = indexed_payload_state
                    payload["refreshing_elsewhere"] = True
                return payload
            if not index.ready and not can_build_index:
                refresh_result = file_index.request_background_owner_refresh({"root": str(root), "query": str(query or ""), "reason": "search-index-missing"})
                if not refresh_result.get("fallback"):
                    return {
                        "root": str(root),
                        "root_realpath": os.path.realpath(root),
                        "query": str(query or ""),
                        "limit": max_results,
                        "truncated": False,
                        "files": [],
                        "index_state": "follower",
                        "refreshing_elsewhere": True,
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
                recent = file_index.recent_disk_entries(root, SEARCH_SKIP_DIRS, index_policy["exclude_signature"], max_results, _recent)
                recent_payload_state = "follower-ready"
            if recent is not None:
                recent_results, recent_truncated = recent
                for entry in recent_results:
                    _annotate_search_dedupe_fields(entry)
                return {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
                    "query": "",
                    "limit": max_results,
                    "truncated": recent_truncated,
                    "files": recent_results,
                    "index_state": recent_payload_state,
                    "refreshing_elsewhere": recent_payload_state == "follower-ready",
                }
            if not index.ready and not can_build_index:
                return {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
                    "query": str(query or ""),
                    "limit": max_results,
                    "truncated": False,
                    "files": [],
                    "index_state": "follower-fallback-skipped",
                    "refreshing_elsewhere": False,
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
        visited_dirs, visited_files, truncated = _search_full_tree(root, root, tokens, results)
    else:
        visited_dirs = 1
        direct_names = sorted(os.listdir(root), key=str.lower)
        for name in direct_names:
            path = root / name
            if name in SEARCH_SKIP_DIRS or paths._path_is_secret(path):
                continue
            if path.is_dir() and _directory_is_repo(path):
                child_dirs, child_files, child_truncated = _search_full_tree(root, path, tokens, results)
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
            entry = _search_file_entry(root, path, tokens)
            if entry is None:
                continue
            results.append(entry)
    results.sort(key=lambda entry: entry.get("_sort_key", (999, 999, 0, 999, 999, "")))
    if len(results) > max_results:
        truncated = True
        results = results[:max_results]
    for entry in results:
        entry.pop("_sort_key", None)
    return {
        "root": str(root),
        "root_realpath": os.path.realpath(root),
        "query": str(query or ""),
        "limit": max_results,
        "truncated": truncated,
        "files": results,
    }


def index_status(raw_root: str) -> dict[str, Any]:
    """Warm the persistent quick-open index for a root and report its build state."""
    root = paths._canonical_root(paths._validated_path(raw_root))
    if not root.is_dir():
        raise paths.FilesystemError.not_directory(root)
    index, policy = _ensure_search_index(root)
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
    # C11: report the real state so the Finder badge shows indexing/indexed honestly instead of guessing
    # (which made the badge flicker). `state` is the single field the UI keys on.
    state = "too_large" if ready and too_large else ("ready" if ready else ("building" if building else "missing"))
    if not ready and not building and not file_index.background_owner_can_build():
        state = "follower"
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
        "refresh_seconds": policy["refresh_seconds"],
        "max_files": policy["max_files"],
        "persist_max_files": policy["persist_max_files"],
        "persist_max_bytes": policy["persist_max_bytes"],
        "excluded_paths": policy["excluded_paths"],
        "state": state,
        "ready_elsewhere": state == "follower" and metadata_ready,
        "refreshing_elsewhere": state == "follower",
    }


def unindex_root(raw_root: str) -> dict[str, Any]:
    """Drop the persistent quick-open index for a root (cancel any build, free memory + on-disk)."""
    root = paths._canonical_root(paths._validated_path(raw_root))
    file_index.unindex(root)
    return {"root": str(root), "ok": True}


def reindex_roots_for_path(raw_path: str, reason: str = "filesystem-change") -> list[str]:
    return reindex_roots_for_paths([raw_path], reason=reason)


def reindex_roots_for_paths(raw_paths: list[str], reason: str = "filesystem-change") -> list[str]:
    """Coalesce changed subtrees and hand one incremental refresh to the owner."""
    normalized_paths = [paths._normalized_scope_path(paths._validated_path(raw_path)) for raw_path in raw_paths]
    roots_by_path: dict[Path, set[Path]] = {}
    for path in normalized_paths:
        for root in file_index.mark_path_dirty(path):
            roots_by_path.setdefault(root, set()).add(path)
    if file_index.background_owner_can_build():
        for root, changed_paths in roots_by_path.items():
            if not root.is_dir():
                continue
            _ensure_search_index(root)
            for path in changed_paths:
                file_index.mark_path_dirty(path)
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

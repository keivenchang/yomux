"""Filesystem browsing + read/write helpers for the File Explorer panel.

All raw path entry points validate through :mod:`yolomux_lib.filesystem.paths`.
The package-level names preserve the old ``yolomux_lib.filesystem`` import surface
while implementation lives in smaller modules.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import time
from typing import Any

from ..common import error_payload
from . import paths
from . import git_ops
from . import io_ops
from . import listing
from . import search
from .errors import normalize_os_errors

AUTH_CONFIG_PATH = paths.AUTH_CONFIG_PATH
AUTH_COOKIE_SECRET_PATH = paths.AUTH_COOKIE_SECRET_PATH
CONFIG_DIR = paths.CONFIG_DIR
BINARY_SNIFF_BYTES = paths.BINARY_SNIFF_BYTES
DEFAULT_FS_ROOTS = paths.DEFAULT_FS_ROOTS
FilesystemError = paths.FilesystemError
FilesystemAccessPolicy = paths.FilesystemAccessPolicy
FS_ACCESS_POLICY_FIELD = paths.FS_ACCESS_POLICY_FIELD
FS_ACCESS_POLICY_VERSION = paths.FS_ACCESS_POLICY_VERSION
FS_ROOTS_ENV = paths.FS_ROOTS_ENV
access_policy_descriptor = paths.access_policy_descriptor
access_policy_from_descriptor = paths.access_policy_from_descriptor
access_policy_refused = paths.access_policy_refused
active_access_policy = paths.active_access_policy
authorized_fs_roots = paths.authorized_fs_roots
capture_access_policy = paths.capture_access_policy
enforce_access_policy = paths.enforce_access_policy
MAX_READ_BYTES = paths.MAX_READ_BYTES
SECRET_DIR_COMPONENTS = paths.SECRET_DIR_COMPONENTS
SECRET_DIR_SUFFIXES = paths.SECRET_DIR_SUFFIXES
SECRET_FILE_NAMES = paths.SECRET_FILE_NAMES
SECRET_FILE_SUFFIXES = paths.SECRET_FILE_SUFFIXES
parsed_request_path = paths.parsed_request_path
validate_request_path_lexical = paths.validate_request_path_lexical

MAX_DIRECTORY_ENTRIES = listing.MAX_DIRECTORY_ENTRIES
REPO_MARKERS = listing.REPO_MARKERS

MAX_BATCH_REQUESTS = 64
WATCH_SIGNATURE_CHILD_LIMIT = 512
BATCH_TRIGGER_LEGACY = "legacy"
BATCH_ALLOWED_TRIGGERS = frozenset({
    BATCH_TRIGGER_LEGACY,
    "tree-render",
    "explicit-user",
    "fresh-repair",
    "watch-diff",
    "watch-diff-fallback",
    "deferred-interaction",
    "repo-enrichment",
    "sync-revalidation",
})
BATCH_CLIENT_SCOPE_LEGACY = "legacy"
BATCH_ALLOWED_CLIENT_SCOPES = frozenset({"browser"})
BATCH_PATH_FINGERPRINT_LIMIT = 8
BATCH_CLIENT_REVISION_MAX_LENGTH = 80
BATCH_TRIGGER_COUNT_LIMIT = MAX_BATCH_REQUESTS

MAX_SEARCH_DIRS = search.MAX_SEARCH_DIRS
MAX_SEARCH_FILES = search.MAX_SEARCH_FILES
MAX_SEARCH_LIMIT = search.MAX_SEARCH_LIMIT
SEARCH_SECRET_EXCLUDE_SIGNATURE = search.SEARCH_SECRET_EXCLUDE_SIGNATURE
SEARCH_SKIP_DIRS = search.SEARCH_SKIP_DIRS

EXTENSIONLESS_TEXT_NAMES = io_ops.EXTENSIONLESS_TEXT_NAMES
IMAGE_EXTENSIONS = io_ops.IMAGE_EXTENSIONS
FS_ZIP_MAX_BYTES = io_ops.FS_ZIP_MAX_BYTES
MAX_RAW_BYTES = io_ops.MAX_RAW_BYTES
MAX_WRITE_BYTES = io_ops.MAX_WRITE_BYTES
MTIME_NS_CONFLICT_TOLERANCE = io_ops.MTIME_NS_CONFLICT_TOLERANCE
TEXT_EXTENSIONS = io_ops.TEXT_EXTENSIONS

_canonical_root = paths._canonical_root
_configured_fs_roots = paths._configured_fs_roots
_ensure_not_configured_root = paths._ensure_not_configured_root
_ensure_path_allowed = paths._ensure_path_allowed
_looks_binary = paths._looks_binary
_normalized_absolute_text_is_within = paths._normalized_absolute_text_is_within
_normalized_scope_path = paths._normalized_scope_path
_path_is_secret = paths._path_is_secret
_path_is_within = paths._path_is_within
_physical_file_identity = paths._physical_file_identity
_secret_directories = paths._secret_directories
_secret_exact_paths = paths._secret_exact_paths
_validated_path = paths._validated_path

_directory_is_repo = listing._directory_is_repo
_entry_info = listing._entry_info
_visible_directory_names = listing._visible_directory_names

_alnum_search_text = search._alnum_search_text
_annotate_search_dedupe_fields = search._annotate_search_dedupe_fields
_compact_search_text = search._compact_search_text
_doit_search_token = search._doit_search_token
_fuzzy_subsequence_match = search._fuzzy_subsequence_match
_fuzzy_subsequence_span = search._fuzzy_subsequence_span
_search_entry_sort_key = search._search_entry_sort_key
_search_file_entry = search._search_file_entry
_search_full_tree = search._search_full_tree
_search_limit = search._search_limit
_search_token_rank = search._search_token_rank

_BLAME_PR_RE = git_ops._BLAME_PR_RE
_BLAME_SHA_RE = git_ops._BLAME_SHA_RE
_blame_cache = git_ops._blame_cache
_diff_ref_resolution_error = git_ops._diff_ref_resolution_error
_diff_refs = git_ops.diff_refs
_normal_ref = git_ops.normal_ref
_parse_blame_porcelain = git_ops._parse_blame_porcelain
_refs_requested = git_ops.refs_requested

_mtime_matches_expected = io_ops._mtime_matches_expected
_sniff_raw_mime = io_ops._sniff_raw_mime
validated_child_name = io_ops.validated_child_name


def _sync_package_overrides() -> None:
    """Keep legacy package-level monkeypatches effective after the module split."""
    paths.AUTH_CONFIG_PATH = AUTH_CONFIG_PATH
    paths.AUTH_COOKIE_SECRET_PATH = AUTH_COOKIE_SECRET_PATH
    paths.CONFIG_DIR = CONFIG_DIR
    paths.BINARY_SNIFF_BYTES = BINARY_SNIFF_BYTES
    paths.DEFAULT_FS_ROOTS = DEFAULT_FS_ROOTS
    paths.FS_ROOTS_ENV = FS_ROOTS_ENV
    paths.MAX_READ_BYTES = MAX_READ_BYTES
    paths.SECRET_DIR_COMPONENTS = SECRET_DIR_COMPONENTS
    paths.SECRET_DIR_SUFFIXES = SECRET_DIR_SUFFIXES
    paths.SECRET_FILE_NAMES = SECRET_FILE_NAMES
    paths.SECRET_FILE_SUFFIXES = SECRET_FILE_SUFFIXES

    listing.MAX_DIRECTORY_ENTRIES = MAX_DIRECTORY_ENTRIES
    listing.REPO_MARKERS = REPO_MARKERS

    search.MAX_SEARCH_DIRS = MAX_SEARCH_DIRS
    search.MAX_SEARCH_FILES = MAX_SEARCH_FILES
    search.MAX_SEARCH_LIMIT = MAX_SEARCH_LIMIT
    search.SEARCH_SECRET_EXCLUDE_SIGNATURE = SEARCH_SECRET_EXCLUDE_SIGNATURE
    search.SEARCH_SKIP_DIRS = SEARCH_SKIP_DIRS
    search._search_full_tree = _search_full_tree

    io_ops.EXTENSIONLESS_TEXT_NAMES = EXTENSIONLESS_TEXT_NAMES
    io_ops.FS_ZIP_MAX_BYTES = FS_ZIP_MAX_BYTES
    io_ops.IMAGE_EXTENSIONS = IMAGE_EXTENSIONS
    io_ops.MAX_RAW_BYTES = MAX_RAW_BYTES
    io_ops.MAX_WRITE_BYTES = MAX_WRITE_BYTES
    io_ops.MTIME_NS_CONFLICT_TOLERANCE = MTIME_NS_CONFLICT_TOLERANCE
    io_ops.TEXT_EXTENSIONS = TEXT_EXTENSIONS


@normalize_os_errors
def list_directory(
    raw_path: str,
    *,
    performance_details: dict[str, float] | None = None,
    watch_signature_child_limit: int = 0,
    include_repo_info: bool = True,
) -> dict[str, Any]:
    _sync_package_overrides()
    result = listing.list_directory(
        raw_path,
        performance_details=performance_details,
        watch_signature_child_limit=watch_signature_child_limit,
        include_repo_info=include_repo_info,
    )
    # Item 6: a directory the user is viewing in Finder is concrete visibility evidence -- promote its
    # indexed root's frontier to user-visible-demand (debounced, non-blocking; never a second crawl).
    search.promote_visible_path(raw_path)
    return result


def validated_batch_requests(payload: dict[str, Any]) -> list[Any]:
    """Return the one bounded request list accepted by HTTP and jobd."""
    requests = payload.get("requests", [])
    if not isinstance(requests, list):
        raise ValueError("requests must be a list")
    if len(requests) > MAX_BATCH_REQUESTS:
        raise ValueError(f"requests must contain at most {MAX_BATCH_REQUESTS} items")
    return requests


def _batch_trigger_counts(item: dict[str, Any]) -> dict[str, int]:
    raw_trigger_counts = item.get("trigger_counts")
    if raw_trigger_counts is None:
        raw_trigger_counts = {str(item.get("trigger", BATCH_TRIGGER_LEGACY) or BATCH_TRIGGER_LEGACY): 1}
    item_trigger_counts: dict[str, int] = {}
    if not isinstance(raw_trigger_counts, dict) or not raw_trigger_counts:
        return item_trigger_counts
    for raw_trigger, raw_count in raw_trigger_counts.items():
        trigger = str(raw_trigger or "")
        if trigger not in BATCH_ALLOWED_TRIGGERS or isinstance(raw_count, bool):
            return {}
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            return {}
        if count < 1 or count > BATCH_TRIGGER_COUNT_LIMIT:
            return {}
        item_trigger_counts[trigger] = count
    return item_trigger_counts


def filesystem_batch_request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Build bounded privacy-safe diagnostics without touching the filesystem."""
    requests = validated_batch_requests(payload)
    raw_client_revision = str(payload.get("client_revision", "") or "")
    client_revision = (
        raw_client_revision
        if re.fullmatch(rf"[A-Za-z0-9._-]{{1,{BATCH_CLIENT_REVISION_MAX_LENGTH}}}", raw_client_revision)
        else ""
    )
    raw_client_scope = str(payload.get("client_scope", "") or "")
    client_scope = raw_client_scope if raw_client_scope in BATCH_ALLOWED_CLIENT_SCOPES else BATCH_CLIENT_SCOPE_LEGACY
    op_counts: dict[str, int] = {}
    trigger_counts: dict[str, int] = {}
    path_fingerprints: list[str] = []
    for item in requests:
        if not isinstance(item, dict):
            op_counts["invalid"] = op_counts.get("invalid", 0) + 1
            continue
        operation = str(item.get("type", item.get("op", "")) or "")
        safe_operation = operation if operation in {"list", "info"} else "invalid"
        op_counts[safe_operation] = op_counts.get(safe_operation, 0) + 1
        item_trigger_counts = _batch_trigger_counts(item)
        if not item_trigger_counts:
            trigger_counts["invalid"] = trigger_counts.get("invalid", 0) + 1
            continue
        for trigger, count in item_trigger_counts.items():
            trigger_counts[trigger] = trigger_counts.get(trigger, 0) + count
        raw_path = str(item.get("path", "") or "")
        if raw_path and len(path_fingerprints) < BATCH_PATH_FINGERPRINT_LIMIT:
            fingerprint = hashlib.sha256(raw_path.encode("utf-8", errors="replace")).hexdigest()[:16]
            if fingerprint not in path_fingerprints:
                path_fingerprints.append(fingerprint)
    return {
        "batch_size": len(requests),
        "operations": op_counts,
        "path_fingerprints": path_fingerprints,
        "triggers": trigger_counts,
        "client_revision": client_revision or "unknown",
        "client_scope": client_scope,
    }


def filesystem_batch_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute one typed, max-64 list/info product under the ACCEPTING server's access policy.

    A batch runs in a shared daemon, so it is authorized by the policy the payload carries, not by
    the daemon's own environment.  A payload without a parsable policy is refused here rather than
    executed with borrowed authority.
    """
    policy = paths.access_policy_from_descriptor(payload.get(paths.FS_ACCESS_POLICY_FIELD))
    with paths.enforce_access_policy(policy):
        return _filesystem_batch_result_authorized(payload)


def _filesystem_batch_result_authorized(payload: dict[str, Any]) -> dict[str, Any]:
    requests = validated_batch_requests(payload)
    summary = filesystem_batch_request_summary(payload)
    responses = []
    repo_info_cache: dict[str, dict[str, Any] | None] = {}
    list_operation_ms = 0.0
    info_operation_ms = 0.0
    list_performance_details: dict[str, float] = {}
    operation_started = time.perf_counter()
    for index, item in enumerate(requests):
        request_id = item.get("id", index) if isinstance(item, dict) else index
        if not isinstance(item, dict):
            responses.append(error_payload(
                "request must be an object",
                message_key="request.error.object",
                message_params={"field": "request"},
                id=request_id,
                ok=False,
                status=400,
            ))
            continue
        operation = str(item.get("type", item.get("op", "")) or "")
        raw_path = str(item.get("path", "") or "")
        if not _batch_trigger_counts(item):
            responses.append(error_payload(
                "invalid fs batch trigger",
                message_key="request.error.unsupportedFsBatchOperation",
                message_params={"operation": "trigger"},
                id=request_id,
                ok=False,
                status=400,
                path=raw_path,
            ))
            continue
        if operation not in {"list", "info"}:
            responses.append(error_payload(
                "unsupported fs batch operation",
                message_key="request.error.unsupportedFsBatchOperation",
                message_params={"operation": operation},
                id=request_id,
                ok=False,
                status=400,
                path=raw_path,
            ))
            continue
        try:
            item_started = time.perf_counter()
            item_watch_signature: tuple[Any, ...] | None = None
            if operation == "list":
                item_list_details: dict[str, float] = {}
                try:
                    include_watch_signature = item.get("include_watch_signature") is True
                    result = list_directory(
                        raw_path,
                        performance_details=item_list_details,
                        watch_signature_child_limit=(WATCH_SIGNATURE_CHILD_LIMIT if include_watch_signature else 0),
                        include_repo_info=item.get("include_repo_info") is not False,
                    )
                    if include_watch_signature:
                        item_watch_signature = result.pop("watch_signature", None)
                finally:
                    list_operation_ms += max(0.0, (time.perf_counter() - item_started) * 1000)
                    for key, value in item_list_details.items():
                        list_performance_details[key] = list_performance_details.get(key, 0.0) + value
            else:
                try:
                    result = path_info(
                        raw_path,
                        operation="fs_batch.info",
                        repo_info_cache=repo_info_cache,
                    )
                finally:
                    info_operation_ms += max(0.0, (time.perf_counter() - item_started) * 1000)
        except FilesystemError as exc:
            responses.append(exc.payload(id=request_id, ok=False, path=raw_path))
            continue
        response = {"id": request_id, "ok": True, "status": 200, "payload": result}
        if item_watch_signature is not None:
            response["watch_signature"] = item_watch_signature
        responses.append(response)
    return {
        "responses": responses,
        "performance": {
            **summary,
            "operation_ms": round(max(0.0, (time.perf_counter() - operation_started) * 1000), 3),
            "list_ms": round(list_operation_ms, 3),
            "info_ms": round(info_operation_ms, 3),
            "list_details": {
                key: round(value, 3) if key.endswith("_ms") else int(value)
                for key, value in list_performance_details.items()
            },
        },
    }


@normalize_os_errors
def watch_signature(raw_path: str, *, child_limit: int = 0) -> tuple[Any, ...]:
    _sync_package_overrides()
    return listing.watch_signature(raw_path, child_limit=child_limit)


@normalize_os_errors
def search_files(
    raw_root: str,
    query: str = "",
    limit: int | str | None = 400,
    recursive: bool = False,
    cursor: str | None = None,
) -> dict[str, Any]:
    _sync_package_overrides()
    return search.search_files(raw_root, query=query, limit=limit, recursive=recursive, cursor=cursor)


@normalize_os_errors
def index_status(raw_root: str) -> dict[str, Any]:
    _sync_package_overrides()
    return search.index_status(raw_root)


@normalize_os_errors
def unindex_root(raw_root: str) -> dict[str, Any]:
    _sync_package_overrides()
    return search.unindex_root(raw_root)


@normalize_os_errors
def reindex_roots_for_paths(raw_paths: list[str], reason: str = "filesystem-change") -> list[str]:
    _sync_package_overrides()
    return search.reindex_roots_for_paths(raw_paths, reason=reason)


@normalize_os_errors
def git_repo_info(repo: Path, include_status: bool = True) -> dict[str, Any]:
    _sync_package_overrides()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=flags) as handle:
        payload = git_ops.pinned_git_repo_info(
            handle,
            display_root=handle.resolved,
            include_status=include_status,
        )
        payload["root"] = str(handle.resolved)
        payload["name"] = handle.resolved.name
        return payload


@normalize_os_errors
def git_tracks_path(path: Path) -> bool:
    _sync_package_overrides()
    try:
        with paths.safe_path(str(path), flags=paths.metadata_descriptor_flags()) as handle:
            _repo, tracked, _history, _relative, _repo_info = git_ops.pinned_file_git_metadata(handle)
            return tracked
    except FilesystemError as error:
        if error.status == 404:
            return False
        raise


@normalize_os_errors
def git_file_history(path: Path, limit: int = 60) -> list[dict[str, Any]]:
    _sync_package_overrides()
    try:
        with paths.safe_path(str(path), flags=paths.metadata_descriptor_flags()) as handle:
            _repo, _tracked, history, _relative, _repo_info = git_ops.pinned_file_git_metadata(
                handle,
                history_limit=limit,
            )
            return history
    except FilesystemError as error:
        if error.status == 404:
            return []
        raise


@normalize_os_errors
def git_history(
    raw_path: str,
    limit: int | str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.git_history(raw_path, limit=limit, cursor=cursor)


@normalize_os_errors
def git_commit(raw_path: str, *, commit: str, head: str) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.git_commit(raw_path, commit=commit, head=head)


@normalize_os_errors
def diff_file(raw_path: str, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    payload = git_ops.diff_file(raw_path, from_ref=from_ref, to_ref=to_ref)
    # Item 6: a file open in the Differ is concrete visibility evidence for its indexed root.
    search.promote_visible_path(raw_path)
    return payload


@normalize_os_errors
def blame_file(raw_path: str, ref: str | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.blame_file(raw_path, ref=ref)


@normalize_os_errors
def git_root_for_path(path: Path) -> str:
    _sync_package_overrides()
    try:
        with paths.safe_path(str(path), flags=paths.metadata_descriptor_flags()) as handle:
            repo = git_ops._pinned_repo_root(handle)
    except FilesystemError as error:
        if error.status != 404:
            raise
        with paths.safe_parent(str(path)) as parent:
            repo = git_ops._pinned_repo_root(parent)
    return str(repo) if repo is not None else ""


@normalize_os_errors
def read_file(raw_path: str, *, include_git: bool = True) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.read_file(raw_path, include_git=include_git)


def _reindex_after_mutation(mutated_paths: list[Any], reason: str) -> list[str]:
    """Route a successful YOLOmux file mutation into the ONE hot-path index owner (item 6).

    A create/upsert/delete/rename/upload that changed the filesystem is concrete change evidence, so
    it must reach the index in seconds instead of waiting for the 1800s safety TTL. It goes through
    the same `search.reindex_roots_for_paths` owner watchd and the persistent indexer already use --
    which coalesces by indexed root and either promotes the pending frontier or runs one bounded
    subtree repair -- so `write`/`delete`/`mkdir`/upload stop bypassing the index. In an HTTP or jobd
    process (not the elected owner) this only marks paths dirty and dispatches a bounded RPC; the
    crawl runs in indexd, never on jobd's single interactive worker.
    """
    candidates = [str(path) for path in mutated_paths if path]
    if not candidates:
        return []
    return search.reindex_roots_for_paths(candidates, reason=reason)


@normalize_os_errors
def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    payload = io_ops.write_file(raw_path, content, expected_mtime=expected_mtime)
    paths.invalidate_path_policy_caches()
    # Covers editor saves too: an editor save reaches the filesystem through this one write funnel.
    payload["reindex_roots"] = _reindex_after_mutation([payload.get("path")], reason="fs-write")
    return payload


@normalize_os_errors
def delete_path(
    raw_path: str,
    *,
    recursive: bool = False,
    cancel_event: Any | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    _sync_package_overrides()
    try:
        payload = io_ops.delete_path(
            raw_path,
            recursive=recursive,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )
    except io_ops.PartialDeleteError as error:
        # Only observed removals invalidate policy/search state. A cancellation or expired deadline
        # before the first unlink is a non-terminal refusal, not a filesystem mutation.
        if error.deleted_paths:
            paths.invalidate_path_policy_caches()
            _reindex_after_mutation([raw_path, *error.deleted_paths], reason="fs-delete-partial")
        raise
    if payload.get("pending"):
        # A non-terminal probe deleted NOTHING.  Invalidating the path policy caches or fanning out
        # a reindex here would publish a filesystem change that never happened; both are terminal
        # side effects and fire only when the delete actually removed the entry.
        return payload
    paths.invalidate_path_policy_caches()
    payload["reindex_roots"] = _reindex_after_mutation([payload.get("path")], reason="fs-delete")
    return payload


@normalize_os_errors
def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    _sync_package_overrides()
    payload = io_ops.rename_path(raw_path, new_name)
    paths.invalidate_path_policy_caches()
    payload["reindex_roots"] = _reindex_after_mutation([payload["old_path"], payload["path"]], reason="fs-rename")
    return payload


@normalize_os_errors
def create_directory(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    payload = io_ops.create_directory(raw_path)
    paths.invalidate_path_policy_caches()
    payload["reindex_roots"] = _reindex_after_mutation([payload.get("path")], reason="fs-mkdir")
    return payload


@normalize_os_errors
def path_info(
    raw_path: str,
    *,
    operation: str = "path_info",
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
    include_git: bool = True,
) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.path_info(
        raw_path,
        operation=operation,
        repo_info_cache=repo_info_cache,
        include_git=include_git,
    )


@normalize_os_errors
def resolve_file_candidates(raw_paths: list[str]) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.resolve_file_candidates(raw_paths)


def is_text_path(raw_path: str) -> bool:
    _sync_package_overrides()
    return io_ops.is_text_path(raw_path)


@normalize_os_errors
def read_raw(raw_path: str, max_bytes: int | None = None) -> tuple[bytes, str]:
    _sync_package_overrides()
    return io_ops.read_raw(raw_path, max_bytes=max_bytes)


@normalize_os_errors
def copy_raw_to(raw_path: str, target: Any, max_bytes: int | None = None) -> tuple[int, str, str]:
    _sync_package_overrides()
    return io_ops.copy_raw_to(raw_path, target, max_bytes=max_bytes)


@normalize_os_errors
def zip_directory(raw_path: str, max_bytes: int | None = None):
    _sync_package_overrides()
    return io_ops.zip_directory(raw_path, max_bytes=max_bytes)


@normalize_os_errors
def zip_directory_to(raw_path: str, target: Any, max_bytes: int | None = None) -> tuple[int, str]:
    _sync_package_overrides()
    return io_ops.zip_directory_to(raw_path, target, max_bytes=max_bytes)


@normalize_os_errors
def count_directory_files(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.count_directory_files(raw_path)


__all__ = [
    "BINARY_SNIFF_BYTES",
    "DEFAULT_FS_ROOTS",
    "EXTENSIONLESS_TEXT_NAMES",
    "FS_ACCESS_POLICY_FIELD",
    "FS_ACCESS_POLICY_VERSION",
    "FilesystemAccessPolicy",
    "FilesystemError",
    "access_policy_descriptor",
    "access_policy_from_descriptor",
    "access_policy_refused",
    "active_access_policy",
    "authorized_fs_roots",
    "capture_access_policy",
    "enforce_access_policy",
    "FS_ZIP_MAX_BYTES",
    "FS_ROOTS_ENV",
    "IMAGE_EXTENSIONS",
    "MAX_DIRECTORY_ENTRIES",
    "MAX_BATCH_REQUESTS",
    "WATCH_SIGNATURE_CHILD_LIMIT",
    "MAX_RAW_BYTES",
    "MAX_READ_BYTES",
    "MAX_SEARCH_DIRS",
    "MAX_SEARCH_FILES",
    "MAX_SEARCH_LIMIT",
    "MAX_WRITE_BYTES",
    "MTIME_NS_CONFLICT_TOLERANCE",
    "SEARCH_SECRET_EXCLUDE_SIGNATURE",
    "SEARCH_SKIP_DIRS",
    "TEXT_EXTENSIONS",
    "blame_file",
    "count_directory_files",
    "create_directory",
    "delete_path",
    "diff_file",
    "git_file_history",
    "git_repo_info",
    "git_root_for_path",
    "git_tracks_path",
    "index_status",
    "is_text_path",
    "filesystem_batch_request_summary",
    "filesystem_batch_result",
    "list_directory",
    "path_info",
    "read_file",
    "read_raw",
    "reindex_roots_for_paths",
    "rename_path",
    "search_files",
    "unindex_root",
    "validated_batch_requests",
    "write_file",
    "zip_directory",
]

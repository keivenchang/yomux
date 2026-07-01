"""Filesystem browsing + read/write helpers for the File Explorer panel.

All raw path entry points validate through :mod:`yolomux_lib.filesystem.paths`.
The package-level names preserve the old ``yolomux_lib.filesystem`` import surface
while implementation lives in smaller modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import paths
from . import git_ops
from . import io_ops
from . import listing
from . import search

AUTH_CONFIG_PATH = paths.AUTH_CONFIG_PATH
AUTH_COOKIE_SECRET_PATH = paths.AUTH_COOKIE_SECRET_PATH
CONFIG_DIR = paths.CONFIG_DIR
BINARY_SNIFF_BYTES = paths.BINARY_SNIFF_BYTES
DEFAULT_FS_ROOTS = paths.DEFAULT_FS_ROOTS
FilesystemError = paths.FilesystemError
FS_ROOTS_ENV = paths.FS_ROOTS_ENV
MAX_READ_BYTES = paths.MAX_READ_BYTES
SECRET_DIR_COMPONENTS = paths.SECRET_DIR_COMPONENTS
SECRET_DIR_SUFFIXES = paths.SECRET_DIR_SUFFIXES
SECRET_FILE_NAMES = paths.SECRET_FILE_NAMES
SECRET_FILE_SUFFIXES = paths.SECRET_FILE_SUFFIXES

MAX_DIRECTORY_ENTRIES = listing.MAX_DIRECTORY_ENTRIES
REPO_MARKERS = listing.REPO_MARKERS

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
_normalized_scope_path = paths._normalized_scope_path
_path_is_secret = paths._path_is_secret
_path_is_within = paths._path_is_within
_physical_file_identity = paths._physical_file_identity
_secret_directories = paths._secret_directories
_secret_exact_paths = paths._secret_exact_paths
_validated_path = paths._validated_path

_directory_is_repo = listing._directory_is_repo
_entry_info = listing._entry_info
_repo_marker_is_real = listing._repo_marker_is_real
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
_diff_refs = git_ops._diff_refs
_ensure_ref_order = git_ops._ensure_ref_order
_git_blob_text = git_ops._git_blob_text
_git_mv_if_tracked = git_ops._git_mv_if_tracked
_normal_ref = git_ops._normal_ref
_parse_blame_porcelain = git_ops._parse_blame_porcelain
_ref_exists = git_ops._ref_exists
_refs_requested = git_ops._refs_requested

_fs_io_errors = io_ops._fs_io_errors
_mtime_matches_expected = io_ops._mtime_matches_expected
_sniff_raw_mime = io_ops._sniff_raw_mime
_validated_child_name = io_ops._validated_child_name


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


def list_directory(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return listing.list_directory(raw_path)


def search_files(raw_root: str, query: str = "", limit: int | str | None = 400, recursive: bool = False) -> dict[str, Any]:
    _sync_package_overrides()
    return search.search_files(raw_root, query=query, limit=limit, recursive=recursive)


def index_status(raw_root: str) -> dict[str, Any]:
    _sync_package_overrides()
    return search.index_status(raw_root)


def unindex_root(raw_root: str) -> dict[str, Any]:
    _sync_package_overrides()
    return search.unindex_root(raw_root)


def git_repo_info(repo: Path, include_status: bool = True) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.git_repo_info(repo, include_status=include_status)


def git_tracks_path(path: Path) -> bool:
    _sync_package_overrides()
    return git_ops.git_tracks_path(path)


def git_file_history(path: Path, limit: int = 60) -> list[dict[str, Any]]:
    _sync_package_overrides()
    return git_ops.git_file_history(path, limit=limit)


def diff_file(raw_path: str, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.diff_file(raw_path, from_ref=from_ref, to_ref=to_ref)


def blame_file(raw_path: str, ref: str | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    return git_ops.blame_file(raw_path, ref=ref)


def git_root_for_path(path: Path) -> str:
    _sync_package_overrides()
    return git_ops.git_root_for_path(path)


def read_file(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.read_file(raw_path)


def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.write_file(raw_path, content, expected_mtime=expected_mtime)


def delete_path(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.delete_path(raw_path)


def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    _sync_package_overrides()
    payload = io_ops.rename_path(raw_path, new_name)
    payload["reindex_roots"] = search.reindex_roots_for_path(payload["old_path"], reason="fs-rename")
    return payload


def create_directory(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.create_directory(raw_path)


def path_info(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.path_info(raw_path)


def is_text_path(raw_path: str) -> bool:
    _sync_package_overrides()
    return io_ops.is_text_path(raw_path)


def read_raw(raw_path: str, max_bytes: int | None = None) -> tuple[bytes, str]:
    _sync_package_overrides()
    return io_ops.read_raw(raw_path, max_bytes=max_bytes)


def zip_directory(raw_path: str, max_bytes: int | None = None):
    _sync_package_overrides()
    return io_ops.zip_directory(raw_path, max_bytes=max_bytes)


def count_directory_files(raw_path: str) -> dict[str, Any]:
    _sync_package_overrides()
    return io_ops.count_directory_files(raw_path)


__all__ = [
    "BINARY_SNIFF_BYTES",
    "DEFAULT_FS_ROOTS",
    "EXTENSIONLESS_TEXT_NAMES",
    "FilesystemError",
    "FS_ZIP_MAX_BYTES",
    "FS_ROOTS_ENV",
    "IMAGE_EXTENSIONS",
    "MAX_DIRECTORY_ENTRIES",
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
    "list_directory",
    "path_info",
    "read_file",
    "read_raw",
    "rename_path",
    "search_files",
    "unindex_root",
    "write_file",
    "zip_directory",
]

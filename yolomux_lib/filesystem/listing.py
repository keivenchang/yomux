"""Directory listing helpers for the File Explorer panel."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from . import paths
from .git_ops import git_repo_info

MAX_DIRECTORY_ENTRIES = 1_000
REPO_MARKERS = (".git", ".hg", ".svn", ".jj")


class _ResolvedDirectoryName(str):
    """Directory entry name carrying the canonical path from the security scan."""

    def __new__(cls, value: str, resolved: Path):
        instance = super().__new__(cls, value)
        instance.resolved = resolved
        return instance


def _repo_marker_is_real(marker_path: Path, marker: str) -> bool:
    if not marker_path.exists():
        return False
    if marker == ".git":
        return marker_path.is_file() or (marker_path / "HEAD").exists()
    if marker == ".hg":
        return (marker_path / "requires").exists() or (marker_path / "store").exists()
    if marker == ".svn":
        return (marker_path / "wc.db").exists() or (marker_path / "entries").exists()
    if marker == ".jj":
        return (marker_path / "repo").exists() or (marker_path / "working_copy").exists()
    return False


def _directory_is_repo(path: Path) -> bool:
    for marker in REPO_MARKERS:
        try:
            if _repo_marker_is_real(path / marker, marker):
                return True
        except OSError:
            continue
    return False


def _entry_info(
    path: Path,
    name: str,
    *,
    resolved: Path | None = None,
    repo_info_cache: dict[Path, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        st = path.lstat()
    except OSError as exc:
        return {"name": name, "kind": "error", "error": str(exc)}
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        try:
            target_st = path.stat()
            target_mode = target_st.st_mode
            kind = "dir" if stat.S_ISDIR(target_mode) else "file"
            size = target_st.st_size
        except OSError:
            kind = "symlink-broken"
            size = 0
    elif stat.S_ISDIR(mode):
        kind = "dir"
        size = 0
    elif stat.S_ISREG(mode):
        kind = "file"
        size = st.st_size
    else:
        kind = "other"
        size = st.st_size
    info = {
        "name": name,
        "kind": kind,
        "size": int(size),
        "mtime": int(st.st_mtime),
        "mtime_ns": int(st.st_mtime_ns),
        "is_symlink": stat.S_ISLNK(mode),
    }
    if stat.S_ISLNK(mode):
        # Surface where the link points so the Finder row can show "name -> target".
        try:
            info["symlink_target"] = os.readlink(path)
        except OSError:
            pass
    if kind == "dir":
        info["is_repo"] = _directory_is_repo(path)
        if info["is_repo"]:
            repo_key = resolved if resolved is not None else paths._normalized_scope_path(path)
            if repo_info_cache is None:
                info["repo"] = git_repo_info(path, include_status=False)
            else:
                repo = repo_info_cache.get(repo_key)
                if repo is None:
                    repo = git_repo_info(path, include_status=False)
                    repo_info_cache[repo_key] = repo
                info["repo"] = repo
    # The directory scan already canonicalized the entry for its secret-path check.  Its
    # target stat is the same stat needed for a symlink's physical identity.
    identity_stat = target_st if stat.S_ISLNK(mode) and kind != "symlink-broken" else st
    info.update(paths._physical_file_identity(path, resolved=resolved, stat_result=identity_stat))
    return info


def _visible_directory_names(path: Path) -> tuple[list[str], bool]:
    limit = max(1, int(MAX_DIRECTORY_ENTRIES))
    names: list[str] = []
    truncated = False
    with os.scandir(path) as entries:
        for entry in entries:
            name = entry.name
            entry_path = path / name
            resolved = paths._normalized_scope_path(entry_path)
            if paths._path_is_secret(entry_path, resolved=resolved):
                continue
            if len(names) >= limit:
                truncated = True
                break
            names.append(_ResolvedDirectoryName(name, resolved))
    return names, truncated


def list_directory(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if not path.is_dir():
        raise paths.FilesystemError.not_directory(path)
    names, truncated = _visible_directory_names(path)
    repo_info_cache: dict[Path, dict[str, Any]] = {}
    entries = []
    for name in names:
        entry_path = path / name
        # Security must be checked for every child after resolving symlinks.  Reuse that
        # canonical result for identity so a row does not resolve/stat the same entry twice.
        resolved = name.resolved if isinstance(name, _ResolvedDirectoryName) else None
        if resolved is None:
            # Retain the standalone helper contract for callers/tests which provide plain
            # names, while normal listings reuse the security scan's canonical result.
            resolved = paths._normalized_scope_path(entry_path)
            if paths._path_is_secret(entry_path, resolved=resolved):
                continue
        entries.append(_entry_info(entry_path, name, resolved=resolved, repo_info_cache=repo_info_cache))
    entries.sort(key=lambda entry: (entry.get("kind") != "dir", str(entry.get("name", "")).lower()))
    parent = str(path.parent) if str(path) != "/" else None
    return {
        "path": str(path),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "entry_limit": MAX_DIRECTORY_ENTRIES,
    }

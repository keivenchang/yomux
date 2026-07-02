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


def _entry_info(path: Path, name: str) -> dict[str, Any]:
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
            info["repo"] = git_repo_info(path, include_status=False)
    info.update(paths._physical_file_identity(path))
    return info


def _visible_directory_names(path: Path) -> tuple[list[str], bool]:
    limit = max(1, int(MAX_DIRECTORY_ENTRIES))
    names: list[str] = []
    truncated = False
    with os.scandir(path) as entries:
        for entry in entries:
            name = entry.name
            if paths._path_is_secret(path / name):
                continue
            if len(names) >= limit:
                truncated = True
                break
            names.append(name)
    return names, truncated


def list_directory(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if not path.is_dir():
        raise paths.FilesystemError.not_directory(path)
    try:
        names, truncated = _visible_directory_names(path)
        entries = [_entry_info(path / name, name) for name in names]
    except PermissionError as exc:
        raise paths.FilesystemError.os_error(exc, status=403) from exc
    entries.sort(key=lambda entry: (entry.get("kind") != "dir", str(entry.get("name", "")).lower()))
    parent = str(path.parent) if str(path) != "/" else None
    return {
        "path": str(path),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "entry_limit": MAX_DIRECTORY_ENTRIES,
    }

"""Filesystem browsing + read/write helpers for the File Explorer panel.

All paths are validated to be absolute, NUL/CRLF-free, and resolved against
the YOLOmux process's own permission view. Symlinks are followed (the user
chose to navigate to them). No sandbox root — the explorer's FS scope is "/"
(anywhere the YOLOmux user can read). Admin role is required for writes.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import Any

MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB cap on file read
MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB cap on file write
BINARY_SNIFF_BYTES = 8 * 1024  # bytes inspected for NUL when classifying

TEXT_EXTENSIONS = {
    ".rs", ".py", ".md", ".txt", ".json", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".scss", ".html", ".htm", ".xml", ".yaml", ".yml", ".toml",
    ".sh", ".bash", ".zsh", ".fish", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".go", ".rb", ".pl", ".lua", ".sql", ".env", ".cfg", ".ini", ".conf",
    ".log", ".gitignore", ".dockerignore", ".dockerfile",
}


class FilesystemError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _validated_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise FilesystemError("path is required")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FilesystemError("path contains illegal characters")
    if not raw.startswith("/") and not raw.startswith("~"):
        raise FilesystemError("path must be absolute")
    return Path(os.path.expanduser(raw))


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
    return {
        "name": name,
        "kind": kind,
        "size": int(size),
        "mtime": int(st.st_mtime),
        "is_symlink": stat.S_ISLNK(mode),
    }


def list_directory(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists():
        raise FilesystemError(f"path not found: {path}", status=404)
    if not path.is_dir():
        raise FilesystemError(f"not a directory: {path}", status=400)
    try:
        names = sorted(os.listdir(path), key=lambda n: (not (path / n).is_dir() if (path / n).exists() else True, n.lower()))
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    entries = [_entry_info(path / name, name) for name in names]
    parent = str(path.parent) if str(path) != "/" else None
    return {
        "path": str(path),
        "parent": parent,
        "entries": entries,
    }


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def read_file(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists():
        raise FilesystemError(f"path not found: {path}", status=404)
    if path.is_dir():
        raise FilesystemError(f"is a directory: {path}", status=400)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    if size > MAX_READ_BYTES:
        raise FilesystemError(f"file too large ({size} bytes; max {MAX_READ_BYTES})", status=413)
    try:
        with path.open("rb") as fh:
            raw = fh.read(MAX_READ_BYTES + 1)
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    if _looks_binary(raw):
        raise FilesystemError("file appears to be binary", status=415)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "size": int(size),
        "mtime": int(path.stat().st_mtime),
        "content": content,
        "extension": path.suffix.lower(),
        "is_text_extension": path.suffix.lower() in TEXT_EXTENSIONS,
    }


def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if path.exists() and path.is_dir():
        raise FilesystemError(f"is a directory: {path}", status=400)
    if not isinstance(content, str):
        raise FilesystemError("content must be a string", status=400)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise FilesystemError(f"content too large ({len(data)} bytes; max {MAX_WRITE_BYTES})", status=413)
    if expected_mtime is not None and path.exists():
        actual = int(path.stat().st_mtime)
        if actual != expected_mtime:
            raise FilesystemError(
                f"file changed on disk (expected mtime {expected_mtime}, got {actual})",
                status=409,
            )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(data)
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    return {
        "path": str(path),
        "size": len(data),
        "mtime": int(path.stat().st_mtime),
    }


def is_text_path(raw_path: str) -> bool:
    try:
        path = _validated_path(raw_path)
    except FilesystemError:
        return False
    return path.suffix.lower() in TEXT_EXTENSIONS

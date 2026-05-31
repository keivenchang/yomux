"""Filesystem browsing + read/write helpers for the File Explorer panel.

All paths are validated to be absolute, NUL/CRLF-free, and resolved against
the YOLOmux process's own permission view. Symlinks are followed (the user
chose to navigate to them). No sandbox root — the explorer's FS scope is "/"
(anywhere the YOLOmux user can read). Admin role is required for writes.
"""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from .common import git
from .common import run_cmd

MAX_READ_BYTES = 20 * 1024 * 1024  # 20 MB cap on file read
MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB cap on file write
BINARY_SNIFF_BYTES = 8 * 1024  # bytes inspected for NUL when classifying

TEXT_EXTENSIONS = {
    ".rs", ".py", ".md", ".txt", ".json", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".scss", ".html", ".htm", ".xml", ".yaml", ".yml", ".toml",
    ".sh", ".bash", ".zsh", ".fish", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".go", ".rb", ".pl", ".lua", ".sql", ".env", ".cfg", ".ini", ".conf",
    ".log", ".gitignore", ".dockerignore", ".dockerfile",
}

EXTENSIONLESS_TEXT_NAMES = {
    "dockerfile", "makefile", "license", "readme", "gemfile", "rakefile",
    "justfile", "procfile",
}

IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
}

MAX_RAW_BYTES = 20 * 1024 * 1024  # 20 MB cap on raw (image) reads
REPO_MARKERS = (".git", ".hg", ".svn", ".jj")
SEARCH_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".jj", ".cache", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", "target",
}
MAX_SEARCH_DIRS = 20_000
MAX_SEARCH_FILES = 50_000
MAX_SEARCH_LIMIT = 2_000


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
        "is_symlink": stat.S_ISLNK(mode),
    }
    if kind == "dir":
        info["is_repo"] = _directory_is_repo(path)
    return info


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


def _fuzzy_subsequence_match(query: str, text: str) -> bool:
    needle = "".join(str(query or "").lower().split())
    if not needle:
        return True
    position = 0
    haystack = str(text or "").lower()
    for char in needle:
        index = haystack.find(char, position)
        if index < 0:
            return False
        position = index + 1
    return True


def _search_limit(raw_limit: int | str | None) -> int:
    try:
        limit = int(raw_limit or 400)
    except (TypeError, ValueError):
        limit = 400
    return max(1, min(limit, MAX_SEARCH_LIMIT))


def search_files(raw_root: str, query: str = "", limit: int | str | None = 400) -> dict[str, Any]:
    root = _validated_path(raw_root)
    if not root.exists():
        raise FilesystemError(f"path not found: {root}", status=404)
    if not root.is_dir():
        raise FilesystemError(f"not a directory: {root}", status=400)
    max_results = _search_limit(limit)
    tokens = [token for token in str(query or "").split() if token]
    results: list[dict[str, Any]] = []
    visited_dirs = 0
    visited_files = 0
    truncated = False
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for current, dirs, files in walker:
            visited_dirs += 1
            if visited_dirs > MAX_SEARCH_DIRS:
                truncated = True
                dirs[:] = []
                break
            dirs[:] = sorted(
                [name for name in dirs if name not in SEARCH_SKIP_DIRS],
                key=str.lower,
            )
            for name in sorted(files, key=str.lower):
                visited_files += 1
                if visited_files > MAX_SEARCH_FILES:
                    truncated = True
                    dirs[:] = []
                    break
                path = Path(current) / name
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    rel = path.name
                haystack = f"{rel} {name}"
                if tokens and not all(_fuzzy_subsequence_match(token, haystack) for token in tokens):
                    continue
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                results.append({
                    "name": name,
                    "path": str(path),
                    "relative_path": rel,
                    "kind": "file",
                    "size": int(st.st_size),
                    "mtime": int(st.st_mtime),
                })
                if len(results) >= max_results:
                    truncated = True
                    dirs[:] = []
                    break
            if len(results) >= max_results or visited_files > MAX_SEARCH_FILES:
                break
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    return {
        "root": str(root),
        "query": str(query or ""),
        "limit": max_results,
        "truncated": truncated,
        "files": results,
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


def _validated_child_name(raw_name: str) -> str:
    if not isinstance(raw_name, str):
        raise FilesystemError("name must be a string")
    name = raw_name.strip()
    if not name:
        raise FilesystemError("name is required")
    if name in {".", ".."} or "/" in name or "\x00" in name or "\n" in name or "\r" in name:
        raise FilesystemError("name contains illegal characters")
    return name


def delete_path(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists() and not path.is_symlink():
        raise FilesystemError(f"path not found: {path}", status=404)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            kind = "dir"
        else:
            path.unlink()
            kind = "file"
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    return {"path": str(path), "deleted": True, "kind": kind}


def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists() and not path.is_symlink():
        raise FilesystemError(f"path not found: {path}", status=404)
    name = _validated_child_name(new_name)
    target = path.with_name(name)
    if target.exists() or target.is_symlink():
        raise FilesystemError(f"target already exists: {target}", status=409)
    try:
        path.rename(target)
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    return {"path": str(target), "old_path": str(path), "name": name}


def path_info(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists() and not path.is_symlink():
        raise FilesystemError(f"path not found: {path}", status=404)
    kind = "dir" if path.is_dir() else "file"
    repo_root = git_root_for_path(path)
    relative_path = ""
    repo_info: dict[str, Any] | None = None
    if repo_root:
        repo = Path(repo_root)
        try:
            relative_path = path.relative_to(repo).as_posix()
        except ValueError:
            relative_path = ""
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), timeout=1.0)
        status = git(["status", "--porcelain=v1"], cwd=str(repo), timeout=2.0)
        upstream = git(["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=str(repo), timeout=1.0)
        ahead = 0
        behind = 0
        if upstream.returncode == 0:
            counts = git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=str(repo), timeout=2.0)
            if counts.returncode == 0:
                parts = counts.stdout.split()
                if len(parts) >= 2:
                    try:
                        ahead = int(parts[0])
                        behind = int(parts[1])
                    except ValueError:
                        ahead = 0
                        behind = 0
        repo_info = {
            "root": str(repo),
            "name": repo.name,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
            "dirty_count": len(status.stdout.splitlines()) if status.returncode == 0 else None,
            "upstream": upstream.stdout.strip() if upstream.returncode == 0 else "",
            "ahead": ahead,
            "behind": behind,
        }
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "repo_root": repo_root,
        "relative_path": relative_path,
        "repo": repo_info,
    }


def diff_file(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    repo_root = git_root_for_path(path)
    if not repo_root:
        raise FilesystemError(f"not in a git repo: {path}", status=400)
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError:
        raise FilesystemError(f"path is outside repo: {path}", status=400)
    tracked = git(["ls-files", "--error-unmatch", "--", rel_path], cwd=str(repo), timeout=3.0)
    if tracked.returncode == 0:
        result = git(["diff", "HEAD", "--", rel_path], cwd=str(repo), timeout=5.0)
        untracked = False
    else:
        result = run_cmd(["git", "-C", str(repo), "diff", "--no-index", "--", "/dev/null", str(path)], timeout=5.0)
        untracked = True
    if result.returncode not in {0, 1}:
        message = (result.stderr or result.stdout or "git diff failed").strip()
        raise FilesystemError(message, status=500)
    diff = result.stdout or ""
    if len(diff.encode("utf-8", errors="replace")) > MAX_READ_BYTES:
        raise FilesystemError(f"diff too large (max {MAX_READ_BYTES})", status=413)
    return {
        "path": str(path),
        "repo": str(repo),
        "relative_path": rel_path,
        "diff": diff,
        "untracked": untracked,
    }


def git_root_for_path(path: Path) -> str:
    cwd = path if path.is_dir() else path.parent
    result = git(["rev-parse", "--show-toplevel"], cwd=str(cwd), timeout=1.0)
    if result.returncode != 0:
        return ""
    root = result.stdout.strip()
    return root if root.startswith("/") else ""


def is_text_path(raw_path: str) -> bool:
    try:
        path = _validated_path(raw_path)
    except FilesystemError:
        return False
    name = path.name.lower()
    return (
        path.suffix.lower() in TEXT_EXTENSIONS
        or name in TEXT_EXTENSIONS
        or name in EXTENSIONLESS_TEXT_NAMES
    )


def read_raw(raw_path: str) -> tuple[bytes, str]:
    """Return (bytes, mime_type) for a file. Used to stream images and other
    binary previews. Caller decides whether to serve based on extension."""
    path = _validated_path(raw_path)
    if not path.exists():
        raise FilesystemError(f"path not found: {path}", status=404)
    if path.is_dir():
        raise FilesystemError(f"is a directory: {path}", status=400)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    if size > MAX_RAW_BYTES:
        raise FilesystemError(f"file too large ({size} bytes; max {MAX_RAW_BYTES})", status=413)
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_RAW_BYTES + 1)
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    mime = IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
    return data, mime

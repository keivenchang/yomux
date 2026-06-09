"""Filesystem browsing + read/write helpers for the File Explorer panel.

All paths are validated to be absolute, NUL/CRLF-free, inside the configured
filesystem roots, and not under known credential paths. Symlinks are followed
for scope checks so a link inside an allowed tree cannot escape it.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from .common import AUTH_CONFIG_PATH
from .common import AUTH_COOKIE_SECRET_PATH
from .common import CONFIG_DIR
from .common import git
from .common import is_generated_upload_name
from .common import run_cmd
from . import file_index

MAX_READ_BYTES = 20 * 1024 * 1024  # 20 MB cap on file read
MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB cap on file write
BINARY_SNIFF_BYTES = 8 * 1024  # bytes inspected for NUL when classifying
# Save conflict detection compares the mtime captured when YOLOmux loaded the file with the mtime on
# disk at save time. Some filesystems and browser/JSON round trips cannot preserve nanosecond mtimes
# exactly: JavaScript Number cannot represent current epoch nanoseconds safely, and remote/synced
# filesystems can report tiny timestamp drift without changing content. The tolerance is deliberately
# small: 10 ms absorbs precision/rounding jitter like the observed 85 ns drift, while still treating
# normal editor/tool writes as real conflicts so YOLOmux does not overwrite newer disk content.
MTIME_NS_CONFLICT_TOLERANCE = 10_000_000

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
SEARCH_SECRET_EXCLUDE_SIGNATURE = "fs-secret-v1"
MAX_SEARCH_DIRS = 20_000
MAX_SEARCH_FILES = 50_000
MAX_SEARCH_LIMIT = 2_000
FS_ROOTS_ENV = "YOLOMUX_FS_ROOTS"
DEFAULT_FS_ROOTS = ("/",)
SECRET_DIR_COMPONENTS = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
})
SECRET_FILE_NAMES = frozenset({
    ".netrc",
    ".npmrc",
    ".pypirc",
})
SECRET_DIR_SUFFIXES = (
    (".config", "gh"),
    (".config", "git"),
)
SECRET_FILE_SUFFIXES = (
    (".config", "gitlab-token"),
    (".cache", "huggingface", "token"),
    (".docker", "config.json"),
    (".ngc", "config"),
)


class FilesystemError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@contextlib.contextmanager
def _fs_io_errors():
    """Map a filesystem IO failure to a FilesystemError: PermissionError -> 403, other OSError -> 500.

    Wraps the read/write/delete/rename/mkdir call sites that all shared this exact except-pair. Only the
    sites that distinguish PermissionError from other OSErrors use this; the plain ``stat()`` probes keep
    their OSError -> 500 mapping inline (a PermissionError there is intentionally a 500, not a 403).
    """
    try:
        yield
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403) from exc
    except OSError as exc:
        raise FilesystemError(str(exc), status=500) from exc


def _validated_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise FilesystemError("path is required")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FilesystemError("path contains illegal characters")
    if not raw.startswith("/") and not raw.startswith("~"):
        raise FilesystemError("path must be absolute")
    path = Path(os.path.expanduser(raw))
    _ensure_path_allowed(path)
    return path


def _canonical_root(path: Path) -> Path:
    """Use the real directory as a search/index root so symlink aliases don't duplicate results."""
    return _normalized_scope_path(path)


def _normalized_scope_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _configured_fs_roots() -> tuple[Path, ...]:
    raw = os.environ.get(FS_ROOTS_ENV, "")
    values = [item for item in raw.split(os.pathsep) if item.strip()] if raw else list(DEFAULT_FS_ROOTS)
    roots: list[Path] = []
    for value in values:
        try:
            root = _normalized_scope_path(Path(os.path.expanduser(value.strip())))
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _secret_exact_paths() -> tuple[Path, ...]:
    home = Path.home()
    return tuple(_normalized_scope_path(path) for path in (
        AUTH_CONFIG_PATH,
        AUTH_COOKIE_SECRET_PATH,
        CONFIG_DIR / "auth.yaml",
        CONFIG_DIR / "auth-cookie-secret",
        home / ".config" / "gitlab-token",
        home / ".cache" / "huggingface" / "token",
        home / ".docker" / "config.json",
        home / ".ngc" / "config",
    ))


def _secret_directories() -> tuple[Path, ...]:
    home = Path.home()
    return tuple(_normalized_scope_path(path) for path in (
        home / ".ssh",
        home / ".gnupg",
        home / ".config" / "gh",
        home / ".config" / "git",
        home / ".docker",
        home / ".ngc",
    ))


def _path_is_secret(path: Path) -> bool:
    resolved = _normalized_scope_path(path)
    if any(resolved == secret for secret in _secret_exact_paths()):
        return True
    if any(resolved == secret or _path_is_within(resolved, secret) for secret in _secret_directories()):
        return True
    parts = resolved.parts
    if any(part in SECRET_DIR_COMPONENTS for part in parts):
        return True
    if resolved.name in SECRET_FILE_NAMES:
        return True
    for suffix in SECRET_DIR_SUFFIXES:
        size = len(suffix)
        for index in range(0, len(parts) - size + 1):
            if parts[index:index + size] == suffix:
                return True
    for suffix in SECRET_FILE_SUFFIXES:
        size = len(suffix)
        if size <= len(parts) and parts[-size:] == suffix:
            return True
    return False


def _ensure_path_allowed(path: Path) -> None:
    resolved = _normalized_scope_path(path)
    roots = _configured_fs_roots()
    if not roots or not any(resolved == root or _path_is_within(resolved, root) for root in roots):
        roots_text = ", ".join(str(root) for root in roots) or "(none)"
        raise FilesystemError(f"path outside configured filesystem roots: {path} (allowed: {roots_text})", status=403)
    if _path_is_secret(path):
        raise FilesystemError(f"path is blocked because it may contain credentials: {path}", status=403)


def _ensure_not_configured_root(path: Path, action: str) -> None:
    resolved = _normalized_scope_path(path)
    if resolved == Path("/"):
        raise FilesystemError(f"refusing to {action} filesystem root", status=403)
    for root in _configured_fs_roots():
        if resolved == root:
            raise FilesystemError(f"refusing to {action} configured filesystem root: {path}", status=403)


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


def git_repo_info(repo: Path, include_status: bool = True) -> dict[str, Any]:
    branch = git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=str(repo), timeout=1.0)
    if branch.returncode != 0:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), timeout=1.0)
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
    dirty_count: int | None = None
    if include_status:
        status = git(["status", "--porcelain=v1"], cwd=str(repo), timeout=2.0)
        dirty_count = len(status.stdout.splitlines()) if status.returncode == 0 else None
    return {
        "root": str(repo),
        "name": repo.name,
        "branch": branch.stdout.strip() if branch.returncode == 0 else "",
        "dirty_count": dirty_count,
        "upstream": upstream.stdout.strip() if upstream.returncode == 0 else "",
        "ahead": ahead,
        "behind": behind,
    }


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
        # DOIT.31: surface where the link points so the Finder row can show "name → target".
        try:
            info["symlink_target"] = os.readlink(path)
        except OSError:
            pass
    if kind == "dir":
        info["is_repo"] = _directory_is_repo(path)
        if info["is_repo"]:
            info["repo"] = git_repo_info(path, include_status=False)
    return info


def _visible_directory_names(path: Path) -> list[str]:
    return [name for name in os.listdir(path) if not _path_is_secret(path / name)]


def list_directory(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists():
        raise FilesystemError(f"path not found: {path}", status=404)
    if not path.is_dir():
        raise FilesystemError(f"not a directory: {path}", status=400)
    try:
        entries = [_entry_info(path / name, name) for name in _visible_directory_names(path)]
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    entries.sort(key=lambda entry: (entry.get("kind") != "dir", str(entry.get("name", "")).lower()))
    parent = str(path.parent) if str(path) != "/" else None
    return {
        "path": str(path),
        "parent": parent,
        "entries": entries,
    }


def _fuzzy_subsequence_match(query: str, text: str) -> bool:
    return _fuzzy_subsequence_span(query, text) is not None


def _compact_search_text(value: str) -> str:
    return "".join(str(value or "").lower().split())


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
    path_text = _compact_search_text(str(path))

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
    span = _fuzzy_subsequence_span(needle, path_text)
    if span is not None:
        return 170 + rel.count("/") * 4 + span
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
        "realpath": os.path.realpath(path),
        "relative_path": rel,
        "kind": "file",
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
        "uploaded": is_generated_upload_name(path),
        "_sort_key": sort_key,
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


def _search_full_tree(root: Path, search_root: Path, tokens: list[str], results: list[dict[str, Any]]) -> tuple[int, int, bool]:
    visited_dirs = 0
    visited_files = 0
    truncated = False
    walker = os.walk(search_root, topdown=True, followlinks=False)
    for current, dirs, files in walker:
        visited_dirs += 1
        if visited_dirs > MAX_SEARCH_DIRS:
            truncated = True
            dirs[:] = []
            break
        dirs[:] = sorted(
            [name for name in dirs if name not in SEARCH_SKIP_DIRS and not _path_is_secret(Path(current) / name)],
            key=str.lower,
        )
        for name in sorted(files, key=str.lower):
            visited_files += 1
            if visited_files > MAX_SEARCH_FILES:
                truncated = True
                dirs[:] = []
                break
            path = Path(current) / name
            if _path_is_secret(path):
                continue
            entry = _search_file_entry(root, path, tokens)
            if entry is not None:
                results.append(entry)
        if visited_files > MAX_SEARCH_FILES:
            break
    return visited_dirs, visited_files, truncated


def search_files(raw_root: str, query: str = "", limit: int | str | None = 400, recursive: bool = False) -> dict[str, Any]:
    root = _canonical_root(_validated_path(raw_root))
    if not root.exists():
        raise FilesystemError(f"path not found: {root}", status=404)
    if not root.is_dir():
        raise FilesystemError(f"not a directory: {root}", status=400)
    max_results = _search_limit(limit)
    tokens = [token for token in str(query or "").split() if token]
    full_tree = bool(recursive) or bool(git_root_for_path(root))
    if full_tree:
        # Accelerate full-tree quick-open with the persistent index: it covers the
        # whole tree (no 20k/50k walk cap) and needs no per-query walk. Warm/refresh
        # it in the background; until it is ready we fall back to the live walk below
        # (stale-while-revalidate), so search never blocks on indexing.
        index = file_index.ensure_index(
            root,
            SEARCH_SKIP_DIRS,
            exclude_path=_path_is_secret,
            exclude_signature=SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        if tokens and index.ready:
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
            indexed_results, indexed_truncated = file_index.search_index(index, _match, max_results)
            for entry in indexed_results:
                entry.pop("_sort_key", None)
                # Annotate the (capped) results with realpath + size so the client can dedupe symlink
                # overlaps and content-mirror copies. Bounded to <= max_results, so the stat is cheap.
                _annotate_search_dedupe_fields(entry)
            return {
                "root": str(root),
                "root_realpath": os.path.realpath(root),
                "query": str(query or ""),
                "limit": max_results,
                "truncated": indexed_truncated,
                "files": indexed_results,
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
            if index.ready:
                recent_results, recent_truncated = file_index.recent_entries(index, max_results, _recent)
                for entry in recent_results:
                    _annotate_search_dedupe_fields(entry)
                return {
                    "root": str(root),
                    "root_realpath": os.path.realpath(root),
                    "query": "",
                    "limit": max_results,
                    "truncated": recent_truncated,
                    "files": recent_results,
                    "index_state": "ready",
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
    try:
        if full_tree:
            visited_dirs, visited_files, truncated = _search_full_tree(root, root, tokens, results)
        else:
            visited_dirs = 1
            direct_names = sorted(os.listdir(root), key=str.lower)
            for name in direct_names:
                path = root / name
                if name in SEARCH_SKIP_DIRS or _path_is_secret(path):
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
    except PermissionError as exc:
        raise FilesystemError(str(exc), status=403)
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
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
    """Warm the persistent quick-open index for a root (kick the background build if missing/stale)
    and report its build state, so the client can eagerly index and show a stable indexing/indexed
    badge instead of paying a cold live walk on the first query."""
    root = _canonical_root(_validated_path(raw_root))
    if not root.is_dir():
        raise FilesystemError(f"not a directory: {root}", status=400)
    index = file_index.ensure_index(
        root,
        SEARCH_SKIP_DIRS,
        exclude_path=_path_is_secret,
        exclude_signature=SEARCH_SECRET_EXCLUDE_SIGNATURE,
    )
    with index.lock:
        ready = bool(index.ready)
        building = bool(index.building)
        built_at = float(index.built_at or 0.0)
        count = len(index.entries)
        truncated = bool(index.truncated)
    # C11: report the real state so the Finder badge shows indexing/indexed honestly instead of guessing
    # (which made the badge flicker). `state` is the single field the UI keys on.
    state = "ready" if ready else ("building" if building else "missing")
    return {
        "root": str(root),
        "root_realpath": os.path.realpath(root),
        "building": building,
        "ready": ready,
        "count": count,
        "built_at": built_at,
        "age": (time.time() - built_at) if built_at else None,
        "truncated": truncated,
        "state": state,
    }


def unindex_root(raw_root: str) -> dict[str, Any]:
    """Drop the persistent quick-open index for a root (cancel any build, free memory + on-disk)."""
    root = _canonical_root(_validated_path(raw_root))
    file_index.unindex(root)
    return {"root": str(root), "ok": True}


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def git_tracks_path(path: Path) -> bool:
    """True when `path` is a file tracked by git (committed or staged). Untracked
    working-tree files and files outside any repo both return False. The editor's
    blame and diff buttons use this to stay hidden for files with no git history."""
    if path.is_dir():
        return False
    # ls-files pathspec is resolved relative to cwd (the file's parent), so `name`
    # is enough; returncode is non-zero both when untracked AND when not in a repo.
    result = git(["ls-files", "--error-unmatch", "--", path.name], cwd=str(path.parent), timeout=1.5)
    return result.returncode == 0


def git_file_history(path: Path, limit: int = 60) -> list[dict[str, Any]]:
    if path.is_dir():
        return []
    repo_root = git_root_for_path(path)
    if not repo_root:
        return []
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError:
        return []
    result = git([
        "log",
        "--follow",
        f"--max-count={max(1, min(int(limit), 100))}",
        "--format=%H%x1f%h%x1f%s%x1f%ct%x1f%an",
        "--",
        rel_path,
    ], cwd=str(repo), timeout=3.0)
    if result.returncode != 0:
        return []
    history: list[dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        full, short, subject, date, author = (line.split("\x1f") + ["", "", "", "", ""])[:5]
        if not full:
            continue
        try:
            date_value = int(date)
        except ValueError:
            date_value = 0
        history.append({
            "ref": full,
            "short": short or full[:9],
            "subject": subject,
            "date": date_value,
            "author": author,
        })
    return history


def read_file(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if not path.exists():
        raise FilesystemError(f"path not found: {path}", status=404)
    if path.is_dir():
        raise FilesystemError(f"is a directory: {path}", status=400)
    try:
        file_stat = path.stat()
        size = file_stat.st_size
    except OSError as exc:
        raise FilesystemError(str(exc), status=500)
    if size > MAX_READ_BYTES:
        raise FilesystemError(f"file too large ({size} bytes; max {MAX_READ_BYTES})", status=413)
    with _fs_io_errors():
        with path.open("rb") as fh:
            raw = fh.read(MAX_READ_BYTES + 1)
    if _looks_binary(raw):
        raise FilesystemError("file appears to be binary", status=415)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    git_root = git_root_for_path(path)
    git_tracked = git_tracks_path(path) if git_root else False
    git_history = git_file_history(path) if git_tracked else []
    return {
        "path": str(path),
        "size": int(size),
        "mtime": int(file_stat.st_mtime),
        "mtime_ns": int(file_stat.st_mtime_ns),
        "content": content,
        "extension": path.suffix.lower(),
        "is_text_extension": path.suffix.lower() in TEXT_EXTENSIONS,
        "git_root": git_root,
        "git_tracked": git_tracked,
        "git_history": git_history,
        "git_has_history": len(git_history) > 1,
    }


def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    path = _validated_path(raw_path)
    _ensure_not_configured_root(path, "write")
    if path.exists() and path.is_dir():
        raise FilesystemError(f"is a directory: {path}", status=400)
    if not isinstance(content, str):
        raise FilesystemError("content must be a string", status=400)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise FilesystemError(f"content too large ({len(data)} bytes; max {MAX_WRITE_BYTES})", status=413)
    if expected_mtime is not None and path.exists():
        actual_stat = path.stat()
        actual = int(actual_stat.st_mtime_ns)
        actual_legacy = int(actual_stat.st_mtime)
        if not _mtime_matches_expected(int(expected_mtime), actual, actual_legacy):
            raise FilesystemError(
                f"file changed on disk (expected mtime {expected_mtime}, got {actual})",
                status=409,
            )
    with _fs_io_errors():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(data)
    file_stat = path.stat()
    return {
        "path": str(path),
        "size": len(data),
        "mtime": int(file_stat.st_mtime),
        "mtime_ns": int(file_stat.st_mtime_ns),
    }


def _mtime_matches_expected(expected: int, actual_ns: int, actual_legacy: int) -> bool:
    if expected == actual_legacy:
        return True
    return abs(expected - actual_ns) <= MTIME_NS_CONFLICT_TOLERANCE


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
    _ensure_not_configured_root(path, "delete")
    if not path.exists() and not path.is_symlink():
        raise FilesystemError(f"path not found: {path}", status=404)
    with _fs_io_errors():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            kind = "dir"
        else:
            path.unlink()
            kind = "file"
    return {"path": str(path), "deleted": True, "kind": kind}


def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    _ensure_not_configured_root(path, "rename")
    if not path.exists() and not path.is_symlink():
        raise FilesystemError(f"path not found: {path}", status=404)
    name = _validated_child_name(new_name)
    target = path.with_name(name)
    if target.exists() or target.is_symlink():
        raise FilesystemError(f"target already exists: {target}", status=409)
    with _fs_io_errors():
        path.rename(target)
    return {"path": str(target), "old_path": str(path), "name": name}


def create_directory(raw_path: str) -> dict[str, Any]:
    path = _validated_path(raw_path)
    if path.exists() or path.is_symlink():
        raise FilesystemError(f"target already exists: {path}", status=409)
    with _fs_io_errors():
        path.mkdir()
    return {"path": str(path), "created": True, "kind": "dir"}


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
        repo_info = git_repo_info(repo, include_status=True)
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "repo_root": repo_root,
        "relative_path": relative_path,
        "repo": repo_info,
    }


def _git_blob_text(repo: Path, ref: str, rel_path: str, label: str) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{rel_path}"],
        capture_output=True,
        timeout=5.0,
        check=False,
    )
    if result.returncode != 0:
        return "", ""
    if len(result.stdout) > MAX_READ_BYTES:
        raise FilesystemError(f"{label} too large (max {MAX_READ_BYTES})", status=413)
    if _looks_binary(result.stdout):
        return "", f"{label} file appears to be binary"
    return result.stdout.decode("utf-8", errors="replace"), ""


def _normal_ref(value: str | None, default: str) -> str:
    ref = str(value or "").strip()
    return ref or default


def _diff_refs(raw_from_ref: str | None, raw_to_ref: str | None) -> tuple[str, str]:
    return _normal_ref(raw_from_ref, "HEAD"), _normal_ref(raw_to_ref, "current")


def _refs_requested(from_ref: str | None, to_ref: str | None) -> bool:
    return bool((from_ref or "").strip() or (to_ref or "").strip())


def _diff_ref_resolution_error(error: Exception) -> bool:
    message = str(error)
    return (
        message.startswith("unknown FROM ref:")
        or message.startswith("unknown TO ref:")
        or message.startswith("FROM ref must be older than TO ref")
    )


def _ref_exists(repo: Path, ref: str) -> bool:
    result = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(repo), timeout=3.0)
    return result.returncode == 0


def _ensure_ref_order(repo: Path, from_ref: str, to_ref: str) -> None:
    if to_ref == "current":
        if from_ref == "current":
            raise FilesystemError("FROM ref must be older than TO ref (current is the working tree)", status=400)
        if not _ref_exists(repo, from_ref):
            raise FilesystemError(f"unknown FROM ref: {from_ref}", status=400)
        return
    if from_ref == "current":
        raise FilesystemError("FROM ref must be older than TO ref (current is the working tree)", status=400)
    if not _ref_exists(repo, from_ref):
        raise FilesystemError(f"unknown FROM ref: {from_ref}", status=400)
    if not _ref_exists(repo, to_ref):
        raise FilesystemError(f"unknown TO ref: {to_ref}", status=400)
    order = git(["merge-base", "--is-ancestor", from_ref, to_ref], cwd=str(repo), timeout=5.0)
    if order.returncode != 0:
        raise FilesystemError(f"FROM ref must be older than TO ref ({from_ref} is not an ancestor of {to_ref})", status=400)


def diff_file(raw_path: str, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
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
    diff_from, diff_to = _diff_refs(from_ref, to_ref)
    if not (diff_to == "current" and tracked.returncode != 0):
        try:
            _ensure_ref_order(repo, diff_from, diff_to)
        except FilesystemError as error:
            if not (_refs_requested(from_ref, to_ref) and _diff_ref_resolution_error(error)):
                raise
            diff_from, diff_to = _diff_refs(None, None)
            _ensure_ref_order(repo, diff_from, diff_to)
    original = ""
    original_error = ""
    working = ""
    working_error = ""
    if diff_to == "current" and tracked.returncode != 0:
        result = run_cmd(["git", "-C", str(repo), "diff", "--no-index", "--", "/dev/null", str(path)], timeout=5.0)
        untracked = True
    else:
        if diff_to == "current":
            result = git(["diff", diff_from, "--", rel_path], cwd=str(repo), timeout=5.0)
            if tracked.returncode == 0:
                original, original_error = _git_blob_text(repo, diff_from, rel_path, "original")
        else:
            result = git(["diff", diff_from, diff_to, "--", rel_path], cwd=str(repo), timeout=5.0)
            original, original_error = _git_blob_text(repo, diff_from, rel_path, "original")
            working, working_error = _git_blob_text(repo, diff_to, rel_path, "working")
        untracked = False
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
        "original": original,
        "original_error": original_error,
        "working": working,
        "working_error": working_error,
        "working_missing": not path.exists(),
        "from_ref": diff_from,
        "to_ref": diff_to,
        "untracked": untracked,
    }


# DOIT.26: inline git blame for the editor. PR number is extracted from the commit summary the same
# way the metadata code does (`(#1234)`). Cached per (path, HEAD sha, file mtime, ref) — blame is the
# expensive call, and it only changes when the file or HEAD moves.
_BLAME_PR_RE = re.compile(r"\(#(\d+)\)")
_BLAME_SHA_RE = re.compile(r"[0-9a-f]{40}")
_blame_cache: dict[tuple[str, str, int, str], dict[str, Any]] = {}


def _parse_blame_porcelain(text: str) -> dict[str, dict[str, Any]]:
    """Parse `git blame --line-porcelain` → {final_line(str): {sha, author, time, summary, pr}}.
    Commit headers (author/summary/author-time) appear once per commit (first line), so they are
    accumulated per-sha and reused for that commit's subsequent lines."""
    lines: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    cur_sha = ""
    final_line: int | None = None
    for raw in text.split("\n"):
        if not raw:
            continue
        if raw[0] == "\t":  # the content line closes the current line's blame block
            if final_line is not None:
                info = meta.get(cur_sha, {})
                uncommitted = cur_sha == "0" * 40
                summary = info.get("summary", "")
                pr = _BLAME_PR_RE.search(summary)
                lines[str(final_line)] = {
                    "sha": cur_sha,
                    "author": "You" if uncommitted else info.get("author", ""),
                    "time": int(time.time()) if uncommitted else info.get("author_time", 0),
                    "summary": "Uncommitted changes" if uncommitted else summary,
                    "pr": int(pr.group(1)) if pr else None,
                }
            continue
        parts = raw.split(" ", 3)
        if parts and _BLAME_SHA_RE.fullmatch(parts[0]) and len(parts) >= 3:
            cur_sha = parts[0]
            final_line = int(parts[2])
            meta.setdefault(cur_sha, {})
        elif raw.startswith("author "):
            meta.setdefault(cur_sha, {})["author"] = raw[len("author "):]
        elif raw.startswith("author-time "):
            with contextlib.suppress(ValueError):
                meta.setdefault(cur_sha, {})["author_time"] = int(raw[len("author-time "):])
        elif raw.startswith("summary "):
            meta.setdefault(cur_sha, {})["summary"] = raw[len("summary "):]
    return lines


def blame_file(raw_path: str, ref: str | None = None) -> dict[str, Any]:
    path = _validated_path(raw_path)
    repo_root = git_root_for_path(path)
    if not repo_root:
        return {"path": str(path), "repo": "", "relative_path": "", "in_repo": False, "lines": {}}
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError:
        raise FilesystemError(f"path is outside repo: {path}", status=400)
    head = git(["rev-parse", "HEAD"], cwd=str(repo), timeout=1.0)
    head_sha = (head.stdout or "").strip() if head.returncode == 0 else ""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    use_ref = ref if (ref and ref not in {"current", "working", "HEAD", ""}) else ""
    cache_key = (str(path), head_sha, mtime_ns, use_ref)
    cached = _blame_cache.get(cache_key)
    if cached is not None:
        return cached
    args = ["blame", "--line-porcelain"]
    if use_ref:
        args.append(use_ref)
    args += ["--", rel_path]
    result = git(args, cwd=str(repo), timeout=3.0)
    if result.returncode != 0:
        # File not committed yet (or blame failed) → no annotation; surface a hint, don't error the page.
        return {"path": str(path), "repo": str(repo), "relative_path": rel_path, "in_repo": True,
                "lines": {}, "error": (result.stderr or "not committed yet").strip()}
    payload = {"path": str(path), "repo": str(repo), "relative_path": rel_path, "head": head_sha,
               "in_repo": True, "lines": _parse_blame_porcelain(result.stdout or "")}
    if len(_blame_cache) > 64:
        _blame_cache.clear()
    _blame_cache[cache_key] = payload
    return payload


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
    with _fs_io_errors():
        with path.open("rb") as fh:
            data = fh.read(MAX_RAW_BYTES + 1)
    mime = IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
    return data, mime

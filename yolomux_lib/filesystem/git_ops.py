"""Git-backed filesystem operations."""

from __future__ import annotations

import contextlib
import copy
import difflib
import hashlib
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from typing import Callable

from ..common import git
from ..common import git_bytes
from ..tmux.tmux_utils import cmd_error
from . import paths

_COMMON_GIT = git


def _git_with_pinned_repo(
    repo: paths.SafePathHandle,
    args: list[str],
    *,
    timeout: float,
    binary: bool = False,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[Any]:
    descriptors = tuple(dict.fromkeys((repo.descriptor, *pass_fds)))
    return subprocess.run(
        ["git", "-C", str(repo.descriptor_path()), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
        text=not binary,
        pass_fds=descriptors,
    )


def _git_at_path(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    if git is not _COMMON_GIT:
        return git(args, cwd=str(cwd), timeout=timeout)
    descriptor = None
    if cwd.parent in {Path("/proc/self/fd"), Path("/dev/fd")}:
        try:
            descriptor = int(cwd.name)
        except ValueError:
            descriptor = None
    if descriptor is None:
        return git(args, cwd=str(cwd), timeout=timeout)
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
        text=True,
        pass_fds=(descriptor,),
    )


def _pinned_repo_root(
    handle: paths.SafePathHandle | paths.SafeParentHandle,
    *,
    operation: str = "",
) -> Path | None:
    candidate = handle.resolved if stat.S_ISDIR(handle.stat_result.st_mode) else handle.resolved.parent
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    while True:
        try:
            with paths.safe_path(str(candidate), flags=directory_flags, operation=operation) as directory:
                marker = os.stat(".git", dir_fd=directory.descriptor, follow_symlinks=False)
        except (paths.FilesystemError, OSError):
            marker = None
        if marker is not None and (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)):
            return candidate
        if candidate == candidate.parent:
            return None
        candidate = candidate.parent


@contextlib.contextmanager
def pinned_repo_path(handle: paths.SafePathHandle, *, operation: str = ""):
    """Keep the repository generation for one pinned file live through its Git operations."""

    repo = _pinned_repo_root(handle, operation=operation)
    if repo is None:
        yield None
        return
    try:
        rel_path = handle.resolved.relative_to(repo).as_posix()
    except ValueError:
        yield None
        return
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=directory_flags, operation=operation) as repo_handle:
        yield repo, rel_path, repo_handle


def git_branch_name(
    repo: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Return the checked-out branch, or an empty string for a detached HEAD."""
    run = runner or (lambda args: git(args, cwd=str(repo), timeout=1.0))
    branch = run(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if branch.returncode != 0:
        branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    name = branch.stdout.strip() if branch.returncode == 0 else ""
    return "" if name == "HEAD" else name


# A repository whose HEAD identity cannot be read -- not a repository, an unborn repository with
# no commit yet, or a Git failure -- gets this typed sentinel.  It is deliberately UNIQUE and can
# never equal a valid ``(symbolic_head, oid)`` signature, so a malformed read for ONE repository
# neither advances its own generation nor can be mistaken for another repository's state.  The
# leading NUL keeps it out of the space of real branch names and object IDs.
REPOSITORY_SIGNATURE_UNKNOWN: tuple[str, ...] = ("\x00repository-signature-unknown",)

_REPOSITORY_GENERATION_LOCK = threading.Lock()
# root text -> (last observed private signature, generation last reported for it)
_REPOSITORY_GENERATIONS: dict[str, tuple[tuple[str, ...], int]] = {}


def private_repository_signature(
    root: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[str, ...]:
    """Return a path-free identity of a repository's checked-out HEAD.

    The signature is ``(symbolic HEAD ref, HEAD commit OID)``.  Two branches that point at
    the SAME commit share an OID but differ in their symbolic ref, so switching between them
    over an identical working tree still changes the signature -- and therefore advances the
    typed generation below -- WITHOUT ever naming a ``.git`` path.  A detached HEAD has no
    symbolic ref, so its ref component is empty; switching from detached to a branch at the
    same commit is still a change.  Nothing here reads or returns a control-file path, so the
    value is safe to compare inside a public consumer's cache identity while ``.git`` itself
    stays excluded everywhere.

    An unreadable HEAD (no repository, no commit yet, or a Git error) returns the typed
    unknown sentinel rather than a fabricated empty signature, so "could not read" stays a
    distinct answer from "detached at no branch".
    """

    run = runner or (lambda args: git(args, cwd=str(root), timeout=1.0))
    head = run(["rev-parse", "HEAD"])
    if head.returncode != 0:
        return REPOSITORY_SIGNATURE_UNKNOWN
    oid = head.stdout.strip()
    if not oid:
        return REPOSITORY_SIGNATURE_UNKNOWN
    symbolic = run(["symbolic-ref", "--quiet", "--short", "HEAD"])
    symbolic_head = symbolic.stdout.strip() if symbolic.returncode == 0 else ""
    return (symbolic_head, oid)


def repository_generation(
    root: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Advance and return a per-repository generation that ticks on each HEAD identity change.

    This is the typed repository generation a consumer compares to decide whether a cached,
    branch-scoped view is still current.  It advances by one every time the private signature
    changes -- including an identical-tree branch switch, which no working-tree filesystem event
    reports -- and stays put while the signature is unchanged.  It is keyed by resolved root, so
    one tenant's repository can never advance another's counter.

    An unreadable HEAD is inconclusive, not a change: it holds the last known generation and does
    NOT overwrite a previously known signature, so a transient Git failure in one repository can
    neither manufacture churn nor leak into a co-tenant repository's generation.
    """

    root_text = str(Path(root).expanduser().resolve(strict=False))
    signature = private_repository_signature(root, runner=runner)
    with _REPOSITORY_GENERATION_LOCK:
        previous = _REPOSITORY_GENERATIONS.get(root_text)
        if signature == REPOSITORY_SIGNATURE_UNKNOWN:
            if previous is None:
                _REPOSITORY_GENERATIONS[root_text] = (signature, 0)
                return 0
            return previous[1]
        if previous is None:
            generation = 1
        elif previous[0] == signature:
            generation = previous[1]
        else:
            generation = previous[1] + 1
        _REPOSITORY_GENERATIONS[root_text] = (signature, generation)
        return generation


# Finder lists can contain many worktrees.  Keep their small branch/status payload behind one
# bounded, process-local owner: browser cache only avoids repeat HTTP calls and cannot save a cold
# `/api/fs/list` or `/api/fs/info` request.  The control-file signature catches direct callers;
# the native watcher additionally calls ``invalidate_repo_info_paths`` through metadata's existing
# Git invalidation parent, so a watched edit never waits for the signature/TTL backstop.
REPO_INFO_CACHE_SECONDS = 15.0
REPO_INFO_CACHE_JITTER_RATIO = 0.5
REPO_INFO_CACHE_MAX_ENTRIES = 256
# A process cannot reliably spawn and execute Git below this budget.  Passing a smaller positive
# timeout to subprocess turns an already-expired Finder request into TimeoutExpired noise.
REPO_INFO_MINIMUM_COMMAND_TIMEOUT_SECONDS = 0.01
_REPO_INFO_CACHE_LOCK = threading.Lock()
_REPO_INFO_CACHE: dict[tuple[str, bool], tuple[tuple[Any, ...], float, dict[str, Any]]] = {}


def _repo_info_cache_ttl_seconds(root_text: str) -> float:
    """Spread the fallback expiry without weakening signature or watcher invalidation."""
    digest = hashlib.sha256(root_text.encode("utf-8", errors="replace")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
    scale = 1.0 - REPO_INFO_CACHE_JITTER_RATIO + 2.0 * REPO_INFO_CACHE_JITTER_RATIO * fraction
    return REPO_INFO_CACHE_SECONDS * scale


def git_control_files_signature(root_text: str) -> tuple[Any, ...]:
    """Return the Git files whose changes can alter Finder branch/status badges."""
    marker = Path(root_text) / ".git"
    git_dir = marker
    if marker.is_file():
        try:
            first_line = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if first_line.lower().startswith("gitdir:"):
            git_dir = Path(first_line.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = marker.parent / git_dir
    common_dir = git_dir
    try:
        common_text = (git_dir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        common_text = ""
    if common_text:
        common_dir = Path(common_text)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
    # Do not recursively traverse every loose ref here. Finder calls this for every repo in a
    # directory, and a large worktree can hold thousands of refs: the validation itself then costs
    # seconds before the bounded Git budget begins. Native filesystem changes already invalidate
    # this cache through ``invalidate_git_metadata_paths``; these inexpensive directory mtimes are
    # only the direct-caller backstop between watcher batches.
    control_paths = [
        git_dir / "HEAD",
        git_dir / "index",
        common_dir / "packed-refs",
        common_dir / "config",
        common_dir / "refs",
    ]
    rows: list[tuple[str, int, int]] = []
    for path in control_paths:
        try:
            stat = path.stat()
            rows.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            rows.append((str(path), 0, 0))
    return tuple(rows)


def invalidate_repo_info_paths(paths_to_invalidate: list[Path] | tuple[Path, ...]) -> set[str]:
    """Drop Finder Git rows intersecting a native watcher batch and return their roots."""
    changed_paths = [Path(path).expanduser().resolve(strict=False) for path in paths_to_invalidate]
    if not changed_paths:
        return set()

    def intersects(root: Path, changed: Path) -> bool:
        try:
            return root == changed or root.is_relative_to(changed) or changed.is_relative_to(root)
        except ValueError:
            return False

    with _REPO_INFO_CACHE_LOCK:
        roots = {key[0] for key in _REPO_INFO_CACHE}
        invalidated = {root for root in roots if any(intersects(Path(root), changed) for changed in changed_paths)}
        for key in [key for key in _REPO_INFO_CACHE if key[0] in invalidated]:
            _REPO_INFO_CACHE.pop(key, None)
    return invalidated


def git_repo_info(repo: Path, include_status: bool = True, timeout: float | None = None) -> dict[str, Any]:
    """Return repo badges within the caller's whole-operation timeout, if supplied."""
    root = str(repo.expanduser().resolve(strict=False))
    cache_key = (root, bool(include_status))
    now = time.monotonic()
    deadline = None if timeout is None else now + max(0.0, float(timeout))
    # Signature traversal is part of the cold-path work.  Starting the deadline first keeps a
    # large refs directory from silently extending the Finder-wide budget before Git is invoked.
    signature = git_control_files_signature(root)
    with _REPO_INFO_CACHE_LOCK:
        cached = _REPO_INFO_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature and now - cached[1] <= _repo_info_cache_ttl_seconds(root):
            return copy.deepcopy(cached[2])
    timed_out = False

    def run(args: list[str], default_timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal timed_out
        command_timeout = default_timeout
        if deadline is not None:
            command_timeout = deadline - time.monotonic()
            if command_timeout < REPO_INFO_MINIMUM_COMMAND_TIMEOUT_SECONDS:
                timed_out = True
                return subprocess.CompletedProcess(args, 124, "", "Finder Git-info budget expired")
        try:
            result = _git_at_path(args, repo, command_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            return subprocess.CompletedProcess(args, 124, "", "Finder Git-info command timed out")
        timed_out = timed_out or result.returncode == 124 or (deadline is not None and time.monotonic() >= deadline)
        return result

    branch = git_branch_name(repo, runner=lambda args: run(args, 1.0))
    upstream = run(["rev-parse", "--abbrev-ref", "@{upstream}"], 1.0)
    ahead = 0
    behind = 0
    if upstream.returncode == 0:
        counts = run(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], 2.0)
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
        status = run(["status", "--porcelain=v1"], 2.0)
        dirty_count = len(status.stdout.splitlines()) if status.returncode == 0 else None
    value = {
        "root": root,
        "name": repo.name,
        "branch": branch,
        "dirty_count": dirty_count,
        "upstream": upstream.stdout.strip() if upstream.returncode == 0 else "",
        "ahead": ahead,
        "behind": behind,
    }
    if not timed_out:
        with _REPO_INFO_CACHE_LOCK:
            _REPO_INFO_CACHE[cache_key] = (signature, now, copy.deepcopy(value))
            if len(_REPO_INFO_CACHE) > REPO_INFO_CACHE_MAX_ENTRIES:
                _REPO_INFO_CACHE.pop(next(iter(_REPO_INFO_CACHE)))
    return value


def git_tracks_path(path: Path) -> bool:
    """True when `path` is a file tracked by git (committed or staged)."""
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


def pinned_file_git_metadata(
    handle: paths.SafePathHandle,
    *,
    include_repo_info: bool = False,
    history_limit: int = 60,
    operation: str = "",
) -> tuple[str, bool, list[dict[str, Any]], str, dict[str, Any] | None]:
    is_directory = stat.S_ISDIR(handle.stat_result.st_mode)
    repo = _pinned_repo_root(handle, operation=operation)
    if repo is None:
        return "", False, [], "", None
    try:
        rel_path = handle.resolved.relative_to(repo).as_posix()
    except ValueError:
        return "", False, [], "", None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=directory_flags, operation=operation) as repo_handle:
        tracked = False if is_directory else _git_with_pinned_repo(
            repo_handle,
            ["ls-files", "--error-unmatch", "--", rel_path],
            timeout=1.5,
        ).returncode == 0
        history: list[dict[str, Any]] = []
        if tracked:
            result = _git_with_pinned_repo(
                repo_handle,
                [
                    "log",
                    "--follow",
                    f"--max-count={max(1, min(int(history_limit), 100))}",
                    "--format=%H%x1f%h%x1f%s%x1f%ct%x1f%an",
                    "--",
                    rel_path,
                ],
                timeout=3.0,
            )
            if result.returncode == 0:
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
        repo_info = None
        if include_repo_info:
            repo_info = git_repo_info(repo_handle.descriptor_path(), include_status=True)
            repo_info["root"] = str(repo)
            repo_info["name"] = repo.name
    return str(repo), tracked, history, rel_path, repo_info


def _git_mv_if_tracked(src: Path, dst: Path) -> bool:
    """Move a git-tracked file with `git mv`; callers fall back to plain rename on False."""
    repo_root = git_root_for_path(src)
    if not repo_root:
        return False
    repo = Path(repo_root)
    try:
        rel_src = src.relative_to(repo).as_posix()
        rel_dst = dst.relative_to(repo).as_posix()
    except ValueError:
        return False
    tracked = git(["ls-files", "--error-unmatch", "--", rel_src], cwd=str(repo), timeout=2.0)
    if tracked.returncode != 0:
        return False
    return git(["mv", "--", rel_src, rel_dst], cwd=str(repo), timeout=5.0).returncode == 0


def _git_blob_text(repo: Path, ref: str, rel_path: str, label: str) -> tuple[str, str]:
    result = git_bytes(["show", f"{ref}:{rel_path}"], cwd=str(repo), timeout=5.0)
    if result.returncode != 0:
        return "", ""
    if len(result.stdout) > paths.MAX_READ_BYTES:
        raise paths.FilesystemError(
            f"{label} too large (max {paths.MAX_READ_BYTES})",
            status=413,
            message_key="fs.error.gitBlobTooLarge",
            message_params={"label": label, "max": paths.MAX_READ_BYTES},
        )
    if paths._looks_binary(result.stdout):
        return "", f"{label} file appears to be binary"
    return result.stdout.decode("utf-8", errors="replace"), ""


def normal_ref(value: str | None, default: str) -> str:
    ref = str(value or "").strip()
    return ref or default


def diff_refs(raw_from_ref: str | None, raw_to_ref: str | None) -> tuple[str, str]:
    return normal_ref(raw_from_ref, "HEAD"), normal_ref(raw_to_ref, "current")


def refs_requested(from_ref: str | None, to_ref: str | None) -> bool:
    return bool((from_ref or "").strip() or (to_ref or "").strip())


def _diff_ref_resolution_error(error: Exception) -> bool:
    return isinstance(error, paths.FilesystemError) and error.message_key in {
        "common.unknownFromRef",
        "common.unknownToRef",
        "fs.error.refOrderCurrent",
        "fs.error.refOrder",
    }


def git_ref_exists(repo: Path, ref: str) -> bool:
    result = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(repo), timeout=3.0)
    return result.returncode == 0


def _ensure_ref_order(repo: Path, from_ref: str, to_ref: str) -> None:
    if to_ref == "current":
        if from_ref == "current":
            raise paths.FilesystemError(
                "FROM ref must be older than TO ref (current is the working tree)",
                message_key="fs.error.refOrderCurrent",
            )
        if not git_ref_exists(repo, from_ref):
            raise paths.FilesystemError(
                f"unknown FROM ref: {from_ref}",
                message_key="common.unknownFromRef",
                message_params={"ref": from_ref},
            )
        return
    if from_ref == "current":
        raise paths.FilesystemError(
            "FROM ref must be older than TO ref (current is the working tree)",
            message_key="fs.error.refOrderCurrent",
        )
    if not git_ref_exists(repo, from_ref):
        raise paths.FilesystemError(
            f"unknown FROM ref: {from_ref}",
            message_key="common.unknownFromRef",
            message_params={"ref": from_ref},
        )
    if not git_ref_exists(repo, to_ref):
        raise paths.FilesystemError(
            f"unknown TO ref: {to_ref}",
            message_key="common.unknownToRef",
            message_params={"ref": to_ref},
        )
    order = git(["merge-base", "--is-ancestor", from_ref, to_ref], cwd=str(repo), timeout=5.0)
    if order.returncode != 0:
        raise paths.FilesystemError(
            f"FROM ref must be older than TO ref ({from_ref} is not an ancestor of {to_ref})",
            message_key="fs.error.refOrder",
            message_params={"fromRef": from_ref, "toRef": to_ref},
        )


def _pinned_blob_text(
    repo: paths.SafePathHandle,
    ref: str,
    rel_path: str,
    label: str,
) -> tuple[str, str]:
    result = _git_with_pinned_repo(repo, ["show", f"{ref}:{rel_path}"], timeout=5.0, binary=True)
    if result.returncode != 0:
        return "", ""
    raw = result.stdout or b""
    if len(raw) > paths.MAX_READ_BYTES:
        raise paths.FilesystemError(
            f"{label} too large (max {paths.MAX_READ_BYTES})",
            status=413,
            message_key="fs.error.gitBlobTooLarge",
            message_params={"label": label, "max": paths.MAX_READ_BYTES},
        )
    if paths._looks_binary(raw):
        return "", f"{label} file appears to be binary"
    return raw.decode("utf-8", errors="replace"), ""


def _pinned_working_text(handle: paths.SafePathHandle | None) -> tuple[str, str]:
    if handle is None:
        return "", ""
    if handle.stat_result.st_size > paths.MAX_READ_BYTES:
        raise paths.FilesystemError.file_too_large(handle.stat_result.st_size, paths.MAX_READ_BYTES)
    with os.fdopen(os.dup(handle.descriptor), "rb") as stream:
        raw = stream.read(paths.MAX_READ_BYTES + 1)
    if paths._looks_binary(raw):
        return "", "working file appears to be binary"
    return raw.decode("utf-8", errors="replace"), ""


def _pinned_ref_exists(repo: paths.SafePathHandle, ref: str) -> bool:
    return _git_with_pinned_repo(
        repo,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        timeout=3.0,
    ).returncode == 0


def _ensure_pinned_ref_order(repo: paths.SafePathHandle, from_ref: str, to_ref: str) -> None:
    if to_ref == "current":
        if from_ref == "current" or not _pinned_ref_exists(repo, from_ref):
            key = "fs.error.refOrderCurrent" if from_ref == "current" else "common.unknownFromRef"
            raise paths.FilesystemError("invalid FROM ref", message_key=key, message_params={"ref": from_ref})
        return
    if from_ref == "current":
        raise paths.FilesystemError("current cannot precede a commit", message_key="fs.error.refOrderCurrent")
    for ref, key in ((from_ref, "common.unknownFromRef"), (to_ref, "common.unknownToRef")):
        if not _pinned_ref_exists(repo, ref):
            raise paths.FilesystemError(f"unknown ref: {ref}", message_key=key, message_params={"ref": ref})
    order = _git_with_pinned_repo(repo, ["merge-base", "--is-ancestor", from_ref, to_ref], timeout=5.0)
    if order.returncode != 0:
        raise paths.FilesystemError(
            "FROM ref must precede TO ref",
            message_key="fs.error.refOrder",
            message_params={"fromRef": from_ref, "toRef": to_ref},
        )


def _unified_file_diff(original: str, working: str, rel_path: str) -> str:
    lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        working.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    )
    body = "".join(lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}" if body else ""


def _diff_file_from_safe_path(
    path: Path,
    working_handle: paths.SafePathHandle | None,
    repo_source_handle: paths.SafePathHandle | paths.SafeParentHandle | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    operation: str = "diff_file",
) -> dict[str, Any]:
    repo_source = working_handle if working_handle is not None else repo_source_handle
    repo = _pinned_repo_root(repo_source, operation=operation) if repo_source is not None else None
    if repo is None and repo_source is None:
        repo_root = git_root_for_path(path)
        repo = Path(repo_root) if repo_root else None
    if repo is None:
        raise paths.FilesystemError(
            f"not in a git repo: {path}",
            message_key="fs.error.notGitRepo",
            message_params={"path": str(path)},
        )
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise paths.FilesystemError.outside_repo(path) from exc
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=directory_flags, operation=operation) as repo_handle:
        tracked = _git_with_pinned_repo(repo_handle, ["ls-files", "--error-unmatch", "--", rel_path], timeout=3.0)
        diff_from, diff_to = diff_refs(from_ref, to_ref)
        if not (diff_to == "current" and tracked.returncode != 0):
            try:
                _ensure_pinned_ref_order(repo_handle, diff_from, diff_to)
            except paths.FilesystemError as error:
                if not (refs_requested(from_ref, to_ref) and _diff_ref_resolution_error(error)):
                    raise
                diff_from, diff_to = diff_refs(None, None)
                _ensure_pinned_ref_order(repo_handle, diff_from, diff_to)
        original = ""
        original_error = ""
        working = ""
        working_error = ""
        if diff_to == "current":
            untracked = tracked.returncode != 0
            if not untracked:
                original, original_error = _pinned_blob_text(repo_handle, diff_from, rel_path, "original")
            working, working_error = _pinned_working_text(working_handle)
            if original_error or working_error:
                diff = f"Binary files a/{rel_path} and b/{rel_path} differ\n" if original != working else ""
            else:
                diff = _unified_file_diff(original, working, rel_path)
        else:
            untracked = False
            result = _git_with_pinned_repo(
                repo_handle,
                ["diff", diff_from, diff_to, "--", rel_path],
                timeout=5.0,
            )
            if result.returncode not in {0, 1}:
                raise paths.FilesystemError(
                    "git diff failed",
                    status=500,
                    message_key="fs.error.gitDiffFailed",
                    diagnostic=cmd_error(result, "git diff failed"),
                )
            diff = result.stdout or ""
            original, original_error = _pinned_blob_text(repo_handle, diff_from, rel_path, "original")
            working, working_error = _pinned_blob_text(repo_handle, diff_to, rel_path, "working")
    if len(diff.encode("utf-8", errors="replace")) > paths.MAX_READ_BYTES:
        raise paths.FilesystemError(
            f"diff too large (max {paths.MAX_READ_BYTES})",
            status=413,
            message_key="fs.error.diffTooLarge",
            message_params={"max": paths.MAX_READ_BYTES},
        )
    return {
        "path": str(path),
        "repo": str(repo),
        "relative_path": rel_path,
        "diff": diff,
        "original": original,
        "original_error": original_error,
        "working": working,
        "working_error": working_error,
        "working_missing": working_handle is None,
        "from_ref": diff_from,
        "to_ref": diff_to,
        "untracked": untracked,
    }


def diff_file(raw_path: str, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
    try:
        with paths.safe_path(raw_path, operation="diff_file") as handle:
            return _diff_file_from_safe_path(
                handle.resolved,
                handle,
                from_ref=from_ref,
                to_ref=to_ref,
                operation="diff_file",
            )
    except paths.FilesystemError as error:
        if error.status != 404:
            raise
        with paths.safe_parent(raw_path, operation="diff_file") as parent:
            return _diff_file_from_safe_path(
                parent.resolved_target,
                None,
                repo_source_handle=parent,
                from_ref=from_ref,
                to_ref=to_ref,
                operation="diff_file",
            )


# Inline git blame for the editor. PR number is extracted from the commit summary the same
# way the metadata code does (`(#1234)`). Cached per (path, HEAD sha, file mtime, ref) because blame is
# expensive and only changes when the file or HEAD moves.
_BLAME_PR_RE = re.compile(r"\(#(\d+)\)")
_BLAME_SHA_RE = re.compile(r"[0-9a-f]{40}")
_blame_cache: dict[tuple[str, str, int, str], dict[str, Any]] = {}


def _parse_blame_porcelain(text: str) -> dict[str, dict[str, Any]]:
    """Parse `git blame --line-porcelain` into per-line metadata."""
    lines: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    cur_sha = ""
    final_line: int | None = None
    for raw in text.split("\n"):
        if not raw:
            continue
        if raw[0] == "\t":
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


def _blame_file_from_safe_path(
    path: Path,
    file_handle: paths.SafePathHandle | None,
    repo_source_handle: paths.SafePathHandle | paths.SafeParentHandle | None = None,
    ref: str | None = None,
    operation: str = "blame_file",
) -> dict[str, Any]:
    repo_source = file_handle if file_handle is not None else repo_source_handle
    repo = _pinned_repo_root(repo_source, operation=operation) if repo_source is not None else None
    if repo is None and repo_source is None:
        repo_root = git_root_for_path(path)
        repo = Path(repo_root) if repo_root else None
    if repo is None:
        return {"path": str(path), "repo": "", "relative_path": "", "in_repo": False, "lines": {}}
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise paths.FilesystemError.outside_repo(path) from exc
    if file_handle is None:
        return {"path": str(path), "repo": str(repo), "relative_path": rel_path, "in_repo": True, "lines": {}}
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=directory_flags, operation=operation) as repo_handle:
        head = _git_with_pinned_repo(repo_handle, ["rev-parse", "HEAD"], timeout=1.0)
        head_sha = (head.stdout or "").strip() if head.returncode == 0 else ""
        mtime_ns = file_handle.stat_result.st_mtime_ns
        use_ref = ref if (ref and ref not in {"current", "working", "HEAD", ""}) else ""
        cache_key = (str(path), head_sha, mtime_ns, use_ref)
        cached = _blame_cache.get(cache_key)
        if cached is not None:
            return cached
        args = ["blame", "--line-porcelain", "--contents", str(file_handle.descriptor_path())]
        if use_ref:
            args.append(use_ref)
        args += ["--", rel_path]
        result = _git_with_pinned_repo(
            repo_handle,
            args,
            timeout=3.0,
            pass_fds=(file_handle.descriptor,),
        )
    if result.returncode != 0:
        return {
            "path": str(path),
            "repo": str(repo),
            "relative_path": rel_path,
            "in_repo": True,
            "lines": {},
            "error": (result.stderr or "not committed yet").strip(),
        }
    payload = {
        "path": str(path),
        "repo": str(repo),
        "relative_path": rel_path,
        "head": head_sha,
        "in_repo": True,
        "lines": _parse_blame_porcelain(result.stdout or ""),
    }
    if len(_blame_cache) > 64:
        _blame_cache.clear()
    _blame_cache[cache_key] = payload
    return payload


def blame_file(raw_path: str, ref: str | None = None) -> dict[str, Any]:
    try:
        with paths.safe_path(raw_path, operation="blame_file") as handle:
            return _blame_file_from_safe_path(handle.resolved, handle, ref=ref, operation="blame_file")
    except paths.FilesystemError as error:
        if error.status != 404:
            raise
        with paths.safe_parent(raw_path, operation="blame_file") as parent:
            return _blame_file_from_safe_path(
                parent.resolved_target,
                None,
                repo_source_handle=parent,
                ref=ref,
                operation="blame_file",
            )


def git_root_for_path(path: Path) -> str:
    cwd = path if path.is_dir() else path.parent
    result = git(["rev-parse", "--show-toplevel"], cwd=str(cwd), timeout=1.0)
    if result.returncode != 0:
        return ""
    root = result.stdout.strip()
    return root if root.startswith("/") else ""

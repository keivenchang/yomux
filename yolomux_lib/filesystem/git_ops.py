"""Git-backed filesystem operations."""

from __future__ import annotations

import base64
import contextlib
import copy
import difflib
import fcntl
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Callable
from urllib.parse import urlsplit

from ..common import git
from ..common import git_bytes
from ..tmux.tmux_utils import cmd_error
from . import paths

_COMMON_GIT = git


@dataclass(frozen=True)
class PinnedGitResult:
    args: list[str]
    returncode: int
    stdout: str | bytes
    stderr: str | bytes
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    killed_for_cap: bool = False


def _git_with_pinned_repo_process(
    repo: paths.SafePathHandle,
    args: list[str],
    *,
    timeout: float,
    binary: bool = False,
    pass_fds: tuple[int, ...] = (),
    max_output_bytes: int | None = None,
    git_dir_handle: paths.SafePathHandle | None = None,
    git_common_dir_handle: paths.SafePathHandle | None = None,
    git_directory: str | None = None,
    git_common_directory: str | None = None,
    git_object_directory: str | None = None,
    git_object_descriptors: tuple[int, ...] = (),
    shallow_file_path: str | None = None,
) -> subprocess.CompletedProcess[Any] | PinnedGitResult:
    control_descriptors = tuple(
        handle.descriptor
        for handle in (git_dir_handle, git_common_dir_handle)
        if handle is not None
    )
    descriptors = tuple(
        dict.fromkeys(
            (
                repo.descriptor,
                *control_descriptors,
                *git_object_descriptors,
                *pass_fds,
            )
        )
    )
    # `/dev/fd/<n>` on Darwin is a devfs character-special node: it duplicates a REGULAR file
    # descriptor for streaming (`cat < /dev/fd/3`), but a DIRECTORY descriptor exposed there
    # cannot be chdir'd, opendir'd, or used as a path prefix at all (`Not a directory` /
    # `No such file or directory` for every nested lookup) -- unlike Linux's `/proc/self/fd/<n>`,
    # which is a real symlink into the mount namespace and supports both. There is no path string
    # that substitutes for "the directory already open at this fd" on Darwin; the only correct
    # primitive is `fchdir(fd)`, which resolves the same identity the descriptor pin already
    # guarantees (no re-open, no re-resolution, so this does not reopen the TOCTOU window that
    # `SafePathHandle.descriptor_path()` deliberately closed -- see its docstring on why a
    # `F_GETPATH`-resolved name was rejected there). `preexec_fn` runs once, post-fork, pre-exec,
    # doing exactly one syscall, which is the documented safe-minimal use of that hook.
    use_fchdir_cwd = sys.platform == "darwin"
    repo_cwd_args = [] if use_fchdir_cwd else ["-C", str(repo.descriptor_path())]
    popen_extra_kwargs: dict[str, Any] = {"preexec_fn": lambda: os.fchdir(repo.descriptor)} if use_fchdir_cwd else {}
    args_with_repo = ["git", *repo_cwd_args, *args]
    process_env = None
    if git_dir_handle is not None:
        args_with_repo = [
            "git",
            *repo_cwd_args,
            "-c",
            "advice.graftFileDeprecated=false",
            *args,
        ]
        process_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        process_env["GIT_DIR"] = git_directory or str(git_dir_handle.descriptor_path())
        process_env["GIT_WORK_TREE"] = "." if use_fchdir_cwd else str(repo.descriptor_path())
        process_env["GIT_COMMON_DIR"] = git_common_directory or str(
            (git_common_dir_handle or git_dir_handle).descriptor_path()
        )
        process_env["GIT_NO_REPLACE_OBJECTS"] = "1"
        process_env["GIT_NO_LAZY_FETCH"] = "1"
        process_env["GIT_GRAFT_FILE"] = os.devnull
        process_env["GIT_CONFIG_NOSYSTEM"] = "1"
        process_env["GIT_CONFIG_GLOBAL"] = os.devnull
        process_env["GIT_ATTR_NOSYSTEM"] = "1"
        process_env["LC_ALL"] = "C"
        process_env["LANG"] = "C"
        if git_object_directory is not None:
            process_env["GIT_OBJECT_DIRECTORY"] = git_object_directory
        if shallow_file_path is not None:
            process_env["GIT_SHALLOW_FILE"] = shallow_file_path
    if use_fchdir_cwd:
        # `/dev/fd/<n>` on Darwin is `dup()` semantics: every reader shares ONE underlying file
        # offset (Linux's `/proc/self/fd/<n>` instead reopens fresh at 0 for each reader). Pack
        # files are exposed to the child via a symlink to `/dev/fd/<n>` and this same descriptor
        # is reused across multiple subprocess invocations within one pinned scope (current-head,
        # then history, then per-commit reads, ...); without rewinding, the first git process to
        # read a pack exhausts the shared offset and every later git process sees EOF immediately
        # -- reported by git as "not a GIT packfile" for a file that is perfectly intact. Rewind
        # every regular-file descriptor before each invocation; directory descriptors (reached via
        # fchdir, not read) reject SEEK_SET and are skipped.
        for descriptor in descriptors:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError:
                continue
    if max_output_bytes is not None:
        output_limit = max(1, int(max_output_bytes))
        stderr_limit = 64 * 1024
        process = subprocess.Popen(
            args_with_repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=descriptors,
            env=process_env,
            **popen_extra_kwargs,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise paths.FilesystemError("bounded Git process has no output pipes", status=500)
        output_chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        output_sizes = {"stdout": 0, "stderr": 0}
        output_limits = {"stdout": output_limit, "stderr": stderr_limit}
        output_truncated = {"stdout": False, "stderr": False}
        deadline = time.monotonic() + timeout
        timed_out = False
        killed_for_cap = False
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 and process.poll() is None and not killed_for_cap:
                    timed_out = True
                    process.kill()
                events = selector.select(timeout=max(0.0, min(remaining, 0.25)) if process.poll() is None else 0.25)
                for key, _mask in events:
                    stream = key.fileobj
                    chunk = os.read(stream.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    label = key.data
                    room = output_limits[label] - output_sizes[label]
                    if room > 0:
                        retained = chunk[:room]
                        output_chunks[label].append(retained)
                        output_sizes[label] += len(retained)
                    if len(chunk) > max(0, room):
                        output_truncated[label] = True
                        if process.poll() is None:
                            killed_for_cap = True
                            process.kill()
                if process.poll() is not None and not events:
                    for registered in list(selector.get_map().values()):
                        stream = registered.fileobj
                        chunk = os.read(stream.fileno(), 64 * 1024)
                        if not chunk:
                            selector.unregister(stream)
                            continue
                        label = registered.data
                        room = output_limits[label] - output_sizes[label]
                        if room > 0:
                            retained = chunk[:room]
                            output_chunks[label].append(retained)
                            output_sizes[label] += len(retained)
                        if len(chunk) > max(0, room):
                            output_truncated[label] = True
            returncode = process.wait()
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        stdout_bytes = b"".join(output_chunks["stdout"])
        stderr_bytes = b"".join(output_chunks["stderr"])
        if timed_out:
            raise subprocess.TimeoutExpired(args_with_repo, timeout, output=stdout_bytes, stderr=stderr_bytes)
        return PinnedGitResult(
            args=args_with_repo,
            returncode=returncode,
            stdout=stdout_bytes if binary else stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes if binary else stderr_bytes.decode("utf-8", errors="replace"),
            stdout_truncated=output_truncated["stdout"],
            stderr_truncated=output_truncated["stderr"],
            killed_for_cap=killed_for_cap,
        )
    return subprocess.run(
        args_with_repo,
        capture_output=True,
        timeout=timeout,
        check=False,
        text=not binary,
        pass_fds=descriptors,
        env=process_env,
        **popen_extra_kwargs,
    )


def _git_with_pinned_repo(
    repo: paths.SafePathHandle,
    args: list[str],
    *,
    timeout: float,
    binary: bool = False,
    pass_fds: tuple[int, ...] = (),
    max_output_bytes: int | None = None,
    git_dir_handle: paths.SafePathHandle | None = None,
    git_common_dir_handle: paths.SafePathHandle | None = None,
    git_directory: str | None = None,
    git_common_directory: str | None = None,
    git_object_directory: str | None = None,
    git_object_descriptors: tuple[int, ...] = (),
    shallow_data: bytes | None = None,
) -> subprocess.CompletedProcess[Any] | PinnedGitResult:
    kwargs = {
        "timeout": timeout,
        "binary": binary,
        "pass_fds": pass_fds,
        "max_output_bytes": max_output_bytes,
        "git_dir_handle": git_dir_handle,
        "git_common_dir_handle": git_common_dir_handle,
        "git_directory": git_directory,
        "git_common_directory": git_common_directory,
        "git_object_directory": git_object_directory,
        "git_object_descriptors": git_object_descriptors,
    }
    if shallow_data is None:
        return _git_with_pinned_repo_process(repo, args, **kwargs)
    with tempfile.NamedTemporaryFile(prefix="yolomux-git-shallow-") as shallow_file:
        shallow_file.write(shallow_data)
        shallow_file.flush()
        return _git_with_pinned_repo_process(
            repo,
            args,
            shallow_file_path=shallow_file.name,
            **kwargs,
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
    # Same Darwin `/dev/fd` directory limitation as `_git_with_pinned_repo_process` above: `-C
    # <fd-path>` cannot chdir on Darwin, so fchdir the fd directly in the child instead.
    use_fchdir_cwd = sys.platform == "darwin"
    return subprocess.run(
        ["git", *([] if use_fchdir_cwd else ["-C", str(cwd)]), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
        text=True,
        pass_fds=(descriptor,),
        **({"preexec_fn": lambda: os.fchdir(descriptor)} if use_fchdir_cwd else {}),
    )


def _read_small_git_control_file(directory_fd: int, relative_path: str) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(relative_path, flags, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 4096:
            return None
        chunks: list[bytes] = []
        retained = 0
        while retained <= 4096:
            chunk = os.read(descriptor, 4097 - retained)
            if not chunk:
                break
            chunks.append(chunk)
            retained += len(chunk)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > 4096:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    record = text.rstrip("\r\n")
    return record if record and "\n" not in record and "\r" not in record else None


def _valid_git_head_record(record: str | None) -> bool:
    if record is None:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", record):
        return True
    if not record.startswith("ref: refs/"):
        return False
    ref = record.removeprefix("ref: ")
    if ref == "refs/" or ref.endswith(("/", ".", ".lock")):
        return False
    if any(not part or part.startswith(".") or part.endswith(".") for part in ref.split("/")):
        return False
    return not any(
        ord(character) < 32
        or ord(character) == 127
        or character in " ~^:?*[\\"
        for character in ref
    ) and ".." not in ref and "@{" not in ref and "//" not in ref


def _git_control_storage_is_valid(directory_fd: int) -> bool:
    try:
        objects = os.stat("objects", dir_fd=directory_fd, follow_symlinks=True)
        refs = os.stat("refs", dir_fd=directory_fd, follow_symlinks=True)
        config = os.stat("config", dir_fd=directory_fd, follow_symlinks=True)
    except OSError:
        return False
    return stat.S_ISDIR(objects.st_mode) and stat.S_ISDIR(refs.st_mode) and stat.S_ISREG(config.st_mode)


def _git_control_directory_is_valid(directory: paths.SafePathHandle, *, operation: str) -> bool:
    if not _valid_git_head_record(_read_small_git_control_file(directory.descriptor, "HEAD")):
        return False
    common_pointer = _read_small_git_control_file(directory.descriptor, "commondir")
    if common_pointer is None:
        return _git_control_storage_is_valid(directory.descriptor)
    common_path = Path(common_pointer)
    if not common_path.is_absolute():
        common_path = directory.resolved / common_path
    try:
        with paths.safe_path(
            str(common_path),
            flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            operation=operation,
        ) as common_directory:
            return _git_control_storage_is_valid(common_directory.descriptor)
    except (paths.FilesystemError, OSError):
        return False


def _valid_git_marker(
    candidate: Path,
    directory_fd: int,
    marker: os.stat_result,
    *,
    operation: str,
) -> bool:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if stat.S_ISDIR(marker.st_mode):
        try:
            with paths.safe_child(
                directory_fd,
                candidate / ".git",
                candidate / ".git",
                flags=directory_flags,
                operation=operation,
                observe_name=False,
            ) as git_directory:
                return _git_control_directory_is_valid(git_directory, operation=operation)
        except (paths.FilesystemError, OSError):
            return False
    if not stat.S_ISREG(marker.st_mode):
        return False
    record = _read_small_git_control_file(directory_fd, ".git")
    if record is None or not record.startswith("gitdir: "):
        return False
    git_dir_text = record.removeprefix("gitdir: ").strip()
    if not git_dir_text or "\x00" in git_dir_text:
        return False
    git_dir_path = Path(git_dir_text)
    if not git_dir_path.is_absolute():
        git_dir_path = candidate / git_dir_path
    try:
        with paths.safe_path(str(git_dir_path), flags=directory_flags, operation=operation) as git_directory:
            return _git_control_directory_is_valid(git_directory, operation=operation)
    except (paths.FilesystemError, OSError):
        return False


def _pinned_repo_root(
    handle: paths.SafePathHandle | paths.SafeParentHandle,
    *,
    deadline: float | None = None,
    operation: str = "",
) -> Path | None:
    candidate = handle.resolved if stat.S_ISDIR(handle.stat_result.st_mode) else handle.resolved.parent
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    while True:
        if deadline is not None:
            _ensure_git_view_deadline(deadline)
        valid_marker = False
        try:
            with paths.safe_path(str(candidate), flags=directory_flags, operation=operation) as directory:
                marker = os.stat(".git", dir_fd=directory.descriptor, follow_symlinks=False)
                valid_marker = _valid_git_marker(candidate, directory.descriptor, marker, operation=operation)
        except (paths.FilesystemError, OSError):
            marker = None
        if deadline is not None:
            _ensure_git_view_deadline(deadline)
        if marker is not None and valid_marker:
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


def git_branch_state(
    repo: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[str, bool]:
    """Return the checked-out branch and whether Git positively reported detached HEAD."""
    run = runner or (lambda args: git(args, cwd=str(repo), timeout=1.0))
    symbolic = run(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if symbolic.returncode == 0:
        return symbolic.stdout.strip(), False
    resolved = run(["rev-parse", "--abbrev-ref", "HEAD"])
    if resolved.returncode != 0:
        return "", False
    name = resolved.stdout.strip()
    return ("", True) if name == "HEAD" else (name, False)


def git_branch_name(
    repo: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Return the checked-out branch, or an empty string when none is available."""
    return git_branch_state(repo, runner=runner)[0]


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
    # Finder listing passes a pinned `/dev/fd/N`/`/proc/self/fd/N` descriptor path here (see
    # `listing.py`'s `inspection_path`) so `_directory_is_repo`'s existence check is fd-safe. On
    # Linux `.resolve()` follows the `/proc/self/fd/N` symlink back to the true directory; Darwin's
    # `/dev/fd/N` is a devfs node, not a symlink, so `root` stayed literally `/dev/fd/N/...` there.
    # `_REPO_INFO_CACHE` is a PROCESS-WIDE cache keyed on `root` -- an unresolved fd-string key is
    # not just wrong for display, it is actively unsafe as a cache key, since the OS recycles low
    # fd numbers quickly: two unrelated repos opened at different times can reuse the same fd
    # number and collide onto the identical cache key, serving one repo's cached branch/status
    # for a completely different repo. `git_control_files_signature` below also opens `root` by
    # plain name (not fd-relative), which silently found nothing under an unresolvable fd-string
    # path either. Resolve once, up front, and use the resolved path everywhere in this function.
    repo = paths._darwin_devfs_live_realpath(repo.expanduser().resolve(strict=False))
    root = str(repo)
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

    branch, detached = git_branch_state(repo, runner=lambda args: run(args, 1.0))
    head = run(["rev-parse", "HEAD"], 1.0)
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
        "detached": detached,
        "head_sha": head.stdout.strip() if head.returncode == 0 else "",
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


GIT_HISTORY_DEFAULT_LIMIT = 50
GIT_HISTORY_MAX_LIMIT = 50
GIT_HISTORY_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
GIT_HISTORY_MAX_PAYLOAD_BYTES = 384 * 1024
GIT_HISTORY_MAX_TEXT_BYTES = 8 * 1024
GIT_HISTORY_CURSOR_MAX_BYTES = 2048
GIT_HISTORY_MAX_CURSOR_OFFSET = 1_000_000
GIT_COMMIT_MAX_MESSAGE_BYTES = 64 * 1024
GIT_COMMIT_MAX_FILES = 1000
GIT_COMMIT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
GIT_COMMIT_MAX_PAYLOAD_BYTES = 384 * 1024
GIT_COMMIT_METADATA_OVERHEAD_BYTES = 64 * 1024
GIT_CONTROL_POINTER_MAX_BYTES = 4096
GIT_CONFIG_MAX_BYTES = 1024 * 1024
GIT_SHALLOW_MAX_BYTES = 4 * 1024 * 1024
GIT_VIEW_BUILD_TIMEOUT_SECONDS = 10.0
GIT_VIEW_MAX_LOOSE_OBJECTS = 16_384
GIT_VIEW_MAX_LOOSE_BYTES = 256 * 1024 * 1024
GIT_VIEW_MAX_PACK_ENTRIES = 4096
GIT_VIEW_MAX_REF_ENTRIES = 100_000
GIT_VIEW_MAX_REF_BYTES = 32 * 1024 * 1024
GIT_VIEW_MAX_REF_DEPTH = 32
_GIT_OBJECT_ID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_GIT_PACK_FILE_RE = re.compile(
    r"(?:pack-[0-9a-f]{40,64}\.(?:pack|idx|rev|bitmap|keep|promisor)|"
    r"multi-pack-index(?:-[0-9a-f]{40,64}\.bitmap)?)"
)
_GIT_INCREMENTAL_MIDX_RE = re.compile(r"multi-pack-index-[0-9a-f]{40,64}\.midx")


@dataclass(frozen=True)
class PinnedGitObjectStore:
    objects_handle: paths.SafePathHandle
    git_directory: str
    git_common_directory: str
    object_directory: str
    descriptors: tuple[int, ...]
    hosted_remote: dict[str, str] | None


def _ensure_git_view_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _history_error(
            "Git repository metadata exceeds the snapshot limit",
            key="fs.error.gitHistoryTooLarge",
            status=413,
        )


@dataclass
class GitViewRetirementBudget:
    deadline: float | None = None

    def begin(self) -> float:
        if self.deadline is None:
            self.deadline = time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS
        return self.deadline

    def check(self) -> None:
        _ensure_git_view_deadline(self.begin())


@dataclass
class GitViewBudget:
    deadline: float
    max_entries: int
    max_bytes: int
    entries: int = 0
    bytes: int = 0

    def check(self) -> None:
        _ensure_git_view_deadline(self.deadline)
        if self.entries > self.max_entries or self.bytes > self.max_bytes:
            raise _history_error(
                "Git repository metadata exceeds the snapshot limit",
                key="fs.error.gitHistoryTooLarge",
                status=413,
            )

    def consume(self, *, size: int = 0) -> None:
        self.entries += 1
        self.bytes += max(0, int(size))
        self.check()


@dataclass(frozen=True)
class PinnedGitHistoryScope:
    path: Path
    repo: Path
    relative_path: str
    scope_handle: paths.SafePathHandle
    repo_handle: paths.SafePathHandle
    git_marker_handle: paths.SafePathHandle
    git_dir_handle: paths.SafePathHandle
    git_common_dir_handle: paths.SafePathHandle
    git_objects_handle: paths.SafePathHandle
    git_directory: str
    git_common_directory: str
    git_object_directory: str
    git_object_descriptors: tuple[int, ...]
    shallow_snapshot: str
    shallow_data: bytes
    hosted_remote: dict[str, str] | None


def _history_error(message: str, *, key: str, status: int = 400, diagnostic: object = "") -> paths.FilesystemError:
    return paths.FilesystemError(message, status=status, message_key=key, diagnostic=diagnostic)


def _ensure_pinned_namespace_unchanged(handle: paths.SafePathHandle) -> None:
    try:
        current = os.stat(handle.resolved, follow_symlinks=True)
    except OSError as error:
        raise _history_error(
            "Git repository path changed after authorization",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if (current.st_dev, current.st_ino) != (handle.stat_result.st_dev, handle.stat_result.st_ino):
        raise _history_error(
            "Git repository path changed after authorization",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _ensure_pinned_regular_file_unchanged(handle: paths.SafePathHandle) -> None:
    try:
        current = os.fstat(handle.descriptor)
    except OSError as error:
        raise _history_error(
            "Git pack metadata changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    original = handle.stat_result
    if (
        not stat.S_ISREG(current.st_mode)
        or (
            current.st_dev,
            current.st_ino,
            current.st_mode,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        != (
            original.st_dev,
            original.st_ino,
            original.st_mode,
            original.st_size,
            original.st_mtime_ns,
            original.st_ctime_ns,
        )
    ):
        raise _history_error(
            "Git pack metadata changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _read_git_control_pointer(handle: paths.SafePathHandle, *, prefix: bytes = b"") -> tuple[str, bytes]:
    try:
        raw = os.pread(handle.descriptor, GIT_CONTROL_POINTER_MAX_BYTES + 1, 0)
    except OSError as error:
        raise _history_error(
            "Git control pointer changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if len(raw) > GIT_CONTROL_POINTER_MAX_BYTES:
        raise _history_error(
            "Git control pointer is too large",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    snapshot = raw
    if prefix:
        if not raw.startswith(prefix):
            raise _history_error(
                "Git control pointer is malformed",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        raw = raw[len(prefix):]
    raw = raw.rstrip(b"\r\n")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _history_error(
            "Git control pointer is malformed",
            key="fs.error.gitRepositoryChanged",
            status=409,
        ) from error
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise _history_error(
            "Git control pointer is malformed",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    return value, snapshot


def _ensure_git_control_pointer_unchanged(
    handle: paths.SafePathHandle,
    expected: bytes,
    *,
    prefix: bytes = b"",
) -> None:
    _value, current = _read_git_control_pointer(handle, prefix=prefix)
    if current != expected:
        raise _history_error(
            "Git control pointer changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _resolved_git_control_path(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def _ensure_git_control_target_unchanged(
    requested: Path,
    handle: paths.SafePathHandle,
) -> None:
    try:
        current = requested.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise _history_error(
            "Git control target changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if current != handle.resolved:
        raise _history_error(
            "Git control target changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _ensure_git_marker_unchanged(
    repo_handle: paths.SafePathHandle,
    marker_handle: paths.SafePathHandle,
) -> None:
    try:
        current = os.stat(".git", dir_fd=repo_handle.descriptor, follow_symlinks=False)
    except OSError as error:
        raise _history_error(
            "Git control path changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if (current.st_dev, current.st_ino) != (
        marker_handle.stat_result.st_dev,
        marker_handle.stat_result.st_ino,
    ):
        raise _history_error(
            "Git control path changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _ensure_pinned_child_unchanged(
    parent_handle: paths.SafePathHandle,
    name: str,
    child_handle: paths.SafePathHandle,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_handle.descriptor, follow_symlinks=False)
    except OSError as error:
        raise _history_error(
            "Git object-store namespace changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        child_handle.stat_result.st_dev,
        child_handle.stat_result.st_ino,
    ):
        raise _history_error(
            "Git object-store namespace changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )


def _ensure_no_git_alternate_objects(objects_handle: paths.SafePathHandle) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | paths.nofollow_flag()
    try:
        info_descriptor = os.open("info", directory_flags, dir_fd=objects_handle.descriptor)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _history_error(
            "Git alternate object configuration is unavailable",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    try:
        try:
            os.stat("alternates", dir_fd=info_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise _history_error(
                "Git alternate object configuration is unavailable",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        raise _history_error(
            "Git alternate object databases are unsupported",
            key="fs.error.gitAlternateObjects",
            status=422,
        )
    finally:
        os.close(info_descriptor)


def _traversable_descriptor_file(handle: paths.SafePathHandle) -> str:
    expected = (handle.stat_result.st_dev, handle.stat_result.st_ino)
    candidate = Path("/dev/fd") / str(handle.descriptor)
    if sys.platform == "darwin":
        # `/dev/fd/<n>` is synthesized by devfs on macOS: stat()-ing the node itself reports
        # devfs's own st_dev, not the underlying filesystem's, so it can never match `expected`
        # even though the fd is legitimate (ino always matches; dev never does). F_GETPATH resolves
        # the fd back to its real filesystem path so the identity check compares apples to apples;
        # the returned candidate is still the fd-relative `/dev/fd/<n>` path, not the resolved one,
        # so callers keep the same rename-proof reference `/proc/self/fd` gives on Linux.
        try:
            raw = fcntl.fcntl(handle.descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            resolved = raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
            current = os.stat(resolved)
        except (OSError, UnicodeDecodeError):
            pass
        else:
            if (current.st_dev, current.st_ino) == expected:
                return str(candidate)
    else:
        for root in (Path("/proc/self/fd"), Path("/dev/fd")):
            candidate = root / str(handle.descriptor)
            try:
                current = os.stat(candidate)
            except OSError:
                continue
            if (current.st_dev, current.st_ino) == expected:
                return str(candidate)
    raise _history_error(
        "Git control-file descriptors cannot be exposed on this platform",
        key="fs.error.operationFailed",
        status=500,
    )


def _read_bounded_git_config(handle: paths.SafePathHandle) -> bytes:
    if not stat.S_ISREG(handle.stat_result.st_mode):
        raise _history_error(
            "Git configuration is not a regular file",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    _ensure_pinned_regular_file_unchanged(handle)
    try:
        data = os.pread(handle.descriptor, GIT_CONFIG_MAX_BYTES + 1, 0)
    except OSError as error:
        raise _history_error(
            "Git configuration changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if len(data) > GIT_CONFIG_MAX_BYTES:
        raise _history_error(
            "Git configuration exceeds the metadata limit",
            key="fs.error.gitHistoryTooLarge",
            status=413,
        )
    _ensure_pinned_regular_file_unchanged(handle)
    return data


def _git_repository_format(config_data: bytes) -> tuple[int, str]:
    section = ""
    repository_format_version = 0
    object_format = "sha1"
    extensions: dict[str, str] = {}
    for raw_line in config_data.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and "]" in line:
            section = line[1:line.index("]")].strip().split(None, 1)[0].lower()
            if section in {"include", "includeif"}:
                raise _history_error(
                    "Git configuration includes are unsupported",
                    key="fs.error.gitRepositoryUnsupported",
                    status=422,
                )
            continue
        if "=" in line:
            key, raw_value = line.split("=", 1)
        else:
            key, raw_value = line, "true"
        normalized_key = key.strip().lower()
        value = raw_value.strip().split(None, 1)[0].strip('"').lower()
        if section == "core" and normalized_key == "repositoryformatversion":
            try:
                repository_format_version = int(value)
            except ValueError as error:
                raise _history_error(
                    "Git repository format is malformed",
                    key="fs.error.gitRepositoryUnsupported",
                    status=422,
                ) from error
            continue
        if section == "extensions":
            extensions[normalized_key] = value
    if repository_format_version not in {0, 1}:
        raise _history_error(
            "Git repository format is unsupported",
            key="fs.error.gitRepositoryUnsupported",
            status=422,
        )
    supported_extensions = {
        "noop",
        "objectformat",
        "partialclone",
        "preciousobjects",
        "relativeworktrees",
        "refstorage",
        "worktreeconfig",
    }
    if repository_format_version == 0 and extensions:
        raise _history_error(
            "Git repository extensions require format version 1",
            key="fs.error.gitRepositoryUnsupported",
            status=422,
        )
    unknown_extensions = set(extensions) - supported_extensions
    if unknown_extensions:
        raise _history_error(
            "Git repository extensions are unsupported",
            key="fs.error.gitRepositoryUnsupported",
            status=422,
            diagnostic=sorted(unknown_extensions),
        )
    object_format = extensions.get("objectformat", "sha1")
    if object_format not in {"sha1", "sha256"}:
        raise _history_error(
            "Git object format is unsupported",
            key="fs.error.gitRepositoryUnsupported",
            status=422,
        )
    if extensions.get("refstorage", "files") != "files":
        raise _history_error(
            "Git ref storage is unsupported",
            key="fs.error.gitRepositoryUnsupported",
            status=422,
        )
    return repository_format_version, object_format


def _git_origin_remote_url(config_data: bytes) -> str:
    in_origin = False
    for raw_line in config_data.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            in_origin = re.fullmatch(r'\[\s*remote\s+"origin"\s*\]', line, flags=re.IGNORECASE) is not None
            continue
        if not in_origin:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        else:
            key, value = line.split(None, 1) if " " in line else (line, "")
        if key.strip().lower() != "url":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"' and "\\" not in value:
            value = value[1:-1]
        return value
    return ""


def _hosted_git_remote(config_data: bytes) -> dict[str, str] | None:
    raw_url = _git_origin_remote_url(config_data)
    if not raw_url or any(character in raw_url for character in ("\x00", "\r", "\n")):
        return None
    host = ""
    port: int | None = None
    repository_path = ""
    if "://" not in raw_url:
        scp_match = re.fullmatch(r"(?:[^@/:]+@)?([A-Za-z0-9.-]+):(.+)", raw_url)
        if scp_match is None:
            return None
        host, repository_path = scp_match.groups()
    else:
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme not in {"https", "ssh", "git"} or parsed.query or parsed.fragment:
            return None
        if parsed.scheme == "https" and (parsed.username or parsed.password):
            return None
        if parsed.scheme != "https":
            port = None
        host = parsed.hostname or ""
        repository_path = parsed.path.lstrip("/")
    normalized_host = host.lower().rstrip(".")
    if re.fullmatch(r"[a-z0-9.-]+", normalized_host) is None:
        return None
    if normalized_host == "github.com" or normalized_host.startswith("github.") or ".github." in normalized_host:
        provider = "github"
    elif normalized_host == "gitlab.com" or normalized_host.startswith("gitlab.") or ".gitlab." in normalized_host:
        provider = "gitlab"
    else:
        return None
    path = repository_path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    segments = path.split("/")
    if len(segments) < 2 or any(re.fullmatch(r"[A-Za-z0-9._~-]+", segment) is None or segment in {".", ".."} for segment in segments):
        return None
    authority = normalized_host if port is None else f"{normalized_host}:{port}"
    return {"provider": provider, "base_url": f"https://{authority}/{'/'.join(segments)}"}


def _snapshot_regular_child(
    parent_handle: paths.SafePathHandle,
    source_path: Path,
    destination: Path,
    *,
    budget: GitViewBudget,
    operation: str,
) -> None:
    try:
        with paths.safe_child(
            parent_handle.descriptor,
            source_path,
            source_path,
            operation=operation,
            observe_name=False,
        ) as source_handle:
            metadata = source_handle.stat_result
            if not stat.S_ISREG(metadata.st_mode):
                raise _history_error(
                    "Git repository metadata is not a regular file",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            budget.consume(size=metadata.st_size)
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o400,
            )
            try:
                offset = 0
                while offset < metadata.st_size:
                    budget.check()
                    chunk = os.pread(
                        source_handle.descriptor,
                        min(64 * 1024, metadata.st_size - offset),
                        offset,
                    )
                    if not chunk:
                        raise _history_error(
                            "Git repository metadata changed during snapshot",
                            key="fs.error.gitRepositoryChanged",
                            status=409,
                        )
                    written = 0
                    while written < len(chunk):
                        written += os.write(destination_descriptor, chunk[written:])
                    offset += len(chunk)
            finally:
                os.close(destination_descriptor)
            current = os.fstat(source_handle.descriptor)
            if (
                current.st_size != metadata.st_size
                or current.st_mtime_ns != metadata.st_mtime_ns
                or current.st_ctime_ns != metadata.st_ctime_ns
            ):
                raise _history_error(
                    "Git repository metadata changed during snapshot",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
    except (paths.FilesystemError, OSError) as error:
        if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
            raise
        raise _history_error(
            "Git repository metadata is unavailable or unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error


def _snapshot_directory_tree(
    source_handle: paths.SafePathHandle,
    source_path: Path,
    destination: Path,
    *,
    budget: GitViewBudget,
    operation: str,
    depth: int = 0,
) -> None:
    if depth > GIT_VIEW_MAX_REF_DEPTH:
        raise _history_error(
            "Git refs exceed the snapshot depth limit",
            key="fs.error.gitHistoryTooLarge",
            status=413,
        )
    try:
        with os.scandir(source_handle.descriptor) as entries:
            for entry in entries:
                child_path = source_path / entry.name
                destination_path = destination / entry.name
                if entry.is_dir(follow_symlinks=False):
                    budget.consume()
                    destination_path.mkdir()
                    with paths.safe_child(
                        source_handle.descriptor,
                        child_path,
                        child_path,
                        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                        operation=operation,
                        observe_name=False,
                    ) as child_handle:
                        _snapshot_directory_tree(
                            child_handle,
                            child_path,
                            destination_path,
                            budget=budget,
                            operation=operation,
                            depth=depth + 1,
                        )
                    continue
                if entry.is_file(follow_symlinks=False):
                    _snapshot_regular_child(
                        source_handle,
                        child_path,
                        destination_path,
                        budget=budget,
                        operation=operation,
                    )
                    continue
                budget.consume()
                raise _history_error(
                    "Git refs contain an unsafe filesystem entry",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
    except (paths.FilesystemError, OSError) as error:
        if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
            raise
        raise _history_error(
            "Git refs changed during snapshot",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error


def _expose_pinned_pack_files(
    stack: contextlib.ExitStack,
    pack_handle: paths.SafePathHandle,
    pack_path: Path,
    destination: Path,
    *,
    budget: GitViewBudget,
    operation: str,
    incremental: bool = False,
) -> list[paths.SafePathHandle]:
    exposed: list[paths.SafePathHandle] = []
    try:
        with os.scandir(pack_handle.descriptor) as entries:
            for entry in entries:
                budget.consume()
                child_path = pack_path / entry.name
                destination_path = destination / entry.name
                if not incremental and entry.name == "multi-pack-index.d":
                    if not entry.is_dir(follow_symlinks=False):
                        raise _history_error(
                            "Git incremental multi-pack index is unsafe",
                            key="fs.error.gitRepositoryChanged",
                            status=409,
                        )
                    destination_path.mkdir()
                    child_handle = stack.enter_context(
                        paths.safe_child(
                            pack_handle.descriptor,
                            child_path,
                            child_path,
                            flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                            operation=operation,
                            observe_name=False,
                        )
                    )
                    exposed.extend(
                        _expose_pinned_pack_files(
                            stack,
                            child_handle,
                            child_path,
                            destination_path,
                            budget=budget,
                            operation=operation,
                            incremental=True,
                        )
                    )
                    continue
                expected = (
                    _GIT_INCREMENTAL_MIDX_RE.fullmatch(entry.name)
                    if incremental
                    else _GIT_PACK_FILE_RE.fullmatch(entry.name)
                )
                if expected is None:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise _history_error(
                        "Git pack metadata is unsafe",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                    )
                child_handle = stack.enter_context(
                    paths.safe_child(
                        pack_handle.descriptor,
                        child_path,
                        child_path,
                        operation=operation,
                        observe_name=False,
                    )
                )
                if not stat.S_ISREG(child_handle.stat_result.st_mode):
                    raise _history_error(
                        "Git pack metadata is not a regular file",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                    )
                exposed.append(child_handle)
                os.symlink(_traversable_descriptor_file(child_handle), destination_path)
    except (paths.FilesystemError, OSError) as error:
        if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
            raise
        raise _history_error(
            "Git pack metadata changed during snapshot",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    return exposed


@contextlib.contextmanager
def _pinned_git_object_store(
    git_dir_handle: paths.SafePathHandle,
    git_common_dir_handle: paths.SafePathHandle,
    *,
    deadline: float,
    operation: str,
    retirement: GitViewRetirementBudget,
):
    objects_path = git_common_dir_handle.resolved / "objects"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with contextlib.ExitStack() as stack:
        try:
            objects_handle = stack.enter_context(
                paths.safe_child(
                    git_common_dir_handle.descriptor,
                    objects_path,
                    objects_path,
                    flags=directory_flags,
                    operation=operation,
                    observe_name=False,
                )
            )
        except (paths.FilesystemError, OSError) as error:
            raise _history_error(
                "Git object directory is unavailable or unsafe",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        _ensure_pinned_child_unchanged(git_common_dir_handle, "objects", objects_handle)
        _ensure_no_git_alternate_objects(objects_handle)
        view_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="yolomux-git-view-")))
        object_directory = view_root / "objects"
        control_directory = view_root / "control"
        object_directory.mkdir()
        control_directory.mkdir()
        exposed_handles: list[paths.SafePathHandle] = []

        repository_format_version = 0
        object_format = "sha1"
        hosted_remote = None
        _ensure_git_view_deadline(deadline)
        try:
            config_stat = os.stat("config", dir_fd=git_common_dir_handle.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            config_stat = None
        except OSError as error:
            raise _history_error(
                "Git configuration is unavailable",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        if config_stat is not None:
            if not stat.S_ISREG(config_stat.st_mode):
                raise _history_error(
                    "Git configuration is not a regular file",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            config_path = git_common_dir_handle.resolved / "config"
            config_handle = stack.enter_context(
                paths.safe_child(
                    git_common_dir_handle.descriptor,
                    config_path,
                    config_path,
                    operation=operation,
                    observe_name=False,
                )
            )
            config_data = _read_bounded_git_config(config_handle)
            repository_format_version, object_format = _git_repository_format(config_data)
            hosted_remote = _hosted_git_remote(config_data)

        loose_budget = GitViewBudget(
            deadline=deadline,
            max_entries=GIT_VIEW_MAX_LOOSE_OBJECTS,
            max_bytes=GIT_VIEW_MAX_LOOSE_BYTES,
        )
        loose_suffix_length = 62 if object_format == "sha256" else 38
        loose_suffix_re = re.compile(rf"[0-9a-f]{{{loose_suffix_length}}}")
        for prefix_index in range(256):
            loose_budget.check()
            name = f"{prefix_index:02x}"
            try:
                child_stat = os.stat(name, dir_fd=objects_handle.descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise _history_error(
                    "Git loose-object directory is unavailable",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                    diagnostic=error,
                ) from error
            if not stat.S_ISDIR(child_stat.st_mode):
                raise _history_error(
                    "Git loose-object directory is unsafe",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            child_path = objects_path / name
            try:
                with paths.safe_child(
                    objects_handle.descriptor,
                    child_path,
                    child_path,
                    flags=directory_flags,
                    operation=operation,
                    observe_name=False,
                ) as child_handle:
                    destination_directory = object_directory / name
                    destination_directory.mkdir()
                    with os.scandir(child_handle.descriptor) as entries:
                        for entry in entries:
                            if loose_suffix_re.fullmatch(entry.name) is None:
                                loose_budget.consume()
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                raise _history_error(
                                    "Git loose object is unsafe",
                                    key="fs.error.gitRepositoryChanged",
                                    status=409,
                                )
                            _snapshot_regular_child(
                                child_handle,
                                child_path / entry.name,
                                destination_directory / entry.name,
                                budget=loose_budget,
                                operation=operation,
                            )
            except (paths.FilesystemError, OSError) as error:
                if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
                    raise
                raise _history_error(
                    "Git object storage changed during history read",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                    diagnostic=error,
                ) from error

        loose_budget.check()
        try:
            pack_stat = os.stat("pack", dir_fd=objects_handle.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pack_stat = None
        except OSError as error:
            raise _history_error(
                "Git pack directory is unavailable",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        if pack_stat is not None:
            if not stat.S_ISDIR(pack_stat.st_mode):
                raise _history_error(
                    "Git pack directory is unsafe",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            pack_path = objects_path / "pack"
            try:
                pack_handle = stack.enter_context(
                    paths.safe_child(
                        objects_handle.descriptor,
                        pack_path,
                        pack_path,
                        flags=directory_flags,
                        operation=operation,
                        observe_name=False,
                    )
                )
                pack_destination = object_directory / "pack"
                pack_destination.mkdir()
                pack_budget = GitViewBudget(
                    deadline=deadline,
                    max_entries=GIT_VIEW_MAX_PACK_ENTRIES,
                    max_bytes=0,
                )
                pack_budget.check()
                exposed_handles.extend(
                    _expose_pinned_pack_files(
                        stack,
                        pack_handle,
                        pack_path,
                        pack_destination,
                        budget=pack_budget,
                        operation=operation,
                    )
                )
                pack_budget.check()
            except (paths.FilesystemError, OSError) as error:
                if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
                    raise
                raise _history_error(
                    "Git pack directory changed during snapshot",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                    diagnostic=error,
                ) from error
        _ensure_git_view_deadline(deadline)
        head_path = git_dir_handle.resolved / "HEAD"
        try:
            head_handle = stack.enter_context(
                paths.safe_child(
                    git_dir_handle.descriptor,
                    head_path,
                    head_path,
                    operation=operation,
                    observe_name=False,
                )
            )
        except (paths.FilesystemError, OSError) as error:
            raise _history_error(
                "Git HEAD is unavailable or unsafe",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        _head_value, head_snapshot = _read_git_control_pointer(head_handle)
        (control_directory / "HEAD").write_bytes(head_snapshot)

        _ensure_git_view_deadline(deadline)
        refs_path = git_common_dir_handle.resolved / "refs"
        try:
            refs_stat = os.stat("refs", dir_fd=git_common_dir_handle.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            refs_stat = None
        except OSError as error:
            raise _history_error(
                "Git refs are unavailable",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        refs_destination = control_directory / "refs"
        refs_destination.mkdir()
        ref_budget = GitViewBudget(
            deadline=deadline,
            max_entries=GIT_VIEW_MAX_REF_ENTRIES,
            max_bytes=GIT_VIEW_MAX_REF_BYTES,
        )
        ref_budget.check()
        if refs_stat is None:
            pass
        elif stat.S_ISDIR(refs_stat.st_mode):
            try:
                with paths.safe_child(
                    git_common_dir_handle.descriptor,
                    refs_path,
                    refs_path,
                    flags=directory_flags,
                    operation=operation,
                    observe_name=False,
                ) as refs_handle:
                    _snapshot_directory_tree(
                        refs_handle,
                        refs_path,
                        refs_destination,
                        budget=ref_budget,
                        operation=operation,
                    )
            except (paths.FilesystemError, OSError) as error:
                if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
                    raise
                raise _history_error(
                    "Git refs changed during snapshot",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                    diagnostic=error,
                ) from error
        else:
            raise _history_error(
                "Git refs are not a directory",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )

        ref_budget.check()
        try:
            packed_refs_stat = os.stat(
                "packed-refs",
                dir_fd=git_common_dir_handle.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            packed_refs_stat = None
        except OSError as error:
            raise _history_error(
                "Git packed refs are unavailable",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=error,
            ) from error
        if packed_refs_stat is not None:
            if not stat.S_ISREG(packed_refs_stat.st_mode):
                raise _history_error(
                    "Git packed refs are not a regular file",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            packed_refs_path = git_common_dir_handle.resolved / "packed-refs"
            _snapshot_regular_child(
                git_common_dir_handle,
                packed_refs_path,
                control_directory / "packed-refs",
                budget=ref_budget,
                operation=operation,
            )

        ref_budget.check()
        config_text = (
            "[core]\n"
            f"\trepositoryformatversion = {repository_format_version}\n"
            "\tbare = false\n"
        )
        if object_format == "sha256":
            config_text += "[extensions]\n\tobjectformat = sha256\n"
        (control_directory / "config").write_text(config_text, encoding="ascii")
        _ensure_git_view_deadline(deadline)

        os.chmod(object_directory, 0o500)
        stack.callback(os.chmod, object_directory, 0o700)
        os.chmod(control_directory, 0o500)
        stack.callback(os.chmod, control_directory, 0o700)
        try:
            yield PinnedGitObjectStore(
                objects_handle=objects_handle,
                git_directory=str(control_directory),
                git_common_directory=str(control_directory),
                object_directory=str(object_directory),
                descriptors=tuple(handle.descriptor for handle in exposed_handles),
                hosted_remote=hosted_remote,
            )
        finally:
            retirement.check()
            for handle in exposed_handles:
                _ensure_pinned_regular_file_unchanged(handle)
                retirement.check()
            _ensure_pinned_child_unchanged(git_common_dir_handle, "objects", objects_handle)
            retirement.check()
            _ensure_no_git_alternate_objects(objects_handle)
            retirement.check()
            _ensure_git_control_pointer_unchanged(head_handle, head_snapshot)
            retirement.check()


def _git_shallow_snapshot(
    git_common_dir_handle: paths.SafePathHandle,
    *,
    deadline: float,
) -> tuple[str, bytes]:
    _ensure_git_view_deadline(deadline)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("shallow", flags, dir_fd=git_common_dir_handle.descriptor)
    except FileNotFoundError:
        _ensure_git_view_deadline(deadline)
        return "absent", b""
    except OSError as error:
        raise _history_error(
            "Git shallow boundary is unavailable",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _history_error(
                "Git shallow boundary is not a regular file",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        chunks = []
        retained = 0
        while retained <= GIT_SHALLOW_MAX_BYTES:
            _ensure_git_view_deadline(deadline)
            chunk = os.read(descriptor, min(64 * 1024, GIT_SHALLOW_MAX_BYTES + 1 - retained))
            if not chunk:
                break
            chunks.append(chunk)
            retained += len(chunk)
    except OSError as error:
        raise _history_error(
            "Git shallow boundary changed during history read",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    finally:
        os.close(descriptor)
    if retained > GIT_SHALLOW_MAX_BYTES:
        raise _history_error(
            "Git shallow boundary exceeds the metadata limit",
            key="fs.error.gitHistoryTooLarge",
            status=413,
        )
    _ensure_git_view_deadline(deadline)
    data = b"".join(chunks)
    return hashlib.sha256(data).hexdigest(), data


@contextlib.contextmanager
def _pinned_git_control(
    repo: Path,
    repo_handle: paths.SafePathHandle,
    *,
    deadline: float,
    operation: str,
    retirement: GitViewRetirementBudget,
):
    marker_path = repo / ".git"
    try:
        marker_stat = os.stat(".git", dir_fd=repo_handle.descriptor, follow_symlinks=False)
    except OSError as error:
        raise _history_error(
            "Git control path is unavailable",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    _ensure_git_view_deadline(deadline)
    if stat.S_ISDIR(marker_stat.st_mode):
        marker_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    elif stat.S_ISREG(marker_stat.st_mode):
        marker_flags = os.O_RDONLY
    else:
        raise _history_error(
            "Git control path is not a regular file or directory",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    with contextlib.ExitStack() as stack:
        marker_pointer_snapshot: bytes | None = None
        common_file_handle: paths.SafePathHandle | None = None
        common_pointer_snapshot: bytes | None = None
        git_dir_requested_path: Path | None = None
        common_dir_requested_path: Path | None = None
        marker_handle = stack.enter_context(
            paths.safe_child(
                repo_handle.descriptor,
                marker_path,
                marker_path,
                flags=marker_flags,
                operation=operation,
                observe_name=False,
            )
        )
        _ensure_git_view_deadline(deadline)
        if stat.S_ISDIR(marker_stat.st_mode):
            git_dir_handle = marker_handle
        else:
            pointer, marker_pointer_snapshot = _read_git_control_pointer(marker_handle, prefix=b"gitdir: ")
            git_dir_path = _resolved_git_control_path(repo, pointer)
            git_dir_requested_path = git_dir_path
            git_dir_handle = stack.enter_context(
                paths.safe_path(
                    str(git_dir_path),
                    flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    operation=operation,
                )
            )
        _ensure_git_view_deadline(deadline)
        try:
            common_stat = os.stat("commondir", dir_fd=git_dir_handle.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            common_stat = None
        if common_stat is None:
            git_common_dir_handle = git_dir_handle
        elif stat.S_ISREG(common_stat.st_mode):
            common_file_path = git_dir_handle.resolved / "commondir"
            common_file_handle = stack.enter_context(
                paths.safe_child(
                    git_dir_handle.descriptor,
                    common_file_path,
                    common_file_path,
                    operation=operation,
                    observe_name=False,
                )
            )
            common_pointer, common_pointer_snapshot = _read_git_control_pointer(common_file_handle)
            common_dir_path = _resolved_git_control_path(git_dir_handle.resolved, common_pointer)
            common_dir_requested_path = common_dir_path
            git_common_dir_handle = stack.enter_context(
                paths.safe_path(
                    str(common_dir_path),
                    flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    operation=operation,
                )
            )
        else:
            raise _history_error(
                "Git common directory pointer is malformed",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        _ensure_git_view_deadline(deadline)
        with _pinned_git_object_store(
            git_dir_handle,
            git_common_dir_handle,
            deadline=deadline,
            operation=operation,
            retirement=retirement,
        ) as object_store:
            shallow_snapshot, shallow_data = _git_shallow_snapshot(
                git_common_dir_handle,
                deadline=deadline,
            )
            _ensure_git_marker_unchanged(repo_handle, marker_handle)
            _ensure_git_view_deadline(deadline)
            try:
                yield (
                    marker_handle,
                    git_dir_handle,
                    git_common_dir_handle,
                    object_store,
                    shallow_snapshot,
                    shallow_data,
                )
            finally:
                retirement_deadline = retirement.begin()
                _ensure_git_marker_unchanged(repo_handle, marker_handle)
                _ensure_git_view_deadline(retirement_deadline)
                _ensure_pinned_namespace_unchanged(marker_handle)
                _ensure_git_view_deadline(retirement_deadline)
                _ensure_pinned_namespace_unchanged(git_dir_handle)
                _ensure_git_view_deadline(retirement_deadline)
                _ensure_pinned_namespace_unchanged(git_common_dir_handle)
                _ensure_git_view_deadline(retirement_deadline)
                if common_file_handle is not None:
                    _ensure_pinned_namespace_unchanged(common_file_handle)
                if _git_shallow_snapshot(
                    git_common_dir_handle,
                    deadline=retirement_deadline,
                ) != (shallow_snapshot, shallow_data):
                    raise _history_error(
                        "Git shallow boundary changed during history read",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                    )
                if git_dir_requested_path is not None:
                    _ensure_git_control_target_unchanged(git_dir_requested_path, git_dir_handle)
                    _ensure_git_view_deadline(retirement_deadline)
                if common_dir_requested_path is not None:
                    _ensure_git_control_target_unchanged(common_dir_requested_path, git_common_dir_handle)
                    _ensure_git_view_deadline(retirement_deadline)
                if marker_pointer_snapshot is not None:
                    _ensure_git_control_pointer_unchanged(
                        marker_handle,
                        marker_pointer_snapshot,
                        prefix=b"gitdir: ",
                    )
                    _ensure_git_view_deadline(retirement_deadline)
                if common_file_handle is not None and common_pointer_snapshot is not None:
                    _ensure_git_control_pointer_unchanged(common_file_handle, common_pointer_snapshot)
                    _ensure_git_view_deadline(retirement_deadline)


def _validate_git_path_text(*values: Path | str) -> None:
    try:
        for value in values:
            str(value).encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise _history_error(
            "Git path is not valid UTF-8",
            key="fs.error.gitPathEncoding",
            status=422,
        ) from error


@contextlib.contextmanager
def _pinned_git_history_scope(raw_path: str, *, operation: str):
    deadline = time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS
    retirement = GitViewRetirementBudget()
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        scope_context = paths.safe_path(raw_path, flags=directory_flags, operation=operation)
        with scope_context as scope_handle:
            _ensure_git_view_deadline(deadline)
            repo = _pinned_repo_root(scope_handle, deadline=deadline, operation=operation)
            _ensure_git_view_deadline(deadline)
            if repo is None:
                raise _history_error("path is not in a Git repository", key="fs.error.notGitRepo")
            _ensure_pinned_namespace_unchanged(scope_handle)
            try:
                relative_path = scope_handle.resolved.relative_to(repo).as_posix()
            except ValueError as error:
                raise paths.FilesystemError.outside_repo(scope_handle.resolved) from error
            _validate_git_path_text(repo, scope_handle.resolved, relative_path)
            _ensure_git_view_deadline(deadline)
            with paths.safe_path(str(repo), flags=directory_flags, operation=operation) as repo_handle:
                _ensure_git_view_deadline(deadline)
                _ensure_pinned_namespace_unchanged(scope_handle)
                _ensure_git_view_deadline(deadline)
                _ensure_pinned_namespace_unchanged(repo_handle)
                _ensure_git_view_deadline(deadline)
                with _pinned_git_control(
                    repo,
                    repo_handle,
                    deadline=deadline,
                    operation=operation,
                    retirement=retirement,
                ) as control:
                    (
                        marker_handle,
                        git_dir_handle,
                        git_common_dir_handle,
                        object_store,
                        shallow_snapshot,
                        shallow_data,
                    ) = control
                    _ensure_git_view_deadline(deadline)
                    try:
                        yield PinnedGitHistoryScope(
                            path=scope_handle.resolved,
                            repo=repo,
                            relative_path="" if relative_path == "." else relative_path,
                            scope_handle=scope_handle,
                            repo_handle=repo_handle,
                            git_marker_handle=marker_handle,
                            git_dir_handle=git_dir_handle,
                            git_common_dir_handle=git_common_dir_handle,
                            git_objects_handle=object_store.objects_handle,
                            git_directory=object_store.git_directory,
                            git_common_directory=object_store.git_common_directory,
                            git_object_directory=object_store.object_directory,
                            git_object_descriptors=object_store.descriptors,
                            shallow_snapshot=shallow_snapshot,
                            shallow_data=shallow_data,
                            hosted_remote=object_store.hosted_remote,
                        )
                    finally:
                        retirement_deadline = retirement.begin()
                        _ensure_pinned_namespace_unchanged(scope_handle)
                        _ensure_git_view_deadline(retirement_deadline)
                        _ensure_pinned_namespace_unchanged(repo_handle)
                        _ensure_git_view_deadline(retirement_deadline)
                        if _pinned_repo_root(
                            scope_handle,
                            deadline=retirement_deadline,
                            operation=operation,
                        ) != repo:
                            raise _history_error(
                                "Git repository boundary changed during history read",
                                key="fs.error.gitRepositoryChanged",
                                status=409,
                            )
                        _ensure_git_view_deadline(retirement_deadline)
    except paths.FilesystemError as error:
        if error.message_key != "fs.error.notDirectory":
            raise
        raise _history_error(
            "Git history requires a directory",
            key="fs.error.gitHistoryDirectoryRequired",
        ) from error


def _bounded_history_limit(raw_limit: int | str | None) -> int:
    if raw_limit is None:
        return GIT_HISTORY_DEFAULT_LIMIT
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, str)):
        raise _history_error(
            "invalid Git history limit",
            key="fs.error.gitHistoryLimit",
            status=422,
        )
    try:
        value = int(raw_limit)
    except ValueError as error:
        raise _history_error(
            "invalid Git history limit",
            key="fs.error.gitHistoryLimit",
            status=422,
        ) from error
    return max(1, min(value, GIT_HISTORY_MAX_LIMIT))


def _git_result_error_text(result: subprocess.CompletedProcess[Any] | PinnedGitResult) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace")
    return str(stderr or "")


def _was_killed_for_output_cap(result: subprocess.CompletedProcess[Any] | PinnedGitResult) -> bool:
    return (
        isinstance(result, PinnedGitResult)
        and result.killed_for_cap
        and result.stdout_truncated
        and not result.stderr_truncated
        and not _git_result_error_text(result).strip()
    )


def _raise_history_git_failure(
    result: subprocess.CompletedProcess[Any] | PinnedGitResult,
    *,
    operation: str,
) -> None:
    detail = _git_result_error_text(result)
    if "permission denied" in detail.lower():
        raise _history_error(
            "Git history permission denied",
            key=f"fs.error.{operation}Permission",
            status=403,
            diagnostic=detail,
        )
    raise _history_error(
        f"{operation} failed",
        key=f"fs.error.{operation}Failed",
        status=500,
        diagnostic=detail,
    )


def _run_bounded_history_git(
    scope: PinnedGitHistoryScope,
    args: list[str],
    *,
    operation: str,
    timeout: float,
    max_output_bytes: int,
    allow_failure: bool = False,
) -> PinnedGitResult:
    _ensure_pinned_child_unchanged(
        scope.git_common_dir_handle,
        "objects",
        scope.git_objects_handle,
    )
    _ensure_no_git_alternate_objects(scope.git_objects_handle)
    try:
        result = _git_with_pinned_repo(
            scope.repo_handle,
            args,
            timeout=timeout,
            binary=True,
            max_output_bytes=max_output_bytes,
            git_dir_handle=scope.git_dir_handle,
            git_common_dir_handle=scope.git_common_dir_handle,
            git_directory=scope.git_directory,
            git_common_directory=scope.git_common_directory,
            git_object_directory=scope.git_object_directory,
            git_object_descriptors=scope.git_object_descriptors,
            shallow_data=scope.shallow_data,
        )
    except subprocess.TimeoutExpired as error:
        raise _history_error(
            f"{operation} timed out",
            key=f"fs.error.{operation}Timeout",
            status=504,
            diagnostic=error,
        ) from error
    finally:
        _ensure_pinned_child_unchanged(
            scope.git_common_dir_handle,
            "objects",
            scope.git_objects_handle,
        )
        _ensure_no_git_alternate_objects(scope.git_objects_handle)
    if result.returncode != 0 and not allow_failure and not _was_killed_for_output_cap(result):
        _raise_history_git_failure(result, operation=operation)
    if not isinstance(result, PinnedGitResult):
        raise _history_error(
            "bounded Git runner returned an unbounded result",
            key="fs.error.operationFailed",
            status=500,
        )
    if result.stderr_truncated:
        _raise_history_git_failure(result, operation=operation)
    return result


def _decode_git_text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _decode_git_path(value: bytes) -> str:
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _history_error(
            "Git path is not valid UTF-8",
            key="fs.error.gitPathEncoding",
            status=422,
        ) from error


def _parse_git_timestamp(value: bytes, *, operation: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise _history_error(
            "malformed Git timestamp",
            key=f"fs.error.{operation}Failed",
            status=500,
        ) from error


def _history_cursor_error() -> paths.FilesystemError:
    return _history_error("invalid Git history cursor", key="fs.error.gitHistoryCursor")


def _encode_history_cursor(scope: PinnedGitHistoryScope, head: str, offset: int) -> str:
    payload = {
        "version": 1,
        "repo": str(scope.repo),
        "repo_dev": scope.repo_handle.stat_result.st_dev,
        "repo_ino": scope.repo_handle.stat_result.st_ino,
        "git_dev": scope.git_dir_handle.stat_result.st_dev,
        "git_ino": scope.git_dir_handle.stat_result.st_ino,
        "common_dev": scope.git_common_dir_handle.stat_result.st_dev,
        "common_ino": scope.git_common_dir_handle.stat_result.st_ino,
        "objects_dev": scope.git_objects_handle.stat_result.st_dev,
        "objects_ino": scope.git_objects_handle.stat_result.st_ino,
        "scope": scope.relative_path,
        "scope_dev": scope.scope_handle.stat_result.st_dev,
        "scope_ino": scope.scope_handle.stat_result.st_ino,
        "shallow": scope.shallow_snapshot,
        "head": head,
        "offset": int(offset),
        "order": "topo",
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_history_cursor(raw_cursor: str) -> dict[str, Any]:
    cursor = str(raw_cursor or "")
    if not cursor or len(cursor) > GIT_HISTORY_CURSOR_MAX_BYTES:
        raise _history_cursor_error()
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise _history_cursor_error() from error
    if not isinstance(payload, dict):
        raise _history_cursor_error()
    return payload


def _cursor_snapshot(scope: PinnedGitHistoryScope, raw_cursor: str) -> tuple[str, int]:
    payload = _decode_history_cursor(raw_cursor)
    if payload.get("shallow") != scope.shallow_snapshot:
        raise _history_error(
            "Git history snapshot is stale",
            key="fs.error.gitHistoryStale",
            status=409,
        )
    expected = {
        "version": 1,
        "repo": str(scope.repo),
        "repo_dev": scope.repo_handle.stat_result.st_dev,
        "repo_ino": scope.repo_handle.stat_result.st_ino,
        "git_dev": scope.git_dir_handle.stat_result.st_dev,
        "git_ino": scope.git_dir_handle.stat_result.st_ino,
        "common_dev": scope.git_common_dir_handle.stat_result.st_dev,
        "common_ino": scope.git_common_dir_handle.stat_result.st_ino,
        "objects_dev": scope.git_objects_handle.stat_result.st_dev,
        "objects_ino": scope.git_objects_handle.stat_result.st_ino,
        "scope": scope.relative_path,
        "scope_dev": scope.scope_handle.stat_result.st_dev,
        "scope_ino": scope.scope_handle.stat_result.st_ino,
        "shallow": scope.shallow_snapshot,
        "order": "topo",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise _history_cursor_error()
    head = payload.get("head")
    offset = payload.get("offset")
    if not isinstance(head, str) or _GIT_OBJECT_ID_RE.fullmatch(head) is None:
        raise _history_cursor_error()
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= GIT_HISTORY_MAX_CURSOR_OFFSET
    ):
        raise _history_cursor_error()
    return head, offset


def _literal_scope_args(relative_path: str) -> list[str]:
    return ["--", f":(literal){relative_path}"] if relative_path else []


def _parse_history_numstat(raw: bytes, *, output_truncated: bool) -> tuple[list[dict[str, Any]], bool, bool]:
    if raw and not output_truncated and not raw.endswith(b"\0"):
        raise _history_error(
            "malformed Git history terminator",
            key="fs.error.gitHistoryFailed",
            status=500,
        )
    tokens = raw.split(b"\0")
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    index = 0
    parse_truncated = output_truncated
    metadata_truncated = False
    while index < len(tokens):
        token = tokens[index]
        if token == b"":
            index += 1
            continue
        if token == b"commit":
            if current is not None:
                commits.append(current)
            if index + 6 >= len(tokens):
                if output_truncated:
                    parse_truncated = True
                    break
                raise _history_error(
                    "malformed Git history metadata",
                    key="fs.error.gitHistoryFailed",
                    status=500,
                )
            sha, short, parents, author, authored_at, subject = tokens[index + 1:index + 7]
            authored_at_value = _parse_git_timestamp(authored_at, operation="gitHistory")
            author_text, author_was_truncated = _bounded_utf8(author, GIT_HISTORY_MAX_TEXT_BYTES)
            subject_text, subject_was_truncated = _bounded_utf8(subject, GIT_HISTORY_MAX_TEXT_BYTES)
            metadata_truncated = metadata_truncated or author_was_truncated or subject_was_truncated
            current = {
                "sha": _decode_git_text(sha),
                "short": _decode_git_text(short),
                "parents": _decode_git_text(parents).split() if parents else [],
                "subject": subject_text,
                "author": author_text,
                "authored_at": authored_at_value,
                "files": 0,
                "added": 0,
                "removed": 0,
                "binary_files": 0,
            }
            index += 7
            continue
        if current is None:
            if output_truncated:
                break
            raise _history_error("malformed Git history output", key="fs.error.gitHistoryFailed", status=500)
        stat_token = token[1:] if token.startswith(b"\n") else token
        fields = stat_token.split(b"\t", 2)
        if len(fields) != 3:
            if output_truncated:
                parse_truncated = True
                break
            raise _history_error("malformed Git history output", key="fs.error.gitHistoryFailed", status=500)
        if fields[2] == b"":
            if index + 2 >= len(tokens):
                if output_truncated:
                    parse_truncated = True
                    break
                raise _history_error(
                    "malformed Git history rename",
                    key="fs.error.gitHistoryFailed",
                    status=500,
                )
            old_token = tokens[index + 1]
            new_token = tokens[index + 2]
            if not old_token or not new_token:
                raise _history_error(
                    "malformed Git history rename",
                    key="fs.error.gitHistoryFailed",
                    status=500,
                )
            index += 3
        else:
            index += 1
        current["files"] += 1
        added_is_binary = fields[0] == b"-"
        removed_is_binary = fields[1] == b"-"
        if added_is_binary != removed_is_binary:
            raise _history_error(
                "malformed Git history counts",
                key="fs.error.gitHistoryFailed",
                status=500,
            )
        if added_is_binary:
            current["binary_files"] += 1
            continue
        try:
            added = int(fields[0])
            removed = int(fields[1])
            if added < 0 or removed < 0:
                raise ValueError("negative Git history count")
            current["added"] += added
            current["removed"] += removed
        except ValueError as error:
            raise _history_error(
                "malformed Git history counts",
                key="fs.error.gitHistoryFailed",
                status=500,
            ) from error
    if current is not None and not parse_truncated:
        commits.append(current)
    return commits, parse_truncated, metadata_truncated


def _current_head(scope: PinnedGitHistoryScope, *, operation: str) -> str:
    result = _run_bounded_history_git(
        scope,
        ["rev-parse", "--verify", "--quiet", "HEAD"],
        operation=operation,
        timeout=3.0,
        max_output_bytes=512,
        allow_failure=True,
    )
    if result.returncode != 0:
        detail = _git_result_error_text(result)
        stdout = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
        if result.returncode == 1 and not stdout and not detail.strip() and not result.stdout_truncated:
            return ""
        _raise_history_git_failure(result, operation=operation)
    return _decode_git_text(result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()).strip()


def _ensure_commit_exists(
    scope: PinnedGitHistoryScope,
    commit_oid: str,
    *,
    operation: str = "gitCommit",
    missing_key: str = "fs.error.gitCommitUnknown",
    missing_status: int = 404,
) -> None:
    result = _run_bounded_history_git(
        scope,
        ["rev-parse", "--verify", "--quiet", f"{commit_oid}^{{commit}}"],
        operation=operation,
        timeout=3.0,
        max_output_bytes=512,
        allow_failure=True,
    )
    if result.returncode == 0:
        resolved = _decode_git_text(
            result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()
        ).strip()
        if resolved == commit_oid:
            return
        raise _history_error("unknown Git commit", key=missing_key, status=missing_status)
    detail = _git_result_error_text(result)
    stdout = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
    if result.returncode == 1 and not stdout and not detail.strip() and not result.stdout_truncated:
        raise _history_error("unknown Git commit", key=missing_key, status=missing_status)
    _raise_history_git_failure(result, operation=operation)


def _ensure_current_head_object(scope: PinnedGitHistoryScope, *, operation: str) -> None:
    result = _run_bounded_history_git(
        scope,
        ["cat-file", "-e", "HEAD^{commit}"],
        operation=operation,
        timeout=3.0,
        max_output_bytes=512,
        allow_failure=True,
    )
    stdout = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
    if result.returncode != 0 or stdout or result.stdout_truncated:
        _raise_history_git_failure(result, operation=operation)


def git_history(raw_path: str, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    with _pinned_git_history_scope(raw_path, operation="git_history") as scope:
        current_head = _current_head(scope, operation="gitHistory")
        if not current_head:
            if cursor:
                raise _history_error("Git history snapshot is stale", key="fs.error.gitHistoryStale", status=409)
            return {
                "path": str(scope.path),
                "repo": str(scope.repo),
                "relative_path": scope.relative_path,
                "head": "",
                "hosted_remote": scope.hosted_remote,
                "snapshot_cursor": "",
                "commits": [],
                "next_cursor": "",
                "truncated": False,
            }
        _ensure_current_head_object(scope, operation="gitHistory")
        if cursor:
            frozen_head, offset = _cursor_snapshot(scope, cursor)
            _ensure_commit_exists(
                scope,
                frozen_head,
                operation="gitHistory",
                missing_key="fs.error.gitHistoryStale",
                missing_status=409,
            )
            ancestry = _run_bounded_history_git(
                scope,
                ["merge-base", "--is-ancestor", frozen_head, current_head],
                operation="gitHistory",
                timeout=5.0,
                max_output_bytes=512,
                allow_failure=True,
            )
            if ancestry.returncode not in {0, 1}:
                _raise_history_git_failure(ancestry, operation="gitHistory")
            if ancestry.returncode == 1:
                raise _history_error("Git history snapshot is stale", key="fs.error.gitHistoryStale", status=409)
        else:
            frozen_head = current_head
            offset = 0
        page_limit = _bounded_history_limit(limit)
        snapshot_cursor = _encode_history_cursor(scope, frozen_head, 0)
        snapshot_cursor_limited = len(snapshot_cursor) > GIT_HISTORY_CURSOR_MAX_BYTES
        if snapshot_cursor_limited:
            snapshot_cursor = ""
        args = [
            "-c",
            "core.quotePath=false",
            "log",
            "--topo-order",
            "--root",
            "--diff-merges=first-parent",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
            "--find-copies-harder",
            "--numstat",
            "-z",
            f"--max-count={page_limit + 1}",
            f"--skip={offset}",
            "--format=commit%x00%H%x00%h%x00%P%x00%an%x00%at%x00%s",
            frozen_head,
            *_literal_scope_args(scope.relative_path),
        ]
        result = _run_bounded_history_git(
            scope,
            args,
            operation="gitHistory",
            timeout=10.0,
            max_output_bytes=GIT_HISTORY_MAX_OUTPUT_BYTES,
        )
        raw = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
        commits, output_truncated, metadata_truncated = _parse_history_numstat(
            raw,
            output_truncated=result.stdout_truncated,
        )
        if output_truncated and not commits:
            raise _history_error(
                "Git history entry exceeds the output limit",
                key="fs.error.gitHistoryTooLarge",
                status=413,
            )
        visible = commits[:page_limit]
        payload_bytes_truncated = False
        snapshot_cursor_truncated = False
        while True:
            has_more = output_truncated or payload_bytes_truncated or len(commits) > len(visible)
            next_offset = offset + len(visible)
            cursor_limited = snapshot_cursor_limited or (
                has_more and bool(visible) and next_offset > GIT_HISTORY_MAX_CURSOR_OFFSET
            )
            next_cursor = ""
            if has_more and visible and not cursor_limited:
                candidate_cursor = _encode_history_cursor(scope, frozen_head, next_offset)
                if len(candidate_cursor) <= GIT_HISTORY_CURSOR_MAX_BYTES:
                    next_cursor = candidate_cursor
                else:
                    cursor_limited = True
            reasons = []
            if output_truncated:
                reasons.append("output_bytes")
            if metadata_truncated:
                reasons.append("metadata_bytes")
            if payload_bytes_truncated:
                reasons.append("payload_bytes")
            if snapshot_cursor_truncated:
                reasons.append("snapshot_cursor")
            if cursor_limited:
                reasons.append("cursor_limit")
            payload = {
                "path": str(scope.path),
                "repo": str(scope.repo),
                "relative_path": scope.relative_path,
                "head": frozen_head,
                "hosted_remote": scope.hosted_remote,
                "snapshot_cursor": snapshot_cursor,
                "commits": visible,
                "next_cursor": next_cursor,
                "truncated": bool(reasons),
            }
            if reasons:
                payload["truncation_reason"] = ",".join(reasons)
            if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= GIT_HISTORY_MAX_PAYLOAD_BYTES:
                break
            if len(visible) == 1 and snapshot_cursor and next_cursor:
                snapshot_cursor = ""
                snapshot_cursor_truncated = True
                payload_bytes_truncated = True
                continue
            if not visible:
                raise _history_error(
                    "Git history metadata exceeds the payload limit",
                    key="fs.error.gitHistoryTooLarge",
                    status=413,
                )
            visible.pop()
            payload_bytes_truncated = True
        return payload


def _validated_history_oid(value: str, *, key: str) -> str:
    oid = str(value or "")
    if _GIT_OBJECT_ID_RE.fullmatch(oid) is None:
        raise _history_error("invalid Git object ID", key=key)
    return oid


def _empty_tree_oid(commit_oid: str) -> str:
    algorithm = "sha256" if len(commit_oid) == 64 else "sha1"
    return hashlib.new(algorithm, b"tree 0\0", usedforsecurity=False).hexdigest()


def _bounded_utf8(raw: bytes, limit: int) -> tuple[str, bool]:
    if len(raw) <= limit:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def _parse_commit_metadata(raw: bytes, *, output_truncated: bool) -> dict[str, Any]:
    has_terminator = raw.endswith(b"\0\n")
    if not output_truncated and not has_terminator:
        raise _history_error("malformed Git commit metadata", key="fs.error.gitCommitFailed", status=500)
    payload = raw[:-2] if has_terminator else raw
    fields = payload.split(b"\0", 5)
    if len(fields) < 6:
        if output_truncated:
            raise _history_error(
                "Git commit metadata exceeds the output limit",
                key="fs.error.gitCommitTooLarge",
                status=413,
            )
        raise _history_error("malformed Git commit metadata", key="fs.error.gitCommitFailed", status=500)
    sha, parents, author, authored_at, subject, message = fields[:6]
    authored_at_value = _parse_git_timestamp(authored_at, operation="gitCommit")
    message_text, message_truncated = _bounded_utf8(message.rstrip(b"\n"), GIT_COMMIT_MAX_MESSAGE_BYTES)
    return {
        "sha": _decode_git_text(sha),
        "parents": _decode_git_text(parents).split() if parents else [],
        "author": _decode_git_text(author),
        "authored_at": authored_at_value,
        "subject": _decode_git_text(subject),
        "message": message_text,
        "message_truncated": message_truncated or output_truncated,
    }


def _history_diff_args(mode: str, from_ref: str, to_ref: str, scope: str) -> list[str]:
    refs = ["--root", to_ref] if not from_ref else [from_ref, to_ref]
    return [
        "-c",
        "core.quotePath=false",
        "diff-tree",
        "-r",
        "--no-commit-id",
        f"--{mode}",
        "-z",
        "--find-renames",
        "--find-copies-harder",
        "--no-ext-diff",
        "--no-textconv",
        *refs,
        *_literal_scope_args(scope),
    ]


def _bounded_complete_nul_tokens(
    raw: bytes,
    *,
    output_truncated: bool,
    max_entries: int,
) -> tuple[list[bytes], bool]:
    if raw and not output_truncated and not raw.endswith(b"\0"):
        raise _history_error(
            "malformed Git diff terminator",
            key="fs.error.gitCommitFailed",
            status=500,
        )
    max_splits = max(1, int(max_entries)) * 3 + 1
    tokens = raw.split(b"\0", max_splits)
    token_limit_truncated = len(tokens) == max_splits + 1
    if token_limit_truncated:
        tokens.pop()
    elif output_truncated and raw and not raw.endswith(b"\0"):
        tokens.pop()
    return tokens, token_limit_truncated


def _parse_name_status(raw: bytes, *, output_truncated: bool) -> tuple[list[tuple[str, str, str]], bool]:
    tokens, token_limit_truncated = _bounded_complete_nul_tokens(
        raw,
        output_truncated=output_truncated,
        max_entries=GIT_COMMIT_MAX_FILES,
    )
    entries: list[tuple[str, str, str]] = []
    identities: set[tuple[str, str]] = set()
    index = 0
    truncated = output_truncated or token_limit_truncated
    while index < len(tokens):
        if len(entries) >= GIT_COMMIT_MAX_FILES:
            truncated = truncated or any(tokens[index:])
            break
        token = tokens[index]
        if not token:
            index += 1
            continue
        status_text = _decode_git_text(token)
        if re.fullmatch(r"[A-Z][0-9]{0,3}", status_text) is None:
            if output_truncated:
                truncated = True
                break
            raise _history_error("malformed Git status output", key="fs.error.gitCommitFailed", status=500)
        if status_text[0] in {"R", "C"}:
            if index + 2 >= len(tokens):
                if truncated:
                    break
                raise _history_error(
                    "malformed Git status path",
                    key="fs.error.gitCommitFailed",
                    status=500,
                )
            old_token = tokens[index + 1]
            new_token = tokens[index + 2]
            if not old_token or not new_token:
                if output_truncated:
                    truncated = True
                    break
                raise _history_error("malformed Git status path", key="fs.error.gitCommitFailed", status=500)
            old_path = _decode_git_path(old_token)
            new_path = _decode_git_path(new_token)
            index += 3
        else:
            if index + 1 >= len(tokens):
                if truncated:
                    break
                raise _history_error(
                    "malformed Git status path",
                    key="fs.error.gitCommitFailed",
                    status=500,
                )
            if not tokens[index + 1]:
                if output_truncated:
                    truncated = True
                    break
                raise _history_error("malformed Git status path", key="fs.error.gitCommitFailed", status=500)
            old_path = ""
            new_path = _decode_git_path(tokens[index + 1])
            index += 2
        identity = (old_path or new_path, new_path)
        if identity in identities:
            raise _history_error("duplicate Git status path", key="fs.error.gitCommitFailed", status=500)
        identities.add(identity)
        entries.append((status_text[0], old_path, new_path))
    return entries, truncated


def _parse_detail_numstat(raw: bytes, *, output_truncated: bool) -> tuple[dict[tuple[str, str], tuple[int | None, int | None, bool]], bool]:
    tokens, token_limit_truncated = _bounded_complete_nul_tokens(
        raw,
        output_truncated=output_truncated,
        max_entries=GIT_COMMIT_MAX_FILES,
    )
    counts: dict[tuple[str, str], tuple[int | None, int | None, bool]] = {}
    index = 0
    truncated = output_truncated or token_limit_truncated
    while index < len(tokens):
        if len(counts) >= GIT_COMMIT_MAX_FILES:
            truncated = truncated or any(tokens[index:])
            break
        token = tokens[index]
        if not token:
            index += 1
            continue
        fields = token.split(b"\t", 2)
        if len(fields) != 3:
            if output_truncated:
                truncated = True
                break
            raise _history_error("malformed Git numstat output", key="fs.error.gitCommitFailed", status=500)
        if fields[2] == b"":
            if index + 2 >= len(tokens):
                if truncated:
                    break
                raise _history_error(
                    "malformed Git numstat path",
                    key="fs.error.gitCommitFailed",
                    status=500,
                )
            old_token = tokens[index + 1]
            new_token = tokens[index + 2]
            if not old_token or not new_token:
                if output_truncated:
                    truncated = True
                    break
                raise _history_error("malformed Git numstat path", key="fs.error.gitCommitFailed", status=500)
            old_path = _decode_git_path(old_token)
            new_path = _decode_git_path(new_token)
            index += 3
        else:
            new_path = _decode_git_path(fields[2])
            old_path = new_path
            index += 1
        identity = (old_path, new_path)
        if identity in counts:
            raise _history_error("duplicate Git numstat path", key="fs.error.gitCommitFailed", status=500)
        added_is_binary = fields[0] == b"-"
        removed_is_binary = fields[1] == b"-"
        if added_is_binary != removed_is_binary:
            raise _history_error("malformed Git numstat counts", key="fs.error.gitCommitFailed", status=500)
        binary = added_is_binary
        if binary:
            counts[identity] = (None, None, True)
            continue
        try:
            added = int(fields[0])
            removed = int(fields[1])
            if added < 0 or removed < 0:
                raise ValueError("negative Git numstat count")
            counts[identity] = (added, removed, False)
        except ValueError as error:
            raise _history_error("malformed Git numstat counts", key="fs.error.gitCommitFailed", status=500) from error
    return counts, truncated


def _validate_historical_path(repo: Path, relative_path: str) -> None:
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise paths.FilesystemError.outside_repo(repo / relative_path)
    candidate = repo.joinpath(*pure_path.parts)
    paths._ensure_path_allowed(candidate, resolved=candidate)


def git_commit(raw_path: str, *, commit: str, head: str) -> dict[str, Any]:
    requested_commit = _validated_history_oid(commit, key="fs.error.gitCommitUnknown")
    frozen_head = _validated_history_oid(head, key="fs.error.gitHistoryStale")
    with _pinned_git_history_scope(raw_path, operation="git_commit") as scope:
        current_head = _current_head(scope, operation="gitCommit")
        if not current_head:
            raise _history_error("Git history snapshot is stale", key="fs.error.gitHistoryStale", status=409)
        _ensure_current_head_object(scope, operation="gitCommit")
        _ensure_commit_exists(
            scope,
            frozen_head,
            operation="gitCommit",
            missing_key="fs.error.gitHistoryStale",
            missing_status=409,
        )
        _ensure_commit_exists(scope, requested_commit)
        metadata_result = _run_bounded_history_git(
            scope,
            [
                "show",
                "--no-patch",
                "--format=%H%x00%P%x00%an%x00%at%x00%s%x00%B%x00",
                requested_commit,
            ],
            operation="gitCommit",
            timeout=5.0,
            max_output_bytes=GIT_COMMIT_MAX_MESSAGE_BYTES + GIT_COMMIT_METADATA_OVERHEAD_BYTES,
            allow_failure=True,
        )
        if metadata_result.returncode != 0:
            if not _was_killed_for_output_cap(metadata_result):
                _raise_history_git_failure(metadata_result, operation="gitCommit")
        metadata_raw = metadata_result.stdout if isinstance(metadata_result.stdout, bytes) else metadata_result.stdout.encode("utf-8")
        metadata = _parse_commit_metadata(metadata_raw, output_truncated=metadata_result.stdout_truncated)
        if metadata["sha"] != requested_commit:
            raise _history_error("unknown Git commit", key="fs.error.gitCommitUnknown", status=404)
        for ancestor, descendant, key in (
            (frozen_head, current_head, "fs.error.gitHistoryStale"),
            (requested_commit, frozen_head, "fs.error.gitCommitUnknown"),
        ):
            relation = _run_bounded_history_git(
                scope,
                ["merge-base", "--is-ancestor", ancestor, descendant],
                operation="gitCommit",
                timeout=5.0,
                max_output_bytes=512,
                allow_failure=True,
            )
            if relation.returncode not in {0, 1}:
                _raise_history_git_failure(relation, operation="gitCommit")
            if relation.returncode == 1:
                raise _history_error(
                    "Git commit is outside the frozen history snapshot",
                    key=key,
                    status=409 if key == "fs.error.gitHistoryStale" else 404,
                    diagnostic=_git_result_error_text(relation),
                )
        parents = metadata["parents"]
        from_ref = parents[0] if parents else _empty_tree_oid(requested_commit)
        status_result = _run_bounded_history_git(
            scope,
            _history_diff_args("name-status", parents[0] if parents else "", requested_commit, scope.relative_path),
            operation="gitCommit",
            timeout=10.0,
            max_output_bytes=GIT_COMMIT_MAX_OUTPUT_BYTES,
        )
        numstat_result = _run_bounded_history_git(
            scope,
            _history_diff_args("numstat", parents[0] if parents else "", requested_commit, scope.relative_path),
            operation="gitCommit",
            timeout=10.0,
            max_output_bytes=GIT_COMMIT_MAX_OUTPUT_BYTES,
        )
        status_raw = status_result.stdout if isinstance(status_result.stdout, bytes) else status_result.stdout.encode("utf-8")
        numstat_raw = numstat_result.stdout if isinstance(numstat_result.stdout, bytes) else numstat_result.stdout.encode("utf-8")
        status_entries, status_truncated = _parse_name_status(status_raw, output_truncated=status_result.stdout_truncated)
        counts, counts_truncated = _parse_detail_numstat(numstat_raw, output_truncated=numstat_result.stdout_truncated)
        rows = []
        status_keys = set()
        for status_value, old_path, path_value in status_entries:
            _validate_historical_path(scope.repo, path_value)
            if old_path:
                _validate_historical_path(scope.repo, old_path)
            key = (old_path or path_value, path_value)
            status_keys.add(key)
            count_values = counts.get(key)
            if count_values is None:
                if not counts_truncated:
                    raise _history_error(
                        "Git status and numstat output disagree",
                        key="fs.error.gitCommitFailed",
                        status=500,
                    )
                added, removed, binary = None, None, False
            else:
                added, removed, binary = count_values
            rows.append({
                "status": status_value,
                "path": path_value,
                "old_path": old_path,
                "added": added,
                "removed": removed,
                "binary": binary,
                "counts_available": count_values is not None,
            })
        if not status_truncated and not counts_truncated and set(counts) != status_keys:
            raise _history_error(
                "Git status and numstat output disagree",
                key="fs.error.gitCommitFailed",
                status=500,
            )
        if not rows and status_truncated:
            raise _history_error(
                "Git commit file list exceeds the output limit",
                key="fs.error.gitCommitTooLarge",
                status=413,
            )
        if not rows:
            raise _history_error(
                "commit does not change the selected history scope",
                key="fs.error.gitCommitScope",
                status=404,
            )
        files_truncated = status_truncated or counts_truncated or len(rows) > GIT_COMMIT_MAX_FILES
        visible_rows = rows[:GIT_COMMIT_MAX_FILES]
        payload = {
            "repo": str(scope.repo),
            "scope_path": scope.relative_path,
            "sha": metadata["sha"],
            "parents": parents,
            "from_ref": from_ref,
            "to_ref": metadata["sha"],
            "subject": metadata["subject"],
            "author": metadata["author"],
            "authored_at": metadata["authored_at"],
            "message": metadata["message"],
            "message_truncated": metadata["message_truncated"],
            "files": visible_rows,
            "files_truncated": files_truncated,
            "truncated": metadata["message_truncated"] or files_truncated,
        }
        payload_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if payload_size > GIT_COMMIT_MAX_PAYLOAD_BYTES:
            payload["files_truncated"] = True
            payload["truncated"] = True
            candidates = visible_rows
            low = 0
            high = len(candidates)
            while low < high:
                middle = (low + high + 1) // 2
                payload["files"] = candidates[:middle]
                candidate_size = len(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                )
                if candidate_size <= GIT_COMMIT_MAX_PAYLOAD_BYTES:
                    low = middle
                else:
                    high = middle - 1
            visible_rows = candidates[:low]
            payload["files"] = visible_rows
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > GIT_COMMIT_MAX_PAYLOAD_BYTES:
            raise _history_error(
                "Git commit metadata exceeds the payload limit",
                key="fs.error.gitCommitTooLarge",
                status=413,
            )
        return payload


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

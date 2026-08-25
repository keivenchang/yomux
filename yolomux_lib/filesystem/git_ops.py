"""Git-backed filesystem operations."""

from __future__ import annotations

import base64
import contextlib
import copy
import difflib
import errno
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Callable
from urllib.parse import urlsplit

from ..common import git
from ..common import record_git_spawn
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
    stdin_descriptor: int | None = None,
) -> subprocess.CompletedProcess[Any] | PinnedGitResult:
    if git is not _COMMON_GIT:
        injected = git(args, cwd=str(repo.descriptor_path()), timeout=timeout)
        if max_output_bytes is None:
            return injected
        raw_stdout = injected.stdout if isinstance(injected.stdout, bytes) else str(injected.stdout or "").encode("utf-8")
        raw_stderr = injected.stderr if isinstance(injected.stderr, bytes) else str(injected.stderr or "").encode("utf-8")
        stdout_truncated = len(raw_stdout) > max_output_bytes
        retained_stdout = raw_stdout[:max_output_bytes]
        return PinnedGitResult(
            args=list(args),
            returncode=-9 if stdout_truncated else injected.returncode,
            stdout=retained_stdout if binary else retained_stdout.decode("utf-8", errors="replace"),
            stderr=raw_stderr if binary else raw_stderr.decode("utf-8", errors="replace"),
            stdout_truncated=stdout_truncated,
            killed_for_cap=stdout_truncated,
        )
    record_git_spawn(args)
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
        process_env["GIT_OPTIONAL_LOCKS"] = "0"
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
            stdin=stdin_descriptor,
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
        stdin=stdin_descriptor,
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
    stdin_descriptor: int | None = None,
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
        "stdin_descriptor": stdin_descriptor,
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


def authorized_repo_root(raw_path: str | Path, *, operation: str) -> Path | None:
    """Discover one authorized repository boundary without constructing a Git object view."""

    deadline = time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(raw_path), flags=directory_flags, operation=operation) as handle:
        root = _pinned_repo_root(handle, deadline=deadline, operation=operation)
        _ensure_pinned_namespace_unchanged(handle)
        return root


@contextlib.contextmanager
def pinned_repo_path(handle: paths.SafePathHandle, *, operation: str = ""):
    """Keep one authorized Git control/index generation live through a namespace move."""

    try:
        with pinned_git_scope_from_handle(
            handle,
            target_path=handle.resolved,
            operation=operation,
            deadline=time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS,
            include_index=True,
            index_mutation=True,
        ) as scope:
            yield scope
    except paths.FilesystemError as error:
        if error.message_key != "fs.error.notGitRepo":
            raise
        yield None


def git_branch_state(
    repo: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> tuple[str, bool]:
    """Return the checked-out branch and whether Git positively reported detached HEAD."""
    symbolic = runner(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if symbolic.returncode == 0:
        return symbolic.stdout.strip(), False
    resolved = runner(["rev-parse", "--abbrev-ref", "HEAD"])
    if resolved.returncode != 0:
        return "", False
    name = resolved.stdout.strip()
    return ("", True) if name == "HEAD" else (name, False)


def git_branch_name(
    repo: Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
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


def _advance_repository_generation(root_text: str, signature: tuple[str, ...]) -> int:
    """Apply one already-measured private signature to the shared generation owner."""

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


def _pinned_repo_info_signature(scope: PinnedGitHistoryScope) -> tuple[Any, ...]:
    digest = hashlib.sha256()
    control_root = Path(scope.git_directory)
    for current_root, directory_names, file_names in os.walk(control_root):
        directory_names.sort()
        file_names.sort()
        relative_root = Path(current_root).relative_to(control_root)
        for name in directory_names:
            digest.update(b"d\0")
            digest.update((relative_root / name).as_posix().encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
        for name in file_names:
            path = Path(current_root) / name
            if path.is_symlink():
                raise _history_error(
                    "Git control snapshot contains an unsafe link",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            digest.update(b"f\0")
            digest.update((relative_root / name).as_posix().encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
    objects = scope.git_objects_handle.stat_result
    return (
        digest.hexdigest(),
        objects.st_dev,
        objects.st_ino,
        objects.st_mtime_ns,
        objects.st_ctime_ns,
    )


def _git_repo_info_from_scope(
    scope: PinnedGitHistoryScope,
    *,
    display_root: Path,
    include_status: bool,
    deadline: float | None,
) -> dict[str, Any]:
    root = str(display_root)
    cache_key = (root, bool(include_status))
    now = time.monotonic()
    signature = _pinned_repo_info_signature(scope)
    with _REPO_INFO_CACHE_LOCK:
        cached = _REPO_INFO_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature and now - cached[1] <= _repo_info_cache_ttl_seconds(root):
            return copy.deepcopy(cached[2])
    timed_out = False

    def run(args: list[str], default_timeout: float) -> subprocess.CompletedProcess[str] | PinnedGitResult:
        nonlocal timed_out
        command_timeout = default_timeout
        if deadline is not None:
            command_timeout = deadline - time.monotonic()
            if command_timeout < REPO_INFO_MINIMUM_COMMAND_TIMEOUT_SECONDS:
                timed_out = True
                return subprocess.CompletedProcess(args, 124, "", "Finder Git-info budget expired")
        try:
            result = _run_pinned_git(
                scope,
                args,
                operation="gitRepoInfo",
                timeout=command_timeout,
                max_output_bytes=4 * 1024 * 1024,
                allow_failure=True,
            )
        except paths.FilesystemError as error:
            if error.status != 504:
                raise
            timed_out = True
            return subprocess.CompletedProcess(args, 124, "", "Finder Git-info command timed out")
        timed_out = timed_out or result.returncode == 124 or (deadline is not None and time.monotonic() >= deadline)
        return result

    branch, detached = git_branch_state(display_root, runner=lambda args: run(args, 1.0))
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
        "name": display_root.name,
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


def pinned_git_repo_info(
    repo_handle: paths.SafePathHandle,
    *,
    display_root: Path,
    include_status: bool = True,
    timeout: float | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = (
        started + GIT_VIEW_BUILD_TIMEOUT_SECONDS
        if timeout is None
        else started + max(0.0, float(timeout))
    )
    with pinned_git_scope_from_handle(
        repo_handle,
        target_path=display_root,
        operation="git_repo_info",
        deadline=deadline,
        include_index=True,
    ) as scope:
        return _git_repo_info_from_scope(
            scope,
            display_root=display_root,
            include_status=include_status,
            deadline=deadline,
        )


def git_repo_info(repo: Path, include_status: bool = True, timeout: float | None = None) -> dict[str, Any]:
    """Return descriptor-bound repo badges within the caller's whole-operation timeout."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(str(repo), flags=directory_flags, operation="git_repo_info") as repo_handle:
        return pinned_git_repo_info(
            repo_handle,
            display_root=repo_handle.resolved,
            include_status=include_status,
            timeout=timeout,
        )


def pinned_file_git_metadata(
    handle: paths.SafePathHandle,
    *,
    include_repo_info: bool = False,
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
    history_limit: int = 60,
    operation: str = "",
    deadline_seconds: float | None = None,
) -> tuple[str, bool, list[dict[str, Any]], str, dict[str, Any] | None]:
    # Resolved here rather than as a default because the budget constants are declared below.
    budget = GIT_VIEW_BUILD_TIMEOUT_SECONDS if deadline_seconds is None else deadline_seconds
    is_directory = stat.S_ISDIR(handle.stat_result.st_mode)
    if is_directory:
        repo = _pinned_repo_root(
            handle,
            deadline=time.monotonic() + budget,
            operation=operation,
        )
        if repo is None:
            return "", False, [], "", None
        try:
            relative_path = handle.resolved.relative_to(repo).as_posix()
        except ValueError as error:
            raise paths.FilesystemError.outside_repo(handle.resolved) from error
        repo_info = None
        if include_repo_info and relative_path == ".":
            cache_key = str(repo)
            if repo_info_cache is not None and cache_key in repo_info_cache:
                repo_info = copy.deepcopy(repo_info_cache[cache_key])
            else:
                repo_info = git_repo_info(repo, include_status=True)
                if repo_info_cache is not None:
                    repo_info_cache[cache_key] = copy.deepcopy(repo_info)
        return str(repo), False, [], "" if relative_path == "." else relative_path, repo_info
    try:
        scope_context = pinned_git_scope_from_handle(
            handle,
            target_path=handle.resolved,
            operation=operation,
            deadline=time.monotonic() + budget,
            include_index=True,
        )
        with scope_context as scope:
            rel_path = scope.relative_path
            tracked = False if is_directory else _run_pinned_git(
                scope,
                ["ls-files", "--error-unmatch", "--", rel_path],
                operation="gitMetadata",
                timeout=1.5,
                max_output_bytes=64 * 1024,
                allow_failure=True,
            ).returncode == 0
            history: list[dict[str, Any]] = []
            if tracked:
                result = _run_pinned_git(
                    scope,
                    [
                        "log",
                        "--follow",
                        f"--max-count={max(1, min(int(history_limit), 100))}",
                        "--format=%H%x1f%h%x1f%s%x1f%ct%x1f%an",
                        "--",
                        rel_path,
                    ],
                    operation="gitMetadata",
                    timeout=3.0,
                    max_output_bytes=4 * 1024 * 1024,
                    allow_failure=True,
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
                repo_info = _git_repo_info_from_scope(
                    scope,
                    display_root=scope.repo,
                    include_status=True,
                    deadline=None,
                )
            return str(scope.repo), tracked, history, rel_path, repo_info
    except paths.FilesystemError as error:
        if error.message_key != "fs.error.notGitRepo":
            raise
        return "", False, [], "", None



# Opening a file must not wait on Git. Enrichment gets its own short budget, well under the full
# view-build deadline, because a reader who asked for a file is not asking for its history.
GIT_OPTIONAL_METADATA_TIMEOUT_SECONDS = 2.0

# The only failures Open is allowed to shrug off. A repository that is merely too big or too slow
# says nothing about whether the file is safe to hand over. Everything else -- an object store
# swapped after the pin, a control directory that moved, a path that escaped its root -- is the
# repository telling us it is not the repository we authorized, and those must still refuse,
# because that refusal is what keeps another repository's contents from being served as this one's.
GIT_OPTIONAL_METADATA_DEGRADABLE_KEYS = frozenset({"fs.error.gitHistoryTooLarge"})


def optional_pinned_file_git_metadata(
    handle: paths.SafePathHandle,
    *,
    include_repo_info: bool = False,
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
    history_limit: int = 60,
    operation: str = "",
    deadline_seconds: float | None = None,
) -> tuple[str, bool, list[dict[str, Any]], str, dict[str, Any] | None, str]:
    """Return Git metadata for a path, or say why there is none, but never fail the caller.

    Reading a file and describing its Git history are two different questions, and the second one
    must not be able to answer the first. A repository whose objects are not packed, a repository
    being rewritten underneath us, or a Git view that runs out of budget all used to propagate out
    of `read_file` and leave the user unable to open a file that had already been read successfully.

    Only a size or budget failure is shrugged off, and it is reported rather than discarded: the
    sixth element is the empty string on success and the error's own message key otherwise, so a
    caller can surface exactly why the decoration is absent. A repository that reports it is not the
    one we authorized still refuses, because that refusal is the thing stopping another repository's
    contents from being served as this one's.
    """

    try:
        repo, tracked, history, relative_path, repo_info = pinned_file_git_metadata(
            handle,
            include_repo_info=include_repo_info,
            repo_info_cache=repo_info_cache,
            history_limit=history_limit,
            operation=operation,
            deadline_seconds=GIT_OPTIONAL_METADATA_TIMEOUT_SECONDS if deadline_seconds is None else deadline_seconds,
        )
    except paths.FilesystemError as error:
        if error.message_key not in GIT_OPTIONAL_METADATA_DEGRADABLE_KEYS:
            raise
        return "", False, [], "", None, error.message_key
    return repo, tracked, history, relative_path, repo_info, ""


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
GIT_INDEX_MAX_BYTES = 64 * 1024 * 1024
GIT_INDEX_RENAME_MAX_ENTRIES = 100_000
GIT_INDEX_RENAME_MAX_INPUT_BYTES = 16 * 1024 * 1024
GIT_SHALLOW_MAX_BYTES = 4 * 1024 * 1024
GIT_VIEW_BUILD_TIMEOUT_SECONDS = 10.0
GIT_VIEW_MAX_LOOSE_OBJECTS = 16_384
GIT_VIEW_MAX_LOOSE_BYTES = 256 * 1024 * 1024
# Reading loose objects is latency-bound, not CPU-bound: one open+read costs ~2 us on a local
# disk and ~2.4 ms over NFS, so a 5k-object store costs ~12 s of serial round trips and overruns
# the view deadline. Overlapping the reads removes the latency without reading anything extra.
GIT_VIEW_LOOSE_OBJECT_WORKERS = 16
GIT_LOOSE_OBJECT_CACHE_MAX_ENTRIES = 32_768
GIT_LOOSE_OBJECT_CACHE_MAX_BYTES = 512 * 1024 * 1024
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
    index_snapshot: GitIndexSnapshot | None
    initial_loose_objects: frozenset[str]


@dataclass(frozen=True)
class GitIndexSnapshot:
    stat_identity: tuple[int, int, int, int, int]
    digest: str


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
    # A view build materializes its loose objects from several workers at once, so the running
    # totals below are shared mutable state and every read-modify-write must be serialized.
    guard: threading.RLock = dataclasses_field(default_factory=threading.RLock)

    def check(self) -> None:
        _ensure_git_view_deadline(self.deadline)
        with self.guard:
            exceeded = self.entries > self.max_entries or self.bytes > self.max_bytes
        if exceeded:
            raise _history_error(
                "Git repository metadata exceeds the snapshot limit",
                key="fs.error.gitHistoryTooLarge",
                status=413,
            )

    def consume(self, *, size: int = 0) -> None:
        with self.guard:
            self.entries += 1
            self.bytes += max(0, int(size))
        self.check()


@dataclass
class GitLooseObjectCacheSession:
    root: Path
    entries: int
    bytes: int
    dirty: bool = False


def _git_loose_object_cache_root() -> Path:
    """Return the validated boot-local cache root without taking its writer lock."""

    runtime_root = Path(tempfile.gettempdir()) / f"yolomux-{os.geteuid()}"
    runtime_root.mkdir(mode=0o700, exist_ok=True)
    runtime_info = runtime_root.lstat()
    if not stat.S_ISDIR(runtime_info.st_mode) or stat.S_ISLNK(runtime_info.st_mode) or runtime_info.st_uid != os.geteuid():
        raise _history_error(
            "Git loose-object cache root is unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    cache_root = runtime_root / "git-object-pins-v1"
    cache_root.mkdir(mode=0o700, exist_ok=True)
    cache_info = cache_root.lstat()
    if not stat.S_ISDIR(cache_info.st_mode) or stat.S_ISLNK(cache_info.st_mode) or cache_info.st_uid != os.geteuid():
        raise _history_error(
            "Git loose-object cache is unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    return cache_root


class _LazyLooseObjectWorkers:
    """Start the loose-object worker pool only once a build actually has to read the repository.

    A fully cached build publishes every object with a local stat and symlink, so starting workers
    for it is pure overhead; it measured about 0.3 s on a 5k-object store, and the pool would also
    contend with whatever else the host is running.
    """

    def __init__(self, stack: contextlib.ExitStack) -> None:
        self._stack = stack
        self._pool: ThreadPoolExecutor | None = None

    def map(self, function, items):
        if self._pool is None:
            self._pool = self._stack.enter_context(
                ThreadPoolExecutor(
                    max_workers=GIT_VIEW_LOOSE_OBJECT_WORKERS,
                    thread_name_prefix="git-view-loose",
                )
            )
        return self._pool.map(function, items)


class GitLooseObjectCacheLease:
    """Own the shared cache session for one view build, and guard its accounting across workers.

    Taking the cross-process cache lock and flushing its accounting once per object costs one
    lock cycle and one fsync per object, which measured 3.1 s of a 13.4 s cold build on a
    5k-object store. A fully cached build publishes nothing, so this opens the session lazily
    and never contends at all; a cold build opens it once and holds it for that build.

    Only the accounting fields are guarded here. Reading a loose object out of the repository
    is the expensive part of a cold build and deliberately runs outside this guard, so workers
    overlap on the network filesystem instead of queueing behind each other.
    """

    def __init__(self, stack: contextlib.ExitStack) -> None:
        self._stack = stack
        self._session: GitLooseObjectCacheSession | None = None
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def accounting(self):
        with self._guard:
            if self._session is None:
                self._session = self._stack.enter_context(_git_loose_object_cache_session())
            yield self._session


@contextlib.contextmanager
def _git_loose_object_cache_session():
    """Serialize cache publication and accounting, leaving immutable hits lock-free."""

    cache_root = _git_loose_object_cache_root()
    lock_descriptor = os.open(
        cache_root / ".lock",
        os.O_RDWR | os.O_CREAT | paths.nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
    state_path = cache_root / ".state.json"
    try:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            entries = int(state["entries"])
            byte_count = int(state["bytes"])
            if entries < 0 or byte_count < 0:
                raise ValueError("negative cache state")
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            entries = 0
            byte_count = 0
            with os.scandir(cache_root) as cached_entries:
                for entry in cached_entries:
                    if not entry.name.startswith("object-") or not entry.is_file(follow_symlinks=False):
                        continue
                    metadata = entry.stat(follow_symlinks=False)
                    entries += 1
                    byte_count += metadata.st_size
        session = GitLooseObjectCacheSession(cache_root, entries, byte_count)
        try:
            yield session
        finally:
            _flush_git_loose_object_cache_state(session, cache_root, state_path)
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def _flush_git_loose_object_cache_state(
    session: GitLooseObjectCacheSession,
    cache_root: Path,
    state_path: Path,
) -> None:
    """Persist cache accounting, including when the build that filled it aborted part-way.

    A build that overruns its deadline still leaves every object it published in the cache, so
    dropping the accounting would let the cache grow past its own cap.
    """

    if not session.dirty:
        return
    state_bytes = json.dumps(
        {"entries": session.entries, "bytes": session.bytes},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    temporary_state = cache_root / f".state-{os.getpid()}-{threading.get_ident()}"
    state_descriptor = os.open(
        temporary_state,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | paths.nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.write(state_descriptor, state_bytes)
        os.fsync(state_descriptor)
    finally:
        os.close(state_descriptor)
    os.replace(temporary_state, state_path)
    session.dirty = False


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
    index_snapshot: GitIndexSnapshot | None
    initial_loose_objects: frozenset[str]
    index_lock_descriptor: int | None = None


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
    legacy_worktree_config = extensions == {"worktreeconfig": "true"}
    if repository_format_version == 0 and extensions and not legacy_worktree_config:
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


def _git_remote_urls(config_data: bytes) -> list[tuple[str, str]]:
    current_remote = ""
    remotes: list[tuple[str, str]] = []
    for raw_line in config_data.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            match = re.fullmatch(r'\[\s*remote\s+"([^"\\]+)"\s*\]', line, flags=re.IGNORECASE)
            current_remote = match.group(1) if match is not None else ""
            continue
        if not current_remote:
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
        remotes.append((current_remote, value))
    return remotes


def _hosted_git_remote_url(raw_url: str) -> dict[str, str] | None:
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


def _hosted_git_remote(config_data: bytes) -> dict[str, str] | None:
    candidates: list[tuple[int, int, dict[str, str]]] = []
    for index, (name, raw_url) in enumerate(_git_remote_urls(config_data)):
        remote = _hosted_git_remote_url(raw_url)
        if remote is not None:
            candidates.append(({"origin": 0, "upstream": 1}.get(name, 2), index, remote))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _snapshot_regular_child(
    parent_handle: paths.SafePathHandle,
    source_path: Path,
    destination: Path,
    *,
    budget: GitViewBudget,
    operation: str,
    consume_budget: bool = True,
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
            if consume_budget:
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


def _pinned_loose_object_cache_name(repo_key: str, prefix: str, suffix: str) -> str:
    """Name one boot-local cache entry from the repository and the object's own content hash.

    The key is partitioned by repository so a loose object published from one repository can
    never be republished into another repository's view under the same name.
    """

    digest = hashlib.sha256(f"{repo_key}:{prefix}/{suffix}".encode("ascii")).hexdigest()
    return f"object-{digest}"


def _publish_cached_loose_object(
    cache_path: Path,
    destination: Path,
    *,
    budget: GitViewBudget,
) -> tuple[int, int, int, int, int] | None:
    """Republish an already-pinned cache entry without touching the source object.

    Returns the published identity, or None when the entry is not usable and the caller must
    fall back to the full source-verifying path.
    """

    try:
        cache_metadata = os.stat(cache_path, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(cache_metadata.st_mode):
        return None
    budget.consume(size=cache_metadata.st_size)
    os.symlink(cache_path, destination)
    destination_metadata = os.stat(destination, follow_symlinks=True)
    if (destination_metadata.st_dev, destination_metadata.st_ino) != (
        cache_metadata.st_dev,
        cache_metadata.st_ino,
    ):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(destination)
        raise _history_error(
            "Git loose object changed during snapshot publication",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    return (
        destination_metadata.st_dev,
        destination_metadata.st_ino,
        destination_metadata.st_size,
        destination_metadata.st_mtime_ns,
        destination_metadata.st_ctime_ns,
    )


def _publish_copied_loose_object(
    source_descriptor: int,
    cache_path: Path,
    cache_root: Path,
    metadata: os.stat_result,
) -> os.stat_result | None:
    """Copy one loose object into the boot-local cache when it cannot be hardlinked.

    A hardlink from a network filesystem into the boot-local cache always fails with EXDEV,
    which previously meant the cache stayed permanently empty for exactly the repositories
    that need it most. The object's file name is its content hash, so an independently
    written copy is as authoritative as a link; the caller still re-checks the source
    generation after the copy, so a source rewritten mid-copy is rejected rather than cached.
    Returns the published entry's metadata, or None when another writer won the race.
    """

    temporary = cache_root / f".pending-{os.getpid()}-{threading.get_ident()}-{cache_path.name}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | paths.nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
        0o400,
    )
    try:
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        written = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            offset = 0
            while offset < len(chunk):
                offset += os.write(descriptor, chunk[offset:])
        if written != metadata.st_size:
            raise _history_error(
                "Git loose object changed during cache publication",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
    except BaseException:
        os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    os.close(descriptor)
    try:
        os.link(temporary, cache_path, follow_symlinks=False)
    except FileExistsError:
        return None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
    return os.stat(cache_path, follow_symlinks=False)


def _materialize_pinned_loose_object(
    cache_root: Path,
    parent_handle: paths.SafePathHandle,
    source_path: Path,
    destination: Path,
    *,
    repo_key: str,
    budget: GitViewBudget,
    operation: str,
    cache_lease: GitLooseObjectCacheLease,
    cached_only: bool = False,
) -> tuple[Path, tuple[int, int, int, int, int]] | None:
    """Pin one loose-object inode into the request view without retaining one fd per object.

    With `cached_only`, return None rather than reading the repository, so a caller can settle
    every already-cached object inline and reserve its workers for the ones that need the network.
    """

    try:
        prefix = source_path.parent.name
        suffix = source_path.name
        if (
            source_path.parent != parent_handle.resolved
            or re.fullmatch(r"[0-9a-f]{2}", prefix) is None
            or re.fullmatch(r"(?:[0-9a-f]{38}|[0-9a-f]{62})", suffix) is None
        ):
            raise _history_error(
                "Git loose-object name is unsafe",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        cache_path = cache_root / _pinned_loose_object_cache_name(repo_key, prefix, suffix)
        # Content-addressed fast path. A loose object's file name is the hash of its own
        # content, and Git only ever creates one by writing a temporary file and renaming it
        # into place, so within a single repository that name can never denote two different
        # payloads. Once this boot has hardlinked the object into the cache we can republish
        # it from the cache alone. That is what keeps a repository on a network filesystem
        # usable: opening and fstat-ing the source costs ~2.4 ms per object over NFS, so a
        # 5k-object store costs ~12 s of pure setup on every request, which overruns both the
        # server-side snapshot deadline and the browser's own request deadline. Publishing
        # from the cache instead touches no network filesystem at all.
        cached_identity = _publish_cached_loose_object(cache_path, destination, budget=budget)
        if cached_identity is not None:
            return destination, cached_identity
        if cached_only:
            return None
        source_descriptor = os.open(
            suffix,
            os.O_RDONLY | paths.nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_handle.descriptor,
        )
        try:
            metadata = os.fstat(source_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _history_error(
                    "Git repository metadata is not a regular file",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            budget.consume(size=metadata.st_size)
            with cache_lease.accounting() as cache:
                may_publish = (
                    cache.entries < GIT_LOOSE_OBJECT_CACHE_MAX_ENTRIES
                    and cache.bytes + metadata.st_size <= GIT_LOOSE_OBJECT_CACHE_MAX_BYTES
                )
            cached = False
            # Only an entry this call hardlinked shares the source inode. A copied entry, or one
            # another worker published first, is a distinct inode and is verified as a copy.
            cache_hardlinked = False
            published_bytes = 0
            if may_publish:
                # Deliberately outside the accounting guard: on a network filesystem this is
                # where a cold build spends nearly all of its time, and serializing it would
                # undo the concurrency the caller went to the trouble of arranging.
                try:
                    os.link(
                        source_path.name,
                        cache_path,
                        src_dir_fd=parent_handle.descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    cached = True
                except OSError as error:
                    if error.errno != errno.EXDEV:
                        raise
                    if _publish_copied_loose_object(source_descriptor, cache_path, cache_root, metadata) is not None:
                        published_bytes = metadata.st_size
                    cached = True
                else:
                    cache_metadata = os.stat(cache_path, follow_symlinks=False)
                    if (cache_metadata.st_dev, cache_metadata.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise _history_error(
                            "Git loose object changed during cache publication",
                            key="fs.error.gitRepositoryChanged",
                            status=409,
                        )
                    published_bytes = metadata.st_size
                    cached = True
                    cache_hardlinked = True
            if published_bytes:
                with cache_lease.accounting() as cache:
                    cache.entries += 1
                    cache.bytes += published_bytes
                    cache.dirty = True
            if cached:
                os.symlink(cache_path, destination)
            else:
                _snapshot_regular_child(
                    parent_handle,
                    source_path,
                    destination,
                    budget=budget,
                    operation=operation,
                    consume_budget=False,
                )
            destination_metadata = os.stat(destination, follow_symlinks=True)
            source_current = os.fstat(source_descriptor)
            source_identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            current_identity = (
                source_current.st_dev,
                source_current.st_ino,
                source_current.st_size,
                source_current.st_mtime_ns,
                source_current.st_ctime_ns,
            )
            destination_identity = (
                destination_metadata.st_dev,
                destination_metadata.st_ino,
                destination_metadata.st_size,
                destination_metadata.st_mtime_ns,
                destination_metadata.st_ctime_ns,
            )
            hardlinked = cache_hardlinked
            source_generation_unchanged = current_identity[:4] == source_identity[:4]
            copied_source_unchanged = hardlinked or current_identity[4] == source_identity[4]
            published_generation_matches = not hardlinked or destination_identity == current_identity
            if not source_generation_unchanged or not copied_source_unchanged or not published_generation_matches:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(destination)
                raise _history_error(
                    "Git loose object changed during snapshot publication",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            return destination, destination_identity
        finally:
            os.close(source_descriptor)
    except (paths.FilesystemError, OSError) as error:
        if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
            raise
        raise _history_error(
            "Git repository metadata is unavailable or unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error


def _snapshot_git_index(
    git_dir_handle: paths.SafePathHandle,
    destination: Path,
    *,
    operation: str,
) -> GitIndexSnapshot | None:
    index_path = git_dir_handle.resolved / "index"
    try:
        index_context = paths.safe_child(
            git_dir_handle.descriptor,
            index_path,
            index_path,
            operation=operation,
            observe_name=False,
        )
        with index_context as index_handle:
            metadata = index_handle.stat_result
            if not stat.S_ISREG(metadata.st_mode):
                raise _history_error(
                    "Git index is not a regular file",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            if metadata.st_size > GIT_INDEX_MAX_BYTES:
                raise _history_error(
                    "Git index exceeds the metadata limit",
                    key="fs.error.gitHistoryTooLarge",
                    status=413,
                )
            digest = hashlib.sha256()
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o400,
            )
            try:
                offset = 0
                while offset < metadata.st_size:
                    chunk = os.pread(
                        index_handle.descriptor,
                        min(64 * 1024, metadata.st_size - offset),
                        offset,
                    )
                    if not chunk:
                        raise _history_error(
                            "Git index changed during snapshot",
                            key="fs.error.gitRepositoryChanged",
                            status=409,
                        )
                    digest.update(chunk)
                    written = 0
                    while written < len(chunk):
                        written += os.write(destination_descriptor, chunk[written:])
                    offset += len(chunk)
            finally:
                os.close(destination_descriptor)
            current = os.fstat(index_handle.descriptor)
            identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            if (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            ) != identity:
                raise _history_error(
                    "Git index changed during snapshot",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            # Git's racy-clean check compares worktree mtimes with the index-file timestamp. A newly
            # created copy would look newer than both and let a same-size, same-mtime content change
            # be trusted as clean. Preserve the authorized source index timestamp so Git performs the
            # same content verification against the snapshot that it would against the live index.
            os.utime(
                destination,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                follow_symlinks=False,
            )
            return GitIndexSnapshot(stat_identity=identity, digest=digest.hexdigest())
    except (paths.FilesystemError, OSError) as error:
        if isinstance(error, paths.FilesystemError) and error.status == 404:
            return None
        if isinstance(error, paths.FilesystemError) and error.message_key.startswith("fs.error.git"):
            raise
        raise _history_error(
            "Git index is unavailable or unsafe",
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


def _materialize_pinned_loose_object_store(
    objects_handle: paths.SafePathHandle,
    objects_path: Path,
    object_directory: Path,
    *,
    object_format: str,
    deadline: float,
    operation: str,
) -> tuple[list[tuple[Path, tuple[int, int, int, int, int]]], set[str]]:
    """Build one request view while holding the shared cache lock only for publication."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    loose_budget = GitViewBudget(
        deadline=deadline,
        max_entries=GIT_VIEW_MAX_LOOSE_OBJECTS,
        max_bytes=GIT_VIEW_MAX_LOOSE_BYTES,
    )
    loose_suffix_length = 62 if object_format == "sha256" else 38
    loose_suffix_re = re.compile(rf"[0-9a-f]{{{loose_suffix_length}}}")
    loose_snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = []
    initial_loose_objects: set[str] = set()
    cache_root = _git_loose_object_cache_root()
    # One stat per view, not per object: the object store's own inode identifies the
    # repository, and every cache entry this view publishes is keyed under it.
    objects_identity = os.fstat(objects_handle.descriptor)
    repo_key = f"{objects_identity.st_dev}:{objects_identity.st_ino}"
    with contextlib.ExitStack() as cache_stack:
        cache_lease = GitLooseObjectCacheLease(cache_stack)
        workers = _LazyLooseObjectWorkers(cache_stack)
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
                    object_names: list[str] = []
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
                            object_names.append(entry.name)

                    def materialize(object_name: str, cached_only: bool, _child_handle=child_handle, _child_path=child_path, _destination=destination_directory):
                        return _materialize_pinned_loose_object(
                            cache_root,
                            _child_handle,
                            _child_path / object_name,
                            _destination / object_name,
                            repo_key=repo_key,
                            budget=loose_budget,
                            operation=operation,
                            cache_lease=cache_lease,
                            cached_only=cached_only,
                        )

                    # Settle the already-cached objects inline. Publishing one is a local stat and
                    # symlink, so handing it to a worker costs more than doing it here, and a fully
                    # cached build should not start a worker at all.
                    settled: dict[str, tuple[Path, tuple[int, int, int, int, int]]] = {}
                    uncached: list[str] = []
                    for object_name in object_names:
                        snapshot = materialize(object_name, True)
                        if snapshot is None:
                            uncached.append(object_name)
                        else:
                            settled[object_name] = snapshot
                    # Whatever is left has to come off the repository, which is latency-bound, so
                    # overlap it. The listing above fixes this layer's membership, so these are
                    # independent; map preserves order and re-raises the first failure.
                    if uncached:
                        settled.update(zip(uncached, workers.map(lambda n: materialize(n, False), uncached)))
                    for object_name in object_names:
                        loose_snapshots.append(settled[object_name])
                        initial_loose_objects.add(f"{name}/{object_name}")
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
    return loose_snapshots, initial_loose_objects


@contextlib.contextmanager
def _pinned_git_object_store(
    git_dir_handle: paths.SafePathHandle,
    git_common_dir_handle: paths.SafePathHandle,
    *,
    deadline: float,
    operation: str,
    retirement: GitViewRetirementBudget,
    include_index: bool = False,
    writable: bool = False,
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
        # A process-pool worker can receive the fixture/daemon process-group SIGTERM while this
        # scope is active. Disable the weakref finalizer so interpreter shutdown cannot race the
        # still-unwinding task; normal scope exit remains strict through the ExitStack callback.
        temporary_view = tempfile.TemporaryDirectory(prefix="yolomux-git-view-", delete=False)
        stack.callback(temporary_view.cleanup)
        view_root = Path(temporary_view.name)
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

        loose_snapshots, initial_loose_objects = _materialize_pinned_loose_object_store(
            objects_handle,
            objects_path,
            object_directory,
            object_format=object_format,
            deadline=deadline,
            operation=operation,
        )
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
        index_snapshot = None
        if include_index:
            index_snapshot = _snapshot_git_index(
                git_dir_handle,
                control_directory / "index",
                operation=operation,
            )
            if writable and index_snapshot is not None:
                os.chmod(control_directory / "index", 0o600)
        _ensure_git_view_deadline(deadline)

        if not writable:
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
                index_snapshot=index_snapshot,
                initial_loose_objects=frozenset(initial_loose_objects),
            )
        finally:
            retirement.check()
            for loose_path, expected_identity in loose_snapshots:
                try:
                    current = os.stat(loose_path, follow_symlinks=True)
                except OSError as error:
                    raise _history_error(
                        "Git loose object changed during consumption",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                        diagnostic=error,
                    ) from error
                current_identity = (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                )
                if current_identity != expected_identity:
                    raise _history_error(
                        "Git loose object changed during consumption",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                    )
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
    include_index: bool = False,
    writable: bool = False,
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
            include_index=include_index,
            writable=writable,
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
def pinned_git_scope_from_handle(
    source_handle: paths.SafePathHandle | paths.SafeParentHandle,
    *,
    target_path: Path,
    operation: str,
    deadline: float,
    include_index: bool = False,
    index_mutation: bool = False,
):
    retirement = GitViewRetirementBudget()
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    scope_handle = source_handle
    _ensure_git_view_deadline(deadline)
    repo = _pinned_repo_root(scope_handle, deadline=deadline, operation=operation)
    _ensure_git_view_deadline(deadline)
    if repo is None:
        raise _history_error("path is not in a Git repository", key="fs.error.notGitRepo")
    _ensure_pinned_namespace_unchanged(scope_handle)
    try:
        relative_path = target_path.relative_to(repo).as_posix()
    except ValueError as error:
        raise paths.FilesystemError.outside_repo(target_path) from error
    _validate_git_path_text(repo, target_path, relative_path)
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
            include_index=include_index,
            writable=index_mutation,
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
            index_lock_descriptor = None
            if index_mutation:
                try:
                    index_lock_descriptor = os.open(
                        "index.lock",
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | paths.nofollow_flag()
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=git_dir_handle.descriptor,
                    )
                except FileExistsError as error:
                    raise _history_error(
                        "Git index is locked by another operation",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                        diagnostic=error,
                    ) from error
                except OSError as error:
                    raise _history_error(
                        "Git index lock is unavailable",
                        key="fs.error.gitRepositoryChanged",
                        status=409,
                        diagnostic=error,
                    ) from error
            try:
                yield PinnedGitHistoryScope(
                    path=target_path,
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
                    index_snapshot=object_store.index_snapshot,
                    initial_loose_objects=object_store.initial_loose_objects,
                    index_lock_descriptor=index_lock_descriptor,
                )
            finally:
                if index_lock_descriptor is not None:
                    try:
                        expected = os.fstat(index_lock_descriptor)
                        current = os.stat(
                            "index.lock",
                            dir_fd=git_dir_handle.descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
                            os.unlink("index.lock", dir_fd=git_dir_handle.descriptor)
                    finally:
                        os.close(index_lock_descriptor)
                retirement_deadline = retirement.begin()
                if not index_mutation:
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


@contextlib.contextmanager
def pinned_git_scope(
    raw_path: str | Path,
    *,
    operation: str,
    include_index: bool = True,
):
    """Keep one policy-authorized path, repository, control tree, object store, and index view."""

    deadline = time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS
    with paths.safe_path(str(raw_path), operation=operation) as source_handle:
        with pinned_git_scope_from_handle(
            source_handle,
            target_path=source_handle.resolved,
            operation=operation,
            deadline=deadline,
            include_index=include_index,
        ) as scope:
            yield scope


@contextlib.contextmanager
def _pinned_git_history_scope(raw_path: str, *, operation: str):
    deadline = time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        with paths.safe_path(raw_path, flags=directory_flags, operation=operation) as scope_handle:
            with pinned_git_scope_from_handle(
                scope_handle,
                target_path=scope_handle.resolved,
                operation=operation,
                deadline=deadline,
            ) as scope:
                yield scope
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


def _run_pinned_git(
    scope: PinnedGitHistoryScope,
    args: list[str],
    *,
    operation: str,
    timeout: float,
    max_output_bytes: int,
    binary: bool = False,
    allow_failure: bool = False,
    pass_fds: tuple[int, ...] = (),
    stdin_data: bytes | None = None,
) -> PinnedGitResult:
    _ensure_pinned_child_unchanged(
        scope.git_common_dir_handle,
        "objects",
        scope.git_objects_handle,
    )
    _ensure_no_git_alternate_objects(scope.git_objects_handle)
    try:
        with contextlib.ExitStack() as stack:
            stdin_descriptor = None
            if stdin_data is not None:
                stdin_file = stack.enter_context(tempfile.TemporaryFile(prefix="yolomux-git-input-"))
                stdin_file.write(stdin_data)
                stdin_file.flush()
                stdin_file.seek(0)
                stdin_descriptor = stdin_file.fileno()
            result = _git_with_pinned_repo(
                scope.repo_handle,
                args,
                timeout=timeout,
                binary=binary,
                pass_fds=pass_fds,
                max_output_bytes=max_output_bytes,
                git_dir_handle=scope.git_dir_handle,
                git_common_dir_handle=scope.git_common_dir_handle,
                git_directory=scope.git_directory,
                git_common_directory=scope.git_common_directory,
                git_object_directory=scope.git_object_directory,
                git_object_descriptors=scope.git_object_descriptors,
                shallow_data=scope.shallow_data,
                stdin_descriptor=stdin_descriptor,
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


def pinned_git_runner(
    scope: PinnedGitHistoryScope,
    *,
    operation: str,
    max_output_bytes: int = 32 * 1024 * 1024,
) -> Callable[[list[str], float], PinnedGitResult]:
    """Return the one bounded command adapter for an already-pinned repository view."""

    def run(args: list[str], timeout: float = 3.0) -> PinnedGitResult:
        return _run_pinned_git(
            scope,
            args,
            operation=operation,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            allow_failure=True,
        )

    return run


def pinned_repository_generation(raw_root: str) -> int:
    """Measure watchd's repository generation from one authorized private Git view."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    root_text = str(paths.parsed_request_path(raw_root))
    signature = REPOSITORY_SIGNATURE_UNKNOWN
    try:
        with paths.safe_path(raw_root, flags=directory_flags, operation="repository_generation") as root_handle:
            root_text = str(root_handle.resolved)
            scope_context = pinned_git_scope_from_handle(
                root_handle,
                target_path=root_handle.resolved,
                operation="repository_generation",
                deadline=time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS,
            )
            with scope_context as scope:
                head = _run_pinned_git(
                    scope,
                    ["rev-parse", "HEAD"],
                    operation="gitGeneration",
                    timeout=1.0,
                    max_output_bytes=64 * 1024,
                    allow_failure=True,
                )
                oid = (head.stdout or "").strip() if head.returncode == 0 else ""
                if not oid:
                    signature = REPOSITORY_SIGNATURE_UNKNOWN
                else:
                    symbolic = _run_pinned_git(
                        scope,
                        ["symbolic-ref", "--quiet", "--short", "HEAD"],
                        operation="gitGeneration",
                        timeout=1.0,
                        max_output_bytes=64 * 1024,
                        allow_failure=True,
                    )
                    symbolic_head = (symbolic.stdout or "").strip() if symbolic.returncode == 0 else ""
                    signature = (symbolic_head, oid)
    except paths.FilesystemError:
        # Reconcile treats an unreadable or changing repo as inconclusive: keep the last generation
        # without exposing control metadata or destabilizing the watch daemon.
        pass
    return _advance_repository_generation(root_text, signature)


def _decode_pinned_index_path(raw_path: bytes) -> str:
    try:
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _history_error(
            "Git index path is not valid UTF-8",
            key="fs.error.gitPathEncoding",
            status=422,
        ) from error
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        raise _history_error(
            "Git index path is unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    return path


def _pinned_index_flag_paths(
    scope: PinnedGitHistoryScope,
    flag: str,
    old_relative: str,
) -> set[str]:
    result = _run_pinned_git(
        scope,
        ["ls-files", flag, "-z", "--", old_relative],
        operation="gitRename",
        timeout=3.0,
        max_output_bytes=GIT_INDEX_RENAME_MAX_INPUT_BYTES,
        binary=True,
        allow_failure=True,
    )
    if result.returncode != 0:
        return set()
    selected: set[str] = set()
    for row in (result.stdout or b"").split(b"\0"):
        if not row:
            continue
        tag, separator, raw_path = row.partition(b" ")
        if not separator or len(tag) != 1:
            raise _history_error(
                "Git index flags are malformed",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        path = _decode_pinned_index_path(raw_path)
        if flag == "-t" and tag == b"S":
            selected.add(path)
        elif flag in {"-v", "-f"} and tag.islower():
            selected.add(path)
    return selected


def prepare_pinned_index_rename(scope: PinnedGitHistoryScope, new_relative: str) -> bool:
    """Prepare a tracked rename in the private index without reopening worktree content."""

    if scope.index_lock_descriptor is None:
        raise _history_error(
            "Git index mutation was not authorized",
            key="fs.error.operationFailed",
            status=500,
        )
    old_relative = scope.relative_path
    _validate_git_path_text(old_relative, new_relative)
    new_candidate = PurePosixPath(new_relative)
    if not old_relative or not new_relative or new_candidate.is_absolute() or ".." in new_candidate.parts:
        raise _history_error(
            "Git rename path is unsafe",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    result = _run_pinned_git(
        scope,
        ["ls-files", "--stage", "-z", "--", old_relative],
        operation="gitRename",
        timeout=3.0,
        max_output_bytes=GIT_INDEX_RENAME_MAX_INPUT_BYTES,
        binary=True,
        allow_failure=True,
    )
    if result.returncode != 0:
        return False
    rows: list[tuple[bytes, str, str]] = []
    old_prefix = f"{old_relative}/"
    for raw_row in (result.stdout or b"").split(b"\0"):
        if not raw_row:
            continue
        metadata, separator, raw_path = raw_row.partition(b"\t")
        parts = metadata.split()
        if not separator or len(parts) != 3 or parts[2] != b"0":
            raise _history_error(
                "Git index contains an unsupported staged entry",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        old_path = _decode_pinned_index_path(raw_path)
        if old_path == old_relative:
            suffix = ""
        elif old_path.startswith(old_prefix):
            suffix = old_path[len(old_relative):]
        else:
            continue
        new_path = f"{new_relative}{suffix}"
        _decode_pinned_index_path(new_path.encode("utf-8"))
        rows.append((metadata, old_path, new_path))
        if len(rows) > GIT_INDEX_RENAME_MAX_ENTRIES:
            raise _history_error(
                "Git rename exceeds the index entry limit",
                key="fs.error.gitHistoryTooLarge",
                status=413,
            )
    if not rows:
        return False
    index_input = bytearray()
    for metadata, old_path, new_path in rows:
        object_id = metadata.split()[1]
        index_input.extend(b"0 " + (b"0" * len(object_id)) + b"\t" + old_path.encode("utf-8") + b"\0")
        index_input.extend(metadata + b"\t" + new_path.encode("utf-8") + b"\0")
    if len(index_input) > GIT_INDEX_RENAME_MAX_INPUT_BYTES:
        raise _history_error(
            "Git rename exceeds the index update limit",
            key="fs.error.gitHistoryTooLarge",
            status=413,
        )
    assume_unchanged = _pinned_index_flag_paths(scope, "-v", old_relative)
    skip_worktree = _pinned_index_flag_paths(scope, "-t", old_relative)
    fsmonitor_valid = _pinned_index_flag_paths(scope, "-f", old_relative)
    updated = _run_pinned_git(
        scope,
        ["update-index", "-z", "--index-info"],
        operation="gitRename",
        timeout=5.0,
        max_output_bytes=64 * 1024,
        binary=True,
        allow_failure=True,
        stdin_data=bytes(index_input),
    )
    if updated.returncode != 0:
        _raise_history_git_failure(updated, operation="gitRename")
    old_to_new = {old_path: new_path for _metadata, old_path, new_path in rows}
    for option, selected in (
        ("--assume-unchanged", assume_unchanged),
        ("--skip-worktree", skip_worktree),
        ("--fsmonitor-valid", fsmonitor_valid),
    ):
        paths_to_mark = [old_to_new[path] for path in selected if path in old_to_new]
        if not paths_to_mark:
            continue
        flag_input = b"\0".join(path.encode("utf-8") for path in paths_to_mark) + b"\0"
        flagged = _run_pinned_git(
            scope,
            ["update-index", option, "-z", "--stdin"],
            operation="gitRename",
            timeout=3.0,
            max_output_bytes=64 * 1024,
            binary=True,
            allow_failure=True,
            stdin_data=flag_input,
        )
        if flagged.returncode != 0:
            _raise_history_git_failure(flagged, operation="gitRename")
    return True


def _git_index_matches_snapshot(scope: PinnedGitHistoryScope, name: str = "index") -> bool:
    index_path = scope.git_dir_handle.resolved / name
    try:
        with paths.safe_child(
            scope.git_dir_handle.descriptor,
            index_path,
            index_path,
            operation="rename_path",
            observe_name=False,
        ) as index_handle:
            if scope.index_snapshot is None or not stat.S_ISREG(index_handle.stat_result.st_mode):
                return False
            metadata = index_handle.stat_result
            identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            expected_identity = scope.index_snapshot.stat_identity
            identity_matches = identity == expected_identity if name == "index" else identity[:3] == expected_identity[:3]
            if not identity_matches or metadata.st_size > GIT_INDEX_MAX_BYTES:
                return False
            digest = hashlib.sha256()
            offset = 0
            while offset < metadata.st_size:
                chunk = os.pread(index_handle.descriptor, min(64 * 1024, metadata.st_size - offset), offset)
                if not chunk:
                    return False
                digest.update(chunk)
                offset += len(chunk)
            return digest.hexdigest() == scope.index_snapshot.digest
    except paths.FilesystemError as error:
        return scope.index_snapshot is None and error.status == 404
    except OSError:
        return False


def publish_pinned_index_rename(scope: PinnedGitHistoryScope) -> None:
    """Install the prepared private index through the pinned Git-directory descriptor."""

    lock_descriptor = scope.index_lock_descriptor
    if lock_descriptor is None or not _git_index_matches_snapshot(scope):
        raise _history_error(
            "Git index changed during rename",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    private_index = Path(scope.git_directory) / "index"
    try:
        source_descriptor = os.open(
            private_index,
            os.O_RDONLY | paths.nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise _history_error(
            "Prepared Git index is unavailable",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    try:
        metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > GIT_INDEX_MAX_BYTES:
            raise _history_error(
                "Prepared Git index is invalid",
                key="fs.error.gitRepositoryChanged",
                status=409,
            )
        os.ftruncate(lock_descriptor, 0)
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(source_descriptor, min(64 * 1024, metadata.st_size - offset), offset)
            if not chunk:
                raise _history_error(
                    "Prepared Git index changed during publication",
                    key="fs.error.gitRepositoryChanged",
                    status=409,
                )
            written = 0
            while written < len(chunk):
                written += os.pwrite(lock_descriptor, chunk[written:], offset + written)
            offset += len(chunk)
        os.fsync(lock_descriptor)
    finally:
        os.close(source_descriptor)
    if not _git_index_matches_snapshot(scope):
        raise _history_error(
            "Git index changed during rename",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    expected_lock = os.fstat(lock_descriptor)
    try:
        current_lock = os.stat(
            "index.lock",
            dir_fd=scope.git_dir_handle.descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise _history_error(
            "Git index lock changed during rename",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if (current_lock.st_dev, current_lock.st_ino) != (expected_lock.st_dev, expected_lock.st_ino):
        raise _history_error(
            "Git index lock changed during rename",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    try:
        paths.rename_exchange(scope.git_dir_handle.descriptor, "index.lock", "index")
    except OSError as error:
        raise _history_error(
            "Git index publication is unavailable",
            key="fs.error.gitRepositoryChanged",
            status=409,
            diagnostic=error,
        ) from error
    if not _git_index_matches_snapshot(scope, "index.lock"):
        try:
            paths.rename_exchange(scope.git_dir_handle.descriptor, "index.lock", "index")
        except OSError as rollback_error:
            raise _history_error(
                "Git index changed during rename and rollback failed",
                key="fs.error.gitRepositoryChanged",
                status=409,
                diagnostic=rollback_error,
            ) from rollback_error
        raise _history_error(
            "Git index changed during rename",
            key="fs.error.gitRepositoryChanged",
            status=409,
        )
    os.unlink("index.lock", dir_fd=scope.git_dir_handle.descriptor)
    with contextlib.suppress(OSError):
        os.fsync(scope.git_dir_handle.descriptor)


def _run_bounded_history_git(
    scope: PinnedGitHistoryScope,
    args: list[str],
    *,
    operation: str,
    timeout: float,
    max_output_bytes: int,
    allow_failure: bool = False,
) -> PinnedGitResult:
    return _run_pinned_git(
        scope,
        args,
        operation=operation,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        binary=True,
        allow_failure=allow_failure,
    )


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


def _pinned_blob_text(
    scope: PinnedGitHistoryScope,
    ref: str,
    rel_path: str,
    label: str,
) -> tuple[str, str]:
    result = _run_pinned_git(
        scope,
        ["show", f"{ref}:{rel_path}"],
        operation="gitBlob",
        timeout=5.0,
        max_output_bytes=paths.MAX_READ_BYTES + 1,
        binary=True,
        allow_failure=True,
    )
    if result.returncode != 0:
        return "", ""
    raw = result.stdout or b""
    if result.stdout_truncated or len(raw) > paths.MAX_READ_BYTES:
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


def _pinned_ref_exists(scope: PinnedGitHistoryScope, ref: str) -> bool:
    return _run_pinned_git(
        scope,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        operation="gitRef",
        timeout=3.0,
        max_output_bytes=64 * 1024,
        allow_failure=True,
    ).returncode == 0


def _ensure_pinned_ref_order(scope: PinnedGitHistoryScope, from_ref: str, to_ref: str) -> None:
    if to_ref == "current":
        if from_ref == "current" or not _pinned_ref_exists(scope, from_ref):
            key = "fs.error.refOrderCurrent" if from_ref == "current" else "common.unknownFromRef"
            raise paths.FilesystemError("invalid FROM ref", message_key=key, message_params={"ref": from_ref})
        return
    if from_ref == "current":
        raise paths.FilesystemError("current cannot precede a commit", message_key="fs.error.refOrderCurrent")
    for ref, key in ((from_ref, "common.unknownFromRef"), (to_ref, "common.unknownToRef")):
        if not _pinned_ref_exists(scope, ref):
            raise paths.FilesystemError(f"unknown ref: {ref}", message_key=key, message_params={"ref": ref})
    order = _run_pinned_git(
        scope,
        ["merge-base", "--is-ancestor", from_ref, to_ref],
        operation="gitRef",
        timeout=5.0,
        max_output_bytes=64 * 1024,
        allow_failure=True,
    )
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
    if repo_source is None:
        raise paths.FilesystemError(
            f"not in a git repo: {path}",
            message_key="fs.error.notGitRepo",
            message_params={"path": str(path)},
        )
    with pinned_git_scope_from_handle(
        repo_source,
        target_path=path,
        operation=operation,
        deadline=time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS,
        include_index=True,
    ) as scope:
        repo = scope.repo
        rel_path = scope.relative_path
        tracked = _run_pinned_git(
            scope,
            ["ls-files", "--error-unmatch", "--", rel_path],
            operation="gitDiff",
            timeout=3.0,
            max_output_bytes=64 * 1024,
            allow_failure=True,
        )
        diff_from, diff_to = diff_refs(from_ref, to_ref)
        if not (diff_to == "current" and tracked.returncode != 0):
            try:
                _ensure_pinned_ref_order(scope, diff_from, diff_to)
            except paths.FilesystemError as error:
                if not (refs_requested(from_ref, to_ref) and _diff_ref_resolution_error(error)):
                    raise
                diff_from, diff_to = diff_refs(None, None)
                _ensure_pinned_ref_order(scope, diff_from, diff_to)
        original = ""
        original_error = ""
        working = ""
        working_error = ""
        if diff_to == "current":
            untracked = tracked.returncode != 0
            if not untracked:
                original, original_error = _pinned_blob_text(scope, diff_from, rel_path, "original")
            working, working_error = _pinned_working_text(working_handle)
            if original_error or working_error:
                diff = f"Binary files a/{rel_path} and b/{rel_path} differ\n" if original != working else ""
            else:
                diff = _unified_file_diff(original, working, rel_path)
        else:
            untracked = False
            result = _run_pinned_git(
                scope,
                ["diff", diff_from, diff_to, "--", rel_path],
                operation="gitDiff",
                timeout=5.0,
                max_output_bytes=paths.MAX_READ_BYTES + 1,
                allow_failure=True,
            )
            if result.returncode not in {0, 1}:
                raise paths.FilesystemError(
                    "git diff failed",
                    status=500,
                    message_key="fs.error.gitDiffFailed",
                    diagnostic=cmd_error(result, "git diff failed"),
                )
            diff = result.stdout or ""
            original, original_error = _pinned_blob_text(scope, diff_from, rel_path, "original")
            working, working_error = _pinned_blob_text(scope, diff_to, rel_path, "working")
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
    if repo_source is None:
        return {"path": str(path), "repo": "", "relative_path": "", "in_repo": False, "lines": {}}
    try:
        scope_context = pinned_git_scope_from_handle(
            repo_source,
            target_path=path,
            operation=operation,
            deadline=time.monotonic() + GIT_VIEW_BUILD_TIMEOUT_SECONDS,
            include_index=True,
        )
        with scope_context as scope:
            repo = scope.repo
            rel_path = scope.relative_path
            if file_handle is None:
                return {"path": str(path), "repo": str(repo), "relative_path": rel_path, "in_repo": True, "lines": {}}
            head = _run_pinned_git(
                scope,
                ["rev-parse", "HEAD"],
                operation="gitBlame",
                timeout=1.0,
                max_output_bytes=64 * 1024,
                allow_failure=True,
            )
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
            result = _run_pinned_git(
                scope,
                args,
                operation="gitBlame",
                timeout=3.0,
                max_output_bytes=paths.MAX_READ_BYTES,
                allow_failure=True,
                pass_fds=(file_handle.descriptor,),
            )
    except paths.FilesystemError as error:
        if error.message_key == "fs.error.notGitRepo":
            return {"path": str(path), "repo": "", "relative_path": "", "in_repo": False, "lines": {}}
        raise
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

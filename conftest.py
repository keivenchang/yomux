# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Route pytest into Docker, retaining Phase 1's isolated host fallback."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

import pytest


REPO_ROOT = Path(__file__).resolve().parent

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from yolomux_lib.infra import worktree_writer

_HOST_ARTIFACTS = worktree_writer.configure_host_local_artifacts(REPO_ROOT)
sys.dont_write_bytecode = _previous_dont_write_bytecode
del _previous_dont_write_bytecode

# Resolve the cross-run host lock before fallback environment variables replace HOME and
# YOLOMUX_TOOL_LOCK_PATH. The lock must serialize container launches on the host; a path
# created inside a container or fixture namespace has no other run to contend with.
HOST_EXPENSIVE_TOOL_LOCK = Path.home() / ".cache" / "yolomux" / "expensive-tools.lock"

# Import only the stdlib-only image helper here. tools.check imports product configuration
# and would bind it from the host environment before isolation is installed.
from tools import docker_image
from tools.tool_guard import hold_host_tool_flock
from tools.tool_guard import parent_owns_tool_lock
from tools.tool_guard import run_reaped_container_command


def host_local_cache_dir() -> Path:
    """Return this pytest controller's host-local cache directory."""

    return _HOST_ARTIFACTS.pytest_cache / f"p{os.getpid()}"


def _install_fallback_environment() -> None:
    """Install Phase 1's short, per-process writable namespace."""

    test_root_env = "YOLOMUX_TEST_ROOT"
    inherited_test_root = os.environ.get(test_root_env, "").strip()
    if inherited_test_root:
        test_root = Path(inherited_test_root)
        test_root.mkdir(parents=True, exist_ok=True)
    else:
        test_root = Path(tempfile.mkdtemp(prefix=f"yolomux-test-{os.getpid()}-{os.getuid()}-", dir="/tmp"))
        test_root.chmod(0o700)
        os.environ[test_root_env] = str(test_root)

    process_root = test_root / f"p{os.getpid()}"
    process_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(process_root)
    tempfile.tempdir = str(process_root)

    writable_env_paths = {
        "XDG_CONFIG_HOME": process_root / "xdg-config",
        "XDG_STATE_HOME": process_root / "xdg-state",
        "XDG_CACHE_HOME": process_root / "xdg-cache",
        "YOLOMUX_CONFIG_DIR": process_root / "config",
        "YOLOMUX_STATE_DIR": process_root / "state",
        "YOLOMUX_CACHE_DIR": process_root / "cache",
        "YOLOMUX_RUNTIME_DIR": process_root / "runtime",
        "YOLOMUX_CODEX_HOME": process_root / "codex-home",
        "CODEX_HOME": process_root / "codex-home",
        "YOLOMUX_START_LOCK_DIR": process_root / "locks" / "start.lock",
        "YOLOMUX_TOOL_LOCK_PATH": process_root / "locks" / "expensive-tools.lock",
        "YOLOMUX_CA_DIR": process_root / "ca",
        "YOLOMUX_LOG_DIR": process_root / "logs",
        "YOLOMUX_WORKSPACE_BASE": process_root / "workspaces",
        "YOLOMUX_TMUX_SOCKET": process_root / "tmux" / "socket",
    }
    for name, path in writable_env_paths.items():
        os.environ[name] = str(path)


# On a Docker-capable host, leave host test state untouched because the hook delegates before
# collection. Inside the container, or when Docker is deliberately/unavoidably unavailable,
# retain the Phase 1 protection and its short Unix-socket paths.
_CONTAINER_AVAILABLE, _CONTAINER_REASON = docker_image.container_available(REPO_ROOT)
if not _CONTAINER_AVAILABLE:
    _install_fallback_environment()


def _translate_argument(argument: str, repo_root: Path) -> str:
    """Map an absolute host node id under the worktree to its /w path."""

    path_part, separator, node_part = argument.partition("::")
    if not path_part.startswith("/"):
        return argument
    try:
        relative = Path(path_part).resolve().relative_to(repo_root)
    except ValueError:
        return argument
    return f"/w/{relative.as_posix()}{separator}{node_part}"


def _container_working_directory(repo_root: Path) -> str:
    """Map the caller's current worktree directory to the /w mount."""

    try:
        relative = Path.cwd().resolve().relative_to(repo_root)
    except ValueError:
        return "/w"
    return "/w" if str(relative) == "." else f"/w/{relative.as_posix()}"


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> int | None:
    """Re-run this pytest invocation inside Docker before host collection."""

    available, reason = docker_image.container_available(REPO_ROOT)
    if not available:
        if not docker_image.running_inside_container():
            print(f"pytest running on the host: {reason}", file=sys.stderr, flush=True)
        return None

    arguments = [_translate_argument(argument, REPO_ROOT) for argument in config.invocation_params.args]
    arguments.extend(["-o", f"cache_dir={host_local_cache_dir()}"])
    print(
        "pytest running in the isolated test container "
        "(YOLOMUX_CHECK_CONTAINER=0 to opt out)",
        file=sys.stderr,
        flush=True,
    )
    command = [
        str(REPO_ROOT / "docker" / "run-tests.sh"),
        "--workdir",
        _container_working_directory(REPO_ROOT),
        "--",
        "python3",
        "-B",
        "-m",
        "pytest",
        *arguments,
    ]
    child_env = dict(os.environ)
    # A collect-only run does no expensive work, and a launching check that already owns the flock
    # must not be serialized behind itself; both run directly. Every other direct run must serialize
    # on the tool flock BEFORE it takes the worktree writer lease, so a run queued behind another
    # agent's docker launch waits holding no lease at all (F8). The container is launched in its own
    # session so an interrupt reaps the docker wrapper and its container instead of leaving them
    # detached and holding the lock.
    #
    # The writer lease MUST write its token into the exact env forwarded to the container:
    # acquire_worktree_writer(environ=child_env) sets YOLOMUX_WORKTREE_WRITER_TOKEN there, and
    # run-tests.sh forwards that name so the in-container pytest borrows this lease instead of
    # declaring a second, conflicting writer and refusing itself.
    serialize = not (
        config.option.collectonly
        or parent_owns_tool_lock(HOST_EXPENSIVE_TOOL_LOCK, environ=child_env, parent_pid=os.getppid())
    )
    try:
        if serialize:
            with hold_host_tool_flock(HOST_EXPENSIVE_TOOL_LOCK, environ=child_env):
                with worktree_writer.acquire_worktree_writer(REPO_ROOT, purpose="pytest", environ=child_env):
                    return run_reaped_container_command(command, cwd=REPO_ROOT, env=child_env)
        with worktree_writer.acquire_worktree_writer(REPO_ROOT, purpose="pytest", environ=child_env):
            return run_reaped_container_command(command, cwd=REPO_ROOT, env=child_env)
    except worktree_writer.WorktreeWriterBusy as error:
        print(f"PYTEST REFUSED: {error}", file=sys.stderr, flush=True)
        return 3


_SESSION_WRITER_LEASE: worktree_writer.WorktreeWriterLease | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest's own cache out of the mounted/shared source tree."""

    config.inicfg["cache_dir"] = str(host_local_cache_dir())


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Declare direct host/in-container pytest runs as worktree writers."""

    global _SESSION_WRITER_LEASE
    try:
        _SESSION_WRITER_LEASE = worktree_writer.acquire_worktree_writer(REPO_ROOT, purpose="pytest")
    except (worktree_writer.WorktreeWriterBusy, worktree_writer.WorktreeWriterContainerRefusal) as error:
        pytest.exit(f"PYTEST REFUSED: {error}", returncode=3)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Release only the declaration token acquired by this pytest process."""

    del session, exitstatus
    global _SESSION_WRITER_LEASE
    if _SESSION_WRITER_LEASE is not None:
        _SESSION_WRITER_LEASE.release()
        _SESSION_WRITER_LEASE = None

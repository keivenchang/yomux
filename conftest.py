# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Route pytest into Docker, retaining Phase 1's isolated host fallback."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parent

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from yomux_lib.infra import worktree_writer
from tools.product_environment import apply_product_environment_assignments
from tools.product_environment import clear_product_environment_assignments
from tools.product_environment import product_env
from tools.product_environment import product_env_name
from tools.tool_guard import host_expensive_tool_lock_path

_HOST_ARTIFACTS = worktree_writer.configure_host_local_artifacts(REPO_ROOT)
sys.dont_write_bytecode = _previous_dont_write_bytecode
del _previous_dont_write_bytecode

# Resolve the cross-run host lock before fallback environment variables replace HOME and
# YOMUX_TOOL_LOCK_PATH. The lock must serialize container launches on the host; a path
# created inside a container or fixture namespace has no other run to contend with.
HOST_EXPENSIVE_TOOL_LOCK = host_expensive_tool_lock_path(os.environ)

# Import only the stdlib-only image helper here. tools.check imports product configuration
# and would bind it from the host environment before isolation is installed.
from tools import docker_image
from tools.tool_guard import hold_host_tool_flock
from tools.tool_guard import parent_owns_tool_lock
from tools.tool_guard import run_reaped_container_command


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip document coordination directories before pytest stats their protected paths."""
    if ".agent-edit.lock" in collection_path.name:
        return True
    return None


def host_local_cache_dir() -> Path:
    """Return this pytest controller's host-local cache directory."""

    return _HOST_ARTIFACTS.pytest_cache / f"p{os.getpid()}"


def _install_fallback_environment() -> None:
    """Install Phase 1's short, per-process writable namespace."""

    # The container runner may provide canonical YOMUX_* roots. Unit fixtures
    # intentionally exercise legacy YOLOMUX_* overrides, so clear both halves
    # of each ambient root pair before installing this process's isolated paths.
    clear_product_environment_assignments(os.environ,
        "ROOT",
        "CONFIG_DIR",
        "STATE_DIR",
        "CACHE_DIR",
        "RUNTIME_DIR",
        "CODEX_HOME",
        "HOST_ARTIFACT_DIR",
        "START_LOCK_DIR",
        "TOOL_LOCK_PATH",
        "CA_DIR",
        "LOG_DIR",
    )

    test_root_env = product_env_name("TEST_ROOT")
    inherited_test_root = (product_env("TEST_ROOT", "") or "").strip()
    if inherited_test_root:
        test_root = Path(inherited_test_root)
        test_root.mkdir(parents=True, exist_ok=True)
    else:
        test_root = Path(tempfile.mkdtemp(prefix=f"yomux-test-{os.getpid()}-{os.getuid()}-", dir="/tmp"))
        test_root.chmod(0o700)
        apply_product_environment_assignments(os.environ, {"TEST_ROOT": str(test_root)})

    process_root = test_root / f"p{os.getpid()}"
    process_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(process_root)
    tempfile.tempdir = str(process_root)

    writable_env_paths = {
        "XDG_CONFIG_HOME": process_root / "xdg-config",
        "XDG_STATE_HOME": process_root / "xdg-state",
        "XDG_CACHE_HOME": process_root / "xdg-cache",
        "CODEX_HOME": process_root / "codex-home",
    }
    for name, path in writable_env_paths.items():
        os.environ[name] = str(path)
    apply_product_environment_assignments(os.environ, {
        "CONFIG_DIR": str(process_root / "config"),
        "STATE_DIR": str(process_root / "state"),
        "CACHE_DIR": str(process_root / "cache"),
        "RUNTIME_DIR": str(process_root / "runtime"),
        "CODEX_HOME": str(process_root / "codex-home"),
        "START_LOCK_DIR": str(process_root / "locks" / "start.lock"),
        "TOOL_LOCK_PATH": str(process_root / "locks" / "expensive-tools.lock"),
        "CA_DIR": str(process_root / "ca"),
        "LOG_DIR": str(process_root / "logs"),
        "WORKSPACE_BASE": str(process_root / "workspaces"),
        "TMUX_SOCKET": str(process_root / "tmux" / "socket"),
    })


# On a Docker-capable host, leave host test state untouched because the hook delegates before
# collection. Inside the container, retain the Phase 1 protection and its short Unix-socket paths.
# A host invocation without Docker is a refusal by default: a green host fallback is not evidence
# for a Dockerized gate. The explicit YOMUX_CHECK_CONTAINER=0 escape hatch remains for small
# unit-test subprocesses that intentionally exercise the host-only fallback.
_CONTAINER_AVAILABLE, _CONTAINER_REASON = docker_image.container_available(REPO_ROOT)
if docker_image.running_inside_container() or (not _CONTAINER_AVAILABLE and product_env("CHECK_CONTAINER") == "0"):
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


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Keep cross-agent document lock directories out of xdist collection."""

    if any(part.endswith(".agent-edit.lock") for part in collection_path.parts):
        return True
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> int | None:
    """Re-run this pytest invocation inside Docker before host collection."""

    available, reason = docker_image.container_available(REPO_ROOT)
    if not available:
        if docker_image.running_inside_container():
            return None
        if product_env("CHECK_CONTAINER") == "0":
            return None
        print(f"PYTEST REFUSED: Docker is required for this test invocation: {reason}", file=sys.stderr, flush=True)
        return 3

    arguments = [_translate_argument(argument, REPO_ROOT) for argument in config.invocation_params.args]
    arguments.extend(["-o", f"cache_dir={host_local_cache_dir()}"])
    print(
        "pytest running in the isolated test container "
        f"({product_env_name('CHECK_CONTAINER')}=0 to opt out)",
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
    # acquire_worktree_writer(environ=child_env) sets YOMUX_WORKTREE_WRITER_TOKEN there, and
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


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    """Give xdist workers the controller's exact shared writer lease."""

    lease = _SESSION_WRITER_LEASE
    if lease is None:
        return
    node.workerinput["yomux_worktree_writer_token"] = lease.token
    node.workerinput["yomux_worktree_writer_slot"] = str(lease.slot_dir)


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Adopt the controller lease in workers before they run tests or subprocess checks."""

    global _SESSION_WRITER_LEASE
    config = session.config
    if hasattr(config, "workerinput"):
        workerinput = config.workerinput
        token = str(workerinput.get("yomux_worktree_writer_token") or "")
        slot = str(workerinput.get("yomux_worktree_writer_slot") or "")
        if token and slot:
            apply_product_environment_assignments(os.environ, {worktree_writer.WRITER_TOKEN_ENV: token, "WORKTREE_WRITER_SLOT": slot})
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

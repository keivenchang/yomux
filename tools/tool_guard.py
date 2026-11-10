# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared parent-lock identity for the check runner and pytest controller."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import contextlib
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time

from tools.product_environment import product_env, product_env_name
from tools.instance_isolation import normalize_yomux_environment
from tools.instance_isolation import apply_yomux_environment_assignments
from tools.instance_isolation import clear_yomux_environment_assignments
from tools.instance_isolation import resolved_home_path
from tools.instance_isolation import resolved_product_path


TOOL_LOCK_OWNER_ENV = product_env_name('CHECK_TOOL_LOCK_OWNER')


def host_expensive_tool_lock_path(environ: Mapping[str, str]) -> Path:
    values = normalize_yomux_environment(environ)
    home = resolved_home_path(values)
    # Existing checkouts must contend on this same inode during the rename transition.
    return resolved_product_path(values, product_env_name('TOOL_LOCK_PATH'), home / ".cache" / "yolomux" / "expensive-tools.lock")


def tool_lock_owner_marker(lock_path: Path, *, pid: int | None = None) -> str:
    """Identify the process and exact lock inherited by its child commands."""

    owner_pid = os.getpid() if pid is None else int(pid)
    return f"{owner_pid}:{lock_path}"


def parent_owns_tool_lock(
    lock_path: Path,
    *,
    environ: Mapping[str, str],
    parent_pid: int,
) -> bool:
    """Return whether the direct parent declares ownership of this exact lock."""

    return product_env('CHECK_TOOL_LOCK_OWNER', environ=environ) == tool_lock_owner_marker(lock_path, pid=parent_pid)


def container_command_with_host_tool_guard(
    command: list[str],
    *,
    lock_path: Path,
    collect_only: bool,
    environ: Mapping[str, str],
    parent_pid: int,
) -> list[str]:
    """Serialize direct pytest, without reacquiring a lock held by its parent check."""

    if collect_only or parent_owns_tool_lock(lock_path, environ=environ, parent_pid=parent_pid):
        return command
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return ["flock", str(lock_path), *command]


@contextlib.contextmanager
def hold_host_tool_flock(
    lock_path: Path,
    *,
    environ: MutableMapping[str, str],
    blocking: bool = True,
):
    """Hold the expensive-tool flock in THIS process before running any child command.

    A queued run must block on the flock while holding no other resource. The prior design wrapped
    the child as ``["flock", lock_path, *command]``, so a run queued behind another agent's docker
    launch waited inside the child while its caller kept the worktree writer lease for the whole
    queue wait. Acquiring the flock here, before the lease, means a queued run holds nothing until it
    can actually start work. The flock lives on this process's file descriptor, so an interrupt that
    kills this process releases it automatically instead of leaving it held by a detached wrapper.
    """

    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous_owner = product_env('CHECK_TOOL_LOCK_OWNER', environ=environ)
        apply_yomux_environment_assignments(environ, {TOOL_LOCK_OWNER_ENV: tool_lock_owner_marker(lock_path)})
        try:
            yield handle.fileno()
        finally:
            if previous_owner is None:
                clear_yomux_environment_assignments(environ, TOOL_LOCK_OWNER_ENV)
            else:
                apply_yomux_environment_assignments(environ, {TOOL_LOCK_OWNER_ENV: previous_owner})
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def terminate_process_group(process: subprocess.Popen, grace_seconds: float = 10.0) -> None:
    """Stop and reap one lane step's private session without waiting indefinitely."""

    # start_new_session=True makes the leader PID the process-group ID. Keep using that captured
    # identity even when the leader has already exited but a descendant still owns the pipe.
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        # The process is ours, so still stop the leader if a platform refuses group signalling.
        if process.poll() is None:
            process.kill()
    # The leader can exit while a descendant still owns stdout/stderr. Waiting only on the leader
    # would return here and skip the forced group kill, leaving that descendant behind. Observe the
    # private group itself for the grace window instead.
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            break
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        # SIGKILL cannot be ignored; this fallback keeps a broken platform implementation from
        # turning the gate's timeout path back into an unbounded wait.
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass


def run_reaped_container_command(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    terminate_grace_seconds: float = 10.0,
) -> int:
    """Run the container command in its own session and reap the whole tree on interrupt.

    A name-pattern kill of the inner python left the ``docker run --rm`` wrapper alive holding the
    lock, invisible to that kill. Launching the wrapper in a new session lets an interrupt signal the
    whole session: ``docker run`` receives SIGTERM, forwards a stop to its ``--rm`` container, and the
    container is removed rather than surviving detached. Any raised interrupt still propagates after
    the tree is reaped so the caller releases its flock and writer lease.
    """

    process = subprocess.Popen(command, cwd=str(cwd), env=dict(env), start_new_session=True)
    try:
        return process.wait()
    except BaseException:
        terminate_process_group(process, terminate_grace_seconds)
        raise

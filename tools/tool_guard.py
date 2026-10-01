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


TOOL_LOCK_OWNER_ENV = "YOLOMUX_CHECK_TOOL_LOCK_OWNER"


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

    return environ.get(TOOL_LOCK_OWNER_ENV) == tool_lock_owner_marker(lock_path, pid=parent_pid)


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
        previous_owner = environ.get(TOOL_LOCK_OWNER_ENV)
        environ[TOOL_LOCK_OWNER_ENV] = tool_lock_owner_marker(lock_path)
        try:
            yield handle.fileno()
        finally:
            if previous_owner is None:
                environ.pop(TOOL_LOCK_OWNER_ENV, None)
            else:
                environ[TOOL_LOCK_OWNER_ENV] = previous_owner
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _terminate_process_session(process: subprocess.Popen, grace_seconds: float) -> None:
    """Signal the child's whole process session so the wrapper and its container are reaped."""

    if process.poll() is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    process.wait()


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
        _terminate_process_session(process, terminate_grace_seconds)
        raise

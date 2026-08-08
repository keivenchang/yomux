# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared parent-lock identity for the check runner and pytest controller."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


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

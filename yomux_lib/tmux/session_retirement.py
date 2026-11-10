# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exact process-birth ownership for tmux session retirement."""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import time

from ..host_identity import process_identity_snapshot
from ..host_identity import ProcessIdentitySnapshot
from ..host_identity import LocalProcessReason
from ..host_identity import current_host_identity
from ..host_identity import is_current_local_process
from ..local_services.registry import ProcessTableEntry
from ..local_services.registry import ProcessTableUnavailable
from ..local_services.registry import bounded_process_table
from ..local_services.registry import process_state
from .sessions import list_tmux_panes


SESSION_RETIREMENT_TIMEOUT_SECONDS = 3.0
SESSION_RETIREMENT_POLL_SECONDS = 0.01
_SESSION_RETIREMENT_POLL = threading.Event()


@dataclass(frozen=True)
class ProcessBirthIdentity:
    """One exact process instance captured before a destructive tmux action."""

    pid: int
    pgid: int
    start_identity: str
    command: str


@dataclass(frozen=True)
class SessionRetirementIdentity:
    """The complete process groups owned by one tmux session at capture time."""

    session: str
    members: tuple[ProcessBirthIdentity, ...]


class SessionRetirementError(RuntimeError):
    """A tmux session's exact retirement could not be established."""


def _session_birth_is_current(member: ProcessBirthIdentity, snapshot: ProcessIdentitySnapshot | None) -> bool:
    identity = current_host_identity()
    diagnostic = is_current_local_process(
        identity.process_record_fields(pid=member.pid, start_identity=member.start_identity),
        host_identity=identity,
        start_identity_reader=lambda _: snapshot.start_identity if snapshot else None,
        state_reader=lambda _: snapshot.state if snapshot else "",
    )
    if diagnostic.current:
        return True
    if diagnostic.reason in {LocalProcessReason.PROCESS_NOT_FOUND, LocalProcessReason.PROCESS_IDENTITY_REUSED}:
        return False
    raise SessionRetirementError(f"cannot establish tmux process retirement: pid={member.pid}, reason={diagnostic.reason.value}")


def capture_tmux_session_retirement(session: str) -> SessionRetirementIdentity:
    """Capture every live birth in every process group owned by ``session``."""

    panes, pane_error = list_tmux_panes()
    if pane_error:
        raise SessionRetirementError(f"tmux pane inventory failed: {pane_error}")
    pane_pids = tuple(pane.pid for pane in panes if pane.session == session)
    if not pane_pids:
        raise SessionRetirementError(f"tmux session has no pane process: {session}")
    try:
        table = bounded_process_table(require_complete=True)
    except ProcessTableUnavailable as error:
        raise SessionRetirementError("process table unavailable before tmux session kill") from error
    missing_panes = tuple(pid for pid in pane_pids if pid not in table)
    if missing_panes:
        raise SessionRetirementError(f"tmux pane births unavailable before kill: {missing_panes}")
    process_groups = {table[pid].pgid for pid in pane_pids}
    members = []
    for pid, entry in table.items():
        if entry.pgid not in process_groups:
            continue
        if not entry.start_identity:
            raise SessionRetirementError(f"process start identity unavailable before kill: pid={pid}")
        members.append(ProcessBirthIdentity(pid, entry.pgid, entry.start_identity, entry.command))
    if not members:
        raise SessionRetirementError(f"tmux session has no serving process births: {session}")
    return SessionRetirementIdentity(session=session, members=tuple(sorted(members, key=lambda item: item.pid)))


def retained_tmux_session_births(
    identity: SessionRetirementIdentity,
    *,
    table: dict[int, ProcessTableEntry] | None = None,
) -> tuple[dict[str, object], ...]:
    """Describe only captured births that remain live.

    Capture needs one complete process table to discover every member of each pane's process
    group. Joining does not: the captured PID and start identity are already the complete set of
    births to fence. Probe those exact births directly so request latency cannot grow with every
    unrelated process on the host.
    """

    retained = []
    for member in identity.members:
        if table is None:
            snapshot = process_identity_snapshot(member.pid)
            if not _session_birth_is_current(member, snapshot):
                continue
            try:
                pgid = os.getpgid(member.pid)
            except ProcessLookupError:
                continue
            except OSError as error:
                raise SessionRetirementError(f"process group unavailable during tmux retirement: pid={member.pid}") from error
            # Fence the getpgid read against exit and PID reuse before publishing a survivor.
            current_snapshot = process_identity_snapshot(member.pid)
            if not _session_birth_is_current(member, current_snapshot):
                continue
            state = current_snapshot.state
            command = member.command
        else:
            current = table.get(member.pid)
            if current is None or current.start_identity != member.start_identity:
                continue
            pgid = current.pgid
            state = process_state(member.pid)
            if state == "Z":
                continue
            command = current.command
        retained.append(
            {
                "pid": member.pid,
                "pgid": pgid,
                "state": state,
                "start_identity": member.start_identity,
                "command": command,
            }
        )
    return tuple(retained)


def join_tmux_session_retirement(
    identity: SessionRetirementIdentity,
    *,
    timeout: float = SESSION_RETIREMENT_TIMEOUT_SECONDS,
) -> None:
    """Boundedly join every exact captured birth; recycled PIDs are already retired."""

    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        retained = retained_tmux_session_births(identity)
        if not retained:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SessionRetirementError(f"tmux session retained process births after kill: {retained}")
        _SESSION_RETIREMENT_POLL.wait(min(SESSION_RETIREMENT_POLL_SECONDS, remaining))

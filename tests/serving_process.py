# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Serving-member predicate for gate liveness oracles, delegating to production.

Production's ``bounded_process_table(require_complete=True)``
(``yolomux_lib/local_services/registry.py``) is the one owner of "which pids are
serving": it reads the process table once and excludes any pid whose
``process_state(pid) == "Z"``, so a zombie -- a process that has exited but not
been reaped, which keeps its PID, its PGID, and its ``/proc/<pid>/stat`` start
ticks -- is simply absent. Tests that asked ``os.killpg(pgid, 0)`` or a raw
``process_start_identity`` read instead counted such a zombie as alive and so
diverged from production, reddening only under load when the transient zombie
window (a SIGKILLed child, or a SIGHUP'd tmux pane awaiting its reaper) outlives
the test's tolerance.

These predicates query that same table rather than building a second copy of its
logic: a pid serves iff it is a key, a group serves iff any entry's ``pgid``
matches. ``require_complete=True`` is load-bearing -- a failed ``ps`` read raises
``ProcessTableUnavailable`` rather than falsely certifying an alive process as
retired.
"""

from __future__ import annotations

from yolomux_lib.local_services.registry import ProcessTableEntry
from yolomux_lib.local_services.registry import bounded_process_table


def serving_process_table() -> dict[int, ProcessTableEntry]:
    """Return one complete, zombie-excluding snapshot from the production owner."""

    return bounded_process_table(require_complete=True)


def pid_is_serving(pid: int, *, table: dict[int, ProcessTableEntry] | None = None) -> bool:
    """Return True iff ``pid`` is a live, non-zombie process per production's table."""

    if table is None:
        table = serving_process_table()
    return pid in table


def process_group_has_serving_member(
    process_group_id: int,
    *,
    table: dict[int, ProcessTableEntry] | None = None,
) -> bool:
    """Return True iff any live, non-zombie member of the group is in the table."""

    if table is None:
        table = serving_process_table()
    return any(entry.pgid == process_group_id for entry in table.values())

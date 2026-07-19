"""Fail-closed launch preflight + ledger reconcile for one YOLOmux port.

Called by boot.sh after its listener teardown and before launching the new
server. Two cases:

- A WEDGED previous owner (alive but no longer listening, so the listener kill
  never reached it, while its lease identity still matches) REFUSES the launch
  with exit code 3 — stacking a new server on top of a live runaway is exactly
  how the 2026-07-19 incident compounded.
- A DEAD previous owner's leftovers are reconciled: members of its recorded
  process group that are now orphans (ppid 1) are identity-verified stale
  children (a SIGTERM'd Python server never runs teardown, so its tmux control
  client always lingers) and get a targeted TERM -> bounded wait -> KILL.
  Nothing outside that recorded group can ever be touched.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable

from ..common import STATE_DIR
from .registry import ProcessTableEntry
from .registry import bounded_process_table
from .registry import read_server_port_lease_record
from .registry import tracked_port_process_group

PREFLIGHT_REFUSE_EXIT = 3
PREFLIGHT_REAP_GRACE_SECONDS = 2.0


def stale_orphans_of_dead_owner(record: dict, table: dict[int, ProcessTableEntry]) -> list[int]:
    """Identity-verified leftovers: orphaned members of the dead owner's recorded group."""
    lease_pid = int(record.get("pid") or 0)
    lease_pgid = int(record.get("pgid") or 0)
    if not lease_pid or not lease_pgid or lease_pid in table:
        return []
    return sorted(pid for pid, entry in table.items() if entry.pgid == lease_pgid and entry.ppid == 1)


def preflight_port(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
    *,
    kill: Callable[[int, int], None] = os.kill,
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_process_table,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Refuse a wedged live owner; reconcile a dead owner's verified orphans."""
    if table is None:
        table = table_reader()
    group = tracked_port_process_group(port, state_dir, table)
    if group:
        return {
            "ok": False,
            "reason": (
                f"a previous port-{port} owner (pid {group['pid']}) is still alive after listener teardown; "
                "it is wedged (not listening) and must be stopped through its tracked group before relaunch"
            ),
            "tracked_pids": list(group["member_pids"]),
            "reaped_pids": [],
        }
    record = read_server_port_lease_record(port, state_dir)
    stale = stale_orphans_of_dead_owner(record, table)
    reaped: list[int] = []
    if stale:
        for pid in stale:
            try:
                kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
        sleep(PREFLIGHT_REAP_GRACE_SECONDS)
        survivors = table_reader()
        for pid in stale:
            if pid in survivors:
                try:
                    kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    continue
            reaped.append(pid)
    return {
        "ok": True,
        "reason": "clear to launch" if not reaped else f"reconciled {len(reaped)} stale orphan(s) of the dead previous owner",
        "tracked_pids": [],
        "reaped_pids": reaped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, default=STATE_DIR)
    args = parser.parse_args(argv)
    result = preflight_port(args.port, args.state_dir)
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        print(f"ERROR: {result['reason']}", file=sys.stderr)
        return PREFLIGHT_REFUSE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

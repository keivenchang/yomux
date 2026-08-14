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

from ..common import RUNTIME_DIR
from .registry import ProcessTableEntry
from .registry import ProcessTableUnavailable
from .registry import bounded_preflight_process_table
from .registry import process_fence_record
from .registry import process_record_diagnostic
from .registry import process_table_start_identity
from .registry import read_server_port_lease_record
from .registry import resolve_tracked_port_process_group
from .registry import stale_local_service_groups_of_dead_launcher
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request

PREFLIGHT_REFUSE_EXIT = 3
PREFLIGHT_REAP_GRACE_SECONDS = 2.0


def stale_orphans_of_dead_owner(record: dict, table: dict[int, ProcessTableEntry]) -> dict[int, dict]:
    """Return fenced records for orphans matching a live-owner member snapshot."""
    try:
        lease_pid = int(record.get("pid") or 0)
        lease_pgid = int(record.get("pgid") or 0)
    except (TypeError, ValueError):
        return {}
    raw_members = record.get("members")
    if not lease_pid or not lease_pgid or lease_pid in table or not isinstance(raw_members, list):
        return {}
    identities: set[tuple[int, int]] = set()
    for member in raw_members:
        if not isinstance(member, dict):
            continue
        try:
            pid, start_time = int(member.get("pid") or 0), int(member.get("start_time") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0 and start_time > 0:
            identities.add((pid, start_time))
    return {
        pid: process_fence_record(record, pid=pid, start_identity=process_table_start_identity(entry))
        for pid, entry in table.items()
        if entry.ppid == 1 and entry.pgid == lease_pgid and (pid, entry.start_time) in identities
    }


def local_service_status(group: dict) -> dict:
    """Read bounded status from the identity-verified service socket."""
    protocol_version = group.get("protocol_version")
    if not isinstance(protocol_version, int) or isinstance(protocol_version, bool) or protocol_version <= 0:
        return {}
    try:
        envelope = new_envelope(
            str(group["service"]),
            "status",
            {"action": "status", "protocol_version": protocol_version},
            timeout_seconds=0.2,
            priority="maintenance",
        )
        response, _binary = request(Path(str(group["socket"])), envelope, timeout_seconds=0.2, fallback_legacy=True)
    except (OSError, LocalRpcError):
        return {}
    return response if isinstance(response, dict) else {}


def stale_idle_service_members(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry],
    status_reader: Callable[[dict], dict] = local_service_status,
) -> dict[int, dict]:
    """Select fenced members of idle service groups left by a dead launcher."""
    members: dict[int, dict] = {}
    for group in stale_local_service_groups_of_dead_launcher(port, state_dir / "services", table):
        status = status_reader(group)
        if not isinstance(status, dict):
            continue
        status_pid = status.get("pid")
        clients = status.get("clients")
        if (
            status.get("ok") is True
            and isinstance(status_pid, int)
            and not isinstance(status_pid, bool)
            and status_pid == group["pid"]
            and isinstance(clients, int)
            and not isinstance(clients, bool)
            and clients == 0
        ):
            members.update(group["member_records"])
    return members


def preflight_port(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
    *,
    kill: Callable[[int, int], None] = os.kill,
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_preflight_process_table,
    sleep: Callable[[float], None] = time.sleep,
    service_status_reader: Callable[[dict], dict] = local_service_status,
) -> dict:
    """Refuse a wedged live owner; reconcile a dead owner's verified orphans."""
    if table is None:
        try:
            table = table_reader()
        except ProcessTableUnavailable as exc:
            return {
                "ok": False,
                "reason": str(exc),
                "reason_code": "process_table_unavailable",
                "tracked_pids": [],
                "reaped_pids": [],
                "failures": {},
            }
    record = read_server_port_lease_record(port, state_dir)
    group, owner_diagnostic = resolve_tracked_port_process_group(port, state_dir, table)
    if group:
        return {
            "ok": False,
            "reason": (
                f"a previous port-{port} owner (pid {group['pid']}) is still alive after listener teardown; "
                "it is wedged (not listening) and must be stopped through its tracked group before relaunch"
            ),
            "reason_code": "previous_owner_wedged",
            "tracked_pids": list(group["member_pids"]),
            "reaped_pids": [],
            "failures": {},
            "diagnostic": owner_diagnostic.as_dict() if owner_diagnostic is not None else {},
        }
    if record and owner_diagnostic is not None:
        if owner_diagnostic.current:
            return {
                "ok": False,
                "reason": "the previous owner record is current but its command/port identity is unverifiable",
                "reason_code": "previous_owner_identity_mismatch",
                "tracked_pids": [owner_diagnostic.pid],
                "reaped_pids": [],
                "failures": {},
                "diagnostic": owner_diagnostic.as_dict(),
            }
        if not owner_diagnostic.may_remove_stale_record:
            # Legacy unpartitioned records remain readable for rollout safety,
            # but missing host/boot proof can block launch only; it never grants
            # authority to signal a process or unlink an artifact.
            return {
                "ok": False,
                "reason": f"previous owner process identity refused: {owner_diagnostic.reason.value}",
                "reason_code": "process_identity_refused",
                "tracked_pids": [owner_diagnostic.pid] if owner_diagnostic.pid > 0 else [],
                "reaped_pids": [],
                "failures": {},
                "diagnostic": owner_diagnostic.as_dict(),
            }
    stale_records = stale_orphans_of_dead_owner(record, table)
    stale_records.update(stale_idle_service_members(port, state_dir, table, service_status_reader))
    stale = sorted(stale_records)
    reaped: list[int] = []
    failures: dict[int, str] = {}
    if stale:
        for pid in stale:
            diagnostic = process_record_diagnostic(stale_records[pid], table=table)
            if not diagnostic.current:
                failures[pid] = f"stale_orphan_{diagnostic.reason.value}"
                continue
            try:
                kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                reaped.append(pid)
            except PermissionError:
                failures[pid] = "stale_orphan_term_permission_denied"
        sleep(PREFLIGHT_REAP_GRACE_SECONDS)
        try:
            survivors = table_reader()
        except ProcessTableUnavailable as exc:
            unresolved = [pid for pid in stale if pid not in reaped]
            return {
                "ok": False,
                "reason": str(exc),
                "reason_code": "process_table_unavailable",
                "tracked_pids": unresolved,
                "reaped_pids": reaped,
                "failures": {str(pid): failures[pid] for pid in unresolved if pid in failures},
            }
        for pid in stale:
            if pid in reaped:
                continue
            if pid not in survivors:
                reaped.append(pid)
                continue
            diagnostic = process_record_diagnostic(stale_records[pid], table=survivors)
            if diagnostic.current:
                try:
                    kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    reaped.append(pid)
                except PermissionError:
                    failures[pid] = "stale_orphan_kill_permission_denied"
                else:
                    reaped.append(pid)
            else:
                failures[pid] = f"stale_orphan_{diagnostic.reason.value}"
        unresolved = [pid for pid in stale if pid not in reaped]
        if unresolved:
            return {
                "ok": False,
                "reason": f"failed to reconcile {len(unresolved)} stale orphan(s) of the dead previous owner",
                "reason_code": "stale_orphan_reap_failed",
                "tracked_pids": unresolved,
                "reaped_pids": reaped,
                "failures": {str(pid): failures.get(pid, "stale_orphan_still_alive") for pid in unresolved},
            }
    return {
        "ok": True,
        "reason": "clear to launch" if not reaped else f"reconciled {len(reaped)} stale orphan(s) of the dead previous owner",
        "reason_code": "clear_to_launch" if not reaped else "stale_orphans_reconciled",
        "tracked_pids": [],
        "reaped_pids": reaped,
        "failures": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, default=RUNTIME_DIR)
    args = parser.parse_args(argv)
    result = preflight_port(args.port, args.state_dir)
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        print(f"ERROR: {result['reason']}", file=sys.stderr)
        return PREFLIGHT_REFUSE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

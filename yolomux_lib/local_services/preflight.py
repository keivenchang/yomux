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

That TERM -> wait -> KILL is NOT written here. It used to be — a private loop on
its own 2-second clock with no force budget at all, one of four copies of one
algorithm — and this module now supplies targets to
:mod:`yolomux_lib.local_services.lifetime`, the one owner, under its
group-scoped mode. These targets are orphaned members of a dead WEB SERVER's
process group, so they carry no service kind of their own and no spawn
generation; the group scope binds what they DO carry (the dead owner's recorded
process group, re-read live before any signal) and refuses when it cannot be
proven.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from typing import Callable

from ..common import RUNTIME_DIR
from .lifetime import GROUP_TERMINATION_FORCE_SECONDS
from .lifetime import GROUP_TERMINATION_GRACE_SECONDS
from .lifetime import SCOPE_TRACKED_PROCESS_GROUP
from .lifetime import TerminationOutcome
from .lifetime import TerminationRequest
from .lifetime import authorize_service_destruction
from .lifetime import terminate_authorized_processes
from .registry import ProcessTableEntry
from .registry import ProcessTableUnavailable
from .registry import bounded_preflight_process_table
from .registry import live_process_group
from .registry import pid_is_serving
from .registry import process_fence_record
from .registry import process_record_diagnostic
from .registry import process_spawn_generation
from .registry import process_table_start_identity
from .registry import read_server_port_lease_record
from .registry import resolve_tracked_port_process_group
from .registry import stale_local_service_groups_of_dead_launcher
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request

PREFLIGHT_REFUSE_EXIT = 3
# The kind an orphaned member of a dead web server's group binds. It is not a
# service name because these targets are not services: it names which resolver
# proved the group, so a record produced by the service-group resolver can never
# be acted on as if it came from the port lease, and vice versa.
PREFLIGHT_WEB_GROUP_KIND = "dead-web-owner-port-group"
# The claim behind a preflight reap: the dead owner's port lease named this exact
# process group, and boot.sh is starting that same port. Group-scoped decisions
# require a claim, so an unnamed one produces zero signals.
PREFLIGHT_CLAIM_STATE = "preflight_dead_owner_port_lease"
# How often the reap re-checks its targets. Deliberately coarser than the
# owner's 30ms default: preflight's liveness probe is a COMPLETE process-table
# read (see `liveness_reader` below), and paying for one every 30ms across a
# three-second grace would be a boot-time cost with no extra proof behind it.
PREFLIGHT_VERIFY_POLL_SECONDS = 0.25


def stale_orphans_of_dead_owner(
    record: dict,
    table: dict[int, ProcessTableEntry],
    *,
    namespace: Path | None = None,
) -> dict[int, dict]:
    """Return fenced records for orphans matching a live-owner member snapshot.

    Each record is stamped with the three dimensions the destructive owner binds
    for a group-scoped target: the resolver that produced it, the directory the
    owning lease was read from, and the dead owner's recorded process group.
    """
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
        pid: {
            **process_fence_record(record, pid=pid, start_identity=process_table_start_identity(entry)),
            "service": PREFLIGHT_WEB_GROUP_KIND,
            "namespace": str(namespace) if namespace is not None else "",
            "pgid": lease_pgid,
        }
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
    """Select fenced members of idle service groups left by a dead launcher.

    Stamped with the same three group-scope dimensions as the web-owner orphans,
    except that the kind here is the service the record itself names -- these
    members share a leader that IS an addressable service.
    """
    members: dict[int, dict] = {}
    service_dir = Path(state_dir) / "services"
    for group in stale_local_service_groups_of_dead_launcher(port, service_dir, table):
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
            members.update({
                pid: {
                    **member_record,
                    "service": str(group["service"]),
                    "namespace": str(service_dir),
                    "pgid": int(group["pgid"]),
                }
                for pid, member_record in group["member_records"].items()
            })
    return members


def _stale_orphan_request(
    stale_record: dict[str, Any],
    *,
    table: dict[int, ProcessTableEntry],
    generation_reader: Callable[[int], str | None],
    process_group_reader: Callable[[int], int | None],
) -> TerminationRequest:
    """Bind one stale orphan to every dimension it genuinely carries, or refuse.

    ``expected_kind`` and ``expected_namespace`` are read back out of the record
    the resolver stamped, so a record that reached this loop from anywhere else
    -- a hand-built dict, a different resolver, a record for another directory --
    carries no kind or the wrong one and produces zero signals. The dimension
    that is MEASURED rather than structural is the process group: the resolver
    proved it from the dead owner's lease, and the owner re-reads it live off the
    running pid immediately before signalling.
    """

    return TerminationRequest(
        authorization=authorize_service_destruction(
            stale_record,
            diagnostic=process_record_diagnostic(stale_record, table=table),
            expected_kind=str(stale_record.get("service") or ""),
            expected_namespace=str(stale_record.get("namespace") or ""),
            live_generation_reader=generation_reader,
            claim_state=PREFLIGHT_CLAIM_STATE,
            require_claim=True,
            scope=SCOPE_TRACKED_PROCESS_GROUP,
            expected_process_group=int(stale_record.get("pgid") or 0),
            live_process_group_reader=process_group_reader,
        ),
        target=str(stale_record.get("service") or ""),
    )


def _stale_orphan_failure(outcome: TerminationOutcome) -> str:
    """Name why one orphan was not reconciled, in the destructive owner's vocabulary."""

    detail = outcome.error or outcome.reason or outcome.result
    return f"stale_orphan_{detail}"


def preflight_port(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
    *,
    kill: Callable[[int, int], None] = os.kill,
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_preflight_process_table,
    sleep: Callable[[float], None] = time.sleep,
    service_status_reader: Callable[[dict], dict] = local_service_status,
    # The two live dimension probes behind a group-scoped destructive decision,
    # injectable for the same reason `kill` and `table_reader` are.
    generation_reader: Callable[[int], str | None] = process_spawn_generation,
    process_group_reader: Callable[[int], int | None] = live_process_group,
    # The escalation's liveness poll. It defaults to the SAME complete-table
    # requirement the launch decision uses, and that is a safety property rather
    # than symmetry: `process_state` returns the empty string BOTH for a pid that
    # is gone and for a /proc it could not read, so polling it directly would
    # certify an unverifiable orphan as reaped and clear the launch. An
    # unreadable process table must refuse.
    liveness_reader: Callable[[int], bool] | None = None,
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
    stale_records = stale_orphans_of_dead_owner(record, table, namespace=Path(state_dir))
    stale_records.update(stale_idle_service_members(port, state_dir, table, service_status_reader))
    stale = sorted(stale_records)
    reaped: list[int] = []
    failures: dict[int, str] = {}
    if stale:
        # ONE batch through the ONE destructive owner: every stale orphan is
        # SIGTERMed before any of them is force-killed, which is what the private
        # loop did with its own clock and its own fence. The liveness poll asks
        # `pid_is_serving` directly rather than re-reading the whole process
        # table on every 30ms pass -- the complete-table requirement belongs to
        # the DECISION above, which has already been made.
        probe = liveness_reader if liveness_reader is not None else (lambda pid: pid_is_serving(pid, table=table_reader()))
        try:
            outcomes = terminate_authorized_processes(
                [
                    _stale_orphan_request(
                        stale_records[pid],
                        table=table,
                        generation_reader=generation_reader,
                        process_group_reader=process_group_reader,
                    )
                    for pid in stale
                ],
                still_current=probe,
                signal_process=kill,
                grace_seconds=GROUP_TERMINATION_GRACE_SECONDS,
                force_seconds=GROUP_TERMINATION_FORCE_SECONDS,
                sleep=sleep,
                poll_interval=PREFLIGHT_VERIFY_POLL_SECONDS,
            )
        except ProcessTableUnavailable as exc:
            # Mid-escalation the table stopped being readable. Signals may already
            # have gone out, but nothing about their effect can be proven, so this
            # refuses the launch instead of reporting a reap it did not observe.
            return {
                "ok": False,
                "reason": str(exc),
                "reason_code": "process_table_unavailable",
                "tracked_pids": stale,
                "reaped_pids": [],
                "failures": {},
            }
        for outcome in outcomes:
            if outcome.confirmed_dead:
                reaped.append(outcome.pid)
            else:
                failures[outcome.pid] = _stale_orphan_failure(outcome)
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

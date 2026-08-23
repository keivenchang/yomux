"""Bounded overload watchdog for one port's tracked YOLOmux process group.

Armed only while the dev stack is starting or its own capture/benchmark is
active. Identity comes exclusively from the shared ledger in ``registry``
(service records verified by exact socket, the web server verified by its own
port lease) — the watchdog holds no process map of its own and can never act
on a PID outside a tracked group. Containment is graceful-then-targeted:
SIGTERM through the group leaders, one bounded grace wait, then SIGKILL only
to still-live tracked member PIDs. System/security/indexing processes are
structurally unreachable because they can never enter a tracked group.

That escalation is NOT written here. It used to be — a private
SIGTERM/sleep/SIGKILL block on its own 3-second clock, one of four copies of
one algorithm — and this module now supplies targets to
:mod:`yolomux_lib.local_services.lifetime`, the one owner, under its
group-scoped mode. Its targets are members of a WEB SERVER's process group and
so carry no service kind of their own and no spawn generation; the group scope
supplies what they DO carry (the process group they share with a leader proven
from a persisted record, re-read live before any signal) and refuses when even
that cannot be proven. Every row in the incident evidence is now the owner's
own measured outcome rather than a second vocabulary mapped onto it.

One target class carries a dimension the web group does not: a tracked LOCAL
SERVICE record names the supervisor that spawned it. Several YOLOmux servers
share one runtime directory, so this watchdog can see -- and used to contain --
services another live server owns. A service whose recorded supervisor is not
PROVABLY gone by the shared identity fence is therefore retained: zero signals,
zero unlinks, and one typed row naming that surviving supervisor. Missing,
unreadable, and merely unprovable all retain too; only `may_remove_stale_record`
(the same property `registry._supervisor_is_gone` uses) counts as gone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from time import monotonic as monotonic_clock
from time import sleep as sleep_clock
from time import time as wall_clock
from typing import Any
from typing import Callable

from .lifetime import GROUP_TERMINATION_FORCE_SECONDS
from .lifetime import GROUP_TERMINATION_GRACE_SECONDS
from .lifetime import SCOPE_TRACKED_PROCESS_GROUP
from .lifetime import TerminationRequest
from .lifetime import authorize_service_destruction
from .lifetime import terminate_authorized_processes
from ..filesystem.io_ops import read_json_file
from ..host_identity import LocalProcessDiagnostic
from ..host_identity import LocalProcessReason
from .registry import ProcessTableEntry
from .registry import bounded_process_table
from .registry import live_process_group
from .registry import pid_is_serving
from .registry import process_record_diagnostic
from .registry import process_spawn_generation
from .registry import resolve_tracked_local_service_groups
from .registry import resolve_tracked_port_process_group

WATCHDOG_SAMPLE_INTERVAL_SECONDS = 2.0
WATCHDOG_CPU_PERCENT_LIMIT = 250.0
WATCHDOG_SUSTAINED_SAMPLES = 15
WATCHDOG_MAX_TRACKED_CHILDREN = 32
# The kind a web-server group member binds in the service-kind dimension. It is
# not a service name because these targets are not services; it is the typed
# name of the group authority the record came from, and a record stamped with
# anything else refuses.
WATCHDOG_WEB_GROUP_KIND = "web-server-port-group"
# The claim behind a containment: this watchdog is armed for THIS port and the
# target came out of that port's tracked ledger. Group-scoped decisions require
# a claim, so an unnamed one produces zero signals.
WATCHDOG_CLAIM_STATE = "watchdog_armed_tracked_group"


@dataclass
class GroupOverloadWatchdog:
    """Sample one tracked group at a fixed cadence and contain a sustained runaway."""

    port: int
    state_dir: Path
    service_dir: Path
    cpu_percent_limit: float = WATCHDOG_CPU_PERCENT_LIMIT
    sustained_samples: int = WATCHDOG_SUSTAINED_SAMPLES
    sample_interval_seconds: float = WATCHDOG_SAMPLE_INTERVAL_SECONDS
    grace_seconds: float = GROUP_TERMINATION_GRACE_SECONDS
    force_seconds: float = GROUP_TERMINATION_FORCE_SECONDS
    max_tracked_children: int = WATCHDOG_MAX_TRACKED_CHILDREN
    evidence_dir: Path = Path("/tmp")
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_process_table
    # The two live dimension probes behind a group-scoped destructive decision,
    # injectable for exactly the reason `kill` and `table_reader` are: whoever
    # tells this watchdog what it may see also drives what it may prove.
    generation_reader: Callable[[int], str | None] = process_spawn_generation
    process_group_reader: Callable[[int], int | None] = live_process_group
    # The escalation's liveness poll. `pid_is_serving` is the SAME zombie-aware
    # rule `bounded_process_table` applies per row, asked about one pid: the poll
    # runs every 30ms per target during a CPU overload, and sweeping the whole
    # process table on each pass would be a second load on top of the first.
    liveness_reader: Callable[[int], bool] = pid_is_serving
    # Who still owns a tracked service. Deliberately the DEFAULT (/proc-backed)
    # fence rather than one bound to the sampled process table: the table is a
    # snapshot taken to measure CPU, and proving a supervisor DEAD from a
    # snapshot that was allowed to come back short would turn a read failure into
    # a kill. This is the same call `registry._supervisor_is_gone` makes, so
    # there is one answer to "is the supervisor gone", not two.
    supervisor_reader: Callable[[dict[str, Any]], LocalProcessDiagnostic] = process_record_diagnostic
    kill: Callable[[int, int], None] = os.kill
    clock: Callable[[], float] = monotonic_clock
    sleep: Callable[[float], None] = sleep_clock
    fired: bool = field(default=False, init=False)
    last_snapshot: dict[str, Any] = field(default_factory=dict, init=False)
    _previous: tuple[tuple[int, ...], float, float] | None = field(default=None, init=False)
    _over_count: int = field(default=0, init=False)
    _cpu_history: list[float] = field(default_factory=list, init=False)
    # The exact fenced record each authorized pid was bound to, so the mid-
    # escalation identity re-proof reads the same evidence the decision did.
    _fenced_records: dict[int, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def tracked(self, table: dict[int, ProcessTableEntry]) -> dict[str, Any]:
        """Resolve the tracked group strictly from the shared ledger."""
        web, web_diagnostic = resolve_tracked_port_process_group(self.port, self.state_dir, table)
        services, service_diagnostics = resolve_tracked_local_service_groups(self.service_dir, table)
        process_diagnostics = list(service_diagnostics)
        if web_diagnostic is not None and not web_diagnostic.current:
            process_diagnostics.append({
                "target": "web",
                "pid": web_diagnostic.pid,
                "diagnostic": web_diagnostic.as_dict(),
            })
        service_members = {pid for group in services for pid in group["member_pids"]}
        web_members = tuple(pid for pid in web.get("member_pids", ()) if pid not in service_members)
        all_members = tuple(sorted(set(web_members) | service_members))
        return {
            "web": web,
            "web_members": web_members,
            "services": services,
            "member_pids": all_members,
            "process_diagnostics": process_diagnostics,
        }

    def sample_once(self) -> dict[str, Any]:
        table = self.table_reader()
        tracked = self.tracked(table)
        members = tracked["member_pids"]
        cpu_seconds = sum(table[pid].cpu_seconds for pid in members if pid in table)
        now = self.clock()
        cpu_percent: float | None = None
        previous = self._previous
        if previous is not None and previous[0] == members and now > previous[1] and cpu_seconds >= previous[2]:
            cpu_percent = round((cpu_seconds - previous[2]) / (now - previous[1]) * 100.0, 3)
        self._previous = (members, now, cpu_seconds)
        if cpu_percent is not None:
            self._cpu_history = (self._cpu_history + [cpu_percent])[-self.sustained_samples :]
        over = (cpu_percent is not None and cpu_percent >= self.cpu_percent_limit) or len(members) > self.max_tracked_children
        self._over_count = self._over_count + 1 if over else 0
        snapshot = {
            "armed": True,
            "port": self.port,
            "member_count": len(members),
            "cpu_percent": cpu_percent,
            "over_count": self._over_count,
            "sustained_samples": self.sustained_samples,
            "cpu_percent_limit": self.cpu_percent_limit,
            "fired": self.fired,
            "process_diagnostics": tracked["process_diagnostics"],
        }
        if self._over_count >= self.sustained_samples and not self.fired and members:
            snapshot.update(self._contain(tracked, table))
            snapshot["fired"] = self.fired
        self.last_snapshot = snapshot
        return snapshot

    def run(self, duration_seconds: float) -> dict[str, Any]:
        """Arm the watchdog for one bounded window (startup or capture)."""
        deadline = self.clock() + max(0.0, duration_seconds)
        while self.clock() < deadline and not self.fired:
            self.sample_once()
            self.sleep(self.sample_interval_seconds)
        return dict(self.last_snapshot)

    def _other_web_ports_active(self, table: dict[int, ProcessTableEntry]) -> bool:
        """Conservative shared-service veto: another live YOLOmux web server exists.

        Command matching here can only REDUCE what gets stopped (a false
        positive skips service groups and reports them), so the ledger's
        no-name-matching rule for membership is not weakened.
        """
        own_marker = f"--port {self.port} "
        for entry in table.values():
            command = entry.command + " "
            if "yolomux.py" in command and "--port " in command and own_marker not in command:
                return True
        return False

    def _identity_replaced(self, pid: int) -> bool:
        """Whether the exact identity this batch was authorized against is gone.

        Reads the SAME fenced record the authorization was built from, so the
        answer cannot drift from the decision. Anything other than a proven
        reuse is False here: "gone" is the escalation's own success condition
        and "unreadable" is already handled by the authorization refusing.
        """

        record = self._fenced_records.get(int(pid))
        if record is None:
            return False
        return process_record_diagnostic(record).reason is LocalProcessReason.PROCESS_IDENTITY_REUSED

    def _service_supervisor(self, group: dict[str, Any]) -> LocalProcessDiagnostic | None:
        """Prove who still owns one tracked service, from that service's own record.

        Re-read from the persisted record rather than taken off the resolved
        group, because the group carries only the fence fields and the supervisor
        is the dimension that decides whether this watchdog may act at all. A
        record that cannot be read, that has rotated onto a different PID since
        the group was resolved, or that names no supervisor returns ``None`` --
        which the authorization RETAINS. "I could not find an owner" is never
        "there is no owner".
        """

        record_path = str(group.get("record_path") or "")
        record = read_json_file(Path(record_path), None) if record_path else None
        if not isinstance(record, dict):
            return None
        try:
            recorded_pid = int(record.get("pid") or 0)
        except (TypeError, ValueError):
            return None
        if recorded_pid != int(group.get("pid") or 0):
            return None
        supervisor = record.get("supervisor")
        if not isinstance(supervisor, dict) or not supervisor:
            return None
        return self.supervisor_reader(supervisor)

    def _termination_request(
        self,
        *,
        target: str,
        pid: int,
        record: dict[str, Any],
        kind: str,
        namespace: Path,
        pgid: int,
        table: dict[int, ProcessTableEntry],
        graceful_first: bool,
        require_supervisor_gone: bool = False,
        supervisor_diagnostic: LocalProcessDiagnostic | None = None,
    ) -> TerminationRequest:
        """Bind one tracked-group target to every dimension it genuinely carries.

        Host, boot, pid and process-start identity come from the shared fence, as
        before. What the private loop this replaced could never bind are the
        three the group scope adds: the directory the owning record was read
        from, the typed group authority that record names, and the process group
        the target still shares with the leader whose identity was proven. A
        record that names a different kind, a different directory, or a different
        group produces zero signals and one typed row.

        ``require_supervisor_gone`` adds the fifth, for a tracked SERVICE only:
        that record names the owner that spawned it, and a service whose owner is
        still alive is that owner's to stop -- containing it from here is exactly
        the cross-owner accident a runtime directory shared by several YOLOmux
        servers makes possible. The web server's port lease names no supervisor
        (it is the top of the supervision tree, started by boot.sh), so its group
        does not demand the dimension rather than failing an unprovable one and
        retaining the very group this watchdog exists to contain.
        """

        fenced = dict(record)
        fenced["service"] = kind
        fenced["namespace"] = str(namespace)
        fenced["pgid"] = int(pgid)
        self._fenced_records[int(pid)] = fenced
        return TerminationRequest(
            authorization=authorize_service_destruction(
                fenced,
                diagnostic=process_record_diagnostic(fenced, table=table),
                expected_kind=kind,
                expected_namespace=str(namespace),
                live_generation_reader=self.generation_reader,
                claim_state=WATCHDOG_CLAIM_STATE,
                require_claim=True,
                scope=SCOPE_TRACKED_PROCESS_GROUP,
                expected_process_group=int(pgid),
                live_process_group_reader=self.process_group_reader,
                require_supervisor_gone=require_supervisor_gone,
                supervisor_diagnostic=supervisor_diagnostic,
            ),
            target=target,
            graceful_first=graceful_first,
        )

    def _contain(self, tracked: dict[str, Any], table: dict[int, ProcessTableEntry]) -> dict[str, Any]:
        """Stop the tracked group: graceful leaders first, then targeted SIGKILL.

        The escalation itself belongs to `terminate_authorized_processes`. This
        function only decides WHO is in the batch and which of them has already
        had its graceful window; the leaders go in graceful-first and the
        remaining members force-only, which is exactly what the private loop did
        (leaders TERMed, one shared grace, survivors KILLed) with one clock, one
        fence and one vocabulary instead of this module's own.

        Every tracked SERVICE additionally carries its recorded supervisor into
        the decision, so a service another live server still owns leaves this
        function with zero signals and one row naming that owner. The batch is
        still built the same way: the veto is a dimension inside the one
        authorization, not a branch in front of it.
        """
        self.fired = True
        actions: list[dict[str, Any]] = []
        shared_veto = self._other_web_ports_active(table)
        web = tracked["web"]
        web_pgid = int(web.get("pgid") or 0) if web else 0
        web_pid = int(web.get("pid") or 0) if web else 0
        # target -> (record, kind, namespace, pgid); leaders are inserted first so
        # the batch signals them gracefully before any member is forced.
        leaders: list[TerminationRequest] = []
        members: list[TerminationRequest] = []
        if web_pid:
            leaders.append(self._termination_request(
                target="web",
                pid=web_pid,
                record=web["process_record"],
                kind=WATCHDOG_WEB_GROUP_KIND,
                namespace=self.state_dir,
                pgid=web_pgid,
                table=table,
                graceful_first=True,
            ))
        for pid in sorted(tracked["web_members"]):
            if pid == web_pid:
                continue
            members.append(self._termination_request(
                target="tracked-member",
                pid=pid,
                record=web["member_records"][pid],
                kind=WATCHDOG_WEB_GROUP_KIND,
                namespace=self.state_dir,
                pgid=web_pgid,
                table=table,
                graceful_first=False,
            ))
        for group in tracked["services"]:
            if shared_veto:
                actions.append({"target": group["service"], "pid": group["pid"], "action": "skipped-shared", "reason": "another web port is live"})
                continue
            # Proven ONCE per group and shared by the leader and every member:
            # they are one service, so two reads could disagree and let a member
            # be killed under a supervisor that retained its leader.
            supervisor = self._service_supervisor(group)
            leaders.append(self._termination_request(
                target=str(group["service"]),
                pid=int(group["pid"]),
                record=group["process_record"],
                kind=str(group["service"]),
                namespace=self.service_dir,
                pgid=int(group["pgid"]),
                table=table,
                graceful_first=True,
                require_supervisor_gone=True,
                supervisor_diagnostic=supervisor,
            ))
            for pid in sorted(group["member_pids"]):
                if pid == int(group["pid"]):
                    continue
                members.append(self._termination_request(
                    target="tracked-member",
                    pid=pid,
                    record=group["member_records"][pid],
                    kind=str(group["service"]),
                    namespace=self.service_dir,
                    pgid=int(group["pgid"]),
                    table=table,
                    graceful_first=False,
                    require_supervisor_gone=True,
                    supervisor_diagnostic=supervisor,
                ))
        outcomes = terminate_authorized_processes(
            leaders + members,
            # Two different questions, deliberately answered by two different
            # readers. The poll asks only "is this pid still SERVING". The
            # identity question runs only at the phase boundaries, where the
            # private loop this replaced asked it too: a pid recycled between the
            # SIGTERM and the SIGKILL is a different process and must yield, not
            # be force-killed.
            still_current=self.liveness_reader,
            identity_replaced=self._identity_replaced,
            signal_process=self.kill,
            grace_seconds=self.grace_seconds,
            force_seconds=self.force_seconds,
            clock=self.clock,
            sleep=self.sleep,
        )
        actions.extend(outcome.as_dict() for outcome in outcomes)
        evidence_path = self._write_evidence(actions, shared_veto)
        return {"actions": actions, "evidence_path": evidence_path}

    def _write_evidence(self, actions: list[dict[str, Any]], shared_veto: bool) -> str:
        """Persist a bounded, redacted incident summary under /tmp (no command lines)."""
        summary = {
            "version": 1,
            "port": self.port,
            "reason": "sustained tracked-group overload",
            "cpu_percent_limit": self.cpu_percent_limit,
            "sustained_samples": self.sustained_samples,
            "cpu_percent_history": self._cpu_history,
            "shared_service_veto": shared_veto,
            "actions": actions,
            "written_at": wall_clock(),
        }
        path = self.evidence_dir / f"yolomux-overload-{self.port}-{int(wall_clock())}.json"
        try:
            path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            return ""
        return str(path)

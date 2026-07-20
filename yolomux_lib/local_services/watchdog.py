"""Bounded overload watchdog for one port's tracked YOLOmux process group.

Armed only while the dev stack is starting or its own capture/benchmark is
active. Identity comes exclusively from the shared ledger in ``registry``
(service records verified by exact socket, the web server verified by its own
port lease) — the watchdog holds no process map of its own and can never act
on a PID outside a tracked group. Containment is graceful-then-targeted:
SIGTERM through the group leaders, one bounded grace wait, then SIGKILL only
to still-live tracked member PIDs. System/security/indexing processes are
structurally unreachable because they can never enter a tracked group.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from time import monotonic as monotonic_clock
from time import sleep as sleep_clock
from time import time as wall_clock
from typing import Any
from typing import Callable

from .registry import ProcessTableEntry
from .registry import bounded_process_table
from .registry import tracked_local_service_groups
from .registry import tracked_port_process_group

WATCHDOG_SAMPLE_INTERVAL_SECONDS = 2.0
WATCHDOG_CPU_PERCENT_LIMIT = 250.0
WATCHDOG_SUSTAINED_SAMPLES = 15
WATCHDOG_MAX_TRACKED_CHILDREN = 32
WATCHDOG_GRACE_SECONDS = 3.0


@dataclass
class GroupOverloadWatchdog:
    """Sample one tracked group at a fixed cadence and contain a sustained runaway."""

    port: int
    state_dir: Path
    service_dir: Path
    cpu_percent_limit: float = WATCHDOG_CPU_PERCENT_LIMIT
    sustained_samples: int = WATCHDOG_SUSTAINED_SAMPLES
    sample_interval_seconds: float = WATCHDOG_SAMPLE_INTERVAL_SECONDS
    grace_seconds: float = WATCHDOG_GRACE_SECONDS
    max_tracked_children: int = WATCHDOG_MAX_TRACKED_CHILDREN
    evidence_dir: Path = Path("/tmp")
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_process_table
    kill: Callable[[int, int], None] = os.kill
    clock: Callable[[], float] = monotonic_clock
    sleep: Callable[[float], None] = sleep_clock
    fired: bool = field(default=False, init=False)
    last_snapshot: dict[str, Any] = field(default_factory=dict, init=False)
    _previous: tuple[tuple[int, ...], float, float] | None = field(default=None, init=False)
    _over_count: int = field(default=0, init=False)
    _cpu_history: list[float] = field(default_factory=list, init=False)

    def tracked(self, table: dict[int, ProcessTableEntry]) -> dict[str, Any]:
        """Resolve the tracked group strictly from the shared ledger."""
        web = tracked_port_process_group(self.port, self.state_dir, table)
        services = tracked_local_service_groups(self.service_dir, table)
        service_members = {pid for group in services for pid in group["member_pids"]}
        web_members = tuple(pid for pid in web.get("member_pids", ()) if pid not in service_members)
        all_members = tuple(sorted(set(web_members) | service_members))
        return {"web": web, "web_members": web_members, "services": services, "member_pids": all_members}

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

    def _signal(self, pid: int, signum: int) -> str:
        try:
            self.kill(pid, signum)
        except ProcessLookupError:
            return "already-exited"
        except PermissionError:
            return "permission-denied"
        return "signalled"

    def _contain(self, tracked: dict[str, Any], table: dict[int, ProcessTableEntry]) -> dict[str, Any]:
        """Stop the tracked group: graceful leaders first, then targeted SIGKILL."""
        self.fired = True
        actions: list[dict[str, Any]] = []
        shared_veto = self._other_web_ports_active(table)
        term_targets: list[tuple[str, int]] = []
        web_pid = int(tracked["web"].get("pid") or 0)
        if web_pid:
            term_targets.append(("web", web_pid))
        kill_scope = set(tracked["web_members"])
        for group in tracked["services"]:
            if shared_veto:
                actions.append({"target": group["service"], "pid": group["pid"], "action": "skipped-shared", "reason": "another web port is live"})
                continue
            term_targets.append((group["service"], group["pid"]))
            kill_scope.update(group["member_pids"])
        for name, pid in term_targets:
            actions.append({"target": name, "pid": pid, "action": "sigterm", "result": self._signal(pid, signal.SIGTERM)})
        self.sleep(self.grace_seconds)
        survivors_table = self.table_reader()
        for pid in sorted(kill_scope):
            if pid in survivors_table:
                actions.append({"target": "tracked-member", "pid": pid, "action": "sigkill", "result": self._signal(pid, signal.SIGKILL)})
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

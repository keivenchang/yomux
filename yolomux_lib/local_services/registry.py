"""Cross-port lifecycle owner for bounded local YOLOmux services."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from time import monotonic as monotonic_clock
from time import sleep as sleep_clock
from time import time as wall_clock

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..background_owner import pid_is_alive
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request
from .rpc import safe_socket_path
from .runtime import redact_local_service_text


LOCAL_SERVICE_REGISTRY_VERSION = 2
LOCAL_SERVICE_IDLE_SECONDS = 60.0
# A cold daemon can be delayed by concurrent browser/E2E workers on a
# developer machine. Startup remains bounded, but it must outlast that normal
# scheduler pressure before declaring the shared service unavailable.
LOCAL_SERVICE_START_TIMEOUT_SECONDS = 5.0
LOCAL_SERVICE_BACKOFF_SECONDS = 0.25
LOCAL_SERVICE_MAX_BACKOFF_SECONDS = 8.0
LOCAL_SERVICE_HEALTH_CACHE_SECONDS = 1.0
LOCAL_SERVICE_IDLE_SECONDS_ENV = "YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS"
LOCAL_SERVICE_START_EXIT_LIMIT = 3
LOCAL_SERVICE_STDERR_TAIL_BYTES = 4096


_LAUNCH_CONTEXT: dict[str, int] = {}


def set_local_service_launch_context(port: int) -> None:
    """Record which web port owns subsequently written service records.

    The ledger (`tracked_local_service_groups`) needs launch provenance so a
    watchdog can tell "spawned for this port" from "shared daemon another port
    still leases". One process serves one port, so module state is the owner.
    """
    _LAUNCH_CONTEXT["port"] = int(port)


def local_service_launch_port() -> int:
    return int(_LAUNCH_CONTEXT.get("port") or 0)


def process_group_id(pid: int) -> int:
    try:
        return os.getpgid(int(pid))
    except (OSError, ValueError):
        return 0


@dataclass(frozen=True)
class ProcessTableEntry:
    ppid: int
    pgid: int
    cpu_seconds: float
    command: str


def bounded_process_table() -> dict[int, ProcessTableEntry]:
    """One bounded read of pid -> (ppid, pgid, cpu seconds, command).

    This is the single identity source for the ledger and the overload
    watchdog. Ledger membership decisions never use bare command-name
    matching; the table supplies exact parent/group identity plus the command
    line so a record's PID is only trusted when its command still names the
    record's exact socket, and cumulative CPU time rides along so overload
    sampling does not need a second process sweep.
    """
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,time=,command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    table: dict[int, ProcessTableEntry] = {}
    for line in str(getattr(completed, "stdout", "") or "").splitlines():
        fields = line.split(None, 4)
        if len(fields) < 4:
            continue
        try:
            pid, ppid, pgid = int(fields[0]), int(fields[1]), int(fields[2])
        except ValueError:
            continue
        cpu_seconds = parse_ps_cpu_seconds(fields[3])
        if cpu_seconds is None:
            continue
        table[pid] = ProcessTableEntry(ppid, pgid, cpu_seconds, fields[4] if len(fields) > 4 else "")
    return table


def service_record_identity_matches(record: dict[str, Any], table: dict[int, ProcessTableEntry]) -> bool:
    """A record's PID counts only when the live command names its exact socket."""
    pid = int(record.get("pid") or 0)
    socket_path = str(record.get("socket") or "")
    if pid <= 0 or not socket_path or pid not in table:
        return False
    return f"--socket {socket_path}" in table[pid].command


def tracked_local_service_groups(
    service_dir: Path,
    table: dict[int, tuple[int, int, str]] | None = None,
) -> list[dict[str, Any]]:
    """Enumerate the identity-verified process groups this registry dir owns.

    Every entry is anchored to a persisted service record whose PID passed the
    exact-socket identity check; members are the PIDs sharing the service's
    process group (each service is spawned with start_new_session, so its
    spawn/pool workers inherit that fresh group and nothing else can join it).
    Unverifiable records yield no entry — the caller must never act on a PID
    that is not in a returned group.
    """
    if table is None:
        table = bounded_process_table()
    groups: list[dict[str, Any]] = []
    try:
        record_paths = sorted(Path(service_dir).glob("*.service.json"))
    except OSError:
        return groups
    for record_path in record_paths:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or not service_record_identity_matches(record, table):
            continue
        pid = int(record.get("pid") or 0)
        pgid = table[pid].pgid
        members = tuple(sorted(member for member, entry in table.items() if entry.pgid == pgid))
        groups.append(
            {
                "service": str(record.get("service") or ""),
                "pid": pid,
                "pgid": pgid,
                "socket": str(record.get("socket") or ""),
                "launcher_pid": int(record.get("launcher_pid") or 0),
                "launcher_port": int(record.get("launcher_port") or 0),
                "member_pids": members,
                "record_path": str(record_path),
            }
        )
    return groups


def read_server_port_lease_record(port: int, state_dir: Path) -> dict[str, Any]:
    """Read the existing per-port ownership record written by acquire_server_port_lease."""
    lease_path = Path(state_dir) / "server-leases" / f"{int(port)}.lock"
    try:
        record = json.loads(lease_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return record if isinstance(record, dict) else {}


def tracked_port_process_group(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> dict[str, Any]:
    """Resolve the web-server process group for one port from its lease record.

    Identity is the lease PID (written by the server itself under its flock)
    cross-checked against the live command naming this exact port; a recycled
    or unrelated PID fails that check and yields an empty result. Members are
    the PIDs sharing the web server's process group — local-service daemons are
    session leaders of their own groups, so they never appear here.
    """
    if table is None:
        table = bounded_process_table()
    record = read_server_port_lease_record(port, state_dir)
    pid = int(record.get("pid") or 0)
    if pid <= 0 or int(record.get("port") or 0) != int(port) or pid not in table:
        return {}
    if f"--port {int(port)} " not in table[pid].command + " ":
        return {}
    pgid = table[pid].pgid
    members = tuple(sorted(member for member, entry in table.items() if entry.pgid == pgid))
    return {"port": int(port), "pid": pid, "pgid": pgid, "member_pids": members}


def parse_ps_cpu_seconds(text: str) -> float | None:
    """Parse a ps cumulative CPU time ([[dd-]hh:]mm:ss[.ff]) into seconds."""
    raw = str(text or "").strip()
    if not raw:
        return None
    days = 0
    if "-" in raw:
        day_part, _, raw = raw.partition("-")
        try:
            days = int(day_part)
        except ValueError:
            return None
    try:
        parts = [float(part) for part in raw.split(":")]
    except ValueError:
        return None
    if not parts:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds + days * 86400.0


@dataclass(frozen=True)
class LocalServiceSpec:
    name: str
    module: str
    socket_name: str
    protocol_version: int
    idle_seconds: float = LOCAL_SERVICE_IDLE_SECONDS
    extra_args: tuple[str, ...] = ()
    # Optional code-revision stamp: when set, a daemon whose ping reports a DIFFERENT
    # (or missing) revision is unhealthy and gets retired + respawned from current code.
    # This closes the same-protocol stale-daemon class (repeated 2026-07-14/15 incidents:
    # daemons surviving restarts while serving old code); a protocol bump already forces
    # respawn, but most code changes do not bump the protocol.
    code_revision: str = ""
    build_revision: int = 0


class LocalServiceRegistry:
    """Discover or start exactly one service for a state directory and spec."""

    def __init__(
        self,
        state_dir: Path,
        spec: LocalServiceSpec,
        *,
        socket_path: Path | None = None,
        service_dir: Path | None = None,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        clock: Callable[[], float] = monotonic_clock,
        sleep: Callable[[float], None] = sleep_clock,
    ):
        self.state_dir = Path(state_dir).expanduser()
        self._service_dir = Path(service_dir).expanduser() if service_dir is not None else None
        self.spec = spec
        self._socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{spec.name}") if socket_path is not None else None
        self.popen = popen
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.process: subprocess.Popen[Any] | None = None
        self.failures = 0
        self.next_start_at = 0.0
        self._healthy_until = 0.0
        self._last_resource_sample: tuple[float, float] | None = None
        self._last_resource_group_sample: tuple[tuple[int, ...], float, float] | None = None
        self._upgrade_required: dict[str, Any] | None = None
        self._start_exit_count = 0
        self._last_exit_code: int | None = None
        self._failure_reason = ""
        self._terminal_failure = False

    @property
    def service_dir(self) -> Path:
        return self._service_dir or self.state_dir / "services"

    @property
    def socket_path(self) -> Path:
        if self._socket_path is not None:
            return self._socket_path
        return safe_socket_path(self.service_dir / self.spec.socket_name, prefix=f"yolomux-{self.spec.name}")

    @property
    def record_path(self) -> Path:
        return self.socket_path.with_suffix(".service.json")

    @property
    def lock_path(self) -> Path:
        # A long socket path can fall back under /tmp. Keep durable locks in
        # the configured state directory so service startup never chmods /tmp.
        return self.service_dir / f"{self.spec.name}.service.lock"

    @property
    def stderr_path(self) -> Path:
        return self.socket_path.with_suffix(".stderr.log")

    def _read_record(self) -> dict[str, Any]:
        try:
            value = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_record(self, record: dict[str, Any]) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.record_path, json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)

    def _remove_stale_record(self) -> None:
        record = self._read_record()
        pid = int(record.get("pid") or 0)
        if pid and pid_is_alive(pid):
            return
        try:
            self.record_path.unlink()
        except FileNotFoundError:
            pass

    def _retire_incompatible_service(self) -> None:
        """Stop the service currently bound to our socket after a protocol bump or
        code-revision drift. Same-protocol drift matters: without it the stale daemon
        keeps the socket, the fresh spawn cannot bind, and ensure_started fails forever."""
        response = self._request("ping", timeout=0.15)
        service_pid = int(response.get("pid") or 0)
        service_version = int(response.get("version") or 0)
        if service_version > self.spec.protocol_version:
            self._upgrade_required = {
                "required_protocol_version": service_version,
                "current_protocol_version": self.spec.protocol_version,
                "pid": service_pid,
            }
            return
        service_build = int(response.get("build") or 0)
        compatible = service_version == self.spec.protocol_version and (
            service_build > self.spec.build_revision
            or not self.spec.code_revision
            or str(response.get("code_revision") or "") == self.spec.code_revision
        )
        older_upgrade = (
            service_version > 0
            and service_version < self.spec.protocol_version
            and (
                response.get("error_code") == "upgrade_required"
                or response.get("status") == "upgrade_required"
            )
        )
        if (not response.get("ok") and not older_upgrade) or not service_pid or compatible:
            return
        record = self._read_record()
        record_pid = int(record.get("pid") or 0)
        if record_pid and record_pid != service_pid:
            return
        self._request("shutdown", timeout=0.25)
        deadline = self.clock() + 0.5
        while pid_is_alive(service_pid) and self.clock() < deadline:
            self.sleep(0.03)
        if pid_is_alive(service_pid):
            try:
                os.kill(service_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                return
            deadline = self.clock() + 0.5
            while pid_is_alive(service_pid) and self.clock() < deadline:
                self.sleep(0.03)
        if not pid_is_alive(service_pid):
            self._remove_stale_record()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _request(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 0.2) -> dict[str, Any]:
        try:
            request_payload = {"action": method, "protocol_version": self.spec.protocol_version, **(payload or {})}
            envelope = new_envelope(self.spec.name, method, request_payload, timeout_seconds=timeout)
            response, _binary = request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError):
            return {}
        return response if isinstance(response, dict) else {}

    def healthy(self) -> bool:
        response = self._request("ping", timeout=0.15)
        service_version = int(response.get("version") or response.get("required_protocol_version") or 0)
        if service_version > self.spec.protocol_version:
            self._upgrade_required = {
                **response,
                "required_protocol_version": service_version,
                "current_protocol_version": self.spec.protocol_version,
                "pid": int(response.get("pid") or 0),
            }
            self.note_rpc_failure()
            return False
        healthy = (
            bool(response.get("ok"))
            and service_version == self.spec.protocol_version
            and int(response.get("pid") or 0) > 0
        )
        if healthy and self.spec.code_revision:
            # Self-heal on code drift: an old daemon that omits the stamp counts as a
            # mismatch too (respawning is idempotent and safe; never a hang).
            service_build = int(response.get("build") or 0)
            healthy = (
                service_build > self.spec.build_revision
                or str(response.get("code_revision") or "") == self.spec.code_revision
            )
        if healthy:
            self._upgrade_required = None
            self.note_rpc_success()
        else:
            self.note_rpc_failure()
        return healthy

    def note_rpc_success(self) -> None:
        """Cache recent transport health to avoid ping/status fan-out per action."""
        self._healthy_until = self.clock() + LOCAL_SERVICE_HEALTH_CACHE_SECONDS

    def note_rpc_failure(self) -> None:
        self._healthy_until = 0.0

    def recently_healthy(self) -> bool:
        return self.clock() < self._healthy_until

    def _record_from_status(self, status: dict[str, Any]) -> dict[str, Any]:
        pid = int(status.get("pid") or 0)
        worker_pids = status.get("worker_pids")
        return {
            "version": LOCAL_SERVICE_REGISTRY_VERSION,
            "service": self.spec.name,
            "module": self.spec.module,
            "pid": pid,
            # Ledger provenance: the process group anchors watchdog/cleanup
            # membership, and the launcher identifies which web port asked for
            # this daemon (shared daemons keep the first launcher's stamp; live
            # lease/client state, not this record, decides sharedness).
            "pgid": process_group_id(pid),
            "launcher_pid": os.getpid(),
            "launcher_port": local_service_launch_port(),
            "worker_pids": [int(worker) for worker in worker_pids if isinstance(worker, int) and worker > 0] if isinstance(worker_pids, list) else [],
            "protocol_version": int(status.get("version") or 0),
            "socket": str(self.socket_path),
            "started_at": float(status.get("started_at") or wall_clock()),
            "updated_at": wall_clock(),
        }

    def _mark_failure(
        self,
        reason: str = "",
        *,
        exit_code: int | None = None,
        exited_before_ready: bool = False,
    ) -> None:
        self.failures += 1
        self._last_exit_code = exit_code
        self._failure_reason = redact_local_service_text(reason)
        if exited_before_ready:
            self._start_exit_count += 1
            self._terminal_failure = self._start_exit_count >= LOCAL_SERVICE_START_EXIT_LIMIT
        delay = min(LOCAL_SERVICE_MAX_BACKOFF_SECONDS, LOCAL_SERVICE_BACKOFF_SECONDS * (2 ** max(0, self.failures - 1)))
        self.next_start_at = self.clock() + delay

    def _stderr_tail(self) -> str:
        try:
            with self.stderr_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - LOCAL_SERVICE_STDERR_TAIL_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return redact_local_service_text(lines[-1] if lines else "")

    def _spawn_failure_reason(self, exit_code: int | None) -> str:
        summary = f"{self.spec.name} exited ({exit_code if exit_code is not None else 'unknown'})"
        tail = self._stderr_tail()
        return f"{summary}: {tail}" if tail else summary

    def failure_response(self) -> dict[str, Any]:
        reason = self._failure_reason or f"{self.spec.name} unavailable"
        return {
            "ok": False,
            "status": "unavailable",
            "reason": reason,
            "terminal": self._terminal_failure,
            "exit_code": self._last_exit_code,
        }

    def retry(self) -> None:
        """Clear a latched startup failure for one explicit operator retry."""
        with self.lock:
            self.failures = 0
            self.next_start_at = 0.0
            self._start_exit_count = 0
            self._last_exit_code = None
            self._failure_reason = ""
            self._terminal_failure = False

    def _spawn(self) -> subprocess.Popen[Any] | None:
        idle_seconds = self.spec.idle_seconds
        configured_idle = os.environ.get(LOCAL_SERVICE_IDLE_SECONDS_ENV)
        if configured_idle:
            try:
                idle_seconds = max(0.1, float(configured_idle))
            except ValueError:
                pass
        args = [
            sys.executable,
            "-m",
            self.spec.module,
            "--serve",
            "--socket",
            str(self.socket_path),
            "--idle-seconds",
            str(idle_seconds),
            *self.spec.extra_args,
        ]
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.stderr_path.open("wb") as output:
                return self.popen(
                    args,
                    close_fds=True,
                    start_new_session=True,
                    # A daemon launched from nohup/launchd can inherit a closed fd 0. Its own
                    # RPC loop starts, but a macOS spawn worker then aborts while initializing
                    # Python's standard streams. Give every local service a valid inert stdin.
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
        except OSError as error:
            self._failure_reason = redact_local_service_text(error)
            return None

    def ensure_started(self) -> bool:
        if self._upgrade_required is not None:
            return False
        if self.recently_healthy():
            return True
        if self.healthy():
            self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
            return True
        if self._upgrade_required is not None:
            return False
        if self._terminal_failure:
            return False
        with self.lock:
            if self.healthy():
                self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
                return True
            if self._upgrade_required is not None:
                return False
            if self._terminal_failure:
                return False
            if self.clock() < self.next_start_at:
                return False
            with file_lock(self.lock_path, dir_mode=0o700):
                if self.healthy():
                    self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
                    return True
                if self._upgrade_required is not None:
                    return False
                self._retire_incompatible_service()
                self._remove_stale_record()
                process = self._spawn()
                if process is None:
                    self._mark_failure(self._failure_reason or f"{self.spec.name} spawn failed")
                    return False
                self.process = process
                deadline = self.clock() + LOCAL_SERVICE_START_TIMEOUT_SECONDS
                while self.clock() < deadline:
                    if self.healthy():
                        status = self._request("status", timeout=0.2)
                        self._write_record(self._record_from_status(status))
                        self.failures = 0
                        self.next_start_at = 0.0
                        self._start_exit_count = 0
                        self._last_exit_code = None
                        self._failure_reason = ""
                        self._terminal_failure = False
                        return True
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    self.sleep(0.03)
                exit_code = process.poll()
                reason = self._spawn_failure_reason(exit_code)
                self._mark_failure(
                    reason,
                    exit_code=exit_code,
                    exited_before_ready=exit_code is not None,
                )
        return False

    def acquire_lease(self) -> dict[str, Any]:
        if not self.ensure_started():
            if self._upgrade_required is not None:
                return {
                    "ok": False,
                    "error": f"{self.spec.name} client upgrade required",
                    "error_code": "upgrade_required",
                    **self._upgrade_required,
                }
            return {"ok": False, "error": f"{self.spec.name} unavailable"}
        response = self._request("lease", {"client_pid": os.getpid()}, timeout=0.25)
        if response.get("ok"):
            self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
        return response

    def release_lease(self, lease_id: str) -> dict[str, Any]:
        return self._request("release", {"lease_id": lease_id}, timeout=0.25)

    def status(self) -> dict[str, Any]:
        status = (
            {
                "ok": False,
                "error": f"{self.spec.name} client upgrade required",
                "error_code": "upgrade_required",
                "version": int(self._upgrade_required.get("required_protocol_version") or 0),
                "pid": int(self._upgrade_required.get("pid") or 0),
            }
            if self._upgrade_required is not None
            else self._request("status", timeout=0.25)
        )
        return {
            "service": self.spec.name,
            "socket": str(self.socket_path),
            "healthy": bool(status.get("ok")) and int(status.get("version") or 0) == self.spec.protocol_version,
            "failures": self.failures,
            "next_start_at": self.next_start_at,
            "record": self._read_record(),
            "status": status,
            "upgrade_required": dict(self._upgrade_required or {}),
            "failure_reason": self._failure_reason,
            "terminal_failure": self._terminal_failure,
            "start_exit_count": self._start_exit_count,
            "last_exit_code": self._last_exit_code,
        }

    def resources(self, pid: int) -> dict[str, float | int | None]:
        """Return best-effort worker CPU/RSS without restarting the subprocess.

        Linux reads /proc directly; macOS/BSD have no /proc, so an existing pid's
        cumulative CPU time and RSS come from a bounded `ps` read (not a worker
        restart). Without this branch every service reported `—` CPU/Memory and
        the Daemons load chart was empty on macOS.
        """
        if pid <= 0:
            return {"cpu_percent": None, "rss_bytes": None}
        reading = self._read_process_cpu_seconds_and_rss(pid)
        if reading is None:
            return {"cpu_percent": None, "rss_bytes": None}
        cpu_seconds, rss_bytes = reading
        now = self.clock()
        previous = self._last_resource_sample
        self._last_resource_sample = (now, cpu_seconds)
        cpu_percent: float | None = None
        if previous is not None and now > previous[0] and cpu_seconds >= previous[1]:
            cpu_percent = round(max(0.0, (cpu_seconds - previous[1]) / (now - previous[0]) * 100.0), 3)
        return {"cpu_percent": cpu_percent, "rss_bytes": rss_bytes}

    def resources_for_pids(self, parent_pid: int, child_pids: list[int] | tuple[int, ...]) -> dict[str, float | int | None]:
        """Return one CPU/RSS reading for a service broker and its verified direct workers.

        A process-pool worker does the costly work while its broker stays mostly idle.  Sampling
        only the broker made the System view materially underreport jobd.  Membership is part of
        the CPU baseline: a spawn/exit yields an honest unknown CPU for one sample rather than a
        false spike from mixing cumulative process times.
        """
        candidates = tuple(sorted({int(pid) for pid in (parent_pid, *child_pids) if int(pid) > 0}))
        if parent_pid <= 0 or not candidates:
            return {"cpu_percent": None, "rss_bytes": None, "process_count": 0}
        readings = self._read_process_group_cpu_seconds_and_rss(parent_pid, candidates)
        if not readings:
            return {"cpu_percent": None, "rss_bytes": None, "process_count": 0}
        members = tuple(sorted(readings))
        cpu_seconds = sum(reading[0] for reading in readings.values())
        rss_bytes = sum(reading[1] for reading in readings.values())
        now = self.clock()
        previous = self._last_resource_group_sample
        self._last_resource_group_sample = (members, now, cpu_seconds)
        cpu_percent: float | None = None
        if previous is not None and previous[0] == members and now > previous[1] and cpu_seconds >= previous[2]:
            cpu_percent = round(max(0.0, (cpu_seconds - previous[2]) / (now - previous[1]) * 100.0), 3)
        return {"cpu_percent": cpu_percent, "rss_bytes": rss_bytes, "process_count": len(members)}

    def _read_process_group_cpu_seconds_and_rss(self, parent_pid: int, pids: tuple[int, ...]) -> dict[int, tuple[float, int]]:
        """Read a parent and its direct children in one bounded platform-specific operation."""
        if platform.system() == "Linux":
            readings: dict[int, tuple[float, int]] = {}
            for pid in pids:
                try:
                    stat_fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
                    statm_fields = (Path("/proc") / str(pid) / "statm").read_text(encoding="utf-8").split()
                    if pid != parent_pid and int(stat_fields[3]) != parent_pid:
                        continue
                    cpu_seconds = (float(stat_fields[13]) + float(stat_fields[14])) / float(os.sysconf("SC_CLK_TCK"))
                    readings[pid] = (cpu_seconds, int(statm_fields[1]) * int(os.sysconf("SC_PAGE_SIZE")))
                except (IndexError, OSError, ValueError):
                    continue
            return readings if parent_pid in readings else {}
        try:
            completed = subprocess.run(
                ["ps", "-o", "pid=,ppid=,rss=,time=", "-p", ",".join(str(pid) for pid in pids)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return {}
        readings = {}
        for line in str(getattr(completed, "stdout", "") or "").splitlines():
            fields = line.split()
            if len(fields) != 4:
                continue
            try:
                pid, ppid, rss_kib = (int(fields[0]), int(fields[1]), int(fields[2]))
            except ValueError:
                continue
            cpu_seconds = parse_ps_cpu_seconds(fields[3])
            if pid not in pids or cpu_seconds is None or (pid != parent_pid and ppid != parent_pid):
                continue
            readings[pid] = (cpu_seconds, rss_kib * 1024)
        return readings if parent_pid in readings else {}

    def _read_process_cpu_seconds_and_rss(self, pid: int) -> tuple[float, int] | None:
        """Return (cumulative CPU seconds, RSS bytes) for an existing pid."""
        if platform.system() == "Linux":
            try:
                stat_fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
                statm_fields = (Path("/proc") / str(pid) / "statm").read_text(encoding="utf-8").split()
                cpu_seconds = (float(stat_fields[13]) + float(stat_fields[14])) / float(os.sysconf("SC_CLK_TCK"))
                rss_bytes = int(statm_fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
            except (IndexError, OSError, ValueError):
                return None
            return (cpu_seconds, rss_bytes)
        try:
            completed = subprocess.run(
                ["ps", "-o", "rss=,time=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        fields = str(getattr(completed, "stdout", "") or "").split()
        if len(fields) < 2:
            return None
        try:
            rss_bytes = int(fields[0]) * 1024
        except ValueError:
            return None
        cpu_seconds = parse_ps_cpu_seconds(fields[1])
        if cpu_seconds is None:
            return None
        return (cpu_seconds, rss_bytes)

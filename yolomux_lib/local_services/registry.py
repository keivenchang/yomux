"""Cross-port lifecycle owner for bounded local YOLOmux services."""

from __future__ import annotations

import json
import ctypes
import ctypes.util
import os
import platform
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from yolomux_lib.filesystem.io_ops import read_json_file
from typing import Callable
from time import monotonic as monotonic_clock
from time import sleep as sleep_clock
from time import time as wall_clock

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..background_owner import pid_is_alive
from ..common import STATE_DIR
from ..host_identity import HostIdentity
from ..host_identity import LocalProcessDiagnostic
from ..host_identity import LocalProcessReason
from ..host_identity import current_host_identity
from ..host_identity import is_current_local_process
from ..host_identity import process_start_identity
from ..host_identity import process_start_ticks
from ..host_identity import process_identity_snapshot
from ..host_identity import recorded_start_identity
from ..infra.worktree_writer import child_process_artifact_environment
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request
from .rpc import safe_socket_path
from .runtime import redact_local_service_text
from .protocol_types import Clock


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
LOCAL_SERVICE_SPAWN_GENERATION_ENV = "YOLOMUX_LOCAL_SERVICE_SPAWN_GENERATION"


_LAUNCH_CONTEXT: dict[str, int] = {}
_TRANSPORT_DIAGNOSTICS_LOCK = threading.Lock()
_TRANSPORT_TEARDOWNS_TOTAL = 0
_TRANSPORT_TEARDOWNS_BY_EXCEPTION: dict[str, int] = {}


def record_transport_teardown(exception_type: str = "unknown") -> None:
    """Count one failed local-RPC transport without treating normal closes as leaks."""

    normalized_type = str(exception_type or "unknown")[:64]
    global _TRANSPORT_TEARDOWNS_TOTAL
    with _TRANSPORT_DIAGNOSTICS_LOCK:
        _TRANSPORT_TEARDOWNS_TOTAL += 1
        _TRANSPORT_TEARDOWNS_BY_EXCEPTION[normalized_type] = (
            _TRANSPORT_TEARDOWNS_BY_EXCEPTION.get(normalized_type, 0) + 1
        )


def read_process_cpu_seconds_and_rss(pid: int) -> tuple[float, int] | None:
    """Return cumulative CPU seconds and RSS bytes for one local process."""

    if pid <= 0:
        return None
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
    fields = str(completed.stdout or "").split()
    if len(fields) < 2:
        return None
    try:
        rss_bytes = int(fields[0]) * 1024
    except ValueError:
        return None
    parsed_cpu_seconds = parse_ps_cpu_seconds(fields[1])
    if parsed_cpu_seconds is None:
        return None
    return (parsed_cpu_seconds, rss_bytes)


def transport_diagnostics() -> dict[str, Any]:
    with _TRANSPORT_DIAGNOSTICS_LOCK:
        record = {
            "teardowns_total": _TRANSPORT_TEARDOWNS_TOTAL,
            "teardowns_by_exception": dict(sorted(_TRANSPORT_TEARDOWNS_BY_EXCEPTION.items())),
        }
    return record


def set_local_service_launch_context(port: int) -> None:
    """Record which web port owns subsequently written service records.

    The ledger (`tracked_local_service_groups`) needs launch provenance so a
    watchdog can tell "spawned for this port" from "shared daemon another port
    still leases". One process serves one port, so module state is the owner.
    """
    _LAUNCH_CONTEXT["port"] = int(port)
    record_live_port_members(int(port))


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
    start_time: int = 0
    session_id: int = 0
    start_identity: str = ""
    spawn_generation: str = ""


@dataclass(frozen=True)
class SpawnProcessOwnership:
    """Durable process identities captured by the owner of a new service session."""

    leader_pid: int
    process_group: int
    session_id: int
    generation_marker: str
    member_identities: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class SpawnOwnershipProof:
    """One process-table snapshot used for both ownership refresh and authorization."""

    ownership: SpawnProcessOwnership
    group_exists: bool
    owned_member_identities: tuple[tuple[int, str], ...]
    # Occupants of the recorded group that are provably NOT this owner's, or
    # that are still present and cannot be proven either way. ``group_exists``
    # comes from one process-table snapshot while the generation marker is read
    # live from /proc, so an occupant that exits between the two reads leaves an
    # empty member set behind without anything being disproven. Recording the
    # occupants that are actually still there separates "this group is not mine"
    # from "the proof could not complete", which are different outcomes.
    disproven_occupants: tuple[tuple[int, str], ...] = ()


@dataclass
class StartupFailureState:
    """Mutable startup/backoff episode state owned as one lifecycle record."""

    failures: int = 0
    next_start_at: float = 0.0
    start_exit_count: int = 0
    last_exit_code: int | None = None
    failure_reason: str = ""
    record_refusal_reason: str = ""
    terminal_failure: bool = False

    def reset(self) -> None:
        self.failures = 0
        self.next_start_at = 0.0
        self.start_exit_count = 0
        self.last_exit_code = None
        self.failure_reason = ""
        self.record_refusal_reason = ""
        self.terminal_failure = False


@dataclass
class HealthProbeCache:
    """Short-lived proof that a recent local RPC reached the expected daemon."""

    healthy_until: float = 0.0

    def note_success(self, now: float) -> None:
        self.healthy_until = now + LOCAL_SERVICE_HEALTH_CACHE_SECONDS

    def invalidate(self) -> None:
        self.healthy_until = 0.0

    def is_recent(self, now: float) -> bool:
        return now < self.healthy_until


@dataclass
class ChildOwnershipState:
    """In-process ownership and reaping state for one service generation."""

    process: subprocess.Popen[Any] | None = None
    spawn_ownership: SpawnProcessOwnership | None = None
    adopted_reaper_pid: int = 0
    adopted_reaper_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    reaper_threads: set[threading.Thread] = field(default_factory=set, repr=False, compare=False)
    reaper_threads_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


class ProcessTableUnavailable(RuntimeError):
    """The launch preflight cannot establish process identity safely."""


def inherited_python_path(environ: dict[str, str]) -> str:
    """Keep service imports attached to the interpreter even when callers replace HOME."""

    candidates = [*str(environ.get("PYTHONPATH") or "").split(os.pathsep), *map(str, sys.path)]
    return os.pathsep.join(dict.fromkeys(path for path in candidates if path))


def process_start_time(pid: int) -> int:
    """Return the platform's numeric process-birth counter, or zero if unavailable."""
    return process_start_ticks(process_start_identity(pid)) or 0


def process_state(pid: int) -> str:
    """Return the native process state, preserving zombies as non-serving."""
    snapshot = process_identity_snapshot(pid)
    if snapshot is not None:
        return snapshot.state
    if platform.system() != "Darwin":
        return ""
    try:
        completed = subprocess.run(
            ("ps", "-o", "state=", "-p", str(int(pid))),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    state = str(completed.stdout or "").strip()
    return state[0] if completed.returncode == 0 and state else ""


def process_spawn_generation(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    darwin_environment_reader: Callable[[int], tuple[bytes, ...]] | None = None,
) -> str | None:
    """Return only the validated inherited spawn marker for one live process."""

    clean_pid = int(pid)
    if clean_pid <= 1:
        return None
    prefix = f"{LOCAL_SERVICE_SPAWN_GENERATION_ENV}=".encode("ascii")
    try:
        environ = (proc_root / str(clean_pid) / "environ").read_bytes()
    except OSError:
        entries = None
    else:
        entries = tuple(environ.split(b"\0"))
    if entries is None:
        reader = darwin_environment_reader
        if reader is None and platform.system() == "Darwin":
            reader = darwin_process_environment
        if reader is None:
            return None
        try:
            entries = tuple(reader(clean_pid))
        except (OSError, ValueError):
            return None
    matches = [item[len(prefix):] for item in entries if item.startswith(prefix)]
    if len(matches) != 1 or re.fullmatch(rb"[0-9a-f]{32}", matches[0]) is None:
        return None
    return matches[0].decode("ascii")


def parse_darwin_process_environment(data: bytes) -> tuple[bytes, ...]:
    """Split one raw KERN_PROCARGS2 buffer at its exact argv/environment boundary."""

    int_size = ctypes.sizeof(ctypes.c_int)
    if len(data) < int_size:
        raise ValueError("KERN_PROCARGS2 header is truncated")
    argc = int.from_bytes(data[:int_size], sys.byteorder, signed=True)
    if argc < 0:
        raise ValueError("KERN_PROCARGS2 argc is invalid")
    cursor = int_size
    executable_end = data.find(b"\0", cursor)
    if executable_end < cursor:
        raise ValueError("KERN_PROCARGS2 executable path is truncated")
    cursor = executable_end + 1
    while cursor < len(data) and data[cursor] == 0:
        cursor += 1
    for _index in range(argc):
        argument_end = data.find(b"\0", cursor)
        if argument_end < cursor:
            raise ValueError("KERN_PROCARGS2 argv is truncated")
        cursor = argument_end + 1
    while cursor < len(data) and data[cursor] == 0:
        cursor += 1
    if cursor == len(data):
        return ()
    if data[-1] != 0:
        raise ValueError("KERN_PROCARGS2 environment is truncated")
    return tuple(field for field in data[cursor:].split(b"\0") if field)


def darwin_process_environment(pid: int) -> tuple[bytes, ...]:
    """Read only the structured KERN_PROCARGS2 environment vector on Darwin."""

    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        raise OSError("Darwin libc is unavailable")
    libc = ctypes.CDLL(libc_path, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, int(pid))
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value <= ctypes.sizeof(ctypes.c_int):
        raise OSError(ctypes.get_errno(), "KERN_PROCARGS2 size query failed")
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "KERN_PROCARGS2 read failed")
    return parse_darwin_process_environment(bytes(buffer.raw[:size.value]))


def bounded_process_table(*, require_complete: bool = False) -> dict[int, ProcessTableEntry]:
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
            ["ps", "-axww", "-o", "pid=,ppid=,pgid=,sess=,time=,command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if require_complete:
            raise ProcessTableUnavailable("process_table_read_failed") from exc
        return {}
    if completed.returncode:
        if require_complete:
            raise ProcessTableUnavailable("process_table_read_failed")
        return {}
    table: dict[int, ProcessTableEntry] = {}
    for line in str(getattr(completed, "stdout", "") or "").splitlines():
        fields = line.split(None, 5)
        if len(fields) < 5:
            continue
        try:
            pid, ppid, pgid, session_id = int(fields[0]), int(fields[1]), int(fields[2]), int(fields[3])
        except ValueError:
            continue
        if platform.system() == "Darwin" and session_id <= 0:
            try:
                session_id = os.getsid(pid)
            except OSError:
                continue
        cpu_seconds = parse_ps_cpu_seconds(fields[4])
        if cpu_seconds is None:
            continue
        snapshot = process_identity_snapshot(pid)
        if snapshot is not None and snapshot.state == "Z":
            continue
        start_identity = snapshot.start_identity if snapshot is not None else ""
        start_time = process_start_ticks(start_identity) or 0
        table[pid] = ProcessTableEntry(
            ppid,
            pgid,
            cpu_seconds,
            fields[5] if len(fields) > 5 else "",
            start_time,
            session_id,
            start_identity,
        )
    if require_complete and not table:
        raise ProcessTableUnavailable("process_table_read_failed")
    return table


def bounded_preflight_process_table() -> dict[int, ProcessTableEntry]:
    """Require a complete process table before authorizing a new web launch."""

    return bounded_process_table(require_complete=True)


def process_record_diagnostic(
    record: dict[str, Any],
    *,
    host_identity: HostIdentity | None = None,
    table: dict[int, ProcessTableEntry] | None = None,
) -> LocalProcessDiagnostic:
    """Route persisted local-service identity through the one central fence."""

    if table is None:
        return is_current_local_process(
            record,
            host_identity=host_identity,
            start_identity_reader=process_start_identity,
            pid_probe=pid_is_alive,
        )

    def table_start_identity(pid: int) -> str | None:
        entry = table.get(pid)
        if entry is None:
            return None
        return process_table_start_identity(entry) or None

    return is_current_local_process(
        record,
        host_identity=host_identity,
        start_identity_reader=table_start_identity,
        pid_probe=lambda pid: pid in table,
    )


def process_fence_record(
    owner_record: dict[str, Any],
    *,
    pid: int | None = None,
    start_identity: str | None = None,
) -> dict[str, Any]:
    """Carry one owner's host/boot proof onto an exact tracked group member."""

    try:
        record_pid = int(owner_record.get("pid") or 0) if pid is None else int(pid)
    except (TypeError, ValueError):
        record_pid = 0
    recorded_start = str(owner_record.get("process_start_identity") or "")
    recorded_ticks = process_start_ticks(recorded_start)
    if recorded_ticks is None:
        try:
            recorded_ticks = int(owner_record.get("process_start_ticks") or 0)
        except (TypeError, ValueError):
            recorded_ticks = 0
    if start_identity is not None:
        recorded_start = str(start_identity)
        recorded_ticks = process_start_ticks(recorded_start) or 0
    return {
        "stable_host_id": owner_record.get("stable_host_id"),
        "boot_id": owner_record.get("boot_id"),
        "pid": record_pid,
        "process_start_identity": recorded_start,
        "process_start_ticks": recorded_ticks,
    }


def process_table_start_identity(entry: ProcessTableEntry) -> str:
    """Preserve one native process identity, with a legacy Linux-tick fallback."""

    return entry.start_identity or (f"proc:{entry.start_time}" if entry.start_time > 0 else "")


def service_record_identity_matches(record: dict[str, Any], table: dict[int, ProcessTableEntry]) -> bool:
    """A record's PID counts only when the live command names its exact socket."""
    pid = int(record.get("pid") or 0)
    socket_path = str(record.get("socket") or "")
    if not process_record_diagnostic(record, table=table).current or not socket_path:
        return False
    return f"--socket {socket_path}" in table[pid].command


def resolve_tracked_local_service_groups(
    service_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve centrally fenced groups plus typed rejected-record diagnostics.

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
    diagnostics: list[dict[str, Any]] = []
    try:
        record_paths = sorted(Path(service_dir).glob("*.service.json"))
    except OSError:
        return groups, diagnostics
    for record_path in record_paths:
        record = read_json_file(record_path, None)
        if record is None:
            continue
        if not isinstance(record, dict):
            continue
        diagnostic = process_record_diagnostic(record, table=table)
        if not diagnostic.current:
            diagnostics.append({
                "target": str(record.get("service") or record_path.stem),
                "pid": diagnostic.pid,
                "record_path": str(record_path),
                "diagnostic": diagnostic.as_dict(),
            })
            continue
        if not service_record_identity_matches(record, table):
            continue
        pid = int(record.get("pid") or 0)
        pgid = table[pid].pgid
        members = tuple(sorted(member for member, entry in table.items() if entry.pgid == pgid))
        member_records = {
            member: process_fence_record(record, pid=member, start_identity=process_table_start_identity(table[member]))
            for member in members
        }
        groups.append(
            {
                "service": str(record.get("service") or ""),
                "pid": pid,
                "pgid": pgid,
                "socket": str(record.get("socket") or ""),
                "launcher_pid": int(record.get("launcher_pid") or 0),
                "launcher_port": int(record.get("launcher_port") or 0),
                "protocol_version": int(record.get("protocol_version") or 0),
                "member_pids": members,
                "record_path": str(record_path),
                "process_record": process_fence_record(record),
                "member_records": member_records,
            }
        )
    return groups, diagnostics


def tracked_local_service_groups(
    service_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> list[dict[str, Any]]:
    """Enumerate only centrally fenced process groups this registry owns."""

    groups, _diagnostics = resolve_tracked_local_service_groups(service_dir, table)
    return groups


def untracked_local_service_processes(
    service_dir: Path,
    table: dict[int, ProcessTableEntry],
    tracked_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe local-service processes absent from every persisted group.

    This is diagnostics only: command discovery never grants signal authority.
    A candidate must name a YOLOmux module and a socket directly inside the
    caller's versioned service directory, which keeps the read bounded to the
    same process-table snapshot used for tracked groups.
    """

    tracked_pids = {
        pid
        for group in tracked_groups
        for pid in group.get("member_pids", ())
        if isinstance(pid, int) and not isinstance(pid, bool)
    }
    root = Path(service_dir).resolve(strict=False)
    rows: list[dict[str, Any]] = []
    for pid, entry in sorted(table.items()):
        if pid in tracked_pids:
            continue
        try:
            arguments = shlex.split(entry.command)
        except ValueError:
            continue
        try:
            module_index = arguments.index("-m") + 1
            module = arguments[module_index]
            socket_index = arguments.index("--socket") + 1
            socket_path = Path(arguments[socket_index])
        except (IndexError, ValueError):
            continue
        if not module.startswith("yolomux_lib.") or socket_path.parent.resolve(strict=False) != root:
            continue
        rows.append({
            "pid": pid,
            "ppid": entry.ppid,
            "pgid": entry.pgid,
            "socket": str(socket_path),
        })
    return rows


def stale_local_service_groups_of_dead_launcher(
    port: int,
    service_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> list[dict[str, Any]]:
    """Return exact service groups left by a dead launcher for one web port.

    Service records anchor the service leader to its exact Unix socket before
    returning any group. Unlike a dead web-server process group, that identity
    remains inspectable after the launcher has exited, so PID/PGID reuse cannot
    turn a stale record into authority to signal an unrelated process.
    """
    if table is None:
        table = bounded_process_table()
    return [
        group
        for group in tracked_local_service_groups(service_dir, table)
        if group["launcher_port"] == int(port)
        and group["launcher_pid"] > 0
        and group["launcher_pid"] not in table
    ]


def shutdown_owned_local_services(
    port: int,
    service_dir: Path,
    *,
    launcher_pid: int | None = None,
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_process_table,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = sleep_clock,
    grace_seconds: float = 0.5,
) -> dict[str, list[int]]:
    """Stop only sidecars whose ledger proves this live launcher created them."""
    owner_pid = int(os.getpid() if launcher_pid is None else launcher_pid)
    initial = table_reader()
    groups = [
        group
        for group in tracked_local_service_groups(service_dir, initial)
        if group["launcher_port"] == int(port) and group["launcher_pid"] == owner_pid
    ]
    members = {
        pid: (initial[pid].start_time, initial[pid].pgid)
        for group in groups
        for pid in group["member_pids"]
        if pid in initial
    }
    term_targets = [int(group["pid"]) for group in groups]
    signalled: list[int] = []
    for pid in term_targets:
        try:
            kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        signalled.append(pid)
    if signalled:
        sleep(max(0.0, float(grace_seconds)))
    survivors = table_reader()
    terminated: list[int] = []
    for pid, (start_time, pgid) in sorted(members.items()):
        entry = survivors.get(pid)
        if entry is None or entry.start_time != start_time or entry.pgid != pgid:
            continue
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
        terminated.append(pid)
    return {"signalled": signalled, "terminated": terminated}


def read_server_port_lease_record(port: int, state_dir: Path) -> dict[str, Any]:
    """Read the existing per-port ownership record written by acquire_server_port_lease."""
    identity = current_host_identity()
    root = Path(state_dir) / "server-leases"
    record = read_json_file(root / identity.stable_host_id / f"{int(port)}.lock", None)
    if isinstance(record, dict):
        return record
    # Read-only rollout compatibility: never write or unlink the legacy lease,
    # but a live v0.6.10 owner must still block a new launch.
    legacy = read_json_file(root / f"{int(port)}.lock", {})
    return legacy if isinstance(legacy, dict) else {}


def record_live_port_members(
    port: int,
    state_dir: Path = STATE_DIR,
    table: dict[int, ProcessTableEntry] | None = None,
) -> bool:
    """Snapshot exact owner-group members into the lease held by this process.

    The server owns the flock on this inode. Updating it in place preserves that
    lock; replacing it atomically would disconnect the lock from the path and
    allow a second launcher to proceed. A different process may read this
    record, but only the matching current owner can update it.
    """
    if table is None:
        table = bounded_process_table()
    record = read_server_port_lease_record(port, state_dir)
    own_pid = os.getpid()
    owner = table.get(own_pid)
    try:
        record_pid = int(record.get("pid") or 0)
        record_port = int(record.get("port") or 0)
        record_pgid = int(record.get("pgid") or 0)
    except (TypeError, ValueError):
        return False
    if (
        record_pid != own_pid
        or record_port != int(port)
        or owner is None
        or owner.pgid != record_pgid
        or owner.start_time <= 0
        or f"--port {int(port)} " not in owner.command + " "
    ):
        return False
    members = [
        {"pid": pid, "start_time": entry.start_time}
        for pid, entry in sorted(table.items())
        if entry.pgid == owner.pgid and entry.start_time > 0
    ]
    lease_path = Path(state_dir) / "server-leases" / current_host_identity().stable_host_id / f"{int(port)}.lock"
    try:
        with lease_path.open("r+", encoding="utf-8") as lease_file:
            lease_file.seek(0)
            lease_file.truncate()
            lease_file.write(json.dumps({**record, "members": members}, sort_keys=True, separators=(",", ":")) + "\n")
            lease_file.flush()
            os.fsync(lease_file.fileno())
    except OSError:
        return False
    return True


def resolve_tracked_port_process_group(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> tuple[dict[str, Any], LocalProcessDiagnostic | None]:
    """Resolve one centrally fenced web group and its typed owner diagnostic.

    Identity is the lease PID (written by the server itself under its flock)
    cross-checked against the live command naming this exact port; a recycled
    or unrelated PID fails that check and yields an empty result. Members are
    the PIDs sharing the web server's process group — local-service daemons are
    session leaders of their own groups, so they never appear here.
    """
    if table is None:
        table = bounded_process_table()
    record = read_server_port_lease_record(port, state_dir)
    if not record:
        return {}, None
    diagnostic = process_record_diagnostic(record, table=table)
    if not diagnostic.current:
        return {}, diagnostic
    pid = int(record.get("pid") or 0)
    if pid <= 0 or int(record.get("port") or 0) != int(port) or pid not in table:
        return {}, diagnostic
    if f"--port {int(port)} " not in table[pid].command + " ":
        return {}, diagnostic
    pgid = table[pid].pgid
    if pid == os.getpid():
        record_live_port_members(port, state_dir, table)
    members = tuple(sorted(member for member, entry in table.items() if entry.pgid == pgid))
    return {
        "port": int(port),
        "pid": pid,
        "pgid": pgid,
        "member_pids": members,
        "process_record": process_fence_record(record),
        "member_records": {
            member: process_fence_record(record, pid=member, start_identity=process_table_start_identity(table[member]))
            for member in members
        },
    }, diagnostic


def tracked_port_process_group(
    port: int,
    state_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
) -> dict[str, Any]:
    """Return only a centrally fenced web-server process group for one port."""

    group, _diagnostic = resolve_tracked_port_process_group(port, state_dir, table)
    return group


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
    # How long ensure_started waits for a freshly spawned daemon to answer a
    # healthy status AND publish its identity record before declaring the start
    # failed. The default suits lightweight daemons. A service whose on_start
    # legitimately opens and audits a large on-disk database (statsd reads its
    # whole retained snapshot before serving) needs a larger budget: with the
    # default 5.0s a 400MB+ stats database measured ~6.5s to first healthy
    # status, so every spawn timed out and statsd never confirmed startup —
    # a permanent crash-loop with empty stderr and an unreaped zombie child.
    start_timeout_seconds: float = LOCAL_SERVICE_START_TIMEOUT_SECONDS
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
        host_identity: HostIdentity | None = None,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        clock: Clock = monotonic_clock,
        sleep: Callable[[float], None] = sleep_clock,
    ):
        self.state_dir = Path(state_dir).expanduser()
        self._service_dir = Path(service_dir).expanduser() if service_dir is not None else None
        self.spec = spec
        self.host_identity = host_identity or current_host_identity()
        self._socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{spec.name}") if socket_path is not None else None
        self.popen = popen
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self._starts_sealed = threading.Event()
        self._child_ownership = ChildOwnershipState()
        # A generation that adopted a healthy daemon over its socket holds no Popen for it,
        # so nothing would wait() it when it idle-exits and it would linger as a zombie. This
        # names the one adopted child a reaper thread is currently parked on; guarded by its
        # own lock so arming from inside `self.lock` (the file-locked start path) cannot
        # deadlock against the reaper's own record retirement.
        self._startup_failure = StartupFailureState()
        self._health_probe_cache = HealthProbeCache()
        self._last_resource_sample: tuple[float, float] | None = None
        self._last_resource_group_sample: tuple[tuple[int, ...], float, float] | None = None
        self._upgrade_required: dict[str, Any] | None = None
        self._process_diagnostic: dict[str, Any] = {}

    @property
    def failures(self) -> int:
        return self._startup_failure.failures

    @failures.setter
    def failures(self, value: int) -> None:
        self._startup_failure.failures = value

    @property
    def next_start_at(self) -> float:
        return self._startup_failure.next_start_at

    @next_start_at.setter
    def next_start_at(self, value: float) -> None:
        self._startup_failure.next_start_at = value

    @property
    def _start_exit_count(self) -> int:
        return self._startup_failure.start_exit_count

    @_start_exit_count.setter
    def _start_exit_count(self, value: int) -> None:
        self._startup_failure.start_exit_count = value

    @property
    def _last_exit_code(self) -> int | None:
        return self._startup_failure.last_exit_code

    @_last_exit_code.setter
    def _last_exit_code(self, value: int | None) -> None:
        self._startup_failure.last_exit_code = value

    @property
    def _failure_reason(self) -> str:
        return self._startup_failure.failure_reason

    @_failure_reason.setter
    def _failure_reason(self, value: str) -> None:
        self._startup_failure.failure_reason = value

    @property
    def _record_refusal_reason(self) -> str:
        return self._startup_failure.record_refusal_reason

    @_record_refusal_reason.setter
    def _record_refusal_reason(self, value: str) -> None:
        self._startup_failure.record_refusal_reason = value

    @property
    def _terminal_failure(self) -> bool:
        return self._startup_failure.terminal_failure

    @_terminal_failure.setter
    def _terminal_failure(self, value: bool) -> None:
        self._startup_failure.terminal_failure = value

    @property
    def _healthy_until(self) -> float:
        return self._health_probe_cache.healthy_until

    @_healthy_until.setter
    def _healthy_until(self, value: float) -> None:
        self._health_probe_cache.healthy_until = value

    @property
    def process(self) -> subprocess.Popen[Any] | None:
        return self._child_ownership.process

    @process.setter
    def process(self, value: subprocess.Popen[Any] | None) -> None:
        self._child_ownership.process = value

    @property
    def spawn_ownership(self) -> SpawnProcessOwnership | None:
        return self._child_ownership.spawn_ownership

    @spawn_ownership.setter
    def spawn_ownership(self, value: SpawnProcessOwnership | None) -> None:
        self._child_ownership.spawn_ownership = value

    @property
    def _adopted_reaper_pid(self) -> int:
        return self._child_ownership.adopted_reaper_pid

    @_adopted_reaper_pid.setter
    def _adopted_reaper_pid(self, value: int) -> None:
        self._child_ownership.adopted_reaper_pid = value

    @property
    def _adopted_reaper_lock(self) -> threading.Lock:
        return self._child_ownership.adopted_reaper_lock

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
        value = read_json_file(self.record_path, {})
        return value if isinstance(value, dict) else {}

    def _write_record(self, record: dict[str, Any]) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.record_path, json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)
        self._process_diagnostic = {}

    def _record_process_diagnostic(self, record: dict[str, Any]) -> LocalProcessDiagnostic:
        diagnostic = process_record_diagnostic(record, host_identity=self.host_identity)
        self._process_diagnostic = diagnostic.as_dict()
        return diagnostic

    def _dead_legacy_record_has_inert_socket_artifact(
        self,
        record: dict[str, Any],
        diagnostic: LocalProcessDiagnostic,
    ) -> bool:
        """Recognize the one pre-identity record shape that is safe to discard."""

        if diagnostic.reason is not LocalProcessReason.MISSING_HOST_IDENTITY:
            return False
        if any(
            field in record
            for field in ("stable_host_id", "boot_id", "process_start_identity", "process_start_ticks")
        ):
            return False
        try:
            record_version = int(record.get("version") or 0)
        except (TypeError, ValueError):
            return False
        recorded_socket = str(record.get("socket") or "")
        if record_version >= LOCAL_SERVICE_REGISTRY_VERSION or (
            recorded_socket and recorded_socket != str(self.socket_path)
        ):
            return False
        if str(record.get("service") or "") != self.spec.name or diagnostic.pid <= 1:
            return False
        if pid_is_alive(diagnostic.pid):
            return False
        try:
            socket_mode = self.socket_path.lstat().st_mode
        except OSError:
            return False
        # A regular file cannot be a listening Unix socket. Removing only the
        # dead legacy record grants no authority to signal a process; the new
        # service remains responsible for replacing this inert path under its lock.
        return stat.S_ISREG(socket_mode)

    def _remove_stale_record(self) -> bool | None:
        record = self._read_record()
        if not record:
            return None
        diagnostic = self._record_process_diagnostic(record)
        # `may_remove_unidentifiable_record` recovers installs already poisoned by
        # the 0.7.0 publication defect: a record whose PID is 0 or 1 names no
        # process, so discarding the file is record-only cleanup -- no signal, no
        # adoption, no socket removal -- and it is the only way a same-host,
        # same-boot `invalid_pid` record can ever stop blocking startup.
        if (
            not diagnostic.may_remove_stale_record
            and not diagnostic.may_remove_unidentifiable_record
            and not self._dead_legacy_record_has_inert_socket_artifact(record, diagnostic)
        ):
            return False
        try:
            self.record_path.unlink()
        except FileNotFoundError:
            return True
        return True

    def _can_reclaim_newer_service(self, service_pid: int) -> bool:
        """Whether a newer daemon is provably left behind by a dead web owner.

        A version fence normally means another live web server may still own the
        shared daemon, so it remains terminal.  A guarded web restart is the
        narrow exception: the persisted record still identifies this exact
        socket/process group, while its launcher has exited.  Only that ledger
        proof permits retiring the daemon and starting the current one.
        """
        if service_pid <= 0:
            return False
        record = self._read_record()
        launcher_pid = int(record.get("launcher_pid") or 0)
        if int(record.get("pid") or 0) != service_pid or launcher_pid <= 0:
            return False
        if launcher_pid == os.getpid() or pid_is_alive(launcher_pid):
            return False
        return any(
            group["service"] == self.spec.name
            and group["pid"] == service_pid
            and group["socket"] == str(self.socket_path)
            for group in tracked_local_service_groups(self.service_dir)
        )

    def _retire_incompatible_service(self) -> bool:
        """Stop the service currently bound to our socket after a protocol bump or
        code-revision drift. Same-protocol drift matters: without it the stale daemon
        keeps the socket, the fresh spawn cannot bind, and ensure_started fails forever."""
        response = self._request("ping", timeout=0.15)
        service_pid = int(response.get("pid") or 0)
        service_version = int(response.get("version") or response.get("required_protocol_version") or 0)
        newer_reclaimable = (
            service_version > self.spec.protocol_version
            and self._can_reclaim_newer_service(service_pid)
        )
        if service_version > self.spec.protocol_version and not newer_reclaimable:
            self._upgrade_required = {
                "required_protocol_version": service_version,
                "current_protocol_version": self.spec.protocol_version,
                "pid": service_pid,
            }
            return False
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
                or response.get("error") == "upgrade_required"
            )
        )
        record = self._read_record()
        record_pid = int(record.get("pid") or 0)
        diagnostic = self._record_process_diagnostic(record)
        shutdown_protocol_version: int | None = None
        if older_upgrade and not service_pid:
            recorded_protocol_value = record.get("protocol_version")
            if (
                isinstance(recorded_protocol_value, bool)
                or not isinstance(recorded_protocol_value, int)
                or recorded_protocol_value <= 0
            ):
                return False
            recorded_protocol_version = recorded_protocol_value
            if (
                record_pid <= 0
                or recorded_protocol_version != service_version
                or not diagnostic.current
            ):
                return False
            legacy_status = self._request("status", timeout=0.2, protocol_version=recorded_protocol_version)
            if (
                legacy_status.get("ok") is not True
                or int(legacy_status.get("pid") or 0) != record_pid
                or int(legacy_status.get("version") or 0) != recorded_protocol_version
            ):
                return False
            service_pid = record_pid
            shutdown_protocol_version = recorded_protocol_version
        if (not response.get("ok") and not older_upgrade and not newer_reclaimable) or not service_pid or compatible:
            return True
        if record_pid != service_pid or not diagnostic.current:
            return False
        if shutdown_protocol_version is None:
            self._request("shutdown", timeout=0.25)
        else:
            self._request("shutdown", timeout=0.25, protocol_version=shutdown_protocol_version)
        deadline = self.clock() + 0.5
        while pid_is_alive(service_pid) and self.clock() < deadline:
            self.sleep(0.03)
        if pid_is_alive(service_pid):
            diagnostic = self._record_process_diagnostic(record)
            if not diagnostic.current:
                return False
            try:
                os.kill(service_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                return False
            deadline = self.clock() + 0.5
            while pid_is_alive(service_pid) and self.clock() < deadline:
                self.sleep(0.03)
        if pid_is_alive(service_pid) or self._remove_stale_record() is not True:
            return False
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            return True
        return True

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.2,
        protocol_version: int | None = None,
    ) -> dict[str, Any]:
        try:
            request_protocol_version = self.spec.protocol_version if protocol_version is None else protocol_version
            request_payload = {"action": method, "protocol_version": request_protocol_version, **(payload or {})}
            envelope = new_envelope(self.spec.name, method, request_payload, timeout_seconds=timeout)
            response, _binary = request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError) as exc:
            self.note_rpc_failure(type(exc).__name__)
            return {}
        return response if isinstance(response, dict) else {}

    def healthy(self) -> bool:
        response = self._request("ping", timeout=0.15)
        service_version = int(response.get("version") or response.get("required_protocol_version") or 0)
        if service_version > self.spec.protocol_version:
            if self._can_reclaim_newer_service(int(response.get("pid") or 0)):
                # _CurrentRegistry observes the wire fence before this health
                # check.  Clear that provisional fence so ensure_started can
                # execute the ledger-proven stale-owner recovery below.
                self._upgrade_required = None
                self.invalidate_rpc_health()
                return False
            self._upgrade_required = {
                **response,
                "required_protocol_version": service_version,
                "current_protocol_version": self.spec.protocol_version,
                "pid": int(response.get("pid") or 0),
            }
            self.invalidate_rpc_health()
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
            self.invalidate_rpc_health()
        return healthy

    def note_rpc_success(self) -> None:
        """Cache recent transport health to avoid ping/status fan-out per action."""
        self._health_probe_cache.note_success(self.clock())

    def note_rpc_failure(self, exception_type: str = "unknown") -> None:
        record_transport_teardown(exception_type)
        self.invalidate_rpc_health()

    def invalidate_rpc_health(self) -> None:
        self._health_probe_cache.invalidate()

    def recently_healthy(self) -> bool:
        return self._health_probe_cache.is_recent(self.clock())

    def _record_from_status(self, status: dict[str, Any]) -> dict[str, Any]:
        pid = int(status.get("pid") or 0)
        worker_pids = status.get("worker_pids")
        start_identity = str(status.get("process_start_identity") or "")
        record = {
            **self.host_identity.process_record_fields(pid=pid, start_identity=start_identity),
            "version": LOCAL_SERVICE_REGISTRY_VERSION,
            "service": self.spec.name,
            "module": self.spec.module,
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
        source_epoch = status.get("source_epoch")
        if isinstance(source_epoch, str) and source_epoch:
            record["source_epoch"] = source_epoch[:160]
        return record

    def _record_publication_refusal(self, status: dict[str, Any], record: dict[str, Any]) -> str:
        """Name why a status response may not become the durable identity record."""

        if status.get("ok") is not True:
            return "status_not_ok"
        claimed_service = status.get("service")
        # Only a string under `service` is a service-name claim: four of the six
        # daemons omit the key entirely and statsd reports a nested diagnostics
        # object there.  A string that disagrees proves a cross-wired socket.
        if isinstance(claimed_service, str) and claimed_service.strip() and claimed_service.strip() != self.spec.name:
            return "service_name_mismatch"
        if int(record.get("protocol_version") or 0) != self.spec.protocol_version:
            return "protocol_version_mismatch"
        # A PID of 0 or 1 cannot name a service process, and a record without a
        # process-birth identity cannot be fenced.  Both are permanently
        # unremovable on this host and boot, so publishing either one bricks the
        # service instead of describing it.
        if int(record.get("pid") or 0) <= 1:
            return "invalid_pid"
        if not recorded_start_identity(record):
            return "missing_process_start_identity"
        return ""

    def _validated_status_identity(
        self,
        status: dict[str, Any],
        *,
        require_carried_identity: bool = False,
    ) -> tuple[dict[str, Any], str]:
        """Build and validate one status identity, requiring wire proof for a final probe."""

        try:
            record = self._record_from_status(status)
            if not recorded_start_identity(record) and not require_carried_identity:
                pid = int(record.get("pid") or 0)
                start_identity = (
                    self.host_identity.process_start_identity
                    if pid == self.host_identity.pid
                    else process_start_identity(pid) or ""
                )
                record.update(self.host_identity.process_record_fields(pid=pid, start_identity=start_identity))
            return record, self._record_publication_refusal(status, record)
        except (TypeError, ValueError) as error:
            return {}, f"malformed_status ({type(error).__name__})"

    def _publish_record(self, status: dict[str, Any], *, require_carried_identity: bool = False) -> bool:
        """Publish the durable identity record, and only from a proven status.

        This is the one place a service record is written.  0.7.0 published
        whatever `_record_from_status` produced straight after a successful
        ping, so a single dropped status RPC wrote a record carrying pid 0 --
        an `invalid_pid` identity no later start on the same host and boot
        could clean -- while still telling the caller the service had started.

        A status that cannot prove `ok`, this exact service and protocol
        version, a PID that can name a process, and a usable process-start
        identity is not a publishable identity.  Refuse it, drop cached health
        so the next attempt re-probes, and let the caller continue through
        bounded startup and retry.  Never report success from here.
        """
        record, refusal = self._validated_status_identity(status, require_carried_identity=require_carried_identity)
        if refusal:
            self._record_refusal_reason = redact_local_service_text(
                f"{self.spec.name} service record refused before publication "
                f"(reason={refusal}, status_pid={status.get('pid')!r})"
            )
            self._failure_reason = self._record_refusal_reason
            self.invalidate_rpc_health()
            return False
        # A successfully published, identity-proven record means the daemon is healthy and current,
        # so ANY latched non-spawn reason is now stale -- not only a prior publication refusal but
        # also a `_record_blocked_start` guard message (e.g. "start blocked by remove_stale_record
        # (reason=current_local_process)"): that guard fires when the web process tries to replace a
        # daemon that is in fact the healthy current one, and once this same registry adopts and
        # publishes that daemon the reason must not keep describing it as an Issue. Spawn-failure
        # latches (`_terminal_failure`, exit code, backoff) are owned by `_accept_started_child` and
        # are deliberately left untouched here.
        self._failure_reason = ""
        self._record_refusal_reason = ""
        self._write_record(record)
        return True

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

    def _record_blocked_start(self, stage: str) -> None:
        """Name the guard that refused a start so an absent daemon stays diagnosable.

        This is not a spawn failure: no child ran, so the exit code, terminal
        latch, and backoff schedule must stay untouched. Only the reported
        reason changes, which is what ``status()`` and ``failure_response()``
        expose to the caller and the server log.
        """
        recorded_pid = int(self._read_record().get("pid") or 0)
        diagnostic_reason = str(self._process_diagnostic.get("reason") or "unknown")
        self._failure_reason = redact_local_service_text(
            f"{self.spec.name} start blocked by {stage} "
            f"(record_pid={recorded_pid}, reason={diagnostic_reason})"
        )

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
        response = {
            "ok": False,
            "status": "unavailable",
            "reason": reason,
            "terminal": self._terminal_failure,
            "exit_code": self._last_exit_code,
        }
        if self._process_diagnostic:
            response.update({
                "reason_code": self._process_diagnostic["reason"],
                "process_diagnostic": dict(self._process_diagnostic),
            })
        return response

    def retry(self) -> None:
        """Clear a latched startup or version-fence failure for one retry."""
        with self.lock:
            self._upgrade_required = None
            self._startup_failure.reset()
            self._process_diagnostic = {}

    def starts_allowed(self) -> bool:
        """Return whether this process may still create a service generation."""

        return not self._starts_sealed.is_set()

    def seal_starts(self) -> None:
        """Fence replacement generations before fixture-owned children retire."""

        with self.lock:
            self._starts_sealed.set()

    def _spawn(self) -> subprocess.Popen[Any] | None:
        idle_seconds = self.spec.idle_seconds
        configured_idle = os.environ.get(LOCAL_SERVICE_IDLE_SECONDS_ENV)
        if configured_idle:
            try:
                idle_seconds = max(0.1, float(configured_idle))
            except ValueError:
                pass
        generation_marker = uuid.uuid4().hex
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
        self.spawn_ownership = None
        # A local service is shared per user, so this inherited environment belongs to whichever
        # server happened to launch it first and is NOT authority for any other server's request.
        # Filesystem access policy in particular travels on the job descriptor
        # (`filesystem.paths.FilesystemAccessPolicy`), never through `YOLOMUX_FS_ROOTS` here.
        spawn_environ = child_process_artifact_environment(Path(__file__).resolve().parents[2], environ=os.environ)
        spawn_environ[LOCAL_SERVICE_SPAWN_GENERATION_ENV] = generation_marker
        spawn_environ["PYTHONPATH"] = inherited_python_path(spawn_environ)
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.stderr_path.open("wb") as output:
                process = self.popen(
                    args,
                    close_fds=True,
                    env=spawn_environ,
                    start_new_session=True,
                    # A daemon launched from nohup/launchd can inherit a closed fd 0. Its own
                    # RPC loop starts, but a macOS spawn worker then aborts while initializing
                    # Python's standard streams. Give every local service a valid inert stdin.
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            if not hasattr(process, "pid"):
                return process
            leader_pid = int(process.pid)
            leader_start_identity = process_start_identity(leader_pid)
            try:
                process_group = os.getpgid(leader_pid)
                session_id = os.getsid(leader_pid)
            except OSError as error:
                process.terminate()
                process.wait(timeout=1)
                self._failure_reason = f"{self.spec.name} spawn ownership unavailable: {redact_local_service_text(error)}"
                return None
            if leader_pid <= 1 or not leader_start_identity or process_group != leader_pid or session_id != leader_pid:
                process.terminate()
                process.wait(timeout=1)
                self._failure_reason = f"{self.spec.name} spawn ownership unavailable"
                return None
            self.spawn_ownership = SpawnProcessOwnership(
                leader_pid=leader_pid,
                process_group=process_group,
                session_id=session_id,
                generation_marker=generation_marker,
                member_identities=((leader_pid, leader_start_identity),),
            )
            return process
        except OSError as error:
            self._failure_reason = redact_local_service_text(error)
            return None

    def refresh_spawn_ownership_proof(self) -> SpawnOwnershipProof | None:
        """Refresh and prove one spawned session from a single process-table snapshot."""

        ownership = self.spawn_ownership
        if ownership is None:
            return None
        table = bounded_process_table()
        group_exists = any(entry.pgid == ownership.process_group for entry in table.values())
        retained_identities = dict(ownership.member_identities)
        leader = table.get(ownership.leader_pid)
        if leader is not None and (
            (leader.start_identity or process_start_identity(ownership.leader_pid)) != retained_identities.get(ownership.leader_pid)
            or leader.pgid != ownership.process_group
            or leader.session_id != ownership.session_id
            or process_spawn_generation(ownership.leader_pid) != ownership.generation_marker
        ):
            leader_identity = leader.start_identity or process_start_identity(ownership.leader_pid)
            leader_generation = process_spawn_generation(ownership.leader_pid)
            # A leader that no longer answers either live read has exited; the
            # snapshot simply predates its exit. That is an incomplete proof,
            # not a disproven one, and must not be reported as a foreign group.
            leader_vanished = not leader_identity and leader_generation is None
            return SpawnOwnershipProof(
                ownership,
                group_exists,
                (),
                () if leader_vanished else ((ownership.leader_pid, leader_identity),),
            )
        members = []
        disproven = []
        for pid, entry in table.items():
            if entry.pgid != ownership.process_group:
                continue
            generation = process_spawn_generation(pid)
            start_identity = entry.start_identity or process_start_identity(pid)
            if generation != ownership.generation_marker:
                # Classify the occupant instead of discarding it. A readable
                # generation that differs is proof the group is not this
                # owner's. An unreadable generation means only that the read
                # did not complete: if the pid is still there it stays
                # unproven and must not be signalled, but if it has already
                # exited there is nothing left in the group to signal at all.
                # Presence is re-read live rather than taken from the snapshot,
                # because the snapshot's identity is exactly the stale value
                # that made an exited occupant look like a foreign one.
                live_identity = process_start_identity(pid)
                if generation is not None or live_identity:
                    disproven.append((pid, entry.start_identity or live_identity))
                continue
            if entry.session_id != ownership.session_id:
                disproven.append((pid, start_identity))
                continue
            if not start_identity:
                continue
            retained_identity = retained_identities.get(pid)
            if retained_identity is not None and retained_identity != start_identity:
                disproven.append((pid, start_identity))
                continue
            members.append((pid, start_identity))
        members_tuple = tuple(sorted(members))
        disproven_tuple = tuple(sorted(disproven))
        if members_tuple:
            ownership = SpawnProcessOwnership(
                leader_pid=ownership.leader_pid,
                process_group=ownership.process_group,
                session_id=ownership.session_id,
                generation_marker=ownership.generation_marker,
                member_identities=members_tuple,
            )
            self.spawn_ownership = ownership
        return SpawnOwnershipProof(ownership, group_exists, members_tuple, disproven_tuple)

    def refresh_spawn_ownership(self) -> SpawnProcessOwnership | None:
        """Retain members while the leader matches or its exact spawned session survives."""

        proof = self.refresh_spawn_ownership_proof()
        return proof.ownership if proof is not None else None

    def _retire_record_naming_pid(self, pid: int) -> None:
        """Remove the durable record only while it still names this exact reaped pid.

        The one owner of that retirement, shared by the fresh-spawn reaper and the adopted
        reaper. `_remove_stale_record` re-reads and re-fences the record under the file lock,
        so a record that has already been replaced by a newer generation, or that names a
        process this host still proves live, is left untouched.
        """
        record = self._read_record()
        if int(record.get("pid") or 0) != pid:
            return
        with file_lock(self.lock_path, dir_mode=0o700):
            current = self._read_record()
            if int(current.get("pid") or 0) == pid:
                self._remove_stale_record()

    def _reap_exited_child(self, process: subprocess.Popen[Any]) -> None:
        """Wait for one child and retire only the record that names that exact child."""
        try:
            exit_code = process.wait()
        except OSError:
            return
        with self.lock:
            if self.process is not process:
                return
            self.process = None
            self._last_exit_code = exit_code
            self.invalidate_rpc_health()
            self._retire_record_naming_pid(process.pid)

    def _start_child_reaper(self, process: subprocess.Popen[Any]) -> None:
        """Reap an idle service at exit instead of deferring it to its next caller."""
        thread = threading.Thread(
            target=self._reap_exited_child,
            args=(process,),
            name=f"{self.spec.name}-reaper",
            daemon=True,
        )
        with self._child_ownership.reaper_threads_lock:
            self._child_ownership.reaper_threads.add(thread)
        thread.start()

    def _reap_adopted_child(self, pid: int) -> None:
        """Wait for one adopted demand daemon to exit and reap it, then retire its record.

        The web process is this child's parent, so `os.waitpid(pid, 0)` parks until the child
        dies and reaps it in place instead of leaving a zombie that reads as alive. It targets
        one specific pid this generation holds no Popen for, so it can never race the fresh-spawn
        path's `Popen.wait()`. A child already reaped elsewhere -- CPython's own dropped-Popen
        cleanup, or a superseding spawn -- raises `ChildProcessError`; that is a recorded outcome,
        not a failure, and the retirement below is fenced by `_remove_stale_record`, which will
        not remove a live or foreign process.
        """
        try:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            with self.lock:
                # A fresh spawn in this generation now owns reaping through its own Popen; the
                # record it published names a different pid and is not this thread's to retire.
                if self.process is None:
                    self._retire_record_naming_pid(pid)
        except OSError:
            # Supervisor boundary for this one reaping unit: end the thread rather than let an
            # unexpected wait error crash the daemon lifecycle it was arming.
            return
        finally:
            with self._adopted_reaper_lock:
                if self._adopted_reaper_pid == pid:
                    self._adopted_reaper_pid = 0

    def _arm_adopted_reaper(self) -> None:
        """Reap a healthy daemon this generation adopted but holds no Popen for.

        The fresh-spawn path arms `_start_child_reaper` against its own Popen. The healthy-socket
        early returns in `ensure_started` adopt a daemon an earlier generation spawned, whose
        Popen was dropped: nothing will `wait()` it, so it becomes an unreaped zombie the moment
        it idle-exits and then reads as alive to every liveness check. Arm a reaper for the
        recorded pid so the web process reaps its own adopted child -- only while this generation
        holds no Popen of its own, because the spawn path owns reaping then.
        """
        if self.process is not None:
            return
        record = self._read_record()
        pid = int(record.get("pid") or 0)
        if pid <= 1:
            return
        if not process_record_diagnostic(record, host_identity=self.host_identity).current:
            return
        if self.process is not None:
            return
        with self._adopted_reaper_lock:
            if self._adopted_reaper_pid == pid:
                return
            self._adopted_reaper_pid = pid
        thread = threading.Thread(
            target=self._reap_adopted_child,
            args=(pid,),
            name=f"{self.spec.name}-adopted-reaper",
            daemon=True,
        )
        with self._child_ownership.reaper_threads_lock:
            self._child_ownership.reaper_threads.add(thread)
        thread.start()

    def settle_reaper_threads(self, timeout: float = 3.0) -> None:
        """Join every reaper this registry started after replacement starts are sealed."""

        deadline = monotonic_clock() + max(0.0, float(timeout))
        while True:
            with self._child_ownership.reaper_threads_lock:
                threads = tuple(self._child_ownership.reaper_threads)
            if not threads:
                return
            for thread in threads:
                if thread is threading.current_thread():
                    continue
                thread.join(timeout=max(0.0, deadline - monotonic_clock()))
            with self._child_ownership.reaper_threads_lock:
                self._child_ownership.reaper_threads.difference_update(
                    thread for thread in threads if not thread.is_alive()
                )
                surviving = tuple(self._child_ownership.reaper_threads)
            if not surviving:
                return
            if monotonic_clock() >= deadline:
                names = tuple(sorted(thread.name for thread in surviving))
                raise RuntimeError(f"{self.spec.name} reaper threads did not settle: {names}")

    def ensure_started(self) -> bool:
        if not self.starts_allowed():
            return False
        # Polling an exited child is the parent-side reap operation. Do it before
        # the healthy-cache shortcut so a quiet service cannot remain defunct.
        if self.process is not None and self.process.poll() is not None:
            self.process = None
        if self._upgrade_required is not None:
            return False
        if self.recently_healthy():
            self._arm_adopted_reaper()
            return True
        if self._terminal_failure:
            return False
        # A healthy ping is not a started service until its identity record is
        # published. When the follow-up status is lost, fall through to bounded
        # startup and retry instead of reporting a success nothing can prove.
        if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
            self._arm_adopted_reaper()
            return True
        if self._upgrade_required is not None:
            return False
        if self._terminal_failure:
            return False
        with self.lock:
            if not self.starts_allowed():
                return False
            if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
                self._arm_adopted_reaper()
                return True
            if self._upgrade_required is not None:
                return False
            if self._terminal_failure:
                return False
            if self.clock() < self.next_start_at:
                return False
            with file_lock(self.lock_path, dir_mode=0o700):
                if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
                    self._arm_adopted_reaper()
                    return True
                if self._upgrade_required is not None:
                    return False
                # A blocked start is a failure the caller must be able to read.
                # Returning False here without recording a reason leaves the
                # daemon absent with an empty stderr log, no service record, and
                # no diagnosable state: the exact signature that makes a missing
                # local service unexplainable from its service directory alone.
                if not self._retire_incompatible_service():
                    self._record_blocked_start("retire_incompatible_service")
                    return False
                if self._remove_stale_record() is False:
                    self._record_blocked_start("remove_stale_record")
                    return False
                process = self._spawn()
                if process is None:
                    self._mark_failure(self._failure_reason or f"{self.spec.name} spawn failed")
                    return False
                self.process = process
                deadline = self.clock() + self.spec.start_timeout_seconds
                while self.clock() < deadline:
                    if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
                        return self._accept_started_child(process)
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    self.sleep(0.03)
                exit_code = process.poll()
                # W7 clause 4: ONE final identity-bearing startup probe -- a single `status`
                # request, no `ping` first -- decides a child still alive past the deadline.
                # The one status response is itself the identity used for protocol/identity/
                # readiness validation, and classifies into exactly three outcomes:
                #   late-valid     -- ok, this service and protocol, and the pid/start-identity
                #                     it carries match the exact leader we spawned -> publish the
                #                     record, accept, NO Error, and NO second generation.
                #   wrong-identity -- otherwise-healthy but the WRONG pid/start-identity: a
                #                     reused-pid imposter (our pid is alive but now carries a
                #                     different start-identity) or a peer answering our socket
                #                     with a valid status naming a process we did not spawn ->
                #                     one terminal-episode Error, no respawn while backoff
                #                     ownership forbids re-entry.
                #   transient      -- not-ok or dropped while our own leader is still alive and
                #                     ours -> hold THIS child for the caller's bounded retry,
                #                     NO Error, no respawn.
                # A child that exited (`exit_code is not None`), or a spawn that never captured
                # ownership, keeps the terminal path below unchanged.
                if exit_code is None and self.spawn_ownership is not None:
                    status = self._request("status", timeout=0.2)
                    if (
                        self._spawned_leader_identity_matches()
                        and self._status_matches_spawned_leader(status)
                        and self._publish_record(status, require_carried_identity=True)
                    ):
                        return self._accept_started_child(process)
                    if self._status_is_wrong_identity(status):
                        self._mark_failure(
                            self._wrong_identity_reason(status),
                            exit_code=exit_code,
                            exited_before_ready=True,
                        )
                        self._terminal_failure = True
                        return False
                    self.next_start_at = self.clock() + LOCAL_SERVICE_BACKOFF_SECONDS
                    return False
                # A child that answered ping but never published a provable
                # identity failed for that reason, not for the generic
                # "did not become ready" one; keep the real cause.
                reason = self._record_refusal_reason or self._spawn_failure_reason(exit_code)
                self._mark_failure(
                    reason,
                    exit_code=exit_code,
                    exited_before_ready=exit_code is not None,
                )
        return False

    def _accept_started_child(self, process: subprocess.Popen[Any]) -> bool:
        """Record one proven-healthy startup and clear every failure latch. One owner."""

        self._startup_failure.reset()
        self.refresh_spawn_ownership()
        self._start_child_reaper(process)
        return True

    def _spawned_leader_identity_matches(self) -> bool:
        """Prove the live pid we spawned is still that exact process, not a reused-pid imposter.

        Reuses the spawn-ownership identity proof captured at spawn: the leader's start-identity.
        A pid that is alive but now carries a DIFFERENT start-identity has been recycled to an
        unrelated process, which is the imposter case; a missing or empty captured identity
        cannot prove ownership and is treated as unproven.
        """

        ownership = self.spawn_ownership
        if ownership is None:
            return False
        leader_pid = ownership.leader_pid
        expected = dict(ownership.member_identities).get(leader_pid, "")
        if not expected or not pid_is_alive(leader_pid):
            return False
        return process_start_identity(leader_pid) == expected

    def _status_matches_spawned_leader(self, status: dict[str, Any]) -> bool:
        """Prove the identity carried IN the status response is the exact leader we spawned.

        `_publish_record`/`_record_publication_refusal` prove a status is a VALID publishable
        identity -- ok, this service and protocol, a usable pid and start-identity -- but not
        that the pid it names is the process THIS generation spawned. Reuse the one spawn-ledger
        identity (`spawn_ownership.member_identities` for the leader pid; the same source
        `_spawned_leader_identity_matches` uses) so a healthy status answering for a DIFFERENT
        process is never mistaken for our own late-but-valid child. No second RPC.
        """

        ownership = self.spawn_ownership
        if ownership is None:
            return False
        leader_pid = ownership.leader_pid
        expected = dict(ownership.member_identities).get(leader_pid, "")
        if not expected:
            return False
        if int(status.get("pid") or 0) != leader_pid:
            return False
        record, refusal = self._validated_status_identity(status, require_carried_identity=True)
        if refusal:
            return False
        return recorded_start_identity(record) == expected

    def _status_is_wrong_identity(self, status: dict[str, Any]) -> bool:
        """Classify a live-past-deadline child as carrying the WRONG identity (terminal).

        Two terminal shapes: the pid we spawned is alive but now carries a different
        start-identity -- a reused-pid imposter, so the OS-level proof fails -- or our leader is
        alive and ours yet the status response is an otherwise-VALID healthy identity naming a
        DIFFERENT process, a peer answering our socket. A not-ok or dropped status from our own
        still-alive leader is transient not-ready, never this.
        """

        if not self._spawned_leader_identity_matches():
            return True
        record, refusal = self._validated_status_identity(status, require_carried_identity=True)
        if refusal in {"service_name_mismatch", "protocol_version_mismatch"}:
            return True
        if refusal:
            return False
        return not self._status_matches_spawned_leader(status)

    def _wrong_identity_reason(self, status: dict[str, Any]) -> str:
        """Name which wrong-identity shape declared this terminal startup episode."""

        leader_pid = self.spawn_ownership.leader_pid if self.spawn_ownership is not None else 0
        _record, refusal = self._validated_status_identity(status, require_carried_identity=True)
        if refusal in {"service_name_mismatch", "protocol_version_mismatch"}:
            return f"{self.spec.name} startup status identity mismatch: {refusal}"
        if self._spawned_leader_identity_matches():
            return (
                f"{self.spec.name} startup status identity mismatch: peer answered with "
                f"pid {int(status.get('pid') or 0)}, not spawned leader {leader_pid}"
            )
        return (
            f"{self.spec.name} startup pid {leader_pid} "
            "start-identity mismatch (reused-pid imposter)"
        )

    def acquire_lease(self, existing_lease_id: str = "") -> dict[str, Any]:
        if not self.ensure_started():
            if self._upgrade_required is not None:
                return {
                    "ok": False,
                    "error": f"{self.spec.name} client upgrade required",
                    "error_code": "upgrade_required",
                    **self._upgrade_required,
                }
            if self._process_diagnostic:
                return {
                    "ok": False,
                    "error": f"{self.spec.name} process identity refused",
                    "error_code": self._process_diagnostic["reason"],
                    "process_diagnostic": dict(self._process_diagnostic),
                }
            return {"ok": False, "error": f"{self.spec.name} unavailable"}
        response = self._request(
            "lease",
            {"client_pid": os.getpid(), "lease_id": str(existing_lease_id or "")},
            timeout=0.25,
        )
        if response.get("ok"):
            # A lease refresh must not publish an unprovable identity either; a
            # refusal only drops cached health so the next call re-probes.
            self._publish_record(self._request("status", timeout=0.2))
        return response

    def release_lease(self, lease_id: str) -> dict[str, Any]:
        return self._request("release", {"lease_id": lease_id}, timeout=0.25)

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = (
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
            "process_diagnostic": dict(self._process_diagnostic),
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
            parsed_cpu_seconds = parse_ps_cpu_seconds(fields[3])
            if pid not in pids or parsed_cpu_seconds is None or (pid != parent_pid and ppid != parent_pid):
                continue
            readings[pid] = (parsed_cpu_seconds, rss_kib * 1024)
        return readings if parent_pid in readings else {}

    def _read_process_cpu_seconds_and_rss(self, pid: int) -> tuple[float, int] | None:
        """Return (cumulative CPU seconds, RSS bytes) for an existing pid."""
        return read_process_cpu_seconds_and_rss(pid)

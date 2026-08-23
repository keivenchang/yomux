"""Cross-port lifecycle owner for bounded local YOLOmux services."""

from __future__ import annotations

import json
import ctypes
import ctypes.util
import fcntl
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
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
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
from ..common import MANAGED_PRIVATE_ROOT
from ..common import STATE_DIR
from ..host_identity import HostIdentity
from ..host_identity import LocalProcessDiagnostic
from ..host_identity import LocalProcessReason
from ..host_identity import current_host_identity
from ..host_identity import is_current_local_process
from ..host_identity import process_start_identity
from ..host_identity import process_start_ticks
from ..host_identity import process_identity_snapshot
from ..host_identity import process_parent_id
from ..host_identity import process_state as host_process_state
from ..host_identity import recorded_start_identity
from ..infra.process_claims import CLAIM_ACTION_ADOPT
from ..infra.process_claims import CLAIM_REASON_KIND_MISMATCH
from ..infra.process_claims import CLAIM_REASON_MISSING_SUPERVISOR_RECORD
from ..infra.process_claims import CLAIM_REASON_NAMESPACE_MISMATCH
from ..infra.process_claims import CLAIM_REASON_SUPERVISOR_ALIVE
from ..infra.process_claims import CLAIM_RESULT_ADOPTED
from ..infra.process_claims import CLAIM_RESULT_ADOPTION_CONTENDED
from ..infra.process_claims import ProcessClaim
from ..infra.process_claims import ProcessClaimError
from ..infra.process_claims import ProcessClaimLedger
from ..infra.worktree_writer import child_process_artifact_environment
from .lifetime import DIMENSION_CLAIM
from .lifetime import LIFETIME_ACTION_NONE
from .lifetime import LOCAL_SERVICE_SPAWN_GENERATION_ENV
from .lifetime import LIFETIME_ACTION_TERMINATE
from .lifetime import LIFETIME_RESULT_REFUSED
# One definition, re-exported here so every existing importer and the launch
# timing tests keep reading the same object rather than a second literal.
from .lifetime import LOCAL_SERVICE_RETIRE_FORCE_SECONDS
from .lifetime import LOCAL_SERVICE_RETIRE_GRACE_SECONDS
from .lifetime import SCOPE_TRACKED_PROCESS_GROUP
from .lifetime import ServiceDestructionAuthorization
from .lifetime import TerminationOutcome
from .lifetime import authorize_service_destruction
from .lifetime import root_sharing_mode
from .lifetime import service_claim_ledger
from .lifetime import terminate_authorized_process
from .rpc import LocalRpcError
from .rpc import local_service_failure_reason
from .rpc import new_envelope
from .rpc import request
from .rpc import retry_local_service_prehandler_busy
from .rpc import safe_socket_path
from .runtime import local_service_exception_cause
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
# jobd accepts request deadlines up to 120 seconds and gives an executing worker a two-second
# cooperative-stop backstop. Registry cannot import jobd without creating a cycle, so keep the
# replacement-side bound explicit and leave one second for the broker to publish terminal state.
LOCAL_SERVICE_JOBD_DRAIN_GRACE_SECONDS = 123.0
LOCAL_SERVICE_IDLE_SECONDS_ENV = "YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS"
LOCAL_SERVICE_START_EXIT_LIMIT = 3
LOCAL_SERVICE_STDERR_TAIL_BYTES = 4096



# What actually happened to a retiring generation, in the caller's vocabulary.
# A retirement that does not complete used to surface as a bare `False`; these
# name the three outcomes an operator has to act on differently.
# The identity fields a claim payload carries, so a re-read claim rebuilds the
# same record the ledger published rather than a second, drifting shape.
_CLAIM_IDENTITY_FIELDS = (
    "stable_host_id",
    "hostname",
    "boot_id",
    "pid",
    "process_start_identity",
    "process_start_ticks",
    "instance_nonce",
)

LOCAL_SERVICE_TRANSITION_HANDOFF = "process_identity_handoff"
LOCAL_SERVICE_TRANSITION_EXITED = "retired_process_exited"
LOCAL_SERVICE_TRANSITION_IDENTITY_UNPROVEN = "retired_process_identity_unproven"
LOCAL_SERVICE_RETIREMENT_TRANSITIONS = {
    LocalProcessReason.PROCESS_IDENTITY_REUSED: LOCAL_SERVICE_TRANSITION_HANDOFF,
    LocalProcessReason.PROCESS_NOT_FOUND: LOCAL_SERVICE_TRANSITION_EXITED,
}

_LAUNCH_CONTEXT: dict[str, int] = {}
_TRANSPORT_DIAGNOSTICS_LOCK = threading.Lock()
_TRANSPORT_TEARDOWNS_TOTAL = 0
_TRANSPORT_TEARDOWNS_BY_EXCEPTION: dict[str, int] = {}


def jobd_retirement_state(
    response: Mapping[str, Any],
    *,
    service_name: str,
    service_pid: int,
    protocol_version: int,
    source_epoch: str,
    shutdown_handshake: bool,
) -> str:
    """Return one exact jobd identity's stopped or draining retirement state."""

    response_pid = response.get("pid")
    response_version = response.get("version")
    response_source_epoch = response.get("source_epoch")
    if (
        service_name != "jobd"
        or response.get("ok") is not True
        or isinstance(response_pid, bool)
        or not isinstance(response_pid, int)
        or response_pid != service_pid
        or isinstance(response_version, bool)
        or not isinstance(response_version, int)
        or response_version != protocol_version
        or (
            source_epoch
            and (
                not isinstance(response_source_epoch, str)
                or response_source_epoch != source_epoch
            )
        )
    ):
        return ""
    if shutdown_handshake:
        draining = response.get("draining")
        if response.get("shutdown") is not True or not isinstance(draining, bool):
            return ""
        return "draining" if draining else "stopped"
    active_records = response.get("active_records")
    queues = response.get("queues")
    if not isinstance(active_records, list) or not isinstance(queues, dict):
        return ""
    draining = bool(active_records) or any(
        not isinstance(count, bool) and isinstance(count, int) and count > 0
        for count in queues.values()
    )
    return "draining" if draining else "stopped"


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
    """Return the native process state, preserving zombies as non-serving.

    Delegates to the one owner beside the identity fence. It used to be a second
    implementation here, which is how the fence and its callers came to disagree
    about whether a corpse is alive.
    """
    return host_process_state(int(pid))


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


def pid_is_serving(pid: int, *, table: dict[int, ProcessTableEntry] | None = None) -> bool:
    """Return True iff ``pid`` is a live, NON-ZOMBIE process in the bounded table.

    "Is this pid still serving" is a different question from "does this pid
    exist", and only this one is safe to build a lifetime decision on.  A
    zombie exists: it answers ``os.kill(pid, 0)``, it keeps its PGID, and
    ``/proc/<pid>/stat`` still reports its original start ticks.  It just cannot
    do anything ever again.  ``bounded_process_table`` already drops those, so
    membership in the table IS the predicate; ``require_complete=True`` means a
    failed process-table read raises rather than silently certifying a live
    process as retired.
    """

    if table is not None:
        return int(pid) in table
    # Same rule, asked about one pid without sweeping the whole process table:
    # `bounded_process_table` keeps a pid only when it is present AND its state
    # is not "Z", which is exactly this. A caller polling one identity every
    # 30ms must not pay for a full `ps` on each pass to learn it.
    return int(pid) > 1 and process_state(int(pid)) not in {"", "Z"}


def live_process_group(pid: int) -> int | None:
    """Return the process group a pid still proves, or None when it cannot be read.

    This is the ONE live prober behind the group-scoped destructive fence, and it
    deliberately asks the KERNEL (``os.getpgid``) rather than the bounded process
    table.  The table is what RESOLVED the tracked group in the first place, so
    checking the group against it again would compare a measurement with itself
    and the dimension could never vary.  Asking a different source at a later
    moment is what makes "is this target still in the group I was authorized
    against" a real question.

    ``None`` means the group could not be read, which the authorization treats as
    unproven and therefore refuses.  It is deliberately distinct from ``0``:
    ``os.getpgid`` never returns 0 for a live process, so collapsing the two
    would make an unreadable group indistinguishable from a real one and hand a
    destructive decision a default it never proved.
    """

    if int(pid) <= 1:
        return None
    observed = process_group_id(int(pid))
    return observed if observed > 0 else None


def process_group_has_serving_member(
    process_group: int,
    *,
    table: dict[int, ProcessTableEntry] | None = None,
) -> bool:
    """Return True iff any live, non-zombie member of the group is in the table."""

    resolved = bounded_process_table(require_complete=True) if table is None else table
    return any(entry.pgid == int(process_group) for entry in resolved.values())


def process_record_diagnostic(
    record: dict[str, Any],
    *,
    host_identity: HostIdentity | None = None,
    table: dict[int, ProcessTableEntry] | None = None,
) -> LocalProcessDiagnostic:
    """Route persisted local-service identity through the one central fence."""

    if table is None:
        # The zombie rule itself belongs to the fence; this only supplies the
        # state reader it needs. Keeping the rule in one place is what stopped
        # `_retire_incompatible_service` from carrying a second, zombie-blind
        # liveness predicate alongside this one.
        return is_current_local_process(
            record,
            host_identity=host_identity,
            start_identity_reader=process_start_identity,
            pid_probe=pid_is_alive,
            state_reader=process_state,
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
        # `bounded_process_table` already drops every pid whose state is "Z", so
        # a pid present here is non-zombie by construction. Re-reading /proc per
        # record would pay for the same proof twice on the watchdog's hot path.
        state_reader=lambda _pid: "",
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
                # The generation this record was PUBLISHED with. Carried out of
                # the record rather than re-read from the live process at the
                # decision site: a dimension both sides read off the same target
                # can never disagree, so re-proving it would prove nothing.
                "spawn_generation": str(record.get("spawn_generation") or ""),
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


ORPHAN_ACTION_NONE = "none"
ORPHAN_ACTION_TERMINATE = LIFETIME_ACTION_TERMINATE
ORPHAN_RESULT_REPORTED_ONLY = "reported_only"
ORPHAN_RESULT_REPAIRED = "repaired"
ORPHAN_RESULT_REFUSED = "refused"
ORPHAN_RESULT_FAILED = "failed"

# Why one claim-backed survivor was not repaired. Every one of these is a real,
# distinguishable authority gap; none of them is a literal that no branch can vary.
ORPHAN_REASON_SUPERVISOR_ALIVE = CLAIM_REASON_SUPERVISOR_ALIVE
ORPHAN_REASON_GENERATION_NOT_SUPERSEDED = "generation_not_superseded"
ORPHAN_REASON_NO_CLAIM = "no_persisted_claim"
ORPHAN_REASON_PROCESS_TABLE_UNAVAILABLE = "process_table_unavailable"

# Why one ambiguous survivor could not be acted on.  These are the real,
# distinguishable authority gaps a survivor can sit in; they were previously
# collapsed into the single constant `untracked_no_ledger_record`, which made
# the field incapable of telling an operator anything.
ORPHAN_REASON_NO_LEDGER_RECORD = "untracked_no_ledger_record"
ORPHAN_REASON_SUPERSEDED_GENERATION = "superseded_by_recorded_generation"
ORPHAN_REASON_UNREADABLE_RECORD = "unreadable_service_record"


class OrphanObservationLedger:
    """The one retained first-observation clock for ambiguous survivors.

    A single process-table snapshot carries no wall-clock-comparable birth time
    (``ProcessTableEntry.start_time`` is an opaque platform tick counter, not an
    epoch), so "how long has this been hanging around" can only come from
    observation retained across supervision passes.  That bookkeeping existed
    only inside ``statusd.StatusDaemon.orphan_diagnostics`` -- a method with no
    product caller -- while the surface the System panel actually reads
    (``app.runtime_process_ledger``) carried no age at all.  One owner, read by
    both, so the two can never disagree again.

    Observations are keyed by service directory: two directories inspected from
    the same process must not prune each other's retained pids.
    """

    def __init__(self) -> None:
        self._first_seen: dict[str, dict[int, float]] = {}
        self._lock = threading.Lock()

    def age_rows(self, service_dir: Path, rows: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        key = str(Path(service_dir))
        with self._lock:
            retained = self._first_seen.setdefault(key, {})
            seen: set[int] = set()
            for row in rows:
                pid = int(row["pid"])
                seen.add(pid)
                row["age_seconds"] = max(0.0, float(now) - retained.setdefault(pid, float(now)))
            for departed in set(retained) - seen:
                del retained[departed]
        return rows

    def forget(self, service_dir: Path) -> None:
        with self._lock:
            self._first_seen.pop(str(Path(service_dir)), None)


ORPHAN_OBSERVATIONS = OrphanObservationLedger()


def _recorded_socket_owners(service_dir: Path) -> dict[str, dict[str, Any] | None]:
    """Map each recorded socket path to the record that names it.

    ``None`` marks a record file that exists but could not be parsed, which is a
    different authority gap from no record at all and must not be collapsed into
    it: an unreadable record hides an owner rather than proving there is none.
    """

    owners: dict[str, dict[str, Any] | None] = {}
    try:
        record_paths = sorted(Path(service_dir).glob("*.service.json"))
    except OSError:
        return owners
    for record_path in record_paths:
        record = read_json_file(record_path, None)
        if not isinstance(record, dict):
            owners[str(record_path.with_suffix("").with_suffix(".sock"))] = None
            continue
        socket_path = str(record.get("socket") or "")
        if socket_path:
            owners[socket_path] = record
    return owners


def verified_orphan_diagnostics(
    service_dir: Path,
    table: dict[int, ProcessTableEntry] | None = None,
    *,
    now: float | None = None,
    observations: OrphanObservationLedger | None = None,
) -> list[dict[str, Any]]:
    """Return one typed, bounded diagnostic row per ambiguous survivor.

    Every candidate this finds lacks a *usable* ledger record (that is what
    ``untracked_local_service_processes`` proves), so identity can never be
    fully verified and no signal or unlink is authorized -- see Rejected
    Shortcuts in ``DOIT.p1.e5.backend-lifetime-supervision.md`` ("do not add
    a broad host sweeper... let a process count its own connection as
    external demand" and "no signal without authority"). This is the bounded
    host-local repair path's diagnostic half: it must never remain silent
    about an ambiguous survivor, but it may only ever report one, never act
    on it beyond reporting.

    ``attempted_action`` and ``result`` are therefore genuinely constant here
    and say so honestly; ``reason`` is not, and now names which authority gap
    the survivor actually sits in.  ``age_seconds`` comes from the shared
    ``OrphanObservationLedger`` so every caller reports the same retained age.
    """
    if table is None:
        table = bounded_process_table()
    tracked = tracked_local_service_groups(service_dir, table)
    untracked = untracked_local_service_processes(service_dir, table, tracked)
    recorded_owners = _recorded_socket_owners(service_dir)
    rows: list[dict[str, Any]] = []
    for candidate in untracked:
        socket_path = str(candidate.get("socket") or "")
        if socket_path not in recorded_owners:
            reason = ORPHAN_REASON_NO_LEDGER_RECORD
        elif recorded_owners[socket_path] is None:
            reason = ORPHAN_REASON_UNREADABLE_RECORD
        else:
            record = recorded_owners[socket_path]
            assert record is not None
            if int(record.get("pid") or 0) == int(candidate["pid"]):
                # The record names this exact pid yet the group was not tracked, so
                # the central fence rejected it. Carry that fence's own reason rather
                # than minting a second vocabulary for the same decision.
                reason = f"identity_{process_record_diagnostic(record, table=table).reason.value}"
            else:
                reason = ORPHAN_REASON_SUPERSEDED_GENERATION
        rows.append({
            "pid": int(candidate["pid"]),
            "ppid": candidate.get("ppid"),
            "pgid": candidate.get("pgid"),
            "socket": candidate.get("socket"),
            "attempted_action": ORPHAN_ACTION_NONE,
            "result": ORPHAN_RESULT_REPORTED_ONLY,
            "reason": reason,
        })
    ledger = observations or ORPHAN_OBSERVATIONS
    return ledger.age_rows(service_dir, rows, wall_clock() if now is None else float(now))


def repair_verified_orphans(
    service_dir: Path,
    state_dir: Path,
    service_names: tuple[str, ...],
    *,
    current_generations: Mapping[str, str] | None = None,
    private_root: bool = MANAGED_PRIVATE_ROOT,
    host_identity: HostIdentity | None = None,
    kill: Callable[[int, int], None] = os.kill,
    clock: Callable[[], float] = monotonic_clock,
    sleep: Callable[[float], None] = sleep_clock,
    grace_seconds: float = LOCAL_SERVICE_RETIRE_GRACE_SECONDS,
    force_seconds: float = LOCAL_SERVICE_RETIRE_FORCE_SECONDS,
    now: float | None = None,
    observations: OrphanObservationLedger | None = None,
) -> list[dict[str, Any]]:
    """Repair claim-backed survivors, host-locally and genuinely bounded.

    This is a SEPARATE producer from ``verified_orphan_diagnostics`` on purpose.
    That function's candidates come from command-text matching, which is a
    rejected authority: a process is not yours because its argv looks like yours.
    Its constants are honest FOR THAT INPUT -- it may only ever report. Repair
    needs a different input, and the only one that carries authority is a claim
    the spawning supervisor persisted while it still had direct proof of what it
    created.

    Every dimension is re-proved here before anything is signalled: the claim
    exists; host and boot match; the pid re-proves its recorded process-start
    identity AND is not an unreaped corpse; the kind matches; the namespace
    matches; the generation is STRICTLY older than the caller's current one; and
    the supervisor is provably gone by the full identity fence, not by
    ``pid_is_alive`` on a bare integer. A survivor that fails any of these gets
    zero signals and one typed row. A survivor whose supervisor is alive is
    retained and the row names that surviving supervisor.

    ``age_seconds``, ``attempted_action``, ``result`` and ``failure_reason`` all
    come from what actually executed. None of them is a literal.
    """

    identity = host_identity or current_host_identity()
    generations = dict(current_generations or {})
    started = clock()
    rows: list[dict[str, Any]] = []
    try:
        table = bounded_process_table(require_complete=True)
    except ProcessTableUnavailable as exc:
        # Fail CLOSED before any signal: an incomplete process table cannot tell
        # a dead survivor from an unreadable one, and the difference is a kill.
        return [{
            "pid": 0,
            "attempted_action": ORPHAN_ACTION_NONE,
            "result": ORPHAN_RESULT_REFUSED,
            "reason": ORPHAN_REASON_PROCESS_TABLE_UNAVAILABLE,
            "failure_reason": str(exc),
            "age_seconds": round(clock() - started, 6),
        }]
    for service_name in service_names:
        ledger = service_claim_ledger(
            Path(state_dir),
            str(service_name),
            private_root=private_root,
            host_identity=identity,
        )
        for claim_path, claim in ledger.rows():
            rows.append(_repair_one_claimed_survivor(
                ledger,
                claim_path,
                claim,
                service_name=str(service_name),
                service_dir=Path(service_dir),
                current_generation=str(generations.get(str(service_name), "")),
                identity=identity,
                table=table,
                kill=kill,
                clock=clock,
                sleep=sleep,
                grace_seconds=grace_seconds,
                force_seconds=force_seconds,
                started=started,
            ))
    ledger_observations = observations or ORPHAN_OBSERVATIONS
    return ledger_observations.age_rows(
        Path(service_dir) / "repair",
        rows,
        wall_clock() if now is None else float(now),
    )


def _repair_one_claimed_survivor(
    ledger: ProcessClaimLedger,
    claim_path: Path,
    claim: dict[str, Any] | None,
    *,
    service_name: str,
    service_dir: Path,
    current_generation: str,
    identity: HostIdentity,
    table: dict[int, ProcessTableEntry],
    kill: Callable[[int, int], None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    grace_seconds: float,
    force_seconds: float,
    started: float,
) -> dict[str, Any]:
    def row(pid: int, action: str, result: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "pid": int(pid),
            "claim_path": str(claim_path),
            "service": service_name,
            "attempted_action": action,
            "result": result,
            "reason": reason,
            "failure_reason": "",
            "age_seconds": round(clock() - started, 6),
            **extra,
        }

    if not isinstance(claim, dict) or not claim:
        return row(0, ORPHAN_ACTION_NONE, ORPHAN_RESULT_REFUSED, ORPHAN_REASON_NO_CLAIM)
    pid = int(claim.get("pid") or 0)
    if str(claim.get("kind") or "") != ledger.kind:
        return row(pid, ORPHAN_ACTION_NONE, ORPHAN_RESULT_REFUSED, CLAIM_REASON_KIND_MISMATCH)
    if str(claim.get("namespace") or "") != ledger.namespace:
        return row(pid, ORPHAN_ACTION_NONE, ORPHAN_RESULT_REFUSED, CLAIM_REASON_NAMESPACE_MISMATCH)
    supervisor = claim.get("supervisor")
    if not isinstance(supervisor, dict) or not supervisor:
        return row(pid, ORPHAN_ACTION_NONE, ORPHAN_RESULT_REFUSED, CLAIM_REASON_MISSING_SUPERVISOR_RECORD)
    supervisor_state = is_current_local_process(supervisor, host_identity=identity)
    if supervisor_state.current:
        return row(
            pid,
            ORPHAN_ACTION_NONE,
            ORPHAN_RESULT_REFUSED,
            ORPHAN_REASON_SUPERVISOR_ALIVE,
            surviving_supervisor=supervisor_state.as_dict(),
        )
    claim_generation = str(claim.get("generation") or "")
    if not claim_generation or not current_generation or claim_generation == current_generation:
        # "Strictly older" is the requirement. An equal generation is the LIVE
        # one and an unknown generation is not older, it is unproven.
        return row(pid, ORPHAN_ACTION_NONE, ORPHAN_RESULT_REFUSED, ORPHAN_REASON_GENERATION_NOT_SUPERSEDED)
    record = {
        **{key: claim[key] for key in _CLAIM_IDENTITY_FIELDS if key in claim},
        "service": service_name,
        "namespace": str(service_dir),
        "spawn_generation": claim_generation,
    }
    diagnostic = process_record_diagnostic(record, table=table)
    authorization = authorize_service_destruction(
        record,
        diagnostic=diagnostic,
        expected_kind=service_name,
        expected_namespace=str(service_dir),
        live_generation_reader=process_spawn_generation,
        claim_state=str(claim.get("claim_id") or ""),
        require_claim=True,
    )
    if not authorization.authorized:
        return row(
            pid,
            ORPHAN_ACTION_NONE,
            ORPHAN_RESULT_REFUSED,
            authorization.reason,
            failed_dimension=authorization.failed_dimension,
        )
    outcome = terminate_authorized_process(
        authorization,
        still_current=lambda: pid_is_serving(pid),
        signal_process=kill,
        grace_seconds=grace_seconds,
        force_seconds=force_seconds,
        clock=clock,
        sleep=sleep,
    )
    if not outcome.confirmed_dead:
        return row(
            pid,
            outcome.attempted_action,
            ORPHAN_RESULT_FAILED,
            outcome.reason,
            failure_reason=outcome.error or outcome.result,
            signals=list(outcome.signals),
        )
    # The claim is spent the moment it is cashed: leaving it would let a later
    # pass signal a recycled pid on an already-used proof.
    try:
        claim_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        return row(
            pid,
            outcome.attempted_action,
            ORPHAN_RESULT_REPAIRED,
            outcome.reason,
            failure_reason=f"claim_remove_failed: {type(error).__name__}",
            signals=list(outcome.signals),
        )
    return row(
        pid,
        outcome.attempted_action,
        ORPHAN_RESULT_REPAIRED,
        outcome.reason,
        signals=list(outcome.signals),
    )


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


def retained_claim_pids(claim_rows: list[dict[str, Any]]) -> set[int]:
    """Pids a claim says are deliberately retained by a still-living supervisor.

    A claim whose supervisor is alive names that surviving supervisor by design.
    Stopping such a helper at OUR launcher exit would kill something another live
    server is still using, which is precisely the failure adoption exists to
    prevent, so those pids are excluded from teardown and reported instead.
    """

    return {
        int(row.get("pid") or 0)
        for row in claim_rows
        if str(row.get("reason") or "") == CLAIM_REASON_SUPERVISOR_ALIVE and int(row.get("pid") or 0) > 0
    }


def _terminate_group_member(
    member_record: dict[str, Any],
    member_pid: int,
    *,
    service_name: str,
    service_dir: Path,
    group_pgid: int,
    generation_reader: Callable[[int], str | None],
    process_group_reader: Callable[[int], int | None],
    kill: Callable[[int, int], None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    force_seconds: float,
    table_reader: Callable[[], dict[int, ProcessTableEntry]],
) -> TerminationOutcome | None:
    """Force one surviving group member, or return None when it is already gone.

    A pool child is not independently addressable: it holds no socket, no record
    of its own, and no spawn generation, so it goes through the ONE owner under
    the GROUP scope. That scope does not waive the generation dimension, it
    substitutes the one this class of target genuinely has: the process group it
    provably shares with a leader whose identity came from a persisted record,
    re-read live before the signal. It is signalled only after the leader's own
    escalation, so a child that was going to exit with its parent already has.
    """

    table = table_reader()
    if not pid_is_serving(member_pid, table=table):
        return None
    record = dict(member_record)
    record.setdefault("service", service_name)
    record["namespace"] = str(service_dir)
    record["pgid"] = int(group_pgid)
    diagnostic = process_record_diagnostic(record, table=table)
    authorization = authorize_service_destruction(
        record,
        diagnostic=diagnostic,
        expected_kind=service_name,
        expected_namespace=str(service_dir),
        live_generation_reader=generation_reader,
        claim_state="launcher_owned_group_member",
        scope=SCOPE_TRACKED_PROCESS_GROUP,
        expected_process_group=int(group_pgid),
        live_process_group_reader=process_group_reader,
    )
    return terminate_authorized_process(
        authorization,
        still_current=lambda: pid_is_serving(member_pid, table=table_reader()),
        signal_process=kill,
        # The leader's own SIGTERM window has already elapsed by the time this
        # runs, so a second graceful window would only double the teardown.
        graceful_first=False,
        target="group-member",
        grace_seconds=0.0,
        force_seconds=force_seconds,
        clock=clock,
        sleep=sleep,
    )


def shutdown_owned_local_services(
    port: int,
    service_dir: Path,
    *,
    launcher_pid: int | None = None,
    table_reader: Callable[[], dict[int, ProcessTableEntry]] = bounded_process_table,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = sleep_clock,
    clock: Callable[[], float] = monotonic_clock,
    claims_reader: Callable[[], list[dict[str, Any]]] | None = None,
    grace_seconds: float = LOCAL_SERVICE_RETIRE_GRACE_SECONDS,
    force_seconds: float = LOCAL_SERVICE_RETIRE_FORCE_SECONDS,
    # The two live dimension probes, injectable for the same reason `kill` and
    # `table_reader` are: a destructive decision this function makes must be
    # drivable by whoever is telling it what it is allowed to see.
    generation_reader: Callable[[int], str | None] = process_spawn_generation,
    process_group_reader: Callable[[int], int | None] = live_process_group,
) -> dict[str, list[int]]:
    """Stop only sidecars whose ledger proves this live launcher created them.

    Routed through the ONE destructive owner rather than a private
    SIGTERM/sleep/SIGKILL block, so the escalation, the identity fence, and the
    reported outcome are the same here as on every other path. The budgets are
    the shared constants, not re-spelled literals: ``grace_seconds`` was
    ``0.5`` -- a second copy of ``LOCAL_SERVICE_RETIRE_GRACE_SECONDS`` that
    could silently drift from it.

    ``unconfirmed`` is the field that stopped this from lying. The old version
    reported ``terminated`` for every pid it managed to send SIGKILL to and never
    re-checked, so a target that survived both signals was indistinguishable from
    one that exited. ``retained`` names the pids a live claim protects.
    """

    owner_pid = int(os.getpid() if launcher_pid is None else launcher_pid)
    initial = table_reader()
    groups = [
        group
        for group in tracked_local_service_groups(service_dir, initial)
        if group["launcher_port"] == int(port) and group["launcher_pid"] == owner_pid
    ]
    retained_pids = retained_claim_pids(claims_reader() if claims_reader is not None else [])
    signalled: list[int] = []
    terminated: list[int] = []
    unconfirmed: list[int] = []
    retained: list[int] = []
    for group in groups:
        leader_pid = int(group["pid"])
        if leader_pid in retained_pids:
            retained.append(leader_pid)
            continue
        record = dict(group["process_record"])
        record.setdefault("service", group["service"])
        record["namespace"] = str(service_dir)
        # The generation the RECORD was published with, not one re-read from the
        # live process at the decision site. Re-reading it here and again inside
        # the authorization made recorded and observed the same measurement, so
        # the dimension could not vary and re-proving it proved nothing.
        record["spawn_generation"] = str(group["spawn_generation"] or "")
        authorization = authorize_service_destruction(
            record,
            diagnostic=process_record_diagnostic(record, table=initial),
            expected_kind=str(group["service"]),
            expected_namespace=str(service_dir),
            live_generation_reader=generation_reader,
            claim_state="launcher_owned_group",
        )
        outcome = terminate_authorized_process(
            authorization,
            # Liveness comes from the SAME reader that resolved the group. A
            # caller injecting `table_reader` is telling this function what it is
            # allowed to see; polling /proc directly instead would quietly ignore
            # that and answer from a process table the caller never authorized.
            still_current=lambda pid=leader_pid: pid_is_serving(pid, table=table_reader()),
            signal_process=kill,
            grace_seconds=grace_seconds,
            force_seconds=force_seconds,
            clock=clock,
            sleep=sleep,
        )
        if outcome.signals:
            signalled.append(leader_pid)
        if outcome.confirmed_dead:
            terminated.append(leader_pid)
        elif outcome.unproven_authority:
            # Either this build may never signal that record (no spawn
            # generation) or authority over the identity was not proven. Neither
            # is an escalation that failed, and reporting them as `unconfirmed`
            # would claim a signal that was never sent; both are things this
            # teardown deliberately left running.
            retained.append(leader_pid)
        else:
            unconfirmed.append(leader_pid)
        # The leader is not the group. Spawn/pool children inherited the leader's
        # fresh session at spawn, so nothing else can be in this group -- but they
        # do not exit just because the leader did, and leaving them would strand
        # exactly the workers this teardown exists to collect.
        #
        # A leader this teardown had no proven authority over retains its whole
        # group. The leader's record is what proved this group exists at all and
        # a member's group-scoped authority is derived from it, so force-killing
        # the workers of a daemon we were not allowed to stop would leave a
        # half-torn group -- worse than either whole answer. A member carries no
        # generation of its own, so without this the superseded-generation and
        # replaced-identity refusals would stop the leader and kill its workers.
        for member_pid, member_record in sorted(group["member_records"].items()):
            member_pid = int(member_pid)
            if outcome.unproven_authority and member_pid != leader_pid:
                retained.append(member_pid)
                continue
            if member_pid == leader_pid or member_pid in retained_pids:
                if member_pid in retained_pids:
                    retained.append(member_pid)
                continue
            member_outcome = _terminate_group_member(
                member_record,
                member_pid,
                service_name=str(group["service"]),
                service_dir=Path(service_dir),
                group_pgid=int(group["pgid"]),
                generation_reader=generation_reader,
                process_group_reader=process_group_reader,
                kill=kill,
                clock=clock,
                sleep=sleep,
                force_seconds=force_seconds,
                table_reader=table_reader,
            )
            if member_outcome is None:
                continue
            if member_outcome.signals:
                signalled.append(member_pid)
            if member_outcome.confirmed_dead:
                terminated.append(member_pid)
            elif member_outcome.unproven_authority:
                # Same rule as the leader: nothing was signalled, so calling it
                # `unconfirmed` would claim an escalation that never ran.
                retained.append(member_pid)
            else:
                unconfirmed.append(member_pid)
    return {
        "signalled": sorted(signalled),
        "terminated": sorted(terminated),
        "unconfirmed": sorted(unconfirmed),
        "retained": sorted(retained),
    }


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
        self._runtime_locks_pruned = False
        # Set the first time this exact registry object durably publishes a
        # record. If the directory later vanishes underneath it (fixture
        # teardown, an external cleanup pass), a later write from THIS SAME
        # owner must never silently resurrect the directory -- that would be
        # a fresh registry object, not this one, taking authority it never
        # proved. A different LocalServiceRegistry instance (adoption,
        # retirement of an incompatible generation) still starts with this
        # False and may create the directory on its own first write.
        self._record_directory_confirmed = False
        # Re-entrancy for the ONE record lock. `ensure_started` already holds it
        # across retire/remove/spawn/publish, and `_write_record` must take it to
        # make its compare-and-swap atomic; `file_lock` is not re-entrant, so a
        # naive second acquire would deadlock the start path.
        self._record_lock_held = False
        self._claim_ledger: ProcessClaimLedger | None = None
        # Authority over the daemon this registry supervises: published when this
        # registry spawns it, or transferred to this registry by an adoption
        # transaction when the original launcher is provably gone.
        self.claim: ProcessClaim | None = None
        self.claim_rows: list[dict[str, Any]] = []
        # True only when this registry's claim arrived through the adoption
        # transaction rather than through publishing its own spawn.
        self._claim_was_adopted = False

    @property
    def managed_private_root(self) -> bool:
        """Whether this registry's root has exactly one possible supervisor.

        A ``YOLOMUX_ROOT`` run owns every path it uses, so a daemon there can
        never be inherited: there is no other caller who could be using it. The
        per-user runtime directory is shared by every YOLOmux server that user
        runs, so a survivor there may legitimately outlive its launcher.
        """

        return MANAGED_PRIVATE_ROOT

    def claim_ledger(self) -> ProcessClaimLedger:
        """Resolve this service kind's claim ledger once, lazily.

        Lazily because building it resolves the host identity, which touches the
        filesystem; a registry is constructed during import in some callers and
        that read must not run there.
        """

        if self._claim_ledger is None:
            self._claim_ledger = service_claim_ledger(
                self.state_dir,
                self.spec.name,
                private_root=self.managed_private_root,
                host_identity=self.host_identity,
            )
        return self._claim_ledger

    @contextmanager
    def _record_lock(self) -> Iterator[None]:
        """Hold the one durable record lock, re-entrantly within this registry."""

        if self._record_lock_held:
            yield
            return
        with file_lock(self.lock_path, dir_mode=0o700):
            self._record_lock_held = True
            try:
                yield
            finally:
                self._record_lock_held = False

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

    def _prune_stale_runtime_locks(self) -> list[Path]:
        """Remove only unlocked runtime generations no current record can name."""

        active = {self.lock_path, self.socket_path.with_suffix(".lock")}
        try:
            record_paths = tuple(self.service_dir.glob("*.service.json"))
        except OSError:
            return []
        for record_path in record_paths:
            record = read_json_file(record_path, None)
            if not isinstance(record, dict):
                continue
            diagnostic = process_record_diagnostic(record, host_identity=self.host_identity)
            if not diagnostic.current and diagnostic.may_remove_stale_record:
                continue
            recorded_socket = str(record.get("socket") or "")
            if recorded_socket:
                active.add(Path(recorded_socket).with_suffix(".lock"))
        try:
            candidates = sorted(self.service_dir.glob(f"{self.spec.name}.*.lock"))
        except OSError:
            return []
        removed: list[Path] = []
        open_flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        for candidate in candidates:
            if candidate in active:
                continue
            try:
                fd = os.open(candidate, open_flags)
            except OSError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue
                opened = os.fstat(fd)
                try:
                    current = candidate.stat(follow_symlinks=False)
                except OSError:
                    continue
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    continue
                try:
                    candidate.unlink()
                except OSError:
                    continue
                removed.append(candidate)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)
        return removed

    @property
    def _lock_directory_is_absent(self) -> bool:
        """Whether the directory the record LOCK lives in does not exist right now.

        The ONE predicate every entry point checks before taking that lock.
        ``file_lock`` does ``parent.mkdir(...)`` followed by ``parent.chmod(...)``,
        so ANY entry point that takes it resurrects and re-permissions a
        directory teardown removed -- not just the record write that was already
        fenced. `_publish_record` was fenced and `prune_stale_runtime_locks_once`
        was not: one owner fenced, its sibling forgotten, which is the shape this
        whole change exists to remove.

        It is deliberately the LOCK's parent, not the record's. A long socket
        path relocates the socket (and therefore the record) under a `/tmp`
        fallback while the lock deliberately stays in the configured service
        directory, so the two can be different directories and only one of them
        is the one `file_lock` would create.
        """

        return not self.lock_path.parent.exists()

    def prune_stale_runtime_locks_once(self) -> list[Path]:
        """Run startup maintenance even when this generation adopts a healthy daemon."""

        if self._runtime_locks_pruned:
            return []
        if self._lock_directory_is_absent:
            # A directory that does not exist holds no stale lock to prune, so
            # there is nothing this call could achieve by creating one.
            return []
        with self.lock:
            if self._runtime_locks_pruned:
                return []
            with self._record_lock():
                removed = self._prune_stale_runtime_locks()
            self._runtime_locks_pruned = True
            return removed

    @property
    def stderr_path(self) -> Path:
        return self.socket_path.with_suffix(".stderr.log")

    def _read_record(self) -> dict[str, Any]:
        value = read_json_file(self.record_path, {})
        return value if isinstance(value, dict) else {}

    def _record_supersession_refusal(self, existing: dict[str, Any], incoming: dict[str, Any]) -> str:
        """Refuse a blind overwrite of a record another live generation owns.

        The write used to be an unconditional ``atomic_write_text``: whoever
        published last won, with no read and no comparison.  That is how the
        supervisor field came to describe the most recent caller instead of the
        owner -- while the comment above it claimed first-writer-wins.
        """

        try:
            existing_pid = int(existing.get("pid") or 0)
            incoming_pid = int(incoming.get("pid") or 0)
        except (TypeError, ValueError):
            return "unreadable_record_identity"
        if existing_pid == incoming_pid and recorded_start_identity(existing) == recorded_start_identity(incoming):
            return ""
        if process_record_diagnostic(existing, host_identity=self.host_identity).current:
            # A different process is alive and this record names it. Overwriting
            # would silently retarget every destructive decision downstream --
            # the watchdog, preflight, and launcher-exit teardown all resolve
            # their targets from exactly this file.
            return f"record_owned_by_live_pid_{existing_pid}"
        return ""

    def _merge_supervision_provenance(self, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        """Keep the FIRST supervisor's provenance unless this registry adopted the daemon.

        A shared daemon is published again by every server that leases it, so a
        last-writer-wins merge made ``supervisor``/``launcher_pid`` name whichever
        server most recently made an RPC -- not the one that created it and not
        the one responsible for stopping it.  Provenance therefore only changes
        through the adoption transaction, which is fenced and single-winner.
        """

        try:
            existing_pid = int(existing.get("pid") or 0)
            incoming_pid = int(incoming.get("pid") or 0)
        except (TypeError, ValueError):
            return incoming
        if existing_pid != incoming_pid or existing_pid <= 0:
            return incoming
        adopted = self.claim is not None and int(self.claim.pid) == incoming_pid
        if adopted:
            return incoming
        merged = dict(incoming)
        for field_name in ("supervisor", "launcher_pid", "launcher_port", "spawn_generation", "claim_id", "started_at"):
            if field_name in existing:
                merged[field_name] = existing[field_name]
        return merged

    def _write_record(self, record: dict[str, Any]) -> bool:
        """Publish the durable identity record as a compare-and-swap, never a blind write."""

        if self._record_directory_confirmed and (
            self._lock_directory_is_absent or not self.record_path.parent.exists()
        ):
            # Checked BEFORE the lock, because taking it is itself a mkdir and a
            # chmod on the directory teardown removed. This exact owner already
            # published here, so the directory being gone is a removal, not a
            # first start.
            self._record_refusal_reason = redact_local_service_text(
                f"{self.spec.name} service record directory was removed after this owner already "
                "published into it; refusing to resurrect it"
            )
            return False
        with self._record_lock():
            existing = self._read_record()
            if existing:
                refusal = self._record_supersession_refusal(existing, record)
                if refusal:
                    self._record_refusal_reason = redact_local_service_text(
                        f"{self.spec.name} service record write refused (reason={refusal})"
                    )
                    return False
                record = self._merge_supervision_provenance(existing, record)
            return self._write_record_unlocked(record)

    def _write_record_unlocked(self, record: dict[str, Any]) -> bool:
        if self._record_directory_confirmed and not self.record_path.parent.exists():
            # This exact owner already proved it published here once; the
            # directory disappearing since is a teardown/removal, not a
            # first-start race. Refuse instead of `mkdir`-ing a fresh
            # directory back into existence and writing a record no caller
            # asked this owner to re-create.
            self._record_refusal_reason = redact_local_service_text(
                f"{self.spec.name} service record directory was removed after this owner already "
                "published into it; refusing to resurrect it"
            )
            return False
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(self.record_path, json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)
        except OSError as exc:
            # The directory can vanish between the mkdir above and the final
            # os.replace/chmod inside atomic_write_text (e.g. a test fixture
            # tearing down its own tmp_path, or a concurrent cleanup pass).
            # This is a same-process synchronous write race with no child
            # process involved; refuse through the same typed refusal surface
            # _publish_record already uses for a validation failure, rather
            # than letting a raw OSError escape ensure_started().
            self._record_refusal_reason = redact_local_service_text(
                f"{self.spec.name} service record write failed after its directory changed underneath it "
                f"({type(exc).__name__}: {exc})"
            )
            return False
        self._process_diagnostic = {}
        self._record_directory_confirmed = True
        return True

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

    def _supervisor_is_gone(self, record: dict[str, Any]) -> bool:
        """Whether the recorded supervisor is PROVABLY gone, by the full fence.

        `pid_is_alive(launcher_pid)` was the old test and it is not a proof: a
        bare integer says nothing about which process now holds that number, and
        a zombie answers it affirmatively. The fenced `supervisor` record carries
        host, boot, pid and process-start identity, so "gone" means gone. A
        legacy record that carries only `launcher_pid` still falls back to that
        integer -- but only to refuse, never to authorize: an unprovable
        supervisor can never be declared gone.
        """

        supervisor = record.get("supervisor")
        if isinstance(supervisor, dict) and supervisor:
            diagnostic = is_current_local_process(supervisor, host_identity=self.host_identity)
            return diagnostic.may_remove_stale_record
        launcher_pid = int(record.get("launcher_pid") or 0)
        if launcher_pid <= 0 or launcher_pid == os.getpid():
            return False
        return not pid_is_alive(launcher_pid)

    def _ledger_proves_recorded_daemon(self, service_pid: int) -> bool:
        """Whether the shared ledger still anchors this exact pid to this exact socket."""

        return any(
            group["service"] == self.spec.name
            and group["pid"] == service_pid
            and group["socket"] == str(self.socket_path)
            for group in tracked_local_service_groups(self.service_dir)
        )

    def _is_dead_launcher_survivor(self, service_pid: int) -> bool:
        """Whether the recorded daemon's supervisor is PROVABLY gone. No side effects.

        Split out from the decision below because a health probe runs on every
        RPC and must never run an adoption transaction as a side effect of
        answering "is this daemon healthy?".
        """

        if service_pid <= 0:
            return False
        record = self._read_record()
        if int(record.get("pid") or 0) != service_pid:
            return False
        if not self._supervisor_is_gone(record):
            return False
        return self._ledger_proves_recorded_daemon(service_pid)

    def _dead_launcher_survivor_decision(self, service_pid: int) -> tuple[bool, str]:
        """Decide the fate of a daemon whose launcher is provably gone.

        Returns ``(may_reclaim, claim_state)``.  This is the keystone the rest of
        the destructive contract hangs off, because the two wrong answers fail in
        opposite directions: reclaim a survivor another live server is using and
        you kill working state; retain one nobody can reach and it runs forever
        with no owner.

        The root's sharing mode decides which risk exists at all.  Under a
        MANAGED-PRIVATE root there is exactly one possible launcher, so a
        survivor has no successor, no election is possible, and ZERO adoption,
        reuse, or cross-root reclaim is attempted -- the survivor is simply
        reclaimable by the one destructive owner.  Under a CALLER-SHARED root a
        successor may legitimately inherit it, but only by winning the atomic
        adoption transaction: two successors racing the same dead launcher must
        never both believe they own the daemon.

        Every ambiguous outcome -- a transfer another successor is mid-way
        through, a claim whose target no longer proves its identity -- returns
        "do not reclaim" plus the reason, because an unresolved transfer is
        exactly the case where a naive sweep would kill the daemon adoption
        exists to preserve.
        """

        if service_pid <= 0:
            return False, ""
        record = self._read_record()
        if int(record.get("pid") or 0) != service_pid:
            return False, ""
        if not self._supervisor_is_gone(record):
            # Still supervised. Retention is the correct outcome and it is named,
            # not silent: the surviving supervisor is in the record.
            return False, CLAIM_REASON_SUPERVISOR_ALIVE
        if not self._ledger_proves_recorded_daemon(service_pid):
            return False, "ledger_does_not_anchor_recorded_daemon"
        # From here the launcher is provably gone and the ledger anchors this
        # exact daemon, so the root's sharing mode decides what happens next.
        if self.managed_private_root:
            return True, "managed_private_root_no_successor"
        rows = self._adopt_survivor_claims(service_pid)
        adopted = [row for row in rows if row.get("result") == CLAIM_RESULT_ADOPTED]
        if adopted:
            return False, CLAIM_RESULT_ADOPTED
        contended = [row for row in rows if row.get("result") == CLAIM_RESULT_ADOPTION_CONTENDED]
        if contended:
            return False, str(contended[0].get("reason") or CLAIM_RESULT_ADOPTION_CONTENDED)
        # No claim exists for this survivor (it predates claim publication) and no
        # successor is mid-transfer. The fenced ledger record -- this exact host,
        # boot, pid, process-start identity, socket and process group, with a
        # supervisor provably gone -- is the remaining authority, and it is named
        # so a reader can tell it apart from a claim-backed decision.
        return True, "ledger_record_only"

    def _adopt_survivor_claims(self, service_pid: int) -> list[dict[str, Any]]:
        """Run the atomic adoption transaction and retain its typed rows."""

        try:
            rows = self.claim_ledger().adopt_unsupervised()
        except (OSError, ProcessClaimError) as exc:
            # Supervisor boundary for one transfer attempt: a ledger that cannot
            # be read must not crash the start path, and must not silently read
            # as "nothing to adopt" either.
            rows = [{
                "pid": service_pid,
                "attempted_action": CLAIM_ACTION_ADOPT,
                "result": "adoption_failed",
                "reason": f"{type(exc).__name__}: {exc}",
            }]
        self.claim_rows = rows
        for row in rows:
            if row.get("result") == CLAIM_RESULT_ADOPTED and int(row.get("pid") or 0) == service_pid:
                self.claim = self._reload_adopted_claim(row)
                self._claim_was_adopted = self.claim is not None
                if self._claim_was_adopted:
                    # The transfer is not complete until the durable record names
                    # the new supervisor. Until it does, every later probe would
                    # re-detect a dead launcher and re-run the transaction.
                    self._stamp_record_supervision(service_pid, adopted=True)
        return [row for row in rows if int(row.get("pid") or 0) == service_pid]

    def _reload_adopted_claim(self, row: dict[str, Any]) -> ProcessClaim | None:
        """Re-read the claim this registry just won so it holds the live handle."""

        claim_path = Path(str(row.get("claim_path") or ""))
        payload = read_json_file(claim_path, None)
        if not isinstance(payload, dict):
            return None
        ledger = self.claim_ledger()
        return ProcessClaim(
            path=claim_path,
            kind=str(payload.get("kind") or ledger.kind),
            namespace=str(payload.get("namespace") or ledger.namespace),
            generation=str(payload.get("generation") or ""),
            claim_id=str(payload.get("claim_id") or ""),
            pid=int(payload.get("pid") or 0),
            record={key: payload[key] for key in _CLAIM_IDENTITY_FIELDS if key in payload},
            supervisor=payload.get("supervisor") if isinstance(payload.get("supervisor"), dict) else {},
            claimed_at=float(payload.get("claimed_at") or 0.0),
        )

    def publish_claim(self, pid: int, generation: str) -> None:
        """Persist reap authority over the daemon this registry just spawned.

        A refused claim is not fatal: the daemon runs and this process still owns
        it through a live handle.  What is lost is a LATER process's ability to
        prove anything about it, so the refusal is recorded rather than defaulted
        away -- an unclaimed helper is simply never adoptable or reapable by
        claim, which is the correct fail-closed outcome.
        """

        try:
            self._claim_was_adopted = False
            self.claim = self.claim_ledger().publish(
                int(pid),
                generation=str(generation or ""),
                details={"service": self.spec.name, "socket": str(self.socket_path)},
            )
        except ProcessClaimError as exc:
            self.claim = None
            self.claim_rows = [{
                "pid": int(pid),
                "attempted_action": LIFETIME_ACTION_NONE,
                "result": LIFETIME_RESULT_REFUSED,
                "reason": exc.reason_code,
            }]

    def _stamp_record_supervision(self, pid: int, *, generation: str = "", adopted: bool = False) -> bool:
        """Write claim and supervision provenance into the record already on disk.

        Deliberately NOT a second `status` RPC. The identity was proven moments
        ago; asking the wire again would be a second source for one fact, and on
        the startup path it would add a post-deadline call the one-final-probe
        contract forbids.
        """

        with self._record_lock():
            record = self._read_record()
            if int(record.get("pid") or 0) != int(pid):
                return False
            stamped = dict(record)
            stamped["claim_id"] = self.claim.claim_id if self.claim is not None else ""
            resolved_generation = str(generation or "") or self._resolved_spawn_generation(int(pid))
            if resolved_generation:
                stamped["spawn_generation"] = resolved_generation
            stamped["namespace"] = str(self.service_dir)
            stamped["root_sharing"] = root_sharing_mode(private_root=self.managed_private_root)
            if adopted:
                # The ONLY path on which supervision provenance changes hands.
                stamped["supervisor"] = self.host_identity.process_record_fields()
                stamped["launcher_pid"] = os.getpid()
                stamped["launcher_port"] = local_service_launch_port()
            stamped["updated_at"] = wall_clock()
            return self._write_record_unlocked(stamped)

    def _authorization_record(self, record: dict[str, Any], service_pid: int) -> dict[str, Any]:
        """Fill dimensions this caller can PROVE, and leave the rest missing.

        These are proofs, not defaults.  The namespace is proven because this
        record was read from exactly ``self.record_path``, which lives in exactly
        ``self.service_dir``; the spawn generation is proven because it is read
        live out of the running process's inherited environment.  Anything that
        cannot be proven stays absent so the authorization refuses, which is what
        keeps a pre-existing record from silently acquiring authority it never
        carried.
        """

        proven = dict(record)
        proven["namespace"] = str(self.service_dir)
        if not str(proven.get("spawn_generation") or ""):
            ownership = self.spawn_ownership
            if ownership is not None and int(ownership.leader_pid) == int(service_pid):
                # This registry spawned that exact pid and REMEMBERS the marker
                # it passed. That memory is independent of the target, so
                # re-proving it against the live environment can genuinely
                # disagree. `_resolved_spawn_generation` is deliberately NOT used
                # here: its first source is the target's own environment, and a
                # dimension read off the target on both sides can never vary.
                proven["spawn_generation"] = str(ownership.generation_marker)
        return proven

    def _terminate_recorded_generation(
        self,
        record: dict[str, Any],
        service_pid: int,
        *,
        claim_state: str,
    ) -> TerminationOutcome:
        """Stop one recorded generation through the ONE destructive owner.

        This used to be a bespoke SIGTERM/grace/SIGKILL block whose trigger was
        "a future launcher start happened", which is not authority for anything:
        it makes a survivor of a launcher that never returns unresolvable, and it
        made this one of four separate places that could kill a daemon on four
        different triggers with four different fences.
        """

        diagnostic = self._record_process_diagnostic(record)
        authorization = authorize_service_destruction(
            self._authorization_record(record, service_pid),
            diagnostic=diagnostic,
            expected_kind=self.spec.name,
            expected_namespace=str(self.service_dir),
            live_generation_reader=process_spawn_generation,
            claim_state=claim_state,
            # A record this registry published before generations existed carries
            # none, and this build may not signal a process on a proof it never
            # wrote. That is the RETAINED disposition, not a waiver: the owner
            # returns one typed row naming the absent dimension and sends
            # nothing, and the caller below refuses to remove the record or
            # unlink the socket because `confirmed_dead` stays False. A
            # generation that IS recorded still has to re-prove live, and a
            # mismatch still refuses.
        )
        return terminate_authorized_process(
            authorization,
            still_current=lambda: process_record_diagnostic(record, host_identity=self.host_identity).current,
            identity_replaced=lambda: process_record_diagnostic(
                record, host_identity=self.host_identity
            ).reason is LocalProcessReason.PROCESS_IDENTITY_REUSED,
            grace_seconds=LOCAL_SERVICE_RETIRE_GRACE_SECONDS,
            force_seconds=LOCAL_SERVICE_RETIRE_FORCE_SECONDS,
            clock=self.clock,
            sleep=self.sleep,
        )

    def _retire_incompatible_service(self) -> bool:
        """Stop the service currently bound to our socket after a protocol bump or
        code-revision drift. Same-protocol drift matters: without it the stale daemon
        keeps the socket, the fresh spawn cannot bind, and ensure_started fails forever."""
        response = self._request("ping", timeout=0.15)
        service_pid = int(response.get("pid") or 0)
        service_version = int(response.get("version") or response.get("required_protocol_version") or 0)
        dead_launcher_reclaimable, claim_state = self._dead_launcher_survivor_decision(service_pid)
        newer_reclaimable = service_version > self.spec.protocol_version and dead_launcher_reclaimable
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
        record_source_epoch = record.get("source_epoch")
        retained_source_epoch = (
            record_source_epoch
            if isinstance(record_source_epoch, str) and record_source_epoch
            else ""
        )
        shutdown_protocol_version: int | None = None
        legacy_idle_confirmed = False
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
            legacy_identity_matches = (
                legacy_status.get("ok") is True
                and int(legacy_status.get("pid") or 0) == record_pid
                and int(legacy_status.get("version") or 0) == recorded_protocol_version
            )
            if not legacy_identity_matches:
                return False
            if self.spec.name == "jobd":
                legacy_retirement_state = jobd_retirement_state(
                    legacy_status,
                    service_name=self.spec.name,
                    service_pid=record_pid,
                    protocol_version=recorded_protocol_version,
                    source_epoch=retained_source_epoch,
                    shutdown_handshake=False,
                )
                if legacy_retirement_state != "stopped":
                    return False
            service_pid = record_pid
            shutdown_protocol_version = recorded_protocol_version
            legacy_idle_confirmed = True
        if (
            (not response.get("ok") and not older_upgrade and not newer_reclaimable)
            or not service_pid
            or (compatible and not dead_launcher_reclaimable)
        ):
            return True
        if record_pid != service_pid or not diagnostic.current:
            return False
        retained_start_identity = recorded_start_identity(record)
        if not retained_start_identity:
            return False
        response_source_epoch = response.get("source_epoch")
        if isinstance(response_source_epoch, str) and response_source_epoch:
            if retained_source_epoch and response_source_epoch != retained_source_epoch:
                return False
            retained_source_epoch = response_source_epoch
        retirement_protocol_version = shutdown_protocol_version or self.spec.protocol_version
        shutdown_payload = (
            {
                "retirement_handshake": True,
                "expected_source_epoch": retained_source_epoch,
            }
            if self.spec.name == "jobd"
            else None
        )
        if shutdown_protocol_version is None:
            shutdown_response = self._request(
                "shutdown",
                shutdown_payload,
                timeout=0.25,
            )
        else:
            shutdown_response = self._request(
                "shutdown",
                shutdown_payload,
                timeout=0.25,
                protocol_version=shutdown_protocol_version,
            )
        grace_seconds = LOCAL_SERVICE_RETIRE_GRACE_SECONDS
        retirement_state = jobd_retirement_state(
            shutdown_response,
            service_name=self.spec.name,
            service_pid=service_pid,
            protocol_version=retirement_protocol_version,
            source_epoch=retained_source_epoch,
            shutdown_handshake=True,
        )
        if (
            legacy_idle_confirmed
            and shutdown_response.get("ok") is True
            and shutdown_response.get("shutdown") is True
        ):
            retirement_state = "stopped"
        if (
            self.spec.name == "jobd"
            and isinstance(shutdown_response.get("draining"), bool)
            and not retirement_state
        ):
            return False
        if retirement_state == "draining":
            grace_seconds = LOCAL_SERVICE_JOBD_DRAIN_GRACE_SECONDS
        if (
            self.spec.name == "jobd"
            and not retirement_state
            and shutdown_response.get("ok") is True
            and shutdown_response.get("shutdown") is True
            and self._record_process_diagnostic(record).current
        ):
            drain_status = self._request(
                "status",
                timeout=0.2,
                protocol_version=retirement_protocol_version,
            )
            status_state = jobd_retirement_state(
                drain_status,
                service_name=self.spec.name,
                service_pid=service_pid,
                protocol_version=retirement_protocol_version,
                source_epoch=retained_source_epoch,
                shutdown_handshake=False,
            )
            if drain_status.get("ok") is True and not status_state:
                return False
            if status_state == "draining":
                grace_seconds = LOCAL_SERVICE_JOBD_DRAIN_GRACE_SECONDS

        def retained_process_state() -> str:
            """Classify the retained identity through the ONE zombie-aware fence.

            This was the first incorrect boundary in the retirement path, and it
            was a divergent copy rather than a missing feature.  ``pid_is_alive``
            is ``os.kill(pid, 0)`` and ``process_start_identity`` reads
            ``/proc/<pid>/stat``; both answer identically for a running process
            and for an exited-but-unreaped one, because a zombie keeps its PID,
            its PGID, and its start ticks.  Measured: every daemon exits on
            SIGTERM in ~0.11-0.14s, yet this predicate reported ``"current"`` for
            the whole 0.5s grace AND the whole 2.0s force budget for our own
            unreaped child, so retirement never confirmed.  The authority gates a
            few lines below already used ``process_record_diagnostic``, which
            handles ``Z`` -- so the same function held two liveness predicates
            that disagreed, and the zombie-blind one drove the loop.

            Raising the budgets, sleeping, or retrying cannot help: the process
            is already dead and is never coming back to be observed.  A reason
            the fence cannot classify as gone or reused is ``"unproven"``, which
            reaches the final ``!= "exited"`` check and refuses -- no signal, no
            record removal, no socket unlink on an identity we cannot prove.
            """
            diagnostic = process_record_diagnostic(record, host_identity=self.host_identity)
            if diagnostic.current:
                return "current"
            # Publish the CURRENT transition, not the pre-handoff one. When a
            # retired pid is replaced mid-retirement the registry returned a bare
            # False while `status()["process_diagnostic"]` still described the
            # process as it was BEFORE the handoff, so a caller could not tell a
            # handoff from a permission failure or a wedged daemon -- three very
            # different operator actions behind one silent False.
            # `PROCESS_IDENTITY_REUSED` already carries both the recorded and the
            # observed birth identity, which is exactly the old-and-new pair a
            # handoff needs, so nothing new is invented here.
            self._publish_retirement_transition(diagnostic)
            if diagnostic.reason is LocalProcessReason.PROCESS_IDENTITY_REUSED:
                return "replaced"
            # `pid_is_serving` is the ONE predicate separating "gone" from "an
            # exited-but-unreaped corpse that still answers os.kill(pid, 0) and
            # still reports its original start ticks". The fence above already
            # routes a zombie to PROCESS_NOT_FOUND, so the two spellings agree
            # rather than being a second copy that can drift apart -- which is
            # exactly what this loop used to carry.
            if diagnostic.reason is LocalProcessReason.PROCESS_NOT_FOUND or not pid_is_serving(service_pid):
                return "exited"
            return "unproven"

        deadline = self.clock() + grace_seconds
        process_state = retained_process_state()
        while process_state == "current" and self.clock() < deadline:
            self.sleep(0.03)
            process_state = retained_process_state()
        if process_state == "replaced":
            return False
        if process_state == "current":
            # ONE destructive owner performs the escalation and reports what it
            # actually did; this function no longer holds a private copy of the
            # SIGTERM/grace/SIGKILL algorithm or of the fence that guards it.
            outcome = self._terminate_recorded_generation(record, service_pid, claim_state=claim_state)
            self._process_diagnostic = {**self._process_diagnostic, "termination": outcome.as_dict()}
            if not outcome.confirmed_dead:
                return False
            process_state = retained_process_state()
        if process_state != "exited" or self._remove_stale_record() is not True:
            return False
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            return True
        return True

    def _publish_retirement_transition(self, diagnostic: LocalProcessDiagnostic) -> None:
        """Publish the CURRENT transition so a caller can act on what just happened."""

        self._process_diagnostic = {
            **diagnostic.as_dict(),
            "transition": LOCAL_SERVICE_RETIREMENT_TRANSITIONS.get(
                diagnostic.reason,
                LOCAL_SERVICE_TRANSITION_IDENTITY_UNPROVEN,
            ),
            "service": self.spec.name,
            "socket": str(self.socket_path),
        }

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.2,
        protocol_version: int | None = None,
    ) -> dict[str, Any]:
        request_protocol_version = self.spec.protocol_version if protocol_version is None else protocol_version
        request_payload = {"action": method, "protocol_version": request_protocol_version, **(payload or {})}

        def attempt(attempt_timeout: float) -> tuple[dict[str, Any], bytes]:
            try:
                envelope = new_envelope(self.spec.name, method, request_payload, timeout_seconds=attempt_timeout)
                response, binary = request(
                    self.socket_path,
                    envelope,
                    timeout_seconds=attempt_timeout,
                    fallback_legacy=True,
                )
            except (OSError, LocalRpcError) as exc:
                self.note_rpc_failure(type(exc).__name__)
                return {
                    "ok": False,
                    "error": redact_local_service_text(exc),
                    "_transport_error": local_service_failure_reason(exc),
                    "exception_type": type(exc).__name__,
                    "cause": local_service_exception_cause(exc),
                }, b""
            return response if isinstance(response, dict) else {}, binary

        response, _binary = retry_local_service_prehandler_busy(
            attempt,
            lambda result: result[0],
            timeout,
            clock=self.clock,
            sleep=self.sleep,
        )
        return response

    def healthy(self) -> bool:
        response = self._request("ping", timeout=0.15)
        service_version = int(response.get("version") or response.get("required_protocol_version") or 0)
        if service_version > self.spec.protocol_version:
            if self._is_dead_launcher_survivor(int(response.get("pid") or 0)):
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
        if healthy and self._is_dead_launcher_survivor(int(response.get("pid") or 0)):
            # The launcher that created this daemon is gone. Whether the daemon
            # survives is the adoption transaction's answer, not this probe's:
            # under a caller-shared root a successor may inherit it, and under a
            # managed-private root there is no successor and it must be stopped.
            may_reclaim, _claim_state = self._dead_launcher_survivor_decision(int(response.get("pid") or 0))
            if may_reclaim:
                self.invalidate_rpc_health()
                return False
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
            # `launcher_pid` scopes a group to the port that asked for it; it is
            # NOT authority. A bare integer proves nothing after that pid exits or
            # is recycled, which is why the fenced `supervisor` record below --
            # host, boot, pid AND process-start identity -- is what every
            # destructive decision reads. First-writer-wins is enforced by
            # `_merge_supervision_provenance`, not by this dict.
            "launcher_pid": os.getpid(),
            "launcher_port": local_service_launch_port(),
            "supervisor": self.host_identity.process_record_fields(),
            # The directory this record belongs to. Two YOLOmux installations on
            # one host must never read each other's records as their own.
            "namespace": str(self.service_dir),
            # The spawn epoch, re-provable live from the child's inherited
            # environment. Without it a survivor of generation N is
            # indistinguishable from the live process of generation N+1.
            "spawn_generation": self._resolved_spawn_generation(pid),
            "root_sharing": root_sharing_mode(private_root=self.managed_private_root),
            "claim_id": self.claim.claim_id if self.claim is not None else "",
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

    def _resolved_spawn_generation(self, pid: int) -> str:
        """Return the spawn marker this exact pid still proves, or the empty string.

        Read live from the process rather than remembered, because the point of
        the marker is to survive the death of whatever remembered it. A registry
        that spawned this generation holds the same value in `spawn_ownership`;
        a registry that ADOPTED a running daemon has no memory of it at all and
        must read it from the process, which is exactly what makes generation a
        usable dimension across a launcher restart.
        """

        if int(pid) <= 1:
            return ""
        observed = process_spawn_generation(int(pid))
        if observed:
            return str(observed)
        ownership = self.spawn_ownership
        if ownership is not None and int(ownership.leader_pid) == int(pid):
            return str(ownership.generation_marker)
        return ""

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
        if self._record_refusal_reason:
            self._failure_reason = self._record_refusal_reason
            self.invalidate_rpc_health()
            return False
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
        # The transition is what separates "someone else replaced this pid while
        # we were retiring it" from "we were refused permission" and from "the
        # daemon is wedged". Without it every one of them read as the same
        # unactionable blocked start.
        transition = str(self._process_diagnostic.get("transition") or "")
        transition_text = f", transition={transition}" if transition else ""
        self._failure_reason = redact_local_service_text(
            f"{self.spec.name} start blocked by {stage} "
            f"(record_pid={recorded_pid}, reason={diagnostic_reason}{transition_text})"
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
        with self._record_lock():
            current = self._read_record()
            if int(current.get("pid") or 0) == pid:
                self._remove_stale_record()
                self._release_claim_for(pid)

    def _adopted_child_pid(self) -> int:
        """Return the recorded pid this process may `os.waitpid` for, or 0.

        Arming the adopted reaper is arming `waitpid`, and `waitpid` is only ever
        legitimate for THIS process's own child. That is the proof, and it is a
        live measurement taken through the one native per-pid reader beside the
        identity fence -- not `launcher_pid` and not the record's `supervisor`,
        because two successors of the same daemon read the SAME values out of the
        same record and under a caller-shared root both of those values can
        legitimately be this very process.

        The guard used to be "I hold no Popen" plus "the record is current",
        which every caller of a healthy shared daemon satisfies. A second server
        that merely leased the daemon armed a thread that waited on a process it
        never parented, got `ChildProcessError` immediately, and walked straight
        into retiring a LIVE daemon another supervisor owns -- stopped only by
        `_remove_stale_record`'s own liveness fence, the second line of defence
        standing in for the missing first one, once per adoption per server.
        """

        if self.process is not None:
            return 0
        record = self._read_record()
        pid = int(record.get("pid") or 0)
        if pid <= 1:
            return 0
        if not process_record_diagnostic(record, host_identity=self.host_identity).current:
            return 0
        if process_parent_id(pid) != os.getpid():
            return 0
        return pid

    def _arm_adopted_reaper_if_owned(self) -> None:
        """Arm only for a daemon this process actually parented.

        The gate lives here rather than only inside `_arm_adopted_reaper` so a
        non-supervising caller does not even attempt it: `ensure_started` reaches
        the healthy-socket early returns on every lease of a shared daemon, and
        "attempted and refused" once per call is still a thread's worth of work
        and a decision this caller has no standing to make.
        """

        if self._adopted_child_pid():
            self._arm_adopted_reaper()

    def _release_claim_for(self, pid: int) -> None:
        """Drop authority once this registry has stopped its own helper.

        A spent claim left behind is not inert: a later pass would read it as
        authority over whatever process next holds that pid. Releasing is part of
        retirement, not cleanup.
        """

        claim = self.claim
        if claim is None or int(claim.pid) != int(pid):
            return
        if not self.claim_ledger().release(claim):
            self.claim_rows = [{
                "pid": int(pid),
                "attempted_action": "release_claim",
                "result": "claim_remove_failed",
                "reason": "claim_release_failed",
            }]
            return
        self.claim = None

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
                # Two different facts arrive as one exception: "this pid was never mine to wait
                # for" and "something else already reaped it". Neither of them is "the child
                # exited", so neither may be turned into a retirement on its own. Ask the ONE
                # zombie-aware liveness fence instead of inferring an exit from a failed wait --
                # that check belongs here, not downstream in `_remove_stale_record`.
                if pid_is_serving(pid):
                    return
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

        Arming is gated on this registry actually HOLDING supervision of that pid. It used to be
        gated only on "I have no Popen" plus "the record is current", which every caller of a
        healthy shared daemon satisfies: a second server that merely leased the daemon armed a
        thread that `waitpid`ed a process it never parented, got `ChildProcessError` immediately,
        and walked straight into retiring a LIVE daemon another supervisor owns. Nothing stopped
        that except `_remove_stale_record`'s own liveness fence -- the second line of defence
        standing in for the missing first one -- and every leasing server started one such thread
        per adoption.
        """
        pid = self._adopted_child_pid()
        if not pid:
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

    def _reap_recorded_child_if_exited(self) -> bool:
        """Recover a child that exited before this generation could adopt it."""

        if self.process is not None or not hasattr(os, "waitpid"):
            return False
        record = self._read_record()
        pid = int(record.get("pid") or 0)
        if (
            pid <= 1
            or int(record.get("launcher_pid") or 0) != os.getpid()
            or process_state(pid) != "Z"
        ):
            return False
        with self._adopted_reaper_lock:
            if self._adopted_reaper_pid == pid:
                return False
            try:
                waited_pid, _status = os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                return False
        if waited_pid != pid:
            return False
        self.invalidate_rpc_health()
        self._retire_record_naming_pid(pid)
        return True

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
        self._reap_recorded_child_if_exited()
        self.prune_stale_runtime_locks_once()
        if self._upgrade_required is not None:
            return False
        if self.recently_healthy():
            self._arm_adopted_reaper_if_owned()
            return True
        if self._terminal_failure:
            return False
        # A healthy ping is not a started service until its identity record is
        # published. When the follow-up status is lost, fall through to bounded
        # startup and retry instead of reporting a success nothing can prove.
        if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
            self._arm_adopted_reaper_if_owned()
            return True
        if self._upgrade_required is not None:
            return False
        if self._terminal_failure:
            return False
        with self.lock:
            if not self.starts_allowed():
                return False
            if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
                self._arm_adopted_reaper_if_owned()
                return True
            if self._upgrade_required is not None:
                return False
            if self._terminal_failure:
                return False
            if self.clock() < self.next_start_at:
                return False
            if self._record_directory_confirmed and (
                self._lock_directory_is_absent or not self.record_path.parent.exists()
            ):
                # This exact owner already published here, so the directory
                # disappearing since is a teardown, not a first start. Taking the
                # lock would mkdir + chmod it back into existence.
                self._record_refusal_reason = redact_local_service_text(
                    f"{self.spec.name} service directory was removed after this owner published "
                    "into it; refusing to recreate it for a start"
                )
                self._failure_reason = self._record_refusal_reason
                return False
            with self._record_lock():
                if self.healthy() and self._publish_record(self._request("status", timeout=0.2)):
                    self._arm_adopted_reaper_if_owned()
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
        ownership = self.spawn_ownership
        if ownership is not None:
            # Published while this process still has direct proof of what it
            # created. That is the only moment the claim can be truthful, and it
            # is what lets a LATER process decide anything about this daemon
            # after this one is gone.
            self.publish_claim(ownership.leader_pid, ownership.generation_marker)
            self._stamp_record_supervision(ownership.leader_pid, generation=ownership.generation_marker)
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
            "root_sharing": root_sharing_mode(private_root=self.managed_private_root),
            "supervisor": self.supervisor_state(),
            "claim": {
                "claim_id": self.claim.claim_id if self.claim is not None else "",
                "pid": int(self.claim.pid) if self.claim is not None else 0,
                "generation": self.claim.generation if self.claim is not None else "",
            },
            "claim_rows": list(self.claim_rows),
            "lifetime": self._read_service_lifetime_record(),
            "terminal_failure": self._terminal_failure,
            "start_exit_count": self._start_exit_count,
            "last_exit_code": self._last_exit_code,
        }

    def supervisor_state(self) -> dict[str, Any]:
        """Name the process currently responsible for this daemon, and whether it inherited it.

        ``transferred`` is the machine-readable answer to "did this daemon
        outlive its launcher?". It is true only when THIS registry won the atomic
        adoption transaction for the recorded pid, so two successors can never
        both report themselves as the transferred supervisor.
        """

        record = self._read_record()
        supervisor = record.get("supervisor")
        if isinstance(supervisor, dict) and supervisor:
            supervisor_pid = int(supervisor.get("pid") or 0)
        else:
            supervisor_pid = int(record.get("launcher_pid") or 0)
        transferred = (
            self._claim_was_adopted
            and self.claim is not None
            and int(self.claim.pid) > 0
            and int(self.claim.pid) == int(record.get("pid") or 0)
        )
        return {"pid": supervisor_pid, "transferred": bool(transferred)}

    def _read_service_lifetime_record(self) -> dict[str, Any]:
        """Read the daemon-side surviving-supervisor record beside the socket.

        Published by the daemon's own lifetime owner, so it stays readable when
        the daemon is too wedged to answer a status RPC -- which is exactly when
        "who retains this process, and has it already been asked to stop?" is the
        question being asked.
        """

        value = read_json_file(self.socket_path.with_suffix(".lifetime.json"), {})
        return value if isinstance(value, dict) else {}

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

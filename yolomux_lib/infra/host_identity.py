# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Stable host identity and fail-closed persisted-process fencing."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import ctypes
import ctypes.util
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Any
import uuid


HOST_ID_OVERRIDE_ENV = "YOLOMUX_HOST_ID"
MACHINE_ID_PATH = Path("/etc/machine-id")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_SAFE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_DARWIN_PROC_PIDTBSDINFO = 3
_DARWIN_MAXCOMLEN = 16
_DARWIN_PROCESS_STATES = {1: "I", 2: "R", 3: "S", 4: "T", 5: "Z"}


class HostIdentityError(RuntimeError):
    """The host or process identity cannot be established safely."""


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * _DARWIN_MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * _DARWIN_MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class ProcessIdentitySnapshot:
    state: str
    start_identity: str


def _darwin_process_identity_snapshot(pid: int) -> ProcessIdentitySnapshot | None:
    library_path = ctypes.util.find_library("proc")
    if not library_path:
        return None
    try:
        library = ctypes.CDLL(library_path, use_errno=True)
        info = _DarwinProcBsdInfo()
        size = ctypes.sizeof(info)
        result = library.proc_pidinfo(int(pid), _DARWIN_PROC_PIDTBSDINFO, 0, ctypes.byref(info), size)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result != size or int(info.pbi_pid) != int(pid) or int(info.pbi_start_tvsec) <= 0:
        return None
    started_microseconds = int(info.pbi_start_tvsec) * 1_000_000 + int(info.pbi_start_tvusec)
    return ProcessIdentitySnapshot(
        state=_DARWIN_PROCESS_STATES.get(int(info.pbi_status), "?"),
        start_identity=f"darwin:{started_microseconds}",
    )


def process_identity_snapshot(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    platform_name: str = sys.platform,
    darwin_reader: Callable[[int], ProcessIdentitySnapshot | None] = _darwin_process_identity_snapshot,
) -> ProcessIdentitySnapshot | None:
    """Return one process state and birth identity from the platform's native owner."""

    clean_pid = int(pid)
    if clean_pid <= 1:
        return None
    try:
        stat_text = (proc_root / str(clean_pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        stat_text = ""
    closing_paren = stat_text.rfind(")")
    if closing_paren >= 0:
        fields = stat_text[closing_paren + 1 :].split()
        if len(fields) > 19:
            try:
                return ProcessIdentitySnapshot(state=fields[0], start_identity=f"proc:{int(fields[19])}")
            except ValueError:
                pass
    if platform_name.casefold() == "darwin":
        # libproc is the authoritative, constant-time owner on macOS. Falling back to one
        # `ps` subprocess per inaccessible PID makes a bounded process-table read take minutes.
        return darwin_reader(clean_pid)
    try:
        completed = runner(
            ("ps", "-o", "state=,lstart=", "-p", str(clean_pid)),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    state, separator, started = " ".join(str(completed.stdout or "").split()).partition(" ")
    if completed.returncode != 0 or not separator or not state or not started:
        return None
    return ProcessIdentitySnapshot(state=state[0], start_identity=f"ps:{started}")


class LocalProcessReason(str, Enum):
    CURRENT = "current_local_process"
    FOREIGN_HOST = "foreign_host"
    PREVIOUS_BOOT = "previous_boot"
    MISSING_HOST_IDENTITY = "missing_host_identity"
    MISSING_BOOT_IDENTITY = "missing_boot_identity"
    INVALID_PID = "invalid_pid"
    MISSING_PROCESS_START_IDENTITY = "missing_process_start_identity"
    PROCESS_NOT_FOUND = "process_not_found"
    PROCESS_IDENTITY_REUSED = "process_identity_reused"
    PROCESS_IDENTITY_UNAVAILABLE = "process_identity_unavailable"


def normalize_stable_host_id(value: object, *, source: str) -> str:
    """Return one path-safe stable host key or reject the configured value."""

    normalized = str(value or "").strip().lower()
    if normalized in {"", ".", ".."} or _SAFE_ID_RE.fullmatch(normalized) is None:
        raise HostIdentityError(
            f"invalid stable host ID from {source}; use 1-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return normalized


def process_start_identity(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Return a stable kernel/process-table birth identity for one live PID."""
    snapshot = process_identity_snapshot(pid, proc_root=proc_root, runner=runner)
    return snapshot.start_identity if snapshot is not None else None


def process_start_ticks(identity: object) -> int | None:
    """Extract the numeric birth counter from a native process-start identity."""

    text = str(identity or "")
    prefix, separator, value = text.partition(":")
    if not separator or prefix not in {"proc", "darwin"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _read_required_identity(path: Path, *, label: str, missing_hint: str = "") -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        hint = f"; {missing_hint}" if missing_hint else ""
        raise HostIdentityError(f"cannot read {label} from {path}{hint}") from exc
    return normalize_stable_host_id(value, source=str(path))


def _host_id_override_state(environ: Mapping[str, str]) -> tuple[bool, str]:
    if HOST_ID_OVERRIDE_ENV not in environ:
        return False, ""
    return True, normalize_stable_host_id(environ[HOST_ID_OVERRIDE_ENV], source=HOST_ID_OVERRIDE_ENV)


def _darwin_command_identity(
    command: tuple[str, ...],
    pattern: str,
    *,
    label: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Read one stable macOS identity without weakening the fail-closed fence."""
    try:
        completed = runner(command, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HostIdentityError(f"cannot read macOS {label}") from exc
    match = re.search(pattern, str(completed.stdout or ""), flags=re.IGNORECASE)
    if completed.returncode != 0 or match is None:
        raise HostIdentityError(f"cannot read macOS {label}")
    return normalize_stable_host_id(match.group(1), source=" ".join(command))


def _darwin_stable_host_id(*, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    return _darwin_command_identity(
        ("ioreg", "-rd1", "-c", "IOPlatformExpertDevice"),
        r'"IOPlatformUUID"\s*=\s*"([A-Za-z0-9-]+)"',
        label="platform UUID",
        runner=runner,
    )


def _darwin_boot_id(*, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    boot_time = _darwin_command_identity(
        ("sysctl", "-n", "kern.boottime"),
        r"sec\s*=\s*([0-9]+)",
        label="boot time",
        runner=runner,
    )
    return f"darwin-{boot_time}"


@dataclass(frozen=True)
class HostIdentity:
    """One process instance's durable host, boot, and birth identity."""

    stable_host_id: str
    display_hostname: str
    boot_id: str
    pid: int
    process_start_identity: str
    process_start_ticks: int | None
    instance_nonce: str
    stable_host_id_source: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stable_host_id",
            normalize_stable_host_id(self.stable_host_id, source=self.stable_host_id_source or "HostIdentity"),
        )
        object.__setattr__(self, "boot_id", normalize_stable_host_id(self.boot_id, source="boot identity"))
        object.__setattr__(self, "instance_nonce", normalize_stable_host_id(self.instance_nonce, source="instance nonce"))
        if not str(self.display_hostname or "").strip():
            raise HostIdentityError("display hostname is empty")
        if int(self.pid) <= 1:
            raise HostIdentityError(f"invalid identity pid {self.pid}")
        if not str(self.process_start_identity or "").strip():
            raise HostIdentityError(f"missing process start identity for pid {self.pid}")
        expected_ticks = process_start_ticks(self.process_start_identity)
        if self.process_start_ticks != expected_ticks:
            raise HostIdentityError(
                f"process start ticks {self.process_start_ticks!r} do not match identity {self.process_start_identity!r}"
            )

    @classmethod
    def from_system(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        machine_id_path: Path = MACHINE_ID_PATH,
        boot_id_path: Path = BOOT_ID_PATH,
        hostname_reader: Callable[[], str] = socket.gethostname,
        pid_reader: Callable[[], int] = os.getpid,
        start_identity_reader: Callable[[int], str | None] = process_start_identity,
        nonce_factory: Callable[[], str] | None = None,
        platform_name: str = sys.platform,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> HostIdentity:
        values = os.environ if environ is None else environ
        if HOST_ID_OVERRIDE_ENV in values:
            source = HOST_ID_OVERRIDE_ENV
            stable_host_id = normalize_stable_host_id(values[HOST_ID_OVERRIDE_ENV], source=source)
        elif platform_name == "darwin" and machine_id_path == MACHINE_ID_PATH:
            source = "ioreg IOPlatformUUID"
            stable_host_id = _darwin_stable_host_id(runner=command_runner)
        else:
            source = str(machine_id_path)
            stable_host_id = _read_required_identity(
                machine_id_path,
                label="machine ID",
                missing_hint=f"set {HOST_ID_OVERRIDE_ENV} when machine identity is unavailable",
            )
        display_hostname = str(hostname_reader() or "").strip()
        if not display_hostname:
            raise HostIdentityError("display hostname is empty")
        boot_id = (
            _darwin_boot_id(runner=command_runner)
            if platform_name == "darwin" and boot_id_path == BOOT_ID_PATH
            else _read_required_identity(boot_id_path, label="boot ID")
        )
        pid = int(pid_reader())
        start_identity = start_identity_reader(pid)
        if pid <= 1 or not start_identity:
            raise HostIdentityError(f"cannot establish process start identity for pid {pid}")
        nonce = str((nonce_factory or (lambda: uuid.uuid4().hex))() or "").strip().lower()
        instance_nonce = normalize_stable_host_id(nonce, source="instance nonce")
        return cls(
            stable_host_id=stable_host_id,
            display_hostname=display_hostname,
            boot_id=boot_id,
            pid=pid,
            process_start_identity=str(start_identity),
            process_start_ticks=process_start_ticks(start_identity),
            instance_nonce=instance_nonce,
            stable_host_id_source=source,
        )

    @property
    def path_segment(self) -> str:
        return self.stable_host_id

    def qualify_key(self, kind: str, value: object) -> str:
        clean_kind = normalize_stable_host_id(kind, source="identity key kind")
        return f"{clean_kind}:{self.stable_host_id}:{value}"

    def namespaced_path(self, root: Path, *parts: str) -> Path:
        return Path(root).joinpath(self.path_segment, *parts)

    def process_record_fields(
        self,
        *,
        pid: int | None = None,
        start_identity: str | None = None,
        display_hostname: str | None = None,
        instance_nonce: str | None = None,
    ) -> dict[str, Any]:
        record_pid = self.pid if pid is None else int(pid)
        record_start = self.process_start_identity if start_identity is None and record_pid == self.pid else str(start_identity or "")
        return {
            "stable_host_id": self.stable_host_id,
            "hostname": self.display_hostname if display_hostname is None else str(display_hostname),
            "boot_id": self.boot_id,
            "pid": record_pid,
            "process_start_identity": record_start,
            "process_start_ticks": process_start_ticks(record_start) or 0,
            "instance_nonce": self.instance_nonce if instance_nonce is None else str(instance_nonce),
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            **self.process_record_fields(),
            "stable_host_id_source": self.stable_host_id_source,
        }


class LateHostIdentityOverrideError(HostIdentityError):
    """A changed override was rejected after the effective identity was fixed."""

    reason_code = "late_host_id_override_rejected"

    def __init__(
        self,
        *,
        identity: HostIdentity,
        resolved_override: tuple[bool, str],
        rejected_override: tuple[bool, str],
        rejected_override_valid: bool = True,
    ) -> None:
        self.identity = identity
        self.resolved_override = resolved_override[1] if resolved_override[0] else None
        self.rejected_override = rejected_override[1] if rejected_override[0] else None
        self.rejected_override_valid = bool(rejected_override_valid)
        super().__init__(
            f"{HOST_ID_OVERRIDE_ENV} changed after host identity was resolved; "
            "set it before host identity is resolved"
        )


@lru_cache(maxsize=1)
def _resolved_current_host_identity() -> tuple[HostIdentity, tuple[bool, str]]:
    environ = dict(os.environ)
    return HostIdentity.from_system(environ=environ), _host_id_override_state(environ)


def current_host_identity() -> HostIdentity:
    """Return the fixed process identity; configure the override before the first call."""

    identity, resolved_override = _resolved_current_host_identity()
    raw_current_override = (
        HOST_ID_OVERRIDE_ENV in os.environ,
        str(os.environ.get(HOST_ID_OVERRIDE_ENV, "")),
    )
    try:
        current_override = _host_id_override_state(os.environ)
    except HostIdentityError as exc:
        raise LateHostIdentityOverrideError(
            identity=identity,
            resolved_override=resolved_override,
            rejected_override=raw_current_override,
            rejected_override_valid=False,
        ) from exc
    if current_override != resolved_override:
        raise LateHostIdentityOverrideError(
            identity=identity,
            resolved_override=resolved_override,
            rejected_override=current_override,
        )
    return identity


# Existing tests and embedding callers use the lru seam to isolate process-level
# identity scenarios. Keep that seam while every production call still checks
# whether the environment changed after the cached identity was established.
setattr(current_host_identity, "cache_clear", _resolved_current_host_identity.cache_clear)


@dataclass(frozen=True)
class LocalProcessDiagnostic:
    """Typed result for a persisted record checked against this host and boot."""

    current: bool
    reason: LocalProcessReason
    pid: int
    record_host_id: str
    current_host_id: str
    record_boot_id: str
    current_boot_id: str
    recorded_start_identity: str = ""
    observed_start_identity: str = ""

    def __bool__(self) -> bool:
        return self.current

    @property
    def same_host_and_boot(self) -> bool:
        return self.record_host_id == self.current_host_id and self.record_boot_id == self.current_boot_id

    @property
    def may_remove_stale_record(self) -> bool:
        return self.same_host_and_boot and self.reason in {
            LocalProcessReason.PROCESS_NOT_FOUND,
            LocalProcessReason.PROCESS_IDENTITY_REUSED,
        }

    @property
    def may_remove_unidentifiable_record(self) -> bool:
        """Whether the record file alone may be discarded, with no process action.

        `INVALID_PID` means the record's own PID is 0 or 1.  Zero is not a
        process ID at all and 1 is init/launchd, which this code never spawns,
        so such a record cannot name any service process: it cannot be
        signalled, adopted, matched against a process group, or used to decide
        that a socket is inert.  Deleting the file therefore cannot orphan or
        kill anything -- it only stops the unusable identity from blocking
        every future start on this host and boot, which is how a single lost
        status RPC used to brick a service permanently.

        This is deliberately NOT folded into `may_remove_stale_record`.  That
        property authorizes callers (preflight, background owner, server lease)
        that go on to act on the record's other process fields -- a poisoned
        record still carries a real `pgid` -- so its authority must stay
        limited to records whose PID was proven dead or reused.
        """
        return self.same_host_and_boot and self.reason is LocalProcessReason.INVALID_PID

    def as_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "reason": self.reason.value,
            "pid": self.pid,
            "record_host_id": self.record_host_id,
            "current_host_id": self.current_host_id,
            "record_boot_id": self.record_boot_id,
            "current_boot_id": self.current_boot_id,
            "recorded_start_identity": self.recorded_start_identity,
            "observed_start_identity": self.observed_start_identity,
        }


def recorded_start_identity(record: Mapping[str, Any]) -> str:
    """Return the process-birth identity a persisted record carries, if any."""

    value = str(record.get("process_start_identity") or "").strip()
    if value:
        return value
    try:
        ticks = int(record.get("process_start_ticks") or 0)
    except (TypeError, ValueError):
        return ""
    return f"proc:{ticks}" if ticks > 0 else ""


def is_current_local_process(
    record: Mapping[str, Any],
    *,
    host_identity: HostIdentity | None = None,
    start_identity_reader: Callable[[int], str | None] | None = None,
    pid_probe: Callable[[int], bool] | None = None,
) -> LocalProcessDiagnostic:
    """Fence a persisted process record before any local process or file action."""

    identity = host_identity or current_host_identity()
    record_host_id = str(record.get("stable_host_id") or "").strip().lower()
    record_boot_id = str(record.get("boot_id") or "").strip().lower()
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0

    def result(reason: LocalProcessReason, *, observed: str = "", recorded: str = "") -> LocalProcessDiagnostic:
        return LocalProcessDiagnostic(
            current=reason is LocalProcessReason.CURRENT,
            reason=reason,
            pid=pid,
            record_host_id=record_host_id,
            current_host_id=identity.stable_host_id,
            record_boot_id=record_boot_id,
            current_boot_id=identity.boot_id,
            recorded_start_identity=recorded,
            observed_start_identity=observed,
        )

    if not record_host_id:
        return result(LocalProcessReason.MISSING_HOST_IDENTITY)
    if record_host_id != identity.stable_host_id:
        return result(LocalProcessReason.FOREIGN_HOST)
    if not record_boot_id:
        return result(LocalProcessReason.MISSING_BOOT_IDENTITY)
    if record_boot_id != identity.boot_id:
        return result(LocalProcessReason.PREVIOUS_BOOT)
    if pid <= 1:
        return result(LocalProcessReason.INVALID_PID)
    recorded_start = recorded_start_identity(record)
    if not recorded_start:
        return result(LocalProcessReason.MISSING_PROCESS_START_IDENTITY)
    read_start_identity = start_identity_reader or process_start_identity
    try:
        observed_start = read_start_identity(pid)
    except ProcessLookupError:
        return result(LocalProcessReason.PROCESS_NOT_FOUND, recorded=recorded_start)
    except PermissionError:
        return result(LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE, recorded=recorded_start)
    except OSError:
        return result(LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE, recorded=recorded_start)
    if not observed_start:
        if pid_probe is not None:
            try:
                alive_without_identity = bool(pid_probe(pid))
            except ProcessLookupError:
                alive_without_identity = False
            except (PermissionError, OSError):
                return result(LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE, recorded=recorded_start)
            if alive_without_identity:
                return result(LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE, recorded=recorded_start)
        return result(LocalProcessReason.PROCESS_NOT_FOUND, recorded=recorded_start)
    if str(observed_start) != recorded_start:
        return result(LocalProcessReason.PROCESS_IDENTITY_REUSED, observed=str(observed_start), recorded=recorded_start)
    if pid_probe is not None:
        try:
            alive = bool(pid_probe(pid))
        except ProcessLookupError:
            alive = False
        except (PermissionError, OSError):
            return result(LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE, observed=str(observed_start), recorded=recorded_start)
        if not alive:
            return result(LocalProcessReason.PROCESS_NOT_FOUND, observed=str(observed_start), recorded=recorded_start)
    return result(LocalProcessReason.CURRENT, observed=str(observed_start), recorded=recorded_start)

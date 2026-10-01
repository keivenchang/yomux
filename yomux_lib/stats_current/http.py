# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authenticated, current-only HTTP forwarding policy for YO!stats."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs

from yomux_lib.stats_current import protocol, resolution as stats_resolution
from yomux_lib.local_services.client import local_service_failure_is_transient

MAX_QUERY_BYTES = 2_048
CLIENT_ID_HMAC_DOMAIN = b"yolomux-stats-client-v1\x00"
MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
LOGGER = logging.getLogger(__name__)


class SnapshotClient(Protocol):
    def ensure_started(self) -> bool: ...

    def retry(self) -> bool: ...

    def status(self) -> dict[str, object]: ...

    def snapshot(
        self,
        request: protocol.SnapshotRequest | Mapping[str, object],
    ) -> tuple[dict[str, object], bytes]: ...

    def delta(
        self,
        request: protocol.DeltaRequest | Mapping[str, object],
    ) -> tuple[dict[str, object], bytes]: ...


@dataclass(frozen=True, slots=True)
class SnapshotHttpResult:
    status: HTTPStatus
    body: bytes = b""
    payload: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class StatsStreamResult:
    status: HTTPStatus
    metadata: Mapping[str, object]
    body: bytes = b""


# Retain the old import name while callers migrate to the shared snapshot/delta result.
DeltaStreamResult = StatsStreamResult


def _unavailable(
    reason: object = "statsd unavailable",
    *,
    terminal: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "unavailable",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "reason": str(reason or "statsd unavailable")[:256],
    }
    if terminal:
        result["terminal"] = True
    return result


def _unsupported(reason: str) -> protocol.UnsupportedWire:
    return protocol.unsupported_response(reason)


def _transient_not_ready(metadata: Mapping[str, object]) -> bool:
    """Recognize bounded daemon-busy replies without hiding terminal failures."""
    return local_service_failure_is_transient(metadata)


def _delta_pending(reason: str = "statsd is refreshing") -> dict[str, object]:
    return {
        "status": "pending",
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "retry_after_seconds": 1,
        "reason": reason,
    }


def parse_http_snapshot_query(raw_query: str) -> protocol.SnapshotRequest:
    """Parse one bounded query without accepting aliases, blanks, or duplicates."""
    if not isinstance(raw_query, str):
        raise protocol.UnsupportedRequest(_unsupported("query must be text"))
    if len(raw_query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise protocol.UnsupportedRequest(_unsupported("query is too large"))
    if MALFORMED_ESCAPE.search(raw_query):
        raise protocol.UnsupportedRequest(_unsupported("query contains a malformed escape"))
    values = parse_qs(raw_query, keep_blank_values=True, strict_parsing=False)
    duplicate = sorted(name for name, items in values.items() if len(items) != 1)
    if duplicate:
        raise protocol.UnsupportedRequest(_unsupported(f"duplicate query parameters: {duplicate}"))
    return protocol.parse_snapshot_request({name: items[0] for name, items in values.items()})


def parse_http_delta_query(raw_query: str) -> protocol.DeltaRequest:
    """Parse the exact numeric delta cursor without accepting AUTO or aliases."""

    if not isinstance(raw_query, str):
        raise protocol.UnsupportedRequest(_unsupported("query must be text"))
    if len(raw_query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise protocol.UnsupportedRequest(_unsupported("query is too large"))
    if MALFORMED_ESCAPE.search(raw_query):
        raise protocol.UnsupportedRequest(_unsupported("query contains a malformed escape"))
    values = parse_qs(raw_query, keep_blank_values=True, strict_parsing=False)
    duplicate = sorted(name for name, items in values.items() if len(items) != 1)
    if duplicate:
        raise protocol.UnsupportedRequest(_unsupported(f"duplicate query parameters: {duplicate}"))
    return protocol.parse_delta_request({name: items[0] for name, items in values.items()})


def bound_client_id(secret: bytes, authenticated_username: str, browser_client_id: str) -> str:
    """Bind a browser-local identity to the authenticated account without exposing either."""
    if not isinstance(secret, bytes) or len(secret) < 16:
        raise ValueError("client binding secret must contain at least 16 bytes")
    username = str(authenticated_username or "").strip()
    if not username:
        raise ValueError("authenticated username must be non-empty")
    normalized_browser_id = browser_client_id.strip()
    material = username.encode("utf-8") + b"\x00" + normalized_browser_id.encode("utf-8")
    digest = hmac.new(secret, CLIENT_ID_HMAC_DOMAIN + material, hashlib.sha256).hexdigest()
    return f"stats-{digest[:32]}"


class StatsHttpForwarder:
    """Map authenticated HTTP snapshots onto the sole current RPC without payload work."""

    def __init__(self, client: SnapshotClient, *, client_binding_secret: bytes):
        self.client = client
        self.client_binding_secret = client_binding_secret
        self._logged_unavailable_reason = ""

    def capabilities(self) -> Mapping[str, object]:
        """Expose the resolution matrix plus the one browser-safe recovery outcome."""
        payload = dict(stats_resolution.wire_capabilities())
        migration = self.client.status().get("migration")
        if not isinstance(migration, Mapping):
            return payload
        state = str(migration.get("state") or "")
        result = str(migration.get("result") or "")
        if state != "ready" or result not in {"existing", "activated", "recovered"}:
            return payload
        issue_kinds = migration.get("issue_kinds")
        if not isinstance(issue_kinds, (list, tuple)):
            issue_kinds = ()
        payload["migration"] = {
            "state": state,
            "result": result,
            "issue_kinds": [str(kind)[:80] for kind in issue_kinds[:16] if isinstance(kind, str)],
        }
        return payload

    def _startup_failure(self) -> Mapping[str, object] | None:
        if self.client.ensure_started():
            self._logged_unavailable_reason = ""
            return None
        status = self.client.status()
        if _transient_not_ready(status):
            return status
        if status.get("status") == "upgrade_required" or status.get("error_code") == "upgrade_required":
            return status
        unavailable = _unavailable(
            status.get("reason") or status.get("error"),
            terminal=status.get("terminal") is True,
        )
        reason = str(unavailable["reason"])
        if reason != self._logged_unavailable_reason:
            LOGGER.warning("YO!stats unavailable: %s", reason)
            self._logged_unavailable_reason = reason
        return unavailable

    def retry(self) -> Mapping[str, object]:
        if self.client.retry():
            self._logged_unavailable_reason = ""
            return {"ok": True, "status": "ready"}
        return dict(self._startup_failure() or {"ok": True, "status": "ready"})

    def snapshot(self, raw_query: str, *, authenticated_username: str) -> SnapshotHttpResult:
        result = self.snapshot_stream(
            raw_query,
            authenticated_username=authenticated_username,
        )
        if result.status == HTTPStatus.OK:
            return SnapshotHttpResult(result.status, body=result.body)
        if result.status == HTTPStatus.NOT_MODIFIED:
            return SnapshotHttpResult(result.status)
        return SnapshotHttpResult(result.status, payload=result.metadata)

    def snapshot_stream(
        self,
        raw_query: str,
        *,
        authenticated_username: str,
    ) -> StatsStreamResult:
        try:
            requested = parse_http_snapshot_query(raw_query)
        except protocol.UnsupportedRequest as error:
            return StatsStreamResult(HTTPStatus.BAD_REQUEST, error.response)
        startup_failure = self._startup_failure()
        if startup_failure is not None:
            if _transient_not_ready(startup_failure):
                return StatsStreamResult(
                    HTTPStatus.ACCEPTED,
                    protocol.pending_response(
                        requested,
                        1,
                        "statsd is refreshing",
                    ),
                )
            status = (
                HTTPStatus.UPGRADE_REQUIRED
                if startup_failure.get("status") == "upgrade_required"
                or startup_failure.get("error_code") == "upgrade_required"
                else HTTPStatus.FAILED_DEPENDENCY
            )
            return StatsStreamResult(status, startup_failure)

        request = protocol.SnapshotRequest(
            requested.range_seconds,
            requested.resolution,
            requested.resolution_seconds,
            bound_client_id(
                self.client_binding_secret,
                authenticated_username,
                requested.client_id,
            ),
            requested.since_generation,
            requested.chunk_index,
            requested.chunk_generation,
        )
        metadata, body = self.client.snapshot(request)
        state = metadata.get("status")

        if _transient_not_ready(metadata) and not body:
            return StatsStreamResult(
                HTTPStatus.ACCEPTED,
                protocol.pending_response(
                    request,
                    1,
                    "statsd is refreshing",
                ),
            )
        if metadata.get("ok") is True and metadata.get("not_modified") is True and not body:
            return StatsStreamResult(HTTPStatus.NOT_MODIFIED, metadata)
        if metadata.get("ok") is True and body and metadata.get("content_type") == "application/json":
            return StatsStreamResult(HTTPStatus.OK, metadata, body)
        if state == "pending" and not body:
            return StatsStreamResult(HTTPStatus.ACCEPTED, metadata)
        if state == "unsupported" and not body:
            return StatsStreamResult(HTTPStatus.BAD_REQUEST, metadata)
        if (state == "upgrade_required" or metadata.get("error_code") == "upgrade_required") and not body:
            return StatsStreamResult(HTTPStatus.UPGRADE_REQUIRED, metadata)
        return StatsStreamResult(HTTPStatus.FAILED_DEPENDENCY, _unavailable())

    def delta(self, raw_query: str, *, authenticated_username: str) -> SnapshotHttpResult:
        result = self.delta_stream(
            raw_query,
            authenticated_username=authenticated_username,
        )
        if result.status == HTTPStatus.OK:
            return SnapshotHttpResult(result.status, body=result.body)
        return SnapshotHttpResult(result.status, payload=result.metadata)

    def delta_stream(
        self,
        raw_query: str,
        *,
        authenticated_username: str,
    ) -> DeltaStreamResult:
        try:
            requested = parse_http_delta_query(raw_query)
        except protocol.UnsupportedRequest as error:
            return DeltaStreamResult(HTTPStatus.BAD_REQUEST, error.response)
        startup_failure = self._startup_failure()
        if startup_failure is not None:
            if _transient_not_ready(startup_failure):
                return DeltaStreamResult(
                    HTTPStatus.ACCEPTED,
                    _delta_pending(),
                )
            status = (
                HTTPStatus.UPGRADE_REQUIRED
                if startup_failure.get("status") == "upgrade_required"
                or startup_failure.get("error_code") == "upgrade_required"
                else HTTPStatus.FAILED_DEPENDENCY
            )
            return DeltaStreamResult(status, startup_failure)
        request = protocol.DeltaRequest(
            requested.range_seconds,
            requested.resolution_seconds,
            bound_client_id(
                self.client_binding_secret,
                authenticated_username,
                requested.client_id,
            ),
            requested.after_cache_generation,
            requested.after_revision,
        )
        metadata, body = self.client.delta(request)
        state = metadata.get("status")
        if _transient_not_ready(metadata) and not body:
            return DeltaStreamResult(HTTPStatus.ACCEPTED, _delta_pending())
        if metadata.get("ok") is True and metadata.get("not_modified") is True and not body:
            return DeltaStreamResult(HTTPStatus.NOT_MODIFIED, metadata)
        if metadata.get("ok") is True and body and metadata.get("content_type") == "application/json":
            return DeltaStreamResult(HTTPStatus.OK, metadata, body)
        if state == "repair_required" and not body:
            return DeltaStreamResult(HTTPStatus.CONFLICT, metadata)
        if state == "queued" and metadata.get("ok") is True and not body:
            return DeltaStreamResult(HTTPStatus.ACCEPTED, metadata)
        if state == "pending" and not body:
            return DeltaStreamResult(HTTPStatus.ACCEPTED, metadata)
        if state == "unsupported" and not body:
            return DeltaStreamResult(HTTPStatus.BAD_REQUEST, metadata)
        if (state == "upgrade_required" or metadata.get("error_code") == "upgrade_required") and not body:
            return DeltaStreamResult(HTTPStatus.UPGRADE_REQUIRED, metadata)
        if state == "unavailable" and not body:
            return DeltaStreamResult(HTTPStatus.FAILED_DEPENDENCY, metadata)
        return DeltaStreamResult(HTTPStatus.FAILED_DEPENDENCY, _unavailable())


# --- statsd resource projection, /livez and /readyz ---------------------------------------------
# Specified in tools/measurements/statsd-readyz-spec-2026-08-25.md. Three measured constraints
# shape everything below and are re-verified in this worktree against live pid 2088396:
#
#   * `_status()` opens `with self.work_lock:` at service.py:4736 -- the lock the materializer
#     worker holds across a build. NEITHER endpoint may route through it. A health check that
#     blocks behind the daemon it is checking reports nothing.
#   * `/proc/<pid>/smaps_rollup` is the only source of PSS/USS and it takes the TARGET's
#     `mmap_read_lock` for the duration. It is excluded from every health path, so this module
#     reports no PSS or USS field at all rather than a cheap number wearing an expensive name.
#   * The memory budget quantity is `RssAnon + VmSwap`, never `VmRSS`. Measured on live statsd
#     here: VmRSS reads 61.36% BELOW VmHWM while the sum reads 1.08% below it, because RSS falls
#     when the kernel swaps a process out. A daemon can breach its budget and look healthy.

PROC_STATUS_BYTES = (
    "RssAnon", "RssFile", "RssShmem", "VmSwap", "VmHWM", "VmPeak", "VmRSS",
)
PROC_STATUS_COUNTS = ("Threads", "FDSize", "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")
# Field 3 of /proc/<pid>/stat. R/S/D are alive; Z is a corpse and T is stopped.
LIVE_PROCESS_STATES = frozenset("RSD")
# A flat process is only wedged if it stays flat. Expressed as a multiple of the daemon's own
# measured full-build time rather than a literal, because a bigger store legitimately builds
# longer -- the spec's open item 9.4. The floor covers a daemon that has never reported one.
LIVEZ_STALL_BUILD_MULTIPLE = 4.0
LIVEZ_STALL_FLOOR_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class HealthBudgets:
    """Ceilings a caller configures. Absent means the condition is not evaluated."""

    memory_bytes: int | None = None
    open_fds: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessSample:
    """Everything about statsd readable WITHOUT entering it.

    Read by the CALLER from `/proc` and `stat()`. statsd is not scheduled, takes no lock and
    does not learn it happened, which is why `/livez` built from this cannot be blocked by the
    condition it exists to detect.
    """

    pid: int
    sampled_at: float
    exists: bool
    error: str = ""
    state: str = ""
    cpu_ticks: int = 0
    voluntary_ctxt_switches: int = 0
    nonvoluntary_ctxt_switches: int = 0
    anon_bytes: int = 0
    file_bytes: int = 0
    shmem_bytes: int = 0
    swap_bytes: int = 0
    peak_rss_bytes: int = 0
    peak_vm_bytes: int = 0
    rss_bytes: int = 0
    threads: int = 0
    fd_size: int = 0
    open_fds: int = 0
    read_bytes: int | None = None
    write_bytes: int | None = None

    @property
    def memory_bytes(self) -> int:
        """The budget quantity. `RssAnon + VmSwap`, never `VmRSS` -- see the module comment."""

        return self.anon_bytes + self.swap_bytes


@dataclass(frozen=True, slots=True)
class StoreSizes:
    """Sizes from `stat()`. Never `PRAGMA page_count`, which needs a connection and a read
    transaction against the database statsd is writing."""

    database_bytes: int = 0
    wal_bytes: int = 0
    shm_bytes: int = 0
    temp_dir: str = ""
    temp_dir_free_bytes: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class HealthVerdict:
    ok: bool
    status: HTTPStatus
    failures: tuple[str, ...]
    payload: Mapping[str, object]
    retry_after_seconds: int | None = None


def _status_values(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, raw = line.partition(":")
        raw = raw.strip()
        if name in PROC_STATUS_BYTES and raw.endswith("kB"):
            values[name] = int(raw[:-2].strip()) * 1024
        elif name in PROC_STATUS_COUNTS:
            values[name] = int(raw)
    return values


def read_process_sample(
    pid: int, *, proc_root: str | Path = "/proc", now: float | None = None,
) -> ProcessSample:
    """Sample statsd from outside it. Fails CLOSED: any unreadable input yields `exists=False`.

    Reads `status`, `stat`, `io` and the fd directory -- measured on this host at 9.7, 6.8, 6.1
    and 6.2 microseconds median against live statsd. `smaps_rollup` is deliberately absent.
    """

    root = Path(proc_root) / str(pid)
    sampled_at = time.time() if now is None else now
    try:
        status = _status_values((root / "status").read_text(encoding="utf-8", errors="replace"))
        # comm can contain spaces and parentheses, so the tail is split off the LAST ") ".
        # tail[0] is field 3 (state); utime and stime are fields 14 and 15, i.e. tail[11:13].
        _, _, tail = (root / "stat").read_text(encoding="utf-8", errors="replace").rpartition(") ")
        fields = tail.split()
        state = fields[0]
        cpu_ticks = int(fields[11]) + int(fields[12])
        open_fds = len(os.listdir(root / "fd"))
    except (OSError, ValueError, IndexError) as error:
        return ProcessSample(pid=pid, sampled_at=sampled_at, exists=False,
                             error=f"{type(error).__name__}: {error}"[:200])
    read_bytes = write_bytes = None
    try:
        # A separate try: /proc/<pid>/io is permission-gated and its absence must not make an
        # otherwise-readable process look dead. Unknown I/O is reported as None, and livez
        # treats unknown as "no evidence of progress" rather than as evidence of a wedge.
        io_values = dict(
            (name, int(raw.strip()))
            for name, _, raw in (
                line.partition(":") for line in
                (root / "io").read_text(encoding="utf-8", errors="replace").splitlines()
            )
            if name in ("read_bytes", "write_bytes")
        )
        read_bytes, write_bytes = io_values.get("read_bytes"), io_values.get("write_bytes")
    except (OSError, ValueError):
        pass
    return ProcessSample(
        pid=pid, sampled_at=sampled_at, exists=True, state=state, cpu_ticks=cpu_ticks,
        voluntary_ctxt_switches=status.get("voluntary_ctxt_switches", 0),
        nonvoluntary_ctxt_switches=status.get("nonvoluntary_ctxt_switches", 0),
        anon_bytes=status.get("RssAnon", 0), file_bytes=status.get("RssFile", 0),
        shmem_bytes=status.get("RssShmem", 0), swap_bytes=status.get("VmSwap", 0),
        peak_rss_bytes=status.get("VmHWM", 0), peak_vm_bytes=status.get("VmPeak", 0),
        rss_bytes=status.get("VmRSS", 0), threads=status.get("Threads", 0),
        fd_size=status.get("FDSize", 0), open_fds=open_fds,
        read_bytes=read_bytes, write_bytes=write_bytes,
    )


def read_store_sizes(database_path: str | Path, *, temp_dir: str | Path | None = None) -> StoreSizes:
    """Database, WAL and SHM sizes from `stat()`, plus the resolved SQLite temp directory.

    SQLite unlinks its temp files immediately, so they have no stat-able path. The
    operationally interesting failure is "the temp filesystem filled", so the free space of the
    resolved directory is reported instead of a size that cannot be observed.
    """

    path = Path(database_path)
    resolved = Path(
        temp_dir if temp_dir is not None
        else os.environ.get("SQLITE_TMPDIR") or os.environ.get("TMPDIR") or "/tmp"
    )
    sizes: dict[str, int] = {}
    error = ""
    for field, candidate in (("database_bytes", path), ("wal_bytes", Path(f"{path}-wal")),
                             ("shm_bytes", Path(f"{path}-shm"))):
        try:
            sizes[field] = candidate.stat().st_size
        except FileNotFoundError:
            sizes[field] = 0
        except OSError as failure:
            error = f"{type(failure).__name__}: {failure}"[:200]
            sizes[field] = 0
    try:
        usage = shutil.disk_usage(resolved)
        free = usage.free
    except OSError as failure:
        error = error or f"{type(failure).__name__}: {failure}"[:200]
        free = 0
    return StoreSizes(temp_dir=str(resolved), temp_dir_free_bytes=free, error=error, **sizes)


def project_resource_state(
    sample: ProcessSample,
    sizes: StoreSizes,
    controls: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """One side-effect-free projection. `controls` is a SNAPSHOT the caller already fetched.

    This function never touches the service object, so it cannot take `work_lock` or
    `cache_lock` however it is called -- the property is structural, not a promise. The
    in-process fields arrive as a plain mapping from the `resource_state` RPC, which reads
    plain attributes and takes no lock.

    Sampled, not transactional: the external fields and the control fields are read at
    different instants and are not mutually consistent. That is the correct trade -- a health
    endpoint wants a recent inconsistent answer, not a consistent one that waited 900 ms.
    """

    control = dict(controls or {})
    memory = {
        "anon_bytes": sample.anon_bytes,
        "file_bytes": sample.file_bytes,
        "shmem_bytes": sample.shmem_bytes,
        "swap_bytes": sample.swap_bytes,
        "rss_bytes": sample.rss_bytes,
        "peak_rss_bytes": sample.peak_rss_bytes,
        "peak_vm_bytes": sample.peak_vm_bytes,
        # Carried explicitly so no consumer has to know to add the terms. RSS alone reads
        # 61.36% low against VmHWM on this daemon because the kernel swapped it out.
        "budget_bytes": sample.memory_bytes,
        "budget_definition": "RssAnon+VmSwap",
        # Named so nobody mistakes these for PSS. file_bytes over-counts true Pss_File by a
        # near-constant ~13 MB of libc and libpython text shared with every Python process here.
        "source": "status",
        "pss_available": False,
        "pss_reason": "smaps_rollup costs ~20.8 ms and takes the target's mmap_read_lock",
    }
    return {
        "ok": sample.exists and not sample.error,
        "pid": sample.pid,
        "sampled_at": sample.sampled_at,
        "process": {
            "exists": sample.exists, "state": sample.state, "cpu_ticks": sample.cpu_ticks,
            "threads": sample.threads, "open_fds": sample.open_fds, "fd_size": sample.fd_size,
            "voluntary_ctxt_switches": sample.voluntary_ctxt_switches,
            "nonvoluntary_ctxt_switches": sample.nonvoluntary_ctxt_switches,
            "read_bytes": sample.read_bytes, "write_bytes": sample.write_bytes,
            "error": sample.error,
        },
        "memory": memory,
        "store": {
            "database_bytes": sizes.database_bytes, "wal_bytes": sizes.wal_bytes,
            "shm_bytes": sizes.shm_bytes, "temp_dir": sizes.temp_dir,
            "temp_dir_free_bytes": sizes.temp_dir_free_bytes, "error": sizes.error,
        },
        "control": control,
        "control_available": bool(control),
    }


def _control_int(control: Mapping[str, object], name: str) -> int | None:
    value = control.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def readyz(
    sample: ProcessSample,
    sizes: StoreSizes,
    controls: Mapping[str, object] | None,
    *,
    budgets: HealthBudgets = HealthBudgets(),
) -> HealthVerdict:
    """Can this process serve a CORRECT snapshot right now. Fails closed.

    Deliberately not a rename of `cache_ready_event`: a sibling lane measured that on 6 of 6
    cold starts the event fires while the served window's ring is still staged, and a snapshot
    at that instant is legitimately refused. Condition 2 is the one the event omits.

    `status.queue.pending` is `int(bool(...))`, so it reads 1 whether one cell is staged or the
    whole ring -- measured at 1,248 staged cells at the readiness instant. This reads
    `pending_cells`, never `queue.pending`.
    """

    failures: list[str] = []
    control = dict(controls or {})
    if not sample.exists:
        failures.append(f"process: not readable ({sample.error or 'absent'})")
    if not control:
        # Unknown state is not ready. An unreachable daemon cannot assert its own readiness.
        failures.append("control: resource_state unavailable")
    else:
        cache_generation = _control_int(control, "cache_generation")
        if cache_generation is None or cache_generation <= 0:
            # Positive, not merely present: the projector reports literal 0 before the first
            # build and the daemon answers RPC with that zero for ~24 s after start.
            failures.append(f"cache_generation: {cache_generation!r} is not > 0")
        pending_cells = _control_int(control, "pending_cells")
        if pending_cells is None or pending_cells != 0:
            failures.append(f"pending_cells: {pending_cells!r} staged, want 0")
        if control.get("ring_failure"):
            failures.append(f"ring_writer.failure: {control['ring_failure']!r}")
        if control.get("materializer_state") == "failed":
            failures.append("materializer.state: failed")
        if control.get("migration_state") != "ready":
            failures.append(f"migration.state: {control.get('migration_state')!r}, want 'ready'")
        if control.get("build_failed_since_publication"):
            failures.append("build.failed increased since the last successful publication")
        if control.get("owed_startup_slots"):
            failures.append(f"recovery: {control['owed_startup_slots']!r} owed startup slots")
    if budgets.memory_bytes is not None and sample.memory_bytes > budgets.memory_bytes:
        failures.append(
            f"memory: {sample.memory_bytes} bytes (RssAnon+VmSwap) over "
            f"{budgets.memory_bytes}"
        )
    if budgets.open_fds is not None and sample.open_fds > budgets.open_fds:
        failures.append(f"open_fds: {sample.open_fds} over {budgets.open_fds}")
    ok = not failures
    payload = project_resource_state(sample, sizes, control)
    # Every failing condition, not the first: one cause per poll costs an operator one restart
    # cycle per cause.
    payload["ready"] = ok
    payload["failures"] = tuple(failures)
    return HealthVerdict(
        ok=ok,
        status=HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE,
        failures=tuple(failures),
        payload=payload,
        # Matches the retry_after_seconds the snapshot path already advertises, so a client
        # sees one retry cadence rather than two.
        retry_after_seconds=None if ok else 1,
    )


def livez_stall_seconds(last_full_build_seconds: float | None) -> float:
    """How long flat is wedged, expressed against the daemon's own measured build time.

    A literal would be wrong on a bigger store: cold build measured 25-30 s at 1x cardinality
    and 52-56 s at 2x, so a fixed 120 s that is 4x today becomes 2x on a doubled store.
    """

    if last_full_build_seconds is None or last_full_build_seconds <= 0:
        return LIVEZ_STALL_FLOOR_SECONDS
    return max(LIVEZ_STALL_FLOOR_SECONDS, LIVEZ_STALL_BUILD_MULTIPLE * last_full_build_seconds)


def livez(
    sample: ProcessSample,
    previous: ProcessSample | None,
    *,
    stall_seconds: float = LIVEZ_STALL_FLOOR_SECONDS,
    has_outstanding_work: bool = True,
) -> HealthVerdict:
    """Is this process capable of making progress. Nothing else.

    Computed entirely from `ProcessSample`, which the CALLER read from `/proc`. It never enters
    statsd and never takes the GIL, so it cannot be blocked by the wedge it is detecting. That
    is the whole reason it does not reuse `/readyz`'s in-process fields: the worker holds the
    GIL for the full 800-940 ms build burst, and lock-free is not stall-free in CPython.

    `has_outstanding_work` gates the stall check so an idle daemon -- flat by definition -- is
    never called dead. The caller supplies it from a CACHED prior `/readyz`, never a fresh
    in-process read, because a fresh read would reintroduce the GIL dependency.
    """

    if not sample.exists:
        return _livez_verdict(False, sample, (f"process: not readable ({sample.error or 'absent'})",))
    if sample.state not in LIVE_PROCESS_STATES:
        # Z is a corpse, T is stopped. Neither can make progress however long you wait.
        return _livez_verdict(False, sample, (f"state: {sample.state!r} is not one of R/S/D",))
    if previous is None:
        # First sample establishes the baseline. A single point cannot show progress, and
        # guessing "alive" from one point is the failure mode this endpoint exists to avoid --
        # but a live process in a live state is not yet evidence of a wedge either.
        return _livez_verdict(True, sample, (), note="baseline sample, no delta yet")
    elapsed = sample.sampled_at - previous.sampled_at
    cpu_advanced = sample.cpu_ticks > previous.cpu_ticks
    switches_advanced = (
        sample.voluntary_ctxt_switches > previous.voluntary_ctxt_switches
        or sample.nonvoluntary_ctxt_switches > previous.nonvoluntary_ctxt_switches
    )
    io_advanced = (
        sample.read_bytes is not None and previous.read_bytes is not None
        and (sample.read_bytes > previous.read_bytes or (
            sample.write_bytes is not None and previous.write_bytes is not None
            and sample.write_bytes > previous.write_bytes))
    )
    if cpu_advanced or switches_advanced:
        # Advancing CPU proves it is executing; advancing switches prove it is still entering
        # and leaving waits. A 25-56 s cold build advances CPU continuously, so a busy daemon
        # passes -- which is the point: busy is not wedged.
        return _livez_verdict(True, sample, ())
    if not has_outstanding_work:
        return _livez_verdict(True, sample, (), note="flat but idle: no outstanding work")
    if elapsed < stall_seconds:
        return _livez_verdict(True, sample, (), note=f"flat for {elapsed:.1f}s, under {stall_seconds:.0f}s")
    if io_advanced:
        return _livez_verdict(True, sample, (), note="flat CPU and switches, but I/O advanced")
    # Triple-flat with work outstanding: neither computing, nor waiting-and-waking, nor moving
    # bytes. Advancing any of the three requires the thing that is wedged, so no wedge leaves
    # one of them moving.
    return _livez_verdict(False, sample, (
        f"no progress for {elapsed:.1f}s with work outstanding: cpu_ticks flat at "
        f"{sample.cpu_ticks}, ctxt switches flat at {sample.voluntary_ctxt_switches}, "
        f"io flat at {sample.read_bytes}/{sample.write_bytes}",
    ))


def _livez_verdict(
    ok: bool, sample: ProcessSample, failures: tuple[str, ...], *, note: str = "",
) -> HealthVerdict:
    payload: dict[str, object] = {
        "live": ok, "pid": sample.pid, "state": sample.state,
        "cpu_ticks": sample.cpu_ticks,
        "voluntary_ctxt_switches": sample.voluntary_ctxt_switches,
        "read_bytes": sample.read_bytes, "write_bytes": sample.write_bytes,
        "sampled_at": sample.sampled_at, "failures": failures,
    }
    if note:
        payload["note"] = note
    return HealthVerdict(
        ok=ok,
        status=HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE,
        failures=failures,
        payload=payload,
        retry_after_seconds=None if ok else 1,
    )

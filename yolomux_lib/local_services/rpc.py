"""Versioned local Unix-RPC transport shared by YOLOmux services.

The first frame is a small JSON envelope. Optional binary bytes follow the
metadata, never pickle or a Python object graph. Readers accept the former
newline-delimited JSON shape during a rolling restart, while all migrated
writers use the bounded length-prefixed form.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import sys
import threading
import uuid
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic as monotonic_clock
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOCAL_RPC_VERSION = 1
LOCAL_RPC_MAX_METADATA_BYTES = 256 * 1024
LOCAL_RPC_MAX_BINARY_BYTES = 4 * 1024 * 1024
LOCAL_RPC_HEADER_BYTES = 4
# Linux exposes 108 bytes in sockaddr_un.sun_path, including its trailing NUL,
# while Darwin exposes 104. On Darwin `/tmp` is expanded to `/private/tmp` by
# the kernel, so reserve that seven-byte prefix rather than rejecting ordinary
# absolute project paths everywhere. This is the actual bind budget shared by
# fallback routing and rooted-run preflight, not an arbitrary safety margin.
LOCAL_RPC_SOCKET_PATH_BYTES = 96 if sys.platform == "darwin" else 107


class LocalRpcError(ValueError):
    """A peer sent a malformed, incompatible, or oversized local RPC frame."""


# A complete, request-id-matched response that lands past the caller's deadline is a DELIVERED
# response that also overran a telemetry budget -- never a failure.  The deadline is a telemetry
# budget, not a correctness bound, so these two labels are DIAGNOSTIC attributions carried on the
# delivered record, not raised errors.  Which span overran is decided by the peer's own handler
# duration versus the budget.
LOCAL_RPC_OVER_BUDGET_HANDLER = "peer_handler_slow"
LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED = "unattributed_latency"

# Retained so a rolling peer that still speaks the pre-0.7.3 spelling classifies identically.
# `response exceeded deadline` is the pre-rename spelling kept here, not copied elsewhere, so no
# consumer grows its own list.
LOCAL_RPC_DEADLINE_REASONS = frozenset({
    LOCAL_RPC_OVER_BUDGET_HANDLER,
    LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED,
    "response exceeded deadline",
})

# The exact refusal strings the shared listener writes on the wire.  A caller classifies
# against them, so one constant owns each spelling: a hand-copied literal on either side
# silently reclassifies real overload as a generic service error, which is the collapse the
# health contract forbids.
LOCAL_SERVICE_ERROR_BUSY = "service busy"
LOCAL_SERVICE_ERROR_INVALID_REQUEST = "invalid request"
LOCAL_SERVICE_ERROR_PEER_UID_MISMATCH = "peer uid mismatch"
LOCAL_SERVICE_ERROR_RESPONSE_TOO_LARGE = "response too large"


# --- Retained per-service request/error/latency accounting -------------------------------
#
# One owner for all six services, chosen over normalizing statsd's and jobd's bespoke
# in-service metrics.  See `LocalServiceTrafficLedger` for the recorded reason.
LOCAL_SERVICE_TRAFFIC_SCHEMA_VERSION = 1
LOCAL_SERVICE_TRAFFIC_WORK = "work"
LOCAL_SERVICE_TRAFFIC_PROBE = "probe"
LOCAL_SERVICE_TRAFFIC_CLASSES = (LOCAL_SERVICE_TRAFFIC_WORK, LOCAL_SERVICE_TRAFFIC_PROBE)
LOCAL_SERVICE_TRAFFIC_LATENCIES = ("client_latency_ms", "service_latency_ms", "queue_wait_ms")
LOCAL_SERVICE_TRAFFIC_MAX_SERVICES = 32
LOCAL_SERVICE_TRAFFIC_MAX_REASONS = 16
LOCAL_SERVICE_TRAFFIC_OTHER_SERVICE = "other"
# `ping` and `status` carry no product semantics: they are the liveness/diagnostic reads the
# registry and the health observer issue.  Classifying them by method keeps demand-path health
# checks out of the user-work aggregate without editing every lifecycle owner that sends them.
LOCAL_SERVICE_PROBE_METHODS = frozenset({"ping", "status"})

LOCAL_SERVICE_REASON_ABSENT = "absent"
LOCAL_SERVICE_REASON_REFUSED = "refused"
LOCAL_SERVICE_REASON_TIMEOUT = "timeout"
LOCAL_SERVICE_REASON_DEADLINE_HANDLER = "deadline_peer_handler_slow"
LOCAL_SERVICE_REASON_DEADLINE_UNATTRIBUTED = "deadline_unattributed"
LOCAL_SERVICE_REASON_OVERLOAD = "overload"
LOCAL_SERVICE_REASON_IDENTITY_MISMATCH = "identity_mismatch"
LOCAL_SERVICE_REASON_REVISION_MISMATCH = "revision_mismatch"
LOCAL_SERVICE_REASON_PROTOCOL = "protocol_error"
LOCAL_SERVICE_REASON_TRANSPORT = "transport_error"
LOCAL_SERVICE_REASON_SERVICE_ERROR = "service_error"
LOCAL_SERVICE_REASON_OTHER = "other"

_LISTENER_ERROR_REASONS = {
    LOCAL_SERVICE_ERROR_BUSY: LOCAL_SERVICE_REASON_OVERLOAD,
    LOCAL_SERVICE_ERROR_INVALID_REQUEST: "invalid_request",
    LOCAL_SERVICE_ERROR_PEER_UID_MISMATCH: "peer_uid_mismatch",
    LOCAL_SERVICE_ERROR_RESPONSE_TOO_LARGE: "response_too_large",
}

_PROBE_DEPTH: ContextVar[int] = ContextVar("yolomux_local_service_probe_depth", default=0)


@contextmanager
def local_service_probe_scope() -> Iterator[None]:
    """Attribute every local RPC issued inside this scope to observer probe traffic.

    The health observer probes all six services every two seconds and reaches them through
    owners it does not control -- ``LocalServiceRegistry.healthy()`` sends its own RPCs.  A
    per-call flag would have to be threaded through each of those owners and would be missed
    by exactly one of them.  A context-scoped depth counter is read at the single place every
    local RPC attempt already passes through, so nested probe traffic cannot leak into the
    user-work aggregate.  Contexts are per-thread: probe work fanned out to a worker thread
    must re-enter this scope there, or pass ``probe=True`` explicitly.
    """

    token = _PROBE_DEPTH.set(_PROBE_DEPTH.get() + 1)
    try:
        yield
    finally:
        _PROBE_DEPTH.reset(token)


def local_service_traffic_class(method: str = "", probe: bool = False) -> str:
    """Return the aggregate a single RPC attempt belongs to."""

    if probe or _PROBE_DEPTH.get() > 0 or str(method or "") in LOCAL_SERVICE_PROBE_METHODS:
        return LOCAL_SERVICE_TRAFFIC_PROBE
    return LOCAL_SERVICE_TRAFFIC_WORK


def local_service_failure_reason(error: BaseException) -> str:
    """Return one typed reason a local RPC attempt failed.

    Absence, refusal, deadline expiry before the handler, deadline expiry attributed to the
    handler, identity mismatch, and revision mismatch are separate outcomes with separate
    recoveries; collapsing them into one unavailable string is the defect the health contract
    names explicitly.
    """

    if isinstance(error, TimeoutError):
        return LOCAL_SERVICE_REASON_TIMEOUT
    if isinstance(error, LocalRpcError):
        text = str(error)
        if text == "peer_handler_slow":
            return LOCAL_SERVICE_REASON_DEADLINE_HANDLER
        if text in LOCAL_RPC_DEADLINE_REASONS:
            return LOCAL_SERVICE_REASON_DEADLINE_UNATTRIBUTED
        if text == "response request_id mismatch":
            return LOCAL_SERVICE_REASON_IDENTITY_MISMATCH
        if text == "unsupported RPC version":
            return LOCAL_SERVICE_REASON_REVISION_MISMATCH
        return LOCAL_SERVICE_REASON_PROTOCOL
    if isinstance(error, OSError):
        if error.errno == errno.ENOENT:
            return LOCAL_SERVICE_REASON_ABSENT
        if error.errno == errno.ECONNREFUSED:
            return LOCAL_SERVICE_REASON_REFUSED
    return LOCAL_SERVICE_REASON_TRANSPORT


def _bounded_reason_slug(value: object) -> str:
    text = "".join(character if character.isalnum() else "_" for character in str(value).strip().lower())
    return text.strip("_")[:48] or LOCAL_SERVICE_REASON_SERVICE_ERROR


def local_service_response_reason(payload: Mapping[str, Any]) -> str:
    """Return the typed reason a delivered response is a failure, or ``""`` when it is not.

    Only ``ok is False`` proves failure.  A payload without ``ok`` cannot be shown to have
    failed, and inventing an error for it would make the completed count a guess.
    """

    if payload.get("ok") is not False:
        return ""
    if payload.get("capacity_rejected") is True:
        return LOCAL_SERVICE_REASON_OVERLOAD
    error_code = payload.get("error_code")
    if error_code == "upgrade_required" or payload.get("status") == "upgrade_required":
        return LOCAL_SERVICE_REASON_REVISION_MISMATCH
    error_text = str(payload.get("error") or "")
    if error_text in _LISTENER_ERROR_REASONS:
        return _LISTENER_ERROR_REASONS[error_text]
    if isinstance(error_code, str) and error_code:
        # `error_code` is a service-owned vocabulary; the free-form `error` text is not
        # retained because it can carry paths and other unbounded material.
        return _bounded_reason_slug(error_code)
    return LOCAL_SERVICE_REASON_SERVICE_ERROR


def _new_latency() -> dict[str, float]:
    return {"count": 0, "total_ms": 0.0, "max_ms": 0.0}


def _new_class_counters() -> dict[str, Any]:
    counters: dict[str, Any] = {
        "accepted": 0,
        "completed": 0,
        "errors": 0,
        "errors_by_reason": {},
        # Delivered responses that also overran the telemetry budget.  These are a subset of
        # `completed`, tracked separately as diagnostics -- a delivered-but-slow response is a
        # completion that also carries a budget-breach label, never an error.
        "over_budget": 0,
        "over_budget_by_reason": {},
    }
    counters.update({name: _new_latency() for name in LOCAL_SERVICE_TRAFFIC_LATENCIES})
    return counters


def _publish_latency(values: Mapping[str, float]) -> dict[str, float]:
    count = int(values["count"])
    total_ms = round(float(values["total_ms"]), 3)
    return {
        "count": count,
        "total_ms": total_ms,
        "max_ms": round(float(values["max_ms"]), 3),
        "avg_ms": round(total_ms / count, 3) if count else 0.0,
    }


class LocalServiceTrafficLedger:
    """The one retained request/error/latency aggregate for one local service.

    Decision (M6): one common aggregator for all six services, not a normalized projection
    over the bespoke metrics statsd and jobd already keep.  Three reasons.  First, uniform
    accepted/completed/error/latency exit criteria are required, and statusd, watchd, indexd
    and approvald expose no RPC ledger at all -- a projection would have to invent four of the
    six rows.  Second, an in-service counter is destroyed by the service restart the monitor
    exists to report, so it can never answer "how many requests since this web process
    started"; this ledger lives in the web process and is naturally cumulative across a peer
    restart, which is also what the port-scoped retained store needs.  Third, only the client
    side observes attempts that never reached the service at all -- absent socket, refused
    connection, deadline expiry -- and those are precisely the failures the monitor reports.

    Only completions contribute latency, because the published average is
    ``total_ms / completed_count``; individual samples are never retained.
    """

    def __init__(self, service: str):
        self.service = str(service)[:64]
        self._lock = threading.Lock()
        self._epoch = ""
        self._epoch_changes = 0
        self._classes = {name: _new_class_counters() for name in LOCAL_SERVICE_TRAFFIC_CLASSES}

    def _counters(self, traffic_class: str) -> dict[str, Any]:
        return self._classes.get(traffic_class) or self._classes[LOCAL_SERVICE_TRAFFIC_WORK]

    @staticmethod
    def _add_sample(values: dict[str, float], sample: float) -> None:
        amount = float(sample)
        if not amount > 0.0:
            # Rejects negatives and NaN alike; a clock that went backwards is not a duration.
            amount = 0.0
        values["count"] += 1
        values["total_ms"] += amount
        values["max_ms"] = max(values["max_ms"], amount)

    def note_epoch(self, epoch: str) -> None:
        """Record the observed peer process identity, counting only proven changes.

        A restart is only provable once a prior identity was observed, so the first identity
        establishes the baseline and never increments the change count.
        """

        text = str(epoch or "")[:64]
        if not text:
            return
        with self._lock:
            if self._epoch and self._epoch != text:
                self._epoch_changes += 1
            self._epoch = text

    def record_completion(
        self,
        traffic_class: str,
        *,
        client_elapsed_ms: float = 0.0,
        service_duration_ms: float = 0.0,
        queue_wait_ms: float = 0.0,
        over_budget_reason: str = "",
    ) -> None:
        counters = self._counters(traffic_class)
        samples = (client_elapsed_ms, service_duration_ms, queue_wait_ms)
        label = str(over_budget_reason or "")[:48]
        with self._lock:
            counters["accepted"] += 1
            counters["completed"] += 1
            for name, sample in zip(LOCAL_SERVICE_TRAFFIC_LATENCIES, samples):
                self._add_sample(counters[name], sample)
            if label:
                # A delivered response that overran the telemetry budget: a completion that also
                # carries a diagnostic breach label, never a failure.
                counters["over_budget"] += 1
                budgets = counters["over_budget_by_reason"]
                budgets[label] = budgets.get(label, 0) + 1

    def record_failure(self, traffic_class: str, reason: str) -> None:
        counters = self._counters(traffic_class)
        key = str(reason or LOCAL_SERVICE_REASON_SERVICE_ERROR)[:48]
        with self._lock:
            counters["accepted"] += 1
            counters["errors"] += 1
            reasons = counters["errors_by_reason"]
            if key not in reasons and len(reasons) >= LOCAL_SERVICE_TRAFFIC_MAX_REASONS:
                # Fold into one named bucket rather than dropping the event: the reason
                # vocabulary is bounded, the total is not allowed to lose a request.
                key = LOCAL_SERVICE_REASON_OTHER
            reasons[key] = reasons.get(key, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "schema_version": LOCAL_SERVICE_TRAFFIC_SCHEMA_VERSION,
                "service": self.service,
                "epoch": self._epoch,
                "epoch_changes": self._epoch_changes,
            }
            for name, counters in self._classes.items():
                published = {
                    "accepted": int(counters["accepted"]),
                    "completed": int(counters["completed"]),
                    "errors": int(counters["errors"]),
                    "errors_by_reason": dict(sorted(counters["errors_by_reason"].items())),
                    "over_budget": int(counters["over_budget"]),
                    "over_budget_by_reason": dict(sorted(counters["over_budget_by_reason"].items())),
                }
                published.update({latency: _publish_latency(counters[latency]) for latency in LOCAL_SERVICE_TRAFFIC_LATENCIES})
                result[name] = published
            return result


class LocalServiceTrafficRegistry:
    """Own the bounded set of traffic ledgers for one caller lifecycle.

    Production keeps one instance for the web-process lifetime. Tests can inject
    a separate instance or temporarily install one through
    :func:`local_service_traffic_scope`; no caller needs access to the registry's
    lock or ledger dictionary.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ledgers: dict[str, LocalServiceTrafficLedger] = {}

    def ledger(self, service: str) -> LocalServiceTrafficLedger:
        name = str(service or "")[:64] or LOCAL_SERVICE_TRAFFIC_OTHER_SERVICE
        with self._lock:
            ledger = self._ledgers.get(name)
            if ledger is None and len(self._ledgers) >= LOCAL_SERVICE_TRAFFIC_MAX_SERVICES:
                name = LOCAL_SERVICE_TRAFFIC_OTHER_SERVICE
                ledger = self._ledgers.get(name)
            if ledger is None:
                ledger = LocalServiceTrafficLedger(name)
                self._ledgers[name] = ledger
            return ledger

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            ledgers = sorted(self._ledgers.items())
        return {name: ledger.snapshot() for name, ledger in ledgers}

    def reset(self) -> None:
        with self._lock:
            self._ledgers.clear()


_PRODUCTION_TRAFFIC_REGISTRY = LocalServiceTrafficRegistry()
_TRAFFIC_FACADE_LOCK = threading.Lock()
_TRAFFIC_SCOPE_REGISTRY: LocalServiceTrafficRegistry | None = None
_TRAFFIC_SCOPE_TOKEN: object | None = None


def _active_traffic_registry() -> LocalServiceTrafficRegistry:
    with _TRAFFIC_FACADE_LOCK:
        return _TRAFFIC_SCOPE_REGISTRY or _PRODUCTION_TRAFFIC_REGISTRY


@contextmanager
def local_service_traffic_scope(
    registry: LocalServiceTrafficRegistry | None = None,
) -> Iterator[LocalServiceTrafficRegistry]:
    """Install one fixture-owned registry across every thread in the scope.

    A global facade is intentional: local RPC tests fan requests out to worker
    threads, and thread-local/context-local injection would silently split their
    accounting. Only the owner token may remove the scope, so an unrelated reset
    or teardown cannot clear a test whose requests are still active.
    """

    global _TRAFFIC_SCOPE_REGISTRY, _TRAFFIC_SCOPE_TOKEN
    owned = registry or LocalServiceTrafficRegistry()
    token = object()
    with _TRAFFIC_FACADE_LOCK:
        if _TRAFFIC_SCOPE_TOKEN is not None:
            raise RuntimeError("a local-service traffic scope is already active")
        _TRAFFIC_SCOPE_REGISTRY = owned
        _TRAFFIC_SCOPE_TOKEN = token
    try:
        yield owned
    finally:
        with _TRAFFIC_FACADE_LOCK:
            if _TRAFFIC_SCOPE_TOKEN is not token:
                raise RuntimeError("local-service traffic scope ownership changed before teardown")
            _TRAFFIC_SCOPE_REGISTRY = None
            _TRAFFIC_SCOPE_TOKEN = None


def local_service_traffic_ledger(
    service: str,
    *,
    registry: LocalServiceTrafficRegistry | None = None,
) -> LocalServiceTrafficLedger:
    """Return the process-wide ledger for one service, bounded by service count."""

    return (registry or _active_traffic_registry()).ledger(service)


def local_service_traffic_snapshot(
    *,
    registry: LocalServiceTrafficRegistry | None = None,
) -> dict[str, dict[str, Any]]:
    """Return every retained per-service aggregate for the status projection."""

    return (registry or _active_traffic_registry()).snapshot()


def reset_local_service_traffic() -> None:
    """Drop every retained aggregate. Test and process-teardown seam only."""

    with _TRAFFIC_FACADE_LOCK:
        if _TRAFFIC_SCOPE_TOKEN is not None:
            raise RuntimeError("cannot reset local-service traffic owned by an active scope")
    _PRODUCTION_TRAFFIC_REGISTRY.reset()


@dataclass(frozen=True)
class LocalRpcEnvelope:
    """Inspectable request or response metadata transported over a Unix socket."""

    service: str
    method: str
    request_id: str
    trace_id: str
    deadline_ms: int
    priority: str
    owner_generation: int
    config_generation: int
    payload: dict[str, Any]
    accept_to_read_ms: float = 0.0
    read_complete_ms: float = 0.0
    service_duration_ms: float = 0.0
    queue_wait_ms: float = 0.0
    queue_depth: int = 0
    capacity_limit: int = 0
    capacity_saturated: bool = False
    capacity_rejected: bool = False
    capacity_rejections: int = 0
    version: int = LOCAL_RPC_VERSION

    def to_dict(self, binary_length: int = 0) -> dict[str, Any]:
        result = {
            "version": self.version,
            "service": self.service,
            "method": self.method,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "deadline_ms": self.deadline_ms,
            "priority": self.priority,
            "owner_generation": self.owner_generation,
            "config_generation": self.config_generation,
            "accept_to_read_ms": round(max(0.0, float(self.accept_to_read_ms)), 3),
            "read_complete_ms": round(max(0.0, float(self.read_complete_ms)), 3),
            "service_duration_ms": round(max(0.0, float(self.service_duration_ms)), 3),
            "binary_length": binary_length,
            "payload": self.payload,
        }
        if self.capacity_limit:
            result.update({
                "queue_wait_ms": round(max(0.0, float(self.queue_wait_ms)), 3),
                "queue_depth": max(0, int(self.queue_depth)),
                "capacity_limit": max(0, int(self.capacity_limit)),
                "capacity_saturated": bool(self.capacity_saturated),
                "capacity_rejected": bool(self.capacity_rejected),
                "capacity_rejections": max(0, int(self.capacity_rejections)),
            })
        return result


def safe_socket_path(path: Path, prefix: str = "yolomux", fallback_name: str | None = None) -> Path:
    """Keep Unix-domain paths portable without leaking a long state directory."""
    candidate = path.expanduser()
    if len(os.fsencode(str(candidate))) <= LOCAL_RPC_SOCKET_PATH_BYTES:
        return candidate
    digest = hashlib.sha256(os.fsencode(str(candidate))).hexdigest()[:20]
    uid = getattr(os, "getuid", lambda: "nouid")()
    if fallback_name:
        return Path("/tmp") / f"{prefix}-{uid}-{digest}" / fallback_name
    return Path("/tmp") / f"{prefix}-{uid}-{digest}.sock"


def new_envelope(
    service: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 2.0,
    trace_id: str | None = None,
    priority: str = "normal",
    owner_generation: int = 0,
    config_generation: int = 0,
) -> LocalRpcEnvelope:
    """Build a bounded, typed request envelope from one service operation."""
    if not isinstance(service, str) or not service:
        raise LocalRpcError("service is required")
    if not isinstance(method, str) or not method:
        raise LocalRpcError("method is required")
    if not isinstance(payload, dict):
        raise LocalRpcError("payload must be an object")
    deadline_ms = max(1, min(int(timeout_seconds * 1000), 60_000))
    return LocalRpcEnvelope(
        service=service,
        method=method,
        request_id=uuid.uuid4().hex,
        trace_id=trace_id or uuid.uuid4().hex,
        deadline_ms=deadline_ms,
        priority=priority if priority in {"interactive", "normal", "maintenance"} else "normal",
        owner_generation=max(0, int(owner_generation)),
        config_generation=max(0, int(config_generation)),
        payload=payload,
    )


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise LocalRpcError("unexpected EOF")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalRpcError("invalid JSON metadata") from exc
    if not isinstance(value, dict):
        raise LocalRpcError("metadata must be an object")
    return value


def _validate_length(value: Any, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise LocalRpcError(f"invalid {field}")
    return value


def _decode_envelope(value: dict[str, Any]) -> tuple[LocalRpcEnvelope, int]:
    version = value.get("version")
    if version != LOCAL_RPC_VERSION:
        raise LocalRpcError("unsupported RPC version")
    payload = value.get("payload")
    fields = ("service", "method", "request_id", "trace_id", "priority")
    if not isinstance(payload, dict) or any(not isinstance(value.get(field), str) or not value[field] for field in fields):
        raise LocalRpcError("invalid RPC envelope")
    deadline_ms = _validate_length(value.get("deadline_ms"), 60_000, "deadline_ms")
    owner_generation = _validate_length(value.get("owner_generation"), 2**63 - 1, "owner_generation")
    config_generation = _validate_length(value.get("config_generation"), 2**63 - 1, "config_generation")
    binary_length = _validate_length(value.get("binary_length", 0), LOCAL_RPC_MAX_BINARY_BYTES, "binary_length")
    durations: dict[str, float] = {}
    for field in ("accept_to_read_ms", "read_complete_ms", "service_duration_ms"):
        duration = value.get(field, 0.0)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0 or duration > 60_000:
            raise LocalRpcError(f"invalid {field}")
        durations[field] = float(duration)
    queue_wait_ms = 0.0
    queue_depth = capacity_limit = capacity_rejections = 0
    capacity_saturated = capacity_rejected = False
    if "capacity_limit" in value:
        queue_wait_ms = value.get("queue_wait_ms", 0.0)
        if isinstance(queue_wait_ms, bool) or not isinstance(queue_wait_ms, (int, float)) or queue_wait_ms < 0 or queue_wait_ms > 60_000:
            raise LocalRpcError("invalid queue_wait_ms")
        queue_depth = _validate_length(value.get("queue_depth", 0), 2**31 - 1, "queue_depth")
        capacity_limit = _validate_length(value["capacity_limit"], 2**31 - 1, "capacity_limit")
        capacity_rejections = _validate_length(value.get("capacity_rejections", 0), 2**63 - 1, "capacity_rejections")
        capacity_saturated = value.get("capacity_saturated", False)
        capacity_rejected = value.get("capacity_rejected", False)
        if not isinstance(capacity_saturated, bool) or not isinstance(capacity_rejected, bool):
            raise LocalRpcError("invalid capacity state")
    return (
        LocalRpcEnvelope(
            service=value["service"],
            method=value["method"],
            request_id=value["request_id"],
            trace_id=value["trace_id"],
            deadline_ms=deadline_ms,
            priority=value["priority"],
            owner_generation=owner_generation,
            config_generation=config_generation,
            payload=payload,
            accept_to_read_ms=durations["accept_to_read_ms"],
            read_complete_ms=durations["read_complete_ms"],
            service_duration_ms=durations["service_duration_ms"],
            queue_wait_ms=float(queue_wait_ms),
            queue_depth=queue_depth,
            capacity_limit=capacity_limit,
            capacity_saturated=capacity_saturated,
            capacity_rejected=capacity_rejected,
            capacity_rejections=capacity_rejections,
            version=version,
        ),
        binary_length,
    )


def read_message(connection: socket.socket) -> tuple[LocalRpcEnvelope | None, dict[str, Any], bytes, bool]:
    """Read one frame, accepting legacy newline JSON only for rolling upgrades.

    Returns ``(envelope, payload, binary, legacy)``. Legacy callers receive a
    synthesized envelope and keep their original request shape as ``payload``.
    """
    # Receive a short prefix rather than one byte. Real sockets can fragment it
    # arbitrarily; test doubles may legally coalesce the whole newline frame.
    first = connection.recv(LOCAL_RPC_HEADER_BYTES)
    if not first:
        raise LocalRpcError("unexpected EOF")
    if first.lstrip().startswith((b"{", b"[")):
        raw = first
        while len(raw) <= LOCAL_RPC_MAX_METADATA_BYTES:
            if b"\n" in raw:
                raw = raw.split(b"\n", 1)[0]
                break
            chunk = connection.recv(4096)
            if not chunk:
                break
            raw += chunk
            if b"\n" in chunk:
                raw = raw.split(b"\n", 1)[0]
                break
        if len(raw) > LOCAL_RPC_MAX_METADATA_BYTES:
            raise LocalRpcError("legacy metadata too large")
        payload = _decode_json(raw)
        return None, payload, b"", True
    header = first[:LOCAL_RPC_HEADER_BYTES]
    prefetched = first[LOCAL_RPC_HEADER_BYTES:]
    if len(header) < LOCAL_RPC_HEADER_BYTES:
        header += _read_exact(connection, LOCAL_RPC_HEADER_BYTES - len(header))
    metadata_length = _validate_length(int.from_bytes(header, "big"), LOCAL_RPC_MAX_METADATA_BYTES, "metadata_length")
    if metadata_length == 0:
        raise LocalRpcError("empty metadata")
    metadata = prefetched
    if len(metadata) < metadata_length:
        metadata += _read_exact(connection, metadata_length - len(metadata))
    elif len(metadata) > metadata_length:
        raise LocalRpcError("invalid RPC metadata")
    envelope, binary_length = _decode_envelope(_decode_json(metadata))
    return envelope, envelope.payload, _read_exact(connection, binary_length) if binary_length else b"", False


def write_message(
    connection: socket.socket,
    envelope: LocalRpcEnvelope | None,
    payload: dict[str, Any],
    binary: bytes = b"",
    *,
    legacy: bool = False,
) -> None:
    """Write either the current frame or the peer-compatible legacy response."""
    if not isinstance(payload, dict):
        raise LocalRpcError("payload must be an object")
    if len(binary) > LOCAL_RPC_MAX_BINARY_BYTES:
        raise LocalRpcError("binary payload too large")
    if legacy:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > LOCAL_RPC_MAX_METADATA_BYTES:
            raise LocalRpcError("legacy metadata too large")
        connection.sendall(encoded + b"\n")
        return
    if envelope is None:
        raise LocalRpcError("current RPC response requires an envelope")
    encoded = encode_metadata(envelope, binary_length=len(binary))
    if len(encoded) > LOCAL_RPC_MAX_METADATA_BYTES:
        raise LocalRpcError("metadata too large")
    connection.sendall(len(encoded).to_bytes(LOCAL_RPC_HEADER_BYTES, "big") + encoded + binary)


def encode_metadata(
    envelope: LocalRpcEnvelope,
    *,
    binary_length: int = 0,
) -> bytes:
    """Encode the exact bounded metadata frame shared by sizing and transport."""

    return json.dumps(
        envelope.to_dict(binary_length),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _over_budget_attribution(response_envelope: LocalRpcEnvelope, deadline_ms: int) -> str:
    """Name which span a delivered-but-slow response overran, as diagnostics only.

    The response is complete and request-id matched, so this is never a failure: the deadline is
    a telemetry budget, not a correctness bound.  Which span overran is decided by the one
    measurement this path already holds -- the peer's own handler duration versus the budget --
    so it separates a slow handler from latency that happened before the handler ever ran.
    """

    if response_envelope.service_duration_ms > deadline_ms:
        return LOCAL_RPC_OVER_BUDGET_HANDLER
    return LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED


def _record_delivered_response(
    ledger: LocalServiceTrafficLedger,
    traffic_class: str,
    envelope: LocalRpcEnvelope,
    response_envelope: LocalRpcEnvelope | None,
    payload: Mapping[str, Any],
    elapsed_ms: float,
    over_budget_reason: str = "",
) -> None:
    """Account one delivered response, typed by its own outcome.

    ``over_budget_reason`` is an optional diagnostic label for a delivered response that also
    overran the caller's telemetry budget.  It rides on the completion here rather than forking a
    second recorder: a delivered-but-slow response is a delivered response that also carries a
    budget-breach label, so exactly one owner accounts it.
    """

    reason = local_service_response_reason(payload)
    if reason:
        ledger.record_failure(traffic_class, reason)
        return
    if envelope.method in LOCAL_SERVICE_PROBE_METHODS:
        pid = payload.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1:
            ledger.note_epoch(f"pid:{pid}")
    ledger.record_completion(
        traffic_class,
        client_elapsed_ms=elapsed_ms,
        service_duration_ms=response_envelope.service_duration_ms if response_envelope is not None else 0.0,
        queue_wait_ms=response_envelope.queue_wait_ms if response_envelope is not None else 0.0,
        over_budget_reason=over_budget_reason,
    )


def _legacy_fallback(
    ledger: LocalServiceTrafficLedger,
    traffic_class: str,
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    timeout_seconds: float,
    started: float,
) -> tuple[None, dict[str, Any], bytes]:
    """Renegotiate one attempt over the former protocol, still accounted exactly once."""

    try:
        payload = legacy_request(socket_path, envelope.payload, timeout_seconds=timeout_seconds)
    except (OSError, LocalRpcError) as exc:
        ledger.record_failure(traffic_class, local_service_failure_reason(exc))
        raise
    _record_delivered_response(ledger, traffic_class, envelope, None, payload, (monotonic_clock() - started) * 1000)
    return None, payload, b""


def _response_written_before_close(
    client: socket.socket,
    write_error: OSError,
) -> tuple[LocalRpcEnvelope | None, dict[str, Any], bytes, bool]:
    """Return the response a peer delivered before closing, or re-raise *write_error*.

    A peer at its handler limit refuses on accept: it writes the typed refusal and closes
    without ever reading the request.  Our send then fails with EPIPE while a complete
    refusal already sits in this socket's receive queue.  The refusal is the authoritative
    outcome of the attempt, so discarding it would both lose the response the caller is
    entitled to and reclassify proven overload as a transport error -- the exact collapse
    the health contract forbids.  A peer that wrote nothing leaves the write failure as the
    only outcome, and it is re-raised unchanged with the read failure kept as its context.
    """

    try:
        return read_message(client)
    except (OSError, LocalRpcError):
        raise write_error


def request_with_envelope(
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    *,
    binary: bytes = b"",
    timeout_seconds: float = 2.0,
    fallback_legacy: bool = False,
    probe: bool = False,
) -> tuple[LocalRpcEnvelope | None, dict[str, Any], bytes]:
    """Send one request and retain the peer phase envelope with its payload and bytes.

    This is the one place every local RPC attempt issued by this process passes through --
    ``request``, ``LocalServiceClient``, statsd's own ``_wire_rpc``, and the registry's
    ``ping``/``status`` -- so it also owns the retained traffic aggregate.  Recording one
    level higher would miss a caller and produce a second, divergent count of the same
    requests.  Every exit records exactly once.
    """
    ledger = local_service_traffic_ledger(envelope.service)
    traffic_class = local_service_traffic_class(envelope.method, probe)
    started = monotonic_clock()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds)
            client.connect(str(socket_path))
            try:
                write_message(client, envelope, envelope.payload, binary)
            except ConnectionError as write_error:
                # The peer closed mid-send.  It may still have written a complete typed
                # response first -- the capacity refusal is written and closed on accept,
                # before the request is ever read -- so read it rather than lose it.
                response_envelope, payload, response_binary, legacy = _response_written_before_close(client, write_error)
            else:
                response_envelope, payload, response_binary, legacy = read_message(client)
    except TimeoutError as exc:
        # A current peer that accepted but missed its deadline is busy.  A
        # second legacy request would duplicate the queued work and amplify
        # overload; legacy fallback is only for an immediate protocol/connect
        # incompatibility during a rolling restart.
        ledger.record_failure(traffic_class, local_service_failure_reason(exc))
        raise
    except OSError as exc:
        # A missing or refused socket proves there is no peer to negotiate with.
        # Let the lifecycle-owning client start or replace it; replaying via the
        # legacy protocol only creates a second identical connection failure.
        if exc.errno in {errno.ENOENT, errno.ECONNREFUSED} or not fallback_legacy or binary:
            ledger.record_failure(traffic_class, local_service_failure_reason(exc))
            raise
        return _legacy_fallback(ledger, traffic_class, socket_path, envelope, timeout_seconds, started)
    except LocalRpcError as exc:
        if not fallback_legacy or binary:
            ledger.record_failure(traffic_class, local_service_failure_reason(exc))
            raise
        return _legacy_fallback(ledger, traffic_class, socket_path, envelope, timeout_seconds, started)
    if legacy or response_envelope is None:
        _record_delivered_response(ledger, traffic_class, envelope, None, payload, (monotonic_clock() - started) * 1000)
        return None, payload, response_binary
    if response_envelope.request_id != envelope.request_id:
        mismatch = LocalRpcError("response request_id mismatch")
        ledger.record_failure(traffic_class, local_service_failure_reason(mismatch))
        raise mismatch
    elapsed_ms = (monotonic_clock() - started) * 1000
    # A complete, request-id-matched response is valid even when the client elapsed time or the
    # peer handler duration exceeds the caller's deadline: the deadline is a telemetry budget,
    # not a correctness bound.  Never raise here -- returning the bytes is what lets a slow
    # product poll deliver instead of collapsing into a 503.  Only a connect/send/receive timeout
    # BEFORE any response envelope exists is a transport failure, and that already raised above.
    # Retain which span overran as a diagnostic on the delivered record, not as an error.
    over_budget_reason = ""
    if elapsed_ms > envelope.deadline_ms:
        over_budget_reason = _over_budget_attribution(response_envelope, envelope.deadline_ms)
    _record_delivered_response(
        ledger, traffic_class, envelope, response_envelope, payload, elapsed_ms, over_budget_reason=over_budget_reason
    )
    return response_envelope, payload, response_binary


def request(
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    *,
    binary: bytes = b"",
    timeout_seconds: float = 2.0,
    fallback_legacy: bool = False,
    probe: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Send one request while preserving the established payload/binary API."""
    _response_envelope, payload, response_binary = request_with_envelope(
        socket_path,
        envelope,
        binary=binary,
        timeout_seconds=timeout_seconds,
        fallback_legacy=fallback_legacy,
        probe=probe,
    )
    return payload, response_binary


def legacy_request(socket_path: str | Path, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    """Use the former newline JSON protocol only when a rolling peer needs it."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > LOCAL_RPC_MAX_METADATA_BYTES:
        raise LocalRpcError("legacy metadata too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path))
        client.sendall(encoded + b"\n")
        raw = b""
        while len(raw) <= LOCAL_RPC_MAX_METADATA_BYTES:
            chunk = client.recv(4096)
            if not chunk:
                break
            raw += chunk
            if b"\n" in chunk:
                raw = raw.split(b"\n", 1)[0]
                break
    if len(raw) > LOCAL_RPC_MAX_METADATA_BYTES:
        raise LocalRpcError("legacy response too large")
    return _decode_json(raw)

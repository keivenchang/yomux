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
import uuid
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


# The single owner of "the peer could not answer inside the caller's deadline".  Which of these
# a caller sees is decided by milliseconds of jitter -- whether the peer's response landed just
# inside the per-recv timer or just outside the total budget -- so they must classify
# identically.  `response exceeded deadline` is the pre-rename spelling kept here, not copied
# elsewhere, so no consumer grows its own list.
LOCAL_RPC_DEADLINE_REASONS = frozenset({
    "peer_handler_slow",
    "unattributed_latency",
    "response exceeded deadline",
})


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


def request_with_envelope(
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    *,
    binary: bytes = b"",
    timeout_seconds: float = 2.0,
    fallback_legacy: bool = False,
) -> tuple[LocalRpcEnvelope | None, dict[str, Any], bytes]:
    """Send one request and retain the peer phase envelope with its payload and bytes."""
    started = monotonic_clock()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds)
            client.connect(str(socket_path))
            write_message(client, envelope, envelope.payload, binary)
            response_envelope, payload, response_binary, legacy = read_message(client)
    except TimeoutError:
        # A current peer that accepted but missed its deadline is busy.  A
        # second legacy request would duplicate the queued work and amplify
        # overload; legacy fallback is only for an immediate protocol/connect
        # incompatibility during a rolling restart.
        raise
    except OSError as exc:
        # A missing or refused socket proves there is no peer to negotiate with.
        # Let the lifecycle-owning client start or replace it; replaying via the
        # legacy protocol only creates a second identical connection failure.
        if exc.errno in {errno.ENOENT, errno.ECONNREFUSED} or not fallback_legacy or binary:
            raise
        return None, legacy_request(socket_path, envelope.payload, timeout_seconds=timeout_seconds), b""
    except LocalRpcError:
        if not fallback_legacy or binary:
            raise
        return None, legacy_request(socket_path, envelope.payload, timeout_seconds=timeout_seconds), b""
    if legacy or response_envelope is None:
        return None, payload, response_binary
    if response_envelope.request_id != envelope.request_id:
        raise LocalRpcError("response request_id mismatch")
    elapsed_ms = (monotonic_clock() - started) * 1000
    if elapsed_ms > envelope.deadline_ms:
        if response_envelope.service_duration_ms > envelope.deadline_ms:
            raise LocalRpcError("peer_handler_slow")
        raise LocalRpcError("unattributed_latency")
    return response_envelope, payload, response_binary


def request(
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    *,
    binary: bytes = b"",
    timeout_seconds: float = 2.0,
    fallback_legacy: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Send one request while preserving the established payload/binary API."""
    _response_envelope, payload, response_binary = request_with_envelope(
        socket_path,
        envelope,
        binary=binary,
        timeout_seconds=timeout_seconds,
        fallback_legacy=fallback_legacy,
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

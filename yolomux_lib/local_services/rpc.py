"""Versioned local Unix-RPC transport shared by YOLOmux services.

The first frame is a small JSON envelope. Optional binary bytes follow the
metadata, never pickle or a Python object graph. Readers accept the former
newline-delimited JSON shape during a rolling restart, while all migrated
writers use the bounded length-prefixed form.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
import uuid
from time import monotonic as monotonic_clock
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOCAL_RPC_VERSION = 1
LOCAL_RPC_MAX_METADATA_BYTES = 256 * 1024
LOCAL_RPC_MAX_BINARY_BYTES = 4 * 1024 * 1024
LOCAL_RPC_HEADER_BYTES = 4
# macOS uses a much smaller Unix-domain pathname budget than Linux. Leave
# enough room for the platform's private /var expansion of /tmp as well as
# the socket filename, instead of accepting a path that only fails at bind.
LOCAL_RPC_SOCKET_PATH_BYTES = 72


class LocalRpcError(ValueError):
    """A peer sent a malformed, incompatible, or oversized local RPC frame."""


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
    version: int = LOCAL_RPC_VERSION

    def to_dict(self, binary_length: int = 0) -> dict[str, Any]:
        return {
            "version": self.version,
            "service": self.service,
            "method": self.method,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "deadline_ms": self.deadline_ms,
            "priority": self.priority,
            "owner_generation": self.owner_generation,
            "config_generation": self.config_generation,
            "binary_length": binary_length,
            "payload": self.payload,
        }


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


def request(
    socket_path: str | Path,
    envelope: LocalRpcEnvelope,
    *,
    binary: bytes = b"",
    timeout_seconds: float = 2.0,
    fallback_legacy: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Send one current-format request and return the peer's metadata and bytes."""
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
    except (OSError, LocalRpcError):
        if not fallback_legacy or binary:
            raise
        return legacy_request(socket_path, envelope.payload, timeout_seconds=timeout_seconds), b""
    if legacy or response_envelope is None:
        return payload, response_binary
    if response_envelope.request_id != envelope.request_id:
        raise LocalRpcError("response request_id mismatch")
    if (monotonic_clock() - started) * 1000 > envelope.deadline_ms:
        raise LocalRpcError("response exceeded deadline")
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

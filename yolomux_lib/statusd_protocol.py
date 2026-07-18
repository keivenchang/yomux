"""Versioned public snapshot contract shared by statusd and web clients."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .local_services.rpc import LOCAL_RPC_MAX_BINARY_BYTES
from .local_services.rpc import LOCAL_RPC_VERSION


STATUSD_PROTOCOL_VERSION = 1
STATUSD_SERVICE_NAME = "statusd"
STATUSD_MAX_WAIT_SECONDS = 30.0
STATUSD_PRIVATE_FIELDS = frozenset({
    "client_id", "client_ip", "cookie", "authorization", "browser_metrics",
    "private_client_state",
})
# The session-inventory is the daemon-owned authority that refresh products
# (session-files, transcripts, Tabber) key their work on. It carries only bounded
# identifiers and per-session source signatures, never heavy enrichment. These
# keys must never appear in an inventory body: they mark work that belongs to a
# refresh product, not to the lightweight roster the daemon discovers itself.
STATUSD_INVENTORY_MAX_SESSIONS = 256
STATUSD_INVENTORY_HEAVY_FIELDS = frozenset({
    "git", "repo", "repos", "transcript", "transcripts", "diff", "content",
    "pull_request", "linear", "branches", "session_files", "activity",
})


class StatusProtocolError(ValueError):
    """A statusd request or snapshot does not satisfy the public contract."""


@dataclass(frozen=True)
class StatusSnapshotMetadata:
    """Immutable metadata for an already JSON-encoded shared status snapshot."""

    generation: int
    status: int
    stale: bool
    built_at: float
    content_type: str = "application/json; charset=utf-8"
    protocol_version: int = STATUSD_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "generation": self.generation,
            "status": self.status,
            "stale": self.stale,
            "built_at": self.built_at,
            "content_type": self.content_type,
        }


def validate_request(request: object) -> dict[str, Any]:
    """Validate a bounded statusd action without accepting browser-private input."""

    if not isinstance(request, dict):
        raise StatusProtocolError("request must be an object")
    if any(field in request for field in STATUSD_PRIVATE_FIELDS):
        raise StatusProtocolError("private client fields are not allowed")
    version = request.get("protocol_version", STATUSD_PROTOCOL_VERSION)
    if version != STATUSD_PROTOCOL_VERSION:
        raise StatusProtocolError("upgrade_required")
    action = request.get("action")
    if action not in {"snapshot", "inventory", "wait_generation", "invalidate", "status", "ping", "lease", "release", "shutdown", "shutdown_if_idle"}:
        raise StatusProtocolError("unknown status action")
    generation = request.get("after_generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise StatusProtocolError("invalid after_generation")
    timeout = request.get("timeout_seconds", 0.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0 or timeout > STATUSD_MAX_WAIT_SECONDS:
        raise StatusProtocolError("invalid timeout_seconds")
    return dict(request)


def validate_snapshot(metadata: object, body: bytes) -> StatusSnapshotMetadata:
    """Validate daemon-owned metadata and exact JSON bytes before HTTP forwarding."""

    if not isinstance(metadata, dict):
        raise StatusProtocolError("snapshot metadata must be an object")
    if len(body) > LOCAL_RPC_MAX_BINARY_BYTES:
        raise StatusProtocolError("snapshot body too large")
    if metadata.get("protocol_version") != STATUSD_PROTOCOL_VERSION:
        raise StatusProtocolError("upgrade_required")
    generation = metadata.get("generation")
    status = metadata.get("status")
    built_at = metadata.get("built_at")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise StatusProtocolError("invalid snapshot generation")
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise StatusProtocolError("invalid snapshot status")
    if isinstance(built_at, bool) or not isinstance(built_at, (int, float)) or built_at < 0:
        raise StatusProtocolError("invalid snapshot timestamp")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusProtocolError("snapshot body must be JSON") from exc
    if not isinstance(decoded, dict) or any(field in decoded for field in STATUSD_PRIVATE_FIELDS):
        raise StatusProtocolError("invalid public snapshot body")
    return StatusSnapshotMetadata(
        generation=generation,
        status=status,
        stale=bool(metadata.get("stale")),
        built_at=float(built_at),
        content_type=str(metadata.get("content_type") or "application/json; charset=utf-8"),
        protocol_version=STATUSD_PROTOCOL_VERSION,
    )


def validate_inventory(metadata: object, body: bytes) -> dict[str, Any]:
    """Validate the daemon-owned session-inventory: bounded identifiers only.

    The inventory is the authority refresh products consume, so it must carry a
    monotonic ``inventory_generation`` and per-session ``source_signature`` while
    excluding browser-private input and any heavy-enrichment field.
    """

    if not isinstance(metadata, dict):
        raise StatusProtocolError("inventory metadata must be an object")
    if len(body) > LOCAL_RPC_MAX_BINARY_BYTES:
        raise StatusProtocolError("inventory body too large")
    if metadata.get("protocol_version") != STATUSD_PROTOCOL_VERSION:
        raise StatusProtocolError("upgrade_required")
    generation = metadata.get("inventory_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise StatusProtocolError("invalid inventory generation")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusProtocolError("inventory body must be JSON") from exc
    if not isinstance(decoded, dict) or any(field in decoded for field in STATUSD_PRIVATE_FIELDS):
        raise StatusProtocolError("invalid inventory body")
    sessions = decoded.get("sessions")
    if not isinstance(sessions, dict) or len(sessions) > STATUSD_INVENTORY_MAX_SESSIONS:
        raise StatusProtocolError("invalid inventory sessions")
    for entry in sessions.values():
        if not isinstance(entry, dict):
            raise StatusProtocolError("invalid inventory session entry")
        if any(field in entry for field in STATUSD_PRIVATE_FIELDS | STATUSD_INVENTORY_HEAVY_FIELDS):
            raise StatusProtocolError("inventory session carries disallowed field")
        if not isinstance(entry.get("source_signature"), str) or not entry["source_signature"]:
            raise StatusProtocolError("inventory session missing source_signature")
    return decoded


def stamped_request(action: str, **fields: object) -> dict[str, object]:
    """Return the sole version-stamped request shape used by statusd clients."""

    request = {"action": action, "protocol_version": STATUSD_PROTOCOL_VERSION, **fields}
    validate_request(request)
    return request

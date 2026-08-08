"""Versioned public snapshot contract shared by statusd and web clients."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from .local_services.rpc import LOCAL_RPC_MAX_BINARY_BYTES


STATUSD_PROTOCOL_VERSION = 2
# Same-protocol daemon behavior changed: a serial old statusd cannot serve a
# generation waiter safely. LocalServiceRegistry retires differing revisions.
STATUSD_CODE_REVISION = "statusd-activity-summary-v5-disabled"
STATUSD_SERVICE_NAME = "statusd"
STATUSD_MAX_WAIT_SECONDS = 30.0
STATUSD_ACTIVITY_MAX_SESSIONS = 256
STATUSD_ACTIVITY_MAX_HOURS = 24.0 * 14
STATUSD_ACTIVITY_MAX_WORK_BYTES = LOCAL_RPC_MAX_BINARY_BYTES
STATUSD_ACTIVITY_WORK_FIELDS = frozenset({"git", "pull_request", "linear", "repos", "loading"})
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


class ActivitySummaryDisabled(StatusProtocolError):
    """The synchronous legacy activity-summary path is not admitted."""


@dataclass(frozen=True)
class ActivitySummaryAdmission:
    enabled: bool
    reason: str


ACTIVITY_SUMMARY_ADMISSION = ActivitySummaryAdmission(
    enabled=False,
    reason="async_replacement_required",
)


def activity_summary_enabled() -> bool:
    """Return the one process-wide admission decision for the legacy path."""

    return ACTIVITY_SUMMARY_ADMISSION.enabled is True


def require_activity_summary_enabled() -> None:
    if not activity_summary_enabled():
        raise ActivitySummaryDisabled(ACTIVITY_SUMMARY_ADMISSION.reason)


def activity_summary_disabled_payload() -> dict[str, object]:
    return {
        "status": "feature_disabled",
        "code": "feature_disabled",
        "reason": ACTIVITY_SUMMARY_ADMISSION.reason,
        "retryable": False,
        "terminal": True,
    }


def activity_summary_disabled_response() -> tuple[dict[str, object], bytes]:
    payload = activity_summary_disabled_payload()
    metadata = {
        "ok": False,
        "status": int(HTTPStatus.SERVICE_UNAVAILABLE),
        "error": "feature_disabled",
        "code": "feature_disabled",
        "reason": ACTIVITY_SUMMARY_ADMISSION.reason,
        "retryable": False,
        "terminal": True,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return metadata, body


def activity_summary_bootstrap() -> dict[str, object]:
    return {
        "enabled": activity_summary_enabled(),
        "reason": "" if activity_summary_enabled() else ACTIVITY_SUMMARY_ADMISSION.reason,
    }


def normalized_activity_summary_bootstrap(value: object) -> dict[str, object]:
    enabled = isinstance(value, dict) and value.get("enabled") is True and activity_summary_enabled()
    return {
        "enabled": enabled,
        "reason": "" if enabled else ACTIVITY_SUMMARY_ADMISSION.reason,
    }


def _decode_json_body(body: bytes, label: str) -> dict[str, Any]:
    if len(body) > LOCAL_RPC_MAX_BINARY_BYTES:
        raise StatusProtocolError(f"{label} body too large")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusProtocolError(f"{label} body must be JSON") from exc
    if not isinstance(decoded, dict):
        raise StatusProtocolError(f"invalid {label} body")
    return decoded


def _validate_activity_work(
    work_by_session: object,
    sessions: list[str] | tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if not isinstance(work_by_session, dict) or len(work_by_session) > STATUSD_ACTIVITY_MAX_SESSIONS:
        raise StatusProtocolError("invalid activity work body")
    allowed_sessions = set(sessions)
    for session, work in work_by_session.items():
        if not isinstance(session, str) or session not in allowed_sessions:
            raise StatusProtocolError("invalid activity work session")
        if not isinstance(work, dict):
            raise StatusProtocolError("invalid activity work entry")
        if any(field not in STATUSD_ACTIVITY_WORK_FIELDS for field in work):
            raise StatusProtocolError("invalid activity work field")
    return work_by_session


def encode_activity_work_body(
    work_by_session: object,
    sessions: list[str] | tuple[str, ...],
) -> bytes:
    """Validate and encode the web-owned cached work projection once."""

    work = _validate_activity_work(work_by_session, sessions)
    try:
        body = json.dumps(work, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StatusProtocolError("invalid activity work body") from exc
    if len(body) > STATUSD_ACTIVITY_MAX_WORK_BYTES:
        raise StatusProtocolError("activity work body too large")
    return body


def decode_activity_work_body(
    body: bytes,
    sessions: list[str] | tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Decode the bounded binary work projection accepted by statusd."""

    return _validate_activity_work(_decode_json_body(body, "activity work"), sessions)


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
    if action not in {"snapshot", "inventory", "activity_summary", "wait_generation", "invalidate", "status", "ping", "lease", "release", "shutdown", "shutdown_if_idle"}:
        raise StatusProtocolError("unknown status action")
    generation = request.get("after_generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise StatusProtocolError("invalid after_generation")
    timeout = request.get("timeout_seconds", 0.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0 or timeout > STATUSD_MAX_WAIT_SECONDS:
        raise StatusProtocolError("invalid timeout_seconds")
    if action == "activity_summary":
        sessions = request.get("sessions")
        if (
            not isinstance(sessions, list)
            or len(sessions) > STATUSD_ACTIVITY_MAX_SESSIONS
            or any(not isinstance(session, str) or not session.strip() for session in sessions)
            or len(set(sessions)) != len(sessions)
        ):
            raise StatusProtocolError("invalid activity sessions")
        if not isinstance(request.get("force"), bool):
            raise StatusProtocolError("invalid activity force")
        locale = request.get("locale")
        if not isinstance(locale, str) or not locale or len(locale) > 64:
            raise StatusProtocolError("invalid activity locale")
        if request.get("session_scope") not in {"configured", "all"}:
            raise StatusProtocolError("invalid activity session_scope")
        hours = request.get("hours")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.25 <= hours <= STATUSD_ACTIVITY_MAX_HOURS:
            raise StatusProtocolError("invalid activity hours")
        if request.get("work_by_session_binary") is not True or "work_by_session" in request:
            raise StatusProtocolError("invalid activity work_by_session_binary")
    return dict(request)


def validate_snapshot(metadata: object, body: bytes) -> StatusSnapshotMetadata:
    """Validate daemon-owned metadata and exact JSON bytes before HTTP forwarding."""

    if not isinstance(metadata, dict):
        raise StatusProtocolError("snapshot metadata must be an object")
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
    decoded = _decode_json_body(body, "snapshot")
    if any(field in decoded for field in STATUSD_PRIVATE_FIELDS):
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
    if metadata.get("protocol_version") != STATUSD_PROTOCOL_VERSION:
        raise StatusProtocolError("upgrade_required")
    generation = metadata.get("inventory_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise StatusProtocolError("invalid inventory generation")
    decoded = _decode_json_body(body, "inventory")
    if any(field in decoded for field in STATUSD_PRIVATE_FIELDS):
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


def validate_activity_summary(metadata: object, body: bytes) -> dict[str, Any]:
    """Validate and decode one daemon-assembled public activity summary."""

    if not isinstance(metadata, dict):
        raise StatusProtocolError("activity metadata must be an object")
    if metadata.get("protocol_version") != STATUSD_PROTOCOL_VERSION:
        raise StatusProtocolError("upgrade_required")
    status = metadata.get("status")
    built_at = metadata.get("built_at")
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise StatusProtocolError("invalid activity status")
    if isinstance(built_at, bool) or not isinstance(built_at, (int, float)) or built_at < 0:
        raise StatusProtocolError("invalid activity timestamp")
    decoded = _decode_json_body(body, "activity")
    if any(field in decoded for field in STATUSD_PRIVATE_FIELDS):
        raise StatusProtocolError("invalid public activity body")
    return decoded


def stamped_request(action: str, **fields: object) -> dict[str, object]:
    """Return the sole version-stamped request shape used by statusd clients."""

    request = {"action": action, "protocol_version": STATUSD_PROTOCOL_VERSION, **fields}
    validate_request(request)
    return request

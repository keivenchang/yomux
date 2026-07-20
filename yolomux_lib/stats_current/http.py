# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authenticated, current-only HTTP forwarding policy for YO!stats."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol
from urllib.parse import parse_qs

from yolomux_lib.stats_current import protocol, resolution as stats_resolution

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
class DeltaStreamResult:
    status: HTTPStatus
    metadata: Mapping[str, object]
    body: bytes = b""


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

    @staticmethod
    def capabilities() -> Mapping[str, object]:
        return stats_resolution.wire_capabilities()

    def _startup_failure(self) -> Mapping[str, object] | None:
        if self.client.ensure_started():
            self._logged_unavailable_reason = ""
            return None
        status = self.client.status()
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
        try:
            requested = parse_http_snapshot_query(raw_query)
        except protocol.UnsupportedRequest as error:
            return SnapshotHttpResult(HTTPStatus.BAD_REQUEST, payload=error.response)
        startup_failure = self._startup_failure()
        if startup_failure is not None:
            status = (
                HTTPStatus.UPGRADE_REQUIRED
                if startup_failure.get("status") == "upgrade_required"
                or startup_failure.get("error_code") == "upgrade_required"
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            return SnapshotHttpResult(status, payload=startup_failure)

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
        )
        metadata, body = self.client.snapshot(request)
        state = metadata.get("status")

        if metadata.get("ok") is True and metadata.get("not_modified") is True and not body:
            return SnapshotHttpResult(HTTPStatus.NOT_MODIFIED)
        if metadata.get("ok") is True and body and metadata.get("content_type") == "application/json":
            return SnapshotHttpResult(HTTPStatus.OK, body=body)
        if state == "pending" and not body:
            return SnapshotHttpResult(HTTPStatus.SERVICE_UNAVAILABLE, payload=metadata)
        if state == "unsupported" and not body:
            return SnapshotHttpResult(HTTPStatus.BAD_REQUEST, payload=metadata)
        if (state == "upgrade_required" or metadata.get("error_code") == "upgrade_required") and not body:
            return SnapshotHttpResult(HTTPStatus.UPGRADE_REQUIRED, payload=metadata)
        return SnapshotHttpResult(HTTPStatus.SERVICE_UNAVAILABLE, payload=_unavailable())

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
            status = (
                HTTPStatus.UPGRADE_REQUIRED
                if startup_failure.get("status") == "upgrade_required"
                or startup_failure.get("error_code") == "upgrade_required"
                else HTTPStatus.SERVICE_UNAVAILABLE
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
        if metadata.get("ok") is True and metadata.get("not_modified") is True and not body:
            return DeltaStreamResult(HTTPStatus.NOT_MODIFIED, metadata)
        if metadata.get("ok") is True and body and metadata.get("content_type") == "application/json":
            return DeltaStreamResult(HTTPStatus.OK, metadata, body)
        if state == "repair_required" and not body:
            return DeltaStreamResult(HTTPStatus.CONFLICT, metadata)
        if state == "pending" and not body:
            return DeltaStreamResult(HTTPStatus.SERVICE_UNAVAILABLE, metadata)
        if state == "unsupported" and not body:
            return DeltaStreamResult(HTTPStatus.BAD_REQUEST, metadata)
        if (state == "upgrade_required" or metadata.get("error_code") == "upgrade_required") and not body:
            return DeltaStreamResult(HTTPStatus.UPGRADE_REQUIRED, metadata)
        return DeltaStreamResult(HTTPStatus.SERVICE_UNAVAILABLE, _unavailable())

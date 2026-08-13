# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded payload contract for durable browser observations."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..diagnostic_redaction import redact_diagnostic_value


class BrowserPayloadError(ValueError):
    """A browser observation contains an unsupported or unbounded value."""


# Fields whose byte bound a redaction marker can push past, and the endpoint
# fields a redacted /share/... path can turn into a non-absolute marker.
_RETAINED_TEXT_BOUNDS = (("message", 500), ("stack", 4000))
_RETAINED_ENDPOINT_FIELDS = ("source", "route", "endpoint")


def _bound_retained_text(text: str, maximum: int) -> str:
    """UTF-8-safe re-bound with the truncation marker reserved INSIDE the byte
    limit, so text that a redaction marker expanded can never exceed the bound."""
    if len(text.encode("utf-8")) <= maximum:
        return text
    marker = "[truncated]"
    budget = max(0, maximum - len(marker.encode("utf-8")))
    return text.encode("utf-8")[:budget].decode("utf-8", "ignore") + marker


def sanitize_retained_payload(validated: Mapping[str, object]) -> dict[str, object]:
    """W2: redact secrets from a raw-validated browser payload, then restore the
    typed contract that redaction can violate. `redact_diagnostic_value` markers
    are longer than short secrets (expanding `message`/`stack` past their byte
    bound) and turn a valid `/share/...` endpoint into a non-absolute marker, so
    the mandatory second `families.validate_payload` would otherwise fail closed
    on a genuinely valid input. Re-bound the bounded-text fields and normalize a
    redaction-changed endpoint to one safe absolute path; never trim before
    redaction and never retain the raw value."""
    redacted = redact_diagnostic_value(dict(validated))
    if not isinstance(redacted, dict):
        raise BrowserPayloadError("sanitized browser payload must be an object")
    for field, maximum in _RETAINED_TEXT_BOUNDS:
        current = redacted.get(field)
        if isinstance(current, str):
            redacted[field] = _bound_retained_text(current, maximum)
    for field in _RETAINED_ENDPOINT_FIELDS:
        current = redacted.get(field)
        if isinstance(current, str) and not current.startswith("/"):
            # redaction replaced a valid absolute /share/... path with a marker;
            # restore a safe, type-preserving absolute path rather than reject.
            redacted[field] = "/share/[redacted]"
    return redacted


EVENT_KINDS = frozenset({
    "api", "sse", "heartbeat", "disconnect", "page_load", "finder_usable",
    "interaction", "operation_wait", "long_task", "warning", "error", "unhandledrejection",
})
COMMON_FIELDS = frozenset({"journey_id", "code_revision", "browser_family"})
API_PHASE_FIELDS = frozenset({
    "queue_ms", "connect_ms", "tls_ms", "ttfb_ms", "download_ms", "apply_render_ms",
})
PAGE_PHASE_FIELDS = frozenset({
    "navigation_ms", "bundle_parse_eval_ms", "first_paint_ms",
    "first_contentful_paint_ms", "first_api_ms", "fanout_ms", "interactive_ms",
    "app_ready_ms",
})
UPLOAD_HEALTH_FIELDS = frozenset({
    "upload_queue_depth", "upload_drops", "upload_retries", "instrumentation_cost_ms",
})
FAILURE_PROVENANCE = frozenset({"controlled_probe", "confirmed_real"})
FAILURE_DELIVERY_OUTCOMES = frozenset({"failed", "stalled", "timeout", "rejected", "dropped", "retrying"})
FAILURE_FIELDS = frozenset({
    "signature", "message", "stack", "source", "line", "column", "provenance",
    "request_id", "route", "event_type", "wall_time", "delivery_outcome", "status",
})
_PACIFIC_WALL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} P(?:S|D)T$")


def validate_payload(payload: Mapping[str, object]) -> None:
    """Validate kind-specific browser fields after generic family validation."""

    kind = payload["kind"]
    if kind not in EVENT_KINDS:
        raise BrowserPayloadError(
            f"browser.kind must be one of: {', '.join(sorted(EVENT_KINDS))}"
        )
    present = set(payload) - {"kind"}
    common = present & COMMON_FIELDS
    present -= common
    if "journey_id" in common:
        _validate_token(payload["journey_id"], "browser.journey_id", maximum=96)
    if "code_revision" in common:
        _validate_token(payload["code_revision"], "browser.code_revision", maximum=80)
    if "browser_family" in common and payload["browser_family"] not in {
        "chromium", "firefox", "safari", "other",
    }:
        raise BrowserPayloadError("browser.browser_family must be a bounded browser family")
    if kind == "disconnect":
        if present != {"duration_ms"}:
            raise BrowserPayloadError("browser disconnect requires only duration_ms")
        return
    if kind in {"warning", "error", "unhandledrejection"}:
        if not present <= FAILURE_FIELDS or not {"signature", "message", "source"} <= present:
            raise BrowserPayloadError(
                f"browser {kind} requires signature, message, source, and optional stack position"
            )
        _validate_token(payload["signature"], "browser.signature", maximum=32)
        _validate_text(payload["message"], "browser.message", maximum=500)
        if "stack" in payload:
            _validate_text(payload["stack"], "browser.stack", maximum=4000)
        _validate_endpoint(payload["source"])
        _validate_integer(payload.get("line"), "browser.line", maximum=10_000_000)
        _validate_integer(payload.get("column"), "browser.column", maximum=10_000_000)
        if "request_id" in payload:
            _validate_request_id(payload["request_id"])
        if "route" in payload:
            _validate_endpoint(payload["route"])
        if "event_type" in payload:
            _validate_token(payload["event_type"], "browser.event_type", maximum=64)
        if "wall_time" in payload and (
            not isinstance(payload["wall_time"], str)
            or _PACIFIC_WALL_TIME.fullmatch(payload["wall_time"]) is None
        ):
            raise BrowserPayloadError("browser.wall_time must be an exact Pacific wall time")
        if "delivery_outcome" in payload and payload["delivery_outcome"] not in FAILURE_DELIVERY_OUTCOMES:
            raise BrowserPayloadError("browser.delivery_outcome must be a bounded failure outcome")
        _validate_integer(payload.get("status"), "browser.status", minimum=100, maximum=599)
        if "provenance" in payload and payload["provenance"] not in FAILURE_PROVENANCE:
            raise BrowserPayloadError("browser.provenance must be controlled_probe or confirmed_real")
        return
    if kind == "page_load":
        allowed = {"endpoint", "fanout_count", "max_concurrency", *PAGE_PHASE_FIELDS}
        if not present <= allowed or "endpoint" not in payload:
            raise BrowserPayloadError(
                "browser page_load accepts only endpoint, fanout, concurrency, and page phases"
            )
        _validate_endpoint(payload["endpoint"])
        _validate_integer(payload.get("fanout_count"), "browser.fanout_count", maximum=10_000)
        _validate_integer(payload.get("max_concurrency"), "browser.max_concurrency", maximum=1_000)
        return
    if "duration_ms" in present:
        raise BrowserPayloadError(f"browser {kind} does not accept duration_ms")
    if kind == "api":
        _validate_api(payload, present)
        return
    if kind == "finder_usable":
        if not present <= {"latency_ms", "entry_count"}:
            raise BrowserPayloadError("browser finder_usable accepts only latency_ms and entry_count")
        _validate_integer(payload.get("entry_count"), "browser.entry_count", maximum=1_000_000)
        return
    if kind == "interaction":
        allowed = {
            "latency_ms", "input_delay_ms", "processing_ms", "presentation_delay_ms",
            "interaction_type",
        }
        if not present <= allowed:
            raise BrowserPayloadError("browser interaction contains unsupported profiling fields")
        if "interaction_type" in payload:
            _validate_token(payload["interaction_type"], "browser.interaction_type", maximum=32)
        return
    if kind == "operation_wait":
        _validate_operation_wait(payload, present)
        return
    if kind == "long_task":
        if not present <= {"latency_ms"}:
            raise BrowserPayloadError("browser long_task accepts only latency_ms")
        return
    allowed = {"latency_ms", "bytes"}
    if kind == "heartbeat":
        allowed |= UPLOAD_HEALTH_FIELDS
    if not present <= allowed:
        raise BrowserPayloadError(f"browser {kind} contains unsupported profiling fields")


def _validate_api(payload: Mapping[str, object], present: set[str]) -> None:
    allowed = {
        "latency_ms", "bytes", "endpoint", "method", "request_id", "status",
        "connection_protocol", *API_PHASE_FIELDS,
    }
    if not present <= allowed:
        raise BrowserPayloadError("browser api contains unsupported profiling fields")
    if "endpoint" in payload:
        _validate_endpoint(payload["endpoint"])
    if "method" in payload:
        method = payload["method"]
        if not isinstance(method, str) or not 1 <= len(method) <= 16 or not method.isascii() or not method.replace("-", "").isalpha() or method != method.upper():
            raise BrowserPayloadError("browser.method must be a bounded uppercase HTTP method")
    if "request_id" in payload:
        _validate_request_id(payload["request_id"])
    if "connection_protocol" in payload:
        _validate_token(
            payload["connection_protocol"], "browser.connection_protocol", maximum=24,
        )
    _validate_integer(payload.get("status"), "browser.status", minimum=100, maximum=599)


def _validate_operation_wait(payload: Mapping[str, object], present: set[str]) -> None:
    if not present <= {"latency_ms", "operation_kind", "outcome", "request_id"}:
        raise BrowserPayloadError("browser operation_wait contains unsupported profiling fields")
    if "operation_kind" in payload:
        _validate_token(payload["operation_kind"], "browser.operation_kind", maximum=64)
    if payload.get("outcome") not in {"ready", "failed"}:
        raise BrowserPayloadError("browser.outcome must be ready or failed")
    if "request_id" in payload:
        _validate_request_id(payload["request_id"])


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, str) or not value.startswith("/") or len(value.encode("utf-8")) > 240 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BrowserPayloadError("browser.endpoint must be a bounded absolute path")
    if "?" in value or "#" in value:
        raise BrowserPayloadError("browser.endpoint must not contain query or fragment data")


def _validate_token(value: object, name: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= maximum
        or any(not (character.isascii() and (character.isalnum() or character in "._-/")) for character in value)
    ):
        raise BrowserPayloadError(f"{name} must be bounded ASCII token text")


def _validate_request_id(value: object) -> None:
    if not isinstance(value, str) or not value.startswith("r-") or len(value.encode("utf-8")) > 128 or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise BrowserPayloadError("browser.request_id must be a bounded request id")


def _validate_text(value: object, name: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= maximum
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
        or any(ord(character) == 127 for character in value)
    ):
        raise BrowserPayloadError(f"{name} must be bounded text")


def _validate_integer(
    value: object,
    name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not float(value).is_integer() or not minimum <= int(value) <= maximum:
        raise BrowserPayloadError(f"{name} must be an integer from {minimum} through {maximum}")

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Canonical validation for current YO!stats usage atoms."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType

from . import identity
from .storage import UsageAtom
from .storage import UsageAtomTombstone


MAX_EVENT_ID_BYTES = identity.MAX_EVENT_ID_BYTES
MAX_VALUE_BYTES = identity.MAX_SERIES_COMPONENT_BYTES

DIRECTIONS = frozenset({"input", "output"})
MODALITIES = frozenset({"text", "image"})
CACHE_ROLES = frozenset({"none", "read", "write_5m", "write_1h"})
UNITS = frozenset({"tokens", "requests"})

REQUIRED_PAYLOAD_FIELDS = frozenset(
    {"quantity", "provider", "model", "agent_id", "telemetry_complete"}
)
OPTIONAL_PAYLOAD_FIELDS = frozenset(
    {"pricing_profile", "service_tier", "effort", "execution_source", "thread_id", "model_evidence"}
)
PAYLOAD_FIELDS = REQUIRED_PAYLOAD_FIELDS | OPTIONAL_PAYLOAD_FIELDS


class UsageValidationError(ValueError):
    """A usage atom violates the sole current source-fact contract."""


def usage_atom_from_source(fields: Mapping[str, object] | object) -> UsageAtom:
    """Translate one structured provider/transcript atom into the current contract."""

    if not isinstance(fields, Mapping):
        try:
            fields = vars(fields)
        except TypeError as error:
            raise UsageValidationError("source usage atom must be an object") from error
    agent_id = fields.get("agent_id") or fields.get("tmux_key")
    agent_id = agent_id or fields.get("agent_thread_id") or fields.get("root_thread_id")
    agent_id = agent_id or fields.get("source") or "unknown"
    payload: dict[str, object] = {
        "quantity": fields.get("quantity", 0),
        "provider": fields.get("provider") or "unknown",
        "model": fields.get("model") or "unknown",
        "agent_id": agent_id,
        "telemetry_complete": fields.get("telemetry_complete", False),
    }
    optional = {
        "pricing_profile": fields.get("pricing_profile"),
        "service_tier": fields.get("service_tier"),
        "effort": fields.get("effort"),
        "model_evidence": fields.get("model_evidence"),
        "execution_source": fields.get("execution_source") or fields.get("agent_kind") or fields.get("endpoint"),
        "thread_id": fields.get("thread_id") or fields.get("agent_thread_id") or fields.get("root_thread_id"),
    }
    payload.update({
        name: value
        for name, value in optional.items()
        if value is not None and value != ""
    })
    return normalize_usage_atom(UsageAtom(
        event_id=fields.get("event_id"),
        direction=fields.get("direction"),
        modality=fields.get("modality"),
        cache_role=fields.get("cache_role"),
        unit=fields.get("unit"),
        observed_at=fields.get("observed_at", fields.get("timestamp")),
        payload=payload,
    ))


def legacy_fork_usage_tombstone_from_source(
    fields: Mapping[str, object] | object,
) -> UsageAtomTombstone:
    """Build the narrow deletion proof emitted only for replayed Codex fork history."""

    if not isinstance(fields, Mapping):
        try:
            fields = vars(fields)
        except TypeError as error:
            raise UsageValidationError("source usage tombstone must be an object") from error
    atom = usage_atom_from_source(fields)
    thread_id = _text(fields.get("agent_thread_id"), "thread_id")
    return UsageAtomTombstone(
        atom.event_id,
        atom.direction,
        atom.modality,
        atom.cache_role,
        atom.unit,
        atom.observed_at,
        float(atom.payload["quantity"]),
        str(atom.payload["provider"]),
        str(atom.payload["model"]),
        thread_id,
    )


def _text(value: object, name: str, *, limit: int = MAX_VALUE_BYTES) -> str:
    try:
        return identity.identity_text(value, name, maximum_bytes=limit, strip=True)
    except identity.IdentityValidationError as error:
        raise UsageValidationError(str(error)) from error


def _enum(value: object, name: str, choices: frozenset[str]) -> str:
    normalized = _text(value, name).lower()
    if normalized not in choices:
        raise UsageValidationError(
            f"{name} must be one of: {', '.join(sorted(choices))}"
        )
    return normalized


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageValidationError(f"{name} must be a non-negative finite number")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise UsageValidationError(f"{name} must be a non-negative finite number") from error
    if not math.isfinite(normalized) or normalized < 0:
        raise UsageValidationError(f"{name} must be a non-negative finite number")
    return normalized


def normalize_usage_atom(atom: UsageAtom) -> UsageAtom:
    """Return one immutable canonical atom suitable for storage and folding."""

    if not isinstance(atom, UsageAtom):
        raise UsageValidationError("usage atom must be a storage.UsageAtom")
    event_id = _text(atom.event_id, "event_id", limit=MAX_EVENT_ID_BYTES)
    direction = _enum(atom.direction, "direction", DIRECTIONS)
    modality = _enum(atom.modality, "modality", MODALITIES)
    cache_role = _enum(atom.cache_role, "cache_role", CACHE_ROLES)
    unit = _enum(atom.unit, "unit", UNITS)
    if direction == "output" and cache_role != "none":
        raise UsageValidationError("output usage cannot carry an input cache role")
    observed_at = _number(atom.observed_at, "observed_at")
    if not isinstance(atom.payload, Mapping):
        raise UsageValidationError("payload must be an object")
    if any(not isinstance(key, str) for key in atom.payload):
        raise UsageValidationError("payload fields must be strings")
    keys = set(atom.payload)
    missing = REQUIRED_PAYLOAD_FIELDS - keys
    unknown = keys - PAYLOAD_FIELDS
    if missing:
        raise UsageValidationError(
            f"payload is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise UsageValidationError(
            f"payload has unknown fields: {', '.join(sorted(unknown))}"
        )
    telemetry_complete = atom.payload["telemetry_complete"]
    if not isinstance(telemetry_complete, bool):
        raise UsageValidationError("payload.telemetry_complete must be a boolean")
    payload: dict[str, object] = {
        "quantity": _number(atom.payload["quantity"], "payload.quantity"),
        "provider": _text(atom.payload["provider"], "payload.provider"),
        "model": _text(atom.payload["model"], "payload.model"),
        "agent_id": _text(atom.payload["agent_id"], "payload.agent_id"),
        "telemetry_complete": telemetry_complete,
    }
    for name in sorted(OPTIONAL_PAYLOAD_FIELDS):
        if name in atom.payload:
            payload[name] = _text(atom.payload[name], f"payload.{name}")
    return UsageAtom(
        event_id=event_id,
        direction=direction,
        modality=modality,
        cache_role=cache_role,
        unit=unit,
        observed_at=observed_at,
        payload=MappingProxyType(payload),
    )

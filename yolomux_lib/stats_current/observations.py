# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Strict authenticated browser-observation input for current YO!stats."""

from __future__ import annotations

import math
from collections.abc import Mapping

from . import families, storage
from .http import bound_client_id


MAX_OBSERVATIONS = 1_000
MAX_CLIENT_ID_BYTES = 128
MAX_EVENT_ID_BYTES = 512
ROOT_FIELDS = frozenset({
    "protocol_version", "schema_generation", "client_id", "observations",
})
OBSERVATION_FIELDS = frozenset({
    "event_id", "family", "source_id", "observed_at", "epoch_id", "payload",
})


class BrowserObservationError(ValueError):
    """A browser upload is not the exact bounded current contract."""


class BrowserObservationUpgradeRequired(BrowserObservationError):
    """The browser writer protocol/schema does not match this server."""


def parse_browser_observations(
    value: object,
    *,
    client_binding_secret: bytes,
    authenticated_username: str,
) -> tuple[storage.Observation, ...]:
    """Validate and privacy-bind one original-observation upload without aggregation."""

    root = _exact_object(value, ROOT_FIELDS, "browser observation upload")
    if (
        root["protocol_version"] != storage.MIN_WRITER_PROTOCOL
        or root["schema_generation"] != storage.SCHEMA_VERSION
    ):
        raise BrowserObservationUpgradeRequired("browser stats writer is not current")
    raw_client_id = _text(root["client_id"], "client_id", MAX_CLIENT_ID_BYTES)
    raw_items = root["observations"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_OBSERVATIONS:
        raise BrowserObservationError(
            f"observations must contain 1..{MAX_OBSERVATIONS} items"
        )
    source_id = bound_client_id(
        client_binding_secret,
        authenticated_username,
        raw_client_id,
    )
    observations = []
    for index, raw_item in enumerate(raw_items):
        item = _exact_object(raw_item, OBSERVATION_FIELDS, f"observations[{index}]")
        if item["family"] != "browser":
            raise BrowserObservationError("browser observation family must be browser")
        if item["source_id"] != raw_client_id:
            raise BrowserObservationError("browser observation source_id must match client_id")
        raw_event_id = _text(
            item["event_id"], f"observations[{index}].event_id", MAX_EVENT_ID_BYTES,
        )
        raw_epoch_id = _text(
            item["epoch_id"], f"observations[{index}].epoch_id", MAX_EVENT_ID_BYTES,
        )
        observed_at = _number(item["observed_at"], f"observations[{index}].observed_at")
        try:
            payload = families.validate_payload("browser", item["payload"])
        except families.FamilyValidationError as error:
            raise BrowserObservationError(str(error)) from error
        observations.append(storage.Observation(
            event_id=bound_client_id(
                client_binding_secret,
                authenticated_username,
                f"{raw_client_id}:event:{raw_event_id}",
            ),
            family="browser",
            source_id=source_id,
            observed_at=observed_at,
            epoch_id=bound_client_id(
                client_binding_secret,
                authenticated_username,
                f"{raw_client_id}:epoch:{raw_epoch_id}",
            ),
            owner_generation=0,
            payload=payload,
        ))
    return tuple(observations)


def _exact_object(
    value: object,
    fields: frozenset[str],
    name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise BrowserObservationError(f"{name} must be an object")
    if set(value) != fields:
        raise BrowserObservationError(f"{name} fields must be exactly {sorted(fields)}")
    return value


def _text(value: object, name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrowserObservationError(f"{name} must be non-empty text")
    normalized = value.strip()
    if (
        len(normalized.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise BrowserObservationError(f"{name} is too long or contains controls")
    return normalized


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrowserObservationError(f"{name} must be a non-negative finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise BrowserObservationError(f"{name} must be a non-negative finite number")
    return number


__all__ = (
    "BrowserObservationError",
    "BrowserObservationUpgradeRequired",
    "MAX_OBSERVATIONS",
    "parse_browser_observations",
)

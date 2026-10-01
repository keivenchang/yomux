# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sole current Range, Resolution, and delivery-cadence policy."""

from __future__ import annotations

RING_CAPACITIES: dict[int, int] = {1: 300, 10: 180, 60: 480, 300: 288}
RESOLUTION_CHOICES: tuple[int, ...] = tuple(RING_CAPACITIES)
# The longest window the GUI can ask for. This is the display knob: change it
# here and the top rung of the range ladder moves with it. It is deliberately
# NOT the same value as storage.RETENTION_SECONDS -- how long history is kept on
# disk is a separate decision -- but the two are ordered: retention must be at
# least this large, or the browser asks for a range whose older half was already
# pruned and renders a truncated span as if it were complete. storage states and
# enforces that invariant; nothing else may re-spell this number.
MAX_RANGE_SECONDS = 24 * 60 * 60
RANGE_SECONDS: tuple[int, ...] = (
    5 * 60,
    15 * 60,
    30 * 60,
    60 * 60,
    2 * 60 * 60,
    4 * 60 * 60,
    8 * 60 * 60,
    16 * 60 * 60,
    MAX_RANGE_SECONDS,
)
MAX_BUCKETS = 600
MIN_BUCKETS = 12
MAX_LIVE_CADENCE_SECONDS = 60
AUTO = "AUTO"


def explicit_resolutions(range_seconds: int) -> tuple[int, ...]:
    if range_seconds <= 0:
        raise ValueError(f"range_seconds must be positive, got {range_seconds!r}")
    return tuple(
        resolution
        for resolution, slot_count in RING_CAPACITIES.items()
        if MIN_BUCKETS <= range_seconds / resolution <= slot_count
    )


def auto_resolution(range_seconds: int) -> int:
    if range_seconds <= 0:
        raise ValueError(f"range_seconds must be positive, got {range_seconds!r}")
    offered = explicit_resolutions(range_seconds)
    if offered:
        return offered[0] if range_seconds <= 15 * 60 else offered[-1]
    for resolution, slot_count in RING_CAPACITIES.items():
        if range_seconds / resolution <= slot_count:
            return resolution
    raise ValueError(
        f"no resolution in {RESOLUTION_CHOICES} retains range {range_seconds}s within "
        "its ring capacity"
    )


def bucket_count(range_seconds: int, resolution_seconds: int) -> int:
    if range_seconds <= 0 or resolution_seconds <= 0:
        raise ValueError(
            "range_seconds and resolution_seconds must be positive, got "
            f"{range_seconds!r}, {resolution_seconds!r}"
        )
    return range_seconds // resolution_seconds


def is_supported(range_seconds: int, resolution_seconds: int) -> bool:
    return (
        range_seconds in RANGE_SECONDS
        and resolution_seconds in explicit_resolutions(range_seconds)
    )


def resolve_requested(range_seconds: int, resolution: int | str) -> int:
    if range_seconds not in RANGE_SECONDS:
        raise ValueError(f"unsupported range_seconds {range_seconds!r}")
    if resolution == AUTO:
        return auto_resolution(range_seconds)
    if resolution in explicit_resolutions(range_seconds):
        return int(resolution)
    raise ValueError(
        f"unsupported resolution {resolution!r} for range {range_seconds}s; "
        f"offered {explicit_resolutions(range_seconds)}"
    )


def normalize_preference(range_seconds: int, resolution: int | str) -> int | str:
    if resolution == AUTO:
        return AUTO
    if range_seconds in RANGE_SECONDS and resolution in explicit_resolutions(range_seconds):
        return int(resolution)
    return AUTO


def live_cadence_seconds(resolution_seconds: int) -> int:
    if resolution_seconds not in RESOLUTION_CHOICES:
        raise ValueError(f"unsupported concrete resolution {resolution_seconds!r}")
    return min(resolution_seconds, MAX_LIVE_CADENCE_SECONDS)


def resolution_matrix() -> dict[int, dict[str, object]]:
    return {
        range_seconds: {
            "auto": auto_resolution(range_seconds),
            "explicit": explicit_resolutions(range_seconds),
        }
        for range_seconds in RANGE_SECONDS
    }


def wire_capabilities() -> dict[str, object]:
    return {
        "resolution_choices": list(RESOLUTION_CHOICES),
        "max_buckets": MAX_BUCKETS,
        "min_buckets": MIN_BUCKETS,
        "max_live_cadence_seconds": MAX_LIVE_CADENCE_SECONDS,
        "ranges": [
            {
                "range_seconds": range_seconds,
                "auto_resolution_seconds": auto_resolution(range_seconds),
                "explicit_resolution_seconds": list(explicit_resolutions(range_seconds)),
                "buckets": {
                    resolution: bucket_count(range_seconds, resolution)
                    for resolution in explicit_resolutions(range_seconds)
                },
            }
            for range_seconds in RANGE_SECONDS
        ],
    }

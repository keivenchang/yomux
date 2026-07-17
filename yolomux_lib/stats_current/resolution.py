# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sole current Range, Resolution, and delivery-cadence policy."""

from __future__ import annotations

RESOLUTION_CHOICES: tuple[int, ...] = (1, 10, 60, 300)
RANGE_SECONDS: tuple[int, ...] = (
    5 * 60,
    15 * 60,
    30 * 60,
    60 * 60,
    2 * 60 * 60,
    4 * 60 * 60,
    8 * 60 * 60,
    16 * 60 * 60,
    24 * 60 * 60,
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
        for resolution in RESOLUTION_CHOICES
        if MIN_BUCKETS <= range_seconds / resolution <= MAX_BUCKETS
    )


def auto_resolution(range_seconds: int) -> int:
    if range_seconds <= 0:
        raise ValueError(f"range_seconds must be positive, got {range_seconds!r}")
    for resolution in RESOLUTION_CHOICES:
        if range_seconds / resolution <= MAX_BUCKETS:
            return resolution
    raise ValueError(
        f"no resolution in {RESOLUTION_CHOICES} keeps {range_seconds}s within "
        f"{MAX_BUCKETS} buckets"
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

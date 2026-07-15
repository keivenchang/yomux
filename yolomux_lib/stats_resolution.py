# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Canonical YO!stats Range x Resolution policy - one server-owned source of truth.

This module is the single owner of the exact resolution universe and the preset
Range x Resolution matrix described in DOIT.1.md. statsd capabilities, cache
construction, request validation, AUTO resolution, YO!stats, and YO!cost all read
from here rather than hand-copying a second table. The browser mirrors these
values through a parity test (DOIT.1 item 6), never an independent derivation.

The whole matrix is reproducible from one formula, so a hand-edited cell that
violates the bucket budget fails a test instead of shipping:

    explicit resolutions for a range = every r in RESOLUTION_CHOICES with
        MIN_BUCKETS <= range_seconds / r <= MAX_BUCKETS
    AUTO for a range = the finest r with range_seconds / r <= MAX_BUCKETS

Every AUTO value is always also in its range's explicit set (asserted in tests).
"""

from __future__ import annotations

# The complete numeric Resolution universe. No other numeric duration (2s, 5s,
# 30s, 120s, 600s, ...) is ever a current cache key, requested/returned
# resolution, option, label, or displayed value.
RESOLUTION_CHOICES: tuple[int, ...] = (1, 10, 60, 300)

# The nine preset ranges, in seconds (5m, 15m, 30m, 1h, 2h, 4h, 8h, 16h, 24h).
# Mirrors the frontend jsDebugGraphRangeOptions; the parity test keeps them equal.
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

# A materialization must draw every point without client decimation, so no matrix
# cell may exceed MAX_BUCKETS; an explicit choice coarse enough to fall below
# MIN_BUCKETS is dropped from the menu (it would render mostly-empty cells).
MAX_BUCKETS: int = 600
MIN_BUCKETS: int = 12

# The nonnumeric selection the UI may show. AUTO always resolves to one concrete
# RESOLUTION_CHOICES value before any request; there is no separate AUTO universe.
AUTO: str = "AUTO"


def explicit_resolutions(range_seconds: int) -> tuple[int, ...]:
    """Explicit resolutions offered for a range, finest first.

    Every r whose bucket count range_seconds/r lands within [MIN_BUCKETS, MAX_BUCKETS].
    """
    if range_seconds <= 0:
        raise ValueError(f"range_seconds must be positive, got {range_seconds!r}")
    return tuple(
        r
        for r in RESOLUTION_CHOICES
        if MIN_BUCKETS <= range_seconds / r <= MAX_BUCKETS
    )


def auto_resolution(range_seconds: int) -> int:
    """Concrete resolution AUTO resolves to for a range: the finest r within budget.

    RESOLUTION_CHOICES is sorted ascending, so the first r whose bucket count is at
    or below MAX_BUCKETS is the finest allowed.
    """
    if range_seconds <= 0:
        raise ValueError(f"range_seconds must be positive, got {range_seconds!r}")
    for r in RESOLUTION_CHOICES:
        if range_seconds / r <= MAX_BUCKETS:
            return r
    # Only reachable for a range so large the coarsest choice still overflows; our
    # preset ranges never do, but keep the failure explicit rather than silent.
    raise ValueError(
        f"no resolution in {RESOLUTION_CHOICES} keeps {range_seconds}s within "
        f"{MAX_BUCKETS} buckets"
    )


def bucket_count(range_seconds: int, resolution_seconds: int) -> int:
    """Number of fixed buckets a (range, resolution) snapshot contains."""
    if range_seconds <= 0 or resolution_seconds <= 0:
        raise ValueError(
            f"range_seconds and resolution_seconds must be positive, got "
            f"{range_seconds!r}, {resolution_seconds!r}"
        )
    return range_seconds // resolution_seconds


def is_supported(range_seconds: int, resolution_seconds: int) -> bool:
    """True when (range, explicit resolution) is a valid preset cache key."""
    return (
        range_seconds in RANGE_SECONDS
        and resolution_seconds in explicit_resolutions(range_seconds)
    )


def resolve_requested(range_seconds: int, resolution: int | str) -> int:
    """Resolve a request's resolution to one concrete supported value, or raise.

    AUTO resolves to the range's auto_resolution. An explicit value must be a
    supported preset for the range; the server never silently promotes, clamps,
    or substitutes it. Callers turn the ValueError into a structured
    unsupported/pending response.
    """
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
    """Normalize a persisted/deeplinked selection to a currently valid choice.

    A value outside the universe, or valid globally but unavailable for its
    range, becomes AUTO so it normalizes visibly before any request is sent.
    """
    if resolution == AUTO:
        return AUTO
    if range_seconds in RANGE_SECONDS and resolution in explicit_resolutions(range_seconds):
        return int(resolution)
    return AUTO


def resolution_matrix() -> dict[int, dict[str, object]]:
    """The full preset matrix, derived (not hand-maintained) from the formula.

    Maps each preset range to its AUTO concrete resolution and explicit choices.
    """
    return {
        range_seconds: {
            "auto": auto_resolution(range_seconds),
            "explicit": explicit_resolutions(range_seconds),
        }
        for range_seconds in RANGE_SECONDS
    }


def wire_capabilities() -> dict[str, object]:
    """JSON-safe advertisement of the canonical policy for the wire/browser.

    The single owner also owns serialization so responses cannot drift from the
    matrix. `ranges` is one entry per preset range with its AUTO concrete value,
    explicit choices, and their bucket counts.
    """
    return {
        "resolution_choices": list(RESOLUTION_CHOICES),
        "max_buckets": MAX_BUCKETS,
        "min_buckets": MIN_BUCKETS,
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

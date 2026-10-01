# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared interval arithmetic for active and quiet polling."""

from __future__ import annotations


def quiet_poll_interval(
    active_interval: float,
    quiet_interval: float,
    quiet_fraction: float,
    jitter_seconds: float = 0.0,
) -> float:
    """Interpolate between active and quiet cadence with bounded caller jitter."""

    active = max(0.0, float(active_interval))
    quiet = max(active, float(quiet_interval))
    fraction = min(max(0.0, float(quiet_fraction)), 1.0)
    interval = active + fraction * (quiet - active)
    return max(active, interval + float(jitter_seconds))

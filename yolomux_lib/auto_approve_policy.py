# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared polling policy for YOLO approval watchers."""

from __future__ import annotations

AUTO_APPROVE_QUIET_RAMP_SECONDS = 60.0
AUTO_APPROVE_QUIET_MAX_INTERVAL_SECONDS = 4.0
AUTO_APPROVE_QUIET_JITTER_SECONDS = 0.5


def auto_approve_poll_is_quiet(screen_key: str, screen_changed: bool) -> bool:
    """Only a static, non-working screen may back off approval polling."""
    return screen_key != "working" and not screen_changed


def auto_approve_quiet_poll_interval(base_interval: float, quiet_seconds: float, jitter_seconds: float = 0.0) -> float:
    """Linearly ramp a quiet watcher, then apply its bounded desynchronizing jitter."""
    base = max(0.0, float(base_interval))
    cap = max(base, AUTO_APPROVE_QUIET_MAX_INTERVAL_SECONDS)
    ramp = min(max(0.0, float(quiet_seconds)) / AUTO_APPROVE_QUIET_RAMP_SECONDS, 1.0)
    interval = base + ramp * (cap - base)
    return max(base, interval + float(jitter_seconds))

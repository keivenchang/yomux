# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sole owner of the nightly YO!stats prune schedule.

Pruning is destructive maintenance, so it runs once a night at a local wall-clock
time the operator picks, not on an interval. Three rules define it, and each one
exists because its opposite fails silently:

* LOCAL TIME, RESOLVED EVERY TIME. The occurrence is computed from the system
  zone at each decision. An offset captured once at startup drifts an hour twice
  a year and then prunes at 01:30 or 03:30 forever.
* A MISSED WINDOW IS STILL DUE. The rule is "the most recent occurrence of the
  configured time has not been pruned yet", not "fire exactly at 02:30". A
  machine that is asleep every night at 02:30 still prunes the next time it runs,
  instead of never pruning while storage grows without a single symptom.
* AN UNUSABLE PREFERENCE FALLS BACK, NEVER OFF. Every parse failure resolves to
  DEFAULT_PRUNE_LOCAL_TIME and says it fell back. A preference that could disable
  cleanup would let the database grow forever with no signal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from datetime import timedelta

# Re-exported from the dependency-free leaf. workspace.settings imports the leaf
# directly; importing it through this module instead closes an import cycle that
# stops every local service from starting as its own process.
from ..prune_policy import DEFAULT_PRUNE_LOCAL_TIME
from ..prune_policy import PRUNE_LOCAL_TIME_CHOICES
from ..prune_policy import PRUNE_LOCAL_TIME_STEP_MINUTES


@dataclass(frozen=True)
class PruneTime:
    """One resolved local time-of-day plus how it was resolved."""

    hour: int
    minute: int
    configured: str
    fell_back: bool

    @property
    def text(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def parse_local_time(value: object) -> tuple[int, int] | None:
    """Return (hour, minute) for a well-formed ``HH:MM``, else None."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    hour_text, minute_text = parts
    if not (hour_text.isdigit() and minute_text.isdigit()):
        return None
    if len(hour_text) > 2 or len(minute_text) != 2:
        return None
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def resolve_local_time(value: object) -> PruneTime:
    """Resolve the configured preference, falling back rather than disabling."""

    parsed = parse_local_time(value)
    configured = value if isinstance(value, str) else ""
    if parsed is None:
        fallback = parse_local_time(DEFAULT_PRUNE_LOCAL_TIME)
        if fallback is None:
            raise ValueError(f"invalid DEFAULT_PRUNE_LOCAL_TIME {DEFAULT_PRUNE_LOCAL_TIME!r}")
        return PruneTime(fallback[0], fallback[1], configured, True)
    return PruneTime(parsed[0], parsed[1], configured, False)


def _occurrence_on(day: date, prune_time: PruneTime) -> float:
    """Return the epoch of ``prune_time`` on ``day`` in the current local zone.

    Both DST offsets are tried and each is checked by converting back: an offset
    is only usable if the clock really shows this wall time at that instant. That
    round trip, not ``tm_isdst=-1``, is what makes the answer stable.
    ``mktime(tm_isdst=-1)`` is explicitly unspecified for an ambiguous local time
    and glibc does return BOTH instants for the same input, which made the same
    autumn night look unpruned an hour after it had been pruned, and pruned twice.

    * Normal day: exactly one offset survives the round trip.
    * Autumn, ambiguous (the hour that happens twice): both survive; the earlier
      instant wins, so the second pass over that wall time is already behind the
      last prune and does not fire again.
    * Spring, nonexistent (the hour that is skipped): neither survives, because
      the clock never shows that time. The later instant wins -- the moment the
      clock jumps to -- so the night has exactly one occurrence instead of none.
    """

    fields = (day.year, day.month, day.day, prune_time.hour, prune_time.minute, 0, 0, 0)
    wanted = (day.year, day.month, day.day, prune_time.hour, prune_time.minute)
    candidates = [time.mktime(fields + (is_dst,)) for is_dst in (1, 0)]
    shown = [
        candidate
        for candidate in candidates
        if _wall_fields(candidate) == wanted
    ]
    return min(shown) if shown else max(candidates)


def _wall_fields(epoch: float) -> tuple[int, int, int, int, int]:
    local = time.localtime(epoch)
    return (local.tm_year, local.tm_mon, local.tm_mday, local.tm_hour, local.tm_min)


def _local_day(now: float) -> date:
    local = time.localtime(now)
    return date(local.tm_year, local.tm_mon, local.tm_mday)


def most_recent_occurrence(now: float, prune_time: PruneTime) -> float:
    """Return the latest occurrence of the configured local time at or before now."""

    today = _local_day(now)
    candidate = _occurrence_on(today, prune_time)
    if candidate <= now:
        return candidate
    return _occurrence_on(today - timedelta(days=1), prune_time)


def next_occurrence(now: float, prune_time: PruneTime) -> float:
    """Return the first occurrence of the configured local time after now."""

    today = _local_day(now)
    candidate = _occurrence_on(today, prune_time)
    if candidate > now:
        return candidate
    return _occurrence_on(today + timedelta(days=1), prune_time)


def is_due(now: float, last_pruned_at: float, prune_time: PruneTime) -> bool:
    """Return whether the most recent scheduled night has not been pruned yet.

    This is the catch-up rule: it is true immediately for a store that never
    pruned, true for a machine that was off at 02:30 and started at 09:00, and
    false for the rest of the day once that night's prune succeeded.
    """

    return last_pruned_at < most_recent_occurrence(now, prune_time)

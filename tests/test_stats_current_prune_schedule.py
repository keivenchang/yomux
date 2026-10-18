# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retention window, its ordering invariant, and the nightly prune schedule."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from yomux_lib.stats_current import prune_schedule
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.storage import DATABASE_FILENAME
from yomux_lib.stats_current.storage import Observation
from yomux_lib.stats_current.storage import PruneResult
from yomux_lib.stats_current.storage import RETENTION_SECONDS
from yomux_lib.stats_current.storage import StatsCurrentError
from yomux_lib.stats_current.storage import Store


PACIFIC = "America/Los_Angeles"
# 2026 US transitions: clocks jump 02:00 -> 03:00 on March 8 and 02:00 -> 01:00
# on November 1. 02:30 is exactly the hour that does not exist in spring, and
# 01:30 is exactly the hour that happens twice in autumn.
SPRING_FORWARD = (2026, 3, 8)
FALL_BACK = (2026, 11, 1)


@contextmanager
def local_zone(name: str):
    """Run the body with one fixed system zone, exactly as the daemon sees it."""

    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def local_epoch(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def local_text(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(epoch))


def fire_days(start: float, end: float, prune_time: prune_schedule.PruneTime, step: float = 300.0) -> list[str]:
    """Walk a clock and return the local day of every prune the schedule asks for."""

    last_pruned_at, now, fired = start, start, []
    while now <= end:
        if prune_schedule.is_due(now, last_pruned_at, prune_time):
            fired.append(time.strftime("%Y-%m-%d", time.localtime(now)))
            last_pruned_at = now
        now += step
    return fired


class FakePruneStore:
    """Minimal writer double: the service only prunes and reads its own clock."""

    def __init__(self) -> None:
        self.prunes: list[float] = []
        self.vacuums: list[float] = []
        self.source_generation = 7

    def prune(self, *, now: float) -> PruneResult:
        self.prunes.append(now)
        return PruneResult(0, 0, 0, 0, self.source_generation, 0, 0)

    def last_pruned_at(self) -> float:
        return self.prunes[-1] if self.prunes else 0.0

    def vacuum(self, *, completed_at: float) -> float:
        self.vacuums.append(completed_at)
        return completed_at

    # Declared, never probed for with `getattr`. The compaction guard reads both before it takes
    # any lock, and a double that silently lacked them would make the service skip the guard rather
    # than reveal that the double does not model compaction benefit at all. The pair means "a
    # rewrite would hand back everything", so these tests keep exercising the cadence and the cap.
    def reclaimable_ratio(self) -> float:
        return 1.0

    def reclaimable_ratio_at_last_vacuum(self) -> float:
        return 0.0




# --------------------------------------------------------------------------
# Retention vs the display window
# --------------------------------------------------------------------------








# --------------------------------------------------------------------------
# The preference value
# --------------------------------------------------------------------------












def test_preferences_panel_offers_the_schedule_in_the_stats_section():
    source = Path("static_src/js/yomux/83_preferences_panel.js").read_text(encoding="utf-8")
    assert "preferenceSettingItem('stats.prune_at_local_time'" in source
    assert "function statsPruneLocalTimeChoices()" in source
    # The client renders the server's list; it never spells its own copy.
    assert "clientSettingsPayload?.choices?.['stats.prune_at_local_time']" in source
    assert "02:30" not in source


# --------------------------------------------------------------------------
# The schedule: once a night, local, catching up a missed window
# --------------------------------------------------------------------------












# --------------------------------------------------------------------------
# The daemon runs bounded cutoff sweeps off the request path
# --------------------------------------------------------------------------

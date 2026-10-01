# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retention window, its ordering invariant, and the nightly prune schedule."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from yomux_lib import settings as settings_module
from yomux_lib.stats_current import prune_schedule
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import service as service_module
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


def build_service(tmp_path: Path, *, clock, monotonic, prune_time_reader) -> service_module.StatsCurrentService:
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / DATABASE_FILENAME,
        clock=clock,
        monotonic=monotonic,
        prune_time_reader=prune_time_reader,
    )
    service._pending_full = False
    return service


# --------------------------------------------------------------------------
# Retention vs the display window
# --------------------------------------------------------------------------


def test_retention_covers_the_largest_display_window():
    # THE invariant. Retention and the display window are separate knobs, and
    # this ordering is the only thing tying them together: if retention ever
    # drops below the longest range the GUI can request, a 24h chart renders a
    # window whose older half was already deleted as though it were complete.
    assert storage.RETENTION_SECONDS >= stats_resolution.MAX_RANGE_SECONDS
    assert storage.retention_covers_display_window() is True
    # The two values are deliberately NOT equal any more, so an equality-shaped
    # assumption cannot creep back in.
    assert storage.RETENTION_SECONDS == 2 * 24 * 60 * 60
    assert stats_resolution.MAX_RANGE_SECONDS == 24 * 60 * 60
    # The display knob is the top of the ladder, not a second copy beside it.
    assert max(stats_resolution.RANGE_SECONDS) == stats_resolution.MAX_RANGE_SECONDS
    assert stats_resolution.RANGE_SECONDS[-1] == stats_resolution.MAX_RANGE_SECONDS


def test_prune_refuses_when_retention_drops_below_the_display_window(tmp_path, monkeypatch):
    path = tmp_path / DATABASE_FILENAME
    now = RETENTION_SECONDS + 10_000.0
    with Store.open(path) as store:
        store.append_batch(observations=(
            Observation("kept", "cpu", "host", now - 60.0, "epoch-1", 1, {"value": 1.0}),
        ))
        # Pruning is the destructive half of retention, so a violated invariant
        # keeps the rows and fails closed rather than deleting what the GUI can
        # still ask for.
        monkeypatch.setattr(storage, "RETENTION_SECONDS", stats_resolution.MAX_RANGE_SECONDS - 1)
        with pytest.raises(StatsCurrentError) as error:
            store.prune(now=now)
        assert "is shorter than the largest display window" in str(error.value)
        monkeypatch.undo()
        assert store.prune(now=now).observations_deleted == 0
        assert len(store.read_snapshot().observations) == 1


def test_prune_records_its_time_so_a_restart_does_not_repeat_the_night(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    now = RETENTION_SECONDS + 10_000.0
    with Store.open(path) as store:
        assert store.last_pruned_at() == 0.0
        store.prune(now=now)
        assert store.last_pruned_at() == now
    # A brand-new daemon on the same database reads back the same answer.
    with Store.open(path) as reopened:
        assert reopened.last_pruned_at() == now
    # Maintenance metadata is not facts: an unreadable sidecar reads as "unknown",
    # which is due, never as "already pruned".
    (path.parent / storage.PRUNE_STATE_FILENAME).write_text("{not json", encoding="utf-8")
    with Store.open(path) as damaged:
        assert damaged.last_pruned_at() == 0.0


# --------------------------------------------------------------------------
# The preference value
# --------------------------------------------------------------------------


def test_default_prune_time_is_half_past_two_and_is_offered():
    assert prune_schedule.DEFAULT_PRUNE_LOCAL_TIME == "02:30"
    assert prune_schedule.DEFAULT_PRUNE_LOCAL_TIME in prune_schedule.PRUNE_LOCAL_TIME_CHOICES
    assert len(prune_schedule.PRUNE_LOCAL_TIME_CHOICES) == 48
    assert prune_schedule.PRUNE_LOCAL_TIME_CHOICES[0] == "00:00"
    assert prune_schedule.PRUNE_LOCAL_TIME_CHOICES[-1] == "23:30"
    assert list(prune_schedule.PRUNE_LOCAL_TIME_CHOICES) == sorted(prune_schedule.PRUNE_LOCAL_TIME_CHOICES)


@pytest.mark.parametrize("value", ["02:30", "00:00", "23:59", "2:30"])
def test_valid_prune_times_are_used_as_written(value):
    resolved = prune_schedule.resolve_local_time(value)
    assert resolved.fell_back is False
    assert (resolved.hour, resolved.minute) == prune_schedule.parse_local_time(value)


@pytest.mark.parametrize("value", ["", "banana", "24:00", "02:60", "-1:00", "0230", "2.30", None, 230, "02:3"])
def test_unusable_prune_times_fall_back_and_never_disable_cleanup(value):
    resolved = prune_schedule.resolve_local_time(value)
    # The failure mode this forbids: a bad preference quietly meaning "never
    # clean up", which has no symptom until the disk is full.
    assert resolved.fell_back is True
    assert resolved.text == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
    assert (resolved.hour, resolved.minute) == (2, 30)


def test_settings_expose_the_schedule_as_an_editable_preference():
    catalog = settings_module.settings_catalog(settings_module.default_settings())
    entry = catalog["stats.prune_at_local_time"]
    assert entry["default"] == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
    assert entry["current"] == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
    assert entry["gui"]["visible"] is True
    assert entry["gui"]["section"] == "Performance"
    assert entry["choices"] == list(prune_schedule.PRUNE_LOCAL_TIME_CHOICES)
    assert entry["write_role"] == "admin"


def test_settings_file_round_trip_keeps_a_valid_time_and_replaces_an_invalid_one(tmp_path):
    path = tmp_path / "settings.yaml"
    settings_module.write_settings_file(
        settings_module.merge_settings(
            settings_module.default_settings(), {"stats": {"prune_at_local_time": "03:00"}}
        ),
        path,
    )
    assert settings_module.stats_prune_local_time(path) == "03:00"

    path.write_text("stats:\n  prune_at_local_time: banana\n", encoding="utf-8")
    assert settings_module.stats_prune_local_time(path) == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
    # A missing file is the first-run case, not a reason to stop cleaning up.
    assert settings_module.stats_prune_local_time(tmp_path / "absent.yaml") == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME


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


def test_a_plain_night_prunes_exactly_once_at_the_configured_local_time():
    with local_zone(PACIFIC):
        prune_time = prune_schedule.resolve_local_time("02:30")
        start = local_epoch(2026, 6, 1, 12, 0)
        end = local_epoch(2026, 6, 4, 12, 0)
        assert fire_days(start, end, prune_time) == ["2026-06-02", "2026-06-03", "2026-06-04"]
        # And it fires AT the configured time, not merely once a day.
        due = prune_schedule.most_recent_occurrence(local_epoch(2026, 6, 2, 12, 0), prune_time)
        assert local_text(due) == "2026-06-02 02:30 PDT"
        assert local_text(prune_schedule.next_occurrence(local_epoch(2026, 6, 2, 12, 0), prune_time)) == "2026-06-03 02:30 PDT"


def test_a_missed_window_is_caught_up_rather_than_skipped():
    with local_zone(PACIFIC):
        prune_time = prune_schedule.resolve_local_time("02:30")
        # The machine was asleep all night and came back at 09:00. The night's
        # prune is still owed; a "fire exactly at 02:30" rule would skip it
        # forever on a laptop that is always closed at 02:30.
        last_pruned_at = local_epoch(2026, 6, 1, 2, 30)
        wake = local_epoch(2026, 6, 3, 9, 0)
        assert prune_schedule.is_due(wake, last_pruned_at, prune_time) is True
        # Once it runs, the rest of the day is quiet again.
        assert prune_schedule.is_due(wake + 60.0, wake, prune_time) is False
        assert prune_schedule.is_due(local_epoch(2026, 6, 3, 23, 59), wake, prune_time) is False
        assert prune_schedule.is_due(local_epoch(2026, 6, 4, 2, 31), wake, prune_time) is True
        # A store that never pruned is due immediately, not in 24 hours.
        assert prune_schedule.is_due(wake, 0.0, prune_time) is True


def test_spring_forward_prunes_once_even_though_the_configured_time_does_not_exist():
    with local_zone(PACIFIC):
        prune_time = prune_schedule.resolve_local_time("02:30")
        year, month, day = SPRING_FORWARD
        # 02:30 does not exist on this date; the clock jumps 02:00 -> 03:00.
        start = local_epoch(year, month, day - 1, 12, 0)
        end = local_epoch(year, month, day + 2, 12, 0)
        assert fire_days(start, end, prune_time) == ["2026-03-08", "2026-03-09", "2026-03-10"]
        due = prune_schedule.most_recent_occurrence(local_epoch(year, month, day, 12, 0), prune_time)
        # Not zero times: the nonexistent wall time resolves to the instant the
        # clock actually reaches, and the offset is still resolved per date.
        assert local_text(due) == "2026-03-08 03:30 PDT"
        assert local_text(
            prune_schedule.most_recent_occurrence(local_epoch(year, month, day + 1, 12, 0), prune_time)
        ) == "2026-03-09 02:30 PDT"


def test_fall_back_prunes_once_even_though_the_ambiguous_hour_happens_twice():
    with local_zone(PACIFIC):
        year, month, day = FALL_BACK
        start = local_epoch(year, month, day - 1, 12, 0)
        end = local_epoch(year, month, day + 2, 12, 0)
        # 02:30 is unambiguous on this date and stays 02:30 local, not 01:30.
        prune_time = prune_schedule.resolve_local_time("02:30")
        assert fire_days(start, end, prune_time) == ["2026-11-01", "2026-11-02", "2026-11-03"]
        assert local_text(
            prune_schedule.most_recent_occurrence(local_epoch(year, month, day, 12, 0), prune_time)
        ) == "2026-11-01 02:30 PST"
        # 01:30 happens twice on this date. Exactly one prune, not two.
        ambiguous = prune_schedule.resolve_local_time("01:30")
        assert fire_days(start, end, ambiguous) == ["2026-11-01", "2026-11-02", "2026-11-03"]
        first_pass = time.mktime((year, month, day, 1, 30, 0, 0, 0, 1))
        second_pass = time.mktime((year, month, day, 1, 30, 0, 0, 0, 0))
        assert second_pass - first_pass == 3600.0
        assert prune_schedule.is_due(second_pass, first_pass, ambiguous) is False


def test_a_fixed_offset_captured_once_would_drift_but_the_local_time_does_not():
    with local_zone(PACIFIC):
        prune_time = prune_schedule.resolve_local_time("02:30")
        winter = prune_schedule.most_recent_occurrence(local_epoch(2026, 1, 15, 12, 0), prune_time)
        summer = prune_schedule.most_recent_occurrence(local_epoch(2026, 7, 15, 12, 0), prune_time)
        assert local_text(winter) == "2026-01-15 02:30 PST"
        assert local_text(summer) == "2026-07-15 02:30 PDT"
        # Same wall clock on both sides of the shift, one hour apart in UTC --
        # which is exactly what a startup-computed offset would get wrong.
        assert (winter % 86400) - (summer % 86400) == 3600.0


# --------------------------------------------------------------------------
# The daemon runs bounded cutoff sweeps off the request path
# --------------------------------------------------------------------------


def test_materializer_worker_owns_cutoff_deadline_when_listener_never_idles(tmp_path):
    with local_zone(PACIFIC):
        wall = [local_epoch(2026, 6, 1, 12, 0)]
        monotonic = [0.0]
        listener_store = FakePruneStore()
        worker_store = FakePruneStore()
        service = build_service(
            tmp_path,
            clock=lambda: wall[0],
            monotonic=lambda: monotonic[0],
            prune_time_reader=lambda: "02:30",
        )
        service.writer = listener_store
        service._last_pruned_at = wall[0] - service_module.PRUNE_CHECK_SECONDS
        service._next_prune_check_at = monotonic[0]

        # A continuously readable listener socket may never reach its idle hook.
        # The materializer wait must therefore wake for retention itself, then
        # prune through the worker thread's SQLite connection.
        assert service._ring_wait_timeout() == 0.0
        assert service._prune_if_due(worker_store) is True
        assert worker_store.prunes == [wall[0]]
        assert listener_store.prunes == []
        assert service._ring_wait_timeout() == service_module.PRUNE_CHECK_SECONDS


def test_cutoff_scheduler_rechecks_fractional_wall_clock_shortfall(tmp_path):
    wall = [1_700_000_059.999]
    monotonic = [60.0]
    store = FakePruneStore()
    service = build_service(
        tmp_path,
        clock=lambda: wall[0],
        monotonic=lambda: monotonic[0],
        prune_time_reader=lambda: "02:30",
    )
    service.writer = store
    service._last_pruned_at = 1_700_000_000.0
    service._next_prune_check_at = monotonic[0]

    assert service._prune_if_due() is False
    assert service._next_prune_check_at == pytest.approx(monotonic[0] + 0.001)

    wall[0] += 0.001
    monotonic[0] += 0.001
    assert service._prune_if_due() is True
    assert store.prunes == [wall[0]]


def test_materializer_worker_enforces_vacuum_cap_when_listener_never_idles(tmp_path):
    wall = [1_700_000_000.0]
    monotonic = [service_module.VACUUM_MAX_DEFER_SECONDS]
    listener_store = FakePruneStore()
    worker_store = FakePruneStore()
    service = build_service(
        tmp_path,
        clock=lambda: wall[0],
        monotonic=lambda: monotonic[0],
        prune_time_reader=lambda: "02:30",
    )
    service.writer = listener_store
    service.last_client_at = monotonic[0]
    service._next_prune_check_at = monotonic[0] + 10_000.0
    service._next_vacuum_at = 0.0
    service._vacuum_due_since = 0.0

    assert service._ring_wait_timeout() == 0.0
    assert service._vacuum_if_due_while_idle(worker_store) is True
    assert worker_store.vacuums == [wall[0]]
    assert listener_store.vacuums == []


def test_service_bounds_cutoff_sweeps_and_catches_up_after_a_missed_window(tmp_path):
    with local_zone(PACIFIC):
        wall = [local_epoch(2026, 6, 1, 12, 0)]
        monotonic = [0.0]
        store = FakePruneStore()
        service = build_service(
            tmp_path,
            clock=lambda: wall[0],
            monotonic=lambda: monotonic[0],
            prune_time_reader=lambda: "02:30",
        )
        service.writer = store
        service._last_pruned_at = wall[0] - 30.0

        # The cutoff is still fresh, so the next wake owns the remaining
        # wall-clock interval instead of adding a fresh full minute.
        assert service._prune_if_due() is False
        assert service._next_prune_check_at == 30.0
        assert store.prunes == []

        monotonic[0] += 29.0
        wall[0] += 29.0
        assert service._prune_if_due() is False
        assert store.prunes == []

        # At the one-minute boundary the cutoff sweep runs, independent of the daily preference.
        monotonic[0] += 1.0
        wall[0] += 1.0
        assert service._prune_if_due() is True
        assert store.prunes == [wall[0]]

        monotonic[0] += service_module.PRUNE_CHECK_SECONDS
        wall[0] += service_module.PRUNE_CHECK_SECONDS
        assert service._prune_if_due() is True
        assert len(store.prunes) == 2
        assert service._prunes == 2

        # The machine is then off for two days and comes back at 09:00. Cleanup
        # catches up at once instead of waiting for the next configured occurrence.
        wall[0] = local_epoch(2026, 6, 4, 9, 0)
        monotonic[0] += service_module.PRUNE_CHECK_SECONDS
        assert service._prune_if_due() is True
        assert len(store.prunes) == 3
        status = service._status()["retention_prune"]
        assert status["at_local_time"] == "02:30"
        assert status["preference_fell_back"] is False
        assert status["overdue"] is False
        assert status["count"] == 3
        assert local_text(status["next_at"]) == "2026-06-05 02:30 PDT"


def test_service_never_prunes_on_the_request_path(tmp_path):
    with local_zone(PACIFIC):
        store = FakePruneStore()
        monotonic = [0.0]
        service = build_service(
            tmp_path,
            clock=lambda: local_epoch(2026, 6, 2, 12, 0),
            monotonic=lambda: monotonic[0],
            prune_time_reader=lambda: "02:30",
        )
        service.writer = store
        service._last_pruned_at = 0.0

        # A due prune plus a hundred client requests: none of them pays for the
        # delete, and none of them contends for the writer lock the observer's
        # two-second sample needs.
        for _ in range(100):
            service._on_client()
        assert store.prunes == []
        # The listener's idle hook is the one and only trigger.
        assert service._prune_if_due() is True
        assert len(store.prunes) == 1


def test_service_falls_back_to_the_default_time_when_the_preference_is_unusable(tmp_path):
    with local_zone(PACIFIC):
        store = FakePruneStore()
        monotonic = [0.0]
        wall = [local_epoch(2026, 6, 2, 1, 0)]
        service = build_service(
            tmp_path,
            clock=lambda: wall[0],
            monotonic=lambda: monotonic[0],
            prune_time_reader=lambda: "banana",
        )
        service.writer = store
        service._last_pruned_at = wall[0] - 30.0

        # The cutoff is fresh, so an honest fallback does not force an early sweep.
        assert service._prune_if_due() is False
        status = service._status()["retention_prune"]
        assert status["configured_local_time"] == "banana"
        assert status["at_local_time"] == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
        assert status["preference_fell_back"] is True

        # ... and cleanup still happens that night. An unusable preference must
        # never be a way to turn pruning off.
        wall[0] = local_epoch(2026, 6, 2, 2, 31)
        monotonic[0] += service_module.PRUNE_CHECK_SECONDS
        assert service._prune_if_due() is True
        assert store.prunes == [wall[0]]


def test_service_keeps_cleaning_up_when_the_preference_cannot_be_read(tmp_path):
    with local_zone(PACIFIC):
        def unreadable() -> str:
            raise PermissionError("settings.yaml is not readable")

        store = FakePruneStore()
        service = build_service(
            tmp_path,
            clock=lambda: local_epoch(2026, 6, 2, 12, 0),
            monotonic=lambda: 0.0,
            prune_time_reader=unreadable,
        )
        service.writer = store
        service._last_pruned_at = local_epoch(2026, 6, 1, 2, 30)

        assert service._prune_if_due() is True
        status = service._status()["retention_prune"]
        assert status["preference_error"] == "PermissionError"
        assert status["at_local_time"] == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME

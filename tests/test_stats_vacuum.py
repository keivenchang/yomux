# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Periodic VACUUM reclaims freed pages and persists its cadence across restart.

Retention/compaction delete rows but SQLite (auto_vacuum off) keeps the freed
pages, so the file only grows. statsd must VACUUM ~hourly and remember when it
last did so in schema_meta, so the cadence survives statsd exiting-when-idle and
respawning.
"""

from yolomux_lib import statsd
from yolomux_lib.local_services.stats_store import StatsStore


def _bloat_then_delete(store: StatsStore, rows: int = 4000) -> None:
    # Insert many buckets, then delete them: leaves a large freelist that VACUUM
    # must reclaim (mirrors retention/compaction on the live DB).
    connection = store._connection()
    payload = "x" * 2048
    with connection:
        for i in range(rows):
            connection.execute(
                "INSERT INTO stats_buckets(start,duration,sequence,server_sequence,bucket_json) VALUES(?,?,?,?,?)",
                (i, 1, i, i, payload),
            )
    with connection:
        connection.execute("DELETE FROM stats_buckets")


def test_store_vacuum_reclaims_freelist(tmp_path):
    store = StatsStore(tmp_path / "s.sqlite3")
    _bloat_then_delete(store)
    before = (tmp_path / "s.sqlite3").stat().st_size
    result = store.vacuum()
    after = (tmp_path / "s.sqlite3").stat().st_size
    assert result["bytes_before"] == before
    assert result["bytes_after"] == after
    assert after < before, "VACUUM did not shrink the file"
    # WAL mode is preserved so the read-only web peer keeps working.
    assert str(store._connection().execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"


def test_maybe_vacuum_runs_when_due_and_persists_across_restart(tmp_path):
    db = tmp_path / "s.sqlite3"
    service = statsd.PersistentStatsService(tmp_path / "s.sock", db)
    _bloat_then_delete(service.store)

    # Not yet due: last_vacuum_at seeds to "now", so an immediate check is a no-op.
    now = 1_000_000.0
    service._last_vacuum_at = now
    service._vacuum_interval_seconds = 3600.0
    assert service._maybe_vacuum(now + 60) is False

    # Past the jittered interval -> vacuum, persist the wall-clock marker.
    fired = service._maybe_vacuum(now + 4000)
    assert fired is True
    assert service.last_vacuum_result["bytes_after"] < service.last_vacuum_result["bytes_before"]
    persisted = float(service.store.metadata_value(statsd.STATSD_VACUUM_MARKER))
    assert persisted == now + 4000

    # A restart (new service on the same DB) reads the persisted timestamp lazily
    # on its first maintenance check, so it is NOT immediately due again.
    restarted = statsd.PersistentStatsService(tmp_path / "s2.sock", db)
    assert restarted._last_vacuum_at is None  # not loaded until first check
    assert restarted._maybe_vacuum(now + 4000 + 60) is False
    assert restarted._last_vacuum_at == now + 4000  # loaded the persisted marker


def test_maybe_vacuum_skips_during_active_compaction(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "s.sock", tmp_path / "s.sqlite3")
    service._last_vacuum_at = 0.0  # long overdue
    service._vacuum_interval_seconds = 1.0
    service.retention_compaction = {"now": 1.0, "next_now": 0.0, "phase": "buckets"}
    assert service._maybe_vacuum(1_000_000.0) is False


def test_vacuum_interval_is_jittered_around_an_hour(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "s.sock", tmp_path / "s.sqlite3")
    intervals = [service._roll_vacuum_interval() for _ in range(50)]
    assert all(45 * 60 <= value <= 75 * 60 for value in intervals)
    assert len(set(intervals)) > 1, "interval is not jittered"

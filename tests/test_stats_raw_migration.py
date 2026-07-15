# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""DOIT.1 item 2b: the atomic populate of raw observations from graduated buckets.

Against throwaway DBs only — the live DB is never migrated here. Verifies the
recovery classifier (finer-or-equal-to-native = real sample; coarser = coarse
coverage floor), cost-atom identity recovery, idempotence, the reconciliation
gate, and that it is populate-only (stats_buckets untouched).
"""

import json

from yolomux_lib import statsd


def _service(tmp_path):
    return statsd.PersistentStatsService(tmp_path / "s.sock", tmp_path / "s.sqlite3")


def _put_bucket(store, start, duration, bucket):
    b = {"start": start, "duration": duration, "sequence": start, "server_sequence": start, **bucket}
    store.upsert_bucket(b)


def test_recovery_classifies_native_vs_coarse(tmp_path):
    svc = _service(tmp_path)
    # cpu native cadence = 1: duration-1 bucket => real sample; duration-60 => coarse.
    _put_bucket(svc.store, 100, 1, {"cpu_total_percent": 20.0, "cpu_count": 1.0})
    _put_bucket(svc.store, 200, 60, {"cpu_total_percent": 1200.0, "cpu_count": 60.0})
    # system_memory native = 60: duration-60 bucket => real sample.
    _put_bucket(svc.store, 300, 60, {"host_metrics": {"system_memory_used_total_bytes": 8.0, "system_memory_count": 1.0}})

    result = svc.migrate_to_raw_observations_once()
    assert result["ok"] and result["migrated"]

    conn = svc.store._connection()
    cpu_samples = conn.execute("SELECT sample_time, payload_json FROM stats_raw_samples WHERE family='cpu'").fetchall()
    assert len(cpu_samples) == 1 and cpu_samples[0][0] == 100.0  # only the duration-1 cpu bucket
    assert json.loads(cpu_samples[0][1])["cpu_total_percent"] == 20.0
    mem_samples = conn.execute("SELECT COUNT(*) FROM stats_raw_samples WHERE family='system_memory'").fetchone()[0]
    assert mem_samples == 1  # duration-60 == native
    # The coarse (duration-60) cpu bucket becomes a coarse-only coverage floor, not a sample.
    coarse = conn.execute("SELECT family, start, end, cadence FROM stats_coverage_intervals WHERE source='migrated-coarse'").fetchall()
    assert ("cpu", 200, 260, 60) in [(r[0], r[1], r[2], r[3]) for r in coarse]


def test_cost_atoms_recovered_by_identity(tmp_path):
    svc = _service(tmp_path)
    component = {"event_id": "e1", "direction": "input", "modality": "text", "cache_role": "none",
                 "unit": "tokens", "timestamp": 150.0, "quantity": 42, "micro_usd": 7}
    # Same atom appears in two buckets (different durations) — must dedupe to one row.
    _put_bucket(svc.store, 100, 10, {"cost_summary": {"components": [component], "priced_components": 1}})
    _put_bucket(svc.store, 500, 60, {"cost_summary": {"components": [dict(component)], "priced_components": 1}})
    result = svc.migrate_to_raw_observations_once()
    assert result["ok"] and result["usage_atoms"] == 1
    rows = svc.store._connection().execute("SELECT event_id, sample_time FROM stats_usage_atoms").fetchall()
    assert rows == [("e1", 150.0)]


def test_migration_is_idempotent(tmp_path):
    svc = _service(tmp_path)
    _put_bucket(svc.store, 100, 1, {"cpu_total_percent": 5.0, "cpu_count": 1.0})
    first = svc.migrate_to_raw_observations_once()
    assert first["migrated"] is True
    count_after_first = svc.store._connection().execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0]
    # A second run is a marker-gated no-op; row count unchanged.
    second = svc.migrate_to_raw_observations_once()
    assert second["migrated"] is False and second["reason"] == "already_migrated"
    assert svc.store._connection().execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0] == count_after_first
    # A restart (new service, same DB) also no-ops.
    restarted = statsd.PersistentStatsService(tmp_path / "s2.sock", tmp_path / "s.sqlite3")
    assert restarted.migrate_to_raw_observations_once()["migrated"] is False


def test_migration_is_populate_only_keeps_buckets(tmp_path):
    svc = _service(tmp_path)
    _put_bucket(svc.store, 100, 1, {"cpu_total_percent": 5.0, "cpu_count": 1.0})
    before = svc.store._connection().execute("SELECT COUNT(*) FROM stats_buckets").fetchone()[0]
    svc.migrate_to_raw_observations_once()
    after = svc.store._connection().execute("SELECT COUNT(*) FROM stats_buckets").fetchone()[0]
    assert after == before == 1  # stats_buckets untouched — current serving unaffected


def test_reconcile_abort_leaves_db_untouched(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    _put_bucket(svc.store, 100, 1, {"cpu_total_percent": 5.0, "cpu_count": 1.0})
    # Force a reconciliation failure: pretend more family-bucket hits than accounted.
    real = svc._recover_raw_observations

    def broken():
        rec = real()
        rec["family_bucket_hits"] += 1  # accounted != hits -> abort
        return rec

    monkeypatch.setattr(svc, "_recover_raw_observations", broken)
    result = svc.migrate_to_raw_observations_once()
    assert result["ok"] is False and result["reason"] == "reconcile_incomplete"
    # No rows written, marker not set -> next (fixed) run can still proceed.
    assert svc.store._connection().execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0] == 0
    assert svc.store.metadata_value(statsd.STATSD_RAW_MIGRATION_MARKER) is None

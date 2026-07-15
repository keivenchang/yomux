# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""DOIT.1 item 3 core: fold raw observations into exact-resolution buckets.

The materializer must produce buckets whose duration is EXACTLY the requested
resolution, epoch-aligned, with correct sum-first-divide-once family arithmetic,
and no fabricated buckets for empty windows. Verified against hand-calculated
fixtures (the divide happens at read time, so we assert the summed extensive
fields the display then divides).
"""

from yolomux_lib import statsd


def _service(tmp_path):
    return statsd.PersistentStatsService(tmp_path / "s.sock", tmp_path / "s.sqlite3")


def test_materialize_is_epoch_aligned_and_exact_duration(tmp_path):
    svc = _service(tmp_path)
    # Three 1s cpu samples landing in one 10s window (100..110) and one in the next.
    samples = [
        {"family": "cpu", "sample_time": 101.0, "payload": {"cpu_total_percent": 20.0, "cpu_count": 1.0}},
        {"family": "cpu", "sample_time": 104.0, "payload": {"cpu_total_percent": 40.0, "cpu_count": 1.0}},
        {"family": "cpu", "sample_time": 108.0, "payload": {"cpu_total_percent": 60.0, "cpu_count": 1.0}},
        {"family": "cpu", "sample_time": 112.0, "payload": {"cpu_total_percent": 90.0, "cpu_count": 1.0}},
    ]
    buckets = svc.materialize_buckets(10, samples)
    assert [b["start"] for b in buckets] == [100, 110]  # epoch-aligned to 10s
    assert all(b["duration"] == 10 for b in buckets)
    # sum-first: the 100s bucket carries the summed percent + count; the display
    # divides 120/3 = 40% avg. The materializer must NOT pre-divide.
    assert buckets[0]["cpu_total_percent"] == 120.0 and buckets[0]["cpu_count"] == 3.0
    assert buckets[1]["cpu_total_percent"] == 90.0 and buckets[1]["cpu_count"] == 1.0


def test_empty_window_yields_no_bucket_not_a_zero_row(tmp_path):
    svc = _service(tmp_path)
    samples = [
        {"family": "cpu", "sample_time": 100.0, "payload": {"cpu_total_percent": 10.0, "cpu_count": 1.0}},
        {"family": "cpu", "sample_time": 340.0, "payload": {"cpu_total_percent": 30.0, "cpu_count": 1.0}},
    ]
    buckets = svc.materialize_buckets(60, samples)
    # Windows 100 and 300 have data; 120/180/240 are genuine gaps -> absent, not zero.
    assert [b["start"] for b in buckets] == [60, 300]


def test_agent_status_stacked_average_sums_first(tmp_path):
    svc = _service(tmp_path)
    samples = [
        {"family": "agent_status", "sample_time": 10.0,
         "payload": {"active_agent_total": 2.0, "idle_agent_total": 1.0, "agent_activity_samples": 1.0}},
        {"family": "agent_status", "sample_time": 15.0,
         "payload": {"active_agent_total": 4.0, "idle_agent_total": 3.0, "agent_activity_samples": 1.0}},
    ]
    buckets = svc.materialize_buckets(60, samples)
    assert len(buckets) == 1
    # Summed totals + summed sample count; display divides (active 6/2=3, idle 4/2=2).
    assert buckets[0]["active_agent_total"] == 6.0
    assert buckets[0]["idle_agent_total"] == 4.0
    assert buckets[0]["agent_activity_samples"] == 2.0


def test_token_rate_folds_via_summed_seconds(tmp_path):
    svc = _service(tmp_path)
    # Two token samples for one agent in a 60s window: tokens and seconds both sum,
    # so tokens/min = (tokens_sum / seconds_sum) * 60 stays exact at any resolution.
    samples = [
        {"family": "agent_tokens", "sample_time": 5.0, "payload": {
            "tokens_per_agent_total": 100.0, "agent_token_samples": 1.0,
            "agent_token_rates": {"a1": {"total": 100.0, "samples": 1.0, "tokens": 100.0, "seconds": 10.0}}}},
        {"family": "agent_tokens", "sample_time": 25.0, "payload": {
            "tokens_per_agent_total": 300.0, "agent_token_samples": 1.0,
            "agent_token_rates": {"a1": {"total": 300.0, "samples": 1.0, "tokens": 300.0, "seconds": 20.0}}}},
    ]
    buckets = svc.materialize_buckets(60, samples)
    assert len(buckets) == 1
    rate = buckets[0]["agent_token_rates"]["a1"]
    assert rate["tokens"] == 400.0 and rate["seconds"] == 30.0  # summed -> 400/30*60 tokens/min


def test_cost_atoms_dedup_by_identity_when_folded(tmp_path):
    svc = _service(tmp_path)
    atom = {"event_id": "e1", "direction": "input", "modality": "text", "cache_role": "none",
            "unit": "tokens", "quantity": 10, "micro_usd": 5}
    other = {**atom, "event_id": "e2", "quantity": 20, "micro_usd": 9}
    samples = [
        {"family": "cost", "sample_time": 5.0, "payload": {"cost_summary": {"components": [atom]}}},
        # Same atom identity again (replayed) -> must NOT double-count.
        {"family": "cost", "sample_time": 15.0, "payload": {"cost_summary": {"components": [dict(atom)]}}},
        {"family": "cost", "sample_time": 25.0, "payload": {"cost_summary": {"components": [other]}}},
    ]
    buckets = svc.materialize_buckets(60, samples)
    assert len(buckets) == 1
    ids = sorted(c.get("event_id") for c in buckets[0]["cost_summary"]["components"])
    assert ids == ["e1", "e2"]  # e1 deduped despite appearing twice


def test_system_memory_host_metrics_fold(tmp_path):
    svc = _service(tmp_path)
    samples = [
        {"family": "system_memory", "sample_time": 5.0, "payload": {"host_metrics": {
            "system_memory_used_total_bytes": 100.0, "system_memory_count": 1.0}}},
        {"family": "system_memory", "sample_time": 35.0, "payload": {"host_metrics": {
            "system_memory_used_total_bytes": 300.0, "system_memory_count": 1.0}}},
    ]
    buckets = svc.materialize_buckets(60, samples)
    assert len(buckets) == 1
    host = buckets[0]["host_metrics"]
    assert host["system_memory_used_total_bytes"] == 400.0 and host["system_memory_count"] == 2.0


def test_reads_raw_samples_and_materializes_end_to_end(tmp_path):
    svc = _service(tmp_path)
    conn = svc.store._connection()
    with conn:
        for t, pct in [(1.0, 10.0), (2.0, 30.0), (65.0, 50.0)]:
            conn.execute(
                "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
                ("cpu", "", t, "e", f'{{"cpu_total_percent": {pct}, "cpu_count": 1.0}}'),
            )
    read = svc._read_raw_samples(0, 120)
    assert len(read) == 3
    buckets = svc.materialize_buckets(60, read)
    assert [b["start"] for b in buckets] == [0, 60]
    assert buckets[0]["cpu_total_percent"] == 40.0 and buckets[0]["cpu_count"] == 2.0  # (10+30)/2 avg=20


def test_materialization_generations_are_immutable_and_atomic(tmp_path):
    svc = _service(tmp_path)
    conn = svc.store._connection()
    with conn:
        for t, pct in [(1.0, 10.0), (2.0, 30.0), (65.0, 50.0)]:
            conn.execute(
                "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
                ("cpu", "", t, "e", f'{{"cpu_total_percent": {pct}, "cpu_count": 1.0}}'),
            )
    assert svc.materialization_generation() == 0
    assert svc.read_materialized_layer(60) == []  # unbuilt -> empty, never a crash

    summary = svc.rebuild_materialization(0, 120, now=1000.0)
    assert summary["generation"] == 1
    # Every preset resolution layer is built from one read.
    assert set(summary["resolutions"]) == {1, 10, 60, 300}
    layer60 = svc.read_materialized_layer(60)
    assert [b["start"] for b in layer60] == [0, 60]
    assert layer60[0]["cpu_total_percent"] == 40.0

    # A reader's mutation cannot corrupt the immutable published generation.
    layer60[0]["cpu_total_percent"] = -999.0
    assert svc.read_materialized_layer(60)[0]["cpu_total_percent"] == 40.0

    # Rebuild bumps the generation and republishes atomically.
    assert svc.rebuild_materialization(0, 120, now=2000.0)["generation"] == 2
    assert svc.materialization_generation() == 2


def test_status_reports_materialization_health(tmp_path):
    svc = _service(tmp_path)
    svc.rebuild_materialization(0, 60, now=5.0)
    status, _binary = svc.handle_with_binary({"action": "status"})
    mat = status["materialization"]
    assert mat["generation"] == 1 and mat["built_at"] == 5.0
    assert set(mat["layers"]) == {1, 10, 60, 300}


def test_materialized_snapshot_serves_exact_key_or_structured_states(tmp_path):
    svc = _service(tmp_path)
    # Not built yet -> pending, never a synchronous DB build.
    pending, _ = svc.handle_with_binary({"action": "materialized_snapshot", "range_seconds": 300, "resolution_seconds": 1})
    assert pending["ok"] and pending["pending"] and pending["retry_after_seconds"] >= 1

    conn = svc.store._connection()
    with conn:
        for t in range(0, 300, 10):
            conn.execute(
                "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
                ("cpu", "", float(t), "e", '{"cpu_total_percent": 10.0, "cpu_count": 1.0}'),
            )
    svc.rebuild_materialization(0, 300, now=300.0)

    ok, _ = svc.handle_with_binary({"action": "materialized_snapshot", "range_seconds": 300, "resolution_seconds": 1, "end": 300})
    assert ok["ok"] and ok["range_seconds"] == 300 and ok["resolution_seconds"] == 1
    # Contract: every returned record has duration == the echoed resolution, no mixing.
    assert ok["records"] and {r["duration"] for r in ok["records"]} == {1}
    assert ok["generation"] == 1

    # AUTO resolves server-side to the concrete value (5m -> 1s).
    auto, _ = svc.handle_with_binary({"action": "materialized_snapshot", "range_seconds": 300, "resolution_seconds": "AUTO", "end": 300})
    assert auto["resolution_seconds"] == 1

    # An unsupported key is rejected with the valid choices, never a coarser substitute.
    bad, _ = svc.handle_with_binary({"action": "materialized_snapshot", "range_seconds": 7200, "resolution_seconds": 10})
    assert bad["ok"] is False and bad["unsupported"] and "choices" in bad


def test_background_rebuild_skips_empty_and_builds_when_raw_present(tmp_path):
    svc = _service(tmp_path)
    # No raw samples -> back off, no generation churn.
    assert svc._maybe_rebuild_materialization(now=10_000.0) is False
    assert svc.materialization_generation() == 0

    conn = svc.store._connection()
    with conn:
        conn.execute(
            "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
            ("cpu", "", 9_999.0, "e", '{"cpu_total_percent": 10.0, "cpu_count": 1.0}'),
        )
    # Cadence not yet elapsed since the back-off stamp -> still skips.
    assert svc._maybe_rebuild_materialization(now=10_100.0) is False
    # Past the cadence with raw data present -> builds.
    assert svc._maybe_rebuild_materialization(now=10_000.0 + statsd.STATSD_MATERIALIZATION_REBUILD_INTERVAL_SECONDS + 1) is True
    assert svc.materialization_generation() == 1

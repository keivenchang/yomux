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

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""DOIT.1 item 9: sweep the whole preset matrix through the materialized serve path.

Builds one 24h synthetic raw series, then drives every preset Range x (AUTO +
explicit) Resolution through the additive materialized_snapshot action and asserts
the non-negotiable contract: echoed key, uniform record.duration == resolution,
<= 600 buckets, AUTO resolved server-side, no coarser/mixed substitute. This is the
matrix guard the exact-resolution rewrite must keep green.
"""

import pytest

from yolomux_lib import statsd
from yolomux_lib import stats_resolution as sr


def _service_with_24h_raw(tmp_path):
    svc = statsd.PersistentStatsService(tmp_path / "s.sock", tmp_path / "s.sqlite3")
    now = 24 * 60 * 60
    conn = svc.store._connection()
    with conn:
        # One cpu sample every 5s across 24h -> fine enough to fill every resolution.
        for t in range(0, now, 5):
            conn.execute(
                "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
                ("cpu", "", float(t), "e", '{"cpu_total_percent": 12.0, "cpu_count": 1.0}'),
            )
    svc.rebuild_materialization(0, now, now=float(now))
    return svc, now


@pytest.mark.parametrize("range_seconds", sr.RANGE_SECONDS)
def test_every_range_auto_and_explicit_serve_exact_contract(tmp_path, range_seconds):
    svc, now = _service_with_24h_raw(tmp_path)
    resolutions = [sr.AUTO, *sr.explicit_resolutions(range_seconds)]
    for resolution in resolutions:
        resp, _ = svc.handle_with_binary({
            "action": "materialized_snapshot",
            "range_seconds": range_seconds,
            "resolution_seconds": resolution,
            "end": now,
        })
        assert resp["ok"], (range_seconds, resolution, resp)
        concrete = sr.auto_resolution(range_seconds) if resolution == sr.AUTO else resolution
        assert resp["resolution_seconds"] == concrete
        assert resp["range_seconds"] == range_seconds
        durations = {r["duration"] for r in resp["records"]}
        assert durations <= {concrete}, f"mixed/coarse durations {durations} for {range_seconds}/{concrete}"
        assert len(resp["records"]) <= sr.MAX_BUCKETS
        # Data exists for this window, so the sweep must actually return points.
        assert resp["records"], (range_seconds, concrete)


def test_unsupported_and_retired_keys_reject_across_the_matrix(tmp_path):
    svc, now = _service_with_24h_raw(tmp_path)
    # Retired dense cells and forbidden universe values must never serve.
    for range_seconds, resolution in [(15 * 60, 1), (2 * 60 * 60, 10), (60 * 60, 120), (24 * 60 * 60, 60)]:
        resp, _ = svc.handle_with_binary({
            "action": "materialized_snapshot", "range_seconds": range_seconds,
            "resolution_seconds": resolution, "end": now,
        })
        assert resp["ok"] is False and resp["unsupported"], (range_seconds, resolution, resp)

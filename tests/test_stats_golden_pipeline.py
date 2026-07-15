"""GOLDEN PIPELINE: the one test that exercises every YO!stats layer boundary together.

Real sampler-shaped payloads (every family at its true cadence, plus a genuine mid-range
outage with coverage epochs around it) are ingested through the REAL statsd service into
the REAL durable store; per-range history responses are encoded through the REAL
`_encoded_history` using the contract-tested request shapes (Phase 0a mirror); and the
node half (tests/golden_pipeline_render.test.js) feeds those responses to the REAL
client fetch machinery (pollJsDebugStatsSample -> apply -> coverage -> readiness) and
asserts the rendered HTML per range — every family drawn, the outage kept honest.

Per-layer suites structurally cannot catch a boundary drop (the 2026-07-14
host-metrics stripping passed the store tests AND the render sweep); this test can.
"""
import json
import shutil
import subprocess
import time

import pytest

from tests.browser_helpers.stats_request_shapes import reader_history_request
from yolomux_lib import statsd
from yolomux_lib.local_services import stats_store

RANGES = (1800, 3600, 4 * 3600, 8 * 3600, 24 * 3600)
OUTAGE_START_AGO = 6 * 3600
OUTAGE_END_AGO = 5 * 3600


def _host_metrics(step: int) -> dict:
    samples = max(1.0, step / 10)
    return {
        "system_memory_used_total_bytes": 48e9 * max(1.0, step / 60),
        "system_memory_capacity_total_bytes": 64e9 * max(1.0, step / 60),
        "system_memory_count": max(1.0, step / 60),
        "service_load": {
            "web:8881": {"label": "web", "cpu_total_percent": 12.0 * samples, "cpu_samples": samples, "rss_total_bytes": 2e8, "rss_samples": samples},
            "statsd": {"label": "statsd", "cpu_total_percent": 0.0, "cpu_samples": samples, "rss_total_bytes": 1e8, "rss_samples": samples},
        },
        "gpu_devices": {"gpu:0": {"label": "GPU 0", "util_total_percent": 5.0 * samples, "memory_used_total_bytes": 2.9e9 * samples, "memory_capacity_total_bytes": 51.5e9 * samples, "samples": samples}},
    }


def _seed_real_pipeline(tmp_path, now: int) -> dict:
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    try:
        sequence = 0
        for span_start, span_end, step in (
            (now - 24 * 3600, now - 8 * 3600, 600),
            (now - 8 * 3600, now - 4 * 3600, 120),
            (now - 4 * 3600, now - 1800, 60),
            (now - 1800, now, 10),
        ):
            for start in range(span_start, span_end, step):
                if now - OUTAGE_START_AGO <= start < now - OUTAGE_END_AGO:
                    continue  # the genuine outage: nothing was recorded
                sequence += 1
                bucket = stats_store.empty_bucket(start, step)
                bucket.update({
                    "sequence": sequence, "server_sequence": sequence,
                    "cpu_total_percent": 20.0 * step, "cpu_count": float(step),
                    "system_cpu_total_percent": 30.0 * step, "system_cpu_count": float(step),
                    "run_agent_total": 1.0, "idle_agent_total": 1.0, "agent_activity_samples": 1.0,
                    "tokens_per_agent_total": 120.0, "agent_token_samples": 1.0,
                })
                bucket["host_metrics"] = _host_metrics(step)
                service.store.upsert_bucket(bucket)

        # Real coverage epochs: recorded before and after the outage, never across it, at
        # each family's TRUE sampler cadence (a span-sized cadence would poison the serve
        # tier choice into uniform-coarsest for every request).
        for family in ("cpu", "agent_status", "agent_tokens", "gpu", "system_memory", "service_load"):
            cadence = {"cpu": 1, "system_memory": 60}.get(family, 10)
            for span_start, span_end, epoch, generation in (
                (now - 24 * 3600, now - OUTAGE_START_AGO, "before-outage", 1),
                (now - OUTAGE_END_AGO, now, "after-outage", 2),
            ):
                for sample_time in (span_start, span_end - cadence):
                    marker = service.handle({
                        "action": "merge_server_records",
                        "protocol_version": statsd.STATSD_PROTOCOL_VERSION,
                        "records": [{
                            "time": sample_time,
                            "_stats_coverage": {
                                "family": family,
                                "cadence_seconds": span_end - span_start if sample_time == span_start else cadence,
                                "epoch_id": f"{epoch}:{family}",
                                "owner_generation": generation,
                            },
                        }],
                        "now": sample_time,
                        "compact": False,
                        "refresh_rollups": False,
                    })
                    assert marker["ok"] is True

        histories = {}
        for range_seconds in RANGES:
            history = service._encoded_history(reader_history_request(range_seconds, now, client_id="golden-pipeline"))
            records = history.get("records", [])
            assert records, f"range {range_seconds}s produced no records"
            histories[str(range_seconds)] = history
        return histories
    finally:
        service.store.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_golden_pipeline_every_layer_boundary_serves_and_renders_every_family(tmp_path):
    now = int(time.time() // 600 * 600)
    histories = _seed_real_pipeline(tmp_path, now)
    payload = {
        "nowSeconds": now,
        "ranges": list(RANGES),
        # The outage (6h..5h ago) is inside every range >= 8h; the 4h range predates it.
        "outageVisibleFromRangeSeconds": 8 * 3600,
        "histories": histories,
    }
    payload_path = tmp_path / "golden-pipeline.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        ["node", "tests/golden_pipeline_render.test.js", str(payload_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"golden pipeline render failed:\nstdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    assert "0 failed" in result.stdout, result.stdout[-2000:]

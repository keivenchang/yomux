"""GOLDEN PIPELINE: the one test that exercises every YO!stats layer boundary together.

Real sampler-shaped payloads (every family at its true cadence, plus a genuine mid-range
outage with coverage epochs around it) are ingested through the REAL statsd service into
the REAL durable store; per-range history responses are encoded through the REAL
`_encoded_history` using the contract-tested request shapes (Phase 0a mirror); and the
node half (tests/golden_pipeline_render.node.js) feeds those responses to the REAL
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


def _usage_atom(event_id: str, tmux_key: str, direction: str, quantity: int, *, timestamp: int, model: str = "gpt-5.6", cache_role: str = "none") -> dict:
    session, window, kind = tmux_key.split("|")
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "source": "rollout",
        "provider": "openai",
        "model": model,
        "direction": direction,
        "modality": "text",
        "cache_role": cache_role,
        "unit": "tokens",
        "quantity": quantity,
        "telemetry_complete": True,
        "tmux_key": tmux_key,
        "tmux_label": f"{session}:{window}",
        "tmux_session": session,
        "tmux_window": window,
        "tmux_window_label": window,
        "agent_kind": kind,
    }


def seed_detail_samples(service, now: int) -> None:
    """Detail-rich token/cost/client/process samples at a near, mid, and far age.

    Shared with tests/test_stats_wire_parity.py. Every encode path — agent token
    rates, model rates, priced + unpriced cost components, browser client
    aggregates, and per-process CPU — gets real data in the ranges the request
    shapes read, so the single history stream must carry token/cost detail at
    EVERY range for the render half to draw it.
    """
    for index, offset in enumerate((900, 2 * 3600 + 30, 10 * 3600 + 90)):
        sample_time = now - offset
        service.merge_server_records([{
            "time": sample_time,
            "tokens_per_agent_total": 35 + index,
            "agent_token_samples": 1,
            "agent_token_rates": [
                {"key": "s|0|codex", "label": "s:0", "total": 30, "samples": 1, "tokens": 30, "seconds": 60,
                 "model_rates": {"gpt-5.6": {"label": "gpt-5.6", "total": 30, "samples": 1, "tokens": 30, "seconds": 60}}},
                {"key": "s|2|claude", "label": "s:2", "total": 5 + index, "samples": 1, "tokens": 5 + index, "seconds": 60},
            ],
            "usage_atoms": [
                _usage_atom(f"parity-{index}-input", "s|0|codex", "input", 100 + index, timestamp=sample_time),
                _usage_atom(f"parity-{index}-cache", "s|0|codex", "input", 20, timestamp=sample_time, cache_role="read"),
                _usage_atom(f"parity-{index}-output", "s|0|codex", "output", 30, timestamp=sample_time),
                _usage_atom(f"parity-{index}-unpriced", "s|2|claude", "output", 9, timestamp=sample_time, model="unpriced-model"),
            ],
            "process": {"id": f"yolomux:{8881 + index}", "label": f"web:{8881 + index}", "pid": 4000 + index, "port": 8881 + index, "started_at": float(now - 86_000), "cpu_percent": 12.5, "cpu_count": 1},
        }], now=sample_time)
        service.merge_records([{
            "time": sample_time,
            "api_count": 3 + index,
            "sse_count": 2,
            "latency_total_ms": 120 + index,
            "latency_count": 3,
            "bandwidth_bytes": 4096,
        }], client_id="golden-client", now=sample_time)


def seed_real_store(service, now: int) -> None:
    """Seed every family at its true cadence, plus the genuine outage, through the REAL service.

    Shared with tests/test_stats_wire_parity.py, which pins the exact wire bytes this
    store encodes for every contract-tested request shape.
    """
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
                })
                assert marker["ok"] is True


def _seed_real_pipeline(tmp_path, now: int) -> dict:
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    # Serve through the production read path: the writer seeds the durable
    # store, and the web's IN-PROCESS StatsHistoryReader encodes from a
    # read-only WAL handle (the stats-reader process is retired).
    reader = statsd.StatsHistoryReader(tmp_path / "stats.sqlite3")
    try:
        seed_real_store(service, now)
        seed_detail_samples(service, now)
        histories = {}
        for range_seconds in RANGES:
            history = reader.history(**reader_history_request(range_seconds, now, client_id="golden-pipeline"))
            assert history.pop("ok") is True
            records = history.get("records", [])
            assert records, f"range {range_seconds}s produced no records"
            # ONE history stream: token and cost detail must ride the records at
            # EVERY range (the retired compact token side-stream no longer exists
            # for current clients), or the node half's token/cost render checks
            # would be drawing from nothing.
            assert any(record.get("agent_token_rates") for record in records), f"range {range_seconds}s carries no inline agent token rates"
            assert any((record.get("cost_summary") or {}).get("components") for record in records), f"range {range_seconds}s carries no inline cost components"
            assert "agent_token_history" not in history, f"range {range_seconds}s reintroduced the legacy token side-stream without token params"
            histories[str(range_seconds)] = history
        # Retired-path negative check: the in-process read must never spawn a
        # reader process or create a reader socket beside the writer's.
        assert not list(tmp_path.glob("**/*reader*.sock")), "the retired stats-reader socket reappeared"
        return histories
    finally:
        reader.close()
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
        ["node", "tests/golden_pipeline_render.node.js", str(payload_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"golden pipeline render failed:\nstdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    assert "0 failed" in result.stdout, result.stdout[-2000:]

"""WIRE PARITY: the family-manifest refactor must not change one wire byte.

tests/fixtures/stats_wire_parity.json pins, for every request shape in
tests/fixtures/stats_request_shapes.json (the Phase 0a contract-tested goldens),
the sha256/length/record-count of the exact JSON wire bytes `_encoded_history`
produced BEFORE the Phase 1 family-manifest refactor — captured with the
ORIGINAL per-flag code path (`include_agent_tokens` field branching,
`merge_agent_details`/`merge_cost_summary` booleans) on a deterministic fixture
store (the golden-pipeline seeder plus detail-rich token/cost/client/process
records). The manifest path must reproduce those bytes exactly.

The fixture store and `nowSeconds` are fully deterministic, so any hash drift
is a REAL wire change. Regenerate ONLY for an intentional wire change, in the
same commit that changes the wire:

    STATS_WIRE_PARITY_REGENERATE=1 python3 -m pytest tests/test_stats_wire_parity.py
"""
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from tests.test_stats_golden_pipeline import seed_real_store
from yolomux_lib import statsd

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stats_wire_parity.json"
SHAPES_PATH = Path(__file__).parent / "fixtures" / "stats_request_shapes.json"


class _FrozenPricingCatalog:
    """Deterministic pricing so catalog-data updates cannot masquerade as wire drift."""

    def resolve_rate(self, **kwargs):
        if kwargs.get("model") == "unpriced-model":
            return None
        return SimpleNamespace(
            usd=Decimal("2.5"),
            scale=1_000_000,
            catalog_revision=7,
            source_url="https://prices.example/model",
            effective_from="2026-01-01T00:00:00Z",
        )

    def estimate_rate_band(self, **_kwargs):
        return SimpleNamespace(
            minimum=SimpleNamespace(usd=Decimal("0.5"), scale=1_000_000, catalog_revision=7, source_url="https://prices.example/min"),
            maximum=SimpleNamespace(usd=Decimal("5.0"), scale=1_000_000, catalog_revision=7, source_url="https://prices.example/max"),
        )


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


def _seed_parity_service(tmp_path, now: int):
    service = statsd.PersistentStatsService(
        tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FrozenPricingCatalog()
    )
    seed_real_store(service, now)
    # Detail-rich samples at a near, mid, and far age so EVERY encode path —
    # agent token rates, model rates, priced + unpriced cost components, browser
    # client aggregates, and per-process CPU — has real data in the ranges the
    # request shapes read, on both sides of the token-stream cutover (>= 4h).
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
    return service


def _wire_bytes(payload) -> bytes:
    # The public wire encoding (`encoded_sample` / `encoded_history_from_buckets`):
    # insertion-ordered keys, compact separators. Key-order drift IS wire drift.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256(payload) -> str:
    return hashlib.sha256(_wire_bytes(payload)).hexdigest()


def _encode_all_cases(service) -> dict:
    shapes = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
    cases = {}
    for case in shapes["cases"]:
        request = case.get("readerRequest")
        if not isinstance(request, dict):
            continue
        payload = service._encoded_history(dict(request))
        records = payload.get("records") or []
        assert records, f"request shape {case['name']!r} produced no records — the parity capture would be vacuous"
        cases[case["name"]] = {
            "sha256": _sha256(payload),
            "bytes": len(_wire_bytes(payload)),
            "records": len(records),
            "records_sha256": _sha256(records),
            "coverage_sha256": _sha256(payload.get("coverage")),
            "has_agent_token_history": "agent_token_history" in payload,
        }
    return cases


def test_encoded_history_wire_bytes_match_the_pre_manifest_capture(tmp_path):
    shapes = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
    now = int(shapes["nowSeconds"])
    service = _seed_parity_service(tmp_path, now)
    try:
        cases = _encode_all_cases(service)

        # Field-group behavior spec (not just hashes): the token-stream groups are
        # slimmed from main records exactly when the compact token stream carries
        # them, and host metrics ride EVERY record regardless.
        inline_rates_seen = False
        for case in json.loads(SHAPES_PATH.read_text(encoding="utf-8"))["cases"]:
            request = case.get("readerRequest")
            if not isinstance(request, dict):
                continue
            payload = service._encoded_history(dict(request))
            token_streamed = bool(request.get("token_resolution_seconds"))
            assert cases[case["name"]]["has_agent_token_history"] is token_streamed
            for record in payload["records"]:
                assert "host_metrics" in record, f"{case['name']}: host metrics stripped from a history record"
                assert ("agent_token_rates" in record) is not token_streamed
                assert ("cost_summary" in record) is not token_streamed
                assert ("tokens_per_agent_total" in record) is not token_streamed
            if token_streamed:
                token_records = payload["agent_token_history"]["records"]
                assert token_records, f"{case['name']}: the compact token stream is empty"
                assert any(record.get("agent_token_rates") for record in token_records)
                assert any(record.get("cost_summary", {}).get("components") for record in token_records)
            else:
                inline_rates_seen = inline_rates_seen or any(record.get("agent_token_rates") for record in payload["records"])
        assert inline_rates_seen, "no non-streamed case carried inline agent token rates — the detail seeding is broken"
    finally:
        service.store.close()

    if os.environ.get("STATS_WIRE_PARITY_REGENERATE") == "1":
        FIXTURE_PATH.write_text(json.dumps({"nowSeconds": now, "cases": cases}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    golden = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert golden["nowSeconds"] == now
    assert set(golden["cases"]) == set(cases), "request-shape goldens and the parity fixture drifted apart — regenerate both intentionally"
    for name, expected in golden["cases"].items():
        assert cases[name] == expected, (
            f"wire bytes drifted for request shape {name!r}: the encoding no longer matches the "
            f"pre-manifest capture (expected {expected}, got {cases[name]})"
        )

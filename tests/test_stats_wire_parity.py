"""WIRE PARITY: the exact YO!stats wire bytes are pinned per request shape.

tests/fixtures/stats_wire_parity.json pins, for every request shape in
tests/fixtures/stats_request_shapes.json (the contract-tested goldens) PLUS the
legacy old-client shapes (token_resolution > 0), the sha256/length/record-count
of the exact JSON wire bytes `_encoded_history` produces on a deterministic
fixture store (the golden-pipeline seeder plus detail-rich
token/cost/client/process records).

Captured 2026-07 for the ONE-history-stream cutover (Phase 2): current clients
send NO token_* params and receive token rates + cost_summary inline on every
history record; the legacy shapes keep receiving the pre-cutover slimmed
records plus the separate `agent_token_history` payload until that compat path
is retired.

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

from tests.browser_helpers.stats_request_shapes import legacy_reader_history_request
from tests.test_stats_golden_pipeline import seed_detail_samples
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


def _seed_parity_service(tmp_path, now: int):
    service = statsd.PersistentStatsService(
        tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FrozenPricingCatalog()
    )
    seed_real_store(service, now)
    # Detail-rich samples (shared with the golden pipeline) so EVERY encode path —
    # agent token rates, model rates, priced + unpriced cost components, browser
    # client aggregates, and per-process CPU — has real data in the ranges the
    # request shapes read.
    seed_detail_samples(service, now)
    return service


def _wire_bytes(payload) -> bytes:
    # The public wire encoding (`encoded_sample` / `encoded_history_from_buckets`):
    # insertion-ordered keys, compact separators. Key-order drift IS wire drift.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256(payload) -> str:
    return hashlib.sha256(_wire_bytes(payload)).hexdigest()


def _parity_requests(now: int) -> list[tuple[str, dict]]:
    """Every pinned request: the client goldens (no token params) plus the
    LEGACY old-client shapes for each range that used the compact token stream
    (retire with the server's legacy path)."""
    shapes = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
    requests = [
        (case["name"], dict(case["readerRequest"]))
        for case in shapes["cases"]
        if isinstance(case.get("readerRequest"), dict)
    ]
    for range_seconds in (4 * 3600, 8 * 3600, 16 * 3600, 24 * 3600):
        requests.append((f"legacy-token-stream-{range_seconds}s", legacy_reader_history_request(range_seconds, now, client_id="golden-client")))
    return requests


def _encode_all_cases(service, now: int) -> dict:
    cases = {}
    for name, request in _parity_requests(now):
        payload = service._encoded_history(dict(request))
        records = payload.get("records") or []
        assert records, f"request shape {name!r} produced no records — the parity capture would be vacuous"
        cases[name] = {
            "sha256": _sha256(payload),
            "bytes": len(_wire_bytes(payload)),
            "records": len(records),
            "records_sha256": _sha256(records),
            "coverage_sha256": _sha256(payload.get("coverage")),
            "has_agent_token_history": "agent_token_history" in payload,
        }
    return cases


def test_encoded_history_wire_bytes_match_the_single_stream_capture(tmp_path):
    shapes = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
    now = int(shapes["nowSeconds"])
    service = _seed_parity_service(tmp_path, now)
    try:
        cases = _encode_all_cases(service, now)

        # Field-group behavior spec (not just hashes): ONE history stream — every
        # record of every current-client shape carries host metrics AND token
        # detail (rates, cost, token scalars); the separate agent_token_history
        # payload appears ONLY for the legacy old-client shapes, which also keep
        # the pre-cutover slimmed main records.
        inline_rates_seen = False
        for name, request in _parity_requests(now):
            payload = service._encoded_history(dict(request))
            legacy_token_streamed = bool(request.get("token_resolution_seconds"))
            assert cases[name]["has_agent_token_history"] is legacy_token_streamed
            for record in payload["records"]:
                assert "host_metrics" in record, f"{name}: host metrics stripped from a history record"
                assert ("agent_token_rates" in record) is not legacy_token_streamed, f"{name}: token rates wire presence"
                assert ("cost_summary" in record) is not legacy_token_streamed, f"{name}: cost summary wire presence"
                assert ("tokens_per_agent_total" in record) is not legacy_token_streamed, f"{name}: token scalars wire presence"
            if legacy_token_streamed:
                token_records = payload["agent_token_history"]["records"]
                assert token_records, f"{name}: the legacy compact token stream is empty"
                assert any(record.get("agent_token_rates") for record in token_records)
                assert any(record.get("cost_summary", {}).get("components") for record in token_records)
            else:
                inline_rates_seen = inline_rates_seen or any(record.get("agent_token_rates") for record in payload["records"])
        assert inline_rates_seen, "no current-client case carried inline agent token rates — the detail seeding is broken"

        # PAYLOAD BUDGET: the single-stream 24h response stays within 1.5x of the
        # old combined history+token payload for the same range (292,920 bytes in
        # the pre-cutover capture of this same deterministic store).
        assert cases["fresh-range-86400s"]["bytes"] <= int(1.5 * 292_920), cases["fresh-range-86400s"]
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
            f"pinned single-stream capture (expected {expected}, got {cases[name]})"
        )

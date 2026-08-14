# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""v0.6.10 YO!stats range-shift gate with realistic fixture volume.

The fixture owns 30 mock transcript-like agent sources across 24 hours: 288
five-minute usage atoms per source (8,640 rows), plus 288 five-minute CPU
observations and one CPU coverage epoch (8,929 stored rows).  That volume
prevents an empty-state response from posing as a range-shift baseline.

Accepted v0.6.10 baseline, 30 samples per transition, milliseconds, measured
from UI selection through the matching real HTTP snapshot's DOM mutation:

| transition | median | p95 | max |
| --- | ---: | ---: | ---: |
| 1s -> 10s | 87.5 | 89.9 | 102.8 |
| 10s -> 60s | 88.3 | 90.1 | 90.9 |
| 60s -> 300s | 102.5 | 107.4 | 109.3 |
| 300s -> 60s | 88.4 | 90.3 | 91.2 |
| 60s -> 10s | 87.7 | 89.1 | 90.4 |
| 10s -> 1s | 88.1 | 90.2 | 90.6 |

Every transition must remain below 350 ms: a 3.2x margin over the 109.3 ms
worst accepted maximum, consistent with the threefold gate margin. The shared
roughly 88 ms floor remains unexplained; source inspection found no matching
fixed 100 ms timer, so it is recorded rather than attributed. The test proves
both guards fire: an injected post-render delay breaches the latency budget and
an injected never-returning snapshot trips the completion timeout.
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen

import pytest

from tests.browser_helpers.browser_layout import (  # noqa: F401
    browser,
    start_browser_share_server,
    stop_browser_share_server,
)
from tests.test_browser_stats_coverage import _start_current_stats, _write_current_stats_fixture_assets
from yolomux_lib import http_routes as http_routes_module
from yolomux_lib import server as server_module
from yolomux_lib import web as web_module
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import migration as stats_migration
from yolomux_lib.stats_current import pricing
from yolomux_lib.stats_current import resolution as stats_resolution
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yolomux_lib.stats_current.usage import usage_atom_from_source
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts


SAMPLE_COUNT = 30
NOW = 1_700_000_000.0
MAX_RANGE_SHIFT_MS = 350.0
# Cold 24h run: snapshot 11.0 ms (496,037 B), the other HTTP routes 0.6-2.5
# ms.  Fifty milliseconds is a 4.5x margin over the slowest measured route.
MAX_24H_ENDPOINT_MS = 50.0
COMPLETION_TIMEOUT_MS = 6_500
INJECTED_COMPLETION_TIMEOUT_MS = 25
SCALE_SWEEP = (
    (900, 10, "1s->10s"),
    (3600, 60, "10s->60s"),
    (86400, 300, "60s->300s"),
    (3600, 60, "300s->60s"),
    (900, 10, "60s->10s"),
    (300, 1, "10s->1s"),
)
EXPECTED_EXPLICIT_RESOLUTIONS = {
    300: (1, 10),
    900: (10, 60),
    1800: (10, 60),
    3600: (60, 300),
    7200: (60, 300),
    14400: (60, 300),
    28800: (60, 300),
    57600: (300,),
    86400: (300,),
}
TRANSCRIPT_CORPUS_SPECS = (
    ("quiet", MockTranscriptSpec("stats-24h-quiet", usage_records=288, span_seconds=86_399, start_timestamp=int(NOW))),
    ("early", MockTranscriptSpec("stats-24h-early", usage_records=96, span_seconds=6 * 3600, start_timestamp=int(NOW))),
    ("mid", MockTranscriptSpec("stats-24h-mid", usage_records=144, span_seconds=4 * 3600, start_timestamp=int(NOW + 8 * 3600), unknown_model=True)),
    ("late", MockTranscriptSpec("stats-24h-late", usage_records=120, span_seconds=6 * 3600, start_timestamp=int(NOW + 16 * 3600))),
)


pytestmark = pytest.mark.browser


def _record_current_database_migration(
    store: storage.Store,
    *,
    observed_at: float,
    observations: int,
    coverage_epochs: int,
    usage_atoms: int,
) -> None:
    assert store.record_migration_reconciliation(storage.MigrationReconciliation(
        stats_migration.MIGRATION_ID,
        observed_at,
        "0" * 64,
        {
            "format": 1,
            "sources": [],
            "counts": {
                "observations": observations,
                "coverage_epochs": coverage_epochs,
                "usage_atoms": usage_atoms,
                "unavailable_spans": 0,
            },
            "issue_counts": {},
            "issues": [],
            "issues_truncated": 0,
            "retirement": {
                "artifacts": 0,
                "bytes": 0,
                "shared_history_rewrites": 0,
            },
        },
    ))


def _seed_realistic_stats(database: Path, *, end: float = NOW) -> int:
    """Create the fixture-owned active database before its sole service starts."""

    usage_atoms = []
    observations = []
    for interval in range(288):
        observed_at = end - (287 - interval) * 300
        observations.append(storage.Observation(
            f"cpu-{interval}", "cpu", "web", observed_at, "cpu-epoch", 1,
            {"process_percent": 7 + interval % 11, "system_percent": 23 + interval % 7},
        ))
        for agent in range(30):
            usage_atoms.append(storage.UsageAtom(
                f"transcript-{agent}-sample-{interval}", "input", "text", "none", "tokens",
                observed_at,
                {
                    "quantity": 25 + agent % 5,
                    "provider": "openai",
                    "model": "gpt-5",
                    "agent_id": f"mock-transcript-agent-{agent:02d}",
                    "telemetry_complete": True,
                },
            ))
    coverage_epochs = (storage.CoverageEpoch(
        "cpu", "web", "cpu-epoch", end - 86400, None, 300, 1,
    ),)
    with storage.Store.open(database) as store:
        appended = store.append_batch(
            observations=observations,
            coverage_epochs=coverage_epochs,
            usage_atoms=usage_atoms,
        )
        assert appended.source_generation == 1
        _record_current_database_migration(
            store,
            observed_at=end,
            observations=len(observations),
            coverage_epochs=len(coverage_epochs),
            usage_atoms=len(usage_atoms),
        )
    return len(observations) + len(coverage_epochs) + len(usage_atoms)


def _distribution(samples: list[float]) -> tuple[float, float, float]:
    ordered = sorted(samples)
    return (
        statistics.median(ordered),
        ordered[math.ceil(len(ordered) * 0.95) - 1],
        ordered[-1],
    )


def _wait_for_ring_publication(
    service: stats_service.StatsCurrentService,
    *,
    timeout: float = 10.0,
) -> bool:
    deadline = time.monotonic() + timeout
    waiter = threading.Event()
    while time.monotonic() < deadline:
        if service._status()["ring_writer"]["publications"] > 0:
            return True
        waiter.wait(0.005)
    return service._status()["ring_writer"]["publications"] > 0


def _assert_range_shift_latency(label: str, maximum_ms: float) -> None:
    assert maximum_ms < MAX_RANGE_SHIFT_MS, (
        f"G4b latency guard: {label} max {maximum_ms:.1f} ms exceeds "
        f"{MAX_RANGE_SHIFT_MS:.1f} ms"
    )


def _series_values(snapshot: dict[str, object], name: str) -> list[int]:
    buckets = snapshot["buckets"]
    assert isinstance(buckets, list)
    return [
        int(bucket["series"][name]["value"])
        for bucket in buckets
        if name in bucket["series"]
    ]


def _bucket_series_values(snapshot: dict[str, object], name: str) -> dict[int, int]:
    """Return an additive series by bucket start, treating absent zeroes as zero."""

    buckets = snapshot["buckets"]
    assert isinstance(buckets, list)
    return {
        int(bucket["start"]): int(bucket["series"].get(name, {"value": 0})["value"])
        for bucket in buckets
    }


def _assert_resolution_aggregation(
    responses: dict[tuple[int, int], dict[str, object]],
    range_seconds: int,
    resolutions: tuple[int, ...],
) -> None:
    """Prove each available usage resolution preserves totals and aligned buckets.

    CPU percentages are averages rather than additive quantities, so summing their
    buckets would be invalid. This fixture prices each token at one micro-USD;
    the cost-report total is therefore the same additive quantity as usage tokens.
    The HTTP API aligns each requested resolution to its own current window and
    has no explicit end-time parameter, so compare only full coarse buckets whose
    fine buckets are in the overlapping API response windows.
    """

    finest_resolution = resolutions[0]
    finest = responses[range_seconds, finest_resolution]
    finest_values = _bucket_series_values(finest, "usage_tokens")
    finest_total = sum(finest_values.values())
    finest_cost = int(finest["cost_report"]["total_micro_usd"])
    for resolution_value in resolutions:
        response = responses[range_seconds, resolution_value]
        values = _bucket_series_values(response, "usage_tokens")
        assert sum(values.values()) == finest_total, (
            range_seconds, finest_resolution, resolution_value, finest_total, sum(values.values()),
        )
        assert int(response["cost_report"]["total_micro_usd"]) == finest_cost, (
            range_seconds, finest_resolution, resolution_value, finest_cost,
            response["cost_report"]["total_micro_usd"],
        )
        compared_bucket_count = 0
        for bucket_start, bucket_value in values.items():
            fine_starts = range(bucket_start, bucket_start + resolution_value, finest_resolution)
            if not all(fine_start in finest_values for fine_start in fine_starts):
                continue
            expected = sum(finest_values[fine_start] for fine_start in fine_starts)
            assert bucket_value == expected, (
                range_seconds, finest_resolution, resolution_value, bucket_start, expected, bucket_value,
            )
            compared_bucket_count += 1
        assert compared_bucket_count, (range_seconds, finest_resolution, resolution_value)


def _generate_24h_transcript_corpora(root: Path) -> dict[str, object]:
    return {
        name: generate_mock_transcripts(root / name, spec)
        for name, spec in TRANSCRIPT_CORPUS_SPECS
    }


def _transcript_usage_timestamps(corpus: object) -> list[int]:
    timestamps = []
    for path in (corpus.claude_path, corpus.codex_path):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("type") == "assistant" and "usage" in record.get("message", {}):
                timestamps.append(int(record["timestamp"]))
            elif "total_token_usage" in record.get("payload", {}).get("info", {}):
                timestamps.append(int(record["timestamp"]))
    return timestamps


def _assert_transcript_corpus_window(name: str, corpus: object, start: int, end: int) -> None:
    timestamps = _transcript_usage_timestamps(corpus)
    assert timestamps, name
    assert min(timestamps) == start, (name, min(timestamps), start)
    assert max(timestamps) == end, (name, max(timestamps), end)


def _scan_transcript_atoms(corpora: dict[str, object]) -> tuple[object, tuple[storage.UsageAtom, ...]]:
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2_000)
    scan = scanner.scan([row for corpus in corpora.values() for row in corpus.scanner_rows])
    scanner.commit(scan.receipt_id)
    return scan, tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key, "agent_kind": item.agent_kind})
        for item in scan.items
    )


def _assert_complete_bucket_window(buckets: list[object], window_start: int, window_end: int, resolution_seconds: int) -> None:
    """Reject a plausible but silently truncated series before it reaches the graph."""

    assert [bucket["start"] for bucket in buckets] == list(range(window_start, window_end, resolution_seconds))


def _assert_24h_endpoint_latency(endpoint: str, elapsed_ms: float) -> None:
    """Keep cold fixture HTTP pulls below a deliberately generous 3x CI margin."""

    assert elapsed_ms < MAX_24H_ENDPOINT_MS, (
        f"24h API latency guard: {endpoint} took {elapsed_ms:.1f} ms; "
        f"limit is {MAX_24H_ENDPOINT_MS:.1f} ms"
    )


def _http_json(base_url: str, path: str, *, payload: object | None = None) -> tuple[int, object, int, float]:
    """Issue the browser's JSON request shape and retain transport measurements."""

    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}", body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            status = response.status
    except HTTPError as error:
        raw = error.read()
        status = error.code
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError:
        decoded = raw.decode("utf-8")
    return status, decoded, len(raw), elapsed_ms


def test_debug_stats_sample_endpoint_is_not_a_live_client_contract():
    """The retired sample route must not survive as a latent browser 404."""
    source = Path("static_src/js/yolomux/85_debug_panel.js").read_text(encoding="utf-8")
    assert "/api/stats-sample" not in source


def test_stats_24h_query_policy_is_limited_to_five_minute_buckets():
    """The 600-bucket ceiling deliberately makes a 24-hour view queryable only at 300 seconds."""

    assert stats_resolution.explicit_resolutions(86_400) == (300,)
    assert stats_resolution.auto_resolution(86_400) == 300
    assert 86_400 // 300 == 288


def test_stats_24h_transcript_window_guard_rejects_a_bunched_corpus(tmp_path):
    corpus = generate_mock_transcripts(
        tmp_path / "bunched",
        MockTranscriptSpec("stats-24h-bunched", usage_records=288, span_seconds=3600, start_timestamp=int(NOW)),
    )
    with pytest.raises(AssertionError, match="1700003600"):
        _assert_transcript_corpus_window("quiet", corpus, int(NOW), int(NOW + 86_399))


def test_stats_24h_transcripts_fill_every_five_minute_bucket_without_direct_seed(tmp_path):
    """The graph's 24-hour token series must come from transcript atoms, not the direct seed."""

    range_seconds = 86_400
    resolution_seconds = 300
    end = NOW + range_seconds
    corpora = _generate_24h_transcript_corpora(tmp_path / "transcripts")
    for name, spec in TRANSCRIPT_CORPUS_SPECS:
        _assert_transcript_corpus_window(name, corpora[name], spec.start_timestamp, spec.start_timestamp + spec.span_seconds)
    scan, transcript_atoms = _scan_transcript_atoms(corpora)
    assert {item.agent_kind for item in scan.items} == {"claude", "codex"}
    database = tmp_path / storage.DATABASE_FILENAME
    socket_path = tmp_path / "services" / "statsd.sock"
    with storage.Store.open(database) as store:
        appended = store.append_batch(usage_atoms=transcript_atoms)
        assert appended.usage_atoms_accepted == len(transcript_atoms)
        _record_current_database_migration(
            store,
            observed_at=end,
            observations=0,
            coverage_epochs=0,
            usage_atoms=len(transcript_atoms),
        )
    service = stats_service.StatsCurrentService(socket_path, database, idle_seconds=60, clock=lambda: end)
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        assert service.cache_ready_event.wait(20), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        metadata, binary = client.snapshot({
            "range_seconds": range_seconds,
            "resolution": resolution_seconds,
            "client_id": "stats-24h-transcript-only",
        })
        assert metadata["resolution_seconds"] == resolution_seconds
        snapshot = json.loads(binary)
        assert len(snapshot["buckets"]) == range_seconds // resolution_seconds
        values = _series_values(snapshot, "usage_tokens")
        assert len(values) == range_seconds // resolution_seconds
        assert all(value > 0 for value in values)
    finally:
        service.stop_event.set()
        service.work_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_stats_24h_combined_observations_and_transcripts_reconcile_at_300_seconds(monkeypatch, tmp_path):
    """Cold, empty-DB day: quiet/burst/idle transcript usage and CPU cover both providers end to end.

    Native Claude ``claude_usage`` and Codex ``codex_meta``/``codex_usage`` files model
    a quiet all-day session plus early, mid-day, and late sessions.  The latter overlap
    the quiet baseline to create bursts; their distinct starts/ends, seeds, and priced/
    unpriced models produce nonuniform token and cost buckets for both providers.  CPU
    coverage and thirty agent sources keep the graph populated end to end.  This is a
    cold build: the database is created below and has no pre-existing cache.
    """

    range_seconds = 86_400
    resolution_seconds = 300
    end = NOW + range_seconds
    state = tmp_path / "stats-24h-correctness"
    state.mkdir()
    socket_path = state / "services" / "statsd.sock"
    database = state / storage.DATABASE_FILENAME
    with storage.Store.open(database):
        pass
    assert database.exists()
    database.unlink()
    assert not database.exists()
    observation_rows = _seed_realistic_stats(database, end=end)
    corpora = _generate_24h_transcript_corpora(tmp_path / "transcripts")
    for name, spec in TRANSCRIPT_CORPUS_SPECS:
        _assert_transcript_corpus_window(name, corpora[name], spec.start_timestamp, spec.start_timestamp + spec.span_seconds)
    scan, transcript_atoms = _scan_transcript_atoms(corpora)
    assert observation_rows == 8_929
    assert sum(corpus.usage_records for corpus in corpora.values()) == 648
    assert {item.agent_kind for item in scan.items} == {"claude", "codex"}
    assert len(transcript_atoms) >= 648
    with storage.Store.open(database) as store:
        appended = store.append_batch(usage_atoms=transcript_atoms)
        assert appended.usage_atoms_accepted == len(transcript_atoms)

    evidence = pricing.PricingEvidence(
        "stats-24h-fixed-rate", "1", 1, "2026-01-01", "fixture",
        "https://example.com/pricing", 1,
    )
    service = stats_service.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=60,
        clock=lambda: end,
        price_resolver=lambda atom: pricing.UsagePriceProjection(
            int(atom.payload["quantity"]), int(atom.payload["quantity"]), evidence,
        ),
    )
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        assert service.cache_ready_event.wait(20), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client, client_binding_secret=b"stats-24h-correctness-client-binding-secret",
            ),
            record_current_browser_observations=lambda _payload, *, authenticated_username: (
                {"ok": True, "accepted": 1, "duplicates": 0, "source_generation": 1}, 200,
            ),
        )
        activity_route_writes = []
        http_routes_module.get_activity_summary(
            SimpleNamespace(
                write_json_bytes=lambda body, *, status: activity_route_writes.append(
                    (status, json.loads(body))
                ),
            ),
            None,
            None,
        )
        assert activity_route_writes == [(HTTPStatus.SERVICE_UNAVAILABLE, {
            "status": "feature_disabled",
            "code": "feature_disabled",
            "reason": "async_replacement_required",
            "retryable": False,
            "terminal": True,
        })]
        http_server, http_thread = start_browser_share_server(monkeypatch, tmp_path, app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
        endpoint_measurements = {}
        status, snapshot, snapshot_bytes, snapshot_ms = _http_json(
            base_url,
            f"/api/stats-snapshot?range_seconds={range_seconds}&resolution={resolution_seconds}&client_id=stats-24h-correctness",
        )
        endpoint_measurements["stats-snapshot"] = (snapshot_ms, snapshot_bytes)
        _assert_24h_endpoint_latency("stats-snapshot", snapshot_ms)
        assert status == 200 and snapshot_bytes > 0 and snapshot_ms > 0
        assert isinstance(snapshot, dict)
        buckets = snapshot["buckets"]
        assert len(buckets) == range_seconds // resolution_seconds
        starts = [bucket["start"] for bucket in buckets]
        assert starts == list(range(snapshot["window_start"], snapshot["window_end"], resolution_seconds))
        with pytest.raises(AssertionError):
            _assert_complete_bucket_window(
                buckets[:-1], snapshot["window_start"], snapshot["window_end"], resolution_seconds,
            )
        assert snapshot["no_data"] == [], snapshot["no_data"]
        cpu_values = _series_values(snapshot, "cpu_percent:web")
        usage_values = _series_values(snapshot, "usage_tokens")
        assert len(cpu_values) == len(usage_values) == len(buckets)
        assert cpu_values[0] and cpu_values[-1] and usage_values[0] and usage_values[-1]
        assert len(set(usage_values)) > 32, usage_values
        window_start = snapshot["window_start"]
        window_end = snapshot["window_end"]
        seeded_observation_tokens = sum(
            sum(25 + agent % 5 for agent in range(30))
            for interval in range(288)
            if window_start <= end - (287 - interval) * 300 < window_end
        )
        seeded_transcript_tokens = sum(
            int(atom.payload["quantity"])
            for atom in transcript_atoms
            if window_start <= atom.observed_at < window_end
        )
        expected_usage_tokens = seeded_observation_tokens + seeded_transcript_tokens
        assert sum(usage_values) == expected_usage_tokens
        assert snapshot["cost_report"]["total_micro_usd"] == expected_usage_tokens

        matrix = {
            range_value: stats_resolution.explicit_resolutions(range_value)
            for range_value in stats_resolution.RANGE_SECONDS
        }
        responses = {}
        for range_value, resolutions in matrix.items():
            for resolution_value in resolutions:
                status, response, response_bytes, response_ms = _http_json(
                    base_url,
                    f"/api/stats-snapshot?range_seconds={range_value}&resolution={resolution_value}&client_id=stats-24h-matrix",
                )
                _assert_24h_endpoint_latency("stats-snapshot", response_ms)
                assert status == 200 and isinstance(response, dict), (range_value, resolution_value, response)
                assert len(response["buckets"]) == range_value // resolution_value
                _assert_complete_bucket_window(
                    response["buckets"], response["window_start"], response["window_end"], resolution_value,
                )
                responses[range_value, resolution_value] = response
        assert set(responses) == {
            (range_value, resolution_value)
            for range_value, resolutions in matrix.items()
            for resolution_value in resolutions
        }
        assert matrix == EXPECTED_EXPLICIT_RESOLUTIONS
        for range_value, resolutions in matrix.items():
            _assert_resolution_aggregation(responses, range_value, resolutions)

        status, capabilities, capabilities_bytes, capabilities_ms = _http_json(base_url, "/api/stats-capabilities")
        endpoint_measurements["stats-capabilities"] = (capabilities_ms, capabilities_bytes)
        _assert_24h_endpoint_latency("stats-capabilities", capabilities_ms)
        assert isinstance(capabilities, dict)
        assert status == 200 and capabilities["ranges"] == json.loads(
            json.dumps(stats_resolution.wire_capabilities()["ranges"])
        )
        status, retry, retry_bytes, retry_ms = _http_json(base_url, "/api/stats-retry", payload={})
        endpoint_measurements["stats-retry"] = (retry_ms, retry_bytes)
        _assert_24h_endpoint_latency("stats-retry", retry_ms)
        assert status == 200
        assert retry["state"] == "ready" and retry["terminal"] is True
        assert retry["request"]["id"].startswith("r-")
        assert retry["data"] == {"ok": True, "status": "ready"}
        assert retry["ok"] is True and retry["status"] == "ready"
        status, observations, observations_bytes, observations_ms = _http_json(base_url, "/api/stats-observations", payload={"fixture": "24h"})
        endpoint_measurements["stats-observations"] = (observations_ms, observations_bytes)
        _assert_24h_endpoint_latency("stats-observations", observations_ms)
        assert status == 200 and observations["accepted"] == 1
        # The production EventSource route is intentionally long-lived; its first SSE
        # frame proves the HTTP stream starts without treating disconnect as success.
        stream_request = Request(
            f"{base_url}/api/stats-stream?range_seconds=86400&resolution_seconds=300&client_id=stats-24h-correctness&after_cache_generation={snapshot['cache_generation']}&after_revision=0",
        )
        stream_started = time.perf_counter()
        with urlopen(stream_request, timeout=10) as stream:
            stream_line = stream.readline()
            assert stream.status == 200 and b"event:" in stream_line
        stream_ms = (time.perf_counter() - stream_started) * 1000
        endpoint_measurements["stats-stream"] = (stream_ms, len(stream_line))
        _assert_24h_endpoint_latency("stats-stream", stream_ms)

        assert set(endpoint_measurements) == {
            "stats-snapshot", "stats-capabilities", "stats-retry", "stats-observations",
            "stats-stream",
        }
        print("24h endpoint ms/bytes", endpoint_measurements)

        # Inject a real server-side delay for every routed endpoint, rather
        # than merely testing the assertion helper.
        injected_delay_seconds = (MAX_24H_ENDPOINT_MS + 10) / 1000
        def delayed(original):
            def invoke(*args, **kwargs):
                threading.Event().wait(injected_delay_seconds)
                return original(*args, **kwargs)
            return invoke

        monkeypatch.setattr(app.stats_current_http, "snapshot", delayed(app.stats_current_http.snapshot))
        monkeypatch.setattr(app.stats_current_http, "capabilities", delayed(app.stats_current_http.capabilities))
        monkeypatch.setattr(app.stats_current_http, "retry", delayed(app.stats_current_http.retry))
        monkeypatch.setattr(app.stats_current_http, "delta_stream", delayed(app.stats_current_http.delta_stream))
        monkeypatch.setattr(app, "record_current_browser_observations", delayed(app.record_current_browser_observations))
        injected_calls = (
            ("stats-snapshot", "/api/stats-snapshot?range_seconds=86400&resolution=300&client_id=stats-24h-correctness", None),
            ("stats-capabilities", "/api/stats-capabilities", None),
            ("stats-retry", "/api/stats-retry", {}),
            ("stats-observations", "/api/stats-observations", {"fixture": "24h-delay"}),
        )
        for endpoint, path, payload in injected_calls:
            _status, _body, _bytes, elapsed_ms = _http_json(base_url, path, payload=payload)
            with pytest.raises(AssertionError, match="24h API latency guard"):
                _assert_24h_endpoint_latency(endpoint, elapsed_ms)
        delayed_stream_started = time.perf_counter()
        with urlopen(stream_request, timeout=10) as stream:
            assert b"event:" in stream.readline()
        with pytest.raises(AssertionError, match="24h API latency guard"):
            _assert_24h_endpoint_latency("stats-stream", (time.perf_counter() - delayed_stream_started) * 1000)

        revisited = responses[300, 1]
        status, returned, _bytes, _ms = _http_json(
            base_url, "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=stats-24h-matrix",
        )
        assert status == 200
        assert returned["request"]["id"] != revisited["request"]["id"]
        assert {
            key: value for key, value in returned.items() if key != "request"
        } == {
            key: value for key, value in revisited.items() if key != "request"
        }
    finally:
        if "http_server" in locals():
            stop_browser_share_server(http_server, http_thread)
        service.stop_event.set()
        service.work_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    ("clock_seconds", "label"),
    (
        (NOW, "before-oldest"),
        (NOW + 2 * 86_400, "empty-tail"),
    ),
)
def test_stats_24h_http_empty_windows_are_not_clamped(monkeypatch, tmp_path, clock_seconds, label):
    """Browser HTTP requests outside the seeded day preserve their requested empty window.

    The current protocol has no historical end-time query.  These cases therefore
    control the fixture service clock; a browser cannot request an arbitrary past
    24-hour window through the public endpoint until that contract is added.
    """

    state = tmp_path / label
    state.mkdir()
    database = state / storage.DATABASE_FILENAME
    socket_path = state / "services" / "statsd.sock"
    seed_end = NOW + 86_400
    _seed_realistic_stats(database, end=seed_end)
    service = stats_service.StatsCurrentService(
        socket_path, database, idle_seconds=60, clock=lambda: clock_seconds,
    )
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    http_server = http_thread = None
    try:
        assert service.cache_ready_event.wait(20), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client, client_binding_secret=b"stats-24h-empty-window-client-binding-secret",
            ),
        )
        http_server, http_thread = start_browser_share_server(monkeypatch, tmp_path, app, auth_bypass=True)
        status, snapshot, _bytes, elapsed_ms = _http_json(
            f"http://127.0.0.1:{http_server.server_address[1]}",
            "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=stats-24h-empty-window",
        )
        assert status == 200 and isinstance(snapshot, dict)
        _assert_24h_endpoint_latency(f"stats-snapshot-{label}", elapsed_ms)
        _assert_complete_bucket_window(snapshot["buckets"], snapshot["window_start"], snapshot["window_end"], 1)
        assert snapshot["window_start"] <= clock_seconds <= snapshot["window_end"]
        assert all(not bucket["series"] for bucket in snapshot["buckets"])
        assert snapshot["no_data"] == [], snapshot
    finally:
        if http_server is not None and http_thread is not None:
            stop_browser_share_server(http_server, http_thread)
        service.stop_event.set()
        service.work_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_v0610_stats_range_shift_g4b_keeps_each_transition_fast_and_complete(browser, monkeypatch, tmp_path):
    """G4b: enforce each range transition's max and rendered completion."""

    state = tmp_path / "stats-range-baseline"
    state.mkdir()
    socket_path = state / "services" / "statsd.sock"
    database = state / storage.DATABASE_FILENAME
    assert _seed_realistic_stats(database) == 8929
    monotonic_now = [0.0]
    service = stats_service.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=60,
        clock=lambda: NOW,
        monotonic=lambda: monotonic_now[0],
    )
    service_thread = threading.Thread(target=service.run, daemon=True)
    service_thread.start()
    http_server = http_thread = None
    try:
        assert service.cache_ready_event.wait(20), service._status()
        monotonic_now[0] = stats_service.RING_FLUSH_SECONDS
        service.work_event.set()
        assert _wait_for_ring_publication(service), service._status()
        with service.cache_lock:
            assert service._cache is not None
            service._cache = stats_service.PublishedCache(
                service._cache.generation,
                {},
                service._cache.resolution_generations,
                {},
            )
        assert service.writer is not None
        stage_samples = {name: [] for name in ("sqlite", "decode", "cost", "encode")}
        original_ring_read = service.writer.read_ring_window
        original_decode = stats_service._decode_ring_bucket
        original_cost_report = stats_service.materializer.build_cost_report
        original_encoder = service.encoder

        def timed_ring_read(**values):
            started = time.perf_counter()
            try:
                return original_ring_read(**values)
            finally:
                stage_samples["sqlite"].append((time.perf_counter() - started) * 1_000)

        def timed_decode(row):
            started = time.perf_counter()
            try:
                return original_decode(row)
            finally:
                stage_samples["decode"].append((time.perf_counter() - started) * 1_000)

        def timed_cost_report(layer):
            started = time.perf_counter()
            try:
                return original_cost_report(layer)
            finally:
                stage_samples["cost"].append((time.perf_counter() - started) * 1_000)

        def timed_encoder(wire):
            started = time.perf_counter()
            try:
                return original_encoder(wire)
            finally:
                stage_samples["encode"].append((time.perf_counter() - started) * 1_000)

        monkeypatch.setattr(service.writer, "read_ring_window", timed_ring_read)
        monkeypatch.setattr(stats_service, "_decode_ring_bucket", timed_decode)
        monkeypatch.setattr(stats_service.materializer, "build_cost_report", timed_cost_report)
        service.encoder = timed_encoder
        client = stats_client.StatsCurrentClient(socket_path, database)
        stage_breakdown = {}
        for range_seconds, resolution_seconds, label in (
            (300, 1, "300/1"),
            (900, 10, "900/10"),
            (3600, 60, "3600/60"),
            (86400, 300, "86400/300"),
        ):
            before = {
                name: (len(samples), sum(samples))
                for name, samples in stage_samples.items()
            }
            started = time.perf_counter()
            metadata, binary = client.snapshot({
                "range_seconds": range_seconds,
                "resolution": resolution_seconds,
                "client_id": "browser-current-fixture",
            })
            total_ms = (time.perf_counter() - started) * 1_000
            assert metadata["resolution_seconds"] == resolution_seconds
            assert binary
            stage_breakdown[label] = {
                "total_ms": total_ms,
                **{
                    f"{name}_ms": sum(samples) - before[name][1]
                    for name, samples in stage_samples.items()
                },
                "decoded_buckets": len(stage_samples["decode"]) - before["decode"][0],
            }
        print("\nYO!stats persisted-ring direct snapshot stages (ms)")
        print("view        total  sqlite  decode  cost  encode  buckets")
        for label, values in stage_breakdown.items():
            print(
                f"{label:>9}  {values['total_ms']:7.2f}  {values['sqlite_ms']:6.2f}  "
                f"{values['decode_ms']:6.2f}  {values['cost_ms']:5.2f}  "
                f"{values['encode_ms']:6.2f}  {values['decoded_buckets']:7d}"
            )
        Path("/tmp/yolomux-g4b-ring-stages-yo7775.json").write_text(
            json.dumps(stage_breakdown, indent=2) + "\n",
            encoding="utf-8",
        )
        initial_metadata, initial_binary = client.snapshot({
            "range_seconds": 300,
            "resolution": 1,
            "client_id": "browser-current-fixture",
        })
        assert initial_metadata["resolution_seconds"] == 1
        assert initial_binary
        initial_snapshot = json.loads(initial_binary)
        assert initial_snapshot["buckets"], initial_snapshot
        assert any(
            "cpu_percent:web" in bucket["series"]
            for bucket in initial_snapshot["buckets"]
        ), initial_snapshot
        asset_name = "stats-range-baseline.html"
        asset_dir = tmp_path / "stats-range-static"
        _write_current_stats_fixture_assets(asset_dir, asset_name)
        monkeypatch.setitem(web_module.STATIC_CONTENT_TYPES, asset_name, "text/html; charset=utf-8")
        monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)
        monkeypatch.setattr(server_module, "start_agent_auth_status_refresh", lambda *args, **kwargs: None)
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client, client_binding_secret=b"stats-range-baseline-client-binding-secret",
            ),
        )
        http_server, http_thread = start_browser_share_server(monkeypatch, tmp_path, app, auth_bypass=True)
        browser.get(f"http://127.0.0.1:{http_server.server_address[1]}/static/{asset_name}")
        _start_current_stats(browser)
        result = browser.execute_async_script(
            """
            const samples = arguments[0];
            const transitions = arguments[1];
            const latencyBudgetMs = arguments[2];
            const completionTimeoutMs = arguments[3];
            const injectedCompletionTimeoutMs = arguments[4];
            const done = arguments[arguments.length - 1];
            (async () => {
              const fixture = window.__statsFixture;
              const root = document.getElementById('stats-root');
              const results = Object.fromEntries(transitions.map(([, , label]) => [label, {first: [], cold: [], cached: [], requestDeltas: []}]));
              const requestsBeforeSweep = fixture.snapshotRequests.length;
              let requestsAfterFirstLap = null;
              const firstAnswers = new Map();
              const renderedState = () => {
                const generation = fixture.lastGeneration;
                const cpuChart = root.querySelector('[data-stats-chart="cpu"]');
                const cpuSeries = cpuChart?.querySelector('[data-series="cpu_percent:web"]');
                return {
                  renderedRange: Number(generation?.range_seconds),
                  renderedResolution: String(generation?.requested_resolution),
                  cpuPoints: Number(cpuSeries?.dataset.pointCount),
                  cpuPath: cpuSeries?.querySelector('path')?.getAttribute('d') || '',
                };
              };
              const waitForRenderedSnapshot = async (rangeSeconds, requestedResolution, renderDelayMs = 0, timeoutMs = completionTimeoutMs) => {
                try {
                  await window.__yolomuxTestWaitFor(() => {
                    const state = renderedState();
                    return state.renderedRange === rangeSeconds
                      && state.renderedResolution === String(requestedResolution)
                      && state.cpuPoints > 0;
                  }, {timeoutMs, description: `G4b rendered ${rangeSeconds}/${requestedResolution}`});
                } catch (_error) {
                  throw new Error(`G4b completion guard: ${rangeSeconds}/${requestedResolution} did not render before ${timeoutMs}ms`);
                }
                if (renderDelayMs) await new Promise(resolve => window.setTimeout(resolve, renderDelayMs));
              };
              const selectAndMeasure = async (rangeSeconds, requestedResolution, renderDelayMs = 0, timeoutMs = completionTimeoutMs) => {
                const started = performance.now();
                const range = root.querySelector('[data-stats-current-range]');
                if (Number(range.value) !== rangeSeconds) {
                  range.value = String(rangeSeconds);
                  range.dispatchEvent(new Event('change', {bubbles: true}));
                  const state = renderedState();
                  if (state.renderedRange !== rangeSeconds || state.renderedResolution !== 'AUTO' || state.cpuPoints <= 0) {
                    await fixture.clock.advance(0);
                    await waitForRenderedSnapshot(rangeSeconds, 'AUTO', renderDelayMs, timeoutMs);
                  } else if (renderDelayMs) {
                    await new Promise(resolve => window.setTimeout(resolve, renderDelayMs));
                  }
                }
                const resolution = root.querySelector('[data-stats-current-resolution]');
                if (String(resolution.value) !== String(requestedResolution)) {
                  resolution.value = String(requestedResolution);
                  resolution.dispatchEvent(new Event('change', {bubbles: true}));
                  const state = renderedState();
                  if (state.renderedRange !== rangeSeconds || state.renderedResolution !== String(requestedResolution) || state.cpuPoints <= 0) {
                    await fixture.clock.advance(0);
                    await waitForRenderedSnapshot(rangeSeconds, requestedResolution, renderDelayMs, timeoutMs);
                  } else if (renderDelayMs) {
                    await new Promise(resolve => window.setTimeout(resolve, renderDelayMs));
                  }
                }
                return performance.now() - started;
              };
              for (let sample = 0; sample < samples; sample += 1) {
                for (const [rangeSeconds, resolution, label] of transitions) {
                  const requestsBeforeSelection = fixture.snapshotRequests.length;
                  const elapsedMs = await selectAndMeasure(rangeSeconds, resolution);
                  const requestDelta = fixture.snapshotRequests.length - requestsBeforeSelection;
                  if (requestDelta < 0) throw new Error(`G4b request counter regressed for ${label}`);
                  const state = renderedState();
                  if (!state.cpuPath || state.cpuPoints <= 0) {
                    throw new Error(`range shift did not render ${label}: ${JSON.stringify(state)}`);
                  }
                  if (sample === 0) firstAnswers.set(label, state.cpuPath);
                  else if (firstAnswers.get(label) !== state.cpuPath) {
                    throw new Error(`G4b cache guard: revisit changed ${label}`);
                  }
                  results[label].requestDeltas.push(requestDelta);
                  if (sample === 0) results[label].first.push(elapsedMs);
                  results[label][requestDelta > 0 ? 'cold' : 'cached'].push(elapsedMs);
                }
                if (sample === 0) {
                  requestsAfterFirstLap = fixture.snapshotRequests.length;
                } else if (fixture.snapshotRequests.length !== requestsAfterFirstLap) {
                  throw new Error(`G4b cache guard: revisit refetched ${JSON.stringify({sample, requestsAfterFirstLap, requests: fixture.snapshotRequests.length})}`);
                }
              }
              const injectedLatencyMs = await selectAndMeasure(900, 10, latencyBudgetMs);
              const originalFetch = window.fetch;
              const pendingBeforeInjection = new Set(fixture.finiteOperations.keys());
              let rejectInjectedFetch;
              let injectedFetch;
              window.fetch = (input, options) => {
                const url = new URL(String(input), location.href);
                if (url.pathname === '/api/stats-snapshot') {
                  injectedFetch = new Promise((_resolve, reject) => { rejectInjectedFetch = reject; });
                  return injectedFetch;
                }
                return originalFetch(input, options);
              };
              let completionInjection = '';
              let injectedFiniteOperation = '';
              try {
                await selectAndMeasure(1800, 60, 0, injectedCompletionTimeoutMs);
              } catch (error) {
                completionInjection = String(error?.message || error);
              } finally {
                const injectedOperations = [...fixture.finiteOperations.keys()].filter(operation => (
                  !pendingBeforeInjection.has(operation)
                  && operation.includes('fetch:')
                  && operation.includes('/api/stats-snapshot')
                ));
                injectedFiniteOperation = injectedOperations.length === 1 ? injectedOperations[0] : '';
                window.fetch = originalFetch;
                if (rejectInjectedFetch) rejectInjectedFetch(new DOMException('G4b injected fetch released', 'AbortError'));
                if (injectedFetch) await Promise.allSettled([injectedFetch]);
                if (injectedFiniteOperation) {
                  await window.__yolomuxTestWaitFor(
                    () => !fixture.finiteOperations.has(injectedFiniteOperation),
                    {timeoutMs: completionTimeoutMs, description: `G4b settled ${injectedFiniteOperation}`},
                  );
                }
                if (injectedOperations.length !== 1) {
                  throw new Error(`G4b completion owner guard: ${JSON.stringify(injectedOperations)}`);
                }
              }
              done({
                results,
                requests: fixture.snapshotRequests.length,
                requestsAfterFirstLap,
                firstLapRequests: fixture.snapshotRequests.slice(requestsBeforeSweep, requestsAfterFirstLap).map(item => item.url),
                injectedLatencyMs,
                completionInjection,
                injectedFiniteOperation,
              });
            })().finally(() => window.__statsFixture?.mounted?.stop?.()).catch(error => done({error: String(error?.stack || error)}));
            """,
            SAMPLE_COUNT,
            SCALE_SWEEP,
            MAX_RANGE_SHIFT_MS,
            COMPLETION_TIMEOUT_MS,
            INJECTED_COMPLETION_TIMEOUT_MS,
        )
        assert result.get("error") is None, result
        expected_first_lap_requests = [
            (900, "AUTO"),
            (900, "10"),
            (3600, "AUTO"),
            (3600, "60"),
            (86400, "AUTO"),
            (86400, "300"),
            (300, "AUTO"),
        ]
        first_lap_request_identities = []
        for request_url in result["firstLapRequests"]:
            parsed = urlparse(request_url)
            query = parse_qs(parsed.query)
            assert parsed.path == "/api/stats-snapshot", request_url
            assert query["client_id"] == ["browser-current-fixture"], request_url
            first_lap_request_identities.append((int(query["range_seconds"][0]), query["resolution"][0]))
        assert first_lap_request_identities == expected_first_lap_requests, result
        assert result["requestsAfterFirstLap"] == 1 + len(expected_first_lap_requests), result
        distribution = {}
        print("\nYO!stats persisted-ring G4b range-shift latency (ms; 30 samples/transition)")
        print("transition  median  p95  max")
        latency_failures = []
        for _range_seconds, _resolution, label in SCALE_SWEEP:
            first = result["results"][label]["first"]
            cold = result["results"][label]["cold"]
            cached = result["results"][label]["cached"]
            request_deltas = result["results"][label]["requestDeltas"]
            assert len(first) == 1, (label, first)
            assert request_deltas[1:] == [0] * (SAMPLE_COUNT - 1), (label, request_deltas)
            expected_cold = 0 if label in {"300s->60s", "60s->10s"} else 1
            assert len(cold) == expected_cold, (label, cold, request_deltas)
            assert len(cached) == SAMPLE_COUNT - expected_cold, (label, cached)
            median, p95, maximum = _distribution(cached)
            distribution[label] = {
                "first_lap_ms": first[0],
                "cold_ms": cold[0] if cold else None,
                "first_lap_request_delta": request_deltas[0],
                "cached_median_ms": median,
                "cached_p95_ms": p95,
                "cached_max_ms": maximum,
            }
            print(f"{label:>10}  {median:7.2f}  {p95:7.2f}  {maximum:7.2f}")
            if first[0] >= MAX_RANGE_SHIFT_MS:
                latency_failures.append(
                    f"{label} first lap {first[0]:.1f} ms exceeds {MAX_RANGE_SHIFT_MS:.1f} ms"
                )
            if maximum >= MAX_RANGE_SHIFT_MS:
                latency_failures.append(
                    f"{label} cached max {maximum:.1f} ms exceeds {MAX_RANGE_SHIFT_MS:.1f} ms"
                )
        with pytest.raises(AssertionError, match="G4b latency guard"):
            _assert_range_shift_latency("injected post-render delay", result["injectedLatencyMs"])
        assert result["completionInjection"] == (
            f"G4b completion guard: 1800/AUTO did not render before "
            f"{INJECTED_COMPLETION_TIMEOUT_MS}ms"
        ), result
        Path("/tmp/yolomux-g4b-ring-yo7775.json").write_text(
            json.dumps({"samples": SAMPLE_COUNT, "distribution_ms": distribution}, indent=2) + "\n",
            encoding="utf-8",
        )
        assert not latency_failures, f"G4b latency guard: {'; '.join(latency_failures)}"
    finally:
        if http_server is not None and http_thread is not None:
            stop_browser_share_server(http_server, http_thread, browser=browser)
        service.stop_event.set()
        service.work_event.set()
        service_thread.join(timeout=3)
        assert not service_thread.is_alive()

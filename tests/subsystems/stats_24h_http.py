# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared 24-hour stats HTTP semantics and latency measurement engine."""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

import pytest

from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.helpers.gate_stats import NOW
from tests.helpers.gate_stats import _seed_realistic_stats
from tests.helpers.gate_stats import record_current_database_migration
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts
from yolomux_lib import http_routes as http_routes_module
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import pricing
from yolomux_lib.stats_current import resolution as stats_resolution
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yolomux_lib.stats_current.usage import usage_atom_from_source


# Cold 24h run: snapshot 11.0 ms (496,037 B), the other HTTP routes 0.6-2.5
# ms. Fifty milliseconds is a 4.5x margin over the slowest measured route.
MAX_24H_ENDPOINT_MS = 50.0
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


def assert_query_policy() -> None:
    assert stats_resolution.explicit_resolutions(86_400) == (300,)
    assert stats_resolution.auto_resolution(86_400) == 300
    assert 86_400 // 300 == 288


def assert_bunched_transcript_window_rejected(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "bunched",
        MockTranscriptSpec("stats-24h-bunched", usage_records=288, span_seconds=3600, start_timestamp=int(NOW)),
    )
    with pytest.raises(AssertionError, match="1700003600"):
        _assert_transcript_corpus_window("quiet", corpus, int(NOW), int(NOW + 86_399))


def assert_transcripts_fill_every_bucket(tmp_path: Path) -> None:
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
        record_current_database_migration(
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


def exercise_combined_observations_and_transcripts(monkeypatch, tmp_path: Path) -> list[dict[str, object]]:
    """Run the cold 24-hour semantic owner and return every ambient wall measurement."""

    tmp_path.mkdir(parents=True, exist_ok=True)
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
                write_json_bytes=lambda body, *, status: activity_route_writes.append((status, json.loads(body))),
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
        http_server, http_thread = start_browser_server(monkeypatch, tmp_path, app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
        endpoint_measurements = {}
        latency_samples = []
        status, snapshot, snapshot_bytes, snapshot_ms = _http_json(
            base_url,
            f"/api/stats-snapshot?range_seconds={range_seconds}&resolution={resolution_seconds}&client_id=stats-24h-correctness",
        )
        endpoint_measurements["stats-snapshot"] = (snapshot_ms, snapshot_bytes)
        latency_samples.append({
            "endpoint": "stats-snapshot", "case": "86400/300-cold",
            "elapsed_ms": snapshot_ms, "bytes": snapshot_bytes,
        })
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
                latency_samples.append({
                    "endpoint": "stats-snapshot", "case": f"{range_value}/{resolution_value}",
                    "elapsed_ms": response_ms, "bytes": response_bytes,
                })
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
        latency_samples.append({
            "endpoint": "stats-capabilities", "case": "default",
            "elapsed_ms": capabilities_ms, "bytes": capabilities_bytes,
        })
        assert isinstance(capabilities, dict)
        assert status == 200 and capabilities["ranges"] == json.loads(json.dumps(stats_resolution.wire_capabilities()["ranges"]))
        status, retry, retry_bytes, retry_ms = _http_json(base_url, "/api/stats-retry", payload={})
        endpoint_measurements["stats-retry"] = (retry_ms, retry_bytes)
        latency_samples.append({
            "endpoint": "stats-retry", "case": "ready",
            "elapsed_ms": retry_ms, "bytes": retry_bytes,
        })
        assert status == 200
        assert retry["state"] == "ready" and retry["terminal"] is True
        assert retry["request"]["id"].startswith("r-")
        assert retry["data"] == {"ok": True, "status": "ready"}
        assert retry["ok"] is True and retry["status"] == "ready"
        status, observations, observations_bytes, observations_ms = _http_json(
            base_url, "/api/stats-observations", payload={"fixture": "24h"},
        )
        endpoint_measurements["stats-observations"] = (observations_ms, observations_bytes)
        latency_samples.append({
            "endpoint": "stats-observations", "case": "accepted",
            "elapsed_ms": observations_ms, "bytes": observations_bytes,
        })
        assert status == 200 and observations["accepted"] == 1
        stream_request = Request(
            f"{base_url}/api/stats-stream?range_seconds=86400&resolution_seconds=300&client_id=stats-24h-correctness&after_cache_generation={snapshot['cache_generation']}&after_revision=0",
        )
        stream_started = time.perf_counter()
        with urlopen(stream_request, timeout=10) as stream:
            stream_line = stream.readline()
            assert stream.status == 200 and b"event:" in stream_line
        stream_ms = (time.perf_counter() - stream_started) * 1000
        endpoint_measurements["stats-stream"] = (stream_ms, len(stream_line))
        latency_samples.append({
            "endpoint": "stats-stream", "case": "first-frame",
            "elapsed_ms": stream_ms, "bytes": len(stream_line),
        })
        assert set(endpoint_measurements) == {
            "stats-snapshot", "stats-capabilities", "stats-retry", "stats-observations", "stats-stream",
        }
        print("24h endpoint ms/bytes", endpoint_measurements)

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
        assert {key: value for key, value in returned.items() if key != "request"} == {
            key: value for key, value in revisited.items() if key != "request"
        }
        return latency_samples
    finally:
        if "http_server" in locals():
            stop_browser_server(http_server, http_thread)
        service.stop_event.set()
        service.work_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def exercise_empty_window(monkeypatch, tmp_path: Path, clock_seconds: float, label: str) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = tmp_path / label
    state.mkdir()
    database = state / storage.DATABASE_FILENAME
    socket_path = state / "services" / "statsd.sock"
    seed_end = NOW + 86_400
    _seed_realistic_stats(database, end=seed_end)
    service = stats_service.StatsCurrentService(socket_path, database, idle_seconds=60, clock=lambda: clock_seconds)
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
        http_server, http_thread = start_browser_server(monkeypatch, tmp_path, app, auth_bypass=True)
        status, snapshot, response_bytes, elapsed_ms = _http_json(
            f"http://127.0.0.1:{http_server.server_address[1]}",
            "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=stats-24h-empty-window",
        )
        assert status == 200 and isinstance(snapshot, dict)
        _assert_complete_bucket_window(snapshot["buckets"], snapshot["window_start"], snapshot["window_end"], 1)
        assert snapshot["window_start"] <= clock_seconds <= snapshot["window_end"]
        assert all(not bucket["series"] for bucket in snapshot["buckets"])
        assert snapshot["no_data"] == [], snapshot
        return {
            "endpoint": "stats-snapshot", "case": label,
            "elapsed_ms": elapsed_ms, "bytes": response_bytes,
        }
    finally:
        if http_server is not None and http_thread is not None:
            stop_browser_server(http_server, http_thread)
        service.stop_event.set()
        service.work_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _series_values(snapshot: dict[str, object], name: str) -> list[int]:
    buckets = snapshot["buckets"]
    assert isinstance(buckets, list)
    return [int(bucket["series"][name]["value"]) for bucket in buckets if name in bucket["series"]]


def _bucket_series_values(snapshot: dict[str, object], name: str) -> dict[int, int]:
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
    finest_resolution = resolutions[0]
    finest = responses[range_seconds, finest_resolution]
    finest_values = _bucket_series_values(finest, "usage_tokens")
    finest_total = sum(finest_values.values())
    finest_cost = int(finest["cost_report"]["total_micro_usd"])
    for resolution_value in resolutions:
        response = responses[range_seconds, resolution_value]
        values = _bucket_series_values(response, "usage_tokens")
        assert sum(values.values()) == finest_total
        assert int(response["cost_report"]["total_micro_usd"]) == finest_cost
        compared_bucket_count = 0
        for bucket_start, bucket_value in values.items():
            fine_starts = range(bucket_start, bucket_start + resolution_value, finest_resolution)
            if not all(fine_start in finest_values for fine_start in fine_starts):
                continue
            assert bucket_value == sum(finest_values[fine_start] for fine_start in fine_starts)
            compared_bucket_count += 1
        assert compared_bucket_count


def _generate_24h_transcript_corpora(root: Path) -> dict[str, object]:
    return {name: generate_mock_transcripts(root / name, spec) for name, spec in TRANSCRIPT_CORPUS_SPECS}


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


def _assert_complete_bucket_window(
    buckets: list[object], window_start: int, window_end: int, resolution_seconds: int,
) -> None:
    assert [bucket["start"] for bucket in buckets] == list(range(window_start, window_end, resolution_seconds))


def _assert_24h_endpoint_latency(endpoint: str, elapsed_ms: float) -> None:
    assert elapsed_ms < MAX_24H_ENDPOINT_MS, (
        f"24h API latency guard: {endpoint} took {elapsed_ms:.1f} ms; limit is {MAX_24H_ENDPOINT_MS:.1f} ms"
    )


def _http_json(base_url: str, path: str, *, payload: object | None = None) -> tuple[int, object, int, float]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        body,
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

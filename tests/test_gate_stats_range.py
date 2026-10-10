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
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest

from tests import latency_calibration
from tests.browser_helpers.browser_layout import (  # noqa: F401
    browser,
    start_browser_server,
    stop_browser_server,
)
from tests.helpers.browser_stats_coverage import _start_current_stats, _write_current_stats_fixture_assets
from tests.helpers.gate_stats import stop_stats_current_service
from tests.gate_harness import run_fixture_cleanup_phases
from tests.helpers.gate_stats import NOW
from tests.helpers.gate_stats import _seed_realistic_stats
from tests.subsystems import stats_24h_http
from tests.subsystems.stats_24h_http import MAX_24H_ENDPOINT_MS
from tests.subsystems.stats_24h_http import assert_bunched_transcript_window_rejected
from tests.subsystems.stats_24h_http import assert_query_policy
from tests.subsystems.stats_24h_http import assert_transcripts_fill_every_bucket
from tests.subsystems.stats_24h_http import exercise_combined_observations_and_transcripts
from tests.subsystems.stats_24h_http import exercise_empty_window
from yomux_lib import server as server_module
from yomux_lib import web as web_module
from yomux_lib.stats_current import client as stats_client
from yomux_lib.stats_current import http as stats_http
from yomux_lib.stats_current import service as stats_service
from yomux_lib.stats_current import storage


SAMPLE_COUNT = 30
MAX_RANGE_SHIFT_MS = 350.0
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
pytestmark = pytest.mark.browser
certification_phase_only = latency_calibration.certification_phase_fixture()


@pytest.mark.no_browser
def test_stats_24h_service_admission_failure_still_retires_service(monkeypatch, tmp_path):
    class FixtureStore:
        def __init__(self, database):
            self.database = Path(database)

        def __enter__(self):
            self.database.touch(exist_ok=True)
            return self

        def __exit__(self, *_args):
            return False

        def append_batch(self, *, usage_atoms):
            return SimpleNamespace(usage_atoms_accepted=len(usage_atoms))

    class NotReady:
        def wait(self, _timeout):
            return False

    class RejectedService:
        def __init__(self, *_args, **_kwargs):
            self.cache_ready_event = NotReady()
            self.stop_event = threading.Event()
            self.work_event = threading.Event()
            self.worker = None
            services.append(self)

        def run(self):
            self.worker = threading.current_thread()
            self.stop_event.wait(1)

        def _status(self):
            return {"admission": "not_ready"}

    services = []
    monkeypatch.setattr(stats_24h_http.storage.Store, "open", lambda database: FixtureStore(database))
    monkeypatch.setattr(stats_24h_http, "_seed_realistic_stats", lambda database, *, end: (Path(database).touch() or 8_929))
    monkeypatch.setattr(
        stats_24h_http,
        "_generate_24h_transcript_corpora",
        lambda _root: {name: SimpleNamespace(usage_records=spec.usage_records) for name, spec in stats_24h_http.TRANSCRIPT_CORPUS_SPECS},
    )
    monkeypatch.setattr(stats_24h_http, "_assert_transcript_corpus_window", lambda *_args: None)
    monkeypatch.setattr(
        stats_24h_http,
        "_scan_transcript_atoms",
        lambda _corpora: (
            SimpleNamespace(items=[SimpleNamespace(agent_kind="claude"), SimpleNamespace(agent_kind="codex")]),
            [object()] * 648,
        ),
    )
    monkeypatch.setattr(stats_24h_http.stats_service, "StatsCurrentService", RejectedService)

    with pytest.raises(AssertionError, match="admission.*not_ready"):
        exercise_combined_observations_and_transcripts(monkeypatch, tmp_path)
    assert len(services) == 1
    service = services[0]
    assert service.stop_event.is_set() and service.work_event.is_set()
    assert service.worker is not None and not service.worker.is_alive()


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


def test_debug_stats_sample_endpoint_is_not_a_live_client_contract():
    """The retired sample route must not survive as a latent browser 404."""
    source = Path("static_src/js/yomux/85_debug_panel.js").read_text(encoding="utf-8")
    assert "/api/stats-sample" not in source


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_stats_24h_query_policy_is_limited_to_five_minute_buckets():
    """The 600-bucket ceiling deliberately makes a 24-hour view queryable only at 300 seconds."""

    assert_query_policy()


def test_stats_24h_transcript_window_guard_rejects_a_bunched_corpus(tmp_path):
    assert_bunched_transcript_window_rejected(tmp_path)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_stats_24h_transcripts_fill_every_five_minute_bucket_without_direct_seed(tmp_path):
    """The graph's 24-hour token series must come from transcript atoms, not the direct seed."""

    assert_transcripts_fill_every_bucket(tmp_path)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_stats_24h_combined_observations_and_transcripts_reconcile_at_300_seconds(monkeypatch, tmp_path):
    """The parallel lane owns 24-hour semantics; exclusive certification owns wall time."""

    exercise_combined_observations_and_transcripts(monkeypatch, tmp_path)


@pytest.mark.parametrize(
    ("clock_seconds", "label"),
    (
        (NOW, "before-oldest"),
        (NOW + 2 * 86_400, "empty-tail"),
    ),
)
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_stats_24h_http_empty_windows_are_not_clamped(monkeypatch, tmp_path, clock_seconds, label):
    """Browser HTTP requests outside the seeded day preserve their requested empty window.

    The current protocol has no historical end-time query.  These cases therefore
    control the fixture service clock; a browser cannot request an arbitrary past
    24-hour window through the public endpoint until that contract is added.
    """

    exercise_empty_window(monkeypatch, tmp_path, clock_seconds, label)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_stats_24h_http_wall_latency_certification(
    certification_phase_only, monkeypatch, tmp_path, request,
):
    """Certify every retained 24-hour HTTP wall budget after exclusive host admission."""

    samples = exercise_combined_observations_and_transcripts(
        monkeypatch, tmp_path / "combined",
    )
    samples.extend(
        exercise_empty_window(
            monkeypatch, tmp_path / f"empty-{label}", clock_seconds, label,
        )
        for clock_seconds, label in (
            (NOW, "before-oldest"),
            (NOW + 2 * 86_400, "empty-tail"),
        )
    )
    verdicts = [
        latency_calibration.fixed_ceiling_verdict(
            label=f"24h HTTP {sample['endpoint']} {sample['case']}",
            raw_measured_ms=sample["elapsed_ms"],
            ceiling_ms=MAX_24H_ENDPOINT_MS,
            statistic="single-request-wall",
        )
        for sample in samples
    ]
    latency_calibration.certify_verdicts(
        nodeid=request.node.nodeid,
        label="stats-24h-http-wall-latency",
        verdicts=verdicts,
        qualification=certification_phase_only,
        extra_evidence={"ceiling_ms": MAX_24H_ENDPOINT_MS, "samples": samples},
    )


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
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
        http_server, http_thread = start_browser_server(monkeypatch, tmp_path, app, auth_bypass=True)
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
                  await window.__yomuxTestWaitFor(() => {
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
                  const rangeDefaultResolution = String(root.querySelector('[data-stats-current-resolution]').value);
                  const state = renderedState();
                  if (state.renderedRange !== rangeSeconds || state.renderedResolution !== rangeDefaultResolution || state.cpuPoints <= 0) {
                    await fixture.clock.advance(0);
                    await waitForRenderedSnapshot(rangeSeconds, rangeDefaultResolution, renderDelayMs, timeoutMs);
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
              const originalOpenNetworkEventSource = fixture.openNetworkEventSource;
              const pendingBeforeInjection = new Set(fixture.finiteOperations.keys());
              let injectedSource = null;
              fixture.openNetworkEventSource = (input, options) => {
                const url = new URL(String(input), location.href);
                if (url.pathname === '/api/stats-stream' && Number(url.searchParams.get('range_seconds')) === 1800) {
                  const listeners = new Map();
                  injectedSource = {
                    closed: false,
                    addEventListener(name, callback) {
                      const values = listeners.get(name) || [];
                      values.push(callback);
                      listeners.set(name, values);
                    },
                    close() { this.closed = true; },
                  };
                  return injectedSource;
                }
                return originalOpenNetworkEventSource(input, options);
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
                  && operation.includes('sse-init:')
                  && operation.includes('/api/stats-stream')
                ));
                injectedFiniteOperation = injectedOperations.length === 1 ? injectedOperations[0] : '';
                fixture.openNetworkEventSource = originalOpenNetworkEventSource;
                injectedSource?.close();
                if (injectedFiniteOperation) {
                  await window.__yomuxTestWaitFor(
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
        # Data identity is (range_seconds, resolved resolution_seconds): an explicit pick that
        # resolves to the same concrete bucket size as the AUTO fetch that just landed for this
        # range (900/10, 86400/300) reuses that generation with zero refetch, so only the first
        # occurrence of each distinct (range, resolved resolution) pair appears here. The trailing
        # 300/AUTO transition also resolves to 1s, already cached from the initial page load.
        expected_first_lap_requests = [
            (900, "AUTO"),
            (3600, "AUTO"),
            (3600, "60"),
            (86400, "AUTO"),
        ]
        first_lap_request_identities = []
        for request_url in result["firstLapRequests"]:
            parsed = urlparse(request_url)
            query = parse_qs(parsed.query)
            assert parsed.path == "/api/stats-stream", request_url
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
            # "10s->1s" (900 -> 300, explicit 1) now also resolves entirely from cache: range 300's
            # resolved-1s generation was already fetched at page load, so the range-change AUTO
            # step and the explicit pick both reuse it under the resolved-resolution cache key.
            expected_cold = 0 if label in {"300s->60s", "60s->10s", "10s->1s"} else 1
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
        run_fixture_cleanup_phases("G4b stats range browser", (
            ("HTTP server", lambda: stop_browser_server(http_server, http_thread, browser=browser) if http_server is not None and http_thread is not None else None),
            ("stats service", lambda: stop_stats_current_service(service, service_thread)),
        ))

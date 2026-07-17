# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""HTTP-served browser coverage for the current render-only YO!stats client."""

import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.request import urlopen

from selenium.webdriver.common.by import By

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from yolomux_lib import server as server_module
from yolomux_lib import web as web_module
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import resolution as stats_resolution
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage


CURRENT_STATS_SOURCE = Path("static_src/js/yolomux/82_stats_current.js").read_text(encoding="utf-8")


def _current_stats_fixture_html(*, network_fetch=False) -> str:
    setup = r"""
    class FixtureClock {
      constructor() {
        this.time = 1700000000000;
        this.nextId = 1;
        this.timers = new Map();
      }
      now = () => this.time;
      setTimeout = (callback, delay) => {
        const id = this.nextId++;
        this.timers.set(id, {at: this.time + delay, callback});
        return id;
      };
      clearTimeout = id => this.timers.delete(id);
      nextDelay() {
        if (!this.timers.size) return null;
        return Math.min(...[...this.timers.values()].map(timer => timer.at - this.time));
      }
      async advance(milliseconds) {
        const target = this.time + milliseconds;
        while (true) {
          const due = [...this.timers.entries()]
            .filter(([_id, timer]) => timer.at <= target)
            .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
          if (!due) break;
          this.time = due[1].at;
          this.timers.delete(due[0]);
          due[1].callback();
          await Promise.resolve();
          await Promise.resolve();
        }
        this.time = target;
        await Promise.resolve();
        await Promise.resolve();
      }
    }

    const matrix = [
      [300, 1, [1, 10]],
      [900, 10, [10, 60]],
      [1800, 10, [10, 60]],
      [3600, 10, [10, 60, 300]],
      [7200, 60, [60, 300]],
      [14400, 60, [60, 300]],
      [28800, 60, [60, 300]],
      [57600, 300, [300]],
      [86400, 300, [300]],
    ];
    const capabilities = {
      resolution_choices: [1, 10, 60, 300],
      max_buckets: 600,
      min_buckets: 12,
      max_live_cadence_seconds: 60,
      ranges: matrix.map(([rangeSeconds, auto, explicit]) => ({
        range_seconds: rangeSeconds,
        auto_resolution_seconds: auto,
        explicit_resolution_seconds: explicit,
        buckets: Object.fromEntries(explicit.map(resolution => [resolution, rangeSeconds / resolution])),
      })),
    };

    function seriesValue(value, at) {
      return {value, source_count: 1, first_timestamp: at, last_timestamp: at};
    }

    function fixtureCostDimensions() {
      return {
        input: {tokens: 900, micro_usd: 100000, api_list_micro_usd: 100000},
        cache_read: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
        cache_write: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
        output: {tokens: 120, micro_usd: 150000, api_list_micro_usd: 150000},
        other: {tokens: 120, micro_usd: 0, api_list_micro_usd: 0},
      };
    }

    function fixtureCostReport() {
      const dimensions = fixtureCostDimensions();
      const attribution = {
        total_tokens: 1140, total_micro_usd: 250000,
        total_api_list_micro_usd: 250000, dimensions,
        priced: {atoms: 2, tokens: 1020}, unpriced: {atoms: 1, tokens: 120},
      };
      return {
        schema_version: 2,
        total_micro_usd: 250000,
        total_api_list_micro_usd: 250000,
        total_tokens: 1140,
        dimensions,
        priced: {atoms: 2, tokens: 1020},
        unpriced: {atoms: 1, tokens: 120},
        models: [{key: '0123456789abcdef01234567', provider: 'openai', model: 'gpt-5.6-sol', ...attribution}],
        agents: [{key: '89abcdef0123456789abcdef', source: 'codex', label: 'yo8881|0|codex', ...attribution}],
        evidence: [{
          key: 'fedcba9876543210fedcba98', provider: 'openai', model: 'gpt-5.6-sol',
          dimension: 'output', direction: 'output', modality: 'text', cache_role: 'none',
          unit: 'tokens', pricing_profile: 'default', service_tier: 'default',
          catalog_model: 'gpt-5.6-sol', rate_usd: '10.00', rate_scale: 1000000,
          effective_from: '2026-07-09', source_kind: 'seed',
          source_url: 'https://example.com/pricing', catalog_revision: 3,
          tokens: 120, micro_usd: 150000, api_list_micro_usd: 150000,
          priced_atoms: 1,
        }],
        catalog_revision: 3,
        omissions: {models: 0, agents: 0, evidence: 0},
        reasoning_available: false,
      };
    }

    function exactSnapshot(rangeSeconds, requestedResolution, resolutionSeconds) {
      const cacheGeneration = ++window.__statsFixture.cacheGeneration;
      const windowEnd = Math.floor(window.__statsFixture.clock.now() / 1000 / resolutionSeconds) * resolutionSeconds;
      const windowStart = windowEnd - rangeSeconds;
      const bucketCount = rangeSeconds / resolutionSeconds;
      const buckets = Array.from({length: bucketCount}, (_unused, index) => {
        const start = windowStart + index * resolutionSeconds;
        const series = {'cpu_percent:host': seriesValue(10 + index % 7, start)};
        if (index === 0) {
          series['agent_tokens_per_minute:sol'] = seriesValue(120, start);
          series['model_tokens_per_minute:output:gpt-5.6-sol'] = seriesValue(120, start);
          series['model_tokens_per_minute:input:gpt-5.6-sol'] = seriesValue(900, start);
          series.cost_micro_usd = seriesValue(250000, start);
          series.usage_tokens = seriesValue(1140, start);
        }
        if (index === bucketCount - Math.ceil(10 / resolutionSeconds)) {
          series.run_agents = seriesValue(2, start);
          series['gpu_util_percent:gpu:0'] = seriesValue(40, start);
        }
        if (index === bucketCount - Math.ceil(60 / resolutionSeconds)) {
          series.system_memory_used_bytes = seriesValue(8000000000, start);
        }
        return {
          start,
          duration: resolutionSeconds,
          series,
          source: {first_timestamp: start, last_timestamp: start, count: 1},
          open: index === bucketCount - 1,
        };
      });
      const snapshot = {
        protocol_version: 2,
        range_seconds: rangeSeconds,
        requested_resolution: requestedResolution,
        resolution_seconds: resolutionSeconds,
        window_start: windowStart,
        window_end: windowEnd,
        generated_at: windowEnd,
        source_generation: cacheGeneration,
        cache_generation: cacheGeneration,
        rightmost_open: true,
        buckets,
        no_data: [{
          family: 'gpu', source_id: 'gpu:0', start: windowStart, end: windowStart + resolutionSeconds,
          epoch: 'gpu-e1', reason: 'coverage_gap', source_cadence_seconds: 10,
        }],
        cost_report: fixtureCostReport(),
      };
      window.__statsFixture.lastSnapshot = snapshot;
      return snapshot;
    }

    class FixtureEventSource {
      constructor(url) {
        this.url = url;
        this.listeners = new Map();
        this.closed = false;
        window.__statsFixture.eventSources.push(this);
      }
      addEventListener(name, callback) {
        const listeners = this.listeners.get(name) || [];
        listeners.push(callback);
        this.listeners.set(name, listeners);
      }
      close() { this.closed = true; }
      emit(name, payload) {
        for (const callback of this.listeners.get(name) || []) callback({data: JSON.stringify(payload)});
      }
    }

    window.__statsFixture = {
      capabilities,
      clock: new FixtureClock(),
      cacheGeneration: 0,
      snapshotRequests: [],
      eventSources: [],
      generationEvents: [],
      lastSnapshot: null,
      mounted: null,
    };

    window.__statsFixture.fetch = async input => {
      const url = new URL(String(input), location.href);
      if (window.__statsNetworkFetch) {
        const response = await window.fetch(input, {credentials: 'same-origin', cache: 'no-store'});
        if (response.status !== 200 || !['/api/stats-capabilities', '/api/stats-snapshot'].includes(url.pathname)) {
          return response;
        }
        const payload = await response.json();
        if (url.pathname === '/api/stats-capabilities') {
          window.__statsFixture.capabilities = payload;
        } else {
          window.__statsFixture.lastSnapshot = payload;
          window.__statsFixture.snapshotRequests.push({url: url.pathname + url.search, snapshot: payload});
        }
        return {status: 200, json: async () => structuredClone(payload)};
      }
      if (url.pathname === '/api/stats-capabilities') return {status: 200, json: async () => capabilities};
      if (url.pathname !== '/api/stats-snapshot') return {status: 404, json: async () => ({})};
      const rangeSeconds = Number(url.searchParams.get('range_seconds'));
      const requestedText = url.searchParams.get('resolution');
      const requestedResolution = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
      const row = capabilities.ranges.find(item => item.range_seconds === rangeSeconds);
      const resolutionSeconds = requestedResolution === 'AUTO' ? row.auto_resolution_seconds : requestedResolution;
      const snapshot = exactSnapshot(rangeSeconds, requestedResolution, resolutionSeconds);
      window.__statsFixture.snapshotRequests.push({url: url.pathname + url.search, snapshot});
      return {status: 200, json: async () => snapshot};
    };

    window.__statsFixture.start = async view => {
      const root = document.getElementById('stats-root');
      const mounted = YOLOmuxStatsCurrent.mount(root, {
        view,
        clientId: 'browser-current-fixture',
        savedRange: 300,
        savedResolution: 1,
        fetch: window.__statsFixture.fetch,
        EventSource: FixtureEventSource,
        controllerOptions: {
          clock: window.__statsFixture.clock,
          onGeneration: generation => window.__statsFixture.generationEvents.push({
            cacheGeneration: generation.cache_generation,
            dataset: JSON.stringify(generation),
          }),
        },
      });
      window.__statsFixture.mounted = mounted;
      await mounted.start();
      await window.__statsFixture.clock.advance(0);
      await window.__yolomuxTestWaitFor(
        () => root.querySelector(view === 'cost' ? '[data-stats-chart="cost"]' : '[data-stats-chart="cpu"]'),
        {description: 'current stats first exact generation'}
      );
      return mounted;
    };

    window.__statsFixture.select = async (rangeSeconds, requestedResolution) => {
      const root = document.getElementById('stats-root');
      const range = root.querySelector('[data-stats-current-range]');
      if (Number(range.value) !== rangeSeconds) {
        const beforeRange = window.__statsFixture.snapshotRequests.length;
        range.value = String(rangeSeconds);
        range.dispatchEvent(new Event('change', {bubbles: true}));
        await window.__statsFixture.clock.advance(0);
        await window.__yolomuxTestWaitFor(
          () => window.__statsFixture.snapshotRequests.slice(beforeRange).some(item => (
            item.snapshot.range_seconds === rangeSeconds && item.snapshot.requested_resolution === 'AUTO'
          )),
          {description: `current stats ${rangeSeconds}/AUTO range generation`}
        );
      }
      const resolution = root.querySelector('[data-stats-current-resolution]');
      if (String(resolution.value) === String(requestedResolution)) return;
      const before = window.__statsFixture.snapshotRequests.length;
      resolution.value = String(requestedResolution);
      resolution.dispatchEvent(new Event('change', {bubbles: true}));
      await window.__statsFixture.clock.advance(0);
      await window.__yolomuxTestWaitFor(
        () => window.__statsFixture.snapshotRequests.slice(before).some(item => (
          item.snapshot.range_seconds === rangeSeconds
          && item.snapshot.requested_resolution === requestedResolution
        )) && root.querySelector('[data-stats-chart="cpu"]'),
        {description: `current stats ${rangeSeconds}/${requestedResolution} generation`}
      );
    };

    window.__statsFixture.emitCpuDelta = value => {
      const base = window.__statsFixture.lastSnapshot;
      const replacement = structuredClone(base.buckets.at(-1));
      replacement.series['cpu_percent:host'] = seriesValue(value, replacement.start);
      const nextGeneration = base.cache_generation + 1;
      const delta = {
        protocol_version: 2,
        range_seconds: base.range_seconds,
        resolution_seconds: base.resolution_seconds,
        source_generation: nextGeneration,
        base_cache_generation: base.cache_generation,
        cache_generation: nextGeneration,
        revision: 1,
        buckets: [replacement],
        no_data: [],
        tombstones: [],
        cost_report: structuredClone(base.cost_report),
      };
      const source = [...window.__statsFixture.eventSources].reverse().find(item => !item.closed);
      source.emit('delta', delta);
      return delta;
    };

    window.__statsFixture.emitSparseCadenceDelta = () => {
      const base = window.__statsFixture.lastSnapshot;
      const replacement = structuredClone(base.buckets.at(-1));
      const at = replacement.start;
      replacement.series.run_agents = seriesValue(3, at);
      replacement.series['gpu_util_percent:gpu:0'] = seriesValue(41, at);
      replacement.series.system_memory_used_bytes = seriesValue(8100000000, at);
      replacement.source = {first_timestamp: at, last_timestamp: at, count: 4};
      const nextGeneration = base.cache_generation + 1;
      const delta = {
        protocol_version: 2,
        range_seconds: base.range_seconds,
        resolution_seconds: base.resolution_seconds,
        source_generation: nextGeneration,
        base_cache_generation: base.cache_generation,
        cache_generation: nextGeneration,
        revision: 1,
        buckets: [replacement],
        no_data: [],
        tombstones: [],
        cost_report: structuredClone(base.cost_report),
      };
      const source = [...window.__statsFixture.eventSources].reverse().find(item => !item.closed);
      source.emit('delta', delta);
      return delta;
    };
    """
    body = f"""
    <main id="stats-shell"><div id="stats-root"></div></main>
    <script>eval({json.dumps(CURRENT_STATS_SOURCE)});</script>
    <script>window.__statsNetworkFetch = {str(network_fetch).lower()};</script>
    <script>{setup}</script>
    """
    return page_html(body, extra_css="#stats-shell { width: 100%; min-width: 0; }")


def _start_current_stats(browser, view="stats"):
    result = browser.execute_async_script(
        """
        const view = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__statsFixture.start(view).then(() => done({ok: true})).catch(error => done({error: String(error?.stack || error)}));
        """,
        view,
    )
    assert result.get("error") is None, result


def _load_current_stats(browser, tmp_path, view="stats"):
    load_static_html_fixture(
        browser,
        tmp_path,
        f"current-stats-{view}.html",
        _current_stats_fixture_html(),
    )
    _start_current_stats(browser, view)


def test_current_stats_browser_traverses_every_exact_matrix_cell(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    cells = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const results = [];
          for (const row of window.__statsFixture.capabilities.ranges) {
            for (const requested of ['AUTO', ...row.explicit_resolution_seconds]) {
              await window.__statsFixture.select(row.range_seconds, requested);
              const root = document.getElementById('stats-root');
              const request = window.__statsFixture.snapshotRequests.at(-1);
              const points = [...root.querySelectorAll('[data-point-count]')].map(item => Number(item.dataset.pointCount));
              const paths = [...root.querySelectorAll('[data-stats-chart]')].map(chart => ({
                id: chart.dataset.statsChart,
                series: chart.querySelectorAll('[data-series]').length,
                paths: chart.querySelectorAll('[data-series] > path').length,
              }));
              results.push({
                range: row.range_seconds,
                requested,
                resolution: request.snapshot.resolution_seconds,
                bucketCount: request.snapshot.buckets.length,
                maxPoints: Math.max(...points),
                exactLabel: root.querySelector('.yo-stats-current-exact').textContent,
                axisSeconds: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
                paths,
                requestUrl: request.url,
              });
            }
          }
          done(results);
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert isinstance(cells, list), cells
    assert len(cells) == 26
    assert {cell["resolution"] for cell in cells} == {1, 10, 60, 300}
    for cell in cells:
        assert cell["bucketCount"] == cell["range"] // cell["resolution"], cell
        assert cell["maxPoints"] == cell["bucketCount"] <= 600, cell
        assert cell["exactLabel"] == f"Exact {cell['resolution']}s", cell
        assert cell["axisSeconds"] == ("true" if cell["resolution"] == 1 else "false"), cell
        assert f"range_seconds={cell['range']}" in cell["requestUrl"], cell
        assert f"resolution={cell['requested']}" in cell["requestUrl"], cell
        assert all(path["paths"] == path["series"] for path in cell["paths"]), cell


def test_http_client_rpc_cache_and_browser_render_every_exact_matrix_cell(
    browser,
    monkeypatch,
    tmp_path,
):
    now = 1_700_000_000.0
    service_now = [now - 60]
    state = tmp_path / "current-stats-e2e"
    state.mkdir()
    socket_path = state / "services" / "statsd.sock"
    database = state / storage.DATABASE_FILENAME
    service = stats_service.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=60,
        clock=lambda: service_now[0],
    )
    service_thread = threading.Thread(target=service.run, daemon=True)
    service_thread.start()
    http_server = http_thread = follower_server = follower_thread = None
    try:
        assert service.cache_ready_event.wait(5), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        service.cache_ready_event.clear()
        service_now[0] = now
        appended = client.append(
            observations=(storage.Observation(
                "cpu-real", "cpu", "web", now - 0.25, "cpu-epoch", 1,
                {"process_percent": 7, "system_percent": 23},
            ),),
            coverage_epochs=(storage.CoverageEpoch(
                "cpu", "web", "cpu-epoch", now - 10, None, 1, 1,
            ),),
            usage_atoms=(storage.UsageAtom(
                "usage-real", "output", "text", "none", "tokens", now - 0.25,
                {
                    "quantity": 25,
                    "provider": "openai",
                    "model": "gpt-real",
                    "agent_id": "sol",
                    "telemetry_complete": True,
                },
            ),),
        )
        assert appended["accepted"] == 3
        assert service.cache_ready_event.wait(5), service._status()
        assert service._status()["generations"]["cache_matches_source"] is True

        asset_name = "stats-current-e2e.html"
        asset_dir = tmp_path / "current-stats-static"
        asset_dir.mkdir()
        (asset_dir / asset_name).write_text(
            _current_stats_fixture_html(network_fetch=True),
            encoding="utf-8",
        )
        monkeypatch.setitem(
            web_module.STATIC_CONTENT_TYPES,
            asset_name,
            "text/html; charset=utf-8",
        )
        monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)
        monkeypatch.setattr(
            server_module,
            "start_agent_auth_status_refresh",
            lambda *args, **kwargs: None,
        )
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client,
                client_binding_secret=b"stats-e2e-client-binding-secret",
            ),
        )
        http_server, http_thread = start_browser_share_server(
            monkeypatch,
            tmp_path,
            app,
            auth_bypass=True,
        )
        browser.get(
            f"http://127.0.0.1:{http_server.server_address[1]}/static/{asset_name}"
        )
        _start_current_stats(browser)

        cells = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const results = [];
              for (const row of window.__statsFixture.capabilities.ranges) {
                for (const requested of ['AUTO', ...row.explicit_resolution_seconds]) {
                  await window.__statsFixture.select(row.range_seconds, requested);
                  const root = document.getElementById('stats-root');
                  const accepted = window.__statsFixture.snapshotRequests.at(-1).snapshot;
                  results.push({
                    range: row.range_seconds,
                    requested,
                    concrete: accepted.resolution_seconds,
                    buckets: accepted.buckets.length,
                    cpuPoints: root.querySelector('[data-series="cpu_percent:web"]')?.dataset.pointCount || '0',
                    axisSeconds: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
                  });
                }
              }
              done(results);
            })().catch(error => done({error: String(error?.stack || error)}));
            """
        )
        assert isinstance(cells, list), cells
        assert len(cells) == 26
        assert {cell["concrete"] for cell in cells} == {1, 10, 60, 300}
        for cell in cells:
            assert cell["buckets"] == cell["range"] // cell["concrete"], cell
            assert cell["cpuPoints"] == "1", cell
            assert cell["axisSeconds"] == ("true" if cell["concrete"] == 1 else "false"), cell
        status = client.status()
        assert status["warm"] == {"ready": 26, "total": 26, "percent": 100.0}
        assert status["requests"]["snapshot"] >= 26

        follower_client = stats_client.StatsCurrentClient(socket_path, database)
        exact_request = {
            "range_seconds": 300,
            "resolution": 1,
            "client_id": "browser-current-fixture",
        }
        owner_metadata, owner_body = client.snapshot(exact_request)
        follower_metadata, follower_body = follower_client.snapshot(exact_request)
        assert owner_metadata["cache_generation"] == follower_metadata["cache_generation"]
        assert owner_metadata["source_generation"] == follower_metadata["source_generation"]
        assert owner_body == follower_body

        follower_app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                follower_client,
                client_binding_secret=b"stats-e2e-client-binding-secret",
            ),
        )
        follower_server, follower_thread = start_browser_share_server(
            monkeypatch,
            tmp_path,
            follower_app,
            auth_bypass=True,
        )
        query = (
            "/api/stats-snapshot?range_seconds=300&resolution=1&"
            "client_id=browser-current-fixture"
        )
        with urlopen(
            f"http://127.0.0.1:{http_server.server_address[1]}{query}", timeout=3,
        ) as response:
            owner_http_body = response.read()
        with urlopen(
            f"http://127.0.0.1:{follower_server.server_address[1]}{query}", timeout=3,
        ) as response:
            follower_http_body = response.read()
        assert owner_http_body == follower_http_body
        assert json.loads(owner_http_body)["cache_generation"] == owner_metadata["cache_generation"]
    finally:
        if follower_server is not None and follower_thread is not None:
            stop_browser_share_server(follower_server, follower_thread)
        if http_server is not None and http_thread is not None:
            stop_browser_share_server(http_server, http_thread)
        service.stop_event.set()
        service.work_event.set()
        service_thread.join(timeout=3)
        assert not service_thread.is_alive()


def test_current_stats_one_second_motion_and_sse_delta_do_not_refetch_or_fabricate(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const fixture = window.__statsFixture;
          const root = document.getElementById('stats-root');
          const tokenPoint = () => root.querySelector('[data-series="agent_tokens_per_minute:sol"] [data-series-point]');
          const before = {
            x: Number(tokenPoint().getAttribute('cx')),
            value: Number(tokenPoint().dataset.pointValue),
            count: root.querySelector('[data-series="agent_tokens_per_minute:sol"]').dataset.pointCount,
            snapshots: fixture.snapshotRequests.length,
          };
          await fixture.clock.advance(1000);
          const afterTick = {
            x: Number(tokenPoint().getAttribute('cx')),
            value: Number(tokenPoint().dataset.pointValue),
            count: root.querySelector('[data-series="agent_tokens_per_minute:sol"]').dataset.pointCount,
            snapshots: fixture.snapshotRequests.length,
          };
          fixture.emitCpuDelta(99);
          await Promise.resolve();
          await Promise.resolve();
          const cpuValues = [...root.querySelectorAll('[data-series="cpu_percent:host"] [data-series-point]')].map(point => Number(point.dataset.pointValue));
          done({
            before,
            afterTick,
            afterDelta: {
              cpuLast: cpuValues.at(-1),
              snapshots: fixture.snapshotRequests.length,
              axis: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
              labels: [...root.querySelectorAll('[data-stats-chart="cpu"] text')].map(item => item.textContent),
            },
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["afterTick"]["x"] < result["before"]["x"], result
    assert result["afterTick"]["value"] == result["before"]["value"] == 120, result
    assert result["afterTick"]["count"] == result["before"]["count"] == "1", result
    assert result["afterTick"]["snapshots"] == result["before"]["snapshots"], result
    assert result["afterDelta"]["cpuLast"] == 99, result
    assert result["afterDelta"]["snapshots"] == result["before"]["snapshots"], result
    assert result["afterDelta"]["axis"] == "true", result
    assert any(label.count(":") == 2 for label in result["afterDelta"]["labels"]), result


def test_current_stats_one_second_motion_preserves_sparse_native_cadence_series(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const fixture = window.__statsFixture;
          const root = document.getElementById('stats-root');
          const names = [
            'run_agents',
            'gpu_util_percent:gpu:0',
            'system_memory_used_bytes',
          ];
          const read = name => {
            const series = root.querySelector(`[data-series="${name}"]`);
            const points = [...series.querySelectorAll('[data-series-point]')];
            return {
              count: Number(series.dataset.pointCount),
              x: points.map(point => Number(point.getAttribute('cx'))),
              values: points.map(point => Number(point.dataset.pointValue)),
              sourceCounts: points.map(point => Number(point.dataset.pointSourceCount)),
            };
          };
          const before = Object.fromEntries(names.map(name => [name, read(name)]));
          const generationBefore = fixture.generationEvents.at(-1);
          const generationCountBefore = fixture.generationEvents.length;
          const snapshotsBefore = fixture.snapshotRequests.length;

          await fixture.clock.advance(3000);
          const afterTicks = Object.fromEntries(names.map(name => [name, read(name)]));
          const presentation = {
            generationCount: fixture.generationEvents.length,
            cacheGeneration: fixture.generationEvents.at(-1).cacheGeneration,
            datasetUnchanged: fixture.generationEvents.at(-1).dataset === generationBefore.dataset,
            snapshots: fixture.snapshotRequests.length,
          };

          fixture.emitSparseCadenceDelta();
          await Promise.resolve();
          await Promise.resolve();
          done({
            names,
            before,
            afterTicks,
            presentation,
            generationCountBefore,
            snapshotsBefore,
            afterDelta: {
              series: Object.fromEntries(names.map(name => [name, read(name)])),
              generationCount: fixture.generationEvents.length,
              cacheGeneration: fixture.generationEvents.at(-1).cacheGeneration,
            },
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["generationCountBefore"] == 1, result
    assert result["presentation"]["generationCount"] == 1, result
    assert result["presentation"]["cacheGeneration"] == 1, result
    assert result["presentation"]["datasetUnchanged"] is True, result
    assert result["presentation"]["snapshots"] == result["snapshotsBefore"], result
    shifts = []
    for name in result["names"]:
        before = result["before"][name]
        after_ticks = result["afterTicks"][name]
        assert after_ticks["count"] == before["count"] == 1, result
        assert after_ticks["values"] == before["values"], result
        assert after_ticks["sourceCounts"] == before["sourceCounts"] == [1], result
        assert after_ticks["x"][0] < before["x"][0], result
        shifts.append(after_ticks["x"][0] - before["x"][0])
    assert max(shifts) - min(shifts) < 0.001, result
    assert result["afterDelta"]["generationCount"] == 2, result
    assert result["afterDelta"]["cacheGeneration"] == 2, result
    for name in result["names"]:
        assert result["afterDelta"]["series"][name]["count"] == 2, result
    assert result["afterDelta"]["series"]["run_agents"]["values"] == [2, 3], result
    assert result["afterDelta"]["series"]["gpu_util_percent:gpu:0"]["values"] == [40, 41], result
    assert result["afterDelta"]["series"]["system_memory_used_bytes"]["values"] == [8000000000, 8100000000], result


def _retired_current_stats_touch_pointer_path_pins_dismisses_and_zooms(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_script(
        """
        const root = document.getElementById('stats-root');
        const cpuChart = root.querySelector('[data-stats-chart="cpu"]');
        const initialSvg = cpuChart.querySelector('[data-stats-current-svg]');
        const controls = root.querySelector('[data-stats-current-controls]');
        const initialLabels = [...initialSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);
        const bounds = initialSvg.getBoundingClientRect();
        const firstPoint = initialSvg.querySelector('[data-series-point]');
        const pointClientX = bounds.left + Number(firstPoint.getAttribute('cx')) / 600 * bounds.width;
        const clientY = bounds.top + bounds.height / 2;
        const touchAction = {
          svg: getComputedStyle(initialSvg).touchAction,
          chart: getComputedStyle(cpuChart).touchAction,
          controls: getComputedStyle(controls).touchAction,
        };
        initialSvg.setPointerCapture = () => {};
        initialSvg.releasePointerCapture = () => {};

        const dispatchTouch = (target, type, clientX, pointerId) => {
          const event = new PointerEvent(type, {
            bubbles: true,
            cancelable: true,
            pointerType: 'touch',
            pointerId,
            button: 0,
            clientX,
            clientY,
          });
          target.dispatchEvent(event);
          return event.defaultPrevented;
        };

        const tapDownPrevented = dispatchTouch(initialSvg, 'pointerdown', pointClientX, 31);
        const tapUpPrevented = dispatchTouch(initialSvg, 'pointerup', pointClientX, 31);
        const tooltip = cpuChart.querySelector('[data-stats-current-tooltip]');
        const pinned = {
          hidden: tooltip.hidden,
          text: tooltip.textContent,
          downPrevented: tapDownPrevented,
          upPrevented: tapUpPrevented,
        };

        dispatchTouch(document.body, 'pointerdown', 1, 32);
        const dismissed = tooltip.hidden;
        const controlPointerPrevented = dispatchTouch(
          controls,
          'pointerdown',
          controls.getBoundingClientRect().left + 1,
          33,
        );

        const dragStart = bounds.left + bounds.width * 0.25;
        const dragEnd = bounds.left + bounds.width * 0.75;
        const dragDownPrevented = dispatchTouch(initialSvg, 'pointerdown', dragStart, 34);
        const dragMovePrevented = dispatchTouch(initialSvg, 'pointermove', dragEnd, 34);
        const selectionVisibleDuringDrag = !initialSvg.querySelector('[data-stats-current-selection]').hidden;
        const dragUpPrevented = dispatchTouch(initialSvg, 'pointerup', dragEnd, 34);

        const zoomedSvg = root.querySelector('[data-stats-chart="cpu"] [data-stats-current-svg]');
        const zoomedLabels = [...zoomedSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);
        const reset = root.querySelector('[data-stats-current-zoom-reset]');
        const resetVisible = Boolean(reset && !reset.hidden);
        reset.click();
        const resetSvg = root.querySelector('[data-stats-chart="cpu"] [data-stats-current-svg]');
        const resetLabels = [...resetSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);

        return {
          pinned,
          dismissed,
          touchAction,
          controlPointerPrevented,
          drag: {
            downPrevented: dragDownPrevented,
            movePrevented: dragMovePrevented,
            upPrevented: dragUpPrevented,
            selectionVisibleDuringDrag,
            resetVisible,
            initialLabels,
            zoomedLabels,
            resetLabels,
          },
        };
        """
    )
    assert result["pinned"]["hidden"] is False, result
    assert "cpu percent" in result["pinned"]["text"].lower(), result
    assert result["pinned"]["downPrevented"] is True, result
    assert result["pinned"]["upPrevented"] is True, result
    assert result["dismissed"] is True, result
    assert result["touchAction"]["svg"] == "none", result
    assert result["touchAction"]["chart"] != "none", result
    assert result["touchAction"]["controls"] != "none", result
    assert result["controlPointerPrevented"] is False, result
    assert result["drag"]["downPrevented"] is True, result
    assert result["drag"]["movePrevented"] is True, result
    assert result["drag"]["upPrevented"] is True, result
    assert result["drag"]["selectionVisibleDuringDrag"] is True, result
    assert result["drag"]["resetVisible"] is True, result
    assert result["drag"]["zoomedLabels"] != result["drag"]["initialLabels"], result
    assert result["drag"]["resetLabels"] == result["drag"]["initialLabels"], result


def _retired_current_stats_controls_and_charts_fit_desktop_and_ipad_widths(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    for width, height in ((1280, 800), (768, 1024), (430, 800)):
        browser.set_window_size(width, height)
        metrics = browser.execute_script(
            """
            const root = document.getElementById('stats-root');
            const rootRect = root.getBoundingClientRect();
            const charts = [...root.querySelectorAll('[data-stats-chart]')].map(chart => {
              const rect = chart.getBoundingClientRect();
              return {left: rect.left, right: rect.right, width: rect.width};
            });
            const controls = root.querySelector('.yo-stats-current-controls').getBoundingClientRect();
            return {
              root: {left: rootRect.left, right: rootRect.right, width: rootRect.width},
              charts,
              rendering: {
                charts: charts.length,
                paths: root.querySelectorAll('[data-series] > path').length,
                points: [...root.querySelectorAll('[data-point-count]')]
                  .reduce((total, series) => total + Number(series.dataset.pointCount), 0),
              },
              controls: {left: controls.left, right: controls.right, width: controls.width},
              svgTouchActions: [...root.querySelectorAll('[data-stats-current-svg]')].map(svg => getComputedStyle(svg).touchAction),
              bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            };
            """
        )
        assert metrics["bodyOverflow"] <= 1, (width, metrics)
        assert metrics["controls"]["left"] >= metrics["root"]["left"] - 1, (width, metrics)
        assert metrics["controls"]["right"] <= metrics["root"]["right"] + 1, (width, metrics)
        assert all(chart["left"] >= metrics["root"]["left"] - 1 for chart in metrics["charts"]), (width, metrics)
        assert all(chart["right"] <= metrics["root"]["right"] + 1 for chart in metrics["charts"]), (width, metrics)
        assert metrics["rendering"] == {"charts": 7, "paths": 7, "points": 306}, (width, metrics)
        assert set(metrics["svgTouchActions"]) == {"none"}, (width, metrics)


def _retired_current_cost_summary_opens_an_internal_scroll_modal_and_dismisses_both_ways(browser, tmp_path):
    _load_current_stats(browser, tmp_path, view="cost")
    browser.set_window_size(430, 600)
    summary = browser.execute_script(
        """
        const root = document.getElementById('stats-root');
        return {
          text: root.querySelector('[data-stats-current-cost-summary]').textContent,
          detailsBefore: root.querySelector('[role="dialog"]') !== null,
          buttonText: root.querySelector('[data-stats-current-cost-more]').textContent,
        };
        """
    )
    assert summary["detailsBefore"] is False, summary
    assert summary["buttonText"] == "More Info", summary
    assert "Total estimate: $0.25" in summary["text"], summary
    assert "Total tokens: 1.1K tokens" in summary["text"], summary

    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-more]").click()
    metrics = browser.execute_script(
        """
        const modal = document.querySelector('[role="dialog"]');
        const scroll = modal.querySelector('[data-stats-current-cost-modal-scroll]');
        const rect = modal.getBoundingClientRect();
        scroll.scrollTop = Math.min(120, scroll.scrollHeight - scroll.clientHeight);
        return {
          title: modal.querySelector('h2').textContent,
          text: modal.textContent,
          link: modal.querySelector('a')?.href || '',
          top: rect.top,
          bottom: rect.bottom,
          right: rect.right,
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
          scrollTop: scroll.scrollTop,
          scrollHeight: scroll.scrollHeight,
          clientHeight: scroll.clientHeight,
          bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
        """
    )
    assert metrics["title"].startswith("Cost summary details ·"), metrics
    assert "Model Usages" in metrics["text"] and "By Agent" in metrics["text"], metrics
    assert "What these columns mean" in metrics["text"], metrics
    assert "Reasoning breakdown unavailable" in metrics["text"], metrics
    assert metrics["link"] == "https://example.com/pricing", metrics
    assert 0 <= metrics["top"] < metrics["bottom"] <= metrics["viewportHeight"], metrics
    assert metrics["right"] <= metrics["viewportWidth"] + 1, metrics
    assert metrics["scrollHeight"] > metrics["clientHeight"], metrics
    assert metrics["scrollTop"] > 0, metrics
    assert metrics["bodyOverflow"] <= 1, metrics

    preserved = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const scroll = document.querySelector('[data-stats-current-cost-modal-scroll]');
          const before = scroll.scrollTop;
          await window.__statsFixture.clock.advance(1000);
          done({before, after: document.querySelector('[data-stats-current-cost-modal-scroll]').scrollTop});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert preserved.get("error") is None and preserved["after"] == preserved["before"], preserved

    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-modal-close]").click()
    assert browser.execute_script("return document.querySelector('[role=dialog]') === null;") is True
    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-more]").click()
    browser.execute_script("document.querySelector('[data-stats-current-cost-modal-backdrop]').click();")
    assert browser.execute_script("return document.querySelector('[role=dialog]') === null;") is True

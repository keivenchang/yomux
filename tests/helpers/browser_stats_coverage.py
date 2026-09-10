"""Shared YO!stats browser fixture scenario builders."""

import json
from pathlib import Path

from tests.browser_helpers.browser_layout import page_html
from yolomux_lib.stats_current import resolution as stats_resolution

CURRENT_STATS_SOURCE = "\n".join(Path(f"static_src/js/yolomux/{name}").read_text(encoding="utf-8") for name in ("09_transport_lifecycle.js", "84_stats_current.js"))
CORE_SOURCE = Path("static_src/js/yolomux/10_core_utils.js").read_text(encoding="utf-8")
LIFECYCLE_SCOPE_SOURCE = CORE_SOURCE[
    CORE_SOURCE.index("function createLifecycleScope("):
    CORE_SOURCE.index("\nfunction delegate(", CORE_SOURCE.index("function createLifecycleScope("))
]

def _current_stats_fixture_capabilities() -> dict[str, object]:
    return stats_resolution.wire_capabilities()


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

    const capabilities = window.__statsFixtureCapabilities;

    function seriesValue(value, at) {
      return {value, source_count: 1, first_timestamp: at, last_timestamp: at};
    }

    function fixtureCostDimensions() {
      return {
        input: {tokens: 900, micro_usd: 100000, api_list_micro_usd: 100000},
        cache_read: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
        cache_write_5m: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
        cache_write_1h: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
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
        schema_version: 4,
        total_micro_usd: 250000,
        total_api_list_micro_usd: 250000,
        total_tokens: 1140,
        dimensions,
        priced: {atoms: 2, tokens: 1020},
        unpriced: {atoms: 1, tokens: 120},
        models: [{key: '0123456789abcdef01234567', provider: 'openai', model: 'gpt-5.6-sol', ...attribution}],
        agents: [{key: '89abcdef0123456789abcdef', source: 'codex', label: 'yo8881|0|codex', sources: [{source: 'codex', ...attribution}], ...attribution}],
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
        queueMicrotask(() => {
          if (this.closed) return;
          const parsed = new URL(String(this.url), location.href);
          const rangeSeconds = Number(parsed.searchParams.get('range_seconds'));
          const requestedText = parsed.searchParams.get('resolution');
          const requestedResolution = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
          const row = capabilities.ranges.find(item => item.range_seconds === rangeSeconds);
          const resolutionSeconds = requestedResolution === 'AUTO' ? row.auto_resolution_seconds : requestedResolution;
          const snapshotKey = `${rangeSeconds}/${requestedResolution}`;
          const sinceGeneration = Number(parsed.searchParams.get('since_generation')) || 0;
          const cached = window.__statsFixture.snapshots.get(snapshotKey);
          if (cached && sinceGeneration >= cached.cache_generation) {
            this.emit('ack', {
              cache_generation: cached.cache_generation,
              chunk_count: 1,
              not_modified: true,
              range_seconds: rangeSeconds,
              requested_resolution: requestedResolution,
              resolution_seconds: resolutionSeconds,
            });
            this.emit('ready', {cache_generation: cached.cache_generation, revision: 0});
            return;
          }
          let snapshot = exactSnapshot(rangeSeconds, requestedResolution, resolutionSeconds);
          if (typeof window.__statsFixture.snapshotTransform === 'function') {
            snapshot = window.__statsFixture.snapshotTransform(structuredClone(snapshot));
          }
          window.__statsFixture.lastSnapshot = snapshot;
          window.__statsFixture.snapshots.set(snapshotKey, structuredClone(snapshot));
          window.__statsFixture.snapshotRequests.push({url: parsed.pathname + parsed.search, snapshot});
          this.emit('ack', {
            cache_generation: snapshot.cache_generation,
            chunk_count: 1,
            not_modified: false,
            range_seconds: rangeSeconds,
            requested_resolution: requestedResolution,
            resolution_seconds: resolutionSeconds,
          });
          this.emit('snapshot', snapshot);
          this.emit('ready', {cache_generation: snapshot.cache_generation, revision: 0});
        });
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

    class NetworkFixtureEventSource {
      constructor(url, options) {
        const fixture = window.__statsFixture;
        const source = fixture.openNetworkEventSource(url, options);
        const requestUrl = new URL(String(url), location.href);
        const operation = `sse-init:${requestUrl.pathname}${requestUrl.search}`;
        let settleInitialization;
        const initialization = new Promise(resolve => { settleInitialization = resolve; });
        const settle = () => {
          if (!settleInitialization) return;
          const resolve = settleInitialization;
          settleInitialization = null;
          resolve();
        };
        fixture.eventSources.push(source);
        fixture.trackFinite(operation, initialization);
        source.addEventListener('snapshot', event => {
          const snapshot = JSON.parse(event.data);
          fixture.snapshotRequests.push({url: requestUrl.pathname + requestUrl.search, snapshot});
        });
        for (const name of ['ready', 'pending', 'upgrade_required', 'unavailable', 'error']) {
          source.addEventListener(name, settle);
        }
        const nativeClose = source.close.bind(source);
        source.close = () => {
          settle();
          nativeClose();
        };
        return source;
      }
    }

    window.__statsFixture = {
      capabilities,
      clock: new FixtureClock(),
      cacheGeneration: 0,
      nextFiniteOperationId: 1,
      finiteOperations: new Map(),
      snapshotRequests: [],
      snapshots: new Map(),
      snapshotTransform: null,
      eventSources: [],
      generationEvents: [],
      lastGeneration: null,
      lastSnapshot: null,
      mounted: null,
    };
    window.__statsFixture.openNetworkEventSource = (url, options) => new window.EventSource(url, options);

    window.__statsFixture.trackFinite = (label, promise) => {
      const id = `${window.__statsFixture.nextFiniteOperationId++}:${label}`;
      window.__statsFixture.finiteOperations.set(id, promise);
      return Promise.resolve(promise).finally(() => window.__statsFixture.finiteOperations.delete(id));
    };
    window.__yolomuxFixtureLifecycle = Object.freeze({
      diagnosticMode: 'browser-console',
      operationState() {
        return {pending: [...window.__statsFixture.finiteOperations.keys()].sort()};
      },
    });

    window.__statsFixture.fetch = input => window.__statsFixture.trackFinite(`fetch:${String(input)}`, (async () => {
      const url = new URL(String(input), location.href);
      if (window.__statsNetworkFetch) {
        const response = await window.fetch(input, {credentials: 'same-origin', cache: 'no-store'});
        if (response.status !== 200 || url.pathname !== '/api/stats-capabilities') {
          return response;
        }
        const payload = await response.json();
        window.__statsFixture.capabilities = payload;
        return {status: 200, json: async () => structuredClone(payload)};
      }
      if (url.pathname === '/api/stats-capabilities') return {status: 200, json: async () => capabilities};
      return {status: 404, json: async () => ({})};
    })());

    window.__statsFixture.start = view => window.__statsFixture.trackFinite(`start:${view}`, (async () => {
      const root = document.getElementById('stats-root');
      const mounted = YOLOmuxStatsCurrent.mount(root, {
        view,
        clientId: 'browser-current-fixture',
        savedRange: 300,
        savedResolution: 1,
        fetch: window.__statsFixture.fetch,
        EventSource: window.__statsNetworkFetch ? NetworkFixtureEventSource : FixtureEventSource,
        controllerOptions: {
          clock: window.__statsFixture.clock,
          onGeneration: generation => {
            window.__statsFixture.lastGeneration = generation;
            window.__statsFixture.lastSnapshot = generation;
            window.__statsFixture.generationEvents.push({
              cacheGeneration: generation.cache_generation,
              dataset: JSON.stringify(generation),
            });
          },
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
    })());

    window.__statsFixture.select = (rangeSeconds, requestedResolution) => window.__statsFixture.trackFinite(
      `select:${rangeSeconds}/${requestedResolution}`,
      (async () => {
      const root = document.getElementById('stats-root');
      const range = root.querySelector('[data-stats-current-range]');
      if (Number(range.value) !== rangeSeconds) {
        const beforeRange = window.__statsFixture.generationEvents.length;
        range.value = String(rangeSeconds);
        range.dispatchEvent(new Event('change', {bubbles: true}));
        await window.__statsFixture.clock.advance(0);
        await window.__yolomuxTestWaitFor(
          () => window.__statsFixture.generationEvents.slice(beforeRange).some(item => (
            JSON.parse(item.dataset).range_seconds === rangeSeconds
          )),
          {description: `current stats ${rangeSeconds}/AUTO range generation`}
        );
      }
      const resolution = root.querySelector('[data-stats-current-resolution]');
      if (String(resolution.value) === String(requestedResolution)) return;
      const beforeGeneration = window.__statsFixture.generationEvents.length;
      resolution.value = String(requestedResolution);
      resolution.dispatchEvent(new Event('change', {bubbles: true}));
      await window.__statsFixture.clock.advance(0);
      await window.__yolomuxTestWaitFor(
        () => window.__statsFixture.generationEvents.slice(beforeGeneration).some(item => {
          const generation = JSON.parse(item.dataset);
          return generation.range_seconds === rangeSeconds
            && generation.requested_resolution === requestedResolution;
        }) && root.querySelector('[data-stats-chart="cpu"]'),
        {description: `current stats ${rangeSeconds}/${requestedResolution} generation`}
      );
    })());

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
    fixture_capabilities = json.dumps(_current_stats_fixture_capabilities(), separators=(",", ":"))
    body = f"""
    <main id="stats-shell"><div id="stats-root"></div></main>
    <script>window.openExternalLinkFromEvent = (event, root) => {{ const anchor = event.target?.closest?.('a[href]'); if (!anchor || !root.contains(anchor) || !/^https?:\\/\\//i.test(anchor.href)) return false; const opened = window.open(anchor.href, '_blank', 'noopener,noreferrer'); if (!opened) return false; event.preventDefault(); return true; }};</script>
    <script>eval({json.dumps(LIFECYCLE_SCOPE_SOURCE)});</script>
    <script>eval({json.dumps(CURRENT_STATS_SOURCE)});</script>
    <script>window.__statsFixtureCapabilities = {fixture_capabilities};</script>
    <script>window.__statsNetworkFetch = {str(network_fetch).lower()};</script>
    <script>{setup}</script>
    """
    return page_html(body, extra_css="#stats-shell { width: 100%; min-width: 0; }")


def _write_current_stats_fixture_assets(asset_dir: Path, asset_name: str) -> None:
    """Serve the minimal stats page and every stylesheet dependency from one owner."""

    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / asset_name).write_text(_current_stats_fixture_html(network_fetch=True), encoding="utf-8")
    font_dir = asset_dir / "fonts"
    font_dir.mkdir()
    (font_dir / "yolomux-ui.woff2").write_bytes(
        (Path("static/fonts") / "yolomux-ui.woff2").read_bytes()
    )


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

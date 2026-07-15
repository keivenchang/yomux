// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const {
  assert,
  flushAsyncWork,
  fs,
  jsonResponse,
  loadYolomux,
  runSuites,
  test,
  testAsync,
} = require('./layout_test_helper');

async function runYostatsPerformanceSuite() {
  test('YO!stats holds sparse gauges for display without inventing samples or bridging outages', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000) * 1000;
    const start = now - 60_000;
    const records = Array.from({length: 60}, (_unused, index) => {
      const record = {
        start: (start / 1000) + index,
        duration: 1,
        sequence: index + 1,
        cpu_total_percent: 10,
        cpu_count: 1,
      };
      if (index === 0) Object.assign(record, {
        run_agent_total: 2,
        agent_activity_samples: 1,
        tokens_per_agent_total: 120,
        agent_token_samples: 1,
        cost_summary: {
          total_micro_usd: 7,
          known_micro_usd: 7,
          lower_micro_usd: 7,
          upper_micro_usd: 7,
          priced_count: 1,
          complete: true,
          components: [{provider: 'openai', model: 'gpt-test', direction: 'output', modality: 'text', cache_role: 'none', unit: 'tokens', quantity: 120, micro_usd: 7}],
        },
        host_metrics: {
          system_memory_used_total_bytes: 300,
          system_memory_capacity_total_bytes: 600,
          system_memory_count: 3,
          gpu_devices: {'gpu:0': {label: 'GPU 0', util_total_percent: 75, memory_used_total_bytes: 50, memory_capacity_total_bytes: 100, samples: 1}},
        },
      });
      if (index === 30) record.disconnected_ms = 1000;
      if (index === 40) record.host_metrics = {
        system_memory_used_total_bytes: 240,
        system_memory_capacity_total_bytes: 480,
        system_memory_count: 2,
      };
      return record;
    });
    api.clearJsDebugEventsForTest();
    api.setDebugGraphRangeForTest(60, {render: false});
    assert.equal(api.debugGraphBucketSummaryForTest(now).rangeSeconds, 5 * 60, 'retired 1m preferences normalize to the first 5m range');
    api.setDebugGraphResolutionOverrideForTest(1);
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    const buckets = api.debugGraphDisplayBucketsForTest(now);
    const byKey = new Map(api.debugGraphSeriesDataForTest(now).map(series => [series.key, series]));
    const memory = byKey.get('systemMemory');
    const gpu = byKey.get('gpu:gpuUtil:gpu:0');
    const status = byKey.get('workingAgents');
    const tokens = byKey.get('tokensPerAgent');

    assert.equal(memory.samples, 2, 'two durable memory observations remain two samples');
    assert.equal(memory.displaySamples, 50, `the minute gauge paints only its bounded pre-outage and post-recovery slots (got ${memory.displaySamples})`);
    assert.equal(memory.provenanceValues[20].held, true, 'a held point is explicitly presentation-only');
    assert.equal(memory.provenanceValues[20].sampleTimeMs, start, 'held points retain the real source timestamp');
    assert.equal(memory.provenanceValues[20].sampleCount, 3, 'held points retain the real aggregate sample count');
    assert.match(api.debugGraphHeldProvenanceTextForTest([memory.provenanceValues[20]]), /^\u21b3 .+ · n=3$/, 'held hover text visibly identifies the real source time and sample count');
    assert.equal(api.debugGraphHeldProvenanceTextForTest([memory.provenanceValues[0]]), '', 'ordinary observed values do not get a misleading held-source annotation');
    assert.equal(memory.provenanceValues[30], null, 'an explicit outage clears the hold before its nominal expiry');
    assert.equal(memory.hasDataValues.slice(31, 40).some(Boolean), false, 'the display does not bridge the post-outage gap');
    assert.equal(memory.provenanceValues[45].sampleTimeMs, start + 40_000, 'a recovered observation becomes the new provenance owner');
    assert.equal(memory.provenanceValues[45].sampleCount, 2, 'recovery preserves its own count instead of inheriting the earlier sample');

    assert.equal(gpu.samples, 1, 'one GPU observation remains one sample');
    assert.equal(gpu.displaySamples, 10, 'a ten-second GPU gauge paints exactly ten one-second slots');
    assert.equal(gpu.hasDataValues[9], true);
    assert.equal(gpu.hasDataValues[10], false, 'GPU hold expires at its named ten-second boundary');
    assert.equal(status.samples, 1, 'Agent Status is not expanded into synthetic one-second samples');
    assert.equal(status.displaySamples, 1, 'Agent Status retains its native observation while its chart keeps ten-second bars');
    assert.equal(tokens.samples, 1, 'token observations are not sample-and-held');
    assert.equal(tokens.displaySamples, 1, 'token rates and totals remain unprojected');
    assert.equal(buckets.reduce((total, bucket) => total + Number(bucket.hostMetrics?.systemMemoryCount || 0), 0), 5, 'display projection does not mutate durable gauge counts');
    assert.equal(api.debugGraphCostSummaryForTest(buckets).totalMicroUsd, 7, 'display projection does not multiply cost totals');
    assert.equal(api.jsDebugGraphChartGroupsForTest().find(group => group.key === 'activity').bucketSeconds, 10, 'Agent Status remains a ten-second bar chart');

    api.setDebugGraphChartVisibleForTest('memory', true);
    const memoryHtml = api.debugGraphInnerHtmlForTest(now).match(/<section[^>]*data-js-debug-chart="memory"[\s\S]*?<\/section>/)?.[0] || '';
    const memoryLines = (memoryHtml.match(/data-js-debug-series="systemMemory"/g) || []).length;
    assert.equal(memoryLines, 2, `the memory gauge renders separate pre-outage and recovered runs (got ${memoryLines})`);
    assert.match(memoryHtml, /data-js-debug-series="systemMemory"[^>]*data-js-debug-series-segment="1"/, 'the recovered gauge is marked as a separate segment');
    const source = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
    assert.match(source, /function debugGraphHoverProvenanceAtTime[\s\S]*series\.provenanceValues[\s\S]*data-js-debug-hover-provenance/, 'hover diagnostics consume the same preserved sample provenance');
    assert.match(source, /data-js-debug-hover-source[\s\S]*source\.textContent = sourceText[\s\S]*source\.hidden = !sourceText/, 'held-source provenance is visible in the graph hover instead of only a private attribute');

    const uninterrupted = loadYolomux('?debug=1&sessions=debug', ['1']);
    uninterrupted.clearJsDebugEventsForTest();
    uninterrupted.setDebugGraphRangeForTest(60, {render: false});
    uninterrupted.setDebugGraphResolutionOverrideForTest(1);
    uninterrupted.debugGraphApplyServerHistoryForTest({
      sequence: 60,
      records: Array.from({length: 60}, (_unused, index) => ({
        start: (start / 1000) + index,
        duration: 1,
        sequence: index + 1,
        cpu_total_percent: 10,
        cpu_count: 1,
        ...(index === 0 ? {host_metrics: {system_memory_used_total_bytes: 100, system_memory_count: 1}} : {}),
      })),
    });
    const uninterruptedMemory = uninterrupted.debugGraphSeriesDataForTest(now).find(series => series.key === 'systemMemory');
    assert.equal(uninterruptedMemory.samples, 1, 'the uninterrupted minute projection still reports one real sample');
    assert.equal(uninterruptedMemory.displaySamples, 60, 'one minute gauge sample may paint exactly sixty one-second display slots');
  });

  test('System reports every stats sampler family without replacing aggregate health', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const nowSeconds = 2_000_000;
    const html = api.debugSystemStatsSamplerCardHtmlForTest([{
      service: 'statsd',
      sampler_alive: true,
      sampler_last_cycle_seconds: 0.25,
      sampler_late_cycles: 4,
      sampler_missed_cycles: 2,
      history_requests: 20,
      history_cache_hits: 18,
      history_profile: {assemble_ms: 3, returned_records: 42, source_records: 43},
      sampler_families: {
        cpu: {cadence_seconds: 1, alive: true, running: true, attempts: 100, successes: 99, failures: 1, late_cycles: 2, missed_cycles: 3, last_runtime_seconds: 0.012, last_success_at: nowSeconds - 2, last_failure: 'timeout'},
        agent_status: {cadence_seconds: 10, alive: true, attempts: 20, successes: 20, last_runtime_ms: 8, last_success_at: nowSeconds - 4},
        gpu: {cadence_seconds: 10, alive: false, attempts: 10, successes: 8, failures: 2, late_cycles: 1, missed_cycles: 1, last_runtime_seconds: 0.4, last_success_at: nowSeconds - 12, last_failure: 'GPU unavailable'},
        system_memory: {cadence_ms: 60_000, alive: true, attempts: 5, successes: 5, last_runtime_seconds: 0.02, last_success_age_seconds: 30},
        agent_tokens: {interval_seconds: 60, alive: true, attempts: 6, successes: 6, last_runtime_seconds: 0.03, last_success_at: nowSeconds - 30},
      },
    }], nowSeconds);
    assert.match(html, /YO!stats sampler/, 'the existing aggregate sampler card remains');
    assert.match(html, /Last cycle[\s\S]*Late \/ missed deadlines[\s\S]*History cache hit rate/, 'legacy aggregate sampler health remains visible');
    for (const family of ['cpu', 'agent_status', 'gpu', 'system_memory', 'agent_tokens']) {
      assert.match(html, new RegExp(`data-js-debug-sampler-family="${family}"`), `${family} has a compact status row`);
    }
    assert.match(html, /data-js-debug-sampler-family="cpu"[\s\S]*100 \/ 99 \/ 1[\s\S]*2 \/ 3[\s\S]*timeout/, 'family rows expose attempt, result, deadline, and failure details');
    assert.match(html, /data-js-debug-sampler-family="gpu"[\s\S]*GPU unavailable/, 'a failing sampler exposes its latest failure');
    assert.match(html, /data-js-debug-sampler-family="system_memory"[\s\S]*60s/, 'millisecond cadence telemetry is normalized for display');
    assert.match(html, /data-js-debug-sampler-family="agent_tokens"[\s\S]*60s/, 'minute token cadence is explicit');
    assert.match(html, /js-debug-system-card--wide[\s\S]*data-js-debug-sampler-families[\s\S]*<table/, 'the family list uses the existing compact scrollable table layout');
    const source = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
    assert.match(source, /function refreshDebugSystemViews[\s\S]*const scrollTop = view\.scrollTop[\s\S]*restoreElementScrollPosition\(view, scrollTop, scrollLeft\)/, 'System refresh preserves vertical scroll while sampler telemetry updates');
    assert.match(source, /function refreshDebugSystemViews[\s\S]*const scrollLeft = view\.scrollLeft[\s\S]*restoreElementScrollPosition\(view, scrollTop, scrollLeft\)/, 'System refresh preserves horizontal scroll while sampler telemetry updates');
  });

  test('System exposes the sustained server CPU budget and bounded compute owners', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const html = api.debugSystemCpuBudgetCardHtmlForTest({
      status: 'warning',
      current_percent: 42.4,
      budget_percent: 30,
      sustained_seconds: 360,
      sustained_budget_seconds: 300,
      top_consumers: [{role: 'metadata', surface: 'refresh', compute_ms_total: 1234.56}],
    });
    assert.match(html, /CPU budget/);
    assert.match(html, /data-js-debug-cpu-budget="warning"/);
    assert.match(html, /42\.4% \/ 30%/);
    assert.match(html, /360s \/ 300s/);
    assert.match(html, /metadata:refresh 1,234\.6ms/);
  });

  test('YO!cost reuses the selected big-range cost aggregate instead of recomputing every render', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const makeComponent = (bucketIndex, rowIndex) => ({
      key: `class-${rowIndex}`,
      provider: 'openai',
      model: rowIndex % 2 ? 'gpt-5.6-terra' : 'gpt-5.6-sol',
      effort: rowIndex % 3 ? 'high' : 'low',
      pricing_profile: '',
      service_tier: '',
      direction: rowIndex % 2 ? 'input' : 'output',
      modality: 'text',
      cache_role: rowIndex === 1 ? 'read' : 'none',
      unit: 'tokens',
      catalog_revision: 7,
      source_url: 'https://platform.openai.com/pricing',
      effective_from: '2026-07-01T00:00:00Z',
      rate_usd: String(rowIndex + 1),
      rate_scale: 1_000_000,
      quantity: 100 + bucketIndex + rowIndex,
      token_quantity: 100 + bucketIndex + rowIndex,
      micro_usd: 1000 + rowIndex,
      lower_micro_usd: 1000 + rowIndex,
      upper_micro_usd: 1000 + rowIndex,
      input_micro_usd: rowIndex % 2 ? 1000 + rowIndex : 0,
      cache_micro_usd: rowIndex === 1 ? 1000 + rowIndex : 0,
      output_micro_usd: rowIndex % 2 ? 0 : 1000 + rowIndex,
      other_micro_usd: 0,
    });
    const buckets = Array.from({length: 2700}, (_unused, bucketIndex) => {
      const components = Array.from({length: 12}, (_component, rowIndex) => makeComponent(bucketIndex, rowIndex));
      return {
        start: bucketIndex * 60,
        duration: 60,
        sequence: bucketIndex + 1,
        costSummary: {
          totalMicroUsd: 12000,
          knownMicroUsd: 12000,
          lowerMicroUsd: 12000,
          upperMicroUsd: 12000,
          pricedCount: components.length,
          complete: true,
          unpricedCount: 0,
          components,
          models: components,
          sources: components.map((component, rowIndex) => ({
            ...component,
            tmux_key: 'build|0|codex',
            tmux_label: 'build:0',
            tmux_session: 'build',
            tmux_window: '0',
            root_thread_id: 'root',
            agent_thread_id: `agent-${rowIndex}`,
          })),
          tmuxWindows: [],
        },
      };
    });
    const coldStarted = performance.now();
    const cold = api.debugGraphCostSummaryForTest(buckets);
    const coldMs = performance.now() - coldStarted;
    const warmStarted = performance.now();
    const warm = api.debugGraphCostSummaryForTest(buckets);
    const warmMs = performance.now() - warmStarted;
    assert.equal(cold.totalMicroUsd, 2700 * 12000, 'cold aggregate still sums the full selected cost range');
    assert.equal(warm, cold, 'warm aggregate reuses the cached selected-range summary object');
    assert.ok(coldMs < 1200, `cold 24h-style YO!cost aggregate took ${coldMs.toFixed(1)}ms`);
    assert.ok(warmMs < 80, `warm 24h-style YO!cost aggregate recomputed instead of using the cache (${warmMs.toFixed(1)}ms)`);
  });

  test('YO!stats median render stays under 300ms for a full 24-hour mixed-resolution fixture', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const tenMinuteMs = 10 * 60_000;
    const now = Math.ceil(Date.now() / tenMinuteMs) * tenMinuteMs;
    assert.equal(now % tenMinuteMs, 0, 'fixture is anchored to the compaction boundary that exposed disconnect-only buckets as client traffic');
    const gapStart = now - (6 * 60 * 60 * 1000);
    const gapEnd = gapStart + (5 * 60 * 1000);
    const disconnectedStart = gapStart + (2 * 60 * 1000);
    const records = [];
    const appendTier = (startMs, count, durationSeconds) => {
      for (let index = 0; index < count; index += 1) {
        const bucketStartMs = startMs + (index * durationSeconds * 1000);
        const hasClientData = bucketStartMs < gapStart || bucketStartMs >= gapEnd;
        const record = {
          start: bucketStartMs / 1000,
          duration: durationSeconds,
          sequence: records.length + 1,
          cpu_total_percent: 12,
          cpu_count: 1,
          system_cpu_total_percent: 24,
          system_cpu_count: 1,
        };
        if (hasClientData) Object.assign(record, {api_count: 2, sse_count: 1, latency_total_ms: 25, latency_count: 1, bandwidth_bytes: 2048});
        if (bucketStartMs === disconnectedStart) record.disconnected_ms = durationSeconds * 1000;
        records.push(record);
      }
    };
    appendTier(now - (24 * 60 * 60 * 1000), 1320, 60);
    appendTier(now - (2 * 60 * 60 * 1000), 360, 10);
    appendTier(now - (60 * 60 * 1000), 3600, 1);
    assert.equal(records.length, 5280, 'fixture has the 1320x60s + 360x10s + 3600x1s mixed-resolution stress shape');
    api.setDebugGraphRangeForTest(24 * 60 * 60, {render: false});
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    api.debugGraphInnerHtmlForTest(now);
    const measuredMs = [];
    let html = '';
    for (let sample = 0; sample < 5; sample += 1) {
      const started = performance.now();
      html = api.debugGraphInnerHtmlForTest(now);
      measuredMs.push(performance.now() - started);
    }
    const sortedMs = [...measuredMs].sort((left, right) => left - right);
    const medianMs = sortedMs[Math.floor(sortedMs.length / 2)];
    const chartHtml = key => html.match(new RegExp(`<section[^>]*data-js-debug-chart="${key}"[\\s\\S]*?<\\/section>`))?.[0] || '';
    for (const key of ['latency', 'count', 'bandwidth']) {
      const noDataCount = (chartHtml(key).match(/data-js-debug-no-data-range="/g) || []).length;
      assert.ok(noDataCount >= 2, `${key} keeps every missing segment around the known disconnect and graduated tier boundaries (got ${noDataCount})`);
    }
    const sampleText = measuredMs.map(value => value.toFixed(1)).join(', ');
    assert.ok(medianMs < 300, `24-hour graph HTML median took ${medianMs.toFixed(1)}ms; samples=[${sampleText}]ms`);
  });

  test('CONTRACT: the client request-shape owner reproduces every shared golden byte-for-byte', () => {
    // The python mirror (tests/browser_helpers/stats_request_shapes.py) asserts the SAME
    // goldens (tests/test_stats_request_shapes.py). If jsDebugStatsSampleQuery changes,
    // regenerate tests/fixtures/stats_request_shapes.json — both languages fail until the
    // owner, the mirror, and the goldens agree. This exists because a diagnosis probe once
    // hand-rolled a request without token_resolution and validated the wrong serve path.
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const goldens = JSON.parse(fs.readFileSync('tests/fixtures/stats_request_shapes.json', 'utf8'));
    assert.ok(goldens.cases.length >= 11, 'the golden corpus covers every range plus prefetch and backoff shapes');
    for (const testCase of goldens.cases) {
      assert.equal(api.jsDebugStatsSampleQueryForTest(testCase.params), testCase.query, testCase.name);
    }
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal((source.match(/\/api\/stats-sample\?/g) || []).length, 1, 'exactly one stats-sample query construction exists: the owner');
    assert.equal((source.match(/return `\/api\/stats-sample\?\$\{parts\.join/g) || []).length, 1, 'and it is the parts-joining owner, not a hand-built string');
  });

  test('MANIFEST: one frozen family table owns every YO!stats alias, cadence, and chart mapping in both languages', () => {
    // Phase 1 of the stability plan: the recurring YO!stats bug class was a flag or
    // special case on one family silently affecting another (2026-07-14: host metrics
    // gated on the token-slimming flags blanked Server Load / System memory / GPU at
    // every range >= 4h). Both sides now READ one manifest — yolomux_lib/stats_families.py
    // and its client mirror jsDebugStatsFamilyManifest — and this test pins the mirrors
    // against each other and bans the retired per-family special cases from returning.
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const manifest = api.jsDebugStatsFamilyManifestForTest();
    const expected = {
      cpu: {aliases: ['server', 'raw', 'buckets'], cadenceSeconds: 1, chartGroups: ['cpu']},
      service_load: {aliases: [], cadenceSeconds: 10, chartGroups: []},
      agent_status: {aliases: ['status'], cadenceSeconds: 10, chartGroups: ['activity']},
      agent_tokens: {aliases: ['tokens'], cadenceSeconds: 10, chartGroups: ['agentTokens']},
      cost: {aliases: ['cost_atoms', 'usage_atoms'], cadenceSeconds: 10, chartGroups: []},
      gpu: {aliases: ['gpu_metrics'], cadenceSeconds: 10, chartGroups: ['gpuUtil', 'gpuMemory']},
      system_memory: {aliases: ['memory'], cadenceSeconds: 60, chartGroups: ['memory']},
    };
    assert.deepStrictEqual([...Object.keys(manifest)].sort(), Object.keys(expected).sort(), 'the client mirror carries exactly the charted families');
    const familyManifestSource = fs.readFileSync('yolomux_lib/stats_families.py', 'utf8');
    for (const [family, entry] of Object.entries(expected)) {
      assert.deepStrictEqual([...manifest[family].legacyAliases], entry.aliases, `${family}: legacy aliases`);
      assert.deepStrictEqual([...manifest[family].chartGroups], entry.chartGroups, `${family}: chart groups`);
      assert.equal(manifest[family].cadenceSeconds, entry.cadenceSeconds, `${family}: true sampler cadence`);
      assert.match(familyManifestSource, new RegExp(`name="${family}"`), `${family}: exists in the python manifest`);
      assert.match(familyManifestSource, new RegExp(`cadence_seconds=${entry.cadenceSeconds}[,\\n]`), `${family}: python manifest carries the same cadence somewhere`);
      for (const alias of entry.aliases) {
        assert.ok(familyManifestSource.includes(`"${alias}"`), `${family}: alias ${alias} exists in the python manifest`);
      }
    }
    // The chart->family mapping resolves through the manifest, including the
    // dimension-dependent modelTokens chart.
    const groupFor = key => api.jsDebugHistoryCoverageFamilyForGroupForTest({key});
    assert.equal(groupFor('activity'), 'agent_status');
    assert.equal(groupFor('memory'), 'system_memory');
    assert.equal(groupFor('gpuUtil'), 'gpu');
    assert.equal(groupFor('gpuMemory'), 'gpu');
    assert.equal(groupFor('cpu'), 'cpu');
    assert.equal(groupFor('agentTokens'), 'agent_tokens');
    assert.equal(groupFor('serversLoad'), '', 'serversLoad keeps its pre-manifest no-coverage-overlay behavior');
    api.setDebugGraphModelTokenDimensionForTest('output');
    assert.equal(groupFor('modelTokens'), 'agent_tokens');
    api.setDebugGraphModelTokenDimensionForTest('all');
    assert.equal(groupFor('modelTokens'), 'cost');

    // GREP-PROOF: the retired per-family special cases stay dead outside the manifest owners.
    const clientSource = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
    const intervalsFn = clientSource.match(/function jsDebugHistoryCoverageIntervalsForFamily\([\s\S]*?\n\}/)[0];
    assert.ok(intervalsFn.includes('jsDebugStatsFamilyManifest'), 'the coverage-interval lookup reads the manifest');
    assert.equal(/'server'|'buckets'|'gpu_metrics'|'usage_atoms'|'memory'|'tokens'|'status'/.test(intervalsFn), false, 'inline alias arrays are gone from the coverage lookup');
    const familyForGroupFn = clientSource.match(/function jsDebugHistoryCoverageFamilyForGroup\([\s\S]*?\n\}/)[0];
    assert.equal(/'agent_status'|'system_memory'|'agent_tokens'|'gpu'|'cpu'/.test(familyForGroupFn), false, 'the family if-chain is gone from the chart-group mapping');
    const statsdSource = fs.readFileSync('yolomux_lib/statsd.py', 'utf8');
    assert.equal(/include_agent_tokens or field not in/.test(statsdSource), false, 'token slimming is manifest wire-group filtering, not per-field flag branching');
    assert.equal(/merge_agent_details|merge_cost_summary/.test(statsdSource), false, 'the cross-family merge booleans are retired for manifest field groups');
    assert.match(statsdSource, /def _merge_bucket\([\s\S]{0,220}field_groups/, '_merge_bucket selects manifest field groups');
    assert.match(statsdSource, /def _record_from_bucket\([\s\S]{0,220}field_groups/, '_record_from_bucket selects manifest field groups');
    assert.equal(/coverage_families = \(/.test(statsdSource), false, 'coverage companion fan-out reads the manifest, not inline family tuples');
    const storeSource = fs.readFileSync('yolomux_lib/local_services/stats_store.py', 'utf8');
    assert.equal(/STATS_COVERAGE_FAMILIES = \(/.test(storeSource), false, 'the store derives coverage families from the manifest');
    assert.equal(/"system_memory": 60/.test(storeSource), false, 'the store derives legacy cadences from the manifest');
  });

  test('host charts (Server Load, System memory, GPU) render at the 4h / 120s view when their data is present', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000 / 120) * 120 * 1000;
    const nowSec = now / 1000;
    const records = [];
    for (let t = nowSec - (4 * 3600); t <= nowSec; t += 120) {
      records.push({
        start: t, duration: 120, sequence: records.length + 1, cpu_total_percent: 20, cpu_count: 1,
        host_metrics: {
          system_memory_used_total_bytes: 48e9, system_memory_capacity_total_bytes: 64e9, system_memory_count: 1,
          service_load: {
            'web:8881': {label: 'web', cpu_total_percent: 15, cpu_samples: 12, rss_total_bytes: 2e8, rss_samples: 12},
            idled: {label: 'idled', cpu_total_percent: 0, cpu_samples: 12, rss_total_bytes: 1e8, rss_samples: 12},
          },
          // Apple GPU: real memory + a genuine ZERO utilization with a sample count.
          // Server semantics: *_total_* fields are SUMS across `samples` (client divides).
          gpu_devices: {'gpu:0': {label: 'GPU 0 (Apple M4 Pro)', util_total_percent: 0, memory_used_total_bytes: 2.9e9 * 14, memory_capacity_total_bytes: 51.5e9 * 14, samples: 14}},
        },
      });
    }
    api.setDebugGraphRangeForTest(4 * 3600, {render: false});
    for (const key of ['serversLoad', 'memory', 'gpuUtil', 'gpuMemory']) api.setDebugGraphChartVisibleForTest(key, true);
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    const html = api.debugGraphInnerHtmlForTest(now);
    const series = api.debugGraphSeriesDataForTest(now);
    const seriesFor = fragment => series.filter(item => item.key.includes(fragment) && item.displaySamples > 0);
    // System memory: one continuous line (not an empty axis).
    assert.equal((html.match(/data-js-debug-series="systemMemory"/g) || []).length, 1, 'System memory draws its line at 4h/120s');
    // Server Load: both services present as drawn series (idle-zero counts as data).
    assert.ok(seriesFor('serviceLoad:web:8881').length === 1, 'Server Load renders the active web service');
    assert.ok(seriesFor('serviceLoad:idled').length === 1, 'a zero-CPU service is still real data');
    // GPU: a zero-util device with samples is NOT "unavailable"; memory and util both render.
    assert.equal(/data-js-debug-gpu-unavailable="gpuUtil"/.test(html), false, 'a zero-util GPU with samples is not shown as unavailable');
    assert.equal(/data-js-debug-gpu-unavailable="gpuMemory"/.test(html), false, 'GPU memory with real data is not shown as unavailable');
    assert.ok(seriesFor('gpu:gpuMemory:gpu:0').length === 1, 'GPU memory renders its device series');
    const gpuMem = series.find(item => item.key === 'gpu:gpuMemory:gpu:0');
    assert.ok(gpuMem.max > 2e9 && gpuMem.max < 4e9, `GPU memory divides the per-sample total (used ~2.9GB, got max=${gpuMem.max})`);
  });

  test('an unavailable GPU chart explains itself precisely, never the ambiguous generic "None"', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000 / 60) * 60 * 1000;
    const nowSec = now / 1000;
    api.setDebugGraphRangeForTest(15 * 60, {render: false});
    for (const key of ['gpuUtil', 'gpuMemory']) api.setDebugGraphChartVisibleForTest(key, true);
    // Host with NO GPU telemetry anywhere: cpu-only history.
    api.debugGraphApplyServerHistoryForTest({sequence: 60, records: Array.from({length: 60}, (_u, i) => ({start: nowSec - 900 + (i * 15), duration: 15, sequence: i + 1, cpu_total_percent: 20 * 15, cpu_count: 15}))});
    let html = api.debugGraphInnerHtmlForTest(now);
    assert.match(html, /data-js-debug-gpu-unavailable="gpuUtil"[^>]*>GPU telemetry is not available on this host</, 'a GPU-less host names the real reason');
    assert.equal(html.includes('>None<'), false, 'the ambiguous generic None label is retired for GPU charts');
    // Same cache but GPU samples exist OUTSIDE the current window (older span only).
    api.debugGraphApplyServerHistoryForTest({sequence: 61, records: [{
      start: nowSec - (2 * 3600), duration: 600, sequence: 61, cpu_total_percent: 20 * 600, cpu_count: 600,
      host_metrics: {gpu_devices: {'gpu:0': {label: 'GPU 0', util_total_percent: 10, memory_used_total_bytes: 1e9, memory_capacity_total_bytes: 5e10, samples: 1}}},
    }]});
    html = api.debugGraphInnerHtmlForTest(now);
    assert.match(html, /data-js-debug-gpu-unavailable="gpuUtil"[^>]*>No GPU samples in this time window</, 'window-scoped absence is distinguished from a GPU-less host');
  });

  test('INVARIANT SWEEP: every range x every resolution renders every family with data — never a silent empty chart', () => {
    // The anti-regression guard for the whole YO!stats matrix. YO!stats regressions have
    // repeatedly come from one (range x resolution x family) cell breaking while the cells
    // that had point-tests stayed green. This sweep renders EVERY range option at AUTO plus
    // EVERY offered resolution override over one realistic graduated-cadence 24h fixture and
    // asserts the product rule: if a family has ANY data in the visible range it is DRAWN as
    // one continuous stroke (coarser recording interpolates; it never blanks), and no chart
    // is ever a silent axes-only shell. If a change blanks any cell, this fails the gate.
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000 / 600) * 600 * 1000;
    const nowSec = now / 1000;
    const records = [];
    const hostMetricsFor = durationSec => ({
      system_memory_used_total_bytes: 48e9 * Math.max(1, durationSec / 60),
      system_memory_capacity_total_bytes: 64e9 * Math.max(1, durationSec / 60),
      system_memory_count: Math.max(1, durationSec / 60),
      service_load: {
        'web:8881': {label: 'web', cpu_total_percent: 12 * Math.max(1, durationSec / 10), cpu_samples: Math.max(1, durationSec / 10), rss_total_bytes: 2e8, rss_samples: Math.max(1, durationSec / 10)},
        statsd: {label: 'statsd', cpu_total_percent: 0, cpu_samples: Math.max(1, durationSec / 10), rss_total_bytes: 1e8, rss_samples: Math.max(1, durationSec / 10)},
      },
      gpu_devices: {'gpu:0': {label: 'GPU 0 (Apple M4 Pro)', util_total_percent: 5 * Math.max(1, durationSec / 10), memory_used_total_bytes: 2.9e9 * Math.max(1, durationSec / 10), memory_capacity_total_bytes: 51.5e9 * Math.max(1, durationSec / 10), samples: Math.max(1, durationSec / 10)}},
    });
    // Graduated cadence matching the real retention tiers (newest last, ~2700 records).
    const tiers = [
      {fromAgo: 24 * 3600, toAgo: 12 * 3600, step: 600},
      {fromAgo: 12 * 3600, toAgo: 8 * 3600, step: 300},
      {fromAgo: 8 * 3600, toAgo: 4 * 3600, step: 120},
      {fromAgo: 4 * 3600, toAgo: 2 * 3600, step: 60},
      {fromAgo: 2 * 3600, toAgo: 30 * 60, step: 10},
      {fromAgo: 30 * 60, toAgo: 0, step: 1},
    ];
    // The killer regression case (screenshots 006/007): an EMPTY-but-covered middle —
    // fine data at both edges, no local buckets in between, and no recorded coverage gap
    // (e.g. after a wide->narrow switch before the middle refetches). Every display over
    // it must interpolate one continuous line across, never blank or split the stroke.
    const coarseHoleStartAgo = 20 * 60;
    const coarseHoleEndAgo = 10 * 60;
    for (const tier of tiers) {
      for (let t = nowSec - tier.fromAgo; t < nowSec - tier.toAgo; t += tier.step) {
        const ago = nowSec - t;
        if (ago <= coarseHoleStartAgo && ago > coarseHoleEndAgo) continue;
        const record = {start: t, duration: tier.step, sequence: records.length + 1, cpu_total_percent: 20 * tier.step, cpu_count: tier.step, system_cpu_total_percent: 30 * tier.step, system_cpu_count: tier.step};
        // Fine 1s buckets carry each family only at its real sampling cadence.
        if (tier.step >= 10 || t % 10 === 0) {
          const host = hostMetricsFor(tier.step);
          if (tier.step < 60 && t % 60 !== 0) {
            delete host.system_memory_used_total_bytes;
            delete host.system_memory_capacity_total_bytes;
            delete host.system_memory_count;
          }
          record.host_metrics = host;
        }
        records.push(record);
      }
    }
    for (const key of ['serversLoad', 'memory', 'gpuUtil', 'gpuMemory']) api.setDebugGraphChartVisibleForTest(key, true);
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    const failures = [];
    for (const range of api.jsDebugGraphRangeOptionsForTest()) {
      api.setDebugGraphRangeForTest(range.seconds, {render: false});
      const resolutions = [0, ...api.debugGraphAvailableResolutionChoicesForTest(now)];
      for (const resolution of resolutions) {
        api.setDebugGraphResolutionOverrideForTest(resolution);
        const html = api.debugGraphInnerHtmlForTest(now);
        const cell = `${range.label}@${resolution === 0 ? 'AUTO' : `${resolution}s`}`;
        const lineCount = key => (html.match(new RegExp(`data-js-debug-series="${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`, 'g')) || []).length;
        const check = (condition, what) => { if (!condition) failures.push(`${cell}: ${what}`); };
        check(!html.includes('js-debug-graph--empty'), 'graph rendered as empty shell');
        check(lineCount('systemCpu') === 1, `systemCpu drew ${lineCount('systemCpu')} segments (want 1 continuous)`);
        check(lineCount('systemMemory') === 1, `systemMemory drew ${lineCount('systemMemory')} segments (want 1 continuous)`);
        check(lineCount('serviceLoad:web:8881') === 1, 'Server Load web service line missing/broken');
        check(lineCount('serviceLoad:statsd') === 1, 'a zero-CPU service line missing (zero is data)');
        check(!/data-js-debug-gpu-unavailable=/.test(html), 'GPU chart claims unavailable despite data');
        check(lineCount('gpu:gpuMemory:gpu:0') >= 1, 'GPU memory device line missing');
      }
    }
    api.setDebugGraphResolutionOverrideForTest(0);
    assert.deepEqual(failures, [], `invariant violations:\n${failures.join('\n')}`);
  });

  function seedShortCurrentView(api, nowSec) {
    // Establish a ~1h current view with real buckets, like the first landed sample.
    const shortStart = nowSec - 3600;
    const records = Array.from({length: 60}, (_unused, index) => ({
      start: shortStart + (index * 60), duration: 60, sequence: index + 1, cpu_total_percent: 10, cpu_count: 1,
    }));
    api.setDebugGraphRangeForTest(3600, {render: false});
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    api.setJsDebugStatsFirstSampleReceivedForTest(true);
  }

  function fullRetentionHistory(nowSec) {
    // A coarse 24h history covering every range, like the server's per-span tiers return.
    const wideStart = nowSec - (24 * 3600);
    const records = Array.from({length: 48}, (_unused, index) => ({
      start: wideStart + (index * 1800), duration: 1800, sequence: index + 1, cpu_total_percent: 12, cpu_count: 1,
    }));
    return {
      sequence: records.length,
      records,
      coverage: {
        resolution_seconds: 600,
        requested_start: wideStart,
        requested_end: nowSec,
        intervals: [{start: wideStart, end: nowSec, resolution_seconds: 600}],
        store_intervals: {},
      },
    };
  }

  function wideViewReachMinutes(api, nowMs) {
    api.setDebugGraphRangeForTest(24 * 3600, {render: false});
    const buckets = api.debugGraphDisplayBucketsForTest(nowMs);
    return buckets.length ? Math.round((nowMs - Math.min(...buckets.map(bucket => bucket.startMs))) / 60000) : 0;
  }

  await testAsync('YO!stats full-retention prefetch silently fills the cache so a wide range renders stale without touching readiness', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const nowSec = Math.floor(Date.now() / 1000);
    const nowMs = nowSec * 1000;
    seedShortCurrentView(api, nowSec);

    // Before prefetch: switching to 24h only reaches back over the ~1h that is cached.
    const wideBeforeMin = wideViewReachMinutes(api, nowMs);
    api.setDebugGraphRangeForTest(3600, {render: false});
    const readinessBefore = api.jsDebugHistoryReadinessForTest();

    let requests = 0;
    let requestUrl = null;
    api.setFetchForTest((url) => {
      if (String(url).includes('/api/stats-sample')) {
        requests += 1;
        requestUrl = String(url);
        return Promise.resolve(jsonResponse({
          ok: true, time: nowSec, pid: 1, uptime_seconds: 10, cpu_percent: 12, rss_bytes: 1e8,
          history: fullRetentionHistory(nowSec),
        }));
      }
      return Promise.resolve(jsonResponse({}));
    });

    const ok = await api.prefetchJsDebugHistoryFullRetentionForTest();
    await flushAsyncWork();

    assert.equal(ok, true, 'prefetch resolves true after applying the full-retention response');
    assert.equal(requests, 1, 'prefetch issues exactly one stats-sample request');
    const historyStart = Number(new URL(requestUrl, 'https://local').searchParams.get('history_start'));
    const historyEnd = Number(new URL(requestUrl, 'https://local').searchParams.get('history_end'));
    assert.equal(historyEnd, 0, 'prefetch requests through the live edge (history_end=0)');
    assert.ok(nowSec - historyStart >= 23 * 3600, `prefetch spans the full retention window (got ${nowSec - historyStart}s)`);

    // Silent: the current view's readiness phase and generation are untouched.
    const readinessAfter = api.jsDebugHistoryReadinessForTest();
    assert.equal(readinessAfter.phase, readinessBefore.phase, 'prefetch does not change the readiness phase');
    assert.equal(readinessAfter.generation, readinessBefore.generation, 'prefetch does not bump the readiness generation');

    // Cache-fill: the 24h view now renders back ~full retention from cache, no poll.
    const wideAfterMin = wideViewReachMinutes(api, nowMs);
    assert.ok(wideBeforeMin <= 120, `before prefetch the 24h view only reached ~1h (got ${wideBeforeMin}m)`);
    assert.ok(wideAfterMin >= 20 * 60, `after prefetch the 24h view reaches ~full retention (got ${wideAfterMin}m)`);
  });

  test('graph line stays continuous across a covered coarse span and breaks only at a genuine no-data range', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const t0 = 1_000_000_000_000;
    // Two data clusters ~280s apart at 10s cadence (a covered-but-coarse span between them).
    const times = [t0, t0 + 10000, t0 + 20000, t0 + 300000, t0 + 310000];
    const values = [10, 12, 11, 20, 21];
    const has = [true, true, true, true, true];
    const durs = [10000, 10000, 10000, 10000, 10000];
    assert.equal(
      api.debugGraphPolylineSegmentCountForTest(values, times, has, durs, []),
      1, 'no genuine gap => one continuous (interpolated) line across the coarse span');
    const genuineGap = [{startMs: t0 + 30000, endMs: t0 + 295000}];
    assert.equal(
      api.debugGraphPolylineSegmentCountForTest(values, times, has, durs, genuineGap),
      2, 'a real recorded hole between the clusters breaks the line honestly');
  });

  test('CPU renders one continuous line across an empty-but-covered middle (wide->narrow), and breaks at a real coverage gap', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000) * 1000;
    const nowSec = now / 1000;
    const records = [];
    for (let t = nowSec - 900; t < nowSec - 720; t += 1) records.push({start: t, duration: 1, sequence: records.length + 1, system_cpu_total_percent: 30, system_cpu_count: 1, cpu_total_percent: 40, cpu_count: 1});
    for (let t = nowSec - 180; t <= nowSec; t += 1) records.push({start: t, duration: 1, sequence: records.length + 1, system_cpu_total_percent: 33, system_cpu_count: 1, cpu_total_percent: 45, cpu_count: 1});
    api.setDebugGraphRangeForTest(900, {render: false});
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});

    // No coverage info: the empty middle is treated as covered -> ONE continuous line.
    let html = api.debugGraphInnerHtmlForTest(now);
    assert.equal((html.match(/data-js-debug-series="systemCpu"/g) || []).length, 1, 'empty-but-covered middle draws one continuous CPU line');

    // A genuine coverage gap over the middle -> the line breaks and a red no-data band paints.
    api.setJsDebugHistoryCoverageForTest(
      'cpu',
      [[nowSec - 900, nowSec - 700], [nowSec - 170, nowSec]],
      [[nowSec - 900, nowSec]],
    );
    html = api.debugGraphInnerHtmlForTest(now);
    assert.equal((html.match(/data-js-debug-series="systemCpu"/g) || []).length, 2, 'a real coverage gap breaks the CPU line into two honest segments');
    assert.ok((html.match(/data-js-debug-history-coverage-family="cpu"/g) || []).length >= 1, 'the real gap still paints a red no-data band');
  });

  test('Sparse 60s System-memory gauge draws one continuous line across the covered range and splits at a real gap', () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const now = Math.floor(Date.now() / 1000) * 1000;
    const nowSec = now / 1000;
    const records = [];
    // Memory sampled once per 60s over 15m (sparse gauge), plus dense cpu so the chart renders.
    // No samples inside the 600s..300s window (a real recorded hole has no data).
    for (let t = nowSec - 900; t <= nowSec; t += 1) {
      const rec = {start: t, duration: 1, sequence: records.length + 1, cpu_total_percent: 20, cpu_count: 1};
      if (t % 60 === 0 && !(t > nowSec - 600 && t < nowSec - 300)) rec.host_metrics = {system_memory_used_total_bytes: 48e9, system_memory_capacity_total_bytes: 64e9, system_memory_count: 1};
      records.push(rec);
    }
    api.setDebugGraphRangeForTest(900, {render: false});
    api.setDebugGraphChartVisibleForTest('memory', true);
    api.debugGraphApplyServerHistoryForTest({sequence: records.length, records});
    const memorySection = () => api.debugGraphInnerHtmlForTest(now).match(/<section[^>]*data-js-debug-chart="memory"[\s\S]*?<\/section>/)?.[0] || '';
    const memoryLines = () => (memorySection().match(/data-js-debug-series="systemMemory"/g) || []).length;
    // Sparse samples span the covered edges; the once-per-60s gaps interpolate into one line.
    assert.equal(memoryLines(), 1, 'a once-per-60s gauge draws one continuous line across the covered range');
    api.setJsDebugHistoryCoverageForTest(
      'system_memory',
      [[nowSec - 900, nowSec - 600], [nowSec - 300, nowSec]],
      [[nowSec - 900, nowSec]],
    );
    assert.equal(memoryLines(), 2, 'a real recorded hole splits the memory line into two honest segments (held value never leaks in)');
  });

  await testAsync('YO!stats prefetch cadence: waits for the first sample, fires once, then throttles', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const nowSec = Math.floor(Date.now() / 1000);
    let requests = 0;
    api.setFetchForTest((url) => {
      if (String(url).includes('/api/stats-sample')) requests += 1;
      return Promise.resolve(jsonResponse({
        ok: true, time: nowSec, pid: 1, uptime_seconds: 10, cpu_percent: 12, rss_bytes: 1e8,
        history: fullRetentionHistory(nowSec),
      }));
    });

    // Before the first sample lands, the cadence gate does nothing.
    api.maybePrefetchJsDebugHistoryForTest();
    await flushAsyncWork();
    assert.equal(requests, 0, 'no prefetch before the first sample is received');
    assert.equal(api.jsDebugHistoryPrefetchStateForTest().didInitial, false, 'didInitial stays false pre-first-sample');

    // After the first sample, the initial prefetch fires exactly once.
    api.setJsDebugStatsFirstSampleReceivedForTest(true);
    api.maybePrefetchJsDebugHistoryForTest();
    await flushAsyncWork();
    assert.equal(requests, 1, 'the first armed cadence tick fires the initial prefetch');
    assert.equal(api.jsDebugHistoryPrefetchStateForTest().didInitial, true, 'didInitial latches after the initial prefetch');

    // A subsequent immediate tick is throttled by the several-minute cadence.
    api.maybePrefetchJsDebugHistoryForTest();
    await flushAsyncWork();
    assert.equal(requests, 1, 'an immediate second tick is throttled, not a per-tick refetch');
  });
}

module.exports = {runYostatsPerformanceSuite};

if (require.main === module) {
  runSuites([runYostatsPerformanceSuite]);
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const {
  assert,
  fs,
  loadYolomux,
  runSuites,
  test,
} = require('./layout_test_helper');

function runYostatsPerformanceSuite() {
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

    const uninterrupted = loadYolomux('?debug=1&sessions=debug', ['1']);
    uninterrupted.clearJsDebugEventsForTest();
    uninterrupted.setDebugGraphRangeForTest(60, {render: false});
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
}

module.exports = {runYostatsPerformanceSuite};

if (require.main === module) {
  runSuites([runYostatsPerformanceSuite]);
}

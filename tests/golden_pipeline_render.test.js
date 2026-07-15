// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
// Node consumer half of the GOLDEN PIPELINE test (tests/test_stats_golden_pipeline.py).
// The python half ingests REAL sampler-shaped payloads through the real statsd service
// (durable store, coverage epochs, an outage) and encodes per-range history responses
// through the contract-tested request shapes. This half feeds those REAL responses to
// the REAL client fetch machinery (pollJsDebugStatsSample -> apply -> coverage ->
// readiness) and asserts the rendered HTML per range: every family draws, and the
// genuine outage stays an honest gap. This is the test that catches layer-boundary
// drops that per-layer suites structurally cannot (e.g. the 2026-07-14 host-metrics
// stripping, which was invisible to both the store tests and the render sweep).

const {assert, flushAsyncWork, jsonResponse, loadYolomux, runSuites, testAsync} = require('./layout_test_helper');
const fs = require('fs');

const payloadPath = process.argv[2];
if (!payloadPath) {
  console.error('usage: node tests/golden_pipeline_render.test.js <payloads.json>');
  process.exit(2);
}
const fixture = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));

async function runGoldenPipelineSuite() {
  await testAsync('golden pipeline: real store -> real encode -> real client poll -> honest render per range', async () => {
    const api = loadYolomux('?debug=1&sessions=debug', ['1']);
    const nowMs = Number(fixture.nowSeconds) * 1000;
    api.setFetchForTest(url => {
      const text = String(url);
      if (!text.includes('/api/stats-sample?')) return Promise.resolve(jsonResponse({}));
      const requestedStart = Number(new URL(text, 'https://localhost').searchParams.get('history_start'));
      const requestedSpan = Math.max(1, Number(fixture.nowSeconds) - requestedStart);
      const key = Object.keys(fixture.histories).sort(
        (left, right) => Math.abs(Number(left) - requestedSpan) - Math.abs(Number(right) - requestedSpan)
      )[0];
      return Promise.resolve(jsonResponse({
        ok: true, time: Number(fixture.nowSeconds), pid: 1, uptime_seconds: 600,
        cpu_percent: 20, system_cpu_percent: 30, rss_bytes: 1e8,
        history: fixture.histories[key],
      }));
    });
    api.setDateNowForTest ? api.setDateNowForTest(nowMs) : (Date.now = () => nowMs);
    for (const key of ['serversLoad', 'memory', 'gpuUtil', 'gpuMemory']) api.setDebugGraphChartVisibleForTest(key, true);

    const failures = [];
    for (const rangeSeconds of fixture.ranges) {
      api.stopJsDebugStatsPollingForTest ? api.stopJsDebugStatsPollingForTest() : null;
      api.clearJsDebugGraphDataForTest();
      api.resetJsDebugHistoryReadinessForTest();
      api.setDebugGraphRangeForTest(rangeSeconds, {render: false});
      await api.pollJsDebugStatsSampleForTest({forceGraphRefresh: true});
      for (let i = 0; i < 10; i += 1) await flushAsyncWork();
      const readiness = api.jsDebugHistoryReadinessForTest();
      const html = api.debugGraphInnerHtmlForTest(nowMs);
      const cell = `${rangeSeconds}s`;
      const check = (condition, what) => { if (!condition) failures.push(`${cell}: ${what}`); };
      const lineCount = key => (html.match(new RegExp(`data-js-debug-series="${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`, 'g')) || []).length;
      check(readiness.phase === 'ready', `readiness ${readiness.phase} (${readiness.error || 'no error'})`);
      check(!html.includes('js-debug-graph--empty'), 'graph rendered as an empty shell');
      check(lineCount('systemCpu') >= 1, 'systemCpu missing');
      check(lineCount('systemMemory') >= 1, 'systemMemory missing');
      check(lineCount('serviceLoad:web:8881') >= 1, 'Server Load web service missing');
      check(!/data-js-debug-gpu-unavailable=/.test(html), 'GPU claims unavailable despite data');
      check(lineCount('gpu:gpuMemory:gpu:0') >= 1, 'GPU memory device missing');
      // Agent-status bars render (bar series, not polylines).
      check((html.match(/data-js-debug-bar-series="(workingAgents|idleAgents)"/g) || []).length >= 1, 'agent status bars missing');
      // The genuine mid-range outage must stay HONEST for ranges that include it: a red
      // coverage band, and no line bridging drawn through it would be caught by the band's
      // absence (the client derives gaps from the response's per-family coverage).
      if (rangeSeconds >= fixture.outageVisibleFromRangeSeconds) {
        check((html.match(/data-js-debug-history-no-data-range/g) || []).length >= 1, 'genuine outage lost its red no-data band');
      }
    }
    assert.deepEqual(failures, [], `golden pipeline violations:\n${failures.join('\n')}`);
  });
}

module.exports = {runGoldenPipelineSuite};

if (require.main === module) {
  runSuites([runGoldenPipelineSuite]);
}

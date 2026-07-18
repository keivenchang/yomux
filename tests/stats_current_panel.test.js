// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const retiredMount = fs.readFileSync('static_src/js/yolomux/83_stats_panel.js', 'utf8');
const css = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
let passed = 0;
let failed = 0;
const pending = [];

function test(name, body) {
  try {
    body();
    passed += 1;
  } catch (error) {
    failed += 1;
    console.error(`FAIL: ${name}`);
    console.error(error.stack || error);
  }
}

function testAsync(name, body) {
  pending.push(Promise.resolve().then(body).then(() => {
    passed += 1;
  }).catch(error => {
    failed += 1;
    console.error(`FAIL: ${name}`);
    console.error(error.stack || error);
  }));
}

function sourceFunction(name, nextName) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`\nfunction ${nextName}(`, start);
  assert.notEqual(start, -1, `${name} exists`);
  assert.notEqual(end, -1, `${nextName} follows ${name}`);
  return source.slice(start, end);
}

test('the established Graph API-SSE System Logs shell remains the renderer owner', () => {
  assert.match(source, /function debugPanelHtml\(\)/);
  assert.match(source, /debugSubTabButtonHtml\('graph'/);
  assert.match(source, /debugSubTabButtonHtml\('events'/);
  assert.match(source, /debugSubTabButtonHtml\('system'/);
  assert.match(source, /debugSubTabButtonHtml\('logs'/);
  assert.match(source, /function debugGraphInnerHtml\(/);
  assert.match(source, /function debugSystemInnerHtml\(/);
  assert.match(source, /function debugLogsInnerHtml\(/);
  assert.doesNotMatch(source, /data-stats-current-view/);
  assert.doesNotMatch(retiredMount, /YOLOmuxStatsCurrent\.mount|data-current-stats-mount/);
});

test('Services omits the duplicated web process while CPU names it clearly', () => {
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const serviceSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  assert.match(cpuSource, /yolomux\.py \(web\) :\$\{legacyWebPort\[1\]\}/);
  assert.match(serviceSource, /if \(key === 'web'\) continue;/);
});

test('all established chart controls and semantic renderers remain present', () => {
  for (const label of ['CPU', 'Servers load', 'Sys mem', 'Agent #', 'Agent tokens', 'Model tokens', 'Cost', 'GPU', 'GPU mem', 'Latency', 'API&SSE', 'Bandwidth']) {
    assert.ok(source.includes(`toggleLabelEn: '${label}'`), label);
  }
  for (const token of ['data-js-debug-range-slider', 'data-js-debug-resolution-override', 'data-js-debug-chart-layout', 'data-js-debug-chart-close']) {
    assert.ok(source.includes(token), token);
  }
  assert.match(css, /\.js-debug-subtabs/);
  assert.match(css, /\.js-debug-chart/);
  assert.match(css, /\.js-debug-system-grid/);
  assert.match(css, /\.js-debug-logs-view/);
});

test('the System sampler renders stalled usage as an explicit bounded warning', () => {
  const functionText = sourceFunction('debugSystemStatsSamplerCardHtml', 'debugSystemCpuBudgetCardHtml');
  const context = {result: null};
  vm.runInNewContext(`
    function esc(value) { return String(value); }
    function debugSystemNumber(value) { return Number.isFinite(Number(value)) ? String(value) : 'N/A'; }
    function debugGraphTerseTimeText(value) { return String(value) + 'ms'; }
    function debugSystemRowsHtml() { return '<dl></dl>'; }
    function debugSystemSamplerFamiliesHtml() { return '<table></table>'; }
    function debugSystemCardHtml(_title, body) { return body; }
    ${functionText}
    result = debugSystemStatsSamplerCardHtml([{service: 'statsd', usage: {
      quarantined_conflict_count: 2,
      health: {state: 'warning', reason: 'transcripts are advancing but usage atoms are stale', last_accepted_atom_age_seconds: 125},
    }}], 1000);
  `, context);
  assert.match(context.result, /data-js-debug-usage-health="warning"/);
  assert.match(context.result, /role="alert"/);
  assert.match(context.result, /transcripts are advancing but usage atoms are stale/);
  assert.match(context.result, /Quarantined conflicts 2/);
  assert.doesNotMatch(context.result, /payload|quantity|token values/);
});

test('the exact current snapshot feeds the established renderer without legacy APIs', () => {
  assert.match(source, /\/api\/stats-snapshot\?range_seconds=/);
  assert.match(source, /function applyJsDebugCurrentSnapshot\(/);
  assert.match(source, /debugGraphApplyServerRecord\(jsDebugCurrentBucketRecord/);
  assert.doesNotMatch(source, /fetchJsDebugStatsJson\(jsDebugStatsSampleQuery/);
});

test('browser observations use the current writer and a calm bounded cadence', () => {
  assert.match(source, /const jsDebugCurrentObservationBatchDelayMs = 10_000/);
  assert.match(source, /'\/api\/stats-observations'/);
  assert.match(bootstrapSource, /const statsWriterFence = \(\(\) =>/);
  assert.match(bootstrapSource, /Object\.freeze\(\{protocolVersion, schemaGeneration\}\)/);
  assert.match(source, /protocol_version: statsWriterFence\.protocolVersion/);
  assert.match(source, /schema_generation: statsWriterFence\.schemaGeneration/);
  assert.doesNotMatch(source, /jsDebugCurrentObservationProtocol|jsDebugCurrentObservationSchema|= 23/);
  assert.match(source, /Math\.min\(jsDebugCurrentObservationRetryMaxMs, state\.retryMs \* 2\)/);
  assert.match(source, /recordJsDebugClientHealthObservation\(latencyMs, bandwidthBytes, sampleTimeMs\)/);
  assert.match(source, /type: 'heartbeat'/);
  assert.match(source, /const jsDebugClientHealthPollMs = 10_000/);
  assert.match(source, /resetRuntimeInterval\('debug-client-health',[\s\S]*jsDebugClientHealthPollMs\)/);
});

testAsync('browser observation uploads acknowledge, back off, stop on 426, and restart with a new page epoch', async () => {
  const uploaderSource = source.slice(
    source.indexOf('function queueJsDebugCurrentObservation('),
    source.indexOf('\nfunction recordApiDebugResponseBytesForGraph('),
  );
  const makeUploader = (fence, epoch) => {
    const requests = [];
    const outcomes = [];
    const timers = [];
    const context = {
      requests,
      outcomes,
      setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; },
      apiFetchJsonQuiet: async (url, options) => {
        requests.push({url, body: JSON.parse(options.body)});
        const outcome = outcomes.shift() || {ok: true};
        if (outcome.status) throw outcome;
        return outcome;
      },
      jsDebugStatsClientIdForRequest: () => 'client-1',
    };
    vm.runInNewContext(`
      const statsWriterFence = ${JSON.stringify(fence)};
      const jsDebugCurrentObservationBatchDelayMs = 10000;
      const jsDebugCurrentObservationRetryMaxMs = 300000;
      const jsDebugCurrentObservationState = {queue: [], keys: new Set(), nextHealthId: 1, timer: null, inFlight: false, retryMs: 10000, stopped: statsWriterFence === null, epoch: ${JSON.stringify(epoch)}};
      ${uploaderSource}
      globalThis.testApi = {state: jsDebugCurrentObservationState, queue: queueJsDebugCurrentObservation, flush: flushJsDebugCurrentObservations};
    `, context);
    return {...context, timers, api: context.testApi};
  };
  const event = {type: 'api', ts: '2026-07-17T12:00:00.000Z', durationMs: 12, requestBytes: 10, responseBytes: 20};
  const current = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-1');
  current.api.queue('page-1:api:1', event);
  current.api.queue('page-1:api:1', event);
  assert.equal(current.api.state.queue.length, 1, 'stable event keys deduplicate before upload');
  current.api.state.timer = null;
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 0, 'durable acknowledgement removes accepted entries');
  assert.deepEqual(current.requests[0].body.protocol_version, 24);
  assert.deepEqual(current.requests[0].body.schema_generation, 5);

  current.api.queue('page-1:api:2', event);
  current.api.state.timer = null;
  current.outcomes.push({status: 503});
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 1, 'transient failure retains the queue');
  assert.equal(current.api.state.retryMs, 20000, 'transient failure doubles the bounded retry delay');
  assert.equal(current.timers.at(-1).delay, 10000, 'first retry waits one batch interval');

  current.api.state.timer = null;
  current.outcomes.push({status: 426});
  await current.api.flush();
  assert.equal(current.api.state.stopped, true, 'upgrade-required is terminal for the loaded page');
  assert.equal(current.api.state.queue.length, 0, 'terminal rejection drops stale queued writes');

  const reloaded = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-2');
  reloaded.api.queue('page-2:api:1', event);
  reloaded.api.state.timer = null;
  await reloaded.api.flush();
  assert.equal(reloaded.api.state.stopped, false, 'a new page epoch starts a fresh current uploader');
  assert.equal(reloaded.requests.length, 1);

  const missingFence = makeUploader(null, 'page-old');
  missingFence.api.queue('page-old:api:1', event);
  await missingFence.api.flush();
  assert.equal(missingFence.api.state.stopped, true);
  assert.equal(missingFence.requests.length, 0, 'an invalid bootstrap fence never reaches the write endpoint');
});

test('the established renderer consumes the protocol-v2 exact stream', () => {
  assert.match(source, /globalThis\.YOLOmuxStatsCurrent\.createBrowserClient/);
  assert.match(source, /onGeneration\(snapshot\)[\s\S]*applyJsDebugCurrentSnapshot\(snapshot/);
  assert.match(source, /client\.select\(selection\.rangeSeconds, selection\.resolution\)/);
  assert.match(source, /onState\(state, error\)[\s\S]*requestedRangeSeconds: liveSelection\.rangeSeconds[\s\S]*error\?\.reason/);
  assert.match(source, /initialHistoryOverlayOwnsLoading \|\| jsDebugHistoryReadiness\.phase === 'error'/);
  assert.match(source, /function retryJsDebugHistory\(\)[\s\S]*client\.retry\(\)/);
  assert.match(source, /if \(jsDebugGraphExactResolutionEnabled\) return false;[\s\S]*function clearJsDebugGraphData/);
});

test('an exact range-resolution switch retains the rendered buckets behind one request owner', () => {
  const requestSource = sourceFunction('requestJsDebugHistoryForCurrentDomain', 'setDebugGraphRange');
  const applySource = sourceFunction('applyJsDebugCurrentSnapshot', 'scheduleJsDebugStatsHistoryFlush');
  const pollOwnerSource = sourceFunction('armJsDebugStatsPolling', 'pollJsDebugStatsOnInterval');
  assert.match(requestSource, /beginJsDebugHistoryReadiness[\s\S]*syncJsDebugCurrentStatsClient\(\{select: true\}\)/);
  assert.doesNotMatch(requestSource, /clearJsDebugGraphData/);
  assert.match(applySource, /clearJsDebugGraphData\(\)[\s\S]*debugGraphApplyServerRecord/);
  assert.match(pollOwnerSource, /if \(jsDebugGraphExactResolutionEnabled && syncJsDebugCurrentStatsClient\(\)\) return;/);
  assert.doesNotMatch(pollOwnerSource, /pollJsDebugStatsSample[\s\S]*syncJsDebugCurrentStatsClient/);

  const context = {result: null};
  vm.runInNewContext(`
    const jsDebugGraphBuckets = new Map([['old-60s', {durationMs: 60000}]]);
    const jsDebugGraphExactResolutionEnabled = true;
    const calls = [];
    function jsDebugStatsPanelVisible() { return true; }
    function ensureJsDebugCurrentStatsClient() {
      return {controller: () => ({
        selection: () => ({range_seconds: 7200, resolution: 60}),
        generation: () => ({cache_generation: 7}),
      })};
    }
    function jsDebugCurrentStatsSelection() { return {rangeSeconds: 7200, resolution: 300}; }
    function debugGraphDomain() { return {startMs: 1000000, endMs: 8200000}; }
    function jsDebugRequestedHistoryResolutionSeconds() { return 300; }
    function beginJsDebugHistoryReadiness(start, options) { calls.push({kind: 'loading', start, options}); }
    function syncJsDebugCurrentStatsClient(options) { calls.push({kind: 'request', options}); return true; }
    ${requestSource}
    const requested = requestJsDebugHistoryForCurrentDomain();
    result = {requested, bucketCount: jsDebugGraphBuckets.size, bucketDurationMs: jsDebugGraphBuckets.get('old-60s').durationMs, calls};
  `, context);
  assert.equal(context.result.requested, true);
  assert.equal(context.result.bucketCount, 1, 'the old rendered generation remains while 300s is pending');
  assert.equal(context.result.bucketDurationMs, 60000);
  assert.deepEqual([...context.result.calls.map(call => call.kind)], ['loading', 'request']);
  assert.equal(context.result.calls[1].options.select, true);
});

test('the retained YO!cost adapter and totals preserve marginal and API-list prices', () => {
  const adapterSource = [
    sourceFunction('jsDebugCurrentCostDimensionRows', 'jsDebugCurrentCostSummary'),
    sourceFunction('debugGraphAgentDisplayLabel', 'debugGraphCostModelAgentKind'),
    sourceFunction('jsDebugCurrentCostSummary', 'jsDebugCurrentModelComponent'),
    sourceFunction('debugGraphCostAggregateRowInto', 'debugGraphCostAggregateValues'),
    sourceFunction('debugGraphCostAggregateValues', 'debugGraphCostAggregateRows'),
    sourceFunction('debugGraphCostAggregateRows', 'debugGraphCostSummarySignature'),
  ].join('\n');
  const adapterContext = {result: null};
  vm.runInNewContext(`
    ${adapterSource}
    const dimensions = {
      input: {tokens: 600, micro_usd: 0, api_list_micro_usd: 300000},
      cache_read: {tokens: 300, micro_usd: 0, api_list_micro_usd: 60000},
      cache_write: {tokens: 100, micro_usd: 0, api_list_micro_usd: 40000},
      output: {tokens: 200, micro_usd: 0, api_list_micro_usd: 200000},
      other: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
    };
    const summary = jsDebugCurrentCostSummary({
      total_micro_usd: 0,
      total_api_list_micro_usd: 600000,
      priced: {atoms: 1, tokens: 1200},
      unpriced: {atoms: 0, tokens: 0},
      dimensions,
      models: [{provider: 'openai', model: 'gpt', total_tokens: 1200, total_micro_usd: 0, total_api_list_micro_usd: 600000, dimensions}],
      agents: [
        {key: 'agent-one', source: 'codex', label: 'yo8881|0|codex', total_tokens: 1200, total_micro_usd: 0, total_api_list_micro_usd: 600000, dimensions},
        {key: 'agent-two', source: 'codex', label: 'yo8881|1|codex', total_tokens: 300, total_micro_usd: 0, total_api_list_micro_usd: 150000, dimensions},
      ],
      evidence: [{tokens: 200, micro_usd: 0, api_list_micro_usd: 200000}],
      catalog_revision: 3,
    });
    const DEBUG_GRAPH_COST_SUBTOTAL_FIELDS = Object.freeze(['micro_usd', 'api_list_micro_usd']);
    const DEBUG_GRAPH_COST_TOKEN_FIELDS = Object.freeze(['token_quantity']);
    const DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS = Object.freeze(['tmux_key', 'tmux_label', 'agent_kind', 'source']);
    const debugGraphCostInteger = value => Math.max(0, Number(value) || 0);
    const debugGraphCostMicroUsd = row => debugGraphCostInteger(row?.micro_usd);
    const grouped = debugGraphCostAggregateRows(summary.sources, DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS);
    result = {summary, grouped};
  `, adapterContext);
  const adapted = adapterContext.result.summary;
  assert.equal(adapted.total_micro_usd, 0);
  assert.equal(adapted.api_list_micro_usd, 600000);
  assert.equal(adapted.models[0].api_list_micro_usd, 600000);
  assert.equal(adapted.sources[0].api_list_micro_usd, 600000);
  assert.equal(adapted.components[0].api_list_micro_usd, 200000);
  assert.equal(adapted.components[0].micro_usd, 0);
  assert.equal(adapted.models[0].cache_api_list_micro_usd, 100000);
  assert.deepEqual([...adapted.sources.map(row => row.tmux_key)], ['agent-one', 'agent-two']);
  assert.deepEqual([...adapted.sources.map(row => row.label)], ['yo8881|0|codex', 'yo8881|1|codex']);
  assert.equal(adapterContext.result.grouped.length, 2, 'distinct agent keys survive cost aggregation');
  assert.equal(adapterContext.result.grouped.reduce((sum, row) => sum + row.token_quantity, 0), 1500);

  const labelContext = {result: null};
  vm.runInNewContext(`
    ${sourceFunction('debugGraphAgentDisplayLabel', 'debugGraphCostModelAgentKind')}
    result = {
      first: debugGraphAgentDisplayLabel('claude-bg:-Users-keivenc-projects-yolomux.dev8881:123456789abc:deadbeef'),
      second: debugGraphAgentDisplayLabel('claude-bg:-Users-keivenc-projects-yolomux.dev8881:abcdef012345:feedface'),
    };
  `, labelContext);
  assert.match(labelContext.result.first, /^claude-bg:/);
  assert.notEqual(labelContext.result.first, labelContext.result.second);
  assert.ok(labelContext.result.first.length <= 64);
  assert.doesNotMatch(labelContext.result.first, /123456789abc/);
  assert.match(sourceFunction('debugGraphLegendHtml', 'debugGraphLegendSwatchHtml'), /debugGraphExplainAttrs\(series\.fullLabel \|\| series\.label/);

  const priceContext = {
    result: null,
    debugGraphCostInteger: value => Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : 0,
    debugGraphCostText: (_key, fallback) => fallback,
    debugGraphCostUsdText: value => `$${(Number(value) / 1000000).toFixed(2)}`,
    esc: value => String(value),
  };
  vm.runInNewContext(`
    ${sourceFunction('debugGraphCostPricePairText', 'debugGraphCostPricePairHtml')}
    ${sourceFunction('debugGraphCostPricePairHtml', 'debugGraphCostBreakdownItems')}
    result = {
      subscription: debugGraphCostPricePairText(0, 600000),
      defaultProfile: debugGraphCostPricePairText(600000, 600000),
      html: debugGraphCostPricePairHtml(0, 600000),
    };
  `, priceContext);
  assert.equal(priceContext.result.subscription, 'Marginal $0.00 · At API list prices $0.60');
  assert.equal(priceContext.result.defaultProfile, 'At API list prices $0.60');
  assert.match(priceContext.result.html, /Marginal \$0\.00[\s\S]*At API list prices \$0\.60/);
  assert.match(sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml'), /grandTotalDual[\s\S]*grandTotalApiList/);
  assert.match(sourceFunction('debugGraphCostReportHtml', 'debugGraphCostSummaryHtml'), /debugGraphCostPricePairText\(summary\.totalMicroUsd, summary\.apiListMicroUsd\)/);
});

test('same-range resolution replacement is not mislabeled as older history', () => {
  const functionText = source.slice(
    source.indexOf('function beginJsDebugHistoryReadiness('),
    source.indexOf('\nfunction jsDebugHistoryRequestIsCurrent('),
  );
  const context = {
    result: null,
    jsDebugGraphRangeSeconds: 7200,
    jsDebugHistoryReadiness: {generation: 1, requestedRangeSeconds: 7200, loadedStartSeconds: 1000},
    performanceNow: () => 10,
    setJsDebugHistoryReadiness: (_phase, updates) => updates,
    recordJsDebugStatsDiagnostic: () => {},
  };
  vm.runInNewContext(`${functionText}\nresult = beginJsDebugHistoryReadiness(970, {requestedEndSeconds: 8170, requestedResolutionSeconds: 300});`, context);
  assert.equal(context.result.reason, 'initial');

  context.jsDebugGraphRangeSeconds = 57600;
  vm.runInNewContext(`result = beginJsDebugHistoryReadiness(0, {requestedEndSeconds: 58600, requestedResolutionSeconds: 300});`, context);
  assert.equal(context.result.reason, 'older');
});

test('active touch charts preserve vertical scrolling and arm only deliberate zoom gestures', () => {
  assert.match(css, /\.js-debug-line-chart\s*\{[\s\S]*?touch-action:\s*pan-y;/);
  const gestureSource = [
    sourceFunction('debugGraphPointerRatioFromRect', 'debugGraphPointerRatioForEvent'),
    sourceFunction('debugGraphPointerRatioForEvent', 'debugGraphSetInteractionLines'),
    sourceFunction('debugGraphSelectionRatioForEvent', 'clearDebugGraphTouchCandidate'),
    sourceFunction('clearDebugGraphTouchCandidate', 'debugGraphTouchCandidateDecision'),
    sourceFunction('debugGraphTouchCandidateDecision', 'startDebugGraphSelection'),
    sourceFunction('startDebugGraphSelection', 'handleDebugGraphPointerDown'),
    sourceFunction('handleDebugGraphPointerDown', 'handleDebugGraphPointerMove'),
    sourceFunction('handleDebugGraphPointerMove', 'handleDebugGraphPointerUp'),
    sourceFunction('handleDebugGraphPointerUp', 'cancelDebugGraphSelection'),
    sourceFunction('cancelDebugGraphSelection', 'handleDebugGraphControlEvent'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    let jsDebugGraphSelectionState = null;
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphZoomDomain = null;
    let jsDebugGraphLastPointerType = 'mouse';
    const jsDebugGraphTouchArmDistancePx = 12;
    const jsDebugGraphTouchDirectionRatio = 2;
    const jsDebugGraphTouchHoldMs = 200;
    const jsDebugGraphZoomMinRatio = 0.04;
    const jsDebugGraphZoomMinBuckets = 3;
    const document = {activeElement: null, querySelectorAll: () => []};
    const timers = new Map();
    let nextTimer = 1;
    function setTimeout(callback) { const id = nextTimer++; timers.set(id, callback); return id; }
    function clearTimeout(id) { timers.delete(id); }
    function performanceNow() { return 0; }
    function debugGraphGridDomain() { return {startMs: 0, endMs: 600000, rangeSeconds: 600}; }
    function debugGraphDisplayResolutionMs() { return 10000; }
    function debugGraphSetInteractionLines() {}
    function debugGraphSetSelectionRects() {}
    function debugGraphClearSelectionRects() {}
    function debugGraphSetHoverTooltip() {}
    function debugGraphClearInteractionLines() {}
    function syncDebugGraphResolutionOverride() {}
    function syncJsDebugStatsDeliveryMode() {}
    let refreshCount = 0;
    function refreshDebugGraphSurfaces() { refreshCount += 1; }
    function requestJsDebugHistoryForCurrentDomain() {}
    function syncDebugGraphControls() {}
    const panel = {};
    const svg = {
      captures: 0,
      releases: 0,
      getBoundingClientRect: () => ({left: 0, width: 1000}),
      setPointerCapture() { this.captures += 1; },
      releasePointerCapture() { this.releases += 1; },
    };
    function pointer(type, x, y, timeStamp, prevented) {
      return {
        button: 0,
        pointerType: type,
        pointerId: 1,
        clientX: x,
        clientY: y,
        timeStamp,
        cancelable: true,
        target: {closest: selector => selector === '.js-debug-line-chart' ? svg : null},
        preventDefault() { prevented.count += 1; },
      };
    }
    function touchMove(x, y, timeStamp, prevented) {
      return {
        touches: [{clientX: x, clientY: y}],
        timeStamp,
        cancelable: true,
        preventDefault() { prevented.count += 1; },
      };
    }
    ${gestureSource}
    function runTouch(dx, dy, elapsed) {
      const prevented = {count: 0};
      handleDebugGraphPointerDown(pointer('touch', 100, 100, 0, prevented), panel);
      handleDebugGraphPointerMove(pointer('touch', 100 + dx, 100 + dy, elapsed, prevented), panel);
      handleDebugGraphPointerUp(pointer('touch', 100 + dx, 100 + dy, elapsed + 1, prevented), panel);
      const zoomed = jsDebugGraphZoomDomain !== null;
      jsDebugGraphZoomDomain = null;
      return {prevented: prevented.count, zoomed};
    }
    const wiggle = runTouch(6, 1, 100);
    const vertical = runTouch(6, 40, 100);
    const horizontal = runTouch(60, 2, 100);
    const mousePrevented = {count: 0};
    handleDebugGraphPointerDown(pointer('mouse', 100, 100, 0, mousePrevented), panel);
    const mouseImmediate = jsDebugGraphSelectionState !== null;
    cancelDebugGraphSelection(panel);
    const cancelPrevented = {count: 0};
    handleDebugGraphPointerDown(pointer('touch', 100, 100, 0, cancelPrevented), panel);
    const touchClaimed = handleDebugGraphTouchMove(touchMove(130, 108, 100, cancelPrevented), panel);
    handleDebugGraphPointerMove(pointer('touch', 700, 108, 150, cancelPrevented), panel);
    handleDebugGraphPointerCancel(pointer('touch', 0, 0, 151, cancelPrevented), panel);
    const cancelCommitted = jsDebugGraphZoomDomain !== null;
    jsDebugGraphZoomDomain = null;
    const nativeScrollPrevented = {count: 0};
    handleDebugGraphPointerDown(pointer('touch', 100, 100, 0, nativeScrollPrevented), panel);
    const verticalClaimed = handleDebugGraphTouchMove(touchMove(106, 140, 100, nativeScrollPrevented), panel);
    const holdCandidate = {startClientX: 0, startClientY: 0, startedAtMs: 0};
    result = {
      wiggle,
      vertical,
      horizontal,
      mouseImmediate,
      mousePrevented: mousePrevented.count,
      touchClaimed,
      cancelCommitted,
      cancelPrevented: cancelPrevented.count,
      verticalClaimed,
      nativeScrollPrevented: nativeScrollPrevented.count,
      refreshCount,
      holdBefore: debugGraphTouchCandidateDecision(holdCandidate, 0, 0, 199),
      holdAt: debugGraphTouchCandidateDecision(holdCandidate, 0, 0, 200),
      captures: svg.captures,
    };
  `, context);
  assert.deepEqual({...context.result.wiggle}, {prevented: 0, zoomed: false});
  assert.deepEqual({...context.result.vertical}, {prevented: 0, zoomed: false});
  assert.equal(context.result.horizontal.zoomed, true);
  assert.ok(context.result.horizontal.prevented >= 1);
  assert.equal(context.result.mouseImmediate, true);
  assert.ok(context.result.mousePrevented >= 1);
  assert.equal(context.result.touchClaimed, true);
  assert.equal(context.result.cancelCommitted, true);
  assert.ok(context.result.cancelPrevented >= 1);
  assert.equal(context.result.verticalClaimed, false);
  assert.equal(context.result.nativeScrollPrevented, 0);
  assert.ok(context.result.refreshCount >= 1);
  assert.equal(context.result.holdBefore, 'wait');
  assert.equal(context.result.holdAt, 'arm');
  assert.ok(context.result.captures >= 2);
});

test('accepted snapshots bypass the event debounce and render immediately', () => {
  assert.match(source, /scheduleJsDebugPanelRefresh\(\{force: forceGraphRefresh, immediate: true\}\)/);
  const functionText = coreSource.slice(
    coreSource.indexOf('function runJsDebugPanelRefresh('),
    coreSource.indexOf('\nfunction flushDeferredJsDebugPanelRefresh('),
  );
  const context = {result: null};
  vm.runInNewContext(`
    var jsDebugCollectionEnabled = true;
    var jsDebugRenderForce = false;
    var jsDebugRenderDragDeferred = false;
    var jsDebugRenderTimer = null;
    var jsDebugRenderDebounceMs = 500;
    var dragState = {item: null};
    var calls = [];
    var cleared = [];
    function refreshDebugPanelsFromEvents(options) { calls.push(options); }
    function setTimeout() { return 7; }
    function clearTimeout(timer) { cleared.push(timer); }
    ${functionText}
    scheduleJsDebugPanelRefresh();
    scheduleJsDebugPanelRefresh({force: true, immediate: true});
    result = {calls, cleared, timer: jsDebugRenderTimer};
  `, context);
  assert.equal(context.result.calls.length, 1);
  assert.equal(context.result.calls[0].force, true);
  assert.deepEqual([...context.result.cleared], [7]);
  assert.equal(context.result.timer, null);
});

test('resolution completion accepts later matching generations only after matching data paints', () => {
  const resolutionSource = [
    sourceFunction('clearDebugGraphPendingResolutionChange', 'debugGraphResolutionChangeDataSatisfied'),
    sourceFunction('debugGraphResolutionChangeDataSatisfied', 'resolveDebugGraphResolutionChange'),
    sourceFunction('resolveDebugGraphResolutionChange', 'setDebugGraphChartLayout'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    let jsDebugGraphPendingResolutionChange = null;
    let jsDebugGraphResolutionOverrideSeconds = 300;
    let jsDebugGraphRangeSeconds = 7200;
    let jsDebugGraphExactResolutionEnabled = true;
    const jsDebugHistoryReadiness = {overlayVisible: true};
    const diagnostics = [];
    const cleared = [];
    function clearTimeout(value) { cleared.push(value); }
    function syncJsDebugHistoryReadinessSurfaces() {}
    function recordJsDebugStatsDiagnostic(level, message) { diagnostics.push({level, message}); }
    function performanceNow() { return 4000; }
    function normalizedDebugGraphResolutionOverrideSeconds(value) { return Number(value) || 0; }
    function debugGraphDomain() { return {startMs: 0, endMs: 7200000}; }
    function saveJsDebugStatsUiPreferences() {}
    function refreshDebugGraphSurfaces() {}
    function emitNotification() {}
    function t() { return ''; }
    ${resolutionSource}
    const pending = {
      previousSeconds: 60,
      targetSeconds: 300,
      rangeSeconds: 7200,
      requestedResolutionSeconds: 300,
      targetStartSeconds: 100,
      targetEndSeconds: 7300,
      armedGeneration: 7,
      armedAtMs: 1000,
      watchdogTimer: 91,
    };
    const matching = {
      phase: 'ready',
      generation: 8,
      resolutionSeconds: 300,
      requestCoverageIntervals: [{startSeconds: 300, endSeconds: 7500, resolutionSeconds: 300}],
    };
    const stale = {...matching, generation: 6};
    const wrongResolution = {...matching, resolutionSeconds: 60};
    clearDebugGraphPendingResolutionChange();
    const emptyClearSucceeded = jsDebugGraphPendingResolutionChange === null;
    jsDebugGraphPendingResolutionChange = pending;
    resolveDebugGraphResolutionChange(matching);
    const beforePaint = jsDebugGraphPendingResolutionChange === pending;
    resolveDebugGraphResolutionChange(matching, {painted: true, watchdog: true});
    result = {
      beforePaint,
      emptyClearSucceeded,
      completed: jsDebugGraphPendingResolutionChange === null,
      overlayVisible: jsDebugHistoryReadiness.overlayVisible,
      staleSatisfied: debugGraphResolutionChangeDataSatisfied(pending, stale),
      wrongResolutionSatisfied: debugGraphResolutionChangeDataSatisfied(pending, wrongResolution),
      diagnostics,
      cleared,
    };
  `, context);
  assert.equal(context.result.beforePaint, true);
  assert.equal(context.result.emptyClearSucceeded, true);
  assert.equal(context.result.completed, true);
  assert.equal(context.result.overlayVisible, false);
  assert.equal(context.result.staleSatisfied, false);
  assert.equal(context.result.wrongResolutionSatisfied, false);
  assert.equal(context.result.diagnostics.length, 1);
  assert.deepEqual([...context.result.cleared], [91]);
  assert.match(source, /setTimeout\([\s\S]*?jsDebugGraphResolutionWatchdogMs\)/);
  assert.match(source, /const jsDebugGraphResolutionWatchdogMs = 3000/);
});

test('already-selected current views keep their cached generation and skip select', () => {
  const functionText = sourceFunction('syncJsDebugCurrentStatsClient', 'jsDebugStatsTokenConsumerEnabled');
  const context = {result: null};
  vm.runInNewContext(`
    const selection = {rangeSeconds: 7200, resolution: 300};
    const generation = {cache_generation: 12};
    const controller = {
      selection: () => ({range_seconds: 7200, resolution: 300}),
      generation: () => generation,
    };
    const calls = {select: 0, start: 0};
    const client = {
      controller: () => controller,
      setVisible() {},
      select() { calls.select += 1; },
      start() { calls.start += 1; return Promise.resolve(); },
    };
    const jsDebugCurrentStatsClientState = {client, selectionKey: '7200:300', startPromise: null};
    const jsDebugCollectionEnabled = true;
    function ensureJsDebugCurrentStatsClient() { return client; }
    function jsDebugStatsPanelVisible() { return true; }
    function jsDebugCurrentStatsSelection() { return selection; }
    function recordJsDebugStatsDiagnostic() {}
    function jsDebugErrorText(error) { return String(error); }
    ${functionText}
    result = {handled: syncJsDebugCurrentStatsClient({select: true}), calls, sameGeneration: controller.generation() === generation};
  `, context);
  assert.equal(context.result.handled, true);
  assert.equal(context.result.calls.select, 0);
  assert.equal(context.result.calls.start, 1);
  assert.equal(context.result.sameGeneration, true);
});

test('live chart slide cadence follows effective resolution with one shared repaint', () => {
  const cadenceSource = [
    sourceFunction('debugGraphSlideIntervalMs', 'debugGraphSlidingAxisActive'),
    sourceFunction('debugGraphSlideLiveViews', 'stopDebugGraphLiveTicker'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    let resolutionMs = 10000;
    const graph = {offsetParent: {}, dataset: {jsDebugGraphRenderedAt: '0'}};
    const renders = [];
    const document = {querySelectorAll: () => [graph]};
    function debugGraphDomain(nowMs) { return {startMs: nowMs - 600000, endMs: nowMs}; }
    function debugGraphDisplayResolutionMs() { return resolutionMs; }
    function refreshDebugGraphElement(_graph, options) { renders.push(options); }
    ${cadenceSource}
    debugGraphSlideLiveViews(4999);
    const coarseBefore = renders.length;
    debugGraphSlideLiveViews(5000);
    const coarseAt = renders.length;
    renders.length = 0;
    resolutionMs = 1000;
    debugGraphSlideLiveViews(999);
    const oneSecondBefore = renders.length;
    debugGraphSlideLiveViews(1000);
    result = {
      intervals: [debugGraphSlideIntervalMs(1000), debugGraphSlideIntervalMs(10000), debugGraphSlideIntervalMs(60000), debugGraphSlideIntervalMs(300000)],
      coarseBefore,
      coarseAt,
      oneSecondBefore,
      oneSecondAt: renders.length,
      forced: renders[0]?.force,
    };
  `, context);
  assert.deepEqual([...context.result.intervals], [1000, 5000, 5000, 5000]);
  assert.equal(context.result.coarseBefore, 0);
  assert.equal(context.result.coarseAt, 1);
  assert.equal(context.result.oneSecondBefore, 0);
  assert.equal(context.result.oneSecondAt, 1);
  assert.equal(context.result.forced, true);
});

test('stats and cost renders defer while a chart gesture owns their live DOM', () => {
  const interactionSource = sourceFunction(
    'debugGraphInteractionBelongsToPanel',
    'flushDeferredDebugGraphInteractionRefresh',
  );
  const refreshSource = sourceFunction('refreshDebugGraphElement', 'bindDebugCostSummaryTabButtons');
  const statsContext = {result: null};
  vm.runInNewContext(`
    const panel = {};
    let jsDebugGraphSelectionState = {panel};
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderDragging = false;
    let replacements = 0;
    const graph = {
      dataset: {},
      closest: () => panel,
      querySelector() { replacements += 1; return null; },
    };
    function debugGraphFocusedControl() { return false; }
    ${interactionSource}
    ${refreshSource}
    const rendered = refreshDebugGraphElement(graph, {force: true});
    result = {rendered, replacements, pending: graph.dataset.jsDebugGraphRefreshPending};
  `, statsContext);
  assert.deepEqual({...statsContext.result}, {rendered: false, replacements: 0, pending: 'true'});

  const costContext = {result: null};
  vm.runInNewContext(`
    const panel = {dataset: {}, querySelector() { throw new Error('active cost DOM was replaced'); }};
    let jsDebugGraphSelectionState = {panel};
    let jsDebugGraphTouchCandidateState = null;
    const dragState = {item: null};
    let jsDebugRenderForce = false;
    let jsDebugRenderDragDeferred = false;
    let jsDebugCostPanelNextRefreshAtMs = 0;
    const yocostItemId = '__yocost__';
    const document = {visibilityState: 'visible', querySelectorAll: () => [panel]};
    function itemIsActivePaneTab() { return true; }
    ${interactionSource}
    ${sourceFunction('renderYoCostPanels', 'refreshDebugGraphSurfaces')}
    result = {
      rendered: renderYoCostPanels({force: true}),
      pending: panel.dataset.jsDebugGraphRefreshPending,
    };
  `, costContext);
  assert.deepEqual({...costContext.result}, {rendered: false, pending: 'true'});
  assert.match(sourceFunction('handleDebugGraphPointerUp', 'handleDebugGraphPointerCancel'), /flushDeferredDebugGraphInteractionRefresh/);
  assert.match(sourceFunction('handleDebugGraphPointerCancel', 'cancelDebugGraphSelection'), /useEventRatio: false/);
});

test('API SSE log preserves reader position through updates and forced rebuilds', () => {
  const anchorSource = [
    sourceFunction('debugLogScrollAnchor', 'restoreDebugLogScrollAnchor'),
    sourceFunction('restoreDebugLogScrollAnchor', 'renderDebugPanels'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    ${anchorSource}
    function logAt(scrollTop, scrollHeight = 1000, clientHeight = 100) {
      return {
        scrollTop,
        scrollLeft: 7,
        scrollHeight,
        clientHeight,
        selectionStart: 3,
        selectionEnd: 8,
        setSelectionRange(start, end) { this.restoredSelection = [start, end]; },
      };
    }
    const reading = logAt(200);
    const readingAnchor = debugLogScrollAnchor(reading);
    reading.scrollHeight = 1200;
    restoreDebugLogScrollAnchor(reading, readingAnchor);
    const bottom = logAt(885);
    const bottomAnchor = debugLogScrollAnchor(bottom);
    bottom.scrollHeight = 1200;
    restoreDebugLogScrollAnchor(bottom, bottomAnchor);
    const threshold = logAt(880);
    const thresholdAnchor = debugLogScrollAnchor(threshold);
    threshold.scrollHeight = 1200;
    restoreDebugLogScrollAnchor(threshold, thresholdAnchor);
    const explicit = logAt(200);
    const explicitAnchor = debugLogScrollAnchor(explicit);
    explicit.scrollHeight = 1200;
    restoreDebugLogScrollAnchor(explicit, explicitAnchor, {scrollToBottom: true});
    result = {
      reading: {top: reading.scrollTop, left: reading.scrollLeft, selection: reading.restoredSelection},
      bottom: bottom.scrollTop,
      threshold: threshold.scrollTop,
      explicit: explicit.scrollTop,
    };
  `, context);
  assert.equal(context.result.reading.top, 200);
  assert.equal(context.result.reading.left, 7);
  assert.deepEqual([...context.result.reading.selection], [3, 8]);
  assert.equal(context.result.bottom, 1200);
  assert.equal(context.result.threshold, 1200);
  assert.equal(context.result.explicit, 1200);
  const renderText = sourceFunction('renderDebugPanels', 'refreshDebugPanelsFromEvents');
  assert.ok(renderText.indexOf('debugLogScrollAnchor(') < renderText.indexOf('body.innerHTML ='));
  assert.ok(renderText.indexOf('body.innerHTML =') < renderText.indexOf('restoreDebugLogScrollAnchor('));
  const refreshText = sourceFunction('refreshDebugPanelFromEvents', 'debugGraphFocusedControl');
  assert.doesNotMatch(refreshText, /document\.activeElement === log/);
  assert.doesNotMatch(refreshText, /options\.force === true \? log\.scrollHeight/);
  assert.match(coreSource, /renderDebugPanels\(\{force: true, scrollLogToBottom: true\}\)/);
});

test('the current snapshot adapter merges both GPU dimensions into one device', () => {
  assert.match(source, /const device = record\.host_metrics\.gpu_devices\[source\] \|\| \{label: source, util_total_percent: 0, memory_used_total_bytes: 0/);
  assert.match(source, /device\.util_total_percent = value/);
  assert.match(source, /device\.memory_used_total_bytes = value/);

  const functionText = source.slice(
    source.indexOf('function jsDebugCurrentBucketRecord('),
    source.indexOf('\nfunction jsDebugCurrentBucketHasFamilyData('),
  );
  const context = {
    result: null,
    jsDebugCurrentSeriesValue: (series, name) => Number(series[name]?.value),
    jsDebugCurrentModelComponent: () => ({}),
    jsDebugCurrentCostSummary: () => ({components: []}),
  };
  vm.runInNewContext(`${functionText}\nresult = jsDebugCurrentBucketRecord({
    start: 100,
    duration: 10,
    series: {
      'gpu_memory_bytes:gpu:0': {value: 1234567890},
      'gpu_util_percent:gpu:0': {value: 8},
    },
  });`, context);
  const device = context.result.host_metrics.gpu_devices['gpu:0'];
  assert.equal(device.util_total_percent, 8);
  assert.equal(device.memory_used_total_bytes, 1234567890);
});

test('the current snapshot adapter retains marginal cost, API-list cost, and usage-token series', () => {
  const functionText = source.slice(
    source.indexOf('function jsDebugCurrentBucketRecord('),
    source.indexOf('\nfunction jsDebugCurrentBucketHasFamilyData('),
  );
  const context = {
    result: null,
    jsDebugCurrentSeriesValue: (series, name) => Number(series[name]?.value),
    jsDebugCurrentModelComponent: () => ({}),
    jsDebugCurrentCostSummary: () => ({components: []}),
  };
  vm.runInNewContext(`${functionText}\nresult = jsDebugCurrentBucketRecord({
    start: 100,
    duration: 10,
    series: {
      cost_micro_usd: {value: 0},
      api_list_cost_micro_usd: {value: 600000},
      usage_tokens: {value: 1200},
    },
  });`, context);
  assert.equal(context.result.cost_summary.range_report, false);
  assert.equal(context.result.cost_summary.total_micro_usd, 0);
  assert.equal(context.result.cost_summary.api_list_micro_usd, 600000);
  assert.equal(context.result.cost_summary.total_token_quantity, 1200);
  vm.runInNewContext(`result = jsDebugCurrentBucketRecord({
    start: 110,
    duration: 10,
    series: {usage_tokens: {value: 300}},
  });`, context);
  assert.equal(context.result.cost_summary.complete, false);
  assert.equal(context.result.cost_summary.priced_count, 0);
  assert.equal(context.result.cost_summary.unpriced_count, 1);
  assert.equal(context.result.cost_summary.unpriced_token_quantity, 300);
});

test('health observations retain measured latency and bytes as original browser facts', () => {
  const functionText = source.slice(
    source.indexOf('function jsDebugCurrentObservationFromEvent('),
    source.indexOf('\nfunction scheduleJsDebugCurrentObservationFlush('),
  );
  const context = {
    result: null,
    jsDebugCurrentObservationState: {epoch: 'epoch-1'},
    jsDebugStatsClientIdForRequest: () => 'client-1',
  };
  vm.runInNewContext(`${functionText}\nresult = jsDebugCurrentObservationFromEvent({
    key: 'epoch-1:health:1',
    event: {type: 'heartbeat', ts: '2026-07-16T17:00:00.000Z', durationMs: 12.5, bytes: 456},
  });`, context);
  assert.equal(context.result.payload.kind, 'heartbeat');
  assert.equal(context.result.payload.latency_ms, 12.5);
  assert.equal(context.result.payload.bytes, 456);
});

Promise.all(pending).then(() => {
  console.log(`stats current panel suite: ${passed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
});

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static_src/js/yolomux/85_debug_panel.js', 'utf8');
const currentSource = fs.readFileSync('static_src/js/yolomux/84_stats_current.js', 'utf8');
const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const terminalSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
const css = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
const localeEn = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));
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
  assert.equal(fs.existsSync('static_src/js/yolomux/83_stats_panel.js'), false);
});

test('pricing links use one synchronous external-open owner and keep in-app links untouched', () => {
  const start = coreSource.indexOf('function normalizedExternalHttpUrl(');
  const end = coreSource.indexOf('\nfunction triggerExternalUrlDownload(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const helperSource = coreSource.slice(start, end);
  const calls = [];
  const external = {href: 'https://pricing.example.test/models', getAttribute: () => 'https://pricing.example.test/models'};
  const internal = {href: 'https://app.example.test/#transcript', getAttribute: () => '#'};
  const root = {contains: node => node === external || node === internal};
  const context = {
    URL,
    String,
    document: {},
    window: {location: {href: 'https://app.example.test/cost', origin: 'https://app.example.test'}, open: (...args) => {calls.push(args); return {}; }},
  };
  vm.runInNewContext(`${helperSource}\nresult = openExternalLinkFromEvent(event, root);`, {...context, root, event: {target: {closest: () => external}, preventDefault: () => calls.push(['prevent'])}});
  assert.deepEqual(calls, [['https://pricing.example.test/models', '_blank', 'noopener,noreferrer'], ['prevent']]);
  const internalResult = vm.runInNewContext(`${helperSource}\nresult = openExternalLinkFromEvent(event, root);`, {...context, root, event: {target: {closest: () => internal}, preventDefault: () => calls.push(['unexpected'])}});
  assert.equal(internalResult, false);
  assert.match(source, /<a href="\$\{esc\(url\)\}" target="_blank" rel="noopener noreferrer"/);
  assert.match(currentSource, /<a href="\$\{currentStatsEscape\(row\.source_url\)\}" target="_blank" rel="noopener noreferrer"/);
});

test('zero-valued CPU samples remain visibly inside the chart viewBox', () => {
  const geometrySource = source.slice(0, source.indexOf('// The readiness machine'));
  const plotSource = sourceFunction('debugGraphPlotYForValue', 'debugGraphXForTime');
  const context = {result: null};
  vm.runInNewContext(`${geometrySource}\n${plotSource}\nresult = {zero: debugGraphPlotYForValue(0, 100), max: debugGraphPlotYForValue(100, 100), height: jsDebugGraphGeometry.height};`, context);
  assert.equal(context.result.zero, context.result.height - 4, 'a valid 0% web CPU line is not clipped below the SVG');
  assert.equal(context.result.max, 8, 'the top plotting bound stays unchanged');
});

test('Services omits the duplicated web process while CPU names it clearly', () => {
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const serviceSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  assert.match(cpuSource, /yolomux\.py \(web\) :\$\{legacyWebPort\[1\]\}/);
  assert.match(serviceSource, /if \(key === 'web'\) continue;/);
});

test('CPU promotes the newest sampled owner instead of covering it with a duplicate fallback', () => {
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const context = {
    result: null,
    location: {port: '9001', protocol: 'https:'},
    jsDebugGraphProcessCpuColors: {current: 'green', peers: ['red']},
    debugGraphProcessCpuBucketValue: () => 0,
    debugGraphProcessCpuBucketHasData: () => true,
  };
  vm.runInNewContext(`${cpuSource}\nresult = debugGraphProcessCpuSeriesDefs([{servers: new Map([['port:9000', {cpuCount: 1, label: 'port:9000'}]])}]);`, context);
  assert.equal(context.result.length, 1);
  assert.equal(context.result[0].key, 'cpu:port:9000');
  assert.equal(context.result[0].labelParams.process, 'yolomux.py (web)');
  assert.equal(context.result[0].color, 'green');
  assert.equal(context.result[0].linePattern, 'solid');
});

test('chart popup uses the full localized chart titles', () => {
  const controls = sourceFunction('debugGraphChartToggleControlsHtml', 'debugGraphLayoutControlsHtml');
  assert.match(controls, /const label = debugGraphLocalizedLabel\(group\)/);
  assert.match(controls, /\$\{esc\(label\)\}<\/label>/);
  assert.doesNotMatch(source, /toggleLabelEn/);
  for (const token of ['data-js-debug-range-slider', 'data-js-debug-resolution-override', 'data-js-debug-chart-layout', 'data-js-debug-chart-close']) {
    assert.ok(source.includes(token), token);
  }
  assert.match(css, /\.js-debug-subtabs/);
  assert.match(css, /\.js-debug-chart/);
  assert.match(css, /\.js-debug-system-grid/);
  assert.match(css, /\.js-debug-logs-view/);
});

test('model output chart is fixed to generated output without a dimension picker', () => {
  const english = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));
  assert.equal(english['debug.graph.chart.modelTokens'], 'Model output tokens/min');
  assert.match(source, /key: 'modelTokens'[\s\S]*dynamicTokenDimension: 'model'/);
  assert.doesNotMatch(source, /data-js-debug-model-token-dimension-select/);
  assert.doesNotMatch(source, /jsDebugGraphModelTokenDimension/);
  assert.doesNotMatch(source, /debugGraphAgentTokenBucketDimensionValue/);
  assert.match(source, /function debugGraphModelTokenSeriesDefs\(buckets\) \{\s*return debugGraphTokenSeriesDefs\(buckets, 'model'\);/);
});

test('macOS keeps Activity Monitor memory facts and pressure in one card', () => {
  const resolver = sourceFunction('debugGraphResolvedChartGroup', 'debugGraphMacMemoryDetailsHtml');
  const details = sourceFunction('debugGraphMacMemoryDetailsHtml', 'debugGraphLegendSeriesItems');
  const chart = sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale');
  const pressureColor = sourceFunction('debugGraphMacMemoryPressureColor', 'debugGraphSeriesStyleAttr');
  assert.match(resolver, /key !== 'memory'/);
  assert.match(resolver, /series: \['macMemoryPressure'\]/);
  assert.match(resolver, /fixedMax: 100/);
  for (const label of ['Physical Memory', 'Memory Used', 'Cached Files', 'Swap Used', 'App Memory', 'Wired Memory', 'Compressed']) assert.match(details, new RegExp(label));
  assert.match(chart, /debugGraphMacMemoryDetailsHtml\(buckets\)/);
  assert.match(css, /\.js-debug-mac-memory-details/);
  assert.match(css, /@container \(max-width: 20rem\)/);
  assert.doesNotMatch(pressureColor, /pressure </);
  assert.match(pressureColor, /level === 1/);
  assert.match(pressureColor, /level === 2/);
  assert.match(pressureColor, /level >= 4/);
  const context = {result: null, Number};
  vm.runInNewContext(`${pressureColor}\nresult = [debugGraphMacMemoryPressureColor(1), debugGraphMacMemoryPressureColor(2), debugGraphMacMemoryPressureColor(4), debugGraphMacMemoryPressureColor(null)];`, context);
  assert.deepEqual([...context.result], ['var(--good)', 'var(--warning-border-strong)', 'var(--bad)', 'var(--muted)']);
  assert.match(sourceFunction('debugGraphSeriesStyleAttr', 'debugGraphSeriesClientAttrs'), /debugGraphSeriesDisplayColor\(series\)/);
  assert.match(sourceFunction('debugGraphSeriesDisplayColor', 'debugGraphSeriesStyleAttr'), /series\?\.colorValues/);
});

test('Agents keeps per-session revision joins and semantic paint', () => {
  assert.match(source, /agent_window_snapshot_revision/);
  assert.match(source, /function debugGraphLiveAgentWindowDetailHtml\(groupKey = 'activity'\)/);
  assert.match(source, /Live status is waiting for the chart snapshot/);
  assert.match(source, /agentWindowPhysicalKey\(agent\)/);
  assert.match(source, /status is stale \(rev/);
  assert.match(source, /const currentRows = rows\.filter\(row => row\.revision >= chartRevision\)/);
  assert.match(source, /const state = agentWindowStateKey\(agent\?\.state\)/);
  assert.match(source, /group\.key === 'activity' \? debugGraphLiveAgentWindowDetailHtml\(group\.key\) : ''/);
  assert.match(source, /cssKey: key/);
  assert.doesNotMatch(source, /activitySessions|Agent sessions|askSessionTotal/);
  assert.match(css, /\.js-debug-agent-window-detail/);
  assert.match(css, /\.js-debug-legend-swatch--workingAgents/);
});

test('Agent-window live detail joins current sessions, diagnoses stale sessions, and recovers', () => {
  const detailStart = source.indexOf('function debugGraphLiveAgentWindowRows()');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  assert.notEqual(detailStart, -1, 'live detail owner exists');
  assert.notEqual(detailEnd, -1, 'live detail owner has a bounded source region');
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    autoApproveStates: new Map(),
    jsDebugStatsPollState: {agentWindowSnapshotRevision: 7},
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => String(kind || ''),
    agentWindowPhysicalKey: agent => String(agent?.window_index ?? ''),
    agentWindowIndex: agent => Number(agent?.window_index),
    agentWindowCanonicalLabel: (_index, kind) => kind,
    agentWindowStateKey: state => String(state || 'idle') === 'transition' ? 'cooldown' : String(state || 'idle'),
    esc: value => String(value),
    document: {querySelectorAll: () => []},
    result: null,
  };
  vm.runInNewContext(`${detailSource}\nautoApproveStates.set('one', {agent_window_snapshot_revision: 7, agent_windows: [{window_index: 0, kind: 'claude', state: 'working'}]});\nautoApproveStates.set('two', {agent_window_snapshot_revision: 6, agent_windows: [{window_index: 1, kind: 'codex', state: 'idle'}]});\nresult = debugGraphLiveAgentWindowDetailHtml('activity');`, context);
  assert.match(context.result, /data-js-debug-agent-window-detail="activity"[^>]*state="stale"/);
  assert.match(context.result, /one → claude → claude → working/);
  assert.match(context.result, /two status is stale \(rev 6 vs 7\)/);
  assert.doesNotMatch(context.result, /waiting for the chart snapshot/);
  context.autoApproveStates.set('two', {agent_window_snapshot_revision: 7, agent_windows: [{window_index: 1, kind: 'codex', state: 'idle'}]});
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowDetailHtml('activity');`, context);
  assert.match(context.result, /data-js-debug-agent-window-detail="activity"[^>]*state="current"/);
  assert.match(context.result, /two → codex → codex → idle/);
  context.jsDebugStatsPollState.agentWindowSnapshotRevision = 0;
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowDetailHtml('activity');`, context);
  assert.match(context.result, /data-js-debug-agent-window-detail="activity"[^>]*state="changed"/);
  assert.match(context.result, /waiting for the chart snapshot/);
});

test('YO!stats puts Charts before Size, Range, and Resolution without changing YO!cost ordering', () => {
  const controlsSource = sourceFunction('debugGraphControlsHtml', 'debugGraphLocalDateKey');
  const costSource = sourceFunction('yoCostPanelHtml', 'openYoCostTranscriptPreview');
  const controlOrder = [
    controlsSource.indexOf('debugGraphChartToggleControlsHtml()'),
    controlsSource.indexOf('debugGraphLayoutControlsHtml()'),
    controlsSource.indexOf('debugGraphRangeResolutionControlsHtml(nowMs)'),
  ];
  const costOrder = [
    costSource.indexOf('data-js-yocost-data-age-label'),
    costSource.indexOf('debugGraphLayoutControlsHtml()'),
    costSource.indexOf('${refresh}'),
    costSource.indexOf('debugGraphRangeResolutionControlsHtml(nowMs)'),
  ];
  assert.ok(controlOrder.every((index, position) => index >= 0 && (position === 0 || index > controlOrder[position - 1])), 'YO!stats control order');
  assert.ok(costOrder.every((index, position) => index >= 0 && (position === 0 || index > costOrder[position - 1])), 'YO!cost control order');
  assert.match(css, /\.js-debug-graph-controls \{\s+flex-wrap: nowrap;/);
  assert.match(css, /\.js-debug-range-resolution-controls \{\s+flex: 1 1 12rem;/);
  assert.match(source, /<details class="js-debug-chart-toggle-control" data-js-debug-chart-menu>/);
  assert.match(source, /<input type="checkbox" data-js-debug-chart-toggle=/);
  assert.match(source, /event\.type === 'change' && chartToggle[\s\S]*?chartToggle\.checked/);
  assert.match(source, /function handleDebugGraphOutsideTapDismiss\(event\)[\s\S]*?data-js-debug-chart-menu\]\[open\][\s\S]*?menu\.open = false/);
  assert.match(css, /\.js-debug-chart-toggle-menu \{[\s\S]*?position: absolute;/);
  assert.match(css, /@container \(max-width: 34rem\) \{\s+\.js-debug-graph-controls/);
  assert.match(css, /grid-template-rows: auto auto;/);
  assert.match(css, /\.js-yocost-controls > \.js-debug-range-resolution-controls \{\s+flex: 1 0 100%;\s+order: 2;/);
});

test('one shared spike-compression descriptor preserves token behavior and makes rare daemon spikes readable', () => {
  const axisSource = sourceFunction('debugGraphSpikeCompressedAxisDescriptor', 'debugGraphTokenSpikeAxisDescriptor');
  const plotSource = sourceFunction('debugGraphPlotYForValue', 'debugGraphXForTime');
  const context = {
    Object,
    Number,
    Math,
    result: null,
    jsDebugGraphGeometry: {plotTop: 8, plotHeight: 150},
    debugGraphChartAxisMax: (_group, rawMax) => rawMax <= 10 ? 10 : 100,
  };
  vm.runInNewContext(`${axisSource}\n${plotSource}\nconst token = debugGraphSpikeCompressedAxisDescriptor({unit: 'tokensPerMinute'}, [1, 2, 3, 4, 5, 5, 5, 5, 5, 100]);\nconst daemon = debugGraphSpikeCompressedAxisDescriptor({unit: 'percent'}, [1, 2, 3, 4, 5, 5, 5, 5, 5, 100]);\nconst quiet = debugGraphSpikeCompressedAxisDescriptor({unit: 'percent'}, [1, 2, 3, 4, 5, 5, 5, 5]);\nresult = {token, daemon, quiet, compressedY: debugGraphPlotYForValue(5, daemon.axisMax, daemon.scale), linearY: debugGraphPlotYForValue(5, daemon.axisMax)};`, context);
  assert.equal(context.result.token.scale.mode, 'broken-linear', 'token descriptor retains its previous rare-spike mode');
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.token)), JSON.parse(JSON.stringify(context.result.daemon)), 'tokens and daemon load use the same descriptor thresholds and geometry');
  assert.equal(context.result.quiet.scale, false, 'ordinary low daemon load stays linear');
  assert.ok(context.result.compressedY + 40 < context.result.linearY, context.result);
  assert.match(source, /function debugGraphTokenSpikeAxisDescriptor\(buckets\)[\s\S]*?debugGraphSpikeCompressedAxisDescriptor/);
  assert.match(source, /group\.key === 'serversLoad' \? debugGraphSpikeCompressedAxisDescriptor\(group, plotSeries\.flatMap\(debugGraphSeriesPlotValues\)\)/);
  assert.match(source, /data-js-debug-chart-scale/);
  assert.match(source, /data-js-debug-chart-axis-break/);
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

test('the System sampler reuses the same warning block for sustained collector failure loops', () => {
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
      quarantined_conflict_count: 0,
      health: {
        state: 'warning',
        reason: 'sustained sampler failure loop in cpu: 496 failures, last FileNotFoundError: statsd.sock missing',
        last_accepted_atom_age_seconds: 5,
        sampler_warning: {family: 'cpu'},
      },
    }}], 1000);
  `, context);
  assert.match(context.result, /data-js-debug-usage-health="warning"/);
  assert.match(context.result, /sustained sampler failure loop in cpu/);
  assert.match(context.result, /FileNotFoundError: statsd\.sock missing/);
});

test('System renders bounded recurring-work diagnostics without client identity or payload data', () => {
  const functionText = sourceFunction('debugSystemRecurringWorkHtml', 'debugSystemSamplerFamilyEntries');
  const context = {result: null};
  vm.runInNewContext(`
    function esc(value) { return String(value); }
    function t(key) { return key; }
    function debugSystemNumber(value) { return Number.isFinite(Number(value)) ? String(value) : 'N/A'; }
    function debugGraphTerseTimeText(value) { return String(value) + 'ms'; }
    function relativeTimeFormat(value) { return String(value) + ' ago'; }
    ${functionText}
    result = debugSystemRecurringWorkHtml([{owner: 'sse_heartbeat', class: 'lease', cadence_seconds: 15, demanded: true, attempts: 4, useful: 4, no_change: 0, failures: 0, last_useful_at: 90, next_due_in_seconds: 15}], 100);
  `, context);
  assert.match(context.result, /data-js-debug-recurring-work/);
  assert.match(context.result, /sse_heartbeat/);
  assert.match(context.result, /4 \/ 4 \/ 0 \/ 0/);
  assert.match(context.result, /10 ago/);
  assert.doesNotMatch(context.result, /client_id|payload|request/);
  assert.match(source, /debugSystemCardHtml\('Recurring work', debugSystemRecurringWorkHtml\(recurringWork\), \{wide: true\}\)/);
});

test('the exact current snapshot feeds the established renderer without legacy APIs', () => {
  assert.match(source, /\/api\/stats-snapshot\?range_seconds=/);
  assert.match(source, /function applyJsDebugCurrentSnapshot\(/);
  assert.match(source, /debugGraphApplyServerRecord\(jsDebugCurrentBucketRecord/);
  assert.doesNotMatch(source, /fetchJsDebugStatsJson\(jsDebugStatsSampleQuery/);
});

test('browser observations share the topbar ping and keep a calm bounded cadence', () => {
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
  assert.match(source, /async function measureClientHealth\(\)/);
  assert.match(terminalSource, /async function updateLatency\(\) \{\s*return measureClientHealth\(\);\s*\}/);
  assert.doesNotMatch(source, /debug-client-health/);
  assert.match(source, /lastObservationAtMs/);
  assert.equal((source.match(/apiFetchJson\(url/g) || []).length, 1, 'YO!stats must reuse the topbar health round trip');
});

testAsync('browser observation writer fences acknowledge, back off, stop terminal 426, and restart with a new page epoch', async () => {
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
      cache_write_5m: {tokens: 100, micro_usd: 0, api_list_micro_usd: 40000},
      cache_write_1h: {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
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
  assert.equal(priceContext.result.subscription, '$0.00 marginal · $0.60 list');
  assert.equal(priceContext.result.defaultProfile, '$0.60');
  assert.match(priceContext.result.html, /\$0\.00 marginal[\s\S]*\$0\.60 list/);
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
  assert.match(source, /const jsDebugGraphTouchHoldMs = 200;/, 'touch zoom has an explicit hold duration instead of an undefined timer delay');
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
    const jsDebugGraphTouchArmDistancePx = 24;
    const jsDebugGraphTouchDirectionRatio = 3;
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
    const horizontalJitter = runTouch(23, 2, 100);
    const vertical = runTouch(6, 40, 100);
    const horizontal = runTouch(60, 2, 100);
    const heldPrevented = {count: 0};
    handleDebugGraphPointerDown(pointer('touch', 100, 100, 0, heldPrevented), panel);
    handleDebugGraphPointerMove(pointer('touch', 100, 100, 200, heldPrevented), panel);
    const heldSelectionStarted = jsDebugGraphSelectionState !== null;
    handleDebugGraphPointerMove(pointer('touch', 700, 102, 250, heldPrevented), panel);
    handleDebugGraphPointerUp(pointer('touch', 700, 102, 251, heldPrevented), panel);
    const heldZoomed = jsDebugGraphZoomDomain !== null;
    jsDebugGraphZoomDomain = null;
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
      horizontalJitter,
      vertical,
      horizontal,
      heldSelectionStarted,
      heldZoomed,
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
  assert.deepEqual({...context.result.horizontalJitter}, {prevented: 0, zoomed: false});
  assert.deepEqual({...context.result.vertical}, {prevented: 0, zoomed: false});
  assert.equal(context.result.horizontal.zoomed, false);
  assert.equal(context.result.horizontal.prevented, 0);
  assert.equal(context.result.heldSelectionStarted, true);
  assert.equal(context.result.heldZoomed, true);
  assert.equal(context.result.mouseImmediate, true);
  assert.ok(context.result.mousePrevented >= 1);
  assert.equal(context.result.touchClaimed, false);
  assert.equal(context.result.cancelCommitted, false);
  assert.equal(context.result.cancelPrevented, 0);
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

test('live ticker sleeps until the next slide boundary instead of polling animation frames', () => {
  const tickerSource = [
    sourceFunction('debugCostAgeLabels', 'debugCostAgeLabelText'),
    sourceFunction('refreshDebugCostAgeLabels', 'debugGraphSlideIntervalMs'),
    sourceFunction('debugGraphSlideIntervalMs', 'debugGraphSlidingAxisActive'),
    sourceFunction('debugGraphSlidingAxisActive', 'debugGraphLiveTickerNextDueMs'),
    sourceFunction('debugGraphLiveTickerNextDueMs', 'debugGraphLiveTickerNeeded'),
    sourceFunction('debugGraphLiveTickerNeeded', 'debugGraphSlideLiveViews'),
    sourceFunction('debugGraphSlideLiveViews', 'stopDebugGraphLiveTicker'),
    sourceFunction('stopDebugGraphLiveTicker', 'debugGraphLiveTimerTick'),
    sourceFunction('debugGraphLiveTimerTick', 'syncDebugGraphLiveTicker'),
    sourceFunction('syncDebugGraphLiveTicker', 'flushDeferredDebugGraphRefresh'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    let nowMs = 0;
    let jsDebugGraphLiveTimer = 0;
    let jsDebugCostAgeNextRefreshAtMs = 0;
    let jsDebugGraphRangeSeconds = 300;
    const jsDebugGraphSlideMaxRangeSeconds = 3600;
    let queryCount = 0;
    const timers = [];
    const document = {visibilityState: 'visible', querySelectorAll() { queryCount += 1; return [{offsetParent: {}, dataset: {jsDebugGraphRenderedAt: '0'}}]; }};
    const yocostItemId = '__yocost__';
    function itemIsActivePaneTab() { return false; }
    function jsDebugStatsPanelVisible() { return true; }
    function debugGraphZoomDomainValid() { return false; }
    function debugGraphDomain(now) { return {startMs: now - 300000, endMs: now}; }
    function debugGraphDisplayResolutionMs() { return 1000; }
    function refreshDebugGraphElement() {}
    function debugCostAgeRefreshDelayMs() { return 3000; }
    function setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; }
    function clearTimeout() {}
    const Date = {now: () => nowMs};
    ${tickerSource}
    syncDebugGraphLiveTicker();
    const firstDelay = timers[0].delay;
    nowMs = 1000;
    timers[0].callback();
    result = {firstDelay, timerCount: timers.length, nextDelay: timers[1].delay, queryCount, liveTimer: jsDebugGraphLiveTimer};
  `, context);
  assert.equal(context.result.firstDelay, 1000, 'a 1s live chart sleeps directly to its next slide boundary');
  assert.equal(context.result.timerCount, 2, 'one timer fire schedules exactly one later wake instead of a frame loop');
  assert.equal(context.result.nextDelay, 1000, 'the next wake remains one slide interval away');
  assert.equal(context.result.queryCount, 1, 'the ticker queries live graphs only at its due fire');
  assert.equal(context.result.liveTimer, 2, 'one pending timeout remains after the due work completes');
  assert.doesNotMatch(source, /requestAnimationFrame\(debugGraphLiveFrameTick\)/, 'the live ticker no longer perpetually arms a frame callback');
});

test('cost-age refresh checks its due time before querying label DOM', () => {
  const ageSource = [
    sourceFunction('debugCostAgeLabels', 'debugCostAgeLabelText'),
    sourceFunction('debugCostAgeLabelText', 'refreshDebugCostAgeLabels'),
    sourceFunction('refreshDebugCostAgeLabels', 'debugGraphSlideIntervalMs'),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    let jsDebugCostAgeNextRefreshAtMs = 100;
    let queries = 0;
    const label = {textContent: '', closest() { return null; }, getClientRects() { return [{}]; }};
    const document = {querySelectorAll() { queries += 1; return [label]; }};
    const yocostItemId = '__yocost__';
    const jsDebugStatsPollState = {lastSampleAtMs: 1};
    const jsDebugPricingRefreshState = {lastRequestedAtMs: 0};
    function itemIsActivePaneTab() { return true; }
    function debugCostAgeRefreshDelayMs() { return 3000; }
    function debugGraphCostText(_key, fallback) { return fallback; }
    function relativeTimeFormat(value) { return String(value); }
    function t() { return 'n/a'; }
    ${ageSource}
    const beforeDue = refreshDebugCostAgeLabels(99);
    const queriedBeforeDue = queries;
    const atDue = refreshDebugCostAgeLabels(100);
    result = {beforeDue, queriedBeforeDue, atDue, queries, text: label.textContent, nextDue: jsDebugCostAgeNextRefreshAtMs};
  `, context);
  assert.equal(context.result.beforeDue, false);
  assert.equal(context.result.queriedBeforeDue, 0, 'an early timer wake does not read label layout');
  assert.equal(context.result.atDue, true);
  assert.equal(context.result.queries, 1);
  assert.match(context.result.text, /Last refreshed/);
  assert.equal(context.result.nextDue, 3100);
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

test('the Cost summary card renders as a content-sized ruled table with the report cost-table style, basis stated once', () => {
  const summaryFn = sourceFunction('debugGraphCostSummaryHtml', 'scheduleDebugCostPricingStatusRefresh');
  // Shared report table style + scroll-wrap owner reused (no bespoke summary table style).
  assert.match(summaryFn, /<div class="js-debug-system-table-wrap js-debug-cost-table-wrap">/);
  assert.match(summaryFn, /<table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="summary"/);
  // Tables stay content-sized; their shared wrapper owns narrow horizontal scrolling.
  assert.match(css, /\.js-debug-cost-usage-table-section \.js-debug-cost-table,\s*\.js-debug-cost-summary \.js-debug-cost-table\s*\{\s*inline-size: max-content;\s*max-inline-size: none;\s*min-inline-size: auto;/);
  // Header row: Usage / Tokens / Price.
  assert.match(summaryFn, /<thead><tr><th scope="col">\$\{esc\(debugGraphCostText\('debug\.cost\.usage', 'Usage'\)\)\}<\/th><th scope="col">\$\{esc\(debugGraphCostText\('debug\.modelTokens\.label', 'Tokens'\)\)\}<\/th><th scope="col">\$\{esc\(debugGraphCostText\('debug\.cost\.priceColumn', 'Price'\)\)\}<\/th><\/tr><\/thead>/);
  // Input/Cache/Output in tbody, Total in tfoot, via the shared row shape.
  assert.match(summaryFn, /<tbody>\$\{compactRows\.slice\(0, 3\)\.map\(summaryRowHtml\)\.join\(''\)\}<\/tbody>/);
  assert.match(summaryFn, /<tfoot>\$\{summaryRowHtml\(compactRows\[3\]\)\}<\/tfoot>/);
  // Row shape: <th scope="row"> keeps the per-usage explain-attrs owner; prices stay concise
  // (default-omit debugGraphCostPricePairHtml => amount only, basis is NOT repeated per row).
  assert.match(summaryFn, /const summaryRowHtml = \(\[label, value, apiListValue, tokenCount\]\) => \{/);
  assert.match(summaryFn, /<th scope="row"\$\{debugGraphCostUsageColumnHeaderAttrs\(key, rowLabel\)\}>/);
  assert.match(summaryFn, /<td>\$\{value === null \? '—' : debugGraphCostPricePairHtml\(value, apiListValue\)\}<\/td>/);
  // Basis "At API list prices" is stated once in the section heading, not in the table.
  assert.match(summaryFn, /js-debug-cost-estimate">\(\$\{esc\(heading\)\}\)/);
  assert.match(summaryFn, /debug\.cost\.atApiListPrices/);
  // Surrounding chrome preserved: head, Refresh, close, range/backfill status, More Info.
  assert.match(summaryFn, /class="js-debug-chart-head"/);
  assert.match(summaryFn, /data-js-debug-cost-details/);
  // Old compact definition-list structure is gone.
  assert.doesNotMatch(summaryFn, /js-debug-cost-compact/);
  assert.doesNotMatch(summaryFn, /<dl /);
  assert.doesNotMatch(summaryFn, /js-debug-cost-token-count/);
  // Dead compact-card CSS was removed with the DOM.
  assert.doesNotMatch(css, /\.js-debug-cost-compact/);
  assert.doesNotMatch(css, /\.js-debug-cost-token-count/);
});

test('the YO!cost report renders one shared always-visible column legend with translated terse glosses', () => {
  const copyStart = source.indexOf('const debugGraphCostUsageColumnCopy = Object.freeze(');
  const copyEnd = source.indexOf('\nfunction debugGraphCostUsageColumnHeaderAttrs(', copyStart);
  const report = sourceFunction('debugGraphCostReportHtml', 'debugGraphCostSummaryHtml');
  const legend = sourceFunction('debugGraphCostUsageColumnLegendHtml', 'debugGraphCostUsageColumnHeaderAttrs');
  assert.notEqual(copyStart, -1, 'one shared copy owner exists');
  assert.notEqual(copyEnd, -1, 'shared copy owner precedes header attrs');
  const copy = source.slice(copyStart, copyEnd);
  for (const key of ['input', 'cache_read', 'cache_write', 'cache_write_5m', 'cache_write_1h', 'output', 'other', 'total']) {
    assert.match(copy, new RegExp(`${key}: Object\\.freeze\\(\\{description:`), `${key} has the long description in the shared owner`);
    const glossKey = key === 'cache_read' ? 'debug.modelTokens.cacheRead' : key === 'cache_write' ? 'debug.cost.cacheWrite.gloss' : key === 'cache_write_5m' ? 'debug.cost.cacheWrite5m.gloss' : key === 'cache_write_1h' ? 'debug.cost.cacheWrite1h.gloss' : `debug.cost.${key}.gloss`;
    assert.match(copy, new RegExp(glossKey.replaceAll('.', '\\.'), 'u'), `${key} has the terse gloss in the shared owner`);
    assert.match(legend, new RegExp(`js-debug-cost-usage-swatch--\\$\\{esc\\(key\\)\\}`), 'legend uses the matching swatch');
  }
  assert.match(report, /<p class="js-debug-cost-report-totals"[\s\S]*?\$\{debugGraphCostUsageColumnLegendHtml\(\)\}[\s\S]*?\$\{debugGraphCostTmuxBreakdownHtml/);
  assert.equal((report.match(/debugGraphCostUsageColumnLegendHtml\(\)/g) || []).length, 1, 'report renders the legend once, not per table');
  assert.match(legend, /<dl class="js-debug-cost-column-legend" data-js-debug-cost-column-legend/);
  assert.match(css, /\.js-debug-cost-column-legend \{[\s\S]*?display: flex;[\s\S]*?flex-wrap: wrap;/);
  assert.match(css, /@container \(max-width: 34rem\) \{[\s\S]*?\.js-debug-cost-column-legend \{[\s\S]*?display: grid;[\s\S]*?repeat\(2, minmax\(0, 1fr\)\)/);
  for (const name of fs.readdirSync('static_src/locales').filter(name => name.endsWith('.json'))) {
    const catalog = JSON.parse(fs.readFileSync(`static_src/locales/${name}`, 'utf8'));
    for (const key of ['input', 'output', 'other', 'total']) assert.ok(String(catalog[`debug.cost.${key}.gloss`] || '').trim(), `${name} has debug.cost.${key}.gloss`);
    assert.ok(String(catalog['debug.cost.cacheWrite.gloss'] || '').trim(), `${name} has debug.cost.cacheWrite.gloss`);
    assert.ok(String(catalog['debug.modelTokens.cacheRead'] || '').trim(), `${name} has debug.modelTokens.cacheRead`);
    assert.ok(String(catalog['debug.modelTokens.cacheWrite'] || '').trim(), `${name} has debug.modelTokens.cacheWrite`);
  }
});

test('the YO!stats Daemons subtab keeps the system key and every locale carries a non-empty label', () => {
  const path = require('node:path');
  // The subtab wiring keeps the internal `system` key; only the human label changed.
  assert.match(source, /debugSubTabButtonHtml\('system', t\('debug\.tab\.services'\)\)/);
  const built = path.join('static', 'locales');
  const shipped = fs.readdirSync(built).filter(name => name.endsWith('.json'));
  assert.ok(shipped.length >= 20, `expected all shipped locales, saw ${shipped.length}`);
  for (const name of shipped) {
    const catalog = JSON.parse(fs.readFileSync(path.join(built, name), 'utf8'));
    const label = catalog['debug.tab.services'];
    assert.ok(typeof label === 'string' && label.trim().length > 0, `${name} debug.tab.services present/non-empty`);
    assert.doesNotMatch(label, /^Services$/, `${name} debug.tab.services no longer the old "Services" label`);
  }
  assert.equal(JSON.parse(fs.readFileSync(path.join(built, 'en.json'), 'utf8'))['debug.tab.services'], 'Daemons');
});

test('the YO!cost report shows one always-visible column legend sharing the description owner', () => {
  // Legend rendered once in the report body, above the usage tables.
  const reportFn = sourceFunction('debugGraphCostReportHtml', 'debugGraphCostSummaryHtml');
  const legendCalls = reportFn.match(/debugGraphCostUsageColumnLegendHtml\(\)/g) || [];
  assert.equal(legendCalls.length, 1, 'legend rendered exactly once per report');
  // The legend renders a swatch (except Total) + label + terse gloss for each column, and reuses
  // the shared header explain-attrs so the full description stays as the hover tooltip.
  const legendFn = sourceFunction('debugGraphCostUsageColumnLegendHtml', 'debugGraphCostUsageColumnHeaderAttrs');
  assert.match(legendFn, /class="js-debug-cost-column-legend"/);
  assert.match(legendFn, /\['input', 'cache_read', 'cache_write', 'output', 'other', 'total'\]/);
  assert.match(legendFn, /js-debug-cost-usage-swatch--\$\{esc\(key\)\}/);
  assert.match(legendFn, /debugGraphCostUsageColumnHeaderAttrs\(key, labels\[key\]\)/);
  assert.match(legendFn, /debugGraphCostUsageColumnGloss\(key\)/);
  assert.match(legendFn, /js-debug-cost-usage-swatch--\$\{esc\(key\)\}/); // Total uses the matching swatch too
  // Gloss owner shares the same keys as the description owner (one wording source, terse layer).
  for (const key of ['input', 'cache_read', 'cache_write', 'output', 'other', 'total']) {
    const translated = key === 'cache_read' ? 'debug\\.modelTokens\\.cacheRead' : key === 'cache_write' ? 'debug\\.cost\\.cacheWrite\\.gloss' : `debug\\.cost\\.${key}\\.gloss`;
    assert.match(source, new RegExp(`${key}: Object\\.freeze\\(\\{description:[^\\n]+${translated}`), `gloss owner has ${key}`);
  }
  // Tight legend CSS: bounded responsive grid, compact font, no fixed width.
  assert.match(css, /\.js-debug-cost-column-legend \{[\s\S]*?display: flex;[\s\S]*?flex-wrap: wrap;/);
});

test('YO!cost keeps exact formulas inside Cost by Model and removes the duplicate calculation table', () => {
  const report = sourceFunction('debugGraphCostReportHtml', 'debugGraphCostSummaryHtml');
  const usageTable = sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml');
  const formula = sourceFunction('debugGraphCostModelFormulaCellHtml', 'debugGraphCostSourceLabel');
  assert.doesNotMatch(report, /debugGraphCostComponentDetailsHtml|data-js-debug-cost-table="calculation"/);
  assert.match(usageTable, /debugGraphCostModelFormulaCellHtml\(components, row, item\)/);
  assert.match(formula, /cache_write_5m.*cache_write_1h/);
  assert.match(formula, /x \$\{esc\(debugGraphCostComponentRateText\(row\)\)\} =/);
  assert.match(formula, /js-debug-cost-model-formula/);
});

test('YO!cost keeps Cost by Model and Cost by Agent on one cache-write grid', () => {
  const gridStart = source.indexOf('const DEBUG_GRAPH_COST_USAGE_COLUMN_KEYS = Object.freeze(');
  const gridEnd = source.indexOf('\nfunction debugGraphCostPricingSourceEntries(', gridStart);
  assert.notEqual(gridStart, -1, 'shared cache-write grid exists');
  assert.notEqual(gridEnd, -1, 'shared cache-write grid ends before the next owner');
  const sharedGrid = source.slice(gridStart, gridEnd);
  const context = {result: null};
  vm.runInNewContext(`
    const debugGraphCostText = (_key, fallback) => fallback;
    const debugGraphCostUsageColumnLabel = key => ({cache_read: 'Cache read', cache_write: 'Cache write', cache_write_5m: '5m cache write', cache_write_1h: '1h cache write'})[key] || key;
    const debugGraphCostInteger = value => Math.max(0, Number(value) || 0);
    const debugGraphCostApiListMicroUsd = row => Math.max(0, Number(row?.api_list_micro_usd) || 0);
    ${sharedGrid}
    const anthro = {provider: 'anthropic', cache_write_5m_tokens: 5, cache_write_1h_tokens: 7, cache_write_5m_micro_usd: 50, cache_write_1h_micro_usd: 140};
    const openai = {provider: 'openai', cache_write_tokens: 9, cache_write_micro_usd: 90};
    result = {
      columns: debugGraphCostUsageColumns().map(column => column.key),
      anthro: debugGraphCostBreakdownItems(anthro, {kind: 'model'}),
      openai: debugGraphCostBreakdownItems(openai, {kind: 'model'}),
      agent: debugGraphCostBreakdownItems(anthro, {kind: 'agent'}),
      total: debugGraphCostBreakdownItems(anthro, {kind: 'agent', total: true}),
    };
  `, context);
  assert.deepEqual([...context.result.columns], ['input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other']);
  const byKey = rows => Object.fromEntries(rows.map(row => [row.key, row]));
  const anthro = byKey(context.result.anthro);
  const openai = byKey(context.result.openai);
  const agent = byKey(context.result.agent);
  const total = byKey(context.result.total);
  assert.equal(anthro.cache_write_5m.tokens, 5);
  assert.equal(anthro.cache_write_1h.tokens, 7);
  assert.equal(openai.cache_write.tokens, 9);
  assert.equal(openai.cache_write.columnSpan, 2);
  assert.equal(agent.cache_write.tokens, 12);
  assert.equal(agent.cache_write.columnSpan, 2);
  assert.equal(total.cache_write_5m.tokens, 5);
  assert.equal(total.cache_write_1h.tokens, 7);
  const usageTable = sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml');
  assert.match(usageTable, /rowspan="2"/);
  assert.match(usageTable, /colSpan: 2/);
  assert.match(usageTable, /item\.columnSpan/);
  assert.match(sourceFunction('debugGraphCostModelUsageChartHtml', 'debugGraphCostComponentRateText'), /Cost by Model/);
  assert.match(sourceFunction('debugGraphCostTmuxBreakdownHtml', 'debugGraphCostReportHtml'), /Cost by Agent/);
});

test('Cost by Agent sorts by the canonical displayed agent name', () => {
  const sorter = sourceFunction('debugGraphCostAgentRowsAlphabetically', 'debugGraphCostTmuxBreakdownRows');
  const context = {result: null};
  vm.runInNewContext(`
    const debugGraphCostTmuxLabel = row => row.label;
    const debugGraphAgentDisplayLabel = value => String(value).split('|')[0];
    ${sorter}
    result = debugGraphCostAgentRowsAlphabetically([
      {label: 'zeta|0|codex'},
      {label: 'Agent-10|0|codex'},
      {label: 'agent-2|0|codex'},
      {label: 'Beta|0|codex'},
    ]).map(row => debugGraphAgentDisplayLabel(row.label));
  `, context);
  assert.deepEqual([...context.result], ['agent-2', 'Agent-10', 'Beta', 'zeta']);
  const table = sourceFunction('debugGraphCostTmuxBreakdownHtml', 'debugGraphCostTranscriptPath');
  assert.match(table, /debugGraphCostAgentRowsAlphabetically/);
});

test('Cost by Agent keeps names compact and labels the footer succinctly', () => {
  const helper = sourceFunction('debugGraphCostAgentLabelHtml', 'debugGraphCostSourceLabelHtml');
  const context = {result: null};
  vm.runInNewContext(`
    const esc = value => String(value);
    ${helper}
    result = {
      short: debugGraphCostAgentLabelHtml('122_frontend-crates'),
      long: debugGraphCostAgentLabelHtml('123_an-extremely-long-project-name-that-needs-a-compact-tail'),
    };
  `, context);
  assert.match(context.result.short, /js-debug-cost-agent-name/);
  assert.doesNotMatch(context.result.short, /--long/);
  assert.match(context.result.long, /js-debug-cost-agent-name--long/);
  assert.match(context.result.long, /…/);
  assert.match(sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml'), /grandTotalApiList/, 'footer uses the localized succinct label');
  assert.equal(localeEn['debug.cost.grandTotalApiList'], 'Grand total');
  assert.match(css, /\.js-debug-cost-agent-name--long\s*\{[\s\S]*display: inline-grid;[\s\S]*line-height: 1\.1;/);
});

test('YO!cost usage metrics and compact pricing links stay on one line', () => {
  const cell = sourceFunction('debugGraphCostUsageTableCellHtml', 'debugGraphCostExactTotalRow');
  const pricing = sourceFunction('debugGraphCostPricingLinksHtml', 'debugGraphCostAllPricingSourcesHtml');
  assert.match(cell, /js-debug-cost-table-metric js-debug-cost-table-metric--inline/);
  assert.match(pricing, /js-debug-cost-pricing-links--compact/);
  assert.match(pricing, /compact \? '\$' : label/);
  assert.match(pricing, /title="\$\{esc\(`\$\{label\} pricing`\)\}"/);
  assert.match(css, /\.js-debug-cost-table-metric--inline\s*\{[\s\S]*?display: inline-flex;[\s\S]*?white-space: nowrap;/);
  assert.match(css, /\.js-debug-cost-table-metric--inline \.js-debug-cost-price-pair\s*\{[\s\S]*?display: inline-flex;/);
  assert.match(css, /\.js-debug-cost-model-copy\s*\{[\s\S]*?display: flex;[\s\S]*?white-space: nowrap;/);
  assert.match(css, /\.js-debug-cost-pricing-links--compact::before,[\s\S]*?\.js-debug-cost-pricing-links--compact::after\s*\{[\s\S]*?content: none;/);
});

Promise.all(pending).then(() => {
  console.log(`stats current panel suite: ${passed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
});

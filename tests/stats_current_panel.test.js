// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = [
  'static_src/js/yomux/84_debug_observation.js',
  'static_src/js/yomux/85_debug_panel.js',
].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const graphDefaultsSource = source.slice(
  source.indexOf('const jsDebugGraphDefaultHiddenChartKeys ='),
  source.indexOf('\nconst jsDebugGraphMovingAverageSamples', source.indexOf('const jsDebugGraphDefaultHiddenChartKeys =')),
);
const currentSource = fs.readFileSync('static_src/js/yomux/84_stats_current.js', 'utf8');
const agentActivitySource = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
const debugRuntimeSource = fs.readFileSync('static_src/js/yomux/84_debug_runtime_facade.js', 'utf8');
const bootstrapSource = fs.readFileSync('static_src/js/yomux/00_bootstrap_state.js', 'utf8');
const coreSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
const apiFetchStart = coreSource.indexOf('async function apiFetch(');
const apiFetchEnd = coreSource.indexOf('\nfunction ', apiFetchStart);
const apiFetchSource = coreSource.slice(apiFetchStart, apiFetchEnd);
const editorSettingsSource = fs.readFileSync('static_src/js/yomux/50_editor_settings_runtime.js', 'utf8');
const tokenCss = fs.readFileSync('static_src/css/yomux/00_tokens_base.css', 'utf8');
const lifecycleScopeSource = coreSource.slice(
  coreSource.indexOf('function createLifecycleScope('),
  coreSource.indexOf('\nfunction createLatestResource(', coreSource.indexOf('function createLifecycleScope(')),
);

const capabilityRequestSource = bootstrapSource.slice(
  bootstrapSource.indexOf('function capabilityNameForRequest('),
  bootstrapSource.indexOf('\nconst statsWriterFence', bootstrapSource.indexOf('function capabilityNameForRequest(')),
);
const canonicalJsonValueSource = bootstrapSource.slice(
  bootstrapSource.indexOf('function canonicalJsonValue('),
  bootstrapSource.indexOf('\nfunction statsOnlyBootEnabled(', bootstrapSource.indexOf('function canonicalJsonValue(')),
);
const terminalSource = [
  'static_src/js/yomux/99_terminal_boot.js',
  'static_src/js/yomux/99_client_event_transport.js',
  'static_src/js/yomux/99_terminal_shortcuts_boot.js',
].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const activityFreshnessSource = agentActivitySource.slice(
  agentActivitySource.indexOf('function agentWindowActivitySnapshotFresh('),
  agentActivitySource.indexOf('\nfunction ', agentActivitySource.indexOf('function agentWindowActivitySnapshotFresh(') + 1),
);
const css = fs.readFileSync('static_src/css/yomux/30_preferences_changes.css', 'utf8');
const localeEn = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));
let passed = 0;
let failed = 0;
const pending = [];

test('bootstrap capability mapping keeps bootstrap, Tabber, agent, and YO routes distinct', () => {
  const context = {URL, globalThis: {location: {href: 'https://example.test/'}}};
  vm.runInNewContext(`${capabilityRequestSource}\nresult = [
    capabilityNameForRequest('/api/session-metadata?force=1'),
    capabilityNameForRequest('/api/transcripts?session=1'),
    capabilityNameForRequest('/api/activity?scope=all'),
    capabilityNameForRequest('/api/yoagent/status?scope=all'),
    capabilityNameForRequest('/api/yolo-rules/open'),
  ];`, context);
  assert.deepEqual([...context.result], [
    'core.bootstrap.v1',
    'core.bootstrap.v1',
    'pane.tabber.v1',
    'pane.agent.v1',
    'yolo.auto-approve.v1',
  ]);
});

test('stale Go activity snapshots are not adopted as current agent status', () => {
  const context = {result: null};
  vm.runInNewContext(`${activityFreshnessSource}\nresult = [
    agentWindowActivitySnapshotFresh({cache: {stale: false}}),
    agentWindowActivitySnapshotFresh({cache: {stale: true}}),
    agentWindowActivitySnapshotFresh({}),
  ];`, context);
  assert.deepEqual([...context.result], [true, false, true]);
  assert.match(terminalSource, /function adoptTabberActivityAsAutoStatusFallback\(options = \{\}\) \{[\s\S]*agentWindowActivitySnapshotFresh\(payload\)/);
  assert.match(agentActivitySource, /let activityRows = agentWindowActivitySnapshotFresh\(tabberActivityPayload\)/);
});

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

testAsync('capability-gated requests report full-mode failures but stay quiet in stats-only boot', async () => {
  async function request(statsOnly) {
    const apiEvents = [];
    const deliveries = [];
    const backendHealth = [];
    const context = {
      AbortController,
      Response,
      clearTimeout() {},
      setTimeout() { throw new Error('a synthetic capability response must not start a request deadline'); },
      terminalAuthenticationActive: () => false,
      capabilityNameForRequest: () => 'pane.agent.v1',
      capabilityAvailable: () => false,
      capabilityReason: () => ({code: 'not_implemented', messageKey: 'capabilityUnavailable', messageParams: {}}),
      pageTransportLifecycle: {
        begin: () => 7,
        noteDelivery: () => deliveries.push('delivery'),
        reasonSince: () => '',
      },
      statsOnlyBootEnabled: () => statsOnly,
      apiFetchDeadlineMs: () => 1000,
      apiFetchResponseWithDeadline: response => response,
      noteBackendHealthFailure: () => backendHealth.push('failure'),
      noteBackendHealthSuccess: () => backendHealth.push('success'),
      jsDebugPerformanceNow: () => 10,
      jsDebugRequestMethod: () => 'GET',
      jsDebugRequestBytes: () => 0,
      applyApiRequestIdHeader: () => 'request-1',
      notePageLoadApiStarted() {},
      notePageLoadApiCompleted() {},
      recordApiDebugEvent: (_url, _method, _startedAt, event) => {
        apiEvents.push(event);
        return event;
      },
      recordApiDebugResponseBytes() {},
      noteApiDebugHeaders() {},
      fetch() { throw new Error('an unavailable route must be gated without network access'); },
    };
    vm.runInNewContext(`${apiFetchSource}\nresult = apiFetch('/api/activity-summary');`, context);
    const response = await context.result;
    return {response, apiEvents, deliveries, backendHealth};
  }

  const fullMode = await request(false);
  assert.equal(fullMode.response.status, 501);
  assert.equal(fullMode.response.headers.get('X-YOmux-Capability-Gated'), 'pane.agent.v1');
  assert.equal(fullMode.apiEvents.length, 1, 'full mode retains typed unsupported-route evidence');
  assert.equal(fullMode.apiEvents[0].status, 501);
  assert.equal(fullMode.apiEvents[0].ok, false);
  assert.deepEqual(fullMode.deliveries, [], 'the synthetic response is not counted as a backend delivery');
  assert.deepEqual(fullMode.backendHealth, [], 'the capability gate does not change backend health');

  const statsOnly = await request(true);
  assert.equal(statsOnly.response.status, 501, 'the caller still receives the typed unavailable response');
  assert.equal(statsOnly.apiEvents.length, 0, 'an expected stats-only gate is not reported as an API failure');
  assert.deepEqual(statsOnly.deliveries, []);
  assert.deepEqual(statsOnly.backendHealth, []);
});

function sourceFunction(name, nextName) {
  const start = source.indexOf(`function ${name}(`);
  const end = Math.min(
    ...[`\nfunction ${nextName}(`, `\nasync function ${nextName}(`]
      .map(marker => source.indexOf(marker, start))
      .filter(index => index >= 0),
  );
  assert.notEqual(start, -1, `${name} exists`);
  assert.notEqual(end, -1, `${nextName} follows ${name}`);
  const body = source.slice(start, end);
  if (!body.includes('debugDisplayProductLabel(')) return body;
  const helperStart = source.indexOf('function debugDisplayProductLabel(');
  const helperEnd = source.indexOf('\nfunction applyJsDebugStatsSamplePush(', helperStart);
  assert.notEqual(helperStart, -1, 'debugDisplayProductLabel exists');
  assert.notEqual(helperEnd, -1, 'debugDisplayProductLabel ends before stats push handling');
  return `${source.slice(helperStart, helperEnd)}\n${body}`;
}

test('host CPU and memory graphs label partial data and keep missing processes unknown', () => {
  const context = {
    debugGraphLocalizedLabel: group => group.label,
    cpuTopology: {logicalCpus: 8, physicalCores: 4},
    result: null,
  };
  vm.runInNewContext(`${sourceFunction('debugGraphChartLabel', 'debugGraphChartShellHtml')}
    result = [
      debugGraphChartLabel({key: 'cpu', label: 'CPU'}, [{hostMetrics: {processCensusIncompleteCpu: true}}]),
      debugGraphChartLabel({key: 'memory', label: 'Memory'}, [{hostMetrics: {processCensusIncompleteMemory: true}}]),
    ];`, context);
  assert.deepEqual([...context.result], [
    'CPU (8 logical CPUs / 4 physical cores) (partial process data)',
    'Memory (partial process data)',
  ]);

  const familyContext = {result: null};
  vm.runInNewContext(`${sourceFunction('debugGraphHostProcessFamilyHasData', 'debugGraphAnyGpuDeviceSamplesCached')}
    const missingProcess = 'pid:42';
    result = [
      debugGraphHostProcessFamilyHasData({hostMetrics: {cpuProcesses: new Map([['pid:7', {}]]), processCensusIncompleteCpu: true}}, 'cpuProcesses', missingProcess, 'processCensusIncompleteCpu'),
      debugGraphHostProcessFamilyHasData({hostMetrics: {cpuProcesses: new Map([['pid:7', {}]])}}, 'cpuProcesses', missingProcess, 'processCensusIncompleteCpu'),
    ];`, familyContext);
  assert.deepEqual([...familyContext.result], [false, true]);

  const unknownContext = {result: null};
  vm.runInNewContext(`${sourceFunction('debugGraphHostProcessFamilyHasUnknownData', 'debugGraphAnyGpuDeviceSamplesCached')}
    result = [
      debugGraphHostProcessFamilyHasUnknownData({hostMetrics: {memoryProcesses: new Map(), processCensusIncompleteMemory: true}}, 'memoryProcesses', 'pid:42', 'processCensusIncompleteMemory'),
      debugGraphHostProcessFamilyHasUnknownData({hostMetrics: {memoryProcesses: new Map([['pid:42', {}]]), processCensusIncompleteMemory: true}}, 'memoryProcesses', 'pid:42', 'processCensusIncompleteMemory'),
    ];`, unknownContext);
  assert.deepEqual([...unknownContext.result], [true, false]);

  const recordContext = {result: null};
  vm.runInNewContext(`${sourceFunction('jsDebugCurrentSeriesValue', 'jsDebugCurrentCpuProjectionValue')}
    ${sourceFunction('jsDebugCurrentCpuProjectionValue', 'jsDebugCurrentServiceLoadItem')}
    ${sourceFunction('jsDebugCurrentBucketRecord', 'jsDebugCurrentSnapshotAgentWindowRevision')}
    result = jsDebugCurrentBucketRecord({start: 10, duration: 1, series: {
      'process_cpu_percent:python': {value: 8},
      'process_memory_bytes:python': {value: 4096},
      'process_census_incomplete:cpu': {value: 1},
      'process_census_incomplete:system_memory': {value: 1},
    }});`, recordContext);
  assert.deepEqual(JSON.parse(JSON.stringify(recordContext.result.host_metrics)), {
    cpu_processes: {python: {label: 'python', total_percent: 8, samples: 1}},
    memory_processes: {python: {label: 'python', total_bytes: 4096, samples: 1}},
    gpu_devices: {}, disk_mounts: {}, service_load: {},
    process_census_incomplete_cpu: true,
    process_census_incomplete_memory: true,
  });
});

test('partial host process data stays visible and memory joins the System graph', () => {
  const extract = (name, nextName) => {
    const start = currentSource.indexOf(`function ${name}(`);
    const end = currentSource.indexOf(`\n  function ${nextName}(`, start);
    assert.notEqual(start, -1, `${name} exists`);
    assert.notEqual(end, -1, `${nextName} follows ${name}`);
    return currentSource.slice(start, end);
  };
  const groupContext = {CURRENT_STATS_SERIES_METADATA: {}};
  vm.runInNewContext(`${extract('currentStatsSeriesMetadata', 'currentStatsSeriesUnit')}
    ${extract('currentStatsSeriesGroup', 'currentStatsGroupDefinitions')}
    result = [
      currentStatsSeriesGroup('process_cpu_percent:python', 'stats', null),
      currentStatsSeriesGroup('process_memory_bytes:python', 'stats', null),
      currentStatsSeriesGroup('process_census_incomplete:cpu', 'stats', null),
    ];`, groupContext);
  assert.deepEqual([...groupContext.result], ['cpu', 'system', null]);

  const chartContext = {
    CURRENT_STATS_SVG_WIDTH: 500, CURRENT_STATS_SVG_HEIGHT: 300,
    CURRENT_STATS_PLOT_LEFT: 30, CURRENT_STATS_PLOT_RIGHT: 10,
    CURRENT_STATS_PLOT_TOP: 10, CURRENT_STATS_PLOT_BOTTOM: 24,
    currentStatsEscape: value => String(value), currentStatsNumber: value => String(value),
    currentStatsPath: () => ({path: '', points: []}), currentStatsTime: () => '00:00',
    currentStatsMetric: value => String(value), currentStatsChartUnit: () => 'percent',
    result: null,
  };
  vm.runInNewContext(`${extract('currentStatsChartHtml', 'currentStatsPath')}
    result = currentStatsChartHtml({
      definition: {id: 'cpu', title: 'CPU', families: ['cpu']},
      series: new Map(), gaps: [],
      generation: {resolution_seconds: 1, buckets: [{series: {
        'process_census_incomplete:cpu': {value: 1},
      }}]},
      frame: {window_start: 0, window_end: 10, range_seconds: 10},
      maximum: 100, rendererId: 1,
    });`, chartContext);
  assert.match(chartContext.result, /CPU \(partial process data\)/);
  assert.match(chartContext.result, /Some process readings are unknown; readable processes remain shown\./);
});

test('stats renderer prefers the canonical module and accepts the legacy alias', () => {
  const canonical = {name: 'canonical'};
  const legacy = {name: 'legacy'};
  const context = vm.createContext({YOmuxStatsCurrent: canonical, YOLOmuxStatsCurrent: legacy});
  vm.runInContext(sourceFunction('debugGraphStatsCurrentModule', 'debugGraphAgentDisplayLabel'), context);
  assert.equal(context.debugGraphStatsCurrentModule().name, 'canonical');
  context.YOmuxStatsCurrent = null;
  assert.equal(context.debugGraphStatsCurrentModule().name, 'legacy');
});

test('CPU chart title distinguishes logical CPUs from physical cores', () => {
  const context = {
    cpuTopology: {logicalCpus: 32, physicalCores: 24},
    debugGraphLocalizedLabel: () => 'CPU',
    result: null,
  };
  vm.runInNewContext(`
    ${sourceFunction('debugGraphChartLabel', 'debugGraphChartShellHtml')}
    result = debugGraphChartLabel({key: 'cpu'});
  `, context);
  assert.equal(context.result, 'CPU (32 logical CPUs / 24 physical cores)');
});

// A bounded region between two literal needles, for the constants a sliced function depends on.
function slice(text, startNeedle, endNeedle) {
  const start = text.indexOf(startNeedle);
  assert.notEqual(start, -1, `${startNeedle} exists`);
  const end = text.indexOf(endNeedle, start);
  assert.notEqual(end, -1, `${endNeedle} follows ${startNeedle}`);
  return text.slice(start, end);
}

const clientCapabilityGuardSource = bootstrapSource.slice(
  bootstrapSource.indexOf('function clientCanUseUnscopedHostRequests()'),
  bootstrapSource.indexOf('\nfunction randomBrowserInstanceId()', bootstrapSource.indexOf('function clientCanUseUnscopedHostRequests()')),
);

function clientCapabilityFixtureSource(unscopedHostRequests) {
  return `
    const clientCapabilityState = Object.freeze({unscopedHostRequests: ${JSON.stringify(unscopedHostRequests === true)}});
    ${clientCapabilityGuardSource}
  `;
}

test('the established Graph API-SSE System Logs shell remains the renderer owner', () => {
  assert.match(source, /function debugPanelHtml\(\)/);
  assert.match(source, /debugSubTabButtonHtml\('graph'/);
  assert.match(source, /debugSubTabButtonHtml\('events'/);
  assert.match(source, /debugSubTabButtonHtml\('system'/);
  assert.match(source, /debugSubTabButtonHtml\('logs'/);
  assert.match(source, /function debugGraphInnerHtml\(/);
  const graphControls = sourceFunction('debugGraphControlsHtml', 'debugGraphLocalDateKey');
  const graphInner = sourceFunction('debugGraphInnerHtml', 'debugGraphHtml');
  assert.doesNotMatch(graphControls, /debugGraphRefillControlsHtml\(\)/, 'refill is not in the graph toolbar');
  assert.match(graphInner, /data-js-debug-graph-body>[\s\S]*?<\/div>\$\{debugGraphRefillControlsHtml\(\)\}/, 'refill follows the complete chart body');
  assert.match(source, /function debugSystemInnerHtml\(/);
  assert.match(source, /function debugLogsInnerHtml\(/);
  assert.doesNotMatch(source, /data-stats-current-view/);
  assert.equal(fs.existsSync('static_src/js/yomux/83_stats_panel.js'), false);
});

test('Logs normalizes browser and server records without duplicate IDs or unsafe extra fields', () => {
  const secret = 'fixture-access-token-never-log';
  const tokenized = label => `${label}?token=${secret}`;
  const classifierContext = {result: null, jsDebugLogLevels: ['info', 'warning', 'debug', 'error']};
  vm.runInNewContext(`
    ${sourceFunction('debugEventDetailText', 'debugClientLogLevel')}
    ${sourceFunction('debugClientLogLevel', 'recordJsDebugStatsDiagnostic')}
    const event = {type: 'client_failure', error: 'graph activity failed'};
    result = {level: debugClientLogLevel(event), detail: debugEventDetailText(event)};
  `, classifierContext);
  assert.deepEqual({...classifierContext.result}, {level: 'error', detail: 'graph activity failed'});
  const modelSource = source.slice(
    source.indexOf('function debugClientLogRecord('),
    source.indexOf('\nfunction debugLogTimeText('),
  );
  const clipboardSource = source.slice(
    source.indexOf('function debugLogTimeText('),
    source.indexOf('\nfunction debugLogsCopyButtonLabel('),
  );
  const pacificTimeStart = coreSource.indexOf('const diagnosticPacificTimeFormatter');
  const pacificTimeSource = coreSource.slice(
    pacificTimeStart,
    coreSource.indexOf('\nfunction redactDiagnosticSecretText(', pacificTimeStart),
  );
  const context = {
    Date,
    Intl,
    Number,
    Object,
    String,
    Set,
    result: null,
    jsDebugLogLevels: ['info', 'warning', 'debug', 'error'],
    jsDebugClientLogEpoch: 'client-epoch',
    jsDebugLogsState: {
      serverEpoch: 'server-epoch',
      clearedCursors: {server: null, client: null},
      levels: new Set(['warning', 'error']),
      payload: [
        {id: 7, timestamp: 100, level: 'error', source: 'watchd', category: 'transport', message: tokenized('timeout'), request_id: tokenized('r-watchd'), route: tokenized('/api/fs/watch-diff'), event_type: tokenized('watch-update'), delivery_outcome: tokenized('timeout'), unsafe: secret},
        {id: 7, timestamp: 100, level: 'error', source: 'watchd', category: 'transport', message: 'duplicate', request_id: 'r-watchd'},
      ],
    },
    jsDebugEvents: [
      {id: 9, ts: '1970-01-01T00:01:41.000Z', type: 'stats_history', level: 'warning', source: '/stats/current', message: tokenized('graph stalled'), requestId: tokenized('r-graph'), route: tokenized('/stats/current'), eventType: tokenized('graph-activity'), deliveryOutcome: tokenized('stalled'), unsafe: secret},
    ],
    debugClientLogLevel: event => event.level,
    debugEventDetailText: event => event.message,
    debugEventStatusText: () => '',
    debugPhaseTimingText: () => '',
  };
  const diagnosticRedactorSource = coreSource.slice(
    coreSource.indexOf('function redactDiagnosticSecretText('),
    coreSource.indexOf('\nfunction recordJsDebugEvent('),
  );
  vm.runInNewContext(`
    ${diagnosticRedactorSource}
    ${pacificTimeSource}
    ${modelSource}
    ${clipboardSource}
    const records = debugMergedLogRecords();
    result = {records, clipboard: debugLogsTextForClipboard()};
  `, context);
  assert.equal(context.result.records.length, 2);
  assert.deepEqual([...context.result.records.map(record => record.id)], ['client:9', 'server:7']);
  assert.equal(JSON.stringify(context.result).includes(secret), false);
  assert.match(JSON.stringify(context.result.records), /\[redacted-secret\]/);
  for (const record of context.result.records) {
    for (const field of ['message', 'requestId', 'route', 'event', 'delivery']) {
      assert.match(record[field], /\[redacted-secret\]/, `${record.id} redacts ${field}`);
    }
  }
  assert.match(context.result.records[0].message, /^graph stalled\?token=/);
  assert.match(context.result.records[0].requestId, /^r-graph\?token=/);
  assert.match(context.result.records[0].event, /^graph-activity\?token=/);
  assert.match(context.result.records[1].message, /^timeout\?token=/);
  assert.match(context.result.records[1].requestId, /^r-watchd\?token=/);
  assert.match(context.result.records[1].route, /^\/api\/fs\/watch-diff\?token=/);
  assert.match(context.result.clipboard, /request=r-graph/);
  assert.match(context.result.clipboard, /delivery=timeout/);
  assert.doesNotMatch(context.result.clipboard, new RegExp(`${secret}|duplicate`));
});

test('Logs formats failure evidence with an exact Pacific wall time', () => {
  const start = coreSource.indexOf('const diagnosticPacificTimeFormatter');
  const end = coreSource.indexOf('\nfunction jsDebugFailureEvents(', start);
  assert.notEqual(start, -1, 'shared Pacific diagnostic time owner exists');
  assert.notEqual(end, -1, 'Pacific diagnostic time owner precedes failure evidence');
  const fixedTime = Date.parse('2026-01-15T12:34:56.000Z');
  class FixedDate extends Date {
    static now() { return fixedTime; }
  }
  const context = {
    Date: FixedDate,
    Intl,
    Number,
    Object,
    String,
    jsDebugEventSeq: 0,
    jsDebugEvents: [],
    jsDebugEventLimit: 500,
    redactDiagnosticValue: value => value,
    scheduleJsDebugPanelRefresh: () => {},
    result: null,
  };
  vm.runInNewContext(`
    ${coreSource.slice(start, end)}
    result = {
      formatted: diagnosticPacificWallTime(${fixedTime}),
      event: recordJsDebugEvent('stats_history', {level: 'warning'}),
    };
  `, context);
  assert.equal(context.result.formatted, '2026-01-15 04:34:56 PST');
  assert.equal(context.result.event.ts, '2026-01-15T12:34:56.000Z');
  assert.equal(context.result.event.wallTime, '2026-01-15 04:34:56 PST');
});

test('the mixed browser diagnostic ring retains the newest 500 records in exact order', () => {
  const start = coreSource.indexOf('const diagnosticPacificTimeFormatter');
  const end = coreSource.indexOf('\nfunction jsDebugFailureClassification(', start);
  const context = {
    Date,
    Intl,
    Number,
    Object,
    String,
    jsDebugEventSeq: 0,
    jsDebugEvents: [],
    jsDebugEventLimit: 500,
    redactDiagnosticValue: value => value,
    scheduleJsDebugPanelRefresh: () => {},
    result: null,
  };
  vm.runInNewContext(`
    ${coreSource.slice(start, end)}
    const types = ['api', 'sse', 'client_failure', 'stats_history', 'heartbeat'];
    for (let index = 1; index <= 777; index += 1) {
      recordJsDebugEvent(types[(index - 1) % types.length], {ordinal: index});
    }
    result = jsDebugEvents.map(event => ({id: event.id, type: event.type, ordinal: event.ordinal}));
  `, context);
  assert.equal(context.result.length, 500);
  assert.deepEqual(context.result.map(event => event.id), Array.from({length: 500}, (_unused, index) => index + 278));
  assert.deepEqual(context.result.map(event => event.ordinal), context.result.map(event => event.id));
  assert.deepEqual(context.result.slice(0, 5).map(event => event.type), ['client_failure', 'stats_history', 'heartbeat', 'api', 'sse']);
  assert.equal(context.result.at(-1).type, 'sse');
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

test('CPU charts preserve multi-core peaks and expand their axis beyond 100%', () => {
  const axisSource = sourceFunction('debugGraphChartAxisMax', 'debugGraphChartCapacityMax');
  const cpuGroup = /\{key: 'cpu',[^\n]+/.exec(source)?.[0] || '';
  const systemCpu = /\{key: 'systemCpu',[^\n]+/.exec(source)?.[0] || '';
  assert.doesNotMatch(cpuGroup, /fixedMax: 100/);
  assert.doesNotMatch(systemCpu, /Math\.min\(100/);
  assert.match(systemCpu, /linePattern: 'dot'/, 'System CPU owns the dotted stroke identity');
  assert.match(systemCpu, /color: jsDebugGraphSeriesPalette\.systemCpu/, 'System CPU owns the semantic red palette entry');
  const context = {Number, Math, result: null, debugGraphNiceAxisMax: value => Math.ceil(value / 10) * 10};
  vm.runInNewContext(`${axisSource}\nresult = debugGraphChartAxisMax({unit: 'percent'}, 157.973);`, context);
  assert.equal(context.result, 160);
});

test('Services omits the duplicated web process while CPU names it clearly', () => {
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const visibleServiceSource = sourceFunction('debugGraphVisibleServiceLoadItems', 'debugGraphServiceLoadRangeAvailable');
  const serviceSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  assert.match(cpuSource, /debugDisplayProductLabel\(legacyWebPort && process\.label === processId/);
  assert.match(visibleServiceSource, /if \(key === 'web'/);
  assert.match(serviceSource, /debugGraphVisibleServiceLoadItems\(buckets, group\)/);
  // familyHasData must prove THIS service was censused-and-absent, not merely that some service
  // had data in the bucket -- the old form cleared a sparse service's held gauge on every
  // ordinary bucket, producing a synthetic sawtooth to zero. It must read this series' own key
  // out of the bucket's serviceLoad census map.
  assert.match(serviceSource, /familyHasData: bucket => \{/);
  assert.match(serviceSource, /source instanceof Map && source\.size > 0 && !\(debugGraphServiceLoadSampleCount\(source\.get\(key\), metric\) > 0\)/);
});

test('per-process RSS holds through sparse samples but becomes a gap when its census is incomplete', () => {
  // A normal sparse bucket retains its measured RSS. A partial census missing that process is
  // unknown and breaks the line; a complete census missing it confirms the process is gone.
  const processSeries = [
    sourceFunction('debugDisplayProductLabel', 'applyJsDebugStatsSamplePush'),
    slice(source, 'const jsDebugGraphHostProcessVisualAssignments = Object.freeze({', '\nfunction normalizedDebugGraphServiceLoadMode('),
  ].join('\n');
  const projectSeries = sourceFunction('debugGraphProjectSeriesSamples', 'debugGraphSeriesData');
  const processFamilySource = sourceFunction('debugGraphHostProcessFamilyHasData', 'debugGraphAnyGpuDeviceSamplesCached');
  const unknownFamilySource = sourceFunction('debugGraphHostProcessFamilyHasUnknownData', 'debugGraphAnyGpuDeviceSamplesCached');
  const context = {
    result: null,
    Map, Set, Number, String, Math, Boolean,
    jsDebugGraphAgentTokenColors: ['blue'],
    jsDebugGraphCpuProcessAreaColors: ['cyan'],
    jsDebugGraphServiceLoadLinePatterns: ['solid'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 120000},
    jsDebugGraphRawBucketMs: 5000,
    debugGraphStablePaletteIndex: (_key, count) => count - 1,
    debugGraphDisplayHoldOutage: bucket => Number(bucket?.disconnectedMs || 0) > 0,
    debugGraphHostMetricBucketValue: (bucket, series) => Number(bucket?.hostMetrics?.memoryProcesses?.get?.(series.hostProcessId)?.totalBytes || 0),
    debugGraphHostMetricBucketHasData: (bucket, series) => Number(bucket?.hostMetrics?.memoryProcesses?.get?.(series.hostProcessId)?.samples || 0) > 0,
    debugGraphHostMetricBucketItem: (bucket, series) => bucket?.hostMetrics?.memoryProcesses?.get?.(series.hostProcessId) || null,
  };
  vm.runInNewContext(`
    ${processSeries}
    ${processFamilySource}
    ${unknownFamilySource}
    ${projectSeries}
    const censusBucket = {startMs: 0, durationMs: 5000, hostMetrics: {memoryProcesses: new Map([['python', {label: 'python', totalBytes: 400, samples: 1}]])}};
    const def = debugGraphMemoryProcessSeriesDefs([censusBucket])[0];
    const observedBucket = censusBucket;
    const sparseBucket = {startMs: 5000, durationMs: 5000, hostMetrics: {memoryProcesses: new Map()}};
    const unknownBucket = {startMs: 10000, durationMs: 5000, hostMetrics: {memoryProcesses: new Map(), processCensusIncompleteMemory: true}};
    const censusMissBucket = {startMs: 20000, durationMs: 5000, hostMetrics: {memoryProcesses: new Map([['other', {label: 'other', totalBytes: 200, samples: 1}]])}};
    const projection = debugGraphProjectSeriesSamples(def, [observedBucket, sparseBucket, unknownBucket, sparseBucket, censusMissBucket]);
    result = {
      hasData: projection.hasDataValues,
      held: projection.provenanceValues.map(p => (p ? p.held : null)),
      values: projection.values,
    };
  `, context);
  const outcome = JSON.parse(JSON.stringify(context.result));
  assert.deepEqual(outcome.hasData, [true, true, false, false, false]);
  assert.deepEqual(outcome.held, [false, true, null, null, null]);
  assert.deepEqual(outcome.values, [400, 400, 0, 0, 0]);
});

test('CPU keeps the exact serving port as the only solid series and shows a gap, never promoting a peer', () => {
  // W5 truthfulness: the exact serving `port:N` is ALWAYS the one solid series even
  // with zero samples (it renders as an honest gap), a peer is NEVER promoted to
  // "current", and there is NO aggregate fallback. The old code promoted the newest
  // sampled owner to solid; these assertions are red against that behavior.
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const helpers = sourceFunction('debugGraphProcessCpuBucketValue', 'debugGraphHostMetricBucketItem');
  const context = {
    result: null, Number,
    location: {port: '9001', protocol: 'https:'},
    jsDebugGraphProcessCpuColors: {current: 'green', peers: ['red']},
  };
  const bucket = "{servers: new Map([['port:9000', {cpuCount: 1, cpuTotalPercent: 40, label: 'port:9000'}]])}";
  vm.runInNewContext(`${helpers}\n${cpuSource}\nresult = debugGraphProcessCpuSeriesDefs([${bucket}]);`, context);
  const solid = context.result.filter(series => series.linePattern === 'solid');
  assert.equal(solid.length, 1);
  assert.equal(solid[0].key, 'cpu:port:9001');
  assert.equal(solid[0].color, 'green');
  assert.equal(solid[0].labelParams.process, 'yomux.py (web)');
  assert.equal(solid[0].currentProcessCpu, true);
  // The serving port had no sample in this bucket, so it is an honest gap, not a value.
  vm.runInNewContext(`${helpers}\nresult = debugGraphProcessCpuBucketHasData(${bucket}, 'port:9001');`, context);
  assert.equal(context.result, false);
  // The one sampled peer stays dotted and port-labelled, never promoted to current.
  vm.runInNewContext(`${helpers}\n${cpuSource}\nresult = debugGraphProcessCpuSeriesDefs([${bucket}]);`, context);
  const dotted = context.result.filter(series => series.linePattern === 'dot');
  assert.equal(dotted.length, 1);
  assert.equal(dotted[0].key, 'cpu:port:9000');
  assert.equal(dotted[0].labelParams.process, 'yomux.py (web) :9000');
  assert.equal(dotted[0].color, 'red');
});

test('CPU serving-port series stands alone on the default port with no aggregate fallback', () => {
  // W5: on the default port (empty location.port) with zero sampled servers, the
  // serving port is still the one solid series and there is NO aggregate `cpu`
  // fallback series. The old code returned a single aggregate `{key:'cpu'}` here.
  const cpuSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const helpers = sourceFunction('debugGraphProcessCpuBucketValue', 'debugGraphHostMetricBucketItem');
  const context = {
    result: null, Number,
    location: {port: '', protocol: 'http:'},
    jsDebugGraphProcessCpuColors: {current: 'green', peers: ['red']},
  };
  vm.runInNewContext(`${helpers}\n${cpuSource}\nresult = debugGraphProcessCpuSeriesDefs([{servers: new Map()}]);`, context);
  assert.equal(context.result.length, 1);
  assert.equal(context.result[0].key, 'cpu:port:80');
  assert.equal(context.result[0].linePattern, 'solid');
  assert.equal(context.result[0].currentProcessCpu, true);
  assert.equal(context.result.some(series => series.key === 'cpu'), false);
});

test('CPU keeps cumulative host and total System CPU while Daemons load carries Go process CPU', () => {
  const context = {result: null, debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu'};
  vm.runInNewContext(`
    ${sourceFunction('debugGraphIsGoProcessId', 'debugGraphGroupSeriesItems')}
    ${sourceFunction('debugGraphGroupSeriesItems', 'debugGraphMacMemoryCardAvailable')}
    const go = {key: 'cpu:yolomux-go', processId: 'yolomux-go', processCpu: true};
    const peer = {key: 'cpu:port:7001', processCpu: true};
    const systemCpu = {key: 'systemCpu'};
    const hostProcess = {key: 'cpuBinary:node', hostMetric: 'cpu'};
    const runtime = {key: 'serviceLoad:runtime:agent_tokens:opencode:session_tree', serviceLoad: true};
    const processService = {key: 'serviceLoad:batchd', serviceLoad: true};
    const items = [go, peer, systemCpu, hostProcess, runtime, processService];
    result = {
      daemons: debugGraphGroupSeriesItems({key: 'serversLoad', serviceLoad: true, runtimeOnly: true, includeGoProcessCpu: true}, items).map(series => series.key).sort(),
      cpu: debugGraphGroupSeriesItems({key: 'cpu', hostMetric: 'cpu'}, items).map(series => series.key).sort(),
    };
  `, context);
  assert.deepEqual([...context.result.daemons], ['cpu:yolomux-go', 'serviceLoad:runtime:agent_tokens:opencode:session_tree']);
  assert.deepEqual([...context.result.cpu], ['cpuBinary:node', 'systemCpu']);
});

test('the shared Daemons chart retains Python service CPU while Go shows runtime occupancy and process CPU', () => {
  const source = [
    sourceFunction('debugGraphIsGoProcessId', 'debugGraphGroupSeriesItems'),
    sourceFunction('debugGraphGroupSeriesItems', 'debugGraphMacMemoryCardAvailable'),
    sourceFunction('debugGraphResolvedChartGroup', 'debugGraphMacMemoryDetailsHtml'),
  ].join('\n');
  const context = {
    Map,
    Number,
    String,
    Object,
    debugGraphMacMemoryCardAvailable: () => false,
    debugGraphServiceLoadSampleCount: item => Number(item?.cpuSamples || 0),
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
  };
  vm.runInNewContext(`${source}
    const buckets = [{hostMetrics: {serviceLoad: new Map([
      ['claude', {cpuSamples: 1}],
      ['codex', {cpuSamples: 1}],
    ])}}];
    const group = debugGraphResolvedChartGroup({
      key: 'serversLoad', serviceLoad: true, runtimeOnly: true, unit: 'runtimeOccupancy',
      descKey: 'debug.graph.chart.serversLoad.desc',
    }, buckets);
    const items = [
      {key: 'serviceLoad:claude', serviceLoad: true},
      {key: 'serviceLoad:codex', serviceLoad: true},
      {key: 'cpu:python', processCpu: true},
    ];
    const goBuckets = [{hostMetrics: {serviceLoad: new Map([
      ['runtime:stats_lifecycle', {cpuSamples: 1}],
      ['yolomux-go', {cpuSamples: 1}],
    ])}}];
    const goGroup = debugGraphResolvedChartGroup({
      key: 'serversLoad', serviceLoad: true, runtimeOnly: true, unit: 'runtimeOccupancy',
      descKey: 'debug.graph.chart.serversLoad.desc',
    }, goBuckets);
    result = {
      unit: group.unit,
      descKey: group.descKey,
      series: debugGraphGroupSeriesItems(group, items).map(item => item.key).sort(),
      goUnit: goGroup.unit,
      goDescKey: goGroup.descKey,
      goIncludesProcessCpu: goGroup.includeGoProcessCpu,
      goSeries: debugGraphGroupSeriesItems(goGroup, [
        {key: 'serviceLoad:runtime:stats_lifecycle', serviceLoad: true},
        {key: 'serviceLoad:yolomux-go', serviceLoad: true},
        {key: 'cpu:yolomux-go', processId: 'yolomux-go', processCpu: true},
        {key: 'cpu:port:7001', processId: 'port:7001', processCpu: true},
      ]).map(item => item.key),
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), {
    unit: 'percent',
    descKey: 'debug.graph.chart.serversLoad.desc',
    series: ['cpu:python', 'serviceLoad:claude', 'serviceLoad:codex'],
    goUnit: 'runtimeOccupancy',
    goDescKey: 'debug.graph.chart.serversLoad.desc',
    goIncludesProcessCpu: true,
    goSeries: ['serviceLoad:runtime:stats_lifecycle', 'cpu:yolomux-go'],
  });
  const chartSource = sourceFunction('debugSystemDaemonsChartHtml', 'debugSystemRegionHtml');
  assert.ok(
    chartSource.indexOf('const chartGroup = debugGraphResolvedChartGroup(group, buckets)')
      < chartSource.indexOf('debugGraphGroupSeriesItems(chartGroup, seriesItems)'),
    'empty-state detection uses the same backend-resolved group as the renderer',
  );
});

test('runtime Daemons history excludes nested lifecycle and OpenCode phase rows', () => {
  const visibleSource = sourceFunction('debugGraphVisibleServiceLoadItems', 'debugGraphServiceLoadRangeAvailable');
  const context = {
    Map,
    String,
    Number,
    debugGraphServiceLoadMetric: () => 'cpu',
    debugGraphServiceLoadSampleCount: item => Number(item?.cpuSamples || 0),
  };
  vm.runInNewContext(`${visibleSource}
    const bucket = {hostMetrics: {serviceLoad: new Map([
      ['runtime:stats_lifecycle', {cpuSamples: 1}],
      ['runtime:stats_lifecycle:gc', {cpuSamples: 1}],
      ['runtime:agent_tokens', {cpuSamples: 1}],
      ['runtime:agent_tokens:opencode:session_tree', {cpuSamples: 1}],
      ['runtime:agent_status', {cpuSamples: 1}],
    ])}};
    result = debugGraphVisibleServiceLoadItems([bucket], {runtimeOnly: true}).map(([key]) => key);
  `, context);
  assert.deepEqual([...context.result], ['runtime:stats_lifecycle', 'runtime:agent_tokens', 'runtime:agent_status']);
});

test('Go stats keeps host CPU separate and exposes the Go process as a CPU series', () => {
  const bucketSource = [
    sourceFunction('jsDebugCurrentSeriesValue', 'jsDebugCurrentCpuProjectionValue'),
    sourceFunction('jsDebugCurrentCpuProjectionValue', 'jsDebugCurrentServiceLoadItem'),
    sourceFunction('jsDebugCurrentServiceLoadItem', 'jsDebugCurrentBucketRecord'),
    sourceFunction('jsDebugCurrentBucketRecord', 'jsDebugCurrentSnapshotAgentWindowRevision'),
  ].join('\n');
  const daemonSource = sourceFunction('debugGraphProcessCpuSeriesDefs', 'debugGraphGpuDeviceSeriesDefs');
  const context = {
    result: null,
    Number,
    Math,
    Map,
    Object,
    String,
    location: {port: '7222', protocol: 'http:'},
    jsDebugGraphProcessCpuColors: {current: 'green', peers: ['red']},
  };
  vm.runInNewContext(`
    ${bucketSource}
    ${daemonSource}
    const record = jsDebugCurrentBucketRecord({duration: 1, series: {
      'cpu_percent:host': {value: 31},
      'process_cpu_percent:yolomux_go': {value: 99},
      'process_cpu_percent:python': {value: 99},
      'process_cpu_percent:tmux': {value: 88},
      'owned_process_cpu_percent:yolomux_go': {value: 7},
    }});
    const daemonBuckets = [{servers: new Map(Object.entries(record.servers).map(([key, item]) => [key, {
      label: item.label,
      cpuTotalPercent: item.cpu_total_percent,
      cpuCount: item.cpu_count,
    }]))}];
    result = {
      hostCpu: record.cpu_total_percent,
      daemonSources: Object.keys(record.servers),
      hostProcessSources: Object.keys(record.host_metrics.cpu_processes).sort(),
      cpuSeries: debugGraphProcessCpuSeriesDefs(daemonBuckets).map(series => series.processId),
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), {
    hostCpu: 31,
    daemonSources: ['yolomux_go'],
    hostProcessSources: ['python', 'tmux', 'yolomux_go'],
    cpuSeries: ['yolomux_go'],
  }, 'host CPU remains a CPU-family aggregate, while the Go-owned process is a CPU series');
});

test('Daemons load includes bounded Go runtime-owner occupancy lines', () => {
  const source = [
    sourceFunction('normalizedDebugGraphServiceLoadMode', 'debugGraphServiceLoadLabel'),
    sourceFunction('debugGraphServiceLoadLabel', 'debugGraphServiceLoadSeriesDefs'),
    sourceFunction('jsDebugCurrentSeriesValue', 'jsDebugCurrentCpuProjectionValue'),
    sourceFunction('jsDebugCurrentServiceLoadItem', 'jsDebugCurrentBucketRecord'),
    sourceFunction('jsDebugCurrentBucketRecord', 'jsDebugCurrentSnapshotAgentWindowRevision'),
    sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage'),
  ].join('\n');
  const context = {
    Number,
    Math,
    Map,
    String,
    Object,
    debugRuntimeState: {serviceLoadMode: 'avg'},
    debugGraphStableServiceLoadVisuals: items => items.map((_item, index) => ({color: `color-${index}`, patternIndex: index})),
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot', 'dash-dot'],
    jsDebugGraphDisplayHoldExpiryMs: {tenSecondGauge: 10000},
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
    debugGraphServiceLoadSampleCount: (item, metric = 'cpu') => Number(item?.[metric === 'rss' ? 'rssSamples' : 'cpuSamples'] || 0),
    debugGraphServiceMemoryValue: item => Number(item?.rssSamples || 0) > 0 ? Number(item.rssTotalBytes || 0) / Number(item.rssSamples) : 0,
    debugGraphDisplayHoldOutage: () => false,
  };
  vm.runInNewContext(`${source}\nconst record = jsDebugCurrentBucketRecord({duration: 1, series: {
    'service_cpu_percent:runtime:stats_lifecycle': {value: 12},
    'service_cpu_percent:runtime:agent_tokens': {value: 34},
    'service_cpu_percent:runtime:agent_status': {value: 5},
  }});\nconst bucket = {hostMetrics: {serviceLoad: new Map(Object.entries(record.host_metrics.service_load).map(([key, item]) => [key, {
    label: item.label, cpuTotalPercent: item.cpu_total_percent, cpuSamples: item.cpu_samples,
    cpuRangeAvailable: false, cpuMinPercent: 0, cpuMaxPercent: 0,
  }]))}};\nresult = debugGraphServiceLoadSeriesDefs([bucket]).map(item => [item.label, item.value(bucket)]);`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    ['agent status collector (goroutine, in-process)', 5],
    ['agent token collector (goroutine, in-process)', 34],
    ['stats lifecycle (goroutine, in-process)', 12],
  ]);
});

test('Daemons exposes the shared range and resolution controls with panel focus keys', () => {
  const rangeControls = sourceFunction('debugGraphRangeControlsHtml', 'debugGraphZoomResetLabel');
  const resolutionControls = sourceFunction('debugGraphResolutionLabelHtml', 'debugGraphRangeControlsHtml');
  const sharedControls = sourceFunction('debugGraphRangeResolutionControlsHtml', 'debugGraphServiceLoadModeLabel');
  const daemonsChart = sourceFunction('debugSystemDaemonsChartHtml', 'debugSystemAnnounceHtml');
  assert.match(rangeControls, /idPrefix = '', focusKeyPrefix = ''/);
  assert.match(rangeControls, /js-debug-range-options\$\{idPrefix \? `-\$\{idPrefix\}` : ''\}/);
  assert.match(rangeControls, /data-js-debug-system-focus-key=.*focusKeyPrefix/);
  assert.match(resolutionControls, /data-js-debug-system-focus-key=.*focusKey/);
  assert.match(sharedControls, /debugGraphRangeControlsHtml\(nowMs, \{idPrefix, focusKeyPrefix\}\)/);
  assert.match(sharedControls, /debugGraphResolutionLabelHtml\(nowMs, \{focusKey:/);
  assert.match(daemonsChart, /debugGraphRangeResolutionControlsHtml\(nowMs, \{idPrefix: 'daemons', focusKeyPrefix: 'daemons'\}\)/);
  assert.match(css, /\.js-debug-daemons-controls > \.js-debug-range-resolution-controls[\s\S]*flex-wrap: wrap;/);
});

test('Daemons load gives Go thread and child rows stable labels', () => {
  const source = sourceFunction('debugGraphServiceLoadLabel', 'debugGraphServiceLoadSeriesDefs');
  const context = {String};
  vm.runInNewContext(`${source}\nresult = [
    debugGraphServiceLoadLabel('yomux-go:thread:42'),
    debugGraphServiceLoadLabel('yomux-go:child:8124:habcdef'),
    debugGraphServiceLoadLabel('yolomux-go:thread:41'),
    debugGraphServiceLoadLabel('yolomux-go:child:8123'),
    debugGraphServiceLoadLabel('yolomux-go:thread:41:9876'),
    debugGraphServiceLoadLabel('yolomux-go:child:8123:habcdef'),
  ];`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    'Go OS thread 42',
    'Go child process 8124',
    'Go OS thread 41',
    'Go child process 8123',
    'Go OS thread 41',
    'Go child process 8123',
  ]);
});

test('Go process graph labels identify instances and preserve legacy history', () => {
  const source = sourceFunction('debugGraphServiceLoadLabel', 'debugGraphServiceLoadSeriesDefs');
  const labels = vm.runInNewContext(`${source}; [
    debugGraphServiceLoadLabel('yomux-go:process:125:7224:3:%2Fbin%2Fyomux-go:host:boot:start:habcdef'),
    debugGraphServiceLoadLabel('yolomux-go:process:123:7222:12:%2Fbin%2Fyolomux-go:host:boot:start:owner'),
    debugGraphServiceLoadLabel('yolomux-go:process:124:7223:0:%2Fbin%2Fcustom+server:host:boot:start:owner'),
    debugGraphServiceLoadLabel('yolomux-go'),
  ]`);
  assert.deepStrictEqual([...labels], [
    'yomux-go (PID 125, port 7224, PPID 3)',
    'yomux-go (PID 123, port 7222, PPID 12)',
    'custom server (PID 124, port 7223)',
    'yomux-go (legacy process; identity unavailable)',
  ]);
});

test('Daemons load labels host collector rows as runtime occupancy', () => {
  const source = sourceFunction('debugGraphServiceLoadLabel', 'debugGraphServiceLoadSeriesDefs');
  const context = {String};
  vm.runInNewContext(`${source}\nresult = [
    debugGraphServiceLoadLabel('runtime:host_collector:cpu'),
    debugGraphServiceLoadLabel('runtime:host_collector:system_memory'),
    debugGraphServiceLoadLabel('runtime:host_collector:gpu'),
  ];`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    'host CPU collector (runtime occupancy)',
    'host memory collector (runtime occupancy)',
    'host GPU collector (runtime occupancy)',
  ]);
});

test('live runtime SSE refreshes reported owners without erasing omitted Daemons history', () => {
  assert.match(currentSource, /scope\.ownEvent\('stream-runtime',[\s\S]*userOnRuntime\(Object\.freeze\(value\.load\)\)/,
    'the exact stats stream forwards validated runtime-owner telemetry');
  assert.match(currentSource, /function closeStream\(\)[\s\S]*for \(const eventName of \[[^\]]*'runtime'/,
    'closing or replacing the exact stream releases its runtime event listener');
  assert.match(source, /onRuntime\(load\) \{\s*applyJsDebugCurrentRuntimeLoad\(load\);/,
    'the current stats client consumes runtime-owner events');
  assert.match(source, /sourceSamples < Number\(item\[samplesKey\] \|\| 0\) && !\(runtimeSse && prefix === 'cpu'\)/,
    'a newer runtime sample can replace the durable aggregate in its open bucket');
  let refreshes = 0;
  const context = {
    Date,
    debugGraphBucketForTime: () => null,
    debugGraphServiceLoadLabel: key => key,
    refreshDebugGraphSurfaces: () => { refreshes += 1; },
  };
  vm.runInNewContext(
    `${sourceFunction('debugGraphNewHostMetrics', 'debugGraphNewServiceLoadItem')}\n`
      + `${sourceFunction('debugGraphNewServiceLoadItem', 'debugGraphNewClientBucket')}\n`
      + `${sourceFunction('debugGraphApplyHostMetricProcesses', 'debugGraphApplyHostMetrics')}\n`
      + `${sourceFunction('debugGraphApplyHostMetrics', 'debugGraphAgentStatusSnapshot')}\n`
      + `${sourceFunction('applyJsDebugCurrentRuntimeLoad', 'debugGraphDeleteServerRecord')}\n`
      + `const bucket = {hostMetrics: debugGraphNewHostMetrics()};`
      + `bucket.hostMetrics.serviceLoad.set('runtime:agent_status', {label: 'agent status', cpuTotalPercent: 4, cpuSamples: 1});`
      + `bucket.hostMetrics.serviceLoad.set('runtime:agent_tokens', {label: 'agent tokens', cpuTotalPercent: 7, cpuSamples: 1});`
      + `bucket.hostMetrics.serviceLoad.set('runtime:stats_lifecycle', {label: 'stats lifecycle', cpuTotalPercent: 2, cpuSamples: 3});`
      + `debugGraphBucketForTime = () => bucket;`
      + `firstResult = applyJsDebugCurrentRuntimeLoad({stats_lifecycle: {load_percent: 9}, new_worker: {load_percent: 9}});`
      + `secondResult = applyJsDebugCurrentRuntimeLoad({new_worker: {load_percent: 11}});`
      + `result = {existingOmitted: bucket.hostMetrics.serviceLoad.get('runtime:agent_status').cpuTotalPercent, tokenOwnerOmitted: bucket.hostMetrics.serviceLoad.get('runtime:agent_tokens').cpuTotalPercent, refreshed: bucket.hostMetrics.serviceLoad.get('runtime:stats_lifecycle').cpuTotalPercent, samples: bucket.hostMetrics.serviceLoad.get('runtime:stats_lifecycle').cpuSamples, later: bucket.hostMetrics.serviceLoad.get('runtime:new_worker').cpuTotalPercent};`,
    context,
  );
  assert.equal(context.firstResult, true);
  assert.equal(context.secondResult, true);
  assert.equal(context.result.existingOmitted, 4,
    'a runtime event that omits this owner keeps its durable sample in the open bucket');
  assert.equal(context.result.tokenOwnerOmitted, 7,
    'a runtime event that omits token collection keeps the session-token daemon history');
  assert.equal(context.result.refreshed, 9,
    'a live runtime sample refreshes the same owner even when the durable aggregate has more samples');
  assert.equal(context.result.samples, 1);
  assert.equal(context.result.later, 11,
    'later live events refresh an owner first introduced by runtime SSE');
  assert.equal(refreshes, 2);
});

test('line charts keep isolated samples visible and Daemons uses a readable scale', () => {
  assert.match(source, /function debugGraphSingletonLineMarkersHtml\(/,
    'singleton line samples have an explicit SVG marker');
  assert.match(source, /debugGraphSingletonLineMarkersHtml\(series,/,
    'line charts paint the singleton marker layer');
  const singletonSource = sourceFunction('debugGraphSingletonLineMarkersHtml', 'debugGraphAreaPathHtml');
  assert.doesNotMatch(singletonSource, /if \(series\?\.serviceLoad !== true\) return ''/,
    'singleton markers are not limited to service-load series');
  assert.match(source, /group\?\.key === 'serversLoad'\) return false/,
    'stacked occupancy uses a linear cumulative axis');
});

test('Daemons cumulative areas sum top-level owners and preserve unavailable middle buckets', () => {
  const context = {
    esc: String,
    jsDebugGraphRawBucketMs: 1000,
    debugGraphSeriesTokenAgentAttrs: () => '',
    debugGraphSeriesStyleAttr: (series, {areaPatternId = ''} = {}) => {
      const declarations = [`--js-debug-series-color: ${series.color || '#123456'}`];
      if (areaPatternId) declarations.push(`fill: url(#${areaPatternId})`);
      return ` style="${declarations.join('; ')};"`;
    },
    debugGraphServiceLoadPatternId: (series, scope) => `service-${scope}-${series.key}`,
    debugGraphPointForValue: (value, time) => [time, 100 - value],
  };
  vm.runInNewContext(`
    ${sourceFunction('debugGraphSeriesPlotValues', 'debugGraphSeriesClassKey')}
    ${sourceFunction('debugGraphSeriesClassKey', 'debugGraphSeriesLineClassName')}
    ${sourceFunction('debugGraphSeriesTimeMs', 'debugGraphPolylinePoints')}
    ${sourceFunction('debugGraphTimeInNoDataRange', 'debugGraphPolylinePointSegments')}
    ${sourceFunction('debugGraphSeriesUsesArea', 'debugGraphLegendSwatchHtml')}
    ${sourceFunction('debugGraphStackedSeries', 'debugGraphChartAxisMax')}
    ${sourceFunction('debugGraphAreaPathHtml', 'debugGraphBarRectsHtml')}
    const common = {serviceLoad: true, color: '#123456', times: [1000, 2000, 3000], durations: [1000, 1000, 1000], hasDataValues: [true, false, true]};
    const owners = [
      {...common, key: 'first', values: [20, 0, 12]},
      {...common, key: 'second', values: [30, 0, 23]},
      {...common, key: 'nested', runtimeDetail: true, values: [9, 0, 9]},
    ];
    const stacked = debugGraphStackedSeries(owners.filter(item => debugGraphSeriesUsesArea(item, 'area')));
    result = {
      layers: stacked.map(item => ({key: item.key, top: item.plotValues, base: item.stackBaseValues})),
      lines: owners.filter(item => !debugGraphSeriesUsesArea(item, 'area')).map(item => item.key),
      html: debugGraphAreaPathHtml(stacked[1], 100, {startMs: 1000, endMs: 4000}, [{startMs: 2000, endMs: 3000}], 'daemons-serversLoad'),
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.layers)), [
    {key: 'first', top: [20, 0, 12], base: [0, 0, 0]},
    {key: 'second', top: [50, 0, 35], base: [20, 0, 12]},
    {key: 'nested', top: [59, 0, 44], base: [50, 0, 35]},
  ]);
  assert.deepEqual([...context.result.lines], [], 'every Daemons contribution is a cumulative area');
  const paths = [...context.result.html.matchAll(/ d="([^"]+)"/g)].map(match => match[1]);
  assert.match(context.result.html, /fill: url\(#js-debug-service-pattern-daemons-serversLoad-second\)/, 'area fill references its chart-scoped pattern');
  assert.equal((context.result.html.match(/\sstyle=/g) || []).length, 2, 'each split area path emits exactly one style attribute');
  assert.match(context.result.html, /--js-debug-series-color: #123456/, 'pattern fill retains the series color style');
  assert.equal(paths.length, 2, 'unavailable middle bucket splits the area');
  const coordinates = paths.map(path => [...path.matchAll(/[ML] ([\d.]+),([\d.]+)/g)].map(match => match.slice(1).map(Number)));
  assert.deepEqual(coordinates.map(points => [Math.min(...points.map(point => point[0])), Math.max(...points.map(point => point[0]))]), [[1000, 2000], [3000, 4000]]);
  assert.deepEqual(coordinates.map(points => [...new Set(points.map(point => point[1]))].sort((a, b) => a - b)), [[50, 80], [65, 88]], 'upper and lower boundaries retain each measured stack across the gap');
});

test('CPU serving-port series leads the legend and paints after every other line', () => {
  const orderingSource = sourceFunction('debugGraphCurrentProcessCpuOrdered', 'debugGraphLegendSeriesItems');
  const chartSource = sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale');
  const context = {result: null};
  vm.runInNewContext(`
    ${orderingSource}
    const area = {key: 'cpuBinary:python'};
    const system = {key: 'systemCpu'};
    const peer = {key: 'cpu:port:7001', processCpu: true};
    const current = {key: 'cpu:port:7442', processCpu: true, currentProcessCpu: true};
    result = {
      legend: debugGraphCurrentProcessCpuOrdered([area, system, peer, current], true).map(series => series.key),
      paint: debugGraphCurrentProcessCpuOrdered([system, current, peer]).map(series => series.key),
    };
  `, context);
  assert.deepEqual([...context.result.legend], ['cpu:port:7442', 'cpuBinary:python', 'systemCpu', 'cpu:port:7001']);
  assert.deepEqual([...context.result.paint], ['systemCpu', 'cpu:port:7001', 'cpu:port:7442']);
  assert.match(chartSource, /const lineSeries = debugGraphCurrentProcessCpuOrdered\(/);
});

test('CPU reserves red dotted for System and violet solid for yolomux.py under the red accent', () => {
  const paletteSource = slice(
    source,
    'const jsDebugGraphSeriesPalette = Object.freeze({',
    'const jsDebugGraphGpuDeviceColors = Object.freeze(',
  );
  const renderSource = source.slice(
    source.indexOf('function debugGraphSeriesPlotValues('),
    source.indexOf('\nfunction debugGraphAreaPathHtml('),
  );
  const context = {
    result: null,
    Number,
    String,
    Array,
    esc: value => String(value),
    debugGraphPolylinePointSegments: () => [['0,0', '1,1']],
    debugGraphCommunicationGapThresholdMs: () => 1,
    debugGraphAgentTokenPatternIndex: () => -1,
  };
  vm.runInNewContext(`
    ${paletteSource}
    ${renderSource}
    ${sourceFunction('debugGraphSeriesUsesArea', 'debugGraphLegendSwatchHtml')}
    ${sourceFunction('debugGraphLegendSwatchHtml', 'debugGraphIntegerAxisValues')}
    const system = {key: 'systemCpu', label: 'system avg CPU %', linePattern: 'dot', color: jsDebugGraphSeriesPalette.systemCpu, values: [47.5], times: [1], durations: [1000]};
    const current = {key: 'cpu:port:7442', label: 'yomux.py (web) CPU %', processCpu: true, currentProcessCpu: true, linePattern: 'solid', color: jsDebugGraphProcessCpuColors.current, values: [32], times: [1], durations: [1000]};
    result = {
      colors: [debugGraphSeriesDisplayColor(system), debugGraphSeriesDisplayColor(current)],
      lines: [debugGraphPolylineHtml(system, 100, {}, false), debugGraphPolylineHtml(current, 100, {}, false)],
      legends: [debugGraphLegendSwatchHtml(system), debugGraphLegendSwatchHtml(current)],
    };
  `, context);
  assert.deepEqual([...context.result.colors], ['var(--js-debug-agent-token-rose)', 'var(--js-debug-agent-token-violet)']);
  assert.match(context.result.lines[0], /--js-debug-series-color: var\(--js-debug-agent-token-rose\)/);
  assert.match(context.result.lines[0], /js-debug-line--pattern-dot/);
  assert.match(context.result.lines[1], /--js-debug-series-color: var\(--js-debug-agent-token-violet\)/);
  assert.match(context.result.lines[1], /js-debug-line--current-process/);
  assert.match(context.result.lines[0], /<title>system avg CPU %<\/title>/);
  assert.match(context.result.lines[1], /<title>yomux\.py \(web\) CPU %<\/title>/);
  assert.match(context.result.legends[0], /--js-debug-series-color: var\(--js-debug-agent-token-rose\)/);
  assert.match(context.result.legends[0], /js-debug-line--pattern-dot/);
  assert.match(context.result.legends[1], /--js-debug-series-color: var\(--js-debug-agent-token-violet\)/);
  assert.match(context.result.legends[1], /js-debug-line--current-process/);

  const presetSource = slice(editorSettingsSource, 'const UI_COLOR_CHOICES =', '\nfunction normalizeEditorCursorColor(');
  const presetContext = {result: null, Object};
  vm.runInNewContext(`${presetSource}\nresult = UI_COLOR_PRESETS.orange.active;`, presetContext);
  const componentTokenValue = semanticName => {
    const match = new RegExp(`--${semanticName}:\\s*var\\((--[^)]+)\\)`).exec(css);
    assert.ok(match, `${semanticName} aliases a shared theme palette token`);
    return match[1];
  };
  const themeTokenValues = semanticName => {
    const rawName = componentTokenValue(semanticName);
    const matches = [...tokenCss.matchAll(new RegExp(`${rawName.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}:\\s*(#[0-9a-fA-F]{6})`, 'g'))];
    assert.equal(matches.length, 2, `${semanticName} has dark and light values`);
    return {dark: matches[0][1], light: matches[1][1]};
  };
  const rgb = value => [1, 3, 5].map(index => parseInt(value.slice(index, index + 2), 16));
  const distance = (left, right) => Math.hypot(...rgb(left).map((value, index) => value - rgb(right)[index]));
  const lab = value => {
    const [red, green, blue] = rgb(value).map(channel => {
      const normalized = channel / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    const x = (red * 0.4124 + green * 0.3576 + blue * 0.1805) / 0.95047;
    const y = red * 0.2126 + green * 0.7152 + blue * 0.0722;
    const z = (red * 0.0193 + green * 0.1192 + blue * 0.9505) / 1.08883;
    const pivot = channel => channel > 0.008856 ? channel ** (1 / 3) : 7.787 * channel + 16 / 116;
    return [116 * pivot(y) - 16, 500 * (pivot(x) - pivot(y)), 200 * (pivot(y) - pivot(z))];
  };
  const perceptualDistance = (left, right) => Math.hypot(...lab(left).map((value, index) => value - lab(right)[index]));
  const systemColors = themeTokenValues('js-debug-agent-token-rose');
  const currentColors = themeTokenValues('js-debug-agent-token-violet');
  for (const theme of ['dark', 'light']) {
    const redAccent = presetContext.result[theme].bright;
    assert.ok(distance(systemColors[theme], currentColors[theme]) >= 70, `${theme}: red System and violet web series stay separated`);
    assert.ok(distance(currentColors[theme], redAccent) >= 120, `${theme}: current web CPU stays separated from red selection chrome`);
  }
  const lightPrimaryPalette = [
    'js-debug-agent-token-cyan',
    'js-debug-agent-token-orange',
    'js-debug-agent-token-magenta',
    'js-debug-agent-token-beige',
    'js-debug-agent-token-turquoise',
    'js-debug-agent-token-rose',
    'js-debug-agent-token-violet',
  ].map(semanticName => themeTokenValues(semanticName).light);
  assert.deepEqual(lightPrimaryPalette, ['#006dff', '#f04400', '#d000b8', '#b06b00', '#008f55', '#d00040', '#6d28d9']);
  const darkPrimaryPalette = [
    'js-debug-agent-token-cyan',
    'js-debug-agent-token-orange',
    'js-debug-agent-token-magenta',
    'js-debug-agent-token-beige',
    'js-debug-agent-token-turquoise',
    'js-debug-agent-token-rose',
    'js-debug-agent-token-violet',
  ].map(semanticName => themeTokenValues(semanticName).dark);
  assert.deepEqual(darkPrimaryPalette, ['#00a8ff', '#ff6a00', '#ff00d4', '#ffd400', '#00e676', '#ff1744', '#8b5cf6']);
  for (let left = 0; left < lightPrimaryPalette.length; left += 1) {
    for (let right = left + 1; right < lightPrimaryPalette.length; right += 1) {
      assert.ok(distance(lightPrimaryPalette[left], lightPrimaryPalette[right]) >= 70, `light graph colors ${left} and ${right} separate in raw RGB channels`);
      assert.ok(perceptualDistance(lightPrimaryPalette[left], lightPrimaryPalette[right]) >= 32, `light graph colors ${left} and ${right} remain perceptually separated in CIE Lab`);
    }
  }
  assert.match(css, /body\.theme-light \.js-debug-line\s*\{[^}]*stroke-width:\s*1\.6/);
  assert.match(css, /\.js-debug-line--current-process\s*\{[^}]*stroke-width:\s*2\.4/);
  assert.match(css, /\.js-debug-legend-line \.js-debug-line--current-process\s*\{[^}]*stroke-width:\s*2\.5/);
  assert.match(css, /body\.theme-light :is\(\.js-debug-graph-view, \.js-yocost-graphs\)\s*\{[^}]*--js-debug-client-comparison-opacity:\s*0\.9/);
});

test('chart legends distinguish filled area keys from thin line keys', () => {
  const areaSource = sourceFunction('debugGraphSeriesUsesArea', 'debugGraphLegendSwatchHtml');
  const swatchSource = sourceFunction('debugGraphLegendSwatchHtml', 'debugGraphIntegerAxisValues');
  const context = {
    result: null,
    esc: value => String(value),
    debugGraphAgentTokenLegendSwatchHtml: () => '',
    debugGraphSeriesLinePattern: series => series.linePattern || '',
    debugGraphSeriesLineClassName: () => 'js-debug-line',
    debugGraphSeriesLinePatternAttrs: () => '',
    debugGraphSeriesStyleAttr: () => '',
    debugGraphSeriesClassKey: series => series.key,
  };
  vm.runInNewContext(`${areaSource}\n${swatchSource}\nresult = [debugGraphLegendSwatchHtml({key: 'memory', hostMetric: 'memory', hostProcessId: 'python'}, 'area'), debugGraphLegendSwatchHtml({key: 'latency', clientMetric: true}, 'line')];`, context);
  assert.match(context.result[0], /<span class="js-debug-legend-area"/);
  assert.doesNotMatch(context.result[0], /<svg/);
  assert.match(context.result[1], /<svg class="js-debug-legend-line"/);
  assert.match(sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale'), /debugGraphLegendHtml\(renderedLegendSeries, \{kind: group\.kind\}\)/);
  assert.match(css, /\.js-debug-legend-area\s*\{[^}]*width:\s*18px[^}]*height:\s*6px/);
});

test('Logs Clear hides at/below a per-producer sequence cursor, ignores wall time, and survives an epoch reset', () => {
  // W5: Clear uses validated (epoch, sequence) cursors, never wall time. A same-tick
  // record above the cursor stays; a clock rollback cannot resurface a hidden record;
  // a server epoch reset makes the old cursor stop matching so post-reset records show.
  const logsSource = source.slice(
    source.indexOf('function debugClientLogRecord('),
    source.indexOf('\nfunction debugVisibleLogRecords('),
  );
  const context = {
    result: null, Number, Object, String, Set, Array, Date,
    jsDebugLogLevels: ['info', 'warning', 'debug', 'error'],
    jsDebugClientLogEpoch: 'client-1',
    redactDiagnosticValue: value => value,
    diagnosticPacificWallTime: () => '',
    debugClientLogLevel: event => event.level || 'info',
    debugEventDetailText: event => String(event.message || ''),
    debugEventStatusText: () => '',
    debugPhaseTimingText: () => '',
    jsDebugEvents: [
      {id: 5, ts: '1970-01-01T00:00:05.000Z', level: 'error', message: 'c5'},
      {id: 6, ts: '1970-01-01T00:00:06.000Z', level: 'error', message: 'c6'},
    ],
    jsDebugLogsState: {
      serverEpoch: 'e1', serverSequence: 3, clearedCursors: {server: null, client: null},
      payload: [
        {id: 1, timestamp: 100, level: 'error', message: 's1'},
        {id: 2, timestamp: 100, level: 'error', message: 's2'},
        {id: 3, timestamp: 90, level: 'error', message: 's3'},
      ],
    },
  };
  const run = 'result = debugMergedLogRecords().map(record => record.id);';
  vm.runInNewContext(`${logsSource}\n${run}`, context);
  assert.deepEqual([...context.result].sort(), ['client:5', 'client:6', 'server:1', 'server:2', 'server:3']);
  // Clear at server seq 2 / client seq 5. Same-tick s2 (ts 100) is hidden while s3
  // (higher seq but EARLIER ts 90 — a clock rollback) stays: sequence, not wall time.
  context.jsDebugLogsState.clearedCursors = {server: {epoch: 'e1', sequence: 2}, client: {epoch: 'client-1', sequence: 5}};
  vm.runInNewContext(`${logsSource}\n${run}`, context);
  assert.deepEqual([...context.result].sort(), ['client:6', 'server:3']);
  // Server ring reset (new epoch) — the old cursor no longer matches, so every
  // server record shows again even though its id is at/below the cleared sequence.
  context.jsDebugLogsState.serverEpoch = 'e2';
  vm.runInNewContext(`${logsSource}\n${run}`, context);
  assert.deepEqual([...context.result].sort(), ['client:6', 'server:1', 'server:2', 'server:3']);
});

test('Logs poll validation rejects malformed, missing-epoch, and inconsistent envelopes but accepts duplicate ids', () => {
  const validatorSource = sourceFunction('jsDebugValidateServerLogEnvelope', 'debugVisibleLogRecords');
  const run = envelope => {
    const context = {result: null, Number, Array, JSON, payload: envelope};
    vm.runInNewContext(`${validatorSource}\nresult = jsDebugValidateServerLogEnvelope(payload);`, context);
    return context.result;
  };
  assert.equal(run({ok: true, epoch: 'e', sequence: 2, logs: [{id: 1}, {id: 2}]}).ok, true);
  assert.match(run({ok: false, epoch: 'e', sequence: 0, logs: []}).reason, /malformed/);
  assert.match(run({ok: true, epoch: '', sequence: 1, logs: []}).reason, /epoch/);
  // Duplicate or nonmonotonic ids are accepted and stored raw; render-time dedup owns them.
  assert.equal(run({ok: true, epoch: 'e', sequence: 2, logs: [{id: 1}, {id: 1}]}).ok, true);
  assert.equal(run({ok: true, epoch: 'e', sequence: 2, logs: [{id: 2}, {id: 1}]}).ok, true);
  assert.match(run({ok: true, epoch: 'e', sequence: 2, logs: [{id: 3}]}).reason, /exceeds/);
});

test('an API observation finalizes its enriched bytes exactly once and ignores late arrivals', () => {
  // W5: reserve immediately, finalize once after bounded byte/timing enrichment
  // (Content-Length or a response clone). A second, later measurement for an
  // already-finalized observation cannot rewrite its immutable content.
  const finalizeSource = sourceFunction('jsDebugCurrentObservationEventSnapshot', 'queueJsDebugCurrentObservation');
  const bytesSource = sourceFunction('finalizeJsDebugCurrentObservationBytes', 'recordJsDebugDisconnectedSpan');
  const context = {
    result: null, Number, String, Object,
    reloadClientJourneyId: 'j', jsDebugCodeRevision: () => 'rev', jsDebugBrowserFamily: () => 'chromium',
    jsDebugCurrentObservationState: {epoch: 'ep', queue: []},
  };
  const live = {type: 'api', id: 7, ts: '1970-01-01T00:00:01.000Z', requestBytes: 100};
  context.jsDebugCurrentObservationState.queue.push({
    key: 'ep:7', epoch: 'ep', event: {...live}, liveEvent: live, releaseBlocking: false, finalized: false,
  });
  const program = `
    ${finalizeSource}
    ${bytesSource}
    live.responseBytes = 345;
    finalizeJsDebugCurrentObservationBytes(live);
    const entry = jsDebugCurrentObservationState.queue[0];
    const afterFirst = {bytes: entry.event.responseBytes, finalized: entry.finalized, live: entry.liveEvent};
    live.responseBytes = 999;
    finalizeJsDebugCurrentObservationBytes(live);
    result = {afterFirst, afterSecond: entry.event.responseBytes};
  `;
  context.live = live;
  vm.runInNewContext(program, context);
  assert.equal(context.result.afterFirst.bytes, 345);
  assert.equal(context.result.afterFirst.finalized, true);
  assert.equal(context.result.afterFirst.live, null);
  assert.equal(context.result.afterSecond, 345);
});

test('a reserved failure and a restored observation finalize without a live event', () => {
  // W5: a failure reserves identity immediately and finalizes its already-complete
  // content unchanged; a restored (pre-finalized) entry is never re-snapshotted.
  const finalizeSource = sourceFunction('finalizeJsDebugCurrentObservation', 'queueJsDebugCurrentObservation');
  const snapshotSource = sourceFunction('jsDebugCurrentObservationEventSnapshot', 'finalizeJsDebugCurrentObservation');
  const context = {
    result: null, Object, String,
    reloadClientJourneyId: 'j', jsDebugCodeRevision: () => 'rev', jsDebugBrowserFamily: () => 'chromium',
  };
  const program = `
    ${snapshotSource}
    ${finalizeSource}
    const failure = {key: 'ep:1', event: {type: 'error', message: 'boom'}, liveEvent: {type: 'error', message: 'boom', id: 1}, finalized: false};
    finalizeJsDebugCurrentObservation(failure);
    const restored = {key: 'ep:2', event: {type: 'error', message: 'kept', responseBytes: 12}, liveEvent: null, finalized: true};
    finalizeJsDebugCurrentObservation(restored);
    result = {
      failureFinalized: failure.finalized, failureLive: failure.liveEvent, failureMessage: failure.event.message,
      restoredMessage: restored.event.message, restoredBytes: restored.event.responseBytes,
    };
  `;
  vm.runInNewContext(program, context);
  assert.equal(context.result.failureFinalized, true);
  assert.equal(context.result.failureLive, null);
  assert.equal(context.result.failureMessage, 'boom');
  assert.equal(context.result.restoredMessage, 'kept');
  assert.equal(context.result.restoredBytes, 12);
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
  assert.equal(english['debug.graph.chart.agentTokens'], 'Session tokens/min');
  assert.equal(english['debug.graph.chart.modelTokens'], 'Model output tokens/min');
  assert.match(source, /key: 'modelTokens'[\s\S]*dynamicTokenDimension: 'model'/);
  assert.doesNotMatch(source, /data-js-debug-model-token-dimension-select/);
  assert.doesNotMatch(source, /jsDebugGraphModelTokenDimension/);
  assert.doesNotMatch(source, /debugGraphAgentTokenBucketDimensionValue/);
  assert.match(source, /function debugGraphModelTokenSeriesDefs\(buckets\) \{\s*return debugGraphTokenSeriesDefs\(buckets, 'model'\);/);
});

test('fresh Graphs defaults show Agents and memory while keeping Space used hidden after GPU memory', () => {
  assert.match(source, /\{key: 'activity',[^\n]+series: jsDebugAgentStatusSeriesKeys/);
  assert.match(source, /\{key: 'agentTokens',[^\n]+noDataWhenEmpty: true,[^\n]+displayedSummary: 'agentTokens'/);
  assert.match(source, /\{key: 'modelTokens',[^\n]+noDataWhenEmpty: true,[^\n]+displayedSummary: 'modelTokens'/);
  assert.match(source, /\{key: 'memory',[^\n]+hostMetric: 'memory'/);
  assert.match(source, /const jsDebugGraphDefaultHiddenChartKeys = Object\.freeze\(\['gpuUtil', 'gpuMemory', 'systemDisk'\]\)/);
  const graphDefinitions = source.slice(source.indexOf('const jsDebugGraphChartGroups = Object.freeze(['), source.indexOf('const jsDebugGraphChartControlItems ='));
  assert.match(graphDefinitions, /\{key: 'systemDisk',[^\n]+fixedMax: 100, kind: 'line', hostMetric: 'diskUsage'\}/);
  assert.match(graphDefinitions, /\{key: 'gpuMemory',[^\n]*\n\s*\{key: 'systemDisk'/, 'Space used % immediately follows GPU memory');
  assert.match(source, /system_disk: Object\.freeze\(\{legacyAliases: Object\.freeze\(\[\]\), cadenceSeconds: 300, chartGroups: Object\.freeze\(\['systemDisk'\]\)/, 'the client mirrors Go disk sampling every five minutes');
  assert.ok(graphDefinitions.indexOf("key: 'systemDisk'") > graphDefinitions.indexOf("key: 'gpuMemory'"), 'Space used % follows GPU memory');
  assert.ok(graphDefinitions.indexOf("key: 'systemDisk'") < graphDefinitions.indexOf("key: 'latency'"), 'Space used % precedes client charts');
  assert.doesNotMatch(source.slice(source.indexOf('const jsDebugGraphChartGroups = Object.freeze(['), source.indexOf('const jsDebugGraphChartControlItems =')), /key: 'serversLoad'/, 'Daemons load is not a Graphs chart');
  const context = {
    result: null,
    Date,
    Object,
    Set,
    String,
    Number,
    Math,
    window: {localStorage: {getItem: () => null}},
    storageGet: key => context.window.localStorage.getItem(key),
    storageSet: (key, value) => context.window.localStorage.setItem(key, String(value)),
    jsDebugGraphChartGroups: [
      {key: 'activity'}, {key: 'agentTokens'}, {key: 'modelTokens'}, {key: 'memory'}, {key: 'systemDisk'},
    ],
    jsDebugStatsUiPreferencesStorageKey: 'stats',
    legacyYoCostMigrationRequested: false,
    jsDebugLogLevels: ['warning', 'error'],
    jsDebugLogDefaultLevels: ['warning', 'error'],
    jsDebugLogsState: {levels: new Set()},
    safeJsonParse: value => JSON.parse(value),
    normalizedJsDebugSubTab: value => value || 'graph',
    normalizedJsDebugGraphRange: value => value || 900,
    normalizedDebugGraphServiceLoadPreference: value => value || 'auto',
    syncDebugGraphResolutionOverride: () => {},
    saveJsDebugStatsUiPreferences: () => {},
  };
  const runtimeSource = debugRuntimeSource.slice(
    debugRuntimeSource.indexOf('const debugRuntimeState ='),
    debugRuntimeSource.indexOf('\nfunction jsDebugGraphRangeOptionIndex('),
  );
  vm.runInNewContext(`
    ${graphDefaultsSource}
    ${runtimeSource}
    const jsDebugGraphDefaultRangeSeconds = 900;
    function debugGraphAvailableRangeOptions() { return [{seconds: 900}]; }
    loadJsDebugStatsUiPreferences();
    result = jsDebugGraphChartGroups.filter(group => debugGraphChartVisible(group.key)).map(group => group.key);
  `, context);
  assert.deepEqual([...context.result], ['activity', 'agentTokens', 'memory']);
  assert.equal(context.result.includes('serversLoad'), false, 'Daemons load is absent from the Graphs chart registry');
});

test('Space used chart control enables its chart and marks the checkbox checked', () => {
  const facadeStart = debugRuntimeSource.indexOf('function debugGraphHiddenChartKeys(');
  const facadeEnd = debugRuntimeSource.indexOf('\nfunction jsDebugGraphRangeOptionIndex(', facadeStart);
  const facadeCharts = debugRuntimeSource.slice(facadeStart, facadeEnd);
  const controlStart = source.indexOf('function debugGraphChartToggleControlsHtml(');
  const controlEnd = source.indexOf('\nfunction debugGraphLayoutControlsHtml(', controlStart);
  const controls = source.slice(controlStart, controlEnd);
  const visibleGroups = sourceFunction('debugGraphVisibleChartGroups', 'debugGraphStackedSeries');
  const context = {
    result: null,
    debugRuntimeState: {statsUiPreferencesLoaded: true, graphHiddenCharts: new Set(['systemDisk']), graphVisibleCharts: new Set()},
    jsDebugGraphChartControlItems: [{key: 'systemDisk', label: 'Space used %'}],
    jsDebugGraphChartGroups: [{key: 'systemDisk'}],
    loadJsDebugStatsUiPreferences: () => {},
    saveJsDebugStatsUiPreferences: () => {},
    refreshDebugGraphSurfaces: () => {},
    esc: String,
    t: key => key,
    debugGraphLocalizedLabel: group => group.label,
  };
  vm.runInNewContext(`${facadeCharts}\n${controls}\n${visibleGroups}\ndebugRuntimeState.graphHiddenCharts = new Set(['systemDisk']); debugRuntimeState.graphVisibleCharts = new Set(); result = {before: debugGraphChartToggleControlsHtml(), initial: debugGraphVisibleChartGroups([]).map(group => group.key)}; setDebugGraphChartVisible('systemDisk', true); result.after = debugGraphChartToggleControlsHtml(); result.enabled = debugGraphVisibleChartGroups([]).map(group => group.key);`, context);
  assert.match(context.result.before, /data-js-debug-chart-toggle="systemDisk"(?! checked)/);
  assert.deepEqual([...context.result.initial], []);
  assert.match(context.result.after, /data-js-debug-chart-toggle="systemDisk" checked/);
  assert.deepEqual([...context.result.enabled], ['systemDisk']);
});

test('Space used series label each mount and stay on a 0 to 100 percent scale', () => {
  const definitionStart = source.indexOf('function debugGraphDiskUsageSeriesDefs(');
  const definitionEnd = source.indexOf('\nconst jsDebugGraphHostProcessVisualAssignments', definitionStart);
  const definitionSource = source.slice(definitionStart, definitionEnd);
  const axisSource = sourceFunction('debugGraphChartAxisMax', 'debugGraphChartCapacityMax');
  const context = {
    result: null,
    jsDebugGraphAgentTokenColors: ['#111', '#222'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 60000},
    debugGraphNiceAxisMax: () => 120,
    jsDebugGraphChartGroups: [{key: 'systemDisk', fixedMax: 100, unit: 'percent'}],
  };
  vm.runInNewContext(`${definitionSource}\n${axisSource}\nresult = {series: debugGraphDiskUsageSeriesDefs([{hostMetrics: {diskMounts: new Map([['/data', {label: '/data', usedPercent: 42, samples: 1}]])}}]), axis: debugGraphChartAxisMax(jsDebugGraphChartGroups[0], 42)};`, context);
  assert.equal(context.result.series.length, 1);
  assert.equal(context.result.series[0].label, '/data');
  assert.equal(context.result.series[0].value({hostMetrics: {diskMounts: new Map([['/data', {usedPercent: 42, samples: 1}]])}}), 42);
  assert.equal(context.result.axis, 100);
});

test('Space used labels only the latest df inventory, annotates high use, and retains history', () => {
  const seriesStart = source.indexOf('function debugGraphDiskUsageSeriesDefs(');
  const seriesEnd = source.indexOf('\nconst jsDebugGraphHostProcessVisualAssignments', seriesStart);
  const seriesSource = source.slice(seriesStart, seriesEnd);
  const context = {
    result: null,
    jsDebugGraphAgentTokenColors: ['#111', '#222', '#333'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 60000},
  };
  vm.runInNewContext(`${seriesSource}
    const oldBucket = {startMs: 100, hostMetrics: {diskMounts: new Map([
      ['/snap/old', {label: '/snap/old', usedPercent: 100, samples: 1}],
      ['/', {label: '/', usedPercent: 70, samples: 1}],
    ])}};
    const currentBucket = {startMs: 200, hostMetrics: {diskMounts: new Map([
      ['/', {label: '/', usedPercent: 94, samples: 1, inventory: true}],
      ['/boot', {label: '/boot', usedPercent: 4, samples: 1, inventory: true}],
      ['/nfs/keivenc', {label: '/nfs/keivenc', usedPercent: 67, samples: 1, inventory: true}],
      ['/mnt/unavailable', {label: '/mnt/unavailable', usedPercent: 0, samples: 0, inventory: true}],
    ])}};
    const series = debugGraphDiskUsageSeriesDefs([oldBucket, currentBucket]);
    result = {labels: series.map(item => item.label), rootHistory: series.find(item => item.label === '/ (94%)')?.value(oldBucket), unavailableHasData: series.find(item => item.label === '/mnt/unavailable')?.hasData(currentBucket)};`, context);
  assert.deepEqual([...context.result.labels], ['/ (94%)', '/boot', '/mnt/unavailable', '/nfs/keivenc']);
  assert.equal(context.result.rootHistory, 70);
  assert.equal(context.result.unavailableHasData, false);
});

test('materialized disk metrics reach separate mountpoint-labeled Graphs series', () => {
  const currentBucketSource = sourceFunction('jsDebugCurrentBucketRecord', 'jsDebugCurrentBucketHasFamilyData');
  const hostMetricsSource = sourceFunction('debugGraphApplyHostMetrics', 'debugGraphAgentStatusSnapshot');
  const processMetricsSource = sourceFunction('debugGraphApplyHostMetricProcesses', 'debugGraphApplyHostMetrics');
  const hostMetricsDefaultsSource = sourceFunction('debugGraphNewHostMetrics', 'debugGraphNewServiceLoadItem');
  const seriesStart = source.indexOf('function debugGraphDiskUsageSeriesDefs(');
  const seriesEnd = source.indexOf('\nconst jsDebugGraphHostProcessVisualAssignments', seriesStart);
  const seriesSource = source.slice(seriesStart, seriesEnd);
  const context = {
    result: null,
    Number,
    Object,
    jsDebugCurrentSeriesValue: (series, name) => series[name]?.value === undefined ? null : Number(series[name].value),
    jsDebugGraphAgentTokenColors: ['blue', 'orange'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 60000},
  };
  vm.runInNewContext(`${currentBucketSource}\n${processMetricsSource}\n${hostMetricsDefaultsSource}\n${hostMetricsSource}\n${seriesSource}
    const record = jsDebugCurrentBucketRecord({start: 100, duration: 900, series: {
      'system_disk_used_percent:/': {value: 23.5, source_count: 3},
      'system_disk_used_percent:/mnt/data': {value: 82},
      'system_disk_inventory:/mnt/n-a': {value: 1},
    }});
    const bucket = {hostMetrics: debugGraphNewHostMetrics()};
    debugGraphApplyHostMetrics(bucket, record.host_metrics);
    const series = debugGraphDiskUsageSeriesDefs([bucket]);
    result = series.map(item => ({label: item.label, value: item.value(bucket), samples: item.sampleCount(bucket)}));`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    {label: '/', value: 23.5, samples: 3},
    {label: '/mnt/data (82%)', value: 82, samples: 1},
    {label: '/mnt/n-a', value: 0, samples: 0},
  ]);
});

test('an unavailable mounted filesystem breaks only its own usage line', () => {
  const seriesStart = source.indexOf('function debugGraphDiskUsageSeriesDefs(');
  const seriesEnd = source.indexOf('\nconst jsDebugGraphHostProcessVisualAssignments', seriesStart);
  const seriesSource = source.slice(seriesStart, seriesEnd);
  const projectSource = sourceFunction('debugGraphProjectSeriesSamples', 'debugGraphSeriesData');
  const noDataSource = sourceFunction('debugGraphTimeInNoDataRange', 'debugGraphPolylinePointSegments');
  const segmentSource = sourceFunction('debugGraphPolylinePointSegments', 'debugGraphPointForValue');
  const context = {
    result: null,
    Number, Math, Array, Map,
    jsDebugGraphAgentTokenColors: ['blue', 'orange'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 60000},
    jsDebugGraphRawBucketMs: 5000,
    debugGraphDisplayHoldOutage: () => false,
    debugGraphPointForValue: (value, timeMs) => [timeMs, value],
  };
  vm.runInNewContext(`${seriesSource}\n${projectSource}\n${noDataSource}\n${segmentSource}
    const buckets = [
      {startMs: 0, durationMs: 5000, hostMetrics: {diskMounts: new Map([
        ['/', {label: '/', usedPercent: 84, samples: 1, inventory: true}],
      ])}},
      {startMs: 5000, durationMs: 5000, hostMetrics: {diskMounts: new Map([
        ['/', {label: '/', usedPercent: 0, samples: 0, inventory: true}],
        ['/boot', {label: '/boot', usedPercent: 4, samples: 1, inventory: true}],
      ])}},
      {startMs: 10000, durationMs: 5000, hostMetrics: {diskMounts: new Map([
        ['/', {label: '/', usedPercent: 86, samples: 1, inventory: true}],
        ['/boot', {label: '/boot', usedPercent: 4, samples: 1, inventory: true}],
      ])}},
    ];
    const series = debugGraphDiskUsageSeriesDefs(buckets).find(item => item.diskMountId === '/');
    const projection = debugGraphProjectSeriesSamples(series, buckets);
    const segments = debugGraphPolylinePointSegments(
      projection.values, buckets.map(bucket => bucket.startMs), 100,
      {startMs: 0, endMs: 15000}, projection.hasDataValues,
      buckets.map(bucket => bucket.durationMs), 0, false, [],
      projection.observedDataValues, projection.censusGapValues,
    );
    result = {censusGaps: projection.censusGapValues, segmentLengths: segments.map(segment => segment.length)};`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.censusGaps || [])), [false, true, false]);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.segmentLengths)), [1, 1]);
});

test('retained display-bucket merging preserves disk mount samples and their average', () => {
  const mergeSource = sourceFunction('debugGraphMergeBucket', 'compactJsDebugGraphBuckets');
  const diskItemSource = sourceFunction('debugGraphNewDiskMountItem', 'debugGraphNewServiceLoadItem');
  const hostMetricsSource = sourceFunction('debugGraphNewHostMetrics', 'debugGraphNewDiskMountItem');
  const context = {
    Number,
    Math,
    Map,
    String,
    debugGraphMergeAgentTokenRates: () => {},
    result: null,
  };
  vm.runInNewContext(`${hostMetricsSource}\n${diskItemSource}\n${mergeSource}
    const target = {
      apiCount: 0, sseCount: 0, latencyTotalMs: 0, latencyCount: 0, bandwidthBytes: 0, disconnectedMs: 0,
      cpuTotalPercent: 0, cpuCount: 0, systemCpuTotalPercent: 0, systemCpuCount: 0,
      askAgentTotal: 0, runAgentTotal: 0, transitionAgentTotal: 0, idleAgentTotal: 0,
      activeAgentTotal: 0, inactiveAgentTotal: 0, agentActivitySamples: 0, agentStatusSequence: -1,
      tokensPerAgentTotal: 0, agentTokenSamples: 0, agentTokenRates: new Map(),
      hostMetrics: debugGraphNewHostMetrics(),
    };
    const source = {
      agentTokenRates: new Map(),
      hostMetrics: {diskMounts: new Map([
        ['/', {label: '/', usedPercent: 80, samples: 2}],
        ['/data', {label: '/data', usedPercent: 60, samples: 1}],
        ['/mnt/unavailable', {label: '/mnt/unavailable', usedPercent: 0, samples: 0, inventory: true}],
      ])},
    };
    debugGraphMergeBucket(target, source, 0.5);
    result = [...target.hostMetrics.diskMounts.entries()];`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    ['/', {label: '/', usedPercent: 40, samples: 1, inventory: false}],
    ['/data', {label: '/data', usedPercent: 30, samples: 0.5, inventory: false}],
    ['/mnt/unavailable', {label: '/mnt/unavailable', usedPercent: 0, samples: 0, inventory: true}],
  ]);
});

test('retained disk compaction replaces inventory membership with the newest df census', () => {
  const mergeSource = sourceFunction('debugGraphMergeBucket', 'compactJsDebugGraphBuckets');
  const diskItemSource = sourceFunction('debugGraphNewDiskMountItem', 'debugGraphNewServiceLoadItem');
  const hostMetricsSource = sourceFunction('debugGraphNewHostMetrics', 'debugGraphNewDiskMountItem');
  const seriesStart = source.indexOf('function debugGraphDiskUsageSeriesDefs(');
  const seriesEnd = source.indexOf('\nconst jsDebugGraphHostProcessVisualAssignments', seriesStart);
  const seriesSource = source.slice(seriesStart, seriesEnd);
  const context = {
    Number,
    Math,
    Map,
    String,
    debugGraphMergeAgentTokenRates: () => {},
    jsDebugGraphAgentTokenColors: ['#111', '#222', '#333'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 60000},
    result: null,
  };
  vm.runInNewContext(`${hostMetricsSource}\n${diskItemSource}\n${mergeSource}\n${seriesSource}
    const target = {
      startMs: 0, apiCount: 0, sseCount: 0, latencyTotalMs: 0, latencyCount: 0, bandwidthBytes: 0, disconnectedMs: 0,
      cpuTotalPercent: 0, cpuCount: 0, systemCpuTotalPercent: 0, systemCpuCount: 0,
      askAgentTotal: 0, runAgentTotal: 0, transitionAgentTotal: 0, idleAgentTotal: 0,
      activeAgentTotal: 0, inactiveAgentTotal: 0, agentActivitySamples: 0, agentStatusSequence: -1,
      tokensPerAgentTotal: 0, agentTokenSamples: 0, agentTokenRates: new Map(),
      hostMetrics: debugGraphNewHostMetrics(),
    };
    const oldCensus = {startMs: 100, agentTokenRates: new Map(), hostMetrics: {diskMounts: new Map([
      ['/old', {label: '/old', usedPercent: 90, samples: 1, inventory: true}],
      ['/', {label: '/', usedPercent: 70, samples: 1, inventory: true}],
    ])}};
    const newCensus = {startMs: 200, agentTokenRates: new Map(), hostMetrics: {diskMounts: new Map([
      ['/', {label: '/', usedPercent: 94, samples: 1, inventory: true}],
      ['/boot', {label: '/boot', usedPercent: 4, samples: 1, inventory: true}],
    ])}};
    debugGraphMergeBucket(target, oldCensus);
    debugGraphMergeBucket(target, newCensus);
    result = {inventoryStartMs: target.hostMetrics.diskInventoryStartMs, labels: debugGraphDiskUsageSeriesDefs([target]).map(item => item.label)};`, context);
  assert.equal(context.result.inventoryStartMs, 200);
  assert.deepEqual([...context.result.labels], ['/ (82%)', '/boot']);
});

test('disk history coverage is tracked independently and keeps sampled buckets covered', () => {
  assert.match(source, /\['cpu', 'service_load', 'agent_status', 'agent_tokens', 'cost', 'gpu', 'system_memory', 'system_disk', 'browser'\]\.map\(family => \[family, jsDebugCurrentCoverageIntervals\(snapshot, family\)\]\)/);
  const familySource = sourceFunction('jsDebugCurrentBucketHasFamilyData', 'jsDebugCurrentCoverageIntervals');
  const coverageSource = sourceFunction('jsDebugCurrentCoverageIntervals', 'updateJsDebugCurrentSnapshotState');
  const context = {Number, Array, Math, Object};
  vm.runInNewContext(`${familySource}\n${coverageSource}\nresult = jsDebugCurrentCoverageIntervals({
    window_start: 100,
    window_end: 1000,
    resolution_seconds: 60,
    buckets: [{start: 100, duration: 900, series: {'system_disk_used_percent:/': {value: 23}}}],
    no_data: [{family: 'system_disk', start: 100, end: 1000}],
  }, 'system_disk');`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    {startSeconds: 100, endSeconds: 1000, resolutionSeconds: 60, sourceResolutionSeconds: 60},
  ]);
});

test('saved Graphs visibility preferences override defaults instead of being backfilled', () => {
  const context = {
    result: null,
    Date,
    Object,
    Set,
    String,
    Number,
    Math,
    window: {localStorage: {getItem: () => JSON.stringify({hiddenCharts: ['activity'], visibleCharts: ['gpuUtil']})}},
    storageGet: key => context.window.localStorage.getItem(key),
    storageSet: (key, value) => context.window.localStorage.setItem(key, String(value)),
    jsDebugStatsUiPreferencesStorageKey: 'stats',
    legacyYoCostMigrationRequested: false,
    jsDebugLogLevels: ['warning', 'error'],
    jsDebugLogDefaultLevels: ['warning', 'error'],
    jsDebugLogsState: {levels: new Set()},
    safeJsonParse: value => JSON.parse(value),
    normalizedJsDebugSubTab: value => value || 'graph',
    normalizedJsDebugGraphRange: value => value || 900,
    normalizedDebugGraphServiceLoadPreference: value => value || 'auto',
    syncDebugGraphResolutionOverride: () => {},
    saveJsDebugStatsUiPreferences: () => {},
  };
  const runtimeSource = debugRuntimeSource.slice(
    debugRuntimeSource.indexOf('const debugRuntimeState ='),
    debugRuntimeSource.indexOf('\nfunction jsDebugGraphRangeOptionIndex('),
  );
  vm.runInNewContext(`
    ${graphDefaultsSource}
    ${runtimeSource}
    const jsDebugGraphDefaultRangeSeconds = 900;
    function debugGraphAvailableRangeOptions() { return [{seconds: 900}]; }
    loadJsDebugStatsUiPreferences();
    result = ['activity', 'agentTokens', 'modelTokens', 'memory'].filter(debugGraphChartVisible);
  `, context);
  assert.deepEqual([...context.result], ['agentTokens', 'memory']);
});

test('AUTO resolution label reports the coarsest resolution actually rendered by token charts', () => {
  assert.match(sourceFunction('debugGraphResolutionLabelHtml', 'debugGraphRangeControlsHtml'), /debugGraphRenderedResolutionSeconds\(nowMs\)/);
  const context = {
    debugGraphAgentTokenDisplayBuckets: () => [{durationMs: 60000}],
    debugGraphDisplayResolutionMs: () => 1000,
    debugGraphDomain: () => ({}),
  };
  vm.runInNewContext(sourceFunction('debugGraphRenderedResolutionSeconds', 'debugGraphHoverBucketIndex'), context);
  assert.equal(context.debugGraphRenderedResolutionSeconds(1), 60);
  assert.equal(context.debugGraphRenderedResolutionSeconds(1, [{durationMs: 10000}]), 10);
  assert.match(sourceFunction('debugGraphResolutionLabelHtml', 'debugGraphRangeControlsHtml'), /data-js-debug-resolution-seconds/);
});

test('token chart groups different agent windows from one tmux session into one summed series', () => {
  assert.match(
    sourceFunction('debugGraphSessionTokenKey', 'debugGraphCostModelAgentKind'),
    /debugGraphStatsCurrentModule\(\)\?\.canonicalSessionKey\?\.\(full\)/,
  );
  const context = {
    result: null,
    Map,
    Set,
    jsDebugGraphAgentTokenSeriesPrefix: 'agentToken:',
    jsDebugGraphModelTokenSeriesPrefix: 'modelToken:',
    debugGraphSessionTokenKey: value => String(value).split('|')[0],
    debugGraphAgentDisplayLabel: value => String(value).split('|')[0],
    debugGraphDisplayedTokenVisuals: items => items.map((_item, index) => ({color: `color-${index}`, patternIndex: index})),
    debugGraphAgentTokenBucketValue: (_bucket, item) => Number(item.rate || 0),
  };
  vm.runInNewContext(`
    ${sourceFunction('debugGraphTokenSeriesDefs', 'debugGraphAgentTokenSeriesDefs')}
    const bucket = {agentTokenRates: new Map([
      ['yo7771-b|0|codex', {label: 'yo7771-b', samples: 1, rate: 100}],
      ['yo7771-b|1|claude', {label: 'yo7771-b', samples: 1, rate: 250}],
    ])};
    const definitions = debugGraphTokenSeriesDefs([bucket], 'agent');
    result = {
      count: definitions.length,
      key: definitions[0]?.agentTokenKey,
      label: definitions[0]?.label,
      value: definitions[0]?.value(bucket),
      samples: definitions[0]?.sampleCount(bucket),
    };
  `, context);
  assert.deepEqual({...context.result}, {
    count: 1,
    key: 'yo7771-b',
    label: 'yo7771-b',
    value: 350,
    samples: 2,
  });
});

test('macOS keeps Activity Monitor memory facts and pressure in one card', () => {
  const resolver = sourceFunction('debugGraphResolvedChartGroup', 'debugGraphMacMemoryDetailsHtml');
  const groupSeries = sourceFunction('debugGraphGroupSeriesItems', 'debugGraphMacMemoryCardAvailable');
  const processPlot = sourceFunction('debugGraphMacMemoryProcessPlotSeries', 'debugGraphVisibleChartGroups');
  const details = sourceFunction('debugGraphMacMemoryDetailsHtml', 'debugGraphLegendSeriesItems');
  const chart = sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale');
  const pressureColor = sourceFunction('debugGraphMacMemoryPressureColor', 'debugGraphSeriesStyleAttr');
  assert.match(resolver, /key !== 'memory'/);
  assert.match(resolver, /series: \['macMemoryPressure'\]/);
  assert.match(resolver, /fixedMax: 100/);
  assert.match(groupSeries, /group\.macMemoryCard === true[\s\S]*series\.key === 'macMemoryPressure'[\s\S]*series\.hostMetric === 'memory' && series\.hostProcessId/);
  assert.match(processPlot, /plotValues[\s\S]*macPhysicalMemoryTotalBytes[\s\S]*\* 100/);
  for (const label of ['Physical Memory', 'Memory Used', 'Cached Files', 'Swap Used', 'App Memory', 'Wired Memory', 'Compressed']) assert.match(details, new RegExp(label));
  assert.match(chart, /debugGraphMacMemoryDetailsHtml\(buckets\)/);
  assert.match(chart, /selectedGroupSeries\.map\(series => debugGraphMacMemoryProcessPlotSeries\(series, buckets\)\)/);
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

test('Go memory diagnostics render stacked runtime classes and ranked sampled allocation sites', () => {
  const chartSource = sourceFunction('debugGraphAreaPatternHtml', 'debugGraphServiceLoadPatternHtml') + sourceFunction('debugSystemRuntimeMemoryChartHtml', 'debugSystemPprofCaptureHtml');
  const heapChartSource = sourceFunction('debugSystemHeapStackChartHtml', 'debugSystemPprofCaptureHtml');
  const profileSource = sourceFunction('debugSystemPprofCaptureHtml', 'debugSystemGoRuntimeDetailHtml');
  const context = {
    esc: value => String(value),
    debugGraphTerseBytesText: value => `${Number(value || 0)}B`,
    debugSystemNumber: value => String(value),
    debugSystemRowsHtml: rows => JSON.stringify(rows),
    Date,
  };
  vm.runInNewContext(`${chartSource}\n${heapChartSource}\n${profileSource}
    const base = {'Heap objects':80,'Heap unused spans':10,'Heap free retained':5,'Goroutine stacks':2,'OS stacks':1,'Runtime metadata':1,'Profiling buckets':1,'Other runtime':1};
    const memory = {available:true, history:[{at:100,pid:42,categories:base,gauges:{'/memory/classes/total:bytes':101,'/memory/classes/heap/released:bytes':12,'/gc/heap/live:bytes':80,'/gc/heap/goal:bytes':128,'/gc/cycles/total:gc-cycles':7,'/sched/goroutines:goroutines':19},unavailable:[]}], last:{at:100,ranked_at:100,artifact_at:101,pid:42,binary:'/candidate/yolomux_go',binary_sha256:'0123456789abcdef',build:'abc1234',go_version:'go1.22.2',sample_rate_bytes:512000,inuse_bytes:100,alloc_bytes:300,other_inuse_bytes:20,artifact_bytes:900,top_inuse:[{key:'abc',function:'stats.materialize',inuse_bytes:80,stack:['stats.materialize x.go:10']}],top_alloc:[]}};
    const gapHistory = [{at:100,pid:42,categories:base},{at:105,pid:42,categories:{...base,'Heap objects':undefined}},{at:120,pid:42,categories:base}];
    const gapReadout = debugSystemRuntimeMemoryChartHtml({sample_interval_seconds:5,history:gapHistory});
    const missingInterval = debugSystemRuntimeMemoryChartHtml({sample_interval_seconds:5,history:[{at:100,pid:42,categories:base},{at:120,pid:42,categories:base}]});
    const stackChart = debugSystemHeapStackChartHtml([
      {at:100,other_inuse_bytes:10,top_inuse:[{key:'a1abcdef-one',function:'json.decode',inuse_bytes:80,stack:['json.decode json.go:1','service.handle api.go:20']},{key:'b2abcdef-two',function:'json.decode',inuse_bytes:20,stack:['json.decode json.go:1','other.handle api.go:30']},{key:'drops-out',function:'cache.fold',inuse_bytes:40,stack:['cache.fold cache.go:2']}]},
      {at:160,other_inuse_bytes:30,top_inuse:[{key:'a1abcdef-one',function:'json.decode',inuse_bytes:60,stack:['json.decode json.go:1','service.handle api.go:20']},{key:'b2abcdef-two',function:'json.decode',inuse_bytes:50,stack:['json.decode json.go:1','other.handle api.go:30']}]},
    ], 42);
    result = {chart:debugSystemRuntimeMemoryChartHtml(memory), profile:debugSystemPprofCaptureHtml(memory), gapChart:debugSystemRuntimeMemoryChartHtml({history:gapHistory}), gapReadout, missingInterval, stackChart};`, context);
  assert.equal((context.result.chart.match(/data-go-memory-area=/g) || []).length, 8);
  assert.match(context.result.chart, /Stacked Go runtime memory classes/);
  assert.match(context.result.chart, /largest class Heap objects: 80B/);
  for (const gauge of ['Go memory total', 'Heap released', 'Live heap', 'Heap goal', 'GC cycles', 'Goroutines']) assert.match(context.result.chart, new RegExp(gauge));
  assert.equal((context.result.gapChart.match(/data-go-memory-area=/g) || []).length, 16, 'the missing sample splits every area into two observed runs');
  assert.match(context.result.gapChart, /1 samples have incomplete class data and are shown as gaps/);
  assert.match(context.result.gapChart, /M54\.0,/);
  assert.match(context.result.gapChart, /M792\.0,/);
  assert.doesNotMatch(context.result.gapChart, /M204\.0,/, 'irregular time gaps do not get interpolated as equal sample spacing');
  assert.equal((context.result.missingInterval.match(/data-go-memory-area=/g) || []).length, 16, 'widely separated complete samples form separate runs');
  assert.doesNotMatch(context.result.missingInterval, /M54\.0,[^<]*L792\.0,/, 'the area does not bridge a long interval with no samples');
  const gapWidths = [...context.result.gapReadout.matchAll(/data-go-memory-sample=\"\d+\"[^>]*width=\"([\d.]+)\"/g)].map(match => Number(match[1]));
  assert.equal(gapWidths.length, 2, 'only complete observed samples receive a hover target');
  assert.ok(gapWidths.every(width => width < 200), 'hover targets stay within the sample cadence and leave the unobserved interval empty: ' + gapWidths);
  assert.match(context.result.profile, /Capture heap profile/);
  assert.match(context.result.profile, /Download pprof/);
  assert.match(context.result.profile, /stats\.materialize/);
  assert.match(context.result.profile, /binary SHA-256 0123456789abcdef/);
  assert.match(context.result.profile, /revision abc1234 · Go go1\.22\.2/);
  assert.match(context.result.profile, /Ranked stacks sampled/);
  assert.match(context.result.profile, /pprof capture started/);
  assert.match(context.result.profile, /adjacent runtime snapshots/);
  assert.match(context.result.profile, /cumulative allocation/);
  assert.match(context.result.profile, /do not expose exact object or struct sizes/);
  assert.match(context.result.profile, /one minute cooldown/);
  assert.match(context.result.chart, /byte values on the vertical axis and capture time on the horizontal axis/);
  assert.match(context.result.chart, /data-go-memory-sample="100"/);
  assert.match(context.result.chart, /Heap objects: 80B/);
  assert.match(context.result.chart, /<text fill=\"currentColor\"/);
  assert.match(context.result.profile, /<text fill=\"currentColor\"/);
  assert.match(context.result.stackChart, /data-heap-stack-area="a1abcdef-one"/);
  assert.doesNotMatch(context.result.stackChart, /data-heap-stack-area="drops-out"/, 'a stack outside a capture top twelve is not shown as zero');
  assert.match(context.result.stackChart, /Other or unranked sampled stacks/);
  assert.match(context.result.stackChart, /json\.decode · a1abcdef/);
  assert.match(context.result.stackChart, /json\.decode · b2abcdef/);
  assert.match(context.result.stackChart, /other\.handle api\.go:30/);
  assert.match(context.result.stackChart, /stacks listed in every capture are shown individually/i);
});

test('System memory renders only the range top five cumulative binary RSS series', () => {
  const processSeries = [
    sourceFunction('debugDisplayProductLabel', 'applyJsDebugStatsSamplePush'),
    slice(
      source,
      'const jsDebugGraphHostProcessVisualAssignments = Object.freeze({',
      '\nfunction normalizedDebugGraphServiceLoadMode(',
    ),
  ].join('\n');
  const context = {
    result: null,
    Map,
    Set,
    Number,
    String,
    jsDebugGraphAgentTokenPatternCount: 7,
    jsDebugGraphAgentTokenColors: ['blue', 'orange', 'magenta', 'gold', 'green', 'red', 'violet'],
    jsDebugGraphCpuProcessAreaColors: ['cyan', 'orange', 'magenta', 'turquoise'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot', 'dash-dot', 'long-dash', 'dense-dot', 'long-short'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 120000},
    debugGraphStablePaletteIndex: (_key, count) => count - 1,
    debugGraphHostMetricBucketValue: (_bucket, series) => Number(series.hostProcessId === 'python' ? 400 : 100),
    debugGraphHostMetricBucketHasData: () => true,
  };
  const entries = Array.from({length: 9}, (_unused, index) => [
    index === 8 ? 'python' : `binary-${index}`,
    {label: index === 8 ? 'python' : `binary-${index}`, totalBytes: index === 8 ? 400 : 100 + index, samples: 1},
  ]);
  vm.runInNewContext(`
    ${processSeries}
    const definitions = debugGraphMemoryProcessSeriesDefs([{hostMetrics: {memoryProcesses: new Map(${JSON.stringify(entries)})}}]);
    result = definitions.map(item => ({key: item.hostProcessId, metric: item.hostMetric}));
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    {key: 'python', metric: 'memory'},
    {key: 'binary-7', metric: 'memory'},
    {key: 'binary-6', metric: 'memory'},
    {key: 'binary-5', metric: 'memory'},
    {key: 'binary-4', metric: 'memory'},
  ]);
});

test('CPU renders only the range top four grouped binary areas with distinct stable colors', () => {
  const processSeries = [
    sourceFunction('debugDisplayProductLabel', 'applyJsDebugStatsSamplePush'),
    slice(
      source,
      'const jsDebugGraphHostProcessVisualAssignments = Object.freeze({',
      '\nfunction normalizedDebugGraphServiceLoadMode(',
    ),
  ].join('\n');
  const entries = Array.from({length: 6}, (_unused, index) => [
    index === 5 ? 'python' : `binary-${index}`,
    {label: index === 5 ? 'python' : `binary-${index}`, totalPercent: index === 5 ? 40 : 10 + index, samples: 1},
  ]);
  const context = {
    result: null, Map, Set, Number, String,
    jsDebugGraphAgentTokenPatternCount: 7,
    jsDebugGraphAgentTokenColors: ['blue', 'orange', 'magenta', 'gold', 'green', 'red', 'violet'],
    jsDebugGraphCpuProcessAreaColors: ['cyan', 'orange', 'magenta', 'turquoise'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot', 'dash-dot', 'long-dash', 'dense-dot', 'long-short'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 120000},
    debugGraphStablePaletteIndex: key => key.length % 4,
    debugGraphHostMetricBucketValue: () => 1,
    debugGraphHostMetricBucketHasData: () => true,
  };
  vm.runInNewContext(`
    ${processSeries}
    const summarize = definitions => definitions.map(item => ({key: item.key, binary: item.hostProcessId, metric: item.hostMetric, color: item.color}));
    const first = debugGraphCpuProcessSeriesDefs([{systemCpuCount: 1, hostMetrics: {cpuProcesses: new Map(${JSON.stringify(entries)})}}]);
    const shiftedEntries = ${JSON.stringify(entries.slice(0, 5))};
    shiftedEntries.push(['new-binary', {label: 'new-binary', totalPercent: 50, samples: 1}]);
    const shifted = debugGraphCpuProcessSeriesDefs([{systemCpuCount: 1, hostMetrics: {cpuProcesses: new Map(shiftedEntries)}}]);
    const oldOnly = debugGraphCpuProcessSeriesDefs([{systemCpuCount: 1, hostMetrics: {cpuProcesses: new Map([
      ['old-a', {label: 'old-a', totalPercent: 20, samples: 1}],
      ['old-b', {label: 'old-b', totalPercent: 10, samples: 1}],
    ])}}]);
    const churned = debugGraphCpuProcessSeriesDefs([{systemCpuCount: 1, hostMetrics: {cpuProcesses: new Map([
      ['new-a', {label: 'new-a', totalPercent: 40, samples: 1}],
      ['new-b', {label: 'new-b', totalPercent: 30, samples: 1}],
      ['new-c', {label: 'new-c', totalPercent: 20, samples: 1}],
      ['new-d', {label: 'new-d', totalPercent: 10, samples: 1}],
    ])}}]);
    result = {first: summarize(first), shifted: summarize(shifted), oldOnly: summarize(oldOnly), churned: summarize(churned)};
  `, context);
  assert.deepEqual([...context.result.first.map(item => item.binary)], ['python', 'binary-4', 'binary-3', 'binary-2']);
  assert.equal(new Set(context.result.first.map(item => item.color)).size, 4);
  assert.equal(context.result.first.every(item => item.key.startsWith('cpuBinary:') && item.metric === 'cpu'), true);
  const firstColors = Object.fromEntries(context.result.first.map(item => [item.binary, item.color]));
  for (const item of context.result.shifted) {
    if (firstColors[item.binary]) assert.equal(item.color, firstColors[item.binary], `${item.binary} keeps its page-lifetime color`);
  }
  assert.equal(new Set(context.result.churned.map(item => item.color)).size, 4, 'four replacements reclaim both inactive assignments');
});

test('CPU hover resolves both binary areas and the existing line identities directly', () => {
  const direct = sourceFunction('debugGraphDirectHoverSeriesKey', 'debugGraphNearestHoverSeriesAtTime');
  const detail = sourceFunction('debugGraphHoverDetailAtTime', 'debugGraphHoverValueAtTime');
  assert.match(direct, /\[data-js-debug-series\], \[data-js-debug-area-series\]/);
  assert.match(direct, /jsDebugAreaSeries/);
  assert.match(detail, /data\?\.group\?\.key === 'cpu'/);
  assert.match(detail, /series\.values\?\.\[index\]/);
  assert.match(detail, /seriesKey: series\.key/);
});

test('CPU coverage overlays exclude intervals already represented by plotted samples', () => {
  const coverageSource = sourceFunction('debugGraphSeriesDataRanges', 'debugGraphHistoryCoverageGapRuns');
  const context = {
    Map,
    Number,
    Math,
    result: null,
    jsDebugGraphRawBucketMs: 1000,
    debugGraphMergeTimeRanges: (ranges, domain) => {
      const sorted = ranges.filter(range => range.endMs > range.startMs).sort((a, b) => a.startMs - b.startMs);
      const merged = [];
      for (const range of sorted) {
        const previous = merged.at(-1);
        if (previous && range.startMs <= previous.endMs) previous.endMs = Math.max(previous.endMs, range.endMs);
        else merged.push({...range});
      }
      return merged;
    },
    debugGraphSeriesPlotValues: series => series.values,
    debugGraphSeriesPlotHasDataValues: series => series.hasDataValues,
  };
  vm.runInNewContext(`${coverageSource}
    result = debugGraphSeriesDataRanges([
      {values: [25, 30], hasDataValues: [true, true], times: [0, 60000], durations: [60000, 60000]},
    ], {startMs: 0, endMs: 120000});`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [
    {startMs: 0, endMs: 120000},
  ]);
});

test('CPU hover chooses binary, System, yolomux, and nearest grid series without summing them', () => {
  const hoverSource = slice(source, 'function debugGraphHoverBucketIndex(', '\nfunction debugGraphHoverValueAtTime(');
  const series = [
    {key: 'cpuBinary:python', label: 'python', values: [30], plotValues: [30], stackBaseValues: [0], times: [100], hasDataValues: [true]},
    {key: 'cpuBinary:node', label: 'node', values: [20], plotValues: [50], stackBaseValues: [30], times: [100], hasDataValues: [true]},
    {key: 'cpuBinary:rustc', label: 'rustc', values: [10], plotValues: [60], stackBaseValues: [50], times: [100], hasDataValues: [true]},
    {key: 'cpuBinary:chromium', label: 'chromium', values: [5], plotValues: [65], stackBaseValues: [60], times: [100], hasDataValues: [true]},
    {key: 'systemCpu', label: 'System', values: [70], times: [100], hasDataValues: [true]},
    {key: 'cpu:port:7442', label: 'yolomux', values: [50], times: [100], hasDataValues: [true]},
  ];
  const directEvent = key => ({target: {closest: () => ({dataset: key.startsWith('cpuBinary:') ? {jsDebugAreaSeries: key} : {jsDebugSeries: key}})}});
  const context = {
    result: null, Map, Array, Number, String, Math,
    jsDebugGraphGeometry: {height: 100},
    jsDebugGraphHoverChartData: new Map([['cpu', {group: {key: 'cpu', unit: 'percent'}, buckets: [{startMs: 100, durationMs: 100}], groupSeries: series, hoverSeries: series}]]),
    debugGraphPlotYForValue: value => value,
    debugGraphValueText: value => `${value}%`,
    directEvent,
  };
  vm.runInNewContext(`
    ${hoverSource}
    const chart = {
      dataset: {jsDebugChart: 'cpu', jsDebugChartAxisMax: '100', jsDebugChartScale: 'linear'},
      querySelector: () => ({getBoundingClientRect: () => ({top: 0, height: 100})}),
    };
    result = {
      binary: debugGraphHoverDetailAtTime(chart, 100, directEvent('cpuBinary:python')),
      system: debugGraphHoverDetailAtTime(chart, 100, directEvent('systemCpu')),
      yolomux: debugGraphHoverDetailAtTime(chart, 100, directEvent('cpu:port:7442')),
      gridPython: debugGraphHoverDetailAtTime(chart, 100, {target: {closest: () => null}, clientY: 15}),
      gridNode: debugGraphHoverDetailAtTime(chart, 100, {target: {closest: () => null}, clientY: 40}),
      gridRustc: debugGraphHoverDetailAtTime(chart, 100, {target: {closest: () => null}, clientY: 55}),
      gridChromium: debugGraphHoverDetailAtTime(chart, 100, {target: {closest: () => null}, clientY: 62}),
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), {
    binary: {text: 'python: 30%', seriesKey: 'cpuBinary:python'},
    system: {text: 'System: 70%', seriesKey: 'systemCpu'},
    yolomux: {text: 'yolomux: 50%', seriesKey: 'cpu:port:7442'},
    gridPython: {text: 'python: 30%', seriesKey: 'cpuBinary:python'},
    gridNode: {text: 'node: 20%', seriesKey: 'cpuBinary:node'},
    gridRustc: {text: 'rustc: 10%', seriesKey: 'cpuBinary:rustc'},
    gridChromium: {text: 'chromium: 5%', seriesKey: 'cpuBinary:chromium'},
  });
});

test('both themes render CPU and System memory with solid areas and block area keys', () => {
  assert.match(css, /\.js-debug-area\s*\{[^}]*opacity:\s*1/);
  assert.match(css, /\.js-debug-legend-area\s*\{[^}]*opacity:\s*1/);
  assert.doesNotMatch(css, /--js-debug-process-area-opacity/);
  assert.match(css, /\.js-debug-chart\[data-js-debug-chart="serversLoad"\] \.js-debug-line\s*\{[^}]*opacity:\s*0\.9[^}]*stroke-width:\s*1\.4/);
  assert.match(css, /\.js-debug-legend-area\s*\{[^}]*width:\s*18px[^}]*height:\s*6px/);
  assert.match(sourceFunction('debugGraphLegendSwatchHtml', 'debugGraphIntegerAxisValues'), /debugGraphSeriesUsesArea\(series, kind\)/);
  assert.match(sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale'), /debugGraphSeriesUsesArea\(series, group\.kind\)/);
});

test('host CPU and memory labels retain separate Go process instances', () => {
  const processSeries = [
    sourceFunction('debugDisplayProductLabel', 'applyJsDebugStatsSamplePush'),
    slice(
      source,
      'const jsDebugGraphHostProcessVisualAssignments = Object.freeze({',
      '\nfunction normalizedDebugGraphServiceLoadMode(',
    ),
  ].join('\n');
  const context = {
    result: null, Map, Set, Number, String,
    jsDebugGraphAgentTokenPatternCount: 7,
    jsDebugGraphAgentTokenColors: ['blue', 'orange', 'magenta', 'gold', 'green', 'red', 'violet'],
    jsDebugGraphCpuProcessAreaColors: ['cyan', 'orange', 'magenta', 'turquoise'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot', 'dash-dot', 'long-dash', 'dense-dot', 'long-short'],
    jsDebugGraphDisplayHoldExpiryMs: {minuteGauge: 120000},
    debugGraphStablePaletteIndex: (_key, count) => count - 1,
    debugGraphHostMetricBucketValue: (_bucket, series) => Number(series.hostProcessId.endsWith('a1b2c3d4') ? 400 : 600),
    debugGraphHostMetricBucketHasData: () => true,
    debugGraphHostMetricBucketItem: (bucket, series) => bucket.hostMetrics[series.hostMetric === 'cpu' ? 'cpuProcesses' : 'memoryProcesses'].get(series.hostProcessId),
  };
  vm.runInNewContext(`
    ${processSeries}
    const first = 'yolomux-go-pid-101-a1b2c3d4';
    const second = 'yolomux_go-pid-202-b2c3d4e5';
    const canonical = 'yomux-go-pid-303-c3d4e5f6';
    const bucket = {hostMetrics: {
      cpuProcesses: new Map([[first, {label: first, totalPercent: 4, samples: 1}], [second, {label: second, totalPercent: 6, samples: 1}], [canonical, {label: canonical, totalPercent: 8, samples: 1}]]),
      memoryProcesses: new Map([[first, {label: first, totalBytes: 400, samples: 1}], [second, {label: second, totalBytes: 600, samples: 1}], [canonical, {label: canonical, totalBytes: 800, samples: 1}]]),
    }};
    result = {
      cpu: debugGraphCpuProcessSeriesDefs([bucket]).map(item => item.label),
      memory: debugGraphMemoryProcessSeriesDefs([bucket]).map(item => item.label),
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), {
    cpu: ['yomux-go (PID 303)', 'yomux-go (PID 202)', 'yomux-go (PID 101)'],
    memory: ['yomux-go (PID 303)', 'yomux-go (PID 202)', 'yomux-go (PID 101)'],
  });
});

test('status publication repaints Pane Tabs and Tabber through one display owner', () => {
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const activity = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const painted = [];
  const context = {
    autoApproveStates: new Map([['yo7111', {agent_window_snapshot_revision:17}]]),
    sessions: ['yo7111'], tabberViewSessions: new Map(), tabberItemId: '__tabber__', tabberActivityPayload: {activity: {}},
    clientPerfStart() {}, clientPerfEnd() {}, updateDocumentTitle() {},
    renderAutoApproveButtons() {}, trackSessionStateChanges() {}, syncTerminalAttentionHighlights() {},
    updateTopbarActivityStatus() {}, updateSessionStateClasses() {}, refreshPaneTabSessionPopovers() {},
    syncSessionTabLeadingActivityChrome() {painted.push('pane');},
    itemInLayout: () => true, refreshTabberPanels(options) {
      painted.push('tabber');
      assert.equal(options.producer, 'status-response');
      assert.equal(options.requestGeneration, 3);
      assert.deepEqual(Array.from(options.revision), [17]);
    },
    scheduleAgentWindowActivityAnimationSync() {},
  };
  for (const [text, name] of [[activity, 'agentWindowSnapshotRevision'], [activity, 'tabberActivityRenderSignature'], [activity, 'tabberActivityPublicationSignature'], [activity, 'refreshTabberPanelsForAgentWindowPublication'], [activity, 'refreshAgentWindowActivityDisplays'], [boot, 'renderAutoApproveStatusSurfaces']]) {
    const start = text.indexOf(`function ${name}(`);
    const end = text.indexOf('\nfunction ', start + 1);
    vm.runInNewContext(text.slice(start, name === 'renderAutoApproveStatusSurfaces' ? text.indexOf('\nlet deferredSealedAutoApprovePayload', start) : end), context);
  }
  context.renderAutoApproveStatusSurfaces({changedSessions: ['yo7111'], sessionsChanged: false, requestGeneration:3});
  assert.deepEqual(painted, ['pane', 'tabber'], 'the accepted status snapshot repaints both surfaces before returning');
});

test('Tabber coalesces identical status revisions but applies changed status content', () => {
  const activity = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const painted = [];
  const records = new Map();
  const context = {
    agentWindowActivityRecords: records,
    autoApproveStates: new Map([['yo7111', {agent_window_snapshot_revision:17, enabled:false}]]),
    tabberActivityPayload: {activity: {}, topology_generation: 3, session_order: ['yo7111'],
      sessions: {yo7111: {name: 'yo7111'}}, agent_window_snapshot_revision: 17}, tabberItemId: '__tabber__',
    itemInLayout: () => true,
    refreshTabberPanels() {painted.push(context.autoApproveStates.get('yo7111').enabled);},
  };
  const revisionStart = activity.indexOf('function agentWindowSnapshotRevision(');
  const revisionEnd = activity.indexOf('\nfunction agentWindowSnapshotIsAuthoritative(', revisionStart);
  vm.runInNewContext(activity.slice(revisionStart, revisionEnd), context);
  const signatureStart = activity.indexOf('function tabberActivityRenderSignature(');
  const signatureEnd = activity.indexOf('\nfunction refreshTabberPanelsForAgentWindowPublication(', signatureStart);
  const publicationStart = activity.indexOf('function tabberActivityPublicationSignature(', signatureStart);
  vm.runInNewContext(activity.slice(signatureStart, publicationStart), context);
  vm.runInNewContext(activity.slice(publicationStart, signatureEnd), context);
  const start = activity.indexOf('function refreshTabberPanelsForAgentWindowPublication(');
  const end = activity.indexOf('\nfunction refreshAgentWindowActivityDisplays(', start);
  vm.runInNewContext(activity.slice(start, end), context);
  const publish = () => context.refreshTabberPanelsForAgentWindowPublication({
    producer: 'status-response', revision: [17], changedSessions: ['yo7111'],
  });
  assert.equal(publish(), true, 'the first status revision publishes');
  records.set('yo7111:0:claude', {activity: {updatedAtMs: 100}, transitionPulseRefresh: {untilMs: 200}});
  assert.equal(publish(), false, 'renderer timestamps cannot republish an identical server revision');
  context.tabberActivityPayload = {...context.tabberActivityPayload,
    topology_generation: 4, session_order: ['yo7111', 'yo7112'],
    sessions: {yo7111: {name: 'yo7111'}, yo7112: {name: 'yo7112'}}};
  assert.equal(publish(), true, 'same-revision topology and roster updates still publish');
  context.autoApproveStates.set('yo7111', {agent_window_snapshot_revision:17, enabled:true});
  assert.equal(publish(), true, 'changed status content still publishes at the same revision');
  assert.deepEqual(painted, [false, false, true]);
});

test('Tabber republishes local acknowledgement and transition timer changes', () => {
  const activity = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const painted = [];
  const agentWindowActivityRecords = new Map([['one:1:claude', {
    activity: {visualTone: 'attention', transitionStartedAt: 1}, acknowledgedStoppedAt: 0,
    stoppedRefresh: null, transitionPulseRefresh: null,
    acknowledgementVisual: {startedAtMs: 10, untilMs: 20, durationMs: 10,
      acknowledgementKey: 'ack-one', agent: {session: 'one', state: 'idle'}},
  }]]);
  const context = {
    agentWindowActivityRecords, tabberActivityPayload: {activity: {}}, tabberItemId: '__tabber__',
    itemInLayout: () => true, refreshTabberPanels() {painted.push('tabber');},
  };
  for (const name of ['tabberActivityRenderSignature',
    'refreshTabberPanelsForAgentWindowPublication']) {
    const start = activity.indexOf(`function ${name}(`);
    const end = activity.indexOf('\nfunction ', start + 1);
    vm.runInNewContext(activity.slice(start, end), context);
  }
  const publish = () => context.refreshTabberPanelsForAgentWindowPublication({revision: []});
  assert.equal(publish(), true, 'initial local state renders');
  assert.equal(publish(), true, 'local refreshes always apply even without a record change');
  agentWindowActivityRecords.get('one:1:claude').acknowledgementVisual = null;
  assert.equal(publish(), true, 'acknowledgement expiry removes the local marker');
  agentWindowActivityRecords.get('one:1:claude').transitionPulseRefresh = {untilMs: 30};
  assert.equal(publish(), true, 'transition timer arming changes rendered pulse state');
  agentWindowActivityRecords.get('one:1:claude').transitionPulseRefresh = null;
  assert.equal(publish(), true, 'transition timer expiry refreshes the settled state');
  agentWindowActivityRecords.get('one:1:claude').stoppedRefresh = {untilMs: 40};
  assert.equal(publish(), true, 'stopped-state timer arming changes rendered pulse state');
  agentWindowActivityRecords.get('one:1:claude').stoppedRefresh = null;
  assert.equal(publish(), true, 'stopped-state timer expiry refreshes the settled state');
  agentWindowActivityRecords.get('one:1:claude').acknowledgedStoppedAt = 7;
  assert.equal(publish(), true, 'local stopped acknowledgement changes rendered status');
  assert.equal(painted.length, 8);
});

test('Tabber retries a failed status refresh on the next unchanged poll', () => {
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const activity = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const autoApproveStates = new Map([
    ['one', {agent_window_snapshot_revision:17}],
    ['two', {agent_window_snapshot_revision:18}],
  ]);
  let refreshAttempts = 0;
  const context = {
    autoApproveStates,
    sessions: ['one', 'two'],
    tabberViewSessions: new Map(),
    tabberItemId: '__tabber__',
    tabberActivityPayload: {activity: {}, session_order: ['one', 'two']},
    clientPerfStart() {}, clientPerfEnd() {}, updateDocumentTitle() {},
    refreshDebugAgentWindowLiveDetails() {},
    renderAutoApproveButtons() {}, trackSessionStateChanges() {}, syncTerminalAttentionHighlights() {},
    updateTopbarActivityStatus() {}, updateSessionStateClasses() {}, refreshPaneTabSessionPopovers() {},
    syncSessionTabLeadingActivityChrome() {}, scheduleAgentWindowActivityAnimationSync() {},
    itemInLayout: () => true,
    refreshTabberPanels() {
      refreshAttempts += 1;
      if (refreshAttempts === 1) throw new Error('transient Tabber render failure');
    },
  };
  for (const name of ['agentWindowSnapshotRevision', 'tabberActivityRenderSignature',
    'tabberActivityPublicationSignature', 'refreshTabberPanelsForAgentWindowPublication',
    'refreshAgentWindowActivityDisplays']) {
    const start = activity.indexOf(`function ${name}(`);
    const end = activity.indexOf('\nfunction ', start + 1);
    vm.runInNewContext(activity.slice(start, end), context);
  }
  const renderStart = boot.indexOf('function renderAutoApproveStatusSurfaces(');
  vm.runInNewContext(boot.slice(renderStart, boot.indexOf('\nlet deferredSealedAutoApprovePayload', renderStart)), context);

  assert.throws(() => context.renderAutoApproveStatusSurfaces({
    changedSessions: ['one', 'two'], sessionsChanged: false, requestGeneration:3,
  }), /transient Tabber render failure/);
  context.renderAutoApproveStatusSurfaces({changedSessions: [], sessionsChanged: false, requestGeneration:4});
  context.renderAutoApproveStatusSurfaces({changedSessions: [], sessionsChanged: false, requestGeneration:5});
  assert.equal(refreshAttempts, 2, 'the unchanged poll retries once and the successful publication is coalesced');
});

test('revision-only status publication refreshes mounted debug details', () => {
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const start = boot.indexOf('function renderAutoApproveStatusSurfaces(');
  let refreshed = 0;
  const context = {autoApproveStates: new Map(), refreshTabberPanelsForAgentWindowPublication() {},
    refreshDebugAgentWindowLiveDetails() {refreshed++;}};
  vm.runInNewContext(boot.slice(start, boot.indexOf('\nlet deferredSealedAutoApprovePayload', start)), context);
  const result = {changedSessions: [], sessionsChanged: false};
  context.renderAutoApproveStatusSurfaces(result);
  assert.equal(refreshed, 1, 'revision joins refresh even when session chrome is unchanged');
  assert.equal(result.rendered, true);
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
  // W4: ONE compact summary carries the stale count; the per-session stale specifics move under
  // the Live breakdown details rather than being repeated as header prose.
  assert.match(context.result, /2 agent windows across 2 sessions \(1 stale\)/);
  const staleSummaryIndex = context.result.indexOf('(1 stale)');
  const breakdownIndex = context.result.indexOf('Live breakdown');
  const stalePerSessionIndex = context.result.indexOf('two status is stale (rev 6 vs 7)');
  assert.ok(stalePerSessionIndex > breakdownIndex, 'per-session stale text lives under Live breakdown');
  assert.ok(breakdownIndex > staleSummaryIndex, 'compact summary precedes the breakdown');
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

test('Agents detail consumes the stats-owner roster when auto-approve is unavailable', () => {
  const detailStart = source.indexOf('function debugGraphStatsAgentState');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  assert.notEqual(detailStart, -1, 'stats-owner roster adapter exists');
  assert.notEqual(detailEnd, -1, 'stats-owner roster adapter has a bounded source region');
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    STATE_KEY: {working: 'working', idle: 'idle'},
    autoApproveStates: new Map(),
    jsDebugStatsAgentStatusState: {
      payload: {
        available: true,
        payload: {snapshot_revision: 7},
        agents: [
          {id: 'debug|0|%debug-0|codex', kind: 'codex', state: 'run', session: 'debug', pane: '%debug-0'},
          {id: 'debug|1|%debug-1|claude', kind: 'claude', state: 'idle', session: 'debug', pane: '%debug-1'},
        ],
      },
      revision: 7,
    },
    jsDebugStatsPollState: {agentWindowSnapshotRevision: 7},
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => ['claude', 'codex', 'opencode'].includes(String(kind || '').toLowerCase()) ? String(kind).toLowerCase() : '',
    agentWindowPhysicalKey: agent => String(agent?.pane_target || agent?.pane || agent?.window_index || ''),
    agentWindowIndex: agent => agent?.window_index === null || agent?.window_index === undefined ? null : Number(agent.window_index),
    agentWindowCanonicalLabel: (index, kind) => index === null ? kind : `${index}:${kind}`,
    agentWindowStateKey: state => String(state || 'idle') === 'run' ? 'working' : String(state || 'idle'),
    esc: value => String(value),
    document: {querySelectorAll: () => []},
    result: null,
  };
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowDetailHtml('activity');`, context);
  assert.match(context.result, /2 agent windows across 1 sessions/);
  assert.match(context.result, /debug → 0:codex → codex → working/);
  assert.match(context.result, /debug → 1:claude → claude → idle/);
  assert.doesNotMatch(context.result, /waiting for the chart snapshot/);
});

test('debug stats status normalizes Go run through the shared agent-state owner', () => {
  const start = source.indexOf('function debugGraphStatsAgentState');
  const end = source.indexOf('\nfunction debugGraphStatsAgentWindowRows(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const calls = [];
  const context = {
    STATE_KEY: {working: 'working', idle: 'idle'},
    agentWindowStateKey(state) {
      calls.push(state);
      return state === 'run' ? 'working' : state;
    },
  };
  vm.runInNewContext(`${source.slice(start, end)}\nresult = debugGraphStatsAgentState('RUN');`, context);
  assert.deepEqual(calls, ['run']);
  assert.equal(context.result, 'working');
});

test('Agents detail replaces unversioned Go activity fallback rows with the revisioned roster', () => {
  const detailStart = source.indexOf('function debugGraphLiveAgentWindowRows()');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    autoApproveStates: new Map([
      ['yo7222', {agent_windows: [{window_index: 1, pane_target: '%73', kind: 'codex', state: 'working'}]}],
    ]),
    jsDebugStatsAgentStatusState: {
      payload: {
        available: true,
        payload: {snapshot_revision: 12},
        agents: [{session: 'yo7222', window: '1', pane: '%73', kind: 'codex', state: 'run'}],
      },
      revision: 12,
    },
    debugGraphStatsAgentWindowRows: () => ({
      rows: [{session: 'yo7222', agent: {window_index: '1', pane: '%73', kind: 'codex', state: 'run'}, kind: 'codex', revision: 12}],
      revision: 12,
    }),
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => String(kind || '').toLowerCase(),
    agentWindowPhysicalKey: agent => String(agent?.pane_target || agent?.pane || agent?.window_index || ''),
    agentWindowIndex: agent => agent?.window_index === null || agent?.window_index === undefined ? null : Number(agent.window_index),
    result: null,
  };
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowRows();`, context);
  assert.equal(context.result.rows.length, 1);
  assert.equal(context.result.rows[0].revision, 12);
  assert.equal(context.result.rows[0].agent.state, 'run');
});

test('Agents detail admits a newer unavailable backend row over earlier working activity', () => {
  const detailStart = source.indexOf('function debugGraphLiveAgentWindowRows()');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    autoApproveStates: new Map([
      ['yo7222', {agent_windows: [{window_index: 1, pane_target: '%73', kind: 'codex', state: 'working'}]}],
    ]),
    jsDebugStatsAgentStatusState: {payload: {available: true}, revision: 12},
    debugGraphStatsAgentWindowRows: () => ({
      rows: [{session: 'yo7222', agent: {window_index: '1', pane: '%73', kind: 'codex', state: 'unavailable'}, kind: 'codex', revision: 12}],
      revision: 12,
    }),
    goAgentStatusUsesActivityFallback: () => true,
    agentWindowStateKey: state => String(state || 'idle'),
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => String(kind || '').toLowerCase(),
    agentWindowPhysicalKey: agent => String(agent?.pane_target || agent?.pane || agent?.window_index || ''),
    agentWindowIndex: agent => agent?.window_index === null || agent?.window_index === undefined ? null : Number(agent.window_index),
    result: null,
  };
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowRows();`, context);
  assert.equal(context.result.rows.length, 1);
  assert.equal(context.result.rows[0].agent.state, 'unavailable');
  assert.equal(context.result.rows[0].revision, 12);
  context.autoApproveStates.get('yo7222').agent_window_snapshot_revision = 13;
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowRows();`, context);
  assert.equal(context.result.rows[0].agent.state, 'working', 'older stats evidence cannot replace a newer admitted state');
  assert.equal(context.result.sessionRevisions.get('yo7222'), 13, 'rejecting an older row also preserves its session revision');
});

test('Agents detail retires fallback rows when the revisioned Go roster becomes empty', () => {
  const detailStart = source.indexOf('function debugGraphLiveAgentWindowRows()');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    autoApproveStates: new Map([
      ['yo7222', {agent_windows: [{window_index: 1, pane_target: '%73', kind: 'codex', state: 'working'}]}],
    ]),
    jsDebugStatsAgentStatusState: {payload: {available: true}},
    debugGraphStatsAgentWindowRows: () => ({rows: [], revision: 13}),
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => String(kind || '').toLowerCase(),
    agentWindowPhysicalKey: agent => String(agent?.pane_target || agent?.pane || agent?.window_index || ''),
    agentWindowIndex: agent => agent?.window_index === null || agent?.window_index === undefined ? null : Number(agent.window_index),
    result: null,
  };
  for (const revision of [0, 12, 13, 14]) {
    context.autoApproveStates.get('yo7222').agent_window_snapshot_revision = revision;
    vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowRows();`, context);
    assert.equal(context.result.rows.length, revision > 13 ? 1 : 0, `roster revision 13 may retire only rows at or before its own revision, current=${revision}`);
  }
});

test('Agents detail reports typed stats-owner unavailability instead of fabricated zero windows', () => {
  const detailStart = source.indexOf('function debugGraphStatsAgentState');
  const detailEnd = source.indexOf('\nfunction debugGraphChartHtml(', detailStart);
  const detailSource = source.slice(detailStart, detailEnd);
  const context = {
    Map,
    Set,
    Number,
    String,
    STATE_KEY: {working: 'working', idle: 'idle'},
    autoApproveStates: new Map(),
    jsDebugStatsAgentStatusState: {payload: {available: false, unavailable: {reason: 'pane-capture-unavailable'}}, revision: 0},
    jsDebugStatsPollState: {agentWindowSnapshotRevision: 7},
    statsOnlyBootEnabled: () => true,
    agentWindowSnapshotRevision: payload => Number(payload?.agent_window_snapshot_revision || 0),
    agentWindowPayloadRows: rows => Array.isArray(rows) ? rows : [],
    agentWindowKind: kind => String(kind || ''),
    agentWindowPhysicalKey: agent => String(agent?.pane_target || ''),
    agentWindowIndex: agent => Number(agent?.window_index),
    agentWindowCanonicalLabel: (_index, kind) => kind,
    agentWindowStateKey: state => String(state || 'idle'),
    esc: value => String(value),
    document: {querySelectorAll: () => []},
    result: null,
  };
  vm.runInNewContext(`${detailSource}\nresult = debugGraphLiveAgentWindowDetailHtml('activity');`, context);
  assert.match(context.result, /data-js-debug-agent-window-detail-state="unavailable"/);
  assert.match(context.result, /pane-capture-unavailable/);
  assert.doesNotMatch(context.result, /0 agent windows across 0 sessions/);
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
  const serviceAxisSource = sourceFunction('debugGraphServiceLoadAxisDescriptor', 'debugGraphMemoryProcessSeriesDefs');
  const seriesPlotValuesSource = sourceFunction('debugGraphSeriesPlotValues', 'debugGraphSeriesPlotHasDataValues');
  const serviceAxisContext = {
    Object,
    Number,
    Math,
    debugGraphChartAxisMax: (_group, rawMax) => rawMax <= 10 ? 10 : 100,
  };
  vm.runInNewContext(`${axisSource}\n${seriesPlotValuesSource}\n${serviceAxisSource}\nresult = debugGraphServiceLoadAxisDescriptor({unit: 'percent'}, [{plotValues: [1, 2, 3, 4, 5, 5, 5, 5, 5]}], [{plotValues: [100]}]);`, serviceAxisContext);
  assert.equal(serviceAxisContext.result.axisMax, 100, 'daemon range includes larger nested runtime-owner lines as well as cumulative area bands');
  assert.match(source, /function debugGraphTokenSpikeAxisDescriptor\(buckets\)[\s\S]*?debugGraphSpikeCompressedAxisDescriptor/);
  assert.match(source, /group\.key === 'serversLoad' \? debugGraphServiceLoadAxisDescriptor\(group, plotSeries, lineSeries\)/);
  assert.match(source, /data-js-debug-chart-scale/);
  assert.match(source, /data-js-debug-chart-axis-break/);
});

// The transport-failure classification used to be re-derived here, in JavaScript, from
// `pid`/`healthy`/`transport_reason` -- a second copy of the rule
// `yomux_lib/app.py:system_status_service` owns. That classifier is retired. The behaviour it
// pinned now has two owners' worth of coverage on the surviving path:
//   * the backend rule and its published row -- tests/test_gate_contract.py
//     `test_m3_live_daemon_transport_failure_is_not_reported_as_process_down`;
//   * the rendered roster row -- tests/system_health_panel.test.js
//     `a running daemon whose transport failed is an issue with its typed reason, not "down"`.

// The sampler block renders an unpublished field as the panel's ONE unmeasured spelling -- an em
// dash carrying its reason. That spelling has a single owner, so these tests run the REAL
// `debugSystemScalar` and the REAL reason table rather than stubbing them: a stub here would let
// the sampler drift back to "not available" or to a `|| 0` zero while the test stayed green.
const SAMPLER_ABSENCE_SOURCE = [
  slice(source, 'const DEBUG_SYSTEM_HEALTH_REASON_TEXT', '\nconst DEBUG_SYSTEM_STATE_TONES'),
  sourceFunction('debugSystemHealthReasonText', 'debugSystemScalar'),
  sourceFunction('debugSystemScalar', 'debugSystemHealthReasonListText'),
].join('\n');

test('the System sampler renders stalled usage as an explicit bounded warning', () => {
  const functionText = sourceFunction('debugSystemStatsSamplerBodyHtml', 'debugSystemWebProcessDetailHtml');
  const context = {result: null};
  vm.runInNewContext(`
    function esc(value) { return String(value); }
    function debugSystemNumber(value) { return Number.isFinite(Number(value)) ? String(value) : 'N/A'; }
    function debugGraphTerseTimeText(value) { return String(value) + 'ms'; }
    function debugSystemRowsHtml() { return '<dl></dl>'; }
    function debugSystemSamplerFamiliesHtml() { return '<table></table>'; }
    function t(key) { return key; }
    ${SAMPLER_ABSENCE_SOURCE}
    ${functionText}
    result = debugSystemStatsSamplerBodyHtml([{service: 'statsd', usage: {
      quarantined_conflict_count: 2,
      health: {state: 'warning', reason: 'transcripts are advancing but usage atoms are stale', last_accepted_atom_age_seconds: 125},
    }}], 1000);
  `, context);
  assert.match(context.result, /data-js-debug-usage-health="warning"/);
  assert.match(context.result, /role="alert"/);
  assert.match(context.result, /transcripts are advancing but usage atoms are stale/);
  assert.match(context.result, /Quarantined conflicts <span>2<\/span>/);
  assert.match(context.result, /Last accepted <span>125000ms<\/span>/);
  assert.doesNotMatch(context.result, /payload|quantity|token values/);
  // A published count carries no reason, because there is nothing to explain.
  assert.doesNotMatch(context.result, /data-value-reason/);
});

test('an unpublished usage figure is an em dash with its reason, never "not available"', () => {
  const functionText = sourceFunction('debugSystemStatsSamplerBodyHtml', 'debugSystemWebProcessDetailHtml');
  const context = {result: null};
  vm.runInNewContext(`
    function esc(value) { return String(value); }
    function debugSystemNumber(value) { return Number.isFinite(Number(value)) ? String(value) : 'N/A'; }
    function debugGraphTerseTimeText(value) { return String(value) + 'ms'; }
    function debugSystemRowsHtml() { return '<dl></dl>'; }
    function debugSystemSamplerFamiliesHtml() { return '<table></table>'; }
    function t(key) { return key; }
    ${SAMPLER_ABSENCE_SOURCE}
    ${functionText}
    result = debugSystemStatsSamplerBodyHtml([{service: 'statsd', usage: {
      health: {state: 'idle', reason: 'no usage health evidence'},
    }}], 1000);
  `, context);
  // NEGATIVE CONTROL: the stubbed `debugSystemNumber` returns 'N/A' for an absent value -- exactly
  // the shape of the old defect, where an unpublished count printed a word instead of the panel's
  // em dash. The absence must never reach that formatter.
  assert.doesNotMatch(context.result, /N\/A/, 'an absent count must not reach the number formatter');
  assert.doesNotMatch(context.result, /not available/);
  assert.match(context.result, /Quarantined conflicts <span title="the usage store has not published a quarantined-conflict count" data-value-reason="[^"]+">—<\/span>/);
  assert.match(context.result, /Last accepted <span title="no usage atom has been accepted since this process started" data-value-reason="[^"]+">—<\/span>/);
});

test('the System sampler reuses the same warning block for sustained collector failure loops', () => {
  const functionText = sourceFunction('debugSystemStatsSamplerBodyHtml', 'debugSystemWebProcessDetailHtml');
  const context = {result: null};
  vm.runInNewContext(`
    function esc(value) { return String(value); }
    function debugSystemNumber(value) { return Number.isFinite(Number(value)) ? String(value) : 'N/A'; }
    function debugGraphTerseTimeText(value) { return String(value) + 'ms'; }
    function debugSystemRowsHtml() { return '<dl></dl>'; }
    function debugSystemSamplerFamiliesHtml() { return '<table></table>'; }
    function t(key) { return key; }
    ${SAMPLER_ABSENCE_SOURCE}
    ${functionText}
    result = debugSystemStatsSamplerBodyHtml([{service: 'statsd', usage: {
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
  // Recurring work is a logical diagnostic, not a daemon: it lives in the collapsed Advanced
  // diagnostics section and never above the service roster.
  assert.match(source, /debugSystemCardHtml\('Recurring work', debugSystemRecurringWorkHtml\(Array\.isArray\(refresh\.recurring_work\) \? refresh\.recurring_work : \[\]\), \{wide: true\}\)/);
  const advanced = sourceFunction('debugSystemAdvancedHtml', 'debugSystemRegionHtml');
  assert.match(advanced, /Recurring work/, 'recurring work belongs to Advanced diagnostics');
});

test('the exact current snapshot feeds the established renderer without legacy APIs', () => {
  assert.match(currentSource, /exactUrl\('\/api\/stats-stream'/);
  assert.doesNotMatch(currentSource, /exactUrl\('\/api\/stats-snapshot'/);
  assert.doesNotMatch(source, /\/api\/stats-snapshot/);
  const clientOwner = sourceFunction('ensureJsDebugCurrentStatsClient', 'syncJsDebugCurrentStatsClient');
  assert.match(clientOwner, /createBrowserClient\(\{\s*fetch: apiFetch,/, 'YO!stats routes capabilities and recovery through the browser-wide API owner');
  assert.match(source, /function applyJsDebugCurrentSnapshot\(/);
  assert.match(source, /debugGraphApplyServerRecord\(jsDebugCurrentBucketRecord/);
  assert.doesNotMatch(source, /fetchJsDebugStatsJson\(jsDebugStatsSampleQuery/);
});

test('every range selection defaults to AUTO', () => {
  const functionText = sourceFunction('debugGraphDefaultResolutionForRange', 'debugGraphAvailableResolutionChoices');
  const context = {result: null};
  vm.runInNewContext(`
    function normalizedJsDebugGraphRange(value) { return Number(value); }
    function debugGraphExactResolutionChoices(rangeSeconds) {
      return ({300: [1, 10], 900: [10, 60], 3600: [60, 300], 86400: [300]})[rangeSeconds] || [];
    }
    ${functionText}
    result = [300, 900, 3600, 86400].map(debugGraphDefaultResolutionForRange);
  `, context);
  assert.deepEqual([...context.result], [0, 0, 0, 0]);
  assert.match(sourceFunction('setDebugGraphRange', 'setDebugGraphResolutionOverride'), /graphResolutionOverrideSeconds = debugGraphDefaultResolutionForRange/);
});

test('AUTO history requests use the controller capability resolution for wide ranges', () => {
  const functionText = sourceFunction('debugGraphExactRequestResolutionSeconds', 'setDebugGraphExactResolutionEnabled');
  const context = {result: null};
  vm.runInNewContext(`
    const debugRuntimeState = {graphResolutionOverrideSeconds: 0};
    const jsDebugCurrentStatsClientState = {
      client: {
        controller: () => ({
          selection: () => ({range_seconds: 3600, resolution: 'AUTO', resolution_seconds: 300}),
          capabilities: () => ({ranges: [{range_seconds: 3600, auto_resolution_seconds: 300}] }),
        }),
      },
    };
    function activeJsDebugGraphRangeSeconds() { return 3600; }
    function debugGraphExactResolutionChoices() { return [60, 300]; }
    ${functionText}
    const auto = debugGraphExactRequestResolutionSeconds();
    debugRuntimeState.graphResolutionOverrideSeconds = 60;
    const explicit = debugGraphExactRequestResolutionSeconds();
    result = {auto, explicit};
  `, context);
  assert.deepEqual({...context.result}, {auto: 300, explicit: 60});
});

test('current cost reports do not expose the retired backfill display state', () => {
  assert.match(source, /const jsDebugUsageAtomBackfill = \{state: 'unknown'/);
  assert.match(source, /debugGraphApplyUsageAtomBackfill\(snapshot\.usage_atom_backfill\)/);
  assert.doesNotMatch(source, /backfillUnknown|backfillPending/);
});

test('browser observations share the topbar ping and keep a calm bounded cadence', () => {
  assert.match(source, /const jsDebugCurrentObservationBatchDelayMs = 10_000/);
  assert.match(source, /'\/api\/stats-observations'/);
  assert.match(bootstrapSource, /const statsWriterFence = \(\(\) =>/);
  assert.match(bootstrapSource, /Object\.freeze\(\{protocolVersion, schemaGeneration\}\)/);
  assert.match(source, /function jsDebugObservationBatchForEntries\(entries, fence = statsWriterFence\)/);
  assert.match(source, /protocol_version: fence\.protocolVersion/);
  assert.match(source, /schema_generation: fence\.schemaGeneration/);
  assert.match(source, /jsDebugObservationBatchForEntries\(validEntries\.map\(item => item\.entry\), statsWriterFence\)/);
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

test('client failures use the existing bounded observation uploader without debug mode or private content', () => {
  assert.match(coreSource, /installJsDebugEventCapture\(\);/);
  assert.match(source, /'error', 'unhandledrejection'/);
  assert.match(source, /payload\.signature = event\.type === 'stats_history'/);
  assert.match(source, /\? producerSignature[\s\S]*: jsDebugFailureSignature/);
  assert.match(source, /payload\.message = jsDebugFailureText/);
  assert.match(source, /const stack = jsDebugFailureStack/);
  assert.match(source, /if \(stack\) payload\.stack = stack/);
  assert.match(coreSource, /function jsDebugFailureClassification\(event\)/);
  assert.match(source, /payload\.source = correlation\.source/);
  assert.match(source, /const jsDebugObservationUploadMaxBytes = 120 \* 1024/);
  assert.match(source, /const jsDebugObservationUploadMaxItems = 100/);
  assert.match(source, /for \(const event of jsDebugEvents\) recordJsDebugEventForGraph\(event\)/);
  assert.doesNotMatch(source, /payload\.(?:typed_text|file_contents|input_value|document_text)/);
});

test('browser observation uploader emits a periodic heartbeat when the page is otherwise idle', () => {
  assert.match(source, /const jsDebugCurrentObservationHeartbeatMs = 10_000/);
  assert.match(source, /function installJsDebugCurrentObservationLiveness\(\)[\s\S]*const scope = currentObservationLifecycleScope\(\)[\s\S]*recordJsDebugClientHealthObservation\(0, 0\)[\s\S]*setInterval[\s\S]*scope\.ownTimer\('liveness'/);
  assert.match(source, /installJsDebugCurrentObservationLiveness\(\);/);
  const functionText = source.slice(
    source.indexOf('function installJsDebugCurrentObservationLiveness()'),
    source.indexOf('\nfunction jsDebugBrowserFamily()'),
  );
  const runFixture = unscopedHostRequests => {
    const context = {calls: [], timers: [], jsDebugCurrentObservationState: {livenessTimer: null}};
    vm.runInNewContext(`
      const jsDebugCurrentObservationHeartbeatMs = 10000;
      let lifecycleCurrent = true;
      function currentObservationLifecycleScope() { return {current() { return lifecycleCurrent; }, ownTimer() {}}; }
      function recordJsDebugClientHealthObservation(...args) { calls.push(args); }
      function setInterval(callback, delay) { timers.push({callback, delay}); return 90 + timers.length; }
      function clearInterval() {}
      ${clientCapabilityFixtureSource(unscopedHostRequests)}
      ${functionText}
      globalThis.installForTest = installJsDebugCurrentObservationLiveness;
      globalThis.retireForTest = () => {
        lifecycleCurrent = false;
        jsDebugCurrentObservationState.livenessTimer = null;
      };
      globalThis.resumeForTest = () => {
        lifecycleCurrent = true;
        installJsDebugCurrentObservationLiveness();
      };
    `, context);
    return context;
  };
  const denied = runFixture(false);
  assert.deepEqual(denied.calls, [], 'clients without host-request capability do not queue observation heartbeats');
  assert.deepEqual(denied.timers, [], 'clients without host-request capability do not install the heartbeat timer');
  const context = runFixture(true);
  assert.deepEqual(context.calls.map(args => [args[0], args[1]]), [[0, 0]], 'boot queues an idle heartbeat immediately');
  assert.equal(context.timers.length, 1, 'boot owns exactly one periodic heartbeat timer');
  context.installForTest();
  assert.equal(context.timers.length, 1, 'starting observation liveness twice retains one timer owner');
  assert.equal(context.timers[0].delay, 10000);
  context.timers[0].callback();
  assert.deepEqual(context.calls.map(args => [args[0], args[1]]), [[0, 0], [0, 0]], 'the periodic timer emits another heartbeat without other traffic');
  context.retireForTest();
  context.timers[0].callback();
  assert.deepEqual(context.calls.map(args => [args[0], args[1]]), [[0, 0], [0, 0]], 'a page-retired heartbeat callback cannot publish stale liveness');
  context.resumeForTest();
  assert.equal(context.timers.length, 2, 'bfcache resume owns one fresh heartbeat timer');
  assert.deepEqual(context.calls.map(args => [args[0], args[1]]), [[0, 0], [0, 0], [0, 0]], 'bfcache resume publishes one fresh liveness sample');
});

test('observation, pricing, and graph resources share lifecycle scopes with pagehide disposal and bfcache resume', () => {
  assert.match(source, /function disposeJsDebugCurrentObservationLifecycle\(reason = 'disposed'\)/);
  assert.match(source, /function disposeDebugPricingRefreshLifecycle\(reason = 'disposed'\)/);
  assert.match(source, /function stopDebugGraphLiveTicker\(\)[\s\S]*debugGraphLifecycleScope\(\)\.release\('live-ticker'/);
  assert.match(source, /window\.addEventListener\('pagehide'[\s\S]*stopDebugGraphLiveTicker\(\)[\s\S]*disposeDebugPricingRefreshLifecycle\('pagehide'\)[\s\S]*disposeJsDebugCurrentObservationLifecycle\('pagehide'\)/);
  assert.match(source, /window\.addEventListener\('pageshow'[\s\S]*event\?\.persisted[\s\S]*installJsDebugCurrentObservationLiveness\(\)[\s\S]*scheduleJsDebugCurrentObservationFlush\(\)[\s\S]*syncDebugGraphLiveTicker\(\)/);
});

test('observation and pricing timer replacement rejects stale callbacks without changing null sentinels', () => {
  const observationSource = slice(source, 'function scheduleJsDebugCurrentObservationFlush(', '\nasync function flushJsDebugCurrentObservations(');
  const pricingSource = [
    sourceFunction('scheduleDebugCostPricingStatusRefresh', 'disposeDebugPricingRefreshLifecycle'),
    slice(source, 'function disposeDebugPricingRefreshLifecycle(', '\nasync function refreshDebugCostPricingStatus('),
  ].join('\n');
  const context = {result: null};
  vm.runInNewContext(`
    ${lifecycleScopeSource}
    let nextTimer = 1;
    const timers = new Map();
    const cleared = [];
    function setTimeout(callback, delay) {
      const timer = {id: nextTimer++, callback, delay};
      timers.set(timer.id, timer);
      return timer;
    }
    function clearTimeout(timer) { cleared.push(timer?.id || 0); }
    function clientCanUseUnscopedHostRequests() { return true; }
    const jsDebugCurrentObservationBatchDelayMs = 10000;
    const jsDebugCurrentObservationState = {queue: [{}], inFlight: false, timer: null};
    let jsDebugCurrentObservationLifecycleScope = createLifecycleScope();
    function currentObservationLifecycleScope() {
      if (jsDebugCurrentObservationLifecycleScope.disposed()) jsDebugCurrentObservationLifecycleScope = createLifecycleScope();
      return jsDebugCurrentObservationLifecycleScope;
    }
    const observationFlushes = [];
    function flushJsDebugCurrentObservations(scope) { observationFlushes.push(scope.current()); }
    ${observationSource}
    scheduleJsDebugCurrentObservationFlush(50);
    const observationFirst = jsDebugCurrentObservationState.timer;
    scheduleJsDebugCurrentObservationFlush(0);
    const observationSecond = jsDebugCurrentObservationState.timer;
    observationFirst.callback();
    const observationAfterStale = jsDebugCurrentObservationState.timer;
    observationSecond.callback();

    const jsDebugPricingRefreshState = {timer: null};
    let jsDebugPricingRefreshLifecycleScope = createLifecycleScope();
    function jsDebugCostSubviewVisible() { return true; }
    function debugPricingRefreshLifecycleScope() {
      if (jsDebugPricingRefreshLifecycleScope.disposed()) jsDebugPricingRefreshLifecycleScope = createLifecycleScope();
      return jsDebugPricingRefreshLifecycleScope;
    }
    const pricingRefreshes = [];
    function refreshDebugCostPricingStatus(scope) { pricingRefreshes.push(scope.current()); }
    ${pricingSource}
    scheduleDebugCostPricingStatusRefresh();
    const pricingFirst = jsDebugPricingRefreshState.timer;
    scheduleDebugCostPricingStatusRefresh();
    const pricingSecond = jsDebugPricingRefreshState.timer;
    pricingFirst.callback();
    const pricingAfterStale = jsDebugPricingRefreshState.timer;
    pricingSecond.callback();
    scheduleDebugCostPricingStatusRefresh();
    const pricingDisposed = jsDebugPricingRefreshState.timer;
    disposeDebugPricingRefreshLifecycle('pagehide');
    pricingDisposed.callback();
    result = {
      observation: {
        replaced: observationFirst !== observationSecond,
        afterStale: observationAfterStale === observationSecond,
        finalTimer: jsDebugCurrentObservationState.timer,
        flushes: observationFlushes,
      },
      pricing: {
        replaced: pricingFirst !== pricingSecond,
        afterStale: pricingAfterStale === pricingSecond,
        finalTimer: jsDebugPricingRefreshState.timer,
        refreshes: pricingRefreshes,
      },
      cleared,
    };
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.observation)), {replaced: true, afterStale: true, finalTimer: null, flushes: [true]});
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.pricing)), {replaced: true, afterStale: true, finalTimer: null, refreshes: [true]});
  assert.equal(context.result.cleared.length >= 4, true, 'replacement and pagehide dispose every superseded timer through its scope');
});

test('client failure observations are signed, source-bounded, and omit arbitrary event fields', () => {
  const functionText = source.slice(
    source.indexOf('function jsDebugBrowserFamily('),
    source.indexOf('\nconst jsDebugObservationUploadMaxBytes'),
  );
  const failureHelpers = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureText('),
    coreSource.indexOf('\nfunction recordApiDebugEvent('),
  );
  const failureClassifier = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureClassification('),
    coreSource.indexOf('\nfunction jsDebugFailureEvents('),
  );
  const context = {
    result: null,
    jsDebugCurrentObservationState: {epoch: 'epoch-1', instrumentationCostMs: 0},
    jsDebugStatsClientIdForRequest: () => 'client-1',
    performanceNow: () => 0,
    reloadClientJourneyId: 'j-reload-test',
    bootstrap: {clientRevision: 'test-revision'},
    navigator: {userAgent: 'Mozilla/5.0 Chrome/140.0'},
    window: {location: {origin: 'https://localhost:7774', pathname: '/'}},
    jsDebugEndpointText: value => String(value || '').split('?', 1)[0],
    redactDiagnosticValue: value => value,
  };
  vm.runInNewContext(`${failureHelpers}\n${failureClassifier}\n${sourceFunction('jsDebugCurrentFailureCorrelation', 'jsDebugCurrentObservationReceiptBarrier')}\n${functionText}\nresult = jsDebugCurrentObservationFromEvent({
    key: 'epoch-1:error:1',
    event: {
      type: 'error', ts: '2026-08-04T12:00:00.000Z',
      message: 'render failed', stack: 'Error: render failed\\n at paint (/static/yomux.js:10:2)',
      source: '/static/yomux.js?secret=value', line: 10, column: 2,
      signature: 'jsf-deadbeef', provenance: 'confirmed_real', typedText: 'do not upload', fileContents: 'private',
    },
  });`, context);
  assert.deepEqual(Object.keys(context.result.payload).sort(), [
    'browser_family', 'code_revision', 'column', 'delivery_outcome', 'event_type',
    'journey_id', 'kind', 'line', 'message', 'provenance', 'route', 'signature', 'source', 'stack',
  ]);
  assert.equal(context.result.payload.kind, 'error');
  assert.equal(context.result.payload.source, '/static/yomux.js');
  assert.match(context.result.payload.signature, /^jsf-[0-9a-f]{8}$/);
  assert.notEqual(context.result.payload.signature, 'jsf-deadbeef');
  assert.equal(context.result.payload.provenance, 'confirmed_real');
  vm.runInNewContext(`${failureHelpers}\n${failureClassifier}\n${sourceFunction('jsDebugCurrentFailureCorrelation', 'jsDebugCurrentObservationReceiptBarrier')}\n${functionText}\nresult = jsDebugCurrentObservationFromEvent({
    key: 'epoch-1:error:2',
    event: {type: 'error', ts: '2026-08-04T12:01:00.000Z', message: 'unmarked', source: '/static/yomux.js'},
  });`, context);
  assert.equal(Object.hasOwn(context.result.payload, 'provenance'), false);
});

test('controlled API probe producer preserves the fixed source and provenance', () => {
  const start = coreSource.indexOf('function recordApiDebugEvent(');
  const end = coreSource.indexOf('\nfunction recordApiDebugResponseBytes(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const recordSource = coreSource.slice(start, end);
  const context = {
    result: null,
    performanceNow: () => 10,
    jsDebugUrlText: value => String(value),
    jsDebugEndpointText: value => String(value).split('?', 1)[0],
    jsDebugDurationMs: () => 0,
    jsDebugErrorText: value => String(value),
    jsDebugFailureSource: value => String(value).split('?', 1)[0],
    recordJsDebugEvent: (_type, payload) => payload,
  };
  vm.runInNewContext(`${recordSource}
    result = recordApiDebugEvent('/__p0_negative_error_probe__', 'GET', 10, {
      status: 500, ok: false, error: 'P0 fixed negative probe', requestId: 'r-p0-negative-fixed',
      source: '/__p0_negative_error_probe__?private=value', provenance: 'controlled_probe',
    });`, context);
  assert.equal(context.result.source, '/__p0_negative_error_probe__');
  assert.equal(context.result.provenance, 'controlled_probe');
  assert.equal(context.result.requestId, 'r-p0-negative-fixed');
});

test('typed YO!stats warnings use the same durable browser-observation path', () => {
  const functionText = source.slice(
    source.indexOf('function jsDebugBrowserFamily('),
    source.indexOf('\nconst jsDebugObservationUploadMaxBytes'),
  );
  const failureHelpers = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureText('),
    coreSource.indexOf('\nfunction recordApiDebugEvent('),
  );
  const failureClassifier = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureClassification('),
    coreSource.indexOf('\nfunction jsDebugFailureEvents('),
  );
  const context = {
    result: null,
    jsDebugCurrentObservationState: {epoch: 'epoch-1', instrumentationCostMs: 0},
    jsDebugStatsClientIdForRequest: () => 'client-1',
    performanceNow: () => 0,
    reloadClientJourneyId: 'j-reload-test',
    bootstrap: {clientRevision: 'test-revision'},
    navigator: {userAgent: 'Mozilla/5.0 Chrome/140.0'},
    window: {location: {origin: 'https://localhost:7774', pathname: '/'}},
    jsDebugEndpointText: value => String(value || '').split('?', 1)[0],
    redactDiagnosticValue: value => value,
  };
  vm.runInNewContext(`${failureHelpers}\n${failureClassifier}\n${sourceFunction('jsDebugCurrentFailureCorrelation', 'jsDebugCurrentObservationReceiptBarrier')}\n${functionText}\nresult = jsDebugCurrentObservationFromEvent({
    key: 'epoch-1:stats:1',
    event: {
      type: 'stats_history', level: 'warning', ts: '2026-08-05T12:00:00.000Z',
      message: 'YO!stats stream initialization unavailable: stats capabilities fields are not exact',
      route: '/api/stats-stream', requestId: 'r-stats-1', eventType: 'stats-generation',
      wallTime: '2026-08-05 05:00:00 PDT', deliveryOutcome: 'stalled', typedText: 'do not upload',
    },
  });`, context);
  assert.equal(context.result.payload.kind, 'warning');
  assert.equal(context.result.payload.source, '/api/stats-stream');
  assert.equal(context.result.payload.request_id, 'r-stats-1');
  assert.equal(context.result.payload.route, '/api/stats-stream');
  assert.equal(context.result.payload.event_type, 'stats-generation');
  assert.equal(context.result.payload.wall_time, '2026-08-05 05:00:00 PDT');
  assert.equal(context.result.payload.delivery_outcome, 'stalled');
  assert.match(context.result.payload.signature, /^jsf-[0-9a-f]{8}$/);
  assert.equal(Object.hasOwn(context.result.payload, 'typedText'), false);
});

testAsync('browser observation writer fences acknowledge, retry authentication, and discard only rejected batches', async () => {
  const uploaderSource = source.slice(
    source.indexOf('function jsDebugCurrentObservationEventSnapshot('),
    source.indexOf('\nfunction finalizeJsDebugCurrentObservationBytes('),
  );
  const endpointStart = coreSource.indexOf('function jsDebugEndpointText(');
  const endpointSource = coreSource.slice(endpointStart, coreSource.indexOf('\nfunction jsDebugRoundedMs(', endpointStart));
  const byteLengthStart = coreSource.indexOf('function utf8ByteLength(');
  const byteLengthSource = coreSource.slice(byteLengthStart, coreSource.indexOf('\nfunction domDataAttributeName(', byteLengthStart));
  const failureClassifierSource = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureClassification('),
    coreSource.indexOf('\nfunction jsDebugFailureEvents('),
  );
  const recordEventSource = coreSource.slice(
    coreSource.indexOf('function recordJsDebugEvent('),
    coreSource.indexOf('\nfunction jsDebugFailureClassification('),
  );
  const clearEventsSource = coreSource.slice(
    coreSource.indexOf('function clearJsDebugEvents('),
    coreSource.indexOf('\nfunction runJsDebugPanelRefresh('),
  );
  const memoryStorage = initial => {
    const values = new Map();
    if (initial !== null && initial !== undefined) values.set('yolomux.current-observation-receipts.v1', String(initial));
    return {
      getItem: key => values.has(String(key)) ? values.get(String(key)) : null,
      setItem: (key, next) => { values.set(String(key), String(next)); },
      removeItem: key => { values.delete(String(key)); },
      value: (key = 'yolomux.current-observation-receipts.v1') => values.get(String(key)) ?? null,
    };
  };
  const makeUploader = (fence, epoch, journal = undefined, durableJournal = undefined, identity = {}) => {
    const requests = [];
    const outcomes = [];
    const timers = [];
    const primaryStorage = journal === undefined ? memoryStorage(null) : journal;
    const fallbackStorage = durableJournal === undefined ? primaryStorage : durableJournal;
    const context = {
      requests,
      outcomes,
      jsDebugEvents: [],
      sessionStorage: primaryStorage,
      localStorage: fallbackStorage,
      setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; },
      clearTimeout(_timer) {},
      apiFetchJsonQuiet: async (url, options) => {
        const body = JSON.parse(options.body);
        requests.push({url, body});
        const outcome = await (outcomes.shift() || {ok: true, accepted: body.observations.length, duplicates: 0});
        if (outcome.status) throw outcome;
        if (outcome.ok === true && !Object.hasOwn(outcome, 'observation_receipts')) {
          const accepted = Number.isSafeInteger(outcome.accepted) ? outcome.accepted : 0;
          return {
            ...outcome,
            observation_receipts: body.observations.map((observation, index) => ({
              event_id: observation.event_id,
              disposition: index < accepted ? 'accepted' : 'duplicate',
            })),
          };
        }
        return outcome;
      },
      jsDebugStatsClientIdForRequest: () => 'client-1',
      performanceNow: () => 0,
      jsDebugRoundedMs: value => Number(value),
      jsDebugFailureSignature: () => 'jsf-deadbeef',
      jsDebugFailureText: value => String(value || '').slice(0, 500),
      jsDebugFailureStack: value => String(value?.stack || '').slice(0, 4000),
      jsDebugFailureSource: value => String(value || '/').split('?', 1)[0],
      redactDiagnosticValue: value => value,
      reloadClientJourneyId: identity.journeyId || 'j-reload-test',
      bootstrap: {clientRevision: identity.codeRevision || 'test-revision'},
      navigator: {userAgent: identity.userAgent || 'Mozilla/5.0 Chrome/140.0'},
    };
    vm.runInNewContext(`
      function browserStorageLegacyKey(key) { return String(key).startsWith('yomux.') ? 'yolomux.' + String(key).slice(6) : ''; }
      function browserStorageGet(storage, key) { const canonical = storage.getItem(key); const legacy = browserStorageLegacyKey(key); return canonical !== null ? canonical : legacy ? storage.getItem(legacy) : null; }
      function browserStorageSet(storage, key, value) { storage.setItem(key, String(value)); const legacy = browserStorageLegacyKey(key); if (legacy) storage.setItem(legacy, String(value)); }
      function browserStorageRemove(storage, key) { storage.removeItem(key); const legacy = browserStorageLegacyKey(key); if (legacy) storage.removeItem(legacy); }
      function sessionStorageSet(key, value) { browserStorageSet(sessionStorage, key, value); }
      function sessionStorageRemove(key) { browserStorageRemove(sessionStorage, key); }
      function storageSet(key, value) { browserStorageSet(localStorage, key, value); }
      const statsWriterFence = ${JSON.stringify(fence)};
      const jsDebugCurrentObservationBatchDelayMs = 10000;
      const jsDebugCurrentObservationRetryMaxMs = 300000;
      const jsDebugCurrentObservationState = {queue: [], keys: new Set(), nextHealthId: 1, timer: null, inFlight: false, retryMs: 10000, epoch: ${JSON.stringify(epoch)}, highWaterDepth: 0, drops: 0, retries: 0, instrumentationCostMs: 0, receipts: new Map()};
      let observationLifecycleCurrent = true;
      function currentObservationLifecycleScope() { return {current() { return observationLifecycleCurrent; }, ownTimer() {}, release() { return false; }}; }
      ${canonicalJsonValueSource}
      ${endpointSource}
      ${byteLengthSource}
      ${failureClassifierSource}
      ${clientCapabilityFixtureSource(true)}
      ${uploaderSource}
      globalThis.testApi = {
        state: jsDebugCurrentObservationState,
        queue: queueJsDebugCurrentObservation,
        flush: flushJsDebugCurrentObservations,
        retire: () => { observationLifecycleCurrent = false; },
        barrier: jsDebugCurrentObservationReceiptBarrier,
        projection: jsDebugCurrentObservationReceiptProjection,
        persist: persistJsDebugCurrentObservationReceipts,
      };
    `, context);
    return {...context, timers, api: context.testApi};
  };
  const installEventClearLifecycle = uploader => {
    vm.runInNewContext(`
      let jsDebugEventSeq = 0;
      const jsDebugEventLimit = 1000;
      const terminalRemovalLatencyPending = new Map();
      let terminalRemovalLatencySamples = [];
      let jsDebugRenderTimer = null;
      let jsDebugRenderForce = false;
      let jsDebugRenderDragDeferred = false;
      function diagnosticPacificWallTime() { return '2026-08-06 12:00:00 PDT'; }
      function recordJsDebugEventForGraph(event) {
        testApi.queue(\`${uploader.api.state.epoch}:\${event.id}\`, event);
      }
      function scheduleJsDebugPanelRefresh() {}
      function clearClientPerfCounters() {}
      function clearJsDebugGraphData() {}
      function clearJsDebugServerHistory() {}
      function renderDebugPanels() {}
      ${recordEventSource}
      ${clearEventsSource}
      globalThis.eventLifecycleApi = {record: recordJsDebugEvent, clear: clearJsDebugEvents};
    `, uploader);
    return uploader.eventLifecycleApi;
  };
  const event = {type: 'api', ts: '2026-07-17T12:00:00.000Z', durationMs: 12, requestBytes: 10, responseBytes: 20};
  const empty = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-empty');
  await empty.api.flush();
  assert.equal(empty.requests.length, 0, 'zero observations never create an empty production upload');

  const current = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-1');
  current.api.queue('page-1:error:1', {...event, type: 'error', message: 'accepted failure'});
  current.api.queue('page-1:error:1', {...event, type: 'error', message: 'accepted failure'});
  assert.equal(current.api.state.queue.length, 1, 'stable event keys deduplicate before upload');
  current.api.state.timer = null;
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 0, 'durable acknowledgement removes accepted entries');
  assert.deepEqual(JSON.parse(JSON.stringify(current.api.barrier('page-1'))), {
    epoch: 'page-1', accepted: 1, pending: 0, retrying: 0, rejected: 0, dropped: 0, quiescent: true, blocking: [],
  }, 'an accepted event reaches the event/epoch receipt barrier before fixture retirement');
  assert.deepEqual(current.requests[0].body.protocol_version, 24);
  assert.deepEqual(current.requests[0].body.schema_generation, 5);

  const retired = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-retired');
  let rejectRetiredUpload;
  retired.outcomes.push(new Promise((_resolve, reject) => { rejectRetiredUpload = reject; }));
  retired.api.queue('page-retired:error:1', {...event, type: 'error', message: 'retired failure'});
  retired.api.state.timer = null;
  const retiredUpload = retired.api.flush();
  retired.api.retire();
  rejectRetiredUpload({status: 503});
  await retiredUpload;
  assert.equal(retired.api.state.retries, 0, 'a request failure after lifecycle retirement cannot mutate retry diagnostics');
  assert.equal(retired.api.state.queue.length, 1, 'a retired request cannot discard or reclassify the pending observation');
  assert.equal(retired.api.barrier('page-retired').pending, 1, 'the untouched receipt remains pending for a resumed lifecycle');
  assert.equal(retired.api.state.inFlight, null, 'retirement restores the in-flight null sentinel after the stale request settles');

  const clearedAccepted = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-clear-accepted');
  const clearedAcceptedEvents = installEventClearLifecycle(clearedAccepted);
  const acceptedBeforeClear = clearedAcceptedEvents.record('error', {message: 'accepted before clear'});
  clearedAccepted.api.state.timer = null;
  await clearedAccepted.api.flush();
  clearedAcceptedEvents.clear();
  const acceptedAfterClear = clearedAcceptedEvents.record('error', {message: 'accepted after clear'});
  clearedAccepted.api.state.timer = null;
  await clearedAccepted.api.flush();
  assert.deepEqual(
    [acceptedBeforeClear.id, acceptedAfterClear.id],
    [1, 2],
    'clearing visible events preserves monotonically increasing durable event identity',
  );
  assert.deepEqual(
    [...clearedAccepted.api.state.receipts.keys()],
    ['page-clear-accepted:1', 'page-clear-accepted:2'],
    'accepted receipts before and after Clear retain distinct epoch/event keys',
  );
  assert.equal(clearedAccepted.api.barrier().accepted, 2, 'both accepted receipts survive visible Clear');

  const clearedPending = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-clear-pending');
  const clearedPendingEvents = installEventClearLifecycle(clearedPending);
  const pendingBeforeClear = clearedPendingEvents.record('error', {message: 'pending before clear'});
  clearedPendingEvents.clear();
  const pendingAfterClear = clearedPendingEvents.record('error', {message: 'pending after clear'});
  assert.deepEqual([pendingBeforeClear.id, pendingAfterClear.id], [1, 2]);
  assert.equal(clearedPending.api.state.queue.length, 2, 'Clear cannot suppress a later event behind a pending receipt key');
  assert.equal(clearedPending.api.barrier().pending, 2, 'pending receipts remain independently release-blocking across Clear');
  clearedPending.api.state.timer = null;
  await clearedPending.api.flush();
  assert.equal(clearedPending.api.barrier().accepted, 2, 'both pending identities receive independent terminal receipts');

  const classified = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-classified');
  for (const [suffix, failure] of [
    ['api', {...event, type: 'api', status: 500, ok: false, endpoint: '/api/fail'}],
    ['sse', {...event, type: 'sse', error: 'stream failed', eventType: 'stats'}],
    ['client', {...event, type: 'client_failure', error: 'graph failed', source: '/graph'}],
  ]) classified.api.queue(`page-classified:${suffix}:1`, failure);
  assert.equal(classified.api.barrier().pending, 3, 'every shared release-blocking failure class owns a durable receipt');
  assert.equal(classified.api.state.queue.every(entry => entry.releaseBlocking), true, 'release-blocking classification cannot diverge from the failure reader');
  classified.api.state.timer = null;
  await classified.api.flush();
  const failurePayloadFields = new Set([
    'kind', 'journey_id', 'code_revision', 'browser_family', 'signature', 'message', 'stack',
    'source', 'line', 'column', 'provenance', 'request_id', 'route', 'event_type',
    'wall_time', 'delivery_outcome', 'status',
  ]);
  for (const observation of classified.requests[0].body.observations) {
    assert.equal(
      Object.keys(observation.payload).every(field => failurePayloadFields.has(field)),
      true,
      `release-blocking ${observation.payload.kind} payload contains only the backend failure schema`,
    );
  }

  const stableRetry = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5},
    'page-stable-retry',
    undefined,
    undefined,
    {journeyId: 'j-first', codeRevision: 'revision-first', userAgent: 'Mozilla/5.0 Chrome/140.0'},
  );
  const mutableFailure = {
    ...event,
    id: 1,
    type: 'api',
    ok: false,
    endpoint: '/api/activity-summary',
    requestId: 'r-stable-retry',
    phaseTimings: {queueMs: 1, ttfbMs: 2},
  };
  stableRetry.api.queue('page-stable-retry:1', mutableFailure);
  stableRetry.api.state.timer = null;
  stableRetry.outcomes.push({status: 503});
  await stableRetry.api.flush();
  const firstRetryObservation = JSON.parse(JSON.stringify(stableRetry.requests[0].body.observations[0]));
  mutableFailure.endpoint = '/api/mutated-after-queue';
  mutableFailure.phaseTimings.queueMs = 999;
  stableRetry.bootstrap.clientRevision = 'revision-after-queue';
  stableRetry.navigator.userAgent = 'Mozilla/5.0 Firefox/140.0';
  stableRetry.api.state.timer = null;
  await stableRetry.api.flush();
  assert.deepEqual(
    stableRetry.requests[1].body.observations[0],
    firstRetryObservation,
    'one event identity retries byte-equivalent facts after its source object and producer context change',
  );

  for (const [accepted, duplicates] of [[2, 0], [1, 1], [0, 2]]) {
    const counted = makeUploader({protocolVersion: 24, schemaGeneration: 5}, `page-count-${accepted}-${duplicates}`);
    for (let index = 0; index < 2; index += 1) {
      counted.api.queue(`page-count-${accepted}-${duplicates}:error:${index}`, {...event, type: 'error', message: `counted ${index}`});
    }
    counted.api.state.timer = null;
    counted.outcomes.push({ok: true, source_generation: 8, accepted, duplicates});
    await counted.api.flush();
    assert.equal(counted.api.state.queue.length, 0, `accepted=${accepted} duplicates=${duplicates} acknowledges the complete batch`);
    assert.equal(counted.api.barrier().accepted, 2, 'new and duplicate server rows are both durable receipt outcomes');
  }

  const concurrent = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-concurrent');
  concurrent.api.queue('page-concurrent:error:1', {...event, type: 'error', message: 'concurrent receipt'});
  concurrent.api.state.timer = null;
  let resolveConcurrentReceipt;
  concurrent.outcomes.push(new Promise(resolve => { resolveConcurrentReceipt = resolve; }));
  const originalFlush = concurrent.api.flush();
  concurrent.api.queue('page-concurrent:error:2', {...event, type: 'error', message: 'next batch'});
  let joinedFlushSettled = false;
  const joinedFlush = concurrent.api.flush().then(() => { joinedFlushSettled = true; });
  await Promise.resolve();
  assert.equal(joinedFlushSettled, false, 'a concurrent flush joins the request carrying its event/epoch receipt');
  resolveConcurrentReceipt({ok: true, source_generation: 8, accepted: 1, duplicates: 0});
  await Promise.all([originalFlush, joinedFlush]);
  assert.equal(concurrent.api.barrier('page-concurrent').accepted, 1, 'all flush waiters observe the exact server receipt transition');
  assert.equal(concurrent.api.barrier('page-concurrent').pending, 1, 'an event queued during the request retains its own later batch');
  assert.equal(concurrent.requests[0].body.observations.length, 1, 'the first acknowledgement removes only the entries in its request');
  concurrent.api.state.timer = null;
  await concurrent.api.flush();
  assert.equal(concurrent.api.barrier('page-concurrent').quiescent, true, 'the later batch transitions independently after its own receipt');

  const partial = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-partial');
  for (let index = 0; index < 2; index += 1) {
    partial.api.queue(`page-partial:error:${index}`, {...event, type: 'error', message: `partial ${index}`});
  }
  partial.api.state.timer = null;
  partial.outcomes.push({ok: true, source_generation: 8, accepted: 1, duplicates: 0});
  await partial.api.flush();
  assert.equal(partial.api.state.queue.length, 2, 'a partial-count response retains the complete batch for idempotent retry');
  assert.equal(partial.api.barrier().retrying, 2, 'a partial-count response cannot retire either exact event receipt');
  partial.api.state.timer = null;
  partial.outcomes.push({ok: true, source_generation: 8, accepted: 0, duplicates: 2});
  await partial.api.flush();
  assert.equal(partial.api.barrier().quiescent, true, 'duplicate replay durably acknowledges the retained batch');

  const malformedCount = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-malformed-count');
  malformedCount.api.queue('page-malformed-count:error:1', {...event, type: 'error', message: 'typed count required'});
  malformedCount.api.state.timer = null;
  malformedCount.outcomes.push({ok: true, source_generation: 8, accepted: '1', duplicates: 0});
  await malformedCount.api.flush();
  assert.equal(malformedCount.api.barrier().retrying, 1, 'response parsing rejects counts outside the integer server schema');

  const malformedMappings = [
    [{event_id: 'wrong-event', disposition: 'accepted'}],
    [{event_id: 'page-malformed-mapping:error:1', disposition: 'unknown'}],
    [
      {event_id: 'page-malformed-mapping:error:1', disposition: 'accepted'},
      {event_id: 'page-malformed-mapping:error:1', disposition: 'duplicate'},
    ],
  ];
  for (const observationReceipts of malformedMappings) {
    const malformedMapping = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-malformed-mapping');
    malformedMapping.api.queue('page-malformed-mapping:error:1', {...event, type: 'error', message: 'exact mapping required'});
    malformedMapping.api.state.timer = null;
    malformedMapping.outcomes.push({
      ok: true, source_generation: 8, accepted: 1, duplicates: 0,
      observation_receipts: observationReceipts,
    });
    await malformedMapping.api.flush();
    assert.equal(malformedMapping.api.state.queue.length, 1, 'a malformed per-event mapping retires no event');
    assert.equal(malformedMapping.api.barrier().retrying, 1, 'a malformed per-event mapping remains release-blocking');
  }

  const unresolved = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-unresolved');
  unresolved.api.queue('page-unresolved:error:1', {...event, type: 'error', message: 'receipt required'});
  unresolved.api.state.timer = null;
  unresolved.outcomes.push({ok: true, accepted: 0, duplicates: 0});
  await unresolved.api.flush();
  assert.equal(unresolved.api.state.queue.length, 1, 'a resolved response without the batch receipt retains the original browser failure');
  assert.deepEqual(JSON.parse(JSON.stringify(unresolved.api.barrier('page-unresolved'))), {
    epoch: 'page-unresolved', accepted: 0, pending: 0, retrying: 1, rejected: 0, dropped: 0, quiescent: false,
    blocking: [{key: 'page-unresolved:error:1', epoch: 'page-unresolved', requestId: '', source: '/', route: '/', event: 'error', wallTime: '', deliveryOutcome: 'failed', httpStatus: null, status: 'retrying'}],
  }, 'fixture retirement fails closed while the original browser failure is retrying');

  const journal = memoryStorage(null);
  const beforeReload = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-before-reload', journal);
  beforeReload.api.queue('page-before-reload:1', {
    id: 1, type: 'stats_history', level: 'warning', ts: '2026-07-17T12:00:00.000Z',
    message: 'reload receipt', route: '/api/stats-stream', requestId: 'r-reload', wallTime: '2026-07-17 05:00:00 PDT',
  });
  beforeReload.api.state.timer = null;
  beforeReload.outcomes.push({status: 503});
  await beforeReload.api.flush();
  const beforeReloadObservation = JSON.parse(JSON.stringify(beforeReload.requests[0].body.observations[0]));
  const restoredReceipt = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5},
    'page-after-reload',
    journal,
    undefined,
    {journeyId: 'j-after-reload', codeRevision: 'revision-after-reload', userAgent: 'Mozilla/5.0 Firefox/140.0'},
  );
  assert.equal(restoredReceipt.api.state.queue.length, 1, 'a reload restores an unacknowledged release-blocking warning');
  assert.equal(restoredReceipt.api.barrier().quiescent, false, 'the default release barrier aggregates restored non-accepted epochs');
  assert.equal(restoredReceipt.api.barrier('page-before-reload').quiescent, false, 'the old epoch remains non-quiescent after a new page epoch starts');
  restoredReceipt.api.state.timer = null;
  await restoredReceipt.api.flush();
  assert.equal(restoredReceipt.requests[0].body.observations[0].epoch_id, 'page-before-reload', 'the receipt stays keyed to the original page epoch');
  assert.deepEqual(
    restoredReceipt.requests[0].body.observations[0],
    beforeReloadObservation,
    'reload retry preserves the exact observation bytes attached to the original event identity',
  );
  assert.equal(restoredReceipt.api.barrier().quiescent, true, 'the global release barrier clears only after every restored epoch is acknowledged');
  assert.equal(restoredReceipt.api.barrier('page-before-reload').quiescent, true, 'the restored warning retires only after its server receipt');

  const malformedPrimary = memoryStorage('{malformed');
  const malformedFallback = memoryStorage(null);
  const malformedReload = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-malformed-reload', malformedPrimary, malformedFallback,
  );
  assert.equal(malformedReload.api.barrier().quiescent, false, 'malformed receipt JSON restores a global release blocker');
  assert.equal(malformedPrimary.value(), '{malformed', 'malformed underlying receipt evidence is not overwritten or removed');

  const incompleteAcceptedRaw = JSON.stringify({
    entries: [], receipts: [{key: 'x', epoch: 'old', status: 'accepted'}],
  });
  const incompleteAcceptedPrimary = memoryStorage(incompleteAcceptedRaw);
  const incompleteAcceptedFallback = memoryStorage(null);
  const incompleteAccepted = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-incomplete-accepted',
    incompleteAcceptedPrimary, incompleteAcceptedFallback,
  );
  assert.equal(incompleteAccepted.api.barrier().quiescent, false, 'an accepted receipt missing production correlation fields fails closed');
  assert.equal(incompleteAcceptedPrimary.value(), incompleteAcceptedRaw, 'the incomplete accepted receipt remains byte-identical');

  const strictReceipt = (overrides = {}) => ({
    key: 'old:7',
    epoch: 'old',
    eventId: 7,
    requestId: '',
    source: '/',
    route: '/',
    event: 'error',
    wallTime: '',
    deliveryOutcome: 'failed',
    httpStatus: null,
    status: 'accepted',
    ...overrides,
  });
  const validAcceptedStorage = memoryStorage(JSON.stringify({entries: [], receipts: [strictReceipt()]}));
  const validAccepted = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-valid-accepted', validAcceptedStorage, memoryStorage(null),
  );
  assert.equal(validAccepted.api.barrier().quiescent, true, 'the exact production accepted-receipt schema restores cleanly');
  assert.equal(validAccepted.api.barrier().accepted, 1, 'the exact production accepted receipt remains visible as accepted history');
  for (const status of ['accepted', 'pending', 'retrying', 'rejected', 'dropped']) {
    const receipt = strictReceipt({status});
    const entries = ['pending', 'retrying'].includes(status)
      ? [{key: receipt.key, epoch: receipt.epoch, event: {id: 7, type: 'error'}, releaseBlocking: true}]
      : [];
    const restored = makeUploader(
      {protocolVersion: 24, schemaGeneration: 5}, `page-valid-status-${status}`,
      memoryStorage(JSON.stringify({entries, receipts: [receipt]})), memoryStorage(null),
    );
    assert.equal(restored.api.barrier()[status], 1, `the exact ${status} receipt schema restores its status`);
    assert.equal(restored.api.barrier().quiescent, status === 'accepted', `the ${status} receipt has the exact release-barrier behavior`);
  }
  const requiredReceiptMutations = [];
  for (const [field, wrongType] of [
    ['key', 7],
    ['epoch', 7],
    ['eventId', '7'],
    ['requestId', null],
    ['source', null],
    ['route', null],
    ['event', null],
    ['wallTime', null],
    ['deliveryOutcome', null],
    ['httpStatus', '200'],
    ['status', 7],
  ]) {
    const missing = strictReceipt();
    delete missing[field];
    requiredReceiptMutations.push([`missing-${field}`, missing]);
    requiredReceiptMutations.push([`typed-${field}`, strictReceipt({[field]: wrongType})]);
  }
  requiredReceiptMutations.push(
    ['unknown-field', strictReceipt({unexpected: true})],
    ['unsafe-epoch', strictReceipt({key: 'bad epoch:7', epoch: 'bad epoch'})],
    ['slash-epoch', strictReceipt({key: 'bad/epoch:7', epoch: 'bad/epoch'})],
    ['long-epoch', strictReceipt({key: `${'e'.repeat(129)}:7`, epoch: 'e'.repeat(129)})],
    ['key-epoch-mismatch', strictReceipt({key: 'other:7'})],
    ['key-event-id-mismatch', strictReceipt({key: 'old:8'})],
    ['negative-event-id', strictReceipt({key: 'old:-1', eventId: -1})],
    ['empty-event', strictReceipt({event: ''})],
    ['long-event', strictReceipt({event: 'e'.repeat(65)})],
    ['empty-delivery', strictReceipt({deliveryOutcome: ''})],
    ['long-delivery', strictReceipt({deliveryOutcome: 'd'.repeat(33)})],
    ['long-request-id', strictReceipt({requestId: 'r'.repeat(129)})],
    ['long-source', strictReceipt({source: `/${'s'.repeat(240)}`})],
    ['long-route', strictReceipt({route: `/${'r'.repeat(240)}`})],
    ['long-wall-time', strictReceipt({wallTime: 'w'.repeat(65)})],
    ['control-request-id', strictReceipt({requestId: 'bad\nrequest'})],
    ['low-http-status', strictReceipt({httpStatus: 99})],
    ['high-http-status', strictReceipt({httpStatus: 600})],
    ['accepted-global-blocker', strictReceipt({globalBlocker: true})],
    ['normal-global-flag', strictReceipt({globalBlocker: false})],
    ['reserved-epoch', strictReceipt({key: '*:7', epoch: '*'})],
    ['special-key-normal-shape', strictReceipt({key: '__yolomux_receipt_journal_overflow__'})],
  );
  const strictOverflow = {
    key: '__yolomux_receipt_journal_overflow__', epoch: '*', eventId: null, requestId: '',
    source: '/', route: '/', event: 'receipt_journal_overflow', wallTime: '',
    deliveryOutcome: 'dropped', httpStatus: null, status: 'dropped', globalBlocker: true,
    journalOverflow: true, omitted: 1,
  };
  requiredReceiptMutations.push(
    ['overflow-accepted-global', {...strictOverflow, status: 'accepted'}],
    ['overflow-global-false', {...strictOverflow, globalBlocker: false}],
    ['overflow-zero-omitted', {...strictOverflow, omitted: 0}],
    ['overflow-extra-field', {...strictOverflow, unexpected: true}],
  );
  for (const [label, receipt] of requiredReceiptMutations) {
    const raw = JSON.stringify({entries: [], receipts: [receipt]});
    const primary = memoryStorage(raw);
    const fallback = memoryStorage(null);
    const invalid = makeUploader(
      {protocolVersion: 24, schemaGeneration: 5}, `page-strict-${label}`, primary, fallback,
    );
    assert.equal(invalid.api.barrier().quiescent, false, `strict receipt schema fails closed: ${label}`);
    invalid.api.persist();
    assert.equal(primary.value(), raw, `strict receipt schema preserves underlying evidence: ${label}`);
    const reloaded = makeUploader(
      {protocolVersion: 24, schemaGeneration: 5}, `page-strict-reload-${label}`, primary, fallback,
    );
    assert.equal(reloaded.api.barrier().quiescent, false, `strict receipt schema remains globally blocking across reload: ${label}`);
  }

  for (const [label, saved] of [
    ['null-root', 'null'],
    ['array-root', '[]'],
    ['entries-not-array', JSON.stringify({entries: {}, receipts: []})],
    ['receipt-not-object', JSON.stringify({entries: [], receipts: [null]})],
    ['receipt-status', JSON.stringify({entries: [], receipts: [{key: 'bad', epoch: 'old', status: 'unknown'}]})],
    ['entry-without-receipt', JSON.stringify({entries: [{key: 'lost', epoch: 'old', event: {type: 'error'}, releaseBlocking: true}], receipts: []})],
  ]) {
    const invalidPrimary = memoryStorage(saved);
    const invalid = makeUploader(
      {protocolVersion: 24, schemaGeneration: 5}, `page-invalid-${label}`, invalidPrimary, memoryStorage(null),
    );
    assert.equal(invalid.api.barrier().quiescent, false, `schema-invalid receipt journal fails closed: ${label}`);
    assert.equal(invalidPrimary.value(), saved, `schema-invalid underlying evidence remains intact: ${label}`);
  }

  const readFailureStorage = {
    getItem() { throw new Error('receipt storage read failed'); },
    setItem() { throw new Error('receipt storage write failed'); },
    removeItem() { throw new Error('receipt storage remove failed'); },
  };
  const readFailure = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-read-failure', readFailureStorage, memoryStorage(null),
  );
  assert.equal(readFailure.api.barrier().quiescent, false, 'receipt storage read failure restores a global release blocker');

  const writeFailureFallback = memoryStorage(null);
  const writeFailure = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-write-failure', readFailureStorage, writeFailureFallback,
  );
  writeFailure.api.queue('page-write-failure:1', {...event, id: 1, type: 'error', message: 'unwritable receipt'});
  const writeFailureReload = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-write-failure-reload', memoryStorage(null), writeFailureFallback,
  );
  assert.equal(writeFailureReload.api.barrier().quiescent, false, 'a write failure leaves a durable global blocker for reload');
  writeFailureReload.api.state.timer = null;
  await writeFailureReload.api.flush();
  assert.equal(
    writeFailureReload.api.barrier().quiescent,
    true,
    `a healthy primary acknowledges the restored fallback entry and clears the storage blocker: ${JSON.stringify(writeFailureReload.api.barrier())}`,
  );
  assert.equal(
    writeFailureFallback.value('yolomux.current-observation-receipts.v1.fallback'), null,
    'successful recovery removes the fallback journal only after the primary journal is healthy',
  );
  assert.equal(
    writeFailureFallback.value('yolomux.current-observation-receipts.v1.failure'), null,
    'successful recovery removes the durable failure marker',
  );

  const recoveryRaceBacking = memoryStorage(null);
  recoveryRaceBacking.setItem('yolomux.current-observation-receipts.v1.failure', '{"schema":1,"reason":"prior_failure"}');
  let recoveryJournalWrites = 0;
  const recoveryRacePrimary = {
    getItem: key => recoveryRaceBacking.getItem(key),
    setItem(key, value) {
      if (key === 'yolomux.current-observation-receipts.v1') {
        recoveryJournalWrites += 1;
        if (recoveryJournalWrites === 2) throw new Error('recovery journal write raced with storage revocation');
      }
      recoveryRaceBacking.setItem(key, value);
    },
    removeItem: key => recoveryRaceBacking.removeItem(key),
  };
  const recoveryRace = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-recovery-race', recoveryRacePrimary, memoryStorage(null),
  );
  assert.doesNotThrow(() => recoveryRace.api.persist(), 'a recovery rewrite race is contained by the persistence boundary');
  assert.equal(recoveryJournalWrites, 0, 'empty recovery removes the primary journal instead of adding a race-prone rewrite');
  assert.equal(recoveryRace.api.barrier().quiescent, true, 'the verified primary journal clears the restored storage blocker');

  const cleanupRaceBacking = memoryStorage(null);
  cleanupRaceBacking.setItem('yolomux.current-observation-receipts.v1.failure', '{"schema":1,"reason":"prior_failure"}');
  let failCleanup = true;
  const cleanupRaceStorage = {
    getItem: key => cleanupRaceBacking.getItem(key),
    setItem: (key, value) => cleanupRaceBacking.setItem(key, value),
    removeItem(key) {
      if (failCleanup && key === 'yolomux.current-observation-receipts.v1.failure') {
        failCleanup = false;
        throw new Error('recovery cleanup raced with storage revocation');
      }
      cleanupRaceBacking.removeItem(key);
    },
  };
  const cleanupRace = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-cleanup-race', cleanupRaceStorage,
  );
  assert.doesNotThrow(() => cleanupRace.api.persist(), 'a recovery cleanup race is contained by the persistence boundary');
  assert.equal(cleanupRace.api.barrier().quiescent, false, 'a failed recovery cleanup retains the global storage blocker');

  const throwingLocalStorage = {
    getItem() { return null; },
    setItem() { throw new Error('local storage quota failure'); },
    removeItem() { throw new Error('local storage quota failure'); },
  };
  const localWriteFailure = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-local-write-failure', memoryStorage(null), throwingLocalStorage,
  );
  assert.equal(localWriteFailure.api.barrier().quiescent, false, 'unwritable durable marker storage fails closed on every load');

  const journalReceipt = (epoch, eventId, status) => ({
    key: `${epoch}:${eventId}`, epoch, eventId, requestId: '', source: '/', route: '/',
    event: 'error', wallTime: '', deliveryOutcome: 'failed', httpStatus: null, status,
  });
  const duplicateKey = 'page-duplicate:1';
  const duplicateReceipt = journalReceipt('page-duplicate', 1, 'retrying');
  const duplicateJournal = eventValue => JSON.stringify({
    entries: [{key: duplicateKey, epoch: 'page-duplicate', event: eventValue, releaseBlocking: true}],
    receipts: [duplicateReceipt],
  });
  const duplicatePrimary = memoryStorage(duplicateJournal({type: 'error', message: 'same'}));
  const duplicateFallback = memoryStorage(null);
  const duplicateFallbackEntry = {
    releaseBlocking: true,
    event: {message: 'same', type: 'error'},
    epoch: 'page-duplicate',
    key: duplicateKey,
  };
  duplicateFallback.setItem(
    'yolomux.current-observation-receipts.v1.fallback',
    JSON.stringify({receipts: [Object.fromEntries(Object.entries(duplicateReceipt).reverse())], entries: [duplicateFallbackEntry]}, null, 2),
  );
  const duplicateReload = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-duplicate-reload', duplicatePrimary, duplicateFallback,
  );
  assert.equal(duplicateReload.api.state.queue.length, 1, 'identical primary/fallback entries merge once');
  assert.equal(duplicateReload.api.state.receiptStorageCorrupt, undefined, 'an identical duplicate journal is not a conflict');

  const conflictPrimary = memoryStorage(duplicateJournal({type: 'error', message: 'primary'}));
  const conflictFallback = memoryStorage(null);
  conflictFallback.setItem(
    'yolomux.current-observation-receipts.v1.fallback',
    duplicateJournal({type: 'error', message: 'fallback'}),
  );
  const conflictReload = makeUploader(
    {protocolVersion: 24, schemaGeneration: 5}, 'page-conflict-reload', conflictPrimary, conflictFallback,
  );
  assert.equal(conflictReload.api.state.receiptStorageCorrupt, true, 'conflicting same-key queued records fail closed');
  assert.ok(
    conflictReload.api.barrier('unrelated-epoch').blocking.some(receipt => receipt.storageFailure === 'journal_conflict'),
    'journal conflict is a global blocker for every epoch-specific release barrier',
  );
  for (const blockingStatus of ['rejected', 'retrying']) {
    const boundedStorage = memoryStorage(null);
    const bounded = makeUploader({protocolVersion: 24, schemaGeneration: 5}, `page-${blockingStatus}`, boundedStorage);
    const blockerKey = `page-${blockingStatus}:0`;
    bounded.api.state.receipts.set(blockerKey, journalReceipt(`page-${blockingStatus}`, 0, blockingStatus));
    for (let index = 0; index < 500; index += 1) {
      const key = `page-${blockingStatus}:${index + 1}`;
      bounded.api.state.receipts.set(key, journalReceipt(`page-${blockingStatus}`, index + 1, 'accepted'));
    }
    bounded.api.persist();
    const saved = JSON.parse(boundedStorage.value());
    assert.ok(saved.receipts.length <= 500, 'the persisted receipt journal stays bounded');
    assert.ok(saved.receipts.some(receipt => receipt.status === blockingStatus), `the oldest ${blockingStatus} blocker survives accepted-history bounding`);
    const restored = makeUploader({protocolVersion: 24, schemaGeneration: 5}, `page-restored-${blockingStatus}`, boundedStorage);
    assert.equal(restored.api.barrier().quiescent, false, `restore fails closed for an oldest ${blockingStatus} receipt plus 500 accepted receipts`);
    assert.equal(restored.api.barrier()[blockingStatus], 1, `restore retains the exact ${blockingStatus} receipt status`);
  }

  const overflowStorage = memoryStorage(null);
  const overflow = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-overflow', overflowStorage);
  for (let index = 0; index < 501; index += 1) {
    const key = `page-overflow:${index}`;
    overflow.api.state.receipts.set(key, journalReceipt('page-overflow', index, index % 2 ? 'retrying' : 'rejected'));
  }
  overflow.api.persist();
  const savedOverflow = JSON.parse(overflowStorage.value());
  assert.ok(savedOverflow.receipts.length <= 500, 'a blocker-only journal remains bounded beyond capacity');
  const restoredOverflow = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-restored-overflow', overflowStorage);
  assert.equal(restoredOverflow.api.barrier().quiescent, false, 'blocker overflow restores a global fail-closed receipt');

  const acceptedStorage = memoryStorage(null);
  const acceptedOnly = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-accepted-only', acceptedStorage);
  for (let index = 0; index < 700; index += 1) {
    const key = `page-accepted-only:${index}`;
    acceptedOnly.api.state.receipts.set(key, journalReceipt('page-accepted-only', index, 'accepted'));
  }
  acceptedOnly.api.persist();
  const savedAccepted = JSON.parse(acceptedStorage.value());
  assert.equal(savedAccepted.receipts.length, 500, 'accepted-only history retains the newest bounded window');
  assert.equal(savedAccepted.receipts[0].key, 'page-accepted-only:200', 'accepted-only history drops only its oldest accepted rows');

  const capped = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-cap');
  for (let index = 0; index < 1001; index += 1) {
    capped.api.queue(`page-cap:api:${index}`, event);
  }
  assert.equal(capped.api.state.queue.length, 1000, 'the shared queue stays bounded');
  assert.equal(capped.api.state.drops, 1, 'overflow is counted rather than retained');

  const itemBatch = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-items');
  for (let index = 0; index < 101; index += 1) {
    itemBatch.api.queue(`page-items:api:${index}`, event);
  }
  itemBatch.api.state.timer = null;
  await itemBatch.api.flush();
  assert.equal(itemBatch.requests.length, 1, 'many events produce one batched request');
  assert.equal(itemBatch.requests[0].body.observations.length, 100, 'one batch contains at most 100 items');
  assert.equal(itemBatch.api.state.queue.length, 1, 'overflow waits for the next batch');

  const byteBatch = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-bytes');
  const largeFailure = {
    type: 'error', ts: '2026-07-17T12:00:00.000Z', message: 'failure',
    stack: `Error: failure\\n${'x'.repeat(3990)}`, source: '/static/yomux.js',
  };
  for (let index = 0; index < 100; index += 1) {
    byteBatch.api.queue(`page-bytes:error:${index}`, largeFailure);
  }
  byteBatch.api.state.timer = null;
  await byteBatch.api.flush();
  assert.ok(
    Buffer.byteLength(JSON.stringify(byteBatch.requests[0].body), 'utf8') <= 120 * 1024,
    'the serialized request stays within 120 KiB',
  );
  assert.ok(byteBatch.api.state.queue.length > 0, 'byte overflow waits for a later batch');

  current.api.queue('page-1:api:2', event);
  current.api.state.timer = null;
  current.outcomes.push({status: 503});
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 1, 'transient failure retains the queue');
  assert.equal(current.api.state.retryMs, 20000, 'transient failure doubles the bounded retry delay');
  assert.equal(current.api.state.retries, 1, 'transient failure increments durable upload health');
  assert.equal(current.timers.at(-1).delay, 10000, 'first retry waits one batch interval');

  current.api.state.timer = null;
  current.outcomes.push({status: 401});
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 1, 'an expired session retains its queued browser failure');
  assert.equal(current.api.state.retries, 2, 'authentication rejection is counted as a retry, not permanent death');
  current.api.queue('page-1:error:3', {...event, type: 'error', message: 'after-authentication'});
  current.api.state.timer = null;
  await current.api.flush();
  assert.equal(current.api.state.queue.length, 0, 'a later authenticated flush delivers the original and later error');
  assert.equal(current.requests.at(-1).body.observations.length, 2, 'the post-authentication request retains both queued observations');
  assert.ok(current.requests.at(-1).body.observations.some(
    observation => observation.payload.kind === 'error' && observation.payload.message === 'after-authentication',
  ), 'a later JavaScript failure survives the prior authentication rejection');

  const rejected = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-rejected');
  rejected.api.queue('page-rejected:api:1', event);
  rejected.api.state.timer = null;
  rejected.outcomes.push({status: 426});
  await rejected.api.flush();
  assert.equal(rejected.api.state.queue.length, 0, 'an upgrade rejection drops only its rejected batch');
  assert.equal(rejected.api.state.drops, 1, 'rejected batches are visible through upload_drops');
  rejected.api.queue('page-rejected:api:2', event);
  rejected.api.state.timer = null;
  await rejected.api.flush();
  assert.equal(rejected.requests.length, 2, 'a rejected batch never permanently stops later uploads');

  const reloaded = makeUploader({protocolVersion: 24, schemaGeneration: 5}, 'page-2');
  reloaded.api.queue('page-2:api:1', event);
  reloaded.api.state.timer = null;
  await reloaded.api.flush();
  assert.equal(reloaded.requests.length, 1);

  const missingFence = makeUploader(null, 'page-old');
  missingFence.api.queue('page-old:api:1', event);
  await missingFence.api.flush();
  assert.equal(missingFence.requests.length, 0, 'an invalid bootstrap fence never reaches the write endpoint');
  assert.equal(missingFence.api.state.queue.length, 1, 'a missing fence retains the observation instead of silently discarding it');
  assert.equal(missingFence.api.state.retries, 1, 'a missing fence is visible in uploader health');
  assert.equal(missingFence.timers.at(-1).delay, 10000, 'a missing fence schedules a bounded retry');
});

test('the established renderer consumes the protocol-v2 exact stream', () => {
  assert.match(source, /function debugGraphStatsCurrentModule\(\)[\s\S]*globalThis\.YOmuxStatsCurrent \|\| globalThis\.YOLOmuxStatsCurrent/);
  assert.match(source, /statsCurrent\.createBrowserClient/);
  assert.match(source, /onGeneration\(snapshot\)[\s\S]*paintJsDebugCurrentStatsGeneration/);
  assert.doesNotMatch(source, /onSnapshotProgress|paintJsDebugCurrentStatsProgress/);
  assert.match(source, /client\.select\(selection\.rangeSeconds, selection\.resolution\)/);
  assert.match(source, /onState\(state, error\)[\s\S]*requestedRangeSeconds: liveSelection\.rangeSeconds[\s\S]*error\?\.reason/);
  assert.match(source, /initialHistoryOverlayOwnsLoading \|\| jsDebugHistoryReadiness\.phase === 'error'/);
  assert.match(source, /function retryJsDebugHistory\(\)[\s\S]*client\.retry\(\)/);
  assert.match(source, /if \(jsDebugGraphExactResolutionEnabled\) return false;[\s\S]*function clearJsDebugGraphData/);
  const initializeSource = sourceFunction('initializeJsDebugStatsBeforeStreams', 'jsDebugTextForClipboard');
  assert.match(initializeSource, /syncJsDebugCurrentStatsClient\(\)/);
  assert.doesNotMatch(initializeSource, /await jsDebugCurrentStatsClientState\.startPromise/, 'hidden stats startup cannot delay normal page boot');
});

test('YO!stats startup begins before locale boot can delay the app shell', () => {
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const statsStart = boot.indexOf('initializeJsDebugStatsBeforeStreams');
  const localeWait = boot.indexOf('await applyLocale(resolveLocalePref');
  assert.ok(statsStart >= 0 && localeWait >= 0 && statsStart < localeWait);
});

test('stats-only boot does not schedule unrelated deferred settings metadata', () => {
  assert.match(
    editorSettingsSource,
    /function scheduleDeferredSettingsMetadataRefresh\(\)[\s\S]*?statsOnlyBootEnabled\(\)[\s\S]*?if \(!clientSettingsMetadataDeferred\)/,
  );
});

test('stats-only boot loads Tabber topology before returning', () => {
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const statsOnlyBranch = boot.slice(
    boot.indexOf('if (statsOnlyBootEnabled()) {'),
    boot.indexOf('\n  refreshBackgroundOwnerStatus', boot.indexOf('if (statsOnlyBootEnabled()) {')),
  );
  assert.match(statsOnlyBranch, /itemInLayout\(tabberItemId\)/, 'stats-only boot checks whether Tabber is present in the layout');
  assert.match(statsOnlyBranch, /await refreshTranscripts\(\{refreshAuto: false, refreshContext: false, refreshActivity: false\}\)/, 'stats-only boot fetches session metadata without full-app producers');
  assert.match(statsOnlyBranch, /await fetchTabberActivity\(\{visible: true, allowHidden: true\}\)/, 'stats-only boot fetches the global Tabber activity/topology API');
  assert.match(statsOnlyBranch, /adoptTabberActivityAsAutoStatusFallback\(\)/, 'stats-only boot promotes the Go activity snapshot into the shared live-status model');
  assert.match(statsOnlyBranch, /adoptTabberActivityAsAutoStatusFallback\(\);[\s\S]*renderPaneTabStrips\(\)/, 'stats-only boot paints the working marker after adopting activity');
  assert.match(statsOnlyBranch, /refreshTabberPanels\(\)/, 'stats-only boot paints the loaded Tabber rows');
  assert.match(boot, /function adoptTabberActivityAsAutoStatusFallback\(options = \{\}\)[\s\S]*new Set\(\[\.\.\.activeSessions, \.\.\.rosterSessions, \.\.\.Object\.keys\(sessionsPayload\)\]\)/, 'activity fallback covers a Debug-only layout before Tabber has populated activeSessions');
});

testAsync('stats-agent-status refresh is coalesced and bounded by the collector cadence', async () => {
  assert.match(source, /function debugStatsAgentStatusConsumerVisible\(\)[\s\S]*jsDebugStatsPanelVisible\(\)[\s\S]*debugRuntimeState\.subTab !== 'graph'[\s\S]*debugGraphChartVisible\('activity'\)/, 'agent status is requested only while the visible Graph activity detail can consume it');
  assert.match(source, /id: 'graph',[\s\S]*activate: \(\{forceAgentStatus = true\} = \{\}\) => \{[\s\S]*refreshDebugStatsAgentStatus\(\{force: forceAgentStatus\}\)/, 'opening Graph forces one fresh agent-status roster read');
  assert.match(source, /visibilitychange[\s\S]*syncDebugSubviewActivation\(\{pollNow: visible, forceAgentStatus: false\}\)/, 'visibility replay does not force a fresh agent-status read');
  assert.match(source, /const nextAgentWindowRevision = jsDebugCurrentSnapshotAgentWindowRevision\(snapshot\)[\s\S]*const agentStatusRevision = Number\(agentStatusState\?\.revision \|\| 0\)[\s\S]*nextAgentWindowRevision > 0 && nextAgentWindowRevision !== agentStatusRevision/, 'ordinary stats snapshots refresh the detailed roster only when the SSE revision changes');
  const functionText = source.slice(
    source.indexOf('async function refreshDebugStatsAgentStatus('),
    source.indexOf('\nfunction debugGraphLiveAgentWindowRows(', source.indexOf('async function refreshDebugStatsAgentStatus(')),
  );
  const context = {result: null};
  vm.runInNewContext(`
    let now = 1000;
    const Date = {now: () => now};
    const jsDebugStatsAgentStatusFreshnessMs = 10000;
    const jsDebugStatsAgentStatusState = {payload: null, revision: 0, inFlight: null, updatedAtMs: 0};
    let requests = 0;
    function statsOnlyBootEnabled() { return true; }
    function debugGraphStatsAgentWindowRows() { return {revision: 7}; }
    function refreshDebugAgentWindowLiveDetails() {}
    async function apiFetchJson() {
      requests += 1;
      return {available: true, agents: [], counts: {total: 0}};
    }
    ${functionText}
    (async () => {
      await refreshDebugStatsAgentStatus();
      await refreshDebugStatsAgentStatus();
      now += 9999;
      await refreshDebugStatsAgentStatus();
      now += 1;
      await refreshDebugStatsAgentStatus();
      await refreshDebugStatsAgentStatus({force: true});
      await refreshDebugStatsAgentStatus({force: true});
      result = requests;
    })();
  `, context);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(context.result, 3, 'repeated snapshot/SSE updates and lifecycle replays do not exceed one request per 10-second freshness window, while force refresh remains available');
});

test('a complete snapshot replaces graph data once and resolves readiness', () => {
  const functionText = sourceFunction('updateJsDebugCurrentSnapshotState', 'scheduleJsDebugStatsHistoryFlush');
  const context = {result: null};
  vm.runInNewContext(`
    const applied = [];
    const resolved = [];
    let jsDebugStatsServerSequence = 0;
    const jsDebugHistoryReadiness = {phase: 'loading-older', reason: 'range', requestedRangeSeconds: 900, overlayVisible: true};
    const jsDebugStatsPollState = {firstSampleReceived: true};
    function clearJsDebugGraphData() { applied.length = 0; }
    function jsDebugCurrentBucketRecord(bucket, includeRangeCost) { return {bucket, includeRangeCost}; }
    function debugGraphApplyServerRecord(record) { applied.push(record); }
    function jsDebugCurrentCoverageIntervals(snapshot) {
      return snapshot.buckets.map(bucket => ({startSeconds: bucket.start, endSeconds: bucket.start + bucket.duration}));
    }
    function debugGraphApplyUsageAtomBackfill() {}
    function jsDebugCurrentSnapshotAgentWindowRevision() { return 0; }
    function resolveDebugGraphResolutionChange() { resolved.push(true); }
    function armJsDebugStatsPolling() {}
    function scheduleJsDebugPanelRefresh() {}
    ${functionText}
    const snapshot = {
      range_seconds: 7200, window_start: 0, window_end: 7200, resolution_seconds: 60, cache_generation: 9,
      buckets: [{start: 0, duration: 60}, {start: 3600, duration: 60}],
      no_data: [], cost_report: {},
    };
    applyJsDebugCurrentSnapshot(snapshot);
    result = {
      phase: jsDebugHistoryReadiness.phase,
      requestedRangeSeconds: jsDebugHistoryReadiness.requestedRangeSeconds,
      overlayVisible: jsDebugHistoryReadiness.overlayVisible,
      intervals: jsDebugHistoryReadiness.requestCoverageIntervals,
      rangeCostFlags: applied.map(record => record.includeRangeCost),
      resolved: resolved.length,
    };
  `, context);
  assert.equal(context.result.phase, 'ready');
  assert.equal(context.result.requestedRangeSeconds, 7200, 'the accepted snapshot owns readiness range state');
  assert.equal(context.result.overlayVisible, false);
  assert.deepEqual([...context.result.intervals].map(interval => [interval.startSeconds, interval.endSeconds]), [[0, 7200]]);
  assert.deepEqual([...context.result.rangeCostFlags], [undefined, undefined]);
  assert.equal(context.result.resolved, 1);
});

test('eleven live deltas mutate only named graph buckets without clearing retained history', () => {
  const deleteSource = sourceFunction('debugGraphDeleteServerRecord', 'debugGraphCostOptionalInteger');
  const applySource = sourceFunction('updateJsDebugCurrentSnapshotState', 'scheduleJsDebugStatsHistoryFlush');
  const context = {result: null};
  vm.runInNewContext(`
    const jsDebugGraphRawBucketMs = 1000;
    const jsDebugGraphBuckets = new Map();
    let clearCalls = 0;
    const applied = [];
    let jsDebugStatsServerSequence = 0;
    const jsDebugHistoryReadiness = {};
    const jsDebugStatsPollState = {firstSampleReceived: true, agentWindowSnapshotRevision: 0};
    function clearJsDebugGraphData() { clearCalls += 1; jsDebugGraphBuckets.clear(); }
    function jsDebugCurrentBucketRecord(bucket) { return bucket; }
    function debugGraphApplyServerRecord(record) {
      applied.push(record.start);
      jsDebugGraphBuckets.set(String(record.start * 1000) + ':' + String(record.duration * 1000), record);
    }
    function jsDebugCurrentCoverageIntervals() { return []; }
    function debugGraphApplyUsageAtomBackfill() {}
    function jsDebugCurrentSnapshotAgentWindowRevision() { return 0; }
    function resolveDebugGraphResolutionChange() {}
    function armJsDebugStatsPolling() {}
    function scheduleJsDebugPanelRefresh() {}
    function debugGraphApplyServerCostSummary() {}
    ${deleteSource}
    ${applySource}

    let buckets = Array.from({length: 21}, (_unused, start) => ({start, duration: 1, open: start === 20}));
    let snapshot = {
      window_start: 0, window_end: 21, resolution_seconds: 1, cache_generation: 1,
      buckets, no_data: [], cost_report: {}, usage_atom_backfill: {},
    };
    applyJsDebugCurrentSnapshot(snapshot);
    const retained = jsDebugGraphBuckets.get('15000:1000');
    const initialApplyCount = applied.length;
    for (let second = 1; second <= 11; second += 1) {
      const previousTail = buckets.at(-1);
      const closedTail = {...previousTail, open: false};
      const newTail = {start: 20 + second, duration: 1, open: true};
      buckets = [...buckets.slice(1, -1), closedTail, newTail];
      snapshot = {...snapshot, window_start: second, window_end: 21 + second, cache_generation: second + 1, buckets};
      applyJsDebugCurrentDelta(snapshot, {
        buckets: [closedTail, newTail],
        tombstones: [{kind: 'bucket', start: second - 1, duration: 1}],
      });
    }
    result = {
      clearCalls,
      initialApplyCount,
      deltaApplies: applied.slice(initialApplyCount),
      retainedIdentity: jsDebugGraphBuckets.get('15000:1000') === retained,
      keys: [...jsDebugGraphBuckets.keys()],
    };
  `, context);
  assert.equal(context.result.clearCalls, 1, 'only the initial snapshot clears graph storage');
  assert.equal(context.result.initialApplyCount, 21);
  assert.equal(context.result.deltaApplies.length, 22, 'each delta applies its two changed records only');
  assert.equal(context.result.retainedIdentity, true, 'unchanged graph records remain in the established Map');
  assert.equal(context.result.keys.length, 21, 'tombstones and replacements keep the moving window bounded');
  assert.equal(context.result.keys.includes('0:1000'), false);
  assert.equal(context.result.keys.includes('31000:1000'), true);
});

test('current deltas replace the authoritative report without per-bucket cost state', () => {
  const deltaSource = source.slice(source.indexOf('function applyJsDebugCurrentDelta('), source.indexOf('\nasync function pollJsDebugStatsSample('));
  assert.match(deltaSource, /if \(delta\.cost_report\) jsDebugCurrentCostReport = delta\.cost_report/);
  assert.doesNotMatch(deltaSource, /cost_summary|rangeReport|rangeCost/);
  assert.doesNotMatch(source, /debugGraphCostSummaryForBuckets|debugGraphCostSummaryHtml|includeCostSummary/);
});

test('same-cursor requested-resolution switches paint and complete readiness in both directions', () => {
  const functionText = sourceFunction('jsDebugCurrentStatsGenerationKey', 'ensureJsDebugCurrentStatsClient');
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const paints = [];
    const jsDebugCurrentStatsClientState = {paintedGenerationKey: ''};
    const readiness = {phase: 'loading'};
    function jsDebugStatsPanelVisible() { return true; }
    function applyJsDebugCurrentSnapshot(snapshot) {
      paints.push(snapshot.requested_resolution);
      readiness.phase = 'ready';
    }
    ${functionText}
    const shared = {
      range_seconds: 300,
      resolution_seconds: 1,
      source_generation: 11,
      cache_generation: 12,
    };
    const autoPainted = paintJsDebugCurrentStatsGeneration({...shared, requested_resolution: 'AUTO'});
    readiness.phase = 'loading';
    const explicitPainted = paintJsDebugCurrentStatsGeneration({...shared, requested_resolution: 1});
    const explicitReadiness = readiness.phase;
    readiness.phase = 'loading';
    const duplicatePainted = paintJsDebugCurrentStatsGeneration({...shared, requested_resolution: 1});
    readiness.phase = 'loading';
    const returnedAutoPainted = paintJsDebugCurrentStatsGeneration({...shared, requested_resolution: 'AUTO'});
    const returnedAutoReadiness = readiness.phase;
    result = {
      autoPainted,
      explicitPainted,
      explicitReadiness,
      duplicatePainted,
      returnedAutoPainted,
      returnedAutoReadiness,
      paints,
    };
  `, context);
  assert.equal(context.result.autoPainted, true);
  assert.equal(context.result.explicitPainted, true);
  assert.equal(context.result.explicitReadiness, 'ready');
  assert.equal(context.result.duplicatePainted, false, 'an unchanged request identity stays deduplicated');
  assert.equal(context.result.returnedAutoPainted, true);
  assert.equal(context.result.returnedAutoReadiness, 'ready');
  assert.deepEqual([...context.result.paints], ['AUTO', 1, 'AUTO']);
});

test('current stream failures emit one provenance-bearing warning record per failed episode', () => {
  const functionText = sourceFunction('recordJsDebugCurrentStatsFailure', 'jsDebugStatsPanelVisible');
  const context = {result: null};
  vm.runInNewContext(`
    const diagnostics = [];
    const events = [];
    const jsDebugCurrentStatsClientState = {failureLatched: false};
    function recordJsDebugStatsDiagnostic(level, message, details = {}) { diagnostics.push({level, message, details}); }
    function recordJsDebugEvent(type, payload) { events.push({type, ...payload}); }
    function jsDebugFailureDetails(type, message, source) { return {message, source, signature: 'jsf-current-stats'}; }
    ${functionText}
    recordJsDebugCurrentStatsFailure({message: 'YO!stats stream generation stalled beyond its resolved cadence', source: '/api/stats-stream'});
    recordJsDebugCurrentStatsFailure({message: 'same failed episode', source: '/api/stats-stream'});
    acceptJsDebugCurrentStatsPushProof();
    recordJsDebugCurrentStatsFailure({message: 'later failed episode', source: '/api/stats-stream'});
    result = {diagnostics, events};
  `, context);
  assert.equal(context.result.diagnostics.length, 2);
  assert.equal(context.result.diagnostics[0].level, 'warning');
  assert.equal(context.result.diagnostics[0].details.category, 'stats_stream');
  assert.equal(context.result.diagnostics[0].details.route, '/api/stats-stream');
  assert.equal(context.result.diagnostics[0].details.eventType, 'stats-generation');
  assert.equal(context.result.diagnostics[0].details.deliveryOutcome, 'stalled');
  assert.equal(context.result.events.length, 0, 'the producer must not create a second article for one stall');
  assert.ok(context.result.diagnostics[0].message.length <= 160);
});

// A stall artifact that records only the message string cannot be classified after the fact.
// The transport samples `streamEvidence()` at report time; this proves that snapshot survives
// into the retained event the browser harness reads, instead of stopping at the panel.
test('a stall failure retains the stream evidence snapshot alongside its unchanged message', () => {
  const recorders = sourceFunction('recordJsDebugCurrentStatsFailure', 'jsDebugStatsPanelVisible');
  const diagnostic = sourceFunction('recordJsDebugStatsDiagnostic', 'debugClientLogRecord');
  const context = {result: null};
  vm.runInNewContext(`
    const events = [];
    const jsDebugCurrentStatsClientState = {failureLatched: false};
    function jsDebugFailureSource(value) { return String(value || '/'); }
    function jsDebugFailureSignature() { return 'jsf-test'; }
    function recordJsDebugEvent(type, payload) { events.push({id: events.length + 1, type, ...payload}); }
    ${diagnostic}
    ${recorders}
    recordJsDebugCurrentStatsFailure({
      message: 'YO!stats stream generation stalled for more than 3s',
      source: '/api/stats-stream',
      evidence: {
        running: true, visible: true, healthy: true, streamOpen: true, streamEpoch: 4,
        deliverySequence: 11, acceptedDeltaSequence: 9, lastDeliveryKind: 'ready',
        lastDeliveryAtMs: 1200, lastDeliveryEmitMs: 900, lastDeliveryEpoch: 4, rangeSeconds: 300,
        resolutionSeconds: 1, sourceGeneration: 77, cacheGeneration: 78, deltaRevision: 5,
      },
    });
    acceptJsDebugCurrentStatsPushProof();
    recordJsDebugCurrentStatsFailure({message: 'YO!stats stream unavailable', source: '/api/stats-stream'});
    result = {events};
  `, context);
  const [stall, plain] = context.result.events;
  assert.equal(stall.message, 'YO!stats stream generation stalled for more than 3s', 'the classification predicate string is byte-identical');
  assert.equal(stall.deliveryOutcome, 'stalled');
  assert.ok(stall.streamEvidence, 'the retained stall record carries the stream evidence snapshot');
  assert.equal(stall.streamEvidence.deliverySequence, 11);
  assert.equal(stall.streamEvidence.streamEpoch, 4);
  assert.equal(stall.streamEvidence.streamOpen, true);
  assert.equal(stall.streamEvidence.lastDeliveryKind, 'ready');
  assert.equal(stall.streamEvidence.lastDeliveryAtMs, 1200);
  assert.equal(stall.streamEvidence.lastDeliveryEmitMs, 900, 'the server emit clock rides into the retained record');
  assert.equal(Object.keys(stall.streamEvidence).length, 16, 'the whole snapshot is retained, not a subset');
  assert.equal(plain.streamEvidence, undefined, 'a failure with no evidence gains no empty placeholder field');
});

test('an unload retirement records a non-blocking info observation while a genuine failure stays release-blocking', () => {
  const recorders = sourceFunction('recordJsDebugCurrentStatsFailure', 'jsDebugStatsPanelVisible');
  const diagnostic = sourceFunction('recordJsDebugStatsDiagnostic', 'debugClientLogRecord');
  const graphGate = sourceFunction('recordJsDebugEventForGraph', 'jsDebugCurrentObservationEventSnapshot');
  const failureClassifier = coreSource.slice(
    coreSource.indexOf('function jsDebugFailureClassification('),
    coreSource.indexOf('\nfunction jsDebugFailureEvents('),
  );
  const context = {result: null};
  vm.runInNewContext(`
    const events = [];
    const queued = [];
    const jsDebugCurrentStatsClientState = {failureLatched: false};
    const jsDebugCurrentObservationState = {epoch: 'epoch-1'};
    function jsDebugFailureSource(value) { return String(value || '/'); }
    function jsDebugFailureSignature() { return 'jsf-test'; }
    function queueJsDebugCurrentObservation(key, event) { queued.push({key, level: event.level}); }
    function recordJsDebugEvent(type, payload) {
      const event = {id: events.length + 1, type, ...payload};
      events.push(event);
      recordJsDebugEventForGraph(event);
    }
    ${failureClassifier}
    ${graphGate}
    ${diagnostic}
    ${recorders}
    recordJsDebugCurrentStatsRetirement({reason: 'page_beforeunload', source: '/api/stats-stream'});
    recordJsDebugCurrentStatsFailure({message: 'YO!stats stream unavailable', source: '/api/stats-stream'});
    result = {
      events,
      queued,
      classifications: events.map(event => jsDebugFailureClassification(event).releaseBlocking),
    };
  `, context);
  const [retirement, failure] = context.result.events;
  assert.equal(retirement.level, 'info', 'the page tearing down its own stream is not a warning');
  assert.equal(retirement.deliveryOutcome, 'retired');
  assert.equal(retirement.reason, 'page_beforeunload', 'the expected close carries a machine-readable reason');
  assert.equal(retirement.route, '/api/stats-stream');
  assert.equal(retirement.eventType, 'stats-generation');
  assert.match(retirement.message, /^YO!stats: stream closed by page retirement \(page_beforeunload\)$/);
  assert.equal(failure.level, 'warning', 'a genuine mid-session stream failure is still a warning');
  assert.equal(failure.deliveryOutcome, 'failed');
  assert.deepEqual([...context.result.classifications], [false, true], 'only the genuine failure is release-blocking');
  assert.deepEqual([...context.result.queued].map(entry => entry.level), ['warning'], 'the retirement creates no durable receipt');
});

test('an exact range-resolution switch retains the rendered buckets behind one request owner', () => {
  const requestSource = sourceFunction('requestJsDebugHistoryForCurrentDomain', 'setDebugGraphRange');
  const applySource = sourceFunction('applyJsDebugCurrentSnapshot', 'scheduleJsDebugStatsHistoryFlush');
  const pollOwnerSource = sourceFunction('armJsDebugStatsPolling', 'pollJsDebugStatsOnInterval');
  const pollCompatibilitySource = sourceFunction('pollJsDebugStatsSample', 'scheduleJsDebugStatsHistoryFlush');
  assert.match(requestSource, /beginJsDebugHistoryReadiness[\s\S]*syncJsDebugCurrentStatsClient\(\{select: true\}\)/);
  assert.doesNotMatch(requestSource, /clearJsDebugGraphData/);
  assert.match(applySource, /clearJsDebugGraphData\(\)[\s\S]*debugGraphApplyServerRecord/);
  assert.match(pollOwnerSource, /if \(jsDebugGraphExactResolutionEnabled && syncJsDebugCurrentStatsClient\(\)\) return;/);
  assert.match(pollCompatibilitySource, /syncJsDebugCurrentStatsClient\(\{select: forceGraphRefresh\}\)/);
  assert.doesNotMatch(pollCompatibilitySource, /\/api\/stats-snapshot/);

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

test('the server cost report is rendered directly without browser aggregation', () => {
  assert.match(sourceFunction('debugGraphCostReportHtml', 'refreshDebugCostPricing'), /report\.total_micro_usd/);
  assert.doesNotMatch(source, /jsDebugCurrentCostSummary|debugGraphCostSummaryForBuckets|components/);
});

test('current cost rows retain typed unpriced coverage instead of rendering it as zero', () => {
  const context = {
    result: null,
    debugGraphCostInteger: value => Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : 0,
    debugGraphCostOptionalInteger: value => value === null || value === undefined ? null : Number(value),
    debugGraphCostText: (_key, fallback) => fallback,
    debugGraphCostUsdText: value => `$${(Number(value) / 1000000).toFixed(2)}`,
    debugGraphCostApiListMicroUsd: row => row?.api_list_micro_usd ?? null,
    debugGraphCostPricePairHtml: value => `<small>$${(Number(value) / 1000000).toFixed(2)}</small>`,
    debugGraphCostPricePairText: value => `$${(Number(value) / 1000000).toFixed(2)}`,
    debugGraphTokenNumberText: value => String(value),
    esc: value => String(value),
  };
  vm.runInNewContext(`
    function debugGraphCostDimensionRows(dimensions) { return Object.fromEntries(Object.entries(dimensions).flatMap(([key, value]) => [[key + '_tokens', value.tokens], [key + '_micro_usd', value.micro_usd], [key + '_api_list_micro_usd', value.api_list_micro_usd]])); }
    function debugGraphCostReportRow(row) { return {...row, token_quantity: row.total_tokens, micro_usd: row.total_micro_usd, api_list_micro_usd: row.total_api_list_micro_usd, priced_token_quantity: row.priced.tokens, unpriced_token_quantity: row.unpriced.tokens, ...debugGraphCostDimensionRows(row.dimensions)}; }
    ${sourceFunction('debugGraphCostUsageUsdText', 'debugGraphCostPricePairText')}
    ${sourceFunction('debugGraphCostUsageTableCellHtml', 'debugGraphCostExactTotalRow')}
    const row = debugGraphCostReportRow({
        provider: 'unknown', model: 'future-model', total_tokens: 10,
        total_micro_usd: 0, total_api_list_micro_usd: 0,
        dimensions: {output: {tokens: 10, micro_usd: 0, api_list_micro_usd: 0}},
        priced: {atoms: 0, tokens: 0}, unpriced: {atoms: 1, tokens: 10},
    });
    result = {
      row,
      html: debugGraphCostUsageTableCellHtml(row.token_quantity, row.micro_usd, {total: true, row}),
      pricedZeroHtml: debugGraphCostUsageTableCellHtml(10, 0, {total: true, row: {...row, priced_token_quantity: 10, unpriced_token_quantity: 0}}),
    };
  `, context);
  assert.equal(context.result.row.unpriced_token_quantity, 10);
  assert.match(context.result.html, /Unpriced/);
  assert.doesNotMatch(context.result.html, /\$0(?:\.00)?/);
  assert.match(context.result.pricedZeroHtml, /\$0\.00/);
  assert.doesNotMatch(context.result.pricedZeroHtml, /Unpriced/);
});

test('same-range resolution replacement is not mislabeled as older history', () => {
  const functionText = source.slice(
    source.indexOf('function beginJsDebugHistoryReadiness('),
    source.indexOf('\nfunction jsDebugHistoryRequestIsCurrent('),
  );
  const context = {
    result: null,
    debugRuntimeState: {graphRangeSeconds: 7200},
    jsDebugHistoryReadiness: {generation: 1, requestedRangeSeconds: 7200, loadedStartSeconds: 1000},
    performanceNow: () => 10,
    setJsDebugHistoryReadiness: (_phase, updates) => updates,
    recordJsDebugStatsDiagnostic: () => {},
  };
  vm.runInNewContext(`${functionText}\nresult = beginJsDebugHistoryReadiness(970, {requestedEndSeconds: 8170, requestedResolutionSeconds: 300});`, context);
  assert.equal(context.result.reason, 'initial');

  context.debugRuntimeState.graphRangeSeconds = 57600;
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
    var jsDebugGraphSelectionState = null;
    var jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderInteraction = null;
    var jsDebugGraphPendingResolutionChange = null;
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
    const graph = {};
    const panel = {contains: node => node === graph};
    const svg = {
      captures: 0,
      releases: 0,
      closest: selector => selector.includes('[data-js-debug-graph]') ? graph : null,
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
    handleDebugGraphPointerMove(pointer('touch', 100, 100, 200, cancelPrevented), panel);
    const cancelArmedSelection = jsDebugGraphSelectionState !== null;
    handleDebugGraphPointerMove(pointer('touch', 700, 102, 250, cancelPrevented), panel);
    handleDebugGraphPointerCancel({...pointer('touch', 0, 0, 251, cancelPrevented), pointerId: 2}, panel);
    const mismatchedCancelPreservedSelection = jsDebugGraphSelectionState !== null;
    handleDebugGraphPointerCancel(pointer('touch', 0, 0, 251, cancelPrevented), panel);
    const cancelCommitted = jsDebugGraphZoomDomain !== null;
    const cancelClearedSelection = jsDebugGraphSelectionState === null;
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
      cancelArmedSelection,
      mismatchedCancelPreservedSelection,
      cancelCommitted,
      cancelClearedSelection,
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
  assert.equal(context.result.cancelArmedSelection, true);
  assert.equal(context.result.mismatchedCancelPreservedSelection, true);
  assert.equal(context.result.cancelCommitted, false);
  assert.equal(context.result.cancelClearedSelection, true);
  assert.ok(context.result.cancelPrevented >= 1);
  assert.equal(context.result.verticalClaimed, false);
  assert.equal(context.result.nativeScrollPrevented, 0);
  assert.ok(context.result.refreshCount >= 1);
  assert.equal(context.result.holdBefore, 'wait');
  assert.equal(context.result.holdAt, 'arm');
  assert.ok(context.result.captures >= 2);
});

test('focused Reset Zoom immediately repaints the shared full-range domain and stays live', () => {
  const resetSource = [
    sourceFunction('debugGraphZoomDomainValid', 'clearDebugGraphZoom'),
    sourceFunction('clearDebugGraphZoom', 'debugEventCounts'),
    sourceFunction('debugGraphDomain', 'debugGraphBucketRate'),
    sourceFunction('refreshDebugGraphSurfaces', 'createDebugPanel'),
     sourceFunction('refreshDebugGraphElement', 'bindDebugGraphTouchSelection'),
    sourceFunction('handleDebugGraphControlEvent', 'bindDebugPanel'),
  ].join('\n');
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const nowMs = 3600000;
    const Date = {now: () => nowMs};
    const debugRuntimeState = {graphRangeSeconds: 900, graphResolutionOverrideSeconds: 60};
    const jsDebugGraphRetentionMs = 86400000;
    const jsDebugGraphExactResolutionEnabled = false;
    let jsDebugGraphZoomDomain = {startMs: 390000, endMs: 3417000};
     var jsDebugGraphSelectionState = {pointerType: 'touch'};
    let jsDebugGraphRangeSliderInteraction = null;
    let resetVisible = true;
    let paintedDomain = {startMs: 390000, endMs: 3417000, rangeSeconds: 3017, zoomed: true};
    let controlsState = {resetVisible: true, sliderDisabled: true, label: '05:39-06:30 · 3017s'};
    let bodyPaints = 0;
    let prevented = 0;
    const panel = {contains: () => true};
    const scrollOwner = {scrollTop: 0, scrollLeft: 0};
    const controls = {};
    const reset = {
      closest(selector) {
        if (selector === '[data-js-debug-zoom-reset]') return this;
        if (selector === '.js-debug-graph-controls') return controls;
        return null;
      },
    };
    const body = {replaceChildren() { bodyPaints += 1; paintedDomain = debugGraphDomain(nowMs); }};
    const graph = {
      className: '',
      dataset: {jsDebugGraphRenderedAt: '1'},
      contains(node) { return resetVisible && node === reset; },
      closest(selector) {
        if (selector === '.js-debug-panel') return panel;
        if (selector === '.js-debug-graph-view') return scrollOwner;
        return null;
      },
       querySelector(selector) { return selector === '[data-js-debug-graph-body]' ? body : null; },
       querySelectorAll() { return []; },
      setAttribute() {},
    };
    const document = {
      activeElement: reset,
      querySelectorAll(selector) { return selector === '[data-js-debug-graph]' ? [graph] : []; },
      createElement() { return {childNodes: [], set innerHTML(_value) {}}; },
    };
    function normalizedJsDebugGraphRange(value) { return Number(value); }
    function syncDebugGraphResolutionOverride() {}
    function syncJsDebugStatsDeliveryMode() {}
    function requestJsDebugHistoryForCurrentDomain() { return false; }
    function jsDebugStatsPanelVisible() { return true; }
    function jsDebugRequestedHistoryResolutionSeconds() { return 60; }
    function jsDebugHistoryCoverageResolutionSeconds() { return 60; }
    function jsDebugHistoryCoverageNeedsRefresh() { return false; }
    function renderYoCostPanels() { return false; }
    function debugGraphInteractionBelongsToPanel() { return false; }
    function debugGraphClassName() { return 'js-debug-graph'; }
    function debugGraphBodyHtml() { return ''; }
    function preserveDebugGraphBodyControls() {}
    function syncDebugGraphControls() {
      const domain = debugGraphDomain(nowMs);
      resetVisible = domain.zoomed;
      controlsState = {
        resetVisible,
        sliderDisabled: domain.zoomed,
        label: domain.zoomed ? 'zoomed' : '15m',
      };
    }
    function writeOwnedElementScroll(element, coordinates) { if (element) { element.scrollTop = coordinates.top; element.scrollLeft = coordinates.left; } }
    function bindDebugCostSummaryTabButtons() {}
    function clientPerfStart() { return null; }
    function clientPerfEnd() {}
    function jsDebugHistoryReadinessStateName() { return 'ready'; }
    function jsDebugHistoryReadinessBusy() { return false; }
    const jsDebugCurrentStatsClientState = {paintedGenerationKey: ''};
    const jsDebugHistoryReadiness = {phase: 'ready'};
    var jsDebugGraphSelectionState = null;
    var jsDebugGraphTouchCandidateState = null;
    var jsDebugGraphPendingResolutionChange = null;
    function debugGraphInteractionBelongsToPanel() { return false; }
    function commitJsDebugCurrentStatsPaint() {}
    function resolveDebugGraphResolutionChange() {}
    function syncDebugGraphLiveTicker() {}
    ${resetSource}
    const handled = handleDebugGraphControlEvent({
      type: 'click',
      target: reset,
      preventDefault() { prevented += 1; },
    }, panel);
    const afterReset = {
      handled,
      prevented,
      zoomed: jsDebugGraphZoomDomain !== null,
      selection: jsDebugGraphSelectionState,
      range: debugRuntimeState.graphRangeSeconds,
      resolution: debugRuntimeState.graphResolutionOverrideSeconds,
      bodyPaints,
      paintedDomain,
      controlsState,
      pending: graph.dataset.jsDebugGraphRefreshPending || '',
    };
    refreshDebugGraphElement(graph, {force: true});
    result = {afterReset, afterTick: {bodyPaints, paintedDomain, controlsState}};
  `, context);
  const result = JSON.parse(JSON.stringify(context.result));
  assert.deepEqual(result.afterReset, {
    handled: true,
    prevented: 1,
    zoomed: false,
    selection: null,
    range: 900,
    resolution: 60,
    bodyPaints: 1,
    paintedDomain: {startMs: 2700000, endMs: 3600000, rangeSeconds: 900, zoomed: false},
    controlsState: {resetVisible: false, sliderDisabled: false, label: '15m'},
    pending: '',
  });
  assert.equal(result.afterTick.bodyPaints, 2, 'the next live repaint stays on the selected full range');
  assert.deepEqual(result.afterTick.paintedDomain, {startMs: 2700000, endMs: 3600000, rangeSeconds: 900, zoomed: false});
  assert.deepEqual(result.afterTick.controlsState, {resetVisible: false, sliderDisabled: false, label: '15m'});
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
    const debugRuntimeState = {graphResolutionOverrideSeconds: 300, graphRangeSeconds: 7200};
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
    function refreshJsDebugSessionAliases() {}
    function ensureJsDebugCurrentStatsClient() { return client; }
    function loadJsDebugStatsUiPreferences() {}
    function jsDebugStatsPanelVisible() { return true; }
    function jsDebugStatsDocumentVisible() { return true; }
    function jsDebugCurrentStatsSelection() { return selection; }
    function paintJsDebugCurrentStatsGeneration() {}
    function recordJsDebugStatsDiagnostic() {}
    function recordJsDebugCurrentStatsFailure() {}
    function recordJsDebugCurrentStatsRetirement() {}
    function jsDebugErrorText(error) { return String(error); }
    ${functionText}
    result = {handled: syncJsDebugCurrentStatsClient({select: true}), calls, sameGeneration: controller.generation() === generation};
  `, context);
  assert.equal(context.result.handled, true);
  assert.equal(context.result.calls.select, 0);
  assert.equal(context.result.calls.start, 1);
  assert.equal(context.result.sameGeneration, true);
});

test('current stats stream selector exposes exact production client evidence without healthy defaults', () => {
  const functionText = sourceFunction('jsDebugCurrentStatsStreamEvidence', 'paintJsDebugCurrentStatsGeneration');
  const evidence = {running: true, streamOpen: true, deliverySequence: 4, acceptedDeltaSequence: 2};
    const context = {result: null, missing: null};
  vm.runInNewContext(`
    const globalThis = {YOmuxStatsCurrent: {createBrowserClient() {}}};
    const generation = {cache_generation: 8};
    const controller = {generation: () => generation};
    const client = {controller: () => controller, streamEvidence: () => (${JSON.stringify(evidence)})};
    let jsDebugCurrentStatsClientState = {client, paintedGenerationKey: '300:1:1:8:8'};
    function jsDebugStatsPanelVisible() { return false; }
    ${sourceFunction('debugGraphStatsCurrentModule', 'debugGraphAgentDisplayLabel')}
    ${functionText}
    result = jsDebugCurrentStatsStreamEvidence();
    jsDebugCurrentStatsClientState = {client: null, paintedGenerationKey: ''};
    missing = jsDebugCurrentStatsStreamEvidence();
  `, context);
  assert.deepEqual({...context.result.stream}, evidence);
  assert.equal(context.result.panelVisible, false);
  assert.equal(context.result.controllerReady, true);
  assert.equal(context.missing.clientReady, false);
  assert.equal(context.missing.controllerReady, false);
  assert.equal(context.missing.generationReady, false);
  assert.equal(context.missing.stream, null);
});

test('accepted current stats generations are not declared painted before a renderer commits', () => {
  const functionText = sourceFunction('paintJsDebugCurrentStatsGeneration', 'ensureJsDebugCurrentStatsClient');
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const snapshot = {
      range_seconds: 300,
      requested_resolution: 10,
      resolution_seconds: 10,
      source_generation: 12,
      cache_generation: 13,
    };
    const calls = [];
    const jsDebugCurrentStatsClientState = {
      paintedGenerationKey: '300:10:10:11:12',
      pendingGenerationKey: '',
    };
    let jsDebugGraphSelectionState = null;
    function jsDebugStatsPanelVisible() { return true; }
    function jsDebugCurrentStatsGenerationKey(value) {
      return [value.range_seconds, value.requested_resolution, value.resolution_seconds, value.source_generation, value.cache_generation].join(':');
    }
    function applyJsDebugCurrentSnapshot(value) { calls.push(value.cache_generation); }
    ${functionText}
    const first = paintJsDebugCurrentStatsGeneration(snapshot);
    const second = paintJsDebugCurrentStatsGeneration(snapshot);
    result = {first, second, calls, state: jsDebugCurrentStatsClientState};
  `, context);
  assert.equal(context.result.first, true);
  assert.equal(context.result.second, false, 'one pending generation has one apply owner');
  assert.deepEqual([...context.result.calls], [13]);
  assert.equal(context.result.state.paintedGenerationKey, '300:10:10:11:12');
  assert.equal(context.result.state.pendingGenerationKey, '300:10:10:12:13');
});

test('a focused stable graph control preserves focus while a newer generation commits', () => {
  const paintSource = [
    sourceFunction('commitJsDebugCurrentStatsPaint', 'paintJsDebugCurrentStatsGeneration'),
    sourceFunction('paintJsDebugCurrentStatsGeneration', 'ensureJsDebugCurrentStatsClient'),
  ].join('\n');
  const refreshSource = sourceFunction('refreshDebugGraphElement', 'bindDebugGraphTouchSelection');
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const toggle = {dataset: {jsDebugChartToggle: 'modelTokens'}};
    const panel = {};
    const scrollOwner = {scrollTop: 0, scrollLeft: 0};
    let bodyPaints = 0;
    let acceptedSnapshot = null;
    let renderResult = false;
    const body = {replaceChildren() { bodyPaints += 1; }};
    const graph = {
      className: '',
      dataset: {jsDebugGraphRenderedAt: '1', jsDebugStatsGenerationKey: '300:10:10:11:12'},
      closest(selector) {
        if (selector === '.js-debug-panel') return panel;
        if (selector === '.js-debug-graph-view') return scrollOwner;
        return null;
      },
       querySelector(selector) { return selector === '[data-js-debug-graph-body]' ? body : null; },
       querySelectorAll() { return []; },
      setAttribute() {},
    };
    const document = {
      activeElement: toggle,
      createElement() { return {childNodes: [], set innerHTML(_value) {}}; },
    };
    const jsDebugCurrentStatsClientState = {
      paintedGenerationKey: '300:10:10:11:12',
      pendingGenerationKey: '',
    };
    const jsDebugHistoryReadiness = {phase: 'ready'};
    let jsDebugGraphSelectionState = null;
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderInteraction = null;
    let jsDebugGraphPendingResolutionChange = null;
    function jsDebugStatsPanelVisible() { return true; }
    function jsDebugCurrentStatsGenerationKey(value) {
      return [value.range_seconds, value.requested_resolution, value.resolution_seconds, value.source_generation, value.cache_generation].join(':');
    }
    function applyJsDebugCurrentSnapshot(snapshot) {
      acceptedSnapshot = snapshot;
      renderResult = refreshDebugGraphElement(graph, {force: true});
    }
    function debugGraphInteractionBelongsToPanel() { return false; }
    function debugGraphClassName() { return 'js-debug-graph'; }
    function debugGraphBodyHtml() { return '<svg data-new-generation></svg>'; }
    function preserveDebugGraphBodyControls() {}
    function syncDebugGraphControls() {}
    function writeOwnedElementScroll(element, coordinates) { if (element) { element.scrollTop = coordinates.top; element.scrollLeft = coordinates.left; } }
    function bindDebugCostSummaryTabButtons() {}
    function clientPerfStart() { return null; }
    function clientPerfEnd() {}
    function jsDebugHistoryReadinessStateName() { return 'ready'; }
    function jsDebugHistoryReadinessBusy() { return false; }
    function resolveDebugGraphResolutionChange() {}
    function syncDebugGraphLiveTicker() {}
    ${paintSource}
    ${refreshSource}
    const snapshot = {
      range_seconds: 300,
      requested_resolution: 10,
      resolution_seconds: 10,
      source_generation: 12,
      cache_generation: 13,
    };
    const accepted = paintJsDebugCurrentStatsGeneration(snapshot);
    result = {
      accepted,
      acceptedCacheGeneration: acceptedSnapshot.cache_generation,
      renderResult,
      bodyPaints,
      focusPreserved: document.activeElement === toggle,
      paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
      pendingGenerationKey: jsDebugCurrentStatsClientState.pendingGenerationKey,
      graphGenerationKey: graph.dataset.jsDebugStatsGenerationKey,
      refreshPending: graph.dataset.jsDebugGraphRefreshPending || '',
    };
  `, context);
  assert.deepEqual({...context.result}, {
    accepted: true,
    acceptedCacheGeneration: 13,
    renderResult: true,
    bodyPaints: 1,
    focusPreserved: true,
    paintedGenerationKey: '300:10:10:12:13',
    pendingGenerationKey: '',
    graphGenerationKey: '300:10:10:12:13',
    refreshPending: '',
  });
  assert.doesNotMatch(source, /debugGraphFocusedControl|deferFocusedControl/);
  assert.doesNotMatch(debugRuntimeSource, /deferFocusedControl/);
});

test('hidden panels start the document-visible client and paint only when opened', () => {
  const start = source.indexOf('function jsDebugStatsPanelVisible()');
  const end = source.indexOf('\nfunction jsDebugStatsTokenConsumerEnabled()', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const functionText = source.slice(start, end);
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    let panelVisible = false;
    const document = {visibilityState: 'visible'};
    const debugModeEnabled = true;
    const debugPaneItemId = 'debug';
    const yocostItemId = 'cost';
    const debugRuntimeState = {graphRangeSeconds: 7200, graphResolutionOverrideSeconds: 300};
    function normalizedJsDebugGraphRange(value) { return value; }
    function normalizedDebugGraphResolutionOverrideSeconds(value) { return value; }
    function itemIsActivePaneTab() { return panelVisible; }
    const selection = {rangeSeconds: 7200, resolution: 300};
    let generation = {cache_generation: 12, source_generation: 12};
    const controller = {
      selection: () => ({range_seconds: 7200, resolution: 300}),
      generation: () => generation,
    };
    const calls = {select: 0, start: 0, visible: [], paints: []};
    const client = {
      controller: () => controller,
      setVisible(value) { calls.visible.push(value); },
      select() { calls.select += 1; },
      start() { calls.start += 1; return Promise.resolve(); },
    };
    const jsDebugCurrentStatsClientState = {
      client, selectionKey: '7200:300', startPromise: null,
      paintedGenerationKey: '',
    };
    function applyJsDebugCurrentSnapshot(value) { calls.paints.push(value.cache_generation); }
    function recordJsDebugStatsDiagnostic() {}
    function recordJsDebugCurrentStatsFailure() {}
    function recordJsDebugCurrentStatsRetirement() {}
    function jsDebugErrorText(error) { return String(error); }
    function armJsDebugStatsPolling() {}
    function loadJsDebugStatsUiPreferences() {}
    ${functionText}
    const hiddenHandled = syncJsDebugCurrentStatsClient();
    generation = {cache_generation: 13, source_generation: 13};
    panelVisible = true;
    const openedHandled = syncJsDebugCurrentStatsClient();
    result = {hiddenHandled, openedHandled, calls};
  `, context);
  assert.equal(context.result.hiddenHandled, true);
  assert.equal(context.result.openedHandled, true);
  assert.deepEqual([...context.result.calls.visible], [true, true], 'panel state does not pause document-visible transport');
  assert.equal(context.result.calls.start, 1, 'normal hidden boot starts the one exact client');
  assert.equal(context.result.calls.select, 0, 'opening cached selection does not request a snapshot or repair');
  assert.deepEqual([...context.result.calls.paints], [13], 'hidden generations advance in memory and the newest paints only when the panel opens');
});

test('hidden boot loads saved exact selection before constructing the one current client', () => {
  const start = source.indexOf('function jsDebugStatsPanelVisible()');
  const end = source.indexOf('\nfunction jsDebugStatsTokenConsumerEnabled()', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const functionText = source.slice(start, end);
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const document = {visibilityState: 'visible'};
    const debugModeEnabled = true;
    const debugPaneItemId = 'debug';
    const yocostItemId = 'cost';
    const debugRuntimeState = {graphRangeSeconds: 900, graphResolutionOverrideSeconds: 0};
    let loaded = false;
    let constructed = null;
    function loadJsDebugStatsUiPreferences() {
      loaded = true;
      debugRuntimeState.graphRangeSeconds = 3600;
      debugRuntimeState.graphResolutionOverrideSeconds = 60;
    }
    function normalizedJsDebugGraphRange(value) { return value; }
    function normalizedDebugGraphResolutionOverrideSeconds(value) { return value; }
    function itemIsActivePaneTab() { return false; }
    function jsDebugStatsClientIdForRequest() { return 'saved-selection'; }
    function apiFetch() {}
    function claimTerminalAuthentication() {}
    function recordJsDebugCurrentStatsFailure() {}
    function recordJsDebugCurrentStatsRetirement() {}
    function acceptJsDebugCurrentStatsPushProof() {}
    function applyJsDebugCurrentSnapshot() {}
    function recordJsDebugStatsDiagnostic() {}
    function jsDebugErrorText(error) { return String(error); }
    function armJsDebugStatsPolling() {}
    const controller = null;
    const client = {
      controller: () => controller,
      setVisible() {},
      select() {},
      start: () => Promise.resolve(controller),
    };
    const YOmuxStatsCurrent = {
      createBrowserClient(options) { constructed = {loaded, ...options}; return client; },
    };
    globalThis.YOmuxStatsCurrent = YOmuxStatsCurrent;
    const jsDebugCurrentStatsClientState = {
      client: null, selectionKey: '', startPromise: null, failureLatched: false,
      paintedGenerationKey: '',
    };
    ${sourceFunction('debugGraphStatsCurrentModule', 'debugGraphAgentDisplayLabel')}
    ${functionText}
    const handled = syncJsDebugCurrentStatsClient();
    result = {handled, constructed, selectionKey: jsDebugCurrentStatsClientState.selectionKey};
  `, context);
  assert.equal(context.result.handled, true);
  assert.equal(context.result.constructed.loaded, true);
  assert.equal(context.result.constructed.savedRange, 3600);
  assert.equal(context.result.constructed.savedResolution, 60);
  assert.equal(context.result.selectionKey, '3600:60');
});

testAsync('synchronous current-client construction failure cannot reject terminal boot', async () => {
  const syncSource = sourceFunction('syncJsDebugCurrentStatsClient', 'jsDebugStatsTokenConsumerEnabled');
  const initializeStart = source.indexOf('async function initializeJsDebugStatsBeforeStreams(');
  const initializeEnd = source.indexOf('\nfunction jsDebugTextForClipboard(', initializeStart);
  assert.notEqual(initializeStart, -1);
  assert.notEqual(initializeEnd, -1);
  const initializeSource = source.slice(initializeStart, initializeEnd);
  const context = {result: null, sessions: [], tabberViewSessions: new Map(), transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, ensureJsDebugAgentHistoryRefill() {}, jsDebugAgentHistoryRefill: null};
  vm.runInNewContext(`
    const failures = [];
    const jsDebugGraphExactResolutionEnabled = true;
    const jsDebugCurrentStatsClientState = {client: null, startPromise: null, failureLatched: false};
    function ensureJsDebugCurrentStatsClient() { throw new Error('EventSource constructor unavailable'); }
    function recordJsDebugCurrentStatsFailure(failure) { failures.push(failure); }
    function recordJsDebugCurrentStatsRetirement() {}
    function loadJsDebugStatsUiPreferences() {}
    function jsDebugErrorText(error) { return String(error?.message || error); }
    async function primeJsDebugStatsBeforeLongLivedStreams() { return false; }
    function syncJsDebugStatsPolling() {}
    const jsDebugStatsPollState = {firstSampleReceived: false};
    ${syncSource}
    ${initializeSource}
    result = initializeJsDebugStatsBeforeStreams().then(value => ({value, failures}));
  `, context);
  const resolved = await context.result;
  assert.equal(resolved.value, false);
  assert.equal(resolved.failures.length, 1);
  assert.equal(resolved.failures[0].source, '/api/stats-stream');
});

testAsync('a rejected current-client start retains the exact owner without starting legacy polling', async () => {
  const functionText = sourceFunction('syncJsDebugCurrentStatsClient', 'jsDebugStatsTokenConsumerEnabled');
  const run = async error => {
    const calls = {start: 0, fallback: []};
    const client = {
      controller: () => null,
      setVisible() {},
      select() {},
      start() {
        calls.start += 1;
        return Promise.reject(error);
      },
    };
    const context = {
    ensureJsDebugAgentHistoryRefill() {},
    jsDebugAgentHistoryRefill: null,
      client,
      calls,
      error,
      result: null,
    };
    vm.runInNewContext(`
      const selection = {rangeSeconds: 300, resolution: 1};
      const jsDebugCurrentStatsClientState = {client, selectionKey: '300:1', startPromise: null, failureLatched: false};
      function refreshJsDebugSessionAliases() {}
    function ensureJsDebugCurrentStatsClient() { return client; }
      function loadJsDebugStatsUiPreferences() {}
      function jsDebugStatsPanelVisible() { return true; }
      function jsDebugStatsDocumentVisible() { return true; }
      function jsDebugCurrentStatsSelection() { return selection; }
      function paintJsDebugCurrentStatsGeneration() {}
      function recordJsDebugStatsDiagnostic() {}
      function recordJsDebugCurrentStatsFailure() {}
      function recordJsDebugCurrentStatsRetirement() {}
      function jsDebugErrorText(value) { return String(value); }
      function armJsDebugStatsPolling(options) { calls.fallback.push(options); }
      ${functionText}
      result = {firstHandled: syncJsDebugCurrentStatsClient(), state: jsDebugCurrentStatsClientState};
    `, context);
    await Promise.resolve();
    await Promise.resolve();
    return {
      firstHandled: context.result.firstHandled,
      secondHandled: vm.runInNewContext('syncJsDebugCurrentStatsClient()', context),
      calls,
    };
  };

  const transport = await run(new Error('stats transport unavailable'));
  assert.equal(transport.firstHandled, true, 'the exact start owns its pending attempt');
  assert.equal(transport.secondHandled, true, 'a failed exact start never releases a second request owner');
  assert.equal(transport.calls.start, 1, 'the pending exact start remains the one initialization owner');
  assert.equal(JSON.stringify(transport.calls.fallback), '[]');

  const contractError = new Error('stats capabilities fields are not exact');
  contractError.statsContractViolation = true;
  const contract = await run(contractError);
  assert.equal(contract.firstHandled, true);
  assert.equal(JSON.stringify(contract.calls.fallback), '[]');
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
    const debugRuntimeState = {graphRangeSeconds: 300};
    const jsDebugGraphSlideMaxRangeSeconds = 3600;
    let queryCount = 0;
    const timers = [];
    const document = {visibilityState: 'visible', querySelectorAll() { queryCount += 1; return [{offsetParent: {}, dataset: {jsDebugGraphRenderedAt: '0'}}]; }};
    function jsDebugCostSubviewVisible() { return false; }
    function jsDebugStatsPanelVisible() { return true; }
    function debugGraphZoomDomainValid() { return false; }
    function debugGraphDomain(now) { return {startMs: now - 300000, endMs: now}; }
    function debugGraphDisplayResolutionMs() { return 1000; }
    function refreshDebugGraphElement() {}
    function debugCostAgeRefreshDelayMs() { return 3000; }
    function setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; }
    function clearTimeout() {}
    function debugGraphLifecycleScope() { return {current() { return true; }, ownTimer() {}, release() { return false; }, relinquish() { return true; }}; }
    const Date = {now: () => nowMs};
    ${tickerSource}
    syncDebugGraphLiveTicker();
    const firstDelay = timers[0].delay;
    stopDebugGraphLiveTicker();
    syncDebugGraphLiveTicker();
    const replacementTimer = jsDebugGraphLiveTimer;
    nowMs = 1000;
    timers[0].callback();
    const afterStale = {queryCount, liveTimer: jsDebugGraphLiveTimer};
    timers[1].callback();
    result = {firstDelay, timerCount: timers.length, nextDelay: timers[2].delay, queryCount, liveTimer: jsDebugGraphLiveTimer, replacementTimer, afterStale};
  `, context);
  assert.equal(context.result.firstDelay, 1000, 'a 1s live chart sleeps directly to its next slide boundary');
  assert.deepEqual({...context.result.afterStale}, {queryCount: 0, liveTimer: context.result.replacementTimer}, 'a retired ticker callback cannot clear or run over its replacement');
  assert.equal(context.result.timerCount, 3, 'one current timer fire schedules exactly one later wake instead of a frame loop');
  assert.equal(context.result.nextDelay, 1000, 'the next wake remains one slide interval away');
  assert.equal(context.result.queryCount, 1, 'the ticker queries live graphs only at its due fire');
  assert.equal(context.result.liveTimer, 3, 'one pending timeout remains after the due work completes');
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
    const jsDebugStatsPollState = {lastSampleAtMs: 1};
    const jsDebugPricingRefreshState = {lastRequestedAtMs: 0};
    function jsDebugCostSubviewVisible() { return true; }
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
    let jsDebugGraphRangeSliderInteraction = null;
    let replacements = 0;
    const graph = {
      dataset: {},
      closest: () => panel,
      querySelector() { replacements += 1; return null; },
    };
    ${interactionSource}
    ${refreshSource}
    const rendered = refreshDebugGraphElement(graph, {force: true});
    result = {rendered, replacements, pending: graph.dataset.jsDebugGraphRefreshPending};
  `, statsContext);
  assert.deepEqual({...statsContext.result}, {rendered: false, replacements: 0, pending: 'true'});

  const zoomContext = {result: null};
  vm.runInNewContext(`
    const graph = {dataset: {jsDebugGraphRefreshPending: 'true'}};
    const panel = {
      dataset: {},
      matches: () => false,
      querySelectorAll: selector => selector === '[data-js-debug-graph]' ? [graph] : [],
    };
    let jsDebugGraphSelectionState = {
      panel,
      pointerId: null,
      pointerType: 'mouse',
      startRatio: 0.1,
      currentRatio: 0.7,
      resolutionMs: 10000,
      domain: {startMs: 0, endMs: 600000},
    };
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderInteraction = null;
    let jsDebugGraphZoomDomain = null;
    let jsDebugGraphLastPointerType = 'mouse';
    const jsDebugGraphZoomMinBuckets = 3;
    const jsDebugGraphZoomMinRatio = 0.04;
    let forcedRepaints = 0;
    const document = {querySelectorAll: () => []};
    function flushDeferredDebugGraphRefresh(target) {
      if (target.dataset.jsDebugGraphRefreshPending !== 'true') return false;
      delete target.dataset.jsDebugGraphRefreshPending;
      forcedRepaints += 1;
      return true;
    }
    function renderYoCostPanels() { return false; }
    function debugGraphSelectionRatioForEvent() { return 0.7; }
    function debugGraphClearSelectionRects() {}
    function debugGraphSetInteractionLines() {}
    function syncDebugGraphResolutionOverride() {}
    function syncJsDebugStatsDeliveryMode() {}
    function refreshDebugGraphSurfaces() {}
    function requestJsDebugHistoryForCurrentDomain() {}
    function syncDebugGraphControls() {}
    ${interactionSource}
    ${sourceFunction('flushDeferredDebugGraphInteractionRefresh', 'clearDebugGraphTouchCandidate')}
    ${sourceFunction('handleDebugGraphPointerUp', 'handleDebugGraphPointerCancel')}
    handleDebugGraphPointerUp({pointerId: 1}, panel);
    result = {
      interactionLive: jsDebugGraphSelectionState !== null,
      pending: graph.dataset.jsDebugGraphRefreshPending || '',
      forcedRepaints,
      zoomed: jsDebugGraphZoomDomain !== null,
    };
  `, zoomContext);
  assert.deepEqual({...zoomContext.result}, {
    interactionLive: false,
    pending: '',
    forcedRepaints: 1,
    zoomed: true,
  });

  const sliderContext = {result: null};
  vm.runInNewContext(`
    const graph = {dataset: {jsDebugGraphRefreshPending: 'true'}};
    const panel = {
      dataset: {},
      contains: () => true,
      matches: () => false,
      querySelectorAll: selector => selector === '[data-js-debug-graph]' ? [graph] : [],
    };
    const slider = {
      closest: selector => selector === '[data-js-debug-range-slider]' ? slider : null,
    };
    let jsDebugGraphRangeSliderInteraction = {panel, slider, pointerId: 1};
    let forcedRepaints = 0;
    function flushDeferredDebugGraphRefresh(target) {
      if (target.dataset.jsDebugGraphRefreshPending !== 'true') return false;
      delete target.dataset.jsDebugGraphRefreshPending;
      forcedRepaints += 1;
      return true;
    }
    function renderYoCostPanels() { return false; }
    ${sourceFunction('flushDeferredDebugGraphInteractionRefresh', 'clearDebugGraphTouchCandidate')}
    const debugRuntimeState = {graphRangeSeconds: 300};
    function jsDebugGraphRangeOptionIndex() { return 0; }
    function updateDebugGraphRangeSliderLabel() {}
    function debugGraphAvailableRangeOptions() { return []; }
    function syncDebugGraphControls() {}
    ${sourceFunction('completeDebugGraphRangeSliderInteraction', 'debugGraphPointerRatioFromRect')}
    ${sourceFunction('handleDebugGraphControlEvent', 'bindDebugPanel')}
    handleDebugGraphControlEvent({type: 'pointercancel', target: slider, pointerId: 1}, panel);
    result = {
      dragging: jsDebugGraphRangeSliderInteraction !== null,
      pending: graph.dataset.jsDebugGraphRefreshPending || '',
      forcedRepaints,
    };
  `, sliderContext);
  assert.deepEqual({...sliderContext.result}, {
    dragging: false,
    pending: '',
    forcedRepaints: 1,
  });

  const candidateExitContext = {result: null};
  vm.runInNewContext(`
    const graph = {dataset: {jsDebugGraphRefreshPending: 'true'}};
    const panel = {
      dataset: {},
      matches: () => false,
      querySelectorAll: selector => selector === '[data-js-debug-graph]' ? [graph] : [],
    };
    let jsDebugGraphSelectionState = null;
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderInteraction = null;
    let jsDebugGraphLastPointerType = 'touch';
    let forcedRepaints = 0;
    function candidate() {
      return {panel, pointerId: 1, rect: {}, armTimer: null, startedAtMs: 0};
    }
    function flushDeferredDebugGraphRefresh(target) {
      if (target.dataset.jsDebugGraphRefreshPending !== 'true') return false;
      delete target.dataset.jsDebugGraphRefreshPending;
      forcedRepaints += 1;
      return true;
    }
    function renderYoCostPanels() { return false; }
    function clearTimeout() {}
    function debugGraphPointerRatioFromRect() { return 0.5; }
    function debugGraphTouchCandidateDecision() { return 'scroll'; }
    function debugGraphClearInteractionLines() {}
    function debugGraphSetInteractionLines() {}
    function debugGraphSetHoverTooltip() {}
    function debugGraphSetSelectionRects() {}
    function debugGraphSelectionRatioForEvent() { return null; }
    function startDebugGraphSelection() {}
    ${sourceFunction('flushDeferredDebugGraphInteractionRefresh', 'clearDebugGraphTouchCandidate')}
    ${sourceFunction('clearDebugGraphTouchCandidate', 'debugGraphTouchCandidateDecision')}
    ${sourceFunction('handleDebugGraphPointerMove', 'handleDebugGraphPointerUp')}
    ${sourceFunction('cancelDebugGraphSelection', 'handleDebugGraphControlEvent')}
    jsDebugGraphTouchCandidateState = candidate();
    handleDebugGraphPointerMove({pointerId: 1, clientX: 1, clientY: 1, timeStamp: 1}, panel);
    const scrollExit = {
      candidateLive: jsDebugGraphTouchCandidateState !== null,
      pending: graph.dataset.jsDebugGraphRefreshPending || '',
      forcedRepaints,
    };
    graph.dataset.jsDebugGraphRefreshPending = 'true';
    jsDebugGraphTouchCandidateState = candidate();
    cancelDebugGraphSelection(panel);
    const cancelExit = {
      candidateLive: jsDebugGraphTouchCandidateState !== null,
      pending: graph.dataset.jsDebugGraphRefreshPending || '',
      forcedRepaints,
    };
    result = {scrollExit, cancelExit};
  `, candidateExitContext);
  assert.deepEqual({...candidateExitContext.result.scrollExit}, {
    candidateLive: false,
    pending: '',
    forcedRepaints: 1,
  });
  assert.deepEqual({...candidateExitContext.result.cancelExit}, {
    candidateLive: false,
    pending: '',
    forcedRepaints: 2,
  });

  const costContext = {result: null};
  vm.runInNewContext(`
    const panel = {dataset: {}, querySelector() { throw new Error('active cost DOM was replaced'); }};
    let jsDebugGraphSelectionState = {panel};
    let jsDebugGraphTouchCandidateState = null;
    let jsDebugGraphRangeSliderInteraction = null;
    const dragState = {item: null};
    let jsDebugRenderForce = false;
    let jsDebugRenderDragDeferred = false;
    let jsDebugCostPanelNextRefreshAtMs = 0;
    const document = {visibilityState: 'visible', querySelectorAll: () => [panel]};
    function jsDebugCostSubviewVisible() { return true; }
    ${interactionSource}
    ${sourceFunction('renderYoCostPanel', 'renderYoCostPanels')}
    ${sourceFunction('renderYoCostPanels', 'refreshDebugGraphSurfaces')}
    result = {
      rendered: renderYoCostPanels({force: true}),
      pending: panel.dataset.jsDebugGraphRefreshPending,
    };
  `, costContext);
  assert.deepEqual({...costContext.result}, {rendered: false, pending: 'true'});
  assert.match(sourceFunction('handleDebugGraphPointerUp', 'handleDebugGraphPointerCancel'), /flushDeferredDebugGraphInteractionRefresh/);
  assert.match(sourceFunction('handleDebugGraphPointerCancel', 'cancelDebugGraphSelection'), /cancelDebugGraphSelection\(panel\)/);
  const panelBinding = sourceFunction('bindDebugPanel', 'registerDebugRuntimeFacade');
  assert.equal((panelBinding.match(/scope\.ownEvent\('pointerdown'/g) || []).length, 1);
  assert.match(panelBinding, /bindDebugGraphTouchSelection\(panel\)/);
  assert.match(panelBinding, /lostpointercapture/);
  assert.doesNotMatch(source, /function bindYoCostPanel\(/);
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
  assert.match(renderText, /reconcilePanelBody\(\{[\s\S]*capture: root => debugLogScrollAnchor\([\s\S]*restore: \(root, value\) => restoreDebugLogScrollAnchor\(/);
  const reconcileText = slice(coreSource, 'function reconcilePanelBody(', '\nfunction elementScrollAnchor(');
  assert.ok(reconcileText.indexOf('anchor.capture(body)') < reconcileText.indexOf('body.innerHTML = html'));
  assert.ok(reconcileText.indexOf('body.innerHTML = html') < reconcileText.indexOf('anchor.restore?.(body, value)'));
  const refreshText = sourceFunction('refreshDebugPanelFromEvents', 'syncDebugGraphControls');
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
  };
  vm.runInNewContext(`${functionText}\nresult = jsDebugCurrentBucketRecord({
    start: 100,
    duration: 10,
    series: {
      'gpu_memory_bytes:gpu:0': {value: 1234567890},
      'gpu_util_percent:gpu:0': {value: 8},
      'gpu_memory_bytes:gpu:nvidia:0': {value: 2097152},
      'gpu_util_percent:gpu:nvidia:0': {value: 12.5},
    },
  });`, context);
  const device = context.result.host_metrics.gpu_devices['gpu:0'];
  assert.equal(device.util_total_percent, 8);
  assert.equal(device.memory_used_total_bytes, 1234567890);
  const nvidiaDevice = context.result.host_metrics.gpu_devices['gpu:nvidia:0'];
  assert.equal(nvidiaDevice.util_total_percent, 12.5);
  assert.equal(nvidiaDevice.memory_used_total_bytes, 2097152);
});

test('the current snapshot adapter maps binary RSS series into memory processes', () => {
  const projectionText = sourceFunction('jsDebugCurrentCpuProjectionValue', 'jsDebugCurrentServiceLoadItem');
  const functionText = source.slice(
    source.indexOf('function jsDebugCurrentBucketRecord('),
    source.indexOf('\nfunction jsDebugCurrentBucketHasFamilyData('),
  );
  const context = {
    result: null,
    jsDebugCurrentSeriesValue: (series, name) => Number(series[name]?.value),
    jsDebugCurrentModelComponent: () => ({}),
  };
  vm.runInNewContext(`${projectionText}\n${functionText}\nresult = jsDebugCurrentBucketRecord({
    start: 100,
    duration: 60,
    series: {
      'process_cpu_percent:python': {value: 12},
      'process_cpu_max_percent:python': {value: 47},
      system_memory_used_bytes: {value: 900},
      'process_memory_bytes:python': {value: 300},
      'process_memory_bytes:node': {value: 200},
    },
  });`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.host_metrics.memory_processes)), {
    python: {label: 'python', total_bytes: 300, samples: 1},
    node: {label: 'node', total_bytes: 200, samples: 1},
  });
  assert.deepEqual(JSON.parse(JSON.stringify(context.result.host_metrics.cpu_processes)), {
    python: {label: 'python', total_percent: 12, samples: 1},
  });
});

test('CPU projection keeps the average at coarse resolutions instead of promoting peaks', () => {
  const projectionSource = sourceFunction('jsDebugCurrentCpuProjectionValue', 'jsDebugCurrentBucketRecord');
  const context = {
    Number,
    result: null,
    jsDebugCurrentSeriesValue: (series, name) => series[name] === undefined ? null : Number(series[name]),
  };
  vm.runInNewContext(`${projectionSource}
    result = [
      jsDebugCurrentCpuProjectionValue({average: 42, maximum: 100}, 'average'),
      jsDebugCurrentCpuProjectionValue({average: 42, maximum: 100}, 'average'),
      jsDebugCurrentCpuProjectionValue({average: 42, maximum: 100}, 'average'),
    ];`, context);
  assert.deepEqual([...context.result], [42, 42, 42]);
  assert.match(projectionSource, /return average/);
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
  assert.equal(context.result.cost_report, undefined);
  vm.runInNewContext(`result = jsDebugCurrentBucketRecord({
    start: 110,
    duration: 10,
    series: {usage_tokens: {value: 300}},
  });`, context);
  assert.equal(context.result.cost_report, undefined);
});

test('health observations retain measured latency and bytes as original browser facts', () => {
  const functionText = source.slice(
    source.indexOf('function jsDebugCurrentObservationFromEvent('),
    source.indexOf('\nfunction scheduleJsDebugCurrentObservationFlush('),
  );
  const context = {
    result: null,
    jsDebugCurrentObservationState: {epoch: 'epoch-1', instrumentationCostMs: 0},
    jsDebugStatsClientIdForRequest: () => 'client-1',
    performanceNow: () => 0,
    reloadClientJourneyId: 'j-reload-test',
    bootstrap: {clientRevision: 'test-revision'},
    navigator: {userAgent: 'Mozilla/5.0 Chrome/140.0'},
    jsDebugCodeRevision: () => 'test-revision',
    jsDebugBrowserFamily: () => 'chromium',
    jsDebugBoundedToken: value => String(value || ''),
    jsDebugFailureClassification: () => ({releaseBlocking: false, kind: '', observationKind: 'heartbeat'}),
  };
  vm.runInNewContext(`${functionText}\nresult = jsDebugCurrentObservationFromEvent({
    key: 'epoch-1:health:1',
    event: {type: 'heartbeat', ts: '2026-07-16T17:00:00.000Z', durationMs: 12.5, bytes: 456},
  });`, context);
  assert.equal(context.result.payload.kind, 'heartbeat');
  assert.equal(context.result.payload.latency_ms, 12.5);
  assert.equal(context.result.payload.bytes, 456);
});

test('the graph has no separate cost summary card', () => {
  assert.doesNotMatch(source, /debugGraphCostSummaryHtml|data-js-debug-summary-group="costSummary"|includeCostSummary/);
  assert.doesNotMatch(source, /jsDebugCurrentCostSummary|components/);
});

test('the YO!cost report renders one shared always-visible column legend with translated terse glosses', () => {
  const copyStart = source.indexOf('const debugGraphCostUsageColumnCopy = Object.freeze(');
  const copyEnd = source.indexOf('\nfunction debugGraphCostUsageColumnHeaderAttrs(', copyStart);
  const report = sourceFunction('debugGraphCostReportHtml', 'refreshDebugCostPricing');
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

test('YO!stats owns Cost between Graphs and API/SSE with persistence, activation, and every locale label', () => {
  const tabs = sourceFunction('debugSubTabsHtml', 'debugSubViewAttrs');
  assert.match(tabs, /debugSubTabButtonHtml\('graph',[\s\S]*debugSubTabButtonHtml\('cost',[\s\S]*debugSubTabButtonHtml\('events',[\s\S]*debugSubTabButtonHtml\('system',[\s\S]*debugSubTabButtonHtml\('logs'/);
  assert.match(debugRuntimeSource, /function normalizedJsDebugSubTab\(value\) \{[\s\S]*value === 'cost'/);
  assert.match(debugRuntimeSource, /legacyYoCostMigrationRequested \? 'cost' : normalizedJsDebugSubTab\(saved\.subTab\)/);
  assert.match(debugRuntimeSource, /if \(legacyYoCostMigrationRequested\) saveJsDebugStatsUiPreferences\(\)/);
  assert.match(debugRuntimeSource, /subTab: debugRuntimeState\.subTab/);
  assert.match(sourceFunction('debugPanelSubviewDescriptors', 'syncDebugSubviewActivation'), /return DEBUG_SUBVIEWS;/);
  assert.match(sourceFunction('scheduleDebugCostPricingStatusRefresh', 'disposeDebugPricingRefreshLifecycle'), /if \(!jsDebugCostSubviewVisible\(\)\) return false;/);
  assert.doesNotMatch(source, /key: 'yocost'/);
  assert.doesNotMatch(source, /function createYoCostPanel\(/);

  const sourceLocales = fs.readdirSync('static_src/locales').filter(name => name.endsWith('.json'));
  assert.equal(sourceLocales.length, 19);
  for (const name of sourceLocales) {
    const catalog = JSON.parse(fs.readFileSync(`static_src/locales/${name}`, 'utf8'));
    assert.ok(String(catalog['debug.tab.cost'] || '').trim(), `${name} has debug.tab.cost`);
  }
});

test('Graphs and Cost give the same retained token range separate SVG pattern identities', () => {
  const patternSource = source.slice(
    source.indexOf('function debugGraphAgentTokenPatternIndex('),
    source.indexOf('\nfunction debugGraphMacMemoryPressureColor('),
  );
  const context = {
    result: null,
    Number,
    Math,
    jsDebugGraphAgentTokenPatternCount: 6,
    jsDebugGraphAgentTokenPatternShapes: [''],
    esc: value => String(value),
    debugGraphSeriesStyleAttr: series => ` style="--js-debug-series-color: ${series.color};"`,
  };
  vm.runInNewContext(`
    ${patternSource}
    const retainedRange = Object.freeze({rangeSeconds: 3600, resolutionSeconds: 60});
    const retainedSeries = Object.freeze({
      key: 'agentToken:yo7771-b',
      agentTokenKey: 'yo7771-b',
      tokenPatternSeries: true,
      agentTokenPatternIndex: 0,
      color: '#38bdf8',
      values: [1200, 2400, 1800],
      times: [1000, 61000, 121000],
      durations: [60000, 60000, 60000],
    });
    const surface = patternScope => {
      const series = {...retainedSeries, agentTokenPatternScope: patternScope};
      const id = debugGraphAgentTokenPatternId(series);
      return {
        id,
        definition: debugGraphAgentTokenPatternDefinitionHtml(series),
        reference: 'url(#' + id + ')',
      };
    };
    result = {retainedRange, graphs: surface('graphs-agentTokens'), cost: surface('cost-agentTokens')};
  `, context);
  assert.deepEqual({...context.result.retainedRange}, {rangeSeconds: 3600, resolutionSeconds: 60});
  assert.notEqual(context.result.graphs.id, context.result.cost.id, 'hidden Graphs defs cannot own Cost bar fills');
  assert.match(context.result.graphs.definition, new RegExp(`id="${context.result.graphs.id}"`));
  assert.match(context.result.cost.definition, new RegExp(`id="${context.result.cost.id}"`));

  const costPanel = sourceFunction('yoCostPanelHtml', 'openYoCostTranscriptPreview');
  assert.match(costPanel, /debugGraphSvgHtml\(buckets, debugGraphSeriesData\(buckets\), tokenGroups, nowMs, \{patternScope: 'cost'\}\)/);
  const graphRenderer = sourceFunction('debugGraphSvgHtml', 'debugGraphClassName');
  assert.match(graphRenderer, /patternScope = 'graphs'/);
  assert.match(graphRenderer, /patternScope: `\$\{patternScope\}-\$\{group\.key\}`/);
});

test('one chart render shares token aggregation and skips hidden chart work', () => {
  let tokenCalls = 0;
  let hiddenCalls = 0;
  let seriesCalls = 0;
  let value = 1;
  const charts = [];
  const groups = [{key: 'hidden', bucketSeconds: 10}, {key: 'agentTokens'}, {key: 'modelTokens'}];
  const context = {
    jsDebugGraphChartGroups: groups,
    debugRuntimeState: {graphRangeSeconds: 900},
    debugGraphDomain: () => ({startMs: 0, endMs: 900000}),
    debugGraphSourceBuckets: () => [],
    debugGraphDisconnectedRanges: () => [],
    debugGraphAgentTokenDisplayBuckets: () => { tokenCalls += 1; return [{value, durationMs: 1000}]; },
    debugGraphDisplayBuckets: () => { hiddenCalls += 1; return []; },
    debugGraphDisplayResolutionMs: () => 1000,
    debugGraphLocalDateKey: () => 'same-day',
    debugGraphTimeLabel: (_time, options) => options.includeSeconds ? 'seconds' : 'minutes',
    esc: value => String(value),
    debugGraphTokenSpikeAxisDescriptor: () => ({}),
    debugGraphSeriesData: buckets => { seriesCalls += 1; return buckets.map(bucket => bucket.value); },
    debugGraphChartHtml: (group, series, domain, _buckets, _overlays, _disconnected, options) => {
      charts.push({key: group.key, series});
      return group.key + context.debugGraphXAxisHtml(domain, options.renderedResolutionSeconds);
    },
    debugGraphChartShellHtml: html => html,
  };
  vm.runInNewContext(`${sourceFunction('debugGraphBucketsForChartGroup', 'debugGraphRenderedResolutionSeconds')}\n${sourceFunction('debugGraphRenderedResolutionSeconds', 'debugGraphHoverBucketIndex')}\n${sourceFunction('debugGraphXAxisHtml', 'debugGraphGroupSeriesItems')}\n${sourceFunction('debugGraphSvgHtml', 'debugGraphClassName')}`, context);
  assert.match(context.debugGraphSvgHtml([], [], groups.slice(1), 1000), /agentTokens.*seconds.*modelTokens.*seconds/s);
  assert.equal(tokenCalls, 1, 'both token charts and their shared axis use one aggregation');
  assert.equal(hiddenCalls, 0, 'hidden chart resolutions do not trigger history aggregation');
  assert.equal(seriesCalls, 1, 'the same aggregated cells project into series once');
  assert.equal(charts[0].series, charts[1].series);
  value = 2;
  context.debugGraphSvgHtml([], [], groups.slice(1), 1000);
  assert.equal(tokenCalls, 2, 'a later render observes changed source data even at the same timestamp');
  assert.equal(charts[2].series[0], 2);
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

test('debug subviews share one complete lifecycle descriptor registry', () => {
  const registry = source.slice(
    source.indexOf('const DEBUG_SUBVIEWS = Object.freeze(['),
    source.indexOf('\nfunction debugSubview(id)'),
  );
  for (const id of ['logs', 'system', 'events', 'graph', 'cost']) {
    assert.equal((registry.match(new RegExp(`id: '${id}'`, 'g')) || []).length, 1, `${id} has one descriptor`);
  }
  const factory = sourceFunction('debugSubviewDescriptor', 'debugPanelHtml');
  for (const hook of ['render', 'bind', 'activate', 'deactivate', 'relocalize']) {
    assert.match(factory, new RegExp(`${hook} = debugSubviewNoop`), `${hook} has an explicit no-op default`);
  }
  assert.match(sourceFunction('debugPanelHtml', 'relocalizeDebugPanelChrome'), /debugSubview\(id\)\.html\(\)/);
  assert.match(sourceFunction('refreshDebugPanelFromEvents', 'syncDebugGraphControls'), /view\.render\(panel, options\)/);
  assert.match(source.slice(source.indexOf('function bindDebugPanel(')), /view\.bind\(panel\)/);
  const logsPolling = sourceFunction('syncDebugLogsPolling', 'debugSubviewNoop');
  assert.match(logsPolling, /return pollDebugLogs\(\{force: true\}\)/, 'the logs owner performs its initial poll');
  assert.doesNotMatch(logsPolling, /syncDebugSubviewActivation/, 'the logs owner does not recursively redispatch subview activation');
  assert.match(source.slice(source.indexOf("id: 'logs'"), source.indexOf("id: 'system'")), /activate: \(\{pollNow = false\} = \{\}\) => syncDebugLogsPolling\(\{pollNow\}\)/, 'the logs descriptor owns logs activation');
  assert.match(sourceFunction('relocalizeDebugPanelChrome', 'yoCostPanelHtml'), /view\.relocalize\(panel\)/);
});

test('the YO!cost report shows one always-visible column legend sharing the description owner', () => {
  // Legend rendered once in the report body, above the usage tables.
  const reportFn = sourceFunction('debugGraphCostReportHtml', 'refreshDebugCostPricing');
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
  const report = sourceFunction('debugGraphCostReportHtml', 'refreshDebugCostPricing');
  const usageTable = sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml');
  const formula = sourceFunction('debugGraphCostModelFormulaCellHtml', 'debugGraphCostTmuxLabel');
  assert.doesNotMatch(report, /debugGraphCostComponentDetailsHtml|data-js-debug-cost-table="calculation"/);
  assert.match(usageTable, /debugGraphCostModelFormulaCellHtml\(evidence, row, item\)/);
  assert.match(formula, /cache_write_5m.*cache_write_1h/);
  assert.match(formula, /x \$\{esc\(debugGraphCostComponentRateText\(row\)\)\} =/);
  assert.match(formula, /js-debug-cost-model-formula/);
});

test('YO!cost keeps Cost by Model and Cost by Agent on one cache-write grid', () => {
  const gridStart = source.indexOf('const DEBUG_GRAPH_COST_USAGE_COLUMN_KEYS = Object.freeze(');
  const gridEnd = source.indexOf('\nfunction debugGraphCostUsageTableCellHtml(', gridStart);
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
  const sorter = sourceFunction('debugGraphCostAgentRowsAlphabetically', 'debugGraphCostTmuxBreakdownHtml');
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
  const helper = sourceFunction('debugGraphCostAgentLabelHtml', 'debugGraphCostCatalogDetailsHtml');
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

test('Cost by Agent owns expandable source attribution instead of a separate source section', () => {
  const table = sourceFunction('debugGraphCostTmuxBreakdownHtml', 'debugGraphCostTranscriptPath');
  assert.match(sourceFunction('debugGraphCostUsageTableHtml', 'debugGraphCostModelUsageChartHtml'), /js-debug-cost-agent-row/);
  assert.match(table, /js-debug-cost-agent-sources/);
  assert.doesNotMatch(table, /data-js-debug-cost-table="source"/);
  assert.doesNotMatch(source, /Agent and source attribution/);
});

test('Cost refresh preserves expanded agent rows by stable key', () => {
  const renderSource = sourceFunction('renderYoCostPanel', 'renderYoCostPanels');
  const anchorSource = coreSource.slice(coreSource.indexOf('function keyedDetailsOpenAnchor('), coreSource.indexOf('\nfunction wsUrl('));
  assert.match(renderSource, /keyedDetailsOpenAnchor\('\.js-debug-cost-agent-row\[data-js-debug-cost-agent-key\]'\)/);
  assert.match(anchorSource, /filter\(element => element\.open\)/);
  assert.match(anchorSource, /element\.open = keys\.has/);
});

test('YO!cost usage metrics and compact pricing links stay on one line', () => {
  const cell = sourceFunction('debugGraphCostUsageTableCellHtml', 'debugGraphCostExactTotalRow');
  assert.match(cell, /js-debug-cost-table-metric js-debug-cost-table-metric--inline/);
  assert.doesNotMatch(source, /debugGraphCostPricingLinksHtml|debugGraphCostAllPricingSourcesHtml/);
  assert.match(css, /\.js-debug-cost-table-metric--inline\s*\{[\s\S]*?display: inline-flex;[\s\S]*?white-space: nowrap;/);
  assert.match(css, /\.js-debug-cost-table-metric--inline \.js-debug-cost-price-pair\s*\{[\s\S]*?display: inline-flex;/);
  assert.match(css, /\.js-debug-cost-model-copy\s*\{[\s\S]*?display: flex;[\s\S]*?white-space: nowrap;/);
});

test('Daemons chart mirrors the server continuous one-second service-load cadence', () => {
  const manifestSource = slice(source, 'const jsDebugStatsFamilyManifest', 'const jsDebugGraphDaemonsChartGroup');
  assert.match(manifestSource, /service_load: Object\.freeze\(\{[^}]*cadenceSeconds: 1,/, 'the client mirrors the server continuous 1 Hz service-load cadence');
  assert.match(source, /const jsDebugGraphDaemonsChartGroup = Object\.freeze\(\{[\s\S]*kind: 'area',[\s\S]*stacked: true,[\s\S]*serviceLoad: true,[\s\S]*runtimeOnly: true,[\s\S]*bucketSeconds: jsDebugStatsFamilyManifest\.service_load\.cadenceSeconds/);
  assert.match(source, /const jsDebugGraphDaemonsProcessSeriesGroup = Object\.freeze\(\{[\s\S]*\.\.\.jsDebugGraphDaemonsChartGroup,[\s\S]*processOnly: true/);
  const areaSource = sourceFunction('debugGraphSeriesUsesArea', 'debugGraphLegendSwatchHtml');
  assert.match(areaSource, /series\?\.serviceLoad === true/, 'Daemons service-load series use the shared area renderer');
  assert.match(areaSource, /series\?\.serviceLoad === true/, 'every displayed Daemons owner uses a cumulative area');
  assert.match(source, /runtimeDetail: false/, 'only independent top-level owners are recorded for stacked occupancy');
  assert.match(css, /\.js-debug-daemons-chart \.js-debug-chart-shell > \.js-debug-chart-grid > \.js-debug-chart\s*\{[\s\S]*max-height: none;/, 'Daemons chart override wins against the shared max-height selector');
  assert.match(css, /max-height: none;[\s\S]*overflow: visible;/, 'Daemon legend entries never scroll inside the chart');
  assert.match(source, /unit: 'runtimeOccupancy'/, 'the Daemons chart labels elapsed occupancy separately from process CPU');
  assert.match(source, /const jsDebugGraphDaemonsMemoryChartGroup = Object\.freeze\(\{[\s\S]*unit: 'bytes',[\s\S]*serviceMetric: 'rss',[\s\S]*processOnly: true/, 'Daemons exposes process RSS on a separate bytes chart');
  assert.match(source, /const jsDebugGraphDaemonsChartGroups = Object\.freeze\(\[[\s\S]*jsDebugGraphDaemonsChartGroup,[\s\S]*jsDebugGraphDaemonsMemoryChartGroup/);
  assert.match(source, /const jsDebugGraphDaemonsSeriesGroups = Object\.freeze\(\[[\s\S]*jsDebugGraphDaemonsChartGroup,[\s\S]*jsDebugGraphDaemonsProcessSeriesGroup,[\s\S]*jsDebugGraphDaemonsMemoryChartGroup/);
  assert.match(sourceFunction('debugGraphSeriesData', 'debugGraphChartCapacityMax'), /jsDebugGraphDaemonsSeriesGroups\.flatMap/);
});

test('Daemons keep unique stable color and line-pattern identities beyond nine services', () => {
  const visualSource = sourceFunction('debugGraphStablePaletteIndex', 'debugGraphSelectedModelTokenBucketValue');
  const seriesSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  const serviceKeys = ['approvald', 'indexd', 'batchd', 'statsd', 'statusd', 'watchd', 'storaged', 'eventd', 'costd', 'schedulerd'];
  const context = {
    Number,
    String,
    Map,
    Set,
    result: null,
    jsDebugGraphAgentTokenColors: ['blue', 'orange', 'magenta', 'gold', 'green', 'red', 'violet'],
    jsDebugGraphAgentTokenPatternCount: 7,
    jsDebugGraphDisplayHoldExpiryMs: {tenSecondGauge: 10000},
    debugGraphServiceLoadEffectiveMode: () => 'avg',
    debugGraphServiceLoadValue: () => 1,
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
    debugGraphServiceLoadSampleCount: (item, metric = 'cpu') => Number(item?.[metric === 'rss' ? 'rssSamples' : 'cpuSamples'] || 0),
    debugGraphServiceMemoryValue: item => Number(item?.rssSamples || 0) > 0 ? Number(item.rssTotalBytes || 0) / Number(item.rssSamples) : 0,
    debugGraphVisibleServiceLoadItems: buckets => [...(buckets[0]?.services || [])].map(key => [key, {label: key, cpuSamples: 1}]),
  };
  vm.runInNewContext(`
    ${visualSource}
    ${seriesSource}
    const identities = defs => Object.fromEntries(defs.map(series => [series.label, series.color + '|' + series.linePattern]));
    const full = debugGraphServiceLoadSeriesDefs([{services: ${JSON.stringify(serviceKeys)}}]);
    const withoutFirst = debugGraphServiceLoadSeriesDefs([{services: ${JSON.stringify(serviceKeys.slice(1))}}]);
    result = {full: identities(full), withoutFirst: identities(withoutFirst)};
  `, context);
  const fullIdentities = {...context.result.full};
  const withoutFirstIdentities = {...context.result.withoutFirst};
  assert.equal(new Set(Object.values(fullIdentities)).size, serviceKeys.length, 'ten visible daemons never reuse an exact color and line-pattern pair');
  assert.equal(new Set(Object.values(fullIdentities).slice(0, 7).map(identity => identity.split('|')[0])).size, 7, 'the first seven visible daemons each receive a different primary color');
  for (const key of serviceKeys.slice(1)) {
    assert.equal(withoutFirstIdentities[key], fullIdentities[key], `${key} keeps its identity when another daemon disappears`);
  }
});

test('Daemon memory uses the System memory palette before using fill patterns for repeats', () => {
  const visualSource = sourceFunction('debugGraphStablePaletteIndex', 'debugGraphSelectedModelTokenBucketValue');
  const seriesSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  const patternSource = sourceFunction('debugGraphAreaPatternHtml', 'debugGraphServiceLoadPatternDefinitionsHtml');
  const serviceKeys = Array.from({length: 8}, (_unused, index) => `process-${index}`);
  const context = {
    Number,
    String,
    Map,
    Set,
    result: null,
    jsDebugGraphAgentTokenColors: ['blue', 'orange', 'magenta', 'gold', 'green', 'red', 'violet'],
    jsDebugGraphCpuProcessAreaColors: ['cyan', 'orange', 'magenta', 'turquoise'],
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot', 'dash-dot', 'long-dash', 'dense-dot', 'long-short'],
    jsDebugGraphDisplayHoldExpiryMs: {tenSecondGauge: 10000},
    debugGraphStablePaletteIndex: (_key, count) => count - 1,
    debugGraphServiceLoadEffectiveMode: () => 'avg',
    debugGraphServiceLoadValue: () => 1,
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
    debugGraphServiceLoadSampleCount: (item, metric = 'cpu') => Number(item?.[metric === 'rss' ? 'rssSamples' : 'cpuSamples'] || 0),
    debugGraphServiceMemoryValue: item => Number(item?.rssSamples || 0) > 0 ? Number(item.rssTotalBytes || 0) / Number(item.rssSamples) : 0,
    debugGraphVisibleServiceLoadItems: buckets => [...(buckets[0]?.services || [])].map(key => [key, {label: key, rssSamples: 1}]),
  };
  vm.runInNewContext(`
    ${visualSource}
    ${seriesSource}
    const definitions = debugGraphServiceLoadSeriesDefs([{services: ${JSON.stringify(serviceKeys)}}], {serviceMetric: 'rss'});
    result = definitions.map(series => ({color: series.color, patternIndex: series.patternIndex}));
  `, context);
  const visuals = JSON.parse(JSON.stringify(context.result));
  assert.deepEqual(visuals.slice(0, 7), [
    {color: 'blue', patternIndex: 0},
    {color: 'orange', patternIndex: 0},
    {color: 'magenta', patternIndex: 0},
    {color: 'gold', patternIndex: 0},
    {color: 'green', patternIndex: 0},
    {color: 'red', patternIndex: 0},
    {color: 'violet', patternIndex: 0},
  ]);
  assert.equal(visuals[7].color, visuals[0].color, 'the eighth process reuses the first palette color');
  assert.notEqual(visuals[7].patternIndex, visuals[0].patternIndex, 'the repeated color gets a different fill pattern');
  assert.equal(new Set(visuals.map(item => `${item.color}|${item.patternIndex}`)).size, visuals.length, 'every displayed memory process keeps a distinct combined identity');
  assert.match(patternSource, /M2 0V8M6 0V8/, 'repeated colors use vertical strokes');
  assert.match(patternSource, /<circle cx=/, 'the pattern set includes visible dots');
  assert.doesNotMatch(patternSource, /M0 3H8|M0 2H4|M6 6H10/, 'fill patterns contain no horizontal hatch');
  const paintContext = {esc: String, result: null};
  vm.runInNewContext(`${patternSource}
    result = Array.from({length: 7}, (_, index) => debugGraphAreaPatternHtml('probe-' + index, '#00a8ff', index));
  `, paintContext);
  for (const [index, pattern] of [...paintContext.result].entries()) {
    assert.doesNotMatch(pattern, /opacity=/, 'no translucent pattern background or marks');
    for (const match of pattern.matchAll(/<path[^>]* d="([^"]+)"/g)) assert.match(match[1], /^[M V0-9.]+$/, 'hatches use only vertical segments');
    if (index > 0) assert.match(pattern, /<path|<circle/, 'every repeated color has visible infill');
  }
});

test('retained service-load apply and merge share one bucket-item initializer', () => {
  const serviceLoadBucketItemSource = sourceFunction('debugGraphNewServiceLoadItem', 'debugGraphNewClientBucket');
  assert.match(serviceLoadBucketItemSource, /cpuTotalPercent: 0,[\s\S]*cpuRangeAvailable: false,[\s\S]*rssMaxBytes: 0/);
  const mergeSource = sourceFunction('debugGraphMergeBucket', 'compactJsDebugGraphBuckets');
  const applyHostSource = sourceFunction('debugGraphApplyHostMetrics', 'debugGraphAgentStatusSnapshot');
  assert.equal((mergeSource.match(/debugGraphNewServiceLoadItem\(/g) || []).length, 1, 'retained bucket merge uses the shared service-load initializer');
  assert.equal((applyHostSource.match(/debugGraphNewServiceLoadItem\(/g) || []).length, 1, 'retained server apply uses the shared service-load initializer');
  assert.doesNotMatch(mergeSource, /targetHost\.serviceLoad\.get\(key\) \|\| \{/, 'retained merge has no divergent inline service-load shape');
  assert.doesNotMatch(applyHostSource, /target\.serviceLoad\.get\(key\) \|\| \{/, 'retained apply has no divergent inline service-load shape');
});

test('Daemon load defaults coarse CPU to Max and one shared selector repaints Avg and Min values', () => {
  const valueSource = sourceFunction('normalizedDebugGraphServiceLoadMode', 'debugGraphServiceLoadSeriesDefs');
  const seriesSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  const context = {
    Number,
    String,
    Map,
    result: null,
    debugRuntimeState: {serviceLoadMode: 'avg'},
    debugGraphStableServiceLoadVisuals: items => items.map((_item, index) => ({color: `color-${index}`, patternIndex: index})),
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {tenSecondGauge: 10000},
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
    debugGraphServiceLoadSampleCount: (item, metric = 'cpu') => Number(item?.[metric === 'rss' ? 'rssSamples' : 'cpuSamples'] || 0),
    debugGraphServiceMemoryValue: item => Number(item?.rssSamples || 0) > 0 ? Number(item.rssTotalBytes || 0) / Number(item.rssSamples) : 0,
  };
  context.bucket = {
    startMs: 0,
    durationMs: 10000,
    hostMetrics: {serviceLoad: new Map([['statusd', {
      label: 'statusd', cpuTotalPercent: 11.7, cpuSamples: 10, cpuMinPercent: 0, cpuMaxPercent: 54, cpuRangeAvailable: true,
    }]])},
  };
  vm.runInNewContext(`${valueSource}\n${seriesSource}\nlet series = debugGraphServiceLoadSeriesDefs([bucket])[0];\nresult = {avg: series.value(bucket)};\ndebugRuntimeState.serviceLoadMode = 'max';\nseries = debugGraphServiceLoadSeriesDefs([bucket])[0];\nresult.max = series.value(bucket);\ndebugRuntimeState.serviceLoadMode = 'min';\nseries = debugGraphServiceLoadSeriesDefs([bucket])[0];\nresult.min = series.value(bucket);`, context);
  assert.deepEqual({...context.result}, {avg: 1.17, max: 54, min: 0});
  assert.match(valueSource, /function debugGraphServiceLoadValue\(item, mode = debugRuntimeState\.serviceLoadMode\)/, 'one value owner selects all three existing retained fields');
  assert.doesNotMatch(seriesSource, /cpuMinPercent[\s\S]*cpuMaxPercent[\s\S]*cpuTotalPercent/, 'the series does not duplicate three render paths');

  const controlsSource = sourceFunction('debugGraphServiceLoadModeControlsHtml', 'debugGraphControlsHtml');
  const controlsContext = {
    result: null,
    debugRuntimeState: {serviceLoadMode: 'avg'},
    normalizedDebugGraphServiceLoadMode: value => ['avg', 'max', 'min'].includes(value) ? value : 'avg',
    debugGraphServiceLoadModeLabel: mode => ({avg: 'Avg', max: 'Max', min: 'Min'}[mode]),
    t: key => key === 'debug.graph.chart.serversLoad' ? 'Daemons load' : key,
    esc: value => String(value),
  };
  controlsContext.availableBuckets = [{hostMetrics: {serviceLoad: new Map([
    ['web', {cpuSamples: 6, cpuRangeAvailable: false}],
    ['statusd', {cpuSamples: 6, cpuRangeAvailable: true}],
  ])}}];
  controlsContext.unavailableBuckets = [{hostMetrics: {serviceLoad: new Map([['statusd', {cpuSamples: 30, cpuRangeAvailable: false}]])}}];
  vm.runInNewContext(`${valueSource}\n${controlsSource}\nresult = {available: debugGraphServiceLoadModeControlsHtml(availableBuckets), unavailable: debugGraphServiceLoadModeControlsHtml(unavailableBuckets)};`, controlsContext);
  controlsContext.result = JSON.parse(JSON.stringify(controlsContext.result));
  const availableControls = controlsContext.result.available;
  const unavailableControls = controlsContext.result.unavailable;
  assert.match(availableControls, /role="radiogroup"[^>]*aria-label="Daemons load"/);
  assert.match(availableControls, /data-js-debug-service-load-mode="avg"[^>]*checked[^>]*aria-checked="true"/);
  assert.doesNotMatch(availableControls, /data-js-debug-service-load-mode="(?:max|min)"[^>]*\sdisabled(?:\s|>)/);
  assert.match(availableControls, />Avg<[^]*>Max<[^]*>Min</);
  assert.match(unavailableControls, /data-js-debug-service-load-mode="avg"[^>]*checked[^>]*aria-checked="true"/);
  assert.match(unavailableControls, /data-js-debug-service-load-mode="max"[^>]*disabled[^>]*aria-disabled="true"/);
  assert.match(unavailableControls, /data-js-debug-service-load-mode="min"[^>]*disabled[^>]*aria-disabled="true"/);
  const selectedModeSelector = '.js-debug-service-load-mode-control input:checked + span';
  const selectedModeStart = tokenCss.indexOf(selectedModeSelector);
  const selectedModeEnd = tokenCss.indexOf('}', selectedModeStart);
  assert.notEqual(selectedModeStart, -1, 'the selected Daemons mode joins the shared active-control selector group');
  const selectedModeRule = tokenCss.slice(selectedModeStart, selectedModeEnd + 1);
  assert.match(selectedModeRule, /color:\s*var\(--active-control-text\)/, 'Moon White and other bright accents use their contrast foreground');
  assert.match(selectedModeRule, /background:\s*var\(--active-control-bg\)/, 'the selected mode uses the shared active-control fill');
  assert.match(selectedModeRule, /border-color:\s*var\(--active-control-border\)/, 'the selected mode uses the shared active-control border');
  assert.match(sourceFunction('debugGraphChartHtml', 'debugGraphUsesLogScale'), /group\.key === 'serversLoad' \? debugGraphServiceLoadModeControlsHtml\(buckets, group\) : displayedSummaryHtml/);
  assert.match(valueSource, /debugGraphVisibleServiceLoadItems\(buckets, group\)/, 'range availability shares the renderer visible-service classifier');
  assert.match(seriesSource, /for \(const \[key, item\] of debugGraphVisibleServiceLoadItems\(buckets, group\)/, 'the renderer consumes the shared visible-service classifier');

  const defaultContext = {
    Number,
    result: null,
    debugRuntimeState: {serviceLoadMode: 'auto'},
  };
  defaultContext.coarse = [{durationMs: 300000, hostMetrics: {serviceLoad: new Map([['statsd', {cpuSamples: 30, cpuRangeAvailable: true}]])}}];
  defaultContext.fine = [{durationMs: 10000, hostMetrics: {serviceLoad: new Map([['statsd', {cpuSamples: 1, cpuRangeAvailable: true}]])}}];
  vm.runInNewContext(`${valueSource}\nresult = {coarse: debugGraphServiceLoadEffectiveMode(coarse), fine: debugGraphServiceLoadEffectiveMode(fine)};`, defaultContext);
  assert.deepEqual({...defaultContext.result}, {coarse: 'max', fine: 'avg'});

  const exactAdapterSource = sourceFunction('jsDebugCurrentBucketRecord', 'jsDebugCurrentBucketHasFamilyData');
  const serviceLoadItemSource = sourceFunction('jsDebugCurrentServiceLoadItem', 'jsDebugCurrentBucketRecord');
  assert.match(serviceLoadItemSource, /cpu_total_percent: 0,[\s\S]*cpu_min_percent: null,[\s\S]*cpu_max_percent: null,[\s\S]*rss_total_bytes: 0,[\s\S]*rss_min_bytes: 0,[\s\S]*rss_max_bytes: 0/);
  assert.equal((exactAdapterSource.match(/jsDebugCurrentServiceLoadItem\(record, source\)/g) || []).length, 4, 'all retained service-load dimensions share one record initializer');
  assert.doesNotMatch(exactAdapterSource, /cpu_min_percent: null|rss_min_bytes: 0/, 'the exact adapter has no divergent inline record shapes');
  assert.match(exactAdapterSource, /name\.startsWith\('service_cpu_min_percent:'\)/);
  assert.match(exactAdapterSource, /name\.startsWith\('service_cpu_max_percent:'\)/);
  assert.doesNotMatch(exactAdapterSource, /cpu_min_percent: value, cpu_max_percent: value/, 'the exact adapter never fabricates extrema from an average');
  assert.match(exactAdapterSource, /cpu_samples: sourceCount/);
  const cpuProjectionSource = sourceFunction('jsDebugCurrentCpuProjectionValue', 'jsDebugCurrentBucketRecord');
  assert.match(cpuProjectionSource, /return average/);
  assert.match(exactAdapterSource, /jsDebugCurrentCpuProjectionValue\(series, name, 'system_cpu_max_percent', duration\)/);
  assert.match(exactAdapterSource, /jsDebugCurrentCpuProjectionValue\(series, name, `cpu_max_percent:\$\{source\}`, duration\)/);

  const setterSource = sourceFunction('setDebugGraphServiceLoadMode', 'setDebugGraphChartLayout');
  const handlerSource = sourceFunction('handleDebugGraphControlEvent', 'bindDebugPanel');
  assert.match(setterSource, /debugRuntimeState\.serviceLoadMode = normalized/);
  assert.match(setterSource, /refreshDebugGraphSurfaces\(\)/, 'mode changes repaint through the shared owner');
  assert.match(handlerSource, /event\.type === 'change' && serviceLoadMode[\s\S]*setDebugGraphServiceLoadMode/);
  assert.equal(debugRuntimeSource.includes("serviceLoadMode: 'max'"), true, 'fresh state defaults Daemons to Max');
  assert.match(debugRuntimeSource, /const savedServiceLoadMode = normalizedDebugGraphServiceLoadPreference\(saved\.serviceLoadMode\)/);
  assert.match(debugRuntimeSource, /debugRuntimeState\.serviceLoadMode = savedServiceLoadMode === 'auto' \? 'max' : savedServiceLoadMode/);
  assert.match(debugRuntimeSource, /serviceLoadMode: debugRuntimeState\.serviceLoadMode/);
  for (const key of ['debug.graph.serviceLoad.mode.avg', 'debug.graph.serviceLoad.mode.max', 'debug.graph.serviceLoad.mode.min']) {
    assert.equal(typeof localeEn[key], 'string', `${key} is localized`);
  }
});

test('Daemons memory series expose retained service RSS without mixing runtime occupancy', () => {
  const valueSource = sourceFunction('normalizedDebugGraphServiceLoadMode', 'debugGraphServiceLoadSeriesDefs');
  const seriesSource = sourceFunction('debugGraphServiceLoadSeriesDefs', 'debugGraphDisplayHoldOutage');
  const context = {
    Number,
    String,
    Map,
    result: null,
    debugRuntimeState: {serviceLoadMode: 'max'},
    debugGraphStableServiceLoadVisuals: items => items.map((_item, index) => ({color: `color-${index}`, patternIndex: index})),
    jsDebugGraphServiceLoadLinePatterns: ['solid', 'dash', 'dot'],
    jsDebugGraphDisplayHoldExpiryMs: {tenSecondGauge: 10000},
    debugGraphServiceLoadEffectiveMode: () => 'avg',
    debugGraphServiceLoadMetric: group => group?.serviceMetric === 'rss' ? 'rss' : 'cpu',
    debugGraphServiceLoadSampleCount: (item, metric = 'cpu') => Number(item?.[metric === 'rss' ? 'rssSamples' : 'cpuSamples'] || 0),
    debugGraphServiceMemoryValue: item => Number(item?.rssSamples || 0) > 0 ? Number(item.rssTotalBytes || 0) / Number(item.rssSamples) : 0,
  };
  context.buckets = [{hostMetrics: {serviceLoad: new Map([
    ['yolomux-go', {label: 'yolomux-go', cpuSamples: 1, rssTotalBytes: 40 * 1024 * 1024, rssSamples: 1}],
    ['runtime:stats_lifecycle', {label: 'stats lifecycle', cpuSamples: 1, rssTotalBytes: 1, rssSamples: 1}],
  ])}}];
  vm.runInNewContext(`${valueSource}\n${seriesSource}\nconst defs = debugGraphServiceLoadSeriesDefs(buckets, {serviceLoad: true, serviceMetric: 'rss', processOnly: true}); result = defs.map(item => ({key: item.key, value: item.value(buckets[0]), samples: item.sampleCount(buckets[0]), hasData: item.hasData(buckets[0])}));`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)), [{key: 'serviceMemory:yolomux-go', value: 40 * 1024 * 1024, samples: 1, hasData: true}]);
});

testAsync('refill confirmation, rejected request, retry and stale GET preserve one operation', async () => {
  const timers = [];
  const timerDelays = [];
  const context = {setTimeout(callback, delay) { timers.push(callback); timerDelays.push(delay); return timers.length; }, clearTimeout() {}, TextEncoder, TextDecoder};
  vm.runInNewContext(currentSource, context);
  const requests = [];
  const confirmations = [];
  const listeners = new Set();
  const document = {hidden: false, addEventListener(_event, callback) { listeners.add(callback); }, removeEventListener(_event, callback) { listeners.delete(callback); }};
  const make = () => context.YOmuxStatsCurrent.createAgentHistoryRefill({document, confirm(options) {
    return new Promise(resolve => confirmations.push({options, resolve}));
  }, fetch(_url, options) {
    return new Promise(resolve => requests.push({method: options.method, resolve}));
  }});
  const click = (owner, selector) => owner.handleClick({target: {closest: expected => expected === selector ? {} : null}});
  const settle = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };
  const owner = make();
  owner.start();
  assert.equal(requests[0].method, 'GET');
  click(owner, '[data-stats-refill]');
  assert.equal(confirmations.at(-1).options.title, 'Refill the last 24 hours?');
  assert.match(confirmations.at(-1).options.message, /Deleted source history cannot be recovered/);
  assert.equal(confirmations.at(-1).options.messageVariant, 'danger');
  confirmations.at(-1).resolve('cancel');
  await settle();
  assert.equal(requests.length, 1);
  click(owner, '[data-stats-refill]');
  confirmations.at(-1).resolve('refill');
  await settle();
  assert.equal(requests[1].method, 'POST');
  assert.equal(owner.busy(), true);
  requests[1].resolve({ok: true, json: async () => ({state: 'running', operation_id: 'one', phase: 'Reading history'})});
  await settle();
  assert.equal(timerDelays.at(-1), 1000);
  requests[0].resolve({ok: true, json: async () => ({state: 'idle'})});
  await settle();
  assert.equal(owner.status().operation_id, 'one');
  assert.equal(owner.busy(), true);
  const confirmationCount = confirmations.length;
  click(owner, '[data-stats-refill]');
  assert.equal(confirmations.length, confirmationCount);
  document.hidden = true;
  for (const listener of listeners) listener();
  assert.equal(requests.length, 2);
  document.hidden = false;
  for (const listener of listeners) listener();
  assert.equal(requests.at(-1).method, 'GET');
  requests.at(-1).resolve({ok: true, json: async () => ({state: 'succeeded', message: 'Agent history refilled.'})});
  await settle();
  assert.equal(owner.busy(), false);
  owner.destroy();
  assert.equal(listeners.size, 0);

  const failed = make();
  click(failed, '[data-stats-refill]');
  confirmations.at(-1).resolve('refill');
  await settle();
  requests.at(-1).resolve({ok: false, status: 503});
  await settle();
  assert.equal(failed.status().state, 'failed');
  assert.match(failed.html(), /Your previous data is unchanged/);
  assert.match(failed.html(), /Try again/);
  const timerCount = timers.length;
  timers.at(-1)();
  requests.at(-1).resolve({ok: true, json: async () => ({state: 'idle'})});
  await settle();
  assert.equal(failed.status().state, 'failed');
  assert.equal(timers.length, timerCount + 1);
  assert.equal(timerDelays.at(-1), 10000);
  click(failed, '[data-stats-refill]');
  assert.equal(confirmations.at(-1).options.title, 'Refill the last 24 hours?');
  confirmations.at(-1).resolve('refill');
  await settle();
  requests.at(-1).resolve({ok: true, json: async () => ({state: 'succeeded', message: 'Agent history refilled.'})});
  await settle();
  assert.match(failed.html(), /Agent history refilled\./);
  assert.equal(failed.busy(), false);
  failed.destroy();
});

testAsync('stats alias snapshots refresh after view creation and reject stale topology responses', async () => {
  const functionText = sourceFunction('jsDebugSessionAliasTopologyKey', 'ensureJsDebugCurrentStatsClient');
  const requests = [];
  const applied = [];
  const context = {
    transcriptMetadataState: {epoch: 'test'}, tmuxTopologyGeneration: 1, sessions: ['source'], tabberViewSessions: new Map(),
    jsDebugCurrentStatsClientState: {client: {controller: () => ({generation: () => null})}, sessionAliasKey: '', sessionAliasPromise: null},
    jsDebugStatsPanelVisible: () => true,
    apiFetch: () => new Promise(resolve => requests.push(resolve)),
    debugGraphStatsCurrentModule: () => ({setSessionAliases: aliases => applied.push(aliases)}),
    paintJsDebugCurrentStatsGeneration() {}, recordJsDebugCurrentStatsFailure() {}, jsDebugErrorText: String,
  };
  vm.runInNewContext(functionText, context);
  const first = context.refreshJsDebugSessionAliases();
  requests.shift()({aliases: {old: 'source'}});
  await first;
  assert.equal(applied.length, 1);
  context.tabberViewSessions.set('new', {sourceSession: 'source', windowId: '@12'});
  const second = context.refreshJsDebugSessionAliases();
  context.tmuxTopologyGeneration = 2;
  requests.shift()({aliases: {stale: 'source'}});
  await second;
  assert.equal(applied.length, 1, 'obsolete response cannot replace last good aliases');
  assert.equal(requests.length, 1, 'changed topology schedules one successor');
  requests.shift()({aliases: {new: 'renamed'}});
  await context.jsDebugCurrentStatsClientState.sessionAliasPromise;
  assert.deepEqual(applied[1], {new: 'renamed'});
});

Promise.all(pending).then(() => {
  console.log(`stats current panel suite: ${passed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
});

test('range pointer preview stays uncommitted and cancelled change cannot resurrect it', () => {
  const context = {result: null};
  vm.runInNewContext(`
    let jsDebugGraphRangeSliderInteraction = null;
    const debugRuntimeState = {graphRangeSeconds: 300, graphResolutionOverrideSeconds: 10};
    let jsDebugGraphZoomDomain = {startMs: 1, endMs: 2};
    let requests = 0;
    let preferences = 0;
    const label = {textContent: '5m'};
    const graph = {querySelector: () => slider};
    const panel = {contains: () => true};
    const slider = {value: '0', closest: selector => selector === '[data-js-debug-range-slider]' ? slider : selector === '[data-js-debug-range-control]' ? {querySelector: () => label} : selector === '[data-js-debug-graph]' ? graph : null};
    function debugGraphAvailableRangeOptions() { return [{seconds: 300, label: '5m'}, {seconds: 3600, label: '1h'}]; }
    function jsDebugGraphRangeOptionIndex(seconds) { return seconds === 300 ? 0 : 1; }
    function syncDebugGraphControls() { slider.value = String(jsDebugGraphRangeOptionIndex(debugRuntimeState.graphRangeSeconds)); label.textContent = debugRuntimeState.graphRangeSeconds === 300 ? '5m' : '1h'; }
    function flushDeferredDebugGraphInteractionRefresh() {}
    function setDebugGraphRange(seconds) { debugRuntimeState.graphRangeSeconds = seconds; debugRuntimeState.graphResolutionOverrideSeconds = 60; jsDebugGraphZoomDomain = null; preferences++; requests++; }
    ${sourceFunction('debugGraphRangeSliderIndex', 'debugGraphPointerRatioFromRect')}
    ${sourceFunction('handleDebugGraphControlEvent', 'bindDebugPanel')}
    handleDebugGraphControlEvent({type: 'pointerdown', target: slider, pointerId: 7}, panel);
    slider.value = '1';
    handleDebugGraphControlEvent({type: 'input', target: slider}, panel);
    const preview = {range: debugRuntimeState.graphRangeSeconds, resolution: debugRuntimeState.graphResolutionOverrideSeconds, zoom: jsDebugGraphZoomDomain, requests, preferences, label: label.textContent};
    handleDebugGraphControlEvent({type: 'pointercancel', target: slider, pointerId: 7}, panel);
    handleDebugGraphControlEvent({type: 'change', target: slider}, panel);
    result = {preview, range: debugRuntimeState.graphRangeSeconds, resolution: debugRuntimeState.graphResolutionOverrideSeconds, zoom: jsDebugGraphZoomDomain, requests, preferences, value: slider.value, label: label.textContent, owner: jsDebugGraphRangeSliderInteraction};
  `, context);
  assert.equal(context.result.preview.range, 300);
  assert.equal(context.result.preview.label, '1h');
  assert.equal(context.result.range, 300);
  assert.equal(context.result.resolution, 10);
  assert.deepEqual({...context.result.zoom}, {startMs: 1, endMs: 2});
  assert.equal(context.result.requests, 0);
  assert.equal(context.result.preferences, 0);
  assert.equal(context.result.value, '0');
  assert.equal(context.result.label, '5m');
  assert.equal(context.result.owner, null);
});

function rangeGestureHarness() {
  const context = {result: null};
  vm.runInNewContext(`
    let jsDebugGraphRangeSliderInteraction = null;
    let jsDebugGraphZoomDomain = {startMs: 1, endMs: 2};
    const debugRuntimeState = {graphRangeSeconds: 300, graphResolutionOverrideSeconds: 10};
    let preferences = 0;
    const requests = [];
    const label = {textContent: '5m'};
    const graph = {dataset: {}, querySelector: () => slider};
    const panel = {contains: () => true};
    const slider = {value: '0', getClientRects: () => visible ? [1] : [], closest: selector => selector === '[data-js-debug-range-slider]' ? slider : selector === '[data-js-debug-range-control]' ? {querySelector: () => label} : selector === '[data-js-debug-graph]' ? graph : null};
    let visible = true;
    const events = new Map();
    let observerCallback;
    let observerDisposed = false;
    const window = {};
    const document = {getElementById: () => panel};
    class MutationObserver { constructor(callback) { observerCallback = callback; } observe() {} disconnect() { observerDisposed = true; } }
    function createLifecycleScope() {
      const disposals = [];
      return {ownEvent: (key, target, type, fn) => events.set(key, fn), replace: (key, value, dispose) => disposals.push(() => dispose(value)), dispose: () => { events.clear(); for (const dispose of disposals) dispose(); }};
    }
    function bindOnce(panel, key, bind) { return bind(); }
    function bindDebugGraphTouchSelection() {}
    function debugPanelSubviewDescriptors() { return []; }
    function syncDebugSubviewActivation() {}
    function bindActionDispatcher() { return () => {}; }
    function handleDebugGraphPointerUp() {}
    function handleDebugGraphPointerCancel() {}
    function setTimeout() {}
    function debugGraphAvailableRangeOptions() { return [{seconds: 300, label: '5m'}, {seconds: 3600, label: '1h'}]; }
    function jsDebugGraphRangeOptionIndex(seconds) { return seconds === 300 ? 0 : 1; }
    function syncDebugGraphControls() { slider.value = String(jsDebugGraphRangeOptionIndex(debugRuntimeState.graphRangeSeconds)); label.textContent = debugRuntimeState.graphRangeSeconds === 300 ? '5m' : '1h'; }
    function flushDeferredDebugGraphInteractionRefresh() {}
    function loadJsDebugStatsUiPreferences() {}
    function saveJsDebugStatsUiPreferences() { preferences++; }
    function normalizedJsDebugGraphRange(value) { return Number(value); }
    function debugGraphDefaultResolutionForRange() { return 60; }
    function activeJsDebugGraphRangeSeconds() {}
    function syncJsDebugStatsDeliveryMode() {}
    function debugGraphDomain() { return {startMs: 0}; }
    const jsDebugHistoryReadiness = {phase: 'ready'};
    function requestJsDebugHistoryForCurrentDomain() { requests.push({range: debugRuntimeState.graphRangeSeconds, resolution: debugRuntimeState.graphResolutionOverrideSeconds}); return true; }
    function refreshDebugGraphSurfaces() { syncDebugGraphControls(); }
    ${sourceFunction('setDebugGraphRange', 'setDebugGraphResolutionOverride')}
    ${sourceFunction('debugGraphRangeSliderIndex', 'debugGraphPointerRatioFromRect')}
    ${sourceFunction('handleDebugGraphControlEvent', 'bindDebugPanel')}
    ${source.slice(source.indexOf('function bindDebugPanel('), source.indexOf("\nregisterDebugRuntimeFacade('panel'"))}
    const dispose = bindDebugPanel(panel);
    globalThis.dispatch = (type, pointerId = 7, target = slider) => handleDebugGraphControlEvent({type, pointerId, target}, panel);
    globalThis.preview = () => { slider.value = '1'; dispatch('input'); };
    globalThis.cancel = kind => {
      if (kind === 'disposal') dispose();
      else if (kind === 'hiding') { visible = false; observerCallback(); }
      else if (kind === 'focusout') events.get('focusout')({target: slider});
      else if (kind === 'blur') events.get('range-blur')();
      else events.get(kind)({type: kind === 'range-release' ? 'pointerup' : kind === 'range-cancel' ? 'pointercancel' : kind, pointerId: 7, target: kind === 'range-release' ? panel : slider});
    };
    globalThis.snapshot = () => ({range: debugRuntimeState.graphRangeSeconds, resolution: debugRuntimeState.graphResolutionOverrideSeconds, zoom: jsDebugGraphZoomDomain, preferences, requests, owner: jsDebugGraphRangeSliderInteraction, value: slider.value, label: label.textContent, listeners: events.size, observerDisposed});
  `, context);
  return context;
}

for (const exit of ['pointercancel', 'lostpointercapture', 'range-cancel', 'focusout', 'blur', 'hiding', 'disposal']) {
  test(`range cancellation restores committed selection on ${exit}`, () => {
    const harness = rangeGestureHarness();
    harness.dispatch('pointerdown');
    harness.preview();
    assert.equal(harness.snapshot().owner.preview.seconds, 3600);
    assert.equal(harness.snapshot().range, 300);
    harness.cancel(exit);
    harness.dispatch('change');
    const state = harness.snapshot();
    assert.equal(state.range, 300);
    assert.equal(state.resolution, 10);
    assert.deepEqual({...state.zoom}, {startMs: 1, endMs: 2});
    assert.equal(state.preferences, 0);
    assert.equal(state.requests.length, 0);
    assert.equal(state.owner, null);
    assert.equal(state.value, '0');
    assert.equal(state.label, '5m');
    if (exit === 'disposal') { assert.equal(state.listeners, 0); assert.equal(state.observerDisposed, true); }
  });
}

for (const exit of ['pointerup', 'change', 'range-release']) {
  test(`range success commits once on ${exit} and ignores delayed completion`, () => {
    const harness = rangeGestureHarness();
    harness.dispatch('pointerdown');
    harness.preview();
    harness.dispatch('pointerup', 99);
    assert.equal(harness.snapshot().range, 300);
    assert.notEqual(harness.snapshot().owner, null);
    if (exit === 'range-release') harness.cancel(exit);
    else harness.dispatch(exit);
    harness.dispatch('change');
    harness.cancel('lostpointercapture');
    const state = harness.snapshot();
    assert.equal(state.range, 3600);
    assert.equal(state.resolution, 60);
    assert.equal(state.zoom, null);
    assert.equal(state.preferences, 1);
    assert.deepEqual(Array.from(state.requests, row => ({...row})), [{range: 3600, resolution: 60}]);
    assert.equal(state.owner, null);
    assert.equal(state.label, '1h');
  });
}

test('keyboard range input commits immediately without interaction ownership', () => {
  const harness = rangeGestureHarness();
  harness.preview();
  harness.dispatch('change');
  const state = harness.snapshot();
  assert.equal(state.range, 3600);
  assert.equal(state.owner, null);
  assert.equal(state.preferences, 1);
  assert.equal(state.requests.length, 1);
});

for (const surface of ['graph', 'cost', 'daemons']) {
  test(`range interaction defers ${surface} replacement through the shared owner`, () => {
    const context = {result: null};
    vm.runInNewContext(`
      const panel = {dataset: {}, querySelector() { throw new Error('active native slider replaced'); }};
      let jsDebugGraphSelectionState = null;
      let jsDebugGraphTouchCandidateState = null;
      let jsDebugGraphRangeSliderInteraction = {panel};
      ${sourceFunction('debugGraphInteractionBelongsToPanel', 'flushDeferredDebugGraphInteractionRefresh')}
      const graph = {dataset: {}, closest: () => panel, querySelector() { throw new Error('active native slider replaced'); }};
      const dragState = {item: null};
      const jsDebugCostPanelNextRefreshAtMs = 0;
      function jsDebugCostSubviewVisible() { return true; }
      const jsDebugSystemState = {payload: {}, inFlight: false};
      const DEBUG_SYSTEM_REGIONS = [1];
      const debugSystemRenderedRegions = new Map();
      const region = {dataset: {jsDebugSystemRegion: 'chart'}, set innerHTML(value) { throw new Error('active native slider replaced'); }};
      const view = {contains: () => false, querySelectorAll: () => [region], closest: () => panel, setAttribute() {}};
      const document = {querySelectorAll: () => [view]};
      function debugSystemSnapshotRefusal() { return false; }
      function debugSystemRegionHtml() { return 'new chart'; }
      function debugSystemRestoreFocus() {}
      function restoreElementScrollPosition() {}
      ${sourceFunction('refreshDebugGraphElement', 'bindDebugCostSummaryTabButtons')}
      ${sourceFunction('renderYoCostPanel', 'renderYoCostPanels')}
      ${sourceFunction('refreshDebugSystemViews', 'toggleDebugSystemRosterRow')}
      if ('${surface}' === 'graph') refreshDebugGraphElement(graph, {force: true});
      if ('${surface}' === 'cost') renderYoCostPanel(panel, {force: true});
      if ('${surface}' === 'daemons') refreshDebugSystemViews();
      result = {owned: debugGraphInteractionBelongsToPanel(panel), graphPending: graph.dataset.jsDebugGraphRefreshPending, costPending: panel.dataset.jsDebugGraphRefreshPending, daemonsPending: panel.dataset.jsDebugSystemChartRefreshPending};
    `, context);
    assert.equal(context.result.owned, true);
    assert.equal(context.result[`${surface}Pending`], 'true');
  });
}

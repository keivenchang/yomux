// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// JavaScript debug panel rendering and controls split from 80_panes_preferences.js.

const jsDebugGraphDefaultRangeSeconds = 15 * 60;
const jsDebugGraphGeometry = (() => {
  const width = 600;
  const height = 120;
  const plotTop = 8;
  const plotHeight = height - plotTop;
  return Object.freeze({
    width,
    height,
    plotTop,
    plotHeight,
    plotBottom: plotTop + plotHeight,
    hoverBottom: plotTop + plotHeight,
  });
})();
const jsDebugHistoryReadinessPhases = Object.freeze(['idle', 'loading-initial', 'loading-older', 'retrying', 'ready', 'error']);
const jsDebugHistoryLoadingPhases = new Set(['loading-initial', 'loading-older', 'retrying']);
const jsDebugHistoryOlderOverlayDelayMs = 120;
const jsDebugHistoryReadiness = {
  phase: 'idle',
  requestedRangeSeconds: jsDebugGraphDefaultRangeSeconds,
  targetStartSeconds: 0,
  targetEndSeconds: 0,
  requestedStartSeconds: 0,
  requestedEndSeconds: 0,
  requestedResolutionSeconds: 1,
  loadedStartSeconds: 0,
  loadedEndSeconds: 0,
  resolutionSeconds: 0,
  coverageIntervals: [],
  attemptCount: 0,
  error: '',
  generation: 0,
  loadingStartedAtMs: 0,
  overlayVisible: false,
  overlayTimer: null,
};
let jsDebugSubTab = 'graph';
let jsDebugGraphRangeSeconds = jsDebugGraphDefaultRangeSeconds;
let jsDebugGraphResolutionOverrideSeconds = 0;
let jsDebugGraphChartLayout = 0;
const jsDebugStatsPollState = {
  inFlight: false,
  pending: false,
  pendingForceGraphRefresh: false,
  firstSampleReceived: false,
};
const jsDebugStatsUploadState = {
  timer: null,
  worker: null,
  generation: 0,
};
let jsDebugStatsServerSequence = 0;
let jsDebugStatsAgentTokenSequence = 0;
let jsDebugStatsAgentTokenResolutionSeconds = 0;
let jsDebugStatsAgentTokenSchemaVersion = 0;
let jsDebugStatsServerUptimeSeconds = null;
let jsDebugStatsServerPid = null;
let jsDebugStatsServerStartedAt = null;
let jsDebugStatsServerRssBytes = null;
let jsDebugStatsClientId = '';
let jsDebugStatsClientConnected = null;
let jsDebugStatsDisconnectStartedAtMs = null;
let jsDebugGraphZoomDomain = null;
let jsDebugGraphSelectionState = null;
let jsDebugGraphRangeSliderDragging = false;
let jsDebugGraphHiddenCharts = null;
let jsDebugGraphVisibleCharts = null;
let jsDebugStatsUiPreferencesLoaded = false;
const jsDebugGraphRangeOptions = Object.freeze([
  {seconds: 60, label: '1m'},
  {seconds: 5 * 60, label: '5m'},
  {seconds: 15 * 60, label: '15m'},
  {seconds: 30 * 60, label: '30m'},
  {seconds: 60 * 60, label: '1h'},
  {seconds: 2 * 60 * 60, label: '2h'},
  {seconds: 4 * 60 * 60, label: '4h'},
  {seconds: 8 * 60 * 60, label: '8h'},
  {seconds: 16 * 60 * 60, label: '16h'},
  {seconds: 24 * 60 * 60, label: '24h'},
]);
const jsDebugGraphRetentionMs = 24 * 60 * 60 * 1000;
const jsDebugGraphMaxDisplayPoints = 120;
const jsDebugGraphDisplayBucketMs = Object.freeze([1000, 2000, 5000, 10_000, 30_000, 60_000, 120_000, 300_000, 600_000]);
const jsDebugGraphTiers = Object.freeze([
  Object.freeze({maxAgeMs: 30 * 60 * 1000, bucketMs: 1000}),
  Object.freeze({maxAgeMs: 2 * 60 * 60 * 1000, bucketMs: 10 * 1000}),
  Object.freeze({maxAgeMs: 4 * 60 * 60 * 1000, bucketMs: 60 * 1000}),
  Object.freeze({maxAgeMs: 8 * 60 * 60 * 1000, bucketMs: 2 * 60 * 1000}),
  Object.freeze({maxAgeMs: 12 * 60 * 60 * 1000, bucketMs: 5 * 60 * 1000}),
  Object.freeze({maxAgeMs: jsDebugGraphRetentionMs, bucketMs: 10 * 60 * 1000}),
]);
const jsDebugGraphRawWindowMs = jsDebugGraphTiers[0].maxAgeMs;
const jsDebugGraphMiddleWindowMs = jsDebugGraphTiers[1].maxAgeMs;
const jsDebugGraphRawBucketMs = jsDebugGraphTiers[0].bucketMs;
const jsDebugGraphMiddleBucketMs = jsDebugGraphTiers[1].bucketMs;
const jsDebugGraphRollupBucketMs = jsDebugGraphTiers[2].bucketMs;
const jsDebugGraphResponseRefRetentionMs = 5 * 60 * 1000;
const jsDebugStatsPollFastMs = 2001;
const jsDebugStatsPollMs = 30001;
const jsDebugStatsPollTimeoutMs = 5000;
const jsDebugStatsHistoryFlushMs = 30000;
const jsDebugGraphRefreshMs = 30001;
// A request-driven client can be quiet between normal polls. Only mark the portion
// after this continuous silence as missing communication, rather than treating each
// empty raw bucket as a connection failure.
const jsDebugGraphNoDataOverlayDelayMs = 30000;
const jsDebugStatsHistoryMaxPoints = 6000;
const jsDebugStatsHistoryPostMaxRecords = 1000;
const jsDebugStatsClientStorageKey = 'yolomux.stats.client_id.v1';
const jsDebugStatsDisconnectedStorageKey = 'yolomux.stats.disconnected_at.v1';
const jsDebugStatsUiPreferencesStorageKey = 'yolomux.stats.ui_preferences.v1';
const jsDebugGraphDefaultHiddenChartKeys = Object.freeze(['memory', 'gpuUtil', 'gpuMemory']);
const jsDebugGraphMovingAverageSamples = 10;
const jsDebugGraphAgentTokenBucketSeconds = 60;
const jsDebugGraphThisClientId = 'this-client';
const jsDebugGraphOtherClientsAverageId = 'other-clients-average';
const jsDebugGraphThisClientAggregate = 'thisClient';
const jsDebugGraphOtherClientsAverageAggregate = 'otherClientsAverage';
const jsDebugGraphThisClientLinePattern = 'solid';
const jsDebugGraphOtherClientsAverageLinePattern = 'solid';
const jsDebugGraphDisplayedSummarySpecs = Object.freeze({
  clientRequests: {
    attribute: 'displayed-client-request-sum',
    labelKey: 'debug.graph.sumDisplayedClientRequests',
    value: buckets => debugGraphDisplayedClientFieldSum(buckets, ['apiCount', 'sseCount']),
    format: debugGraphTokenNumberText,
  },
  bandwidth: {
    attribute: 'displayed-bandwidth-sum',
    labelKey: 'debug.graph.sumDisplayed',
    value: buckets => debugGraphDisplayedClientFieldSum(buckets, ['bandwidthBytes']),
    format: value => debugGraphValueText(value, 'bytes'),
  },
  agentTokens: {
    attribute: 'displayed-token-sum',
    labelKey: 'debug.graph.sumDisplayed',
    value: debugGraphAgentTokenDisplayedSum,
    format: debugGraphTokenNumberText,
  },
});
const jsDebugGraphClientMetrics = Object.freeze([
  {key: 'api', labelKey: 'debug.graph.metric.api', unit: 'countPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.apiCount), hasData: bucket => Number(bucket.apiCount || 0) > 0},
  {key: 'sse', labelKey: 'debug.graph.metric.sse', unit: 'countPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.sseCount), hasData: bucket => Number(bucket.sseCount || 0) > 0},
  {key: 'latency', labelKey: 'common.clientLatency', unit: 'ms', value: bucket => bucket.latencyCount ? bucket.latencyTotalMs / bucket.latencyCount : 0, hasData: bucket => Number(bucket.latencyCount || 0) > 0},
  {key: 'bandwidth', labelKey: 'debug.graph.metric.bandwidth', unit: 'bytesPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.bandwidthBytes), hasData: bucket => Number(bucket.bandwidthBytes || 0) > 0},
]);
const jsDebugGraphAgentTokenSeriesPrefix = 'agentToken:';
const jsDebugAgentStatusSeriesKeys = Object.freeze(['askAgents', 'workingAgents', 'transitionAgents', 'idleAgents']);
const jsDebugAgentStatusLegendSeriesKeys = Object.freeze(['workingAgents', 'askAgents', 'transitionAgents', 'idleAgents']);
const jsDebugAgentStatusSeriesLabelKeys = Object.freeze({
  askAgents: 'debug.graph.status.attention',
  workingAgents: 'state.working',
  transitionAgents: 'debug.graph.status.transition',
  idleAgents: 'state.idle',
});
const jsDebugAgentStatusBucketValueGetters = Object.freeze({
  askAgents: bucket => bucket.agentActivitySamples ? bucket.askAgentTotal / bucket.agentActivitySamples : 0,
  workingAgents: bucket => bucket.agentActivitySamples ? bucket.runAgentTotal / bucket.agentActivitySamples : 0,
  transitionAgents: bucket => bucket.agentActivitySamples ? bucket.transitionAgentTotal / bucket.agentActivitySamples : 0,
  idleAgents: bucket => bucket.agentActivitySamples ? bucket.idleAgentTotal / bucket.agentActivitySamples : 0,
});
function debugGraphAgentStatusSeriesDef(key) {
  return {
    key,
    labelKey: jsDebugAgentStatusSeriesLabelKeys[key],
    unit: 'count',
    value: bucket => jsDebugAgentStatusBucketValueGetters[key](bucket),
    hasData: bucket => Number(bucket?.agentActivitySamples || 0) > 0,
  };
}
const jsDebugGraphAgentTokenColors = Object.freeze([
  'var(--js-debug-agent-token-cyan)',
  'var(--js-debug-agent-token-orange)',
  'var(--js-debug-agent-token-magenta)',
  'var(--js-debug-agent-token-beige)',
  'var(--js-debug-agent-token-turquoise)',
  'var(--js-debug-agent-token-rose)',
  'var(--js-debug-agent-token-violet)',
]);
// Horizontal-only strokes remain legible inside short stacked bars. Color is the primary identity;
// these distinct horizontal cadences provide a second cue without vertical hatching disappearing
// into the one-minute bar edges.
const jsDebugGraphAgentTokenPatternShapes = Object.freeze([
  '',
  '<path d="M0 1H6"></path>',
  '<path d="M0 1H2M3 1H5"></path>',
  '<path d="M0 1H0.5M1.5 1H2M3 1H3.5M4.5 1H5"></path>',
  '<path d="M0 1H3M4 1H4.5"></path>',
  '<path d="M0 0.5H2M3 0.5H5M1 1.5H3M4 1.5H6"></path>',
  '<path d="M0 0.5H6M0 1.5H6"></path>',
]);
const jsDebugGraphAgentTokenPatternCount = jsDebugGraphAgentTokenPatternShapes.length;
const jsDebugGraphProcessCpuColors = Object.freeze({
  current: 'var(--active-accent-bright)',
  // Green is reserved for the server that is serving this browser. Peers must remain
  // distinguishable without being mistaken for the current YOLOmux process.
  peers: Object.freeze(['var(--bad)', 'var(--accent-gold)', 'var(--link-soft)']),
});
const jsDebugGraphGpuDeviceColors = Object.freeze([
  'var(--active-accent-bright)',
  'var(--bad)',
  'var(--link-soft)',
  'var(--accent-gold)',
]);
const jsDebugGraphRawBuckets = new Map();
const jsDebugGraphRollupBuckets = new Map();
const jsDebugGraphAgentTokenBuckets = new Map();
const jsDebugGraphEventRecords = new Map();
const jsDebugGraphPendingServerBuckets = new Map();
const jsDebugGraphHoverChartData = new Map();
const jsDebugGraphSeries = Object.freeze([
  ...jsDebugGraphClientMetrics.map(metric => debugGraphClientSeriesDef(metric, {labelKey: 'debug.graph.series.thisClient', clientId: jsDebugGraphThisClientId, clientAggregate: jsDebugGraphThisClientAggregate, clientLinePattern: jsDebugGraphThisClientLinePattern})),
  ...jsDebugAgentStatusSeriesKeys.map(debugGraphAgentStatusSeriesDef),
  {key: 'tokensPerAgent', labelKey: 'debug.graph.series.tokensPerAgent', unit: 'tokensPerMinute', value: bucket => bucket.agentTokenSamples ? bucket.tokensPerAgentTotal / bucket.agentTokenSamples : 0, hasData: bucket => Number(bucket?.agentTokenSamples || 0) > 0},
  {key: 'systemCpu', labelKey: 'debug.graph.series.systemCpu', unit: 'percent', linePattern: 'solid', value: bucket => bucket.systemCpuCount ? Math.min(100, bucket.systemCpuTotalPercent / bucket.systemCpuCount) : 0, hasData: bucket => Number(bucket?.systemCpuCount || 0) > 0},
  {key: 'systemMemory', labelKey: 'debug.graph.series.systemMemory', unit: 'bytes', linePattern: 'solid', value: bucket => bucket.hostMetrics?.systemMemoryCount ? bucket.hostMetrics.systemMemoryUsedTotalBytes / bucket.hostMetrics.systemMemoryCount : 0, hasData: bucket => Number(bucket?.hostMetrics?.systemMemoryCount || 0) > 0},
]);
const jsDebugGraphChartGroups = Object.freeze([
  {key: 'cpu', labelKey: 'debug.graph.chart.cpu', series: ['systemCpu'], unit: 'percent', fixedMax: 100, hostMetric: 'cpu'},
  {key: 'memory', labelKey: 'debug.graph.chart.memory', series: ['systemMemory'], unit: 'bytes', kind: 'area', stacked: true, hostMetric: 'memory', capacityMetric: 'systemMemory'},
  {key: 'gpuUtil', labelKey: 'debug.graph.chart.gpuUtil', series: [], unit: 'percent', fixedMax: 100, kind: 'bar', zeroBar: true, hostMetric: 'gpuUtil'},
  {key: 'gpuMemory', labelKey: 'debug.graph.chart.gpuMemory', series: [], unit: 'bytes', hostMetric: 'gpuMemory', capacityMetric: 'gpuMemory'},
  {key: 'latency', labelKey: 'common.clientLatency', series: ['latency'], unit: 'ms', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'count', labelKey: 'debug.graph.chart.clientApiSse', series: ['api', 'sse'], unit: 'countPerSecond', displayedSummary: 'clientRequests', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'bandwidth', labelKey: 'debug.graph.chart.clientBandwidth', series: ['bandwidth'], unit: 'bytesPerSecond', displayedSummary: 'bandwidth', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'activity', labelKey: 'debug.graph.chart.agentStatus', series: jsDebugAgentStatusSeriesKeys, legendSeries: jsDebugAgentStatusLegendSeriesKeys, unit: 'count', kind: 'bar', stacked: true, integerAxis: true, integerGridLines: true, exactIntegerAxisMax: true, bucketSeconds: 10},
  {key: 'agentTokens', labelKey: 'debug.graph.chart.agentTokens', series: [], unit: 'tokensPerMinute', kind: 'bar', stacked: true, dynamicAgentTokens: true, displayedSummary: 'agentTokens', bucketSeconds: jsDebugGraphAgentTokenBucketSeconds},
]);

function debugGraphLocalizedLabel(item = {}) {
  if (!item.labelKey) return String(item.label || '');
  const params = {...(item.labelParams || {})};
  if (item.metricLabelKey) params.metric = t(item.metricLabelKey);
  return t(item.labelKey, params);
}

function normalizedJsDebugSubTab(value) {
  return value === 'events' ? 'events' : 'graph';
}

function normalizedJsDebugGraphRange(value, nowMs = Date.now()) {
  const seconds = Number(value);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (options.some(option => option.seconds === seconds)) return seconds;
  if (options.some(option => option.seconds === jsDebugGraphDefaultRangeSeconds)) return jsDebugGraphDefaultRangeSeconds;
  return options[0]?.seconds || jsDebugGraphDefaultRangeSeconds;
}

function activeJsDebugGraphRangeSeconds(nowMs = Date.now()) {
  jsDebugGraphRangeSeconds = normalizedJsDebugGraphRange(jsDebugGraphRangeSeconds, nowMs);
  return jsDebugGraphRangeSeconds;
}

function loadJsDebugStatsUiPreferences() {
  if (jsDebugStatsUiPreferencesLoaded) return;
  jsDebugStatsUiPreferencesLoaded = true;
  let saved = {};
  try {
    saved = JSON.parse(window.localStorage?.getItem(jsDebugStatsUiPreferencesStorageKey) || '{}');
  } catch (_) {
  }
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {};
  jsDebugSubTab = normalizedJsDebugSubTab(saved.subTab);
  jsDebugGraphRangeSeconds = jsDebugGraphRangeOptions.some(option => option.seconds === Number(saved.rangeSeconds))
    ? Number(saved.rangeSeconds)
    : jsDebugGraphDefaultRangeSeconds;
  jsDebugGraphResolutionOverrideSeconds = Math.max(0, Number(saved.resolutionOverrideSeconds) || 0);
  jsDebugGraphChartLayout = Math.max(0, Math.min(4, Math.round(Number(saved.chartLayout) || 0)));
  const hidden = new Set(jsDebugGraphDefaultHiddenChartKeys);
  const visible = new Set(Array.isArray(saved.visibleCharts) ? saved.visibleCharts.map(value => String(value || '')) : []);
  for (const key of visible) hidden.delete(key);
  for (const key of Array.isArray(saved.hiddenCharts) ? saved.hiddenCharts : []) hidden.add(String(key || ''));
  jsDebugGraphHiddenCharts = hidden;
  jsDebugGraphVisibleCharts = visible;
}

function saveJsDebugStatsUiPreferences() {
  if (!jsDebugStatsUiPreferencesLoaded) return;
  try {
    window.localStorage?.setItem(jsDebugStatsUiPreferencesStorageKey, JSON.stringify({
      subTab: jsDebugSubTab,
      rangeSeconds: jsDebugGraphRangeSeconds,
      resolutionOverrideSeconds: jsDebugGraphResolutionOverrideSeconds,
      chartLayout: jsDebugGraphChartLayout,
      hiddenCharts: [...debugGraphHiddenChartKeys()].sort(),
      visibleCharts: [...(jsDebugGraphVisibleCharts instanceof Set ? jsDebugGraphVisibleCharts : [])].sort(),
    }));
  } catch (_) {
  }
}

function debugGraphHiddenChartKeys() {
  loadJsDebugStatsUiPreferences();
  if (!(jsDebugGraphHiddenCharts instanceof Set)) jsDebugGraphHiddenCharts = new Set();
  if (!(jsDebugGraphVisibleCharts instanceof Set)) jsDebugGraphVisibleCharts = new Set();
  return jsDebugGraphHiddenCharts;
}

function debugGraphChartVisible(key) {
  return !debugGraphHiddenChartKeys().has(String(key || ''));
}

function setDebugGraphChartVisible(key, visible) {
  const chartKey = String(key || '');
  if (!chartKey) return;
  const hidden = debugGraphHiddenChartKeys();
  if (visible) {
    hidden.delete(chartKey);
    jsDebugGraphVisibleCharts.add(chartKey);
  } else {
    hidden.add(chartKey);
    jsDebugGraphVisibleCharts.delete(chartKey);
  }
  saveJsDebugStatsUiPreferences();
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) refreshDebugGraphElement(graph, {force: true});
}

function jsDebugGraphRangeOptionIndex(rangeSeconds = jsDebugGraphRangeSeconds, nowMs = Date.now()) {
  const options = debugGraphAvailableRangeOptions(nowMs);
  const normalized = normalizedJsDebugGraphRange(rangeSeconds, nowMs);
  return Math.max(0, options.findIndex(option => option.seconds === normalized));
}

function jsDebugGraphRangeLabel(seconds = jsDebugGraphRangeSeconds, nowMs = Date.now()) {
  const options = debugGraphAvailableRangeOptions(nowMs);
  const normalized = normalizedJsDebugGraphRange(seconds, nowMs);
  return options.find(option => option.seconds === normalized)?.label || `${normalized}s`;
}

function jsDebugHistoryReadinessBusy(state = jsDebugHistoryReadiness) {
  return jsDebugHistoryLoadingPhases.has(String(state?.phase || ''));
}

function jsDebugHistoryReadinessSnapshot() {
  const state = jsDebugHistoryReadiness;
  return {
    phase: state.phase,
    requestedRangeSeconds: state.requestedRangeSeconds,
    targetStartSeconds: state.targetStartSeconds,
    targetEndSeconds: state.targetEndSeconds,
    requestedStartSeconds: state.requestedStartSeconds,
    requestedEndSeconds: state.requestedEndSeconds,
    requestedResolutionSeconds: state.requestedResolutionSeconds,
    loadedStartSeconds: state.loadedStartSeconds,
    loadedEndSeconds: state.loadedEndSeconds,
    resolutionSeconds: state.resolutionSeconds,
    coverageIntervals: state.coverageIntervals.map(interval => ({...interval})),
    attemptCount: state.attemptCount,
    error: state.error,
    generation: state.generation,
    overlayVisible: state.overlayVisible,
    busy: jsDebugHistoryReadinessBusy(state),
  };
}

function clearJsDebugHistoryOverlayTimer() {
  if (jsDebugHistoryReadiness.overlayTimer !== null && typeof clearTimeout === 'function') {
    clearTimeout(jsDebugHistoryReadiness.overlayTimer);
  }
  jsDebugHistoryReadiness.overlayTimer = null;
}

function syncJsDebugHistoryReadinessSurfaces() {
  const state = jsDebugHistoryReadiness;
  const busy = jsDebugHistoryReadinessBusy(state);
  const content = debugGraphHistoryOverlayContentHtml(state);
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    graph.setAttribute('aria-busy', busy ? 'true' : 'false');
    graph.dataset.jsDebugHistoryState = state.phase;
    let overlay = graph.querySelector('[data-js-debug-history-overlay]');
    if (!overlay && (busy || state.phase === 'error')) {
      refreshDebugGraphElement(graph, {force: true});
      overlay = graph.querySelector('[data-js-debug-history-overlay]');
    }
    if (!overlay) continue;
    overlay.hidden = state.overlayVisible !== true;
    if (overlay.innerHTML !== content) overlay.innerHTML = content;
  }
}

function setJsDebugHistoryReadiness(phase, updates = {}) {
  const nextPhase = String(phase || 'idle');
  if (!jsDebugHistoryReadinessPhases.includes(nextPhase)) throw new Error(`unknown YO!stats history state: ${nextPhase}`);
  const state = jsDebugHistoryReadiness;
  const previousPhase = state.phase;
  const wasBusy = jsDebugHistoryReadinessBusy(state);
  const previousStartedAt = Number(state.loadingStartedAtMs) || 0;
  clearJsDebugHistoryOverlayTimer();
  for (const field of ['requestedRangeSeconds', 'targetStartSeconds', 'targetEndSeconds', 'requestedStartSeconds', 'requestedEndSeconds', 'requestedResolutionSeconds', 'loadedStartSeconds', 'loadedEndSeconds', 'resolutionSeconds', 'coverageIntervals', 'attemptCount', 'error', 'generation', 'loadingStartedAtMs']) {
    if (Object.prototype.hasOwnProperty.call(updates, field)) state[field] = updates[field];
  }
  state.phase = nextPhase;
  const busy = jsDebugHistoryReadinessBusy(state);
  state.overlayVisible = nextPhase === 'loading-initial' || nextPhase === 'retrying' || nextPhase === 'error';
  if (nextPhase === 'loading-older' && typeof setTimeout === 'function') {
    const generation = state.generation;
    state.overlayTimer = setTimeout(() => {
      state.overlayTimer = null;
      if (state.phase !== 'loading-older' || state.generation !== generation) return;
      state.overlayVisible = true;
      syncJsDebugHistoryReadinessSurfaces();
    }, jsDebugHistoryOlderOverlayDelayMs);
  }
  if (wasBusy && !busy) {
    recordClientPerfCounter('statsHistoryLoading', performanceNow() - previousStartedAt, {state: nextPhase, previousState: previousPhase});
    state.loadingStartedAtMs = 0;
  }
  syncJsDebugHistoryReadinessSurfaces();
  return jsDebugHistoryReadinessSnapshot();
}

function beginJsDebugHistoryReadiness(requestedStartSeconds, {requestedEndSeconds = 0, targetStartSeconds = requestedStartSeconds, targetEndSeconds = requestedEndSeconds, requestedResolutionSeconds = 1, retry = false} = {}) {
  const state = jsDebugHistoryReadiness;
  const generation = Number(state.generation || 0) + 1;
  const phase = retry ? 'retrying' : (Number(state.loadedStartSeconds) > 0 ? 'loading-older' : 'loading-initial');
  return setJsDebugHistoryReadiness(phase, {
    requestedRangeSeconds: jsDebugGraphRangeSeconds,
    targetStartSeconds: Math.max(0, Math.floor(Number(targetStartSeconds) || 0)),
    targetEndSeconds: Math.max(0, Math.ceil(Number(targetEndSeconds) || 0)),
    requestedStartSeconds: Math.max(0, Math.floor(Number(requestedStartSeconds) || 0)),
    requestedEndSeconds: Math.max(0, Math.floor(Number(requestedEndSeconds) || 0)),
    requestedResolutionSeconds: Math.max(1, Math.floor(Number(requestedResolutionSeconds) || 1)),
    attemptCount: retry ? Math.max(1, Number(state.attemptCount) + 1) : 1,
    error: '',
    generation,
    loadingStartedAtMs: performanceNow(),
  });
}

function jsDebugHistoryRequestIsCurrent(generation, requestedRangeSeconds, requestedStartSeconds) {
  const state = jsDebugHistoryReadiness;
  return Number(state.generation) === Number(generation)
    && Number(state.requestedRangeSeconds) === Number(requestedRangeSeconds)
    && Number(state.requestedStartSeconds) === Number(requestedStartSeconds);
}

function normalizedJsDebugHistoryCoverage(history = {}) {
  const raw = history?.coverage;
  if (!raw || typeof raw !== 'object') return null;
  const coverage = {
    mode: raw.mode === 'older' ? 'older' : 'live',
    requestedStart: Number(raw.requested_start),
    requestedEnd: Number(raw.requested_end),
    coveredStart: Number(raw.covered_start),
    coveredEnd: Number(raw.covered_end),
    resolutionSeconds: Number(raw.resolution_seconds),
    sourceResolutionSeconds: Number(raw.source_resolution_seconds),
    complete: raw.complete === true,
    hasMoreOlder: raw.has_more_older === true,
    nextOlderEnd: Number(raw.next_older_end),
  };
  if (!Number.isFinite(coverage.coveredStart) || !Number.isFinite(coverage.coveredEnd) || coverage.coveredEnd < coverage.coveredStart) return null;
  if (!Number.isFinite(coverage.resolutionSeconds) || coverage.resolutionSeconds <= 0) coverage.resolutionSeconds = 1;
  if (!Number.isFinite(coverage.sourceResolutionSeconds) || coverage.sourceResolutionSeconds <= 0) coverage.sourceResolutionSeconds = 0;
  return coverage;
}

function mergeJsDebugHistoryCoverageIntervals(intervals) {
  const grouped = new Map();
  for (const interval of intervals || []) {
    const resolution = Number(interval?.resolutionSeconds);
    const sourceResolution = Number(interval?.sourceResolutionSeconds) || 0;
    const start = Number(interval?.startSeconds);
    const end = Number(interval?.endSeconds);
    if (!Number.isFinite(resolution) || resolution <= 0 || !Number.isFinite(start) || end <= start) continue;
    const key = `${resolution}:${sourceResolution}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push({startSeconds: start, endSeconds: end, resolutionSeconds: resolution, sourceResolutionSeconds: sourceResolution});
  }
  const output = [];
  for (const [resolution, items] of grouped.entries()) {
    items.sort((left, right) => left.startSeconds - right.startSeconds || right.endSeconds - left.endSeconds);
    for (const item of items) {
      const previous = output.at(-1);
      if (previous?.resolutionSeconds === resolution && previous.sourceResolutionSeconds === sourceResolution && item.startSeconds <= previous.endSeconds) {
        previous.endSeconds = Math.max(previous.endSeconds, item.endSeconds);
      } else {
        output.push({...item});
      }
    }
  }
  return output;
}

function applyJsDebugHistoryCoverage(coverage) {
  if (!coverage) return jsDebugHistoryReadinessSnapshot();
  const state = jsDebugHistoryReadiness;
  if (coverage.coveredStart > 0) {
    state.loadedStartSeconds = Number(state.loadedStartSeconds) > 0
      ? Math.min(Number(state.loadedStartSeconds), coverage.coveredStart)
      : coverage.coveredStart;
  }
  if (coverage.coveredEnd > 0) state.loadedEndSeconds = Math.max(Number(state.loadedEndSeconds) || 0, coverage.coveredEnd);
  state.resolutionSeconds = coverage.resolutionSeconds;
  const satisfiedStart = coverage.hasMoreOlder
    ? coverage.coveredStart
    : Math.max(0, Number(state.targetStartSeconds) || coverage.requestedStart || coverage.coveredStart);
  const satisfiedEnd = coverage.mode === 'live'
    ? Infinity
    : Math.max(satisfiedStart, coverage.coveredEnd, Number(state.targetEndSeconds) || coverage.requestedEnd || 0);
  if (satisfiedEnd > satisfiedStart) {
    state.coverageIntervals = mergeJsDebugHistoryCoverageIntervals([
      ...state.coverageIntervals,
      {startSeconds: satisfiedStart, endSeconds: satisfiedEnd, resolutionSeconds: coverage.resolutionSeconds, sourceResolutionSeconds: coverage.sourceResolutionSeconds},
    ]);
  }
  if (coverage.hasMoreOlder && Number.isFinite(coverage.nextOlderEnd)) {
    state.requestedEndSeconds = coverage.nextOlderEnd;
  }
  return jsDebugHistoryReadinessSnapshot();
}

function jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, maxResolutionSeconds) {
  const intervals = jsDebugHistoryReadiness.coverageIntervals
    .filter(interval => Number(interval.resolutionSeconds) <= Math.max(maxResolutionSeconds, Number(interval.sourceResolutionSeconds) || 0))
    .sort((left, right) => Number(left.startSeconds) - Number(right.startSeconds) || Number(right.endSeconds) - Number(left.endSeconds));
  let cursor = startSeconds;
  for (const interval of intervals) {
    const intervalStart = Number(interval.startSeconds);
    const intervalEnd = Number(interval.endSeconds);
    const sourceResolution = Math.max(0, Number(interval.sourceResolutionSeconds) || 0);
    if (!Number.isFinite(intervalStart) || intervalEnd <= cursor) continue;
    if (intervalStart > cursor) return false;
    if (Number(interval.resolutionSeconds) > Math.max(maxResolutionSeconds, sourceResolution)) return false;
    cursor = Math.max(cursor, intervalEnd);
    if (cursor >= endSeconds) return true;
  }
  return false;
}

function jsDebugHistoryCoverageResolutionForRange(startSeconds, endSeconds) {
  const resolutions = [...new Set(jsDebugHistoryReadiness.coverageIntervals.map(interval => Number(interval.resolutionSeconds)))]
    .filter(resolution => Number.isFinite(resolution) && resolution > 0)
    .sort((left, right) => left - right);
  return resolutions.find(resolution => jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, resolution)) ?? Infinity;
}

function jsDebugHistoryCoverageNeedsRefresh(startSeconds, endSeconds, resolutionSeconds) {
  if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds) return true;
  return !jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, resolutionSeconds);
}

function jsDebugRequestedHistoryResolutionSeconds() {
  // The server chooses the coarsest retained source tier for the domain. Asking
  // for the finest resolution lets a later zoom recover finer retained history.
  return 1;
}

function jsDebugHistoryCoverageResolutionSeconds(startSeconds, requestedResolutionSeconds, nowMs = Date.now()) {
  const retainedResolutionSeconds = debugGraphBucketDurationForTime(Math.max(0, Number(startSeconds) || 0) * 1000, nowMs) / 1000;
  return Math.max(1, Number(requestedResolutionSeconds) || 0, retainedResolutionSeconds);
}

function jsDebugHistoryRequestWindow(targetStartSeconds, targetEndSeconds, resolutionSeconds) {
  const existingResolution = jsDebugHistoryCoverageResolutionForRange(targetStartSeconds, targetEndSeconds);
  if (Number.isFinite(existingResolution) && resolutionSeconds < existingResolution) {
    return {
      startSeconds: Math.max(0, Math.floor(targetStartSeconds / existingResolution) * existingResolution),
      endSeconds: Math.ceil(targetEndSeconds / existingResolution) * existingResolution,
    };
  }
  const loadedStart = Number(jsDebugHistoryReadiness.loadedStartSeconds) || 0;
  return {
    startSeconds: targetStartSeconds,
    endSeconds: loadedStart > targetStartSeconds ? loadedStart : 0,
  };
}

function resetJsDebugHistoryReadiness() {
  return setJsDebugHistoryReadiness('idle', {
    requestedRangeSeconds: jsDebugGraphRangeSeconds,
    targetStartSeconds: 0,
    targetEndSeconds: 0,
    requestedStartSeconds: 0,
    requestedEndSeconds: 0,
    requestedResolutionSeconds: 1,
    loadedStartSeconds: 0,
    loadedEndSeconds: 0,
    resolutionSeconds: 0,
    coverageIntervals: [],
    attemptCount: 0,
    error: '',
    generation: Number(jsDebugHistoryReadiness.generation || 0) + 1,
    loadingStartedAtMs: 0,
  });
}

function debugGraphZoomDomainValid(domain = jsDebugGraphZoomDomain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs - startMs >= 1000;
}

function clearDebugGraphZoom({render = true} = {}) {
  jsDebugGraphZoomDomain = null;
  jsDebugGraphSelectionState = null;
  if (!render) return;
  if (requestJsDebugHistoryForCurrentDomain()) return;
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force: true});
  }
}

function debugEventCounts() {
  const apiCalls = jsDebugEvents.filter(event => event.type === 'api').length;
  const sseEvents = jsDebugEvents.filter(event => event.type === 'sse').length;
  const errors = jsDebugEvents.filter(event => event.type === 'error' || event.type === 'unhandledrejection' || event.error).length;
  const apiRequestBytes = jsDebugEvents.reduce((total, event) => total + (event.type === 'api' && Number.isFinite(event.requestBytes) ? event.requestBytes : 0), 0);
  const apiResponseBytes = jsDebugEvents.reduce((total, event) => total + (event.type === 'api' && Number.isFinite(event.responseBytes) ? event.responseBytes : 0), 0);
  const sseBytes = jsDebugEvents.reduce((total, event) => total + (event.type === 'sse' && Number.isFinite(event.frameBytes) ? event.frameBytes : 0), 0);
  return {apiCalls, sseEvents, errors, apiRequestBytes, apiResponseBytes, sseBytes};
}

function debugMetaText() {
  return t('debug.meta', {count: jsDebugEvents.length});
}

function debugStatHtml(label, value, key = '') {
  const data = key ? ` data-js-debug-stat="${esc(key)}"` : '';
  return `<div class="js-debug-stat"><span>${esc(label)}</span><strong${data}>${esc(value)}</strong></div>`;
}

function debugSubTabButtonHtml(tab, label) {
  const active = normalizedJsDebugSubTab(tab) === jsDebugSubTab;
  return `<button type="button" class="js-debug-subtab${active ? ' active' : ''}" role="tab" data-js-debug-subtab="${esc(tab)}" aria-selected="${active ? 'true' : 'false'}"><span class="session-button-dir">${esc(label)}</span></button>`;
}

function debugSubTabsHtml() {
  loadJsDebugStatsUiPreferences();
  return `<div class="js-debug-subtabs" role="tablist" aria-label="${esc(t('tab.debug'))}">
    ${debugSubTabButtonHtml('graph', t('debug.tab.graph'))}
    ${debugSubTabButtonHtml('events', t('debug.tab.events'))}
  </div>`;
}

function debugSubViewAttrs(tab) {
  const active = normalizedJsDebugSubTab(tab) === jsDebugSubTab;
  return `data-js-debug-subview="${esc(tab)}"${active ? '' : ' hidden'}`;
}

function debugTimeText(value) {
  const match = String(value || '').match(/T(\d\d:\d\d:\d\d)/);
  return match ? match[1] : String(value || '');
}

function debugEventTypeLabel(type) {
  if (type === 'api') return 'API';
  if (type === 'sse') return 'SSE';
  if (type === 'unhandledrejection') return 'Promise';
  if (type === 'error') return 'Error';
  return String(type || 'Event');
}

function debugEventStatusText(event) {
  if (event.error) return 'error';
  if (Number.isFinite(event.status)) return `HTTP ${event.status}`;
  if (typeof event.ok === 'boolean') return event.ok ? 'ok' : 'not ok';
  return '';
}

function debugEventDetailText(event) {
  if (event.type === 'api') return `${event.method || 'GET'} ${event.url || ''}`.trim();
  if (event.type === 'sse') return [
    event.eventType || 'event',
    event.trigger ? `trigger=${event.trigger}` : '',
    event.cache ? `cache=${event.cache}` : '',
    debugFilesystemEventSummaryText(event),
    event.key ? `key=${event.key}` : '',
  ].filter(Boolean).join(' ');
  return event.message || event.reason || event.source || '';
}

function debugCountToken(prefix, value, {includeZero = false} = {}) {
  const count = Number(value);
  if (!Number.isFinite(count)) return '';
  if (!includeZero && count === 0) return '';
  return `${prefix}${count}`;
}

function debugFilesystemEventSummaryText(event) {
  if (event.type !== 'sse' || event.eventType !== 'fs_changed') return '';
  const change = event.changeSummary && typeof event.changeSummary === 'object' ? event.changeSummary : {};
  const listing = event.listingSummary && typeof event.listingSummary === 'object' ? event.listingSummary : {};
  const parts = [];
  const rootsChanged = debugCountToken('roots:', change.roots_changed);
  const entriesAdded = debugCountToken('+', change.entries_added);
  const entriesRemoved = debugCountToken('-', change.entries_removed);
  const entriesModified = debugCountToken('~', change.entries_modified);
  const entryParts = [entriesAdded, entriesRemoved, entriesModified].filter(Boolean).join(' ');
  if (rootsChanged || entryParts) parts.push(`changed=${[rootsChanged, entryParts].filter(Boolean).join(' ')}`);
  const filesAdded = debugCountToken('+', change.files_added);
  const filesRemoved = debugCountToken('-', change.files_removed);
  const filesModified = debugCountToken('~', change.files_modified);
  const fileParts = [filesAdded, filesRemoved, filesModified].filter(Boolean).join(' ');
  if (fileParts) parts.push(`files=${fileParts}`);
  const dirsAdded = debugCountToken('+', change.dirs_added);
  const dirsRemoved = debugCountToken('-', change.dirs_removed);
  const dirsModified = debugCountToken('~', change.dirs_modified);
  const dirParts = [dirsAdded, dirsRemoved, dirsModified].filter(Boolean).join(' ');
  if (dirParts) parts.push(`dirs=${dirParts}`);
  const listedEntries = debugCountToken('listed=', listing.entries_listed, {includeZero: true});
  const listedRoots = debugCountToken('/', listing.roots_listed, {includeZero: true});
  if (listedEntries) parts.push(`${listedEntries}${listedRoots}`);
  const rootErrors = debugCountToken('errors=', listing.roots_error);
  if (rootErrors) parts.push(rootErrors);
  return parts.length ? `fs=${parts.join(' ')}` : '';
}

function debugPhaseTimingText(event) {
  const timings = event.phaseTimings && typeof event.phaseTimings === 'object' ? event.phaseTimings : null;
  if (!timings) return '';
  const rows = Object.entries(timings)
    .filter(([_key, value]) => Number.isFinite(Number(value)))
    .map(([key, value]) => `${key}=${Number(value).toFixed(1)}ms`);
  return rows.length ? `timings=${rows.join(',')}` : '';
}

function debugEventMetaText(event) {
  return [
    debugTimeText(event.ts),
    Number.isFinite(event.durationMs) ? `${event.durationMs} ms` : '',
    Number.isFinite(event.computeMs) ? `server ${event.computeMs} ms` : '',
    Number.isFinite(event.receiveLatencyMs) ? `receive ${event.receiveLatencyMs} ms` : '',
    Number.isFinite(event.frameBytes) ? `rx ${event.frameBytes} B` : '',
    Number.isFinite(event.bytes) && event.bytes !== event.frameBytes ? `data ${event.bytes} B` : '',
    Number.isFinite(event.responseBytes) ? `${event.responseBytes} B rx` : '',
    debugPhaseTimingText(event),
    debugEventStatusText(event),
    event.source ? `source: ${event.source}` : '',
    event.line ? `line ${event.line}${event.column ? `:${event.column}` : ''}` : '',
  ].filter(Boolean).join(' | ');
}

function debugEventLineText(event) {
  const status = debugEventStatusText(event);
  const durationMs = Number.isFinite(event.durationMs)
    ? event.durationMs
    : (event.type === 'sse' && Number.isFinite(event.receiveLatencyMs) ? event.receiveLatencyMs : NaN);
  const duration = Number.isFinite(durationMs) ? `${durationMs}ms` : '';
  const sseMeta = event.type === 'sse'
    ? [
      Number.isFinite(event.frameBytes) ? `rx=${event.frameBytes}B` : '',
      debugPhaseTimingText(event),
    ].filter(Boolean).join(' ')
    : '';
  const location = event.source ? `${event.source}${event.line ? `:${event.line}${event.column ? `:${event.column}` : ''}` : ''}` : '';
  return [
    debugTimeText(event.ts),
    debugEventTypeLabel(event.type).padEnd(7),
    status.padEnd(8),
    duration.padStart(8),
    sseMeta,
    debugEventDetailText(event) || t('common.eventLabel'),
    location,
  ].filter(Boolean).join(' ');
}

function debugApiSummaryKey(url) {
  const value = String(url || '');
  try {
    const parsed = new URL(value, window.location.origin);
    return parsed.pathname || value;
  } catch (_) {
    return value.split('?')[0] || value;
  }
}

function debugApiSummaryRows(limit = 6) {
  const summaries = new Map();
  for (const event of jsDebugEvents) {
    if (event.type !== 'api' || !Number.isFinite(event.durationMs)) continue;
    const key = `${event.method || 'GET'} ${debugApiSummaryKey(event.url)}`;
    const item = summaries.get(key) || {key, count: 0, total: 0, max: 0, bytes: 0, lastStatus: ''};
    item.count += 1;
    item.total += event.durationMs;
    item.max = Math.max(item.max, event.durationMs);
    item.bytes += Number.isFinite(event.responseBytes) ? event.responseBytes : 0;
    item.lastStatus = debugEventStatusText(event);
    summaries.set(key, item);
  }
  return [...summaries.values()]
    .sort((a, b) => (b.max - a.max) || (b.total - a.total) || a.key.localeCompare(b.key))
    .slice(0, limit)
    .map(item => {
      const avg = item.count ? item.total / item.count : 0;
      return `${item.key.padEnd(28)} max=${item.max.toFixed(1).padStart(7)}ms avg=${avg.toFixed(1).padStart(7)}ms count=${String(item.count).padStart(3)} rx=${String(item.bytes).padStart(7)}B ${item.lastStatus}`.trimEnd();
    });
}

function debugSseSummaryRows(limit = 6) {
  return jsDebugEvents
    .filter(event => event.type === 'sse' && Number.isFinite(event.computeMs))
    .sort((a, b) => (b.computeMs - a.computeMs) || String(a.eventType || '').localeCompare(String(b.eventType || '')))
    .slice(0, limit)
    .map(event => `${String(event.eventType || 'event').padEnd(28)} server=${event.computeMs.toFixed(1).padStart(7)}ms rx=${String(event.frameBytes || event.bytes || 0).padStart(7)}B ${event.trigger || ''}`.trimEnd());
}

function debugSseLatencySummaryRows(limit = 6) {
  const summaries = new Map();
  for (const event of jsDebugEvents) {
    if (event.type !== 'sse' || !Number.isFinite(event.receiveLatencyMs)) continue;
    const key = String(event.eventType || 'event');
    const item = summaries.get(key) || {key, count: 0, total: 0, max: 0, bytes: 0};
    item.count += 1;
    item.total += event.receiveLatencyMs;
    item.max = Math.max(item.max, event.receiveLatencyMs);
    item.bytes += Number.isFinite(event.frameBytes) ? event.frameBytes : Number(event.bytes || 0);
    summaries.set(key, item);
  }
  return [...summaries.values()]
    .sort((a, b) => (b.max - a.max) || (b.total - a.total) || a.key.localeCompare(b.key))
    .slice(0, limit)
    .map(item => {
      const avg = item.count ? item.total / item.count : 0;
      return `${item.key.padEnd(28)} max=${item.max.toFixed(1).padStart(7)}ms avg=${avg.toFixed(1).padStart(7)}ms count=${String(item.count).padStart(3)} rx=${String(item.bytes).padStart(7)}B`;
    });
}

function debugGraphNewBucket(startMs, durationMs) {
  return {
    startMs,
    durationMs,
    apiCount: 0,
    sseCount: 0,
    latencyTotalMs: 0,
    latencyCount: 0,
    bandwidthBytes: 0,
    heartbeatCount: 0,
    disconnectedMs: 0,
    cpuTotalPercent: 0,
    cpuCount: 0,
    systemCpuTotalPercent: 0,
    systemCpuCount: 0,
    askAgentTotal: 0,
    runAgentTotal: 0,
    transitionAgentTotal: 0,
    idleAgentTotal: 0,
    activeAgentTotal: 0,
    inactiveAgentTotal: 0,
    agentActivitySamples: 0,
    agentStatusSequence: -1,
    tokensPerAgentTotal: 0,
    agentTokenSamples: 0,
    agentTokenRates: new Map(),
    hostMetrics: debugGraphNewHostMetrics(),
    clients: new Map(),
    servers: new Map(),
  };
}

function debugGraphNewHostMetrics() {
  return {
    systemMemoryUsedTotalBytes: 0,
    systemMemoryCapacityTotalBytes: 0,
    systemMemoryCount: 0,
    cpuProcesses: new Map(),
    memoryProcesses: new Map(),
    gpuUtilProcesses: new Map(),
    gpuMemoryProcesses: new Map(),
    gpuDevices: new Map(),
  };
}

function debugGraphNewClientBucket() {
  return {
    apiCount: 0,
    sseCount: 0,
    latencyTotalMs: 0,
    latencyCount: 0,
    bandwidthBytes: 0,
    heartbeatCount: 0,
    disconnectedMs: 0,
  };
}

function debugGraphBucketHasData(bucket) {
  return Boolean(
    Number(bucket?.apiCount || 0)
    || Number(bucket?.sseCount || 0)
    || Number(bucket?.latencyCount || 0)
    || Number(bucket?.bandwidthBytes || 0)
    || Number(bucket?.disconnectedMs || 0)
    || Number(bucket?.cpuCount || 0)
    || Number(bucket?.systemCpuCount || 0)
    || Number(bucket?.agentActivitySamples || 0)
    || Number(bucket?.agentTokenSamples || 0)
    || Number(bucket?.agentTokenRates?.size || 0)
    || Number(bucket?.clients?.size || 0)
    || Number(bucket?.servers?.size || 0)
  );
}

function debugGraphBucket(map, startMs, durationMs) {
  const key = `${startMs}:${durationMs}`;
  let bucket = map.get(key);
  if (!bucket) {
    bucket = debugGraphNewBucket(startMs, durationMs);
    map.set(key, bucket);
  }
  bucket.durationMs = Math.max(bucket.durationMs || durationMs, durationMs);
  return bucket;
}

function debugGraphEventTimeMs(event) {
  const parsed = Date.parse(event?.ts || '');
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function debugGraphLatencyMs(event) {
  if (event.type === 'api' && Number.isFinite(event.durationMs)) return Number(event.durationMs);
  if (event.type === 'sse' && Number.isFinite(event.receiveLatencyMs)) return Number(event.receiveLatencyMs);
  if (event.type === 'sse' && Number.isFinite(event.computeMs)) return Number(event.computeMs);
  return NaN;
}

function debugGraphBucketDurationForTime(timeMs, nowMs = Date.now()) {
  const ageMs = Math.max(0, nowMs - timeMs);
  return (jsDebugGraphTiers.find(tier => ageMs <= tier.maxAgeMs) || jsDebugGraphTiers[jsDebugGraphTiers.length - 1]).bucketMs;
}

function debugGraphBucketForTime(timeMs, nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  if (!Number.isFinite(timeMs) || timeMs < retentionCutoff) return null;
  const durationMs = debugGraphBucketDurationForTime(timeMs, nowMs);
  const startMs = Math.floor(timeMs / durationMs) * durationMs;
  const map = durationMs < jsDebugGraphMiddleBucketMs ? jsDebugGraphRawBuckets : jsDebugGraphRollupBuckets;
  return debugGraphBucket(map, startMs, durationMs);
}

function debugGraphServerBucketRefForTime(timeMs, nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  if (!Number.isFinite(timeMs) || timeMs < retentionCutoff) return null;
  const durationMs = debugGraphBucketDurationForTime(timeMs, nowMs);
  return {
    startMs: Math.floor(timeMs / durationMs) * durationMs,
    durationMs,
  };
}

function jsDebugStatsRandomHex(bytes = 12) {
  const count = Math.max(1, Math.floor(Number(bytes) || 1));
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const values = new Uint8Array(count);
    crypto.getRandomValues(values);
    return [...values].map(value => value.toString(16).padStart(2, '0')).join('');
  }
  return Array.from({length: count}, () => Math.floor(Math.random() * 256).toString(16).padStart(2, '0')).join('');
}

function jsDebugStatsStorageGet(key) {
  try {
    return window.sessionStorage?.getItem(key) || '';
  } catch (_) {
    return '';
  }
}

function jsDebugStatsStorageSet(key, value) {
  try {
    window.sessionStorage?.setItem(key, String(value || ''));
  } catch (_) {
  }
}

function jsDebugStatsStorageRemove(key) {
  try {
    window.sessionStorage?.removeItem(key);
  } catch (_) {
  }
}

function jsDebugStatsClientIdForRequest() {
  if (jsDebugStatsClientId) return jsDebugStatsClientId;
  const stored = jsDebugStatsStorageGet(jsDebugStatsClientStorageKey).trim();
  jsDebugStatsClientId = stored || `client-${Date.now().toString(36)}-${jsDebugStatsRandomHex(8)}`;
  jsDebugStatsStorageSet(jsDebugStatsClientStorageKey, jsDebugStatsClientId);
  return jsDebugStatsClientId;
}

function debugGraphAddBucketData(bucket, data = {}) {
  if (!bucket) return;
  bucket.apiCount += Number(data.apiCount || 0);
  bucket.sseCount += Number(data.sseCount || 0);
  const latencyMs = Number(data.latencyMs);
  if (Number.isFinite(latencyMs)) {
    bucket.latencyTotalMs += latencyMs;
    bucket.latencyCount += 1;
  }
  const bytes = Number(data.bandwidthBytes || 0);
  if (Number.isFinite(bytes) && bytes > 0) bucket.bandwidthBytes += bytes;
  const heartbeatCount = Number(data.heartbeatCount || 0);
  if (Number.isFinite(heartbeatCount) && heartbeatCount > 0) bucket.heartbeatCount += heartbeatCount;
  const disconnectedMs = Number(data.disconnectedMs || 0);
  if (Number.isFinite(disconnectedMs) && disconnectedMs > 0) bucket.disconnectedMs += disconnectedMs;
  const cpuPercent = Number(data.cpuPercent);
  if (Number.isFinite(cpuPercent)) {
    bucket.cpuTotalPercent += Math.max(0, cpuPercent);
    bucket.cpuCount += 1;
  }
  const systemCpuPercent = Number(data.systemCpuPercent);
  if (Number.isFinite(systemCpuPercent)) {
    bucket.systemCpuTotalPercent += Math.max(0, systemCpuPercent);
    bucket.systemCpuCount += 1;
  }
  const activeAgents = Number(data.activeAgents);
  const inactiveAgents = Number(data.inactiveAgents);
  const askAgents = Number(data.askAgents);
  const workingAgentsFromPayload = Number(data.runAgents);
  const transitionAgents = Number(data.transitionAgents);
  const idleAgents = Number(data.idleAgents);
  if (Number.isFinite(askAgents) || Number.isFinite(workingAgentsFromPayload) || Number.isFinite(transitionAgents) || Number.isFinite(idleAgents)) {
    const normalizedAskAgents = Number.isFinite(askAgents) ? askAgents : 0;
    const normalizedWorkingAgents = Number.isFinite(workingAgentsFromPayload) ? workingAgentsFromPayload : 0;
    const normalizedIdleAgents = Number.isFinite(idleAgents) ? idleAgents : (Number.isFinite(inactiveAgents) ? inactiveAgents : 0);
    const normalizedTransitionAgents = Number.isFinite(transitionAgents)
      ? Math.max(0, transitionAgents - (Number.isFinite(idleAgents) ? 0 : normalizedIdleAgents))
      : 0;
    bucket.askAgentTotal += Math.max(0, Number.isFinite(askAgents) ? askAgents : 0);
    bucket.runAgentTotal += Math.max(0, normalizedWorkingAgents);
    bucket.transitionAgentTotal += Math.max(0, normalizedTransitionAgents);
    bucket.idleAgentTotal += Math.max(0, normalizedIdleAgents);
    bucket.activeAgentTotal += Math.max(0, Number.isFinite(activeAgents) ? activeAgents : normalizedAskAgents + normalizedWorkingAgents + normalizedTransitionAgents);
    bucket.inactiveAgentTotal += Math.max(0, Number.isFinite(inactiveAgents) ? inactiveAgents : 0);
    bucket.agentActivitySamples += 1;
  } else if (Number.isFinite(activeAgents) && Number.isFinite(inactiveAgents)) {
    bucket.runAgentTotal += Math.max(0, activeAgents);
    bucket.idleAgentTotal += Math.max(0, inactiveAgents);
    bucket.activeAgentTotal += Math.max(0, activeAgents);
    bucket.inactiveAgentTotal += Math.max(0, inactiveAgents);
    bucket.agentActivitySamples += 1;
  }
  const tokensPerAgent = data.tokensPerAgent == null ? NaN : Number(data.tokensPerAgent);
  if (Number.isFinite(tokensPerAgent)) {
    bucket.tokensPerAgentTotal += Math.max(0, tokensPerAgent);
    bucket.agentTokenSamples += 1;
  }
  const agentTokenRates = data.agentTokenRates instanceof Map
    ? data.agentTokenRates
    : new Map(Array.isArray(data.agentTokenRates) ? data.agentTokenRates : []);
  for (const [key, item] of agentTokenRates.entries()) {
    const rate = Number(item?.rate ?? item?.value ?? item);
    if (!Number.isFinite(rate)) continue;
    const label = String(item?.label || key || '').trim() || String(key);
    const existing = bucket.agentTokenRates.get(String(key)) || {label, total: 0, samples: 0};
    existing.label = label;
    existing.total += Math.max(0, rate);
    existing.samples += 1;
    bucket.agentTokenRates.set(String(key), existing);
  }
}

function debugGraphServerDeltaKey(bucket) {
  if (!bucket) return '';
  return `${Math.floor(Number(bucket.startMs) || 0)}:${Math.floor(Number(bucket.durationMs) || 0)}`;
}

function debugGraphQueueServerDelta(bucket, data = {}) {
  if (!jsDebugCollectionEnabled || !bucket) return;
  const key = debugGraphServerDeltaKey(bucket);
  if (!key) return;
  let record = jsDebugGraphPendingServerBuckets.get(key);
  if (!record) {
    record = {
      start: Math.floor(Number(bucket.startMs) / 1000),
      duration: Math.max(1, Math.floor(Number(bucket.durationMs) / 1000)),
      api_count: 0,
      sse_count: 0,
      latency_total_ms: 0,
      latency_count: 0,
      bandwidth_bytes: 0,
      heartbeat_count: 0,
      disconnected_ms: 0,
      cpu_total_percent: 0,
      cpu_count: 0,
      system_cpu_total_percent: 0,
      system_cpu_count: 0,
    };
    jsDebugGraphPendingServerBuckets.set(key, record);
  }
  record.api_count += Number(data.apiCount || 0);
  record.sse_count += Number(data.sseCount || 0);
  const latencyMs = Number(data.latencyMs);
  if (Number.isFinite(latencyMs)) {
    record.latency_total_ms += latencyMs;
    record.latency_count += 1;
  }
  const bytes = Number(data.bandwidthBytes || 0);
  if (Number.isFinite(bytes) && bytes > 0) record.bandwidth_bytes += bytes;
  const heartbeatCount = Number(data.heartbeatCount || 0);
  if (Number.isFinite(heartbeatCount) && heartbeatCount > 0) record.heartbeat_count += heartbeatCount;
  const disconnectedMs = Number(data.disconnectedMs || 0);
  if (Number.isFinite(disconnectedMs) && disconnectedMs > 0) record.disconnected_ms += disconnectedMs;
  scheduleJsDebugStatsHistoryFlush();
}

function debugGraphMergeAgentTokenRates(target, source) {
  if (!(source?.agentTokenRates instanceof Map)) return;
  if (!(target.agentTokenRates instanceof Map)) target.agentTokenRates = new Map();
  for (const [key, item] of source.agentTokenRates.entries()) {
    const existing = target.agentTokenRates.get(String(key)) || {label: item?.label || String(key), total: 0, samples: 0, tokens: 0, seconds: 0};
    existing.label = item?.label || existing.label;
    existing.total += Number(item?.total || 0);
    existing.samples += Number(item?.samples || 0);
    existing.tokens += Number(item?.tokens || 0);
    existing.seconds += Number(item?.seconds || 0);
    target.agentTokenRates.set(String(key), existing);
  }
}

function debugGraphMergeBucket(target, source) {
  target.apiCount += source.apiCount || 0;
  target.sseCount += source.sseCount || 0;
  target.latencyTotalMs += source.latencyTotalMs || 0;
  target.latencyCount += source.latencyCount || 0;
  target.bandwidthBytes += source.bandwidthBytes || 0;
  target.disconnectedMs += source.disconnectedMs || 0;
  target.cpuTotalPercent += source.cpuTotalPercent || 0;
  target.cpuCount += source.cpuCount || 0;
  target.systemCpuTotalPercent += source.systemCpuTotalPercent || 0;
  target.systemCpuCount += source.systemCpuCount || 0;
  target.askAgentTotal += source.askAgentTotal || 0;
  target.runAgentTotal += source.runAgentTotal || 0;
  target.transitionAgentTotal += source.transitionAgentTotal || 0;
  target.idleAgentTotal += source.idleAgentTotal || 0;
  target.activeAgentTotal += source.activeAgentTotal || 0;
  target.inactiveAgentTotal += source.inactiveAgentTotal || 0;
  target.agentActivitySamples += source.agentActivitySamples || 0;
  target.agentStatusSequence = Math.max(Number(target.agentStatusSequence ?? -1), Number(source.agentStatusSequence ?? -1));
  target.tokensPerAgentTotal += source.tokensPerAgentTotal || 0;
  target.agentTokenSamples += source.agentTokenSamples || 0;
  debugGraphMergeAgentTokenRates(target, source);
  const sourceHost = source.hostMetrics;
  if (sourceHost) {
    const targetHost = target.hostMetrics || (target.hostMetrics = debugGraphNewHostMetrics());
    targetHost.systemMemoryUsedTotalBytes += Number(sourceHost.systemMemoryUsedTotalBytes || 0);
    targetHost.systemMemoryCapacityTotalBytes += Number(sourceHost.systemMemoryCapacityTotalBytes || 0);
    targetHost.systemMemoryCount += Number(sourceHost.systemMemoryCount || 0);
    for (const [targetMap, sourceMap, valueKey] of [
      [targetHost.cpuProcesses, sourceHost.cpuProcesses, 'totalPercent'],
      [targetHost.memoryProcesses, sourceHost.memoryProcesses, 'totalBytes'],
      [targetHost.gpuUtilProcesses, sourceHost.gpuUtilProcesses, 'totalPercent'],
      [targetHost.gpuMemoryProcesses, sourceHost.gpuMemoryProcesses, 'totalBytes'],
    ]) {
      if (!(sourceMap instanceof Map)) continue;
      for (const [key, sourceItem] of sourceMap.entries()) {
        const item = targetMap.get(key) || {label: sourceItem.label || key, [valueKey]: 0, samples: 0};
        item.label = sourceItem.label || item.label;
        item[valueKey] += Number(sourceItem[valueKey] || 0);
        item.samples += Number(sourceItem.samples || 0);
        targetMap.set(key, item);
      }
    }
    if (sourceHost.gpuDevices instanceof Map) {
      for (const [key, sourceItem] of sourceHost.gpuDevices.entries()) {
        const item = targetHost.gpuDevices.get(key) || {label: sourceItem.label || key, utilTotalPercent: 0, memoryUsedTotalBytes: 0, memoryCapacityTotalBytes: 0, samples: 0};
        item.label = sourceItem.label || item.label;
        item.utilTotalPercent += Number(sourceItem.utilTotalPercent || 0);
        item.memoryUsedTotalBytes += Number(sourceItem.memoryUsedTotalBytes || 0);
        item.memoryCapacityTotalBytes += Number(sourceItem.memoryCapacityTotalBytes || 0);
        item.samples += Number(sourceItem.samples || 0);
        targetHost.gpuDevices.set(key, item);
      }
    }
  }
  if (source.clients instanceof Map) {
    if (!(target.clients instanceof Map)) target.clients = new Map();
    for (const [clientId, sourceClient] of source.clients.entries()) {
      const targetClient = target.clients.get(clientId) || debugGraphNewClientBucket();
      targetClient.apiCount += Number(sourceClient.apiCount || 0);
      targetClient.sseCount += Number(sourceClient.sseCount || 0);
      targetClient.latencyTotalMs += Number(sourceClient.latencyTotalMs || 0);
      targetClient.latencyCount += Number(sourceClient.latencyCount || 0);
      targetClient.bandwidthBytes += Number(sourceClient.bandwidthBytes || 0);
      targetClient.disconnectedMs += Number(sourceClient.disconnectedMs || 0);
      target.clients.set(clientId, targetClient);
    }
  }
  if (source.servers instanceof Map) {
    if (!(target.servers instanceof Map)) target.servers = new Map();
    for (const [processId, sourceProcess] of source.servers.entries()) {
      const targetProcess = target.servers.get(processId) || {label: processId, cpuTotalPercent: 0, cpuCount: 0};
      targetProcess.label = sourceProcess.label || targetProcess.label;
      targetProcess.cpuTotalPercent += Number(sourceProcess.cpuTotalPercent || 0);
      targetProcess.cpuCount += Number(sourceProcess.cpuCount || 0);
      target.servers.set(processId, targetProcess);
    }
  }
}

function compactJsDebugGraphBuckets(nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  for (const buckets of [jsDebugGraphRawBuckets, jsDebugGraphRollupBuckets]) {
    for (const [key, bucket] of [...buckets.entries()]) {
      if (bucket.startMs < retentionCutoff) {
        buckets.delete(key);
        continue;
      }
      const targetDurationMs = debugGraphBucketDurationForTime(bucket.startMs, nowMs);
      if (bucket.durationMs >= targetDurationMs) continue;
      const targetStartMs = Math.floor(bucket.startMs / targetDurationMs) * targetDurationMs;
      const targetBuckets = targetDurationMs === jsDebugGraphRawBucketMs ? jsDebugGraphRawBuckets : jsDebugGraphRollupBuckets;
      const target = debugGraphBucket(targetBuckets, targetStartMs, targetDurationMs);
      debugGraphMergeBucket(target, bucket);
      buckets.delete(key);
    }
  }
  const refCutoff = nowMs - jsDebugGraphResponseRefRetentionMs;
  for (const [id, record] of [...jsDebugGraphEventRecords.entries()]) {
    if (Number(record?.lastSeenAt || 0) >= refCutoff) continue;
    jsDebugGraphEventRecords.delete(id);
  }
}

function recordJsDebugEventForGraph(event) {
  if (!jsDebugCollectionEnabled || !event || typeof event !== 'object') return;
  if (event.type !== 'api' && event.type !== 'sse') return;
  const nowMs = Date.now();
  const bucketRef = debugGraphServerBucketRefForTime(debugGraphEventTimeMs(event), nowMs);
  if (!bucketRef) return;
  const latencyMs = debugGraphLatencyMs(event);
  const requestBytes = event.type === 'api' && Number.isFinite(event.requestBytes) ? Number(event.requestBytes) : 0;
  const responseBytes = event.type === 'api' && Number.isFinite(event.responseBytes) ? Number(event.responseBytes) : 0;
  const sseBytes = event.type === 'sse'
    ? (Number.isFinite(event.frameBytes) ? Number(event.frameBytes) : Number(event.bytes || 0))
    : 0;
  const data = {
    apiCount: event.type === 'api' ? 1 : 0,
    sseCount: event.type === 'sse' ? 1 : 0,
    latencyMs,
    bandwidthBytes: requestBytes + responseBytes + sseBytes,
  };
  debugGraphAddBucketData(debugGraphBucketForTime(debugGraphEventTimeMs(event), nowMs), data);
  debugGraphQueueServerDelta(bucketRef, data);
  if (event.type === 'api' && Number.isFinite(event.id)) {
    jsDebugGraphEventRecords.set(event.id, {bucket: bucketRef, responseBytes, lastSeenAt: nowMs});
  }
  compactJsDebugGraphBuckets(nowMs);
}

function recordApiDebugResponseBytesForGraph(event, responseBytes) {
  if (!jsDebugCollectionEnabled || !event || !Number.isFinite(event.id)) return;
  const record = jsDebugGraphEventRecords.get(event.id);
  if (!record?.bucket) return;
  const nextBytes = Number(responseBytes);
  if (!Number.isFinite(nextBytes) || nextBytes < 0) return;
  const previousBytes = Number(record.responseBytes || 0);
  const delta = nextBytes - previousBytes;
  record.responseBytes = nextBytes;
  record.lastSeenAt = Date.now();
  if (delta === 0) return;
  debugGraphAddBucketData(debugGraphBucketForTime(Number(record.bucket.startMs), Date.now()), {bandwidthBytes: delta});
  debugGraphQueueServerDelta(record.bucket, {bandwidthBytes: delta});
}

function recordJsDebugDisconnectedSpan(startMs, endMs = Date.now()) {
  if (!jsDebugCollectionEnabled) return;
  const spanStart = Number(startMs);
  const spanEnd = Number(endMs);
  if (!Number.isFinite(spanStart) || !Number.isFinite(spanEnd) || spanEnd <= spanStart) return;
  const nowMs = Date.now();
  let cursor = Math.max(spanStart, nowMs - jsDebugGraphRetentionMs);
  const boundedEnd = Math.min(spanEnd, nowMs);
  while (cursor < boundedEnd) {
    const bucketRef = debugGraphServerBucketRefForTime(cursor, nowMs);
    if (!bucketRef) break;
    const bucketStart = Number(bucketRef.startMs) || cursor;
    const bucketEnd = bucketStart + Math.max(jsDebugGraphRawBucketMs, Number(bucketRef.durationMs) || jsDebugGraphRawBucketMs);
    const overlapStart = Math.max(cursor, bucketStart);
    const overlapEnd = Math.min(boundedEnd, bucketEnd);
    const disconnectedMs = Math.max(0, overlapEnd - overlapStart);
    if (disconnectedMs > 0) {
      const bucket = debugGraphBucketForTime(overlapStart, nowMs);
      debugGraphAddBucketData(bucket, {disconnectedMs});
      debugGraphQueueServerDelta(bucketRef, {disconnectedMs});
    }
    cursor = Math.max(overlapEnd, cursor + 1);
  }
  compactJsDebugGraphBuckets(nowMs);
  scheduleJsDebugPanelRefresh();
}

function recordJsDebugClientEventsConnectionState(connected) {
  const nextConnected = connected === true;
  if (jsDebugStatsClientConnected === nextConnected) return;
  jsDebugStatsClientConnected = nextConnected;
  if (typeof setBadConnectionCursorState === 'function') setBadConnectionCursorState(!nextConnected);
  const nowMs = Date.now();
  if (!nextConnected) {
    jsDebugStatsDisconnectStartedAtMs = nowMs;
    jsDebugStatsStorageSet(jsDebugStatsDisconnectedStorageKey, String(nowMs));
    const bucket = debugGraphBucketForTime(nowMs, nowMs);
    debugGraphAddBucketData(bucket, {disconnectedMs: 1});
    scheduleJsDebugPanelRefresh();
    return;
  }
  const storedStart = Number(jsDebugStatsStorageGet(jsDebugStatsDisconnectedStorageKey));
  const startMs = Number.isFinite(Number(jsDebugStatsDisconnectStartedAtMs))
    ? Number(jsDebugStatsDisconnectStartedAtMs)
    : storedStart;
  jsDebugStatsDisconnectStartedAtMs = null;
  jsDebugStatsStorageRemove(jsDebugStatsDisconnectedStorageKey);
  if (Number.isFinite(startMs) && startMs > 0 && nowMs > startMs) recordJsDebugDisconnectedSpan(startMs, nowMs);
  flushJsDebugStatsHistory();
  scheduleJsDebugPanelRefresh();
}

function recordJsDebugStatsSample(payload = {}, {forceGraphRefresh = false, scheduleRefresh = true, advanceHistoryCursor = true, replaceCoverage = null} = {}) {
  if (!jsDebugCollectionEnabled || !payload || typeof payload !== 'object') return;
  const nextPid = Number(payload.pid);
  const nextStartedAt = Number(payload.started_at);
  const serverChanged = (
    (Number.isFinite(nextPid) && Number.isFinite(jsDebugStatsServerPid) && nextPid !== jsDebugStatsServerPid)
    || (Number.isFinite(nextStartedAt) && Number.isFinite(jsDebugStatsServerStartedAt) && nextStartedAt !== jsDebugStatsServerStartedAt)
  );
  if (serverChanged) clearJsDebugGraphData();
  if (Number.isFinite(Number(payload.uptime_seconds))) jsDebugStatsServerUptimeSeconds = Math.max(0, Number(payload.uptime_seconds));
  if (Number.isFinite(nextPid)) jsDebugStatsServerPid = nextPid;
  if (Number.isFinite(nextStartedAt)) jsDebugStatsServerStartedAt = nextStartedAt;
  if (Number.isFinite(Number(payload.rss_bytes))) jsDebugStatsServerRssBytes = Number(payload.rss_bytes);
  const sampleApplied = Object.prototype.hasOwnProperty.call(payload, 'history') || [
    payload.uptime_seconds,
    payload.pid,
    payload.started_at,
    payload.rss_bytes,
    payload.cpu_percent,
    payload.system_cpu_percent,
  ].some(value => Number.isFinite(Number(value)));
  const firstSampleApplied = sampleApplied && !jsDebugStatsPollState.firstSampleReceived;
  if (firstSampleApplied) {
    jsDebugStatsPollState.firstSampleReceived = true;
    armJsDebugStatsPolling();
  }
  debugGraphApplyServerHistory(payload.history, {advanceLiveCursor: advanceHistoryCursor, replaceCoverage});
  // The restart response was requested with the previous process's sequence. Refetch from zero so
  // stale high-water marks cannot hide the replacement process's durable history. Drop the
  // partial old-cursor response and queue the zero-cursor fetch now instead of showing an empty
  // graph until the normal 30-second poll.
  if (serverChanged) {
    clearJsDebugGraphData();
    resetJsDebugHistoryReadiness();
    jsDebugStatsServerSequence = 0;
    jsDebugStatsPollState.pending = true;
    jsDebugStatsPollState.pendingForceGraphRefresh = true;
  }
  if (payload.history && typeof payload.history === 'object') {
    if (scheduleRefresh) scheduleJsDebugPanelRefresh({force: firstSampleApplied || forceGraphRefresh});
    return;
  }
  const cpuPercent = Number(payload.cpu_percent);
  if (!Number.isFinite(cpuPercent)) return;
  const systemCpuPercent = Number(payload.system_cpu_percent);
  const sampleTimeMs = Number.isFinite(Number(payload.time)) ? Number(payload.time) * 1000 : Date.now();
  const bucket = debugGraphBucketForTime(sampleTimeMs, Date.now());
  debugGraphAddBucketData(bucket, {
    cpuPercent,
    systemCpuPercent: Number.isFinite(systemCpuPercent) ? systemCpuPercent : 0,
  });
  compactJsDebugGraphBuckets();
  if (scheduleRefresh) scheduleJsDebugPanelRefresh({force: firstSampleApplied || forceGraphRefresh});
}

function clearJsDebugGraphData() {
  jsDebugGraphRawBuckets.clear();
  jsDebugGraphRollupBuckets.clear();
  resetDebugGraphAgentTokenHistory();
  jsDebugStatsAgentTokenSchemaVersion = 0;
  jsDebugGraphEventRecords.clear();
  jsDebugGraphPendingServerBuckets.clear();
}

function debugGraphBucketForServerRecord(record) {
  if (!record || typeof record !== 'object') return null;
  const startSeconds = Number(record.start);
  const durationSeconds = Number(record.duration);
  if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return null;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, durationSeconds * 1000);
  const startMs = Math.floor(startSeconds * 1000);
  const map = durationMs < jsDebugGraphMiddleBucketMs ? jsDebugGraphRawBuckets : jsDebugGraphRollupBuckets;
  return debugGraphBucket(map, startMs, durationMs);
}

function debugGraphApplyServerRecord(record) {
  const bucket = debugGraphBucketForServerRecord(record);
  if (!bucket) return;
  bucket.apiCount = Math.max(bucket.apiCount, Number(record.api_count || 0));
  bucket.sseCount = Math.max(bucket.sseCount, Number(record.sse_count || 0));
  bucket.latencyTotalMs = Math.max(bucket.latencyTotalMs, Number(record.latency_total_ms || 0));
  bucket.latencyCount = Math.max(bucket.latencyCount, Number(record.latency_count || 0));
  bucket.bandwidthBytes = Math.max(bucket.bandwidthBytes, Number(record.bandwidth_bytes || 0));
  bucket.heartbeatCount = Math.max(bucket.heartbeatCount, Number(record.heartbeat_count || 0));
  bucket.disconnectedMs = Math.max(bucket.disconnectedMs, Number(record.disconnected_ms || 0));
  debugGraphApplyServerClients(bucket, record.clients);
  debugGraphApplyServerProcesses(bucket, record.servers);
  bucket.cpuTotalPercent = Math.max(bucket.cpuTotalPercent, Number(record.cpu_total_percent || 0));
  bucket.cpuCount = Math.max(bucket.cpuCount, Number(record.cpu_count || 0));
  bucket.systemCpuTotalPercent = Math.max(bucket.systemCpuTotalPercent, Number(record.system_cpu_total_percent || 0));
  bucket.systemCpuCount = Math.max(bucket.systemCpuCount, Number(record.system_cpu_count || 0));
  debugGraphApplyHostMetrics(bucket, record.host_metrics);
  debugGraphApplyServerAgentStatus(bucket, record);
  bucket.tokensPerAgentTotal = Math.max(bucket.tokensPerAgentTotal, Number(record.tokens_per_agent_total || 0));
  bucket.agentTokenSamples = Math.max(bucket.agentTokenSamples, Number(record.agent_token_samples || 0));
  debugGraphApplyServerAgentTokenRates(bucket, record.agent_token_rates);
}

function debugGraphApplyHostMetricProcesses(target, source, valueKey = 'totalPercent') {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  for (const [key, record] of Object.entries(source)) {
    if (!record || typeof record !== 'object') continue;
    const total = Number(valueKey === 'totalBytes' ? record.total_bytes : record.total_percent || 0);
    const samples = Number(record.samples || 0);
    if (!Number.isFinite(total) || !Number.isFinite(samples) || samples <= 0) continue;
    const item = target.get(key) || {label: String(record.label || key), [valueKey]: 0, samples: 0};
    item.label = String(record.label || item.label || key);
    item[valueKey] = Math.max(item[valueKey], Math.max(0, total));
    item.samples = Math.max(item.samples, Math.max(0, samples));
    target.set(key, item);
  }
}

function debugGraphApplyHostMetrics(bucket, source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  const target = bucket.hostMetrics || (bucket.hostMetrics = debugGraphNewHostMetrics());
  target.systemMemoryUsedTotalBytes = Math.max(target.systemMemoryUsedTotalBytes, Number(source.system_memory_used_total_bytes || 0));
  target.systemMemoryCapacityTotalBytes = Math.max(target.systemMemoryCapacityTotalBytes, Number(source.system_memory_capacity_total_bytes || 0));
  target.systemMemoryCount = Math.max(target.systemMemoryCount, Number(source.system_memory_count || 0));
  debugGraphApplyHostMetricProcesses(target.cpuProcesses, source.cpu_processes);
  debugGraphApplyHostMetricProcesses(target.memoryProcesses, source.memory_processes, 'totalBytes');
  debugGraphApplyHostMetricProcesses(target.gpuUtilProcesses, source.gpu_util_processes);
  debugGraphApplyHostMetricProcesses(target.gpuMemoryProcesses, source.gpu_memory_processes, 'totalBytes');
  if (!source.gpu_devices || typeof source.gpu_devices !== 'object' || Array.isArray(source.gpu_devices)) return;
  for (const [key, record] of Object.entries(source.gpu_devices)) {
    if (!record || typeof record !== 'object') continue;
    const samples = Number(record.samples || 0);
    if (!Number.isFinite(samples) || samples <= 0) continue;
    const item = target.gpuDevices.get(key) || {label: String(record.label || key), utilTotalPercent: 0, memoryUsedTotalBytes: 0, memoryCapacityTotalBytes: 0, samples: 0};
    item.label = String(record.label || item.label || key);
    item.utilTotalPercent = Math.max(item.utilTotalPercent, Math.max(0, Number(record.util_total_percent || 0)));
    item.memoryUsedTotalBytes = Math.max(item.memoryUsedTotalBytes, Math.max(0, Number(record.memory_used_total_bytes || 0)));
    item.memoryCapacityTotalBytes = Math.max(item.memoryCapacityTotalBytes, Math.max(0, Number(record.memory_capacity_total_bytes || 0)));
    item.samples = Math.max(item.samples, Math.max(0, samples));
    target.gpuDevices.set(key, item);
  }
}

function debugGraphAgentStatusSnapshot(record) {
  const askAgentTotal = Number(record?.ask_agent_total);
  const runAgentTotal = Number(record?.run_agent_total);
  const transitionAgentTotal = Number(record?.transition_agent_total);
  const idleAgentTotal = Number(record?.idle_agent_total);
  const hasSplitAgentTotals = [askAgentTotal, runAgentTotal, transitionAgentTotal, idleAgentTotal].some(Number.isFinite);
  if (!hasSplitAgentTotals && !Number.isFinite(Number(record?.active_agent_total)) && !Number.isFinite(Number(record?.inactive_agent_total))) return null;
  const ask = hasSplitAgentTotals ? Math.max(0, askAgentTotal || 0) : 0;
  const run = hasSplitAgentTotals ? Math.max(0, runAgentTotal || 0) : Math.max(0, Number(record.active_agent_total || 0));
  const idle = hasSplitAgentTotals && Number.isFinite(idleAgentTotal)
    ? Math.max(0, idleAgentTotal)
    : Math.max(0, Number(record.inactive_agent_total || 0));
  const transition = hasSplitAgentTotals
    ? Math.max(0, (transitionAgentTotal || 0) - (Number.isFinite(idleAgentTotal) ? 0 : idle))
    : 0;
  return {
    askAgentTotal: ask,
    runAgentTotal: run,
    transitionAgentTotal: transition,
    idleAgentTotal: idle,
    activeAgentTotal: ask + run + transition,
    inactiveAgentTotal: idle,
    agentActivitySamples: Math.max(0, Number(record.agent_activity_samples || 0)),
  };
}

function debugGraphApplyServerAgentStatus(bucket, record) {
  const snapshot = debugGraphAgentStatusSnapshot(record);
  if (!snapshot) return;
  const sequence = Number(record.sequence);
  if (Number.isFinite(sequence)) {
    if (sequence < Number(bucket.agentStatusSequence ?? -1)) return;
    bucket.agentStatusSequence = sequence;
  } else if (snapshot.agentActivitySamples < Number(bucket.agentActivitySamples || 0)) {
    return;
  }
  Object.assign(bucket, snapshot);
}

function debugGraphApplyServerProcesses(bucket, servers) {
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) return;
  if (!(bucket.servers instanceof Map)) bucket.servers = new Map();
  for (const [processId, record] of Object.entries(servers)) {
    const cleanProcessId = String(processId || '').trim();
    if (!cleanProcessId || !record || typeof record !== 'object') continue;
    const process = bucket.servers.get(cleanProcessId) || {label: cleanProcessId, cpuTotalPercent: 0, cpuCount: 0};
    process.label = String(record.label || process.label || cleanProcessId);
    process.cpuTotalPercent = Math.max(process.cpuTotalPercent, Number(record.cpu_total_percent || 0));
    process.cpuCount = Math.max(process.cpuCount, Number(record.cpu_count || 0));
    bucket.servers.set(cleanProcessId, process);
  }
}

function debugGraphApplyServerClients(bucket, clients) {
  if (!clients || typeof clients !== 'object' || Array.isArray(clients)) return;
  if (!(bucket.clients instanceof Map)) bucket.clients = new Map();
  for (const [clientId, record] of Object.entries(clients)) {
    const cleanClientId = String(clientId || '').trim();
    if (!cleanClientId || !record || typeof record !== 'object') continue;
    const client = bucket.clients.get(cleanClientId) || debugGraphNewClientBucket();
    client.apiCount = Math.max(client.apiCount, Number(record.api_count || 0));
    client.sseCount = Math.max(client.sseCount, Number(record.sse_count || 0));
    client.latencyTotalMs = Math.max(client.latencyTotalMs, Number(record.latency_total_ms || 0));
    client.latencyCount = Math.max(client.latencyCount, Number(record.latency_count || 0));
    client.bandwidthBytes = Math.max(client.bandwidthBytes, Number(record.bandwidth_bytes || 0));
    client.heartbeatCount = Math.max(client.heartbeatCount, Number(record.heartbeat_count || 0));
    client.disconnectedMs = Math.max(client.disconnectedMs, Number(record.disconnected_ms || 0));
    bucket.clients.set(cleanClientId, client);
  }
}

function debugGraphApplyServerAgentTokenRates(bucket, rates) {
  const items = Array.isArray(rates) ? rates : [];
  if (!items.length) return;
  if (!(bucket.agentTokenRates instanceof Map)) bucket.agentTokenRates = new Map();
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const key = String(item.key || '').trim();
    if (!key) continue;
    const total = Number(item.total ?? item.rate ?? item.value);
    const samples = Number(item.samples || 0);
    const tokens = Number(item.tokens || 0);
    const seconds = Number(item.seconds || 0);
    if (!Number.isFinite(total) && !Number.isFinite(samples) && !Number.isFinite(tokens)) continue;
    const label = String(item.label || key).trim() || key;
    const existing = bucket.agentTokenRates.get(key) || {label, total: 0, samples: 0, tokens: 0, seconds: 0};
    existing.label = label;
    if (Number.isFinite(total)) existing.total = Math.max(Number(existing.total || 0), Math.max(0, total));
    if (Number.isFinite(samples)) existing.samples = Math.max(Number(existing.samples || 0), Math.max(0, samples));
    if (Number.isFinite(tokens)) existing.tokens = Math.max(Number(existing.tokens || 0), Math.max(0, tokens));
    if (Number.isFinite(seconds)) existing.seconds = Math.max(Number(existing.seconds || 0), Math.max(0, seconds));
    bucket.agentTokenRates.set(key, existing);
  }
}

function debugGraphRemoveCoarserServerBuckets(startSeconds, endSeconds, resolutionSeconds) {
  const startMs = Number(startSeconds) * 1000;
  const endMs = Number(endSeconds) * 1000;
  const resolutionMs = Number(resolutionSeconds) * 1000;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !Number.isFinite(resolutionMs) || resolutionMs <= 0) return 0;
  let removed = 0;
  for (const map of [jsDebugGraphRawBuckets, jsDebugGraphRollupBuckets]) {
    for (const [key, bucket] of map.entries()) {
      const bucketStart = Number(bucket?.startMs);
      const bucketDuration = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
      const bucketEnd = bucketStart + bucketDuration;
      if (bucketDuration <= resolutionMs || bucketStart < startMs || bucketEnd > endMs) continue;
      map.delete(key);
      removed += 1;
    }
  }
  return removed;
}

function debugGraphApplyServerHistory(history = {}, {advanceLiveCursor = true, replaceCoverage = null} = {}) {
  if (!history || typeof history !== 'object') return;
  if (replaceCoverage) {
    debugGraphRemoveCoarserServerBuckets(
      replaceCoverage.covered_start,
      replaceCoverage.covered_end,
      replaceCoverage.resolution_seconds,
    );
  }
  // Compact local fine buckets before applying an authoritative server coarse bucket. Applying
  // first would merge the same measurements a second time at the 1h/2h tier boundaries.
  compactJsDebugGraphBuckets();
  const tokenSchemaVersion = Number(history.agent_token_schema_version);
  if (Number.isFinite(tokenSchemaVersion) && tokenSchemaVersion > 0 && tokenSchemaVersion !== jsDebugStatsAgentTokenSchemaVersion) {
    clearDebugGraphAgentTokenData();
    jsDebugStatsAgentTokenSchemaVersion = tokenSchemaVersion;
  }
  const sequence = Number(history.latest_sequence ?? history.sequence);
  if (advanceLiveCursor && Number.isFinite(sequence)) jsDebugStatsServerSequence = Math.max(0, sequence);
  const records = Array.isArray(history.records) ? history.records : [];
  records.forEach(debugGraphApplyServerRecord);
  debugGraphApplyServerAgentTokenHistory(history.agent_token_history, {advanceLiveCursor});
  compactJsDebugGraphBuckets();
}

function debugGraphApplyServerAgentTokenHistory(history = {}, {advanceLiveCursor = true} = {}) {
  if (!history || typeof history !== 'object') return;
  const resolutionSeconds = Number(history.resolution_seconds);
  if (!Number.isFinite(resolutionSeconds) || resolutionSeconds <= 0) return;
  if (history.snapshot || resolutionSeconds !== jsDebugStatsAgentTokenResolutionSeconds) jsDebugGraphAgentTokenBuckets.clear();
  jsDebugStatsAgentTokenResolutionSeconds = resolutionSeconds;
  const sequence = Number(history.sequence);
  if (advanceLiveCursor && Number.isFinite(sequence)) jsDebugStatsAgentTokenSequence = Math.max(0, sequence);
  const records = Array.isArray(history.records) ? history.records : [];
  for (const record of records) {
    const startSeconds = Number(record?.start);
    const durationSeconds = Number(record?.duration);
    if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds) || durationSeconds <= 0) continue;
    const bucket = debugGraphBucket(jsDebugGraphAgentTokenBuckets, Math.floor(startSeconds * 1000), durationSeconds * 1000);
    bucket.tokensPerAgentTotal = Math.max(bucket.tokensPerAgentTotal, Number(record.tokens_per_agent_total || 0));
    bucket.agentTokenSamples = Math.max(bucket.agentTokenSamples, Number(record.agent_token_samples || 0));
    debugGraphApplyServerAgentTokenRates(bucket, record.agent_token_rates);
  }
}

function debugGraphAgentTokenResolution(nowMs = Date.now()) {
  const rangeSeconds = debugGraphDomain(nowMs).rangeSeconds;
  if (rangeSeconds < 4 * 60 * 60) return 0;
  return rangeSeconds >= 16 * 60 * 60 ? 5 * 60 : 2 * 60;
}

function resetDebugGraphAgentTokenHistory() {
  jsDebugStatsAgentTokenSequence = 0;
  jsDebugStatsAgentTokenResolutionSeconds = 0;
  jsDebugGraphAgentTokenBuckets.clear();
}

function syncDebugGraphAgentTokenResolution() {
  const resolutionSeconds = debugGraphAgentTokenResolution();
  if (resolutionSeconds === jsDebugStatsAgentTokenResolutionSeconds) return false;
  resetDebugGraphAgentTokenHistory();
  // Long ranges intentionally omit token rates from normal history and use the separate compact
  // token stream. Returning to a short range must fetch normal history again with token rates;
  // otherwise the old coverage marker leaves only new live samples to render.
  if (resolutionSeconds === 0) resetJsDebugHistoryReadiness();
  return true;
}

function clearDebugGraphAgentTokenData() {
  for (const map of [jsDebugGraphRawBuckets, jsDebugGraphRollupBuckets]) {
    for (const bucket of map.values()) {
      bucket.tokensPerAgentTotal = 0;
      bucket.agentTokenSamples = 0;
      bucket.agentTokenRates = new Map();
    }
  }
  resetDebugGraphAgentTokenHistory();
}

function debugGraphAggregateBucket(map, source, scaleMs) {
  const durationMs = Math.max(scaleMs, Number(source.durationMs) || scaleMs);
  const startMs = Math.floor(source.startMs / durationMs) * durationMs;
  const bucket = debugGraphBucket(map, startMs, durationMs);
  debugGraphMergeBucket(bucket, source);
}

function debugGraphBucketInRange(bucket, cutoffMs, nowMs) {
  const startMs = Number(bucket.startMs);
  if (!Number.isFinite(startMs)) return false;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
  return startMs + durationMs > cutoffMs && startMs <= nowMs;
}

function debugGraphAvailableRangeOptions(nowMs = Date.now()) {
  compactJsDebugGraphBuckets(nowMs);
  return jsDebugGraphRangeOptions;
}

function debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds = 0, nowMs = Date.now()) {
  const domainStartMs = Number(domain?.startMs);
  const domainEndMs = Number(domain?.endMs);
  const domainSpanMs = Number.isFinite(domainStartMs) && Number.isFinite(domainEndMs)
    ? Math.max(jsDebugGraphRawBucketMs, domainEndMs - domainStartMs)
    : jsDebugGraphDefaultRangeSeconds * 1000;
  const targetMs = domainSpanMs / jsDebugGraphMaxDisplayPoints;
  const displayMs = jsDebugGraphDisplayBucketMs.find(bucketMs => bucketMs >= targetMs)
    || jsDebugGraphDisplayBucketMs.at(-1);
  const retainedMs = Number.isFinite(domainStartMs)
    ? debugGraphBucketDurationForTime(domainStartMs, nowMs)
    : jsDebugGraphRawBucketMs;
  const minimumMs = Math.max(0, Number(minimumResolutionSeconds) || 0) * 1000;
  const overrideMs = Math.max(0, Number(jsDebugGraphResolutionOverrideSeconds) || 0) * 1000;
  if (overrideMs > 0) return Math.max(jsDebugGraphRawBucketMs, retainedMs, minimumMs, overrideMs);
  return Math.max(jsDebugGraphRawBucketMs, displayMs, retainedMs, minimumMs);
}

function debugGraphSourceBuckets(domain) {
  return [...jsDebugGraphRollupBuckets.values(), ...jsDebugGraphRawBuckets.values()]
    .filter(bucket => debugGraphBucketInRange(bucket, domain.startMs, domain.endMs))
    .sort((left, right) => left.startMs - right.startMs);
}

function debugGraphDisplayBuckets(nowMs = Date.now(), {minimumResolutionSeconds = 0, rangeSeconds = jsDebugGraphRangeSeconds} = {}) {
  compactJsDebugGraphBuckets(nowMs);
  const domain = debugGraphDomain(nowMs, rangeSeconds);
  const scaleMs = debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds, nowMs);
  const buckets = new Map();
  for (const bucket of jsDebugGraphRollupBuckets.values()) {
    if (debugGraphBucketInRange(bucket, domain.startMs, domain.endMs)) debugGraphAggregateBucket(buckets, bucket, scaleMs);
  }
  for (const bucket of jsDebugGraphRawBuckets.values()) {
    if (debugGraphBucketInRange(bucket, domain.startMs, domain.endMs)) debugGraphAggregateBucket(buckets, bucket, scaleMs);
  }
  return [...buckets.values()].sort((a, b) => a.startMs - b.startMs);
}

function debugGraphAgentTokenDisplayBuckets(nowMs = Date.now()) {
  const resolutionSeconds = debugGraphAgentTokenResolution(nowMs);
  if (!resolutionSeconds || resolutionSeconds !== jsDebugStatsAgentTokenResolutionSeconds || !jsDebugGraphAgentTokenBuckets.size) {
    return debugGraphDisplayBuckets(nowMs, {minimumResolutionSeconds: jsDebugGraphAgentTokenBucketSeconds, rangeSeconds: jsDebugGraphRangeSeconds});
  }
  const domain = debugGraphDomain(nowMs, jsDebugGraphRangeSeconds);
  return [...jsDebugGraphAgentTokenBuckets.values()]
    .filter(bucket => debugGraphBucketInRange(bucket, domain.startMs, domain.endMs))
    .sort((a, b) => a.startMs - b.startMs);
}

function debugGraphDomain(nowMs = Date.now(), rangeSeconds = jsDebugGraphRangeSeconds) {
  const fallbackEndMs = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
  if (debugGraphZoomDomainValid()) {
    const startMs = Math.max(fallbackEndMs - jsDebugGraphRetentionMs, Number(jsDebugGraphZoomDomain.startMs));
    const endMs = Math.max(startMs + 1000, Number(jsDebugGraphZoomDomain.endMs));
    return {startMs, endMs, rangeSeconds: (endMs - startMs) / 1000, zoomed: true};
  }
  const endMs = fallbackEndMs;
  const activeRangeSeconds = normalizedJsDebugGraphRange(rangeSeconds, endMs);
  const durationMs = Math.max(1000, activeRangeSeconds * 1000);
  return {startMs: endMs - durationMs, endMs, rangeSeconds: activeRangeSeconds, zoomed: false};
}

function debugGraphBucketRate(bucket, value) {
  const seconds = Math.max(1, Number(bucket?.durationMs || jsDebugGraphRawBucketMs) / 1000);
  return Number(value || 0) / seconds;
}

function debugGraphAgentTokenBucketValue(bucket, item) {
  const tokens = Number(item?.tokens);
  if (Number.isFinite(tokens) && tokens > 0) {
    // `seconds` is the real elapsed span over which the transcript counter advanced. It remains
    // correct after the server folds raw samples into 2/5-minute history buckets; using the rendered
    // bucket width here made the same activity look like a different tokens/min rate as the view changed.
    const seconds = Number(item?.seconds);
    if (Number.isFinite(seconds) && seconds > 0) return (tokens / seconds) * 60;
    const minutes = Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
    return tokens / minutes;
  }
  return Number(item?.samples || 0) > 0 ? Number(item.total || 0) / Number(item.samples || 1) : 0;
}

function debugGraphAgentTokenDisplayedSum(buckets) {
  let total = 0;
  for (const bucket of buckets || []) {
    if (!(bucket?.agentTokenRates instanceof Map)) continue;
    for (const item of bucket.agentTokenRates.values()) {
      const tokens = Number(item?.tokens);
      if (Number.isFinite(tokens) && tokens >= 0) {
        total += tokens;
        continue;
      }
      if (Number(item?.samples || 0) <= 0) continue;
      const minutes = Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
      total += debugGraphAgentTokenBucketValue(bucket, item) * minutes;
    }
  }
  return Math.max(0, total);
}

function debugGraphBucketFieldSum(bucket, fields) {
  return fields.reduce((bucketTotal, field) => (
    bucketTotal + Math.max(0, Number(bucket?.[field]) || 0)
  ), 0);
}

function debugGraphDisplayedClientFieldSum(buckets, fields) {
  return (buckets || []).reduce((total, bucket) => {
    const clientBuckets = bucket?.clients instanceof Map && bucket.clients.size ? [...bucket.clients.values()] : [bucket];
    return total + clientBuckets.reduce((clientTotal, clientBucket) => clientTotal + debugGraphBucketFieldSum(clientBucket, fields), 0);
  }, 0);
}

function debugGraphDisplayedSummary(group, buckets) {
  const spec = jsDebugGraphDisplayedSummarySpecs[group?.displayedSummary];
  if (!spec) return null;
  const value = Math.max(0, Number(spec.value(buckets)) || 0);
  return {
    attribute: spec.attribute,
    text: t(spec.labelKey, {count: spec.format(value)}),
    value,
  };
}

function debugGraphThisClientMetricBucket(bucket, metric) {
  if (!bucket || !metric) return null;
  if (bucket.clients instanceof Map) {
    const mapped = bucket.clients.get(jsDebugStatsClientIdForRequest());
    if (mapped) return mapped;
  }
  // The server's top-level values belong to this requesting browser. They also preserve
  // pre-client-map history when another retained client map has no row for this browser.
  return metric.hasData(bucket) ? bucket : null;
}

function debugGraphOtherClientMetricBuckets(bucket, metric) {
  if (!(bucket?.clients instanceof Map) || !metric) return [];
  const thisClientId = jsDebugStatsClientIdForRequest();
  return [...bucket.clients.entries()]
    .filter(([clientId, clientBucket]) => clientId !== thisClientId
      && (metric.key !== 'latency' || metric.hasData(clientBucket)))
    .map(([, clientBucket]) => clientBucket);
}

function debugGraphOtherClientMetricAverage(bucket, metric) {
  const clientBuckets = debugGraphOtherClientMetricBuckets(bucket, metric);
  if (!clientBuckets.length) return 0;
  return clientBuckets.reduce((sum, clientBucket) => sum + metric.value(clientBucket), 0) / clientBuckets.length;
}

function debugGraphClientSeriesDef(metric, {key = metric.key, labelKey, clientId, clientAggregate, clientLinePattern, color = ''}) {
  const otherClients = clientAggregate === jsDebugGraphOtherClientsAverageAggregate;
  return {
    ...metric, key, labelKey, metricLabelKey: metric.labelKey, cssKey: metric.key, clientMetric: true, metricKey: metric.key, clientId, clientAggregate, clientLinePattern,
    ...(color ? {color} : {}),
    value: bucket => otherClients ? debugGraphOtherClientMetricAverage(bucket, metric) : (() => { const clientBucket = debugGraphThisClientMetricBucket(bucket, metric); return clientBucket ? metric.value(clientBucket) : 0; })(),
    hasData: bucket => otherClients ? debugGraphOtherClientMetricBuckets(bucket, metric).length > 0 : (() => { const clientBucket = debugGraphThisClientMetricBucket(bucket, metric); return Boolean(clientBucket && (metric.key !== 'latency' || metric.hasData(clientBucket))); })(),
  };
}

function debugGraphProcessCpuBucketValue(bucket, processId) {
  const process = bucket?.servers instanceof Map ? bucket.servers.get(processId) : null;
  return Number(process?.cpuCount || 0) > 0
    ? Math.min(100, Number(process.cpuTotalPercent || 0) / Number(process.cpuCount || 1))
    : 0;
}

function debugGraphProcessCpuBucketHasData(bucket, processId) {
  return Number(bucket?.servers instanceof Map ? bucket.servers.get(processId)?.cpuCount : 0) > 0;
}

function debugGraphHostMetricBucketItem(bucket, series) {
  const mapName = series.hostProcessId
    ? (series.hostMetric === 'cpu' ? 'cpuProcesses' : series.hostMetric === 'memory' ? 'memoryProcesses' : series.hostMetric === 'gpuUtil' ? 'gpuUtilProcesses' : 'gpuMemoryProcesses')
    : 'gpuDevices';
  return bucket?.hostMetrics?.[mapName] instanceof Map ? bucket.hostMetrics[mapName].get(series.hostProcessId || series.gpuDeviceId) : null;
}

function debugGraphHostMetricBucketValue(bucket, series) {
  const item = debugGraphHostMetricBucketItem(bucket, series);
  if (series.hostProcessId) {
    const total = series.hostMetric === 'memory' || series.hostMetric === 'gpuMemory' ? Number(item?.totalBytes || 0) : Number(item?.totalPercent || 0);
    return Number(item?.samples || 0) > 0 ? total / Number(item.samples || 1) : 0;
  }
  const total = series.hostMetric === 'gpuUtil' ? Number(item?.utilTotalPercent || 0) : Number(item?.memoryUsedTotalBytes || 0);
  return Number(item?.samples || 0) > 0 ? total / Number(item.samples || 1) : 0;
}

function debugGraphHostMetricBucketHasData(bucket, series) {
  return Number(debugGraphHostMetricBucketItem(bucket, series)?.samples || 0) > 0;
}

function debugGraphMovingAverageValues(values, sampleCount = jsDebugGraphMovingAverageSamples) {
  const count = Math.max(1, Math.floor(Number(sampleCount) || 1));
  const window = [];
  let total = 0;
  return values.map(value => {
    const sample = Math.max(0, Number(value) || 0);
    window.push(sample);
    total += sample;
    if (window.length > count) total -= window.shift();
    return total / window.length;
  });
}

function debugGraphNiceCeil(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(number));
  const scaled = number / magnitude;
  for (const step of [1, 2, 5, 10]) {
    if (scaled <= step) return step * magnitude;
  }
  return 10 * magnitude;
}

function debugGraphNiceCountPerSecondAxisMax(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const whole = Math.max(2, Math.ceil(number));
  return whole % 2 === 0 ? whole : whole + 1;
}

function debugGraphNiceBytesPerSecondAxisMax(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const unit = number >= 1024 * 1024 ? 1024 * 1024 : (number >= 1024 ? 1024 : 1);
  return debugGraphNiceCeil(number / unit) * unit;
}

function debugGraphNiceAxisMax(value, unit) {
  if (unit === 'count') return Math.max(1, Math.ceil(debugGraphNiceCeil(value)));
  if (unit === 'countPerSecond') return debugGraphNiceCountPerSecondAxisMax(value);
  if (unit === 'ms') return debugGraphNiceCeil(value);
  if (unit === 'bytesPerSecond') return debugGraphNiceBytesPerSecondAxisMax(value);
  if (unit === 'tokens') return Math.max(1, debugGraphNiceCeil(value));
  if (unit === 'tokensPerMinute') return Math.max(1, debugGraphNiceCeil(value));
  return value;
}

function debugGraphTokenNumberText(value) {
  const number = Math.max(0, Number(value) || 0);
  if (number >= 1000 * 1000) return `${(number / 1000 / 1000).toFixed(number >= 100 * 1000 * 1000 ? 0 : 1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 100 * 1000 ? 0 : 1)}k`;
  if (Number.isInteger(number)) return String(number);
  return number >= 100 ? number.toFixed(0) : number.toFixed(number >= 10 ? 1 : 2);
}

function debugGraphTokensText(value) {
  return t('debug.graph.unit.tokens', {count: debugGraphTokenNumberText(value)});
}

function debugGraphTokensPerMinuteText(value) {
  return t('debug.graph.unit.tokensPerMinute', {count: debugGraphTokenNumberText(value)});
}

function debugGraphValueText(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  if (unit === 'count') return Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2);
  if (unit === 'countPerSecond') return `${Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2)}/s`;
  if (unit === 'ms' && number >= 1000) return `${number >= 10000 ? (number / 1000).toFixed(0) : (number / 1000).toFixed(1)} s`;
  if (unit === 'ms') return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} ms`;
  if (unit === 'bytes') {
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)} KB`;
    return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} B`;
  }
  if (unit === 'bytesPerSecond') {
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)}MB/s`;
    if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)}kB/s`;
    return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}B/s`;
  }
  if (unit === 'tokens') return debugGraphTokensText(number);
  if (unit === 'tokensPerMinute') return debugGraphTokensPerMinuteText(number);
  if (unit === 'percent') return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}%`;
  return number >= 10 ? number.toFixed(1) : number.toFixed(2);
}

function debugGraphTerseTimeText(milliseconds) {
  const number = Math.max(0, Number(milliseconds) || 0);
  if (number >= 1000) {
    const seconds = number / 1000;
    return `${Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(seconds >= 10 ? 1 : 2)}s`;
  }
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}ms`;
}

function debugGraphTerseBytesText(bytes) {
  const number = Math.max(0, Number(bytes) || 0);
  if (number >= 1024 * 1024 * 1024) return `${(number / 1024 / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 * 1024 ? 0 : 1)}GB`;
  if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)}MB`;
  if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)}kB`;
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}B`;
}

function debugGraphAxisValueText(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    if (unit === 'count') return '0';
    if (unit === 'countPerSecond') return '0';
    if (unit === 'ms') return '0';
    if (unit === 'bytes') return '0GB';
    if (unit === 'bytesPerSecond') return '0';
    if (unit === 'tokens') return '0';
    if (unit === 'tokensPerMinute') return '0';
    if (unit === 'percent') return '0%';
    return '0';
  }
  if (unit === 'countPerSecond') return Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2);
  if (unit === 'ms') return debugGraphTerseTimeText(number);
  if (unit === 'bytes' || unit === 'bytesPerSecond') return debugGraphTerseBytesText(number);
  if (unit === 'tokens') return debugGraphTokenNumberText(number);
  if (unit === 'tokensPerMinute') return debugGraphTokenNumberText(number);
  return debugGraphValueText(number, unit);
}

function debugGraphUptimeText(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function debugGraphBytesText(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  const mib = value / 1024 / 1024;
  if (mib >= 1024) return `${(mib / 1024).toFixed(1)} GiB`;
  return `${mib.toFixed(mib >= 100 ? 0 : 1)} MiB`;
}

function debugGraphTotalMegabytesText(bytes) {
  const value = Math.max(0, Number(bytes) || 0) / 1024 / 1024;
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function debugRemovalLatencyMetaText() {
  if (typeof terminalRemovalLatencySummary !== 'function') return '';
  const summary = terminalRemovalLatencySummary();
  if (!summary?.count) return '';
  return t('debug.graph.meta.removal', {
    last: debugGraphTerseTimeText(summary.last?.durationMs),
    average: debugGraphTerseTimeText(summary.averageMs),
    count: summary.count,
  });
}

function debugClientPerfRows() {
  if (typeof clientPerfSummary !== 'function') return [];
  const preferred = ['focusSet', 'tabActivationPaint', 'tabberFullRefresh', 'tabberLayoutSync', 'statsHistoryFetch', 'statsHistoryParse', 'statsHistoryApply', 'statsHistoryRender', 'statsHistoryPaint', 'statsHistoryLoading', 'statsNoDataSweep', 'keydownToTermData', 'term.onData', 'wsSend', 'echoToTermWrite', 'xtermWrite', 'terminalUnderlineRender', 'terminalAttentionScan', 'terminalBlankProbe', 'finderRefresh', 'sessionFilesRefresh', 'sessionFilesRender', 'renderInfoPanel', 'renderSessionButtons', 'renderPaneTabStrips', 'renderPanels', 'sseEvent', 'autoStatusRender', 'longTask'];
  const order = new Map(preferred.map((name, index) => [name, index]));
  return clientPerfSummary()
    .filter(row => Number(row.count || 0) > 0)
    .sort((left, right) => (order.get(left.name) ?? 999) - (order.get(right.name) ?? 999) || Number(right.maxMs || 0) - Number(left.maxMs || 0))
    .slice(0, 18);
}

function debugClientPerfText(row) {
  const parts = [
    `${row.name}`,
    `n=${Math.floor(Number(row.count || 0))}`,
  ];
  if (Number.isFinite(Number(row.avgMs)) && Number(row.avgMs) > 0) parts.push(`avg=${Number(row.avgMs).toFixed(1)}ms`);
  if (Number.isFinite(Number(row.maxMs)) && Number(row.maxMs) > 0) parts.push(`max=${Number(row.maxMs).toFixed(1)}ms`);
  if (Number(row.rows || 0) > 0) parts.push(`rows=${Math.floor(Number(row.rows))}`);
  if (Number(row.nodes || 0) > 0) parts.push(`nodes=${Math.floor(Number(row.nodes))}`);
  if (Number(row.bytes || 0) > 0) parts.push(`bytes=${Math.floor(Number(row.bytes))}`);
  if (Number(row.skipped || 0) > 0) parts.push(`skipped=${Math.floor(Number(row.skipped))}`);
  return parts.join(' ');
}

function debugClientPerfHtml() {
  if (debugModeExplicitUrlEnabled !== true) return '';
  const rows = debugClientPerfRows();
  const longTasks = typeof clientPerfLongTaskSummary === 'function' ? clientPerfLongTaskSummary() : {count: 0, averageMs: 0, maxMs: 0};
  const activeAnimations = typeof clientPerfActiveAnimationCount === 'function' ? clientPerfActiveAnimationCount() : 0;
  if (!rows.length && !longTasks.count && !activeAnimations) return '';
  const timing = longTasks.count ? t('debug.graph.clientWorkTiming', {average: longTasks.averageMs, max: longTasks.maxMs}) : '';
  const header = t('debug.graph.clientWork', {animations: activeAnimations, tasks: longTasks.count, timing});
  return `<div class="js-debug-client-perf" data-js-debug-client-perf>
    <div class="js-debug-client-perf-title">${esc(header)}</div>
    <div class="js-debug-client-perf-grid">${rows.map(row => `<div class="js-debug-client-perf-row">${esc(debugClientPerfText(row))}</div>`).join('')}</div>
  </div>`;
}

function debugGraphMetaItems() {
  const items = [];
  if (Number.isFinite(jsDebugStatsServerUptimeSeconds)) items.push(t('debug.graph.meta.uptime', {uptime: debugGraphUptimeText(jsDebugStatsServerUptimeSeconds)}));
  if (Number.isFinite(jsDebugStatsServerPid)) items.push(`PID=${Math.floor(jsDebugStatsServerPid)}`);
  const rss = debugGraphBytesText(jsDebugStatsServerRssBytes);
  if (rss) items.push(t('debug.graph.meta.rss', {rss}));
  if (Number.isFinite(jsDebugStatsServerSequence) && jsDebugStatsServerSequence > 0) items.push(t('debug.graph.meta.serverSequence', {sequence: Math.floor(jsDebugStatsServerSequence)}));
  const removalLatency = debugRemovalLatencyMetaText();
  if (removalLatency) items.push(removalLatency);
  if (items.length) {
    const counts = debugEventCounts();
    const uploadedMb = debugGraphTotalMegabytesText(counts.apiRequestBytes);
    const downloadedMb = debugGraphTotalMegabytesText(counts.apiResponseBytes + counts.sseBytes);
    items.push(t('debug.graph.meta.totalTraffic', {uploaded: uploadedMb, downloaded: downloadedMb}));
  }
  return items;
}

function debugGraphWaitingForServerStats() {
  return debugGraphMetaItems().length === 0;
}

function debugGraphMetaHtml() {
  const items = debugGraphMetaItems();
  const initialHistoryOverlayOwnsLoading = jsDebugHistoryReadiness.phase === 'loading-initial'
    && jsDebugHistoryReadiness.overlayVisible === true;
  const metaHtml = items.length
    ? esc(items.join(' | '))
    : (initialHistoryOverlayOwnsLoading ? '' : textWithMovingEllipsisHtml(t('debug.waitingForServerStats')));
  return `<div class="js-debug-graph-meta" data-js-debug-uptime="${esc(Number.isFinite(jsDebugStatsServerUptimeSeconds) ? debugGraphUptimeText(jsDebugStatsServerUptimeSeconds) : '')}">${metaHtml}</div>`;
}

function debugGraphHistoryOverlayText(state = jsDebugHistoryReadiness) {
  const range = jsDebugGraphRangeLabel(state.requestedRangeSeconds);
  if (state.phase === 'loading-initial') return t('debug.graph.history.loadingInitial');
  if (state.phase === 'loading-older') return t('debug.graph.history.loadingOlder', {range});
  if (state.phase === 'retrying') return t('debug.graph.history.retrying', {range});
  if (state.phase === 'error') return t('debug.graph.history.error', {range, error: state.error || t('common.unknown')});
  return '';
}

function debugGraphHistoryOverlayContentHtml(state = jsDebugHistoryReadiness) {
  const text = debugGraphHistoryOverlayText(state);
  if (!text) return '';
  const message = jsDebugHistoryReadinessBusy(state)
    ? textWithMovingEllipsisHtml(text, 'js-debug-history-loading-dots')
    : esc(text);
  const retry = state.phase === 'error'
    ? `<button type="button" class="preferences-inline-action js-debug-history-retry" data-js-debug-history-retry>${esc(t('common.retry'))}</button>`
    : '';
  return `<div class="js-debug-history-overlay-message"><span>${message}</span>${retry}</div>`;
}

function debugGraphHistoryOverlayHtml(state = jsDebugHistoryReadiness) {
  const hidden = state.overlayVisible === true ? '' : ' hidden';
  return `<div class="js-debug-history-overlay" data-js-debug-history-overlay aria-live="polite" aria-atomic="true"${hidden}>${debugGraphHistoryOverlayContentHtml(state)}</div>`;
}

function debugGraphAgentTokenSeriesDefs(buckets) {
  const tokenAgents = new Map();
  for (const bucket of buckets) {
    if (!(bucket.agentTokenRates instanceof Map)) continue;
    for (const [key, item] of bucket.agentTokenRates.entries()) {
      const existing = tokenAgents.get(String(key)) || {label: item?.label || String(key), samples: 0};
      existing.label = item?.label || existing.label;
      existing.samples += Number(item?.samples || 0);
      tokenAgents.set(String(key), existing);
    }
  }
  const agentSeries = [...tokenAgents.entries()]
    .filter(([, item]) => item.samples > 0)
    .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]))
    .map(([key, item], index) => ({
      key: `${jsDebugGraphAgentTokenSeriesPrefix}${key}`,
      label: item.label,
      unit: 'tokensPerMinute',
      cssKey: 'agentToken',
      agentTokenSeries: true,
      agentTokenKey: key,
      agentTokenPatternIndex: index % jsDebugGraphAgentTokenPatternCount,
      color: jsDebugGraphAgentTokenColors[index % jsDebugGraphAgentTokenColors.length],
      value: bucket => {
        const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
        return tokenItem ? debugGraphAgentTokenBucketValue(bucket, tokenItem) : 0;
      },
      hasData: bucket => {
        const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
        return Number(tokenItem?.samples || 0) > 0 || Number(tokenItem?.tokens || 0) > 0;
      },
    }));
  return agentSeries;
}

function debugGraphClientMetricSeriesDefs(buckets) {
  return jsDebugGraphClientMetrics
    .filter(metric => buckets.some(bucket => debugGraphOtherClientMetricBuckets(bucket, metric).length > 0))
    .map(metric => debugGraphClientSeriesDef(metric, {
      key: `client:${jsDebugGraphOtherClientsAverageId}:${metric.key}`,
      labelKey: 'debug.graph.series.otherClientsAverage',
      clientId: jsDebugGraphOtherClientsAverageId,
      clientAggregate: jsDebugGraphOtherClientsAverageAggregate,
      clientLinePattern: jsDebugGraphOtherClientsAverageLinePattern,
      color: 'var(--bad)',
    }));
}

function debugGraphProcessCpuSeriesDefs(buckets) {
  const processes = new Map();
  for (const bucket of buckets) {
    if (!(bucket.servers instanceof Map)) continue;
    for (const [processId, process] of bucket.servers.entries()) {
      if (Number(process?.cpuCount || 0) <= 0) continue;
      processes.set(processId, String(process?.label || processId));
    }
  }
  const currentPort = String(location.port || (location.protocol === 'https:' ? '443' : '80')).trim();
  const currentProcessId = `port:${currentPort}`;
  const fallbackSelf = {
    key: 'cpu', labelKey: 'debug.graph.series.defaultProcessCpu', unit: 'percent', linePattern: 'solid', color: jsDebugGraphProcessCpuColors.current,
    value: bucket => bucket.cpuCount ? Math.min(100, bucket.cpuTotalPercent / bucket.cpuCount) : 0,
    hasData: bucket => Number(bucket?.cpuCount || 0) > 0,
  };
  if (!processes.size) return [fallbackSelf];
  let peerIndex = 0;
  const definitions = [...processes.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]))
    .map(([processId, label]) => {
      const current = processId === currentProcessId;
      const color = current
        ? jsDebugGraphProcessCpuColors.current
        : jsDebugGraphProcessCpuColors.peers[peerIndex++ % jsDebugGraphProcessCpuColors.peers.length];
      return {
        key: `cpu:${processId}`,
        labelKey: 'debug.graph.series.processCpu',
        labelParams: {process: label},
        unit: 'percent',
        cssKey: 'cpu',
        chartMetricKey: 'cpu',
        processCpu: true,
        processId,
        linePattern: current ? 'solid' : 'dot',
        color,
        value: bucket => debugGraphProcessCpuBucketValue(bucket, processId),
        hasData: bucket => debugGraphProcessCpuBucketHasData(bucket, processId),
      };
    });
  return processes.has(currentProcessId) ? definitions : [fallbackSelf, ...definitions];
}

function debugGraphGpuDeviceSeriesDefs(buckets, metric) {
  const devices = new Map();
  for (const bucket of buckets) {
    const source = bucket.hostMetrics?.gpuDevices;
    if (!(source instanceof Map)) continue;
    for (const [key, item] of source.entries()) {
      if (Number(item?.samples || 0) <= 0) continue;
      devices.set(key, String(item.label || key));
    }
  }
  return [...devices.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]))
    .map(([deviceId, label], index) => ({
      key: `gpu:${metric}:${deviceId}`,
      label,
      unit: metric === 'gpuMemory' ? 'bytes' : 'percent',
      hostMetric: metric,
      gpuDeviceId: deviceId,
      color: jsDebugGraphGpuDeviceColors[index % jsDebugGraphGpuDeviceColors.length],
      value: bucket => debugGraphHostMetricBucketValue(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
      hasData: bucket => debugGraphHostMetricBucketHasData(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
    }));
}

function debugGraphHostMetricSeriesDefs(buckets) {
  return [
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuUtil'),
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuMemory'),
  ];
}

function debugGraphSeriesData(buckets) {
  const times = buckets.map(bucket => Number(bucket.startMs) || 0);
  const durations = buckets.map(bucket => Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs));
  const defs = [...jsDebugGraphSeries, ...debugGraphClientMetricSeriesDefs(buckets), ...debugGraphProcessCpuSeriesDefs(buckets), ...debugGraphHostMetricSeriesDefs(buckets), ...debugGraphAgentTokenSeriesDefs(buckets)];
  return defs.map(def => {
    const localizedDef = {...def, label: debugGraphLocalizedLabel(def)};
    const values = buckets.map(bucket => def.value(bucket));
    const hasDataValues = buckets.map(bucket => def.hasData(bucket));
    const sampleValues = values.filter((_value, index) => hasDataValues[index]);
    const sampleTimes = times.filter((_time, index) => hasDataValues[index]);
    const samples = sampleValues.length;
    const max = Math.max(0, ...sampleValues);
    const current = sampleValues.length ? sampleValues[sampleValues.length - 1] : 0;
    const movingAverageSamples = Number(def.movingAverageSamples || 0);
    const movingAverageValues = movingAverageSamples > 0 ? debugGraphMovingAverageValues(sampleValues, movingAverageSamples) : [];
    return {...localizedDef, values, times, durations, hasDataValues, movingAverageValues, movingAverageTimes: sampleTimes, movingAverageSamples, max, current, samples};
  });
}

function debugGraphResolutionLabelHtml(nowMs = Date.now()) {
  const domain = debugGraphDomain(nowMs);
  const defaultSeconds = debugGraphBucketDurationForTime(domain.startMs, nowMs) / 1000;
  const resolutionSeconds = debugGraphDisplayResolutionMs(domain, 0, nowMs) / 1000;
  const choices = [1, 2, 5, 10, 30, 60, 120, 300, 600];
  const retainedSeconds = debugGraphBucketDurationForTime(domain.startMs, nowMs) / 1000;
  const availableChoices = choices.filter(value => value >= retainedSeconds && value * 10 <= domain.rangeSeconds);
  const overrideSeconds = availableChoices.includes(Number(jsDebugGraphResolutionOverrideSeconds)) ? Number(jsDebugGraphResolutionOverrideSeconds) : 0;
  return `<label class="js-debug-resolution-label" data-js-debug-resolution data-js-debug-resolution-seconds="${esc(resolutionSeconds)}">${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}<select data-js-debug-resolution-override aria-label="${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}"><option value="0"${overrideSeconds === 0 ? ' selected' : ''}>AUTO</option>${availableChoices.map(value => `<option value="${value}"${overrideSeconds === value ? ' selected' : ''}>${value}s</option>`).join('')}</select></label>`;
}

function debugGraphRangeControlsHtml(nowMs = Date.now()) {
  const activeRange = activeJsDebugGraphRangeSeconds(nowMs);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (!options.length) return '';
  const sliderId = 'js-debug-range-options';
  const value = jsDebugGraphRangeOptionIndex(activeRange, nowMs);
  const zoomed = debugGraphZoomDomainValid();
  const rangeLabel = zoomed ? t('debug.graph.control.zoom') : jsDebugGraphRangeLabel(activeRange, nowMs);
  return `<div class="js-debug-range-slider-control" data-js-debug-range-control>
    <span class="js-debug-range-prefix" aria-hidden="true">${esc(t('debug.graph.control.timeRange'))}</span>
    <input class="js-debug-range-slider" type="range" min="0" max="${esc(Math.max(0, options.length - 1))}" step="any" value="${esc(value)}" list="${esc(sliderId)}" data-js-debug-range-slider aria-label="${esc(t('debug.graph.control.timeRange'))}">
    <datalist id="${esc(sliderId)}">${options.map((option, index) => `<option value="${esc(index)}" label="${esc(option.label)}" data-js-debug-range="${esc(option.seconds)}"></option>`).join('')}</datalist>
    <span class="js-debug-range-label" data-js-debug-range-label>${esc(rangeLabel)}</span>
    ${zoomed ? `<button type="button" class="js-debug-zoom-reset" data-js-debug-zoom-reset>${esc(t('common.reset'))}</button>` : ''}
  </div>`;
}

function debugGraphHiddenChartsHtml() {
  const hiddenGroups = jsDebugGraphChartGroups.filter(group => !debugGraphChartVisible(group.key));
  if (!hiddenGroups.length) return '';
  return `<div class="js-debug-hidden-charts" role="group" aria-label="${esc(t('debug.graph.control.charts'))}">
    <span class="js-debug-hidden-charts-icon" aria-hidden="true">▣</span>
    ${hiddenGroups.map(group => `<button type="button" class="js-debug-hidden-chart control-active-hover" data-js-debug-chart-restore="${esc(group.key)}" aria-label="${esc(debugGraphLocalizedLabel(group))}" title="${esc(debugGraphLocalizedLabel(group))}">↗ ${esc(debugGraphLocalizedLabel(group))}</button>`).join('')}
  </div>`;
}

function debugGraphControlsHtml(nowMs = Date.now()) {
  return `<div class="js-debug-graph-controls">
    ${debugGraphRangeControlsHtml(nowMs)}
    ${debugGraphResolutionLabelHtml(nowMs)}
    <div class="js-debug-chart-layout-control" role="group" aria-label="${esc(t('debug.graph.control.charts'))}"><span>${esc(t('debug.graph.control.charts'))}:</span>${['AUTO', 'S', 'M', 'L', 'MAX'].map((label, value) => `<button type="button" data-js-debug-chart-layout="${value}" aria-pressed="${jsDebugGraphChartLayout === value ? 'true' : 'false'}">${label}</button>`).join('')}</div>
    ${debugGraphHiddenChartsHtml()}
  </div>`;
}

function debugGraphLocalDateKey(ms) {
  if (!Number.isFinite(ms)) return '';
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '';
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()]
    .map((value, index) => String(value).padStart(index === 0 ? 4 : 2, '0'))
    .join('-');
}

function debugGraphTimeLabel(ms, {includeDate = false, includeSeconds = !includeDate} = {}) {
  if (!Number.isFinite(ms)) return '';
  if (typeof localizedDateTimeFormat === 'function') {
    const options = includeDate
      ? {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}
      : {hour: '2-digit', minute: '2-digit'};
    if (includeSeconds) options.second = '2-digit';
    const localized = localizedDateTimeFormat(ms / 1000, options);
    if (localized) return localized;
  }
  const date = new Date(ms);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  const time = includeSeconds ? `${hours}:${minutes}:${seconds}` : `${hours}:${minutes}`;
  return includeDate ? `${debugGraphLocalDateKey(ms)} ${time}` : time;
}

function debugGraphExactTimeLabel(ms) {
  if (!Number.isFinite(ms)) return '';
  const localized = localizedDateTimeFormat(ms / 1000, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  return localized || debugGraphTimeLabel(ms, {includeDate: true, includeSeconds: true});
}

function debugGraphSeriesTimeMs(series, index) {
  const times = Array.isArray(series.times) ? series.times : [];
  const value = Number(times[index]);
  return Number.isFinite(value) ? value : NaN;
}

function debugGraphPolylinePoints(values, times, chartMax, domain, hasDataValues = null) {
  return debugGraphPolylinePointSegments(values, times, chartMax, domain, hasDataValues).map(segment => segment.join(' ')).join(' ');
}

function debugGraphPolylinePointSegments(values, times, chartMax, domain, hasDataValues = null, durations = [], gapThresholdMs = 0) {
  const segments = [];
  let current = [];
  let previousDataEndMs = NaN;
  values.forEach((value, index) => {
    if (hasDataValues && hasDataValues[index] !== true) {
      if (gapThresholdMs <= 0 && current.length) {
        segments.push(current);
        current = [];
      }
      return;
    }
    const timeMs = Number(times[index]);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(durations[index]) || jsDebugGraphRawBucketMs);
    if (gapThresholdMs > 0 && current.length && Number.isFinite(previousDataEndMs) && Number.isFinite(timeMs) && timeMs - previousDataEndMs >= gapThresholdMs) {
      segments.push(current);
      current = [];
    }
    current.push(debugGraphPointForValue(value, timeMs, chartMax, domain).join(','));
    previousDataEndMs = Number.isFinite(timeMs) ? timeMs + durationMs : NaN;
  });
  if (current.length) segments.push(current);
  return segments;
}

function debugGraphPointForValue(value, timeMs, chartMax, domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  const rawX = Number.isFinite(Number(timeMs)) && Number.isFinite(startMs) && Number.isFinite(endMs)
    ? ((Number(timeMs) - startMs) / spanMs) * jsDebugGraphGeometry.width
    : jsDebugGraphGeometry.width;
  const x = Math.max(0, Math.min(jsDebugGraphGeometry.width, rawX));
  const y = debugGraphPlotYForValue(value, chartMax);
  return [x.toFixed(1), y.toFixed(1)];
}

function debugGraphPlotYForValue(value, chartMax) {
  const max = Math.max(Number(chartMax) || 0, 1);
  const normalized = Math.max(0, Math.min(1, (Number(value) || 0) / max));
  return jsDebugGraphGeometry.plotTop + ((1 - normalized) * jsDebugGraphGeometry.plotHeight);
}

function debugGraphXForTime(timeMs, domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  if (!Number.isFinite(Number(timeMs)) || !Number.isFinite(startMs) || !Number.isFinite(endMs)) return 0;
  return Math.max(0, Math.min(jsDebugGraphGeometry.width, ((Number(timeMs) - startMs) / spanMs) * jsDebugGraphGeometry.width));
}

function debugGraphDisconnectedRanges(buckets, domain) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const ranges = [];
  for (const bucket of buckets || []) {
    const startMs = Number(bucket?.startMs);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    const disconnectedMs = Math.min(durationMs, Math.max(0, Number(bucket?.disconnectedMs || 0)));
    if (!Number.isFinite(startMs) || disconnectedMs <= 0) continue;
    const rangeStart = Math.max(domainStart, startMs);
    const rangeEnd = Math.min(domainEnd, startMs + disconnectedMs);
    if (rangeEnd <= rangeStart) continue;
    ranges.push({startMs: rangeStart, endMs: rangeEnd});
  }
  return debugGraphMergeTimeRanges(ranges, domain)
    .map(range => ({...range, disconnectedMs: range.endMs - range.startMs}));
}

function debugGraphDisconnectedRectsHtml(buckets, domain, ranges = null) {
  const disconnectedRanges = Array.isArray(ranges) ? ranges : debugGraphDisconnectedRanges(buckets, domain);
  return disconnectedRanges.map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    const width = Math.max(1.5, x2 - x1);
    const title = t('debug.graph.badConnection', {duration: debugGraphTerseTimeText(range.disconnectedMs)});
    return debugGraphPlotOverlayRectHtml('js-debug-disconnected-range', 'data-js-debug-disconnected-range', index, x1, width, title);
  }).join('');
}

function debugGraphBucketRanges(buckets) {
  return (buckets || [])
    .map(bucket => {
      const startMs = Number(bucket?.startMs);
      const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
      return Number.isFinite(startMs) ? {bucket, startMs, endMs: startMs + durationMs, durationMs} : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.startMs - b.startMs);
}

function debugGraphMergeTimeRanges(ranges, domain = null) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  const hasDomain = Number.isFinite(domainStart) && Number.isFinite(domainEnd) && domainEnd > domainStart;
  const normalized = (ranges || [])
    .map(range => {
      const rawStart = Number(range?.startMs);
      const rawEnd = Number(range?.endMs);
      if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd) || rawEnd <= rawStart) return null;
      const startMs = hasDomain ? Math.max(domainStart, rawStart) : rawStart;
      const endMs = hasDomain ? Math.min(domainEnd, rawEnd) : rawEnd;
      return endMs > startMs ? {startMs, endMs} : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);
  const merged = [];
  for (const range of normalized) {
    const previous = merged.at(-1);
    if (previous && range.startMs <= previous.endMs + 1) previous.endMs = Math.max(previous.endMs, range.endMs);
    else merged.push({...range});
  }
  return merged;
}

function debugGraphComplementTimeRanges(ranges, domain) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const gaps = [];
  let cursor = domainStart;
  for (const range of debugGraphMergeTimeRanges(ranges, domain)) {
    if (range.startMs > cursor) gaps.push({startMs: cursor, endMs: range.startMs});
    cursor = Math.max(cursor, range.endMs);
  }
  if (cursor < domainEnd) gaps.push({startMs: cursor, endMs: domainEnd});
  return gaps;
}

function debugGraphCurrentClientSeriesItems(seriesItems) {
  const items = Array.isArray(seriesItems) ? seriesItems.filter(Boolean) : [];
  const currentClientItems = items.filter(series => series.clientMetric === true && series.clientAggregate === jsDebugGraphThisClientAggregate);
  return currentClientItems.length ? currentClientItems : items;
}

function debugGraphCurrentClientHeartbeatCount(bucket) {
  const clientBucket = bucket?.clients instanceof Map ? bucket.clients.get(jsDebugStatsClientIdForRequest()) : null;
  return Number(clientBucket?.heartbeatCount ?? bucket?.heartbeatCount ?? 0);
}

function debugGraphCommunicationGapThresholdMs(seriesItems) {
  const displayResolutionMs = Math.max(jsDebugGraphRawBucketMs, ...(seriesItems || []).flatMap(series => series?.durations || []));
  return jsDebugStatsHistoryFlushMs + Math.min(jsDebugStatsHistoryFlushMs, displayResolutionMs);
}

function debugGraphNoDataRuns(buckets, domain, seriesItems) {
  const items = Array.isArray(seriesItems) ? seriesItems.filter(Boolean) : [];
  if (!items.length) return [];
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const perf = clientPerfStart('statsNoDataSweep');
  try {
    const hasCurrentClientHeartbeat = debugGraphBucketRanges(buckets)
      .some(item => debugGraphCurrentClientHeartbeatCount(item.bucket) > 0);
    const dataRanges = debugGraphBucketRanges(buckets)
      .filter(item => hasCurrentClientHeartbeat
        ? debugGraphCurrentClientHeartbeatCount(item.bucket) > 0
        : items.some(series => series.hasData(item.bucket)))
      .map(item => ({startMs: item.startMs, endMs: item.endMs}));
    const disconnectedRanges = debugGraphDisconnectedRanges(buckets, domain);
    return debugGraphComplementTimeRanges([...dataRanges, ...disconnectedRanges], domain)
      .map(range => ({...range, startMs: range.startMs + jsDebugGraphNoDataOverlayDelayMs}))
      .filter(range => range.endMs > range.startMs);
  } finally {
    clientPerfEnd(perf, {rows: (buckets || []).length});
  }
}

function debugGraphNoDataRectsHtml(buckets, domain, seriesItems) {
  return debugGraphNoDataRuns(buckets, domain, seriesItems).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    const width = Math.max(1.5, x2 - x1);
    return debugGraphPlotOverlayRectHtml('js-debug-no-data-range', 'data-js-debug-no-data-range', index, x1, width, t('debug.noCommunicationData'));
  }).join('');
}

function debugGraphPlotOverlayRectHtml(className, attribute, index, x, width, title) {
  return `<rect class="${esc(className)}" ${attribute}="${esc(index)}" x="${esc(x.toFixed(1))}" y="${esc(jsDebugGraphGeometry.plotTop)}" width="${esc(width.toFixed(1))}" height="${esc(jsDebugGraphGeometry.plotHeight)}"><title>${esc(title)}</title></rect>`;
}

function debugGraphSeriesPlotValues(series) {
  return Array.isArray(series.plotValues) ? series.plotValues : (series.values || []);
}

function debugGraphSeriesPlotHasDataValues(series) {
  return Array.isArray(series.plotHasDataValues) ? series.plotHasDataValues : (series.hasDataValues || null);
}

function debugGraphSeriesClassKey(series) {
  return String(series?.cssKey || series?.key || '').replace(/[^A-Za-z0-9_-]/g, '-');
}

function debugGraphAgentTokenPatternIndex(series) {
  if (series?.agentTokenSeries !== true) return -1;
  const index = Math.floor(Number(series.agentTokenPatternIndex));
  return Number.isFinite(index) && index >= 0 ? index % jsDebugGraphAgentTokenPatternCount : 0;
}

function debugGraphAgentTokenPatternId(series, suffix = '') {
  const patternIndex = debugGraphAgentTokenPatternIndex(series);
  if (patternIndex < 0) return '';
  const key = String(series?.agentTokenKey || series?.key || 'series')
    .replace(/[^A-Za-z0-9_-]/g, '-')
    .slice(-64);
  return `js-debug-agent-token-pattern-${patternIndex}-${key || 'series'}${suffix}`;
}

function debugGraphAgentTokenPatternShapeHtml(patternIndex) {
  return jsDebugGraphAgentTokenPatternShapes[patternIndex] || '';
}

function debugGraphAgentTokenPatternDefinitionHtml(series, options = {}) {
  const patternIndex = debugGraphAgentTokenPatternIndex(series);
  if (patternIndex < 0) return '';
  const legend = options.legend === true;
  const patternId = debugGraphAgentTokenPatternId(series, legend ? '-legend' : '');
  const shape = debugGraphAgentTokenPatternShapeHtml(patternIndex);
  const dataAttr = legend ? 'data-js-debug-token-legend-pattern-def' : 'data-js-debug-token-pattern-def';
  return `<pattern id="${esc(patternId)}" ${dataAttr}="${esc(patternIndex)}" patternUnits="userSpaceOnUse" width="6" height="2"${debugGraphSeriesStyleAttr(series)}><rect width="6" height="2" fill="var(--js-debug-series-color, var(--accent-sky-strong))"></rect>${shape ? `<g class="js-debug-agent-token-pattern-ink">${shape}</g>` : ''}</pattern>`;
}

function debugGraphAgentTokenPatternDefsHtml(seriesItems) {
  const patterns = (seriesItems || [])
    .filter(series => debugGraphAgentTokenPatternIndex(series) >= 0)
    .map(series => debugGraphAgentTokenPatternDefinitionHtml(series));
  return patterns.length ? `<defs>${patterns.join('')}</defs>` : '';
}

function debugGraphAgentTokenLegendSwatchHtml(series) {
  const patternId = debugGraphAgentTokenPatternId(series, '-legend');
  if (!patternId) return '';
  return `<svg class="js-debug-legend-token-swatch" viewBox="0 0 10 10" aria-hidden="true"${debugGraphSeriesStyleAttr(series)}><defs>${debugGraphAgentTokenPatternDefinitionHtml(series, {legend: true})}</defs><rect width="10" height="10" rx="1.5" fill="url(#${esc(patternId)})"></rect></svg>`;
}

function debugGraphSeriesStyleAttr(series, {barPattern = false} = {}) {
  const color = String(series?.color || '').trim();
  const declarations = color ? [`--js-debug-series-color: ${color}`] : [];
  const patternId = barPattern ? debugGraphAgentTokenPatternId(series) : '';
  if (patternId) declarations.push(`fill: url(#${patternId})`);
  return declarations.length ? ` style="${esc(`${declarations.join('; ')};`)}"` : '';
}

function debugGraphSeriesClientAttrs(series) {
  if (series?.clientMetric !== true) return '';
  const clientId = String(series.clientId || 'this');
  return ` data-js-debug-client-series="${esc(clientId)}" data-js-debug-client-line="${esc(series.clientLinePattern || 'solid')}"`;
}

function debugGraphSeriesLinePattern(series) {
  const pattern = String(series?.linePattern || (series?.clientMetric === true ? series.clientLinePattern : '') || '').trim();
  return ['solid', 'dot', 'dash'].includes(pattern) ? pattern : '';
}

function debugGraphSeriesLinePatternAttrs(series) {
  const pattern = debugGraphSeriesLinePattern(series);
  return pattern ? ` data-js-debug-line-pattern="${esc(pattern)}"` : '';
}

function debugGraphSeriesLineClassName(series, extraClass = '') {
  const classes = ['js-debug-line', `js-debug-line--${debugGraphSeriesClassKey(series)}`];
  const linePattern = debugGraphSeriesLinePattern(series);
  if (linePattern) classes.push('js-debug-line--pattern', `js-debug-line--pattern-${linePattern}`);
  if (series?.clientMetric === true) {
    classes.push('js-debug-line--client', `js-debug-line--client-${series.clientLinePattern || 'solid'}`);
  }
  if (extraClass) classes.push(extraClass);
  return classes.join(' ');
}

function debugGraphSeriesTokenAgentAttrs(series) {
  if (series?.agentTokenSeries !== true) return '';
  return ` data-js-debug-token-agent="${esc(series.agentTokenKey || '')}" data-js-debug-token-agent-label="${esc(series.label || '')}" data-js-debug-token-pattern="${esc(debugGraphAgentTokenPatternIndex(series))}"`;
}

function debugGraphPolylineHtml(series, chartMax, domain) {
  const gapThresholdMs = series?.clientMetric === true ? debugGraphCommunicationGapThresholdMs([series]) : 0;
  return debugGraphPolylinePointSegments(
    debugGraphSeriesPlotValues(series),
    series.times || [],
    chartMax,
    domain,
    debugGraphSeriesPlotHasDataValues(series),
    series.durations || [],
    gapThresholdMs,
  ).map((points, index) => {
    if (!points.length) return '';
    const segmentAttr = index > 0 ? ` data-js-debug-series-segment="${esc(index)}"` : '';
    return `<polyline class="${esc(debugGraphSeriesLineClassName(series))}" data-js-debug-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}${debugGraphSeriesLinePatternAttrs(series)}${segmentAttr} points="${esc(points.join(' '))}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.label)}</title></polyline>`;
  }).join('');
}

function debugGraphAreaPathHtml(series, chartMax, domain) {
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const pointIndexes = debugGraphSeriesPlotValues(series)
    .map((_value, index) => index)
    .filter(index => !hasDataValues || hasDataValues[index] === true);
  const upperPoints = pointIndexes.map(index => debugGraphPointForValue(debugGraphSeriesPlotValues(series)[index], debugGraphSeriesTimeMs(series, index), chartMax, domain));
  if (!upperPoints.length) return '';
  const baseline = jsDebugGraphGeometry.plotBottom;
  const lowerValues = Array.isArray(series.stackBaseValues) ? series.stackBaseValues : null;
  const lowerPoints = lowerValues
    ? pointIndexes.map(index => debugGraphPointForValue(lowerValues[index], debugGraphSeriesTimeMs(series, index), chartMax, domain))
    : upperPoints.map(point => [point[0], baseline.toFixed(1)]);
  const firstLower = lowerPoints[0] || [upperPoints[0][0], baseline.toFixed(1)];
  const path = [
    `M ${firstLower[0]},${firstLower[1]}`,
    ...upperPoints.map(point => `L ${point[0]},${point[1]}`),
    ...lowerPoints.slice().reverse().map(point => `L ${point[0]},${point[1]}`),
    'Z',
  ].join(' ');
  const stacked = lowerValues ? ` data-js-debug-area-stacked="${esc(series.key)}"` : '';
  const plotCurrent = debugGraphSeriesPlotValues(series).at(-1);
  const total = Number.isFinite(Number(plotCurrent)) ? ` data-js-debug-area-total="${esc(Number(plotCurrent))}"` : '';
  return `<path class="js-debug-area js-debug-area--${esc(debugGraphSeriesClassKey(series))}" data-js-debug-area-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked}${total} d="${esc(path)}"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.label)}</title></path>`;
}

function debugGraphBarRectsHtml(series, chartMax, domain) {
  const values = debugGraphSeriesPlotValues(series);
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const lowerValues = Array.isArray(series.stackBaseValues) ? series.stackBaseValues : null;
  const durations = Array.isArray(series.durations) ? series.durations : [];
  const classKey = debugGraphSeriesClassKey(series);
  return values.map((value, index) => {
    if (hasDataValues && hasDataValues[index] !== true) return '';
    const topValue = Math.max(0, Number(value) || 0);
    const bottomValue = Math.max(0, Number(lowerValues?.[index] || 0));
    if (topValue <= bottomValue && series.zeroBar !== true) return '';
    const startMs = debugGraphSeriesTimeMs(series, index);
    const durationMs = Math.max(1000, Number(durations[index] || jsDebugGraphAgentTokenBucketSeconds * 1000));
    const x1 = debugGraphXForTime(startMs, domain);
    const x2 = debugGraphXForTime(startMs + durationMs, domain);
    const slotWidth = Math.max(0, x2 - x1);
    const gap = jsDebugAgentStatusSeriesKeys.includes(series.key) ? 0 : Math.min(0.15, slotWidth * 0.05);
    const x = x1 + gap / 2;
    const width = Math.max(0, slotWidth - gap);
    const vertical = debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, series.zeroBar === true);
    const stacked = lowerValues ? ` data-js-debug-bar-stacked="${esc(series.key)}"` : '';
    return `<rect class="js-debug-bar js-debug-bar--${esc(classKey)}" data-js-debug-bar-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked} data-js-debug-bar-total="${esc(topValue)}" data-js-debug-bar-gap="${esc(gap.toFixed(2))}" x="${esc(x.toFixed(2))}" y="${esc(vertical.y.toFixed(2))}" width="${esc(width.toFixed(2))}" height="${esc(vertical.height.toFixed(2))}"${debugGraphSeriesStyleAttr(series, {barPattern: true})}><title>${esc(series.label)}</title></rect>`;
  }).join('');
}

function debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, zeroBar = false) {
  const top = debugGraphPlotYForValue(topValue, chartMax);
  const bottom = debugGraphPlotYForValue(bottomValue, chartMax);
  const height = Math.max(0, bottom - top);
  if (height > 0 || !zeroBar) return {y: top, height};
  const zeroHeight = 0.75;
  return {y: bottom - zeroHeight, height: zeroHeight};
}

function debugGraphMovingAveragePolylineHtml(series, chartMax, domain) {
  const sampleCount = Number(series?.movingAverageSamples || 0);
  if (sampleCount <= 0) return '';
  const points = debugGraphPolylinePoints(series.movingAverageValues || [], series.movingAverageTimes || [], chartMax, domain);
  if (!points) return '';
  const title = t('debug.graph.movingAverage', {label: series.label, count: sampleCount});
  return `<polyline class="${esc(debugGraphSeriesLineClassName(series, 'js-debug-line--moving-average'))}" data-js-debug-moving-average="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}${debugGraphSeriesLinePatternAttrs(series)} data-js-debug-moving-average-samples="${esc(sampleCount)}" points="${esc(points)}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(title)}</title></polyline>`;
}

function debugGraphInteractionOverlayHtml() {
  return `<rect class="js-debug-selection-rect" data-js-debug-selection-rect x="0" y="${esc(jsDebugGraphGeometry.plotTop)}" width="0" height="${esc(jsDebugGraphGeometry.plotHeight)}"></rect><line class="js-debug-hover-line" data-js-debug-hover-line x1="0" y1="${esc(jsDebugGraphGeometry.plotTop)}" x2="0" y2="${esc(jsDebugGraphGeometry.hoverBottom)}" vector-effect="non-scaling-stroke"></line>`;
}

function debugGraphLegendHtml(seriesItems) {
  return `<div class="js-debug-legend" aria-label="${esc(t('debug.summary'))}">
    ${seriesItems.map(series => `<div class="js-debug-legend-item" data-js-debug-legend="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}>${debugGraphLegendSwatchHtml(series)}<span>${esc(series.label)}</span></div>`).join('')}
  </div>`;
}

function debugGraphLegendSwatchHtml(series) {
  if (series?.agentTokenSeries === true) return debugGraphAgentTokenLegendSwatchHtml(series);
  if (series?.clientMetric === true || series?.processCpu === true || series?.key === 'systemCpu' || series?.key === 'systemMemory' || debugGraphSeriesLinePattern(series)) {
    return `<svg class="js-debug-legend-line" viewBox="0 0 18 4" aria-hidden="true"><line class="${esc(debugGraphSeriesLineClassName(series))}"${debugGraphSeriesLinePatternAttrs(series)} x1="0" y1="2" x2="18" y2="2" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}></line></svg>`;
  }
  return `<span class="js-debug-legend-swatch js-debug-legend-swatch--${esc(debugGraphSeriesClassKey(series))}"${debugGraphSeriesStyleAttr(series)}></span>`;
}

function debugGraphIntegerAxisValues(max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  if (axisMax <= 0) return [0];
  const stride = axisMax <= 10 ? 1 : Math.max(1, Math.ceil(axisMax / 8));
  const values = [];
  for (let value = axisMax; value >= 0; value -= stride) values.push(value);
  if (values.at(-1) !== 0) values.push(0);
  return values;
}

function debugGraphIntegerGridValues(max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  return Array.from({length: axisMax + 1}, (_unused, index) => axisMax - index);
}

function debugGraphIntegerAxisHtml(group, max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  const ticks = debugGraphIntegerAxisValues(axisMax);
  return `<div class="js-debug-y-axis js-debug-y-axis--integer" data-js-debug-axis="${esc(group.key)}">
    ${ticks.map(value => {
      const marker = value === axisMax
        ? ` data-js-debug-axis-max="${esc(group.key)}"`
        : value === 0
          ? ` data-js-debug-axis-zero="${esc(group.key)}"`
          : '';
      return `<span data-js-debug-axis-tick="${esc(group.key)}" data-js-debug-axis-value="${esc(value)}"${marker}${debugGraphAxisTickStyle(value, axisMax)}>${esc(debugGraphAxisValueText(value, group.unit))}</span>`;
    }).join('')}
  </div>`;
}

function debugGraphGridLineY(value, chartMax) {
  return debugGraphPlotYForValue(value, chartMax);
}

function debugGraphAxisTickStyle(value, chartMax) {
  const percent = (debugGraphGridLineY(value, chartMax) / jsDebugGraphGeometry.height) * 100;
  return ` style="--js-debug-axis-y: ${esc(percent.toFixed(3))}%;"`;
}

function debugGraphGridLinesHtml(group, axisMax) {
  const max = Math.max(0, Number(axisMax) || 0);
  const fallbackMax = max > 0 ? max : 1;
  const values = group.integerGridLines === true
    ? debugGraphIntegerGridValues(max)
    : [fallbackMax, fallbackMax / 2, 0];
  return values.map(value => {
    const y = debugGraphGridLineY(value, max).toFixed(1);
    const axisValue = group.integerGridLines === true ? ` data-js-debug-grid-value="${esc(value)}"` : '';
    return `<line class="js-debug-grid-line${group.integerGridLines === true ? ' js-debug-grid-line--integer' : ''}" data-js-debug-grid-line="${esc(group.key)}"${axisValue} x1="0" y1="${esc(y)}" x2="${esc(jsDebugGraphGeometry.width)}" y2="${esc(y)}" vector-effect="non-scaling-stroke"></line>`;
  }).join('');
}

function debugGraphAxisHtml(group, max) {
  const axisMax = Math.max(0, Number(max) || 0);
  if (group.integerAxis === true) return debugGraphIntegerAxisHtml(group, axisMax);
  const positionMax = axisMax > 0 ? axisMax : 1;
  return `<div class="js-debug-y-axis" data-js-debug-axis="${esc(group.key)}">
    <span data-js-debug-axis-max="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax, positionMax)}>${esc(debugGraphAxisValueText(axisMax, group.unit))}</span>
    <span data-js-debug-axis-mid="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax / 2, positionMax)}>${esc(debugGraphAxisValueText(axisMax / 2, group.unit))}</span>
    <span data-js-debug-axis-zero="${esc(group.key)}"${debugGraphAxisTickStyle(0, positionMax)}>${esc(debugGraphAxisValueText(0, group.unit))}</span>
  </div>`;
}

function debugGraphXAxisHtml(domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return '';
  const ticks = [
    {name: 'start', ms: startMs},
    {name: 'mid', ms: startMs + ((endMs - startMs) / 2)},
    {name: 'end', ms: endMs},
  ];
  const includeDate = debugGraphLocalDateKey(startMs) !== debugGraphLocalDateKey(endMs);
  return `<div class="js-debug-x-axis" data-js-debug-x-axis>
    ${ticks.map(tick => `<span data-js-debug-x-tick="${esc(tick.name)}"${includeDate ? ` data-js-debug-x-date="${esc(debugGraphLocalDateKey(tick.ms))}"` : ''}>${esc(debugGraphTimeLabel(tick.ms, {includeDate}))}</span>`).join('')}
  </div>`;
}

function debugGraphGroupSeriesItems(group, seriesItems) {
  if (group.dynamicAgentTokens === true) return seriesItems.filter(series => series.agentTokenSeries === true);
  if (group.hostMetric) {
    const hostSeries = seriesItems.filter(series => series.hostMetric === group.hostMetric);
    if (group.hostMetric === 'cpu') {
      return seriesItems.filter(series => series.processCpu === true || series.key === 'cpu' || series.key === 'systemCpu');
    }
    if (hostSeries.length || group.hostMetric !== 'cpu') {
      return [...hostSeries, ...seriesItems.filter(series => group.hostMetric === 'memory' && series.key === 'systemMemory')];
    }
    // Existing history predates host process sampling. Keep its per-YOLOmux CPU lines readable
    // until those one-second buckets age out instead of rendering an empty CPU chart.
    return seriesItems.filter(series => series.processCpu === true || series.key === 'cpu' || series.key === 'systemCpu');
  }
  const seriesKeys = new Set(group.series);
  return seriesItems.filter(series => seriesKeys.has(series.chartMetricKey || (series.clientMetric === true ? series.metricKey : series.key)));
}

function debugGraphLegendSeriesItems(group, groupSeries) {
  const legendKeys = Array.isArray(group?.legendSeries) ? group.legendSeries : null;
  if (!legendKeys) return groupSeries;
  const seriesByKey = new Map(groupSeries.map(series => [series.key, series]));
  return legendKeys.map(key => seriesByKey.get(key)).filter(Boolean);
}

function debugGraphVisibleChartGroups(seriesItems) {
  return jsDebugGraphChartGroups.filter(group => {
    if (!debugGraphChartVisible(group.key)) return false;
    if (group.optional !== true) return true;
    return debugGraphGroupSeriesItems(group, seriesItems).some(series => Number(series?.samples || 0) > 0);
  });
}

function debugGraphStackedSeries(seriesItems) {
  const count = Math.max(0, ...seriesItems.map(series => (series.values || []).length));
  const totals = Array.from({length: count}, () => 0);
  return seriesItems.map(series => {
    const values = series.values || [];
    const stackBaseValues = totals.slice();
    const plotValues = values.map((value, index) => {
      const next = totals[index] + Math.max(0, Number(value) || 0);
      totals[index] = next;
      return next;
    });
    return {
      ...series,
      plotValues,
      stackBaseValues,
      plotHasDataValues: series.hasDataValues || null,
      plotMax: Math.max(0, ...plotValues),
    };
  });
}

function debugGraphChartAxisMax(group, rawMax) {
  const fixedMax = Number(group.fixedMax);
  if (Number.isFinite(fixedMax) && fixedMax > 0) return fixedMax;
  if (group.exactIntegerAxisMax === true) return Math.max(0, Math.ceil(Number(rawMax) || 0));
  return debugGraphNiceAxisMax(rawMax, group.unit);
}

function debugGraphChartCapacityMax(group, buckets) {
  if (group.capacityMetric === 'systemMemory') {
    return Math.max(0, ...(buckets || []).map(bucket => {
      const host = bucket?.hostMetrics;
      return Number(host?.systemMemoryCount || 0) > 0
        ? Number(host.systemMemoryCapacityTotalBytes || 0) / Number(host.systemMemoryCount || 1)
        : 0;
    }));
  }
  if (group.capacityMetric === 'gpuMemory') {
    return Math.max(0, ...(buckets || []).map(bucket => {
      if (!(bucket?.hostMetrics?.gpuDevices instanceof Map)) return 0;
      let total = 0;
      for (const item of bucket.hostMetrics.gpuDevices.values()) {
        if (Number(item?.samples || 0) > 0) total += Number(item.memoryCapacityTotalBytes || 0) / Number(item.samples || 1);
      }
      return total;
    }));
  }
  return 0;
}

function debugGraphBucketsForChartGroup(group, defaultBuckets, nowMs = Date.now()) {
  if (group?.key === 'agentTokens') return debugGraphAgentTokenDisplayBuckets(nowMs);
  const bucketSeconds = Number(group?.bucketSeconds);
  if (Number.isFinite(bucketSeconds) && bucketSeconds > 0) {
    return debugGraphDisplayBuckets(nowMs, {minimumResolutionSeconds: bucketSeconds, rangeSeconds: jsDebugGraphRangeSeconds});
  }
  return defaultBuckets;
}

function debugGraphHoverBucketIndex(buckets, timestamp) {
  let low = 0;
  let high = buckets.length - 1;
  let index = -1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(buckets[middle]?.startMs) <= timestamp) {
      index = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  if (index < 0) return -1;
  const bucket = buckets[index];
  const end = Number(bucket?.startMs) + Math.max(1, Number(bucket?.durationMs) || 0);
  return timestamp < end ? index : -1;
}

function debugGraphHoverValueAtTime(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data) return debugGraphValueText(0, chart?.dataset?.jsDebugChartUnit);
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  if (index < 0) return debugGraphValueText(0, data.group.unit);
  const series = data.group.key === 'activity'
    ? data.groupSeries.filter(item => item.key !== 'idleAgents')
    : data.groupSeries;
  const values = series
    .filter(item => !Array.isArray(item.hasDataValues) || item.hasDataValues[index] === true)
    .map(item => Math.max(0, Number(item.values?.[index]) || 0));
  const value = data.group.stacked === true
    ? values.reduce((total, item) => total + item, 0)
    : Math.max(0, ...values);
  return debugGraphValueText(value, data.group.unit);
}

function debugGraphChartHtml(group, seriesItems, domain, buckets = [], overlayBuckets = buckets, disconnectedRanges = null) {
  const groupLabel = debugGraphLocalizedLabel(group);
  const groupSeries = debugGraphGroupSeriesItems(group, seriesItems);
  jsDebugGraphHoverChartData.set(group.key, {buckets, group, groupSeries});
  const legendSeries = debugGraphLegendSeriesItems(group, groupSeries);
  const plottedGroupSeries = groupSeries.filter(series => series.movingAverageOnly !== true && series.overlayLineOnly !== true);
  const overlayLineSeries = groupSeries.filter(series => series.overlayLineOnly === true);
  const areaSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => series.hostMetric && series.hostProcessId) : [];
  const lineSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => !areaSeries.includes(series)) : plottedGroupSeries;
  const plotSeries = group.kind === 'area'
    ? debugGraphStackedSeries(areaSeries)
    : (group.stacked === true ? debugGraphStackedSeries(plottedGroupSeries) : plottedGroupSeries);
  const movingAverageSeries = groupSeries.filter(series => Number(series.movingAverageSamples || 0) > 0);
  const rawMax = Math.max(0, ...plotSeries.map(series => Number(series.plotMax ?? series.max) || 0), ...lineSeries.map(series => Number(series.max) || 0), debugGraphChartCapacityMax(group, buckets));
  const max = debugGraphChartAxisMax(group, rawMax);
  const axisMax = max > 0 ? max : 0;
  const chartClasses = ['js-debug-chart'];
  if (group.dynamicAgentTokens === true) chartClasses.push('js-debug-chart--token-agents');
  const bucketSeconds = Number(group.bucketSeconds);
  const bucketAttr = Number.isFinite(bucketSeconds) && bucketSeconds > 0 ? ` data-js-debug-chart-bucket-seconds="${esc(bucketSeconds)}"` : '';
  const displayedSummary = debugGraphDisplayedSummary(group, buckets);
  const displayedSummaryHtml = displayedSummary === null
    ? ''
    : `<span class="js-debug-chart-summary" data-js-debug-${esc(displayedSummary.attribute)}="${esc(displayedSummary.value)}">${esc(displayedSummary.text)}</span>`;
  const gpuUnavailable = (group.hostMetric === 'gpuUtil' || group.hostMetric === 'gpuMemory') && !groupSeries.length;
  return `<section class="${esc(chartClasses.join(' '))}" data-js-debug-chart="${esc(group.key)}" data-js-debug-chart-kind="${esc(group.kind || 'line')}" data-js-debug-chart-axis-max="${esc(axisMax)}" data-js-debug-chart-unit="${esc(group.unit || '')}"${bucketAttr}${group.stacked === true ? ' data-js-debug-chart-stacked="true"' : ''}>
    <div class="js-debug-chart-head">
      <div class="js-debug-chart-heading-row">
        <span class="js-debug-chart-title">${esc(groupLabel)}</span>
        ${displayedSummaryHtml}
        <button type="button" class="js-debug-chart-close control-active-hover" data-js-debug-chart-close="${esc(group.key)}" aria-label="${esc(t('common.close'))} ${esc(groupLabel)}" title="${esc(t('common.close'))}">×</button>
      </div>
      ${gpuUnavailable ? '' : debugGraphLegendHtml(legendSeries)}
    </div>
    ${gpuUnavailable ? `<div class="js-debug-chart-unavailable" data-js-debug-gpu-unavailable="${esc(group.key)}">${esc(t('finder.dateMode.none'))}</div>` : `<div class="js-debug-chart-body">
      ${debugGraphAxisHtml(group, axisMax)}
      <div class="js-debug-plot">
        <svg class="js-debug-line-chart" viewBox="0 0 ${esc(jsDebugGraphGeometry.width)} ${esc(jsDebugGraphGeometry.height)}" role="img" aria-label="${esc(groupLabel)}" preserveAspectRatio="none">
          ${group.kind === 'bar' ? debugGraphAgentTokenPatternDefsHtml(plotSeries) : ''}
          ${group.kind === 'area' ? plotSeries.map(series => debugGraphAreaPathHtml(series, Math.max(axisMax, 1), domain)).join('') : ''}
          ${group.kind === 'bar' ? plotSeries.map(series => debugGraphBarRectsHtml({...series, zeroBar: group.zeroBar === true}, Math.max(axisMax, 1), domain)).join('') : ''}
          ${debugGraphGridLinesHtml(group, axisMax)}
          ${group.noDataOverlay === true ? debugGraphNoDataRectsHtml(overlayBuckets, domain, debugGraphCurrentClientSeriesItems(groupSeries)) : ''}
          ${group.kind === 'bar' ? '' : (group.kind === 'area' ? lineSeries : plotSeries).map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${overlayLineSeries.map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${movingAverageSeries.map(series => debugGraphMovingAveragePolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${group.disconnectedOverlay === true ? debugGraphDisconnectedRectsHtml(overlayBuckets, domain, disconnectedRanges) : ''}
          ${debugGraphInteractionOverlayHtml()}
        </svg>
      </div>
      ${debugGraphXAxisHtml(domain)}
    </div>`}
    ${gpuUnavailable ? '' : '<div class="js-debug-hover-tooltip" data-js-debug-hover-tooltip hidden><span data-js-debug-hover-max></span><span aria-hidden="true"> · </span><time data-js-debug-hover-time></time></div>'}
  </section>`;
}

function debugGraphChartShellHtml(gridHtml = '', domain = debugGraphDomain()) {
  return `<div class="js-debug-chart-shell">
    <div class="js-debug-chart-grid" data-js-debug-chart-grid data-js-debug-chart-layout="${esc(jsDebugGraphChartLayout)}" data-js-debug-domain-start="${esc(Math.floor(domain.startMs))}" data-js-debug-domain-end="${esc(Math.floor(domain.endMs))}"${domain.zoomed ? ' data-js-debug-zoomed="true"' : ''}>${gridHtml}</div>
    ${debugGraphHistoryOverlayHtml()}
  </div>`;
}

function debugGraphSvgHtml(buckets, seriesItems, chartGroups = debugGraphVisibleChartGroups(seriesItems), nowMs = Date.now()) {
  const domain = debugGraphDomain(nowMs);
  const overlayBuckets = debugGraphSourceBuckets(domain);
  const disconnectedRanges = debugGraphDisconnectedRanges(overlayBuckets, domain);
  const gridHtml = chartGroups.map(group => {
      const groupBuckets = debugGraphBucketsForChartGroup(group, buckets, nowMs);
      const groupSeriesItems = groupBuckets === buckets ? seriesItems : debugGraphSeriesData(groupBuckets);
      return debugGraphChartHtml(group, groupSeriesItems, domain, groupBuckets, overlayBuckets, disconnectedRanges);
    }).join('');
  return debugGraphChartShellHtml(gridHtml, domain);
}

function debugGraphClassName(nowMs = Date.now()) {
  return `js-debug-graph${debugGraphDisplayBuckets(nowMs).length ? '' : ' js-debug-graph--empty'}${debugGraphZoomDomainValid() ? ' js-debug-graph--zoomed' : ''}`;
}

function debugGraphInnerHtml(nowMs = Date.now()) {
  loadJsDebugStatsUiPreferences();
  activeJsDebugGraphRangeSeconds(nowMs);
  const controls = debugGraphControlsHtml(nowMs);
  const meta = debugGraphMetaHtml();
  const clientPerf = debugClientPerfHtml();
  const buckets = debugGraphDisplayBuckets(nowMs);
  if (!buckets.length) {
    const empty = debugGraphWaitingForServerStats() ? '' : `<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
    const loadingShell = jsDebugHistoryReadiness.overlayVisible === true || jsDebugHistoryReadinessBusy()
      ? debugGraphChartShellHtml('', debugGraphDomain(nowMs))
      : '';
    return `${controls}${meta}${clientPerf}${empty}${loadingShell}`;
  }
  const seriesItems = debugGraphSeriesData(buckets);
  const chartGroups = debugGraphVisibleChartGroups(seriesItems);
  return `${controls}${meta}${clientPerf}${debugGraphSvgHtml(buckets, seriesItems, chartGroups, nowMs)}`;
}

function debugGraphHtml() {
  const nowMs = Date.now();
  return `<div class="${debugGraphClassName(nowMs)}" data-js-debug-graph data-js-debug-graph-rendered-at="${esc(nowMs)}" data-js-debug-history-state="${esc(jsDebugHistoryReadiness.phase)}" aria-busy="${jsDebugHistoryReadinessBusy() ? 'true' : 'false'}" aria-label="${esc(t('debug.summary'))}">${debugGraphInnerHtml(nowMs)}</div>`;
}

function debugGraphBucketSummary(nowMs = Date.now()) {
  activeJsDebugGraphRangeSeconds(nowMs);
  const domain = debugGraphDomain(nowMs, jsDebugGraphRangeSeconds);
  const buckets = debugGraphDisplayBuckets(nowMs, {rangeSeconds: jsDebugGraphRangeSeconds});
  const availableRangeSeconds = debugGraphAvailableRangeOptions(nowMs).map(option => option.seconds);
  return {
    rawBuckets: jsDebugGraphRawBuckets.size,
    rollupBuckets: jsDebugGraphRollupBuckets.size,
    middleBuckets: [...jsDebugGraphRollupBuckets.values()].filter(bucket => bucket.durationMs === jsDebugGraphMiddleBucketMs).length,
    oldBuckets: [...jsDebugGraphRollupBuckets.values()].filter(bucket => bucket.durationMs === jsDebugGraphRollupBucketMs).length,
    tierBucketCounts: jsDebugGraphTiers.map(tier => [...jsDebugGraphRawBuckets.values(), ...jsDebugGraphRollupBuckets.values()].filter(bucket => bucket.durationMs === tier.bucketMs).length),
    displayBucketSeconds: [...new Set(buckets.map(bucket => bucket.durationMs / 1000))].sort((left, right) => left - right),
    agentTokenBuckets: jsDebugGraphAgentTokenBuckets.size,
    agentTokenResolutionSeconds: jsDebugStatsAgentTokenResolutionSeconds,
    agentTokenSchemaVersion: jsDebugStatsAgentTokenSchemaVersion,
    displayBuckets: buckets.length,
    eventRefs: jsDebugGraphEventRecords.size,
    resolutionSeconds: debugGraphDisplayResolutionMs(domain, 0, nowMs) / 1000,
    rangeSeconds: jsDebugGraphRangeSeconds,
    zoomed: debugGraphZoomDomainValid(),
    zoomRangeSeconds: debugGraphZoomDomainValid() ? (Number(jsDebugGraphZoomDomain.endMs) - Number(jsDebugGraphZoomDomain.startMs)) / 1000 : 0,
    availableRangeSeconds,
    retentionHours: jsDebugGraphRetentionMs / 60 / 60 / 1000,
    rawWindowSeconds: jsDebugGraphRawWindowMs / 1000,
    middleWindowSeconds: jsDebugGraphMiddleWindowMs / 1000,
    middleBucketSeconds: jsDebugGraphMiddleBucketMs / 1000,
    rollupBucketSeconds: jsDebugGraphRollupBucketMs / 1000,
    tiers: jsDebugGraphTiers.map(tier => ({maxAgeSeconds: tier.maxAgeMs / 1000, bucketSeconds: tier.bucketMs / 1000})),
    serverSequence: jsDebugStatsServerSequence,
    pendingServerBuckets: jsDebugGraphPendingServerBuckets.size,
    disconnectedBuckets: buckets.filter(bucket => Number(bucket.disconnectedMs || 0) > 0).length,
    clientId: jsDebugStatsClientIdForRequest(),
    uptimeSeconds: jsDebugStatsServerUptimeSeconds,
    series: jsDebugGraphSeries.map(series => series.key),
    charts: debugGraphVisibleChartGroups(debugGraphSeriesData(buckets)).map(group => group.key),
  };
}

function jsDebugStatsPanelVisible() {
  return debugModeEnabled === true
    && document.visibilityState !== 'hidden'
    && typeof itemIsActivePaneTab === 'function'
    && itemIsActivePaneTab(debugPaneItemId);
}

function jsDebugStatsTokenConsumerEnabled() {
  return jsDebugStatsPanelVisible();
}

function stopJsDebugStatsPolling() {
  clearRuntimeInterval('debug-stats');
}

function jsDebugStatsPollIntervalMs() {
  return jsDebugStatsPollState.firstSampleReceived ? jsDebugStatsPollMs : jsDebugStatsPollFastMs;
}

function armJsDebugStatsPolling({pollNow = false, forceGraphRefresh = false} = {}) {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  stopJsDebugStatsPolling();
  if (pollNow) void pollJsDebugStatsSample({forceGraphRefresh});
  resetRuntimeInterval('debug-stats', pollJsDebugStatsSample, jsDebugStatsPollIntervalMs());
}

async function fetchJsDebugStatsJson(url, options = {}) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const phaseTimings = {};
  let timeoutId = null;
  try {
    if (controller && typeof setTimeout === 'function') {
      timeoutId = setTimeout(() => controller.abort(), jsDebugStatsPollTimeoutMs);
    }
    return await apiFetchJsonQuiet(url, {...options, ...(controller ? {signal: controller.signal} : {})}, phaseTimings);
  } finally {
    if (timeoutId !== null && typeof clearTimeout === 'function') clearTimeout(timeoutId);
    if (Number.isFinite(phaseTimings.fetchMs)) recordClientPerfCounter('statsHistoryFetch', phaseTimings.fetchMs);
    if (Number.isFinite(phaseTimings.parseMs)) recordClientPerfCounter('statsHistoryParse', phaseTimings.parseMs);
  }
}

async function paintJsDebugHistoryResponse(generation, requestedRangeSeconds, requestedStartSeconds) {
  await nextAnimationFrame();
  if (!jsDebugHistoryRequestIsCurrent(generation, requestedRangeSeconds, requestedStartSeconds)) return false;
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force: true});
  }
  const paintStartedAt = performanceNow();
  await nextAnimationFrame();
  recordClientPerfCounter('statsHistoryPaint', performanceNow() - paintStartedAt);
  if (!jsDebugHistoryRequestIsCurrent(generation, requestedRangeSeconds, requestedStartSeconds)) return false;
  setJsDebugHistoryReadiness('ready', {
    requestedRangeSeconds,
    requestedStartSeconds,
    error: '',
  });
  return true;
}

async function pollJsDebugStatsSample({forceGraphRefresh = false} = {}) {
  if (!jsDebugCollectionEnabled) return;
  if (!jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  if (jsDebugStatsPollState.inFlight) {
    jsDebugStatsPollState.pending = true;
    jsDebugStatsPollState.pendingForceGraphRefresh ||= forceGraphRefresh;
    return;
  }
  if (typeof apiFetchJsonQuiet !== 'function') return;
  jsDebugStatsPollState.pending = false;
  jsDebugStatsPollState.inFlight = true;
  let readinessRequest = null;
  try {
    const clientId = jsDebugStatsClientIdForRequest();
    const tokenConsumer = jsDebugStatsTokenConsumerEnabled() ? '1' : '0';
    const tokenResolution = debugGraphAgentTokenResolution();
    syncDebugGraphAgentTokenResolution();
    const domain = debugGraphDomain();
    const targetStart = Math.max(0, Math.floor(domain.startMs / 1000));
    const targetEnd = Math.max(targetStart + 1, Math.ceil(domain.endMs / 1000));
    const historyResolution = jsDebugRequestedHistoryResolutionSeconds();
    const coverageResolution = jsDebugHistoryCoverageResolutionSeconds(targetStart, historyResolution);
    const needsHistoryCoverage = jsDebugHistoryCoverageNeedsRefresh(targetStart, targetEnd, coverageResolution);
    if (needsHistoryCoverage) {
      const state = jsDebugHistoryReadiness;
      const currentRequestMatches = jsDebugHistoryReadinessBusy(state)
        && Number(state.requestedRangeSeconds) === Number(jsDebugGraphRangeSeconds)
        && Number(state.targetStartSeconds) === Number(targetStart)
        && Number(state.targetEndSeconds) === Number(targetEnd)
        && Number(state.requestedResolutionSeconds) === Number(coverageResolution);
      if (!currentRequestMatches) {
        const requestWindow = jsDebugHistoryRequestWindow(targetStart, targetEnd, coverageResolution);
        beginJsDebugHistoryReadiness(requestWindow.startSeconds, {
          targetStartSeconds: targetStart,
          targetEndSeconds: targetEnd,
          requestedEndSeconds: requestWindow.endSeconds,
          requestedResolutionSeconds: coverageResolution,
          retry: state.phase === 'error',
        });
      }
      readinessRequest = jsDebugHistoryReadinessSnapshot();
      await nextAnimationFrame();
      if (!jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) return;
    }
    const historyEnd = readinessRequest
      ? Math.max(0, Math.floor(Number(readinessRequest.requestedEndSeconds) || 0))
      : 0;
    const historyStart = readinessRequest ? readinessRequest.requestedStartSeconds : targetStart;
    const tokenHistory = tokenResolution
      ? `&token_since=${encodeURIComponent(String(readinessRequest ? 0 : (jsDebugStatsAgentTokenSequence || 0)))}&token_resolution=${encodeURIComponent(String(tokenResolution))}&token_history_start=${encodeURIComponent(String(targetStart))}&token_history_end=0`
      : '';
    const requestSince = readinessRequest ? 0 : (jsDebugStatsServerSequence || 0);
    const payload = await fetchJsDebugStatsJson(`/api/stats-sample?since=${encodeURIComponent(String(requestSince))}&client_id=${encodeURIComponent(clientId)}&token_consumer=${tokenConsumer}&history_start=${encodeURIComponent(String(historyStart))}&history_end=${encodeURIComponent(String(historyEnd))}&history_resolution=${encodeURIComponent(String(historyResolution))}&history_max_points=${encodeURIComponent(String(jsDebugStatsHistoryMaxPoints))}${tokenHistory}`, {cache: 'no-store'});
    if (readinessRequest && !jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) return;
    const coverage = normalizedJsDebugHistoryCoverage(payload?.history);
    if (readinessRequest && !coverage) throw new Error('stats history response omitted coverage');
    const replaceCoverage = coverage && jsDebugHistoryCoverageResolutionForRange(coverage.coveredStart, coverage.coveredEnd) > coverage.resolutionSeconds
      ? payload.history.coverage
      : null;
    const applyStartedAt = performanceNow();
    recordJsDebugStatsSample(payload, {
      forceGraphRefresh: forceGraphRefresh || needsHistoryCoverage,
      scheduleRefresh: !readinessRequest,
      advanceHistoryCursor: coverage?.mode !== 'older',
      replaceCoverage,
    });
    if (coverage) applyJsDebugHistoryCoverage(coverage);
    recordClientPerfCounter('statsHistoryApply', performanceNow() - applyStartedAt);
    if (readinessRequest) {
      if (coverage.hasMoreOlder && coverage.coveredStart > readinessRequest.targetStartSeconds && Number.isFinite(coverage.nextOlderEnd)) {
        jsDebugStatsPollState.pending = true;
        jsDebugStatsPollState.pendingForceGraphRefresh = true;
        return;
      }
      await paintJsDebugHistoryResponse(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds);
    }
  } catch (error) {
    if (readinessRequest && jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) {
      setJsDebugHistoryReadiness('error', {error: jsDebugErrorText(error)});
    }
  } finally {
    jsDebugStatsPollState.inFlight = false;
    if (jsDebugStatsPollState.pending) {
      const pendingForceGraphRefresh = jsDebugStatsPollState.pendingForceGraphRefresh;
      jsDebugStatsPollState.pending = false;
      jsDebugStatsPollState.pendingForceGraphRefresh = false;
      pollJsDebugStatsSample({forceGraphRefresh: pendingForceGraphRefresh});
    }
  }
}

function scheduleJsDebugStatsHistoryFlush() {
  if (!jsDebugCollectionEnabled || jsDebugStatsUploadState.timer || typeof setTimeout !== 'function') return;
  jsDebugStatsUploadState.timer = setTimeout(() => {
    jsDebugStatsUploadState.timer = null;
    flushJsDebugStatsHistory();
  }, jsDebugStatsHistoryFlushMs);
}

async function flushJsDebugStatsHistory() {
  if (!jsDebugCollectionEnabled || !jsDebugGraphPendingServerBuckets.size || typeof apiFetchJsonQuiet !== 'function') return;
  if (jsDebugStatsUploadState.worker) return jsDebugStatsUploadState.worker;
  const records = [...jsDebugGraphPendingServerBuckets.values()]
    .map(record => ({...record}))
    .filter(record => record.api_count || record.sse_count || record.latency_count || record.bandwidth_bytes || record.disconnected_ms || record.cpu_count || record.system_cpu_count)
    .sort((a, b) => (Number(a.start) - Number(b.start)) || (Number(a.duration) - Number(b.duration)));
  const chunk = records.slice(0, jsDebugStatsHistoryPostMaxRecords);
  const held = records.slice(jsDebugStatsHistoryPostMaxRecords);
  for (const record of chunk) {
    const key = `${Math.floor(Number(record.start) * 1000)}:${Math.floor(Number(record.duration) * 1000)}`;
    jsDebugGraphPendingServerBuckets.delete(key);
  }
  if (!records.length) return;
  const generation = jsDebugStatsUploadState.generation;
  let worker = null;
  worker = (async () => {
    try {
      await apiFetchJsonQuiet('/api/stats-history', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({client_id: jsDebugStatsClientIdForRequest(), since: jsDebugStatsServerSequence || 0, ack_only: true, records: chunk}),
      });
      if (jsDebugStatsUploadState.generation !== generation || jsDebugStatsUploadState.worker !== worker) return;
      scheduleJsDebugPanelRefresh();
    } catch (_error) {
      if (jsDebugStatsUploadState.generation !== generation || jsDebugStatsUploadState.worker !== worker) return;
      for (const record of chunk) {
        const key = `${Math.floor(Number(record.start) * 1000)}:${Math.floor(Number(record.duration) * 1000)}`;
        const existing = jsDebugGraphPendingServerBuckets.get(key);
        if (existing) {
          for (const field of ['api_count', 'sse_count', 'latency_total_ms', 'latency_count', 'bandwidth_bytes', 'disconnected_ms', 'cpu_total_percent', 'cpu_count', 'system_cpu_total_percent', 'system_cpu_count']) {
            existing[field] = Number(existing[field] || 0) + Number(record[field] || 0);
          }
        } else {
          jsDebugGraphPendingServerBuckets.set(key, record);
        }
      }
    } finally {
      if (jsDebugStatsUploadState.generation !== generation || jsDebugStatsUploadState.worker !== worker) return;
      for (const record of held) {
        const key = `${Math.floor(Number(record.start) * 1000)}:${Math.floor(Number(record.duration) * 1000)}`;
        if (!jsDebugGraphPendingServerBuckets.has(key)) jsDebugGraphPendingServerBuckets.set(key, record);
      }
      jsDebugStatsUploadState.worker = null;
      if (jsDebugGraphPendingServerBuckets.size) scheduleJsDebugStatsHistoryFlush();
    }
  })();
  jsDebugStatsUploadState.worker = worker;
  return worker;
}

async function clearJsDebugServerHistory() {
  const priorWorker = jsDebugStatsUploadState.worker;
  const generation = ++jsDebugStatsUploadState.generation;
  const restartPolling = runtimeIntervalActive('debug-stats');
  stopJsDebugStatsPolling();
  jsDebugStatsPollState.firstSampleReceived = false;
  jsDebugStatsServerSequence = 0;
  jsDebugStatsServerUptimeSeconds = null;
  jsDebugStatsServerPid = null;
  jsDebugStatsServerStartedAt = null;
  jsDebugStatsServerRssBytes = null;
  resetJsDebugHistoryReadiness();
  jsDebugGraphPendingServerBuckets.clear();
  if (jsDebugStatsUploadState.timer) {
    clearTimeout(jsDebugStatsUploadState.timer);
    jsDebugStatsUploadState.timer = null;
  }
  if (priorWorker) {
    try {
      await priorWorker;
    } catch (_error) {}
  }
  if (jsDebugStatsUploadState.generation !== generation) return;
  if (jsDebugStatsUploadState.worker === priorWorker) jsDebugStatsUploadState.worker = null;
  if (typeof apiFetchJsonQuiet !== 'function') {
    if (restartPolling) armJsDebugStatsPolling({pollNow: true});
    return;
  }
  try {
    const payload = await apiFetchJsonQuiet('/api/stats-history', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({client_id: jsDebugStatsClientIdForRequest(), clear: true}),
    });
    if (jsDebugStatsUploadState.generation !== generation) return;
    debugGraphApplyServerHistory(payload?.history);
    scheduleJsDebugPanelRefresh();
  } catch (_error) {
  } finally {
    if (jsDebugStatsUploadState.generation !== generation) return;
    if (restartPolling) armJsDebugStatsPolling({pollNow: true});
    if (jsDebugGraphPendingServerBuckets.size) scheduleJsDebugStatsHistoryFlush();
  }
}

function startJsDebugStatsPolling() {
  startJsDebugClientHealthPolling();
  syncJsDebugStatsPolling({pollNow: true});
}

const jsDebugClientHealthPollState = {inFlight: false};

async function pollJsDebugClientHealth() {
  if (!jsDebugCollectionEnabled || jsDebugClientHealthPollState.inFlight || typeof apiFetchJsonQuiet !== 'function') return;
  jsDebugClientHealthPollState.inFlight = true;
  const url = `/api/ping?client_id=${encodeURIComponent(jsDebugStatsClientIdForRequest())}`;
  const startedAt = performanceNow();
  try {
    const payload = await apiFetchJsonQuiet(url, {cache: 'no-store'});
    const latencyMs = Math.max(0, performanceNow() - startedAt);
    const bandwidthBytes = jsDebugRequestBytes(url) + utf8ByteLength(JSON.stringify(payload || {}));
    const sampleTimeMs = Date.now();
    const bucketRef = debugGraphServerBucketRefForTime(sampleTimeMs, sampleTimeMs);
    const data = {heartbeatCount: 1, latencyMs, bandwidthBytes};
    debugGraphAddBucketData(debugGraphBucketForTime(sampleTimeMs, sampleTimeMs), data);
    debugGraphQueueServerDelta(bucketRef, data);
    compactJsDebugGraphBuckets(sampleTimeMs);
  } finally {
    jsDebugClientHealthPollState.inFlight = false;
  }
}

function startJsDebugClientHealthPolling() {
  if (!jsDebugCollectionEnabled || runtimeIntervalActive('debug-client-health')) return;
  // A background health request is best-effort: an offline/browser-suspended page has no
  // sample to contribute and must not surface an unhandled promise rejection.
  void pollJsDebugClientHealth().catch(() => {});
  resetRuntimeInterval('debug-client-health', () => { void pollJsDebugClientHealth().catch(() => {}); }, jsDebugStatsPollMs);
}

function syncJsDebugStatsPolling({pollNow = true, forceGraphRefresh = false} = {}) {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return false;
  }
  if (runtimeIntervalActive('debug-stats') && !pollNow) return true;
  armJsDebugStatsPolling({pollNow, forceGraphRefresh});
  return true;
}

async function primeJsDebugStatsBeforeLongLivedStreams() {
  if (!jsDebugStatsPanelVisible() || jsDebugStatsPollState.firstSampleReceived) return false;
  await pollJsDebugStatsSample();
  return jsDebugStatsPollState.firstSampleReceived;
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('visibilitychange', () => {
    const visible = document.visibilityState === 'visible';
    syncJsDebugStatsPolling({pollNow: visible, forceGraphRefresh: visible});
  });
}

function jsDebugTextForClipboard() {
  const page = `${location.pathname || ''}${location.search || ''}${location.hash || ''}`;
  const counts = debugEventCounts();
  const removalSummary = terminalRemovalLatencySummary();
  const header = [
    `JS Debug ${new Date().toISOString()}`,
    `page=${page || '/'}`,
    `events=${jsDebugEvents.length}`,
    `api=${counts.apiCalls}`,
    `sse=${counts.sseEvents}`,
    `errors=${counts.errors}`,
    `removals=${removalSummary.count}`,
    `removal_avg=${removalSummary.averageMs}ms`,
    `api_tx=${counts.apiRequestBytes}B`,
    `api_rx=${counts.apiResponseBytes}B`,
    `sse_rx=${counts.sseBytes}B`,
  ].join(' ');
  const apiSummaryRows = debugApiSummaryRows();
  const sseSummaryRows = debugSseSummaryRows();
  const sseLatencySummaryRows = debugSseLatencySummaryRows();
  const clientPerfRows = debugClientPerfRows().map(debugClientPerfText);
  const rows = jsDebugEvents.map(debugEventLineText);
  return [
    header,
    ...(apiSummaryRows.length ? ['Slow API by max latency:', ...apiSummaryRows, ''] : []),
    ...(sseSummaryRows.length ? ['Slow SSE server work:', ...sseSummaryRows, ''] : []),
    ...(sseLatencySummaryRows.length ? ['Slow SSE receive latency:', ...sseLatencySummaryRows, ''] : []),
    ...(clientPerfRows.length ? ['Client work counters:', ...clientPerfRows, ''] : []),
    ...rows,
  ].join('\n');
}

function debugPanelHtml() {
  const counts = debugEventCounts();
  return `
    ${debugSubTabsHtml()}
    <div class="js-debug-subview js-debug-events-view" ${debugSubViewAttrs('events')}>
      <div class="js-debug-toolbar">
        <div class="js-debug-summary" aria-label="${esc(t('debug.summary'))}">
          ${debugStatHtml(t('debug.events'), jsDebugEvents.length, 'events')}
          ${debugStatHtml(t('debug.apiCalls'), counts.apiCalls, 'api')}
          ${debugStatHtml('SSE', counts.sseEvents, 'sse')}
          ${debugStatHtml(t('debug.errors'), counts.errors, 'errors')}
        </div>
        <div class="js-debug-actions">
          <button type="button" class="preferences-inline-action" data-js-debug-copy>${esc(t('common.copy'))}</button>
          <button type="button" class="preferences-inline-action" data-js-debug-clear>${esc(t('common.clear'))}</button>
        </div>
      </div>
      <textarea class="js-debug-log" data-js-debug-log readonly spellcheck="false" aria-label="${esc(t('debug.recent'))}">${esc(jsDebugTextForClipboard())}</textarea>
    </div>
    <div class="js-debug-subview js-debug-graph-view" ${debugSubViewAttrs('graph')}>${debugGraphHtml()}</div>`;
}

function relocalizeDebugPanelChrome(panel = document.getElementById(panelDomId(debugPaneItemId))) {
  return relocalizeVirtualPanelChrome(panel, t('tab.debug'));
}

function createDebugPanel() {
  enableDebugMode();
  const panel = document.createElement('article');
  panel.className = 'panel js-debug-panel';
  panel.id = panelDomId(debugPaneItemId);
  panel.innerHTML = panelFrameHtml({
    item: debugPaneItemId,
    headClass: 'preferences-panel-head',
    controlsHtml: virtualPanelControlsHtml(debugPaneItemId),
    afterHeadHtml: `<div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-copy panel-copy">
          <div id="panel-tab-${debugPaneItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.debug'))}</span></div>
          <div id="meta-${debugPaneItemId}" class="pane-info-bar-meta meta">${esc(debugMetaText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(debugPaneItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>`,
    bodyClass: 'preferences-body js-debug-body',
    bodyHtml: `<div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`,
  });
  bindPanelShell(panel, debugPaneItemId);
  bindDebugPanel(panel);
  return panel;
}

function renderDebugPanels(options = {}) {
  if (dragState.item != null) return;
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    const body = panel.querySelector('.js-debug-body');
    refreshDebugPanelFromEvents(panel, options);
    if (body && (options.force === true || !body.querySelector('[data-js-debug-log]'))) {
      body.innerHTML = `${panelToastStackHtml(debugPaneItemId)}<div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`;
      refreshDebugPanelFromEvents(panel, {force: true});
    }
    bindDebugPanel(panel);
  }
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts(debugPaneItemId);
}

function refreshDebugPanelsFromEvents(options = {}) {
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    refreshDebugPanelFromEvents(panel, options);
  }
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts(debugPaneItemId);
}

function refreshDebugPanelFromEvents(panel, options = {}) {
  if (!panel) return;
  const meta = panel.querySelector(`#meta-${cssEscape(debugPaneItemId)}`);
  if (meta) meta.textContent = debugMetaText();
  const counts = debugEventCounts();
  const statEvents = panel.querySelector('[data-js-debug-stat="events"]');
  const statApi = panel.querySelector('[data-js-debug-stat="api"]');
  const statSse = panel.querySelector('[data-js-debug-stat="sse"]');
  const statErrors = panel.querySelector('[data-js-debug-stat="errors"]');
  if (statEvents) statEvents.textContent = String(jsDebugEvents.length);
  if (statApi) statApi.textContent = String(counts.apiCalls);
  if (statSse) statSse.textContent = String(counts.sseEvents);
  if (statErrors) statErrors.textContent = String(counts.errors);
  applyDebugSubTab(panel);
  const graph = panel.querySelector('[data-js-debug-graph]');
  refreshDebugGraphElement(graph, options);
  const log = panel.querySelector('[data-js-debug-log]');
  if (!log || (document.activeElement === log && options.force !== true)) return;
  const text = jsDebugTextForClipboard();
  if (log.value === text) return;
  const oldTop = log.scrollTop;
  const maxScroll = Math.max(0, log.scrollHeight - log.clientHeight);
  const nearBottom = maxScroll - oldTop <= 20;
  log.value = text;
  log.scrollTop = nearBottom || options.force === true ? log.scrollHeight : oldTop;
}

function refreshDebugGraphElement(graph, {force = false} = {}) {
  if (!graph || jsDebugGraphRangeSliderDragging) return false;
  const nowMs = Date.now();
  const lastRenderedAt = Number(graph.dataset.jsDebugGraphRenderedAt);
  if (!force && Number.isFinite(lastRenderedAt) && nowMs - lastRenderedAt < jsDebugGraphRefreshMs) return false;
  const perf = clientPerfStart('statsHistoryRender');
  try {
    graph.className = debugGraphClassName(nowMs);
    graph.innerHTML = debugGraphInnerHtml(nowMs);
    graph.dataset.jsDebugGraphRenderedAt = String(nowMs);
    graph.dataset.jsDebugHistoryState = jsDebugHistoryReadiness.phase;
    graph.setAttribute('aria-busy', jsDebugHistoryReadinessBusy() ? 'true' : 'false');
  } finally {
    clientPerfEnd(perf);
  }
  return true;
}

function applyDebugSubTab(panel) {
  if (!panel) return;
  panel.querySelectorAll('[data-js-debug-subtab]').forEach(button => {
    const active = normalizedJsDebugSubTab(button.dataset.jsDebugSubtab) === jsDebugSubTab;
    button.classList.toggle(CLS.active, active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  panel.querySelectorAll('[data-js-debug-subview]').forEach(view => {
    const active = normalizedJsDebugSubTab(view.dataset.jsDebugSubview) === jsDebugSubTab;
    view.hidden = !active;
  });
}

function setDebugSubTab(tab) {
  loadJsDebugStatsUiPreferences();
  jsDebugSubTab = normalizedJsDebugSubTab(tab);
  saveJsDebugStatsUiPreferences();
  for (const panel of document.querySelectorAll('.js-debug-panel')) applyDebugSubTab(panel);
}

function requestJsDebugHistoryForCurrentDomain({retry = false, forceGraphRefresh = true} = {}) {
  if (!jsDebugStatsPanelVisible()) return false;
  syncDebugGraphAgentTokenResolution();
  const domain = debugGraphDomain();
  const requestedStartSeconds = Math.max(0, Math.floor(domain.startMs / 1000));
  const requestedDomainEndSeconds = Math.max(requestedStartSeconds + 1, Math.ceil(domain.endMs / 1000));
  const requestedResolutionSeconds = jsDebugRequestedHistoryResolutionSeconds();
  const coverageResolutionSeconds = jsDebugHistoryCoverageResolutionSeconds(requestedStartSeconds, requestedResolutionSeconds);
  if (!retry && !jsDebugHistoryCoverageNeedsRefresh(requestedStartSeconds, requestedDomainEndSeconds, coverageResolutionSeconds)) return false;
  const state = jsDebugHistoryReadiness;
  const currentRequestMatches = jsDebugHistoryReadinessBusy(state)
    && Number(state.requestedRangeSeconds) === Number(jsDebugGraphRangeSeconds)
    && Number(state.targetStartSeconds) === Number(requestedStartSeconds)
    && Number(state.targetEndSeconds) === Number(requestedDomainEndSeconds)
    && Number(state.requestedResolutionSeconds) === Number(coverageResolutionSeconds);
  if (!currentRequestMatches || retry) {
    const requestWindow = jsDebugHistoryRequestWindow(requestedStartSeconds, requestedDomainEndSeconds, coverageResolutionSeconds);
    beginJsDebugHistoryReadiness(requestWindow.startSeconds, {
      targetStartSeconds: requestedStartSeconds,
      targetEndSeconds: requestedDomainEndSeconds,
      requestedEndSeconds: requestWindow.endSeconds,
      requestedResolutionSeconds: coverageResolutionSeconds,
      retry,
    });
  }
  armJsDebugStatsPolling({pollNow: true, forceGraphRefresh});
  return true;
}

function setDebugGraphRange(value, {render = true} = {}) {
  loadJsDebugStatsUiPreferences();
  jsDebugGraphZoomDomain = null;
  jsDebugGraphRangeSeconds = normalizedJsDebugGraphRange(value);
  saveJsDebugStatsUiPreferences();
  activeJsDebugGraphRangeSeconds();
  syncDebugGraphAgentTokenResolution();
  if (!render) return;
  const requestedStartSeconds = Math.max(0, Math.floor(debugGraphDomain().startMs / 1000));
  if (requestJsDebugHistoryForCurrentDomain()) return;
  if (jsDebugHistoryReadinessBusy() || jsDebugHistoryReadiness.phase === 'error') {
    setJsDebugHistoryReadiness('ready', {
      requestedRangeSeconds: jsDebugGraphRangeSeconds,
      requestedStartSeconds,
      attemptCount: 0,
      error: '',
      generation: Number(jsDebugHistoryReadiness.generation || 0) + 1,
    });
  }
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force: true});
  }
}

function setDebugGraphResolutionOverride(value) {
  loadJsDebugStatsUiPreferences();
  const seconds = Math.max(0, Number(value) || 0);
  jsDebugGraphResolutionOverrideSeconds = [0, 1, 2, 5, 10, 30, 60, 120, 300, 600].includes(seconds) ? seconds : 0;
  saveJsDebugStatsUiPreferences();
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) refreshDebugGraphElement(graph, {force: true});
}

function setDebugGraphChartLayout(value) {
  loadJsDebugStatsUiPreferences();
  jsDebugGraphChartLayout = Math.max(0, Math.min(4, Math.round(Number(value) || 0)));
  saveJsDebugStatsUiPreferences();
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) refreshDebugGraphElement(graph, {force: true});
}

function retryJsDebugHistory() {
  if (jsDebugHistoryReadiness.phase !== 'error' || !jsDebugStatsPanelVisible()) return false;
  return requestJsDebugHistoryForCurrentDomain({retry: true});
}

function debugGraphRangeSliderIndex(slider, options = debugGraphAvailableRangeOptions()) {
  const rawValue = Number(slider?.value);
  const value = Number.isFinite(rawValue) ? rawValue : 0;
  return Math.max(0, Math.min(options.length - 1, Math.round(value)));
}

function debugGraphRangeOptionForSlider(slider) {
  const options = debugGraphAvailableRangeOptions();
  const index = debugGraphRangeSliderIndex(slider, options);
  return options[index] || null;
}

function updateDebugGraphRangeSliderLabel(slider, option) {
  const label = slider?.closest?.('[data-js-debug-range-control]')?.querySelector?.('[data-js-debug-range-label]');
  if (label && option) label.textContent = option.label;
}

function setDebugGraphRangeFromSlider(slider, {render = true, snap = false} = {}) {
  const options = debugGraphAvailableRangeOptions();
  const index = debugGraphRangeSliderIndex(slider, options);
  const option = options[index];
  if (!option) return false;
  if (snap) slider.value = String(index);
  setDebugGraphRange(option.seconds, {render});
  updateDebugGraphRangeSliderLabel(slider, option);
  return true;
}

function debugGraphPointerRatioFromRect(clientX, rect) {
  const left = Number(rect?.left);
  const width = Number(rect?.width);
  if (!Number.isFinite(Number(clientX)) || !Number.isFinite(left) || !Number.isFinite(width) || width <= 0) return null;
  return Math.max(0, Math.min(1, (Number(clientX) - left) / width));
}

function debugGraphPointerRatioForEvent(event) {
  const svg = event?.target?.closest?.('.js-debug-line-chart');
  if (!svg) return null;
  return debugGraphPointerRatioFromRect(event.clientX, svg.getBoundingClientRect());
}

function debugGraphSetInteractionLines(panel, ratio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (!graph || ratio == null) return;
  const x = (Math.max(0, Math.min(1, Number(ratio))) * jsDebugGraphGeometry.width).toFixed(1);
  graph.classList.add('js-debug-graph--hovering');
  graph.querySelectorAll('[data-js-debug-hover-line]').forEach(line => {
    line.setAttribute('x1', x);
    line.setAttribute('x2', x);
  });
}

function debugGraphSetHoverTooltip(panel, event, ratio) {
  const svg = event?.target?.closest?.('.js-debug-line-chart');
  const chart = svg?.closest?.('[data-js-debug-chart]');
  const tooltip = chart?.querySelector?.('[data-js-debug-hover-tooltip]');
  if (!svg || !chart || !tooltip || ratio == null) return;
  const domain = debugGraphGridDomain(panel);
  const spanMs = Number(domain.endMs) - Number(domain.startMs);
  if (!Number.isFinite(spanMs) || spanMs <= 0) return;
  const timestamp = Number(domain.startMs) + (Math.max(0, Math.min(1, Number(ratio))) * spanMs);
  tooltip.querySelector('[data-js-debug-hover-max]').textContent = debugGraphHoverValueAtTime(chart, timestamp);
  tooltip.querySelector('[data-js-debug-hover-time]').textContent = debugGraphExactTimeLabel(timestamp);
  for (const item of panel.querySelectorAll('[data-js-debug-hover-tooltip]')) item.hidden = item !== tooltip;
  tooltip.hidden = false;
  tooltip.style.left = '0px';
  tooltip.style.top = '0px';
  const chartRect = chart.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const left = Math.max(4, Math.min(chartRect.width - tooltipRect.width - 4, event.clientX - chartRect.left + 4));
  const top = Math.max(4, Math.min(chartRect.height - tooltipRect.height - 4, event.clientY - chartRect.top - tooltipRect.height - 4));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function debugGraphClearInteractionLines(panel) {
  if (jsDebugGraphSelectionState) return;
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (graph) graph.classList.remove('js-debug-graph--hovering');
  panel?.querySelectorAll?.('[data-js-debug-hover-tooltip]').forEach(tooltip => { tooltip.hidden = true; });
}

function debugGraphSetSelectionRects(panel, startRatio, endRatio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (!graph) return;
  const start = Math.max(0, Math.min(1, Number(startRatio)));
  const end = Math.max(0, Math.min(1, Number(endRatio)));
  const x = Math.min(start, end) * jsDebugGraphGeometry.width;
  const width = Math.abs(end - start) * jsDebugGraphGeometry.width;
  graph.classList.add('js-debug-graph--selecting');
  graph.querySelectorAll('[data-js-debug-selection-rect]').forEach(rect => {
    rect.setAttribute('x', x.toFixed(1));
    rect.setAttribute('width', width.toFixed(1));
  });
}

function debugGraphClearSelectionRects(panel) {
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (!graph) return;
  graph.classList.remove('js-debug-graph--selecting');
  graph.querySelectorAll('[data-js-debug-selection-rect]').forEach(rect => {
    rect.setAttribute('x', '0');
    rect.setAttribute('width', '0');
  });
}

function debugGraphGridDomain(panel) {
  const grid = panel?.querySelector?.('[data-js-debug-chart-grid]');
  const startMs = Number(grid?.dataset?.jsDebugDomainStart);
  const endMs = Number(grid?.dataset?.jsDebugDomainEnd);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return debugGraphDomain();
  return {startMs, endMs, rangeSeconds: (endMs - startMs) / 1000};
}

function debugGraphSelectionRatioForEvent(event, selection = jsDebugGraphSelectionState) {
  if (!selection) return null;
  return debugGraphPointerRatioFromRect(event?.clientX, selection.rect);
}

function handleDebugGraphPointerDown(event, panel) {
  const ratio = debugGraphPointerRatioForEvent(event);
  if (ratio == null || event.button > 0) return false;
  event.preventDefault();
  const svg = event.target.closest('.js-debug-line-chart');
  jsDebugGraphSelectionState = {
    panel,
    rect: svg.getBoundingClientRect(),
    domain: debugGraphGridDomain(panel),
    startRatio: ratio,
    currentRatio: ratio,
  };
  debugGraphSetInteractionLines(panel, ratio);
  debugGraphSetSelectionRects(panel, ratio, ratio);
  return true;
}

function handleDebugGraphPointerMove(event, panel) {
  if (jsDebugGraphSelectionState?.panel === panel) {
    const ratio = debugGraphSelectionRatioForEvent(event);
    if (ratio == null) return;
    jsDebugGraphSelectionState.currentRatio = ratio;
    debugGraphSetInteractionLines(panel, ratio);
    debugGraphSetHoverTooltip(panel, event, ratio);
    debugGraphSetSelectionRects(panel, jsDebugGraphSelectionState.startRatio, ratio);
    return;
  }
  const ratio = debugGraphPointerRatioForEvent(event);
  if (ratio == null) return;
  debugGraphSetInteractionLines(panel, ratio);
  debugGraphSetHoverTooltip(panel, event, ratio);
}

function handleDebugGraphPointerUp(event, panel) {
  const selection = jsDebugGraphSelectionState;
  if (!selection || selection.panel !== panel) return;
  const ratio = debugGraphSelectionRatioForEvent(event);
  if (ratio != null) selection.currentRatio = ratio;
  const start = Math.max(0, Math.min(1, Number(selection.startRatio)));
  const end = Math.max(0, Math.min(1, Number(selection.currentRatio)));
  debugGraphClearSelectionRects(panel);
  jsDebugGraphSelectionState = null;
  const minRatio = Math.min(start, end);
  const maxRatio = Math.max(start, end);
  const domain = selection.domain;
  const spanMs = Math.max(1, Number(domain.endMs) - Number(domain.startMs));
  const selectedMs = (maxRatio - minRatio) * spanMs;
  if (selectedMs >= 1000 && Math.abs(maxRatio - minRatio) >= 0.01) {
    jsDebugGraphZoomDomain = {
      startMs: Number(domain.startMs) + (minRatio * spanMs),
      endMs: Number(domain.startMs) + (maxRatio * spanMs),
    };
    if (requestJsDebugHistoryForCurrentDomain()) return;
    for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
      refreshDebugGraphElement(graph, {force: true});
    }
  } else {
    debugGraphSetInteractionLines(panel, end);
  }
}

function cancelDebugGraphSelection(panel) {
  if (jsDebugGraphSelectionState?.panel !== panel) return;
  debugGraphClearSelectionRects(panel);
  jsDebugGraphSelectionState = null;
}

function handleDebugGraphControlEvent(event, panel) {
  const chartClose = event.target.closest('[data-js-debug-chart-close]');
  if (chartClose && panel.contains(chartClose)) {
    event.preventDefault();
    setDebugGraphChartVisible(chartClose.dataset.jsDebugChartClose, false);
    return true;
  }
  const chartRestore = event.target.closest('[data-js-debug-chart-restore]');
  if (chartRestore && panel.contains(chartRestore)) {
    event.preventDefault();
    setDebugGraphChartVisible(chartRestore.dataset.jsDebugChartRestore, true);
    return true;
  }
  const retry = event.target.closest('[data-js-debug-history-retry]');
  if (retry && panel.contains(retry)) {
    event.preventDefault();
    retryJsDebugHistory();
    return true;
  }
  const reset = event.target.closest('[data-js-debug-zoom-reset]');
  if (reset && panel.contains(reset)) {
    event.preventDefault();
    clearDebugGraphZoom();
    return true;
  }
  const resolutionOverride = event.target.closest('[data-js-debug-resolution-override]');
  if (resolutionOverride && panel.contains(resolutionOverride) && event.type === 'change') {
    setDebugGraphResolutionOverride(resolutionOverride.value);
    return true;
  }
  const chartLayout = event.target.closest('button[data-js-debug-chart-layout]');
  if (chartLayout && panel.contains(chartLayout) && event.type === 'pointerdown') {
    event.preventDefault();
    setDebugGraphChartLayout(chartLayout.dataset.jsDebugChartLayout);
    return true;
  }
  const slider = event.target.closest('[data-js-debug-range-slider]');
  if (slider && panel.contains(slider)) {
    if (event.type === 'pointerdown') {
      jsDebugGraphRangeSliderDragging = true;
      // Claim the event at the graph shell so chart-selection handling cannot
      // inspect or replace the native range input during its drag gesture.
      // Do not preventDefault: the browser owns the range-thumb movement.
      return true;
    }
    if (event.type === 'input') {
      jsDebugGraphRangeSliderDragging = true;
      return setDebugGraphRangeFromSlider(slider, {render: false});
    }
    if (event.type === 'change') {
      jsDebugGraphRangeSliderDragging = false;
      return setDebugGraphRangeFromSlider(slider, {snap: true});
    }
    if (event.type === 'pointerup') return false;
    if (event.type === 'pointercancel') {
      jsDebugGraphRangeSliderDragging = false;
      return false;
    }
    return false;
  }
  const range = event.target.closest('[data-js-debug-range]');
  if (range && panel.contains(range)) {
    event.preventDefault();
    setDebugGraphRange(range.dataset.jsDebugRange);
    return true;
  }
  return false;
}

function bindDebugPanel(panel) {
  if (!panel || panel.dataset.debugBound === 'true') return;
  panel.dataset.debugBound = 'true';
  panel.addEventListener('pointerdown', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerDown(event, panel);
  });
  panel.addEventListener('pointermove', event => {
    handleDebugGraphPointerMove(event, panel);
  });
  panel.addEventListener('pointerleave', () => {
    debugGraphClearInteractionLines(panel);
  });
  panel.addEventListener('pointerup', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerUp(event, panel);
  });
  panel.addEventListener('pointercancel', event => {
    handleDebugGraphControlEvent(event, panel);
    cancelDebugGraphSelection(panel);
  });
  panel.addEventListener('input', event => {
    handleDebugGraphControlEvent(event, panel);
  });
  panel.addEventListener('change', event => {
    handleDebugGraphControlEvent(event, panel);
  });
  panel.addEventListener('click', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    const subtab = event.target.closest('[data-js-debug-subtab]');
    if (subtab && panel.contains(subtab)) {
      event.preventDefault();
      setDebugSubTab(subtab.dataset.jsDebugSubtab);
      return;
    }
    const copy = event.target.closest('[data-js-debug-copy]');
    if (copy && panel.contains(copy)) {
      event.preventDefault();
      copyTextToClipboard(jsDebugTextForClipboard())
        .then(() => { statusEl.textContent = t('debug.copied'); })
        .catch(error => { statusErr(localizedHtml('common.copyFailed', {error})); });
      return;
    }
    const clear = event.target.closest('[data-js-debug-clear]');
    if (clear && panel.contains(clear)) {
      event.preventDefault();
      clearJsDebugEvents();
      statusEl.textContent = t('debug.cleared');
    }
  });
}

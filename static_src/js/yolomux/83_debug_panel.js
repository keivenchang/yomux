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
// The readiness machine has FOUR phases; `reason` qualifies the one loading phase
// ('initial' | 'older' | 'retry', '' otherwise). The DOM/data-attribute contract and
// the test snapshot still emit the historical composite names (loading-initial /
// loading-older / retrying) derived from phase+reason via
// jsDebugHistoryReadinessStateName, so CSS hooks, specs, and tests keep one stable
// state vocabulary while the machine itself carries fewer states.
const jsDebugHistoryReadinessPhases = Object.freeze(['idle', 'loading', 'ready', 'error']);
const jsDebugHistoryReadinessReasonByLegacyPhase = Object.freeze({'loading-initial': 'initial', 'loading-older': 'older', 'retrying': 'retry'});
const jsDebugHistoryLegacyPhaseByReason = Object.freeze({initial: 'loading-initial', older: 'loading-older', retry: 'retrying'});
const jsDebugHistoryOlderOverlayDelayMs = 120;
const jsDebugHistoryRetryInitialDelayMs = 10_000;
const jsDebugHistoryRetryMaxDelayMs = 5 * 60_000;
const jsDebugHistoryCoverageIntervalLimit = 256;
const jsDebugHistoryReadiness = {
  phase: 'idle',
  reason: '',
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
  requestCoverageIntervals: [],
  storeCoverageIntervals: {},
  attemptCount: 0,
  error: '',
  generation: 0,
  loadingStartedAtMs: 0,
  nextAutoRetryAtMs: 0,
  overlayVisible: false,
  overlayTimer: null,
};
let jsDebugSubTab = 'graph';
const jsDebugSystemPollMs = 5000;
const jsDebugLogsPollMs = 5000;
const jsDebugSystemState = {
  payload: null,
  error: '',
  inFlight: false,
  updatedAt: 0,
};
const jsDebugLogLevels = Object.freeze(['info', 'warning', 'debug', 'error']);
// Fresh state hides the chatty info/debug levels; warnings and errors are the
// signal a first-time viewer needs. Info/Debug remain one toggle away and their
// selection persists (see save/load of jsDebugStatsUiPreferences).
const jsDebugLogDefaultLevels = Object.freeze(['warning', 'error']);
const jsDebugLogsState = {
  payload: [],
  error: '',
  inFlight: false,
  updatedAt: 0,
  clearedAt: 0,
  levels: new Set(jsDebugLogDefaultLevels),
};
let jsDebugGraphRangeSeconds = jsDebugGraphDefaultRangeSeconds;
let jsDebugGraphResolutionOverrideSeconds = 0;
// When a Resolution change needs a history fetch, this holds the value to restore and the
// history generation to match so a stale response cannot revert a newer request. Cleared
// on the matching ready (success) or error (revert + toast). Null when the last change was
// served from cache (instant, no overlay).
let jsDebugGraphPendingResolutionChange = null;
let jsDebugGraphChartLayout = 0;
const jsDebugStatsPollState = {
  inFlight: false,
  pending: false,
  pendingForceGraphRefresh: false,
  firstSampleReceived: false,
  lastSampleAtMs: 0,
};
// Background prefetch of the full retention window into the shared bucket cache so a
// range/zoom switch renders cached (stale) content instantly while the normal poll
// revalidates the switched-to range on top. Pure cache-fill: it never touches the
// readiness state machine or overlay (the current view owns those).
const jsDebugHistoryPrefetchState = {
  inFlight: false,
  didInitial: false,
  lastFullPrefetchAtMs: 0,
  // Bumped whenever the bucket cache is cleared; an in-flight prefetch whose fetch
  // resolves after a clear must NOT apply its (now stale) buckets.
  generation: 0,
};
const jsDebugStatsUploadState = {
  timer: null,
  worker: null,
  generation: 0,
};
let jsDebugStatsServerSequence = 0;
let jsDebugStatsServerUptimeSeconds = null;
let jsDebugStatsServerPid = null;
let jsDebugStatsServerStartedAt = null;
let jsDebugStatsServerRssBytes = null;
let jsDebugStatsClientId = '';
let jsDebugStatsClientConnected = null;
let jsDebugStatsDisconnectStartedAtMs = null;
let jsDebugGraphZoomDomain = null;
let jsDebugGraphSelectionState = null;
// Last pointer type seen on a chart. Touch has no hover-without-contact, so a
// tap pins the value tooltip (it must NOT clear on the pointerleave that fires
// when the finger lifts); a mouse still clears on leave as before.
let jsDebugGraphLastPointerType = 'mouse';
let jsDebugGraphRangeSliderDragging = false;
let jsDebugGraphLiveFrame = 0;
let jsDebugGraphLiveFrameLastMs = 0;
let jsDebugGraphLiveFrameTicking = false;
let jsDebugCostAgeNextRefreshAtMs = 0;
let jsDebugCostPanelNextRefreshAtMs = 0;
let jsDebugGraphHiddenCharts = null;
let jsDebugGraphVisibleCharts = null;
let jsDebugStatsUiPreferencesLoaded = false;
// Output is the compatibility view: before component-level accounting existed,
// Model tokens/min was a projection of generated (output) transcript tokens.
const jsDebugGraphModelTokenDimensions = Object.freeze([
  Object.freeze({key: 'output', labelKey: 'debug.cost.output', fallback: 'Output'}),
  Object.freeze({key: 'all', labelKey: 'debug.modelTokens.allBillable', fallback: 'All billable'}),
  Object.freeze({key: 'input', labelKey: 'debug.cost.input', fallback: 'Input'}),
  Object.freeze({key: 'cacheRead', labelKey: 'debug.modelTokens.cacheRead', fallback: 'Cache hits & refreshes'}),
  Object.freeze({key: 'cacheWrite', labelKey: 'debug.modelTokens.cacheWrite', fallback: 'Cache write'}),
]);
let jsDebugGraphModelTokenDimension = 'output';
const jsDebugPricingRefreshState = {inFlight: false, error: '', status: '', timer: null, lastRequestedAtMs: 0};
const jsDebugCostComponentSortState = {key: 'cost', direction: 'desc'};
const jsDebugUsageAtomBackfill = {state: 'pending', sources: 0, missing: 0};
const jsDebugGraphRangeOptions = Object.freeze([
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
// User-directed Resolution picker universe. Deliberately DECOUPLED from the AUTO
// effective-resolution set (jsDebugGraphDisplayBucketMs): each of these four values
// matches a durable graduated retention tier (raw 1s + the 10/60/300s bands), so an
// explicit pick is served from the graduated buckets — buckets at or coarser than the
// pick pass through unchanged; finer newer buckets group up to it at serve time.
// AUTO/effective clamping may still RENDER coarser values (e.g. 600s for the oldest
// retention windows) — that honest retained resolution is shown in the label, not the
// picker. Persisted/deeplinked out-of-set overrides normalize into this set.
const jsDebugGraphResolutionChoices = Object.freeze([1, 10, 60, 300]);
// Rendered-point cap for EXPLICIT overrides. AUTO is already bounded by
// jsDebugGraphMaxDisplayPoints; an explicit override that would render more than this
// many buckets for the current domain is clamped up to the finest universe choice that
// stays within budget (the label then shows the effective, coarser value). This is what
// keeps a fine override from ballooning render time + RAM on a wide domain.
const jsDebugGraphOverridePointCap = 600;
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
const jsDebugStatsCoarsePollMs = 60001;
// Full-retention background prefetch: one request spans the whole retention window and
// (via the server's per-span tiers) returns a few hundred coarse buckets covering EVERY
// range, so every range switch renders from cache. The current short range keeps its own
// fast live cadence (1s SSE / minute poll); this only refreshes the wider windows, which
// change slowly -> a several-minute cadence keeps them fresh cheaply (the 24h window is
// re-pulled by the same request but barely moves between pulls).
const jsDebugHistoryPrefetchRetentionSeconds = Math.floor(jsDebugGraphRetentionMs / 1000);
const jsDebugHistoryPrefetchIntervalMs = 5 * 60 * 1000;
const jsDebugStatsLivePushRangeSeconds = 30 * 60;
// Wall-clock slide cadence for live (<=30m, non-zoomed) views. One render per
// second advances the axis and drifts content left without a data tick.
// Display/animation cadence (AGENTS.md timing rule) -> round 1000, not an odd
// backend poll interval: this only re-paints the view, it never fetches.
const jsDebugGraphSlideRenderMs = 1000;
// The wall-clock slide extends to 1h, independent of the 30m SSE-demand range: a live,
// non-zoomed view up to an hour re-renders ~1/sec so its axis advances and content drifts
// left between the coarser (60s) data fetches — the chart stays visibly live even where
// data no longer streams. Ranges over 1h, zoomed, and hidden views stay static.
const jsDebugGraphSlideMaxRangeSeconds = 60 * 60;
const jsDebugStatsPollTimeoutMs = 8000;
const jsDebugStatsHistoryMaxTimeoutMs = 30000;
const jsDebugStatsHistoryFlushMs = 30000;
const jsDebugGraphRefreshMs = 30001;
// A request-driven client can be quiet between normal polls. Only mark the portion
// after this continuous silence as missing communication, rather than treating each
// empty raw bucket as a connection failure.
const jsDebugGraphNoDataOverlayDelayMs = 30000;
const jsDebugStatsHistoryMaxPoints = 6000;
const jsDebugStatsHistoryPostMaxRecords = 1000;
const jsDebugStatsHistoryPostMaxBytes = 96 * 1024;
const jsDebugStatsClientStorageKey = 'yolomux.stats.client_id.v1';
const jsDebugStatsDisconnectedStorageKey = 'yolomux.stats.disconnected_at.v1';
const jsDebugStatsUiPreferencesStorageKey = 'yolomux.stats.ui_preferences.v1';
const jsDebugGraphDefaultHiddenChartKeys = Object.freeze(['serversLoad', 'memory', 'gpuUtil', 'gpuMemory', 'costSummary']);
const jsDebugGraphMovingAverageSamples = 10;
const jsDebugGraphAgentTokenBucketSeconds = 60;
const jsDebugGraphDisplayHoldExpiryMs = Object.freeze({
  tenSecondGauge: 10 * 1000,
  minuteGauge: 60 * 1000,
});
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
    descKey: 'debug.graph.sumDisplayedClientRequests.desc',
    value: buckets => debugGraphDisplayedClientFieldSum(buckets, ['apiCount', 'sseCount']),
    format: debugGraphTokenNumberText,
  },
  bandwidth: {
    attribute: 'displayed-bandwidth-sum',
    labelKey: 'debug.graph.sumDisplayed',
    descKey: 'debug.graph.sumDisplayed.desc',
    value: buckets => debugGraphDisplayedClientFieldSum(buckets, ['bandwidthBytes']),
    format: value => debugGraphValueText(value, 'bytes'),
  },
  agentTokens: {
    attribute: 'displayed-token-sum',
    labelKey: 'debug.graph.sumDisplayed',
    descKey: 'debug.graph.sumDisplayed.desc',
    value: debugGraphAgentTokenDisplayedSum,
    format: debugGraphTokenNumberText,
  },
  modelTokens: {
    attribute: 'displayed-token-sum',
    labelKey: 'debug.graph.sumDisplayed',
    descKey: 'debug.graph.sumDisplayed.desc',
    value: debugGraphModelTokenDisplayedSum,
    format: debugGraphTokenNumberText,
  },
});
const jsDebugGraphDescriptionKeyByLabelKey = Object.freeze({
  'debug.graph.metric.api': 'debug.graph.metric.api.desc',
  'debug.graph.metric.sse': 'debug.graph.metric.sse.desc',
  'debug.graph.metric.bandwidth': 'debug.graph.metric.bandwidth.desc',
  'debug.graph.meta.removal': 'debug.graph.meta.removal.desc',
  'debug.graph.meta.rss': 'debug.graph.meta.rss.desc',
  'debug.graph.meta.serverSequence': 'debug.graph.meta.serverSequence.desc',
  'debug.graph.meta.totalTraffic': 'debug.graph.meta.totalTraffic.desc',
  'debug.graph.meta.uptime': 'debug.graph.meta.uptime.desc',
  'debug.graph.status.attention': 'debug.graph.status.attention.desc',
  'debug.graph.status.transition': 'debug.graph.status.transition.desc',
  'debug.graph.series.allAgentsTotal': 'debug.graph.series.allAgentsTotal.desc',
  'debug.graph.series.allClientsApiSseTotal': 'debug.graph.series.allClientsApiSseTotal.desc',
  'debug.graph.series.defaultProcessCpu': 'debug.graph.series.defaultProcessCpu.desc',
  'debug.graph.series.otherClientsAverage': 'debug.graph.series.otherClientsAverage.desc',
  'debug.graph.series.processCpu': 'debug.graph.series.processCpu.desc',
  'debug.graph.series.systemCpu': 'debug.graph.series.systemCpu.desc',
  'debug.graph.series.systemMemory': 'debug.graph.series.systemMemory.desc',
  'debug.graph.series.thisClient': 'debug.graph.series.thisClient.desc',
  'debug.graph.series.tokensPerAgent': 'debug.graph.series.tokensPerAgent.desc',
  'debug.graph.sumDisplayed': 'debug.graph.sumDisplayed.desc',
  'debug.graph.sumDisplayedClientRequests': 'debug.graph.sumDisplayedClientRequests.desc',
  'debug.modelTokens.allBillable': 'debug.modelTokens.allBillable.desc',
  'debug.modelTokens.cacheRead': 'debug.modelTokens.cacheRead.desc',
  'debug.modelTokens.cacheWrite': 'debug.modelTokens.cacheWrite.desc',
  'state.idle': 'debug.graph.status.idle.desc',
  'state.working': 'debug.graph.status.working.desc',
});
const jsDebugGraphClientMetrics = Object.freeze([
  {key: 'api', labelKey: 'debug.graph.metric.api', unit: 'countPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.apiCount), hasData: bucket => Number(bucket.apiCount || 0) > 0},
  {key: 'sse', labelKey: 'debug.graph.metric.sse', unit: 'countPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.sseCount), hasData: bucket => Number(bucket.sseCount || 0) > 0},
  {key: 'latency', labelKey: 'common.clientLatency', unit: 'ms', value: bucket => bucket.latencyCount ? bucket.latencyTotalMs / bucket.latencyCount : 0, hasData: bucket => Number(bucket.latencyCount || 0) > 0},
  {key: 'bandwidth', labelKey: 'debug.graph.metric.bandwidth', unit: 'bytesPerSecond', value: bucket => debugGraphBucketRate(bucket, bucket.bandwidthBytes), hasData: bucket => Number(bucket.bandwidthBytes || 0) > 0},
]);
const jsDebugGraphAgentTokenSeriesPrefix = 'agentToken:';
const jsDebugGraphModelTokenSeriesPrefix = 'modelToken:';
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
// THE one client display cache: every retained bucket of every tier lives here,
// keyed `${startMs}:${durationMs}`. Tier membership is the key's durationMs (the
// jsDebugGraphTiers graduated-compaction owner rewrites keys as buckets age); the
// former raw/rollup Map split was only bookkeeping over the same keyspace.
const jsDebugGraphBuckets = new Map();
const jsDebugGraphEventRecords = new Map();
// NOT display cache: upload staging for client-observed metrics awaiting POST
// /api/stats-history (snake_case wire records; drained by flushJsDebugStatsHistory,
// re-queued on failure). Its lifecycle is pending-until-acknowledged, so it cannot
// merge into the display Map above without losing upload dedupe on flush/clear.
const jsDebugGraphPendingServerBuckets = new Map();
const jsDebugGraphHoverChartData = new Map();
const jsDebugGraphSeries = Object.freeze([
  ...jsDebugGraphClientMetrics.map(metric => debugGraphClientSeriesDef(metric, {labelKey: 'debug.graph.series.thisClient', clientId: jsDebugGraphThisClientId, clientAggregate: jsDebugGraphThisClientAggregate, clientLinePattern: jsDebugGraphThisClientLinePattern})),
  ...jsDebugAgentStatusSeriesKeys.map(debugGraphAgentStatusSeriesDef),
  {key: 'tokensPerAgent', labelKey: 'debug.graph.series.tokensPerAgent', unit: 'tokensPerMinute', value: bucket => bucket.agentTokenSamples ? bucket.tokensPerAgentTotal / bucket.agentTokenSamples : 0, hasData: bucket => Number(bucket?.agentTokenSamples || 0) > 0},
  {key: 'systemCpu', labelKey: 'debug.graph.series.systemCpu', unit: 'percent', linePattern: 'solid', value: bucket => bucket.systemCpuCount ? Math.min(100, bucket.systemCpuTotalPercent / bucket.systemCpuCount) : 0, hasData: bucket => Number(bucket?.systemCpuCount || 0) > 0},
  {
    key: 'systemMemory', labelKey: 'debug.graph.series.systemMemory', unit: 'bytes', linePattern: 'solid',
    value: bucket => bucket.hostMetrics?.systemMemoryCount ? bucket.hostMetrics.systemMemoryUsedTotalBytes / bucket.hostMetrics.systemMemoryCount : 0,
    hasData: bucket => Number(bucket?.hostMetrics?.systemMemoryCount || 0) > 0,
    sampleCount: bucket => Number(bucket?.hostMetrics?.systemMemoryCount || 0),
    displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.minuteGauge,
  },
]);
// Mirror of yolomux_lib/stats_families.py — the ONE YO!stats family manifest.
// Per family: the canonical name (identical to the server's
// stats_coverage_intervals family), the legacy alias names an OLDER server may
// still write into coverage payloads (canonical is tried first), the true
// sampler cadence, and the owning chart groups / series. Coverage lookups and
// chart->family mapping READ this table; inline per-family if/alias chains
// outside it are contract-banned (tests/yostats_performance.test.js pins both
// mirrors against each other). modelTokenDimension names which modelTokens
// chart dimension the family backs ('output' is the generated-token counter;
// 'default' covers every billing dimension).
const jsDebugStatsFamilyManifest = Object.freeze({
  cpu: Object.freeze({legacyAliases: Object.freeze(['server', 'raw', 'buckets']), cadenceSeconds: 1, chartGroups: Object.freeze(['cpu']), series: Object.freeze(['systemCpu'])}),
  service_load: Object.freeze({legacyAliases: Object.freeze([]), cadenceSeconds: 10, chartGroups: Object.freeze([]), series: Object.freeze([])}),
  agent_status: Object.freeze({legacyAliases: Object.freeze(['status']), cadenceSeconds: 10, chartGroups: Object.freeze(['activity']), series: jsDebugAgentStatusSeriesKeys}),
  agent_tokens: Object.freeze({legacyAliases: Object.freeze(['tokens']), cadenceSeconds: 10, idleCadenceSeconds: 60, chartGroups: Object.freeze(['agentTokens']), modelTokenDimension: 'output', series: Object.freeze(['tokensPerAgent'])}),
  cost: Object.freeze({legacyAliases: Object.freeze(['cost_atoms', 'usage_atoms']), cadenceSeconds: 10, idleCadenceSeconds: 60, chartGroups: Object.freeze([]), modelTokenDimension: 'default', series: Object.freeze([])}),
  gpu: Object.freeze({legacyAliases: Object.freeze(['gpu_metrics']), cadenceSeconds: 10, chartGroups: Object.freeze(['gpuUtil', 'gpuMemory']), series: Object.freeze([])}),
  system_memory: Object.freeze({legacyAliases: Object.freeze(['memory']), cadenceSeconds: 60, chartGroups: Object.freeze(['memory']), series: Object.freeze(['systemMemory'])}),
});
const jsDebugStatsFamilyByChartGroup = Object.freeze(Object.fromEntries(Object.entries(jsDebugStatsFamilyManifest)
  .flatMap(([family, entry]) => entry.chartGroups.map(group => [group, family]))));
const jsDebugStatsFamilyByModelTokenDimension = Object.freeze(Object.fromEntries(Object.entries(jsDebugStatsFamilyManifest)
  .filter(([, entry]) => entry.modelTokenDimension).map(([family, entry]) => [entry.modelTokenDimension, family])));
const jsDebugGraphChartGroups = Object.freeze([
  {key: 'cpu', labelKey: 'debug.graph.chart.cpu', descKey: 'debug.graph.chart.cpu.desc', toggleLabelEn: 'CPU', series: ['systemCpu'], unit: 'percent', fixedMax: 100, hostMetric: 'cpu'},
  {key: 'serversLoad', labelKey: 'debug.graph.chart.serversLoad', descKey: 'debug.graph.chart.serversLoad.desc', toggleLabelEn: 'Servers load', series: [], unit: 'percent', serviceLoad: true, bucketSeconds: jsDebugStatsFamilyManifest.service_load.cadenceSeconds},
  {key: 'memory', labelKey: 'debug.graph.chart.memory', descKey: 'debug.graph.chart.memory.desc', toggleLabelEn: 'Sys mem', series: ['systemMemory'], unit: 'bytes', kind: 'area', stacked: true, hostMetric: 'memory', capacityMetric: 'systemMemory'},
  {key: 'activity', labelKey: 'debug.graph.chart.agentStatus', descKey: 'debug.graph.chart.agentStatus.desc', toggleLabelEn: 'Agent #', series: jsDebugAgentStatusSeriesKeys, legendSeries: jsDebugAgentStatusLegendSeriesKeys, unit: 'count', kind: 'bar', stacked: true, integerAxis: true, integerGridLines: true, exactIntegerAxisMax: true, minimumAxisMax: 4, bucketSeconds: jsDebugStatsFamilyManifest.agent_status.cadenceSeconds, statusNoDataOverlay: true},
  {key: 'agentTokens', labelKey: 'debug.graph.chart.agentTokens', descKey: 'debug.graph.chart.agentTokens.desc', toggleLabelEn: 'Agent tokens', series: [], unit: 'tokensPerMinute', kind: 'bar', stacked: true, dynamicAgentTokens: true, displayedSummary: 'agentTokens', bucketSeconds: jsDebugGraphAgentTokenBucketSeconds},
  {key: 'modelTokens', labelKey: 'debug.graph.chart.modelTokens', descKey: 'debug.graph.chart.modelTokens.desc', toggleLabelEn: 'Model tokens', series: [], unit: 'tokensPerMinute', kind: 'bar', stacked: true, dynamicTokenDimension: 'model', displayedSummary: 'modelTokens', bucketSeconds: jsDebugGraphAgentTokenBucketSeconds},
  {key: 'gpuUtil', labelKey: 'debug.graph.chart.gpuUtil', descKey: 'debug.graph.chart.gpuUtil.desc', toggleLabelEn: 'GPU', series: [], unit: 'percent', fixedMax: 100, kind: 'bar', zeroBar: true, hostMetric: 'gpuUtil'},
  {key: 'gpuMemory', labelKey: 'debug.graph.chart.gpuMemory', descKey: 'debug.graph.chart.gpuMemory.desc', toggleLabelEn: 'GPU mem', series: [], unit: 'bytes', hostMetric: 'gpuMemory', capacityMetric: 'gpuMemory'},
  {key: 'latency', labelKey: 'common.clientLatency', descKey: 'debug.graph.chart.latency.desc', toggleLabelEn: 'Latency', series: ['latency'], unit: 'ms', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'count', labelKey: 'debug.graph.chart.clientApiSse', descKey: 'debug.graph.chart.clientApiSse.desc', toggleLabelEn: 'API&SSE', series: ['api', 'sse'], unit: 'countPerSecond', displayedSummary: 'clientRequests', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'bandwidth', labelKey: 'debug.graph.chart.clientBandwidth', descKey: 'debug.graph.chart.clientBandwidth.desc', toggleLabelEn: 'Bandwidth', series: ['bandwidth'], unit: 'bytesPerSecond', displayedSummary: 'bandwidth', disconnectedOverlay: true, noDataOverlay: true},
]);
const jsDebugGraphChartControlItems = Object.freeze(jsDebugGraphChartGroups.flatMap(group => group.key === 'modelTokens'
  ? [group, Object.freeze({key: 'costSummary', labelKey: 'debug.cost.title', toggleLabelEn: 'Cost'})]
  : [group]));

function debugGraphLocalizedLabel(item = {}) {
  if (!item.labelKey) return String(item.label || '');
  const params = {...(item.labelParams || {})};
  if (item.metricLabelKey) params.metric = t(item.metricLabelKey);
  return t(item.labelKey, params);
}

function debugGraphLocalizedDescription(item = {}) {
  const descKey = item.descKey || jsDebugGraphDescriptionKeyByLabelKey[item.labelKey];
  if (!descKey) return '';
  const params = {...(item.descParams || item.labelParams || {})};
  if (item.metricLabelKey) params.metric = t(item.metricLabelKey);
  return t(descKey, params);
}

function debugGraphExplainAttrs(label, descKey, {attribute = 'data-js-debug-explain', desc = '', params = {}} = {}) {
  if (!descKey) return '';
  const text = desc || t(descKey, params);
  if (!text || text === descKey) return '';
  return ` title="${esc(text)}" aria-label="${esc(`${label}: ${text}`)}" ${attribute}="${esc(descKey)}"`;
}

function normalizedJsDebugSubTab(value) {
  return value === 'events' || value === 'system' || value === 'logs' ? value : 'graph';
}

function normalizedJsDebugGraphRange(value, nowMs = Date.now()) {
  const seconds = Number(value);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (options.some(option => option.seconds === seconds)) return seconds;
  if (seconds === 60) return options[0]?.seconds || jsDebugGraphDefaultRangeSeconds;
  if (options.some(option => option.seconds === jsDebugGraphDefaultRangeSeconds)) return jsDebugGraphDefaultRangeSeconds;
  return options[0]?.seconds || jsDebugGraphDefaultRangeSeconds;
}

function activeJsDebugGraphRangeSeconds(nowMs = Date.now()) {
  jsDebugGraphRangeSeconds = normalizedJsDebugGraphRange(jsDebugGraphRangeSeconds, nowMs);
  syncDebugGraphResolutionOverride(nowMs, {persist: true});
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
  jsDebugGraphRangeSeconds = normalizedJsDebugGraphRange(saved.rangeSeconds);
  jsDebugGraphResolutionOverrideSeconds = Math.max(0, Number(saved.resolutionOverrideSeconds) || 0);
  jsDebugGraphChartLayout = Math.max(0, Math.min(4, Math.round(Number(saved.chartLayout) || 0)));
  jsDebugGraphModelTokenDimension = jsDebugGraphModelTokenDimensions.some(item => item.key === String(saved.modelTokenDimension || ''))
    ? String(saved.modelTokenDimension)
    : 'output';
  const hidden = new Set(jsDebugGraphDefaultHiddenChartKeys);
  const visible = new Set(Array.isArray(saved.visibleCharts) ? saved.visibleCharts.map(value => String(value || '')) : []);
  for (const key of visible) hidden.delete(key);
  for (const key of Array.isArray(saved.hiddenCharts) ? saved.hiddenCharts : []) hidden.add(String(key || ''));
  jsDebugGraphHiddenCharts = hidden;
  jsDebugGraphVisibleCharts = visible;
  // Respect a previously-persisted level selection (including an intentionally
  // empty one); only fresh state falls back to the warning+error default.
  const storedLogLevels = Array.isArray(saved.logLevels)
    ? saved.logLevels.map(value => String(value || '')).filter(value => jsDebugLogLevels.includes(value))
    : null;
  jsDebugLogsState.levels = new Set(storedLogLevels || jsDebugLogDefaultLevels);
  syncDebugGraphResolutionOverride(Date.now(), {persist: true});
}

function saveJsDebugStatsUiPreferences() {
  if (!jsDebugStatsUiPreferencesLoaded) return;
  try {
    window.localStorage?.setItem(jsDebugStatsUiPreferencesStorageKey, JSON.stringify({
      subTab: jsDebugSubTab,
      rangeSeconds: jsDebugGraphRangeSeconds,
      resolutionOverrideSeconds: jsDebugGraphResolutionOverrideSeconds,
      chartLayout: jsDebugGraphChartLayout,
      modelTokenDimension: jsDebugGraphModelTokenDimension,
      hiddenCharts: [...debugGraphHiddenChartKeys()].sort(),
      visibleCharts: [...(jsDebugGraphVisibleCharts instanceof Set ? jsDebugGraphVisibleCharts : [])].sort(),
      logLevels: [...jsDebugLogsState.levels].sort(),
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
  const chartKey = String(key || '');
  if (chartKey === 'modelTokens' && !jsDebugGraphVisibleCharts.has(chartKey)) return false;
  return !debugGraphHiddenChartKeys().has(chartKey);
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
  // A direct toggle/close owns this mutation. Passive SSE/timer paints defer
  // while a graph control is focused, but deferring the user's own activation
  // leaves aria-pressed and the chart body visibly stale until focus moves.
  refreshDebugGraphSurfaces({deferFocusedControl: false});
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
  return String(state?.phase || '') === 'loading';
}

// Error-like states share retry backoff semantics: an explicit error, or a loading
// pass that exists to retry one (reason is '' outside the loading phase).
function jsDebugHistoryReadinessErrorLike(state = jsDebugHistoryReadiness) {
  return state?.phase === 'error' || state?.reason === 'retry';
}

// The composite state name the DOM contract, snapshot, and diagnostics emit:
// loading + reason folds back to the historical loading-initial / loading-older /
// retrying strings; every other phase passes through unchanged.
function jsDebugHistoryReadinessStateName(state = jsDebugHistoryReadiness) {
  if (state?.phase !== 'loading') return String(state?.phase || 'idle');
  return jsDebugHistoryLegacyPhaseByReason[state.reason] || 'loading-initial';
}

function jsDebugHistoryReadinessSnapshot() {
  const state = jsDebugHistoryReadiness;
  return {
    phase: jsDebugHistoryReadinessStateName(state),
    reason: state.reason,
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
    requestCoverageIntervals: state.requestCoverageIntervals.map(interval => ({...interval})),
    storeCoverageIntervals: Object.fromEntries(Object.entries(state.storeCoverageIntervals || {}).map(([key, intervals]) => [key, intervals.map(interval => ({...interval}))])),
    attemptCount: state.attemptCount,
    error: state.error,
    generation: state.generation,
    nextAutoRetryAtMs: state.nextAutoRetryAtMs,
    overlayVisible: state.overlayVisible,
    busy: jsDebugHistoryReadinessBusy(state),
  };
}

function jsDebugHistoryRetryDelayMs(attemptCount = jsDebugHistoryReadiness.attemptCount) {
  const attempts = Math.max(1, Number(attemptCount) || 1);
  const multiplier = 2 ** Math.min(8, attempts - 1);
  return Math.min(jsDebugHistoryRetryMaxDelayMs, jsDebugHistoryRetryInitialDelayMs * multiplier);
}

function jsDebugHistoryAutoRetryDue(state = jsDebugHistoryReadiness, nowMs = performanceNow()) {
  return !jsDebugHistoryReadinessErrorLike(state) || Number(state.nextAutoRetryAtMs || 0) <= Number(nowMs || 0);
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
    graph.dataset.jsDebugHistoryState = jsDebugHistoryReadinessStateName(state);
    let overlay = graph.querySelector('[data-js-debug-history-overlay]');
    if (!overlay && (busy || state.phase === 'error')) {
      refreshDebugGraphElement(graph, {force: true});
      overlay = graph.querySelector('[data-js-debug-history-overlay]');
    }
    if (!overlay) continue;
    overlay.hidden = state.overlayVisible !== true;
    if (overlay.innerHTML !== content) overlay.innerHTML = content;
  }
  // YO!cost chart areas are not [data-js-debug-graph] surfaces (the graph-refresh
  // loops would rebuild them with YO!stats content); toggle their always-present
  // shared overlay directly so range/resolution changes show "Loading…" there too.
  for (const area of document.querySelectorAll('[data-js-yocost-chart-area]')) {
    area.setAttribute('aria-busy', busy ? 'true' : 'false');
    area.dataset.jsDebugHistoryState = jsDebugHistoryReadinessStateName(state);
    const overlay = area.querySelector('[data-js-debug-history-overlay]');
    if (!overlay) continue;
    overlay.hidden = state.overlayVisible !== true;
    if (overlay.innerHTML !== content) overlay.innerHTML = content;
  }
}

function setJsDebugHistoryReadiness(phase, updates = {}) {
  // Legacy composite names (loading-initial / loading-older / retrying) remain valid
  // inputs — tests and older callers use them — and normalize to loading + reason.
  const requestedPhase = String(phase || 'idle');
  const legacyReason = jsDebugHistoryReadinessReasonByLegacyPhase[requestedPhase];
  const nextPhase = legacyReason ? 'loading' : requestedPhase;
  if (!jsDebugHistoryReadinessPhases.includes(nextPhase)) throw new Error(`unknown YO!stats history state: ${nextPhase}`);
  const nextReason = nextPhase === 'loading'
    ? (legacyReason || (jsDebugHistoryLegacyPhaseByReason[String(updates.reason)] ? String(updates.reason) : 'initial'))
    : '';
  const state = jsDebugHistoryReadiness;
  const previousStateName = jsDebugHistoryReadinessStateName(state);
  const wasBusy = jsDebugHistoryReadinessBusy(state);
  const previousStartedAt = Number(state.loadingStartedAtMs) || 0;
  clearJsDebugHistoryOverlayTimer();
  for (const field of ['requestedRangeSeconds', 'targetStartSeconds', 'targetEndSeconds', 'requestedStartSeconds', 'requestedEndSeconds', 'requestedResolutionSeconds', 'loadedStartSeconds', 'loadedEndSeconds', 'resolutionSeconds', 'coverageIntervals', 'requestCoverageIntervals', 'storeCoverageIntervals', 'attemptCount', 'error', 'generation', 'loadingStartedAtMs', 'nextAutoRetryAtMs']) {
    if (Object.prototype.hasOwnProperty.call(updates, field)) state[field] = updates[field];
  }
  state.phase = nextPhase;
  state.reason = nextReason;
  const busy = jsDebugHistoryReadinessBusy(state);
  // Older/refined loads keep the current chart and delay the overlay by 120ms to
  // avoid a flash; initial/retry loads and errors surface the overlay immediately.
  const olderLoad = nextPhase === 'loading' && nextReason === 'older';
  state.overlayVisible = (nextPhase === 'loading' && !olderLoad) || nextPhase === 'error';
  if (olderLoad && typeof setTimeout === 'function') {
    const generation = state.generation;
    state.overlayTimer = setTimeout(() => {
      state.overlayTimer = null;
      if (state.phase !== 'loading' || state.reason !== 'older' || state.generation !== generation) return;
      state.overlayVisible = true;
      syncJsDebugHistoryReadinessSurfaces();
    }, jsDebugHistoryOlderOverlayDelayMs);
  }
  if (wasBusy && !busy) {
    recordClientPerfCounter('statsHistoryLoading', performanceNow() - previousStartedAt, {state: jsDebugHistoryReadinessStateName(state), previousState: previousStateName});
    state.loadingStartedAtMs = 0;
  }
  syncJsDebugHistoryReadinessSurfaces();
  resolveDebugGraphResolutionChange(state);
  return jsDebugHistoryReadinessSnapshot();
}

function beginJsDebugHistoryReadiness(requestedStartSeconds, {requestedEndSeconds = 0, targetStartSeconds = requestedStartSeconds, targetEndSeconds = requestedEndSeconds, requestedResolutionSeconds = 1, retry = false} = {}) {
  const state = jsDebugHistoryReadiness;
  const generation = Number(state.generation || 0) + 1;
  const snapshot = setJsDebugHistoryReadiness('loading', {
    reason: retry ? 'retry' : (Number(state.loadedStartSeconds) > 0 ? 'older' : 'initial'),
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
    nextAutoRetryAtMs: 0,
  });
  if (retry) recordJsDebugStatsDiagnostic('warning', `retry entered (attempt ${snapshot.attemptCount}) for unavailable history coverage`);
  return snapshot;
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
  const fallbackResolution = Number(raw.resolution_seconds);
  const fallbackSourceResolution = Number(raw.source_resolution_seconds);
  // Degrade granularly rather than rejecting a whole multi-family response so a
  // single malformed family can never blank every chart. The interval-count
  // bound always degrades by capping to the most recent entries (never a hard
  // reject). The top-level required list stays structurally strict (a reversed
  // or non-object interval is a real contract violation), but a per-family
  // store list is lenient: an individual bad interval is skipped (its span
  // renders as honest no-data) and a structurally-unusable family is dropped,
  // keeping every other family's charts alive.
  const normalizeIntervals = (intervals, {strict = false} = {}) => {
    if (!Array.isArray(intervals)) return null;
    const bounded = intervals.length > jsDebugHistoryCoverageIntervalLimit
      ? intervals.slice(-jsDebugHistoryCoverageIntervalLimit)
      : intervals;
    const normalized = [];
    for (const interval of bounded) {
      if (!interval || typeof interval !== 'object' || Array.isArray(interval)) {
        if (strict) return null;
        continue;
      }
      const startSeconds = Number(interval.start ?? interval.start_seconds);
      const endSeconds = Number(interval.end ?? interval.end_seconds);
      const resolutionSeconds = Number(interval.resolution_seconds ?? interval.resolution ?? fallbackResolution);
      const sourceResolutionSeconds = Number(interval.source_resolution_seconds ?? interval.source_resolution ?? fallbackSourceResolution) || 0;
      if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds) {
        if (strict) return null;
        continue;
      }
      if (!Number.isFinite(resolutionSeconds) || resolutionSeconds <= 0) {
        if (strict) return null;
        continue;
      }
      if (!Number.isFinite(sourceResolutionSeconds) || sourceResolutionSeconds < 0) {
        if (strict) return null;
        continue;
      }
      normalized.push({
        startSeconds,
        endSeconds,
        resolutionSeconds,
        sourceResolutionSeconds,
        ...(interval.epoch_id != null ? {epochId: String(interval.epoch_id)} : {}),
      });
    }
    return mergeJsDebugHistoryCoverageIntervals(normalized);
  };
  const intervals = normalizeIntervals(raw.intervals, {strict: true});
  if (!intervals) return null;
  const rawStores = raw.store_intervals ?? raw.family_intervals ?? {};
  if (!rawStores || typeof rawStores !== 'object' || Array.isArray(rawStores)) return null;
  const storeIntervals = {};
  const droppedFamilies = [];
  for (const [key, value] of Object.entries(rawStores)) {
    const normalized = normalizeIntervals(value);
    if (!normalized) {
      droppedFamilies.push(String(key));
      continue;
    }
    storeIntervals[String(key)] = normalized;
  }
  if (droppedFamilies.length) {
    recordJsDebugStatsDiagnostic('warning', `coverage degraded: dropped malformed families ${droppedFamilies.join(', ')}; other families render`);
  }
  const intervalStart = intervals.length ? Math.min(...intervals.map(interval => interval.startSeconds)) : 0;
  const intervalEnd = intervals.length ? Math.max(...intervals.map(interval => interval.endSeconds)) : 0;
  const coverage = {
    mode: raw.mode === 'older' ? 'older' : 'live',
    requestedStart: Number(raw.requested_start),
    requestedEnd: Number(raw.requested_end),
    coveredStart: intervalStart,
    coveredEnd: intervalEnd,
    resolutionSeconds: Number.isFinite(fallbackResolution) && fallbackResolution > 0 ? fallbackResolution : (intervals[0]?.resolutionSeconds || 1),
    sourceResolutionSeconds: Number.isFinite(fallbackSourceResolution) && fallbackSourceResolution > 0 ? fallbackSourceResolution : 0,
    complete: raw.complete === true,
    hasMoreOlder: raw.has_more_older === true,
    nextOlderEnd: Number(raw.next_older_end),
    intervals,
    storeIntervals,
    epochs: Array.isArray(raw.epochs) ? raw.epochs.slice(0, jsDebugHistoryCoverageIntervalLimit) : [],
  };
  if (!Number.isFinite(coverage.resolutionSeconds) || coverage.resolutionSeconds <= 0) coverage.resolutionSeconds = 1;
  if (!Number.isFinite(coverage.sourceResolutionSeconds) || coverage.sourceResolutionSeconds <= 0) coverage.sourceResolutionSeconds = 0;
  return coverage;
}

function normalizedJsDebugHistoryPending(history = {}) {
  const coverage = history?.coverage;
  if (!coverage || typeof coverage !== 'object' || coverage.pending !== true) return null;
  const retrySeconds = Number(coverage.retry_after_seconds ?? coverage.retry_after_s ?? 1);
  return {
    retryAfterMs: Math.max(1000, Math.min(60_000, Number.isFinite(retrySeconds) ? retrySeconds * 1000 : 1000)),
    reason: String(coverage.reason || 'Backfill in progress'),
  };
}

function jsDebugHistoryIntervalSummary(intervals) {
  const values = Array.isArray(intervals) ? intervals : [];
  if (!values.length) return '0 intervals';
  const start = Math.min(...values.map(interval => Number(interval.startSeconds)));
  const end = Math.max(...values.map(interval => Number(interval.endSeconds)));
  return `${values.length} interval${values.length === 1 ? '' : 's'} [${Math.floor(start)}..${Math.ceil(end)}]`;
}

function recordJsDebugHistoryCoverageDiagnostic(coverage, request) {
  const requestStart = Number(request?.targetStartSeconds ?? coverage?.requestedStart);
  const requestEnd = Number(request?.targetEndSeconds ?? coverage?.requestedEnd);
  const stores = Object.entries(coverage?.storeIntervals || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, intervals]) => `${key}=${jsDebugHistoryIntervalSummary(intervals)}`)
    .join(', ') || 'compatibility-global';
  const explicitEpochs = Array.isArray(coverage?.epochs) ? coverage.epochs : [];
  const intervalEpochs = [...(coverage?.intervals || []), ...Object.values(coverage?.storeIntervals || {}).flat()]
    .filter(interval => interval?.epochId != null)
    .map(interval => ({id: interval.epochId, start: interval.startSeconds, end: interval.endSeconds}));
  const epochRows = explicitEpochs.length ? explicitEpochs : intervalEpochs;
  const epochIds = new Set(epochRows.map(epoch => String(epoch?.id ?? epoch?.epoch_id ?? 'boundary')));
  const epochStarts = epochRows.map(epoch => Number(epoch?.start ?? epoch?.start_seconds)).filter(Number.isFinite);
  const epochEnds = epochRows.map(epoch => Number(epoch?.end ?? epoch?.end_seconds)).filter(Number.isFinite);
  const epochSummary = epochRows.length
    ? `${epochIds.size}${epochStarts.length && epochEnds.length ? ` [${Math.floor(Math.min(...epochStarts))}..${Math.ceil(Math.max(...epochEnds))}]` : ''}`
    : '0';
  recordJsDebugStatsDiagnostic(
    'info',
    `coverage accepted: requested [${Math.floor(requestStart)}..${Math.ceil(requestEnd)}], global=${jsDebugHistoryIntervalSummary(coverage?.intervals)}, stores: ${stores}, epochs=${epochSummary}`,
  );
}

function mergeJsDebugHistoryCoverageIntervals(intervals) {
  const grouped = new Map();
  for (const interval of intervals || []) {
    const resolution = Number(interval?.resolutionSeconds);
    const sourceResolution = Number(interval?.sourceResolutionSeconds) || 0;
    const start = Number(interval?.startSeconds);
    const end = Number(interval?.endSeconds);
    if (!Number.isFinite(resolution) || resolution <= 0 || !Number.isFinite(start) || end <= start) continue;
    const epochId = interval?.epochId == null ? '' : String(interval.epochId);
    const key = `${resolution}:${sourceResolution}:${epochId}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push({startSeconds: start, endSeconds: end, resolutionSeconds: resolution, sourceResolutionSeconds: sourceResolution, ...(epochId ? {epochId} : {})});
  }
  const output = [];
  for (const items of grouped.values()) {
    items.sort((left, right) => left.startSeconds - right.startSeconds || right.endSeconds - left.endSeconds);
    for (const item of items) {
      const previous = output.at(-1);
      if (previous?.resolutionSeconds === item.resolutionSeconds && previous.sourceResolutionSeconds === item.sourceResolutionSeconds && previous.epochId === item.epochId && item.startSeconds <= previous.endSeconds) {
        previous.endSeconds = Math.max(previous.endSeconds, item.endSeconds);
      } else {
        output.push({...item});
      }
    }
  }
  return output.sort((left, right) => left.startSeconds - right.startSeconds || left.endSeconds - right.endSeconds);
}

function jsDebugHistoryReplaceIntervals(existing, replacement, startSeconds, endSeconds) {
  const kept = (existing || []).flatMap(interval => {
    if (interval.endSeconds <= startSeconds || interval.startSeconds >= endSeconds) return [interval];
    const pieces = [];
    if (interval.startSeconds < startSeconds) pieces.push({...interval, endSeconds: startSeconds});
    if (interval.endSeconds > endSeconds) pieces.push({...interval, startSeconds: endSeconds});
    return pieces;
  });
  return mergeJsDebugHistoryCoverageIntervals([...kept, ...(replacement || [])]);
}

function applyJsDebugHistoryCoverage(coverage, request = null) {
  if (!coverage) return jsDebugHistoryReadinessSnapshot();
  const state = jsDebugHistoryReadiness;
  const actualIntervals = Array.isArray(coverage.intervals)
    ? coverage.intervals
    : (coverage.coveredEnd > coverage.coveredStart ? [{
        startSeconds: coverage.coveredStart,
        endSeconds: coverage.coveredEnd,
        resolutionSeconds: coverage.resolutionSeconds,
        sourceResolutionSeconds: coverage.sourceResolutionSeconds,
      }] : []);
  if (coverage.coveredStart > 0) {
    state.loadedStartSeconds = Number(state.loadedStartSeconds) > 0
      ? Math.min(Number(state.loadedStartSeconds), coverage.coveredStart)
      : coverage.coveredStart;
  }
  if (coverage.coveredEnd > 0) state.loadedEndSeconds = Math.max(Number(state.loadedEndSeconds) || 0, coverage.coveredEnd);
  state.resolutionSeconds = coverage.resolutionSeconds;
  const targetStart = Number(request?.targetStartSeconds ?? coverage.requestedStart);
  const targetEnd = Number(request?.targetEndSeconds ?? coverage.requestedEnd);
  const intervalStart = actualIntervals.length ? Math.min(...actualIntervals.map(interval => interval.startSeconds)) : targetStart;
  const requestStart = coverage.mode === 'older'
    ? Number(coverage.requestedStart)
    : (coverage.hasMoreOlder ? intervalStart : targetStart);
  const olderEnd = Number(coverage.requestedEnd);
  const requestEnd = coverage.mode === 'older' && olderEnd > requestStart
    ? olderEnd
    : (targetEnd > requestStart ? targetEnd : Math.max(coverage.coveredEnd, requestStart));
  if (requestEnd > requestStart) {
    state.coverageIntervals = jsDebugHistoryReplaceIntervals(state.coverageIntervals, actualIntervals, requestStart, requestEnd);
    const storeKeys = new Set([...Object.keys(state.storeCoverageIntervals || {}), ...Object.keys(coverage.storeIntervals || {})]);
    const nextStores = {...state.storeCoverageIntervals};
    for (const key of storeKeys) {
      const replacement = Object.prototype.hasOwnProperty.call(coverage.storeIntervals || {}, key)
        ? coverage.storeIntervals[key]
        : actualIntervals;
      nextStores[key] = jsDebugHistoryReplaceIntervals(nextStores[key] || [], replacement, requestStart, requestEnd);
    }
    state.storeCoverageIntervals = nextStores;
    state.requestCoverageIntervals = mergeJsDebugHistoryCoverageIntervals([
      ...state.requestCoverageIntervals,
      {startSeconds: requestStart, endSeconds: requestEnd, resolutionSeconds: coverage.resolutionSeconds, sourceResolutionSeconds: coverage.sourceResolutionSeconds},
    ]);
  }
  if (coverage.hasMoreOlder && Number.isFinite(coverage.nextOlderEnd)) {
    state.requestedEndSeconds = coverage.nextOlderEnd;
  }
  return jsDebugHistoryReadinessSnapshot();
}

function jsDebugHistoryAcceptableResolutionSeconds(rangeStartSeconds, requestedResolutionSeconds, sourceResolutionSeconds, nowMs = Date.now()) {
  // The coarsest resolution a cached interval may use to satisfy this range. We trust an
  // interval's server-reported `sourceResolutionSeconds` (its retention floor) ONLY up to
  // the age-derived tier for the requested range start: a wide 16h/24h response stamps one
  // whole-query MAX(duration)=600s across ALL its coverage — including the recent portion
  // that truly retains 1s — and that inflated claim must not certify coarse data over a
  // recent domain. Capping at the range-start age tier keeps genuinely old 120s/300s/600s
  // retention acceptable (no infinite retry) while rejecting the stale wide-superset claim.
  const rangeTierSeconds = debugGraphBucketDurationForTime(Math.max(0, Number(rangeStartSeconds) || 0) * 1000, nowMs) / 1000;
  const trustedSource = Math.min(Math.max(0, Number(sourceResolutionSeconds) || 0), rangeTierSeconds);
  return Math.max(Number(requestedResolutionSeconds) || 0, trustedSource);
}

function jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, maxResolutionSeconds, nowMs = Date.now()) {
  const acceptableFor = interval => jsDebugHistoryAcceptableResolutionSeconds(startSeconds, maxResolutionSeconds, interval.sourceResolutionSeconds, nowMs);
  const intervals = jsDebugHistoryReadiness.requestCoverageIntervals
    .filter(interval => Number(interval.resolutionSeconds) <= acceptableFor(interval))
    .sort((left, right) => Number(left.startSeconds) - Number(right.startSeconds) || Number(right.endSeconds) - Number(left.endSeconds));
  let cursor = startSeconds;
  for (const interval of intervals) {
    const intervalStart = Number(interval.startSeconds);
    const intervalEnd = Number(interval.endSeconds);
    if (!Number.isFinite(intervalStart) || intervalEnd <= cursor) continue;
    if (intervalStart > cursor) return false;
    if (Number(interval.resolutionSeconds) > acceptableFor(interval)) return false;
    cursor = Math.max(cursor, intervalEnd);
    if (cursor >= endSeconds) return true;
  }
  return false;
}

function jsDebugHistoryCoverageResolutionForRange(startSeconds, endSeconds) {
  const resolutions = [...new Set(jsDebugHistoryReadiness.requestCoverageIntervals.map(interval => Number(interval.resolutionSeconds)))]
    .filter(resolution => Number.isFinite(resolution) && resolution > 0)
    .sort((left, right) => left - right);
  return resolutions.find(resolution => jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, resolution)) ?? Infinity;
}

function jsDebugHistoryCoverageNeedsRefresh(startSeconds, endSeconds, resolutionSeconds, nowMs = Date.now()) {
  if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds) return true;
  if (jsDebugHistoryIntervalsCoverRange(startSeconds, endSeconds, resolutionSeconds, nowMs)) return false;
  const intervals = jsDebugHistoryReadiness.requestCoverageIntervals
    .filter(interval => Number(interval.resolutionSeconds) <= jsDebugHistoryAcceptableResolutionSeconds(startSeconds, resolutionSeconds, interval.sourceResolutionSeconds, nowMs))
    .sort((left, right) => Number(left.startSeconds) - Number(right.startSeconds) || Number(right.endSeconds) - Number(left.endSeconds));
  let cursor = startSeconds;
  for (const interval of intervals) {
    const intervalStart = Number(interval.startSeconds);
    const intervalEnd = Number(interval.endSeconds);
    if (!Number.isFinite(intervalStart) || intervalEnd <= cursor) continue;
    if (intervalStart > cursor) return true;
    cursor = Math.max(cursor, intervalEnd);
  }
  // A continuously covered prefix is enough for an ordinary incremental poll:
  // its sequence cursor supplies the newly elapsed live tail. Older-prefix,
  // interior-gap, and finer-resolution requests still require a full snapshot.
  return cursor <= startSeconds;
}

// DOIT.1 cutover: the browser requests the exact preset resolution (server returns
// exactly that, honest no-data past each tier) instead of coarsening/stitching
// client-side. Now the DEFAULT. Set window.__yolomuxExactStats = false to fall back
// to the legacy coarsen-and-stitch path (a few render tests pin themselves to it to
// keep guarding that fallback).
let jsDebugGraphExactResolutionEnabled = !(typeof globalThis !== 'undefined' && globalThis.__yolomuxExactStats === false);

function debugGraphExactRequestResolutionSeconds() {
  // The concrete resolution to request: the explicit pick, or the range's AUTO
  // (finest offered choice) when the picker is on AUTO. Matches the server matrix.
  const override = Math.max(0, Number(jsDebugGraphResolutionOverrideSeconds) || 0);
  if (override > 0) return override;
  const choices = debugGraphAvailableResolutionChoices();
  return choices.length ? Number(choices[0]) : 1;
}

function setDebugGraphExactResolutionEnabled(value) {
  jsDebugGraphExactResolutionEnabled = value === true;
}

function jsDebugRequestedHistoryResolutionSeconds() {
  // EXACT mode: request the exact preset resolution the chart will render at.
  if (jsDebugGraphExactResolutionEnabled) return debugGraphExactRequestResolutionSeconds();
  // DEFAULT: ask for the finest resolution; the server coarsens to the retained
  // tier and the client stitches/aggregates. A later zoom recovers finer history.
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
    requestCoverageIntervals: [],
    storeCoverageIntervals: {},
    attemptCount: 0,
    error: '',
    generation: Number(jsDebugHistoryReadiness.generation || 0) + 1,
    loadingStartedAtMs: 0,
    nextAutoRetryAtMs: 0,
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
  syncDebugGraphResolutionOverride(Date.now(), {persist: true});
  if (!render) return;
  syncJsDebugStatsDeliveryMode();
  requestJsDebugHistoryForCurrentDomain();
  refreshDebugGraphSurfaces();
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
    ${debugSubTabButtonHtml('system', t('common.theme.system'))}
    ${debugSubTabButtonHtml('logs', t('debug.tab.logs'))}
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

function debugClientLogLevel(event) {
  if (event?.type === 'stats_history' && jsDebugLogLevels.includes(String(event?.level || ''))) return String(event.level);
  if (event?.type === 'error' || event?.type === 'unhandledrejection' || event?.error || Number(event?.status || 0) >= 500) return 'error';
  if (Number(event?.status || 0) >= 400 || event?.ok === false) return 'warning';
  if (event?.type === 'sse') return 'debug';
  return 'info';
}

function recordJsDebugStatsDiagnostic(level, message) {
  recordJsDebugEvent('stats_history', {level, message: `YO!stats: ${message}`});
}

function debugClientLogRecord(event, index = 0) {
  const timestampMs = Date.parse(event?.ts || '');
  return {
    id: `client:${event?.id ?? index}`,
    timestamp: Number.isFinite(timestampMs) ? timestampMs / 1000 : 0,
    level: debugClientLogLevel(event),
    source: 'browser',
    category: String(event?.type || 'client'),
    message: [debugEventDetailText(event), debugEventStatusText(event), debugPhaseTimingText(event)].filter(Boolean).join(' | '),
  };
}

function debugMergedLogRecords() {
  const server = jsDebugLogsState.payload.filter(entry => entry && typeof entry === 'object');
  const client = jsDebugEvents.map(debugClientLogRecord);
  return [...server, ...client]
    .filter(entry => Number(entry.timestamp || 0) * 1000 > jsDebugLogsState.clearedAt)
    .sort((a, b) => (Number(b.timestamp || 0) - Number(a.timestamp || 0)) || String(b.id || '').localeCompare(String(a.id || '')))
    .slice(0, 500);
}

function debugVisibleLogRecords() {
  return debugMergedLogRecords().filter(entry => jsDebugLogsState.levels.has(String(entry.level || 'info')));
}

function debugLogTimeText(timestamp) {
  const date = new Date(Number(timestamp || 0) * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : '';
}

function debugLogsTextForClipboard() {
  return debugVisibleLogRecords().map(entry => [
    debugLogTimeText(entry.timestamp),
    String(entry.level || 'info').toUpperCase().padEnd(7),
    `[${entry.source || 'server'}${entry.category ? `/${entry.category}` : ''}]`,
    entry.message || '',
  ].filter(Boolean).join(' ')).join('\n');
}

function debugLogsInnerHtml() {
  const records = debugVisibleLogRecords();
  return `<div class="js-debug-logs-toolbar">
    <div class="js-debug-log-levels" role="group" aria-label="${esc(t('debug.logs.levels'))}">${jsDebugLogLevels.map(level => {
      const active = jsDebugLogsState.levels.has(level);
      return `<button type="button" class="preferences-inline-action js-debug-log-level js-debug-log-level--${esc(level)}${active ? ' active' : ''}" data-js-debug-log-level="${esc(level)}" aria-pressed="${active ? 'true' : 'false'}">${esc(t(`debug.logs.level.${level}`))}</button>`;
    }).join('')}</div>
    <div class="js-debug-actions">
      <button type="button" class="preferences-inline-action" data-js-debug-logs-copy>${esc(t('common.copy'))}</button>
      <button type="button" class="preferences-inline-action" data-js-debug-logs-clear>${esc(t('common.clear'))}</button>
    </div>
  </div>
  ${jsDebugLogsState.error ? `<div class="js-debug-logs-error" role="status">${esc(t('debug.logs.loadFailed', {error: jsDebugLogsState.error}))}</div>` : ''}
  <div class="js-debug-log-list" data-js-debug-log-list aria-label="${esc(t('debug.logs.recent'))}" aria-busy="${jsDebugLogsState.inFlight ? 'true' : 'false'}">${records.length ? records.map(entry => {
    const level = jsDebugLogLevels.includes(entry.level) ? entry.level : 'info';
    return `<article class="js-debug-log-row js-debug-log-row--${esc(level)}" data-js-debug-log-entry data-level="${esc(level)}">
      <div class="js-debug-log-meta"><time>${esc(debugLogTimeText(entry.timestamp))}</time><span class="js-debug-log-chip">${esc(t(`debug.logs.level.${level}`))}</span><span>${esc(entry.source || 'server')}</span>${entry.category ? `<span>${esc(entry.category)}</span>` : ''}</div>
      <div class="js-debug-log-message">${esc(entry.message || '')}</div>
    </article>`;
  }).join('') : `<div class="js-debug-log-empty">${esc(t('debug.logs.empty'))}</div>`}</div>`;
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
    costSummary: null,
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
    cpuLabel: '',
    systemMemoryLabel: '',
    cpuProcesses: new Map(),
    memoryProcesses: new Map(),
    gpuUtilProcesses: new Map(),
    gpuMemoryProcesses: new Map(),
    gpuDevices: new Map(),
    serviceLoad: new Map(),
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
  return debugGraphBucket(jsDebugGraphBuckets, startMs, durationMs);
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

function debugGraphMergeAgentTokenRates(target, source, multiplier = 1) {
  if (!(source?.agentTokenRates instanceof Map)) return;
  const scale = Math.max(0, Math.min(1, Number(multiplier) || 0));
  if (!scale) return;
  if (!(target.agentTokenRates instanceof Map)) target.agentTokenRates = new Map();
  for (const [key, item] of source.agentTokenRates.entries()) {
    const existing = target.agentTokenRates.get(String(key)) || {label: item?.label || String(key), total: 0, samples: 0, tokens: 0, seconds: 0};
    existing.label = item?.label || existing.label;
    existing.total += Number(item?.total || 0) * scale;
    existing.samples += Number(item?.samples || 0) * scale;
    existing.tokens += Number(item?.tokens || 0) * scale;
    existing.seconds += Number(item?.seconds || 0) * scale;
    existing.billableAvailable = existing.billableAvailable === true || item?.billableAvailable === true;
    if (!existing.billableTokens || typeof existing.billableTokens !== 'object') {
      existing.billableTokens = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    if (!existing.billableSamples || typeof existing.billableSamples !== 'object') {
      existing.billableSamples = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    for (const dimension of ['input', 'cacheRead', 'cacheWrite', 'all']) {
      existing.billableTokens[dimension] += Number(item?.billableTokens?.[dimension] || 0) * scale;
      existing.billableSamples[dimension] += Number(item?.billableSamples?.[dimension] || 0) * scale;
    }
    if (!(existing.modelRates instanceof Map)) existing.modelRates = new Map();
    const sourceModelRates = item?.modelRates instanceof Map
      ? item.modelRates
      : new Map(Array.isArray(item?.modelRates) ? item.modelRates : []);
    for (const [model, sourceRate] of sourceModelRates.entries()) {
      const targetRate = existing.modelRates.get(String(model)) || {total: 0, samples: 0, tokens: 0, seconds: 0};
      targetRate.total += Number(sourceRate?.total || 0) * scale;
      targetRate.samples += Number(sourceRate?.samples || 0) * scale;
      targetRate.tokens += Number(sourceRate?.tokens || 0) * scale;
      targetRate.seconds += Number(sourceRate?.seconds || 0) * scale;
      existing.modelRates.set(String(model), targetRate);
    }
    target.agentTokenRates.set(String(key), existing);
  }
}

function debugGraphMergeCostSummary(target, source, multiplier = 1) {
  if (!source?.costSummary) return;
  const scale = Math.max(0, Math.min(1, Number(multiplier) || 0));
  if (!scale) return;
  const current = target.costSummary || {
    totalMicroUsd: 0, knownMicroUsd: 0, lowerMicroUsd: 0, upperMicroUsd: 0, pricedCount: 0, complete: true, unpricedCount: 0, unpricedTokenQuantity: 0,
    components: [], models: [], sources: [], tmuxWindows: [], catalogRevision: '', activeCatalogRevision: '', freshness: '',
  };
  current.totalMicroUsd += debugGraphCostInteger(source.costSummary.totalMicroUsd) * scale;
  current.knownMicroUsd += debugGraphCostInteger(source.costSummary.knownMicroUsd) * scale;
  current.lowerMicroUsd += debugGraphCostInteger(source.costSummary.lowerMicroUsd ?? source.costSummary.knownMicroUsd) * scale;
  current.upperMicroUsd += debugGraphCostInteger(source.costSummary.upperMicroUsd ?? source.costSummary.totalMicroUsd ?? source.costSummary.knownMicroUsd) * scale;
  current.pricedCount += debugGraphCostInteger(source.costSummary.pricedCount) * scale;
  current.complete = current.complete && source.costSummary.complete === true;
  current.unpricedCount += debugGraphCostInteger(source.costSummary.unpricedCount) * scale;
  current.unpricedTokenQuantity += Math.max(0, Number(source.costSummary.unpricedTokenQuantity) || 0) * scale;
  const scaledRows = value => debugGraphCostRows(value).map(row => {
    if (scale === 1) return row;
    const scaled = {...row};
    for (const key of ['quantity', 'token_quantity', 'micro_usd', 'total_micro_usd', 'cost_micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'output_micro_usd', 'other_micro_usd']) {
      if (Number.isFinite(Number(scaled[key]))) scaled[key] = Number(scaled[key]) * scale;
    }
    return scaled;
  });
  current.components.push(...scaledRows(source.costSummary.components));
  current.models.push(...scaledRows(source.costSummary.models));
  current.sources.push(...scaledRows(source.costSummary.sources));
  current.tmuxWindows.push(...scaledRows(source.costSummary.tmuxWindows));
  current.catalogRevision = source.costSummary.catalogRevision || current.catalogRevision;
  current.activeCatalogRevision = source.costSummary.activeCatalogRevision || current.activeCatalogRevision;
  current.freshness = source.costSummary.freshness || current.freshness;
  target.costSummary = current;
}

function debugGraphMergeBucket(target, source, multiplier = 1) {
  const scale = Math.max(0, Math.min(1, Number(multiplier) || 0));
  if (!scale) return;
  target.apiCount += (source.apiCount || 0) * scale;
  target.sseCount += (source.sseCount || 0) * scale;
  target.latencyTotalMs += (source.latencyTotalMs || 0) * scale;
  target.latencyCount += (source.latencyCount || 0) * scale;
  target.bandwidthBytes += (source.bandwidthBytes || 0) * scale;
  target.disconnectedMs += (source.disconnectedMs || 0) * scale;
  target.cpuTotalPercent += (source.cpuTotalPercent || 0) * scale;
  target.cpuCount += (source.cpuCount || 0) * scale;
  target.systemCpuTotalPercent += (source.systemCpuTotalPercent || 0) * scale;
  target.systemCpuCount += (source.systemCpuCount || 0) * scale;
  target.askAgentTotal += (source.askAgentTotal || 0) * scale;
  target.runAgentTotal += (source.runAgentTotal || 0) * scale;
  target.transitionAgentTotal += (source.transitionAgentTotal || 0) * scale;
  target.idleAgentTotal += (source.idleAgentTotal || 0) * scale;
  target.activeAgentTotal += (source.activeAgentTotal || 0) * scale;
  target.inactiveAgentTotal += (source.inactiveAgentTotal || 0) * scale;
  target.agentActivitySamples += (source.agentActivitySamples || 0) * scale;
  target.agentStatusSequence = Math.max(Number(target.agentStatusSequence ?? -1), Number(source.agentStatusSequence ?? -1));
  target.tokensPerAgentTotal += (source.tokensPerAgentTotal || 0) * scale;
  target.agentTokenSamples += (source.agentTokenSamples || 0) * scale;
  debugGraphMergeAgentTokenRates(target, source, scale);
  debugGraphMergeCostSummary(target, source, scale);
  const sourceHost = source.hostMetrics;
  if (sourceHost) {
    const targetHost = target.hostMetrics || (target.hostMetrics = debugGraphNewHostMetrics());
    targetHost.systemMemoryUsedTotalBytes += Number(sourceHost.systemMemoryUsedTotalBytes || 0) * scale;
    targetHost.systemMemoryCapacityTotalBytes += Number(sourceHost.systemMemoryCapacityTotalBytes || 0) * scale;
    targetHost.systemMemoryCount += Number(sourceHost.systemMemoryCount || 0) * scale;
    if (sourceHost.cpuLabel) targetHost.cpuLabel = String(sourceHost.cpuLabel);
    if (sourceHost.systemMemoryLabel) targetHost.systemMemoryLabel = String(sourceHost.systemMemoryLabel);
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
        item[valueKey] += Number(sourceItem[valueKey] || 0) * scale;
        item.samples += Number(sourceItem.samples || 0) * scale;
        targetMap.set(key, item);
      }
    }
    if (sourceHost.gpuDevices instanceof Map) {
      for (const [key, sourceItem] of sourceHost.gpuDevices.entries()) {
        const item = targetHost.gpuDevices.get(key) || {label: sourceItem.label || key, utilTotalPercent: 0, memoryUsedTotalBytes: 0, memoryCapacityTotalBytes: 0, samples: 0};
        item.label = sourceItem.label || item.label;
        item.utilTotalPercent += Number(sourceItem.utilTotalPercent || 0) * scale;
        item.memoryUsedTotalBytes += Number(sourceItem.memoryUsedTotalBytes || 0) * scale;
        item.memoryCapacityTotalBytes += Number(sourceItem.memoryCapacityTotalBytes || 0) * scale;
        item.samples += Number(sourceItem.samples || 0) * scale;
        targetHost.gpuDevices.set(key, item);
      }
    }
    if (sourceHost.serviceLoad instanceof Map) {
      for (const [key, sourceItem] of sourceHost.serviceLoad.entries()) {
        const item = targetHost.serviceLoad.get(key) || {label: sourceItem.label || key, cpuTotalPercent: 0, cpuSamples: 0, cpuMinPercent: 0, cpuMaxPercent: 0, rssTotalBytes: 0, rssSamples: 0, rssMinBytes: 0, rssMaxBytes: 0};
        item.label = sourceItem.label || item.label;
        for (const prefix of ['cpu', 'rss']) {
          const totalKey = `${prefix}Total${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const samplesKey = `${prefix}Samples`;
          const minKey = `${prefix}Min${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const maxKey = `${prefix}Max${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const sourceSamples = Number(sourceItem[samplesKey] || 0) * scale;
          if (sourceSamples <= 0) continue;
          const previousSamples = Number(item[samplesKey] || 0);
          item[totalKey] += Number(sourceItem[totalKey] || 0) * scale;
          item[samplesKey] += sourceSamples;
          item[minKey] = previousSamples > 0 ? Math.min(item[minKey], Number(sourceItem[minKey] || 0)) : Number(sourceItem[minKey] || 0);
          item[maxKey] = Math.max(item[maxKey], Number(sourceItem[maxKey] || 0));
        }
        targetHost.serviceLoad.set(key, item);
      }
    }
  }
  if (source.clients instanceof Map) {
    if (!(target.clients instanceof Map)) target.clients = new Map();
    for (const [clientId, sourceClient] of source.clients.entries()) {
      const targetClient = target.clients.get(clientId) || debugGraphNewClientBucket();
      targetClient.apiCount += Number(sourceClient.apiCount || 0) * scale;
      targetClient.sseCount += Number(sourceClient.sseCount || 0) * scale;
      targetClient.latencyTotalMs += Number(sourceClient.latencyTotalMs || 0) * scale;
      targetClient.latencyCount += Number(sourceClient.latencyCount || 0) * scale;
      targetClient.bandwidthBytes += Number(sourceClient.bandwidthBytes || 0) * scale;
      targetClient.disconnectedMs += Number(sourceClient.disconnectedMs || 0) * scale;
      target.clients.set(clientId, targetClient);
    }
  }
  if (source.servers instanceof Map) {
    if (!(target.servers instanceof Map)) target.servers = new Map();
    for (const [processId, sourceProcess] of source.servers.entries()) {
      const targetProcess = target.servers.get(processId) || {label: processId, cpuTotalPercent: 0, cpuCount: 0};
      targetProcess.label = sourceProcess.label || targetProcess.label;
      targetProcess.cpuTotalPercent += Number(sourceProcess.cpuTotalPercent || 0) * scale;
      targetProcess.cpuCount += Number(sourceProcess.cpuCount || 0) * scale;
      target.servers.set(processId, targetProcess);
    }
  }
}

function compactJsDebugGraphBuckets(nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  for (const [key, bucket] of [...jsDebugGraphBuckets.entries()]) {
    if (bucket.startMs < retentionCutoff) {
      jsDebugGraphBuckets.delete(key);
      continue;
    }
    const targetDurationMs = debugGraphBucketDurationForTime(bucket.startMs, nowMs);
    if (bucket.durationMs >= targetDurationMs) continue;
    const targetStartMs = Math.floor(bucket.startMs / targetDurationMs) * targetDurationMs;
    const target = debugGraphBucket(jsDebugGraphBuckets, targetStartMs, targetDurationMs);
    debugGraphMergeBucket(target, bucket);
    jsDebugGraphBuckets.delete(key);
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
  if (serverChanged) {
    recordJsDebugStatsDiagnostic('warning', `owner changed from PID ${jsDebugStatsServerPid || 'unknown'} to PID ${nextPid || 'unknown'}; refreshing durable history`);
    clearJsDebugGraphData();
  }
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
  if (sampleApplied) jsDebugStatsPollState.lastSampleAtMs = Date.now();
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

// `stats_sample` arrives on the shared client-events EventSource.  Its record
// is the durable one-second owner delta, so the visible graph advances without
// waiting for the 30-second history-backfill poll.  Polling remains the
// range/zoom and reconnect fallback, not the live-tail transport.
function applyJsDebugStatsSamplePush(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const sample = payload.sample && typeof payload.sample === 'object' ? payload.sample : {};
  const record = payload.record && typeof payload.record === 'object' ? payload.record : null;
  if (!record) return false;
  const sequence = Number(payload.sequence);
  const cursor = Number.isFinite(sequence) ? sequence : Number(record.sequence || 0);
  recordJsDebugStatsSample({
    ...sample,
    history: {sequence: cursor, latest_sequence: cursor, records: [record]},
  }, {advanceHistoryCursor: true});
  return true;
}

function clearJsDebugGraphData() {
  jsDebugGraphBuckets.clear();
  jsDebugGraphEventRecords.clear();
  jsDebugGraphPendingServerBuckets.clear();
  // Invalidate any in-flight silent prefetch so its late response cannot repopulate
  // the cache we just cleared (kept the reload-idempotency of the rendered history).
  jsDebugHistoryPrefetchState.generation += 1;
}

function debugGraphBucketForServerRecord(record) {
  if (!record || typeof record !== 'object') return null;
  const startSeconds = Number(record.start);
  const durationSeconds = Number(record.duration);
  if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return null;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, durationSeconds * 1000);
  const startMs = Math.floor(startSeconds * 1000);
  return debugGraphBucket(jsDebugGraphBuckets, startMs, durationMs);
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
  debugGraphApplyServerCostSummary(bucket, record.cost_summary);
}

// Cost projection stays attached to the existing stats bucket. The pricing owner supplies
// integer micro-USD amounts, so this view never introduces a float-based cost cache or a
// second time-range selection path.
function debugGraphCostInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

function debugGraphCostRows(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
}

function debugGraphApplyServerCostSummary(bucket, source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  bucket.costSummary = {
    totalMicroUsd: debugGraphCostInteger(source.total_micro_usd),
    knownMicroUsd: debugGraphCostInteger(source.known_micro_usd),
    lowerMicroUsd: debugGraphCostInteger(source.lower_micro_usd ?? source.known_micro_usd),
    upperMicroUsd: debugGraphCostInteger(source.upper_micro_usd ?? source.total_micro_usd ?? source.known_micro_usd),
    pricedCount: debugGraphCostInteger(source.priced_count),
    complete: source.complete === true,
    unpricedCount: debugGraphCostInteger(source.unpriced_count),
    unpricedTokenQuantity: Math.max(0, Number(source.unpriced_token_quantity) || 0),
    components: debugGraphCostRows(source.components),
    models: debugGraphCostRows(source.models),
    sources: debugGraphCostRows(source.sources),
    tmuxWindows: debugGraphCostRows(source.tmux_windows),
    catalogRevision: String(source.catalog_revision || '').slice(0, 160),
    activeCatalogRevision: String(source.active_catalog_revision || '').slice(0, 160),
    freshness: String(source.freshness || '').slice(0, 80),
  };
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
  if (source.cpu_label) target.cpuLabel = String(source.cpu_label);
  if (source.system_memory_label) target.systemMemoryLabel = String(source.system_memory_label);
  debugGraphApplyHostMetricProcesses(target.cpuProcesses, source.cpu_processes);
  debugGraphApplyHostMetricProcesses(target.memoryProcesses, source.memory_processes, 'totalBytes');
  debugGraphApplyHostMetricProcesses(target.gpuUtilProcesses, source.gpu_util_processes);
  debugGraphApplyHostMetricProcesses(target.gpuMemoryProcesses, source.gpu_memory_processes, 'totalBytes');
  if (source.service_load && typeof source.service_load === 'object' && !Array.isArray(source.service_load)) {
    for (const [key, record] of Object.entries(source.service_load)) {
      if (!record || typeof record !== 'object') continue;
      const item = target.serviceLoad.get(key) || {label: String(record.label || key), cpuTotalPercent: 0, cpuSamples: 0, cpuMinPercent: 0, cpuMaxPercent: 0, rssTotalBytes: 0, rssSamples: 0, rssMinBytes: 0, rssMaxBytes: 0};
      item.label = String(record.label || item.label || key);
      for (const prefix of ['cpu', 'rss']) {
        const unit = prefix === 'cpu' ? 'Percent' : 'Bytes';
        const sourceUnit = prefix === 'cpu' ? 'percent' : 'bytes';
        const samplesKey = `${prefix}Samples`;
        const sourceSamples = Math.max(0, Number(record[`${prefix}_samples`] || 0));
        if (sourceSamples < Number(item[samplesKey] || 0)) continue;
        item[`${prefix}Total${unit}`] = Math.max(0, Number(record[`${prefix}_total_${sourceUnit}`] || 0));
        item[samplesKey] = sourceSamples;
        item[`${prefix}Min${unit}`] = Math.max(0, Number(record[`${prefix}_min_${sourceUnit}`] || 0));
        item[`${prefix}Max${unit}`] = Math.max(0, Number(record[`${prefix}_max_${sourceUnit}`] || 0));
      }
      target.serviceLoad.set(key, item);
    }
  }
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
    const existing = bucket.agentTokenRates.get(key) || {label, total: 0, samples: 0, tokens: 0, seconds: 0, modelRates: new Map()};
    existing.label = label;
    if (Number.isFinite(total)) existing.total = Math.max(Number(existing.total || 0), Math.max(0, total));
    if (Number.isFinite(samples)) existing.samples = Math.max(Number(existing.samples || 0), Math.max(0, samples));
    if (Number.isFinite(tokens)) existing.tokens = Math.max(Number(existing.tokens || 0), Math.max(0, tokens));
    if (Number.isFinite(seconds)) existing.seconds = Math.max(Number(existing.seconds || 0), Math.max(0, seconds));
    const billable = item.billable_tokens && typeof item.billable_tokens === 'object' ? item.billable_tokens : {};
    const billableSamples = item.billable_samples && typeof item.billable_samples === 'object' ? item.billable_samples : {};
    existing.billableAvailable = existing.billableAvailable === true || item.billable_available === true;
    if (!existing.billableTokens || typeof existing.billableTokens !== 'object') {
      existing.billableTokens = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    if (!existing.billableSamples || typeof existing.billableSamples !== 'object') {
      existing.billableSamples = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    existing.billableTokens.input = Math.max(Number(existing.billableTokens.input || 0), Math.max(0, Number(billable.input) || 0));
    existing.billableTokens.cacheRead = Math.max(Number(existing.billableTokens.cacheRead || 0), Math.max(0, Number(billable.cache_read) || 0));
    existing.billableTokens.cacheWrite = Math.max(Number(existing.billableTokens.cacheWrite || 0), Math.max(0, Number(billable.cache_write) || 0));
    existing.billableTokens.all = Math.max(Number(existing.billableTokens.all || 0), Math.max(0, Number(billable.all) || 0));
    existing.billableSamples.input = Math.max(Number(existing.billableSamples.input || 0), Math.max(0, Number(billableSamples.input) || 0));
    existing.billableSamples.cacheRead = Math.max(Number(existing.billableSamples.cacheRead || 0), Math.max(0, Number(billableSamples.cache_read) || 0));
    existing.billableSamples.cacheWrite = Math.max(Number(existing.billableSamples.cacheWrite || 0), Math.max(0, Number(billableSamples.cache_write) || 0));
    existing.billableSamples.all = Math.max(Number(existing.billableSamples.all || 0), Math.max(0, Number(billableSamples.all) || 0));
    if (!(existing.modelRates instanceof Map)) existing.modelRates = new Map();
    const modelRates = item.model_rates && typeof item.model_rates === 'object' && !Array.isArray(item.model_rates)
      ? Object.entries(item.model_rates)
      : [];
    for (const [rawModel, rawRate] of modelRates) {
      if (!rawRate || typeof rawRate !== 'object') continue;
      const model = String(rawModel || 'unknown').trim() || 'unknown';
      const current = existing.modelRates.get(model) || {total: 0, samples: 0, tokens: 0, seconds: 0};
      const modelTotal = Number(rawRate.total ?? rawRate.rate ?? rawRate.value);
      const modelSamples = Number(rawRate.samples || 0);
      const modelTokens = Number(rawRate.tokens || 0);
      const modelSeconds = Number(rawRate.seconds || 0);
      if (Number.isFinite(modelTotal)) current.total = Math.max(Number(current.total || 0), Math.max(0, modelTotal));
      if (Number.isFinite(modelSamples)) current.samples = Math.max(Number(current.samples || 0), Math.max(0, modelSamples));
      if (Number.isFinite(modelTokens)) current.tokens = Math.max(Number(current.tokens || 0), Math.max(0, modelTokens));
      if (Number.isFinite(modelSeconds)) current.seconds = Math.max(Number(current.seconds || 0), Math.max(0, modelSeconds));
      existing.modelRates.set(model, current);
    }
    bucket.agentTokenRates.set(key, existing);
}
}

function debugGraphRemoveCoarserServerBuckets(startSeconds, endSeconds, resolutionSeconds) {
  const startMs = Number(startSeconds) * 1000;
  const endMs = Number(endSeconds) * 1000;
  const resolutionMs = Number(resolutionSeconds) * 1000;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !Number.isFinite(resolutionMs) || resolutionMs <= 0) return 0;
  let removed = 0;
  for (const [key, bucket] of jsDebugGraphBuckets.entries()) {
    const bucketStart = Number(bucket?.startMs);
    const bucketDuration = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    const bucketEnd = bucketStart + bucketDuration;
    // Remove EVERY coarser bucket that intersects the authoritative finer interval, not
    // only those fully contained. A coarse boundary bucket that merely straddles the
    // interval edge otherwise survives, claims an aggregate prefix around a real no-data
    // gap, and forces the whole view back to its coarse duration. Its portion outside the
    // domain is re-provided by the shared wide-range/prefetch cache; a straddling partial
    // aggregate must never be retained inside a finer-covered domain.
    if (bucketDuration <= resolutionMs || bucketEnd <= startMs || bucketStart >= endMs) continue;
    jsDebugGraphBuckets.delete(key);
    removed += 1;
  }
  return removed;
}

function debugGraphApplyServerHistory(history = {}, {advanceLiveCursor = true, replaceCoverage = null} = {}) {
  if (!history || typeof history !== 'object') return;
  if (replaceCoverage) {
    const replacements = Array.isArray(replaceCoverage) ? replaceCoverage : [replaceCoverage];
    for (const interval of replacements) {
      debugGraphRemoveCoarserServerBuckets(
        interval.start ?? interval.start_seconds ?? interval.covered_start,
        interval.end ?? interval.end_seconds ?? interval.covered_end,
        interval.resolution_seconds ?? interval.resolution,
      );
    }
  }
  // Compact local fine buckets before applying an authoritative server coarse bucket. Applying
  // first would merge the same measurements a second time at the 1h/2h tier boundaries.
  compactJsDebugGraphBuckets();
  const sequence = Number(history.latest_sequence ?? history.sequence);
  if (advanceLiveCursor && Number.isFinite(sequence)) jsDebugStatsServerSequence = Math.max(0, sequence);
  const backfill = history.usage_atom_backfill;
  if (backfill && typeof backfill === 'object' && !Array.isArray(backfill)) {
    const state = String(backfill.state || '').toLowerCase();
    jsDebugUsageAtomBackfill.state = ['pending', 'running', 'partial', 'complete'].includes(state) ? state : 'pending';
    jsDebugUsageAtomBackfill.sources = Math.max(0, Number(backfill.sources) || 0);
    jsDebugUsageAtomBackfill.missing = Math.max(0, Number(backfill.missing) || 0);
  }
  const records = Array.isArray(history.records) ? history.records : [];
  records.forEach(debugGraphApplyServerRecord);
  compactJsDebugGraphBuckets();
}

// The compact token side-stream is gone (ONE history stream since 2026-07):
// token detail rides every history record and lands in the same unified bucket
// cache. This per-range value survives ONLY as the token charts' display
// floor, so wide-range token bars keep their pre-unification widths
// (>=120s at 4h+, >=300s at 16h+); it is not a second fetch resolution.
function debugGraphAgentTokenResolution(nowMs = Date.now()) {
  const rangeSeconds = debugGraphDomain(nowMs).rangeSeconds;
  if (rangeSeconds < 4 * 60 * 60) return 0;
  return rangeSeconds >= 16 * 60 * 60 ? 5 * 60 : 2 * 60;
}

function debugGraphAggregateBucket(map, source, scaleMs, multiplier = 1) {
  const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(scaleMs) || jsDebugGraphRawBucketMs);
  const startMs = Math.floor(source.startMs / durationMs) * durationMs;
  const bucket = debugGraphBucket(map, startMs, durationMs);
  debugGraphMergeBucket(bucket, source, multiplier);
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

function debugGraphMinimumDisplayResolutionMs(domain, nowMs = Date.now()) {
  const domainStartMs = Number(domain?.startMs);
  return Math.max(
    Number.isFinite(domainStartMs) ? debugGraphBucketDurationForTime(domainStartMs, nowMs) : jsDebugGraphRawBucketMs,
    ...debugGraphContributingSourceSlices(domain).map(slice => slice.sourceDurationMs),
  );
}

function debugGraphAvailableResolutionChoices(domain = debugGraphDomain(), nowMs = Date.now()) {
  const rangeSeconds = Math.max(1, Number(domain?.rangeSeconds) || 0);
  const domainStartMs = Number(domain?.startMs);
  const retainedSeconds = Number.isFinite(domainStartMs)
    ? debugGraphBucketDurationForTime(domainStartMs, nowMs) / 1000
    : jsDebugGraphRawBucketMs / 1000;
  // A 30-minute-or-longer chart should not offer sub-10-second overrides:
  // they create hundreds or thousands of mostly empty cells and resurrected
  // the misleading coarse-boundary menu. Short live ranges retain 1/2/5s.
  const friendlyMinimumSeconds = rangeSeconds >= 30 * 60 ? 10 : 1;
  return jsDebugGraphResolutionChoices.filter(value => value >= Math.max(retainedSeconds, friendlyMinimumSeconds) && value * 10 <= rangeSeconds);
}

function normalizedDebugGraphResolutionOverrideSeconds(value, domain = debugGraphDomain(), nowMs = Date.now()) {
  const requested = Math.max(0, Number(value) || 0);
  if (requested === 0) return 0;
  const choices = debugGraphAvailableResolutionChoices(domain, nowMs);
  if (!choices.length) return 0;
  if (choices.includes(requested)) return requested;
  // Normalize an out-of-set persisted/deeplinked value (e.g. a legacy 30/120/600s
  // override) or a now-invalid prior-range value by rounding UP to the nearest coarser
  // valid choice — never render finer than the request implied. If nothing coarser is
  // available for this range (the request was coarser than the whole menu), fall back to
  // the coarsest offered choice.
  return choices.find(candidate => candidate >= requested) ?? choices[choices.length - 1];
}

function syncDebugGraphResolutionOverride(nowMs = Date.now(), {persist = false, domain = debugGraphDomain(nowMs)} = {}) {
  const normalized = normalizedDebugGraphResolutionOverrideSeconds(jsDebugGraphResolutionOverrideSeconds, domain, nowMs);
  if (normalized === jsDebugGraphResolutionOverrideSeconds) return false;
  jsDebugGraphResolutionOverrideSeconds = normalized;
  if (persist) saveJsDebugStatsUiPreferences();
  return true;
}

function debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds = 0, nowMs = Date.now()) {
  // EXACT mode: render at exactly the requested preset resolution (the server
  // already returned uniform buckets at it), so the client never re-coarsens the
  // exact data down to the 120-point display cap.
  if (jsDebugGraphExactResolutionEnabled && !debugGraphZoomDomainValid()) {
    return debugGraphExactRequestResolutionSeconds() * 1000;
  }
  const domainStartMs = Number(domain?.startMs);
  const domainEndMs = Number(domain?.endMs);
  const domainSpanMs = Number.isFinite(domainStartMs) && Number.isFinite(domainEndMs)
    ? Math.max(jsDebugGraphRawBucketMs, domainEndMs - domainStartMs)
    : jsDebugGraphDefaultRangeSeconds * 1000;
  const targetMs = domainSpanMs / jsDebugGraphMaxDisplayPoints;
  const displayMs = jsDebugGraphDisplayBucketMs.find(bucketMs => bucketMs >= targetMs)
    || jsDebugGraphDisplayBucketMs.at(-1);
  // A display set has one bar width. Its scale must therefore accommodate the
  // coarsest retained source in the whole domain, not merely the tier at its
  // left edge. This also covers server history overlapping the live raw tail.
  // The retained-tier minimum (`debugGraphMinimumDisplayResolutionMs`) already
  // coarsens to the server's authoritative resolution for the domain from the
  // ACTUALLY-LOADED source buckets. Do NOT additionally clamp to
  // `jsDebugHistoryCoverageResolutionForRange`: that scans the last request's
  // coverage intervals, which can be STALE from a wider range (e.g. a 24h fetch
  // whose old tail is 600s), and would wrongly coarsen a 10s pick at 1h to 600s
  // when the 1h fetch is rejected/pending — the reported "10s does nothing / shows
  // 600s" regression. One resolution per view still holds via the retained tier.
  const retainedMs = debugGraphMinimumDisplayResolutionMs(domain, nowMs);
  const minimumMs = Math.max(0, Number(minimumResolutionSeconds) || 0) * 1000;
  const overrideMs = normalizedDebugGraphResolutionOverrideSeconds(jsDebugGraphResolutionOverrideSeconds, domain, nowMs) * 1000;
  if (overrideMs > 0) {
    let effectiveMs = Math.max(jsDebugGraphRawBucketMs, retainedMs, minimumMs, overrideMs);
    // Point-cap: an explicit override that would render more than the budget of buckets
    // for this domain is clamped UP to the finest universe choice that stays within the
    // cap. The label reads back this effective (coarser) value so the render never blows
    // past the point budget even when the picker offers a finer value.
    const budgetMs = domainSpanMs / jsDebugGraphOverridePointCap;
    if (effectiveMs < budgetMs) {
      const cappedMs = jsDebugGraphResolutionChoices
        .map(seconds => seconds * 1000)
        .find(candidateMs => candidateMs >= budgetMs) ?? jsDebugGraphResolutionChoices[jsDebugGraphResolutionChoices.length - 1] * 1000;
      effectiveMs = Math.max(effectiveMs, cappedMs);
    }
    return effectiveMs;
  }
  return Math.max(jsDebugGraphRawBucketMs, displayMs, retainedMs, minimumMs);
}

function debugGraphSourceBuckets(domain) {
  return [...jsDebugGraphBuckets.values()]
    .filter(bucket => debugGraphBucketInRange(bucket, domain.startMs, domain.endMs))
    .sort((left, right) => left.startMs - right.startMs);
}

function debugGraphAddCoveredInterval(intervals, startMs, endMs) {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return;
  let index = 0;
  while (index < intervals.length && intervals[index].endMs < startMs) index += 1;
  let mergedStart = startMs;
  let mergedEnd = endMs;
  while (index < intervals.length && intervals[index].startMs <= mergedEnd) {
    mergedStart = Math.min(mergedStart, intervals[index].startMs);
    mergedEnd = Math.max(mergedEnd, intervals[index].endMs);
    intervals.splice(index, 1);
  }
  intervals.splice(index, 0, {startMs: mergedStart, endMs: mergedEnd});
}

function debugGraphUncoveredIntervals(intervals, startMs, endMs) {
  const uncovered = [];
  let cursor = startMs;
  for (const interval of intervals) {
    if (interval.endMs <= cursor) continue;
    if (interval.startMs >= endMs) break;
    if (interval.startMs > cursor) uncovered.push({startMs: cursor, endMs: Math.min(endMs, interval.startMs)});
    cursor = Math.max(cursor, interval.endMs);
    if (cursor >= endMs) break;
  }
  if (cursor < endMs) uncovered.push({startMs: cursor, endMs});
  return uncovered;
}

function debugGraphContributingSourceSlices(domain) {
  const domainStartMs = Number(domain?.startMs);
  const domainEndMs = Number(domain?.endMs);
  if (!Number.isFinite(domainStartMs) || !Number.isFinite(domainEndMs) || domainEndMs <= domainStartMs) return [];
  const coveredIntervals = [];
  const slices = [];
  const sources = debugGraphSourceBuckets(domain).sort((left, right) => (
    (Number(left.durationMs) - Number(right.durationMs))
    || (Number(left.startMs) - Number(right.startMs))
  ));
  for (const bucket of sources) {
    const sourceStartMs = Number(bucket.startMs);
    const sourceDurationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
    const visibleStartMs = Math.max(domainStartMs, sourceStartMs);
    // Keep the current/right-edge bucket whole: its sample is attached to the
    // bucket start even while the interval is still in progress. Only the old
    // left edge has an out-of-view prefix that can contaminate a narrower range.
    const visibleEndMs = sourceStartMs + sourceDurationMs;
    if (visibleEndMs <= visibleStartMs) continue;
    for (const interval of debugGraphUncoveredIntervals(coveredIntervals, visibleStartMs, visibleEndMs)) {
      slices.push({
        bucket,
        startMs: interval.startMs,
        endMs: interval.endMs,
        sourceDurationMs,
        multiplier: (interval.endMs - interval.startMs) / sourceDurationMs,
      });
    }
    // Coverage is clipped to the selected domain. A coarse bucket retained for a wider
    // range must not claim an out-of-view prefix and poison a fully fine-covered view.
    debugGraphAddCoveredInterval(coveredIntervals, visibleStartMs, visibleEndMs);
  }
  return slices;
}

function debugGraphDisplayBuckets(nowMs = Date.now(), {minimumResolutionSeconds = 0, rangeSeconds = jsDebugGraphRangeSeconds} = {}) {
  compactJsDebugGraphBuckets(nowMs);
  const domain = debugGraphDomain(nowMs, rangeSeconds);
  const scaleMs = debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds, nowMs);
  const buckets = new Map();
  for (const slice of debugGraphContributingSourceSlices(domain)) {
    // Once a finer source has claimed an instant, a coarser history response may
    // only fill the remaining visible interval. Place that proportional slice in
    // its visible cell while retaining the complete source bucket for wider views.
    debugGraphAggregateBucket(buckets, {...slice.bucket, startMs: slice.startMs}, scaleMs, slice.multiplier);
  }
  return [...buckets.values()].sort((a, b) => a.startMs - b.startMs);
}

// Token/model charts read the SAME unified bucket cache as every other chart.
// The only token-specific behavior left is the display floor: at least the
// token sampling cadence (60s), coarsened per range by
// debugGraphAgentTokenResolution so wide-range bars keep their legacy widths.
function debugGraphAgentTokenDisplayBuckets(nowMs = Date.now()) {
  const floorSeconds = Math.max(jsDebugGraphAgentTokenBucketSeconds, debugGraphAgentTokenResolution(nowMs));
  return debugGraphDisplayBuckets(nowMs, {minimumResolutionSeconds: floorSeconds, rangeSeconds: jsDebugGraphRangeSeconds});
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

function debugGraphAgentTokenBucketDimensionValue(bucket, item, dimension = jsDebugGraphModelTokenDimension) {
  if (dimension === 'output') return debugGraphAgentTokenBucketValue(bucket, item);
  const quantity = Math.max(0, Number(item?.billableTokens?.[dimension]) || 0);
  return quantity / Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
}

function debugGraphAgentTokenDisplayedSum(buckets) {
  let total = 0;
  for (const bucket of buckets || []) {
    if (!(bucket?.agentTokenRates instanceof Map)) continue;
    for (const item of bucket.agentTokenRates.values()) {
      if (jsDebugGraphModelTokenDimension !== 'output') {
        if (item?.billableAvailable === true) total += Math.max(0, Number(item?.billableTokens?.[jsDebugGraphModelTokenDimension]) || 0);
        continue;
      }
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

function debugGraphModelTokenDisplayedSum(buckets) {
  let total = 0;
  for (const bucket of buckets || []) {
    const components = debugGraphModelTokenComponentRecords(bucket);
    if (components.length) {
      total += components.reduce((sum, component) => sum + Math.max(0, Number(component?.quantity) || 0), 0);
      continue;
    }
    if (jsDebugGraphModelTokenDimension !== 'output') continue;
    if (!(bucket?.agentTokenRates instanceof Map)) continue;
    for (const item of bucket.agentTokenRates.values()) {
      if (!(item?.modelRates instanceof Map)) continue;
      for (const rate of item.modelRates.values()) total += Math.max(0, Number(rate?.tokens) || 0);
    }
  }
  return total;
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
    descKey: spec.descKey,
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

// True when any cached bucket (any range) carries a GPU device sample. Distinguishes a
// host with no GPU telemetry at all from a window that merely lacks GPU samples, so the
// unavailable state can explain itself precisely. Only consulted when a visible GPU
// chart has no series for the current window (not on the hot per-bucket render path).
function debugGraphAnyGpuDeviceSamplesCached() {
  for (const bucket of jsDebugGraphBuckets.values()) {
    for (const item of bucket?.hostMetrics?.gpuDevices?.values?.() || []) {
      if (Number(item?.samples || 0) > 0) return true;
    }
  }
  return false;
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
  // Percent charts without a fixed 0-100 axis (e.g. Servers Load, where a single
  // multi-core service can exceed 100%) still need round tick steps. A 1/2/5
  // ceil keeps the max and its half-step both round (100->50, 50->25, 20->10)
  // instead of the raw data max (the 88.3% / 44.1% ticks in the report).
  if (unit === 'percent') return Math.max(1, debugGraphNiceCeil(value));
  return value;
}

function debugGraphTokenNumberText(value) {
  const number = Math.max(0, Number(value) || 0);
  if (number >= 1000 * 1000) {
    const millions = number / 1000 / 1000;
    return `${millions.toFixed(Number.isInteger(millions) || number >= 100 * 1000 * 1000 ? 0 : 1)}M`;
  }
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

function debugGraphMetaItem(labelKey, params = {}) {
  return {text: t(labelKey, params), labelKey, descKey: jsDebugGraphDescriptionKeyByLabelKey[labelKey]};
}

function debugGraphPlainMetaItem(text, descKey = '') {
  return {text: String(text || ''), descKey};
}

function debugRemovalLatencyMetaItem() {
  if (typeof terminalRemovalLatencySummary !== 'function') return '';
  const summary = terminalRemovalLatencySummary();
  if (!summary?.count) return '';
  return debugGraphMetaItem('debug.graph.meta.removal', {
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
  if (Number.isFinite(jsDebugStatsServerUptimeSeconds)) items.push(debugGraphMetaItem('debug.graph.meta.uptime', {uptime: debugGraphUptimeText(jsDebugStatsServerUptimeSeconds)}));
  if (Number.isFinite(jsDebugStatsServerPid)) items.push(debugGraphPlainMetaItem(`PID=${Math.floor(jsDebugStatsServerPid)}`));
  const rss = debugGraphBytesText(jsDebugStatsServerRssBytes);
  if (rss) items.push(debugGraphMetaItem('debug.graph.meta.rss', {rss}));
  if (Number.isFinite(jsDebugStatsServerSequence) && jsDebugStatsServerSequence > 0) items.push(debugGraphMetaItem('debug.graph.meta.serverSequence', {sequence: Math.floor(jsDebugStatsServerSequence)}));
  const removalLatency = debugRemovalLatencyMetaItem();
  if (removalLatency) items.push(removalLatency);
  if (items.length) {
    const counts = debugEventCounts();
    const uploadedMb = debugGraphTotalMegabytesText(counts.apiRequestBytes);
    const downloadedMb = debugGraphTotalMegabytesText(counts.apiResponseBytes + counts.sseBytes);
    items.push(debugGraphMetaItem('debug.graph.meta.totalTraffic', {uploaded: uploadedMb, downloaded: downloadedMb}));
  }
  return items;
}

function debugGraphWaitingForServerStats() {
  return debugGraphMetaItems().length === 0;
}

function debugGraphMetaHtml() {
  const items = debugGraphMetaItems();
  const initialHistoryOverlayOwnsLoading = jsDebugHistoryReadinessStateName() === 'loading-initial'
    && jsDebugHistoryReadiness.overlayVisible === true;
  const metaHtml = items.length
    ? items.map(item => `<span class="js-debug-graph-meta-item"${debugGraphExplainAttrs(item.text, item.descKey, {attribute: 'data-js-debug-meta-desc'})}>${esc(item.text)}</span>`).join('<span aria-hidden="true"> | </span>')
    : (initialHistoryOverlayOwnsLoading ? '' : textWithMovingEllipsisHtml(t('debug.waitingForServerStats')));
  return `<div class="js-debug-graph-meta" data-js-debug-uptime="${esc(Number.isFinite(jsDebugStatsServerUptimeSeconds) ? debugGraphUptimeText(jsDebugStatsServerUptimeSeconds) : '')}">${metaHtml}</div>`;
}

function debugGraphHistoryOverlayText(state = jsDebugHistoryReadiness) {
  const range = jsDebugGraphRangeLabel(state.requestedRangeSeconds);
  const stateName = jsDebugHistoryReadinessStateName(state);
  if (stateName === 'loading-initial') return t('debug.graph.history.loadingInitial');
  if (stateName === 'loading-older') return t('debug.graph.history.loadingOlder', {range});
  if (stateName === 'retrying') return state.error || t('debug.graph.history.retrying', {range});
  if (stateName === 'error') return t('debug.graph.history.error', {range, error: state.error || t('common.unknown')});
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

function debugGraphTokenSeriesDefs(buckets, dimension = 'agent') {
  const selectedAgentDimension = dimension === 'agent' ? jsDebugGraphModelTokenDimension : 'output';
  const tokenItems = new Map();
  for (const bucket of buckets) {
    if (!(bucket.agentTokenRates instanceof Map)) continue;
    for (const [key, item] of bucket.agentTokenRates.entries()) {
      if (dimension === 'agent') {
        const existing = tokenItems.get(String(key)) || {label: item?.label || String(key), samples: 0};
        existing.label = item?.label || existing.label;
        existing.samples += selectedAgentDimension === 'output'
          ? Number(item?.samples || 0)
          : (item?.billableAvailable === true ? 1 : 0);
        tokenItems.set(String(key), existing);
        continue;
      }
      if (!(item?.modelRates instanceof Map)) continue;
      for (const [model, rate] of item.modelRates.entries()) {
        const modelKey = String(model || 'unknown').trim() || 'unknown';
        const existing = tokenItems.get(modelKey) || {label: modelKey, samples: 0};
        existing.samples += Number(rate?.samples || 0);
        tokenItems.set(modelKey, existing);
      }
    }
  }
  const prefix = dimension === 'agent' ? jsDebugGraphAgentTokenSeriesPrefix : jsDebugGraphModelTokenSeriesPrefix;
  const displayedItems = [...tokenItems.entries()]
    .filter(([, item]) => item.samples > 0)
    .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]));
  const visuals = debugGraphDisplayedTokenVisuals(displayedItems, ([key]) => key);
  return displayedItems.map(([key, item], index) => ({
      key: `${prefix}${key}`,
      label: item.label,
      descKey: dimension === 'agent' ? 'debug.graph.series.agentToken.desc' : 'debug.graph.series.modelToken.desc',
      descParams: dimension === 'agent' ? {agent: item.label} : {model: item.label},
      unit: 'tokensPerMinute',
      cssKey: 'agentToken',
      tokenPatternSeries: true,
      agentTokenSeries: dimension === 'agent',
      agentTokenKey: key,
      tokenDimension: dimension,
      agentTokenPatternIndex: visuals[index].patternIndex,
      color: visuals[index].color,
      value: bucket => {
        const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
        if (dimension === 'agent') {
          if (!tokenItem) return 0;
          return debugGraphAgentTokenBucketDimensionValue(bucket, tokenItem, selectedAgentDimension);
        }
        let value = 0;
        if (bucket?.agentTokenRates instanceof Map) {
          for (const agentRate of bucket.agentTokenRates.values()) {
            const modelRate = agentRate?.modelRates instanceof Map ? agentRate.modelRates.get(key) : null;
            if (!modelRate) continue;
            value += debugGraphAgentTokenBucketValue(bucket, {...modelRate, seconds: agentRate.seconds});
          }
        }
        return value;
      },
      hasData: bucket => {
        if (dimension === 'agent') {
          const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
          if (selectedAgentDimension !== 'output') return tokenItem?.billableAvailable === true;
          return Number(tokenItem?.samples || 0) > 0 || Number(tokenItem?.tokens || 0) > 0;
        }
        return [...(bucket?.agentTokenRates?.values?.() || [])].some(agentRate => {
          const modelRate = agentRate?.modelRates instanceof Map ? agentRate.modelRates.get(key) : null;
          return Number(modelRate?.samples || 0) > 0 || Number(modelRate?.tokens || 0) > 0;
        });
      },
      sampleCount: bucket => {
        if (dimension === 'agent') {
          const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
          if (selectedAgentDimension !== 'output') return Math.max(0, Number(tokenItem?.billableSamples?.[selectedAgentDimension]) || 0);
          return Math.max(0, Number(tokenItem?.samples) || 0);
        }
        let samples = 0;
        for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
          samples += Math.max(0, Number(agentRate?.modelRates?.get(key)?.samples) || 0);
        }
        return samples;
      },
    }));
}

function debugGraphAgentTokenSeriesDefs(buckets) {
  return debugGraphTokenSeriesDefs(buckets, 'agent');
}

function debugGraphStablePaletteIndex(identity, count) {
  const size = Math.max(1, Math.floor(Number(count) || 0));
  let hash = 2166136261;
  for (const character of String(identity || 'unknown')) {
    hash ^= character.codePointAt(0) || 0;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash % size;
}

function debugGraphDisplayedTokenVisuals(items, identityForItem = item => item?.key) {
  const colorCount = Math.max(1, jsDebugGraphAgentTokenColors.length);
  const patternCount = Math.max(1, jsDebugGraphAgentTokenPatternCount);
  const combinations = [];
  const pairedCount = Math.min(colorCount, patternCount);
  for (let index = 0; index < pairedCount; index += 1) combinations.push([index, index]);
  for (let colorIndex = 0; colorIndex < colorCount; colorIndex += 1) {
    for (let patternIndex = 0; patternIndex < patternCount; patternIndex += 1) {
      if (colorIndex === patternIndex && colorIndex < pairedCount) continue;
      combinations.push([colorIndex, patternIndex]);
    }
  }
  return (items || []).map((item, index) => {
    const identity = identityForItem(item);
    const combinationIndex = index < combinations.length
      ? index
      : debugGraphStablePaletteIndex(identity, combinations.length);
    const [colorIndex, patternIndex] = combinations[combinationIndex];
    return {color: jsDebugGraphAgentTokenColors[colorIndex], colorIndex, patternIndex};
  });
}

function debugGraphModelTokenDimensionLabel(dimension = jsDebugGraphModelTokenDimension) {
  const item = jsDebugGraphModelTokenDimensions.find(candidate => candidate.key === dimension);
  return item ? debugGraphCostText(item.labelKey, item.fallback) : debugGraphCostText('debug.cost.output', 'Output');
}

function debugGraphModelTokenDimensionDescriptionKey(dimension = jsDebugGraphModelTokenDimension) {
  const item = jsDebugGraphModelTokenDimensions.find(candidate => candidate.key === dimension);
  return item ? jsDebugGraphDescriptionKeyByLabelKey[item.labelKey] : '';
}

function setDebugGraphModelTokenDimension(value, {persist = true} = {}) {
  const selected = String(value || '');
  jsDebugGraphModelTokenDimension = jsDebugGraphModelTokenDimensions.some(item => item.key === selected)
    ? selected
    : 'output';
  if (persist) saveJsDebugStatsUiPreferences();
  // Every token dimension is projected from the buckets already in memory.
  // Repaint both tabs synchronously and bypass the passive focused-control
  // deferral; no history request or artificial loading state is warranted.
  refreshDebugGraphSurfaces({force: true, deferFocusedControl: false});
  return jsDebugGraphModelTokenDimension;
}

function debugGraphModelTokenComponentMatches(component, dimension = jsDebugGraphModelTokenDimension) {
  if (String(component?.unit || '').toLowerCase() !== 'tokens') return false;
  const direction = String(component?.direction || '').toLowerCase();
  const cacheRole = String(component?.cache_role || '').toLowerCase();
  if (dimension === 'all') return true;
  if (dimension === 'input') return direction === 'input' && !cacheRole.includes('read') && !cacheRole.includes('write');
  if (dimension === 'cacheRead') return cacheRole.includes('read');
  if (dimension === 'cacheWrite') return cacheRole.includes('write');
  return direction === 'output';
}

function debugGraphModelTokenComponentRecords(bucket, dimension = jsDebugGraphModelTokenDimension) {
  // Output is the compatibility projection of the authoritative generated-token
  // partition. Cost atoms can be temporarily partial during migration or across
  // provider telemetry variants, so they must not override the exact agent/model
  // totals that keep Agent tokens and Model tokens synchronized.
  if (dimension === 'output') return [];
  const components = debugGraphCostRows(bucket?.costSummary?.components);
  return components.filter(component => debugGraphModelTokenComponentMatches(component, dimension));
}

function debugGraphModelTokenSeriesIdentity(component) {
  const model = String(component?.model || 'unknown').trim() || 'unknown';
  const effort = String(component?.effort || '').trim().toLowerCase() || 'unknown';
  return {key: `${model}\u0000${effort}`, model, effort, label: effort === 'unknown' ? model : `${model} · ${effort}`};
}

function debugGraphSelectedModelTokenBucketValue(bucket) {
  const components = debugGraphModelTokenComponentRecords(bucket);
  if (components.length) return components.reduce((total, component) => total + Math.max(0, Number(component?.quantity) || 0), 0)
    / Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
  // Existing retained history only has the generated-output projection. Keep
  // that output view exact until component atoms are available for a bucket.
  if (jsDebugGraphModelTokenDimension !== 'output') return 0;
  let total = 0;
  for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
    for (const rate of agentRate?.modelRates?.values?.() || []) total += debugGraphAgentTokenBucketValue(bucket, {...rate, seconds: agentRate.seconds});
  }
  return total;
}

function debugGraphModelTokenSeriesDefs(buckets) {
  const tokenItems = new Map();
  for (const bucket of buckets) {
    const components = debugGraphModelTokenComponentRecords(bucket);
    if (components.length) {
      for (const component of components) {
        const identity = debugGraphModelTokenSeriesIdentity(component);
        const item = tokenItems.get(identity.key) || {...identity, samples: 0, componentBacked: true};
        item.samples += 1;
        tokenItems.set(identity.key, item);
      }
      continue;
    }
    // The default Output selector always uses the authoritative generated-token
    // partition, including new buckets with cost atoms, so its stack is exactly
    // the same total as Agent tokens/min.
    if (jsDebugGraphModelTokenDimension !== 'output') continue;
    for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
      for (const [rawModel, rate] of agentRate?.modelRates?.entries?.() || []) {
        const model = String(rawModel || 'unknown').trim() || 'unknown';
        const item = tokenItems.get(`legacy\u0000${model}`) || {key: `legacy\u0000${model}`, model, effort: '', label: model, samples: 0, componentBacked: false};
        item.samples += Number(rate?.samples || 0) > 0 || Number(rate?.tokens || 0) > 0 ? 1 : 0;
        tokenItems.set(item.key, item);
      }
    }
  }
  const displayedItems = [...tokenItems.values()]
    .filter(item => item.samples > 0)
    .sort((left, right) => left.label.localeCompare(right.label) || left.key.localeCompare(right.key));
  const visuals = debugGraphDisplayedTokenVisuals(displayedItems, item => item.key);
  return displayedItems.map((item, index) => ({
      key: `${jsDebugGraphModelTokenSeriesPrefix}${item.key}`,
      label: item.label,
      descKey: 'debug.graph.series.modelToken.desc',
      descParams: {model: item.label},
      unit: 'tokensPerMinute',
      cssKey: 'agentToken',
      tokenPatternSeries: true,
      agentTokenKey: item.key,
      tokenDimension: 'model',
      agentTokenPatternIndex: visuals[index].patternIndex,
      color: visuals[index].color,
      value: bucket => {
        const components = debugGraphModelTokenComponentRecords(bucket);
        if (components.length) {
          const quantity = components
            .filter(component => debugGraphModelTokenSeriesIdentity(component).key === item.key)
            .reduce((total, component) => total + Math.max(0, Number(component?.quantity) || 0), 0);
          return quantity / Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
        }
        if (item.componentBacked || jsDebugGraphModelTokenDimension !== 'output') return 0;
        let value = 0;
        for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
          const rate = agentRate?.modelRates instanceof Map ? agentRate.modelRates.get(item.model) : null;
          if (rate) value += debugGraphAgentTokenBucketValue(bucket, {...rate, seconds: agentRate.seconds});
        }
        return value;
      },
      hasData: bucket => {
        const components = debugGraphModelTokenComponentRecords(bucket);
        if (components.length) return components.some(component => debugGraphModelTokenSeriesIdentity(component).key === item.key);
        if (item.componentBacked || jsDebugGraphModelTokenDimension !== 'output') return false;
        return [...(bucket?.agentTokenRates?.values?.() || [])].some(agentRate => Number(agentRate?.modelRates?.get(item.model)?.tokens || 0) > 0);
      },
      sampleCount: bucket => {
        const components = debugGraphModelTokenComponentRecords(bucket)
          .filter(component => debugGraphModelTokenSeriesIdentity(component).key === item.key);
        if (components.length) {
          return components.reduce((total, component) => total + Math.max(1, Number(component?.samples ?? component?.sample_count) || 0), 0);
        }
        if (item.componentBacked || jsDebugGraphModelTokenDimension !== 'output') return 0;
        let samples = 0;
        for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
          samples += Math.max(0, Number(agentRate?.modelRates?.get(item.model)?.samples) || 0);
        }
        return samples;
      },
    }));
}


function debugGraphClientMetricSeriesDefs(buckets) {
  const peerSeries = jsDebugGraphClientMetrics
    .filter(metric => !['api', 'sse'].includes(metric.key))
    .filter(metric => buckets.some(bucket => debugGraphOtherClientMetricBuckets(bucket, metric).length > 0))
    .map(metric => debugGraphClientSeriesDef(metric, {
      key: `client:${jsDebugGraphOtherClientsAverageId}:${metric.key}`,
      labelKey: 'debug.graph.series.otherClientsAverage',
      clientId: jsDebugGraphOtherClientsAverageId,
      clientAggregate: jsDebugGraphOtherClientsAverageAggregate,
      clientLinePattern: jsDebugGraphOtherClientsAverageLinePattern,
      color: 'var(--bad)',
    }));
  const apiMetric = jsDebugGraphClientMetrics.find(metric => metric.key === 'api');
  const sseMetric = jsDebugGraphClientMetrics.find(metric => metric.key === 'sse');
  if (!apiMetric || !sseMetric || !buckets.some(bucket => debugGraphOtherClientMetricBuckets(bucket, apiMetric).length > 0)) return peerSeries;
  // API and SSE are two transports for the same request-rate comparison. A single red peer
  // line shows their summed per-client average rather than misleading parallel red averages.
  return [{
    key: `client:${jsDebugGraphOtherClientsAverageId}:apiSse`,
    chartMetricKey: 'api',
    metricKey: 'apiSse',
    cssKey: 'api',
    labelKey: 'debug.graph.series.otherClientsAverage',
    metricLabelKey: 'debug.graph.chart.clientApiSse',
    clientMetric: true,
    clientId: jsDebugGraphOtherClientsAverageId,
    clientAggregate: jsDebugGraphOtherClientsAverageAggregate,
    clientLinePattern: jsDebugGraphOtherClientsAverageLinePattern,
    unit: 'countPerSecond',
    color: 'var(--bad)',
    value: bucket => debugGraphOtherClientMetricAverage(bucket, apiMetric) + debugGraphOtherClientMetricAverage(bucket, sseMetric),
    hasData: bucket => debugGraphOtherClientMetricBuckets(bucket, apiMetric).length > 0,
  }, ...peerSeries];
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
      descKey: 'debug.graph.series.gpuDevice.desc',
      descParams: {device: label, metric: metric === 'gpuMemory' ? t('debug.graph.chart.gpuMemory') : t('debug.graph.chart.gpuUtil')},
      unit: metric === 'gpuMemory' ? 'bytes' : 'percent',
      hostMetric: metric,
      gpuDeviceId: deviceId,
      color: jsDebugGraphGpuDeviceColors[index % jsDebugGraphGpuDeviceColors.length],
      value: bucket => debugGraphHostMetricBucketValue(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
      hasData: bucket => debugGraphHostMetricBucketHasData(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
      sampleCount: bucket => Number(debugGraphHostMetricBucketItem(bucket, {hostMetric: metric, gpuDeviceId: deviceId})?.samples || 0),
      familyHasData: bucket => [...(bucket?.hostMetrics?.gpuDevices?.values?.() || [])]
        .some(item => Number(item?.samples || 0) > 0),
      displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.tenSecondGauge,
    }));
}

function debugGraphHostMetricSeriesDefs(buckets) {
  return [
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuUtil'),
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuMemory'),
  ];
}

function debugGraphServiceLoadSeriesDefs(buckets) {
  const services = new Map();
  for (const bucket of buckets) {
    for (const [key, item] of bucket?.hostMetrics?.serviceLoad?.entries?.() || []) {
      if (Number(item?.cpuSamples || 0) > 0) services.set(key, String(item.label || key));
    }
  }
  const items = [...services.entries()].sort((left, right) => left[1].localeCompare(right[1]) || left[0].localeCompare(right[0]));
  const visuals = debugGraphDisplayedTokenVisuals(items, ([key]) => key);
  const linePatterns = ['solid', 'dash', 'dot'];
  return items.map(([key, label], index) => ({
    key: `serviceLoad:${key}`, label, unit: 'percent', serviceLoad: true,
    color: visuals[index].color, linePattern: linePatterns[visuals[index].patternIndex % linePatterns.length],
    value: bucket => {
      const item = bucket?.hostMetrics?.serviceLoad?.get?.(key);
      return Number(item?.cpuSamples || 0) > 0 ? Number(item.cpuTotalPercent || 0) / Number(item.cpuSamples) : 0;
    },
    hasData: bucket => Number(bucket?.hostMetrics?.serviceLoad?.get?.(key)?.cpuSamples || 0) > 0,
    sampleCount: bucket => Number(bucket?.hostMetrics?.serviceLoad?.get?.(key)?.cpuSamples || 0),
    familyHasData: bucket => [...(bucket?.hostMetrics?.serviceLoad?.values?.() || [])].some(item => Number(item?.cpuSamples || 0) > 0),
    displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.tenSecondGauge,
  }));
}

function debugGraphDisplayHoldOutage(bucket) {
  return Number(bucket?.disconnectedMs || 0) > 0;
}

function debugGraphProjectSeriesSamples(def, buckets) {
  const holdMs = Math.max(0, Number(def?.displayHoldMs) || 0);
  const values = [];
  const hasDataValues = [];
  const observedDataValues = [];
  const provenanceValues = [];
  let heldSample = null;
  for (const [index, bucket] of (buckets || []).entries()) {
    const value = def.value(bucket);
    const observed = def.hasData(bucket) === true;
    if (observed) {
      const bucketStartMs = Number(bucket?.startMs) || 0;
      const requestedSampleTimeMs = typeof def.sampleTimeMs === 'function' ? Number(def.sampleTimeMs(bucket)) : NaN;
      const sampleTimeMs = Number.isFinite(requestedSampleTimeMs) ? requestedSampleTimeMs : bucketStartMs;
      const requestedSampleCount = typeof def.sampleCount === 'function' ? Number(def.sampleCount(bucket)) : 1;
      const sampleCount = Number.isFinite(requestedSampleCount) ? Math.max(0, requestedSampleCount) : 0;
      const provenance = {
        sampleTimeMs,
        sampleCount,
        sourceBucketStartMs: bucketStartMs,
        sourceBucketDurationMs: Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs),
        sourceIndex: index,
        expiresAtMs: holdMs > 0 ? sampleTimeMs + holdMs : sampleTimeMs,
        held: false,
      };
      values.push(value);
      hasDataValues.push(true);
      observedDataValues.push(true);
      provenanceValues.push(provenance);
      heldSample = holdMs > 0 ? {value, provenance} : null;
      continue;
    }
    const familyObserved = typeof def.familyHasData === 'function' && def.familyHasData(bucket) === true;
    if (familyObserved || debugGraphDisplayHoldOutage(bucket)) heldSample = null;
    const bucketStartMs = Number(bucket?.startMs) || 0;
    const bucketDurationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    const bucketEndMs = bucketStartMs + bucketDurationMs;
    const held = heldSample && bucketStartMs >= heldSample.provenance.sampleTimeMs
      && bucketEndMs <= heldSample.provenance.expiresAtMs;
    values.push(held ? heldSample.value : value);
    hasDataValues.push(Boolean(held));
    observedDataValues.push(false);
    provenanceValues.push(held ? {...heldSample.provenance, held: true} : null);
  }
  return {values, hasDataValues, observedDataValues, provenanceValues};
}

function debugGraphSeriesData(buckets) {
  const times = buckets.map(bucket => Number(bucket.startMs) || 0);
  const durations = buckets.map(bucket => Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs));
  const defs = [...jsDebugGraphSeries, ...debugGraphClientMetricSeriesDefs(buckets), ...debugGraphProcessCpuSeriesDefs(buckets), ...debugGraphHostMetricSeriesDefs(buckets), ...debugGraphServiceLoadSeriesDefs(buckets), ...debugGraphAgentTokenSeriesDefs(buckets), ...debugGraphModelTokenSeriesDefs(buckets)];
  return defs.map(def => {
    const localizedDef = {...def, label: debugGraphLocalizedLabel(def)};
    const {values, hasDataValues, observedDataValues, provenanceValues} = debugGraphProjectSeriesSamples(def, buckets);
    const sampleValues = values.filter((_value, index) => observedDataValues[index]);
    const sampleTimes = provenanceValues
      .filter((_provenance, index) => observedDataValues[index])
      .map(provenance => provenance.sampleTimeMs);
    const samples = sampleValues.length;
    const displayValues = values.filter((_value, index) => hasDataValues[index]);
    const displaySamples = displayValues.length;
    const max = Math.max(0, ...displayValues);
    const current = displayValues.length ? displayValues[displayValues.length - 1] : 0;
    const movingAverageSamples = Number(def.movingAverageSamples || 0);
    const movingAverageValues = movingAverageSamples > 0 ? debugGraphMovingAverageValues(sampleValues, movingAverageSamples) : [];
    return {
      ...localizedDef,
      values,
      times,
      durations,
      hasDataValues,
      observedDataValues,
      provenanceValues,
      movingAverageValues,
      movingAverageTimes: sampleTimes,
      movingAverageSamples,
      max,
      current,
      samples,
      displaySamples,
    };
  });
}

function debugGraphResolutionLabelHtml(nowMs = Date.now()) {
  const domain = debugGraphDomain(nowMs);
  syncDebugGraphResolutionOverride(nowMs, {persist: true, domain});
  const resolutionSeconds = debugGraphDisplayResolutionMs(domain, 0, nowMs) / 1000;
  const availableChoices = debugGraphAvailableResolutionChoices(domain, nowMs);
  const overrideSeconds = Number(jsDebugGraphResolutionOverrideSeconds) || 0;
  return `<label class="js-debug-resolution-label" data-js-debug-resolution data-js-debug-resolution-seconds="${esc(resolutionSeconds)}">${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}<select data-js-debug-resolution-override aria-label="${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}"><option value="0"${overrideSeconds === 0 ? ' selected' : ''}>AUTO</option>${availableChoices.map(value => `<option value="${value}"${overrideSeconds === value ? ' selected' : ''}>${value}s</option>`).join('')}</select></label>`;
}

function debugGraphRangeControlsHtml(nowMs = Date.now()) {
  const activeRange = activeJsDebugGraphRangeSeconds(nowMs);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (!options.length) return '';
  const sliderId = 'js-debug-range-options';
  const value = jsDebugGraphRangeOptionIndex(activeRange, nowMs);
  const zoomed = debugGraphZoomDomainValid();
  const rangeLabel = zoomed ? debugGraphCostRangeText(debugGraphDomain(nowMs)) : jsDebugGraphRangeLabel(activeRange, nowMs);
  const resetLabel = `${t('common.reset')} ${t('debug.graph.control.zoom')}`;
  return `<div class="js-debug-range-slider-control" data-js-debug-range-control>
    <span class="js-debug-range-prefix" aria-hidden="true">${esc(t('debug.graph.control.timeRange'))}</span>
    <input class="js-debug-range-slider" type="range" min="0" max="${esc(Math.max(0, options.length - 1))}" step="any" value="${esc(value)}" list="${esc(sliderId)}" data-js-debug-range-slider aria-label="${esc(t('debug.graph.control.timeRange'))}"${zoomed ? ' disabled aria-disabled="true"' : ''}>
    <datalist id="${esc(sliderId)}">${options.map((option, index) => `<option value="${esc(index)}" label="${esc(option.label)}" data-js-debug-range="${esc(option.seconds)}"></option>`).join('')}</datalist>
    <span class="js-debug-range-label" data-js-debug-range-label>${esc(rangeLabel)}</span>
    ${zoomed ? `<button type="button" class="js-debug-zoom-reset" data-js-debug-zoom-reset>${esc(resetLabel)}</button>` : ''}
  </div>`;
}

function debugGraphChartToggleControlsHtml() {
  return `<div class="js-debug-chart-toggle-control" role="group" aria-label="${esc(t('debug.graph.control.charts'))}">
    <span>${esc(t('debug.graph.control.charts'))}:</span>
    ${jsDebugGraphChartControlItems.map(group => {
      const label = debugGraphLocalizedLabel(group);
      // Compact labels are an English-only design vocabulary. Other locales keep the existing
      // localized chart title until their own compact translation exists; never leak English UI.
      const toggleLabel = i18nActiveLocale === 'en' ? String(group.toggleLabelEn || label) : label;
      const visible = debugGraphChartVisible(group.key);
      return `<button type="button" data-js-debug-chart-toggle="${esc(group.key)}" aria-pressed="${visible ? 'true' : 'false'}" title="${esc(label)}">${esc(toggleLabel)}</button>`;
    }).join('')}
  </div>`;
}

function debugGraphControlsHtml(nowMs = Date.now()) {
  return `<div class="js-debug-graph-controls">
    ${debugGraphRangeControlsHtml(nowMs)}
    ${debugGraphResolutionLabelHtml(nowMs)}
    <div class="js-debug-chart-layout-control" role="group" aria-label="${esc(t('debug.graph.control.size'))}"><span>${esc(t('debug.graph.control.size'))}:</span>${['AUTO', 'S', 'M', 'L', 'MAX'].map((label, value) => `<button type="button" data-js-debug-chart-layout="${value}" aria-pressed="${jsDebugGraphChartLayout === value ? 'true' : 'false'}">${label}</button>`).join('')}</div>
    ${debugGraphChartToggleControlsHtml()}
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

// True when [startMs, endMs) overlaps any genuine no-data range (a real
// coverage/communication hole), so the line should BREAK there instead of
// bridging it. Everything not inside such a range is treated as covered — the
// line stays continuous across it even when the recorded resolution is coarser
// than the display (linear interpolation between the surrounding real samples).
function debugGraphTimeInNoDataRange(noDataRanges, startMs, endMs) {
  if (!Array.isArray(noDataRanges) || !noDataRanges.length) return false;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
  return noDataRanges.some(range => Number(range?.startMs) < endMs && Number(range?.endMs) > startMs);
}

function debugGraphPolylinePointSegments(values, times, chartMax, domain, hasDataValues = null, durations = [], gapThresholdMs = 0, logScale = false, noDataRanges = null, observedValues = null) {
  // Coverage-aware breaking: when a genuine no-data range list is supplied, an
  // empty display cell never breaks the line on its own (a covered-but-coarser
  // span reads as one continuous, linearly interpolated line). The line breaks
  // only where a real recorded gap lies between two samples. Without a range
  // list, fall back to the legacy time-threshold behavior.
  const rangeBreak = Array.isArray(noDataRanges);
  const segments = [];
  let current = [];
  let previousDataEndMs = NaN;
  values.forEach((value, index) => {
    const timeMs = Number(times[index]);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(durations[index]) || jsDebugGraphRawBucketMs);
    // A HELD (carried-forward, non-observed) value that lands inside a genuine
    // no-data range is dropped and ends the run, so a held gauge can never leak a
    // flat line through a real recorded hole. A genuinely OBSERVED sample is always
    // drawable — coverage never erases a real measurement, it only stops the fill.
    const observed = !observedValues || observedValues[index] === true;
    const heldInNoData = rangeBreak && !observed && hasDataValues && hasDataValues[index] === true
      && debugGraphTimeInNoDataRange(noDataRanges, timeMs, Number.isFinite(timeMs) ? timeMs + durationMs : timeMs + 1);
    if ((hasDataValues && hasDataValues[index] !== true) || heldInNoData) {
      if (current.length && (heldInNoData || (!rangeBreak && gapThresholdMs <= 0))) {
        segments.push(current);
        current = [];
      }
      return;
    }
    const breakHere = current.length && (rangeBreak
      ? debugGraphTimeInNoDataRange(noDataRanges, previousDataEndMs, timeMs)
      : (gapThresholdMs > 0 && Number.isFinite(previousDataEndMs) && Number.isFinite(timeMs) && timeMs - previousDataEndMs >= gapThresholdMs));
    if (breakHere) {
      segments.push(current);
      current = [];
    }
    current.push(debugGraphPointForValue(value, timeMs, chartMax, domain, logScale).join(','));
    previousDataEndMs = Number.isFinite(timeMs) ? timeMs + durationMs : NaN;
  });
  if (current.length) segments.push(current);
  return segments;
}

function debugGraphPointForValue(value, timeMs, chartMax, domain, logScale = false) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  const rawX = Number.isFinite(Number(timeMs)) && Number.isFinite(startMs) && Number.isFinite(endMs)
    ? ((Number(timeMs) - startMs) / spanMs) * jsDebugGraphGeometry.width
    : jsDebugGraphGeometry.width;
  const x = Math.max(0, Math.min(jsDebugGraphGeometry.width, rawX));
  const y = debugGraphPlotYForValue(value, chartMax, logScale);
  return [x.toFixed(1), y.toFixed(1)];
}

function debugGraphPlotYForValue(value, chartMax, logScale = false) {
  const max = Math.max(Number(chartMax) || 0, 1);
  const rawValue = Math.max(0, Number(value) || 0);
  let normalized;
  if (logScale?.mode === 'broken-linear') {
    const threshold = Math.max(1, Math.min(max, Number(logScale.threshold) || max));
    const upperFraction = Math.max(0.1, Math.min(0.3, Number(logScale.upperFraction) || 0.18));
    normalized = rawValue <= threshold || max <= threshold
      ? (rawValue / threshold) * (1 - upperFraction)
      : (1 - upperFraction) + (((rawValue - threshold) / (max - threshold)) * upperFraction);
    normalized = Math.max(0, Math.min(1, normalized));
  } else {
    normalized = logScale === true
      ? Math.max(0, Math.min(1, Math.log1p(rawValue) / Math.log1p(max)))
      : Math.max(0, Math.min(1, rawValue / max));
  }
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
    // EventSource can reconnect while ordinary API requests still succeed. A stream reconnect is
    // useful telemetry, but it is not a full client outage and must not paint over real latency.
    if (debugGraphCurrentClientCommunicationCount(bucket) > 0) continue;
    const disconnectedMs = Math.min(durationMs, Math.max(0, debugGraphCurrentClientDisconnectedMs(bucket)));
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

function debugGraphCurrentClientRecord(bucket) {
  const clients = bucket?.clients;
  if (clients instanceof Map && clients.size > 0) return clients.get(jsDebugStatsClientIdForRequest()) || null;
  return bucket && typeof bucket === 'object' ? bucket : null;
}

function debugGraphCurrentClientCommunicationCount(bucket) {
  const record = debugGraphCurrentClientRecord(bucket);
  if (!record) return 0;
  return ['apiCount', 'sseCount', 'latencyCount', 'bandwidthBytes', 'heartbeatCount']
    .reduce((total, key) => total + Math.max(0, Number(record[key] || 0)), 0);
}

function debugGraphCurrentClientDisconnectedMs(bucket) {
  return Math.max(0, Number(debugGraphCurrentClientRecord(bucket)?.disconnectedMs || 0));
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
    const hasCurrentClientCommunication = debugGraphBucketRanges(buckets)
      .some(item => debugGraphCurrentClientCommunicationCount(item.bucket) > 0);
    const dataRanges = debugGraphBucketRanges(buckets)
      .filter(item => hasCurrentClientCommunication
        ? debugGraphCurrentClientCommunicationCount(item.bucket) > 0
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

function jsDebugHistoryCoverageFamilyForGroup(group) {
  const key = String(group?.key || '');
  if (!key) return '';
  // modelTokens is the one dimension-dependent chart: the manifest declares
  // which family backs each dimension instead of an inline family if-chain.
  if (key === 'modelTokens') {
    return jsDebugStatsFamilyByModelTokenDimension[jsDebugGraphModelTokenDimension === 'output' ? 'output' : 'default'] || '';
  }
  return jsDebugStatsFamilyByChartGroup[key] || '';
}

function jsDebugHistoryCoverageIntervalsForFamily(family) {
  const stores = jsDebugHistoryReadiness.storeCoverageIntervals || {};
  const manifestEntry = jsDebugStatsFamilyManifest[family];
  for (const key of manifestEntry ? [family, ...manifestEntry.legacyAliases] : []) {
    if (Object.prototype.hasOwnProperty.call(stores, key)) return stores[key];
  }
  // Per-family independence: once the server reports ANY per-store coverage, a
  // family with no store entry of its own was never recorded (fresh install or
  // a newly added metric such as system_memory / service_load). Treat it as
  // fully uncovered so its never-recorded window paints red, instead of
  // borrowing another family's coverage through the compatibility-global
  // intervals (cross-family inference). Only a legacy all-empty store map — the
  // pre-per-store protocol — falls back to the global coverage.
  if (Object.keys(stores).length > 0) return [];
  return jsDebugHistoryReadiness.coverageIntervals;
}

function debugGraphHistoryCoverageGapRuns(group, domain, alreadyPaintedRanges = []) {
  const family = jsDebugHistoryCoverageFamilyForGroup(group);
  if (!family) return [];
  const requestedRanges = (jsDebugHistoryReadiness.requestCoverageIntervals || []).map(interval => ({
    startMs: Number(interval.startSeconds) * 1000,
    endMs: Number(interval.endSeconds) * 1000,
  }));
  const coveredRanges = jsDebugHistoryCoverageIntervalsForFamily(family).map(interval => ({
    startMs: Number(interval.startSeconds) * 1000,
    endMs: Number(interval.endSeconds) * 1000,
  }));
  const gaps = [];
  for (const requested of debugGraphMergeTimeRanges(requestedRanges, domain)) {
    gaps.push(...debugGraphComplementTimeRanges(coveredRanges, requested));
  }
  const mergedGaps = debugGraphMergeTimeRanges(gaps, domain);
  const trimmedGaps = !alreadyPaintedRanges.length
    ? mergedGaps
    : debugGraphMergeTimeRanges(
      mergedGaps.flatMap(gap => debugGraphComplementTimeRanges(alreadyPaintedRanges, gap)),
      domain,
    );
  return debugGraphMeaningfulCoverageGaps(trimmedGaps, domain);
}

// A durable-coverage gap should paint only when it spans at least one rendered
// bucket at its own age. The 1-2 second sampler micro-breaks at owner/epoch
// handoffs (server restarts) are sub-bucket at coarse ranges; without this
// filter each inflated to a 1.5px red hairline and a fully recorded region read
// as a fake "missing chunk". Genuine holes and never-recorded prefixes stay.
function debugGraphMeaningfulCoverageGaps(ranges, domain) {
  const nowMs = Number(domain?.endMs) || Date.now();
  return (ranges || []).filter(range => {
    const startMs = Number(range?.startMs);
    const endMs = Number(range?.endMs);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
    return endMs - startMs >= debugGraphBucketDurationForTime(startMs, nowMs);
  });
}

function debugGraphHistoryCoverageGapRectsHtml(group, domain, alreadyPaintedRanges = []) {
  const family = jsDebugHistoryCoverageFamilyForGroup(group);
  return debugGraphHistoryCoverageGapRuns(group, domain, alreadyPaintedRanges).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    return `<g data-js-debug-history-coverage-family="${esc(family)}">${debugGraphPlotOverlayRectHtml(
      'js-debug-no-data-range js-debug-history-no-data-range',
      'data-js-debug-history-no-data-range',
      index,
      x1,
      Math.max(1.5, x2 - x1),
      t('debug.graph.noDataRecorded'),
    )}</g>`;
  }).join('');
}

// The union of every genuine no-data range painted for a chart (real coverage
// holes, client communication gaps, agent-status gaps, and disconnected spans).
// These are the ONLY places a series line/area may break; everything else is a
// covered span the line stays continuous across (interpolating a coarser tier).
function debugGraphChartGenuineNoDataRanges(group, domain, overlayBuckets, disconnectedRanges, groupSeries) {
  const ranges = [];
  const statusRuns = group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRuns(overlayBuckets, domain) : [];
  ranges.push(...debugGraphHistoryCoverageGapRuns(group, domain, statusRuns));
  ranges.push(...statusRuns);
  if (group.noDataOverlay === true) {
    ranges.push(...debugGraphNoDataRuns(overlayBuckets, domain, debugGraphCurrentClientSeriesItems(groupSeries)));
  }
  // A disconnected span is a genuine sampling outage for EVERY series (the gauge
  // was not observed), so it always breaks the line — not only on charts that
  // draw the dedicated disconnected overlay.
  ranges.push(...(Array.isArray(disconnectedRanges) ? disconnectedRanges : debugGraphDisconnectedRanges(overlayBuckets, domain)));
  return debugGraphMergeTimeRanges(ranges, domain);
}

function debugGraphAgentStatusNoDataRuns(buckets, domain) {
  const ranges = debugGraphBucketRanges(buckets);
  const statusRanges = ranges
    .filter(item => Number(item.bucket?.agentActivitySamples || 0) > 0)
    .map(item => ({startMs: item.startMs, endMs: item.endMs}));
  const serverRanges = ranges.filter(item => Number(item.bucket?.cpuCount || 0) > 0 || Number(item.bucket?.agentActivitySamples || 0) > 0);
  if (!serverRanges.length) return [];
  const scope = {
    startMs: Math.max(Number(domain?.startMs) || 0, serverRanges[0].startMs),
    endMs: Number(domain?.endMs) || 0,
  };
  if (scope.endMs <= scope.startMs) return [];
  const lastServerEndMs = serverRanges.at(-1).endMs;
  return debugGraphComplementTimeRanges(statusRanges, scope)
    .map(range => range.startMs >= lastServerEndMs - 1
      ? {...range, startMs: range.startMs + jsDebugGraphNoDataOverlayDelayMs}
      : range)
    .filter(range => range.endMs > range.startMs);
}

function debugGraphAgentStatusNoDataRectsHtml(buckets, domain) {
  return debugGraphAgentStatusNoDataRuns(buckets, domain).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    return debugGraphPlotOverlayRectHtml(
      'js-debug-no-data-range js-debug-agent-status-no-data-range',
      'data-js-debug-agent-status-no-data-range',
      index,
      x1,
      Math.max(1.5, x2 - x1),
      t('debug.graph.agentStatus.noData'),
    );
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

function debugGraphSeriesPlotObservedValues(series) {
  return Array.isArray(series.plotObservedValues) ? series.plotObservedValues : (series.observedDataValues || null);
}

function debugGraphSeriesClassKey(series) {
  return String(series?.cssKey || series?.key || '').replace(/[^A-Za-z0-9_-]/g, '-');
}

function debugGraphAgentTokenPatternIndex(series) {
  if (series?.tokenPatternSeries !== true) return -1;
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
  if (series?.tokenPatternSeries !== true) return '';
  return ` data-js-debug-token-agent="${esc(series.agentTokenKey || '')}" data-js-debug-token-agent-label="${esc(series.label || '')}" data-js-debug-token-pattern="${esc(debugGraphAgentTokenPatternIndex(series))}"`;
}

function debugGraphPolylineHtml(series, chartMax, domain, logScale = false, noDataRanges = null) {
  // The line is one continuous, linearly interpolated stroke across every covered
  // span — a coarser recorded resolution (e.g. 60s data on a 10s chart) never
  // shows as a gap. It breaks ONLY at genuine no-data ranges (real coverage or
  // communication holes) supplied by the chart, which stay honest as red no-data
  // bands. Without a supplied range list (legacy callers) fall back to the old
  // time-threshold: client metrics break at their communication-gap threshold,
  // other series at any gap.
  const rangeAware = Array.isArray(noDataRanges);
  const cpuSeries = series?.processCpu === true || series?.key === 'cpu' || series?.key === 'systemCpu';
  const heldGaugeSeries = Number(series?.displayHoldMs || 0) > 0;
  const gapThresholdMs = rangeAware ? 0 : (series?.clientMetric === true
    ? debugGraphCommunicationGapThresholdMs([series])
    : ((cpuSeries || heldGaugeSeries) ? 1 : 0));
  return debugGraphPolylinePointSegments(
    debugGraphSeriesPlotValues(series),
    series.times || [],
    chartMax,
    domain,
    debugGraphSeriesPlotHasDataValues(series),
    series.durations || [],
    gapThresholdMs,
    logScale,
    rangeAware ? noDataRanges : null,
    rangeAware ? debugGraphSeriesPlotObservedValues(series) : null,
  ).map((points, index) => {
    if (!points.length) return '';
    const segmentAttr = index > 0 ? ` data-js-debug-series-segment="${esc(index)}"` : '';
    return `<polyline class="${esc(debugGraphSeriesLineClassName(series))}" data-js-debug-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}${debugGraphSeriesLinePatternAttrs(series)}${segmentAttr} points="${esc(points.join(' '))}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.label)}</title></polyline>`;
  }).join('');
}

function debugGraphAreaPathHtml(series, chartMax, domain, noDataRanges = null) {
  const values = debugGraphSeriesPlotValues(series);
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const pointIndexes = values
    .map((_value, index) => index)
    .filter(index => !hasDataValues || hasDataValues[index] === true);
  if (!pointIndexes.length) return '';
  const baseline = jsDebugGraphGeometry.plotBottom;
  const lowerValues = Array.isArray(series.stackBaseValues) ? series.stackBaseValues : null;
  // Split the fill into runs broken ONLY at genuine no-data ranges, so a
  // covered-but-coarser span fills continuously (matching the line) while a real
  // recorded hole stays an honest gap under its red no-data band.
  const observedValues = debugGraphSeriesPlotObservedValues(series);
  const runs = [];
  let run = [];
  let previousEndMs = NaN;
  for (const index of pointIndexes) {
    const startMs = debugGraphSeriesTimeMs(series, index);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(series.durations?.[index]) || jsDebugGraphRawBucketMs);
    // Drop a HELD (non-observed) point that lands inside a genuine no-data range so
    // the fill never leaks into a real hole; a real measurement is always kept.
    const observed = !observedValues || observedValues[index] === true;
    if (!observed && debugGraphTimeInNoDataRange(noDataRanges, startMs, Number.isFinite(startMs) ? startMs + durationMs : startMs + 1)) {
      if (run.length) { runs.push(run); run = []; }
      continue;
    }
    if (run.length && debugGraphTimeInNoDataRange(noDataRanges, previousEndMs, startMs)) {
      runs.push(run);
      run = [];
    }
    run.push(index);
    previousEndMs = Number.isFinite(startMs) ? startMs + durationMs : NaN;
  }
  if (run.length) runs.push(run);
  const stacked = lowerValues ? ` data-js-debug-area-stacked="${esc(series.key)}"` : '';
  const plotCurrent = values.at(-1);
  const total = Number.isFinite(Number(plotCurrent)) ? ` data-js-debug-area-total="${esc(Number(plotCurrent))}"` : '';
  return runs.map(runIndexes => {
    const upperPoints = runIndexes.map(index => debugGraphPointForValue(values[index], debugGraphSeriesTimeMs(series, index), chartMax, domain));
    const lowerPoints = lowerValues
      ? runIndexes.map(index => debugGraphPointForValue(lowerValues[index], debugGraphSeriesTimeMs(series, index), chartMax, domain))
      : upperPoints.map(point => [point[0], baseline.toFixed(1)]);
    const firstLower = lowerPoints[0] || [upperPoints[0][0], baseline.toFixed(1)];
    const path = [
      `M ${firstLower[0]},${firstLower[1]}`,
      ...upperPoints.map(point => `L ${point[0]},${point[1]}`),
      ...lowerPoints.slice().reverse().map(point => `L ${point[0]},${point[1]}`),
      'Z',
    ].join(' ');
    return `<path class="js-debug-area js-debug-area--${esc(debugGraphSeriesClassKey(series))}" data-js-debug-area-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked}${total} d="${esc(path)}"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.label)}</title></path>`;
  }).join('');
}

function debugGraphBarRectsHtml(series, chartMax, domain, logScale = false) {
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
    const vertical = debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, series.zeroBar === true, logScale);
    const stacked = lowerValues ? ` data-js-debug-bar-stacked="${esc(series.key)}"` : '';
    return `<rect class="js-debug-bar js-debug-bar--${esc(classKey)}" data-js-debug-bar-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked} data-js-debug-bar-total="${esc(topValue)}" data-js-debug-bar-gap="${esc(gap.toFixed(2))}" x="${esc(x.toFixed(2))}" y="${esc(vertical.y.toFixed(2))}" width="${esc(width.toFixed(2))}" height="${esc(vertical.height.toFixed(2))}"${debugGraphSeriesStyleAttr(series, {barPattern: true})}><title>${esc(series.label)}</title></rect>`;
  }).join('');
}

function debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, zeroBar = false, logScale = false) {
  const top = debugGraphPlotYForValue(topValue, chartMax, logScale);
  const bottom = debugGraphPlotYForValue(bottomValue, chartMax, logScale);
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
    ${seriesItems.map(series => {
      const descKey = series.descKey || jsDebugGraphDescriptionKeyByLabelKey[series.labelKey] || jsDebugGraphDescriptionKeyByLabelKey[series.metricLabelKey];
      return `<div class="js-debug-legend-item" data-js-debug-legend="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}>${debugGraphLegendSwatchHtml(series)}<span${debugGraphExplainAttrs(series.label, descKey, {attribute: 'data-js-debug-legend-label-desc', desc: debugGraphLocalizedDescription({...series, descKey})})}>${esc(series.label)}</span></div>`;
    }).join('')}
  </div>`;
}

function debugGraphLegendSwatchHtml(series) {
  if (series?.tokenPatternSeries === true) return debugGraphAgentTokenLegendSwatchHtml(series);
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

function debugGraphGridLineY(value, chartMax, logScale = false) {
  return debugGraphPlotYForValue(value, chartMax, logScale);
}

function debugGraphAxisTickStyle(value, chartMax, logScale = false) {
  const percent = (debugGraphGridLineY(value, chartMax, logScale) / jsDebugGraphGeometry.height) * 100;
  return ` style="--js-debug-axis-y: ${esc(percent.toFixed(3))}%;"`;
}

function debugGraphGridLinesHtml(group, axisMax) {
  const max = Math.max(0, Number(axisMax) || 0);
  const fallbackMax = max > 0 ? max : 1;
  const scale = group.scale ?? (group.logScale === true);
  const values = group.integerGridLines === true
    ? debugGraphIntegerGridValues(max)
    : scale?.mode === 'broken-linear'
    ? [fallbackMax, scale.threshold, scale.threshold / 2, 0]
    : scale === true
    ? [fallbackMax, Math.expm1(Math.log1p(fallbackMax) / 2), 0]
    : [fallbackMax, fallbackMax / 2, 0];
  return values.map(value => {
    const y = debugGraphGridLineY(value, max, scale).toFixed(1);
    const axisValue = group.integerGridLines === true ? ` data-js-debug-grid-value="${esc(value)}"` : '';
    return `<line class="js-debug-grid-line${group.integerGridLines === true ? ' js-debug-grid-line--integer' : ''}" data-js-debug-grid-line="${esc(group.key)}"${axisValue} x1="0" y1="${esc(y)}" x2="${esc(jsDebugGraphGeometry.width)}" y2="${esc(y)}" vector-effect="non-scaling-stroke"></line>`;
  }).join('');
}

function debugGraphAxisBreakHtml(group, axisMax, scale) {
  if (scale?.mode !== 'broken-linear') return '';
  const y = debugGraphGridLineY(scale.threshold, axisMax, scale);
  const left = `M0 ${y - 2}l4 4 4-4 4 4`;
  const rightX = jsDebugGraphGeometry.width - 12;
  const right = `M${rightX} ${y - 2}l4 4 4-4 4 4`;
  return `<path class="js-debug-axis-break" data-js-debug-axis-break="${esc(group.key)}" data-js-debug-axis-break-value="${esc(scale.threshold)}" d="${esc(`${left} ${right}`)}" fill="none" vector-effect="non-scaling-stroke"></path>`;
}

function debugGraphAxisHtml(group, max) {
  const axisMax = Math.max(0, Number(max) || 0);
  if (group.integerAxis === true) return debugGraphIntegerAxisHtml(group, axisMax);
  const positionMax = axisMax > 0 ? axisMax : 1;
  const scale = group.scale ?? (group.logScale === true);
  if (scale?.mode === 'broken-linear') {
    const threshold = Math.min(positionMax, Number(scale.threshold) || positionMax);
    return `<div class="js-debug-y-axis js-debug-y-axis--broken" data-js-debug-axis="${esc(group.key)}" data-js-debug-axis-break="${esc(threshold)}">
      <span data-js-debug-axis-max="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax, positionMax, scale)}>${esc(debugGraphAxisValueText(axisMax, group.unit))}</span>
      <span data-js-debug-axis-break-label="${esc(group.key)}"${debugGraphAxisTickStyle(threshold, positionMax, scale)}>${esc(debugGraphAxisValueText(threshold, group.unit))}</span>
      <span data-js-debug-axis-mid="${esc(group.key)}"${debugGraphAxisTickStyle(threshold / 2, positionMax, scale)}>${esc(debugGraphAxisValueText(threshold / 2, group.unit))}</span>
      <span data-js-debug-axis-zero="${esc(group.key)}"${debugGraphAxisTickStyle(0, positionMax, scale)}>${esc(debugGraphAxisValueText(0, group.unit))}</span>
    </div>`;
  }
  return `<div class="js-debug-y-axis" data-js-debug-axis="${esc(group.key)}">
    <span data-js-debug-axis-max="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax, positionMax, scale)}>${esc(debugGraphAxisValueText(axisMax, group.unit))}</span>
    <span data-js-debug-axis-mid="${esc(group.key)}"${debugGraphAxisTickStyle(scale === true ? Math.expm1(Math.log1p(positionMax) / 2) : positionMax / 2, positionMax, scale)}>${esc(debugGraphAxisValueText(scale === true ? Math.expm1(Math.log1p(axisMax) / 2) : axisMax / 2, group.unit))}</span>
    <span data-js-debug-axis-zero="${esc(group.key)}"${debugGraphAxisTickStyle(0, positionMax, scale)}>${esc(debugGraphAxisValueText(0, group.unit))}</span>
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
  // Show seconds when the chart is actually rendering at 1-second resolution — where
  // the data (and the wall-clock slide) genuinely tick every second — regardless of the
  // range's span. Coarser resolutions (10s/60s/300s) show HH:MM because a seconds digit
  // there is fake precision. Keyed off the same effective-resolution owner the Resolution
  // label reads, not a span proxy.
  const resolutionSeconds = debugGraphDisplayResolutionMs(domain, 0, Date.now()) / 1000;
  const includeSeconds = !includeDate && resolutionSeconds <= 1;
  return `<div class="js-debug-x-axis" data-js-debug-x-axis>
    ${ticks.map(tick => `<span data-js-debug-x-tick="${esc(tick.name)}"${includeDate ? ` data-js-debug-x-date="${esc(debugGraphLocalDateKey(tick.ms))}"` : ''}>${esc(debugGraphTimeLabel(tick.ms, {includeDate, includeSeconds}))}</span>`).join('')}
  </div>`;
}

function debugGraphGroupSeriesItems(group, seriesItems) {
  if (group.serviceLoad === true) return seriesItems.filter(series => series.serviceLoad === true);
  if (group.dynamicAgentTokens === true) return seriesItems.filter(series => series.agentTokenSeries === true);
  if (group.dynamicTokenDimension) return seriesItems.filter(series => series.tokenDimension === group.dynamicTokenDimension);
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
  const minimumAxisMax = Math.max(0, Number(group.minimumAxisMax) || 0);
  if (group.exactIntegerAxisMax === true) return Math.max(minimumAxisMax, Math.ceil(Number(rawMax) || 0));
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
  if (group?.key === 'agentTokens' || group?.key === 'modelTokens') return debugGraphAgentTokenDisplayBuckets(nowMs);
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
  if (data.group.key === 'serversLoad') {
    const bucket = data.buckets[index];
    const details = data.groupSeries.flatMap(series => {
      const key = String(series.key || '').replace(/^serviceLoad:/, '');
      const item = bucket?.hostMetrics?.serviceLoad?.get?.(key);
      const samples = Number(item?.cpuSamples || 0);
      if (samples <= 0) return [];
      const avg = Number(item.cpuTotalPercent || 0) / samples;
      return [`${series.label}: ${debugGraphValueText(avg, 'percent')} (${t('debug.graph.serviceLoad.range', {
        minimum: debugGraphValueText(Number(item.cpuMinPercent || 0), 'percent'),
        average: debugGraphValueText(avg, 'percent'),
        maximum: debugGraphValueText(Number(item.cpuMaxPercent || 0), 'percent'),
      })})`];
    });
    return details.join(' · ') || debugGraphValueText(0, data.group.unit);
  }
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

function debugGraphTokenHoverDetailAtTime(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data || !['agentTokens', 'modelTokens'].includes(data.group?.key)) return null;
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  const bucket = index >= 0 ? data.buckets[index] : null;
  const startMs = Number(bucket?.startMs);
  const endMs = startMs + Math.max(1, Number(bucket?.durationMs) || 0);
  const hoveredTime = debugGraphTimeLabel(timestamp, {includeSeconds: false});
  const span = Number.isFinite(startMs)
    ? `${debugGraphTimeLabel(startMs, {includeSeconds: false})}–${debugGraphTimeLabel(endMs, {includeSeconds: false})}`
    : hoveredTime;
  if (index < 0) return {span, detail: debugGraphCostText('debug.graph.tokens.noData', 'No token samples'), noData: true};
  const activeSeries = data.groupSeries
    .filter(series => !data.group.dynamicTokenDimension || series.tokenDimension === data.group.dynamicTokenDimension)
    .filter(series => !Array.isArray(series.hasDataValues) || series.hasDataValues[index] === true);
  const sampleCount = activeSeries.reduce((total, series) => {
    const provenance = Array.isArray(series.provenanceValues) ? series.provenanceValues[index] : null;
    return total + Math.max(0, Number(provenance?.sampleCount) || 0);
  }, 0);
  if (!activeSeries.length || sampleCount <= 0) {
    return {span, detail: debugGraphCostText('debug.graph.tokens.noData', 'No token samples'), noData: true};
  }
  const value = debugGraphHoverValueAtTime(chart, timestamp);
  const sampleLabel = sampleCount === 1 ? 'sample' : 'samples';
  return {span, detail: `${value} · ${debugSystemNumber(sampleCount)} ${sampleLabel}`, noData: false};
}

function debugGraphHoverProvenanceAtTime(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data) return [];
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  if (index < 0) return [];
  return data.groupSeries.flatMap(series => {
    if (Array.isArray(series.hasDataValues) && series.hasDataValues[index] !== true) return [];
    const provenance = Array.isArray(series.provenanceValues) ? series.provenanceValues[index] : null;
    return provenance ? [{series: series.key, ...provenance}] : [];
  });
}

function debugGraphHeldProvenanceText(provenance) {
  const held = (provenance || []).filter(item => item?.held === true && Number.isFinite(Number(item.sampleTimeMs)));
  if (!held.length) return '';
  const sampleTimeMs = Math.max(...held.map(item => Number(item.sampleTimeMs)));
  const sampleCount = held
    .filter(item => Number(item.sampleTimeMs) === sampleTimeMs)
    .reduce((total, item) => total + Math.max(0, Number(item.sampleCount) || 0), 0);
  return `↳ ${debugGraphExactTimeLabel(sampleTimeMs)} · n=${debugSystemNumber(sampleCount)}`;
}

function debugGraphLivePulseHtml(groupSeries, buckets, domain, nowMs = Date.now()) {
  if (domain?.zoomed || Number(domain?.rangeSeconds) > 3600) return '';
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainEnd) || nowMs > domainEnd + 1000) return '';
  // The live edge is the cell that contains "now" at this chart's own display
  // resolution. Mark it on EVERY live chart whether or not a sample has landed
  // in it yet: sparse charts (agent/model tokens) have no data bucket at the
  // edge but are still live, so the shared heartbeat must appear there too. The
  // pulse only ever marks this one ongoing cell, never a gap, and its paint is
  // driven solely by the shared agent-status opacity clock.
  const durationMs = Math.max(1, Number(buckets?.at?.(-1)?.durationMs) || debugGraphBucketDurationForTime(nowMs, nowMs));
  const startMs = Math.floor(nowMs / durationMs) * durationMs;
  const xStart = debugGraphXForTime(startMs, domain);
  const xLimit = debugGraphXForTime(startMs + durationMs, domain);
  const width = Math.max(0.5, xLimit - xStart);
  return `<rect class="js-debug-live-pulse heartbeat-pulse" data-js-debug-live-pulse x="${esc(xStart)}" y="0" width="${esc(width)}" height="${esc(jsDebugGraphGeometry.height)}" pointer-events="none"></rect>`;
}

function debugGraphChartHtml(group, seriesItems, domain, buckets = [], overlayBuckets = buckets, disconnectedRanges = null, options = {}) {
  const groupLabel = debugGraphChartLabel(group, buckets);
  const groupTitleAttrs = debugGraphExplainAttrs(groupLabel, group.descKey, {attribute: 'data-js-debug-chart-desc'});
  const groupSeries = debugGraphGroupSeriesItems(group, seriesItems);
  jsDebugGraphHoverChartData.set(group.key, {buckets, group, groupSeries});
  // Series lines/areas stay continuous across every covered span and break only
  // at these genuine no-data ranges (the same holes painted as red no-data bands).
  const genuineNoDataRanges = debugGraphChartGenuineNoDataRanges(group, domain, overlayBuckets, disconnectedRanges, groupSeries);
  const legendSeries = debugGraphLegendSeriesItems(group, groupSeries);
  const plottedGroupSeries = groupSeries.filter(series => series.movingAverageOnly !== true && series.overlayLineOnly !== true);
  const overlayLineSeries = groupSeries.filter(series => series.overlayLineOnly === true);
  const areaSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => series.hostMetric && series.hostProcessId) : [];
  const lineSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => !areaSeries.includes(series)) : plottedGroupSeries;
  const plotSeries = group.kind === 'area'
    ? debugGraphStackedSeries(areaSeries)
    : (group.stacked === true ? debugGraphStackedSeries(plottedGroupSeries) : plottedGroupSeries);
  const tokenAxis = (group.key === 'agentTokens' || group.key === 'modelTokens') ? options.tokenAxis : null;
  const plotScale = tokenAxis?.scale || debugGraphUsesLogScale(group, plotSeries);
  const movingAverageSeries = groupSeries.filter(series => Number(series.movingAverageSamples || 0) > 0);
  const rawMax = Math.max(0, ...plotSeries.map(series => Number(series.plotMax ?? series.max) || 0), ...lineSeries.map(series => Number(series.max) || 0), debugGraphChartCapacityMax(group, buckets));
  const max = tokenAxis ? tokenAxis.axisMax : debugGraphChartAxisMax(group, rawMax);
  const axisMax = max > 0 ? max : 0;
  const chartClasses = ['js-debug-chart'];
  if (group.dynamicAgentTokens === true || group.dynamicTokenDimension) chartClasses.push('js-debug-chart--token-agents');
  const bucketSeconds = Number(group.bucketSeconds);
  const bucketAttr = Number.isFinite(bucketSeconds) && bucketSeconds > 0 ? ` data-js-debug-chart-bucket-seconds="${esc(bucketSeconds)}"` : '';
  const displayedSummary = debugGraphDisplayedSummary(group, buckets);
  const displayedSummaryHtml = displayedSummary === null
    ? ''
    : `<span class="js-debug-chart-summary"${debugGraphExplainAttrs(displayedSummary.text, displayedSummary.descKey, {attribute: 'data-js-debug-summary-desc'})} data-js-debug-${esc(displayedSummary.attribute)}="${esc(displayedSummary.value)}">${esc(displayedSummary.text)}</span>`;
  const gpuUnavailable = (group.hostMetric === 'gpuUtil' || group.hostMetric === 'gpuMemory') && !groupSeries.length;
  // A GPU chart with no device series must explain itself precisely, never the ambiguous
  // generic "None" (screenshot 010): distinguish a host with NO GPU telemetry at all from
  // one whose samples exist outside the current window.
  const gpuUnavailableText = gpuUnavailable
    ? (debugGraphAnyGpuDeviceSamplesCached()
      ? debugGraphCostText('debug.graph.gpuNoWindowSamples', 'No GPU samples in this time window')
      : debugGraphCostText('debug.graph.gpuUnavailableHost', 'GPU telemetry is not available on this host'))
    : '';
  const agentBillableUnavailable = group.key === 'agentTokens'
    && jsDebugGraphModelTokenDimension !== 'output'
    && !buckets.some(bucket => [...(bucket?.agentTokenRates?.values?.() || [])].some(rate => rate?.billableAvailable === true));
  const chartUnavailable = gpuUnavailable || agentBillableUnavailable;
  const chartUnavailableText = agentBillableUnavailable
    ? debugGraphCostText('debug.graph.agentTokens.billableUnavailable', 'No billable breakdown for this window')
    : gpuUnavailableText;
  const scaleAttr = plotScale?.mode === 'broken-linear' ? 'broken-linear' : (plotScale === true ? 'log' : 'linear');
  const breakAttr = plotScale?.mode === 'broken-linear' ? ` data-js-debug-chart-axis-break="${esc(plotScale.threshold)}"` : '';
  const modelDimensionControl = group.key === 'modelTokens'
    ? `<label class="js-debug-resolution-label js-debug-model-token-dimension" data-js-debug-model-token-dimension>${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}: <select data-js-debug-model-token-dimension-select aria-label="${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}">${jsDebugGraphModelTokenDimensions.map(item => {
        const label = debugGraphModelTokenDimensionLabel(item.key);
        return `<option value="${esc(item.key)}"${item.key === jsDebugGraphModelTokenDimension ? ' selected' : ''}${debugGraphExplainAttrs(label, debugGraphModelTokenDimensionDescriptionKey(item.key), {attribute: 'data-js-debug-model-token-dimension-desc'})}>${esc(label)}</option>`;
      }).join('')}</select></label>`
    : '';
  return `<section class="${esc(chartClasses.join(' '))}" data-js-debug-chart="${esc(group.key)}" data-js-debug-chart-kind="${esc(group.kind || 'line')}" data-js-debug-chart-axis-max="${esc(axisMax)}" data-js-debug-chart-unit="${esc(group.unit || '')}"${tokenAxis ? ' data-js-debug-token-axis="shared"' : ''}${breakAttr}${bucketAttr}${group.stacked === true ? ' data-js-debug-chart-stacked="true"' : ''} data-js-debug-chart-scale="${esc(scaleAttr)}">
    <div class="js-debug-chart-head">
      <div class="js-debug-chart-heading-row">
        <span class="js-debug-chart-title"${groupTitleAttrs}>${esc(groupLabel)}</span>
        ${displayedSummaryHtml}
        ${modelDimensionControl}
        <button type="button" class="js-debug-chart-close control-active-hover" data-js-debug-chart-close="${esc(group.key)}" aria-label="${esc(t('common.close'))} ${esc(groupLabel)}" title="${esc(t('common.close'))}">×</button>
      </div>
      ${chartUnavailable ? '' : debugGraphLegendHtml(legendSeries)}
    </div>
    ${chartUnavailable ? `<div class="js-debug-chart-unavailable"${gpuUnavailable ? ` data-js-debug-gpu-unavailable="${esc(group.key)}"` : ' data-js-debug-agent-billable-unavailable'}>${esc(chartUnavailableText)}</div>` : `<div class="js-debug-chart-body">
      ${debugGraphAxisHtml({...group, scale: plotScale}, axisMax)}
      <div class="js-debug-plot">
        <svg class="js-debug-line-chart" viewBox="0 0 ${esc(jsDebugGraphGeometry.width)} ${esc(jsDebugGraphGeometry.height)}" role="img" aria-label="${esc(groupLabel)}" preserveAspectRatio="none">
          ${group.kind === 'bar' ? debugGraphAgentTokenPatternDefsHtml(plotSeries) : ''}
          ${group.kind === 'area' ? plotSeries.map(series => debugGraphAreaPathHtml(series, Math.max(axisMax, 1), domain, genuineNoDataRanges)).join('') : ''}
          ${group.kind === 'bar' ? plotSeries.map(series => debugGraphBarRectsHtml({...series, zeroBar: group.zeroBar === true}, Math.max(axisMax, 1), domain, plotScale)).join('') : ''}
          ${debugGraphGridLinesHtml({...group, scale: plotScale}, axisMax)}
          ${plotScale?.mode === 'broken-linear' ? debugGraphAxisBreakHtml(group, axisMax, plotScale) : ''}
          ${group.noDataOverlay === true ? debugGraphNoDataRectsHtml(overlayBuckets, domain, debugGraphCurrentClientSeriesItems(groupSeries)) : ''}
          ${group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRectsHtml(overlayBuckets, domain) : ''}
          ${debugGraphHistoryCoverageGapRectsHtml(group, domain, group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRuns(overlayBuckets, domain) : [])}
          ${group.kind === 'bar' ? '' : (group.kind === 'area' ? lineSeries : plotSeries).map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain, plotScale, genuineNoDataRanges)).join('')}
          ${overlayLineSeries.map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain, plotScale, genuineNoDataRanges)).join('')}
          ${movingAverageSeries.map(series => debugGraphMovingAveragePolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${debugGraphLivePulseHtml(groupSeries, buckets, domain)}
          ${group.disconnectedOverlay === true ? debugGraphDisconnectedRectsHtml(overlayBuckets, domain, disconnectedRanges) : ''}
          ${debugGraphInteractionOverlayHtml()}
        </svg>
      </div>
      ${debugGraphXAxisHtml(domain)}
    </div>`}
    ${chartUnavailable ? '' : '<div class="js-debug-hover-tooltip" data-js-debug-hover-tooltip hidden><span data-js-debug-hover-max></span><span aria-hidden="true"> · </span><time data-js-debug-hover-time></time><span data-js-debug-hover-source-separator aria-hidden="true" hidden> · </span><span data-js-debug-hover-source hidden></span></div>'}
  </section>`;
}

function debugGraphUsesLogScale(group, seriesItems) {
  const candidates = (seriesItems || []).flatMap(series => series.plotValues || series.values || []);
  const values = candidates.map(Number).filter(value => Number.isFinite(value) && value > 0);
  if (!values.length) return false;
  const max = Math.max(...values);
  if (group?.key === 'latency') return max > 1000;
  return false;
}

function debugGraphTokenAxisDescriptor(buckets) {
  const values = (buckets || []).map(bucket => {
    let total = 0;
    for (const rate of bucket?.agentTokenRates?.values?.() || []) {
      if (jsDebugGraphModelTokenDimension !== 'output' && rate?.billableAvailable !== true) continue;
      total += debugGraphAgentTokenBucketDimensionValue(bucket, rate);
    }
    // Model input/cache can legitimately exceed generated output. The shared
    // descriptor must include the selected Model chart while remaining one
    // exact axis for both token charts.
    return Math.max(total, debugGraphSelectedModelTokenBucketValue(bucket));
  }).filter(value => Number.isFinite(value) && value > 0);
  const rawMax = Math.max(0, ...values);
  const group = jsDebugGraphChartGroups.find(item => item.key === 'agentTokens');
  const axisMax = debugGraphChartAxisMax(group || {unit: 'tokensPerMinute'}, rawMax);
  const sorted = [...values].sort((left, right) => left - right);
  const normalMax = sorted.length >= 8 ? sorted[Math.floor((sorted.length - 1) * 0.9)] : rawMax;
  const threshold = debugGraphChartAxisMax(group || {unit: 'tokensPerMinute'}, normalMax);
  const peakCount = sorted.filter(value => value > threshold).length;
  const broken = sorted.length >= 8
    && peakCount > 0
    && peakCount <= Math.max(1, Math.ceil(sorted.length * 0.1))
    && rawMax >= Math.max(threshold * 2.5, normalMax * 3)
    && threshold < axisMax;
  return Object.freeze({
    axisMax,
    scale: broken ? Object.freeze({mode: 'broken-linear', threshold, upperFraction: 0.18}) : false,
  });
}

function debugGraphChartLabel(group, buckets = []) {
  const label = debugGraphLocalizedLabel(group);
  const detailKey = group?.key === 'cpu' ? 'cpuLabel' : group?.key === 'memory' ? 'systemMemoryLabel' : '';
  if (!detailKey) return label;
  const detail = buckets.map(bucket => String(bucket?.hostMetrics?.[detailKey] || '').trim()).find(Boolean);
  return detail ? `${label} (${detail})` : label;
}

function debugGraphChartShellHtml(gridHtml = '', domain = debugGraphDomain()) {
  return `<div class="js-debug-chart-shell">
    <div class="js-debug-chart-grid" data-js-debug-chart-grid data-js-debug-chart-layout="${esc(jsDebugGraphChartLayout)}" data-js-debug-domain-start="${esc(Math.floor(domain.startMs))}" data-js-debug-domain-end="${esc(Math.floor(domain.endMs))}"${domain.zoomed ? ' data-js-debug-zoomed="true"' : ''}>${gridHtml}</div>
    ${debugGraphHistoryOverlayHtml()}
  </div>`;
}

function debugGraphCostText(key, fallback, params = {}) {
  const translated = t(key, params);
  return translated === key ? fallback : translated;
}

function debugGraphCostMicroUsd(item) {
  return debugGraphCostInteger(item?.micro_usd ?? item?.total_micro_usd ?? item?.cost_micro_usd);
}

function debugGraphCostUsdText(microUsd) {
  const value = debugGraphCostInteger(microUsd);
  if (value === 0) return '$0.00';
  const usd = value / 1000000;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(6)}`;
}

function debugGraphCostRangeUsdText(summary) {
  const lower = debugGraphCostInteger(summary?.lowerMicroUsd ?? summary?.knownMicroUsd);
  const upper = Math.max(lower, debugGraphCostInteger(summary?.upperMicroUsd ?? summary?.totalMicroUsd ?? summary?.knownMicroUsd));
  if (lower === upper) return debugGraphCostUsdText(lower);
  return `${debugGraphCostUsdText(lower)} – ${debugGraphCostUsdText(upper)}`;
}

function debugGraphCostKind(item) {
  return String(item?.key || item?.kind || item?.direction || item?.label || '').toLowerCase();
}

function debugGraphCostClass(item) {
  const unit = String(item?.unit || 'tokens').toLowerCase();
  const modality = String(item?.modality || 'text').toLowerCase();
  if (unit !== 'tokens' || modality !== 'text') return 'other';
  const cacheRole = String(item?.cache_role || '').toLowerCase();
  if (['read', 'write', 'write_5m', 'write_1h'].includes(cacheRole)) return 'cache';
  const direction = String(item?.direction || debugGraphCostKind(item)).toLowerCase();
  if (direction.includes('input') || direction.includes('uncached')) return 'input';
  if (direction.includes('output')) return 'output';
  return 'other';
}

// One description owner for the visible usage columns. The wording states exactly what
// debugGraphCostClass implements (a mutually exclusive projection: Cached bundles cache
// READS and WRITES and is separated from Input, so nothing is double-counted), not
// assumed provider semantics. Rendered via the shared explain-attrs (title + aria) owner.
function debugGraphCostUsageColumnDescription(key) {
  const descriptions = {
    input: ['debug.cost.input.desc', 'Newly processed prompt/context tokens, counted after cache reads and writes are separated into Cached. Reused cached context is never double-counted here.'],
    cache: ['debug.cost.cached.desc', 'Cumulative prompt-cache token accounting across requests: cache READS (hits/refreshes) and cache WRITES (5m/1h creation) combined. Reused history/tool/system context is counted again on every request, so in long conversations Cached can legitimately dwarf Input. This is billing accounting, not stored cache size or GPU cache occupancy.'],
    output: ['debug.cost.output.desc', 'Model-generated tokens, including provider-reported reasoning and tool-call output.'],
    other: ['debug.cost.other.desc', 'Retained usage that fits none of Input / Cached / Output, such as non-text or non-token units. Non-token image, audio, request, and tool units can add cost in Cost calculation without being added to token totals.'],
    total: ['debug.cost.total.desc', 'The reconciliation of the four columns: Input + Cached + Output + Other. The projection is mutually exclusive, so each token is counted in exactly one column and the sum is not double-counted.'],
  };
  const entry = descriptions[key];
  return entry ? debugGraphCostText(entry[0], entry[1]) : '';
}

function debugGraphCostUsageColumnHeaderAttrs(key, label) {
  return debugGraphExplainAttrs(label, `debug.cost.${key === 'cache' ? 'cached' : key}.desc`, {attribute: 'data-js-debug-cost-column-desc', desc: debugGraphCostUsageColumnDescription(key)});
}

function debugGraphCostCompactTotals(summary) {
  const totals = {input: 0, cache: 0, output: 0};
  for (const item of summary.components) {
    const value = debugGraphCostMicroUsd(item);
    const itemClass = debugGraphCostClass(item);
    if (itemClass === 'input') totals.input += value;
    else if (itemClass === 'cache') totals.cache += value;
    else totals.output += value;
  }
  return totals;
}

function debugGraphCostTokenTotals(summary) {
  const totals = {input: 0, cache: 0, output: 0, other: 0, total: 0};
  for (const item of summary.components) {
    if (String(item?.unit || 'tokens').toLowerCase() !== 'tokens') continue;
    const quantity = Math.max(0, Number(item?.quantity) || 0);
    const itemClass = debugGraphCostClass(item);
    totals[itemClass] += quantity;
    totals.total += quantity;
  }
  return totals;
}

const DEBUG_GRAPH_COST_SUBTOTAL_FIELDS = Object.freeze(['micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'output_micro_usd', 'other_micro_usd', 'input_lower_micro_usd', 'cache_lower_micro_usd', 'output_lower_micro_usd', 'other_lower_micro_usd', 'input_upper_micro_usd', 'cache_upper_micro_usd', 'output_upper_micro_usd', 'other_upper_micro_usd']);
const DEBUG_GRAPH_COST_TOKEN_FIELDS = Object.freeze(['quantity', 'token_quantity', 'unpriced_token_quantity', 'input_tokens', 'cache_tokens', 'output_tokens', 'other_tokens']);
const DEBUG_GRAPH_COST_COMPONENT_KEY_FIELDS = Object.freeze(['key', 'kind', 'provider', 'model', 'effort', 'pricing_profile', 'service_tier', 'direction', 'modality', 'cache_role', 'unit', 'catalog_revision', 'source_url', 'effective_from', 'rate_usd', 'rate_scale']);
const DEBUG_GRAPH_COST_MODEL_KEY_FIELDS = Object.freeze(['provider', 'model', 'effort']);
const DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS = Object.freeze(['tmux_key', 'tmux_label', 'tmux_session', 'tmux_window', 'tmux_window_label', 'agent_kind', 'root_thread_id', 'agent_thread_id', 'parent_thread_id', 'endpoint', 'tool_name', 'source']);
const DEBUG_GRAPH_COST_TMUX_KEY_FIELDS = Object.freeze(['tmux_key', 'tmux_label', 'tmux_session', 'tmux_window', 'tmux_window_label', 'agent_kind']);
let jsDebugCostSummaryCache = {signature: '', summary: null};

function debugGraphCostAggregateRowInto(grouped, row, keyFields) {
  if (!row || typeof row !== 'object') return;
  let key = '';
  for (let index = 0; index < keyFields.length; index += 1) {
    if (index) key += '\u0000';
    key += String(row[keyFields[index]] || '');
  }
  key ||= 'unknown';
  const current = grouped.get(key) || {...row};
  if (!grouped.has(key)) {
    for (const field of DEBUG_GRAPH_COST_SUBTOTAL_FIELDS) current[field] = 0;
    for (const field of DEBUG_GRAPH_COST_TOKEN_FIELDS) current[field] = 0;
    grouped.set(key, current);
  }
  for (const field of DEBUG_GRAPH_COST_SUBTOTAL_FIELDS) current[field] += debugGraphCostInteger(row?.[field]);
  for (const field of DEBUG_GRAPH_COST_TOKEN_FIELDS) current[field] += Math.max(0, Number(row?.[field]) || 0);
}

function debugGraphCostAggregateValues(grouped) {
  return [...grouped.values()].sort((left, right) => debugGraphCostMicroUsd(right) - debugGraphCostMicroUsd(left)
    || String(left?.model || left?.label || left?.key || '').localeCompare(String(right?.model || right?.label || right?.key || '')));
}

function debugGraphCostAggregateRows(rows, keyFields) {
  const grouped = new Map();
  for (const row of rows || []) {
    debugGraphCostAggregateRowInto(grouped, row, keyFields);
  }
  return debugGraphCostAggregateValues(grouped);
}

function debugGraphCostSummarySignature(buckets) {
  if (!Array.isArray(buckets) || !buckets.length) {
    return `0:${jsDebugStatsServerSequence}:${jsDebugUsageAtomBackfill.state || ''}:${jsDebugUsageAtomBackfill.sources || 0}:${jsDebugUsageAtomBackfill.missing || 0}`;
  }
  const first = buckets[0] || {};
  const last = buckets[buckets.length - 1] || {};
  return [
    buckets.length,
    Number(first.startMs ?? first.start ?? 0) || 0,
    Number(first.durationMs ?? first.duration ?? 0) || 0,
    Number(first.sequence ?? 0) || 0,
    Number(last.startMs ?? last.start ?? 0) || 0,
    Number(last.durationMs ?? last.duration ?? 0) || 0,
    Number(last.sequence ?? 0) || 0,
    jsDebugStatsServerSequence,
    jsDebugUsageAtomBackfill.state || '',
    jsDebugUsageAtomBackfill.sources || 0,
    jsDebugUsageAtomBackfill.missing || 0,
  ].join(':');
}

function debugGraphCostSummaryForBuckets(buckets) {
  const signature = debugGraphCostSummarySignature(buckets);
  if (signature && jsDebugCostSummaryCache.signature === signature && jsDebugCostSummaryCache.summary) return jsDebugCostSummaryCache.summary;
  const summaries = (buckets || []).map(bucket => bucket?.costSummary).filter(Boolean);
  const componentRows = new Map();
  const modelRows = new Map();
  const sourceRows = new Map();
  const tmuxRows = new Map();
  const result = {
    totalMicroUsd: 0, knownMicroUsd: 0, lowerMicroUsd: 0, upperMicroUsd: 0, pricedCount: 0, complete: summaries.length > 0,
    unpricedCount: 0, unpricedTokenQuantity: 0, components: [], models: [], sources: [], tmuxWindows: [], catalogRevision: '', activeCatalogRevision: '', freshness: '',
    backfill: {...jsDebugUsageAtomBackfill},
  };
  for (const summary of summaries) {
    result.totalMicroUsd += debugGraphCostInteger(summary.totalMicroUsd);
    result.knownMicroUsd += debugGraphCostInteger(summary.knownMicroUsd);
    result.lowerMicroUsd += debugGraphCostInteger(summary.lowerMicroUsd ?? summary.knownMicroUsd);
    result.upperMicroUsd += debugGraphCostInteger(summary.upperMicroUsd ?? summary.totalMicroUsd ?? summary.knownMicroUsd);
    result.pricedCount += debugGraphCostInteger(summary.pricedCount);
    result.complete = result.complete && summary.complete === true;
    result.unpricedCount += debugGraphCostInteger(summary.unpricedCount);
    result.unpricedTokenQuantity += Math.max(0, Number(summary.unpricedTokenQuantity) || 0);
    for (const row of debugGraphCostRows(summary.components)) debugGraphCostAggregateRowInto(componentRows, row, DEBUG_GRAPH_COST_COMPONENT_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.models)) debugGraphCostAggregateRowInto(modelRows, row, DEBUG_GRAPH_COST_MODEL_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.sources)) debugGraphCostAggregateRowInto(sourceRows, row, DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.tmuxWindows)) debugGraphCostAggregateRowInto(tmuxRows, row, DEBUG_GRAPH_COST_TMUX_KEY_FIELDS);
    result.catalogRevision = summary.catalogRevision || result.catalogRevision;
    result.activeCatalogRevision = summary.activeCatalogRevision || result.activeCatalogRevision;
    result.freshness = summary.freshness || result.freshness;
  }
  // Effective price/source evidence is part of a billable component identity:
  // retaining it prevents a displayed-range reprice boundary from being
  // misleadingly collapsed into one synthetic rate row.
  result.components = debugGraphCostAggregateValues(componentRows);
  result.models = debugGraphCostAggregateValues(modelRows);
  result.sources = debugGraphCostAggregateValues(sourceRows);
  result.tmuxWindows = debugGraphCostAggregateValues(tmuxRows);
  if (result.backfill.state !== 'complete') result.complete = false;
  jsDebugCostSummaryCache = {signature, summary: result};
  return result;
}

function debugGraphCostRangeText(domain) {
  const start = debugGraphExactTimeLabel(domain.startMs);
  const end = debugGraphExactTimeLabel(domain.endMs);
  const seconds = Math.max(0, Math.round((Number(domain.endMs) - Number(domain.startMs)) / 1000));
  return `${start} – ${end} · ${debugGraphCostText('debug.cost.duration', `${seconds}s`, {seconds})}`;
}

function debugGraphCostModelLabel(row) {
  const label = String(row?.label || row?.model || row?.source || row?.agent || row?.key || 'unknown');
  const effort = String(row?.effort || '').trim();
  return effort ? `${label} · ${effort}` : label;
}

function debugGraphCostModelAgentKind(row) {
  const identity = [row?.provider, row?.model, row?.label].map(value => String(value || '').toLowerCase()).join(' ');
  if (identity.includes('anthropic') || identity.includes('claude')) return 'claude';
  if (identity.includes('openai') || identity.includes('gpt') || identity.includes('codex')) return 'codex';
  return '';
}

function debugGraphCostModelIdentityHtml(row, {showProvider = false, secondaryHtml = ''} = {}) {
  const model = String(row?.model || row?.label || row?.source || row?.agent || row?.key || 'unknown');
  const effort = String(row?.effort || '').trim();
  const provider = String(row?.provider || '').trim();
  const meta = [showProvider ? provider : '', effort].filter(Boolean).join(' · ');
  const kind = debugGraphCostModelAgentKind(row);
  const icon = kind ? `<span class="js-debug-cost-model-icon" aria-hidden="true">${agentIcon(kind)}</span>` : '';
  const secondary = meta || secondaryHtml ? `<span class="js-debug-cost-model-meta">${meta ? `<small>${esc(meta)}</small>` : ''}${secondaryHtml}</span>` : '';
  return `<span class="js-debug-cost-model-identity">${icon}<span class="js-debug-cost-model-copy"><strong>${esc(model)}</strong>${secondary}</span></span>`;
}

function debugGraphCostUsageTokensText(tokens) {
  const value = Math.max(0, Number(tokens) || 0);
  return value > 0 ? debugGraphTokensText(value) : '0';
}

function debugGraphCostUsageUsdText(microUsd, tokens = 1) {
  const value = debugGraphCostInteger(microUsd);
  if (value > 0) return debugGraphCostUsdText(value);
  if (Math.max(0, Number(tokens) || 0) <= 0) return '$0';
  return '$0';
}

function debugGraphCostBreakdownItems(row) {
  return [
    ['input', debugGraphCostText('debug.cost.input', 'Input')],
    ['cache', debugGraphCostText('debug.cost.cache', 'Cache')],
    ['output', debugGraphCostText('debug.cost.output', 'Output')],
    ['other', debugGraphCostText('debug.cost.other', 'Other')],
  ].map(([key, label]) => ({
    key,
    label,
    tokens: Math.max(0, Number(row?.[`${key}_tokens`]) || 0),
    microUsd: debugGraphCostInteger(row?.[`${key}_micro_usd`]),
  }));
}

function debugGraphCostPricingSourceEntries(components, modelRow = null) {
  const provider = String(modelRow?.provider || '').trim();
  const model = String(modelRow?.model || '').trim();
  const links = new Map();
  for (const row of components || []) {
    if (provider && String(row?.provider || '').trim() !== provider) continue;
    if (model && String(row?.model || '').trim() !== model) continue;
    const url = normalizedExternalHttpUrl(row?.source_url, {maxLength: 2048});
    if (!url || links.has(url)) continue;
    const sourceLabel = [row?.provider, row?.model].map(value => String(value || '').trim()).filter(Boolean).join(' · ')
      || debugGraphCostText('debug.cost.source', 'Pricing source');
    links.set(url, sourceLabel);
  }
  return [...links].map(([url, label]) => ({url, label}));
}

function debugGraphCostPricingLinksHtml(components, modelRow = null, {compact = false} = {}) {
  const links = debugGraphCostPricingSourceEntries(components, modelRow);
  if (!links.length) return '';
  return `<span class="js-debug-cost-pricing-links">${links.map(({url, label}) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"${compact ? ` aria-label="${esc(`${label} pricing`)}"` : ''}>${esc(compact ? debugGraphCostText('debug.cost.pricing', 'pricing') : label)}</a>`).join(' · ')}</span>`;
}

function debugGraphCostAllPricingSourcesHtml(components) {
  const links = debugGraphCostPricingSourceEntries(components);
  if (!links.length) return '';
  return `<section class="js-debug-cost-details-section js-debug-cost-pricing-sources">
    <h2>${esc(debugGraphCostText('debug.cost.pricingSources', 'Pricing sources'))}</h2>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="pricing-sources"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.source', 'Pricing source'))}</th><th scope="col">URL</th></tr></thead><tbody>${links.map(({url, label}) => `<tr><th scope="row">${esc(label)}</th><td><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a></td></tr>`).join('')}</tbody></table></div>
  </section>`;
}

function debugGraphCostUsageTableCellHtml(tokens, microUsd, {total = false, row = null} = {}) {
  const hasRange = row && (debugGraphCostInteger(row?.lower_micro_usd) > 0 || debugGraphCostInteger(row?.upper_micro_usd) > 0);
  const cost = total && hasRange ? debugGraphCostRowRangeUsdText(row) : debugGraphCostUsageUsdText(microUsd, tokens);
  const exactTokens = `${Math.max(0, Number(tokens) || 0).toLocaleString()} tokens`;
  return `<span class="js-debug-cost-table-metric" title="${esc(exactTokens)}"><strong>${esc(debugGraphTokenNumberText(tokens))}</strong><small>${esc(cost)}</small></span>`;
}

function debugGraphCostUsageTableHtml(rows, {kind, heading, labelHeading, labelFor, components = []} = {}) {
  if (!rows.length) return '';
  const usageKeys = ['input', 'cache', 'output', 'other'];
  const usageLabels = {
    input: debugGraphCostText('debug.cost.input', 'Input'),
    cache: debugGraphCostText('debug.cost.cached', 'Cached'),
    output: debugGraphCostText('debug.cost.output', 'Output'),
    other: debugGraphCostText('debug.cost.other', 'Other'),
  };
  const totalRow = debugGraphCostAggregateRows(rows, [])[0] || {};
  const rowHtml = row => {
    const breakdown = debugGraphCostBreakdownItems(row);
    const totalTokens = Math.max(0, Number(row?.token_quantity) || 0);
    const pricingLinks = kind === 'model' ? debugGraphCostPricingLinksHtml(components, row, {compact: true}) : '';
    const accessible = `${labelFor(row)}: ${debugGraphCostText('debug.cost.total', 'Total')} ${debugGraphCostUsageTokensText(totalTokens)} ${debugGraphCostUsageUsdText(debugGraphCostMicroUsd(row), totalTokens)}; ${breakdown.map(item => `${usageLabels[item.key]} ${debugGraphCostUsageTokensText(item.tokens)} ${debugGraphCostUsageUsdText(item.microUsd, item.tokens)}`).join('; ')}`;
    const identity = kind === 'model' ? debugGraphCostModelIdentityHtml(row, {secondaryHtml: pricingLinks}) : `<strong>${esc(labelFor(row))}</strong>`;
    return `<tr aria-label="${esc(accessible)}"><th scope="row">${identity}</th>${breakdown.map(item => `<td data-label="${esc(usageLabels[item.key])}">${debugGraphCostUsageTableCellHtml(item.tokens, item.microUsd)}</td>`).join('')}<td data-label="${esc(debugGraphCostText('debug.cost.total', 'Total'))}">${debugGraphCostUsageTableCellHtml(totalTokens, debugGraphCostMicroUsd(row), {total: true, row})}</td></tr>`;
  };
  const totalBreakdown = debugGraphCostBreakdownItems(totalRow);
  const totalTokens = Math.max(0, Number(totalRow?.token_quantity) || 0);
  return `<section class="js-debug-cost-${esc(kind)}-usages js-debug-cost-details-section js-debug-cost-usage-table-section"><h2>${esc(heading)}</h2><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="${esc(kind)}"><thead><tr><th scope="col">${esc(labelHeading)}</th>${usageKeys.map(key => `<th scope="col"${debugGraphCostUsageColumnHeaderAttrs(key, usageLabels[key])}><i class="js-debug-cost-usage-swatch js-debug-cost-usage-swatch--${esc(key)}" aria-hidden="true"></i>${esc(usageLabels[key])}</th>`).join('')}<th scope="col"${debugGraphCostUsageColumnHeaderAttrs('total', debugGraphCostText('debug.cost.total', 'Total'))}>${esc(debugGraphCostText('debug.cost.total', 'Total'))}</th></tr></thead><tbody>${rows.map(rowHtml).join('')}</tbody><tfoot><tr><th scope="row">${esc(debugGraphCostText('debug.cost.grandTotal', 'Grand total'))}</th>${totalBreakdown.map(item => `<td data-label="${esc(usageLabels[item.key])}">${debugGraphCostUsageTableCellHtml(item.tokens, item.microUsd)}</td>`).join('')}<td data-label="${esc(debugGraphCostText('debug.cost.total', 'Total'))}">${debugGraphCostUsageTableCellHtml(totalTokens, debugGraphCostMicroUsd(totalRow), {total: true, row: totalRow})}</td></tr></tfoot></table></div></section>`;
}

function debugGraphCostModelUsageChartHtml(rows, components, options = {}) {
  if (options.report !== true) return '';
  return debugGraphCostUsageTableHtml(rows, {
    kind: 'model',
    heading: debugGraphCostText('debug.cost.modelUsages', 'Model Usages'),
    labelHeading: debugGraphCostText('debug.cost.model', 'Model'),
    labelFor: debugGraphCostModelLabel,
    components,
  });
}

function debugGraphCostComponentLabel(row) {
  const parts = [row?.provider, row?.model, row?.pricing_profile !== 'default' ? row?.pricing_profile : '', row?.service_tier !== 'default' ? row?.service_tier : '', row?.direction, row?.cache_role, row?.modality, row?.unit]
    .map(value => String(value || '').trim())
    .filter(Boolean);
  return parts.join(' · ') || String(row?.label || row?.key || 'unknown');
}

function debugGraphCostComponentRateText(row) {
  const exactRate = String(row?.rate_usd || '').trim();
  const scale = Math.max(0, Number(row?.rate_scale) || 0);
  if (exactRate && scale > 0) return `$${exactRate}/${scale.toLocaleString()} ${String(row?.unit || 'unit')}`;
  const quantity = Number(row?.quantity);
  const microUsd = debugGraphCostMicroUsd(row);
  if (!Number.isFinite(quantity) || quantity <= 0 || microUsd <= 0) return '—';
  return `${debugGraphCostUsdText(Math.round((microUsd * 1000000) / quantity))}/${String(row?.unit || 'unit')}`;
}

function debugGraphCostComponentFormulaText(row) {
  const quantity = Number(row?.quantity);
  const rate = debugGraphCostComponentRateText(row);
  if (!Number.isFinite(quantity) || quantity <= 0 || rate === '—') return '—';
  return `${quantity.toLocaleString()} ${String(row?.unit || 'unit')} × ${rate}`;
}

function debugGraphCostComponentSortValue(row, key) {
  if (key === 'tokens') return Math.max(0, Number(row?.quantity) || 0);
  if (key === 'rate') {
    const rate = Number(row?.rate_usd);
    const scale = Math.max(1, Number(row?.rate_scale) || 1);
    return Number.isFinite(rate) ? rate / scale : -1;
  }
  if (key === 'cost') return debugGraphCostMicroUsd(row);
  if (key === 'effective') {
    const timestamp = Date.parse(String(row?.effective_from || ''));
    return Number.isFinite(timestamp) ? timestamp : 0;
  }
  if (key === 'cache') return String(row?.cache_role || '');
  if (key === 'source') return String(row?.source_url || '');
  return String(row?.[key] || '');
}

function debugGraphCostSortedComponentRows(rows) {
  const {key, direction} = jsDebugCostComponentSortState;
  const multiplier = direction === 'asc' ? 1 : -1;
  return [...rows].sort((left, right) => {
    const leftValue = debugGraphCostComponentSortValue(left, key);
    const rightValue = debugGraphCostComponentSortValue(right, key);
    const comparison = typeof leftValue === 'number' && typeof rightValue === 'number'
      ? leftValue - rightValue
      : String(leftValue).localeCompare(String(rightValue));
    return (comparison * multiplier) || debugGraphCostComponentLabel(left).localeCompare(debugGraphCostComponentLabel(right));
  });
}

function debugGraphCostComponentSortHeaderHtml(key, label) {
  const active = jsDebugCostComponentSortState.key === key;
  const ariaSort = active ? (jsDebugCostComponentSortState.direction === 'asc' ? 'ascending' : 'descending') : 'none';
  const nextDirection = active && jsDebugCostComponentSortState.direction === 'asc' ? 'descending' : 'ascending';
  return `<th scope="col" aria-sort="${ariaSort}"><button type="button" class="js-debug-cost-sort control-active-hover" data-js-debug-cost-sort="${esc(key)}" data-js-debug-cost-next-sort="${nextDirection}">${esc(label)}</button></th>`;
}

// Render a usage class with the provider's own conventions (Anthropic's price
// sheet: Base input, 5m/1h cache write, Cache hits & refreshes, Output) instead
// of the raw `direction · cache_role`. Anything not covered (other modalities/
// providers) falls back to the raw joined form so nothing is mislabeled.
function debugGraphCostUsageClassLabel(direction, cacheRole) {
  const dir = String(direction || '').trim().toLowerCase();
  const role = String(cacheRole || '').trim().toLowerCase();
  if (dir === 'output') return debugGraphCostText('debug.cost.class.output', 'Output');
  if (dir === 'input' || dir === '') {
    if (role === 'read') return debugGraphCostText('debug.cost.class.cacheHits', 'Cache hits & refreshes');
    if (role === 'write_5m') return debugGraphCostText('debug.cost.class.cacheWrite5m', '5m cache write');
    if (role === 'write_1h') return debugGraphCostText('debug.cost.class.cacheWrite1h', '1h cache write');
    if (role.startsWith('write')) return debugGraphCostText('debug.cost.class.cacheWrite', 'Cache write');
    if (role === 'none' || role === '') return debugGraphCostText('debug.cost.class.baseInput', 'Base input');
  }
  return [direction, cacheRole].map(value => String(value || '').trim()).filter(Boolean).join(' · ') || '—';
}

function debugGraphCostComponentDetailsHtml(rows) {
  if (!rows.length) return '';
  const headers = [
    ['provider', 'Provider'], ['model', 'Model'], ['direction', debugGraphCostText('debug.cost.usageClass', 'Usage class')],
    ['tokens', debugGraphCostText('debug.modelTokens.label', 'Tokens')], ['cost', debugGraphCostText('debug.cost.rateAndCost', 'Rate / cost')],
    ['effective', debugGraphCostText('debug.cost.pricing', 'Pricing')],
  ];
  return `<section class="js-debug-cost-details-section">
    <h2>${esc(debugGraphCostText('debug.cost.byTokenClass', 'Cost calculation'))}</h2>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table js-debug-cost-component-table" data-js-debug-cost-table="calculation"><thead><tr>${headers.map(([key, label]) => debugGraphCostComponentSortHeaderHtml(key, label)).join('')}</tr></thead><tbody>${debugGraphCostSortedComponentRows(rows).map(row => {
      const url = normalizedExternalHttpUrl(row?.source_url, {maxLength: 2048});
      const usageClass = debugGraphCostUsageClassLabel(row?.direction, row?.cache_role);
      const usageMeta = [row?.modality, row?.unit].map(value => String(value || '').trim()).filter(Boolean).join(' · ');
      const exactQuantity = `${Math.max(0, Number(row?.quantity) || 0).toLocaleString()} ${String(row?.unit || 'units')}`;
      return `<tr><td data-label="Provider">${esc(String(row?.provider || '—'))}</td><td data-label="Model">${debugGraphCostModelIdentityHtml(row)}</td><td data-label="Usage class"><span class="js-debug-cost-stacked-cell"><strong>${esc(usageClass)}</strong>${usageMeta ? `<small>${esc(usageMeta)}</small>` : ''}</span></td><td data-label="Tokens" title="${esc(exactQuantity)}">${esc(debugGraphTokenNumberText(row?.quantity))}</td><td data-label="Rate / cost"><span class="js-debug-cost-stacked-cell"><strong>${esc(debugGraphCostUsdText(debugGraphCostMicroUsd(row)))}</strong><small>${esc(debugGraphCostComponentRateText(row))}</small></span></td><td data-label="Pricing"><span class="js-debug-cost-stacked-cell"><small>${esc(String(row?.effective_from || '—'))}</small>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(debugGraphCostText('debug.cost.source', 'Pricing source'))}</a>` : '—'}</span></td></tr>`;
    }).join('')}</tbody></table></div>
  </section>`;
}

function debugGraphCostSourceLabel(row) {
  const root = String(row?.root_thread_id || '').trim();
  const agent = String(row?.agent_thread_id || '').trim();
  const tool = String(row?.tool_name || '').trim();
  const source = String(row?.source || '').trim();
  return [source, agent && agent !== root ? agent : '', tool].filter(Boolean).join(' · ') || root || debugGraphCostText('debug.cost.unknown', 'Unknown');
}

function debugGraphCostRowRangeUsdText(row) {
  const lower = debugGraphCostInteger(row?.lower_micro_usd ?? row?.micro_usd);
  const upper = Math.max(lower, debugGraphCostInteger(row?.upper_micro_usd ?? row?.micro_usd));
  if (lower === upper) return debugGraphCostUsdText(lower);
  return `${debugGraphCostUsdText(lower)} – ${debugGraphCostUsdText(upper)}`;
}

function debugGraphCostSubtotalText(row) {
  const parts = [
    ['input_micro_usd', debugGraphCostText('debug.cost.input', 'Input')],
    ['cache_micro_usd', debugGraphCostText('debug.cost.cache', 'Cache')],
    ['output_micro_usd', debugGraphCostText('debug.cost.output', 'Output')],
    ['other_micro_usd', debugGraphCostText('debug.cost.other', 'Other')],
  ];
  return `${debugGraphTokensText(row?.token_quantity)} · ${parts.map(([key, label]) => `${label} ${debugGraphCostUsdText(debugGraphCostInteger(row?.[key]))}`).join(' · ')} · ${debugGraphCostText('debug.cost.total', 'Total')} ${debugGraphCostRowRangeUsdText(row)}`;
}

function debugGraphCostTmuxLabel(row) {
  const explicit = String(row?.tmux_label || '').trim();
  if (explicit) return explicit;
  const session = String(row?.tmux_session || '').trim();
  const windowLabel = String(row?.tmux_window_label || row?.tmux_window || '').trim();
  const kind = String(row?.agent_kind || '').trim();
  return [session, windowLabel || kind].filter(Boolean).join(':') || debugGraphCostSourceLabel(row);
}

function debugGraphCostTmuxBreakdownRows(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    const key = String(row?.tmux_key || row?.root_thread_id || row?.source || debugGraphCostTmuxLabel(row)).trim() || 'unknown';
    const current = grouped.get(key) || {
      ...row,
      token_quantity: 0,
      micro_usd: 0,
      lower_micro_usd: 0,
      upper_micro_usd: 0,
      input_micro_usd: 0,
      cache_micro_usd: 0,
      output_micro_usd: 0,
      other_micro_usd: 0,
      input_tokens: 0,
      cache_tokens: 0,
      output_tokens: 0,
      other_tokens: 0,
    };
    current.token_quantity += Math.max(0, Number(row?.token_quantity) || 0);
    for (const field of ['micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'output_micro_usd', 'other_micro_usd']) {
      current[field] += debugGraphCostInteger(row?.[field]);
    }
    for (const field of ['input_tokens', 'cache_tokens', 'output_tokens', 'other_tokens']) {
      current[field] += Math.max(0, Number(row?.[field]) || 0);
    }
    grouped.set(key, current);
  }
  return [...grouped.values()].sort((left, right) => debugGraphCostInteger(right?.upper_micro_usd ?? right?.micro_usd) - debugGraphCostInteger(left?.upper_micro_usd ?? left?.micro_usd)
    || debugGraphCostTmuxLabel(left).localeCompare(debugGraphCostTmuxLabel(right)));
}

function debugGraphCostTmuxBreakdownHtml(summary) {
  const directRows = debugGraphCostRows(summary?.tmuxWindows);
  const rows = directRows.length ? directRows : debugGraphCostTmuxBreakdownRows(summary.sources);
  if (!rows.length) return '';
  return debugGraphCostUsageTableHtml(rows, {
    kind: 'agent',
    heading: debugGraphCostText('debug.cost.byAgent', 'By Agent'),
    labelHeading: t('yoagent.action.row.agent'),
    labelFor: debugGraphCostTmuxLabel,
  });
}

function debugGraphCostTranscriptPath(row) {
  const path = String(row?.transcript || '').trim();
  if (!path.startsWith('/') || !/\.(?:jsonl|ndjson)$/i.test(path) || /[\u0000-\u001f\u007f]/.test(path)) return '';
  const segments = path.split('/');
  if (segments.slice(1).some(segment => !segment || segment === '.' || segment === '..')) return '';
  return path;
}

function debugGraphMiddleTruncatedTextHtml(value, tailLength = 20) {
  const text = String(value || '');
  const characters = Array.from(text);
  const tailSize = Math.max(1, Math.min(characters.length - 1, Math.floor(Number(tailLength) || 20)));
  if (characters.length <= tailSize + 1) return `<span class="js-debug-responsive-text">${esc(text)}</span>`;
  const split = characters.length - tailSize;
  return `<span class="js-debug-responsive-text js-debug-responsive-text--middle"><span class="js-debug-responsive-text-prefix" data-middle-truncate-part="prefix">${esc(characters.slice(0, split).join(''))}</span><span class="js-debug-responsive-text-suffix" data-middle-truncate-part="suffix">${esc(characters.slice(split).join(''))}</span></span>`;
}

function debugGraphCostSourceLabelHtml(row) {
  const label = debugGraphCostSourceLabel(row);
  const transcript = debugGraphCostTranscriptPath(row);
  if (!transcript) return esc(label);
  return `<a href="#" class="js-debug-cost-transcript-link" data-js-debug-cost-transcript-path="${esc(transcript)}" title="${esc(transcript)}" aria-label="${esc(label)}">${debugGraphMiddleTruncatedTextHtml(label)}</a>`;
}

function debugGraphCostSourceTreeHtml(rows) {
  if (!rows.length) return '';
  return `<section class="js-debug-cost-details-section">
    <h2>${esc(debugGraphCostText('debug.cost.bySource', 'Agent and source attribution'))}</h2>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="source"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.source', 'Source'))}</th><th scope="col">${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}</th><th scope="col">${esc(debugGraphCostText('debug.cost.total', 'Total'))}</th></tr></thead><tbody>${rows.map(row => `<tr><th scope="row">${debugGraphCostSourceLabelHtml(row)}</th><td>${esc(debugGraphTokensText(row?.token_quantity))}</td><td>${esc(debugGraphCostSubtotalText(row))}</td></tr>`).join('')}</tbody></table></div>
  </section>`;
}

function debugGraphCostCatalogDetailsHtml(summary) {
  // One compact catalog-status line replacing the four-row table: every fact
  // (revision, freshness, priced coverage, unpriced exclusions) stays present
  // and localized, wraps as meaningful field groups on a narrow pane, and
  // never stretches four scalars across a wide screen. Unpriced exclusions
  // keep their warning semantics when nonzero.
  const revision = String(summary.activeCatalogRevision || summary.catalogRevision || '').trim() || '—';
  const freshness = String(summary.freshness || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
  const exclusions = Math.max(0, Number(summary.unpricedCount) || 0);
  const priced = Math.max(0, Number(summary.pricedCount) || 0);
  const groups = [
    {label: debugGraphCostText('debug.cost.catalog', 'Catalog'), value: `${debugGraphCostText('debug.cost.rev', 'rev')} ${revision}`},
    {label: debugGraphCostText('debug.cost.freshnessCompact', 'freshness'), value: freshness.toLowerCase() === freshness ? freshness : freshness.charAt(0).toLowerCase() + freshness.slice(1)},
    {label: debugGraphCostText('debug.cost.coverageCompact', 'coverage'), value: `${priced}/${priced + exclusions}`},
    {label: debugGraphCostText('debug.cost.unpricedCompact', 'unpriced'), value: String(exclusions), warning: exclusions > 0},
  ];
  const accessible = [
    `${debugGraphCostText('debug.cost.catalogRevision', 'Catalog revision')}: ${revision}`,
    `${debugGraphCostText('debug.cost.freshness', 'Catalog freshness')}: ${freshness}`,
    `${debugGraphCostText('debug.cost.coverage', 'Priced coverage')}: ${priced}/${priced + exclusions}`,
    `${debugGraphCostText('debug.cost.exclusions', 'Unpriced exclusions')}: ${exclusions}`,
  ].join('; ');
  return `<p class="js-debug-cost-catalog-line" data-js-debug-cost-catalog aria-label="${esc(accessible)}">${groups.map((group, index) => `<span class="js-debug-cost-catalog-group${group.warning ? ' js-debug-cost-catalog-group--warning' : ''}">${index === 0 ? `${esc(group.label)}: ` : ''}${index > 0 ? `${esc(group.label)} ` : ''}${esc(group.value)}</span>`).join('<span class="js-debug-cost-catalog-separator" aria-hidden="true"> · </span>')}</p>`;
}

function debugGraphCostBackfillText(summary) {
  const state = String(summary?.backfill?.state || 'pending');
  if (state === 'complete') return '';
  if (state === 'partial') return debugGraphCostText('debug.cost.backfillPartial', 'Backfill incomplete');
  if (state === 'running') return debugGraphCostText('debug.cost.backfillRunning', 'Backfill in progress');
  return debugGraphCostText('debug.cost.backfillPending', 'Backfill pending');
}

function debugGraphCostUnpricedUsage(summary) {
  const rows = debugGraphCostRows(summary?.components).filter(row => row?.priced === false || Math.max(0, Number(row?.unpriced_count) || 0) > 0);
  const classesByKey = new Map();
  for (const row of rows) {
    const provider = String(row?.provider || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
    const model = String(row?.model || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
    const itemClass = debugGraphCostClass(row);
    const key = `${provider}\u0000${model}\u0000${itemClass}`;
    const current = classesByKey.get(key) || {provider, model, itemClass, tokenQuantity: 0};
    current.tokenQuantity += Math.max(0, Number(row?.unpriced_token_quantity) || (row?.priced === false ? Number(row?.token_quantity ?? row?.quantity) || 0 : 0));
    classesByKey.set(key, current);
  }
  const classes = [...classesByKey.values()];
  const rowsTokenQuantity = rows.reduce((total, row) => total + Math.max(0, Number(row?.unpriced_token_quantity) || (row?.priced === false ? Number(row?.token_quantity ?? row?.quantity) || 0 : 0)), 0);
  const tokenQuantity = Math.max(0, Number(summary?.unpricedTokenQuantity) || rowsTokenQuantity);
  const knownMicroUsd = debugGraphCostInteger(summary?.knownMicroUsd);
  const upperMicroUsd = Math.max(knownMicroUsd, debugGraphCostInteger(summary?.upperMicroUsd));
  return {tokenQuantity, worstCaseMicroUsd: upperMicroUsd - knownMicroUsd, classes};
}

function debugGraphCostUnknownUsageHtml(summary) {
  if (Math.max(0, Number(summary?.unpricedCount) || 0) === 0) return '';
  const usage = debugGraphCostUnpricedUsage(summary);
  const rows = [
    [debugGraphCostText('debug.cost.knownTotal', 'Known priced total'), debugGraphCostUsdText(summary?.knownMicroUsd)],
    [debugGraphCostText('debug.cost.unpricedTokens', 'Unpriced tokens'), debugGraphTokensText(usage.tokenQuantity)],
    [debugGraphCostText('debug.cost.worstCase', 'Worst-case estimate'), debugGraphCostUsdText(usage.worstCaseMicroUsd)],
  ];
  const classesLabel = debugGraphCostText('debug.cost.unpricedModels', 'Unpriced model/classes');
  const classRows = usage.classes.map(item => {
    const label = `${item.provider} · ${item.model} · ${item.itemClass}`;
    return `<tr data-js-debug-unpriced-class><th scope="row">${esc(label)}</th><td>${esc(debugGraphTokensText(item.tokenQuantity))}</td></tr>`;
  }).join('');
  const disclosure = usage.classes.length ? `<details class="js-debug-cost-unpriced-disclosure"><summary aria-label="${esc(`${classesLabel}: ${usage.classes.length}`)}"><span>${esc(classesLabel)}</span><strong>${usage.classes.length}</strong></summary><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="unpriced-classes"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.modelClass', 'Provider · model · class'))}</th><th scope="col">${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}</th></tr></thead><tbody>${classRows}</tbody></table></div></details>` : '';
  return `<section class="js-debug-cost-details-section js-debug-cost-unknown-usage"><h2>${esc(debugGraphCostText('debug.cost.unpricedUsage', 'Unpriced usage'))}</h2><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="unpriced"><tbody>${rows.map(([label, value]) => `<tr><th scope="row">${esc(label)}</th><td>${esc(value)}</td></tr>`).join('')}</tbody></table></div>${disclosure}</section>`;
}

function debugGraphCostReportHtml(summary, domain) {
  const hasEstimatedUsage = summary.pricedCount > 0 || summary.unpricedCount > 0 || summary.upperMicroUsd > 0;
  const exact = hasEstimatedUsage && summary.complete === true && summary.unpricedCount === 0 && debugGraphCostInteger(summary.lowerMicroUsd) === debugGraphCostInteger(summary.upperMicroUsd);
  const hasFiniteRange = debugGraphCostInteger(summary.upperMicroUsd) > debugGraphCostInteger(summary.lowerMicroUsd);
  const total = hasEstimatedUsage ? debugGraphCostRangeUsdText(summary) : '—';
  const tokens = debugGraphCostTokenTotals(summary);
  const title = debugGraphCostText('debug.cost.details', 'Cost summary details');
  // Compact report shell: one heading line carrying the range, one totals line
  // replacing the old Summary heading + nested list, one catalog status line
  // replacing the four-row catalog table. Exact values stay reachable through
  // the accessible labels; nothing about estimate/lower-bound semantics changes.
  const estimateSentence = !hasEstimatedUsage
    ? debugGraphCostText('debug.cost.waiting', 'Waiting for priced usage')
    : (exact
      ? debugGraphCostText('debug.cost.exact', `Estimated API list-price total ${total}`, {amount: total})
      : hasFiniteRange
        ? debugGraphCostText('debug.cost.range', `Estimated API list-price range ${total}`, {amount: total})
        : debugGraphCostText('debug.cost.lowerBound', `Known estimated lower bound ${total}`, {amount: total}));
  const tokenParts = [
    `${debugGraphCostText('debug.cost.input', 'Input').toLowerCase()}=${debugGraphTokenNumberText(tokens.input)}`,
    `${debugGraphCostText('debug.cost.cache', 'Cache').toLowerCase()}=${debugGraphTokenNumberText(tokens.cache)}`,
    `${debugGraphCostText('debug.cost.output', 'Output').toLowerCase()}=${debugGraphTokenNumberText(tokens.output)}`,
    ...(Math.max(0, Number(tokens.other) || 0) > 0 ? [`${debugGraphCostText('debug.cost.other', 'Other').toLowerCase()}=${debugGraphTokenNumberText(tokens.other)}`] : []),
  ];
  const totalsLine = `${estimateSentence}, ${debugGraphCostText('debug.cost.totalTokens', 'total tokens')}: ${debugGraphTokensText(tokens.total)} (${tokenParts.join(', ')})`;
  const totalsExact = `${debugGraphCostText('debug.cost.totalTokens', 'total tokens')}: ${Math.max(0, Number(tokens.total) || 0).toLocaleString()}; ${['input', 'cache', 'output', 'other'].map(key => `${key}=${Math.max(0, Number(tokens[key]) || 0).toLocaleString()}`).join('; ')}`;
  return `<article class="js-debug-cost-report" aria-label="${esc(title)}">
    <div class="js-debug-cost-report-title">
      <h1>${esc(title)}</h1><span class="js-debug-cost-report-range meta-muted">${esc(debugGraphCostRangeText(domain))}</span>
    </div>
    <div class="js-debug-cost-report-body">
      <p class="js-debug-cost-report-totals" data-js-debug-cost-report-totals aria-label="${esc(`${estimateSentence}; ${totalsExact}`)}">${esc(totalsLine)}</p>
      ${debugGraphCostUnknownUsageHtml(summary)}
      ${debugGraphCostCatalogDetailsHtml(summary)}
      ${debugGraphCostTmuxBreakdownHtml(summary)}
      ${debugGraphCostModelUsageChartHtml(summary.models, summary.components, {report: true})}
      ${debugGraphCostComponentDetailsHtml(summary.components)}
      ${debugGraphCostSourceTreeHtml(summary.sources)}
      ${debugGraphCostAllPricingSourcesHtml(summary.components)}
    </div>
  </article>`;
}

function debugGraphCostSummaryHtml(buckets, domain) {
  const summary = debugGraphCostSummaryForBuckets(buckets);
  const hasEstimatedUsage = summary.pricedCount > 0 || summary.unpricedCount > 0 || summary.upperMicroUsd > 0;
  const exact = hasEstimatedUsage && summary.complete === true && summary.unpricedCount === 0 && debugGraphCostInteger(summary.lowerMicroUsd) === debugGraphCostInteger(summary.upperMicroUsd);
  const hasFiniteRange = debugGraphCostInteger(summary.upperMicroUsd) > debugGraphCostInteger(summary.lowerMicroUsd);
  const estimated = hasEstimatedUsage ? debugGraphCostRangeUsdText(summary) : '—';
  const compact = debugGraphCostCompactTotals(summary);
  const tokens = debugGraphCostTokenTotals(summary);
  const heading = hasEstimatedUsage ? `${exact || hasFiniteRange ? 'est. ' : 'est. ≥'}${estimated}, Σ displayed` : 'est. —, Σ displayed';
  const accessible = !hasEstimatedUsage
    ? 'No displayed usage has a selected price'
    : exact
    ? `Estimated API list-price total ${estimated} across displayed usage; open calculation and pricing sources`
    : `Estimated API list-price range ${estimated}; unknown or incomplete displayed usage widens the range`;
  const refreshLabel = debugGraphCostText('common.refresh', 'Refresh');
  const refreshHtml = readOnlyMode ? '' : `<button type="button" class="js-debug-cost-refresh control-active-hover" data-js-debug-cost-refresh aria-label="${esc(refreshLabel)}" title="${esc(jsDebugPricingRefreshState.error || refreshLabel)}"${jsDebugPricingRefreshState.inFlight ? ' disabled aria-busy="true"' : ''}>${esc(jsDebugPricingRefreshState.inFlight ? `${refreshLabel}…` : refreshLabel)}</button>`;
  const refreshStatus = jsDebugPricingRefreshState.error || (jsDebugPricingRefreshState.inFlight ? (jsDebugPricingRefreshState.status || `${refreshLabel}…`) : '');
  const backfillStatus = debugGraphCostBackfillText(summary);
  const moreInfo = debugGraphCostText('debug.cost.moreInfo', 'More Info');
  const compactRows = [
    ['Input', compact.input === null ? null : debugGraphCostUsdText(compact.input), tokens.input],
    ['Cache', compact.cache === null ? null : debugGraphCostUsdText(compact.cache), tokens.cache],
    ['Output', compact.output === null ? null : debugGraphCostUsdText(compact.output), tokens.output],
    ['Total', hasEstimatedUsage ? estimated : null, tokens.total],
  ];
  return `<section class="js-debug-chart js-debug-cost-summary" data-js-debug-summary-group="costSummary">
    <div class="js-debug-chart-head">
      <div class="js-debug-chart-heading-row">
        <span class="js-debug-chart-title">${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}</span>
        <span class="js-debug-chart-summary js-debug-cost-estimate">(${esc(heading)})</span>
        ${refreshHtml}
        <button type="button" class="js-debug-chart-close control-active-hover" data-js-debug-chart-close="costSummary" aria-label="${esc(t('common.close'))} ${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}" title="${esc(t('common.close'))}">×</button>
      </div>
      <div class="js-debug-cost-range">${esc(debugGraphCostRangeText(domain))}</div>
      ${refreshStatus ? `<div class="js-debug-cost-refresh-status" role="status">${esc(refreshStatus)}</div>` : ''}
      ${backfillStatus ? `<div class="js-debug-cost-refresh-status" role="status">${esc(backfillStatus)}</div>` : ''}
    </div>
    <dl class="js-debug-cost-compact" aria-label="${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}">
      ${compactRows.map(([label, value, tokenCount]) => `<div><dt${debugGraphCostUsageColumnHeaderAttrs(String(label).toLowerCase(), debugGraphCostText(`debug.cost.${String(label).toLowerCase()}`, label))}>${esc(debugGraphCostText(`debug.cost.${String(label).toLowerCase()}`, label))}</dt><dd>${esc(value === null ? '—' : value)}<span class="js-debug-cost-token-count">${esc(debugGraphTokensText(tokenCount))}</span></dd></div>`).join('')}
    </dl>
    <span class="js-debug-cost-modal-host"><button type="button" class="js-debug-cost-details control-active-hover" data-js-debug-cost-details aria-label="${esc(accessible)}">${esc(moreInfo)}</button></span>
  </section>`;
}

async function refreshDebugCostPricing() {
  if (readOnlyMode || jsDebugPricingRefreshState.inFlight) return;
  jsDebugPricingRefreshState.inFlight = true;
  jsDebugPricingRefreshState.error = '';
  jsDebugPricingRefreshState.lastRequestedAtMs = Date.now();
  refreshDebugGraphSurfaces();
  try {
    const payload = await apiFetchJson('/api/pricing-catalog/refresh', {method: 'POST'});
    jsDebugPricingRefreshState.status = String(payload?.status || 'running');
    if (jsDebugPricingRefreshState.status === 'running') {
      scheduleDebugCostPricingStatusRefresh();
    } else {
      jsDebugPricingRefreshState.inFlight = false;
    }
  } catch (error) {
    jsDebugPricingRefreshState.inFlight = false;
    jsDebugPricingRefreshState.error = userMessageText(error, t('common.requestFailed'));
  } finally {
    refreshDebugGraphSurfaces();
  }
}

function scheduleDebugCostPricingStatusRefresh() {
  if (jsDebugPricingRefreshState.timer !== null) clearTimeout(jsDebugPricingRefreshState.timer);
  jsDebugPricingRefreshState.timer = setTimeout(() => {
    jsDebugPricingRefreshState.timer = null;
    void refreshDebugCostPricingStatus();
  }, 750);
}

async function refreshDebugCostPricingStatus() {
  try {
    const payload = await apiFetchJson('/api/pricing-catalog', {cache: 'no-store'});
    const refresh = payload?.refresh && typeof payload.refresh === 'object' ? payload.refresh : {};
    const status = String(refresh.status || 'idle');
    jsDebugPricingRefreshState.status = status;
    jsDebugPricingRefreshState.error = status === 'failed' ? String(refresh.error || t('common.requestFailed')) : '';
    jsDebugPricingRefreshState.inFlight = status === 'running';
    if (jsDebugPricingRefreshState.inFlight) scheduleDebugCostPricingStatusRefresh();
  } catch (error) {
    jsDebugPricingRefreshState.inFlight = false;
    jsDebugPricingRefreshState.error = userMessageText(error, t('common.requestFailed'));
  }
  refreshDebugGraphSurfaces();
}

function debugGraphSvgHtml(buckets, seriesItems, chartGroups = debugGraphVisibleChartGroups(seriesItems), nowMs = Date.now(), {includeCostSummary = true} = {}) {
  const domain = debugGraphDomain(nowMs);
  const overlayBuckets = debugGraphSourceBuckets(domain);
  const disconnectedRanges = debugGraphDisconnectedRanges(overlayBuckets, domain);
  const tokenBuckets = debugGraphAgentTokenDisplayBuckets(nowMs);
  const tokenAxis = debugGraphTokenAxisDescriptor(tokenBuckets);
  const visibleGroupKeys = new Set(chartGroups.map(group => group.key));
  const gridHtml = jsDebugGraphChartGroups.flatMap(group => {
      const groupBuckets = debugGraphBucketsForChartGroup(group, buckets, nowMs);
      const groupSeriesItems = groupBuckets === buckets ? seriesItems : debugGraphSeriesData(groupBuckets);
      const items = visibleGroupKeys.has(group.key)
        ? [debugGraphChartHtml(group, groupSeriesItems, domain, groupBuckets, overlayBuckets, disconnectedRanges, {tokenAxis})]
        : [];
      // This is deliberately a non-chart sibling: it consumes precisely the Model tokens/min
      // displayed bucket array from the unified cache, but adds no axes, bars, or
      // independent range state.
      if (includeCostSummary && group.key === 'modelTokens' && debugGraphChartVisible('costSummary')) {
        items.push(debugGraphCostSummaryHtml(groupBuckets, domain));
      }
      return items;
    }).join('');
  return debugGraphChartShellHtml(gridHtml, domain);
}

function debugGraphClassName(nowMs = Date.now()) {
  return `js-debug-graph${debugGraphDisplayBuckets(nowMs).length ? '' : ' js-debug-graph--empty'}${debugGraphZoomDomainValid() ? ' js-debug-graph--zoomed' : ''}`;
}

function debugGraphBodyHtml(nowMs = Date.now()) {
  loadJsDebugStatsUiPreferences();
  activeJsDebugGraphRangeSeconds(nowMs);
  const meta = debugGraphMetaHtml();
  const clientPerf = debugClientPerfHtml();
  const buckets = debugGraphDisplayBuckets(nowMs);
  if (!buckets.length) {
    const empty = debugGraphWaitingForServerStats() ? '' : `<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
    const loadingShell = jsDebugHistoryReadiness.overlayVisible === true || jsDebugHistoryReadinessBusy()
      ? debugGraphChartShellHtml('', debugGraphDomain(nowMs))
      : '';
    return `${clientPerf}${empty}${loadingShell}${meta}`;
  }
  const seriesItems = debugGraphSeriesData(buckets);
  const chartGroups = debugGraphVisibleChartGroups(seriesItems);
  return `${clientPerf}${debugGraphSvgHtml(buckets, seriesItems, chartGroups, nowMs)}${meta}`;
}

function debugGraphInnerHtml(nowMs = Date.now()) {
  return `${debugGraphControlsHtml(nowMs)}<div data-js-debug-graph-body>${debugGraphBodyHtml(nowMs)}</div>`;
}

function debugGraphHtml() {
  const nowMs = Date.now();
  return `<div class="${debugGraphClassName(nowMs)}" data-js-debug-graph data-js-debug-graph-rendered-at="${esc(nowMs)}" data-js-debug-history-state="${esc(jsDebugHistoryReadinessStateName())}" aria-busy="${jsDebugHistoryReadinessBusy() ? 'true' : 'false'}" aria-label="${esc(t('debug.summary'))}">${debugGraphInnerHtml(nowMs)}</div>`;
}

function debugGraphBucketSummary(nowMs = Date.now()) {
  activeJsDebugGraphRangeSeconds(nowMs);
  const domain = debugGraphDomain(nowMs, jsDebugGraphRangeSeconds);
  const buckets = debugGraphDisplayBuckets(nowMs, {rangeSeconds: jsDebugGraphRangeSeconds});
  const availableRangeSeconds = debugGraphAvailableRangeOptions(nowMs).map(option => option.seconds);
  // rawBuckets/rollupBuckets survive as derived diagnostics of the ONE bucket Map:
  // "raw" is the finest (sub-middle-tier) durations, "rollup" everything coarser.
  const cachedBuckets = [...jsDebugGraphBuckets.values()];
  return {
    rawBuckets: cachedBuckets.filter(bucket => bucket.durationMs < jsDebugGraphMiddleBucketMs).length,
    rollupBuckets: cachedBuckets.filter(bucket => bucket.durationMs >= jsDebugGraphMiddleBucketMs).length,
    middleBuckets: cachedBuckets.filter(bucket => bucket.durationMs === jsDebugGraphMiddleBucketMs).length,
    oldBuckets: cachedBuckets.filter(bucket => bucket.durationMs === jsDebugGraphRollupBucketMs).length,
    tierBucketCounts: jsDebugGraphTiers.map(tier => cachedBuckets.filter(bucket => bucket.durationMs === tier.bucketMs).length),
    displayBucketSeconds: [...new Set(buckets.map(bucket => bucket.durationMs / 1000))].sort((left, right) => left - right),
    agentTokenDisplayFloorSeconds: Math.max(jsDebugGraphAgentTokenBucketSeconds, debugGraphAgentTokenResolution(nowMs)),
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
    && (itemIsActivePaneTab(debugPaneItemId) || itemIsActivePaneTab(yocostItemId));
}

function jsDebugStatsLayoutItemsVisible(items) {
  return Array.isArray(items) && (items.includes(debugPaneItemId) || items.includes(yocostItemId));
}

function jsDebugStatsTokenConsumerEnabled() {
  return jsDebugStatsPanelVisible();
}

function stopJsDebugStatsPolling() {
  clearRuntimeInterval('debug-stats');
}

function jsDebugStatsLivePushEnabled() {
  // A drag zoom is a fixed historical domain. The shared range slider is the
  // live-tail owner for both YO!stats and YO!cost; only its 5m/15m views need
  // every durable one-second push.
  return !debugGraphZoomDomainValid() && jsDebugGraphRangeSeconds < jsDebugStatsLivePushRangeSeconds;
}

function jsDebugStatsPollIntervalMs() {
  if (!jsDebugStatsPollState.firstSampleReceived) return jsDebugStatsPollFastMs;
  return jsDebugStatsLivePushEnabled() ? jsDebugStatsPollMs : jsDebugStatsCoarsePollMs;
}

function syncJsDebugStatsDeliveryMode() {
  if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
  // Re-entering a short live range can follow almost a minute without stats
  // SSE. Fetch once now so the existing history merger fills that delivery
  // gap before subsequent one-second pushes arrive.
  armJsDebugStatsPolling({pollNow: jsDebugStatsLivePushEnabled(), forceGraphRefresh: true});
}

function armJsDebugStatsPolling({pollNow = false, forceGraphRefresh = false} = {}) {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  stopJsDebugStatsPolling();
  if (pollNow) void pollJsDebugStatsSample({forceGraphRefresh});
  resetRuntimeInterval('debug-stats', pollJsDebugStatsOnInterval, jsDebugStatsPollIntervalMs());
}

function pollJsDebugStatsOnInterval() {
  // Passive cadence ticks never need to queue behind an explicit range,
  // activation, or initial request. The next full interval is soon enough and
  // avoids a slow request degenerating into an immediate back-to-back fetch.
  maybePrefetchJsDebugHistory();
  if (jsDebugStatsPollState.inFlight) return;
  return pollJsDebugStatsSample();
}

// Fire the full-retention prefetch once shortly after the current range lands, then on a
// slow cadence. Visibility is enforced by the poll loop itself (it stops when hidden) plus
// the guard inside prefetchJsDebugHistoryFullRetention, so a hidden panel does zero work.
function maybePrefetchJsDebugHistory() {
  if (!jsDebugStatsPollState.firstSampleReceived) return;
  if (jsDebugHistoryPrefetchState.inFlight) return;
  const nowMs = performanceNow();
  const due = !jsDebugHistoryPrefetchState.didInitial
    || (nowMs - Number(jsDebugHistoryPrefetchState.lastFullPrefetchAtMs || 0)) >= jsDebugHistoryPrefetchIntervalMs;
  if (!due) return;
  jsDebugHistoryPrefetchState.didInitial = true;
  void prefetchJsDebugHistoryFullRetention();
}

// Silent cache-fill of the whole retention window. Populates ONLY the shared bucket Map
// (jsDebugGraphBuckets) so a later range switch renders
// cached content instantly. Deliberately does NOT touch jsDebugHistoryReadiness, the
// overlay, coverage, or the live cursor: the current view owns loading state, and the
// normal poll revalidates the switched-to range's fresh tail on top of this cache.
// Finest-source-wins at render keeps the live 1s/10s tail intact (no replaceCoverage,
// so no fine buckets are removed).
async function prefetchJsDebugHistoryFullRetention() {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible()) return false;
  if (jsDebugHistoryPrefetchState.inFlight) return false;
  if (typeof apiFetchJsonQuiet !== 'function') return false;
  jsDebugHistoryPrefetchState.inFlight = true;
  const requestGeneration = jsDebugHistoryPrefetchState.generation;
  try {
    const nowSeconds = Math.floor(Date.now() / 1000);
    const historyStart = Math.max(0, nowSeconds - jsDebugHistoryPrefetchRetentionSeconds);
    const clientId = jsDebugStatsClientIdForRequest();
    const payload = await fetchJsDebugStatsJson(
      jsDebugStatsSampleQuery({clientId, historyStart, historyEnd: 0, historyResolution: 1}),
      {cache: 'no-store', timeoutMs: jsDebugStatsHistoryTimeoutMs(jsDebugHistoryPrefetchRetentionSeconds)},
    );
    // The cache was cleared (range reset / history clear) while this fetch was in flight:
    // dropping the stale response keeps the rendered history deterministic.
    if (jsDebugHistoryPrefetchState.generation !== requestGeneration) return false;
    const coverage = normalizedJsDebugHistoryCoverage(payload?.history);
    // A malformed/omitted coverage list only aborts THIS silent fill; it never blanks the
    // visible chart (unlike the readiness path, which must reject malformed coverage).
    if (!coverage) return false;
    debugGraphApplyServerHistory(payload.history, {advanceLiveCursor: false});
    jsDebugHistoryPrefetchState.lastFullPrefetchAtMs = performanceNow();
    return true;
  } catch (error) {
    recordJsDebugStatsDiagnostic('info', `history prefetch skipped: ${jsDebugErrorText(error)}`);
    return false;
  } finally {
    jsDebugHistoryPrefetchState.inFlight = false;
  }
}

// THE one owner of the /api/stats-sample request shape. Every runtime fetch, test
// fixture, and diagnostic probe must build its query through this function or its
// contract-tested python mirror (tests/browser_helpers/stats_request_shapes.py):
// the 2026-07-14 host-metrics outage escaped because a diagnosis probe hand-rolled
// a request that validated the wrong serve path. The shared goldens live in
// tests/fixtures/stats_request_shapes.json. There are NO token_* params anymore:
// token rates and cost ride every history record of the one history stream (the
// server still accepts the legacy params from old clients; this client never
// sends them).
function jsDebugStatsSampleQuery(params = {}) {
  const {
    since = 0,
    clientId = '',
    tokenConsumer = '0',
    historyStart = 0,
    historyEnd = 0,
    historyResolution = 1,
    historyMaxPoints = jsDebugStatsHistoryMaxPoints,
    history = true,
    exactResolution = false,
  } = params;
  const parts = [
    `since=${encodeURIComponent(String(since))}`,
    `client_id=${encodeURIComponent(String(clientId))}`,
    `token_consumer=${encodeURIComponent(String(tokenConsumer))}`,
    `history_start=${encodeURIComponent(String(historyStart))}`,
    `history_end=${encodeURIComponent(String(historyEnd))}`,
    `history_resolution=${encodeURIComponent(String(historyResolution))}`,
    `history_max_points=${encodeURIComponent(String(historyMaxPoints))}`,
  ];
  // Opt-in exact-resolution serve (DOIT.1 cutover). Additive: omitted by default,
  // so the request shape and its goldens are unchanged until the renderer flips.
  if (exactResolution) parts.push('exact_resolution=1');
  if (!history) parts.push('history=0');
  return `/api/stats-sample?${parts.join('&')}`;
}

function jsDebugStatsHistoryTimeoutMs(rangeSeconds = 0) {
  const rangeHoursBeyondFirst = Math.max(0, Math.ceil(Math.max(0, Number(rangeSeconds) || 0) / 3600) - 1);
  return Math.min(jsDebugStatsHistoryMaxTimeoutMs, jsDebugStatsPollTimeoutMs + (rangeHoursBeyondFirst * 1000));
}

function jsDebugStatsTimeoutError(timeoutMs) {
  const error = new Error(`history request timed out after ${Math.round(timeoutMs / 1000)}s`);
  error.name = 'TimeoutError';
  return error;
}

async function fetchJsDebugStatsJson(url, options = {}) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const phaseTimings = {};
  const timeoutMs = Math.max(1, Number(options.timeoutMs) || jsDebugStatsPollTimeoutMs);
  const requestOptions = {...options};
  delete requestOptions.timeoutMs;
  let timeoutId = null;
  let timeoutError = null;
  try {
    if (controller && typeof setTimeout === 'function') {
      timeoutId = setTimeout(() => {
        timeoutError = jsDebugStatsTimeoutError(timeoutMs);
        controller.abort(timeoutError);
      }, timeoutMs);
    }
    return await apiFetchJsonQuiet(url, {...requestOptions, ...(controller ? {signal: controller.signal} : {})}, phaseTimings);
  } catch (error) {
    if (controller?.signal?.aborted && timeoutError) throw timeoutError;
    throw error;
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
  const recoveredRetry = jsDebugHistoryReadiness.reason === 'retry';
  setJsDebugHistoryReadiness('ready', {
    requestedRangeSeconds,
    requestedStartSeconds,
    error: '',
    nextAutoRetryAtMs: 0,
  });
  if (recoveredRetry) recordJsDebugStatsDiagnostic('info', 'retry exited after durable history coverage recovered');
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
  let historyRequestSuppressed = false;
  try {
    const clientId = jsDebugStatsClientIdForRequest();
    const tokenConsumer = jsDebugStatsTokenConsumerEnabled() ? '1' : '0';
    const domain = debugGraphDomain();
    const targetStart = Math.max(0, Math.floor(domain.startMs / 1000));
    const targetEnd = Math.max(targetStart + 1, Math.ceil(domain.endMs / 1000));
    const historyResolution = jsDebugRequestedHistoryResolutionSeconds();
    const coverageResolution = jsDebugHistoryCoverageResolutionSeconds(targetStart, historyResolution);
    const needsHistoryCoverage = jsDebugHistoryCoverageNeedsRefresh(targetStart, targetEnd, coverageResolution);
    if (needsHistoryCoverage) {
      const state = jsDebugHistoryReadiness;
      if (jsDebugHistoryReadinessErrorLike(state) && !jsDebugHistoryAutoRetryDue(state)) {
        historyRequestSuppressed = true;
      } else {
        const currentRequestMatches = jsDebugHistoryReadinessBusy(state)
          && Number(state.requestedRangeSeconds) === Number(jsDebugGraphRangeSeconds)
          && Number(state.targetStartSeconds) === Number(targetStart)
          && Number(state.targetEndSeconds) === Number(targetEnd)
          && Number(state.requestedResolutionSeconds) === Number(coverageResolution);
        if (!currentRequestMatches || state.reason === 'retry') {
          const requestWindow = jsDebugHistoryRequestWindow(targetStart, targetEnd, coverageResolution);
          beginJsDebugHistoryReadiness(requestWindow.startSeconds, {
            targetStartSeconds: targetStart,
            targetEndSeconds: targetEnd,
            requestedEndSeconds: requestWindow.endSeconds,
            requestedResolutionSeconds: coverageResolution,
            retry: jsDebugHistoryReadinessErrorLike(state),
          });
        }
        readinessRequest = jsDebugHistoryReadinessSnapshot();
        await nextAnimationFrame();
        if (!jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) return;
      }
    }
    const historyEnd = readinessRequest
      ? Math.max(0, Math.floor(Number(readinessRequest.requestedEndSeconds) || 0))
      : 0;
    const historyStart = readinessRequest ? readinessRequest.requestedStartSeconds : (historyRequestSuppressed ? 0 : targetStart);
    const payload = await fetchJsDebugStatsJson(jsDebugStatsSampleQuery({
      since: readinessRequest ? 0 : (jsDebugStatsServerSequence || 0),
      clientId,
      tokenConsumer,
      historyStart,
      historyEnd,
      historyResolution,
      history: !historyRequestSuppressed,
      exactResolution: jsDebugGraphExactResolutionEnabled,
    }), {
      cache: 'no-store',
      timeoutMs: jsDebugStatsHistoryTimeoutMs(readinessRequest?.requestedRangeSeconds || jsDebugGraphRangeSeconds),
    });
    if (readinessRequest && !jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) return;
    const pendingCoverage = normalizedJsDebugHistoryPending(payload?.history);
    if (readinessRequest && pendingCoverage) {
      recordJsDebugStatsDiagnostic('info', `history backfill pending: ${pendingCoverage.reason}`);
      setJsDebugHistoryReadiness('loading', {
        reason: 'retry',
        error: pendingCoverage.reason,
        nextAutoRetryAtMs: performanceNow() + pendingCoverage.retryAfterMs,
      });
      return;
    }
    const coverage = normalizedJsDebugHistoryCoverage(payload?.history);
    if (readinessRequest && !coverage) {
      recordJsDebugStatsDiagnostic('error', 'coverage rejected: response omitted or malformed the required interval list');
      throw new Error('stats history response has malformed coverage intervals');
    }
    if (readinessRequest) recordJsDebugHistoryCoverageDiagnostic(coverage, readinessRequest);
    if (coverage?.mode === 'older' && Number(coverage.sourceResolutionSeconds || 0) >= 60) {
      recordJsDebugStatsDiagnostic('info', `history range served from ${coverage.sourceResolutionSeconds}s retained rollup`);
    }
    const replaceCoverage = coverage?.intervals.filter(interval => (
      jsDebugHistoryCoverageResolutionForRange(interval.startSeconds, interval.endSeconds) > interval.resolutionSeconds
    )) || null;
    const applyStartedAt = performanceNow();
    recordJsDebugStatsSample(payload, {
      forceGraphRefresh: forceGraphRefresh || needsHistoryCoverage,
      scheduleRefresh: !readinessRequest,
      advanceHistoryCursor: coverage?.mode !== 'older',
      replaceCoverage,
    });
    if (coverage) applyJsDebugHistoryCoverage(coverage, readinessRequest);
    recordClientPerfCounter('statsHistoryApply', performanceNow() - applyStartedAt);
    if (readinessRequest) {
      if (coverage.hasMoreOlder && Number.isFinite(coverage.nextOlderEnd) && coverage.nextOlderEnd > readinessRequest.targetStartSeconds) {
        jsDebugStatsPollState.pending = true;
        jsDebugStatsPollState.pendingForceGraphRefresh = true;
        return;
      }
      await paintJsDebugHistoryResponse(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds);
    }
  } catch (error) {
    if (readinessRequest && jsDebugHistoryRequestIsCurrent(readinessRequest.generation, readinessRequest.requestedRangeSeconds, readinessRequest.requestedStartSeconds)) {
      const elapsedMs = Math.max(0, performanceNow() - Number(readinessRequest.loadingStartedAtMs || performanceNow()));
      const level = error?.name === 'TimeoutError' ? 'error' : 'warning';
      const reason = error?.name === 'TimeoutError'
        ? `history request timed out after ${Math.round(elapsedMs)}ms`
        : `history request failed: ${jsDebugErrorText(error)}`;
      recordJsDebugStatsDiagnostic(level, reason);
      setJsDebugHistoryReadiness('error', {
        error: jsDebugErrorText(error),
        nextAutoRetryAtMs: performanceNow() + jsDebugHistoryRetryDelayMs(readinessRequest.attemptCount),
      });
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

function jsDebugStatsHistoryUploadRequest(records, clientId, since) {
  let low = 1;
  let high = Math.min(records.length, jsDebugStatsHistoryPostMaxRecords);
  let chunkSize = 1;
  let body = '';
  while (low <= high) {
    const candidateSize = Math.floor((low + high) / 2);
    const candidateBody = JSON.stringify({client_id: clientId, since, ack_only: true, records: records.slice(0, candidateSize)});
    if (utf8ByteLength(candidateBody) <= jsDebugStatsHistoryPostMaxBytes) {
      chunkSize = candidateSize;
      body = candidateBody;
      low = candidateSize + 1;
    } else {
      high = candidateSize - 1;
    }
  }
  const chunk = records.slice(0, chunkSize);
  return {
    chunk,
    held: records.slice(chunkSize),
    body: body || JSON.stringify({client_id: clientId, since, ack_only: true, records: chunk}),
  };
}

async function flushJsDebugStatsHistory() {
  if (!jsDebugCollectionEnabled || !jsDebugGraphPendingServerBuckets.size || typeof apiFetchJsonQuiet !== 'function') return;
  if (jsDebugStatsUploadState.worker) return jsDebugStatsUploadState.worker;
  const records = [...jsDebugGraphPendingServerBuckets.values()]
    .map(record => ({...record}))
    .filter(record => record.api_count || record.sse_count || record.latency_count || record.bandwidth_bytes || record.disconnected_ms || record.cpu_count || record.system_cpu_count)
    .sort((a, b) => (Number(a.start) - Number(b.start)) || (Number(a.duration) - Number(b.duration)));
  if (!records.length) return;
  const clientId = jsDebugStatsClientIdForRequest();
  const since = jsDebugStatsServerSequence || 0;
  const {chunk, held, body} = jsDebugStatsHistoryUploadRequest(records, clientId, since);
  for (const record of chunk) {
    const key = `${Math.floor(Number(record.start) * 1000)}:${Math.floor(Number(record.duration) * 1000)}`;
    jsDebugGraphPendingServerBuckets.delete(key);
  }
  const generation = jsDebugStatsUploadState.generation;
  let worker = null;
  worker = (async () => {
    try {
      await apiFetchJsonQuiet('/api/stats-history', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body,
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
  jsDebugHistoryPrefetchState.didInitial = false;
  jsDebugHistoryPrefetchState.lastFullPrefetchAtMs = 0;
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

function startJsDebugStatsPolling({pollNow = true} = {}) {
  startJsDebugClientHealthPolling();
  syncJsDebugStatsPolling({pollNow});
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

async function initializeJsDebugStatsBeforeStreams() {
  startJsDebugClientHealthPolling();
  await primeJsDebugStatsBeforeLongLivedStreams();
  syncJsDebugStatsPolling({pollNow: false});
  return jsDebugStatsPollState.firstSampleReceived;
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('visibilitychange', () => {
    const visible = document.visibilityState === 'visible';
    syncJsDebugStatsPolling({pollNow: visible, forceGraphRefresh: visible});
    syncDebugSystemPolling({pollNow: visible});
    syncDebugLogsPolling({pollNow: visible});
    if (visible) syncDebugGraphLiveTicker();
    else stopDebugGraphLiveTicker();
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

function debugSystemNumber(value, digits = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, {maximumFractionDigits: digits}) : t('common.notAvailable');
}

function debugSystemRowsHtml(rows = []) {
  return `<dl class="js-debug-system-kv">${rows.map(([label, value]) => `
    <div><dt>${esc(label)}</dt><dd>${esc(value == null || value === '' ? t('common.notAvailable') : value)}</dd></div>`).join('')}</dl>`;
}

function debugSystemCardHtml(title, body, options = {}) {
  return `<section class="js-debug-system-card${options.wide ? ' js-debug-system-card--wide' : ''}">
    <h3>${esc(title)}</h3>${body}
  </section>`;
}

function debugSystemServiceState(service = {}) {
  const pid = Number(service.pid || 0);
  if (pid <= 0) {
    if (Number(service.restart_backoff_seconds || 0) > 0) return {label: t('debug.system.localServices.state.issue'), tone: 'bad'};
    return {label: t('state.idle'), tone: 'muted'};
  }
  if (service.healthy === false) return {label: t('debug.system.localServices.state.issue'), tone: 'bad'};
  return {label: t('debug.system.localServices.state.running'), tone: 'good'};
}

const DEBUG_SYSTEM_SERVICE_FRESH_MS = 60_000;
const debugSystemLocalServicesState = {records: new Map(), signature: ''};
const debugSystemLocalServiceFields = Object.freeze([
  {key: 'status', labelKey: 'debug.system.localServices.field.status'},
  {key: 'pid', labelKey: 'debug.system.localServices.field.pid'},
  {key: 'started', labelKey: 'debug.system.localServices.field.started'},
  {key: 'lastRan', labelKey: 'debug.system.localServices.field.lastRan'},
  {key: 'uptime', labelKey: 'debug.system.localServices.field.uptime'},
  {key: 'cpu', labelKey: 'debug.graph.chart.cpu'},
  {key: 'memory', labelKey: 'debug.system.localServices.field.memory'},
  {key: 'clients', labelKey: 'debug.system.localServices.field.clients'},
  {key: 'activeTask', labelKey: 'debug.system.localServices.field.activeTask'},
  {key: 'lastFailure', labelKey: 'debug.system.localServices.field.lastFailure'},
  {key: 'queues', labelKey: 'debug.system.localServices.field.queues'},
]);

function debugSystemServiceName(service = {}) {
  return String(service.service || t('debug.system.localServices.serviceFallback')).trim() || t('debug.system.localServices.serviceFallback');
}

function debugSystemPrevText(value) {
  const text = String(value == null || value === '' ? t('common.notAvailable') : value);
  const prefix = t('debug.system.localServices.prevPrefix');
  return text.startsWith(`${prefix} `) ? text : t('debug.system.localServices.prevValue', {value: text});
}

function debugSystemStripPrevText(value) {
  const prefix = t('debug.system.localServices.prevPrefix').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return String(value || '').replace(new RegExp(`^${prefix}\\s*`), '');
}

function debugSystemQueueText(service = {}) {
  const queues = service.queues && typeof service.queues === 'object' ? service.queues : {};
  return Object.entries(queues).map(([key, count]) => `${key} ${count}`).join('\n');
}

function debugSystemLocalServiceRecord(name) {
  let record = debugSystemLocalServicesState.records.get(name);
  if (!record) {
    record = {name, fields: new Map(), startedAt: 0, exitedAt: 0, lastPid: 0, running: false};
    debugSystemLocalServicesState.records.set(name, record);
  }
  return record;
}

function debugSystemLocalServiceUpdateLifecycle(record, service = {}, nowSeconds) {
  const pid = Number(service.pid || 0);
  const running = pid > 0;
  const serverStartedAt = Number(service.started_at || 0);
  const uptimeSeconds = Number(service.uptime_seconds);
  if (running) {
    record.lastPid = pid;
    record.exitedAt = 0;
    record.startedAt = serverStartedAt > 0
      ? serverStartedAt
      : (Number.isFinite(uptimeSeconds) ? Math.max(0, nowSeconds - uptimeSeconds) : (record.running ? record.startedAt : nowSeconds));
  } else if (record.running) {
    record.exitedAt = nowSeconds;
  }
  record.running = running;
}

function debugSystemLocalServiceFieldValue(service = {}, record, fieldKey, nowSeconds) {
  const state = debugSystemServiceState(service);
  const pid = Number(service.pid || 0);
  const running = pid > 0;
  const resources = service.resources && typeof service.resources === 'object' ? service.resources : {};
  const previous = record.fields.get(fieldKey);
  const previousDisplay = debugSystemStripPrevText(previous?.display || '');
  const previousAvailable = previousDisplay && previousDisplay !== '—' && previousDisplay !== t('common.notAvailable');
  const previousValue = () => previousAvailable ? {display: debugSystemPrevText(previousDisplay), identity: `prev:${previous?.identity || previousDisplay}`, previous: true} : null;
  const valueOrPrevious = value => {
    if (running) return value;
    if (value.display && value.display !== '—' && value.display !== t('common.notAvailable')) return {display: debugSystemPrevText(value.display), identity: `prev:${value.identity}`, previous: true};
    return previousValue() || value;
  };
  if (fieldKey === 'status') return {display: state.label, identity: state.label, tone: state.tone};
  if (fieldKey === 'pid') {
    if (running) return {display: String(pid), identity: String(pid)};
    return record.lastPid > 0 ? {display: debugSystemPrevText(record.lastPid), identity: `prev:${record.lastPid}`, previous: true} : {display: '—', identity: 'empty'};
  }
  if (fieldKey === 'started') {
    return record.startedAt > 0
      ? {display: relativeTimeFormat(Math.max(0, nowSeconds - record.startedAt)), identity: `started:${record.startedAt}`, dynamic: true}
      : {display: '—', identity: 'empty'};
  }
  if (fieldKey === 'lastRan') {
    if (running && record.startedAt > 0) return {display: relativeTimeFormat(Math.max(0, nowSeconds - record.startedAt)), identity: `running:${record.startedAt}`, dynamic: true};
    if (record.exitedAt > 0) return {display: t('debug.system.localServices.exitedAgo', {time: relativeTimeFormat(Math.max(0, nowSeconds - record.exitedAt))}), identity: `exited:${record.exitedAt}`, dynamic: true, previous: true};
    return {display: '—', identity: 'empty'};
  }
  if (fieldKey === 'uptime') return valueOrPrevious({display: service.uptime_seconds == null ? '—' : debugGraphUptimeText(service.uptime_seconds), identity: String(service.uptime_seconds ?? '')});
  if (fieldKey === 'cpu') return valueOrPrevious({display: resources.cpu_percent == null ? '—' : `${debugSystemNumber(resources.cpu_percent, 1)}%`, identity: String(resources.cpu_percent ?? '')});
  if (fieldKey === 'memory') return valueOrPrevious({display: resources.rss_bytes == null ? '—' : debugGraphTerseBytesText(resources.rss_bytes), identity: String(resources.rss_bytes ?? '')});
  if (fieldKey === 'clients') return valueOrPrevious({display: service.clients == null ? '—' : debugSystemNumber(service.clients), identity: String(service.clients ?? '')});
  if (fieldKey === 'activeTask') return valueOrPrevious({display: service.active_task || '—', identity: String(service.active_task || '')});
  if (fieldKey === 'lastFailure') return valueOrPrevious({display: service.last_failure || '—', identity: String(service.last_failure || '')});
  if (fieldKey === 'queues') {
    const queueText = debugSystemQueueText(service);
    return valueOrPrevious({display: queueText || '—', identity: queueText});
  }
  return {display: '—', identity: 'empty'};
}

function debugSystemLocalServicesCardHtml() {
  return `<section class="js-debug-system-card js-debug-system-card--wide" data-js-debug-local-services-card>
    <h3>${esc(t('debug.system.localServices.title'))}</h3><div data-js-debug-local-services></div>
  </section>`;
}

function debugSystemLocalServicesTableHtml(serviceNames = []) {
  const minWidthRem = 10 + (Math.max(1, serviceNames.length) * 9);
  return `<div class="js-debug-system-table-wrap js-debug-system-local-services-wrap"><table class="js-debug-system-table js-debug-system-fixed-table js-debug-system-local-services-table" style="--js-debug-system-local-services-min-width:${minWidthRem}rem">
    <thead><tr><th>${esc(t('debug.system.localServices.fieldColumn'))}</th>${serviceNames.map(name => `<th data-js-debug-service-head="${esc(name)}"><span class="js-debug-system-service-name">${esc(name)}</span><span class="js-debug-system-state js-debug-system-state--muted" data-js-debug-service-state="${esc(name)}">${esc(t('state.idle'))}</span></th>`).join('')}</tr></thead>
    <tbody>${debugSystemLocalServiceFields.map(field => `<tr data-js-debug-service-row="${esc(field.key)}"><th scope="row">${esc(t(field.labelKey))}</th>${serviceNames.map(name => `<td data-js-debug-service-cell data-service="${esc(name)}" data-field="${esc(field.key)}">—</td>`).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

function debugSystemLocalServiceCellMap(root) {
  const cells = new Map();
  root.querySelectorAll?.('[data-js-debug-service-cell]').forEach(cell => {
    cells.set(`${cell.dataset.service || ''}\x1f${cell.dataset.field || ''}`, cell);
  });
  return cells;
}

function ensureDebugSystemLocalServicesTable(root, serviceNames) {
  if (!root) return;
  const signature = serviceNames.join('\x1f');
  const table = root.querySelector('.js-debug-system-local-services-table');
  if (table && root.dataset.signature === signature) return;
  root.innerHTML = serviceNames.length ? debugSystemLocalServicesTableHtml(serviceNames) : `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  root.dataset.signature = signature;
}

function updateDebugSystemLocalServiceCell(cell, record, fieldKey, value, nowMs) {
  const field = record.fields.get(fieldKey) || {display: '', identity: '', lastChangedAt: nowMs};
  if (field.identity !== value.identity) field.lastChangedAt = nowMs;
  field.display = value.display;
  field.identity = value.identity;
  record.fields.set(fieldKey, field);
  if (cell.textContent !== value.display) cell.textContent = value.display;
  cell.classList.toggle('js-debug-system-service-cell--prev', value.previous === true);
  cell.classList.toggle('js-debug-system-service-cell--fresh', value.previous !== true && nowMs - field.lastChangedAt <= DEBUG_SYSTEM_SERVICE_FRESH_MS);
  cell.classList.toggle('js-debug-system-service-cell--stale', value.previous === true || nowMs - field.lastChangedAt > DEBUG_SYSTEM_SERVICE_FRESH_MS);
}

function updateDebugSystemLocalServicesCard(card, payload = {}) {
  const root = card?.querySelector?.('[data-js-debug-local-services]');
  if (!root) return;
  const services = Array.isArray(payload.local_services?.services) ? payload.local_services.services : [];
  const incomingNames = services.map(debugSystemServiceName);
  const retainedNames = [...debugSystemLocalServicesState.records.keys()].filter(name => !incomingNames.includes(name));
  const serviceNames = [...incomingNames, ...retainedNames];
  ensureDebugSystemLocalServicesTable(root, serviceNames);
  const cells = debugSystemLocalServiceCellMap(root);
  const nowMs = Date.now();
  const nowSeconds = nowMs / 1000;
  const servicesByName = new Map(services.map(service => [debugSystemServiceName(service), service]));
  for (const name of serviceNames) {
    const service = servicesByName.get(name) || {service: name, pid: 0};
    const record = debugSystemLocalServiceRecord(name);
    debugSystemLocalServiceUpdateLifecycle(record, service, nowSeconds);
    const state = debugSystemServiceState(service);
    const headState = root.querySelector(`[data-js-debug-service-state="${cssEscape(name)}"]`);
    if (headState) {
      headState.textContent = state.label;
      headState.className = `js-debug-system-state js-debug-system-state--${state.tone}`;
    }
    for (const field of debugSystemLocalServiceFields) {
      const cell = cells.get(`${name}\x1f${field.key}`);
      if (!cell) continue;
      updateDebugSystemLocalServiceCell(cell, record, field.key, debugSystemLocalServiceFieldValue(service, record, field.key, nowSeconds), nowMs);
    }
  }
}

function debugSystemRolesHtml(roles = {}) {
  const rows = Object.entries(roles && typeof roles === 'object' ? roles : {});
  if (!rows.length) return `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  return `<div class="js-debug-system-table-wrap"><table class="js-debug-system-table">
    <thead><tr><th>Role</th><th>Status</th><th>Refreshes</th><th>Fallbacks</th><th>Stale reads</th></tr></thead>
    <tbody>${rows.map(([name, role]) => `<tr>
      <td>${esc(name)}</td><td>${esc(role?.status || (role?.owner ? 'owner' : 'follower'))}</td>
      <td>${esc(debugSystemNumber(role?.refresh_requests))}</td><td>${esc(debugSystemNumber(role?.fallback_count))}</td>
      <td>${esc(debugSystemNumber(role?.follower_stale_reads))}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function debugSystemPerformanceTableHtml(rows = [], kind = 'endpoint') {
  if (!Array.isArray(rows) || !rows.length) return `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  return `<div class="js-debug-system-table-wrap"><table class="js-debug-system-table">
    <thead><tr><th>${kind === 'endpoint' ? 'Endpoint' : 'Worker'}</th><th>Calls</th><th>Max</th><th>Payload</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td>${esc(kind === 'endpoint' ? row.surface : `${row.role || ''} · ${row.surface || ''}`)}</td>
      <td>${esc(debugSystemNumber(row.count))}</td>
      <td>${esc(debugGraphTerseTimeText(row.compute_ms_max))}</td>
      <td>${esc(debugGraphTerseBytesText(row.payload_bytes_total))}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function debugSystemSamplerFamilyEntries(value) {
  if (Array.isArray(value)) {
    return value.map((family, index) => [String(family?.family || family?.name || index), family || {}]);
  }
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).filter(([, family]) => family && typeof family === 'object');
}

function debugSystemSamplerFamilyNumber(family, ...keys) {
  for (const key of keys) {
    if (family?.[key] == null) continue;
    const value = Number(family[key]);
    if (Number.isFinite(value)) return Math.max(0, value);
  }
  return 0;
}

function debugSystemSamplerFamilySuccessAge(family, nowSeconds) {
  const reportedAge = debugSystemSamplerFamilyNumber(family, 'last_success_age_seconds');
  if (reportedAge > 0) return relativeTimeFormat(reportedAge);
  let succeededAt = debugSystemSamplerFamilyNumber(family, 'last_success_at', 'last_success');
  if (succeededAt > 1e12) succeededAt /= 1000;
  return succeededAt > 0 ? relativeTimeFormat(Math.max(0, nowSeconds - succeededAt)) : '—';
}

function debugSystemSamplerFamilySeconds(family, secondsKeys, millisecondsKeys) {
  const seconds = debugSystemSamplerFamilyNumber(family, ...secondsKeys);
  if (seconds > 0) return seconds;
  return debugSystemSamplerFamilyNumber(family, ...millisecondsKeys) / 1000;
}

function debugSystemSamplerHeaderHtml(shortKey, fullKey) {
  const full = t(fullKey);
  return `<th scope="col" title="${esc(full)}" aria-label="${esc(full)}"><span aria-hidden="true">${esc(t(shortKey))}</span></th>`;
}

function debugSystemSamplerFamiliesHtml(value, nowSeconds = Date.now() / 1000) {
  const families = debugSystemSamplerFamilyEntries(value);
  if (!families.length) return '';
  return `<div class="js-debug-system-table-wrap" data-js-debug-sampler-families><table class="js-debug-system-table js-debug-system-fixed-table js-debug-system-sampler-table">
    <thead><tr>${[
      ['debug.system.sampler.header.family.short', 'debug.system.sampler.header.family.short'],
      ['debug.system.sampler.header.cadence.short', 'debug.system.sampler.header.cadence.full'],
      ['debug.system.sampler.header.aliveRun.short', 'debug.system.sampler.header.aliveRun.full'],
      ['debug.system.sampler.header.attSuccFails.short', 'debug.system.sampler.header.attSuccFails.full'],
      ['debug.system.sampler.header.lateMiss.short', 'debug.system.sampler.header.lateMiss.full'],
      ['debug.system.sampler.header.runtime.short', 'debug.system.sampler.header.runtime.full'],
      ['debug.system.sampler.header.lastOk.short', 'debug.system.sampler.header.lastOk.full'],
      ['debug.system.sampler.header.lastFail.short', 'debug.system.localServices.field.lastFailure'],
    ].map(([shortKey, fullKey]) => debugSystemSamplerHeaderHtml(shortKey, fullKey)).join('')}</tr></thead>
    <tbody>${families.map(([name, family]) => {
      const cadence = debugSystemSamplerFamilySeconds(family, ['cadence_seconds', 'interval_seconds'], ['cadence_ms', 'interval_ms']);
      const runtime = debugSystemSamplerFamilySeconds(family, ['last_runtime_seconds', 'runtime_seconds', 'last_runtime'], ['last_runtime_ms', 'runtime_ms']);
      const running = family.running === true || family.in_flight === true;
      const alive = family.alive === true || family.sampler_alive === true || running;
      const attempts = debugSystemSamplerFamilyNumber(family, 'attempts', 'attempt_count');
      const successes = debugSystemSamplerFamilyNumber(family, 'successes', 'success_count');
      const failures = debugSystemSamplerFamilyNumber(family, 'failures', 'failure_count');
      const late = debugSystemSamplerFamilyNumber(family, 'late', 'late_cycles');
      const missed = debugSystemSamplerFamilyNumber(family, 'missed', 'missed_cycles');
      return `<tr data-js-debug-sampler-family="${esc(name)}">
        <th scope="row">${esc(name)}</th><td>${esc(cadence > 0 ? debugGraphTerseTimeText(cadence * 1000) : '—')}</td>
        <td>${alive ? 'Yes' : 'No'} / ${running ? 'Yes' : 'No'}</td>
        <td>${esc(`${debugSystemNumber(attempts)} / ${debugSystemNumber(successes)} / ${debugSystemNumber(failures)}`)}</td>
        <td>${esc(`${debugSystemNumber(late)} / ${debugSystemNumber(missed)}`)}</td>
        <td>${esc(runtime > 0 ? debugGraphTerseTimeText(runtime * 1000) : '—')}</td>
        <td>${esc(debugSystemSamplerFamilySuccessAge(family, nowSeconds))}</td>
        <td>${esc(family.last_failure || '—')}</td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

function debugSystemStatsSamplerCardHtml(services = [], nowSeconds = Date.now() / 1000) {
  const statsd = (services || []).find(service => String(service?.service || '') === 'statsd') || {};
  const profile = statsd.history_profile && typeof statsd.history_profile === 'object' ? statsd.history_profile : {};
  const requests = Math.max(0, Number(statsd.history_requests) || 0);
  const hits = Math.min(requests, Math.max(0, Number(statsd.history_cache_hits) || 0));
  const hitRate = requests > 0 ? `${debugSystemNumber((hits / requests) * 100, 1)}% (${hits}/${requests})` : '—';
  const historyQuery = profile.returned_records == null || profile.source_records == null
    ? '—'
    : `${debugSystemNumber(profile.returned_records)} returned · ${debugSystemNumber(profile.source_records)} source`;
  const aggregate = debugSystemRowsHtml([
    ['Status', statsd.sampler_alive === true ? 'Running' : 'Idle'],
    ['Last cycle', debugGraphTerseTimeText(Number(statsd.sampler_last_cycle_seconds || 0) * 1000)],
    ['Late / missed deadlines', `${debugSystemNumber(statsd.sampler_late_cycles)} / ${debugSystemNumber(statsd.sampler_missed_cycles)}`],
    ['History cache hit rate', hitRate],
    ['Last history latency', debugGraphTerseTimeText(Number(profile.assemble_ms || 0))],
    ['Last history query', historyQuery],
  ]);
  return debugSystemCardHtml(
    'YO!stats sampler',
    `${aggregate}${debugSystemSamplerFamiliesHtml(statsd.sampler_families, nowSeconds)}`,
    {wide: true},
  );
}

function debugSystemCpuBudgetCardHtml(budget = {}) {
  const status = ['ok', 'watching', 'warning'].includes(budget.status) ? budget.status : 'ok';
  const consumers = Array.isArray(budget.top_consumers) ? budget.top_consumers : [];
  const consumerText = consumers.map(row => {
    const owner = [row?.role, row?.surface].filter(Boolean).join(':');
    return `${owner || t('common.notAvailable')} ${debugSystemNumber(row?.compute_ms_total, 1)}ms`;
  }).join(' · ') || 'None';
  return debugSystemCardHtml('CPU budget', `<div data-js-debug-cpu-budget="${esc(status)}">${debugSystemRowsHtml([
    ['Status', status],
    ['Current / budget', `${debugSystemNumber(budget.current_percent, 1)}% / ${debugSystemNumber(budget.budget_percent, 1)}%`],
    ['Sustained', `${debugSystemNumber(budget.sustained_seconds, 0)}s / ${debugSystemNumber(budget.sustained_budget_seconds, 0)}s`],
    ['Top compute owners', consumerText],
  ])}</div>`);
}

function debugSystemInnerHtml() {
  const payload = jsDebugSystemState.payload;
  if (!payload) {
    const message = jsDebugSystemState.error || t('common.loading');
    return `<div class="js-debug-system-loading" role="status">${esc(message)}</div>`;
  }
  const server = payload.server || {};
  const owner = payload.owner || {};
  const currentOwner = owner.current_owner || {};
  const refresh = payload.refresh || {};
  const localRefreshing = refresh.local_refreshing || {};
  const coalescing = refresh.coalescing || {};
  const search = payload.search_index || {};
  const caches = payload.caches || {};
  const clientEvents = payload.client_events || {};
  const chat = payload.chat || {};
  const totals = payload.local_services?.totals || {};
  const cpuBudget = payload.cpu_budget || {};
  const services = Array.isArray(payload.local_services?.services) ? payload.local_services.services : [];
  const generatedAgo = payload.generated_at ? relativeTimeFormat(Math.max(0, Date.now() / 1000 - Number(payload.generated_at))) : t('common.notAvailable');
  const cards = [
    debugSystemCardHtml('Server', debugSystemRowsHtml([
      ['Status', payload.ok ? 'Running' : 'Issue'],
      ['Version', server.version], ['PID', server.pid], ['Uptime', debugGraphUptimeText(server.uptime_seconds)],
      ['Process CPU', `${debugSystemNumber(server.cpu_percent, 1)}%`], ['System CPU', `${debugSystemNumber(server.system_cpu_percent, 1)}%`],
      ['Memory', debugGraphTerseBytesText(server.rss_bytes)], ['State directory', payload.state_dir],
    ])),
    debugSystemCpuBudgetCardHtml(cpuBudget),
    debugSystemCardHtml('Distributed owner', debugSystemRowsHtml([
      ['Status', owner.status], ['This server owns work', owner.owner ? 'Yes' : 'No'],
      ['Owner port', currentOwner.port], ['Owner PID', currentOwner.pid],
      ['Index mode', owner.search_index?.mode], ['Generations', owner.debug?.generation_count],
    ])),
    debugSystemCardHtml('Worker totals', debugSystemRowsHtml([
      ['Processes', totals.processes], ['CPU', `${debugSystemNumber(totals.cpu_percent, 1)}%`],
      ['Memory', debugGraphTerseBytesText(totals.rss_bytes)],
      ['Refreshing now', Object.entries(localRefreshing).filter(([, value]) => Boolean(value)).map(([key, value]) => `${key} ${value === true ? '' : value}`.trim()).join(' · ') || 'None'],
      ['Pending refreshes', coalescing.recent_pending_count ?? 0], ['Coalesced requests', refresh.counters?.coalesced_refresh_requests ?? 0],
    ])),
    debugSystemCardHtml('Search & caches', debugSystemRowsHtml([
      ['Indexed roots', search.root_count], ['Builds', search.build_count], ['Scanned entries', search.scanned_entries],
      ['Ignored entries', search.ignored_entries], ['Index bytes', debugGraphTerseBytesText(search.cache_bytes)],
      ['Session files cache', `${debugSystemNumber(caches.session_files?.files)} files · ${debugGraphTerseBytesText(caches.session_files?.bytes)}`],
      ['Activity cache', `${debugSystemNumber(caches.activity?.files)} files · ${debugGraphTerseBytesText(caches.activity?.bytes)}`],
    ])),
    debugSystemCardHtml('Events & chat', debugSystemRowsHtml([
      ['SSE subscribers', Object.values(clientEvents.channel_counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)],
      ['Published events', clientEvents.published_events],
      ['Delivered events', clientEvents.delivered_events],
      ['Chat subscribers', chat.subscribers], ['Chat messages', chat.store?.message_rows], ['Typing leases', chat.store?.typing_leases],
    ])),
    debugSystemStatsSamplerCardHtml(services),
    debugSystemCardHtml('Distributed roles', debugSystemRolesHtml(refresh.roles), {wide: true}),
    debugSystemLocalServicesCardHtml(),
    debugSystemCardHtml('Top API endpoints', debugSystemPerformanceTableHtml(payload.top_endpoints, 'endpoint'), {wide: true}),
    debugSystemCardHtml('Top background work', debugSystemPerformanceTableHtml(payload.top_background_work, 'worker'), {wide: true}),
  ];
  return `<div class="js-debug-system-toolbar">
    <span role="status">Updated ${esc(generatedAgo)}${jsDebugSystemState.error ? ` · ${esc(jsDebugSystemState.error)}` : ''}</span>
    <button type="button" class="preferences-inline-action" data-js-debug-system-refresh${jsDebugSystemState.inFlight ? ' disabled' : ''}>${esc(t('common.refresh'))}</button>
  </div><div class="js-debug-system-grid">${cards.join('')}</div>`;
}

function refreshDebugSystemViews() {
  for (const view of document.querySelectorAll('[data-js-debug-system]')) {
    const scrollTop = view.scrollTop;
    const scrollLeft = view.scrollLeft;
    const oldLocalServicesCard = view.querySelector('[data-js-debug-local-services-card]');
    view.innerHTML = debugSystemInnerHtml();
    const newLocalServicesCard = view.querySelector('[data-js-debug-local-services-card]');
    let localServicesCard = newLocalServicesCard;
    if (oldLocalServicesCard && newLocalServicesCard && jsDebugSystemState.payload) {
      newLocalServicesCard.replaceWith(oldLocalServicesCard);
      localServicesCard = oldLocalServicesCard;
    }
    if (jsDebugSystemState.payload) updateDebugSystemLocalServicesCard(localServicesCard, jsDebugSystemState.payload);
    restoreElementScrollPosition(view, scrollTop, scrollLeft);
    view.setAttribute('aria-busy', jsDebugSystemState.inFlight ? 'true' : 'false');
  }
}

async function pollDebugSystemStatus({force = false} = {}) {
  if (jsDebugSystemState.inFlight || typeof apiFetchJsonQuiet !== 'function') return false;
  if (!force && (jsDebugSubTab !== 'system' || !jsDebugStatsPanelVisible())) return false;
  jsDebugSystemState.inFlight = true;
  jsDebugSystemState.error = '';
  refreshDebugSystemViews();
  try {
    jsDebugSystemState.payload = await apiFetchJsonQuiet('/api/system-status', {cache: 'no-store'});
    jsDebugSystemState.updatedAt = Date.now();
    return true;
  } catch (error) {
    jsDebugSystemState.error = userMessageText(error);
    return false;
  } finally {
    jsDebugSystemState.inFlight = false;
    refreshDebugSystemViews();
  }
}

function syncDebugSystemPolling({pollNow = false} = {}) {
  if (jsDebugSubTab !== 'system' || !jsDebugStatsPanelVisible()) {
    clearRuntimeInterval('debug-system');
    return;
  }
  resetRuntimeInterval('debug-system', () => { void pollDebugSystemStatus(); }, jsDebugSystemPollMs);
  if (pollNow || !jsDebugSystemState.payload) void pollDebugSystemStatus({force: true});
}

function refreshDebugLogsViews() {
  for (const view of document.querySelectorAll('[data-js-debug-subview="logs"]')) {
    const list = view.querySelector('[data-js-debug-log-list]');
    const scrollTop = list?.scrollTop || 0;
    view.innerHTML = debugLogsInnerHtml();
    const replacement = view.querySelector('[data-js-debug-log-list]');
    if (replacement) replacement.scrollTop = scrollTop;
  }
}

async function pollDebugLogs({force = false} = {}) {
  if (jsDebugLogsState.inFlight || typeof apiFetchJsonQuiet !== 'function') return false;
  if (!force && (jsDebugSubTab !== 'logs' || !jsDebugStatsPanelVisible())) return false;
  jsDebugLogsState.inFlight = true;
  jsDebugLogsState.error = '';
  refreshDebugLogsViews();
  try {
    const payload = await apiFetchJsonQuiet('/api/logs', {cache: 'no-store'});
    jsDebugLogsState.payload = Array.isArray(payload?.logs) ? payload.logs.slice(-500) : [];
    jsDebugLogsState.updatedAt = Date.now();
    return true;
  } catch (error) {
    jsDebugLogsState.error = userMessageText(error);
    return false;
  } finally {
    jsDebugLogsState.inFlight = false;
    refreshDebugLogsViews();
  }
}

function syncDebugLogsPolling({pollNow = false} = {}) {
  if (jsDebugSubTab !== 'logs' || !jsDebugStatsPanelVisible()) {
    clearRuntimeInterval('debug-logs');
    return;
  }
  resetRuntimeInterval('debug-logs', () => { void pollDebugLogs(); }, jsDebugLogsPollMs);
  if (pollNow || !jsDebugLogsState.updatedAt) void pollDebugLogs({force: true});
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
    <div class="js-debug-subview js-debug-graph-view" ${debugSubViewAttrs('graph')}>${debugGraphHtml()}</div>
    <div class="js-debug-subview js-debug-system-view" data-js-debug-system ${debugSubViewAttrs('system')}>${debugSystemInnerHtml()}</div>
    <div class="js-debug-subview js-debug-logs-view" ${debugSubViewAttrs('logs')}>${debugLogsInnerHtml()}</div>`;
}

function relocalizeDebugPanelChrome(panel = document.getElementById(panelDomId(debugPaneItemId))) {
  return relocalizeVirtualPanelChrome(panel, t('tab.debug'));
}

function yoCostPanelHtml() {
  const nowMs = Date.now();
  const buckets = debugGraphDisplayBuckets(nowMs);
  const tokenGroups = jsDebugGraphChartGroups.filter(group => group.key === 'agentTokens' || group.key === 'modelTokens');
  const charts = buckets.length
    ? debugGraphSvgHtml(buckets, debugGraphSeriesData(buckets), tokenGroups, nowMs, {includeCostSummary: false})
    : `<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
  const costBuckets = debugGraphAgentTokenDisplayBuckets(nowMs);
  const refreshedAtMs = Math.max(Number(jsDebugStatsPollState.lastSampleAtMs) || 0, Number(jsDebugPricingRefreshState.lastRequestedAtMs) || 0);
  const ageSeconds = refreshedAtMs > 0 ? Math.max(0, Math.floor((nowMs - refreshedAtMs) / 1000)) : null;
  const age = ageSeconds === null ? t('common.notAvailable') : ageSeconds < 60 ? `${ageSeconds}s ago` : relativeTimeFormat(ageSeconds);
  const refreshLabel = debugGraphCostText('common.refresh', 'Refresh');
  const refresh = readOnlyMode ? '' : `<button type="button" class="js-debug-cost-refresh control-active-hover" data-js-debug-cost-refresh${jsDebugPricingRefreshState.inFlight ? ' disabled aria-busy="true"' : ''}>${esc(jsDebugPricingRefreshState.inFlight ? `${refreshLabel}…` : refreshLabel)}</button>`;
  const ageLabel = debugGraphCostText('debug.cost.lastRefreshed', `Last refreshed ${age}`, {time: age});
  // The YO!cost chart area carries the ONE shared history loading overlay so a
  // range/resolution change dims it and centers "Loading…" exactly like
  // YO!stats. It deliberately is NOT a [data-js-debug-graph] surface (those get
  // rebuilt with YO!stats content by the graph-refresh loops); the readiness
  // sync toggles this overlay through its own targeted pass.
  const chartArea = `<div class="js-yocost-chart-area" data-js-yocost-chart-area data-js-debug-history-state="${esc(jsDebugHistoryReadinessStateName())}">${charts}${debugGraphHistoryOverlayHtml()}</div>`;
  return `<div class="js-yocost-graphs" data-js-yocost-graphs><div class="js-yocost-controls" data-js-yocost-data-age><span data-js-yocost-data-age-label>${esc(ageLabel)}</span>${debugGraphRangeControlsHtml(nowMs)}${debugGraphResolutionLabelHtml(nowMs)}${refresh}</div>${chartArea}</div>${debugGraphCostReportHtml(debugGraphCostSummaryForBuckets(costBuckets), debugGraphDomain(nowMs))}`;
}

function openYoCostTranscriptPreview(event) {
  const link = event.target?.closest?.('[data-js-debug-cost-transcript-path]');
  if (!link) return false;
  event.preventDefault();
  const path = debugGraphCostTranscriptPath({transcript: link.dataset.jsDebugCostTranscriptPath});
  if (!path) return true;
  Promise.resolve(openFileInEditor(path, basenameOf(path), {viewMode: 'preview', userInitiated: true}))
    .catch(() => emitNotification('previewOpen', {item: fileEditorItemFor(path), title: t('preview.openFailed', {path}), className: 'attention-alert toast'}));
  return true;
}

function handleYoCostTableSort(event, panel) {
  const button = event.target?.closest?.('[data-js-debug-cost-sort]');
  if (!button || !panel?.contains(button)) return false;
  event.preventDefault();
  const key = String(button.dataset.jsDebugCostSort || 'cost');
  const nextDirection = jsDebugCostComponentSortState.key === key && jsDebugCostComponentSortState.direction === 'asc' ? 'desc' : 'asc';
  jsDebugCostComponentSortState.key = key;
  jsDebugCostComponentSortState.direction = nextDirection;
  // Age-label cadence throttles passive panel repainting; a user sort is an
  // explicit content mutation and must repaint immediately.
  renderYoCostPanels({force: true});
  return true;
}

function bindYoCostPanel(panel) {
  if (!panel || panel.dataset.jsYoCostBound === 'true') return;
  panel.dataset.jsYoCostBound = 'true';
  panel.addEventListener('pointerdown', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerDown(event, panel);
  });
  panel.addEventListener('pointermove', event => { handleDebugGraphPointerMove(event, panel); });
  panel.addEventListener('pointerleave', () => { debugGraphClearInteractionLinesUnlessPinned(panel); });
  panel.addEventListener('pointerup', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerUp(event, panel);
  });
  panel.addEventListener('pointercancel', event => {
    handleDebugGraphControlEvent(event, panel);
    cancelDebugGraphSelection(panel);
  });
  panel.addEventListener('input', event => { handleDebugGraphControlEvent(event, panel); });
  panel.addEventListener('change', event => { handleDebugGraphControlEvent(event, panel); });
  panel.addEventListener('click', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    if (handleYoCostTableSort(event, panel)) return;
    openYoCostTranscriptPreview(event);
  });
}

function createYoCostPanel() {
  enableDebugMode();
  const panel = document.createElement('article');
  panel.className = 'panel js-yocost-panel';
  panel.id = panelDomId(yocostItemId);
  panel.innerHTML = panelFrameHtml({
    item: yocostItemId,
    headClass: 'preferences-panel-head',
    controlsHtml: virtualPanelInnerControlsHtml(yocostItemId),
    afterHeadHtml: `<div class="pane-info-bar panel-detail-row"><div class="pane-info-bar-copy panel-copy"><div id="panel-tab-${yocostItemId}" class="panel-session-label"><span class="session-button-dir">${esc(yocostTabLabel())}</span></div><div id="meta-${yocostItemId}" class="pane-info-bar-meta meta">${esc(debugGraphCostText('debug.cost.details', 'Cost summary details'))}</div></div><button type="button" class="panel-detail-close" data-detail-toggle="${esc(yocostItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button></div>`,
    bodyClass: 'preferences-body js-yocost-body',
    bodyHtml: `<div class="preferences-scroll js-yocost-scroll">${yoCostPanelHtml()}</div>`,
  });
  bindPanelShell(panel, yocostItemId);
  bindYoCostPanel(panel);
  return panel;
}

function debugCostAgeRefreshDelayMs(randomValue = Math.random()) {
  return 3000 + Math.floor(Math.max(0, Math.min(1, Number(randomValue) || 0)) * 7000);
}

function renderYoCostPanels({force = false} = {}) {
  if (dragState.item != null) {
    jsDebugRenderForce ||= force;
    jsDebugRenderDragDeferred = true;
    return false;
  }
  const nowMs = Date.now();
  const visible = typeof document !== 'undefined'
    && document.visibilityState !== 'hidden'
    && itemIsActivePaneTab(yocostItemId);
  if (!force && (!visible || nowMs < jsDebugCostPanelNextRefreshAtMs)) return false;
  for (const panel of document.querySelectorAll('.js-yocost-panel')) {
    const body = panel.querySelector('.js-yocost-body');
    const scroll = body?.querySelector('.js-yocost-scroll');
    const scrollTop = scroll?.scrollTop || 0;
    const scrollLeft = scroll?.scrollLeft || 0;
    if (body) {
      body.innerHTML = `${panelToastStackHtml(yocostItemId)}<div class="preferences-scroll js-yocost-scroll">${yoCostPanelHtml()}</div>`;
      restoreElementScrollPosition(body.querySelector('.js-yocost-scroll'), scrollTop, scrollLeft);
    }
    bindYoCostPanel(panel);
  }
  const delayMs = debugCostAgeRefreshDelayMs();
  jsDebugCostPanelNextRefreshAtMs = nowMs + delayMs;
  jsDebugCostAgeNextRefreshAtMs = nowMs + delayMs;
  syncDebugGraphLiveTicker();
  return true;
}

function refreshDebugGraphSurfaces({force = true, deferFocusedControl = true} = {}) {
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force, deferFocusedControl});
  }
  renderYoCostPanels({force});
}

function relocalizeYoCostPanelChrome(panel = document.getElementById(panelDomId(yocostItemId))) {
  return relocalizeVirtualPanelChrome(panel, yocostTabLabel());
}

function createDebugPanel() {
  enableDebugMode();
  const panel = document.createElement('article');
  panel.className = 'panel js-debug-panel';
  panel.id = panelDomId(debugPaneItemId);
  panel.innerHTML = panelFrameHtml({
    item: debugPaneItemId,
    headClass: 'preferences-panel-head',
    controlsHtml: virtualPanelInnerControlsHtml(debugPaneItemId),
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
  renderYoCostPanels(options);
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts(debugPaneItemId);
}

function refreshDebugPanelsFromEvents(options = {}) {
  if (dragState.item != null) {
    jsDebugRenderForce ||= options.force === true;
    jsDebugRenderDragDeferred = true;
    return;
  }
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    refreshDebugPanelFromEvents(panel, options);
  }
  renderYoCostPanels(options);
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
  if (panel.querySelector('[data-js-debug-subview="logs"]')) refreshDebugLogsViews();
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

function debugGraphFocusedControl(graph) {
  const active = typeof document !== 'undefined' ? document.activeElement : null;
  if (!graph || !active || !graph.contains(active)) return null;
  if (active.matches?.('[data-js-debug-range-slider]')) return null;
  return active.closest?.('.js-debug-graph-controls, [data-js-debug-model-token-dimension]') || null;
}

function syncDebugGraphControls(graph, nowMs = Date.now()) {
  if (!graph) return;
  const options = debugGraphAvailableRangeOptions(nowMs);
  const domain = debugGraphDomain(nowMs);
  const zoomed = debugGraphZoomDomainValid();
  const slider = graph.querySelector('[data-js-debug-range-slider]');
  if (slider) {
    slider.max = String(Math.max(0, options.length - 1));
    slider.value = String(jsDebugGraphRangeOptionIndex(activeJsDebugGraphRangeSeconds(nowMs), nowMs));
    slider.disabled = zoomed;
    slider.setAttribute('aria-disabled', zoomed ? 'true' : 'false');
  }
  const rangeLabel = graph.querySelector('[data-js-debug-range-label]');
  if (rangeLabel) rangeLabel.textContent = zoomed ? debugGraphCostRangeText(domain) : jsDebugGraphRangeLabel(jsDebugGraphRangeSeconds, nowMs);
  const rangeControl = graph.querySelector('[data-js-debug-range-control]');
  let reset = rangeControl?.querySelector('[data-js-debug-zoom-reset]');
  if (zoomed && rangeControl && !reset) {
    reset = makeButton({
      className: 'js-debug-zoom-reset',
      dataset: {jsDebugZoomReset: ''},
      label: `${t('common.reset')} ${t('debug.graph.control.zoom')}`,
    });
    rangeControl.append(reset);
  } else if (!zoomed) {
    reset?.remove();
  }
  graph.querySelectorAll('[data-js-debug-chart-layout]').forEach(button => {
    button.setAttribute('aria-pressed', Number(button.dataset.jsDebugChartLayout) === jsDebugGraphChartLayout ? 'true' : 'false');
  });
  graph.querySelectorAll('[data-js-debug-chart-toggle]').forEach(button => {
    button.setAttribute('aria-pressed', debugGraphChartVisible(button.dataset.jsDebugChartToggle) ? 'true' : 'false');
  });
  const resolution = graph.querySelector('[data-js-debug-resolution]');
  const expectedHost = document.createElement('div');
  expectedHost.innerHTML = debugGraphResolutionLabelHtml(nowMs);
  const expectedResolution = expectedHost.firstElementChild;
  if (resolution && expectedResolution) {
    resolution.dataset.jsDebugResolutionSeconds = expectedResolution.dataset.jsDebugResolutionSeconds;
    const select = resolution.querySelector('[data-js-debug-resolution-override]');
    const expectedSelect = expectedResolution.querySelector('[data-js-debug-resolution-override]');
    if (select && expectedSelect && document.activeElement !== select && select.innerHTML !== expectedSelect.innerHTML) select.innerHTML = expectedSelect.innerHTML;
    if (select && expectedSelect && document.activeElement !== select) select.value = expectedSelect.value;
    const firstText = [...resolution.childNodes].find(node => node.nodeType === 3);
    const expectedText = [...expectedResolution.childNodes].find(node => node.nodeType === 3);
    if (firstText && expectedText) firstText.textContent = expectedText.textContent;
  }
  const dimension = graph.querySelector('[data-js-debug-model-token-dimension-select]');
  if (dimension && document.activeElement !== dimension) dimension.value = jsDebugGraphModelTokenDimension;
}

function preserveDebugGraphBodyControls(graph, nextBody) {
  const selectors = ['[data-js-debug-model-token-dimension-select]', '[data-js-debug-chart-close]'];
  for (const selector of selectors) {
    const currentByKey = new Map([...graph.querySelectorAll(selector)].map(control => [
      control.dataset.jsDebugChartClose || control.getAttribute('data-js-debug-model-token-dimension-select') || 'modelTokens',
      control,
    ]));
    for (const replacement of nextBody.querySelectorAll(selector)) {
      const key = replacement.dataset.jsDebugChartClose || replacement.getAttribute('data-js-debug-model-token-dimension-select') || 'modelTokens';
      const current = currentByKey.get(key);
      if (!current) continue;
      for (const attribute of [...replacement.attributes]) current.setAttribute(attribute.name, attribute.value);
      for (const attribute of [...current.attributes]) {
        if (!replacement.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
      }
      replacement.replaceWith(current);
    }
  }
}

function debugCostAgeLabels() {
  if (typeof document === 'undefined' || !itemIsActivePaneTab(yocostItemId)) return [];
  return [...document.querySelectorAll('[data-js-yocost-data-age-label]')].filter(label => !label.closest('[hidden]') && label.getClientRects().length > 0);
}

function debugCostAgeLabelText(nowMs = Date.now()) {
  const refreshedAtMs = Math.max(Number(jsDebugStatsPollState.lastSampleAtMs) || 0, Number(jsDebugPricingRefreshState.lastRequestedAtMs) || 0);
  const ageSeconds = refreshedAtMs > 0 ? Math.max(0, Math.floor((nowMs - refreshedAtMs) / 1000)) : null;
  const age = ageSeconds === null ? t('common.notAvailable') : ageSeconds < 60 ? `${ageSeconds}s ago` : relativeTimeFormat(ageSeconds);
  return debugGraphCostText('debug.cost.lastRefreshed', `Last refreshed ${age}`, {time: age});
}

function refreshDebugCostAgeLabels(nowMs = Date.now()) {
  const labels = debugCostAgeLabels();
  if (!labels.length || nowMs < jsDebugCostAgeNextRefreshAtMs) return false;
  const text = debugCostAgeLabelText(nowMs);
  labels.forEach(label => { label.textContent = text; });
  jsDebugCostAgeNextRefreshAtMs = nowMs + debugCostAgeRefreshDelayMs();
  return true;
}

function debugGraphSlidingAxisActive() {
  // Live ranges up to 1h advance continuously with the wall clock so the axis
  // slides and content drifts left even between (up to 60s) data ticks. Coarser
  // (>1h), zoomed, and hidden views stay static per the range-scaled cadence contract.
  return !debugGraphZoomDomainValid() && jsDebugGraphRangeSeconds <= jsDebugGraphSlideMaxRangeSeconds;
}

function debugGraphLiveTickerNeeded() {
  return debugCostAgeLabels().length > 0 || debugGraphSlidingAxisActive();
}

function debugGraphSlideLiveViews(nowMs = Date.now()) {
  // Re-render each visible live graph at most once per slide interval so the
  // plot region drifts left with wall clock. The per-graph throttle keeps this
  // to ~1s and never fires within a sub-second window of a fresh render, so
  // data-tick throttling and mounted controls are undisturbed.
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    if (graph.offsetParent === null) continue;
    const renderedAt = Number(graph.dataset.jsDebugGraphRenderedAt);
    if (Number.isFinite(renderedAt) && nowMs - renderedAt < jsDebugGraphSlideRenderMs) continue;
    refreshDebugGraphElement(graph, {force: true});
  }
}

function stopDebugGraphLiveTicker() {
  if (jsDebugGraphLiveFrame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(jsDebugGraphLiveFrame);
  jsDebugGraphLiveFrame = 0;
  jsDebugGraphLiveFrameLastMs = 0;
}

function debugGraphLiveFrameTick(frameMs = performanceNow()) {
  jsDebugGraphLiveFrame = 0;
  // A slide render re-arms the ticker; guard against synchronous re-entry so a
  // synchronous requestAnimationFrame (test harness) or a nested refresh cannot
  // recurse. Real browsers schedule the next tick on the following frame.
  if (jsDebugGraphLiveFrameTicking) return;
  if (typeof document === 'undefined' || document.visibilityState === 'hidden') return;
  const ageLabels = debugCostAgeLabels();
  const slidingActive = debugGraphSlidingAxisActive();
  if (!ageLabels.length && !slidingActive) return;
  jsDebugGraphLiveFrameTicking = true;
  try {
    if (ageLabels.length && frameMs - jsDebugGraphLiveFrameLastMs >= 50) {
      jsDebugGraphLiveFrameLastMs = frameMs;
      refreshDebugCostAgeLabels(Date.now());
    }
    if (slidingActive) debugGraphSlideLiveViews();
    jsDebugGraphLiveFrame = requestAnimationFrame(debugGraphLiveFrameTick);
  } finally {
    jsDebugGraphLiveFrameTicking = false;
  }
}

function syncDebugGraphLiveTicker() {
  if (typeof requestAnimationFrame !== 'function' || typeof document === 'undefined' || document.visibilityState === 'hidden' || !debugGraphLiveTickerNeeded()) {
    stopDebugGraphLiveTicker();
    return;
  }
  if (!jsDebugGraphLiveFrame) jsDebugGraphLiveFrame = requestAnimationFrame(debugGraphLiveFrameTick);
}

function flushDeferredDebugGraphRefresh(graph) {
  if (!graph || graph.dataset.jsDebugGraphRefreshPending !== 'true' || debugGraphFocusedControl(graph)) return false;
  delete graph.dataset.jsDebugGraphRefreshPending;
  return refreshDebugGraphElement(graph, {force: true});
}

function refreshDebugGraphElement(graph, {force = false, deferFocusedControl = true} = {}) {
  if (!graph || jsDebugGraphRangeSliderDragging) return false;
  if (deferFocusedControl && debugGraphFocusedControl(graph)) {
    graph.dataset.jsDebugGraphRefreshPending = 'true';
    return false;
  }
  const nowMs = Date.now();
  const lastRenderedAt = Number(graph.dataset.jsDebugGraphRenderedAt);
  if (!force && Number.isFinite(lastRenderedAt) && nowMs - lastRenderedAt < jsDebugGraphRefreshMs) return false;
  const perf = clientPerfStart('statsHistoryRender');
  const scrollOwner = graph.closest('.js-debug-graph-view');
  const scrollTop = scrollOwner?.scrollTop || 0;
  const scrollLeft = scrollOwner?.scrollLeft || 0;
  try {
    graph.className = debugGraphClassName(nowMs);
    let body = graph.querySelector('[data-js-debug-graph-body]');
    if (!body) {
      graph.innerHTML = debugGraphInnerHtml(nowMs);
      body = graph.querySelector('[data-js-debug-graph-body]');
    } else {
      const nextBody = document.createElement('div');
      nextBody.innerHTML = debugGraphBodyHtml(nowMs);
      preserveDebugGraphBodyControls(graph, nextBody);
      body.replaceChildren(...nextBody.childNodes);
    }
    syncDebugGraphControls(graph, nowMs);
    restoreElementScrollPosition(scrollOwner, scrollTop, scrollLeft);
    bindDebugCostSummaryTabButtons(graph);
    graph.dataset.jsDebugGraphRenderedAt = String(nowMs);
    graph.dataset.jsDebugHistoryState = jsDebugHistoryReadinessStateName();
    graph.setAttribute('aria-busy', jsDebugHistoryReadinessBusy() ? 'true' : 'false');
    delete graph.dataset.jsDebugGraphRefreshPending;
    if (typeof scheduleAgentWindowActivityAnimationSync === 'function') scheduleAgentWindowActivityAnimationSync(graph);
    syncDebugGraphLiveTicker();
  } finally {
    clientPerfEnd(perf);
  }
  return true;
}

function bindDebugCostSummaryTabButtons(graph) {
  if (!graph) return;
  graph.querySelectorAll('[data-js-debug-cost-details]').forEach(anchor => {
    if (anchor.dataset.jsDebugCostDetailsBound === 'true') return;
    anchor.dataset.jsDebugCostDetailsBound = 'true';
    anchor.addEventListener('click', event => {
      event.preventDefault();
      selectSession(yocostItemId, {userInitiated: true});
    });
  });
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
  syncDebugSystemPolling({pollNow: jsDebugSubTab === 'system'});
  syncDebugLogsPolling({pollNow: jsDebugSubTab === 'logs'});
}

function requestJsDebugHistoryForCurrentDomain({retry = false, forceGraphRefresh = true} = {}) {
  if (!jsDebugStatsPanelVisible()) return false;
  const domain = debugGraphDomain();
  const requestedStartSeconds = Math.max(0, Math.floor(domain.startMs / 1000));
  const requestedDomainEndSeconds = Math.max(requestedStartSeconds + 1, Math.ceil(domain.endMs / 1000));
  const requestedResolutionSeconds = jsDebugRequestedHistoryResolutionSeconds();
  const coverageResolutionSeconds = jsDebugHistoryCoverageResolutionSeconds(requestedStartSeconds, requestedResolutionSeconds);
  if (!retry && !jsDebugHistoryCoverageNeedsRefresh(requestedStartSeconds, requestedDomainEndSeconds, coverageResolutionSeconds)) return false;
  const state = jsDebugHistoryReadiness;
  if (!retry && jsDebugHistoryReadinessErrorLike(state) && !jsDebugHistoryAutoRetryDue(state)) return false;
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
  activeJsDebugGraphRangeSeconds();
  saveJsDebugStatsUiPreferences();
  if (!render) return;
  syncJsDebugStatsDeliveryMode();
  const requestedStartSeconds = Math.max(0, Math.floor(debugGraphDomain().startMs / 1000));
  // An explicit range action is also an explicit retry. Do not let an old
  // automatic-retry backoff make a newly requested domain appear ready while
  // no request is queued.
  const requestedHistory = requestJsDebugHistoryForCurrentDomain({retry: jsDebugHistoryReadiness.phase === 'error'});
  if (!requestedHistory && (jsDebugHistoryReadinessBusy() || jsDebugHistoryReadiness.phase === 'error')) {
    setJsDebugHistoryReadiness('ready', {
      requestedRangeSeconds: jsDebugGraphRangeSeconds,
      requestedStartSeconds,
      attemptCount: 0,
      error: '',
      generation: Number(jsDebugHistoryReadiness.generation || 0) + 1,
    });
  }
  refreshDebugGraphSurfaces();
}

function setDebugGraphResolutionOverride(value) {
  loadJsDebugStatsUiPreferences();
  const previousSeconds = Number(jsDebugGraphResolutionOverrideSeconds) || 0;
  const seconds = Math.max(0, Number(value) || 0);
  const normalized = normalizedDebugGraphResolutionOverrideSeconds(seconds, debugGraphDomain(), Date.now());
  jsDebugGraphResolutionOverrideSeconds = normalized;
  saveJsDebugStatsUiPreferences();
  // Immediate ≤1-frame acknowledgement: the control + Resolution label reflect the target
  // value now, before any fetch resolves.
  refreshDebugGraphSurfaces();
  if (normalized === previousSeconds) {
    jsDebugGraphPendingResolutionChange = null;
    return;
  }
  // Cached/instant path: when the domain's buckets are already client-side (the common
  // case, since a resolution change is an in-memory re-aggregation) no fetch is needed and
  // the swap is instant with no overlay. Only when the change genuinely needs finer/coarser
  // history do we show the shared dimmed loading overlay over the still-visible old data and
  // arm a generation-guarded revert-on-failure.
  //
  const fetching = requestJsDebugHistoryForCurrentDomain();
  if (!fetching) {
    jsDebugGraphPendingResolutionChange = null;
    return;
  }
  jsDebugGraphPendingResolutionChange = {previousSeconds, generation: Number(jsDebugHistoryReadiness.generation || 0)};
  // An explicit user action must acknowledge within a frame, so surface the shared overlay
  // immediately rather than after the older-load debounce that avoids flashing on passive
  // tail repairs.
  jsDebugHistoryReadiness.overlayVisible = true;
  clearJsDebugHistoryOverlayTimer();
  syncJsDebugHistoryReadinessSurfaces();
}

// Resolve a pending Resolution-change fetch. Generation-guarded so a stale history response
// can neither clear nor revert a newer request. On success the overlay clears through the
// normal ready path; on failure the control reverts to its previous value with a danger
// toast (never a silent snap-back) and the chart returns to that cached resolution.
function resolveDebugGraphResolutionChange(state) {
  const pending = jsDebugGraphPendingResolutionChange;
  if (!pending || Number(state.generation) !== Number(pending.generation)) return;
  if (state.phase === 'ready') {
    jsDebugGraphPendingResolutionChange = null;
    return;
  }
  if (state.phase !== 'error') return;
  jsDebugGraphPendingResolutionChange = null;
  const revertedSeconds = normalizedDebugGraphResolutionOverrideSeconds(pending.previousSeconds, debugGraphDomain(), Date.now());
  jsDebugGraphResolutionOverrideSeconds = revertedSeconds;
  saveJsDebugStatsUiPreferences();
  refreshDebugGraphSurfaces();
  const label = revertedSeconds > 0 ? `${revertedSeconds}s` : 'AUTO';
  // A toast is user feedback, never a state-machine dependency: its rendering must not be
  // able to throw back into the readiness transition that invoked this resolver.
  try {
    emitNotification('statsResolution', {
      title: t('debug.graph.resolution.loadFailed', {resolution: label}),
      className: 'danger-alert toast',
      coalesceKey: 'statsResolution',
    });
  } catch (_) {}
}

function setDebugGraphChartLayout(value) {
  loadJsDebugStatsUiPreferences();
  jsDebugGraphChartLayout = Math.max(0, Math.min(4, Math.round(Number(value) || 0)));
  saveJsDebugStatsUiPreferences();
  refreshDebugGraphSurfaces();
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
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (!graph || ratio == null) return;
  const x = (Math.max(0, Math.min(1, Number(ratio))) * jsDebugGraphGeometry.width).toFixed(1);
  graph.classList.add('js-debug-graph--hovering');
  graph.querySelectorAll('[data-js-debug-hover-line]').forEach(line => {
    line.setAttribute('x1', x);
    line.setAttribute('x2', x);
  });
}

function debugGraphSetHoverLegendItems(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  const items = chart?.querySelectorAll?.('[data-js-debug-legend]') || [];
  if (!data || (data.group.dynamicAgentTokens !== true && !data.group.dynamicTokenDimension)) {
    items.forEach(item => item.classList.remove('js-debug-legend-item--hovered'));
    return;
  }
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  const activeKeys = new Set(index < 0 ? [] : data.groupSeries
    .filter(series => series.agentTokenSeries === true && (!data.group.dynamicTokenDimension || series.tokenDimension === data.group.dynamicTokenDimension))
    .filter(series => !Array.isArray(series.hasDataValues) || series.hasDataValues[index] === true)
    .map(series => series.key));
  items.forEach(item => item.classList.toggle('js-debug-legend-item--hovered', activeKeys.has(String(item.dataset.jsDebugLegend || ''))));
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
  const tokenDetail = debugGraphTokenHoverDetailAtTime(chart, timestamp);
  tooltip.querySelector('[data-js-debug-hover-max]').textContent = tokenDetail?.span || debugGraphHoverValueAtTime(chart, timestamp);
  tooltip.querySelector('[data-js-debug-hover-time]').textContent = tokenDetail?.detail || debugGraphExactTimeLabel(timestamp);
  tooltip.toggleAttribute('data-js-debug-hover-no-data', tokenDetail?.noData === true);
  const provenance = debugGraphHoverProvenanceAtTime(chart, timestamp);
  if (provenance.length) tooltip.setAttribute('data-js-debug-hover-provenance', JSON.stringify(provenance));
  else tooltip.removeAttribute('data-js-debug-hover-provenance');
  const sourceText = debugGraphHeldProvenanceText(provenance);
  const source = tooltip.querySelector('[data-js-debug-hover-source]');
  const sourceSeparator = tooltip.querySelector('[data-js-debug-hover-source-separator]');
  if (source) {
    source.textContent = sourceText;
    source.hidden = !sourceText;
  }
  if (sourceSeparator) sourceSeparator.hidden = !sourceText;
  debugGraphSetHoverLegendItems(chart, timestamp);
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
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (graph) graph.classList.remove('js-debug-graph--hovering');
  panel?.querySelectorAll?.('[data-js-debug-hover-tooltip]').forEach(tooltip => { tooltip.hidden = true; });
  panel?.querySelectorAll?.('[data-js-debug-legend-item--hovered]').forEach(item => item.classList.remove('js-debug-legend-item--hovered'));
}

function debugGraphClearInteractionLinesUnlessPinned(panel) {
  // A touch tap pins the tooltip: the pointerleave that fires when the finger
  // lifts must not clear it (there is no "move away" gesture on touch). A mouse
  // leaving the chart clears immediately, exactly as before.
  if (jsDebugGraphLastPointerType === 'touch') return;
  debugGraphClearInteractionLines(panel);
}

function handleDebugGraphOutsideTapDismiss(event) {
  // Dismiss a pinned touch tooltip when the next tap lands outside the chart that
  // owns it. A tap on a chart updates the tooltip through the normal pointerdown
  // path, so only genuinely-outside taps clear here.
  if (jsDebugGraphLastPointerType !== 'touch') return;
  if (event.target?.closest?.('.js-debug-line-chart')) return;
  for (const panel of document.querySelectorAll('.js-debug-graph-view, [data-js-debug-panel]')) {
    debugGraphClearInteractionLines(panel);
  }
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('pointerdown', handleDebugGraphOutsideTapDismiss, true);
}

function debugGraphSetSelectionRects(panel, startRatio, endRatio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
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
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
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
  jsDebugGraphLastPointerType = event.pointerType || 'mouse';
  if (document.activeElement?.closest?.('.js-debug-graph-controls, [data-js-debug-range-control], [data-js-debug-model-token-dimension]')) document.activeElement.blur?.();
  const svg = event.target.closest('.js-debug-line-chart');
  // Capture the pointer so a touch drag keeps delivering pointermove even when the
  // finger strays past the SVG's bounding box mid-drag.
  if (event.pointerId != null && typeof svg?.setPointerCapture === 'function') {
    try { svg.setPointerCapture(event.pointerId); } catch (_) { /* capture is best-effort */ }
  }
  jsDebugGraphSelectionState = {
    panel,
    svg,
    pointerId: event.pointerId,
    rect: svg.getBoundingClientRect(),
    domain: debugGraphGridDomain(panel),
    startRatio: ratio,
    currentRatio: ratio,
  };
  debugGraphSetInteractionLines(panel, ratio);
  debugGraphSetSelectionRects(panel, ratio, ratio);
  // Touch has no hover-before-press, so surface the value at the touched point
  // immediately on contact (a mouse already shows it from hover).
  debugGraphSetHoverTooltip(panel, event, ratio);
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
  if (selection.pointerId != null && typeof selection.svg?.releasePointerCapture === 'function') {
    try { selection.svg.releasePointerCapture(selection.pointerId); } catch (_) { /* already released */ }
  }
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
    syncDebugGraphResolutionOverride(Date.now(), {persist: true});
    syncJsDebugStatsDeliveryMode();
    refreshDebugGraphSurfaces();
    requestJsDebugHistoryForCurrentDomain();
    for (const graph of document.querySelectorAll('[data-js-debug-graph]')) syncDebugGraphControls(graph);
  } else {
    debugGraphSetInteractionLines(panel, end);
  }
}

function cancelDebugGraphSelection(panel) {
  const selection = jsDebugGraphSelectionState;
  if (selection?.panel !== panel) return;
  if (selection.pointerId != null && typeof selection.svg?.releasePointerCapture === 'function') {
    try { selection.svg.releasePointerCapture(selection.pointerId); } catch (_) { /* already released */ }
  }
  debugGraphClearSelectionRects(panel);
  jsDebugGraphSelectionState = null;
}

function handleDebugGraphControlEvent(event, panel) {
  const costRefresh = event.target.closest('[data-js-debug-cost-refresh]');
  if (event.type === 'click' && costRefresh && panel.contains(costRefresh)) {
    event.preventDefault();
    void refreshDebugCostPricing();
    return true;
  }
  const chartClose = event.target.closest('[data-js-debug-chart-close]');
  // A chart close reflows the grid. Handling it on pointerdown replaces the target before the
  // corresponding pointerup, so that follow-up event can land on another chart's X. Click is the
  // browser's single completed activation and preserves both mouse and keyboard semantics.
  if (event.type === 'click' && chartClose && panel.contains(chartClose)) {
    event.preventDefault();
    setDebugGraphChartVisible(chartClose.dataset.jsDebugChartClose, false);
    return true;
  }
  const chartToggle = event.target.closest('[data-js-debug-chart-toggle]');
  if (event.type === 'click' && chartToggle && panel.contains(chartToggle)) {
    event.preventDefault();
    const chartKey = chartToggle.dataset.jsDebugChartToggle;
    setDebugGraphChartVisible(chartKey, !debugGraphChartVisible(chartKey));
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
  const modelDimension = event.target.closest('[data-js-debug-model-token-dimension-select]');
  if (modelDimension && panel.contains(modelDimension) && event.type === 'change') {
    setDebugGraphModelTokenDimension(modelDimension.value);
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
  bindDebugCostSummaryTabButtons(panel.querySelector('[data-js-debug-graph]'));
  syncDebugSystemPolling({pollNow: jsDebugSubTab === 'system' && !jsDebugSystemState.payload});
  syncDebugLogsPolling({pollNow: jsDebugSubTab === 'logs' && !jsDebugLogsState.updatedAt});
  panel.addEventListener('focusout', event => {
    const graph = event.target?.closest?.('[data-js-debug-graph]');
    if (!graph) return;
    setTimeout(() => { flushDeferredDebugGraphRefresh(graph); }, 0);
  });
  panel.addEventListener('pointerdown', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerDown(event, panel);
  });
  panel.addEventListener('pointermove', event => {
    handleDebugGraphPointerMove(event, panel);
  });
  panel.addEventListener('pointerleave', () => {
    debugGraphClearInteractionLinesUnlessPinned(panel);
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
    const systemRefresh = event.target.closest('[data-js-debug-system-refresh]');
    if (systemRefresh && panel.contains(systemRefresh)) {
      event.preventDefault();
      void pollDebugSystemStatus({force: true});
      return;
    }
    const logLevel = event.target.closest('[data-js-debug-log-level]');
    if (logLevel && panel.contains(logLevel)) {
      event.preventDefault();
      const level = String(logLevel.dataset.jsDebugLogLevel || '');
      if (!jsDebugLogLevels.includes(level)) return;
      if (jsDebugLogsState.levels.has(level)) jsDebugLogsState.levels.delete(level);
      else jsDebugLogsState.levels.add(level);
      saveJsDebugStatsUiPreferences();
      refreshDebugLogsViews();
      return;
    }
    const logsCopy = event.target.closest('[data-js-debug-logs-copy]');
    if (logsCopy && panel.contains(logsCopy)) {
      event.preventDefault();
      copyTextToClipboard(debugLogsTextForClipboard())
        .then(() => { statusEl.textContent = t('debug.copied'); })
        .catch(error => { statusErr(localizedHtml('common.copyFailed', {error})); });
      return;
    }
    const logsClear = event.target.closest('[data-js-debug-logs-clear]');
    if (logsClear && panel.contains(logsClear)) {
      event.preventDefault();
      jsDebugLogsState.clearedAt = Date.now();
      refreshDebugLogsViews();
      statusEl.textContent = t('debug.logs.cleared');
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

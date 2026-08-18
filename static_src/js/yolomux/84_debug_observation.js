// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Debug telemetry state, durable history ingestion, and current-observation upload.

const jsDebugGraphDefaultRangeSeconds = 15 * 60;
const jsDebugGraphGeometry = (() => {
  const width = 600;
  const height = 120;
  const plotTop = 8;
  // Keep zero-valued sampled lines inside the SVG viewBox. A baseline at `height`
  // clips the stroke completely, leaving a legend item without a visible chart line.
  const plotBottom = height - 4;
  const plotHeight = plotBottom - plotTop;
  return Object.freeze({
    width,
    height,
    plotTop,
    plotHeight,
    plotBottom,
    hoverBottom: plotBottom,
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
const jsDebugGraphResolutionWatchdogMs = 3000;
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
const jsDebugSystemPollMs = 5000;
// The retry cadence used while `/api/system-status` answers with a TYPED REFUSAL (no snapshot has
// been published yet, or the newest one is past its freshness deadline and its aged body is
// withheld). The producer rebuilds on demand, so the body a reader is waiting for usually exists
// far sooner than the next 5s poll; waiting a whole poll interval would leave the panel blank on
// first open for the entire cold-start window.
const jsDebugSystemRefusalPollMs = 500;
// The Advanced body's own cadence. It matches `ADVANCED_CADENCE_SECONDS` in
// yolomux_lib/system_status_snapshot.py: asking faster than the producer republishes only re-reads
// the same bytes. It is a MINIMUM AGE, not a second interval -- the one `debug-system` interval
// drives both reads (see `pollDebugSystemStatus`).
const jsDebugSystemAdvancedPollMs = 10000;
const jsDebugLogsPollMs = 5000;
const jsDebugSystemState = {
  payload: null,
  error: '',
  inFlight: false,
  updatedAt: 0,
};
// The Advanced disclosure's body, from `GET /api/system-status/advanced`. It is a SEPARATE ROUTE,
// not a second copy: since the snapshot split, the core body no longer carries `refresh`,
// `top_endpoints`, `top_background_work`, `top_event_types`, `login_throttle`,
// `largest_active_transcripts`, `transcripts_cache` or `owner.debug`/`owner.control`, so this is
// the ONE place those facts live in the client. There is no second poller and no second cache.
const jsDebugSystemAdvancedState = {
  payload: null,
  error: '',
  inFlight: false,
  updatedAt: 0,
};
// Which Daemons roster rows the reader opened, and whether Advanced diagnostics is open. This is
// deliberately NOT read back out of the DOM: the 5s poll re-renders the roster, and DOM-held
// disclosure state would snap every open row shut twice per poll.
const jsDebugSystemRosterState = {
  expanded: new Set(),
  advancedOpen: false,
};
const jsDebugLogLevels = Object.freeze(['info', 'warning', 'debug', 'error']);
// Fresh state hides the chatty info/debug levels; warnings and errors are the
// signal a first-time viewer needs. Info/Debug remain one toggle away and their
// selection persists (see save/load of jsDebugStatsUiPreferences).
const jsDebugLogDefaultLevels = Object.freeze(['warning', 'error']);
// Logs Clear identity is a per-producer (epoch, sequence) cursor, never wall time:
// `serverEpoch`/`serverSequence` track the last validated server envelope, and
// `clearedCursors` holds the {epoch, sequence} captured by the most recent Clear for
// each producer. A record is hidden only while its producer epoch still matches the
// cursor and its sequence is at or below it, so a clock rollback cannot resurface a
// hidden record and a producer epoch reset resurfaces everything.
const jsDebugClientLogEpoch = reloadClientJourneyId;
const jsDebugLogsState = {
  payload: [],
  error: '',
  inFlight: false,
  updatedAt: 0,
  serverEpoch: '',
  serverSequence: 0,
  clearedCursors: {server: null, client: null},
  levels: new Set(jsDebugLogDefaultLevels),
};
// When a Resolution change needs a history fetch, this holds the value to restore and the
// history generation to match so a stale response cannot revert a newer request. Cleared
// on the matching ready (success) or error (revert + toast). Null when the last change was
// served from cache (instant, no overlay).
let jsDebugGraphPendingResolutionChange = null;
const jsDebugStatsPollState = {
  inFlight: false,
  pending: false,
  pendingForceGraphRefresh: false,
  firstSampleReceived: false,
  lastSampleAtMs: 0,
  agentWindowSnapshotRevision: 0,
};
const jsDebugCurrentStatsClientState = {
  client: null,
  selectionKey: '',
  startPromise: null,
  failureLatched: false,
  paintedGenerationKey: '',
  pendingGenerationKey: '',
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
const jsDebugCurrentObservationBatchDelayMs = 10_000;
const jsDebugCurrentObservationHeartbeatMs = 10_000;
const jsDebugCurrentObservationRetryMaxMs = 5 * 60_000;
const jsDebugCurrentObservationState = {
  queue: [],
  keys: new Set(),
  nextHealthId: 1,
  timer: null,
  livenessTimer: null,
  inFlight: false,
  retryMs: 10_000,
  epoch: globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
  highWaterDepth: 0,
  drops: 0,
  retries: 0,
  instrumentationCostMs: 0,
  receipts: new Map(),
};
let jsDebugCurrentObservationLifecycleScope = createLifecycleScope();
function currentObservationLifecycleScope() {
  if (jsDebugCurrentObservationLifecycleScope.disposed()) jsDebugCurrentObservationLifecycleScope = createLifecycleScope();
  return jsDebugCurrentObservationLifecycleScope;
}
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
let jsDebugGraphTouchCandidateState = null;
const jsDebugGraphTouchArmDistancePx = 24;
const jsDebugGraphTouchDirectionRatio = 3;
// A touch must pause before the chart claims a horizontal drag. This keeps
// ordinary iPhone scrolling available while preserving a deliberate zoom path.
const jsDebugGraphTouchHoldMs = 200;
const jsDebugGraphZoomMinRatio = 0.04;
const jsDebugGraphZoomMinBuckets = 3;
// Last pointer type seen on a chart. Touch has no hover-without-contact, so a
// tap pins the value tooltip (it must NOT clear on the pointerleave that fires
// when the finger lifts); a mouse still clears on leave as before.
let jsDebugGraphLastPointerType = 'mouse';
let jsDebugGraphRangeSliderDragging = false;
let jsDebugGraphLiveTimer = 0;
let jsDebugGraphLifecycleScope = createLifecycleScope();
function debugGraphLifecycleScope() {
  if (jsDebugGraphLifecycleScope.disposed()) jsDebugGraphLifecycleScope = createLifecycleScope();
  return jsDebugGraphLifecycleScope;
}
let jsDebugCostAgeNextRefreshAtMs = 0;
let jsDebugCostPanelNextRefreshAtMs = 0;
const jsDebugPricingRefreshState = {inFlight: false, error: '', status: '', timer: null, lastRequestedAtMs: 0};
let jsDebugPricingRefreshLifecycleScope = createLifecycleScope();
function debugPricingRefreshLifecycleScope() {
  if (jsDebugPricingRefreshLifecycleScope.disposed()) jsDebugPricingRefreshLifecycleScope = createLifecycleScope();
  return jsDebugPricingRefreshLifecycleScope;
}
const jsDebugUsageAtomBackfill = {state: 'unknown', sources: 0, missing: 0};
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
// picker. Persisted/deeplinked out-of-set overrides normalize back to AUTO.
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
// The wall-clock slide extends to 1h, independent of the 30m SSE-demand range: a live,
// non-zoomed view up to an hour re-renders ~1/sec so its axis advances and content drifts
// left between the coarser (60s) data fetches — the chart stays visibly live even where
// data no longer streams. Ranges over 1h and fixed historical zooms are static by design.
// A hidden document or a hidden panel is different: neither is inherently static, they simply
// do not repaint, so the axis holds where it was until the tab/document is shown again.
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
    cssKey: key,
    labelKey: jsDebugAgentStatusSeriesLabelKeys[key],
    unit: 'count',
    value: bucket => jsDebugAgentStatusBucketValueGetters[key](bucket),
    hasData: bucket => Number(bucket?.agentActivitySamples || 0) > 0,
  };
}
// One theme-aware series palette for every chart family. System CPU deliberately owns the red
// data identity; its dotted cadence keeps it distinct when red also appears in surrounding chrome.
const jsDebugGraphSeriesPalette = Object.freeze({
  cyan: 'var(--js-debug-agent-token-cyan)',
  orange: 'var(--js-debug-agent-token-orange)',
  magenta: 'var(--js-debug-agent-token-magenta)',
  beige: 'var(--js-debug-agent-token-beige)',
  turquoise: 'var(--js-debug-agent-token-turquoise)',
  rose: 'var(--js-debug-agent-token-rose)',
  violet: 'var(--js-debug-agent-token-violet)',
  systemCpu: 'var(--js-debug-agent-token-rose)',
  currentProcessCpu: 'var(--js-debug-agent-token-violet)',
});
const jsDebugGraphAgentTokenColors = Object.freeze([
  jsDebugGraphSeriesPalette.cyan,
  jsDebugGraphSeriesPalette.orange,
  jsDebugGraphSeriesPalette.magenta,
  jsDebugGraphSeriesPalette.beige,
  jsDebugGraphSeriesPalette.turquoise,
  jsDebugGraphSeriesPalette.rose,
  jsDebugGraphSeriesPalette.violet,
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
  current: jsDebugGraphSeriesPalette.currentProcessCpu,
  peers: Object.freeze([jsDebugGraphSeriesPalette.turquoise, jsDebugGraphSeriesPalette.magenta, jsDebugGraphSeriesPalette.beige]),
});
const jsDebugGraphGpuDeviceColors = Object.freeze([
  jsDebugGraphSeriesPalette.cyan,
  jsDebugGraphSeriesPalette.orange,
  jsDebugGraphSeriesPalette.magenta,
  jsDebugGraphSeriesPalette.turquoise,
]);
// THE one client display cache: every retained bucket of every tier lives here,
// keyed `${startMs}:${durationMs}`. Tier membership is the key's durationMs (the
// jsDebugGraphTiers graduated-compaction owner rewrites keys as buckets age); the
// former raw/rollup Map split was only bookkeeping over the same keyspace.
const jsDebugGraphBuckets = new Map();
// NOT display cache: short-lived staging retained for the established renderer's
// bucket bookkeeping. Current browser observations are uploaded separately.
const jsDebugGraphPendingServerBuckets = new Map();
const jsDebugGraphHoverChartData = new Map();
const jsDebugGraphSeries = Object.freeze([
  ...jsDebugGraphClientMetrics.map(metric => debugGraphClientSeriesDef(metric, {labelKey: 'debug.graph.series.thisClient', clientId: jsDebugGraphThisClientId, clientAggregate: jsDebugGraphThisClientAggregate, clientLinePattern: jsDebugGraphThisClientLinePattern})),
  ...jsDebugAgentStatusSeriesKeys.map(debugGraphAgentStatusSeriesDef),
  {key: 'tokensPerAgent', labelKey: 'debug.graph.series.tokensPerAgent', unit: 'tokensPerMinute', value: bucket => bucket.agentTokenSamples ? bucket.tokensPerAgentTotal / bucket.agentTokenSamples : 0, hasData: bucket => Number(bucket?.agentTokenSamples || 0) > 0},
  {key: 'systemCpu', labelKey: 'debug.graph.series.systemCpu', unit: 'percent', linePattern: 'dot', color: jsDebugGraphSeriesPalette.systemCpu, value: bucket => bucket.systemCpuCount ? bucket.systemCpuTotalPercent / bucket.systemCpuCount : 0, hasData: bucket => Number(bucket?.systemCpuCount || 0) > 0},
  {
    key: 'systemMemory', labelKey: 'debug.graph.series.systemMemory', unit: 'bytes', linePattern: 'solid',
    value: bucket => bucket.hostMetrics?.systemMemoryCount ? bucket.hostMetrics.systemMemoryUsedTotalBytes / bucket.hostMetrics.systemMemoryCount : 0,
    hasData: bucket => Number(bucket?.hostMetrics?.systemMemoryCount || 0) > 0,
    sampleCount: bucket => Number(bucket?.hostMetrics?.systemMemoryCount || 0),
    displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.minuteGauge,
  },
  {
    key: 'macMemoryPressure', label: 'Memory pressure', desc: 'macOS kernel memory pressure. Green means the Mac can satisfy memory demand without significant reclamation; yellow and red indicate increasing pressure.', unit: 'percent', linePattern: 'solid', colorForValue: debugGraphMacMemoryPressureColor,
    value: bucket => bucket.hostMetrics?.macMemoryDetailCount ? bucket.hostMetrics.macMemoryPressureTotalPercent / bucket.hostMetrics.macMemoryDetailCount : 0,
    colorValue: bucket => bucket.hostMetrics?.macMemoryPressureLevel,
    hasData: bucket => Number(bucket?.hostMetrics?.macMemoryDetailCount || 0) > 0 && Number.isFinite(Number(bucket?.hostMetrics?.macMemoryPressureTotalPercent)),
    sampleCount: bucket => Number(bucket?.hostMetrics?.macMemoryDetailCount || 0),
    displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.minuteGauge,
  },
]);
// Mirror of yolomux_lib/stats_current/families.py — the ONE YO!stats family manifest.
// Per family: the canonical name (identical to the server's
// stats_coverage_intervals family), the legacy alias names an OLDER server may
// still write into coverage payloads (canonical is tried first), the true
// sampler cadence, and the owning chart groups / series. Coverage lookups and
// chart->family mapping READ this table; inline per-family if/alias chains
// outside it are contract-banned. tests/stats_current_panel.test.js pins the
// client manifest and chart owner; tests/test_stats_current_families.py pins the server cadence.
const jsDebugStatsFamilyManifest = Object.freeze({
  cpu: Object.freeze({legacyAliases: Object.freeze(['server', 'raw', 'buckets']), cadenceSeconds: 1, chartGroups: Object.freeze(['cpu']), series: Object.freeze(['systemCpu'])}),
  service_load: Object.freeze({legacyAliases: Object.freeze([]), cadenceSeconds: 1, chartGroups: Object.freeze([]), series: Object.freeze([])}),
  agent_status: Object.freeze({legacyAliases: Object.freeze(['status']), cadenceSeconds: 10, chartGroups: Object.freeze(['activity']), series: jsDebugAgentStatusSeriesKeys}),
  agent_tokens: Object.freeze({legacyAliases: Object.freeze(['tokens']), cadenceSeconds: 10, idleCadenceSeconds: 60, chartGroups: Object.freeze(['agentTokens', 'modelTokens']), series: Object.freeze(['tokensPerAgent'])}),
  cost: Object.freeze({legacyAliases: Object.freeze(['cost_atoms', 'usage_atoms']), cadenceSeconds: 10, idleCadenceSeconds: 60, chartGroups: Object.freeze([]), series: Object.freeze([])}),
  gpu: Object.freeze({legacyAliases: Object.freeze(['gpu_metrics']), cadenceSeconds: 10, chartGroups: Object.freeze(['gpuUtil', 'gpuMemory']), series: Object.freeze([])}),
  system_memory: Object.freeze({legacyAliases: Object.freeze(['memory']), cadenceSeconds: 60, chartGroups: Object.freeze(['memory']), series: Object.freeze(['systemMemory', 'macMemoryPressure'])}),
});
const jsDebugStatsFamilyByChartGroup = Object.freeze(Object.fromEntries(Object.entries(jsDebugStatsFamilyManifest)
  .flatMap(([family, entry]) => entry.chartGroups.map(group => [group, family]))));
const jsDebugGraphChartGroups = Object.freeze([
  {key: 'cpu', labelKey: 'debug.graph.chart.cpu', descKey: 'debug.graph.chart.cpu.desc', series: ['systemCpu'], unit: 'percent', hostMetric: 'cpu'},
  {key: 'serversLoad', labelKey: 'debug.graph.chart.serversLoad', descKey: 'debug.graph.chart.serversLoad.desc', series: [], unit: 'percent', serviceLoad: true, bucketSeconds: jsDebugStatsFamilyManifest.service_load.cadenceSeconds},
  {key: 'memory', labelKey: 'debug.graph.chart.memory', descKey: 'debug.graph.chart.memory.desc', series: ['systemMemory'], unit: 'bytes', kind: 'area', stacked: true, hostMetric: 'memory', capacityMetric: 'systemMemory'},
  {key: 'activity', labelKey: 'debug.graph.chart.agentStatus', descKey: 'debug.graph.chart.agentStatus.desc', series: jsDebugAgentStatusSeriesKeys, legendSeries: jsDebugAgentStatusLegendSeriesKeys, unit: 'count', kind: 'bar', stacked: true, integerAxis: true, integerGridLines: true, exactIntegerAxisMax: true, minimumAxisMax: 4, bucketSeconds: jsDebugStatsFamilyManifest.agent_status.cadenceSeconds, statusNoDataOverlay: true},
  {key: 'agentTokens', labelKey: 'debug.graph.chart.agentTokens', descKey: 'debug.graph.chart.agentTokens.desc', series: [], unit: 'tokensPerMinute', kind: 'bar', stacked: true, dynamicAgentTokens: true, displayedSummary: 'agentTokens', bucketSeconds: jsDebugGraphAgentTokenBucketSeconds},
  {key: 'modelTokens', labelKey: 'debug.graph.chart.modelTokens', descKey: 'debug.graph.chart.modelTokens.desc', series: [], unit: 'tokensPerMinute', kind: 'bar', stacked: true, dynamicTokenDimension: 'model', displayedSummary: 'modelTokens', bucketSeconds: jsDebugGraphAgentTokenBucketSeconds},
  {key: 'gpuUtil', labelKey: 'debug.graph.chart.gpuUtil', descKey: 'debug.graph.chart.gpuUtil.desc', series: [], unit: 'percent', fixedMax: 100, kind: 'bar', zeroBar: true, hostMetric: 'gpuUtil'},
  {key: 'gpuMemory', labelKey: 'debug.graph.chart.gpuMemory', descKey: 'debug.graph.chart.gpuMemory.desc', series: [], unit: 'bytes', hostMetric: 'gpuMemory', capacityMetric: 'gpuMemory'},
  {key: 'latency', labelKey: 'common.clientLatency', descKey: 'debug.graph.chart.latency.desc', series: ['latency'], unit: 'ms', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'count', labelKey: 'debug.graph.chart.clientApiSse', descKey: 'debug.graph.chart.clientApiSse.desc', series: ['api', 'sse'], unit: 'countPerSecond', displayedSummary: 'clientRequests', disconnectedOverlay: true, noDataOverlay: true},
  {key: 'bandwidth', labelKey: 'debug.graph.chart.clientBandwidth', descKey: 'debug.graph.chart.clientBandwidth.desc', series: ['bandwidth'], unit: 'bytesPerSecond', displayedSummary: 'bandwidth', disconnectedOverlay: true, noDataOverlay: true},
]);
const jsDebugGraphChartControlItems = Object.freeze(jsDebugGraphChartGroups.flatMap(group => group.key === 'modelTokens'
  ? [group, Object.freeze({key: 'costSummary', labelKey: 'debug.cost.title'})]
  : [group]));

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
  const previousRangeSeconds = Number(state.requestedRangeSeconds) || 0;
  const nextRangeSeconds = Number(debugRuntimeState.graphRangeSeconds) || 0;
  const loadingOlder = Number(state.loadedStartSeconds) > 0
    && previousRangeSeconds > 0
    && nextRangeSeconds > previousRangeSeconds;
  const snapshot = setJsDebugHistoryReadiness('loading', {
    reason: retry ? 'retry' : (loadingOlder ? 'older' : 'initial'),
    requestedRangeSeconds: debugRuntimeState.graphRangeSeconds,
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
  // (finest supported exact cell) when the picker is on AUTO.
  const override = Math.max(0, Number(debugRuntimeState.graphResolutionOverrideSeconds) || 0);
  if (override > 0) return override;
  const choices = debugGraphExactResolutionChoices(activeJsDebugGraphRangeSeconds());
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
    requestedRangeSeconds: debugRuntimeState.graphRangeSeconds,
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
  // Reset is a completed activation, so replacing its focused button is safe and
  // must not leave the visible charts on the retired zoom domain until focusout.
  refreshDebugGraphSurfaces({deferFocusedControl: false});
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

const debugMobileCaptureEventTypes = new Set(['preview_scroll_trace', 'terminal_mobile_input_trace']);

function debugMobileCaptureSnapshot() {
  const screenState = window.screen && typeof window.screen === 'object' ? window.screen : {};
  const viewportState = appViewport();
  const locationState = window.location && typeof window.location === 'object' ? window.location : {};
  const captureUrl = String(locationState.href || `${locationState.protocol || ''}//${locationState.host || ''}${locationState.pathname || ''}${locationState.search || ''}${locationState.hash || ''}`);
  const orientationState = screenState.orientation && typeof screenState.orientation === 'object'
    ? screenState.orientation
    : {};
  const activeItems = activePaneItems().map(item => ({item: String(item), slot: String(slotForItem(item) || '')}));
  const preview = Array.from(document.querySelectorAll('.file-editor-preview-pane-panel')).map(node => {
    const context = previewScrollTraceContext(node) || {};
    const content = node.closest?.('.file-editor-content');
    return {
      item: String(context.item || ''),
      surface: String(context.surface || ''),
      classes: Array.from(node.classList || [], value => String(value)),
      split: content?.classList?.contains('split-preview') === true,
      top: Number(node.scrollTop || 0),
      left: Number(node.scrollLeft || 0),
      scrollHeight: Number(node.scrollHeight || 0),
      clientHeight: Number(node.clientHeight || 0),
    };
  });
  return redactDiagnosticValue({
    capturedAt: new Date().toISOString(),
    url: captureUrl,
    debugArmed: debugModeExplicitUrlEnabled === true,
    codeRevision: jsDebugCodeRevision(),
    userAgent: String(navigator?.userAgent || ''),
    platform: String(navigator?.platform || ''),
    maxTouchPoints: Number(navigator?.maxTouchPoints || 0),
    standalone: navigator?.standalone === true
      || (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches === true),
    orientation: {
      type: String(orientationState.type || ''),
      angle: Number.isFinite(Number(orientationState.angle))
        ? Number(orientationState.angle)
        : (Number.isFinite(Number(window.orientation)) ? Number(window.orientation) : null),
      inferred: Number(viewportState.width || 0) > Number(viewportState.height || 0) ? 'landscape' : 'portrait',
    },
    screen: {
      width: Number(screenState.width || 0),
      height: Number(screenState.height || 0),
      dpr: Number(window.devicePixelRatio || 0),
    },
    viewport: viewportDiagnosticsSnapshot(),
    layout: {
      slots: cloneLayoutSlots(layoutSlots),
      activeItems,
      focusedItem: String(focusedPanelItem || ''),
      visualItem: String(visualActivePaneItem() || ''),
      generation: Number(runtimeState?.layoutMutationGeneration || 0),
      completedGeneration: Number(runtimeState?.layoutMutationCompletedGeneration || 0),
      pendingGeneration: Number(runtimeState?.pendingLayoutMutationGeneration || 0),
    },
    preview,
    focused: viewportDiagnosticsFocusedElementText(),
    events: jsDebugEvents
      .filter(event => debugMobileCaptureEventTypes.has(String(event?.type || '')))
      .map(event => ({...event})),
  });
}

function debugMobileCaptureTextForClipboard(snapshot = debugMobileCaptureSnapshot()) {
  return JSON.stringify(snapshot, null, 2);
}

function debugMetaText() {
  return t('debug.meta', {count: jsDebugEvents.length});
}

function debugStatHtml(label, value, key = '') {
  const data = key ? ` data-js-debug-stat="${esc(key)}"` : '';
  return `<div class="js-debug-stat"><span>${esc(label)}</span><strong${data}>${esc(value)}</strong></div>`;
}

function debugSubTabButtonHtml(tab, label) {
  const active = normalizedJsDebugSubTab(tab) === debugRuntimeState.subTab;
  return toolbarButtonHtml({
    className: `js-debug-subtab${active ? ' active' : ''}`,
    role: 'tab',
    action: 'debug-subtab',
    dataset: {jsDebugSubtab: tab},
    attributes: {'aria-selected': active ? 'true' : 'false'},
    html: `<span class="session-button-dir">${esc(label)}</span>`,
  });
}

function debugEventsSubviewHtml() {
  const counts = debugEventCounts();
  const apiCopyLabel = debugApiCopyButtonLabel();
  const mobileCopyLabel = debugMobileCopyButtonLabel();
  return `<div class="js-debug-subview js-debug-events-view" ${debugSubViewAttrs('events')}>
      <div class="js-debug-toolbar">
        <div class="js-debug-summary" aria-label="${esc(t('debug.summary'))}">
          ${debugStatHtml(t('debug.events'), jsDebugEvents.length, 'events')}
          ${debugStatHtml(t('debug.apiCalls'), counts.apiCalls, 'api')}
          ${debugStatHtml('SSE', counts.sseEvents, 'sse')}
          ${debugStatHtml(t('debug.errors'), counts.errors, 'errors')}
        </div>
        <div class="js-debug-actions">
          ${debugModeExplicitUrlEnabled ? `<button type="button" class="preferences-inline-action" data-js-debug-mobile-copy data-copy-feedback-key="debug-mobile" data-copy-feedback-label="${esc(`${t('common.copy')} ${t('debug.events')}`)}" aria-label="${esc(mobileCopyLabel)}">${esc(mobileCopyLabel)}</button>` : ''}
          <button type="button" class="preferences-inline-action" data-js-debug-copy data-copy-feedback-key="debug-api" data-copy-feedback-label="${esc(t('common.copy'))}" aria-label="${esc(apiCopyLabel)}">${esc(apiCopyLabel)}</button>
          <button type="button" class="preferences-inline-action" data-js-debug-clear>${esc(t('common.clear'))}</button>
        </div>
      </div>
      <textarea class="js-debug-log" data-js-debug-log readonly spellcheck="false" aria-label="${esc(t('debug.recent'))}">${esc(jsDebugTextForClipboard())}</textarea>
    </div>`;
}

function debugSubTabsHtml() {
  loadJsDebugStatsUiPreferences();
  return `<div class="js-debug-subtabs" role="tablist" aria-label="${esc(t('tab.debug'))}">
    ${debugSubTabButtonHtml('graph', t('debug.tab.graph'))}
    ${debugSubTabButtonHtml('cost', t('debug.tab.cost'))}
    ${debugSubTabButtonHtml('events', t('debug.tab.events'))}
    ${debugSubTabButtonHtml('system', t('debug.tab.services'))}
    ${debugSubTabButtonHtml('logs', t('debug.tab.logs'))}
  </div>`;
}

function debugSubViewAttrs(tab) {
  const active = normalizedJsDebugSubTab(tab) === debugRuntimeState.subTab;
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
  return event.message || event.reason || event.error || event.source || '';
}

function debugClientLogLevel(event) {
  if (event?.type === 'stats_history' && jsDebugLogLevels.includes(String(event?.level || ''))) return String(event.level);
  if (event?.type === 'error' || event?.type === 'unhandledrejection' || event?.type === 'client_failure' || event?.error || Number(event?.status || 0) >= 500) return 'error';
  if (Number(event?.status || 0) >= 400 || event?.ok === false) return 'warning';
  if (event?.type === 'sse') return 'debug';
  return 'info';
}

function recordJsDebugStatsDiagnostic(level, message, details = {}) {
  const text = String(message || '').replace(/\s+/g, ' ').trim();
  const displayMessage = /^YO!stats(?::|\s)/.test(text) ? text : `YO!stats: ${text}`;
  const normalizedLevel = String(level || '').toLowerCase();
  const source = jsDebugFailureSource(details.route || details.source || '/');
  recordJsDebugEvent('stats_history', {
    ...details,
    level: normalizedLevel,
    message: displayMessage,
    signature: jsDebugFailureSignature(normalizedLevel, displayMessage, '', source, 0, 0),
  });
}

function debugClientLogRecord(event, index = 0) {
  const redacted = redactDiagnosticValue(event);
  const timestampMs = Date.parse(redacted?.ts || '');
  return {
    id: `client:${redacted?.id ?? index}`,
    owner: 'client',
    sequence: Number(redacted?.id),
    timestamp: Number.isFinite(timestampMs) ? timestampMs / 1000 : 0,
    wallTime: String(redacted?.wallTime || diagnosticPacificWallTime(timestampMs)),
    level: debugClientLogLevel(redacted),
    source: 'browser',
    category: String(redacted?.category || redacted?.type || 'client'),
    message: [debugEventDetailText(redacted), debugEventStatusText(redacted), debugPhaseTimingText(redacted)].filter(Boolean).join(' | '),
    requestId: String(redacted?.requestId || redacted?.request_id || redacted?.request?.id || ''),
    route: String(redacted?.route || redacted?.endpoint || redacted?.source || ''),
    event: String(redacted?.event || redacted?.eventType || redacted?.event_type || ''),
    delivery: String(redacted?.delivery || redacted?.deliveryOutcome || redacted?.delivery_outcome || ''),
  };
}

function debugServerLogRecord(entry, index = 0) {
  const redacted = redactDiagnosticValue(entry);
  const timestampMs = Date.parse(redacted?.ts || '');
  const timestamp = Number(redacted?.timestamp);
  return {
    id: `server:${redacted?.id ?? index}`,
    owner: 'server',
    sequence: Number(redacted?.id),
    timestamp: Number.isFinite(timestamp) ? timestamp : (Number.isFinite(timestampMs) ? timestampMs / 1000 : 0),
    wallTime: String(redacted?.wallTime || diagnosticPacificWallTime(
      Number.isFinite(timestamp) ? timestamp * 1000 : timestampMs,
    )),
    level: jsDebugLogLevels.includes(String(redacted?.level || '')) ? String(redacted.level) : 'info',
    source: String(redacted?.source || 'server'),
    category: String(redacted?.category || 'server'),
    message: String(redacted?.message || ''),
    requestId: String(redacted?.requestId || redacted?.request_id || redacted?.request?.id || ''),
    route: String(redacted?.route || redacted?.endpoint || ''),
    event: String(redacted?.event || redacted?.eventType || redacted?.event_type || ''),
    delivery: String(redacted?.delivery || redacted?.deliveryOutcome || redacted?.delivery_outcome || ''),
  };
}

function debugMergedLogRecords() {
  const server = jsDebugLogsState.payload
    .filter(entry => entry && typeof entry === 'object')
    .map(debugServerLogRecord);
  const client = jsDebugEvents.map(debugClientLogRecord);
  const seen = new Set();
  const cursors = jsDebugLogsState.clearedCursors || {};
  const producerEpoch = owner => owner === 'server' ? jsDebugLogsState.serverEpoch : jsDebugClientLogEpoch;
  return [...server, ...client]
    .filter(entry => {
      if (seen.has(entry.id)) return false;
      seen.add(entry.id);
      return true;
    })
    // Clear hides only records at or below the per-producer cleared sequence, and only while
    // the producer's epoch still matches — a ring/epoch reset resurfaces everything. Wall time
    // is never consulted, so a clock rollback cannot resurface a record hidden by a later Clear.
    .filter(entry => {
      const cursor = cursors[entry.owner];
      if (!cursor) return true;
      return !(cursor.epoch === producerEpoch(entry.owner) && Number(entry.sequence) <= Number(cursor.sequence));
    })
    .sort((a, b) => (Number(b.timestamp || 0) - Number(a.timestamp || 0)) || String(b.id || '').localeCompare(String(a.id || '')))
    .slice(0, 500);
}

// Clear captures the current per-producer high-water sequence under its live epoch, so
// only records visible at Clear time are hidden. A later record above the cursor, a clock
// rollback, or a producer epoch reset all correctly resurface. This is the ONE owner of the
// cleared-cursor identity; nothing consults wall time.
function jsDebugLogRecordCleared() {
  const highWaterSequence = ids => ids.reduce((max, id) => (Number.isSafeInteger(id) && id > max ? id : max), 0);
  jsDebugLogsState.clearedCursors = {
    server: {
      epoch: jsDebugLogsState.serverEpoch,
      sequence: highWaterSequence(jsDebugLogsState.payload.map(entry => Number(entry?.id))),
    },
    client: {
      epoch: jsDebugClientLogEpoch,
      sequence: highWaterSequence(jsDebugEvents.map(event => Number(event?.id))),
    },
  };
}

// Validate a raw /api/logs envelope before it is adopted. A malformed, missing-epoch, or
// sequence-inconsistent envelope is rejected with a bounded reason so the poll can keep the
// last good snapshot and surface the failure visibly. Duplicate or repeated ids are NOT an
// envelope error: the server may legitimately re-emit a record, so the poll stores the raw
// logs and de-duplication happens at render time in debugMergedLogRecords (dedup by id).
function jsDebugValidateServerLogEnvelope(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.ok !== true) {
    return {ok: false, reason: 'malformed envelope'};
  }
  if (typeof payload.epoch !== 'string' || payload.epoch === '') {
    return {ok: false, reason: 'missing epoch'};
  }
  const sequence = Number(payload.sequence);
  if (!Number.isSafeInteger(sequence) || sequence < 0) {
    return {ok: false, reason: 'malformed envelope'};
  }
  if (!Array.isArray(payload.logs)) {
    return {ok: false, reason: 'malformed envelope'};
  }
  for (const entry of payload.logs) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      return {ok: false, reason: 'malformed envelope'};
    }
    const id = Number(entry.id);
    if (!Number.isSafeInteger(id) || id < 0) {
      return {ok: false, reason: 'malformed envelope'};
    }
    // Duplicate or nonmonotonic ids are accepted and stored raw; the render owner
    // (debugMergedLogRecords) de-duplicates by id. Only an id beyond the envelope's own
    // declared sequence is envelope-inconsistent and rejected.
    if (id > sequence) {
      return {ok: false, reason: 'log id exceeds envelope sequence'};
    }
  }
  return {ok: true, epoch: payload.epoch, sequence, logs: payload.logs};
}

function debugVisibleLogRecords() {
  return debugMergedLogRecords().filter(entry => jsDebugLogsState.levels.has(String(entry.level || 'info')));
}

function debugLogTimeText(timestamp) {
  return diagnosticPacificWallTime(Number(timestamp || 0) * 1000);
}

function debugLogsTextForClipboard() {
  return debugVisibleLogRecords().map(entry => [
    debugLogTimeText(entry.timestamp),
    String(entry.level || 'info').toUpperCase().padEnd(7),
    `[${entry.source || 'server'}${entry.category ? `/${entry.category}` : ''}]`,
    entry.message || '',
    entry.requestId ? `request=${entry.requestId}` : '',
    entry.route ? `route=${entry.route}` : '',
    entry.event ? `event=${entry.event}` : '',
    entry.delivery ? `delivery=${entry.delivery}` : '',
  ].filter(Boolean).join(' ')).join('\n');
}

function debugLogsCopyButtonLabel(nowMs = Date.now()) {
  return copyFeedbackLabel('debug-logs', t('common.copy'), nowMs);
}

function debugApiCopyButtonLabel(nowMs = Date.now()) {
  return copyFeedbackLabel('debug-api', t('common.copy'), nowMs);
}

function debugMobileCopyButtonLabel(nowMs = Date.now()) {
  return copyFeedbackLabel('debug-mobile', `${t('common.copy')} ${t('debug.events')}`, nowMs);
}

function runDebugCopy(text, options = {}) {
  return copyTextWithFeedback(text, {statusText: t('debug.copied'), ...options});
}

function debugLogsInnerHtml() {
  const records = debugVisibleLogRecords();
  const copyLabel = debugLogsCopyButtonLabel();
  return `<div class="js-debug-logs-toolbar">
    <div class="js-debug-log-levels" role="group" aria-label="${esc(t('debug.logs.levels'))}">${jsDebugLogLevels.map(level => {
      const active = jsDebugLogsState.levels.has(level);
      return `<button type="button" class="preferences-inline-action js-debug-log-level js-debug-log-level--${esc(level)}${active ? ' active' : ''}" data-js-debug-log-level="${esc(level)}" aria-pressed="${active ? 'true' : 'false'}">${esc(t(`debug.logs.level.${level}`))}</button>`;
    }).join('')}</div>
    <div class="js-debug-actions">
      <button type="button" class="preferences-inline-action" data-js-debug-logs-copy data-copy-feedback-key="debug-logs" data-copy-feedback-label="${esc(t('common.copy'))}" aria-label="${esc(copyLabel)}">${esc(copyLabel)}</button>
      <button type="button" class="preferences-inline-action" data-js-debug-logs-clear>${esc(t('common.clear'))}</button>
    </div>
  </div>
  ${jsDebugLogsState.error ? `<div class="js-debug-logs-error" role="status">${esc(t('debug.logs.loadFailed', {error: jsDebugLogsState.error}))}</div>` : ''}
  <div class="js-debug-log-list" data-js-debug-log-list aria-label="${esc(t('debug.logs.recent'))}" aria-busy="${jsDebugLogsState.inFlight ? 'true' : 'false'}">${records.length ? records.map(entry => {
    const level = jsDebugLogLevels.includes(entry.level) ? entry.level : 'info';
    return `<article class="js-debug-log-row js-debug-log-row--${esc(level)}" data-js-debug-log-entry data-js-debug-log-id="${esc(entry.id || '')}" data-js-debug-log-owner="${esc(entry.owner || '')}" data-level="${esc(level)}">
      <div class="js-debug-log-meta"><time>${esc(debugLogTimeText(entry.timestamp))}</time><span class="js-debug-log-chip">${esc(t(`debug.logs.level.${level}`))}</span><span data-js-debug-log-source>${esc(entry.source || 'server')}</span>${entry.category ? `<span data-js-debug-log-category>${esc(entry.category)}</span>` : ''}${entry.requestId ? `<span data-js-debug-log-request-id>${esc(entry.requestId)}</span>` : ''}${entry.route ? `<span data-js-debug-log-route>${esc(entry.route)}</span>` : ''}${entry.event ? `<span data-js-debug-log-event>${esc(entry.event)}</span>` : ''}${entry.delivery ? `<span data-js-debug-log-delivery>${esc(entry.delivery)}</span>` : ''}</div>
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
    macMemoryDetailCount: 0,
    macPhysicalMemoryTotalBytes: 0,
    macMemoryUsedTotalBytes: 0,
    macCachedFilesTotalBytes: 0,
    macSwapUsedTotalBytes: 0,
    macAppMemoryTotalBytes: 0,
    macWiredMemoryTotalBytes: 0,
    macCompressedMemoryTotalBytes: 0,
    macMemoryPressureTotalPercent: NaN,
    macMemoryPressureLevel: NaN,
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

function debugGraphNewServiceLoadItem(label) {
  return {
    label: String(label || ''),
    cpuTotalPercent: 0,
    cpuSamples: 0,
    cpuMinPercent: 0,
    cpuMaxPercent: 0,
    cpuRangeAvailable: false,
    rssTotalBytes: 0,
    rssSamples: 0,
    rssMinBytes: 0,
    rssMaxBytes: 0,
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
  if (!bucket) return;
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
    totalMicroUsd: 0, apiListMicroUsd: null, totalTokenQuantity: 0, dimensionTotals: null, rangeReport: false, knownMicroUsd: 0, lowerMicroUsd: 0, upperMicroUsd: 0, pricedCount: 0, complete: true, unpricedCount: 0, unpricedTokenQuantity: 0,
    components: [], models: [], sources: [], tmuxWindows: [], catalogRevision: '', activeCatalogRevision: '', freshness: '',
  };
  current.totalMicroUsd += debugGraphCostInteger(source.costSummary.totalMicroUsd) * scale;
  const sourceApiListMicroUsd = debugGraphCostApiListMicroUsd(source.costSummary);
  if (sourceApiListMicroUsd !== null) current.apiListMicroUsd = (current.apiListMicroUsd ?? 0) + sourceApiListMicroUsd * scale;
  current.totalTokenQuantity += Math.max(0, Number(source.costSummary.totalTokenQuantity) || 0) * scale;
  if (source.costSummary.dimensionTotals) {
    current.dimensionTotals ||= {};
    for (const field of [...DEBUG_GRAPH_COST_TOKEN_FIELDS, ...DEBUG_GRAPH_COST_SUBTOTAL_FIELDS]) {
      if (source.costSummary.dimensionTotals[field] === undefined) continue;
      current.dimensionTotals[field] = (Number(current.dimensionTotals[field]) || 0) + Math.max(0, Number(source.costSummary.dimensionTotals[field]) || 0) * scale;
    }
  }
  current.rangeReport = current.rangeReport || source.costSummary.rangeReport === true;
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
    for (const key of ['quantity', 'token_quantity', 'micro_usd', 'total_micro_usd', 'cost_micro_usd', 'api_list_micro_usd', 'total_api_list_micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'output_micro_usd', 'other_micro_usd']) {
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
    targetHost.macMemoryDetailCount += Number(sourceHost.macMemoryDetailCount || 0) * scale;
    for (const key of ['macPhysicalMemoryTotalBytes', 'macMemoryUsedTotalBytes', 'macCachedFilesTotalBytes', 'macSwapUsedTotalBytes', 'macAppMemoryTotalBytes', 'macWiredMemoryTotalBytes', 'macCompressedMemoryTotalBytes']) targetHost[key] += Number(sourceHost[key] || 0) * scale;
    if (Number.isFinite(Number(sourceHost.macMemoryPressureTotalPercent))) targetHost.macMemoryPressureTotalPercent = Number(sourceHost.macMemoryPressureTotalPercent);
    if (Number.isFinite(Number(sourceHost.macMemoryPressureLevel))) targetHost.macMemoryPressureLevel = Math.max(Number(targetHost.macMemoryPressureLevel) || 0, Number(sourceHost.macMemoryPressureLevel));
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
        const item = targetHost.serviceLoad.get(key) || debugGraphNewServiceLoadItem(sourceItem.label || key);
        item.label = sourceItem.label || item.label;
        for (const prefix of ['cpu', 'rss']) {
          const totalKey = `${prefix}Total${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const samplesKey = `${prefix}Samples`;
          const minKey = `${prefix}Min${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const maxKey = `${prefix}Max${prefix === 'cpu' ? 'Percent' : 'Bytes'}`;
          const sourceSamples = Number(sourceItem[samplesKey] || 0) * scale;
          if (sourceSamples <= 0) continue;
          const previousSamples = Number(item[samplesKey] || 0);
          if (prefix === 'cpu') {
            item.cpuRangeAvailable = (previousSamples <= 0 || item.cpuRangeAvailable === true)
              && sourceItem.cpuRangeAvailable === true;
          }
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
    const bucketEndMs = Number(bucket.startMs) + Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
    if (bucketEndMs <= retentionCutoff) {
      jsDebugGraphBuckets.delete(key);
      continue;
    }
    // Exact current snapshots are already the requested uniform resolution.
    // The retained renderer may discard expired buckets, but it must never
    // re-aggregate an exact server cell into an old client-side tier.
    if (jsDebugGraphExactResolutionEnabled) continue;
    const targetDurationMs = debugGraphBucketDurationForTime(bucket.startMs, nowMs);
    if (bucket.durationMs >= targetDurationMs) continue;
    const targetStartMs = Math.floor(bucket.startMs / targetDurationMs) * targetDurationMs;
    const target = debugGraphBucket(jsDebugGraphBuckets, targetStartMs, targetDurationMs);
    debugGraphMergeBucket(target, bucket);
    jsDebugGraphBuckets.delete(key);
  }
}

function recordJsDebugEventForGraph(event) {
  if (!event || typeof event !== 'object') return;
  if (![
    'api', 'sse', 'page_load', 'finder_usable', 'interaction', 'operation_wait',
    'long_task', 'error', 'unhandledrejection', 'client_failure', 'stats_history',
  ].includes(event.type)) return;
  if (event.type === 'stats_history' && !['warning', 'error'].includes(String(event.level || '').toLowerCase())) return;
  const id = Number(event.id);
  if (!Number.isSafeInteger(id) || id < 0) return;
  const key = `${jsDebugCurrentObservationState.epoch}:${id}`;
  queueJsDebugCurrentObservation(key, event);
}

function jsDebugCurrentObservationEventSnapshot(event) {
  const snapshot = {
    ...(event || {}),
    journeyId: String(event?.journeyId || reloadClientJourneyId).slice(0, 96),
    observationCodeRevision: jsDebugCodeRevision(),
    observationBrowserFamily: jsDebugBrowserFamily(),
  };
  if (event?.phaseTimings && typeof event.phaseTimings === 'object') {
    snapshot.phaseTimings = {...event.phaseTimings};
  }
  return snapshot;
}

// Finalize a reserved observation EXACTLY ONCE: snapshot its current live content into the
// immutable upload event, then release the live-event reference so no later measurement can
// rewrite it. A failure's content is already complete, and a restored (pre-finalized) entry
// is never re-snapshotted.
function finalizeJsDebugCurrentObservation(entry) {
  if (!entry || entry.finalized) return entry;
  entry.event = jsDebugCurrentObservationEventSnapshot(entry.liveEvent || entry.event);
  entry.finalized = true;
  entry.liveEvent = null;
  return entry;
}

function queueJsDebugCurrentObservation(key, event) {
  if (!clientCanUseUnscopedHostRequests()) return;
  if (jsDebugCurrentObservationState.keys.has(key)) return;
  const observationEvent = jsDebugCurrentObservationEventSnapshot(event);
  const releaseBlocking = jsDebugCurrentObservationReleaseBlocking(observationEvent);
  if (jsDebugCurrentObservationState.queue.length >= 1000 && !releaseBlocking) {
    jsDebugCurrentObservationState.drops += 1;
    return;
  }
  // Reserve identity immediately, keeping a reference to the live event. A failure reserves
  // its already-complete identity and finalizes at once; a non-failure API observation stays
  // open until its bounded byte/timing enrichment arrives (finalizeJsDebugCurrentObservationBytes)
  // or the flush finalizes whatever it measured. Every other event has no post-queue enrichment,
  // so it finalizes immediately.
  const entry = {key, epoch: jsDebugCurrentObservationState.epoch, event: observationEvent, liveEvent: event, releaseBlocking, finalized: false};
  jsDebugCurrentObservationState.keys.add(entry.key);
  jsDebugCurrentObservationState.queue.push(entry);
  if (releaseBlocking || observationEvent.type !== 'api') finalizeJsDebugCurrentObservation(entry);
  if (releaseBlocking) {
    jsDebugCurrentObservationState.receipts.set(entry.key, {
      key: entry.key,
      epoch: entry.epoch,
      eventId: observationEvent.id,
      ...jsDebugCurrentFailureCorrelation(observationEvent),
      status: 'pending',
    });
    trimJsDebugCurrentObservationReceipts();
    persistJsDebugCurrentObservationReceipts();
  }
  jsDebugCurrentObservationState.highWaterDepth = Math.max(
    jsDebugCurrentObservationState.highWaterDepth,
    jsDebugCurrentObservationState.queue.length,
  );
  scheduleJsDebugCurrentObservationFlush(releaseBlocking ? 0 : jsDebugCurrentObservationBatchDelayMs);
}

function jsDebugCurrentObservationReleaseBlocking(event) {
  return jsDebugFailureClassification(event).releaseBlocking;
}

function jsDebugCurrentFailureCorrelation(event) {
  const redacted = typeof redactDiagnosticValue === 'function'
    ? redactDiagnosticValue(event || {})
    : (event || {});
  const route = jsDebugFailureSource(redacted.route || redacted.endpoint || redacted.url || redacted.source || '/');
  const source = jsDebugFailureSource(redacted.source || route);
  const eventType = String(redacted.eventType || redacted.event || redacted.type || '').replace(/[^A-Za-z0-9._/-]/g, '').slice(0, 64);
  const deliveryOutcome = String(redacted.deliveryOutcome || redacted.delivery || (
    /(?:stalled|missing)/i.test(String(redacted.message || redacted.error || '')) ? 'stalled' : 'failed'
  )).replace(/[^A-Za-z0-9._/-]/g, '').slice(0, 32);
  const status = Number(redacted.status);
  return {
    requestId: String(redacted.requestId || redacted.request_id || '').slice(0, 128),
    source,
    route,
    event: eventType,
    wallTime: String(redacted.wallTime || '').slice(0, 64),
    deliveryOutcome,
    httpStatus: Number.isSafeInteger(status) && status >= 100 && status <= 599 ? status : null,
  };
}

function jsDebugCurrentObservationReceiptBarrier(epoch = null) {
  const selectedEpoch = epoch === null || epoch === undefined ? null : String(epoch || '');
  const result = {
    epoch: selectedEpoch === null ? 'all' : selectedEpoch,
    accepted: 0, pending: 0, retrying: 0, rejected: 0, dropped: 0,
    quiescent: true, blocking: [],
  };
  for (const receipt of jsDebugCurrentObservationState.receipts.values()) {
    if (selectedEpoch !== null && receipt.epoch !== selectedEpoch && receipt.globalBlocker !== true) continue;
    if (receipt.status === 'accepted') result.accepted += 1;
    else if (receipt.status === 'pending') result.pending += 1;
    else if (receipt.status === 'retrying') result.retrying += 1;
    else if (receipt.status === 'rejected') result.rejected += 1;
    else if (receipt.status === 'dropped') result.dropped += 1;
    if (receipt.status !== 'accepted') {
      result.quiescent = false;
      result.blocking.push({...receipt});
    }
  }
  return result;
}

function jsDebugCurrentObservationReceiptProjection(epoch = null) {
  const receipts = jsDebugCurrentObservationReceiptJournal().map(receipt => ({...receipt}));
  const barrier = jsDebugCurrentObservationReceiptBarrier(epoch);
  if (receipts.length > jsDebugCurrentObservationReceiptStorageLimit
      || receipts.some(receipt => !jsDebugCurrentObservationReceiptValid(receipt))) return null;
  const selectedEpoch = epoch === null || epoch === undefined ? null : String(epoch || '');
  const selected = receipts.filter(receipt => selectedEpoch === null || receipt.epoch === selectedEpoch || receipt.globalBlocker === true);
  const counts = {accepted: 0, pending: 0, retrying: 0, rejected: 0, dropped: 0};
  for (const receipt of selected) counts[receipt.status] += 1;
  if (Object.keys(counts).some(status => counts[status] !== barrier[status])) return null;
  return {receipts, barrier};
}

const jsDebugCurrentObservationReceiptStorageKey = 'yolomux.current-observation-receipts.v1';
const jsDebugCurrentObservationReceiptFallbackKey = `${jsDebugCurrentObservationReceiptStorageKey}.fallback`;
const jsDebugCurrentObservationReceiptFailureKey = `${jsDebugCurrentObservationReceiptStorageKey}.failure`;
const jsDebugCurrentObservationReceiptProbeKey = `${jsDebugCurrentObservationReceiptStorageKey}.probe`;
const jsDebugCurrentObservationReceiptFailureReceiptKey = '__yolomux_receipt_storage_failure__';
const jsDebugCurrentObservationReceiptOverflowKey = '__yolomux_receipt_journal_overflow__';
const jsDebugCurrentObservationReceiptStorageLimit = 500;
const jsDebugCurrentObservationReceiptStatuses = new Set(['accepted', 'pending', 'retrying', 'rejected', 'dropped']);
const jsDebugCurrentObservationReceiptFields = new Set([
  'key', 'epoch', 'eventId', 'requestId', 'source', 'route', 'event', 'wallTime',
  'deliveryOutcome', 'httpStatus', 'status',
]);
const jsDebugCurrentObservationReceiptOverflowFields = new Set([
  ...jsDebugCurrentObservationReceiptFields, 'globalBlocker', 'journalOverflow', 'omitted',
]);

function jsDebugCurrentObservationReceiptStorages() {
  const stores = [];
  for (const storage of [globalThis.sessionStorage, globalThis.localStorage]) {
    if (!storage || stores.includes(storage)) continue;
    stores.push(storage);
  }
  return stores;
}

function jsDebugCurrentObservationStorageFailureReceipt(reason) {
  return {
    key: jsDebugCurrentObservationReceiptFailureReceiptKey,
    epoch: '*',
    eventId: null,
    requestId: '',
    source: '/',
    route: '/',
    event: 'receipt_storage_failure',
    wallTime: '',
    deliveryOutcome: 'failed',
    httpStatus: null,
    status: 'dropped',
    globalBlocker: true,
    storageFailure: String(reason || 'unknown').replace(/[^A-Za-z0-9._/-]/g, '').slice(0, 64) || 'unknown',
  };
}

function markJsDebugCurrentObservationStorageFailure(reason, {corrupt = false} = {}) {
  const state = jsDebugCurrentObservationState;
  state.receiptStorageFailure = String(reason || 'unknown');
  state.receiptStorageCorrupt = state.receiptStorageCorrupt === true || corrupt === true;
  state.receipts.set(
    jsDebugCurrentObservationReceiptFailureReceiptKey,
    jsDebugCurrentObservationStorageFailureReceipt(state.receiptStorageFailure),
  );
  const marker = JSON.stringify({schema: 1, reason: state.receiptStorageFailure});
  for (const storage of jsDebugCurrentObservationReceiptStorages()) {
    try {
      storage.setItem(jsDebugCurrentObservationReceiptFailureKey, marker);
    } catch (_error) {
      // Every reload probes both stores and recreates the global blocker while a store remains unwritable.
    }
  }
}

function jsDebugCurrentObservationReceiptStorageHealthy() {
  const stores = jsDebugCurrentObservationReceiptStorages();
  if (!stores.length) return false;
  let healthy = true;
  for (const storage of stores) {
    try {
      storage.setItem(jsDebugCurrentObservationReceiptProbeKey, '1');
      if (storage.getItem(jsDebugCurrentObservationReceiptProbeKey) !== '1') healthy = false;
      storage.removeItem(jsDebugCurrentObservationReceiptProbeKey);
    } catch (_error) {
      healthy = false;
    }
  }
  return healthy;
}

function jsDebugCurrentObservationReceiptHasExactFields(receipt, fields) {
  const keys = Object.keys(receipt);
  return keys.length === fields.size && keys.every(key => fields.has(key));
}

function jsDebugCurrentObservationReceiptBoundedText(value, maximum, {empty = true, token = false} = {}) {
  if (typeof value !== 'string' || value.length > maximum || (!empty && !value)) return false;
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return !token || /^[A-Za-z0-9._/-]+$/.test(value);
}

function jsDebugCurrentObservationReceiptIdValid(value, maximum) {
  return typeof value === 'string' && value.length >= 1 && value.length <= maximum
    && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value);
}

function jsDebugCurrentObservationReceiptCorrelationValid(receipt) {
  return Number.isSafeInteger(receipt.eventId) && receipt.eventId >= 0
    && jsDebugCurrentObservationReceiptBoundedText(receipt.requestId, 128)
    && jsDebugCurrentObservationReceiptBoundedText(receipt.source, 240, {empty: false})
    && jsDebugCurrentObservationReceiptBoundedText(receipt.route, 240, {empty: false})
    && jsDebugCurrentObservationReceiptBoundedText(receipt.event, 64, {empty: false, token: true})
    && jsDebugCurrentObservationReceiptBoundedText(receipt.wallTime, 64)
    && jsDebugCurrentObservationReceiptBoundedText(receipt.deliveryOutcome, 32, {empty: false, token: true})
    && (receipt.httpStatus === null || (
      Number.isSafeInteger(receipt.httpStatus) && receipt.httpStatus >= 100 && receipt.httpStatus <= 599
    ));
}

function jsDebugCurrentObservationNormalReceiptValid(receipt) {
  if (!jsDebugCurrentObservationReceiptHasExactFields(receipt, jsDebugCurrentObservationReceiptFields)
    || !jsDebugCurrentObservationReceiptIdValid(receipt.epoch, 128)
    || receipt.epoch === '*'
    || receipt.key !== `${receipt.epoch}:${receipt.eventId}`
    || !jsDebugCurrentObservationReceiptStatuses.has(receipt.status)) return false;
  return jsDebugCurrentObservationReceiptCorrelationValid(receipt);
}

function jsDebugCurrentObservationOverflowReceiptValid(receipt) {
  return jsDebugCurrentObservationReceiptHasExactFields(receipt, jsDebugCurrentObservationReceiptOverflowFields)
    && receipt.key === jsDebugCurrentObservationReceiptOverflowKey
    && receipt.epoch === '*'
    && receipt.eventId === null
    && receipt.requestId === ''
    && receipt.source === '/'
    && receipt.route === '/'
    && receipt.event === 'receipt_journal_overflow'
    && receipt.wallTime === ''
    && receipt.deliveryOutcome === 'dropped'
    && receipt.httpStatus === null
    && receipt.status === 'dropped'
    && receipt.globalBlocker === true
    && receipt.journalOverflow === true
    && Number.isSafeInteger(receipt.omitted)
    && receipt.omitted >= 1;
}

function jsDebugCurrentObservationReceiptValid(receipt) {
  if (!receipt || typeof receipt !== 'object' || Array.isArray(receipt)) return false;
  return receipt.key === jsDebugCurrentObservationReceiptOverflowKey
    ? jsDebugCurrentObservationOverflowReceiptValid(receipt)
    : jsDebugCurrentObservationNormalReceiptValid(receipt);
}

function jsDebugCurrentObservationReceiptJournalValid(saved) {
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)
    || !Array.isArray(saved.entries) || !Array.isArray(saved.receipts)
    || saved.entries.length > jsDebugCurrentObservationReceiptStorageLimit
    || saved.receipts.length > jsDebugCurrentObservationReceiptStorageLimit) return false;
  const receiptKeys = new Set();
  const receiptsByKey = new Map();
  for (const receipt of saved.receipts) {
    if (!jsDebugCurrentObservationReceiptValid(receipt) || receiptKeys.has(receipt.key)) return false;
    receiptKeys.add(receipt.key);
    receiptsByKey.set(receipt.key, receipt);
  }
  const entryKeys = new Set();
  for (const entry of saved.entries) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)
      || typeof entry.key !== 'string' || !entry.key
      || typeof entry.epoch !== 'string' || !entry.epoch
      || !entry.event || typeof entry.event !== 'object' || Array.isArray(entry.event)
      || entry.releaseBlocking !== true || entryKeys.has(entry.key) || !receiptKeys.has(entry.key)
      || receiptsByKey.get(entry.key)?.epoch !== entry.epoch
      || !['pending', 'retrying'].includes(receiptsByKey.get(entry.key)?.status)) return false;
    entryKeys.add(entry.key);
  }
  return true;
}

function jsDebugCurrentObservationReceiptRecordCanonical(value) {
  if (Array.isArray(value)) return value.map(jsDebugCurrentObservationReceiptRecordCanonical);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map(key => [
    key, jsDebugCurrentObservationReceiptRecordCanonical(value[key]),
  ]));
}

function jsDebugCurrentObservationReceiptRecordsEqual(left, right) {
  return JSON.stringify(jsDebugCurrentObservationReceiptRecordCanonical(left))
    === JSON.stringify(jsDebugCurrentObservationReceiptRecordCanonical(right));
}

function trimJsDebugCurrentObservationReceipts() {
  const receipts = jsDebugCurrentObservationState.receipts;
  const acceptedKeys = [...receipts]
    .filter(([_key, receipt]) => receipt?.status === 'accepted')
    .map(([key]) => key);
  while (acceptedKeys.length > jsDebugCurrentObservationReceiptStorageLimit) {
    receipts.delete(acceptedKeys.shift());
  }
}

function jsDebugCurrentObservationReceiptJournal() {
  const values = [...jsDebugCurrentObservationState.receipts.values()]
    .filter(receipt => receipt?.key !== jsDebugCurrentObservationReceiptFailureReceiptKey);
  const blocking = values.filter(receipt => receipt?.status !== 'accepted');
  const accepted = values.filter(receipt => receipt?.status === 'accepted');
  if (blocking.length > jsDebugCurrentObservationReceiptStorageLimit) {
    const retained = blocking.slice(0, jsDebugCurrentObservationReceiptStorageLimit - 1);
    retained.push({
      key: jsDebugCurrentObservationReceiptOverflowKey,
      epoch: '*',
      eventId: null,
      requestId: '',
      source: '/',
      route: '/',
      event: 'receipt_journal_overflow',
      wallTime: '',
      deliveryOutcome: 'dropped',
      httpStatus: null,
      status: 'dropped',
      journalOverflow: true,
      globalBlocker: true,
      omitted: blocking.length - retained.length,
    });
    return retained;
  }
  const acceptedCapacity = jsDebugCurrentObservationReceiptStorageLimit - blocking.length;
  return [...blocking, ...(acceptedCapacity > 0 ? accepted.slice(-acceptedCapacity) : [])];
}

function jsDebugCurrentObservationReceiptJournalPayload() {
  const receipts = jsDebugCurrentObservationReceiptJournal();
  const persistedKeys = new Set(receipts.map(receipt => receipt.key));
  const entries = jsDebugCurrentObservationState.queue
    .filter(entry => entry.releaseBlocking && persistedKeys.has(entry.key))
    .map(entry => ({key: entry.key, epoch: entry.epoch, event: entry.event, releaseBlocking: true}));
  return {entries, receipts};
}

function persistJsDebugCurrentObservationReceipts() {
  const state = jsDebugCurrentObservationState;
  if (state.receiptStorageCorrupt === true) {
    markJsDebugCurrentObservationStorageFailure(state.receiptStorageFailure || 'corrupt', {corrupt: true});
    return false;
  }
  const {entries, receipts} = jsDebugCurrentObservationReceiptJournalPayload();
  const encoded = JSON.stringify({entries, receipts});
  let primaryWritten = false;
  try {
    if (!globalThis.sessionStorage) throw new Error('session storage unavailable');
    if (!entries.length && !receipts.length) sessionStorage.removeItem(jsDebugCurrentObservationReceiptStorageKey);
    else sessionStorage.setItem(jsDebugCurrentObservationReceiptStorageKey, encoded);
    primaryWritten = true;
  } catch (_error) {
    try {
      if (!globalThis.localStorage) throw new Error('local storage unavailable');
      localStorage.setItem(jsDebugCurrentObservationReceiptFallbackKey, encoded);
    } catch (_fallbackError) {
      // The storage capability probe recreates the blocker on every reload while both stores remain unwritable.
    }
    markJsDebugCurrentObservationStorageFailure('write_failed');
    return false;
  }
  if (!jsDebugCurrentObservationReceiptStorageHealthy()) {
    markJsDebugCurrentObservationStorageFailure('storage_unwritable');
    return false;
  }
  if (state.receiptStorageFailure) {
    try {
      for (const storage of jsDebugCurrentObservationReceiptStorages()) {
        storage.removeItem(jsDebugCurrentObservationReceiptFailureKey);
        storage.removeItem(jsDebugCurrentObservationReceiptFallbackKey);
      }
    } catch (_error) {
      markJsDebugCurrentObservationStorageFailure('recovery_cleanup_failed');
      return false;
    }
    state.receipts.delete(jsDebugCurrentObservationReceiptFailureReceiptKey);
    state.receiptStorageFailure = '';
  }
  return primaryWritten;
}

function restoreJsDebugCurrentObservationReceipts() {
  if (!clientCanUseUnscopedHostRequests()) return;
  const state = jsDebugCurrentObservationState;
  const savedJournals = [];
  const seenRaw = new Set();
  const restoredEntries = new Map();
  for (const storage of jsDebugCurrentObservationReceiptStorages()) {
    try {
      if (storage.getItem(jsDebugCurrentObservationReceiptFailureKey) !== null) {
        markJsDebugCurrentObservationStorageFailure('restored_failure_marker');
      }
    } catch (_error) {
      markJsDebugCurrentObservationStorageFailure('marker_read_failed');
    }
  }
  for (const [storage, key] of [
    [globalThis.sessionStorage, jsDebugCurrentObservationReceiptStorageKey],
    [globalThis.localStorage, jsDebugCurrentObservationReceiptFallbackKey],
  ]) {
    if (!storage) continue;
    let raw;
    try {
      raw = storage.getItem(key);
    } catch (_error) {
      markJsDebugCurrentObservationStorageFailure('journal_read_failed');
      continue;
    }
    if (raw === null || raw === undefined || raw === '' || seenRaw.has(raw)) continue;
    seenRaw.add(raw);
    let saved;
    try {
      saved = JSON.parse(raw);
    } catch (_error) {
      markJsDebugCurrentObservationStorageFailure('journal_parse_failed', {corrupt: true});
      continue;
    }
    if (!jsDebugCurrentObservationReceiptJournalValid(saved)) {
      markJsDebugCurrentObservationStorageFailure('journal_schema_failed', {corrupt: true});
      continue;
    }
    savedJournals.push(saved);
  }
  for (const saved of savedJournals) {
    for (const receipt of saved.receipts) {
      const existing = state.receipts.get(receipt.key);
      if (existing && !jsDebugCurrentObservationReceiptRecordsEqual(existing, receipt)) {
        markJsDebugCurrentObservationStorageFailure('journal_conflict', {corrupt: true});
        continue;
      }
      state.receipts.set(receipt.key, {...receipt});
    }
    trimJsDebugCurrentObservationReceipts();
    for (const entry of saved.entries) {
      const existing = restoredEntries.get(entry.key);
      if (existing) {
        if (!jsDebugCurrentObservationReceiptRecordsEqual(existing, entry)) {
          markJsDebugCurrentObservationStorageFailure('journal_conflict', {corrupt: true});
        }
        continue;
      }
      restoredEntries.set(entry.key, entry);
      state.keys.add(entry.key);
      // A restored receipt journal carries already-finalized, immutable content; it is never
      // re-snapshotted, so it has no live event and is marked finalized on adoption.
      state.queue.push({key: entry.key, epoch: entry.epoch, event: entry.event, liveEvent: null, releaseBlocking: true, finalized: true});
    }
  }
  if (!jsDebugCurrentObservationReceiptStorageHealthy()) {
    markJsDebugCurrentObservationStorageFailure('storage_unwritable');
  }
  if (state.queue.length) scheduleJsDebugCurrentObservationFlush(0);
}

function setJsDebugCurrentObservationReceipt(entries, status) {
  for (const entry of entries) {
    const receipt = jsDebugCurrentObservationState.receipts.get(entry.key);
    if (receipt) receipt.status = status;
  }
  persistJsDebugCurrentObservationReceipts();
}

restoreJsDebugCurrentObservationReceipts();

for (const event of jsDebugEvents) recordJsDebugEventForGraph(event);

function recordJsDebugClientHealthObservation(latencyMs, bandwidthBytes, sampleTimeMs = Date.now()) {
  const id = jsDebugCurrentObservationState.nextHealthId++;
  queueJsDebugCurrentObservation(
    `${jsDebugCurrentObservationState.epoch}:health:${id}`,
    {
      type: 'heartbeat',
      ts: new Date(sampleTimeMs).toISOString(),
      journeyId: reloadClientJourneyId,
      durationMs: latencyMs,
      bytes: bandwidthBytes,
      uploadQueueDepth: jsDebugCurrentObservationState.queue.length,
      uploadDrops: jsDebugCurrentObservationState.drops,
      uploadRetries: jsDebugCurrentObservationState.retries,
      instrumentationCostMs: jsDebugRoundedMs(jsDebugCurrentObservationState.instrumentationCostMs),
    },
  );
}

function installJsDebugCurrentObservationLiveness() {
  const state = jsDebugCurrentObservationState;
  if (!clientCanUseUnscopedHostRequests() || state.livenessTimer !== null || typeof setInterval !== 'function') return;
  const scope = currentObservationLifecycleScope();
  recordJsDebugClientHealthObservation(0, 0);
  const timer = setInterval(() => {
    if (!scope.current() || state.livenessTimer !== timer) return;
    recordJsDebugClientHealthObservation(0, 0);
  }, jsDebugCurrentObservationHeartbeatMs);
  state.livenessTimer = timer;
  scope.ownTimer('liveness', timer, clearInterval);
}

function disposeJsDebugCurrentObservationLifecycle(reason = 'disposed') {
  const state = jsDebugCurrentObservationState;
  jsDebugCurrentObservationLifecycleScope.dispose(reason);
  state.timer = null;
  state.livenessTimer = null;
  state.inFlight = null;
}

installJsDebugCurrentObservationLiveness();

function jsDebugBrowserFamily() {
  const userAgent = String(navigator.userAgent || '').toLowerCase();
  if (userAgent.includes('firefox/')) return 'firefox';
  if (userAgent.includes('safari/') && !userAgent.includes('chrome/') && !userAgent.includes('chromium/')) return 'safari';
  if (userAgent.includes('chrome/') || userAgent.includes('chromium/') || userAgent.includes('edg/')) return 'chromium';
  return 'other';
}

function jsDebugCodeRevision() {
  const revision = String(bootstrap.clientRevision || bootstrap.devBundleRevision || bootstrap.version || 'unknown');
  return /^[A-Za-z0-9._-]{1,80}$/.test(revision) ? revision : 'unknown';
}

function jsDebugBoundedToken(value, maximum = 64) {
  const text = String(value || '').trim();
  return /^[A-Za-z0-9._/-]+$/.test(text) ? text.slice(0, maximum) : '';
}

function jsDebugCurrentObservationFromEvent(entry) {
  const instrumentationStartedAt = performanceNow();
  const event = entry.event;
  const failure = jsDebugFailureClassification(event);
  const observedAt = Date.parse(event.ts) / 1000;
  if (!Number.isFinite(observedAt) || observedAt < 0) return null;
  const payload = {
    kind: event.type,
    journey_id: String(event.journeyId || reloadClientJourneyId).slice(0, 96),
    code_revision: String(event.observationCodeRevision || jsDebugCodeRevision()),
    browser_family: String(event.observationBrowserFamily || jsDebugBrowserFamily()),
  };
  const latency = Number(event.type === 'sse' ? event.receiveLatencyMs : event.durationMs);
  if (Number.isFinite(latency) && latency >= 0) payload.latency_ms = latency;
  const bytes = event.type === 'api'
    ? Number(event.requestBytes || 0) + Number(event.responseBytes || 0)
    : Number(event.type === 'heartbeat' ? event.bytes : (event.frameBytes === undefined ? event.bytes : event.frameBytes));
  if (Number.isFinite(bytes) && bytes >= 0) payload.bytes = bytes;
  if (event.type === 'api') {
    payload.endpoint = jsDebugEndpointText(event.endpoint || event.url);
    payload.method = String(event.method || 'GET').toUpperCase().slice(0, 16);
    if (event.requestId) payload.request_id = String(event.requestId).slice(0, 128);
    const connectionProtocol = jsDebugBoundedToken(event.connectionProtocol, 24);
    if (connectionProtocol) payload.connection_protocol = connectionProtocol;
    const status = Number(event.status);
    if (Number.isSafeInteger(status) && status >= 100 && status <= 599) payload.status = status;
  } else if (event.type === 'page_load') {
    payload.endpoint = jsDebugEndpointText(event.url || window.location.pathname);
    const fanoutCount = Number(event.fanoutCount);
    if (Number.isSafeInteger(fanoutCount) && fanoutCount >= 0) payload.fanout_count = fanoutCount;
    const maxConcurrency = Number(event.maxConcurrency);
    if (Number.isSafeInteger(maxConcurrency) && maxConcurrency >= 0) payload.max_concurrency = maxConcurrency;
  } else if (event.type === 'finder_usable') {
    const entryCount = Number(event.entryCount);
    if (Number.isSafeInteger(entryCount) && entryCount >= 0) payload.entry_count = entryCount;
  } else if (event.type === 'interaction') {
    const interactionType = jsDebugBoundedToken(event.interactionType, 32);
    if (interactionType) payload.interaction_type = interactionType;
  } else if (event.type === 'operation_wait') {
    const operationKind = jsDebugBoundedToken(event.operationKind, 64);
    const outcome = jsDebugBoundedToken(event.outcome, 16);
    if (operationKind) payload.operation_kind = operationKind;
    if (outcome) payload.outcome = outcome;
    if (event.requestId) payload.request_id = String(event.requestId).slice(0, 128);
  } else if (event.type === 'stats_history' && !failure.releaseBlocking) {
    return null;
  }
  if (failure.releaseBlocking) {
    const failureKind = failure.observationKind;
    const correlation = jsDebugCurrentFailureCorrelation(event);
    const line = Math.max(0, Math.trunc(Number(event.line) || 0));
    const column = Math.max(0, Math.trunc(Number(event.column) || 0));
    payload.kind = failureKind;
    const producerSignature = String(event.signature || '');
    payload.signature = event.type === 'stats_history' && /^jsf-[0-9a-f]{8}$/.test(producerSignature)
      ? producerSignature
      : jsDebugFailureSignature(
        failureKind, event.message || event.error, event.stack, correlation.source, line, column,
      );
    payload.message = jsDebugFailureText(event.message || event.error || `HTTP ${correlation.httpStatus || 'failure'}`);
    const stack = jsDebugFailureStack({stack: event.stack});
    if (stack) payload.stack = stack;
    payload.source = correlation.source;
    if (correlation.requestId) payload.request_id = correlation.requestId;
    if (correlation.route) payload.route = correlation.route;
    if (correlation.event) payload.event_type = correlation.event;
    if (correlation.wallTime) payload.wall_time = correlation.wallTime;
    if (correlation.deliveryOutcome) payload.delivery_outcome = correlation.deliveryOutcome;
    if (correlation.httpStatus !== null) payload.status = correlation.httpStatus;
    const provenance = event.provenance === 'controlled_probe' || event.provenance === 'confirmed_real'
      ? event.provenance
      : '';
    if (provenance) payload.provenance = provenance;
    if (line) payload.line = line;
    if (column) payload.column = column;
  }
  const phaseFields = {
    queueMs: 'queue_ms',
    connectMs: 'connect_ms',
    tlsMs: 'tls_ms',
    ttfbMs: 'ttfb_ms',
    downloadMs: 'download_ms',
    applyRenderMs: 'apply_render_ms',
    navigationMs: 'navigation_ms',
    bundleParseEvalMs: 'bundle_parse_eval_ms',
    firstApiMs: 'first_api_ms',
    fanoutMs: 'fanout_ms',
    interactiveMs: 'interactive_ms',
    firstPaintMs: 'first_paint_ms',
    firstContentfulPaintMs: 'first_contentful_paint_ms',
    appReadyMs: 'app_ready_ms',
  };
  for (const [source, target] of Object.entries(phaseFields)) {
    const rawValue = event.phaseTimings?.[source];
    if (rawValue === null || rawValue === undefined) continue;
    const value = Number(rawValue);
    if (Number.isFinite(value) && value >= 0) payload[target] = value;
  }
  const perceptualFields = {
    inputDelayMs: 'input_delay_ms',
    processingMs: 'processing_ms',
    presentationDelayMs: 'presentation_delay_ms',
  };
  for (const [source, target] of Object.entries(perceptualFields)) {
    const rawValue = event[source];
    if (rawValue === null || rawValue === undefined) continue;
    const value = Number(rawValue);
    if (Number.isFinite(value) && value >= 0) payload[target] = value;
  }
  const healthFields = {
    uploadQueueDepth: 'upload_queue_depth',
    uploadDrops: 'upload_drops',
    uploadRetries: 'upload_retries',
    instrumentationCostMs: 'instrumentation_cost_ms',
  };
  for (const [source, target] of Object.entries(healthFields)) {
    const rawValue = event[source];
    if (rawValue === null || rawValue === undefined) continue;
    const value = Number(rawValue);
    if (Number.isFinite(value) && value >= 0) payload[target] = value;
  }
  if (failure.releaseBlocking) {
    const failureFields = new Set([
      'kind', 'journey_id', 'code_revision', 'browser_family', 'signature', 'message', 'stack',
      'source', 'line', 'column', 'provenance', 'request_id', 'route', 'event_type',
      'wall_time', 'delivery_outcome', 'status',
    ]);
    for (const key of Object.keys(payload)) {
      if (!failureFields.has(key)) delete payload[key];
    }
  }
  jsDebugCurrentObservationState.instrumentationCostMs += Math.max(0, performanceNow() - instrumentationStartedAt);
  return {
    event_id: entry.key,
    family: 'browser',
    source_id: jsDebugStatsClientIdForRequest(),
    observed_at: observedAt,
    epoch_id: entry.epoch || jsDebugCurrentObservationState.epoch,
    payload,
  };
}

const jsDebugObservationUploadMaxBytes = 120 * 1024;
const jsDebugObservationUploadMaxItems = 100;

function jsDebugObservationBatchForEntries(entries, fence = statsWriterFence) {
  const root = {
    protocol_version: fence.protocolVersion,
    schema_generation: fence.schemaGeneration,
    client_id: jsDebugStatsClientIdForRequest(),
    observations: [],
  };
  let encodedBytes = utf8ByteLength(JSON.stringify(root));
  for (const entry of entries.slice(0, jsDebugObservationUploadMaxItems)) {
    const observation = jsDebugCurrentObservationFromEvent(entry);
    if (!observation) continue;
    const observationBytes = utf8ByteLength(JSON.stringify(observation)) + (root.observations.length ? 1 : 0);
    if (encodedBytes + observationBytes > jsDebugObservationUploadMaxBytes) break;
    root.observations.push(observation);
    encodedBytes += observationBytes;
  }
  return root;
}

function scheduleJsDebugCurrentObservationFlush(delay = jsDebugCurrentObservationBatchDelayMs) {
  const state = jsDebugCurrentObservationState;
  if (!clientCanUseUnscopedHostRequests() || state.inFlight || !state.queue.length) return;
  if (state.timer !== null) {
    if (delay !== 0) return;
    clearTimeout(state.timer);
    currentObservationLifecycleScope().release('flush', state.timer);
    state.timer = null;
  }
  const scope = currentObservationLifecycleScope();
  const timer = setTimeout(() => {
    if (!scope.current() || state.timer !== timer) return;
    scope.relinquish('flush', timer);
    state.timer = null;
    void flushJsDebugCurrentObservations(scope);
  }, delay);
  state.timer = timer;
  scope.ownTimer('flush', timer);
}

async function flushJsDebugCurrentObservations(scope = currentObservationLifecycleScope()) {
  const state = jsDebugCurrentObservationState;
  if (!clientCanUseUnscopedHostRequests()) return;
  if (statsWriterFence === null) {
    state.retries += 1;
    const delay = state.retryMs;
    state.retryMs = Math.min(jsDebugCurrentObservationRetryMaxMs, state.retryMs * 2);
    scheduleJsDebugCurrentObservationFlush(delay);
    return;
  }
  if (state.inFlight) return state.inFlight;
  if (!state.queue.length || typeof apiFetchJsonQuiet !== 'function') return;
  // Any observation still open at flush time (e.g. an API response that never reported bytes)
  // finalizes with whatever it measured, so its content is immutable before it is uploaded.
  for (const entry of state.queue) finalizeJsDebugCurrentObservation(entry);
  const entries = [...state.queue.filter(entry => entry.releaseBlocking), ...state.queue.filter(entry => !entry.releaseBlocking)]
    .slice(0, jsDebugObservationUploadMaxItems);
  const prepared = entries.map(entry => ({entry, observation: jsDebugCurrentObservationFromEvent(entry)}));
  let validEntries = prepared.filter(item => item.observation);
  for (const {entry} of prepared.filter(item => !item.observation)) {
    state.queue.splice(state.queue.indexOf(entry), 1);
    state.keys.delete(entry.key);
  }
  const batch = jsDebugObservationBatchForEntries(validEntries.map(item => item.entry), statsWriterFence);
  validEntries = validEntries.slice(0, batch.observations.length);
  if (!batch.observations.length) return;
  const inFlight = (async () => {
    let batchRetired = false;
    try {
      const receipt = await apiFetchJsonQuiet('/api/stats-observations', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(batch),
      });
      if (!scope.current()) return;
      const accepted = receipt?.accepted;
      const duplicates = receipt?.duplicates;
      const observationReceipts = receipt?.observation_receipts;
      if (!receipt || receipt.ok !== true || !Number.isSafeInteger(accepted) || !Number.isSafeInteger(duplicates)
        || accepted < 0 || duplicates < 0 || accepted + duplicates !== validEntries.length
        || !Array.isArray(observationReceipts) || observationReceipts.length !== validEntries.length) {
        throw new Error('browser observation receipt did not acknowledge the batch');
      }
      const expectedEventIds = batch.observations.map(observation => observation.event_id);
      const seenEventIds = new Set();
      let acceptedRows = 0;
      let duplicateRows = 0;
      for (let index = 0; index < observationReceipts.length; index += 1) {
        const row = observationReceipts[index];
        if (!row || typeof row !== 'object' || Array.isArray(row)
            || Object.keys(row).sort().join(',') !== 'disposition,event_id'
            || row.event_id !== expectedEventIds[index]
            || seenEventIds.has(row.event_id)
            || !['accepted', 'duplicate'].includes(row.disposition)) {
          throw new Error('browser observation receipt mapping is malformed');
        }
        seenEventIds.add(row.event_id);
        if (row.disposition === 'accepted') acceptedRows += 1;
        else duplicateRows += 1;
      }
      if (acceptedRows !== accepted || duplicateRows !== duplicates) {
        throw new Error('browser observation receipt mapping disagrees with aggregate counts');
      }
      for (const {entry} of validEntries) {
        const index = state.queue.indexOf(entry);
        if (index >= 0) state.queue.splice(index, 1);
        state.keys.delete(entry.key);
      }
      setJsDebugCurrentObservationReceipt(validEntries.map(item => item.entry), 'accepted');
      state.retryMs = jsDebugCurrentObservationBatchDelayMs;
      batchRetired = true;
    } catch (error) {
      if (!scope.current()) return;
      if ([400, 404, 405, 410, 413, 422, 426].includes(Number(error?.status))) {
        for (const {entry} of validEntries) {
          const index = state.queue.indexOf(entry);
          if (index >= 0) state.queue.splice(index, 1);
          state.keys.delete(entry.key);
        }
        setJsDebugCurrentObservationReceipt(validEntries.map(item => item.entry), 'rejected');
        state.drops += validEntries.length;
        state.retryMs = jsDebugCurrentObservationBatchDelayMs;
        batchRetired = true;
      } else {
        state.retries += 1;
        setJsDebugCurrentObservationReceipt(validEntries.map(item => item.entry), 'retrying');
      }
    } finally {
      if (state.inFlight === inFlight) state.inFlight = null;
      if (scope.current() && state.queue.length) {
        const releaseBlockingPending = state.queue.some(entry => entry.releaseBlocking);
        const delay = releaseBlockingPending && batchRetired ? 0 : state.retryMs;
        if (!(releaseBlockingPending && batchRetired)) {
          state.retryMs = Math.min(jsDebugCurrentObservationRetryMaxMs, state.retryMs * 2);
        }
        scheduleJsDebugCurrentObservationFlush(delay);
      }
    }
  })();
  state.inFlight = inFlight;
  return inFlight;
}

// The bytes finalizer for a still-open API observation. The live event already carries the
// measured, bounded byte count (Content-Length or a response clone; rejected, hung, or
// unconsumed bodies simply leave it absent), so this locates the reserved queue entry by live
// identity and finalizes it exactly once. A late measurement for an already-finalized (or
// immediate-failure) observation finds no open entry and cannot rewrite its immutable content.
function finalizeJsDebugCurrentObservationBytes(liveEvent) {
  if (!liveEvent || typeof liveEvent !== 'object') return;
  const entry = jsDebugCurrentObservationState.queue.find(item => item && item.liveEvent === liveEvent && !item.finalized);
  if (!entry) return;
  finalizeJsDebugCurrentObservation(entry);
}

function recordJsDebugDisconnectedSpan(startMs, endMs = Date.now()) {
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
  if (!payload || typeof payload !== 'object') return;
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

registerDebugRuntimeFacade('observation', {
  disposeJsDebugCurrentObservationLifecycle,
  flushJsDebugCurrentObservations,
  installJsDebugCurrentObservationLiveness,
  queueJsDebugCurrentObservation,
  recordJsDebugClientEventsConnectionState,
  recordJsDebugStatsSample,
  scheduleJsDebugCurrentObservationFlush,
});

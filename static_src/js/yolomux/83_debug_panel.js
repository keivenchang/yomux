// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// JavaScript debug panel rendering and controls split from 80_panes_preferences.js.

const jsDebugGraphDefaultScaleSeconds = 5;
const jsDebugGraphDefaultRangeSeconds = 15 * 60;
let jsDebugSubTab = 'graph';
let jsDebugGraphScaleSeconds = jsDebugGraphDefaultScaleSeconds;
let jsDebugGraphRangeSeconds = jsDebugGraphDefaultRangeSeconds;
let jsDebugStatsPollTimer = null;
let jsDebugStatsPollInFlight = false;
let jsDebugStatsHistoryFlushTimer = null;
let jsDebugStatsHistoryFlushInFlight = false;
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
let jsDebugGraphRangeSliderDragging = false;
const jsDebugGraphScaleOptions = Object.freeze([1, 5, 10, 30]);
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
const jsDebugGraphRawWindowMs = 60 * 60 * 1000;
const jsDebugGraphRawBucketMs = 1000;
const jsDebugGraphRollupBucketMs = 30 * 1000;
const jsDebugGraphResponseRefRetentionMs = 5 * 60 * 1000;
const jsDebugStatsPollMs = 3000;
const jsDebugStatsHistoryFlushMs = 10000;
const jsDebugStatsHistoryPostMaxRecords = 1000;
const jsDebugStatsClientStorageKey = 'yolomux.stats.client_id.v1';
const jsDebugStatsDisconnectedStorageKey = 'yolomux.stats.disconnected_at.v1';
const jsDebugGraphMovingAverageSamples = 10;
const jsDebugGraphMovingAverageSeries = new Set(['api', 'sse', 'latency', 'bandwidth']);
const jsDebugGraphAgentTokenSeriesPrefix = 'agentToken:';
const jsDebugGraphAgentTokenColors = Object.freeze([
  'var(--accent-sky-strong)',
  'var(--good)',
  'var(--accent-gold)',
  'var(--link-soft)',
  'var(--bad)',
]);
const jsDebugGraphRawBuckets = new Map();
const jsDebugGraphRollupBuckets = new Map();
const jsDebugGraphEventBuckets = new Map();
const jsDebugGraphEventResponseBytes = new Map();
const jsDebugGraphEventRefTimes = new Map();
const jsDebugGraphPendingServerBuckets = new Map();
const jsDebugGraphSeries = Object.freeze([
  {key: 'api', label: 'API', unit: 'countPerSecond'},
  {key: 'sse', label: 'SSE', unit: 'countPerSecond'},
  {key: 'latency', label: 'Latency', unit: 'ms'},
  {key: 'bandwidth', label: 'Bandwidth', unit: 'bytesPerSecond'},
  {key: 'askAgents', label: 'ASK', unit: 'count'},
  {key: 'runAgents', label: 'RUN', unit: 'count'},
  {key: 'transitionAgents', label: 'Transition', unit: 'count'},
  {key: 'idleAgents', label: 'Idle', unit: 'count'},
  {key: 'tokensPerAgent', label: 'Tokens/agent/min', unit: 'tokensPerMinute'},
  {key: 'cpu', label: 'yolomux.py CPU %', unit: 'percent'},
  {key: 'systemCpu', label: 'system avg CPU %', unit: 'percent'},
]);
const jsDebugGraphChartGroups = Object.freeze([
  {key: 'latency', label: 'Latency', series: ['latency'], unit: 'ms'},
  {key: 'count', label: 'API/SSE/sec', series: ['api', 'sse'], unit: 'countPerSecond'},
  {key: 'cpu', label: 'CPU', series: ['cpu', 'systemCpu'], unit: 'percent', fixedMax: 100},
  {key: 'bandwidth', label: 'Bandwidth/sec', series: ['bandwidth'], unit: 'bytesPerSecond'},
  {key: 'activity', label: 'Agent status', series: ['askAgents', 'runAgents', 'transitionAgents', 'idleAgents'], unit: 'count', kind: 'area', stacked: true, integerAxis: true, integerGridLines: true, exactIntegerAxisMax: true},
  {key: 'agentTokens', label: 'Agent tokens/min', series: [], unit: 'tokensPerMinute', kind: 'area', stacked: true, dynamicAgentTokens: true, legendPlacement: 'footer'},
]);

function normalizedJsDebugSubTab(value) {
  return value === 'graph' ? 'graph' : 'events';
}

function normalizedJsDebugGraphScale(value) {
  const seconds = Number(value);
  return jsDebugGraphScaleOptions.includes(seconds) ? seconds : jsDebugGraphDefaultScaleSeconds;
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

function debugGraphZoomDomainValid(domain = jsDebugGraphZoomDomain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs - startMs >= 1000;
}

function clearDebugGraphZoom({render = true} = {}) {
  jsDebugGraphZoomDomain = null;
  jsDebugGraphSelectionState = null;
  if (!render) return;
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    graph.className = debugGraphClassName();
    graph.innerHTML = debugGraphInnerHtml();
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
    debugEventDetailText(event) || t('debug.event'),
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
    tokensPerAgentTotal: 0,
    agentTokenSamples: 0,
    agentTokenRates: new Map(),
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
  );
}

function debugGraphBucket(map, startMs, durationMs) {
  const key = String(startMs);
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

function debugGraphBucketForTime(timeMs, nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  if (!Number.isFinite(timeMs) || timeMs < retentionCutoff) return null;
  const rawCutoff = nowMs - jsDebugGraphRawWindowMs;
  if (timeMs < rawCutoff) {
    const startMs = Math.floor(timeMs / jsDebugGraphRollupBucketMs) * jsDebugGraphRollupBucketMs;
    return debugGraphBucket(jsDebugGraphRollupBuckets, startMs, jsDebugGraphRollupBucketMs);
  }
  const startMs = Math.floor(timeMs / jsDebugGraphRawBucketMs) * jsDebugGraphRawBucketMs;
  return debugGraphBucket(jsDebugGraphRawBuckets, startMs, jsDebugGraphRawBucketMs);
}

function debugGraphServerBucketRefForTime(timeMs, nowMs = Date.now()) {
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  if (!Number.isFinite(timeMs) || timeMs < retentionCutoff) return null;
  const rawCutoff = nowMs - jsDebugGraphRawWindowMs;
  const durationMs = timeMs < rawCutoff ? jsDebugGraphRollupBucketMs : jsDebugGraphRawBucketMs;
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
  const runAgents = Number(data.runAgents);
  const transitionAgents = Number(data.transitionAgents);
  const idleAgents = Number(data.idleAgents);
  if (Number.isFinite(askAgents) || Number.isFinite(runAgents) || Number.isFinite(transitionAgents) || Number.isFinite(idleAgents)) {
    const normalizedAskAgents = Number.isFinite(askAgents) ? askAgents : 0;
    const normalizedRunAgents = Number.isFinite(runAgents) ? runAgents : 0;
    const normalizedIdleAgents = Number.isFinite(idleAgents) ? idleAgents : (Number.isFinite(inactiveAgents) ? inactiveAgents : 0);
    const normalizedTransitionAgents = Number.isFinite(transitionAgents)
      ? Math.max(0, transitionAgents - (Number.isFinite(idleAgents) ? 0 : normalizedIdleAgents))
      : 0;
    bucket.askAgentTotal += Math.max(0, Number.isFinite(askAgents) ? askAgents : 0);
    bucket.runAgentTotal += Math.max(0, normalizedRunAgents);
    bucket.transitionAgentTotal += Math.max(0, normalizedTransitionAgents);
    bucket.idleAgentTotal += Math.max(0, normalizedIdleAgents);
    bucket.activeAgentTotal += Math.max(0, Number.isFinite(activeAgents) ? activeAgents : normalizedAskAgents + normalizedRunAgents + normalizedTransitionAgents);
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
  const disconnectedMs = Number(data.disconnectedMs || 0);
  if (Number.isFinite(disconnectedMs) && disconnectedMs > 0) record.disconnected_ms += disconnectedMs;
  scheduleJsDebugStatsHistoryFlush();
}

function debugGraphMergeAgentTokenRates(target, source) {
  if (!(source?.agentTokenRates instanceof Map)) return;
  if (!(target.agentTokenRates instanceof Map)) target.agentTokenRates = new Map();
  for (const [key, item] of source.agentTokenRates.entries()) {
    const existing = target.agentTokenRates.get(String(key)) || {label: item?.label || String(key), total: 0, samples: 0};
    existing.label = item?.label || existing.label;
    existing.total += Number(item?.total || 0);
    existing.samples += Number(item?.samples || 0);
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
  target.tokensPerAgentTotal += source.tokensPerAgentTotal || 0;
  target.agentTokenSamples += source.agentTokenSamples || 0;
  debugGraphMergeAgentTokenRates(target, source);
}

function compactJsDebugGraphBuckets(nowMs = Date.now()) {
  const rawCutoff = nowMs - jsDebugGraphRawWindowMs;
  for (const [key, bucket] of [...jsDebugGraphRawBuckets.entries()]) {
    if (bucket.startMs >= rawCutoff) continue;
    const rollupStartMs = Math.floor(bucket.startMs / jsDebugGraphRollupBucketMs) * jsDebugGraphRollupBucketMs;
    const rollup = debugGraphBucket(jsDebugGraphRollupBuckets, rollupStartMs, jsDebugGraphRollupBucketMs);
    debugGraphMergeBucket(rollup, bucket);
    jsDebugGraphRawBuckets.delete(key);
  }
  const retentionCutoff = nowMs - jsDebugGraphRetentionMs;
  for (const [key, bucket] of [...jsDebugGraphRollupBuckets.entries()]) {
    if (bucket.startMs < retentionCutoff) jsDebugGraphRollupBuckets.delete(key);
  }
  for (const [key, bucket] of [...jsDebugGraphRawBuckets.entries()]) {
    if (bucket.startMs < retentionCutoff) jsDebugGraphRawBuckets.delete(key);
  }
  const refCutoff = nowMs - jsDebugGraphResponseRefRetentionMs;
  for (const [id, timeMs] of [...jsDebugGraphEventRefTimes.entries()]) {
    if (timeMs >= refCutoff) continue;
    jsDebugGraphEventRefTimes.delete(id);
    jsDebugGraphEventBuckets.delete(id);
    jsDebugGraphEventResponseBytes.delete(id);
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
  debugGraphQueueServerDelta(bucketRef, data);
  if (event.type === 'api' && Number.isFinite(event.id)) {
    jsDebugGraphEventBuckets.set(event.id, bucketRef);
    jsDebugGraphEventResponseBytes.set(event.id, responseBytes);
    jsDebugGraphEventRefTimes.set(event.id, nowMs);
  }
  compactJsDebugGraphBuckets(nowMs);
}

function recordApiDebugResponseBytesForGraph(event, responseBytes) {
  if (!jsDebugCollectionEnabled || !event || !Number.isFinite(event.id)) return;
  const bucket = jsDebugGraphEventBuckets.get(event.id);
  if (!bucket) return;
  const nextBytes = Number(responseBytes);
  if (!Number.isFinite(nextBytes) || nextBytes < 0) return;
  const previousBytes = Number(jsDebugGraphEventResponseBytes.get(event.id) || 0);
  const delta = nextBytes - previousBytes;
  jsDebugGraphEventResponseBytes.set(event.id, nextBytes);
  jsDebugGraphEventRefTimes.set(event.id, Date.now());
  if (delta === 0) return;
  debugGraphQueueServerDelta(bucket, {bandwidthBytes: delta});
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

function recordJsDebugStatsSample(payload = {}) {
  if (!jsDebugCollectionEnabled || !payload || typeof payload !== 'object') return;
  const nextPid = Number(payload.pid);
  const nextStartedAt = Number(payload.started_at);
  const serverChanged = (
    (Number.isFinite(nextPid) && Number.isFinite(jsDebugStatsServerPid) && nextPid !== jsDebugStatsServerPid)
    || (Number.isFinite(nextStartedAt) && Number.isFinite(jsDebugStatsServerStartedAt) && nextStartedAt !== jsDebugStatsServerStartedAt)
  );
  if (serverChanged) {
    jsDebugStatsServerSequence = 0;
    jsDebugGraphPendingServerBuckets.clear();
  }
  if (Number.isFinite(Number(payload.uptime_seconds))) jsDebugStatsServerUptimeSeconds = Math.max(0, Number(payload.uptime_seconds));
  if (Number.isFinite(nextPid)) jsDebugStatsServerPid = nextPid;
  if (Number.isFinite(nextStartedAt)) jsDebugStatsServerStartedAt = nextStartedAt;
  if (Number.isFinite(Number(payload.rss_bytes))) jsDebugStatsServerRssBytes = Number(payload.rss_bytes);
  debugGraphApplyServerHistory(payload.history);
  if (payload.history && typeof payload.history === 'object') {
    scheduleJsDebugPanelRefresh();
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
  scheduleJsDebugPanelRefresh();
}

function clearJsDebugGraphData() {
  jsDebugGraphRawBuckets.clear();
  jsDebugGraphRollupBuckets.clear();
  jsDebugGraphEventBuckets.clear();
  jsDebugGraphEventResponseBytes.clear();
  jsDebugGraphEventRefTimes.clear();
  jsDebugGraphPendingServerBuckets.clear();
}

function debugGraphBucketForServerRecord(record) {
  if (!record || typeof record !== 'object') return null;
  const startSeconds = Number(record.start);
  const durationSeconds = Number(record.duration);
  if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return null;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, durationSeconds * 1000);
  const startMs = Math.floor(startSeconds * 1000);
  const map = durationMs >= jsDebugGraphRollupBucketMs ? jsDebugGraphRollupBuckets : jsDebugGraphRawBuckets;
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
  bucket.disconnectedMs = Math.max(bucket.disconnectedMs, Number(record.disconnected_ms || 0));
  bucket.cpuTotalPercent = Math.max(bucket.cpuTotalPercent, Number(record.cpu_total_percent || 0));
  bucket.cpuCount = Math.max(bucket.cpuCount, Number(record.cpu_count || 0));
  bucket.systemCpuTotalPercent = Math.max(bucket.systemCpuTotalPercent, Number(record.system_cpu_total_percent || 0));
  bucket.systemCpuCount = Math.max(bucket.systemCpuCount, Number(record.system_cpu_count || 0));
  const askAgentTotal = Number(record.ask_agent_total);
  const runAgentTotal = Number(record.run_agent_total);
  const transitionAgentTotal = Number(record.transition_agent_total);
  const idleAgentTotal = Number(record.idle_agent_total);
  const hasSplitAgentTotals = Number.isFinite(askAgentTotal) || Number.isFinite(runAgentTotal) || Number.isFinite(transitionAgentTotal) || Number.isFinite(idleAgentTotal);
  if (hasSplitAgentTotals) {
    bucket.askAgentTotal = Math.max(bucket.askAgentTotal, Number(record.ask_agent_total || 0));
    bucket.runAgentTotal = Math.max(bucket.runAgentTotal, Number(record.run_agent_total || 0));
    const fallbackIdleAgentTotal = Number.isFinite(idleAgentTotal) ? Number(record.idle_agent_total || 0) : Number(record.inactive_agent_total || 0);
    const normalizedTransitionAgentTotal = Math.max(0, Number(record.transition_agent_total || 0) - (Number.isFinite(idleAgentTotal) ? 0 : fallbackIdleAgentTotal));
    bucket.transitionAgentTotal = Math.max(bucket.transitionAgentTotal, normalizedTransitionAgentTotal);
    bucket.idleAgentTotal = Math.max(bucket.idleAgentTotal, fallbackIdleAgentTotal);
  } else {
    bucket.runAgentTotal = Math.max(bucket.runAgentTotal, Number(record.active_agent_total || 0));
    bucket.idleAgentTotal = Math.max(bucket.idleAgentTotal, Number(record.inactive_agent_total || 0));
  }
  bucket.activeAgentTotal = Math.max(bucket.activeAgentTotal, Number(record.active_agent_total || 0));
  bucket.inactiveAgentTotal = Math.max(bucket.inactiveAgentTotal, Number(record.inactive_agent_total || 0));
  bucket.agentActivitySamples = Math.max(bucket.agentActivitySamples, Number(record.agent_activity_samples || 0));
  bucket.tokensPerAgentTotal = Math.max(bucket.tokensPerAgentTotal, Number(record.tokens_per_agent_total || 0));
  bucket.agentTokenSamples = Math.max(bucket.agentTokenSamples, Number(record.agent_token_samples || 0));
  debugGraphApplyServerAgentTokenRates(bucket, record.agent_token_rates);
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
    if (!Number.isFinite(total) && !Number.isFinite(samples)) continue;
    const label = String(item.label || key).trim() || key;
    const existing = bucket.agentTokenRates.get(key) || {label, total: 0, samples: 0};
    existing.label = label;
    if (Number.isFinite(total)) existing.total = Math.max(Number(existing.total || 0), Math.max(0, total));
    if (Number.isFinite(samples)) existing.samples = Math.max(Number(existing.samples || 0), Math.max(0, samples));
    bucket.agentTokenRates.set(key, existing);
  }
}

function debugGraphApplyServerHistory(history = {}) {
  if (!history || typeof history !== 'object') return;
  const sequence = Number(history.sequence);
  if (Number.isFinite(sequence)) jsDebugStatsServerSequence = Math.max(0, sequence);
  const records = Array.isArray(history.records) ? history.records : [];
  records.forEach(debugGraphApplyServerRecord);
  compactJsDebugGraphBuckets();
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

function debugGraphDisplayBuckets(nowMs = Date.now(), scaleSeconds = jsDebugGraphScaleSeconds, rangeSeconds = jsDebugGraphRangeSeconds) {
  compactJsDebugGraphBuckets(nowMs);
  const domain = debugGraphDomain(nowMs, rangeSeconds);
  const scaleMs = normalizedJsDebugGraphScale(scaleSeconds) * 1000;
  const buckets = new Map();
  for (const bucket of jsDebugGraphRollupBuckets.values()) {
    if (debugGraphBucketInRange(bucket, domain.startMs, domain.endMs)) debugGraphAggregateBucket(buckets, bucket, scaleMs);
  }
  for (const bucket of jsDebugGraphRawBuckets.values()) {
    if (debugGraphBucketInRange(bucket, domain.startMs, domain.endMs)) debugGraphAggregateBucket(buckets, bucket, scaleMs);
  }
  return [...buckets.values()].sort((a, b) => a.startMs - b.startMs);
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

function debugGraphBucketValue(bucket, key) {
  if (key === 'api') return debugGraphBucketRate(bucket, bucket.apiCount);
  if (key === 'sse') return debugGraphBucketRate(bucket, bucket.sseCount);
  if (key === 'latency') return bucket.latencyCount ? bucket.latencyTotalMs / bucket.latencyCount : 0;
  if (key === 'bandwidth') return debugGraphBucketRate(bucket, bucket.bandwidthBytes);
  if (key === 'askAgents') return bucket.agentActivitySamples ? bucket.askAgentTotal / bucket.agentActivitySamples : 0;
  if (key === 'runAgents') return bucket.agentActivitySamples ? bucket.runAgentTotal / bucket.agentActivitySamples : 0;
  if (key === 'transitionAgents') return bucket.agentActivitySamples ? bucket.transitionAgentTotal / bucket.agentActivitySamples : 0;
  if (key === 'idleAgents') return bucket.agentActivitySamples ? bucket.idleAgentTotal / bucket.agentActivitySamples : 0;
  if (key === 'tokensPerAgent') return bucket.agentTokenSamples ? bucket.tokensPerAgentTotal / bucket.agentTokenSamples : 0;
  if (String(key || '').startsWith(jsDebugGraphAgentTokenSeriesPrefix)) {
    const tokenKey = String(key).slice(jsDebugGraphAgentTokenSeriesPrefix.length);
    const item = bucket.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(tokenKey) : null;
    return item?.samples ? Number(item.total || 0) / item.samples : 0;
  }
  if (key === 'cpu') return bucket.cpuCount ? Math.min(100, bucket.cpuTotalPercent / bucket.cpuCount) : 0;
  if (key === 'systemCpu') return bucket.systemCpuCount ? Math.min(100, bucket.systemCpuTotalPercent / bucket.systemCpuCount) : 0;
  return 0;
}

function debugGraphBucketHasSeriesData(bucket, key) {
  if (!bucket) return false;
  if (key === 'latency') return Number(bucket.latencyCount || 0) > 0;
  if (key === 'askAgents' || key === 'runAgents' || key === 'transitionAgents' || key === 'idleAgents') return Number(bucket.agentActivitySamples || 0) > 0;
  if (key === 'tokensPerAgent') return Number(bucket.agentTokenSamples || 0) > 0;
  if (String(key || '').startsWith(jsDebugGraphAgentTokenSeriesPrefix)) {
    const tokenKey = String(key).slice(jsDebugGraphAgentTokenSeriesPrefix.length);
    const item = bucket.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(tokenKey) : null;
      return Number(item?.samples || 0) > 0;
  }
  if (key === 'cpu') return Number(bucket.cpuCount || 0) > 0;
  if (key === 'systemCpu') return Number(bucket.systemCpuCount || 0) > 0;
  return debugGraphBucketValue(bucket, key) > 0;
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
  return `${debugGraphTokenNumberText(value)} tokens`;
}

function debugGraphTokensPerMinuteText(value) {
  return `${debugGraphTokenNumberText(value)} tokens/min`;
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
    if (unit === 'bytes') return '0';
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

function debugGraphMetaHtml() {
  const items = [];
  if (Number.isFinite(jsDebugStatsServerUptimeSeconds)) items.push(`yolomux.py uptime ${debugGraphUptimeText(jsDebugStatsServerUptimeSeconds)}`);
  if (Number.isFinite(jsDebugStatsServerPid)) items.push(`PID=${Math.floor(jsDebugStatsServerPid)}`);
  const rss = debugGraphBytesText(jsDebugStatsServerRssBytes);
  if (rss) items.push(`rss ${rss}`);
  if (Number.isFinite(jsDebugStatsServerSequence) && jsDebugStatsServerSequence > 0) items.push(`server seq ${Math.floor(jsDebugStatsServerSequence)}`);
  if (items.length) {
    const counts = debugEventCounts();
    const uploadedMb = debugGraphTotalMegabytesText(counts.apiRequestBytes);
    const downloadedMb = debugGraphTotalMegabytesText(counts.apiResponseBytes + counts.sseBytes);
    items.push(`total ${uploadedMb}/${downloadedMb} MB up/down`);
  }
  return `<div class="js-debug-graph-meta" data-js-debug-uptime="${esc(Number.isFinite(jsDebugStatsServerUptimeSeconds) ? debugGraphUptimeText(jsDebugStatsServerUptimeSeconds) : '')}">${esc(items.join(' | ') || 'waiting for server stats')}</div>`;
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
  return [...tokenAgents.entries()]
    .filter(([, item]) => item.samples > 0)
    .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]))
    .map(([key, item], index) => ({
      key: `${jsDebugGraphAgentTokenSeriesPrefix}${key}`,
      label: item.label,
      unit: 'tokensPerMinute',
      cssKey: 'agentToken',
      agentTokenSeries: true,
      agentTokenKey: key,
      color: jsDebugGraphAgentTokenColors[index % jsDebugGraphAgentTokenColors.length],
    }));
}

function debugGraphSeriesData(buckets) {
  const times = buckets.map(bucket => Number(bucket.startMs) || 0);
  const defs = [...jsDebugGraphSeries, ...debugGraphAgentTokenSeriesDefs(buckets)];
  return defs.map(def => {
    const values = buckets.map(bucket => debugGraphBucketValue(bucket, def.key));
    const hasDataValues = buckets.map(bucket => debugGraphBucketHasSeriesData(bucket, def.key));
    const sampleValues = values.filter((_value, index) => hasDataValues[index]);
    const sampleTimes = times.filter((_time, index) => hasDataValues[index]);
    const samples = sampleValues.length;
    const max = Math.max(0, ...sampleValues);
    const current = sampleValues.length ? sampleValues[sampleValues.length - 1] : 0;
    const movingAverageValues = jsDebugGraphMovingAverageSeries.has(def.key) ? debugGraphMovingAverageValues(sampleValues) : [];
    return {...def, values, times, hasDataValues, movingAverageValues, movingAverageTimes: sampleTimes, max, current, samples};
  });
}

function debugGraphScaleControlsHtml() {
  return `<div class="js-debug-graph-control-group" role="toolbar" aria-label="${esc(t('debug.tab.graph'))} bucket size">
    ${jsDebugGraphScaleOptions.map(seconds => {
      const active = seconds === jsDebugGraphScaleSeconds;
      return `<button type="button" class="js-debug-scale-button${active ? ' active' : ''}" data-js-debug-scale="${seconds}" aria-pressed="${active ? 'true' : 'false'}">${seconds}s</button>`;
    }).join('')}
  </div>`;
}

function debugGraphRangeControlsHtml(nowMs = Date.now()) {
  const activeRange = activeJsDebugGraphRangeSeconds(nowMs);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (!options.length) return '';
  const sliderId = 'js-debug-range-options';
  const value = jsDebugGraphRangeOptionIndex(activeRange, nowMs);
  const zoomed = debugGraphZoomDomainValid();
  return `<div class="js-debug-range-slider-control" data-js-debug-range-control>
    <span class="js-debug-range-label" data-js-debug-range-label>${esc(zoomed ? 'Zoom' : jsDebugGraphRangeLabel(activeRange, nowMs))}</span>
    <input class="js-debug-range-slider" type="range" min="0" max="${esc(Math.max(0, options.length - 1))}" step="any" value="${esc(value)}" list="${esc(sliderId)}" data-js-debug-range-slider aria-label="${esc(t('debug.tab.graph'))} time range">
    <datalist id="${esc(sliderId)}">${options.map((option, index) => `<option value="${esc(index)}" label="${esc(option.label)}" data-js-debug-range="${esc(option.seconds)}"></option>`).join('')}</datalist>
    <span class="js-debug-range-end-label" aria-hidden="true">${esc(options.at(-1)?.label || '')}</span>
    ${zoomed ? '<button type="button" class="js-debug-zoom-reset" data-js-debug-zoom-reset>Reset</button>' : ''}
  </div>`;
}

function debugGraphControlsHtml(nowMs = Date.now()) {
  return `<div class="js-debug-graph-controls">
    ${debugGraphScaleControlsHtml()}
    ${debugGraphRangeControlsHtml(nowMs)}
  </div>`;
}

function debugGraphTimeLabel(ms) {
  if (!Number.isFinite(ms)) return '';
  const date = new Date(ms);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
}

function debugGraphSeriesTimeMs(series, index) {
  const times = Array.isArray(series.times) ? series.times : [];
  const value = Number(times[index]);
  return Number.isFinite(value) ? value : NaN;
}

function debugGraphPolylinePoints(values, times, chartMax, domain, hasDataValues = null) {
  return values
    .map((value, i) => ({value, time: times[i], hasData: !hasDataValues || hasDataValues[i] === true}))
    .filter(point => point.hasData)
    .map(point => debugGraphPointForValue(point.value, point.time, chartMax, domain).join(','))
    .join(' ');
}

function debugGraphPointForValue(value, timeMs, chartMax, domain) {
  const top = 8;
  const width = 600;
  const height = 104;
  const max = Math.max(chartMax, 1);
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  const rawX = Number.isFinite(Number(timeMs)) && Number.isFinite(startMs) && Number.isFinite(endMs)
    ? ((Number(timeMs) - startMs) / spanMs) * width
    : width;
  const x = Math.max(0, Math.min(width, rawX));
  const y = top + (1 - (Math.max(0, value) / max)) * height;
  return [x.toFixed(1), y.toFixed(1)];
}

function debugGraphXForTime(timeMs, domain) {
  const width = 600;
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  if (!Number.isFinite(Number(timeMs)) || !Number.isFinite(startMs) || !Number.isFinite(endMs)) return 0;
  return Math.max(0, Math.min(width, ((Number(timeMs) - startMs) / spanMs) * width));
}

function debugGraphDisconnectedActiveStartMs() {
  const inMemory = Number(jsDebugStatsDisconnectStartedAtMs);
  if (Number.isFinite(inMemory) && inMemory > 0) return inMemory;
  const stored = Number(jsDebugStatsStorageGet(jsDebugStatsDisconnectedStorageKey));
  return Number.isFinite(stored) && stored > 0 ? stored : NaN;
}

function debugGraphDisconnectedSegments(buckets, domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return [];
  const segments = [];
  for (const bucket of buckets || []) {
    if (Number(bucket?.disconnectedMs || 0) <= 0) continue;
    const bucketStart = Number(bucket.startMs);
    const bucketDuration = Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
    if (!Number.isFinite(bucketStart)) continue;
    const segmentStart = Math.max(startMs, bucketStart);
    const segmentEnd = Math.min(endMs, bucketStart + bucketDuration);
    if (segmentEnd > segmentStart) segments.push({startMs: segmentStart, endMs: segmentEnd});
  }
  const activeStart = debugGraphDisconnectedActiveStartMs();
  if (Number.isFinite(activeStart) && endMs > activeStart) {
    segments.push({startMs: Math.max(startMs, activeStart), endMs});
  }
  return segments
    .filter(segment => segment.endMs > segment.startMs)
    .sort((a, b) => a.startMs - b.startMs)
    .reduce((merged, segment) => {
      const previous = merged.at(-1);
      if (previous && segment.startMs <= previous.endMs) {
        previous.endMs = Math.max(previous.endMs, segment.endMs);
      } else {
        merged.push({...segment});
      }
      return merged;
    }, []);
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

function debugGraphSeriesStyleAttr(series) {
  const color = String(series?.color || '').trim();
  return color ? ` style="--js-debug-series-color: ${esc(color)};"` : '';
}

function debugGraphSeriesTokenAgentAttrs(series) {
  if (series?.agentTokenSeries !== true) return '';
  return ` data-js-debug-token-agent="${esc(series.agentTokenKey || '')}" data-js-debug-token-agent-label="${esc(series.label || '')}"`;
}

function debugGraphPolylineHtml(series, chartMax, domain) {
  const points = debugGraphPolylinePoints(debugGraphSeriesPlotValues(series), series.times || [], chartMax, domain, debugGraphSeriesPlotHasDataValues(series));
  if (!points) return '';
  return `<polyline class="js-debug-line js-debug-line--${esc(debugGraphSeriesClassKey(series))}" data-js-debug-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)} points="${esc(points)}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.label)}</title></polyline>`;
}

function debugGraphAreaPathHtml(series, chartMax, domain) {
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const pointIndexes = debugGraphSeriesPlotValues(series)
    .map((_value, index) => index)
    .filter(index => !hasDataValues || hasDataValues[index] === true);
  const upperPoints = pointIndexes.map(index => debugGraphPointForValue(debugGraphSeriesPlotValues(series)[index], debugGraphSeriesTimeMs(series, index), chartMax, domain));
  if (!upperPoints.length) return '';
  const baseline = 112;
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

function debugGraphMovingAveragePolylineHtml(series, chartMax, domain) {
  if (!jsDebugGraphMovingAverageSeries.has(series.key)) return '';
  const points = debugGraphPolylinePoints(series.movingAverageValues || [], series.movingAverageTimes || [], chartMax, domain);
  if (!points) return '';
  return `<polyline class="js-debug-line js-debug-line--${esc(series.key)} js-debug-line--moving-average" data-js-debug-moving-average="${esc(series.key)}" data-js-debug-moving-average-samples="${esc(jsDebugGraphMovingAverageSamples)}" points="${esc(points)}" fill="none" vector-effect="non-scaling-stroke"><title>${esc(series.label)} ${jsDebugGraphMovingAverageSamples}-sample moving average</title></polyline>`;
}

function debugGraphDisconnectedOverlayHtml(buckets, domain) {
  const y = 116;
  return debugGraphDisconnectedSegments(buckets, domain).map((segment, index) => {
    const x1 = debugGraphXForTime(segment.startMs, domain).toFixed(1);
    const x2 = debugGraphXForTime(segment.endMs, domain).toFixed(1);
    if (x1 === x2) return '';
    return `<line class="js-debug-disconnect-line" data-js-debug-disconnect-line="${esc(index)}" data-js-debug-disconnect-start="${esc(Math.floor(segment.startMs))}" data-js-debug-disconnect-end="${esc(Math.floor(segment.endMs))}" x1="${esc(x1)}" y1="${esc(y)}" x2="${esc(x2)}" y2="${esc(y)}" vector-effect="non-scaling-stroke"><title>Client disconnected</title></line>`;
  }).join('');
}

function debugGraphInteractionOverlayHtml() {
  return '<rect class="js-debug-selection-rect" data-js-debug-selection-rect x="0" y="8" width="0" height="104"></rect><line class="js-debug-hover-line" data-js-debug-hover-line x1="0" y1="8" x2="0" y2="116" vector-effect="non-scaling-stroke"></line>';
}

function debugGraphLegendHtml(seriesItems) {
  return `<div class="js-debug-legend" aria-label="${esc(t('debug.summary'))}">
    ${seriesItems.map(series => `<div class="js-debug-legend-item" data-js-debug-legend="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}><span class="js-debug-legend-swatch js-debug-legend-swatch--${esc(debugGraphSeriesClassKey(series))}"${debugGraphSeriesStyleAttr(series)}></span><span>${esc(series.label)}</span></div>`).join('')}
  </div>`;
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
  const top = 8;
  const height = 104;
  const max = Math.max(Number(chartMax) || 0, 1);
  return top + (1 - (Math.max(0, Number(value) || 0) / max)) * height;
}

function debugGraphAxisTickStyle(value, chartMax) {
  const percent = (debugGraphGridLineY(value, chartMax) / 120) * 100;
  return ` style="--js-debug-axis-y: ${esc(percent.toFixed(3))}%;"`;
}

function debugGraphGridLinesHtml(group, axisMax) {
  const max = Math.max(0, Number(axisMax) || 0);
  const fallbackMax = max > 0 ? max : 1;
  const values = group.integerGridLines === true
    ? debugGraphIntegerAxisValues(max)
    : [fallbackMax, fallbackMax / 2, 0];
  return values.map(value => {
    const y = debugGraphGridLineY(value, max).toFixed(1);
    const axisValue = group.integerGridLines === true ? ` data-js-debug-grid-value="${esc(value)}"` : '';
    return `<line class="js-debug-grid-line${group.integerGridLines === true ? ' js-debug-grid-line--integer' : ''}" data-js-debug-grid-line="${esc(group.key)}"${axisValue} x1="0" y1="${esc(y)}" x2="600" y2="${esc(y)}" vector-effect="non-scaling-stroke"></line>`;
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
  return `<div class="js-debug-x-axis" data-js-debug-x-axis>
    ${ticks.map(tick => `<span data-js-debug-x-tick="${esc(tick.name)}">${esc(debugGraphTimeLabel(tick.ms))}</span>`).join('')}
  </div>`;
}

function debugGraphGroupSeriesItems(group, seriesItems) {
  if (group.dynamicAgentTokens === true) return seriesItems.filter(series => series.agentTokenSeries === true);
  const seriesByKey = new Map(seriesItems.map(series => [series.key, series]));
  return group.series.map(key => seriesByKey.get(key)).filter(Boolean);
}

function debugGraphVisibleChartGroups(seriesItems) {
  return jsDebugGraphChartGroups.filter(group => {
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

function debugGraphChartHtml(group, seriesItems, domain, buckets = []) {
  const groupSeries = debugGraphGroupSeriesItems(group, seriesItems);
  const plotSeries = group.stacked === true ? debugGraphStackedSeries(groupSeries) : groupSeries;
  const rawMax = Math.max(0, ...plotSeries.map(series => Number(series.plotMax ?? series.max) || 0));
  const max = debugGraphChartAxisMax(group, rawMax);
  const axisMax = max > 0 ? max : 0;
  const legendPlacement = group.legendPlacement === 'footer' ? 'footer' : 'head';
  const chartClasses = ['js-debug-chart'];
  if (legendPlacement === 'footer') chartClasses.push('js-debug-chart--legend-footer');
  if (group.dynamicAgentTokens === true) chartClasses.push('js-debug-chart--token-agents');
  return `<section class="${esc(chartClasses.join(' '))}" data-js-debug-chart="${esc(group.key)}"${group.stacked === true ? ' data-js-debug-chart-stacked="true"' : ''}>
    <div class="js-debug-chart-head">
      <span class="js-debug-chart-title">${esc(group.label)}</span>
      ${legendPlacement === 'head' ? debugGraphLegendHtml(groupSeries) : ''}
    </div>
    <div class="js-debug-chart-body">
      ${debugGraphAxisHtml(group, axisMax)}
      <div class="js-debug-plot">
        <svg class="js-debug-line-chart" viewBox="0 0 600 120" role="img" aria-label="${esc(group.label)}" preserveAspectRatio="none">
          ${group.kind === 'area' ? plotSeries.map(series => debugGraphAreaPathHtml(series, Math.max(axisMax, 1), domain)).join('') : ''}
          ${debugGraphGridLinesHtml(group, axisMax)}
          ${plotSeries.map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${groupSeries.map(series => debugGraphMovingAveragePolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${debugGraphDisconnectedOverlayHtml(buckets, domain)}
          ${debugGraphInteractionOverlayHtml()}
        </svg>
      </div>
      ${debugGraphXAxisHtml(domain)}
    </div>
    ${legendPlacement === 'footer' ? `<div class="js-debug-chart-legend-footer">${debugGraphLegendHtml(groupSeries)}</div>` : ''}
  </section>`;
}

function debugGraphSvgHtml(buckets, seriesItems, chartGroups = debugGraphVisibleChartGroups(seriesItems), nowMs = Date.now()) {
  const domain = debugGraphDomain(nowMs);
  return `<div class="js-debug-chart-shell">
    <div class="js-debug-chart-grid" data-js-debug-chart-grid data-js-debug-domain-start="${esc(Math.floor(domain.startMs))}" data-js-debug-domain-end="${esc(Math.floor(domain.endMs))}"${domain.zoomed ? ' data-js-debug-zoomed="true"' : ''}>${chartGroups.map(group => debugGraphChartHtml(group, seriesItems, domain, buckets)).join('')}</div>
  </div>`;
}

function debugGraphClassName(nowMs = Date.now()) {
  return `js-debug-graph${debugGraphDisplayBuckets(nowMs).length ? '' : ' js-debug-graph--empty'}${debugGraphZoomDomainValid() ? ' js-debug-graph--zoomed' : ''}`;
}

function debugGraphInnerHtml() {
  const nowMs = Date.now();
  activeJsDebugGraphRangeSeconds(nowMs);
  const controls = debugGraphControlsHtml(nowMs);
  const meta = debugGraphMetaHtml();
  const buckets = debugGraphDisplayBuckets(nowMs);
  if (!buckets.length) {
    return `${controls}${meta}<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
  }
  const seriesItems = debugGraphSeriesData(buckets);
  const chartGroups = debugGraphVisibleChartGroups(seriesItems);
  return `${controls}${meta}${debugGraphSvgHtml(buckets, seriesItems, chartGroups, nowMs)}`;
}

function debugGraphHtml() {
  return `<div class="${debugGraphClassName()}" data-js-debug-graph aria-label="${esc(t('debug.summary'))}">${debugGraphInnerHtml()}</div>`;
}

function debugGraphBucketSummary(nowMs = Date.now()) {
  activeJsDebugGraphRangeSeconds(nowMs);
  const buckets = debugGraphDisplayBuckets(nowMs, jsDebugGraphScaleSeconds, jsDebugGraphRangeSeconds);
  const availableRangeSeconds = debugGraphAvailableRangeOptions(nowMs).map(option => option.seconds);
  return {
    rawBuckets: jsDebugGraphRawBuckets.size,
    rollupBuckets: jsDebugGraphRollupBuckets.size,
    displayBuckets: buckets.length,
    eventRefs: jsDebugGraphEventBuckets.size,
    scaleSeconds: jsDebugGraphScaleSeconds,
    rangeSeconds: jsDebugGraphRangeSeconds,
    zoomed: debugGraphZoomDomainValid(),
    zoomRangeSeconds: debugGraphZoomDomainValid() ? (Number(jsDebugGraphZoomDomain.endMs) - Number(jsDebugGraphZoomDomain.startMs)) / 1000 : 0,
    availableRangeSeconds,
    retentionHours: jsDebugGraphRetentionMs / 60 / 60 / 1000,
    rawWindowSeconds: jsDebugGraphRawWindowMs / 1000,
    rollupBucketSeconds: jsDebugGraphRollupBucketMs / 1000,
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
  if (jsDebugStatsPollTimer && typeof clearInterval === 'function') clearInterval(jsDebugStatsPollTimer);
  jsDebugStatsPollTimer = null;
}

async function pollJsDebugStatsSample() {
  if (!jsDebugCollectionEnabled) return;
  if (!jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  if (jsDebugStatsPollInFlight || typeof apiFetchJsonQuiet !== 'function') return;
  jsDebugStatsPollInFlight = true;
  try {
    const clientId = jsDebugStatsClientIdForRequest();
    const tokenConsumer = jsDebugStatsTokenConsumerEnabled() ? '1' : '0';
    const payload = await apiFetchJsonQuiet(`/api/stats-sample?since=${encodeURIComponent(String(jsDebugStatsServerSequence || 0))}&client_id=${encodeURIComponent(clientId)}&token_consumer=${tokenConsumer}`, {cache: 'no-store'});
    recordJsDebugStatsSample(payload);
  } catch (_error) {
  } finally {
    jsDebugStatsPollInFlight = false;
  }
}

function scheduleJsDebugStatsHistoryFlush() {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible() || jsDebugStatsHistoryFlushTimer || typeof setTimeout !== 'function') return;
  jsDebugStatsHistoryFlushTimer = setTimeout(() => {
    jsDebugStatsHistoryFlushTimer = null;
    flushJsDebugStatsHistory();
  }, jsDebugStatsHistoryFlushMs);
}

async function flushJsDebugStatsHistory() {
  if (!jsDebugCollectionEnabled || !jsDebugStatsPanelVisible() || jsDebugStatsHistoryFlushInFlight || !jsDebugGraphPendingServerBuckets.size || typeof apiFetchJsonQuiet !== 'function') return;
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
  jsDebugStatsHistoryFlushInFlight = true;
  try {
    const payload = await apiFetchJsonQuiet('/api/stats-history', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({client_id: jsDebugStatsClientIdForRequest(), since: jsDebugStatsServerSequence || 0, records: chunk}),
    });
    debugGraphApplyServerHistory(payload?.history);
    scheduleJsDebugPanelRefresh();
  } catch (_error) {
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
    for (const record of held) {
      const key = `${Math.floor(Number(record.start) * 1000)}:${Math.floor(Number(record.duration) * 1000)}`;
      if (!jsDebugGraphPendingServerBuckets.has(key)) jsDebugGraphPendingServerBuckets.set(key, record);
    }
    jsDebugStatsHistoryFlushInFlight = false;
    if (jsDebugGraphPendingServerBuckets.size) scheduleJsDebugStatsHistoryFlush();
  }
}

function clearJsDebugServerHistory() {
  jsDebugStatsServerSequence = 0;
  jsDebugStatsServerUptimeSeconds = null;
  jsDebugStatsServerPid = null;
  jsDebugStatsServerStartedAt = null;
  jsDebugStatsServerRssBytes = null;
  if (jsDebugStatsHistoryFlushTimer) {
    clearTimeout(jsDebugStatsHistoryFlushTimer);
    jsDebugStatsHistoryFlushTimer = null;
  }
  if (typeof apiFetchJsonQuiet !== 'function') return;
  apiFetchJsonQuiet('/api/stats-history', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({client_id: jsDebugStatsClientIdForRequest(), clear: true}),
  }).then(payload => {
    debugGraphApplyServerHistory(payload?.history);
    scheduleJsDebugPanelRefresh();
  }).catch(() => {});
}

function startJsDebugStatsPolling() {
  if (!jsDebugCollectionEnabled || jsDebugStatsPollTimer) return;
  if (!jsDebugStatsPanelVisible()) return;
  pollJsDebugStatsSample();
  if (typeof setInterval === 'function') jsDebugStatsPollTimer = setInterval(pollJsDebugStatsSample, jsDebugStatsPollMs);
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('visibilitychange', () => {
    if (jsDebugStatsPanelVisible()) startJsDebugStatsPolling();
    else stopJsDebugStatsPolling();
  });
}

function jsDebugTextForClipboard() {
  const page = `${location.pathname || ''}${location.search || ''}${location.hash || ''}`;
  const counts = debugEventCounts();
  const header = [
    `JS Debug ${new Date().toISOString()}`,
    `page=${page || '/'}`,
    `events=${jsDebugEvents.length}`,
    `api=${counts.apiCalls}`,
    `sse=${counts.sseEvents}`,
    `errors=${counts.errors}`,
    `api_tx=${counts.apiRequestBytes}B`,
    `api_rx=${counts.apiResponseBytes}B`,
    `sse_rx=${counts.sseBytes}B`,
  ].join(' ');
  const apiSummaryRows = debugApiSummaryRows();
  const sseSummaryRows = debugSseSummaryRows();
  const sseLatencySummaryRows = debugSseLatencySummaryRows();
  const rows = jsDebugEvents.map(debugEventLineText);
  return [
    header,
    ...(apiSummaryRows.length ? ['Slow API by max latency:', ...apiSummaryRows, ''] : []),
    ...(sseSummaryRows.length ? ['Slow SSE server work:', ...sseSummaryRows, ''] : []),
    ...(sseLatencySummaryRows.length ? ['Slow SSE receive latency:', ...sseLatencySummaryRows, ''] : []),
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
          <button type="button" class="preferences-inline-action" data-js-debug-copy>${esc(t('debug.copy'))}</button>
          <button type="button" class="preferences-inline-action" data-js-debug-clear>${esc(t('debug.clear'))}</button>
        </div>
      </div>
      <textarea class="js-debug-log" data-js-debug-log readonly spellcheck="false" aria-label="${esc(t('debug.recent'))}">${esc(jsDebugTextForClipboard())}</textarea>
    </div>
    <div class="js-debug-subview js-debug-graph-view" ${debugSubViewAttrs('graph')}>${debugGraphHtml()}</div>`;
}

function createDebugPanel() {
  enableDebugMode();
  const panel = document.createElement('article');
  panel.className = 'panel js-debug-panel';
  panel.id = panelDomId(debugPaneItemId);
  panel.innerHTML = `
      <div class="panel-head preferences-panel-head">
        ${virtualPanelControlsHtml(debugPaneItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-copy panel-copy">
          <div id="panel-tab-${debugPaneItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.debug'))}</span></div>
          <div id="meta-${debugPaneItemId}" class="pane-info-bar-meta meta">${esc(debugMetaText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(debugPaneItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="preferences-body js-debug-body panel-overlay-root">
        <div id="panel-toasts-${debugPaneItemId}" class="panel-toast-stack"></div>
        <div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>
      </div>`;
  bindPanelShell(panel, debugPaneItemId);
  bindDebugPanel(panel);
  return panel;
}

function renderDebugPanels(options = {}) {
  if (dragSession != null) return;
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    const body = panel.querySelector('.js-debug-body');
    refreshDebugPanelFromEvents(panel, options);
    if (body && (options.force === true || !body.querySelector('[data-js-debug-log]'))) {
      body.innerHTML = `<div id="panel-toasts-${debugPaneItemId}" class="panel-toast-stack"></div><div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`;
      refreshDebugPanelFromEvents(panel, {force: true});
    }
    bindDebugPanel(panel);
  }
}

function refreshDebugPanelsFromEvents() {
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    refreshDebugPanelFromEvents(panel);
  }
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
  if (graph && !jsDebugGraphRangeSliderDragging) {
    graph.className = debugGraphClassName();
    graph.innerHTML = debugGraphInnerHtml();
  }
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
  jsDebugSubTab = normalizedJsDebugSubTab(tab);
  for (const panel of document.querySelectorAll('.js-debug-panel')) applyDebugSubTab(panel);
}

function setDebugGraphScale(value) {
  jsDebugGraphScaleSeconds = normalizedJsDebugGraphScale(value);
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    graph.className = debugGraphClassName();
    graph.innerHTML = debugGraphInnerHtml();
  }
}

function setDebugGraphRange(value, {render = true} = {}) {
  jsDebugGraphZoomDomain = null;
  jsDebugGraphRangeSeconds = normalizedJsDebugGraphRange(value);
  activeJsDebugGraphRangeSeconds();
  if (!render) return;
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    graph.className = debugGraphClassName();
    graph.innerHTML = debugGraphInnerHtml();
  }
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
  const x = (Math.max(0, Math.min(1, Number(ratio))) * 600).toFixed(1);
  graph.classList.add('js-debug-graph--hovering');
  graph.querySelectorAll('[data-js-debug-hover-line]').forEach(line => {
    line.setAttribute('x1', x);
    line.setAttribute('x2', x);
  });
}

function debugGraphClearInteractionLines(panel) {
  if (jsDebugGraphSelectionState) return;
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (graph) graph.classList.remove('js-debug-graph--hovering');
}

function debugGraphSetSelectionRects(panel, startRatio, endRatio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph]');
  if (!graph) return;
  const start = Math.max(0, Math.min(1, Number(startRatio)));
  const end = Math.max(0, Math.min(1, Number(endRatio)));
  const x = Math.min(start, end) * 600;
  const width = Math.abs(end - start) * 600;
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
    debugGraphSetSelectionRects(panel, jsDebugGraphSelectionState.startRatio, ratio);
    return;
  }
  const ratio = debugGraphPointerRatioForEvent(event);
  if (ratio == null) return;
  debugGraphSetInteractionLines(panel, ratio);
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
    for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
      graph.className = debugGraphClassName();
      graph.innerHTML = debugGraphInnerHtml();
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
  const reset = event.target.closest('[data-js-debug-zoom-reset]');
  if (reset && panel.contains(reset)) {
    event.preventDefault();
    clearDebugGraphZoom();
    return true;
  }
  const slider = event.target.closest('[data-js-debug-range-slider]');
  if (slider && panel.contains(slider)) {
    if (event.type === 'pointerdown') {
      jsDebugGraphRangeSliderDragging = true;
      return false;
    }
    if (event.type === 'input') {
      jsDebugGraphRangeSliderDragging = true;
      return setDebugGraphRangeFromSlider(slider, {render: false});
    }
    if (event.type === 'change' || event.type === 'pointerup') {
      jsDebugGraphRangeSliderDragging = false;
      return setDebugGraphRangeFromSlider(slider, {snap: true});
    }
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
  const scale = event.target.closest('[data-js-debug-scale]');
  if (scale && panel.contains(scale)) {
    event.preventDefault();
    setDebugGraphScale(scale.dataset.jsDebugScale);
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
        .catch(error => { statusErr(localizedHtml('status.copyFailed', {error})); });
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

startJsDebugStatsPolling();

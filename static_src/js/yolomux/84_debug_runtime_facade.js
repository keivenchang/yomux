// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Debug graph/history runtime declarations used by the following rendering partial.

const debugRuntimeFacades = new Map();

function registerDebugRuntimeFacade(name, facade) {
  const key = String(name || '');
  if (!key || !facade || typeof facade !== 'object' || debugRuntimeFacades.has(key)) return false;
  debugRuntimeFacades.set(key, Object.freeze({...facade}));
  return true;
}

function debugRuntimeFacade(name) {
  return debugRuntimeFacades.get(String(name || '')) || null;
}

const debugRuntimeState = {
  subTab: 'graph',
  graphRangeSeconds: 15 * 60,
  graphResolutionOverrideSeconds: 0,
  graphChartLayout: 0,
  serviceLoadMode: 'auto',
  graphHiddenCharts: null,
  graphVisibleCharts: null,
  statsUiPreferencesLoaded: false,
};

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
  return value === 'cost' || value === 'events' || value === 'system' || value === 'logs' ? value : 'graph';
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
  debugRuntimeState.graphRangeSeconds = normalizedJsDebugGraphRange(debugRuntimeState.graphRangeSeconds, nowMs);
  syncDebugGraphResolutionOverride(nowMs, {persist: true});
  return debugRuntimeState.graphRangeSeconds;
}

function loadJsDebugStatsUiPreferences() {
  if (debugRuntimeState.statsUiPreferencesLoaded) return;
  debugRuntimeState.statsUiPreferencesLoaded = true;
  let saved = safeJsonParse(window.localStorage?.getItem(jsDebugStatsUiPreferencesStorageKey), {});
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {};
  debugRuntimeState.subTab = legacyYoCostMigrationRequested ? 'cost' : normalizedJsDebugSubTab(saved.subTab);
  debugRuntimeState.graphRangeSeconds = normalizedJsDebugGraphRange(saved.rangeSeconds);
  debugRuntimeState.graphResolutionOverrideSeconds = Math.max(0, Number(saved.resolutionOverrideSeconds) || 0);
  debugRuntimeState.graphChartLayout = Math.max(0, Math.min(4, Math.round(Number(saved.chartLayout) || 0)));
  debugRuntimeState.serviceLoadMode = normalizedDebugGraphServiceLoadPreference(saved.serviceLoadMode);
  const hidden = new Set(jsDebugGraphDefaultHiddenChartKeys);
  const visible = new Set(Array.isArray(saved.visibleCharts) ? saved.visibleCharts.map(value => String(value || '')) : []);
  for (const key of visible) hidden.delete(key);
  for (const key of Array.isArray(saved.hiddenCharts) ? saved.hiddenCharts : []) hidden.add(String(key || ''));
  debugRuntimeState.graphHiddenCharts = hidden;
  debugRuntimeState.graphVisibleCharts = visible;
  // Respect a previously-persisted level selection (including an intentionally
  // empty one); only fresh state falls back to the warning+error default.
  const storedLogLevels = Array.isArray(saved.logLevels)
    ? saved.logLevels.map(value => String(value || '')).filter(value => jsDebugLogLevels.includes(value))
    : null;
  jsDebugLogsState.levels = new Set(storedLogLevels || jsDebugLogDefaultLevels);
  syncDebugGraphResolutionOverride(Date.now(), {persist: true});
  if (legacyYoCostMigrationRequested) saveJsDebugStatsUiPreferences();
}

function saveJsDebugStatsUiPreferences() {
  if (!debugRuntimeState.statsUiPreferencesLoaded) return;
  try {
    window.localStorage?.setItem(jsDebugStatsUiPreferencesStorageKey, JSON.stringify({
      subTab: debugRuntimeState.subTab,
      rangeSeconds: debugRuntimeState.graphRangeSeconds,
      resolutionOverrideSeconds: debugRuntimeState.graphResolutionOverrideSeconds,
      chartLayout: debugRuntimeState.graphChartLayout,
      serviceLoadMode: debugRuntimeState.serviceLoadMode,
      hiddenCharts: [...debugGraphHiddenChartKeys()].sort(),
      visibleCharts: [...(debugRuntimeState.graphVisibleCharts instanceof Set ? debugRuntimeState.graphVisibleCharts : [])].sort(),
      logLevels: [...jsDebugLogsState.levels].sort(),
    }));
  } catch (_) {
  }
}

function debugGraphHiddenChartKeys() {
  loadJsDebugStatsUiPreferences();
  if (!(debugRuntimeState.graphHiddenCharts instanceof Set)) debugRuntimeState.graphHiddenCharts = new Set();
  if (!(debugRuntimeState.graphVisibleCharts instanceof Set)) debugRuntimeState.graphVisibleCharts = new Set();
  return debugRuntimeState.graphHiddenCharts;
}

function debugGraphChartVisible(key) {
  const chartKey = String(key || '');
  if (chartKey === 'modelTokens' && !debugRuntimeState.graphVisibleCharts.has(chartKey)) return false;
  return !debugGraphHiddenChartKeys().has(chartKey);
}

function setDebugGraphChartVisible(key, visible) {
  const chartKey = String(key || '');
  if (!chartKey) return;
  const hidden = debugGraphHiddenChartKeys();
  if (visible) {
    hidden.delete(chartKey);
    debugRuntimeState.graphVisibleCharts.add(chartKey);
  } else {
    hidden.add(chartKey);
    debugRuntimeState.graphVisibleCharts.delete(chartKey);
  }
  saveJsDebugStatsUiPreferences();
  // A direct toggle/close owns this mutation. Passive SSE/timer paints defer
  // while a graph control is focused, but deferring the user's own activation
  // leaves aria-pressed and the chart body visibly stale until focus moves.
  refreshDebugGraphSurfaces({deferFocusedControl: false});
}

function jsDebugGraphRangeOptionIndex(rangeSeconds = debugRuntimeState.graphRangeSeconds, nowMs = Date.now()) {
  const options = debugGraphAvailableRangeOptions(nowMs);
  const normalized = normalizedJsDebugGraphRange(rangeSeconds, nowMs);
  return Math.max(0, options.findIndex(option => option.seconds === normalized));
}

function jsDebugGraphRangeLabel(seconds = debugRuntimeState.graphRangeSeconds, nowMs = Date.now()) {
  const options = debugGraphAvailableRangeOptions(nowMs);
  const normalized = normalizedJsDebugGraphRange(seconds, nowMs);
  return options.find(option => option.seconds === normalized)?.label || `${normalized}s`;
}

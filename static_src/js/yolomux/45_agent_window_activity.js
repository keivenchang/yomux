// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared Claude/Codex tmux-window activity state for pane tabs, Tabber, Info Bar, and popovers.

const AGENT_WINDOW_VISIBLE_TONES = Object.freeze([STATE_KEY.working, 'attention', 'cooldown']);
const AGENT_WINDOW_AGGREGATE_TONES = Object.freeze(['attention', 'cooldown', STATE_KEY.working]);

function agentWindowVisibleTone(value) {
  return AGENT_WINDOW_VISIBLE_TONES.includes(value);
}

function agentWindowPathEntries(agent) {
  const entries = [];
  const seen = new Set();
  const addEntry = (value, fallback = {}) => {
    let path = '';
    let mtime = Number(fallback.mtime || 0);
    let git = fallback.git && typeof fallback.git === 'object' ? fallback.git : null;
    if (value && typeof value === 'object') {
      path = String(value.path || value.root || '').trim();
      mtime = Number(value.mtime || mtime || 0);
      git = value.git && typeof value.git === 'object' ? value.git : git;
    } else {
      path = String(value || '').trim();
    }
    if (!path || seen.has(path)) return;
    seen.add(path);
    entries.push({path, mtime: Number.isFinite(mtime) ? mtime : 0, git});
  };
  for (const entry of Array.isArray(agent?.path_entries) ? agent.path_entries : []) addEntry(entry);
  for (const entry of Array.isArray(agent?.paths) ? agent.paths : []) addEntry(entry, {git: agent?.git});
  return entries;
}

function agentWindowPrimaryGit(agent) {
  if (agent?.git && typeof agent.git === 'object') return agent.git;
  const entry = agentWindowPathEntries(agent).find(item => item.git && typeof item.git === 'object');
  return entry?.git || null;
}

function agentWindowPrimaryPath(agent) {
  const entry = agentWindowPathEntries(agent).find(item => item.path);
  return entry?.path || String(agent?.path || '').trim();
}

function agentWindowIndex(agent) {
  const value = agent?.window_index ?? agent?.window;
  return value === null || value === undefined || String(value).trim() === '' ? null : tmuxWindowIndexKey(value);
}

function agentWindowPayloadKey(agent) {
  const index = agentWindowIndex(agent);
  const kind = agentWindowKind(agent?.kind);
  return index !== null && kind ? `${index}:${kind}` : '';
}

function agentWindowStateKey(state) {
  return String(state || '').trim();
}

function agentWindowIsWorkingState(state) {
  return agentWindowStateKey(state) === STATE_KEY.working;
}

function agentWindowIsAttentionState(state) {
  return AGENT_WINDOW_ATTENTION_STATES.has(agentWindowStateKey(state));
}

function agentWindowActivityTone(state) {
  const key = agentWindowStateKey(state);
  if (key === STATE_KEY.working) return STATE_KEY.working;
  if (key === 'cooldown') return 'cooldown';
  if (key === 'attention') return 'attention';
  if (key === 'active') return 'active';
  if (key === 'settled') return 'settled';
  return STATE_KEY.idle;
}

function agentWindowStateRank(state) {
  return {[STATE_KEY.working]: 0, [STATE_KEY.approval]: 1, [STATE_KEY.needsInput]: 2, [STATE_KEY.idle]: 3}[agentWindowStateKey(state)] ?? 9;
}

function agentWindowActivityVisualRank(state) {
  const tone = agentWindowActivityTone(state);
  if (tone === 'attention') return 0;
  // A live worker wins over a completed worker. Otherwise a yellow child can make the session
  // look stopped while another child is still doing work.
  if (tone === STATE_KEY.working) return 1;
  if (tone === 'cooldown') return 2;
  if (tone === 'active') return 3;
  return 9;
}

function agentWindowKind(value) {
  const key = String(value || '').trim().toLowerCase();
  return key === 'claude' || key === 'codex' ? key : '';
}

function agentWindowCanonicalLabel(windowIndex, agentKey, fallback = '') {
  const kind = agentWindowKind(agentKey);
  if (!kind) return String(fallback || '').trim();
  const index = tmuxWindowIndexKey(windowIndex);
  return index !== null ? `${index}:${kind}` : kind;
}

function normalizedAgentWindowPayload(agent) {
  const kind = agentWindowKind(agent?.kind);
  const windowIndex = tmuxWindowNumber(agent?.window_index ?? agent?.window);
  const windowText = windowIndex !== null ? String(windowIndex) : String(agent?.window || '').trim();
  const canonical = agentWindowCanonicalLabel(windowText || agent?.window_index, kind, agent?.window_label || agent?.label || kind);
  const pathEntries = agentWindowPathEntries(agent);
  const git = agentWindowPrimaryGit(agent);
  const normalized = {
    ...agent,
    kind,
    window: windowText,
    window_index: windowIndex,
    label: String(agent?.label || canonical),
    window_label: canonical,
    pid: Number.isFinite(Number(agent?.pid)) && Number(agent.pid) > 0 ? Math.floor(Number(agent.pid)) : agent?.pid,
    current: typeof agent?.current === 'boolean' ? agent.current : typeof agent?.window_active === 'boolean' ? agent.window_active : typeof agent?.active === 'boolean' ? agent.active : agent?.current,
    window_active: typeof agent?.window_active === 'boolean' ? agent.window_active : typeof agent?.current === 'boolean' ? agent.current : typeof agent?.active === 'boolean' ? agent.active : agent?.window_active,
    path: pathEntries[0]?.path || String(agent?.path || ''),
    paths: pathEntries.map(item => item.path),
    path_entries: pathEntries,
    git,
  };
  delete normalized.active;
  return normalized;
}

function agentWindowPayloadRows(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
}

function agentWindowObservedTs(agent) {
  for (const key of ['observed_ts', 'observedTs', 'captured_ts', 'updated_ts']) {
    const value = Number(agent?.[key] || 0);
    if (Number.isFinite(value) && value > 0) return value;
  }
  return 0;
}

function agentWindowStateMergeRank(state) {
  const key = agentWindowStateKey(state);
  if (agentWindowIsAttentionState(key)) return 0;
  if (key === STATE_KEY.working) return 1;
  if (key === 'cooldown') return 2;
  if (key === 'active') return 3;
  if (key === STATE_KEY.idle) return 4;
  return 9;
}

function agentWindowPayloadHasVisibleAttention(agent) {
  return agentWindowIsAttentionState(agentWindowStateKey(agent?.state)) && agent?.attention_acknowledged !== true;
}

function agentWindowPayloadAcknowledgesSameStatus(candidate, current) {
  const attentionKey = String(candidate?.attention_key || '');
  if (candidate?.attention_acknowledged === true && attentionKey && attentionKey === String(current?.attention_key || '')) return true;
  const cooldownKey = String(candidate?.cooldown_attention_key || '');
  return candidate?.cooldown_acknowledged === true && cooldownKey && cooldownKey === String(current?.cooldown_attention_key || '');
}

function agentWindowPayloadIsPreferred(candidate, current) {
  if (!current) return true;
  // The shared acknowledgement identifies one exact prompt/transition. Its fresh explicit true
  // must beat an older Tabber cache row for that same key, while a new key still re-arms normally.
  if (agentWindowPayloadAcknowledgesSameStatus(candidate, current)) return true;
  const candidateAttention = agentWindowPayloadHasVisibleAttention(candidate);
  const currentAttention = agentWindowPayloadHasVisibleAttention(current);
  // The parent Tab and its child button must not disagree because a later activity poll saw the
  // pane quiet after the prompt capture. A live, unacknowledged approval remains authoritative
  // until the acknowledgement state explicitly clears it.
  if (candidateAttention !== currentAttention) return candidateAttention;
  const candidateTs = agentWindowObservedTs(candidate);
  const currentTs = agentWindowObservedTs(current);
  if (candidateTs > 0 || currentTs > 0) {
    if (candidateTs !== currentTs) return candidateTs > currentTs;
  }
  const candidateRank = agentWindowStateMergeRank(candidate?.state);
  const currentRank = agentWindowStateMergeRank(current?.state);
  if (candidateRank !== currentRank) return candidateRank < currentRank;
  return true;
}

function mergedAgentWindowBaseRows(stateRows, activityRows, infoRows) {
  const rowsByKey = new Map();
  const looseRows = [];
  for (const row of [...infoRows, ...activityRows, ...stateRows]) {
    const key = agentWindowPayloadKey(row);
    if (!key) {
      looseRows.push(row);
      continue;
    }
    const current = rowsByKey.get(key);
    if (agentWindowPayloadIsPreferred(row, current)) rowsByKey.set(key, row);
  }
  return [...rowsByKey.values(), ...looseRows];
}

function agentWindowStatusVisualSignature(payload = {}) {
  const rows = agentWindowPayloadRows(payload?.agent_windows)
    .map(agent => normalizedAgentWindowPayload(agent))
    .map(agent => ({
      kind: agent.kind,
      window_index: agentWindowIndex(agent),
      state: agentWindowStateKey(agent.state),
      current: agentWindowPayloadCurrent(agent) === true,
      window_active: agent.window_active === true,
      working_elapsed_seconds: Number.isFinite(Number(agent.working_elapsed_seconds)) ? Math.floor(Number(agent.working_elapsed_seconds)) : null,
      working_stopped_ts: Number.isFinite(Number(agent.working_stopped_ts)) ? Number(agent.working_stopped_ts) : null,
      last_active_ts: Number.isFinite(Number(agent.last_active_ts)) ? Number(agent.last_active_ts) : null,
      idle_since: Number.isFinite(Number(agent.idle_since)) ? Number(agent.idle_since) : null,
    }))
    .sort((a, b) => String(a.window_index ?? '').localeCompare(String(b.window_index ?? '')) || a.kind.localeCompare(b.kind));
  return JSON.stringify(rows);
}

function mergeAgentWindowPayload(base, candidates) {
  const key = agentWindowPayloadKey(base);
  const enrich = candidates
    .map(rows => rows.find(row => agentWindowPayloadKey(row) === key))
    .find(Boolean) || {};
  const merged = {...enrich, ...base};
  if (!agentWindowPathEntries(merged).length && agentWindowPathEntries(enrich).length) {
    merged.path = enrich.path;
    merged.paths = enrich.paths;
    merged.path_entries = enrich.path_entries;
    merged.git = enrich.git;
  }
  if (!(merged.git && typeof merged.git === 'object') && enrich.git && typeof enrich.git === 'object') merged.git = enrich.git;
  if (typeof merged.current !== 'boolean' && typeof enrich.current === 'boolean') merged.current = enrich.current;
  if (typeof merged.window_active !== 'boolean' && typeof enrich.window_active === 'boolean') merged.window_active = enrich.window_active;
  if ((!Number.isFinite(Number(merged.pid)) || Number(merged.pid) <= 0) && Number.isFinite(Number(enrich.pid)) && Number(enrich.pid) > 0) merged.pid = enrich.pid;
  return normalizedAgentWindowPayload(merged);
}

function agentWindowPayloadCurrent(agent) {
  if (typeof agent?.current === 'boolean') return agent.current;
  if (typeof agent?.window_active === 'boolean') return agent.window_active;
  if (typeof agent?.active === 'boolean') return agent.active;
  return null;
}

function activeTmuxWindowIndexFromInfo(info = null) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  for (const pane of [
    panes.find(item => item?.window_active === true && item?.active === true),
    panes.find(item => item?.window_active === true),
    info?.selected_pane,
  ]) {
    const index = tmuxWindowIndexKey(pane?.window_index ?? pane?.window);
    if (index !== null) return index;
  }
  return null;
}

function agentWindowWithInfoActiveWindow(agent, activeIndex = null) {
  if (activeIndex === null) return agent;
  const agentIndex = agentWindowIndex(agent);
  if (agentIndex === null) return agent;
  const active = agentIndex === activeIndex;
  if (agent?.current === active && agent?.window_active === active) return agent;
  return {...agent, current: active, window_active: active};
}

function sessionAgentWindowScreenIsWorking(payload) {
  return String(payload?.screen?.key || '') === STATE_KEY.working;
}

function sessionAgentWindowStatusModel(session, info = null, autoPayload = null) {
  const statePayload = autoPayload || autoApproveStates.get(session) || {};
  const activityPayload = tabberActivityPayload?.agent_windows && typeof tabberActivityPayload.agent_windows === 'object'
    ? tabberActivityPayload.agent_windows[String(session || '')]
    : null;
  const stateRows = agentWindowPayloadRows(statePayload.agent_windows);
  const activityRows = agentWindowPayloadRows(activityPayload);
  const infoRows = agentWindowPayloadRows(info?.agent_windows);
  const source = mergedAgentWindowBaseRows(stateRows, activityRows, infoRows);
  const fallback = source.length ? source : (Array.isArray(info?.agents) ? info.agents : [])
    .map(agent => ({
      kind: agent?.kind || '',
      state: STATE_KEY.idle,
      pane_target: agent?.pane_target || '',
      window_label: agent?.window_label || '',
      transcript: agent?.transcript || '',
      transcript_id: agent?.transcript_id || agent?.session_id || agent?.agent_session_id || '',
      agent_session_id: agent?.agent_session_id || agent?.session_id || '',
      last_active_ts: 0,
      idle_since: null,
    }));
  const activeIndex = activeTmuxWindowIndexFromInfo(info);
  const agents = fallback
    .map(agent => mergeAgentWindowPayload(agent, [activityRows, infoRows, stateRows]))
    .map(agent => agentWindowWithInfoActiveWindow(agent, activeIndex))
    .filter(agent => agent.kind);
  for (const visualAgent of agentWindowAcknowledgementVisualAgents(session)) {
    if (agents.some(agent => agentWindowPayloadKey(agent) === agentWindowPayloadKey(visualAgent))) continue;
    agents.push(agentWindowWithInfoActiveWindow(visualAgent, activeIndex));
  }
  const hasAttributedWindows = agents.some(agent => agentWindowIndex(agent) !== null);
  const screenWorking = sessionAgentWindowScreenIsWorking(statePayload);
  const hasWorkingWindow = agents.some(agent => agentWindowIsWorkingState(agent.state));
  // The screen capture is authoritative for a current working turn even before the per-window
  // activity poll catches up. Promote one non-attention window here, at the shared model boundary,
  // so the window bar, parent tab, Tabber, and session working count all see the same row.
  const screenProxyIndex = screenWorking && !hasWorkingWindow
    ? agents.findIndex(agent => agentWindowPayloadCurrent(agent) === true && !agentWindowIsAttentionState(agent.state))
    : -1;
  const fallbackProxyIndex = screenWorking && !hasWorkingWindow && screenProxyIndex < 0
    ? agents.findIndex(agent => !agentWindowIsAttentionState(agent.state))
    : -1;
  const proxyIndex = screenProxyIndex >= 0 ? screenProxyIndex : fallbackProxyIndex;
  const effectiveAgents = proxyIndex >= 0
    ? agents.map((agent, index) => (index === proxyIndex ? {...agent, state: STATE_KEY.working, screen_working_proxy: true} : agent))
    : agents;
  return {agents: effectiveAgents, hasAttributedWindows, screenWorking, screenProxyIndex: proxyIndex};
}

function sessionAgentWindowStatusPayloads(session, info = null, autoPayload = null) {
  return sessionAgentWindowStatusModel(session, info, autoPayload).agents;
}

function sessionAgentWindowHasWorkingSignal(session, info = null, autoPayload = null) {
  const model = sessionAgentWindowStatusModel(session, info, autoPayload);
  return model.screenWorking || model.agents.some(agent => agentWindowIsWorkingState(agent.state));
}

function sessionAgentWindowStatusSummary(session, info = null, autoPayload = null) {
  const model = sessionAgentWindowStatusModel(session, info, autoPayload);
  const {agents, hasAttributedWindows} = model;
  if (!agents.length) return {agents, hasAttributedWindows, agent: null, item: null};
  let selected = null;
  const visibleItems = [];
  for (const agent of agents) {
    const item = agentWindowActivityIconForStatusItem(agent, agent.kind, session);
    const tone = agentWindowStatusToneForItem(item);
    if (!item || (tone !== 'acknowledged' && !agentWindowVisibleTone(tone))) continue;
    const rank = tone === 'acknowledged' ? 8 : agentWindowActivityVisualRank(item.state);
    const selectedRank = selected ? (selected.item.acknowledging === true ? 8 : agentWindowActivityVisualRank(selected.item.state)) : 99;
    const current = agentWindowPayloadCurrent(agent) === true;
    const selectedCurrent = selected ? agentWindowPayloadCurrent(selected.agent) === true : false;
    visibleItems.push({agent, item, tone});
    if (!selected || rank < selectedRank || (rank === selectedRank && current && !selectedCurrent)) selected = {agent, item};
  }
  if (!selected) return {agents, hasAttributedWindows, agent: null, item: null};
  const allAggregateTones = AGENT_WINDOW_AGGREGATE_TONES
    .filter(tone => visibleItems.some(entry => entry.tone === tone));
  const item = {
    ...selected.item,
    pulseActive: visibleItems.some(({item: child}) => child.pulseActive === true),
    transitionPulseActive: visibleItems.some(({item: child}) => child.transitionPulseActive === true),
    aggregateTones: allAggregateTones,
    allAggregateTones,
  };
  return {
    agents,
    hasAttributedWindows,
    ...selected,
    item,
    label: item.label || agentLabel(selected.agent?.kind),
  };
}

function windowViewModel(session, windowIndex, info = null, autoPayload = null) {
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (indexKey === null) return null;
  return sessionAgentWindowStatusPayloads(session, info, autoPayload)
    .find(agent => agentWindowIndex(agent) === indexKey) || null;
}

function agentWindowStatusForRecord(session, record, info = null) {
  const indexKey = tmuxWindowIndexKey(record?.index ?? record?.indexText);
  if (indexKey === null) return null;
  const rows = sessionAgentWindowStatusPayloads(session, info);
  return rows.find(agent => agentWindowIndex(agent) === indexKey) || null;
}

function agentWindowIdleSeconds(agent, nowSeconds = Date.now() / 1000) {
  const lastActive = Number(agent?.idle_since || agent?.last_active_ts || 0);
  return Number.isFinite(lastActive) && lastActive > 0 ? Math.max(0, nowSeconds - lastActive) : null;
}

const agentWindowActivityStates = new Map();
const agentWindowStoppedTimers = new Map();
const agentWindowTransitionPulseTimers = new Map();
const agentWindowAcknowledgedStops = new Map();
const agentWindowAcknowledgementVisuals = new Map();
const agentWindowActivityAcknowledgeDelayMs = 700;
let agentWindowActivityAnimationSyncFrame = 0;
let agentWindowActivityMutationObserver = null;
const agentWindowActivityPulseSelector = '.agent-window-activity, .status-indicator.heartbeat-pulse, .status-indicator.attention-pulse';
const agentWindowActivityPulseAnimationNames = new Set([
  'attention-ring-fade',
  'agent-status-acknowledgement-fade',
  'agent-status-opacity-pulse',
  'red-pill-fill-fade',
  'agent-symbol-glow-cadence',
]);

function agentWindowAcknowledgementVisualDurationMs() {
  return typeof attentionAnimationDurationMs === 'function'
    ? attentionAnimationDurationMs(agentStatusPulsePeriodMs)
    : Math.max(1, Number(agentStatusPulsePeriodMs) || 1);
}

function agentWindowTransitionGlowDurationSeconds() {
  return Math.max(0, Number(workflowTransitionGlowSeconds) || 0);
}

function agentWindowTransitionGlowActive(startedAt, nowSeconds = Date.now() / 1000) {
  const durationSeconds = agentWindowTransitionGlowDurationSeconds();
  const started = Number(startedAt) || 0;
  if (!Number.isFinite(started) || started <= 0 || durationSeconds <= 0) return false;
  return Math.max(0, Number(nowSeconds) - started) < durationSeconds;
}

function agentWindowTransitionPulseActive(startedAt, nowSeconds = Date.now() / 1000) {
  return agentWindowTransitionGlowActive(startedAt, nowSeconds);
}

function agentWindowTransitionStartedAt(previous = {}, tone, nowSeconds) {
  const previousStartedAt = Number(previous.transitionStartedAt || 0);
  if (previous.visualTone === tone && previousStartedAt > 0) return previousStartedAt;
  // Red/yellow are transition notifications only when work begins or ends. Moving between two
  // already-settled notification colors must not restart their opacity pulse.
  return !previous.visualTone || previous.visualTone === STATE_KEY.working ? nowSeconds : 0;
}

function scheduleAgentWindowStatusGlowRefresh(key, startedAt, options = {}) {
  const durationSeconds = agentWindowTransitionGlowDurationSeconds();
  const start = Number(startedAt) || 0;
  if (options.scheduleRefresh === false || !key || start <= 0 || durationSeconds <= 0) return;
  const untilMs = (start + durationSeconds) * 1000;
  if (untilMs <= Date.now()) return;
  scheduleAgentWindowStoppedRefresh(key, untilMs);
}

function scheduleAgentWindowTransitionPulseRefresh(key, startedAt, options = {}) {
  const start = Number(startedAt) || 0;
  if (options.scheduleRefresh === false || !key || start <= 0) return;
  const durationSeconds = agentWindowTransitionGlowDurationSeconds();
  if (durationSeconds <= 0) return;
  const untilMs = (start + durationSeconds) * 1000;
  if (untilMs <= Date.now()) return;
  const previous = agentWindowTransitionPulseTimers.get(key);
  if (previous?.untilMs === untilMs) return;
  if (previous?.timer) clearTimeout(previous.timer);
  const timer = setTimeout(() => {
    const current = agentWindowTransitionPulseTimers.get(key);
    if (!current || current.untilMs !== untilMs) return;
    agentWindowTransitionPulseTimers.delete(key);
    refreshAgentWindowActivityDisplays();
  }, Math.max(0, untilMs - Date.now()));
  agentWindowTransitionPulseTimers.set(key, {timer, untilMs});
}

function clearAgentWindowTransitionPulseRefresh(key) {
  const previous = agentWindowTransitionPulseTimers.get(key);
  if (previous?.timer) clearTimeout(previous.timer);
  agentWindowTransitionPulseTimers.delete(key);
}

function mutationTouchesAgentWindowActivity(mutation) {
  for (const node of mutation?.addedNodes || []) {
    if (node?.classList?.contains?.('agent-window-activity')) return true;
    if (node?.classList?.contains?.('status-indicator') && (node.classList.contains('heartbeat-pulse') || node.classList.contains('attention-pulse'))) return true;
    if (node?.querySelector?.('.agent-window-activity')) return true;
    if (node?.querySelector?.('.status-indicator.heartbeat-pulse, .status-indicator.attention-pulse')) return true;
  }
  return false;
}

function ensureAgentWindowActivityMutationObserver() {
  if (typeof statusPulseAnimationEnabled === 'function' && !statusPulseAnimationEnabled()) return;
  if (agentWindowActivityMutationObserver || typeof MutationObserver !== 'function' || !document?.body) return;
  agentWindowActivityMutationObserver = new MutationObserver(mutations => {
    if (mutations.some(mutationTouchesAgentWindowActivity)) scheduleAgentWindowActivityAnimationSync();
  });
  agentWindowActivityMutationObserver.observe(document.body, {childList: true, subtree: true});
}

function disconnectAgentWindowActivityMutationObserver() {
  if (!agentWindowActivityMutationObserver) return;
  agentWindowActivityMutationObserver.disconnect?.();
  agentWindowActivityMutationObserver = null;
}

function agentWindowActivityAnimationUsesGlobalPhase(node, name) {
  if (!agentWindowActivityPulseAnimationNames.has(name)) return false;
  if (name !== 'agent-status-acknowledgement-fade') return true;
  // Live acknowledgements begin fully opaque at the click and fade once. Only the looping
  // Preferences sample joins the global phase shared by the colored pulse examples.
  return node?.classList?.contains?.('agent-window-status-dot--acknowledgement-preview')
    || Boolean(node?.querySelector?.('.agent-window-status-dot--acknowledgement-preview'));
}

function syncAgentWindowPulseAnimationCurrentTime(node, nowMs = Date.now()) {
  const animations = typeof node?.getAnimations === 'function' ? node.getAnimations({subtree: true}) : [];
  for (const animation of animations) {
    const name = String(animation?.animationName || '').trim();
    if (!agentWindowActivityAnimationUsesGlobalPhase(node, name)) continue;
    const timing = animation.effect?.getTiming?.() || {};
    const duration = Number(timing.duration) || attentionAnimationDurationMs();
    if (!Number.isFinite(duration) || duration <= 0) continue;
    animation.currentTime = Number(nowMs) || 0;
  }
}

function syncAgentWindowActivityAnimationDelays(root = document) {
  const scope = root?.querySelectorAll ? root : document;
  const nodes = Array.from(scope?.querySelectorAll?.(agentWindowActivityPulseSelector) || []);
  if (!nodes.length) return;
  const nowMs = Date.now();
  const delay = typeof attentionAnimationClockDelay === 'function'
    ? attentionAnimationClockDelay(nowMs)
    : attentionAnimationDelay(nowMs);
  for (const node of nodes) {
    if (!node?.style) continue;
    if (!node.classList?.contains?.('status-indicator') && !node.querySelector?.('.agent-window-status-dot')) continue;
    const localDelay = node.style.getPropertyValue('--attention-animation-delay').trim();
    if (localDelay && localDelay !== delay) node.style.removeProperty('--attention-animation-delay');
    syncAgentWindowPulseAnimationCurrentTime(node, nowMs);
  }
}

function restartAgentWindowActivityPulseAnimations(root = document) {
  const scope = root?.querySelectorAll ? root : document;
  const nodes = Array.from(scope?.querySelectorAll?.(agentWindowActivityPulseSelector) || []);
  for (const node of nodes) {
    const animations = typeof node?.getAnimations === 'function' ? node.getAnimations({subtree: true}) : [];
    for (const animation of animations) {
      if (!agentWindowActivityAnimationUsesGlobalPhase(node, String(animation?.animationName || '').trim())) continue;
      animation.cancel?.();
      animation.play?.();
    }
  }
  syncAgentWindowActivityAnimationDelays(scope);
}

function scheduleAgentWindowActivityAnimationSync(root = document) {
  if (typeof statusPulseAnimationEnabled === 'function' && !statusPulseAnimationEnabled()) disconnectAgentWindowActivityMutationObserver();
  ensureAgentWindowActivityMutationObserver();
  syncAgentWindowActivityAnimationDelays(root);
  if (agentWindowActivityAnimationSyncFrame && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(agentWindowActivityAnimationSyncFrame);
  }
  if (typeof requestAnimationFrame !== 'function') return;
  agentWindowActivityAnimationSyncFrame = requestAnimationFrame(() => {
    agentWindowActivityAnimationSyncFrame = 0;
    syncAgentWindowActivityAnimationDelays(root);
  });
}

function agentWindowActivityTransitionKey(agentKey, options = {}) {
  const explicit = String(options.transitionKey || '').trim();
  if (explicit) return explicit;
  const kind = agentWindowKind(agentKey);
  if (!kind) return '';
  const session = String(options.session || '').trim();
  const windowIndex = tmuxWindowIndexKey(options.window_index ?? options.window);
  const pane = String(options.pane_target || options.pane || '').trim();
  // A tmux window index is stable across selection, while pane_target may arrive only in the
  // async readback. Use the pane only when no window identity exists so the acknowledgment visual
  // does not move to a different key immediately after the click.
  return [session, windowIndex ?? '', windowIndex === null ? pane : '', kind].join(':');
}

function scheduleAgentWindowStoppedRefresh(key, untilMs) {
  if (!key || !Number.isFinite(untilMs) || untilMs <= 0) return;
  const previous = agentWindowStoppedTimers.get(key);
  if (previous?.untilMs === untilMs) return;
  if (previous?.timer) clearTimeout(previous.timer);
  const delay = Math.max(0, untilMs - Date.now());
  const timer = setTimeout(() => {
    const current = agentWindowStoppedTimers.get(key);
    if (!current || current.untilMs !== untilMs) return;
    agentWindowStoppedTimers.delete(key);
    if (typeof renderPanels === 'function' && typeof activePaneItems === 'function') {
      renderPanels(activePaneItems());
    }
  }, delay);
  agentWindowStoppedTimers.set(key, {timer, untilMs});
}

function clearAgentWindowStoppedRefresh(key) {
  const previous = agentWindowStoppedTimers.get(key);
  if (previous?.timer) clearTimeout(previous.timer);
  agentWindowStoppedTimers.delete(key);
}

function agentWindowStoppedIsAcknowledged(key, stoppedAt) {
  const stoppedAtNumber = Number(stoppedAt || 0);
  const acknowledged = Number(agentWindowAcknowledgedStops.get(key) || 0);
  return Boolean(key && stoppedAtNumber > 0 && acknowledged > 0 && Math.abs(acknowledged - stoppedAtNumber) < 0.001);
}

function agentWindowAcknowledgementVisualActive(key, nowMs = Date.now()) {
  const visual = agentWindowAcknowledgementVisuals.get(key);
  if (!visual) return false;
  if (visual.untilMs > nowMs) return true;
  if (visual.timer) clearTimeout(visual.timer);
  agentWindowAcknowledgementVisuals.delete(key);
  return false;
}

function agentWindowAcknowledgementVisualAgents(session) {
  const sessionKey = String(session || '').trim();
  const agents = [];
  for (const [key, visual] of agentWindowAcknowledgementVisuals.entries()) {
    if (!agentWindowAcknowledgementVisualActive(key) || !visual.agent) continue;
    if (String(visual.agent.session || '') !== sessionKey) continue;
    agents.push(visual.agent);
  }
  return agents;
}

function clearAgentWindowAcknowledgementVisual(key) {
  const visual = agentWindowAcknowledgementVisuals.get(key);
  if (visual?.timer) clearTimeout(visual.timer);
  agentWindowAcknowledgementVisuals.delete(key);
}

function showAgentWindowAcknowledgementVisual(key, options = {}) {
  if (!key) return false;
  const acknowledgementKey = String(options.acknowledgementKey || '');
  const current = agentWindowAcknowledgementVisuals.get(key);
  if (agentWindowAcknowledgementVisualActive(key) && String(current?.acknowledgementKey || '') === acknowledgementKey) {
    if (options.refresh !== false && current?.refreshed !== true) {
      current.refreshed = true;
      refreshAgentWindowActivityDisplays();
    }
    return true;
  }
  const durationMs = agentWindowAcknowledgementVisualDurationMs();
  const startedAtMs = Date.now();
  const untilMs = startedAtMs + durationMs;
  clearAgentWindowAcknowledgementVisual(key);
  const timer = setTimeout(() => {
    const visual = agentWindowAcknowledgementVisuals.get(key);
    if (!visual || visual.untilMs !== untilMs) return;
    agentWindowAcknowledgementVisuals.delete(key);
    // The browser must retire the gray marker at the promised time even if the acknowledgement
    // request is slow; a later explicit server false still re-arms a genuinely new prompt.
    if (acknowledgementKey && typeof recordAttentionAcknowledgementKey === 'function') recordAttentionAcknowledgementKey(acknowledgementKey);
    refreshAgentWindowActivityDisplays();
  }, durationMs);
  const sourceAgent = options.agent && typeof options.agent === 'object' ? options.agent : null;
  const visualAgent = sourceAgent ? {
    ...sourceAgent,
    session: String(options.session || sourceAgent.session || ''),
    window_index: options.windowIndex ?? sourceAgent.window_index ?? sourceAgent.window,
    state: options.visualState === 'attention' ? (sourceAgent.state || STATE_KEY.needsInput) : (sourceAgent.state || STATE_KEY.idle),
    working_stopped_ts: Number(options.stoppedAt || sourceAgent.working_stopped_ts || sourceAgent.workingStoppedTs || 0),
  } : null;
  agentWindowAcknowledgementVisuals.set(key, {startedAtMs, untilMs, durationMs, timer, acknowledgementKey, refreshed: options.refresh !== false, agent: visualAgent});
  if (options.refresh !== false) refreshAgentWindowActivityDisplays();
  return true;
}

function agentWindowActivityAcknowledgementKey(kind, state, options = {}, signature = '') {
  const agentKind = agentWindowKind(kind);
  if (!agentKind) return '';
  const stateKey = String(state || '').trim();
  if (stateKey === 'cooldown') {
    if (options.cooldown_attention_key) return String(options.cooldown_attention_key);
  } else if (options.attention_key) {
    return String(options.attention_key);
  }
  if (typeof attentionAcknowledgementKey !== 'function') return '';
  const session = String(options.session || '').trim();
  const windowIndex = tmuxWindowIndexKey(options.window_index ?? options.window);
  const pane = String(options.pane_target || options.pane || '').trim();
  const keySignature = String(signature || options.attention_signature || options.screen_text || stateKey || '').trim();
  return session && windowIndex !== null && keySignature
    ? attentionAcknowledgementKey(['agent-window', session, windowIndex, pane, agentKind, stateKey, keySignature])
    : '';
}

function agentWindowActivityAcknowledgementKeyIsRecorded(key, options = {}) {
  if (!key) return false;
  if (options.attention_acknowledged === true || options.cooldown_acknowledged === true) return true;
  return typeof attentionAcknowledgementKeyIsRecorded === 'function'
    // A live per-window snapshot is more recent than the browser's optimistic acknowledgement
    // cache. Passing it through prevents an identical later ASK from inheriting the old ACK.
    ? attentionAcknowledgementKeyIsRecorded(key, options)
    : false;
}

function refreshAgentWindowActivityDisplays() {
  if (typeof renderPanels === 'function' && typeof activePaneItems === 'function') {
    renderPanels(activePaneItems(), {reason: 'agent-window-activity'});
  }
  if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
  if (typeof updatePanelWindowStepButtons === 'function' && typeof activePaneItems === 'function') {
    for (const session of activePaneItems()) {
      if (typeof isTmuxSession === 'function' && !isTmuxSession(session)) continue;
      updatePanelWindowStepButtons(session, transcriptMeta.sessions?.[session]);
    }
  }
  if (typeof renderSessionButtons === 'function') renderSessionButtons({force: true});
  if (typeof renderInfoPanel === 'function') renderInfoPanel();
  if (typeof refreshTabberPanels === 'function' && typeof fileExplorerMode !== 'undefined' && fileExplorerMode === 'tabber') refreshTabberPanels();
}

function acknowledgeAgentWindowStoppedTransition(transitionKey, stoppedAt = null, options = {}) {
  const key = String(transitionKey || '').trim();
  if (!key) return false;
  const previous = agentWindowActivityStates.get(key) || {};
  const stoppedAtNumber = Number(stoppedAt ?? previous.stoppedAt ?? 0);
  if (!Number.isFinite(stoppedAtNumber) || stoppedAtNumber <= 0) return false;
  const currentStoppedAt = Number(previous.stoppedAt || 0);
  if (!Number.isFinite(currentStoppedAt) || currentStoppedAt <= 0 || Math.abs(currentStoppedAt - stoppedAtNumber) >= 0.001) return false;
  if (agentWindowStoppedIsAcknowledged(key, stoppedAtNumber)) return false;
  agentWindowAcknowledgedStops.set(key, stoppedAtNumber);
  clearAgentWindowStoppedRefresh(key);
  clearAgentWindowTransitionPulseRefresh(key);
  if (options.refresh !== false) refreshAgentWindowActivityDisplays();
  return true;
}

function agentWindowActivityAcknowledgementTarget(session, windowIndex = null, options = {}) {
  const sessionKey = String(session || '').trim();
  if (!sessionKey || !isTmuxSession(sessionKey)) return null;
  const info = transcriptMeta.sessions?.[sessionKey] || null;
  const explicitIndex = windowIndex === null || windowIndex === undefined ? null : tmuxWindowIndexKey(windowIndex);
  let summaryIndex = null;
  if (explicitIndex === null && options.preferSummary === true && typeof sessionStatusAgentWindowSummaryForTab === 'function') {
    const summary = sessionStatusAgentWindowSummaryForTab(sessionKey, info, autoApproveStates.get(sessionKey));
    if (['attention', 'cooldown'].includes(summary?.item?.state)) {
      summaryIndex = tmuxWindowIndexKey(summary?.agent?.window_index ?? summary?.agent?.window);
    }
  }
  const activeIndex = explicitIndex !== null
    ? explicitIndex
    : summaryIndex !== null
      ? summaryIndex
    : typeof tmuxWindowCurrentActiveIndex === 'function'
      ? tmuxWindowCurrentActiveIndex(sessionKey, info)
      : typeof tmuxWindowInfoActiveIndex === 'function'
        ? tmuxWindowInfoActiveIndex(info)
        : null;
  if (activeIndex === null) return null;
  const agent = windowViewModel(sessionKey, activeIndex, info);
  if (!agent) return null;
  const itemOptions = agentWindowActivityOptionsForStatus(agent, sessionKey, {scheduleRefresh: false});
  const item = agentWindowActivityIconForStatusItem(agent, agent.kind, sessionKey, itemOptions);
  if (item?.acknowledged === true) return null;
  if (!['attention', 'cooldown'].includes(item?.state)) return null;
  const transitionKey = agentWindowActivityTransitionKey(agent.kind, itemOptions);
  const previous = transitionKey ? agentWindowActivityStates.get(transitionKey) : null;
  const stoppedAt = Number(previous?.stoppedAt || itemOptions.working_stopped_ts || 0);
  const ackKey = item.state === 'cooldown'
    ? agentWindowActivityAcknowledgementKey(agent.kind, 'cooldown', itemOptions, stoppedAt)
    : agentWindowActivityAcknowledgementKey(agent.kind, agentWindowStateKey(agent.state), itemOptions, itemOptions.attention_signature || itemOptions.screen_text || agentWindowStateKey(agent.state));
  if (ackKey) return {ackKey, transitionKey, stoppedAt, state: item.state, agent};
  return transitionKey && stoppedAt > 0 ? {transitionKey, stoppedAt, state: item.state, agent} : null;
}

function acknowledgeAgentWindowActivity(session, windowIndex = null, options = {}) {
  const target = agentWindowActivityAcknowledgementTarget(session, windowIndex, options);
  if (!target) return false;
  showAgentWindowAcknowledgementVisual(target.transitionKey, {
    ...options,
    acknowledgementKey: target.ackKey,
    agent: target.agent,
    session,
    windowIndex,
    visualState: target.state,
    stoppedAt: target.stoppedAt,
  });
  const delayMs = Math.max(0, Number(options.delayMs) || 0);
  const acknowledgeStoppedTransition = () => (
    target.state === 'cooldown'
      ? acknowledgeAgentWindowStoppedTransition(target.transitionKey, target.stoppedAt, {...options, delayMs: 0})
      : false
  );
  if (target.ackKey && typeof acknowledgeAttentionKeys === 'function') {
    const posted = acknowledgeAttentionKeys([target.ackKey], options);
    if (target.state === 'cooldown' && target.transitionKey && Number(target.stoppedAt || 0) > 0) {
      if (delayMs > 0) {
        setTimeout(acknowledgeStoppedTransition, delayMs);
      } else {
        acknowledgeStoppedTransition();
      }
    }
    return posted || target.state === 'cooldown';
  }
  if (delayMs > 0) {
    setTimeout(() => {
      acknowledgeStoppedTransition();
    }, delayMs);
    return true;
  }
  return acknowledgeStoppedTransition();
}

function agentWindowActivityLabel(agentKey, state, acknowledged = false) {
  const kind = agentWindowKind(agentKey);
  const stateKey = agentWindowStateKey(state);
  const status = stateKey === 'cooldown'
    ? t('state.cooldown')
    : stateKey === 'active'
      ? t('state.active')
      : stateDef(stateKey).label;
  return t('state.agentStatus', {
    agent: agentLabel(kind),
    status: acknowledged ? t('state.statusAcknowledged', {status}) : status,
  });
}

function agentWindowActivityIcon(agentKey, state, idleSeconds, options = {}) {
  const kind = agentWindowKind(agentKey);
  if (!kind) return null;
  const nowSeconds = Number.isFinite(Number(options.nowSeconds)) ? Number(options.nowSeconds) : Date.now() / 1000;
  const transitionKey = agentWindowActivityTransitionKey(kind, options);
  const previous = transitionKey ? (agentWindowActivityStates.get(transitionKey) || {}) : {};
  const stateKey = agentWindowStateKey(state);
  const current = options.current === true || options.window_active === true;
  const acknowledgementVisualActive = transitionKey ? agentWindowAcknowledgementVisualActive(transitionKey) : false;
  const acknowledgementVisual = acknowledgementVisualActive ? agentWindowAcknowledgementVisuals.get(transitionKey) : null;
  const acknowledgementTiming = acknowledgementVisual ? {
    acknowledgementDurationMs: acknowledgementVisual.durationMs,
    acknowledgementElapsedMs: Math.max(0, Date.now() - acknowledgementVisual.startedAtMs),
  } : {};
  const acknowledgementVisualTone = ['attention', 'cooldown'].includes(previous.visualTone) ? previous.visualTone : '';
  // Switching the clicked tmux window can immediately promote its screen capture to `working`.
  // Preserve the acknowledged red/yellow state during its promised gray interval instead of
  // replacing it with a green play before the user can see the acknowledgement.
  if (acknowledgementVisualActive && acknowledgementVisualTone) {
    return {
      state: acknowledgementVisualTone,
      icon: '●',
      label: agentWindowActivityLabel(kind, acknowledgementVisualTone === 'attention' ? STATE_KEY.needsInput : 'cooldown', true),
      pulseActive: false,
      transitionPulseActive: false,
      acknowledged: false,
      acknowledging: true,
      ...acknowledgementTiming,
    };
  }
  if (agentWindowIsAttentionState(stateKey)) {
    const ackKey = agentWindowActivityAcknowledgementKey(kind, stateKey, options, options.attention_signature || options.screen_text || stateKey);
    const recordedAcknowledgement = agentWindowActivityAcknowledgementKeyIsRecorded(ackKey, {...options, cooldown_acknowledged: false});
    const acknowledging = acknowledgementVisualActive;
    const acknowledged = recordedAcknowledgement && !acknowledging;
    const transitionStartedAt = agentWindowTransitionStartedAt(previous, 'attention', nowSeconds);
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowActivityStates.set(transitionKey, {
        state: stateKey,
        visualTone: 'attention',
        seenWorking: previous.seenWorking === true,
        stoppedAt: Number(previous.stoppedAt) || 0,
        attentionKey: ackKey,
        transitionStartedAt,
      });
      scheduleAgentWindowStatusGlowRefresh(transitionKey, transitionStartedAt, options);
      if (!acknowledged) scheduleAgentWindowTransitionPulseRefresh(transitionKey, transitionStartedAt, options);
      else clearAgentWindowTransitionPulseRefresh(transitionKey);
    }
    return {
      state: 'attention',
      icon: '●',
      label: agentWindowActivityLabel(kind, STATE_KEY.needsInput, acknowledged),
      pulseActive: acknowledged ? false : agentWindowTransitionGlowActive(transitionStartedAt, nowSeconds),
      transitionPulseActive: acknowledged ? false : agentWindowTransitionPulseActive(transitionStartedAt, nowSeconds),
      acknowledged,
      acknowledging,
      ...(acknowledging ? acknowledgementTiming : {}),
    };
  }
  if (agentWindowIsWorkingState(stateKey)) {
    const transitionStartedAt = agentWindowTransitionStartedAt(previous, STATE_KEY.working, nowSeconds);
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowAcknowledgedStops.delete(transitionKey);
      agentWindowActivityStates.set(transitionKey, {state: STATE_KEY.working, visualTone: STATE_KEY.working, seenWorking: true, stoppedAt: 0, transitionStartedAt});
      scheduleAgentWindowTransitionPulseRefresh(transitionKey, transitionStartedAt, options);
    }
    return {
      state: STATE_KEY.working,
      icon: '●',
      label: agentWindowActivityLabel(kind, STATE_KEY.working),
      pulseActive: true,
      transitionPulseActive: true,
    };
  }
  const workingStoppedTs = Number(options.working_stopped_ts || options.workingStoppedTs || 0);
  let stoppedAt = Number.isFinite(workingStoppedTs) && workingStoppedTs > 0 ? workingStoppedTs : 0;
  const seenWorking = previous.seenWorking === true || stoppedAt > 0;
  if (!stoppedAt && previous.state === STATE_KEY.working) stoppedAt = nowSeconds;
  if (!stoppedAt && Number(previous.stoppedAt) > 0) stoppedAt = Number(previous.stoppedAt);
  const cooldownTransitionStartedAt = agentWindowTransitionStartedAt(previous, 'cooldown', nowSeconds);
  if (transitionKey) agentWindowActivityStates.set(transitionKey, {state: String(state || STATE_KEY.idle), visualTone: seenWorking && stoppedAt > 0 ? 'cooldown' : '', seenWorking, stoppedAt, transitionStartedAt: cooldownTransitionStartedAt});
  if (seenWorking && stoppedAt > 0) {
    const cooldownAckKey = agentWindowActivityAcknowledgementKey(kind, 'cooldown', options, stoppedAt);
    const recordedAcknowledgement = agentWindowStoppedIsAcknowledged(transitionKey, stoppedAt) || agentWindowActivityAcknowledgementKeyIsRecorded(cooldownAckKey, {...options, attention_acknowledged: false});
    const acknowledging = acknowledgementVisualActive;
    const acknowledged = recordedAcknowledgement && !acknowledging;
    scheduleAgentWindowStatusGlowRefresh(transitionKey, cooldownTransitionStartedAt, options);
    if (transitionKey) {
      if (!acknowledged) scheduleAgentWindowTransitionPulseRefresh(transitionKey, cooldownTransitionStartedAt, options);
      else clearAgentWindowTransitionPulseRefresh(transitionKey);
    }
    return {
      state: 'cooldown',
      icon: '●',
      label: agentWindowActivityLabel(kind, 'cooldown', acknowledged),
      pulseActive: acknowledged ? false : agentWindowTransitionGlowActive(cooldownTransitionStartedAt, nowSeconds),
      transitionPulseActive: acknowledged ? false : agentWindowTransitionPulseActive(cooldownTransitionStartedAt, nowSeconds),
      acknowledged,
      acknowledging,
      ...(acknowledging ? acknowledgementTiming : {}),
    };
  }
  if (current) return {state: 'active', icon: '', label: agentWindowActivityLabel(kind, 'active')};
  return null;
}

function agentWindowActivityOptionsForStatus(agent, session = '', options = {}) {
  return {
    session,
    window: agent?.window,
    window_index: agent?.window_index,
    pane: agent?.pane,
    pane_target: agent?.pane_target,
    current: agentWindowPayloadCurrent(agent) === true,
    window_active: agent?.window_active === true,
    working_stopped_ts: agent?.working_stopped_ts,
    attention_key: agent?.attention_key,
    attention_acknowledged: agent?.attention_acknowledged === true,
    cooldown_attention_key: agent?.cooldown_attention_key,
    cooldown_acknowledged: agent?.cooldown_acknowledged === true,
    attention_signature: agent?.attention_signature,
    screen_text: agent?.screen_text,
    ...options,
  };
}

function agentWindowActivityIconForStatusItem(agent, agentKey = agent?.kind, session = '', options = {}) {
  const itemOptions = agentWindowActivityOptionsForStatus(agent, session, options);
  return agentWindowActivityIcon(agentKey, agent?.state, agentWindowIdleSeconds(agent, itemOptions.nowSeconds), itemOptions);
}

function agentWindowStatusToneClass(tone) {
  return tone === STATE_KEY.working ? 'working' : String(tone || '');
}

function agentWindowStatusToneForItem(item) {
  if (item?.acknowledging === true) return 'acknowledged';
  if (item?.acknowledged === true) return '';
  const tone = item?.state ? agentWindowActivityTone(item.state) : '';
  return agentWindowVisibleTone(tone) ? tone : '';
}

function agentWindowActivityToneWrapperClass(tone) {
  const normalizedTone = agentWindowActivityTone(tone);
  return agentWindowVisibleTone(normalizedTone)
    ? `agent-window-activity--${agentWindowStatusToneClass(normalizedTone)}`
    : '';
}

function agentWindowStatusSampleItem(tone, options = {}) {
  const state = agentWindowActivityTone(tone);
  const pulse = options.pulse === true;
  return {
    state,
    icon: String(options.icon || '●'),
    label: String(options.label || ''),
    pulseActive: pulse,
    transitionPulseActive: pulse,
    acknowledged: false,
    acknowledging: options.acknowledging === true,
  };
}

function agentWindowStatusDotHtmlForTone(tone, options = {}) {
  const subwindow = options.surface === 'subwindow';
  return agentWindowStatusDotHtml(agentWindowStatusSampleItem(tone, options), {
    animate: options.pulse === true,
    subwindowGlyphPulse: subwindow,
    acknowledgementPreview: options.acknowledging === true,
    label: options.label,
  });
}

function agentWindowStatusDotHtml(item, options = {}) {
  if (!item || item.acknowledged === true) return '';
  const acknowledging = item.acknowledging === true;
  const tone = agentWindowStatusToneForItem(item);
  if (!tone) return '';
  const animate = options.animate !== false;
  const pulse = !acknowledging && animate && item.pulseActive !== false;
  const subwindowPulse = pulse;
  const transitionPulse = !acknowledging && animate && item.transitionPulseActive === true && item.acknowledged !== true;
  const transitionGlow = pulse && agentWindowVisibleTone(tone);
  const subwindowGlyphPulse = options.subwindowGlyphPulse === true && subwindowPulse && agentWindowVisibleTone(tone);
  // Acknowledged is the temporary gray color/timing tone, not a new shape. Keep the original
  // play/stop/pause modifier so live feedback and the Preferences example use the same renderer.
  const acknowledgementShapeClass = acknowledging && agentWindowVisibleTone(item.state)
    ? `status-indicator--${item.state}`
    : '';
  const aggregateTones = Array.isArray(item.aggregateTones)
    ? item.aggregateTones.filter(agentWindowVisibleTone).slice(0, AGENT_WINDOW_VISIBLE_TONES.length)
    : [];
  const allAggregateTones = Array.isArray(item.allAggregateTones)
    ? item.allAggregateTones.filter(agentWindowVisibleTone)
    : aggregateTones;
  const aggregateToneClasses = aggregateTones.length > 1
    ? [
      'agent-window-status-dot--segmented',
      `agent-window-status-dot--${aggregateTones.join('-')}`,
      ...allAggregateTones.map(value => `agent-window-status-dot--tone-${value}`),
    ]
    : [];
  const classes = statusIndicatorDotClasses(
    tone,
    'agent-window-activity-icon',
    'agent-window-status-dot',
    `agent-window-activity-icon--${item.state}`,
    acknowledging ? 'status-indicator--acknowledged' : '',
    acknowledging ? 'agent-window-status-dot--acknowledging' : '',
    acknowledging && options.acknowledgementPreview === true ? 'agent-window-status-dot--acknowledgement-preview' : '',
    acknowledgementShapeClass,
    transitionGlow ? 'agent-window-status-dot--transition-glow' : '',
    transitionPulse ? 'agent-window-status-dot--transition-pulse' : '',
    subwindowGlyphPulse ? 'agent-window-status-dot--subwindow-pulse' : '',
    aggregateToneClasses,
    {pulse},
  );
  const label = String(options.label || '').trim();
  const accessibility = label ? ` role="img" aria-label="${esc(label)}"` : ' aria-hidden="true"';
  return `<span class="${esc(classes)}"${accessibility}>${esc(item.icon)}</span>`;
}

function agentWindowActivityStyleAttribute(tone, item = {}, options = {}) {
  if (tone !== 'active' && !agentWindowVisibleTone(tone)) return '';
  const styles = [];
  const pulseEnabled = item?.pulseActive !== false && typeof statusPulseAnimationEnabled === 'function' && statusPulseAnimationEnabled();
  const subwindowPulse = item?.pulseActive !== false;
  const subwindowGlyphPulse = options.subwindowGlyphPulse === true && subwindowPulse && agentWindowVisibleTone(tone);
  const hasAnimationDelayStyle = pulseEnabled || subwindowGlyphPulse;
  if (hasAnimationDelayStyle && typeof attentionAnimationStyle === 'function') styles.push(attentionAnimationStyle());
  if (item?.transitionPulseActive === true && item?.acknowledged !== true) {
    const durationMs = Math.max(1, Number(agentStatusPulsePeriodMs) || 1);
    styles.push(`--agent-status-transition-pulse-duration: ${durationMs / 1000}s`);
    if (!hasAnimationDelayStyle && typeof attentionAnimationStyle === 'function') styles.push(attentionAnimationStyle(Date.now(), durationMs));
  }
  if (item?.acknowledging === true && options.acknowledgementPreview !== true) {
    const durationMs = Math.max(1, Number(item.acknowledgementDurationMs) || agentWindowAcknowledgementVisualDurationMs());
    const elapsedMs = Math.max(0, Math.min(durationMs, Number(item.acknowledgementElapsedMs) || 0));
    styles.push(`--agent-status-acknowledgement-duration: ${durationMs / 1000}s`);
    styles.push(`--agent-status-acknowledgement-delay: ${-elapsedMs / 1000}s`);
  }
  return styles.length ? ` style="${esc(styles.join('; '))}"` : '';
}

function agentWindowActivityMarkupSignature(value) {
  // A new ball needs a fresh wall-clock phase, but phase alone is not a semantic DOM change. Ignore
  // only this property when a renderer decides whether an existing window bar can stay mounted.
  return String(value || '').replace(/--attention-animation-delay:\s*-?[0-9.]+s;?\s*/g, '');
}

function agentWindowActivityIconHtml(agentKey, state, idleSeconds, options = {}) {
  const kind = agentWindowKind(agentKey);
  if (!kind) return '';
  const item = options.item || agentWindowActivityIcon(kind, state, idleSeconds, options);
  const acknowledged = item?.acknowledged === true;
  const acknowledging = item?.acknowledging === true;
  const stateKey = item?.state || 'idle-recent';
  const label = options.label || item?.label || agentLabel(kind);
  const statusOnly = options.statusOnly === true || options.hideAgentIcon === true;
  // Acknowledgement clears the transient state glyph, never the stable Claude/Codex identity on a
  // sub-window. Parent Tab circles are status-only, so they still disappear entirely.
  if (acknowledged && statusOnly) return '';
  const subwindowGlyphPulse = options.subwindowGlyphPulse === true || (options.subwindowGlyphPulse !== false && statusOnly !== true);
  const agentClasses = [
    'agent-window-activity-icon',
    'agent-window-agent-icon',
    `agent-window-activity-icon--${stateKey}`,
    `agent-window-agent-icon--${stateKey}`,
    item?.state === 'active' ? 'heartbeat-pulse' : '',
  ].filter(Boolean).join(' ');
  const markerHtml = acknowledged ? '' : agentWindowStatusDotHtml(item, {
    animate: options.animate !== false,
    subwindowGlyphPulse,
    acknowledgementPreview: options.acknowledgementPreview === true,
  });
  if (statusOnly && !markerHtml) return '';
  const placeholderHtml = options.reserveStatusSlot === true && !statusOnly && !markerHtml
    ? '<span class="agent-window-status-placeholder" aria-hidden="true"></span>'
    : '';
  const tone = item?.state ? agentWindowActivityTone(item.state) : '';
  const style = agentWindowActivityStyleAttribute(tone, item, {
    subwindowGlyphPulse,
    acknowledgementPreview: options.acknowledgementPreview === true,
  });
  const toneWrapperClass = agentWindowActivityToneWrapperClass(item?.state);
  const wrapperClasses = [
    'agent-window-activity',
    subwindowGlyphPulse ? 'agent-window-activity--subwindow' : '',
    toneWrapperClass || `agent-window-activity--${stateKey}`,
    acknowledging ? 'agent-window-activity--acknowledging' : '',
    acknowledged ? 'agent-window-activity--acknowledged' : '',
    statusOnly ? 'agent-window-activity--status-only' : '',
  ].filter(Boolean).join(' ');
  const agentHtml = statusOnly ? '' : agentIcon(kind, {label, className: agentClasses});
  const statusHtml = `${markerHtml}${placeholderHtml}`;
  const contentHtml = options.statusBeforeAgent === true ? `${statusHtml}${agentHtml}` : `${agentHtml}${statusHtml}`;
  return `<span class="${esc(wrapperClasses)}" title="${esc(label)}" aria-label="${esc(label)}"${style}>${contentHtml}</span>`;
}

function agentWindowActivityIconHtmlForStatus(agent, agentKey = agent?.kind, session = '', extraOptions = {}) {
  const options = agentWindowActivityOptionsForStatus(agent, session);
  return agentWindowActivityIconHtml(agentKey, agent?.state, agentWindowIdleSeconds(agent), {
    ...options,
    ...extraOptions,
    item: agentWindowActivityIconForStatusItem(agent, agentKey, session, options),
  });
}

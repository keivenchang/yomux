// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared Claude/Codex tmux-window activity state for pane tabs, Tabber, Info Bar, and popovers.

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

function agentWindowPayloadKey(agent) {
  const index = tmuxWindowIndexKey(agent?.window_index ?? agent?.window);
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
  if (tone === 'cooldown') return 1;
  if (tone === STATE_KEY.working) return 2;
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

function agentWindowPayloadIsPreferred(candidate, current) {
  if (!current) return true;
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
      window_index: tmuxWindowIndexKey(agent.window_index ?? agent.window),
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
  const agentIndex = tmuxWindowIndexKey(agent?.window_index ?? agent?.window);
  if (agentIndex === null) return agent;
  const active = agentIndex === activeIndex;
  if (agent?.current === active && agent?.window_active === active) return agent;
  return {...agent, current: active, window_active: active};
}

function sessionAgentWindowStatusPayloads(session, info = null, autoPayload = null) {
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
  return fallback
    .map(agent => mergeAgentWindowPayload(agent, [activityRows, infoRows, stateRows]))
    .map(agent => agentWindowWithInfoActiveWindow(agent, activeIndex))
    .filter(agent => agent.kind);
}

function windowViewModel(session, windowIndex, info = null, autoPayload = null) {
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (indexKey === null) return null;
  return sessionAgentWindowStatusPayloads(session, info, autoPayload)
    .find(agent => tmuxWindowIndexKey(agent.window_index ?? agent.window) === indexKey) || null;
}

function agentWindowStatusForRecord(session, record, info = null) {
  const indexKey = tmuxWindowIndexKey(record?.index ?? record?.indexText);
  if (indexKey === null) return null;
  const rows = sessionAgentWindowStatusPayloads(session, info);
  return rows.find(agent => tmuxWindowIndexKey(agent.window_index ?? agent.window) === indexKey) || null;
}

function agentWindowIdleSeconds(agent, nowSeconds = Date.now() / 1000) {
  const lastActive = Number(agent?.idle_since || agent?.last_active_ts || 0);
  return Number.isFinite(lastActive) && lastActive > 0 ? Math.max(0, nowSeconds - lastActive) : null;
}

const agentWindowActivityStates = new Map();
const agentWindowStoppedTimers = new Map();
const agentWindowAcknowledgedStops = new Map();
const agentWindowActivityAcknowledgeDelayMs = 1000;
let agentWindowActivityAnimationSyncFrame = 0;
let agentWindowActivityMutationObserver = null;
const agentWindowActivityPulseSelector = '.agent-window-activity, .status-indicator.heartbeat-pulse, .status-indicator.attention-pulse';
const agentWindowActivityPulseAnimationNames = new Set([
  'attention-ring-fade',
  'working-ball-hard-flash',
  'red-pill-fill-fade',
  'agent-symbol-glow-cadence',
]);

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
  if (agentWindowActivityMutationObserver || typeof MutationObserver !== 'function' || !document?.body) return;
  agentWindowActivityMutationObserver = new MutationObserver(mutations => {
    if (mutations.some(mutationTouchesAgentWindowActivity)) scheduleAgentWindowActivityAnimationSync();
  });
  agentWindowActivityMutationObserver.observe(document.body, {childList: true, subtree: true});
}

function syncAgentWindowPulseAnimationCurrentTime(node, nowMs = Date.now()) {
  const animations = typeof node?.getAnimations === 'function' ? node.getAnimations({subtree: true}) : [];
  for (const animation of animations) {
    const name = String(animation?.animationName || '').trim();
    if (!agentWindowActivityPulseAnimationNames.has(name)) continue;
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

function scheduleAgentWindowActivityAnimationSync(root = document) {
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
  return [session, windowIndex ?? '', pane, kind].join(':');
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
    ? attentionAcknowledgementKeyIsRecorded(key)
    : false;
}

function refreshAgentWindowActivityDisplays() {
  if (typeof renderPanels === 'function' && typeof activePaneItems === 'function') {
    renderPanels(activePaneItems(), {reason: 'agent-window-activity'});
  }
  if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
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
  if (!['attention', 'cooldown'].includes(item?.state)) return null;
  const transitionKey = agentWindowActivityTransitionKey(agent.kind, itemOptions);
  const previous = transitionKey ? agentWindowActivityStates.get(transitionKey) : null;
  const stoppedAt = Number(previous?.stoppedAt || itemOptions.working_stopped_ts || 0);
  const ackKey = item.state === 'cooldown'
    ? agentWindowActivityAcknowledgementKey(agent.kind, 'cooldown', itemOptions, stoppedAt)
    : agentWindowActivityAcknowledgementKey(agent.kind, agentWindowStateKey(agent.state), itemOptions, itemOptions.attention_signature || itemOptions.screen_text || agentWindowStateKey(agent.state));
  if (ackKey) return {ackKey, transitionKey, stoppedAt, state: item.state};
  return transitionKey && stoppedAt > 0 ? {transitionKey, stoppedAt, state: item.state} : null;
}

function acknowledgeAgentWindowActivity(session, windowIndex = null, options = {}) {
  const target = agentWindowActivityAcknowledgementTarget(session, windowIndex, options);
  if (!target) return false;
  const delayMs = Math.max(0, Number(options.delayMs) || 0);
  if (target.ackKey && typeof acknowledgeAttentionKeys === 'function') {
    return acknowledgeAttentionKeys([target.ackKey], options);
  }
  if (delayMs > 0) {
    setTimeout(() => {
      acknowledgeAgentWindowStoppedTransition(target.transitionKey, target.stoppedAt, {...options, delayMs: 0});
    }, delayMs);
    return true;
  }
  return acknowledgeAgentWindowStoppedTransition(target.transitionKey, target.stoppedAt, options);
}

function agentWindowActivityIcon(agentKey, state, idleSeconds, options = {}) {
  const kind = agentWindowKind(agentKey);
  if (!kind) return null;
  const nowSeconds = Number.isFinite(Number(options.nowSeconds)) ? Number(options.nowSeconds) : Date.now() / 1000;
  const cooldownSeconds = Math.max(0, Number(agentWindowCooldownSeconds) || 0);
  const transitionKey = agentWindowActivityTransitionKey(kind, options);
  const previous = transitionKey ? (agentWindowActivityStates.get(transitionKey) || {}) : {};
  const stateKey = agentWindowStateKey(state);
  const current = options.current === true || options.window_active === true;
  if (agentWindowIsAttentionState(stateKey)) {
    const ackKey = agentWindowActivityAcknowledgementKey(kind, stateKey, options, options.attention_signature || options.screen_text || stateKey);
    if (agentWindowActivityAcknowledgementKeyIsRecorded(ackKey, {...options, cooldown_acknowledged: false})) return null;
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowActivityStates.set(transitionKey, {state: stateKey, seenWorking: previous.seenWorking === true, stoppedAt: Number(previous.stoppedAt) || 0});
    }
    return {state: 'attention', icon: '●', label: `${agentLabel(kind)} ${t('state.needs-input')}`};
  }
  if (agentWindowIsWorkingState(stateKey)) {
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowAcknowledgedStops.delete(transitionKey);
      agentWindowActivityStates.set(transitionKey, {state: STATE_KEY.working, seenWorking: true, stoppedAt: 0});
    }
    return {state: STATE_KEY.working, icon: '●', label: `${agentLabel(kind)} ${t('state.working')}`};
  }
  const workingStoppedTs = Number(options.working_stopped_ts || options.workingStoppedTs || 0);
  let stoppedAt = Number.isFinite(workingStoppedTs) && workingStoppedTs > 0 ? workingStoppedTs : 0;
  const seenWorking = previous.seenWorking === true || stoppedAt > 0;
  if (!stoppedAt && previous.state === STATE_KEY.working) stoppedAt = nowSeconds;
  if (!stoppedAt && Number(previous.stoppedAt) > 0) stoppedAt = Number(previous.stoppedAt);
  if (transitionKey) agentWindowActivityStates.set(transitionKey, {state: String(state || STATE_KEY.idle), seenWorking, stoppedAt});
  if (seenWorking && stoppedAt > 0) {
    const stoppedAgeSeconds = Math.max(0, nowSeconds - stoppedAt);
    const cooldownAckKey = agentWindowActivityAcknowledgementKey(kind, 'cooldown', options, stoppedAt);
    if (agentWindowStoppedIsAcknowledged(transitionKey, stoppedAt) || agentWindowActivityAcknowledgementKeyIsRecorded(cooldownAckKey, {...options, attention_acknowledged: false})) return null;
    if (cooldownSeconds === 0 || stoppedAgeSeconds < cooldownSeconds) {
      if (cooldownSeconds > 0 && options.scheduleRefresh !== false) scheduleAgentWindowStoppedRefresh(transitionKey, (stoppedAt + cooldownSeconds) * 1000);
      return {state: 'cooldown', icon: '●', label: `${agentLabel(kind)} stopped`};
    }
    return null;
  }
  if (current) return {state: 'active', icon: '', label: `${agentLabel(kind)} active`};
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

function agentWindowStatusToneOrder(tones = []) {
  const toneSet = new Set((Array.isArray(tones) ? tones : [])
    .map(tone => agentWindowActivityTone(tone))
    .filter(tone => ['attention', 'cooldown', STATE_KEY.working].includes(tone)));
  return ['attention', 'cooldown', STATE_KEY.working].filter(tone => toneSet.has(tone));
}

function agentWindowStatusToneClass(tone) {
  return tone === STATE_KEY.working ? 'working' : String(tone || '');
}

function agentWindowStatusDotHtml(item, options = {}) {
  if (!item) return '';
  if (!['attention', 'cooldown', STATE_KEY.working].includes(item.state)) return '';
  const tone = agentWindowActivityTone(item.state);
  const statusTones = agentWindowStatusToneOrder(options.statusTones || [tone]);
  const segmented = statusTones.length > 1;
  const segmentKey = statusTones.map(agentWindowStatusToneClass).join('-');
  const classes = statusIndicatorDotClasses(
    tone,
    'agent-window-activity-icon',
    'agent-window-status-dot',
    `agent-window-activity-icon--${item.state}`,
    segmented ? 'agent-window-status-dot--segmented' : '',
    segmented ? `agent-window-status-dot--${segmentKey}` : '',
    segmented ? `agent-window-status-dot--segments-${statusTones.length}` : '',
    segmented ? statusTones.map(itemTone => `agent-window-status-dot--tone-${agentWindowStatusToneClass(itemTone)}`) : '',
  );
  return `<span class="${esc(classes)}" aria-hidden="true">${esc(item.icon)}</span>`;
}

function agentWindowActivityIconHtml(agentKey, state, idleSeconds, options = {}) {
  const kind = agentWindowKind(agentKey);
  if (!kind) return '';
  const item = options.item || agentWindowActivityIcon(kind, state, idleSeconds, options);
  const stateKey = item?.state || 'idle-recent';
  const label = options.label || item?.label || agentLabel(kind);
  const statusOnly = options.statusOnly === true || options.hideAgentIcon === true;
  const agentClasses = [
    'agent-window-activity-icon',
    'agent-window-agent-icon',
    `agent-window-activity-icon--${stateKey}`,
    `agent-window-agent-icon--${stateKey}`,
    item?.state === 'active' ? 'heartbeat-pulse' : '',
  ].filter(Boolean).join(' ');
  const markerHtml = agentWindowStatusDotHtml(item, {statusTones: options.statusTones});
  if (statusOnly && !markerHtml) return '';
  const tone = item?.state ? agentWindowActivityTone(item.state) : '';
  const style = [STATE_KEY.working, 'active', 'attention', 'cooldown'].includes(tone) ? statusIndicatorToneStyle(tone) : '';
  const wrapperClasses = [
    'agent-window-activity',
    `agent-window-activity--${stateKey}`,
    statusOnly ? 'agent-window-activity--status-only' : '',
  ].filter(Boolean).join(' ');
  const agentHtml = statusOnly ? '' : agentIcon(kind, {label, className: agentClasses});
  return `<span class="${esc(wrapperClasses)}" title="${esc(label)}" aria-label="${esc(label)}"${style}>${agentHtml}${markerHtml}</span>`;
}

function agentWindowActivityIconHtmlForStatus(agent, agentKey = agent?.kind, session = '', extraOptions = {}) {
  const options = agentWindowActivityOptionsForStatus(agent, session);
  return agentWindowActivityIconHtml(agentKey, agent?.state, agentWindowIdleSeconds(agent), {
    ...options,
    ...extraOptions,
    item: agentWindowActivityIconForStatusItem(agent, agentKey, session, options),
  });
}

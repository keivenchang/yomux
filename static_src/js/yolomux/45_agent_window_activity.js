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

const AGENT_WINDOW_ATTENTION_STATES = new Set(['approval', 'needs-approval', 'needs-input', 'interrupted']);

function agentWindowStateKey(state) {
  return String(state || '').trim();
}

function agentWindowIsWorkingState(state) {
  return agentWindowStateKey(state) === 'working';
}

function agentWindowIsAttentionState(state) {
  return AGENT_WINDOW_ATTENTION_STATES.has(agentWindowStateKey(state));
}

function agentWindowActivityTone(state) {
  const key = agentWindowStateKey(state);
  if (key === 'working') return 'working';
  if (key === 'cooldown') return 'cooldown';
  if (key === 'attention') return 'attention';
  if (key === 'active') return 'active';
  if (key === 'settled') return 'settled';
  return 'idle';
}

function agentWindowStateRank(state) {
  return {working: 0, approval: 1, 'needs-input': 2, idle: 3}[agentWindowStateKey(state)] ?? 9;
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
  if (key === 'working') return 1;
  if (key === 'cooldown') return 2;
  if (key === 'active') return 3;
  if (key === 'idle') return 4;
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
      state: 'idle',
      pane_target: agent?.pane_target || '',
      window_label: agent?.window_label || '',
      transcript: agent?.transcript || '',
      transcript_id: agent?.transcript_id || agent?.session_id || agent?.agent_session_id || '',
      agent_session_id: agent?.agent_session_id || agent?.session_id || '',
      last_active_ts: 0,
      idle_since: null,
    }));
  return fallback
    .map(agent => mergeAgentWindowPayload(agent, [activityRows, infoRows, stateRows]))
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
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowActivityStates.set(transitionKey, {state: stateKey, seenWorking: previous.seenWorking === true, stoppedAt: Number(previous.stoppedAt) || 0});
    }
    return {state: 'attention', icon: '●', label: `${agentLabel(kind)} ${t('state.needs-input')}`};
  }
  if (agentWindowIsWorkingState(stateKey)) {
    if (transitionKey) {
      clearAgentWindowStoppedRefresh(transitionKey);
      agentWindowActivityStates.set(transitionKey, {state: 'working', seenWorking: true, stoppedAt: 0});
    }
    return {state: 'working', icon: '●', label: `${agentLabel(kind)} ${t('state.working')}`};
  }
  const workingStoppedTs = Number(options.working_stopped_ts || options.workingStoppedTs || 0);
  let stoppedAt = Number.isFinite(workingStoppedTs) && workingStoppedTs > 0 ? workingStoppedTs : 0;
  const seenWorking = previous.seenWorking === true || stoppedAt > 0;
  if (!stoppedAt && previous.state === 'working') stoppedAt = nowSeconds;
  if (!stoppedAt && Number(previous.stoppedAt) > 0) stoppedAt = Number(previous.stoppedAt);
  if (transitionKey) agentWindowActivityStates.set(transitionKey, {state: String(state || 'idle'), seenWorking, stoppedAt});
  if (seenWorking && stoppedAt > 0) {
    const stoppedAgeSeconds = Math.max(0, nowSeconds - stoppedAt);
    if (cooldownSeconds > 0 && stoppedAgeSeconds < cooldownSeconds) {
      if (options.scheduleRefresh !== false) scheduleAgentWindowStoppedRefresh(transitionKey, (stoppedAt + cooldownSeconds) * 1000);
      return {state: 'cooldown', icon: '●', label: `${agentLabel(kind)} stopped`};
    }
    return null;
  }
  if (current) return {state: 'active', icon: '', label: `${agentLabel(kind)} active`};
  return null;
}

function agentWindowStatusDotHtml(item) {
  if (!item || item.state === 'working') return '';
  if (!['attention', 'cooldown'].includes(item.state)) return '';
  const tone = agentWindowActivityTone(item.state);
  const classes = statusIndicatorDotClasses(
    tone,
    'agent-window-activity-icon',
    'agent-window-status-dot',
    `agent-window-activity-icon--${item.state}`,
  );
  return `<span class="${esc(classes)}" aria-hidden="true">${esc(item.icon)}</span>`;
}

function agentWindowActivityIconHtml(agentKey, state, idleSeconds, options = {}) {
  const kind = agentWindowKind(agentKey);
  if (!kind) return '';
  const item = agentWindowActivityIcon(kind, state, idleSeconds, options);
  const stateKey = item?.state || 'idle-recent';
  const label = item?.label || agentLabel(kind);
  const agentClasses = [
    'agent-window-activity-icon',
    'agent-window-agent-icon',
    `agent-window-activity-icon--${stateKey}`,
    `agent-window-agent-icon--${stateKey}`,
    item?.state === 'working' || item?.state === 'active' || item?.state === 'attention' || item?.state === 'cooldown' ? 'heartbeat-pulse' : '',
  ].filter(Boolean).join(' ');
  const markerHtml = agentWindowStatusDotHtml(item);
  const tone = item?.state ? agentWindowActivityTone(item.state) : '';
  const style = tone === 'working' || tone === 'active'
    ? statusIndicatorToneStyle(tone)
    : ['attention', 'cooldown'].includes(tone)
      ? ` style="${agentAlternateAnimationStyle()}"`
      : '';
  return `<span class="agent-window-activity agent-window-activity--${esc(stateKey)}" title="${esc(label)}" aria-label="${esc(label)}"${style}>${agentIcon(kind, {label, className: agentClasses})}${markerHtml}</span>`;
}

function agentWindowActivityIconHtmlForStatus(agent, agentKey = agent?.kind, session = '') {
  return agentWindowActivityIconHtml(agentKey, agent?.state, agentWindowIdleSeconds(agent), {
    session,
    window: agent?.window,
    window_index: agent?.window_index,
    pane: agent?.pane,
    pane_target: agent?.pane_target,
    current: agentWindowPayloadCurrent(agent) === true,
    window_active: agent?.window_active === true,
    working_stopped_ts: agent?.working_stopped_ts,
  });
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared browser client-event stream, demand, repair, and lifecycle ownership.

function clientEventEnvelope(event) {
  const parsed = safeJsonParse(event?.data, {});
  return parsed && typeof parsed === 'object' ? parsed : {};
}

function clientEventPayloadFromEnvelope(envelope) {
  return envelope && typeof envelope === 'object' && envelope.payload && typeof envelope.payload === 'object'
    ? envelope.payload
    : envelope;
}

function applyClientEventReadyEnvelope(envelope = {}) {
  const epoch = String(envelope.epoch || '');
  if (!epoch) return false;
  adoptServerEpoch(epoch);
  // `ready` is a reconnect fence, not state.  Seeding accepted revisions here
  // would discard queued frames without proving the corresponding panel read
  // happened.  Channel-scoped repair below establishes readable state first.
  return true;
}

function repairClientEventReadyChannels(channels) {
  if (channels.has('files')) {
    if (typeof retryNetworkFailedFileExplorerExpansion === 'function') void retryNetworkFailedFileExplorerExpansion();
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots({immediate: true, force: true});
  }
  if (channels.has('status') || channels.has('attention')) refreshAutoStatuses({force: true}).catch(error => console.warn('client-events ready auto-status refresh failed', error));
  if (channels.has('core')) refreshBackgroundOwnerStatus({preferFresh: true}).catch(error => console.warn('client-events ready background-owner refresh failed', error));
  if (channels.has('chat') && typeof loadChatBootstrap === 'function') loadChatBootstrap({incoming: true});
  if (channels.has('transcripts') && typeof refreshTranscripts === 'function') refreshTranscripts({refreshAuto: false, refreshActivity: false}).catch(error => console.warn('client-events ready transcript refresh failed', error));
  if (channels.has('activity') && typeof refreshActivitySummary === 'function') refreshActivitySummary({force: true}).catch(error => console.warn('client-events ready activity refresh failed', error));
  if (channels.has('events') && typeof refreshOpenEventLogs === 'function') refreshOpenEventLogs().catch(error => console.warn('client-events ready event-log refresh failed', error));
  if (channels.has('yoagent') && typeof loadYoagentConversation === 'function') loadYoagentConversation({force: true, render: yoagentPanelIsActive(), scrollBottom: false}).catch(error => console.warn('client-events ready YO!agent refresh failed', error));
}

function repairReadyEventLogRevisions(resources, envelope) {
  const eventLogResources = resources.filter(resource => /^event_log_changed/.test(resource));
  if (!eventLogResources.length || typeof refreshOpenEventLogs !== 'function') return;
  const epoch = String(envelope.epoch || '');
  refreshOpenEventLogs().then(() => {
    if (clientEventTransportState.resourceEpoch !== epoch) return;
    for (const resource of eventLogResources) {
      const revision = Number(envelope.resource_revisions?.[resource]);
      if (Number.isFinite(revision)) clientEventTransportState.resourceRevisions.set(resource, revision);
    }
  }).catch(error => console.warn('client-events ready event-log refresh failed', error));
}

function clientEventRepairChannels(resources = []) {
  const channels = new Set();
  for (const rawResource of resources) {
    const resource = String(rawResource || '');
    if (/^(?:files_changed|fs_changed|roots_changed|session_files_ready)/.test(resource)) channels.add('files');
    else if (/^(?:auto_approve_changed|attention_acks_changed|tmux_signals_changed)/.test(resource)) channels.add('status');
    else if (/^event_log_changed/.test(resource)) channels.add('events');
    else if (/^(?:transcripts_changed|context_changed|context_items_ready)/.test(resource)) channels.add('transcripts');
    else if (activitySummaryEnabled && /^activity_summary_ready|background:tabber-activity/.test(resource)) channels.add('activity');
    else if (/^yoagent_/.test(resource)) channels.add('yoagent');
    else if (/^chat_/.test(resource)) channels.add('chat');
    else channels.add('core');
  }
  return channels;
}

async function refreshTmuxSignalsSnapshot() {
  const payload = await apiFetchJson(tmuxWindowSignalReadbackUrl(''));
  applyTmuxSignalsPayload({data: payload});
  return payload;
}

function clientEventPatchResourceRevision(resource, envelope = {}) {
  const revisions = envelope.resource_revisions;
  const readyRevision = revisions && typeof revisions === 'object' ? Number(revisions[resource]) : 0;
  if (Number.isSafeInteger(readyRevision) && readyRevision > 0) return readyRevision;
  if (String(envelope.resource || '') !== resource) return 0;
  const eventRevision = Number(envelope.resource_revision);
  return Number.isSafeInteger(eventRevision) && eventRevision > 0 ? eventRevision : 0;
}

function repairClientEventPatchResource(resource, envelope = {}) {
  if (!['auto_approve_changed', 'tmux_signals_changed'].includes(resource)) return false;
  const epoch = String(envelope.epoch || clientEventTransportState.resourceEpoch || '');
  const targetRevision = clientEventPatchResourceRevision(resource, envelope);
  if (!epoch || targetRevision < 1) return false;
  let record = clientEventTransportState.resourceRepairs.get(resource);
  if (!record || record.epoch !== epoch) {
    record = {epoch, targetRevision: 0, promise: null};
    clientEventTransportState.resourceRepairs.set(resource, record);
  }
  record.targetRevision = Math.max(record.targetRevision, targetRevision);
  if (record.promise) return true;
  record.promise = (async () => {
    while (clientEventTransportState.resourceEpoch === epoch) {
      const repairingRevision = record.targetRevision;
      if (resource === 'auto_approve_changed') await refreshAutoStatuses({force: true});
      else await refreshTmuxSignalsSnapshot();
      if (clientEventTransportState.resourceEpoch !== epoch) return;
      if (record.targetRevision !== repairingRevision) continue;
      clientEventTransportState.resourceRevisions.set(resource, repairingRevision);
      return;
    }
  })().catch(error => console.warn(`client-events ${resource} repair failed`, error)).finally(() => {
    if (clientEventTransportState.resourceRepairs.get(resource) === record) clientEventTransportState.resourceRepairs.delete(resource);
  });
  return true;
}

function repairClientEventResources(resources = [], envelope = {}) {
  const genericResources = [];
  for (const rawResource of resources || []) {
    const resource = String(rawResource || '');
    if (!repairClientEventPatchResource(resource, envelope)) genericResources.push(resource);
  }
  const channels = clientEventRepairChannels(genericResources);
  if (channels.size) repairClientEventReadyChannels(channels);
}

function clientEventReadyGapResources(envelope = {}) {
  const revisions = envelope.resource_revisions;
  if (!revisions || typeof revisions !== 'object') return [];
  const gaps = [];
  for (const [resource, rawRevision] of Object.entries(revisions)) {
    const revision = Number(rawRevision);
    if (!Number.isSafeInteger(revision) || revision < 1) continue;
    if (revision > (clientEventTransportState.resourceRevisions.get(resource) || 0)) gaps.push(resource);
  }
  return gaps;
}

function clientEventEnvelopeIsCurrent(envelope = {}, payload = {}) {
  const epoch = String(envelope.epoch || '');
  const resource = String(envelope.resource || '');
  const revision = Number(envelope.resource_revision);
  // Older servers and direct unit-test calls remain valid until every dev server has re-execed.
  if (!epoch || !resource || !Number.isSafeInteger(revision) || revision < 1) return true;
  adoptServerEpoch(epoch);
  const previous = clientEventTransportState.resourceRevisions.get(resource) || 0;
  if (revision <= previous) return false;
  const baseRevision = Number(envelope.base_resource_revision);
  if (payload?.patch === true && (!Number.isSafeInteger(baseRevision) || baseRevision !== previous || revision !== baseRevision + 1)) {
    repairClientEventResources([resource], envelope);
    return false;
  }
  clientEventTransportState.resourceRevisions.set(resource, revision);
  return true;
}

function recordSseDebugEvent(eventType, envelope = {}, rawEvent = null) {
  const payload = clientEventPayloadFromEnvelope(envelope);
  const rawData = rawEvent?.data || '';
  const dataBytes = utf8ByteLength(rawData);
  const dataLines = String(rawData || '').split(/\r?\n/);
  const frameBytes = utf8ByteLength(`event: ${eventType}\n`)
    + dataLines.reduce((total, line) => total + utf8ByteLength(`data: ${line}\n`), 0)
    + 1;
  const serverTimeMs = Number(envelope?.time) * 1000;
  const receiveLatencyMs = Number.isFinite(serverTimeMs)
    ? Math.max(0, Number((Date.now() - serverTimeMs).toFixed(1)))
    : undefined;
  const diagnosticFailure = envelope?.diagnosticFailure === true;
  recordJsDebugEvent('sse', {
    eventType,
    serverEventId: Number(envelope?.id || 0) || undefined,
    trigger: payload?.trigger || '',
    cache: payload?.cache || '',
    computeMs: Number.isFinite(Number(payload?.compute_ms)) ? Number(payload.compute_ms) : undefined,
    receiveLatencyMs,
    bytes: dataBytes,
    frameBytes,
    changeSummary: payload?.change_summary && typeof payload.change_summary === 'object' ? payload.change_summary : null,
    listingSummary: payload?.listing_summary && typeof payload.listing_summary === 'object' ? payload.listing_summary : null,
    phaseTimings: payload?.timings && typeof payload.timings === 'object' ? payload.timings : null,
    key: payload?.session || payload?.locale || payload?.request?.session || '',
    disconnectEpisode: Number(envelope?.disconnectEpisode || 0) || undefined,
    disconnectedMs: Number.isFinite(Number(envelope?.disconnectedMs)) ? Number(envelope.disconnectedMs) : undefined,
    source: diagnosticFailure ? '/api/client-events' : undefined,
    route: diagnosticFailure ? '/api/client-events' : undefined,
    error: diagnosticFailure ? 'client-events stream unavailable after 15s grace' : undefined,
    ok: diagnosticFailure ? false : undefined,
    deliveryOutcome: diagnosticFailure ? 'stalled' : (envelope?.recovered === true ? 'recovered' : undefined),
  });
}

function updateDryRunEnabled() {
  return typeof urlFlagEnabled === 'function' && urlFlagEnabled('updateDryRun');
}

function updateActionButton(label, onClick) {
  const button = makeButton({
    className: 'toast-action',
    label,
    onClick: event => {
      event.stopPropagation();
      onClick(event, button.closest('.toast'));
    },
  });
  return button;
}

async function triggerSelfUpdate(_event = null, ownerToast = null) {
  const dry = updateDryRunEnabled();
  const confirmed = window.confirm(dry
    ? t('update.confirmDryRun')
    : t('update.confirmInstall'));
  if (!confirmed) return;
  const target = String(ownerToast?.dataset?.updateTarget || selfUpdateAvailableTarget || '').trim();
  dismissUpdateAvailableToasts(ownerToast);
  hideUpdateBadge();
  try {
    const data = await apiFetchJson(`/api/self-update${dry ? '?dryrun=1' : ''}`, {method: 'POST'});
    const title = data.ok ? (data.restarting ? t('update.installing') : t('update.softwareTitle')) : t('update.failed');
    emitNotification('update', {title, lines: [userMessageText(data, t(data.ok ? 'state.done' : 'update.seeServerLogs'))], coalesceKey: 'self-update-result'});
    if (data.ok && data.restarting) {
      startSelfUpdateReloadPolling(data.target || data.version || target);
    }
  } catch (error) {
    emitNotification('update', {title: t('update.failed'), lines: [userMessageText(error, t('update.seeServerLogs'))], coalesceKey: 'self-update-result'});
  }
}

// Non-intrusive "a newer version exists" cue: unhide the topbar badge and show one dismissible toast
// with an "Update Now" action (admin-only; the endpoint rejects readonly).
function applyUpdateAvailable(status) {
  if (!status || !status.available) return;
  if (status.notify === false) return;
  const target = String(status.target || '').trim();
  selfUpdateAvailableTarget = target;
  const badge = document.querySelector('[data-update-badge]');
  if (badge) {
    badge.hidden = false;
    if (target) badge.dataset.updateTarget = target;
    else delete badge.dataset.updateTarget;
    renderUpdateBadgeChrome();
  }
  const node = emitNotification('update', {
    title: t('update.availableTitle'),
    lines: [t('update.availableBody', {target: status.target ? ` (${status.target})` : ''})],
    actions: [updateActionButton(t('update.now'), triggerSelfUpdate)],
    countdownMs: 4 * 60 * 60 * 1000,  // keep the update cue up for 4 hours, not the default ~10s
    className: 'attention-alert toast toast-update',  // solid (opaque) background, not the translucent default
    coalesceKey: 'update-available',
  }).inApp;
  if (node && target) node.dataset.updateTarget = target;
}

async function checkForUpdateOnce() {
  try {
    const status = await apiFetchJson(`/api/update-status${updateDryRunEnabled() ? '?dryrun=1' : ''}`);
    if (status && status.available) applyUpdateAvailable(status);
  } catch (_error) { /* offline / transient — the hourly push will retry */ }
}

function yoagentJobNotificationTitle(notification = {}) {
  return structuredMessageText(notification, 'title', t('brand.tab.agent'));
}

function yoagentJobNotificationBody(notification = {}) {
  return structuredMessageText(notification, 'body', '').trim();
}

function maybeNotifyYoagentJob(notification = {}) {
  const title = yoagentJobNotificationTitle(notification);
  const body = yoagentJobNotificationBody(notification);
  if (!body || !notificationDeliveryEnabled()) return;
  const session = String(notification.session || '').trim();
  const tag = `yoagent-job:${session || 'global'}:${body}`;
  try {
    emitNotification('yoagentJob', {
      session, title, body, systemTitle: hostNotificationTitle(title),
      systemTag: tag, renotify: true, coalesceKey: tag,
    });
  } catch (error) {
    postEvent(session || null, 'yoagent_job_notification_error', `notification failed: ${error}`, {});
  }
}

function tmuxSignalsPayloadWithWindowOverrides(data) {
  if (!data || typeof data !== 'object' || !Array.isArray(data.windows)) return data;
  const overrides = new Map();
  for (const [session, override] of tmuxWindowActiveIndexOverrideEntries()) {
    if (override === tmuxWindowPendingActiveIndex) continue;
    const indexKey = tmuxWindowIndexKey(override);
    if (indexKey !== null) overrides.set(String(session), indexKey);
  }
  if (typeof tmuxWindowDirectTargetGuardEntries === 'function') {
    for (const [session, guard] of tmuxWindowDirectTargetGuardEntries()) {
      if (overrides.has(session)) continue;
      const guardIndex = tmuxWindowIndexKey(guard?.index);
      if (guardIndex !== null) overrides.set(String(session), guardIndex);
    }
  }
  if (!overrides.size) return data;
  let changed = false;
  const windows = data.windows.map(windowRecord => {
    const session = tmuxSignalWindowSession(windowRecord);
    const override = overrides.get(session);
    if (override === undefined) return windowRecord;
    const active = override === tmuxWindowIndexKey(windowRecord?.window_index);
    if (windowRecord?.active === active) return windowRecord;
    changed = true;
    return {...windowRecord, active};
  });
  return changed ? {...data, windows} : data;
}

function tmuxSignalsPayloadWithPatch(data) {
  if (!data || typeof data !== 'object' || data.patch !== true) return data;
  return applyClientEventKeyedPatch(tmuxSignalState, data, tmuxSignalWindowKey);
}

function recordTmuxSignalRemovedWindowLatencies(data) {
  if (!data || typeof data !== 'object') return;
  const removedWindowEventAt = Number(data.removed_window_event_at);
  const removedWindowEventType = String(data.removed_window_event_type || '');
  for (const key of data.removed_window_keys || []) {
    const windowKey = String(key || '');
    if (!windowKey) continue;
    completeTerminalRemovalLatencyFromEpochSeconds('window', windowKey, removedWindowEventAt, {
      origin: removedWindowEventType || 'tmux-signal',
      eventType: removedWindowEventType,
      reason: data.patch === true ? 'tmux-signal-patch' : 'tmux-signal-snapshot',
    });
  }
}

function applyTmuxSignalsPayload(payload = {}) {
  const rawData = tmuxSignalsPayloadWithPatch(tmuxSignalPayloadData(payload));
  const data = tmuxSignalsPayloadWithWindowOverrides(rawData);
  if (!data || typeof data !== 'object') return null;
  recordTmuxSignalRemovedWindowLatencies(data);
  tmuxSignalState = data;
  applyTmuxSignalActiveWindowsToTranscriptInfo(data);
  confirmTmuxWindowActiveOverridesFromRawSignals(rawData);
  reconcileTmuxWindowDirectTargetGuardsFromRawSignals(rawData);
  return data;
}

function clientPushEventSessionKey(payload = {}) {
  return String(payload.session || payload.request?.session || payload.data?.session || payload.data?.target || '');
}

// Keep EventSource registration and the browser dispatch owner on one typed contract. The server
// validates this authoritative set in ClientEventBroker; local browser notifications stay separate
// so they cannot be mistaken for an EventSource type with no server producer.
const clientServerPushEventTypes = Object.freeze([
  'settings_changed', 'pricing_catalog_changed', 'stats_sample', 'attention_acks_changed', 'auto_approve_changed',
  'backend_health_changed',
  'background_owner_changed', 'background_refresh_done', 'background_refresh_requested', 'tmux_signals_changed',
  'watched_prs_changed', 'files_changed', 'fs_changed', 'roots_changed', 'search_progress', 'session_files_ready', 'transcripts_changed',
  'operation_terminal',
  'context_changed', 'context_items_ready', 'activity_summary_ready', 'event_log_changed', 'update_available',
  'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta',
  'chat_messages_changed', 'chat_typing_changed',
]);
const clientLocalPushEventTypes = Object.freeze(['generation_ready']);
const clientPushEventTypes = Object.freeze([...clientServerPushEventTypes, ...clientLocalPushEventTypes]);

function clientPushEventCoalesceKey(type, payload = {}) {
  const key = String(type || 'event');
  if (type === 'operation_terminal') {
    const operationId = String(payload?.operation?.id || '');
    if (operationId) return `${key}:${operationId}`;
  }
  const session = clientPushEventSessionKey(payload);
  if (session) return `${key}:${session}`;
  return key;
}

function queueClientPushEvent(type, payload = {}, envelope = {}) {
  const key = clientPushEventCoalesceKey(type, payload);
  clientEventTransportState.queue.set(key, {type, payload, envelope});
  // Chrome pauses requestAnimationFrame in background tabs. Status events still have to update
  // notification state there, otherwise a complete green->red/yellow transition can be missed
  // before the user returns to YOLOmux.
  if (document.visibilityState === 'hidden') {
    if (clientEventTransportState.frame) currentClientEventTransportLifecycleScope().release('dispatch-frame', clientEventTransportState.frame);
    currentClientEventTransportLifecycleScope().release('dispatch-generation');
    clientEventTransportState.frame = 0;
    flushQueuedClientPushEvents();
    return;
  }
  if (clientEventTransportState.frame) return;
  const scope = currentClientEventTransportLifecycleScope();
  const generation = {};
  scope.replace('dispatch-generation', generation, () => {});
  let frame = 0;
  frame = requestAnimationFrame(() => {
    if (!scope.current() || scope.value('dispatch-generation') !== generation) return;
    scope.relinquish('dispatch-generation', generation);
    if (frame) scope.relinquish('dispatch-frame', frame);
    clientEventTransportState.frame = 0;
    flushQueuedClientPushEvents();
  });
  if (scope.value('dispatch-generation') !== generation) return;
  clientEventTransportState.frame = frame;
  scope.replace('dispatch-frame', frame, cancelAnimationFrame);
}

function flushQueuedClientPushEvents() {
  const events = Array.from(clientEventTransportState.queue.values());
  clientEventTransportState.queue.clear();
  recordClientPerfCounter('sseEvent', 0, {nodes: events.length});
  for (const event of events) handleClientPushEventNow(event.type, event.payload, event.envelope);
}

// A pushed metadata payload carries its own identity because the HTTP path has no envelope to read
// one from. The two must agree: an inner identity from a different process than the envelope that
// delivered it is malformed, and applying it would stamp one server's generation under another's
// epoch. Fail closed on the identity without going blind -- the inline bytes are dropped and the
// handler falls back to an HTTP read, which carries an identity of its own.
function clientPushEventPayloadWithVerifiedIdentity(type, payload = {}, envelope = {}) {
  if (type !== 'transcripts_changed' || !payload?.data) return payload;
  const envelopeEpoch = String(envelope?.epoch || '');
  const identity = sessionMetadataPayloadIdentity(payload.data);
  if (identity && (!envelopeEpoch || identity.epoch === envelopeEpoch)) return payload;
  const {data, ...rest} = payload;
  return rest;
}

function handleClientPushEvent(type, payload = {}, envelope = {}) {
  const repairResources = Array.isArray(envelope.repair_resources) ? envelope.repair_resources.map(resource => String(resource || '')) : [];
  repairClientEventResources(repairResources, envelope);
  if (payload?.patch === true && repairResources.includes(String(envelope.resource || ''))) return false;
  if (!clientEventEnvelopeIsCurrent(envelope, payload)) return false;
  queueClientPushEvent(type, clientPushEventPayloadWithVerifiedIdentity(type, payload, envelope), envelope);
  return true;
}

function handleClientPushEventNowByType(type, payload = {}) {
  if (type === 'operation_terminal') {
    applyApiOperationTerminal(payload);
    return;
  }
  if (type === 'generation_ready') {
    window.dispatchEvent(new CustomEvent('yolomux:generation-ready', {detail: payload}));
    return;
  }
  if (type === 'update_available') {
    applyUpdateAvailable(payload && payload.available !== undefined ? payload : (payload.data || {}));
    return;
  }
  if (type === 'settings_changed') {
    if (payload.data && typeof payload.data === 'object') {
      applySettingsPayload(payload.data, {force: true});
    }
    return;
  }
  if (type === 'pricing_catalog_changed') {
    if (typeof refreshDebugCostPricingStatus === 'function') refreshDebugCostPricingStatus().catch(error => console.warn('pricing catalog refresh failed', error));
    return;
  }
  if (type === 'stats_sample') {
    if (typeof applyJsDebugStatsSamplePush === 'function') applyJsDebugStatsSamplePush(payload);
    return;
  }
  if (type === 'auto_approve_changed') {
    if (payload.refresh) {
      refreshAutoStatuses().catch(() => {});
      return;
    }
    if (payload.data) applyAutoApprovePayload(payload.data);
    else if (payload.patch === true) applyAutoApprovePayload(payload);
    return;
  }
  if (type === 'attention_acks_changed') {
    applyAttentionAcknowledgementResponse(payload);
    return;
  }
  if (type === 'backend_health_changed') {
    // Push-only, deliberately: this is what makes a dead backend service visible in the topbar with
    // no diagnostics panel open. Do NOT add a /api/system-status refetch here.
    applyBackendHealthPayload(payload);
    return;
  }
  if (type === 'background_owner_changed') {
    if (!applyBackgroundOwnerStatusPayload(payload)) {
      refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('background-owner status refresh failed', error));
    } else if (typeof refreshAllIndexedDirsStatus === 'function') {
      // A new owner may have rebuilt or invalidated an index while this client was
      // following the previous owner. Revalidate only surfaces that are currently demanded.
      refreshAllIndexedDirsStatus();
    }
    return;
  }
  if (type === 'background_refresh_requested') {
    refreshBackgroundOwnerStatus({preferFresh: true}).catch(error => console.warn('background refresh request status failed', error));
    return;
  }
  if (type === 'background_refresh_done') {
    if (payload.role === 'search-index') {
      const applied = payload.root && typeof applyFileIndexStatusPayload === 'function'
        ? applyFileIndexStatusPayload(payload.root, payload)
        : false;
      // A completion event contains the authoritative lifecycle snapshot.  Re-reading it
      // immediately recreates the retired building-index poll and can race a newer generation.
      if (payload.root && !applied) refreshFileIndexStatus(payload.root);
      // A completed search-index refresh is authoritative: re-issue the open palette query through the
      // one re-query owner, forcing past any in-flight search so the new snapshot's rows win.
      requeryOpenFileQuickOpenForIndexChange({force: true});
    }
    if (payload.role === 'session-files') {
      const session = String(payload.session || '');
      if (!session || session === fileExplorerSessionFilesTargetSession()) {
        fetchSessionFiles({silent: true}).catch(error => console.warn('session-files refresh failed', error));
      }
    }
    if (payload.role === 'tabber-activity' && typeof itemIsActivePaneTab === 'function' && itemIsActivePaneTab(tabberItemId) && document.visibilityState !== 'hidden') {
      fetchTabberActivity().catch(error => console.warn('Tabber activity refresh failed', error));
    }
    return;
  }
  if (type === 'tmux_signals_changed') {
    applyTmuxSignalsPayload(payload);
    if (typeof updatePanelWindowStepButtons === 'function' && typeof activePaneItems === 'function') {
      for (const session of activePaneItems()) {
        if (typeof isTmuxSession === 'function' && !isTmuxSession(session)) continue;
        updatePanelWindowStepButtons(session, transcriptMetadataState.payload.sessions?.[session]);
      }
    }
    return;
  }
  if (type === 'watched_prs_changed') {
    if (payload.data) applyWatchedPrsPayload(payload.data);
    return;
  }
  if (type === 'transcripts_changed') {
    if (payload.data) {
      applyTranscriptsPayload(payload.data, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    } else {
      refreshTranscripts({refreshAuto: false, refreshActivity: false}).catch(error => console.warn('client-events transcript refresh failed', error));
    }
    return;
  }
  if (type === 'context_changed') {
    if (typeof refreshTranscripts === 'function') refreshTranscripts({refreshAuto: false, refreshActivity: false}).catch(error => console.warn('client-events context refresh failed', error));
    return;
  }
  if (type === 'context_items_ready') {
    if (payload.data) applyContextItemsPayloadFromPush(payload.data, {session: payload.session, preserveScroll: true});
    return;
  }
  if (type === 'activity_summary_ready') {
    if (payload.data) applyActivitySummaryPayloadFromPush(payload.data);
    return;
  }
  if (type === 'event_log_changed') {
    refreshEventLogsFromPush(payload);
    return;
  }
  if (type === 'yoagent_conversation_changed') {
    loadYoagentConversation({force: true, render: yoagentPanelIsActive(), scrollBottom: 'auto'}).catch(error => console.warn('YO!agent conversation refresh failed', error));
    return;
  }
  if (type === 'yoagent_stream_delta') {
    if (typeof applyYoagentStreamPayload === 'function' && applyYoagentStreamPayload(payload)) {
      renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto'});
    }
    return;
  }
  if (type === 'yoagent_jobs_changed') {
    if (typeof loadYoagentJobs === 'function') {
      loadYoagentJobs({force: true, silent: true, render: yoagentPanelIsActive(), scrollBottom: false}).catch(error => console.warn('YO!agent jobs refresh failed', error));
    }
    maybeNotifyYoagentJob(payload.notification || {});
    return;
  }
  if (type === 'yoagent_skills_changed') {
    refreshActivitySummary({force: true, render: yoagentPanelIsActive()}).catch(error => console.warn('YO!agent skills refresh failed', error));
    return;
  }
  if (type === 'chat_messages_changed' || type === 'chat_typing_changed') {
    if (typeof handleChatInvalidation === 'function') {
      handleChatInvalidation(type, payload);
    }
    return;
  }
  if (type === 'session_files_ready') {
    if (payload.data && typeof applySessionFilesPayloadFromPush === 'function') {
      applySessionFilesPayloadFromPush(payload.data, payload.request || {});
    }
    return;
  }
  if (type === 'files_changed') {
    if (typeof refreshOpenFilesFromPush === 'function') {
      refreshOpenFilesFromPush(payload).catch(error => console.warn('client file push refresh failed', error));
    }
    return;
  }
  if (type === 'fs_changed') {
    if (typeof refreshFileExplorerFromPush === 'function') {
      refreshFileExplorerFromPush(payload).catch(error => console.warn('client fs push refresh failed', error));
    }
    return;
  }
  if (type === 'roots_changed') {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots({immediate: true, force: true});
    return;
  }
  if (type === 'search_progress') {
    // A per-root crawl-advance signal (path-free {scope_id, generation, revision, coverage}). The
    // open Quick Open palette streams the newly-committed matches by cursor instead of re-querying.
    if (typeof handleFileSearchProgressSignal === 'function') handleFileSearchProgressSignal(payload);
  }
}

const clientPushEventHandlers = Object.freeze(Object.fromEntries(
  clientPushEventTypes.map(type => [type, payload => handleClientPushEventNowByType(type, payload)])
));

function handleClientPushEventNow(type, payload = {}) {
  const handler = clientPushEventHandlers[type];
  if (handler) handler(payload);
}

function clientEventDemandDescriptor() {
  const visible = document.visibilityState !== 'hidden';
  const activeItems = visible && typeof activePaneItems === 'function' ? activePaneItems() : [];
  const channels = new Set();
  const operations = Array.from(apiOperationState.pending.keys()).sort();
  const notificationAttention = typeof notificationDeliveryEnabled === 'function' && notificationDeliveryEnabled('system');
  const notificationChat = typeof notificationDeliveryEnabled === 'function'
    && (notificationDeliveryEnabled('inApp') || notificationDeliveryEnabled('system'));
  if (visible) {
    channels.add('core');
    channels.add('status');
    const finderActive = activeItems.includes(fileExplorerItemId);
    const differActive = activeItems.includes(differItemId);
    const fileEditorActive = activeItems.some(item => isFileEditorItem(item));
    if (finderActive && fileExplorerMode === 'tabber') channels.add('activity');
    if ((finderActive && fileExplorerMode !== 'tabber') || differActive || fileEditorActive) channels.add('files');
    if (activeItems.includes(infoItemId)) {
      channels.add('activity');
      channels.add('transcripts');
    }
    if (activeItems.some(item => isTmuxSession(item) && panelActiveTabName(item) === 'events')) channels.add('events');
    if ((activeItems.includes(debugPaneItemId) || activeItems.includes(yocostItemId))
        && (typeof jsDebugStatsLivePushEnabled !== 'function' || jsDebugStatsLivePushEnabled())) channels.add('stats');
    if (activeItems.includes(yoagentItemId)) {
      channels.add('activity');
      channels.add('transcripts');
      channels.add('yoagent');
    }
    if (activeItems.includes(chatItemId) || notificationChat) channels.add('chat');
    if (activeItems.some(item => isTmuxSession(item) && typeof transcriptPreviewPaneIsActive === 'function' && transcriptPreviewPaneIsActive(item))) {
      channels.add('transcripts');
    }
  } else if (notificationAttention) {
    channels.add('attention');
    channels.add('chat');
  }
  if (operations.length) channels.add('core');
  return {
    visibility: visible ? 'visible' : 'hidden',
    active_panes: activeItems.slice().sort(),
    active_subtabs: {
      finder: finderActiveMode(),
      yoagent: activeItems.includes(yoagentItemId),
      chat: activeItems.includes(chatItemId),
    },
    channels: Array.from(channels).sort(),
    operations,
    notification_attention: notificationAttention,
  };
}

function finderActiveMode() {
  return itemIsActivePaneTab(fileExplorerItemId) ? normalizeFileExplorerMode(fileExplorerMode) : '';
}

const clientEventDemandItemLimit = 64;
const clientEventDemandItemTextLimit = 128;

function normalizedClientEventDemandItems(value) {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value
    .filter(item => typeof item === 'string')
    .map(item => item.trim().slice(0, clientEventDemandItemTextLimit))
    .filter(Boolean)))
    .sort()
    .slice(0, clientEventDemandItemLimit);
}

function normalizeClientEventDemandDescriptor(descriptor = {}) {
  const source = descriptor && typeof descriptor === 'object' ? descriptor : {};
  return {
    ...source,
    channels: normalizedClientEventDemandItems(source.channels),
    operations: normalizedClientEventDemandItems(source.operations),
  };
}

function clientEventDemandSignature(descriptor) {
  return JSON.stringify(normalizeClientEventDemandDescriptor(descriptor));
}

function clearClientEventDisconnectEpisode(source, options = {}) {
  const episode = clientEventTransportState.disconnectEpisode;
  if (!episode || episode.source !== source) return false;
  if (clientEventTransportState.disconnectTimer) currentClientEventTransportLifecycleScope().release('disconnect-episode', clientEventTransportState.disconnectTimer);
  clientEventTransportState.disconnectTimer = null;
  clientEventTransportState.disconnectEpisode = null;
  if (options.recovered === true && episode.reported === true) {
    recordSseDebugEvent('client_events_recovered', {
      disconnectEpisode: episode.id,
      disconnectedMs: Math.max(0, performance.now() - episode.startedAt),
      recovered: true,
    });
  }
  return true;
}

function scheduleClientEventDisconnectEpisode(source) {
  if (source !== null && clientEventTransportState.source !== source) return false;
  if (source === null && clientEventTransportState.source !== null) return false;
  const active = clientEventTransportState.disconnectEpisode;
  if (active?.source === source) return false;
  const episode = {
    id: clientEventTransportState.nextDisconnectEpisode++,
    source,
    startedAt: performance.now(),
    reported: false,
  };
  clientEventTransportState.disconnectEpisode = episode;
  const scope = currentClientEventTransportLifecycleScope();
  const timer = setTimeout(() => {
    if (!scope.current() || clientEventTransportState.disconnectTimer !== timer) return;
    scope.relinquish('disconnect-episode', timer);
    clientEventTransportState.disconnectTimer = null;
    if ((source !== null && clientEventTransportState.source !== source)
        || (source === null && clientEventTransportState.source !== null)
        || clientEventTransportState.connected
        || clientEventTransportState.disconnectEpisode !== episode) return;
    episode.reported = true;
    recordSseDebugEvent('client_events_failure', {
      disconnectEpisode: episode.id,
      disconnectedMs: Math.max(clientEventDisconnectGraceMs, performance.now() - episode.startedAt),
      diagnosticFailure: true,
    });
  }, clientEventDisconnectGraceMs);
  clientEventTransportState.disconnectTimer = timer;
  scope.ownTimer('disconnect-episode', timer);
  return true;
}

function clearClientEventCandidateEpisode(source) {
  const episode = clientEventTransportState.candidateEpisode;
  if (!episode || (source !== undefined && episode.source !== source)) return false;
  clientEventTransportState.candidateEpisode = null;
  return true;
}

// Abandon a pre-ready CANDIDATE stream and its retry episode without touching the ACTIVE stream.
function abandonClientEventCandidate(source) {
  if (clientEventTransportState.replacementSource !== source) return false;
  clientEventTransportState.replacementSource = null;
  clearClientEventCandidateEpisode(source);
  if (!currentClientEventTransportLifecycleScope().release('candidate-stream', source)) source?.close?.();
  return true;
}

function closeClientEventStream() {
  const source = clientEventTransportState.source;
  clearClientEventDisconnectEpisode(source);
  clientEventTransportState.source = null;
  const replacementSource = clientEventTransportState.replacementSource;
  clientEventTransportState.replacementSource = null;
  clearClientEventCandidateEpisode();
  clientEventTransportState.connected = false;
  if (!currentClientEventTransportLifecycleScope().release('active-stream', source)) source?.close?.();
  if (replacementSource !== source && !currentClientEventTransportLifecycleScope().release('candidate-stream', replacementSource)) replacementSource?.close?.();
}

function openClientEventStream(descriptor, options = {}) {
  descriptor = normalizeClientEventDemandDescriptor(descriptor);
  if (!descriptor.channels.length) return null;
  if (typeof EventSource === 'undefined') {
    clientEventTransportState.connected = false;
    clientEventTransportState.reconnectPending = true;
    scheduleClientEventDisconnectEpisode(null);
    return null;
  }
  const params = new URLSearchParams({
    channels: descriptor.channels.join(','),
    client_id: String(browserClientId || ''),
  });
  if (descriptor.operations.length) params.set('operations', descriptor.operations.join(','));
  let source;
  try {
    source = new EventSource(`/api/client-events?${params.toString()}`);
  } catch (_error) {
    clientEventTransportState.connected = false;
    clientEventTransportState.reconnectPending = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
    scheduleClientEventDisconnectEpisode(null);
    return null;
  }
  if (clientEventTransportState.disconnectEpisode?.source === null) {
    clientEventTransportState.disconnectEpisode.source = source;
  }
  const replacing = options.replace === true && clientEventTransportState.source !== null;
  if (replacing) {
    const priorReplacement = clientEventTransportState.replacementSource;
    clientEventTransportState.replacementSource = source;
    // One bounded retry episode per candidate: opening a new candidate (whether the first for this
    // demand or a corrected one for changed demand) starts a fresh episode.
    clientEventTransportState.candidateEpisode = {
      source,
      demandSignature: String(options.demandSignature || ''),
      attempts: 0,
      startedAt: performance.now(),
    };
    if (!currentClientEventTransportLifecycleScope().release('candidate-stream', priorReplacement)) priorReplacement?.close?.();
    currentClientEventTransportLifecycleScope().ownStream('candidate-stream', source);
  } else {
    clientEventTransportState.source = source;
    currentClientEventTransportLifecycleScope().ownStream('active-stream', source);
  }
  const channels = new Set(descriptor.channels);
  source.addEventListener('ready', event => {
    if (clientEventTransportState.replacementSource === source) {
      const demandedSignature = String(options.demandSignature || '');
      if (demandedSignature && demandedSignature !== clientEventDemandSignature(clientEventDemandDescriptor())) {
        clientEventTransportState.replacementSource = null;
        clearClientEventCandidateEpisode(source);
        source.close();
        syncClientEventDemand({immediate: true});
        return;
      }
      const previousSource = clientEventTransportState.source;
      clientEventTransportState.source = source;
      clientEventTransportState.replacementSource = null;
      // The candidate is now the ACTIVE stream; its bounded retry episode is over.
      clearClientEventCandidateEpisode(source);
      currentClientEventTransportLifecycleScope().relinquish('candidate-stream', source);
      currentClientEventTransportLifecycleScope().ownStream('active-stream', source);
    } else if (clientEventTransportState.source !== source) {
      return;
    }
    clearClientEventDisconnectEpisode(source, {recovered: true});
    const isRecoveryReady = clientEventTransportState.reconnectPending;
    clientEventTransportState.reconnectPending = false;
    clientEventTransportState.connected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    const envelope = clientEventEnvelope(event);
    const readyEpoch = String(envelope.epoch || '');
    // Older servers did not include an epoch/revision summary. Treat that compatibility frame
    // as an unknown reconnect and conservatively repair current demand rather than assuming it
    // is the same generation with zero gaps.
    const freshEpoch = !readyEpoch || clientEventTransportState.resourceEpoch !== readyEpoch;
    applyClientEventReadyEnvelope(envelope);
    recordSseDebugEvent('ready', envelope, event);
    if (isRecoveryReady && channels.has('files') && typeof drainFileExplorerFsBatchWithoutPush === 'function') {
      void drainFileExplorerFsBatchWithoutPush();
    }
    if (freshEpoch) {
      const readyResources = Object.keys(envelope.resource_revisions || {});
      repairClientEventResources(readyResources, envelope);
      const unrepairedChannels = new Set(channels);
      for (const channel of clientEventRepairChannels(readyResources)) unrepairedChannels.delete(channel);
      repairClientEventReadyChannels(unrepairedChannels);
    } else {
      const gapResources = clientEventReadyGapResources(envelope);
      repairReadyEventLogRevisions(gapResources, envelope);
      repairClientEventResources(gapResources.filter(resource => !/^event_log_changed/.test(resource)), envelope);
    }
  });
  source.addEventListener('ping', event => {
    if (clientEventTransportState.source !== source) return;
    clientEventTransportState.connected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    recordSseDebugEvent('ping', clientEventEnvelope(event), event);
  });
  source.onerror = () => {
    if (clientEventTransportState.replacementSource === source) {
      // A CANDIDATE that errors before it is ever ready must not be silently retried forever while the
      // ACTIVE stream keeps claiming to serve demand it no longer covers. Bound the retry episode: the
      // browser EventSource auto-reconnects the same URL, so tolerate a few transient errors, then
      // abandon the candidate and re-drive demand so a fresh stream + HTTP resync repair current state.
      const episode = clientEventTransportState.candidateEpisode;
      if (!episode || episode.source !== source) return;
      episode.attempts += 1;
      if (episode.attempts < clientEventCandidateRetryLimit) return;
      abandonClientEventCandidate(source);
      // The active stream does not serve the new demand: demote it so consumers fall back to HTTP
      // instead of trusting a stream that covers only the old channel set.
      clientEventTransportState.connected = false;
      clientEventTransportState.reconnectPending = true;
      if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
      recordSseDebugEvent('client_events_candidate_failed', {
        attempts: episode.attempts,
        demandSignature: episode.demandSignature,
        diagnosticFailure: true,
      });
      // Force demand to be re-driven (the signature was already advanced when this candidate opened),
      // opening one corrected candidate, and schedule an HTTP resync so no channel is left stranded.
      clientEventTransportState.demandSignature = '';
      scheduleReconnectResync('candidate-failed');
      syncClientEventDemand({immediate: true});
      return;
    }
    if (clientEventTransportState.source !== source) return;
    clientEventTransportState.connected = false;
    clientEventTransportState.reconnectPending = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
    scheduleClientEventDisconnectEpisode(source);
    if (channels.has('files') && typeof drainFileExplorerFsBatchWithoutPush === 'function') {
      void drainFileExplorerFsBatchWithoutPush();
    }
  };
  for (const type of Object.keys(clientPushEventHandlers)) {
    source.addEventListener(type, event => {
      if (clientEventTransportState.source !== source) return;
      clientEventTransportState.connected = true;
      if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
      const envelope = clientEventEnvelope(event);
      recordSseDebugEvent(type, envelope, event);
      handleClientPushEvent(type, clientEventPayloadFromEnvelope(envelope), envelope);
    });
  }
  return source;
}

function applyClientEventDemand(timer = clientEventTransportState.demandTimer, scope = currentClientEventTransportLifecycleScope()) {
  if (timer !== null && (!scope.current() || clientEventTransportState.demandTimer !== timer)) return false;
  scope.release('demand', timer);
  clientEventTransportState.demandTimer = null;
  if (!clientEventTransportState.enabled) return false;
  const descriptor = clientEventDemandDescriptor();
  const signature = clientEventDemandSignature(descriptor);
  const sameDemand = signature === clientEventTransportState.demandSignature;
  if (sameDemand && clientEventTransportState.source) return false;
  clientEventTransportState.demand = descriptor;
  clientEventTransportState.demandSignature = signature;
  if (!descriptor.channels.length) {
    if (!sameDemand) closeClientEventStream();
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
    return true;
  }
  openClientEventStream(descriptor, {replace: !sameDemand, demandSignature: signature});
  return true;
}

function syncClientEventDemand(options = {}) {
  if (!clientEventTransportState.enabled) return false;
  if (clientEventTransportState.demandTimer) currentClientEventTransportLifecycleScope().release('demand', clientEventTransportState.demandTimer);
  if (options.immediate === true) return applyClientEventDemand(null, currentClientEventTransportLifecycleScope());
  const scope = currentClientEventTransportLifecycleScope();
  const timer = setTimeout(() => applyClientEventDemand(timer, scope), clientEventDemandDebounceMs);
  clientEventTransportState.demandTimer = timer;
  scope.ownTimer('demand', timer);
  return true;
}

function installClientEventStream() {
  clientEventTransportState.enabled = true;
  return syncClientEventDemand({immediate: true});
}

function disposeClientEventTransportLifecycle(reason = 'disposed') {
  clientEventTransportLifecycleScope?.dispose(reason);
  clientEventTransportState.source = null;
  clientEventTransportState.replacementSource = null;
  clientEventTransportState.connected = false;
  clientEventTransportState.disconnectTimer = null;
  clientEventTransportState.disconnectEpisode = null;
  clientEventTransportState.candidateEpisode = null;
  clientEventTransportState.demandTimer = null;
  clientEventTransportState.frame = 0;
  clientEventTransportState.resyncTimer = null;
}

if (typeof window !== 'undefined' && window?.addEventListener) {
  window.addEventListener('pagehide', () => disposeClientEventTransportLifecycle('pagehide'));
  window.addEventListener('pageshow', event => {
    if (event?.persisted === true && clientEventTransportState.enabled) syncClientEventDemand({immediate: true});
  });
}

registerTerminalRuntimeFacade('client-events', {
  disposeClientEventTransportLifecycle,
  handleClientPushEvent,
  installClientEventStream,
  queueClientPushEvent,
  syncClientEventDemand,
});

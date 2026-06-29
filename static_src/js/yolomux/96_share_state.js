// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Share status, creation payloads, mirror sockets, and shared state snapshots.
let shareDefaultTtlSeconds = initialSetting('share.ttl_seconds', 600);
let shareDefaultMaxViewers = initialSetting('share.max_viewers', 2);
let shareDefaultReadOnly = initialSetting('share.read_only', true) !== false;
let shareDefaultScheme = initialSetting('share.scheme', 'http') === 'https' ? 'https' : 'http';
let shareViewFit = normalizeShareViewFit(storageGet(shareViewFitStorageKey) || initialSetting('share.view_fit', 'cover'));
let shareStatusPill = null;
let shareStatusTimer = null;
let shareViewerBanner = null;
let shareMirrorStage = null;
let shareReplayShellActive = false;
let shareReplayShellState = {status: 'idle'};
let shareReplayLastKeyframe = null;
let shareReplayNodeMap = new Map();
let shareReplayTerminalPlaceholders = new Map();
const shareReplayScrollPublishTimers = new Map();
let shareReplayLastKeyframeBytes = 0;
let shareReplayLastDeltaBytes = 0;
let shareReplayLastLatencyMs = null;
let shareReplayLastFrameReceivedAt = 0;
let shareReplayLastRedactionPolicyVersion = 1;
let shareStatusLastRefreshAt = 0;
let shareStatusRefreshInFlight = false;
const shareReplayHostDeltaMaxBytes = 48 * 1024;
let yolomuxFontsReadyPromise = null;

function normalizeSharePayload(payload) {
  if (!payload || payload.active === false) return null;
  return {
    active: true,
    token: String(payload.token || ''),
    url: String(payload.url || ''),
    session: String(payload.session || ''),
    expiresAt: Number(payload.expires_at ?? payload.expiresAt ?? 0),
    mode: String(payload.mode || 'ro') === 'rw' ? 'rw' : 'ro',
    scheme: String(payload.scheme || 'http') === 'https' ? 'https' : 'http',
    shortId: String(payload.short_id ?? payload.shortId ?? ''),
    maxViewers: Number(payload.max_viewers ?? payload.maxViewers ?? 0) || 0,
    viewers: Number(payload.viewers || 0) || 0,
    viewerDetails: normalizeShareViewerDetails(payload),
    createdBy: String(payload.created_by ?? payload.createdBy ?? ''),
    debugProfile: payload.debug_profile === true || payload.debugProfile === true,
    layout: String(payload.layout || ''),
    tabs: String(payload.tabs || ''),
    viewport: payload.viewport && typeof payload.viewport === 'object' ? payload.viewport : {},
    appearance: payload.appearance && typeof payload.appearance === 'object' ? payload.appearance : {},
    finder: payload.finder && typeof payload.finder === 'object' ? payload.finder : {},
    uiState: payload.uiState && typeof payload.uiState === 'object' ? payload.uiState : {},
  };
}

function normalizeShareViewerDetails(payload) {
  const raw = Array.isArray(payload?.viewer_details)
    ? payload.viewer_details
    : Array.isArray(payload?.viewerDetails)
      ? payload.viewerDetails
      : [];
  return raw.map((viewer, index) => ({
    connectedAt: Math.max(0, Number(viewer?.connected_at ?? viewer?.connectedAt ?? 0) || 0),
    connectedSeconds: Math.max(0, Number(viewer?.connected_seconds ?? viewer?.connectedSeconds ?? 0) || 0),
    ip: String(viewer?.ip || ''),
    browser: String(viewer?.browser || ''),
    id: String(viewer?.id || index),
  })).filter(viewer => viewer.connectedAt || viewer.connectedSeconds || viewer.ip || viewer.browser);
}

function normalizeShareListPayload(payload) {
  const rawShares = Array.isArray(payload?.shares) ? payload.shares : [];
  const shares = rawShares.map(normalizeSharePayload).filter(Boolean);
  const single = normalizeSharePayload(payload);
  if (single && !shares.some(share => share.token === single.token)) shares.unshift(single);
  return shares;
}

function setActiveShares(shares) {
  activeShares = (Array.isArray(shares) ? shares : []).filter(share => share?.token);
  activeShare = activeShares[0] || null;
}

function mergeShareStatusPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return;
  const shortId = String(payload.short_id ?? payload.shortId ?? '');
  const targetIndex = activeShares.findIndex(share => (
    (shortId && share.shortId === shortId)
    || (!shortId && activeShares.length === 1)
  ));
  if (targetIndex < 0) return;
  const current = activeShares[targetIndex];
  const updated = normalizeSharePayload({
    ...current,
    ...payload,
    token: current.token,
    url: current.url,
    short_id: shortId || current.shortId,
  });
  if (!updated) return;
  activeShares = activeShares.map((share, index) => index === targetIndex ? updated : share);
  activeShare = activeShares[0] || null;
  renderShareStatusPill();
  updateShareViewerBanner();
  renderShareViewerLists();
  renderShareCountdowns();
}

function shareHasActiveShare() {
  return activeShares.length > 0;
}

function shareSecondsRemaining(share, now = Date.now()) {
  const expiresAtMs = Number(share?.expiresAt || 0) * 1000;
  return Math.max(0, Math.ceil((expiresAtMs - now) / 1000));
}

function shareViewerCurrentShare() {
  if (!shareViewMode) return activeShare;
  return activeShare || normalizeSharePayload({
    active: true,
    token: shareToken,
    ...shareBootstrap,
    expires_at: shareBootstrap?.expiresAt,
    max_viewers: shareBootstrap?.maxViewers,
  });
}

function shareIsExpired(share) {
  return Number(share?.expiresAt || 0) > 0 && shareSecondsRemaining(share) <= 0;
}

function redirectExpiredShareViewerToLogin() {
  if (!shareViewMode || !shareIsExpired(shareViewerCurrentShare())) return false;
  return redirectToLoginUrl();
}

function shareTimeLeftText(share) {
  const seconds = shareSecondsRemaining(share);
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, '0');
  return `${minutes}:${remainder}`;
}

function shareViewerConnectedSeconds(viewer) {
  const connectedAt = Number(viewer?.connectedAt || 0);
  if (connectedAt > 0) return Math.max(0, Math.floor((Date.now() / 1000) - connectedAt));
  return Math.max(0, Math.floor(Number(viewer?.connectedSeconds || 0)));
}

function shareViewerDurationText(viewer) {
  const seconds = shareViewerConnectedSeconds(viewer);
  if (seconds < 60) return tPlural('duration.second', Math.max(1, seconds));
  if (seconds < 3600) return tPlural('duration.minute', Math.floor(seconds / 60));
  if (seconds < 86400) return tPlural('duration.hour', Math.floor(seconds / 3600));
  return tPlural('duration.day', Math.floor(seconds / 86400));
}

function shareMinuteUnitLabel() {
  return t('share.duration.minute', {count: 1}).replace(/\b1\b/g, '').trim() || 'min';
}

function clampShareTtlSeconds(value, fallback = shareDefaultTtlSeconds) {
  const fallbackValue = Math.max(60, Math.min(28800, Math.round(Number(fallback) || 600)));
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallbackValue;
  return Math.max(60, Math.min(28800, Math.round(numeric)));
}

function shareTtlMinutesFromSeconds(seconds) {
  return Math.max(1, Math.min(480, Math.round(clampShareTtlSeconds(seconds) / 60)));
}

function shareTtlSecondsFromForm(form) {
  const minuteText = String(form?.elements?.ttl_minutes?.value ?? '').trim();
  if (minuteText) return clampShareTtlSeconds(Number(minuteText) * 60);
  const secondText = String(form?.elements?.ttl_seconds?.value ?? '').trim();
  return secondText ? clampShareTtlSeconds(secondText) : clampShareTtlSeconds(shareDefaultTtlSeconds);
}

function shareDefaultSchemeForForm(readOnly = shareDefaultReadOnly) {
  if (!shareTlsAvailable()) return 'http';
  if (!readOnly) return 'https';
  return shareDefaultScheme === 'https' ? 'https' : 'http';
}

function shareModeLabel(share) {
  return share?.mode === 'rw' ? t('share.mode.write') : t('share.mode.readOnly');
}

function shareTotalViewers() {
  return activeShares.reduce((sum, share) => sum + (Number(share?.viewers) || 0), 0);
}

function shareSoonestTimeLeftText() {
  if (!activeShares.length) return '0:00';
  const soonest = activeShares.reduce((best, share) => shareSecondsRemaining(share) < shareSecondsRemaining(best) ? share : best, activeShares[0]);
  return shareTimeLeftText(soonest);
}

function shareLayoutSeed() {
  return {
    layout: layoutParamValue(layoutSlots),
    tabs: layoutTabsParamValue(layoutSlots),
  };
}

function shareViewportSnapshot() {
  const viewport = appViewport();
  return {width: viewport.width, height: viewport.height};
}

function shareAppearanceSnapshot() {
  return {
    locale: typeof i18nActiveLocaleId === 'function' ? i18nActiveLocaleId() : 'en',
    languagePref: initialSetting('general.language', 'system'),
    theme: normalizeGlobalThemeMode(globalThemeMode),
    resolvedTheme: resolvedGlobalThemeMode(),
    terminalTheme: normalizeTerminalThemeMode(terminalThemeMode),
    uiFontSize: numberSetting('appearance.ui_font_size', 13),
    terminalFontSize,
    terminalLineHeight: 1,
    editorFontSize,
    previewFontSize: clampEditorPreviewFontSize(editorPreviewFontSize),
    fileExplorerFontSize,
    tabWidth: numberSetting('appearance.tab_width', 180),
    paneSpacing: Math.max(0, Math.min(20, numberSetting('appearance.pane_spacing', 3))),
    paneRingOpacity: Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75))),
    inactivePaneOpacity: Math.max(0, Math.min(100, numberSetting('appearance.inactive_pane_opacity', 60))),
    activeColor: initialSetting('appearance.active_color', 'green'),
    separatorColor: initialSetting('appearance.separator_color', 'theme'),
  };
}

function normalizeShareViewFit(value) {
  return String(value || '').trim() === 'contain' ? 'contain' : 'cover';
}

function shareMirrorFitTransform(hostViewport, clientViewport, fit = shareViewFit) {
  const host = normalizeAppViewport(hostViewport);
  const client = normalizeAppViewport(clientViewport);
  const scaleX = client.width / host.width;
  const scaleY = client.height / host.height;
  const mode = normalizeShareViewFit(fit);
  const scale = mode === 'contain' ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY);
  const width = host.width * scale;
  const height = host.height * scale;
  return {
    fit: mode,
    scale,
    tx: (client.width - width) / 2,
    ty: (client.height - height) / 2,
    hostWidth: host.width,
    hostHeight: host.height,
    clientWidth: client.width,
    clientHeight: client.height,
  };
}

function ensureShareMirrorStage() {
  if (!shareViewMode) return null;
  const root = appRootElement();
  if (!root || root === document.body) return null;
  if (shareMirrorStage?.isConnected) {
    if (root.parentElement !== shareMirrorStage) shareMirrorStage.appendChild(root);
    return shareMirrorStage;
  }
  const existing = document.getElementById?.('shareMirrorStage');
  shareMirrorStage = existing || document.createElement('div');
  shareMirrorStage.id = 'shareMirrorStage';
  shareMirrorStage.className = 'share-mirror-stage';
  shareMirrorStage.setAttribute('aria-hidden', 'false');
  if (root.parentElement) root.parentElement.insertBefore(shareMirrorStage, root);
  else document.body?.prepend?.(shareMirrorStage);
  shareMirrorStage.appendChild(root);
  return shareMirrorStage;
}

function shareReadOnlyReplayModeEnabled() {
  return shareViewMode && !shareWriteMode && shareReplayFeatureEnabled();
}

function shareReplayViewerModeEnabled() {
  return shareViewMode && shareReplayFeatureEnabled();
}

function shareSemanticReadOnlyMirrorEnabled() {
  return shareViewMode && !shareWriteMode && !shareReplayViewerModeEnabled();
}

function shareSemanticMirrorApplyAllowed() {
  if (shareReplayViewerModeEnabled()) return false;
  return (!shareViewMode && shareHasActiveShare()) || shareViewMode;
}

function shareReplayShellEnabled() {
  return shareReplayViewerModeEnabled();
}

function shareReplayUserStatusText(status = '') {
  const cleanStatus = String(status || '').trim();
  if (cleanStatus === 'mirrored') return 'mirrored';
  if (cleanStatus === 'host-disconnected') return 'host disconnected';
  if (cleanStatus === 'viewer-behind') return 'viewer behind';
  return 'resyncing';
}

function setShareReplayShellStatus(status = 'waiting', detail = {}) {
  if (!shareReplayShellActive) return;
  const cleanStatus = String(status || 'waiting');
  shareReplayShellState = {
    status: cleanStatus,
    sequence: Number.isFinite(Number(detail.sequence)) ? Number(detail.sequence) : null,
    digest: String(detail.digest || ''),
    updatedAt: Date.now(),
  };
  const root = appRootElement();
  if (root?.dataset) {
    root.dataset.shareReplayStatus = cleanStatus;
    if (shareReplayShellState.digest) root.dataset.shareReplayDigest = shareReplayShellState.digest;
    else delete root.dataset.shareReplayDigest;
    if (shareReplayShellState.sequence !== null) root.dataset.shareReplaySequence = String(shareReplayShellState.sequence);
    else delete root.dataset.shareReplaySequence;
  }
  if (['viewer-behind', 'error', 'host-disconnected'].includes(cleanStatus)) {
    void shareUploadDebugProfile(`share-replay-${cleanStatus}`, shareReplayHealthDiagnostics());
  }
  const node = shareViewerBanner?.querySelector?.('.share-viewer-mirror-status');
  if (!node) return;
  node.classList.toggle('match', cleanStatus === 'mirrored');
  node.classList.toggle('mismatch', cleanStatus === 'error');
  node.textContent = shareReplayUserStatusText(cleanStatus);
}

function prepareShareReplayMirrorRoot() {
  const root = appRootElement();
  if (!root || root === document.body) return null;
  root.replaceChildren();
  root.classList.add('share-replay-root');
  root.dataset.shareReplayRoot = 'true';
  root.dataset.shareReplayInert = 'true';
  root.dataset.shareReplayStatus = 'waiting';
  root.setAttribute('role', 'presentation');
  root.setAttribute('aria-label', 'YO!share replay mirror');
  root.setAttribute('tabindex', '-1');
  return root;
}

function installShareReplayShell() {
  if (!shareReplayShellEnabled()) return false;
  shareReplayShellActive = true;
  applyShareViewBodyClasses();
  document.body?.classList?.add('share-replay-shell');
  const stage = ensureShareMirrorStage();
  if (stage?.dataset) stage.dataset.shareReplayShell = 'true';
  prepareShareReplayMirrorRoot();
  installShareViewerBanner();
  setShareReplayShellStatus('waiting');
  startShareStatusRefresh();
  exposeShareDebugApi();
  return true;
}

function shareReplaySendUiMessage(message = {}) {
  if (!shareReplayShellActive || !shareToken || !message?.type) return false;
  const socket = ensureShareHostSocket(shareToken);
  if (!socket) return false;
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
    return true;
  }
  const shareHostQueue = shareHostQueues.get(shareToken) || [];
  if (shareHostQueue.length < 32) shareHostQueue.push(message);
  shareHostQueues.set(shareToken, shareHostQueue);
  return true;
}

function shareReplayResetKeyframeRequestBackoff() {
  shareReplayKeyframeInFlight = false;
  shareReplayKeyframeBackoffMs = 0;
}

function shareReplayNextKeyframeRequestBackoff() {
  const previous = Math.max(0, Math.round(Number(shareReplayKeyframeBackoffMs) || 0));
  if (!previous) return shareReplayKeyframeRequestInitialBackoffMs;
  return Math.min(shareReplayKeyframeRequestMaxBackoffMs, Math.round(previous * 1.7));
}

function shareReplayRequestKeyframe(reason = 'replay-error', detail = {}) {
  const now = Date.now();
  shareReplayRecordLastReplayError(reason, detail);
  const activeBackoffMs = Math.max(0, Math.round(Number(shareReplayKeyframeBackoffMs) || 0));
  const lastRequestAt = Math.max(0, Math.round(Number(shareReplayKeyframeLastRequestAt) || 0));
  const requestFloorMs = Math.max(shareReplayKeyframeRequestMinIntervalMs, shareReplayKeyframeInFlight ? activeBackoffMs : 0);
  if (lastRequestAt > 0 && now - lastRequestAt < requestFloorMs) {
    shareReplayKeyframeRequestSuppressedCount = Math.max(0, Math.round(Number(shareReplayKeyframeRequestSuppressedCount) || 0)) + 1;
    void shareUploadDebugProfile('share-keyframe-request-suppressed', {
      reason: String(reason || 'replay-error'),
      detail,
      health: shareReplayHealthDiagnostics(),
    });
    return false;
  }
  shareReplayKeyframeRequestCount = Math.max(0, Math.round(Number(shareReplayKeyframeRequestCount) || 0)) + 1;
  shareReplayKeyframeLastRequestAt = now;
  shareReplayKeyframeBackoffMs = shareReplayNextKeyframeRequestBackoff();
  shareReplayKeyframeInFlight = true;
  const payload = {
    reason: String(reason || 'replay-error'),
    error: String(detail.error || '').slice(0, 500),
    digest: String(detail.digest || ''),
    backoffMs: shareReplayKeyframeBackoffMs,
    suppressed: shareReplayKeyframeRequestSuppressedCount,
  };
  for (const key of ['epoch', 'sequence', 'baseSequence', 'currentEpoch', 'lastSequence']) {
    const value = Number(detail[key]);
    if (Number.isFinite(value)) payload[key] = Math.round(value);
  }
  const sent = shareReplaySendUiMessage(shareBuildUiMessage(shareMirrorProtocol.frames.domKeyframeRequest, payload, {reason: payload.reason}));
  void shareUploadDebugProfile('share-keyframe-request', {
    request: payload,
    detail,
    sent,
    health: shareReplayHealthDiagnostics(),
  });
  return sent;
}

function applyShareReplayKeyframe(payload = {}, message = {}) {
  if (!shareReplayShellActive || !payload || typeof payload !== 'object') return false;
  shareReplayRecordFrameMetrics(shareMirrorProtocol.frames.domKeyframe, payload, message);
  try {
    const applied = shareReplayApplyStaticKeyframe(payload, message);
    if (applied) shareReplayResetKeyframeRequestBackoff();
    return applied;
  } catch (error) {
    console.warn('share replay keyframe apply failed', error);
    setShareReplayShellStatus('error', {
      digest: payload.digest,
      sequence: message.sequence,
    });
    shareReplayRequestKeyframe('replay-error', {
      digest: payload.digest,
      error,
    });
  }
  return true;
}

function applyShareReplayShellMessage(message = {}) {
  if (!shareReplayShellActive || !message || message.ch !== 'ui') return false;
  if (message.sender && message.sender === shareClientId) return true;
  const payload = message.payload && typeof message.payload === 'object' ? message.payload : {};
  if (message.type === 'pointer') {
    renderSharePointerGhost({...payload, sender: message.sender || payload.sender || ''});
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.domDelta) return applyShareReplayDelta(payload, message);
  if (shareDropStaleMirrorFrame(message)) return true;
  if (message.type === shareMirrorProtocol.frames.viewport) {
    applyShareViewportState(payload);
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.appearance) {
    applyShareAppearanceState(payload);
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.domKeyframe) return applyShareReplayKeyframe(payload, message);
  if (message.type === shareMirrorProtocol.frames.shareStatus) {
    mergeShareStatusPayload(payload);
    renderShareStatusPill();
    updateShareViewerBanner();
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.terminalHostResize || message.type === shareMirrorProtocol.frames.hostResize) {
    updateShareHostTerminalSize(payload.session, payload.rows, payload.cols);
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.geometryDigest) {
    exposeShareDebugApi();
    return true;
  }
  if (message.type === shareMirrorProtocol.frames.domKeyframeRequest) {
    const reason = String(payload.reason || message.reason || '');
    setShareReplayShellStatus(reason === 'backpressure' ? 'viewer-behind' : 'waiting', {sequence: message.sequence});
    return true;
  }
  if (shareMirrorFrameTypeIsReplay(message.type)) {
    setShareReplayShellStatus(String(message.type || 'replay-frame'), {sequence: message.sequence});
    return true;
  }
  return true;
}

function applyShareMirrorTransform() {
  const root = appRootElement();
  if (!root?.style) return;
  if (!shareViewMode) {
    setAppMirrorTransform({scale: 1, tx: 0, ty: 0});
    root.style.removeProperty('--share-mirror-scale');
    root.style.removeProperty('--share-mirror-tx');
    root.style.removeProperty('--share-mirror-ty');
    return;
  }
  ensureShareMirrorStage();
  const transform = shareMirrorFitTransform(appViewport(), nativeViewport(), shareViewFit);
  setAppMirrorTransform(transform);
  root.style.setProperty('--share-mirror-scale', String(transform.scale));
  root.style.setProperty('--share-mirror-tx', `${transform.tx}px`);
  root.style.setProperty('--share-mirror-ty', `${transform.ty}px`);
  document.body?.classList?.toggle('share-fit-cover', transform.fit === 'cover');
  document.body?.classList?.toggle('share-fit-contain', transform.fit === 'contain');
}

function setShareViewFit(mode, options = {}) {
  const next = normalizeShareViewFit(mode);
  shareViewFit = next;
  storageSet(shareViewFitStorageKey, next);
  clientSettings = mergeSettingObjects(clientSettings, {share: {view_fit: next}});
  applyShareMirrorTransform();
  updateShareViewerBanner();
  if (options.persist !== false && !readOnlyMode) {
    saveSettingsPatch(settingPatch('share.view_fit', next), {preserveLayout: false}).catch(() => {});
  }
}

function yolomuxFontSpecsForCurrentSettings() {
  const uiSize = Math.max(6, Math.round(numberSetting('appearance.ui_font_size', 13)));
  const monoSizes = [
    uiSize,
    terminalFontSize,
    editorFontSize,
    editorPreviewFontSize,
    fileExplorerFontSize,
  ].map(value => Math.max(6, Math.round(Number(value) || uiSize)));
  return [
    `${uiSize}px "YOLOmux UI"`,
    ...monoSizes.map(size => `${size}px "YOLOmux Mono"`),
  ].filter((spec, index, values) => values.indexOf(spec) === index);
}

function waitForYolomuxFontsReady(options = {}) {
  const fonts = document.fonts;
  if (!fonts?.load) return Promise.resolve(false);
  if (!yolomuxFontsReadyPromise) {
    const specs = yolomuxFontSpecsForCurrentSettings();
    yolomuxFontsReadyPromise = Promise.all(specs.map(spec => fonts.load(spec).catch(() => [])))
      .then(() => fonts.ready || true)
      .then(() => true)
      .catch(() => false);
  }
  const timeoutMs = Math.max(0, Math.round(Number(options.timeoutMs ?? yolomuxFontReadyTimeoutMs) || 0));
  if (!timeoutMs) return yolomuxFontsReadyPromise;
  return Promise.race([
    yolomuxFontsReadyPromise,
    new Promise(resolve => setTimeout(() => resolve(false), timeoutMs)),
  ]);
}

function refreshLayoutAfterFontMetricsReady() {
  applyCssSettings();
  renderSessionButtons();
  renderPaneTabStrips();
  if (typeof autosizePreferenceTextareas === 'function') autosizePreferenceTextareas(document);
  for (const session of terminals.keys()) fitTerminal(session);
  document.querySelectorAll?.('.file-editor-panel').forEach(panel => {
    try { panel._cmView?.requestMeasure?.(); } catch (_) {}
  });
  applyShareMirrorTransform();
  if (!shareViewMode && shareHasActiveShare()) scheduleShareUiStatePublish();
}

function installYolomuxFontMetricRefresh() {
  if (!document.fonts?.ready) return;
  waitForYolomuxFontsReady({timeoutMs: 0})
    .then(() => refreshLayoutAfterFontMetricsReady())
    .catch(() => {});
}

function shareFinderSeed() {
  const root = fileExplorerRoot || (typeof fileExplorerRootForOpen === 'function' ? fileExplorerRootForOpen() : '') || homePath || '/';
  return {
    root,
    rootMode: fileExplorerRootMode === 'fixed' ? 'fixed' : 'sync',
    mode: normalizeFileExplorerMode(fileExplorerMode),
    session: typeof fileExplorerSessionFilesTargetSession === 'function' ? fileExplorerSessionFilesTargetSession() : '',
  };
}

function shareSessionsFromLayout(slots = layoutSlots) {
  const result = [];
  for (const item of paneItems(slots)) {
    if (isTmuxSession(item) && !result.includes(item)) result.push(item);
  }
  return result;
}

function shareCreatePayloadFromForm(form) {
  const ttlSeconds = shareTtlSecondsFromForm(form);
  const maxViewers = Number(form?.elements?.max_viewers?.value || shareDefaultMaxViewers);
  const readOnly = form?.elements?.read_only?.checked !== false;
  const scheme = String(form?.elements?.scheme?.value || 'http') === 'https' ? 'https' : 'http';
  const sharedSessions = shareSessionsFromLayout();
  const targetSession = sharedSessions[0] || currentSessionActionTarget();
  const seed = shareLayoutSeed();
  return {
    session: targetSession,
    sessions: sharedSessions.length ? sharedSessions : [targetSession].filter(Boolean),
    ttl_seconds: ttlSeconds,
    max_viewers: maxViewers,
    mode: readOnly ? 'ro' : 'rw',
    read_only: readOnly,
    scheme,
    debug_profile: form?.elements?.debug_profile?.checked === true,
    layout: seed.layout,
    tabs: seed.tabs,
    finder: shareFinderSeed(),
    ui_state: shareCreateUiStateSnapshot(),
  };
}

function shareHostWsUrl(token) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({share: token, client: shareClientId});
  return `${scheme}//${location.host}/ws/share-host?${params.toString()}`;
}

function shareViewerUiWsUrl(token) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({token, viewer: shareViewerId || shareClientId, client: shareClientId});
  return `${scheme}//${location.host}/ws/share-ui?${params.toString()}`;
}

function closeShareHostSocket() {
  for (const socket of shareHostSockets.values()) {
    try { socket?.close?.(); } catch (_) {}
  }
  shareHostSockets = new Map();
  shareHostQueues = new Map();
}

function flushShareHostQueue(token) {
  const socket = shareHostSockets.get(token);
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const shareHostQueue = shareHostQueues.get(token) || [];
  const pending = shareHostQueue.splice(0, shareHostQueue.length);
  shareHostQueues.set(token, shareHostQueue);
  for (const message of pending) socket.send(typeof message === 'string' ? message : JSON.stringify(message));
}

function ensureShareHostSocket(token) {
  const cleanToken = String(token || '');
  if ((!shareViewMode && readOnlyMode) || !cleanToken) return null;
  const current = shareHostSockets.get(cleanToken);
  if (current && current.readyState !== WebSocket.CLOSED && current.readyState !== WebSocket.CLOSING) {
    return current;
  }
  const socket = new WebSocket(shareViewMode ? shareViewerUiWsUrl(cleanToken) : shareHostWsUrl(cleanToken));
  shareHostSockets.set(cleanToken, socket);
  if (!shareHostQueues.has(cleanToken)) shareHostQueues.set(cleanToken, []);
  socket.onopen = () => flushShareHostQueue(cleanToken);
  socket.onmessage = event => {
    const message = shareSocketMessage(event.data);
    if (message?.ch === 'ui') applyShareUiMessage(message);
  };
  socket.onclose = () => {
    if (shareHostSockets.get(cleanToken) === socket) shareHostSockets.delete(cleanToken);
  };
  socket.onerror = () => {};
  return socket;
}

function ensureShareHostSockets() {
  const activeTokens = new Set(
    shareViewMode
      ? [shareToken].filter(Boolean)
      : activeShares.map(share => share.token).filter(Boolean)
  );
  for (const token of activeTokens) ensureShareHostSocket(token);
  for (const [token, socket] of shareHostSockets.entries()) {
    if (activeTokens.has(token)) continue;
    try { socket?.close?.(); } catch (_) {}
    shareHostSockets.delete(token);
    shareHostQueues.delete(token);
  }
}

function shareCanPublishUi() {
  if (applyingShareRemoteUiState) return false;
  if (shareReplayViewerModeEnabled()) return false;
  if (shareViewMode) return shareWriteMode && Boolean(shareToken);
  return !readOnlyMode && shareHasActiveShare();
}

function shareCanPublishScroll() {
  if (shareViewMode && !shareWriteMode) return false;
  return shareCanPublishUi();
}

function beginShareRemoteUiApply() {
  applyingShareRemoteUiState = Math.max(0, Number(applyingShareRemoteUiState) || 0) + 1;
  let finished = false;
  return () => {
    if (finished) return;
    finished = true;
    applyingShareRemoteUiState = Math.max(0, (Number(applyingShareRemoteUiState) || 0) - 1);
  };
}

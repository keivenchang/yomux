// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Share admin UI, viewer banner, status refresh, and read-only interaction blocking.
function ensureShareStatusPill() {
  if (shareStatusPill) return shareStatusPill;
  shareStatusPill = makeButton({
    id: 'shareStatusPill',
    className: 'share-status-pill',
    hidden: true,
    onClick: () => showShareModal(),
  });
  const tabMeta = document.getElementById('tabMetaToggle');
  const notify = document.getElementById('notifyToggle');
  const anchor = tabMeta?.parentElement ? tabMeta : notify;
  if (anchor?.parentElement) anchor.parentElement.insertBefore(shareStatusPill, anchor);
  else statusEl?.parentElement?.insertBefore(shareStatusPill, statusEl);
  return shareStatusPill;
}

function renderShareStatusPill() {
  const pill = ensureShareStatusPill();
  if (!shareHasActiveShare()) {
    pill.hidden = true;
    pill.classList.remove('share-mode-read', 'share-mode-write');
    return;
  }
  pill.hidden = false;
  const mode = activeShares.some(share => share?.mode === 'rw') ? 'write' : 'read';
  pill.classList.toggle('share-mode-write', mode === 'write');
  pill.classList.toggle('share-mode-read', mode === 'read');
  const text = activeShares.length === 1
    ? t('share.pill', {
      viewers: activeShares[0].viewers,
      time: shareTimeLeftText(activeShares[0]),
    })
    : t('share.pillMultiple', {
      count: activeShares.length,
      viewers: shareTotalViewers(),
      time: shareSoonestTimeLeftText(),
    });
  const count = document.createElement('span');
  count.className = 'share-status-count';
  count.textContent = String(shareTotalViewers());
  count.setAttribute('aria-hidden', 'true');
  pill.replaceChildren(count);
  pill.title = `${t('share.pill.title')} - ${text}`;
  pill.setAttribute('aria-label', text);
}

function shareByRow(row) {
  const token = row?.dataset?.shareToken || '';
  const shortId = row?.dataset?.shareShortId || '';
  return activeShares.find(share => (
    (token && share.token === token)
    || (shortId && share.shortId === shortId)
  )) || null;
}

function renderShareCountdowns() {
  document.querySelectorAll('[data-share-countdown]').forEach(node => {
    const share = shareByRow(node.closest('.share-entry'));
    if (share) node.textContent = t('share.resultExpires', {time: shareTimeLeftText(share)});
  });
  document.querySelectorAll('[data-share-viewer-count]').forEach(node => {
    const share = shareByRow(node.closest('.share-entry'));
    if (!share) return;
    node.textContent = t('share.resultViewers', {
      viewers: share.viewers,
      max: share.maxViewers || shareDefaultMaxViewers,
    });
  });
  document.querySelectorAll('[data-share-viewer-duration]').forEach(node => {
    const row = node.closest('[data-share-viewer-row]');
    const share = shareByRow(node.closest('.share-entry'));
    const index = Number(row?.dataset?.shareViewerIndex || 0);
    const viewer = share?.viewerDetails?.[index];
    if (viewer) node.textContent = shareViewerDurationText(viewer);
  });
}

function shareModalIsVisible() {
  const modal = document.getElementById('modal');
  return Boolean(modal?.classList?.contains(CLS.open) && modal.classList.contains('share-open'));
}

function shareStatusSurfaceVisible() {
  if (document.visibilityState === 'hidden') return false;
  if (shareViewMode) return Boolean(shareViewerBanner?.isConnected);
  if (shareStatusPill?.isConnected && shareStatusPill.hidden !== true) return true;
  if (!shareModalIsVisible()) return false;
  const {body} = shareModalElements();
  return Boolean(body?.querySelector?.('.share-entry, [data-share-countdown], [data-share-viewer-count], [data-share-viewer-duration]'));
}

async function refreshActiveShare(options = {}) {
  if (readOnlyMode) return [];
  if (shareStatusRefreshInFlight) return activeShares;
  shareStatusRefreshInFlight = true;
  try {
    const previousTokens = new Set(activeShares.map(share => share.token).filter(Boolean));
    setActiveShares(normalizeShareListPayload(await apiFetchJson('/api/share', {cache: 'no-store'})));
    shareStatusLastRefreshAt = Date.now();
    renderShareStatusPill();
    if (shareHasActiveShare()) {
      ensureShareHostSockets();
      installShareReplayMutationPublisher();
      if (options.publishReplayKeyframe === true || activeShares.some(share => share?.token && !previousTokens.has(share.token))) {
        sharePublishDomKeyframe('join');
      }
    } else {
      closeShareHostSocket();
    }
    return activeShares;
  } catch (error) {
    if (options.silent !== true) statusErr(localizedHtml('share.statusLoadFailed', {error}));
    return activeShares;
  } finally {
    shareStatusRefreshInFlight = false;
  }
}

async function refreshShareViewerStatus(options = {}) {
  if (!shareViewMode || !shareToken || shareStatusRefreshInFlight) return activeShares;
  shareStatusRefreshInFlight = true;
  try {
    const share = normalizeSharePayload(await apiFetchJson('/api/share', {cache: 'no-store'}));
    if (share) setActiveShares([share]);
    shareStatusLastRefreshAt = Date.now();
    updateShareViewerBanner();
    return activeShares;
  } catch (error) {
    if (options.silent !== true) statusErr(localizedHtml('share.statusLoadFailed', {error}));
    return activeShares;
  } finally {
    shareStatusRefreshInFlight = false;
  }
}

function shareModalElements() {
  return {
    modal: document.getElementById('modal'),
    title: document.getElementById('modalTitle'),
    body: document.getElementById('modalBody'),
  };
}

function openShareModalChrome(titleText) {
  const {modal, title, body} = shareModalElements();
  if (!modal || !title || !body) return {};
  modal.classList.remove('about-open');
  modal.classList.add('share-open', 'open');
  relocalizeModalChrome({content: false});
  title.textContent = titleText;
  body.innerHTML = '';
  scheduleSharePopupLayerPublish();
  return {modal, title, body};
}

function shareTlsAvailable() {
  return location.protocol === 'https:';
}

function syncShareProtocolControls(form) {
  if (!form) return;
  const readOnly = form.elements.read_only;
  const http = form.querySelector('input[name="scheme"][value="http"]');
  const https = form.querySelector('input[name="scheme"][value="https"]');
  const hint = form.querySelector('[data-share-protocol-hint]');
  if (!shareTlsAvailable()) {
    readOnly.checked = true;
    readOnly.disabled = true;
    http.checked = true;
    http.disabled = false;
    https.disabled = true;
    if (hint) hint.textContent = t('share.hint.noTls');
    return;
  }
  readOnly.disabled = false;
  if (!readOnly.checked) {
    https.checked = true;
    http.disabled = true;
    if (hint) hint.textContent = t('share.hint.writeHttps');
    return;
  }
  http.disabled = false;
  https.disabled = false;
  if (hint) hint.textContent = http.checked ? t('share.hint.http') : '';
}

function renderShareCreateView() {
  const {body} = openShareModalChrome(t('brand.share'));
  if (!body) return;
  body.innerHTML = shareCreateFormHtml();
  bindShareCreateForm(body);
  scheduleSharePopupLayerPublish();
}

function shareCreateFormHtml() {
  const sharedSessions = shareSessionsFromLayout();
  if (!sharedSessions.length && !currentSessionActionTarget()) {
    return `<div class="share-modal-message">${esc(t('share.noSession'))}</div>`;
  }
  const ttlDefault = Math.max(60, Math.min(28800, Math.round(Number(shareDefaultTtlSeconds) || 600)));
  const ttlMinutesDefault = shareTtlMinutesFromSeconds(ttlDefault);
  const maxViewersDefault = Math.max(1, Math.min(300, Math.round(Number(shareDefaultMaxViewers) || 2)));
  const readOnlyChecked = shareDefaultReadOnly || !shareTlsAvailable();
  const defaultScheme = shareDefaultSchemeForForm(readOnlyChecked);
  const errorText = shareCreateErrorPayload ? userMessageText(shareCreateErrorPayload) : '';
  return `<form class="share-modal-form" id="shareCreateForm">
    <div class="share-create-controls">
    <label class="share-field">
      <span>${esc(t('share.maxTime'))}</span>
      <span class="share-duration-control">
        <input name="ttl_minutes" type="number" min="1" max="480" step="1" inputmode="numeric" value="${ttlMinutesDefault}">
        <span class="share-duration-unit">${esc(shareMinuteUnitLabel())}</span>
      </span>
    </label>
    <label class="share-field">
      <span>${esc(t('share.maxViewers'))}</span>
      <input name="max_viewers" type="number" min="1" max="300" step="1" value="${maxViewersDefault}">
    </label>
    <label class="share-checkbox">
      <input name="read_only" type="checkbox" ${readOnlyChecked ? 'checked' : ''}>
      <span>${esc(t('share.readOnly'))}</span>
    </label>
    <label class="share-checkbox" title="${esc(t('share.debugProfileHelp'))}">
      <input name="debug_profile" type="checkbox">
      <span>${esc(t('share.debugProfile'))}</span>
    </label>
    </div>
    <fieldset class="share-protocol-group">
      <legend>${esc(t('share.protocol'))}</legend>
      <label><input name="scheme" type="radio" value="http" ${defaultScheme === 'http' ? 'checked' : ''}> http</label>
      <label><input name="scheme" type="radio" value="https" ${defaultScheme === 'https' ? 'checked' : ''}> https</label>
      <div class="share-hint" data-share-protocol-hint></div>
    </fieldset>
    <div class="share-security-note">${esc(t('share.securityNote'))}</div>
    <div class="share-error" ${errorText ? '' : 'hidden'}>${esc(errorText)}</div>
    <div class="share-actions">
      <button type="submit">${esc(t('share.create'))}</button>
    </div>
  </form>`;
}

function bindShareCreateForm(root) {
  const form = root?.querySelector?.('#shareCreateForm');
  if (!form) return;
  form.addEventListener('change', () => syncShareProtocolControls(form));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    await createShareFromForm(form);
  });
  syncShareProtocolControls(form);
}

function shareEntryHtml(share) {
  const session = share.session ? sessionLabel(share.session) : t('share.host');
  const mode = shareModeLabel(share);
  const scheme = share.scheme === 'https' ? 'https' : 'http';
  const title = t('share.entryTitle', {session, mode, scheme});
  return `<section class="share-entry share-mode-${share.mode === 'rw' ? 'write' : 'read'}" data-share-token="${esc(share.token)}" data-share-short-id="${esc(share.shortId)}">
    <div class="share-entry-heading">
      <strong>${esc(title)}</strong>
      <span>${esc(t('share.id', {id: share.shortId || share.token.slice(0, 8)}))}</span>
    </div>
    <label class="share-field share-url-field share-url-primary">
      <span class="share-url-primary-head">
        <span>${esc(t('share.url'))}</span>
      </span>
      <span class="share-url-control">
        <input type="text" readonly value="${esc(share.url)}" data-share-secret>
        <button type="button" class="path-copy-button share-url-copy-button" data-share-copy data-share-secret title="${esc(t('common.copy'))}" aria-label="${esc(t('common.copy'))}"></button>
      </span>
    </label>
    <div class="share-result-meta">
      <span data-share-countdown>${esc(t('share.resultExpires', {time: shareTimeLeftText(share)}))}</span>
      <span data-share-viewer-count>${esc(t('share.resultViewers', {viewers: share.viewers, max: share.maxViewers || shareDefaultMaxViewers}))}</span>
      <span>${esc(mode)}</span>
      <span>${esc(scheme)}</span>
      ${share.debugProfile ? `<span title="${esc(t('share.debugProfileHelp'))}">${esc(t('share.debugProfileOn'))}</span>` : ''}
      <button type="button" class="share-extend-button control-active-hover" data-share-extend>${esc(t('share.extendTenMinutes'))}</button>
      <button type="button" class="danger share-stop-inline" data-share-stop>${esc(t('share.stop'))}</button>
    </div>
    ${shareViewerListHtml(share)}
  </section>`;
}

function shareViewerListHtml(share) {
  const viewers = Array.isArray(share?.viewerDetails) ? share.viewerDetails : [];
  const rows = viewers.length
    ? viewers.map((viewer, index) => {
      const ip = viewer.ip || t('share.users.unknownIp');
      const browser = viewer.browser || t('share.users.unknownBrowser');
      return `<div class="share-users-row" data-share-viewer-row data-share-viewer-index="${index}">
        <span data-share-viewer-duration>${esc(shareViewerDurationText(viewer))}</span>
        <span title="${esc(ip)}">${esc(ip)}</span>
        <span title="${esc(browser)}">${esc(browser)}</span>
      </div>`;
    }).join('')
    : `<div class="share-users-empty">${esc(t('share.users.empty'))}</div>`;
  return `<div class="share-users" data-share-users>
    <div class="share-users-title">${esc(t('share.users.title', {count: viewers.length}))}</div>
    <div class="share-users-table" role="table" aria-label="${esc(t('share.users.title', {count: viewers.length}))}">
      <div class="share-users-row header" role="row">
        <span role="columnheader">${esc(t('share.users.duration'))}</span>
        <span role="columnheader">${esc(t('share.users.ip'))}</span>
        <span role="columnheader">${esc(t('share.users.browser'))}</span>
      </div>
      ${rows}
    </div>
  </div>`;
}

function renderShareViewerLists() {
  document.querySelectorAll('[data-share-users]').forEach(node => {
    const share = shareByRow(node.closest('.share-entry'));
    if (share) node.outerHTML = shareViewerListHtml(share);
  });
}

function bindShareEntries(root) {
  root?.querySelectorAll?.('[data-share-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const token = button.closest('[data-share-token]')?.dataset.shareToken || '';
      const share = activeShares.find(candidate => candidate.token === token);
      if (!share) return;
      await copyTextToClipboard(share.url);
      statusOk(localizedHtml('share.copied'));
    });
  });
  root?.querySelectorAll?.('[data-share-stop]').forEach(button => {
    button.addEventListener('click', async () => {
      const token = button.closest('[data-share-token]')?.dataset.shareToken || '';
      await stopActiveShare(token);
    });
  });
  root?.querySelectorAll?.('[data-share-extend]').forEach(button => {
    button.addEventListener('click', async () => {
      const token = button.closest('[data-share-token]')?.dataset.shareToken || '';
      await extendActiveShare(token);
    });
  });
}

function renderShareManageView() {
  const {body} = openShareModalChrome(t('brand.share'));
  if (!body) return;
  if (!shareHasActiveShare()) {
    renderShareCreateView();
    return;
  }
  const list = activeShares.map(shareEntryHtml).join('');
  body.innerHTML = `<div class="share-result">
    <div class="share-create-panel">
      <div class="share-section-title">${esc(t('share.newShare'))}</div>
      ${shareCreateFormHtml()}
    </div>
    <div class="share-active-panel">
      <div class="share-section-title">${esc(t('share.activeShares', {count: activeShares.length}))}</div>
      <div class="share-entry-list">${list}</div>
    </div>
  </div>`;
  bindShareEntries(body);
  bindShareCreateForm(body);
  scheduleSharePopupLayerPublish();
}

function renderShareResultView(share = activeShare) {
  if (share && !activeShares.some(candidate => candidate.token === share.token)) {
    setActiveShares([...activeShares, share]);
  }
  renderShareManageView();
}

function shareCreateFormValues(form) {
  if (!form) return null;
  return {
    ttlMinutes: form.elements.ttl_minutes?.value || '',
    maxViewers: form.elements.max_viewers?.value || '',
    readOnly: form.elements.read_only?.checked === true,
    debugProfile: form.elements.debug_profile?.checked === true,
    scheme: form.elements.scheme?.value || '',
  };
}

function restoreShareCreateFormValues(form, values) {
  if (!form || !values) return;
  if (form.elements.ttl_minutes) form.elements.ttl_minutes.value = values.ttlMinutes;
  if (form.elements.max_viewers) form.elements.max_viewers.value = values.maxViewers;
  if (form.elements.read_only) form.elements.read_only.checked = values.readOnly;
  if (form.elements.debug_profile) form.elements.debug_profile.checked = values.debugProfile;
  const scheme = form.querySelector(`input[name="scheme"][value="${cssEscape(values.scheme)}"]`);
  if (scheme) scheme.checked = true;
  syncShareProtocolControls(form);
}

function relocalizeShareModal() {
  if (!shareModalIsVisible()) return false;
  const previousForm = document.getElementById('shareCreateForm');
  const values = shareCreateFormValues(previousForm);
  if (shareHasActiveShare()) renderShareManageView();
  else renderShareCreateView();
  restoreShareCreateFormValues(document.getElementById('shareCreateForm'), values);
  return true;
}

async function createShareFromForm(form) {
  const submit = form.querySelector('button[type="submit"]');
  const error = form.querySelector('.share-error');
  if (submit) submit.disabled = true;
  if (error) {
    error.hidden = true;
    error.textContent = '';
  }
  shareCreateErrorPayload = null;
  try {
    const payload = shareCreatePayloadFromForm(form);
    const createdShare = normalizeSharePayload(await apiFetchJson('/api/share', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }));
    if (createdShare) {
      setActiveShares([...activeShares.filter(share => share.token !== createdShare.token), createdShare]);
    }
    ensureShareHostSockets();
    await refreshActiveShare({silent: true});
    sharePublishLayout();
    sharePublishUiState();
    sharePublishDomKeyframe('join');
    renderShareStatusPill();
    renderShareManageView();
    statusOk(localizedHtml('share.created'));
  } catch (err) {
    shareCreateErrorPayload = userMessageSnapshot(err);
    if (error) {
      error.hidden = false;
      error.textContent = userMessageText(shareCreateErrorPayload);
    }
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function stopActiveShare(tokenOrShortId = '') {
  try {
    const target = String(tokenOrShortId || '').trim();
    const options = target
      ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: target})}
      : {method: 'POST'};
    await apiFetchJson('/api/share/stop', options);
    await refreshActiveShare({silent: true});
    renderShareStatusPill();
    if (shareHasActiveShare()) renderShareManageView();
    else renderShareCreateView();
    statusOk(localizedHtml('share.stopped'));
  } catch (error) {
    statusErr(localizedHtml('share.stopFailed', {error}));
  }
}

async function extendActiveShare(tokenOrShortId = '', addSeconds = 600) {
  try {
    const target = String(tokenOrShortId || '').trim();
    const updated = normalizeSharePayload(await apiFetchJson('/api/share/extend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: target, add_seconds: addSeconds}),
    }));
    if (updated) {
      setActiveShares(activeShares.map(share => share.token === updated.token ? updated : share));
      renderShareStatusPill();
      renderShareCountdowns();
      updateShareViewerBanner();
    }
  } catch (error) {
    statusErr(localizedHtml('share.statusLoadFailed', {error}));
  }
}

async function showShareModal() {
  shareCreateErrorPayload = null;
  openShareModalChrome(t('brand.share'));
  const {body} = shareModalElements();
  if (body) body.textContent = t('common.loading');
  const shares = await refreshActiveShare({silent: true});
  if (shares.length) renderShareManageView();
  else renderShareCreateView();
}

function updateShareViewerBanner() {
  if (!shareViewerBanner || !shareBootstrap) return;
  const host = shareBootstrap.createdBy || t('share.host');
  const share = shareViewerCurrentShare();
  shareViewerBanner.classList.toggle('share-mode-write', share?.mode === 'rw');
  shareViewerBanner.classList.toggle('share-mode-read', share?.mode !== 'rw');
  const text = document.createElement('span');
  text.className = 'share-viewer-banner-text';
  text.textContent = t('share.viewerBanner', {
    host,
    mode: share?.mode === 'rw' ? t('share.mode.write') : t('common.readOnly'),
    time: shareTimeLeftText(share),
  });
  const fitTitle = shareViewFit === 'cover' ? t('share.fit.switchToContain') : t('share.fit.switchToCover');
  const fit = makeButton({
    className: 'share-view-fit-toggle control-active-hover',
    dataset: {shareViewerControl: 'fit'},
    label: shareViewFit === 'cover' ? t('share.fit.cover') : t('share.fit.contain'),
    title: fitTitle,
    ariaLabel: fitTitle,
    onClick: event => {
      event.preventDefault();
      event.stopPropagation();
      setShareViewFit(shareViewFit === 'cover' ? 'contain' : 'cover');
    },
  });
  const mirror = document.createElement('span');
  mirror.className = 'share-viewer-mirror-status';
  mirror.setAttribute('aria-live', 'polite');
  const children = [text, mirror, fit];
  if (shareDebugEnabled) {
    const debugTitle = t('share.debug.copyDiagnostics');
    const debug = makeButton({
      className: 'share-view-fit-toggle share-debug-copy control-active-hover',
      dataset: {shareViewerControl: 'debug'},
      label: t('common.copy'),
      title: debugTitle,
      ariaLabel: debugTitle,
      onClick: event => {
        event.preventDefault();
        event.stopPropagation();
        copyShareDebugDiagnostics().catch(error => console.warn('share debug copy failed', error));
      },
    });
    children.push(debug);
  }
  shareViewerBanner.replaceChildren(...children);
  updateShareMirrorDigestStatus();
  exposeShareDebugApi();
}

function installShareViewerBanner() {
  if (!shareViewMode || shareViewerBanner) return;
  applyShareViewBodyClasses();
  ensureShareMirrorStage();
  shareViewerBanner = document.createElement('div');
  shareViewerBanner.className = 'share-viewer-banner';
  shareViewerBanner.setAttribute('role', 'status');
  shareViewerBanner.setAttribute('aria-live', 'polite');
  document.body?.appendChild(shareViewerBanner);
  updateShareViewerBanner();
  applyShareMirrorTransform();
}

function startShareStatusRefresh() {
  ensureShareStatusPill();
  if (shareViewMode) {
    setActiveShares([normalizeSharePayload({
      active: true,
      token: shareToken,
      ...shareBootstrap,
      expires_at: shareBootstrap?.expiresAt,
      max_viewers: shareBootstrap?.maxViewers,
    })].filter(Boolean));
    if (redirectExpiredShareViewerToLogin()) return;
    ensureShareHostSockets();
    refreshShareViewerStatus({silent: true});
  } else if (!readOnlyMode) {
    refreshActiveShare({silent: true});
  }
  resetRuntimeInterval('share-status', () => {
    if (redirectExpiredShareViewerToLogin()) return;
    if (shareStatusSurfaceVisible()) {
      renderShareStatusPill();
      updateShareViewerBanner();
      renderShareCountdowns();
    }
    if (shareViewMode && Date.now() - shareStatusLastRefreshAt >= shareViewerStatusBackupRefreshMs) {
      refreshShareViewerStatus({silent: true});
    } else if (shareHostStatusBackupPollDue()) {
      refreshActiveShare({silent: true});
    }
  }, 1000);
}

function shareHostStatusBackupPollDue(nowMs = Date.now()) {
  return !readOnlyMode
    && shareHasActiveShare()
    && nowMs - shareStatusLastRefreshAt >= shareHostStatusBackupRefreshMs;
}

const shareReadonlyPreventDefaultEvents = new Set(['beforeinput', 'change', 'drop', 'dragstart', 'input', 'paste', 'submit']);
const shareReadonlyActivationEvents = new Set(['auxclick', 'click', 'dblclick']);
const shareReadonlyNavigationKeys = new Set([
  'arrowdown',
  'arrowleft',
  'arrowright',
  'arrowup',
  'end',
  'escape',
  'home',
  'pagedown',
  'pageup',
  'tab',
]);

function shareReadonlyKeyboardAllowsDefault(event) {
  const key = String(event?.key || '').toLowerCase();
  const copyModifier = event?.metaKey || event?.ctrlKey;
  if (copyModifier && ['a', 'c', 'insert'].includes(key)) return true;
  if (event?.shiftKey && key === 'insert') return true;
  return key === 'escape';
}

function shareReadonlyTargetActivates(target) {
  const node = target?.closest?.('button, a, input, select, textarea, label, summary, [role="button"], [role="menuitem"], [role="option"], [role="checkbox"], [contenteditable="true"]');
  return Boolean(node);
}

function shareReadonlyShouldPreventDefault(event) {
  if (!event) return false;
  if (event.type === 'wheel' || event.type === 'touchmove') return shareReadonlyTargetIsMirroredSurface(event.target);
  if (event.type === 'mousedown' || event.type === 'pointerdown' || event.type === 'touchstart') {
    return shareReadonlyPointerEventHitsScrollContainer(event);
  }
  if (shareReadonlyPreventDefaultEvents.has(event.type)) return true;
  if (event.type === 'keydown' || event.type === 'keypress' || event.type === 'keyup') {
    return !shareReadonlyKeyboardAllowsDefault(event);
  }
  if (shareReadonlyActivationEvents.has(event.type)) {
    return shareReadonlyTargetActivates(event.target);
  }
  return false;
}

function shareReadonlyTargetIsMirroredSurface(target) {
  const node = target?.closest?.('#appRoot, .app-root');
  if (!node || target?.closest?.('[data-share-viewer-control]')) return false;
  return Boolean(target?.closest?.('.file-editor-panel, .file-explorer-panel, .terminal-pane, .terminal, .preferences-scroll, .app-menu, #modal'));
}

function shareReadonlyPointerEventHitsScrollContainer(event) {
  if (!shareReadonlyTargetIsMirroredSurface(event?.target)) return false;
  const descriptor = shareScrollTargetForElement(event.target);
  return Boolean(descriptor?.element && descriptor.element === event.target);
}

function shareReadonlyScrollStateForTarget(target) {
  const descriptor = shareScrollTargetForElement(target);
  if (!descriptor?.target) return null;
  const state = shareSenderRecord(descriptor.target, {create: false})?.scrollTarget;
  if (!state) return null;
  const top = Math.max(0, Math.round(Number(state.top || 0)));
  const left = Math.max(0, Math.round(Number(state.left || 0)));
  return {descriptor, top, left};
}

function shareReadonlyScrollMatchesAppliedState(target) {
  const snapshot = shareReadonlyScrollStateForTarget(target);
  if (!snapshot?.descriptor?.element) return false;
  return Math.round(Number(snapshot.descriptor.element.scrollTop || 0)) === snapshot.top
    && Math.round(Number(snapshot.descriptor.element.scrollLeft || 0)) === snapshot.left;
}

function restoreShareReadonlyScrollTarget(target) {
  const snapshot = shareReadonlyScrollStateForTarget(target);
  if (!snapshot) return false;
  const {descriptor, top, left} = snapshot;
  return applyShareScrollDescriptorPosition(descriptor, top, left);
}

function restoreShareScrollTargetByKey(target) {
  const cleanTarget = String(target || '');
  if (!cleanTarget) return false;
  const state = shareSenderRecord(cleanTarget, {create: false})?.scrollTarget;
  if (!state) return false;
  const payload = {
    ...(state.payload || {}),
    target: cleanTarget,
    top: Math.max(0, Math.round(Number(state.top || 0))),
    left: Math.max(0, Math.round(Number(state.left || 0))),
  };
  const descriptor = shareScrollElementForPayload(payload);
  if (!descriptor) return false;
  return applyShareScrollDescriptorPosition(descriptor, payload.top, payload.left);
}

function scheduleShareScrollRestoreByKey(target, options = {}) {
  const cleanTarget = String(target || '');
  if (!shareViewMode || !cleanTarget) return false;
  const record = shareSenderRecord(cleanTarget, {create: false})?.scrollTarget;
  if (!record) return false;
  const frames = Math.max(1, Math.min(8, Math.round(Number(options.frames || 4))));
  const restoreGeneration = record.restoreGeneration + 1;
  record.restoreGeneration = restoreGeneration;
  record.remainingFrames = frames;
  const run = () => {
    if (shareSenderRecord(cleanTarget, {create: false})?.scrollTarget !== record || record.restoreGeneration !== restoreGeneration) return;
    const previous = applyingShareRemoteScroll;
    applyingShareRemoteScroll = true;
    try {
      restoreShareScrollTargetByKey(cleanTarget);
    } finally {
      applyingShareRemoteScroll = previous;
    }
    record.remainingFrames -= 1;
    if (record.remainingFrames > 0) {
      requestAnimationFrame(run);
    } else if (shareSenderRecord(cleanTarget, {create: false})?.scrollTarget === record && record.restoreGeneration === restoreGeneration) {
      record.remainingFrames = 0;
    }
  };
  requestAnimationFrame(run);
  return true;
}

function restoreShareScrollTargetsByPrefix(prefix) {
  const cleanPrefix = String(prefix || '');
  if (!cleanPrefix) return;
  for (const [target] of shareSenderRecordEntries('scrollTarget')) {
    if (String(target).startsWith(cleanPrefix)) scheduleShareScrollRestoreByKey(target);
  }
}

function blockShareReadonlyInteraction(event) {
  if (!shareSemanticReadOnlyMirrorEnabled()) return;
  if (event.target?.closest?.('[data-share-viewer-control]')) return;
  if (event.type === 'scroll') {
    // Host-authored scrolls must reach virtualized widgets such as CodeMirror so they can mount the
    // rows at the mirrored offset. Only a scroll that diverges from the authoritative host position
    // is local input and needs to be restored/stopped.
    if (shareReadonlyScrollMatchesAppliedState(event.target)) return;
    restoreShareReadonlyScrollTarget(event.target);
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
    return;
  }
  if (shareReadonlyShouldPreventDefault(event)) event.preventDefault?.();
  event.stopImmediatePropagation?.();
  event.stopPropagation?.();
}

function installShareReadonlyInteractionBlocker() {
  if (!shareSemanticReadOnlyMirrorEnabled()) return;
  const blockedEvents = [
    'auxclick',
    'beforeinput',
    'change',
    'click',
    'contextmenu',
    'dblclick',
    'dragstart',
    'drop',
    'focusin',
    'input',
    'keydown',
    'keypress',
    'keyup',
    'mousedown',
    'mouseover',
    'mouseup',
    'paste',
    'pointerenter',
    'pointerdown',
    'pointerover',
    'pointerup',
    'submit',
    'scroll',
    'touchstart',
    'touchmove',
    'wheel',
  ];
  for (const name of blockedEvents) {
    window.addEventListener(name, blockShareReadonlyInteraction, {capture: true, passive: false});
  }
}

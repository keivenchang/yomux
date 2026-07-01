
function paneFrameControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => ` type="button" disabled title="${esc(t('tab.unavailableFor', {name: unavailableLabel}))}" aria-label="${esc(label)}"`;
  const controls = [];
  const includeActions = options.actions ?? isTmuxSession(session);
  const includeDetails = options.details === true;
  const includeMinimize = options.minimize !== false;
  const includeExpand = options.expand !== false;
  const includePopout = options.popout === true;
  if (includeActions) {
    controls.push(disabled
      ? `<button class="tab pane-actions" ${disabledAttrs(t('pane.actions'))}><span class="pane-actions-dots" aria-hidden="true">...</span></button>`
      : `<button type="button" class="tab pane-actions" data-pane-actions="${esc(session)}" title="${esc(t('pane.actions'))}" aria-label="${esc(t('pane.actions'))}"><span class="pane-actions-dots" aria-hidden="true">...</span></button>`);
  }
  if (includeDetails) {
    const detailsLabel = t('pane.details.hide');
    controls.push(disabled
      ? `<button class="tab panel-detail-toggle pane-detail-toggle ${platformWindowControlClass('minimize')}" ${disabledAttrs(detailsLabel)}></button>`
      : `<button type="button" class="tab panel-detail-toggle pane-detail-toggle ${platformWindowControlClass('minimize')} active" data-detail-toggle="${esc(session)}" title="${esc(detailsLabel)}" aria-label="${esc(detailsLabel)}" aria-pressed="true"></button>`);
  }
  if (includePopout) {
    controls.push(disabled
      ? `<button class="tab pane-popout" ${disabledAttrs(t('tab.popout'))}></button>`
      : `<button type="button" class="tab pane-popout" data-pane-popout="${esc(session)}" title="${esc(t('tab.popout'))}" aria-label="${esc(t('tab.popout'))}"></button>`);
  }
  if (includeMinimize) {
    controls.push(disabled
      ? `<button class="tab pane-minimize ${platformWindowControlClass('minimize')}" ${disabledAttrs(t('pane.minimize'))}></button>`
      : `<button type="button" class="tab pane-minimize ${platformWindowControlClass('minimize')}" data-pane-minimize="${esc(session)}" title="${esc(t('pane.minimize'))}" aria-label="${esc(t('pane.minimize'))}"></button>`);
  }
  if (includeExpand) {
    const expandAttrs = `${canPaneExpand(session) ? '' : ' hidden'} type="button" data-pane-expand="${esc(session)}" title="${esc(t('pane.expand'))}" aria-label="${esc(t('pane.expand'))}"`;
    controls.push(disabled
      ? `<button class="tab pane-expand ${platformWindowControlClass('zoom')}" ${disabledAttrs(t('pane.expand'))}></button>`
      : `<button class="tab pane-expand ${platformWindowControlClass('zoom')}" ${expandAttrs}></button>`);
  }
  if (options.close) {
    const closeLabel = options.closeLabel || t('pane.closeTab');
    const closeTitle = options.closeTitle || closeLabel;
    const closeClass = options.closeClass ? ` ${options.closeClass}` : '';
    const closeData = `data-pane-close="${esc(session)}"`;
    controls.push(disabled
      ? `<button class="tab pane-close ${platformWindowControlClass('close')}${closeClass}" ${disabledAttrs(closeLabel)}></button>`
      : `<button type="button" class="tab pane-close ${platformWindowControlClass('close')}${closeClass}" ${closeData} title="${esc(closeTitle)}" aria-label="${esc(closeLabel)}"></button>`);
  }
  return controls.join('');
}

function paneFrameControlsGroupHtml(session, options = {}) {
  const groupClass = options.groupClass ? ` ${options.groupClass}` : '';
  return `<div class="tabs pane-frame-controls${groupClass}" role="tablist">${paneFrameControlsHtml(session, options)}</div>`;
}

function panelControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => disabled ? ` type="button" disabled title="${esc(t('tab.unavailableFor', {name: unavailableLabel}))}" aria-label="${esc(label)}"` : '';
  const readonlyAttrs = label => ` type="button" disabled title="${esc(t('tab.adminRequiredFor', {name: label}))}" aria-label="${esc(label)}"`;
  const tabAttrs = (name, label = '') => {
    if (disabled) return disabledAttrs(label || name);
    if (readOnlyMode && name === 'summary') return readonlyAttrs('YO!summary');
    const labelAttrs = label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : '';
    return ` type="button" data-tab="${esc(session)}" data-tab-name="${name}"${labelAttrs}`;
  };
  const info = transcriptMeta.sessions?.[session];
  const terminalTitle = terminalTabTitle(session, info);
  const terminalAttrs = disabled ? disabledAttrs(terminalTitle) : `${tabAttrs('terminal')} title="${esc(terminalTitle)}" aria-label="${esc(terminalTitle)}"`;
  const terminalLabel = disabled ? t('tab.terminal.short') : terminalTabLabel(session, info);
  const isFiles = isFileExplorerItem(session);
  // Term is pressed ONLY when the terminal view is the active one — computed from the live view, not
  // hardcoded, so a panel re-render (Dockview header refresh) doesn't re-press it after the user
  // switched to transcript / YO!summary / events. activateTab also toggles it on click.
  const terminalActive = panelActiveTabName(session) === 'terminal';
  const terminalButtonHtml = `<button class="tab${terminalActive ? ' active' : ''} terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>`;
  const frameHtml = isFiles
    ? paneFrameControlsHtml(session, {
      disabled,
      actions: false,
      minimize: false,
      expand: false,
      close: true,
      closeTitle: t('finder.close', {name: fileExplorerLabel()}),
      closeLabel: t('finder.close', {name: fileExplorerLabel()}),
    })
    : paneFrameControlsHtml(session, {disabled, actions: isTmuxSession(session), details: true, close: false});
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          ${terminalButtonHtml}
          ${frameHtml}
        </div>`;
}

function virtualPanelControlsHtml(session, options = {}) {
  return `<div class="tabs virtual-panel-controls" role="tablist">
          ${paneFrameControlsHtml(session, {actions: false, close: false, ...options})}
        </div>`;
}

function panelActiveTabName(session) {
  const activePane = document.getElementById(panelDomId(session))?.querySelector('.tab-pane.active');
  const id = activePane?.id || '';
  if (id === `transcript-pane-${session}`) return 'transcript';
  if (id === `summary-pane-${session}`) return 'summary';
  if (id === `events-pane-${session}`) return 'events';
  return 'terminal';
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = panelDomId(session);
  panel.innerHTML = `
      <div class="panel-head">
        ${panelControlsHtml(session)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-popover-zone panel-popover-zone">
          <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(sessionState(session, transcriptMeta.sessions?.[session]))}</div>
          <div id="meta-${session}" class="pane-info-bar-meta meta">${esc(t('pane.findingBranch'))}</div>
          ${sessionPopoverHtml(session, transcriptMeta.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMeta.sessions?.[session]))}
        </div>
        ${isTmuxSession(session) ? tmuxWindowBarHtml(session, transcriptMeta.sessions?.[session], {infoBar: true}) : ''}
      </div>
      <div id="terminal-pane-${session}" class="tab-pane active panel-overlay-root">
        <div id="term-${session}" class="terminal"></div>
        <div id="panel-toasts-${session}" class="panel-toast-stack">
          <div id="upload-${session}" class="upload-result toast" hidden></div>
        </div>
      </div>
      <div id="transcript-pane-${session}" class="tab-pane">
        <div class="transcript">
          <div class="transcript-head">${esc(t('tab.transcript'))}</div>
          <div id="transcript-path-${session}" class="transcript-path-row">${esc(t('pane.findingTranscript'))}</div>
          <div id="transcript-${session}" class="transcript-preview">${esc(t('pane.findingTranscript'))}</div>
        </div>
      </div>
      <div id="summary-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">${esc(t('menu.tmux.aiTranscript', {session: sessionLabel(session)}))}</div>
          <div id="summary-context-${session}" class="summary-context">${esc(t('summary.loadingContext'))}</div>
          <div id="summary-${session}" class="summary-preview markdown-body">${esc(t('summary.emptyPrompt'))}</div>
        </div>
      </div>
      <div id="events-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">${esc(t('events.title'))}</div>
          <div id="events-${session}" class="event-list">${esc(t('events.loading'))}</div>
        </div>
      </div>`;
  bindPanelShell(panel, session);
  bindPanelControls(panel, session);
  return panel;
}

function setMetadataRefreshButtonLoading(button, loading, idleLabel, idleTitle) {
  if (!button) return;
  button.classList.toggle('loading', loading);
  button.disabled = loading;
  button.setAttribute('aria-busy', loading ? 'true' : 'false');
  button.textContent = loading ? t('info.loadingShort') : idleLabel;
  button.title = loading ? t('info.loadingRepo') : idleTitle;
  button.setAttribute('aria-label', loading ? t('info.loadingRepo') : idleTitle);
}

function syncTranscriptMetaLoadingUi() {
  document.getElementById(panelDomId(infoItemId))?.querySelectorAll('[data-info-refresh]').forEach(button => {
    setMetadataRefreshButtonLoading(button, transcriptMetaLoading, t('meta.refresh'), t('meta.refresh'));
  });
  const metaRefreshButton = refreshMeta;
  if (metaRefreshButton) {
    metaRefreshButton.classList.toggle('loading', transcriptMetaLoading);
    metaRefreshButton.disabled = transcriptMetaLoading;
    metaRefreshButton.setAttribute('aria-busy', transcriptMetaLoading ? 'true' : 'false');
    if (transcriptMetaLoading) {
      metaRefreshButton.title = t('info.loadingRepo');
      metaRefreshButton.setAttribute('aria-label', t('info.loadingRepo'));
    } else {
      refreshMetaButtonTitle();
      metaRefreshButton.setAttribute('aria-label', t('meta.refreshAria'));
    }
  }
  document.getElementById(panelDomId(infoItemId))?.classList.toggle('metadata-loading', transcriptMetaLoading);
}

function infoMetadataLoadingHtml() {
  return `<div class="info-empty info-loading" role="status" aria-live="polite">
    <span class="info-loading-spinner" aria-hidden="true"></span>
    <span>${esc(t('info.loadingRepo'))}</span>
  </div>`;
}

function backgroundServerPortText(record) {
  const port = Number(record?.port);
  return Number.isFinite(port) && port > 0 ? `:${Math.trunc(port)}` : '';
}

function backgroundServerLabel(record, fallback = '') {
  const source = record && typeof record === 'object' ? record : {};
  const host = String(source.hostname || fallback || serverHostname || '').trim();
  const endpoint = host ? `${host}${backgroundServerPortText(source)}` : '';
  const root = compactHomePath(source.project_root || '');
  const pid = Number(source.pid);
  return [
    endpoint,
    root,
    Number.isFinite(pid) && pid > 0 ? `pid ${Math.trunc(pid)}` : '',
  ].filter(Boolean).join(' · ') || 'this server';
}

function backgroundOwnerRoleSummary(roleName, payload = backgroundOwnerStatusPayload, options = {}) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const roles = data.roles && typeof data.roles === 'object' ? data.roles : {};
  const role = roles[roleName] && typeof roles[roleName] === 'object' ? roles[roleName] : {};
  const ownsRole = role.owner === true;
  const current = data.generation && typeof data.generation === 'object' ? data.generation : {};
  const owner = data.current_owner && typeof data.current_owner === 'object' ? data.current_owner : null;
  return {
    ownsRole,
    mode: ownsRole ? (options.ownerMode || 'leader') : (options.followerMode || 'follower'),
    state: ownsRole ? 'leader' : 'follower',
    currentLabel: backgroundServerLabel(current),
    ownerLabel: owner ? backgroundServerLabel(owner) : '',
    status: String(role.status || data.status || ''),
    error: String(data.last_error || ''),
  };
}

function backgroundOwnerSearchIndexSummary(payload = backgroundOwnerStatusPayload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const searchIndex = data.search_index && typeof data.search_index === 'object' ? data.search_index : {};
  const summary = backgroundOwnerRoleSummary('search-index', payload);
  const ownsIndex = searchIndex.owner === true || summary.ownsRole === true;
  const current = searchIndex.current_server && typeof searchIndex.current_server === 'object' ? searchIndex.current_server : data.generation;
  const owner = searchIndex.owner_server && typeof searchIndex.owner_server === 'object' ? searchIndex.owner_server : data.current_owner;
  return {
    ...summary,
    ownsIndex,
    ownsRole: ownsIndex,
    mode: ownsIndex ? 'leader' : 'follower',
    state: ownsIndex ? 'leader' : 'follower',
    currentLabel: backgroundServerLabel(current),
    ownerLabel: owner && typeof owner === 'object' ? backgroundServerLabel(owner) : '',
    status: String(searchIndex.status || summary.status || data.status || ''),
  };
}

function backgroundOwnerStatsSummary(payload = backgroundOwnerStatusPayload) {
  return backgroundOwnerRoleSummary('stats-sampler', payload);
}

function backgroundOwnerSessionFilesSummary(payload = backgroundOwnerStatusPayload) {
  return backgroundOwnerRoleSummary('session-files', payload);
}

function applyBackgroundOwnerStatusPayload(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  backgroundOwnerStatusPayload = payload;
  backgroundOwnerStatusLoaded = true;
  backgroundOwnerStatusError = '';
  backgroundOwnerStatusLoading = false;
  if (options.render !== false) renderInfoPanel();
  if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
  return true;
}

async function refreshBackgroundOwnerStatus(options = {}) {
  if (shareViewMode) return false;
  if (backgroundOwnerStatusRefreshPromise && options.force !== true) return backgroundOwnerStatusRefreshPromise;
  backgroundOwnerStatusLoading = !backgroundOwnerStatusPayload;
  backgroundOwnerStatusError = '';
  if (options.render !== false) renderInfoPanel();
  if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
  backgroundOwnerStatusRefreshPromise = (async () => {
    try {
      const payload = await apiFetchJson('/api/background/status', {cache: 'no-store'});
      return applyBackgroundOwnerStatusPayload(payload, options);
    } catch (error) {
      backgroundOwnerStatusError = String(error?.payload?.error || error?.message || error);
      backgroundOwnerStatusLoading = false;
      if (options.render !== false) renderInfoPanel();
      if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
      return false;
    } finally {
      backgroundOwnerStatusRefreshPromise = null;
    }
  })();
  return backgroundOwnerStatusRefreshPromise;
}

// client-side mirror of the backend parse_pull_request_ref — normalize a watched-PR entry
// ("owner/repo#N", "owner/repo/N", or a github.com PR URL) to the canonical "owner/repo#N", else ''.
// Used to dedupe and to match a stored entry (which may be a URL) against a PR's canonical ref.
function normalizeWatchedPrRef(entry) {
  const text = String(entry || '').trim();
  if (!text) return '';
  const seg = '[A-Za-z0-9._-]+';
  if (/github\.com/i.test(text) && /:\/\//.test(text)) {
    const match = text.match(new RegExp(`github\\.com/(${seg})/(${seg})/(?:pull|pulls)/(\\d+)`, 'i'));
    if (match) return `${match[1]}/${match[2]}#${Number(match[3])}`;
    return '';
  }
  const short = text.match(new RegExp(`^(${seg})/(${seg})(?:#|/(?:pull/)?)(\\d+)$`));
  if (short && Number(short[3]) > 0) return `${short[1]}/${short[2]}#${Number(short[3])}`;
  return '';
}

async function refreshWatchedPrs() {
  try {
    const data = await apiFetchJson('/api/watched-prs');
    applyWatchedPrsPayload(data);
  } catch (_error) {}
}

function applyWatchedPrsPayload(data) {
  if (!data || typeof data !== 'object') return false;
  watchedPrsData = {
    watched_prs: Array.isArray(data.watched_prs) ? data.watched_prs : [],
    truncated: Number(data.truncated) || 0,
    invalid: Array.isArray(data.invalid) ? data.invalid : [],
  };
  notifyWatchedPrTransitions(watchedPrsData.watched_prs);
  return true;
}

function yoagentChatScrollOwner(node = document.getElementById('yoagent-content')) {
  return node?.querySelector?.('.yoagent-chat-history') || node || null;
}

function scrollYoagentChatToBottom(node = document.getElementById('yoagent-content')) {
  const owner = yoagentChatScrollOwner(node);
  if (owner) owner.scrollTop = owner.scrollHeight;
  yoagentScrollbackLocked = false;
}

function yoagentChatHistoryIsNearBottom(owner, threshold = 48) {
  if (!owner) return true;
  return owner.scrollHeight - owner.clientHeight - owner.scrollTop <= threshold;
}

function yoagentChatScrollState(node = document.getElementById('yoagent-content')) {
  const owner = yoagentChatScrollOwner(node);
  return {
    nearBottom: yoagentChatHistoryIsNearBottom(owner),
    ownerTop: owner ? owner.scrollTop : 0,
  };
}

function restoreYoagentChatScrollState(node, state) {
  if (!node || !state) return;
  const owner = yoagentChatScrollOwner(node);
  if (owner) owner.scrollTop = state.ownerTop || 0;
  yoagentScrollbackLocked = state.nearBottom === false;
}

function installYoagentChatScrollTracker(node = document.getElementById('yoagent-content')) {
  const history = yoagentChatScrollOwner(node);
  if (!history || history.dataset.yoagentScrollTracker === 'true') return;
  history.dataset.yoagentScrollTracker = 'true';
  history.addEventListener('scroll', () => {
    yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom(history);
  }, {passive: true});
  const form = node?.querySelector?.('[data-yoagent-chat-form]');
  if (!form || form.dataset.yoagentWheelForward === 'true') return;
  form.dataset.yoagentWheelForward = 'true';
  form.addEventListener('wheel', event => {
    const maxTop = history.scrollHeight - history.clientHeight;
    const delta = Number(event.deltaY || 0);
    if (maxTop <= 0 || !delta) return;
    const nextTop = Math.max(0, Math.min(maxTop, history.scrollTop + delta));
    if (nextTop === history.scrollTop) return;
    event.preventDefault();
    history.scrollTop = nextTop;
    yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom(history);
  }, {passive: false});
}

function yoagentOpenMessageDetailsState(node = document.getElementById('yoagent-content')) {
  const openKeys = new Set();
  (node?.querySelectorAll?.('.yoagent-message-details[open][data-yoagent-message-details-key]') || []).forEach(details => {
    const key = details.dataset?.yoagentMessageDetailsKey || '';
    if (key) openKeys.add(key);
  });
  return openKeys;
}

function restoreYoagentOpenMessageDetailsState(node, openKeys) {
  if (!node || !openKeys?.size) return;
  (node.querySelectorAll?.('.yoagent-message-details[data-yoagent-message-details-key]') || []).forEach(details => {
    const key = details.dataset?.yoagentMessageDetailsKey || '';
    if (key && openKeys.has(key)) details.open = true;
  });
}

function yoagentShouldScrollBottom(options, scrollState) {
  if (options.scrollBottom === true) return true;
  if (options.scrollBottom === false) return false;
  if (yoagentScrollbackLocked) return false;
  return scrollState?.nearBottom !== false;
}

function focusYoagentChatInput(node = document.getElementById('yoagent-content')) {
  const input = node?.querySelector?.('[data-yoagent-chat-input]');
  if (!input || input.disabled) return;
  input.focus({preventScroll: true});
  const end = input.value.length;
  try { input.setSelectionRange(end, end); } catch (_) {}
}

function yoagentChatInputIsFocused(node = document.getElementById('yoagent-content')) {
  const input = node?.querySelector?.('[data-yoagent-chat-input]');
  return Boolean(input && document.activeElement === input);
}

function restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd) {
  if (!inputFocused) return false;
  const nextInput = node?.querySelector?.('[data-yoagent-chat-input]');
  if (!nextInput || nextInput.disabled) return false;
  nextInput.focus({preventScroll: true});
  if (selectionStart !== null && selectionEnd !== null) {
    try { nextInput.setSelectionRange(selectionStart, selectionEnd); } catch (_) {}
  }
  return true;
}

function refreshYoagentSummaryRegions(node = document.getElementById('yoagent-content')) {
  if (!node) return false;
  const chat = node.querySelector('.yoagent-chat');
  if (!chat) return false;
  const openDetails = yoagentOpenMessageDetailsState(node);
  chat.outerHTML = yoagentChatHtml();
  renderYoagentMessageMarkdown(node);
  restoreYoagentOpenMessageDetailsState(node, openDetails);
  installYoagentChatScrollTracker(node);
  return true;
}

function yoagentBusyUiIsMounted(node = document.getElementById('yoagent-content')) {
  return Boolean(yoagentBusy && node?.querySelector?.('.yoagent-chat-status'));
}

// Downgrade block-level headings (#/##/### …) to inline bold so an embedded agent heading renders as
// emphasis inside a compact card instead of a giant h1/h2 that balloons its height. Inline emphasis,
// code, lists, and links are left intact for marked.js to render.
// the LLM backends emit "loose" markdown (blank lines between list items, double blank
// lines between sections) which marked.js renders with big gaps. Tighten ONLY the yoagent inputs
// (not the shared file-editor preview): collapse 2+ blank lines to one, and drop blank lines between
// adjacent list items so a loose list renders as tightly as a tight one.
function yoagentTightMarkdown(text) {
  return String(text || '')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^([ \t]*(?:[-*+]|\d+\.)[ \t].*)\n(?:[ \t]*\n)+(?=[ \t]*(?:[-*+]|\d+\.)[ \t])/gm, '$1\n');
}

function yoagentInlineMarkdown(text) {
  const downgraded = String(text || '').replace(/^[ \t]*#{1,6}[ \t]+(.*?)[ \t]*#*$/gm, (match, title) => (title ? `**${title}**` : ''));
  return yoagentTightMarkdown(downgraded);
}

function yoagentSessionFromHref(href) {
  try {
    const url = new URL(String(href || ''), window.location.href);
    return url.searchParams.get('yoagent-session') || '';
  } catch (_) {
    return '';
  }
}

function handleYoagentSessionLinkClick(event) {
  const anchor = event.target?.closest?.('a[href]');
  if (!anchor) return;
  const session = yoagentSessionFromHref(anchor.getAttribute('href') || '');
  if (!session) return;
  event.preventDefault();
  selectSession(session, {userInitiated: true});
}

function linkYoagentSessionCodeReferences(container) {
  if (!container) return;
  (container.querySelectorAll?.('code') || []).forEach(code => {
    if (code.closest('a')) return;
    const session = (code.textContent || '').trim();
    if (!session || !sessions.includes(session)) return;
    const previousText = code.previousSibling?.nodeType === Node.TEXT_NODE ? code.previousSibling.textContent || '' : '';
    if (!/(^|\b)(tmux\s+)?session\s*$/i.test(previousText)) return;
    const link = document.createElement('a');
    link.href = `?yoagent-session=${encodeURIComponent(session)}`;
    link.className = 'yoagent-session-link';
    link.title = `Open tmux session ${session}`;
    code.replaceWith(link);
    link.appendChild(code);
  });
}

function installYoagentSessionLinks(container) {
  if (!container) return;
  linkYoagentSessionCodeReferences(container);
  if (container.dataset.yoagentSessionLinksBound !== 'true') {
    container.dataset.yoagentSessionLinksBound = 'true';
    container.addEventListener('click', handleYoagentSessionLinkClick);
  }
}

function renderYoagentMessageMarkdown(node = document.getElementById('yoagent-content')) {
  // Render assistant chat replies through the Markdown pipeline so bold titles, code, lists, and links
  // display formatted. Without marked.js the escaped-text fallback stays.
  if (!node || typeof window.marked === 'undefined') return;
  (node.querySelectorAll?.('.yoagent-global [data-yoagent-global-markdown]') || []).forEach(body => {
    renderMarkdownPreviewInto(body, yoagentTightMarkdown(body.textContent || ''));
    installYoagentSessionLinks(body);
    body.removeAttribute('data-yoagent-global-markdown');
  });
  (node.querySelectorAll?.('.yoagent-message.assistant [data-yoagent-markdown]') || []).forEach(body => {
    renderMarkdownPreviewInto(body, yoagentTightMarkdown(body.textContent || ''));
    installYoagentSessionLinks(body);
    body.removeAttribute('data-yoagent-markdown');
  });
}

function renderYoagentPanel(options = {}) {
  const node = document.getElementById('yoagent-content');
  if (!node) return;
  const scrollState = yoagentChatScrollState(node);
  const openDetails = yoagentOpenMessageDetailsState(node);
  const shouldScrollBottom = yoagentShouldScrollBottom(options, scrollState);
  const input = node.querySelector('[data-yoagent-chat-input]');
  const inputFocused = input && document.activeElement === input;
  const selectionStart = inputFocused ? input.selectionStart : null;
  const selectionEnd = inputFocused ? input.selectionEnd : null;
  if (input && options.preserveDraft !== false) yoagentDraft = input.value || '';
  if (yoagentBusyUiIsMounted(node) && options.allowBusyRebuild !== true) {
    if (refreshYoagentSummaryRegions(node)) {
      if (shouldScrollBottom) scrollYoagentChatToBottom(node);
      else restoreYoagentChatScrollState(node, scrollState);
      restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    }
    return;
  }
  if (options.summaryOnly && refreshYoagentSummaryRegions(node)) {
    if (shouldScrollBottom) scrollYoagentChatToBottom(node);
    else restoreYoagentChatScrollState(node, scrollState);
    restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    return;
  }
  node.innerHTML = yoagentChatHtml();
  renderYoagentMessageMarkdown(node);
  restoreYoagentOpenMessageDetailsState(node, openDetails);
  installYoagentChatScrollTracker(node);
  if (shouldScrollBottom) {
    requestAnimationFrame(() => scrollYoagentChatToBottom(node));
    setTimeout(() => scrollYoagentChatToBottom(node), 0);
  } else {
    restoreYoagentChatScrollState(node, scrollState);
  }
  if (options.focusInput) {
    requestAnimationFrame(() => focusYoagentChatInput(node));
    return;
  }
  if (!inputFocused) return;
  restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
}

function infoBranchRows() {
  if (shareViewMode && Array.isArray(shareInfoBranchRowsOverride)) return shareInfoBranchRowsOverride.slice();
  return rawInfoBranchRows();
}

const infoGroupingStorageKey = 'yolomux.info.grouping.v1';
const infoLegacyGroupingStorageKey = 'yolomux.info2.grouping.v1';
const infoSortStorageKey = 'yolomux.info.sort.v1';
const infoLegacySortStorageKey = 'yolomux.info2.sort.v1';
const infoDefaultGrouping = Object.freeze(['tab', 'path']);
const infoDefaultSort = Object.freeze({key: 'date', dir: 'desc'});
const infoSearchMaxLength = 240;
const infoSortDefs = Object.freeze([
  {key: 'name', dir: 'asc', value: 'name:asc', label: 'A-Z'},
  {key: 'name', dir: 'desc', value: 'name:desc', label: 'Z-A'},
  {key: 'date', dir: 'desc', value: 'date:desc', label: 'recent'},
  {key: 'date', dir: 'asc', value: 'date:asc', label: 'oldest'},
]);
const infoDimensionDefs = Object.freeze([
  {key: 'tab', label: 'Tab'},
  {key: 'tmux-window', label: 'tmux sub-window'},
  {key: 'ai', label: 'AI'},
  {key: 'path', label: 'Path'},
  {key: 'branch', label: 'Branch'},
  {key: 'linear', label: 'Linear'},
  {key: 'pr', label: 'PR'},
]);
const infoPresetDefs = Object.freeze([
  {key: 'tab-tmux-window', label: 'Tab > tmux sub-window', title: 'Tab, then tmux sub-window', grouping: ['tab', 'tmux-window']},
  {key: 'tab-path', label: 'Tab > Path', title: 'Tab, then path', grouping: ['tab', 'path']},
  {key: 'path-branch', label: 'Path > Branch', title: 'Path, then branch', grouping: ['path', 'branch']},
  {key: 'linear-pr', label: 'Linear > PR', title: 'Linear, then PR', grouping: ['linear', 'pr']},
  {key: 'pr-branch', label: 'PR > Branch', title: 'PR, then branch', grouping: ['pr', 'branch']},
]);
let infoGrouping = readStoredInfoGrouping();
let infoSort = readStoredInfoSort();
let infoSearch = '';
const infoCollapsedGroupKeys = new Set();

function infoGroupDimensions() {
  return infoDimensionDefs.map(dimension => ({...dimension}));
}

function infoGroupDimensionAllowedAtLevel(key, level, grouping = []) {
  const dimension = String(key || '').trim();
  const index = Number(level);
  if (!dimension || !Number.isInteger(index) || index < 0 || index > 3) return false;
  if (dimension === 'ai' && index === 0) return false;
  if (dimension === 'tmux-window') {
    const parent = Array.isArray(grouping) ? String(grouping[0] || '') : '';
    return index === 1 && parent === 'tab';
  }
  return true;
}

function infoGroupDimensionsForLevel(level = 0, grouping = infoGrouping) {
  const index = Number(level);
  const normalizedIndex = Number.isInteger(index) ? Math.max(0, Math.min(3, index)) : 0;
  const activeGrouping = Array.isArray(grouping) ? grouping.slice() : normalizeInfoGrouping(grouping);
  return infoDimensionDefs
    .filter(dimension => infoGroupDimensionAllowedAtLevel(dimension.key, normalizedIndex, activeGrouping))
    .map(dimension => ({...dimension}));
}

function infoSortFields() {
  return infoSortDefs.map(def => ({...def}));
}

function infoGroupingPresets() {
  return infoPresetDefs.map(preset => ({...preset, grouping: preset.grouping.slice()}));
}

function normalizeInfoGrouping(value, options = {}) {
  const valid = new Set(infoDimensionDefs.map(dimension => dimension.key));
  const input = Array.isArray(value) ? value : String(value || '').split(/[,\s|>]+/);
  const candidates = [];
  for (const item of input) {
    const key = String(item || '').trim();
    if (!key || !valid.has(key) || candidates.includes(key)) continue;
    candidates.push(key);
  }
  const migrated = options.migrateLegacyPresets ? normalizeInfoLegacyPresetGrouping(candidates) : candidates;
  const result = [];
  for (const key of Array.isArray(migrated) ? migrated : []) {
    if (!infoGroupDimensionAllowedAtLevel(key, result.length, result)) continue;
    result.push(key);
    if (result.length >= 4) break;
  }
  return (result.length ? result : infoDefaultGrouping).slice();
}

function normalizeInfoLegacyPresetGrouping(grouping) {
  const key = (Array.isArray(grouping) ? grouping : []).join('|');
  if (key === 'tab|ai|path|branch') return ['tab', 'path'];
  if (key === 'path|branch|tab|ai') return ['path', 'branch'];
  if (key === 'branch|path|tab|ai') return ['path', 'branch'];
  if (key === 'ai|tab|path|branch') return ['linear', 'pr'];
  return Array.isArray(grouping) ? grouping.slice() : infoDefaultGrouping.slice();
}

function readStoredInfoGrouping() {
  const raw = storageGet(infoGroupingStorageKey, '') || storageGet(infoLegacyGroupingStorageKey, '');
  if (!raw) return infoDefaultGrouping.slice();
  try {
    const parsed = JSON.parse(raw);
    return normalizeInfoGrouping(parsed, {migrateLegacyPresets: true});
  } catch (_) {
    return normalizeInfoGrouping(raw, {migrateLegacyPresets: true});
  }
}

function writeInfoGrouping(value) {
  infoGrouping = normalizeInfoGrouping(value);
  storageSet(infoGroupingStorageKey, JSON.stringify(infoGrouping));
  return infoGrouping.slice();
}

function currentInfoGrouping() {
  return infoGrouping.slice();
}

function normalizeInfoSort(value) {
  let raw = value;
  if (typeof raw === 'string') {
    try {
      raw = raw.trim().startsWith('{') ? JSON.parse(raw) : raw;
    } catch (_) {
      raw = value;
    }
  }
  if (typeof raw === 'string') {
    const text = raw.trim();
    if (['date-desc', 'date:desc', 'new', 'recent'].includes(text)) raw = {key: 'date', dir: 'desc'};
    else if (['date-asc', 'date:asc', 'old', 'oldest'].includes(text)) raw = {key: 'date', dir: 'asc'};
    else if (['name-asc', 'name:asc', 'az', 'a-z'].includes(text)) raw = {key: 'name', dir: 'asc'};
    else if (['name-desc', 'name:desc', 'za', 'z-a'].includes(text)) raw = {key: 'name', dir: 'desc'};
    else {
      const [key, dir] = text.split(/[:|,]/);
      raw = {key, dir};
    }
  }
  const rawKey = String(raw?.key || '').trim();
  const key = rawKey === 'date' ? 'date' : (rawKey ? 'name' : infoDefaultSort.key);
  const dir = String(raw?.dir || '').trim() === 'asc' ? 'asc' : 'desc';
  return {key, dir};
}

function readStoredInfoSort() {
  return normalizeInfoSort(storageGet(infoSortStorageKey, '') || storageGet(infoLegacySortStorageKey, '') || JSON.stringify(infoDefaultSort));
}

function writeInfoSort(value) {
  infoSort = normalizeInfoSort(value);
  storageSet(infoSortStorageKey, JSON.stringify(infoSort));
  return {...infoSort};
}

function currentInfoSort() {
  return {...infoSort};
}

function normalizeInfoSearch(value) {
  return String(value || '').slice(0, infoSearchMaxLength);
}

function currentInfoSearch() {
  return infoSearch;
}

function infoTreeGroupIdentity(group = {}) {
  const key = group.key ?? group.label ?? group.title ?? '';
  return [String(group.dimension || ''), String(key)];
}

function infoTreeGroupCollapseKey(group = {}, ancestorGroupIdentities = []) {
  const identities = Array.isArray(ancestorGroupIdentities) ? ancestorGroupIdentities.slice() : [];
  identities.push(infoTreeGroupIdentity(group));
  return encodeURIComponent(JSON.stringify(identities));
}

function setInfoTreeGroupCollapsed(key, collapsed) {
  const groupKey = String(key || '');
  if (!groupKey) return false;
  const wasCollapsed = infoCollapsedGroupKeys.has(groupKey);
  if (collapsed) infoCollapsedGroupKeys.add(groupKey);
  else infoCollapsedGroupKeys.delete(groupKey);
  return infoCollapsedGroupKeys.has(groupKey) !== wasCollapsed;
}

function pruneInfoTreeCollapsedGroups(activeKeys) {
  if (!(activeKeys instanceof Set) || !infoCollapsedGroupKeys.size) return;
  [...infoCollapsedGroupKeys].forEach(key => {
    if (!activeKeys.has(key)) infoCollapsedGroupKeys.delete(key);
  });
}

function refreshInfoGroupingControls() {
  document.querySelectorAll('.info-tree-actions-bar').forEach(bar => {
    const actions = bar.querySelector('.info-subtab-actions');
    bar.innerHTML = `${typeof infoGroupingControlsHtml === 'function' ? infoGroupingControlsHtml() : ''}${actions ? actions.outerHTML : ''}`;
  });
}

function setInfoGrouping(value) {
  const previous = infoGrouping.join(',');
  writeInfoGrouping(value);
  refreshInfoGroupingControls();
  renderInfoPanel();
  if (infoGrouping.join(',') !== previous) scheduleShareUiStatePublish();
}

function setInfoSort(value, options = {}) {
  const previous = `${infoSort.key}:${infoSort.dir}`;
  writeInfoSort(value);
  refreshInfoGroupingControls();
  renderInfoPanel();
  if (`${infoSort.key}:${infoSort.dir}` !== previous && options.publish !== false) scheduleShareUiStatePublish();
  return {...infoSort};
}

function setInfoSortMode(value, options = {}) {
  return setInfoSort(value, options);
}

function setInfoSearch(value, options = {}) {
  const previous = infoSearch;
  infoSearch = normalizeInfoSearch(value);
  if (options.refreshControls === true) refreshInfoGroupingControls();
  if (options.render !== false) renderInfoPanel();
  if (infoSearch !== previous && options.publish !== false) scheduleShareUiStatePublish();
  return infoSearch;
}

function setInfoGroupingPreset(key) {
  const preset = infoPresetDefs.find(item => item.key === key);
  if (preset) setInfoGrouping(preset.grouping);
}

function setInfoGroupingLevel(level, value) {
  const index = Number(level);
  if (!Number.isInteger(index) || index < 0 || index > 3) return;
  const next = infoGrouping.slice();
  const key = String(value || '').trim();
  next[index] = key;
  setInfoGrouping(next);
}

function infoRecordAiKind(record = {}) {
  const direct = String(record?.aiAgentKey || record?.aiKind || '').trim();
  if (direct && direct !== '__no_ai__' && direct !== 'no-ai') return direct;
  const label = String(record?.aiAgentLabel || record?.aiLabel || '').trim();
  if (!label || /^no\s+ai$/i.test(label)) return '';
  if (label.includes(':')) return label.split(':').pop().trim();
  return label;
}

function infoRecordAiAgentLabel(record = {}) {
  return infoAgentKindLabel(infoRecordAiKind(record));
}

function infoRecordTmuxWindowIndex(record = {}) {
  return String(record?.aiWindowIndex ?? record?.aiWindow ?? '').trim();
}

function infoRecordTmuxWindowLabel(record = {}) {
  if (!infoRecordHasAi(record)) return 'No tmux sub-window';
  return String(record?.tmuxWindowLabel || record?.aiLabel || '').trim() || 'tmux sub-window';
}

function infoRecordTmuxWindowKey(record = {}) {
  const explicit = String(record?.tmuxWindowKey || '').trim();
  if (explicit) return explicit;
  if (!infoRecordHasAi(record)) return '__no_tmux_window__';
  const index = infoRecordTmuxWindowIndex(record);
  return `${record?.tabSession || record?.tabKey || 'no-tab'}:${index || infoRecordTmuxWindowLabel(record)}:${infoRecordTmuxWindowLabel(record)}`;
}

function infoDimensionValue(record, dimension) {
  const fallback = {key: 'none', label: 'None', title: ''};
  if (!record || !dimension) return fallback;
  if (dimension === 'tab') return {key: record.tabKey, label: record.tabLabel, title: record.tabTitle, sortValue: infoRecordNumericSortValue(record, 'tab')};
  if (dimension === 'tmux-window') return {key: infoRecordTmuxWindowKey(record), label: infoRecordTmuxWindowLabel(record), title: record.tmuxWindowTitle || record.aiTitle || infoRecordTmuxWindowLabel(record), sortValue: infoRecordNumericSortValue(record, 'tmux-window')};
  if (dimension === 'ai') return {key: infoRecordAiKind(record) || '__no_ai__', label: infoRecordAiAgentLabel(record), title: record.aiAgentTitle || infoRecordAiAgentLabel(record)};
  if (dimension === 'path') return {key: record.pathKey, label: record.pathTitle || record.pathLabel, title: record.pathTitle};
  if (dimension === 'branch') return {key: record.branchKey, label: record.branchLabel, title: record.branchTitle};
  if (dimension === 'pr') return {key: record.prKey, label: record.prTitle || record.prLabel, title: record.prTitle, sortValue: infoRecordNumericSortValue(record, 'pr')};
  if (dimension === 'linear') return {key: record.linearKey, label: record.linearTitle || record.linearLabel, title: record.linearTitle, sortValue: infoRecordNumericSortValue(record, 'linear')};
  return fallback;
}

function infoFirstIntegerFromValue(value) {
  const match = String(value || '').match(/\d+/);
  return match ? Number(match[0]) : NaN;
}

function infoPrNumberFromValue(value) {
  const direct = Number(value);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const text = String(value || '');
  const hashMatch = text.match(/#\s*(\d+)/);
  if (hashMatch) return Number(hashMatch[1]);
  const urlMatch = text.match(/\/pull\/(\d+)(?:\D|$)/);
  return urlMatch ? Number(urlMatch[1]) : NaN;
}

function infoRowPrNumber(row = {}) {
  for (const value of [row.prNumber, row.prLabel, row.prTitle, row.prSort, row.prUrl]) {
    const number = infoPrNumberFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoRelationshipRecords(rows = infoBranchRows()) {
  const records = [];
  const noTab = {session: '', label: 'No tab / no AI', title: 'No tab or AI associated with this branch', kind: '', window: '', tabLabel: 'No tab', aiLabel: 'No AI'};
  for (const row of Array.isArray(rows) ? rows : []) {
    const directTabAgents = Array.isArray(row?.tabAgents) && row.tabAgents.length ? row.tabAgents : [];
    const pathTabAgents = Array.isArray(row?.pathTabAgents) && row.pathTabAgents.length ? row.pathTabAgents : [];
    const tabAgents = directTabAgents.length ? directTabAgents : (pathTabAgents.length ? pathTabAgents : [noTab]);
    for (const agent of tabAgents) {
      const session = String(agent?.session || '');
      const tabLabel = String(agent?.tabLabel || (session && typeof sessionLabel === 'function' ? sessionLabel(session) : session) || 'No tab');
      const aiLabel = String(agent?.aiLabel || infoTabAgentAiLabel(agent));
      const aiKind = String(agent?.kind || '');
      const tmuxWindowIndex = String(agent?.windowIndex ?? agent?.window_index ?? agent?.window ?? '');
      const tmuxWindowKey = tmuxWindowIndex
        ? `${session || 'no-tab'}:${tmuxWindowIndex}:${aiLabel}`
        : '__no_tmux_window__';
      const tmuxWindowLabel = tmuxWindowKey === '__no_tmux_window__' ? 'No tmux sub-window' : aiLabel;
      const path = String(row?.path || '');
      const branch = String(row?.branch || '');
      const linearLabel = Array.isArray(row?.linearItems) && row.linearItems.length
        ? row.linearItems.map(item => item.identifier || item.url || '').filter(Boolean).join(', ')
        : String(row?.linearTitle || '').trim();
      const prKeyLabel = String(row?.prLabel || row?.prSort || row?.prTitle || '').trim();
      const prDisplayLabel = String(row?.prDescriptionTitle || row?.prTitle || row?.prSort || row?.prLabel || '').trim();
      const prNumber = infoRowPrNumber(row);
      const prCompactLabel = Number.isFinite(prNumber) ? `#${prNumber}` : prKeyLabel;
      records.push({
        id: [path, branch, session || 'no-tab', aiKind || 'no-ai', tmuxWindowIndex, prCompactLabel || prKeyLabel, linearLabel].join('\n'),
        tabKey: session || '__no_tab__',
        tabSession: session,
        tabLabel,
        tabTitle: String(agent?.title || tabLabel),
        aiKey: `${agent?.kind || 'no-ai'}:${agent?.window || ''}:${aiLabel}`,
        aiKind,
        aiAgentKey: aiKind || '__no_ai__',
        aiAgentLabel: infoAgentKindLabel(aiKind),
        aiAgentTitle: infoAgentKindLabel(aiKind),
        aiWindow: String(agent?.window || ''),
        aiWindowIndex: String(agent?.windowIndex ?? agent?.window_index ?? agent?.window ?? ''),
        aiState: String(agent?.state || ''),
        aiPane: String(agent?.pane || ''),
        aiPaneTarget: String(agent?.pane_target || ''),
        aiCurrent: agent?.current === true,
        aiWindowActive: agent?.window_active === true,
        aiPid: tmuxWindowProcessPid(agent),
        aiWorkingStoppedTs: Number.isFinite(Number(agent?.working_stopped_ts)) ? Number(agent.working_stopped_ts) : 0,
        aiIdleSince: Number.isFinite(Number(agent?.idle_since)) ? Number(agent.idle_since) : 0,
        aiLastActiveTs: Number.isFinite(Number(agent?.last_active_ts)) ? Number(agent.last_active_ts) : 0,
        aiLabel,
        aiTitle: String(agent?.title || aiLabel),
        tmuxWindowKey,
        tmuxWindowLabel,
        tmuxWindowTitle: String(agent?.title || tmuxWindowLabel),
        pathKey: infoNormalizedPath(path) || '__no_path__',
        pathLabel: String(row?.pathLabel || compactHomePath(path) || 'No path'),
        pathTitle: String(row?.pathTitle || path || 'No path'),
        pathActivityTs: Number.isFinite(row?.pathActivityTs) ? row.pathActivityTs : 0,
        pathActivitySource: String(row?.pathActivitySource || ''),
        branchKey: branch || '__no_branch__',
        branchLabel: branch || 'No branch',
        branchTitle: branch || 'No branch',
        branchHtml: row?.branchHtml || esc(branch || 'No branch'),
        prKey: prCompactLabel || prKeyLabel || '__no_pr__',
        prLabel: prCompactLabel || prKeyLabel || prDisplayLabel || 'No PR',
        prTitle: prDisplayLabel || prCompactLabel || 'No PR',
        prNumber: Number.isFinite(prNumber) ? prNumber : null,
        prUrl: String(row?.prUrl || ''),
        prClass: String(row?.prClass || ''),
        prLifecycleText: String(row?.prLifecycleText || ''),
        prLifecycleClass: String(row?.prLifecycleClass || ''),
        prCiText: String(row?.prCiText || ''),
        prCiClass: String(row?.prCiClass || ''),
        prHtml: infoPrCellHtml(row) || '',
        linearKey: linearLabel || '__no_linear__',
        linearLabel: linearLabel || 'No Linear',
        linearTitle: String(row?.linearTitle || linearLabel || 'No Linear'),
        linearHtml: infoLinearCellHtml(row) || '',
        linearItems: Array.isArray(row?.linearItems) ? row.linearItems.slice(0, 20) : [],
        desc: String(row?.desc || ''),
        updated: String(row?.updatedText || row?.updated || ''),
        updatedTitle: String(row?.updatedTitle || row?.updated || ''),
        updatedTs: Number.isFinite(row?.updatedTs) ? row.updatedTs : 0,
        updatedSource: String(row?.updatedSource || ''),
      });
    }
  }
  return infoSortedRecords(records, infoSort);
}

function infoCompareLabels(left, right, direction = 1) {
  const leftMissing = infoRecordMissingValue(left);
  const rightMissing = infoRecordMissingValue(right);
  if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
  return String(left || '').localeCompare(String(right || ''), undefined, {sensitivity: 'base'}) * direction;
}

function infoCompareNumbers(left, right, direction = 1) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const leftHasNumber = Number.isFinite(leftNumber);
  const rightHasNumber = Number.isFinite(rightNumber);
  if (leftHasNumber && rightHasNumber && leftNumber !== rightNumber) return (leftNumber - rightNumber) * direction;
  if (leftHasNumber !== rightHasNumber) return leftHasNumber ? -1 : 1;
  return 0;
}

function infoRecordPrNumber(record = {}) {
  for (const value of [record.prNumber, record.prLabel, record.prTitle, record.prKey]) {
    const number = infoPrNumberFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoFirstRecordNumber(record = {}, values = []) {
  for (const value of values) {
    const number = infoFirstIntegerFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoRecordLinearNumber(record = {}) {
  const items = Array.isArray(record.linearItems) ? record.linearItems : [];
  for (const item of items) {
    const number = infoFirstRecordNumber(item, [item?.identifier, item?.title, item?.url]);
    if (Number.isFinite(number)) return number;
  }
  return infoFirstRecordNumber(record, [record.linearKey, record.linearLabel, record.linearTitle]);
}

function infoRecordNumericSortValue(record = {}, dimension = '') {
  if (dimension === 'pr') return infoRecordPrNumber(record);
  if (dimension === 'linear') return infoRecordLinearNumber(record);
  if (dimension === 'tab') return infoFirstRecordNumber(record, [record.tabKey, record.tabLabel, record.tabTitle, record.tabSession]);
  if (dimension === 'tmux-window') return infoFirstRecordNumber(record, [infoRecordTmuxWindowIndex(record), infoRecordTmuxWindowLabel(record), record.aiLabel, record.aiKey, record.aiTitle]);
  return NaN;
}

function infoCompareNumberThenLabel(leftNumber, rightNumber, leftLabel, rightLabel, direction = 1) {
  const leftMissing = infoRecordMissingValue(leftLabel);
  const rightMissing = infoRecordMissingValue(rightLabel);
  if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
  const leftHasNumber = Number.isFinite(Number(leftNumber));
  const rightHasNumber = Number.isFinite(Number(rightNumber));
  if (leftHasNumber && rightHasNumber && Number(leftNumber) !== Number(rightNumber)) return (Number(leftNumber) - Number(rightNumber)) * direction;
  if (leftHasNumber !== rightHasNumber) return leftHasNumber ? -1 : 1;
  return infoCompareLabels(leftLabel, rightLabel, direction);
}

function infoCompareRecordNumberThenLabel(left = {}, right = {}, dimension = '', direction = 1) {
  return infoCompareNumberThenLabel(
    infoRecordNumericSortValue(left, dimension),
    infoRecordNumericSortValue(right, dimension),
    infoRecordLabel(left, dimension),
    infoRecordLabel(right, dimension),
    direction,
  );
}

function infoRecordLabel(record = {}, dimension = '') {
  if (dimension === 'tab') return record.tabLabel;
  if (dimension === 'tmux-window') return infoRecordTmuxWindowLabel(record);
  if (dimension === 'ai') return infoRecordAiAgentLabel(record);
  if (dimension === 'linear') return record.linearLabel || record.linearTitle;
  if (dimension === 'pr') return record.prLabel || record.prTitle;
  if (dimension === 'path') return record.pathLabel;
  if (dimension === 'branch') return record.branchLabel;
  return '';
}

function infoSearchText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function infoSearchField(kind, ...values) {
  const text = values.map(infoSearchText).filter(Boolean).join(' ');
  return text ? {kind, text} : null;
}

function infoSearchFields(kind, ...values) {
  const seen = new Set();
  return values
    .map(infoSearchText)
    .filter(Boolean)
    .filter(value => {
      const key = value.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map(text => ({kind, text}));
}

function infoPrSearchText(record = {}) {
  if (String(record?.prKey || '') === '__no_pr__') return '';
  const label = String(record?.prLabel || '').trim();
  const title = String(record?.prTitle || '').trim();
  const numberText = Number.isFinite(record?.prNumber)
    ? `#${record.prNumber}`
    : (!infoRecordMissingValue(label) ? label : '');
  const description = title && numberText && title.startsWith(numberText)
    ? title.slice(numberText.length).trim()
    : title;
  return [numberText, description, record.prLifecycleText, record.prCiText].filter(Boolean).join(' ');
}

function infoLinearSearchFields(record = {}) {
  if (String(record?.linearKey || '') === '__no_linear__') return [];
  const items = Array.isArray(record?.linearItems) ? record.linearItems : [];
  const fields = items
    .map(item => infoSearchField('linear', item?.identifier, item?.title, item?.state))
    .filter(Boolean);
  const label = String(record?.linearLabel || '').trim();
  const title = String(record?.linearTitle || '').trim();
  const description = title && label && title.startsWith(label) ? title.slice(label.length).trim() : title;
  const fallback = infoSearchField('linear', label, description);
  if (fallback) fields.push(fallback);
  return fields;
}

function infoRecordSearchFields(record = {}) {
  return [
    ...(infoRecordHasTab(record) ? infoSearchFields('tab', sessionLabel(record.tabSession), record.tabLabel, record.tabSession) : []),
    ...(infoRecordHasAi(record) ? infoSearchFields('tmux-window', infoRecordTmuxWindowLabel(record)) : []),
    ...(infoRecordHasAi(record) ? infoSearchFields('ai', infoRecordAiAgentLabel(record), infoRecordAiKind(record)) : []),
    !infoRecordMissingValue(record?.pathLabel) && String(record?.pathKey || '') !== '__no_path__'
      ? infoSearchField('path', record.pathTitle || record.pathLabel)
      : null,
    !infoRecordMissingValue(record?.branchLabel) && String(record?.branchKey || '') !== '__no_branch__'
      ? infoSearchField('branch', record.branchTitle || record.branchLabel)
      : null,
    infoSearchField('pr', infoPrSearchText(record)),
    ...infoLinearSearchFields(record),
    !infoRecordMissingValue(record?.updated) ? infoSearchField('updated', record.updated) : null,
  ].filter(Boolean);
}

function infoSearchFieldMatches(field = {}, query = infoSearch) {
  const text = String(query || '').trim();
  return Boolean(text && Number.isFinite(fuzzySearchScore(text, [field.text])));
}

function infoRecordSearchKindMatches(record = {}, kind = '', query = infoSearch) {
  const text = String(query || '').trim();
  if (!text || !kind) return false;
  return infoRecordSearchFields(record).some(field => field.kind === kind && infoSearchFieldMatches(field, text));
}

function infoRecordMatchesSearch(record = {}, query = infoSearch) {
  const text = String(query || '').trim();
  if (!text) return true;
  return infoRecordSearchFields(record).some(field => infoSearchFieldMatches(field, text));
}

function infoFilteredRecords(records = [], query = infoSearch) {
  return (Array.isArray(records) ? records : []).filter(record => infoRecordMatchesSearch(record, query));
}

function infoSearchHighlightHtml(value, query = infoSearch) {
  const text = String(value ?? '');
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return esc(text);
  const indexes = new Set();
  for (const token of tokens) {
    const match = fuzzySubsequenceMatch(token, text);
    if (match) for (const matchIndex of match.indexes) indexes.add(matchIndex);
  }
  if (!indexes.size) return esc(text);
  const chars = Array.from(text);
  const parts = [];
  let index = 0;
  while (index < chars.length) {
    if (!indexes.has(index)) {
      parts.push(esc(chars[index]));
      index += 1;
      continue;
    }
    const start = index;
    while (index < chars.length && indexes.has(index)) index += 1;
    parts.push(`<mark class="info-tree-search-match">${esc(chars.slice(start, index).join(''))}</mark>`);
  }
  return parts.join('');
}

function infoRecordSearchValueHtml(record = {}, kind = '', value = '', query = infoSearch) {
  return infoRecordSearchKindMatches(record, kind, query)
    ? infoSearchHighlightHtml(value, query)
    : esc(value);
}

function infoGroupSearchKindMatches(group = {}, query = infoSearch) {
  const dimension = String(group?.dimension || '');
  if (!dimension) return false;
  return (Array.isArray(group.records) ? group.records : []).some(record => infoRecordSearchKindMatches(record, dimension, query));
}

function infoGroupSearchValueHtml(group = {}, value = '', query = infoSearch) {
  return infoGroupSearchKindMatches(group, query)
    ? infoSearchHighlightHtml(value, query)
    : esc(value);
}

function infoHighlightedLinkHtml(url, label, title = '', className = '', highlight = false) {
  const labelHtml = highlight ? infoSearchHighlightHtml(label) : esc(label);
  if (!url) return `<span>${labelHtml}</span>`;
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const classAttr = className ? ` class="${esc(className)}"` : '';
  return `<a href="${esc(url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr}${classAttr}>${labelHtml}</a>`;
}

function compareInfoRecords(left, right, sort = infoSort, options = {}) {
  const normalizedSort = normalizeInfoSort(sort);
  const direction = normalizedSort.dir === 'desc' ? -1 : 1;
  let result = 0;
  if (normalizedSort.key === 'date') {
    result = infoCompareNumbers(left?.updatedTs, right?.updatedTs, direction);
  } else {
    result = infoCompareLabels(left?.pathLabel, right?.pathLabel, direction)
      || infoCompareLabels(left?.branchLabel, right?.branchLabel, direction)
      || infoCompareRecordNumberThenLabel(left, right, 'tab', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'ai', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'linear', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'pr', direction);
  }
  if (result || options.fallback === false) return result;
  return (right?.updatedTs || 0) - (left?.updatedTs || 0)
    || infoCompareLabels(left?.pathLabel, right?.pathLabel)
    || infoCompareLabels(left?.branchLabel, right?.branchLabel)
    || infoCompareLabels(left?.tabLabel, right?.tabLabel)
    || infoCompareLabels(left?.aiLabel, right?.aiLabel)
    || infoCompareLabels(left?.prLabel, right?.prLabel);
}

function infoSortedRecords(records = [], sort = infoSort) {
  return (Array.isArray(records) ? records : []).slice().sort((left, right) => compareInfoRecords(left, right, sort));
}

function infoGroupRepresentativeRecord(group, sort = infoSort) {
  return infoSortedRecords(group?.records || [], sort)[0] || {};
}

function infoGroupTree(records = infoRelationshipRecords(), grouping = infoGrouping, sort = infoSort) {
  const levels = normalizeInfoGrouping(grouping);
  const build = (items, depth) => {
    const dimension = levels[depth];
    const sortedItems = infoSortedRecords(items, sort);
    if (!dimension) return {type: 'leaf-list', records: sortedItems};
    const groups = new Map();
    for (const record of sortedItems) {
      const value = infoDimensionValue(record, dimension);
      const key = String(value.key || value.label || 'none');
      if (!groups.has(key)) groups.set(key, {type: 'group', dimension, key, label: String(value.label || 'None'), title: String(value.title || value.label || ''), sortValue: value.sortValue, count: 0, records: [], children: []});
      const group = groups.get(key);
      if (!Number.isFinite(Number(group.sortValue)) && Number.isFinite(Number(value.sortValue))) group.sortValue = value.sortValue;
      group.count += 1;
      group.records.push(record);
    }
    const children = [...groups.values()]
      .sort((left, right) => compareInfoGroups(left, right, sort))
      .map(group => {
        const childTree = build(group.records, depth + 1);
        return {...group, children: childTree.children || childTree.records || []};
      });
    return {type: 'tree', dimension, children};
  };
  return build(records, 0);
}

function compareInfoGroups(left, right, sort = infoSort) {
  const normalizedSort = normalizeInfoSort(sort);
  const direction = normalizedSort.dir === 'desc' ? -1 : 1;
  if (normalizedSort.key === 'date') {
    const selectedResult = compareInfoRecords(infoGroupRepresentativeRecord(left, sort), infoGroupRepresentativeRecord(right, sort), sort, {fallback: false});
    if (selectedResult) return selectedResult;
  } else if (left?.dimension === right?.dimension && ['tab', 'tmux-window', 'linear', 'pr'].includes(left?.dimension)) {
    return infoCompareNumberThenLabel(left.sortValue, right.sortValue, left?.label, right?.label, direction)
      || (right?.count || 0) - (left?.count || 0);
  }
  return infoCompareLabels(left?.label, right?.label, normalizedSort.key === 'date' ? 1 : direction)
    || (right?.count || 0) - (left?.count || 0);
}

function infoTreeItemClasses(baseClass, options = {}) {
  return [
    baseClass,
    'info-tree-item',
    options.first ? 'info-tree-item-first' : '',
    options.last ? 'info-tree-item-last' : '',
  ].filter(Boolean).join(' ');
}

function infoRecordMissingValue(value) {
  const text = String(value || '').trim();
  return !text || /^no\s+(?:path|pr|linear|tab|ai|branch|tmux\s+sub-window)$/i.test(text);
}

function infoRecordHasTab(record) {
  return !infoRecordMissingValue(record?.tabLabel) && String(record?.tabKey || '') !== '__no_tab__' && Boolean(record?.tabSession);
}

function infoRecordHasAi(record) {
  return !infoRecordMissingValue(record?.aiLabel)
    && !String(record?.aiKey || '').startsWith('no-ai:')
    && Boolean(record?.tabSession)
    && String(record?.aiWindow || '') !== '';
}

function infoTabIsShown(record = {}) {
  const session = String(record?.tabSession || record?.tabKey || '').trim();
  return Boolean(session && typeof itemIsActivePaneTab === 'function' && itemIsActivePaneTab(session));
}

function infoRecordTabValueHtml(record = {}, options = {}) {
  if (!infoRecordHasTab(record)) return '';
  const label = String(options.label || record?.tabLabel || record?.tabSession || '').trim();
  if (!label) return '';
  const active = infoTabIsShown(record);
  const title = String(options.title || record?.tabTitle || label);
  const attrs = [`data-info-tab-state="${active ? 'active' : 'inactive'}"`];
  if (options.action !== false) attrs.push(`data-info-open-tab="${esc(record.tabSession)}"`);
  const sessionText = sessionLabel(record.tabSession);
  const sessionLabelHtml = infoRecordSearchKindMatches(record, 'tab')
    ? infoSearchHighlightHtml(sessionText)
    : undefined;
  return tmuxPaneTabTokenHtml(record.tabSession, {
    tag: options.action === false ? 'span' : 'button',
    classes: ['info-tree-tab-token', options.action === false ? 'info-tree-tab-token-static' : 'info-tree-tab-token-action'],
    active,
    title,
    attrs,
    sessionLabelHtml,
    leadingHtml: options.leadingHtml,
  });
}

function infoPullRequestLifecycleText(pr) {
  if (!pr?.number) return '';
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (status === 'merged' || pr.merged || pr.merged_at) return t('pr.status.merged');
  if (status === 'draft' || pr.draft) return t('pr.status.draft');
  if (status === 'closed') return t('pr.status.closed');
  return t('pr.status.open');
}

function infoPullRequestLifecycleClass(pr) {
  if (!pr?.number) return '';
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (status === 'merged' || pr.merged || pr.merged_at) return 'pr-status-merged';
  if (status === 'draft' || pr.draft) return 'pr-status-draft';
  if (status === 'closed') return 'pr-status-closed';
  return 'pr-status-open';
}

function infoPullRequestCiText(pr) {
  if (!pr?.number || infoPullRequestLifecycleClass(pr) === 'pr-status-merged') return '';
  const checks = pr.checks && typeof pr.checks === 'object' ? pr.checks : null;
  const checkState = String(checks?.state || checks?.status_label || checks?.conclusion || '').trim();
  const checkSummary = String(checks?.summary || '').trim();
  if (checkState && checkState.toLowerCase() !== 'unknown') {
    const label = checkSummary || checkState;
    return /^ci\b/i.test(label) ? label : `CI ${label}`;
  }
  const status = String(pr?.status_label || '').trim();
  if (!status || /^(open|merged|closed|draft)$/i.test(status)) return '';
  if (!/(ci|fail|pend|pass|error|queued|running|success)/i.test(status)) return '';
  const display = pullRequestStatusDisplay(pr) || status;
  return /^ci\b/i.test(display) ? display : `CI ${display}`;
}

function infoPullRequestCiClass(pr) {
  const checks = pr?.checks && typeof pr.checks === 'object' ? pr.checks : null;
  const state = String(checks?.state || checks?.status_label || checks?.conclusion || pr?.status_label || '').toLowerCase();
  if (['success', 'passing', 'passed', 'green'].includes(state)) return 'pr-status-passing';
  if (['failure', 'failing', 'failed', 'red', 'error', 'cancelled', 'timed_out', 'action_required'].includes(state)) return 'pr-status-failing';
  if (['pending', 'queued', 'in_progress', 'running', 'waiting', 'requested'].includes(state)) return 'pr-status-pending';
  return pullRequestCiStatusClass(pr);
}

function infoStatusBadgeHtml(record, text, className, options = {}) {
  const label = String(text || '').trim();
  if (!label) return '';
  const labelHtml = options.highlight ? infoSearchHighlightHtml(label) : esc(label);
  return pullRequestStatusBadgeHtml(record?.tabSession, label, className, {labelHtml});
}

function infoRecordPrStatusHtml(record) {
  const parts = [];
  const highlight = infoRecordSearchKindMatches(record, 'pr');
  if (record?.prLifecycleText) parts.push(infoStatusBadgeHtml(record, record.prLifecycleText, record.prLifecycleClass, {highlight}));
  if (record?.prCiText) parts.push(infoStatusBadgeHtml(record, record.prCiText, record.prCiClass, {highlight}));
  return parts.filter(Boolean).join(' ');
}

function infoRecordPrDescHtml(record) {
  if (String(record?.prKey || '') === '__no_pr__') return '';
  const text = String(record?.prTitle || record?.prLabel || '').trim();
  if (infoRecordMissingValue(text)) return '';
  const label = String(record?.prLabel || '').trim();
  const numberText = Number.isFinite(record?.prNumber)
    ? `#${record.prNumber}`
    : (!infoRecordMissingValue(label) ? label : '');
  const highlight = infoRecordSearchKindMatches(record, 'pr');
  if (!numberText) return infoHighlightedLinkHtml(record?.prUrl || '', text, record?.prUrl || record?.prTitle || text, record?.prClass || '', highlight);
  const description = text.startsWith(numberText) ? text.slice(numberText.length).trim() : (text === numberText ? '' : text);
  return [
    infoHighlightedLinkHtml(record?.prUrl || '', numberText, record?.prUrl || record?.prTitle || numberText, record?.prClass || '', highlight),
    description ? (highlight ? infoSearchHighlightHtml(description) : esc(description)) : '',
    infoRecordPrStatusHtml(record),
  ]
    .filter(Boolean)
    .join(' ');
}

function infoLinearItemDescHtml(item = {}, options = {}) {
  const identifier = String(item?.identifier || '').trim();
  const title = String(item?.title || '').trim();
  const url = String(item?.url || '').trim();
  if (!identifier && !title) return '';
  const href = url || linearIssueUrl(identifier);
  const highlight = options.highlight === true;
  const identifierHtml = identifier ? infoHighlightedLinkHtml(href, identifier, href || title || identifier, '', highlight) : '';
  const description = title && title !== identifier ? (highlight ? infoSearchHighlightHtml(title) : esc(title)) : '';
  return [identifierHtml, description].filter(Boolean).join(' ');
}

function infoRecordLinearDescHtml(record) {
  if (String(record?.linearKey || '') === '__no_linear__') return '';
  const highlight = infoRecordSearchKindMatches(record, 'linear');
  const items = Array.isArray(record?.linearItems) ? record.linearItems : [];
  const withDescriptions = items
    .map(item => ({
      identifier: String(item?.identifier || '').trim(),
      title: String(item?.title || '').trim(),
      url: String(item?.url || '').trim(),
    }))
    .filter(item => item.identifier || item.title);
  if (withDescriptions.length) {
    return withDescriptions.map(item => infoLinearItemDescHtml(item, {highlight})).filter(Boolean).join(' ');
  }
  const title = String(record?.linearTitle || '').trim();
  const label = String(record?.linearLabel || '').trim();
  if (infoRecordMissingValue(title) && infoRecordMissingValue(label)) return '';
  if (!infoRecordMissingValue(label)) {
    const description = title && title !== label
      ? (title.startsWith(label) ? title.slice(label.length).trim() : title)
      : '';
    const href = linearIssueUrl(label);
    return [
      infoHighlightedLinkHtml(href, label, href || title || label, '', highlight),
      description ? (highlight ? infoSearchHighlightHtml(description) : esc(description)) : '',
    ].filter(Boolean).join(' ');
  }
  return highlight ? infoSearchHighlightHtml(title) : esc(title);
}

function infoFieldLabel(kind) {
  const labels = {
    path: 'info.field.path',
    branch: 'info.field.gitBranch',
    pr: 'info.field.githubPr',
    linear: 'info.field.linear',
    tab: 'info.field.tabTmuxSession',
    ai: 'info.field.tmuxSubWindow',
    'tmux-window': 'info.field.tmuxSubWindow',
    updated: 'info.field.updated',
  };
  return t(labels[kind] || kind);
}

function infoRecordFieldHtml(kind, html, title = '') {
  if (!html) return '';
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const label = infoFieldLabel(kind);
  return `<div class="info-tree-field info-tree-field-${esc(kind)}"${titleAttr}>
      <span class="info-tree-field-label">${esc(label)}:</span>
      <span class="info-tree-field-value">${html}</span>
    </div>`;
}

function infoRecordAgentPayload(record) {
  const agent = {
    kind: record.aiKind,
    state: record.aiState,
    window: record.aiWindow,
    window_index: record.aiWindowIndex || record.aiWindow,
    pane: record.aiPane,
    pane_target: record.aiPaneTarget,
    current: record.aiCurrent === true,
    window_active: record.aiWindowActive === true,
    working_stopped_ts: record.aiWorkingStoppedTs,
    idle_since: record.aiIdleSince,
    last_active_ts: record.aiLastActiveTs,
    pid: record.aiPid,
  };
  return agent;
}

function infoRecordAgentActivityItem(record) {
  if (!infoRecordHasAi(record) || typeof agentWindowActivityIcon !== 'function') return null;
  const agent = infoRecordAgentPayload(record);
  return agentWindowActivityIcon(record.aiKind, agent.state, typeof agentWindowIdleSeconds === 'function' ? agentWindowIdleSeconds(agent) : null, {
    session: record.tabSession,
    window: agent.window,
    window_index: agent.window_index,
    pane: agent.pane,
    pane_target: agent.pane_target,
    current: false,
    window_active: false,
    working_stopped_ts: agent.working_stopped_ts,
    scheduleRefresh: false,
  });
}

function infoTabGroupStatusRank(state) {
  const key = String(state || '');
  if (key === 'attention') return 0;
  if (key === 'cooldown') return 1;
  if (key === STATE_KEY.working) return 2;
  return 9;
}

function infoTabGroupStatusItem(group = {}) {
  return infoTabGroupStatusRecord(group)?.item || null;
}

function infoTabGroupStatusRecord(group = {}) {
  if (group.dimension !== 'tab') return null;
  let best = null;
  for (const record of Array.isArray(group.records) ? group.records : []) {
    const item = infoRecordAgentActivityItem(record);
    if (!item || infoTabGroupStatusRank(item.state) >= 9) continue;
    if (!best || infoTabGroupStatusRank(item.state) < infoTabGroupStatusRank(best.item.state)) best = {record, item};
  }
  return best;
}

function infoTabGroupLeadingActivityHtml(group = {}) {
  const status = infoTabGroupStatusRecord(group);
  if (!status?.record || typeof agentWindowActivityIconHtmlForStatus !== 'function') return undefined;
  const record = status.record;
  const session = String(record?.tabSession || '').trim();
  if (!session) return undefined;
  const info = transcriptMeta.sessions?.[session] || {};
  const summary = typeof sessionStatusAgentWindowSummaryForTab === 'function'
    ? sessionStatusAgentWindowSummaryForTab(session, info, autoApproveStates.get(session))
    : null;
  const payload = autoApproveStates.get(session);
  const auto = payload?.enabled === true;
  const yoloHtml = yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: !readOnlyMode, yoloWorking: false, payload});
  const agent = summary?.agent || infoRecordAgentPayload(record);
  const activityHtml = summary?.item
    ? agentWindowActivityIconHtml(agent.kind, agent.state, agentWindowIdleSeconds(agent), {
      ...agentWindowActivityOptionsForStatus(agent, session),
      item: summary.item,
      statusOnly: true,
    })
    : agentWindowActivityIconHtmlForStatus(agent, record.aiKind, session, {statusOnly: true});
  return activityHtml ? `${yoloHtml}<span class="session-agent-activity-marker info-tree-tab-group-status">${activityHtml}</span>` : undefined;
}

function infoAgentAttentionHtml(record) {
  if (!infoRecordHasAi(record) || typeof agentWindowIsAttentionState !== 'function' || !agentWindowIsAttentionState(record.aiState)) return '';
  return '';
}

function infoRecordAiWindowButtonHtml(record, options = {}) {
  if (!infoRecordHasAi(record) || typeof tmuxWindowButtonHtml !== 'function') return '';
  const label = String(record?.aiLabel || '').trim();
  if (!label) return '';
  const labelHtml = infoRecordSearchValueHtml(record, 'tmux-window', label);
  const agent = infoRecordAgentPayload(record);
  const active = record.aiCurrent === true || record.aiWindowActive === true;
  const title = String(options.title || record.aiTitle || label);
  const attrs = options.action === false
    ? []
    : [
        `data-info-open-ai-tab="${esc(record.tabSession)}"`,
        `data-info-open-ai-window="${esc(record.aiWindow)}"`,
      ];
  return tmuxWindowButtonHtml({
    tag: options.action === false ? 'span' : 'button',
    classes: ['info-tree-ai-window-button'],
    session: record.tabSession,
    visibleName: label,
    labelHtml,
    numberLabel: record.aiWindowIndex || record.aiWindow || label,
    active,
    agentStatus: agent,
    agentKey: record.aiKind,
    title,
    attrs,
    ariaPressed: options.action !== false,
  });
}

function infoRecordAiRecencyHtml(record) {
  const agent = infoRecordAgentPayload(record);
  const lastActive = Number(agent.idle_since || agent.last_active_ts || 0);
  if (!Number.isFinite(lastActive) || lastActive <= 0) return '';
  const text = typeof sessionPopoverAgentRecencyText === 'function'
    ? sessionPopoverAgentRecencyText(agent)
    : sessionFileRelativeTimeText(lastActive);
  return text ? `<span class="info-tree-ai-recency info-tree-trailing-meta">${esc(text)}</span>` : '';
}

function infoRecordAiPidHtml(record) {
  const pidText = tmuxWindowPidText(record?.aiPid);
  return pidText ? `<span class="info-tree-ai-pid">${esc(pidText)}</span>` : '';
}

function infoRecordAiValueHtml(record, options = {}) {
  if (!infoRecordHasAi(record)) return '';
  const buttonHtml = infoRecordAiWindowButtonHtml(record, options);
  if (!buttonHtml) return '';
  const status = infoAgentAttentionHtml(record);
  const pid = infoRecordAiPidHtml(record);
  const recency = infoRecordAiRecencyHtml(record);
  return `<span class="info-tree-ai-value tmux-window-bar info-tree-ai-window-token" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info">${buttonHtml}${status}${pid}${recency}</span>`;
}

function infoRecordUpdatedMetaHtml(record) {
  if (infoRecordMissingValue(record?.updated)) return '';
  const source = record?.updatedSource === 'git-commit' ? t('info.meta.gitCommit') : '';
  const text = [source, record.updated].filter(Boolean).join(' ');
  const title = [source, String(record?.updatedTitle || record.updated)].filter(Boolean).join(': ');
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  return `<span class="info-tree-meta-updated info-tree-trailing-meta"${titleAttr}>${infoRecordSearchValueHtml(record, 'updated', text)}</span>`;
}

function infoRecordPathActivityMetaHtml(record) {
  const timestamp = Number(record?.pathActivityTs || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '';
  const text = relativeTimeFormat(Math.max(0, Math.floor(Date.now() / 1000) - timestamp));
  const title = `Latest repository path activity: ${text}`;
  return `<span class="info-tree-meta-updated info-tree-meta-path-activity info-tree-trailing-meta" title="${esc(title)}">${esc(text)}</span>`;
}

function infoRecordMainChipsHtml(record, options = {}) {
  const hiddenDimensions = new Set(Array.isArray(options.hiddenDimensions) ? options.hiddenDimensions : []);
  const fields = [];
  const pathVisible = !hiddenDimensions.has('path') && !infoRecordMissingValue(record?.pathLabel) && String(record?.pathKey || '') !== '__no_path__';
  const branchVisible = !hiddenDimensions.has('branch') && !infoRecordMissingValue(record?.branchLabel) && String(record?.branchKey || '') !== '__no_branch__';
  const updatedMeta = infoRecordUpdatedMetaHtml(record);
  if (!hiddenDimensions.has('tab') && infoRecordHasTab(record)) {
    fields.push(infoRecordFieldHtml('tab', infoRecordTabValueHtml(record), record.tabTitle));
  }
  if (!hiddenDimensions.has('tmux-window') && infoRecordHasAi(record)) {
    fields.push(infoRecordFieldHtml('ai', infoRecordAiValueHtml(record), record.aiTitle));
  }
  if (pathVisible) {
    const pathText = String(record?.pathTitle || record?.pathLabel || '').trim();
    fields.push(infoRecordFieldHtml('path', `<button type="button" class="info-tree-action-link info-tree-action-link-path" data-info-open-path="${esc(record.pathKey || pathText)}" title="${esc(pathText)}">${infoRecordSearchValueHtml(record, 'path', pathText)}</button>${infoRecordPathActivityMetaHtml(record)}`, record.pathTitle));
  }
  if (branchVisible) {
    const branchText = String(record?.branchTitle || record?.branchLabel || '').trim();
    fields.push(infoRecordFieldHtml('branch', `<span class="info-tree-value-text">${infoRecordSearchValueHtml(record, 'branch', branchText)}</span>${updatedMeta}`, record.branchTitle));
  }
  const prDesc = infoRecordPrDescHtml(record);
  if (!hiddenDimensions.has('pr') && prDesc) fields.push(infoRecordFieldHtml('pr', prDesc, record.prTitle));
  const linearDesc = infoRecordLinearDescHtml(record);
  if (!hiddenDimensions.has('linear') && linearDesc) fields.push(infoRecordFieldHtml('linear', linearDesc, record.linearTitle));
  return fields.join('');
}

function infoRecordHtml(record, options = {}) {
  return `<div class="${esc(infoTreeItemClasses('info-tree-record', options))}" data-info-record="${esc(record.id)}">
    <div class="info-tree-record-main">${infoRecordMainChipsHtml(record, options)}</div>
  </div>`;
}

function infoTreeHiddenDimensions(ancestors, dimension) {
  return Array.from(new Set([...(Array.isArray(ancestors) ? ancestors : []), dimension].filter(Boolean)));
}

function infoDimensionCountNoun(key, count) {
  const plural = count !== 1;
  if (key === 'tab') return plural ? 'tabs' : 'tab';
  if (key === 'tmux-window') return plural ? 'tmux sub-windows' : 'tmux sub-window';
  if (key === 'ai') return 'AI';
  if (key === 'path') return plural ? 'paths' : 'path';
  if (key === 'branch') return plural ? 'branches' : 'branch';
  if (key === 'pr') return plural ? 'PRs' : 'PR';
  if (key === 'linear') return plural ? 'Linear issues' : 'Linear issue';
  return plural ? 'items' : 'item';
}

function infoGroupLabelHtml(group = {}) {
  const label = String(group.label || '');
  if (group.dimension === 'path' && !infoRecordMissingValue(label) && String(group.key || '') !== '__no_path__') {
    const path = String(group.key || group.title || label);
    return `<span class="info-tree-group-label info-tree-group-label-path"><button type="button" class="info-tree-group-label-action" data-info-open-path="${esc(path)}" title="${esc(group.title || path)}">${infoGroupSearchValueHtml(group, label)}</button></span>`;
  }
  const representative = infoGroupRepresentativeRecord(group);
  if (group.dimension === 'tmux-window') {
    const html = infoRecordAiValueHtml(representative, {action: false});
    if (html) return `<span class="info-tree-group-label info-tree-group-label-ai">${html}</span>`;
  }
  if (group.dimension === 'pr') {
    const html = infoRecordPrDescHtml(representative);
    return `<span class="info-tree-group-label info-tree-group-label-pr">${html || 'None'}</span>`;
  }
  if (group.dimension === 'linear') {
    const html = infoRecordLinearDescHtml(representative);
    return `<span class="info-tree-group-label info-tree-group-label-linear">${html || 'None'}</span>`;
  }
  if (group.dimension === 'tab') {
    const tabHtml = infoRecordTabValueHtml(representative, {
      action: false,
      label,
      title: group.title || label,
      leadingHtml: infoTabGroupLeadingActivityHtml(group),
    });
    return tabHtml || `<span class="info-tree-group-label">${infoGroupSearchValueHtml(group, label)}</span>`;
  }
  return `<span class="info-tree-group-label">${infoGroupSearchValueHtml(group, label)}</span>`;
}

function infoGroupChildCountHtml(group = {}) {
  const directChildGroups = Array.isArray(group.children) ? group.children.filter(child => child?.type === 'group') : [];
  if (directChildGroups.length <= 1) return '';
  const dimension = directChildGroups[0]?.dimension || '';
  return `<span class="info-tree-group-child-count">(${esc(`${directChildGroups.length} ${infoDimensionCountNoun(dimension, directChildGroups.length)}`)})</span>`;
}

function infoGroupDimensionLabel(key) {
  if (key === 'tab' || key === 'tmux-window' || key === 'branch' || key === 'pr') return `${infoFieldLabel(key)}:`;
  return `${infoDimensionLabel(key)}:`;
}

function infoTreeChildrenHtml(children, depth = 0, ancestorDimensions = [], ancestorGroupIdentities = [], activeGroupKeys = null) {
  if (!Array.isArray(children) || !children.length) return '';
  return children.map((child, index) => {
    const treeItemOptions = {first: index === 0, last: index === children.length - 1};
    if (child?.type !== 'group') return infoRecordHtml(child, {...treeItemOptions, hiddenDimensions: ancestorDimensions});
    const hiddenDimensions = infoTreeHiddenDimensions(ancestorDimensions, child.dimension);
    const groupKey = infoTreeGroupCollapseKey(child, ancestorGroupIdentities);
    const childGroupIdentities = [...ancestorGroupIdentities, infoTreeGroupIdentity(child)];
    if (activeGroupKeys instanceof Set) activeGroupKeys.add(groupKey);
    const nested = child.children?.length && child.children[0]?.type === 'group'
      ? infoTreeChildrenHtml(child.children, depth + 1, hiddenDimensions, childGroupIdentities, activeGroupKeys)
      : (child.children || []).map((record, recordIndex, records) => infoRecordHtml(record, {
        first: recordIndex === 0,
        last: recordIndex === records.length - 1,
        hiddenDimensions,
      })).join('');
    const childCount = infoGroupChildCountHtml(child);
    const trailingMeta = child.dimension === 'path' ? infoRecordPathActivityMetaHtml(infoGroupRepresentativeRecord(child)) : '';
    const openAttr = infoCollapsedGroupKeys.has(groupKey) ? '' : ' open';
    return `<details class="${esc(infoTreeItemClasses('info-tree-group', treeItemOptions))}" data-info-dimension="${esc(child.dimension)}" data-info-depth="${depth}" data-info-group-key="${esc(groupKey)}"${openAttr}>
      <summary title="${esc(child.title)}">
        <span class="info-tree-group-dimension">${esc(infoGroupDimensionLabel(child.dimension))}</span>
        <span class="info-tree-group-label-line">${infoGroupLabelHtml(child)}${childCount}${trailingMeta}</span>
      </summary>
      <div class="info-tree-group-children">${nested}</div>
    </details>`;
  }).join('');
}

function infoDimensionLabel(key) {
  return infoDimensionDefs.find(dimension => dimension.key === key)?.label || key;
}

function infoTreeHtml(records = infoRelationshipRecords(), grouping = infoGrouping, sort = infoSort) {
  const normalizedSort = normalizeInfoSort(sort);
  const tree = infoGroupTree(records, grouping, normalizedSort);
  const activeGroupKeys = new Set();
  const childrenHtml = infoTreeChildrenHtml(tree.children || [], 0, [], [], activeGroupKeys);
  pruneInfoTreeCollapsedGroups(activeGroupKeys);
  return `<div class="info-tree" data-info-grouping="${esc(normalizeInfoGrouping(grouping).join(','))}" data-info-sort="${esc(`${normalizedSort.key}:${normalizedSort.dir}`)}" data-info-search="${esc(infoSearch.trim())}">${childrenHtml}</div>`;
}

function infoPanelRenderVisible() {
  return activePaneItems().includes(infoItemId);
}

function infoPanelRenderSignature() {
  return JSON.stringify({
    loading: transcriptMetaLoading,
    loaded: transcriptMetaLoaded,
    error: transcriptMetaLoadError,
    search: infoSearch,
    grouping: infoGrouping,
    sort: infoSort,
    meta: transcriptMeta,
  });
}

function renderInfoPanel(options = {}) {
  const node = document.getElementById('info-content');
  if (!node) return;
  if (options.force !== true && !infoPanelRenderVisible()) {
    infoPanelRenderPending = true;
    recordClientPerfCounter('renderInfoPanel', 0, {skipped: 1});
    return;
  }
  infoPanelRenderPending = false;
  let renderedNodes = 0;
  const perf = clientPerfStart('renderInfoPanel');
  try {
    return renderInfoPanelMeasured(node, options);
  } finally {
    renderedNodes = node.querySelectorAll?.('.info-tree-record, .info-tree-group')?.length || 0;
    clientPerfEnd(perf, {nodes: renderedNodes});
  }
}

function renderInfoPanelMeasured(node, options = {}) {
  const syncInfoContent = () => {
    if (typeof syncInfoTreeScrolledState === 'function') syncInfoTreeScrolledState(node.closest('.info-tree-panel'));
    if (typeof refreshPanePopouts === 'function') refreshPanePopouts(infoItemId);
  };
  const renderInfoContent = html => {
    node.innerHTML = html;
    syncInfoContent();
  };
  syncTranscriptMetaLoadingUi();
  const signature = infoPanelRenderSignature();
  if (options.force !== true && signature === infoPanelLastRenderSignature && infoPanelLastRenderHtml) {
    const hasContent = Boolean(node.children?.length || String(node.innerHTML || '').trim());
    if (!hasContent) renderInfoContent(infoPanelLastRenderHtml);
    else syncInfoContent();
    return;
  }
  const commitInfoContent = html => {
    infoPanelLastRenderSignature = signature;
    infoPanelLastRenderHtml = html;
    renderInfoContent(html);
  };
  const allRecords = infoRelationshipRecords();
  const records = infoFilteredRecords(allRecords, infoSearch);
  if (!records.length) {
    if (allRecords.length && infoSearch.trim()) {
      commitInfoContent(`<div class="info-empty info-tree-empty">No matches for "${esc(infoSearch.trim())}"</div>`);
      return;
    }
    if (transcriptMetaLoading) {
      commitInfoContent(infoMetadataLoadingHtml());
      return;
    }
    if (transcriptMetaLoadError) {
      commitInfoContent(`<div class="info-empty info-error">${esc(t('info.loadFailed'))} ${esc(transcriptMetaLoadError)}</div>`);
      return;
    }
    if (!transcriptMetaLoaded) {
      commitInfoContent(infoMetadataLoadingHtml());
      return;
    }
    commitInfoContent(`<div class="info-empty">${esc(t('info.empty'))}</div>`);
    return;
  }
  commitInfoContent(infoTreeHtml(records, infoGrouping, infoSort));
}

function infoPrCellHtml(row) {
  if (row?.prLabel) return linkHtml(row.prUrl || '', row.prLabel, row.prTitle || '', row.prClass || '');
  return row?.prHtml || '';
}

function infoLinearCellHtml(row) {
  if (Array.isArray(row?.linearItems)) {
    return row.linearItems.map(item => {
      if (item?.url) return linearIssueHtml(item);
      return linearIssueLinkHtml(item?.identifier || '');
    }).filter(Boolean).join(' ');
  }
  return row?.linearHtml || '';
}

function infoPathLabel(git) {
  const path = infoGitRoot(git);
  const label = compactHomePath(path);
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return label;
  return `${label} (worktree of ${compactHomePath(parent)})`;
}

function infoPathTitle(git) {
  const path = infoGitRoot(git);
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return path;
  return `${path} (worktree of ${parent})`;
}

function infoGitRoot(git) {
  return String(git?.worktree?.path || git?.root || git?.cwd || '');
}

function infoNormalizedPath(value) {
  const text = String(value || '').trim();
  return typeof normalizeDirectoryPath === 'function' ? normalizeDirectoryPath(text) : text.replace(/\/+$/, '') || text;
}

function infoGitPathKey(git) {
  return infoNormalizedPath(infoGitRoot(git));
}

function infoPathIsWithin(path, root) {
  const normalizedPath = infoNormalizedPath(path);
  const normalizedRoot = infoNormalizedPath(root);
  return Boolean(normalizedPath && normalizedRoot && (normalizedPath === normalizedRoot || normalizedPath.startsWith(`${normalizedRoot}/`)));
}

function infoBranchSourcesForSession(session, info) {
  const project = info?.project || {};
  const primaryGit = project.git;
  const primaryKey = infoGitPathKey(primaryGit);
  const sources = [];
  const seenSources = new Set();
  const addSource = (git, primary) => {
    const sourceKey = infoGitPathKey(git);
    const branches = git?.other_branches?.branches;
    if (!sourceKey || !Array.isArray(branches) || !branches.length || seenSources.has(sourceKey)) return;
    seenSources.add(sourceKey);
    sources.push({session, info, project, git, primary: primary === true});
  };
  addSource(primaryGit, true);
  for (const repo of Array.isArray(project.repos) ? project.repos : []) {
    addSource(repo, Boolean(primaryKey && infoGitPathKey(repo) === primaryKey));
  }
  for (const row of Array.isArray(info?.window_metadata) ? info.window_metadata : []) {
    addSource(row?.git, Boolean(primaryKey && infoGitPathKey(row?.git) === primaryKey));
  }
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(session, info, autoApproveStates.get(session))
    : [];
  for (const agent of agents) {
    addSource(agent?.git, Boolean(primaryKey && infoGitPathKey(agent?.git) === primaryKey));
    for (const entry of typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : []) {
      addSource(entry?.git, Boolean(primaryKey && infoGitPathKey(entry?.git) === primaryKey));
    }
  }
  return sources;
}

function infoBranchSourcesForIndexedRepos() {
  const repos = Array.isArray(transcriptMeta?.indexed_repos) ? transcriptMeta.indexed_repos : [];
  return repos
    .filter(git => infoGitPathKey(git) && Array.isArray(git?.other_branches?.branches) && git.other_branches.branches.length)
    .map(git => ({session: '', info: {}, project: {}, git, primary: false, indexed: true}));
}

function infoBranchOwnedBySource(git, branch) {
  const branchName = String(branch?.name || '');
  const currentBranch = String(git?.branch || '');
  return branch?.current === true && Boolean(branchName) && (!currentBranch || currentBranch === branchName);
}

function infoBranchGitMatches(git, branchName, options = {}) {
  const gitBranch = String(git?.branch || '');
  if (options.requireBranch === true) return Boolean(branchName && gitBranch && gitBranch === branchName);
  return !branchName || !gitBranch || gitBranch === branchName;
}

function infoAgentWindowMatchesBranchGit(agent, sourceRoot, branchName, options = {}) {
  const pathEntries = typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : [];
  const gitCandidates = [
    agent?.git,
    ...(pathEntries.map(entry => entry?.git)),
  ].filter(git => git && typeof git === 'object');
  let sawMatchingRoot = false;
  for (const git of gitCandidates) {
    if (infoGitPathKey(git) !== infoNormalizedPath(sourceRoot)) continue;
    sawMatchingRoot = true;
    if (infoBranchGitMatches(git, branchName, options)) return true;
  }
  if (sawMatchingRoot) return false;
  if (options.requireBranch === true) return false;
  const paths = [
    agent?.path,
    ...(Array.isArray(agent?.paths) ? agent.paths : []),
    ...(pathEntries.map(entry => entry?.path)),
  ].filter(Boolean);
  return paths.some(path => infoPathIsWithin(path, sourceRoot));
}

function infoAgentWindowMatchesPathRoot(agent, sourceRoot) {
  const pathEntries = typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : [];
  const gitCandidates = [
    agent?.git,
    ...(pathEntries.map(entry => entry?.git)),
  ].filter(git => git && typeof git === 'object');
  if (gitCandidates.some(git => infoGitPathKey(git) === infoNormalizedPath(sourceRoot))) return true;
  const paths = [
    agent?.path,
    ...(Array.isArray(agent?.paths) ? agent.paths : []),
    ...(pathEntries.map(entry => entry?.path)),
  ].filter(Boolean);
  return paths.some(path => infoPathIsWithin(path, sourceRoot));
}

function infoSourceWindowRowsForBranch(source, branch, options = {}) {
  if (!source.session) return [];
  const root = infoGitPathKey(source.git);
  const branchName = String(branch?.name || source.git?.branch || '');
  return (Array.isArray(source.info?.window_metadata) ? source.info.window_metadata : [])
    .filter(row => {
      const git = row?.git || {};
      return infoGitPathKey(git) === root
        && infoBranchGitMatches(git, branchName, options);
    });
}

function infoTabAgentLabel(session, agent = null) {
  const tabLabel = typeof sessionLabel === 'function' ? sessionLabel(session) : String(session || '');
  if (!agent) return `${tabLabel} / no AI`;
  return `${tabLabel} / ${infoTabAgentAiLabel(agent)}`;
}

function infoTabAgentAiLabel(agent = null) {
  if (!agent) return 'no AI';
  if (agent.aiLabel) return String(agent.aiLabel);
  if (agent.label && String(agent.label).includes(' / ')) return String(agent.label).split(' / ').slice(1).join(' / ') || 'AI';
  const agentLabel = typeof agentWindowCanonicalLabel === 'function'
    ? agentWindowCanonicalLabel(agent.window_index ?? agent.window, agent.kind, agent.window_label || agent.label || agent.kind)
    : String(agent.label || agent.kind || 'AI');
  return agentLabel || 'AI';
}

function infoAgentKindLabel(value) {
  const kind = String(value || '').trim();
  return kind || 'No AI';
}

function infoTabAgentEntry(session, agent = null) {
  const tabLabel = typeof sessionLabel === 'function' ? sessionLabel(session) : String(session || '');
  const aiLabel = infoTabAgentAiLabel(agent);
  const aiKind = String(agent?.kind || '');
  const label = infoTabAgentLabel(session, agent);
  const title = agent
    ? `${label}${agent.state ? ` · ${agent.state}` : ''}${agent.path ? ` · ${agent.path}` : ''}`
    : `${label} · no Claude/Codex tmux sub-window detected`;
  return {
    session: String(session || ''),
    label,
    title,
    tabLabel,
    aiLabel,
    kind: aiKind,
    aiAgentLabel: infoAgentKindLabel(aiKind),
    window: String(agent?.window_index ?? agent?.window ?? ''),
    windowIndex: String(agent?.window_index ?? agent?.window ?? ''),
    state: String(agent?.state || ''),
    pane: String(agent?.pane || ''),
    pane_target: String(agent?.pane_target || ''),
    current: agentWindowPayloadCurrent(agent) === true,
    window_active: agent?.window_active === true,
    pid: tmuxWindowProcessPid(agent),
    working_stopped_ts: Number.isFinite(Number(agent?.working_stopped_ts)) ? Number(agent.working_stopped_ts) : 0,
    idle_since: Number.isFinite(Number(agent?.idle_since)) ? Number(agent.idle_since) : 0,
    last_active_ts: Number.isFinite(Number(agent?.last_active_ts)) ? Number(agent.last_active_ts) : 0,
  };
}

function infoBranchTabAgentsForSource(source, branch) {
  if (!source.session) return [];
  const owned = infoBranchOwnedBySource(source.git, branch);
  const root = infoGitRoot(source.git);
  const branchName = String(branch?.name || source.git?.branch || '');
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(source.session, source.info, autoApproveStates.get(source.session))
    : [];
  const matchingAgents = agents.filter(agent => infoAgentWindowMatchesBranchGit(agent, root, branchName, {requireBranch: !owned}));
  if (matchingAgents.length) return matchingAgents.map(agent => infoTabAgentEntry(source.session, agent));
  const matchingWindows = infoSourceWindowRowsForBranch(source, branch, {requireBranch: !owned});
  if (!owned) {
    if (matchingWindows.length) return [infoTabAgentEntry(source.session, null)];
    return [];
  }
  if (source.primary === true && agents.length) return agents.map(agent => infoTabAgentEntry(source.session, agent));
  if (matchingWindows.length || source.primary === true || !agents.length) return [infoTabAgentEntry(source.session, null)];
  return [];
}

function infoPathTabAgentsForSource(source) {
  if (!source.session) return [];
  const root = infoGitRoot(source.git);
  if (!root) return [];
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(source.session, source.info, autoApproveStates.get(source.session))
    : [];
  const matchingAgents = agents.filter(agent => infoAgentWindowMatchesPathRoot(agent, root));
  if (matchingAgents.length) return matchingAgents.map(agent => infoTabAgentEntry(source.session, agent));
  return [infoTabAgentEntry(source.session, null)];
}

function mergedInfoTabAgents(...groups) {
  const seen = new Set();
  const result = [];
  for (const group of groups) {
    for (const item of Array.isArray(group) ? group : []) {
      const label = String(item?.label || '');
      const session = String(item?.session || '');
      const key = `${session}\n${label}`;
      if (!label || seen.has(key)) continue;
      seen.add(key);
      result.push({
        session,
        label,
        title: String(item?.title || label),
        tabLabel: String(item?.tabLabel || (session && typeof sessionLabel === 'function' ? sessionLabel(session) : session) || ''),
        aiLabel: String(item?.aiLabel || infoTabAgentAiLabel(item)),
        kind: String(item?.kind || ''),
        window: String(item?.window || ''),
        windowIndex: String(item?.windowIndex ?? item?.window_index ?? item?.window ?? ''),
        state: String(item?.state || ''),
        pane: String(item?.pane || ''),
        pane_target: String(item?.pane_target || ''),
        current: item?.current === true,
        window_active: item?.window_active === true,
        working_stopped_ts: Number.isFinite(Number(item?.working_stopped_ts)) ? Number(item.working_stopped_ts) : 0,
        idle_since: Number.isFinite(Number(item?.idle_since)) ? Number(item.idle_since) : 0,
        last_active_ts: Number.isFinite(Number(item?.last_active_ts)) ? Number(item.last_active_ts) : 0,
      });
    }
  }
  return result;
}

function infoTabAgentsText(items) {
  return (Array.isArray(items) ? items : []).map(item => item?.label || '').filter(Boolean).join(', ');
}

function rowWithInfoTabAgents(row, tabAgents) {
  const merged = mergedInfoTabAgents(tabAgents);
  const pathAgents = mergedInfoTabAgents(row?.pathTabAgents);
  const text = infoTabAgentsText(merged);
  return {
    ...row,
    tabAgents: merged,
    tabAgentsTitle: merged.map(item => item.title || item.label).filter(Boolean).join('\n'),
    pathTabAgents: pathAgents,
    pathTabAgentsTitle: pathAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    session: text,
    current: merged.length > 0,
  };
}

function infoPathActivityForSource(source = {}) {
  const root = infoGitRoot(source.git);
  const repos = Array.isArray(source?.project?.repos) ? source.project.repos : [];
  const repo = repos.find(item => infoNormalizedPath(item?.root) === infoNormalizedPath(root));
  const timestamp = Number(repo?.activity_ts ?? source?.git?.activity_ts ?? 0);
  return {
    timestamp: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
    source: String(repo?.activity_source || source?.git?.activity_source || ''),
  };
}

function infoBranchRowForSource(source, branch, ownsSession) {
  const {session, info, project, git, primary} = source;
  const useCurrentProjectMetadata = ownsSession && primary;
  const currentPr = useCurrentProjectMetadata ? displayPullRequest(info) : null;
  const currentLinear = useCurrentProjectMetadata ? project.linear || [] : [];
  const branchLinear = Array.isArray(branch.linear) ? branch.linear : [];
  const branchLinearIds = Array.isArray(branch.linear_ids) ? branch.linear_ids : [];
  const prLinearIds = Array.isArray(branch.pull_request?.linear_ids) ? branch.pull_request.linear_ids : [];
  const linearSourceItems = currentLinear.length ? currentLinear : branchLinear;
  const fallbackLinearIds = Array.from(new Set([...branchLinearIds, ...prLinearIds].map(item => String(item || '').trim()).filter(Boolean)));
  const linearIds = linearSourceItems.length
    ? linearSourceItems.map(issue => issue.identifier).filter(Boolean)
    : fallbackLinearIds;
  const linearHtml = linearSourceItems.length
    ? linearSourceItems.map(issue => linearIssueHtml(issue)).join(' ')
    : linearIds.map(linearIssueLinkHtml).filter(Boolean).join(' ');
  const prHtml = currentPr?.number ? pullRequestColumnLinkHtml(currentPr) : pullRequestLinkForBranch(git, branch);
  const prValue = currentPr?.number ? currentPr : branch.pull_request;
  const prTitle = pullRequestTextForBranch(prValue, branch.subject || '');
  const prNumber = infoPrNumberFromValue(prValue?.number);
  const prDescriptionTitle = prValue?.number
    ? [`#${prValue.number}`, prValue.title || prValue.description || branch.subject || ''].filter(Boolean).join(' ')
    : '';
  const repoUrl = git?.github_repo?.url || '';
  const prUrl = prValue?.url || (prValue?.number && repoUrl ? `${repoUrl}/pull/${prValue.number}` : '');
  const prLabel = prValue?.number ? pullRequestLinkLabel(prValue) : '';
  const prClass = prValue?.number ? pullRequestStatusClass(prValue) : '';
  const prLifecycleText = prValue?.number ? infoPullRequestLifecycleText(prValue) : '';
  const prLifecycleClass = prValue?.number ? infoPullRequestLifecycleClass(prValue) : '';
  const prCiText = prValue?.number ? infoPullRequestCiText(prValue) : '';
  const prCiClass = prCiText ? infoPullRequestCiClass(prValue) : '';
  const linearTitle = linearSourceItems.length
    ? linearSourceItems.map(issue => [issue.identifier, issue.state, issue.title].filter(Boolean).join(' ')).filter(Boolean).join(' · ')
    : linearIds.join(' ');
  const linearItems = linearSourceItems.length
    ? linearSourceItems.map(issue => ({
      identifier: String(issue?.identifier || ''),
      state: String(issue?.state || ''),
      title: String(issue?.title || ''),
      url: String(issue?.url || ''),
    })).filter(issue => issue.identifier || issue.url)
    : linearIds.map(identifier => ({identifier: String(identifier || '')})).filter(issue => issue.identifier);
  const desc = shortText(
    currentPr?.title
      || currentPr?.description
      || currentLinear.find(issue => issue.title)?.title
      || branch.subject
      || '',
    180,
  );
  const pathActivity = infoPathActivityForSource(source);
  return {
    session: '',
    path: infoGitRoot(git),
    pathLabel: infoPathLabel(git),
    pathTitle: infoPathTitle(git),
    pathActivityTs: pathActivity.timestamp,
    pathActivitySource: pathActivity.source,
    branch: branch.name || '',
    branchHtml: branchLinkHtml(git, branch.name),
    desc,
    updated: branch.updated || '',
    updatedText: branchUpdatedText(branch),
    updatedTitle: branch.updated || branchUpdatedText(branch),
    updatedTs: Number.isFinite(branch.updated_ts) ? branch.updated_ts : 0,
    updatedSource: 'git-commit',
    prHtml: prHtml || '',
    prTitle,
    prDescriptionTitle,
    prUrl,
    prLabel,
    prNumber: Number.isFinite(prNumber) ? prNumber : null,
    prClass,
    prLifecycleText,
    prLifecycleClass,
    prCiText,
    prCiClass,
    prSort: prTitle || (prValue?.number ? String(prValue.number) : ''),
    linearHtml,
    linearItems,
    linearTitle,
    current: false,
    sourcePrimary: primary,
    tabAgents: infoBranchTabAgentsForSource(source, branch),
    pathTabAgents: infoPathTabAgentsForSource(source),
  };
}

function preferInfoBranchMetadataRow(existing, next) {
  if (!existing) return next;
  if (next.tabAgents?.length && !existing.tabAgents?.length) return next;
  if (next.tabAgents?.length && next.sourcePrimary && !existing.sourcePrimary) return next;
  return existing;
}

function mergeInfoBranchRow(existing, next) {
  if (!existing) return rowWithInfoTabAgents(next, next.tabAgents);
  const preferred = preferInfoBranchMetadataRow(existing, next);
  const mergedAgents = mergedInfoTabAgents(existing.tabAgents, next.tabAgents);
  const mergedPathAgents = mergedInfoTabAgents(existing.pathTabAgents, next.pathTabAgents);
  return rowWithInfoTabAgents({...preferred, pathTabAgents: mergedPathAgents}, mergedAgents);
}

function rawInfoBranchRows() {
  const rowsByKey = new Map();
  const infoSessions = Array.isArray(transcriptMeta?.session_order) ? transcriptMeta.session_order : sessions;
  for (const session of infoSessions) {
    const info = transcriptMeta.sessions?.[session];
    for (const source of infoBranchSourcesForSession(session, info)) {
      for (const branch of source.git?.other_branches?.branches || []) {
        const key = `${infoGitPathKey(source.git)}\n${branch.name || ''}`;
        const row = infoBranchRowForSource(source, branch, infoBranchOwnedBySource(source.git, branch));
        rowsByKey.set(key, mergeInfoBranchRow(rowsByKey.get(key), row));
      }
    }
  }
  for (const source of infoBranchSourcesForIndexedRepos()) {
    for (const branch of source.git?.other_branches?.branches || []) {
      const key = `${infoGitPathKey(source.git)}\n${branch.name || ''}`;
      const row = infoBranchRowForSource(source, branch, false);
      rowsByKey.set(key, mergeInfoBranchRow(rowsByKey.get(key), row));
    }
  }
  return [...rowsByKey.values()];
}

function shareInfoString(value, limit = 500) {
  return String(value || '').slice(0, limit);
}

function shareInfoTabAgentsSnapshot(items) {
  return Array.isArray(items)
    ? items.slice(0, 20).map(item => ({
      session: shareInfoString(item?.session, 80),
      label: shareInfoString(item?.label, 200),
      title: shareInfoString(item?.title, 500),
      tabLabel: shareInfoString(item?.tabLabel, 120),
      aiLabel: shareInfoString(item?.aiLabel, 120),
      kind: shareInfoString(item?.kind, 40),
      window: shareInfoString(item?.window, 40),
    })).filter(item => item.label)
    : [];
}

function shareInfoRowSnapshot(row = {}) {
  const tabAgents = shareInfoTabAgentsSnapshot(row.tabAgents);
  const pathTabAgents = shareInfoTabAgentsSnapshot(row.pathTabAgents);
  const tabAgentText = tabAgents.length ? infoTabAgentsText(tabAgents) : shareInfoString(row.session, 200);
  return {
    session: tabAgentText,
    tabAgents,
    tabAgentsTitle: tabAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    pathTabAgents,
    pathTabAgentsTitle: pathTabAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    path: shareInfoString(row.path, 1000),
    pathLabel: shareInfoString(row.pathLabel, 1000),
    pathTitle: shareInfoString(row.pathTitle, 1000),
    pathActivityTs: Number.isFinite(row.pathActivityTs) ? row.pathActivityTs : 0,
    pathActivitySource: shareInfoString(row.pathActivitySource, 100),
    branch: shareInfoString(row.branch, 500),
    desc: shareInfoString(row.desc, 1000),
    updated: shareInfoString(row.updated, 200),
    updatedText: shareInfoString(row.updatedText, 200),
    updatedTitle: shareInfoString(row.updatedTitle, 500),
    updatedTs: Number.isFinite(row.updatedTs) ? row.updatedTs : 0,
    updatedSource: shareInfoString(row.updatedSource, 100),
    prTitle: shareInfoString(row.prTitle, 1000),
    prUrl: shareInfoString(row.prUrl, 1000),
    prLabel: shareInfoString(row.prLabel, 100),
    prNumber: Number.isFinite(infoRowPrNumber(row)) ? infoRowPrNumber(row) : null,
    prClass: shareInfoString(row.prClass, 100),
    prSort: shareInfoString(row.prSort, 1000),
    linearTitle: shareInfoString(row.linearTitle, 1000),
    linearItems: Array.isArray(row.linearItems)
      ? row.linearItems.slice(0, 20).map(item => ({
        identifier: shareInfoString(item?.identifier, 120),
        state: shareInfoString(item?.state, 120),
        title: shareInfoString(item?.title, 500),
        url: shareInfoString(item?.url, 1000),
      })).filter(item => item.identifier || item.url)
      : [],
    current: row.current === true,
  };
}

function cleanShareInfoRows(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 1000).map(shareInfoRowSnapshot);
}

function shareInfoStateSnapshot(options = {}) {
  const snapshot = {
    grouping: currentInfoGrouping(),
    sort: currentInfoSort(),
    search: currentInfoSearch(),
  };
  if (options.includeRows !== false) snapshot.branchRows = infoBranchRows().map(shareInfoRowSnapshot);
  return snapshot;
}

function applyShareInfoState(info = {}) {
  if (!info || typeof info !== 'object') return;
  if ('grouping' in info || 'infoGrouping' in info || 'info2Grouping' in info) {
    infoGrouping = normalizeInfoGrouping(info.grouping || info.infoGrouping || info.info2Grouping);
  }
  if ('sort' in info || 'infoSort' in info || 'info2Sort' in info) {
    infoSort = normalizeInfoSort(info.sort || info.infoSort || info.info2Sort);
  }
  if ('search' in info || 'infoSearch' in info || 'info2Search' in info) {
    setInfoSearch(info.search ?? info.infoSearch ?? info.info2Search, {publish: false, render: false});
  }
  if ('branchRows' in info) shareInfoBranchRowsOverride = cleanShareInfoRows(info.branchRows);
  refreshInfoGroupingControls();
  renderInfoPanel({force: true});
  restoreShareScrollTargetByKey('info');
}

function bindPanelControls(panel, session) {
  delegate(panel, 'click', '[data-tab]', (_event, button) => {
    const currentName = button.dataset.tabName;
    const nextName = currentName !== 'terminal' && button.classList.contains(CLS.active) ? 'terminal' : currentName;
    activateTab(button.dataset.tab, nextName, {userInitiated: true});
  });
  delegate(panel, 'click', '[data-window-dir], [data-window-index]', (event, button) => {
    if (button.dataset.pointerActionHandled === '1') {
      delete button.dataset.pointerActionHandled;
      return;
    }
    handleWindowStepButtonClick(event);
  });
  delegate(panel, 'click', '[data-pane-close]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    removePaneFromLayout(button.dataset.paneClose);
  });
  delegate(panel, 'click', '[data-pane-minimize]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    minimizePaneFromLayout(button.dataset.paneMinimize);
  });
  delegate(panel, 'click', '[data-pane-expand]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    expandPaneFromLayout(button.dataset.paneExpand);
  });
  delegate(panel, 'click', '[data-pane-actions]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = button.getBoundingClientRect();
    showSessionContextMenu(button.dataset.paneActions || session, rect.left, rect.bottom + 4);
  });
  if (isTmuxSession(session)) {
    panel.querySelector('.panel-head')?.addEventListener('contextmenu', event => {
      if (event.target.closest('button, input')) return;
      event.preventDefault();
      event.stopPropagation();
      showSessionContextMenu(session, event.clientX, event.clientY);
    });
  }
  delegate(panel, 'click', '[data-context]', () => showContext(session));
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-auto-session]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    toggleAutoApprove(target.dataset.autoSession || session);
  });
  panel.querySelector('.meta')?.addEventListener('click', event => {
    event.stopPropagation();
    const cycle = event.target.closest('[data-repo-cycle]');
    if (cycle) {
      event.preventDefault();
      const targetSession = cycle.dataset.repoCycle || session;
      cycleSessionRepoDisplay(targetSession, transcriptMeta.sessions?.[targetSession], cycle.dataset.repoCycleDir || 1);
      updatePanelHeader(targetSession, transcriptMeta.sessions?.[targetSession]);
      renderSessionButtons();
      renderPaneTabStrips();
      return;
    }
    // The repo count opens the per-session multi-repo popover (delegated, since .meta re-renders).
    const chip = event.target.closest('[data-repo-chip]');
    if (chip) {
      event.preventDefault();
      showRepoChipMenu(chip.dataset.repoChip || session, event.clientX, event.clientY);
    }
  });
  panel.querySelector('.meta')?.addEventListener('dragstart', event => event.stopPropagation());
  bindFileUpload(panel, session);
}

function hasFileDrag(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes('Files') || Boolean(event.dataTransfer?.files?.length);
}

function hasUploadableDrag(event) {
  // External file drops (any type) OR an image exposed as rich data with no File entry — both must be
  // claimed so a dragged image never leaks to the terminal-backed agent as a rich [Image #N] attachment.
  return hasFileDrag(event) || dataTransferHasImagePayload(event?.dataTransfer);
}

function bindFileUpload(panel, session) {
  if (readOnlyMode) return;
  panel.addEventListener('dragenter', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragover', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragleave', event => {
    if (!hasUploadableDrag(event)) return;
    if (panel.contains(event.relatedTarget)) return;
    panel.classList.remove(CLS.fileDragOver);
  });
  panel.addEventListener('drop', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.remove(CLS.fileDragOver);
    // DOIT.57: remember the drop point so the post-upload suggestion overlay can anchor there.
    // Prefer the plain File list; fall back to images extracted from rich data (text/html <img>,
    // image MIME) so a dragged image exposed without a File still uploads instead of leaking.
    const dropped = event.dataTransfer?.files?.length ? event.dataTransfer.files : dataTransferImageFiles(event.dataTransfer);
    uploadFiles(session, dropped, {suggestAt: {x: event.clientX, y: event.clientY}});
  });
}

function insertFileDragPayloadIntoTerminal(session, payload) {
  const references = terminalFileReferences(session, payload);
  if (!references.length) return;
  const inserted = insertIntoTerminal(session, `${references.map(shellQuote).join(' ')} `);
  const label = references.length === 1 ? references[0] : tPlural('files.pathCount', references.length);
  statusEl.innerHTML = inserted
    ? `<span class="ok">${localizedHtml('status.insertedInto', {name: label, session: sessionLabel(session)})}</span>`
    : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
}

function bindClipboardPaste() {
  if (readOnlyMode) return;
  if (clipboardPasteBound) return;
  clipboardPasteBound = true;
  document.addEventListener('paste', event => {
    if (!dataTransferHasImagePayload(event.clipboardData)) return;
    const editorTarget = markdownEditorPasteTarget(event);
    // Image-bearing paste: ALWAYS claim it (preventDefault + stopPropagation) so the raw image can never
    // reach a CodeMirror editor or terminal-backed agent as a rich [Image #N] attachment.
    // Then upload ALL pasted images and insert only textual references for the focused surface.
    event.preventDefault();
    event.stopPropagation();
    if (editorTarget) {
      const files = dataTransferImageFiles(event.clipboardData);
      if (!files.length) {
        statusErr(localizedHtml('status.selectPaneForImagePaste'));
        return;
      }
      if (!beginPasteUpload(`editor:${editorTarget.path}`)) return;
      uploadEditorFiles(editorTarget, files).finally(() => {
        pasteUploadInFlight = false;
      });
      return;
    }
    const session = pasteTargetSession(event);
    if (!session) {
      statusErr(localizedHtml('status.selectPaneForImagePaste'));
      return;
    }
    const files = dataTransferImageFiles(event.clipboardData);
    if (!files.length) {
      // Claimed (so nothing leaks to the agent) but the image was exposed only as un-extractable rich
      // data (e.g. a remote <img> URL with no File and no data: URL).
      statusErr(localizedHtml('status.selectPaneForImagePaste'));
      return;
    }
    if (!beginPasteUpload(session)) return;
    uploadFiles(session, files, {source: 'paste'}).finally(() => {
      pasteUploadInFlight = false;
    });
  }, {capture: true});
}

function markdownEditorPasteTarget(event) {
  const eventPanel = event.target?.closest?.('.file-editor-panel') || null;
  const focusedPanel = !eventPanel && !focusedTerminal && isFileEditorItem(focusedPanelItem) ? panelNodes.get(focusedPanelItem) || null : null;
  const panel = eventPanel || focusedPanel;
  const view = panel?._cmView || null;
  if (!panel || !view || panel._cmMode === 'diff') return null;
  const path = fileEditorPanelPath(panel) || fileItemPath(fileEditorPanelItem(panel) || focusedPanelItem);
  if (!path || previewRendererForPath(path)?.id !== 'markdown') return null;
  return {panel, view, path};
}

// ONE shared image-payload contract for BOTH paste (clipboardData) and drop (dataTransfer). A browser may
// expose an image as a File, OR as rich data (a text/html <img>, an image MIME type) with NO File. ALL of
// these must be detectable so the handlers can CLAIM the event and never let a raw image reach the
// terminal-backed agent as a rich [Image #N] attachment. See AGENTS.md (rich-data drag/paste note).
function dataTransferHasImagePayload(dt) {
  if (!dt) return false;
  if (Array.from(dt.items || []).some(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))) return true;
  if (dt.files && Array.from(dt.files).some(file => String(file.type || '').startsWith('image/'))) return true;
  const types = Array.from(dt.types || []);
  if (types.some(type => String(type).startsWith('image/'))) return true;
  if (types.includes('text/html') && /<img\b/i.test(typeof dt.getData === 'function' ? (dt.getData('text/html') || '') : '')) return true;
  return false;
}

// Extract EVERY image in the payload as a renamed upload File, so multi-image prompts are deterministic
// (N images -> N uploaded path references, never one text ref + one attachment). Handles File items, a
// plain File list, and data: URL <img> sources embedded in text/html (browser image copies).
function dataTransferImageFiles(dt) {
  if (!dt) return [];
  const files = [];
  for (const item of Array.from(dt.items || [])) {
    if (item.kind !== 'file' || !String(item.type || '').startsWith('image/')) continue;
    const file = item.getAsFile?.();
    if (!file) continue;
    const type = file.type || item.type || 'image/png';
    files.push(new File([file], pastedImageFilename(file.name, type), {type}));
  }
  if (!files.length && dt.files) {
    for (const file of Array.from(dt.files)) {
      if (!String(file.type || '').startsWith('image/')) continue;
      const type = file.type || 'image/png';
      files.push(new File([file], pastedImageFilename(file.name, type), {type}));
    }
  }
  if (!files.length && typeof dt.getData === 'function') {
    const html = dt.getData('text/html') || '';
    const re = /<img\b[^>]*\bsrc\s*=\s*["']?(data:image\/[^"'\s>]+)/gi;
    let match;
    while ((match = re.exec(html))) {
      const file = dataUrlToImageFile(match[1]);
      if (file) files.push(file);
    }
  }
  return files;
}

function dataUrlToImageFile(dataUrl) {
  const match = /^data:(image\/[a-z0-9.+-]+);base64,(.*)$/i.exec(String(dataUrl || ''));
  if (!match) return null;
  const type = match[1];
  try {
    const binary = atob(match[2]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return new File([bytes], pastedImageFilename('', type), {type});
  } catch (_) {
    return null;
  }
}

function beginPasteUpload(session) {
  const now = Date.now();
  if (pasteUploadInFlight) return false;
  const existing = readStoredJson(pasteLockStorageKey, null);
  if (existing?.expiresAt && existing.expiresAt > now) return false;
  storageSet(pasteLockStorageKey, JSON.stringify({session, expiresAt: now + 1500}));
  pasteUploadInFlight = true;
  return true;
}

function pasteTargetSession(event) {
  const panel = event.target?.closest?.('.panel');
  const panelSession = panel?.id?.startsWith('panel-') ? panel.id.slice('panel-'.length) : '';
  if (sessions.includes(panelSession) && activeSessions.includes(panelSession)) return panelSession;
  if (focusedTerminal && activeSessions.includes(focusedTerminal)) return focusedTerminal;
  if (focusedPanelItem && sessions.includes(focusedPanelItem) && activeSessions.includes(focusedPanelItem)) return focusedPanelItem;
  if (lastFocusedTmuxSession && activeSessions.includes(lastFocusedTmuxSession)) return lastFocusedTmuxSession;
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  return activeTmuxSessions.length === 1 ? activeTmuxSessions[0] : null;
}

function nextPasteFilename(mimeType) {
  const stamp = pacificDateStamp();
  const suffix = imageSuffix(mimeType);
  const key = `${stamp}:${suffix}`;
  const next = nextPasteCounter(key);
  return `${stamp}-${String(next).padStart(3, '0')}${suffix}`;
}

function pastedImageFilename(originalName, mimeType) {
  const suffix = imageSuffixFromFilename(originalName) || imageSuffix(mimeType);
  const imageNumber = imageNumberFromFilename(originalName);
  if (Number.isFinite(imageNumber)) {
    return `${pacificDateStamp()}-${String(imageNumber).padStart(3, '0')}${suffix}`;
  }
  return nextPasteFilename(mimeType);
}

function nextPasteCounter(key) {
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key)) + 1;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
  return next;
}

function readPasteCounters() {
  const counters = readStoredJson(pasteCountersStorageKey, {});
  return counters && typeof counters === 'object' ? counters : {};
}

function writePasteCounters(counters) {
  storageSet(pasteCountersStorageKey, JSON.stringify(counters));
}

function pasteCounterValue(counters, key) {
  return Number(counters?.[key]) || 0;
}

function imageNumberFromFilename(filename) {
  const name = pathBasename(filename || '').replace(/\.[A-Za-z0-9]{1,8}$/, '');
  const match = name.match(/(?:^|[^A-Za-z])image[^0-9]*(\d+)(?:[^0-9]|$)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function pasteUploadIndexFromPath(path) {
  const match = pathBasename(path || '').match(/^\d{8}-(\d{3})(?:\.[A-Za-z0-9]{1,8})$/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function imageSuffixFromFilename(filename) {
  const match = String(filename || '').match(/(\.[A-Za-z0-9]{1,8})$/);
  if (!match) return '';
  const suffix = match[1].toLowerCase();
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'].includes(suffix) ? (suffix === '.jpeg' ? '.jpg' : suffix) : '';
}

function pacificDateStamp() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}${values.month}${values.day}`;
}

function imageSuffix(mimeType) {
  const value = String(mimeType || '').toLowerCase();
  if (value.includes('jpeg') || value.includes('jpg')) return '.jpg';
  if (value.includes('gif')) return '.gif';
  if (value.includes('webp')) return '.webp';
  if (value.includes('bmp')) return '.bmp';
  return '.png';
}

async function uploadFiles(session, fileList, options = {}) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyUploadFiles'));
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const totalBytes = files.reduce((total, file) => total + (Number(file?.size) || 0), 0);
  if (uploadMaxBytes > 0 && totalBytes > uploadMaxBytes) {
    statusErr(localizedHtml('status.uploadTooLarge', {selected: formatFileSize(totalBytes), limit: formatFileSize(uploadMaxBytes)}));
    showUploadRsyncRecommendation({session, sizeBytes: totalBytes});
    return;
  }
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const payload = await apiFetchJson(`/api/upload?session=${encodeURIComponent(session)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    const paths = (payload.files || []).map(file => file.path).filter(Boolean);
    if (options.source === 'paste') syncPasteCountersFromPayload(payload);
    activateTab(session, 'terminal');
    const dropPayload = {path: paths[0], paths, kind: 'file'};
    const pathInserted = options.source === 'paste' || terminalDropShouldInsertPathFirst(session, dropPayload);
    const inserted = pathInserted
      ? (options.source === 'paste'
          ? insertPasteUploadReferences(session, payload.files || [], {silent: true})
          : insertUploadPaths(session, paths, {silent: true}))
      : false;
    const uploadResult = showUploadResult(session, payload, inserted);
    if (paths.length && boolSetting('uploads.show_suggestions', true)) {
      const timeoutMs = uploadResult?.expiresAt ? uploadResult.expiresAt - Date.now() : toastDurationMs;
      const shown = showTerminalDropSuggestions(session, dropPayload, options.suggestAt?.x, options.suggestAt?.y, {pathInserted, timeoutMs});
      if (!shown && !pathInserted) insertUploadPaths(session, paths, {silent: true});
    } else if (!pathInserted) {
      insertUploadPaths(session, paths, {silent: true});
    }
    refreshTerminalAfterUpload(session);
    refreshOpenEventLogs();
    refreshTranscripts({force: true});
  } catch (error) {
    const message = t('status.uploadFailed', {error: error?.payload?.error || error});
    statusErr(esc(message));
    showFileTransferError(message, {session});
  }
}

async function uploadEditorFiles(editorTarget, fileList) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyUploadFiles'));
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const totalBytes = files.reduce((total, file) => total + (Number(file?.size) || 0), 0);
  if (uploadMaxBytes > 0 && totalBytes > uploadMaxBytes) {
    statusErr(localizedHtml('status.uploadTooLarge', {selected: formatFileSize(totalBytes), limit: formatFileSize(uploadMaxBytes)}));
    showUploadRsyncRecommendation({item: focusedPanelItem, sizeBytes: totalBytes});
    return;
  }
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const payload = await apiFetchJson(`/api/upload?editor_path=${encodeURIComponent(editorTarget.path)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    syncPasteCountersFromPayload(payload);
    insertEditorPasteUploadReferences(editorTarget, payload.files || []);
  } catch (error) {
    const message = t('status.uploadFailed', {error: error?.payload?.error || error});
    statusErr(esc(message));
    showFileTransferError(message, {item: focusedPanelItem});
  }
}

function refreshTerminalAfterUpload(session) {
  if (!isTmuxSession(session)) return;
  scheduleFit(session);
  refreshTerminal(session);
  requestAnimationFrame(() => {
    scheduleFit(session);
    refreshTerminal(session);
    requestAnimationFrame(() => refreshTerminal(session));
  });
}

function insertUploadPaths(session, paths, options = {}) {
  if (!paths.length) return false;
  const inserted = insertIntoTerminal(session, `${paths.map(shellQuote).join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">${localizedHtml('status.insertedUploadPath', {session: sessionLabel(session)})}</span>`
      : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
  }
  return inserted;
}

function insertPasteUploadReferences(session, files, options = {}) {
  const references = pasteUploadReferences(files);
  if (!references.length) return insertUploadPaths(session, files.map(file => file.path).filter(Boolean), options);
  const inserted = insertIntoTerminal(session, `${references.join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">${localizedHtml('status.insertedPastedImage', {session: sessionLabel(session)})}</span>`
      : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
  }
  return inserted;
}

function pasteUploadReferences(files) {
  return (files || []).map((file, index) => {
    const path = file.path || '';
    if (!path) return '';
    const number = pasteUploadIndexFromPath(path) || index + 1;
    return `[Image #${number}] ${shellQuote(path)}`;
  }).filter(Boolean);
}

function insertEditorPasteUploadReferences(editorTarget, files) {
  const references = markdownImageUploadReferences(files);
  if (!references.length) return false;
  const view = editorTarget?.view;
  if (!view?.state?.doc || typeof view.dispatch !== 'function') return false;
  const selection = view.state.selection?.main || {};
  const docLength = Number(view.state.doc.length) || 0;
  const from = Math.max(0, Math.min(docLength, Number.isFinite(selection.from) ? selection.from : docLength));
  const to = Math.max(from, Math.min(docLength, Number.isFinite(selection.to) ? selection.to : from));
  const insert = references.join('\n');
  view.dispatch({
    changes: {from, to, insert},
    selection: {anchor: from + insert.length},
  });
  view.focus?.();
  return true;
}

function markdownImageUploadReferences(files) {
  return (files || []).map(file => {
    const path = file.relative_path || pathBasename(file.path || '') || file.saved_name || '';
    if (!path) return '';
    return `![image](${markdownLinkTarget(path)})`;
  }).filter(Boolean);
}

function markdownLinkTarget(path) {
  return String(path || '').split('/').map(part => encodeURIComponent(part)).join('/');
}

function syncPasteCountersFromPayload(payload) {
  const files = payload?.files || [];
  for (const file of files) syncPasteCounterFromPath(file.path || file.saved_name || '');
}

function syncPasteCounterFromPath(path) {
  const index = pasteUploadIndexFromPath(path);
  if (!Number.isFinite(index)) return;
  const suffix = imageSuffixFromFilename(path) || imageSuffix('');
  const stampMatch = pathBasename(path).match(/^(\d{8})-/);
  const stamp = stampMatch?.[1] || pacificDateStamp();
  const key = `${stamp}:${suffix}`;
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key), index);
  if (next <= localValue) return;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
}

function insertIntoTerminal(session, text) {
  if (readOnlyMode && !shareWriteMode) {
    statusErr(localizedHtml('status.readOnlyTypeTerminals'));
    return false;
  }
  const item = terminals.get(session);
  if (!item) return false;
  const filtered = stripTerminalQueryResponses(text);
  if (!filtered) return false;
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
  if (shareReplayShellActive && shareWriteMode) {
    const sent = shareSendTerminalInputIntent(session, filtered);
    if (sent && autoFocusEnabled) item.term?.focus?.();
    return sent;
  }
  if (item.socket?.readyState !== WebSocket.OPEN) return false;
  const sendPerf = clientPerfStart('wsSend');
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  clientPerfEnd(sendPerf, {bytes: jsDebugByteLength(filtered)});
  item.lastInputSentAt = clientPerfNow();
  if (autoFocusEnabled) item.term?.focus?.();
  return true;
}

function noteTerminalExplicitInput(session) {
  const item = terminals.get(session);
  if (item) {
    item.lastExplicitInputMark = clientPerfMark(`terminal-keydown:${session}`);
    item.lastExplicitInputAt = clientPerfNow();
  }
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true, syncFinder: false});
}

function terminalDataWithoutPassiveReports(data) {
  return String(data || '')
    .replace(/\x1b\[[IO]/g, '')
    .replace(/\x1b\[<\d+(?:;\d+){2}[mM]/g, '')
    .replace(/\x1b\[M[\s\S]{3}/g, '');
}

function terminalDataShouldAcknowledgeAttention(data) {
  return terminalDataWithoutPassiveReports(data).length > 0;
}

function acknowledgeTerminalAttentionFromTransportInput(session, data, options = {}) {
  if (options.acknowledgeAttention === false) return false;
  if (!terminalDataShouldAcknowledgeAttention(data)) return false;
  if (typeof acknowledgeTerminalAttentionFromUserAction !== 'function') return false;
  return acknowledgeTerminalAttentionFromUserAction(session, null, options.attentionOptions || {});
}

const terminalTmuxPrefixPendingBySession = new Map();
const tmuxWindowReadbackDelayMs = tmuxWindowReadbackMs;
const tmuxWindowReadbackRetryDelayMs = tmuxWindowReadbackRetryMs;
const tmuxWindowReadbackMaxAttempts = 6;
const terminalTmuxWindowRepeatMs = 900;
const terminalTmuxWindowRepeatBySession = new Map();

function terminalTmuxPrefixWindowShortcut(key) {
  const value = String(key || '');
  if (value === 'n') return {label: 'next tmux sub-window', repeatable: true};
  if (value === 'p') return {label: 'previous tmux sub-window', repeatable: true};
  if (value === 'l') return {label: 'last tmux sub-window', requireChanged: true};
  if (value === 'w') return {label: 'tmux sub-window chooser', requireChanged: true};
  if (value === "'") return {label: 'tmux sub-window prompt', requireChanged: true};
  if (value === 'f') return {label: 'tmux find sub-window', requireChanged: true};
  if (/^[0-9]$/.test(value)) return {label: `tmux sub-window ${value}`, windowIndex: value};
  return null;
}

function terminalTmuxAltWindowShortcut(key) {
  const value = String(key || '');
  if (value === 'n') return {label: 'next tmux sub-window', repeatable: true};
  if (value === 'p') return {label: 'previous tmux sub-window', repeatable: true};
  return null;
}

function tmuxWindowSignalReadbackUrl(session) {
  const params = new URLSearchParams();
  params.set('force', '1');
  const target = String(session || '').trim();
  if (target) params.set('session', target);
  return `/api/tmux-signals?${params.toString()}`;
}

function tmuxSignalPayloadData(payload) {
  return payload?.data && typeof payload.data === 'object' ? payload.data : payload;
}

function activeTmuxSignalWindowForSession(session, payload = tmuxSignalState) {
  const sessionText = String(session || '').trim();
  if (!sessionText) return null;
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  return windows.find(windowRecord => tmuxSignalWindowSession(windowRecord) === sessionText && windowRecord?.active === true) || null;
}

function confirmTmuxWindowActiveOverridesFromRawSignals(payload = {}) {
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
    const override = tmuxWindowActiveIndexOverride(session);
    if (!session || activeIndex === null || override === undefined || override === tmuxWindowPendingActiveIndex) continue;
    if (activeIndex === tmuxWindowIndexKey(override)) confirmTmuxWindowActiveIndexOverride(session, activeIndex);
  }
}

function reconcileTmuxWindowDirectTargetGuardsFromRawSignals(payload = {}) {
  if (typeof tmuxWindowDirectTargetGuard !== 'function') return;
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
    const guard = tmuxWindowDirectTargetGuard(session);
    if (!session || activeIndex === null || !guard) continue;
    if (activeIndex === tmuxWindowIndexKey(guard.index)) {
      confirmTmuxWindowDirectTargetGuard(session, activeIndex, {sequence: guard.sequence});
      continue;
    }
  }
}

function transcriptPaneMatchesSignalPane(pane, signalPane) {
  if (!pane || !signalPane) return false;
  const paneTarget = String(pane.target || pane.pane_id || '').trim();
  const signalTarget = String(signalPane.target || signalPane.pane_id || '').trim();
  if (paneTarget && signalTarget && paneTarget === signalTarget) return true;
  const paneWindow = tmuxWindowIndexKey(pane.window ?? pane.window_index);
  const signalWindow = tmuxWindowIndexKey(signalPane.window_index);
  const paneIndex = String(pane.pane ?? pane.pane_index ?? '').trim();
  const signalIndex = String(signalPane.pane_index ?? '').trim();
  return paneWindow !== null && paneWindow === signalWindow && paneIndex && signalIndex && paneIndex === signalIndex;
}

function mergeTranscriptPaneWithSignalPane(pane, signalPane, activeIndex) {
  const windowIndex = tmuxWindowIndexKey(pane?.window ?? pane?.window_index);
  const next = {...pane, window_active: windowIndex !== null && windowIndex === activeIndex};
  if (!signalPane) return next;
  if (signalPane.current_path) next.current_path = normalizeDirectoryPath(signalPane.current_path);
  if (signalPane.current_command) next.command = signalPane.current_command;
  if (signalPane.pane_id) next.pane_id = signalPane.pane_id;
  if (signalPane.target) next.target = signalPane.target;
  if (signalPane.pane_index !== undefined) next.pane = String(signalPane.pane_index);
  next.active = signalPane.active === true;
  return next;
}

function applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord, options = {}) {
  const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
  const info = transcriptMeta.sessions?.[session];
  if (activeIndex === null || !info || !Array.isArray(info.panes)) return false;
  const signalPanes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
  let selectedPane = info.selected_pane || null;
  const panes = info.panes.map(pane => {
    const signalPane = signalPanes.find(item => transcriptPaneMatchesSignalPane(pane, item)) || null;
    const next = mergeTranscriptPaneWithSignalPane(pane, signalPane, activeIndex);
    if (next.window_active && (next.active || !selectedPane || tmuxWindowIndexKey(selectedPane.window ?? selectedPane.window_index) !== activeIndex)) {
      selectedPane = next;
    }
    return next;
  });
  if (!panes.some(pane => pane.window_active) && signalPanes.length) {
    const signalPane = signalPanes.find(item => item.active === true) || signalPanes[0];
    const synthesized = mergeTranscriptPaneWithSignalPane({
      window: windowRecord.window_index,
      window_name: windowRecord.window_name || '',
      pane: signalPane.pane_index ?? '',
      pane_id: signalPane.pane_id || signalPane.target || '',
      target: signalPane.target || signalPane.pane_id || '',
      current_path: signalPane.current_path || '',
      command: signalPane.current_command || '',
      active: signalPane.active === true,
    }, signalPane, activeIndex);
    panes.push(synthesized);
    selectedPane = synthesized;
  }
  const nextInfo = {...info, selected_pane: selectedPane, panes};
  transcriptMeta = {
    ...transcriptMeta,
    sessions: {...(transcriptMeta.sessions || {}), [session]: nextInfo},
  };
  if (options.render !== false) {
    updatePanelHeader(session, nextInfo);
    renderInfoPanel();
    if (typeof refreshTabberPanelsForTmuxWindowChange === 'function') refreshTabberPanelsForTmuxWindowChange();
  }
  return true;
}

function applyTmuxSignalActiveWindowsToTranscriptInfo(payload = {}) {
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  let changed = false;
  const seen = new Set();
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    if (!session || seen.has(session)) continue;
    seen.add(session);
    changed = applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord, {render: false}) || changed;
  }
  if (changed) {
    for (const session of seen) updatePanelHeader(session, transcriptMeta.sessions?.[session]);
    renderInfoPanel();
    if (typeof refreshTabberPanelsForTmuxWindowChange === 'function') refreshTabberPanelsForTmuxWindowChange();
  }
  return changed;
}

async function refreshTmuxWindowActiveFromSignals(session, options = {}) {
  const payload = await apiFetchJson(tmuxWindowSignalReadbackUrl(session), {cache: 'no-store'});
  const rawData = tmuxSignalPayloadData(payload);
  if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return true;
  const expected = tmuxWindowIndexKey(options.expectedIndex);
  const rawWindowRecord = expected !== null ? activeTmuxSignalWindowForSession(session, rawData) : null;
  const data = applyTmuxSignalsPayload(payload) || payload;
  const windowRecord = expected !== null ? rawWindowRecord : activeTmuxSignalWindowForSession(session, data);
  const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
  if (activeIndex === null) return false;
  if (expected !== null && activeIndex !== expected) {
    const override = tmuxWindowActiveIndexOverride(session);
    updateTmuxWindowBarActiveButtons(session, override === tmuxWindowPendingActiveIndex ? null : override);
    return false;
  }
  const previous = tmuxWindowIndexKey(options.previousIndex);
  const retryingChangedWindow = options.requireChanged === true && previous !== null && activeIndex === previous && options.acceptUnchanged !== true;
  if (retryingChangedWindow) {
    updateTmuxWindowBarActiveButtons(session, null);
    return false;
  }
  applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord);
  confirmTmuxWindowActiveIndexOverride(session, activeIndex, {sequence: options.sequence});
  return true;
}

function scheduleTmuxWindowReadback(session, options = {}) {
  const delayMs = Number.isFinite(options.delayMs) ? Math.max(0, options.delayMs) : tmuxWindowReadbackDelayMs;
  const attempt = Number.isFinite(options.attempt) ? Number(options.attempt) : 0;
  const run = () => {
    if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return Promise.resolve(true);
    const acceptUnchanged = options.requireChanged === true && attempt + 1 >= tmuxWindowReadbackMaxAttempts;
    const readback = refreshTmuxWindowActiveFromSignals(session, {...options, acceptUnchanged});
    return Promise.resolve(readback).then(confirmed => {
      if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return;
      if (!confirmed && attempt + 1 < tmuxWindowReadbackMaxAttempts) {
        scheduleTmuxWindowReadback(session, {...options, delayMs: tmuxWindowReadbackRetryDelayMs, attempt: attempt + 1});
      }
    }).catch(error => {
      console.warn('tmux sub-window signal readback failed', error);
      if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return;
      if (attempt + 1 < tmuxWindowReadbackMaxAttempts) {
        scheduleTmuxWindowReadback(session, {...options, delayMs: tmuxWindowReadbackRetryDelayMs, attempt: attempt + 1});
      } else {
        const info = transcriptMeta.sessions?.[session];
        reconcileTmuxWindowActiveIndexOverride(session, info, {expectedIndex: options.expectedIndex, sequence: options.sequence});
      }
    });
  };
  if (delayMs <= 0) return run();
  return new Promise(resolve => {
    setTimeout(() => resolve(run()), delayMs);
  });
}

function noteTerminalTmuxWindowSwitch(session, shortcut) {
  if (!shortcut) return false;
  const directIndex = tmuxWindowNumber(shortcut.windowIndex);
  const previousIndex = tmuxWindowInfoActiveIndex(transcriptMeta.sessions?.[session]);
  const sequence = directIndex !== null
    ? setTmuxWindowActiveIndexOverride(session, directIndex)
    : setTmuxWindowActiveIndexPending(session);
  if (shortcut.repeatable) terminalTmuxWindowRepeatBySession.set(session, Date.now() + terminalTmuxWindowRepeatMs);
  else terminalTmuxWindowRepeatBySession.delete(session);
  statusOk(`${esc(shortcut.label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  const requireChanged = shortcut.requireChanged === true || directIndex === null;
  scheduleTmuxWindowReadback(session, directIndex !== null
    ? {clearActiveIndexOverride: true, expectedIndex: directIndex, sequence}
    : {requireChanged: requireChanged && previousIndex !== null, previousIndex, sequence});
  return true;
}

function observeTerminalTmuxPrefixWindowSwitches(session, data) {
  const text = String(data || '');
  if (!text) return false;
  let pending = terminalTmuxPrefixPendingBySession.get(session) === true;
  let mirrored = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '\x1b' && index + 1 < text.length) {
      const altShortcut = terminalTmuxAltWindowShortcut(text[index + 1]);
      if (altShortcut) {
        mirrored = noteTerminalTmuxWindowSwitch(session, altShortcut) || mirrored;
        index += 1;
        continue;
      }
    }
    const repeatUntil = Number(terminalTmuxWindowRepeatBySession.get(session) || 0);
    const repeatActive = repeatUntil > Date.now();
    const repeatShortcut = repeatActive ? terminalTmuxPrefixWindowShortcut(char) : null;
    if (!pending && repeatShortcut && repeatShortcut.repeatable) {
      mirrored = noteTerminalTmuxWindowSwitch(session, repeatShortcut) || mirrored;
      continue;
    }
    if (pending) {
      mirrored = noteTerminalTmuxWindowSwitch(session, terminalTmuxPrefixWindowShortcut(char)) || mirrored;
      pending = false;
      continue;
    }
    if (char === '\x02') pending = true;
  }
  if (pending) terminalTmuxPrefixPendingBySession.set(session, true);
  else terminalTmuxPrefixPendingBySession.delete(session);
  for (const [key, expires] of terminalTmuxWindowRepeatBySession.entries()) {
    if (Number(expires || 0) <= Date.now()) terminalTmuxWindowRepeatBySession.delete(key);
  }
  return mirrored;
}

function handleTerminalData(session, data, options = {}) {
  const perf = clientPerfStart('term.onData');
  try {
    return handleTerminalDataMeasured(session, data, options);
  } finally {
    clientPerfEnd(perf, {bytes: jsDebugByteLength(data)});
  }
}

function handleTerminalDataMeasured(session, data, options = {}) {
  if (readOnlyMode && !shareWriteMode) return false;
  const filtered = stripTerminalQueryResponses(data);
  if (!filtered) return false;
  const current = terminals.get(session);
  if (current?.lastExplicitInputMark) {
    clientPerfMeasureSinceMark('keydownToTermData', current.lastExplicitInputMark, {bytes: jsDebugByteLength(filtered)});
    current.lastExplicitInputMark = '';
  }
  if (shareReplayShellActive && shareWriteMode) {
    acknowledgeTerminalAttentionFromTransportInput(session, filtered, options);
    shareSendTerminalInputIntent(session, filtered);
    return true;
  }
  const socket = current?.socket;
  if (socket?.readyState !== WebSocket.OPEN) return false;
  observeTerminalTmuxPrefixWindowSwitches(session, filtered);
  acknowledgeTerminalAttentionFromTransportInput(session, filtered, options);
  const sendPerf = clientPerfStart('wsSend');
  socket.send(JSON.stringify({type: 'input', data: filtered}));
  clientPerfEnd(sendPerf, {bytes: jsDebugByteLength(filtered)});
  current.lastInputSentAt = clientPerfNow();
  return true;
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function showUploadResult(session, payload, inserted) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return null;
  const files = payload.files || [];
  const paths = files.map(file => file.path).filter(Boolean);
  const label = files.length === 1 ? (files[0].saved_name || files[0].name || t('popover.kind.file')) : t('files.count', {count: files.length});
  const target = payload.target_dir || '';
  const uploadResultKey = inserted ? 'upload.resultInserted' : 'upload.resultTerminalDisconnected';
  const expiresAt = Date.now() + toastDurationMs;
  const newEntries = files.length
    ? files.map(file => {
      const name = file.saved_name || file.name || t('popover.kind.file');
      const destination = pathBasename(file.path || target) || target;
      return {
        id: ++uploadResultSequence,
        text: t(uploadResultKey, {name, destination}),
        path: file.path || '',
        expiresAt,
      };
    })
    : [{
      id: ++uploadResultSequence,
      text: t(uploadResultKey, {name: label, destination: pathBasename(target) || target}),
      path: target,
      expiresAt,
    }];
  const existing = uploadResultsBySession.get(session) || [];
  const active = [...existing.filter(entry => entry.expiresAt > Date.now()), ...newEntries].slice(-8);
  uploadResultsBySession.set(session, active);
  renderUploadResult(session);
  return {expiresAt};
}

function ensureUploadResultShell(session, node) {
  return ensureToastShell(node, {
    title: t('upload.resultTitle', {session: sessionLabel(session)}),
    closeLabel: t('upload.hideStatus'),
    keepLabel: t('upload.keepStatus'),
    onKeep: () => keepUploadResult(session),
    onClose: () => hideUploadResult(session),
  });
}

function keepUploadResult(session) {
  const entries = uploadResultsBySession.get(session) || [];
  for (const entry of entries) entry.expiresAt = Number.POSITIVE_INFINITY;
  uploadResultsBySession.set(session, entries);
  if (uploadCleanupTimers.has(session)) {
    clearTimeout(uploadCleanupTimers.get(session));
    uploadCleanupTimers.delete(session);
  }
}

function scheduleUploadResultCleanup(session, active, now) {
  if (uploadCleanupTimers.has(session)) clearTimeout(uploadCleanupTimers.get(session));
  const delay = Math.max(1, Math.min(...active.map(entry => entry.expiresAt - now)));
  uploadCleanupTimers.set(session, window.setTimeout(() => {
    uploadCleanupTimers.delete(session);
    renderUploadResult(session);
  }, delay));
}

function renderUploadResult(session) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
  const now = Date.now();
  const active = (uploadResultsBySession.get(session) || []).filter(entry => entry.expiresAt > now).slice(-8);
  uploadResultsBySession.set(session, active);
  if (!active.length) {
    node.hidden = true;
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    if (uploadCleanupTimers.has(session)) {
      clearTimeout(uploadCleanupTimers.get(session));
      uploadCleanupTimers.delete(session);
    }
    return;
  }
  const textNode = ensureUploadResultShell(session, node);
  if (!textNode) return;
  const paths = active.map(entry => entry.path).filter(Boolean);
  node.hidden = false;
  textNode.title = paths.join('\n');
  renderToastLines(textNode, active.map(entry => ({
    text: entry.text,
    countdownMs: entry.expiresAt - now,
  })));
  scheduleUploadResultCleanup(session, active, now);
}

function hideUploadResult(session) {
  uploadResultsBySession.delete(session);
  if (uploadCleanupTimers.has(session)) {
    clearTimeout(uploadCleanupTimers.get(session));
    uploadCleanupTimers.delete(session);
  }
  const node = document.getElementById(`upload-${session}`);
  if (node) {
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    node.hidden = true;
  }
}

function updatePanelSlot(panel, session, slot) {
  panel.dataset.slot = slot;
  panel.dataset.layoutItem = session;
  const head = panel.querySelector('.panel-head');
  if (head) head.dataset.dragSlot = slot;
  if (isFileEditorItem(session)) renderFileEditorPanel(panel, session, {updateActiveFile: !dockviewLayoutActive(), captureViewState: false});
  updatePaneExpandButton(panel, session);
  if (!hideDockviewInnerPaneTabs(panel)) updatePaneTabStrip(panel, slot);
  updatePanelInactiveOverlays();
}

function updatePaneExpandButton(panel, session) {
  const button = panel.querySelector('[data-pane-expand]');
  if (button) button.hidden = !canPaneExpand(session);
}

function syncPanelVisibility(previousActive = []) {
  const visible = new Set(activeSessions);
  for (const session of sessions) {
    if (!visible.has(session)) {
      stopTranscriptStream(session);
      stopSummaryStream(session);
      if (focusedTerminal === session) focusedTerminal = null;
    }
    updateTypingIndicator(session);
  }
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`terminal-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) scheduleFit(session);
  }
}

function activateTab(session, name, options = {}) {
  setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
  if (name !== 'transcript') stopTranscriptStream(session);
  if (name !== 'summary') stopSummaryStream(session);
  document.querySelectorAll(`[data-tab="${session}"]`).forEach(button => {
    button.classList.toggle(CLS.active, button.dataset.tabName === name);
  });
  document.querySelectorAll(`[data-panel-tab-overflow="${session}"]`).forEach(button => {
    button.classList.toggle(CLS.active, ['transcript', 'summary', 'events'].includes(name));
  });
  for (const tabName of ['terminal', 'transcript', 'summary', 'events']) {
    const pane = document.getElementById(`${tabName}-pane-${session}`);
    if (pane) pane.classList.toggle(CLS.active, tabName === name);
  }
  updateTypingIndicator(session);
  if (name === 'terminal') {
    scheduleFit(session);
    setTimeout(() => refreshTerminal(session), terminalRefreshAfterTabSelectMs);
    scheduleTerminalBlankScreenRefresh(session, {reason: 'terminal-tab'});
    if (options.userInitiated) focusTerminalFromUserAction(session);
    else focusTerminalWhenAutoFocus(session, 25);
  } else {
    clearFocusedTerminal(session);
  }
  if (name === 'transcript') {
    startTranscriptStream(session, {scrollBottom: true});
  }
  if (name === 'summary') startSummaryStream(session);
  if (name === 'events') refreshEventLog(session);
  if (!shareViewMode && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function tmuxWindow(session, key, label) {
  if (readOnlyMode) {
    statusErr(localizedHtml('terminal.connection.readonlyTmuxWindow'));
    return;
  }
  const directIndex = tmuxWindowNumber(key?.windowIndex);
  if (directIndex !== null) {
    const previousInfo = transcriptMeta.sessions?.[session] || null;
    const sequence = setTmuxWindowActiveIndexOverride(session, directIndex);
    statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
    scheduleFit(session);
    focusTerminalFromUserAction(session, 75);
    apiFetchJson(`/api/tmux-window?session=${encodeURIComponent(session)}&window=${encodeURIComponent(String(directIndex))}`, {method: 'POST'})
      .then(() => {
        if (!tmuxWindowSwitchSequenceMatches(session, sequence)) return;
        scheduleTmuxWindowReadback(session, {delayMs: 0, clearActiveIndexOverride: true, expectedIndex: directIndex, sequence});
      })
      .catch(error => {
        if (!clearTmuxWindowActiveIndexOverride(session, {sequence, clearDirectTarget: true})) return;
        if (previousInfo) {
          transcriptMeta = {
            ...transcriptMeta,
            sessions: {...(transcriptMeta.sessions || {}), [session]: previousInfo},
          };
          updatePanelHeader(session, previousInfo);
          renderInfoPanel();
        } else {
          reconcileTmuxWindowActiveIndexOverride(session, transcriptMeta.sessions?.[session], {sequence});
        }
        statusErr(localizedHtml('terminal.window.failed', {error: error.message || error}));
      });
    return;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) {
    statusErr(terminalNotConnectedHtml(session));
    return;
  }
  const previousIndex = tmuxWindowInfoActiveIndex(transcriptMeta.sessions?.[session]);
  const sequence = setTmuxWindowActiveIndexPending(session);
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  scheduleTmuxWindowReadback(session, {requireChanged: previousIndex !== null, previousIndex, sequence});
}

async function ensureTerminalRunning(session) {
  const key = String(session || '');
  const existing = terminalStartupPromises.get(key);
  if (existing) return existing;
  const promise = (async () => {
    const item = terminals.get(session);
    const readyState = item?.socket?.readyState;
    const container = document.getElementById(terminalDomId(session));
    const boundToCurrentContainer = Boolean(item?.term && container?.isConnected && item.container === container);
    if (item && boundToCurrentContainer && readyState !== undefined && readyState !== WebSocket.CLOSING && readyState !== WebSocket.CLOSED) return;
    if (readOnlyMode) {
      startTerminal(session);
      return;
    }
    const knownFromTranscriptPayload = Boolean(transcriptMetaLoaded && transcriptMeta.sessions?.[session]);
    const ensured = knownFromTranscriptPayload || await ensureSession(session);
    if (!ensured) {
      const container = document.getElementById(terminalDomId(session));
      if (container) container.innerHTML = `<pre class="terminal-error">${localizedHtml('terminal.connection.sessionUnavailableRetry', {session: sessionLabel(session)})}</pre>`;
      return;
    }
    startTerminal(session);
  })();
  terminalStartupPromises.set(key, promise);
  try {
    return await promise;
  } finally {
    if (terminalStartupPromises.get(key) === promise) terminalStartupPromises.delete(key);
  }
}

function connectTerminalSocket(session, item) {
  if (!item?.term || !item?.container) return;
  if (item.socket && item.socket.readyState !== WebSocket.CLOSED && item.socket.readyState !== WebSocket.CLOSING) return;
  const socket = new WebSocket(wsUrl(session));
  socket.binaryType = 'arraybuffer';
  item.socket = socket;
  item.manualClose = false;
  socket.onopen = () => {
    clearTerminalRemovalLatency('session', session);
    item.terminalOutputSeen = false;
    item.reconnectAttempt = 0;
    dismissTerminalConnectionToasts(session);
    if (terminalIsVisible(session, item.container)) {
      scheduleFit(session);
      scheduleTerminalBlankScreenRefresh(session, {reason: 'socket-open'});
      if (!shareViewMode) scheduleRemoteResize(session, shareRemoteResizeAfterSocketOpenMs);
    }
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (terminals.get(session) !== item || !item.term) return;
    try {
      const dataBytes = event.data instanceof ArrayBuffer ? event.data.byteLength : jsDebugByteLength(event.data);
      const inputSentAt = Number(item.lastInputSentAt || 0);
      if (inputSentAt > 0) {
        recordClientPerfCounter('echoToTermWrite', clientPerfNow() - inputSentAt, {bytes: dataBytes});
        item.lastInputSentAt = 0;
      }
      const writePerf = clientPerfStart('xtermWrite');
      if (shareViewMode) {
        handleShareViewSocketMessage(session, item, event.data);
      } else if (event.data instanceof ArrayBuffer) {
        item.term.write(new Uint8Array(event.data));
      } else {
        item.term.write(String(event.data));
      }
      clientPerfEnd(writePerf, {bytes: dataBytes});
      const firstOutput = item.terminalOutputSeen !== true;
      item.terminalOutputSeen = true;
      item.fileUnderlineController?.schedule?.({reason: 'output'});
      if (firstOutput) scheduleTerminalBlankScreenRefresh(session, {reason: 'first-output'});
      scheduleTerminalAttentionHighlight(session);
    } catch (_) {
      if (terminals.get(session) === item) closeTerminalItem(session, item);
    }
  };
  socket.onclose = event => {
    if (item.manualClose || terminals.get(session) !== item) return;
    postEvent(session, 'terminal_disconnected', `terminal disconnected from ${session}`, {});
    clearFocusedTerminal(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
    // Confirm once before reconnecting: a dead tmux session is pruned without the old reconnect
    // backoff loop, while a live session still reconnects after a transient close.
    confirmSessionGoneOrReconnect(session, item, event);
  };
  socket.onerror = () => {
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
}

function shareSocketMessage(data) {
  if (typeof data !== 'string') return null;
  try {
    return JSON.parse(data);
  } catch (_) {
    return null;
  }
}

function shareTerminalBytesFromMessage(session, message) {
  if (!message || message.ch !== 'term' || message.pane !== session || typeof message.data !== 'string') {
    return null;
  }
  const raw = atob(message.data);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

function handleShareViewSocketMessage(session, item, data) {
  const message = shareSocketMessage(data);
  if (!message) return;
  if (message.ch === 'ui') {
    applyShareUiMessage(message);
    return;
  }
  if (message.ch === 'ptr') {
    renderSharePointerGhost(message.payload && typeof message.payload === 'object' ? message.payload : message);
    return;
  }
  const bytes = shareTerminalBytesFromMessage(session, message);
  if (bytes) {
    item.shareTerminalBytesReceived = true;
    item.shareTerminalLastByteAt = Date.now();
    item.shareTerminalByteCount = Math.max(0, Math.round(Number(item.shareTerminalByteCount) || 0)) + bytes.length;
    item.term.write(bytes);
  }
}

function bindTerminalContainerForSession(session, term, container) {
  if (!session || !term || !container) return;
  if (container.dataset?.terminalHandlersBound === session) return;
  if (container.dataset) container.dataset.terminalHandlersBound = session;
  installTerminalContextMenu(session, term, container);
  installTerminalCopyShortcut(session, term, container);
  installTerminalFileDrop(session, container);
  enableTerminalScroll(session, term, container);
  observeTerminalResize(session, container);
  container.addEventListener('focusin', () => {
    setFocusedTerminal(session);
  });
  container.addEventListener('focusout', () => {
    clearFocusedTerminal(session);
  });
  container.addEventListener('copy', event => {
    copyTerminalSelectionToClipboardEvent(session, term, event, container);
  }, {capture: true});
  container.addEventListener('keydown', () => noteTerminalExplicitInput(session), {capture: true});
  container.addEventListener('paste', () => noteTerminalExplicitInput(session), {capture: true});
  container.addEventListener('beforeinput', () => noteTerminalExplicitInput(session), {capture: true});
}

function terminalUnicode11AddonCtor() {
  return window.Unicode11Addon?.Unicode11Addon || null;
}

function applyTerminalUnicode11Addon(term) {
  const Unicode11AddonCtor = terminalUnicode11AddonCtor();
  if (!Unicode11AddonCtor || typeof term?.loadAddon !== 'function' || !term?.unicode) return false;
  try {
    const addon = new Unicode11AddonCtor();
    term.loadAddon(addon);
    term.unicode.activeVersion = '11';
    return term.unicode.activeVersion === '11';
  } catch (error) {
    console.warn('xterm Unicode 11 width addon failed', error);
    return false;
  }
}

function startTerminal(session) {
  const existing = terminals.get(session);
  const reconnectAttempt = existing?.reconnectAttempt || 0;
  const container = document.getElementById(terminalDomId(session));
  if (!container) return;
  if (existing?.term && existing.container === container) {
    connectTerminalSocket(session, existing);
    return;
  }
  if (existing) {
    closeTerminalItem(session, existing);
    terminals.delete(session);
  }
  const TerminalCtor = window.Terminal?.Terminal || window.Terminal;
  if (!TerminalCtor) {
    container.innerHTML = '<pre class="terminal-error">xterm.js failed to load from /static/xterm.js. Terminal cannot attach.</pre>';
    statusErr(localizedHtml('status.xtermUnavailable'));
    return;
  }
  container.innerHTML = '';
  const size = shareHostTerminalSize(session) || estimateTerminalSize(container);
  const baseTheme = terminalThemeForGlobalTheme();
  const term = new TerminalCtor({
    cols: size.cols,
    rows: size.rows,
    cursorBlink: typeof terminalCursorBlinkEnabled === 'function' ? terminalCursorBlinkEnabled() : true,
    convertEol: false,
    fontFamily: terminalFontFamily,
    fontSize: terminalFontSize,
    letterSpacing: 0,
    lineHeight: 1.0,
    scrollback: terminalScrollback,
    disableStdin: readOnlyMode && !shareWriteMode,
    theme: terminalThemeForSession(session, baseTheme),
    minimumContrastRatio: terminalMinimumContrastRatio(),
    // Unicode11Addon uses xterm's unicode width service; this local xterm build gates it behind proposed API opt-in.
    allowProposedApi: true,
    // Alt-screen TUIs (claude, vim, less) enable mouse reporting, which makes xterm send drags to the app
    // instead of selecting text — so Ctrl-C/Cmd-C has nothing to copy. Option-click (Mac) forces a text
    // selection anyway; on Linux/Windows hold Shift while dragging (xterm's built-in bypass).
    macOptionClickForcesSelection: true,
  });
  applyTerminalUnicode11Addon(term);
  term.open(container);
  // match the container bg to the terminal theme so every pane shares one white.
  applyTerminalContainerTheme(container, baseTheme);
  installTerminalLinkProvider(session, term);
  installTerminalOsc52Bridge(session, term);   // Claude/tmux OSC 52 clipboard escapes -> browser clipboard
  const openedSize = shareHostTerminalSize(session) || estimateTerminalSize(container, term);
  if (term.cols !== openedSize.cols || term.rows !== openedSize.rows) {
    term.resize(openedSize.cols, openedSize.rows);
  }
  const item = {
    term,
    socket: null,
    container,
    manualClose: false,
    reconnectAttempt,
    reconnectTimer: null,
    resizeTimer: null,
    scrollTimer: null,
    pendingScrollLines: 0,
    shareTerminalBytesReceived: false,
    shareTerminalLastByteAt: 0,
    shareTerminalByteCount: 0,
    shareTerminalLastResetAt: 0,
    shareTerminalSkippedResetCount: 0,
    blankScreenRefreshTimer: 0,
    blankScreenRefreshAttempts: 0,
    attentionHighlightFrame: 0,
    terminalOutputSeen: false,
    fileUnderlineController: null,
  };
  terminals.set(session, item);
  item.fileUnderlineController = installTerminalFileReferenceUnderlines(session, term, container);
  bindTerminalContainerForSession(session, term, container);
  term.onFocus?.(() => {
    setFocusedTerminal(session);
  });
  term.onBlur?.(() => {
    clearFocusedTerminal(session);
  });
  // xterm can emit focus and mouse-tracking bytes from hover. Keep Differ commits on DOM
  // keydown/paste/beforeinput and pane pointerdown, not on the terminal transport stream.
  term.onData(data => handleTerminalData(session, data));
  if (focusedTerminal === session && terminalPaneIsActive(session)) focusTerminalDom(session);
  connectTerminalSocket(session, item);
}

function updateTypingIndicator(session) {
  const item = terminals.get(session);
  const container = item?.container || document.getElementById(terminalDomId(session));
  const pane = document.getElementById(`terminal-pane-${session}`);
  const panel = document.getElementById(panelDomId(session));
  const ready = Boolean(
    item?.socket?.readyState === WebSocket.OPEN
    && focusedTerminal === session
    && pane?.classList.contains(CLS.active)
  );
  container?.classList.toggle('typing-ready', ready);
  panel?.classList.toggle('typing-ready-pane', ready);
  panel?.classList.toggle('yolo-ready-pane', ready && autoApproveStates.get(session)?.enabled === true);
}

function updateStatus() {
  if (activeSessions.length === 0) {
    statusEl.textContent = t('terminal.status.noSessionSelected');
    statusEl.removeAttribute('title');
    return;
  }
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  if (!activeTmuxSessions.length) {
    statusEl.textContent = t('terminal.status.viewShown', {view: infoTabLabel()});
    statusEl.removeAttribute('title');
    return;
  }
  let open = 0;
  for (const session of activeTmuxSessions) {
    const item = terminals.get(session);
    if (item?.socket?.readyState === WebSocket.OPEN) open += 1;
  }
  const total = activeTmuxSessions.length;
  statusEl.textContent = open === total ? '' : t('terminal.connection.connShort', {open, total});
  statusEl.title = open === total ? '' : t('terminal.connection.socketsTitle', {open, total});
}

async function toggleAutoApprove(session) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.yoloReadOnlyChange'));
    return;
  }
  const state = autoApproveStates.get(session) || {};
  const current = state.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.yoloReadOnlyChange'));
    return;
  }
  try {
    const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    autoApproveStates.set(session, payload);
    updateDocumentTitle();
    updateSessionButtonStates();
    renderAutoApproveButton(session, payload);
    scheduleTerminalAttentionHighlight(session);
    scheduleShareUiStatePublish();
    statusEl.innerHTML = payload.enabled
      ? `<span class="ok">${localizedHtml('status.yoloEnabledFor', {session: sessionLabel(session)})}</span>`
      : `<span class="ok">${localizedHtml('status.yoloDisabledFor', {session: sessionLabel(session)})}</span>`;
  } catch (error) {
    const payload = error?.payload || {};
    if (error?.status) {
      if (payload?.target || payload?.session) {
        autoApproveStates.set(session, payload);
        updateDocumentTitle();
        updateSessionButtonStates();
        renderAutoApproveButton(session, payload);
        scheduleTerminalAttentionHighlight(session);
        scheduleShareUiStatePublish();
      }
      statusErr(localizedHtml('status.yoloApprovalFailed', {error: payload.error || t('status.yoloApprovalFailedDefault')}));
      return;
    }
    statusErr(localizedHtml('status.yoloRequestFailed', {error}));
  }
}

async function refreshAutoStatuses() {
  const result = await loadAutoStatuses({render: false});
  renderAutoApproveStatusSurfaces(result);
  bindClipboardPaste();
  refreshOpenEventLogs();
}

async function loadAutoStatuses(options = {}) {
  let result = null;
  try {
    const payload = await apiFetchJson('/api/auto-approve');
    result = applyAutoApprovePayload(payload, options);
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
    result = {applied: false, sessionsChanged: false, previousActive: activeSessions.slice()};
  }
  if (options.render !== false && !result?.rendered) renderAutoApproveStatusSurfaces(result);
  return result;
}

function renderAutoApproveStatusSurfaces(result = {}) {
  const perf = clientPerfStart('autoStatusRender');
  try {
    if (result?.sessionsChanged) renderPanels(result.previousActive || activeSessions.slice());
    else if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
    updateDocumentTitle();
  // Re-toggle the YO markers' working class from the fresh states on the SAME poll the title updates,
  // so a finished/idle pane's marker stops spinning instead of lingering (the transcript poll path
  // updated the title but never re-synced the markers).
    renderAutoApproveButtons();
    updateSessionButtonStates();
    refreshActivePanelHeaders();
    trackSessionStateChanges();
    syncTerminalAttentionHighlights();
    scheduleShareUiStatePublish();
    if (result && typeof result === 'object') result.rendered = true;
  } finally {
    clientPerfEnd(perf, {sessions: sessions.length});
  }
}

function applyAutoApprovePayload(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const previousActive = activeSessions.slice();
  const sessionsChanged = Array.isArray(payload.session_order) ? updateSessionList(payload.session_order) : false;
  if (payload.rules) {
    yoloRulesPayload = payload.rules;
    renderPreferencesPanels();
  }
  for (const session of sessions) {
    const state = payload.sessions?.[session] || {target: session, enabled: false, last_action: 'off'};
    autoApproveStates.set(session, state);
    reconcileTmuxWindowMetadataFromAgentWindows(session, state);
  }
  const result = {applied: true, sessionsChanged, previousActive};
  if (options.render === false) return result;
  renderAutoApproveStatusSurfaces(result);
  return result;
}

function reconcileTmuxWindowMetadataFromAgentWindows(session, payload = {}) {
  const info = transcriptMeta.sessions?.[session];
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const agentWindows = typeof agentWindowPayloadRows === 'function'
    ? agentWindowPayloadRows(payload.agent_windows)
    : [];
  if (!info || !agentWindows.length) return false;
  const paneWindows = new Set(panes.map(pane => tmuxWindowIndexKey(pane?.window)).filter(index => index !== null));
  const missing = agentWindows.filter(agent => {
    const index = tmuxWindowIndexKey(agent?.window_index ?? agent?.window);
    return index !== null && !paneWindows.has(index);
  });
  if (!missing.length) return false;
  const reconciledPanes = [...panes, ...missing.map(agent => {
    const window = tmuxWindowIndexKey(agent.window_index ?? agent.window);
    const name = String(agent.window_name || agent.kind || 'window').trim();
    const active = agent.current === true || agent.window_active === true;
    return {
      window,
      window_name: name,
      process_label: name,
      target: agent.pane_target || '',
      pane_id: agent.pane_target || '',
      active,
      window_active: active,
      pid: agent.pid || null,
      process_label_pid: agent.pid || null,
    };
  })].sort((left, right) => Number(left.window) - Number(right.window));
  transcriptMeta = {
    ...transcriptMeta,
    sessions: {
      ...(transcriptMeta.sessions || {}),
      [session]: {
        ...info,
        panes: reconciledPanes,
        selected_pane: reconciledPanes.find(pane => pane.window_active === true) || info.selected_pane,
      },
    },
  };
  return true;
}

function autoApproveOwnerLabel(payload) {
  const owner = payload?.lock_owner || {};
  const pid = owner.pid ? `pid ${owner.pid}` : '';
  const root = owner.project_root || '';
  return [pid, root].filter(Boolean).join(' ') || payload?.last_action || t('yolo.ownerFallback');
}

function renderAutoApproveButtons() {
  for (const session of sessions) {
    const state = autoApproveStates.get(session) || {target: session, enabled: false, last_action: 'off'};
    renderAutoApproveButton(session, state);
  }
}

function renderAutoApproveButton(session, payload) {
  const buttons = document.querySelectorAll(`[data-yolo-session="${cssEscape(session)}"]`);
  const enabled = payload?.enabled === true;
  const locked = payload?.locked === true && !enabled;
  const working = sessionYoloIsWorking(session, payload);
  for (const button of buttons) {
    syncPressedButton(button, enabled);
    button.classList.toggle('inactive', !enabled && !locked);
    button.classList.toggle('locked', locked);
    button.classList.toggle('working', working);
    button.closest('.pane-tab')?.classList.remove('is-working');
    button.textContent = t('brand.marker');
    const action = payload?.last_action ? t('yolo.actionSuffix', {action: payload.last_action}) : '';
    const readonly = readOnlyMode ? t('yolo.readonlySuffix') : '';
    const buttonLabel = enabled
      ? t('yolo.buttonOnForSession', {session: sessionLabel(session), action, readonly})
      : locked
        ? t('yolo.buttonOwnedBy', {owner: autoApproveOwnerLabel(payload)})
      : t('yolo.buttonOffForSession', {session: sessionLabel(session), readonly});
    button.setAttribute('aria-label', buttonLabel);
    if (button.closest('.tabber-session-tab')) button.removeAttribute('title');
    else button.title = buttonLabel;
  }
  updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  updateTypingIndicator(session);
}

function startSummaryStream(session) {
  stopSummaryStream(session);
  const node = document.getElementById(summaryDomId(session));
  if (!node) return;
  if (readOnlyMode) {
    node.textContent = t('transcript.adminRequired');
    statusErr(`${esc(t('transcript.adminStatus'))}`);
    return;
  }
  // Accumulate the raw streamed text and render it through the markdown pipeline
  // (coalesced to one render per frame) so the panel shows formatted markdown,
  // not raw `##`/`**`/backticks. The leading `[codex]` status lines render as
  // plain paragraphs, then the model's markdown summary renders properly.
  let raw = 'Starting structured Codex summary for the last hour…\n\n';
  let renderScheduled = false;
  const renderSummary = () => {
    renderScheduled = false;
    renderMarkdownPreviewInto(node, raw);
    node.scrollTop = node.scrollHeight;
  };
  const appendSummary = text => {
    raw += text;
    if (!renderScheduled) {
      renderScheduled = true;
      requestAnimationFrame(renderSummary);
    }
  };
  renderSummary();
  const source = new EventSource(`/api/summary-stream?session=${encodeURIComponent(session)}&lookback=${60 * 60}`);
  summaryStreams.set(session, source);
  source.addEventListener('meta', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    const fallback = payload.fallback ? 'recent transcript tail' : 'last hour';
    const projectCount = Array.isArray(payload.projects) ? payload.projects.length : 0;
    appendSummary(`[codex] summarizing ${fallback} for ${payload.focus_root || session}\n`);
    if (payload.summary_model) appendSummary(`[codex] model: ${payload.summary_model}; effort: ${payload.summary_effort || 'default'}\n`);
    appendSummary(`[codex] project inventory: ${projectCount} sessions\n\n`);
  });
  source.addEventListener('log', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.text) appendSummary(`[codex] ${payload.text}\n`);
  });
  source.addEventListener('delta', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.text) appendSummary(payload.text);
  });
  source.addEventListener('summary_error', event => {
    // A bad frame must still tear the stream down (this is the error path); guard the read but always stop
    // — an unguarded JSON.parse throw here would leak the EventSource.
    const payload = safeJsonParse(event.data, null);
    appendSummary(`\n[error] ${payload?.error || 'summary failed'}\n`);
    stopSummaryStream(session);
  });
  source.addEventListener('done', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.return_code && payload.return_code !== 0) {
      appendSummary(`\n[codex exited ${payload.return_code}]\n`);
    }
    stopSummaryStream(session);
  });
  source.onerror = () => {
    if (summaryStreams.get(session) !== source) return;
    appendSummary(`\n${t('terminal.summary.streamDisconnected')}\n`);
    stopSummaryStream(session);
  };
}

function stopSummaryStream(session) {
  const source = summaryStreams.get(session);
  if (!source) return;
  source.close();
  summaryStreams.delete(session);
}

function reloadIsSafe() {
  // Don't yank the page out from under unsaved work or active typing.
  for (const file of openFiles.values()) {
    if (file?.dirty) return false;
  }
  const active = document.activeElement;
  if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return false;
  return true;
}

let selfUpdateAvailableTarget = '';
let selfUpdateReloadPending = false;
let selfUpdateReloadTarget = '';
let selfUpdateReloadAttempts = 0;
let selfUpdateReloadTimer = null;
let selfUpdateReloadDeferredToastShown = false;
const selfUpdateReloadPollMs = 1500;
const selfUpdateReloadMaxAttempts = 120;

function dismissToastNode(node) {
  if (!node) return;
  const alertId = Number(node.dataset?.alertId || 0);
  if (alertId) removeAttentionAlert(alertId);
  else node.remove?.();
}

function dismissUpdateAvailableToasts(ownerToast = null) {
  const toasts = new Set();
  if (ownerToast) toasts.add(ownerToast);
  for (const node of document.querySelectorAll?.('.toast-update') || []) {
    toasts.add(node);
  }
  for (const node of toasts) dismissToastNode(node);
}

function hideUpdateBadge() {
  const badge = document.querySelector('[data-update-badge]');
  if (!badge) return;
  badge.hidden = true;
  delete badge.dataset.updateTarget;
}

function markSelfUpdateReloadPending(target = '') {
  selfUpdateReloadPending = true;
  selfUpdateReloadTarget = String(target || selfUpdateReloadTarget || '').trim();
  selfUpdateReloadAttempts = 0;
  selfUpdateReloadDeferredToastShown = false;
  if (selfUpdateReloadTarget) serverVersionReloadHandled = selfUpdateReloadTarget;
  document.getElementById('serverUpdateBanner')?.remove();
}

function selfUpdateOwnsServerVersion(serverVersion) {
  if (!selfUpdateReloadPending) return false;
  if (!selfUpdateReloadTarget || serverVersion === selfUpdateReloadTarget) {
    if (serverVersion) serverVersionReloadHandled = serverVersion;
    return true;
  }
  return false;
}

function selfUpdateReloadDeferredReason() {
  for (const file of openFiles.values()) {
    if (file?.dirty) return 'unsaved edits';
  }
  const active = document.activeElement;
  if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {
    return 'active typing';
  }
  return 'current activity';
}

function showSelfUpdateReloadDeferredToast() {
  if (selfUpdateReloadDeferredToastShown) return;
  selfUpdateReloadDeferredToastShown = true;
  showToast('Software Update', [
    `Reload deferred because of ${selfUpdateReloadDeferredReason()}. YOLOmux will reload when it is safe.`,
  ], {className: 'attention-alert toast toast-update'});
}

function maybeReloadAfterSelfUpdate() {
  if (!selfUpdateReloadPending) return false;
  if (reloadIsSafe()) {
    location.reload();
    return true;
  }
  showSelfUpdateReloadDeferredToast();
  scheduleSelfUpdateReloadPoll();
  return false;
}

function scheduleSelfUpdateReloadPoll(delayMs = selfUpdateReloadPollMs) {
  if (!selfUpdateReloadPending) return;
  if (selfUpdateReloadTimer) clearTimeout(selfUpdateReloadTimer);
  selfUpdateReloadTimer = window.setTimeout(() => {
    selfUpdateReloadTimer = null;
    pollSelfUpdateReload();
  }, delayMs);
}

async function pollSelfUpdateReload() {
  if (!selfUpdateReloadPending) return false;
  selfUpdateReloadAttempts += 1;
  try {
    const res = await fetch(`/api/ping?selfUpdate=${Date.now()}`, {cache: 'no-store'});
    if (res && res.ok !== false) return maybeReloadAfterSelfUpdate();
  } catch (_error) {
    // The old process may already be gone. Keep polling until the replacement server answers.
  }
  if (selfUpdateReloadAttempts >= selfUpdateReloadMaxAttempts) {
    selfUpdateReloadPending = false;
    showToast('Software Update', ['Update installed, but YOLOmux did not answer after restart. Reload when it is reachable.']);
    return false;
  }
  scheduleSelfUpdateReloadPoll();
  return false;
}

function startSelfUpdateReloadPolling(target = '') {
  markSelfUpdateReloadPending(target);
  scheduleSelfUpdateReloadPoll(0);
}

function showServerUpdateBanner(version) {
  let banner = document.getElementById('serverUpdateBanner');
  if (banner && banner.parentElement) {
    banner.dataset.version = version;
    return;
  }
  banner = [...(document.body?.children || [])].find(node => node?.id === 'serverUpdateBanner') || null;
  if (banner) {
    banner.dataset.version = version;
    return;
  }
  banner = document.createElement('div');
  banner.id = 'serverUpdateBanner';
  banner.className = 'server-update-banner';
  banner.dataset.version = version;
  const msg = document.createElement('span');
  msg.className = 'server-update-banner-msg';
  msg.textContent = t('update.available');
  const reload = document.createElement('button');
  reload.type = 'button';
  reload.className = 'server-update-banner-reload';
  reload.textContent = t('update.reload');
  reload.addEventListener('click', () => location.reload());
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'server-update-banner-dismiss';
  dismiss.setAttribute('aria-label', t('update.dismiss'));
  dismiss.textContent = t('update.dismiss');
  dismiss.addEventListener('click', () => banner.remove());
  banner.append(msg, reload, dismiss);
  document.body.appendChild(banner);
}

function maybeHandleServerVersionChange(serverVersion, serverClientRevision = '') {
  // The boot version/revision only update on page load; this lets a long-lived
  // client learn that the running server no longer matches the bundle that booted this tab.
  const normalizedServerVersion = String(serverVersion || '');
  const bootVersion = String(bootstrap.version || '');
  const versionChanged = normalizedServerVersion && normalizedServerVersion !== bootVersion;
  const versionReloadAllowed = versionChanged && updateNotificationAllowsVersion(bootVersion, normalizedServerVersion);
  const bootClientRevision = String(bootstrap.clientRevision || '');
  const normalizedClientRevision = String(serverClientRevision || '');
  const reloadNotificationsEnabled = normalizeUpdateNotificationLevel(updateNotificationLevelSetting()) !== 'none';
  const clientRevisionChanged = reloadNotificationsEnabled && normalizedClientRevision && bootClientRevision && normalizedClientRevision !== bootClientRevision;
  if (!versionReloadAllowed && !clientRevisionChanged) return;
  if (versionReloadAllowed && selfUpdateOwnsServerVersion(normalizedServerVersion)) return;
  const reloadKey = versionReloadAllowed ? `version:${normalizedServerVersion}` : `client:${normalizedClientRevision}`;
  if (serverVersionReloadHandled === reloadKey) return;
  serverVersionReloadHandled = reloadKey;
  if (boolSetting('general.reload_on_update_auto', false) && reloadIsSafe()) {
    location.reload();
    return;
  }
  showServerUpdateBanner(versionReloadAllowed ? normalizedServerVersion : reloadKey);
}

async function applySessionMetadataPayload(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  transcriptMeta = transcriptPayloadWithTmuxWindowOverrides(payload);
  // Metadata can arrive after the more-frequent auto-approve poll. Keep every agent window that
  // poll already proved exists, so a late or missed tmux window event cannot make buttons vanish
  // until the next poll repairs the client model.
  for (const session of Object.keys(transcriptMeta.sessions || {})) {
    reconcileTmuxWindowMetadataFromAgentWindows(session, autoApproveStates.get(session));
  }
  transcriptMetaLoaded = true;
  transcriptMetaLoadError = '';
  if (typeof warmTabberDataOnLaunch === 'function') warmTabberDataOnLaunch();
  maybeHandleServerVersionChange(transcriptMeta.server_version, transcriptMeta.client_revision);
  applyAgentAvailabilityPayload(transcriptMeta);
  updateMetadataBadgePulses(transcriptMeta);
  const previousActive = activeSessions.slice();
  const sessionsChanged = updateSessionList(transcriptMeta.session_order || []);
  if (options.refreshAuto !== false) {
    await loadAutoStatuses();
  }
  if (sessionsChanged) renderPanels(previousActive);
  renderSessionButtons();
  renderInfoPanel();
  renderYoagentPanel();
  if (options.refreshActivity !== false) refreshActivitySummary({silent: true});
  for (const session of activeSessions.filter(isTmuxSession)) {
    const preview = document.getElementById(transcriptDomId(session));
    const info = transcriptMeta.sessions?.[session];
    const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
    updatePanelHeader(session, info);
    renderSummaryContext(session, info, agent);
    if (!preview) continue;
    if (agent?.transcript) {
      updateTranscriptPathRow(session, agent.transcript);
      if (options.refreshContext === false) continue;
      if (typeof transcriptPreviewPaneIsActive === 'function' && !transcriptPreviewPaneIsActive(session)) continue;
      preview.textContent = `session_id: ${agent.session_id || ''}\nstatus: ${agent.status || ''}\n\n${t('transcript.loadingRecentContext')}`;
      refreshTranscriptPreview(session, preview, {preserveScroll: false});
    } else if (agent?.error) {
      updateTranscriptPathRow(session, '', agent.error);
      preview.textContent = agent.error;
    } else {
      updateTranscriptPathRow(session, '', t('transcript.noAgentFound'));
      preview.textContent = t('transcript.noAgentFound');
    }
  }
  renderPaneTabStrips();
  scheduleFileExplorerActiveTabSync();
  if (!shareViewMode && typeof syncServerWatchRoots === 'function') syncServerWatchRoots({renew: true});
  if (!shareViewMode) scheduleShareUiStatePublish();
  trackSessionStateChanges();
  refreshOpenEventLogs();
  return true;
}

function applyTranscriptsPayload(payload, options = {}) {
  return applySessionMetadataPayload(payload, options);
}

async function refreshSessionMetadata(options = {}) {
  if (transcriptMetaRefreshPromise) return transcriptMetaRefreshPromise;
  transcriptMetaLoading = true;
  transcriptMetaLoadError = '';
  syncTranscriptMetaLoadingUi();
  renderInfoPanel();
  transcriptMetaRefreshPromise = (async () => {
    try {
      const params = new URLSearchParams();
      if (options.force === true) params.set('force', '1');
      const suffix = params.toString();
      const payload = await apiFetchJson(`/api/session-metadata${suffix ? `?${suffix}` : ''}`);
      await applySessionMetadataPayload(payload, {
        refreshAuto: options.refreshAuto !== false,
        refreshContext: true,
        refreshActivity: options.refreshActivity !== false,
      });
    } catch (error) {
      transcriptMetaLoadError = String(error);
      for (const session of activeSessions.filter(isTmuxSession)) {
        const meta = document.getElementById(`meta-${session}`);
        const preview = document.getElementById(transcriptDomId(session));
        if (meta) meta.innerHTML = `<span class="err">transcript lookup failed</span>`;
        updateTranscriptPathRow(session, '', 'transcript lookup failed');
        if (preview) preview.textContent = `transcript lookup failed: ${error}`;
      }
    } finally {
      transcriptMetaLoading = false;
      transcriptMetaRefreshPromise = null;
      syncTranscriptMetaLoadingUi();
      renderInfoPanel();
    }
  })();
  return transcriptMetaRefreshPromise;
}

async function refreshTranscripts(options = {}) {
  return refreshSessionMetadata(options);
}

let paneInfoBarResizeObserver = null;
const PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS = 3;
const PANE_INFO_BAR_SCROLL_END_HOLD_SECONDS = 2;

function paneInfoBarScrollDurationSeconds(distancePx) {
  const distance = Math.max(0, Number(distancePx) || 0);
  return Math.min(90, Math.max(12, distance / 22));
}

function paneInfoBarScrollTiming(distancePx) {
  const travelSeconds = paneInfoBarScrollDurationSeconds(distancePx);
  const totalSeconds = PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS + travelSeconds + PANE_INFO_BAR_SCROLL_END_HOLD_SECONDS;
  const startPercent = (PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS / totalSeconds) * 100;
  const endPercent = ((PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS + travelSeconds) / totalSeconds) * 100;
  return {
    totalSeconds,
    timing: `linear(0 0%, 0 ${startPercent.toFixed(2)}%, 1 ${endPercent.toFixed(2)}%, 1 100%)`,
  };
}

function setStylePropertyIfChanged(style, name, value) {
  if (!style) return;
  const next = String(value);
  if (style.getPropertyValue?.(name) === next) return;
  style.setProperty?.(name, next);
}

function removeStylePropertyIfPresent(style, name) {
  if (!style?.getPropertyValue?.(name)) return;
  style.removeProperty?.(name);
}

function paneInfoBarMetaNodes(root = document) {
  if (!root) return [];
  const nodes = [];
  if (root.matches?.('.pane-info-bar-meta')) nodes.push(root);
  if (typeof root.querySelectorAll === 'function') nodes.push(...root.querySelectorAll('.pane-info-bar-meta'));
  return [...new Set(nodes)];
}

function observePaneInfoBarResizeTarget(target) {
  if (!target || !paneInfoBarResizeObserver) return;
  if (target._paneInfoBarResizeObserved === true) return;
  target._paneInfoBarResizeObserved = true;
  paneInfoBarResizeObserver.observe(target);
}

function ensurePaneInfoBarResizeObserver(meta, viewport = null, text = null) {
  if (!meta || typeof window === 'undefined' || typeof window.ResizeObserver !== 'function') return;
  if (!paneInfoBarResizeObserver) {
    paneInfoBarResizeObserver = new ResizeObserver(entries => {
      for (const entry of entries || []) schedulePaneInfoBarMetaOverflowSync(entry?.target?.closest?.('.pane-info-bar') || entry?.target || document);
    });
  }
  observePaneInfoBarResizeTarget(meta);
  const bar = meta.closest?.('.pane-info-bar');
  observePaneInfoBarResizeTarget(bar);
  observePaneInfoBarResizeTarget(viewport);
  observePaneInfoBarResizeTarget(text);
}

function syncPaneInfoBarMetaOverflow(root = document) {
  for (const meta of paneInfoBarMetaNodes(root)) {
    const viewport = meta.querySelector?.('.pane-info-bar-scroll-viewport');
    const text = viewport?.querySelector?.('.pane-info-bar-scroll-text');
    ensurePaneInfoBarResizeObserver(meta, viewport, text);
    if (!viewport || !text) {
      meta.classList?.remove?.('pane-info-bar-meta-overflow');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-distance');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-offset');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-duration');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-timing');
      continue;
    }
    const viewportWidth = Number(viewport.clientWidth || viewport.getBoundingClientRect?.().width || 0);
    const textWidth = Number(text.scrollWidth || text.getBoundingClientRect?.().width || 0);
    const distance = Math.max(0, Math.ceil(textWidth - viewportWidth));
    const overflowing = distance > 1;
    meta.classList?.toggle?.('pane-info-bar-meta-overflow', overflowing);
    if (overflowing) {
      const previousDistance = Number.parseFloat(meta.style?.getPropertyValue?.('--pane-info-bar-scroll-distance') || '');
      const scrollDistance = Number.isFinite(previousDistance) && previousDistance > 0 && Math.abs(previousDistance - distance) <= 4
        ? previousDistance
        : distance;
      const scrollTiming = paneInfoBarScrollTiming(scrollDistance);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-distance', `${scrollDistance}px`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-offset', `${-scrollDistance}px`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-duration', `${scrollTiming.totalSeconds.toFixed(2)}s`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-timing', scrollTiming.timing);
    } else {
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-distance');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-offset');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-duration');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-timing');
    }
  }
}

function schedulePaneInfoBarMetaOverflowSync(root = document) {
  const run = () => syncPaneInfoBarMetaOverflow(root);
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => {
    run();
    requestAnimationFrame(run);
  });
  else setTimeout(run, 0);
}

function updatePanelInfoBarMeta(session, info) {
  const meta = document.getElementById(`meta-${session}`);
  if (!meta) return;
  const html = stripTitleAttrs(paneInfoBarMetaHtml(session, info));
  const changed = meta.innerHTML !== html;
  if (changed) meta.innerHTML = html;
  meta.removeAttribute('title');
  if (changed) schedulePaneInfoBarMetaOverflowSync(meta);
}

function updatePanelHeader(session, info) {
  const tab = document.getElementById(paneTabDomId(session));
  const panel = document.getElementById(panelDomId(session));
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  updatePanelControlLabels(session, info);
  syncAttentionAnimation(panel, state.attention === true);
  if (tab) {
    tab.className = ['panel-session-label', auto ? 'auto' : '', state.attention ? STATE_CLASS.needsAttention : ''].filter(Boolean).join(' ');
    syncAttentionAnimation(tab, state.attention === true);
    tab.innerHTML = panelHeaderStateHtml(state);
    tab.removeAttribute('title');
  }
  scheduleAgentWindowActivityAnimationSync(panel || document);
  updatePanelInfoBarMeta(session, info);
  const popover = panel?.querySelector(':scope .panel-popover-zone > .session-popover');
  if (popover) {
    const agentKind = sessionAgentKind(session);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = sessionPopoverHtml(session, info, agentKind, auto, state);
    popover.replaceWith(wrapper.firstElementChild);
  }
  panel?.classList.toggle(STATE_CLASS.needsInputPane, state.key === STATE_KEY.needsInput && state.attention === true);
  panel?.classList.toggle(STATE_CLASS.needsExecPane, state.key === STATE_KEY.needsApproval && state.attention === true);
  panel?.classList.toggle(STATE_CLASS.needsBlockedPane, state.key === STATE_KEY.blocked);
}

function refreshSessionChrome(session) {
  updateSessionButtonStates();
  updatePanelHeader(session, transcriptMeta.sessions?.[session]);
}

function refreshTrackedSessionChrome(session) {
  refreshSessionChrome(session);
  trackSessionStateChanges();
}

function refreshActivePanelHeaders() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  }
}

function renderSummaryContext(session, info, agent) {
  const node = document.getElementById(`summary-context-${session}`);
  if (!node) return;
  node.innerHTML = summaryContextHtml(session, info, agent);
}

function transcriptPathRowHtml(path, fallback = 'no transcript path') {
  if (!path) return `<span class="transcript-path-missing">${esc(fallback)}</span>`;
  return `<span class="transcript-path-label">path</span><span class="transcript-path-value">${esc(path)}</span>${pathCopyButtonHtml(path, {className: 'transcript-path-copy', title: 'Copy transcript path'})}`;
}

function updateTranscriptPathRow(session, path, fallback = 'no transcript path') {
  const row = document.getElementById(`transcript-path-${session}`);
  if (!row) return;
  row.innerHTML = transcriptPathRowHtml(path, fallback);
}

async function refreshTranscriptPreview(session, preview, options = {}) {
  try {
    const payload = await apiFetchJson(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    if (!applyContextItemsPayloadFromPush(payload, options)) {
      preview.textContent = JSON.stringify(payload, null, 2);
    }
  } catch (error) {
    preview.textContent += `\n\ncontext load failed: ${error}`;
  }
}

function applyContextItemsPayloadFromPush(payload = {}, options = {}) {
  if (!payload || !payload.items) return false;
  const session = payload.session || options.session || '';
  const preview = options.preview || (session ? document.getElementById(transcriptDomId(session)) : null);
  if (!preview) return false;
  updateTranscriptPathRow(session, payload.path);
  renderTranscriptItems(preview, payload.path, payload.items, options);
  return true;
}

function startTranscriptStream(session, options = {}) {
  stopTranscriptStream(session);
  const preview = document.getElementById(transcriptDomId(session));
  if (!preview) return;
  const url = `/api/context-stream?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`;
  const source = new EventSource(url);
  transcriptStreams.set(session, source);
  source.addEventListener('reset', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    updateTranscriptPathRow(session, payload.path);
    renderTranscriptItems(preview, payload.path, payload.items || [], {scrollBottom: options.scrollBottom === true});
  });
  source.addEventListener('items', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    appendTranscriptItems(preview, payload.items || []);
  });
  source.addEventListener('ping', () => {});
  source.onerror = () => {
    stopTranscriptStream(session);
    const pane = document.getElementById(`transcript-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) {
      statusErr(localizedHtml('terminal.transcript.streamDisconnected', {session: sessionLabel(session)}));
      setTimeout(() => {
        if (document.getElementById(`transcript-pane-${session}`)?.classList.contains(CLS.active)) {
          startTranscriptStream(session, {scrollBottom: false});
        }
      }, 1500);
    }
  };
}

function stopTranscriptStream(session) {
  const source = transcriptStreams.get(session);
  if (source) {
    source.close();
    transcriptStreams.delete(session);
  }
}

function renderTranscriptItems(container, path, items, options = {}) {
  const shouldScrollBottom = options.scrollBottom === true;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  const oldTop = container.scrollTop;
  const oldHeight = container.scrollHeight;
  const blocks = items.map(item => transcriptItemHtml(item));
  container.innerHTML = blocks.join('');
  if (shouldScrollBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  } else if (options.preserveScroll) {
    if (wasNearBottom) {
      container.scrollTop = container.scrollHeight;
    } else {
      container.scrollTop = Math.max(0, oldTop + container.scrollHeight - oldHeight);
    }
  } else {
    container.scrollTop = container.scrollHeight;
  }
}

function appendTranscriptItems(container, items) {
  if (!items.length) return;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  container.insertAdjacentHTML('beforeend', items.map(item => transcriptItemHtml(item)).join(''));
  const rendered = Array.from(container.querySelectorAll('.transcript-item:not(.system)'));
  const extra = rendered.length - transcriptPreviewMessages;
  for (const item of rendered.slice(0, Math.max(0, extra))) item.remove();
  if (wasNearBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }
}

function transcriptItemHtml(item) {
  const role = normalizeRole(item.role);
  return `<div class="transcript-item ${role}">
    <div class="transcript-role">${esc(item.header || role)}</div>
    <div class="transcript-text">${esc(item.text || '')}</div>
  </div>`;
}

function eventItemHtml(event) {
  const details = event.details && typeof event.details === 'object' ? event.details : {};
  const detailText = Object.entries(details)
    .filter(([, value]) => value != null && value !== '')
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : value}`)
    .join(' · ');
  const title = detailText ? `${event.message || ''}\n${detailText}` : event.message || '';
  return `<div class="event-item" title="${esc(title)}">
    <span class="event-time">${esc(formatEventTime(event.time))}</span>
    <span class="event-type">${esc(event.type || 'event')}</span>
    <span class="event-message">${esc(event.message || '')}${detailText ? ` · ${esc(detailText)}` : ''}</span>
  </div>`;
}

function formatEventTime(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

async function refreshEventLog(session) {
  const node = document.getElementById(`events-${session}`);
  if (!node) return;
  try {
    const payload = await apiFetchJson(`/api/events?session=${encodeURIComponent(session)}&limit=120`);
    const events = Array.isArray(payload.events) ? payload.events : [];
    node.innerHTML = events.length
      ? events.slice().reverse().map(eventItemHtml).join('')
      : '<div class="event-empty">no events yet</div>';
  } catch (error) {
    if (error?.status) {
      node.innerHTML = `<div class="event-empty">${esc(error.payload?.error || 'failed to load events')}</div>`;
      return;
    }
    node.innerHTML = `<div class="event-empty">failed to load events: ${esc(error)}</div>`;
  }
}

function refreshOpenEventLogs() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`events-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) refreshEventLog(session);
  }
}

function postEvent(session, type, message, details = {}) {
  apiFetch('/api/event', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session, type, message, details}),
  }).then(() => {
    refreshOpenEventLogs();
  }).catch(() => {});
}

function normalizeRole(role) {
  const value = String(role || 'message').toLowerCase();
  if (value.includes('tool_use')) return 'tool_use';
  if (value.includes('tool_result')) return 'tool_result';
  if (value.includes('assistant')) return 'assistant';
  if (value.includes('user')) return 'user';
  if (value.includes('summary')) return 'summary';
  if (value.includes('system')) return 'system';
  return 'system';
}

function renderLatency(latestMs) {
  const samples = latencySamples.slice(-latencySamplesMax);
  if (samples.length === 0) {
    latencyLine.setAttribute('points', '');
  } else {
    const maxMs = Math.max(100, ...samples);
    const width = 44;
    const height = 18;
    const points = samples.map((value, index) => {
      const x = samples.length === 1 ? width : (index / (samples.length - 1)) * width;
      const y = height - 1 - (Math.min(value, maxMs) / maxMs) * (height - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    latencyLine.setAttribute('points', points.join(' '));
  }

  latencyMeter.classList.remove('good', 'warn', 'bad');
  if (latestMs == null) {
    latencyMeter.classList.add('bad');
    latencyNumber.textContent = '-- ms';
    return;
  }
  latencyNumber.textContent = `${latestMs} ms`;
  if (latestMs <= 80) {
    latencyMeter.classList.add('good');
  } else if (latestMs <= 200) {
    latencyMeter.classList.add('warn');
  } else {
    latencyMeter.classList.add('bad');
  }
}

async function updateLatency() {
  const startedAt = performance.now();
  try {
    await apiFetchJson(`/api/ping?t=${Date.now()}`, {cache: 'no-store'});
    const elapsedMs = Math.max(1, Math.round(performance.now() - startedAt));
    latencySamples = [...latencySamples, elapsedMs].slice(-latencySamplesMax);
    renderLatency(elapsedMs);
  } catch (_) {
    renderLatency(null);
  }
}

function refreshAll() {
  resyncVisibleTerminalRemoteSizes('refresh');
  refreshVisibleTerminalScreens('manual-refresh');
  refreshTranscripts({force: true});
  refreshBackgroundOwnerStatus({force: true});
  refreshAutoStatuses();
  refreshWatchedFilesystem({full: true});
}

function scheduleReconnectResync(reason = '') {
  if (reconnectResyncTimer) clearTimeout(reconnectResyncTimer);
  reconnectResyncTimer = setTimeout(() => {
    reconnectResyncTimer = null;
    refreshAll();
  }, reconnectResyncDebounceMs);
}

function resyncVisibleTerminalRemoteSizes(reason = '') {
  void reason;
  for (const session of activeSessions.filter(isTmuxSession)) {
    scheduleFit(session);
    forceRemoteResize(session);
  }
}

function installReconnectResyncHandlers() {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      scheduleReconnectResync('visible');
      resyncVisibleTerminalRemoteSizes('visible');
      if (fileExplorerMode === 'tabber' && typeof fetchTabberActivity === 'function') fetchTabberActivity();
    }
  });
  window.addEventListener('online', () => {
    scheduleReconnectResync('online');
    resyncVisibleTerminalRemoteSizes('online');
  });
}

let initialAppShellPainted = false;

function paintInitialAppShell() {
  if (initialAppShellPainted) return;
  renderSessionButtons();
  renderPanels([], {prune: false});
  seedVisualActivePaneItem(activeSessions);
  updatePanelInactiveOverlays();
  initialAppShellPainted = true;
}

async function boot() {
  applySettingsPayload(clientSettingsPayload, {initial: true, force: true});
  installReconnectResyncHandlers();
  if (shareViewMode) {
    applyShareViewBodyClasses();
    const bootstrapUiState = shareBootstrap?.uiState && typeof shareBootstrap.uiState === 'object' ? shareBootstrap.uiState : {};
    applyShareViewportState(bootstrapUiState.viewport || shareBootstrap?.viewport || {});
    applyShareAppearanceState(bootstrapUiState.appearance || shareBootstrap?.appearance || {});
    applyShareMirrorTransform();
  }
  waitForYolomuxFontsReady({timeoutMs: 0}).catch(() => {});
  syncAppViewportBreakpointClasses();
  // i18n: AWAIT the active locale catalog (all-static-fetch) before the first render so menus,
  // tabs, and the wordmark paint in the right language from the start — no flash of raw t() keys (the
  // menu bar renders synchronously at boot, before any later re-render could fix it). A 'system' pref is
  // resolved client-side against navigator.language (the server can't see the browser locale).
  await applyLocale(resolveLocalePref(initialSetting('general.language', 'system')));
  installGlobalThemeMediaListener();
  if (installShareReplayShell()) {
    installDevAutoReload();
    return;
  }
  applyFileExplorerStaticLabels();
  renderTransportWarning();
  renderTabMetaToggle();
  bindTopbarMetrics();
  syncInitialLayoutUrl();
  statusEl.textContent = t('status.yoloLoading');
  let initialAutoStatusesPromise = Promise.resolve(false);
  if (!shareViewMode) {
    loadNotificationDelivery();
    refreshBackgroundOwnerStatus({render: false}).catch(error => {
      console.warn('initial background-owner status refresh failed', error);
      return false;
    });
    initialAutoStatusesPromise = loadAutoStatuses().catch(error => {
      console.warn('initial auto-status refresh failed', error);
      return false;
    });
  }
  bindClipboardPaste();
  paintInitialAppShell();
  scheduleDeferredSettingsMetadataRefresh();
  if (!shareViewMode) {
    await refreshTranscripts({refreshAuto: false});
  } else {
    transcriptMeta = {session_order: sessions.slice(), sessions: Object.fromEntries(sessions.map(session => [session, {target: session}]))};
    transcriptMetaLoaded = true;
    await refreshTranscripts({refreshAuto: false, refreshActivity: false});
  }
  installYolomuxFontMetricRefresh();
  updatePanelInactiveOverlays();
  if (shareViewMode) {
    const bootstrapUiState = shareBootstrap?.uiState && typeof shareBootstrap.uiState === 'object' ? shareBootstrap.uiState : {};
    await applyShareUiState({
      ...bootstrapUiState,
      finder: bootstrapUiState.finder || shareBootstrap?.finder || {},
    });
  }
  if (!shareViewMode && clientPushCanSupplyData() && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  await Promise.all(activeSessions.filter(isTmuxSession).map(session => ensureTerminalRunning(session)));
  if (!shareViewMode && typeof primeJsDebugStatsBeforeLongLivedStreams === 'function') {
    await primeJsDebugStatsBeforeLongLivedStreams();
  }
  if (!shareViewMode) installClientEventStream();
  if (!shareViewMode) {
    initialAutoStatusesPromise.then(() => {
      renderAutoApproveButtons();
      updateSessionButtonStates();
      refreshActivePanelHeaders();
      trackSessionStateChanges();
    });
  }
  if (!shareViewMode) refreshWatchedPrs();
  renderAutoApproveButtons();
  updateLatency();
  installRuntimeIntervals();
  scheduleStartupHelperTip();
  installShareViewerBanner();
  installSharePointerPublisher();
  installShareScrollPublisher();
  installShareGeometryDigestLoop();
  installSharePopupLayerPublisher();
  installShareReplayMutationPublisher();
  startShareStatusRefresh();
  installDevAutoReload();
  document.querySelector('[data-update-badge]')?.addEventListener('click', triggerSelfUpdate);
  checkForUpdateOnce();
}

function clientEventEnvelope(event) {
  try {
    const parsed = JSON.parse(event?.data || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function clientEventPayloadFromEnvelope(envelope) {
  return envelope && typeof envelope === 'object' && envelope.payload && typeof envelope.payload === 'object'
    ? envelope.payload
    : envelope;
}

function recordSseDebugEvent(eventType, envelope = {}, rawEvent = null) {
  if (!jsDebugCollectionEnabled) return;
  const payload = clientEventPayloadFromEnvelope(envelope);
  const rawData = rawEvent?.data || '';
  const dataBytes = jsDebugByteLength(rawData);
  const dataLines = String(rawData || '').split(/\r?\n/);
  const frameBytes = jsDebugByteLength(`event: ${eventType}\n`)
    + dataLines.reduce((total, line) => total + jsDebugByteLength(`data: ${line}\n`), 0)
    + 1;
  const serverTimeMs = Number(envelope?.time) * 1000;
  const receiveLatencyMs = Number.isFinite(serverTimeMs)
    ? Math.max(0, Number((Date.now() - serverTimeMs).toFixed(1)))
    : undefined;
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
  });
}

function updateDryRunEnabled() {
  return typeof urlFlagEnabled === 'function' && urlFlagEnabled('updateDryRun');
}

function updateActionButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'toast-action';
  button.textContent = label;
  button.addEventListener('click', event => {
    event.stopPropagation();
    onClick(event, button.closest('.toast'));
  });
  return button;
}

async function triggerSelfUpdate(_event = null, ownerToast = null) {
  const dry = updateDryRunEnabled();
  const confirmed = window.confirm(dry
    ? 'Dry run: simulate installing a YOLOmux update? Nothing is pulled and the server is not restarted.'
    : 'Install the latest YOLOmux update and restart now?');
  if (!confirmed) return;
  const target = String(ownerToast?.dataset?.updateTarget || selfUpdateAvailableTarget || '').trim();
  dismissUpdateAvailableToasts(ownerToast);
  hideUpdateBadge();
  try {
    const res = await fetch(`/api/self-update${dry ? '?dryrun=1' : ''}`, {method: 'POST'});
    const data = await res.json().catch(() => ({}));
    const title = data.ok ? (data.restarting ? 'Installing update...' : 'Software Update') : 'Update failed';
    showToast(title, [data.message || (data.ok ? 'done' : 'see server logs')]);
    if (data.ok && data.restarting) {
      startSelfUpdateReloadPolling(data.target || data.version || target);
    }
  } catch (error) {
    showToast('Update failed', [String(error)]);
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
    badge.title = `YOLOmux update available${status.target ? ` (${status.target})` : ''} - click to update now`;
    if (target) badge.dataset.updateTarget = target;
  }
  const node = showToast('YOLOmux update available', [
    `A new YOLOmux update is available${status.target ? ` (${status.target})` : ''}.`,
  ], {
    actions: [updateActionButton('Update Now', triggerSelfUpdate)],
    countdownMs: 4 * 60 * 60 * 1000,  // keep the update cue up for 4 hours, not the default ~10s
    className: 'attention-alert toast toast-update',  // solid (opaque) background, not the translucent default
  });
  if (node && target) node.dataset.updateTarget = target;
}

async function checkForUpdateOnce() {
  try {
    const res = await fetch(`/api/update-status${updateDryRunEnabled() ? '?dryrun=1' : ''}`);
    if (!res.ok) return;  // readonly (403) or unavailable
    const status = await res.json();
    if (status && status.available) applyUpdateAvailable(status);
  } catch (_error) { /* offline / transient — the hourly push will retry */ }
}

function maybeNotifyYoagentJob(notification = {}) {
  const title = String(notification.title || 'YO!agent');
  const body = String(notification.body || '').trim();
  if (!body || !notificationDeliveryEnabled()) return;
  const session = String(notification.session || '').trim();
  const tag = `yoagent-job:${session || 'global'}:${body}`;
  if (notificationDeliveryEnabled('inApp')) showToast(title, [body], {session});
  if (!notificationDeliveryEnabled('system') || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(hostNotificationTitle(title), {
      body,
      tag,
      renotify: true,
      session,
    });
  } catch (error) {
    postEvent(session || null, 'yoagent_job_notification_error', `notification failed: ${error}`, {});
  }
}

function tmuxSignalsPayloadWithWindowOverrides(data) {
  if (!data || typeof data !== 'object' || !Array.isArray(data.windows)) return data;
  const overrides = new Map();
  for (const [session, override] of tmuxWindowActiveIndexOverrides.entries()) {
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
  if (!tmuxSignalState || typeof tmuxSignalState !== 'object' || !Array.isArray(tmuxSignalState.windows)) return data;
  const nextByKey = new Map(tmuxSignalState.windows.map(windowRecord => [tmuxSignalWindowKey(windowRecord), windowRecord]).filter(([key]) => key));
  for (const key of data.removed_window_keys || []) {
    nextByKey.delete(String(key || ''));
  }
  for (const windowRecord of data.windows || []) {
    const key = tmuxSignalWindowKey(windowRecord);
    if (key) nextByKey.set(key, windowRecord);
  }
  return {
    ...tmuxSignalState,
    ...data,
    patch: false,
    windows: Array.from(nextByKey.values()),
  };
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

function clientPushEventCoalesceKey(type, payload = {}) {
  const key = String(type || 'event');
  const session = clientPushEventSessionKey(payload);
  if (session) return `${key}:${session}`;
  return key;
}

function queueClientPushEvent(type, payload = {}) {
  const key = clientPushEventCoalesceKey(type, payload);
  clientPushEventQueue.set(key, {type, payload});
  if (clientPushEventFrame) return;
  clientPushEventFrame = requestAnimationFrame(() => {
    clientPushEventFrame = 0;
    flushQueuedClientPushEvents();
  });
}

function flushQueuedClientPushEvents() {
  const events = Array.from(clientPushEventQueue.values());
  clientPushEventQueue.clear();
  recordClientPerfCounter('sseEvent', 0, {nodes: events.length});
  for (const event of events) handleClientPushEventNow(event.type, event.payload);
}

function handleClientPushEvent(type, payload = {}) {
  queueClientPushEvent(type, payload);
}

function handleClientPushEventNow(type, payload = {}) {
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
  if (type === 'auto_approve_changed') {
    if (payload.refresh) {
      refreshAutoStatuses().catch(() => {});
      return;
    }
    if (payload.data) applyAutoApprovePayload(payload.data);
    return;
  }
  if (type === 'attention_acks_changed') {
    applyAttentionAcknowledgementResponse(payload);
    return;
  }
  if (type === 'background_owner_changed') {
    if (!applyBackgroundOwnerStatusPayload(payload)) {
      refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('background-owner status refresh failed', error));
    }
    return;
  }
  if (type === 'background_refresh_done') {
    if (payload.role === 'search-index') {
      refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('search-index status refresh failed', error));
      if (commandPaletteNode && !commandPaletteNode.hidden && commandPaletteEffectiveMode() === 'files') {
        refreshFileQuickOpenCandidates(commandPaletteQuery).catch(error => console.warn('search-index quick-open refresh failed', error));
      }
    }
    if (payload.role === 'session-files') {
      const session = String(payload.session || '');
      if (!session || session === fileExplorerSessionFilesTargetSession()) {
        fetchSessionFiles({silent: true}).catch(error => console.warn('session-files refresh failed', error));
      }
    }
    return;
  }
  if (type === 'tmux_signals_changed') {
    applyTmuxSignalsPayload(payload);
    if (typeof updatePanelWindowStepButtons === 'function' && typeof activePaneItems === 'function') {
      for (const session of activePaneItems()) {
        if (typeof isTmuxSession === 'function' && !isTmuxSession(session)) continue;
        updatePanelWindowStepButtons(session, transcriptMeta.sessions?.[session]);
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
  if (type === 'context_items_ready') {
    if (payload.data) applyContextItemsPayloadFromPush(payload.data, {session: payload.session, preserveScroll: true});
    return;
  }
  if (type === 'activity_summary_ready') {
    if (payload.data) applyActivitySummaryPayloadFromPush(payload.data);
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
  }
}

function installClientEventStream() {
  if (typeof EventSource === 'undefined' || clientEventsSource) return;
  let source;
  try {
    source = new EventSource('/api/client-events');
  } catch (_error) {
    return;
  }
  clientEventsSource = source;
  source.addEventListener('ready', event => {
    clientEventsConnected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    recordSseDebugEvent('ready', clientEventEnvelope(event), event);
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    refreshAutoStatuses().catch(error => console.warn('client-events ready auto-status refresh failed', error));
    refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('client-events ready background-owner refresh failed', error));
  });
  source.addEventListener('ping', event => {
    clientEventsConnected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    recordSseDebugEvent('ping', clientEventEnvelope(event), event);
  });
  source.onerror = () => {
    clientEventsConnected = false;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
  };
  for (const type of ['settings_changed', 'attention_acks_changed', 'auto_approve_changed', 'background_owner_changed', 'background_refresh_done', 'tmux_signals_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta']) {
    source.addEventListener(type, event => {
      clientEventsConnected = true;
      if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
      const envelope = clientEventEnvelope(event);
      recordSseDebugEvent(type, envelope, event);
      handleClientPushEvent(type, clientEventPayloadFromEnvelope(envelope));
    });
  }
}

// Dev-velocity #1b: in --dev mode, reload the page when the static bundle changes (ends the recurring
// "is the bundle stale?" misdiagnoses). Listens to the server's /api/dev-reload SSE 'reload' event;
// no-op outside dev mode. The EventSource auto-reconnects across the backend re-exec (#1c).
function installDevAutoReload() {
  if (!devMode || typeof EventSource === 'undefined') return;
  let source;
  try {
    const revision = encodeURIComponent(String(bootstrap.devBundleRevision || ''));
    source = new EventSource(`/api/dev-reload?bundle_revision=${revision}`);
  } catch (_error) {
    return;
  }
  source.addEventListener('ready', event => {
    // A client reconnects after a server restart, which means it misses the old process's
    // `reload` event. The fresh server's revision makes that stale bundle observable at once.
    const serverRevision = String(safeJsonParse(event.data, {})?.signature || '');
    const bootRevision = String(bootstrap.devBundleRevision || '');
    if (serverRevision && bootRevision && serverRevision !== bootRevision) location.reload();
  });
  source.addEventListener('reload', () => {
    statusOk('dev: bundle changed — reloading');
    location.reload();
  });
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  modal.classList.remove('about-open', 'share-open');
  title.textContent = t('transcript.tailTitle', {session: sessionLabel(session)});
  body.innerHTML = '';
  body.textContent = t('common.loading');
  modal.classList.add(CLS.open);
  const payload = await apiFetchJson(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
  scheduleSharePopupLayerPublish();
}

function globalShortcutTargetAllowsAppAction(target) {
  const nodes = [
    typeof Element !== 'undefined' && target instanceof Element ? target : null,
    document.activeElement,
  ].filter(Boolean);
  if (!nodes.length) return true;
  const blocked = ['.xterm', '.terminal-pane', '.cm-editor', 'input', 'textarea', 'select', '[contenteditable="true"]'];
  return !nodes.some(node => blocked.some(selector => node.closest?.(selector)));
}

function globalShortcutTargetAllowsPlatformAction(target) {
  return isMacPlatform() || globalShortcutTargetAllowsAppAction(target);
}

function globalShortcutTargetIsTerminalSurface(target) {
  const node = typeof Element !== 'undefined' && target instanceof Element ? target : document.activeElement;
  return Boolean(node?.closest?.('.xterm') || node?.closest?.('.terminal-pane'));
}

function globalShortcutTargetAllowsFinderShortcut(target) {
  if (globalShortcutTargetAllowsAppAction(target)) return true;
  return isMacPlatform() && globalShortcutTargetIsTerminalSurface(target);
}

function globalShortcutShouldToggleFinder(event, key = String(event?.key || '').toLowerCase(), mod = appModifier(event)) {
  return Boolean(mod && key === 'b' && globalShortcutTargetAllowsFinderShortcut(event?.target));
}

function clearPendingGlobalShortcutChord() {
  pendingGlobalShortcutChord = null;
  if (pendingGlobalShortcutChordTimer) {
    clearTimeout(pendingGlobalShortcutChordTimer);
    pendingGlobalShortcutChordTimer = null;
  }
}

function startPinTabShortcutChord() {
  clearPendingGlobalShortcutChord();
  pendingGlobalShortcutChord = 'pin-tab';
  pendingGlobalShortcutChordTimer = setTimeout(clearPendingGlobalShortcutChord, globalShortcutChordTimeoutMs);
  statusEl.textContent = t('shortcuts.pinTabPrompt', {keys: `${appShortcutText('K', {shift: true})} Enter`});
}

function handlePendingGlobalShortcutChord(event, key) {
  if (!pendingGlobalShortcutChord) return false;
  if (pendingGlobalShortcutChord === 'pin-tab' && key === 'enter') {
    event.preventDefault();
    event.stopPropagation();
    clearPendingGlobalShortcutChord();
    toggleActiveTabPinned();
    return true;
  }
  if (event.key === 'Escape') {
    clearPendingGlobalShortcutChord();
    return false;
  }
  clearPendingGlobalShortcutChord();
  return false;
}

function itemCanCloseWithAppShortcut(item) {
  return isFileEditorItem(item) || isImageViewerItem(item);
}

function toggleFileExplorerShortcut() {
  if (itemInLayout(fileExplorerItemId)) {
    fileExplorerShortcutRestoreSlots = cloneLayoutSlots();
    rememberFileExplorerOpenIntent(false);
    applyLayoutSlots(layoutWithoutItem(fileExplorerItemId, {
      preservePlaceholders: false,
    }), {
      preserveMissingFileExplorer: true,
      message: fileExplorerHiddenStatusMessage(),
    });
    return;
  }
  if (fileExplorerShortcutRestoreSlots && itemInLayout(fileExplorerItemId, fileExplorerShortcutRestoreSlots)) {
    rememberFileExplorerOpenIntent(true);
    applyLayoutSlots(fileExplorerShortcutRestoreSlots, {
      prune: false,
      message: `${fileExplorerLabel()} restored`,
    });
    fileExplorerShortcutRestoreSlots = null;
    return;
  }
  rememberFileExplorerOpenIntent(true);
  selectSession(fileExplorerItemId);
}

function handleFocusedTerminalCopyShortcut(event) {
  if (!globalShortcutTargetIsTerminalSurface(event.target) && !globalShortcutTargetAllowsAppAction(event.target)) return false;
  const session = focusedTerminal;
  if (!session) return false;
  const item = terminals.get(session);
  if (!item?.term) return false;
  if (!handleTerminalTmuxWindowShortcutKeydown(session, event) && !handleTerminalCopyShortcutKeydown(session, item.term, item.container, event)) return false;
  event.stopImmediatePropagation?.();
  event.stopPropagation?.();
  return true;
}

if (refreshMeta) {
  refreshMeta.textContent = t('meta.refresh');
  refreshMeta.setAttribute('aria-label', t('meta.refreshAria'));
  refreshMetaButtonTitle();
  refreshMeta.onclick = refreshAll;
}
if (tabMetaToggle) {
  tabMetaToggle.onclick = toggleTabMetadata;
  // Restore the `#` tab-metadata toggle to the top-right cluster, just left of Notify.
  notifyToggle?.parentElement?.insertBefore(tabMetaToggle, notifyToggle);
}
if (logoutButton) logoutButton.onclick = () => { window.location.href = '/logout'; };
document.getElementById('closeModal').onclick = () => {
  const modal = document.getElementById('modal');
  modal.classList.remove(CLS.open, 'about-open', 'share-open');
  scheduleSharePopupLayerPublish({immediate: true});
};
function promptAttentionClearElement(target) {
  return target?.closest?.('[data-prompt-attention-clear]');
}

function handlePromptAttentionClearEvent(event) {
  const node = promptAttentionClearElement(event.target);
  if (!node) return false;
  event.preventDefault();
  event.stopPropagation();
  clearPromptAttentionForSession(node.dataset.session || '', {delayMs: agentWindowActivityAcknowledgeDelayMs});
  return true;
}

document.addEventListener('click', handlePromptAttentionClearEvent);
document.addEventListener('keydown', event => {
  if (!['Enter', ' '].includes(event.key)) return;
  handlePromptAttentionClearEvent(event);
});
document.addEventListener('click', event => {
  if (event.target?.closest?.('.app-menu')) return;
  closeAppMenus();
});
topbar?.addEventListener('pointerenter', () => {
  closeOtherSessionPopovers(null, {force: true});
  closeFileImagePreview();
});
function handleGlobalShortcutKeydown(event) {
  if (handleFocusedTerminalCopyShortcut(event)) return;
  // C10: the Finder tree claims Command-Delete (Mac) / Delete (PC) to delete the selected file(s) before
  // the global Mod+Delete tab-close fallback can fire.
  if (handleFileExplorerDeleteShortcut(event)) return;
  // File Explorer / Finder-style keyboard traversal of the Finder/Differ selection (Arrow + Shift+Arrow,
  // Home/End, Mod+A) — claimed before the global shortcuts so arrows move the file selection when the
  // Finder/Differ is the active surface.
  if (handleFileExplorerArrowNav(event)) return;
  const mod = appModifier(event);
  const key = String(event.key || '').toLowerCase();
  const focusedEditorPanel = (() => {
    const direct = event.target?.closest?.('.file-editor-panel');
    if (direct && direct.offsetParent !== null) return direct;
    if (!isFileEditorItem(focusedPanelItem)) return null;
    return [...document.querySelectorAll('.file-editor-panel')].find(panel => panel.dataset.layoutItem === focusedPanelItem && panel.offsetParent !== null) || null;
  })();
  if (mod && !event.shiftKey && key === 'f' && focusedEditorPanel) {
    event.preventDefault();
    event.stopPropagation();
    openEditorFindShortcut(focusedEditorPanel).then(() => {
      updateEditorFindButton(focusedEditorPanel.querySelector('.file-editor-find-panel'), openFiles.get(fileEditorPanelPath(focusedEditorPanel)), focusedEditorPanel);
    });
    return;
  }
  const platformActionAllowed = globalShortcutTargetAllowsPlatformAction(event.target);
  if (handlePendingGlobalShortcutChord(event, key)) return;
  const paneTabShortcutDirection = terminalTmuxWindowShortcutDirection(event);
  if (paneTabShortcutDirection && globalShortcutTargetAllowsAppAction(event.target)) {
    event.preventDefault();
    event.stopPropagation();
    selectAdjacentPaneTab(paneTabShortcutDirection, {userInitiated: true});
    return;
  }
  // editor back/forward history via the keyboard — Mod+Alt+[ / Mod+Alt+]. (appModifier() is
  // false when Alt is held, so test the platform modifier directly.) Matched by event.code so a layout
  // where Alt remaps the bracket char still works; plain Mod+[ / Mod+] stay with CodeMirror (indent).
  const platformMod = isMacPlatform() ? (event.metaKey === true && event.ctrlKey !== true) : (event.ctrlKey === true && event.metaKey !== true);
  if (platformMod && event.altKey && (event.code === 'BracketLeft' || event.code === 'BracketRight')) {
    event.preventDefault();
    event.stopPropagation();
    if (event.code === 'BracketLeft') editorNavBack();
    else editorNavForward();
    return;
  }
  if (platformMod && event.altKey && event.code === 'KeyB') {
    event.preventDefault();
    event.stopPropagation();
    openYoagentRightPane();
    return;
  }
  if (mod && key === 'w') {
    event.preventDefault();
    event.stopPropagation();
    const item = currentActiveMenuItem();
    if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);
    return;
  }
  if (mod && key === 'p' && platformActionAllowed) {
    event.preventDefault();
    if (event.shiftKey) openCommandPalette();
    else openFileQuickOpen();
    return;
  }
  if (mod && platformActionAllowed) {
    if (key === 'k') {
      event.preventDefault();
      event.stopPropagation();
      if (event.shiftKey) startPinTabShortcutChord();
      else showShareModal();
      return;
    }
    if ((key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)) {
      event.preventDefault();
      const item = currentActiveMenuItem();
      if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);
      return;
    }
    if (globalShortcutShouldToggleFinder(event, key, mod)) {
      event.preventDefault();
      toggleFileExplorerShortcut();
      return;
    }
    if (event.key === ',') {
      event.preventDefault();
      selectSession(prefsItemId);
      return;
    }
  }
  if (!mod && globalShortcutTargetAllowsAppAction(event.target) && (event.key === '?' || (event.key === '/' && event.shiftKey))) {
    event.preventDefault();
    openKeyboardShortcutsOverlay();
    return;
  }
  if (event.key === 'Escape') {
    closeKeyboardShortcutsOverlay();
    closeAppMenus();
  }
}
installShareReadonlyInteractionBlocker();
installTerminalResizeAuthorityHandlers();
window.addEventListener('keydown', handleGlobalShortcutKeydown, true);
window.addEventListener('resize', () => {
  syncAppViewportBreakpointClasses();
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  applyShareMirrorTransform();
  scheduleShareViewportPublish();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

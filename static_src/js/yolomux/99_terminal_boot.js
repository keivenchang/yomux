
function paneFrameControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => ` type="button" disabled title="unavailable for ${esc(unavailableLabel)}" aria-label="${esc(label)}"`;
  const controls = [];
  const includeActions = options.actions ?? isTmuxSession(session);
  const includeDetails = options.details === true;
  const includeMinimize = options.minimize !== false;
  const includeExpand = options.expand !== false;
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
  const disabledAttrs = label => disabled ? ` type="button" disabled title="unavailable for ${esc(unavailableLabel)}" aria-label="${esc(label)}"` : '';
  const readonlyAttrs = label => ` type="button" disabled title="${esc(label)} requires admin access" aria-label="${esc(label)}"`;
  const tabAttrs = (name, label = '') => {
    if (disabled) return disabledAttrs(label || name);
    if (readOnlyMode && name === 'summary') return readonlyAttrs('AI Transcript');
    const labelAttrs = label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : '';
    return ` type="button" data-tab="${esc(session)}" data-tab-name="${name}"${labelAttrs}`;
  };
  const info = transcriptMeta.sessions?.[session];
  const terminalTitle = terminalTabTitle(session, info);
  const terminalAttrs = disabled ? disabledAttrs(terminalTitle) : `${tabAttrs('terminal')} title="${esc(terminalTitle)}" aria-label="${esc(terminalTitle)}"`;
  const terminalLabel = disabled ? 'Term' : terminalTabLabel(session, info);
  const isFiles = isFileExplorerItem(session);
  const terminalButtonHtml = `<button class="tab active terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>`;
  const frameHtml = isFiles
    ? paneFrameControlsHtml(session, {
      disabled,
      actions: false,
      minimize: false,
      expand: false,
      close: true,
      closeTitle: `close ${fileExplorerLabel()}`,
      closeLabel: `Close ${fileExplorerLabel()}`,
    })
    : paneFrameControlsHtml(session, {disabled, actions: isTmuxSession(session), details: true, close: false});
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          ${terminalButtonHtml}
          ${frameHtml}
        </div>`;
}

function virtualPanelControlsHtml(session) {
  return `<div class="tabs virtual-panel-controls" role="tablist">
          ${paneFrameControlsHtml(session, {actions: false, close: false})}
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
      <div class="panel-detail-row">
        <div class="panel-popover-zone">
          <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(sessionState(session, transcriptMeta.sessions?.[session]))}</div>
          <div id="meta-${session}" class="meta">${esc(t('pane.findingBranch'))}</div>
          ${sessionPopoverHtml(session, transcriptMeta.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMeta.sessions?.[session]))}
        </div>
        ${isTmuxSession(session) ? tmuxWindowBarHtml(session, transcriptMeta.sessions?.[session]) : ''}
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(session)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
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
          <div class="transcript-head">AI Transcript for session '${esc(sessionLabel(session))}'</div>
          <div id="summary-context-${session}" class="summary-context">loading session context...</div>
          <div id="summary-${session}" class="summary-preview markdown-body">click "AI Transcript" to generate a Codex summary of the last hour</div>
        </div>
      </div>
      <div id="events-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">YOLO log</div>
          <div id="events-${session}" class="event-list">loading events...</div>
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
  document.querySelectorAll('[data-info-refresh]').forEach(button => {
    setMetadataRefreshButtonLoading(button, transcriptMetaLoading, t('info.refreshRepo'), t('info.refreshRepo'));
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

function renderInfoPanel() {
  const node = document.getElementById('info-content');
  if (!node) return;
  syncTranscriptMetaLoadingUi();
  applyInfoBranchColumnWidth();
  bindInfoPrContextMenu(node);   // idempotent — "Watch this PR" on the PR column
  renderWatchedPrs();   // the Watched PRs section repaints alongside the branch table
  const rows = infoBranchRows();
  if (!rows.length) {
    if (transcriptMetaLoading) {
      node.innerHTML = infoMetadataLoadingHtml();
      return;
    }
    if (transcriptMetaLoadError) {
      node.innerHTML = `<div class="info-empty info-error">${esc(t('info.loadFailed'))} ${esc(transcriptMetaLoadError)}</div>`;
      return;
    }
    if (!transcriptMetaLoaded) {
      node.innerHTML = infoMetadataLoadingHtml();
      return;
    }
    node.innerHTML = `<div class="info-empty">${esc(t('info.empty'))}</div>`;
    return;
  }
  const headerCell = (key, label) => {
    const active = infoBranchSort.key === key;
    const dirLabel = active ? (infoBranchSort.dir === 'asc' ? t('sort.ascending') : t('sort.descending')) : t('sort.unsorted');
    const marker = active ? (infoBranchSort.dir === 'asc' ? 'A-Z' : 'Z-A') : '';
    return `<button type="button" class="info-sort-button${active ? ' active' : ''}" data-info-sort="${esc(key)}" aria-label="${esc(t('sort.aria', {label, dir: dirLabel}))}"><span>${esc(label)}</span>${marker ? `<span class="info-sort-marker">${marker}</span>` : ''}</button>`;
  };
  const resizeHandle = (column, label) => `<button type="button" class="info-column-resizer" data-info-column-resize="${esc(column)}" title="${esc(label)}" aria-label="${esc(label)}"></button>`;
  const header = `<div class="info-row header">
    <div class="info-cell">${headerCell('session', t('info.header.session'))}</div>
    <div class="info-cell">${headerCell('path', t('info.header.path'))}</div>
    <div class="info-cell info-resizable-header-cell info-branch-header-cell">${headerCell('branch', t('info.header.branch'))}${resizeHandle('branch', t('info.resizeBranchColumn'))}</div>
    <div class="info-cell">${headerCell('pr', 'PR')}</div>
    <div class="info-cell">${headerCell('linear', 'Linear')}</div>
    <div class="info-cell info-resizable-header-cell info-desc-header-cell">${headerCell('desc', t('info.header.desc'))}${resizeHandle('desc', t('info.resizeDescColumn'))}</div>
    <div class="info-cell">${headerCell('updated', t('info.header.updated'))}</div>
  </div>`;
  const body = rows.map(row => `<div class="info-row${row.current ? ' current' : ''}">
    <div class="info-cell" title="${esc(row.session)}">${esc(row.session)}</div>
    <div class="info-cell" title="${esc(row.path)}">${esc(pathBasename(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${row.branchHtml}</div>
    <div class="info-cell" title="${esc(row.prTitle)}">${row.prHtml}</div>
    <div class="info-cell" title="${esc(row.linearTitle)}">${row.linearHtml}</div>
    <div class="info-cell" title="${esc(row.desc)}">${esc(row.desc)}</div>
    <div class="info-cell" title="${esc(row.updatedTitle || row.updated)}">${esc(row.updatedText || row.updated)}</div>
  </div>`).join('');
  node.innerHTML = header + body;
  node.querySelectorAll('[data-info-sort]').forEach(button => {
    button.addEventListener('click', () => {
      setInfoBranchSort(button.dataset.infoSort);
      renderInfoPanel();
    });
  });
  bindInfoColumnResizers(node);
}

function infoColumnResizeConfig(column) {
  if (column === 'branch') {
    return {
      cssVar: '--info-branch-column-width',
      defaultWidthPx: infoBranchColumnDefaultWidthPx,
      maxWidthPx: infoBranchColumnMaxWidthPx,
      minWidthPx: infoBranchColumnMinWidthPx,
      storageKey: infoBranchColumnWidthStorageKey,
    };
  }
  if (column === 'desc') {
    return {
      cssVar: '--info-desc-column-width',
      defaultWidthPx: infoDescColumnDefaultWidthPx,
      maxWidthPx: infoDescColumnMaxWidthPx,
      minWidthPx: infoDescColumnMinWidthPx,
      storageKey: infoDescColumnWidthStorageKey,
    };
  }
  return null;
}

function infoColumnWidth(column) {
  return column === 'desc' ? infoDescColumnWidthPx : infoBranchColumnWidthPx;
}

function setInfoColumnWidthState(column, value) {
  if (column === 'desc') infoDescColumnWidthPx = value;
  else infoBranchColumnWidthPx = value;
}

function clampInfoColumnWidth(column, value) {
  const config = infoColumnResizeConfig(column);
  if (!config) return infoBranchColumnDefaultWidthPx;
  const number = Number(value);
  if (!Number.isFinite(number)) return config.defaultWidthPx;
  return Math.max(config.minWidthPx, Math.min(config.maxWidthPx, Math.round(number)));
}

function readStoredInfoColumnWidth(column) {
  const config = infoColumnResizeConfig(column);
  if (!config) return infoBranchColumnDefaultWidthPx;
  return clampInfoColumnWidth(column, storageGet(config.storageKey, config.defaultWidthPx));
}

function setInfoColumnWidth(column, value, options = {}) {
  const config = infoColumnResizeConfig(column);
  if (!config) return infoColumnWidth('branch');
  const width = clampInfoColumnWidth(column, value);
  setInfoColumnWidthState(column, width);
  applyInfoColumnWidth(column);
  if (options.persist !== false) storageSet(config.storageKey, width);
  return width;
}

function resetInfoColumnWidth(column) {
  const config = infoColumnResizeConfig(column);
  return setInfoColumnWidth(column, config?.defaultWidthPx);
}

function applyInfoColumnWidth(column, root = document.documentElement) {
  const config = infoColumnResizeConfig(column);
  if (!config) return;
  root?.style?.setProperty(config.cssVar, `${clampInfoColumnWidth(column, infoColumnWidth(column))}px`);
}

function applyInfoColumnWidths(root = document.documentElement) {
  applyInfoColumnWidth('branch', root);
  applyInfoColumnWidth('desc', root);
}

function setInfoBranchColumnWidth(value, options = {}) {
  return setInfoColumnWidth('branch', value, options);
}

function setInfoDescColumnWidth(value, options = {}) {
  return setInfoColumnWidth('desc', value, options);
}

function resetInfoBranchColumnWidth() {
  return resetInfoColumnWidth('branch');
}

function resetInfoDescColumnWidth() {
  return resetInfoColumnWidth('desc');
}

function applyInfoBranchColumnWidth(root = document.documentElement) {
  applyInfoColumnWidths(root);
}

function bindInfoColumnResizers(node) {
  node.querySelectorAll('[data-info-column-resize]').forEach(handle => {
    if (handle.dataset.bound === 'true') return;
    const column = handle.dataset.infoColumnResize;
    const config = infoColumnResizeConfig(column);
    if (!config) return;
    handle.dataset.bound = 'true';
    handle.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
    });
    handle.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      resetInfoColumnWidth(column);
    });
    handle.addEventListener('pointerdown', event => {
      event.preventDefault();
      event.stopPropagation();
      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startWidth = infoColumnWidth(column);
      const direction = getComputedStyle(node).direction === 'rtl' ? -1 : 1;
      handle.setPointerCapture?.(pointerId);
      document.body?.classList.add('info-column-resizing');
      const move = moveEvent => {
        const delta = (moveEvent.clientX - startX) * direction;
        setInfoColumnWidth(column, startWidth + delta, {persist: false});
      };
      const done = () => {
        storageSet(config.storageKey, infoColumnWidth(column));
        document.body?.classList.remove('info-column-resizing');
        try { handle.releasePointerCapture?.(pointerId); } catch (_) {}
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', done);
        window.removeEventListener('pointercancel', done);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', done);
      window.addEventListener('pointercancel', done);
    });
  });
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

function watchedPrStatusText(pr) {
  return pullRequestStatusDisplay(pr) || (pr?.state ? String(pr.state).toUpperCase() : t('common.unknown'));
}

// The Watched PRs section of YO!info — PRs tracked independent of any open session's branch. Reuses
// the pr-status-* badge classes; renders into its own #info-watched container so it can repaint on the
// (longer) watched-PR poll cadence without re-rendering the branch table.
function renderWatchedPrs() {
  const node = document.getElementById('info-watched');
  if (!node) return;
  const prs = Array.isArray(watchedPrsData.watched_prs) ? watchedPrsData.watched_prs : [];
  const heading = `<div class="info-watched-head">${esc(t('info.watched.heading'))}</div>`;
  if (!prs.length) {
    node.innerHTML = `${heading}<div class="info-empty info-watched-empty">${esc(t('info.watched.empty'))}</div>`;
    return;
  }
  const rows = prs.map(pr => {
    const ref = String(pr.ref || `#${pr.number}`);
    const statusCls = pullRequestStatusClass(pr);
    const statusText = watchedPrStatusText(pr);
    const link = linkHtml(pr.url, ref, pr.title || pr.description || '', 'info-watched-ref');
    return `<div class="info-row info-watched-row" data-watched-ref="${esc(ref)}">
      <div class="info-cell info-watched-ref-cell">${link}</div>
      <div class="info-cell info-watched-title" title="${esc(pr.title || '')}">${esc(pr.title || '')}</div>
      <div class="info-cell info-watched-status"><span class="meta-pr-status ${esc(statusCls)}">${esc(statusText)}</span></div>
      <div class="info-cell info-watched-actions"><button type="button" class="info-watched-remove" data-watched-remove="${esc(ref)}" title="${esc(t('info.watched.remove'))}" aria-label="${esc(t('info.watched.remove'))}">×</button></div>
    </div>`;
  }).join('');
  const truncated = watchedPrsData.truncated > 0
    ? `<div class="info-watched-note">${esc(t('info.watched.truncated', {count: String(watchedPrsData.truncated)}))}</div>`
    : '';
  node.innerHTML = heading + rows + truncated;
  node.querySelectorAll('[data-watched-remove]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      removeWatchedPr(button.dataset.watchedRemove);
    });
  });
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
  renderWatchedPrs();
  return true;
}

// Append a PR ref to github.watched_prs (dedup by canonical ref); accepts owner/repo#N or a PR URL.
function addWatchedPr(entry) {
  const ref = normalizeWatchedPrRef(entry);
  if (!ref) {
    statusErr(t('info.watched.invalid', {entry: String(entry || '')}));
    return;
  }
  const current = initialSetting('github.watched_prs', []);
  const list = Array.isArray(current) ? current.slice() : [];
  if (list.some(item => normalizeWatchedPrRef(item) === ref)) {
    statusOk(t('info.watched.already', {ref}));
    return;
  }
  list.push(ref);
  saveSettingsPatch(settingPatch('github.watched_prs', list))
    .then(() => { statusOk(t('info.watched.added', {ref})); refreshWatchedPrs(); })
    .catch(error => statusErr(`settings save failed: ${esc(error)}`));
}

function removeWatchedPr(ref) {
  const target = normalizeWatchedPrRef(ref) || String(ref || '');
  const current = initialSetting('github.watched_prs', []);
  const list = (Array.isArray(current) ? current : []).filter(item => normalizeWatchedPrRef(item) !== target && String(item).trim() !== target);
  saveSettingsPatch(settingPatch('github.watched_prs', list))
    .then(() => { statusOk(t('info.watched.removed', {ref: target})); refreshWatchedPrs(); })
    .catch(error => statusErr(`settings save failed: ${esc(error)}`));
}

// right-clicking a PR link in YO!info offers "Watch this PR" (adds it to github.watched_prs).
// Delegated on #info-content so it covers both the branch-table PR column and any future PR cells.
function bindInfoPrContextMenu(node) {
  if (!node || node.dataset.watchedPrMenuBound === '1') return;
  node.dataset.watchedPrMenuBound = '1';
  node.addEventListener('contextmenu', event => {
    const cell = event.target.closest('.info-cell');
    const link = cell?.querySelector?.('a[href*="github.com/"][href*="/pull/"]');
    if (!link) return;
    const ref = normalizeWatchedPrRef(link.getAttribute('href') || '');
    if (!ref) return;
    event.preventDefault();
    event.stopPropagation();
    showWatchPrContextMenu(ref, event.clientX, event.clientY);
  });
}

function showWatchPrContextMenu(ref, x, y) {
  const already = (Array.isArray(initialSetting('github.watched_prs', [])) ? initialSetting('github.watched_prs', []) : [])
    .some(item => normalizeWatchedPrRef(item) === ref);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu watched-pr-context-menu';
  menu.setAttribute('role', 'menu');
  appendContextMenuButton(
    menu,
    already ? t('info.watched.unwatchThis', {ref}) : t('info.watched.watchThis', {ref}),
    () => (already ? removeWatchedPr(ref) : addWatchedPr(ref)),
    () => watchedPrContextMenu.close(),
  );
  watchedPrContextMenu.open(menu, x, y);
}

function scrollYoagentChatToBottom(node = document.getElementById('yoagent-content')) {
  const history = node?.querySelector?.('.yoagent-chat-history');
  if (history) history.scrollTop = history.scrollHeight;
  if (node) node.scrollTop = node.scrollHeight;
  const panelBody = node?.closest?.('.info-pane, .panel-overlay-root, .panel');
  if (panelBody && panelBody !== node) panelBody.scrollTop = panelBody.scrollHeight;
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
  chat.outerHTML = yoagentChatHtml();
  renderYoagentMessageMarkdown(node);
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
  (node.querySelectorAll?.('.yoagent-message.assistant .yoagent-message-body[data-yoagent-markdown]') || []).forEach(body => {
    renderMarkdownPreviewInto(body, yoagentTightMarkdown(body.textContent || ''));
    installYoagentSessionLinks(body);
    body.removeAttribute('data-yoagent-markdown');
  });
}

function renderYoagentPanel(options = {}) {
  const node = document.getElementById('yoagent-content');
  if (!node) return;
  const input = node.querySelector('[data-yoagent-chat-input]');
  const inputFocused = input && document.activeElement === input;
  const selectionStart = inputFocused ? input.selectionStart : null;
  const selectionEnd = inputFocused ? input.selectionEnd : null;
  if (input && options.preserveDraft !== false) yoagentDraft = input.value || '';
  if (yoagentBusyUiIsMounted(node) && options.allowBusyRebuild !== true) {
    if (refreshYoagentSummaryRegions(node)) {
      restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    }
    return;
  }
  if (options.summaryOnly && refreshYoagentSummaryRegions(node)) {
    restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    return;
  }
  node.innerHTML = yoagentChatHtml();
  renderYoagentMessageMarkdown(node);
  if (options.scrollBottom !== false) {
    requestAnimationFrame(() => scrollYoagentChatToBottom(node));
    setTimeout(() => scrollYoagentChatToBottom(node), 0);
  }
  if (options.focusInput) {
    requestAnimationFrame(() => focusYoagentChatInput(node));
    return;
  }
  if (!inputFocused) return;
  restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
}

function infoBranchRows() {
  return sortedInfoBranchRows(rawInfoBranchRows(), infoBranchSort);
}

function rawInfoBranchRows() {
  const rows = [];
  const seen = new Set();
  for (const session of sessions) {
    const info = transcriptMeta.sessions?.[session];
    const project = info?.project || {};
    const git = project.git;
    const branches = git?.other_branches?.branches || [];
    for (const branch of branches) {
      const key = `${git?.root || ''}\n${branch.name || ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const current = branch.current === true;
      const currentPr = current ? displayPullRequest(info) : null;
      const currentLinear = current ? project.linear || [] : [];
      const linearIds = currentLinear.length
        ? currentLinear.map(issue => issue.identifier).filter(Boolean)
        : branch.linear_ids || [];
      const linearHtml = currentLinear.length
        ? currentLinear.map(issue => linearIssueHtml(issue)).join(' ')
        : linearIds.map(linearIssueLinkHtml).filter(Boolean).join(' ');
      const prHtml = currentPr?.number ? pullRequestColumnLinkHtml(currentPr) : pullRequestLinkForBranch(git, branch);
      const prValue = currentPr?.number ? currentPr : branch.pull_request;
      const prTitle = pullRequestTextForBranch(prValue, branch.subject || '');
      const linearTitle = currentLinear.length
        ? currentLinear.map(issue => [issue.identifier, issue.state, issue.title].filter(Boolean).join(' ')).filter(Boolean).join(' · ')
        : linearIds.join(' ');
      const desc = shortText(
        currentPr?.title
          || currentPr?.description
          || currentLinear.find(issue => issue.title)?.title
          || branch.subject
          || '',
        180,
      );
      rows.push({
        session,
        path: git?.root || git?.cwd || '',
        branch: branch.name || '',
        branchHtml: branchLinkHtml(git, branch.name),
        desc,
        updated: branch.updated || '',
        updatedText: branchUpdatedText(branch),
        updatedTitle: branch.updated || branchUpdatedText(branch),
        updatedTs: Number.isFinite(branch.updated_ts) ? branch.updated_ts : 0,
        prHtml: prHtml || '',
        prTitle,
        prSort: prTitle || (prValue?.number ? String(prValue.number) : ''),
        linearHtml,
        linearTitle,
        current,
      });
    }
  }
  return rows;
}

function setInfoBranchSort(key) {
  if (!infoBranchSortColumns.has(key)) return;
  if (infoBranchSort.key === key) {
    infoBranchSort = {key, dir: infoBranchSort.dir === 'asc' ? 'desc' : 'asc'};
  } else {
    infoBranchSort = {key, dir: 'asc'};
  }
}

const infoBranchSortColumns = new Set(['session', 'path', 'branch', 'pr', 'linear', 'desc', 'updated']);

function infoBranchSortValue(row, key) {
  if (key === 'updated') return Number.isFinite(row.updatedTs) ? row.updatedTs : 0;
  if (key === 'pr') return row.prSort || row.prTitle || '';
  if (key === 'linear') return row.linearTitle || '';
  return row[key] || '';
}

function compareInfoBranchRows(left, right, sortState) {
  const key = infoBranchSortColumns.has(sortState?.key) ? sortState.key : 'updated';
  const direction = sortState?.dir === 'asc' ? 1 : -1;
  const leftValue = infoBranchSortValue(left, key);
  const rightValue = infoBranchSortValue(right, key);
  let result = 0;
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    result = leftValue - rightValue;
  } else {
    result = String(leftValue).localeCompare(String(rightValue), undefined, {numeric: true, sensitivity: 'base'});
  }
  if (result !== 0) return result * direction;
  return (right.updatedTs - left.updatedTs)
    || String(left.session).localeCompare(String(right.session), undefined, {numeric: true, sensitivity: 'base'})
    || String(left.path).localeCompare(String(right.path), undefined, {numeric: true, sensitivity: 'base'})
    || String(left.branch).localeCompare(String(right.branch), undefined, {numeric: true, sensitivity: 'base'});
}

function sortedInfoBranchRows(rows, sortState = infoBranchSort) {
  return rows.slice().sort((left, right) => compareInfoBranchRows(left, right, sortState));
}

function bindPanelControls(panel, session) {
  delegate(panel, 'click', '[data-tab]', (_event, button) => {
    const currentName = button.dataset.tabName;
    const nextName = currentName !== 'terminal' && button.classList.contains(CLS.active) ? 'terminal' : currentName;
    activateTab(button.dataset.tab, nextName, {userInitiated: true});
  });
  delegate(panel, 'click', '[data-window-dir], [data-window-index]', event => {
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
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-copy-transcript-path]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    const path = target.dataset.copyTranscriptPath || '';
    if (!path) return;
    copyTextToClipboard(path)
      .then(() => { statusEl.textContent = 'copied transcript path'; })
      .catch(error => { statusErr(`copy failed: ${esc(error)}`); });
  });
  panel.querySelector('.meta')?.addEventListener('click', event => {
    event.stopPropagation();
    // C9: the "+N repos" chip opens the per-session multi-repo popover (delegated, since .meta re-renders).
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

function bindFileUpload(panel, session) {
  if (readOnlyMode) return;
  panel.addEventListener('dragenter', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragover', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragleave', event => {
    if (!hasFileDrag(event)) return;
    if (panel.contains(event.relatedTarget)) return;
    panel.classList.remove(CLS.fileDragOver);
  });
  panel.addEventListener('drop', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.remove(CLS.fileDragOver);
    // DOIT.57: remember the drop point so the post-upload suggestion overlay can anchor there.
    uploadFiles(session, event.dataTransfer?.files || [], {suggestAt: {x: event.clientX, y: event.clientY}});
  });
}

function insertFileDragPayloadIntoTerminal(session, payload) {
  const references = terminalFileReferences(session, payload);
  if (!references.length) return;
  const inserted = insertIntoTerminal(session, `${references.map(shellQuote).join(' ')} `);
  const label = references.length === 1 ? references[0] : `${references.length} paths`;
  statusEl.innerHTML = inserted
    ? `<span class="ok">inserted ${esc(label)} into ${esc(sessionLabel(session))}</span>`
    : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
}

// DOIT.57: dropping a file/dir onto a terminal shows a transient, keyboard-driven suggestion overlay
// (Alt+1..9) of context-aware actions. It inserts nothing on its own — keep typing and it fades, or
// press Alt+N to compose a prompt for the pane's agent (referencing the path) and insert it for review
// (no auto-Enter). Gated by uploads.show_suggestions (default true). Alt+1 is always Insert path.
const DROP_SUGGESTION_CATEGORY_EXTS = {
  image: ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.heic', '.heif', '.avif'],
  log: ['.log', '.out', '.err'],
  diff: ['.diff', '.patch'],
  data: ['.csv', '.tsv', '.json', '.ndjson', '.yaml', '.yml', '.parquet'],
  doc: ['.md', '.markdown', '.rst', '.pdf', '.txt', '.docx', '.html'],
  config: ['.toml', '.ini', '.env', '.cfg', '.conf'],
  code: ['.py', '.js', '.ts', '.tsx', '.jsx', '.mjs', '.rs', '.go', '.java', '.c', '.h', '.cpp', '.cc', '.rb', '.php', '.lua', '.sql', '.css', '.sh', '.bash', '.zsh'],
  archive: ['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.whl'],
};

function fileDropCategory(pathOrName, kind = 'file') {
  if (kind === 'dir') return 'dir';
  const name = String(pathOrName || '').toLowerCase();
  const base = name.slice(Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')) + 1);
  const dot = base.lastIndexOf('.');
  const ext = dot > 0 ? base.slice(dot) : '';
  for (const category of Object.keys(DROP_SUGGESTION_CATEGORY_EXTS)) {
    if (DROP_SUGGESTION_CATEGORY_EXTS[category].includes(ext)) return category;
  }
  return 'any';
}

// `prompt(ref)` composes the inserted text; `ref` is the shell-quoted path string. Display/combo order.
const DROP_SUGGESTIONS = [
  {id: 'img-error', cats: ['image'], agent: true, label: 'Diagnose the error in this screenshot', prompt: r => `This screenshot ${r} shows an error or problem. Read it, explain what is wrong, and suggest a fix.`},
  {id: 'img-describe', cats: ['image'], agent: true, label: 'Describe the image', prompt: r => `Describe what is shown in the image ${r}.`},
  {id: 'img-ocr', cats: ['image'], agent: true, label: 'Extract the text (OCR)', prompt: r => `Extract all of the text from the image ${r}.`},
  {id: 'log-errors', cats: ['log'], agent: true, label: 'Find the errors', prompt: r => `Read the log ${r} and list the errors and warnings, most important first.`},
  {id: 'log-cause', cats: ['log'], agent: true, label: 'Find the root cause', prompt: r => `Read the log ${r}, find the root cause of the failure, and suggest a fix.`},
  {id: 'code-review', cats: ['code'], agent: true, label: 'Review for bugs', prompt: r => `Review ${r} for bugs and correctness issues.`},
  {id: 'code-explain', cats: ['code', 'config'], agent: true, label: 'Explain what it does', prompt: r => `Explain what ${r} does.`},
  {id: 'code-security', cats: ['code', 'config'], agent: true, label: 'Find security issues', prompt: r => `Review ${r} for security problems.`},
  {id: 'code-tests', cats: ['code'], agent: true, label: 'Write tests', prompt: r => `Write tests for ${r}.`},
  {id: 'diff-review', cats: ['diff'], agent: true, label: 'Review the diff', prompt: r => `Review the diff in ${r} for risks and regressions.`},
  {id: 'diff-commit', cats: ['diff'], agent: true, label: 'Write a commit message', prompt: r => `Write a commit message for the change in ${r}.`},
  {id: 'data-summary', cats: ['data'], agent: true, label: 'Summarize the data', prompt: r => `Summarize the structure and contents of ${r} (columns/schema, row count, anything notable).`},
  {id: 'data-anomaly', cats: ['data'], agent: true, label: 'Find anomalies', prompt: r => `Look at ${r} and point out anomalies or outliers.`},
  {id: 'doc-summary', cats: ['doc'], agent: true, label: 'Summarize', prompt: r => `Summarize ${r}.`},
  {id: 'doc-todos', cats: ['doc'], agent: true, label: 'Extract the action items', prompt: r => `Extract the action items and TODOs from ${r}.`},
  {id: 'dir-tree', cats: ['dir'], agent: true, label: 'Summarize this folder', prompt: r => `Summarize the contents of the directory ${r}.`},
  {id: 'dir-large', cats: ['dir'], agent: true, label: 'Find the largest files', prompt: r => `Find the largest files under ${r}.`},
  {id: 'analyze', cats: ['any'], agent: true, label: 'Take a look at it', prompt: r => `Take a look at ${r} and tell me what it is and anything notable.`},
];

function dropSuggestionsFor(category, agentKind, count = 1) {
  const isAgent = agentKind === 'claude' || agentKind === 'codex';
  // ⌥1 is reserved for Insert path, so context suggestions fill ⌥2..⌥9 (cap 8).
  return DROP_SUGGESTIONS.filter(s => {
    if (s.agent && !isAgent) return false;
    return s.cats.includes('any') || s.cats.includes(category);
  }).slice(0, 8);
}

function composeDropSuggestion(suggestion, references) {
  return suggestion.prompt(references.join(' '));
}

let terminalDropSuggestionState = null;

function dismissTerminalDropSuggestions() {
  const state = terminalDropSuggestionState;
  if (!state) return;
  terminalDropSuggestionState = null;
  clearTimeout(state.timer);
  document.removeEventListener('keydown', state.onKeyDown, true);
  document.removeEventListener('pointerdown', state.onPointerDown, true);
  state.node.remove();
}

function showTerminalDropSuggestions(session, payload, x, y) {
  dismissTerminalDropSuggestions();
  const paths = Array.isArray(payload?.paths) ? payload.paths.filter(Boolean) : [payload?.path].filter(Boolean);
  if (!paths.length) return false;
  const references = terminalFileReferences(session, payload).map(shellQuote);
  const category = fileDropCategory(paths[0], payload?.kind);
  const suggestions = dropSuggestionsFor(category, sessionAgentKind(session), paths.length);
  const rows = [
    {label: paths.length > 1 ? `Insert ${paths.length} paths` : 'Insert path', run: () => insertFileDragPayloadIntoTerminal(session, payload)},
    ...suggestions.map(s => ({label: s.label, run: () => insertIntoTerminal(session, composeDropSuggestion(s, references))})),
  ].slice(0, 9);

  const node = document.createElement('div');
  node.className = 'terminal-drop-suggestions';
  node.setAttribute('role', 'listbox');
  const mac = isMacPlatform();
  const head = document.createElement('div');
  head.className = 'terminal-drop-suggestions-head';
  head.textContent = paths.length > 1 ? `${paths.length} files — pick an action or keep typing` : `${basenameOf(paths[0])} — pick an action or keep typing`;
  node.appendChild(head);
  rows.forEach((row, index) => {
    const item = document.createElement('div');
    item.className = 'terminal-drop-suggestion';
    item.setAttribute('role', 'option');
    const combo = document.createElement('span');
    combo.className = 'terminal-drop-suggestion-combo';
    combo.textContent = mac ? `⌥${index + 1}` : `Alt+${index + 1}`;
    const label = document.createElement('span');
    label.className = 'terminal-drop-suggestion-label';
    label.textContent = row.label;
    item.append(combo, label);
    item.addEventListener('click', () => { row.run(); dismissTerminalDropSuggestions(); });
    node.appendChild(item);
  });
  document.body.appendChild(node);
  const rect = node.getBoundingClientRect();
  node.style.left = `${Math.round(Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)))}px`;
  node.style.top = `${Math.round(Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)))}px`;

  const onKeyDown = event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      dismissTerminalDropSuggestions();
      return;
    }
    // Alt+1..9 picks a row; gate strictly so AltGr (Ctrl+Alt) composing on EU layouts is not misread.
    if (event.altKey && !event.ctrlKey && !event.metaKey && /^Digit[1-9]$/.test(event.code)) {
      const index = Number(event.code.slice(5)) - 1;
      if (index < rows.length) {
        event.preventDefault();
        event.stopPropagation();
        rows[index].run();
        dismissTerminalDropSuggestions();
      }
      return;
    }
    // Any other key: the overlay is advisory — dismiss it but let the keystroke reach the terminal.
    dismissTerminalDropSuggestions();
  };
  const onPointerDown = event => { if (!node.contains(event.target)) dismissTerminalDropSuggestions(); };
  const timer = setTimeout(dismissTerminalDropSuggestions, 6000);
  terminalDropSuggestionState = {node, timer, onKeyDown, onPointerDown};
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('pointerdown', onPointerDown, true);
  return true;
}

// Decide what a drop onto a terminal does. 'ignore' = let it bubble to the layout (files keep opening
// in the editor when suggestions are off); 'editor' = open a split (edge drops); 'suggest' = the
// transient overlay (center drops when uploads.show_suggestions is on); 'insert' = legacy dir path-insert.
function terminalDropMode(payload, intent) {
  if (!payload?.path) return 'ignore';
  const center = !intent?.targetSlot || intent.zone === 'middle';
  if (!center) return payload.kind === 'dir' ? 'editor' : 'ignore';
  if (boolSetting('uploads.show_suggestions', true)) return 'suggest';
  return payload.kind === 'dir' ? 'insert' : 'ignore';
}

function installFilePathDropTarget(session, target) {
  if (readOnlyMode) return;
  target.addEventListener('dragover', event => {
    const payload = fileDragPayload(event);
    const intent = dropIntentForEvent(event);
    const mode = terminalDropMode(payload, intent);
    if (mode === 'ignore') return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    if (mode === 'editor' && intent?.targetSlot && pathDropIntentAllowsPayload(payload, intent)) showDropPreview(intent);
    else clearDropPreview();
    target.classList.add(CLS.pathDragOver);
  });
  target.addEventListener('dragleave', event => {
    if (target.contains(event.relatedTarget)) return;
    target.classList.remove(CLS.pathDragOver);
    clearDropPreview();
  });
  target.addEventListener('drop', event => {
    const payload = fileDragPayload(event);
    const intent = dropIntentForEvent(event);
    const mode = terminalDropMode(payload, intent);
    if (mode === 'ignore') return;
    event.preventDefault();
    event.stopPropagation();
    target.classList.remove(CLS.pathDragOver);
    clearDropPreview();
    if (mode === 'editor') {
      if (intent?.targetSlot && pathDropIntentAllowsPayload(payload, intent)) {
        openDraggedFilesInEditor(payload, {targetSlot: intent.targetSlot, targetZone: intent.zone});
      }
      return;
    }
    if (mode === 'suggest') {
      showTerminalDropSuggestions(session, payload, event.clientX, event.clientY);
      return;
    }
    insertFileDragPayloadIntoTerminal(session, payload);
  });
}

function installTerminalFileDrop(session, container) {
  installFilePathDropTarget(session, container);
}

function bindClipboardPaste() {
  if (readOnlyMode) return;
  if (clipboardPasteBound) return;
  clipboardPasteBound = true;
  document.addEventListener('paste', event => {
    const file = pastedImageFile(event);
    if (!file) return;
    const session = pasteTargetSession(event);
    if (!session) {
      statusErr('select a YOLOmux pane before pasting an image');
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (!beginPasteUpload(session)) return;
    uploadFiles(session, [file], {source: 'paste'}).finally(() => {
      pasteUploadInFlight = false;
    });
  }, {capture: true});
}

function pastedImageFile(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItems = items.filter(item => item.kind === 'file' && String(item.type || '').startsWith('image/'));
  const item = imageItems.find(candidate => candidate.type === 'image/png') || imageItems[0];
  if (!item) return null;
  const file = item.getAsFile();
  if (!file) return null;
  const type = file.type || item.type || 'image/png';
  return new File([file], pastedImageFilename(file.name, type), {type});
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
    statusErr('readonly access cannot upload files');
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const totalBytes = files.reduce((total, file) => total + (Number(file?.size) || 0), 0);
  if (uploadMaxBytes > 0 && totalBytes > uploadMaxBytes) {
    statusErr(`upload failed: ${esc(`selected files total ${formatFileSize(totalBytes)}; limit is ${formatFileSize(uploadMaxBytes)}`)}`);
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
    // DOIT.57: a dropped/pasted upload shows the suggestion overlay (Alt+1..9) for the uploaded path
    // instead of auto-inserting it — Alt+1 still inserts the path. Falls back to the legacy insert when
    // uploads.show_suggestions is off or there is no anchor point.
    if (options.suggestAt && paths.length && boolSetting('uploads.show_suggestions', true)) {
      showUploadResult(session, payload, false);
      showTerminalDropSuggestions(session, {path: paths[0], paths, kind: 'file'}, options.suggestAt.x, options.suggestAt.y);
    } else {
      const inserted = options.source === 'paste'
        ? insertPasteUploadReferences(session, payload.files || [], {silent: true})
        : insertUploadPaths(session, paths, {silent: true});
      showUploadResult(session, payload, inserted);
    }
    refreshOpenEventLogs();
    refreshTranscripts({force: true});
  } catch (error) {
    statusErr(`upload failed: ${esc(error?.payload?.error || error)}`);
  }
}

function insertUploadPaths(session, paths, options = {}) {
  if (!paths.length) return false;
  const inserted = insertIntoTerminal(session, `${paths.map(shellQuote).join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">inserted upload path into ${esc(sessionLabel(session))}</span>`
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
      ? `<span class="ok">inserted pasted image into ${esc(sessionLabel(session))}</span>`
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
  if (readOnlyMode) {
    statusErr('readonly access cannot type into terminal sessions');
    return false;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) return false;
  const filtered = stripTerminalQueryResponses(text);
  if (!filtered) return false;
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  if (autoFocusEnabled) item.term?.focus?.();
  return true;
}

function noteTerminalExplicitInput(session) {
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function showUploadResult(session, payload, inserted) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
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
}

function ensureUploadResultShell(session, node) {
  return ensureToastShell(node, {
    title: t('upload.resultTitle', {host: serverHostname, session: sessionLabel(session)}),
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
  if (isFileEditorItem(session)) renderFileEditorPanel(panel, session, {updateActiveFile: !dockviewLayoutActive()});
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
    if (pane?.classList.contains('active')) scheduleFit(session);
  }
}

function activateTab(session, name, options = {}) {
  setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
  if (name !== 'transcript') stopTranscriptStream(session);
  if (name !== 'summary') stopSummaryStream(session);
  document.querySelectorAll(`[data-tab="${session}"]`).forEach(button => {
    button.classList.toggle('active', button.dataset.tabName === name);
  });
  document.querySelectorAll(`[data-panel-tab-overflow="${session}"]`).forEach(button => {
    button.classList.toggle('active', ['transcript', 'summary', 'events'].includes(name));
  });
  for (const tabName of ['terminal', 'transcript', 'summary', 'events']) {
    const pane = document.getElementById(`${tabName}-pane-${session}`);
    if (pane) pane.classList.toggle('active', tabName === name);
  }
  updateTypingIndicator(session);
  if (name === 'terminal') {
    scheduleFit(session);
    setTimeout(() => refreshTerminal(session), 120);
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
}

function tmuxWindow(session, key, label) {
  if (readOnlyMode) {
    statusErr(localizedHtml('terminal.connection.readonlyTmuxWindow'));
    return;
  }
  const directIndex = tmuxWindowNumber(key?.windowIndex);
  if (directIndex !== null) {
    previewTmuxWindowLabel(session, {windowIndex: directIndex});
    statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
    scheduleFit(session);
    focusTerminalFromUserAction(session, 75);
    apiFetchJson(`/api/tmux-window?session=${encodeURIComponent(session)}&window=${encodeURIComponent(String(directIndex))}`, {method: 'POST'})
      .then(() => setTimeout(() => refreshTranscripts({force: true}), 250))
      .catch(error => statusErr(`tmux window failed: ${esc(error.message || error)}`));
    return;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) {
    statusErr(terminalNotConnectedHtml(session));
    return;
  }
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  previewTmuxWindowLabel(session, key);
  statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  setTimeout(() => refreshTranscripts({force: true}), 250);
}

async function ensureTerminalRunning(session) {
  const item = terminals.get(session);
  const readyState = item?.socket?.readyState;
  if (item && readyState !== undefined && readyState !== WebSocket.CLOSING && readyState !== WebSocket.CLOSED) return;
  if (readOnlyMode) {
    startTerminal(session);
    return;
  }
  const ensured = await ensureSession(session);
  if (!ensured) {
    const container = document.getElementById(terminalDomId(session));
    if (container) container.innerHTML = `<pre class="terminal-error">${localizedHtml('terminal.connection.sessionUnavailableRetry', {session: sessionLabel(session)})}</pre>`;
    return;
  }
  startTerminal(session);
}

function connectTerminalSocket(session, item) {
  if (!item?.term || !item?.container) return;
  if (item.socket && item.socket.readyState !== WebSocket.CLOSED && item.socket.readyState !== WebSocket.CLOSING) return;
  const socket = new WebSocket(wsUrl(session));
  socket.binaryType = 'arraybuffer';
  item.socket = socket;
  item.manualClose = false;
  socket.onopen = () => {
    item.reconnectAttempt = 0;
    dismissTerminalConnectionToasts(session);
    if (terminalIsVisible(session, item.container)) {
      scheduleFit(session);
      scheduleRemoteResize(session, 50);
    }
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (event.data instanceof ArrayBuffer) {
      item.term.write(new Uint8Array(event.data));
    } else {
      item.term.write(String(event.data));
    }
  };
  socket.onclose = () => {
    if (item.manualClose || terminals.get(session) !== item) return;
    postEvent(session, 'terminal_disconnected', `terminal disconnected from ${session}`, {});
    clearFocusedTerminal(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
    // Roster-confirm before reconnecting: a killed session is pruned immediately, a transient
    // disconnect reconnects as before.
    confirmSessionGoneOrReconnect(session, item);
  };
  socket.onerror = () => {
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
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
    statusErr('xterm unavailable');
    return;
  }
  container.innerHTML = '';
  const size = estimateTerminalSize(container);
  const term = new TerminalCtor({
    cols: size.cols,
    rows: size.rows,
    cursorBlink: true,
    convertEol: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: terminalFontSize,
    letterSpacing: 0,
    lineHeight: 1.0,
    scrollback: terminalScrollback,
    disableStdin: readOnlyMode,
    theme: terminalThemeForSession(session),
    minimumContrastRatio: terminalMinimumContrastRatio(),
    // Alt-screen TUIs (claude, vim, less) enable mouse reporting, which makes xterm send drags to the app
    // instead of selecting text — so Ctrl-C/Cmd-C has nothing to copy. Option-click (Mac) forces a text
    // selection anyway; on Linux/Windows hold Shift while dragging (xterm's built-in bypass).
    macOptionClickForcesSelection: true,
  });
  term.open(container);
  // match the container bg to the terminal theme so every pane shares one white.
  if (container?.style) container.style.background = terminalThemeForGlobalTheme().background;
  installTerminalLinkProvider(term);
  installTerminalContextMenu(session, term, container);
  installTerminalCopyShortcut(session, term, container);
  installTerminalOsc52Bridge(session, term);   // Claude/tmux OSC 52 clipboard escapes -> browser clipboard
  installTerminalFileDrop(session, container);
  const openedSize = estimateTerminalSize(container, term);
  if (term.cols !== openedSize.cols || term.rows !== openedSize.rows) {
    term.resize(openedSize.cols, openedSize.rows);
  }
  const item = {term, socket: null, container, manualClose: false, reconnectAttempt, reconnectTimer: null, resizeTimer: null, scrollTimer: null, pendingScrollLines: 0};
  terminals.set(session, item);
  enableTerminalScroll(session, term, container);
  observeTerminalResize(session, container);
  term.onFocus?.(() => {
    setFocusedTerminal(session);
  });
  term.onBlur?.(() => {
    clearFocusedTerminal(session);
  });
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
  term.onData(data => {
    if (readOnlyMode) return;
    const current = terminals.get(session);
    const socket = current?.socket;
    if (socket?.readyState === WebSocket.OPEN) {
      const filtered = stripTerminalQueryResponses(data);
      if (filtered) {
        socket.send(JSON.stringify({type: 'input', data: filtered}));
      }
    }
  });
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
    && pane?.classList.contains('active')
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
    statusErr('readonly access cannot change YOLO');
    return;
  }
  const state = autoApproveStates.get(session) || {};
  const current = state.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  if (readOnlyMode) {
    statusErr('readonly access cannot change YOLO');
    return;
  }
  try {
    const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    autoApproveStates.set(session, payload);
    updateDocumentTitle();
    updateSessionButtonStates();
    renderAutoApproveButton(session, payload);
    statusEl.innerHTML = payload.enabled
      ? `<span class="ok">enabled YOLO for ${esc(sessionLabel(session))}</span>`
      : `<span class="ok">disabled YOLO for ${esc(sessionLabel(session))}</span>`;
  } catch (error) {
    const payload = error?.payload || {};
    if (error?.status) {
      if (payload?.target || payload?.session) {
        autoApproveStates.set(session, payload);
        updateDocumentTitle();
        updateSessionButtonStates();
        renderAutoApproveButton(session, payload);
      }
      statusErr(`${esc(payload.error || 'YOLO approval failed')}`);
      return;
    }
    statusErr(`YOLO request failed: ${esc(error)}`);
  }
}

async function refreshAutoStatuses() {
  await loadAutoStatuses();
  bindClipboardPaste();
  renderAutoApproveButtons();
  updateSessionButtonStates();
  refreshActivePanelHeaders();
  trackSessionStateChanges();
  refreshOpenEventLogs();
}

async function loadAutoStatuses() {
  try {
    const payload = await apiFetchJson('/api/auto-approve');
    applyAutoApprovePayload(payload);
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
  }
  updateDocumentTitle();
  // Re-toggle the YO markers' working class from the fresh states on the SAME poll the title updates,
  // so a finished/idle pane's marker stops spinning instead of lingering (the transcript poll path
  // updated the title but never re-synced the markers).
  renderAutoApproveButtons();
}

function applyAutoApprovePayload(payload) {
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
  }
  if (sessionsChanged) renderPanels(previousActive);
  updateDocumentTitle();
  renderAutoApproveButtons();
  updateSessionButtonStates();
  refreshActivePanelHeaders();
  trackSessionStateChanges();
  return true;
}

function autoApproveOwnerLabel(payload) {
  const owner = payload?.lock_owner || {};
  const pid = owner.pid ? `pid ${owner.pid}` : '';
  const root = owner.project_root || '';
  return [pid, root].filter(Boolean).join(' ') || payload?.last_action || 'another YOLOmux';
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
    const wasWorking = button.classList.contains('working');
    syncPressedButton(button, enabled);
    button.classList.toggle('inactive', !enabled && !locked);
    button.classList.toggle('locked', locked);
    button.classList.toggle('working', working);
    if (working) {
      if (!wasWorking || !button.style.getPropertyValue('--yolo-rotate-delay')) {
        button.style.setProperty('--yolo-rotate-delay', yoloRotationDelay());
      }
    } else {
      button.style.removeProperty('--yolo-rotate-delay');
    }
    button.closest('.pane-tab')?.classList.remove('is-working');
    button.textContent = t('brand.marker');
    const action = payload?.last_action ? `; ${payload.last_action}` : '';
    button.title = enabled
      ? `YOLO on for ${sessionLabel(session)}${action}${readOnlyMode ? '; readonly access' : ''}`
      : locked
        ? `YOLO owned by ${autoApproveOwnerLabel(payload)}`
      : `YOLO off for ${sessionLabel(session)}${readOnlyMode ? '; readonly access' : ''}`;
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

function showServerUpdateBanner(version) {
  let banner = document.getElementById('serverUpdateBanner');
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
  dismiss.setAttribute('aria-label', 'Dismiss');
  dismiss.textContent = '×';
  dismiss.addEventListener('click', () => banner.remove());
  banner.append(msg, reload, dismiss);
  document.body.appendChild(banner);
}

function maybeHandleServerVersionChange(serverVersion) {
  // The boot version (bootstrap.version) only updates on page load; this lets a
  // long-lived open client learn that a newer server shipped, via the metadata poll.
  if (!serverVersion || serverVersion === bootstrap.version) return;
  if (!boolSetting('general.reload_on_update', false)) return;
  if (serverVersionReloadHandled === serverVersion) return;
  serverVersionReloadHandled = serverVersion;
  if (boolSetting('general.reload_on_update_auto', false) && reloadIsSafe()) {
    location.reload();
    return;
  }
  showServerUpdateBanner(serverVersion);
}

async function applyTranscriptsPayload(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  transcriptMeta = payload;
  transcriptMetaLoaded = true;
  transcriptMetaLoadError = '';
  maybeHandleServerVersionChange(transcriptMeta.server_version);
  if (transcriptMeta.agentAuth) agentAuth = transcriptMeta.agentAuth;
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
    const meta = document.getElementById(`meta-${session}`);
    const preview = document.getElementById(transcriptDomId(session));
    const info = transcriptMeta.sessions?.[session];
    const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
    updatePanelHeader(session, info);
    if (meta) {
      meta.innerHTML = stripTitleAttrs(projectMetaHtml(session, info));
      meta.removeAttribute('title');
    }
    renderSummaryContext(session, info, agent);
    if (!preview) continue;
    if (agent?.transcript) {
      updateTranscriptPathRow(session, agent.transcript);
      if (options.refreshContext === false) continue;
      preview.textContent = `session_id: ${agent.session_id || ''}\nstatus: ${agent.status || ''}\n\nloading recent transcript context...`;
      refreshTranscriptPreview(session, preview, {preserveScroll: false});
    } else if (agent?.error) {
      updateTranscriptPathRow(session, '', agent.error);
      preview.textContent = agent.error;
    } else {
      updateTranscriptPathRow(session, '', 'no agent transcript found');
      preview.textContent = 'no agent transcript found';
    }
  }
  renderPaneTabStrips();
  scheduleFileExplorerActiveTabSync();
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots({renew: true});
  trackSessionStateChanges();
  refreshOpenEventLogs();
  return true;
}

async function refreshTranscripts(options = {}) {
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
      const payload = await apiFetchJson(`/api/transcripts${suffix ? `?${suffix}` : ''}`);
      await applyTranscriptsPayload(payload, {
        refreshAuto: options.refreshAuto !== false,
        refreshContext: true,
        refreshActivity: true,
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

function updatePanelHeader(session, info) {
  const tab = document.getElementById(paneTabDomId(session));
  const panel = document.getElementById(panelDomId(session));
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  updatePanelControlLabels(session, info);
  syncAttentionAnimation(panel, state.attention === true);
  if (tab) {
    tab.className = `panel-session-label ${auto ? 'auto' : ''} ${state.attention ? 'needs-attention' : ''}`;
    syncAttentionAnimation(tab, state.attention === true);
    tab.innerHTML = panelHeaderStateHtml(state);
    tab.removeAttribute('title');
  }
  const popover = panel?.querySelector(':scope .panel-popover-zone > .session-popover');
  if (popover) {
    const agentKind = sessionAgentKind(session);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = sessionPopoverHtml(session, info, agentKind, auto, state);
    popover.replaceWith(wrapper.firstElementChild);
  }
  panel?.classList.toggle('needs-input-pane', state.key === 'needs-input');
  panel?.classList.toggle('needs-exec-pane', state.key === 'needs-approval');
  panel?.classList.toggle('needs-blocked-pane', state.key === 'blocked');
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
  return `<span class="transcript-path-label">path</span><span class="transcript-path-value">${esc(path)}</span>${pathCopyButtonHtml(path, {className: 'transcript-path-copy', dataAttr: 'data-copy-transcript-path', title: 'Copy transcript path'})}`;
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
    if (pane?.classList.contains('active')) {
      statusErr(localizedHtml('terminal.transcript.streamDisconnected', {session: sessionLabel(session)}));
      setTimeout(() => {
        if (document.getElementById(`transcript-pane-${session}`)?.classList.contains('active')) {
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
    if (pane?.classList.contains('active')) refreshEventLog(session);
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
  refreshTranscripts({force: true});
  refreshAutoStatuses();
  refreshWatchedFilesystem();
}

async function boot() {
  applySettingsPayload(clientSettingsPayload, {initial: true, force: true});
  installClientEventStream();
  // i18n: AWAIT the active locale catalog (all-static-fetch) before the first render so menus,
  // tabs, and the wordmark paint in the right language from the start — no flash of raw t() keys (the
  // menu bar renders synchronously at boot, before any later re-render could fix it). A 'system' pref is
  // resolved client-side against navigator.language (the server can't see the browser locale).
  await applyLocale(resolveLocalePref(initialSetting('general.language', 'system')));
  installGlobalThemeMediaListener();
  applyFileExplorerStaticLabels();
  renderTransportWarning();
  renderTabMetaToggle();
  bindTopbarMetrics();
  syncInitialLayoutUrl();
  statusEl.textContent = 'loading YOLO status...';
  await loadNotifyStatus();
  await loadAutoStatuses();
  bindClipboardPaste();
  await refreshTranscripts({refreshAuto: false});
  renderSessionButtons();
  renderPanels([], {prune: false});
  seedVisualActivePaneItem(activeSessions);
  updatePanelInactiveOverlays();
  if (clientPushCanSupplyData() && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  await Promise.all(activeSessions.filter(isTmuxSession).map(session => ensureTerminalRunning(session)));
  refreshWatchedPrs();
  renderAutoApproveButtons();
  updateLatency();
  installRuntimeIntervals();
  scheduleStartupHelperTip();
  installDevAutoReload();
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
  if (!debugModeEnabled) return;
  const payload = clientEventPayloadFromEnvelope(envelope);
  const rawData = rawEvent?.data || '';
  const dataBytes = jsDebugByteLength(rawData);
  const dataLines = String(rawData || '').split(/\r?\n/);
  const frameBytes = jsDebugByteLength(`event: ${eventType}\n`)
    + dataLines.reduce((total, line) => total + jsDebugByteLength(`data: ${line}\n`), 0)
    + 1;
  const serverTimeMs = Number(envelope?.time) * 1000;
  const receiveLatencyMs = Number.isFinite(serverTimeMs)
    ? Number((Date.now() - serverTimeMs).toFixed(1))
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
    key: payload?.session || payload?.locale || payload?.request?.session || '',
  });
}

function handleClientPushEvent(type, payload = {}) {
  if (type === 'settings_changed') {
    if (payload.data && typeof payload.data === 'object') {
      applySettingsPayload(payload.data, {force: true});
    }
    return;
  }
  if (type === 'auto_approve_changed') {
    if (payload.data) applyAutoApprovePayload(payload.data);
    return;
  }
  if (type === 'watched_prs_changed') {
    if (payload.data) applyWatchedPrsPayload(payload.data);
    return;
  }
  if (type === 'transcripts_changed') {
    if (payload.data) {
      applyTranscriptsPayload(payload.data, {refreshAuto: false, refreshContext: false, refreshActivity: false});
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
    recordSseDebugEvent('ready', clientEventEnvelope(event), event);
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  });
  source.addEventListener('ping', event => {
    clientEventsConnected = true;
    recordSseDebugEvent('ping', clientEventEnvelope(event), event);
  });
  source.onerror = () => { clientEventsConnected = false; };
  for (const type of ['settings_changed', 'auto_approve_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready']) {
    source.addEventListener(type, event => {
      clientEventsConnected = true;
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
    source = new EventSource('/api/dev-reload');
  } catch (_error) {
    return;
  }
  source.addEventListener('reload', () => {
    statusOk('dev: bundle changed — reloading');
    location.reload();
  });
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  modal.classList.remove('about-open');
  title.textContent = `${sessionLabel(session)} transcript tail`;
  body.innerHTML = '';
  body.textContent = t('common.loading');
  modal.classList.add('open');
  const payload = await apiFetchJson(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
}

function globalShortcutTargetAllowsAppAction(target) {
  const node = typeof Element !== 'undefined' && target instanceof Element ? target : document.activeElement;
  if (!node) return true;
  const blocked = ['.xterm', '.terminal-pane', '.cm-editor', 'input', 'textarea', 'select', '[contenteditable="true"]'];
  return !blocked.some(selector => node.closest?.(selector));
}

function globalShortcutTargetAllowsPlatformAction(target) {
  return isMacPlatform() || globalShortcutTargetAllowsAppAction(target);
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
  statusEl.textContent = t('shortcuts.pinTabPrompt', {keys: `${appShortcutText('K')} Enter`});
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
    removeSessionFromLayout(fileExplorerItemId);
    return;
  }
  if (fileExplorerShortcutRestoreSlots && itemInLayout(fileExplorerItemId, fileExplorerShortcutRestoreSlots)) {
    applyLayoutSlots(fileExplorerShortcutRestoreSlots, {
      prune: false,
      message: `${fileExplorerLabel()} restored`,
    });
    fileExplorerShortcutRestoreSlots = null;
    return;
  }
  selectSession(fileExplorerItemId);
}

function handleFocusedTerminalCopyShortcut(event) {
  const session = focusedTerminal;
  if (!session) return false;
  const item = terminals.get(session);
  if (!item?.term) return false;
  if (!handleTerminalCopyShortcutKeydown(session, item.term, item.container, event)) return false;
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
notifyToggle.onclick = toggleNotifications;
document.getElementById('closeModal').onclick = () => {
  const modal = document.getElementById('modal');
  modal.classList.remove('open', 'about-open');
};
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
  const platformActionAllowed = globalShortcutTargetAllowsPlatformAction(event.target);
  if (handlePendingGlobalShortcutChord(event, key)) return;
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
      startPinTabShortcutChord();
      return;
    }
    if ((key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)) {
      event.preventDefault();
      const item = currentActiveMenuItem();
      if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);
      return;
    }
    if (key === 'b') {
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
window.addEventListener('keydown', handleGlobalShortcutKeydown, true);
window.addEventListener('resize', () => {
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

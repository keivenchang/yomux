
function paneFrameControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => ` type="button" disabled title="${esc(t('tab.unavailableFor', {name: unavailableLabel}))}" aria-label="${esc(label)}"`;
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
  const hasShareRows = shareViewMode && Array.isArray(shareInfoBranchRowsOverride);
  if (!rows.length) {
    if (hasShareRows) {
      node.innerHTML = `<div class="info-empty">${esc(t('info.empty'))}</div>`;
      return;
    }
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
    <div class="info-cell" title="${esc(row.pathTitle || row.path)}">${esc(row.pathLabel || compactHomePath(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${infoBranchCellHtml(row)}</div>
    <div class="info-cell" title="${esc(row.prTitle)}">${infoPrCellHtml(row)}</div>
    <div class="info-cell" title="${esc(row.linearTitle)}">${infoLinearCellHtml(row)}</div>
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
  const previous = infoColumnWidth(column);
  setInfoColumnWidthState(column, width);
  applyInfoColumnWidth(column);
  if (options.persist !== false) storageSet(config.storageKey, width);
  if (options.publish !== false && width !== previous) scheduleShareUiStatePublish();
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
    .catch(error => statusErr(localizedHtml('status.settingsSaveFailed', {error})));
}

function removeWatchedPr(ref) {
  const target = normalizeWatchedPrRef(ref) || String(ref || '');
  const current = initialSetting('github.watched_prs', []);
  const list = (Array.isArray(current) ? current : []).filter(item => normalizeWatchedPrRef(item) !== target && String(item).trim() !== target);
  saveSettingsPatch(settingPatch('github.watched_prs', list))
    .then(() => { statusOk(t('info.watched.removed', {ref: target})); refreshWatchedPrs(); })
    .catch(error => statusErr(localizedHtml('status.settingsSaveFailed', {error})));
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
  yoagentScrollbackLocked = false;
}

function yoagentChatHistoryIsNearBottom(history, threshold = 48) {
  if (!history) return true;
  return history.scrollHeight - history.clientHeight - history.scrollTop <= threshold;
}

function yoagentChatScrollState(node = document.getElementById('yoagent-content')) {
  const history = node?.querySelector?.('.yoagent-chat-history');
  const panelBody = node?.closest?.('.info-pane, .panel-overlay-root, .panel');
  return {
    nearBottom: yoagentChatHistoryIsNearBottom(history),
    historyTop: history ? history.scrollTop : 0,
    nodeTop: node ? node.scrollTop : 0,
    panelTop: panelBody && panelBody !== node ? panelBody.scrollTop : 0,
  };
}

function restoreYoagentChatScrollState(node, state) {
  if (!node || !state) return;
  const history = node.querySelector?.('.yoagent-chat-history');
  if (history) history.scrollTop = state.historyTop || 0;
  node.scrollTop = state.nodeTop || 0;
  const panelBody = node.closest?.('.info-pane, .panel-overlay-root, .panel');
  if (panelBody && panelBody !== node) panelBody.scrollTop = state.panelTop || 0;
  yoagentScrollbackLocked = state.nearBottom === false;
}

function installYoagentChatScrollTracker(node = document.getElementById('yoagent-content')) {
  const history = node?.querySelector?.('.yoagent-chat-history');
  if (!history || history.dataset.yoagentScrollTracker === 'true') return;
  history.dataset.yoagentScrollTracker = 'true';
  history.addEventListener('scroll', () => {
    yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom(history);
  }, {passive: true});
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
  if (shareViewMode && Array.isArray(shareInfoBranchRowsOverride)) {
    return sortedInfoBranchRows(shareInfoBranchRowsOverride, infoBranchSort);
  }
  return sortedInfoBranchRows(rawInfoBranchRows(), infoBranchSort);
}

function infoBranchCellHtml(row) {
  return row?.branchHtml || esc(row?.branch || '');
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
  const path = git?.root || git?.cwd || '';
  const label = compactHomePath(path);
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return label;
  return `${label} (worktree of ${compactHomePath(parent)})`;
}

function infoPathTitle(git) {
  const path = git?.root || git?.cwd || '';
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return path;
  return `${path} (worktree of ${parent})`;
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
      const repoUrl = git?.github_repo?.url || '';
      const prUrl = prValue?.url || (prValue?.number && repoUrl ? `${repoUrl}/pull/${prValue.number}` : '');
      const prLabel = prValue?.number ? pullRequestLinkLabel(prValue) : '';
      const prClass = prValue?.number ? pullRequestStatusClass(prValue) : '';
      const linearTitle = currentLinear.length
        ? currentLinear.map(issue => [issue.identifier, issue.state, issue.title].filter(Boolean).join(' ')).filter(Boolean).join(' · ')
        : linearIds.join(' ');
      const linearItems = currentLinear.length
        ? currentLinear.map(issue => ({
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
      rows.push({
        session,
        path: git?.root || git?.cwd || '',
        pathLabel: infoPathLabel(git),
        pathTitle: infoPathTitle(git),
        branch: branch.name || '',
        branchHtml: branchLinkHtml(git, branch.name),
        desc,
        updated: branch.updated || '',
        updatedText: branchUpdatedText(branch),
        updatedTitle: branch.updated || branchUpdatedText(branch),
        updatedTs: Number.isFinite(branch.updated_ts) ? branch.updated_ts : 0,
        prHtml: prHtml || '',
        prTitle,
        prUrl,
        prLabel,
        prClass,
        prSort: prTitle || (prValue?.number ? String(prValue.number) : ''),
        linearHtml,
        linearItems,
        linearTitle,
        current,
      });
    }
  }
  return rows;
}

function setInfoBranchSort(key) {
  if (!infoBranchSortColumns.has(key)) return;
  const previous = `${infoBranchSort.key}:${infoBranchSort.dir}`;
  if (infoBranchSort.key === key) {
    infoBranchSort = {key, dir: infoBranchSort.dir === 'asc' ? 'desc' : 'asc'};
  } else {
    infoBranchSort = {key, dir: 'asc'};
  }
  if (`${infoBranchSort.key}:${infoBranchSort.dir}` !== previous) scheduleShareUiStatePublish();
}

const infoBranchSortColumns = new Set(['session', 'path', 'branch', 'pr', 'linear', 'desc', 'updated']);

function normalizeShareInfoSort(value = {}) {
  const key = infoBranchSortColumns.has(value?.key) ? value.key : 'updated';
  const dir = value?.dir === 'asc' ? 'asc' : 'desc';
  return {key, dir};
}

function shareInfoString(value, limit = 500) {
  return String(value || '').slice(0, limit);
}

function shareInfoRowSnapshot(row = {}) {
  return {
    session: shareInfoString(row.session, 80),
    path: shareInfoString(row.path, 1000),
    pathLabel: shareInfoString(row.pathLabel, 1000),
    pathTitle: shareInfoString(row.pathTitle, 1000),
    branch: shareInfoString(row.branch, 500),
    desc: shareInfoString(row.desc, 1000),
    updated: shareInfoString(row.updated, 200),
    updatedText: shareInfoString(row.updatedText, 200),
    updatedTitle: shareInfoString(row.updatedTitle, 500),
    updatedTs: Number.isFinite(row.updatedTs) ? row.updatedTs : 0,
    prTitle: shareInfoString(row.prTitle, 1000),
    prUrl: shareInfoString(row.prUrl, 1000),
    prLabel: shareInfoString(row.prLabel, 100),
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
    branchSort: normalizeShareInfoSort(infoBranchSort),
    columnWidths: {
      branch: clampInfoColumnWidth('branch', infoBranchColumnWidthPx),
      desc: clampInfoColumnWidth('desc', infoDescColumnWidthPx),
    },
  };
  if (options.includeRows !== false) snapshot.branchRows = infoBranchRows().map(shareInfoRowSnapshot);
  return snapshot;
}

function applyShareInfoState(info = {}) {
  if (!info || typeof info !== 'object') return;
  if ('branchSort' in info) infoBranchSort = normalizeShareInfoSort(info.branchSort);
  if ('branchRows' in info) shareInfoBranchRowsOverride = cleanShareInfoRows(info.branchRows);
  const widths = info.columnWidths && typeof info.columnWidths === 'object' ? info.columnWidths : {};
  if ('branch' in widths) setInfoColumnWidth('branch', widths.branch, {persist: false, publish: false});
  if ('desc' in widths) setInfoColumnWidth('desc', widths.desc, {persist: false, publish: false});
  renderInfoPanel();
  restoreShareScrollTargetByKey('info');
}

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
      .then(() => { statusOk(localizedHtml('status.copiedTranscriptPath')); })
      .catch(error => { statusErr(localizedHtml('status.copyFailed', {error})); });
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
  const label = references.length === 1 ? references[0] : tPlural('files.pathCount', references.length);
  statusEl.innerHTML = inserted
    ? `<span class="ok">${localizedHtml('status.insertedInto', {name: label, session: sessionLabel(session)})}</span>`
    : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
}

// DOIT.57: one data-driven file-drop action registry. Agent panes keep the shipped "path first, then
// append a deictic clause" behavior; shell and server actions compose full commands/results from the
// selected action so they do not run a stray bare path before the useful command.
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

const DROP_ACTION_CATEGORIES = Object.freeze(['any', 'image', 'log', 'code', 'diff', 'data', 'doc', 'config', 'archive', 'dir']);
const DEFAULT_IMAGE_DROP_ACTION_ORDER = Object.freeze([
  'Extract the text (OCR): ; do OCR on this image and extract all of the text.',
  'Diagnose the error: ; diagnose the error/problem shown in this screenshot & suggest a fix.',
  'Describe the image: ; describe what is shown in this image.',
  'info',
]);
const DROP_ACTIONS = [
  {id: 'insert-path', cats: DROP_ACTION_CATEGORIES, kind: 'insert', label: 'Insert path', labelKey: 'drop.action.insertPath', readOnly: true},
  {id: 'img-error', cats: ['image'], kind: 'prompt', agent: true, label: 'Diagnose the error', labelKey: 'drop.action.imgError', prompt: () => 'diagnose the error/problem shown in this screenshot & suggest a fix.', aliases: ['Diagnose the error in this screenshot', 'diagnose the error or problem shown in this screenshot and suggest a fix.', 'Diagnose the error in this screenshot: ; diagnose the error or problem shown in this screenshot and suggest a fix.']},
  {id: 'img-describe', cats: ['image'], kind: 'prompt', agent: true, label: 'Describe the image', labelKey: 'drop.action.imgDescribe', prompt: () => 'describe what is shown in this image.'},
  {id: 'img-ocr', cats: ['image'], kind: 'prompt', agent: true, label: 'Extract the text (OCR)', labelKey: 'drop.action.imgOcr', prompt: () => 'do OCR on this image and extract all of the text.'},
  {id: 'log-errors', cats: ['log'], kind: 'prompt', agent: true, label: 'Find the errors', labelKey: 'drop.action.logErrors', prompt: () => 'read this log and list the errors and warnings, most important first.'},
  {id: 'log-cause', cats: ['log'], kind: 'prompt', agent: true, label: 'Find the root cause', labelKey: 'drop.action.logCause', prompt: () => 'read this log, find the root cause of the failure, and suggest a fix.'},
  {id: 'code-review', cats: ['code'], kind: 'prompt', agent: true, label: 'Review for bugs', labelKey: 'drop.action.codeReview', prompt: () => 'review this file for bugs and correctness issues.'},
  {id: 'code-explain', cats: ['code', 'config'], kind: 'prompt', agent: true, label: 'Explain what it does', labelKey: 'drop.action.codeExplain', prompt: () => 'explain what this file does.'},
  {id: 'code-security', cats: ['code', 'config'], kind: 'prompt', agent: true, label: 'Find security issues', labelKey: 'drop.action.codeSecurity', prompt: () => 'review this file for security problems.'},
  {id: 'code-tests', cats: ['code'], kind: 'prompt', agent: true, label: 'Write tests', labelKey: 'drop.action.codeTests', prompt: () => 'write tests for this file.'},
  {id: 'diff-review', cats: ['diff'], kind: 'prompt', agent: true, label: 'Review the diff', labelKey: 'drop.action.diffReview', prompt: () => 'review this diff for risks and regressions.'},
  {id: 'diff-commit', cats: ['diff'], kind: 'prompt', agent: true, label: 'Write a commit message', labelKey: 'drop.action.diffCommit', prompt: () => 'write a commit message for the change in this diff.'},
  {id: 'data-summary', cats: ['data'], kind: 'prompt', agent: true, label: 'Summarize the data', labelKey: 'drop.action.dataSummary', prompt: () => 'summarize the structure and contents of this data file (columns/schema, row count, anything notable).'},
  {id: 'data-anomaly', cats: ['data'], kind: 'prompt', agent: true, label: 'Find anomalies', labelKey: 'drop.action.dataAnomaly', prompt: () => 'look at this data file and point out anomalies or outliers.'},
  {id: 'doc-summary', cats: ['doc'], kind: 'prompt', agent: true, label: 'Summarize', labelKey: 'drop.action.docSummary', prompt: () => 'summarize this document.'},
  {id: 'doc-todos', cats: ['doc'], kind: 'prompt', agent: true, label: 'Extract the action items', labelKey: 'drop.action.docTodos', prompt: () => 'extract the action items and TODOs from this document.'},
  {id: 'dir-tree', cats: ['dir'], kind: 'prompt', agent: true, label: 'Summarize this folder', labelKey: 'drop.action.dirTree', prompt: () => 'summarize the contents of this folder.'},
  {id: 'dir-large', cats: ['dir'], kind: 'prompt', agent: true, label: 'Find the largest files', labelKey: 'drop.action.dirLarge', prompt: () => 'find the largest files in this folder.'},
  {id: 'multi-diff', cats: DROP_ACTION_CATEGORIES, kind: 'prompt', agent: true, label: 'Compare these files', labelKey: 'drop.action.multiDiff', minFiles: 2, prompt: ctx => `compare these ${ctx.paths.length} files and summarize the important differences.`},
  {id: 'multi-summary', cats: DROP_ACTION_CATEGORIES, kind: 'prompt', agent: true, label: 'Summarize all files', labelKey: 'drop.action.multiSummary', minFiles: 2, prompt: ctx => `summarize these ${ctx.paths.length} files together and call out common themes.`},
  {id: 'analyze', cats: ['any'], kind: 'prompt', agent: true, label: 'Take a look at it', labelKey: 'drop.action.analyze', prompt: () => 'take a look at this file and tell me what it is and anything notable.'},
  {id: 'shell-file', cats: DROP_ACTION_CATEGORIES.filter(cat => cat !== 'dir'), kind: 'shell', shell: true, readOnly: true, label: 'Show file type', labelKey: 'drop.action.shellFile', command: ctx => `file ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-wc', cats: ['log', 'code', 'diff', 'data', 'doc', 'config', 'any'], kind: 'shell', shell: true, readOnly: true, label: 'Count lines and bytes', labelKey: 'drop.action.shellWc', command: ctx => `wc -l -c ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-tail', cats: ['log', 'any'], kind: 'shell', shell: true, readOnly: true, label: 'Tail and watch', labelKey: 'drop.action.shellTail', command: ctx => `tail -F ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-jq', cats: ['data'], kind: 'shell', shell: true, readOnly: true, label: 'Pretty-print JSON with jq', labelKey: 'drop.action.shellJq', command: ctx => `jq . ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-column', cats: ['data'], kind: 'shell', shell: true, readOnly: true, label: 'Show as table', labelKey: 'drop.action.shellColumn', command: ctx => `column -t -s, ${dropActionQuotedPaths(ctx).join(' ')} | less -S`},
  {id: 'shell-du', cats: ['dir'], kind: 'shell', shell: true, readOnly: true, label: 'Largest files here', labelKey: 'drop.action.shellDu', command: ctx => `du -ah ${dropActionQuotedPaths(ctx).join(' ')} | sort -h | tail -40`},
  {id: 'server-info', cats: DROP_ACTION_CATEGORIES, kind: 'server', readOnly: true, label: 'Server: file info', labelKey: 'drop.action.serverInfo'},
  {id: 'server-head', cats: ['log', 'code', 'diff', 'data', 'doc', 'config', 'any'], kind: 'server', readOnly: true, label: 'Server: preview head', labelKey: 'drop.action.serverHead'},
  {id: 'server-log-errors', cats: ['log', 'any'], kind: 'server', readOnly: true, label: 'Server: scan errors', labelKey: 'drop.action.serverLogErrors'},
  {id: 'server-data-stats', cats: ['data'], kind: 'server', readOnly: true, label: 'Server: data stats + chart', labelKey: 'drop.action.serverDataStats'},
  {id: 'server-ocr', cats: ['image'], kind: 'server', readOnly: true, label: 'Server: OCR image', labelKey: 'drop.action.serverOcr'},
];

function customDropActions() {
  const lines = nestedSetting(clientSettings, 'uploads.custom_actions', []);
  if (!Array.isArray(lines)) return [];
  return lines.map((line, index) => customDropActionFromLine(line, index)).filter(Boolean);
}

function customDropActionFromLine(line, index = 0) {
  const parts = String(line || '').split('|').map(part => part.trim());
  if (parts.length < 2 || !parts[0] || !parts[1]) return null;
  const rawCats = (parts[2] || 'any').split(',').map(cat => cat.trim().toLowerCase()).filter(Boolean);
  const cats = rawCats.filter(cat => DROP_ACTION_CATEGORIES.includes(cat));
  const body = parts[1];
  const shell = body.toLowerCase().startsWith('shell:');
  return {
    id: `custom-${index}-${fuzzyCanonicalPrefixText(parts[0]).slice(0, 24) || 'action'}`,
    custom: true,
    cats: cats.length ? cats : ['any'],
    kind: shell ? 'shell' : 'prompt',
    shell,
    agent: !shell,
    readOnly: shell,
    label: parts[0],
    template: shell ? body.slice(6).trim() : body,
  };
}

function dropActionMatchesCategory(action, category) {
  const cats = Array.isArray(action?.cats) ? action.cats : ['any'];
  if (category === 'dir') return cats.includes('dir');
  return cats.includes('any') || cats.includes(category);
}

function dropActionLastKey(category) {
  return `yolomux.dropAction.last.${category || 'any'}`;
}

function rememberDropAction(category, actionId) {
  if (!actionId) return;
  storageSet(dropActionLastKey(category), actionId);
}

function normalizedDropActionOrderText(value) {
  return String(value || '')
    .trim()
    .replace(/^;\s*/, '')
    .replace(/[.:]+$/g, '')
    .replace(/\s+/g, ' ')
    .toLowerCase();
}

function dropActionPromptText(action) {
  if (action?.kind !== 'prompt') return '';
  return String(action.template ? action.template : action.prompt?.({paths: [''], category: 'image'}) || '').trim();
}

function dropActionLabelAliases(action) {
  const label = String(action?.label || '').trim();
  const aliases = [label, label.replace(/\s*\([^)]*\)\s*/g, ' ').replace(/\s+/g, ' ').trim()];
  return aliases.map(normalizedDropActionOrderText).filter(Boolean);
}

function dropActionDisplayLabel(action) {
  if (!action) return '';
  if (action.menuLabel) return String(action.menuLabel);
  if (action.labelKey) return t(action.labelKey);
  return String(action.label || action.id || '');
}

function dropActionOrderAliases(action) {
  const aliases = [action?.id, ...dropActionLabelAliases(action)];
  if (Array.isArray(action?.aliases)) aliases.push(...action.aliases);
  if (action?.kind === 'prompt') {
    const clause = dropActionPromptText(action);
    if (clause) {
      aliases.push(clause, `; ${clause}`);
      dropActionLabelAliases(action).forEach(label => {
        aliases.push(`${label}: ${clause}`, `${label}: ; ${clause}`);
      });
    }
  }
  if (action?.id === 'server-info') aliases.push('info', 'file info', 'server info');
  if (action?.id === 'server-ocr') aliases.push('server ocr', 'ocr result');
  if (action?.id === 'shell-file') aliases.push('file', 'file type');
  if (action?.id === 'insert-path') aliases.push('insert path', 'path');
  return aliases.map(normalizedDropActionOrderText).filter(Boolean);
}

function imageDropActionPreferenceId(value) {
  const raw = String(value || '').trim();
  const normalized = normalizedDropActionOrderText(value);
  if (!normalized) return '';
  if (['insert-path', 'insert path', 'path', 'server-ocr', 'server ocr', 'server ocr image', 'ocr result', 'shell-file', 'show file type', 'file', 'file type'].includes(normalized)) return '';
  const exact = DROP_ACTIONS.find(candidate => dropActionOrderAliases(candidate).includes(normalized));
  if (exact) return exact.id;
  const colonIndex = raw.indexOf(':');
  if (colonIndex < 0) return '';
  const labelText = normalizedDropActionOrderText(raw.slice(0, colonIndex));
  const promptText = normalizedDropActionOrderText(raw.slice(colonIndex + 1));
  if (!promptText) return '';
  const promptMatches = DROP_ACTIONS.filter(candidate => dropActionOrderAliases(candidate).includes(promptText));
  if (!promptMatches.length) return '';
  const labelMatch = promptMatches.find(candidate => dropActionLabelAliases(candidate).includes(labelText));
  return (labelMatch || promptMatches[0]).id || '';
}

function canonicalDropActionPreferenceLabel(action) {
  if (!action) return '';
  if (action.id === 'server-info') return action.label || 'Server: file info';
  const prompt = dropActionPromptText(action);
  if (action.kind === 'prompt' && prompt) return `${action.label}: ; ${prompt}`;
  return action.label || action.id || '';
}

function imageDropActionPreferenceLabel(value, actionId) {
  const action = DROP_ACTIONS.find(candidate => candidate.id === actionId);
  const raw = String(value || '').trim();
  if (!raw) return canonicalDropActionPreferenceLabel(action);
  if (actionId === 'server-info') return action?.label || 'Server: file info';
  if (normalizedDropActionOrderText(raw) === normalizedDropActionOrderText(actionId)) return canonicalDropActionPreferenceLabel(action);
  return raw;
}

function preferredDropActionEntries(category) {
  if (category !== 'image') return [];
  const configured = nestedSetting(clientSettings, 'uploads.image_action_order', DEFAULT_IMAGE_DROP_ACTION_ORDER);
  const rawOrder = Array.isArray(configured) && configured.length ? configured : DEFAULT_IMAGE_DROP_ACTION_ORDER;
  const seen = new Set();
  const ordered = [];
  const addValue = value => {
    const actionId = imageDropActionPreferenceId(value);
    if (!actionId || seen.has(actionId)) return;
    seen.add(actionId);
    ordered.push({id: actionId, label: imageDropActionPreferenceLabel(value, actionId)});
  };
  rawOrder.forEach(addValue);
  if (!ordered.length && rawOrder !== DEFAULT_IMAGE_DROP_ACTION_ORDER) DEFAULT_IMAGE_DROP_ACTION_ORDER.forEach(addValue);
  return ordered;
}

function preferredDropActionOrder(category) {
  return preferredDropActionEntries(category).map(entry => entry.id);
}

function sortDropActionsByPreference(actions, category, options = {}) {
  const preferred = preferredDropActionEntries(category);
  if (!preferred.length) return actions;
  const byId = new Map(actions.map(action => [action.id, action]));
  const ordered = [];
  preferred.forEach(entry => {
    const action = byId.get(entry.id);
    if (!action) return;
    ordered.push({...action, menuLabel: action.custom ? (entry.label || action.label) : ''});
  });
  return ordered;
}

function sortDropActionsForCategory(actions, category, options = {}) {
  const hasPreferredOrder = preferredDropActionOrder(category).length > 0;
  actions = sortDropActionsByPreference(actions, category, options);
  if (hasPreferredOrder) return actions;
  const lastId = storageGet(dropActionLastKey(category), '');
  if (!lastId) return actions;
  const insert = actions.find(action => action.id === 'insert-path');
  const rest = actions.filter(action => action.id !== 'insert-path');
  const last = rest.find(action => action.id === lastId);
  if (!last) return actions;
  const ordered = [last, ...rest.filter(action => action !== last)];
  return insert && options.pathInserted !== true ? [insert, ...ordered] : ordered;
}

function dropActionsFor(category, agentKind, count = 1, options = {}) {
  const isAgent = agentKind === 'claude' || agentKind === 'codex';
  const preferredImageIds = category === 'image' ? new Set(preferredDropActionOrder(category)) : new Set();
  const all = [DROP_ACTIONS[0], ...customDropActions(), ...DROP_ACTIONS.slice(1)];
  const filtered = all.filter(action => {
    const configuredImageAction = preferredImageIds.has(action.id);
    if (action.id === 'insert-path' && options.pathInserted === true) return false;
    if (action.agent && !isAgent) return false;
    if (action.shell && isAgent && options.includeShellForAgents !== true && !configuredImageAction) return false;
    if (action.kind === 'server' && options.includeServer === false) return false;
    const minFiles = Number(action.minFiles || 1);
    const maxFiles = Number(action.maxFiles || 0);
    if (count < minFiles) return false;
    if (maxFiles > 0 && count > maxFiles) return false;
    return dropActionMatchesCategory(action, category);
  });
  return sortDropActionsForCategory(filtered, category, options).slice(0, 9);
}

function dropSuggestionsFor(category, agentKind, count = 1, options = {}) {
  return dropActionsFor(category, agentKind, count, options);
}

function dropActionContext(action, paths, category, agentKind, options = {}) {
  return {action, paths, category, agentKind, session: options.session || '', kind: options.kind || 'file', pathInserted: options.pathInserted === true};
}

function dropActionQuotedPaths(context) {
  return (context.paths || []).map(shellQuote);
}

function formatDropActionTemplate(template, context) {
  const paths = context.paths || [];
  const first = paths[0] || '';
  const values = {
    path: first,
    qpath: shellQuote(first),
    paths: paths.join(' '),
    qpaths: paths.map(shellQuote).join(' '),
    name: basenameOf(first),
    count: String(paths.length),
    category: context.category || 'any',
  };
  return String(template || '').replace(/\{(path|qpath|paths|qpaths|name|count|category)\}/g, (_m, key) => values[key] || '');
}

function composeDropSuggestion(action, context = {}) {
  if (!action) return '';
  const paths = Array.isArray(context.paths) && context.paths.length ? context.paths : ['/var/log/app.log'];
  const category = context.category || fileDropCategory(paths[0], context.kind || 'file');
  const fullContext = dropActionContext(action, paths, category, context.agentKind || '', context);
  if (action.kind === 'insert') return `${paths.map(shellQuote).join(' ')} `;
  if (action.kind === 'shell') {
    const command = action.template ? formatDropActionTemplate(action.template, fullContext) : action.command?.(fullContext);
    return String(command || '').trim();
  }
  if (action.kind === 'server') return '';
  const clause = action.template ? formatDropActionTemplate(action.template, fullContext) : action.prompt?.(fullContext);
  return String(clause || '').trim();
}

function insertedDropActionText(action, context = {}) {
  const text = composeDropSuggestion(action, context);
  if (!text) return '';
  const pathInserted = context.pathInserted === true;
  if (action.kind === 'prompt' && pathInserted) return `; ${text}`;
  if (action.kind === 'shell' && pathInserted) {
    const isAgent = context.agentKind === 'claude' || context.agentKind === 'codex';
    if (isAgent) {
      if (action.id === 'shell-file') return '; show the file type';
      return `; ${String(action.label || text).toLowerCase()}`;
    }
    return `\u0015${text}`;
  }
  return text;
}

function terminalDropShouldInsertPathFirst(session, payload) {
  const paths = Array.isArray(payload?.paths) ? payload.paths.filter(Boolean) : [payload?.path].filter(Boolean);
  if (!paths.length) return false;
  const agentKind = sessionAgentKind(session);
  if (agentKind !== 'claude' && agentKind !== 'codex') return false;
  const category = fileDropCategory(paths[0], payload?.kind);
  return dropActionsFor(category, agentKind, paths.length, {pathInserted: true, includeServer: false}).some(action => action.kind === 'prompt');
}

async function runDropAction(action, context) {
  const paths = context.paths || [];
  const category = context.category || fileDropCategory(paths[0], context.kind || 'file');
  rememberDropAction(category, action.id);
  if (action.kind === 'server') {
    await runServerDropAction(action, paths);
    return;
  }
  const text = composeDropSuggestion(action, context);
  if (!text) return;
  const suffix = insertedDropActionText(action, context);
  const shellActionActsAsAgentPrompt = action.kind === 'shell' && context.pathInserted && (context.agentKind === 'claude' || context.agentKind === 'codex');
  const autoEnter = action.kind === 'shell' && action.readOnly === true && !shellActionActsAsAgentPrompt && boolSetting('uploads.suggestion_autorun', false);
  const inserted = insertIntoTerminal(context.session, `${suffix}${autoEnter ? '\r' : ''}`);
  statusEl.innerHTML = inserted
    ? `<span class="ok">${localizedHtml(autoEnter ? 'status.ranDropAction' : 'status.insertedDropAction', {name: action.label || action.id})}</span>`
    : `<span class="err">${terminalNotConnectedHtml(context.session)}</span>`;
}

async function runServerDropAction(action, paths) {
  try {
    const payload = await apiFetchJson('/api/drop-action/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action.id, paths}),
    });
    showDropActionResult(payload);
  } catch (error) {
    statusErr(localizedHtml('status.copyFailed', {error: error?.payload?.error || error}));
  }
}

function showDropActionResult(payload) {
  showFileEditorDecisionDialog({
    title: payload?.title || t('upload.dropActionResultTitle'),
    bodyHtml: `<div class="drop-action-result"><pre>${esc(payload?.body || payload?.error || '')}</pre></div>`,
    actions: [{id: 'close', label: t('common.close')}],
    className: 'drop-action-result-dialog',
  });
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

function dropSuggestionIndexFromKeyEvent(event) {
  if (event?.ctrlKey || event?.metaKey) return -1;
  const code = String(event?.code || '');
  if (/^Digit[1-9]$/.test(code)) return Number(code.slice(5)) - 1;
  if (/^Numpad[1-9]$/.test(code)) return Number(code.slice(6)) - 1;
  const key = String(event?.key || '');
  if (/^[1-9]$/.test(key)) return Number(key) - 1;
  return -1;
}

function showTerminalDropSuggestions(session, payload, x, y, options = {}) {
  dismissTerminalDropSuggestions();
  const paths = Array.isArray(payload?.paths) ? payload.paths.filter(Boolean) : [payload?.path].filter(Boolean);
  if (!paths.length) return false;
  const category = fileDropCategory(paths[0], payload?.kind);
  const agentKind = sessionAgentKind(session);
  const pathInserted = options.pathInserted === true;
  const suggestions = dropActionsFor(category, agentKind, paths.length, {pathInserted});
  if (!suggestions.length) return false;
  const rows = suggestions.map(action => ({
    label: dropActionDisplayLabel(action),
    run: () => runDropAction(action, dropActionContext(action, paths, category, agentKind, {pathInserted, session, kind: payload?.kind})),
  }));

  const node = document.createElement('div');
  node.className = 'terminal-drop-suggestions';
  node.setAttribute('role', 'listbox');
  const head = document.createElement('div');
  head.className = 'terminal-drop-suggestions-head';
  const prefix = pathInserted ? t('drop.pathInserted') : (paths.length > 1 ? tPlural('drop.files', paths.length) : basenameOf(paths[0]));
  head.textContent = t('drop.suggestionHint', {prefix, max: Math.min(rows.length, 9)});
  node.appendChild(head);
  rows.forEach((row, index) => {
    const item = document.createElement('div');
    item.className = 'terminal-drop-suggestion';
    item.setAttribute('role', 'option');
    item.tabIndex = -1;
    const combo = document.createElement('span');
    combo.className = 'terminal-drop-suggestion-combo';
    combo.textContent = String(index + 1);
    const label = document.createElement('span');
    label.className = 'terminal-drop-suggestion-label';
    label.textContent = row.label;
    item.append(combo, label);
    item.addEventListener('click', () => { row.run(); dismissTerminalDropSuggestions(); });
    node.appendChild(item);
  });
  document.body.appendChild(node);
  // Anchor at the drop point when there is one; for paste (no drop point) anchor near the session's
  // terminal, falling back to the viewport so the overlay is always on-screen.
  let anchorX = x;
  let anchorY = y;
  if (!Number.isFinite(anchorX) || !Number.isFinite(anchorY)) {
    const host = document.getElementById(terminalDomId(session)) || document.getElementById(panelDomId(session));
    const hostRect = host?.getBoundingClientRect?.();
    const fallbackViewport = appViewport();
    anchorX = hostRect ? hostRect.left + 16 : fallbackViewport.width / 2;
    anchorY = hostRect ? hostRect.top + 16 : fallbackViewport.height / 3;
  }
  const rect = node.getBoundingClientRect();
  const viewport = appViewport();
  node.style.left = `${Math.round(Math.max(8, Math.min(anchorX, viewport.width - rect.width - 8)))}px`;
  node.style.top = `${Math.round(Math.max(8, Math.min(anchorY, viewport.height - rect.height - 8)))}px`;

  const onKeyDown = event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      dismissTerminalDropSuggestions();
      return;
    }
    // Press 1..9 to pick a row. Accept top-row digits, numpad digits, and browsers that only provide
    // event.key. Exclude platform browser tab-switch shortcuts.
    const index = dropSuggestionIndexFromKeyEvent(event);
    if (index >= 0) {
      event.preventDefault();
      event.stopPropagation();
      if (index < rows.length) {
        rows[index].run();
        dismissTerminalDropSuggestions();
      }
      return;
    }
    // Any other key: the overlay is advisory — dismiss it but let the keystroke reach the terminal.
    dismissTerminalDropSuggestions();
  };
  const onPointerDown = event => { if (!node.contains(event.target)) dismissTerminalDropSuggestions(); };
  const timeoutMs = Number.isFinite(Number(options.timeoutMs)) ? Math.max(1, Number(options.timeoutMs)) : 6000;
  const timer = setTimeout(dismissTerminalDropSuggestions, timeoutMs);
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
      const pathInserted = terminalDropShouldInsertPathFirst(session, payload);
      if (pathInserted) insertFileDragPayloadIntoTerminal(session, payload);
      const shown = showTerminalDropSuggestions(session, payload, event.clientX, event.clientY, {pathInserted});
      if (!shown && !pathInserted) insertFileDragPayloadIntoTerminal(session, payload);
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
      statusErr(localizedHtml('status.selectPaneForImagePaste'));
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
    statusErr(localizedHtml('status.uploadFailed', {error: error?.payload?.error || error}));
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
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  if (autoFocusEnabled) item.term?.focus?.();
  return true;
}

function noteTerminalExplicitInput(session) {
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
}

function terminalDataIsPassiveFocusReport(data) {
  return /^\x1b\[[IO]$/.test(String(data || ''));
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
      .catch(error => statusErr(localizedHtml('terminal.window.failed', {error: error.message || error})));
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
  const container = document.getElementById(terminalDomId(session));
  const boundToCurrentContainer = Boolean(item?.term && container?.isConnected && item.container === container);
  if (item && boundToCurrentContainer && readyState !== undefined && readyState !== WebSocket.CLOSING && readyState !== WebSocket.CLOSED) return;
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
      if (!shareViewMode) scheduleRemoteResize(session, 50);
    }
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (shareViewMode) {
      handleShareViewSocketMessage(session, item, event.data);
    } else if (event.data instanceof ArrayBuffer) {
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
  const term = new TerminalCtor({
    cols: size.cols,
    rows: size.rows,
    cursorBlink: true,
    convertEol: false,
    fontFamily: terminalFontFamily,
    fontSize: terminalFontSize,
    letterSpacing: 0,
    lineHeight: 1.0,
    scrollback: terminalScrollback,
    disableStdin: readOnlyMode && !shareWriteMode,
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
  };
  terminals.set(session, item);
  bindTerminalContainerForSession(session, term, container);
  term.onFocus?.(() => {
    setFocusedTerminal(session);
  });
  term.onBlur?.(() => {
    clearFocusedTerminal(session);
  });
  term.onData(data => {
    if (readOnlyMode && !shareWriteMode) return;
    const filtered = stripTerminalQueryResponses(data);
    if (!filtered) return;
    if (shareReplayShellActive && shareWriteMode) {
      if (!terminalDataIsPassiveFocusReport(filtered)) noteTerminalExplicitInput(session);
      shareSendTerminalInputIntent(session, filtered);
      return;
    }
    const current = terminals.get(session);
    const socket = current?.socket;
    if (socket?.readyState === WebSocket.OPEN) {
      if (!terminalDataIsPassiveFocusReport(filtered)) noteTerminalExplicitInput(session);
      socket.send(JSON.stringify({type: 'input', data: filtered}));
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
        scheduleShareUiStatePublish();
      }
      statusErr(localizedHtml('status.yoloApprovalFailed', {error: payload.error || t('status.yoloApprovalFailedDefault')}));
      return;
    }
    statusErr(localizedHtml('status.yoloRequestFailed', {error}));
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
  scheduleShareUiStatePublish();
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
    const action = payload?.last_action ? t('yolo.actionSuffix', {action: payload.last_action}) : '';
    const readonly = readOnlyMode ? t('yolo.readonlySuffix') : '';
    button.title = enabled
      ? t('yolo.buttonOnForSession', {session: sessionLabel(session), action, readonly})
      : locked
        ? t('yolo.buttonOwnedBy', {owner: autoApproveOwnerLabel(payload)})
      : t('yolo.buttonOffForSession', {session: sessionLabel(session), readonly});
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
  dismiss.setAttribute('aria-label', t('update.dismiss'));
  dismiss.textContent = '×';
  dismiss.addEventListener('click', () => banner.remove());
  banner.append(msg, reload, dismiss);
  document.body.appendChild(banner);
}

function maybeHandleServerVersionChange(serverVersion) {
  // The boot version (bootstrap.version) only updates on page load; this lets a
  // long-lived open client learn that a newer server shipped, via the metadata poll.
  if (!serverVersion || serverVersion === bootstrap.version) return;
  if (!updateNotificationAllowsVersion(bootstrap.version, serverVersion)) return;
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
  if (typeof warmTabberDataOnLaunch === 'function') warmTabberDataOnLaunch();
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
    const preview = document.getElementById(transcriptDomId(session));
    const info = transcriptMeta.sessions?.[session];
    const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
    updatePanelHeader(session, info);
    renderSummaryContext(session, info, agent);
    if (!preview) continue;
    if (agent?.transcript) {
      updateTranscriptPathRow(session, agent.transcript);
      if (options.refreshContext === false) continue;
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

function updatePanelMeta(session, info) {
  const meta = document.getElementById(`meta-${session}`);
  if (!meta) return;
  meta.innerHTML = stripTitleAttrs(projectMetaHtml(session, info));
  meta.removeAttribute('title');
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
  updatePanelMeta(session, info);
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

function scheduleReconnectResync(reason = '') {
  if (reconnectResyncTimer) clearTimeout(reconnectResyncTimer);
  reconnectResyncTimer = setTimeout(() => {
    reconnectResyncTimer = null;
    refreshAll();
  }, reconnectResyncDebounceMs);
}

function installReconnectResyncHandlers() {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') scheduleReconnectResync('visible');
  });
  window.addEventListener('online', () => scheduleReconnectResync('online'));
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
  await waitForYolomuxFontsReady({timeoutMs: 0});
  syncAppViewportBreakpointClasses();
  if (!shareViewMode) installClientEventStream();
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
  if (!shareViewMode) {
    await loadNotifyStatus();
    await loadAutoStatuses();
  }
  bindClipboardPaste();
  if (!shareViewMode) {
    await refreshTranscripts({refreshAuto: false});
  } else {
    transcriptMeta = {session_order: sessions.slice(), sessions: Object.fromEntries(sessions.map(session => [session, {target: session}]))};
    transcriptMetaLoaded = true;
    await refreshTranscripts({refreshAuto: false, refreshActivity: false});
  }
  renderSessionButtons();
  renderPanels([], {prune: false});
  installYolomuxFontMetricRefresh();
  seedVisualActivePaneItem(activeSessions);
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

function updateDryRunEnabled() {
  return typeof urlFlagEnabled === 'function' && urlFlagEnabled('updateDryRun');
}

function updateActionButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'toast-action';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

async function triggerSelfUpdate() {
  const dry = updateDryRunEnabled();
  const confirmed = window.confirm(dry
    ? 'Dry run: simulate installing a YOLOmux update? Nothing is pulled and the server is not restarted.'
    : 'Install the latest YOLOmux update and restart now?');
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/self-update${dry ? '?dryrun=1' : ''}`, {method: 'POST'});
    const data = await res.json().catch(() => ({}));
    const title = data.ok ? (data.restarting ? 'Installing update...' : 'Software Update') : 'Update failed';
    showToast(title, [data.message || (data.ok ? 'done' : 'see server logs')]);
  } catch (error) {
    showToast('Update failed', [String(error)]);
  }
}

// Non-intrusive "a newer version exists" cue: unhide the topbar badge and show one dismissible toast
// with an "Update Now" action (admin-only; the endpoint rejects readonly).
function applyUpdateAvailable(status) {
  if (!status || !status.available) return;
  if (status.notify === false) return;
  const badge = document.querySelector('[data-update-badge]');
  if (badge) {
    badge.hidden = false;
    badge.title = `YOLOmux update available${status.target ? ` (${status.target})` : ''} - click to update now`;
  }
  showToast('YOLOmux update available', [
    `A new YOLOmux update is available${status.target ? ` (${status.target})` : ''}.`,
  ], {
    actions: [updateActionButton('Update Now', triggerSelfUpdate)],
    countdownMs: 4 * 60 * 60 * 1000,  // keep the update cue up for 4 hours, not the default ~10s
    className: 'attention-alert toast toast-update',  // solid (opaque) background, not the translucent default
  });
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
  if (!body || !notificationsEnabled) return;
  const session = String(notification.session || '').trim();
  const tag = `yoagent-job:${session || 'global'}:${body}`;
  showToast(title, [body], {session});
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
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

function handleClientPushEvent(type, payload = {}) {
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
  if (type === 'yoagent_conversation_changed') {
    loadYoagentConversation({force: true, render: infoPanelSubTab === 'yoagent', scrollBottom: 'auto'}).catch(error => console.warn('YO!agent conversation refresh failed', error));
    return;
  }
  if (type === 'yoagent_stream_delta') {
    if (typeof applyYoagentStreamPayload === 'function' && applyYoagentStreamPayload(payload)) {
      renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto'});
    }
    return;
  }
  if (type === 'yoagent_jobs_changed') {
    maybeNotifyYoagentJob(payload.notification || {});
    return;
  }
  if (type === 'yoagent_skills_changed') {
    refreshActivitySummary({force: true, render: infoPanelSubTab === 'yoagent'}).catch(error => console.warn('YO!agent skills refresh failed', error));
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
    refreshAutoStatuses().catch(error => console.warn('client-events ready auto-status refresh failed', error));
  });
  source.addEventListener('ping', event => {
    clientEventsConnected = true;
    recordSseDebugEvent('ping', clientEventEnvelope(event), event);
  });
  source.onerror = () => { clientEventsConnected = false; };
  for (const type of ['settings_changed', 'auto_approve_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta']) {
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
let shareReplayPointerFramePending = false;
let shareReplayPointerLastPayload = null;
let shareReplayLastKeyframeBytes = 0;
let shareReplayLastDeltaBytes = 0;
let shareReplayLastLatencyMs = null;
let shareReplayLastFrameReceivedAt = 0;
let shareReplayLastRedactionPolicyVersion = 1;
let shareStatusLastRefreshAt = 0;
let shareStatusRefreshInFlight = false;
const shareViewerStatusBackupRefreshMs = 30000;
const shareHostStatusBackupRefreshMs = 3000;
const yolomuxFontReadyTimeoutMs = 2500;
const shareReplayKeyframeRequestInitialBackoffMs = 5000;
const shareReplayKeyframeRequestMinIntervalMs = 5000;
const shareReplayKeyframeRequestMaxBackoffMs = 5000;
const shareGeometryResyncMinIntervalMs = 10000;
const shareReplayHostKeyframeMinIntervalMs = shareReplayKeyframeRequestMinIntervalMs;
const shareReplayPostTopologyKeyframeQuietMs = 3000;
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
  if (message.type === shareMirrorProtocol.frames.domDelta) return applyShareReplayDelta(payload, message);
  if (shareDropStaleMirrorFrame(message)) return true;
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
  for (const message of pending) socket.send(JSON.stringify(message));
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

// Share mirror protocol owner. Replay code must add names here first, then reference these constants.
const shareMirrorFrameTypes = Object.freeze({
  uiState: 'ui-state',
  layout: 'layout',
  viewport: 'viewport',
  appearance: 'appearance',
  popupLayer: 'popup-layer',
  geometryDigest: 'geometry-digest',
  hostResize: 'host-resize',
  pointer: 'pointer',
  scroll: 'scroll',
  fileVersion: 'file-version',
  activeTab: 'active-tab',
  focus: 'focus',
  finderMode: 'finder-mode',
  menu: 'menu',
  shareStatus: 'share-status',
  domKeyframe: 'dom-keyframe',
  domDelta: 'dom-delta',
  domKeyframeRequest: 'dom-keyframe-request',
  domKeyframeAck: 'dom-keyframe-ack',
  domReplayError: 'dom-replay-error',
  terminalHostResize: 'terminal-host-resize',
  textWrapMetrics: 'text-wrap-metrics',
  inputIntent: 'input-intent',
});

const shareMirrorReplayFrameTypes = Object.freeze([
  shareMirrorFrameTypes.domKeyframe,
  shareMirrorFrameTypes.domDelta,
  shareMirrorFrameTypes.domKeyframeRequest,
  shareMirrorFrameTypes.domKeyframeAck,
  shareMirrorFrameTypes.domReplayError,
  shareMirrorFrameTypes.terminalHostResize,
]);

const shareMirrorProtocol = Object.freeze({
  version: 1,
  frames: shareMirrorFrameTypes,
  replayFrameTypes: shareMirrorReplayFrameTypes,
  sequencedFrameTypes: Object.freeze([
    shareMirrorFrameTypes.uiState,
    shareMirrorFrameTypes.layout,
    shareMirrorFrameTypes.viewport,
    shareMirrorFrameTypes.appearance,
    shareMirrorFrameTypes.popupLayer,
    shareMirrorFrameTypes.geometryDigest,
    shareMirrorFrameTypes.hostResize,
    ...shareMirrorReplayFrameTypes,
  ]),
  keyframeReasons: Object.freeze(['join', 'gap', 'digest', 'replay-error', 'backpressure', 'topology', 'manual-debug']),
  sequenceFields: Object.freeze(['epoch', 'sequence', 'baseSequence']),
  redaction: Object.freeze({
    policyVersion: 1,
    metadataFields: Object.freeze(['policyVersion', 'removedCount']),
  }),
  terminalPlaceholder: Object.freeze({
    dataset: 'shareTerminalPlaceholder',
    fields: Object.freeze(['placeholderId', 'session', 'rows', 'cols', 'terminalEpoch']),
  }),
  inputIntentTypes: Object.freeze({
    terminalInput: 'terminal-input',
    terminalPaste: 'terminal-paste',
    terminalScroll: 'terminal-scroll',
    tabActivate: 'tab-activate',
    menuCommand: 'menu-command',
    hostCommand: 'host-command',
  }),
  debugNames: Object.freeze({
    domKeyframe: 'DOM keyframe',
    domDelta: 'DOM delta',
    keyframeRequest: 'DOM keyframe request',
    replayError: 'DOM replay error',
    terminalPlaceholder: 'terminal placeholder',
  }),
});

// End share mirror protocol owner.

const shareMirrorSequencedFrameTypes = new Set(shareMirrorProtocol.sequencedFrameTypes);
const shareMirrorReplayFrameTypeSet = new Set(shareMirrorProtocol.replayFrameTypes);

function shareMirrorFrameTypeIsSequenced(type) {
  return shareMirrorSequencedFrameTypes.has(String(type || ''));
}

function shareMirrorFrameTypeIsReplay(type) {
  return shareMirrorReplayFrameTypeSet.has(String(type || ''));
}

function shareMirrorFrameTypeIsDomReplayContent(type) {
  const cleanType = String(type || '');
  return cleanType === shareMirrorProtocol.frames.domKeyframe || cleanType === shareMirrorProtocol.frames.domDelta;
}

function shareMirrorFrameReason(type, options = {}) {
  const reason = String(options.reason || type || 'update').trim();
  return reason || 'update';
}

function shareNextDomReplayFrameMetadata(type, options = {}) {
  if (type === shareMirrorProtocol.frames.domKeyframe || options.resetEpoch === true) {
    shareReplayMirrorEpoch = Math.max(1, Math.floor(Number(shareReplayMirrorEpoch) || 1) + 1);
  }
  shareReplayMirrorSequence = Math.max(0, Math.floor(Number(shareReplayMirrorSequence) || 0)) + 1;
  return {
    epoch: shareReplayMirrorEpoch,
    sequence: shareReplayMirrorSequence,
    reason: shareMirrorFrameReason(type, options),
  };
}

function shareNextMirrorFrameMetadata(type, options = {}) {
  if (shareMirrorFrameTypeIsDomReplayContent(type)) return shareNextDomReplayFrameMetadata(type, options);
  if (type === shareMirrorProtocol.frames.uiState || options.resetEpoch === true) {
    shareMirrorEpoch = Math.max(1, Math.floor(Number(shareMirrorEpoch) || 1) + 1);
  }
  shareMirrorSequence = Math.max(0, Math.floor(Number(shareMirrorSequence) || 0)) + 1;
  return {
    epoch: shareMirrorEpoch,
    sequence: shareMirrorSequence,
    reason: shareMirrorFrameReason(type, options),
  };
}

function shareBuildUiMessage(type, payload = {}, options = {}) {
  const message = {type, payload, sender: shareClientId};
  if (shareMirrorFrameTypeIsReplay(type)) message.version = shareMirrorProtocol.version;
  if (shareMirrorFrameTypeIsSequenced(type)) Object.assign(message, shareNextMirrorFrameMetadata(type, options));
  if (type === shareMirrorProtocol.frames.domDelta) {
    const explicitBase = Number(payload?.baseSequence ?? options.baseSequence);
    message.baseSequence = Number.isFinite(explicitBase) ? Math.max(0, Math.round(explicitBase)) : Math.max(0, Number(message.sequence || 0) - 1);
  }
  return message;
}

function shareSendInputIntent(payload = {}, options = {}) {
  if (!shareViewMode || !shareWriteMode || !shareToken || !payload || typeof payload !== 'object') return false;
  if (shareReplayShellActive) {
    return shareReplaySendUiMessage(shareBuildUiMessage(shareMirrorProtocol.frames.inputIntent, payload, options));
  }
  sharePublish(shareMirrorProtocol.frames.inputIntent, payload, options);
  return true;
}

function shareSendTerminalInputIntent(session, data) {
  const filtered = stripTerminalQueryResponses(data);
  if (!filtered) return false;
  return shareSendInputIntent({
    intent: shareMirrorProtocol.inputIntentTypes.terminalInput,
    session: String(session || ''),
    data: filtered,
  }, {reason: 'terminal-input'});
}

function applyShareInputIntent(payload = {}) {
  if (shareViewMode || !payload || typeof payload !== 'object') return false;
  const intent = String(payload.intent || '');
  if (intent === shareMirrorProtocol.inputIntentTypes.tabActivate) {
    const item = resolveLayoutItem(payload.item || payload.session || '');
    const slot = slotForItem(item);
    if (!slot) return false;
    activatePaneTab(slot, item, {userInitiated: false});
    return true;
  }
  if (intent === shareMirrorProtocol.inputIntentTypes.hostCommand && payload.command === 'request-keyframe') {
    return sharePublishDomKeyframe('manual-debug');
  }
  return false;
}

function shareReplayFeatureEnabled() {
  if (!shareViewMode && shareHasActiveShare()) return true;
  return shareReplayEnabled === true;
}

function shareReplayHostNodeId(node) {
  if (!node || (typeof node !== 'object' && typeof node !== 'function') || (typeof Node !== 'undefined' && !(node instanceof Node) && !node.localName && !node.tagName && Number(node.nodeType) !== 3)) {
    return 0;
  }
  const existing = shareReplayHostNodeIds.get(node);
  if (existing) return existing;
  const next = Math.max(1, Math.round(Number(shareReplayHostNextNodeId) || 1));
  shareReplayHostNextNodeId = next + 1;
  shareReplayHostNodeIds.set(node, next);
  return next;
}

function shareReplayRememberSerializedNode(context, node) {
  if (!context || !node || (typeof node !== 'object' && typeof node !== 'function')) return;
  if (!Array.isArray(context.mirroredNodes)) context.mirroredNodes = [];
  context.mirroredNodes.push(node);
}

function shareReplayHostMirroredNodeSet(nodes = []) {
  const result = new WeakSet();
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node && (typeof node === 'object' || typeof node === 'function')) result.add(node);
  }
  return result;
}

function shareReplayHostMergeMirroredNodes(nodes = []) {
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node && (typeof node === 'object' || typeof node === 'function')) shareReplayHostMirroredNodes.add(node);
  }
}

function shareReplayHostForgetMirroredSubtree(node) {
  if (!node || (typeof node !== 'object' && typeof node !== 'function')) return;
  shareReplayHostMirroredNodes.delete(node);
  for (const child of Array.from(node.childNodes || node.children || [])) shareReplayHostForgetMirroredSubtree(child);
}

function shareReplaySerializedNodeId(node, context) {
  if (context?.useStableNodeIds === true) return shareReplayHostNodeId(node);
  const next = Math.max(1, Math.round(Number(context.nextNodeId) || 1));
  context.nextNodeId = next + 1;
  return next;
}

function shareReplayDatasetAttributeName(key = '') {
  return `data-${String(key || '').replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)}`;
}

function shareReplayElementHasFlag(element, name) {
  const dataKey = name.replace(/^data-/, '').replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  return element?.hasAttribute?.(name) || element?.dataset?.[dataKey] !== undefined;
}

const shareReplayBlockedTags = new Set(['script', 'iframe', 'object', 'embed', 'style']);
const shareReplayAllowedHtmlTags = new Set([
  'a', 'abbr', 'article', 'aside', 'b', 'br', 'button', 'canvas', 'code', 'dd', 'del', 'details', 'div', 'dl', 'dt', 'em', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'i', 'img', 'input', 'kbd', 'label', 'legend', 'li', 'main', 'mark', 'menu', 'nav', 'ol', 'option', 'p', 'pre', 'progress', 's', 'samp', 'section', 'select', 'small', 'span', 'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'textarea', 'tfoot', 'th', 'thead', 'time', 'tr', 'u', 'ul',
]);
const shareReplayAllowedSvgTags = new Set([
  'circle', 'defs', 'ellipse', 'g', 'line', 'path', 'polyline', 'rect', 'svg', 'text', 'title', 'tspan',
]);
const shareReplaySvgNamespace = 'http://www.w3.org/2000/svg';

function shareReplayRedactText(value) {
  let text = String(value ?? '');
  for (const secret of shareDebugSecretValues()) {
    text = text.split(secret).join('[redacted-share-token]');
  }
  return text
    .replace(/(?:https?:\/\/[^\s"'<>]+)?\/share\/[A-Za-z0-9_-]+(?:#[^\s"'<>]*)?/g, '[redacted-share-url]')
    .replace(/([?#&](?:t|token|share|shareToken|share_token)=)[^&#\s"']+/gi, '$1[redacted-share-token]')
    .replace(/\b((?:token|shareToken|share_token)=)[^&#\s"']+/gi, '$1[redacted-share-token]');
}

function shareReplayAttributeIsTokenBearing(name = '') {
  return /(?:^|[-_:])(?:token|sharetoken|share-token|share_token|secret|password)(?:$|[-_:])/i.test(String(name || ''));
}

function shareReplayUrlAttributeIsUnsafe(name = '', value = '') {
  const cleanName = String(name || '').toLowerCase();
  if (!['href', 'src', 'xlink:href', 'action', 'formaction'].includes(cleanName)) return false;
  const cleanValue = String(value || '').trim();
  if (!cleanValue) return false;
  if (/\/share\/[A-Za-z0-9_-]+/i.test(cleanValue) || /#t=/i.test(cleanValue)) return true;
  return /^(?:javascript|vbscript|data):/i.test(cleanValue);
}

function shareReplayElementRedactionAction(element) {
  const tag = String(element?.localName || element?.tagName || '').toLowerCase();
  if (shareReplayBlockedTags.has(tag)) return 'drop';
  if (shareReplayElementHasFlag(element, 'data-share-private') || shareReplayElementHasFlag(element, 'data-share-redact')) return 'drop';
  const type = String(element?.getAttribute?.('type') || element?.attributes?.type || '').toLowerCase();
  if ((tag === 'input' && type === 'password') || shareReplayElementHasFlag(element, 'data-share-secret')) return 'placeholder';
  return 'keep';
}

function shareReplayElementInlineStyleValue(element, property = '') {
  const style = element?.style;
  if (!style || !property) return '';
  if (typeof style.getPropertyValue === 'function') return String(style.getPropertyValue(property) || '');
  const camel = String(property).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  return String(style[property] || style[camel] || '');
}

function shareReplayElementOrAncestorHidden(element) {
  let node = element;
  while (node) {
    if (node.hidden === true || node.hasAttribute?.('hidden')) return true;
    if (String(node.getAttribute?.('aria-hidden') || '').toLowerCase() === 'true') return true;
    if (String(shareReplayElementInlineStyleValue(node, 'display')).toLowerCase() === 'none') return true;
    if (String(shareReplayElementInlineStyleValue(node, 'visibility')).toLowerCase() === 'hidden') return true;
    node = node.parentElement;
  }
  return false;
}

function shareReplayElementHasVisibleBox(element) {
  if (!element) return false;
  const rect = typeof element.getBoundingClientRect === 'function' ? element.getBoundingClientRect() : null;
  const rectWidth = Number(rect?.width || 0);
  const rectHeight = Number(rect?.height || 0);
  const clientWidth = Number(element.clientWidth || 0);
  const clientHeight = Number(element.clientHeight || 0);
  const offsetWidth = Number(element.offsetWidth || 0);
  const offsetHeight = Number(element.offsetHeight || 0);
  return (rectWidth > 0 && rectHeight > 0) || (clientWidth > 0 && clientHeight > 0) || (offsetWidth > 0 && offsetHeight > 0);
}

function shareReplayElementIsTerminalContainer(element) {
  return Boolean(
    element?.classList?.contains?.('terminal')
    || element?.dataset?.shareTerminalSession
  );
}

function shareReplayTerminalSessionForElement(element) {
  const id = String(element?.id || '');
  if (element?.classList?.contains?.('terminal') && id.startsWith('term-')) return id.slice('term-'.length);
  if (element?.dataset?.shareTerminalSession) return String(element.dataset.shareTerminalSession || '');
  if (element?.dataset?.session && element?.classList?.contains?.('terminal')) return String(element.dataset.session || '');
  return '';
}

function shareReplayTerminalElementIsVisible(element) {
  if (!element || element.isConnected === false) return false;
  if (element.closest?.('#panelPool')) return false;
  if (shareReplayElementOrAncestorHidden(element)) return false;
  return shareReplayElementHasVisibleBox(element);
}

function shareReplayTerminalPlaceholderForElement(element, nodeId) {
  const session = shareReplayTerminalSessionForElement(element);
  if (!session) return null;
  if (!shareReplayTerminalElementIsVisible(element)) return null;
  const item = terminals.get(session);
  const hostSize = shareHostTerminalSize(session);
  const rows = Math.max(0, Math.round(Number(hostSize?.rows || item?.term?.rows || element?.dataset?.rows || 0)));
  const cols = Math.max(0, Math.round(Number(hostSize?.cols || item?.term?.cols || element?.dataset?.cols || 0)));
  const terminalEpoch = Math.max(0, Math.round(Number(element?.dataset?.terminalEpoch || shareReplayMirrorEpoch || 0)));
  const placeholderId = `term-ph-${session}`;
  return {
    node: {
      nodeId,
      tag: 'div',
      attrs: {
        class: 'share-terminal-placeholder',
        [shareReplayDatasetAttributeName(shareMirrorProtocol.terminalPlaceholder.dataset)]: session,
        'data-session': session,
        'data-rows': String(rows),
        'data-cols': String(cols),
      },
      children: [],
    },
    terminal: {placeholderId, session, rows, cols, terminalEpoch},
  };
}

function shareReplaySanitizeAttribute(name = '', value = '') {
  const cleanName = String(name || '').toLowerCase();
  if (!cleanName || cleanName.startsWith('on')) return null;
  if (cleanName === 'srcdoc') return null;
  if (shareReplayAttributeIsTokenBearing(cleanName)) return null;
  if (shareReplayUrlAttributeIsUnsafe(cleanName, value)) return null;
  return [String(name || '').trim(), shareReplayRedactText(value)];
}

function shareReplayAttributeNameIsSafe(name = '') {
  return /^[A-Za-z_:-][A-Za-z0-9_:\\.-]*$/.test(String(name || ''));
}

function shareReplayTagIsAllowed(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  return shareReplayAllowedHtmlTags.has(cleanTag) || shareReplayAllowedSvgTags.has(cleanTag);
}

function shareReplaySafeSerializedTag(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  if (shareReplayTagIsAllowed(cleanTag)) return cleanTag;
  return 'span';
}

function shareReplayCreateElement(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  if (shareReplayBlockedTags.has(cleanTag)) {
    throw new Error(`unsupported replay tag: ${cleanTag || 'missing'}`);
  }
  const replayTag = shareReplaySafeSerializedTag(cleanTag);
  if (shareReplayAllowedSvgTags.has(cleanTag)) {
    return document.createElementNS(shareReplaySvgNamespace, replayTag);
  }
  return document.createElement(replayTag);
}

function shareReplayNormalizeNodeId(value) {
  const nodeId = Number(value);
  if (!Number.isSafeInteger(nodeId) || nodeId <= 0) throw new Error('invalid replay node id');
  return nodeId;
}

function shareReplayApplyAttributes(element, attrs = {}, options = {}) {
  if (!element || !attrs || typeof attrs !== 'object' || Array.isArray(attrs)) return;
  for (const [rawName, rawValue] of Object.entries(attrs)) {
    const sanitized = shareReplaySanitizeAttribute(rawName, rawValue);
    if (!sanitized) continue;
    const [name, value] = sanitized;
    const cleanName = String(name || '');
    if (!shareReplayAttributeNameIsSafe(cleanName)) continue;
    if (options.preserveRoot === true && cleanName === 'id') continue;
    if (options.preserveRoot === true && cleanName === 'class') continue;
    element.setAttribute(cleanName, value);
  }
}

function shareReplayResetMirrorRootAttributes(root, attrs = {}) {
  if (!root) return;
  const hostClass = String(attrs?.class || 'app-root').trim() || 'app-root';
  root.className = `${hostClass} share-replay-root`;
  root.id = 'appRoot';
  root.dataset.shareReplayRoot = 'true';
  root.dataset.shareReplayInert = 'true';
  root.setAttribute('role', 'presentation');
  root.setAttribute('aria-label', 'YO!share replay mirror');
  root.setAttribute('tabindex', '-1');
  shareReplayApplyAttributes(root, attrs, {preserveRoot: true});
}

function shareReplayBuildNode(entry = {}, context) {
  if (!entry || typeof entry !== 'object') throw new Error('invalid replay node');
  const nodeId = shareReplayNormalizeNodeId(entry.nodeId);
  if (context.nodeMap.has(nodeId)) throw new Error(`duplicate replay node id: ${nodeId}`);
  if ('text' in entry && !entry.tag) {
    const node = document.createTextNode(shareReplayRedactText(entry.text));
    context.nodeMap.set(nodeId, node);
    return node;
  }
  const tag = String(entry.tag || '').toLowerCase();
  const element = shareReplayCreateElement(tag);
  if (element.dataset) element.dataset.shareReplayNodeId = String(nodeId);
  else element.setAttribute('data-share-replay-node-id', String(nodeId));
  shareReplayApplyAttributes(element, entry.attrs || {});
  context.nodeMap.set(nodeId, element);
  const children = Array.isArray(entry.children) ? entry.children : [];
  for (const child of children) {
    element.appendChild(shareReplayBuildNode(child, context));
  }
  if (!children.length && 'text' in entry) {
    element.appendChild(document.createTextNode(shareReplayRedactText(entry.text)));
  }
  return element;
}

function shareReplayNormalizeTerminalPlaceholder(entry = {}) {
  const placeholderId = String(entry?.placeholderId || '').slice(0, 200);
  const session = String(entry?.session || '').slice(0, 200);
  if (!placeholderId || !session) return null;
  return {
    placeholderId,
    session,
    rows: Math.max(0, Math.round(Number(entry.rows) || 0)),
    cols: Math.max(0, Math.round(Number(entry.cols) || 0)),
    terminalEpoch: Math.max(0, Math.round(Number(entry.terminalEpoch) || 0)),
  };
}

function shareReplayTerminalPlaceholderElement(entry = {}) {
  const session = String(entry?.session || '').trim();
  if (!session) return null;
  return document.querySelector?.(`[data-share-terminal-placeholder="${cssEscape(session)}"]`) || null;
}

function shareReplayPrepareTerminalPlaceholderElement(element, entry = {}) {
  if (!element || !entry?.session) return null;
  element.id = terminalDomId(entry.session);
  element.classList?.add('terminal', 'share-terminal-placeholder-bound');
  if (element.dataset) {
    element.dataset.session = entry.session;
    element.dataset.rows = String(entry.rows);
    element.dataset.cols = String(entry.cols);
    element.dataset.terminalEpoch = String(entry.terminalEpoch);
    element.dataset.shareTerminalPlaceholderId = entry.placeholderId;
  }
  element.setAttribute?.('role', 'presentation');
  element.setAttribute?.('aria-label', `Shared terminal ${entry.session}`);
  return element;
}

function shareReplayDetachTerminalPlaceholder(entry = {}) {
  const session = String(entry?.session || '');
  if (!session) return;
  const item = terminals.get(session);
  const termElement = item?.term?.element || item?.container?.querySelector?.('.xterm') || null;
  if (termElement?.parentElement) termElement.remove();
}

function shareReplayBindTerminalPlaceholder(entry = {}) {
  const element = shareReplayPrepareTerminalPlaceholderElement(shareReplayTerminalPlaceholderElement(entry), entry);
  if (!element) return false;
  updateShareHostTerminalSize(entry.session, entry.rows, entry.cols);
  const item = terminals.get(entry.session);
  if (item?.term) {
    const termElement = item.term.element || item.container?.querySelector?.('.xterm') || null;
    if (item.container !== element) {
      element.replaceChildren();
      if (termElement) element.appendChild(termElement);
      item.container = element;
    } else if (termElement && termElement.parentElement !== element) {
      element.appendChild(termElement);
    }
    item.placeholderId = entry.placeholderId;
    bindTerminalContainerForSession(entry.session, item.term, element);
    connectTerminalSocket(entry.session, item);
    scheduleFit(entry.session);
    return true;
  }
  startTerminal(entry.session);
  const created = terminals.get(entry.session);
  if (created) created.placeholderId = entry.placeholderId;
  return Boolean(created?.term && created.container === element);
}

function shareReplayApplyTerminalPlaceholders(terminals = []) {
  const next = new Map();
  for (const raw of Array.isArray(terminals) ? terminals : []) {
    const entry = shareReplayNormalizeTerminalPlaceholder(raw);
    if (!entry) continue;
    next.set(entry.placeholderId, entry);
  }
  for (const [placeholderId, entry] of shareReplayTerminalPlaceholders.entries()) {
    if (!next.has(placeholderId)) shareReplayDetachTerminalPlaceholder(entry);
  }
  shareReplayTerminalPlaceholders = next;
  for (const entry of shareReplayTerminalPlaceholders.values()) {
    shareReplayBindTerminalPlaceholder(entry);
  }
}

function bindShareReplayPaneTabPopovers(root = appRootElement()) {
  if (!shareReplayShellActive || shareReadOnlyReplayModeEnabled() || !root?.querySelectorAll || typeof createHoverPopover !== 'function') return 0;
  let bound = 0;
  const tabs = root.querySelectorAll('.pane-tab, .dockview-pane-tab, .panel-popover-zone');
  for (const tab of tabs) {
    if (!tab || tab.dataset?.shareReplayPopoverBound === 'true') continue;
    const popover = tab.querySelector?.(':scope > .session-popover');
    if (!popover) continue;
    if (tab.dataset) tab.dataset.shareReplayPopoverBound = 'true';
    const position = event => {
      if (typeof positionPaneTabPopover === 'function') positionPaneTabPopover(tab, popover, event);
    };
    createHoverPopover({
      anchor: tab,
      popover,
      showDelay: () => (document.querySelector('.pane-tab.popover-open, .dockview-pane-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
      hideDelay: () => popoverHideDelayMs,
      canOpen: () => true,
      onQueue: position,
      onOpen: event => {
        popover.classList?.add('popover-open');
        position(event);
      },
      onClose: () => popover.classList?.remove('popover-open'),
      position,
      closeOthers: () => {
        if (typeof closeOtherSessionPopovers === 'function') closeOtherSessionPopovers(tab);
      },
    });
    bound += 1;
  }
  return bound;
}

function shareReplayApplyStaticKeyframe(payload = {}, message = {}) {
  if (!shareReplayShellActive || !payload || typeof payload !== 'object') return false;
  const rootEntry = payload.root && typeof payload.root === 'object' ? payload.root : null;
  if (!rootEntry) throw new Error('replay keyframe missing root');
  const context = {nodeMap: new Map()};
  const rootNodeId = shareReplayNormalizeNodeId(rootEntry.nodeId);
  context.nodeMap.set(rootNodeId, null);
  const children = Array.isArray(rootEntry.children) ? rootEntry.children : [];
  const fragment = document.createDocumentFragment();
  for (const child of children) fragment.appendChild(shareReplayBuildNode(child, context));
  const root = appRootElement();
  if (!root || root === document.body) throw new Error('replay mirror root missing');
  shareReplayResetMirrorRootAttributes(root, rootEntry.attrs || {});
  root.replaceChildren(fragment);
  root.dataset.shareReplayNodeId = String(rootNodeId);
  context.nodeMap.set(rootNodeId, root);
  shareReplayNodeMap = context.nodeMap;
  shareReplayApplyTerminalPlaceholders(payload.terminals || []);
  shareReplayApplyScrollEntries(payload.scroll || []);
  if (payload.viewport && typeof payload.viewport === 'object') applyShareViewportState(payload.viewport);
  bindShareReplayPaneTabPopovers(root);
  shareReplayCurrentEpoch = Math.max(0, Math.round(Number(message.epoch) || Number(payload.epoch) || shareReplayCurrentEpoch || 1));
  shareReplayLastSequence = Math.max(0, Math.round(Number(message.sequence) || Number(payload.sequence) || 0));
  shareReplayLastKeyframe = {payload, message};
  setShareReplayShellStatus('mirrored', {
    digest: payload.digest,
    sequence: message.sequence ?? payload.sequence,
  });
  return true;
}

function shareReplayCurrentDomDigest() {
  const root = appRootElement();
  if (!root) return '';
  return shareHashText(String(root.textContent || ''));
}

function shareReplayFrameNumberDetail(message = {}) {
  const payload = message?.payload && typeof message.payload === 'object' ? message.payload : {};
  const frameNumber = value => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number) : null;
  };
  return {
    epoch: frameNumber(message.epoch),
    sequence: frameNumber(message.sequence),
    baseSequence: frameNumber(message.baseSequence ?? payload.baseSequence),
    currentEpoch: Math.max(0, Math.round(Number(shareReplayCurrentEpoch) || 0)),
    lastSequence: Math.max(0, Math.round(Number(shareReplayLastSequence) || 0)),
  };
}

function shareReplayErrorDetail(reason = 'replay-error', detail = {}) {
  const cleanDetail = detail && typeof detail === 'object' ? detail : {};
  const currentEpoch = Math.max(0, Math.round(Number(cleanDetail.currentEpoch ?? shareReplayCurrentEpoch) || 0));
  const lastSequence = Math.max(0, Math.round(Number(cleanDetail.lastSequence ?? shareReplayLastSequence) || 0));
  const epoch = Number(cleanDetail.epoch);
  const sequence = Number(cleanDetail.sequence);
  const baseSequence = Number(cleanDetail.baseSequence);
  const expectedBaseSequence = Number(cleanDetail.expectedBaseSequence ?? lastSequence);
  const expectedSequence = Number(cleanDetail.expectedSequence ?? (lastSequence + 1));
  const error = cleanDetail.error instanceof Error ? cleanDetail.error.message : cleanDetail.error;
  const entry = {
    frameType: String(cleanDetail.frameType || cleanDetail.type || shareMirrorProtocol.frames.domDelta),
    reason: String(reason || cleanDetail.reason || 'replay-error'),
    error: String(error || cleanDetail.message || '').slice(0, 500),
    currentEpoch,
    lastSequence,
  };
  for (const [key, value] of Object.entries({
    epoch,
    sequence,
    baseSequence,
    expectedSequence,
    expectedBaseSequence,
    frameBytes: Number(cleanDetail.frameBytes),
  })) {
    if (Number.isFinite(value)) entry[key] = Math.round(value);
  }
  if (cleanDetail.digest) entry.digest = String(cleanDetail.digest || '');
  return shareRedactDiagnosticValue(entry);
}

function shareReplayRecordLastReplayError(reason = 'replay-error', detail = {}) {
  const cleanDetail = detail && typeof detail === 'object' ? detail : {};
  const shouldRecord = reason !== 'join' && reason !== 'manual-debug'
    && (cleanDetail.error || cleanDetail.reason || cleanDetail.frameType || cleanDetail.sequence || cleanDetail.baseSequence || cleanDetail.digest);
  if (!shouldRecord) return null;
  shareReplayLastReplayError = shareReplayErrorDetail(reason, cleanDetail);
  return shareReplayLastReplayError;
}

function shareReplayDeltaSequenceStatus(message = {}) {
  const detail = shareReplayFrameNumberDetail(message);
  const epoch = detail.epoch;
  const sequence = detail.sequence;
  const baseSequence = detail.baseSequence;
  const currentEpoch = detail.currentEpoch;
  const lastSequence = detail.lastSequence;
  if (!Number.isFinite(epoch) || !Number.isFinite(sequence) || !Number.isFinite(baseSequence)) {
    return {ok: false, reason: 'gap', error: 'missing replay sequence', ...detail};
  }
  if (!currentEpoch || epoch > currentEpoch) {
    return {ok: false, reason: 'gap', error: 'delta epoch has no keyframe base', ...detail};
  }
  if (epoch < currentEpoch || sequence <= lastSequence) {
    return {ok: false, reason: 'stale', stale: true, error: 'stale replay delta', ...detail};
  }
  if (baseSequence !== lastSequence || sequence !== lastSequence + 1) {
    return {ok: false, reason: 'gap', error: 'non-contiguous replay delta', ...detail};
  }
  return {ok: true, epoch, sequence, baseSequence};
}

function shareReplayDeltaCanApplyBestEffort(sequenceStatus = {}) {
  if (!sequenceStatus || sequenceStatus.ok || sequenceStatus.stale === true) return false;
  if (sequenceStatus.reason !== 'gap') return false;
  const epoch = Number(sequenceStatus.epoch);
  const sequence = Number(sequenceStatus.sequence);
  const currentEpoch = Number(sequenceStatus.currentEpoch);
  const lastSequence = Number(sequenceStatus.lastSequence);
  return Number.isFinite(epoch)
    && Number.isFinite(sequence)
    && Number.isFinite(currentEpoch)
    && Number.isFinite(lastSequence)
    && currentEpoch > 0
    && epoch === currentEpoch
    && sequence > lastSequence;
}

function shareReplayNodeForDelta(nodeId) {
  const id = Number(nodeId);
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('invalid replay delta node id');
  const node = shareReplayNodeMap.get(id);
  if (!node) throw new Error(`unknown replay node id: ${id}`);
  return node;
}

function shareReplayApplyDeltaMutation(entry = {}) {
  if (!entry || typeof entry !== 'object') return;
  const kind = String(entry.kind || '');
  const target = shareReplayNodeForDelta(entry.target);
  if (kind === 'characterData') {
    target.textContent = shareReplayRedactText(entry.text || '');
    return;
  }
  if (kind === 'attributes') {
    const name = String(entry.name || '');
    if (!shareReplayAttributeNameIsSafe(name)) return;
    if (entry.removed === true || entry.value === null || entry.value === undefined) {
      target.removeAttribute?.(name);
      return;
    }
    const sanitized = shareReplaySanitizeAttribute(name, entry.value);
    if (!sanitized || !shareReplayAttributeNameIsSafe(sanitized[0])) return;
    target.setAttribute?.(sanitized[0], sanitized[1]);
    return;
  }
  if (kind === 'childList') {
    for (const rawId of Array.isArray(entry.removed) ? entry.removed : []) {
      const removed = shareReplayNodeForDelta(rawId);
      removed.remove?.();
      shareReplayNodeMap.delete(Number(rawId));
    }
    for (const child of Array.isArray(entry.added) ? entry.added : []) {
      const node = shareReplayBuildNode(child, {nodeMap: shareReplayNodeMap});
      target.appendChild?.(node);
    }
  }
}

function shareReplayApplyDeltaTerminalPlaceholders(payload = {}) {
  const entries = Array.isArray(payload?.terminals)
    ? payload.terminals
    : Array.isArray(payload?.terminalPlaceholders)
      ? payload.terminalPlaceholders
      : [];
  if (!entries.length) return 0;
  shareReplayApplyTerminalPlaceholders(entries);
  return entries.length;
}

function shareReplayApplyScrollEntry(entry = {}) {
  if (!entry || typeof entry !== 'object') return;
  const element = shareReplayNodeForDelta(entry.nodeId ?? entry.target);
  if (!element || !('scrollTop' in element || 'scrollLeft' in element)) return;
  const top = Math.max(0, Math.round(Number(entry.top || 0)));
  const left = Math.max(0, Math.round(Number(entry.left || 0)));
  const previous = applyingShareRemoteScroll;
  applyingShareRemoteScroll = true;
  try {
    element.scrollTop = top;
    element.scrollLeft = left;
  } finally {
    requestAnimationFrame(() => { applyingShareRemoteScroll = previous; });
  }
}

function shareReplayApplyScrollEntries(scroll = []) {
  for (const entry of Array.isArray(scroll) ? scroll : []) shareReplayApplyScrollEntry(entry);
}

function shareReplayPointerPayload(payload = {}, sender = '') {
  if (!payload || typeof payload !== 'object') return null;
  const x = Number(payload.x);
  const y = Number(payload.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    scope: payload.scope || 'viewport',
    x,
    y,
    visible: payload.visible !== false,
    click: payload.click === true,
    sender: String(payload.sender || sender || 'host'),
  };
}

function shareReplayApplyPointer(payload = {}, message = {}) {
  const pointer = shareReplayPointerPayload(payload, message.sender || 'host');
  if (!pointer || pointer.visible === false) return;
  renderSharePointerGhost(pointer);
}

function applyShareReplayDelta(payload = {}, message = {}) {
  if (!shareReplayShellActive || !payload || typeof payload !== 'object') return false;
  shareReplayRecordFrameMetrics(shareMirrorProtocol.frames.domDelta, payload, message);
  const sequenceStatus = shareReplayDeltaSequenceStatus(message);
  if (!sequenceStatus.ok) {
    if (sequenceStatus.stale === true) {
      shareReplayStaleFrames += 1;
      return true;
    }
    shareReplayDroppedFrames += 1;
    shareReplayRequestKeyframe(sequenceStatus.reason, {
      ...sequenceStatus,
      frameType: message.type || shareMirrorProtocol.frames.domDelta,
      digest: payload.digest,
      frameBytes: shareReplayFrameByteLength({type: message.type || shareMirrorProtocol.frames.domDelta, payload, epoch: message.epoch, sequence: message.sequence}),
    });
    if (!shareReplayDeltaCanApplyBestEffort(sequenceStatus)) {
      setShareReplayShellStatus('viewer-behind', {sequence: message.sequence, digest: payload.digest});
      return true;
    }
  }
  try {
    for (const mutation of Array.isArray(payload.mutations) ? payload.mutations : []) {
      shareReplayApplyDeltaMutation(mutation);
    }
    shareReplayApplyDeltaTerminalPlaceholders(payload);
    shareReplayApplyScrollEntries(payload.scroll || []);
    if (payload.pointer && typeof payload.pointer === 'object') shareReplayApplyPointer(payload.pointer, message);
    const expectedDigest = String(payload.digest || '');
    const actualDigest = shareReplayCurrentDomDigest();
    if (expectedDigest && expectedDigest !== actualDigest) {
      throw new Error('replay DOM digest mismatch');
    }
    shareReplayCurrentEpoch = sequenceStatus.epoch;
    shareReplayLastSequence = sequenceStatus.sequence;
    setShareReplayShellStatus('mirrored', {
      sequence: sequenceStatus.sequence,
      digest: expectedDigest || actualDigest,
    });
    bindShareReplayPaneTabPopovers();
  } catch (error) {
    shareReplayDroppedFrames += 1;
    setShareReplayShellStatus('error', {sequence: message.sequence, digest: payload.digest});
    shareReplayRequestKeyframe('replay-error', {
      ...shareReplayFrameNumberDetail(message),
      frameType: message.type || shareMirrorProtocol.frames.domDelta,
      error,
      digest: payload.digest,
      frameBytes: shareReplayFrameByteLength({type: message.type || shareMirrorProtocol.frames.domDelta, payload, epoch: message.epoch, sequence: message.sequence}),
    });
  }
  return true;
}

function shareReplayMutationElement(node) {
  const value = Number(node?.nodeType);
  if (value === 1) return node;
  if (value === 3) return node?.parentElement || node?.parentNode || null;
  return node?.localName || node?.tagName ? node : null;
}

function shareReplayMutationNodeIsIgnored(node) {
  const element = shareReplayMutationElement(node);
  if (!element) return true;
  const selectors = [
    '.terminal',
    '.xterm',
    '.xterm-screen',
    '.xterm-viewport',
    '.xterm-cursor',
    '.xterm-helper-textarea',
    '[data-share-private]',
    '[data-share-redact]',
    '[data-share-volatile]',
    '[data-volatile]',
    '.file-explorer-panel',
    '.share-replay-volatile',
    '#latencyMeter',
    '#latencyNumber',
    '#latencyLine',
    '.latency-meter',
    '.share-pointer-ghost',
    '.share-viewer-banner',
  ];
  return selectors.some(selector => element.closest?.(selector));
}

function shareReplayHostMutationTargetIsMirrored(node) {
  const element = shareReplayMutationElement(node);
  if (!element) return false;
  const root = appRootElement();
  const disconnected = element.isConnected === false;
  const outsideRoot = typeof root?.contains === 'function' && !root.contains(element);
  if (!root || (element !== root && (disconnected || outsideRoot))) return false;
  if (Number(node?.nodeType) === 3) return shareReplayHostMirroredNodes.has(node);
  return shareReplayHostMirroredNodes.has(node) || shareReplayHostMirroredNodes.has(element);
}

function shareReplaySanitizeMutationAttribute(name = '', value = '') {
  const cleanName = String(name || '').trim();
  const lowerName = cleanName.toLowerCase();
  if (!cleanName || lowerName.startsWith('on') || lowerName === 'srcdoc' || shareReplayAttributeIsTokenBearing(lowerName)) return null;
  if (shareReplayUrlAttributeIsUnsafe(lowerName, value)) return [cleanName, null, true];
  const sanitized = shareReplaySanitizeAttribute(cleanName, value);
  if (!sanitized || !shareReplayAttributeNameIsSafe(sanitized[0])) return null;
  return [sanitized[0], sanitized[1], false];
}

function shareReplayMutationEntries(records = []) {
  const entries = [];
  const terminals = [];
  const mirroredAdditions = [];
  const mirroredRemovals = [];
  let batchNeedsKeyframe = false;
  for (const record of Array.from(records || [])) {
    if (batchNeedsKeyframe) continue;
    const type = String(record?.type || '');
    const target = record?.target || null;
    if (!target || shareReplayMutationNodeIsIgnored(target)) continue;
    if (!shareReplayHostMutationTargetIsMirrored(target)) continue;
    const targetId = shareReplayHostNodeId(target);
    if (!targetId) continue;
    if (type === 'characterData') {
      entries.push({
        kind: 'characterData',
        target: targetId,
        text: shareReplayRedactText(target.textContent || ''),
      });
      continue;
    }
    if (type === 'attributes') {
      const name = String(record.attributeName || '');
      const rawValue = target.getAttribute?.(name);
      const sanitized = shareReplaySanitizeMutationAttribute(name, rawValue ?? '');
      if (!sanitized) continue;
      entries.push({
        kind: 'attributes',
        target: targetId,
        name: sanitized[0],
        value: sanitized[2] ? null : sanitized[1],
        removed: sanitized[2] || rawValue === null || rawValue === undefined,
      });
      continue;
    }
    if (type === 'childList') {
      const added = [];
      let needsKeyframe = false;
      for (const node of Array.from(record.addedNodes || [])) {
        if (shareReplayMutationNodeIsIgnored(node)) {
          needsKeyframe = true;
          continue;
        }
        const context = {nextNodeId: 1, terminals: [], removedCount: 0, mirroredNodes: [], useStableNodeIds: true};
        const serialized = shareReplaySerializeNode(node, context);
        if (serialized) added.push(serialized);
        if (context.mirroredNodes.length) mirroredAdditions.push(...context.mirroredNodes);
        if (context.terminals.length) terminals.push(...context.terminals);
      }
      const removed = [];
      for (const node of Array.from(record.removedNodes || [])) {
        if (shareReplayMutationNodeIsIgnored(node)) {
          needsKeyframe = true;
          continue;
        }
        if (!shareReplayHostMirroredNodes.has(node)) continue;
        const nodeId = shareReplayHostNodeId(node);
        if (nodeId) {
          removed.push(nodeId);
          mirroredRemovals.push(node);
        }
      }
      if (needsKeyframe) {
        batchNeedsKeyframe = true;
        entries.splice(0, entries.length);
        mirroredAdditions.splice(0, mirroredAdditions.length);
        mirroredRemovals.splice(0, mirroredRemovals.length);
        scheduleShareTopologyDomKeyframe();
        continue;
      }
      if (added.length || removed.length) {
        entries.push({kind: 'childList', target: targetId, added, removed});
      }
    }
  }
  if (entries.length || terminals.length) {
    shareReplayHostMergeMirroredNodes(mirroredAdditions);
    for (const node of mirroredRemovals) shareReplayHostForgetMirroredSubtree(node);
  }
  Object.defineProperty(entries, 'terminals', {
    value: terminals.sort((a, b) => String(a.session || '').localeCompare(String(b.session || ''))),
    configurable: true,
  });
  return entries;
}

function shareReplayEnqueueMutationRecords(records = []) {
  if (shareViewMode || !shareReplayFeatureEnabled() || shareReplayMutationPublisherPaused) return [];
  const entries = shareReplayMutationEntries(records);
  const terminals = Array.isArray(entries.terminals) ? entries.terminals : [];
  if (!entries.length && !terminals.length) return [];
  if (entries.length) shareReplayPendingMutations.push(...entries);
  if (terminals.length) shareReplayPendingTerminalPlaceholders.push(...terminals);
  if (!shareReplayDeltaFramePending) {
    shareReplayDeltaFramePending = true;
    requestAnimationFrame(shareReplayFlushMutationDeltas);
  }
  return entries;
}

function shareReplayDrainMutationPublisher() {
  if (shareReplayMutationObserver) {
    shareReplayMutationObserver.takeRecords?.();
    shareReplayMutationObserver.disconnect?.();
    shareReplayMutationObserver = null;
  }
  shareReplayPendingMutations.splice(0, shareReplayPendingMutations.length);
  shareReplayPendingTerminalPlaceholders.splice(0, shareReplayPendingTerminalPlaceholders.length);
  shareReplayDeltaFramePending = false;
}

function shareReplayResumeMutationPublisher() {
  shareReplayMutationPublisherPaused = false;
  installShareReplayMutationPublisher();
}

function shareReplayResumeMutationPublisherAfterFrames(delayMs = 0) {
  if (shareReplayTopologyMutationPauseTimer) {
    clearTimeout(shareReplayTopologyMutationPauseTimer);
    shareReplayTopologyMutationPauseTimer = null;
  }
  const resume = () => shareReplayResumeMutationPublisher();
  const delay = Math.max(0, Math.round(Number(delayMs) || 0));
  const resumeAfterDelay = () => {
    if (delay > 0) setTimeout(resume, delay);
    else resume();
  };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => requestAnimationFrame(resumeAfterDelay));
  } else {
    setTimeout(resumeAfterDelay, 32);
  }
}

function shareReplayPauseMutationPublisherForTopology() {
  shareReplayMutationPublisherPaused = true;
  shareReplayDrainMutationPublisher();
  if (shareReplayTopologyMutationPauseTimer) clearTimeout(shareReplayTopologyMutationPauseTimer);
  shareReplayTopologyMutationPauseTimer = setTimeout(() => {
    shareReplayTopologyMutationPauseTimer = null;
    if (!shareReplayHostKeyframeTimer && !shareReplayTopologyKeyframeTimer) shareReplayResumeMutationPublisher();
  }, shareReplayHostKeyframeMinIntervalMs + 1000);
}

function shareReplayPublishDeltaPayload(payload = {}, reason = 'mutation') {
  if (shareViewMode || !shareReplayFeatureEnabled() || !payload || typeof payload !== 'object') return null;
  const cleanPayload = {
    ...payload,
    capturedAt: Date.now(),
  };
  shareReplayLastDeltaBatch = cleanPayload;
  sharePublish(shareMirrorProtocol.frames.domDelta, cleanPayload, {reason});
  return cleanPayload;
}

function shareReplayFlushMutationDeltas() {
  shareReplayDeltaFramePending = false;
  const mutations = shareReplayPendingMutations.splice(0, shareReplayPendingMutations.length);
  const terminals = shareReplayPendingTerminalPlaceholders.splice(0, shareReplayPendingTerminalPlaceholders.length);
  if (!mutations.length && !terminals.length) return null;
  const payload = {
    mutations,
    count: mutations.length,
  };
  if (terminals.length) payload.terminals = terminals;
  const bytes = shareReplayFrameByteLength({type: shareMirrorProtocol.frames.domDelta, payload});
  if (bytes > shareReplayHostDeltaMaxBytes) {
    shareReplayLastDeltaBatch = {...payload, skipped: true, bytes};
    sharePublishDomKeyframe('backpressure');
    return null;
  }
  return shareReplayPublishDeltaPayload(payload, 'mutation');
}

function installShareReplayMutationPublisher() {
  if (shareViewMode || !shareReplayFeatureEnabled() || shareReplayMutationPublisherPaused || shareReplayMutationObserver || typeof MutationObserver === 'undefined') return;
  const root = appRootElement();
  if (!root || root === document.body) return;
  shareReplayHostNodeId(root);
  shareReplayMutationObserver = new MutationObserver(records => shareReplayEnqueueMutationRecords(records));
  shareReplayMutationObserver.observe(root, {
    attributes: true,
    characterData: true,
    childList: true,
    subtree: true,
  });
}

function shareReplayResetMutationPublisherForKeyframe(reason = 'manual-debug') {
  shareReplayMutationPublisherPaused = true;
  shareReplayDrainMutationPublisher();
  const cleanReason = shareReplayKeyframeReason(reason);
  const quietMs = cleanReason === 'join' || cleanReason === 'topology' ? shareReplayPostTopologyKeyframeQuietMs : 0;
  shareReplayResumeMutationPublisherAfterFrames(quietMs);
}

function shareReplayElementAttributes(element) {
  const attrs = {};
  const add = (name, value) => {
    const attr = shareReplaySanitizeAttribute(name, value);
    if (!attr) return;
    attrs[attr[0]] = attr[1];
  };
  if (element?.id) add('id', element.id);
  if (element?.className) add('class', element.className);
  const rawAttrs = element?.attributes;
  if (rawAttrs && typeof rawAttrs.length === 'number') {
    for (const attr of Array.from(rawAttrs)) add(attr?.name, attr?.value);
  } else if (rawAttrs && typeof rawAttrs === 'object') {
    for (const [name, value] of Object.entries(rawAttrs)) add(name, value);
  }
  for (const [key, value] of Object.entries(element?.dataset || {})) add(shareReplayDatasetAttributeName(key), value);
  if (element?.classList?.contains('pane-tab-detached-popover') && element?.classList?.contains('popover-open')) {
    const rect = appSpaceRect(element);
    if (rect && Number.isFinite(rect.left) && Number.isFinite(rect.top) && Number.isFinite(rect.width) && Number.isFinite(rect.height)) {
      const fixedGeometry = [
        `left:${rect.left.toFixed(3)}px`,
        `top:${rect.top.toFixed(3)}px`,
        'right:auto',
        'bottom:auto',
        `width:${rect.width.toFixed(3)}px`,
        `height:${rect.height.toFixed(3)}px`,
      ];
      const style = String(attrs.style || '')
        .split(';')
        .map(part => part.trim())
        .filter(part => part && !/^(?:inset|left|top|right|bottom|width|height)\s*:/i.test(part));
      attrs.style = [...style, ...fixedGeometry].join(';');
    }
  }
  return Object.fromEntries(Object.keys(attrs).sort().map(key => [key, attrs[key]]));
}

function shareReplaySerializeNode(node, context) {
  if (!node) return null;
  const nodeType = Number(node.nodeType || (node.localName || node.tagName ? 1 : 0));
  if (nodeType === 3) {
    const text = shareReplayRedactText(node.textContent || '');
    if (!text) return null;
    shareReplayRememberSerializedNode(context, node);
    return {nodeId: shareReplaySerializedNodeId(node, context), text};
  }
  if (nodeType !== 1) return null;
  const redactionAction = shareReplayElementRedactionAction(node);
  if (redactionAction === 'drop') {
    context.removedCount += 1;
    return null;
  }
  const terminalContainer = shareReplayElementIsTerminalContainer(node);
  if (terminalContainer && (!shareReplayTerminalSessionForElement(node) || !shareReplayTerminalElementIsVisible(node))) {
    context.removedCount += 1;
    return null;
  }
  const nodeId = shareReplaySerializedNodeId(node, context);
  if (redactionAction === 'placeholder') {
    context.removedCount += 1;
    shareReplayRememberSerializedNode(context, node);
    return {
      nodeId,
      tag: String(node.localName || node.tagName || 'input').toLowerCase(),
      attrs: {'data-share-redacted': 'secret'},
      children: [],
    };
  }
  const terminalPlaceholder = terminalContainer ? shareReplayTerminalPlaceholderForElement(node, nodeId) : null;
  if (terminalPlaceholder) {
    context.terminals.push(terminalPlaceholder.terminal);
    shareReplayRememberSerializedNode(context, node);
    return terminalPlaceholder.node;
  }
  if (terminalContainer) {
    context.removedCount += 1;
    return null;
  }
  const children = [];
  const childNodes = Array.from(node.childNodes || node.children || []);
  for (const child of childNodes) {
    const serialized = shareReplaySerializeNode(child, context);
    if (serialized) children.push(serialized);
  }
  const tag = shareReplaySafeSerializedTag(node.localName || node.tagName || 'div');
  const entry = {nodeId, tag, attrs: shareReplayElementAttributes(node), children};
  const text = shareReplayRedactText(node.textContent || '');
  if (!children.length && text) entry.text = text;
  shareReplayRememberSerializedNode(context, node);
  return entry;
}

function shareReplayScrollContainerSelector() {
  return typeof paneScrollContainerSelector === 'string' && paneScrollContainerSelector
    ? paneScrollContainerSelector
    : '.preferences-scroll, .file-explorer-tree-panel, .file-explorer-changes-panel, .file-editor-preview-pane-panel, .file-editor-codemirror .cm-scroller, .file-editor-codemirror-panel .cm-scroller, #info-content';
}

function shareReplayScrollableElementAllowed(element) {
  if (!element || shareReplayMutationNodeIsIgnored(element)) return false;
  if (element.closest?.('.terminal, .xterm, .xterm-viewport')) return false;
  const scrollTop = Math.max(0, Math.round(Number(element.scrollTop || 0)));
  const scrollLeft = Math.max(0, Math.round(Number(element.scrollLeft || 0)));
  const scrollHeight = Math.round(Number(element.scrollHeight || 0));
  const scrollWidth = Math.round(Number(element.scrollWidth || 0));
  const clientHeight = Math.round(Number(element.clientHeight || 0));
  const clientWidth = Math.round(Number(element.clientWidth || 0));
  return Boolean(
    scrollTop
    || scrollLeft
    || scrollHeight > clientHeight + 1
    || scrollWidth > clientWidth + 1
    || element.dataset?.shareReplayScroll === 'true'
  );
}

function shareReplayScrollableElements(root = appRootElement()) {
  const queryRoot = root?.querySelectorAll ? root : document;
  const selector = shareReplayScrollContainerSelector();
  const nodes = Array.from(queryRoot.querySelectorAll?.(selector) || []);
  if (root?.matches?.(selector)) nodes.unshift(root);
  const seen = new Set();
  return nodes.filter(node => {
    if (seen.has(node) || !shareReplayScrollableElementAllowed(node)) return false;
    seen.add(node);
    return true;
  });
}

function shareReplayScrollEntryForElement(element) {
  if (!shareReplayScrollableElementAllowed(element)) return null;
  const nodeId = shareReplayHostNodeId(element);
  if (!nodeId) return null;
  const semantic = typeof shareScrollPayloadForElement === 'function' ? shareScrollPayloadForElement(element) : null;
  return {
    nodeId,
    target: semantic?.target || '',
    kind: semantic?.kind || '',
    top: Math.max(0, Math.round(Number(element.scrollTop || 0))),
    left: Math.max(0, Math.round(Number(element.scrollLeft || 0))),
  };
}

function shareReplayScrollSnapshot(root = appRootElement()) {
  return shareReplayScrollableElements(root).map(shareReplayScrollEntryForElement).filter(Boolean);
}

function shareReplayAssetFingerprint() {
  return {
    js: String(bootstrap.versionCommit || bootstrap.version || ''),
    css: String(bootstrap.version || ''),
    fonts: shareFontFingerprint(),
  };
}

function shareCreateDomKeyframePayload(reason = 'manual-debug') {
  if (!shareReplayFeatureEnabled()) return null;
  const context = {nextNodeId: 1, terminals: [], removedCount: 0, mirroredNodes: [], useStableNodeIds: true};
  const root = shareReplaySerializeNode(appRootElement(), context);
  if (!root) return null;
  shareReplayHostMirroredNodes = shareReplayHostMirroredNodeSet(context.mirroredNodes);
  const payload = {
    shareId: String(shareBootstrap?.id || activeShares[0]?.id || activeShares[0]?.token || ''),
    reason: shareMirrorFrameReason(shareMirrorProtocol.frames.domKeyframe, {reason}),
    createdAt: Date.now() / 1000,
    viewport: shareViewportSnapshot(),
    assets: shareReplayAssetFingerprint(),
    root,
    terminals: context.terminals.sort((a, b) => a.session.localeCompare(b.session)),
    scroll: shareReplayScrollSnapshot(),
    redaction: {
      policyVersion: shareMirrorProtocol.redaction.policyVersion,
      removedCount: context.removedCount,
    },
  };
  payload.digest = shareHashText(stableDigestJson({
    root: payload.root,
    terminals: payload.terminals,
    redaction: payload.redaction,
  }));
  return payload;
}

function shareCreateDomKeyframeMessage(reason = 'manual-debug') {
  const payload = shareCreateDomKeyframePayload(reason);
  return payload ? shareBuildUiMessage(shareMirrorProtocol.frames.domKeyframe, payload, {reason}) : null;
}

function shareReplayKeyframeReason(reason = 'manual-debug', fallback = 'manual-debug') {
  const clean = String(reason || '').trim();
  if (shareMirrorProtocol.keyframeReasons.includes(clean)) return clean;
  return fallback;
}

function shareReplayCoalesceKeyframeReason(currentReason = '', nextReason = '') {
  const current = shareReplayKeyframeReason(currentReason, '');
  const next = shareReplayKeyframeReason(nextReason, '');
  if (!current) return next || 'manual-debug';
  if (!next) return current;
  const priority = ['manual-debug', 'join', 'topology', 'backpressure', 'replay-error', 'digest', 'gap'];
  return priority.indexOf(next) >= 0 && priority.indexOf(next) < priority.indexOf(current) ? next : current;
}

function sharePublishDomKeyframeNow(reason = 'manual-debug') {
  if (shareReplayHostKeyframeTimer) {
    clearTimeout(shareReplayHostKeyframeTimer);
    shareReplayHostKeyframeTimer = null;
  }
  shareReplayHostKeyframePendingReason = '';
  if (shareViewMode || !shareHasActiveShare()) return false;
  const cleanReason = shareReplayKeyframeReason(reason);
  shareReplayResetMutationPublisherForKeyframe(cleanReason);
  const payload = shareCreateDomKeyframePayload(cleanReason);
  if (!payload) return false;
  sharePublish(shareMirrorProtocol.frames.domKeyframe, payload, {reason: cleanReason});
  shareReplayHostLastKeyframeAt = Date.now();
  return true;
}

function sharePublishDomKeyframe(reason = 'manual-debug') {
  const cleanReason = shareReplayKeyframeReason(reason);
  const now = Date.now();
  const lastAt = Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0));
  if (cleanReason === 'manual-debug' || cleanReason === 'topology' || cleanReason === 'join' || lastAt <= 0 || now - lastAt >= shareReplayHostKeyframeMinIntervalMs) {
    return sharePublishDomKeyframeNow(cleanReason);
  }
  shareReplayHostKeyframeSuppressedCount = Math.max(0, Math.round(Number(shareReplayHostKeyframeSuppressedCount) || 0)) + 1;
  shareReplayHostKeyframePendingReason = shareReplayCoalesceKeyframeReason(shareReplayHostKeyframePendingReason, cleanReason);
  if (!shareReplayHostKeyframeTimer) {
    const delayMs = Math.max(0, shareReplayHostKeyframeMinIntervalMs - (now - lastAt));
    shareReplayHostKeyframeTimer = setTimeout(() => {
      const pendingReason = shareReplayHostKeyframePendingReason || cleanReason;
      shareReplayHostKeyframeTimer = null;
      shareReplayHostKeyframePendingReason = '';
      sharePublishDomKeyframeNow(pendingReason);
    }, delayMs);
  }
  return true;
}

function shareMirrorFrameSequenceFamily(message = {}) {
  return shareMirrorFrameTypeIsDomReplayContent(message?.type) ? 'dom-replay' : 'semantic';
}

function shareMirrorSenderKey(message = {}) {
  const sender = String(message.sender || message.owner || 'host').trim() || 'host';
  const family = shareMirrorFrameSequenceFamily(message);
  return family === 'semantic' ? sender : `${sender}:${family}`;
}

function shareMirrorFrameNumbers(message = {}) {
  const epoch = Math.floor(Number(message.epoch));
  const sequence = Math.floor(Number(message.sequence));
  if (!Number.isFinite(epoch) || !Number.isFinite(sequence)) return null;
  return {epoch, sequence};
}

function shareDropStaleMirrorFrame(message = {}) {
  if (!message || !shareMirrorFrameTypeIsSequenced(message.type)) return false;
  const current = shareMirrorFrameNumbers(message);
  if (!current) return false;
  const sender = shareMirrorSenderKey(message);
  const previous = shareMirrorLastFrameBySender.get(sender);
  if (previous) {
    if (current.epoch < previous.epoch) return true;
    if (current.epoch === previous.epoch && current.sequence <= previous.sequence) return true;
  }
  shareMirrorLastFrameBySender.set(sender, current);
  return false;
}

function sharePublish(type, payload = {}, options = {}) {
  if (type === 'scroll' && !shareCanPublishScroll()) return;
  if (!shareCanPublishUi() || !type) return;
  const message = shareBuildUiMessage(type, payload, options);
  const targets = shareViewMode ? [{token: shareToken}] : activeShares;
  for (const share of targets) {
    const token = share?.token || share;
    if (!token) continue;
    const socket = ensureShareHostSocket(token);
    if (!socket) continue;
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      continue;
    }
    const shareHostQueue = shareHostQueues.get(token) || [];
    if (shareHostQueue.length < 32) shareHostQueue.push(message);
    shareHostQueues.set(token, shareHostQueue);
  }
}

function shareRedactSecretNode(node) {
  if (!node) return;
  if ('value' in node) node.setAttribute('value', '...');
  node.textContent = '...';
}

function shareSanitizePopupHtml(html = '') {
  const text = String(html || '');
  if (!text) return '';
  const template = document.createElement('template');
  if (!('content' in template)) return '';
  template.innerHTML = text;
  template.content.querySelectorAll('script, iframe, object, embed').forEach(node => node.remove());
  template.content.querySelectorAll('*').forEach(node => {
    for (const attr of Array.from(node.attributes || [])) {
      const sanitized = shareReplaySanitizeAttribute(attr.name, attr.value);
      if (!sanitized || sanitized[0] === 'id') {
        node.removeAttribute(attr.name);
      } else if (sanitized[1] !== String(attr.value ?? '')) {
        node.setAttribute(sanitized[0], sanitized[1]);
      }
    }
  });
  template.content.querySelectorAll('[data-share-secret]').forEach(shareRedactSecretNode);
  template.content.querySelectorAll('input[value*="/share/"], input[value*="#t="]').forEach(shareRedactSecretNode);
  const result = template.innerHTML || '';
  return result.length > 8192 ? '' : result;
}

function sharePopupLayerElements() {
  const selectors = [
    '.app-menu.open .app-menu-popover',
    '.terminal-context-menu',
    '.session-rename-backdrop',
    '.file-image-preview-popover',
    '.pane-tab-popover',
    '.pane-tab.popover-open > .session-popover',
    '.dockview-pane-tab.popover-open > .session-popover',
    '.panel-popover-zone.popover-open > .session-popover',
    '.pane-tab-detached-popover.popover-open',
    '.diff-ref-popover',
    '.diff-ref-suggestion-popover:not([hidden])',
    '#modal.open',
  ];
  const nodes = [];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (node && !nodes.includes(node)) nodes.push(node);
    }
  }
  return nodes.filter(node => {
    const rect = node.getBoundingClientRect?.();
    return rect && rect.width > 0 && rect.height > 0;
  });
}

function sharePopupLayerPayload() {
  const items = [];
  for (const node of sharePopupLayerElements()) {
    const html = shareSanitizePopupHtml(node.outerHTML || '');
    if (!html) continue;
    const rect = appSpaceRect(node);
    items.push({
      kind: node.classList?.contains('modal') ? 'modal' : 'popup',
      rect: {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      html,
    });
  }
  sharePopupLayerSequence += 1;
  return {items, seq: sharePopupLayerSequence, owner: shareClientId};
}

function sharePublishPopupLayer() {
  if (!shareCanPublishUi()) return;
  sharePublish('popup-layer', sharePopupLayerPayload());
}

function scheduleSharePopupLayerPublish(options = {}) {
  if (!shareCanPublishUi()) return;
  if (sharePopupLayerPublishTimer) clearTimeout(sharePopupLayerPublishTimer);
  sharePopupLayerPublishTimer = null;
  if (options.immediate === true) {
    sharePublishPopupLayer();
    return;
  }
  sharePopupLayerPublishTimer = setTimeout(() => {
    sharePopupLayerPublishTimer = null;
    sharePublishPopupLayer();
  }, 60);
}

function ensureSharePopupLayer() {
  if (sharePopupLayerNode?.isConnected) return sharePopupLayerNode;
  const root = appRootElement();
  if (!root || root === document.body) return null;
  sharePopupLayerNode = document.createElement('div');
  sharePopupLayerNode.className = 'share-popup-mirror-layer';
  sharePopupLayerNode.setAttribute('aria-hidden', 'true');
  root.appendChild(sharePopupLayerNode);
  return sharePopupLayerNode;
}

function applySharePopupLayer(payload = {}, sender = '') {
  if (!shareViewMode || shareReadOnlyReplayModeEnabled()) return;
  const layer = ensureSharePopupLayer();
  if (!layer) return;
  const owner = String(payload.owner || sender || 'host');
  const seq = Number(payload.seq);
  if (Number.isFinite(seq)) {
    const previousSeq = Number(sharePopupLayerLastSeqBySender.get(owner) || 0);
    if (seq <= previousSeq) return;
    sharePopupLayerLastSeqBySender.set(owner, seq);
  }
  const items = Array.isArray(payload.items) ? payload.items : [];
  layer.replaceChildren();
  for (const item of items.slice(0, 8)) {
    const rect = item?.rect && typeof item.rect === 'object' ? item.rect : {};
    const html = shareSanitizePopupHtml(item?.html || '');
    if (!html) continue;
    const shell = document.createElement('div');
    shell.className = 'share-popup-mirror-item';
    shell.style.left = `${Math.round(Number(rect.left) || 0)}px`;
    shell.style.top = `${Math.round(Number(rect.top) || 0)}px`;
    shell.style.width = `${Math.max(0, Math.round(Number(rect.width) || 0))}px`;
    shell.style.height = `${Math.max(0, Math.round(Number(rect.height) || 0))}px`;
    shell.innerHTML = html;
    layer.appendChild(shell);
  }
}

function installSharePopupLayerPublisher() {
  if (shareViewMode || sharePopupLayerObserver || typeof MutationObserver === 'undefined' || !document.body) return;
  sharePopupLayerObserver = new MutationObserver(() => scheduleSharePopupLayerPublish());
  sharePopupLayerObserver.observe(document.body, {
    attributes: true,
    attributeFilter: ['aria-expanded', 'class', 'hidden', 'style'],
    childList: true,
    subtree: true,
  });
}

function sharePublishLayout() {
  sharePublish('layout', shareLayoutSeed());
}

function shareStringArray(value, limit = 1000) {
  if (!Array.isArray(value)) return [];
  const result = [];
  for (const item of value) {
    const text = String(item || '').trim();
    if (!text || result.includes(text)) continue;
    result.push(text);
    if (result.length >= limit) break;
  }
  return result;
}

function shareSetSnapshot(set, limit = 1000) {
  return Array.from(set || []).map(item => String(item || '')).filter(Boolean).slice(0, limit).sort();
}

function shareSetSignature(set, limit = 1000) {
  return shareSetSnapshot(set, limit).join('\n');
}

function shareAutoApproveStateSnapshot() {
  const states = {};
  for (const session of sessions.filter(isTmuxSession)) {
    const state = autoApproveStates.get(session);
    if (!state || typeof state !== 'object') continue;
    states[session] = {
      target: String(state.target || session),
      enabled: state.enabled === true,
      locked: state.locked === true,
      last_action: String(state.last_action || ''),
      last_screen_sig: String(state.last_screen_sig || ''),
      lock_owner: state.lock_owner && typeof state.lock_owner === 'object'
        ? {
          pid: state.lock_owner.pid || '',
          project_root: String(state.lock_owner.project_root || ''),
        }
        : {},
    };
  }
  return {sessions: states};
}

function shareScrollSnapshotElements() {
  const selectors = [
    '.preferences-scroll',
    '#info-content',
    '.file-explorer-tree-panel',
    '.file-explorer-changes-panel',
    '.file-editor-preview-pane-panel',
    '.file-editor-codemirror-panel .cm-scroller',
    '.terminal[id^="term-"] .xterm-viewport',
  ];
  const nodes = [];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!node || nodes.includes(node)) continue;
      const rect = node.getBoundingClientRect?.();
      if (rect && (rect.width > 0 || rect.height > 0)) nodes.push(node);
    }
  }
  return nodes;
}

function shareScrollStateSnapshot() {
  const byTarget = new Map();
  for (const element of shareScrollSnapshotElements()) {
    const payload = shareScrollPayloadForElement(element);
    if (!payload?.target) continue;
    byTarget.set(payload.target, payload);
  }
  return Array.from(byTarget.values()).slice(0, 100);
}

function shareReplaceSet(target, values, limit = 1000) {
  if (!target?.clear) return;
  target.clear();
  for (const item of shareStringArray(values, limit)) target.add(item);
}

function shareDiffRefsByRepoSnapshot() {
  const result = {};
  for (const [repo, refs] of Object.entries(diffRefsByRepo || {})) {
    const cleanRepo = String(repo || '').trim();
    if (!cleanRepo || !refs || typeof refs !== 'object') continue;
    result[cleanRepo] = {
      from: cleanDiffRef(refs.from, 'HEAD'),
      to: cleanDiffRef(refs.to, 'current'),
    };
  }
  return result;
}

function shareCleanDiffRefsByRepo(value) {
  const result = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
  for (const [repo, refs] of Object.entries(value)) {
    const cleanRepo = String(repo || '').trim();
    if (!cleanRepo || !refs || typeof refs !== 'object') continue;
    result[cleanRepo] = {
      from: cleanDiffRef(refs.from, 'HEAD'),
      to: cleanDiffRef(refs.to, 'current'),
    };
  }
  return result;
}

function shareEditorModesSnapshot() {
  const modes = [];
  const seen = new Set();
  const addMode = (path, item = null, mode = '') => {
    const cleanPath = String(path || '').trim();
    const cleanItem = item && isFileEditorItem(item) ? item : '';
    const cleanMode = editorViewModes.has(mode) ? mode : editorViewModeFor(cleanPath, cleanItem || null);
    if (!cleanPath || !editorViewModes.has(cleanMode)) return;
    const key = `${cleanPath}\n${cleanItem || cleanPath}`;
    if (seen.has(key)) return;
    seen.add(key);
    const entry = {path: cleanPath, item: cleanItem, mode: cleanMode};
    const state = openFiles.get(cleanPath);
    const itemKey = cleanItem || fileEditorItemFor(cleanPath);
    const viewState = fileEditorViewState.get(itemKey);
    if (viewState) {
      entry.viewState = {
        top: Math.max(0, Math.round(Number(viewState.scrollTop || 0))),
        left: Math.max(0, Math.round(Number(viewState.scrollLeft || 0))),
        anchor: Math.max(0, Math.round(Number(viewState.anchor || 0))),
        head: Math.max(0, Math.round(Number(viewState.head ?? viewState.anchor ?? 0))),
      };
    }
    if (cleanMode === 'diff') {
      const refs = diffRefParams(fileRepoForPath(cleanPath));
      entry.diffFromRef = cleanDiffRef(state?.diffPinnedFromRef || state?.diffFromRef || refs.from || 'HEAD', 'HEAD');
      entry.diffToRef = cleanDiffRef(state?.diffPinnedToRef || state?.diffToRef || refs.to || 'current', 'current');
      entry.diffExpandUnchanged = fileEditorDiffExpandUnchangedForItem(itemKey);
    }
    modes.push(entry);
  };
  for (const item of paneItems(layoutSlots)) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    if (path) addMode(path, item, editorViewModeFor(path, item));
  }
  for (const [path, state] of openFiles.entries()) {
    const viewModes = state?.viewMode instanceof Map ? state.viewMode : fileEditorViewModesForPath(path);
    for (const [key, mode] of viewModes.entries()) {
      addMode(path, key === path ? '' : key, mode);
    }
  }
  return modes;
}

function shareRememberEditorViewState(payload = {}, top = 0, left = 0) {
  const path = String(payload.path || '').trim();
  const item = String(payload.item || '').trim();
  const key = item && isFileEditorItem(item) ? item : (path ? fileEditorItemFor(path) : '');
  if (!key) return;
  const previous = fileEditorViewState.get(key) || {};
  fileEditorViewState.set(key, {
    ...previous,
    scrollTop: Math.max(0, Math.round(Number(top || 0))),
    scrollLeft: Math.max(0, Math.round(Number(left || 0))),
    anchor: Math.max(0, Math.round(Number(payload.anchor ?? previous.anchor ?? 0))),
    head: Math.max(0, Math.round(Number(payload.head ?? payload.anchor ?? previous.head ?? previous.anchor ?? 0))),
    scrollSnapshot: previous.scrollSnapshot || null,
  });
}

function shareApplyEditorModeEntry(entry = {}) {
  if (!entry || typeof entry !== 'object') return null;
  const path = String(entry.path || '').trim();
  const item = String(entry.item || '').trim();
  const mode = String(entry.mode || '').trim();
  if (!path || !editorViewModes.has(mode)) return null;
  const cleanItem = item && isFileEditorItem(item) ? item : '';
  if (cleanItem && !openFiles.has(path) && typeof registerFileEditorLayoutItem === 'function') {
    registerFileEditorLayoutItem(path, {item: cleanItem});
  }
  const key = cleanItem || path;
  fileEditorViewModesForPath(path, true).set(key, mode);
  const viewState = entry.viewState && typeof entry.viewState === 'object' ? entry.viewState : null;
  if (viewState) {
    shareRememberEditorViewState({
      path,
      item: cleanItem,
      anchor: viewState.anchor,
      head: viewState.head,
    }, viewState.top, viewState.left);
  }
  if (mode === 'diff') {
    const state = openFiles.get(path) || (cleanItem ? ensureFileState(path) : null);
    if (state) {
      state.diffPinnedFromRef = cleanDiffRef(entry.diffFromRef || state.diffPinnedFromRef || state.diffFromRef || 'HEAD', 'HEAD');
      state.diffPinnedToRef = cleanDiffRef(entry.diffToRef || state.diffPinnedToRef || state.diffToRef || 'current', 'current');
    }
    if ('diffExpandUnchanged' in entry && cleanItem) {
      fileEditorDiffExpandOverrides.set(cleanItem, entry.diffExpandUnchanged === true);
    }
  }
  return {path, item: cleanItem, mode};
}

function shareTerminalDimensionsSnapshot() {
  return Array.from(terminals.entries()).map(([session, item]) => ({
    session: String(session || ''),
    rows: Math.max(0, Math.round(Number(item?.term?.rows) || 0)),
    cols: Math.max(0, Math.round(Number(item?.term?.cols) || 0)),
  })).filter(entry => entry.session && entry.rows > 0 && entry.cols > 0);
}

function shareFinderStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const finder = {
    ...shareFinderSeed(),
    showHidden: fileExplorerShowHidden === true,
    treeDateMode: normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode),
    treeSortMode: ['az', 'za', 'newest', 'oldest'].includes(fileExplorerTreeSortMode) ? fileExplorerTreeSortMode : 'az',
    sessionFilesSortMode: normalizeSessionFilesSortMode(sessionFilesSortMode),
    diffRefFrom: cleanDiffRef(diffRefFrom, 'HEAD'),
    diffRefTo: cleanDiffRef(diffRefTo, 'current'),
  };
  if (compact) return finder;
  return {
    ...finder,
    expanded: shareSetSnapshot(fileExplorerExpanded),
    selectedPaths: shareSetSnapshot(fileExplorerSelectedPaths),
    selectionAnchor: fileExplorerSelectionAnchor || '',
    selectionLead: fileExplorerSelectionLead || '',
    changesFolderCollapsed: shareSetSnapshot(changesFolderCollapsed),
    changesRepoCollapsed: shareSetSnapshot(changesRepoCollapsed),
    tabberCollapsed: shareSetSnapshot(fileExplorerTabberCollapsed),
    diffRefsByRepo: shareDiffRefsByRepoSnapshot(),
  };
}

function shareEditorStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const editor = {
    globalThemeMode: normalizeGlobalThemeMode(globalThemeMode),
    terminalThemeMode: normalizeTerminalThemeMode(terminalThemeMode),
    themeMode: normalizeEditorThemeMode(fileEditorThemeMode),
    previewDisplayMode: normalizeEditorPreviewDisplayMode(fileEditorPreviewDisplayMode),
    wrapEnabled: fileEditorWrapEnabled === true,
    lineNumbersEnabled: fileEditorLineNumbersEnabled === true,
    blameEnabled: fileEditorBlameEnabled === true,
    diffExpandUnchanged: diffExpandUnchanged === true,
    previewFontSize: clampEditorPreviewFontSize(editorPreviewFontSize),
  };
  if (!compact) editor.modes = shareEditorModesSnapshot();
  return editor;
}

function sharePreferencesStateSnapshot(options = {}) {
  const compact = options.compact === true;
  return {
    searchText: String(preferencesSearchText || '').slice(0, compact ? 200 : 2000),
    collapsedSections: shareSetSnapshot(collapsedPreferenceSections, compact ? 40 : 200),
    resetConfirmVisible: preferencesResetConfirmVisible === true,
  };
}

function shareBaseUiStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const seed = shareLayoutSeed();
  return {
    layout: seed.layout,
    tabs: seed.tabs,
    viewport: shareViewportSnapshot(),
    appearance: shareAppearanceSnapshot(),
    terminalDims: shareTerminalDimensionsSnapshot(),
    chrome: {
      tabMetaVisible: tabMetaVisible !== false,
      infoSubTab: normalizedInfoSubTab(infoPanelSubTab),
    },
    autoApprove: shareAutoApproveStateSnapshot(),
    info: shareInfoStateSnapshot({includeRows: !compact}),
    finder: shareFinderStateSnapshot({compact}),
    editor: shareEditorStateSnapshot({compact}),
    preferences: sharePreferencesStateSnapshot({compact}),
  };
}

function shareCreateUiStateSnapshot() {
  return shareBaseUiStateSnapshot({compact: true});
}

function shareUiStateSnapshot() {
  return {
    ...shareBaseUiStateSnapshot({compact: false}),
    textWraps: shareWrappedTextDigestSnapshot(),
    scroll: shareScrollStateSnapshot(),
  };
}

function sharePublishUiState(options = {}) {
  if (!shareCanPublishUi()) return;
  sharePublish('ui-state', shareUiStateSnapshot(), options);
}

function scheduleShareUiStatePublish(options = {}) {
  if (!shareCanPublishUi()) return;
  if (shareUiStatePublishTimer) clearTimeout(shareUiStatePublishTimer);
  shareUiStatePublishTimer = setTimeout(() => {
    shareUiStatePublishTimer = null;
    sharePublishUiState(options);
  }, 80);
}

function scheduleShareTopologySnapshot(reason = 'topology') {
  const cleanReason = String(reason || 'topology').trim() || 'topology';
  scheduleShareUiStatePublish({reason: `topology:${cleanReason}`});
  scheduleShareTopologyDomKeyframe();
}

function scheduleShareTopologyDomKeyframe() {
  if (shareViewMode || !shareHasActiveShare() || !shareReplayFeatureEnabled()) return;
  shareReplayPauseMutationPublisherForTopology();
  if (shareReplayTopologyKeyframeTimer) return;
  shareReplayTopologyKeyframeTimer = setTimeout(() => {
    shareReplayTopologyKeyframeTimer = null;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        sharePublishDomKeyframe('topology');
      });
    });
  }, 0);
}

function scheduleShareViewportPublish() {
  if (shareViewMode || !shareCanPublishUi()) return;
  if (shareViewportPublishTimer) clearTimeout(shareViewportPublishTimer);
  shareViewportPublishTimer = setTimeout(() => {
    shareViewportPublishTimer = null;
    sharePublish('viewport', shareViewportSnapshot());
  }, 150);
}

function scheduleShareAppearancePublish(options = {}) {
  if (shareViewMode || !shareCanPublishUi()) return;
  if (shareAppearancePublishTimer) clearTimeout(shareAppearancePublishTimer);
  shareAppearancePublishTimer = setTimeout(() => {
    shareAppearancePublishTimer = null;
    sharePublish('appearance', shareAppearanceSnapshot());
  }, 80);
  if (options.topology !== false) scheduleShareTopologySnapshot(options.reason || 'appearance');
}

function applyShareViewportState(viewport = {}) {
  if (!shareViewMode || !viewport || typeof viewport !== 'object') return;
  const width = Number(viewport.width ?? viewport.w);
  const height = Number(viewport.height ?? viewport.h);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return;
  setAppViewportOverride({width, height});
  applyShareMirrorTransform();
}

function applyShareAppearanceNumber(appearance, key, settingKey, min, max, fallback) {
  if (!(key in appearance)) return fallback;
  const value = Math.max(min, Math.min(max, Math.round(Number(appearance[key]) || fallback)));
  clientSettings = mergeSettingObjects(clientSettings, {appearance: {[settingKey]: value}});
  return value;
}

function applyShareAppearanceString(appearance, key, settingKey, allowed, fallback) {
  if (!(key in appearance)) return fallback;
  const value = String(appearance[key] || '');
  const normalized = allowed.includes(value) ? value : fallback;
  clientSettings = mergeSettingObjects(clientSettings, {appearance: {[settingKey]: normalized}});
  return normalized;
}

function applyShareAppearanceState(appearance = {}) {
  if (!shareViewMode || !appearance || typeof appearance !== 'object') return;
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    const languagePref = String(appearance.languagePref || '').trim();
    const locale = String(appearance.locale || '').trim();
    if (languagePref || locale) {
      const nextPref = languagePref || locale;
      clientSettings = mergeSettingObjects(clientSettings, {general: {language: nextPref}});
      const resolvedLocale = locale || resolveLocalePref(nextPref);
      if (resolvedLocale && resolvedLocale !== i18nActiveLocaleId()) void applyLocale(resolvedLocale);
    }
    const uiFontSize = applyShareAppearanceNumber(appearance, 'uiFontSize', 'ui_font_size', 6, 20, numberSetting('appearance.ui_font_size', 13));
    terminalFontSize = applyShareAppearanceNumber(appearance, 'terminalFontSize', 'terminal_font_size', 6, 28, terminalFontSize);
    editorFontSize = applyShareAppearanceNumber(appearance, 'editorFontSize', 'editor_font_size', 6, 28, editorFontSize);
    editorPreviewFontSize = applyShareAppearanceNumber(appearance, 'previewFontSize', 'preview_font_size', 6, 32, editorPreviewFontSize);
    fileExplorerFontSize = applyShareAppearanceNumber(appearance, 'fileExplorerFontSize', 'file_explorer_font_size', 6, 24, fileExplorerFontSize);
    applyShareAppearanceNumber(appearance, 'tabWidth', 'tab_width', 120, 420, numberSetting('appearance.tab_width', 180));
    applyShareAppearanceNumber(appearance, 'paneSpacing', 'pane_spacing', 0, 20, numberSetting('appearance.pane_spacing', 3));
    applyShareAppearanceNumber(appearance, 'paneRingOpacity', 'pane_ring_opacity', 5, 100, numberSetting('appearance.pane_ring_opacity', 75));
    applyShareAppearanceNumber(appearance, 'inactivePaneOpacity', 'inactive_pane_opacity', 0, 100, numberSetting('appearance.inactive_pane_opacity', 60));
    globalThemeMode = normalizeGlobalThemeMode(appearance.theme || globalThemeMode);
    terminalThemeMode = normalizeTerminalThemeMode(appearance.terminalTheme || terminalThemeMode);
    clientSettings = mergeSettingObjects(clientSettings, {appearance: {
      theme: globalThemeMode,
      terminal_theme: terminalThemeMode,
      active_color: applyShareAppearanceString(appearance, 'activeColor', 'active_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white'], initialSetting('appearance.active_color', 'green')),
      separator_color: applyShareAppearanceString(appearance, 'separatorColor', 'separator_color', ['theme', 'green', 'blue', 'orange', 'yellow', 'purple', 'white'], initialSetting('appearance.separator_color', 'theme')),
      ui_font_size: uiFontSize,
    }});
    applyCssSettings();
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true, refreshEditors: true});
    applyEditorThemeMode({refreshEditors: true});
    renderSessionButtons();
    renderPaneTabStrips();
  } finally {
    finishRemoteApply();
  }
}

function applyShareTerminalDimensionsState(value = []) {
  if (!shareViewMode || !Array.isArray(value)) return;
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') continue;
    updateShareHostTerminalSize(entry.session, entry.rows, entry.cols);
  }
}

function applyShareEditorState(editor = {}) {
  if (!editor || typeof editor !== 'object') return;
  if ('globalThemeMode' in editor) globalThemeMode = normalizeGlobalThemeMode(editor.globalThemeMode);
  if ('terminalThemeMode' in editor) terminalThemeMode = normalizeTerminalThemeMode(editor.terminalThemeMode);
  if ('themeMode' in editor) fileEditorThemeMode = normalizeEditorThemeMode(editor.themeMode);
  if ('previewDisplayMode' in editor) fileEditorPreviewDisplayMode = normalizeEditorPreviewDisplayMode(editor.previewDisplayMode);
  if ('wrapEnabled' in editor) fileEditorWrapEnabled = editor.wrapEnabled === true;
  if ('lineNumbersEnabled' in editor) fileEditorLineNumbersEnabled = editor.lineNumbersEnabled === true;
  if ('blameEnabled' in editor) fileEditorBlameEnabled = editor.blameEnabled === true;
  if ('diffExpandUnchanged' in editor) diffExpandUnchanged = editor.diffExpandUnchanged === true;
  if ('previewFontSize' in editor) editorPreviewFontSize = clampEditorPreviewFontSize(editor.previewFontSize);
  const modes = Array.isArray(editor.modes) ? editor.modes : [];
  for (const entry of modes) shareApplyEditorModeEntry(entry);
  applyCssSettings();
  applyGlobalThemeMode({updateEditor: true, updateTerminals: true, refreshEditors: true});
  applyEditorThemeMode({refreshEditors: true});
  applyEditorWrapPreference();
  updateEditorPreviewFontControls();
  for (const panel of document.querySelectorAll('.file-editor-panel')) {
    const item = panel.dataset.layoutItem || fileEditorItemFor(panel.dataset.filePath || '');
    if (item) renderFileEditorPanel(panel, item, {updateActiveFile: false});
  }
  restoreShareScrollTargetsByPrefix('editor:');
  renderSessionButtons();
  renderPaneTabStrips();
}

function applyShareChromeState(chrome = {}) {
  if (!chrome || typeof chrome !== 'object') return;
  if ('tabMetaVisible' in chrome) {
    tabMetaVisible = chrome.tabMetaVisible !== false;
    renderTabMetaToggle();
  }
  if ('infoSubTab' in chrome) {
    infoPanelSubTab = normalizedInfoSubTab(chrome.infoSubTab);
    applyInfoSubTab();
    if (infoPanelSubTab === 'yoagent') {
      renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
    }
  }
  renderSessionButtons();
  renderPaneTabStrips();
}

async function applyShareFinderState(finder = {}) {
  if (!finder || typeof finder !== 'object') return;
  const session = String(finder.session || '').trim();
  const previousRoot = normalizeDirectoryPath(fileExplorerRoot || '');
  const previousExpandedSignature = shareSetSignature(fileExplorerExpanded);
  if ('mode' in finder) fileExplorerMode = normalizeFileExplorerMode(finder.mode);
  if ('rootMode' in finder) fileExplorerRootMode = finder.rootMode === 'fixed' ? 'fixed' : 'sync';
  if ('showHidden' in finder) fileExplorerShowHidden = finder.showHidden === true;
  if (isTmuxSession(session)) {
    fileExplorerChangesSelectedSession = session;
    fileExplorerExplicitSyncSession = session;
  }
  if ('treeDateMode' in finder) fileExplorerTreeDateMode = normalizeFileExplorerTreeDateMode(finder.treeDateMode);
  if ('treeSortMode' in finder) fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(finder.treeSortMode) ? finder.treeSortMode : 'az';
  if ('sessionFilesSortMode' in finder) sessionFilesSortMode = normalizeSessionFilesSortMode(finder.sessionFilesSortMode);
  if ('diffRefFrom' in finder) diffRefFrom = cleanDiffRef(finder.diffRefFrom, diffRefFrom || 'HEAD');
  if ('diffRefTo' in finder) diffRefTo = cleanDiffRef(finder.diffRefTo, diffRefTo || 'current');
  if ('diffRefsByRepo' in finder) diffRefsByRepo = shareCleanDiffRefsByRepo(finder.diffRefsByRepo);
  if ('expanded' in finder) shareReplaceSet(fileExplorerExpanded, finder.expanded);
  if ('selectedPaths' in finder) shareReplaceSet(fileExplorerSelectedPaths, finder.selectedPaths);
  if ('selectionAnchor' in finder) fileExplorerSelectionAnchor = String(finder.selectionAnchor || '');
  if ('selectionLead' in finder) fileExplorerSelectionLead = String(finder.selectionLead || '');
  if ('changesFolderCollapsed' in finder) shareReplaceSet(changesFolderCollapsed, finder.changesFolderCollapsed);
  if ('changesRepoCollapsed' in finder) shareReplaceSet(changesRepoCollapsed, finder.changesRepoCollapsed);
  if ('tabberCollapsed' in finder) shareReplaceSet(fileExplorerTabberCollapsed, finder.tabberCollapsed);
  const expandedChanged = previousExpandedSignature !== shareSetSignature(fileExplorerExpanded);
  applyFileExplorerMode();
  renderFileExplorerRootModeControls();
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  document.querySelectorAll('.file-explorer-hidden-toggle-panel').forEach(syncFileExplorerHiddenButton);
  syncFileExplorerTreeDateButtons();
  const root = String(finder.root || '').trim();
  const normalizedRoot = root ? normalizeDirectoryPath(expandUserPath(root)) : '';
  if (!itemInLayout(fileExplorerItemId)) {
    if (normalizedRoot) fileExplorerRoot = normalizedRoot;
    renderPaneTabStrips();
    return;
  }
  if (normalizedRoot) {
    fileExplorerRoot = normalizedRoot;
    const hasRenderedTreeRows = fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'));
    if (previousRoot !== normalizedRoot || !hasRenderedTreeRows) {
      await openFileExplorerAt(normalizedRoot, {preserveExpanded: true, preserveScroll: true});
    } else {
      setFileExplorerPathDisplay(fileExplorerRoot);
      const shouldRefreshTree = expandedChanged || 'showHidden' in finder || 'treeDateMode' in finder || 'treeSortMode' in finder;
      if (shouldRefreshTree) {
        const refreshed = await refreshFileExplorerTreesInPlace({
          root: fileExplorerRoot,
          preserveExpanded: false,
          preserveScroll: true,
        });
        if (!refreshed && !fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'))) {
          await openFileExplorerAt(normalizedRoot, {preserveExpanded: true, preserveScroll: true});
        }
      }
    }
  } else if (fileExplorerRoot) {
    await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  }
  if (fileExplorerMode === 'tabber') {
    refreshTabberPanels();
    fetchTabberActivity();
  } else {
    renderFileExplorerChangesPanels({force: true});
    if (fileExplorerChangesSelectedSession) {
      fetchSessionFiles({destination: 'finder', session: fileExplorerChangesSelectedSession, silent: true, force: false, background: true});
    }
  }
  restoreShareScrollTargetByKey(`finder:${normalizeFileExplorerMode(fileExplorerMode)}`);
  updateFileExplorerCurrentFileHighlight();
  renderPaneTabStrips();
}

function applySharePreferencesState(preferences = {}) {
  if (!preferences || typeof preferences !== 'object') return;
  if ('searchText' in preferences) preferencesSearchText = String(preferences.searchText || '');
  if (Array.isArray(preferences.collapsedSections)) {
    collapsedPreferenceSections = new Set(shareStringArray(preferences.collapsedSections, 200));
  }
  if ('resetConfirmVisible' in preferences) preferencesResetConfirmVisible = preferences.resetConfirmVisible === true;
  renderPreferencesPanels({force: true});
  restoreShareScrollTargetByKey('preferences');
}

function applyShareAutoApproveState(autoApprove = {}) {
  if (!autoApprove || typeof autoApprove !== 'object') return;
  const payload = {sessions: autoApprove.sessions && typeof autoApprove.sessions === 'object' ? autoApprove.sessions : {}};
  applyAutoApprovePayload(payload);
  renderSessionButtons();
  renderPaneTabStrips();
}

async function applyShareUiState(payload = {}) {
  if (!shareSemanticMirrorApplyAllowed() || !payload || typeof payload !== 'object') return;
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    applyShareViewportState(payload.viewport || {});
    applyShareAppearanceState(payload.appearance || {});
    applyShareTerminalDimensionsState(payload.terminalDims || []);
    if (payload.layout || payload.tabs) {
      const next = layoutFromParam(payload.layout || '', payload.tabs || '', {preserveMissingFileExplorer: true});
      if (next) applyLayoutSlots(next, {prune: false, preserveMissingFileExplorer: true});
    }
    applyShareChromeState(payload.chrome || {});
    applyShareAutoApproveState(payload.autoApprove || {});
    applyShareInfoState(payload.info || {});
    applyShareEditorState(payload.editor || {});
    applySharePreferencesState(payload.preferences || {});
    await applyShareFinderState(payload.finder || {});
    applyShareTextWrapMetrics(payload.textWraps || []);
    applyShareScrollSnapshot(payload.scroll || []);
  } finally {
    finishRemoteApply();
  }
}

const sharePointerPublishIntervalMs = 33;

function sharePointerPayloadForPoint(clientX, clientY, options = {}) {
  const point = appSpacePoint(clientX, clientY);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  return {
    scope: 'viewport',
    x: Math.round(point.x * 10) / 10,
    y: Math.round(point.y * 10) / 10,
    ...(options.click ? {click: true} : {}),
  };
}

function sharePointerPayloadForEvent(event, options = {}) {
  return sharePointerPayloadForPoint(event?.clientX, event?.clientY, options);
}

function sharePointFromPointerPayload(payload = {}) {
  if (payload.scope && payload.scope !== 'viewport') return null;
  const x = Number(payload.x);
  const y = Number(payload.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const point = visualPointFromAppSpace(x, y);
  return {
    x: point.x,
    y: point.y,
    scope: 'viewport',
  };
}

function sharePointerSenderKey(sender = '') {
  const clean = String(sender || '').trim();
  return clean || 'host';
}

function sharePointerSenderColor(sender = '') {
  const palette = ['#e53935', '#00897b', '#3949ab', '#f4511e', '#8e24aa', '#0277bd', '#6d8f00', '#d81b60'];
  const text = sharePointerSenderKey(sender);
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) hash = ((hash * 31) + text.charCodeAt(index)) >>> 0;
  return palette[hash % palette.length];
}

function ensureSharePointerGhost(sender = '') {
  const key = sharePointerSenderKey(sender);
  const existing = sharePointerGhosts.get(key);
  if (existing?.isConnected) return existing;
  const ghost = document.createElement('div');
  ghost.className = 'share-ghost-cursor';
  ghost.dataset.shareSender = key;
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.setProperty('--share-cursor-color', sharePointerSenderColor(key));
  document.body.appendChild(ghost);
  sharePointerGhosts.set(key, ghost);
  sharePointerGhost = ghost;
  return ghost;
}

function renderShareClickRipple(x, y, sender = '') {
  const ripple = document.createElement('div');
  ripple.className = 'share-click-ripple';
  ripple.setAttribute('aria-hidden', 'true');
  ripple.style.left = `${Math.round(x)}px`;
  ripple.style.top = `${Math.round(y)}px`;
  ripple.style.setProperty('--share-cursor-color', sharePointerSenderColor(sender));
  document.body.appendChild(ripple);
  setTimeout(() => ripple.remove(), 560);
}

function renderSharePointerGhost(payload = {}) {
  if (!shareViewMode && !shareHasActiveShare()) return;
  if (payload.sender && payload.sender === shareClientId) return;
  const point = sharePointFromPointerPayload(payload);
  if (!point) return;
  const sender = sharePointerSenderKey(payload.sender || '');
  const ghost = ensureSharePointerGhost(sender);
  ghost.style.transform = `translate3d(${Math.round(point.x)}px, ${Math.round(point.y)}px, 0)`;
  ghost.classList.add('visible');
  const existingTimer = sharePointerHideTimers.get(sender);
  if (existingTimer) clearTimeout(existingTimer);
  const timer = setTimeout(() => {
    sharePointerHideTimers.delete(sender);
    sharePointerGhosts.get(sender)?.classList.remove('visible');
  }, 1800);
  sharePointerHideTimers.set(sender, timer);
  sharePointerHideTimer = timer;
  if (payload.click === true) renderShareClickRipple(point.x, point.y, sender);
}

function sharePublishPointerEvent(event, options = {}) {
  if (event?.isPrimary === false) return;
  const payload = sharePointerPayloadForEvent(event, options);
  if (!payload) return;
  if (!shareViewMode && shareReplayFeatureEnabled()) {
    shareReplayPointerLastPayload = {...payload, visible: true};
    if (!shareReplayPointerFramePending) {
      shareReplayPointerFramePending = true;
      requestAnimationFrame(() => {
        shareReplayPointerFramePending = false;
        const latest = shareReplayPointerLastPayload;
        shareReplayPointerLastPayload = null;
        if (latest) shareReplayPublishDeltaPayload({pointer: latest}, latest.click ? 'pointer-click' : 'pointer');
      });
    }
  }
  sharePublish('pointer', payload);
}

function queueSharePointerMove(event) {
  if (!shareCanPublishUi()) return;
  sharePointerLastEvent = event;
  if (sharePointerFramePending) return;
  sharePointerFramePending = true;
  requestAnimationFrame(() => {
    sharePointerFramePending = false;
    const now = performance.now();
    if (now - sharePointerLastPublishedAt < sharePointerPublishIntervalMs) return;
    sharePointerLastPublishedAt = now;
    const latest = sharePointerLastEvent;
    sharePointerLastEvent = null;
    if (latest) sharePublishPointerEvent(latest);
  });
}

function installSharePointerPublisher() {
  document.addEventListener('pointermove', queueSharePointerMove, {passive: true});
  document.addEventListener('pointerdown', event => {
    if (!shareCanPublishUi() || event?.isPrimary === false) return;
    sharePublishPointerEvent(event, {click: true});
  }, {passive: true});
}

function shareScrollTargetForElement(element) {
  if (!element?.closest) return null;
  const editorPanel = element.closest('.file-editor-panel');
  if (editorPanel?.dataset?.filePath) {
    const item = editorPanel.dataset.layoutItem || (typeof fileEditorPanelItem === 'function' ? fileEditorPanelItem(editorPanel) : '') || '';
    const path = editorPanel.dataset.filePath || '';
    const source = element.closest('.file-editor-preview-pane-panel') ? 'preview' : 'editor';
    const scroller = source === 'preview'
      ? editorPanel.querySelector('.file-editor-preview-pane-panel')
      : (editorPanel._cmView?.scrollDOM || element.closest('.cm-scroller'));
    if (scroller) return {target: `editor:${item || path}:${source}`, kind: 'editor', item, path, source, element: scroller, panel: editorPanel};
  }
  const finderScroller = element.closest('.file-explorer-tree-panel, .file-explorer-changes-panel');
  if (finderScroller) {
    const mode = normalizeFileExplorerMode(fileExplorerMode);
    return {target: `finder:${mode}`, kind: 'finder', mode, element: finderScroller};
  }
  const preferencesScroller = element.closest('.preferences-scroll');
  if (preferencesScroller) return {target: 'preferences', kind: 'preferences', element: preferencesScroller};
  const infoScroller = element.closest('#info-content');
  if (infoScroller) return {target: 'info', kind: 'info', element: infoScroller};
  const terminal = element.closest('.terminal[id^="term-"]');
  if (terminal) {
    const session = terminal.id.slice('term-'.length);
    const item = terminals.get(session);
    const viewport = terminal.querySelector('.xterm-viewport') || element;
    return {target: `terminal:${session}`, kind: 'terminal', session, element: viewport, term: item?.term || null};
  }
  return null;
}

function shareScrollPayloadForElement(element) {
  const descriptor = shareScrollTargetForElement(element);
  if (!descriptor?.target || !descriptor.element) return null;
  const payload = {
    target: descriptor.target,
    kind: descriptor.kind,
    top: Math.max(0, Math.round(Number(descriptor.element.scrollTop || 0))),
    left: Math.max(0, Math.round(Number(descriptor.element.scrollLeft || 0))),
  };
  if (descriptor.kind === 'editor') {
    payload.path = descriptor.path || '';
    payload.item = descriptor.item || '';
    payload.source = descriptor.source || 'editor';
    const view = descriptor.panel?._cmView || null;
    const selection = view?.state?.selection?.main;
    if (selection) {
      payload.anchor = Math.max(0, Number(selection.anchor || 0));
      payload.head = Math.max(0, Number(selection.head ?? selection.anchor ?? 0));
    }
  } else if (descriptor.kind === 'finder') {
    payload.mode = descriptor.mode || '';
  } else if (descriptor.kind === 'terminal') {
    payload.session = descriptor.session || '';
    const viewportY = Number(descriptor.term?.buffer?.active?.viewportY);
    if (Number.isFinite(viewportY)) payload.top = Math.max(0, Math.round(viewportY));
  }
  return payload;
}

function scheduleShareScrollPublishForElement(element) {
  if (!shareViewMode && shareReplayFeatureEnabled() && !applyingShareRemoteScroll) {
    scheduleShareReplayScrollPublishForElement(element);
  }
  if (!shareCanPublishScroll() || applyingShareRemoteScroll) {
    if (shareViewMode && !shareWriteMode) restoreShareReadonlyScrollTarget(element);
    return;
  }
  const payload = shareScrollPayloadForElement(element);
  if (!payload?.target) return;
  if (shareScrollPublishTimers.has(payload.target)) {
    shareScrollPublishTimers.get(payload.target).payload = payload;
    return;
  }
  const state = {payload};
  state.timer = setTimeout(() => {
    shareScrollPublishTimers.delete(payload.target);
    if (shareCanPublishScroll()) sharePublish('scroll', state.payload);
  }, 50);
  shareScrollPublishTimers.set(payload.target, state);
}

function scheduleShareReplayScrollPublishForElement(element) {
  if (shareViewMode || !shareReplayFeatureEnabled() || applyingShareRemoteScroll) return;
  const entry = shareReplayScrollEntryForElement(element);
  if (!entry?.nodeId) return;
  const key = String(entry.nodeId);
  if (shareReplayScrollPublishTimers.has(key)) {
    shareReplayScrollPublishTimers.get(key).entry = entry;
    return;
  }
  const state = {entry};
  state.timer = setTimeout(() => {
    shareReplayScrollPublishTimers.delete(key);
    shareReplayPublishDeltaPayload({scroll: [state.entry]}, 'scroll');
  }, 50);
  shareReplayScrollPublishTimers.set(key, state);
}

function sharePublishFileVersion(path, options = {}) {
  const cleanPath = String(path || '').trim();
  if (!cleanPath || !shareCanPublishUi()) return;
  sharePublish('file-version', {
    path: cleanPath,
    mtime: Number(options.mtime || 0) || 0,
    size: Number(options.size || 0) || 0,
    version: Date.now(),
  });
}

function shareScrollElementForPayload(payload = {}) {
  const target = String(payload.target || '');
  if (target.startsWith('editor:')) {
    const path = String(payload.path || '');
    const item = String(payload.item || '');
    const source = String(payload.source || 'editor') === 'preview' ? 'preview' : 'editor';
    const panels = Array.from(document.querySelectorAll('.file-editor-panel'));
    const panel = panels.find(candidate => (
      (item && candidate.dataset?.layoutItem === item)
      || (path && candidate.dataset?.filePath === path)
    ));
    if (!panel) return null;
    const element = source === 'preview'
      ? panel.querySelector('.file-editor-preview-pane-panel')
      : (panel._cmView?.scrollDOM || panel.querySelector('.cm-scroller'));
    return element ? {kind: 'editor', source, element, panel} : null;
  }
  if (target.startsWith('finder:')) {
    const mode = String(payload.mode || target.slice('finder:'.length) || '');
    const selector = mode === 'diff' || mode === 'tabber' ? '.file-explorer-changes-panel' : '.file-explorer-tree-panel';
    const element = Array.from(document.querySelectorAll(selector)).find(node => node.offsetParent !== null) || document.querySelector(selector);
    return element ? {kind: 'finder', element} : null;
  }
  if (target === 'preferences') {
    const element = Array.from(document.querySelectorAll('.preferences-scroll')).find(node => node.offsetParent !== null) || document.querySelector('.preferences-scroll');
    return element ? {kind: 'preferences', element} : null;
  }
  if (target === 'info') {
    const element = document.getElementById('info-content');
    return element ? {kind: 'info', element} : null;
  }
  if (target.startsWith('terminal:')) {
    const session = String(payload.session || target.slice('terminal:'.length) || '');
    const item = terminals.get(session);
    const element = item?.container?.querySelector?.('.xterm-viewport') || null;
    return item ? {kind: 'terminal', session, term: item.term, element} : null;
  }
  return null;
}

function applyShareScrollState(payload = {}) {
  if (!payload || typeof payload !== 'object') return;
  const target = String(payload.target || '');
  const top = Math.max(0, Math.round(Number(payload.top || 0)));
  const left = Math.max(0, Math.round(Number(payload.left || 0)));
  if (target) {
    shareLastAppliedScrollByTarget.set(target, {top, left});
    shareLastAppliedScrollPayloadByTarget.set(target, {...payload, target, top, left});
  }
  if (String(payload.kind || '') === 'editor' || target.startsWith('editor:')) {
    shareRememberEditorViewState(payload, top, left);
  }
  const descriptor = shareScrollElementForPayload(payload);
  if (!descriptor) {
    scheduleShareScrollRestoreByKey(target);
    return;
  }
  const previous = applyingShareRemoteScroll;
  applyingShareRemoteScroll = true;
  try {
    if (descriptor.kind === 'terminal' && typeof descriptor.term?.scrollToLine === 'function') {
      descriptor.term.scrollToLine(top);
      return;
    }
    if (descriptor.element) {
      descriptor.element.scrollTop = top;
      descriptor.element.scrollLeft = left;
    }
    if (descriptor.kind === 'editor' && descriptor.panel?._cmView) {
      const view = descriptor.panel._cmView;
      const docLength = view.state?.doc?.length || 0;
      const anchor = Math.max(0, Math.min(docLength, Number(payload.anchor || 0)));
      const head = Math.max(0, Math.min(docLength, Number(payload.head ?? anchor)));
      if (Number.isFinite(anchor) && Number.isFinite(head)) {
        try { view.dispatch({selection: {anchor, head}}); } catch (_) {}
      }
    }
  } finally {
    scheduleShareScrollRestoreByKey(target);
    requestAnimationFrame(() => { applyingShareRemoteScroll = previous; });
  }
}

function applyShareScrollSnapshot(scroll = []) {
  if (!Array.isArray(scroll)) return;
  for (const payload of scroll.slice(0, 100)) applyShareScrollState(payload);
}

async function applyShareFileVersion(payload = {}) {
  if (!shareViewMode || !payload || typeof payload !== 'object') return false;
  const path = String(payload.path || '').trim();
  if (!path || !openFiles.has(path)) return false;
  const state = openFiles.get(path);
  if (state?.dirty) return false;
  return replaceOpenFileStateFromDisk(path);
}

function installShareScrollPublisher() {
  document.addEventListener('scroll', event => {
    if (shareViewMode && !shareWriteMode) {
      restoreShareReadonlyScrollTarget(event.target);
      return;
    }
    scheduleShareScrollPublishForElement(event.target);
  }, true);
}

function shareRoundedRect(rect = {}) {
  return {
    left: Math.round(Number(rect.left || 0)),
    top: Math.round(Number(rect.top || 0)),
    width: Math.round(Number(rect.width || 0)),
    height: Math.round(Number(rect.height || 0)),
  };
}

function shareFontFingerprint() {
  const canvas = document.createElement?.('canvas');
  const context = canvas?.getContext?.('2d');
  if (!context) return {};
  context.font = `${Math.max(6, Math.round(numberSetting('appearance.ui_font_size', 13)))}px ${getComputedStyle(document.documentElement).getPropertyValue('--ui-font') || 'sans-serif'}`;
  const ui = Math.round(context.measureText('YOLOmux Tabs README.md 0123456789').width * 10) / 10;
  context.font = `${Math.max(6, Math.round(terminalFontSize))}px ${terminalFontFamily}`;
  const mono = Math.round(context.measureText('YOLOmux Tabs README.md 0123456789').width * 10) / 10;
  return {ui, mono};
}

const shareWrappedTextDigestSelectors = [
  'textarea[data-setting-path]',
  'input[type="text"][data-setting-path]',
  '.preferences-setting-help',
  '.preferences-global-reset-warning',
  '.app-menu-command-label',
  '.pane-tab .session-button-dir',
  '.file-tree-name',
  '.changes-file-name',
  '.info-row',
  '.tabber-row-label',
];

function shareWrappedTextElementKey(element, index) {
  const data = element?.dataset || {};
  return String(
    data.settingPath
    || data.path
    || data.paneTab
    || data.item
    || data.session
    || element?.getAttribute?.('aria-label')
    || element?.id
    || `${String(element?.localName || element?.tagName || 'node').toLowerCase()}:${index}`,
  ).slice(0, 240);
}

function shareWrappedTextValue(element) {
  if (!element) return '';
  if ('value' in element) return String(element.value || '');
  return String(element.textContent || '');
}

function shareWrappedTextNodesByKey() {
  const result = new Map();
  const root = appRootElement();
  const queryRoot = root?.querySelectorAll ? root : document;
  const nodes = [];
  for (const selector of shareWrappedTextDigestSelectors) {
    try {
      queryRoot.querySelectorAll(selector).forEach(node => {
        if (node && !nodes.includes(node)) nodes.push(node);
      });
    } catch (_) {}
  }
  nodes.forEach((node, index) => {
    const key = shareWrappedTextElementKey(node, index);
    if (key && !result.has(key)) result.set(key, node);
  });
  return result;
}

function applyShareTextWrapMetrics(metrics = []) {
  if (!shareViewMode || !Array.isArray(metrics)) return;
  shareAppliedTextWrapMetricsByKey.clear();
  const nodesByKey = shareWrappedTextNodesByKey();
  for (const metric of metrics.slice(0, 80)) {
    if (!metric || typeof metric !== 'object') continue;
    const key = String(metric.key || '').trim();
    if (!key) continue;
    shareAppliedTextWrapMetricsByKey.set(key, metric);
    const node = nodesByKey.get(key);
    if (!node?.style) continue;
    const tag = String(node.localName || node.tagName || '').toLowerCase();
    const rect = metric.rect && typeof metric.rect === 'object' ? metric.rect : {};
    const width = Math.round(Number(rect.width || 0));
    const height = Math.round(Number(rect.height || 0));
    if (tag === 'textarea' && node.dataset?.settingPath !== undefined) {
      if (width > 0) node.style.width = `${width}px`;
      if (height > 0) {
        node.style.height = `${height}px`;
        node.style.minHeight = `${height}px`;
        node.style.maxHeight = `${height}px`;
      }
      node.style.overflowY = Number(metric.scrollHeight || 0) > height ? 'auto' : 'hidden';
    } else if (tag === 'input' && node.dataset?.settingPath !== undefined) {
      if (width > 0) node.style.width = `${width}px`;
      if (height > 0) node.style.height = `${height}px`;
    } else {
      const current = shareRoundedRect(appSpaceRect(node));
      const widthDiffers = width > 0 && Math.round(Number(current.width || 0)) !== width;
      const heightDiffers = height > 0 && Math.round(Number(current.height || 0)) !== height;
      if (widthDiffers) {
        node.style.width = `${width}px`;
        node.style.maxWidth = `${width}px`;
      }
      if (heightDiffers) {
        node.style.height = `${height}px`;
        node.style.minHeight = `${height}px`;
        node.style.maxHeight = `${height}px`;
      }
      if (widthDiffers || heightDiffers) node.style.overflow = 'hidden';
    }
  }
}

function shareTextWrapDigestEntryWithHostMetrics(entry, metric = null) {
  if (!shareViewMode || !metric || typeof metric !== 'object') return entry;
  const rect = metric.rect && typeof metric.rect === 'object' ? metric.rect : {};
  const hostLeft = Math.round(Number(rect.left));
  const hostTop = Math.round(Number(rect.top));
  const hostWidth = Math.round(Number(rect.width || 0));
  const hostHeight = Math.round(Number(rect.height || 0));
  const nextRect = {...entry.rect};
  if (Number.isFinite(hostLeft)) nextRect.left = hostLeft;
  if (Number.isFinite(hostTop)) nextRect.top = hostTop;
  if (hostWidth > 0) nextRect.width = hostWidth;
  if (hostHeight > 0) nextRect.height = hostHeight;
  return {
    ...entry,
    rect: nextRect,
    clientWidth: hostWidth > 0 ? hostWidth : entry.clientWidth,
    clientHeight: hostHeight > 0 ? hostHeight : entry.clientHeight,
    scrollWidth: Math.round(Number(metric.scrollWidth || entry.scrollWidth || 0)),
    scrollHeight: Math.round(Number(metric.scrollHeight || entry.scrollHeight || 0)),
    font: String(metric.font || entry.font || '').slice(0, 200),
    fontFamily: String(metric.fontFamily || entry.fontFamily || '').slice(0, 160),
    fontSize: String(metric.fontSize || entry.fontSize || ''),
    lineHeight: String(metric.lineHeight || entry.lineHeight || ''),
    letterSpacing: String(metric.letterSpacing || entry.letterSpacing || ''),
    whiteSpace: String(metric.whiteSpace || entry.whiteSpace || ''),
    wordBreak: String(metric.wordBreak || entry.wordBreak || ''),
    overflowWrap: String(metric.overflowWrap || entry.overflowWrap || ''),
  };
}

function shareWrappedTextDigestSnapshot() {
  const root = appRootElement();
  const nodes = [];
  const addNode = node => {
    if (!node || nodes.includes(node)) return;
    const rect = node.getBoundingClientRect?.();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    nodes.push(node);
  };
  for (const selector of shareWrappedTextDigestSelectors) {
    const queryRoot = root?.querySelectorAll ? root : document;
    try {
      queryRoot.querySelectorAll(selector).forEach(addNode);
    } catch (_) {}
  }
  return nodes.slice(0, 80).map((node, index) => {
    const style = getComputedStyle(node);
    const rect = shareRoundedRect(appSpaceRect(node));
    const value = shareWrappedTextValue(node);
    const key = shareWrappedTextElementKey(node, index);
    const entry = {
      key,
      tag: String(node.localName || node.tagName || '').toLowerCase(),
      rect,
      clientWidth: Math.round(Number(node.clientWidth || rect.width || 0)),
      clientHeight: Math.round(Number(node.clientHeight || rect.height || 0)),
      scrollWidth: Math.round(Number(node.scrollWidth || 0)),
      scrollHeight: Math.round(Number(node.scrollHeight || 0)),
      textHash: shareHashText(value),
      textLength: value.length,
      font: String(style?.font || '').slice(0, 200),
      fontFamily: String(style?.fontFamily || '').slice(0, 160),
      fontSize: String(style?.fontSize || ''),
      lineHeight: String(style?.lineHeight || ''),
      letterSpacing: String(style?.letterSpacing || ''),
      whiteSpace: String(style?.whiteSpace || ''),
      wordBreak: String(style?.wordBreak || ''),
      overflowWrap: String(style?.overflowWrap || ''),
    };
    return shareTextWrapDigestEntryWithHostMetrics(entry, shareAppliedTextWrapMetricsByKey.get(key));
  }).sort((a, b) => a.key.localeCompare(b.key) || a.tag.localeCompare(b.tag));
}

function shareTerminalCellDigest(session, item) {
  const cell = item?.term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || item?.term?._core?._renderService?.dimensions?.css?.cell
    || {};
  return {
    session: String(session || ''),
    cols: Number(item?.term?.cols || 0),
    rows: Number(item?.term?.rows || 0),
    cellWidth: Math.round(Number(cell.width || 0) * 10) / 10,
    cellHeight: Math.round(Number(cell.height || 0) * 10) / 10,
  };
}

function shareEditorDigest(panel) {
  const item = panel?.dataset?.layoutItem || '';
  const path = panel?.dataset?.filePath || '';
  const mode = typeof fileEditorPanelMode === 'function' ? fileEditorPanelMode(panel) : '';
  const state = openFiles.get(path);
  const kind = state?.loading ? 'loading' : String(state?.kind || 'missing');
  const content = kind === 'text' ? String(state?.content || '') : '';
  const error = kind === 'error' || kind === 'too-large' || kind === 'missing'
    ? String(state?.error || state?.message || '')
    : '';
  return {
    item,
    path,
    mode,
    rect: shareRoundedRect(appSpaceRect(panel)),
    kind,
    dirty: state?.dirty === true,
    contentHash: content ? shareHashText(content) : '',
    contentLength: content.length,
    errorHash: error ? shareHashText(error) : '',
    size: Number.isFinite(Number(state?.size)) ? Number(state.size) : 0,
    mtime: Number.isFinite(Number(state?.mtime)) ? Number(state.mtime) : 0,
  };
}

function shareSlotDigestSnapshot() {
  return {
    layout: layoutParamValue(layoutSlots),
    slots: layoutSlotKeys().map(slot => ({
      slot,
      placeholder: paneIsPlaceholder(slot),
    })),
  };
}

function shareGeometryDigestSnapshot() {
  const viewport = appViewport();
  const slots = shareSlotDigestSnapshot();
  const tabStrips = Array.from(document.querySelectorAll('.dv-tabs-container, .pane-tabs')).map((strip, index) => {
    const tabs = Array.from(strip.querySelectorAll('.dockview-pane-tab, .pane-tab'));
    const rect = shareRoundedRect(appSpaceRect(strip));
    const first = tabs[0] ? shareRoundedRect(appSpaceRect(tabs[0])) : null;
    const last = tabs.length ? shareRoundedRect(appSpaceRect(tabs[tabs.length - 1])) : null;
    const hiddenStrip = rect.width === 0 && rect.height === 0
      && (!first || (first.width === 0 && first.height === 0))
      && (!last || (last.width === 0 && last.height === 0));
    if (hiddenStrip) return null;
    return {
      index,
      rect,
      first,
      last,
      items: tabs.map(tab => tab.dataset?.paneTab || tab.dataset?.item || tab.textContent?.trim?.() || '').filter(Boolean),
      count: tabs.length,
    };
  }).filter(Boolean);
  const terminalCells = Array.from(terminals.entries()).map(([session, item]) => shareTerminalCellDigest(session, item)).sort((a, b) => a.session.localeCompare(b.session));
  const editors = Array.from(document.querySelectorAll('.file-editor-panel'))
    .map(shareEditorDigest)
    .sort((a, b) => (a.item || a.path).localeCompare(b.item || b.path));
  return {
    viewport: {width: viewport.width, height: viewport.height},
    slots,
    tabStrips,
    terminalCells,
    editors,
    fonts: shareFontFingerprint(),
    textWraps: shareWrappedTextDigestSnapshot(),
  };
}

function stableDigestJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableDigestJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableDigestJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function shareHashText(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function shareGeometryDigestValue(snapshot = shareGeometryDigestSnapshot()) {
  return shareHashText(stableDigestJson(snapshot));
}

function shareGeometryDigestFrame() {
  const snapshot = shareGeometryDigestSnapshot();
  return {digest: shareGeometryDigestValue(snapshot), snapshot};
}

function shareGeometryRectsWithinTolerance(hostRect = {}, localRect = {}, tolerancePx = 1) {
  for (const key of ['left', 'top', 'width', 'height']) {
    const hostValue = Math.round(Number(hostRect?.[key] || 0));
    const localValue = Math.round(Number(localRect?.[key] || 0));
    if (Math.abs(hostValue - localValue) > tolerancePx) return false;
  }
  return true;
}

function shareTabStripEntriesEquivalent(hostStrip = {}, localStrip = {}) {
  return Number(hostStrip?.index) === Number(localStrip?.index)
    && Number(hostStrip?.count || 0) === Number(localStrip?.count || 0)
    && stableDigestJson(hostStrip?.items || []) === stableDigestJson(localStrip?.items || [])
    && shareGeometryRectsWithinTolerance(hostStrip?.rect, localStrip?.rect)
    && shareGeometryRectsWithinTolerance(hostStrip?.first, localStrip?.first)
    && shareGeometryRectsWithinTolerance(hostStrip?.last, localStrip?.last);
}

function shareTabStripsEquivalent(hostTabStrips = [], localTabStrips = []) {
  if (!Array.isArray(hostTabStrips) || !Array.isArray(localTabStrips)) return false;
  if (hostTabStrips.length !== localTabStrips.length) return false;
  for (let index = 0; index < hostTabStrips.length; index += 1) {
    if (!shareTabStripEntriesEquivalent(hostTabStrips[index], localTabStrips[index])) return false;
  }
  return true;
}

function shareGeometryFirstDifference(host = {}, local = {}) {
  const hostSnapshot = host.snapshot && typeof host.snapshot === 'object' ? host.snapshot : {};
  const localSnapshot = local.snapshot && typeof local.snapshot === 'object' ? local.snapshot : {};
  for (const key of ['viewport', 'fonts', 'slots', 'tabStrips', 'terminalCells', 'editors', 'textWraps']) {
    if (key === 'tabStrips' && shareTabStripsEquivalent(hostSnapshot[key], localSnapshot[key])) continue;
    if (stableDigestJson(hostSnapshot[key]) !== stableDigestJson(localSnapshot[key])) return key;
  }
  return '';
}

function shareDebugSecretValues() {
  const values = new Set();
  const add = value => {
    const text = String(value || '');
    if (text.length >= 4) values.add(text);
  };
  add(shareToken);
  add(shareBootstrap?.token);
  for (const share of activeShares || []) add(share?.token);
  return Array.from(values).sort((a, b) => b.length - a.length);
}

function shareRedactSecretText(value) {
  return shareReplayRedactText(value);
}

function shareRedactDiagnosticValue(value, depth = 0) {
  if (depth > 12) return '[truncated-depth]';
  if (typeof value === 'string') return shareRedactSecretText(value);
  if (typeof value !== 'object' || value === null) return value;
  if (Array.isArray(value)) return value.map(item => shareRedactDiagnosticValue(item, depth + 1));
  const result = {};
  for (const [key, rawValue] of Object.entries(value)) {
    result[key] = /token|secret/i.test(key) && typeof rawValue === 'string'
      ? '[redacted-share-token]'
      : shareRedactDiagnosticValue(rawValue, depth + 1);
  }
  return result;
}

function shareDebugNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) / 100 : null;
}

function shareDebugRect(rect) {
  if (!rect) return null;
  return {
    left: shareDebugNumber(rect.left),
    top: shareDebugNumber(rect.top),
    right: shareDebugNumber(rect.right),
    bottom: shareDebugNumber(rect.bottom),
    width: shareDebugNumber(rect.width),
    height: shareDebugNumber(rect.height),
  };
}

function shareDebugVisualViewportSnapshot() {
  const viewport = window.visualViewport;
  if (!viewport) return null;
  return {
    width: shareDebugNumber(viewport.width),
    height: shareDebugNumber(viewport.height),
    offsetLeft: shareDebugNumber(viewport.offsetLeft),
    offsetTop: shareDebugNumber(viewport.offsetTop),
    pageLeft: shareDebugNumber(viewport.pageLeft),
    pageTop: shareDebugNumber(viewport.pageTop),
    scale: shareDebugNumber(viewport.scale),
  };
}

function shareDebugLocationSnapshot() {
  return {
    protocol: location.protocol,
    host: location.host,
    pathname: location.pathname,
    search: shareRedactSecretText(location.search || ''),
    hash: location.hash ? shareRedactSecretText(location.hash) : '',
  };
}

function shareDebugContextSnapshot() {
  const root = appRootElement();
  return shareRedactDiagnosticValue({
    share: {
      id: shareBootstrap?.id || '',
      mode: shareBootstrap?.mode || '',
      view: shareViewMode,
      write: shareWriteMode,
      fit: shareViewFit,
      viewerId: shareViewerId || '',
      repairInFlight: shareGeometryRepairInFlight,
      resyncInFlight: shareGeometryResyncInFlight,
    },
    location: shareDebugLocationSnapshot(),
    browser: {
      userAgent: navigator.userAgent || '',
      platform: navigator.platform || '',
      language: navigator.language || '',
      devicePixelRatio: shareDebugNumber(window.devicePixelRatio || 1),
    },
    viewport: {
      native: nativeViewport(),
      app: appViewport(),
      visual: shareDebugVisualViewportSnapshot(),
      documentElement: {
        clientWidth: document.documentElement?.clientWidth || 0,
        clientHeight: document.documentElement?.clientHeight || 0,
      },
    },
    mirror: {
      transform: appMirrorTransformState(),
      rootRect: shareDebugRect(root?.getBoundingClientRect?.()),
      stageRect: shareDebugRect(shareMirrorStage?.getBoundingClientRect?.()),
    },
  });
}

function shareGeometryDebugEntryKey(bucket, entry, index) {
  if (!entry || typeof entry !== 'object') return String(index);
  if (bucket === 'textWraps') return `${entry.key || index}:${entry.tag || ''}`;
  if (bucket === 'terminalCells') return String(entry.session || index);
  if (bucket === 'editors') return String(entry.item || entry.path || index);
  if (bucket === 'tabStrips') return String((entry.index ?? (entry.items || []).join('|')) || index);
  if (bucket === 'slots') return String(entry.slot || index);
  return String(entry.key || entry.id || entry.name || index);
}

function shareGeometryDebugArrayDelta(bucket, hostValue = [], localValue = []) {
  const hostMap = new Map(hostValue.map((entry, index) => [shareGeometryDebugEntryKey(bucket, entry, index), entry]));
  const localMap = new Map(localValue.map((entry, index) => [shareGeometryDebugEntryKey(bucket, entry, index), entry]));
  const hostOnly = Array.from(hostMap.keys()).filter(key => !localMap.has(key));
  const localOnly = Array.from(localMap.keys()).filter(key => !hostMap.has(key));
  const changed = [];
  for (const key of hostMap.keys()) {
    if (!localMap.has(key)) continue;
    const hostEntry = hostMap.get(key);
    const localEntry = localMap.get(key);
    if (stableDigestJson(hostEntry) === stableDigestJson(localEntry)) continue;
    changed.push({
      key,
      host: shareRedactDiagnosticValue(hostEntry),
      local: shareRedactDiagnosticValue(localEntry),
    });
    if (changed.length >= 8) break;
  }
  return {
    match: !hostOnly.length && !localOnly.length && !changed.length,
    hostCount: hostValue.length,
    localCount: localValue.length,
    hostOnly: hostOnly.slice(0, 12),
    localOnly: localOnly.slice(0, 12),
    changed,
  };
}

function shareGeometryDebugObjectDelta(bucket, hostValue = {}, localValue = {}) {
  const keys = Array.from(new Set([...Object.keys(hostValue || {}), ...Object.keys(localValue || {})]));
  const changed = [];
  for (const key of keys) {
    if (stableDigestJson(hostValue?.[key]) === stableDigestJson(localValue?.[key])) continue;
    changed.push({
      key,
      host: shareRedactDiagnosticValue(hostValue?.[key]),
      local: shareRedactDiagnosticValue(localValue?.[key]),
    });
    if (changed.length >= 12) break;
  }
  return {
    match: changed.length === 0,
    bucket,
    changed,
  };
}

function shareGeometryDebugBucketDelta(bucket, hostValue, localValue) {
  if (!bucket) return {match: true};
  if (Array.isArray(hostValue) || Array.isArray(localValue)) {
    return shareGeometryDebugArrayDelta(bucket, Array.isArray(hostValue) ? hostValue : [], Array.isArray(localValue) ? localValue : []);
  }
  if (hostValue && typeof hostValue === 'object' && localValue && typeof localValue === 'object') {
    if (bucket === 'slots') {
      return {
        ...shareGeometryDebugObjectDelta(bucket, hostValue, localValue),
        slotEntries: shareGeometryDebugArrayDelta(bucket, hostValue.slots || [], localValue.slots || []),
      };
    }
    return shareGeometryDebugObjectDelta(bucket, hostValue, localValue);
  }
  return {
    match: stableDigestJson(hostValue) === stableDigestJson(localValue),
    host: shareRedactDiagnosticValue(hostValue),
    local: shareRedactDiagnosticValue(localValue),
  };
}

function shareGeometryDebugDeltas(hostSnapshot = {}, localSnapshot = {}) {
  return Object.fromEntries(['viewport', 'fonts', 'slots', 'tabStrips', 'terminalCells', 'editors', 'textWraps'].map(bucket => [
    bucket,
    shareGeometryDebugBucketDelta(bucket, hostSnapshot[bucket], localSnapshot[bucket]),
  ]));
}

function shareReplayFrameByteLength(value = {}) {
  const text = stableDigestJson(shareRedactDiagnosticValue(value));
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text).length;
  return text.length;
}

function shareReplayFrameLatencyMs(payload = {}) {
  const capturedAt = Number(payload?.capturedAt);
  const createdAt = Number(payload?.createdAt);
  const timestampMs = Number.isFinite(capturedAt) && capturedAt > 0
    ? capturedAt
    : Number.isFinite(createdAt) && createdAt > 0
      ? createdAt * 1000
      : 0;
  return timestampMs > 0 ? Math.max(0, Math.round(Date.now() - timestampMs)) : null;
}

function shareReplayRecordFrameMetrics(kind = '', payload = {}, message = {}) {
  shareReplayLastFrameReceivedAt = Date.now();
  shareReplayLastLatencyMs = shareReplayFrameLatencyMs(payload);
  const bytes = shareReplayFrameByteLength({type: message.type || kind, payload, epoch: message.epoch, sequence: message.sequence});
  if (kind === shareMirrorProtocol.frames.domKeyframe) {
    shareReplayLastKeyframeBytes = bytes;
    const policyVersion = Number(payload?.redaction?.policyVersion);
    if (Number.isFinite(policyVersion)) shareReplayLastRedactionPolicyVersion = policyVersion;
  } else if (kind === shareMirrorProtocol.frames.domDelta) {
    shareReplayLastDeltaBytes = bytes;
  }
}

function shareReplayTerminalPlaceholderDiagnostics() {
  const streamStatusForItem = item => {
    if (!item?.term) return 'not-mounted';
    const readyState = Number(item?.socket?.readyState);
    if (readyState === 0) return 'connecting';
    if (readyState === 1) return item.shareTerminalBytesReceived === true ? 'received-bytes' : 'open-no-bytes';
    if (readyState === 2) return 'closing';
    if (readyState === 3) return 'closed';
    return 'no-socket';
  };
  const entries = Array.from(shareReplayTerminalPlaceholders.values()).map(entry => {
    const item = terminals.get(entry.session);
    const lastByteAt = Math.max(0, Math.round(Number(item?.shareTerminalLastByteAt) || 0));
    const lastResetAt = Math.max(0, Math.round(Number(item?.shareTerminalLastResetAt) || 0));
    return {
      placeholderId: entry.placeholderId,
      session: entry.session,
      rows: entry.rows,
      cols: entry.cols,
      terminalEpoch: entry.terminalEpoch,
      connected: Boolean(document.querySelector?.(`[data-share-terminal-placeholder="${cssEscape(entry.session)}"]`)),
      streamStatus: streamStatusForItem(item),
      socketReadyState: Number.isFinite(Number(item?.socket?.readyState)) ? Number(item.socket.readyState) : null,
      receivedBytes: item?.shareTerminalBytesReceived === true,
      byteCount: Math.max(0, Math.round(Number(item?.shareTerminalByteCount) || 0)),
      lastByteAgeMs: lastByteAt > 0 ? Math.max(0, Math.round(Date.now() - lastByteAt)) : null,
      lastResetAgeMs: lastResetAt > 0 ? Math.max(0, Math.round(Date.now() - lastResetAt)) : null,
      skippedResetCount: Math.max(0, Math.round(Number(item?.shareTerminalSkippedResetCount) || 0)),
    };
  });
  const connected = entries.filter(entry => entry.connected).length;
  return {
    count: entries.length,
    connected,
    disconnected: entries.length - connected,
    healthy: connected === entries.length,
    entries,
  };
}

function shareReplayHealthDiagnostics() {
  const terminalPlaceholders = shareReplayTerminalPlaceholderDiagnostics();
  return shareRedactDiagnosticValue({
    kind: 'share-replay-health',
    at: new Date().toISOString(),
    match: shareReplayShellState.status === 'mirrored' && terminalPlaceholders.healthy,
    status: shareReplayShellState.status || 'idle',
    userStatus: shareReplayUserStatusText(shareReplayShellState.status || ''),
    epoch: shareReplayCurrentEpoch,
    sequence: shareReplayLastSequence,
    keyframeBytes: shareReplayLastKeyframeBytes,
    deltaBytes: shareReplayLastDeltaBytes,
    droppedFrames: shareReplayDroppedFrames,
    staleFrames: shareReplayStaleFrames,
    keyframeRequests: shareReplayKeyframeRequestCount,
    keyframeRequestsSuppressed: shareReplayKeyframeRequestSuppressedCount,
    keyframeRequestBackoffMs: shareReplayKeyframeBackoffMs,
    keyframeRequestInFlight: shareReplayKeyframeInFlight,
    hostKeyframesSuppressed: shareReplayHostKeyframeSuppressedCount,
    hostKeyframePending: Boolean(shareReplayHostKeyframeTimer),
    replayLatencyMs: shareReplayLastLatencyMs,
    lastFrameAgeMs: shareReplayLastFrameReceivedAt ? Math.max(0, Math.round(Date.now() - shareReplayLastFrameReceivedAt)) : null,
    domDigest: shareReplayCurrentDomDigest(),
    redactionPolicyVersion: shareReplayLastRedactionPolicyVersion,
    nodeCount: shareReplayNodeMap.size,
    lastReplayError: shareReplayLastReplayError,
    terminalPlaceholders,
    context: shareDebugContextSnapshot(),
  });
}

function shareDebugTextForClipboard(report = shareDebugReports[shareDebugReports.length - 1] || null) {
  const fallback = shareReplayShellActive ? shareReplayHealthDiagnostics() : {message: 'No share debug diagnostics recorded yet'};
  return JSON.stringify(shareRedactDiagnosticValue(report || fallback), null, 2);
}

function shareDebugProfileUploadEnabled() {
  if (!shareViewMode || !shareToken) return false;
  if (shareBootstrap?.debugProfile === true || shareBootstrap?.debug_profile === true) return true;
  return Boolean(shareViewerCurrentShare()?.debugProfile);
}

function shareDebugProfileUploadKind(kind = '') {
  return String(kind || 'share-debug-profile').replace(/[^A-Za-z0-9_.:-]+/g, '-').slice(0, 120) || 'share-debug-profile';
}

function shareDebugProfileUploadAllowed(kind = '', floorMs = shareDebugProfileUploadMinIntervalMs) {
  const key = shareDebugProfileUploadKind(kind);
  const now = Date.now();
  const last = Math.max(0, Number(shareDebugProfileLastUploadAtByKind.get(key)) || 0);
  const floor = Math.max(0, Number(floorMs) || 0);
  if (last > 0 && now - last < floor) return false;
  shareDebugProfileLastUploadAtByKind.set(key, now);
  return true;
}

function shareDebugProfileUploadPayload(kind = '', detail = {}) {
  return shareRedactDiagnosticValue({
    kind: shareDebugProfileUploadKind(kind),
    at: new Date().toISOString(),
    viewerId: shareViewerId || shareClientId || '',
    shareId: shareBootstrap?.id || shareViewerCurrentShare()?.shortId || '',
    detail,
  });
}

async function shareUploadDebugProfile(kind = 'share-debug-profile', detail = {}, options = {}) {
  if (!shareDebugProfileUploadEnabled()) return false;
  if (options.force !== true && !shareDebugProfileUploadAllowed(kind, options.floorMs ?? shareDebugProfileUploadMinIntervalMs)) return false;
  const payload = shareDebugProfileUploadPayload(kind, detail);
  try {
    await apiFetchJson('/api/share/debug-profile', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    return true;
  } catch (error) {
    if (shareDebugEnabled) console.warn('share debug profile upload failed', error);
    return false;
  }
}

async function copyShareDebugDiagnostics() {
  await copyTextToClipboard(shareDebugTextForClipboard());
}

function exposeShareDebugApi() {
  if (!shareViewMode) return;
  window.yolomuxShareDebug = {
    enabled: shareDebugEnabled,
    uploadEnabled: shareDebugProfileUploadEnabled(),
    latest: shareDebugReports[shareDebugReports.length - 1] || null,
    reports: shareDebugReports.slice(),
    replayHealth: shareReplayHealthDiagnostics,
    text: shareDebugTextForClipboard,
    copy: copyShareDebugDiagnostics,
    upload: shareUploadDebugProfile,
  };
}

function recordShareGeometryDebugReport(payload = {}, local = {}, options = {}) {
  const hostSnapshot = payload.snapshot && typeof payload.snapshot === 'object' ? payload.snapshot : {};
  const localSnapshot = local.snapshot && typeof local.snapshot === 'object' ? local.snapshot : {};
  const diff = options.diff || shareGeometryFirstDifference(payload, local);
  const report = {
    kind: 'share-geometry-drift',
    sequence: ++shareDebugSequence,
    at: new Date().toISOString(),
    phase: options.phase || 'persistent',
    match: options.match === true,
    diff,
    initialDiff: options.initialDiff || '',
    hostDigest: payload.digest || shareGeometryDigestValue(hostSnapshot),
    localDigest: local.digest || shareGeometryDigestValue(localSnapshot),
    context: shareDebugContextSnapshot(),
    delta: shareGeometryDebugBucketDelta(diff, hostSnapshot[diff], localSnapshot[diff]),
    deltas: shareGeometryDebugDeltas(hostSnapshot, localSnapshot),
    snapshots: {
      host: shareRedactDiagnosticValue(hostSnapshot),
      local: shareRedactDiagnosticValue(localSnapshot),
    },
  };
  shareDebugReports = [...shareDebugReports, report].slice(-shareDebugReportLimit);
  exposeShareDebugApi();
  void shareUploadDebugProfile(report.kind, report);
  if (shareDebugEnabled || options.phase === 'persistent') {
    console.warn('share mirror geometry drift', report);
  }
  return report;
}

async function resyncShareViewerUiState() {
  const now = Date.now();
  const lastStartedAt = Math.max(0, Math.round(Number(shareGeometryResyncLastStartedAt) || 0));
  if (!shareViewMode || shareGeometryResyncInFlight) return false;
  if (lastStartedAt > 0 && now - lastStartedAt < shareGeometryResyncMinIntervalMs) return false;
  shareGeometryResyncLastStartedAt = now;
  shareGeometryResyncInFlight = true;
  try {
    const payload = await apiFetchJson('/api/share', {cache: 'no-store'});
    const uiState = payload?.uiState && typeof payload.uiState === 'object' ? payload.uiState : {};
    await applyShareUiState({
      ...uiState,
      layout: payload?.layout || uiState.layout || '',
      tabs: payload?.tabs || uiState.tabs || '',
      viewport: payload?.viewport || uiState.viewport || {},
      appearance: payload?.appearance || uiState.appearance || {},
      finder: payload?.finder || uiState.finder || {},
      terminalDims: payload?.terminalDims || uiState.terminalDims || [],
    });
    return true;
  } catch (error) {
    console.warn('share mirror resync failed', error);
    return false;
  } finally {
    shareGeometryResyncInFlight = false;
  }
}

function updateShareMirrorDigestStatus(result = shareLastGeometryDigestResult) {
  shareLastGeometryDigestResult = result;
  if (!shareViewerBanner) return;
  const node = shareViewerBanner.querySelector('.share-viewer-mirror-status');
  if (!node) return;
  if (shareReplayShellActive) {
    const status = shareReplayShellState.status || 'waiting';
    node.classList.toggle('match', status === 'mirrored');
    node.classList.toggle('mismatch', status === 'error');
    node.textContent = shareReplayUserStatusText(status);
    return;
  }
  if (!result) {
    node.textContent = t('share.mirror.checking');
    node.classList.remove('mismatch', 'match');
    return;
  }
  node.classList.toggle('match', result.match === true);
  node.classList.toggle('mismatch', result.match === false);
  node.textContent = result.match
    ? t('share.mirror.synced')
    : t('share.mirror.drift');
}

function applyShareViewBodyClasses() {
  if (!shareViewMode) return;
  document.body?.classList?.add('share-view-mode');
  document.body?.classList?.toggle('share-view-readonly', !shareWriteMode);
  document.body?.classList?.toggle('share-view-write', shareWriteMode);
}

function shareNextAnimationFrame() {
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

async function waitForShareGeometryResyncIdle(maxFrames = 90) {
  for (let frame = 0; frame < maxFrames; frame += 1) {
    if (!shareGeometryResyncInFlight) return true;
    await shareNextAnimationFrame();
  }
  return !shareGeometryResyncInFlight;
}

function shareGeometryDigestCompare(payload = {}) {
  let local = shareGeometryDigestFrame();
  let match = payload.digest === local.digest;
  let diff = match ? '' : shareGeometryFirstDifference(payload, local);
  if (!match && !diff) match = true;
  if (!match && diff === 'textWraps') {
    applyShareTextWrapMetrics(payload.snapshot?.textWraps || []);
    local = shareGeometryDigestFrame();
    match = payload.digest === local.digest;
    diff = match ? '' : shareGeometryFirstDifference(payload, local);
    if (!match && !diff) match = true;
  }
  return {match, diff, local};
}

function shareGeometryRepairActionForDiff(diff = '') {
  const bucket = String(diff || '');
  if (bucket === 'terminalCells') return shareMirrorProtocol.frames.terminalHostResize;
  if (bucket === 'textWraps') return shareMirrorProtocol.frames.textWrapMetrics;
  if (bucket === shareMirrorProtocol.frames.popupLayer) return shareMirrorProtocol.frames.popupLayer;
  if (bucket === 'domDigest') return shareMirrorProtocol.frames.domKeyframe;
  if (['slots', 'tabStrips', 'editors', 'viewport', 'fonts'].includes(bucket)) return shareMirrorProtocol.frames.uiState;
  return shareMirrorProtocol.frames.uiState;
}

function applyShareTerminalCellsRepair(terminalCells = []) {
  if (!shareViewMode || !Array.isArray(terminalCells)) return false;
  let applied = false;
  for (const entry of terminalCells) {
    if (!entry || typeof entry !== 'object') continue;
    const session = String(entry.session || '').trim();
    const rows = Math.floor(Number(entry.rows) || 0);
    const cols = Math.floor(Number(entry.cols) || 0);
    if (!session || rows <= 0 || cols <= 0) continue;
    updateShareHostTerminalSize(session, rows, cols);
    applied = true;
  }
  return applied;
}

async function repairShareGeometryBucket(payload = {}, diff = '') {
  const action = shareGeometryRepairActionForDiff(diff);
  if (action === shareMirrorProtocol.frames.terminalHostResize) {
    return applyShareTerminalCellsRepair(payload.snapshot?.terminalCells || []);
  }
  if (action === shareMirrorProtocol.frames.textWrapMetrics) {
    applyShareTextWrapMetrics(payload.snapshot?.textWraps || []);
    return true;
  }
  if (action === shareMirrorProtocol.frames.popupLayer || action === shareMirrorProtocol.frames.domKeyframe) {
    return false;
  }
  return resyncShareViewerUiState();
}

async function repairShareGeometryDigest(payload = {}, initialDiff = '') {
  if (shareGeometryRepairInFlight) return;
  shareGeometryRepairInFlight = true;
  try {
    let {match, diff, local} = shareGeometryDigestCompare(payload);
    for (let attempt = 0; attempt < 2 && !match; attempt += 1) {
      if (shareGeometryResyncInFlight) await waitForShareGeometryResyncIdle();
      else await repairShareGeometryBucket(payload, diff);
      await waitForShareGeometryResyncIdle();
      await shareNextAnimationFrame();
      await shareNextAnimationFrame();
      ({match, diff, local} = shareGeometryDigestCompare(payload));
    }
    updateShareMirrorDigestStatus({match, diff});
    if (!match) {
      recordShareGeometryDebugReport(payload, local, {phase: 'persistent', diff: diff || initialDiff, initialDiff, match});
    }
  } finally {
    shareGeometryRepairInFlight = false;
  }
}

function applyShareGeometryDigest(payload = {}) {
  if (!payload || typeof payload !== 'object') return;
  shareLastGeometryDigest = payload;
  if (!shareViewMode) return;
  const {match, diff, local} = shareGeometryDigestCompare(payload);
  if (match) {
    updateShareMirrorDigestStatus({match, diff});
    return;
  }
  if (!shareGeometryRepairInFlight) void repairShareGeometryDigest(payload, diff);
}

function publishShareGeometryDigest() {
  if (shareViewMode || !shareCanPublishUi()) return;
  sharePublish('geometry-digest', shareGeometryDigestFrame());
}

function installShareGeometryDigestLoop() {
  if (shareGeometryDigestTimer) clearInterval(shareGeometryDigestTimer);
  shareGeometryDigestTimer = setInterval(publishShareGeometryDigest, 2000);
}

function applyShareUiMessage(message) {
  if ((!shareViewMode && !shareHasActiveShare()) || !message || message.ch !== 'ui') return;
  if (shareReplayShellActive) {
    applyShareReplayShellMessage(message);
    return;
  }
  if (shareReadOnlyReplayModeEnabled()) return;
  if (message.sender && message.sender === shareClientId) return;
  if (shareDropStaleMirrorFrame(message)) return;
  const payload = message.payload && typeof message.payload === 'object' ? message.payload : {};
  if (message.type === shareMirrorProtocol.frames.inputIntent) {
    applyShareInputIntent(payload);
    return;
  }
  if (message.type === shareMirrorProtocol.frames.domKeyframeRequest) {
    const reason = shareReplayKeyframeReason(payload.reason || message.reason || 'join', 'join');
    sharePublishDomKeyframe(reason);
    return;
  }
  if (message.type === 'ui-state') {
    void applyShareUiState(payload);
    return;
  }
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    if (message.type === 'layout') {
      const next = layoutFromParam(payload.layout || '', payload.tabs || '', {preserveMissingFileExplorer: true});
      if (next) applyLayoutSlots(next, {prune: false, preserveMissingFileExplorer: true});
      return;
    }
    if (message.type === 'active-tab') {
      activatePaneTab(payload.slot, payload.item);
      return;
    }
    if (message.type === 'focus') {
      setFocusedPanelItem(payload.item);
      return;
    }
    if (message.type === 'finder-mode' && payload.session) {
      switchFileExplorerChangesSession(payload.session);
      return;
    }
    if (message.type === 'menu') {
      return;
    }
    if (message.type === 'viewport') {
      applyShareViewportState(payload);
      return;
    }
    if (message.type === 'appearance') {
      applyShareAppearanceState(payload);
      return;
    }
    if (message.type === 'scroll') {
      applyShareScrollState(payload);
      return;
    }
    if (message.type === 'file-version') {
      void applyShareFileVersion(payload);
      return;
    }
    if (message.type === 'geometry-digest') {
      applyShareGeometryDigest(payload);
      return;
    }
    if (message.type === 'share-status') {
      mergeShareStatusPayload(payload);
      return;
    }
    if (message.type === 'popup-layer') {
      applySharePopupLayer(payload, message.sender || '');
      return;
    }
    if (message.type === 'pointer') {
      renderSharePointerGhost({...payload, sender: message.sender || payload.sender || ''});
      return;
    }
    if (message.type === 'host-resize' || message.type === shareMirrorProtocol.frames.terminalHostResize) {
      updateShareHostTerminalSize(payload.session, payload.rows, payload.cols);
    }
  } finally {
    finishRemoteApply();
  }
}

function ensureShareStatusPill() {
  if (shareStatusPill) return shareStatusPill;
  shareStatusPill = document.createElement('button');
  shareStatusPill.id = 'shareStatusPill';
  shareStatusPill.className = 'share-status-pill';
  shareStatusPill.type = 'button';
  shareStatusPill.hidden = true;
  shareStatusPill.onclick = () => showShareModal();
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

function renderShareCreateView(errorText = '') {
  const {body} = openShareModalChrome(t('share.title'));
  if (!body) return;
  body.innerHTML = shareCreateFormHtml(errorText);
  bindShareCreateForm(body);
  scheduleSharePopupLayerPublish();
}

function shareCreateFormHtml(errorText = '') {
  const sharedSessions = shareSessionsFromLayout();
  if (!sharedSessions.length && !currentSessionActionTarget()) {
    return `<div class="share-modal-message">${esc(t('share.noSession'))}</div>`;
  }
  const ttlDefault = Math.max(60, Math.min(28800, Math.round(Number(shareDefaultTtlSeconds) || 600)));
  const ttlMinutesDefault = shareTtlMinutesFromSeconds(ttlDefault);
  const maxViewersDefault = Math.max(1, Math.min(300, Math.round(Number(shareDefaultMaxViewers) || 2)));
  const readOnlyChecked = shareDefaultReadOnly || !shareTlsAvailable();
  const defaultScheme = shareDefaultSchemeForForm(readOnlyChecked);
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
        <button type="button" class="path-copy-button share-url-copy-button" data-share-copy data-share-secret title="${esc(t('share.copy'))}" aria-label="${esc(t('share.copy'))}"></button>
      </span>
    </label>
    <div class="share-result-meta">
      <span data-share-countdown>${esc(t('share.resultExpires', {time: shareTimeLeftText(share)}))}</span>
      <span data-share-viewer-count>${esc(t('share.resultViewers', {viewers: share.viewers, max: share.maxViewers || shareDefaultMaxViewers}))}</span>
      <span>${esc(mode)}</span>
      <span>${esc(scheme)}</span>
      ${share.debugProfile ? `<span title="${esc(t('share.debugProfileHelp'))}">${esc(t('share.debugProfileOn'))}</span>` : ''}
      <button type="button" class="share-extend-button" data-share-extend>${esc(t('share.extendTenMinutes'))}</button>
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

function renderShareManageView(errorText = '') {
  const {body} = openShareModalChrome(t('share.manageTitle'));
  if (!body) return;
  if (!shareHasActiveShare()) {
    renderShareCreateView(errorText);
    return;
  }
  const list = activeShares.map(shareEntryHtml).join('');
  body.innerHTML = `<div class="share-result">
    <div class="share-create-panel">
      <div class="share-section-title">${esc(t('share.newShare'))}</div>
      ${shareCreateFormHtml(errorText)}
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

async function createShareFromForm(form) {
  const submit = form.querySelector('button[type="submit"]');
  const error = form.querySelector('.share-error');
  if (submit) submit.disabled = true;
  if (error) {
    error.hidden = true;
    error.textContent = '';
  }
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
    if (error) {
      error.hidden = false;
      error.textContent = err?.message || String(err);
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
  openShareModalChrome(t('share.title'));
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
    mode: share?.mode === 'rw' ? t('share.mode.write') : t('share.mode.readOnly'),
    time: shareTimeLeftText(share),
  });
  const fit = document.createElement('button');
  fit.type = 'button';
  fit.className = 'share-view-fit-toggle';
  fit.dataset.shareViewerControl = 'fit';
  fit.textContent = shareViewFit === 'cover' ? t('share.fit.cover') : t('share.fit.contain');
  fit.title = shareViewFit === 'cover' ? t('share.fit.switchToContain') : t('share.fit.switchToCover');
  fit.setAttribute('aria-label', fit.title);
  fit.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    setShareViewFit(shareViewFit === 'cover' ? 'contain' : 'cover');
  });
  const mirror = document.createElement('span');
  mirror.className = 'share-viewer-mirror-status';
  mirror.setAttribute('aria-live', 'polite');
  const children = [text, mirror, fit];
  if (shareDebugEnabled) {
    const debug = document.createElement('button');
    debug.type = 'button';
    debug.className = 'share-view-fit-toggle share-debug-copy';
    debug.dataset.shareViewerControl = 'debug';
    debug.textContent = 'debug';
    debug.title = 'Copy share mirror diagnostics';
    debug.setAttribute('aria-label', debug.title);
    debug.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyShareDebugDiagnostics().catch(error => console.warn('share debug copy failed', error));
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
  if (shareStatusTimer) clearInterval(shareStatusTimer);
  shareStatusTimer = setInterval(() => {
    if (redirectExpiredShareViewerToLogin()) return;
    renderShareStatusPill();
    updateShareViewerBanner();
    renderShareCountdowns();
    if (shareViewMode && Date.now() - shareStatusLastRefreshAt >= shareViewerStatusBackupRefreshMs) {
      refreshShareViewerStatus({silent: true});
    } else if (!readOnlyMode && Date.now() - shareStatusLastRefreshAt >= shareHostStatusBackupRefreshMs) {
      refreshActiveShare({silent: true});
    }
  }, 1000);
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  modal.classList.remove('about-open', 'share-open');
  title.textContent = t('transcript.tailTitle', {session: sessionLabel(session)});
  body.innerHTML = '';
  body.textContent = t('common.loading');
  modal.classList.add('open');
  const payload = await apiFetchJson(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
  scheduleSharePopupLayerPublish();
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

function restoreShareReadonlyScrollTarget(target) {
  const descriptor = shareScrollTargetForElement(target);
  if (!descriptor?.target) return false;
  const state = shareLastAppliedScrollByTarget.get(descriptor.target);
  if (!state) return false;
  const top = Math.max(0, Math.round(Number(state.top || 0)));
  const left = Math.max(0, Math.round(Number(state.left || 0)));
  if (descriptor.kind === 'terminal' && typeof descriptor.term?.scrollToLine === 'function') {
    descriptor.term.scrollToLine(top);
    return true;
  }
  if (!descriptor.element) return false;
  descriptor.element.scrollTop = top;
  descriptor.element.scrollLeft = left;
  return true;
}

function restoreShareScrollTargetByKey(target) {
  const cleanTarget = String(target || '');
  if (!cleanTarget) return false;
  const state = shareLastAppliedScrollByTarget.get(cleanTarget);
  if (!state) return false;
  const payload = {
    ...(shareLastAppliedScrollPayloadByTarget.get(cleanTarget) || {}),
    target: cleanTarget,
    top: Math.max(0, Math.round(Number(state.top || 0))),
    left: Math.max(0, Math.round(Number(state.left || 0))),
  };
  const descriptor = shareScrollElementForPayload(payload);
  if (!descriptor) return false;
  if (descriptor.kind === 'terminal' && typeof descriptor.term?.scrollToLine === 'function') {
    descriptor.term.scrollToLine(payload.top);
    return true;
  }
  if (!descriptor.element) return false;
  descriptor.element.scrollTop = payload.top;
  descriptor.element.scrollLeft = payload.left;
  return true;
}

function scheduleShareScrollRestoreByKey(target, options = {}) {
  const cleanTarget = String(target || '');
  if (!shareViewMode || !cleanTarget) return false;
  const frames = Math.max(1, Math.min(8, Math.round(Number(options.frames || 4))));
  const state = {remaining: frames};
  shareScrollRestoreFrameTimers.set(cleanTarget, state);
  const run = () => {
    if (shareScrollRestoreFrameTimers.get(cleanTarget) !== state) return;
    const previous = applyingShareRemoteScroll;
    applyingShareRemoteScroll = true;
    try {
      restoreShareScrollTargetByKey(cleanTarget);
    } finally {
      applyingShareRemoteScroll = previous;
    }
    state.remaining -= 1;
    if (state.remaining > 0) {
      requestAnimationFrame(run);
    } else if (shareScrollRestoreFrameTimers.get(cleanTarget) === state) {
      shareScrollRestoreFrameTimers.delete(cleanTarget);
    }
  };
  requestAnimationFrame(run);
  return true;
}

function restoreShareScrollTargetsByPrefix(prefix) {
  const cleanPrefix = String(prefix || '');
  if (!cleanPrefix) return;
  for (const target of shareLastAppliedScrollByTarget.keys()) {
    if (String(target).startsWith(cleanPrefix)) scheduleShareScrollRestoreByKey(target);
  }
}

function blockShareReadonlyInteraction(event) {
  if (!shareSemanticReadOnlyMirrorEnabled()) return;
  if (event.target?.closest?.('[data-share-viewer-control]')) return;
  if (event.type === 'scroll') {
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
    removeSessionFromLayout(fileExplorerItemId);
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
  modal.classList.remove('open', 'about-open', 'share-open');
  scheduleSharePopupLayerPublish({immediate: true});
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
installShareReadonlyInteractionBlocker();
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

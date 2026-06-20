
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
  const body = rows.map(row => {
    const sessionToggle = infoSessionDrawerToggleHtml(row);
    const rowHtml = `<div class="info-row${row.current ? ' current' : ''}">
    <div class="info-cell info-session-cell" title="${esc(row.session)}">${sessionToggle}${esc(row.session)}</div>
    <div class="info-cell" title="${esc(row.pathTitle || row.path)}">${esc(row.pathLabel || compactHomePath(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${infoBranchCellHtml(row)}</div>
    <div class="info-cell" title="${esc(row.prTitle)}">${infoPrCellHtml(row)}</div>
    <div class="info-cell" title="${esc(row.linearTitle)}">${infoLinearCellHtml(row)}</div>
    <div class="info-cell" title="${esc(row.desc)}">${esc(row.desc)}</div>
    <div class="info-cell" title="${esc(row.updatedTitle || row.updated)}">${esc(row.updatedText || row.updated)}</div>
  </div>`;
    return rowHtml + (row.session && infoSessionDrawerOpen.has(row.session) ? cachedInfoSessionDrawerHtml(row.session) : '');
  }).join('');
  node.innerHTML = header + body;
  node.querySelectorAll('[data-info-sort]').forEach(button => {
    button.addEventListener('click', () => {
      setInfoBranchSort(button.dataset.infoSort);
      renderInfoPanel();
    });
  });
  node.querySelectorAll('[data-info-session-drawer]').forEach(button => {
    button.addEventListener('click', () => toggleInfoSessionDrawer(button.dataset.infoSessionDrawer || ''));
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

const infoSessionDrawerOpen = new Set();
const infoSessionDrawerHtmlCache = new Map();

function clearInfoSessionDrawerCache() {
  infoSessionDrawerHtmlCache.clear();
}

function infoSessionDrawerToggleHtml(row) {
  const session = String(row?.session || '');
  if (!session) return '';
  const expanded = infoSessionDrawerOpen.has(session);
  const label = expanded ? 'hide session info' : 'show session info';
  return `<button type="button" class="info-session-drawer-toggle" data-info-session-drawer="${esc(session)}" aria-expanded="${expanded ? 'true' : 'false'}" aria-label="${esc(label)}" title="${esc(label)}"></button>`;
}

function infoSessionDrawerCacheKey(session) {
  return [
    session,
    activitySummaryPayload?.generated_at || '',
    transcriptMeta?.server_version || '',
    infoSessionFileLookbackHours,
  ].map(value => String(value ?? '')).join('|');
}

function cachedInfoSessionDrawerHtml(session) {
  const key = infoSessionDrawerCacheKey(session);
  const cached = infoSessionDrawerHtmlCache.get(key);
  if (cached) return cached;
  const html = infoSessionDrawerHtml(session);
  infoSessionDrawerHtmlCache.set(key, html);
  return html;
}

function toggleInfoSessionDrawer(session) {
  const key = String(session || '');
  if (!key) return false;
  if (infoSessionDrawerOpen.has(key)) {
    infoSessionDrawerOpen.delete(key);
  } else {
    infoSessionDrawerOpen.add(key);
    if (!activitySummaryPayload?.session_info?.[key] && !activitySummaryRefreshing && typeof refreshActivitySummary === 'function') {
      refreshActivitySummary({force: true});
    }
  }
  renderInfoPanel();
  scheduleShareUiStatePublish();
  return infoSessionDrawerOpen.has(key);
}

function infoSessionDrawerData(session) {
  const fromActivity = activitySummaryPayload?.session_info?.[session];
  if (fromActivity && typeof fromActivity === 'object') return fromActivity;
  const info = transcriptMeta?.sessions?.[session] || {};
  const project = info.project || {};
  return {session, project, git: project.git || null, pull_request: project.pull_request || null, linear: project.linear || [], latest_summary: '', recent_events: []};
}

function infoSessionCountText(label, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  return `${label} ${Math.max(0, Math.trunc(number))}`;
}

function infoSessionDrawerPairsHtml(items) {
  const rows = items.map(item => {
    const value = String(item?.value ?? '').trim();
    const html = String(item?.html || '').trim();
    return item && (value || html) ? {...item, value, html} : null;
  }).filter(Boolean).map(item => `
    <div class="info-session-drawer-field">
      <span class="info-session-drawer-label">${esc(item.label)}</span>
      <span class="info-session-drawer-value">${item.html || esc(item.value)}</span>
    </div>`);
  return rows.join('');
}

function infoSessionDrawerEventsHtml(events) {
  const rows = Array.isArray(events) ? events.slice(0, 5) : [];
  if (!rows.length) return '<div class="info-session-drawer-empty">No recent events</div>';
  return `<ul class="info-session-drawer-events">${rows.map(event => {
    const text = [event?.time || '', event?.type || '', event?.message || ''].filter(Boolean).join(' · ');
    return `<li>${esc(text)}</li>`;
  }).join('')}</ul>`;
}

function infoSessionDrawerLinearHtml(linear) {
  const items = Array.isArray(linear) ? linear : [];
  if (!items.length) return '';
  return items.map(issue => linearIssueHtml(issue)).filter(Boolean).join(' ');
}

function infoSessionDrawerPrHtml(pr) {
  if (!pr || typeof pr !== 'object') return '';
  if (pr.number) return pullRequestColumnLinkHtml(pr);
  return pr.title || pr.description || '';
}

function infoSessionDrawerHtml(session) {
  const data = infoSessionDrawerData(session);
  const git = data.git && typeof data.git === 'object' ? data.git : {};
  const pr = data.pull_request && typeof data.pull_request === 'object' ? data.pull_request : null;
  const ci = data.ci && typeof data.ci === 'object' ? data.ci : (pr?.checks && typeof pr.checks === 'object' ? pr.checks : null);
  const linearHtml = infoSessionDrawerLinearHtml(data.linear);
  const prHtml = infoSessionDrawerPrHtml(pr);
  const counts = [
    infoSessionCountText('dirty', git.dirty_count),
    infoSessionCountText('ahead', git.ahead),
    infoSessionCountText('behind', git.behind),
  ].filter(Boolean).join(' · ');
  const fields = infoSessionDrawerPairsHtml([
    {label: 'Full path', value: data.path || git.root || git.cwd || data.cwd || ''},
    {label: 'Branch', value: git.branch || ''},
    {label: 'Git', value: counts},
    {label: 'PR', value: prHtml ? 'pr' : '', html: prHtml},
    {label: 'CI', value: ci?.status_label || ci?.state || ci?.conclusion || ''},
    {label: 'Issues', value: linearHtml ? 'issues' : '', html: linearHtml},
  ]);
  const summary = String(data.latest_summary || '').trim();
  const content = activitySummaryPayload?.session_info?.[session]
    ? `
      <div class="info-session-drawer-grid">${fields || '<div class="info-session-drawer-empty">No project metadata</div>'}</div>
      ${summary ? `<div class="info-session-drawer-summary"><span>Latest summary</span><p>${esc(summary)}</p></div>` : ''}
      <div class="info-session-drawer-section"><span>Recent events</span>${infoSessionDrawerEventsHtml(data.recent_events)}</div>`
    : `<div class="info-session-drawer-empty">${esc(activitySummaryRefreshing ? 'Loading session info...' : 'Open drawer data by refreshing YO!info')}</div>`;
  return `<div class="info-row info-session-drawer-row" data-info-session-drawer-row="${esc(session)}">
    <div class="info-session-drawer">${content}</div>
  </div>`;
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

function infoGitRoot(git) {
  return String(git?.root || git?.cwd || '');
}

function infoBranchSourcesForSession(session, info) {
  const project = info?.project || {};
  const primaryGit = project.git;
  const primaryRoot = infoGitRoot(primaryGit);
  const sources = [];
  const seenRoots = new Set();
  const addSource = (git, primary) => {
    const root = infoGitRoot(git);
    const branches = git?.other_branches?.branches;
    if (!root || !Array.isArray(branches) || !branches.length || seenRoots.has(root)) return;
    seenRoots.add(root);
    sources.push({session, info, project, git, primary: primary === true});
  };
  addSource(primaryGit, true);
  for (const repo of Array.isArray(project.repos) ? project.repos : []) {
    addSource(repo, Boolean(primaryRoot && infoGitRoot(repo) === primaryRoot));
  }
  return sources;
}

function infoBranchOwnedBySource(git, branch) {
  const branchName = String(branch?.name || '');
  const currentBranch = String(git?.branch || '');
  return branch?.current === true && Boolean(branchName) && (!currentBranch || currentBranch === branchName);
}

function infoBranchRowForSource(source, branch, ownsSession) {
  const {session, info, project, git, primary} = source;
  const useCurrentProjectMetadata = ownsSession && primary;
  const currentPr = useCurrentProjectMetadata ? displayPullRequest(info) : null;
  const currentLinear = useCurrentProjectMetadata ? project.linear || [] : [];
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
  return {
    session: ownsSession ? session : '',
    path: infoGitRoot(git),
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
    current: ownsSession,
    sourcePrimary: primary,
  };
}

function preferInfoBranchRow(existing, next) {
  if (!existing) return next;
  if (next.session && !existing.session) return next;
  if (next.session && next.sourcePrimary && !existing.sourcePrimary) return next;
  return existing;
}

function rawInfoBranchRows() {
  const rowsByKey = new Map();
  const infoSessions = Array.isArray(transcriptMeta?.session_order) ? transcriptMeta.session_order : sessions;
  for (const session of infoSessions) {
    const info = transcriptMeta.sessions?.[session];
    for (const source of infoBranchSourcesForSession(session, info)) {
      for (const branch of source.git?.other_branches?.branches || []) {
        const key = `${infoGitRoot(source.git)}\n${branch.name || ''}`;
        const row = infoBranchRowForSource(source, branch, infoBranchOwnedBySource(source.git, branch));
        rowsByKey.set(key, preferInfoBranchRow(rowsByKey.get(key), row));
      }
    }
  }
  return [...rowsByKey.values()];
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
    // Image-bearing paste: ALWAYS claim it (preventDefault + stopPropagation) so the raw image can never
    // reach the terminal-backed agent as a rich [Image #N] attachment (the mixed text+attachment bug).
    // Then upload ALL pasted images and insert only their textual uploaded-path references.
    event.preventDefault();
    event.stopPropagation();
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

const terminalTmuxPrefixPendingBySession = new Map();

function terminalTmuxPrefixWindowShortcut(key) {
  const value = String(key || '');
  if (value === 'n') return {previewKey: 'n', label: 'next tmux window'};
  if (value === 'p') return {previewKey: 'p', label: 'previous tmux window'};
  if (/^[0-9]$/.test(value)) return {previewKey: {windowIndex: value}, label: `tmux window ${value}`};
  return null;
}

function mirrorTerminalTmuxWindowSwitch(session, shortcut) {
  if (!shortcut) return false;
  previewTmuxWindowLabel(session, shortcut.previewKey);
  statusOk(`${esc(shortcut.label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  setTimeout(() => refreshTranscripts({force: true}), 250);
  return true;
}

function observeTerminalTmuxPrefixWindowSwitches(session, data) {
  const text = String(data || '');
  if (!text) return false;
  let pending = terminalTmuxPrefixPendingBySession.get(session) === true;
  let mirrored = false;
  for (const char of text) {
    if (pending) {
      mirrored = mirrorTerminalTmuxWindowSwitch(session, terminalTmuxPrefixWindowShortcut(char)) || mirrored;
      pending = false;
      continue;
    }
    if (char === '\x02') pending = true;
  }
  if (pending) terminalTmuxPrefixPendingBySession.set(session, true);
  else terminalTmuxPrefixPendingBySession.delete(session);
  return mirrored;
}

function handleTerminalData(session, data) {
  if (readOnlyMode && !shareWriteMode) return false;
  const filtered = stripTerminalQueryResponses(data);
  if (!filtered) return false;
  if (shareReplayShellActive && shareWriteMode) {
    shareSendTerminalInputIntent(session, filtered);
    return true;
  }
  const current = terminals.get(session);
  const socket = current?.socket;
  if (socket?.readyState !== WebSocket.OPEN) return false;
  observeTerminalTmuxPrefixWindowSwitches(session, filtered);
  socket.send(JSON.stringify({type: 'input', data: filtered}));
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
    scheduleTerminalBlankScreenRefresh(session);
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
      scheduleTerminalBlankScreenRefresh(session);
      if (!shareViewMode) scheduleRemoteResize(session, shareRemoteResizeAfterSocketOpenMs);
    }
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (terminals.get(session) !== item || !item.term) return;
    try {
      if (shareViewMode) {
        handleShareViewSocketMessage(session, item, event.data);
      } else if (event.data instanceof ArrayBuffer) {
        item.term.write(new Uint8Array(event.data));
      } else {
        item.term.write(String(event.data));
      }
      scheduleTerminalBlankScreenRefresh(session);
    } catch (_) {
      if (terminals.get(session) === item) closeTerminalItem(session, item);
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
    blankScreenRefreshTimer: 0,
    blankScreenRefreshAttempts: 0,
  };
  terminals.set(session, item);
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
  if (selfUpdateOwnsServerVersion(serverVersion)) return;
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
  clearInfoSessionDrawerCache();
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
  panel?.classList.toggle('needs-input-pane', state.key === 'needs-input' && state.attention === true);
  panel?.classList.toggle('needs-exec-pane', state.key === 'needs-approval' && state.attention === true);
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
    }
  });
  window.addEventListener('online', () => {
    scheduleReconnectResync('online');
    resyncVisibleTerminalRemoteSizes('online');
  });
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

function applyTmuxSignalsPayload(payload = {}) {
  const data = payload?.data && typeof payload.data === 'object' ? payload.data : payload;
  if (!data || typeof data !== 'object') return;
  tmuxSignalState = data;
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
  if (type === 'tmux_signals_changed') {
    applyTmuxSignalsPayload(payload);
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
    if (typeof loadYoagentJobs === 'function') {
      loadYoagentJobs({force: true, silent: true, render: infoPanelSubTab === 'yoagent', scrollBottom: false}).catch(error => console.warn('YO!agent jobs refresh failed', error));
    }
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
  for (const type of ['settings_changed', 'auto_approve_changed', 'tmux_signals_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta']) {
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
notifyToggle.onclick = toggleNotifications;
document.getElementById('closeModal').onclick = () => {
  const modal = document.getElementById('modal');
  modal.classList.remove('open', 'about-open', 'share-open');
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
  clearPromptAttentionForSession(node.dataset.session || '');
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

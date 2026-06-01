
function windowStepButtonLabel(dir) {
  return dir === 'prev' ? 'previous' : 'next';
}

function windowStepButtonHtml(session, dir, visible, disabled) {
  if (!visible) return '';
  const label = windowStepButtonLabel(dir);
  const glyph = dir === 'prev' ? '&lt;' : '&gt;';
  if (disabled) {
    return `<button type="button" class="tab tmux-window-step" data-window-step-button="${dir}" disabled title="unavailable for ${esc(itemLabel(session))}">${glyph}</button>`;
  }
  if (readOnlyMode) {
    return `<button type="button" class="tab tmux-window-step" data-window-step-button="${dir}" disabled title="${label} tmux window requires admin access">${glyph}</button>`;
  }
  return `<button class="tab tmux-window-step" data-window-step-button="${dir}" data-window-dir="${dir}" data-window-session="${esc(session)}" title="${label} tmux window">${glyph}</button>`;
}

function paneFrameControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => ` type="button" disabled title="unavailable for ${esc(unavailableLabel)}" aria-label="${esc(label)}"`;
  const controls = [];
  const includeActions = options.actions ?? isTmuxSession(session);
  const includeMinimize = options.minimize !== false;
  const includeExpand = options.expand !== false;
  if (includeActions) {
    controls.push(disabled
      ? `<button class="tab pane-actions" ${disabledAttrs('Actions')}><span class="pane-actions-dots" aria-hidden="true">...</span></button>`
      : `<button type="button" class="tab pane-actions" data-pane-actions="${esc(session)}" title="session actions" aria-label="Session actions"><span class="pane-actions-dots" aria-hidden="true">...</span></button>`);
  }
  if (includeMinimize) {
    controls.push(disabled
      ? `<button class="tab pane-minimize ${platformWindowControlClass('minimize')}" ${disabledAttrs('Minimize pane')}></button>`
      : `<button type="button" class="tab pane-minimize ${platformWindowControlClass('minimize')}" data-pane-minimize="${esc(session)}" title="minimize pane" aria-label="Minimize pane"></button>`);
  }
  if (includeExpand) {
    const expandAttrs = `${canPaneExpand(session) ? '' : ' hidden'} type="button" data-pane-expand="${esc(session)}" title="expand pane" aria-label="Expand pane"`;
    controls.push(disabled
      ? `<button class="tab pane-expand ${platformWindowControlClass('zoom')}" ${disabledAttrs('Expand pane')}></button>`
      : `<button class="tab pane-expand ${platformWindowControlClass('zoom')}" ${expandAttrs}></button>`);
  }
  if (options.close) {
    const closeLabel = options.closeLabel || 'Close pane tab';
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
    if (readOnlyMode && name === 'summary') return readonlyAttrs('AI summary');
    const labelAttrs = label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : '';
    return ` type="button" data-tab="${esc(session)}" data-tab-name="${name}"${labelAttrs}`;
  };
  const infoAttrs = disabled ? disabledAttrs(infoTabLabel) : ` type="button" data-detail-toggle="${esc(session)}" title="${esc(infoTabLabel)}" aria-label="${esc(infoTabLabel)}"`;
  const info = transcriptMeta.sessions?.[session];
  const terminalTitle = terminalTabTitle(session, info);
  const terminalAttrs = disabled ? disabledAttrs(terminalTitle) : `${tabAttrs('terminal')} title="${esc(terminalTitle)}" aria-label="${esc(terminalTitle)}"`;
  const terminalLabel = disabled ? 'Term' : terminalTabLabel(session, info);
  const steps = disabled ? {prev: false, next: false} : windowStepVisibility(info?.panes);
  const isFiles = isFileExplorerItem(session);
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
    : paneFrameControlsHtml(session, {disabled, actions: isTmuxSession(session), close: false});
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          ${windowStepButtonHtml(session, 'prev', steps.prev, disabled)}
          <button class="tab active terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>
          ${windowStepButtonHtml(session, 'next', steps.next, disabled)}
          <button type="button" class="tab panel-tab-overflow" data-panel-tab-overflow="${esc(session)}" title="Transcript, AI summary, and event log" aria-label="Transcript, AI summary, and event log"><span class="pane-actions-dots" aria-hidden="true">...</span></button>
          <button class="tab panel-detail-toggle active" ${infoAttrs}>Info</button>
          ${frameHtml}
        </div>`;
}

function showPanelTabOverflowMenu(session, x, y) {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  const active = activeSessions.includes(session);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu session-context-menu panel-tab-overflow-menu';
  const disabledDetail = active ? '' : 'Open the tab in a pane first';
  const add = (label, tabName, options = {}) => {
    appendContextMenuButton(menu, label, () => {
      if (active) activateTab(session, tabName, {userInitiated: true});
    }, closeSessionContextMenu, {
      disabled: !active || options.disabled,
      checked: active && panelActiveTabName(session) === tabName,
      title: options.disabled ? options.disabledTitle : disabledDetail,
    });
  };
  add('Transcript', 'transcript');
  add('AI summary', 'summary', {
    disabled: readOnlyMode,
    disabledTitle: 'AI summary requires admin access',
  });
  add('Event log', 'events');
  sessionContextMenu.open(menu, x, y);
}

function virtualPanelControlsHtml(session, label) {
  const safeLabel = label || itemLabel(session);
  return `<div class="tabs virtual-panel-controls" role="tablist">
          <button class="tab active terminal-tab" type="button" title="${esc(safeLabel)}" aria-label="${esc(safeLabel)}">${esc(safeLabel)}</button>
          ${paneFrameControlsHtml(session, {actions: false, close: false})}
        </div>`;
}

function panelActiveTabName(session) {
  const activePane = document.getElementById(`panel-${session}`)?.querySelector('.tab-pane.active');
  const id = activePane?.id || '';
  if (id === `transcript-pane-${session}`) return 'transcript';
  if (id === `summary-pane-${session}`) return 'summary';
  if (id === `events-pane-${session}`) return 'events';
  return 'terminal';
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = `panel-${session}`;
  panel.innerHTML = `
      <div class="panel-head">
        ${panelControlsHtml(session)}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-popover-zone">
          <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(sessionState(session, transcriptMeta.sessions?.[session]))}</div>
          <div id="meta-${session}" class="meta">finding branch...</div>
          ${sessionPopoverHtml(session, transcriptMeta.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMeta.sessions?.[session]))}
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(session)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div id="terminal-pane-${session}" class="tab-pane active panel-overlay-root">
        <div id="term-${session}" class="terminal"></div>
        <div id="panel-toasts-${session}" class="panel-toast-stack">
          <div id="upload-${session}" class="upload-result toast" hidden></div>
        </div>
      </div>
      <div id="transcript-pane-${session}" class="tab-pane">
        <div class="transcript">
          <div class="transcript-head">Transcript</div>
          <div id="transcript-path-${session}" class="transcript-path-row">finding transcript...</div>
          <div id="transcript-${session}" class="transcript-preview">finding transcript...</div>
        </div>
      </div>
      <div id="summary-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">AI summary</div>
          <div id="summary-context-${session}" class="summary-context">loading session context...</div>
          <pre id="summary-${session}" class="summary-preview">click AI summary to generate a Codex summary of the last hour</pre>
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

function renderInfoPanel() {
  const node = document.getElementById('info-content');
  if (!node) return;
  const rows = infoBranchRows();
  if (!rows.length) {
    node.innerHTML = '<div class="info-empty">No branch metadata loaded yet.</div>';
    return;
  }
  const headerCell = (key, label) => {
    const active = infoBranchSort.key === key;
    const dirLabel = active ? (infoBranchSort.dir === 'asc' ? 'ascending' : 'descending') : 'unsorted';
    const marker = active ? (infoBranchSort.dir === 'asc' ? 'A-Z' : 'Z-A') : '';
    return `<button type="button" class="info-sort-button${active ? ' active' : ''}" data-info-sort="${esc(key)}" aria-label="sort ${esc(label)} ${esc(dirLabel)}"><span>${esc(label)}</span>${marker ? `<span class="info-sort-marker">${marker}</span>` : ''}</button>`;
  };
  const header = `<div class="info-row header">
    <div class="info-cell">${headerCell('session', 'session-name')}</div>
    <div class="info-cell">${headerCell('path', 'path')}</div>
    <div class="info-cell">${headerCell('branch', 'branch')}</div>
    <div class="info-cell">${headerCell('pr', 'PR')}</div>
    <div class="info-cell">${headerCell('linear', 'Linear')}</div>
    <div class="info-cell">${headerCell('desc', 'desc')}</div>
    <div class="info-cell">${headerCell('updated', 'updated')}</div>
  </div>`;
  const body = rows.map(row => `<div class="info-row${row.current ? ' current' : ''}">
    <div class="info-cell" title="${esc(row.session)}">${esc(row.session)}</div>
    <div class="info-cell" title="${esc(row.path)}">${esc(pathBasename(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${row.branchHtml}</div>
    <div class="info-cell" title="${esc(row.prTitle)}">${row.prHtml}</div>
    <div class="info-cell" title="${esc(row.linearTitle)}">${row.linearHtml}</div>
    <div class="info-cell" title="${esc(row.desc)}">${esc(row.desc)}</div>
    <div class="info-cell" title="${esc(row.updated)}">${esc(row.updated)}</div>
  </div>`).join('');
  node.innerHTML = header + body;
  node.querySelectorAll('[data-info-sort]').forEach(button => {
    button.addEventListener('click', () => {
      setInfoBranchSort(button.dataset.infoSort);
      renderInfoPanel();
    });
  });
}

function renderYosupPanel() {
  const node = document.getElementById('yosup-content');
  if (!node) return;
  node.innerHTML = globalActivitySummaryHtml();
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
  panel.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click', () => {
      const currentName = button.dataset.tabName;
      const nextName = currentName !== 'terminal' && button.classList.contains('active') ? 'terminal' : currentName;
      activateTab(button.dataset.tab, nextName, {userInitiated: true});
    });
  });
  panel.querySelectorAll('[data-window-dir]').forEach(button => {
    button.addEventListener('click', handleWindowStepButtonClick);
  });
  panel.querySelector('[data-panel-tab-overflow]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const button = event.currentTarget;
    const rect = button.getBoundingClientRect();
    showPanelTabOverflowMenu(button.dataset.panelTabOverflow || session, rect.left, rect.bottom + 4);
  });
  panel.querySelector('[data-pane-close]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    removePaneFromLayout(event.currentTarget.dataset.paneClose);
  });
  panel.querySelector('[data-pane-minimize]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    minimizePaneFromLayout(event.currentTarget.dataset.paneMinimize);
  });
  panel.querySelector('[data-pane-expand]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    expandPaneFromLayout(event.currentTarget.dataset.paneExpand);
  });
  panel.querySelector('[data-pane-actions]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const button = event.currentTarget;
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
  panel.querySelector('[data-context]')?.addEventListener('click', () => showContext(session));
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
      .catch(error => { statusEl.innerHTML = `<span class="err">copy failed: ${esc(error)}</span>`; });
  });
  panel.querySelector('.meta')?.addEventListener('click', event => event.stopPropagation());
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
    panel.classList.add('file-drag-over');
  });
  panel.addEventListener('dragover', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    panel.classList.add('file-drag-over');
  });
  panel.addEventListener('dragleave', event => {
    if (!hasFileDrag(event)) return;
    if (panel.contains(event.relatedTarget)) return;
    panel.classList.remove('file-drag-over');
  });
  panel.addEventListener('drop', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.remove('file-drag-over');
    uploadFiles(session, event.dataTransfer?.files || []);
  });
}

function insertFileDragPayloadIntoTerminal(session, payload) {
  const references = terminalFileReferences(session, payload);
  if (!references.length) return;
  const inserted = insertIntoTerminal(session, `${references.map(shellQuote).join(' ')} `);
  const label = references.length === 1 ? references[0] : `${references.length} paths`;
  statusEl.innerHTML = inserted
    ? `<span class="ok">inserted ${esc(label)} into ${esc(sessionLabel(session))}</span>`
    : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
}

function terminalPathDropPayload(event) {
  const payload = fileDragPayload(event);
  if (!payload?.path) return null;
  return payload.kind === 'dir' ? payload : null;
}

function installFilePathDropTarget(session, target) {
  if (readOnlyMode) return;
  target.addEventListener('dragover', event => {
    const payload = terminalPathDropPayload(event);
    if (!payload) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    const intent = dropIntentForEvent(event);
    if (intent?.targetSlot) showDropPreview(intent);
    target.classList.add('path-drag-over');
  });
  target.addEventListener('dragleave', event => {
    if (target.contains(event.relatedTarget)) return;
    target.classList.remove('path-drag-over');
    clearDropPreview();
  });
  target.addEventListener('drop', event => {
    const payload = terminalPathDropPayload(event);
    if (!payload?.path) return;
    event.preventDefault();
    event.stopPropagation();
    target.classList.remove('path-drag-over');
    const intent = dropIntentForEvent(event);
    clearDropPreview();
    if (intent?.targetSlot && intent.zone !== 'middle') {
      openDraggedFilesInEditor(payload, {targetSlot: intent.targetSlot, targetZone: intent.zone});
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
      statusEl.innerHTML = '<span class="err">select a YOLOmux pane before pasting an image</span>';
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
  try {
    const existing = JSON.parse(localStorage.getItem(pasteLockStorageKey) || 'null');
    if (existing?.expiresAt && existing.expiresAt > now) return false;
    localStorage.setItem(pasteLockStorageKey, JSON.stringify({session, expiresAt: now + 1500}));
  } catch (_) {
    // Clipboard events can arrive as a burst; the in-memory flag is the fallback.
  }
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
  try {
    const counters = JSON.parse(localStorage.getItem(pasteCountersStorageKey) || '{}');
    return counters && typeof counters === 'object' ? counters : {};
  } catch (_) {
    return {};
  }
}

function writePasteCounters(counters) {
  try {
    localStorage.setItem(pasteCountersStorageKey, JSON.stringify(counters));
  } catch (_) {}
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
    statusEl.innerHTML = '<span class="err">readonly access cannot upload files</span>';
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const response = await apiFetch(`/api/upload?session=${encodeURIComponent(session)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">upload failed: ${esc(payload.error || response.statusText)}</span>`;
      return;
    }
    const paths = (payload.files || []).map(file => file.path).filter(Boolean);
    if (options.source === 'paste') syncPasteCountersFromPayload(payload);
    activateTab(session, 'terminal');
    const inserted = options.source === 'paste'
      ? insertPasteUploadReferences(session, payload.files || [], {silent: true})
      : insertUploadPaths(session, paths, {silent: true});
    showUploadResult(session, payload, inserted);
    refreshOpenEventLogs();
    refreshTranscripts();
  } catch (error) {
    statusEl.innerHTML = `<span class="err">upload failed: ${esc(error)}</span>`;
  }
}

function insertUploadPaths(session, paths, options = {}) {
  if (!paths.length) return false;
  const inserted = insertIntoTerminal(session, `${paths.map(shellQuote).join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">inserted upload path into ${esc(sessionLabel(session))}</span>`
      : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
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
      : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
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
    statusEl.innerHTML = '<span class="err">readonly access cannot type into terminal sessions</span>';
    return false;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) return false;
  const filtered = stripTerminalQueryResponses(text);
  if (!filtered) return false;
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  if (autoFocusEnabled) item.term?.focus?.();
  setFocusedTerminal(session);
  return true;
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function showUploadResult(session, payload, inserted) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
  const files = payload.files || [];
  const paths = files.map(file => file.path).filter(Boolean);
  const label = files.length === 1 ? (files[0].saved_name || files[0].name || 'file') : `${files.length} files`;
  const target = payload.target_dir || '';
  const insertedText = inserted ? '; path inserted' : '; terminal not connected';
  const expiresAt = Date.now() + toastDurationMs;
  const newEntries = files.length
    ? files.map(file => {
      const name = file.saved_name || file.name || 'file';
      const destination = pathBasename(file.path || target) || target;
      return {
        id: ++uploadResultSequence,
        text: `uploaded ${name} to ${destination}${insertedText}`,
        path: file.path || '',
        expiresAt,
      };
    })
    : [{
      id: ++uploadResultSequence,
      text: `uploaded ${label} to ${pathBasename(target) || target}${insertedText}`,
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
    title: `YOLOmux - ${serverHostname}: ${sessionLabel(session)} upload`,
    closeLabel: 'Hide upload status',
    keepLabel: 'Keep upload status visible',
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
  const head = panel.querySelector('.panel-head');
  if (head) head.dataset.dragSlot = slot;
  if (isFileEditorItem(session)) renderFileEditorPanel(panel, session);
  updatePaneExpandButton(panel, session);
  updatePaneTabStrip(panel, slot);
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
  setFocusedPanelItem(session);
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
    statusEl.innerHTML = '<span class="err">readonly access cannot switch tmux windows</span>';
    return;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) {
    statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
    return;
  }
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  previewTmuxWindowLabel(session, key);
  statusEl.innerHTML = `<span class="ok">${esc(label)}: ${esc(sessionLabel(session))}</span>`;
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  setTimeout(refreshTranscripts, 250);
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
    const container = document.getElementById(`term-${session}`);
    if (container) container.innerHTML = `<pre class="terminal-error">Session ${esc(sessionLabel(session))} is not available. Click or drag it again to retry.</pre>`;
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
    scheduleTerminalReconnect(session, item);
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
  const container = document.getElementById(`term-${session}`);
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
    statusEl.innerHTML = '<span class="err">xterm unavailable</span>';
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
    theme: {
      background: '#11151d',
      foreground: '#dfe6ef',
      cursor: '#f5f7fb',
      selectionBackground: '#3a4b64'
    }
  });
  term.open(container);
  installTerminalLinkProvider(term);
  installTerminalContextMenu(session, term, container);
  installTerminalCopyShortcut(session, term);
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
  term.onData(data => {
    if (readOnlyMode) return;
    const current = terminals.get(session);
    const socket = current?.socket;
    if (socket?.readyState === WebSocket.OPEN) {
      const filtered = stripTerminalQueryResponses(data);
      if (filtered) socket.send(JSON.stringify({type: 'input', data: filtered}));
    }
  });
  connectTerminalSocket(session, item);
}

function updateTypingIndicator(session) {
  const item = terminals.get(session);
  const container = item?.container || document.getElementById(`term-${session}`);
  const pane = document.getElementById(`terminal-pane-${session}`);
  const panel = document.getElementById(`panel-${session}`);
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
    statusEl.textContent = 'no session selected';
    statusEl.removeAttribute('title');
    return;
  }
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  if (!activeTmuxSessions.length) {
    statusEl.textContent = `${infoTabLabel} shown`;
    statusEl.removeAttribute('title');
    return;
  }
  let open = 0;
  for (const session of activeTmuxSessions) {
    const item = terminals.get(session);
    if (item?.socket?.readyState === WebSocket.OPEN) open += 1;
  }
  const total = activeTmuxSessions.length;
  statusEl.textContent = open === total ? '' : `${open}/${total} conn`;
  statusEl.title = open === total ? '' : `${open}/${total} terminal sockets connected`;
}

async function toggleAutoApprove(session) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change YOLO</span>';
    return;
  }
  const state = autoApproveStates.get(session) || {};
  const current = state.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change YOLO</span>';
    return;
  }
  try {
    const response = await apiFetch(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      if (payload?.target || payload?.session) {
        autoApproveStates.set(session, payload);
        updateSessionButtonStates();
        renderAutoApproveButton(session, payload);
      }
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'YOLO approval failed')}</span>`;
      return;
    }
    autoApproveStates.set(session, payload);
    updateSessionButtonStates();
    renderAutoApproveButton(session, payload);
    statusEl.innerHTML = payload.enabled
      ? `<span class="ok">enabled YOLO for ${esc(sessionLabel(session))}</span>`
      : `<span class="ok">disabled YOLO for ${esc(sessionLabel(session))}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">YOLO request failed: ${esc(error)}</span>`;
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
    const response = await apiFetch('/api/auto-approve');
    const payload = await response.json();
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
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const response = await apiFetch(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        const payload = await response.json();
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
  }
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
    button.classList.toggle('active', enabled);
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
    button.textContent = 'YO';
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
  const node = document.getElementById(`summary-${session}`);
  if (!node) return;
  if (readOnlyMode) {
    node.textContent = 'AI summary requires admin access.';
    statusEl.innerHTML = '<span class="err">readonly access cannot run AI summary</span>';
    return;
  }
  node.textContent = 'starting structured Codex summary for the last hour...\n\n';
  const source = new EventSource(`/api/summary-stream?session=${encodeURIComponent(session)}&lookback=${60 * 60}`);
  summaryStreams.set(session, source);
  source.addEventListener('meta', event => {
    const payload = JSON.parse(event.data);
    const fallback = payload.fallback ? 'recent transcript tail' : 'last hour';
    const projectCount = Array.isArray(payload.projects) ? payload.projects.length : 0;
    node.textContent += `[codex] summarizing ${fallback} for ${payload.focus_root || session}\n`;
    if (payload.summary_model) node.textContent += `[codex] model: ${payload.summary_model}; effort: ${payload.summary_effort || 'default'}\n`;
    node.textContent += `[codex] project inventory: ${projectCount} sessions\n\n`;
    node.scrollTop = node.scrollHeight;
  });
  source.addEventListener('log', event => {
    const payload = JSON.parse(event.data);
    if (payload.text) {
      node.textContent += `[codex] ${payload.text}\n`;
      node.scrollTop = node.scrollHeight;
    }
  });
  source.addEventListener('delta', event => {
    const payload = JSON.parse(event.data);
    if (payload.text) {
      node.textContent += payload.text;
      node.scrollTop = node.scrollHeight;
    }
  });
  source.addEventListener('summary_error', event => {
    const payload = JSON.parse(event.data);
    node.textContent += `\n[error] ${payload.error || 'summary failed'}\n`;
    node.scrollTop = node.scrollHeight;
    stopSummaryStream(session);
  });
  source.addEventListener('done', event => {
    const payload = JSON.parse(event.data);
    if (payload.return_code && payload.return_code !== 0) {
      node.textContent += `\n[codex exited ${payload.return_code}]\n`;
    }
    stopSummaryStream(session);
  });
  source.onerror = () => {
    if (summaryStreams.get(session) !== source) return;
    node.textContent += '\n[error] summary stream disconnected\n';
    stopSummaryStream(session);
  };
}

function stopSummaryStream(session) {
  const source = summaryStreams.get(session);
  if (!source) return;
  source.close();
  summaryStreams.delete(session);
}

async function refreshTranscripts() {
  try {
    const response = await apiFetch('/api/transcripts');
    transcriptMeta = await response.json();
    updateMetadataBadgePulses(transcriptMeta);
    const previousActive = activeSessions.slice();
    const sessionsChanged = updateSessionList(transcriptMeta.session_order || []);
    await loadAutoStatuses();
    if (sessionsChanged) renderPanels(previousActive);
    renderSessionButtons();
    renderInfoPanel();
    renderYosupPanel();
    refreshActivitySummary({silent: true});
    for (const session of activeSessions.filter(isTmuxSession)) {
      const meta = document.getElementById(`meta-${session}`);
      const preview = document.getElementById(`transcript-${session}`);
      const info = transcriptMeta.sessions?.[session];
      const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
      updatePanelHeader(session, info);
      if (meta) {
        meta.innerHTML = stripTitleAttrs(projectMetaHtml(session, info));
        meta.removeAttribute('title');
      }
      renderSummaryContext(session, info, agent);
      if (agent?.transcript) {
        updateTranscriptPathRow(session, agent.transcript);
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
    refreshWatchedFilesystem();
    trackSessionStateChanges();
    refreshOpenEventLogs();
  } catch (error) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      const meta = document.getElementById(`meta-${session}`);
      const preview = document.getElementById(`transcript-${session}`);
      if (meta) meta.innerHTML = `<span class="err">transcript lookup failed</span>`;
      updateTranscriptPathRow(session, '', 'transcript lookup failed');
      if (preview) preview.textContent = `transcript lookup failed: ${error}`;
    }
  }
}

function updatePanelHeader(session, info) {
  const tab = document.getElementById(`panel-tab-${session}`);
  const panel = document.getElementById(`panel-${session}`);
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
    const response = await apiFetch(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    const payload = await response.json();
    if (payload.items) {
      updateTranscriptPathRow(session, payload.path);
      renderTranscriptItems(preview, payload.path, payload.items, options);
    } else {
      preview.textContent = JSON.stringify(payload, null, 2);
    }
  } catch (error) {
    preview.textContent += `\n\ncontext load failed: ${error}`;
  }
}

function startTranscriptStream(session, options = {}) {
  stopTranscriptStream(session);
  const preview = document.getElementById(`transcript-${session}`);
  if (!preview) return;
  const url = `/api/context-stream?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`;
  const source = new EventSource(url);
  transcriptStreams.set(session, source);
  source.addEventListener('reset', event => {
    const payload = JSON.parse(event.data);
    updateTranscriptPathRow(session, payload.path);
    renderTranscriptItems(preview, payload.path, payload.items || [], {scrollBottom: options.scrollBottom === true});
  });
  source.addEventListener('items', event => {
    const payload = JSON.parse(event.data);
    appendTranscriptItems(preview, payload.items || []);
  });
  source.addEventListener('ping', () => {});
  source.onerror = () => {
    stopTranscriptStream(session);
    const pane = document.getElementById(`transcript-pane-${session}`);
    if (pane?.classList.contains('active')) {
      statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} transcript stream disconnected</span>`;
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
    const response = await apiFetch(`/api/events?session=${encodeURIComponent(session)}&limit=120`);
    const payload = await response.json();
    if (!response.ok) {
      node.innerHTML = `<div class="event-empty">${esc(payload.error || 'failed to load events')}</div>`;
      return;
    }
    const events = Array.isArray(payload.events) ? payload.events : [];
    node.innerHTML = events.length
      ? events.slice().reverse().map(eventItemHtml).join('')
      : '<div class="event-empty">no events yet</div>';
  } catch (error) {
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
    const response = await apiFetch(`/api/ping?t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(response.statusText || `HTTP ${response.status}`);
    await response.json();
    const elapsedMs = Math.max(1, Math.round(performance.now() - startedAt));
    latencySamples = [...latencySamples, elapsedMs].slice(-latencySamplesMax);
    renderLatency(elapsedMs);
  } catch (_) {
    renderLatency(null);
  }
}

function refreshAll() {
  refreshTranscripts();
  refreshAutoStatuses();
  refreshWatchedFilesystem();
}

async function boot() {
  applySettingsPayload(clientSettingsPayload, {initial: true, force: true});
  applyEditorThemeMode();
  applyFileExplorerStaticLabels();
  renderTransportWarning();
  renderTabMetaToggle();
  bindTopbarMetrics();
  syncInitialLayoutUrl();
  statusEl.textContent = 'loading YOLO status...';
  await loadNotifyStatus();
  await loadAutoStatuses();
  renderSessionButtons();
  renderPanels([], {prune: false});
  await Promise.all(activeSessions.filter(isTmuxSession).map(session => ensureTerminalRunning(session)));
  refreshTranscripts();
  renderAutoApproveButtons();
  updateLatency();
  installRuntimeIntervals();
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  title.textContent = `${sessionLabel(session)} transcript tail`;
  body.textContent = 'loading...';
  modal.classList.add('open');
  const response = await apiFetch(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  const payload = await response.json();
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

if (refreshMeta) {
  refreshMeta.textContent = 'Refresh';
  refreshMeta.setAttribute('aria-label', 'Refresh session state');
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
document.getElementById('closeModal').onclick = () => document.getElementById('modal').classList.remove('open');
document.addEventListener('click', event => {
  if (event.target?.closest?.('.app-menu')) return;
  closeAppMenus();
});
topbar?.addEventListener('pointerenter', () => {
  closeOtherSessionPopovers(null, {force: true});
  closeFileImagePreview();
});
document.addEventListener('keydown', event => {
  const mod = appModifier(event);
  const key = event.key.toLowerCase();
  const platformActionAllowed = globalShortcutTargetAllowsPlatformAction(event.target);
  if (mod && key === 'p' && platformActionAllowed) {
    event.preventDefault();
    if (event.shiftKey) openCommandPalette();
    else openFileQuickOpen();
    return;
  }
  if (mod && platformActionAllowed) {
    if (key === 'w') {
      event.preventDefault();
      const item = currentActiveMenuItem();
      if (item) removeSessionFromLayout(item);
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
});
window.addEventListener('resize', () => {
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

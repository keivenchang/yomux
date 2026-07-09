// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// YO!info and YO!agent panel shells split from 80_panes_preferences.js.

function setInfoSessionFileLookbackHours(hours, options = {}) {
  const previous = infoSessionFileLookbackHours;
  infoSessionFileLookbackHours = writeStoredInfoLookbackHours(hours);
  if (infoSessionFileLookbackHours !== previous) {
    activitySummaryState.payload = {...activitySummaryState.payload, session_file_hours: infoSessionFileLookbackHours};
    if (options.refresh !== false) refreshActivitySummary({force: true, silent: options.silent === true});
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  }
  return infoSessionFileLookbackHours;
}

function infoGroupingControlsHtml() {
  const grouping = typeof currentInfoGrouping === 'function' ? currentInfoGrouping() : ['tab', 'path', 'tmux-window'];
  const sort = typeof currentInfoSort === 'function' ? currentInfoSort() : {key: 'date', dir: 'desc'};
  const search = typeof currentInfoSearch === 'function' ? currentInfoSearch() : '';
  const presets = typeof infoGroupingPresets === 'function' ? infoGroupingPresets() : [];
  const sortFields = typeof infoSortFields === 'function' ? infoSortFields() : [{key: 'date', dir: 'desc', value: 'date:desc', label: t('common.sort.recent')}];
  const dimensionsForLevel = level => typeof infoGroupDimensionsForLevel === 'function'
    ? infoGroupDimensionsForLevel(level, grouping)
    : (typeof infoGroupDimensions === 'function' ? infoGroupDimensions() : []);
  const optionHtml = level => {
    const dimensions = dimensionsForLevel(level);
    return [
      `<option value="">${esc(t('info.group.none'))}</option>`,
      ...dimensions.map(dimension => `<option value="${esc(dimension.key)}"${grouping[level] === dimension.key ? ' selected' : ''}>${esc(dimension.label)}</option>`),
    ].join('');
  };
  const sortValue = `${sort.key}:${sort.dir}`;
  const sortFieldHtml = sortFields.map(field => `<option value="${esc(field.value || `${field.key}:${field.dir || ''}`)}"${sortValue === (field.value || `${field.key}:${field.dir || ''}`) ? ' selected' : ''}>${esc(field.label)}</option>`).join('');
  const presetHtml = presets.map(preset => {
    const active = grouping.join('|') === preset.grouping.join('|');
    return `<button type="button" class="info-tree-preset${active ? ' active' : ''}" data-info-preset="${esc(preset.key)}" aria-pressed="${active ? 'true' : 'false'}" title="${esc(preset.title)}">${esc(preset.label)}</button>`;
  }).join('');
  const searchHtml = `<label class="info-tree-search-control"><span>${esc(t('common.search'))}</span><input type="search" data-info-search value="${esc(search)}" placeholder="${esc(t('info.search.placeholder'))}" aria-label="${esc(t('info.search.placeholder'))}"></label>`;
  const selects = [0, 1, 2, 3].map(index => `${index === 0 ? '' : '<span class="info-tree-order-separator" aria-hidden="true">&gt;</span>'}<label class="info-tree-group-select info-tree-order-select"><select data-info-group-level="${index}" aria-label="${esc(t('info.group.orderByLevel', {level: index + 1}))}">${optionHtml(index)}</select></label>`).join('');
  const sortControls = `<div class="info-tree-sort-controls" role="group" aria-label="${esc(t('info.sort.order'))}">
          <label class="info-tree-group-select"><span>${esc(t('changes.sort'))}</span><select data-info-sort-mode>${sortFieldHtml}</select></label>
        </div>`;
  return `
        <div class="info-tree-primary-controls">
          <div class="info-tree-presets" role="group" aria-label="${esc(t('info.group.presets'))}">${presetHtml}</div>
          ${searchHtml}
        </div>
        <div class="info-tree-group-selects" role="group" aria-label="${esc(t('info.group.levels'))}"><span class="info-tree-order-label">${esc(t('info.group.orderBy'))}</span>${selects}</div>
        ${sortControls}`;
}

function syncInfoTreeScrolledState(root = document) {
  const panels = root?.matches?.('.info-tree-panel') ? [root] : Array.from(root?.querySelectorAll?.('.info-tree-panel') || []);
  for (const panel of panels) {
    const pane = panel.querySelector('.info-pane');
    const scroller = panel.querySelector('.info-tree-list');
    if (!pane) continue;
    pane.classList.toggle('info-tree-pane-scrolled', Boolean(scroller && scroller.scrollTop > 0));
  }
}

function bindInfoPanel(panel) {
  if (!panel || panel.__yolomuxInfoPanelBound === true) return;
  panel.__yolomuxInfoPanelBound = true;
  const treeScroller = panel.querySelector('.info-tree-list');
  if (treeScroller) {
    treeScroller.addEventListener('scroll', () => syncInfoTreeScrolledState(panel), {passive: true});
    syncInfoTreeScrolledState(panel);
  }
  delegate(panel, 'click', '[data-info-refresh]', event => {
    event.preventDefault();
    refreshTranscripts({force: true});
    refreshActivitySummary({force: true});
  });
  delegate(panel, 'click', '[data-info-preset]', (event, button) => {
    event.preventDefault();
    if (typeof setInfoGroupingPreset === 'function') setInfoGroupingPreset(button.dataset.infoPreset || '');
  });
  delegate(panel, 'click', '[data-auto-session][data-action="pane-tab-auto-approve"]', async (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    if (typeof toggleAutoApprove === 'function') await toggleAutoApprove(button.dataset.autoSession || '');
  });
  delegate(panel, 'click', '[data-info-open-tab]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const session = button.dataset.infoOpenTab || '';
    if (session) selectSession(session, {userInitiated: true});
  });
  delegate(panel, 'click', '[data-info-open-ai-window]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const session = button.dataset.infoOpenAiTab || '';
    const windowIndex = button.dataset.infoOpenAiWindow || '';
    if (!session) return;
    if (windowIndex !== '') {
      activateTmuxWindowFromUserAction(session, windowIndex, button.textContent || t('terminal.window.title', {name: windowIndex}));
    }
    selectSession(session, {userInitiated: true});
  });
  delegate(panel, 'click', '[data-info-open-path]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const path = button.dataset.infoOpenPath || '';
    if (!path) return;
    (async () => {
      if (typeof openFileExplorerPane === 'function') await openFileExplorerPane();
      if (typeof setFileExplorerMode === 'function') setFileExplorerMode('files');
      if (typeof openFileExplorerAt === 'function') {
        const opened = await openFileExplorerAt(path, {manualSelection: true});
        if (opened && typeof selectFileTreePath === 'function') selectFileTreePath(path);
      }
    })().catch(error => {
      statusErr(localizedHtml('status.expandDirectoryFailed', {error}));
    });
  });
  panel.addEventListener('change', event => {
    const select = event.target.closest('[data-info-group-level]');
    if (select && panel.contains(select) && typeof setInfoGroupingLevel === 'function') {
      setInfoGroupingLevel(select.dataset.infoGroupLevel, select.value || '');
      return;
    }
    const sortMode = event.target.closest('[data-info-sort-mode]');
    if (sortMode && panel.contains(sortMode) && typeof setInfoSortMode === 'function') setInfoSortMode(sortMode.value || '');
  });
  panel.addEventListener('toggle', event => {
    const details = event.target.closest?.('details[data-info-group-key]');
    if (!details || !panel.contains(details) || typeof setInfoTreeGroupCollapsed !== 'function') return;
    setInfoTreeGroupCollapsed(details.dataset.infoGroupKey || '', !details.open);
  }, true);
  panel.addEventListener('input', event => {
    const search = event.target.closest('[data-info-search]');
    if (search && panel.contains(search) && typeof setInfoSearch === 'function') setInfoSearch(search.value || '');
  });
}

function createInfoPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel info-tree-panel';
  panel.id = panelDomId(infoItemId);
  panel.innerHTML = panelFrameHtml({
    item: infoItemId,
    controlsHtml: virtualPanelInnerControlsHtml(infoItemId),
    afterHeadHtml: `<div class="info-actions-bar info-tree-actions-bar">
        ${infoGroupingControlsHtml()}
        <div class="info-subtab-actions">
          <button type="button" class="info-refresh" data-info-refresh title="${esc(t('common.refresh'))}" aria-label="${esc(t('common.refresh'))}">${esc(t('common.refresh'))}</button>
        </div>
      </div>`,
    bodyClass: 'info-pane',
    bodyHtml: '<div id="info-content" class="info-list info-tree-list"></div>',
  });
  bindPanelShell(panel, infoItemId);
  bindInfoPanel(panel);
  if (typeof renderInfoPanel === 'function') renderInfoPanel();
  return panel;
}

function createYoagentPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel yoagent-panel';
  panel.id = panelDomId(yoagentItemId);
  panel.innerHTML = panelFrameHtml({
    item: yoagentItemId,
    controlsHtml: virtualPanelInnerControlsHtml(yoagentItemId),
    afterHeadHtml: `<div class="info-actions-bar">
        <div class="info-subtab-actions">
          <button type="button" class="info-refresh" data-action="yoagent-refresh" data-yoagent-refresh title="${esc(t('yoagent.refreshTitle'))}">${esc(t('yoagent.refresh'))}</button>
        </div>
      </div>`,
    bodyClass: 'info-pane',
    bodyHtml: '<div id="yoagent-content" class="info-list yoagent-list"></div>',
  });
  bindPanelShell(panel, yoagentItemId);
  bindYoagentPanel(panel);
  showYoagentStartupInfoOnce();
  renderYoagentPanel({scrollBottom: true});
  loadYoagentConversation({silent: true, scrollBottom: true});
  loadYoagentJobs({silent: true, scrollBottom: true});
  refreshActivitySummary({silent: true});
  prewarmYoagent({scrollBottom: true});
  return panel;
}

function bindYoagentPanel(panel) {
  bindActionDispatcher(panel, {
    'yoagent-refresh': () => {
      loadYoagentConversation({force: true, silent: true, scrollBottom: false});
      loadYoagentJobs({silent: true, scrollBottom: false});
      refreshActivitySummary({force: true});
    },
    'yoagent-clear': () => clearYoagentConversation(),
    'yoagent-retry': () => {
      const input = panel.querySelector('[data-yoagent-chat-input]');
      sendYoagentChatMessage(input?.value || yoagentChatState.draft);
    },
    'yoagent-chat-cancel': () => cancelActiveYoagentChatRequest(),
    'yoagent-queued-cancel': (_event, target) => cancelQueuedYoagentChatMessage(target.dataset.yoagentQueuedCancel || ''),
    'yoagent-agent-restart': (_event, target) => createNextSession(target.dataset.yolomuxAgentRestart || 'claude'),
    'yoagent-action-send': (_event, target) => executeYoagentActionSend(target.dataset.yoagentActionSend || ''),
    'yoagent-job-confirm': (_event, target) => confirmYoagentJob(target.dataset.yoagentJobConfirm || ''),
    'yoagent-job-cancel': (_event, target) => cancelYoagentJob(target.dataset.yoagentJobCancel || ''),
    'yoagent-wait-clear': (_event, target) => clearYoagentPendingWait(target.dataset.yoagentWaitClear || ''),
  });
  panel.addEventListener('submit', event => {
    const form = event.target.closest('[data-yoagent-chat-form]');
    if (!form || !panel.contains(form)) return;
    event.preventDefault();
    const input = form.querySelector('[data-yoagent-chat-input]');
    const value = input?.value || '';
    if (input) input.value = '';
    yoagentChatState.draft = '';
    resetYoagentComposerHistory();
    sendYoagentChatMessage(value);
  });
  panel.addEventListener('input', event => {
    const input = event.target.closest('[data-yoagent-chat-input]');
    if (input && panel.contains(input)) {
      yoagentChatState.draft = input.value || '';
      if (yoagentChatState.historyCursor === null) yoagentChatState.historyDraft = '';
    }
  });
  panel.addEventListener('keydown', event => {
    const input = event.target.closest('[data-yoagent-chat-input]');
    if (input && panel.contains(input)) handleYoagentChatHistoryKeydown(event, input);
  });
  panel.addEventListener('change', event => {
    const yoagentSetting = event.target.closest('[data-yoagent-setting-path]');
    if (!yoagentSetting || !panel.contains(yoagentSetting) || readOnlyMode) return;
    const path = yoagentSetting.dataset.yoagentSettingPath || '';
    saveSettingsPatch(settingPatchForPath(path, yoagentSetting.value))
      .then(() => { statusEl.textContent = t('yoagent.statusBackend', {backend: yoagentBackendLabel(yoagentComposerBackendKey())}); renderYoagentPanel(); })
      .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
  });
}

// Persistent virtual panels keep their chrome mounted between language changes. Re-label those controls in place.
function relocalizeInfoPanelChrome(panel = document.getElementById(panelDomId(infoItemId))) {
  if (!panel) return;
  relocalizeVirtualPanelChrome(panel, infoTabLabel());
  const refresh = panel.querySelector('[data-info-refresh]');
  if (refresh) {
    if (typeof setMetadataRefreshButtonLoading === 'function') {
      setMetadataRefreshButtonLoading(refresh, transcriptMetadataState.loading, t('common.refresh'), t('common.refresh'));
    } else {
      const label = t('common.refresh');
      refresh.textContent = label;
      refresh.title = label;
      refresh.setAttribute('aria-label', label);
    }
  }
  const bar = panel.querySelector('.info-tree-actions-bar');
  if (bar) {
    const actions = bar.querySelector('.info-subtab-actions');
    bar.innerHTML = `${infoGroupingControlsHtml()}${actions ? actions.outerHTML : ''}`;
  }
}

function relocalizeYoagentPanelChrome(panel = document.getElementById(panelDomId(yoagentItemId))) {
  if (!panel) return;
  relocalizeVirtualPanelChrome(panel, yoagentTabLabel());
  const agentRefresh = panel.querySelector('[data-yoagent-refresh]');
  if (agentRefresh) {
    agentRefresh.textContent = t('yoagent.refresh');
    agentRefresh.title = t('yoagent.refreshTitle');
    agentRefresh.setAttribute('aria-label', t('yoagent.refreshTitle'));
  }
}

// Compatibility shim for old share/deeplink state. The active tab now owns visibility.
function applyInfoSubTab(panel = document.getElementById(panelDomId(infoItemId))) {
  if (panel) panel.dataset.infoSubtab = infoPanelSubTab;
}

function setInfoSubTab(tab, options = {}) {
  const next = normalizedInfoSubTab(tab);
  if (next !== infoPanelSubTab) {
    infoPanelSubTab = next;
    writeStoredInfoSubTab(next);
  }
  applyInfoSubTab();
  if (next === 'yoagent') {
    selectSession(yoagentItemId, {userInitiated: options.userInitiated === true});
    activateYoagentPanel({focusChat: options.focusChat === true});
  } else {
    selectSession(infoItemId, {userInitiated: options.userInitiated === true});
  }
  scheduleShareUiStatePublish();
}

function activateYoagentPanel(options = {}) {
  const scrollBottom = options.scrollBottom ?? true;
  showYoagentStartupInfoOnce();
  renderYoagentPanel({preserveDraft: true, focusInput: options.focusChat === true, scrollBottom});
  loadYoagentConversation({silent: true, scrollBottom});
  loadYoagentJobs({silent: true, scrollBottom});
  refreshActivitySummary({silent: true});
  prewarmYoagent({scrollBottom});
}

// Legacy open helper kept for older tests/share replays. New menus target the standalone tab directly.
async function openInfoSubTab(tab) {
  infoPanelSubTab = normalizedInfoSubTab(tab);
  writeStoredInfoSubTab(infoPanelSubTab);
  await selectSession(infoPanelSubTab === 'yoagent' ? yoagentItemId : infoItemId);
  applyInfoSubTab();
  if (infoPanelSubTab === 'yoagent') {
    activateYoagentPanel({focusChat: true});
  }
  scheduleShareUiStatePublish();
}

function rightmostLeafSlotWithRowSplit(node, insideRow = false, slots = layoutSlots, options = {}) {
  if (!node) return null;
  if (node.slot) {
    if (!insideRow) return null;
    if (options.excludeFileExplorer && isFileExplorerItem(activeItemForSide(node.slot, slots))) return null;
    return node.slot;
  }
  const children = Array.isArray(node.children) ? node.children : [];
  if (node.split === 'row') {
    return rightmostLeafSlotWithRowSplit(children[1], true, slots, options)
      || rightmostLeafSlotWithRowSplit(children[0], true, slots, options);
  }
  return rightmostLeafSlotWithRowSplit(children[0], insideRow, slots, options)
    || rightmostLeafSlotWithRowSplit(children[1], insideRow, slots, options);
}

function layoutTreeHasRowSplit(node) {
  if (!node || node.slot) return false;
  if (node.split === 'row') return true;
  return (node.children || []).some(layoutTreeHasRowSplit);
}

function nonFileExplorerLeafSlots(root, slots = layoutSlots) {
  return layoutLeafSlots(root).filter(slot => !isFileExplorerItem(activeItemForSide(slot, slots)));
}

function rightmostExistingPaneSlot(slots = layoutSlots) {
  const root = slots?.[layoutTreeKey] || null;
  if (!root || nonFileExplorerLeafSlots(root, slots).length < 2 || !layoutTreeHasRowSplit(root)) return null;
  return rightmostLeafSlotWithRowSplit(root, false, slots, {excludeFileExplorer: true})
    || rightmostLeafSlotWithRowSplit(root, false, slots);
}

function focusYoagentChatSoon() {
  setTimeout(() => focusYoagentChatInput(), 80);
}

function splitVirtualItemToRightPane(item, sourceSlot = null) {
  const next = layoutWithoutItem(item);
  const root = next[layoutTreeKey] || legacyLayoutTree(next);
  if (!root) {
    const targetSlot = sourceSlot || slotForNewSession();
    next[layoutTreeKey] = leafNode(targetSlot);
    next[targetSlot] = paneStateWithTabs([item], item);
    applyLayoutSlots(next, {focusSession: item, prune: false});
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([item], item);
  next[layoutTreeKey] = splitNode('row', root, leafNode(newSlot), splitPercentForNewItem(item, 'right'));
  applyLayoutSlots(next, {focusSession: item, prune: false});
}

function splitInfoItemToRightPane(sourceSlot = null) {
  splitVirtualItemToRightPane(infoItemId, sourceSlot);
}

function openYoagentRightPane() {
  const sourceSlot = slotForSession(yoagentItemId);
  const targetSlot = rightmostExistingPaneSlot();
  if (targetSlot) {
    if (sourceSlot === targetSlot) {
      activatePaneTab(targetSlot, yoagentItemId);
    } else {
      moveSessionToSlot(yoagentItemId, targetSlot, sourceSlot, paneTabs(targetSlot).length);
    }
  } else {
    splitVirtualItemToRightPane(yoagentItemId, sourceSlot);
  }
  infoPanelSubTab = 'yoagent';
  writeStoredInfoSubTab('yoagent');
  activateYoagentPanel({focusChat: true});
  focusYoagentChatSoon();
}

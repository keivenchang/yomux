// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// YO!info and YO!agent panel shells split from 80_panes_preferences.js.
function infoLookbackControlHtml() {
  const options = sessionFileLookbackOptions()
    .map(option => `<option value="${esc(option.hours)}"${option.hours === infoSessionFileLookbackHours ? ' selected' : ''}>${esc(option.label)}</option>`)
    .join('');
  return `<label class="info-lookback-control">${esc(t('info.lookback'))}<select data-info-lookback>${options}</select></label>`;
}

function setInfoSessionFileLookbackHours(hours, options = {}) {
  const previous = infoSessionFileLookbackHours;
  infoSessionFileLookbackHours = writeStoredInfoLookbackHours(hours);
  if (infoSessionFileLookbackHours !== previous) {
    activitySummaryPayload = {...activitySummaryPayload, session_file_hours: infoSessionFileLookbackHours};
    if (options.refresh !== false) refreshActivitySummary({force: true, silent: options.silent === true});
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  }
  return infoSessionFileLookbackHours;
}

function createInfoPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel';
  panel.id = panelDomId(infoItemId);
  panel.innerHTML = `
      <div class="panel-head">
        ${virtualPanelControlsHtml(infoItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="info-actions-bar">
        <div class="info-subtab-actions">
          ${infoLookbackControlHtml()}
          <button type="button" class="info-refresh" data-info-refresh title="${esc(t('info.refreshRepo'))}">${esc(t('info.refreshRepo'))}</button>
        </div>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${infoItemId}" class="panel-toast-stack"></div>
        <div id="info-content" class="info-list"></div>
        <div id="info-watched" class="info-watched"></div>
      </div>`;
  bindPanelShell(panel, infoItemId);
  panel.querySelector('[data-info-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
    refreshTranscripts({force: true});
    refreshActivitySummary({force: true});
  });
  panel.addEventListener('change', event => {
    const lookback = event.target.closest('[data-info-lookback]');
    if (lookback && panel.contains(lookback)) setInfoSessionFileLookbackHours(lookback.value);
  });
  renderInfoPanel();
  return panel;
}

function createYoagentPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel yoagent-panel';
  panel.id = panelDomId(yoagentItemId);
  panel.innerHTML = `
      <div class="panel-head">
        ${virtualPanelControlsHtml(yoagentItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="info-actions-bar">
        <div class="info-subtab-actions">
          <button type="button" class="info-refresh" data-yoagent-refresh title="${esc(t('yoagent.refreshTitle'))}">${esc(t('yoagent.refresh'))}</button>
        </div>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${yoagentItemId}" class="panel-toast-stack"></div>
        <div id="yoagent-content" class="info-list yoagent-list"></div>
      </div>`;
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
  panel.querySelector('[data-yoagent-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
    loadYoagentConversation({force: true, silent: true, scrollBottom: false});
    loadYoagentJobs({silent: true, scrollBottom: false});
    refreshActivitySummary({force: true});
  });
  panel.addEventListener('submit', event => {
    const form = event.target.closest('[data-yoagent-chat-form]');
    if (!form || !panel.contains(form)) return;
    event.preventDefault();
    const input = form.querySelector('[data-yoagent-chat-input]');
    const value = input?.value || '';
    if (input) input.value = '';
    yoagentDraft = '';
    resetYoagentComposerHistory();
    sendYoagentChatMessage(value);
  });
  panel.addEventListener('click', event => {
    const clear = event.target.closest('[data-yoagent-clear]');
    if (clear && panel.contains(clear)) {
      event.preventDefault();
      clearYoagentConversation();
      return;
    }
    const retry = event.target.closest('[data-yoagent-retry]');
    if (retry && panel.contains(retry)) {
      event.preventDefault();
      const input = panel.querySelector('[data-yoagent-chat-input]');
      sendYoagentChatMessage(input?.value || yoagentDraft);
      return;
    }
    const activeCancel = event.target.closest('[data-yoagent-chat-cancel]');
    if (activeCancel && panel.contains(activeCancel)) {
      event.preventDefault();
      cancelActiveYoagentChatRequest();
      return;
    }
    const queuedCancel = event.target.closest('[data-yoagent-queued-cancel]');
    if (queuedCancel && panel.contains(queuedCancel)) {
      event.preventDefault();
      cancelQueuedYoagentChatMessage(queuedCancel.dataset.yoagentQueuedCancel || '');
      return;
    }
    const agentRestart = event.target.closest('[data-yolomux-agent-restart]');
    if (agentRestart && panel.contains(agentRestart)) {
      event.preventDefault();
      createNextSession(agentRestart.dataset.yolomuxAgentRestart || 'claude');
      return;
    }
    const actionSend = event.target.closest('[data-yoagent-action-send]');
    if (actionSend && panel.contains(actionSend)) {
      event.preventDefault();
      executeYoagentActionSend(actionSend.dataset.yoagentActionSend || '');
      return;
    }
    const jobConfirm = event.target.closest('[data-yoagent-job-confirm]');
    if (jobConfirm && panel.contains(jobConfirm)) {
      event.preventDefault();
      confirmYoagentJob(jobConfirm.dataset.yoagentJobConfirm || '');
      return;
    }
    const jobCancel = event.target.closest('[data-yoagent-job-cancel]');
    if (jobCancel && panel.contains(jobCancel)) {
      event.preventDefault();
      cancelYoagentJob(jobCancel.dataset.yoagentJobCancel || '');
      return;
    }
    const waitClear = event.target.closest('[data-yoagent-wait-clear]');
    if (waitClear && panel.contains(waitClear)) {
      event.preventDefault();
      clearYoagentPendingWait(waitClear.dataset.yoagentWaitClear || '');
    }
  });
  panel.addEventListener('input', event => {
    const input = event.target.closest('[data-yoagent-chat-input]');
    if (input && panel.contains(input)) {
      yoagentDraft = input.value || '';
      if (yoagentHistoryCursor === null) yoagentHistoryDraft = '';
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
      .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
  });
}

// Persistent virtual panels keep their chrome mounted between language changes. Re-label those controls in place.
function relocalizeInfoPanelChrome(panel = document.getElementById(panelDomId(infoItemId))) {
  if (!panel) return;
  const minimizeLabel = t('pane.minimize');
  const expandLabel = t('pane.expand');
  panel.querySelectorAll('[data-pane-minimize]').forEach(button => {
    button.title = minimizeLabel;
    button.setAttribute('aria-label', minimizeLabel);
  });
  panel.querySelectorAll('[data-pane-expand]').forEach(button => {
    button.title = expandLabel;
    button.setAttribute('aria-label', expandLabel);
  });
  const infoRefresh = panel.querySelector('[data-info-refresh]');
  if (infoRefresh) {
    if (typeof setMetadataRefreshButtonLoading === 'function') {
      setMetadataRefreshButtonLoading(infoRefresh, transcriptMetaLoading, t('info.refreshRepo'), t('info.refreshRepo'));
    } else {
      const label = t('info.refreshRepo');
      infoRefresh.textContent = label;
      infoRefresh.title = label;
      infoRefresh.setAttribute('aria-label', label);
    }
  }
  const lookback = panel.querySelector('.info-lookback-control');
  if (lookback) lookback.outerHTML = infoLookbackControlHtml();
}

function relocalizeYoagentPanelChrome(panel = document.getElementById(panelDomId(yoagentItemId))) {
  if (!panel) return;
  const minimizeLabel = t('pane.minimize');
  const expandLabel = t('pane.expand');
  panel.querySelectorAll('[data-pane-minimize]').forEach(button => {
    button.title = minimizeLabel;
    button.setAttribute('aria-label', minimizeLabel);
  });
  panel.querySelectorAll('[data-pane-expand]').forEach(button => {
    button.title = expandLabel;
    button.setAttribute('aria-label', expandLabel);
  });
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

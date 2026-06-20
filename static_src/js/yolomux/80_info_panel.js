// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Merged YO!info panel shell and sub-tab routing split from 80_panes_preferences.js.

// ONE merged panel hosting both YO!info (repo metadata) and YO!agent (chat + activity
// context), switched by a segmented sub-tab row under the pane tabs. Both sub-views render into their
// own containers (#info-content / #yoagent-content) and the active one is shown via CSS; the chosen
// sub-tab is remembered across reloads (infoPanelSubTab).
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
  panel.dataset.infoSubtab = infoPanelSubTab;
  panel.innerHTML = `
      <div class="panel-head">
        ${virtualPanelControlsHtml(infoItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="info-subtabs" role="tablist" aria-label="${esc(infoTabLabel())} / ${esc(yoagentTabLabel())}">
        <div class="info-subtab-group" role="presentation">
          <button type="button" class="info-subtab" role="tab" data-info-subtab="info"><span class="session-button-dir">${esc(infoTabLabel())}</span></button>
          <button type="button" class="info-subtab" role="tab" data-info-subtab="yoagent"><span class="session-button-dir">${esc(yoagentTabLabel())}</span></button>
        </div>
        <div class="info-subtab-actions">
          ${infoLookbackControlHtml()}
          <button type="button" class="info-refresh" data-info-subtab-action="info" data-info-refresh title="${esc(t('info.refreshRepo'))}">${esc(t('info.refreshRepo'))}</button>
          <button type="button" class="info-refresh" data-info-subtab-action="yoagent" data-yoagent-refresh title="${esc(t('yoagent.refreshTitle'))}">${esc(t('yoagent.refresh'))}</button>
        </div>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${infoItemId}" class="panel-toast-stack"></div>
        <div class="info-subview" data-info-subview="info">
          <div id="info-content" class="info-list"></div>
          <div id="info-watched" class="info-watched"></div>
        </div>
        <div class="info-subview yoagent-subview" data-info-subview="yoagent">
          <div id="yoagent-content" class="info-list yoagent-list"></div>
        </div>
      </div>`;
  bindPanelShell(panel, infoItemId);
  panel.querySelector('[data-info-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
    refreshTranscripts({force: true});
    refreshActivitySummary({force: true});
  });
  panel.querySelector('[data-yoagent-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
    loadYoagentConversation({force: true, silent: true, scrollBottom: false});
    loadYoagentJobs({silent: true, scrollBottom: false});
    refreshActivitySummary({force: true});
  });
  panel.querySelectorAll('[data-info-subtab]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      setInfoSubTab(button.dataset.infoSubtab, {focusChat: button.dataset.infoSubtab === 'yoagent'});
    });
  });
  // YO!agent chat interactions (submit / clear / retry / draft) — preserved from the old yoagent panel.
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
    const lookback = event.target.closest('[data-info-lookback]');
    if (lookback && panel.contains(lookback)) {
      setInfoSessionFileLookbackHours(lookback.value);
      return;
    }
    // The composer's backend pill writes the real yoagent.backend setting and re-renders (which also
    // flips chat enablement when switching to/from No agent).
    const backend = event.target.closest('[data-yoagent-backend]');
    if (!backend || !panel.contains(backend) || readOnlyMode) return;
    saveSettingsPatch(settingPatch('yoagent.backend', backend.value))
      .then(() => { statusEl.textContent = t('yoagent.statusBackend', {backend: yoagentBackendLabel(backend.value)}); renderYoagentPanel(); })
      .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
  });
  applyInfoSubTab(panel);
  renderInfoPanel();
  if (infoPanelSubTab === 'yoagent') showYoagentStartupInfoOnce();
  renderYoagentPanel();
  if (infoPanelSubTab === 'yoagent') {
    loadYoagentConversation({silent: true});
    loadYoagentJobs({silent: true});
    prewarmYoagent();
  }
  return panel;
}

// The merged YO!info pane keeps its outer chrome and sub-tab row mounted between language changes so the
// YO!agent chat draft and active sub-tab survive. Re-label those persistent controls in place.
function relocalizeInfoPanelChrome(panel = document.getElementById(panelDomId(infoItemId))) {
  if (!panel) return;
  const infoLabel = infoTabLabel();
  const agentLabel = yoagentTabLabel();
  const setLabel = (node, label) => {
    if (!node) return;
    node.textContent = label;
    node.title = label;
    node.setAttribute('aria-label', label);
  };
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
  panel.querySelector('.info-subtabs')?.setAttribute('aria-label', `${infoLabel} / ${agentLabel}`);
  panel.querySelectorAll('[data-info-subtab]').forEach(button => {
    const label = button.dataset.infoSubtab === 'yoagent' ? agentLabel : infoLabel;
    const labelNode = button.querySelector('.session-button-dir') || button;
    labelNode.textContent = label;
    button.title = label;
    button.setAttribute('aria-label', label);
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
  const agentRefresh = panel.querySelector('[data-yoagent-refresh]');
  if (agentRefresh) {
    agentRefresh.textContent = t('yoagent.refresh');
    agentRefresh.title = t('yoagent.refreshTitle');
    agentRefresh.setAttribute('aria-label', t('yoagent.refreshTitle'));
  }
  const lookback = panel.querySelector('.info-lookback-control');
  if (lookback) lookback.outerHTML = infoLookbackControlHtml();
  applyInfoSubTab(panel);
}

// Reflect the active sub-tab onto the merged panel (button highlight + which sub-view is visible).
function applyInfoSubTab(panel = document.getElementById(panelDomId(infoItemId))) {
  if (!panel) return;
  panel.dataset.infoSubtab = infoPanelSubTab;
  panel.querySelectorAll('[data-info-subtab]').forEach(button => {
    const active = button.dataset.infoSubtab === infoPanelSubTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  panel.querySelectorAll('[data-info-subview]').forEach(view => {
    view.classList.toggle('active', view.dataset.infoSubview === infoPanelSubTab);
  });
  panel.querySelectorAll('[data-info-subtab-action]').forEach(button => {
    button.hidden = button.dataset.infoSubtabAction !== infoPanelSubTab;
  });
}

function setInfoSubTab(tab, options = {}) {
  const next = normalizedInfoSubTab(tab);
  if (next !== infoPanelSubTab) {
    infoPanelSubTab = next;
    writeStoredInfoSubTab(next);
  }
  applyInfoSubTab();
  if (next === 'yoagent') {
    showYoagentStartupInfoOnce();
    renderYoagentPanel({preserveDraft: true, focusInput: options.focusChat === true});
    loadYoagentConversation({silent: true});
    loadYoagentJobs({silent: true});
    refreshActivitySummary({silent: true});
    prewarmYoagent();
  }
  scheduleShareUiStatePublish();
}

// Open the merged YO!info pane on a given sub-tab — used by the File menu, command palette, the topbar
// activity button, and the boot deep-link for legacy ?…=yoagent / __yoagent__ references.
async function openInfoSubTab(tab) {
  infoPanelSubTab = normalizedInfoSubTab(tab);
  writeStoredInfoSubTab(infoPanelSubTab);
  await selectSession(infoItemId);
  applyInfoSubTab();
  if (infoPanelSubTab === 'yoagent') {
    showYoagentStartupInfoOnce();
    renderYoagentPanel({preserveDraft: true, focusInput: true});
    loadYoagentConversation({silent: true});
    loadYoagentJobs({silent: true});
    refreshActivitySummary({silent: true});
    prewarmYoagent();
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
  setTimeout(() => setInfoSubTab('yoagent', {focusChat: true}), 80);
}

function splitInfoItemToRightPane(sourceSlot = null) {
  const next = layoutWithoutItem(infoItemId);
  const root = next[layoutTreeKey] || legacyLayoutTree(next);
  if (!root) {
    const targetSlot = sourceSlot || slotForNewSession();
    next[layoutTreeKey] = leafNode(targetSlot);
    next[targetSlot] = paneStateWithTabs([infoItemId], infoItemId);
    applyLayoutSlots(next, {focusSession: infoItemId, prune: false});
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([infoItemId], infoItemId);
  next[layoutTreeKey] = splitNode('row', root, leafNode(newSlot), splitPercentForNewItem(infoItemId, 'right'));
  applyLayoutSlots(next, {focusSession: infoItemId, prune: false});
}

function openYoagentRightPane() {
  const sourceSlot = slotForSession(infoItemId);
  const targetSlot = rightmostExistingPaneSlot();
  if (targetSlot) {
    if (sourceSlot === targetSlot) {
      activatePaneTab(targetSlot, infoItemId);
    } else {
      moveSessionToSlot(infoItemId, targetSlot, sourceSlot, paneTabs(targetSlot).length);
    }
  } else {
    splitInfoItemToRightPane(sourceSlot);
  }
  setInfoSubTab('yoagent', {focusChat: true});
  focusYoagentChatSoon();
}

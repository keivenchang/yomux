// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Development reload, modal, global shortcut, and final terminal boot wiring.

// Dev-velocity #1b: in --dev mode, reload the page when the static bundle changes (ends the recurring
// "is the bundle stale?" misdiagnoses). Listens to the server's /api/dev-reload SSE 'reload' event;
// no-op outside dev mode. The EventSource auto-reconnects across the backend re-exec (#1c).
function installDevAutoReload() {
  if (!devMode || typeof EventSource === 'undefined' || devAutoReloadSource) return;
  try {
    const revision = encodeURIComponent(String(bootstrap.devBundleRevision || ''));
    devAutoReloadSource = new EventSource(`/api/dev-reload?bundle_revision=${revision}`);
  } catch (_error) {
    devAutoReloadSource = null;
    return;
  }
  const source = devAutoReloadSource;
  source.addEventListener('ready', event => {
    // A client reconnects after a server restart, which means it misses the old process's
    // `reload` event. The fresh server's revision makes that stale bundle observable at once.
    const serverRevision = String(safeJsonParse(event.data, {})?.signature || '');
    const bootRevision = String(bootstrap.devBundleRevision || '');
    if (serverRevision && bootRevision && serverRevision !== bootRevision) location.reload();
  });
  source.addEventListener('reload', () => {
    statusOk(localizedHtml('status.devBundleReloading'));
    location.reload();
  });
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const body = document.getElementById('modalBody');
  modal.classList.remove('about-open');
  body.innerHTML = '';
  modal.dataset.modalKind = 'context';
  modal.dataset.modalSession = session;
  body.dataset.localeTextKey = 'common.loading';
  modal.classList.add(CLS.open);
  relocalizeModalChrome();
  try {
    const payload = await apiFetchJson(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    renderContextTailPayload(session, payload);
  } catch (error) {
    if (isApiPendingResponse(error)) return;
    delete body.dataset.localeTextKey;
    body.textContent = userMessageText(error, t('common.requestFailed'));
  }
}

function relocalizeModalChrome(options = {}) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  const close = document.getElementById('closeModal');
  const closeLabel = t('common.close');
  if (close) {
    close.title = closeLabel;
    close.setAttribute('aria-label', closeLabel);
  }
  if (!modal || options.content === false || !modal.classList.contains(CLS.open)) return Boolean(modal);
  if (modal.classList.contains('about-open')) {
    showAboutModal();
    return true;
  }
  if (modal.dataset.modalKind !== 'context') return true;
  if (title) title.textContent = t('transcript.tailTitle', {session: sessionLabel(modal.dataset.modalSession || '')});
  if (body?.dataset.localeTextKey) body.textContent = t(body.dataset.localeTextKey);
  return true;
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
  // Cmd/Ctrl+B is one atomic triplet transaction. The layout owner owns its saved placement and
  // default-home restoration; terminal code only preserves the platform/terminal eligibility gate.
  if (typeof toggleAllFileSurfaces === 'function') return toggleAllFileSurfaces();
  console.warn('file surface shortcut is unavailable until the layout triplet owner is ready');
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

registerTerminalRuntimeFacade('panel', {
  createPanel,
  panelControlsHtml,
  relocalizeTerminalPanelChrome,
});
registerTerminalRuntimeFacade('transport', {
  connectTerminalSocket,
  installClientEventStream,
  startSummaryStream,
  startTranscriptStream,
});
registerTerminalRuntimeFacade('boot', {
  boot,
  installDevAutoReload,
  installReconnectResyncHandlers,
});
if (refreshMeta) {
  refreshMetaButtonChrome();
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
  modal.classList.remove(CLS.open, 'about-open');
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
document.addEventListener('pointerdown', event => {
  if (event.target?.closest?.('.app-menu')) return;
  closeAppMenus();
}, true);
topbar?.addEventListener('pointerenter', () => {
  closeOtherSessionPopovers(null, {force: true});
  closeFileImagePreview();
});

function focusedPanelSearchTarget(event, item) {
  const direct = event.target?.closest?.('[data-layout-item]');
  if (direct?.dataset?.layoutItem === item && direct.offsetParent !== null) return direct;
  const registered = panelNodes.get(item);
  if (registered?.offsetParent !== null) return registered;
  return Array.from(document.querySelectorAll('[data-layout-item]'))
    .find(panel => panel.dataset.layoutItem === item && panel.offsetParent !== null) || null;
}

function handleFocusedPanelSearchShortcut(event, {mod = appModifier(event), key = String(event.key || '').toLowerCase()} = {}) {
  if (!mod || event.shiftKey || key !== 'f') return false;
  const item = focusedPanelItem;
  const focusSearch = tabTypeForItem(item)?.focusSearch;
  if (typeof focusSearch !== 'function') return false;
  const panel = focusedPanelSearchTarget(event, item);
  if (!panel) return false;
  // The tab-type registry owns which panels have an app find control. This single dispatcher keeps
  // Cmd/Ctrl-F aligned across those panels while leaving native Find intact elsewhere.
  event.preventDefault();
  event.stopPropagation();
  Promise.resolve(focusSearch(item, panel)).catch(error => console.warn('panel search shortcut failed', error));
  return true;
}

function handleGlobalShortcutKeydown(event) {
  if (handleFocusedTerminalCopyShortcut(event)) return;
  const focusedTerminalItem = focusedTerminal ? terminals.get(focusedTerminal) : null;
  // OpenCode documents Ctrl+Alt+B/F as native message navigation. The exact-pane check prevents
  // an unknown or ambiguous terminal from changing the shortcut behavior of other clients.
  if (terminalOpenCodeNativeShortcut(focusedTerminal, focusedTerminalItem?.container, event)) return;
  // C10: the Finder tree claims Command-Delete (Mac) / Delete (PC) to delete the selected file(s) before
  // the global Mod+Delete tab-close fallback can fire.
  if (handleFileExplorerDeleteShortcut(event)) return;
  // File Explorer / Finder-style keyboard traversal of the Finder/Differ selection (Arrow + Shift+Arrow,
  // Home/End, Mod+A) — claimed before the global shortcuts so arrows move the file selection when the
  // Finder/Differ is the active surface.
  if (handleFileExplorerArrowNav(event)) return;
  const mod = appModifier(event);
  const key = String(event.key || '').toLowerCase();
  if (handleFocusedPanelSearchShortcut(event, {mod, key})) return;
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
    if (key === 'k' && event.shiftKey) {
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
registerTerminalRuntimeFacade('shortcuts', {
  handleFocusedPanelSearchShortcut,
  handleGlobalShortcutKeydown,
  handlePendingGlobalShortcutChord,
});
installTerminalResizeAuthorityHandlers();
window.addEventListener('keydown', handleGlobalShortcutKeydown, true);
window.addEventListener(APP_VIEWPORT_CHANGE_EVENT, () => {
  // Safari can publish the new viewport before its topbar flex geometry has settled. The shared
  // fit check runs on the next frame (and the ResizeObserver covers a later width update), so the
  // full/compact menu decision is based only on current space, never the previous presentation.
  scheduleTopbarNavigationFitCheck();
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  if (typeof dockviewScheduleLayoutToHost === 'function') dockviewScheduleLayoutToHost();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

pageLoadProfileState.bundleEvalEndedAt = performanceNow();
boot();

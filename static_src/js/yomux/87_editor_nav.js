// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Recency belongs to tab instances for this page lifetime; traversal only moves the cursor.
function recordEditorNav(item) {
  if (!item || editorNav.navigating) return;
  editorNav.pendingFocus = null;
  editorNav.visitGeneration += 1;
  editorNav.stack = editorNav.stack.filter(entry => entry !== item);
  editorNav.stack.push(item);
  editorNav.index = editorNav.stack.length - 1;
  updateEditorNavButtons();
}

function replaceEditorNavItems(replacements) {
  const previousFocus = focusedPanelItem || editorNav.focusedItem;
  const replacementFocus = replacements.get(previousFocus);
  if (replacementFocus && panelNodes.get(previousFocus)?.contains(document.activeElement)) {
    editorNav.pendingFocus = {item: replacementFocus, generation: nextTabSwitchHighlightGeneration()};
  }
  const current = editorNav.stack[editorNav.index];
  editorNav.stack = [...new Set(editorNav.stack.map(item => replacements.get(item) || item))];
  editorNav.index = editorNav.stack.indexOf(replacements.get(current) || current);
  editorNav.focusedItem = replacements.get(editorNav.focusedItem) || editorNav.focusedItem;
  editorNav.liveItems = new Set([...editorNav.liveItems].map(item => replacements.get(item) || item));
  if (replacements.has(focusedPanelItem)) setFocusedPanelItem(replacements.get(focusedPanelItem));
  updateEditorNavButtons();
}

// Reconcile at layout commit: pure removal helpers are also used to move tabs.
function reconcileEditorNav(previousFocus = focusedPanelItem || editorNav.focusedItem, removedItems = [], slots = layoutSlots) {
  const items = paneItems(slots);
  // Normal Tabber keeps open tabs registered while laying out only the selected item.
  const removed = new Set(removedItems);
  const registeredItems = tabberIsAdvanced() ? items : [...items, ...layoutItems];
  const live = new Set(registeredItems.filter(item => !removed.has(item)));
  const current = editorNav.stack[editorNav.index];
  const removedEntries = [...editorNav.liveItems].some(item => !live.has(item));
  editorNav.liveItems = live;
  const preserveContentFocus = removedEntries && (panelNodes.get(previousFocus)?.contains(document.activeElement)
    || document.activeElement?.closest?.('[data-pane-tab-close], .session-context-menu'));
  editorNav.stack = editorNav.stack.filter(item => live.has(item));
  editorNav.index = editorNav.stack.indexOf(current);
  const removedFocus = previousFocus && !live.has(previousFocus);
  const target = removedFocus
    ? editorNav.stack[editorNav.stack.length - 1] || items[0]
    : previousFocus || activePaneItems(slots)[0] || items[0];
  if (!editorNav.stack.length && target) {
    editorNav.stack.push(target);
    editorNav.index = 0;
  }
  if (removedFocus && target) {
    editorNav.index = editorNav.stack.indexOf(target);
    const side = slotForItem(target, slots);
    if (side) slots[side].active = target;
  }
  if (target && (removedFocus || preserveContentFocus)) {
    editorNav.pendingFocus = {item: target, generation: nextTabSwitchHighlightGeneration()};
  }
  editorNav.focusedItem = target || null;
  updateEditorNavButtons();
  return removedFocus ? target : null;
}

function focusEditorNavItem(item) {
  if (!item || !itemInLayout(item) || !itemIsActivePaneTab(item)) return;
  setFocusedPanelItem(item);
  focusPanel(item, {focusContent: true});
}

function scheduleEditorNavContentFocus(item) {
  const generation = tabSwitchHighlightGeneration;
  const visitGeneration = editorNav.visitGeneration;
  const focus = () => {
    const active = document.activeElement;
    const panel = panelNodes.get(item);
    if (generation === tabSwitchHighlightGeneration && visitGeneration === editorNav.visitGeneration
        && editorNav.focusedItem === item && (active === document.body || panel?.contains(active))) focusEditorNavItem(item);
  };
  scheduleTabSwitchPhaseTwo(generation, focus);
}

function finishEditorNavFocus() {
  const pending = editorNav.pendingFocus;
  editorNav.pendingFocus = null;
  if (pending && pending.generation === tabSwitchHighlightGeneration) {
    focusEditorNavItem(pending.item);
    scheduleEditorNavContentFocus(pending.item);
  }
}

function activateNavItem(item) {
  if (!tabberIsAdvanced() && isLayoutItem(item) && !itemInLayout(item)) {
    storeTabberNormalSelections([item], {syncLayout: false});
    applyTabberNormalSelectionLayout(item, {userInitiated: false});
  }
  const side = slotForItem(item);
  if (!side) return false;
  nextTabSwitchHighlightGeneration();
  activatePaneTab(side, item);
  focusEditorNavItem(item);
  scheduleEditorNavContentFocus(item);
  return true;
}

async function editorNavGo(delta) {
  reconcileEditorNav();
  const index = editorNav.index + delta;
  if (index < 0 || index >= editorNav.stack.length) return;
  // Activation is synchronous. Never suppress a newer click across an await.
  const item = editorNav.stack[index];
  editorNav.navigating = true;
  try {
    if (activateNavItem(item)) editorNav.index = index;
  } finally {
    editorNav.navigating = false;
  }
  updateEditorNavButtons();
}

function editorNavBack() { return editorNavGo(-1); }
function editorNavForward() { return editorNavGo(1); }

// The back/forward control lives in the GLOBAL TOPBAR (left of the search bar), not per editor pane —
// it's one global file-history control, like a browser's. Always visible; disabled at the ends.
function updateEditorNavButtons() {
  const back = document.getElementById('topbarNavBack');
  const forward = document.getElementById('topbarNavForward');
  if (back) back.disabled = editorNav.index <= 0;
  if (forward) forward.disabled = editorNav.index >= editorNav.stack.length - 1;
}

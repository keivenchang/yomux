function layoutWithoutItemFromSlots(item, slots = layoutSlots, options = {}) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = slots?.[layoutTreeKey] || null;
  const preserveEmptySlot = options.preserveEmptySlot || null;
  const preserveRemovedSlot = options.preserveRemovedSlot === true;
  const preservePlaceholders = options.preservePlaceholders !== false;
  for (const side of layoutSlotKeys(slots)) {
    if (paneIsPlaceholder(side, slots)) {
      if (preservePlaceholders) next[side] = emptyPlaceholderPaneState();
      continue;
    }
    const hadItem = paneTabs(side, slots).includes(item);
    const active = activeItemForSide(side, slots);
    const tabs = paneTabs(side, slots).filter(value => value !== item);
    next[side] = !tabs.length && (side === preserveEmptySlot || (preserveRemovedSlot && hadItem))
      ? emptyPlaceholderPaneState()
      : paneStateWithTabs(tabs, active === item ? null : active);
  }
  return next;
}

function layoutWithoutItem(item, options = {}) {
  return layoutWithoutItemFromSlots(item, layoutSlots, options);
}

function fileExplorerHiddenStatusMessage() {
  return t('finder.hiddenStatus', {name: fileExplorerLabel(), keys: appShortcutText('B')});
}

function hiddenFromLayoutStatusMessage(item) {
  return isFileExplorerItem(item) ? fileExplorerHiddenStatusMessage() : `${itemLabel(item)} hidden from layout`;
}

function removeSessionFromLayout(item, options = {}) {
  if (!itemInLayout(item)) return;
  const isFiles = isFileExplorerItem(item);
  if (isFiles) rememberFileExplorerOpenIntent(false);
  applyLayoutSlots(layoutWithoutItem(item, {
    preserveRemovedSlot: !isFiles,
    preservePlaceholders: !isFiles,
  }), {
    message: options.message || hiddenFromLayoutStatusMessage(item),
  });
}

function removePaneFromLayout(item) {
  const slot = slotForSession(item);
  if (!slot) return;
  const moved = paneTabs(slot);
  if (moved.includes(fileExplorerItemId)) rememberFileExplorerOpenIntent(false);
  applyLayoutSlots(layoutWithoutSlot(slot, {preserveRemovedSlot: shouldPreserveClosedPaneSlot(slot)}), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} hidden from layout` : '',
  });
}

function shouldPreserveClosedPaneSlot(slot) {
  if (!slot || isFileExplorerItem(activeItemForSide(slot))) return false;
  return layoutSlotKeys().some(side => side !== slot && isFileExplorerItem(activeItemForSide(side)));
}

function layoutWithoutSlot(slot, options = {}) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (side === slot) {
      if (options.preserveRemovedSlot === true) next[side] = emptyPlaceholderPaneState();
      continue;
    }
    next[side] = paneStateForLayoutSlot(side);
  }
  return next;
}

function appendUniqueItems(target, items) {
  for (const item of items) {
    if (isLayoutItem(item) && !target.includes(item)) target.push(item);
  }
  return target;
}

// Per-pane tab cap (LRU eviction). Records when a tab was last activated so the oldest UNUSED tab
// can auto-close once a pane grows past appearance.max_tabs_per_pane.
function recordTabActivation(item) {
  if (isLayoutItem(item)) tabLastActivatedAt.set(item, Date.now());
}

function maxTabsPerPane() {
  const raw = Math.floor(Number(initialSetting('appearance.max_tabs_per_pane', 10)));
  if (!Number.isFinite(raw)) return 10;
  return Math.max(2, Math.min(30, raw));
}

// A tab may be auto-evicted unless it is the one being kept active, pinned, the Finder dock,
// or a dirty/unsaved editor (never silently drop unsaved edits).
function tabIsEvictableForCap(item, keepItem) {
  if (item === keepItem || tabIsPinned(item) || isFileExplorerItem(item)) return false;
  if (isFileEditorItem(item)) {
    const state = openFiles.get(fileItemPath(item));
    if (state && state.dirty) return false;
  }
  return true;
}

// Given a pane's tab list (newest tab already inserted) return the least-recently-used evictable
// tabs to drop so the pane fits the cap. Keeps keepItem, dirty editors, and the Finder.
function tabsToEvictForCap(tabs, keepItem) {
  const cap = maxTabsPerPane();
  const overflow = tabs.length - cap;
  if (overflow <= 0) return [];
  const evictable = tabs.filter(item => tabIsEvictableForCap(item, keepItem));
  if (!evictable.length) return [];
  const byLeastRecent = [...evictable].sort((a, b) => (tabLastActivatedAt.get(a) || 0) - (tabLastActivatedAt.get(b) || 0));
  return byLeastRecent.slice(0, Math.min(overflow, byLeastRecent.length));
}

function paneTabsWithoutFinder(slot, slots = layoutSlots) {
  return paneTabs(slot, slots).filter(item => !isFileExplorerItem(item));
}

function canPaneExpand(item, slots = layoutSlots) {
  const targetSlot = slotForItem(item, slots);
  if (!targetSlot || isFileExplorerItem(activeItemForSide(targetSlot, slots))) return false;
  if (!activeItemForSide(targetSlot, slots)) return false;
  return layoutSlotKeys(slots).some(slot => (
    slot !== targetSlot
    && !isFileExplorerItem(activeItemForSide(slot, slots))
    && paneTabsWithoutFinder(slot, slots).length > 0
  ));
}

function minimizePaneFromLayout(item) {
  const sourceSlot = slotForSession(item);
  if (!sourceSlot) return;
  if (isFileExplorerItem(activeItemForSide(sourceSlot))) {
    removePaneFromLayout(item);
    return;
  }
  const minimizedTabs = paneTabsWithoutFinder(sourceSlot);
  const targetSlot = largestNonFileExplorerPaneSlot(new Set([sourceSlot]));
  if (!targetSlot || !minimizedTabs.length) {
    removePaneFromLayout(item);
    return;
  }
  const targetActive = activeItemForSide(targetSlot);
  const next = layoutWithoutSlot(sourceSlot, {preserveRemovedSlot: shouldPreserveClosedPaneSlot(sourceSlot)});
  const targetTabs = appendUniqueItems(paneTabsWithoutFinder(targetSlot, next), minimizedTabs);
  next[targetSlot] = paneStateWithTabs(targetTabs, targetActive);
  applyLayoutSlots(next, {
    focusSession: targetActive || targetTabs[0],
    prune: false,
    message: `${minimizedTabs.map(itemLabel).join(', ')} minimized`,
  });
}

function finderLeadsExpandedPane(finderSlot, targetSlot) {
  const finderRect = layoutColumnNode(finderSlot)?.getBoundingClientRect();
  const targetRect = layoutColumnNode(targetSlot)?.getBoundingClientRect();
  if (finderRect && targetRect && Math.abs(finderRect.left - targetRect.left) > 1) return finderRect.left < targetRect.left;
  const leaves = layoutLeafSlots(layoutSlots[layoutTreeKey]);
  const finderIndex = leaves.indexOf(finderSlot);
  const targetIndex = leaves.indexOf(targetSlot);
  if (finderIndex !== -1 && targetIndex !== -1) return finderIndex < targetIndex;
  return true;
}

function expandPaneFromLayout(item) {
  const targetSlot = slotForSession(item);
  if (!targetSlot || !canPaneExpand(item)) return;
  const active = activeItemForSide(targetSlot);
  if (!active) return;
  const finderSlot = slotForSession(fileExplorerItemId);
  const targetTabs = appendUniqueItems([], paneTabsWithoutFinder(targetSlot));
  for (const slot of layoutSlotKeys()) {
    if (slot === targetSlot) continue;
    appendUniqueItems(targetTabs, paneTabsWithoutFinder(slot));
  }
  const next = emptyLayoutSlots();
  next[targetSlot] = paneStateWithTabs(targetTabs, active);
  if (finderSlot && finderSlot !== targetSlot) {
    next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
    const finderFirst = finderLeadsExpandedPane(finderSlot, targetSlot);
    next[layoutTreeKey] = finderFirst
      ? splitNode('row', leafNode(finderSlot), leafNode(targetSlot), fileExplorerSplitPercent)
      : splitNode('row', leafNode(targetSlot), leafNode(finderSlot), 100 - fileExplorerSplitPercent);
  } else {
    next[layoutTreeKey] = leafNode(targetSlot);
  }
  applyLayoutSlots(next, {
    focusSession: active,
    prune: false,
    message: `${itemLabel(active)} expanded`,
  });
}

function visibleNonFinderPaneItems(slots = layoutSlots) {
  const result = [];
  for (const slot of layoutSlotKeys(slots)) appendUniqueItems(result, paneTabsWithoutFinder(slot, slots));
  return result;
}

function firstNonFinderPaneSlot(slots = layoutSlots) {
  return layoutSlotKeys(slots).find(slot => !isFileExplorerItem(activeItemForSide(slot, slots)) && paneTabsWithoutFinder(slot, slots).length > 0) || null;
}

function preferredNonFinderLayoutSlots(finderSlot = null) {
  return layoutSlotKeys().filter(slot => slot !== finderSlot && paneTabsWithoutFinder(slot).length > 0);
}

function layoutModeStatusMessage(mode) {
  const normalized = normalizeLayoutMode(mode);
  if (normalized === 'single') return 'single pane layout';
  if (normalized === 'split') return 'split layout';
  if (normalized === 'grid') return 'grid layout';
  return 'wall layout';
}

function applyNonFinderLayoutMode(mode) {
  const items = visibleNonFinderPaneItems();
  if (!items.length) return;
  const normalized = normalizeLayoutMode(mode);
  const active = items.includes(focusedPanelItem) ? focusedPanelItem : items[0];
  const finderSlot = slotForSession(fileExplorerItemId);
  const preserveSlots = normalized === 'single' || normalized === 'split';
  const next = layoutSlotsForItems(items, normalized, {
    active,
    finderSlot,
    preferredSlots: preserveSlots ? preferredNonFinderLayoutSlots(finderSlot) : [],
  });
  if (finderSlot) {
    next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
    const targetSlot = layoutLeafSlots(next[layoutTreeKey]).find(slot => slot !== finderSlot) || firstNonFinderPaneSlot() || 'right';
    const nonFinderTree = next[layoutTreeKey];
    const finderFirst = finderLeadsExpandedPane(finderSlot, targetSlot);
    next[layoutTreeKey] = finderFirst
      ? splitNode('row', leafNode(finderSlot), nonFinderTree, fileExplorerSplitPercent)
      : splitNode('row', nonFinderTree, leafNode(finderSlot), 100 - fileExplorerSplitPercent);
  }
  applyLayoutSlots(next, {focusSession: active, prune: false, message: layoutModeStatusMessage(normalized)});
}

function applyLayoutMode(mode) {
  applyNonFinderLayoutMode(normalizeLayoutMode(mode));
}

function setLayoutToSinglePane() {
  applyLayoutMode('single');
}

function setLayoutToSplitPanes() {
  applyLayoutMode('split');
}

function setLayoutToGridPanes() {
  applyLayoutMode('grid');
}

function setLayoutToWallPanes() {
  applyLayoutMode('wall');
}

function layoutWithFileExplorerDockedLeft(slots = layoutSlots, options = {}) {
  const rightRaw = layoutWithoutItemFromSlots(fileExplorerItemId, slots, {
    preservePlaceholders: options.preservePlaceholders !== false,
  });
  const right = options.preservePlaceholders === false && !paneItems(rightRaw).length
    ? rightRaw
    : compactLayoutSlots(rightRaw);
  const rightSlots = layoutSlotKeys(right).filter(slot => paneHasLayoutContent(slot, right));
  const next = emptyLayoutSlots();
  for (const slot of rightSlots) next[slot] = paneStateForLayoutSlot(slot, right);
  const used = new Set(rightSlots);
  const currentSlot = slotForItem(fileExplorerItemId, slots);
  const preferredSlot = options.preferredSlot && !used.has(options.preferredSlot) ? options.preferredSlot : null;
  let finderSlot = currentSlot && !used.has(currentSlot) ? currentSlot : null;
  if (!finderSlot) finderSlot = preferredSlot;
  if (!finderSlot) finderSlot = !used.has('left') ? 'left' : nextLayoutSlot(next);
  next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
  next[layoutTreeKey] = rightSlots.length
    ? splitNode('row', leafNode(finderSlot), right[layoutTreeKey], fileExplorerSplitPercent)
    : leafNode(finderSlot);
  return layoutWithFileExplorerPeerPane(compactLayoutSlots(next));
}

function dockFileExplorerPane() {
  rememberFileExplorerOpenIntent(true);
  applyLayoutSlots(layoutWithFileExplorerDockedLeft(), {
    focusSession: fileExplorerItemId,
    prune: false,
  });
}

function firstEmptyPane(slots = layoutSlots) {
  const placeholderSlot = layoutSlotKeys(slots).find(slot => paneIsPlaceholder(slot, slots));
  if (placeholderSlot) return placeholderSlot;
  return layoutSlotKeys(slots).length ? null : 'left';
}

function slotForNewSession() {
  const empty = firstEmptyPane();
  if (empty) return empty;
  const focusedSlot = focusedPanelItem ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return 'left';
}

function focusedActivationSlot() {
  const item = currentActiveMenuItem();
  const slot = item ? slotForSession(item) : null;
  return slot && !slotIsFileExplorerPane(slot) ? slot : null;
}

function fileEditorActivationSlot() {
  const focusedSlot = focusedActivationSlot();
  if (focusedSlot) return focusedSlot;
  const previousSlot = lastActiveNonFileExplorerPaneItem ? slotForSession(lastActiveNonFileExplorerPaneItem) : null;
  return previousSlot && !slotIsFileExplorerPane(previousSlot) ? previousSlot : null;
}

function slotForTabActivation(item) {
  const currentSlot = slotForSession(item);
  if (currentSlot) return currentSlot;
  return fileEditorActivationSlot() || largestNonFileExplorerPaneSlot() || firstEmptyPane() || largestPaneSlot() || slotForNewSession();
}

async function activateTabInExistingPane(item) {
  if (!isLayoutItem(item)) return;
  if (isTmuxSession(item)) {
    const ensured = await ensureSession(item);
    if (!ensured) return;
  }
  const targetSlot = slotForTabActivation(item);
  if (!targetSlot) return;
  const currentSlot = slotForSession(item);
  if (currentSlot === targetSlot) {
    activatePaneTab(targetSlot, item);
    return;
  }
  await moveSessionToSlot(item, targetSlot, currentSlot, paneTabs(targetSlot).length);
}

function filesOnlySlotForSession(session) {
  const filesSlot = slotForSession(fileExplorerItemId);
  if (!filesSlot) return null;
  const stack = paneTabs(filesSlot).filter(item => item !== session);
  return stack.length === 1 && stack[0] === fileExplorerItemId ? filesSlot : null;
}

function slotForNewTmuxSession(session) {
  const currentSlot = slotForSession(session);
  if (currentSlot) return currentSlot;
  const empty = firstEmptyPane();
  if (empty) return empty;
  const targetSlot = largestNonFileExplorerPaneSlot();
  if (targetSlot) return targetSlot;
  return filesOnlySlotForSession(session) || largestPaneSlot() || slotForNewSession();
}

async function placeTmuxSession(session) {
  const currentSlot = slotForSession(session);
  if (currentSlot) {
    activatePaneTab(currentSlot, session);
    return;
  }
  const targetSlot = slotForNewTmuxSession(session);
  if (!targetSlot) return;
  if (paneIsPlaceholder(targetSlot) || !paneTabs(targetSlot).length) {
    await moveSessionToSlot(session, targetSlot, null);
    return;
  }
  if (isFileExplorerItem(activeItemForSide(targetSlot))) {
    await splitSessionAtSlot(session, targetSlot, 'right', null, fileExplorerSplitPercent);
    return;
  }
  await moveSessionToSlot(session, targetSlot, null, paneTabs(targetSlot).length);
}

// File -> Finder toggles. Hide the Finder when it is already in the layout (same path as the
// Finder panel's close button), otherwise open/focus it. The menu's `checked` state tracks this.
function toggleFinderPane() {
  if (itemInLayout(fileExplorerItemId)) {
    removeSessionFromLayout(fileExplorerItemId);
    return false;
  }
  selectSession(fileExplorerItemId);
  return true;
}

async function openFileExplorerPane() {
  rememberFileExplorerOpenIntent(true);
  const currentSlot = slotForSession(fileExplorerItemId);
  if (currentSlot) {
    if (paneTabs(currentSlot).length === 1 && !fileExplorerNeedsLeftDock()) {
      activatePaneTab(currentSlot, fileExplorerItemId);
      return;
    }
    dockFileExplorerPane();
    return;
  }
  const empty = firstEmptyPane();
  if (empty) {
    if (layoutSlotKeys().includes(empty) && paneIsPlaceholder(empty)) {
      await splitSessionBesidePlaceholder(fileExplorerItemId, empty, 'left', fileExplorerSplitPercent);
    } else {
      await moveSessionToSlot(fileExplorerItemId, empty, null);
    }
    return;
  }
  const targetSlot = largestPaneSlot();
  if (targetSlot && paneTabs(targetSlot).length) {
    dockFileExplorerPane();
    return;
  }
  await moveSessionToSlot(fileExplorerItemId, slotForNewSession(), null);
}

async function splitSessionBesidePlaceholder(session, targetSlot, zone, pct = defaultSplitPercent) {
  if (!isLayoutItem(session) || !targetSlot || !['top', 'bottom', 'left', 'right'].includes(zone)) return;
  if (!paneIsPlaceholder(targetSlot)) {
    await splitSessionAtSlot(session, targetSlot, zone, null, pct);
    return;
  }
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyPlaceholderPaneState();
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const existingNode = leafNode(targetSlot);
  const newNode = leafNode(newSlot);
  const splitPct = splitPercentForNewItem(session, zone, pct);
  const replacement = zone === 'right' || zone === 'bottom'
    ? splitNode(direction, existingNode, newNode, splitPct)
    : splitNode(direction, newNode, existingNode, splitPct);
  next[layoutTreeKey] = replaceLayoutLeaf(next[layoutTreeKey], targetSlot, replacement);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

// C12 F1: is this session's terminal already attached (socket open/connecting)? A live pane does not need
// the blocking /api/ensure-session round-trip (two tmux subprocesses) on a move — it is already running.
function sessionTerminalIsLive(session) {
  const readyState = terminals.get(session)?.socket?.readyState;
  return readyState === WebSocket.OPEN || readyState === WebSocket.CONNECTING;
}

async function moveSessionToSlot(session, targetSlot, sourceSlot = null, insertIndex = 0) {
  if (!isLayoutItem(session) || !targetSlot) return;
  // C12 F1: only pay the ensure-session round-trip when the pane is NOT already running. For a live pane
  // (the common left<->right move) apply the layout optimistically; applyLayoutSlots -> ensureTerminalRunning
  // still reconciles/recovers if the session turns out to be gone (its socket would no longer be live).
  if (isTmuxSession(session) && !sessionTerminalIsLive(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyPaneState();
  const tabs = next[targetSlot].tabs;
  const index = Math.max(0, Math.min(Number.isFinite(insertIndex) ? insertIndex : 0, tabs.length));
  tabs.splice(index, 0, session);
  recordTabActivation(session);
  // Enforce the per-pane tab cap: if this pane is now over the limit, auto-close the least-recently
  // used tabs (keeping the new active tab, the Finder, and any dirty editors).
  const evicted = tabsToEvictForCap(tabs, session);
  const keptTabs = evicted.length ? tabs.filter(item => !evicted.includes(item)) : tabs;
  next[targetSlot] = paneStateWithTabs(keptTabs, session);
  applyLayoutSlots(next, {
    focusSession: session,
    prune: false,
    message: evicted.length ? `${evicted.map(itemLabel).join(', ')} auto-closed (tab limit ${maxTabsPerPane()})` : '',
  });
}

async function dropSessionWithIntent(session, intent, sourceSlot = null) {
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
    return;
  }
  if (intent?.boundary === 'gutter') {
    await splitSessionAtGutter(session, intent.splitPath, intent.zone, sourceSlot);
    return;
  }
  if (intent?.boundary === 'root') {
    await splitSessionAtLayoutBoundary(session, intent.zone, sourceSlot);
    return;
  }
  if (!intent?.targetSlot || intent.zone === 'middle') {
    await moveSessionToSlot(session, intent?.targetSlot || slotForNewSession(), sourceSlot);
    return;
  }
  await splitSessionAtSlot(session, intent.targetSlot, intent.zone, sourceSlot);
}

function splitPercentForNewItem(session, zone, pct = null) {
  if (pct !== null && pct !== undefined && pct !== '' && Number.isFinite(Number(pct))) return Number(pct);
  if (isFileExplorerItem(session) && (zone === 'left' || zone === 'right')) {
    return zone === 'left' ? fileExplorerSplitPercent : 100 - fileExplorerSplitPercent;
  }
  return defaultSplitPercent;
}

function shouldPreserveSourceSlotForSplit(sourceSlot, targetSlot) {
  return Boolean(sourceSlot && sourceSlot !== targetSlot && isFileExplorerItem(activeItemForSide(targetSlot)));
}

function dockedFileExplorerRootSplit(node, slots = layoutSlots) {
  if (!node || node.split !== 'row' || !Array.isArray(node.children) || node.children.length !== 2) return null;
  const finderIndex = node.children.findIndex(child => child?.slot && isFileExplorerItem(activeItemForSide(child.slot, slots)));
  if (finderIndex < 0) return null;
  return {
    finderIndex,
    contentIndex: finderIndex === 0 ? 1 : 0,
    pct: Number.isFinite(Number(node.pct)) ? Number(node.pct) : fileExplorerSplitPercent,
  };
}

function splitRootPreservingDockedFileExplorer(root, newNode, session, zone, pct = null, slots = layoutSlots) {
  const docked = (zone === 'top' || zone === 'bottom') ? dockedFileExplorerRootSplit(root, slots) : null;
  if (!docked) return null;
  const children = [...(root.children || [])];
  const contentNode = children[docked.contentIndex];
  if (!contentNode) return null;
  const splitPct = splitPercentForNewItem(session, zone, pct);
  children[docked.contentIndex] = zone === 'bottom'
    ? splitNode('column', contentNode, newNode, splitPct)
    : splitNode('column', newNode, contentNode, splitPct);
  return splitNode('row', children[0], children[1], docked.pct);
}

async function splitSessionAtSlot(session, targetSlot, zone, sourceSlot = null, pct = null) {
  if (!isLayoutItem(session) || !targetSlot || !['top', 'bottom', 'left', 'right'].includes(zone)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session, {preserveEmptySlot: shouldPreserveSourceSlotForSplit(sourceSlot, targetSlot) ? sourceSlot : null});
  const targetTabs = paneTabs(targetSlot, next);
  if (!targetTabs.length) {
    await moveSessionToSlot(session, targetSlot, sourceSlot);
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const existingNode = leafNode(targetSlot);
  const newNode = leafNode(newSlot);
  const splitPct = splitPercentForNewItem(session, zone, pct);
  const replacement = zone === 'right' || zone === 'bottom'
    ? splitNode(direction, existingNode, newNode, splitPct)
    : splitNode(direction, newNode, existingNode, splitPct);
  next[layoutTreeKey] = replaceLayoutLeaf(next[layoutTreeKey], targetSlot, replacement);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

async function splitSessionAtLayoutBoundary(session, zone, sourceSlot = null, pct = null) {
  if (!isLayoutItem(session) || !layoutSplitZone(zone)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  const root = next[layoutTreeKey] || legacyLayoutTree(next);
  if (!root) {
    await moveSessionToSlot(session, sourceSlot || slotForNewSession(), sourceSlot);
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const newNode = leafNode(newSlot);
  const direction = layoutSplitDirectionForZone(zone);
  const splitPct = splitPercentForNewItem(session, zone, pct);
  next[layoutTreeKey] = splitRootPreservingDockedFileExplorer(root, newNode, session, zone, pct, next)
    || (zone === 'right' || zone === 'bottom'
    ? splitNode(direction, root, newNode, splitPct)
    : splitNode(direction, newNode, root, splitPct));
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

async function splitSessionAtGutter(session, splitPath, zone, sourceSlot = null, pct = null) {
  if (!isLayoutItem(session) || !layoutSplitZone(zone)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  const root = next[layoutTreeKey] || legacyLayoutTree(next);
  const target = layoutNodeAtPath(splitPath, root);
  if (!root || !target?.children?.length) {
    await splitSessionAtLayoutBoundary(session, zone, sourceSlot, pct);
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const replacement = splitLayoutNodeAtGutter(target, leafNode(newSlot), session, zone, pct);
  next[layoutTreeKey] = replaceLayoutNodeAtPath(root, splitPath, replacement);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

function layoutSplitZone(zone) {
  return zone === 'top' || zone === 'bottom' || zone === 'left' || zone === 'right';
}

function layoutSplitDirectionForZone(zone) {
  return zone === 'left' || zone === 'right' ? 'row' : 'column';
}

function splitLayoutNodeAtGutter(node, newNode, session, zone, pct = null) {
  const direction = node.split === 'column' ? 'column' : 'row';
  const first = node.children?.[0] || null;
  const second = node.children?.[1] || null;
  if (!first || !second) return newNode;
  const insertAfterFirst = direction === 'row' ? zone === 'left' : zone === 'top';
  if (insertAfterFirst) {
    const nestedZone = direction === 'row' ? 'right' : 'bottom';
    return splitNode(direction, splitNode(direction, first, newNode, splitPercentForNewItem(session, nestedZone, pct)), second, node.pct);
  }
  const nestedZone = direction === 'row' ? 'left' : 'top';
  return splitNode(direction, first, splitNode(direction, newNode, second, splitPercentForNewItem(session, nestedZone, pct)), node.pct);
}

function replaceLayoutLeaf(node, slot, replacement) {
  if (!node) return replacement;
  if (node.slot) return node.slot === slot ? replacement : node;
  const children = (node.children || []).map(child => replaceLayoutLeaf(child, slot, replacement));
  return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
}

function replaceLayoutNodeAtPath(node, path, replacement) {
  if (!path) return replacement;
  if (!node?.children) return node;
  const parts = String(path).split('.');
  const index = Number(parts[0]);
  if (!Number.isInteger(index) || index < 0 || index > 1) return node;
  const children = [...node.children];
  children[index] = replaceLayoutNodeAtPath(children[index], parts.slice(1).join('.'), replacement);
  return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
}

function activatePaneTab(side, session, options = {}) {
  if (!layoutSlotKeys().includes(side) || !itemInLayout(session)) return;
  if (options.userInitiated === true && isTmuxSession(session)) {
    noteFileExplorerChangesSessionInteraction(session);
  }
  if (options.userInitiated === true && isFileEditorItem(session)) {
    const path = fileItemPath(session);
    const owners = openFileOwnerSessionsForPath(path);
    const owner = changedFileOwnerSessionForPath(path, {owners}) || (owners.length === 1 ? owners[0] : '');
    if (owner) noteFileExplorerChangesSessionInteraction(owner);
  }
  recordTabActivation(session);
  const previous = activeItemForSide(side);
  if (previous && previous !== session) capturePaneViewStateForItemIfPresent(previous);
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  // a user-initiated tab switch IS navigation. setFocusedPanelItem records the previous
  // focused item plus the newly activated tab so Back returns to the pane the user just left.
  setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
  if (!shareViewMode && isTmuxSession(session)) {
    const item = terminals.get(session);
    if (item?.term) sharePublish('host-resize', {session, rows: item.term.rows, cols: item.term.cols});
  }
  sharePublish('active-tab', {slot: side, item: session});
  scheduleShareTopologySnapshot('tab-activation');
  if (activeItemForSide(side) === session) {
    focusPanel(session, {userInitiated: options.userInitiated === true});
    return;
  }
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const key of layoutSlotKeys()) next[key] = paneStateForLayoutSlot(key);
  next[side].active = session;
  applyLayoutSlots(next, {focusSession: session});
  if (options.userInitiated && isTmuxSession(session)) focusTerminalFromUserAction(session, 25);
}

async function selectSession(session, options = {}) {
  if (options.userInitiated === true && isTmuxSession(session)) {
    noteFileExplorerChangesSessionInteraction(session);
  }
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
    scheduleFileExplorerActiveTabSync();
    return;
  }
  if (activeSessions.includes(session)) {
    focusPanel(session, {userInitiated: options.userInitiated === true});
    return;
  }
  if (isTmuxSession(session) && filesOnlySlotForSession(session)) {
    await placeTmuxSession(session);
    return;
  }
  await activateTabInExistingPane(session);
}

function sessionAgentKind(session) {
  const info = transcriptMeta.sessions?.[session];
  const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
  const kind = String(agent?.kind || '').toLowerCase();
  return kind === 'claude' || kind === 'codex' ? kind : '';
}

function agentIcon(kind, options = {}) {
  const name = agentLabel(kind);
  const label = options.label || name;
  const labelAttr = label ? ` aria-label="${esc(label)}" title="${esc(label)}"` : '';
  if (kind === 'codex') {
    return `<span class="agent-icon codex"${labelAttr}>${codexIcon()}</span>`;
  }
  if (kind === 'claude') {
    return `<span class="agent-icon claude"${labelAttr}>${claudeIcon()}</span>`;
  }
  return '';
}

function codexIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <path fill="#667ef8" d="M7.3 20.8c-3.1 0-5.7-2.4-5.9-5.5-.2-2.4 1.1-4.6 3.1-5.7C4.8 5.9 7.9 3 11.8 3c3.3 0 6.2 2.2 7 5.4 2.4.7 4 2.8 4 5.4 0 3.2-2.6 5.8-5.8 5.8-.9 1.1-2.2 1.8-3.8 1.8-1.2 0-2.3-.4-3.1-1.1-.8.3-1.8.5-2.8.5z"/>
    <path fill="#fff" d="M6.4 8.2c.5-.5 1.2-.5 1.7 0l2.8 2.8c.5.5.5 1.2 0 1.7l-2.8 2.8c-.5.5-1.2.5-1.7 0s-.5-1.2 0-1.7l1.9-1.9-1.9-1.9c-.5-.5-.5-1.3 0-1.8zM13 13.2h5.1c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2H13c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2z"/>
  </svg>`;
}

function claudeIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <rect width="24" height="24" rx="5.5" fill="#cf7554"/>
    <g fill="#fff7f1">
      <path d="M11.1 2.4h1.8l1.1 7.9-2 .6-2-.6 1.1-7.9z"/>
      <path d="m17.8 4.3 1.4 1.1-4.3 6.7-2.1-1.3 5-6.5z"/>
      <path d="m21.5 10.2.3 1.8-8.2 2-1-2.3 8.9-1.5z"/>
      <path d="m20.2 16.8-1.1 1.4-6.7-4.3 1.3-2.1 6.5 5z"/>
      <path d="m13.8 21.5-1.8.3-2-8.2 2.3-1 1.5 8.9z"/>
      <path d="m6.2 19.7-1.4-1.1 4.3-6.7 2.1 1.3-5 6.5z"/>
      <path d="m2.5 13.8-.3-1.8 8.2-2 1 2.3-8.9 1.5z"/>
      <path d="m3.8 7.2 1.1-1.4 6.7 4.3-1.3 2.1-6.5-5z"/>
      <circle cx="12" cy="12" r="2.2"/>
    </g>
  </svg>`;
}

function agentName(kind) {
  return kind === 'codex' ? 'Codex' : kind === 'claude' ? 'Claude' : kind === 'term' ? 'Term' : '';
}

function numericSessionName(session) {
  const match = String(session).match(/^[1-9]\d*$/);
  return match ? Number(match[0]) : null;
}

function sessionLabelAssignments() {
  const assigned = new Map();
  const used = new Set();
  for (const session of visibleSessions) {
    const numeric = numericSessionName(session);
    if (numeric !== null) {
      assigned.set(session, String(numeric));
      used.add(numeric);
    }
  }

  const backfill = [];
  for (let value = 9; value >= 1; value -= 1) {
    if (!used.has(value)) backfill.push(value);
  }

  let overflow = 10;
  for (const session of visibleSessions) {
    if (assigned.has(session)) continue;
    let label = backfill.length ? backfill.shift() : overflow;
    while (used.has(label)) label += 1;
    assigned.set(session, String(label));
    used.add(label);
    if (label >= overflow) overflow = label + 1;
  }
  return assigned;
}

function sessionForLabel(label) {
  const text = String(label);
  for (const [session, assignedLabel] of sessionLabelAssignments()) {
    if (assignedLabel === text) return session;
  }
  return null;
}

function sessionLabel(session) {
  const assigned = sessionLabelAssignments().get(session);
  if (assigned) return assigned;
  const numeric = numericSessionName(session);
  if (numeric !== null) return String(numeric);
  return String(session);
}

function shortText(value, limit = 96) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 3))}...`;
}

function shortBranch(value) {
  const text = String(value || '');
  if (text.length <= 46) return text;
  return `${text.slice(0, 18)}...${text.slice(-25)}`;
}

function linkHtml(url, label, title = '', className = '') {
  if (!url) return `<span>${esc(label)}</span>`;
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const classAttr = className ? ` class="${esc(className)}"` : '';
  return `<a href="${esc(url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr}${classAttr}>${esc(label)}</a>`;
}

function pullRequestStatusLabel(pr) {
  if (!pr) return '';
  if (pr.status_label) return pr.status_label;
  if (pr.source_only) return '';
  if (pr.draft) return 'draft';
  if (pr.merged || pr.merged_at) return 'merged';
  return pr.state || '';
}

function pullRequestStatusDisplay(pr) {
  const status = pullRequestStatusLabel(pr);
  if (!status) return '';
  const key = status.toLowerCase();
  if (key === 'unknown') return '';
  if (key === 'merged') return t('pr.status.merged');
  if (key === 'draft') return t('pr.status.draft');
  if (key === 'closed') return t('pr.status.closed');
  if (key === 'open') return t('pr.status.open');
  if (key.includes('failing')) return t('pr.status.failing');
  if (key.includes('pending')) return t('pr.status.pending');
  if (key.includes('passing')) return t('pr.status.passing');
  return status.replace(/\bci\b/gi, 'CI').toUpperCase();
}

function pullRequestIsMerged(pr) {
  return pullRequestStatusLabel(pr).toLowerCase() === 'merged';
}

function pullRequestInlineStatusDisplay(pr) {
  const key = pullRequestStatusLabel(pr).toLowerCase();
  if (key === 'merged' || key === 'open') return '';
  return pullRequestStatusDisplay(pr);
}

function pullRequestLinkLabel(pr) {
  const status = pullRequestInlineStatusDisplay(pr);
  return `#${pr.number}${status ? ` ${status}` : ''}`;
}

function pullRequestStatusClass(pr) {
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (status.includes('failing')) return 'pr-status-failing';
  if (status.includes('pending')) return 'pr-status-pending';
  if (status.includes('passing')) return 'pr-status-passing';
  if (status.includes('merged')) return 'pr-status-merged';
  if (status.includes('draft')) return 'pr-status-draft';
  if (status.includes('closed')) return 'pr-status-closed';
  return 'pr-status-unknown';
}

function pullRequestCiStatusClass(pr) {
  const state = String(pr?.checks?.state || '').toLowerCase();
  if (['success', 'passing', 'passed', 'green'].includes(state)) return 'pr-status-passing';
  if (['failure', 'failing', 'failed', 'red', 'error', 'cancelled', 'timed_out', 'action_required'].includes(state)) return 'pr-status-failing';
  if (['pending', 'queued', 'in_progress', 'running', 'waiting', 'requested'].includes(state)) return 'pr-status-pending';
  return pullRequestStatusClass(pr);
}

function pullRequestStatusIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (!['draft', 'closed'].includes(status)) return '';
  return `<span class="${metadataBadgeClasses(session, 'status', `ci-indicator tab-symbol ${pullRequestStatusClass(pr)}`)}">${esc(pullRequestStatusDisplay(pr))}</span>`;
}

function pullRequestCiIndicatorHtml(session, pr) {
  if (pullRequestIsMerged(pr)) return '';
  const state = pr?.checks?.state;
  if (!state || state === 'unknown') return '';
  return `<span class="${metadataBadgeClasses(session, 'ci', `ci-indicator tab-symbol ${pullRequestCiStatusClass(pr)}`)}">CI</span>`;
}

function pullRequestNumberIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  // No native title — the rich custom session popover already shows PR #, CI, and review state
  // (avoid a duplicate browser tooltip alongside the popover).
  const classes = pullRequestIsMerged(pr)
    ? metadataBadgeClasses(session, 'status', `ci-indicator tab-symbol pr-number-chip ${pullRequestStatusClass(pr)}`)
    : 'ci-indicator tab-symbol pr-number-chip';
  return `<span class="${esc(classes)}">#${esc(String(pr.number))}</span>`;
}

// #38: GitHub reviewDecision (APPROVED / CHANGES_REQUESTED / REVIEW_REQUIRED) per PR.
function pullRequestReviewDecision(pr) {
  return String(pr?.review_decision || '').toUpperCase();
}

function pullRequestApprovalClass(decision) {
  if (decision === 'APPROVED') return 'pr-review-approved';
  if (decision === 'CHANGES_REQUESTED') return 'pr-review-changes';
  if (decision === 'REVIEW_REQUIRED') return 'pr-review-required';
  return '';
}

function pullRequestApprovalLabel(decision) {
  if (decision === 'APPROVED') return t('pr.review.approvedShort');
  if (decision === 'CHANGES_REQUESTED') return t('pr.review.changesShort');
  if (decision === 'REVIEW_REQUIRED') return t('pr.review.reviewShort');
  return '';
}

function pullRequestApprovalIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  // No review badge once the PR is merged/closed (review state no longer actionable).
  if (['merged', 'closed'].includes(pullRequestStatusLabel(pr).toLowerCase())) return '';
  const decision = pullRequestReviewDecision(pr);
  const cls = pullRequestApprovalClass(decision);
  if (!cls) return '';
  const label = pullRequestApprovalLabel(decision);
  // No native title — the session popover carries the review state (no duplicate tooltip).
  return `<span class="${metadataBadgeClasses(session, 'review', `ci-indicator tab-symbol pr-review-chip ${cls}`)}">${esc(label)}</span>`;
}

// review status + reviewer(s) for the session popover PR row ("Approved by alice" /
// "Changes requested by bob" / "Review required"). Reuses the meta-pr-status color classes.
function pullRequestReviewInlineHtml(pr) {
  const decision = pullRequestReviewDecision(pr);
  if (!decision) return '';
  const reviewers = Array.isArray(pr?.review_reviewers) ? pr.review_reviewers : [];
  const loginsFor = state => reviewers
    .filter(reviewer => String(reviewer?.state || '').toUpperCase() === state)
    .map(reviewer => reviewer.login)
    .filter(Boolean);
  const by = logins => (logins.length ? t('pr.by', {logins: esc(logins.join(', '))}) : '');
  if (decision === 'APPROVED') return `<span class="meta-pr-status pr-status-passing">${esc(t('pr.approved'))}${by(loginsFor('APPROVED'))}</span>`;
  if (decision === 'CHANGES_REQUESTED') return `<span class="meta-pr-status pr-status-failing">${esc(t('pr.changesRequested'))}${by(loginsFor('CHANGES_REQUESTED'))}</span>`;
  if (decision === 'REVIEW_REQUIRED') return `<span class="meta-muted">${esc(t('pr.reviewRequired'))}</span>`;
  return '';
}

function pullRequestLinkHtml(pr) {
  return linkHtml(pr.url, pullRequestLinkLabel(pr), pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestAuthorHtml(pr) {
  const author = String(pr?.author_login || '').trim();
  return author ? `<span class="meta-muted">${esc(t('pr.authorBy', {author}))}</span>` : '';
}

function pullRequestColumnLinkHtml(pr) {
  return linkHtml(pr.url, pullRequestLinkLabel(pr), pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestChecksHtml(pr) {
  if (pullRequestIsMerged(pr)) return '';
  const checks = pr?.checks;
  if (!checks || !checks.state || checks.state === 'unknown') return '';
  const cls = pullRequestCiStatusClass(pr);
  const parts = [`<span class="meta-pr-status ${cls}">${esc(checks.summary || t('pr.checks.state', {state: checks.state}))}</span>`];
  const checkLinks = items => (items || []).map(item => (
    item?.name ? linkHtml(item.url || '', item.name, item.state || '') : ''
  )).filter(Boolean).join(', ');
  const failing = checkLinks(checks.failing);
  const pending = checkLinks(checks.pending);
  if (failing) parts.push(`<span class="meta-muted">${esc(t('pr.checks.failing'))}: ${failing}</span>`);
  if (pending) parts.push(`<span class="meta-muted">${esc(t('pr.checks.pending'))}: ${pending}</span>`);
  if (Number.isFinite(checks.total)) parts.push(`<span class="meta-muted">${esc(t('pr.checks.count', {count: checks.total}))}</span>`);
  return metaJoin(parts);
}

function panelFullPath(session, info) {
  const project = info?.project || {};
  const git = project.git;
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const nonHomePane = panes.find(pane => pane?.current_path && pane.current_path !== homePath && !['claude', 'codex'].includes(String(pane.command || '').toLowerCase()));
  if (nonHomePane?.current_path) return nonHomePane.current_path;
  if (git?.cwd) return git.cwd;
  if (git?.root) return git.root;
  if (info?.selected_pane?.current_path) return info.selected_pane.current_path;
  return '';
}

function compactHomePath(path) {
  const text = String(path || '').replace(/\/+$/, '');
  const home = String(homePath || '').replace(/\/+$/, '');
  if (!text || !home) return text;
  if (text === home) return '~';
  if (text.startsWith(`${home}/`)) return `~/${text.slice(home.length + 1)}`;
  return text;
}

function projectMetaHtml(session, info) {
  const project = info?.project || {};
  const repos = sessionRepoSummaries(info);
  const repoIndex = selectedSessionRepoIndex(session, info);
  const selectedRepo = repoIndex >= 0 ? repos[repoIndex] : null;
  const git = displayedSessionGit(session, info);
  const showingPrimaryGit = !selectedRepo || repoRootKey(project.git?.root) === repoRootKey(selectedRepo.root);
  const parts = [];
  const fullPath = selectedRepo?.cwd || selectedRepo?.root || panelFullPath(session, info);
  const repoSwitchHtml = repos.length > 1 ? (() => {
    const position = Math.max(0, repoIndex) + 1;
    const switchLabel = `${position}/${repos.length}`;
    return `<span class="meta-repo-switch" aria-label="${esc(t('detail.repos.switch', {position, count: repos.length}))}">
      <button type="button" class="meta-repo-cycle" data-repo-cycle="${esc(session)}" data-repo-cycle-dir="-1" title="${esc(t('detail.repos.previous'))}" aria-label="${esc(t('detail.repos.previous'))}">&lt;</button>
      <button type="button" class="meta-repo-chip" data-repo-chip="${esc(session)}" title="${esc(t('detail.repos.more', {count: repos.length - 1}))}" aria-label="${esc(t('detail.repos.switch', {position, count: repos.length}))}">${esc(switchLabel)}</button>
      <button type="button" class="meta-repo-cycle" data-repo-cycle="${esc(session)}" data-repo-cycle-dir="1" title="${esc(t('detail.repos.next'))}" aria-label="${esc(t('detail.repos.next'))}">&gt;</button>
    </span>`;
  })() : '';
  if (!git) {
    if (repoSwitchHtml) parts.push(repoSwitchHtml);
    if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
    parts.push(`<span class="meta-muted">${esc(t('git.noCheckout'))}</span>`);
    return metaJoin(parts);
  }
  const pr = displayPullRequest(info);
  if (repoSwitchHtml) parts.push(repoSwitchHtml);
  if (showingPrimaryGit && pr?.number) parts.push(pullRequestLinkHtml(pr));
  if (git.branch) parts.push(`<span class="meta-branch">${esc(shortBranch(git.branch))}</span>`);
  if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(`<span class="meta-muted">${esc(t('git.behind', {count: git.behind}))}</span>`);
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(`<span class="meta-muted">${esc(t('git.ahead', {count: git.ahead}))}</span>`);
  if (Number.isFinite(git.dirty_count) && git.dirty_count > 0) parts.push(`<span class="meta-muted">${esc(t('git.dirty', {count: git.dirty_count}))}</span>`);
  if (showingPrimaryGit && pr?.number) {
    if (!pullRequestIsMerged(pr) && pr.checks?.state && pr.checks.state !== 'unknown') {
      parts.push(`<span class="meta-pr-status ${pullRequestCiStatusClass(pr)}">${esc(pr.checks.summary || pullRequestStatusLabel(pr))}</span>`);
    }
  }
  if (showingPrimaryGit) {
    for (const issue of project.linear || []) {
      const state = issue.state ? ` ${issue.state}` : '';
      parts.push(linkHtml(issue.url, `${issue.identifier}${state}`, issue.title || ''));
    }
    const desc = pr?.title || pr?.description || (project.linear || []).find(issue => issue.title)?.title || '';
    if (desc) parts.push(`<span class="meta-desc">${esc(shortText(desc, 160))}</span>`);
  }
  return parts.length ? metaJoin(parts) : `<span class="meta-muted">${esc(t('git.checkoutDetected'))}</span>`;
}

// C9: popover listing every repo a session touches (focused first), each row: path, branch, dirty,
// ahead/behind. Clicking a row scopes the Finder to that repo. Reuses the shared context-menu controller.
function repoChipMenuRowHtml(repo) {
  const label = compactHomePath(repo.root || '');
  const bits = [];
  if (repo.branch) bits.push(`<span class="meta-branch">${esc(shortBranch(repo.branch))}</span>`);
  if (Number.isFinite(repo.dirty_count) && repo.dirty_count > 0) bits.push(`<span class="meta-muted">${esc(t('git.dirty', {count: repo.dirty_count}))}</span>`);
  if (Number.isFinite(repo.ahead) && repo.ahead > 0) bits.push(`<span class="meta-muted">${esc(t('git.ahead', {count: repo.ahead}))}</span>`);
  if (Number.isFinite(repo.behind) && repo.behind > 0) bits.push(`<span class="meta-muted">${esc(t('git.behind', {count: repo.behind}))}</span>`);
  const primary = repo.primary ? ' repo-chip-row-primary' : '';
  return `<button type="button" class="repo-chip-row${primary}" data-repo-chip-open="${esc(repo.root || '')}">
    <span class="repo-chip-path">${esc(label)}</span>
    <span class="repo-chip-meta">${bits.join('')}</span>
  </button>`;
}

function showRepoChipMenu(session, x, y) {
  const info = transcriptMeta.sessions?.[session];
  const repos = sessionRepoSummaries(info);
  if (repos.length < 2) return;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu repo-chip-menu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML = repos.map(repoChipMenuRowHtml).join('');
  delegate(menu, 'click', '[data-repo-chip-open]', (event, row) => {
    const root = row.dataset.repoChipOpen || '';
    repoChipContextMenu.close();
    if (root) openFileExplorerAt(root);
  });
  repoChipContextMenu.open(menu, x, y);
}

function summaryContextHtml(session, info, agent) {
  const lines = [];
  const pane = info?.selected_pane;
  if (agent) {
    lines.push(summaryContextLine('agent', `${agent.kind || 'agent'} pid=${agent.pid || ''}${agent.status ? ` status=${agent.status}` : ''}`));
    if (agent.transcript) lines.push(summaryContextLine('transcript', agent.transcript));
    if (agent.error && !agent.transcript) lines.push(summaryContextLine('transcript', agent.error));
  } else {
    lines.push(summaryContextLine('agent', 'not detected'));
  }
  if (pane) lines.push(summaryContextLine('pane', `${pane.command || 'tmux'} ${pane.target || session} in ${pane.current_path || ''}`));

  const project = info?.project || {};
  const git = project.git;
  if (git) {
    lines.push(summaryContextLine('branch', `${git.branch || 'unknown'}${git.upstream ? ` -> ${git.upstream}` : ''}`));
    if (git.root) lines.push(summaryContextLine('repo', git.root));
    // S7: name a linked worktree vs its parent repo so the focused path isn't mistaken for the main checkout.
    if (git.worktree) lines.push(summaryContextLine('worktree', `${git.worktree.name || git.worktree.path} — worktree of ${git.worktree.parent_root}`));
    if (git.head) lines.push(summaryContextLine('head', git.head));
  } else {
    lines.push(summaryContextLine('repo', 'no git checkout detected'));
  }
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const label = pullRequestLinkLabel(pr);
    lines.push(summaryContextLine('github', `${label} ${pr.title || pr.description || ''}`, pr.url, label, pullRequestStatusClass(pr)));
  }
  for (const issue of project.linear || []) {
    const label = `${issue.identifier}${issue.state ? ` ${issue.state}` : ''}`;
    lines.push(summaryContextLine('linear', `${label} ${issue.title || ''}`, issue.url, issue.identifier));
  }
  return lines.join('');
}

function summaryContextLine(label, text, url = '', linkLabel = '', linkClass = '') {
  const value = url && linkLabel
    ? `${linkHtml(url, linkLabel, text, linkClass)} ${esc(text.replace(linkLabel, '').trim())}`
    : esc(text);
  return `<div class="summary-context-line"><span class="summary-context-label">${esc(label)}:</span> ${value}</div>`;
}

async function ensureSession(session) {
  if (readOnlyMode) return true;
  try {
    const payload = await apiFetchJson(`/api/ensure-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    statusEl.innerHTML = payload.created
      ? `<span class="ok">created ${esc(sessionLabel(session))} with Claude</span>`
      : `<span class="ok">${esc(sessionLabel(session))} ready</span>`;
    return true;
  } catch (error) {
    if (error?.status) {
      statusErr(esc(error.payload?.error || t('status.sessionCreateFailedDefault')));
      return false;
    }
    statusErr(localizedHtml('status.sessionCheckFailed', {error}));
    return false;
  }
}

async function createNextSession(agent) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyCreateSessions'));
    return;
  }
  const agentLabel = agentName(agent) || 'agent';
  statusEl.textContent = `creating ${agentLabel} session...`;
  try {
    const payload = await apiFetchJson(`/api/create-session?agent=${encodeURIComponent(agent)}`, {method: 'POST'});
    const previousActive = activeSessions.slice();
    updateSessionList(payload.sessions || []);
    renderSessionButtons();
    renderPanels(previousActive);
    await placeTmuxSession(payload.session);
    await ensureTerminalRunning(payload.session);
    refreshTranscripts({force: true});
    renderAutoApproveButtons();
    statusOk(`created ${esc(sessionLabel(payload.session))} (${esc(payload.session)}) with ${esc(agentName(payload.agent) || agentLabel)}`);
  } catch (error) {
    if (error?.status) {
      statusErr(esc(error.payload?.error || t('status.sessionCreateFailedDefault')));
      return;
    }
    statusErr(localizedHtml('status.sessionCreateFailed', {error}));
  }
}

function tmuxSessionNameError(name) {
  const text = String(name || '').trim();
  if (!text) return t('rename.error.required');
  if (text.length > 64) return t('rename.error.tooLong');
  // Keep in sync with TMUX_SESSION_NAME_RE in yolomux_lib/app.py.
  if (!/^[A-Za-z0-9_. -]+$/.test(text)) return t('rename.error.invalidChars');
  return '';
}

function rekeyMap(map, oldKey, newKey) {
  if (!map.has(oldKey) || oldKey === newKey) return;
  if (!map.has(newKey)) map.set(newKey, map.get(oldKey));
  map.delete(oldKey);
}

function clearSessionUiState(session) {
  stopTranscriptStream(session);
  stopSummaryStream(session);
  autoApproveStates.delete(session);
  paneViewState.delete(session);
  pendingPaneViewStateCaptures.delete(session);
  uploadResultsBySession.delete(session);
  if (uploadCleanupTimers.has(session)) {
    clearTimeout(uploadCleanupTimers.get(session));
    uploadCleanupTimers.delete(session);
  }
}

function stopSessionUi(session) {
  const item = terminals.get(session);
  if (item) closeTerminalItem(session, item);
  terminals.delete(session);
  clearSessionUiState(session);
  const panel = panelNodes.get(session);
  if (panel) panel.remove();
  panelNodes.delete(session);
}

function replaceSessionMetadata(oldSession, newSession) {
  for (const map of [
    autoApproveStates,
    sessionStateKeys,
    notificationLastSent,
    attentionAlertTimers,
    metadataBadgePulseUntil,
    uploadResultsBySession,
    uploadCleanupTimers,
    pasteCounters,
    paneViewState,
    // carry the per-pane LRU timestamp across a session rename too, or the renamed tab's
    // eviction ordering glitches (it reads as never-activated).
    tabLastActivatedAt,
  ]) {
    rekeyMap(map, oldSession, newSession);
  }
  if (transcriptMeta.sessions?.[oldSession]) {
    transcriptMeta.sessions = {
      ...(transcriptMeta.sessions || {}),
      [newSession]: transcriptMeta.sessions[newSession] || transcriptMeta.sessions[oldSession],
    };
    delete transcriptMeta.sessions[oldSession];
  }
  if (Array.isArray(transcriptMeta.session_order)) {
    transcriptMeta.session_order = transcriptMeta.session_order.map(item => item === oldSession ? newSession : item);
  }
}

function replaceTmuxSessionInClient(oldSession, newSession, nextSessions) {
  const next = normalizedSessionOrder(nextSessions) || sessions.map(item => item === oldSession ? newSession : item);
  stopSessionUi(oldSession);
  replaceSessionMetadata(oldSession, newSession);
  setSessionOrder(next);
  if (focusedTerminal === oldSession) focusedTerminal = newSession;
  if (focusedPanelItem === oldSession) focusedPanelItem = newSession;
  if (lastFocusedTmuxSession === oldSession) lastFocusedTmuxSession = newSession;
  applyLayoutSlots(layoutWithReplacedItem(oldSession, newSession), {focusSession: newSession, prune: false});
}

function closeSessionRenameDialog() {
  if (!sessionRenameDialog) return;
  sessionRenameDialog.remove();
  sessionRenameDialog = null;
  document.removeEventListener('keydown', sessionRenameDialogKeydown, true);
}

function sessionRenameDialogKeydown(event) {
  if (event.key === 'Escape') closeSessionRenameDialog();
}

function showSessionRenameDialog(session) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyRenameSessions'));
    return false;
  }
  if (!isTmuxSession(session)) return false;
  closeContextMenus();
  closeAppMenus();
  closeSessionRenameDialog();
  const overlay = document.createElement('div');
  overlay.className = 'app-modal-overlay session-rename-backdrop';
  overlay.setAttribute('role', 'presentation');
  overlay.innerHTML = `
    <form class="session-rename-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('rename.aria'))}">
      <div class="session-rename-title">${esc(t('rename.title', {name: `${sessionLabel(session)} ${session}`}))}</div>
      <input class="session-rename-input" name="sessionName" value="${esc(session)}" aria-label="${esc(t('rename.inputAria'))}" autocomplete="off">
      <div class="session-rename-error" hidden></div>
      <div class="session-rename-actions">
        <button type="button" class="session-rename-cancel">${esc(t('rename.cancel'))}</button>
        <button type="submit" class="session-rename-submit">${esc(t('rename.submit'))}</button>
      </div>
    </form>`;
  const form = overlay.querySelector('form');
  const input = overlay.querySelector('.session-rename-input');
  const errorNode = overlay.querySelector('.session-rename-error');
  const cancel = overlay.querySelector('.session-rename-cancel');
  const showError = message => {
    errorNode.textContent = message;
    errorNode.hidden = false;
  };
  overlay.addEventListener('pointerdown', event => {
    if (event.target === overlay) closeSessionRenameDialog();
  });
  cancel.addEventListener('click', closeSessionRenameDialog);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const nextName = input.value.trim();
    const nameError = tmuxSessionNameError(nextName);
    if (nameError) {
      showError(nameError);
      input.focus();
      return;
    }
    errorNode.hidden = true;
    const renamed = await renameTmuxSession(session, nextName);
    if (!renamed) {
      showError(t('status.renameFailedSeeStatus'));
      input.focus();
    }
  });
  appOverlayRootElement().appendChild(overlay);
  sessionRenameDialog = overlay;
  document.addEventListener('keydown', sessionRenameDialogKeydown, true);
  setTimeout(() => {
    input.focus();
    input.select();
  }, 0);
  return true;
}

async function renameTmuxSession(session, proposedName) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyRenameSessions'));
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (proposedName === undefined) return showSessionRenameDialog(session);
  const rawName = proposedName;
  const newName = String(rawName || '').trim();
  const nameError = tmuxSessionNameError(newName);
  if (nameError) {
    statusErr(`${esc(nameError)}`);
    return false;
  }
  if (newName === session) {
    closeSessionRenameDialog();
    return true;
  }
  statusEl.textContent = `renaming ${sessionLabel(session)}...`;
  try {
    const payload = await apiFetchJson(`/api/rename-session?session=${encodeURIComponent(session)}&new_name=${encodeURIComponent(newName)}`, {method: 'POST'});
    const renamed = payload.new_session || newName;
    replaceTmuxSessionInClient(session, renamed, payload.sessions);
    closeSessionRenameDialog();
    await ensureTerminalRunning(renamed);
    refreshTranscripts({force: true});
    renderAutoApproveButtons();
    statusOk(`renamed ${esc(session)} to ${esc(renamed)}`);
    return true;
  } catch (error) {
    if (error?.status) {
      statusErr(esc(error.payload?.error || t('status.sessionRenameFailedDefault')));
      return false;
    }
    statusErr(localizedHtml('status.sessionRenameFailed', {error}));
    return false;
  }
}

async function killTmuxSession(session) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyKillSessions'));
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (!window.confirm(`Kill tmux session ${sessionLabel(session)}?`)) return false;
  statusEl.textContent = `killing ${sessionLabel(session)}...`;
  try {
    const payload = await apiFetchJson(`/api/kill-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    const previousActive = activeSessions.slice();
    stopSessionUi(session);
    const sessionsChanged = updateSessionList(payload.sessions || []);
    autoApproveStates.delete(session);
    updateDocumentTitle();
    renderSessionButtons();
    renderPanels(previousActive);
    if (sessionsChanged) renderPaneTabStrips();
    refreshTranscripts({force: true});
    renderAutoApproveButtons();
    statusOk(`killed ${esc(sessionLabel(session))}`);
    return true;
  } catch (error) {
    if (error?.status) {
      statusErr(esc(error.payload?.error || t('status.sessionKillFailedDefault')));
      return false;
    }
    statusErr(localizedHtml('status.sessionKillFailed', {error}));
    return false;
  }
}

function focusPanel(session, options = {}) {
  const panel = document.getElementById(panelDomId(session));
  if (!panel) return;
  if (options.userInitiated === true || options.scrollIntoView === true) {
    panel.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  if (isFileEditorItem(session)) {
    focusedTerminal = null;
    setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
    if (autoFocusEnabled) {
      requestFileEditorPanelFocus(session);
      focusFileEditorPanelIfReady(panel, session);
    }
    return;
  }
  if (isVirtualItem(session)) {
    focusedTerminal = null;
    setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
    return;
  }
  activateTab(session, 'terminal', {userInitiated: options.userInitiated === true});
}

function shareHostTerminalSize(session) {
  if (!shareViewMode) return null;
  const dims = shareHostDimensions.get(String(session || ""));
  const rawRows = Math.floor(Number(dims?.rows) || 0);
  const rawCols = Math.floor(Number(dims?.cols) || 0);
  if (rawRows <= 0 || rawCols <= 0) return null;
  return {rows: Math.max(10, rawRows), cols: Math.max(40, rawCols)};
}

function updateShareHostTerminalSize(session, rows, cols) {
  if (!shareViewMode || !session) return;
  shareHostDimensions.set(String(session), {
    rows: Math.max(10, Math.floor(Number(rows) || 0)),
    cols: Math.max(40, Math.floor(Number(cols) || 0)),
  });
  fitTerminal(session);
}

function terminalFitMetricKey(value, scale = 1000) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.round(number * scale) / scale;
}

function terminalFitSignature(size) {
  if (!size) return '';
  return [
    Math.round(Number(size.contentWidth) || 0),
    Math.round(Number(size.contentHeight) || 0),
    terminalFitMetricKey(size.cellWidth),
    terminalFitMetricKey(size.cellHeight),
    size.cols,
    size.rows,
    terminalFitMetricKey(terminalFontSize),
    terminalFontFamily,
  ].join(':');
}

function terminalFitIsUnchanged(item, size) {
  if (!item?.term || !size) return false;
  return item.lastFitSignature === terminalFitSignature(size)
    && item.term.cols === size.cols
    && item.term.rows === size.rows;
}

function fitTerminal(session, options = {}) {
  const item = terminals.get(session);
  if (!item || !item.term || !item.container) return;
  const hostSize = shareHostTerminalSize(session);
  if (shareViewMode) {
    if (!hostSize) return;
    const changed = item.term.cols !== hostSize.cols || item.term.rows !== hostSize.rows;
    item.term.resize(hostSize.cols, hostSize.rows);
    if (changed && item.shareTerminalBytesReceived === true) {
      item.shareTerminalSkippedResetCount = Math.max(0, Math.round(Number(item.shareTerminalSkippedResetCount) || 0)) + 1;
    } else if (changed) {
      item.shareTerminalLastResetAt = Date.now();
      try { item.term.reset(); } catch (_) {}
    }
    refreshTerminal(session);
    return;
  }
  if (!terminalIsVisible(session, item.container)) return;
  const size = estimateTerminalSize(item.container, item.term);
  const signature = terminalFitSignature(size);
  if (options.force !== true && terminalFitIsUnchanged(item, size)) return;
  item.lastFitSignature = signature;
  const changed = item.term.cols !== size.cols || item.term.rows !== size.rows;
  if (changed) item.term.resize(size.cols, size.rows);
  if (!shareViewMode && changed) scheduleRemoteResize(session);
  refreshTerminal(session);
}

function sendRemoteResize(session) {
  if (shareViewMode) return;
  const item = terminals.get(session);
  if (!item?.term || item?.socket?.readyState !== WebSocket.OPEN) return;
  item.socket.send(JSON.stringify({type: 'resize', cols: item.term.cols, rows: item.term.rows}));
}

function scheduleRemoteResize(session, delay = remoteResizeDelayMs) {
  if (shareViewMode) return;
  const item = terminals.get(session);
  if (!item) return;
  if (item.resizeTimer) clearTimeout(item.resizeTimer);
  item.resizeTimer = setTimeout(() => {
    item.resizeTimer = null;
    sendRemoteResize(session);
  }, delay);
}

function refreshTerminal(session) {
  const item = terminals.get(session);
  if (!item?.term) return;
  requestAnimationFrame(() => {
    try { item.term.refresh(0, Math.max(0, item.term.rows - 1)); } catch (_) {}
  });
}

function terminalIsVisible(session, container) {
  const pane = document.getElementById(`terminal-pane-${session}`);
  return Boolean(
    pane?.classList.contains('active')
    && container.clientWidth > 40
    && container.clientHeight > 40
  );
}

function scheduleFit(session) {
  const item = terminals.get(session);
  if (item) {
    if (item.fitFrame) cancelAnimationFrame(item.fitFrame);
    if (item.fitTimer) clearTimeout(item.fitTimer);
    // C12 F3: one rAF fit for the common fast case + a SINGLE trailing fit to catch a late layout settle
    // (was rAF + 80ms + 250ms = three fits per resize). fitTerminal already skips the remote resize when
    // cols/rows are unchanged, so the trailing fit is a cheap no-op when nothing actually moved.
    item.fitFrame = requestAnimationFrame(() => {
      item.fitFrame = 0;
      fitTerminal(session);
    });
    item.fitTimer = setTimeout(() => {
      item.fitTimer = 0;
      fitTerminal(session);
    }, 250);
    return;
  }
  requestAnimationFrame(() => fitTerminal(session));
}

function observeTerminalResize(session, container) {
  const oldObserver = resizeObservers.get(session);
  if (oldObserver) oldObserver.disconnect();
  if (!window.ResizeObserver) return;
  const observer = new ResizeObserver(() => scheduleFit(session));
  observer.observe(container);
  resizeObservers.set(session, observer);
}

function enableTerminalScroll(session, term, container) {
  container.addEventListener('wheel', event => {
    if (event.ctrlKey && event.deltaY !== 0) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const signedLines = terminalWheelSignedLines(event, term.rows);
    if (!signedLines) return;
    event.preventDefault();
    event.stopPropagation();
    const item = terminals.get(session);
    if (!readOnlyMode && item?.socket?.readyState === WebSocket.OPEN) {
      queueTmuxScroll(item, signedLines);
      return;
    }
    queueLocalTerminalScroll(term, signedLines);
  }, {capture: true, passive: false});
}

function terminalWheelSignedLines(event, rows = 0) {
  const deltaY = Number(event?.deltaY);
  if (!Number.isFinite(deltaY) || deltaY === 0 || event?.ctrlKey) return 0;
  const direction = deltaY < 0 ? -1 : 1;
  const pageLines = Math.max(1, Math.floor((Number(rows) || 0) * terminalWheelPageFraction));
  if (event?.shiftKey) return direction * pageLines;
  const magnitude = Math.abs(deltaY);
  let lines;
  if (event?.deltaMode === 1) lines = magnitude;
  else if (event?.deltaMode === 2) lines = magnitude * pageLines;
  else lines = magnitude / terminalWheelPixelLinePx;
  return direction * Math.min(terminalWheelMaxLinesPerEvent, lines);
}

function queueTmuxScroll(item, signedLines) {
  item.pendingScrollLines = (item.pendingScrollLines || 0) + signedLines;
  if (item.scrollTimer) return;
  item.scrollTimer = setTimeout(() => {
    item.scrollTimer = null;
    const signed = item.pendingScrollLines || 0;
    item.pendingScrollLines = 0;
    if (!signed || item.socket?.readyState !== WebSocket.OPEN) return;
    const direction = signed < 0 ? 'up' : 'down';
    const lines = Math.max(1, Math.min(80, Math.ceil(Math.abs(signed))));
    item.socket.send(JSON.stringify({type: 'tmux-scroll', direction, lines}));
  }, 30);
}

function queueLocalTerminalScroll(term, signedLines) {
  term.pendingWheelScrollLines = (term.pendingWheelScrollLines || 0) + signedLines;
  if (term.wheelScrollTimer) return;
  term.wheelScrollTimer = setTimeout(() => {
    term.wheelScrollTimer = null;
    const signed = term.pendingWheelScrollLines || 0;
    term.pendingWheelScrollLines = 0;
    if (!signed) return;
    const direction = signed < 0 ? -1 : 1;
    const lines = Math.max(1, Math.min(80, Math.ceil(Math.abs(signed))));
    term.scrollLines(direction * lines);
  }, 30);
}

function closeTerminalItem(session, item) {
  item.manualClose = true;
  if (item.reconnectTimer) clearTimeout(item.reconnectTimer);
  if (item.resizeTimer) clearTimeout(item.resizeTimer);
  if (item.scrollTimer) clearTimeout(item.scrollTimer);
  const observer = resizeObservers.get(session);
  if (observer) {
    observer.disconnect();
    resizeObservers.delete(session);
  }
  try { item.socket.close(); } catch (_) {}
  try { item.term.dispose(); } catch (_) {}
}

function dismissTerminalConnectionToasts(session) {
  for (const node of document.querySelectorAll('.toast[data-toast-kind="terminal-connection"]')) {
    if (node.dataset.toastSession !== session) continue;
    removeAttentionAlert(Number(node.dataset.alertId || 0));
  }
}

function showTerminalConnectionToast(session, text, countdownMs = toastDurationMs) {
  dismissTerminalConnectionToasts(session);
  const node = showToast(
    compactNotificationTitle(sessionLabel(session), 'terminal'),
    text,
    {
      container: displayToastContainer(session),
      countdownMs,
      onClick: () => selectSession(session, {userInitiated: true}),
    },
  );
  if (node) {
    node.dataset.toastSession = session;
    node.dataset.toastKind = 'terminal-connection';
  }
}

function scheduleTerminalReconnect(session, item) {
  if (item.manualClose || terminals.get(session) !== item || !activeSessions.includes(session)) return;
  const delay = Math.min(8000, 1000 * 2 ** item.reconnectAttempt);
  const seconds = Math.round(delay / 1000);
  item.reconnectAttempt += 1;
  if (item.reconnectTimer) clearTimeout(item.reconnectTimer);
  statusErr(localizedHtml('terminal.connection.reconnectingStatus', {session: sessionLabel(session), seconds}));
  showTerminalConnectionToast(session, t('terminal.connection.reconnectingToast', {seconds}), delay);
  item.reconnectTimer = setTimeout(() => {
    if (item.manualClose || terminals.get(session) !== item || !activeSessions.includes(session)) return;
    item.reconnectTimer = null;
    connectTerminalSocket(session, item);
  }, delay);
}

// A tmux session that is absent from the live roster has been killed (vs a transient disconnect).
function sessionConfirmedGone(session, order) {
  return isTmuxSession(session) && Array.isArray(order) && !order.includes(session);
}

// Tear down a dead session's UI immediately (terminal, panel, metadata) — mirrors killSession's
// cleanup without the confirm/POST, for sessions that ended outside this client.
function pruneDeadSession(session) {
  const previousActive = activeSessions.slice();
  stopSessionUi(session);
  autoApproveStates.delete(session);
  if (sessions.includes(session)) updateSessionList(sessions.filter(item => item !== session));
  updateDocumentTitle();
  renderSessionButtons();
  renderPanels(previousActive);
  renderPaneTabStrips();
  renderAutoApproveButtons();
  statusOk(`${esc(sessionLabel(session))} ended`);
}

// On a terminal WebSocket close, confirm via the roster whether the session is actually gone. If so,
// prune it from the UI immediately instead of reconnecting and waiting for the next poll to notice.
async function confirmSessionGoneOrReconnect(session, item) {
  if (item.manualClose || terminals.get(session) !== item) return;
  // one in-flight confirmation per terminal. A flapping WS could otherwise run several
  // concurrent confirmations, each scheduling a reconnect and double-incrementing reconnectAttempt
  // (distorting the backoff).
  if (item.confirmingGone) return;
  item.confirmingGone = true;
  try {
    let order = null;
    try {
      const payload = await apiFetchJson('/api/auto-approve');
      if (Array.isArray(payload.session_order)) order = payload.session_order;
    } catch (_) {}
    if (item.manualClose || terminals.get(session) !== item) return;
    if (sessionConfirmedGone(session, order)) {
      pruneDeadSession(session);
      return;
    }
    scheduleTerminalReconnect(session, item);
  } finally {
    item.confirmingGone = false;
  }
}

function estimateTerminalSize(container, term = null) {
  const content = terminalContentSize(container);
  const measured = term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || term?._core?._renderService?.dimensions?.css?.cell
    || null;
  if (measured?.width && measured?.height) {
    return {
      cols: Math.max(40, Math.floor((content.width - 2) / measured.width)),
      rows: Math.max(10, Math.floor((content.height - terminalFitBottomReservePx) / measured.height)),
      contentWidth: content.width,
      contentHeight: content.height,
      cellWidth: measured.width,
      cellHeight: measured.height,
      measuredCell: 'renderer',
    };
  }
  const probe = document.createElement('span');
  probe.textContent = 'W';
  probe.style.position = 'absolute';
  probe.style.visibility = 'hidden';
  probe.style.fontFamily = terminalProbeFontFamily(container);
  probe.style.fontSize = `${Math.max(6, Math.round(Number(terminalFontSize) || 13))}px`;
  probe.style.lineHeight = '1';
  document.body.appendChild(probe);
  const rect = probe.getBoundingClientRect();
  probe.remove();
  const charWidth = Math.max(7, rect.width || 8);
  const charHeight = Math.max(14, rect.height || 16);
  return {
    cols: Math.max(40, Math.floor((content.width - 2) / charWidth)),
    rows: Math.max(10, Math.floor((content.height - terminalFitBottomReservePx) / charHeight)),
    contentWidth: content.width,
    contentHeight: content.height,
    cellWidth: charWidth,
    cellHeight: charHeight,
    measuredCell: 'probe',
  };
}

function terminalProbeFontFamily(container) {
  const localToken = getComputedStyle(container || document.documentElement)?.getPropertyValue?.('--mono-font')?.trim();
  if (localToken) return localToken;
  const rootToken = getComputedStyle(document.documentElement)?.getPropertyValue?.('--mono-font')?.trim();
  return rootToken || terminalFontFamily;
}

function terminalContentSize(container) {
  const style = getComputedStyle(container);
  const horizontalPadding = px(style.paddingLeft) + px(style.paddingRight);
  const verticalPadding = px(style.paddingTop) + px(style.paddingBottom);
  return {
    width: Math.max(0, container.clientWidth - horizontalPadding),
    height: Math.max(0, container.clientHeight - verticalPadding),
  };
}

function px(value) {
  const number = Number.parseFloat(value);
  return Number.isFinite(number) ? number : 0;
}

function slotSide(slot) {
  return slotColumn(slot);
}

function slotForItem(item, slots = layoutSlots) {
  return layoutSlotKeys(slots).find(side => paneTabs(side, slots).includes(item)) || null;
}

function slotForSession(session) {
  return slotForItem(session);
}

function slotForDropEvent(event) {
  const rect = grid.getBoundingClientRect();
  return event.clientX < rect.left + rect.width / 2 ? 'left' : 'right';
}

function layoutBoundaryDropBandPx(size) {
  const scaled = Math.max(0, Number(size) || 0) * layoutBoundaryDropFraction;
  return Math.min(layoutBoundaryDropMaxPx, Math.max(layoutBoundaryDropMinPx, scaled));
}

function rootBoundaryDropZoneForEvent(event, rect) {
  if (!rect.width || !rect.height) return null;
  const insideX = event.clientX >= rect.left && event.clientX <= rect.right;
  const insideY = event.clientY >= rect.top && event.clientY <= rect.bottom;
  if (!insideX || !insideY) return null;
  const xBand = layoutBoundaryDropBandPx(rect.width);
  const yBand = layoutBoundaryDropBandPx(rect.height);
  const candidates = [
    {zone: 'left', distance: Math.abs(event.clientX - rect.left), active: event.clientX <= rect.left + xBand},
    {zone: 'right', distance: Math.abs(rect.right - event.clientX), active: event.clientX >= rect.right - xBand},
    {zone: 'top', distance: Math.abs(event.clientY - rect.top), active: event.clientY <= rect.top + yBand},
    {zone: 'bottom', distance: Math.abs(rect.bottom - event.clientY), active: event.clientY >= rect.bottom - yBand},
  ].filter(candidate => candidate.active);
  candidates.sort((left, right) => left.distance - right.distance);
  return candidates[0]?.zone || null;
}

function rootBoundaryDropIntentForEvent(event) {
  const targetRect = grid.getBoundingClientRect();
  const zone = rootBoundaryDropZoneForEvent(event, targetRect);
  if (!zone) return null;
  if (rootBoundaryDropOverDockedFileExplorer(event, zone)) return null;
  return {targetSlot: slotForDropEvent(event), zone, previewNode: grid, targetRect, boundary: 'root'};
}

function gutterDropZoneForEvent(event, node, rect) {
  if (!node?.children?.length || !rect.width || !rect.height) return null;
  if (node.split === 'column') return event.clientY < rect.top + rect.height / 2 ? 'top' : 'bottom';
  return event.clientX < rect.left + rect.width / 2 ? 'left' : 'right';
}

function gutterDropIntentForEvent(event) {
  const resizer = event.target?.closest?.('.layout-resizer');
  const splitPath = resizer?.dataset?.splitPath;
  if (!resizer || splitPath === undefined) return null;
  const targetRect = resizer.getBoundingClientRect();
  const node = layoutNodeAtPath(splitPath);
  const zone = gutterDropZoneForEvent(event, node, targetRect);
  if (!zone) return null;
  return {targetSlot: slotForDropEvent(event), zone, previewNode: grid, targetRect, boundary: 'gutter', splitPath};
}

function dropZoneForRect(event, rect) {
  if (!rect.width || !rect.height) return 'middle';
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (y < 0.24) return rect.height / 2 >= (rootCssLengthPx('--min-split-pane-height') || 220) ? 'top' : 'middle';
  if (y > 0.76) return rect.height / 2 >= (rootCssLengthPx('--min-split-pane-height') || 220) ? 'bottom' : 'middle';
  if (x < 0.24) return rect.width / 2 >= (rootCssLengthPx('--min-split-pane-width') || 320) ? 'left' : 'middle';
  if (x > 0.76) return rect.width / 2 >= (rootCssLengthPx('--min-split-pane-width') || 320) ? 'right' : 'middle';
  return 'middle';
}

function dropIntentForEvent(event, options = {}) {
  if (options.allowBoundary === true) {
    const rootIntent = rootBoundaryDropIntentForEvent(event);
    if (rootIntent) return rootIntent;
    const gutterIntent = gutterDropIntentForEvent(event);
    if (gutterIntent) return gutterIntent;
  }
  const slotNode = event.target?.closest?.('.drop-slot');
  if (slotNode?.dataset.slot) {
    const targetSlot = slotNode.dataset.slot;
    const targetRect = slotNode.getBoundingClientRect();
    return {targetSlot, zone: dropZoneForRect(event, targetRect), previewNode: slotNode, targetRect};
  }
  return {targetSlot: slotForDropEvent(event), zone: 'middle', previewNode: null, targetRect: grid.getBoundingClientRect()};
}

function slotIsFileExplorerPane(slot) {
  return isFileExplorerItem(activeItemForSide(slot));
}

function dropIntentTargetRect(intent) {
  if (!intent || typeof intent === 'string') return null;
  return intent.targetRect || intent.previewNode?.getBoundingClientRect?.() || null;
}

function minHeightForLayoutItem(_item) {
  return rootCssLengthPx('--min-split-pane-height') || 220;
}

function dropIntentInlineSize(intent) {
  if (!intent || typeof intent === 'string') return 0;
  const rect = dropIntentTargetRect(intent);
  return Math.max(0, Number(rect?.width) || 0);
}

function dropItemCanBeDragged(item, options = {}) {
  if (isLayoutItem(item)) return !isFileExplorerItem(item);
  return options.allowCandidate === true && isFileEditorItem(item);
}

function rectCanShowLayoutItem(rect, item) {
  if (!rect) return true;
  return (Number(rect.width) || 0) >= minWidthForLayoutItem(item)
    && (Number(rect.height) || 0) >= minHeightForLayoutItem(item);
}

function rectCanShowPaneTabs(rect, tabs) {
  if (!rect) return true;
  const items = (tabs || []).filter(isLayoutItem);
  if (!items.length) return false;
  const minWidth = Math.max(...items.map(minWidthForLayoutItem));
  const minHeight = Math.max(...items.map(minHeightForLayoutItem));
  return (Number(rect.width) || 0) >= minWidth
    && (Number(rect.height) || 0) >= minHeight;
}

function paneSwapAllowed(sourceSlot, targetSlot, slots = layoutSlots) {
  if (!sourceSlot || !targetSlot || sourceSlot === targetSlot) return false;
  const keys = layoutSlotKeys(slots);
  if (!keys.includes(sourceSlot) || !keys.includes(targetSlot)) return false;
  if (paneIsPlaceholder(sourceSlot, slots) || paneIsPlaceholder(targetSlot, slots)) return false;
  if (isFileExplorerItem(activeItemForSide(sourceSlot, slots)) || isFileExplorerItem(activeItemForSide(targetSlot, slots))) return false;
  const sourceTabs = paneTabs(sourceSlot, slots);
  const targetTabs = paneTabs(targetSlot, slots);
  if (!sourceTabs.length || !targetTabs.length) return false;
  return rectCanShowPaneTabs(layoutSlotScreenRect(targetSlot), sourceTabs)
    && rectCanShowPaneTabs(layoutSlotScreenRect(sourceSlot), targetTabs);
}

function paneSwapTargetForEvent(event) {
  const slotNode = event.target?.closest?.('.drop-slot');
  if (slotNode?.dataset.slot) return {slot: slotNode.dataset.slot, previewNode: slotNode};
  const panel = event.target?.closest?.('.panel');
  if (panel?.dataset.slot) return {slot: panel.dataset.slot, previewNode: panel.closest?.('.drop-slot') || panel};
  if (typeof dockviewSlotForGroupElement === 'function') {
    const group = event.target?.closest?.('.dv-groupview');
    const slot = group ? dockviewSlotForGroupElement(group) : null;
    if (slot) return {slot, previewNode: group};
  }
  return {slot: null, previewNode: null};
}

function paneSwapIntentForEvent(event, sourceSlot) {
  const target = paneSwapTargetForEvent(event);
  if (!target.slot || target.slot === sourceSlot) return null;
  return {
    sourceSlot,
    targetSlot: target.slot,
    zone: 'middle',
    swap: true,
    previewNode: target.previewNode,
    targetRect: layoutSlotScreenRect(target.slot),
  };
}

function paneSwapIntentAllowed(intent) {
  return Boolean(intent?.swap && paneSwapAllowed(intent.sourceSlot, intent.targetSlot));
}

function swapPaneSlots(sourceSlot, targetSlot) {
  if (!paneSwapAllowed(sourceSlot, targetSlot)) return false;
  const next = cloneLayoutSlots(layoutSlots);
  const sourceState = paneStateForLayoutSlot(sourceSlot);
  const targetState = paneStateForLayoutSlot(targetSlot);
  next[sourceSlot] = targetState;
  next[targetSlot] = sourceState;
  applyLayoutSlots(next, {
    focusSession: activeItemForSide(targetSlot, next) || activeItemForSide(sourceSlot, next),
    prune: false,
    forceFull: dockviewLayoutActive(),
    message: 'panes swapped',
  });
  return true;
}

function dropIntentHasRoomForItem(item, intent) {
  if (!intent || typeof intent === 'string') return true;
  if (intent.boundary === 'root' || intent.boundary === 'gutter') return true;
  const rect = dropIntentTargetRect(intent);
  if (!rect) return true;
  const zone = intent.zone || 'middle';
  if (!layoutSplitZone(zone)) return rectCanShowLayoutItem(rect, item);
  const targetItem = activeItemForSide(intent.targetSlot);
  const targetMinWidth = minWidthForLayoutItem(targetItem);
  const itemMinWidth = minWidthForLayoutItem(item);
  const targetMinHeight = minHeightForLayoutItem(targetItem);
  const itemMinHeight = minHeightForLayoutItem(item);
  if (zone === 'left' || zone === 'right') {
    return (Number(rect.width) || 0) >= targetMinWidth + itemMinWidth
      && (Number(rect.height) || 0) >= Math.max(targetMinHeight, itemMinHeight);
  }
  return (Number(rect.width) || 0) >= Math.max(targetMinWidth, itemMinWidth)
    && (Number(rect.height) || 0) >= targetMinHeight + itemMinHeight;
}

function itemCanSplitSinglePurposePane(item, intent) {
  const zone = typeof intent === 'string' ? intent : intent?.zone;
  if (zone !== 'bottom') return false;
  if (isFileExplorerItem(item)) return false;
  if (!dropIntentTargetRect(intent)) return false;
  return dropIntentHasRoomForItem(item, intent);
}

function dropIntentAllowsSession(session, intent, options = {}) {
  if (!dropItemCanBeDragged(session, options)) return false;
  if ((intent?.boundary === 'root' || intent?.boundary === 'gutter') && layoutSplitZone(intent.zone)) return true;
  if (!intent?.targetSlot) return false;
  if (slotIsFileExplorerPane(intent.targetSlot)) {
    return itemCanSplitSinglePurposePane(session, intent);
  }
  return dropIntentHasRoomForItem(session, intent);
}

function fileDragLayoutItem(payload) {
  const path = payload?.path || payload?.paths?.find?.(Boolean) || '';
  if (!path || payload?.kind === 'dir') return null;
  return fileEditorItemFor(path);
}

function fileDropIntentAllowsPayload(payload, intent) {
  const item = fileDragLayoutItem(payload);
  return Boolean(item && dropIntentAllowsSession(item, intent, {allowCandidate: true}));
}

function pathDropIntentAllowsPayload(payload, intent) {
  const path = payload?.path || payload?.paths?.find?.(Boolean) || '';
  const item = path ? fileEditorItemFor(path) : null;
  return Boolean(item && dropIntentAllowsSession(item, intent, {allowCandidate: true}));
}

function clearDropPreview() {
  const nodes = new Set([grid]);
  for (const className of DROP_PREVIEW_CLASSES) {
    grid.querySelectorAll(`.${className}`).forEach(node => nodes.add(node));
  }
  nodes.forEach(node => {
    node.classList.remove(...DROP_PREVIEW_CLASSES);
    node.style?.removeProperty('--tab-drop-x');
    node.style?.removeProperty('--tab-drop-y');
    node.style?.removeProperty('--tab-drop-height');
    node.style?.removeProperty('--drop-preview-left');
    node.style?.removeProperty('--drop-preview-top');
    node.style?.removeProperty('--drop-preview-width');
    node.style?.removeProperty('--drop-preview-height');
    if (node.dataset) delete node.dataset.dropLabel;
  });
}

function applyGutterDropPreviewGeometry(node, intent) {
  if (intent?.boundary !== 'gutter' || node !== grid) return;
  const gridRect = grid.getBoundingClientRect();
  const targetRect = intent.targetRect || gridRect;
  const isHorizontalBand = intent.zone === 'left' || intent.zone === 'right';
  const inlineSize = isHorizontalBand ? gridRect.width : gridRect.height;
  const bandSize = Math.max(layoutBoundaryDropMinPx * 2, Math.min(layoutBoundaryDropMaxPx * 3, inlineSize * 0.24));
  if (isHorizontalBand) {
    const center = targetRect.left + targetRect.width / 2 - gridRect.left;
    const left = Math.max(6, Math.min(gridRect.width - bandSize - 6, center - bandSize / 2));
    node.style.setProperty('--drop-preview-left', `${Math.round(left)}px`);
    node.style.setProperty('--drop-preview-top', '6px');
    node.style.setProperty('--drop-preview-width', `${Math.round(bandSize)}px`);
    node.style.setProperty('--drop-preview-height', 'calc(100% - 12px)');
  } else {
    const center = targetRect.top + targetRect.height / 2 - gridRect.top;
    const top = Math.max(6, Math.min(gridRect.height - bandSize - 6, center - bandSize / 2));
    node.style.setProperty('--drop-preview-left', '6px');
    node.style.setProperty('--drop-preview-top', `${Math.round(top)}px`);
    node.style.setProperty('--drop-preview-width', 'calc(100% - 12px)');
    node.style.setProperty('--drop-preview-height', `${Math.round(bandSize)}px`);
  }
}

function layoutNodeScreenRect(layoutNode) {
  const rects = layoutLeafSlots(layoutNode)
    .map(slot => layoutSlotScreenRect(slot))
    .filter(rect => rect && rect.width > 0 && rect.height > 0);
  if (!rects.length) return null;
  const left = Math.min(...rects.map(rect => rect.left));
  const top = Math.min(...rects.map(rect => rect.top));
  const right = Math.max(...rects.map(rect => rect.right));
  const bottom = Math.max(...rects.map(rect => rect.bottom));
  return {left, top, right, bottom, width: right - left, height: bottom - top};
}

function layoutSlotScreenRect(slot) {
  const column = layoutColumnNode(slot);
  const columnRect = column?.getBoundingClientRect?.();
  if (columnRect?.width > 0 && columnRect?.height > 0) return columnRect;
  const panel = grid?.querySelector(`.dockview-panel-content > .panel[data-slot="${cssEscape(slot)}"]`);
  const panelGroup = panel?.closest?.('.dv-groupview');
  const panelGroupRect = panelGroup?.getBoundingClientRect?.();
  if (panelGroupRect?.width > 0 && panelGroupRect?.height > 0) return panelGroupRect;
  const item = activeItemForSide(slot) || paneTabs(slot)[0] || '';
  const tabGroup = item
    ? grid?.querySelector(`.dockview-pane-tab[data-pane-tab="${cssEscape(item)}"]`)?.closest?.('.dv-groupview')
    : null;
  const tabGroupRect = tabGroup?.getBoundingClientRect?.();
  return tabGroupRect?.width > 0 && tabGroupRect?.height > 0 ? tabGroupRect : null;
}

function dockedFileExplorerScreenRect(slots = layoutSlots) {
  const root = slots?.[layoutTreeKey];
  const docked = dockedFileExplorerRootSplit(root, slots);
  if (!docked) return null;
  return layoutNodeScreenRect(root?.children?.[docked.finderIndex]);
}

function eventInsideRect(event, rect) {
  return Boolean(
    event && rect
    && event.clientX >= rect.left && event.clientX <= rect.right
    && event.clientY >= rect.top && event.clientY <= rect.bottom
  );
}

function rootBoundaryDropOverDockedFileExplorer(event, zone, slots = layoutSlots) {
  if (!layoutSplitZone(zone)) return false;
  return eventInsideRect(event, dockedFileExplorerScreenRect(slots));
}

function applyDockedFileExplorerBoundaryPreviewGeometry(node, intent) {
  if (intent?.boundary !== 'root' || node !== grid) return;
  if (intent.zone !== 'top' && intent.zone !== 'bottom') return;
  const root = layoutSlots?.[layoutTreeKey];
  const docked = dockedFileExplorerRootSplit(root, layoutSlots);
  if (!docked) return;
  const gridRect = grid.getBoundingClientRect();
  const contentRect = layoutNodeScreenRect(root?.children?.[docked.contentIndex]);
  if (!gridRect || !contentRect) return;
  node.style.setProperty('--drop-preview-left', `${Math.round(contentRect.left - gridRect.left + 6)}px`);
  node.style.setProperty('--drop-preview-width', `${Math.round(Math.max(0, contentRect.width - 12))}px`);
}

function showDropPreview(intent) {
  clearDropPreview();
  const node = intent?.boundary ? grid : intent?.previewNode;
  if (!node) return;
  const zone = intent.zone || 'middle';
  node.classList.add(CLS.dragOver, CLS.dropPreview, `drop-preview-${zone}`);
  if (intent.boundary) node.classList.add(`drop-preview-${intent.boundary}`);
  node.dataset.dropLabel = intent.swap === true
    ? 'swap'
    : intent.boundary === 'root'
    ? `full ${zone}`
    : intent.boundary === 'gutter'
      ? 'full span'
      : zone === 'middle'
        ? 'take over'
        : zone;
  applyGutterDropPreviewGeometry(node, intent);
  applyDockedFileExplorerBoundaryPreviewGeometry(node, intent);
}

function dropSessionAtEvent(event) {
  const panePayload = paneDragPayload(event);
  if (panePayload?.slot) {
    event.preventDefault();
    event.stopPropagation();
    const intent = paneSwapIntentForEvent(event, panePayload.slot);
    clearDropPreview();
    if (paneSwapIntentAllowed(intent)) swapPaneSlots(intent.sourceSlot, intent.targetSlot);
    return;
  }
  const filePayload = fileDragPayload(event);
  if (filePayload?.path) {
    event.preventDefault();
    event.stopPropagation();
    const intent = dropIntentForEvent(event, {allowBoundary: false});
    clearDropPreview();
    if (!intent?.targetSlot || !fileDropIntentAllowsPayload(filePayload, intent)) return;
    openDraggedFilesInEditor(filePayload, {targetSlot: intent.targetSlot, targetZone: intent.zone});
    return;
  }
  const payload = dragPayload(event);
  if (!payload?.session) return;
  if (event.target?.closest?.('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    clearDropPreview();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const intent = dropIntentForEvent(event, {allowBoundary: true});
  clearDropPreview();
  if (!dropIntentAllowsSession(payload.session, intent)) return;
  dropSessionWithIntent(payload.session, intent, payload.sourceSlot || slotForSession(payload.session));
}

function handleDropDragOver(event) {
  const panePayload = paneDragPayload(event);
  if (panePayload?.slot) {
    const intent = paneSwapIntentForEvent(event, panePayload.slot);
    event.preventDefault();
    event.stopPropagation();
    if (!paneSwapIntentAllowed(intent)) {
      event.dataTransfer.dropEffect = 'none';
      clearDropPreview();
      return;
    }
    event.dataTransfer.dropEffect = 'move';
    showDropPreview(intent);
    return;
  }
  const filePayload = fileDragPayload(event);
  if (filePayload?.path) {
    const intent = dropIntentForEvent(event, {allowBoundary: false});
    if (!fileDropIntentAllowsPayload(filePayload, intent)) {
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'none';
      clearDropPreview();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    showDropPreview(intent);
    return;
  }
  const payload = dragPayload(event);
  if (!payload?.session) return;
  if (event.target?.closest?.('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  const intent = dropIntentForEvent(event, {allowBoundary: true});
  event.preventDefault();
  event.stopPropagation();
  if (!dropIntentAllowsSession(payload.session, intent)) {
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  event.dataTransfer.dropEffect = 'move';
  showDropPreview(intent);
}

function handleDropDragLeave(event) {
  const current = event.currentTarget;
  if (current?.contains(event.relatedTarget)) return;
  clearDropPreview();
}

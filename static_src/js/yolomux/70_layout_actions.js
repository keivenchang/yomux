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

function removeSessionFromLayout(item) {
  if (!itemInLayout(item)) return;
  const isFiles = isFileExplorerItem(item);
  applyLayoutSlots(layoutWithoutItem(item, {
    preserveRemovedSlot: !isFiles,
    preservePlaceholders: !isFiles,
  }), {
    message: `${itemLabel(item)} hidden from layout`,
  });
}

function removePaneFromLayout(item) {
  const slot = slotForSession(item);
  if (!slot) return;
  const moved = paneTabs(slot);
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

function setLayoutToSinglePane() {
  const items = visibleNonFinderPaneItems();
  if (!items.length) return;
  const active = items.includes(focusedPanelItem) ? focusedPanelItem : items[0];
  const finderSlot = slotForSession(fileExplorerItemId);
  const targetSlot = firstNonFinderPaneSlot() || (finderSlot === 'left' ? 'right' : 'left');
  const next = emptyLayoutSlots();
  next[targetSlot] = paneStateWithTabs(items, active);
  if (finderSlot) {
    next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
    const finderFirst = finderLeadsExpandedPane(finderSlot, targetSlot);
    next[layoutTreeKey] = finderFirst
      ? splitNode('row', leafNode(finderSlot), leafNode(targetSlot), fileExplorerSplitPercent)
      : splitNode('row', leafNode(targetSlot), leafNode(finderSlot), 100 - fileExplorerSplitPercent);
  } else {
    next[layoutTreeKey] = leafNode(targetSlot);
  }
  applyLayoutSlots(next, {focusSession: active, prune: false, message: 'single pane layout'});
}

function setLayoutToSplitPanes() {
  const items = visibleNonFinderPaneItems();
  if (!items.length) return;
  const finderSlot = slotForSession(fileExplorerItemId);
  const preferredSlots = basePaneKeys.filter(slot => slot !== finderSlot);
  while (preferredSlots.length < 2) preferredSlots.push(nextLayoutSlot({...layoutSlots, [preferredSlots[0] || 'left']: emptyPaneState()}));
  const leftSlot = preferredSlots[0] || 'left';
  const rightSlot = preferredSlots[1] || 'right';
  const groups = [[], []];
  const assigned = new Set();
  for (const item of items) {
    const slot = slotForSession(item);
    if (slot === leftSlot) {
      groups[0].push(item);
      assigned.add(item);
    } else if (slot === rightSlot) {
      groups[1].push(item);
      assigned.add(item);
    }
  }
  for (const item of items) {
    if (assigned.has(item)) continue;
    groups[groups[0].length <= groups[1].length ? 0 : 1].push(item);
  }
  if (!groups[1].length && groups[0].length > 1) groups[1].push(...groups[0].splice(Math.ceil(groups[0].length / 2)));
  const active = items.includes(focusedPanelItem) ? focusedPanelItem : items[0];
  const next = emptyLayoutSlots();
  next[leftSlot] = paneStateWithTabs(groups[0], groups[0].includes(active) ? active : groups[0][0]);
  if (groups[1].length) next[rightSlot] = paneStateWithTabs(groups[1], groups[1].includes(active) ? active : groups[1][0]);
  const nonFinderTree = groups[1].length
    ? splitNode('row', leafNode(leftSlot), leafNode(rightSlot), defaultSplitPercent)
    : leafNode(leftSlot);
  if (finderSlot) {
    next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
    const finderFirst = finderLeadsExpandedPane(finderSlot, leftSlot);
    next[layoutTreeKey] = finderFirst
      ? splitNode('row', leafNode(finderSlot), nonFinderTree, fileExplorerSplitPercent)
      : splitNode('row', nonFinderTree, leafNode(finderSlot), 100 - fileExplorerSplitPercent);
  } else {
    next[layoutTreeKey] = nonFinderTree;
  }
  applyLayoutSlots(next, {focusSession: active, prune: false, message: 'split layout'});
}

function layoutWithFileExplorerDockedLeft(slots = layoutSlots) {
  const right = compactLayoutSlots(layoutWithoutItemFromSlots(fileExplorerItemId, slots, {preservePlaceholders: true}));
  const rightSlots = layoutSlotKeys(right).filter(slot => paneHasLayoutContent(slot, right));
  const next = emptyLayoutSlots();
  for (const slot of rightSlots) next[slot] = paneStateForLayoutSlot(slot, right);
  const used = new Set(rightSlots);
  const currentSlot = slotForItem(fileExplorerItemId, slots);
  let finderSlot = currentSlot && !used.has(currentSlot) ? currentSlot : null;
  if (!finderSlot) finderSlot = !used.has('left') ? 'left' : nextLayoutSlot(next);
  next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
  next[layoutTreeKey] = rightSlots.length
    ? splitNode('row', leafNode(finderSlot), right[layoutTreeKey], fileExplorerSplitPercent)
    : leafNode(finderSlot);
  return compactLayoutSlots(next);
}

function dockFileExplorerPane() {
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

function slotForTabActivation(item) {
  const currentSlot = slotForSession(item);
  if (currentSlot) return currentSlot;
  return largestNonFileExplorerPaneSlot() || firstEmptyPane() || largestPaneSlot() || slotForNewSession();
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

async function openFileExplorerPane() {
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

async function moveSessionToSlot(session, targetSlot, sourceSlot = null, insertIndex = 0) {
  if (!isLayoutItem(session) || !targetSlot) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyPaneState();
  const tabs = next[targetSlot].tabs;
  const index = Math.max(0, Math.min(Number.isFinite(insertIndex) ? insertIndex : 0, tabs.length));
  tabs.splice(index, 0, session);
  next[targetSlot] = paneStateWithTabs(tabs, session);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

async function dropSessionWithIntent(session, intent, sourceSlot = null) {
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
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

function replaceLayoutLeaf(node, slot, replacement) {
  if (!node) return replacement;
  if (node.slot) return node.slot === slot ? replacement : node;
  const children = (node.children || []).map(child => replaceLayoutLeaf(child, slot, replacement));
  return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
}

function activatePaneTab(side, session, options = {}) {
  if (!layoutSlotKeys().includes(side) || !itemInLayout(session)) return;
  const previous = activeItemForSide(side);
  if (previous && previous !== session && typeof captureFileEditorPanelViewStateForItem === 'function') {
    captureFileEditorPanelViewStateForItem(previous);
  }
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  setFocusedPanelItem(session);
  if (activeItemForSide(side) === session) {
    focusPanel(session, {userInitiated: options.userInitiated === true});
    return;
  }
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const key of layoutSlotKeys()) next[key] = paneStateForLayoutSlot(key);
  next[side].active = session;
  applyLayoutSlots(next, {focusSession: session});
  if (isPreferencesItem(session) && options.userInitiated === true && autoFocusEnabled) {
    focusFreshPreferencesSearchSoon();
  }
  if (options.userInitiated && isTmuxSession(session)) focusTerminalFromUserAction(session, 25);
}

async function selectSession(session) {
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  const shouldFocusPreferencesSearch = isPreferencesItem(session);
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
    scheduleFileExplorerActiveTabSync();
    return;
  }
  if (activeSessions.includes(session)) {
    focusPanel(session, {userInitiated: true});
    return;
  }
  if (isTmuxSession(session) && filesOnlySlotForSession(session)) {
    await placeTmuxSession(session);
    return;
  }
  await activateTabInExistingPane(session);
  if (shouldFocusPreferencesSearch) focusFreshPreferencesSearchSoon();
}

function sessionAgentKind(session) {
  const info = transcriptMeta.sessions?.[session];
  const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
  const kind = String(agent?.kind || '').toLowerCase();
  return kind === 'claude' || kind === 'codex' ? kind : '';
}

function agentIcon(kind) {
  if (kind === 'codex') {
    return `<span class="agent-icon codex" aria-label="Codex" title="Codex">${codexIcon()}</span>`;
  }
  if (kind === 'claude') {
    return `<span class="agent-icon claude" aria-label="Claude" title="Claude">${claudeIcon()}</span>`;
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
  if (pr.source_only) return '';
  if (pr.status_label) return pr.status_label;
  if (pr.draft) return 'draft';
  if (pr.merged || pr.merged_at) return 'merged';
  return pr.state || '';
}

function pullRequestStatusDisplay(pr) {
  const status = pullRequestStatusLabel(pr);
  if (!status) return '';
  const key = status.toLowerCase();
  if (key === 'unknown') return '';
  if (key === 'merged') return 'MERGED';
  if (key === 'draft') return 'DRAFT';
  if (key === 'closed') return 'CLOSED';
  if (key === 'open') return 'OPEN';
  return status.replace(/\bci\b/gi, 'CI').toUpperCase();
}

function pullRequestLinkLabel(pr) {
  const status = pullRequestStatusDisplay(pr);
  return `PR #${pr.number}${status ? ` ${status}` : ''}`;
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
  if (!['merged', 'draft', 'closed'].includes(status)) return '';
  return `<span class="${metadataBadgeClasses(session, 'status', `ci-indicator tab-symbol ${pullRequestStatusClass(pr)}`)}">${pullRequestStatusDisplay(pr)}</span>`;
}

function pullRequestCiIndicatorHtml(session, pr) {
  if (pullRequestStatusLabel(pr).toLowerCase() === 'merged') return '';
  const state = pr?.checks?.state;
  if (!state || state === 'unknown') return '';
  return `<span class="${metadataBadgeClasses(session, 'ci', `ci-indicator tab-symbol ${pullRequestCiStatusClass(pr)}`)}">CI</span>`;
}

function pullRequestLinkHtml(pr) {
  return linkHtml(pr.url, pullRequestLinkLabel(pr), pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestAuthorHtml(pr) {
  const author = String(pr?.author_login || '').trim();
  return author ? `<span class="meta-muted">by ${esc(author)}</span>` : '';
}

function pullRequestColumnLinkHtml(pr) {
  const status = pullRequestStatusDisplay(pr);
  const label = `#${pr.number}${status ? ` ${status}` : ''}`;
  return linkHtml(pr.url, label, pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestChecksHtml(pr) {
  const checks = pr?.checks;
  if (!checks || !checks.state || checks.state === 'unknown') return '';
  const cls = pullRequestCiStatusClass(pr);
  const parts = [`<span class="meta-pr-status ${cls}">${esc(checks.summary || `CI ${checks.state}`)}</span>`];
  const checkLinks = items => (items || []).map(item => (
    item?.name ? linkHtml(item.url || '', item.name, item.state || '') : ''
  )).filter(Boolean).join(', ');
  const failing = checkLinks(checks.failing);
  const pending = checkLinks(checks.pending);
  if (failing) parts.push(`<span class="meta-muted">failing: ${failing}</span>`);
  if (pending) parts.push(`<span class="meta-muted">pending: ${pending}</span>`);
  if (Number.isFinite(checks.total)) parts.push(`<span class="meta-muted">${checks.total} checks</span>`);
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
  const git = project.git;
  const parts = [];
  const fullPath = panelFullPath(session, info);
  if (!git) {
    if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
    parts.push('<span class="meta-muted">no git checkout detected</span>');
    return metaJoin(parts);
  }
  const pr = displayPullRequest(info);
  if (pr?.number) parts.push(pullRequestLinkHtml(pr));
  if (git.branch) parts.push(`<span class="meta-branch">${esc(shortBranch(git.branch))}</span>`);
  if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(`<span class="meta-muted">behind ${git.behind}</span>`);
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(`<span class="meta-muted">ahead ${git.ahead}</span>`);
  if (Number.isFinite(git.dirty_count) && git.dirty_count > 0) parts.push(`<span class="meta-muted">dirty ${git.dirty_count}</span>`);
  if (pr?.number) {
    if (pr.checks?.state && pr.checks.state !== 'unknown') {
      parts.push(`<span class="meta-pr-status ${pullRequestCiStatusClass(pr)}">${esc(pr.checks.summary || pullRequestStatusLabel(pr))}</span>`);
    }
  }
  for (const issue of project.linear || []) {
    const state = issue.state ? ` ${issue.state}` : '';
    parts.push(linkHtml(issue.url, `${issue.identifier}${state}`, issue.title || ''));
  }
  const desc = pr?.title || pr?.description || (project.linear || []).find(issue => issue.title)?.title || '';
  if (desc) parts.push(`<span class="meta-desc">${esc(shortText(desc, 160))}</span>`);
  return parts.length ? metaJoin(parts) : '<span class="meta-muted">git checkout detected</span>';
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
    const response = await apiFetch(`/api/ensure-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session create failed')}</span>`;
      return false;
    }
    statusEl.innerHTML = payload.created
      ? `<span class="ok">created ${esc(sessionLabel(session))} with Claude</span>`
      : `<span class="ok">${esc(sessionLabel(session))} ready</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session check failed: ${esc(error)}</span>`;
    return false;
  }
}

async function createNextSession(agent) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot create sessions</span>';
    return;
  }
  const agentLabel = agentName(agent) || 'agent';
  statusEl.textContent = `creating ${agentLabel} session...`;
  try {
    const response = await apiFetch(`/api/create-session?agent=${encodeURIComponent(agent)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session create failed')}</span>`;
      return;
    }
    const previousActive = activeSessions.slice();
    updateSessionList(payload.sessions || []);
    renderSessionButtons();
    renderPanels(previousActive);
    await placeTmuxSession(payload.session);
    await ensureTerminalRunning(payload.session);
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">created ${esc(sessionLabel(payload.session))} (${esc(payload.session)}) with ${esc(agentName(payload.agent) || agentLabel)}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session create failed: ${esc(error)}</span>`;
  }
}

function tmuxSessionNameError(name) {
  const text = String(name || '').trim();
  if (!text) return 'session name is required';
  if (text.length > 64) return 'session name must be 64 characters or fewer';
  // Keep in sync with TMUX_SESSION_NAME_RE in yolomux_lib/app.py.
  if (!/^[A-Za-z0-9_. -]+$/.test(text)) return 'session name may contain only letters, numbers, spaces, dot, dash, and underscore';
  return '';
}

function rekeyMap(map, oldKey, newKey) {
  if (!map.has(oldKey) || oldKey === newKey) return;
  if (!map.has(newKey)) map.set(newKey, map.get(oldKey));
  map.delete(oldKey);
}

function stopSessionUi(session) {
  const item = terminals.get(session);
  if (item) closeTerminalItem(session, item);
  terminals.delete(session);
  stopTranscriptStream(session);
  stopSummaryStream(session);
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
    statusEl.innerHTML = '<span class="err">readonly access cannot rename sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  closeContextMenus();
  closeAppMenus();
  closeSessionRenameDialog();
  const overlay = document.createElement('div');
  overlay.className = 'session-rename-backdrop';
  overlay.setAttribute('role', 'presentation');
  overlay.innerHTML = `
    <form class="session-rename-dialog" role="dialog" aria-modal="true" aria-label="Rename tmux session">
      <div class="session-rename-title">Rename ${esc(sessionLabel(session))} ${esc(session)}</div>
      <input class="session-rename-input" name="sessionName" value="${esc(session)}" aria-label="New session name" autocomplete="off">
      <div class="session-rename-error" hidden></div>
      <div class="session-rename-actions">
        <button type="button" class="session-rename-cancel">Cancel</button>
        <button type="submit" class="session-rename-submit">Rename</button>
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
      showError('rename failed; see status line');
      input.focus();
    }
  });
  document.body.appendChild(overlay);
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
    statusEl.innerHTML = '<span class="err">readonly access cannot rename sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (proposedName === undefined) return showSessionRenameDialog(session);
  const rawName = proposedName;
  const newName = String(rawName || '').trim();
  const nameError = tmuxSessionNameError(newName);
  if (nameError) {
    statusEl.innerHTML = `<span class="err">${esc(nameError)}</span>`;
    return false;
  }
  if (newName === session) {
    closeSessionRenameDialog();
    return true;
  }
  statusEl.textContent = `renaming ${sessionLabel(session)}...`;
  try {
    const response = await apiFetch(`/api/rename-session?session=${encodeURIComponent(session)}&new_name=${encodeURIComponent(newName)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session rename failed')}</span>`;
      return false;
    }
    const renamed = payload.new_session || newName;
    replaceTmuxSessionInClient(session, renamed, payload.sessions);
    closeSessionRenameDialog();
    await ensureTerminalRunning(renamed);
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">renamed ${esc(session)} to ${esc(renamed)}</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session rename failed: ${esc(error)}</span>`;
    return false;
  }
}

async function killTmuxSession(session) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot kill sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (!window.confirm(`Kill tmux session ${sessionLabel(session)}?`)) return false;
  statusEl.textContent = `killing ${sessionLabel(session)}...`;
  try {
    const response = await apiFetch(`/api/kill-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session kill failed')}</span>`;
      return false;
    }
    const previousActive = activeSessions.slice();
    stopSessionUi(session);
    const sessionsChanged = updateSessionList(payload.sessions || []);
    autoApproveStates.delete(session);
    renderSessionButtons();
    renderPanels(previousActive);
    if (sessionsChanged) renderPaneTabStrips();
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">killed ${esc(sessionLabel(session))}</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session kill failed: ${esc(error)}</span>`;
    return false;
  }
}

function focusPanel(session, options = {}) {
  const panel = document.getElementById(`panel-${session}`);
  if (!panel) return;
  if (options.userInitiated === true || options.scrollIntoView === true) {
    panel.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  if (isFileEditorItem(session)) {
    focusedTerminal = null;
    setFocusedPanelItem(session);
    if (autoFocusEnabled) {
      requestFileEditorPanelFocus(session);
      focusFileEditorPanelIfReady(panel, session);
    }
    return;
  }
  if (isVirtualItem(session)) {
    focusedTerminal = null;
    setFocusedPanelItem(session);
    if (isPreferencesItem(session) && options.userInitiated === true && autoFocusEnabled) {
      focusFreshPreferencesSearchSoon(panel);
    }
    return;
  }
  activateTab(session, 'terminal', {userInitiated: options.userInitiated === true});
}

function fitTerminal(session) {
  const item = terminals.get(session);
  if (!item || !item.term || !item.container) return;
  if (!terminalIsVisible(session, item.container)) return;
  const size = estimateTerminalSize(item.container, item.term);
  const changed = item.term.cols !== size.cols || item.term.rows !== size.rows;
  item.term.resize(size.cols, size.rows);
  if (changed) scheduleRemoteResize(session);
  refreshTerminal(session);
}

function sendRemoteResize(session) {
  const item = terminals.get(session);
  if (!item?.term || item?.socket?.readyState !== WebSocket.OPEN) return;
  item.socket.send(JSON.stringify({type: 'resize', cols: item.term.cols, rows: item.term.rows}));
}

function scheduleRemoteResize(session, delay = remoteResizeDelayMs) {
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
  requestAnimationFrame(() => fitTerminal(session));
  setTimeout(() => fitTerminal(session), 80);
  setTimeout(() => fitTerminal(session), 250);
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
    `YOLOmux - ${serverHostname}: ${sessionLabel(session)} terminal`,
    text,
    {
      container: displayToastContainer(session),
      countdownMs,
      onClick: () => selectSession(session),
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
  item.reconnectAttempt += 1;
  if (item.reconnectTimer) clearTimeout(item.reconnectTimer);
  statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} disconnected; reconnecting in ${Math.round(delay / 1000)}s</span>`;
  showTerminalConnectionToast(session, `Disconnected. Reconnecting in ${Math.round(delay / 1000)}s.`, delay);
  item.reconnectTimer = setTimeout(() => {
    if (item.manualClose || terminals.get(session) !== item || !activeSessions.includes(session)) return;
    item.reconnectTimer = null;
    connectTerminalSocket(session, item);
  }, delay);
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
    };
  }
  const probe = document.createElement('span');
  probe.textContent = 'W';
  probe.style.position = 'absolute';
  probe.style.visibility = 'hidden';
  probe.style.font = '13px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
  document.body.appendChild(probe);
  const rect = probe.getBoundingClientRect();
  probe.remove();
  const charWidth = Math.max(7, rect.width || 8);
  const charHeight = Math.max(14, rect.height || 16);
  return {
    cols: Math.max(40, Math.floor((content.width - 2) / charWidth)),
    rows: Math.max(10, Math.floor((content.height - terminalFitBottomReservePx) / charHeight)),
  };
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

function dropZoneForRect(event, rect) {
  if (!rect.width || !rect.height) return 'middle';
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (y < 0.24) return rect.height / 2 >= minSplitPaneHeightPx ? 'top' : 'middle';
  if (y > 0.76) return rect.height / 2 >= minSplitPaneHeightPx ? 'bottom' : 'middle';
  if (x < 0.24) return rect.width / 2 >= minSplitPaneWidthPx ? 'left' : 'middle';
  if (x > 0.76) return rect.width / 2 >= minSplitPaneWidthPx ? 'right' : 'middle';
  return 'middle';
}

function dropIntentForEvent(event) {
  const slotNode = event.target.closest('.drop-slot');
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

function slotIsChangesPane(slot) {
  return isChangesItem(activeItemForSide(slot));
}

function dropIntentInlineSize(intent) {
  if (!intent || typeof intent === 'string') return 0;
  const rect = intent.targetRect || intent.previewNode?.getBoundingClientRect?.();
  return Math.max(0, Number(rect?.width) || 0);
}

function itemCanSplitSinglePurposePane(item, intent) {
  const zone = typeof intent === 'string' ? intent : intent?.zone;
  if (zone !== 'top' && zone !== 'bottom') return false;
  if (isFileExplorerItem(item) || isChangesItem(item)) return false;
  const inlineSize = dropIntentInlineSize(intent);
  return !inlineSize || inlineSize >= minWidthForLayoutItem(item);
}

function dropIntentAllowsSession(session, intent) {
  if (!isLayoutItem(session) || !intent?.targetSlot) return false;
  if (slotIsFileExplorerPane(intent.targetSlot) || slotIsChangesPane(intent.targetSlot)) {
    return itemCanSplitSinglePurposePane(session, intent);
  }
  return true;
}

function clearDropPreview() {
  grid.querySelectorAll('.drag-over, .tab-drag-over, .tab-drop-preview, .drop-preview, .drop-preview-top, .drop-preview-bottom, .drop-preview-left, .drop-preview-right, .drop-preview-middle').forEach(node => {
    node.classList.remove('drag-over', 'tab-drag-over', 'tab-drop-preview', 'drop-preview', 'drop-preview-top', 'drop-preview-bottom', 'drop-preview-left', 'drop-preview-right', 'drop-preview-middle');
    node.style?.removeProperty('--tab-drop-x');
    node.style?.removeProperty('--tab-drop-y');
    node.style?.removeProperty('--tab-drop-height');
    if (node.dataset) delete node.dataset.dropLabel;
  });
}

function showDropPreview(intent) {
  clearDropPreview();
  const node = intent?.previewNode;
  if (!node) return;
  const zone = intent.zone || 'middle';
  node.classList.add('drag-over', 'drop-preview', `drop-preview-${zone}`);
  node.dataset.dropLabel = zone === 'middle' ? 'take over' : zone;
}

function dropSessionAtEvent(event) {
  const filePayload = fileDragPayload(event);
  if (filePayload?.path) {
    event.preventDefault();
    event.stopPropagation();
    const intent = dropIntentForEvent(event);
    clearDropPreview();
    if (!intent?.targetSlot) return;
    if ((slotIsFileExplorerPane(intent.targetSlot) || slotIsChangesPane(intent.targetSlot)) && intent.zone === 'middle') return;
    const zone = slotIsFileExplorerPane(intent.targetSlot) && intent.zone === 'middle' ? 'right' : intent.zone;
    openDraggedFilesInEditor(filePayload, {targetSlot: intent.targetSlot, targetZone: zone});
    return;
  }
  const payload = dragPayload(event);
  if (!payload?.session) return;
  if (event.target.closest('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    clearDropPreview();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const intent = dropIntentForEvent(event);
  clearDropPreview();
  if (!dropIntentAllowsSession(payload.session, intent)) return;
  dropSessionWithIntent(payload.session, intent, payload.sourceSlot || slotForSession(payload.session));
}

function handleDropDragOver(event) {
  const filePayload = fileDragPayload(event);
  if (filePayload?.path) {
    const intent = dropIntentForEvent(event);
    if ((slotIsFileExplorerPane(intent.targetSlot) || slotIsChangesPane(intent.targetSlot)) && intent.zone === 'middle') {
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
  if (event.target.closest('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  const intent = dropIntentForEvent(event);
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

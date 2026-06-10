// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
function renderPanels(previousActive = [], options = {}) {
  // DOIT.6 #114: a full panel re-render pools every panel and clears the grid, which detaches
  // the node being dragged and aborts the native HTML5 drag. Defer the re-render until the drag
  // ends. The shared layout scheduler stores a forced-full request so metadata-driven renders do
  // not get mistaken for a cheap same-shape layout update on drop.
  if (dragSession != null) {
    requestLayoutRender({
      previousActive,
      options,
      reason: 'renderPanels',
      forceFull: true,
    });
    return;
  }
  movePanelsToPool();
  const activePaneCount = layoutSlotKeys().filter(side => activeItemForSide(side) || paneIsPlaceholder(side)).length;
  grid.className = `grid ${activePaneCount === 1 ? 'full' : ''} ${activePaneCount === 0 ? 'empty' : ''}`.trim();
  grid.innerHTML = '';
  const tree = layoutSlots[layoutTreeKey];
  if (tree) grid.appendChild(renderLayoutRoot(tree));

  bindDropTargets();
  syncPanelVisibility(previousActive);
  renderAutoApproveButtons();
  if (options.prune === false) {
    if (responsiveLayoutPruneTimer) {
      clearTimeout(responsiveLayoutPruneTimer);
      responsiveLayoutPruneTimer = null;
    }
  } else {
    scheduleResponsiveLayoutPrune();
  }
}

function movePanelsToPool() {
  for (const [item, panel] of panelNodes.entries()) {
    if (isFileEditorItem(item)) captureFileEditorPanelViewState(item, panel);
    panel.classList.remove('active-pane');
    panel.dataset.slot = '';
    panelPool.appendChild(panel);
  }
}

// DOIT.9 S2: the registered item for a mounted panel node (reverse lookup of panelNodes).
function panelItemForNode(node) {
  if (!node) return null;
  for (const [item, panel] of panelNodes.entries()) {
    if (panel === node) return item;
  }
  return null;
}

// DOIT.9 S2: move ONE displaced panel back to the pool (preserving editor state), like movePanelsToPool
// does for all. An empty-pane placeholder (not in panelNodes) is just dropped.
function poolDisplacedPanel(node) {
  const item = panelItemForNode(node);
  if (item == null) return;
  if (isFileEditorItem(item)) captureFileEditorPanelViewState(item, node);
  node.classList.remove('active-pane');
  node.dataset.slot = '';
  panelPool.appendChild(node);
}

// DOIT.9 S2: for a SAME-SHAPE layout change, swap only the slots whose ACTIVE item changed — no
// grid.innerHTML='' / topbar teardown, no detach/reattach of unrelated panes. A pure reorder touches
// zero panels (active unchanged) and only reconciles the tab strip via updatePanelSlot.
function syncActivePanelsInPlace() {
  for (const dropSlot of grid.querySelectorAll('.drop-slot[data-slot]')) {
    const slot = dropSlot.dataset.slot;
    const item = activeItemForSide(slot);
    const mounted = dropSlot.querySelector(':scope > .panel');
    if (!item) {
      if (!mounted || !mounted.classList.contains('empty-pane-panel')) {
        poolDisplacedPanel(mounted);
        dropSlot.replaceChildren(renderEmptyPane(slot));
      }
      continue;
    }
    const desired = getOrCreatePanel(item);
    if (mounted === desired) {
      updatePanelSlot(desired, item, slot);
      continue;
    }
    poolDisplacedPanel(mounted);
    dropSlot.replaceChildren(desired);
    updatePanelSlot(desired, item, slot);
    renderAttachedPanelContent(item);
  }
}

function renderAttachedPanelContent(item) {
  const renderAttached = tabTypeForItem(item)?.renderAttached;
  if (typeof renderAttached === 'function') renderAttached(item);
}

function bindDropTargets() {
  grid.ondragover = handleDropDragOver;
  grid.ondragleave = handleDropDragLeave;
  grid.ondrop = dropSessionAtEvent;
  grid.querySelectorAll('[data-side], [data-slot]').forEach(node => {
    node.addEventListener('dragover', handleDropDragOver);
    node.addEventListener('dragleave', handleDropDragLeave);
    node.addEventListener('drop', dropSessionAtEvent);
  });
}

function renderLayoutRoot(node) {
  const section = document.createElement('section');
  section.className = 'layout-root';
  section.appendChild(renderLayoutNode(node, ''));
  return section;
}

function renderLayoutNode(node, path) {
  if (node.slot) return renderLayoutColumn(node.slot);
  const section = document.createElement('section');
  section.className = `layout-split ${node.split === 'column' ? 'split-column' : 'split-row'}`;
  section.dataset.splitPath = path;
  const children = node.children || [];
  const first = renderLayoutNode(children[0], layoutChildPath(path, 0));
  const second = renderLayoutNode(children[1], layoutChildPath(path, 1));
  const handle = document.createElement('div');
  handle.className = `layout-resizer ${node.split === 'column' ? 'resizer-column' : 'resizer-row'}`;
  handle.role = 'separator';
  handle.tabIndex = 0;
  handle.dataset.splitPath = path;
  handle.setAttribute('aria-orientation', node.split === 'column' ? 'horizontal' : 'vertical');
  handle.setAttribute('aria-label', t('pane.resize'));
  section.append(first, handle, second);
  applySplitPercentToSection(section, node.pct);
  bindLayoutResizer(handle, section, path);
  return section;
}

function layoutChildPath(path, index) {
  return path ? `${path}.${index}` : String(index);
}

function layoutNodeAtPath(path, root = layoutSlots[layoutTreeKey]) {
  let node = root;
  if (!path) return node;
  for (const part of String(path).split('.')) {
    const index = Number(part);
    if (!node?.children || !Number.isInteger(index)) return null;
    node = node.children[index];
  }
  return node || null;
}

function applySplitPercentToSection(section, pct) {
  const first = section.children[0];
  const second = section.children[2];
  if (!first || !second) return;
  const value = splitPercent(pct);
  first.style.flex = `0 1 ${value}%`;
  second.style.flex = `1 1 ${100 - value}%`;
  const handle = section.children[1];
  if (handle?.style) handle.style.setProperty('--split-percent', `${value}%`);
}

function bindLayoutResizer(handle, section, path) {
  handle.addEventListener('pointerdown', event => {
    const node = layoutNodeAtPath(path);
    if (!node || !node.children) return;
    event.preventDefault();
    event.stopPropagation();
    layoutResizeState = {section, path, pointerId: event.pointerId};
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add('layout-resizing', node.split === 'column' ? 'layout-resizing-column' : 'layout-resizing-row');
    window.addEventListener('pointermove', onLayoutResizeMove, {capture: true});
    window.addEventListener('pointerup', onLayoutResizeEnd, {capture: true});
    onLayoutResizeMove(event);
  });
}

function onLayoutResizeMove(event) {
  const state = layoutResizeState;
  if (!state) return;
  event.preventDefault();
  const node = layoutNodeAtPath(state.path);
  if (!node || !node.children) return;
  const pct = splitPercentForPointer(state.section, node, event);
  node.pct = pct;
  applySplitPercentToSection(state.section, pct);
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
}

function onLayoutResizeEnd(event) {
  if (!layoutResizeState) return;
  event.preventDefault();
  const state = layoutResizeState;
  const handle = state.section.querySelector(`:scope > .layout-resizer[data-split-path="${cssEscape(state.path)}"]`);
  try { handle?.releasePointerCapture?.(state.pointerId); } catch (_) {}
  layoutResizeState = null;
  document.body.classList.remove('layout-resizing', 'layout-resizing-column', 'layout-resizing-row');
  window.removeEventListener('pointermove', onLayoutResizeMove, {capture: true});
  window.removeEventListener('pointerup', onLayoutResizeEnd, {capture: true});
  updateActiveSessionParam();
  scheduleResponsiveLayoutPrune();
}

function splitPercentForPointer(section, nodeOrDirection, event) {
  const direction = typeof nodeOrDirection === 'string' ? nodeOrDirection : nodeOrDirection?.split;
  const rect = section.getBoundingClientRect();
  const size = direction === 'column' ? rect.height : rect.width;
  if (!size) return defaultSplitPercent;
  const offset = direction === 'column' ? event.clientY - rect.top : event.clientX - rect.left;
  const raw = (offset / size) * 100;
  const children = Array.isArray(nodeOrDirection?.children) ? nodeOrDirection.children : null;
  const minFirst = children
    ? (direction === 'column' ? layoutNodeMinHeight(children[0]) : layoutNodeMinWidth(children[0]))
    : (direction === 'column' ? rootCssLengthPx('--min-split-pane-height') || 220 : rootCssLengthPx('--min-split-pane-width') || 320);
  const minSecond = children
    ? (direction === 'column' ? layoutNodeMinHeight(children[1]) : layoutNodeMinWidth(children[1]))
    : (direction === 'column' ? rootCssLengthPx('--min-split-pane-height') || 220 : rootCssLengthPx('--min-split-pane-width') || 320);
  if (size >= minFirst + minSecond) {
    const minFirstPct = (minFirst / size) * 100;
    const maxFirstPct = 100 - (minSecond / size) * 100;
    return Math.min(maxFirstPct, Math.max(minFirstPct, raw));
  }
  return splitPercent(raw);
}

function scheduleResponsiveLayoutPrune() {
  if (layoutResizeState || responsiveLayoutPruneTimer) return;
  responsiveLayoutPruneTimer = setTimeout(() => {
    responsiveLayoutPruneTimer = null;
    requestAnimationFrame(pruneSmallLayoutSlots);
  }, 80);
}

function pruneSmallLayoutSlots() {
  if (layoutResizeState || layoutVisiblePaneCount() <= 1) return;
  const candidate = smallLayoutSlotCandidate();
  if (!candidate) return;
  const moved = paneTabs(candidate.slot);
  applyLayoutSlots(layoutWithoutSlot(candidate.slot), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} hidden from layout: not enough room` : '',
  });
}

function minWidthForLayoutItem(item) {
  const type = tabTypeForItem(item);
  if (type?.minWidth) return type.minWidth(item);
  return rootCssLengthPx('--min-split-pane-width') || 320;
}

function minWidthForLayoutSlot(slot, slots = layoutSlots) {
  return minWidthForLayoutItem(activeItemForSide(slot, slots));
}

function layoutVisiblePaneCount(slots = layoutSlots) {
  return layoutSlotKeys(slots).filter(slot => paneHasLayoutContent(slot, slots)).length;
}

function layoutNodeMinWidth(node, slots = layoutSlots) {
  if (!node) return rootCssLengthPx('--min-split-pane-width') || 320;
  if (node.slot) return paneHasLayoutContent(node.slot, slots) ? minWidthForLayoutSlot(node.slot, slots) : rootCssLengthPx('--min-split-pane-width') || 320;
  const children = node.children || [];
  if (node.split === 'column') return Math.max(...children.map(child => layoutNodeMinWidth(child, slots)), rootCssLengthPx('--min-split-pane-width') || 320);
  return children.reduce((sum, child) => sum + layoutNodeMinWidth(child, slots), 0);
}

function layoutNodeMinHeight(node, slots = layoutSlots) {
  if (!node) return rootCssLengthPx('--min-split-pane-height') || 220;
  if (node.slot) return paneHasLayoutContent(node.slot, slots) ? rootCssLengthPx('--min-split-pane-height') || 220 : rootCssLengthPx('--min-split-pane-height') || 220;
  const children = node.children || [];
  if (node.split === 'column') return children.reduce((sum, child) => sum + layoutNodeMinHeight(child, slots), 0);
  return Math.max(...children.map(child => layoutNodeMinHeight(child, slots)), rootCssLengthPx('--min-split-pane-height') || 220);
}

function prunePriorityForLayoutSlot(slot) {
  const item = activeItemForSide(slot);
  const type = tabTypeForItem(item);
  if (type?.prunePriority) return type.prunePriority(item);
  return 2;
}

function slotCanAutoPrune(slot) {
  return !isFileExplorerItem(activeItemForSide(slot));
}

function smallLayoutSlotCandidate() {
  let candidate = null;
  let virtualCandidate = null;
  for (const column of grid.querySelectorAll('.layout-column[data-slot]')) {
    const slot = column.dataset.slot;
    if (!slot || !paneTabs(slot).length) continue;
    if (!slotCanAutoPrune(slot)) continue;
    const rect = column.getBoundingClientRect();
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    const priority = prunePriorityForLayoutSlot(slot);
    const item = activeItemForSide(slot);
    if (isVirtualItem(item) && (!virtualCandidate || area < virtualCandidate.area)) {
      virtualCandidate = {slot, area, priority};
    }
    const tooSmall = rect.width < minWidthForLayoutSlot(slot) || rect.height < (rootCssLengthPx('--min-split-pane-height') || 220);
    if (!tooSmall) continue;
    const nextCandidate = {slot, area, priority};
    if (!candidate || priority < candidate.priority || (priority === candidate.priority && area < candidate.area)) {
      candidate = nextCandidate;
    }
  }
  if (candidate && prunePriorityForLayoutSlot(candidate.slot) >= 2 && virtualCandidate) return virtualCandidate;
  return candidate;
}

function renderLayoutColumn(side) {
  const column = document.createElement('section');
  const session = activeItemForSide(side);
  column.className = 'layout-column';
  if (isFileExplorerItem(session)) column.classList.add('file-explorer-column');
  if (isPreferencesItem(session)) column.classList.add('preferences-column');
  if (isFileEditorItem(session)) column.classList.add('file-editor-column');
  if (!session) column.classList.add('empty-pane-column');
  column.dataset.slot = side;
  column.dataset.side = slotSide(side);
  column.appendChild(renderDropSlot(side, session));
  return column;
}

function renderDropSlot(slot, session) {
  const node = document.createElement('section');
  node.className = 'drop-slot';
  node.dataset.slot = slot;
  node.dataset.side = slotSide(slot);
  if (!session) {
    node.appendChild(renderEmptyPane(slot));
    return node;
  }
  const panel = getOrCreatePanel(session);
  updatePanelSlot(panel, session, slot);
  node.appendChild(panel);
  renderAttachedPanelContent(session);
  return node;
}

function renderEmptyPane(slot) {
  const panel = document.createElement('article');
  panel.className = 'panel empty-pane-panel';
  panel.dataset.slot = slot;
  panel.setAttribute('aria-label', t('pane.empty'));
  panel.appendChild(document.createElement('div'));
  panel.children[0].className = 'empty-pane-fill';
  return panel;
}

function renderPaneTabStrips() {
  // DOIT.6 #30: do not rebuild tab DOM while a tab is being dragged — replacing the dragged node
  // aborts the native drag. Defer to the endSessionDrag flush.
  if (dragSession != null) { pendingTabStripRender = true; return; }
  for (const side of layoutSlotKeys()) {
    const session = activeItemForSide(side);
    if (!session) continue;
    const panel = panelNodes.get(session);
    if (panel) {
      updatePaneExpandButton(panel, session);
      updatePaneTabStrip(panel, side);
    }
  }
}

function updatePaneTabStrip(panel, side) {
  const strip = panel.querySelector('.pane-tabs');
  if (!strip) return;
  const stack = paneTabs(side);
  strip.dataset.side = side;
  if (isFileExplorerItem(activeItemForSide(side))) {
    strip.hidden = true;
    strip.replaceChildren();
    return;
  }
  strip.hidden = false;
  const restorePopoverItem = paneTabPopoverItemToRestore(strip);
  const activeItem = activeItemForSide(side);
  const items = stack.slice();
  if (activeItem && !items.includes(activeItem)) items.push(activeItem);
  reconcilePaneTabChildren(strip, side, items, paneTabDisplayContext(items));
  bindPaneTabStrip(strip, side);
  restorePaneTabPopover(strip, restorePopoverItem);
  scheduleTabStripOverflowCheck(strip);
}

function reconcilePaneTabChildren(strip, side, items, displayContext = {}) {
  const existingByItem = new Map(Array.from(strip.querySelectorAll(':scope > .pane-tab')).map(tab => [tab.dataset.paneTab || '', tab]));
  const nextNodes = [];
  for (const item of items) {
    const fresh = createPaneTab(side, item, displayContext);
    const existing = existingByItem.get(item);
    if (existing && paneTabShouldPreserve(existing)) {
      syncPreservedPaneTab(existing, fresh);
      nextNodes.push(existing);
    } else {
      nextNodes.push(fresh);
    }
    existingByItem.delete(item);
  }
  for (const leftover of existingByItem.values()) leftover.remove();
  reconcileChildNodes(strip, nextNodes, {lockedNodes: nextNodes.filter(paneTabShouldPreserve)});
}

function paneTabDisplayContext(items) {
  return {fileParentLabels: fileTabParentDisambiguators(items)};
}

function fileTabParentDisambiguators(items) {
  const byName = new Map();
  for (const item of items) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    if (!path) continue;
    const name = basenameOf(path);
    if (!byName.has(name)) byName.set(name, new Map());
    byName.get(name).set(path, item);
  }
  const labels = new Map();
  for (const pathsByName of byName.values()) {
    const paths = Array.from(pathsByName.keys());
    if (paths.length <= 1) continue;
    const parentSegments = paths.map(path => dirnameOf(path).split('/').filter(Boolean));
    const maxDepth = Math.max(1, ...parentSegments.map(parts => parts.length));
    let depth = 1;
    for (; depth <= maxDepth; depth++) {
      const seen = new Set(parentSegments.map(parts => fileTabParentSuffix(parts, depth)));
      if (seen.size === paths.length) break;
    }
    for (let index = 0; index < paths.length; index++) {
      labels.set(paths[index], fileTabParentSuffix(parentSegments[index], depth));
    }
  }
  return labels;
}

function fileTabParentSuffix(segments, depth) {
  if (!segments.length) return '/';
  return segments.slice(Math.max(0, segments.length - depth)).join('/');
}

function paneTabShouldPreserve(tab) {
  const popover = tab.querySelector(':scope > .session-popover');
  return Boolean(popover && (popoverLifecycleActive(tab, popover) || popoverStillActive(tab, popover)));
}

function classNameSet(value) {
  return new Set(String(value || '').split(/\s+/).filter(Boolean));
}

function syncClassListPreserving(node, freshClassName, preservedNames = []) {
  const current = classNameSet(node.className);
  const next = classNameSet(freshClassName);
  for (const name of preservedNames) {
    if (current.has(name) || node.classList?.contains?.(name)) next.add(name);
  }
  for (const name of current) {
    if (!next.has(name)) node.classList?.remove?.(name);
  }
  for (const name of next) {
    if (!current.has(name)) node.classList?.add?.(name);
  }
  // Test fakes do not keep className and classList synchronized; browsers do.
  if (!(globalThis.DOMTokenList && node.classList instanceof globalThis.DOMTokenList)) {
    node.className = Array.from(next).join(' ');
  }
}

function syncPreservedPaneTab(tab, fresh) {
  const hoverState = tab.dataset.popoverHoverState || '';
  syncClassListPreserving(tab, fresh.className, ['popover-open', 'dragging']);
  tab.role = fresh.role;
  tab.tabIndex = fresh.tabIndex;
  tab.draggable = fresh.draggable;
  tab.dataset.paneTab = fresh.dataset.paneTab;
  if (hoverState) tab.dataset.popoverHoverState = hoverState;
  else delete tab.dataset.popoverHoverState;
  const label = fresh.getAttribute('aria-label');
  if (label) tab.setAttribute('aria-label', label);
  else tab.removeAttribute('aria-label');
}

function paneTabPopoverItemToRestore(strip) {
  for (const tab of strip.querySelectorAll(':scope > .pane-tab')) {
    const popover = tab.querySelector(':scope > .session-popover');
    if (popover && (popoverLifecycleActive(tab, popover) || popoverStillActive(tab, popover))) return tab.dataset.paneTab || null;
  }
  return null;
}

function restorePaneTabPopover(strip, item) {
  if (!item) return;
  const tab = strip.querySelector(`:scope > .pane-tab[data-pane-tab="${cssEscape(item)}"]`);
  const popover = tab?.querySelector(':scope > .session-popover');
  if (!tab || !popover) return;
  if (tab.classList.contains('popover-open') && popoverLifecycleActive(tab, popover)) return;
  positionPaneTabPopover(tab);
  closeOtherSessionPopovers(tab);
  tab.classList.add('popover-open');
}

function createPaneTab(side, item, displayContext = {}) {
  const type = tabTypeForItem(item);
  const isFiles = type?.key === 'files';
  const isEditor = isFileEditorItem(item);
  const isVirtual = Boolean(type);
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true && !isVirtual;
  const state = isVirtual ? null : sessionState(item, info);
  const agentKind = isVirtual ? '' : sessionAgentKind(item);
  const active = item === activeItemForSide(side);
  const tab = document.createElement('div');
  tab.role = 'button';
  tab.tabIndex = 0;
  const virtualClass = type?.className?.(item) || '';
  const missingFileClass = isEditor && openFileIsMissing(fileItemPath(item)) ? 'file-missing' : '';
  tab.className = `pane-tab ${virtualClass} ${missingFileClass} ${active ? 'active' : ''}`;
  applySessionStateClasses(tab, state);
  tab.draggable = true;
  tab.dataset.paneTab = item;
  const rowOptions = isEditor ? {parentLabel: displayContext.fileParentLabels?.get(fileItemPath(item)) || ''} : {};
  if (type?.rowHtml) tab.innerHTML = type.rowHtml(item, rowOptions);
  else tab.innerHTML = tmuxPaneTabHtml(item, info, state, auto);
  if (!isFiles) {
    const closeTitle = isEditor ? `Close ${itemLabel(item)}` : `hide ${itemLabel(item)} from layout`;
    const closeLabel = isEditor ? `Close ${itemLabel(item)}` : `Hide ${itemLabel(item)} from layout`;
    const controlKind = isEditor ? 'close' : 'minimize';
    tab.insertAdjacentHTML('beforeend', `<button type="button" class="pane-tab-close ${platformWindowControlClass(controlKind)}" data-pane-tab-close title="${esc(closeTitle)}" aria-label="${esc(closeLabel)}"></button>`);
  }
  if (isEditor) {
    tab.insertAdjacentHTML('beforeend', filePopoverHtml(item));
    bindFilePopoverActions(tab);
    bindPaneTabPopover(tab, item);
  } else if (!isVirtual) {
    tab.insertAdjacentHTML('beforeend', sessionPopoverHtml(item, info, agentKind, auto, state));
    bindPaneTabPopover(tab, item);
  }
  tab.setAttribute('aria-label', isEditor
    ? `${itemLabel(item)} ${fileItemPath(item)}${missingFileClass ? ' missing on disk' : ''}`
    : type ? itemLabel(item) : `${sessionLabel(item)} ${sessionWorkDescription(item, info, 140)}`.trim());
  tab.addEventListener('pointerdown', event => {
    dragTimingReset();           // S14: starts the opt-in drag-timing window (no-op unless the flag is on)
    dragTimingMark('pointerdown');
    if (event.target.closest('[data-pane-tab-close]')) {
      event.stopPropagation();
      return;
    }
    const autoTarget = event.target.closest('[data-auto-session]');
    if (!autoTarget) return;
    event.preventDefault();
    event.stopPropagation();
    if (item === activeItemForSide(side)) setFocusedPanelItem(item);
  });
  tab.addEventListener('click', async event => {
    if (event.target.closest('[data-pane-tab-close]')) {
      event.preventDefault();
      event.stopPropagation();
      if (isEditor) closeFileTab(fileItemPath(item), {item});
      else removeSessionFromLayout(item);
      return;
    }
    const autoTarget = event.target.closest('[data-auto-session]');
    if (autoTarget) {
      event.preventDefault();
      event.stopPropagation();
      const shouldRefocus = item === activeItemForSide(side);
      await toggleAutoApprove(autoTarget.dataset.autoSession);
      if (shouldRefocus) focusPanel(item);
      return;
    }
  });
  tab.addEventListener('keydown', event => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    activatePaneTab(side, item, {userInitiated: true});
  });
  bindTabActivation(tab, () => activatePaneTab(side, item, {userInitiated: true}), {
    stopPropagation: true,
    ignore: event => Boolean(event.target.closest('[data-auto-session], [data-pane-tab-close]')),
  });
  if (isEditor) {
    tab.addEventListener('dblclick', event => {
      if (event.target.closest('[data-pane-tab-close]')) return;
      event.preventDefault();
      event.stopPropagation();
      beginPaneTabRename(tab, item);
    });
  } else if (!isVirtual) {
    tab.addEventListener('dblclick', event => {
      if (event.target.closest('[data-auto-session], [data-pane-tab-close]')) return;
      event.preventDefault();
      event.stopPropagation();
      beginPaneTabRename(tab, item);
    });
    tab.addEventListener('contextmenu', event => {
      event.preventDefault();
      event.stopPropagation();
      showSessionContextMenu(item, event.clientX, event.clientY, {tab});
    });
  }
  tab.addEventListener('dragstart', event => {
    event.stopPropagation();
    startSessionDrag(event, item, side);
  });
  tab.addEventListener('dragend', endSessionDrag);
  return tab;
}

function beginPaneTabRename(tab, session) {
  if (isFileEditorItem(session)) {
    beginFileTabRename(tab, session);
    return;
  }
  if (isTmuxSession(session)) renameTmuxSession(session);
}

function beginFileTabRename(tab, item) {
  const path = fileItemPath(item);
  if (!path) return;
  const entry = {kind: 'file', name: basenameOf(path)};
  const row = document.querySelector(`.file-tree-row[data-path="${cssEscape(path)}"]`);
  if (row) {
    beginFileTreeRename(row, path, entry);
    return;
  }
  if (readOnlyMode) {
    statusErr('readonly access cannot rename files');
    return;
  }
  const currentName = basenameOf(path);
  const nextName = window.prompt(`Rename ${currentName}`, currentName);
  if (nextName === null) return;
  renameFileTreePath(path, entry, nextName);
}

function bindPaneTabPopover(tab, session) {
  const popover = tab.querySelector?.(':scope > .session-popover');
  if (!popover) return;
  bindDelayedSessionPopover(tab, popover, () => positionPaneTabPopover(tab), {
    onOpen: () => maybeLoadFileTabForPopover(tab, session),
  });
}

function bindDelayedSessionPopover(anchor, popover, position, options = {}) {
  createHoverPopover({
    anchor,
    popover,
    showDelay: () => (document.querySelector('.pane-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
    hideDelay: () => popoverHideDelayMs,
    canOpen: () => !appMenuIsOpen() && !topbar?.matches?.(':hover'),
    onQueue: position,
    onOpen: options.onOpen,
    position,
    closeOthers: () => closeOtherSessionPopovers(anchor),
  });
}

function maybeLoadFileTabForPopover(tab, item) {
  if (!isFileEditorItem(item)) return;
  const state = ensureFileTabStateForItem(item);
  refreshFileTabPopover(tab, item);
  if (!state?.loading) return;
  const path = fileItemPath(item);
  if (!state.loadingPromise) loadFileEditorState(path, panelNodes.get(item), item);
  const pending = openFiles.get(path)?.loadingPromise;
  pending?.finally?.(() => refreshFilePopoversForPath(path));
}

function ensureFileTabStateForItem(item) {
  const path = fileItemPath(item);
  if (!path) return null;
  if (!isImageViewerItem(item)) addFileEditorTabItem(path, item);
  let state = fileStateFor(path);
  if (!state || !state.kind) {
    state = ensureFileState(path, {
      mtime: 0,
      kind: isImageViewerItem(item) ? 'image' : 'file',
      original: '',
      content: '',
      dirty: false,
      loading: true,
    });
  }
  return state;
}

function refreshFilePopoversForPath(path) {
  for (const item of [imageViewerItemFor(path), fileEditorItemFor(path)]) {
    document.querySelectorAll(`.pane-tab[data-pane-tab="${cssEscape(item)}"]`).forEach(tab => refreshFileTabPopover(tab, item));
  }
}

function refreshFileTabPopover(tab, item) {
  const popover = tab?.querySelector?.(':scope > .file-popover');
  if (!popover) return;
  const path = fileItemPath(item);
  const rows = filePopoverRows(path, fileStateFor(path) || {});
  popover.innerHTML = `
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(basenameOf(path))}</div>
      </div>
    </div>
    ${rows.join('')}`;
  bindFilePopoverActions(popover);
}

function positionPaneTabPopover(tab) {
  const rect = tab.getBoundingClientRect();
  const popover = tab.querySelector?.(':scope > .session-popover');
  const bridgeGap = 3;
  const edgeGap = popoverEdgeGapPx();
  const topbarBottom = Math.ceil(topbar?.getBoundingClientRect?.().bottom || rootCssLengthPx('--topbar-height') || 0);
  const bounds = viewportBounds(edgeGap);
  const maxInline = Math.max(0, bounds.right - bounds.left);
  // #45: when the live popover width measures 0 (e.g. before first paint), fall back to the popover's
  // CSS inline size (capped to the viewport) — NOT the tiny tab width. Over-estimating only pulls a
  // near-right-edge popover further left; clamping to the tab width let a wide needs-input popover
  // overflow and clip off the top-right corner.
  const measured = Math.ceil(popover?.getBoundingClientRect?.().width || 0);
  const width = Math.min(maxInline, measured || rootCssLengthPx('--pane-tab-popover-inline-size') || maxInline);
  const height = Math.ceil(popover?.getBoundingClientRect?.().height || 0);
  const position = clampToViewport(
    Math.floor(rect.left),
    Math.ceil(rect.bottom) + bridgeGap,
    width,
    height,
    {edgeGap, minTop: topbarBottom + edgeGap},
  );
  document.documentElement.style.setProperty('--pane-tab-popover-top', `${Math.round(position.top)}px`);
  document.documentElement.style.setProperty('--pane-tab-popover-left', `${Math.round(position.left)}px`);
}

function paneInfoTabHtml(item = infoItemId, options = {}) {
  // DOIT.6: use .session-button-dir (like the Finder/Prefs tabs) so the label gets the themed
  // active/inactive colors; the old .pane-tab-info-label set no color and went white-on-white in light.
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir pane-tab-info-label">${esc(itemLabel(item))}</span></span>`;
}

function fileExplorerPaneTabHtml(item = fileExplorerItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">${esc(fileExplorerLabel())}</span></span>`;
}

function preferencesPaneTabHtml(item = prefsItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">${esc(t('tab.preferences'))}</span></span>`;
}

function debugPaneTabHtml(item = debugPaneItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">${esc(t('tab.debug'))}</span></span>`;
}

function fileEditorPaneTabHtml(item, options = {}) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const owners = openFileOwnerSessionsForPath(path);
  const ownerTitle = owners.length > 1 ? t('filetab.ownersMulti', {sessions: owners.join(', ')}) : owners[0] ? t('filetab.owner', {session: owners[0]}) : '';
  const ownerText = owners.length > 1 ? t('filetab.multi') : owners[0] || '';
  const owner = ownerText ? `<span class="file-tab-owner" title="${esc(ownerTitle)}">${esc(ownerText)}</span>` : '';
  const dirty = state.dirty ? `<span class="file-tab-dirty" title="${esc(t('filetab.modified'))}" aria-label="${esc(t('filetab.modified'))}"></span>` : '';
  const missing = openFileIsMissing(path) ? `<span class="file-tab-missing-badge" title="${esc(t('filetab.missingTitle'))}" aria-label="${esc(t('filetab.missingTitle'))}">${esc(t('filetab.missing'))}</span>` : '';
  const parentLabel = options.parentLabel ? `<span class="file-tab-parent" title="${esc(path)}">${esc(options.parentLabel)}</span>` : '';
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-text">${owner}${dirty}${missing}<span class="session-button-dir">${esc(basenameOf(path))}</span>${parentLabel}</span></span>`;
}

function tmuxPaneTabHtml(session, info, state, auto) {
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(session, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="pane-tab-core">${yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: true, yoloWorking: sessionYoloIsWorking(session)})}<span class="session-button-prefix">${sessionNumberNameHtml(session)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(session, info)}${pullRequestCompactBadgesHtml(session, pr)}${detailHtml}</span></span>`;
}

function bindPaneTabStrip(strip, side) {
  strip.ondragover = event => {
    const filePayload = fileDragPayload(event);
    if (filePayload?.path) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (slotIsFileExplorerPane(side)) {
        event.dataTransfer.dropEffect = 'none';
        clearPaneTabDropPreview(strip);
        return;
      }
      event.dataTransfer.dropEffect = 'copy';
      clearDropPreview();
      showPaneTabDropPreview(strip, event, '');
      return;
    }
    const payload = dragPayload(event);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (slotIsFileExplorerPane(side)) {
      event.dataTransfer.dropEffect = 'none';
      clearPaneTabDropPreview(strip);
      return;
    }
    event.dataTransfer.dropEffect = 'move';
    clearDropPreview();
    showPaneTabDropPreview(strip, event, payload.session);
  };
  strip.ondragleave = event => {
    event.stopImmediatePropagation();
    if (!strip.contains(event.relatedTarget)) clearPaneTabDropPreview(strip);
  };
  strip.ondrop = event => {
    const filePayload = fileDragPayload(event);
    if (filePayload?.path) {
      clearPaneTabDropPreview(strip);
      event.preventDefault();
      event.stopImmediatePropagation();
      if (slotIsFileExplorerPane(side)) return;
      openDraggedFilesInEditor(filePayload, {targetSlot: side, targetIndex: paneTabDropIndex(strip, event, '')});
      return;
    }
    const payload = dragPayload(event);
    clearPaneTabDropPreview(strip);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (slotIsFileExplorerPane(side)) return;
    moveSessionToSlot(payload.session, side, payload.sourceSlot || slotForSession(payload.session), paneTabDropIndex(strip, event, payload.session));
  };
}

function showPaneTabDropPreview(strip, event, movingSession) {
  const placement = paneTabDropPlacement(strip, event, movingSession);
  strip.style.setProperty('--tab-drop-x', `${Math.round(placement.x)}px`);
  strip.style.setProperty('--tab-drop-y', `${Math.round(placement.y)}px`);
  strip.style.setProperty('--tab-drop-height', `${Math.round(placement.height)}px`);
  strip.classList.add(CLS.dragOver, CLS.tabDropPreview);
}

function clearPaneTabDropPreview(strip) {
  strip.classList.remove(CLS.dragOver, CLS.tabDropPreview);
  strip.style.removeProperty('--tab-drop-x');
  strip.style.removeProperty('--tab-drop-y');
  strip.style.removeProperty('--tab-drop-height');
}

// #47: reset the per-drag tab-rect cache (called at drag start/end). See dragMeasureStrip.
function resetDragTabRectCache() {
  dragTabRectCache = null;
}

// During a live drag the tabs do not move (renders are deferred, #30), so measure each strip's rect +
// its tab rects ONCE and reuse them for every dragover instead of forcing sync layout on every move.
// Outside a drag (e.g. unit tests calling paneTabDropPlacement directly), measure fresh each time.
function dragMeasureStrip(strip) {
  dragTimingMarkOnce('dragMeasureStrip:first');   // S14: first strip getBoundingClientRect of the drag
  if (dragSession != null) {
    if (!dragTabRectCache || dragTabRectCache.strip !== strip) {
      dragTabRectCache = {strip, stripRect: strip.getBoundingClientRect(), rects: new Map()};
    }
    return dragTabRectCache;
  }
  return {strip, stripRect: strip.getBoundingClientRect(), rects: new Map()};
}

function dragMeasureTab(cache, tab) {
  let rect = cache.rects.get(tab);
  if (!rect) {
    rect = tab.getBoundingClientRect();
    cache.rects.set(tab, rect);
  }
  return rect;
}

function paneTabDropPlacement(strip, event, movingSession) {
  dragTimingMarkOnce('paneTabDropPlacement:first');   // S14: first drop-placement pass (first real dragover)
  const allTabs = Array.from(strip.querySelectorAll('.pane-tab'));
  // The source's position in the FULL strip (before filtering) drives a directional insert threshold
  // for same-strip reorder; -1 means a cross-pane move (keep the centered threshold).
  const sourceVisualIndex = movingSession ? allTabs.findIndex(tab => tab.dataset.paneTab === movingSession) : -1;
  const tabs = allTabs.filter(tab => tab.dataset.paneTab !== movingSession);
  const measure = dragMeasureStrip(strip);
  const stripRect = measure.stripRect;
  const clampX = value => Math.max(2, Math.min(stripRect.width - 2, value));
  const clampY = (value, height) => Math.max(0, Math.min(Math.max(0, stripRect.height - height), value));
  const defaultHeight = Math.min(32, Math.max(24, stripRect.height || 27));
  if (!tabs.length) {
    return {
      index: 0,
      x: clampX(event.clientX - stripRect.left),
      y: clampY(event.clientY - stripRect.top - defaultHeight / 2, defaultHeight),
      height: defaultHeight,
    };
  }
  const rows = paneTabRows(tabs, tab => dragMeasureTab(measure, tab));
  const row = rows.reduce((best, item) => {
    const distance = Math.abs(event.clientY - item.centerY);
    return !best || distance < best.distance ? {row: item, distance} : best;
  }, null)?.row || rows[0];
  const rowTabs = row.items.slice().sort((left, right) => left.rect.left - right.rect.left);
  const sameStrip = sourceVisualIndex >= 0;
  for (const item of rowTabs) {
    const rect = item.rect;
    // Cross-pane drops insert at the tab center. Same-strip reorder uses the FAR edge relative to the
    // source (item.index >= sourceVisualIndex means the source sits to its LEFT) so dropping the source
    // anywhere on a neighbor moves it PAST that neighbor — fixes left->right needing a center overshoot.
    let threshold = rect.left + rect.width / 2;
    if (sameStrip) threshold = item.index >= sourceVisualIndex ? rect.left : rect.right;
    if (event.clientX < threshold) {
      return {
        index: item.index,
        x: clampX(rect.left - stripRect.left),
        y: clampY(row.top - stripRect.top, row.height),
        height: row.height,
      };
    }
  }
  const last = rowTabs[rowTabs.length - 1];
  return {
    index: last.index + 1,
    x: clampX(last.rect.right - stripRect.left),
    y: clampY(row.top - stripRect.top, row.height),
    height: row.height,
  };
}

function paneTabRows(tabs, rectFor = tab => tab.getBoundingClientRect()) {
  const rows = [];
  tabs.forEach((tab, index) => {
    const rect = rectFor(tab);
    const centerY = rect.top + rect.height / 2;
    const row = rows.find(item => Math.abs(centerY - item.centerY) <= Math.max(4, item.height / 2));
    const target = row || {items: [], top: rect.top, bottom: rect.bottom, centerY, height: rect.height};
    target.items.push({index, rect});
    target.top = Math.min(target.top, rect.top);
    target.bottom = Math.max(target.bottom, rect.bottom);
    target.height = Math.max(1, target.bottom - target.top);
    target.centerY = target.top + target.height / 2;
    if (!row) rows.push(target);
  });
  return rows.sort((left, right) => left.top - right.top);
}

function paneTabDropIndex(strip, event, movingSession) {
  return paneTabDropPlacement(strip, event, movingSession).index;
}

function getOrCreatePanel(session) {
  let panel = panelNodes.get(session);
  if (panel) return panel;
  const type = tabTypeForItem(session);
  if (type?.createPanel) panel = type.createPanel(session);
  else panel = createPanel(session);
  panelNodes.set(session, panel);
  panelPool.appendChild(panel);
  return panel;
}

function bindPanelShell(panel, session) {
  // Inactive-pane dimming is pure CSS now (`.panel:not(.focused-pane) .panel-overlay-root::after`,
  // keyed off the uniformly-toggled .focused-pane class) — no per-pane overlay <div> to install.
  bindPanelPopover(panel);
  bindPaneFrameControls(panel, session);
  panel.addEventListener('pointerenter', () => selectPanelOnHover(session));
  panel.addEventListener('pointerdown', event => {
    if (isTmuxSession(session)) {
      noteFileExplorerChangesSessionInteraction(session);
      setFocusedTerminal(session, {userInitiated: true});
    } else {
      setFocusedPanelItem(session, {userInitiated: true});
    }
  }, {capture: true});
  panel.addEventListener('focusin', event => {
    if (!isTmuxSession(session)) {
      setFocusedPanelItem(session);
    }
  });
  const head = panel.querySelector('.panel-head');
  if (head) {
    head.draggable = true;
    head.dataset.dragSession = session;
    head.addEventListener('dragstart', event => startSessionDrag(event, session, head.dataset.dragSlot || null));
    head.addEventListener('dragend', endSessionDrag);
    head.addEventListener('dragover', event => {
      const filePayload = fileDragPayload(event);
      if (filePayload?.path) {
        event.preventDefault();
        event.stopPropagation();
        clearDropPreview();
        const targetSlot = head.dataset.dragSlot || slotForSession(session);
        if (slotIsFileExplorerPane(targetSlot)) {
          event.dataTransfer.dropEffect = 'none';
          return;
        }
        event.dataTransfer.dropEffect = 'copy';
        head.classList.add(CLS.tabDragOver);
        return;
      }
      const payload = dragPayload(event);
      if (!payload?.session) return;
      event.preventDefault();
      event.stopPropagation();
      clearDropPreview();
      if (event.target.closest('.pane-tabs')) return;
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (slotIsFileExplorerPane(targetSlot)) {
        event.dataTransfer.dropEffect = 'none';
        return;
      }
      event.dataTransfer.dropEffect = 'move';
      head.classList.add(CLS.tabDragOver);
    });
    head.addEventListener('dragleave', event => {
      if (!head.contains(event.relatedTarget)) head.classList.remove(CLS.tabDragOver);
    });
    head.addEventListener('drop', event => {
      const filePayload = fileDragPayload(event);
      if (filePayload?.path && !event.target.closest('.pane-tabs')) {
        head.classList.remove(CLS.tabDragOver);
        event.preventDefault();
        event.stopPropagation();
        const targetSlot = head.dataset.dragSlot || slotForSession(session);
        if (slotIsFileExplorerPane(targetSlot)) return;
        if (targetSlot) openDraggedFilesInEditor(filePayload, {targetSlot});
        return;
      }
      const payload = dragPayload(event);
      head.classList.remove(CLS.tabDragOver);
      if (!payload?.session || event.target.closest('.pane-tabs')) return;
      event.preventDefault();
      event.stopPropagation();
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (!targetSlot) return;
      if (slotIsFileExplorerPane(targetSlot)) return;
      if (isFileExplorerItem(payload.session)) {
        dockFileExplorerPane();
        return;
      }
      moveSessionToSlot(payload.session, targetSlot, payload.sourceSlot || slotForSession(payload.session), paneTabs(targetSlot).length);
    });
  }
  panel.querySelector('[data-detail-toggle]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
  });
}

function bindPaneFrameControls(panel, session) {
  if (!panel || panel.dataset.frameControlsBound === 'true') return;
  panel.dataset.frameControlsBound = 'true';
  panel.addEventListener('click', async event => {
    const button = event.target.closest('[data-pane-actions], [data-pane-minimize], [data-pane-expand], [data-pane-close]');
    if (!button || !panel.contains(button)) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    if (button.dataset.paneActions !== undefined) {
      const rect = button.getBoundingClientRect();
      showSessionContextMenu(button.dataset.paneActions || session, rect.left, rect.bottom + 4);
      return;
    }
    if (button.dataset.paneMinimize !== undefined) {
      minimizePaneFromLayout(button.dataset.paneMinimize || session);
      return;
    }
    if (button.dataset.paneExpand !== undefined) {
      expandPaneFromLayout(button.dataset.paneExpand || session);
      return;
    }
    if (button.dataset.paneClose !== undefined) {
      removePaneFromLayout(button.dataset.paneClose || session);
    }
  }, true);
}

function bindPanelPopover(panel) {
  const zone = panel.querySelector('.panel-popover-zone');
  if (!zone || zone.dataset.popoverBound === 'true') return;
  zone.dataset.popoverBound = 'true';
  createHoverPopover({
    anchor: zone,
    popover: () => zone.querySelector(':scope > .session-popover'),
    showDelay: 0,
    hideDelay: () => popoverHideDelayMs,
  });
}

function setPanelDetailsCollapsed(panel, collapsed) {
  panel.classList.toggle('details-collapsed', collapsed);
  const button = panel.querySelector('[data-detail-toggle]');
  if (button) {
    button.classList.toggle('active', !collapsed);
    const detailsLabel = collapsed ? t('pane.details.show') : t('pane.details.hide');
    button.title = detailsLabel;
    button.setAttribute('aria-label', detailsLabel);
    button.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
  }
}

function terminalTabLabel(session, info) {
  const type = tabTypeForItem(session);
  if (type?.shortLabel) return type.shortLabel(session);
  const label = terminalProcessLabel(info);
  return shortText(label || 'Term', 16);
}

function terminalTabTitle(session, info) {
  const type = tabTypeForItem(session);
  if (type?.terminalTitle) return type.terminalTitle(session);
  return `terminal: ${terminalProcessLabel(info) || 'Term'}`;
}

function terminalProcessLabel(info) {
  const pane = terminalDisplayPane(info);
  if (pane?.process_label) return pane.process_label;
  const agent = agentForPane(info, pane) || info?.agents?.[0];
  if (agent?.command) return processLabelFromCommand(agent.command);
  if (agent?.kind) return agent.kind;
  if (pane?.command) return pane.command;
  return 'Term';
}

function terminalDisplayPane(info) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  return panes.find(pane => pane.window_active && pane.active)
    || panes.find(pane => pane.window_active)
    || info?.selected_pane
    || panes[0]
    || null;
}

function tmuxWindowNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function tmuxWindowIndices(panes) {
  const windows = [];
  const seen = new Set();
  for (const pane of Array.isArray(panes) ? panes : []) {
    const index = tmuxWindowNumber(pane.window);
    if (index === null || seen.has(index)) continue;
    seen.add(index);
    windows.push(index);
  }
  return windows.sort((left, right) => left - right);
}

function windowStepVisibility(panes) {
  const windows = tmuxWindowIndices(panes);
  if (windows.length <= 1) return {prev: false, next: false};
  const activeIndex = tmuxWindowNumber((Array.isArray(panes) ? panes : []).find(pane => pane.window_active)?.window) ?? windows[0];
  return {
    prev: windows.some(index => index < activeIndex),
    next: windows.some(index => index > activeIndex),
  };
}

function previewTmuxWindowInfo(info, key) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const windows = tmuxWindowIndices(panes);
  if (windows.length < 2) return null;
  const activePane = terminalDisplayPane(info);
  const activeIndex = tmuxWindowNumber(activePane?.window);
  const current = Math.max(0, windows.findIndex(index => index === activeIndex));
  const delta = key === 'p' ? -1 : 1;
  const target = windows[(current + delta + windows.length) % windows.length];
  const nextPanes = panes.map(pane => ({...pane, window_active: tmuxWindowNumber(pane.window) === target}));
  return {
    ...info,
    panes: nextPanes,
    selected_pane: nextPanes.find(pane => pane.window_active && pane.active)
      || nextPanes.find(pane => pane.window_active)
      || info?.selected_pane
      || null,
  };
}

function previewTmuxWindowLabel(session, key) {
  const info = transcriptMeta.sessions?.[session];
  const nextInfo = previewTmuxWindowInfo(info, key);
  if (!nextInfo) return;
  transcriptMeta = {
    ...transcriptMeta,
    sessions: {
      ...(transcriptMeta.sessions || {}),
      [session]: nextInfo,
    },
  };
  updatePanelControlLabels(session, nextInfo);
}

function handleWindowStepButtonClick(event) {
  const button = event.currentTarget;
  const key = button.dataset.windowDir === 'prev' ? 'p' : 'n';
  const label = button.dataset.windowDir === 'prev' ? 'previous tmux window' : 'next tmux window';
  tmuxWindow(button.dataset.windowSession, key, label);
}

function createWindowStepButton(session, dir) {
  const label = windowStepButtonLabel(dir);
  const button = document.createElement('button');
  button.className = 'tab tmux-window-step';
  button.dataset.windowStepButton = dir;
  button.textContent = dir === 'prev' ? '<' : '>';
  if (readOnlyMode) {
    button.type = 'button';
    button.disabled = true;
    button.title = `${label} tmux window requires admin access`;
    return button;
  }
  button.dataset.windowDir = dir;
  button.dataset.windowSession = session;
  button.title = `${label} tmux window`;
  button.addEventListener('click', handleWindowStepButtonClick);
  return button;
}

function syncWindowStepButton(controls, terminalButton, session, dir, visible) {
  const selector = `[data-window-step-button="${dir}"]`;
  const existing = controls.querySelector(selector);
  if (!visible) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const button = createWindowStepButton(session, dir);
  if (dir === 'prev') controls.insertBefore(button, terminalButton);
  else controls.insertBefore(button, terminalButton.nextSibling || null);
}

function updatePanelWindowStepButtons(session, info) {
  const controls = document.getElementById(`panel-${session}`)?.querySelector('.tabs');
  const terminalButton = controls?.querySelector('.terminal-tab');
  if (!controls || !terminalButton) return;
  const steps = windowStepVisibility(info?.panes);
  syncWindowStepButton(controls, terminalButton, session, 'prev', steps.prev);
  syncWindowStepButton(controls, terminalButton, session, 'next', steps.next);
}

function agentForPane(info, pane) {
  if (!pane || !Array.isArray(info?.agents)) return null;
  return info.agents.find(agent => agent.pane_target === pane.target) || null;
}

function processLabelFromCommand(command) {
  const tokens = String(command || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return '';
  const base = pathBasename(tokens[0]) || tokens[0];
  const lower = base.toLowerCase();
  if (lower.startsWith('python') && tokens[1] && !tokens[1].startsWith('-')) return pathBasename(tokens[1]) || tokens[1];
  if (lower === 'node' && tokens[1] && !tokens[1].startsWith('-')) return pathBasename(tokens[1]) || tokens[1];
  return base;
}

function updatePanelControlLabels(session, info) {
  const button = document.querySelector(`[data-tab="${cssEscape(session)}"][data-tab-name="terminal"]`);
  updatePanelWindowStepButtons(session, info);
  if (button) {
    button.textContent = terminalTabLabel(session, info);
    button.title = terminalTabTitle(session, info);
  }
}

// DOIT.6 #40: ONE merged panel hosting both YO!info (repo metadata) and YO!agent (chat + activity
// context), switched by a segmented sub-tab row under the pane tabs. Both sub-views render into their
// own containers (#info-content / #yoagent-content) and the active one is shown via CSS; the chosen
// sub-tab is remembered across reloads (infoPanelSubTab).
function createInfoPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel';
  panel.id = `panel-${infoItemId}`;
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
    refreshTranscripts();
  });
  panel.querySelector('[data-yoagent-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
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
    }
  });
  panel.addEventListener('input', event => {
    const input = event.target.closest('[data-yoagent-chat-input]');
    if (input && panel.contains(input)) yoagentDraft = input.value || '';
  });
  panel.addEventListener('change', event => {
    // The composer's backend pill writes the real yoagent.backend setting and re-renders (which also
    // flips chat enablement when switching to/from No agent).
    const backend = event.target.closest('[data-yoagent-backend]');
    if (!backend || !panel.contains(backend) || readOnlyMode) return;
    saveSettingsPatch(settingPatch('yoagent.backend', backend.value))
      .then(() => { statusEl.textContent = `YO!agent backend: ${yoagentBackendLabel(backend.value)}`; renderYoagentPanel(); })
      .catch(error => { statusErr(`settings save failed: ${esc(error)}`); refreshSettings({force: true}); });
  });
  applyInfoSubTab(panel);
  renderInfoPanel();
  renderYoagentPanel();
  if (infoPanelSubTab === 'yoagent') prewarmYoagent();
  return panel;
}

// The merged YO!info pane keeps its outer chrome and sub-tab row mounted between language changes so the
// YO!agent chat draft and active sub-tab survive. Re-label those persistent controls in place.
function relocalizeInfoPanelChrome(panel = document.getElementById(`panel-${infoItemId}`)) {
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
  applyInfoSubTab(panel);
}

// Reflect the active sub-tab onto the merged panel (button highlight + which sub-view is visible).
function applyInfoSubTab(panel = document.getElementById(`panel-${infoItemId}`)) {
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
    renderYoagentPanel({preserveDraft: true, focusInput: options.focusChat === true});
    prewarmYoagent();
  }
}

// Open the merged YO!info pane on a given sub-tab — used by the File menu, command palette, the topbar
// activity button, and the boot deep-link for legacy ?…=yoagent / __yoagent__ references.
async function openInfoSubTab(tab) {
  infoPanelSubTab = normalizedInfoSubTab(tab);
  writeStoredInfoSubTab(infoPanelSubTab);
  await selectSession(infoItemId);
  applyInfoSubTab();
  if (infoPanelSubTab === 'yoagent') {
    renderYoagentPanel({preserveDraft: true, focusInput: true});
    prewarmYoagent();
  }
}

function sessionActivitySummary(session) {
  return activitySummaryPayload?.sessions?.[session] || null;
}

function activitySummaryMarkdownBlockHtml(text, className) {
  return `<div class="${esc(className)} markdown-body" data-yoagent-global-markdown>${esc(text)}</div>`;
}

function activitySummaryLinesHtml(lines, options = {}) {
  const items = Array.isArray(lines) ? lines.filter(Boolean) : [];
  if (!items.length) return options.empty ? `<div class="yoagent-empty">${esc(options.empty)}</div>` : '';
  return items.map(line => activitySummaryMarkdownBlockHtml(String(line), 'yoagent-line')).join('');
}

function yoagentTimestampText(value) {
  const date = value ? new Date(value) : new Date();
  if (!Number.isFinite(date.getTime())) return '';
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: 'America/Los_Angeles',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(date);
  } catch (_) {
    return date.toLocaleString();
  }
}

function yoagentMessageTimestampHtml(value) {
  const text = yoagentTimestampText(value);
  return text ? `<span class="yoagent-message-time">${esc(text)}</span>` : '';
}

function relativeActivityGeneratedText(payload = activitySummaryPayload) {
  const ts = Number(payload?.generated_ts || 0) || Date.parse(payload?.generated_at || '') / 1000;
  if (!Number.isFinite(ts) || ts <= 0) return {text: t('yoagent.notLoaded'), title: ''};
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  // DOIT.8 Phase 3: render the relative time with Intl.RelativeTimeFormat(activeLocale) for native
  // locale phrasing, wrapped by the localized "last updated {rel}" string.
  const text = seconds < 60
    ? t('yoagent.updated.justNow')
    : t('yoagent.updated.wrap', {rel: relativeTimeFormat(seconds)});
  let title = payload?.generated_at || '';
  try {
    title = new Intl.DateTimeFormat(undefined, {
      timeZone: 'America/Los_Angeles',
      dateStyle: 'medium',
      timeStyle: 'medium',
      timeZoneName: 'short',
    }).format(new Date(ts * 1000));
  } catch (_) {}
  return {text, title};
}

function globalActivitySummaryHtml() {
  const summary = activitySummaryPayload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = summary.headline || lines[0] || '';
  const detailLines = lines.filter(line => line && line !== headline && !/^Session\s+\S+:/i.test(String(line)));
  const generated = relativeActivityGeneratedText();
  const refreshBar = activitySummaryRefreshing ? `<div class="yoagent-refresh-progress" aria-label="${esc(t('yoagent.refreshing'))}"></div>` : '';
  return `<section class="yoagent-global" aria-label="${esc(t('yoagent.globalAria', {name: yoagentTabLabel()}))}">
    <div class="yoagent-global-head">
      <span>${esc(yoagentTabLabel())}</span>
      <span class="yoagent-generated" title="${esc(generated.title)}">(${esc(generated.text)})</span>
    </div>
    ${refreshBar}
    ${headline ? activitySummaryMarkdownBlockHtml(headline, 'yoagent-headline') : activitySummaryLinesHtml([], {empty: t('yoagent.emptyGlobal')})}
    ${activitySummaryLinesHtml(detailLines)}
  </section>`;
}

function yoagentChatMessagesHtml() {
  const messages = Array.isArray(yoagentMessages) ? yoagentMessages : [];
  const intro = yoagentIntroMessageHtml();
  if (!messages.length) {
    if (intro) return intro;
    if (!yoagentChatEnabled()) {
      return `<div class="yoagent-chat-empty">${esc(t('yoagent.chatDisabled'))}</div>`;
    }
    return `<div class="yoagent-chat-empty">${esc(t('yoagent.chatEmpty', {name: yoagentTabLabel()}))}</div>`;
  }
  const messageHtml = messages.map(message => {
    const role = message.role === 'user' ? t('yoagent.you') : yoagentTabLabel();
    const roleClass = message.role === 'user' ? 'user' : 'assistant';
    // Assistant replies are Markdown (numbered sections, bold titles, sub-bullets); flag the body so
    // renderYoagentMessageMarkdown() can render it. The escaped text stays as the no-marked fallback.
    const bodyClass = roleClass === 'assistant' ? 'yoagent-message-body markdown-body' : 'yoagent-message-body';
    const markdownAttr = roleClass === 'assistant' ? ' data-yoagent-markdown' : '';
    return `<div class="yoagent-message ${roleClass}">
      <div class="yoagent-message-role"><span>${esc(role)}</span>${yoagentMessageTimestampHtml(message.createdAt)}</div>
      <div class="${bodyClass}"${markdownAttr}>${esc(message.content || '')}</div>
    </div>`;
  }).join('');
  return `${intro}${messageHtml}`;
}

function yoagentIntroMessageText() {
  const summary = activitySummaryPayload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = String(summary.headline || lines[0] || '').trim();
  if (!headline) return '';
  const details = lines
    .filter(line => line && line !== headline && !/^Session\s+\S+:/i.test(String(line)))
    .slice(0, 2)
    .map(line => `- ${String(line).trim()}`);
  return ["Here's what I see right now:", "", headline, ...details].filter(Boolean).join('\n');
}

function yoagentIntroMessageHtml() {
  const text = yoagentIntroMessageText();
  if (!text || !yoagentChatEnabled()) return '';
  return `<div class="yoagent-message assistant yoagent-intro-message">
    <div class="yoagent-message-role"><span>${esc(yoagentTabLabel())}</span>${yoagentMessageTimestampHtml(activitySummaryPayload?.generated_at)}</div>
    <div class="yoagent-message-body markdown-body" data-yoagent-markdown>${esc(text)}</div>
  </div>`;
}

function yoagentNoticeHtml() {
  if (!yoagentNotice?.reason) return '';
  const backend = yoagentNotice.backend ? `<span class="yoagent-chat-notice-backend">${esc(yoagentNotice.backend)}</span> ` : '';
  return `<div class="yoagent-chat-notice">${backend}${esc(yoagentNotice.reason)}</div>`;
}

function yoagentAutoRefreshStatusHtml() {
  const summary = activitySummaryPayload?.yoagent_summaries || {};
  if (!summary.auto_refresh) return '';
  const generated = summary.updated_ts
    ? relativeActivityGeneratedText({generated_ts: summary.updated_ts, generated_at: summary.updated_at})
    : {text: t('yoagent.notLoaded'), title: ''};
  return `<div class="yoagent-chat-notice yoagent-auto-refresh-status" title="${esc(generated.title)}">${esc(t('yoagent.autoRefreshStatus', {updated: generated.text}))}</div>`;
}

function yoagentBackendLabel(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'auto') return t('yoagent.backend.auto');
  if (key === 'deterministic') return t('yoagent.backend.none');
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return value || t('yoagent.backend.none');
}

function yoagentBackendKey() {
  return String(initialSetting('yoagent.backend', 'auto') || 'auto').trim().toLowerCase();
}

// #41: mirror the server's auto-resolution (codex -> claude -> deterministic) using the cached agent
// login status, so the chat input enables/disables to match what the backend will actually run.
function yoagentResolvedBackend() {
  const key = yoagentBackendKey();
  if (key !== 'auto') return key;
  for (const agent of ['codex', 'claude']) {
    if (availableAgents.has(agent) && agentLoggedIn(agent)) return agent;
  }
  return 'deterministic';
}

function yoagentChatEnabled() {
  return ['claude', 'codex'].includes(yoagentResolvedBackend());
}

// The composer's "Auto" pill = the real yoagent.backend setting (Auto / Claude / Codex / No agent),
// rendered as a styled select so it can be changed inline (the mockup's mode pill). No other mockup
// pills are rendered — they have no YO!agent mapping.
function yoagentBackendPillHtml(disabled) {
  const current = yoagentBackendKey();
  // Only Auto / Claude / Codex are selectable. "No agent" (deterministic) stays as an internal
  // auto-fallback when no agent is logged in, but is never offered as a pick.
  const options = ['auto', 'claude', 'codex']
    .map(value => `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(yoagentBackendLabel(value))}</option>`)
    .join('');
  return `<label class="yoagent-backend-pill" title="${esc(t('pref.yoagent.backend.label'))}">
    <span class="yoagent-backend-pill-dot" aria-hidden="true"></span>
    <select data-yoagent-backend aria-label="${esc(t('pref.yoagent.backend.label'))}"${disabled}>${options}</select>
  </label>`;
}

function yoagentChatHtml() {
  const disabled = yoagentBusy || readOnlyMode ? ' disabled' : '';
  const placeholder = readOnlyMode ? t('yoagent.chatAdminPlaceholder', {name: yoagentTabLabel()}) : t('yoagent.chatPlaceholder');
  const isThinking = yoagentBusy || yoagentPrewarming;
  const hasConversation = Boolean(yoagentMessages.length || yoagentNotice || isThinking || yoagentError || yoagentIntroMessageText());
  // Strip any trailing dots/ellipsis from the localized label so the three animated dots carry the motion.
  const thinkingLabel = esc(t('yoagent.thinking').replace(/[.…\s]+$/, ''));
  const thinkingDots = '<span class="yoagent-thinking-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>';
  const busy = isThinking
    ? `<div class="yoagent-chat-status"><span class="session-yolo-marker active working yoagent-chat-spinner" style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}" aria-hidden="true">${esc(t('brand.marker'))}</span><span class="yoagent-thinking">${thinkingLabel}${thinkingDots}</span></div>`
    : '';
  const retry = yoagentError && yoagentDraft && yoagentChatEnabled() && !yoagentBusy && !readOnlyMode
    ? `<button type="button" class="yoagent-chat-retry" data-yoagent-retry>${esc(t('yoagent.retry'))}</button>`
    : '';
  const error = yoagentError ? `<div class="yoagent-chat-error"><span>${esc(yoagentError)}</span>${retry}</div>` : '';
  const clearDisabled = yoagentBusy || (!yoagentMessages.length && !yoagentNotice && !yoagentError) ? ' disabled' : '';
  const form = yoagentChatEnabled()
    ? `<form class="yoagent-chat-form" data-yoagent-chat-form>
      <input type="text" class="yoagent-chat-input" data-yoagent-chat-input value="${esc(yoagentDraft)}" placeholder="${esc(placeholder)}"${disabled}>
      <div class="yoagent-chat-controls">
        ${yoagentBackendPillHtml(disabled)}
        <span class="yoagent-chat-controls-spacer"></span>
        <button type="button" class="yoagent-chat-clear" data-yoagent-clear${clearDisabled}>${esc(t('yoagent.clear'))}</button>
        <button type="submit" class="yoagent-chat-send"${disabled} title="${esc(t('yoagent.ask'))}" aria-label="${esc(t('yoagent.ask'))}">
          <svg class="yoagent-chat-send-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M12 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </form>`
    : '';
  return `<section class="yoagent-chat ${hasConversation ? 'has-history' : 'empty'}" aria-label="${esc(t('yoagent.chatAria', {name: yoagentTabLabel()}))}">
    <div class="yoagent-chat-history">${yoagentAutoRefreshStatusHtml()}${yoagentNoticeHtml()}${yoagentChatMessagesHtml()}${busy}${error}</div>
    ${form}
  </section>`;
}

function yoagentChatNetworkError(error) {
  const text = String(error?.message || error || '');
  return error instanceof TypeError || /failed to fetch|networkerror|load failed|fetch failed/i.test(text);
}

function yoagentChatErrorMessage(error) {
  if (yoagentChatNetworkError(error)) {
    return t('yoagent.networkError');
  }
  return t('yoagent.chatFailed', {error: error?.message || error});
}

let yoagentFocusSerial = 0;
let yoagentFocusTrackerInstalled = false;

function yoagentDocumentHasFocus() {
  if (typeof document !== 'undefined' && document.visibilityState && document.visibilityState !== 'visible') return false;
  if (typeof document !== 'undefined' && typeof document.hasFocus === 'function') return document.hasFocus();
  return true;
}

function yoagentEventIsInsideComposer(event) {
  return Boolean(event?.target?.closest?.('[data-yoagent-chat-form]'));
}

function installYoagentFocusTracker() {
  if (yoagentFocusTrackerInstalled || typeof document === 'undefined') return;
  yoagentFocusTrackerInstalled = true;
  document.addEventListener('pointerdown', event => {
    if (!yoagentEventIsInsideComposer(event)) yoagentFocusSerial += 1;
  }, true);
  document.addEventListener('focusin', event => {
    if (!yoagentEventIsInsideComposer(event)) yoagentFocusSerial += 1;
  }, true);
  if (typeof window !== 'undefined') {
    window.addEventListener('blur', () => { yoagentFocusSerial += 1; });
  }
}

async function clearYoagentConversation() {
  yoagentMessages = [];
  yoagentBusy = false;
  yoagentError = '';
  yoagentDraft = '';
  yoagentNotice = null;
  renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  try {
    await apiFetchJson('/api/yoagent/reset', {method: 'POST'});
    statusEl.textContent = t('yoagent.statusCleared');
  } catch (error) {
    statusErr(`${esc(t('yoagent.statusClearFailed', {error}))}`);
  }
}

async function sendYoagentChatMessage(rawText) {
  const text = String(rawText || '').trim();
  if (!text || yoagentBusy || readOnlyMode || !yoagentChatEnabled()) return;
  installYoagentFocusTracker();
  const focusSerial = yoagentFocusSerial;
  const shouldRestoreFocus = yoagentChatInputIsFocused() && yoagentDocumentHasFocus();
  yoagentMessages.push({role: 'user', content: text, createdAt: new Date().toISOString()});
  yoagentDraft = '';
  yoagentBusy = true;
  yoagentPrewarming = false;
  yoagentError = '';
  yoagentNotice = null;
  renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  try {
    const payload = await apiFetchJson('/api/yoagent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      // DOIT.8 Phase 1: pass the active UI locale so the LLM backend replies in that language.
      body: JSON.stringify({message: text, history: yoagentMessages.slice(-10), locale: i18nActiveLocaleId()}),
    });
    if (payload.fallback && payload.fallback_reason) {
      yoagentNotice = {backend: yoagentBackendLabel(payload.backend_used || payload.backend), reason: payload.fallback_reason};
    }
    yoagentMessages.push({role: 'assistant', content: payload.answer || t('yoagent.noAnswer'), createdAt: payload.answered_at || new Date().toISOString()});
    statusEl.textContent = t('yoagent.statusAnswered', {backend: yoagentBackendLabel(payload.backend_used || payload.backend)});
  } catch (error) {
    if (yoagentChatNetworkError(error)) yoagentDraft = text;
    yoagentError = yoagentChatErrorMessage(error);
  } finally {
    yoagentBusy = false;
    renderYoagentPanel({
      preserveDraft: true,
      scrollBottom: true,
      focusInput: shouldRestoreFocus && focusSerial === yoagentFocusSerial && yoagentDocumentHasFocus(),
    });
  }
}

async function prewarmYoagent(options = {}) {
  if (yoagentPrewarmStarted || readOnlyMode || !yoagentChatEnabled()) return;
  yoagentPrewarmStarted = true;
  yoagentPrewarming = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    await apiFetchJson('/api/yoagent/prewarm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({locale: i18nActiveLocaleId()}),
    });
  } catch (_) {
    // Prewarm is opportunistic; visible chat requests handle real errors.
  } finally {
    yoagentPrewarming = false;
    renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom === true});
  }
}

async function refreshActivitySummary(options = {}) {
  if (activitySummaryRefreshing && options.force !== true) return;
  const requestIsCurrent = activitySummaryGuard.begin();
  activitySummaryRefreshing = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
  try {
    const params = new URLSearchParams();
    if (options.force) params.set('force', '1');
    params.set('locale', i18nActiveLocaleId());
    const payload = await apiFetchJson(`/api/activity-summary?${params.toString()}`, {cache: 'no-store'});
    if (!requestIsCurrent()) return;
    activitySummaryPayload = payload;
  } catch (error) {
    if (!requestIsCurrent()) return;
    activitySummaryPayload = {
      ...activitySummaryPayload,
      errors: [String(error)],
      global: {lines: [`activity summary unavailable: ${String(error)}`]},
    };
    if (!options.silent) statusErr(`activity summary failed: ${esc(error)}`);
  } finally {
    if (requestIsCurrent()) {
      activitySummaryRefreshing = false;
      renderInfoPanel();
      renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
      if (infoPanelSubTab === 'yoagent') prewarmYoagent();
    }
  }
}

function editorSchemePreferenceChoices(options = {}) {
  const preferredOrder = [
    'dark',
    'vscode-dark-plus',
    'one-dark',
    'dracula',
    'monokai',
    'nord',
    'vscode-light-plus',
    'yolomux-light',
    'github-light',
    'one-light',
    'solarized-light',
  ];
  const ids = [...preferredOrder, ...EDITOR_SCHEME_IDS.filter(id => !preferredOrder.includes(id))];
  return ids
    .filter(id => options.dark === undefined || EDITOR_SCHEMES[id]?.dark === options.dark)
    .map(id => {
      const scheme = EDITOR_SCHEMES[id];
      return {value: id, label: scheme.label, group: scheme.dark ? 'Dark' : 'Light'};
    });
}

function globalThemePreferenceChoices() {
  return [
    {value: 'system', label: t('pref.appearance.theme.system')},
    {value: 'dark', label: t('pref.appearance.theme.dark')},
    {value: 'light', label: t('pref.appearance.theme.light')},
  ];
}

function activeColorPreferenceChoice(value, label) {
  const preset = ACTIVE_COLOR_PRESETS[value];
  const swatches = preset
    ? [preset.dark.bright, preset.light.bright]
    : ['#86d600', '#4f9e3a'];
  return {value, label, swatches, joinedSwatches: true};
}

function activeColorPreferenceChoices() {
  return UI_COLOR_CHOICES.map(value => activeColorPreferenceChoice(value, t(UI_COLOR_PRESETS[value].labelKey)));
}

function cursorColorPreferenceChoice(value) {
  const preset = UI_COLOR_PRESETS[value];
  const color = value === 'theme' ? activeEditorScheme().cursor : preset?.cursor;
  const label = value === 'theme' ? t('pref.appearance.editor_cursor_color.theme') : t(preset.labelKey);
  return color ? {value, label, swatches: [color]} : {value, label};
}

function cursorColorPreferenceChoices() {
  return [DEFAULT_CURSOR_COLOR, ...UI_COLOR_CHOICES.filter(value => value !== DEFAULT_CURSOR_COLOR), 'theme']
    .map(cursorColorPreferenceChoice);
}

function preferenceSections() {
  return [
    {title: t('pref.section.general'), items: [
      // #51: Language is the FIRST General preference.
      {path: 'general.language', label: t('pref.general.language.label'), type: 'select', choices: [
        // Endonym labels (each language in its own script); Traditional Chinese before Simplified.
        {value: 'system', label: t('pref.general.language.system')},
        {value: 'en', label: 'English'},
        {value: 'zh-Hant', label: '繁體中文'},
        {value: 'zh-Hans', label: '简体中文'},
        {value: 'es', label: 'Español'},
        {value: 'ja', label: '日本語'},
        {value: 'de', label: 'Deutsch'},
        {value: 'fr', label: 'Français'},
        {value: 'pt-BR', label: 'Português (BR)'},
        {value: 'ru', label: 'Русский'},
        {value: 'ko', label: '한국어'},
        {value: 'hi', label: 'हिन्दी'},
        {value: 'ar', label: 'العربية'},
        {value: 'en-XA', label: t('pref.general.language.pseudo')},
      ], help: t('pref.general.language.help')},
      {path: 'general.auto_focus', label: t('pref.general.auto_focus.label'), type: 'boolean', help: t('pref.general.auto_focus.help')},
      {path: 'general.startup_tips', label: t('pref.general.startup_tips.label'), type: 'boolean', help: t('pref.general.startup_tips.help')},
      {path: 'general.default_sessions', label: t('pref.general.default_sessions.label'), type: 'list', help: t('pref.general.default_sessions.help')},
    ]},
    {title: t('pref.section.appearance'), items: [
      {path: 'appearance.theme', label: t('pref.appearance.theme.label'), type: 'radio', choices: globalThemePreferenceChoices(), help: t('pref.appearance.theme.help')},
      {path: 'general.default_layout', label: t('pref.general.default_layout.label'), type: 'radio', choices: ['single', 'grid', 'wall'], help: t('pref.general.default_layout.help')},
      {path: 'appearance.ui_font_size', label: t('pref.appearance.ui_font_size.label'), type: 'number', min: 6, max: 20, step: 1, suffix: 'px', help: t('pref.appearance.ui_font_size.help')},
      {path: 'appearance.file_explorer_font_size', label: t('pref.appearance.file_explorer_font_size.label', {name: fileExplorerLabel()}), type: 'number', min: 6, max: 24, step: 1, suffix: 'px', help: t('pref.appearance.file_explorer_font_size.help')},
      {type: 'note', text: t('pref.appearance.font_sizes.note')},
      {path: 'appearance.tab_width', label: t('pref.appearance.tab_width.label'), type: 'number', min: 120, max: 420, step: 5, suffix: 'px', help: t('pref.appearance.tab_width.help')},
      {path: 'appearance.max_tabs_per_pane', label: t('pref.appearance.max_tabs_per_pane.label'), type: 'number', min: 2, max: 30, step: 1, help: t('pref.appearance.max_tabs_per_pane.help')},
      {path: 'appearance.pane_spacing', label: t('pref.appearance.pane_spacing.label'), type: 'number', min: 0, max: 20, step: 1, suffix: 'px', help: t('pref.appearance.pane_spacing.help')},
      {path: 'appearance.pane_ring_opacity', label: t('pref.appearance.pane_ring_opacity.label'), type: 'range', min: 5, max: 100, step: 5, suffix: '%', help: t('pref.appearance.pane_ring_opacity.help')},
      {path: 'appearance.inactive_pane_opacity', label: t('pref.appearance.inactive_pane_opacity.label'), type: 'range', min: 0, max: 100, step: 5, suffix: '%', help: t('pref.appearance.inactive_pane_opacity.help')},
      {path: 'appearance.active_color', label: t('pref.appearance.active_color.label'), type: 'radio', choices: activeColorPreferenceChoices(), help: t('pref.appearance.active_color.help')},
      {path: 'appearance.editor_cursor_color', label: t('pref.appearance.editor_cursor_color.label'), type: 'radio', choices: cursorColorPreferenceChoices(), help: t('pref.appearance.editor_cursor_color.help')},
      {path: 'appearance.yolo_rotate_ms', label: t('pref.appearance.yolo_rotate_ms.label'), type: 'number', min: 0, max: 60000, step: 250, suffix: 'ms', help: t('pref.appearance.yolo_rotate_ms.help')},
      {path: 'appearance.date_time_hour_cycle', label: t('pref.appearance.date_time_hour_cycle.label'), type: 'radio', choices: [
        {value: '24', label: t('pref.appearance.date_time_hour_cycle.24')},
        {value: '12', label: t('pref.appearance.date_time_hour_cycle.12')},
      ], help: t('pref.appearance.date_time_hour_cycle.help')},
    ]},
    {title: t('pref.section.terminal_editor'), items: [
      {path: 'appearance.terminal_theme', label: t('pref.appearance.terminal_theme.label'), type: 'radio', choices: [
        {value: 'follow-app', label: t('pref.appearance.terminal_theme.follow-app')},
        {value: 'dark', label: t('pref.appearance.terminal_theme.dark')},
        {value: 'light', label: t('pref.appearance.terminal_theme.light')},
      ], help: t('pref.appearance.terminal_theme.help')},
      {path: 'appearance.terminal_font_size', label: t('pref.appearance.terminal_font_size.label'), type: 'number', min: 6, max: 28, step: 1, suffix: 'px', help: t('pref.appearance.terminal_font_size.help')},
      {path: 'appearance.editor_font_size', label: t('pref.appearance.editor_font_size.label'), type: 'number', min: 6, max: 28, step: 1, suffix: 'px', help: t('pref.appearance.editor_font_size.help')},
      {path: 'appearance.preview_font_size', label: t('pref.appearance.preview_font_size.label'), type: 'number', min: 6, max: 32, step: 1, suffix: 'px', help: t('pref.appearance.preview_font_size.help')},
      {path: 'terminal_editor.scrollback', label: t('pref.terminal_editor.scrollback.label'), type: 'number', min: 1000, max: 50000, step: 500, suffix: 'lines', help: t('pref.terminal_editor.scrollback.help')},
      {path: 'appearance.editor_dark_color_scheme', label: t('pref.appearance.editor_dark_color_scheme.label'), type: 'select', choices: editorSchemePreferenceChoices({dark: true}), help: t('pref.appearance.editor_dark_color_scheme.help')},
      {path: 'appearance.editor_light_color_scheme', label: t('pref.appearance.editor_light_color_scheme.label'), type: 'select', choices: editorSchemePreferenceChoices({dark: false}), help: t('pref.appearance.editor_light_color_scheme.help')},
      {path: 'appearance.editor_cursor_style', label: t('pref.appearance.editor_cursor_style.label'), type: 'radio', choices: [
        {value: 'line', label: t('pref.appearance.editor_cursor_style.line')},
        {value: 'block', label: t('pref.appearance.editor_cursor_style.block')},
      ], help: t('pref.appearance.editor_cursor_style.help')},
      {path: 'terminal_editor.word_wrap', label: t('pref.terminal_editor.word_wrap.label'), type: 'boolean', help: t('pref.terminal_editor.word_wrap.help')},
      {path: 'terminal_editor.line_numbers', label: t('pref.terminal_editor.line_numbers.label'), type: 'boolean', help: t('pref.terminal_editor.line_numbers.help')},
      {path: 'editor.autosave', label: t('pref.editor.autosave.label'), type: 'boolean', help: t('pref.editor.autosave.help')},
      {path: 'editor.autosave_delay_seconds', label: t('pref.editor.autosave_delay_seconds.label'), type: 'number', min: 0.5, max: 60, step: 0.5, suffix: 's', help: t('pref.editor.autosave_delay_seconds.help')},
      {path: 'editor.blame_all_lines', label: t('pref.editor.blame_all_lines.label'), type: 'boolean', help: t('pref.editor.blame_all_lines.help')},
    ]},
    {title: t('pref.section.notifications'), items: [
      {path: 'general.reload_on_update', label: t('pref.general.reload_on_update.label'), type: 'boolean', help: t('pref.general.reload_on_update.help')},
      {path: 'notifications.notify_transitions', label: t('pref.notifications.notify_transitions.label'), type: 'list', help: t('pref.notifications.notify_transitions.help')},
      {path: 'notifications.toast_duration_ms', label: t('pref.notifications.toast_duration_ms.label'), type: 'number', min: 1000, max: 60000, step: 500, suffix: 'ms', help: t('pref.notifications.toast_duration_ms.help')},
      {path: 'notifications.throttle_seconds', label: t('pref.notifications.throttle_seconds.label'), type: 'number', min: 0, max: 600, step: 5, suffix: 's', help: t('pref.notifications.throttle_seconds.help')},
      {path: 'appearance.red_reminder_ms', label: t('pref.appearance.red_reminder_ms.label'), type: 'number', min: 0, max: 10000, step: 50, suffix: 'ms', help: t('pref.appearance.red_reminder_ms.help')},
      {path: 'appearance.metadata_badge_pulse_seconds', label: t('pref.appearance.metadata_badge_pulse_seconds.label'), type: 'number', min: 0, max: 120, step: 1, suffix: 's', help: t('pref.appearance.metadata_badge_pulse_seconds.help')},
    ]},
    {title: fileExplorerLabel(), items: [
      {path: 'file_explorer.root_mode', label: t('pref.file_explorer.root_mode.label'), type: 'radio', choices: ['fixed', 'sync'], help: t('pref.file_explorer.root_mode.help')},
      {path: 'file_explorer.image_open_mode', label: t('pref.file_explorer.image_open_mode.label'), type: 'radio', choices: ['same-tab', 'new-tab'], help: t('pref.file_explorer.image_open_mode.help')},
      {path: 'file_explorer.image_preview_max_px', label: t('pref.file_explorer.image_preview_max_px.label'), type: 'number', min: 120, max: 1200, step: 20, suffix: 'px', help: t('pref.file_explorer.image_preview_max_px.help')},
      {path: 'file_explorer.quick_access_paths', label: t('pref.file_explorer.quick_access_paths.label'), type: 'list', help: t('pref.file_explorer.quick_access_paths.help')},
      {path: 'file_explorer.indexed_dirs', label: t('pref.file_explorer.indexed_dirs.label'), type: 'list', help: t('pref.file_explorer.indexed_dirs.help')},
      {path: 'file_explorer.index_refresh_seconds', label: t('pref.file_explorer.index_refresh_seconds.label'), type: 'number', min: 0, max: 3600, step: 10, suffix: 's', help: t('pref.file_explorer.index_refresh_seconds.help')},
      {path: 'file_explorer.companion_dirs', label: t('pref.file_explorer.companion_dirs.label'), type: 'list', help: t('pref.file_explorer.companion_dirs.help')},
      {path: 'file_explorer.refresh_seconds', label: t('pref.file_explorer.refresh_seconds.label', {name: fileExplorerLabel()}), type: 'number', min: 1, max: 60, step: 1, suffix: 's', help: t('pref.file_explorer.refresh_seconds.help')},
      {path: 'file_explorer.dir_cache_ms', label: t('pref.file_explorer.dir_cache_ms.label'), type: 'number', min: 0, max: 10000, step: 100, suffix: 'ms', help: t('pref.file_explorer.dir_cache_ms.help')},
      {path: 'file_explorer.new_entry_highlight_ms', label: t('pref.file_explorer.new_entry_highlight_ms.label'), type: 'number', min: 0, max: 600000, step: 1000, suffix: 'ms', help: t('pref.file_explorer.new_entry_highlight_ms.help')},
    ]},
    {title: t('pref.section.uploads'), items: [
      {path: 'uploads.filename_template', label: t('pref.uploads.filename_template.label'), type: 'text', wide: true, help: t('pref.uploads.filename_template.help')},
      {path: 'uploads.max_bytes', label: t('pref.uploads.max_bytes.label'), type: 'number', min: 1, max: 512, step: 1, suffix: 'MB', scale: 1048576, help: t('pref.uploads.max_bytes.help')},
    ]},
    {title: t('pref.section.performance'), items: [
      {path: 'general.reload_on_update_auto', label: t('pref.general.reload_on_update_auto.label'), type: 'boolean', help: t('pref.general.reload_on_update_auto.help')},
      {path: 'performance.metadata_refresh_ms', label: t('pref.performance.metadata_refresh_ms.label'), type: 'number', min: 3000, max: 120000, step: 100, suffix: 'ms', help: t('pref.performance.metadata_refresh_ms.help')},
      {path: 'performance.watched_pr_refresh_ms', label: t('pref.performance.watched_pr_refresh_ms.label'), type: 'number', min: 15000, max: 600000, step: 1000, suffix: 'ms', help: t('pref.performance.watched_pr_refresh_ms.help')},
      {path: 'performance.pane_state_refresh_ms', label: t('pref.performance.pane_state_refresh_ms.label'), type: 'number', min: 500, max: 30000, step: 100, suffix: 'ms', help: t('pref.performance.pane_state_refresh_ms.help')},
      {path: 'performance.latency_refresh_ms', label: t('pref.performance.latency_refresh_ms.label'), type: 'number', min: 1000, max: 30000, step: 100, suffix: 'ms', help: t('pref.performance.latency_refresh_ms.help')},
      {path: 'performance.event_log_refresh_ms', label: t('pref.performance.event_log_refresh_ms.label'), type: 'number', min: 1000, max: 60000, step: 100, suffix: 'ms', help: t('pref.performance.event_log_refresh_ms.help')},
      {path: 'performance.popover_show_delay_ms', label: t('pref.performance.popover_show_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.popover_show_delay_ms.help')},
      {path: 'performance.popover_hide_delay_ms', label: t('pref.performance.popover_hide_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.popover_hide_delay_ms.help')},
      {path: 'performance.menu_hover_open_delay_ms', label: t('pref.performance.menu_hover_open_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.menu_hover_open_delay_ms.help')},
      {path: 'performance.tab_popover_show_delay_ms', label: t('pref.performance.tab_popover_show_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.tab_popover_show_delay_ms.help')},
      {path: 'performance.tab_popover_follow_delay_ms', label: t('pref.performance.tab_popover_follow_delay_ms.label'), type: 'number', min: 0, max: 1000, step: 20, suffix: 'ms', help: t('pref.performance.tab_popover_follow_delay_ms.help')},
      {path: 'performance.remote_resize_delay_ms', label: t('pref.performance.remote_resize_delay_ms.label'), type: 'number', min: 50, max: 2000, step: 10, suffix: 'ms', help: t('pref.performance.remote_resize_delay_ms.help')},
    ]},
    {title: t('pref.section.github'), items: [
      {path: 'github.watched_prs', label: t('pref.github.watched_prs.label'), type: 'list', wide: true, help: t('pref.github.watched_prs.help')},
    ]},
    {id: 'yolo', title: t('pref.section.yolo'), items: [
      {path: 'performance.auto_approve_interval_seconds', label: t('pref.performance.auto_approve_interval_seconds.label'), type: 'number', min: 0.1, max: 10, step: 0.1, suffix: 's', help: t('pref.performance.auto_approve_interval_seconds.help')},
      {path: 'yolo.rule_file_path', label: t('pref.yolo.rule_file_path.label'), type: 'text', action: 'open-yolo-rule', wide: true, help: t('pref.yolo.rule_file_path.help')},
      {path: 'yolo.dry_run', label: t('pref.yolo.dry_run.label'), type: 'boolean', help: t('pref.yolo.dry_run.help')},
      {path: 'yolo.prompt_source', label: t('pref.yolo.prompt_source.label'), type: 'radio', choices: [
        {value: 'hybrid', label: t('pref.yolo.prompt_source.hybrid')},
        {value: 'pane', label: t('pref.yolo.prompt_source.pane')},
      ], help: t('pref.yolo.prompt_source.help')},
    ]},
    {title: t('pref.section.yoagent'), items: [
      {path: 'yoagent.backend', label: t('pref.yoagent.backend.label'), type: 'radio', choices: [
        {value: 'auto', label: t('pref.yoagent.backend.auto')},
        {value: 'codex', label: t('pref.yoagent.backend.codex')},
        {value: 'claude', label: t('pref.yoagent.backend.claude')},
      ], help: t('pref.yoagent.backend.help')},
      {path: 'yoagent.invocation', label: t('pref.yoagent.invocation.label'), type: 'radio', choices: [
        {value: 'cli', label: t('pref.yoagent.invocation.cli')},
        {value: 'api-key', label: t('pref.yoagent.invocation.api-key')},
      ], help: t('pref.yoagent.invocation.help')},
      {path: 'yoagent.auto_refresh', label: t('pref.yoagent.auto_refresh.label'), type: 'boolean', help: t('pref.yoagent.auto_refresh.help')},
      {path: 'yoagent.refresh_interval_seconds', label: t('pref.yoagent.refresh_interval_seconds.label'), type: 'number', min: 30, max: 3600, step: 30, suffix: 's', help: t('pref.yoagent.refresh_interval_seconds.help')},
      {path: 'yoagent.system_prompt', label: t('pref.yoagent.system_prompt.label'), type: 'textarea', help: t('pref.yoagent.system_prompt.help'), alwaysEnableReset: true},
      {path: 'yoagent.intro', label: t('pref.yoagent.intro.label'), type: 'textarea', help: t('pref.yoagent.intro.help'), alwaysEnableReset: true},
      {path: 'yoagent.format', label: t('pref.yoagent.format.label'), type: 'textarea', help: t('pref.yoagent.format.help'), alwaysEnableReset: true},
    ]},
  ];
}

function preferenceItemByPath(path) {
  for (const section of preferenceSections()) {
    const item = section.items.find(candidate => candidate.path === path);
    if (item) return item;
  }
  return null;
}

function preferenceValue(path) {
  return nestedSetting(clientSettings, path, nestedSetting(clientSettingsDefaults, path, ''));
}

function preferenceDefault(path) {
  return nestedSetting(clientSettingsDefaults, path, '');
}

function preferenceStatusText() {
  if (clientSettingsPayload.error) return `settings error: ${clientSettingsPayload.error}`;
  if (yoloRulesPayload.error) return `YOLO rules error: ${yoloRulesPayload.error}`;
  return settingsLoadedAgeText();
}

function settingsLoadedAgeText(nowMs = Date.now()) {
  const loadedMs = Number(clientSettingsPayload.mtime_ns || 0) / 1000000;
  if (!Number.isFinite(loadedMs) || loadedMs <= 0) return 'loaded';
  const ageSeconds = Math.max(0, Math.floor((Number(nowMs) - loadedMs) / 1000));
  if (ageSeconds < 60) return `loaded ${ageSeconds} sec ago`;
  const ageMinutes = Math.floor(ageSeconds / 60);
  if (ageMinutes < 60) return `loaded ${ageMinutes} min ago`;
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `loaded ${ageHours} hr ago`;
  const ageDays = Math.floor(ageHours / 24);
  return `loaded ${ageDays} day${ageDays === 1 ? '' : 's'} ago`;
}

function preferencesPathRowsHtml() {
  const settingsPath = settingsConfigPath();
  return `
    <div class="preferences-path-row">
      <span class="preferences-path-label">${esc(t('pref.path.settings'))}</span><span class="preferences-path-value">${esc(settingsPath)} ${esc(settingsLoadedAgeText())}</span>${pathCopyButtonHtml(settingsPath, {className: 'preferences-path-copy', title: t('pref.path.copySettings')})}
    </div>`;
}

function preferencesYoloRulesPathHtml() {
  const rulesPath = yoloRulePath();
  const rulesDetail = yoloRulesPayload.source ? ` · ${yoloRuleStatusDetail()}` : '';
  return `
    <div class="preferences-path-row preferences-path-row--section">
      <span class="preferences-path-label">${esc(t('pref.path.rules'))}</span><span class="preferences-path-value">${esc(rulesPath)}${esc(rulesDetail)}</span>${pathCopyButtonHtml(rulesPath, {className: 'preferences-path-copy', title: t('pref.path.copyRules')})}
    </div>`;
}

function preferenceSearchNeedle() {
  return preferencesSearchText.trim().toLowerCase();
}

const preferenceSearchAliasGroups = [
  ['large', 'larger', 'big', 'bigger', 'huge', 'small', 'smaller', 'tiny', 'text', 'scale', 'zoom', 'font', 'size'],
  ['wide', 'narrow', 'width'],
  ['duration', 'timeout', 'time', 'timing', 'ms', 'millisecond', 'milliseconds', 'second', 'seconds', 'speed', 'fast', 'slow', 'quick', 'lag', 'wait', 'debounce', 'period', 'rate', 'frequency', 'often', 'delay', 'refresh', 'interval'],
  ['refresh', 'reload', 'update', 'poll', 'polling', 'sync', 'live'],
  ['tooltip', 'popup', 'popover', 'peek', 'flyout', 'hover'],
  ['animation', 'animate', 'blink', 'flash', 'pulse', 'spin', 'glow', 'attention', 'reminder', 'red'],
  ['color', 'colour', 'theme', 'dark', 'light', 'background', 'bg', 'contrast', 'style', 'look'],
  ['shell', 'history', 'buffer', 'backlog', 'lines', 'scrollback', 'terminal'],
  ['code', 'edit', 'editor', 'codemirror', 'monaco'],
  ['wrap', 'wrapping', 'softwrap'],
  ['numbers', 'number', 'gutter'],
  ['notify', 'notification', 'notifications', 'alert', 'alerts', 'toast', 'message', 'banner', 'sound', 'ding', 'ping', 'bell', 'beep', 'desktop', 'dismiss'],
  ['throttle', 'mute', 'quiet', 'spam', 'cooldown'],
  ['finder', 'file', 'files', 'explorer', 'tree', 'sidebar', 'browser', 'directory', 'folder', 'navigator'],
  ['root', 'home', 'base', 'cwd'],
  ['shortcuts', 'bookmarks', 'favorites', 'pinned', 'jump'],
  ['yolo', 'autoapprove', 'approve', 'approval', 'permission', 'permissions', 'accept', 'confirm', 'rules', 'policy', 'safe', 'danger', 'dangerous'],
  ['yoagent', 'yo agent', 'assistant', 'chat', 'summary', 'activity', 'prompt', 'backend', 'claude', 'codex'],
  ['dry', 'simulate', 'test'],
  ['startup', 'launch', 'open', 'start', 'split', 'grid', 'wall', 'layout'],
];
const preferenceSearchAliasMap = new Map();
for (const group of preferenceSearchAliasGroups) {
  for (const term of group) preferenceSearchAliasMap.set(term, group);
}

function preferenceSearchTokens(query) {
  return String(query || '').toLowerCase().match(/[a-z0-9_./-]+/g) || [];
}

function preferenceSearchAliasesForToken(token) {
  return preferenceSearchAliasMap.get(token) || [token];
}

function preferenceSearchKeywordsForItem(item) {
  const path = String(item?.path || '');
  const label = String(item?.label || '').toLowerCase();
  const keywords = [];
  const add = terms => keywords.push(...terms);
  if (path.includes('font_size') || path === 'appearance.tab_width') add(['large', 'larger', 'big', 'bigger', 'huge', 'small', 'smaller', 'tiny', 'text', 'scale', 'zoom', 'wide', 'narrow']);
  if (/(_ms|_seconds|_delay|_refresh|_interval|duration|period|pulse|rotate|throttle|resize)/.test(path)) add(['duration', 'timeout', 'time', 'timing', 'milliseconds', 'seconds', 'speed', 'fast', 'slow', 'quick', 'lag', 'wait', 'debounce', 'period', 'rate', 'frequency', 'often']);
  if (path.includes('_refresh_ms') || path === 'file_explorer.refresh_seconds') add(['reload', 'update', 'poll', 'polling', 'sync', 'live', 'auto']);
  if (path.includes('popover') || path.includes('hover')) add(['tooltip', 'popup', 'peek', 'flyout']);
  if (path.includes('red_reminder') || path.includes('yolo_rotate') || path.includes('badge_pulse')) add(['animation', 'animate', 'blink', 'flash', 'glow', 'attention', 'reminder']);
  if (path.startsWith('appearance.')) add(['color', 'colour', 'theme', 'dark', 'light', 'background', 'bg', 'contrast', 'style', 'look']);
  if (path === 'appearance.date_time_hour_cycle') add(['date', 'time', 'clock', 'hour', 'hours', '12', '24', 'am', 'pm']);
  if (path === 'terminal_editor.scrollback' || path === 'appearance.terminal_font_size' || path === 'appearance.terminal_theme') add(['shell', 'history', 'buffer', 'backlog', 'lines', 'terminal', 'tui', 'ansi', 'xterm', 'codex', 'claude']);
  if (path.startsWith('editor.') || path.includes('editor_') || path.startsWith('terminal_editor.')) add(['code', 'edit', 'codemirror', 'monaco']);
  if (path === 'terminal_editor.word_wrap') add(['softwrap', 'wrapping']);
  if (path === 'terminal_editor.line_numbers') add(['numbers', 'gutter']);
  if (path.startsWith('notifications.')) add(['notify', 'alert', 'toast', 'message', 'banner', 'sound', 'ding', 'ping', 'bell', 'beep', 'desktop', 'dismiss']);
  if (path.includes('throttle')) add(['mute', 'quiet', 'spam', 'cooldown', 'rate limit']);
  if (path.startsWith('file_explorer.')) add(['finder', 'files', 'tree', 'sidebar', 'browser', 'directory', 'folder', 'navigator']);
  if (path.startsWith('uploads.')) add(['upload', 'paste', 'drop', 'filename', 'template', 'file']);
  if (path === 'file_explorer.root_mode') add(['root', 'home', 'base', 'working', 'cwd', 'follow', 'track']);
  if (path === 'file_explorer.quick_access_paths') add(['shortcuts', 'bookmarks', 'favorites', 'pinned', 'jump']);
  if (path === 'file_explorer.indexed_dirs') add(['index', 'indexed', 'quick open', 'quick-open', 'search', 'scan', 'directories', 'folders']);
  if (path === 'file_explorer.index_refresh_seconds') add(['index', 'refresh', 'auto', 'rebuild', 'background', 'quick-open', 'interval', 'stale']);
  if (path === 'file_explorer.companion_dirs') add(['companion', 'repos', 'sibling', 'extra', 'always', 'dirty', 'branch', 'status', 'frontend-crates']);
  if (path === 'file_explorer.image_preview_max_px') add(['image', 'picture', 'photo', 'preview', 'thumbnail', 'hover', 'popup', 'large', 'small', 'size']);
  if (path === 'file_explorer.new_entry_highlight_ms') add(['new file', 'recent']);
  if (path.startsWith('yolo.')) add(['auto approve', 'approve', 'approval', 'permission', 'accept', 'confirm', 'rules', 'policy', 'safe', 'danger']);
  if (path.startsWith('yoagent.')) add(['assistant', 'chat', 'summary', 'activity', 'prompt', 'backend', 'claude', 'codex']);
  if (path === 'yolo.dry_run') add(['test', 'simulate', 'what would']);
  if (path === 'yolo.rule_file_path') add(['yaml', 'config']);
  if (path === 'general.auto_focus') add(['click', 'focus', 'hover', 'menu', 'dropdown', 'select pane', 'terminal', 'editor', 'finder', 'file explorer', 'preferences', 'everything']);
  if (path === 'general.default_layout') add(['startup', 'launch', 'open', 'start', 'split', 'grid', 'wall']);
  if (path === 'general.default_sessions') add(['startup', 'launch', 'which sessions']);
  if (label.includes('quick')) add(['shortcuts', 'bookmarks', 'favorites']);
  return keywords;
}

function preferenceSearchHaystack(item) {
  const choices = Array.isArray(item.choices) ? item.choices.map(choice => [preferenceChoiceValue(choice), preferenceChoiceLabel(choice), preferenceChoiceGroup(choice)]).flat() : [];
  return [item.label, item.path, item.help, item.text, item.suffix, item.keywords, choices, preferenceSearchKeywordsForItem(item)]
    .flat(Infinity)
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function textMatchesPreferenceQuery(value, query) {
  const haystack = String(value || '').toLowerCase();
  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized) return true;
  if (haystack.includes(normalized)) return true;
  return preferenceSearchTokens(normalized).every(token => (
    preferenceSearchAliasesForToken(token).some(alias => haystack.includes(alias))
  ));
}

function preferenceItemMatches(item, query) {
  if (!query) return true;
  return textMatchesPreferenceQuery(preferenceSearchHaystack(item), query);
}

function preferenceSectionMatches(section, query) {
  if (!query) return true;
  return textMatchesPreferenceQuery(section.title, query) || section.items.some(item => preferenceItemMatches(item, query));
}

function preferenceChoiceValue(choice) {
  return typeof choice === 'object' && choice !== null ? choice.value : choice;
}

function preferenceChoiceLabel(choice) {
  if (typeof choice === 'object' && choice !== null) return choice.label || choice.value;
  return String(choice || '')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, match => match.toUpperCase());
}

function preferenceChoiceGroup(choice) {
  return typeof choice === 'object' && choice !== null ? (choice.group || '') : '';
}

function preferenceSelectOptionsHtml(item, value) {
  const choices = Array.isArray(item.choices) ? item.choices : [];
  const groups = [];
  const groupLookup = new Map();
  const optionHtml = choice => {
    const choiceValue = String(preferenceChoiceValue(choice));
    return `<option value="${esc(choiceValue)}"${choiceValue === String(value) ? ' selected' : ''}>${esc(preferenceChoiceLabel(choice))}</option>`;
  };
  const looseOptions = [];
  for (const choice of choices) {
    const group = preferenceChoiceGroup(choice);
    if (!group) {
      looseOptions.push(optionHtml(choice));
      continue;
    }
    if (!groupLookup.has(group)) {
      groupLookup.set(group, []);
      groups.push(group);
    }
    groupLookup.get(group).push(optionHtml(choice));
  }
  return [
    ...looseOptions,
    ...groups.map(group => `<optgroup label="${esc(group)}">${groupLookup.get(group).join('')}</optgroup>`),
  ].join('');
}

function preferenceControlHtml(item, query = '') {
  if (!preferenceItemMatches(item, query)) return '';
  if (item.type === 'note') {
    return `<div class="preferences-setting-row preferences-setting-note">${esc(item.text || '')}</div>`;
  }
  const value = preferenceValue(item.path);
  const defaultValue = preferenceDefault(item.path);
  const disabled = readOnlyMode ? ' disabled' : '';
  const controlId = `preference-${item.path.replace(/[^A-Za-z0-9_-]+/g, '-')}`;
  const minAttr = item.min !== undefined ? ` data-setting-min="${esc(item.min)}"` : '';
  const maxAttr = item.max !== undefined ? ` data-setting-max="${esc(item.max)}"` : '';
  const baseAttrs = `id="${esc(controlId)}" data-setting-path="${esc(item.path)}" data-setting-type="${esc(item.type)}"${minAttr}${maxAttr}${disabled}`;
  let control = '';
  if (item.type === 'boolean') {
    control = `<input type="checkbox" ${baseAttrs}${value ? ' checked' : ''}>`;
  } else if (item.type === 'number') {
    control = `<input type="number" ${baseAttrs} inputmode="decimal" value="${esc(clampPreferenceNumber(item, item.scale ? Number(value) / item.scale : value))}" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(item.step || 1)}">`;
  } else if (item.type === 'range') {
    const rangeValue = clampPreferenceNumber(item, item.scale ? Number(value) / item.scale : value);
    control = `<input type="range" ${baseAttrs} value="${esc(rangeValue)}" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(item.step || 1)}"><output class="preferences-range-value" for="${esc(controlId)}">${esc(rangeValue)}</output>`;
  } else if (item.type === 'select') {
    control = `<select ${baseAttrs}>${preferenceSelectOptionsHtml(item, value)}</select>`;
  } else if (item.type === 'radio') {
    // #260: plain radio-button group (replaced the macOS-style theme-cards). One input + label per
    // choice; each input carries data-setting-path so the shared change handler -> savePreferenceControl
    // persists it (and live-applies appearance.theme). The current value is checked.
    const choices = item.choices || [];
    const groupHasSwatches = choices.some(choice => Array.isArray(choice?.swatches));
    const radios = choices.map(choice => {
      const choiceValue = String(preferenceChoiceValue(choice));
      const selected = String(value) === choiceValue;
      const radioId = `${controlId}-${choiceValue.replace(/[^A-Za-z0-9_-]+/g, '-')}`;
      const swatches = Array.isArray(choice?.swatches)
        ? `<span class="preferences-radio-swatches${choice.joinedSwatches ? ' joined' : ''}" aria-hidden="true">${choice.swatches.map(color => `<span class="preferences-radio-swatch" style="--preferences-radio-swatch:${esc(color)}"></span>`).join('')}</span>`
        : '';
      return `<label class="preferences-radio${swatches ? ' has-swatches' : ''}" for="${esc(radioId)}">
        <input type="radio" id="${esc(radioId)}" name="${esc(controlId)}" value="${esc(choiceValue)}" data-setting-path="${esc(item.path)}" data-setting-type="radio"${selected ? ' checked' : ''}${disabled}>
        ${swatches}<span>${esc(preferenceChoiceLabel(choice))}</span>
      </label>`;
    }).join('');
    control = `<div class="preferences-radio-group${groupHasSwatches ? ' has-swatches' : ''}" role="radiogroup" aria-label="${esc(item.label)}">${radios}</div>`;
  } else if (item.type === 'list') {
    const text = Array.isArray(value) ? value.join('\n') : String(value || '');
    control = `<textarea ${baseAttrs} rows="3">${esc(text)}</textarea>`;
  } else if (item.type === 'textarea') {
    control = `<textarea ${baseAttrs} rows="3" data-setting-autosize="true">${esc(String(value || ''))}</textarea>`;
  } else {
    control = `<input type="text" ${baseAttrs} value="${esc(value)}">`;
  }
  const resetDisabled = readOnlyMode || (!item.alwaysEnableReset && JSON.stringify(value) === JSON.stringify(defaultValue)) ? ' disabled' : '';
  const extraControl = item.action === 'open-yolo-rule'
    ? `<button type="button" class="preferences-inline-action" data-yolo-rule-open${readOnlyMode ? ' disabled' : ''}>${esc(t('pref.openAction'))}</button>`
    : '';
  const suffix = item.suffix ? `<span class="preferences-setting-suffix">${esc(item.suffix)}</span>` : '';
  const help = item.help ? `<span class="preferences-setting-help">${esc(item.help)}</span>` : '';
  const advisory = preferenceAdvisoryHtml(item, value);
  const rowClass = item.type === 'textarea' || item.wide ? ' preferences-setting-row--wide' : '';
  return `<div class="preferences-setting-row${rowClass}"><label class="preferences-setting-label" for="${esc(controlId)}">${esc(item.label)}${help}</label><span class="preferences-setting-control setting-type-${esc(item.type)}">${control}${suffix}${extraControl}<button type="button" class="preferences-reset" data-setting-reset="${esc(item.path)}"${resetDisabled}>${esc(t('pref.reset.row'))}</button></span>${advisory}</div>`;
}

function uploadRsyncExampleCommand() {
  const host = serverHostname || '<host>';
  const destination = homePath || '~';
  return `rsync -avz <local-path> ${host}:${destination}/`;
}

function preferenceAdvisoryHtml(item, value) {
  if (item.path !== 'uploads.max_bytes' || Number(value) <= uploadRsyncRecommendationBytes) return '';
  const command = uploadRsyncExampleCommand();
  return `<div class="preferences-setting-advisory">
    <span>${esc(t('pref.advisory.upload', {size: formatFileSize(uploadRsyncRecommendationBytes)}))}</span>
    <code>${esc(command)}</code>
    <button type="button" class="preferences-inline-action" data-copy-text="${esc(command)}">${esc(t('pref.advisory.copyRsync'))}</button>
  </div>`;
}

function preferencesPanelHtml() {
  const query = preferenceSearchNeedle();
  const sections = preferenceSections()
    .filter(section => preferenceSectionMatches(section, query))
    .map(section => {
      const titleMatches = textMatchesPreferenceQuery(section.title, query);
      const visibleItems = section.items.filter(item => titleMatches || preferenceItemMatches(item, query));
      const collapsed = !query && collapsedPreferenceSections.has(section.title);
      const sectionIntro = section.id === 'yolo' && (!query || textMatchesPreferenceQuery('yolo rules rule file yaml auto approve approval', query))
        ? preferencesYoloRulesPathHtml()
        : '';
      const rows = `${sectionIntro}${visibleItems.map(item => preferenceControlHtml(item)).join('')}`;
      const count = visibleItems.length;
      return `
        <section class="preferences-section${collapsed ? ' collapsed' : ''}" data-preference-section="${esc(section.title)}">
          <button type="button" class="preferences-section-toggle" data-preference-section-toggle="${esc(section.title)}" aria-expanded="${collapsed ? 'false' : 'true'}">
            <span class="preferences-section-caret" aria-hidden="true"></span>
            <span class="preferences-section-title">${esc(section.title)}</span>
            <span class="preferences-section-count">${count}</span>
          </button>
          <div class="preferences-settings"${collapsed ? ' hidden' : ''}>${rows}</div>
        </section>`;
    }).join('');
  const readonly = readOnlyMode ? `<span class="preferences-readonly">${esc(t('pref.readonly'))}</span>` : '';
  const resetDisabled = readOnlyMode ? ' disabled' : '';
  const resetTitle = preferencesResetConfirmVisible ? t('pref.reset.confirmTitle') : t('pref.reset.title');
  const resetWarning = preferencesResetConfirmVisible
    ? t('pref.reset.confirmWarning')
    : t('pref.reset.warning', {name: fileExplorerLabel()});
  const resetAction = preferencesResetConfirmVisible ? `
      <div class="preferences-reset-confirm">
        <button type="button" class="preferences-reset-continue" data-preferences-reset-confirm${resetDisabled}>${esc(t('pref.reset.continue'))}</button>
        <button type="button" class="preferences-reset-cancel" data-preferences-reset-cancel>${esc(t('pref.reset.cancel'))}</button>
      </div>` : `<button type="button" class="preferences-reset-all" data-preferences-reset-all${resetDisabled}>${esc(t('pref.reset.all'))}</button>`;
  const resetBlock = `
    <div class="preferences-global-reset${preferencesResetConfirmVisible ? ' confirming' : ''}" role="group" aria-label="${esc(t('pref.reset.aria'))}">
      <div>
        <div class="preferences-global-reset-title">${resetTitle}</div>
        <div class="preferences-global-reset-warning">${resetWarning}</div>
      </div>
      ${resetAction}
    </div>`;
  return `
    <div class="preferences-search-row">
      <input type="search" class="preferences-search" data-preferences-search value="${esc(preferencesSearchText)}" placeholder="${esc(t('pref.searchPlaceholder'))}" aria-label="${esc(t('pref.searchPlaceholder'))}">
      <button type="button" class="preferences-search-button" data-preferences-search-action>YOsearch</button>
    </div>
    <div class="preferences-path-rows">${preferencesPathRowsHtml()}${readonly}</div>
    <div class="preferences-sections">${sections}</div>
    ${resetBlock}`;
}

function debugEventCounts() {
  const apiCalls = jsDebugEvents.filter(event => event.type === 'api').length;
  const errors = jsDebugEvents.filter(event => event.type === 'error' || event.type === 'unhandledrejection' || event.error).length;
  return {apiCalls, errors};
}

function debugMetaText() {
  return t('debug.meta', {count: jsDebugEvents.length});
}

function debugStatHtml(label, value, key = '') {
  const data = key ? ` data-js-debug-stat="${esc(key)}"` : '';
  return `<div class="js-debug-stat"><span>${esc(label)}</span><strong${data}>${esc(value)}</strong></div>`;
}

function debugTimeText(value) {
  const match = String(value || '').match(/T(\d\d:\d\d:\d\d)/);
  return match ? match[1] : String(value || '');
}

function debugEventTypeLabel(type) {
  if (type === 'api') return 'API';
  if (type === 'unhandledrejection') return 'Promise';
  if (type === 'error') return 'Error';
  return String(type || 'Event');
}

function debugEventStatusText(event) {
  if (event.error) return 'error';
  if (Number.isFinite(event.status)) return `HTTP ${event.status}`;
  if (typeof event.ok === 'boolean') return event.ok ? 'ok' : 'not ok';
  return '';
}

function debugEventDetailText(event) {
  if (event.type === 'api') return `${event.method || 'GET'} ${event.url || ''}`.trim();
  return event.message || event.reason || event.source || '';
}

function debugEventMetaText(event) {
  return [
    debugTimeText(event.ts),
    Number.isFinite(event.durationMs) ? `${event.durationMs} ms` : '',
    debugEventStatusText(event),
    event.source ? `source: ${event.source}` : '',
    event.line ? `line ${event.line}${event.column ? `:${event.column}` : ''}` : '',
  ].filter(Boolean).join(' | ');
}

function debugEventLineText(event) {
  const status = debugEventStatusText(event);
  const duration = Number.isFinite(event.durationMs) ? `${event.durationMs}ms` : '';
  const location = event.source ? `${event.source}${event.line ? `:${event.line}${event.column ? `:${event.column}` : ''}` : ''}` : '';
  return [
    debugTimeText(event.ts),
    debugEventTypeLabel(event.type).padEnd(7),
    status.padEnd(8),
    duration.padStart(8),
    debugEventDetailText(event) || t('debug.event'),
    location,
  ].filter(Boolean).join(' ');
}

function jsDebugTextForClipboard() {
  const page = `${location.pathname || ''}${location.search || ''}${location.hash || ''}`;
  const counts = debugEventCounts();
  const header = [
    `JS Debug ${new Date().toISOString()}`,
    `page=${page || '/'}`,
    `events=${jsDebugEvents.length}`,
    `api=${counts.apiCalls}`,
    `errors=${counts.errors}`,
  ].join(' ');
  const rows = jsDebugEvents.map(debugEventLineText);
  return [header, ...rows].join('\n');
}

function debugPanelHtml() {
  const counts = debugEventCounts();
  return `
    <div class="js-debug-toolbar">
      <div class="js-debug-summary" aria-label="${esc(t('debug.summary'))}">
        ${debugStatHtml(t('debug.events'), jsDebugEvents.length, 'events')}
        ${debugStatHtml(t('debug.apiCalls'), counts.apiCalls, 'api')}
        ${debugStatHtml(t('debug.errors'), counts.errors, 'errors')}
      </div>
      <div class="js-debug-actions">
        <button type="button" class="preferences-inline-action" data-js-debug-copy>${esc(t('debug.copy'))}</button>
        <button type="button" class="preferences-inline-action" data-js-debug-clear>${esc(t('debug.clear'))}</button>
      </div>
    </div>
    <textarea class="js-debug-log" data-js-debug-log readonly spellcheck="false" aria-label="${esc(t('debug.recent'))}">${esc(jsDebugTextForClipboard())}</textarea>`;
}

function createDebugPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel js-debug-panel';
  panel.id = `panel-${debugPaneItemId}`;
  panel.innerHTML = `
      <div class="panel-head preferences-panel-head">
        ${virtualPanelControlsHtml(debugPaneItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${debugPaneItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.debug'))}</span></div>
          <div id="meta-${debugPaneItemId}" class="meta">${esc(debugMetaText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(debugPaneItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="preferences-body js-debug-body panel-overlay-root">
        <div id="panel-toasts-${debugPaneItemId}" class="panel-toast-stack"></div>
        <div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>
      </div>`;
  bindPanelShell(panel, debugPaneItemId);
  bindDebugPanel(panel);
  return panel;
}

function renderDebugPanels(options = {}) {
  if (dragSession != null) return;
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    const body = panel.querySelector('.js-debug-body');
    refreshDebugPanelFromEvents(panel, options);
    if (body && (options.force === true || !body.querySelector('[data-js-debug-log]'))) {
      body.innerHTML = `<div id="panel-toasts-${debugPaneItemId}" class="panel-toast-stack"></div><div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`;
      refreshDebugPanelFromEvents(panel, {force: true});
    }
    bindDebugPanel(panel);
  }
}

function refreshDebugPanelsFromEvents() {
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    refreshDebugPanelFromEvents(panel);
  }
}

function refreshDebugPanelFromEvents(panel, options = {}) {
  if (!panel) return;
  const meta = panel.querySelector(`#meta-${cssEscape(debugPaneItemId)}`);
  if (meta) meta.textContent = debugMetaText();
  const counts = debugEventCounts();
  const statEvents = panel.querySelector('[data-js-debug-stat="events"]');
  const statApi = panel.querySelector('[data-js-debug-stat="api"]');
  const statErrors = panel.querySelector('[data-js-debug-stat="errors"]');
  if (statEvents) statEvents.textContent = String(jsDebugEvents.length);
  if (statApi) statApi.textContent = String(counts.apiCalls);
  if (statErrors) statErrors.textContent = String(counts.errors);
  const log = panel.querySelector('[data-js-debug-log]');
  if (!log || (document.activeElement === log && options.force !== true)) return;
  const text = jsDebugTextForClipboard();
  if (log.value === text) return;
  const oldTop = log.scrollTop;
  const maxScroll = Math.max(0, log.scrollHeight - log.clientHeight);
  const nearBottom = maxScroll - oldTop <= 20;
  log.value = text;
  log.scrollTop = nearBottom || options.force === true ? log.scrollHeight : oldTop;
}

function bindDebugPanel(panel) {
  if (!panel || panel.dataset.debugBound === 'true') return;
  panel.dataset.debugBound = 'true';
  panel.addEventListener('click', event => {
    const copy = event.target.closest('[data-js-debug-copy]');
    if (copy && panel.contains(copy)) {
      event.preventDefault();
      copyTextToClipboard(jsDebugTextForClipboard())
        .then(() => { statusEl.textContent = t('debug.copied'); })
        .catch(error => { statusErr(`copy failed: ${esc(error)}`); });
      return;
    }
    const clear = event.target.closest('[data-js-debug-clear]');
    if (clear && panel.contains(clear)) {
      event.preventDefault();
      clearJsDebugEvents();
      statusEl.textContent = t('debug.cleared');
    }
  });
}

function createPreferencesPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel preferences-panel';
  panel.id = `panel-${prefsItemId}`;
  panel.innerHTML = `
      <div class="panel-head preferences-panel-head">
        ${virtualPanelControlsHtml(prefsItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${prefsItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.preferences'))}</span></div>
          <div id="meta-${prefsItemId}" class="meta">${esc(preferenceStatusText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(prefsItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="preferences-body panel-overlay-root">
        <div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div>
        <div class="preferences-scroll">${preferencesPanelHtml()}</div>
      </div>`;
  bindPanelShell(panel, prefsItemId);
  bindPreferencesPanel(panel);
  return panel;
}

function focusPreferencesSearch(panel = null) {
  // DOIT.6 #30: never steal focus into the search box while a tab is being dragged — focus() during a
  // drag (and the re-render it triggers) aborts the native drag.
  if (dragSession != null) return false;
  const root = panel && panel.isConnected !== false
    ? panel
    : (Array.from(document.querySelectorAll('.preferences-panel')).find(candidate => candidate.offsetParent !== null) || document.querySelector('.preferences-panel'));
  const search = root?.querySelector?.('[data-preferences-search]');
  if (!search) return false;
  search.focus?.({preventScroll: true});
  const position = String(search.value || '').length;
  search.setSelectionRange?.(position, position);
  return true;
}

function preferencesScrollIsActive(now = Date.now()) {
  return Number(now) < preferencesScrollActiveUntil;
}

function schedulePreferencesScrollFlush() {
  if (preferencesScrollFlushTimer) clearTimeout(preferencesScrollFlushTimer);
  preferencesScrollFlushTimer = setTimeout(() => {
    preferencesScrollFlushTimer = null;
    if (!pendingPreferencesRender) return;
    if (preferencesScrollIsActive()) {
      schedulePreferencesScrollFlush();
      return;
    }
    pendingPreferencesRender = false;
    renderPreferencesPanels();
  }, preferencesScrollRenderDeferMs);
}

function notePreferencesScrollActivity(now = Date.now()) {
  preferencesScrollActiveUntil = Math.max(preferencesScrollActiveUntil, Number(now) + preferencesScrollRenderDeferMs);
  schedulePreferencesScrollFlush();
}

function renderPreferencesPanels(options = {}) {
  // DOIT.6 #30: defer Preferences re-render while a tab drag is in flight; rebuilding the dragged tab
  // node aborts the native HTML5 drag.
  if (dragSession != null) { pendingPreferencesRender = true; return; }
  if (options.force !== true && preferencesScrollIsActive()) {
    pendingPreferencesRender = true;
    schedulePreferencesScrollFlush();
    return;
  }
  for (const panel of document.querySelectorAll('.preferences-panel')) {
    const body = panel.querySelector('.preferences-body');
    const meta = panel.querySelector(`#meta-${cssEscape(prefsItemId)}`);
    if (meta) meta.textContent = preferenceStatusText();
    if (body) {
      const activeControl = activePreferenceControl(panel);
      const shouldKeepDom = activeControl && options.force !== true;
      // DOIT.16 C3: the scroller is the inner .preferences-scroll, not the overlay-root body.
      const scroller = () => body.querySelector('.preferences-scroll') || body;
      const prevScroll = scroller();
      const scrollTop = prevScroll.scrollTop;
      const scrollLeft = prevScroll.scrollLeft;
      if (shouldKeepDom) {
        const pathRows = body.querySelector('.preferences-path-rows');
        if (pathRows) pathRows.innerHTML = `${preferencesPathRowsHtml()}${readOnlyMode ? '<span class="preferences-readonly">readonly access</span>' : ''}`;
      } else {
        body.innerHTML = `<div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div><div class="preferences-scroll">${preferencesPanelHtml()}</div>`;
      }
      if (options.focusSearch !== true) {
        const restore = () => { const s = scroller(); s.scrollTop = scrollTop; s.scrollLeft = scrollLeft; };
        restore();
        requestAnimationFrame(restore);
      }
    }
    bindPreferencesPanel(panel);
    autosizePreferenceTextareas(panel);
    if (options.focusSearch) focusPreferencesSearch(panel);
  }
}

function autosizePreferenceTextarea(textarea) {
  if (!textarea || textarea.dataset.settingAutosize !== 'true') return;
  textarea.style.height = 'auto';
  textarea.style.height = `${textarea.scrollHeight}px`;
}

function autosizePreferenceTextareas(root) {
  root.querySelectorAll?.('textarea[data-setting-autosize="true"]').forEach(autosizePreferenceTextarea);
}

function bindPreferencesPanel(panel) {
  if (!panel || panel.dataset.preferencesBound === 'true') return;
  panel.dataset.preferencesBound = 'true';
  panel.addEventListener('input', event => {
    const search = event.target.closest('[data-preferences-search]');
    if (search && panel.contains(search)) {
      preferencesSearchText = search.value || '';
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true, focusSearch: true});
      return;
    }
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control)) return;
    if (control.dataset.settingAutosize === 'true') {
      autosizePreferenceTextarea(control);
    }
    if (control.dataset.settingType === 'number') {
      validatePreferenceNumberControl(control);
      return;
    }
    if (control.dataset.settingType === 'range') {
      const value = valueFromPreferenceControl(control);
      const output = control.parentElement?.querySelector('.preferences-range-value');
      if (output) output.textContent = String(control.value);
      if (control.dataset.settingPath === 'appearance.inactive_pane_opacity') applyInactivePaneOpacity(value);
      if (control.dataset.settingPath === 'appearance.pane_ring_opacity') applyPaneRingOpacity(value);
    }
  });
  panel.addEventListener('change', event => {
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control)) return;
    savePreferenceControl(control);
  });
  panel.addEventListener('wheel', event => {
    if (event.target.closest?.('.preferences-scroll')) notePreferencesScrollActivity();
  }, {passive: true});
  panel.addEventListener('touchmove', event => {
    if (event.target.closest?.('.preferences-scroll')) notePreferencesScrollActivity();
  }, {passive: true});
  panel.addEventListener('scroll', event => {
    if (event.target?.classList?.contains('preferences-scroll')) notePreferencesScrollActivity();
  }, true);
  panel.addEventListener('focusout', () => {
    setTimeout(() => {
      if (!activePreferenceControl(panel)) renderPreferencesPanels();
    }, 0);
  });
  panel.addEventListener('click', async event => {
    const searchAction = event.target.closest('[data-preferences-search-action]');
    if (searchAction && panel.contains(searchAction)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true});
      focusPreferencesSearch(panel);
      return;
    }
    const resetAll = event.target.closest('[data-preferences-reset-all]');
    if (resetAll && panel.contains(resetAll)) {
      event.preventDefault();
      preferencesResetConfirmVisible = true;
      renderPreferencesPanels({force: true});
      setTimeout(() => {
        const confirm = document.querySelector('[data-preferences-reset-confirm]');
        confirm?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});
        confirm?.focus?.();
      }, 0);
      return;
    }
    const resetConfirm = event.target.closest('[data-preferences-reset-confirm]');
    if (resetConfirm && panel.contains(resetConfirm)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      resetAllPreferences();
      return;
    }
    const resetCancel = event.target.closest('[data-preferences-reset-cancel]');
    if (resetCancel && panel.contains(resetCancel)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true});
      return;
    }
    const copy = event.target.closest('[data-copy-path]');
    if (copy && panel.contains(copy)) {
      event.preventDefault();
      copyTextToClipboard(copy.dataset.copyPath || '')
        .then(() => { statusEl.textContent = 'copied path'; })
        .catch(error => { statusErr(`copy failed: ${esc(error)}`); });
      return;
    }
    const copyText = event.target.closest('[data-copy-text]');
    if (copyText && panel.contains(copyText)) {
      event.preventDefault();
      copyTextToClipboard(copyText.dataset.copyText || '')
        .then(() => { statusEl.textContent = 'copied text'; })
        .catch(error => { statusErr(`copy failed: ${esc(error)}`); });
      return;
    }
    const yoloRuleOpen = event.target.closest('[data-yolo-rule-open]');
    if (yoloRuleOpen && panel.contains(yoloRuleOpen)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      openYoloRuleFile();
      return;
    }
    const sectionToggle = event.target.closest('[data-preference-section-toggle]');
    if (sectionToggle && panel.contains(sectionToggle)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      const title = sectionToggle.dataset.preferenceSectionToggle || '';
      if (collapsedPreferenceSections.has(title)) collapsedPreferenceSections.delete(title);
      else collapsedPreferenceSections.add(title);
      writeStoredCollapsedPreferenceSections();
      const section = sectionToggle.closest('[data-preference-section]');
      const collapsed = collapsedPreferenceSections.has(title);
      if (section) {
        section.classList.toggle('collapsed', collapsed);
        sectionToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const settings = section.querySelector('.preferences-settings');
        if (settings) settings.hidden = collapsed;
      } else {
        renderPreferencesPanels({force: true});
      }
      return;
    }
    const reset = event.target.closest('[data-setting-reset]');
    if (!reset || !panel.contains(reset)) return;
    event.preventDefault();
    resetPreference(reset.dataset.settingReset || '');
  });
}

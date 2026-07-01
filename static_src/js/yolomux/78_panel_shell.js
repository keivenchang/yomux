// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared panel layout, pane-tab shell, tmux sub-window controls, and search/history panel helpers split from 80_panes_preferences.js.

function renderPanels(previousActive = [], options = {}) {
  const perf = clientPerfStart('renderPanels');
  try {
    return renderPanelsMeasured(previousActive, options);
  } finally {
    clientPerfEnd(perf, {nodes: grid?.childElementCount || 0});
  }
}

function renderPanelsMeasured(previousActive = [], options = {}) {
  if (renderPanelsDockview(previousActive, options)) return;
  // a full panel re-render pools every panel and clears the grid, which detaches
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
  scheduleAgentWindowActivityAnimationSync();
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
    capturePaneViewState(item, panel);
    panel.classList.remove('active-pane');
    panel.dataset.slot = '';
    panelPool.appendChild(panel);
  }
}

// the registered item for a mounted panel node (reverse lookup of panelNodes).
function panelItemForNode(node) {
  if (!node) return null;
  for (const [item, panel] of panelNodes.entries()) {
    if (panel === node) return item;
  }
  return null;
}

// move ONE displaced panel back to the pool (preserving editor state), like movePanelsToPool
// does for all. An empty-pane placeholder (not in panelNodes) is just dropped.
function poolDisplacedPanel(node) {
  const item = panelItemForNode(node);
  if (item == null) return;
  capturePaneViewState(item, node);
  node.classList.remove('active-pane');
  node.dataset.slot = '';
  panelPool.appendChild(node);
}

// for a SAME-SHAPE layout change, swap only the slots whose ACTIVE item changed — no
// grid.innerHTML='' / topbar teardown, no detach/reattach of unrelated panes. A pure reorder touches
// zero panels (active unchanged) and only reconciles the tab strip via updatePanelSlot.
function syncActivePanelsInPlace() {
  if (dockviewLayoutActive()) {
    syncActivePanelsDockview();
    return;
  }
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
    restorePaneViewState(item, desired);
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
    : (direction === 'column' ? minSplitPaneHeightPx() : minSplitPaneWidthPx());
  const minSecond = children
    ? (direction === 'column' ? layoutNodeMinHeight(children[1]) : layoutNodeMinWidth(children[1]))
    : (direction === 'column' ? minSplitPaneHeightPx() : minSplitPaneWidthPx());
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
  return minSplitPaneWidthPx();
}

function minWidthForLayoutSlot(slot, slots = layoutSlots) {
  return minWidthForLayoutItem(activeItemForSide(slot, slots));
}

function layoutVisiblePaneCount(slots = layoutSlots) {
  return layoutSlotKeys(slots).filter(slot => paneHasLayoutContent(slot, slots)).length;
}

function layoutNodeMinWidth(node, slots = layoutSlots) {
  if (!node) return minSplitPaneWidthPx();
  if (node.slot) return paneHasLayoutContent(node.slot, slots) ? minWidthForLayoutSlot(node.slot, slots) : minSplitPaneWidthPx();
  const children = node.children || [];
  if (node.split === 'column') return Math.max(...children.map(child => layoutNodeMinWidth(child, slots)), minSplitPaneWidthPx());
  return children.reduce((sum, child) => sum + layoutNodeMinWidth(child, slots), 0);
}

function layoutNodeMinHeight(node, slots = layoutSlots) {
  if (!node) return minSplitPaneHeightPx();
  if (node.slot) return minSplitPaneHeightPx();
  const children = node.children || [];
  if (node.split === 'column') return children.reduce((sum, child) => sum + layoutNodeMinHeight(child, slots), 0);
  return Math.max(...children.map(child => layoutNodeMinHeight(child, slots)), minSplitPaneHeightPx());
}

function prunePriorityForLayoutSlot(slot) {
  const item = activeItemForSide(slot);
  const type = tabTypeForItem(item);
  if (type?.prunePriority) return type.prunePriority(item);
  return 2;
}

function slotCanAutoPrune(slot) {
  return !paneTabs(slot).some(isFileExplorerItem);
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
    const tooSmall = rect.width < minWidthForLayoutSlot(slot) || rect.height < minSplitPaneHeightPx();
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
  restorePaneViewState(session, panel);
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
  const perf = clientPerfStart('renderPaneTabStrips');
  try {
    return renderPaneTabStripsMeasured();
  } finally {
    clientPerfEnd(perf, {nodes: document.querySelectorAll?.('.pane-tab')?.length || 0});
  }
}

function renderPaneTabStripsMeasured() {
  if (dockviewLayoutActive()) {
    dockviewRefreshTabs();
    dockviewSyncMountedPanels();
    return;
  }
  // do not rebuild tab DOM while a tab is being dragged — replacing the dragged node
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

function paneTabPopoverForAnchor(tab) {
  return tab?.querySelector?.(':scope > .session-popover') || tab?.__yolomuxDetachedPopover || null;
}

function paneTabShouldPreserve(tab) {
  const popover = paneTabPopoverForAnchor(tab);
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
    const popover = paneTabPopoverForAnchor(tab);
    if (popover && (popoverLifecycleActive(tab, popover) || popoverStillActive(tab, popover))) return tab.dataset.paneTab || null;
  }
  return null;
}

function restorePaneTabPopover(strip, item) {
  if (!item) return;
  const tab = strip.querySelector(`:scope > .pane-tab[data-pane-tab="${cssEscape(item)}"]`);
  const popover = paneTabPopoverForAnchor(tab);
  if (!tab || !popover) return;
  if (tab.classList.contains('popover-open') && popoverLifecycleActive(tab, popover)) return;
  positionPaneTabPopover(tab, popover);
  closeOtherSessionPopovers(tab);
  tab.classList.add('popover-open');
}

// the inner markup of a pane tab — pin icon + row (virtual rowHtml or tmux) + close button +
// the file/session popover — built once here and shared by BOTH the DOM factory (createPaneTab) and the
// Dockview string builder (dockviewPaneTabHtml in 75_dockview_layout.js), so pin/close/popover parity is
// enforced in one place instead of by hand across two renderers.
function paneTabInnerHtml(item, rowOptions = {}) {
  const type = tabTypeForItem(item);
  const isFiles = type?.key === 'files';
  const isEditor = isFileEditorItem(item);
  const isVirtual = Boolean(type);
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true && !isVirtual;
  const state = isVirtual ? null : sessionState(item, info);
  const agentKind = isVirtual ? '' : sessionAgentKind(item);
  let html = type?.rowHtml ? type.rowHtml(item, rowOptions) : tmuxPaneTabHtml(item, info, state, auto);
  html = `${pinnedTabIconHtml(item)}${html}`;
  if (!isFiles) {
    const closeTitle = isEditor ? `Close ${itemLabel(item)}` : `hide ${itemLabel(item)} from layout`;
    const closeLabel = isEditor ? `Close ${itemLabel(item)}` : `Hide ${itemLabel(item)} from layout`;
    const controlKind = isEditor ? 'close' : 'minimize';
    html += toolbarButtonHtml({
      className: `pane-tab-close ${platformWindowControlClass(controlKind)}`,
      action: 'pane-tab-close',
      dataset: {paneTabClose: ''},
      title: closeTitle,
      ariaLabel: closeLabel,
    });
  }
  if (isEditor) html += filePopoverHtml(item);
  else if (!isVirtual) html += sessionPopoverHtml(item, info, agentKind, auto, state);
  return html;
}

function paneTabDragSourceItem(itemOrGetter, event) {
  return typeof itemOrGetter === 'function' ? String(itemOrGetter(event) || '') : String(itemOrGetter || '');
}

function paneTabDragSourceSlot(item, sourceSlotOrGetter, event) {
  const slot = typeof sourceSlotOrGetter === 'function' ? sourceSlotOrGetter(item, event) : sourceSlotOrGetter;
  return slot || slotForItem(item);
}

function bindPaneTabNativeDragSource(tab, itemOrGetter, sourceSlotOrGetter = null, options = {}) {
  if (!tab || tab.__yolomuxPaneTabNativeDragBound) return;
  tab.__yolomuxPaneTabNativeDragBound = true;
  tab.draggable = true;
  tab.addEventListener('dragstart', event => {
    if (options.ignore?.(event) === true) {
      event.preventDefault?.();
      return;
    }
    const item = paneTabDragSourceItem(itemOrGetter, event);
    if (!isLayoutItem(item)) {
      event.preventDefault?.();
      return;
    }
    event.stopPropagation();
    startSessionDrag(event, item, paneTabDragSourceSlot(item, sourceSlotOrGetter, event), {
      dragImage: options.dragImage,
    });
  });
  tab.addEventListener('dragend', endSessionDrag);
}

function createPaneTab(side, item, displayContext = {}) {
  const type = tabTypeForItem(item);
  const isEditor = isFileEditorItem(item);
  const isVirtual = Boolean(type);
  const info = transcriptMeta.sessions?.[item];
  const state = isVirtual ? null : sessionState(item, info);
  const active = item === activeItemForSide(side);
  const tab = document.createElement('div');
  tab.role = 'button';
  tab.tabIndex = 0;
  const virtualClass = type?.className?.(item) || '';
  const missingFileClass = isEditor && openFileIsMissing(fileItemPath(item)) ? 'file-missing' : '';
  const tmuxTabClass = !isVirtual && !isEditor ? 'tmux-pane-tab-token' : '';
  tab.className = `pane-tab session-popover-host ${tmuxTabClass} ${virtualClass} ${missingFileClass} ${tabIsPinned(item) ? 'pinned-tab' : ''} ${active ? 'active' : ''}`;
  applySessionStateClasses(tab, state);
  tab.draggable = true;
  tab.dataset.paneTab = item;
  const rowOptions = isEditor ? {parentLabel: displayContext.fileParentLabels?.get(fileItemPath(item)) || ''} : {};
  tab.innerHTML = paneTabInnerHtml(item, rowOptions);
  if (isEditor) {
    bindFilePopoverActions(tab);
    bindPaneTabPopover(tab, item);
  } else if (!isVirtual) {
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
  bindActionDispatcher(tab, {
    'pane-tab-close': () => {
      if (isEditor) closeFileTab(fileItemPath(item), {item});
      else removeSessionFromLayout(item);
    },
    'pane-tab-auto-approve': async (_event, autoTarget) => {
      const shouldRefocus = item === activeItemForSide(side);
      await toggleAutoApprove(autoTarget.dataset.autoSession);
      if (shouldRefocus) focusPanel(item);
    },
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
  }
  if (isPinnableTab(item)) {
    tab.addEventListener('contextmenu', event => {
      event.preventDefault();
      event.stopPropagation();
      showTabContextMenu(item, event.clientX, event.clientY, {tab});
    });
  }
  bindPaneTabNativeDragSource(tab, item, () => side);
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
    statusErr(localizedHtml('status.readOnlyRenameFiles'));
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
  const detached = tab.classList?.contains('dockview-pane-tab') === true;
  if (detached) detachPaneTabPopover(tab, popover);
  bindDelayedSessionPopover(tab, popover, () => positionPaneTabPopover(tab, popover), {
    onOpen: () => maybeLoadFileTabForPopover(tab, session),
    onStateOpen: () => popover.classList.add('popover-open'),
    onClose: () => popover.classList.remove('popover-open'),
  });
}

function bindDelayedSessionPopover(anchor, popover, position, options = {}) {
  createHoverPopover({
    anchor,
    popover,
    showDelay: () => (document.querySelector('.pane-tab.popover-open, .tabber-session-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
    hideDelay: () => popoverHideDelayMs,
    canOpen: () => !appMenuIsOpen() && !topbar?.matches?.(':hover'),
    onQueue: position,
    onOpen: event => {
      options.onStateOpen?.(event);
      options.onOpen?.(event);
    },
    onClose: options.onClose,
    position,
    closeOthers: () => closeOtherSessionPopovers(anchor),
  });
}

function detachPaneTabPopover(tab, popover) {
  cleanupDetachedPaneTabPopover(tab, popover);
  popover.classList.add('pane-tab-detached-popover');
  const host = appOverlayRootElement();
  if (popover.parentElement !== host) host.appendChild(popover);
  tab.__yolomuxDetachedPopover = popover;
}

function cleanupDetachedPaneTabPopover(tab, keep = null) {
  const previous = tab?.__yolomuxDetachedPopover;
  if (previous && previous !== keep) previous.remove();
  if (previous && previous !== keep) tab.__yolomuxDetachedPopover = null;
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
  const detached = tab?.__yolomuxDetachedPopover?.classList?.contains('file-popover') ? tab.__yolomuxDetachedPopover : null;
  const popover = tab?.querySelector?.(':scope > .file-popover') || detached;
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

function positionPaneTabPopover(tab, popover = null) {
  const rect = tab.getBoundingClientRect();
  popover = popover || paneTabPopoverForAnchor(tab);
  const bridgeGap = 3;
  const edgeGap = popoverEdgeGapPx();
  const topbarBottom = Math.ceil(topbar?.getBoundingClientRect?.().bottom || rootCssLengthPx('--topbar-height') || 0);
  const bounds = viewportBounds(edgeGap);
  const maxInline = Math.max(0, bounds.right - bounds.left);
  // #45: when the live popover width measures 0 (e.g. before first paint), fall back to the popover's
  // CSS inline size (capped to the viewport) — NOT the tiny tab width. Over-estimating only pulls a
  // near-right-edge popover further left; clamping to the tab width let a wide needs-input popover
  // overflow and clip off the top-right corner.
  if (popover?.style) popover.style.height = '';
  const measured = Math.ceil(popover?.getBoundingClientRect?.().width || 0);
  const width = Math.min(maxInline, measured || rootCssLengthPx('--pane-tab-popover-inline-size') || maxInline);
  const height = Math.ceil(popover?.getBoundingClientRect?.().height || 0);
  const blockSize = height > 0 ? `${Math.round(height)}px` : '';
  const position = clampToViewport(
    Math.floor(rect.left),
    Math.ceil(rect.bottom) + bridgeGap,
    width,
    height,
    {edgeGap, minTop: topbarBottom + edgeGap},
  );
  const top = `${Math.round(position.top)}px`;
  const left = `${Math.round(position.left)}px`;
  const inlineSize = `${Math.round(width)}px`;
  document.documentElement.style.setProperty('--pane-tab-popover-top', top);
  document.documentElement.style.setProperty('--pane-tab-popover-left', left);
  if (popover?.style) {
    popover.style.top = top;
    popover.style.left = left;
    popover.style.width = inlineSize;
    if (blockSize) popover.style.height = blockSize;
    else popover.style.height = '';
  }
}

function paneInfoTabHtml(item = infoItemId, options = {}) {
  // use .session-button-dir (like the Finder/Prefs tabs) so the label gets the themed
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

function searchHistoryPaneTabHtml(item = searchHistoryItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">${esc(searchHistoryTabLabel())}</span></span>`;
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

function tmuxPaneTabHtml(session, info, state, auto, options = {}) {
  const pr = options.showBadges === false ? null : displayPullRequest(info);
  const desc = options.showDetail === false
    ? ''
    : (options.detail !== undefined ? String(options.detail || '') : sessionTabDescription(session, info));
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  const leadingHtml = options.leadingHtml !== undefined
    ? String(options.leadingHtml || '')
    : options.showLeading === false
    ? ''
    : sessionTabLeadingActivityHtml(session, info, auto, {enabledOnly: false, toggle: options.toggleYolo !== false, state});
  const stateHtml = options.showState === false || !state ? '' : sessionStateHtml(state);
  const badgeHtml = options.showBadges === false ? '' : `${defaultBranchBadgeHtml(session, info)}${pullRequestCompactBadgesHtml(session, pr)}`;
  return `<span class="pane-tab-core">${leadingHtml}<span class="session-button-prefix">${sessionNumberNameHtml(session, {labelHtml: options.sessionLabelHtml})}</span>
    <span class="session-button-text">${stateHtml}${badgeHtml}${detailHtml}</span></span>`;
}

function tmuxPaneTabTokenHtml(session, options = {}) {
  const item = String(options.item || session || '').trim();
  if (!item) return '';
  const info = Object.prototype.hasOwnProperty.call(options, 'info') ? options.info : transcriptMeta.sessions?.[item];
  const state = Object.prototype.hasOwnProperty.call(options, 'state') ? options.state : sessionState(item, info);
  const auto = Object.prototype.hasOwnProperty.call(options, 'auto') ? options.auto : autoApproveStates.get(item)?.enabled === true;
  const active = Object.prototype.hasOwnProperty.call(options, 'active') ? options.active === true : itemIsActivePaneTab(item);
  const tag = options.tag || (options.action === false ? 'span' : 'button');
  const actionClass = options.action === false ? 'tmux-pane-tab-token-static' : 'tmux-pane-tab-token-action';
  const classes = [
    'tmux-pane-tab-token',
    actionClass,
    ...(Array.isArray(options.classes) ? options.classes : []),
    tabIsPinned(item) ? 'pinned-tab' : '',
    active ? 'active' : '',
  ].filter(Boolean).join(' ');
  const attrs = [`class="${esc(classes)}"`, `data-pane-tab="${esc(item)}"`, `data-tmux-pane-tab-state="${active ? 'active' : 'inactive'}"`];
  if (tag === 'button') attrs.unshift('type="button"');
  if (options.title) attrs.push(`title="${esc(options.title)}"`);
  if (Array.isArray(options.attrs)) attrs.push(...options.attrs.filter(Boolean));
  const rawContentHtml = options.contentHtml || tmuxPaneTabHtml(item, info, state, auto, options);
  const contentHtml = options.stripContentTitles === true ? stripTitleAttrs(rawContentHtml) : rawContentHtml;
  const pinHtml = options.showPin === false ? '' : pinnedTabIconHtml(item);
  return `<${tag} ${attrs.join(' ')}>${pinHtml}${contentHtml}${options.afterHtml || ''}</${tag}>`;
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
    const placement = paneTabDropPlacement(strip, event, payload.session);
    if (placement.noop) return;
    moveSessionToSlot(payload.session, side, payload.sourceSlot || slotForSession(payload.session), placement.index);
  };
}

function showPaneTabDropPreview(strip, event, movingSession) {
  const placement = paneTabDropPlacement(strip, event, movingSession);
  if (placement.noop) {
    clearPaneTabDropPreview(strip);
    return;
  }
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

function paneTabDropPlacementResult(index, x, y, height, sourceVisualIndex = -1) {
  return {
    index,
    x,
    y,
    height,
    noop: sourceVisualIndex >= 0 && index === sourceVisualIndex,
  };
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
    return paneTabDropPlacementResult(
      0,
      clampX(event.clientX - stripRect.left),
      clampY(event.clientY - stripRect.top - defaultHeight / 2, defaultHeight),
      defaultHeight,
      sourceVisualIndex,
    );
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
      return paneTabDropPlacementResult(
        item.index,
        clampX(rect.left - stripRect.left),
        clampY(row.top - stripRect.top, row.height),
        row.height,
        sourceVisualIndex,
      );
    }
  }
  const last = rowTabs[rowTabs.length - 1];
  return paneTabDropPlacementResult(
    last.index + 1,
    clampX(last.rect.right - stripRect.left),
    clampY(row.top - stripRect.top, row.height),
    row.height,
    sourceVisualIndex,
  );
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
    // Native range dragging must keep the same input node for the whole gesture;
    // focusing YO!stats here would rerender the panel before the browser moves the thumb.
    if (event.target?.closest?.('[data-js-debug-range-slider], .js-debug-line-chart, [data-js-debug-zoom-reset]')) return;
    if (isTmuxSession(session)) {
      const windowTarget = event.target?.closest?.('[data-window-index]');
      const chromeTarget = event.target?.closest?.('[data-window-dir], [data-window-index], [data-pane-actions], [data-pane-minimize], [data-pane-expand], [data-pane-close], [data-detail-toggle], [data-auto-session]');
      if (windowTarget) {
        // Run the original target before polling or focus-side updates can replace it.
        // The marker suppresses the later delegated click if the browser still emits one.
        event.preventDefault();
        event.stopPropagation();
        windowTarget.dataset.pointerActionHandled = '1';
        handleWindowStepButtonClick({target: windowTarget, currentTarget: windowTarget});
        return;
      }
      if (chromeTarget) {
        setFocusedTerminal(session, {userInitiated: true, acknowledgeAgentWindow: false});
        return;
      }
      if (eventTargetIsTerminalFocusSurface(event?.target)) {
        focusTerminalFromUserAction(session);
      } else {
        noteFileExplorerChangesSessionInteraction(session);
        setFocusedTerminal(session, {userInitiated: true});
      }
    } else {
      setFocusedPanelItem(session, {userInitiated: true});
    }
  }, {capture: true});
  panel.addEventListener('focusin', event => {
    if (!isTmuxSession(session)) {
      setFocusedPanelItem(session);
    }
  });
  panel.addEventListener('scroll', event => {
    const target = event.target;
    if (target?.matches?.(paneScrollContainerSelector) && panel.contains(target)) {
      schedulePaneViewStateCapture(session, panel);
    }
  }, true);
  const head = panel.querySelector('.panel-head');
  if (head) {
    head.draggable = true;
    head.dataset.dragSession = session;
    head.addEventListener('dragstart', event => startPaneDrag(event, head.dataset.dragSlot || slotForSession(session)));
    head.addEventListener('dragend', endSessionDrag);
    head.addEventListener('dragover', event => {
      const panePayload = paneDragPayload(event);
      if (panePayload?.slot) {
        event.preventDefault();
        event.stopPropagation();
        clearDropPreview();
        const targetSlot = head.dataset.dragSlot || slotForSession(session);
        const intent = paneSwapIntentForEvent(event, panePayload.slot) || {sourceSlot: panePayload.slot, targetSlot, swap: true};
        if (!paneSwapIntentAllowed(intent)) {
          event.dataTransfer.dropEffect = 'none';
          head.classList.remove(CLS.tabDragOver);
          return;
        }
        event.dataTransfer.dropEffect = 'move';
        head.classList.add(CLS.tabDragOver);
        return;
      }
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
      const panePayload = paneDragPayload(event);
      if (panePayload?.slot && !event.target.closest('.pane-tabs')) {
        head.classList.remove(CLS.tabDragOver);
        event.preventDefault();
        event.stopPropagation();
        const targetSlot = head.dataset.dragSlot || slotForSession(session);
        const intent = paneSwapIntentForEvent(event, panePayload.slot) || {sourceSlot: panePayload.slot, targetSlot, swap: true};
        clearDropPreview();
        if (paneSwapIntentAllowed(intent)) swapPaneSlots(intent.sourceSlot, intent.targetSlot);
        return;
      }
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

function eventTargetIsTerminalFocusSurface(target) {
  return Boolean(target?.closest?.('.terminal, .xterm'));
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

function panelDetailsToggleLabel(collapsed) {
  return collapsed ? t('pane.details.show') : t('pane.details.hide');
}

function syncPanelDetailsToggleButton(button, collapsed) {
  if (!button) return;
  const detailsLabel = panelDetailsToggleLabel(collapsed);
  button.classList.toggle(CLS.active, !collapsed);
  button.title = detailsLabel;
  button.setAttribute('aria-label', detailsLabel);
  button.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
}

function panelDetailToggleButtons(panel) {
  if (!panel) return [];
  const buttons = [];
  const seen = new Set();
  const add = button => {
    if (!button || seen.has(button)) return;
    seen.add(button);
    buttons.push(button);
  };
  panel.querySelectorAll?.('[data-detail-toggle]')?.forEach(add);
  const item = panel.dataset?.layoutItem || String(panel.id || '').replace(/^panel-/, '') || panel.dataset?.slot || '';
  if (item) {
    document.body?.querySelectorAll?.(`[data-detail-toggle="${cssEscape(item)}"]`)?.forEach(add);
  }
  return buttons;
}

function syncPanelDetailsToggleState(panel) {
  const collapsed = panel?.classList?.contains('details-collapsed') === true;
  panelDetailToggleButtons(panel).forEach(button => syncPanelDetailsToggleButton(button, collapsed));
}

function setPanelDetailsCollapsed(panel, collapsed) {
  panel.classList.toggle('details-collapsed', collapsed);
  syncPanelDetailsToggleState(panel);
  schedulePanelDetailsFit(panel);
}

function panelItemFromPanel(panel) {
  const id = String(panel?.id || '');
  return id.startsWith('panel-') ? id.slice('panel-'.length) : '';
}

function schedulePanelDetailsFit(panel) {
  const item = panelItemFromPanel(panel);
  if (item && isTmuxSession(item)) scheduleFit(item);
}

function terminalTabDisplayLabel(session, info) {
  return 'Term';
}

function terminalTabDetailLabel(session, info) {
  const label = terminalProcessLabel(session, info);
  return label || 'Term';
}

function terminalTabLabel(session, info) {
  const type = tabTypeForItem(session);
  if (type?.shortLabel) return type.shortLabel(session);
  return shortText(terminalTabDisplayLabel(session, info), 16);
}

function terminalTabTitle(session, info) {
  const type = tabTypeForItem(session);
  if (type?.terminalTitle) return type.terminalTitle(session);
  return `terminal: ${terminalTabDetailLabel(session, info)}`;
}

function terminalProcessLabel(session, info) {
  const pane = terminalDisplayPane(info, session);
  if (pane?.process_label) return pane.process_label;
  const agent = agentForPane(info, pane) || info?.agents?.[0];
  if (agent?.command) return processLabelFromCommand(agent.command);
  if (agent?.kind) return agent.kind;
  if (pane?.command) return pane.command;
  return 'Term';
}

function terminalDisplayPaneForWindowIndex(info, windowIndex) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (!panes.length || indexKey === null) return null;
  return panes.find(pane => tmuxWindowIndexKey(pane.window ?? pane.window_index) === indexKey && pane.active)
    || panes.find(pane => tmuxWindowIndexKey(pane.window ?? pane.window_index) === indexKey)
    || null;
}

function terminalDisplayPane(info, session = '') {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const override = session ? tmuxWindowDisplayActiveIndex(session) : undefined;
  if (override !== undefined && override !== tmuxWindowPendingActiveIndex) {
    const overridePane = terminalDisplayPaneForWindowIndex(info, override);
    if (overridePane) return overridePane;
  }
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

const tmuxWindowActiveIndexOverrides = new Map();
const tmuxWindowSwitchSequences = new Map();
const tmuxWindowDirectTargetGuards = new Map();
const tmuxWindowPendingActiveIndex = '__pending__';
const tmuxWindowConfirmedOverrideHoldMs = 4000;
const tmuxWindowDirectTargetGuardMs = 17000;

function tmuxWindowIndexKey(value) {
  const index = tmuxWindowNumber(value);
  return index === null ? null : String(index);
}

function tmuxWindowActiveIndexOverride(session) {
  return tmuxWindowActiveIndexOverrides.get(String(session || ''));
}

function tmuxWindowDisplayActiveIndex(session) {
  const override = tmuxWindowActiveIndexOverride(session);
  if (override !== undefined) return override;
  return tmuxWindowDirectTargetGuard(session)?.index;
}

function tmuxWindowDirectTargetGuard(session) {
  const key = String(session || '');
  const guard = tmuxWindowDirectTargetGuards.get(key);
  if (!guard) return null;
  if (Number(guard.guardUntilMs || 0) > Date.now()) return guard;
  tmuxWindowDirectTargetGuards.delete(key);
  return null;
}

function tmuxWindowDirectTargetGuardEntries() {
  const entries = [];
  for (const [session, guard] of tmuxWindowDirectTargetGuards.entries()) {
    const active = tmuxWindowDirectTargetGuard(session);
    if (active) entries.push([session, active]);
    else if (guard) tmuxWindowDirectTargetGuards.delete(session);
  }
  return entries;
}

function setTmuxWindowDirectTargetGuard(session, windowIndex, sequence) {
  const key = String(session || '');
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (!key || indexKey === null) return false;
  const now = Date.now();
  tmuxWindowDirectTargetGuards.set(key, {
    index: indexKey,
    sequence: Number(sequence) || 0,
    startedAtMs: now,
    confirmedAtMs: 0,
    guardUntilMs: now + tmuxWindowDirectTargetGuardMs,
  });
  return true;
}

function confirmTmuxWindowDirectTargetGuard(session, windowIndex, options = {}) {
  const key = String(session || '');
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (!key || indexKey === null) return false;
  const guard = tmuxWindowDirectTargetGuard(key);
  if (!guard || guard.index !== indexKey) return false;
  const sequence = tmuxWindowSwitchOptionSequence(options);
  if (sequence > 0 && Number(guard.sequence || 0) > 0 && Number(guard.sequence) !== sequence) return false;
  const now = Date.now();
  tmuxWindowDirectTargetGuards.set(key, {
    ...guard,
    confirmedAtMs: now,
    guardUntilMs: now + tmuxWindowDirectTargetGuardMs,
  });
  return true;
}

function clearTmuxWindowDirectTargetGuard(session, options = {}) {
  const key = String(session || '');
  if (!key) return false;
  const sequence = tmuxWindowSwitchOptionSequence(options);
  const guard = tmuxWindowDirectTargetGuard(key);
  if (sequence > 0 && guard && Number(guard.sequence || 0) > 0 && Number(guard.sequence) !== sequence) return false;
  return tmuxWindowDirectTargetGuards.delete(key);
}

function tmuxWindowSwitchSequence(session) {
  return Number(tmuxWindowSwitchSequences.get(String(session || '')) || 0);
}

function tmuxWindowSwitchOptionSequence(options = {}) {
  const sequence = Number(options.sequence);
  return Number.isFinite(sequence) && sequence > 0 ? sequence : 0;
}

function tmuxWindowSwitchSequenceMatches(session, sequence) {
  const value = Number(sequence);
  return !Number.isFinite(value) || value <= 0 || tmuxWindowSwitchSequence(session) === value;
}

function nextTmuxWindowSwitchSequence(session) {
  const key = String(session || '');
  if (!key) return 0;
  const next = tmuxWindowSwitchSequence(key) + 1;
  tmuxWindowSwitchSequences.set(key, next);
  return next;
}

function tmuxWindowOverrideSequence(session, options = {}) {
  const key = String(session || '');
  if (!key) return 0;
  const explicit = tmuxWindowSwitchOptionSequence(options);
  if (explicit > 0) {
    tmuxWindowSwitchSequences.set(key, explicit);
    return explicit;
  }
  if (options.bumpSequence === false) return tmuxWindowSwitchSequence(key);
  return nextTmuxWindowSwitchSequence(key);
}

function updateTmuxWindowBarActiveButtons(session, windowIndex) {
  const indexKey = windowIndex === null ? null : tmuxWindowIndexKey(windowIndex);
  if (indexKey === null && windowIndex !== null) return false;
  let matched = false;
  document.body?.querySelectorAll?.(`[data-tmux-window-bar="${cssEscape(session)}"]`)?.forEach(bar => {
    bar.querySelectorAll?.('[data-window-index]')?.forEach(button => {
      const active = indexKey !== null && tmuxWindowIndexKey(button.dataset.windowIndex) === indexKey;
      matched = matched || active;
      button.classList.toggle(CLS.active, active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  });
  return matched;
}

function refreshTabberPanelsForTmuxWindowChange() {
  if (fileExplorerMode === 'tabber' && itemIsActivePaneTab(fileExplorerItemId) && typeof refreshTabberPanels === 'function') refreshTabberPanels();
}

function tmuxWindowInfoWithActiveIndex(info, windowIndex) {
  const activeIndex = tmuxWindowIndexKey(windowIndex);
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  if (!info || activeIndex === null || !panes.length) return null;
  let selectedPane = null;
  let matched = false;
  const nextPanes = panes.map(pane => {
    const active = tmuxWindowIndexKey(pane.window ?? pane.window_index) === activeIndex;
    if (active) {
      matched = true;
      if (!selectedPane || pane.active === true) selectedPane = {...pane, window_active: true};
    }
    return {...pane, window_active: active};
  });
  if (!matched) return null;
  return {...info, selected_pane: selectedPane || info.selected_pane || nextPanes.find(pane => pane.window_active) || null, panes: nextPanes};
}

function applyTmuxWindowActiveIndexToTranscriptInfo(session, windowIndex, options = {}) {
  const info = transcriptMeta.sessions?.[session];
  const nextInfo = tmuxWindowInfoWithActiveIndex(info, windowIndex);
  if (!nextInfo) return false;
  transcriptMeta = {
    ...transcriptMeta,
    sessions: {...(transcriptMeta.sessions || {}), [session]: nextInfo},
  };
  if (options.render === true) {
    updatePanelHeader(session, nextInfo);
    renderInfoPanel();
  }
  return true;
}

function sessionMetadataIsLightweight(info) {
  if (!info || typeof info !== 'object') return false;
  if (info.metadata_loading === true) return true;
  const project = info.project;
  return Boolean(project && typeof project === 'object' && project.loading === true);
}

function mergeSessionMetadataDuringLightweightRefresh(nextInfo, previousInfo) {
  if (!sessionMetadataIsLightweight(nextInfo) || !previousInfo || typeof previousInfo !== 'object') return nextInfo;
  return {
    ...nextInfo,
    project: previousInfo.project || nextInfo.project,
    window_metadata: Array.isArray(previousInfo.window_metadata) && previousInfo.window_metadata.length
      ? previousInfo.window_metadata
      : nextInfo.window_metadata,
    metadata_loading: true,
  };
}

function transcriptPayloadWithPriorSessionMetadata(payload, previousPayload = transcriptMeta) {
  if (!payload || typeof payload !== 'object' || !(payload.sessions && typeof payload.sessions === 'object')) return payload;
  const previousSessions = previousPayload?.sessions && typeof previousPayload.sessions === 'object' ? previousPayload.sessions : {};
  let nextSessions = null;
  for (const [session, nextInfo] of Object.entries(payload.sessions)) {
    const merged = mergeSessionMetadataDuringLightweightRefresh(nextInfo, previousSessions[session]);
    if (merged === nextInfo) continue;
    if (!nextSessions) nextSessions = {...payload.sessions};
    nextSessions[session] = merged;
  }
  const previousIndexedRepos = Array.isArray(previousPayload?.indexed_repos) ? previousPayload.indexed_repos : null;
  const preserveIndexedRepos = payload.metadata_loading === true && previousIndexedRepos && !payload.indexed_repos?.length;
  if (!nextSessions && !preserveIndexedRepos) return payload;
  return {
    ...payload,
    ...(nextSessions ? {sessions: nextSessions} : {}),
    ...(preserveIndexedRepos ? {indexed_repos: previousIndexedRepos} : {}),
  };
}

function transcriptPayloadWithTmuxWindowOverrides(payload) {
  payload = transcriptPayloadWithPriorSessionMetadata(payload);
  if (!payload || typeof payload !== 'object' || !(payload.sessions && typeof payload.sessions === 'object')) return payload;
  let nextSessions = null;
  for (const session of Object.keys(payload.sessions)) {
    const override = tmuxWindowDisplayActiveIndex(session);
    const activeIndex = override === tmuxWindowPendingActiveIndex
      ? null
      : override !== undefined
        ? override
        : tmuxWindowActiveIndexFromSignals(session);
    if (activeIndex === null) continue;
    const nextInfo = tmuxWindowInfoWithActiveIndex(payload.sessions[session], activeIndex);
    if (!nextInfo) continue;
    if (!nextSessions) nextSessions = {...payload.sessions};
    nextSessions[session] = nextInfo;
  }
  return nextSessions ? {...payload, sessions: nextSessions} : payload;
}

function tmuxWindowActiveIndexFromSignals(session) {
  if (typeof tmuxSignalWindowsForSession !== 'function') return null;
  const activeWindow = tmuxSignalWindowsForSession(session).find(windowRecord => windowRecord?.active === true);
  return tmuxWindowIndexKey(activeWindow?.window_index);
}

function setTmuxWindowActiveIndexOverride(session, windowIndex, options = {}) {
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (!session || indexKey === null) return false;
  const sequence = tmuxWindowOverrideSequence(session, options);
  tmuxWindowActiveIndexOverrides.set(String(session), indexKey);
  setTmuxWindowDirectTargetGuard(session, indexKey, sequence);
  applyTmuxWindowActiveIndexToTranscriptInfo(String(session), indexKey, {render: true});
  updateTmuxWindowBarActiveButtons(session, indexKey);
  refreshTabberPanelsForTmuxWindowChange();
  return sequence || true;
}

function setTmuxWindowActiveIndexPending(session, options = {}) {
  if (!session) return false;
  const sequence = tmuxWindowOverrideSequence(session, options);
  tmuxWindowActiveIndexOverrides.set(String(session), tmuxWindowPendingActiveIndex);
  clearTmuxWindowDirectTargetGuard(session);
  updateTmuxWindowBarActiveButtons(session, null);
  refreshTabberPanelsForTmuxWindowChange();
  return sequence || true;
}

function clearTmuxWindowActiveIndexOverride(session, options = {}) {
  if (!session) return false;
  const sequence = tmuxWindowSwitchOptionSequence(options);
  if (sequence > 0 && !tmuxWindowSwitchSequenceMatches(session, sequence)) return false;
  const deleted = tmuxWindowActiveIndexOverrides.delete(String(session));
  if (options.clearDirectTarget === true) clearTmuxWindowDirectTargetGuard(session, {sequence});
  if (deleted) refreshTabberPanelsForTmuxWindowChange();
  return deleted;
}

function confirmTmuxWindowActiveIndexOverride(session, windowIndex, options = {}) {
  const indexKey = tmuxWindowIndexKey(windowIndex);
  if (!session || indexKey === null) return false;
  const sequence = tmuxWindowSwitchOptionSequence(options);
  if (sequence > 0 && !tmuxWindowSwitchSequenceMatches(session, sequence)) return false;
  tmuxWindowActiveIndexOverrides.set(String(session), indexKey);
  confirmTmuxWindowDirectTargetGuard(session, indexKey, {sequence});
  updateTmuxWindowBarActiveButtons(session, indexKey);
  refreshTabberPanelsForTmuxWindowChange();
  setTimeout(() => {
    if (sequence > 0 && !tmuxWindowSwitchSequenceMatches(session, sequence)) return;
    if (tmuxWindowActiveIndexOverride(session) !== indexKey) return;
    if (tmuxWindowInfoActiveIndex(transcriptMeta.sessions?.[session]) === indexKey) {
      clearTmuxWindowActiveIndexOverride(session, {sequence});
    }
  }, tmuxWindowConfirmedOverrideHoldMs);
  return true;
}

function tmuxWindowInfoActiveIndex(info) {
  const active = tmuxWindowRecords(Array.isArray(info) ? info : info?.panes).find(record => record.active === true);
  return active ? String(active.index) : null;
}

function tmuxWindowCurrentActiveIndex(session, info) {
  const override = session ? tmuxWindowDisplayActiveIndex(session) : undefined;
  if (override !== undefined) {
    return override === tmuxWindowPendingActiveIndex ? null : tmuxWindowIndexKey(override);
  }
  const active = tmuxWindowInfoActiveIndex(info);
  if (active !== null) return active;
  const pane = info?.selected_pane || null;
  return tmuxWindowIndexKey(pane?.window ?? pane?.window_index);
}

function reconcileTmuxWindowActiveIndexOverride(session, info, options = {}) {
  const sequence = tmuxWindowSwitchOptionSequence(options);
  if (sequence > 0 && !tmuxWindowSwitchSequenceMatches(session, sequence)) return false;
  const override = tmuxWindowActiveIndexOverride(session);
  if (override === undefined) return false;
  const activeIndex = tmuxWindowInfoActiveIndex(info);
  if (override === tmuxWindowPendingActiveIndex) {
    if (activeIndex !== null) {
      confirmTmuxWindowActiveIndexOverride(session, activeIndex, {sequence});
      return true;
    }
    updateTmuxWindowBarActiveButtons(session, null);
    return false;
  }
  const expected = tmuxWindowIndexKey(options.expectedIndex);
  const target = expected === null ? override : expected;
  if (activeIndex === target) {
    confirmTmuxWindowActiveIndexOverride(session, activeIndex, {sequence});
    return true;
  }
  updateTmuxWindowBarActiveButtons(session, override);
  return false;
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

const tmuxWindowBarNumericFallbackCount = 8;
const tmuxWindowBarNamedCharLimit = 80;

function tmuxWindowDisplayName(pane) {
  const process = String(pane?.process_label || '').trim();
  if (process) return process;
  const name = String(pane?.window_name || '').trim();
  if (name) return name;
  const inferred = processLabelFromCommand(pane?.command || '');
  return String(inferred || '').trim() || `window ${pane?.window ?? ''}`.trim() || 'window';
}

function tmuxWindowProcessPid(pane) {
  const value = Number(pane?.process_label_pid || pane?.pid || 0);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : null;
}

function tmuxWindowPidText(pid) {
  const value = Number(pid);
  return Number.isFinite(value) && value > 0 ? `(pid=${Math.floor(value)})` : '';
}

function tmuxWindowDisplayLabel(name, pid) {
  const pidText = tmuxWindowPidText(pid);
  return pidText ? `${name} ${pidText}` : name;
}

function tmuxWindowRecords(panes) {
  const byIndex = new Map();
  for (const pane of Array.isArray(panes) ? panes : []) {
    const index = tmuxWindowNumber(pane.window);
    if (index === null) continue;
    const indexText = String(pane.window);
    const existing = byIndex.get(index);
    const active = pane.window_active === true;
    const record = existing || {index, indexText, name: tmuxWindowDisplayName(pane), pid: tmuxWindowProcessPid(pane), active: false};
    if (!existing || active || !record.name) {
      record.name = tmuxWindowDisplayName(pane);
      record.pid = tmuxWindowProcessPid(pane);
    }
    record.active = record.active || active;
    byIndex.set(index, record);
  }
  const records = [...byIndex.values()].sort((left, right) => left.index - right.index);
  const nameCounts = new Map();
  records.forEach(record => {
    nameCounts.set(record.name, (nameCounts.get(record.name) || 0) + 1);
  });
  return records.map(record => ({
    ...record,
    buttonNameLabel: nameCounts.get(record.name) > 1 ? `${record.name}(${record.indexText})` : record.name,
    processLabel: tmuxWindowDisplayLabel(record.name, record.pid),
    nameLabel: tmuxWindowDisplayLabel(nameCounts.get(record.name) > 1 ? `${record.name}(${record.indexText})` : record.name, record.pid),
    numberLabel: record.indexText,
  })).map(record => ({
    ...record,
    indexedButtonLabel: `${record.indexText}:${record.name}`,
    indexedNameLabel: `${record.indexText}:${tmuxWindowDisplayLabel(record.name, record.pid)}`,
  }));
}

function tmuxWindowBarLabelMode(records, options = {}) {
  if (options.labelMode === 'numbers' || options.labelMode === 'names') return options.labelMode;
  const items = Array.isArray(records) ? records : [];
  const fallbackCount = Number.isFinite(options.fallbackCount) ? options.fallbackCount : tmuxWindowBarNumericFallbackCount;
  const charLimit = Number.isFinite(options.namedCharLimit) ? options.namedCharLimit : tmuxWindowBarNamedCharLimit;
  const namedChars = items.reduce((total, item) => total + String(item.indexedButtonLabel || item.buttonNameLabel || '').length, 0);
  return items.length > fallbackCount || namedChars > charLimit ? 'numbers' : 'names';
}

function tmuxWindowButtonHtml(options = {}) {
  const tag = options.tag || 'button';
  const visibleName = String(options.visibleName || '').trim();
  if (!visibleName) return '';
  const active = options.active === true;
  const classes = [
    'tab',
    'tmux-window-button',
    ...(Array.isArray(options.classes) ? options.classes : []),
    active ? 'active' : '',
  ].filter(Boolean).join(' ');
  const title = String(options.title || visibleName);
  const disabled = options.disabled === true;
  const disabledTitle = String(options.disabledTitle || title);
  const attrs = [`class="${esc(classes)}"`];
  if (tag === 'button') attrs.unshift('type="button"');
  if (disabled) {
    attrs.push('disabled', `title="${esc(disabledTitle)}"`, `aria-label="${esc(title)}"`);
  } else {
    if (Array.isArray(options.attrs)) attrs.push(...options.attrs.filter(Boolean));
    attrs.push(`title="${esc(title)}"`, `aria-label="${esc(title)}"`);
    if (options.ariaPressed !== false) attrs.push(`aria-pressed="${active ? 'true' : 'false'}"`);
  }
  const agentStatus = options.agentStatus || null;
  const activityIconHtml = options.activityIconHtml !== undefined
    ? String(options.activityIconHtml || '')
    : agentWindowActivityIconHtmlForStatus(agentStatus, options.agentKey, options.session || '', {animate: options.activityAnimate !== false, reserveStatusSlot: true, statusBeforeAgent: true});
  const labelHtml = options.labelHtml !== undefined ? String(options.labelHtml || '') : esc(visibleName);
  const numberLabel = String(options.numberLabel || options.indexText || visibleName);
  const numberHtml = options.showNumberLabel === false ? '' : `<span class="tmux-window-number-label">${esc(numberLabel)}</span>`;
  return `<${tag} ${attrs.join(' ')}><span class="tmux-window-name-label">${activityIconHtml}<span class="tmux-window-name-text">${labelHtml}</span></span>${numberHtml}</${tag}>`;
}

function tmuxWindowAgentKey(name) {
  const base = String(name || '').trim().toLowerCase().replace(/\(\d+\)$/, '').split(/[\s:/]/)[0];
  if (base === 'claude' || base === 'codex') return base;
  if (['bash', 'sh', 'zsh', 'fish', 'shell', '-bash', '-zsh'].includes(base)) return 'shell';
  if (['vim', 'nvim', 'vi', 'nano', 'emacs', 'hx', 'helix'].includes(base)) return 'editor';
  if (['python', 'python3', 'ipython', 'node', 'ruby', 'irb', 'bun', 'deno'].includes(base)) return 'repl';
  if (['git', 'lazygit', 'tig', 'gh'].includes(base)) return 'git';
  return 'other';
}

function tmuxWindowBarPanes(session, info) {
  const panes = Array.isArray(info) ? info : info?.panes;
  const result = Array.isArray(panes) ? [...panes] : [];
  const knownWindows = new Set(result.map(pane => tmuxWindowIndexKey(pane?.window ?? pane?.window_index)).filter(index => index !== null));
  if (typeof tmuxSignalWindowsForSession !== 'function') return result;
  for (const windowRecord of tmuxSignalWindowsForSession(session)) {
    const windowIndex = tmuxWindowIndexKey(windowRecord?.window_index);
    if (windowIndex === null || knownWindows.has(windowIndex)) continue;
    const activePane = (windowRecord.panes || []).find(pane => pane?.active === true) || windowRecord.panes?.[0] || {};
    result.push({
      window: windowIndex,
      window_name: windowRecord.window_name || activePane.current_command || `window ${windowIndex}`,
      pane: activePane.pane_index ?? '',
      pane_id: activePane.pane_id || activePane.target || '',
      target: activePane.target || activePane.pane_id || '',
      current_path: activePane.current_path || '',
      command: activePane.current_command || '',
      active: activePane.active === true,
      window_active: windowRecord.active === true,
    });
    knownWindows.add(windowIndex);
  }
  return result;
}

function tmuxWindowBarHtml(session, info, options = {}) {
  const panes = tmuxWindowBarPanes(session, info);
  const records = tmuxWindowRecords(panes);
  if (!records.length) return '';
  const disabled = options.disabled === true || readOnlyMode;
  const activeIndexOverride = tmuxWindowDisplayActiveIndex(session);
  const labelMode = tmuxWindowBarLabelMode(records, options.infoBar === true && !options.labelMode ? {...options, labelMode: 'names'} : options);
  const disabledTitle = readOnlyMode ? t('terminal.window.adminRequired') : t('tab.unavailableFor', {name: itemLabel(session)});
  const contextAttr = options.infoBar === true ? ' data-tmux-window-bar-context="info-bar"' : '';
  const buttons = records.map(record => {
    const infoPayload = Array.isArray(info) ? {panes: info} : info;
    const fallbackName = record.indexedButtonLabel || `${record.indexText}:${record.buttonNameLabel || record.nameLabel}`;
    const visibleName = tmuxWindowCanonicalLabel(session, record, fallbackName, infoPayload);
    const {status: agentStatus, agentKey} = tmuxWindowAgentStatus(session, record, infoPayload);
    const agentCurrent = typeof agentWindowPayloadCurrent === 'function' ? agentWindowPayloadCurrent(agentStatus) : null;
    const recordActive = agentCurrent === null ? record.active : agentCurrent === true;
    const active = activeIndexOverride === undefined ? recordActive : String(record.index) === activeIndexOverride;
    const title = t('terminal.window.title', {name: visibleName});
    return tmuxWindowButtonHtml({
      session,
      visibleName,
      numberLabel: record.numberLabel,
      active,
      agentStatus,
      agentKey,
      title,
      disabled,
      disabledTitle,
      attrs: [
        `data-window-index="${esc(record.indexText)}"`,
        `data-window-session="${esc(session)}"`,
        `data-window-label="${esc(visibleName)}"`,
      ],
    });
  }).join('');
  return `<div class="tmux-window-bar" data-tmux-window-bar="${esc(session)}"${contextAttr} data-tmux-window-label-mode="${esc(labelMode)}" role="group" aria-label="${esc(t('terminal.window.groupAria'))}">${buttons}</div>`;
}

function handleWindowStepButtonClick(event) {
  const button = windowStepButtonFromEvent(event);
  if (!button) return;
  if (button.dataset.windowIndex !== undefined) {
    const label = button.dataset.windowLabel || button.dataset.windowIndex;
    if (typeof acknowledgeTerminalAttentionFromUserAction === 'function') {
      acknowledgeTerminalAttentionFromUserAction(button.dataset.windowSession, button.dataset.windowIndex);
    } else if (typeof acknowledgeAgentWindowActivity === 'function') {
      acknowledgeAgentWindowActivity(button.dataset.windowSession, button.dataset.windowIndex, {delayMs: agentWindowActivityAcknowledgeDelayMs});
    }
    tmuxWindow(button.dataset.windowSession, {windowIndex: button.dataset.windowIndex}, `tmux sub-window ${label}`);
    return;
  }
  const key = button.dataset.windowDir === 'prev' ? 'p' : 'n';
  const label = button.dataset.windowDir === 'prev' ? 'previous tmux sub-window' : 'next tmux sub-window';
  tmuxWindow(button.dataset.windowSession, key, label);
}

function windowStepButtonFromEvent(event) {
  const current = event?.currentTarget;
  if (current?.matches?.('[data-window-dir]')) return current;
  if (current?.matches?.('[data-window-index]')) return current;
  return event?.target?.closest?.('[data-window-index]') || event?.target?.closest?.('[data-window-dir]') || null;
}

function syncTmuxWindowBarOverflow(session) {
  document.body?.querySelectorAll?.(`[data-tmux-window-bar="${cssEscape(session)}"]`)?.forEach(bar => {
    if (bar?.dataset?.tmuxWindowBarContext === 'info-bar') return;
    if (!bar || bar.dataset.tmuxWindowLabelMode === 'numbers') return;
    if (bar.scrollWidth > bar.clientWidth + 1) bar.dataset.tmuxWindowLabelMode = 'numbers';
  });
}

function updatePanelWindowStepButtons(session, info) {
  const panel = document.getElementById(panelDomId(session));
  const barSelector = `[data-tmux-window-bar="${cssEscape(session)}"]`;
  const bars = [...new Set([
    ...(document.body?.querySelectorAll?.(barSelector) || []),
    ...(panel?.querySelectorAll?.(barSelector) || []),
  ])];
  const html = tmuxWindowBarHtml(session, info, {infoBar: true});
  let changed = false;
  if (!html) {
    bars.forEach(bar => {
      bar.remove();
      changed = true;
    });
    syncTmuxWindowBarOverflow(session);
    if (changed) schedulePaneInfoBarMetaOverflowSync(panel);
    return;
  }
  const replacementFromHtml = () => {
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    return wrapper.firstElementChild;
  };
  const insertBarIntoDetailRow = () => {
    const row = panel?.querySelector(':scope > .pane-info-bar') || panel?.querySelector('.pane-info-bar') || panel?.querySelector(':scope > .panel-detail-row') || panel?.querySelector('.panel-detail-row');
    if (!row) return false;
    const replacement = replacementFromHtml();
    if (!replacement) return false;
    const close = row.querySelector(':scope > .panel-detail-close') || row.querySelector('.panel-detail-close');
    if (close?.parentElement === row) row.insertBefore(replacement, close);
    else row.appendChild(replacement);
    changed = true;
    return true;
  };
  if (!bars.length) {
    insertBarIntoDetailRow();
    syncTmuxWindowBarOverflow(session);
    if (changed) scheduleAgentWindowActivityAnimationSync(panel || document);
    if (changed) schedulePaneInfoBarMetaOverflowSync(panel);
    return;
  }
  bars.forEach(existing => {
    if (existing.outerHTML === html) return;
    const replacement = replacementFromHtml();
    if (replacement) {
      existing.replaceWith(replacement);
      changed = true;
    }
  });
  syncTmuxWindowBarOverflow(session);
  if (changed) scheduleAgentWindowActivityAnimationSync(panel || document);
  if (changed) schedulePaneInfoBarMetaOverflowSync(panel);
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
    const title = terminalTabTitle(session, info);
    button.title = title;
    button.setAttribute('aria-label', title);
  }
}

function searchHistoryResults() {
  return Array.isArray(searchHistoryPayload?.results) ? searchHistoryPayload.results : [];
}

function runHistoryRows() {
  return Array.isArray(runHistoryPayload?.runs) ? runHistoryPayload.runs : [];
}

function searchHistoryTimestampSeconds(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value > 1e11 ? value / 1000 : value;
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function searchHistoryTimeLabel(value, fallbackSeconds = 0) {
  const seconds = searchHistoryTimestampSeconds(value) || Number(fallbackSeconds || 0);
  return seconds ? localizedDateTimeFormat(seconds, {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}) : '';
}

function runHistoryAgentLabel(row) {
  const agent = row?.agent && typeof row.agent === 'object' ? row.agent : null;
  return [agent?.kind, agent?.model].filter(Boolean).join(' ');
}

function runHistoryPrLabel(row) {
  const pr = row?.pr && typeof row.pr === 'object' ? row.pr : null;
  if (!pr?.number) return '';
  return `${t('searchHistory.pr')} #${pr.number}${pr.state ? ` ${pr.state}` : ''}`;
}

function runHistoryMetaParts(row) {
  const started = searchHistoryTimeLabel(row?.started_at, row?.started_ts);
  const ended = searchHistoryTimeLabel(row?.ended_at, row?.ended_ts);
  return [
    row?.cwd ? compactHomePath(row.cwd) : '',
    runHistoryAgentLabel(row),
    started ? `${t('searchHistory.started')}: ${started}` : '',
    ended ? `${t('searchHistory.ended')}: ${ended}` : '',
    row?.final_state ? `${t('searchHistory.finalState')}: ${row.final_state}` : '',
    runHistoryPrLabel(row),
  ].filter(Boolean);
}

function searchHistoryResultHtml(result, index) {
  const target = result?.target && typeof result.target === 'object' ? result.target : {};
  const session = target.session || result?.session || '';
  const time = searchHistoryTimeLabel(result?.timestamp);
  const meta = [session, result?.kind || result?.source || '', time].filter(Boolean).join(' · ');
  return `<button type="button" class="search-history-result" data-search-result-index="${index}">
    <span class="search-history-row-meta">${esc(meta)}</span>
    <span class="search-history-row-title">${esc(result?.title || result?.kind || t('searchHistory.result'))}</span>
    <span class="search-history-row-snippet">${esc(result?.snippet || '')}</span>
  </button>`;
}

function searchHistoryResultsHtml() {
  const results = searchHistoryResults();
  if (searchHistoryLoading) return `<div class="search-history-empty">${esc(t('common.loading'))}</div>`;
  if (!searchHistoryQuery.trim()) return `<div class="search-history-empty">${esc(t('searchHistory.emptyQuery'))}</div>`;
  if (!results.length) return `<div class="search-history-empty">${esc(t('searchHistory.emptyResults'))}</div>`;
  return `<div class="search-history-list">${results.map(searchHistoryResultHtml).join('')}</div>`;
}

function runHistoryRowHtml(row) {
  const session = String(row?.session || '');
  const meta = runHistoryMetaParts(row);
  const disabled = sessions.includes(session) ? '' : ' disabled';
  const prompt = row?.prompt ? `<div class="search-history-row-field"><span>${esc(t('searchHistory.prompt'))}</span>${esc(row.prompt)}</div>` : '';
  const summary = row?.latest_summary ? `<div class="search-history-row-field"><span>${esc(t('searchHistory.summary'))}</span>${esc(row.latest_summary)}</div>` : '';
  return `<button type="button" class="search-history-run" data-run-history-session="${esc(session)}"${disabled}>
    <span class="search-history-row-title">${esc(session || t('common.unknown'))}</span>
    <span class="search-history-row-meta">${esc(meta.join(' · '))}</span>
    ${prompt}
    ${summary}
  </button>`;
}

function runHistoryRowsHtml() {
  const rows = runHistoryRows();
  if (runHistoryLoading && !rows.length) return `<div class="search-history-empty">${esc(t('common.loading'))}</div>`;
  if (!rows.length) return `<div class="search-history-empty">${esc(t('searchHistory.emptyRuns'))}</div>`;
  return `<div class="search-history-list">${rows.map(runHistoryRowHtml).join('')}</div>`;
}

function searchHistoryPanelStatusText() {
  if (searchHistoryLoading || runHistoryLoading) return t('common.loading');
  if (searchHistoryError || runHistoryError) return searchHistoryError || runHistoryError;
  return t('searchHistory.detail');
}

function searchHistoryPanelHtml() {
  const errors = [searchHistoryError, runHistoryError].filter(Boolean);
  const errorHtml = errors.length ? `<div class="search-history-error">${esc(errors.join(' · '))}</div>` : '';
  return `
    <form class="search-history-search" data-search-history-form>
      <input class="search-history-input" data-search-history-query value="${esc(searchHistoryQuery)}" placeholder="${esc(t('searchHistory.query.placeholder'))}" aria-label="${esc(t('searchHistory.query.placeholder'))}">
      <button type="submit" class="preferences-search-button">${esc(t('searchHistory.search'))}</button>
      <button type="button" class="preferences-reset-all" data-run-history-refresh>${esc(t('searchHistory.refresh'))}</button>
    </form>
    ${errorHtml}
    <section class="search-history-section" aria-label="${esc(t('searchHistory.results'))}">
      <div class="search-history-section-head">${esc(t('searchHistory.results'))}</div>
      ${searchHistoryResultsHtml()}
    </section>
    <section class="search-history-section" aria-label="${esc(t('searchHistory.runHistory'))}">
      <div class="search-history-section-head">${esc(t('searchHistory.runHistory'))}</div>
      ${runHistoryRowsHtml()}
    </section>`;
}

function renderSearchHistoryPanel(panel = document.getElementById(panelDomId(searchHistoryItemId))) {
  if (!panel) return;
  const meta = panel.querySelector(`#meta-${cssEscape(searchHistoryItemId)}`);
  if (meta) meta.textContent = searchHistoryPanelStatusText();
  const scroll = panel.querySelector('[data-search-history-scroll]');
  if (scroll) scroll.innerHTML = searchHistoryPanelHtml();
}

function renderSearchHistoryPanels() {
  document.querySelectorAll('.search-history-panel').forEach(panel => renderSearchHistoryPanel(panel));
}

async function refreshRunHistoryData() {
  runHistoryLoading = true;
  runHistoryError = '';
  renderSearchHistoryPanels();
  try {
    runHistoryPayload = await apiFetchJson('/api/run-history', {cache: 'no-store'});
  } catch (error) {
    runHistoryError = String(error?.payload?.error || error?.message || error);
  } finally {
    runHistoryLoading = false;
    renderSearchHistoryPanels();
  }
  return runHistoryPayload;
}

async function runSearchHistoryQuery(query = searchHistoryQuery) {
  searchHistoryQuery = String(query || '').trim();
  searchHistoryError = '';
  if (!searchHistoryQuery) {
    searchHistoryPayload = {query: '', results: []};
    renderSearchHistoryPanels();
    return searchHistoryPayload;
  }
  searchHistoryLoading = true;
  renderSearchHistoryPanels();
  try {
    searchHistoryPayload = await apiFetchJson(`/api/search?q=${encodeURIComponent(searchHistoryQuery)}`, {cache: 'no-store'});
  } catch (error) {
    searchHistoryError = String(error?.payload?.error || error?.message || error);
  } finally {
    searchHistoryLoading = false;
    renderSearchHistoryPanels();
  }
  return searchHistoryPayload;
}

async function loadSearchHistoryPanelData(options = {}) {
  renderSearchHistoryPanels();
  await refreshRunHistoryData();
  const query = options.query == null ? searchHistoryQuery : String(options.query || '');
  if (query.trim()) await runSearchHistoryQuery(query);
  else renderSearchHistoryPanels();
}

async function openSearchHistoryResult(index) {
  const result = searchHistoryResults()[Number(index)];
  const target = result?.target && typeof result.target === 'object' ? result.target : {};
  if (target.type === 'activity-summary' || target.tab === 'yoagent') {
    await selectSession(yoagentItemId, {userInitiated: true});
    activateYoagentPanel({focusChat: true});
    return;
  }
  const session = String(target.session || result?.session || '');
  if (!session || !sessions.includes(session)) {
    statusErr(localizedHtml('searchHistory.sessionUnavailable', {session: session || t('common.unknown')}));
    return;
  }
  await selectSession(session, {userInitiated: true});
  const tab = String(target.tab || '');
  if (['terminal', 'transcript', 'summary', 'events'].includes(tab)) activateTab(session, tab, {userInitiated: true});
}

async function openRunHistorySession(session) {
  const name = String(session || '');
  if (!name || !sessions.includes(name)) {
    statusErr(localizedHtml('searchHistory.sessionUnavailable', {session: name || t('common.unknown')}));
    return;
  }
  await selectSession(name, {userInitiated: true});
}

function bindSearchHistoryPanel(panel) {
  if (!panel || panel.dataset.searchHistoryBound === 'true') return;
  panel.dataset.searchHistoryBound = 'true';
  panel.addEventListener('submit', event => {
    const form = event.target.closest('[data-search-history-form]');
    if (!form || !panel.contains(form)) return;
    event.preventDefault();
    runSearchHistoryQuery(form.querySelector('[data-search-history-query]')?.value || '');
  });
  panel.addEventListener('click', event => {
    const refresh = event.target.closest('[data-run-history-refresh]');
    if (refresh && panel.contains(refresh)) {
      event.preventDefault();
      loadSearchHistoryPanelData({query: searchHistoryQuery});
      return;
    }
    const result = event.target.closest('[data-search-result-index]');
    if (result && panel.contains(result)) {
      event.preventDefault();
      openSearchHistoryResult(result.dataset.searchResultIndex);
      return;
    }
    const run = event.target.closest('[data-run-history-session]');
    if (run && panel.contains(run)) {
      event.preventDefault();
      openRunHistorySession(run.dataset.runHistorySession);
    }
  });
}

function createSearchHistoryPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel search-history-panel';
  panel.id = panelDomId(searchHistoryItemId);
  panel.innerHTML = `
      <div class="panel-head search-history-panel-head">
        ${virtualPanelControlsHtml(searchHistoryItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-copy panel-copy">
          <div id="panel-tab-${searchHistoryItemId}" class="panel-session-label"><span class="session-button-dir">${esc(searchHistoryTabLabel())}</span></div>
          <div id="meta-${searchHistoryItemId}" class="pane-info-bar-meta meta">${esc(searchHistoryPanelStatusText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(searchHistoryItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="search-history-body info-pane panel-overlay-root">
        <div id="panel-toasts-${searchHistoryItemId}" class="panel-toast-stack"></div>
        <div class="search-history-scroll info-list" data-search-history-scroll>${searchHistoryPanelHtml()}</div>
      </div>`;
  bindPanelShell(panel, searchHistoryItemId);
  bindSearchHistoryPanel(panel);
  return panel;
}

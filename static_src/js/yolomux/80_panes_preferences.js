// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
function renderPanels(previousActive = [], options = {}) {
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
  handle.setAttribute('aria-label', 'Resize panes');
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
    : (direction === 'column' ? minSplitPaneHeightPx : minSplitPaneWidthPx);
  const minSecond = children
    ? (direction === 'column' ? layoutNodeMinHeight(children[1]) : layoutNodeMinWidth(children[1]))
    : (direction === 'column' ? minSplitPaneHeightPx : minSplitPaneWidthPx);
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
  return minSplitPaneWidthPx;
}

function minWidthForLayoutSlot(slot, slots = layoutSlots) {
  return minWidthForLayoutItem(activeItemForSide(slot, slots));
}

function layoutVisiblePaneCount(slots = layoutSlots) {
  return layoutSlotKeys(slots).filter(slot => paneHasLayoutContent(slot, slots)).length;
}

function layoutNodeMinWidth(node, slots = layoutSlots) {
  if (!node) return minSplitPaneWidthPx;
  if (node.slot) return paneHasLayoutContent(node.slot, slots) ? minWidthForLayoutSlot(node.slot, slots) : minSplitPaneWidthPx;
  const children = node.children || [];
  if (node.split === 'column') return Math.max(...children.map(child => layoutNodeMinWidth(child, slots)), minSplitPaneWidthPx);
  return children.reduce((sum, child) => sum + layoutNodeMinWidth(child, slots), 0);
}

function layoutNodeMinHeight(node, slots = layoutSlots) {
  if (!node) return minSplitPaneHeightPx;
  if (node.slot) return paneHasLayoutContent(node.slot, slots) ? minSplitPaneHeightPx : minSplitPaneHeightPx;
  const children = node.children || [];
  if (node.split === 'column') return children.reduce((sum, child) => sum + layoutNodeMinHeight(child, slots), 0);
  return Math.max(...children.map(child => layoutNodeMinHeight(child, slots)), minSplitPaneHeightPx);
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
    const tooSmall = rect.width < minWidthForLayoutSlot(slot) || rect.height < minSplitPaneHeightPx;
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
  if (isChangesItem(session)) column.classList.add('changes-column');
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
  return node;
}

function renderEmptyPane(slot) {
  const panel = document.createElement('article');
  panel.className = 'panel empty-pane-panel';
  panel.dataset.slot = slot;
  panel.setAttribute('aria-label', 'Empty pane');
  panel.appendChild(document.createElement('div'));
  panel.children[0].className = 'empty-pane-fill';
  return panel;
}

function renderPaneTabStrips() {
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
  reconcilePaneTabChildren(strip, side, items);
  bindPaneTabStrip(strip, side);
  restorePaneTabPopover(strip, restorePopoverItem);
  scheduleTabStripOverflowCheck(strip);
}

function reconcilePaneTabChildren(strip, side, items) {
  const existingByItem = new Map(Array.from(strip.querySelectorAll(':scope > .pane-tab')).map(tab => [tab.dataset.paneTab || '', tab]));
  const nextNodes = [];
  for (const item of items) {
    const fresh = createPaneTab(side, item);
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

function createPaneTab(side, item) {
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
  if (type?.rowHtml) tab.innerHTML = type.rowHtml(item);
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
  tab.setAttribute('aria-label', type ? itemLabel(item) : isEditor ? `${itemLabel(item)}${missingFileClass ? ' missing on disk' : ''}` : `${sessionLabel(item)} ${sessionWorkDescription(item, info, 140)}`.trim());
  tab.addEventListener('pointerdown', event => {
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
    statusEl.innerHTML = '<span class="err">readonly access cannot rename files</span>';
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
  bindDelayedSessionPopover(tab, popover, () => positionPaneTabPopover(tab));
}

function bindDelayedSessionPopover(anchor, popover, position) {
  createHoverPopover({
    anchor,
    popover,
    showDelay: () => (document.querySelector('.pane-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
    hideDelay: () => popoverHideDelayMs,
    canOpen: () => !appMenuIsOpen() && !topbar?.matches?.(':hover'),
    onQueue: position,
    position,
    closeOthers: () => closeOtherSessionPopovers(anchor),
  });
}

function positionPaneTabPopover(tab) {
  const rect = tab.getBoundingClientRect();
  const popover = tab.querySelector?.(':scope > .session-popover');
  const bridgeGap = 3;
  const edgeGap = popoverEdgeGapPx();
  const topbarBottom = Math.ceil(topbar?.getBoundingClientRect?.().bottom || rootCssLengthPx('--topbar-height') || 0);
  const width = Math.ceil(popover?.getBoundingClientRect?.().width || rect.width || 0);
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
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="pane-tab-info-label">${esc(itemLabel(item))}</span></span>`;
}

function fileExplorerPaneTabHtml(item = fileExplorerItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">${esc(fileExplorerLabel())}</span></span>`;
}

function preferencesPaneTabHtml(item = prefsItemId, options = {}) {
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">Preferences</span></span>`;
}

function changesPaneTabHtml(item = changesItemId, options = {}) {
  const count = sessionFilesPayload.files?.length || 0;
  const badge = count ? `<span class="session-state-badge changes-count-badge">${count}</span>` : '';
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-dir">Changes</span>${badge}</span>`;
}

function fileEditorPaneTabHtml(item, options = {}) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const owners = openFileOwnerSessionsForPath(path);
  const ownerTitle = owners.length > 1 ? `multiple owning sessions: ${owners.join(', ')}` : owners[0] ? `owning session: ${owners[0]}` : '';
  const ownerText = owners.length > 1 ? 'multi' : owners[0] || '';
  const owner = ownerText ? `<span class="file-tab-owner" title="${esc(ownerTitle)}">${esc(ownerText)}</span>` : '';
  const dirty = state.dirty ? '<span class="file-tab-dirty" title="modified" aria-label="modified"></span>' : '';
  const missing = openFileIsMissing(path) ? '<span class="file-tab-missing-badge" title="missing on disk" aria-label="missing on disk">missing</span>' : '';
  const kind = isFilePreviewItem(item) ? '<span class="file-tab-kind" title="preview only">Preview</span>' : '';
  return `<span class="pane-tab-core">${tabTypeIconHtml(item, options)}<span class="session-button-text">${owner}${dirty}${missing}${kind}<span class="session-button-dir">${esc(basenameOf(path))}</span></span></span>`;
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
      if (slotIsFileExplorerPane(side) || slotIsChangesPane(side)) {
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
    if (slotIsFileExplorerPane(side) || slotIsChangesPane(side)) {
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
      if (slotIsFileExplorerPane(side) || slotIsChangesPane(side)) return;
      openDraggedFilesInEditor(filePayload, {targetSlot: side, targetIndex: paneTabDropIndex(strip, event, '')});
      return;
    }
    const payload = dragPayload(event);
    clearPaneTabDropPreview(strip);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (slotIsFileExplorerPane(side) || slotIsChangesPane(side)) return;
    moveSessionToSlot(payload.session, side, payload.sourceSlot || slotForSession(payload.session), paneTabDropIndex(strip, event, payload.session));
  };
}

function showPaneTabDropPreview(strip, event, movingSession) {
  const placement = paneTabDropPlacement(strip, event, movingSession);
  strip.style.setProperty('--tab-drop-x', `${Math.round(placement.x)}px`);
  strip.style.setProperty('--tab-drop-y', `${Math.round(placement.y)}px`);
  strip.style.setProperty('--tab-drop-height', `${Math.round(placement.height)}px`);
  strip.classList.add('drag-over', 'tab-drop-preview');
}

function clearPaneTabDropPreview(strip) {
  strip.classList.remove('drag-over', 'tab-drop-preview');
  strip.style.removeProperty('--tab-drop-x');
  strip.style.removeProperty('--tab-drop-y');
  strip.style.removeProperty('--tab-drop-height');
}

function paneTabDropPlacement(strip, event, movingSession) {
  const tabs = Array.from(strip.querySelectorAll('.pane-tab'))
    .filter(tab => tab.dataset.paneTab !== movingSession);
  const stripRect = strip.getBoundingClientRect();
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
  const rows = paneTabRows(tabs);
  const row = rows.reduce((best, item) => {
    const distance = Math.abs(event.clientY - item.centerY);
    return !best || distance < best.distance ? {row: item, distance} : best;
  }, null)?.row || rows[0];
  const rowTabs = row.items.slice().sort((left, right) => left.rect.left - right.rect.left);
  for (const item of rowTabs) {
    const rect = item.rect;
    if (event.clientX < rect.left + rect.width / 2) {
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

function paneTabRows(tabs) {
  const rows = [];
  tabs.forEach((tab, index) => {
    const rect = tab.getBoundingClientRect();
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
  installPanelInactiveOverlays(panel, session);
  bindPanelPopover(panel);
  bindPaneFrameControls(panel, session);
  panel.addEventListener('pointerenter', () => selectPanelOnHover(session));
  panel.addEventListener('pointerdown', event => {
    if (!isTmuxSession(session)) {
      setFocusedPanelItem(session, {
        focusPreferencesSearch: !preferenceFocusTargetIsInteractive(event.target),
      });
    }
  }, {capture: true});
  panel.addEventListener('focusin', event => {
    if (!isTmuxSession(session)) {
      setFocusedPanelItem(session, {
        focusPreferencesSearch: !preferenceFocusTargetIsInteractive(event.target),
      });
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
        if (slotIsFileExplorerPane(targetSlot) || slotIsChangesPane(targetSlot)) {
          event.dataTransfer.dropEffect = 'none';
          return;
        }
        event.dataTransfer.dropEffect = 'copy';
        head.classList.add('tab-drag-over');
        return;
      }
      const payload = dragPayload(event);
      if (!payload?.session) return;
      event.preventDefault();
      event.stopPropagation();
      clearDropPreview();
      if (event.target.closest('.pane-tabs')) return;
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (slotIsFileExplorerPane(targetSlot) || slotIsChangesPane(targetSlot)) {
        event.dataTransfer.dropEffect = 'none';
        return;
      }
      event.dataTransfer.dropEffect = 'move';
      head.classList.add('tab-drag-over');
    });
    head.addEventListener('dragleave', event => {
      if (!head.contains(event.relatedTarget)) head.classList.remove('tab-drag-over');
    });
    head.addEventListener('drop', event => {
      const filePayload = fileDragPayload(event);
      if (filePayload?.path && !event.target.closest('.pane-tabs')) {
        head.classList.remove('tab-drag-over');
        event.preventDefault();
        event.stopPropagation();
        const targetSlot = head.dataset.dragSlot || slotForSession(session);
        if (slotIsFileExplorerPane(targetSlot) || slotIsChangesPane(targetSlot)) return;
        if (targetSlot) openDraggedFilesInEditor(filePayload, {targetSlot});
        return;
      }
      const payload = dragPayload(event);
      head.classList.remove('tab-drag-over');
      if (!payload?.session || event.target.closest('.pane-tabs')) return;
      event.preventDefault();
      event.stopPropagation();
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (!targetSlot) return;
      if (slotIsFileExplorerPane(targetSlot) || slotIsChangesPane(targetSlot)) return;
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
    button.title = collapsed ? 'show details' : 'hide details';
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

function installPanelInactiveOverlays(panel, session) {
  if (isVirtualItem(session)) {
    panel.querySelectorAll('.panel-inactive-overlay').forEach(node => node.remove());
    return;
  }
  for (const root of panel.querySelectorAll('.panel-overlay-root')) {
    if (root.querySelector(':scope > .panel-inactive-overlay')) continue;
    const overlay = document.createElement('div');
    overlay.className = 'panel-inactive-overlay';
    overlay.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      focusPanel(session, {userInitiated: true});
    });
    root.appendChild(overlay);
  }
}

function createInfoPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel';
  panel.id = `panel-${infoItemId}`;
  panel.innerHTML = `
      <div class="panel-head">
        ${virtualPanelControlsHtml(infoItemId, infoTabLabel)}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${infoItemId}" class="panel-session-label"><span class="session-button-dir">${esc(infoTabLabel)}</span></div>
          <div id="meta-${infoItemId}" class="meta">Branches, PRs, CI, and repo metadata sorted by recent activity</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(infoItemId)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${infoItemId}" class="panel-toast-stack"></div>
        <div class="transcript-head info-head">
          <span>${esc(infoTabLabel)}</span>
          <button type="button" class="info-refresh" data-info-refresh title="Refresh repo metadata">Refresh repo metadata</button>
        </div>
        <div id="info-content" class="info-list"></div>
      </div>`;
  bindPanelShell(panel, infoItemId);
  panel.querySelector('[data-info-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
    refreshTranscripts();
  });
  renderInfoPanel();
  return panel;
}

function createYoagentPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel yoagent-panel';
  panel.id = `panel-${yoagentItemId}`;
  panel.innerHTML = `
      <div class="panel-head">
        ${virtualPanelControlsHtml(yoagentItemId, yoagentTabLabel)}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${yoagentItemId}" class="panel-session-label"><span class="session-button-dir">${esc(yoagentTabLabel)}</span></div>
          <div id="meta-${yoagentItemId}" class="meta">Activity roll-up of active AI agents and changed files</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(yoagentItemId)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${yoagentItemId}" class="panel-toast-stack"></div>
        <div class="transcript-head info-head">
          <span>${esc(yoagentTabLabel)}</span>
          <button type="button" class="info-refresh" data-yoagent-refresh title="Refresh AI activity summary">Refresh summary</button>
        </div>
        <div id="yoagent-content" class="info-list yoagent-list"></div>
      </div>`;
  bindPanelShell(panel, yoagentItemId);
  panel.querySelector('[data-yoagent-refresh]')?.addEventListener('click', event => {
    event.preventDefault();
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
    sendYoagentChatMessage(value);
  });
  panel.addEventListener('click', event => {
    const clear = event.target.closest('[data-yoagent-clear]');
    if (!clear || !panel.contains(clear)) return;
    event.preventDefault();
    clearYoagentConversation();
  });
  panel.addEventListener('input', event => {
    const input = event.target.closest('[data-yoagent-chat-input]');
    if (input && panel.contains(input)) yoagentDraft = input.value || '';
  });
  renderYoagentPanel();
  return panel;
}

function sessionActivitySummary(session) {
  return activitySummaryPayload?.sessions?.[session] || null;
}

function activitySummaryLinesHtml(lines, options = {}) {
  const items = Array.isArray(lines) ? lines.filter(Boolean) : [];
  if (!items.length) return options.empty ? `<div class="yoagent-empty">${esc(options.empty)}</div>` : '';
  return items.map(line => `<div class="yoagent-line">${esc(line)}</div>`).join('');
}

function relativeActivityGeneratedText(payload = activitySummaryPayload) {
  const ts = Number(payload?.generated_ts || 0) || Date.parse(payload?.generated_at || '') / 1000;
  if (!Number.isFinite(ts) || ts <= 0) return {text: 'not loaded', title: ''};
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  const text = seconds < 60
    ? 'last updated just now'
    : seconds < 3600
      ? `last updated ${Math.round(seconds / 60)} min ago`
      : `last updated ${Math.round(seconds / 3600)} hr ago`;
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
  const refreshBar = activitySummaryRefreshing ? '<div class="yoagent-refresh-progress" aria-label="Refreshing summary"></div>' : '';
  return `<section class="yoagent-global" aria-label="${esc(yoagentTabLabel)} AI activity summary">
    <div class="yoagent-global-head">
      <span>${esc(yoagentTabLabel)}</span>
      <span class="yoagent-generated" title="${esc(generated.title)}">(${esc(generated.text)})</span>
    </div>
    ${refreshBar}
    ${headline ? `<div class="yoagent-headline">${esc(headline)}</div>` : activitySummaryLinesHtml([], {empty: 'No AI agent activity detected yet.'})}
    ${activitySummaryLinesHtml(detailLines)}
  </section>`;
}

function yoagentSessionSummariesHtml() {
  const sessions = activitySummaryPayload?.sessions || {};
  const order = Array.isArray(activitySummaryPayload?.session_order) ? activitySummaryPayload.session_order : Object.keys(sessions);
  const rows = order
    .map(session => {
      const summary = sessions?.[session];
      if (!summary?.local) return '';
      const status = summary.active ? 'active' : 'idle';
      const files = summary.files?.count ? `${summary.files.count} files (+${summary.files.added || 0}/-${summary.files.removed || 0})` : 'no files yet';
      return `<article class="yoagent-session-summary ${esc(status)}">
        <div class="yoagent-session-summary-head">
          <span>session ${esc(session)}</span>
          <span>${esc(summary.agent_label || summary.agent || 'agent')}</span>
          <span>${esc(files)}</span>
        </div>
        <div class="yoagent-session-summary-body">${esc(summary.local)}</div>
      </article>`;
    })
    .filter(Boolean)
    .join('');
  return `<section class="yoagent-session-summaries" aria-label="Per-session AI activity summaries">
    ${rows || '<div class="yoagent-empty">No per-session AI activity detected yet.</div>'}
  </section>`;
}

function yoagentChatMessagesHtml() {
  const messages = Array.isArray(yoagentMessages) ? yoagentMessages : [];
  if (!messages.length) {
    if (!yoagentChatEnabled()) {
      return '<div class="yoagent-chat-empty">Set a Claude or Codex backend in Preferences to chat.</div>';
    }
    return '<div class="yoagent-chat-empty">Ask YO!agent what the running AI agents are doing.</div>';
  }
  return messages.map(message => {
    const role = message.role === 'user' ? 'You' : yoagentTabLabel;
    const roleClass = message.role === 'user' ? 'user' : 'assistant';
    return `<div class="yoagent-message ${roleClass}">
      <div class="yoagent-message-role">${esc(role)}</div>
      <div class="yoagent-message-body">${esc(message.content || '')}</div>
    </div>`;
  }).join('');
}

function yoagentNoticeHtml() {
  if (!yoagentNotice?.reason) return '';
  const backend = yoagentNotice.backend ? `<span class="yoagent-chat-notice-backend">${esc(yoagentNotice.backend)}</span> ` : '';
  return `<div class="yoagent-chat-notice">${backend}${esc(yoagentNotice.reason)}</div>`;
}

function yoagentBackendLabel(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'deterministic') return 'No agent';
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return value || 'No agent';
}

function yoagentBackendKey() {
  return String(initialSetting('yoagent.backend', 'deterministic') || 'deterministic').trim().toLowerCase();
}

function yoagentChatEnabled() {
  return ['claude', 'codex'].includes(yoagentBackendKey());
}

function yoagentChatHtml() {
  const disabled = yoagentBusy || readOnlyMode ? ' disabled' : '';
  const placeholder = readOnlyMode ? 'YO!agent chat requires admin access' : 'Ask about agents, repos, files, CI, blockers...';
  const busy = yoagentBusy ? '<div class="yoagent-chat-status">YO!agent is answering...</div>' : '';
  const error = yoagentError ? `<div class="yoagent-chat-error">${esc(yoagentError)}</div>` : '';
  const clearDisabled = yoagentBusy || (!yoagentMessages.length && !yoagentNotice && !yoagentError) ? ' disabled' : '';
  const form = yoagentChatEnabled()
    ? `<form class="yoagent-chat-form" data-yoagent-chat-form>
      <input type="text" class="yoagent-chat-input" data-yoagent-chat-input value="${esc(yoagentDraft)}" placeholder="${esc(placeholder)}"${disabled}>
      <button type="submit" class="yoagent-chat-send"${disabled}>Ask</button>
    </form>`
    : '';
  return `<section class="yoagent-chat" aria-label="YO!agent chat">
    <div class="yoagent-chat-toolbar">
      <button type="button" class="yoagent-chat-clear" data-yoagent-clear${clearDisabled}>Clear conversation</button>
    </div>
    <div class="yoagent-chat-history">${yoagentNoticeHtml()}${yoagentChatMessagesHtml()}${busy}${error}</div>
    ${form}
  </section>`;
}

async function clearYoagentConversation() {
  yoagentMessages = [];
  yoagentBusy = false;
  yoagentError = '';
  yoagentDraft = '';
  yoagentNotice = null;
  renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  try {
    await apiFetch('/api/yoagent/reset', {method: 'POST'});
    statusEl.textContent = 'cleared YO!agent conversation';
  } catch (error) {
    statusEl.innerHTML = `<span class="err">clear YO!agent failed: ${esc(error)}</span>`;
  }
}

async function sendYoagentChatMessage(rawText) {
  const text = String(rawText || '').trim();
  if (!text || yoagentBusy || readOnlyMode || !yoagentChatEnabled()) return;
  yoagentMessages.push({role: 'user', content: text});
  yoagentDraft = '';
  yoagentBusy = true;
  yoagentError = '';
  yoagentNotice = null;
  renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  try {
    const response = await apiFetch('/api/yoagent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, history: yoagentMessages.slice(-10)}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (payload.fallback && payload.fallback_reason) {
      yoagentNotice = {backend: yoagentBackendLabel(payload.backend_used || payload.backend), reason: payload.fallback_reason};
    }
    yoagentMessages.push({role: 'assistant', content: payload.answer || 'No answer.'});
    statusEl.textContent = `YO!agent answered with ${yoagentBackendLabel(payload.backend_used || payload.backend)}`;
  } catch (error) {
    yoagentError = `chat failed: ${error}`;
  } finally {
    yoagentBusy = false;
    renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  }
}

async function refreshActivitySummary(options = {}) {
  activitySummaryRefreshing = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    const response = await apiFetch(`/api/activity-summary${options.force ? '?force=1' : ''}`, {cache: 'no-store'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.status);
    activitySummaryPayload = payload;
  } catch (error) {
    activitySummaryPayload = {
      ...activitySummaryPayload,
      errors: [String(error)],
      global: {lines: [`activity summary unavailable: ${String(error)}`]},
    };
    if (!options.silent) statusEl.innerHTML = `<span class="err">activity summary failed: ${esc(error)}</span>`;
  } finally {
    activitySummaryRefreshing = false;
  }
  renderInfoPanel();
  renderYoagentPanel();
}

function editorSchemePreferenceChoices(options = {}) {
  const preferredOrder = [
    'dark',
    'yolomux-light',
    'vscode-dark-plus',
    'one-dark',
    'dracula',
    'monokai',
    'nord',
    'github-light',
    'one-light',
    'vscode-light-plus',
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

function preferenceSections() {
  return [
    {title: 'General', items: [
      {path: 'general.auto_focus', label: 'Auto-focus active pane', type: 'boolean', help: 'Focus panes and enable hover-open menus after tab switches, layout moves, and hover gestures. Off by default.'},
      {path: 'general.default_layout', label: 'Default layout', type: 'select', choices: ['single', 'grid', 'wall'], help: 'Preferred starting pane layout for new browser visits.'},
      {path: 'general.default_sessions', label: 'Default sessions', type: 'list', help: 'Preferred tmux sessions to open first, one session name per line.'},
    ]},
    {title: 'Appearance', items: [
      {path: 'appearance.theme', label: 'Global app theme', type: 'select', choices: [
        {value: 'dark', label: 'Dark'},
        {value: 'light', label: 'Light'},
        {value: 'system', label: 'System'},
      ], help: 'Theme for menus, panes, Finder/File Explorer, Preferences, Modified files, and terminal colors.'},
      {path: 'appearance.ui_font_size', label: 'UI font size', type: 'number', min: 8, max: 20, step: 1, suffix: 'px', help: 'Font size for menus, tabs, and compact UI text. Values outside the range are clamped.'},
      {path: 'appearance.terminal_font_size', label: 'Terminal font size', type: 'number', min: 8, max: 28, step: 1, suffix: 'px', help: 'Font size used by xterm.js panes. Values outside the range are clamped.'},
      {path: 'appearance.editor_font_size', label: 'Editor font size', type: 'number', min: 8, max: 28, step: 1, suffix: 'px', help: 'Font size used by editor text, code highlighting, and rendered previews.'},
      {path: 'appearance.editor_dark_color_scheme', label: 'Dark editor color scheme', type: 'select', choices: editorSchemePreferenceChoices({dark: true}), help: 'Dark palette used by the editor dark/light toggle.'},
      {path: 'appearance.editor_light_color_scheme', label: 'Light editor color scheme', type: 'select', choices: editorSchemePreferenceChoices({dark: false}), help: 'Light palette used by the editor dark/light toggle.'},
      {path: 'appearance.editor_cursor_style', label: 'Editor cursor style', type: 'select', choices: [
        {value: 'line', label: 'Line'},
        {value: 'block', label: 'Block'},
      ], help: 'Caret shape for CodeMirror editors. Both styles use the active editor cursor color.'},
      {path: 'appearance.file_explorer_font_size', label: `${fileExplorerLabel()} font size`, type: 'number', min: 8, max: 24, step: 1, suffix: 'px', help: 'Font size used by Finder/File Explorer rows.'},
      {path: 'appearance.tab_width', label: 'Tab width', type: 'number', min: 120, max: 420, step: 5, suffix: 'px', help: 'Target width for pane tabs before they wrap to additional rows.'},
      {path: 'appearance.red_reminder_ms', label: 'Red reminder duration', type: 'number', min: 0, max: 10000, step: 50, suffix: 'ms', help: 'Duration of the red attention pulse for sessions that need input or approval. Set 0 to disable.'},
      {path: 'appearance.yolo_rotate_ms', label: 'Active YO rotation period', type: 'number', min: 0, max: 60000, step: 250, suffix: 'ms', help: 'How often the active YO indicator completes a rotation. Set 0 to disable.'},
      {path: 'appearance.metadata_badge_pulse_seconds', label: 'Badge pulse duration', type: 'number', min: 0, max: 120, step: 1, suffix: 's', help: 'When branch, PR, status, or CI metadata changes, badges like PR and CI flash for this many seconds.'},
    ]},
    {title: 'YOLO', items: [
      {path: 'yolo.rule_file_path', label: 'Rule file', type: 'text', action: 'open-yolo-rule', help: 'YAML file with ordered first-match YOLO rules.'},
      {path: 'yolo.dry_run', label: 'Dry run', type: 'boolean', help: 'Log matched rules and actions without pressing an approval key.'},
      {path: 'yolo.prompt_source', label: 'Approval prompt source', type: 'select', choices: [
        {value: 'hybrid', label: 'Pane + transcript'},
        {value: 'pane', label: 'Pane only'},
      ], help: 'Hybrid keeps the visible pane as the trigger and uses recent transcript JSONL only to recover prompt type or command context.'},
    ]},
    {title: 'Performance', items: [
      {path: 'performance.metadata_refresh_ms', label: 'Metadata refresh interval', type: 'number', min: 3000, max: 120000, step: 100, suffix: 'ms', help: 'How often YOLOmux refreshes branch, PR, cwd, and process metadata. Client-side jitter avoids synchronized polling.'},
      {path: 'performance.pane_state_refresh_ms', label: 'Pane state refresh interval', type: 'number', min: 500, max: 30000, step: 100, suffix: 'ms', help: 'How often YOLOmux refreshes YOLO status, prompt state, and tmux session roster. Client-side jitter avoids synchronized polling.'},
      {path: 'performance.latency_refresh_ms', label: 'Latency refresh interval', type: 'number', min: 1000, max: 30000, step: 100, suffix: 'ms', help: 'How often the browser pings the server and updates the latency display. Client-side jitter avoids synchronized polling.'},
      {path: 'performance.event_log_refresh_ms', label: 'Event log refresh interval', type: 'number', min: 1000, max: 60000, step: 100, suffix: 'ms', help: 'How often open YOLO/event-log panes refresh. Client-side jitter avoids synchronized polling.'},
      {path: 'performance.popover_show_delay_ms', label: 'Help/preview hover delay', type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: 'Delay before regular hover help and image previews open.'},
      {path: 'performance.popover_hide_delay_ms', label: 'Popover hide delay', type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: 'Delay before hover popovers close after the pointer leaves.'},
      {path: 'performance.menu_hover_open_delay_ms', label: 'Menu hover open delay', type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: 'Delay before File, View, tmux, Tabs, and Help open from hover.'},
      {path: 'performance.tab_popover_show_delay_ms', label: 'Tab detail hover delay', type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: 'Initial delay before a tab details popup opens.'},
      {path: 'performance.tab_popover_follow_delay_ms', label: 'Tab detail follow delay', type: 'number', min: 0, max: 1000, step: 20, suffix: 'ms', help: 'Shorter delay when moving between tabs after one tab details popup is already open.'},
      {path: 'performance.remote_resize_delay_ms', label: 'Remote resize debounce', type: 'number', min: 50, max: 2000, step: 10, suffix: 'ms', help: 'Delay before sending a settled browser resize to tmux.'},
      {path: 'performance.auto_approve_interval_seconds', label: 'YOLO worker poll interval', type: 'number', min: 0.1, max: 10, step: 0.1, suffix: 's', help: 'How often newly enabled YOLO workers inspect visible tmux prompts.'},
    ]},
    {title: 'Notifications', items: [
      {path: 'notifications.toast_duration_ms', label: 'Notification popup duration', type: 'number', min: 1000, max: 60000, step: 500, suffix: 'ms', help: 'How long in-page notification popups stay visible before auto-closing.'},
      {path: 'notifications.throttle_seconds', label: 'Notification throttle', type: 'number', min: 0, max: 600, step: 5, suffix: 's', help: 'Minimum time before repeating the same notification.'},
      {path: 'notifications.notify_transitions', label: 'Notify transitions', type: 'list', help: 'Session state transitions that may trigger notifications, one transition key per line.'},
    ]},
    {title: 'Terminal / Editor', items: [
      {path: 'terminal_editor.scrollback', label: 'Terminal scrollback', type: 'number', min: 1000, max: 50000, step: 500, suffix: 'lines', help: 'Number of terminal lines kept in browser memory.'},
      {path: 'terminal_editor.word_wrap', label: 'Default editor word wrap', type: 'boolean', help: 'Initial word-wrap state for newly opened editor tabs.'},
      {path: 'terminal_editor.line_numbers', label: 'Default editor line numbers', type: 'boolean', help: 'Initial line-number gutter state for newly opened editor tabs.'},
      {path: 'editor.autosave', label: 'Editor auto-save', type: 'boolean', help: 'Save dirty editor tabs after the delay when the file has not changed on disk.'},
      {path: 'editor.autosave_delay_seconds', label: 'Auto save after N seconds', type: 'number', min: 0.5, max: 60, step: 0.5, suffix: 's', help: 'Delay before dirty editor tabs auto-save. Conflicts ask instead of overwriting.'},
    ]},
    {title: fileExplorerLabel(), items: [
      {path: 'file_explorer.root_mode', label: 'Root mode', type: 'select', choices: ['fixed', 'sync'], help: 'Fixed keeps the current root; Sync follows the focused tmux session directory.'},
      {path: 'file_explorer.image_open_mode', label: 'Image open mode', type: 'select', choices: ['same-tab', 'new-tab'], help: 'Same-tab reuses one image viewer while browsing. New-tab keeps one image tab per file.'},
      {path: 'file_explorer.image_preview_max_px', label: 'Image preview max size', type: 'number', min: 120, max: 1200, step: 20, suffix: 'px', help: 'Maximum width and height for hover previews in Finder/File Explorer.'},
      {path: 'file_explorer.quick_access_paths', label: 'Quick paths', type: 'list', help: 'Pinned Finder/File Explorer roots, one path per line.'},
      {path: 'file_explorer.refresh_ms', label: `${fileExplorerLabel()} refresh interval`, type: 'number', min: 1000, max: 60000, step: 100, suffix: 'ms', help: 'How often YOLOmux checks changed Finder/File Explorer directories and open files. Client-side jitter avoids synchronized polling.'},
      {path: 'file_explorer.new_entry_highlight_ms', label: 'New file highlight duration', type: 'number', min: 0, max: 600000, step: 1000, suffix: 'ms', help: 'How long newly detected files or directories stay colored in Finder/File Explorer.'},
    ]},
    {title: 'Uploads', items: [
      {path: 'uploads.filename_template', label: 'Upload filename template', type: 'text', help: 'Template for pasted and dropped filenames. Use {date:%Y%m%d}, {seq:03d}, {name}, and {ext}.'},
    ]},
    {title: 'YO!agent', items: [
      {path: 'yoagent.backend', label: 'YO!agent backend', type: 'select', choices: [
        {value: 'deterministic', label: 'No agent'},
        {value: 'codex', label: 'Codex'},
        {value: 'claude', label: 'Claude'},
      ], help: 'Backend used for YO!agent chat. No agent is local and always available.'},
      {path: 'yoagent.invocation', label: 'YO!agent invocation', type: 'select', choices: [
        {value: 'cli', label: 'CLI'},
        {value: 'api-key', label: 'API key'},
      ], help: 'CLI runs a local agent binary. API key mode is reserved and falls back safely today.'},
      {path: 'yoagent.system_prompt', label: 'YO!agent system prompt', type: 'textarea', help: 'System prompt used when YO!agent calls a model backend.'},
      {path: 'yoagent.intro', label: 'YO!agent intro', type: 'textarea', help: 'Instruction prefix before the activity context.'},
      {path: 'yoagent.format', label: 'YO!agent answer format', type: 'textarea', help: 'Output-format instruction before the user question.'},
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
  return `loaded ${compactHomePath(settingsConfigPath())}`;
}

function preferencesPathRowsHtml() {
  const settingsPath = settingsConfigPath();
  const rulesPath = yoloRulePath();
  const rulesDetail = yoloRulesPayload.source ? ` · ${yoloRuleStatusDetail()}` : '';
  return `
    <div class="preferences-path-row">
      <span class="preferences-path-label">settings</span><span class="preferences-path-value">${esc(settingsPath)}</span>${pathCopyButtonHtml(settingsPath, {className: 'preferences-path-copy', title: 'Copy settings path'})}
    </div>
    <div class="preferences-path-row">
      <span class="preferences-path-label">YOLO rules</span><span class="preferences-path-value">${esc(rulesPath)}${esc(rulesDetail)}</span>${pathCopyButtonHtml(rulesPath, {className: 'preferences-path-copy', title: 'Copy YOLO rules path'})}
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
  if (path.includes('_refresh_ms') || path === 'file_explorer.refresh_ms') add(['reload', 'update', 'poll', 'polling', 'sync', 'live', 'auto']);
  if (path.includes('popover') || path.includes('hover')) add(['tooltip', 'popup', 'peek', 'flyout']);
  if (path.includes('red_reminder') || path.includes('yolo_rotate') || path.includes('badge_pulse')) add(['animation', 'animate', 'blink', 'flash', 'glow', 'attention', 'reminder']);
  if (path.startsWith('appearance.')) add(['color', 'colour', 'theme', 'dark', 'light', 'background', 'bg', 'contrast', 'style', 'look']);
  if (path === 'terminal_editor.scrollback' || path === 'appearance.terminal_font_size') add(['shell', 'history', 'buffer', 'backlog', 'lines', 'terminal']);
  if (path.startsWith('editor.') || path.includes('editor_') || path.startsWith('terminal_editor.')) add(['code', 'edit', 'codemirror', 'monaco']);
  if (path === 'terminal_editor.word_wrap') add(['softwrap', 'wrapping']);
  if (path === 'terminal_editor.line_numbers') add(['numbers', 'gutter']);
  if (path.startsWith('notifications.')) add(['notify', 'alert', 'toast', 'message', 'banner', 'sound', 'ding', 'ping', 'bell', 'beep', 'desktop', 'dismiss']);
  if (path.includes('throttle')) add(['mute', 'quiet', 'spam', 'cooldown', 'rate limit']);
  if (path.startsWith('file_explorer.')) add(['finder', 'files', 'tree', 'sidebar', 'browser', 'directory', 'folder', 'navigator']);
  if (path.startsWith('uploads.')) add(['upload', 'paste', 'drop', 'filename', 'template', 'file']);
  if (path === 'file_explorer.root_mode') add(['root', 'home', 'base', 'working', 'cwd', 'follow', 'track']);
  if (path === 'file_explorer.quick_access_paths') add(['shortcuts', 'bookmarks', 'favorites', 'pinned', 'jump']);
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
  return [item.label, item.path, item.help, item.suffix, item.keywords, choices, preferenceSearchKeywordsForItem(item)]
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
  return typeof choice === 'object' && choice !== null ? (choice.label || choice.value) : choice;
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
    control = `<input type="number" ${baseAttrs} inputmode="decimal" value="${esc(clampPreferenceNumber(item, value))}" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(item.step || 1)}">`;
  } else if (item.type === 'select') {
    control = `<select ${baseAttrs}>${preferenceSelectOptionsHtml(item, value)}</select>`;
  } else if (item.type === 'list') {
    const text = Array.isArray(value) ? value.join('\n') : String(value || '');
    control = `<textarea ${baseAttrs} rows="3">${esc(text)}</textarea>`;
  } else if (item.type === 'textarea') {
    control = `<textarea ${baseAttrs} rows="3">${esc(String(value || ''))}</textarea>`;
  } else {
    control = `<input type="text" ${baseAttrs} value="${esc(value)}">`;
  }
  const resetDisabled = readOnlyMode || JSON.stringify(value) === JSON.stringify(defaultValue) ? ' disabled' : '';
  const extraControl = item.action === 'open-yolo-rule'
    ? `<button type="button" class="preferences-inline-action" data-yolo-rule-open${readOnlyMode ? ' disabled' : ''}>Open</button>`
    : '';
  const suffix = item.suffix ? `<span class="preferences-setting-suffix">${esc(item.suffix)}</span>` : '';
  const help = item.help ? `<span class="preferences-setting-help">${esc(item.help)}</span>` : '';
  return `<div class="preferences-setting-row"><label class="preferences-setting-label" for="${esc(controlId)}">${esc(item.label)}${help}</label><span class="preferences-setting-control setting-type-${esc(item.type)}">${control}${suffix}${extraControl}<button type="button" class="preferences-reset" data-setting-reset="${esc(item.path)}"${resetDisabled}>Reset</button></span></div>`;
}

function preferencesAllDefault() {
  return preferenceSections().every(section => section.items.every(item => (
    JSON.stringify(preferenceValue(item.path)) === JSON.stringify(preferenceDefault(item.path))
  )));
}

function preferencesPanelHtml() {
  const query = preferenceSearchNeedle();
  const sections = preferenceSections()
    .filter(section => preferenceSectionMatches(section, query))
    .map(section => {
      const titleMatches = textMatchesPreferenceQuery(section.title, query);
      const visibleItems = section.items.filter(item => titleMatches || preferenceItemMatches(item, query));
      const collapsed = !query && collapsedPreferenceSections.has(section.title);
      const rows = visibleItems.map(item => preferenceControlHtml(item)).join('');
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
  const readonly = readOnlyMode ? '<span class="preferences-readonly">readonly access</span>' : '';
  const resetDisabled = readOnlyMode ? ' disabled' : '';
  const resetTitle = preferencesResetConfirmVisible ? 'Confirm GLOBAL reset' : 'GLOBAL reset';
  const resetWarning = preferencesResetConfirmVisible
    ? 'This is destructive for preferences: every setting will be reset to the built-in defaults.'
    : 'Warning: resets every Preferences value to the built-in defaults, including YOLO, appearance, editor, Finder/File Explorer, notification, and performance settings.';
  const resetAction = preferencesResetConfirmVisible ? `
      <div class="preferences-reset-confirm">
        <button type="button" class="preferences-reset-continue" data-preferences-reset-confirm${resetDisabled}>Continue reset</button>
        <button type="button" class="preferences-reset-cancel" data-preferences-reset-cancel>Cancel</button>
      </div>` : `<button type="button" class="preferences-reset-all" data-preferences-reset-all${resetDisabled}>Reset all defaults</button>`;
  const resetBlock = preferencesAllDefault() && !preferencesResetConfirmVisible ? '' : `
    <div class="preferences-global-reset${preferencesResetConfirmVisible ? ' confirming' : ''}" role="group" aria-label="GLOBAL reset warning">
      <div>
        <div class="preferences-global-reset-title">${resetTitle}</div>
        <div class="preferences-global-reset-warning">${resetWarning}</div>
      </div>
      ${resetAction}
    </div>`;
  return `
    <div class="preferences-search-row">
      <input type="search" class="preferences-search" data-preferences-search value="${esc(preferencesSearchText)}" placeholder="Search settings" aria-label="Search settings">
      <button type="button" class="preferences-search-button" data-preferences-search-action>YOsearch</button>
    </div>
    <div class="preferences-path-rows">${preferencesPathRowsHtml()}${readonly}</div>
    <div class="preferences-status" data-level="${clientSettingsPayload.error || yoloRulesPayload.error ? 'error' : 'ok'}">${esc(preferenceStatusText())}</div>
    <div class="preferences-sections">${sections}</div>
    ${resetBlock}`;
}

function createPreferencesPanel() {
  preferencesSearchFresh = true;
  const panel = document.createElement('article');
  panel.className = 'panel preferences-panel';
  panel.id = `panel-${prefsItemId}`;
  panel.innerHTML = `
      <div class="panel-head preferences-panel-head">
        ${virtualPanelControlsHtml(prefsItemId, 'Preferences')}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${prefsItemId}" class="panel-session-label"><span class="session-button-dir">Preferences</span></div>
          <div id="meta-${prefsItemId}" class="meta">${esc(preferenceStatusText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(prefsItemId)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div class="preferences-body panel-overlay-root">
        <div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div>
        ${preferencesPanelHtml()}
      </div>`;
  bindPanelShell(panel, prefsItemId);
  bindPreferencesPanel(panel);
  focusPreferencesSearchSoon(panel);
  return panel;
}

function markPreferencesInteracted() {
  preferencesSearchFresh = false;
}

function focusPreferencesSearch(panel = null) {
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

function focusPreferencesSearchSoon(panel = null) {
  focusPreferencesSearch(panel);
  requestAnimationFrame(() => focusPreferencesSearch(panel));
  setTimeout(() => focusPreferencesSearch(panel), 0);
  setTimeout(() => focusPreferencesSearch(panel), 80);
}

function focusFreshPreferencesSearchSoon(panel = null) {
  if (!preferencesSearchFresh) return;
  focusPreferencesSearchSoon(panel);
}

function renderPreferencesPanels(options = {}) {
  for (const panel of document.querySelectorAll('.preferences-panel')) {
    const body = panel.querySelector('.preferences-body');
    const meta = panel.querySelector(`#meta-${cssEscape(prefsItemId)}`);
    if (meta) meta.textContent = preferenceStatusText();
    if (body) {
      const activeControl = activePreferenceControl(panel);
      const shouldKeepDom = activeControl && options.force !== true;
      const scrollTop = body.scrollTop;
      const scrollLeft = body.scrollLeft;
      if (shouldKeepDom) {
        const status = body.querySelector('.preferences-status');
        if (status) {
          status.dataset.level = clientSettingsPayload.error || yoloRulesPayload.error ? 'error' : 'ok';
          status.textContent = preferenceStatusText();
        }
        const pathRows = body.querySelector('.preferences-path-rows');
        if (pathRows) pathRows.innerHTML = `${preferencesPathRowsHtml()}${readOnlyMode ? '<span class="preferences-readonly">readonly access</span>' : ''}`;
      } else {
        body.innerHTML = `<div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div>${preferencesPanelHtml()}`;
      }
      if (options.focusSearch !== true) {
        body.scrollTop = scrollTop;
        body.scrollLeft = scrollLeft;
        requestAnimationFrame(() => {
          body.scrollTop = scrollTop;
          body.scrollLeft = scrollLeft;
        });
      }
    }
    bindPreferencesPanel(panel);
    if (options.focusSearch) focusPreferencesSearch(panel);
  }
}

function bindPreferencesPanel(panel) {
  if (!panel || panel.dataset.preferencesBound === 'true') return;
  panel.dataset.preferencesBound = 'true';
  panel.addEventListener('input', event => {
    const search = event.target.closest('[data-preferences-search]');
    if (search && panel.contains(search)) {
      markPreferencesInteracted();
      preferencesSearchText = search.value || '';
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true, focusSearch: true});
      return;
    }
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control) || control.dataset.settingType !== 'number') return;
    validatePreferenceNumberControl(control);
  });
  panel.addEventListener('change', event => {
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control)) return;
    markPreferencesInteracted();
    savePreferenceControl(control);
  });
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
      renderPreferencesPanels({force: true, focusSearch: true});
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
      markPreferencesInteracted();
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
        .catch(error => { statusEl.innerHTML = `<span class="err">copy failed: ${esc(error)}</span>`; });
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
    markPreferencesInteracted();
    resetPreference(reset.dataset.settingReset || '');
  });
}

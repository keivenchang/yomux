const dockviewContentComponentName = 'yolomux-panel';
const dockviewTabComponentName = 'yolomux-tab';
const dockviewPanelRenderer = 'onlyWhenVisible';
const dockviewRootId = 'dockviewRoot';
// named geometry/timing constants for the Dockview layer (were repeated unnamed literals).
const DRAG_HYSTERESIS_PX = 8;            // px a pointer must move before a header drag is treated as a drag
const PANE_DRAG_SUPPRESS_MS = 500;       // window after a pane drag during which pointer events are ignored
const SPLIT_PCT_EPSILON = 0.01;          // split-percent diffs below this are treated as equal (no re-layout)
const SERIALIZED_WEIGHT_BASE = 1000;     // Dockview gridview node weight base for serialization
const DOCKVIEW_MIN_LAYOUT_WIDTH = 640;   // enough room for a serialized Finder + content pane snapshot
const DOCKVIEW_MIN_LAYOUT_HEIGHT = 240;  // prevents sleep/hidden-tab 1px layouts from serializing
const dockviewLayoutState = {
  api: null,
  host: null,
  disposables: [],
  applyingFromLayout: false,
  adoptingFromDockview: false,
  syncQueued: false,
  hostLayoutFrame: 0,
  lastHostLayoutSignature: '',
  lastAppliedLayoutSignature: '',
  groupSlots: new Map(),
  pendingRootBoundaryDrop: null,
  reloadAfterAdoption: false,
  tabPointerDrag: null,
  tabDropHandledAt: 0,
  panePointerDrag: null,
  panePointerDragSuppressedUntil: 0,
};

function dockviewCore() {
  return window['dockview-core'] || null;
}

function dockviewLayoutAvailable() {
  return typeof dockviewCore()?.createDockview === 'function';
}

function dockviewLayoutEnabled() {
  return dockviewLayoutAvailable();
}

function dockviewLayoutActive() {
  return Boolean(dockviewLayoutState.api && grid?.classList?.contains('dockview-grid'));
}

function dockviewOrientationForSplit(direction) {
  return direction === 'column' ? 'VERTICAL' : 'HORIZONTAL';
}

function dockviewSplitForOrientation(orientation) {
  return orientation === 'VERTICAL' ? 'column' : 'row';
}

function dockviewOppositeOrientation(orientation) {
  return orientation === 'VERTICAL' ? 'HORIZONTAL' : 'VERTICAL';
}

function dockviewThemeForApp() {
  const core = dockviewCore();
  if (!core) return undefined;
  return document.body?.classList?.contains(themeBodyClass('light')) ? core.themeLight : core.themeDark;
}

function dockviewRootBoundaryDropIntent(event) {
  if ((event?.kind !== 'content' && event?.kind !== 'edge') || !layoutSplitZone(event.position)) return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  if (!isLayoutItem(item) || isFileExplorerItem(item)) return null;
  const nativeEvent = event.nativeEvent;
  const rect = dockviewLayoutState.host?.getBoundingClientRect?.();
  if (!nativeEvent || !rect) return null;
  const zone = rootBoundaryDropZoneForEvent(nativeEvent, rect);
  if (!layoutSplitZone(zone)) return null;
  if (event.kind === 'content' && event.group && !dockviewContentDropCanUseRootBoundary(nativeEvent, zone)) return null;
  if (rootBoundaryDropOverDockedFileExplorer(nativeEvent, zone)) return null;
  return {
    item,
    zone,
    sourceSlot: slotForItem(item) || dockviewSlotForGroupId(data?.groupId || ''),
    targetRect: rect,
  };
}

function dockviewContentDropCanUseRootBoundary(event, zone) {
  const root = layoutSlots?.[layoutTreeKey];
  const rootRect = layoutNodeScreenRect(root);
  if (!root || !rootRect) return false;
  const crossSplit = zone === 'left' || zone === 'right' ? 'column' : 'row';
  const axis = crossSplit === 'column' ? 'y' : 'x';
  const pointer = axis === 'y' ? event.clientY : event.clientX;
  const tolerance = Math.max(48, layoutBoundaryDropBandPx(axis === 'y' ? rootRect.height : rootRect.width));
  const visit = node => {
    if (!node || node.slot || node.split !== crossSplit) return false;
    const firstRect = layoutNodeScreenRect(node.children?.[0]);
    const secondRect = layoutNodeScreenRect(node.children?.[1]);
    if (!firstRect || !secondRect) return false;
    const boundary = axis === 'y'
      ? (firstRect.bottom + secondRect.top) / 2
      : (firstRect.right + secondRect.left) / 2;
    if (Math.abs(pointer - boundary) <= tolerance) return true;
    return visit(node.children?.[0]) || visit(node.children?.[1]);
  };
  return visit(root);
}

function dockviewPaneContentDropInfo(event) {
  if (event?.kind !== 'content' || !event.group) return null;
  const zone = event.position === 'center' ? 'middle' : event.position;
  if (zone !== 'middle' && !layoutSplitZone(zone)) return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  const targetSlot = dockviewSlotForGroupId(event.group.id || '');
  if (!item || !targetSlot) return null;
  const sourceSlot = slotForItem(item) || dockviewSlotForGroupId(data?.groupId || '');
  const intent = {
    item,
    sourceSlot,
    targetSlot,
    targetRect: layoutSlotScreenRect(targetSlot),
    zone,
    createsPane: layoutSplitZone(zone),
  };
  return {item, intent};
}

function dockviewPaneContentDropIntent(event) {
  const info = dockviewPaneContentDropInfo(event);
  if (!info) return null;
  if (!layoutSplitZone(info.intent.zone)) return null;
  if (dockviewPinnedTabCrossPaneViolation(info.intent)) return null;
  return dropIntentAllowsSession(info.item, info.intent) ? info.intent : null;
}

function dockviewShouldSuppressPaneContentDrop(event) {
  const info = dockviewPaneContentDropInfo(event);
  return Boolean(info && (
    dockviewPinnedTabCrossPaneViolation(info.intent)
      || !dropIntentAllowsSession(info.item, info.intent)
  ));
}

function dockviewShouldSuppressReservedRootBoundary(event) {
  if ((event?.kind !== 'content' && event?.kind !== 'edge') || !layoutSplitZone(event.position)) return false;
  const nativeEvent = event.nativeEvent;
  const rect = dockviewLayoutState.host?.getBoundingClientRect?.();
  if (!nativeEvent || !rect) return false;
  const zone = rootBoundaryDropZoneForEvent(nativeEvent, rect);
  return Boolean(zone && rootBoundaryDropOverDockedFileExplorer(nativeEvent, zone));
}

function dockviewClearRootBoundaryPreview() {
  if (!grid?.classList?.contains('drop-preview-root')) return;
  clearDropPreview();
}

function dockviewSetInvalidTabDropPreview(active) {
  dockviewLayoutState.host?.classList?.toggle('dockview-invalid-tab-drop-preview', Boolean(active));
}

function dockviewShowRootBoundaryPreview(intent) {
  if (!intent || typeof showDropPreview !== 'function') {
    dockviewClearRootBoundaryPreview();
    return;
  }
  showDropPreview({
    boundary: 'root',
    previewNode: grid,
    sourceSlot: intent.sourceSlot,
    targetRect: intent.targetRect || dockviewLayoutState.host?.getBoundingClientRect?.(),
    targetSlot: intent.sourceSlot,
    zone: intent.zone,
  });
}

function dockviewTrackRootBoundaryOverlay(event) {
  const invalidTabDrop = dockviewTabDropViolatesPinnedPartition(event);
  dockviewSetInvalidTabDropPreview(invalidTabDrop);
  if (invalidTabDrop) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    return;
  }
  if (dockviewTabDropWouldNoop(event)) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    event.preventDefault?.();
    return;
  }
  const intent = dockviewRootBoundaryDropIntent(event);
  if (dockviewPinnedTabRootBoundaryViolation(intent)) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    event.preventDefault?.();
    return;
  }
  const paneIntent = intent ? null : dockviewPaneContentDropIntent(event);
  dockviewLayoutState.pendingRootBoundaryDrop = intent ? {
    ...intent,
    signature: layoutSlotsSignature(layoutSlots),
  } : null;
  if (intent) {
    dockviewShowRootBoundaryPreview(intent);
    event.preventDefault?.();
  } else if (dockviewShouldSuppressPaneContentDrop(event) || (!paneIntent && dockviewShouldSuppressReservedRootBoundary(event))) {
    dockviewClearRootBoundaryPreview();
    event.preventDefault?.();
  } else {
    dockviewClearRootBoundaryPreview();
  }
}

function dockviewTabDropWouldNoop(event) {
  const stripEnd = dockviewTabStripEndDropInfoForEvent(event);
  if (stripEnd) return dockviewTabStripEndDropWouldNoop(stripEnd);
  if (dockviewTabEdgeReorderIntent(event)) return false;
  const info = dockviewTabInsertionInfo(event);
  return Boolean(info && info.sourceSlot === info.targetSlot && info.adjustedIndex === info.sourceIndex);
}

// Resolve the DROP TARGET tab for a dockview tab-drop event by POINTER COORDINATES, never by
// `nativeEvent.target` alone: with `dndStrategy: 'pointer'` the dragged tab smooth-reorders UNDER the
// cursor, so the element under the pointer at drop time is usually the dragged tab itself. Hit-testing
// that made every slow same-strip reorder look like a self-drop and the no-op veto silently swallowed
// it (the "drag first tab to second does nothing" bug). Excludes the dragged tab, picks the tab whose
// rect contains the pointer x (nearest rect when the pointer sits over the dragged tab's visual gap or
// past the strip ends), and recomputes left/right from the resolved tab's own rect.
function dockviewTabDropHit(event, item) {
  const nativeEvent = event.nativeEvent;
  const nativeTarget = nativeEvent?.target;
  const hoveredTab = nativeTarget?.closest?.('.dv-tab') || null;
  const tabStrip = nativeTarget?.closest?.('.dv-tabs-container') || hoveredTab?.parentElement || null;
  if (!tabStrip) return null;
  const tabs = Array.from(tabStrip.querySelectorAll?.('.dv-tab') || []);
  if (!tabs.length) return null;
  const tabItems = tabs.map(tab => tab.querySelector?.('.dockview-pane-tab')?.dataset?.paneTab || '');
  const sourceIndex = tabItems.indexOf(item);
  const pointerX = Number(nativeEvent?.clientX);
  const hoveredIsDragged = hoveredTab ? tabs.indexOf(hoveredTab) === sourceIndex && sourceIndex >= 0 : false;
  let targetTab = !hoveredIsDragged ? hoveredTab : null;
  if ((!targetTab || !tabs.includes(targetTab)) && Number.isFinite(pointerX)) {
    let best = null;
    for (let index = 0; index < tabs.length; index++) {
      if (index === sourceIndex) continue;   // never resolve to the dragged tab
      const rect = tabs[index].getBoundingClientRect();
      const distance = pointerX < rect.left ? rect.left - pointerX : (pointerX > rect.right ? pointerX - rect.right : 0);
      if (!best || distance < best.distance) best = {tab: tabs[index], distance};
    }
    targetTab = best?.tab || null;
  }
  if (!targetTab) return null;
  const targetIndex = tabs.indexOf(targetTab);
  if (targetIndex < 0) return null;
  let position = event.position === 'right' ? 'right' : 'left';
  if (Number.isFinite(pointerX)) {
    const rect = targetTab.getBoundingClientRect();
    position = pointerX >= rect.left + rect.width / 2 ? 'right' : 'left';
  }
  return {tabs, tabItems, sourceIndex, targetTab, targetIndex, position};
}

function dockviewTabInsertionInfo(event) {
  if (event?.kind !== 'tab') return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  const targetSlot = dockviewSlotForGroupId(event.group?.id || '');
  const sourceSlot = slotForItem(item) || dockviewSlotForGroupId(data?.groupId || '');
  if (!item || !targetSlot) return null;
  const hit = dockviewTabDropHit(event, item);
  if (!hit) return null;
  const {tabItems, sourceIndex, targetIndex, position} = hit;
  const insertionIndex = position === 'right' ? targetIndex + 1 : targetIndex;
  const adjustedIndex = insertionIndex - (sourceSlot === targetSlot && sourceIndex >= 0 && sourceIndex < insertionIndex ? 1 : 0);
  const targetItems = tabItems.filter(tab => tab && tab !== item);
  const pinnedBoundary = targetItems.filter(tabIsPinned).length;
  return {item, targetSlot, sourceSlot, sourceIndex, targetIndex, tabItems, targetItems, position, insertionIndex, adjustedIndex, pinnedBoundary};
}

function dockviewTabStripForPoint(x, y) {
  const node = document.elementFromPoint?.(x, y);
  return node?.closest?.('.dv-tabs-container') || null;
}

function dockviewTabStripEndDropInfo(options = {}) {
  const tabStrip = options.tabStrip;
  const item = options.item || '';
  const pointerX = Number(options.pointerX);
  if (!tabStrip || !item || !Number.isFinite(pointerX)) return null;
  const tabs = Array.from(tabStrip.querySelectorAll?.('.dv-tab') || []);
  if (!tabs.length) return null;
  const tabItems = tabs.map(tab => tab.querySelector?.('.dockview-pane-tab')?.dataset?.paneTab || '');
  const sourceIndex = tabItems.indexOf(item);
  const targetItems = tabItems.filter(tab => tab && tab !== item);
  const candidateTabs = tabs.filter((_, index) => index !== sourceIndex);
  if (!candidateTabs.length) return null;
  const stripRect = tabStrip.getBoundingClientRect?.();
  if (!stripRect) return null;
  const lastRect = candidateTabs
    .map(tab => tab.getBoundingClientRect())
    .filter(rect => rect && Number.isFinite(rect.right))
    .reduce((best, rect) => (!best || rect.right > best.right ? rect : best), null);
  if (!lastRect || pointerX < lastRect.right || pointerX > stripRect.right) return null;
  const adjustedIndex = targetItems.length;
  return {
    item,
    targetSlot: options.targetSlot || '',
    sourceSlot: options.sourceSlot || '',
    sourceIndex,
    tabItems,
    targetItems,
    position: 'right',
    insertionIndex: tabItems.length,
    adjustedIndex,
    insertIndex: adjustedIndex,
    pinnedBoundary: targetItems.filter(tabIsPinned).length,
  };
}

function dockviewTabStripEndDropInfoForEvent(event) {
  if (event?.kind !== 'tab') return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  const nativeEvent = event.nativeEvent;
  const nativeTarget = nativeEvent?.target;
  const tabStrip = nativeTarget?.closest?.('.dv-tabs-container') || null;
  const targetSlot = dockviewSlotForGroupId(event.group?.id || '') || dockviewSlotForGroupElement(tabStrip?.closest?.('.dv-groupview'));
  const sourceSlot = slotForItem(item) || dockviewSlotForGroupId(data?.groupId || '');
  return dockviewTabStripEndDropInfo({
    tabStrip,
    item,
    targetSlot,
    sourceSlot,
    pointerX: nativeEvent?.clientX,
  });
}

function dockviewTabStripEndDropInfoForPointer(event, state) {
  if (!state?.item || !state.slot) return null;
  const x = Number(event.clientX) || 0;
  const y = Number(event.clientY) || 0;
  const tabStrip = dockviewTabStripForPoint(x, y);
  const targetSlot = dockviewSlotForGroupElement(tabStrip?.closest?.('.dv-groupview'));
  return dockviewTabStripEndDropInfo({
    tabStrip,
    item: state.item,
    targetSlot,
    sourceSlot: state.slot,
    pointerX: x,
  });
}

function dockviewTabStripEndDropWouldNoop(info) {
  return Boolean(info?.sourceSlot && info.sourceSlot === info.targetSlot && info.sourceIndex === info.insertIndex);
}

function dockviewTabStripEndDropViolatesPinnedPartition(info) {
  if (!info || !isPinnableTab(info.item)) return false;
  if (dockviewPinnedTabCrossPaneViolation(info)) return true;
  return tabIsPinned(info.item)
    ? info.adjustedIndex > info.pinnedBoundary
    : info.adjustedIndex < info.pinnedBoundary;
}

function dockviewTabStripEndDropIntent(event) {
  const info = dockviewTabStripEndDropInfoForEvent(event);
  if (!info || !info.targetSlot || dockviewTabStripEndDropWouldNoop(info)) return null;
  if (dockviewTabStripEndDropViolatesPinnedPartition(info)) return null;
  return info;
}

function dockviewTabDropViolatesPinnedPartition(event) {
  const stripEnd = dockviewTabStripEndDropInfoForEvent(event);
  if (stripEnd) return dockviewTabStripEndDropViolatesPinnedPartition(stripEnd);
  if (dockviewTabEdgeReorderIntent(event)) return false;
  const info = dockviewTabInsertionInfo(event);
  if (!info || !isPinnableTab(info.item)) return false;
  if (dockviewPinnedTabCrossPaneViolation(info)) return true;
  return tabIsPinned(info.item)
    ? info.adjustedIndex > info.pinnedBoundary
    : info.adjustedIndex < info.pinnedBoundary;
}

function dockviewPinnedTabCrossPaneViolation(info) {
  if (!info?.item || !tabIsPinned(info.item) || !info.sourceSlot) return false;
  if (info.createsPane === true) return true;
  return Boolean(info.targetSlot && info.targetSlot !== info.sourceSlot);
}

function dockviewPinnedTabRootBoundaryViolation(intent) {
  return dockviewPinnedTabCrossPaneViolation({
    item: intent?.item || '',
    sourceSlot: intent?.sourceSlot || '',
    createsPane: true,
  });
}

function dockviewAdjacentEdgeTabInsertIndex(sourceIndex, targetIndex, tabCount) {
  if (sourceIndex < 0 || targetIndex < 0 || Math.abs(sourceIndex - targetIndex) !== 1) return null;
  if (sourceIndex !== 0 && sourceIndex !== tabCount - 1) return null;
  return targetIndex;
}

function dockviewTabEdgeReorderIntent(event) {
  if (event?.kind !== 'tab') return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  const targetSlot = dockviewSlotForGroupId(event.group?.id || '');
  const sourceSlot = slotForItem(item) || dockviewSlotForGroupId(data?.groupId || '');
  if (!item || !targetSlot || sourceSlot !== targetSlot) return null;
  const hit = dockviewTabDropHit(event, item);   // coordinate-resolved: never the dragged tab itself
  if (!hit) return null;
  const targetItem = hit.tabItems[hit.targetIndex] || '';
  if (!tabIsPinned(item) || !tabIsPinned(targetItem)) return null;
  const insertIndex = dockviewAdjacentEdgeTabInsertIndex(hit.sourceIndex, hit.targetIndex, hit.tabs.length);
  if (insertIndex === null) return null;
  if (!targetSlot || slotIsFileExplorerPane(targetSlot)) return null;
  return {item, targetSlot, sourceSlot, insertIndex};
}

function dockviewTabForPoint(x, y) {
  const node = document.elementFromPoint?.(x, y);
  const tab = node?.closest?.('.dv-tab');
  return tab?.querySelector?.('.dockview-pane-tab') || node?.closest?.('.dockview-pane-tab') || null;
}

function dockviewSuppressPanePointerDrag(ms = PANE_DRAG_SUPPRESS_MS) {
  dockviewLayoutState.panePointerDragSuppressedUntil = Math.max(
    Number(dockviewLayoutState.panePointerDragSuppressedUntil) || 0,
    Date.now() + ms,
  );
}

function dockviewPanePointerDragSuppressed() {
  return Boolean(
    dockviewLayoutState.tabPointerDrag
      || Date.now() < (Number(dockviewLayoutState.panePointerDragSuppressedUntil) || 0),
  );
}

function dockviewBeginTabPointerDrag(event, item) {
  if (event.button !== undefined && event.button !== 0) return;
  const slot = slotForItem(item);
  if (!slot) return;
  dockviewSuppressPanePointerDrag();
  dockviewLayoutState.tabPointerDrag = {
    item,
    slot,
    x: Number(event.clientX) || 0,
    y: Number(event.clientY) || 0,
  };
}

function dockviewFinishTabPointerDrag(event) {
  const state = dockviewLayoutState.tabPointerDrag;
  dockviewLayoutState.tabPointerDrag = null;
  dockviewSetInvalidTabDropPreview(false);
  if (state) dockviewSuppressPanePointerDrag();
  if (!state?.item || !state.slot) return;
  const dx = Math.abs((Number(event.clientX) || 0) - state.x);
  const dy = Math.abs((Number(event.clientY) || 0) - state.y);
  if (Math.max(dx, dy) < DRAG_HYSTERESIS_PX) return;
  const stripEnd = dockviewTabStripEndDropInfoForPointer(event, state);
  if (stripEnd && !dockviewTabStripEndDropWouldNoop(stripEnd) && !dockviewTabStripEndDropViolatesPinnedPartition(stripEnd)) {
    window.setTimeout(() => {
      if (Date.now() - (Number(dockviewLayoutState.tabDropHandledAt) || 0) < 800) return;
      void moveSessionToSlot(stripEnd.item, stripEnd.targetSlot, stripEnd.sourceSlot, stripEnd.insertIndex);
    }, 0);
    return;
  }
  const target = dockviewTabForPoint(Number(event.clientX) || 0, Number(event.clientY) || 0);
  const targetItem = target?.dataset?.paneTab || '';
  if (!targetItem || targetItem === state.item) return;
  if (!tabIsPinned(state.item) || !tabIsPinned(targetItem)) return;
  const targetSlot = slotForItem(targetItem);
  if (!targetSlot || targetSlot !== state.slot || slotIsFileExplorerPane(targetSlot)) return;
  const tabs = paneTabs(targetSlot);
  const sourceIndex = tabs.indexOf(state.item);
  const targetIndex = tabs.indexOf(targetItem);
  const insertIndex = dockviewAdjacentEdgeTabInsertIndex(sourceIndex, targetIndex, tabs.length);
  if (insertIndex === null) return;
  window.setTimeout(() => {
    // The fallback only acts when dockview's own drop pipeline did NOT handle this gesture (onWillDrop
    // stamps tabDropHandledAt). Without this guard the fallback re-runs the swap AFTER the primary move
    // and, in a two-tab strip, computes the mirror swap — silently UNDOING the user's drag.
    if (Date.now() - (Number(dockviewLayoutState.tabDropHandledAt) || 0) < 800) return;
    const currentTabs = paneTabs(targetSlot);
    const currentSourceIndex = currentTabs.indexOf(state.item);
    const currentTargetIndex = currentTabs.indexOf(targetItem);
    const currentInsertIndex = dockviewAdjacentEdgeTabInsertIndex(currentSourceIndex, currentTargetIndex, currentTabs.length);
    if (currentInsertIndex === null) return;
    void moveSessionToSlot(state.item, targetSlot, targetSlot, currentInsertIndex);
  }, 0);
}

function dockviewGroupForPoint(x, y) {
  return document.elementFromPoint?.(x, y)?.closest?.('.dv-groupview') || null;
}

function dockviewBeginPanePointerDrag(event, sourceSlot) {
  if (event.button !== undefined && event.button !== 0) return;
  if (dockviewPanePointerDragSuppressed() || dockviewLayoutState.panePointerDrag) return;
  if (!sourceSlot || slotIsFileExplorerPane(sourceSlot) || !activeItemForSide(sourceSlot)) return;
  dockviewLayoutState.panePointerDrag = {
    sourceSlot,
    active: false,
    previewStarted: false,
    x: Number(event.clientX) || 0,
    y: Number(event.clientY) || 0,
  };
}

function dockviewPanePointerIntent(event) {
  const state = dockviewLayoutState.panePointerDrag;
  if (!state?.sourceSlot) return null;
  const group = dockviewGroupForPoint(Number(event.clientX) || 0, Number(event.clientY) || 0);
  const targetSlot = group ? dockviewSlotForGroupElement(group) : null;
  if (!targetSlot || targetSlot === state.sourceSlot) return null;
  return {
    sourceSlot: state.sourceSlot,
    targetSlot,
    zone: 'middle',
    swap: true,
    previewNode: group,
    targetRect: layoutSlotScreenRect(targetSlot),
  };
}

function dockviewTrackPanePointerDrag(event) {
  const state = dockviewLayoutState.panePointerDrag;
  if (!state?.sourceSlot) return;
  const dx = Math.abs((Number(event.clientX) || 0) - state.x);
  const dy = Math.abs((Number(event.clientY) || 0) - state.y);
  if (!state.active && Math.max(dx, dy) < DRAG_HYSTERESIS_PX) return;
  state.active = true;
  if (!state.previewStarted) {
    startPaneDragPreview(event, state.sourceSlot);
    state.previewStarted = true;
  } else {
    moveCustomDragPreview(event);
  }
  const intent = dockviewPanePointerIntent(event);
  if (!paneSwapIntentAllowed(intent)) {
    clearDropPreview();
    return;
  }
  showDropPreview(intent);
}

function dockviewFinishPanePointerDrag(event) {
  const state = dockviewLayoutState.panePointerDrag;
  const intent = dockviewPanePointerIntent(event);
  dockviewLayoutState.panePointerDrag = null;
  if (!state?.active) {
    return;
  }
  if (state.previewStarted) stopCustomDragPreview();
  clearDropPreview();
  if (paneSwapIntentAllowed(intent)) swapPaneSlots(intent.sourceSlot, intent.targetSlot);
}

function dockviewFinishPendingRootBoundaryDrop(event) {
  const pending = dockviewLayoutState.pendingRootBoundaryDrop;
  dockviewClearRootBoundaryPreview();
  if (!pending) return;
  const rect = dockviewLayoutState.host?.getBoundingClientRect?.();
  const zone = rect ? rootBoundaryDropZoneForEvent(event, rect) : null;
  if (zone !== pending.zone) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    return;
  }
  window.setTimeout(() => {
    if (dockviewLayoutState.pendingRootBoundaryDrop !== pending) return;
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    if (layoutSlotsSignature(layoutSlots) !== pending.signature || !itemInLayout(pending.item)) return;
    void splitSessionAtLayoutBoundary(pending.item, pending.zone, pending.sourceSlot);
  }, 0);
}

function dockviewInstallRootBoundaryDropFallback() {
  const finish = event => dockviewFinishPendingRootBoundaryDrop(event);
  window.addEventListener('pointerup', finish, true);
  window.addEventListener('dragend', finish, true);
  return {
    dispose() {
      window.removeEventListener('pointerup', finish, true);
      window.removeEventListener('dragend', finish, true);
    },
  };
}

function dockviewGroupForEvent(event) {
  return event.target?.closest?.('.dv-groupview') || null;
}

function dockviewSlotForGroupElement(group) {
  if (!group) return '';
  const panelSlot = group.querySelector('.dockview-panel-content > .panel')?.dataset?.slot;
  if (panelSlot) return panelSlot;
  const activeItem = group.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '';
  return slotForItem(activeItem) || '';
}

function dockviewGroupDropIntentForEvent(event) {
  const group = dockviewGroupForEvent(event);
  const targetSlot = dockviewSlotForGroupElement(group);
  const targetRect = group?.getBoundingClientRect?.();
  if (!group || !targetSlot || !targetRect) return null;
  return {
    targetSlot,
    zone: dropZoneForRect(event, targetRect),
    previewNode: group,
    targetRect,
  };
}

function dockviewFileDropIntentForEvent(event) {
  return dockviewGroupDropIntentForEvent(event);
}

function dockviewHandleFileDragOver(event) {
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
  const payload = fileDragPayload(event);
  if (payload?.path) {
    const intent = dockviewFileDropIntentForEvent(event);
    if (!intent) return;
    event.preventDefault();
    event.stopPropagation();
    if (!fileDropIntentAllowsPayload(payload, intent)) {
      event.dataTransfer.dropEffect = 'none';
      clearDropPreview();
      return;
    }
    event.dataTransfer.dropEffect = 'copy';
    showDropPreview(intent);
    return;
  }
  const tabPayload = dragPayload(event);
  if (!tabPayload?.session) return;
  const intent = dockviewGroupDropIntentForEvent(event);
  if (!intent) return;
  event.preventDefault();
  event.stopPropagation();
  if (!dropIntentAllowsSession(tabPayload.session, intent)) {
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  event.dataTransfer.dropEffect = 'move';
  showDropPreview(intent);
}

function dockviewHandleFileDrop(event) {
  const panePayload = paneDragPayload(event);
  if (panePayload?.slot) {
    event.preventDefault();
    event.stopPropagation();
    const intent = paneSwapIntentForEvent(event, panePayload.slot);
    clearDropPreview();
    if (paneSwapIntentAllowed(intent)) swapPaneSlots(intent.sourceSlot, intent.targetSlot);
    return;
  }
  const payload = fileDragPayload(event);
  if (payload?.path) {
    const intent = dockviewFileDropIntentForEvent(event);
    if (!intent) return;
    event.preventDefault();
    event.stopPropagation();
    clearDropPreview();
    if (!fileDropIntentAllowsPayload(payload, intent)) return;
    openDraggedFilesInEditor(payload, {targetSlot: intent.targetSlot, targetZone: intent.zone});
    return;
  }
  const tabPayload = dragPayload(event);
  if (!tabPayload?.session) return;
  const intent = dockviewGroupDropIntentForEvent(event);
  if (!intent) return;
  event.preventDefault();
  event.stopPropagation();
  clearDropPreview();
  if (!dropIntentAllowsSession(tabPayload.session, intent)) return;
  dropSessionWithIntent(tabPayload.session, intent, tabPayload.sourceSlot || slotForSession(tabPayload.session));
}

function dockviewInstallFileDropBridge(host) {
  const dragOver = event => dockviewHandleFileDragOver(event);
  const drop = event => dockviewHandleFileDrop(event);
  const dragLeave = event => {
    if (host?.contains?.(event.relatedTarget)) return;
    clearDropPreview();
  };
  host.addEventListener('dragover', dragOver, true);
  host.addEventListener('drop', drop, true);
  host.addEventListener('dragleave', dragLeave, true);
  return {
    dispose() {
      host.removeEventListener('dragover', dragOver, true);
      host.removeEventListener('drop', drop, true);
      host.removeEventListener('dragleave', dragLeave, true);
    },
  };
}

function dockviewLayoutToHost(api = dockviewLayoutState.api, host = dockviewLayoutState.host, options = {}) {
  if (!api || !host) return;
  const size = dockviewHostLayoutSize(host);
  const width = Math.max(1, Math.round(size.width || 0));
  const height = Math.max(1, Math.round(size.height || 0));
  const signature = `${width}x${height}`;
  if (options.force !== true && dockviewLayoutState.lastHostLayoutSignature === signature) return;
  dockviewLayoutState.lastHostLayoutSignature = signature;
  api.layout?.(width, height);
}

function dockviewScheduleLayoutToHost(api = dockviewLayoutState.api, host = dockviewLayoutState.host) {
  if (!api || !host) return;
  if (dockviewLayoutState.hostLayoutFrame) cancelAnimationFrame(dockviewLayoutState.hostLayoutFrame);
  dockviewLayoutState.hostLayoutFrame = requestAnimationFrame(() => {
    dockviewLayoutState.hostLayoutFrame = 0;
    dockviewLayoutToHost(api, host);
  });
}

function dockviewHostLayoutSize(host = dockviewLayoutState.host) {
  if (!host) return {width: DOCKVIEW_MIN_LAYOUT_WIDTH, height: DOCKVIEW_MIN_LAYOUT_HEIGHT};
  const rect = host.getBoundingClientRect?.();
  const width = Number(host.clientWidth || rect?.width || 0);
  const height = Number(host.clientHeight || rect?.height || 0);
  return {
    width: Number.isFinite(width) ? width : 0,
    height: Number.isFinite(height) ? height : 0,
  };
}

function dockviewHostCanAdoptLayout(host = dockviewLayoutState.host) {
  if (!host) return true;
  const {width, height} = dockviewHostLayoutSize(host);
  return width > 1 && height > 1;
}

function dockviewInstallHostResizeObserver(host, api) {
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(() => dockviewScheduleLayoutToHost(api, host));
    observer.observe(host);
    return {dispose: () => observer.disconnect()};
  }
  const resize = () => dockviewScheduleLayoutToHost(api, host);
  window.addEventListener('resize', resize);
  return {dispose: () => window.removeEventListener('resize', resize)};
}

function dockviewInstallTabPointerReorderFallback() {
  const track = event => {
    dockviewTrackPanePointerDrag(event);
  };
  const finish = event => {
    dockviewFinishTabPointerDrag(event);
    dockviewFinishPanePointerDrag(event);
  };
  document.addEventListener('pointerup', finish, true);
  document.addEventListener('pointercancel', finish, true);
  document.addEventListener('mouseup', finish, true);
  document.addEventListener('pointermove', track, true);
  document.addEventListener('mousemove', track, true);
  return {
    dispose() {
      document.removeEventListener('pointerup', finish, true);
      document.removeEventListener('pointercancel', finish, true);
      document.removeEventListener('mouseup', finish, true);
      document.removeEventListener('pointermove', track, true);
      document.removeEventListener('mousemove', track, true);
    },
  };
}

function dockviewPaneChromeDragExcluded(target) {
  return Boolean(target?.closest?.('.dv-tab, .dockview-pane-tab, [data-pane-drag], button, input, textarea, select, a'));
}

function dockviewSyncHeaderBackgroundDragSources() {
  if (!dockviewLayoutActive()) return;
  document.querySelectorAll('.dv-groupview').forEach(group => {
    const header = group.querySelector('.dv-tabs-and-actions-container');
    const infoBar = group.querySelector('.dockview-panel-content > .panel > .pane-info-bar, .dockview-panel-content > .panel > .panel-detail-row');
    const editorToolbar = group.querySelector('.dockview-panel-content > .file-editor-panel > .file-editor-toolbar');
    const slot = dockviewSlotForGroupElement(group);
    const draggable = Boolean(slot && !slotIsFileExplorerPane(slot) && activeItemForSide(slot));
    const syncDragSource = element => {
      if (!element) return;
      element.dataset.paneDragSlot = draggable ? slot : '';
      element.classList.toggle('pane-drag-source', draggable);
      if (element.__yolomuxPaneDragBound) return;
      element.__yolomuxPaneDragBound = true;
      const begin = event => {
        if (dockviewPaneChromeDragExcluded(event.target)) return;
        const sourceSlot = element.dataset.paneDragSlot || dockviewSlotForGroupElement(group);
        dockviewBeginPanePointerDrag(event, sourceSlot);
      };
      element.addEventListener('pointerdown', begin);
      element.addEventListener('mousedown', begin);
    };
    syncDragSource(header);
    syncDragSource(infoBar);
    syncDragSource(editorToolbar);
  });
}

function dockviewClearTabRowBreaks(tabsContainer) {
  Array.from(tabsContainer?.children || [])
    .filter(node => node.classList?.contains('dockview-tab-row-break'))
    .forEach(node => node.remove());
}

function dockviewSyncHeaderActionReservations() {
  if (!dockviewLayoutActive()) return;
  document.querySelectorAll('.dv-groupview').forEach(group => {
    const header = group.querySelector('.dv-tabs-and-actions-container');
    if (!header) return;
    const tabsContainer = header.querySelector('.dv-tabs-container');
    dockviewClearTabRowBreaks(tabsContainer);
    const tabs = Array.from(tabsContainer?.children || [])
      .filter(node => node.classList?.contains('dv-tab'));
    const actions = group.querySelector('.dockview-pane-header-actions:not([hidden])');
    const width = actions ? Math.ceil(appSpaceRect(actions).width || actions.offsetWidth || 0) : 0;
    const reservedWidth = width > 0 ? width + 8 : 0;
    const headerWidth = Math.floor(appSpaceRect(header).width || header.clientWidth || 0);
    const rootStyle = getComputedStyle(document.documentElement);
    const preferredTabWidth = Number.parseFloat(rootStyle.getPropertyValue('--pane-tab-width')) || 180;
    const minTabWidth = Number.parseFloat(rootStyle.getPropertyValue('--dockview-tab-min-inline-size')) || 64;
    const availableWidth = headerWidth > reservedWidth ? headerWidth - reservedWidth : headerWidth;
    const tabWidth = availableWidth > 0
      ? Math.min(Math.max(minTabWidth, preferredTabWidth), Math.max(minTabWidth, availableWidth))
      : Math.max(minTabWidth, preferredTabWidth);
    header.style.setProperty('--dockview-header-actions-reserved-inline-size', reservedWidth > 0 ? `${reservedWidth}px` : '0px');
    header.style.setProperty('--dockview-tab-inline-size', `${tabWidth}px`);
    if (!tabsContainer || tabs.length < 2 || reservedWidth <= 0 || headerWidth <= reservedWidth) return;
    const tabStyle = getComputedStyle(tabs[0]);
    const tabInlineGap = (Number.parseFloat(tabStyle.marginLeft) || 0) + (Number.parseFloat(tabStyle.marginRight) || 0);
    const tabOuterWidth = Math.max(1, tabWidth + tabInlineGap);
    const firstRowWidth = Math.max(0, headerWidth - reservedWidth);
    const firstRowCapacity = Math.max(1, Math.min(tabs.length, Math.floor((firstRowWidth + tabInlineGap) / tabOuterWidth)));
    if (firstRowCapacity >= tabs.length) return;
    const rowBreak = document.createElement('span');
    rowBreak.className = 'dockview-tab-row-break';
    rowBreak.setAttribute('aria-hidden', 'true');
    tabsContainer.insertBefore(rowBreak, tabs[firstRowCapacity]);
  });
}

function dockviewEnsureHost() {
  if (dockviewLayoutState.host?.isConnected) return dockviewLayoutState.host;
  movePanelsToPool();
  grid.innerHTML = '';
  const host = document.createElement('section');
  host.id = dockviewRootId;
  host.className = 'yolomux-dockview';
  grid.appendChild(host);
  dockviewLayoutState.host = host;
  return host;
}

function dockviewInit() {
  if (dockviewLayoutState.api || !dockviewLayoutEnabled()) return dockviewLayoutState.api;
  const core = dockviewCore();
  const host = dockviewEnsureHost();
  const api = core.createDockview(host, {
    className: 'yolomux-dockview-core',
    createComponent: () => createDockviewPanelRenderer(),
    createRightHeaderActionComponent: () => createDockviewHeaderActionsRenderer(),
    createTabComponent: () => createDockviewTabRenderer(),
    defaultRenderer: dockviewPanelRenderer,
    disableFloatingGroups: true,
    dndStrategy: 'pointer',
    noPanelsOverlay: 'emptyGroup',
    scrollbars: 'native',
    singleTabMode: 'default',
    tabGroupAccent: 'off',
    theme: dockviewThemeForApp(),
    getTabContextMenuItems: () => [],
    getTabGroupChipContextMenuItems: () => [],
  });
  dockviewLayoutState.api = api;
  dockviewLayoutToHost(api, host);
  dockviewLayoutState.disposables = [
    dockviewInstallRootBoundaryDropFallback(),
    dockviewInstallFileDropBridge(host),
    dockviewInstallHostResizeObserver(host, api),
    dockviewInstallTabPointerReorderFallback(),
    api.onDidLayoutChange(() => queueDockviewLayoutAdoption()),
    api.onDidRemoveGroup?.(group => dockviewHandleRemovedGroup(group)),
    api.onDidActivePanelChange(panel => {
      if (dockviewLayoutState.applyingFromLayout) return;
      const item = panel?.id || '';
      if (!item) return;
      setFocusedPanelItem(item);
    }),
    api.onWillShowOverlay(event => dockviewTrackRootBoundaryOverlay(event)),
    api.onWillDrop(event => {
      // Tab drops are handled here (or committed by dockview itself); stamp the gesture so the pinned
      // pointer-reorder FALLBACK stands down instead of double-applying (see dockviewFinishTabPointerDrag).
      if (event?.kind === 'tab') dockviewLayoutState.tabDropHandledAt = Date.now();
      dockviewSetInvalidTabDropPreview(false);
      const edgeReorder = dockviewTabEdgeReorderIntent(event);
      if (edgeReorder) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void moveSessionToSlot(edgeReorder.item, edgeReorder.targetSlot, edgeReorder.sourceSlot, edgeReorder.insertIndex);
        });
        return;
      }
      if (dockviewTabDropWouldNoop(event)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        return;
      }
      if (dockviewTabDropViolatesPinnedPartition(event)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        return;
      }
      const stripEndDrop = dockviewTabStripEndDropIntent(event);
      if (stripEndDrop) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void moveSessionToSlot(stripEndDrop.item, stripEndDrop.targetSlot, stripEndDrop.sourceSlot, stripEndDrop.insertIndex);
        });
        return;
      }
      const rootIntent = dockviewRootBoundaryDropIntent(event);
      if (dockviewPinnedTabRootBoundaryViolation(rootIntent)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        return;
      }
      if (rootIntent) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void splitSessionAtLayoutBoundary(rootIntent.item, rootIntent.zone, rootIntent.sourceSlot);
        });
        return;
      }
      const paneIntent = dockviewPaneContentDropIntent(event);
      if (paneIntent) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void splitSessionAtSlot(paneIntent.item, paneIntent.targetSlot, paneIntent.zone, paneIntent.sourceSlot);
        });
        return;
      }
      if (dockviewShouldSuppressPaneContentDrop(event) || dockviewShouldSuppressReservedRootBoundary(event)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        return;
      }
      dockviewLayoutState.pendingRootBoundaryDrop = null;
      dockviewClearRootBoundaryPreview();
      const targetSlot = dockviewSlotForGroupId(event.group?.id || '');
      const targetActive = targetSlot ? activeItemForSide(targetSlot) : '';
      if (event.position === 'center' && isFileExplorerItem(targetActive)) event.preventDefault();
    }),
  ];
  return api;
}

function dockviewDispose() {
  for (const disposable of dockviewLayoutState.disposables) disposable?.dispose?.();
  dockviewLayoutState.disposables = [];
  dockviewLayoutState.api?.dispose?.();
  dockviewLayoutState.api = null;
  dockviewLayoutState.host = null;
  if (dockviewLayoutState.hostLayoutFrame) cancelAnimationFrame(dockviewLayoutState.hostLayoutFrame);
  dockviewLayoutState.hostLayoutFrame = 0;
  dockviewLayoutState.lastHostLayoutSignature = '';
  dockviewLayoutState.lastAppliedLayoutSignature = '';
  dockviewLayoutState.groupSlots.clear();
}

function renderPanelsDockview(previousActive = [], options = {}) {
  if (!dockviewLayoutEnabled()) return false;
  const api = dockviewInit();
  if (!api) return false;
  const activePaneCount = layoutSlotKeys().filter(side => activeItemForSide(side) || paneIsPlaceholder(side)).length;
  grid.className = `grid dockview-grid ${activePaneCount === 1 ? 'full' : ''} ${activePaneCount === 0 ? 'empty' : ''}`.trim();
  dockviewEnsureHost();
  api.updateOptions?.({theme: dockviewThemeForApp()});
  dockviewLayoutToHost(api);
  const signature = layoutSlotsSignature(layoutSlots);
  if (!dockviewLayoutState.adoptingFromDockview && dockviewLayoutState.lastAppliedLayoutSignature !== signature) {
    dockviewLoadLayout(layoutSlots);
  }
  dockviewRefreshTabs();
  dockviewSyncMountedPanels();
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
  return true;
}

function syncActivePanelsDockview(previousActive = []) {
  renderPanelsDockview(previousActive, {prune: false});
}

function dockviewLoadLayout(slots = layoutSlots) {
  const api = dockviewLayoutState.api;
  if (!api) return;
  const items = paneItems(slots);
  dockviewLayoutState.applyingFromLayout = true;
  try {
    if (!items.length) {
      api.clear();
      dockviewLayoutState.lastAppliedLayoutSignature = layoutSlotsSignature(slots);
      return;
    }
    api.fromJSON(dockviewJsonFromLayoutSlots(slots), {reuseExistingPanels: true});
    dockviewLayoutState.lastAppliedLayoutSignature = layoutSlotsSignature(slots);
    dockviewSyncMountedPanels();
  } finally {
    dockviewLayoutState.applyingFromLayout = false;
  }
}

function dockviewJsonFromLayoutSlots(slots = layoutSlots) {
  const tree = slots?.[layoutTreeKey] || legacyLayoutTree(slots);
  const rootOrientation = dockviewOrientationForSplit(tree?.split === 'column' ? 'column' : 'row');
  const panelItems = paneItems(slots);
  const size = dockviewHostLayoutSize(dockviewLayoutState.host || grid);
  const viewport = appViewport();
  const panels = {};
  for (const item of panelItems) {
    panels[item] = {
      id: item,
      contentComponent: dockviewContentComponentName,
      tabComponent: dockviewTabComponentName,
      title: itemLabel(item),
      renderer: dockviewPanelRenderer,
      params: {item},
      minimumWidth: minWidthForLayoutItem(item),
      minimumHeight: minSplitPaneHeightPx(),
    };
  }
  return {
    grid: {
      root: dockviewSerializedNodeFromLayout(tree, slots, rootOrientation),
      height: Math.max(DOCKVIEW_MIN_LAYOUT_HEIGHT, Math.round(size.height || viewport.height || 0)),
      width: Math.max(DOCKVIEW_MIN_LAYOUT_WIDTH, Math.round(size.width || viewport.width || 0)),
      orientation: rootOrientation,
    },
    panels,
    activeGroup: slotForItem(focusedPanelItem, slots) || layoutSlotKeys(slots)[0],
  };
}

function dockviewSerializedNodeFromLayout(node, slots, orientation, weight = SERIALIZED_WEIGHT_BASE) {
  if (!node || node.slot) {
    return {
      type: 'branch',
      data: [dockviewSerializedLeaf(node?.slot || layoutSlotKeys(slots)[0] || 'left', slots, weight)],
      size: Math.max(1, Math.round(weight)),
    };
  }
  return dockviewSerializedNodeContentFromLayout(node, slots, orientation, weight);
}

function dockviewSerializedNodeContentFromLayout(node, slots, orientation, weight = SERIALIZED_WEIGHT_BASE) {
  if (!node || node.slot) return dockviewSerializedLeaf(node?.slot || layoutSlotKeys(slots)[0] || 'left', slots, weight);
  const direction = dockviewSplitForOrientation(orientation);
  const parts = flattenLayoutNodeForDirection(node, slots, direction);
  if (parts.length === 1) {
    const part = parts[0];
    return dockviewSerializedNodeContentFromLayout(part.node, slots, dockviewOppositeOrientation(orientation), weight);
  }
  const totalWeight = parts.reduce((sum, part) => sum + part.weight, 0) || 1;
  return {
    type: 'branch',
    data: parts.map(part => dockviewSerializedNodeContentFromLayout(
      part.node,
      slots,
      dockviewOppositeOrientation(orientation),
      Math.max(1, Math.round((part.weight / totalWeight) * weight)),
    )),
    size: Math.max(1, Math.round(weight)),
  };
}

function flattenLayoutNodeForDirection(node, slots, direction, weight = 1) {
  if (!node || node.slot || node.split !== direction) return [{node, weight}];
  const pct = splitPercent(node.pct) / 100;
  const children = node.children || [];
  return [
    ...flattenLayoutNodeForDirection(children[0], slots, direction, weight * pct),
    ...flattenLayoutNodeForDirection(children[1], slots, direction, weight * (1 - pct)),
  ];
}

function dockviewSerializedLeaf(slot, slots, weight = SERIALIZED_WEIGHT_BASE) {
  const tabs = paneTabs(slot, slots);
  const groupId = slot || nextLayoutSlot(slots);
  dockviewLayoutState.groupSlots.set(groupId, groupId);
  return {
    type: 'leaf',
    data: {
      id: groupId,
      views: tabs.slice(),
      activeView: activeItemForSide(slot, slots) || tabs[0],
      hideHeader: tabs.length === 1 && isFileExplorerItem(tabs[0]),
    },
    size: Math.max(1, Math.round(weight)),
    visible: true,
  };
}

function queueDockviewLayoutAdoption() {
  if (dockviewLayoutState.applyingFromLayout || dockviewLayoutState.adoptingFromDockview) return;
  if (dockviewLayoutState.syncQueued) return;
  dockviewLayoutState.syncQueued = true;
  queueMicrotask(adoptDockviewLayout);
}

function adoptDockviewLayout() {
  dockviewLayoutState.syncQueued = false;
  const api = dockviewLayoutState.api;
  if (!api || dockviewLayoutState.applyingFromLayout) return;
  if (!dockviewHostCanAdoptLayout()) return;
  let next = layoutSlotsFromDockviewJson(api.toJSON());
  const previousFinderSlot = slotForItem(fileExplorerItemId, layoutSlots);
  if (previousFinderSlot && !itemInLayout(fileExplorerItemId, next)) {
    next = layoutWithFileExplorerDockedLeft(next, {preferredSlot: previousFinderSlot});
    dockviewLayoutState.reloadAfterAdoption = true;
  }
  if (!layoutHasRestorableContent(next)) return;
  const nextSignature = layoutSlotsSignature(next);
  if (nextSignature === layoutSlotsSignature(layoutSlots)) {
    dockviewRefreshTabs();
    dockviewSyncMountedPanels();
    return;
  }
  dockviewLayoutState.lastAppliedLayoutSignature = nextSignature;
  dockviewLayoutState.adoptingFromDockview = true;
  try {
    applyLayoutSlots(next, {prune: false});
    if (dockviewLayoutState.reloadAfterAdoption) {
      dockviewLayoutState.reloadAfterAdoption = false;
      dockviewLoadLayout(layoutSlots);
    }
  } finally {
    dockviewLayoutState.reloadAfterAdoption = false;
    dockviewLayoutState.adoptingFromDockview = false;
  }
}

function layoutSlotsFromDockviewJson(data, previous = layoutSlots) {
  const next = emptyLayoutSlots();
  const usedSlots = new Set();
  const parse = (node, orientation) => {
    if (!node) return null;
    if (node.type === 'leaf') {
      const group = node.data || {};
      const slot = dockviewSlotForGroupId(group.id, usedSlots);
      usedSlots.add(slot);
      let tabs = (Array.isArray(group.views) ? group.views : [])
        .map(resolveLayoutItem)
        .filter(item => isLayoutItem(item));
      let activeView = resolveLayoutItem(group.activeView);
      const previousTabs = paneTabs(slot, previous);
      if (!tabs.length && previousTabs.includes(fileExplorerItemId)) {
        tabs = previousTabs.slice();
        activeView = activeItemForSide(slot, previous) || fileExplorerItemId;
        dockviewLayoutState.reloadAfterAdoption = true;
      }
      next[slot] = paneStateWithTabs(tabs, activeView);
      return paneHasLayoutContent(slot, next) ? leafNode(slot) : null;
    }
    const children = (Array.isArray(node.data) ? node.data : [])
      .map(child => ({
        node: parse(child, dockviewOppositeOrientation(orientation)),
        size: Number(child?.size) || 1,
      }))
      .filter(child => child.node);
    return dockviewLayoutTreeFromChildren(children, dockviewSplitForOrientation(orientation));
  };
  next[layoutTreeKey] = parse(data?.grid?.root, data?.grid?.orientation || 'HORIZONTAL');
  preserveDockviewDockedFileExplorerSplit(next, previous);
  return compactLayoutSlots(next);
}

function dockviewGroupId(group) {
  return String(group?.id || group?.api?.id || group?.model?.id || group?.data?.id || '');
}

function dockviewRemovedGroupItems(group) {
  const raw = [
    ...(Array.isArray(group?.panels) ? group.panels : []),
    ...(Array.isArray(group?.api?.panels) ? group.api.panels : []),
    ...(Array.isArray(group?.model?.panels) ? group.model.panels : []),
  ];
  return raw
    .map(panel => resolveLayoutItem(panel?.id || panel?.api?.id || panel?.data?.id || panel))
    .filter(item => isLayoutItem(item));
}

function dockviewHandleRemovedGroup(group) {
  const items = dockviewRemovedGroupItems(group);
  const groupId = dockviewGroupId(group);
  const slot = dockviewLayoutState.groupSlots.get(groupId) || layoutSlotName(groupId);
  if (!items.includes(fileExplorerItemId) && !(slot && paneTabs(slot).includes(fileExplorerItemId))) return;
  dockviewLayoutState.reloadAfterAdoption = true;
  queueDockviewLayoutAdoption();
}

function preserveDockviewDockedFileExplorerSplit(next, previous = layoutSlots) {
  const previousRoot = previous?.[layoutTreeKey];
  const nextRoot = next?.[layoutTreeKey];
  const previousDocked = dockedFileExplorerRootSplit(previousRoot, previous);
  const nextDocked = dockedFileExplorerRootSplit(nextRoot, next);
  if (!previousDocked || !nextDocked || !nextRoot) return;
  if (dockviewLayoutContentSignature(next) === dockviewLayoutContentSignature(previous)) {
    preserveDockviewContentSplitPercentagesAfterDockResize(nextRoot, previousRoot, nextDocked, previousDocked);
    return;
  }
  const finderPct = previousDocked.finderIndex === 0 ? previousDocked.pct : 100 - previousDocked.pct;
  const preservedPct = nextDocked.finderIndex === 0 ? finderPct : 100 - finderPct;
  if (Math.abs((Number(nextRoot.pct) || 0) - preservedPct) > SPLIT_PCT_EPSILON) {
    nextRoot.pct = preservedPct;
    dockviewLayoutState.reloadAfterAdoption = true;
  }
}

function preserveDockviewContentSplitPercentagesAfterDockResize(nextRoot, previousRoot, nextDocked, previousDocked) {
  const previousFinderPct = previousDocked.finderIndex === 0 ? previousDocked.pct : 100 - previousDocked.pct;
  const nextFinderPct = nextDocked.finderIndex === 0 ? nextDocked.pct : 100 - nextDocked.pct;
  if (Math.abs(nextFinderPct - previousFinderPct) <= SPLIT_PCT_EPSILON) return;
  const nextContent = nextRoot.children?.[nextDocked.contentIndex];
  const previousContent = previousRoot.children?.[previousDocked.contentIndex];
  if (copyLayoutSplitPercentagesByShape(nextContent, previousContent)) {
    dockviewLayoutState.reloadAfterAdoption = true;
  }
}

function copyLayoutSplitPercentagesByShape(target, source) {
  if (!target || !source || target.slot || source.slot || target.split !== source.split) return false;
  let changed = false;
  const sourcePct = splitPercent(source.pct);
  if (Math.abs(splitPercent(target.pct) - sourcePct) > SPLIT_PCT_EPSILON) {
    target.pct = sourcePct;
    changed = true;
  }
  const targetChildren = target.children || [];
  const sourceChildren = source.children || [];
  for (let index = 0; index < Math.min(targetChildren.length, sourceChildren.length); index += 1) {
    changed = copyLayoutSplitPercentagesByShape(targetChildren[index], sourceChildren[index]) || changed;
  }
  return changed;
}

function dockviewLayoutContentSignature(slots = layoutSlots) {
  const nodeSignature = node => {
    if (!node) return '';
    if (node.slot) return `S:${node.slot}`;
    return `${node.split || ''}:[${(node.children || []).map(nodeSignature).join(',')}]`;
  };
  const paneSignature = layoutSlotKeys(slots)
    .slice()
    .sort()
    .map(slot => `${slot}:${paneTabs(slot, slots).slice().sort().join(',')}`)
    .join('|');
  return `${nodeSignature(slots?.[layoutTreeKey])}::${paneSignature}`;
}

function dockviewLayoutTreeFromChildren(children, direction) {
  if (!children.length) return null;
  if (children.length === 1) return children[0].node;
  const [first, ...rest] = children;
  const restSize = rest.reduce((sum, child) => sum + child.size, 0);
  const total = first.size + restSize;
  const secondNode = dockviewLayoutTreeFromChildren(rest, direction);
  return splitNode(direction, first.node, secondNode, total ? (first.size / total) * 100 : defaultSplitPercent);
}

function dockviewSlotForGroupId(groupId, usedSlots = new Set()) {
  const text = String(groupId || '');
  const existing = dockviewLayoutState.groupSlots.get(text);
  if (existing && !usedSlots.has(existing)) return existing;
  if (layoutSlotName(text) && !usedSlots.has(text)) {
    dockviewLayoutState.groupSlots.set(text, text);
    return text;
  }
  let slot = nextLayoutSlot({...layoutSlots, ...Object.fromEntries(Array.from(usedSlots).map(key => [key, emptyPaneState()]))});
  while (usedSlots.has(slot)) slot = nextLayoutSlot({...layoutSlots, [slot]: emptyPaneState()});
  dockviewLayoutState.groupSlots.set(text || slot, slot);
  return slot;
}

function dockviewEnsureMountedTerminal(item, panel) {
  if (!isTmuxSession(item)) return;
  queueMicrotask(() => {
    if (!panel?.isConnected || !itemInLayout(item)) return;
    const slot = slotForItem(item);
    if (slot && activeItemForSide(slot) !== item) return;
    if (typeof ensureTerminalRunning === 'function') void ensureTerminalRunning(item);
    if (typeof scheduleFit === 'function') scheduleFit(item);
  });
}

function createDockviewPanelRenderer() {
  const element = document.createElement('div');
  element.className = 'dockview-panel-content';
  let item = '';
  let panel = null;
  const mount = params => {
    item = params?.params?.item || params?.api?.id || item;
    if (!isLayoutItem(item)) return;
    panel = getOrCreatePanel(item);
    const slot = slotForItem(item) || dockviewSlotForGroupId(params?.api?.group?.id || '');
    updatePanelSlot(panel, item, slot);
    element.replaceChildren(panel);
    renderAttachedPanelContent(item);
    restorePaneViewState(item, panel);
    updatePanelInactiveOverlays();
    dockviewEnsureMountedTerminal(item, panel);
  };
  const pool = () => {
    if (!panel || panel.parentElement !== element) return;
    capturePaneViewState(item, panel);
    panel.classList.remove('active-pane');
    panel.dataset.slot = '';
    panelPool.appendChild(panel);
  };
  return {
    element,
    init: mount,
    update: event => mount({params: event?.params || {item}, api: {id: item}}),
    onShow: () => mount({params: {item}, api: {id: item}}),
    onHide: pool,
    dispose: pool,
    layout: () => {
      if (isTmuxSession(item)) {
        dockviewEnsureMountedTerminal(item, panel);
        scheduleFit(item);
      }
    },
  };
}

function dockviewHeaderActionsHtml(item) {
  if (!isLayoutItem(item) || isFileExplorerItem(item)) return '';
  const paneHandle = paneDragHandleHtml(item);
  if (isTmuxSession(item)) return `${paneHandle}${panelControlsHtml(item)}`;
  if (isFileEditorItem(item)) {
    return `${paneHandle}${paneFrameControlsGroupHtml(item, {
      groupClass: 'file-editor-frame-controls',
      actions: false,
      minimize: true,
      expand: true,
      close: true,
      closeClass: 'file-editor-panel-close',
      closeTitle: t('editor.closePane'),
      closeLabel: t('editor.closePane'),
    })}`;
  }
  if (isVirtualItem(item)) return `${paneHandle}${virtualPanelControlsHtml(item)}`;
  return '';
}

function paneDragHandleHtml(item) {
  const slot = slotForItem(item);
  if (!slot || slotIsFileExplorerPane(slot)) return '';
  return `<button type="button" class="tab pane-drag-handle" data-pane-drag="${esc(slot)}" draggable="true" title="${esc(t('pane.drag'))}" aria-label="${esc(t('pane.drag'))}"></button>`;
}

function handleDockviewHeaderActionClick(event, fallbackItem = '') {
  const button = event.target?.closest?.('button');
  if (!button) return;
  const item = button.dataset.tab || button.dataset.windowSession || button.dataset.detailToggle
    || button.dataset.paneActions || button.dataset.paneMinimize || button.dataset.paneExpand
    || button.dataset.paneClose || fallbackItem;
  if (!item) return;
  if (button.dataset.tab !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    const currentName = button.dataset.tabName;
    const nextName = currentName !== 'terminal' && button.classList.contains(CLS.active) ? 'terminal' : currentName;
    activateTab(button.dataset.tab, nextName, {userInitiated: true});
    return;
  }
  if (button.dataset.windowDir !== undefined || button.dataset.windowIndex !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    handleWindowStepButtonClick(event);
    return;
  }
  if (button.dataset.detailToggle !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    const panel = document.getElementById(panelDomId(item));
    if (panel) setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
    return;
  }
  if (button.dataset.paneActions !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    const rect = button.getBoundingClientRect();
    showSessionContextMenu(button.dataset.paneActions || item, rect.left, rect.bottom + 4);
    return;
  }
  if (button.dataset.paneMinimize !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    minimizePaneFromLayout(button.dataset.paneMinimize || item);
    return;
  }
  if (button.dataset.paneExpand !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    expandPaneFromLayout(button.dataset.paneExpand || item);
    return;
  }
  if (button.dataset.paneClose !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    removePaneFromLayout(button.dataset.paneClose || item);
  }
}

function createDockviewHeaderActionsRenderer() {
  const element = document.createElement('div');
  element.className = 'dockview-pane-header-actions';
  let group = null;
  let activeItem = '';
  let disposables = [];
  const render = () => {
    activeItem = resolveLayoutItem(group?.activePanel?.id || '');
    const html = dockviewHeaderActionsHtml(activeItem);
    element.hidden = !html;
    element.innerHTML = html;
    updatePanelWindowStepButtons(activeItem, transcriptMeta.sessions?.[activeItem]);
    const panel = document.getElementById(panelDomId(activeItem));
    if (panel) {
      updatePaneExpandButton(panel, activeItem);
      syncPanelDetailsToggleState(panel);
    }
  };
  const dispose = () => {
    for (const disposable of disposables) disposable?.dispose?.();
    disposables = [];
  };
  element.addEventListener('dragstart', event => {
    const handle = event.target?.closest?.('[data-pane-drag]');
    if (!handle) return;
    event.stopPropagation();
    startPaneDrag(event, handle.dataset.paneDrag || slotForItem(activeItem));
  });
  element.addEventListener('dragend', endSessionDrag);
  element.addEventListener('click', event => handleDockviewHeaderActionClick(event, activeItem));
  element.__yolomuxDockviewRefresh = render;
  return {
    element,
    init: params => {
      dispose();
      group = params?.group || null;
      disposables = [
        params?.api?.onDidActivePanelChange?.(render),
        params?.api?.onDidActiveChange?.(render),
      ].filter(Boolean);
      render();
    },
    update: render,
    dispose,
  };
}

function captureDockviewPreviousPaneBeforeTabActivation(tabElement, targetItem) {
  const group = tabElement?.closest?.('.dv-groupview');
  const slot = group && typeof dockviewSlotForGroupElement === 'function' ? dockviewSlotForGroupElement(group) : null;
  const previous = slot ? activeItemForSide(slot) : focusedPanelItem;
  if (previous && previous !== targetItem) capturePaneViewStateForItemIfPresent(previous);
}

function createDockviewTabRenderer() {
  const element = document.createElement('div');
  element.className = 'pane-tab dockview-pane-tab';
  element.role = 'button';
  element.tabIndex = 0;
  let item = '';
  let api = null;
  let disposables = [];
  const render = () => {
    if (!item) return;
    syncDockviewTabShell(element, item, api);
    if (paneTabShouldPreserve(element)) {
      const popover = paneTabPopoverForAnchor(element);
      if (popover) positionPaneTabPopover(element, popover);
      if (isFileEditorItem(item)) refreshFileTabPopover(element, item);
      return;
    }
    cleanupDetachedPaneTabPopover(element);
    element.innerHTML = dockviewPaneTabHtml(item);
    if (isFileEditorItem(item)) {
      bindFilePopoverActions(element);
      bindPaneTabPopover(element, item);
    } else if (!isVirtualItem(item)) {
      bindPaneTabPopover(element, item);
    }
  };
  const dispose = () => {
    cleanupDetachedPaneTabPopover(element);
    for (const disposable of disposables) disposable?.dispose?.();
    disposables = [];
  };
  const commitExplicitTabInteraction = () => {
    if (isTmuxSession(item)) noteFileExplorerChangesSessionInteraction(item);
    setFocusedPanelItem(item, {userInitiated: true});
  };
  element.__yolomuxDockviewRefresh = render;
  element.addEventListener('pointerdown', event => {
    dragTimingReset();
    dragTimingMark('pointerdown');
    if (event.target.closest('[data-pane-tab-close], [data-auto-session]')) event.stopPropagation();
    else {
      captureDockviewPreviousPaneBeforeTabActivation(element, item);
      dockviewBeginTabPointerDrag(event, item);
    }
  });
  element.addEventListener('mousedown', event => {
    if (event.target.closest('[data-pane-tab-close], [data-auto-session]')) return;
    captureDockviewPreviousPaneBeforeTabActivation(element, item);
    dockviewBeginTabPointerDrag(event, item);
  });
  element.addEventListener('click', async event => {
    const close = event.target.closest('[data-pane-tab-close]');
    if (close) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation?.();
      if (isFileEditorItem(item)) closeFileTab(fileItemPath(item), {item});
      else removeSessionFromLayout(item);
      return;
    }
    const autoTarget = event.target.closest('[data-auto-session]');
    if (autoTarget) {
      event.preventDefault();
      event.stopPropagation();
      const shouldRefocus = activeSessions.includes(item);
      await toggleAutoApprove(autoTarget.dataset.autoSession);
      if (shouldRefocus) focusPanel(item);
      return;
    }
    captureDockviewPreviousPaneBeforeTabActivation(element, item);
    commitExplicitTabInteraction();
  });
  element.addEventListener('keydown', event => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    captureDockviewPreviousPaneBeforeTabActivation(element, item);
    commitExplicitTabInteraction();
    api?.setActive?.();
  });
  element.addEventListener('dblclick', event => {
    if (event.target.closest('[data-auto-session], [data-pane-tab-close]')) return;
    event.preventDefault();
    event.stopPropagation();
    beginPaneTabRename(element, item);
  });
  element.addEventListener('contextmenu', event => {
    if (!isPinnableTab(item) && !isTmuxSession(item)) return;
    event.preventDefault();
    event.stopPropagation();
    showTabContextMenu(item, event.clientX, event.clientY, {tab: element});
  });
  return {
    element,
    init: params => {
      dispose();
      item = params?.params?.item || params?.api?.id || '';
      api = params?.api || null;
      disposables = [
        api?.onDidActiveChange?.(render),
        api?.onDidTitleChange?.(render),
        api?.onDidParametersChange?.(parameters => {
          item = parameters?.item || item;
          render();
        }),
      ].filter(Boolean);
      render();
    },
    update: event => {
      if (event?.params?.item) item = event.params.item;
      render();
    },
    dispose,
  };
}

function syncDockviewTabShell(tab, item, api = null) {
  tab.dataset.paneTab = item;
  syncDockviewTabActiveClass(tab, api);
  tab.classList.toggle('file-missing', isFileEditorItem(item) && openFileIsMissing(fileItemPath(item)));
  tab.classList.toggle('pinned-tab', tabIsPinned(item));
  applySessionStateClasses(tab, isVirtualItem(item) ? null : sessionState(item, transcriptMeta.sessions?.[item]));
  tab.setAttribute('aria-label', dockviewTabAriaLabel(item));
}

function dockviewPaneTabHtml(item) {
  // Shares paneTabInnerHtml with the DOM-building createPaneTab so pin/close/popover markup parity is
  // enforced in one place (the Dockview tab renderer just needs the string form).
  return paneTabInnerHtml(item);
}

function dockviewTabAriaLabel(item) {
  if (isFileEditorItem(item)) {
    const missing = openFileIsMissing(fileItemPath(item)) ? ' missing on disk' : '';
    return `${itemLabel(item)} ${fileItemPath(item)}${missing}`;
  }
  const type = tabTypeForItem(item);
  if (type) return itemLabel(item);
  return `${sessionLabel(item)} ${sessionWorkDescription(item, transcriptMeta.sessions?.[item], 140)}`.trim();
}

function dockviewRefreshTabs() {
  if (!dockviewLayoutActive()) return;
  document.querySelectorAll('.dockview-pane-tab').forEach(tab => {
    syncDockviewTabActiveClass(tab);
    tab.__yolomuxDockviewRefresh?.();
  });
  document.querySelectorAll('.dockview-pane-header-actions').forEach(actions => {
    actions.__yolomuxDockviewRefresh?.();
  });
  dockviewSyncHeaderBackgroundDragSources();
  dockviewSyncHeaderActionReservations();
  scheduleAgentWindowActivityAnimationSync();
}

function syncDockviewTabActiveClass(tab, api = null) {
  const dockviewActive = tab?.closest?.('.dv-tab')?.classList?.contains('dv-active-tab') === true;
  tab?.classList?.toggle(CLS.active, api?.isActive === true || dockviewActive);
}

function dockviewSyncMountedPanels() {
  if (!dockviewLayoutActive()) return;
  for (const item of activePaneItems()) {
    const panel = panelNodes.get(item);
    if (!panel?.isConnected) continue;
    const slot = slotForItem(item);
    if (slot) updatePanelSlot(panel, item, slot);
    renderAttachedPanelContent(item);
  }
  updatePanelInactiveOverlays();
}

function hideDockviewInnerPaneTabs(panel) {
  if (!panel || !dockviewLayoutEnabled()) return false;
  panel.classList.remove('dockview-inner-head-collapsed');
  const head = panel.querySelector('.panel-head');
  const strip = head?.querySelector('.pane-tabs');
  if (!strip) return true;
  if (!head.classList.contains('file-explorer-head')) {
    head.hidden = true;
    head.classList.add('dockview-inner-head-hidden');
    panel.classList.add('dockview-inner-head-collapsed');
  }
  strip.hidden = true;
  strip.replaceChildren();
  return true;
}

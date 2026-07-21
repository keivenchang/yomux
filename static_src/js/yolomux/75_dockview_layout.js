const dockviewContentComponentName = 'yolomux-panel';
const dockviewTabComponentName = 'yolomux-tab';
const dockviewPanelRenderer = 'onlyWhenVisible';
const dockviewRootId = 'dockviewRoot';
const dockviewEmptyPaneItemPrefix = '__dockview-empty-pane__:';
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
  pendingLoadFrame: 0,
  pendingCompletionFrame: 0,
  lastHostLayoutSignature: '',
  lastAppliedLayoutSignature: '',
  lastAppliedActiveOnlySignature: '',
  groupSlots: new Map(),
  pendingRootBoundaryDrop: null,
  reloadAfterAdoption: false,
  tabPointerDrag: null,
  tabContentInteractionSuppressedUntil: 0,
  tabDropHandledAt: 0,
  panePointerDrag: null,
  panePointerDragSuppressedUntil: 0,
  tabActivationPerf: null,
  pendingUserPanelActivation: '',
};

function dockviewEmptyPaneItem(slot) {
  return `${dockviewEmptyPaneItemPrefix}${encodeURIComponent(String(slot || ''))}`;
}

function dockviewEmptyPaneSlot(item) {
  const text = String(item || '');
  if (!text.startsWith(dockviewEmptyPaneItemPrefix)) return null;
  try {
    return decodeURIComponent(text.slice(dockviewEmptyPaneItemPrefix.length)) || null;
  } catch (_) {
    return null;
  }
}

function dockviewBeginTabActivationPerf(item) {
  const previous = dockviewLayoutState.tabActivationPerf;
  if (previous?.frame) cancelAnimationFrame(previous.frame);
  dockviewLayoutState.tabActivationPerf = {
    item: String(item || ''),
    token: clientPerfStart('tabActivationPaint'),
    frame: 0,
  };
}

function dockviewFinishTabActivationPerf(item) {
  const state = dockviewLayoutState.tabActivationPerf;
  if (!state || state.item !== String(item || '') || state.frame) return;
  state.frame = requestAnimationFrame(() => {
    if (dockviewLayoutState.tabActivationPerf !== state) return;
    const slot = slotForItem(state.item);
    if (slot && activeItemForSide(slot) === state.item) {
      clientPerfEnd(state.token, {nodes: document.querySelectorAll?.('.file-tree-row[data-tabber-type]')?.length || 0});
    }
    dockviewLayoutState.tabActivationPerf = null;
  });
}

function dockviewCommitPanelActivation(item, options = {}) {
  const panelItem = String(item || '');
  if (!panelItem) return false;
  const pendingUserGesture = dockviewLayoutState.pendingUserPanelActivation === panelItem;
  if (pendingUserGesture) dockviewLayoutState.pendingUserPanelActivation = '';
  const userInitiated = options.userInitiated === true || pendingUserGesture;
  if (isTmuxSession(panelItem) && userInitiated && focusedPanelItem !== panelItem) {
    noteFileExplorerChangesSessionInteraction(panelItem);
  }
  setFocusedPanelItem(panelItem, {userInitiated});
  dockviewFinishTabActivationPerf(panelItem);
  return true;
}

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

function dockviewSplitLayoutHasMinimumSize(zone, rect = dockviewLayoutState.host?.getBoundingClientRect?.()) {
  if (!layoutSplitZone(zone)) return false;
  if (!rect) return false;
  if (zone === 'left' || zone === 'right') {
    return (Number(rect.width) || 0) >= DOCKVIEW_MIN_LAYOUT_WIDTH;
  }
  return (Number(rect.height) || 0) >= DOCKVIEW_MIN_LAYOUT_HEIGHT;
}

function dockviewRootBoundaryDropIntent(event) {
  if (narrowSingleColumnMode()) return null;
  const tabPointerDrag = dockviewLayoutState.tabPointerDrag;
  if (event?.nativeEvent && tabPointerDrag?.item) {
    // Dockview labels the tab strip at the top of an outermost pane as a `top` edge. That is
    // useful for a deliberate root drop, but wrong for an ordinary tab move across that strip.
    // The pointer gesture owns the decision while it is live: it only returns a root intent when
    // the drag actually reaches an eligible root boundary. Falling through to Dockview's broad
    // overlay here would recreate the false top-root drop after the shared pointer resolver
    // rejected it.
    return dockviewTabPointerRootBoundaryIntentWithMemory(event.nativeEvent, tabPointerDrag);
  }
  if (!['content', 'edge', 'tab'].includes(event?.kind) || !layoutSplitZone(event.position)) return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  if (!isLayoutItem(item)) return null;
  const nativeEvent = event.nativeEvent;
  const target = rootBoundaryLayoutTarget();
  const rect = target?.rect || dockviewLayoutState.host?.getBoundingClientRect?.();
  if (!nativeEvent || !rect) return null;
  const zone = rootBoundaryDropZoneForEvent(nativeEvent, rect, event.position);
  if (!layoutSplitZone(zone)) return null;
  if (!dockviewSplitLayoutHasMinimumSize(zone, rect)) return null;
  return {
    item,
    zone,
    sourceSlot: slotForItem(item) || dockviewSlotForGroupId(data?.groupId || ''),
    targetRect: rect,
    targetNode: target?.node,
  };
}

function dockviewPaneContentDropInfo(event) {
  if (event?.kind !== 'content' || !event.group) return null;
  const targetSlot = dockviewSlotForGroupId(event.group.id || '');
  const targetRect = targetSlot ? layoutSlotScreenRect(targetSlot) : null;
  const fileSurfaceContent = event.nativeEvent?.target?.closest?.(
    '.file-explorer-tree-panel, .file-explorer-changes-panel, .file-tree-row',
  );
  // A real Finder/Differ row is a center-stack target. Dockview's broad edge overlay can classify
  // the same pointer as top/bottom; accepting that would turn a row drop into an accidental Side
  // split. Vertical Side splits remain available from the tab menu and actual pane-edge chrome.
  const zone = narrowSingleColumnMode() || fileSurfaceContent
    ? 'middle'
    : (event.nativeEvent && targetRect ? dropZoneForRect(event.nativeEvent, targetRect) : event.position);
  if (zone !== 'middle' && !layoutSplitZone(zone)) return null;
  const data = event.getData?.();
  const item = resolveLayoutItem(data?.panelId || '');
  if (!item || !targetSlot) return null;
  const sourceSlot = slotForItem(item) || dockviewSlotForGroupId(data?.groupId || '');
  const intent = {
    item,
    sourceSlot,
    targetSlot,
    targetRect,
    zone,
    createsPane: layoutSplitZone(zone),
  };
  return {item, intent};
}

function dockviewSideVerticalDropIntent(event, state = dockviewLayoutState.tabPointerDrag) {
  if (!state?.item || !state.slot) return null;
  const nativeEvent = event?.nativeEvent || event;
  const pointerX = Number(nativeEvent?.clientX);
  const pointerY = Number(nativeEvent?.clientY);
  if (!Number.isFinite(pointerX) || !Number.isFinite(pointerY)) return null;
  const dx = Math.abs(pointerX - (Number(state.x) || 0));
  const dy = Math.abs(pointerY - (Number(state.y) || 0));
  const explicitZone = ['top', 'bottom'].includes(event?.position) ? event.position : null;
  // A tab-strip reorder also lands in the pane's top quarter. Dockview's explicit edge position is
  // authoritative; the pointer fallback requires meaningful vertical travel. Horizontal travel is
  // irrelevant because compact tabs commonly start far from the center of a narrow edge pane.
  if (!explicitZone && dy < DRAG_HYSTERESIS_PX * 3) return null;
  const group = dockviewGroupForPoint(pointerX, pointerY);
  const targetSlot = dockviewSlotForGroupElement(group);
  const targetRect = group?.getBoundingClientRect?.();
  if (!targetSlot || !targetRect || !slotIsSidePane(targetSlot)) return null;
  const zone = explicitZone || dropZoneForRect(nativeEvent, targetRect);
  const verticalEdgeZone = ['top', 'bottom'].includes(zone);
  const hitNode = document.elementFromPoint?.(pointerX, pointerY) || nativeEvent?.target;
  const targetHeader = group.querySelector?.('.dv-tabs-and-actions-container, .dv-tabs-container')?.getBoundingClientRect?.();
  const overAnotherFileSurfaceBody = !verticalEdgeZone
    && targetSlot !== state.slot
    && isFileExplorerItem(activeItemForSide(targetSlot))
    && (!targetHeader || pointerY > targetHeader.bottom);
  if (overAnotherFileSurfaceBody) return null;
  if (!verticalEdgeZone && targetSlot !== state.slot && hitNode?.closest?.(
    '.file-explorer-tree-panel, .file-explorer-changes-panel, .file-tree-row',
  )) return null;
  const targetRole = paneRoleForSlot(targetSlot);
  if (targetRole.kind !== paneRoleSide) return null;
  const sourceRole = paneRoleForSlot(state.slot);
  // A Side-to-Side vertical split must stay on the same physical edge. Dual-role YO!* tabs may
  // also enter from a Generic Pane; in that case the target edge alone owns the new leaf.
  if (sourceRole.kind === paneRoleSide && sourceRole.side !== targetRole.side) return null;
  if (!verticalEdgeZone) return null;
  const intent = {
    item: state.item,
    sourceSlot: state.slot,
    targetSlot,
    targetRect,
    zone,
    createsPane: true,
  };
  return dropIntentAllowsSession(state.item, intent, {sourceSlot: state.slot}) ? intent : null;
}

function dockviewCommitSideVerticalDrop(intent) {
  return splitLayoutItemAtSlot(intent.item, intent.targetSlot, intent.zone, intent.sourceSlot, null, {
    forceSplitEmpty: true,
    preserveSourcePlaceholder: true,
  });
}

function dockviewPaneContentDropIntent(event) {
  const info = dockviewPaneContentDropInfo(event);
  return dockviewPaneContentSplitAllowed(info) ? info.intent : null;
}

function dockviewPaneContentDropAllowed(info) {
  return Boolean(
    info
      && !dockviewPinnedTabCrossPaneViolation(info.intent)
      && dropIntentAllowsSession(info.item, info.intent)
  );
}

function dockviewPaneContentSplitAllowed(info) {
  return Boolean(
    dockviewPaneContentDropAllowed(info)
      && layoutSplitZone(info.intent.zone)
      && dockviewSplitLayoutHasMinimumSize(info.intent.zone)
  );
}

function dockviewShouldSuppressPaneContentDrop(event) {
  const info = dockviewPaneContentDropInfo(event);
  return Boolean(info && (
    (narrowSingleColumnMode() && event.position !== 'center')
      ||
    !dockviewPaneContentDropAllowed(info)
      || (layoutSplitZone(info.intent.zone) && !dockviewPaneContentSplitAllowed(info))
  ));
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
    item: intent.item,
    previewNode: grid,
    sourceSlot: intent.sourceSlot,
    targetRect: intent.targetRect || dockviewLayoutState.host?.getBoundingClientRect?.(),
    targetNode: intent.targetNode,
    targetSlot: intent.sourceSlot,
    zone: intent.zone,
  });
}

function dockviewTrackRootBoundaryOverlay(event) {
  const invalidTabDrop = dockviewTabDropViolatesPinnedPartition(event);
  const paneInfo = dockviewPaneContentDropInfo(event);
  const capacityRefusal = dockviewTabCapacityRefusalStatus(event) || (paneInfo?.intent?.zone === 'middle'
    ? dropIntentCapacityRefusalStatus(paneInfo.item, paneInfo.intent, paneInfo.intent.sourceSlot)
    : '');
  dockviewSetInvalidTabDropPreview(invalidTabDrop || Boolean(capacityRefusal));
  if (invalidTabDrop || capacityRefusal) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    return;
  }
  const intent = dockviewRootBoundaryDropIntent(event);
  if (dockviewPinnedTabRootBoundaryViolation(intent)) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    event.preventDefault?.();
    return;
  }
  if (intent) {
    dockviewLayoutState.pendingRootBoundaryDrop = {
      ...intent,
      signature: layoutSlotsSignature(layoutSlots),
    };
    dockviewShowRootBoundaryPreview(intent);
    event.preventDefault?.();
    return;
  }
  if (dockviewTabDropWouldNoop(event)) {
    dockviewLayoutState.pendingRootBoundaryDrop = null;
    dockviewClearRootBoundaryPreview();
    event.preventDefault?.();
    return;
  }
  const paneIntent = dockviewPaneContentDropIntent(event);
  dockviewLayoutState.pendingRootBoundaryDrop = null;
  if (dockviewShouldSuppressPaneContentDrop(event)) {
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

function dockviewTabCapacityRefusalStatus(event) {
  const info = dockviewTabStripEndDropInfoForEvent(event) || dockviewTabInsertionInfo(event);
  if (!info || !info.targetSlot) return '';
  return dropIntentCapacityRefusalStatus(info.item, {targetSlot: info.targetSlot, zone: 'middle'}, info.sourceSlot);
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
    rootBoundaryStartEdges: dockviewRootBoundaryEdgesAtPoint(event),
    rootBoundaryExitedEdges: {},
    lastRootBoundaryIntent: null,
  };
}

function dockviewTabContentInteractionSuppressed() {
  return Boolean(
    dockviewLayoutState.tabPointerDrag
      || Date.now() < (Number(dockviewLayoutState.tabContentInteractionSuppressedUntil) || 0),
  );
}

function dockviewRootBoundaryEdgesAtPoint(event, rect = null) {
  const targetRect = rect || rootBoundaryLayoutTarget()?.rect || dockviewLayoutState.host?.getBoundingClientRect?.();
  if (!targetRect?.width || !targetRect?.height) return {};
  const x = Number(event?.clientX);
  const y = Number(event?.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return {};
  const xBand = layoutBoundaryDropBandPx(targetRect.width);
  const yBand = layoutBoundaryDropBandPx(targetRect.height);
  return {
    left: x >= targetRect.left && x <= targetRect.left + xBand,
    right: x <= targetRect.right && x >= targetRect.right - xBand,
    top: y >= targetRect.top && y <= targetRect.top + yBand,
    bottom: y <= targetRect.bottom && y >= targetRect.bottom - yBand,
  };
}

function dockviewUpdateRootBoundaryExitEdges(event, state, rect) {
  if (!state) return;
  const startEdges = state.rootBoundaryStartEdges || {};
  const currentEdges = dockviewRootBoundaryEdgesAtPoint(event, rect);
  const exitedEdges = state.rootBoundaryExitedEdges || (state.rootBoundaryExitedEdges = {});
  for (const zone of ['left', 'right', 'top', 'bottom']) {
    if (startEdges[zone] && !currentEdges[zone]) exitedEdges[zone] = true;
  }
}

function dockviewTabPointerRootBoundaryIntent(event, state = dockviewLayoutState.tabPointerDrag) {
  if (!state?.item || narrowSingleColumnMode()) return null;
  const target = rootBoundaryLayoutTarget();
  const rect = target?.rect || dockviewLayoutState.host?.getBoundingClientRect?.();
  if (!rect) return null;
  dockviewUpdateRootBoundaryExitEdges(event, state, rect);
  const dx = (Number(event.clientX) || 0) - state.x;
  const dy = (Number(event.clientY) || 0) - state.y;
  const horizontalZone = Math.abs(dx) >= DRAG_HYSTERESIS_PX
    ? rootBoundaryDropZoneForEvent(event, rect, dx >= 0 ? 'right' : 'left')
    : null;
  const verticalZone = Math.abs(dy) >= DRAG_HYSTERESIS_PX
    ? rootBoundaryDropZoneForEvent(event, rect, dy >= 0 ? 'bottom' : 'top')
    : null;
  let zone = horizontalZone && verticalZone
    ? (Math.abs(dx) >= Math.abs(dy) ? horizontalZone : verticalZone)
    : (horizontalZone || verticalZone);
  // A deliberate edge gesture can return to the tab's original root band, making its final
  // start-to-end delta too small to identify an axis. The tracked exit proves it was not merely
  // released in the header; use the current boundary only for that re-entry case.
  if (!zone) {
    const reenteredZone = rootBoundaryDropZoneForEvent(event, rect);
    if (reenteredZone && state.rootBoundaryStartEdges?.[reenteredZone] && state.rootBoundaryExitedEdges?.[reenteredZone]) {
      zone = reenteredZone;
    }
  }
  // A tab header can itself live inside an outer root band's top/left/right/bottom hit area.
  // Require the pointer to leave that same band once before it can create a new outer pane there.
  // That preserves ordinary tab movement from the outermost pane while retaining an explicit,
  // intentional root-edge gesture: move into the content area, then back to the desired edge.
  if (zone && state.rootBoundaryStartEdges?.[zone] && !state.rootBoundaryExitedEdges?.[zone]) return null;
  if (!layoutSplitZone(zone) || !dockviewSplitLayoutHasMinimumSize(zone, rect)) return null;
  return {
    item: state.item,
    zone,
    sourceSlot: state.slot,
    targetRect: rect,
    targetNode: target?.node,
  };
}

function dockviewTabPointerRootBoundaryIntentWithMemory(event, state = dockviewLayoutState.tabPointerDrag) {
  if (!state) return null;
  const intent = dockviewTabPointerRootBoundaryIntent(event, state);
  if (intent) {
    state.lastRootBoundaryIntent = intent;
    return intent;
  }
  const x = Number(event?.clientX);
  const y = Number(event?.clientY);
  const hasUsableCoordinates = Number.isFinite(x) && Number.isFinite(y) && (x !== 0 || y !== 0);
  if (hasUsableCoordinates) state.lastRootBoundaryIntent = null;
  return hasUsableCoordinates ? null : state.lastRootBoundaryIntent;
}

function dockviewTrackTabPointerDrag(event) {
  const state = dockviewLayoutState.tabPointerDrag;
  if (!state?.item) return;
  const dx = Math.abs((Number(event.clientX) || 0) - state.x);
  const dy = Math.abs((Number(event.clientY) || 0) - state.y);
  if (Math.max(dx, dy) < DRAG_HYSTERESIS_PX) return;
  const intent = dockviewTabPointerRootBoundaryIntentWithMemory(event, state);
  if (intent && !dockviewPinnedTabRootBoundaryViolation(intent)) dockviewShowRootBoundaryPreview(intent);
  else dockviewClearRootBoundaryPreview();
}

function dockviewFinishTabPointerDrag(event) {
  const state = dockviewLayoutState.tabPointerDrag;
  dockviewLayoutState.tabPointerDrag = null;
  dockviewSetInvalidTabDropPreview(false);
  dockviewClearRootBoundaryPreview();
  if (state) dockviewSuppressPanePointerDrag();
  if (!state?.item || !state.slot) return;
  const dx = Math.abs((Number(event.clientX) || 0) - state.x);
  const dy = Math.abs((Number(event.clientY) || 0) - state.y);
  if (Math.max(dx, dy) < DRAG_HYSTERESIS_PX) return;
  dockviewLayoutState.tabContentInteractionSuppressedUntil = Date.now() + PANE_DRAG_SUPPRESS_MS;
  const sideIntent = dockviewSideVerticalDropIntent(event, state);
  if (sideIntent) {
    // This gesture belongs to the app layout, not Dockview's center-stack transaction. Commit it
    // before Dockview can flatten the tab back into one group. Keep propagation intact so Dockview
    // can remove its own drag ghost; preventDefault marks the release as app-owned.
    event.preventDefault?.();
    dockviewLayoutState.tabDropHandledAt = Date.now();
    void dockviewCommitSideVerticalDrop(sideIntent);
    return;
  }
  const rootIntent = dockviewTabPointerRootBoundaryIntentWithMemory(event, state);
  if (rootIntent && !dockviewPinnedTabRootBoundaryViolation(rootIntent)) {
    // The app owns the visible root-edge preview. Commit it before Dockview's generic tab-drop
    // stamp can make the pointer fallback stand down without producing the requested root split.
    event.preventDefault?.();
    dockviewLayoutState.tabDropHandledAt = Date.now();
    void splitSessionAtLayoutBoundary(rootIntent.item, rootIntent.zone, rootIntent.sourceSlot);
    return;
  }
  const stripEnd = dockviewTabStripEndDropInfoForPointer(event, state);
  if (stripEnd && !dockviewTabStripEndDropWouldNoop(stripEnd) && !dockviewTabStripEndDropViolatesPinnedPartition(stripEnd)) {
    window.setTimeout(() => {
      if (Date.now() - (Number(dockviewLayoutState.tabDropHandledAt) || 0) < 800) return;
      void moveSessionToSlot(stripEnd.item, stripEnd.targetSlot, stripEnd.sourceSlot, stripEnd.insertIndex);
    }, 0);
    return;
  }
  const targetGroup = dockviewGroupForPoint(Number(event.clientX) || 0, Number(event.clientY) || 0);
  const contentTargetSlot = dockviewSlotForGroupElement(targetGroup);
  const contentIntent = contentTargetSlot ? {
    item: state.item,
    sourceSlot: state.slot,
    targetSlot: contentTargetSlot,
    targetRect: layoutSlotScreenRect(contentTargetSlot),
    zone: 'middle',
    createsPane: false,
  } : null;
  if (
    contentTargetSlot
    && contentTargetSlot !== state.slot
    && !dockviewPinnedTabCrossPaneViolation(contentIntent)
    && dropIntentAllowsSession(state.item, contentIntent)
  ) {
    window.setTimeout(() => {
      // Dockview's onWillDrop may have committed an edge split after the document-level pointer
      // fallback queued this center move. Do not collapse that newer app-owned transaction.
      if (Date.now() - (Number(dockviewLayoutState.tabDropHandledAt) || 0) < 800) return;
      if (slotForItem(state.item) === contentTargetSlot) return;
      void moveSessionToSlot(state.item, contentTargetSlot, state.slot, paneTabs(contentTargetSlot).length);
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
  const direct = document.elementFromPoint?.(x, y)?.closest?.('.dv-groupview');
  const directRect = direct?.getBoundingClientRect?.();
  if (directRect && x >= directRect.left && x <= directRect.right && y >= directRect.top && y <= directRect.bottom) return direct;
  // Dockview's drop overlay can be the topmost hit-test node while a tab is being dragged, so
  // elementFromPoint may not have a group ancestor even though the pointer is visibly inside one.
  return Array.from(dockviewLayoutState.host?.querySelectorAll?.('.dv-groupview') || []).find(group => {
    const rect = group.getBoundingClientRect?.();
    return rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  }) || null;
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
  const rect = pending.targetRect || rootBoundaryLayoutTarget()?.rect || dockviewLayoutState.host?.getBoundingClientRect?.();
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

function dockviewCapacityRefusalForDrag(drag) {
  if (drag?.kind !== 'session' || !drag.intent) return '';
  return dropIntentCapacityRefusalStatus(drag.payload?.session, drag.intent, drag.payload?.sourceSlot || slotForItem(drag.payload?.session));
}

function dockviewHandleFileDragOver(event) {
  const drag = classifyLayoutDrag(event, {
    intentForFile: dockviewFileDropIntentForEvent,
    intentForSession: dockviewGroupDropIntentForEvent,
    ignoreMissingIntent: true,
  });
  const capacityRefusal = dockviewCapacityRefusalForDrag(drag);
  if (capacityRefusal) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'none';
    dockviewSetInvalidTabDropPreview(true);
    showDropPreview(drag.intent);
    return;
  }
  applyLayoutDragIntent(event, drag);
}

function dockviewHandleFileDrop(event) {
  const drag = classifyLayoutDrag(event, {
    intentForFile: dockviewFileDropIntentForEvent,
    intentForSession: dockviewGroupDropIntentForEvent,
    ignoreMissingIntent: true,
  });
  const capacityRefusal = dockviewCapacityRefusalForDrag(drag);
  if (capacityRefusal) {
    event.preventDefault();
    event.stopPropagation();
    clearDropPreview();
    dockviewSetInvalidTabDropPreview(false);
    showLayoutStatus(capacityRefusal, 'danger');
    return;
  }
  applyLayoutDragIntent(event, drag, {phase: 'drop'});
}

function dockviewInstallFileDropBridge(host) {
  const dragOver = event => dockviewHandleFileDragOver(event);
  const drop = event => dockviewHandleFileDrop(event);
  const dragLeave = event => {
    if (host?.contains?.(event.relatedTarget)) return;
    clearDropPreview();
    dockviewSetInvalidTabDropPreview(false);
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
  requestAnimationFrame(() => {
    if (api !== dockviewLayoutState.api || host !== dockviewLayoutState.host) return;
    dockviewSyncHeaderActionReservations();
  });
}

function dockviewScheduleLayoutToHost(api = dockviewLayoutState.api, host = dockviewLayoutState.host) {
  if (!api || !host) return;
  if (dockviewLayoutState.hostLayoutFrame) cancelAnimationFrame(dockviewLayoutState.hostLayoutFrame);
  dockviewLayoutState.hostLayoutFrame = requestAnimationFrame(() => {
    dockviewLayoutState.hostLayoutFrame = 0;
    dockviewLayoutToHost(api, host);
  });
}

function dockviewCancelPendingLoad() {
  if (!dockviewLayoutState.pendingLoadFrame) return;
  cancelAnimationFrame(dockviewLayoutState.pendingLoadFrame);
  clearTimeout(dockviewLayoutState.pendingLoadFrame);
  dockviewLayoutState.pendingLoadFrame = 0;
}

function dockviewScheduleDeferredLoad(previousActive = [], options = {}) {
  dockviewCancelPendingLoad();
  const deferredActive = [...previousActive];
  const deferredOptions = {...options, deferDockviewLoad: false, deferDockviewPostLoad: true};
  if (typeof requestAnimationFrame !== 'function') {
    renderPanelsDockview(deferredActive, deferredOptions);
    return;
  }
  // Let the host-layout/header-reservation frame queued by dockviewLayoutToHost
  // settle before fromJSON mutates the group tree. Combining them is what turns
  // an otherwise bounded topology load into one browser long task.
  dockviewLayoutState.pendingLoadFrame = requestAnimationFrame(() => {
    // A timer task follows the host paint instead of becoming another part of
    // its rendering callback. The exact Dockview JSON reload remains unchanged.
    dockviewLayoutState.pendingLoadFrame = window.setTimeout(() => {
      dockviewLayoutState.pendingLoadFrame = 0;
      renderPanelsDockview(deferredActive, deferredOptions);
    }, 0);
  });
}

function dockviewRefreshLoadedLayout(items = []) {
  const tabsPerf = clientPerfStart('dockviewRefreshTabs');
  try {
    dockviewRefreshTabs();
  } finally {
    clientPerfEnd(tabsPerf, {nodes: items.length});
  }
  const mountedPerf = clientPerfStart('dockviewSyncMountedPanels');
  try {
    dockviewSyncMountedPanels();
  } finally {
    clientPerfEnd(mountedPerf, {nodes: items.length});
  }
}

function dockviewScheduleLoadedLayoutCompletion(items = [], previousActive = [], options = {}) {
  if (dockviewLayoutState.pendingCompletionFrame) cancelAnimationFrame(dockviewLayoutState.pendingCompletionFrame);
  const completedItems = [...items];
  const completedActive = [...previousActive];
  const completedOptions = {...options, deferDockviewPostLoad: false};
  const complete = () => {
    dockviewLayoutState.pendingCompletionFrame = 0;
    dockviewRefreshLoadedLayout(completedItems);
    finishPanelLayoutRender(completedActive, completedOptions);
  };
  if (typeof requestAnimationFrame !== 'function') {
    complete();
    return;
  }
  dockviewLayoutState.pendingCompletionFrame = requestAnimationFrame(complete);
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

function dockviewLayoutAdoptionAllowed() {
  return !shareViewMode || shareWriteMode;
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
    dockviewTrackTabPointerDrag(event);
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

function dockviewBindTabContainerContextMenu(tabsContainer, group) {
  if (!tabsContainer || tabsContainer.__yolomuxEmptySpaceContextMenuBound) return;
  tabsContainer.__yolomuxEmptySpaceContextMenuBound = true;
  tabsContainer.addEventListener('contextmenu', event => {
    // Tab context menus retain their own target and inline-rename behavior. This handler owns only
    // the blank portion of a pane's Dockview tab strip, where the browser menu has no app meaning.
    if (event.target.closest('.dv-tab, .dockview-pane-tab')) return;
    const activeItem = group.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab
      || activeItemForSide(dockviewSlotForGroupElement(group));
    if (!activeItem || !isLayoutItem(activeItem)) return;
    event.preventDefault();
    event.stopPropagation();
    showTabContextMenu(activeItem, event.clientX, event.clientY);
  });
}

function dockviewSyncHeaderBackgroundDragSources() {
  if (!dockviewLayoutActive()) return;
  document.querySelectorAll('.dv-groupview').forEach(group => {
    const header = group.querySelector('.dv-tabs-and-actions-container');
    const tabsContainer = header?.querySelector('.dv-tabs-container');
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
    dockviewBindTabContainerContextMenu(tabsContainer, group);
  });
}

function dockviewSyncHeaderActionReservations() {
  if (!dockviewLayoutActive()) return;
  document.querySelectorAll('.dv-groupview').forEach(group => {
    const header = group.querySelector('.dv-tabs-and-actions-container');
    if (!header) return;
    const tabsContainer = header.querySelector('.dv-tabs-container');
    if (!tabsContainer) return;
    tabsContainer.querySelectorAll(':scope > .dockview-tab-first-row-reservation').forEach(node => node.remove());
    const actions = group.querySelector('.dockview-pane-header-actions:not([hidden])');
    const width = actions ? Math.ceil(appSpaceRect(actions).width || actions.offsetWidth || 0) : 0;
    const reservedWidth = width > 0 ? width + 8 : 0;
    // The reservation is a child of the tab container, which may be wider than Dockview's header
    // content box when actions are absolutely positioned. Measure that shared flex surface.
    const headerWidth = Math.floor(appSpaceRect(tabsContainer).width || tabsContainer.clientWidth || 0);
    const slot = dockviewSlotForGroupElement(group);
    const intrinsicSidePaneTabs = Boolean(slot && slotIsSidePane(slot));
    const rootStyle = getComputedStyle(document.documentElement);
    const preferredTabWidth = Number.parseFloat(rootStyle.getPropertyValue('--pane-tab-width')) || 180;
    const minTabWidth = Number.parseFloat(rootStyle.getPropertyValue('--dockview-tab-min-inline-size')) || 64;
    const availableWidth = headerWidth > reservedWidth ? headerWidth - reservedWidth : headerWidth;
    // Keep the first row clear of its absolutely positioned actions, while allowing later rows to
    // use the whole header. The normal tab width is retained without actions. With actions, fit
    // the next whole-tab count across the full header so later rows do not inherit a dead gutter.
    const sampleTab = tabsContainer.querySelector('.dv-tab');
    const sampleTabStyle = sampleTab ? getComputedStyle(sampleTab) : null;
    const tabMargin = sampleTabStyle
      ? Number.parseFloat(sampleTabStyle.marginInlineStart || '0') + Number.parseFloat(sampleTabStyle.marginInlineEnd || '0')
      : 0;
    const tabs = Array.from(tabsContainer.querySelectorAll(':scope > .dv-tab'));
    const preferredTabFootprint = preferredTabWidth + tabMargin;
    const firstRowPreferredCount = availableWidth > 0 ? Math.max(1, Math.floor(availableWidth / preferredTabFootprint)) : 1;
    const tabsNeedWrap = tabs.length > firstRowPreferredCount;
    const fullRowTabCount = headerWidth > 0 ? Math.max(1, Math.ceil(headerWidth / preferredTabWidth)) : 1;
    const laterRowTabWidth = headerWidth > 0 ? Math.floor((headerWidth - tabMargin * fullRowTabCount) / fullRowTabCount) : preferredTabWidth;
    const tabWidth = reservedWidth > 0 && tabsNeedWrap
      ? Math.max(minTabWidth, Math.min(preferredTabWidth, laterRowTabWidth))
      : (availableWidth > 0 ? Math.min(Math.max(minTabWidth, preferredTabWidth), Math.max(minTabWidth, availableWidth)) : Math.max(minTabWidth, preferredTabWidth));
    header.style.setProperty('--dockview-header-actions-reserved-inline-size', reservedWidth > 0 ? `${reservedWidth}px` : '0px');
    // The narrow triplet home uses content-width tabs. Its shared action gutter is ordinary end
    // padding, so all three tabs stay on one row and shrink together instead of inheriting the
    // preferred-width reservation algorithm used by normal multi-row pane tabs.
    if (intrinsicSidePaneTabs) {
      header.style.removeProperty('--dockview-tab-inline-size');
      return;
    }
    header.style.setProperty('--dockview-tab-inline-size', `${tabWidth}px`);
    if (!reservedWidth || !headerWidth) return;
    const firstRowWidth = Math.max(0, headerWidth - reservedWidth);
    let usedWidth = 0;
    let usedActualWidth = 0;
    let firstExcludedTab = null;
    for (const tab of tabs) {
      const style = getComputedStyle(tab);
      const measuredTabWidth = Math.ceil(appSpaceRect(tab).width || tab.offsetWidth || 0)
        + Number.parseFloat(style.marginInlineStart || '0')
        + Number.parseFloat(style.marginInlineEnd || '0');
      // The first row keeps the preferred-width capacity even when later rows shrink slightly to
      // reclaim the action gutter. That gives the rows below enough tabs to use the full header.
      const firstRowTabWidth = reservedWidth > 0
        ? preferredTabWidth + Number.parseFloat(style.marginInlineStart || '0') + Number.parseFloat(style.marginInlineEnd || '0')
        : measuredTabWidth;
      if (usedWidth > 0 && usedWidth + firstRowTabWidth > firstRowWidth) {
        firstExcludedTab = tab;
        break;
      }
      usedWidth += firstRowTabWidth;
      usedActualWidth += measuredTabWidth;
    }
    if (!tabs.length || !usedActualWidth) return;
    const reservation = document.createElement('span');
    reservation.className = 'dockview-tab-first-row-reservation';
    reservation.setAttribute('aria-hidden', 'true');
    reservation.style.setProperty('--dockview-first-row-reservation-inline-size', `${Math.max(0, Math.floor(headerWidth - usedActualWidth))}px`);
    tabsContainer.insertBefore(reservation, firstExcludedTab);
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
      dockviewCommitPanelActivation(item);
    }),
    api.onWillShowOverlay(event => dockviewTrackRootBoundaryOverlay(event)),
    api.onWillDrop(event => {
      // Tab drops are handled here (or committed by dockview itself); stamp the gesture so the pinned
      // pointer-reorder FALLBACK stands down instead of double-applying (see dockviewFinishTabPointerDrag).
      if (event?.kind === 'tab') dockviewLayoutState.tabDropHandledAt = Date.now();
      dockviewSetInvalidTabDropPreview(false);
      const sideIntent = dockviewSideVerticalDropIntent(event);
      if (sideIntent) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void dockviewCommitSideVerticalDrop(sideIntent);
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
      const tabCapacityRefusal = dockviewTabCapacityRefusalStatus(event);
      if (tabCapacityRefusal) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        showLayoutStatus(tabCapacityRefusal, 'danger');
        return;
      }
      const tabInsertion = dockviewTabInsertionInfo(event);
      // A tab-header drop is `kind: tab`, not `kind: content`. Dockview otherwise owns that move
      // internally and its later adoption path rejects the protected home group. A triplet item
      // is explicitly allowed to center-stack there, so commit it through the common layout move.
      if (tabInsertion && slotIsSidePane(tabInsertion.targetSlot)
        && paneRoleAllowsItemTransfer(tabInsertion.item, tabInsertion.sourceSlot, tabInsertion.targetSlot)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void moveSessionToSlot(tabInsertion.item, tabInsertion.targetSlot, tabInsertion.sourceSlot, tabInsertion.adjustedIndex);
        });
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
      const paneInfo = dockviewPaneContentDropInfo(event);
      const capacityRefusal = paneInfo?.intent?.zone === 'middle'
        ? dropIntentCapacityRefusalStatus(paneInfo.item, paneInfo.intent, paneInfo.intent.sourceSlot)
        : '';
      if (capacityRefusal) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        showLayoutStatus(capacityRefusal, 'danger');
        return;
      }
      // Dockview's default center-drop mutates its private group first and relies on a later
      // adoption pass. That lost center drops into the protected triplet home column. Apply every
      // allowed center move through the same layout transaction as the rest of the app instead.
      if (paneInfo && paneInfo.intent.zone === 'middle' && dockviewPaneContentDropAllowed(paneInfo)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        queueMicrotask(() => {
          void moveSessionToSlot(paneInfo.item, paneInfo.intent.targetSlot, paneInfo.intent.sourceSlot, paneTabs(paneInfo.intent.targetSlot).length);
        });
        return;
      }
      const paneIntent = dockviewPaneContentDropIntent(event);
      if (paneIntent) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        dockviewLayoutState.tabDropHandledAt = Date.now();
        queueMicrotask(() => {
          void splitSessionAtSlot(paneIntent.item, paneIntent.targetSlot, paneIntent.zone, paneIntent.sourceSlot);
        });
        return;
      }
      if (dockviewShouldSuppressPaneContentDrop(event)) {
        dockviewLayoutState.pendingRootBoundaryDrop = null;
        dockviewClearRootBoundaryPreview();
        event.preventDefault();
        return;
      }
      dockviewLayoutState.pendingRootBoundaryDrop = null;
      dockviewClearRootBoundaryPreview();
      const targetSlot = dockviewSlotForGroupId(event.group?.id || '');
      if (event.position === 'center' && targetSlot) {
        const item = resolveLayoutItem(event.getData?.()?.panelId || '');
        if (!paneRoleAllowsItemTransfer(item, slotForItem(item), targetSlot)) event.preventDefault();
      }
    }),
  ];
  return api;
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
  let activeOnlyChange = false;
  let layoutReloaded = false;
  let layoutLoadDeferred = false;
  let layoutCompletionDeferred = false;
  if (!dockviewLayoutState.adoptingFromDockview && dockviewLayoutState.lastAppliedLayoutSignature !== signature) {
    const activeOnlySignature = dockviewLayoutActiveOnlySignature(layoutSlots);
    if (dockviewLayoutState.lastAppliedActiveOnlySignature !== activeOnlySignature || !dockviewActivateLayoutTabs(layoutSlots)) {
      if (options.deferDockviewLoad === true) {
        dockviewScheduleDeferredLoad(previousActive, options);
        layoutLoadDeferred = true;
      } else {
        layoutCompletionDeferred = dockviewLoadLayout(layoutSlots, {
          deferPostLoad: options.deferDockviewPostLoad === true,
          previousActive,
          renderOptions: options,
        });
        layoutReloaded = true;
      }
    } else {
      dockviewLayoutState.lastAppliedLayoutSignature = signature;
      activeOnlyChange = true;
    }
  }
  // dockviewLoadLayout already refreshes these once after fromJSON. Repeating
  // the same mounted-panel walk on a whole-pane swap made the drop path a
  // single long task even though Dockview reused every panel instance.
  if (!layoutReloaded && !layoutLoadDeferred) {
    const tabsPerf = clientPerfStart('dockviewRefreshTabs');
    try {
      dockviewRefreshTabs();
    } finally {
      clientPerfEnd(tabsPerf, {nodes: activePaneCount});
    }
    const mountedPerf = clientPerfStart('dockviewSyncMountedPanels');
    try {
      dockviewSyncMountedPanels({renderAttached: !activeOnlyChange});
    } finally {
      clientPerfEnd(mountedPerf, {nodes: activePaneCount});
    }
  }
  if (!layoutLoadDeferred && !layoutCompletionDeferred) finishPanelLayoutRender(previousActive, options);
  return true;
}

function syncActivePanelsDockview(previousActive = []) {
  renderPanelsDockview(previousActive, {prune: false});
}

function dockviewLoadLayout(slots = layoutSlots, options = {}) {
  const api = dockviewLayoutState.api;
  if (!api) return;
  const items = paneItems(slots);
  dockviewLayoutState.applyingFromLayout = true;
  try {
    if (!items.length) {
      api.clear();
      dockviewLayoutState.lastAppliedLayoutSignature = layoutSlotsSignature(slots);
      dockviewLayoutState.lastAppliedActiveOnlySignature = dockviewLayoutActiveOnlySignature(slots);
      return false;
    }
    const loadPerf = clientPerfStart('dockviewFromJson');
    try {
      api.fromJSON(dockviewJsonFromLayoutSlots(slots), {reuseExistingPanels: true});
    } finally {
      clientPerfEnd(loadPerf, {nodes: items.length});
    }
    dockviewLayoutState.lastAppliedLayoutSignature = layoutSlotsSignature(slots);
    dockviewLayoutState.lastAppliedActiveOnlySignature = dockviewLayoutActiveOnlySignature(slots);
    if (options.deferPostLoad === true) {
      dockviewScheduleLoadedLayoutCompletion(items, options.previousActive, options.renderOptions);
      return true;
    }
    dockviewRefreshLoadedLayout(items);
  } finally {
    dockviewLayoutState.applyingFromLayout = false;
  }
  return false;
}

// An active-tab change does not alter the Dockview topology or its mounted panel contents. Calling
// fromJSON in that case rebuilds the whole layout and is the visible tab-switch delay; activate the
// existing panel in each group instead, which is Dockview's native pre-rendered-panel path.
function dockviewActivateLayoutTabs(slots = layoutSlots) {
  const api = dockviewLayoutState.api;
  if (!api || !dockviewLayoutActive()) return false;
  const activeItems = layoutSlotKeys(slots)
    .map(slot => activeItemForSide(slot, slots))
    .filter(Boolean);
  if (!activeItems.length && paneItems(slots).length) return false;
  const panels = activeItems.map(item => api.getPanel?.(item));
  if (panels.some(panel => typeof panel?.api?.setActive !== 'function')) return false;
  for (const panel of panels) panel.api.setActive();
  return true;
}

function dockviewJsonFromLayoutSlots(slots = layoutSlots) {
  const tree = slots?.[layoutTreeKey] || legacyLayoutTree(slots);
  const rootOrientation = dockviewOrientationForSplit(tree?.split === 'column' ? 'column' : 'row');
  const panelItems = paneItems(slots);
  const size = dockviewHostLayoutSize(dockviewLayoutState.host || grid);
  const viewport = appViewport();
  const sidePaneMaximumWidth = Math.max(
    minSplitPaneWidthPx(),
    Math.floor((size.width || viewport.width || 0) * sidePaneRoleDefinition.maxViewportFraction),
  );
  const panels = {};
  for (const item of panelItems) {
    const slot = slotForItem(item, slots);
    const sidePane = slotIsSidePane(slot, slots);
    panels[item] = {
      id: item,
      contentComponent: dockviewContentComponentName,
      tabComponent: dockviewTabComponentName,
      title: itemLabel(item),
      renderer: dockviewPanelRenderer,
      params: {item},
      minimumWidth: sidePane ? minWidthForSidePaneItem(item) : minWidthForLayoutItem(item),
      ...(sidePane ? {maximumWidth: sidePaneMaximumWidth} : {}),
      minimumHeight: minSplitPaneHeightPx(),
    };
  }
  for (const slot of layoutSlotKeys(slots)) {
    if (!paneIsPlaceholder(slot, slots)) continue;
    const item = dockviewEmptyPaneItem(slot);
    panels[item] = {
      id: item,
      contentComponent: dockviewContentComponentName,
      tabComponent: dockviewTabComponentName,
      title: t('pane.empty'),
      renderer: dockviewPanelRenderer,
      params: {item},
      minimumWidth: minSplitPaneWidthPx(),
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
  const placeholder = paneIsPlaceholder(slot, slots);
  dockviewLayoutState.groupSlots.set(groupId, groupId);
  return {
    type: 'leaf',
    data: {
      id: groupId,
      // Dockview does not visually retain a zero-view group. Its synthetic, headerless panel
      // gives our existing empty-pane renderer a real group to occupy while keeping app state
      // explicitly tab-free.
      views: placeholder ? [dockviewEmptyPaneItem(groupId)] : tabs.slice(),
      activeView: placeholder ? dockviewEmptyPaneItem(groupId) : (activeItemForSide(slot, slots) || tabs[0]),
      // The Dockview group header is the only tab strip when an inner panel head is hidden.
      // A singleton Finder used to suppress it as a legacy reserved-pane optimization, leaving
      // no visible route to the other independent triplet tabs after they are added.
      hideHeader: placeholder,
    },
    size: Math.max(1, Math.round(weight)),
    visible: true,
  };
}

function queueDockviewLayoutAdoption() {
  if (!dockviewLayoutAdoptionAllowed()) return;
  if (dockviewLayoutState.applyingFromLayout || dockviewLayoutState.adoptingFromDockview) return;
  if (dockviewLayoutState.syncQueued) return;
  dockviewLayoutState.syncQueued = true;
  queueMicrotask(adoptDockviewLayout);
}

function adoptDockviewLayout() {
  dockviewLayoutState.syncQueued = false;
  const api = dockviewLayoutState.api;
  if (!api || dockviewLayoutState.applyingFromLayout) return;
  if (!dockviewLayoutAdoptionAllowed()) return;
  if (!dockviewHostCanAdoptLayout()) return;
  let next = layoutSlotsFromDockviewJson(api.toJSON());
  const missingSurfaces = layoutFileSurfaceItems().filter(item => itemInLayout(item, layoutSlots) && !itemInLayout(item, next));
  if (missingSurfaces.length) {
    next = layoutWithSidePaneItems(next, missingSurfaces, {side: paneSideLeft});
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
    applyLayoutSlots(next, {prune: false, preservePlaceholderSlots: true});
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
      const views = Array.isArray(group.views) ? group.views : [];
      let tabs = views
        .filter(view => dockviewEmptyPaneSlot(view) === null)
        .map(resolveLayoutItem)
        .filter(item => isLayoutItem(item));
      let activeView = resolveLayoutItem(group.activeView);
      const previousTabs = paneTabs(slot, previous);
      if (!tabs.length && previousTabs.some(layoutIsFileSurfaceItem)) {
        tabs = previousTabs.slice();
        activeView = activeItemForSide(slot, previous) || tabs[0];
        dockviewLayoutState.reloadAfterAdoption = true;
      }
      // Dockview keeps an explicit zero-tab group after a directional Move so the user has the
      // empty half they asked for. It is not stale layout: retain it as our placeholder pane
      // instead of dropping the leaf while translating Dockview back into app state.
      const previousRole = paneRoleForSlot(slot, previous);
      next[slot] = tabs.length ? paneStateWithTabs(tabs, activeView, previousRole) : emptyPlaceholderPaneState(previousRole);
      return leafNode(slot);
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
  const missingPlaceholder = layoutSlotKeys(previous).find(slot => (
    paneIsPlaceholder(slot, previous)
    && !layoutSlotKeys(next).includes(slot)
  ));
  if (missingPlaceholder) {
    // fromJSON can emit an intermediate layout event before its synthetic empty-pane group is
    // mounted. That event is not a user deletion; adopting it would immediately erase the visible
    // disposable peer created by directional Move.
    dockviewLayoutState.reloadAfterAdoption = true;
    return cloneLayoutSlots(previous);
  }
  if (dockviewLayoutHasRoleTransferViolation(next, previous)) {
    dockviewLayoutState.reloadAfterAdoption = true;
    return cloneLayoutSlots(previous);
  }
  preserveDockviewSidePaneSplit(next, previous);
  return normalizeLayoutSlots(
    compactLayoutSlots(next, {preservePlaceholderSlots: true}),
    {preservePlaceholderSlots: true},
  );
}

function dockviewLayoutHasRoleTransferViolation(next, previous = layoutSlots) {
  return paneItems(next).some(item => {
    const previousSlot = slotForItem(item, previous);
    const nextSlot = slotForItem(item, next);
    if (!previousSlot || !nextSlot || previousSlot === nextSlot) return false;
    return !paneRoleAllowsItemTransfer(item, previousSlot, nextSlot, next, {
      sourceRole: paneRoleForSlot(previousSlot, previous),
      targetRole: paneRoleForSlot(nextSlot, next),
    });
  });
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
  if (!items.some(layoutIsFileSurfaceItem) && !(slot && paneTabs(slot).some(layoutIsFileSurfaceItem))) return;
  dockviewLayoutState.reloadAfterAdoption = true;
  queueDockviewLayoutAdoption();
}

function preserveDockviewSidePaneSplit(next, previous = layoutSlots) {
  const previousRoot = previous?.[layoutTreeKey];
  const nextRoot = next?.[layoutTreeKey];
  const previousDocked = layoutSidePaneRootSplit(previousRoot, previous);
  const nextDocked = layoutSidePaneRootSplit(nextRoot, next);
  if (!previousDocked || !nextDocked || !nextRoot) return;
  if (dockviewLayoutContentSignature(next) === dockviewLayoutContentSignature(previous)) {
    preserveDockviewContentSplitPercentagesAfterDockResize(nextRoot, previousRoot, nextDocked, previousDocked);
    return;
  }
  const sidePct = previousDocked.sideIndex === 0 ? previousDocked.pct : 100 - previousDocked.pct;
  const preservedPct = nextDocked.sideIndex === 0 ? sidePct : 100 - sidePct;
  if (Math.abs((Number(nextRoot.pct) || 0) - preservedPct) > SPLIT_PCT_EPSILON) {
    nextRoot.pct = preservedPct;
    dockviewLayoutState.reloadAfterAdoption = true;
  }
}

function preserveDockviewContentSplitPercentagesAfterDockResize(nextRoot, previousRoot, nextDocked, previousDocked) {
  const previousSidePct = previousDocked.sideIndex === 0 ? previousDocked.pct : 100 - previousDocked.pct;
  const nextSidePct = nextDocked.sideIndex === 0 ? nextDocked.pct : 100 - nextDocked.pct;
  if (Math.abs(nextSidePct - previousSidePct) <= SPLIT_PCT_EPSILON) return;
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

// Tab order is observable in Dockview (including pinning); only the active item is safe to change
// without fromJSON. Keep this distinct from the unordered content signature used for resize math.
function dockviewLayoutActiveOnlySignature(slots = layoutSlots) {
  const paneSignature = layoutSlotKeys(slots)
    .slice()
    .sort()
    .map(slot => `${slot}:${paneTabs(slot, slots).join(',')}`)
    .join('|');
  return `${layoutShapeSignature(slots)}::${paneSignature}`;
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
    const emptySlot = dockviewEmptyPaneSlot(item);
    if (emptySlot) {
      panel = null;
      element.replaceChildren(renderEmptyPane(emptySlot));
      return;
    }
    if (!isLayoutItem(item)) return;
    panel = getOrCreatePanel(item);
    const slot = slotForItem(item) || dockviewSlotForGroupId(params?.api?.group?.id || '');
    updatePanelSlot(panel, item, slot);
    element.replaceChildren(panel);
    renderAttachedPanelContent(item);
    restorePaneViewState(item, panel);
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

function dockviewHeaderActionsHtml(item, slot = slotForItem(item)) {
  if (!isLayoutItem(item)) return '';
  if (slotIsSidePane(slot)) {
    return paneFrameControlsGroupHtml(item, {
      slot,
      actions: false,
      details: false,
      popout: false,
      expand: false,
      close: false,
      minimize: true,
    });
  }
  const paneHandle = paneDragHandleHtml(item, slot);
  if (isTmuxSession(item)) return `${paneHandle}${panelControlsHtml(item)}`;
  if (isFileEditorItem(item)) {
    return `${paneHandle}${paneFrameControlsGroupHtml(item, {
      groupClass: 'file-editor-frame-controls',
      actions: false,
      popout: paneCanPopout(item),
      minimize: true,
      expand: true,
      close: true,
      closeClass: 'file-editor-panel-close',
      closeTitle: t('editor.closePane'),
      closeLabel: t('editor.closePane'),
    })}`;
  }
  if (isVirtualItem(item)) return `${paneHandle}${virtualPanelControlsHtml(item, {popout: paneCanPopout(item)})}`;
  return '';
}

function paneDragHandleHtml(item, slot = slotForItem(item)) {
  if (!slot || slotIsFileExplorerPane(slot)) return '';
  return `<button type="button" class="tab pane-drag-handle" data-pane-drag="${esc(slot)}" draggable="true" title="${esc(t('pane.drag'))}" aria-label="${esc(t('pane.drag'))}"></button>`;
}

function handleDockviewHeaderActionClick(event, fallbackItem = '') {
  const button = event.target?.closest?.('button');
  if (!button) return;
  const item = button.dataset.tab || button.dataset.windowSession || button.dataset.detailToggle
    || button.dataset.paneActions || button.dataset.paneMinimize || button.dataset.paneExpand
    || button.dataset.panePopout || button.dataset.paneClose || fallbackItem;
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
  if (button.dataset.panePopout !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    openPanePopout(button.dataset.panePopout || item);
    return;
  }
  if (button.dataset.paneClose !== undefined) {
    event.preventDefault();
    event.stopPropagation();
    closePaneFrameItem(button.dataset.paneClose || item);
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
    const slot = dockviewSlotForGroupId(group?.id || '') || slotForItem(activeItem);
    const html = dockviewHeaderActionsHtml(activeItem, slot);
    element.hidden = !html;
    element.innerHTML = html;
    updatePanelWindowStepButtons(activeItem, transcriptMetadataState.payload.sessions?.[activeItem]);
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
    if (dockviewEmptyPaneSlot(item)) {
      element.hidden = true;
      element.replaceChildren();
      return;
    }
    syncDockviewTabShell(element, item, api);
    if (paneTabShouldPreserve(element)) {
      syncPaneTabPinnedChrome(element, item);
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
    } else bindTabInteraction({anchor: element, item, sourceSlot: () => slotForItem(item)});
  };
  const dispose = () => {
    cleanupDetachedPaneTabPopover(element);
    for (const disposable of disposables) disposable?.dispose?.();
    disposables = [];
  };
  const commitExplicitTabInteraction = () => {
    dockviewCommitPanelActivation(item, {userInitiated: true});
  };
  element.__yolomuxDockviewRefresh = render;
  element.addEventListener('pointerdown', event => {
    if (event.target.closest('[data-pane-tab-close], [data-auto-session]')) event.stopPropagation();
    else {
      dockviewBeginTabActivationPerf(item);
      dockviewLayoutState.pendingUserPanelActivation = item;
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
    dockviewFinishTabActivationPerf(item);
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
  tab.classList.toggle('tmux-pane-tab-token', isTmuxSession(item));
  tab.classList.toggle('file-missing', isFileEditorItem(item) && openFileIsMissing(fileItemPath(item)));
  tab.classList.toggle('pinned-tab', tabIsPinned(item));
  applySessionStateClasses(tab, isVirtualItem(item) ? null : sessionState(item, transcriptMetadataState.payload.sessions?.[item]));
  tab.setAttribute('aria-label', dockviewTabAriaLabel(item));
}

function dockviewPaneTabHtml(item) {
  // Shares paneTabInnerHtml with the DOM-building createPaneTab so pin/close/popover markup parity is
  // enforced in one place (the Dockview tab renderer just needs the string form).
  return paneTabInnerHtml(item);
}

function dockviewTabAriaLabel(item) {
  return paneTabAriaLabel(item);
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

function dockviewSyncMountedPanels(options = {}) {
  if (!dockviewLayoutActive()) return;
  for (const item of activePaneItems()) {
    const panel = panelNodes.get(item);
    if (!panel?.isConnected) continue;
    const slot = slotForItem(item);
    if (slot) {
      updatePanelSlot(panel, item, slot);
      syncPaneRoleDom(panel.closest?.('.dv-groupview'), slot);
    }
    if (options.renderAttached !== false) renderAttachedPanelContent(item);
  }
  updatePanelInactiveOverlays();
}

// Re-run the existing Dockview renderer's shared mount path after a cached panel is evicted.
// A normal renderPanels() call cannot do this when the layout signature is unchanged: Dockview keeps
// its component instance, so its content stays empty until updateParameters tells that renderer to
// resolve the panel from panelNodes again.
function dockviewRemountPanel(item) {
  if (!dockviewLayoutActive()) return false;
  const dockviewPanel = dockviewLayoutState.api?.getPanel?.(item);
  const panelApi = dockviewPanel?.api;
  if (typeof panelApi?.updateParameters !== 'function') return false;
  panelApi.updateParameters({
    ...(typeof panelApi.getParameters === 'function' ? panelApi.getParameters() : {}),
    item,
    locale: typeof i18nActiveLocaleId === 'function' ? i18nActiveLocaleId() : '',
  });
  return panelNodes.get(item)?.isConnected === true;
}

function hideDockviewInnerPaneTabs(panel) {
  if (!panel || !dockviewLayoutEnabled()) return false;
  panel.classList.remove('dockview-inner-head-collapsed');
  const head = panel.querySelector('.panel-head');
  if (!head) return true;
  // Collapse the inner head for every non-file-explorer terminal panel — even one without a
  // .pane-tabs strip — so the dockview tab bar is the only tab row and NO pane keeps the head's
  // min-height as an inconsistent buffer above the Info Bar. (file-explorer heads keep their head.)
  if (!head.classList.contains('file-explorer-head')) {
    head.hidden = true;
    head.classList.add('dockview-inner-head-hidden');
    panel.classList.add('dockview-inner-head-collapsed');
  }
  const strip = head.querySelector('.pane-tabs');
  if (strip) {
    strip.hidden = true;
    strip.replaceChildren();
  }
  const controls = head.querySelector('.virtual-panel-controls');
  if (controls) controls.remove();
  return true;
}

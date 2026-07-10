function toggleHiddenFiles() {
  setFileExplorerManualRootMode();
  fileExplorerShowHidden = !fileExplorerShowHidden;
  storageSet(fileExplorerHiddenStorageKey, fileExplorerShowHidden ? '1' : '0');
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  if (fileExplorerRoot) refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
}

function syncDirectoryRowExpansionVisual(row, expanded, loading = false) {
  if (!row) return;
  row.classList.toggle('expanded', expanded);
  row.classList.toggle(CLS.collapsed, !expanded);
  row.classList.toggle('loading-children', loading);
  row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  const icon = row.querySelector('.file-tree-icon');
  if (icon) setDisclosureTriangleElement(icon, expanded);
}

async function expandDirectoryRow(row, fullPath, options = {}) {
  fileExplorerPendingExpansions.add(fullPath);
  syncDirectoryRowExpansionVisual(row, true, true);
  const entries = await fetchDirectory(fullPath);
  fileExplorerPendingExpansions.delete(fullPath);
  if (!entries) {
    if (!fileExplorerExpanded.has(fullPath)) syncDirectoryRowExpansionVisual(row, false, false);
    else syncDirectoryRowExpansionVisual(row, true, false);
    return;
  }
  if (options.manual === true) {
    forgetFileExplorerSyncManualCollapse(fullPath);
    resetFileExplorerAppliedSyncPlan();
  }
  // A manual collapse can land while fetchDirectory() is in flight, so an AUTO reveal (active-tab/file
  // reveal or sync expand-loop, both pass auto:true) must re-check suppression HERE -- the mutation
  // point, after the await -- or it resurrects a directory the user just collapsed. Scope this to
  // auto:true so it matches the expandFileTreeContainerToPath ancestor guard and does NOT touch the
  // remembered-state restore path (auto:false), which has its own pre-await suppression filter and must
  // be free to restore a directory across sync-target switches.
  if (options.auto === true && fileExplorerRootMode === 'sync' && fileExplorerSyncPathSuppressed(fullPath)) {
    if (!fileExplorerExpanded.has(fullPath)) syncDirectoryRowExpansionVisual(row, false, false);
    else syncDirectoryRowExpansionVisual(row, true, false);
    return;
  }
  fileExplorerExpanded.add(fullPath);
  syncDirectoryRowExpansionVisual(row, true, false);
  const existingChildren = childContainerForRow(row, fullPath);
  const children = existingChildren || createFileTreeChildContainer(fullPath);
  const nextDepth = fileTreeRowDepth(row) + 1;
  renderTreeChildren(children, fullPath, entries, nextDepth);
  if (!existingChildren) row.insertAdjacentElement('afterend', children);
  rememberFileExplorerSyncExpandedState();
  scheduleShareUiStatePublish();
  refreshLayoutUrlStateSoon();
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function collapseDirectoryRow(row, fullPath, options = {}) {
  if (options.manual === true) {
    rememberFileExplorerSyncManualCollapse(fullPath);
    resetFileExplorerAppliedSyncPlan();
  }
  fileExplorerExpanded.delete(fullPath);
  fileExplorerPendingExpansions.delete(fullPath);
  syncDirectoryRowExpansionVisual(row, false, false);
  Array.from(row.parentElement?.children || [])
    .filter(node => node.classList?.contains('file-tree-children') && node.dataset?.parent === fullPath)
    .forEach(node => node.remove());
  rememberFileExplorerSyncExpandedState();
  scheduleShareUiStatePublish();
  refreshLayoutUrlStateSoon();
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

if (fileExplorerClose) fileExplorerClose.addEventListener('click', () => toggleFileExplorer());
if (fileExplorerPathCopy) fileExplorerPathCopy.addEventListener('click', copyCurrentFileExplorerPath);
bindFileExplorerPathInput(fileExplorerPath);
bindFileExplorerHeaderActions(fileExplorer);
if (fileExplorerRootModeButton) {
  fileExplorerRootModeButton.addEventListener('click', toggleFileExplorerRootMode);
}
renderFileExplorerRootModeControls();
if (fileExplorerHiddenToggle) {
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  fileExplorerHiddenToggle.addEventListener('click', toggleHiddenFiles);
}

function updateSessionButtonStates() {
  // YO ownership is rendered into pane-tab markup, so a restored/changed worker state must
  // rebuild the shared tab strip as well as the popovers that describe it.
  updateTopbarActivityStatus();
  renderPaneTabStrips();
  refreshPaneTabSessionPopovers();
}

function sessionPopoverNodeFromHtml(html) {
  const template = document.createElement('template');
  if (template?.content !== undefined) {
    template.innerHTML = String(html || '');
    return template.content.firstElementChild;
  }
  const text = String(html || '');
  const match = text.match(/^<div class="session-popover" role="tooltip">([\s\S]*)<\/div>\s*$/);
  return {
    className: 'session-popover',
    innerHTML: match ? match[1] : text,
    getAttribute(name) {
      return name === 'role' ? 'tooltip' : null;
    },
  };
}

function refreshSessionPopoverContent(popover, html) {
  if (!popover) return;
  const fresh = sessionPopoverNodeFromHtml(html);
  if (!fresh) return;
  const keepOpen = popover.classList?.contains?.('popover-open') === true;
  const keepDetached = popover.classList?.contains?.('pane-tab-detached-popover') === true;
  popover.className = fresh.className || 'session-popover';
  if (keepOpen) popover.classList?.add?.('popover-open');
  if (keepDetached) popover.classList?.add?.('pane-tab-detached-popover');
  const role = fresh.getAttribute?.('role') || 'tooltip';
  popover.setAttribute?.('role', role);
  popover.innerHTML = fresh.innerHTML;
}

function refreshPaneTabSessionPopovers() {
  document.querySelectorAll('.pane-tab[data-pane-tab], .dockview-pane-tab[data-pane-tab]').forEach(tab => {
    const session = String(tab?.dataset?.paneTab || '').trim();
    if (!session || tabTypeForItem(session)) return;
    const popover = typeof paneTabPopoverForAnchor === 'function'
      ? paneTabPopoverForAnchor(tab)
      : tab?.querySelector?.(':scope > .session-popover');
    if (!popover || popover.classList?.contains?.('file-popover')) return;
    const info = transcriptMetadataState.payload.sessions?.[session];
    const auto = autoApproveStates.get(session)?.enabled === true;
    const state = sessionState(session, info);
    const agentKind = sessionAgentKind(session);
    refreshSessionPopoverContent(popover, sessionPopoverHtml(session, info, agentKind, auto, state));
  });
}

function bindFilePopoverActions(_container) {
  // Popover copy buttons route through the document-level data-copy-path delegate.
}

function clearTimer(timer) {
  if (timer) clearTimeout(timer);
  return null;
}

function numericOption(value, fallback) {
  const resolved = typeof value === 'function' ? value() : value;
  const number = Number(resolved);
  return Number.isFinite(number) ? number : fallback;
}

function viewportBounds(edgeGap = popoverEdgeGapPx()) {
  const viewport = appViewport();
  const width = Math.max(0, viewport.width || 0);
  const height = Math.max(0, viewport.height || 0);
  return {
    left: edgeGap,
    top: edgeGap,
    right: Math.max(edgeGap, width - edgeGap),
    bottom: Math.max(edgeGap, height - edgeGap),
  };
}

function clampToViewport(left, top, width, height, options = {}) {
  const bounds = viewportBounds(options.edgeGap ?? popoverEdgeGapPx());
  const minTop = Math.max(bounds.top, Number(options.minTop || 0));
  const maxLeft = Math.max(bounds.left, bounds.right - Math.max(0, width || 0));
  const maxTop = Math.max(minTop, bounds.bottom - Math.max(0, height || 0));
  return {
    left: Math.min(Math.max(bounds.left, left), maxLeft),
    top: Math.min(Math.max(minTop, top), maxTop),
  };
}

function stopPopoverEvent(event) {
  event.stopPropagation();
}

function closeOtherSessionPopovers(current, options = {}) {
  const force = options.force === true;
  let changed = false;
  for (const other of document.querySelectorAll('.pane-tab.popover-open, .tabber-session-tab.popover-open, .panel-popover-zone.popover-open')) {
    if (other !== current) {
      const popover = other.querySelector?.(':scope > .session-popover, :scope > .panel-detail-popover')
        || other.__yolomuxDetachedPopover;
      if (current === null && !force && popoverStillActive(other, popover)) continue;
      if (other.classList?.contains('popover-open') || popover?.classList?.contains('popover-open') || other.__yolomuxDetachedPopover?.classList?.contains('popover-open')) changed = true;
      other.classList.remove('popover-open');
      popover?.classList?.remove('popover-open');
      other.__yolomuxDetachedPopover?.classList?.remove('popover-open');
      delete other.dataset.popoverHoverState;
    }
  }
  if (changed && typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish({immediate: true});
}

function popoverLifecycleActive(anchor, popover) {
  const state = anchor?.dataset?.popoverHoverState || '';
  return Boolean(
    state === 'open'
      || state === 'pending'
      || state === 'closing'
      || anchor?.classList?.contains?.('popover-open')
      || anchor?.matches?.(':hover')
      || popover?.matches?.(':hover')
  );
}

function popoverStillActive(anchor, popover) {
  const focused = document.activeElement;
  return Boolean(
    anchor?.matches?.(':hover')
      || popover?.matches?.(':hover')
      || (focused && (anchor.contains(focused) || popover?.contains(focused)))
  );
}

function bindPopoverHover(anchor, popover, handlers) {
  const queueOpen = handlers.queueOpen || handlers.keepOpen;
  const keepOpen = handlers.keepOpen || queueOpen;
  const closeSoon = handlers.closeSoon;
  const closeNow = handlers.closeNow;
  const closeIfOutside = event => {
    const next = event?.relatedTarget;
    if (next && (anchor.contains(next) || popover?.contains(next))) return;
    closeSoon(event);
  };

  anchor.addEventListener('pointerenter', event => {
    if (browserHasCursorHover(event)) queueOpen(event);
  });
  anchor.addEventListener('pointerleave', closeIfOutside);
  anchor.addEventListener('focusin', event => {
    // Touching a tab may focus it, but a keyboard-visible focus is the only focus state that
    // should expose a hover-only popover when no cursor is present.
    if (anchor.matches?.(':focus-visible')) queueOpen(event);
  });
  anchor.addEventListener('focusout', closeIfOutside);
  anchor.addEventListener('pointerdown', event => {
    if (!browserHasCursorHover(event)) closeNow?.(event);
  });
  if (!popover) return;
  popover.addEventListener('pointerenter', event => {
    if (browserHasCursorHover(event)) keepOpen(event);
  });
  popover.addEventListener('pointerleave', closeIfOutside);
  popover.addEventListener('click', stopPopoverEvent);
  popover.addEventListener('dragstart', stopPopoverEvent);
  popover.querySelectorAll('a').forEach(link => {
    link.addEventListener('pointerenter', event => {
      if (browserHasCursorHover(event)) keepOpen(event);
    });
    link.addEventListener('click', stopPopoverEvent);
  });
}

function createHoverPopover(options) {
  const anchor = options.anchor;
  if (!anchor) return null;
  if (typeof options.onPointerMove === 'function') anchor.addEventListener('pointermove', options.onPointerMove);
  const stateClass = options.stateClass === undefined ? 'popover-open' : options.stateClass;
  let showTimer = null;
  let hideTimer = null;
  const popover = () => (typeof options.popover === 'function' ? options.popover() : options.popover);
  const canOpen = event => (typeof options.canOpen === 'function' ? options.canOpen(event) !== false : true);
  const stillActive = event => (typeof options.stillActive === 'function'
    ? options.stillActive(event) !== false
    : popoverStillActive(anchor, popover()));
  const markState = state => {
    if (!anchor.dataset) return;
    if (state) anchor.dataset.popoverHoverState = state;
    else delete anchor.dataset.popoverHoverState;
  };
  const cancelTimers = () => {
    showTimer = clearTimer(showTimer);
    hideTimer = clearTimer(hideTimer);
  };
  const closeNow = event => {
    cancelTimers();
    if (stateClass) anchor.classList.remove(stateClass);
    markState('');
    options.onClose?.(event);
    if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish({immediate: true});
  };
  const openNow = event => {
    cancelTimers();
    markState('');
    if (anchor.isConnected === false || !canOpen(event)) return;
    if (event && !stillActive(event)) return;
    if (stateClass && anchor.classList.contains(stateClass) && stillActive(event)) {
      options.position?.(event);
      options.onOpen?.(event);
      markState('open');
      if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish();
      return;
    }
    options.position?.(event);
    options.closeOthers?.();
    options.onOpen?.(event);
    if (stateClass) anchor.classList.add(stateClass);
    markState('open');
    if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish();
    const activePopover = popover();
    if (activePopover && activePopover.dataset.hoverPopoverBound !== 'true') {
      bindPopoverHover(anchor, activePopover, {queueOpen, keepOpen: openNow, closeSoon, closeNow});
      activePopover.dataset.hoverPopoverBound = 'true';
    }
  };
  function queueOpen(event) {
    hideTimer = clearTimer(hideTimer);
    markState('pending');
    if (anchor.isConnected === false || !canOpen(event)) {
      markState('');
      return;
    }
    if (stateClass && anchor.classList.contains(stateClass)) return;
    showTimer = clearTimer(showTimer);
    options.onQueue?.(event);
    const delay = numericOption(options.showDelay, popoverShowDelayMs);
    showTimer = setTimeout(() => openNow(event), Math.max(0, delay));
  }
  function closeSoon(event) {
    showTimer = clearTimer(showTimer);
    hideTimer = clearTimer(hideTimer);
    markState('closing');
    const delay = numericOption(options.hideDelay, popoverHideDelayMs);
    hideTimer = setTimeout(() => {
      if (anchor.isConnected === false) {
        markState('');
        hideTimer = null;
        if (stateClass) anchor.classList.remove(stateClass);
        options.onClose?.(event);
        if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish({immediate: true});
        return;
      }
      if (stillActive(event)) {
        markState('open');
        hideTimer = null;
        return;
      }
      if (stateClass) anchor.classList.remove(stateClass);
      markState('');
      hideTimer = null;
      options.onClose?.(event);
      if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish({immediate: true});
    }, Math.max(0, delay));
  }
  const initialPopover = popover();
  bindPopoverHover(anchor, initialPopover, {queueOpen, keepOpen: openNow, closeSoon, closeNow});
  if (initialPopover) initialPopover.dataset.hoverPopoverBound = 'true';
  return {queueOpen, openNow, closeSoon, closeNow, cancelTimers};
}

function tabInteractionControllerForApp() {
  if (tabInteractionController) return tabInteractionController;
  let touchPress = null;
  let suppressContextUntil = 0;
  const cancelCurrentTouchPress = event => {
    if (!touchPress) return;
    const opened = touchPress.opened;
    clearTimer(touchPress.timer);
    touchPress = null;
    // A tab activation may replace its anchor before pointerup reaches that anchor. Own the
    // terminal touch event at document capture so a short tap cannot leave its long-press timer
    // behind to open the action sheet after the selected panel has rendered.
    if (opened && event?.cancelable) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  document.addEventListener('pointerup', event => {
    if (event.pointerType === 'touch') cancelCurrentTouchPress(event);
  }, true);
  document.addEventListener('pointercancel', event => {
    if (event.pointerType === 'touch') cancelCurrentTouchPress(event);
  }, true);
  const currentDescriptor = descriptor => descriptor?.anchor?.__yolomuxTabInteractionDescriptor || descriptor;
  const close = () => {
    touchPress = null;
    closeOtherSessionPopovers(null, {force: true});
    closeSessionContextMenu();
  };
  const pointForAnchor = anchor => {
    const rect = anchor?.getBoundingClientRect?.();
    return {
      x: Math.round((rect?.left || 0) + (rect?.width || 0) / 2),
      y: Math.round((rect?.bottom || 0) + 6),
    };
  };
  const showActions = (descriptor, event = null, options = {}) => {
    descriptor = currentDescriptor(descriptor);
    const item = String(descriptor?.item || '').trim();
    if (!item || !isLayoutItem(item)) return false;
    const eventPoint = event && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)
      ? {x: event.clientX, y: event.clientY}
      : pointForAnchor(descriptor.anchor);
    // A touch long press is an anchored tab action, not a global bottom sheet. Use the tab's
    // lower edge so the compact action surface opens beside the tab that owns it.
    const point = options.presentation === 'sheet' ? pointForAnchor(descriptor.anchor) : eventPoint;
    closeOtherSessionPopovers(null, {force: true});
    showTabContextMenu(item, point.x, point.y, {
      tab: descriptor.anchor,
      sourceSlot: descriptor.sourceSlot?.() || slotForItem(item),
      presentation: options.presentation || 'context',
    });
    return true;
  };
  const bindDetail = descriptor => {
    const {anchor} = descriptor;
    const detailPopover = () => {
      const current = currentDescriptor(descriptor);
      return typeof current?.popover === 'function' ? current.popover() : current?.popover;
    };
    if (!detailPopover()) return null;
    return createHoverPopover({
      anchor,
      popover: detailPopover,
      showDelay: () => (document.querySelector('.pane-tab.popover-open, .tabber-session-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
      hideDelay: () => popoverHideDelayMs,
      canOpen: () => !appMenuIsOpen() && !contextMenuIsOpen() && !topbar?.matches?.(':hover'),
      onQueue: event => currentDescriptor(descriptor)?.positionDetail?.(event),
      onOpen: event => {
        currentDescriptor(descriptor)?.onDetailOpen?.(event);
      },
      onClose: event => currentDescriptor(descriptor)?.onDetailClose?.(event),
      position: event => currentDescriptor(descriptor)?.positionDetail?.(event),
      closeOthers: () => closeOtherSessionPopovers(anchor),
    });
  };
  const bind = descriptor => {
    const anchor = descriptor?.anchor;
    if (!anchor) return null;
    anchor.__yolomuxTabInteractionDescriptor = descriptor;
    if (anchor.dataset?.tabInteractionBound === 'true') return {showActions: event => showActions(descriptor, event), close};
    if (anchor.dataset) anchor.dataset.tabInteractionBound = 'true';
    const detail = bindDetail(descriptor);
    const cancelTouchPress = () => {
      if (!touchPress || touchPress.anchor !== anchor) return;
      cancelCurrentTouchPress();
    };
    const movedBeyondThreshold = event => {
      if (!touchPress || touchPress.anchor !== anchor) return false;
      return Math.hypot(event.clientX - touchPress.x, event.clientY - touchPress.y) > tabTouchLongPressMoveThresholdPx;
    };
    anchor.addEventListener('contextmenu', event => {
      if (Date.now() < suppressContextUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      showActions(descriptor, event);
    });
    anchor.addEventListener('keydown', event => {
      if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
      event.preventDefault();
      event.stopPropagation();
      showActions(descriptor);
    }, true);
    anchor.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch' || event.button !== 0) return;
      cancelTouchPress();
      const current = currentDescriptor(descriptor);
      const item = String(current?.item || '');
      const sourceSlot = current?.sourceSlot?.() || slotForItem(item);
      const record = {
        anchor,
        x: event.clientX,
        y: event.clientY,
        timer: null,
        opened: false,
        sourceSlot,
        priorActiveItem: sourceSlot ? activeItemForSide(sourceSlot) : '',
      };
      record.timer = setTimeout(() => {
        if (touchPress !== record || movedBeyondThreshold({clientX: record.x, clientY: record.y})) return;
        record.opened = showActions(descriptor, {clientX: record.x, clientY: record.y}, {presentation: 'sheet'});
        if (record.opened) {
          // Dockview activates a tab on touch pointer-down before the browser can know this is a
          // long press. Put the prior visible tab back before opening the sheet; a long press is
          // an action gesture, never an activation or keyboard-focus gesture.
          if (record.sourceSlot && record.priorActiveItem && record.priorActiveItem !== item) {
            activatePaneTab(record.sourceSlot, record.priorActiveItem);
          }
          suppressContextUntil = Date.now() + tabTouchLongPressDelayMs;
        }
      }, tabTouchLongPressDelayMs);
      touchPress = record;
    }, true);
    anchor.addEventListener('pointermove', event => {
      if (event.pointerType === 'touch' && movedBeyondThreshold(event)) cancelTouchPress();
    }, true);
    anchor.addEventListener('pointerup', event => {
      if (event.pointerType !== 'touch' || !touchPress || touchPress.anchor !== anchor) return;
      const opened = touchPress.opened;
      cancelTouchPress();
      if (!opened) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    anchor.addEventListener('pointercancel', cancelTouchPress, true);
    anchor.addEventListener('dragstart', cancelTouchPress, true);
    return {detail, showActions: event => showActions(descriptor, event), close};
  };
  tabInteractionController = {bind, close, showActions};
  return tabInteractionController;
}

function bindTabInteraction(descriptor) {
  return tabInteractionControllerForApp().bind(descriptor);
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

// Tab buttons are also drag handles, so activation waits until pointer release proves it was a click.
function bindTabActivation(node, activate, options = {}) {
  let pointerCandidate = false;
  let pointerActivated = false;
  let dragged = false;
  let startX = 0;
  let startY = 0;
  const stop = event => {
    if (options.stopPropagation) event.stopPropagation();
  };
  const ignored = event => options.ignore?.(event) === true;
  const resetPointer = () => {
    pointerCandidate = false;
    dragged = false;
  };

  node.addEventListener('pointerdown', event => {
    if (event.button !== 0 || ignored(event)) return;
    pointerCandidate = true;
    pointerActivated = false;
    dragged = false;
    startX = event.clientX;
    startY = event.clientY;
  });
  node.addEventListener('pointerup', event => {
    if (!pointerCandidate || event.button !== 0 || ignored(event)) {
      resetPointer();
      return;
    }
    const moved = Math.abs(event.clientX - startX) > 4 || Math.abs(event.clientY - startY) > 4;
    const wasDragged = dragged;
    resetPointer();
    if (wasDragged || moved) return;
    event.preventDefault();
    stop(event);
    pointerActivated = true;
    activate(event);
  });
  node.addEventListener('click', event => {
    if (ignored(event)) return;
    if (pointerActivated) {
      pointerActivated = false;
      event.preventDefault();
      stop(event);
      return;
    }
    event.preventDefault();
    stop(event);
    activate(event);
  });
  node.addEventListener('dragstart', () => {
    dragged = true;
    pointerCandidate = false;
    pointerActivated = false;
  });
  node.addEventListener('dragend', resetPointer);
}

// Tabs, headers, and popovers all use these helpers so badge precedence stays consistent.
function metaSeparatorHtml(classes = '') {
  const extraClasses = String(classes || '').trim();
  return `<span class="meta-sep${extraClasses ? ` ${extraClasses}` : ''}" aria-hidden="true">|</span>`;
}

function metaJoin(parts) {
  return parts.filter(Boolean).join(metaSeparatorHtml());
}

function sessionNumberNameHtml(session, options = {}) {
  const label = sessionLabel(session);
  const className = numericSessionName(label) !== null ? 'session-button-number' : 'session-button-name';
  const labelHtml = options.labelHtml !== undefined ? String(options.labelHtml) : esc(label);
  return `<strong class="${className} session-button-identifier">[${labelHtml}]</strong>`;
}

function yoloMarkerHtml(session, auto, options = {}) {
  const payload = options.payload || autoApproveStates.get(session);
  const locked = !auto && (options.locked === true || (options.locked !== false && autoApproveEnabledElsewhere(payload)));
  if (!auto && !locked && options.enabledOnly !== false) return '';
  const classes = ['session-yolo-marker'];
  if (auto) classes.push('active');
  else if (locked) classes.push('locked');
  else classes.push('inactive');
  if (options.yoloWorking) classes.push('working');
  if (readOnlyMode) classes.push('readonly');
  const yoloAttr = ` data-yolo-session="${esc(session)}"`;
  const toggleAttr = options.toggle && !readOnlyMode ? ` data-auto-session="${esc(session)}" data-action="pane-tab-auto-approve"` : '';
  const stateText = auto ? t('yolo.state.onHere') : (locked ? t('yolo.state.onElsewhere') : t('state.off'));
  const title = options.toggle && readOnlyMode
    ? t('yolo.titleReadonly', {state: stateText, session: sessionLabel(session)})
    : (options.toggle ? t('yolo.titleForSession', {state: stateText, session: sessionLabel(session)}) : t('yolo.title', {state: stateText}));
  return `<span class="${esc(classes.join(' '))}"${yoloAttr}${toggleAttr} title="${esc(title)}" aria-label="${esc(title)}">${esc(t('brand.marker'))}</span>`;
}

function sessionShouldOfferYoloMarker(session, info, payload, auto, state = null) {
  if (auto) return true;
  if (autoApproveEnabledElsewhere(payload)) return true;
  const tabState = state || sessionState(session, info);
  return [STATE_KEY.needsApproval, STATE_KEY.needsInput].includes(tabState?.key) && tabState?.promptAttentionCleared !== true;
}

function sessionStatusBallPlaceholderHtml() {
  // Every session Tab reserves the same ball column. Keeping the placeholder inside the canonical
  // activity wrapper makes its geometry follow the real red/yellow/green status ball exactly.
  return '<span class="session-agent-activity-marker session-agent-activity-marker--placeholder" aria-hidden="true"><span class="agent-window-activity agent-window-activity--status-only"><span class="agent-window-status-dot"></span></span></span>';
}

function sessionTabLeadingActivityContainerHtml(session, info, auto, options = {}) {
  return `<span class="session-tab-leading-activity">${sessionTabLeadingActivityHtml(session, info, auto, options)}</span>`;
}

function sessionTabLeadingActivityHtml(session, info, auto, options = {}) {
  const payload = options.payload || autoApproveStates.get(session);
  const state = Object.prototype.hasOwnProperty.call(options, 'state') ? options.state : sessionState(session, info);
  const offerYolo = sessionShouldOfferYoloMarker(session, info, payload, auto, state);
  const yoloHtml = offerYolo
    ? yoloMarkerHtml(session, auto, {...options, enabledOnly: false, yoloWorking: false, payload})
    : '';
  const statusSummary = sessionAgentWindowStatusSummary(session, info, payload);
  if (statusSummary?.agent) {
    const statusAgent = statusSummary.agent;
    const iconHtml = agentWindowActivityIconHtml(statusAgent.kind, statusAgent.state, agentWindowIdleSeconds(statusAgent), {
      ...agentWindowActivityOptionsForStatus(statusAgent, session),
      item: statusSummary.item,
      label: statusSummary.label,
      statusOnly: true,
    });
    if (iconHtml) {
      return `${yoloHtml}<span class="session-agent-activity-marker">${iconHtml}</span>`;
    }
  }
  const fallbackYoloHtml = offerYolo
    ? yoloMarkerHtml(session, auto, {
      ...options,
      enabledOnly: false,
      yoloWorking: sessionYoloIsWorking(session, payload),
      payload,
    })
    : '';
  return `${fallbackYoloHtml}${sessionStatusBallPlaceholderHtml()}`;
}

// Hovering a tab keeps its popover DOM mounted. Do not let that presentation concern freeze the
// status marker: its color is shared live state and must agree with the Tabber row immediately.
function syncSessionTabLeadingActivityChrome() {
  if (typeof document === 'undefined') return 0;
  let updated = 0;
  for (const tab of document.querySelectorAll('.pane-tab[data-pane-tab], .tmux-pane-tab-token[data-pane-tab]')) {
    const session = String(tab.dataset?.paneTab || '').trim();
    if (!session || !isTmuxSession(session)) continue;
    const leading = tab.querySelector?.(':scope > .session-tab-leading-activity, .pane-tab-core > .session-tab-leading-activity');
    if (!leading) continue;
    const info = transcriptMetadataState.payload.sessions?.[session];
    const auto = autoApproveStates.get(session)?.enabled === true;
    const state = sessionState(session, info);
    const html = sessionTabLeadingActivityHtml(session, info, auto, {enabledOnly: false, toggle: !readOnlyMode, state});
    if (leading.innerHTML === html) continue;
    leading.innerHTML = html;
    updated += 1;
  }
  return updated;
}

function pullRequestCompactBadgesHtml(session, pr) {
  const numberHtml = pullRequestNumberIndicatorHtml(session, pr);
  const statusHtml = pullRequestStatusIndicatorHtml(session, pr);
  const ciHtml = pullRequestCiIndicatorHtml(session, pr);
  const reviewHtml = pullRequestApprovalIndicatorHtml(session, pr);
  return [numberHtml, statusHtml, ciHtml, reviewHtml].filter(Boolean).join('');
}

function applySessionStateClasses(node, state) {
  node.classList.toggle(STATE_CLASS.needsAttention, state?.attention === true);
  node.classList.toggle(STATE_CLASS.needsInput, state?.key === STATE_KEY.needsInput && state?.attention === true);
  node.classList.toggle(STATE_CLASS.needsExec, state?.key === STATE_KEY.needsApproval && state?.attention === true);
  node.classList.toggle(STATE_CLASS.needsBlocked, state?.key === STATE_KEY.blocked);
  syncAttentionAnimation(node, state?.attention === true);
}

function panelHeaderStateHtml(state) {
  return state ? sessionStateHtml(state) : '';
}

function currentBranchSubject(git) {
  const branches = git?.other_branches?.branches || [];
  const current = branches.find(branch => branch.current);
  return current?.subject || '';
}

function isDefaultBranch(git) {
  return ['main', 'master'].includes(String(git?.branch || ''));
}

function gitHeadSubject(git) {
  return String(git?.head || '').replace(/^[0-9a-f]{7,40}\s+/, '');
}

function pullRequestNumberFromSubject(subject) {
  const match = String(subject || '').match(/\(#(\d+)\)\s*$/);
  return match ? Number(match[1]) : null;
}

function subjectWithoutPullRequestNumber(subject) {
  return String(subject || '').replace(/\s*\(#\d+\)\s*$/, '').trim();
}

function githubPullRequestUrlFromGit(git, number) {
  const repoUrl = git?.github_repo?.url;
  return repoUrl && number ? `${repoUrl}/pull/${number}` : '';
}

function pullRequestWithUrl(git, pr) {
  if (!pr?.number) return null;
  return {
    ...pr,
    url: pr.url || githubPullRequestUrlFromGit(git, pr.number),
  };
}

function defaultBranchHeadPullRequestForGit(info, git) {
  const project = info?.project || {};
  if (!isDefaultBranch(git)) return null;
  const subject = gitHeadSubject(git);
  const number = pullRequestNumberFromSubject(subject);
  if (!number) return null;
  const projectGitRoot = repoRootKey(project.git?.root);
  const showingPrimaryGit = !projectGitRoot || projectGitRoot === repoRootKey(git?.root);
  const existing = showingPrimaryGit && project.pull_request?.number === number ? project.pull_request : {};
  const title = subjectWithoutPullRequestNumber(existing.title || subject);
  const description = subjectWithoutPullRequestNumber(existing.description || subject);
  return {
    ...existing,
    number,
    title,
    description,
    url: existing.url || githubPullRequestUrlFromGit(git, number),
    checks: existing.checks || {state: 'unknown'},
    // A (#NNNN) in the default branch's HEAD merge commit means that PR is, by definition, merged
    // (it is in main's history). Keep that fact semantic; shared lifecycle rendering localizes it.
    merged: true,
    source_only: true,
  };
}

function defaultBranchHeadPullRequest(info) {
  return defaultBranchHeadPullRequestForGit(info, info?.project?.git);
}

function currentBranchInventoryPullRequestForGit(git) {
  const branches = git?.other_branches?.branches || [];
  return branches.find(branch => branch.current === true)?.pull_request || null;
}

function currentBranchInventoryPullRequest(info) {
  return currentBranchInventoryPullRequestForGit(info?.project?.git);
}

function displayPullRequestForGit(info, git) {
  const project = info?.project || {};
  const targetGit = git || project.git;
  const projectGitRoot = repoRootKey(project.git?.root);
  const showingPrimaryGit = !targetGit || !projectGitRoot || projectGitRoot === repoRootKey(targetGit.root);
  return defaultBranchHeadPullRequestForGit(info, targetGit)
    || (showingPrimaryGit ? project.pull_request : null)
    || currentBranchInventoryPullRequestForGit(targetGit);
}

function displayPullRequest(info) {
  return displayPullRequestForGit(info, info?.project?.git);
}

function metadataBadgePulseClass(session, badge) {
  if (!session) return '';
  const until = sessionStatusRecord(session)?.metadataBadgePulseUntil.get(badge);
  if (!until || until <= Date.now()) return '';
  return ' metadata-pulse';
}

function metadataBadgeClasses(session, badge, classes) {
  return `${classes}${metadataBadgePulseClass(session, badge)}`;
}

function updateMetadataBadgePulses(meta) {
  const now = Date.now();
  for (const record of sessionStatusRecords.values()) {
    for (const [badge, until] of record.metadataBadgePulseUntil.entries()) {
      if (until <= now) record.metadataBadgePulseUntil.delete(badge);
    }
  }
  for (const [session, info] of Object.entries(meta?.sessions || {})) {
    const pulses = info?.metadata_badge_pulse_remaining_ms || {};
    const pulseMap = sessionStatusRecord(session, true)?.metadataBadgePulseUntil;
    if (!pulseMap) continue;
    for (const badge of ['main', 'pr', 'status', 'ci']) {
      const remaining = Number(pulses[badge] || 0);
      if (remaining > 0) {
        pulseMap.set(badge, now + remaining);
      }
    }
  }
}

function defaultBranchBadgeHtml(session, info) {
  if (!isDefaultBranch(info?.project?.git)) return '';
  return `<span class="${metadataBadgeClasses(session, 'main', 'ci-indicator tab-symbol branch-indicator')}">MAIN</span>`;
}

function sessionWorkDescriptionSource(session, info) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const title = pr.title || pr.description || '';
    const prefix = pullRequestLinkLabel(pr);
    return title ? `${prefix}: ${title}` : prefix;
  }
  const linear = project.linear || [];
  const issue = linear.find(item => item.title);
  if (issue) return `${issue.identifier}: ${issue.title}`;
  const subject = currentBranchSubject(git);
  if (subject) return subject;
  if (git?.branch) return shortBranch(git.branch);
  return projectDirName(session, info);
}

function sessionWorkDescription(session, info, limit = 96) {
  const description = sessionWorkDescriptionSource(session, info);
  return Number.isFinite(limit) && limit > 0 ? shortText(description, limit) : description;
}

function sessionTabDescription(session, info) {
  // A fresh Xterm can be fully interactive before transcript/repository metadata exists. Its tab
  // already identifies the session and tmux window, so a generic "Loading..." description implies
  // the terminal itself is blocked. Omit only this tab-local metadata until a real description arrives.
  if (!info) return '';
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const title = pr.title || pr.description || '';
    if (title) return shortText(title, 72);
  }
  return sessionWorkDescription(session, info, 72);
}

function tabMenuDetailText(item, info = transcriptMetadataState.payload.sessions?.[item]) {
  if (isInfoItem(item)) return t('tab.info.detail');
  const project = info?.project || {};
  const git = project.git;
  const parts = [];
  if (git?.branch) parts.push(git.branch);
  const path = panelFullPath(item, info);
  if (path) parts.push(compactHomePath(path));
  const pr = displayPullRequest(info);
  const linear = (project.linear || []).map(issue => issue.identifier).filter(Boolean).join(', ');
  if (linear) parts.push(linear);
  if (pr?.number) {
    parts.push(pullRequestLinkLabel(pr));
  }
  const desc = sessionWorkDescription(item, info, 180);
  if (desc && !parts.includes(desc)) parts.push(desc);
  return parts.join(' · ') || itemLabel(item);
}

function projectDirName(session, info) {
  if (!info) return t('common.loading');
  const {gitRoot, gitCwd, selectedPath} = sessionTranscriptInfo(session);
  const repo = selectedSessionRepo(session, info);
  const path = repo?.cwd || repo?.root || gitRoot || gitCwd || selectedPath;
  return pathBasename(path) || t('info.missing.path');
}

function pathBasename(path) {
  const text = String(path || '').replace(/\/+$/, '');
  if (!text) return '';
  const parts = text.split('/');
  return parts[parts.length - 1] || '';
}

function filePopoverHtml(item) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const rows = filePopoverRows(path, state);
  return `<div class="session-popover file-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(basenameOf(path))}</div>
      </div>
    </div>
    ${rows.join('')}
  </div>`;
}

function filePopoverRows(path, state = {}) {
  const kind = state.kind === 'image' ? t('popover.kind.image') : state.kind === 'text' ? t('popover.kind.text') : state.kind || t('popover.kind.file');
  const status = state.dirty ? t('state.modified') : state.loading ? t('common.loading') : state.error ? String(state.error) : kind;
  const rows = [
    popoverRow(t('common.field.path'), filePopoverPathHtml(path)),
  ];
  if (status && status !== kind) rows.push(popoverPairRow(t('popover.type'), esc(kind), t('popover.status'), esc(status)));
  else rows.push(popoverRow(t('popover.type'), esc(kind)));
  if (Number.isFinite(state.size)) rows.push(popoverRow(t('popover.size'), formatFileSize(state.size)));
  return rows;
}

function pathCopyButtonHtml(path, options = {}) {
  const className = ['path-copy-button', options.className || ''].filter(Boolean).join(' ');
  const dataAttr = options.dataAttr || 'data-copy-path';
  const title = options.title || t('contextmenu.copyPath');
  return `<button type="button" class="${esc(className)}" ${dataAttr}="${esc(path)}" title="${esc(title)}" aria-label="${esc(options.ariaLabel || title)}"></button>`;
}

function popoverCopyValueHtml(value, options = {}) {
  const text = String(value || '').trim();
  if (!text) return '';
  const copyLabel = options.title || t('common.copy');
  return `<span class="popover-copy-value">${esc(text)}</span>${pathCopyButtonHtml(text, {className: options.className || 'popover-copy-button', title: copyLabel, ariaLabel: options.ariaLabel || copyLabel})}`;
}

function filePopoverPathHtml(path) {
  return `<span class="popover-copy-value">${esc(path)}</span>${pathCopyButtonHtml(path, {className: 'popover-copy-button'})}`;
}

function sessionPopoverSubtitleHtml(session, info, fallback = '') {
  const project = info?.project || {};
  const git = project.git;
  const pr = pullRequestWithUrl(git, displayPullRequest(info));
  const chips = [];
  if (isDefaultBranch(git)) chips.push(defaultBranchBadgeHtml(session, info));
  if (pr?.number) chips.push(pullRequestNumberChipLinkHtml(session, pr));
  const text = pr?.number
    ? shortText(pr.title || pr.description || '', 220)
    : String(fallback || '');
  const textHtml = text ? `<span class="popover-subtitle-text">${esc(text)}</span>` : '';
  return `<div class="popover-subtitle">${chips.join('')}${textHtml}</div>`;
}

function sessionBranchValueHtml(session, info) {
  const git = info?.project?.git;
  if (!git?.branch) return '';
  const branchHtml = isDefaultBranch(git)
    ? defaultBranchBadgeHtml(session, info)
    : branchLinkHtml(git, git.branch);
  return `${branchHtml}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`;
}

function pullRequestPopoverRowHtml(session, pr) {
  const prParts = [pullRequestNumberChipLinkHtml(session, pr), pullRequestAuthorHtml(pr)].filter(Boolean);
  const checks = pullRequestChecksHtml(pr);
  if (checks) prParts.push(checks);
  const review = pullRequestReviewInlineHtml(pr);
  if (review) prParts.push(review);
  return metaJoin(prParts);
}

function gitStatusHasFacts(git) {
  return Number.isFinite(git?.dirty_count) || Number.isFinite(git?.ahead) || Number.isFinite(git?.behind);
}

function popoverActivityText(session, git) {
  const text = String(sessionActivitySummary(session)?.local || '').trim();
  if (!text) return '';
  return gitStatusHasFacts(git)
    ? text.replace(/\s*Status check:\s*[^.]+\.?\s*$/i, '').trim()
    : text;
}

function gitHeadValueHtml(git) {
  const head = String(git?.head || '').trim();
  const match = head.match(/^([0-9a-f]{7,40})\b/i);
  if (match) return esc(match[1]);
  return esc(shortText(subjectWithoutPullRequestNumber(gitHeadSubject(git)), 120));
}

function sessionPopoverAgentRecencyText(agent, nowSeconds = Date.now() / 1000, options = {}) {
  const lastActive = Number(agent?.idle_since || agent?.last_active_ts || 0);
  let timestamp = Number.isFinite(lastActive) && lastActive > 0 ? lastActive : nowSeconds;
  if (options.forceAgo === true && timestamp >= nowSeconds) timestamp = nowSeconds - 1;
  return sessionFileRelativeTimeText(timestamp, nowSeconds);
}

function sessionPopoverAgentStateText(agent, nowSeconds = Date.now() / 1000) {
  const state = String(agent?.state || STATE_KEY.idle);
  if (agentWindowIsWorkingState(state)) {
    const elapsed = Number(agent?.working_elapsed_seconds);
    return Number.isFinite(elapsed) && elapsed >= 0
      ? t('state.workingFor', {duration: compactElapsedDurationText(elapsed)})
      : stateDef(STATE_KEY.working).label;
  }
  if (agentWindowIsAttentionState(state)) return sessionPopoverAgentRecencyText(agent, nowSeconds, {forceAgo: true});
  const lastActive = Number(agent?.idle_since || agent?.last_active_ts || 0);
  return Number.isFinite(lastActive) && lastActive > 0 ? sessionPopoverAgentRecencyText(agent, nowSeconds) : stateDef(STATE_KEY.idle).label;
}

function sessionPopoverAgentStatusHtml(agent, nowSeconds = Date.now() / 1000, className = 'session-agent-status') {
  const text = sessionPopoverAgentStateText(agent, nowSeconds);
  if (agentWindowIsAttentionState(agent?.state)) return statusIndicatorLabelHtml(text, 'attention', className, 'agent-status-attention');
  return `<span class="${esc(className)}">${esc(text)}</span>`;
}

function tmuxSessionDescriptorLabel(label) {
  const text = String(label || '').trim();
  if (/^tmux\s+session\b/i.test(text)) return text;
  return t('common.tmuxSession', {label: text});
}

function tmuxWindowDescriptorLabel(label) {
  const text = String(label || '').trim();
  if (/^tmux\s+window\b/i.test(text)) return text;
  return t('popover.tmuxWindow', {label: text});
}

function sessionPopoverWindowPidByIndex(info) {
  if (typeof tmuxWindowRecords !== 'function') return new Map();
  return new Map(tmuxWindowRecords(info?.panes || [])
    .map(record => [tmuxWindowIndexKey(record.index ?? record.indexText), record.pid])
    .filter(([index, pid]) => index !== null && Number.isFinite(Number(pid)) && Number(pid) > 0));
}

function sessionPopoverAgentWindowPid(agent, pidByIndex) {
  const directPid = Number(agent?.pid || agent?.process_label_pid || 0);
  if (Number.isFinite(directPid) && directPid > 0) return Math.floor(directPid);
  const index = agentWindowIndex(agent);
  const sharedPid = index !== null ? Number(pidByIndex.get(index)) : null;
  if (Number.isFinite(sharedPid) && sharedPid > 0) return Math.floor(sharedPid);
  return null;
}

function sessionPopoverActiveWindowIndex(session, info) {
  if (typeof tmuxWindowCurrentActiveIndex === 'function') return tmuxWindowCurrentActiveIndex(session, info);
  const active = typeof tmuxWindowInfoActiveIndex === 'function' ? tmuxWindowInfoActiveIndex(info) : null;
  if (active !== null) return active;
  const pane = info?.selected_pane || null;
  return tmuxWindowIndexKey(pane?.window ?? pane?.window_index);
}

function sessionPopoverSortedAgentWindows(session, info, autoPayload) {
  const activeWindowIndex = sessionPopoverActiveWindowIndex(session, info);
  const pidByIndex = sessionPopoverWindowPidByIndex(info);
  return sessionAgentWindowStatusPayloads(session, info, autoPayload)
    .map((agent, index) => ({
      ...agent,
      _session: session,
      _index: index,
      kind: String(agent?.kind || '').toLowerCase(),
      state: String(agent?.state || STATE_KEY.idle),
      current: typeof agentWindowPayloadCurrent === 'function' && agentWindowPayloadCurrent(agent) !== null
        ? agentWindowPayloadCurrent(agent) === true
        : activeWindowIndex !== null && agentWindowIndex(agent) === activeWindowIndex,
      pid: sessionPopoverAgentWindowPid(agent, pidByIndex),
    }))
    .filter(agent => ['claude', 'codex'].includes(agent.kind))
    .sort((left, right) => agentWindowStateRank(left.state) - agentWindowStateRank(right.state)
      || Number(left.window_index ?? 9999) - Number(right.window_index ?? 9999)
      || left._index - right._index);
}

function sessionPopoverAgentWindowRowHtml(agent, nowSeconds = Date.now() / 1000) {
  const working = agentWindowIsWorkingState(agent.state);
  const attention = agentWindowIsAttentionState(agent.state);
  const descriptor = tmuxWindowDescriptorLabel(agentWindowCanonicalLabel(agent.window_index ?? agent.window, agent.kind, agent.window_label || agent.kind));
  const label = typeof tmuxWindowDisplayLabel === 'function' ? tmuxWindowDisplayLabel(descriptor, agent.pid) : descriptor;
  const classes = ['session-agent-row', `state-${agent.state}`];
  if (working) classes.push('working');
  if (attention) classes.push('attention');
  if (agent.current === true) classes.push('current');
  // Match the tmux-window button and attention toast: the state glyph (play/stop/pause)
  // comes before the stable Claude/Codex identity everywhere a sub-window is shown.
  const activityHtml = agentWindowActivityIconHtmlForStatus(agent, agent.kind, agent._session || '', {statusBeforeAgent: true});
  return `<div class="${esc(classes.join(' '))}">
    <span class="session-agent-kind">${activityHtml}</span>${esc(label)}
    <span class="session-agent-sep">—</span>
    ${sessionPopoverAgentStatusHtml(agent, nowSeconds)}
  </div>`;
}

function sessionPopoverWindowMetadataItems(session, info, agentRows) {
  const agents = Array.isArray(agentRows) ? agentRows : [];
  if (!agents.length) return [];
  return agents.map(agent => {
    const pathEntries = typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : [];
    const paths = pathEntries.map(item => item.path).filter(Boolean);
    const git = typeof agentWindowPrimaryGit === 'function' ? agentWindowPrimaryGit(agent) : (agent?.git || null);
    const path = paths[0] || (typeof agentWindowPrimaryPath === 'function' ? agentWindowPrimaryPath(agent) : String(agent?.path || ''));
    const meta = {
      window: String(agent?.window ?? ''),
      window_index: agentWindowIndex(agent),
      window_name: String(agent?.window_name || ''),
      path,
      paths,
      path_entries: pathEntries,
      git,
    };
    const html = windowMetadataRowsHtml(meta);
    return html ? {agent, meta, html} : null;
  }).filter(Boolean);
}

function sessionPopoverAgentWindowHtml(session, info, autoPayload, agentRows = null, metadataItems = null) {
  const agents = Array.isArray(agentRows) ? agentRows : sessionPopoverSortedAgentWindows(session, info, autoPayload);
  if (!agents.length) {
    return `<div class="session-agent-list empty"><div class="session-agent-empty">${esc(t('yoagent.emptyPerSession'))}</div></div>`;
  }
  const nowSeconds = Date.now() / 1000;
  const items = Array.isArray(metadataItems) ? metadataItems : sessionPopoverWindowMetadataItems(session, info, agents);
  const metadataByWindow = new Map(items.map(item => [agentWindowIndex(item.agent), item]));
  const sharedMetadata = items.length > 1 && items.length === agents.length && new Set(items.map(item => sessionWindowMetadataSignature(item.meta))).size === 1;
  const rows = agents.map(agent => {
    const row = sessionPopoverAgentWindowRowHtml(agent, nowSeconds);
    const item = metadataByWindow.get(agentWindowIndex(agent));
    const transcriptHtml = agentTranscriptRowsHtml(agent);
    if ((!item || sharedMetadata) && !transcriptHtml) return row;
    const metadataHtml = item && !sharedMetadata ? item.html : '';
    return `<div class="session-agent-window-block">${row}<div class="session-window-metadata">${metadataHtml}${transcriptHtml}</div></div>`;
  }).join('');
  const sharedRows = sharedMetadata ? `<div class="session-window-metadata-list shared">${items[0].html}</div>` : '';
  const metadataClass = items.length ? ' has-window-metadata' : '';
  return `<div class="session-agent-list${metadataClass}">${rows}${sharedRows}</div>`;
}

function sessionWindowMetadataSignature(row) {
  const git = row?.git || {};
  const paths = Array.isArray(row?.paths) ? row.paths.join('\u0001') : (row?.path || '');
  return [paths, git.root || '', git.branch || '', git.worktree?.path || ''].join('\u0000');
}

function windowMetadataBranchHtml(git) {
  if (!git?.branch) return '';
  return `${branchLinkHtml(git, git.branch)}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`;
}

function worktreePopoverValueHtml(worktree) {
  return esc(worktreeDisplayText(worktree));
}

function windowMetadataRowsHtml(row) {
  if (!row) return '';
  const git = row.git || {};
  const rows = [];
  const paths = Array.isArray(row.paths) ? row.paths.map(path => String(path || '').trim()).filter(Boolean) : [];
  const displayPaths = paths.length ? paths : (row.path ? [String(row.path)] : []);
  for (const path of displayPaths) rows.push(popoverRow(t('common.field.path'), filePopoverPathHtml(path)));
  if (git.branch) rows.push(popoverRow(t('common.field.branch'), windowMetadataBranchHtml(git)));
  if (git.root && !displayPaths.includes(git.root)) rows.push(popoverRow(t('popover.repo'), git.root));
  if (git.worktree) rows.push(popoverRow(t('popover.worktree'), worktreePopoverValueHtml(git.worktree)));
  if (git.head) rows.push(popoverRow('HEAD', gitHeadValueHtml(git)));
  if (gitStatusHasFacts(git)) rows.push(popoverRow(t('popover.git'), gitStatusText(git)));
  return rows.join('');
}

function agentTranscriptId(agent) {
  const direct = String(agent?.transcript_id || agent?.agent_session_id || agent?.session_id || '').trim();
  if (direct) return direct;
  const transcript = String(agent?.transcript || '').trim();
  if (!transcript) return '';
  return basenameOf(transcript).replace(/\.[^.]+$/, '');
}

function agentTranscriptRowsHtml(agent) {
  const transcript = String(agent?.transcript || '').trim();
  if (!transcript) return '';
  const transcriptId = agentTranscriptId(agent);
  const rows = [];
  if (transcriptId) rows.push(popoverRow(t('popover.sessionId'), popoverCopyValueHtml(transcriptId, {title: t('popover.copySessionId')})));
  rows.push(popoverRow(t('common.transcript'), popoverCopyValueHtml(transcript, {title: t('common.copyTranscriptPath')})));
  return rows.join('');
}

function sessionPopoverHtml(session, info, agentKind, autoEnabled, state = sessionState(session, info)) {
  const project = info?.project || {};
  const git = project.git;
  const pr = pullRequestWithUrl(git, displayPullRequest(info));
  const linear = project.linear || [];
  const pane = info?.selected_pane;
  const description = sessionWorkDescription(session, info, 220);
  const title = `${tmuxSessionDescriptorLabel(sessionLabel(session))} · ${projectDirName(session, info)}`;
  const subtitle = description || git?.branch || pane?.current_path || t('git.noCheckout');
  const subtitleHtml = sessionPopoverSubtitleHtml(session, info, subtitle);
  const rows = [];
  const stateValue = `${sessionStateHtml(state)} <span class="meta-muted">${esc(state.reason)}</span>`;
  const autoPayload = autoApproveStates.get(session);
  const autoElsewhere = autoApproveEnabledElsewhere(autoPayload);
  const autoText = autoEnabled ? t('yolo.on') : (autoElsewhere ? t('yolo.elsewhere') : '');
  const agentValue = agentKind
    ? `${agentName(agentKind)}${autoText ? ` · ${autoText}` : ''}`
    : (autoText || `<span data-locale-text-key="agent.notDetected">${esc(t('agent.notDetected'))}</span>`);
  const displayPath = panelFullPath(session, info) || pane?.current_path || t('common.notAvailable');
  const agentRows = sessionPopoverSortedAgentWindows(session, info, autoPayload);
  const windowMetadataItems = sessionPopoverWindowMetadataItems(session, info, agentRows);
  const agentWindowsHtml = sessionPopoverAgentWindowHtml(session, info, autoPayload, agentRows, windowMetadataItems);
  const perWindowMetadata = Boolean(windowMetadataItems.length);
  if (!perWindowMetadata) rows.push(popoverPairRow(t('common.stateLabel'), stateValue, t('common.agentLabel'), agentValue));
  const activityText = popoverActivityText(session, git);
  if (activityText) rows.push(popoverRow(yoagentTabLabel(), esc(activityText)));
  if (!perWindowMetadata) {
    rows.push(popoverRow(t('common.field.path'), displayPath));
    if (git?.branch) rows.push(popoverRow(t('common.field.branch'), sessionBranchValueHtml(session, info)));
  }
  let linearValue = '';
  let linearDesc = '';
  if (linear.length) {
    linearValue = linearInlineHtml(linear);
    linearDesc = linearDescriptionsInlineHtml(linear);
    if (linearValue) rows.push(popoverRow(t('info.field.linear'), linearValue));
    if (linearDesc) rows.push(popoverRow(t('common.details'), linearDesc));
  }
  if (pr?.number) {
    rows.push(popoverRow(t('common.pullRequestShort'), pullRequestPopoverRowHtml(session, pr)));
  }
  const subject = currentBranchSubject(git);
  if (subject && !pr?.number) rows.push(popoverRow(t('common.descriptionShort'), `<div class="popover-desc">${esc(subject)}</div>`));
  if (!perWindowMetadata && git?.root && git.root !== displayPath) rows.push(popoverRow(t('popover.repo'), git.root));
  // S7: name a linked worktree vs its parent repo so the focused path isn't mistaken for the main checkout.
  if (!perWindowMetadata && git?.worktree) rows.push(popoverRow(t('popover.worktree'), worktreePopoverValueHtml(git.worktree)));
  if (!perWindowMetadata && git?.head) rows.push(popoverRow('HEAD', gitHeadValueHtml(git)));
  if (!perWindowMetadata && gitStatusHasFacts(git)) rows.push(popoverRow(t('popover.git'), gitStatusText(git)));
  return `<div class="session-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(title)}</div>
        ${subtitleHtml}
      </div>
    </div>
    ${agentWindowsHtml}
    ${rows.join('')}
    ${otherBranchesHtml(session, info)}
  </div>`;
}

function popoverRow(label, valueHtml) {
  return `<div class="popover-row"><div class="popover-label">${esc(label)}</div><div class="popover-value">${stripTitleAttrs(valueHtml)}</div></div>`;
}

function popoverPairRow(leftLabel, leftValueHtml, rightLabel, rightValueHtml) {
  return `<div class="popover-row compact">
    <div class="popover-label">${esc(leftLabel)}</div><div class="popover-value">${stripTitleAttrs(leftValueHtml)}</div>
    <div class="popover-label">${esc(rightLabel)}</div><div class="popover-value">${stripTitleAttrs(rightValueHtml)}</div>
  </div>`;
}

function stripTitleAttrs(html) {
  return String(html || '').replace(/\s+title=(?:"[^"]*"|'[^']*'|[^\s>]+)/g, '');
}

function linearInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    const label = issue.identifier || '';
    if (!label) continue;
    const link = linkHtml(issue.url, label, issue.title || '');
    if (!link) continue;
    const state = issue.state ? `<span class="meta-muted"> ${esc(issue.state)}</span>` : '';
    parts.push(`${link}${state}`);
  }
  return metaJoin(parts);
}

function linearDescriptionsInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    if (!issue?.title) continue;
    const prefix = issue.identifier ? `${issue.identifier} ` : '';
    parts.push(`${prefix}${issue.title}`);
  }
  return parts.length ? esc(shortText(parts.join(' · '), 180)) : '';
}

function gitStatusText(git) {
  const parts = [];
  if (Number.isFinite(git.dirty_count)) parts.push(t('git.dirty', {count: git.dirty_count}));
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(t('git.ahead', {count: git.ahead}));
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(t('git.behind', {count: git.behind}));
  return esc(parts.length ? parts.join(' · ') : t('git.clean'));
}

function branchLinkHtml(git, branchName) {
  return esc(branchName || '');
}

function linearIssueHtml(issue) {
  const label = `${issue.identifier}${issue.state ? ` ${issue.state}` : ''}`;
  return linkHtml(issue.url, label, issue.title || '');
}

function linearIssueUrl(identifier) {
  const id = String(identifier || '').trim();
  if (!id || !linearIssueBaseUrl) return '';
  return `${linearIssueBaseUrl}/${encodeURIComponent(id)}`;
}

function linearIssueLinkHtml(identifier) {
  if (!identifier) return '';
  return linkHtml(linearIssueUrl(identifier), identifier, identifier);
}

function pullRequestLinkForBranch(git, branch) {
  const pr = branch?.pull_request;
  const repoUrl = git?.github_repo?.url;
  if (!pr?.number) return '';
  const url = pr.url || (repoUrl ? `${repoUrl}/pull/${pr.number}` : '');
  return linkHtml(url, pullRequestLinkLabel(pr), pr.title || pr.description || branch.subject || '', pullRequestStatusClass(pr));
}

function pullRequestNumberChipLinkHtml(session, pr) {
  if (!pr?.number) return '';
  const chip = pullRequestNumberIndicatorHtml(session, pr);
  if (!pr.url) return chip;
  const title = pr.title || pr.description || '';
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  return `<a href="${esc(pr.url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr} class="popover-chip-link">${chip}</a>`;
}

function pullRequestForBranch(git, branch, info) {
  const pr = branch?.current ? displayPullRequest(info) || branch.pull_request : branch?.pull_request;
  return pullRequestWithUrl(git, pr);
}

function branchListBranchHtml(session, git, branch) {
  const branchName = branch?.name || '';
  if (isDefaultBranch({branch: branchName})) {
    const classes = branch?.current
      ? metadataBadgeClasses(session, 'main', 'ci-indicator tab-symbol branch-indicator')
      : 'ci-indicator tab-symbol branch-indicator';
    return `<span class="${esc(classes)}">MAIN</span>`;
  }
  return branchLinkHtml(git, branchName);
}

function normalizeBranchSubjectText(value) {
  return subjectWithoutPullRequestNumber(value).replace(/\s+/g, ' ').trim().toLowerCase();
}

function branchListSubjectHtml(branch, pr) {
  const subject = subjectWithoutPullRequestNumber(branch?.subject || '');
  if (!subject) return '';
  if (branch?.current) {
    const currentTitles = [pr?.title, pr?.description].map(normalizeBranchSubjectText).filter(Boolean);
    if (currentTitles.includes(normalizeBranchSubjectText(subject))) return '';
  }
  return `<div class="branch-subject">${esc(shortText(subject, 240))}</div>`;
}

function branchPullRequestMetaHtml(session, pr) {
  if (!pr?.number) return '';
  const parts = [pullRequestNumberChipLinkHtml(session, pr)];
  const status = pullRequestInlineStatusDisplay(pr);
  if (status) parts.push(pullRequestStatusBadgeHtml('', status, pullRequestStatusClass(pr), {variant: 'meta'}));
  return metaJoin(parts);
}

function pullRequestTextForBranch(pr, fallback = '') {
  if (!pr?.number) return '';
  return [pullRequestLinkLabel(pr), pr.title || pr.description || fallback].filter(Boolean).join(' ');
}

function branchUpdatedText(branch) {
  const ts = Number(branch?.updated_ts || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    return relativeTimeFormat(seconds);
  }
  return branch?.updated || '';
}

function otherBranchesHtml(session, info) {
  const git = info?.project?.git;
  const inventory = git?.other_branches || {};
  const branches = inventory.branches || [];
  if (!branches.length) {
    return `<div class="branch-list"><div class="branch-list-title">${esc(t('branch.all'))}</div><div class="meta-muted">${esc(t('branch.none'))}</div></div>`;
  }
  const items = branches.map(branch => {
    const pr = pullRequestForBranch(git, branch, info);
    const branchLink = branchListBranchHtml(session, git, branch);
    const prLink = branchPullRequestMetaHtml(session, pr);
    const linearLinks = (branch.linear_ids || []).map(linearIssueLinkHtml).filter(Boolean).join(' ');
    const meta = [prLink, linearLinks, esc(branchUpdatedText(branch))].filter(Boolean).join(' ');
    return `<div class="branch-item">
      <div class="branch-name">${branchLink}</div>
      <div class="branch-meta">${meta}</div>
      ${branchListSubjectHtml(branch, pr)}
    </div>`;
  }).join('');
  const hidden = Number(inventory.hidden_count || 0) > 0
    ? `<div class="meta-muted">${esc(t('branch.more', {count: inventory.hidden_count}))}</div>`
    : '';
  return `<div class="branch-list"><div class="branch-list-title">${esc(t('branch.all'))}</div>${items}${hidden}</div>`;
}

function dragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-session') || '';
  if (!raw && dragState.paneSlot) return null;
  if (!raw && dragState.item) return {session: dragState.item, sourceSlot: dragState.sourceSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isLayoutItem(parsed.session) ? parsed : null;
  } catch (_) {
    return isLayoutItem(raw) ? {session: raw, sourceSlot: null} : null;
  }
}

function paneDragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-pane') || '';
  if (!raw && dragState.paneSlot) return {slot: dragState.paneSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return layoutSlotKeys().includes(parsed.slot) ? parsed : null;
  } catch (_) {
    return layoutSlotKeys().includes(raw) ? {slot: raw} : null;
  }
}

function normalizeFileDragPayload(parsed) {
  if (!parsed?.path && !Array.isArray(parsed?.paths)) return null;
  const paths = Array.isArray(parsed.paths) ? parsed.paths.filter(Boolean) : [parsed.path].filter(Boolean);
  return paths.length ? {...parsed, path: parsed.path || paths[0], paths} : null;
}

function parseFileDragPayload(raw) {
  if (!raw) return null;
  try {
    return normalizeFileDragPayload(JSON.parse(raw));
  } catch (_) {
    return null;
  }
}

function hasYolomuxFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes('application/x-yolomux-file');
}

function fileDragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-file') || '';
  return parseFileDragPayload(raw) || (hasYolomuxFileDrag(event) ? dragState.filePayload : null);
}

async function openDraggedFilesInEditor(payload, options = {}) {
  const paths = Array.from(new Set((payload?.paths || [payload?.path]).filter(Boolean)));
  if (!paths.length) return;
  let opened = 0;
  for (const [index, path] of paths.entries()) {
    try {
      const info = await fetchFilePathInfo(path);
      if (info.kind !== 'file') continue;
      await openFileInEditor(path, info, {
        forceNewTab: true,
        targetSlot: options.targetSlot || null,
        targetZone: options.targetZone || 'middle',
        targetIndex: options.targetIndex == null ? null : Number(options.targetIndex) + index,
        rehomeExisting: true,
      });
      // Unify with double-click: a dragged CHANGED (tracked, has-diff) file opens in the SAME diff
      // view (ensureCodeMirrorDiffPanel) as openChangedFileInDiff, not a plain edit-mode editor.
      // Files with no diff payload stay in edit.
      await refreshOpenFileDiff(path, {silent: true});
      const draggedState = openFiles.get(path);
      if (draggedState && openFileDiffAvailable(draggedState)) {
        setFileEditorViewMode(path, 'diff', fileEditorItemFor(path));
        for (const draggedPanel of fileEditorPanelsForPath(path)) {
          renderFileEditorPanel(draggedPanel, draggedPanel.dataset.layoutItem || fileEditorItemFor(path));
        }
      }
      // #260: a drag-drop open is a FRESH open at the current disk state, not a "changed on disk"
      // conflict. If the just-opened file is NOT dirty, clear any external-change flags so it never
      // pops a spurious reload prompt (this matches double-click's openChangedFileInDiff, which opens
      // with a clean baseline). A dirty file keeps its conflict state so real unsaved-edit warnings stay.
      if (draggedState && !draggedState.dirty) {
        delete draggedState.externalChanged;
        clearOpenFileMissingState(draggedState);
        delete draggedState.externalError;
        delete draggedState.externalChangeEditPrompted;
        renderOpenFilePath(path);
      }
      opened += 1;
    } catch (error) {
      showFileOpenError(path, error);
    }
  }
  if (opened) statusOk(esc(tPlural('status.openedFiles', opened, {name: basenameOf(paths[0])})));
}

function terminalCurrentPath(session) {
  const signalPath = tmuxSignalPanePathForSession(session);
  if (signalPath) return signalPath;
  const info = transcriptMetadataState.payload.sessions?.[session];
  return terminalDisplayPane(info, session)?.current_path || info?.selected_pane?.current_path || '';
}

function pathRelativeToDirectory(path, directory) {
  const fullPath = String(path || '');
  const rawBase = String(directory || '');
  const base = rawBase === '/' ? '/' : rawBase.replace(/\/+$/, '');
  if (!fullPath || !base || !fullPath.startsWith('/')) return fullPath;
  if (fullPath === base) return '.';
  if (base === '/') return fullPath.slice(1);
  if (!fullPath.startsWith(`${base}/`)) return fullPath;
  return fullPath.slice(base.length + 1);
}

function terminalFileReference(session, path) {
  return pathRelativeToDirectory(path, terminalCurrentPath(session));
}

function terminalFileReferences(session, payload) {
  const paths = Array.isArray(payload?.paths) ? payload.paths : [payload?.path].filter(Boolean);
  return paths.map(path => terminalFileReference(session, path));
}

function transparentNativeDragImage() {
  if (dragState.transparentImage) return dragState.transparentImage;
  const node = document.createElement('div');
  node.className = 'transparent-drag-image';
  document.body.appendChild(node);
  dragState.transparentImage = node;
  return node;
}

function clearNativeDragImagePreview() {
  dragState.nativePreview?.remove?.();
  dragState.nativePreview = null;
}

function beginFileDrag(payloadObject) {
  dragState.item = null;
  dragState.sourceSlot = null;
  dragState.paneSlot = null;
  dragState.tabRectCache = null;
  clearNativeDragImagePreview();
  dragState.filePayload = normalizeFileDragPayload(payloadObject);
  return dragState.filePayload;
}

function moveCustomDragPreview(event) {
  if (!dragState.customPreview || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
  dragState.customPreview.style.left = `${Math.round(event.clientX - dragState.customPreviewOffset.x)}px`;
  dragState.customPreview.style.top = `${Math.round(event.clientY - dragState.customPreviewOffset.y)}px`;
}

const customDragPreviewCleanupEvents = ['drop', 'dragend', 'pointerup', 'mouseup', 'blur', 'visibilitychange'];

function customDragPreviewEventTargets() {
  return [document, window].filter(Boolean);
}

function bindCustomDragPreviewListeners() {
  for (const target of customDragPreviewEventTargets()) {
    target.addEventListener?.('dragover', moveCustomDragPreview, true);
    target.addEventListener?.('drag', moveCustomDragPreview, true);
    for (const eventName of customDragPreviewCleanupEvents) {
      target.addEventListener?.(eventName, cancelDragOperationState, true);
    }
  }
}

function unbindCustomDragPreviewListeners() {
  for (const target of customDragPreviewEventTargets()) {
    target.removeEventListener?.('dragover', moveCustomDragPreview, true);
    target.removeEventListener?.('drag', moveCustomDragPreview, true);
    for (const eventName of customDragPreviewCleanupEvents) {
      target.removeEventListener?.(eventName, cancelDragOperationState, true);
    }
  }
}

function stopCustomDragPreview() {
  unbindCustomDragPreviewListeners();
  dragState.customPreview?.remove();
  dragState.customPreview = null;
  closeFileImagePreview();
}

function cancelDragOperationState() {
  dragState.item = null;
  dragState.sourceSlot = null;
  dragState.paneSlot = null;
  dragState.filePayload = null;
  dragState.tabRectCache = null;
  clearNativeDragImagePreview();
  stopCustomDragPreview();
  clearDropPreview();
}

function paneDragPreviewMetrics(slot, event) {
  const rect = layoutSlotScreenRect(slot);
  const fallbackWidth = 360;
  const fallbackHeight = 220;
  const sourceWidth = Math.max(1, Number(rect?.width) || fallbackWidth);
  const sourceHeight = Math.max(1, Number(rect?.height) || fallbackHeight);
  const viewport = appViewport();
  const viewportWidth = effectiveViewportWidth(viewport);
  const viewportHeight = Math.max(240, Number(viewport.height) || 800);
  const maxWidth = Math.min(720, Math.max(220, viewportWidth * 0.64));
  const maxHeight = Math.min(420, Math.max(160, viewportHeight * 0.58));
  const scale = Math.min(1, maxWidth / sourceWidth, maxHeight / sourceHeight);
  const width = Math.max(180, Math.round(sourceWidth * scale));
  const height = Math.max(120, Math.round(sourceHeight * scale));
  const sourceOffsetX = rect ? (Number(event?.clientX) || rect.left) - rect.left : width * 0.12;
  const sourceOffsetY = rect ? (Number(event?.clientY) || rect.top) - rect.top : 18;
  return {
    width,
    height,
    offsetX: Math.max(16, Math.min(width - 16, Math.round(sourceOffsetX * scale))),
    offsetY: Math.max(16, Math.min(height - 16, Math.round(sourceOffsetY * scale))),
  };
}

function paneDragPreviewHtml(slot) {
  const tabs = paneTabs(slot);
  const active = activeItemForSide(slot) || tabs[0] || slot;
  const title = itemLabel(active);
  const count = tabs.length;
  const extra = tabs
    .filter(item => item !== active)
    .slice(0, 3)
    .map(item => `<span>${esc(itemLabel(item))}</span>`)
    .join('');
  return `
    <div class="pane-drag-image-frame">
      <div class="pane-drag-image-title">${esc(title)}</div>
      <div class="pane-drag-image-meta">${esc(tPlural('common.tabs', count))}</div>
      ${extra ? `<div class="pane-drag-image-tabs">${extra}</div>` : ''}
    </div>`;
}

function startPaneDragPreview(event, slot, options = {}) {
  stopCustomDragPreview();
  const metrics = paneDragPreviewMetrics(slot, event);
  const preview = document.createElement('div');
  preview.className = 'pane-drag-image drag-image';
  preview.dataset.dragSlot = slot;
  preview.innerHTML = paneDragPreviewHtml(slot);
  preview.style.position = 'fixed';
  preview.style.pointerEvents = 'none';
  preview.style.zIndex = '99999';
  preview.style.width = `${metrics.width}px`;
  preview.style.height = `${metrics.height}px`;
  document.body.appendChild(preview);
  dragState.customPreview = preview;
  dragState.customPreviewOffset = {x: metrics.offsetX, y: metrics.offsetY};
  moveCustomDragPreview(event);
  if (options.nativeDrag === true) {
    bindCustomDragPreviewListeners();
    preview.getBoundingClientRect();
    event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
  }
}

// #47: tab drags use the native drag image (see startSessionDrag) — the clone-follow tab preview is
// gone. The custom-preview machinery below is retained only for the rich FILE drag preview.
function startFileDragPreview(event, paths, entry) {
  stopCustomDragPreview();
  const normalizedPaths = Array.from(new Set((paths || []).filter(Boolean)));
  const firstPath = normalizedPaths[0] || '';
  const preview = document.createElement('div');
  preview.className = 'file-drag-image drag-image';
  const title = normalizedPaths.length === 1 ? basenameOf(firstPath) : t('common.items', {count: normalizedPaths.length});
  const pathRows = normalizedPaths.slice(0, 4)
    .map(path => `<div class="file-drag-path">${esc(path)}</div>`)
    .join('');
  const more = normalizedPaths.length > 4 ? `<div class="file-drag-more">${esc(t('common.more', {count: normalizedPaths.length - 4}))}</div>` : '';
  preview.innerHTML = `
    <div class="file-drag-main">
      ${fileDragPreviewMedia(firstPath, entry)}
      <div class="file-drag-copy">
        <div class="file-drag-title">${esc(title)}</div>
        ${pathRows}${more}
      </div>
    </div>`;
  preview.style.position = 'fixed';
  preview.style.pointerEvents = 'none';
  preview.style.zIndex = '99999';
  document.body.appendChild(preview);
  dragState.customPreview = preview;
  dragState.customPreviewOffset = {x: -14, y: -14};
  moveCustomDragPreview(event);
  bindCustomDragPreviewListeners();
  preview.getBoundingClientRect();
  event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
}

function fileDragPreviewMedia(path, entry) {
  const kind = entry?.kind || 'file';
  if (kind === 'file' && previewMediaKindForPath(path) === 'image') {
    return `<img class="file-drag-thumb" src="${rawFileUrl(path)}" alt="">`;
  }
  const icon = kind === 'dir' ? disclosureTriangleCollapsedGlyph : '📄';
  return `<span class="file-drag-thumb file-drag-icon" aria-hidden="true">${icon}</span>`;
}

// S14: OPT-IN tab-drag timing to diagnose the ~500ms first-drag delay without guessing. Off by
// default (no permanent user-visible perf log). Enable by setting storage key 'yolomux.debugDragTiming' to
// '1' (via storageSet in the console), drag a tab, then read the per-bucket console.table at drop. Marks
// the buckets the DOIT calls out: pointerdown -> dragstart/startSessionDrag (begin/end) -> first dragover
// -> first dragMeasureStrip / paneTabDropPlacement. dragTimingMarkOnce dedups the repeating measure calls.
let dragTimingMarks = null;
const dragTimingSeen = new Set();
function dragTimingEnabled() {
  return storageGet('yolomux.debugDragTiming') === '1';
}
function dragTimingReset() {
  dragTimingSeen.clear();
  dragTimingMarks = dragTimingEnabled() ? [] : null;
}
function dragTimingMark(label) {
  if (dragTimingMarks) dragTimingMarks.push({label, t: performance.now()});
}
function dragTimingMarkOnce(label) {
  if (dragTimingMarks && !dragTimingSeen.has(label)) { dragTimingSeen.add(label); dragTimingMark(label); }
}
function dragTimingReport() {
  if (dragTimingMarks && dragTimingMarks.length >= 2) {
    const first = dragTimingMarks[0].t;
    const rows = dragTimingMarks.map((mark, i) => ({
      mark: mark.label,
      deltaMs: i ? Number((mark.t - dragTimingMarks[i - 1].t).toFixed(1)) : 0,
      sinceStartMs: Number((mark.t - first).toFixed(1)),
    }));
    console.table(rows);
    showDragTimingOverlay(rows);   // copyable on-page readout — no DevTools needed
  }
  dragTimingMarks = null;
  dragTimingSeen.clear();
}

// S14: render the last drag's timing into a fixed, click-to-select-all box so it can be
// copy-pasted (or screenshotted) back without opening DevTools. Created lazily; only the flag-gated
// dragTimingReport calls it, so it never appears in normal use.
function showDragTimingOverlay(rows) {
  let el = document.getElementById('drag-timing-overlay');
  if (!el) {
    el = document.createElement('pre');
    el.id = 'drag-timing-overlay';
    el.className = 'drag-timing-overlay';
    el.title = t('debug.dragTimingTitle');
    el.addEventListener('click', () => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    });
    document.body.appendChild(el);
  }
  const width = Math.max(...rows.map(row => row.mark.length));
  el.textContent = ['drag timing (ms) — click to select, copy, paste back:',
    ...rows.map(row => `${row.mark.padEnd(width)}  +${String(row.deltaMs).padStart(7)}  total ${String(row.sinceStartMs).padStart(7)}`)].join('\n');
}

function startSessionDrag(event, session, sourceSlot = null, options = {}) {
  dragTimingMark('startSessionDrag:begin');
  clearNativeDragImagePreview();
  dragState.item = session;
  dragState.sourceSlot = sourceSlot;
  dragState.paneSlot = null;
  dragState.filePayload = null;
  const payload = JSON.stringify({session, sourceSlot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-session', payload);
  event.dataTransfer.setData('text/plain', session);
  // #47: use the NATIVE drag image — a one-time compositor snapshot of the tab itself — instead of the
  // JS clone-follow preview. That removes the per-move reposition, the two document capture listeners,
  // and the animated heavyweight clone that caused the "won't budge" first drag and the per-move jank.
  // C12 F2: take the grab offset from event.offsetX/offsetY (already on the event) instead of
  // getBoundingClientRect(), which forced a synchronous layout reflow inside the handler — coldest on the
  // first drag after load — before the browser could start the drag.
  const source = event.currentTarget;
  const dragImageSource = typeof options.dragImage === 'function'
    ? (options.dragImage(event, source) || source)
    : (options.dragImage || source);
  if (dragImageSource && event.dataTransfer?.setDragImage) {
    const offsetX = Math.max(0, Number(event.offsetX) || 0);
    const offsetY = Math.max(0, Number(event.offsetY) || 0);
    event.dataTransfer.setDragImage(dragImageSource, offsetX, offsetY);
  }
  resetDragTabRectCache();
  dragTimingMark('startSessionDrag:end');
}

function startPaneDrag(event, sourceSlot) {
  const slot = layoutSlotKeys().includes(sourceSlot) ? sourceSlot : null;
  if (!slot || slotIsFileExplorerPane(slot)) {
    event.preventDefault?.();
    return;
  }
  const active = activeItemForSide(slot);
  if (!active) {
    event.preventDefault?.();
    return;
  }
  dragTimingMark('startPaneDrag');
  dragState.item = active;
  dragState.sourceSlot = slot;
  dragState.paneSlot = slot;
  dragState.filePayload = null;
  const payload = JSON.stringify({slot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-pane', payload);
  event.dataTransfer.setData('text/plain', paneTabs(slot).map(itemLabel).join('\n'));
  startPaneDragPreview(event, slot, {nativeDrag: true});
}

function endSessionDrag(event) {
  cancelDragOperationState();
  sessionButtons.classList.remove(CLS.dragOver);
  // flush any tab/preferences re-renders that were deferred during the drag.
  if (pendingTabStripRender) { pendingTabStripRender = false; renderPaneTabStrips(); }
  if (pendingPreferencesRender) { pendingPreferencesRender = false; renderPreferencesPanels(); }
  // flush through the shared layout render scheduler so same-shape drops keep the cheap path.
  flushPendingLayoutRender();
  dragTimingReport();
}

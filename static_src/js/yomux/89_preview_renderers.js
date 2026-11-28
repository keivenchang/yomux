// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// File preview renderers and zoom controls split from 92_codemirror_editor.js.

const previewZoomPolicy = Object.freeze({
  minScale: 0.2,
  maxScale: 32,
  step: 1.25,
  disabledEpsilon: 0.001,
  actualPressedEpsilon: 0.01,
  fitPaddingPx: 24,
  fitScaleCaps: Object.freeze({
    image: 1,
    mermaidInline: 3,
    mermaidFull: Number.POSITIVE_INFINITY,
  }),
  panThresholdPx: 2,
});

const previewZoomShellClasses = Object.freeze([
  'file-editor-preview-zoom-shell',
  'file-editor-preview-zoom-full',
  'file-editor-preview-zoom-inline',
]);

const previewZoomRendererDefaults = Object.freeze({
  imagePane: Object.freeze({zoomKey: 'image-pane', fitMaxScale: previewZoomPolicy.fitScaleCaps.image, full: true, panDrag: true}),
  imagePreview: Object.freeze({zoomKey: 'image-preview', fitMaxScale: previewZoomPolicy.fitScaleCaps.image, full: true, panDrag: true}),
  mermaidFull: Object.freeze({zoomKey: 'mermaid', fitMaxScale: previewZoomPolicy.fitScaleCaps.mermaidFull, full: true, panDrag: true}),
  mermaidInline: Object.freeze({zoomKey: 'mermaid', fitMaxScale: previewZoomPolicy.fitScaleCaps.mermaidInline, full: false, panDrag: true}),
  default: Object.freeze({zoomKey: 'default', fitMaxScale: Number.POSITIVE_INFINITY, full: true}),
});

const previewZoomActions = Object.freeze([
  Object.freeze({
    id: 'out',
    label: '-',
    titleKey: 'preview.zoom.out',
    zoomState: current => ({mode: 'manual', scale: current / previewZoomPolicy.step}),
    disabled: scale => scale <= previewZoomPolicy.minScale + previewZoomPolicy.disabledEpsilon,
  }),
  Object.freeze({
    id: 'fit',
    labelKey: 'preview.zoom.fit.label',
    titleKey: 'preview.zoom.fit.title',
    zoomState: current => ({mode: 'fit', scale: current}),
    pressed: state => state.mode === 'fit',
  }),
  Object.freeze({
    id: 'actual',
    label: '1:1',
    titleKey: 'preview.zoom.actual',
    zoomState: () => ({mode: 'actual', scale: 1}),
    pressed: (state, scale) => state.mode !== 'fit' && Math.abs(scale - 1) < previewZoomPolicy.actualPressedEpsilon,
  }),
  Object.freeze({
    id: 'in',
    label: '+',
    titleKey: 'preview.zoom.in',
    zoomState: current => ({mode: 'manual', scale: current * previewZoomPolicy.step}),
    disabled: scale => scale >= previewZoomPolicy.maxScale - previewZoomPolicy.disabledEpsilon,
  }),
]);

const previewZoomActionById = new Map(previewZoomActions.map(action => [action.id, action]));

function previewContextId(value) {
  return String(value || '').trim();
}

function previewZoomScopedKey(key, context) {
  const normalized = normalizedPreviewZoomKey(key);
  const scope = previewContextId(context);
  return scope ? `${scope}:${normalized}` : normalized;
}

function previewZoomOptionsForKind(kind, options = {}) {
  const defaults = previewZoomRendererDefaults[kind] || previewZoomRendererDefaults.default;
  const baseZoomKey = Object.prototype.hasOwnProperty.call(options, 'zoomKey') ? options.zoomKey : defaults.zoomKey;
  const context = previewContextId(options.context || defaults.context || '');
  const result = {...defaults, ...options};
  result.context = context;
  result.zoomKeyBase = normalizedPreviewZoomKey(baseZoomKey);
  result.zoomKey = previewZoomScopedKey(baseZoomKey, context);
  if (!Object.prototype.hasOwnProperty.call(options, 'fitMaxScale')) result.fitMaxScale = defaults.fitMaxScale;
  if (!Object.prototype.hasOwnProperty.call(options, 'full')) result.full = defaults.full;
  if (!Object.prototype.hasOwnProperty.call(options, 'panDrag')) result.panDrag = defaults.panDrag === true;
  return result;
}

function previewZoomStateForAction(actionId, currentScale) {
  return previewZoomActionById.get(actionId)?.zoomState?.(currentScale) || null;
}

function previewZoomStateForRender(options = {}) {
  const state = previewZoomReadState(options);
  return state.mode === 'fit' ? {mode: 'fit', scale: 1} : state;
}

function clampPreviewZoomScale(scale) {
  const value = Number.parseFloat(String(scale || ''));
  if (!Number.isFinite(value)) return 1;
  return Math.max(previewZoomPolicy.minScale, Math.min(previewZoomPolicy.maxScale, value));
}

function clampPreviewFitScale(scale) {
  const value = Number.parseFloat(String(scale || ''));
  if (!Number.isFinite(value)) return 1;
  return Math.max(previewZoomPolicy.minScale, value);
}

function resetPreviewZoomSurfaceClasses(shell) {
  if (!shell?.classList) return;
  shell.classList.remove(...previewZoomShellClasses);
  if (shell.dataset) {
    delete shell.dataset.previewZoomScale;
    delete shell.dataset.previewZoomMode;
  }
}

function disconnectPreviewZoomSurface(shell, options = {}) {
  shell?._previewZoomLifecycleScope?.dispose('preview-zoom-disconnect');
  if (shell) {
    shell._previewZoomApplyGeneration = Number(shell._previewZoomApplyGeneration || 0) + 1;
    shell._previewZoomLifecycleScope = null;
  }
  shell?.classList?.remove?.('file-editor-preview-zoom-measuring');
  if (options.resetClasses === true) resetPreviewZoomSurfaceClasses(shell);
}

function previewZoomOwnerWindow(shell) {
  return shell?.ownerDocument?.defaultView || window;
}

function schedulePreviewZoomFrame(shell, callback) {
  const ownerWindow = previewZoomOwnerWindow(shell);
  if (typeof ownerWindow?.requestAnimationFrame === 'function') return ownerWindow.requestAnimationFrame(callback);
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(callback);
  return setTimeout(callback, 0);
}

function writePreviewZoomSurfaceDataset(shell, options = {}) {
  if (!shell?.dataset) return;
  shell.dataset.previewZoomPath = options.path || '';
  shell.dataset.previewZoomKey = options.zoomKey || 'default';
  shell.dataset.previewZoomFull = options.full === false ? '0' : '1';
  shell.dataset.previewZoomPan = options.panDrag === true ? '1' : '0';
  shell.dataset.previewZoomWheelParent = options.wheelScrollParent === true ? '1' : '0';
  if (Number.isFinite(options.fitMaxScale)) shell.dataset.previewZoomFitMaxScale = String(options.fitMaxScale);
  else delete shell.dataset.previewZoomFitMaxScale;
}

function previewZoomOptionsFromSurface(shell) {
  const fitMaxScale = Number.parseFloat(shell?.dataset?.previewZoomFitMaxScale || '');
  return {
    path: shell?.dataset?.previewZoomPath || '',
    zoomKey: shell?.dataset?.previewZoomKey || 'default',
    full: shell?.dataset?.previewZoomFull !== '0',
    panDrag: shell?.dataset?.previewZoomPan === '1',
    wheelScrollParent: shell?.dataset?.previewZoomWheelParent === '1',
    fitMaxScale: Number.isFinite(fitMaxScale) ? fitMaxScale : Number.POSITIVE_INFINITY,
  };
}

function previewZoomSurfaceContent(shell) {
  return shell?.querySelector?.(':scope > .file-editor-preview-zoom-viewport > .file-editor-preview-zoom-stage > .file-editor-preview-zoom-content')
    || shell?.querySelector?.(':scope > .file-editor-preview-zoom-viewport > .file-editor-preview-zoom-stage > *')
    || null;
}

function previewZoomContentSize(content) {
  const naturalWidth = Number(content?.naturalWidth || 0);
  const naturalHeight = Number(content?.naturalHeight || 0);
  if (naturalWidth > 0 && naturalHeight > 0) return {width: naturalWidth, height: naturalHeight};
  const rect = content?.getBoundingClientRect?.();
  return {
    width: Math.max(1, Math.round(rect?.width || 1)),
    height: Math.max(1, Math.round(rect?.height || 1)),
  };
}

function previewZoomPadding(element) {
  const ownerWindow = previewZoomOwnerWindow(element);
  const style = ownerWindow?.getComputedStyle?.(element) || (typeof getComputedStyle === 'function' ? getComputedStyle(element) : null);
  const px = name => Number.parseFloat(style?.getPropertyValue?.(name) || '') || 0;
  return {
    x: px('padding-left') + px('padding-right'),
    y: px('padding-top') + px('padding-bottom'),
  };
}

function previewZoomFitScale(size, width, height, padding, options) {
  const availableWidth = Math.max(1, width - padding.x);
  const availableHeight = Math.max(1, height - padding.y);
  const fitScale = Math.min(availableWidth / size.width, availableHeight / size.height);
  const maxFitScale = Number.isFinite(options.fitMaxScale) ? options.fitMaxScale : Number.POSITIVE_INFINITY;
  const scale = Math.min(maxFitScale, fitScale);
  return options.full === false ? scale : clampPreviewFitScale(scale);
}

function previewZoomInlinePane(shell) {
  return shell.parentElement?.closest('.file-editor-preview-pane, .file-editor-preview-pane-panel');
}

function updatePreviewZoomInlineHeight(shell, toolbar) {
  const pane = previewZoomInlinePane(shell);
  if (!pane?.clientHeight) return;
  const style = previewZoomOwnerWindow(shell).getComputedStyle(shell);
  const margins = (Number.parseFloat(style.marginTop) || 0) + (Number.parseFloat(style.marginBottom) || 0);
  // The viewport is still laid out for the previous zoom mode during this calculation.
  const chrome = previewZoomPadding(shell).y + 2 * shell.clientTop +
    (toolbar?.offsetHeight || 0) + (Number.parseFloat(style.rowGap) || 0);
  const height = Math.max(1, pane.clientHeight - previewZoomPadding(pane).y - margins - chrome);
  shell.style.setProperty('--preview-zoom-fit-height', `${height}px`);
}

function previewZoomButton(action) {
  const title = action.titleKey ? t(action.titleKey) : action.title;
  return makeButton({
    dataset: {previewZoomAction: action.id},
    label: action.labelKey ? t(action.labelKey) : action.label,
    title,
    ariaLabel: title,
  });
}

function previewZoomReadState(options = {}) {
  if (options.path) return fileEditorPreviewZoomStateForPath(options.path, options.zoomKey || 'default');
  return normalizePreviewZoomState(options.shell?._previewZoomState);
}

function previewZoomWriteState(shell, options = {}, zoomState) {
  const normalized = normalizePreviewZoomState(zoomState);
  if (options.path) setFileEditorPreviewZoomStateForPath(options.path, options.zoomKey || 'default', normalized);
  shell._previewZoomState = normalized;
  return normalized;
}

function applyPreviewZoomSurface(shell, content, options = {}, applyOptions = {}) {
  const viewport = shell.querySelector(':scope > .file-editor-preview-zoom-viewport');
  const value = shell.querySelector(':scope > .file-editor-preview-zoom-toolbar .file-editor-preview-zoom-value');
  if (!viewport || !content) return false;
  const zoomGeneration = Number(shell._previewZoomApplyGeneration || 0) + 1;
  shell._previewZoomApplyGeneration = zoomGeneration;
  const deferredOwner = applyOptions.deferredScrollOwner || (
    applyOptions.userInitiated === true
      ? createDeferredElementScrollOwner(viewport)
      : createPassiveDeferredElementScrollOwner(viewport)
  );
  // Fit changes scroll geometry before its positioning frame. A claimed native gesture owns both:
  // letting a passive refit shrink scrollHeight here would make WebKit clamp scrollTop even though
  // the later guarded write correctly yielded.
  if (!deferredElementScrollOwnerOwnsElement(deferredOwner, viewport)) return false;
  const previousScale = Number.parseFloat(shell.dataset.previewZoomScale || '1') || 1;
  const viewportRect = viewport.getBoundingClientRect?.();
  const focusOffsetX = Number.isFinite(applyOptions.focusClientX) && viewportRect
    ? Math.max(0, Math.min(viewport.clientWidth, applyOptions.focusClientX - viewportRect.left))
    : (viewport.clientWidth / 2);
  const focusOffsetY = Number.isFinite(applyOptions.focusClientY) && viewportRect
    ? Math.max(0, Math.min(viewport.clientHeight, applyOptions.focusClientY - viewportRect.top))
    : (viewport.clientHeight / 2);
  const hasFocusPoint = Number.isFinite(applyOptions.focusClientX) || Number.isFinite(applyOptions.focusClientY);
  const focusX = (viewport.scrollLeft + focusOffsetX) / previousScale;
  const focusY = (viewport.scrollTop + focusOffsetY) / previousScale;
  const state = previewZoomStateForRender({...options, shell});
  const size = previewZoomContentSize(content);
  const stage = content.closest?.('.file-editor-preview-zoom-stage') || null;
  const padding = stage ? previewZoomPadding(stage) : {x: 0, y: 0};
  const parent = shell.parentElement;
  const inlineFit = options.full === false && state.mode === 'fit' && parent?.clientWidth > 0;
  const fitPadding = inlineFit ? padding : {x: previewZoomPolicy.fitPaddingPx, y: previewZoomPolicy.fitPaddingPx};
  let fitWidth = viewport.clientWidth || 1;
  let fitHeight = viewport.clientHeight || 1;
  let shellChrome = 0, viewportBorder = 0;
  if (inlineFit) {
    updatePreviewZoomInlineHeight(shell, value?.parentElement);
    // Measure the surrounding layout so a compact diagram can grow again when the pane grows.
    shellChrome = previewZoomPadding(shell).x + 2 * shell.clientLeft;
    viewportBorder = 2 * viewport.clientTop;
    fitWidth = parent.clientWidth - previewZoomPadding(parent).x - shellChrome - viewportBorder;
    // The fit max-height rule applies only once the shell's mode attribute reads fit, which a
    // switch from Actual has not written yet. Read the fit height itself, as that rule will.
    const fitMaxHeight = Number.parseFloat(shell.style.getPropertyValue('--preview-zoom-fit-height'));
    fitHeight = (Number.isFinite(fitMaxHeight) ? fitMaxHeight
      : Number.parseFloat(previewZoomOwnerWindow(viewport).getComputedStyle(viewport).maxHeight)) - viewportBorder;
  }
  const scale = state.mode === 'fit'
    ? previewZoomFitScale(size, fitWidth, fitHeight, fitPadding, options)
    : clampPreviewZoomScale(state.scale);
  const scaledWidth = Math.max(1, Math.round(size.width * scale));
  const scaledHeight = Math.max(1, Math.round(size.height * scale));
  content.style.width = `${scaledWidth}px`;
  content.style.height = `${scaledHeight}px`;
  content.classList.add('file-editor-preview-zoom-content');
  if (stage) {
    stage.style.width = `${Math.max(inlineFit ? 0 : viewport.clientWidth, scaledWidth + padding.x)}px`;
    stage.style.height = `${Math.max(inlineFit ? 0 : viewport.clientHeight, scaledHeight + padding.y)}px`;
  }
  if (inlineFit) {
    const toolbarWidth = value?.parentElement.offsetWidth || 0;
    const boxWidth = Math.max(toolbarWidth, scaledWidth + padding.x + viewportBorder);
    shell.style.setProperty('--preview-zoom-fit-width', `${boxWidth + shellChrome}px`);
  }
  shell.dataset.previewZoomScale = String(scale);
  shell.dataset.previewZoomMode = state.mode;
  if (value) value.textContent = `${Math.round(scale * 100)}%`;
  shell.querySelectorAll('[data-preview-zoom-action]').forEach(button => {
    const action = previewZoomActionById.get(button.dataset.previewZoomAction);
    button.disabled = Boolean(action?.disabled?.(scale));
    if (action?.pressed) button.setAttribute('aria-pressed', action.pressed(state, scale) ? 'true' : 'false');
    else button.removeAttribute('aria-pressed');
  });
  const writeIfOwned = (coordinates, details) => writeDeferredElementScrollIfOwned(
    deferredOwner,
    viewport,
    coordinates,
    'preview-zoom-apply',
    {previewSurface: options.zoomKey || 'zoom', zoomGeneration, ...details},
  );
  schedulePreviewZoomFrame(shell, () => {
    if (shell._previewZoomApplyGeneration !== zoomGeneration) return;
    if (state.mode === 'fit') {
      writeIfOwned({left: 0, top: 0}, {zoomMode: state.mode});
      return;
    }
    if (applyOptions.centerIfUnfocused === true && !hasFocusPoint) {
      writeIfOwned({
        left: Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2),
        top: Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2),
      }, {zoomMode: 'center'});
      return;
    }
    writeIfOwned({
      left: Math.max(0, (focusX * scale) - focusOffsetX),
      top: Math.max(0, (focusY * scale) - focusOffsetY),
    }, {zoomMode: state.mode});
  });
  return true;
}

function setPreviewZoomSurfaceState(shell, content, options = {}, zoomState = {}, applyOptions = {}) {
  previewZoomWriteState(shell, options, zoomState);
  applyPreviewZoomSurface(shell, content, options, applyOptions);
}

function bindPreviewZoomDragPan(shell, viewport, bind, options) {
  let drag = null;
  const ownerDocument = viewport.ownerDocument || shell.ownerDocument;
  const finish = event => {
    if (!drag || (event.pointerId !== undefined && event.pointerId !== drag.pointerId)) return;
    try { viewport.releasePointerCapture?.(drag.pointerId); } catch (_) {}
    shell.classList.remove('file-editor-preview-zoom-panning');
    drag = null;
  };
  bind(viewport, 'pointerdown', event => {
    if (event.button !== 0 || event.defaultPrevented) return;
    event.preventDefault();
    drag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
    };
    shell.classList.add('file-editor-preview-zoom-panning');
    try { viewport.setPointerCapture?.(event.pointerId); } catch (_) {}
  }, {capture: true, passive: false});
  bind(ownerDocument, 'pointermove', event => {
    if (!drag || (event.pointerId !== undefined && event.pointerId !== drag.pointerId)) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (Math.abs(dx) > previewZoomPolicy.panThresholdPx || Math.abs(dy) > previewZoomPolicy.panThresholdPx) event.preventDefault();
    writeOwnedElementScroll(viewport, {
      left: Math.max(0, drag.scrollLeft - dx),
      top: Math.max(0, drag.scrollTop - dy),
    }, 'preview-zoom-drag-pan', {previewSurface: options.zoomKey || 'zoom'});
  }, {capture: true, passive: false});
  bind(ownerDocument, 'pointerup', finish, {capture: true});
  bind(ownerDocument, 'pointercancel', finish, {capture: true});
  bind(ownerDocument, 'lostpointercapture', finish, {capture: true});
}

function bindPreviewZoomParentWheel(shell, viewport, bind) {
  bind(shell.ownerDocument, 'wheel', event => {
    if (!shell.isConnected || !viewport.contains(event.target)) return;
    if (event.defaultPrevented || event.ctrlKey) return;
    const scrollOwner = shell.closest?.('.file-editor-preview-pane-panel');
    if (!scrollOwner || scrollOwner === viewport) return;
    const lineScale = event.deltaMode === 1
      ? (Number.parseFloat(previewZoomOwnerWindow(shell)?.getComputedStyle?.(scrollOwner)?.lineHeight || '') || 16)
      : 1;
    const xScale = event.deltaMode === 2 ? Math.max(1, scrollOwner.clientWidth) : lineScale;
    const yScale = event.deltaMode === 2 ? Math.max(1, scrollOwner.clientHeight) : lineScale;
    event.preventDefault();
    const innerLeft = viewport.scrollLeft;
    const innerTop = viewport.scrollTop;
    scrollOwner.scrollBy({left: event.deltaX * xScale, top: event.deltaY * yScale, behavior: 'auto'});
    const restoreInnerScroll = () => {
      if (!shell._previewZoomLifecycleScope?.current?.()) return;
      viewport.scrollLeft = innerLeft;
      viewport.scrollTop = innerTop;
    };
    restoreInnerScroll();
    schedulePreviewZoomFrame(shell, restoreInnerScroll);
  }, {capture: true, passive: false});
}

function hydratePreviewZoomSurface(shell, content = null, options = null) {
  if (!shell) return false;
  const resolvedContent = content || previewZoomSurfaceContent(shell);
  if (!resolvedContent) return false;
  const resolvedOptions = options || previewZoomOptionsFromSurface(shell);
  disconnectPreviewZoomSurface(shell);
  writePreviewZoomSurfaceDataset(shell, resolvedOptions);
  const toolbar = shell.querySelector(':scope > .file-editor-preview-zoom-toolbar');
  const viewport = shell.querySelector(':scope > .file-editor-preview-zoom-viewport');
  if (!toolbar || !viewport) return false;
  const lifecycleScope = createLifecycleScope({
    isCurrent: () => shell._previewZoomLifecycleScope === lifecycleScope,
    onDispose: () => {
      if (shell._previewZoomLifecycleScope === lifecycleScope) shell._previewZoomLifecycleScope = null;
    },
  });
  shell._previewZoomLifecycleScope = lifecycleScope;
  let nextListenerId = 0;
  const bind = (target, type, handler, listenerOptions = false) => {
    if (!target?.addEventListener) return;
    lifecycleScope.ownEvent(`listener-${nextListenerId += 1}`, target, type, handler, listenerOptions);
  };
  bind(toolbar, 'click', event => {
    const button = event.target?.closest?.('[data-preview-zoom-action]');
    if (!button || !toolbar.contains(button) || button.disabled) return;
    const current = Number.parseFloat(shell.dataset.previewZoomScale || '1') || 1;
    const zoomState = previewZoomStateForAction(button.dataset.previewZoomAction, current);
    if (zoomState) setPreviewZoomSurfaceState(shell, resolvedContent, resolvedOptions, zoomState, {
      centerIfUnfocused: true,
      userInitiated: true,
    });
  });
  if (resolvedOptions.panDrag === true) bindPreviewZoomDragPan(shell, viewport, bind, resolvedOptions);
  if (resolvedOptions.wheelScrollParent === true) bindPreviewZoomParentWheel(shell, viewport, bind);
  const ownerWindow = previewZoomOwnerWindow(shell);
  // Hide the diagram until its viewport size has settled, then reveal. A file editor pane opens at a
  // transient height and Dockview re-lays-it-out ~150ms later (and a hover that triggers a relayout
  // does the same), so fitting against the transient size and then re-fitting makes the diagram
  // visibly jump/resize. `visibility:hidden` keeps the viewport measurable while hidden, and a
  // debounce after the last apply reveals it once at the settled size.
  shell.classList.add('file-editor-preview-zoom-measuring');
  const scheduleReveal = () => {
    if (!lifecycleScope.current() || !shell.classList.contains('file-editor-preview-zoom-measuring')) return;
    lifecycleScope.release('reveal-timer');
    let timer = null;
    timer = ownerWindow?.setTimeout?.(() => {
      lifecycleScope.release('reveal-timer', timer);
      if (!lifecycleScope.current()) return;
      shell.classList.remove('file-editor-preview-zoom-measuring');
    }, 150);
    lifecycleScope.ownTimer('reveal-timer', timer, value => ownerWindow?.clearTimeout?.(value));
  };
  const applyAndScheduleReveal = (applyOptions, deferredScrollOwner) => {
    const applied = applyPreviewZoomSurface(shell, resolvedContent, resolvedOptions, {
      ...(applyOptions || {}),
      deferredScrollOwner: deferredScrollOwner || createPassiveDeferredElementScrollOwner(viewport),
    });
    if (applied) scheduleReveal();
    return applied;
  };
  const ResizeObserverCtor = ownerWindow?.ResizeObserver || (typeof ResizeObserver === 'function' ? ResizeObserver : null);
  if (ResizeObserverCtor) {
    const scheduleRefit = () => {
      // Coalesce to one apply per frame. applyPreviewZoomSurface resizes the content inside the
      // observed viewport (and can toggle a scrollbar, which changes the viewport content-box),
      // so applying synchronously here would re-trigger this observer and emit the noisy
      // "ResizeObserver loop completed with undelivered notifications" warning.
      const ownerWin = previewZoomOwnerWindow(shell);
      const deferredScrollOwner = createPassiveDeferredElementScrollOwner(viewport);
      lifecycleScope.release('resize-frame');
      let frame = 0;
      frame = schedulePreviewZoomFrame(shell, () => {
        lifecycleScope.release('resize-frame', frame);
        if (!lifecycleScope.current()) return;
        applyAndScheduleReveal({}, deferredScrollOwner);
      });
      lifecycleScope.ownTimer('resize-frame', frame, value => ownerWin?.cancelAnimationFrame?.(value));
    };
    const resizeObserver = new ResizeObserverCtor(scheduleRefit);
    lifecycleScope.ownObserver('resize-observer', resizeObserver);
    resizeObserver.observe(viewport);
    if (resolvedOptions.full === false && shell.parentElement) {
      resizeObserver.observe(shell.parentElement);
      const pane = previewZoomInlinePane(shell);
      if (pane && pane !== shell.parentElement) resizeObserver.observe(pane);
      bind(ownerWindow, 'resize', scheduleRefit);
    }
  }
  bind(resolvedContent, 'load', () => applyAndScheduleReveal(
    {centerIfUnfocused: true},
    createPassiveDeferredElementScrollOwner(viewport),
  ), {once: true});
  const initialScrollOwner = createPassiveDeferredElementScrollOwner(viewport);
  let initialFrame = 0;
  initialFrame = schedulePreviewZoomFrame(shell, () => {
    lifecycleScope.release('initial-frame', initialFrame);
    if (lifecycleScope.current()) applyAndScheduleReveal({centerIfUnfocused: true}, initialScrollOwner);
  });
  lifecycleScope.ownTimer('initial-frame', initialFrame, value => ownerWindow?.cancelAnimationFrame?.(value));
  return true;
}

function hydratePreviewZoomSurfaces(root) {
  const surfaces = Array.from(root?.querySelectorAll?.('.file-editor-preview-zoom-shell') || []);
  if (root?.classList?.contains('file-editor-preview-zoom-shell')) surfaces.unshift(root);
  for (const shell of surfaces) hydratePreviewZoomSurface(shell);
  return surfaces.length;
}

function installPreviewZoomSurface(shell, content, options = {}) {
  disconnectPreviewZoomSurface(shell, {resetClasses: true});
  shell.classList.add('file-editor-preview-zoom-shell');
  shell.classList.toggle('file-editor-preview-zoom-full', options.full !== false);
  shell.classList.toggle('file-editor-preview-zoom-inline', options.full === false);
  writePreviewZoomSurfaceDataset(shell, options);
  const toolbar = document.createElement('div');
  toolbar.className = 'file-editor-preview-zoom-toolbar';
  toolbar.append(...previewZoomActions.map(previewZoomButton));
  const value = document.createElement('span');
  value.className = 'file-editor-preview-zoom-value';
  value.setAttribute('aria-live', 'polite');
  value.textContent = '100%';
  toolbar.appendChild(value);
  const viewport = document.createElement('div');
  viewport.className = 'file-editor-preview-zoom-viewport';
  const stage = document.createElement('div');
  stage.className = 'file-editor-preview-zoom-stage';
  stage.appendChild(content);
  viewport.appendChild(stage);
  shell.replaceChildren(toolbar, viewport);
  hydratePreviewZoomSurface(shell, content, options);
  return shell;
}

function previewZoomSurfaceNode(content, options = {}) {
  return installPreviewZoomSurface(document.createElement('div'), content, options);
}

let mermaidPreviewRenderSeq = 0;
const mermaidRenderCache = new Map();
const mermaidRenderInflight = new Map();
const MERMAID_RENDER_CACHE_LIMIT = 64;

function mermaidRenderCacheKey(source) {
  const text = String(source || '').trim();
  const config = typeof mermaidPreviewConfig === 'function' ? mermaidPreviewConfig() : {};
  return JSON.stringify({text, config, cacheVersion: 1});
}

function mermaidCachedSvg(source) {
  const key = mermaidRenderCacheKey(source);
  const cached = mermaidRenderCache.get(key);
  if (cached) {
    mermaidRenderCache.delete(key);
    mermaidRenderCache.set(key, cached);
    return {key, value: cached};
  }
  return {key, value: null};
}

function rememberMermaidSvg(key, svg) {
  mermaidRenderCache.delete(key);
  mermaidRenderCache.set(key, svg);
  while (mermaidRenderCache.size > MERMAID_RENDER_CACHE_LIMIT) {
    mermaidRenderCache.delete(mermaidRenderCache.keys().next().value);
  }
  return svg;
}

function mermaidSvgForRender(api, key, text, seq) {
  const existing = mermaidRenderInflight.get(key);
  if (existing) return existing;
  const id = `yomux-mermaid-${Date.now()}-${seq}`;
  const promise = Promise.resolve()
    .then(() => api.render(id, text))
    .then(result => {
      const rawSvg = typeof result === 'string' ? result : result?.svg;
      return rememberMermaidSvg(key, sanitizeStandaloneSvg(rawSvg));
    })
    .finally(() => {
      if (mermaidRenderInflight.get(key) === promise) mermaidRenderInflight.delete(key);
    });
  mermaidRenderInflight.set(key, promise);
  return promise;
}

function mermaidErrorNode(source, error) {
  const node = document.createElement('div');
  node.className = 'mermaid-preview-error';
  const title = document.createElement('div');
  title.className = 'file-editor-empty-title';
  title.textContent = t('preview.mermaid.renderFailed');
  const detail = document.createElement('div');
  detail.className = 'file-editor-empty-detail';
  detail.textContent = String(error || t('preview.mermaid.invalidSource'));
  const pre = document.createElement('pre');
  const code = document.createElement('code');
  code.className = 'language-mermaid';
  code.textContent = source;
  pre.appendChild(code);
  node.append(title, detail, pre);
  return node;
}

function mermaidLoadingNode() {
  // While a NEW diagram loads/renders, show the shared blinking "..." (moving-ellipsis) used by the
  // other loading states, not a static "Rendering...". Reuses the empty-state shell so it matches
  // the Mermaid empty/error states.
  const node = document.createElement('div');
  node.className = 'file-editor-empty-state mermaid-preview-loading';
  node.setAttribute('aria-live', 'polite');
  node.setAttribute('aria-busy', 'true');
  const title = document.createElement('div');
  title.className = 'file-editor-empty-title';
  title.innerHTML = textWithMovingEllipsisHtml(t('preview.mermaid.rendering'), 'mermaid-preview-loading-dots');
  node.appendChild(title);
  return node;
}

function disposeMermaidPreviewHost(host) {
  if (!host) return;
  host.dataset.mermaidRenderSeq = `stale-${++mermaidPreviewRenderSeq}`;
  disconnectPreviewZoomSurface(host, {resetClasses: true});
  const source = host.querySelector?.('img.mermaid-preview-image')?.getAttribute?.('src') || '';
  if (source.startsWith('blob:') && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(source);
  }
}

async function renderMermaidSourceInto(container, source, options = {}) {
  const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
  if (!isCurrent()) return false;
  const text = String(source || '').trim();
  disconnectPreviewZoomSurface(container, {resetClasses: true});
  if (!text) {
    container.replaceChildren(fileEditorEmptyState(t('preview.mermaid.empty')));
    return false;
  }
  const seq = ++mermaidPreviewRenderSeq;
  container.dataset.mermaidRenderSeq = String(seq);
  container.classList.add('mermaid-preview');
  container.replaceChildren(mermaidLoadingNode());
  try {
    const api = await loadMermaidApi();
    if (!isCurrent() || container.dataset.mermaidRenderSeq !== String(seq)) return false;
    const cached = mermaidCachedSvg(text);
    let svg = cached.value;
    if (!svg) svg = await mermaidSvgForRender(api, cached.key, text, seq);
    if (!isCurrent() || container.dataset.mermaidRenderSeq !== String(seq)) return false;
    if (!svg) throw new Error(t('preview.mermaid.noSvg'));
    const fullPreview = Object.prototype.hasOwnProperty.call(options, 'full')
      ? options.full !== false
      : container.classList.contains('file-editor-preview-pane-panel');
    return await schedulePreviewDeferredWorkAfterUserScroll(container, 'mermaid-completion', () => {
      if (!isCurrent() || container.dataset.mermaidRenderSeq !== String(seq)) return false;
       const img = document.createElement('img');
       img.className = 'mermaid-preview-image';
       img.alt = t('preview.mermaid.alt');
       img.src = svgImageUrl(svg);
       if (options.interactive === false) {
         container.replaceChildren(img);
         container.classList.add('mermaid-preview');
       } else {
         installPreviewZoomSurface(container, img, previewZoomOptionsForKind(fullPreview ? 'mermaidFull' : 'mermaidInline', {
           ...options,
           path: options.path || '',
           full: fullPreview,
         }));
       }
       return true;
    });
  } catch (error) {
    return await schedulePreviewDeferredWorkAfterUserScroll(container, 'mermaid-completion', () => {
      if (!isCurrent() || container.dataset.mermaidRenderSeq !== String(seq)) return false;
      disconnectPreviewZoomSurface(container, {resetClasses: true});
      container.replaceChildren(mermaidErrorNode(text, error));
      return false;
    });
  }
}

function renderMarkdownMermaidBlocks(container, markdownPath = '', options = {}) {
  const blocks = Array.from(container.querySelectorAll?.('pre > code') || [])
    .filter(block => isMermaidFenceLanguage(markdownFenceLanguage(block)));
  const renders = [];
  blocks.forEach((block, index) => {
    const source = block.textContent || '';
    const pre = block.closest?.('pre');
    if (!pre) return;
    const host = document.createElement('div');
    host.className = 'mermaid-preview-host';
    pre.replaceWith(host);
    renders.push(renderMermaidSourceInto(host, source, {
      full: false,
      path: markdownPath,
      zoomKey: `mermaid:${index}`,
      context: options.context || '',
      isCurrent: options.isCurrent,
    }));
  });
  return renders.length ? Promise.allSettled(renders) : null;
}

function applyMarkdownFenceFallbackHighlight(block) {
  const language = markdownFenceLanguage(block);
  if (!language) return;
  const html = simpleCodeSyntaxHtml(language, block.textContent || '');
  if (html === null) return;
  block.innerHTML = html;
  block.classList.add('editor-highlight-code');
}

function localPathFromFileHref(href) {
  const raw = String(href || '').trim();
  if (!/^file:/i.test(raw)) return '';
  try {
    const base = globalThis.location?.href || 'http://localhost/';
    const url = new URL(raw, base);
    if (url.protocol !== 'file:') return '';
    return safeDecodeURIComponent(url.pathname || '');
  } catch (_) {
    const match = raw.match(/^file:\/\/(?:localhost)?(\/[^?#]*)/i);
    return match ? safeDecodeURIComponent(match[1]) : '';
  }
}

function openMarkdownPreviewPathLink(markdownPath, resolved) {
  const owner = openFileOwnerSessionsForPath(markdownPath || '')[0] || undefined;
  return Promise.resolve(openFileInEditor(resolved, basenameOf(resolved), {
    viewMode: editorPreviewModeAvailable(resolved) ? 'preview' : 'edit',
    ownerSession: owner,
  })).catch(() => emitNotification('previewOpen', {item: fileEditorItemFor(markdownPath || ''), title: t('preview.openFailed', {path: resolved}), className: 'attention-alert toast'}));
}

function markdownPreviewLinkTarget(markdownPath, value, basePath = '') {
  const href = String(value || '').trim();
  if (!href || href.startsWith('#')) return {kind: 'anchor'};
  if (/^file:/i.test(href)) {
    const path = localPathFromFileHref(href);
    return path ? {kind: 'local', path} : {kind: 'invalid'};
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) return {kind: 'external'};
  if (!markdownPath) return {kind: 'relative'};
  const clean = href.split('#')[0].split('?')[0];
  if (!clean) return {kind: 'anchor'};
  const sourceDirectory = basePath || dirnameOf(markdownPath) || '/';
  return {kind: 'local', path: joinAndNormalize(clean.startsWith('/') ? '/' : sourceDirectory, clean)};
}

function markdownPreviewLinkContextMenuOptions(markdownPath, href, basePath = '') {
  const target = markdownPreviewLinkTarget(markdownPath, href, basePath);
  if (target.kind !== 'local') return {};
  return {
    openLabel: localizedLabel('contextmenu.openNewEditor', 'Open in a new Editor'),
    openHandler: () => openMarkdownPreviewPathLink(markdownPath, target.path),
  };
}

function previewLinkDestination(link, path, honorDocumentBase = false) {
  const href = link.getAttribute('href').trim();
  const base = honorDocumentBase && link.ownerDocument.querySelector('base[href]');
  const external = base || markdownPreviewLinkTarget(path, href).kind === 'external';
  if (external) {
    const url = normalizedExternalHttpUrl(link.href);
    return url ? {href: url, url} : {href};
  }
  return {href, ...(bootstrap.guestWorkspace ? {} : markdownPreviewLinkContextMenuOptions(path, href))};
}

function openPreviewLinkDestination(destination) {
  if (destination.openHandler) destination.openHandler();
  else if (destination.url) window.open(destination.url, '_blank', 'noopener,noreferrer');
}

function installPreviewLinkHover(root, {resolve, isCurrent, clientPoint, receivesEvents = true}) {
  const popup = document.createElement('div');
  popup.className = 'viewedit-link-tooltip';
  const events = new AbortController();
  let hover = null;
  let activeLink = null;
  const close = () => hover?.closeNow();
  const showLink = (link, anchor, {x, y}, pointerX, event = {target: link}) => {
    if (!link || !isCurrent()) return;
    if (link === activeLink) {
      if (popup.isConnected) hover.openNow(event);
      else hover.queueOpen(event);
      return;
    }
    close();
    const authoredHref = link.getAttribute('href');
    const destination = resolve(link);
    if (!destination.href) return;
    const action = destination.url ? document.createElement('a')
      : destination.openHandler ? makeButton({label: destination.href}) : null;
    if (action?.tagName === 'A') {
      action.href = destination.url;
      action.target = '_blank';
      action.rel = 'noopener noreferrer';
    }
    if (action) {
      action.textContent = destination.href;
      action.addEventListener('click', event => {
        event.preventDefault();
        openPreviewLinkDestination(destination);
        close();
      });
      popup.removeAttribute('role');
      popup.replaceChildren(action);
    } else {
      popup.setAttribute('role', 'tooltip');
      popup.textContent = destination.href;
    }
    activeLink = link;
    hover = createHoverPopover({
      anchor: link,
      popover: action ? popup : null,
      stateClass: null,
      reflectState: false,
      showDelay: 1500,
      hideDelay: action ? 300 : 0,
      canOpen: () => isCurrent() && link.getAttribute('href') === authoredHref,
      onOpen() {
        if (popup.isConnected) return;
        appOverlayRootElement().append(popup);
        const {width, height} = popup.getBoundingClientRect();
        const below = y + anchor.bottom;
        const top = below + height <= viewportBounds().bottom ? below : y + anchor.top - height;
        const left = Math.max(anchor.left + x, pointerX - width / 2);
        const point = clampToViewport(left, top, width, height);
        Object.assign(popup.style, {left: `${point.left}px`, top: `${point.top}px`});
      },
      onClose() {
        popup.remove();
        hover?.dispose();
        hover = null;
        activeLink = null;
      },
    });
    // An event at timer admission makes the shared controller recheck the live hover.
    hover.queueOpen(event);
  };
  if (receivesEvents) root.addEventListener('pointerover', event => {
    const link = event.target.closest?.('a[href]');
    // Anchor leave starts the grace period. Crossing ordinary content must not end it.
    if (!link || link === activeLink || link.contains(event.relatedTarget)) return;
    close();
    if (event.buttons || !browserHasCursorHover(event)) return;
    const {x, y} = clientPoint(event);
    const anchor = Array.from(link.getClientRects()).find(rect =>
      event.clientY >= rect.top && event.clientY <= rect.bottom) || link.getBoundingClientRect();
    showLink(link, anchor, {x: x - event.clientX, y: y - event.clientY}, x, event);
  }, {signal: events.signal});
  for (const doc of new Set([root.ownerDocument || root, document])) {
    doc.addEventListener('scroll', close, {capture: true, signal: events.signal});
    for (const type of ['pointerdown', 'contextmenu']) {
      doc.addEventListener(type, event => {
        if (!popup.contains(event.target)) close();
      }, {capture: true, signal: events.signal});
    }
    doc.addEventListener('keydown', event => {
      if (event.key === 'Escape') close();
    }, {signal: events.signal});
  }
  return {close, showLink, closeSoon: () => hover?.closeSoon(), destroy() { close(); events.abort(); }};
}

function installHtmlPreviewLinks(container, frame, doc, path, receivesEvents) {
  const isCurrent = () => frame.parentElement === container && frame.contentDocument === doc && Boolean(frame.getClientRects().length);
  const clientPoint = event => {
    const rect = frame.getBoundingClientRect();
    return {x: rect.left + event.clientX, y: rect.top + event.clientY};
  };
  const resolve = link => previewLinkDestination(link, path, true);
  const hover = installPreviewLinkHover(doc, {resolve, isCurrent, clientPoint, receivesEvents});
  const events = new AbortController();
  let pending = 0;
  if (!receivesEvents) {
    for (const link of doc.querySelectorAll('a[href]')) {
      if (link.getAttribute('href').trim().startsWith('#') && !doc.querySelector('base[href]')) continue;
      if (!normalizedExternalHttpUrl(link.href)) continue;
      // The sandbox blocks native popups. Keep direct navigation from replacing the editor
      // document, and open destinations from the application's actionable URL popup.
      link.target = '_blank';
    }
    let previousLink = null, previousScroll = null;
    const stop = () => {
      cancelAnimationFrame(pending);
      pending = 0;
      previousLink = previousScroll = null;
      hover.closeSoon();
    };
    const sample = () => {
      if (!isCurrent() || !frame.matches(':hover')) { stop(); return; }
      const hovered = doc.querySelector('a[href]:hover');
      const link = hovered?.matches(':active') ? null : hovered;
      const scroll = previewEmbeddedScrollElement(frame);
      const position = ownedElementScrollPosition(scroll);
      if (previousScroll && (position.top !== previousScroll.top || position.left !== previousScroll.left)) {
        hover.close();
        previousLink = null;
      }
      previousScroll = position;
      if (link !== previousLink) {
        previousLink = link;
        if (link) {
          const anchor = link.getClientRects()[0];
          if (anchor) {
            const outer = frame.getBoundingClientRect();
            hover.showLink(link, anchor, {x: outer.left, y: outer.top}, outer.left + anchor.left + anchor.width / 2);
          }
        } else hover.closeSoon();
      }
      pending = requestAnimationFrame(sample);
    };
    frame.addEventListener('pointerenter', event => {
      if (browserHasCursorHover(event) && !pending) pending = requestAnimationFrame(sample);
    }, {signal: events.signal});
    frame.addEventListener('pointerleave', stop, {signal: events.signal});
    if (frame.matches(':hover') && browserHasCursorHover()) pending = requestAnimationFrame(sample);
  }
  const destinationForEvent = event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.defaultPrevented || !isCurrent()) return null;
    const destination = resolve(link);
    return destination.url || destination.openHandler ? destination : null;
  };
  if (receivesEvents) doc.addEventListener('click', event => {
    const destination = destinationForEvent(event);
    if (!destination) return;
    event.preventDefault();
    openPreviewLinkDestination(destination);
  }, {signal: events.signal});
  if (receivesEvents) doc.addEventListener('contextmenu', event => {
    const destination = destinationForEvent(event);
    if (!destination) return;
    event.preventDefault();
    const {x, y} = clientPoint(event);
    openLinkContextMenu(destination.href, x, y, destination);
  }, {signal: events.signal});
  return () => { cancelAnimationFrame(pending); hover.destroy(); events.abort(); };
}

// File links use the owning Markdown path for both clicks and context-menu actions; explicit URLs
// retain browser navigation, and the server's read endpoint still enforces filesystem access.
function handleMarkdownPreviewLinkClick(event) {
  const anchor = event.target.closest?.('a');
  if (!anchor) return;
  const container = event.currentTarget;
  const href = anchor.getAttribute('href') || '';
  const target = markdownPreviewLinkTarget(container?.dataset?.mdPath || '', href, container?.dataset?.basePath || '');
  if (target.kind === 'anchor') return;
  event.preventDefault();
  if (target.kind === 'external') {
    window.open(anchor.href, '_blank', 'noopener,noreferrer');
    return;
  }
  if (target.kind === 'local') openMarkdownPreviewPathLink(container?.dataset?.mdPath || '', target.path);
}

function editorPreviewModeAvailable(path, state = null) {
  return previewPathIsPreviewable(path, state || fileState.get(path));
}

function editorVisualLineFragments(line, columnCount, wrapEnabled = fileEditorWrapEnabled) {
  const text = String(line ?? '');
  const width = Math.floor(Number(columnCount) || 0);
  if (!wrapEnabled || width <= 0 || text.length <= width) return [text];
  const fragments = [];
  for (let index = 0; index < text.length; index += width) {
    fragments.push(text.slice(index, index + width));
  }
  return fragments.length ? fragments : [''];
}

function simpleLineSyntaxHtml(language, line) {
  const highlighted = simpleCodeSyntaxHtml(language, line);
  return highlighted === null ? esc(line) : highlighted;
}

function editorVisualHighlightHtml(language, text, options = {}) {
  const source = String(text ?? '');
  const wrapEnabled = options.wrap === true;
  const lineNumbers = options.lineNumbers === true;
  const columnCount = options.columnCount || 88;
  const measuredRows = Array.isArray(options.visualRows) ? options.visualRows : null;
  const rows = source.split('\n');
  return rows.map((line, lineIndex) => {
    const fragments = measuredRows?.[lineIndex] || editorVisualLineFragments(line, columnCount, wrapEnabled);
    return fragments.map((fragment, fragmentIndex) => {
      const sourceLine = lineIndex + 1;
      const continuation = fragmentIndex > 0;
      const rowClass = continuation ? 'editor-visual-line continuation' : 'editor-visual-line';
      const lineNumber = lineNumbers && !continuation ? String(sourceLine) : '';
      const marker = wrapEnabled && continuation ? '↪' : '';
      const code = simpleLineSyntaxHtml(language, fragment);
      return `<span class="${rowClass}" data-source-line="${sourceLine}"><span class="editor-line-number">${esc(lineNumber)}</span><span class="editor-soft-wrap-marker">${esc(marker)}</span><span class="editor-line-code">${code}</span></span>`;
    }).join('');
  }).join('') || '<span class="editor-visual-line" data-source-line="1"><span class="editor-line-number">1</span><span class="editor-soft-wrap-marker"></span><span class="editor-line-code"></span></span>';
}

function renderEditorCodePreviewInto(container, path, text) {
  const language = syntaxLanguageForPath(path);
  const pre = document.createElement('pre');
  pre.className = ['file-editor-code-preview', 'editor-wrap', fileEditorLineNumbersEnabled ? 'editor-line-numbers' : ''].filter(Boolean).join(' ');
  const code = document.createElement('code');
  code.className = `language-${language || 'text'} editor-highlight-code`;
  code.innerHTML = editorVisualHighlightHtml(language, text, {
    wrap: true,
    lineNumbers: fileEditorLineNumbersEnabled,
    columnCount: 96,
  });
  pre.appendChild(code);
  container.replaceChildren(pre);
}

function boundedPreviewText(text, maxChars = 20000) {
  const source = String(text ?? '');
  if (source.length <= maxChars) return {text: source, truncated: false};
  return {text: source.slice(0, maxChars), truncated: true};
}

function previewRendererLanguageForPath(path) {
  const renderer = previewRendererForPath(path);
  const ext = fileExtensionOf(path);
  return renderer?.languageByExtension?.[ext] || renderer?.language || syntaxLanguageForPath(path) || 'text';
}

function jsonStructuredPreview(label, source, errorLabel = t('preview.structured.parseError', {format: label})) {
  try {
    return {label, text: JSON.stringify(JSON.parse(source), null, 2), language: 'json', error: ''};
  } catch (error) {
    return {label: errorLabel, text: source, language: 'json', error: String(error?.message || error)};
  }
}

function notebookStructuredPreview(source) {
  let notebook;
  try {
    notebook = JSON.parse(String(source ?? ''));
  } catch (error) {
    return {label: t('preview.structured.parseError', {format: t('preview.format.notebook')}), text: source, language: 'json', error: String(error?.message || error)};
  }
  const cells = Array.isArray(notebook?.cells) ? notebook.cells : [];
  const out = [t('preview.notebook.summary', {count: cells.length})];
  cells.slice(0, 80).forEach((cell, index) => {
    const type = String(cell?.cell_type || 'cell');
    const sourceText = Array.isArray(cell?.source) ? cell.source.join('') : String(cell?.source || '');
    const outputCount = Array.isArray(cell?.outputs) ? cell.outputs.length : 0;
    const outputs = outputCount ? t('preview.notebook.outputsHidden', {count: outputCount}) : '';
    out.push('', `## ${index + 1}. ${type}${outputs}`, sourceText.trimEnd());
  });
  if (cells.length > 80) out.push('', t('preview.notebook.moreCells', {count: cells.length - 80}));
  return {label: t('preview.notebook.title'), text: out.join('\n'), language: 'markdown', error: ''};
}

function parseJsonStructuredPreviewStrategy(source) {
  return jsonStructuredPreview(t('preview.structured.title', {format: 'JSON'}), source, t('preview.structured.parseError', {format: 'JSON'}));
}

function parseGeoJsonStructuredPreviewStrategy(source) {
  return jsonStructuredPreview(t('preview.structured.title', {format: 'GeoJSON'}), source, t('preview.structured.parseError', {format: 'GeoJSON'}));
}

function parseExcalidrawStructuredPreviewStrategy(source) {
  return jsonStructuredPreview(t('preview.structured.title', {format: 'Excalidraw JSON'}), source, t('preview.structured.parseError', {format: 'Excalidraw'}));
}

function parseNotebookStructuredPreviewStrategy(source) { return notebookStructuredPreview(source); }
function parseTomlStructuredPreviewStrategy(source) { return {label: t('preview.structured.title', {format: 'TOML'}), text: source, language: 'ini', error: ''}; }
function parseXmlStructuredPreviewStrategy(source) { return {label: t('preview.structured.title', {format: 'XML'}), text: source, language: 'xml', error: ''}; }
function parseDrawioStructuredPreviewStrategy(source) { return {label: t('preview.structured.title', {format: 'Draw.io XML'}), text: source, language: 'xml', error: ''}; }

function parseStructuredPreviewStrategy(path, text, renderer = PREVIEW_RENDERER_BY_ID.get('structured')) {
  const source = String(text ?? '');
  const ext = fileExtensionOf(path);
  const parse = renderer.parseByExtension?.[ext];
  if (parse) return parse(source);
  const language = renderer?.languageByExtension?.[ext] || 'yaml';
  const format = language === 'ini' ? t('preview.format.config') : 'YAML';
  return {label: t('preview.structured.title', {format}), text: source, language, error: ''};
}

function renderStructuredPreviewInto(container, path, text, renderer = PREVIEW_RENDERER_BY_ID.get('structured')) {
  const value = renderer.parse(path, text, renderer);
  const bounded = boundedPreviewText(value.text);
  const wrapper = document.createElement('div');
  wrapper.className = 'file-editor-data-preview';
  const header = document.createElement('div');
  header.className = 'file-editor-data-preview-header';
  header.textContent = bounded.truncated ? t('preview.truncated', {label: value.label}) : value.label;
  wrapper.appendChild(header);
  if (value.error) {
    const error = document.createElement('div');
    error.className = 'file-editor-preview-error';
    error.textContent = value.error;
    wrapper.appendChild(error);
  }
  const pre = document.createElement('pre');
  pre.className = 'file-editor-code-preview editor-wrap';
  const code = document.createElement('code');
  code.className = `language-${value.language} editor-highlight-code`;
  code.innerHTML = editorVisualHighlightHtml(value.language, bounded.text, {
    wrap: true,
    lineNumbers: fileEditorLineNumbersEnabled,
    columnCount: 96,
  });
  pre.appendChild(code);
  wrapper.appendChild(pre);
  container.replaceChildren(wrapper);
}

function splitDelimitedPreviewLine(line, delimiter) {
  const cells = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const ch = line[index];
    if (ch === '"') {
      if (quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }
    if (ch === delimiter && !quoted) {
      cells.push(value);
      value = '';
      continue;
    }
    value += ch;
  }
  cells.push(value);
  return cells;
}

function compactJsonLinesCell(value, maxChars = 240) {
  let full;
  if (typeof value === 'string') full = value;
  else if (value === undefined) full = '';
  else if (value !== null && typeof value === 'object') full = JSON.stringify(value);
  else full = String(value);
  const truncated = full.length > maxChars;
  return {text: truncated ? `${full.slice(0, Math.max(0, maxChars - 1))}…` : full, title: full, truncated};
}

function jsonLinesTablePreview(path, text, options = {}) {
  const maxRows = Math.max(1, Number(options.maxRows) || 200);
  const maxColumns = Math.max(1, Number(options.maxColumns) || 40);
  const maxCellChars = Math.max(8, Number(options.maxCellChars) || 240);
  const sourceLines = String(text ?? '').split(/\r?\n/);
  const nonEmptyLines = sourceLines
    .map((raw, index) => ({raw, lineNumber: index + 1}))
    .filter(entry => entry.raw.trim());
  const parsedRows = nonEmptyLines.slice(0, maxRows).map(entry => {
    try {
      const value = JSON.parse(entry.raw);
      const record = value && typeof value === 'object' && !Array.isArray(value) ? value : {$value: value};
      return {...entry, parsed: true, record};
    } catch (_error) {
      return {...entry, parsed: false, record: null};
    }
  });
  const allColumns = [];
  const seenColumns = new Set();
  for (const row of parsedRows) {
    for (const key of Object.keys(row.record || {})) {
      if (seenColumns.has(key)) continue;
      seenColumns.add(key);
      allColumns.push(key);
    }
  }
  const columns = allColumns.slice(0, maxColumns);
  const overflowColumns = allColumns.slice(maxColumns);
  const rows = parsedRows.map(row => {
    if (!row.parsed) return {lineNumber: row.lineNumber, parsed: false, raw: row.raw};
    const cells = columns.map(key => compactJsonLinesCell(Object.hasOwn(row.record, key) ? row.record[key] : undefined, maxCellChars));
    const overflow = Object.create(null);
    for (const key of overflowColumns) {
      if (Object.hasOwn(row.record, key)) overflow[key] = row.record[key];
    }
    return {
      lineNumber: row.lineNumber,
      parsed: true,
      cells,
      overflow: overflowColumns.length ? compactJsonLinesCell(overflow, maxCellChars) : null,
    };
  });
  return {
    format: fileExtensionOf(path) === '.ndjson' ? 'NDJSON' : 'JSONL',
    total: nonEmptyLines.length,
    shown: rows.length,
    columns,
    overflowColumns,
    rows,
    truncated: nonEmptyLines.length > rows.length || overflowColumns.length > 0,
  };
}

function appendJsonLinesTableCell(row, tagName, cell, className = '') {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  node.textContent = cell?.text || '';
  if (cell?.title) node.title = cell.title;
  row.appendChild(node);
  return node;
}

function jsonLinesTableColumnClass(column) {
  const normalized = String(column || '').trim().toLowerCase();
  if (normalized === 'payload') return 'file-editor-jsonl-payload';
  if (normalized === 'timestamp' || normalized === 'time' || normalized === 'type') return 'file-editor-jsonl-compact-column';
  return '';
}

function renderJsonLinesTablePreviewInto(container, path, text) {
  const preview = jsonLinesTablePreview(path, text);
  const wrapper = document.createElement('div');
  wrapper.className = 'file-editor-table-preview file-editor-jsonl-preview';
  const header = document.createElement('div');
  header.className = 'file-editor-data-preview-header';
  header.textContent = t('preview.table.summary', {
    format: preview.format,
    shown: preview.shown,
    total: preview.total,
    truncated: preview.truncated ? t('preview.table.truncatedSuffix') : '',
  });
  wrapper.appendChild(header);
  const table = document.createElement('table');
  table.className = 'file-editor-jsonl-table';
  const colgroup = document.createElement('colgroup');
  preview.columns.forEach(column => {
    const col = document.createElement('col');
    const className = jsonLinesTableColumnClass(column);
    if (className) col.className = className;
    col.dataset.jsonlField = column;
    colgroup.appendChild(col);
  });
  if (preview.overflowColumns.length) colgroup.appendChild(document.createElement('col'));
  if (!preview.columns.length && !preview.overflowColumns.length) colgroup.appendChild(document.createElement('col'));
  table.appendChild(colgroup);
  const head = document.createElement('thead');
  const headingRow = document.createElement('tr');
  preview.columns.forEach(column => appendJsonLinesTableCell(headingRow, 'th', {text: column, title: column}, jsonLinesTableColumnClass(column)));
  if (preview.overflowColumns.length) {
    appendJsonLinesTableCell(headingRow, 'th', {
      text: `… +${preview.overflowColumns.length}`,
      title: preview.overflowColumns.join(', '),
    }, 'file-editor-jsonl-overflow');
  }
  if (!preview.columns.length && !preview.overflowColumns.length) {
    appendJsonLinesTableCell(headingRow, 'th', {text: preview.format});
  }
  head.appendChild(headingRow);
  table.appendChild(head);
  const body = document.createElement('tbody');
  const columnSpan = Math.max(1, preview.columns.length + (preview.overflowColumns.length ? 1 : 0));
  preview.rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.dataset.sourceLine = String(row.lineNumber);
    if (!row.parsed) {
      tr.className = 'file-editor-jsonl-unparsed';
      const raw = appendJsonLinesTableCell(tr, 'td', {text: row.raw, title: row.raw});
      raw.colSpan = columnSpan;
      raw.dataset.unparsedLine = String(row.lineNumber);
    } else {
      row.cells.forEach((cell, index) => appendJsonLinesTableCell(tr, 'td', cell, jsonLinesTableColumnClass(preview.columns[index])));
      if (row.overflow) appendJsonLinesTableCell(tr, 'td', row.overflow, 'file-editor-jsonl-overflow');
    }
    body.appendChild(tr);
  });
  table.appendChild(body);
  wrapper.appendChild(table);
  container.replaceChildren(wrapper);
}

function parseDelimitedPreviewStrategy(path, text, renderer = PREVIEW_RENDERER_BY_ID.get('table')) {
  const delimiter = renderer.delimiterByExtension[fileExtensionOf(path)];
  const maxRows = 200;
  const maxCols = 50;
  const lines = String(text ?? '').split(/\r?\n/).filter(line => line.length > 0);
  let truncatedColumns = false;
  const rows = lines.slice(0, maxRows).map(line => {
    const cells = splitDelimitedPreviewLine(line, delimiter);
    if (cells.length > maxCols) truncatedColumns = true;
    return cells.slice(0, maxCols);
  });
  return {delimiter, lines, maxRows, rows, truncatedColumns};
}

function renderDelimitedPreviewInto(container, path, text, renderer = PREVIEW_RENDERER_BY_ID.get('table')) {
  const {delimiter, lines, maxRows, rows, truncatedColumns} = renderer.parse(path, text, renderer);
  const wrapper = document.createElement('div');
  wrapper.className = 'file-editor-table-preview';
  const header = document.createElement('div');
  header.className = 'file-editor-data-preview-header';
  header.textContent = t('preview.table.summary', {
    format: delimiter === '\t' ? 'TSV' : 'CSV',
    shown: Math.min(lines.length, maxRows),
    total: lines.length,
    truncated: lines.length > maxRows || truncatedColumns ? t('preview.table.truncatedSuffix') : '',
  });
  wrapper.appendChild(header);
  const table = document.createElement('table');
  const body = document.createElement('tbody');
  rows.forEach((row, rowIndex) => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const node = document.createElement(rowIndex === 0 ? 'th' : 'td');
      node.textContent = cell;
      tr.appendChild(node);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);
  wrapper.appendChild(table);
  container.replaceChildren(wrapper);
}

function htmlPreviewHasDisabledJavaScript(text) {
  const source = String(text ?? '');
  return /<script\b/i.test(source) || /\son[a-z]+\s*=/i.test(source);
}

function htmlPreviewUrl(path) {
  return `/api/batch/html-preview?path=${encodeURIComponent(path)}`;
}

function renderRawImagePreviewInto(container, path, state = null, options = {}) {
  const version = rawFileMediaVersion(state);
  const img = document.createElement('img');
  img.className = 'file-editor-preview-image';
  img.alt = basenameOf(path);
  img.loading = 'eager';
  img.decoding = 'async';
  container.replaceChildren(previewZoomSurfaceNode(img, previewZoomOptionsForKind('imagePreview', {
    path,
    context: options.context || '',
  })));
  container._previewAsync = installRawFileMediaSource(img, path, {
    snapshot: options.snapshot === true,
    params: version ? {v: version} : {},
    isCurrent: () => container.contains(img),
    onFailure: error => scheduleRawMediaFallbackAfterUserScroll(container, img, 'raw-image-failure', () => (
      previewActionFallbackNode(
        t('preview.image.loadFailed'),
        userMessageText(error, t('common.requestFailed')),
        path,
      )
    )),
    onDecodeFailure: () => scheduleRawMediaFallbackAfterUserScroll(container, img, 'raw-image-failure', () => (
      previewActionFallbackNode(
        t('preview.image.loadFailed'),
        `${previewMimeForPath(path) || 'image'}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`,
        path,
      )
    )),
  });
}

function renderPdfPreviewInto(container, path, {sourceUrl = rawFileUrl(path), objectUrl = '', item: previewItem = '', readOnly = false, retainPrevious = false, onLoadError = null, onDocumentReady = null} = {}) {
  container.classList.add('pdf-preview-body');
  const ownerDocument = container.ownerDocument || document;
  const source = new URL(sourceUrl, `${location.protocol}//${location.host}/`).href;
  const frame = ownerDocument.createElement('iframe');
  frame.className = 'file-editor-pdf-preview';
  let staging = retainPrevious && Boolean(container.querySelector(':scope > iframe[data-pdf-ready="true"]'));
  if (staging) {
    container.classList.add('pdf-preview-status-host');
    frame.classList.add('pdf-preview-staged-frame');
  }
  frame.setAttribute('title', t('preview.pdf.frameTitle', {name: basenameOf(path)}));
  const item = previewItem || container.closest?.('[data-layout-item]')?.dataset.layoutItem || fileEditorItemFor(path);
  frame.dataset.layoutItem = item;
  const pdfViewOwner = Symbol();
  const savedPage = fileEditorViewState.get(item)?.pdfPage;
  const initialPage = Number.isSafeInteger(savedPage) && savedPage > 0 ? savedPage : 1;
  let active = true;
  let app = null;
  let viewerDocument = null;
  let documentInitialized = false;
  let wasVisible = false;
  let wideSidebar = null;
  let retiring = false;
  let lastPdfScrollTop = 0;
  let lastPdfScrollLeft = 0;
  let stagedPage = 0;
  const paintedPages = new Set();
  const visible = () => active && frame.isConnected && frame.getBoundingClientRect().width > 0
    && frame.getBoundingClientRect().height > 0;
  const blockDocumentReplacement = event => {
    if (event.dataTransfer?.files?.[0]?.type === 'application/pdf' || event.type === 'change' && event.target?.id === 'fileInput'
        || event.type === 'keydown' && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'o') {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  const rememberViewState = (patch, deferUrl = false) => {
    if (!visible()) {
      wasVisible = false;
      return;
    }
    // A reveal can publish PDF.js's temporary page before the resize observer restores ours.
    if (!wasVisible || app !== frame.contentWindow?.PDFViewerApplication || app?.url !== source) return;
    if (patch.pdfPage) frame.dataset.pdfPage = String(patch.pdfPage);
    const previous = fileEditorViewState.get(item) || {};
    if (Object.entries(patch).every(([key, value]) => previous[key] === value)) return;
    const next = {...previous, ...patch};
    if (patch.pdfPage || patch.pdfViewport) next.pdfViewOwner = pdfViewOwner;
    fileEditorViewState.set(item, next);
    if (!retiring && itemInLayout(item)) {
      if (deferUrl) {
        storePdfReloadViewport(item, patch.pdfViewport);
        scheduleLayoutUrlStateRefresh();
      } else {
        storePdfReloadViewport(item, null);
        updateActiveSessionParam();
      }
    }
  };
  const rememberPage = ({pageNumber}) => {
    if (Number.isSafeInteger(pageNumber) && pageNumber > 0) rememberViewState({pdfPage: pageNumber});
  };
  const rememberViewport = ({location: position}) => {
    const viewer = app.pdfViewer;
    lastPdfScrollTop = viewer.container.scrollTop;
    lastPdfScrollLeft = viewer.container.scrollLeft;
    const viewport = normalizedPdfViewport({
      page: position.pageNumber, activePage: viewer.currentPageNumber,
      left: position.left, top: position.top, zoom: viewer.currentScaleValue,
      rotation: viewer.pagesRotation, scrollMode: viewer.scrollMode, spreadMode: viewer.spreadMode,
    });
    if (!viewport) return;
    const previous = fileEditorViewState.get(item)?.pdfViewport;
    if (previous && Object.keys(viewport).every(key => previous[key] === viewport[key])) return;
    const scrolling = previous && ['zoom', 'rotation', 'scrollMode', 'spreadMode'].every(key => previous[key] === viewport[key]);
    rememberViewState({pdfViewport: viewport}, scrolling);
  };
  const rememberSidebar = () => {
    if (!wideSidebar && isPdfSidebarView(app?.viewsManager?.active)) {
      rememberViewState({pdfSidebarView: app.viewsManager.active, pdfSidebarOpen: app.viewsManager.isOpen});
    }
  };
  const fitOfficeSidebar = () => {
    const sidebar = app.viewsManager;
    if (!readOnly || !sidebar) return;
    // At this width PDF.js overlays its sidebar on the page. Keep the wide preference.
    if (frame.contentWindow.matchMedia('(max-width: 840px)').matches) {
      if (!wideSidebar) {
        wideSidebar = {view: sidebar.active, open: sidebar.isOpen};
        sidebar.close();
      }
    } else if (wideSidebar) {
      const {view, open} = wideSidebar;
      wideSidebar = null;
      if (open) sidebar.switchView(view, true);
      else sidebar.close();
    }
  };
  const documentReady = () => {
    if (!active || app !== frame.contentWindow?.PDFViewerApplication || app.url !== source) return;
    documentInitialized = true;
    if (!visible()) return;
    wasVisible = false;
    app.eventBus.off('pagechanging', rememberPage);
    app.eventBus.off('updateviewarea', rememberViewport);
    app.eventBus.off('sidebarviewchanged', rememberSidebar);
    const state = fileEditorViewState.get(item);
    if (!wideSidebar) {
      if (isPdfSidebarView(state?.pdfSidebarView)) app.viewsManager.switchView(state.pdfSidebarView, state.pdfSidebarOpen === true);
      if (state?.pdfSidebarOpen === false) app.viewsManager.switchView(0);
    }
    fitOfficeSidebar();
    const page = state?.pdfPage || initialPage;
    const targetPage = Math.min(page, app.pagesCount);
    const viewport = state?.pdfViewport;
    const viewer = app.pdfViewer;
    if (viewport) {
      if (viewport.rotation !== undefined && viewer.pagesRotation !== viewport.rotation) viewer.pagesRotation = viewport.rotation;
      if (viewport.scrollMode !== undefined && viewer.scrollMode !== viewport.scrollMode) viewer.scrollMode = viewport.scrollMode;
      if (viewport.spreadMode !== undefined && viewer.spreadMode !== viewport.spreadMode) viewer.spreadMode = viewport.spreadMode;
      if (viewer.currentScaleValue !== viewport.zoom) viewer.currentScaleValue = viewport.zoom;
    }
    if (viewport?.activePage === page && page <= app.pagesCount && viewport.page <= app.pagesCount) {
      viewer.scrollPageIntoView({
        pageNumber: viewport.page,
        destArray: [null, {name: 'XYZ'}, viewport.left, viewport.top, null],
        allowNegativeOffset: true, ignoreDestinationZoom: true,
      });
    } else if (viewer.currentPageNumber !== targetPage) {
      viewer.currentPageNumber = targetPage;
    }
    if (staging) {
      stagedPage = viewer.currentPageNumber;
      const view = viewer.getPageView(stagedPage - 1);
      if (!paintedPages.has(stagedPage) || !view?.div.querySelector('canvas')) {
        viewer.update();
        return;
      }
    }
    let restoreFocus = false;
    let focusedControl = '';
    if (staging) {
      const focusedFrame = ownerDocument.activeElement;
      restoreFocus = focusedFrame !== frame && focusedFrame?.parentElement === container && focusedFrame.matches('iframe');
      if (restoreFocus) focusedControl = focusedFrame.contentDocument?.activeElement?.id || '';
      // Reparenting an iframe reloads it. Keep the admitted viewer node mounted while retiring its siblings.
      for (const sibling of [...container.children]) {
        if (sibling === frame) continue;
        if (sibling.matches('iframe')) releaseRawFileMediaSource(sibling);
        sibling.remove();
      }
      staging = false;
      app.eventBus.off('pagerendered', stagedPageRendered);
      frame.classList.remove('pdf-preview-staged-frame');
      container.append(fallback);
    }
    lastPdfScrollTop = viewer.container.scrollTop;
    lastPdfScrollLeft = viewer.container.scrollLeft;
    wasVisible = true;
    rememberPage({pageNumber: app.pdfViewer.currentPageNumber});
    rememberSidebar();
    app.eventBus.on('pagechanging', rememberPage);
    app.eventBus.on('updateviewarea', rememberViewport);
    app.eventBus.on('sidebarviewchanged', rememberSidebar);
    frame.dataset.pdfReady = 'true';
    fallback.hidden = true;
    if (restoreFocus) {
      frame.focus({preventScroll: true});
      if (focusedControl) viewerDocument.getElementById(focusedControl)?.focus({preventScroll: true});
    }
    onDocumentReady?.({frame, app, canNavigate: () => active && wasVisible && visible()});
  };
  const reportLoadError = message => {
    if (!active) return;
    if (staging) {
      releaseRawFileMediaSource(frame);
      frame.remove();
    }
    if (onLoadError) onLoadError(message);
    else statusErr(message);
  };
  const documentError = ({source: failedApp, message}) => {
    if (failedApp === app) reportLoadError(message);
  };
  const stagedPageRendered = ({pageNumber, cssTransform, error}) => {
    if (!active || !staging || cssTransform) return;
    if (error) {
      if (pageNumber === stagedPage) reportLoadError(userMessageText(error, t('common.requestFailed')));
      return;
    }
    paintedPages.add(pageNumber);
    if (pageNumber === stagedPage) documentReady();
  };
  const detachViewer = () => {
    for (const type of ['drop', 'change', 'keydown']) viewerDocument?.removeEventListener(type, blockDocumentReplacement, true);
    app?.eventBus?.off('documentinit', documentReady);
    app?.eventBus?.off('pagerendered', stagedPageRendered);
    app?.eventBus?.off('documenterror', documentError);
    app?.eventBus?.off('pagechanging', rememberPage);
    app?.eventBus?.off('updateviewarea', rememberViewport);
    app?.eventBus?.off('sidebarviewchanged', rememberSidebar);
  };
  const initialize = event => {
    if (!active || event.detail?.source !== frame.contentWindow) return;
    detachViewer();
    documentInitialized = false;
    paintedPages.clear();
    wasVisible = false;
    wideSidebar = null;
    delete frame.dataset.pdfReady;
    const viewer = event.detail.source;
    viewerDocument = viewer.document;
    for (const type of ['drop', 'change', 'keydown']) viewerDocument.addEventListener(type, blockDocumentReplacement, true);
    for (const id of ['openFile', 'secondaryOpenFile']) {
      const control = viewerDocument.getElementById(id);
      if (control) control.hidden = true;
    }
    const sidebar = fileEditorViewState.get(item);
    viewer.PDFViewerApplicationOptions.setAll({
      disablePreferences: true, disableHistory: true, viewOnLoad: 1,
      sidebarViewOnLoad: typeof sidebar?.pdfSidebarOpen === 'boolean'
        ? (sidebar.pdfSidebarOpen ? (isPdfSidebarView(sidebar.pdfSidebarView) ? sidebar.pdfSidebarView : 1) : 0) : -1,
      enableScripting: false, enableXfa: false, externalLinkTarget: 2,
      enableWebGPU: false,
      ...(readOnly ? {annotationEditorMode: -1, annotationMode: 1, enableSignatureEditor: false} : {}),
    });
    if (readOnly) {
      for (const id of ['editorModeButtons', 'editorModeSeparator', 'editorCommentButton', 'editorSignatureButton']) {
        const control = viewerDocument.getElementById(id);
        if (control) control.hidden = true;
      }
    }
    app = viewer.PDFViewerApplication;
    const initializedApp = app;
    app.initializedPromise.then(() => {
      if (!active || app !== initializedApp) return;
      // Dockview hides inactive iframe documents, where PDF.js cannot scroll a page into layout.
      const scrollPage = app.pdfViewer.scrollPageIntoView.bind(app.pdfViewer);
      app.pdfViewer.scrollPageIntoView = options => { if (visible()) scrollPage(options); };
      app.eventBus.on('documentinit', documentReady);
      if (staging) app.eventBus.on('pagerendered', stagedPageRendered);
      if (onLoadError) app.eventBus.on('documenterror', documentError);
    }).catch(error => {
      if (!active || app !== initializedApp) return;
      const message = userMessageText(error, t('common.requestFailed'));
      reportLoadError(message);
    });
  };
  ownerDocument.addEventListener('webviewerloaded', initialize);
  const ResizeObserverCtor = ownerDocument.defaultView?.ResizeObserver;
  const visibilityObserver = ResizeObserverCtor ? new ResizeObserverCtor(() => {
    const isVisible = visible();
    if (isVisible && !wasVisible && documentInitialized) documentReady();
    else if (isVisible && documentInitialized) fitOfficeSidebar();
    if (!isVisible) wasVisible = false;
  }) : null;
  visibilityObserver?.observe(frame);
  const retire = event => {
    if (event.persisted) return;
    const currentOwner = fileEditorViewState.get(item)?.pdfViewOwner;
    const scroller = app?.pdfViewer?.container;
    const unreadScroll = scroller && (scroller.scrollTop !== lastPdfScrollTop || scroller.scrollLeft !== lastPdfScrollLeft);
    if (ownerDocument === document && !staging && wasVisible && visible() && itemInLayout(item)
        && (!currentOwner || currentOwner === pdfViewOwner || unreadScroll)) {
      retiring = true;
      // PDF.js reads native scroll on the next animation frame, which a reload can interrupt.
      app.pdfViewer.update();
      storePdfReloadViewport(item, fileEditorViewState.get(item)?.pdfViewport);
    }
    releaseRawFileMediaSource(frame);
  };
  ownerDocument.defaultView?.addEventListener('pagehide', retire);
  frame._rawFileReadyCleanup = () => {
    active = false;
    visibilityObserver?.disconnect();
    ownerDocument.removeEventListener('webviewerloaded', initialize);
    ownerDocument.defaultView?.removeEventListener('pagehide', retire);
    detachViewer();
  };
  frame.src = `/static/vendor/pdfjs/web/viewer.html?file=${encodeURIComponent(source)}`;
  frame._rawFileObjectUrl = objectUrl;
  const fallback = ownerDocument.createElement('div');
  fallback.className = 'file-editor-preview-fallback';
  const title = ownerDocument.createElement('div');
  title.className = 'file-editor-empty-title';
  title.textContent = t('preview.pdf.title');
  const detail = ownerDocument.createElement('div');
  detail.className = 'file-editor-empty-detail';
  if (!objectUrl) detail.append(...previewFileActionLinks(path));
  fallback.append(title, detail);
  if (staging) container.append(frame);
  else {
    releaseRawFileMediaSources(container);
    container.replaceChildren(frame, fallback);
  }
}

function bindTexPreviewScrollSync(panel) {
  panel?.querySelector('.file-editor-preview-pane-panel')?._texPreviewSyncBind?.();
}

function cleanupTexProgressJob(jobID) {
  if (!jobID) return;
  const query = new URLSearchParams({job_id: jobID});
  void apiFetch(`/api/tex/compiler?${query}`, {method: 'DELETE', keepalive: true}, {recordDebug: false}).catch(() => {});
}

function retireStagedPdfPreview(container) {
  for (const frame of container?.querySelectorAll('.pdf-preview-staged-frame') || []) {
    releaseRawFileMediaSource(frame);
    frame.remove();
  }
}

// Keep status separate from the PDF viewport so progress does not resize the reading surface.
function showPdfPreviewUpdateStatus(viewer, card) {
  retireStagedPdfPreview(viewer);
  const previous = viewer.querySelector(':scope > .pdf-preview-update-status');
  previous?.remove();
  if (viewer.querySelector(':scope > iframe[data-pdf-ready="true"]')) {
    viewer.classList.add('pdf-preview-status-host');
    card.classList.add('pdf-preview-update-status');
    viewer.append(card);
  } else {
    releaseRawFileMediaSources(viewer);
    viewer.replaceChildren(card);
  }
}

function renderOfficePreviewStrategy({container, path, signature}) {
  let host = container.querySelector(':scope > [data-office-preview]');
  const retainPdf = host?.dataset.officePreviewPath === path && Boolean(host.querySelector('iframe[data-pdf-ready="true"]'));
  if (!prepareRetainedPreviewStrategy(container, signature, '[data-office-preview]', {retainPdf})) return false;
  if (!retainPdf) {
    host = document.createElement('div');
    host.dataset.officePreview = '';
    host.dataset.officePreviewPath = path;
    host.className = 'office-preview';
    const actions = document.createElement('div');
    actions.className = 'office-preview-actions';
    const viewer = document.createElement('div');
    viewer.className = 'office-preview-viewer';
    host.append(actions, viewer);
    container.replaceChildren(host);
  }
  const [actions, viewer] = host.children;
  const download = previewFileActionLinks(path).filter(node => node.tagName === 'A').at(-1);
  download.textContent = localizedLabel('preview.office.download', 'Download original');
  actions.replaceChildren(download);
  const controller = new AbortController();
  container._officePreviewController = controller;
  const current = () => container._officePreviewController === controller && !controller.signal.aborted;
  let busy = false;
  const refresh = makeButton({label: t('common.reload'), title: t('common.reload'), ariaLabel: t('common.reload'), onClick: () => { container._previewAsync = run(); }});
  actions.append(refresh);
  const showFailure = message => {
    if (!current()) return;
    const card = fileEditorEmptyState(localizedLabel('preview.office.failed', 'Office preview unavailable'), message);
    card.append(makeButton({label: t('common.retry'), title: t('common.retry'), ariaLabel: t('common.retry'), onClick: () => { container._previewAsync = run(); }}));
    showPdfPreviewUpdateStatus(viewer, card);
  };
  const run = async () => {
    if (!current() || busy) return;
    busy = true;
    refresh.disabled = true;
    showPdfPreviewUpdateStatus(viewer, fileEditorEmptyState(localizedLabel('preview.office.converting', 'Converting Office document to PDF…')));
    try {
      const response = await apiFetch('/api/fs/office-preview', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path}),
        signal: controller.signal, deadlineMs: 35000, startupImmediate: true,
      }, {abortRetirementReason: 'office_preview_replaced'});
      if (!current()) return;
      if (!response.ok || !String(response.headers.get('Content-Type') || '').startsWith('application/pdf')) {
        const payload = await response.json();
        throw new Error([payload.error || t('common.requestFailed'), payload.instructions].filter(Boolean).join('\n'));
      }
      const blob = await response.blob();
      if (!current()) return;
      const objectUrl = URL.createObjectURL(blob);
      renderPdfPreviewInto(viewer, path, {sourceUrl: objectUrl, objectUrl, readOnly: true, retainPrevious: true, onLoadError: showFailure});
    } catch (error) {
      showFailure(userMessageText(error, t('common.requestFailed')));
    } finally {
      busy = false;
      refresh.disabled = false;
    }
  };
  container._previewAsync = run();
  return true;
}

function renderTexPreviewStrategy({container, path, text, signature}) {
  if (bootstrap.guestWorkspace || fileState.get(path)?._shareTeX) return renderSharePublishedPDF(container, path, text);
  let host = container.querySelector(':scope > [data-tex-preview]');
  const retainPdf = host?.dataset.texPreviewPath === path && Boolean(host.querySelector(':scope > iframe[data-pdf-ready="true"]'));
  if (!prepareRetainedPreviewStrategy(container, signature, '[data-tex-preview]', {retainPdf})) return false;
  if (!retainPdf) {
    host = document.createElement('div');
    host.dataset.texPreview = '';
    host.dataset.texPreviewPath = path;
    host.style.cssText = 'display:flex;flex-direction:column;min-height:100%;height:100%';
    container.replaceChildren(host);
  }
  const controller = new AbortController();
  let instructions = '';
  container._texPreviewController = controller;
  const current = () => container._texPreviewController === controller && !controller.signal.aborted;
  const request = (url, body) => apiFetch(url, {
    ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
    signal: controller.signal, deadlineMs: 200000, startupImmediate: true,
  }, {abortRetirementReason: 'tex_preview_replaced'});
  const newJobID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  const runWithProgress = async (title, startRequest, retainCompletedJob = false) => {
    const card = show(title);
    if (!card) return null;
    const progress = document.createElement('progress');
    progress.className = 'tex-preview-progress-bar';
    progress.removeAttribute('value');
    const message = document.createElement('div');
    message.className = 'tex-preview-progress-message';
    message.textContent = title;
    const elapsed = document.createElement('div');
    elapsed.className = 'tex-preview-progress-elapsed';
    const output = document.createElement('pre');
    output.className = 'tex-preview-progress-output';
    output.hidden = true;
    card.append(progress, message, elapsed, output);
    const jobID = newJobID();
    const startedAt = Date.now();
    let active = true;
    let polling = false;
    const updateElapsed = () => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      elapsed.textContent = `${t('duration.minuteShort', {count: Math.floor(seconds / 60)})} ${tPlural('duration.second', seconds % 60)}`;
    };
    const updateProgress = async () => {
      if (!active || polling || !current()) return;
      polling = true;
      try {
        const response = await request(`/api/tex/compiler?job_id=${encodeURIComponent(jobID)}`);
        if (!response.ok) await apiJsonResponse(response);
        const state = (await response.json()).progress;
        if (!state || !active || !current()) return;
        message.textContent = state.message || title;
        const total = Number(state.total_bytes) || 0;
        const downloaded = Number(state.downloaded_bytes) || 0;
        if (total > 0) {
          progress.max = total;
          progress.value = Math.min(downloaded, total);
          message.textContent += ` (${Math.floor(downloaded * 100 / total)}%)`;
        } else {
          progress.removeAttribute('value');
        }
        if (state.log) {
          output.hidden = false;
          output.textContent = state.log;
          output.scrollTop = output.scrollHeight;
        }
      } catch (error) {
        if (active && current()) message.textContent = `${title} · ${userMessageText(error, t('common.requestFailed'))}`;
      } finally {
        polling = false;
      }
    };
    updateElapsed();
    const elapsedTimer = setInterval(updateElapsed, 1000);
    const progressTimer = setInterval(updateProgress, 1000);
    updateProgress();
    let keepJob = false;
    try {
      const response = await startRequest(jobID);
      keepJob = retainCompletedJob;
      return {response, jobID};
    } finally {
      active = false;
      clearInterval(elapsedTimer);
      clearInterval(progressTimer);
      if (!keepJob) cleanupTexProgressJob(jobID);
    }
  };
  const show = (title, detail = '', payload = null) => {
    if (!current()) return;
    const card = fileEditorEmptyState(title, detail);
    card.classList.add('tex-preview-status');
    if (payload?.instructions) {
      const instructions = document.createElement('pre');
      instructions.className = 'tex-preview-instructions';
      instructions.textContent = payload.instructions;
      card.append(instructions);
    }
    showPdfPreviewUpdateStatus(host, card);
    return card;
  };
  const button = (parent, label, action, className = '') => {
    const control = makeButton({label, title: label, ariaLabel: label, className, onClick: () => {
      control.disabled = true;
      container._previewAsync = run(action);
    }});
    if (!parent.classList.contains('tex-preview-status')) {
      parent.append(control);
      return;
    }
    let actions = parent.querySelector('.tex-preview-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'tex-preview-actions';
      parent.append(actions);
    }
    actions.append(control);
    return control;
  };
  const bindTexSplitScroll = (frame, app, canNavigate, anchors) => {
    const panel = container.closest('.file-editor-panel');
    const editor = panel?._cmView;
    if (!editor || !frame.isConnected || !anchors.length) return;
    if (container._texPreviewSyncCleanup?.editor === editor
        && container._texPreviewSyncCleanup.frame === frame && container._texPreviewSyncCleanup.app === app) return;
    container._texPreviewSyncCleanup?.();
    const panelItem = panel.dataset.layoutItem || fileEditorItemFor(path);
    const scroller = editor.scrollDOM;
    const ownerWindow = panel.ownerDocument.defaultView;
    let observedDoc = null;
    let matchesCompile = false;
    const usable = () => {
      if (!current() || !canNavigate() || app !== frame.contentWindow?.PDFViewerApplication
          || panel._cmView !== editor || editorViewModeFor(path, panelItem) !== 'split') return false;
      if (observedDoc !== editor.state.doc) {
        observedDoc = editor.state.doc;
        matchesCompile = observedDoc.toString() === text;
      }
      return matchesCompile;
    };
    const firstLineByPage = new Map();
    for (const {line, page} of anchors) {
      if (!firstLineByPage.has(page)) firstLineByPage.set(page, line);
    }
    let pendingScroll = 0;
    let lastTop = scroller.scrollTop;
    let lastCaret = editor.state.selection.main.head;
    let settingPdfPage = false;
    const pdfDocument = frame.contentDocument;
    const pdfNavigationEvents = ['wheel', 'pointerdown', 'keydown'];
    const cancelEditorScroll = () => {
      if (pendingScroll) ownerWindow.cancelAnimationFrame(pendingScroll);
      pendingScroll = 0;
    };
    const revealPdfPage = line => {
      if (!usable() || !Number.isFinite(line)) return;
      let first = 0;
      let end = anchors.length;
      while (first < end) {
        const middle = Math.floor((first + end) / 2);
        if (anchors[middle].line < line) first = middle + 1;
        else end = middle;
      }
      const before = anchors[first - 1];
      const after = anchors[first];
      const page = !after || before && line - before.line <= after.line - line ? before.page : after.page;
      if (page === app.pdfViewer.currentPageNumber) return;
      settingPdfPage = true;
      try {
        app.pdfViewer.currentPageNumber = page;
      } finally {
        settingPdfPage = false;
      }
    };
    const onEditorScroll = () => {
      const top = scroller.scrollTop;
      if (top === lastTop) return;
      lastTop = top;
      if (pendingScroll || !usable()) return;
      pendingScroll = ownerWindow.requestAnimationFrame(() => {
        pendingScroll = 0;
        const line = scroller.scrollTop === 0 ? 1 : fileEditorScrollAtBottom(scroller)
          ? editor.state.doc.lines : sourcePositionForEditorScroll(editor)?.line;
        revealPdfPage(line);
      });
    };
    const onCaretChange = () => {
      const head = editor.state.selection.main.head;
      if (head === lastCaret) return;
      lastCaret = head;
      if (!editor.hasFocus || !usable()) return;
      cancelEditorScroll();
      revealPdfPage(editor.state.doc.lineAt(head).number);
    };
    const onPdfPageChange = ({pageNumber}) => {
      if (settingPdfPage || !usable()) return;
      cancelEditorScroll();
      const line = firstLineByPage.get(pageNumber);
      if (line === undefined) return;
      const top = editorScrollTopForSourcePosition(editor, {line});
      if (top === null) return;
      writeOwnedElementScroll(scroller, {top}, 'tex-pdf-page');
      // A reflected scroll may clamp near EOF. Its scroll event must not change the PDF page.
      lastTop = scroller.scrollTop;
    };
    scroller.addEventListener('scroll', onEditorScroll, {passive: true});
    panel.ownerDocument.addEventListener('selectionchange', onCaretChange);
    app.eventBus.on('pagechanging', onPdfPageChange);
    for (const type of pdfNavigationEvents) pdfDocument.addEventListener(type, cancelEditorScroll, {capture: true, passive: true});
    const cleanup = () => {
      cancelEditorScroll();
      scroller.removeEventListener('scroll', onEditorScroll);
      panel.ownerDocument.removeEventListener('selectionchange', onCaretChange);
      app.eventBus.off('pagechanging', onPdfPageChange);
      for (const type of pdfNavigationEvents) pdfDocument.removeEventListener(type, cancelEditorScroll, true);
    };
    cleanup.app = app;
    cleanup.editor = editor;
    cleanup.frame = frame;
    container._texPreviewSyncCleanup = cleanup;
  };
  const compile = async () => {
    const result = await runWithProgress(t('preview.tex.compiling'), jobID => request('/api/tex/preview', {path, content: text, job_id: jobID}), true);
    if (!result) return;
    const {response, jobID} = result;
    try {
      if (!response.ok) await apiJsonResponse(response);
      if (!current()) {
        cleanupTexProgressJob(jobID);
        return;
      }
      if (!String(response.headers.get('Content-Type') || '').startsWith('application/pdf')) {
        cleanupTexProgressJob(jobID);
        const payload = await response.json();
        if (payload.code === 'compiler_missing') return missing(payload);
        const card = show(t('preview.tex.failed'), payload.error || t('common.requestFailed'), payload);
        if (card) button(card, t('common.retry'), check);
        return;
      }
      const blob = await response.blob();
      if (!current()) {
        cleanupTexProgressJob(jobID);
        return;
      }
      const objectUrl = URL.createObjectURL(blob);
      container._texPreviewSyncJobID = jobID;
      const query = new URLSearchParams({job_id: jobID, sync: '1'});
      const sourceMap = request(`/api/tex/compiler?${query}`).then(async response => {
        if (!response.ok) await apiJsonResponse(response);
        return (await response.json()).sync_pages || [];
      }).catch(error => {
        if (current()) console.warn('TeX source map unavailable', error);
        return [];
      });
      renderPdfPreviewInto(host, path, {
        sourceUrl: objectUrl, objectUrl, retainPrevious: true,
        onLoadError: message => {
          if (!current()) return;
          cleanupTexProgressJob(jobID);
          container._texPreviewSyncJobID = '';
          const card = show(t('preview.tex.failed'), message);
          if (card) button(card, t('common.retry'), check);
        },
        onDocumentReady: async ({frame, app, canNavigate}) => {
          host.dataset.texPreviewReady = 'true';
          const anchors = (await sourceMap).filter(({line, page}) => Number.isSafeInteger(line) && line > 0
            && Number.isSafeInteger(page) && page > 0 && page <= app.pagesCount);
          if (!current() || !frame.isConnected) return;
          container._texPreviewSyncBind = () => bindTexSplitScroll(frame, app, canNavigate, anchors);
          bindTexPreviewScrollSync(container.closest('.file-editor-panel'));
        },
      });
    } catch (error) {
      cleanupTexProgressJob(jobID);
      throw error;
    }
  };
  const missing = payload => {
    const card = show(t('preview.tex.missing'));
    if (!card) return;
    if (payload.install_supported !== false) {
      const installRow = document.createElement('div');
      installRow.className = 'tex-preview-install-row';
      card.append(installRow);
      button(installRow, t('preview.tex.install'), async () => {
        const result = await runWithProgress(t('preview.tex.installing'), jobID => request('/api/tex/compiler/install', {consent: true, job_id: jobID}));
        if (!result) return;
        const {response} = result;
        if (!response.ok) await apiJsonResponse(response);
        const installed = await response.json();
        if (!current()) return;
        if (installed.available) return compile();
        const failure = show(t('preview.tex.failed'), installed.error, installed);
        if (failure) button(failure, t('common.retry'), check);
      }, 'tex-preview-install-button');
    }
    if (payload.install_path) {
      const location = document.createElement('div');
      location.className = 'tex-preview-install-location';
      location.textContent = `${localizedLabel('preview.tex.installLocation', 'Shared install location')}: ${payload.install_path}`;
      card.append(location);
    }
    if (payload.instructions) {
      const manual = document.createElement('div');
      manual.className = 'tex-preview-manual-install';
      manual.append(document.createTextNode(`${localizedLabel('preview.tex.manualInstall', 'Or install manually')}: `));
      const command = document.createElement('code');
      command.textContent = payload.instructions;
      manual.append(command);
      card.append(manual);
    }
    button(card, t('common.retry'), check);
  };
  const check = async () => {
    show(t('common.loading'));
    const response = await request('/api/tex/compiler');
    if (!response.ok) await apiJsonResponse(response);
    const status = await response.json();
    instructions = status.instructions || '';
    if (!current()) return;
    if (!status.available) return missing(status);
    return compile();
  };
  const run = async action => {
    try { await action(); }
    catch (error) {
      if (!current()) return;
      const card = show(t('preview.tex.failed'), userMessageText(error, t('common.requestFailed')), {instructions});
      if (card) button(card, t('common.retry'), check);
    }
  };
  container._previewAsync = run(check);
  return true;
}

function previewFileActionLinks(path, {separator = ' · ', leadingSeparator = '', target = '_blank'} = {}) {
  if (!path) return [];
  const open = document.createElement('a');
  open.href = rawFileUrl(path);
  open.target = target;
  open.rel = 'noopener noreferrer';
  open.textContent = t('common.open');
  open.addEventListener('click', event => {
    event.preventDefault();
    void openRawFileInNewTab(path);
  });
  const download = document.createElement('a');
  download.href = rawFileDownloadUrl(path);
  download.textContent = t('common.download');
  download.addEventListener('click', event => {
    event.preventDefault();
    void triggerFileDownload(path);
  });
  return [
    ...(leadingSeparator ? [document.createTextNode(leadingSeparator)] : []),
    open,
    document.createTextNode(separator),
    download,
  ];
}

function previewActionFallbackNode(titleText, detailText, path) {
  const fallback = document.createElement('div');
  fallback.className = 'file-editor-preview-fallback';
  const title = document.createElement('div');
  title.className = 'file-editor-empty-title';
  title.textContent = titleText;
  const detail = document.createElement('div');
  detail.className = 'file-editor-empty-detail';
  detail.append(document.createTextNode(detailText || ''));
  detail.append(...previewFileActionLinks(path, {leadingSeparator: detailText ? ' · ' : ''}));
  fallback.append(title, detail);
  return fallback;
}

function scheduleRawMediaFallbackAfterUserScroll(container, media, key, createFallback) {
  const completion = schedulePreviewDeferredWorkAfterUserScroll(container, key, () => {
    if (!container.contains(media)) return false;
    releasePreviewSurfaceResources(container);
    container.replaceChildren(createFallback());
    return true;
  });
  trackPreviewAsyncCompletion(container, completion);
  return completion;
}

function renderNativeMediaPreviewInto(container, path, state = null, kind = 'audio', options = {}) {
  const media = document.createElement(kind === 'video' ? 'video' : 'audio');
  media.className = `file-editor-native-media file-editor-native-${kind}`;
  media.controls = true;
  media.preload = 'metadata';
  container.replaceChildren(media, previewActionFallbackNode(t(kind === 'video' ? 'preview.video.title' : 'preview.audio.title'), `${previewMimeForPath(path) || kind}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`, path));
  container._previewAsync = installRawFileMediaSource(media, path, {
    snapshot: options.snapshot === true,
    params: {v: rawFileMediaVersion(state)},
    isCurrent: () => container.contains(media),
    onFailure: error => scheduleRawMediaFallbackAfterUserScroll(container, media, 'raw-media-failure', () => (
      previewActionFallbackNode(
        t(kind === 'video' ? 'preview.video.loadFailed' : 'preview.audio.loadFailed'),
        userMessageText(error, t('common.requestFailed')),
        path,
      )
    )),
    onDecodeFailure: () => scheduleRawMediaFallbackAfterUserScroll(container, media, 'raw-media-failure', () => (
      previewActionFallbackNode(
        t(kind === 'video' ? 'preview.video.loadFailed' : 'preview.audio.loadFailed'),
        `${previewMimeForPath(path) || kind}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`,
        path,
      )
    )),
  });
}

function renderUnsupportedPreviewInto(container, path, state = null) {
  const renderer = previewRendererForPath(path, state);
  const title = renderer?.fallbackTitleKey ? t(renderer.fallbackTitleKey) : t('preview.unsupported.default');
  const label = state?.mime || previewMimeForPath(path) || state?.kind || t('preview.unsupported.file');
  container.replaceChildren(previewActionFallbackNode(title, `${label}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`, path));
}

async function openHtmlPreviewWithAuth(path) {
  const previewWindow = window.open('about:blank', '_blank');
  if (previewWindow) previewWindow.opener = null;
  try {
    const response = await apiFetch(htmlPreviewUrl(path));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const source = await response.text();
    const blobUrl = URL.createObjectURL(new Blob([source], {type: 'text/html'}));
    if (previewWindow) {
      previewWindow.location.href = blobUrl;
    } else {
      window.open(blobUrl, '_blank', 'noopener,noreferrer');
    }
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  } catch (error) {
    if (previewWindow) previewWindow.close();
    statusErr(localizedHtml('status.htmlPreviewFailed', {error}));
  }
}

function htmlViewEditRestriction(panel, path, state) {
  if (!fileEditorStateWritable(state)) return 'editor.htmlViewReadOnly';
  if (state.original?.includes('\r')) return 'editor.htmlViewReadOnlyLineEndings';
  if (panel._cmPath === path && panel._cmPlainFallback) return 'editor.htmlViewUnavailable';
  return '';
}

function htmlViewEditSourceEditor(panel, path, state) {
  return panel._cmPath === path && !htmlViewEditRestriction(panel, path, state) &&
    (!state._shareActive || panel._shareConnection) ? panel._cmView : null;
}

const htmlViewEditProblemLimit = 8;
const htmlViewEditProblemRepeatMs = 2000;

const htmlViewEditTraceLimit = 100;

// ViewEdit failures often show only as an ignored click. Each panel keeps a short history of what
// ViewEdit did, which "Copy details" in the status popover hands to whoever debugs it.
function traceHtmlViewEdit(panel, event, details = {}) {
  if (!panel) return;
  const trace = panel._htmlViewEditTrace ||= [];
  trace.push(`${diagnosticPacificWallTime(Date.now())} +${Math.round(performance.now())}ms ${event} ${JSON.stringify(details)}`);
  trace.splice(0, Math.max(0, trace.length - htmlViewEditTraceLimit));
}

function htmlViewEditNodeLabel(node) {
  if (!node) return '';
  if (node.nodeType === Node.TEXT_NODE) return `#text "${node.data.slice(0, 24)}"`;
  return `${node.tagName?.toLowerCase() || node.nodeName}${node.id ? `#${node.id}` : ''}${node.classList?.length ? `.${[...node.classList].join('.')}` : ''}`;
}

// A snapshot of everything ViewEdit depends on, then the recent history. Plain text for pasting.
function htmlViewEditDiagnostics(panel) {
  const path = fileEditorPanelPath(panel), state = fileEditorPanelState(panel), view = panel._cmView;
  const frame = htmlEditorPreviewFrame(panel), pane = fileEditorPanelPreviewPane(panel);
  const doc = frame?._htmlPreviewDocument;
  const map = view && frame && doc ? htmlSplitSourceMap(panel, frame, htmlSourceMapInputBudgetMs) : null;
  const head = view?.state.selection.main.head;
  const caret = map && Number.isInteger(head) ? htmlSplitCaretGeometry(frame, map, map.toSnapshot(head)) : null;
  const peers = (panel._htmlSharePeers?.peers || []).map(peer => {
    const marker = frame?._htmlShareCarets?.get(peer.clientID), box = marker?.getBoundingClientRect();
    return {name: peer.name, head: peer.head, anchor: peer.anchor, drawn: marker && !marker.hidden ? [Math.round(box.top), Math.round(box.left)] : null};
  });
  const facts = {
    path, mode: fileEditorPanelMode(panel), userAgent: navigator.userAgent,
    status: pane?.querySelector('[data-html-viewedit-status]')?.dataset.htmlVieweditStatus,
    writable: fileEditorStateWritable(state), dirty: Boolean(state?.dirty), readOnlyEditor: view?.state.readOnly,
    live: {active: Boolean(state?._shareActive), localOnly: Boolean(state?._shareLocalOnly), connected: Boolean(panel._shareConnection)},
    source: {length: view?.state.doc.length, head, focused: Boolean(view?.hasFocus)},
    page: {frames: pane?.querySelectorAll('.file-editor-html-preview').length, staged: Boolean(pane?._htmlStagedFrame),
      loaded: Boolean(doc) && doc === frame.contentDocument, sourceLength: frame?._htmlPreviewSource?.length,
      sameText: Boolean(view) && frame?._htmlPreviewSource === view.state.doc.toString(), elements: frame?._htmlPreviewElements?.length,
      receivesEvents: doc ? htmlPreviewReceivesEvents(doc) : null, scrollTop: Math.round(doc?.scrollingElement?.scrollTop || 0)},
    map: map ? {records: map.records.length, mapped: map.elements.size, foreign: map.foreign, missing: map.missing, editsSinceDrawn: Boolean(map.snapshot.changes), body: [map.body.from, map.body.to]} : null,
    caret: caret ? {top: Math.round(caret.position.top), left: Math.round(caret.position.left), element: htmlViewEditNodeLabel(caret.element)} : null,
    editing: {host: Boolean(frame?._htmlViewEditHost), hostConnected: Boolean(frame?._htmlViewEditHost?.isConnected),
      pageFocus: doc?.hasFocus?.() ?? null, active: htmlViewEditNodeLabel(doc?.activeElement), windowFocus: document.hasFocus()},
    peers,
  };
  return [`ViewEdit details ${diagnosticPacificWallTime(Date.now())}`, JSON.stringify(facts, null, 1),
    'Problems:', ...htmlViewEditProblemLines(panel), 'History:', ...(panel._htmlViewEditTrace || [])].join('\n');
}

// ViewEdit failures are silent by nature (an ignored click). Record why, in YO!Stats Logs and the status popover.
function reportHtmlViewEditProblem(panel, reason, details = {}, {flag = true} = {}) {
  if (!panel) return;
  traceHtmlViewEdit(panel, `problem: ${reason}`, details);
  const now = Date.now(), last = panel._htmlViewEditLastProblem;
  if (last?.reason === reason && now - last.at < htmlViewEditProblemRepeatMs) return;
  panel._htmlViewEditLastProblem = {reason, at: now};
  const message = `ViewEdit: ${reason} ${JSON.stringify({path: fileEditorPanelPath(panel), mode: fileEditorPanelMode(panel), ...details})}`;
  recordJsDebugEvent('viewedit', {category: 'viewedit', ok: !flag, message});
  if (!flag) return;
  const problems = panel._htmlViewEditProblems ||= [];
  problems.push(`${diagnosticPacificWallTime(now)} ${message}`);
  problems.splice(0, Math.max(0, problems.length - htmlViewEditProblemLimit));
  syncHtmlViewEditStatus(fileEditorPanelPreviewPane(panel));
}

function htmlViewEditProblemLines(panel) {
  const errors = jsDebugEvents.filter(event => event.type === 'error' || event.type === 'unhandledrejection')
    .slice(-htmlViewEditProblemLimit)
    .map(event => `${event.wallTime} JS ${event.type}: ${debugEventDetailText(event)}`);
  return [...errors, ...(panel?._htmlViewEditProblems || [])];
}

// The debug capture in 10_core_utils records the error first, since it registered earlier.
for (const type of ['error', 'unhandledrejection']) {
  window.addEventListener(type, () => {
    for (const panel of document.querySelectorAll('.file-editor-panel')) syncHtmlViewEditProblems(panel);
  });
}

function syncHtmlViewEditProblems(pane) {
  const list = pane?.querySelector('[data-html-viewedit-problems]');
  if (!list) return;
  const lines = htmlViewEditProblemLines(pane.closest('.file-editor-panel'));
  const text = lines.join('\n');
  pane.querySelector('[data-html-viewedit-info]')?.toggleAttribute('data-html-viewedit-problem', lines.length > 0);
  list.hidden = !lines.length;
  const log = list.querySelector('pre');
  if (log.textContent !== text) log.textContent = text;
}

function retryHtmlViewEditLoad(panel, pane) {
  delete pane._htmlViewEditLoad;
  renderFileEditorPanel(panel, fileEditorPanelItem(panel));
}

function syncHtmlViewEditStatus(pane) {
  const badge = pane?.querySelector('[data-html-viewedit-status]');
  if (!badge) return;
  syncHtmlViewEditProblems(pane);
  const panel = pane.closest('.file-editor-panel');
  const path = fileEditorPanelPath(panel), mode = fileEditorPanelMode(panel), state = fileEditorPanelState(panel);
  const frame = pane.querySelector('.file-editor-html-preview');
  const attempt = pane._htmlViewEditLoad;
  const restriction = htmlViewEditRestriction(panel, path, state);
  const failed = attempt?.path === path && attempt.mode === mode && attempt.failed;
  const ready = !restriction && !failed && frame?._htmlSplitViewGuide &&
    frame._htmlPreviewDocument === frame.contentDocument && htmlViewEditSourceEditor(panel, path, state);
  const status = restriction ? 'readonly' : failed ? 'unavailable' : ready ? 'ready' : 'loading';
  const label = restriction ? t(restriction) : failed ? t('editor.htmlViewUnavailable')
    : ready ? t('editor.htmlViewReady') : t('editor.htmlViewPreparing');
  if (badge.dataset.htmlVieweditStatus !== status) {
    badge.dataset.htmlVieweditStatus = status;
    if (ready) frame._wakeHtmlViewEditInput?.();
  }
  if (badge.textContent !== label) badge.textContent = label;
  const retry = pane.querySelector('[data-html-viewedit-retry]');
  retry.hidden = !failed || Boolean(restriction);
  const retryLabel = t('common.retry');
  if (retry.textContent !== retryLabel) retry.textContent = retryLabel;
}

function renderHtmlPreviewInto(container, path, text, stage = false) {
  let embeddedScrollPosition = previewEmbeddedScrollPosition(container);
  container._previewEmbeddedScrollPosition = embeddedScrollPosition;
  const children = [];
  const panel = container.closest('.file-editor-panel');
  const hasDisabledJavaScript = htmlPreviewHasDisabledJavaScript(text);
  const info = document.createElement('div');
  info.className = 'file-editor-html-extra-info';
  let problems = null, details = null;
  // Retry lives inside the status popover. Close it, so it does not cover the preview being retried.
  let closeStatusPopover = () => {};
  if (panel) {
    const badge = document.createElement('span');
    badge.dataset.htmlVieweditStatus = 'loading';
    badge.setAttribute('role', 'status');
    const retry = makeButton({label: t('common.retry'), onClick: event => {
      event.stopPropagation();
      closeStatusPopover();
      retryHtmlViewEditLoad(panel, container);
    }});
    retry.dataset.htmlVieweditRetry = '';
    retry.hidden = true;
    info.append(retry, document.createTextNode(' '), badge);
    problems = document.createElement('div');
    problems.dataset.htmlVieweditProblems = '';
    problems.hidden = true;
    const copy = makeButton({label: t('common.copy'), onClick: event => {
      event.stopPropagation();
      copyTextWithFeedback(problems.querySelector('pre').textContent, {statusText: t('debug.copied')});
    }});
    problems.append(document.createElement('pre'), copy);
    details = makeButton({label: t('editor.htmlViewCopyDetails'), onClick: event => {
      event.stopPropagation();
      copyTextWithFeedback(htmlViewEditDiagnostics(panel), {statusText: t('debug.copied')});
    }});
    details.dataset.htmlVieweditDetails = '';
    details.insertAdjacentHTML('afterbegin', fileContextMenuIcon('copy'));
  }
  if (hasDisabledJavaScript) {
    const link = document.createElement('a');
    link.href = htmlPreviewUrl(path);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.dataset.htmlPreviewAuth = '1';
    link.addEventListener('click', event => {
      event.preventDefault();
      openHtmlPreviewWithAuth(path);
    });
    link.textContent = t('preview.openWithJs');
    info.append(document.createTextNode(` ${t('preview.jsDisabled')} `), link);
  }
  // Keep the status sentence contiguous. Problems, when present, and the details button follow it.
  if (problems) info.append(problems, details);
  if (panel || hasDisabledJavaScript) {
    const status = document.createElement('div');
    status.className = 'file-editor-html-status';
    const popover = document.createElement('div');
    popover.className = 'file-editor-html-status-popover';
    const setOpen = open => {
      popover.hidden = !open;
      trigger.setAttribute('aria-expanded', String(open));
    };
    closeStatusPopover = () => setOpen(false);
    const dismiss = () => {
      setOpen(false);
      trigger.focus({preventScroll: true});
    };
    const trigger = makeButton({label: 'ⓘ', ariaLabel: t('preview.htmlTitle'),
      onClick: () => setOpen(true), events: {pointerenter: event => {
        if (event.pointerType === 'mouse') setOpen(true);
      }},
    });
    trigger.dataset.htmlVieweditInfo = '';
    const close = makeButton({label: '×', ariaLabel: t('common.close'), onClick: dismiss});
    close.dataset.htmlVieweditInfoClose = '';
    popover.append(info, close);
    status.append(trigger, popover);
    setOpen(false);
    status.addEventListener('pointerleave', event => {
      if (event.pointerType === 'mouse' && !status.contains(document.activeElement)) setOpen(false);
    });
    status.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || popover.hidden) return;
      event.stopPropagation();
      dismiss();
    });
    children.push(status);
  }
  const frame = document.createElement('iframe');
  frame.className = 'file-editor-html-preview';
  // The host reads the rendered document for link actions and scroll sync. Scripts remain disabled.
  frame.setAttribute('sandbox', 'allow-same-origin');
  frame.setAttribute('title', t('preview.htmlTitle'));
  frame._htmlPreviewSource = String(text ?? '').replace(/\r\n?/g, '\n');
  const sourceView = panel?._cmPath === path ? panel._cmView : null;
  // Start following edits now, so typing while the frame loads does not strand it.
  if (sourceView) htmlPreviewSourceSnapshot(frame, sourceView.state);
  frame.addEventListener('load', () => {
    if (frame.parentElement !== container) return;
    const frameDocument = frame.contentDocument;
    if (frameDocument?.URL !== 'about:srcdoc') return;
    if (container._htmlStagedFrame === frame) {
      const view = panel?._cmPath === path ? panel._cmView : null;
      if (view && !view.state.readOnly && !htmlPreviewSourceSnapshot(frame, view.state)) {
        traceHtmlViewEdit(panel, 'page dropped: source moved on while it loaded');
        retireStagedHtmlPreview(container);
        return;
      }
      embeddedScrollPosition = previewEmbeddedScrollPosition(container);
      rememberSharedHtmlPreviewSelection(container, path, frame._htmlPreviewSource, frame._htmlSourceSnapshot?.toSnapshot);
      delete container._htmlStagedFrame;
      releasePreviewSurfaceResources(container);
      // Reparenting an iframe navigates it again. Retire its siblings in place.
      for (const sibling of Array.from(container.children)) if (sibling !== frame) sibling.remove();
      for (const child of children) container.insertBefore(child, frame);
      frame.classList.remove('html-preview-staged-frame');
      frame.removeAttribute('aria-hidden');
      frame.removeAttribute('tabindex');
    }
    frame._disposeHtmlViewEdit?.();
    frame._disposeHtmlPreviewScroll?.();
    container._htmlPreviewLinks?.();
    container._htmlPreviewLinks = null;
    restorePreviewEmbeddedScrollPosition(container, frame, embeddedScrollPosition);
    frame._htmlPreviewDocument = frameDocument;
    const receivesEvents = frame._htmlPreviewDocument && htmlPreviewReceivesEvents(frameDocument);
    traceHtmlViewEdit(panel, 'page loaded', {length: frame._htmlPreviewSource.length, receivesEvents, linked: Boolean(frame._htmlSourceSnapshot)});
    if (frame._htmlPreviewDocument && !receivesEvents) {
      repaintWebKitHtmlPreview(frame);
      setTimeout(() => repaintWebKitHtmlPreview(frame), webKitHtmlPreviewSettleMs);
    }
    if (frame._htmlPreviewDocument) {
      // Capture the browser's parse before preview enhancements add elements.
      frame._htmlPreviewElements = Array.from(frameDocument.querySelectorAll('*'));
      frame._htmlPreviewElementKeys = frame._htmlPreviewElements.map(htmlElementAlignmentKey);
      container._htmlPreviewLinks = installHtmlPreviewLinks(container, frame, frameDocument, path, receivesEvents);
    }
    frame._htmlSplitMap = null;
    const mathStylesheet = document.querySelector('link[data-katex-stylesheet]');
    if (frameDocument && mathStylesheet && !frameDocument.querySelector('link[data-katex-stylesheet]')) {
      const stylesheet = frameDocument.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = mathStylesheet.href;
      stylesheet.dataset.katexStylesheet = '1';
      frameDocument.head?.append(stylesheet);
    }
    renderTexMathInElement(frameDocument?.body);
    if (panel && frame._htmlPreviewDocument) {
      installHtmlPreviewScrollSync(panel, frame, receivesEvents);
      installHtmlViewEdit(panel, path, frame, receivesEvents);
      panel._shareConnection?.syncPreviewInput();
      syncHtmlViewEditStatus(container);
      scheduleSplitEditorCaret(panel);
    }
  });
  frame.srcdoc = String(text ?? '');
  traceHtmlViewEdit(panel, 'page render', {staged: stage, length: frame._htmlPreviewSource.length, linked: Boolean(sourceView)});
  if (stage) {
    frame.classList.add('html-preview-staged-frame');
    frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1;
    container._htmlStagedFrame = frame;
    container.append(frame);
  } else container.replaceChildren(...children, frame);
  syncHtmlViewEditStatus(container);
}

// Safari can leave part of a srcdoc frame unpainted after it is shown, scrolled, or edited, until
// the frame is resized or scrolled. A one-pixel resize alone does not always repaint a scrolled
// page, so also scroll it by one pixel, then put both back on the next frame. Requests made while
// one is pending share it.
function repaintWebKitHtmlPreview(frame) {
  if (frame._webKitRepaintPending) return;
  frame._webKitRepaintPending = true;
  requestAnimationFrame(() => {
    if (!frame.isConnected) { frame._webKitRepaintPending = false; return; }
    const scroller = previewEmbeddedScrollElement(frame);
    const top = scroller ? scroller.scrollTop : 0;
    const nudged = scroller ? (top > 0 ? top - 1 : top + 1) : top;
    frame.style.setProperty('width', 'calc(100% - 1px)');
    if (scroller) writeOwnedElementScroll(scroller, {top: nudged}, 'webkit-html-repaint');
    requestAnimationFrame(() => {
      frame.style.removeProperty('width');
      // A user scroll in between wins.
      if (scroller && scroller.scrollTop === nudged) writeOwnedElementScroll(scroller, {top}, 'webkit-html-repaint');
      frame._webKitRepaintPending = false;
    });
  });
}

// Scroll restoration and late styles can move a new page after its first repaint.
const webKitHtmlPreviewSettleMs = 300;

function htmlSplitPreviewFrame(panel) {
  if (!panel || fileEditorPanelMode(panel) !== 'split') return null;
  return htmlEditorPreviewFrame(panel);
}

function htmlEditorPreviewFrame(panel) {
  const pane = fileEditorPanelPreviewPane(panel);
  const frame = pane && !pane.hidden && pane.querySelector('.file-editor-html-preview');
  return frame?._htmlPreviewDocument && frame.contentDocument === frame._htmlPreviewDocument ? frame : null;
}

function installHtmlPreviewScrollSync(panel, frame, receivesEvents) {
  const doc = frame._htmlPreviewDocument;
  const isCurrentSplit = () => htmlSplitPreviewFrame(panel) === frame;
  const sync = (rootScroll, observedPreviewScroll = false) => {
    if (htmlEditorPreviewFrame(panel) !== frame) return;
    if (rootScroll && isCurrentSplit()) {
      const source = observedPreviewScroll ? 'preview' : fileEditorPreviewScrollSyncSource(panel);
      scheduleFileEditorSplitScrollSync(panel, source, {observedPreviewScroll});
    }
    scheduleSplitEditorCaret(panel);
  };
  if (receivesEvents) {
    doc.addEventListener('scroll', event => sync(event.target === doc), {capture: true, passive: true});
    doc.defaultView.addEventListener('resize', () => sync(false));
    return;
  }
  const scroller = previewEmbeddedScrollElement(frame);
  if (!scroller) return;
  const size = () => [scroller.clientWidth, scroller.clientHeight];
  let position = ownedElementScrollPosition(scroller);
  let [width, height] = size();
  let pending = 0;
  const sample = () => {
    pending = 0;
    if (!frame.isConnected || htmlEditorPreviewFrame(panel) !== frame) return;
    const next = ownedElementScrollPosition(scroller);
    const [nextWidth, nextHeight] = size();
    const resized = width !== nextWidth || height !== nextHeight;
    // The repaint nudge is not a scroll the source should follow, so compare against the position before it.
    const nudging = frame._webKitRepaintPending;
    const moved = !nudging && (next.top !== position.top || next.left !== position.left);
    if (!nudging) position = next;
    width = nextWidth;
    height = nextHeight;
    if (moved || resized) sync(moved, moved && !resized);
    pending = requestAnimationFrame(sample);
  };
  // WebKit scrolls the sandbox natively even when it suppresses its scroll events.
  // Sample only while the current preview is visible, since wheel input need not focus it.
  const observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) {
      if (!pending) pending = requestAnimationFrame(sample);
    } else {
      cancelAnimationFrame(pending);
      pending = 0;
      position = ownedElementScrollPosition(scroller);
      [width, height] = size();
    }
  });
  observer.observe(frame);
  frame._disposeHtmlPreviewScroll = () => {
    observer.disconnect();
    cancelAnimationFrame(pending);
    delete frame._disposeHtmlPreviewScroll;
  };
}

const htmlElementAlignmentMaxEdits = 256;

// Tag names alone would pair a removed paragraph's neighbour with the wrong paragraph, so an
// element's alignment key also carries its id, class, and own text. Rendered keys are read when the
// page loads, before ViewEdit hosts or math rendering change the page.
function htmlElementAlignmentKey(element) {
  let text = '';
  for (const child of element.childNodes) {
    // A ViewEdit editing host holds the element's own text while it is edited.
    if (child.nodeType === 3 || child.matches?.('[data-html-viewedit]')) text += child.textContent;
  }
  return [element.namespaceURI, element.localName, element.id, element.getAttribute('class') || '',
    text.replace(/\s+/g, ' ').trim().slice(0, 64)].join('\0');
}

// In-place ViewEdit typing changes an element's own text without reloading the page.
function refreshHtmlPreviewElementKey(frame, element) {
  const index = frame._htmlPreviewElements.indexOf(element);
  if (index >= 0) frame._htmlPreviewElementKeys[index] = htmlElementAlignmentKey(element);
}

// Pair two key sequences the way a text diff pairs lines (Myers' algorithm). Returns, per `a`
// index, its `b` index or -1, or null when they differ too much.
function htmlElementAlignment(a, b) {
  const n = a.length, m = b.length, max = Math.min(n + m, htmlElementAlignmentMaxEdits), offset = max + 1;
  const v = new Int32Array(2 * max + 3), trace = [];
  for (let d = 0; d <= max; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]) ? v[offset + k + 1] : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      v[offset + k] = x;
      if (x < n || y < m) continue;
      const pairs = new Int32Array(n).fill(-1);
      for (let step = d; step > 0; step--) {
        const previous = trace[step], diagonal = x - y;
        const fromK = diagonal === -step || (diagonal !== step && previous[offset + diagonal - 1] < previous[offset + diagonal + 1])
          ? diagonal + 1 : diagonal - 1;
        const fromX = previous[offset + fromK], fromY = fromX - fromK;
        while (x > fromX && y > fromY) pairs[--x] = --y;
        x = fromX;
        y = fromY;
      }
      while (x > 0 && y > 0) pairs[--x] = --y;
      return pairs;
    }
  }
  return null;
}

function htmlSplitMappedElements(source, tree, rendered, renderedKeys) {
  let attribute = 'data-yomux-source-index';
  const lowerSource = source.toLowerCase();
  while (lowerSource.includes(attribute)) attribute += '-';
  const syntaxNodes = [], parts = [];
  let copied = 0;
  tree.iterate({enter(node) {
    if (node.name === 'Element') {
      const syntax = node.node;
      const tag = syntax.firstChild?.getChild('TagName');
      if (!tag) return;
      parts.push(source.slice(copied, tag.to), ` ${attribute}="${syntaxNodes.length}"`);
      copied = tag.to;
      syntaxNodes.push(syntax);
    } else if (node.name === 'AttributeName' &&
        /^(src|srcset|srcdoc|imagesrcset|href|xlink:href|poster|data|background)$/i.test(source.slice(node.from, node.to))) {
      // Only the displayed iframe should load document resources.
      parts.push(source.slice(copied, node.from), `${attribute}-resource`);
      copied = node.to;
    }
  }});
  parts.push(source.slice(copied));
  // Parse a detached copy so the same HTML tree builder can insert, move, or ignore tags.
  const indexed = new DOMParser().parseFromString(parts.join(''), 'text/html').querySelectorAll('*');
  // Browser extensions can add, wrap, or remove elements on a same-origin page. Pair the rest, so a
  // change costs only the elements it touched instead of the whole page.
  const pairs = htmlElementAlignment(Array.from(indexed, htmlElementAlignmentKey), renderedKeys);
  if (!pairs) return Object.assign([], {foreign: Array.from(rendered), missing: indexed.length});
  const used = new Set(pairs);
  const foreign = Array.from(rendered).filter((_element, index) => !used.has(index));
  const matched = Array.from(pairs, index => index < 0 ? undefined : rendered[index]);
  const elements = new Map();
  const ambiguous = new Set();
  indexed.forEach((element, index) => {
    const key = element.getAttribute(attribute);
    // The browser can clone formatting elements when it repairs misnested tags.
    if (key === null) return;
    if (elements.has(key)) ambiguous.add(key);
    else elements.set(key, matched[index]);
  });
  return Object.assign(syntaxNodes.map((syntax, index) => {
    const key = String(index);
    return {syntax, element: elements.get(key), exact: !ambiguous.has(key)};
  }), {foreign, missing: pairs.filter(index => index < 0).length});
}

// The editor state whose text the frame rendered, plus the edits made since then.
function htmlPreviewSourceSnapshot(frame, state) {
  let snapshot = frame._htmlSourceSnapshot;
  if (snapshot?.doc === state.doc) return snapshot;
  // Edits are only composed while their chain is unbroken. Otherwise the text must match again.
  const source = frame._htmlPreviewSource;
  if (state.doc.length !== source.length || state.doc.toString() !== source) return null;
  snapshot = frame._htmlSourceSnapshot = {
    state, doc: state.doc, changes: null,
    toCurrent: (pos, assoc = -1) => snapshot.changes ? snapshot.changes.mapPos(pos, assoc) : pos,
    toSnapshot: (pos, assoc = -1) => snapshot.changes ? snapshot.changes.invertedDesc.mapPos(pos, assoc) : pos,
  };
  return snapshot;
}

function mapHtmlPreviewSources(panel, transaction) {
  for (const frame of fileEditorPanelPreviewPane(panel)?.querySelectorAll('.file-editor-html-preview') || []) {
    const snapshot = frame._htmlSourceSnapshot;
    if (snapshot?.doc !== transaction.startState.doc) continue;
    snapshot.changes = snapshot.changes ? snapshot.changes.compose(transaction.changes) : transaction.changes;
    snapshot.doc = transaction.newDoc;
    // Text that returns to the rendered source maps one to one again, even after a whole-document replace.
    const source = frame._htmlPreviewSource;
    if (snapshot.doc.length === source.length && snapshot.doc.toString() === source) snapshot.changes = null;
  }
}

// Repaints retry on the next frame, so they parse briefly. Input must not lose a click to a busy CPU.
const htmlSourceMapPaintBudgetMs = 20;
const htmlSourceMapInputBudgetMs = 500;

function htmlSplitSourceMap(panel, frame, budgetMs = htmlSourceMapPaintBudgetMs) {
  const view = panel._cmView;
  if (!view) return null;
  // Geometry belongs to the loaded snapshot. Offsets are mapped through edits until the next iframe loads.
  const snapshot = htmlPreviewSourceSnapshot(frame, view.state);
  if (!snapshot) {
    reportHtmlViewEditProblem(panel, 'lost the link between the page and the source', {
      docLength: view.state.doc.length, pageSourceLength: frame._htmlPreviewSource.length,
      chain: Boolean(frame._htmlSourceSnapshot), pageLoaded: frame._htmlPreviewDocument === frame.contentDocument,
    });
    return null;
  }
  if (frame._htmlSplitMap?.snapshot === snapshot) return frame._htmlSplitMap;
  const {state} = snapshot;
  const tree = panel._cmApi.ensureSyntaxTree(state, state.doc.length, budgetMs);
  if (!tree) {
    if (budgetMs >= htmlSourceMapInputBudgetMs) {
      reportHtmlViewEditProblem(panel, 'source parse did not finish in time', {docLength: state.doc.length, budgetMs});
    }
    return null;
  }
  const source = frame._htmlPreviewSource;
  const doc = frame._htmlPreviewDocument;
  const records = [];
  let bodySyntax = tree.topNode;
  let body = null;
  const mapped = htmlSplitMappedElements(source, tree, frame._htmlPreviewElements, frame._htmlPreviewElementKeys);
  if ((mapped.foreign.length || mapped.missing) && !frame._htmlForeignElementsTraced) {
    frame._htmlForeignElementsTraced = true;
    traceHtmlViewEdit(panel, 'page differs from the source', {added: mapped.foreign.slice(0, 8).map(htmlViewEditNodeLabel),
      missing: mapped.missing, matched: mapped.length > 0});
  }
  for (const {syntax, element, exact} of mapped) {
    if (element === doc.documentElement) bodySyntax = syntax;
    if (!element || !doc.body.contains(element)) continue;
    const record = {syntax, element, exact, from: syntax.from, to: syntax.to};
    if (element === doc.body) body = record;
    records.push(record);
  }
  if (!body) {
    // With an implied body, its direct text is written under <html> or the document root.
    body = {syntax: bodySyntax, element: doc.body, exact: true, from: 0, to: source.length};
    records.unshift(body);
  }
  const elements = new Map(records.filter(record => record !== body).map(record => [record.from, record.element]));
  const {toCurrent, toSnapshot} = snapshot;
  return (frame._htmlSplitMap = {snapshot, tree, records, body, elements, toCurrent, toSnapshot, foreign: mapped.foreign.length, missing: mapped.missing});
}

// Moves a rendered run into current source coordinates. Returns null when its source text changed.
function htmlPreviewCurrentTarget(map, doc, target) {
  const {changes} = map.snapshot;
  if (!changes) return target;
  if (!target.raw) return changes.touchesRange(target.from) ? null : {...target, from: changes.mapPos(target.from)};
  const from = changes.mapPos(target.from, 1), to = changes.mapPos(target.from + target.raw.length, -1);
  return to - from === target.raw.length && doc.sliceString(from, to) === target.raw ? {...target, from} : null;
}

function htmlSplitTextNodeCaretRect(text, offset) {
  const range = text.ownerDocument.createRange();
  range.setStart(text, offset);
  range.collapse(true);
  const rect = range.getBoundingClientRect();
  return rect.height > 0 ? rect : null;
}

function htmlSplitBoundaryCaretRect(node, forward) {
  const sibling = forward ? 'nextSibling' : 'previousSibling';
  const child = forward ? 'firstChild' : 'lastChild';
  for (; node; node = node[sibling]) {
    if (node.nodeType === 3 && node.data.trim()) {
      const rect = htmlSplitTextNodeCaretRect(node, forward ? 0 : node.length);
      if (rect) return rect;
    }
    if (node.nodeType !== 1 || !previewSourceAnchorIsRendered(node) ||
        node.ownerDocument.defaultView.getComputedStyle(node).visibility !== 'visible') continue;
    const rect = htmlSplitBoundaryCaretRect(node[child], forward) || node.getClientRects()[0];
    if (rect?.height > 0) return rect;
  }
  return null;
}

function htmlSourceTextToken(token) {
  return /^(Text|EntityReference|CharacterReference|InvalidEntity)$/.test(token?.name);
}

function* htmlMappedTextRuns(record, map, source, editingHost = null) {
  let next = record.element.firstChild;
  for (let token = record.syntax.firstChild; token; token = token.nextSibling) {
    if (token.name === 'Element') {
      const element = map.elements.get(token.from);
      if (element?.parentNode === record.element) next = element.nextSibling;
      continue;
    }
    if (token.name === 'Comment') {
      const data = source.slice(token.from, token.to).slice(4).replace(/--!?>$/, '');
      let comment = next;
      while (comment?.nodeType === 3 || (comment && comment === editingHost)) comment = comment.nextSibling;
      if (comment?.nodeType === 8 && comment.data === data) {
        next = comment.nextSibling;
        yield {node: comment, from: token.from, to: token.to};
      }
      continue;
    }
    if (!htmlSourceTextToken(token)) continue;
    const from = token.from;
    while (htmlSourceTextToken(token.nextSibling) && token.to === token.nextSibling.from) token = token.nextSibling;
    // Consume a single DOM run. A mismatch must not search past a comment for duplicate text.
    const node = next && next === editingHost ? next.firstChild : next;
    if (node?.nodeType !== 3) continue;
    next = next.nextSibling;
    yield {node, from, to: token.to, raw: source.slice(from, token.to)};
  }
}

function htmlSplitTextCaretRect(frame, map, record, offset) {
  for (const {node, from, to, raw} of htmlMappedTextRuns(record, map, frame._htmlPreviewSource, frame._htmlViewEditHost)) {
    if (offset < from) break;
    if (node.nodeType === 8) {
      if (offset < to) {
        return htmlSplitBoundaryCaretRect(node.nextSibling, true) || htmlSplitBoundaryCaretRect(node.previousSibling, false);
      }
      continue;
    }
    if (offset > to) continue;
    const offsets = htmlViewEditTextOffsets(node.ownerDocument, raw, node.data);
    if (offsets === false) continue;
    const relative = offset - from;
    const rendered = offsets ? offsets.findLastIndex(position => position <= relative) : relative;
    const rect = htmlSplitTextNodeCaretRect(node, rendered);
    if (rect) return rect;
  }
  return null;
}

function htmlSplitCaretClip(element, viewport = null) {
  const clip = viewport ? {left: 0, top: 0, right: viewport.width, bottom: viewport.height}
    : {left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity};
  const doc = element.ownerDocument;
  for (; element && element !== doc.body && element !== doc.documentElement; element = element.parentElement) {
    const style = doc.defaultView.getComputedStyle(element);
    if (style.display === 'inline' || style.display === 'contents' ||
        (style.overflowX === 'visible' && style.overflowY === 'visible')) continue;
    const rect = element.getBoundingClientRect();
    if (style.overflowX !== 'visible') {
      const left = rect.left + element.clientLeft;
      clip.left = Math.max(clip.left, left);
      clip.right = Math.min(clip.right, left + element.clientWidth);
    }
    if (style.overflowY !== 'visible') {
      const top = rect.top + element.clientTop;
      clip.top = Math.max(clip.top, top);
      clip.bottom = Math.min(clip.bottom, top + element.clientHeight);
    }
  }
  return clip;
}

function htmlSplitCaretOutsideClip(position, end, clip) {
  return position.left < clip.left || position.left > clip.right || end <= clip.top || position.top >= clip.bottom;
}

function paintHtmlSplitSelection(frame, map, selections, layerClass) {
  const pane = frame.parentElement;
  let layer = pane.querySelector(`.${layerClass}`);
  if (!selections.length) {
    layer?.remove();
    return;
  }
  const viewport = frame.getBoundingClientRect();
  const domRange = frame.contentDocument.createRange();
  const rectangles = [];
  const paint = (element, rects, peer) => {
    const clip = htmlSplitCaretClip(element, viewport);
    for (const rect of rects) {
      const left = Math.max(clip.left, rect.left), right = Math.min(clip.right, rect.right);
      const top = Math.max(clip.top, rect.top), bottom = Math.min(clip.bottom, rect.bottom);
      if (right > left && bottom > top) rectangles.push({left, top, width: right - left, height: bottom - top, peer});
    }
  };
  const targets = htmlViewEditTextTargets(frame, map, frame._htmlViewEditHost, {selection: true});
  for (const selection of selections) {
    for (const {node, from, raw, offsets} of targets.values()) {
      const start = Math.max(0, selection.from - from), end = Math.min(raw.length, selection.to - from);
      if (start >= end) continue;
      domRange.setStart(node, offsets ? offsets.findLastIndex(offset => offset <= start) : start);
      domRange.setEnd(node, offsets ? offsets.findIndex(offset => offset >= end) : end);
      paint(node.parentElement, domRange.getClientRects(), selection.peer);
    }
    for (const {element, exact, from, to} of map.records) {
      if (exact && from < selection.to && to > selection.from && element.matches(htmlViewEditGraphic) &&
          previewSourceAnchorIsRendered(element) && frame.contentWindow.getComputedStyle(element).visibility === 'visible') {
        paint(element, element.getClientRects(), selection.peer);
      }
    }
  }
  if (!layer) {
    layer = document.createElement('div');
    layer.className = `html-split-selection-layer ${layerClass}`;
    layer.setAttribute('aria-hidden', 'true');
    pane.append(layer);
  }
  layer.replaceChildren(...rectangles.map(rect => {
    const marker = document.createElement('span');
    marker.className = rect.peer ? 'share-remote-selection' : 'split-editor-selection';
    if (rect.peer) marker.setAttribute('style', shareParticipantStyle(rect.peer));
    Object.assign(marker.style, {
      left: `${viewport.left + rect.left}px`, top: `${viewport.top + rect.top}px`,
      width: `${rect.width}px`, height: `${rect.height}px`,
    });
    return marker;
  }));
}

function htmlSplitCaretGeometry(frame, map, offset) {
  const visible = record => previewSourceAnchorIsRendered(record.element)
    && frame.contentWindow.getComputedStyle(record.element).visibility === 'visible';
  const enclosing = map.records.findLast(entry => entry.from <= offset && offset < entry.to &&
    entry !== map.body && visible(entry));
  const textOwner = enclosing || map.body;
  const textPosition = visible(textOwner) ? htmlSplitTextCaretRect(frame, map, textOwner, offset) : null;
  const fallback = textPosition ? null : enclosing
    || map.records.findLast(entry => entry.from <= offset && visible(entry))
    || map.records.find(visible);
  const position = textPosition || fallback?.element.getClientRects()[0];
  const viewport = frame.getBoundingClientRect();
  if (!position || viewport.width <= 0 || viewport.height <= 0) return null;
  const height = Math.min(viewport.height, Math.max(2, Math.min(18, position.height)));
  // Container tags map to their start, even when the element spans the whole page.
  const end = textPosition ? position.bottom : position.top + height;
  return {position, viewport, height, end, element: textPosition ? textOwner.element : fallback.element};
}

function paintHtmlSplitCaret(panel, frame, reveal = false) {
  const container = frame.parentElement;
  let marker = container.querySelector('.html-split-caret.split-editor-caret');
  if (marker) marker.hidden = true;
  const view = panel._cmView;
  const map = panel.isConnected && view?.hasFocus ? htmlSplitSourceMap(panel, frame) : null;
  const selection = map ? view.state.selection.main : null;
  const head = selection && map.toSnapshot(selection.head);
  const caret = selection && (selection.empty || reveal) ? htmlSplitCaretGeometry(frame, map, head) : null;
  if (reveal && view?.hasFocus && !caret) {
    traceHtmlViewEdit(panel, 'source caret not found on the page', {head: selection?.head, map: Boolean(map)});
  }
  if (reveal && caret && !htmlSplitCaretOutsideClip(caret.position, caret.end, htmlSplitCaretClip(caret.element))) {
    const {position, viewport, height, end} = caret;
    const source = view.coordsAtPos(selection.head, selection.head === selection.from ? 1 : -1);
    const targetTop = source
      ? Math.max(0, Math.min(viewport.height - height, source.top - viewport.top))
      : viewport.height * fileEditorHtmlSplitScrollFocusRatio;
    if ((source || end <= 0 || position.top >= viewport.height) &&
        revealSplitCaret(panel, previewEmbeddedScrollElement(frame), position.top, targetTop)) {
      paintHtmlSplitCaret(panel, frame);
      return;
    }
  }
  const selected = selection && !selection.empty
    ? [{from: map.toSnapshot(selection.from, 1), to: map.toSnapshot(selection.to)}] : [];
  paintHtmlSplitSelection(frame, map, selected, 'html-local-selection');
  if (!selection?.empty || !caret) return;
  if (!marker) {
    marker = createSplitEditorCaret();
    marker.classList.add('html-split-caret');
    container.append(marker);
  }
  positionHtmlPreviewCaret(marker, map, head, caret, true);
}

function positionHtmlPreviewCaret(marker, map, offset, caret, pinComment = false) {
  const {position, viewport, height, end, element} = caret;
  const inComment = pinComment && map.tree.resolve(offset, 1).name === 'Comment';
  const clip = !inComment && htmlSplitCaretClip(element, viewport);
  if (clip && htmlSplitCaretOutsideClip(position, end, clip)) return;
  const top = inComment ? Math.max(0, Math.min(position.top, viewport.height - height)) : Math.max(clip.top, position.top);
  const left = inComment ? Math.max(0, Math.min(position.left, viewport.width - 2)) : position.left;
  const bottom = inComment ? top + height : Math.min(clip.bottom, end);
  Object.assign(marker.style, {
    left: `${viewport.left + left}px`,
    top: `${viewport.top + top}px`,
    height: `${bottom - top}px`,
  });
  marker.hidden = false;
}

function paintSharedHtmlPresence(panel, frame) {
  const cached = panel._htmlSharePeers;
  const map = cached?.peers.length && cached.doc === panel._cmView?.state.doc ? htmlSplitSourceMap(panel, frame) : null;
  const pane = frame.parentElement;
  const markers = frame._htmlShareCarets ||= new Map();
  const peers = map ? cached.peers : [];
  const ids = new Set(peers.map(peer => peer.clientID));
  for (const [id, marker] of markers) {
    if (ids.has(id)) continue;
    marker.remove();
    markers.delete(id);
  }
  const selections = [];
  for (const peer of peers) {
    const anchor = map.toSnapshot(peer.anchor), head = map.toSnapshot(peer.head);
    if (anchor !== head && !peer.approximate) {
      selections.push({from: Math.min(anchor, head), to: Math.max(anchor, head), peer});
    }
    let marker = markers.get(peer.clientID);
    if (!marker) {
      marker = document.createElement('span');
      marker.className = 'html-split-caret share-rich-caret';
      marker.setAttribute('aria-hidden', 'true');
      markers.set(peer.clientID, marker);
      pane.append(marker);
    }
    marker.hidden = true;
    marker.style.cssText = shareParticipantStyle(peer);
    marker.dataset.shareCaretName = peer.name;
    marker.toggleAttribute('data-share-caret-follower', peer.follower);
    marker.toggleAttribute('data-share-caret-primary', !peer.follower);
    // A peer caret outside <body> (for example a fresh page at offset 0) has no place on the page.
    // The geometry fallback would put it at the top-left, which reads as a real position.
    const caret = map.body.from <= head && head < map.body.to ? htmlSplitCaretGeometry(frame, map, head) : null;
    if (caret) {
      marker._htmlCaretElement = caret.element;
      positionHtmlPreviewCaret(marker, map, head, caret);
    }
  }
  paintHtmlSplitSelection(frame, map, selections, 'html-shared-selection');
  if (peers.length) refreshLegacyShareCaretColors?.();
}

const htmlViewEditElements = 'address,article,aside,b,blockquote,caption,code,dd,div,dt,em,figcaption,h1,h2,h3,h4,h5,h6,i,li,p,pre,section,span,strong,td,th,u';
const htmlViewEditProtected = 'a[href],button,canvas,iframe,input,math,noembed,noframes,noscript,option,plaintext,script,select,style,svg,template,textarea,title,xmp,[contenteditable],[hidden],[inert]';
const htmlViewEditGraphic = 'img,svg';

function htmlViewEditTextOffsets(doc, raw, rendered) {
  if (raw === rendered && !raw.includes('&')) return null;
  const decoder = doc.createElement('textarea');
  const positions = [0];
  let text = '', end = 0;
  for (const entity of raw.matchAll(/&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]*);?/gi)) {
    const spelling = entity[0];
    decoder.innerHTML = spelling;
    const decoded = decoder.value;
    if (decoded === spelling) continue;
    // Legacy named references may decode only a prefix, as in &copycat → ©cat.
    let suffix = 0;
    while (suffix < decoded.length - 1 &&
        spelling.at(-suffix - 1) === decoded.at(-suffix - 1)) suffix++;
    for (; end < entity.index; end++) { text += raw[end]; positions.push(end + 1); }
    const value = decoded.slice(0, decoded.length - suffix);
    end += spelling.length - suffix;
    text += value;
    for (let index = 1; index < value.length; index++) positions.push(undefined);
    positions.push(end);
  }
  for (; end < raw.length; end++) { text += raw[end]; positions.push(end + 1); }
  return text === rendered && htmlDecodedText(decoder, raw) === rendered ? positions : false;
}

// The text a run of HTML source renders as. Escaping '<' keeps any markup as literal text.
function htmlDecodedText(decoder, raw) {
  decoder.innerHTML = raw.replaceAll('<', '&lt;');
  return decoder.value;
}

function htmlViewEditTextTargets(frame, map, editingHost = null, {selection = false} = {}) {
  const targets = new Map();
  const source = frame._htmlPreviewSource;
  for (const record of map.records) {
    const element = record.element;
    if (!record.exact || (!selection && element.closest(htmlViewEditProtected))) continue;
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    if (style.visibility !== 'visible' || (!previewSourceAnchorIsRendered(element) &&
        !(selection && style.display === 'contents' && !elementHiddenByClosedDetails(element)))) continue;
    for (const {node, from, raw} of htmlMappedTextRuns(record, map, source, editingHost)) {
      if (node.nodeType !== 3) continue;
      const offsets = htmlViewEditTextOffsets(node.ownerDocument, raw, node.data);
      if (offsets !== false) targets.set(node, {node, from, raw, offsets});
    }
  }
  return targets;
}

function htmlViewEditPatch(target, from, to, value, preformatted) {
  const sourceFrom = target.offsets ? target.offsets[from] : from;
  const sourceTo = target.offsets ? target.offsets[to] : to;
  if (!Number.isInteger(sourceFrom) || !Number.isInteger(sourceTo)) return null;
  const text = value.replace(/\r\n?/g, '\n').replaceAll('\0', '\ufffd');
  let insert = text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const structural = target.wrap || (!preformatted && text.includes('\n'));
  if (!preformatted) insert = insert.replaceAll('\n', '<br>');
  const caret = target.from + sourceFrom + insert.length + (target.wrap ? 3 : 0);
  if (target.wrap) insert = `<p>${insert}</p>`;
  return {changes: {from: target.from + sourceFrom, to: target.from + sourceTo, insert}, caret, structural, text};
}

function htmlViewEditDeletionBoundary(text, offset, backward, sourceOffsets) {
  const boundaries = [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(text)]
    .map(item => item.index).concat(text.length)
    .filter(position => !sourceOffsets || Number.isInteger(sourceOffsets[position]));
  return (backward ? boundaries.findLast(position => position < offset) : boundaries.find(position => position > offset)) ?? offset;
}

function clearHtmlViewEditRestore(panel, restore = panel._htmlViewEditRestore) {
  if (!restore || panel._htmlViewEditRestore !== restore) return;
  delete panel._htmlViewEditRestore;
  restore.events.abort();
  clearTimeout(restore.timeout);
}

function rememberHtmlViewEditRestore(panel, path, source, {anchor, head}) {
  clearHtmlViewEditRestore(panel);
  const restore = {source, anchor, head, path, events: new AbortController()};
  const cancel = event => {
    if (event.isTrusted) clearHtmlViewEditRestore(panel, restore);
  };
  for (const type of ['pointerdown', 'keydown']) {
    document.addEventListener(type, cancel, {capture: true, signal: restore.events.signal});
  }
  restore.timeout = setTimeout(() => clearHtmlViewEditRestore(panel, restore), 1000);
  panel._htmlViewEditRestore = restore;
}

function htmlPreviewReceivesEvents(doc) {
  const event = new Event('yomux-preview-probe');
  let received = false;
  const listener = () => { received = true; };
  doc.addEventListener(event.type, listener);
  doc.dispatchEvent(event);
  doc.removeEventListener(event.type, listener);
  return received;
}

function installHtmlViewEdit(panel, path, frame, receivesEvents) {
  const doc = frame._htmlPreviewDocument, pane = frame.parentElement;
  // WebKit suppresses callbacks in script-disabled documents, even for listeners installed
  // by their same-origin parent. Keep trusted native input in the application document.
  const input = receivesEvents ? null : document.createElement('span');
  const inputSurface = input || doc;
  const inputDocument = inputSurface.ownerDocument || doc;
  let caret = null;
  const inputSourceMap = () => htmlSplitSourceMap(panel, frame, htmlSourceMapInputBudgetMs);
  let active = null, composition = null, applyingOwnEdit = false;
  let verticalCaretX = null;
  const documentView = () => {
    const state = fileEditorPanelState(panel), mode = fileEditorPanelMode(panel);
    return panel.isConnected && pane.contains(frame) && frame.contentDocument === doc &&
      ['split', 'preview'].includes(mode) ? htmlViewEditSourceEditor(panel, path, state) : null;
  };
  const sourceOffset = (target, offset) => target.from + (target.offsets ? target.offsets[offset] : offset);
  const selectSource = (view, from, to) => {
    if (Number.isInteger(from) && Number.isInteger(to) &&
        (view.state.selection.main.anchor !== from || view.state.selection.main.head !== to)) {
      view.dispatch({selection: {anchor: from, head: to}});
    }
  };
  const dissolve = () => {
    if (!active) return;
    const {host, target, text} = active;
    active = composition = null;
    delete frame._htmlViewEditHost;
    if (text) {
      const node = target.node || doc.createTextNode('');
      node.data = text;
      host.replaceWith(node);
    } else host.remove();
    if (input) {
      input.hidden = true;
      caret.hidden = true;
    }
    scheduleSplitEditorCaret(panel);
  };
  const inputHost = () => input || active?.host;
  const ownsInput = event => active && inputHost().contains(event.target);
  const hasEditFocus = () => input ? document.activeElement === input :
    document.activeElement === frame && doc.activeElement === active?.host && doc.hasFocus();
  const selection = () => {
    const current = inputDocument.getSelection(), host = inputHost();
    if (!active || !current?.rangeCount || !host.contains(current.anchorNode) || !host.contains(current.focusNode)) return null;
    const range = current.getRangeAt(0), prefix = inputDocument.createRange();
    prefix.setStart(host, 0);
    prefix.setEnd(range.startContainer, range.startOffset);
    const from = prefix.toString().length;
    prefix.setEnd(range.endContainer, range.endOffset);
    const to = prefix.toString().length;
    const backward = current.anchorNode !== range.startContainer || current.anchorOffset !== range.startOffset;
    return {from, to, anchor: backward ? to : from, head: backward ? from : to};
  };
  const activeCaretRect = offset => {
    const node = active.host.firstChild;
    const rect = node?.nodeType === 3 && offset <= node.length && htmlSplitTextNodeCaretRect(node, offset);
    return rect || active.host.getClientRects()[0];
  };
  const paintBridgedSelection = range => {
    if (!active || !hasEditFocus() || !range) {
      caret.hidden = true;
      return;
    }
    const node = active.host.firstChild;
    const visible = doc.getSelection();
    if (visible.anchorNode !== node || visible.focusNode !== node || visible.anchorOffset !== range.anchor || visible.focusOffset !== range.head) {
      visible.setBaseAndExtent(node, range.anchor, node, range.head);
    }
    const rect = activeCaretRect(range.head);
    const viewport = frame.getBoundingClientRect();
    const clip = htmlSplitCaretClip(active.host, viewport);
    const onScreen = rect && rect.left >= clip.left && rect.left <= clip.right && rect.bottom > clip.top && rect.top < clip.bottom;
    caret.hidden = !onScreen || range.from !== range.to;
    if (!onScreen) return;
    const left = `${viewport.left + rect.left}px`, top = `${viewport.top + Math.max(rect.top, clip.top)}px`;
    Object.assign(input.style, {left, top});
    Object.assign(caret.style, {left, top, height: `${Math.min(rect.bottom, clip.bottom) - Math.max(rect.top, clip.top)}px`,
      backgroundColor: doc.defaultView.getComputedStyle(active.host).color});
  };
  const place = (host, anchor, head = anchor) => {
    const target = input || host;
    // An empty inline span can have no line box for the caret overlay to measure.
    if (active.text) {
      host.style.removeProperty('display');
      host.style.removeProperty('min-block-size');
    } else {
      host.style.setProperty('display', 'inline-block', 'important');
      host.style.setProperty('min-block-size', '1em', 'important');
    }
    if (input && (input.childNodes.length !== 1 || input.firstChild?.data !== active.text)) {
      input.replaceChildren(document.createTextNode(active.text));
    }
    if (input) input.hidden = false;
    target.focus({preventScroll: true});
    const node = target.firstChild;
    inputDocument.getSelection().setBaseAndExtent(node, anchor, node, head);
    if (input) paintBridgedSelection({anchor, head, from: Math.min(anchor, head), to: Math.max(anchor, head)});
    scheduleSplitEditorCaret(panel);
  };
  const selectedSourceOffsets = (target, start, end) => {
    const {offsets, from, raw} = target;
    const first = sourceOffset(target, start), last = sourceOffset(target, end);
    return {
      from: Number.isInteger(first) ? first : from + (offsets.slice(0, start).findLast(Number.isInteger) ?? 0),
      to: Number.isInteger(last) ? last : from + (offsets.slice(end).find(Number.isInteger) ?? raw.length),
    };
  };
  const selectionGuide = (bounds, forward, rects, host = null) => {
    const viewport = frame.getBoundingClientRect();
    const clip = host ? htmlSplitCaretClip(host, viewport)
      : {left: 0, right: frame.clientWidth, top: 0, bottom: frame.clientHeight};
    const visible = rect => rect.height > 0 && (bounds.from === bounds.to || rect.width > 0) &&
      rect.bottom > clip.top && rect.top < clip.bottom && rect.right >= clip.left && rect.left <= clip.right;
    const boxes = Array.from(rects || []);
    const edge = forward ? boxes.findLast(visible) : boxes.find(visible);
    const clientTop = edge ? viewport.top + Math.max(clip.top, edge.top) : null;
    return {...bounds, head: forward ? bounds.to : bounds.from, clientTop};
  };
  frame._htmlSplitViewGuide = () => {
    const selected = doc.getSelection();
    const range = selected?.rangeCount ? selected.getRangeAt(0) : null;
    const current = hasEditFocus() && (composition || selection());
    if (current && documentView()?.state.doc === active?.sourceDoc) {
      if (composition || current.from === current.to) {
        const point = sourceOffset(active.target, current.head);
        if (!Number.isInteger(point)) return null;
        const rect = activeCaretRect(current.head);
        return selectionGuide({from: point, to: point}, true, rect ? [rect] : [], active.host);
      }
      const bounds = selectedSourceOffsets(active.target, current.from, current.to);
      return selectionGuide(bounds, current.head !== current.from, range?.getClientRects(), active.host);
    }
    if (document.activeElement !== frame || !doc.hasFocus()) return null;
    if (!range || selected.isCollapsed) return null;
    const map = htmlSplitSourceMap(panel, frame);
    if (!map) return null;
    let from = Infinity, to = -1;
    for (const target of htmlViewEditTextTargets(frame, map, active?.host, {selection: true}).values()) {
      const {node} = target;
      if (!range.intersectsNode(node)) continue;
      const start = range.startContainer === node ? range.startOffset : 0;
      const end = range.endContainer === node ? range.endOffset : node.length;
      if (start >= end) continue;
      const bounds = selectedSourceOffsets(target, start, end);
      from = Math.min(from, bounds.from);
      to = Math.max(to, bounds.to);
    }
    const forward = selected.anchorNode === range.startContainer && selected.anchorOffset === range.startOffset;
    return from < to
      ? selectionGuide({from: map.toCurrent(from, 1), to: map.toCurrent(to)}, forward, range.getClientRects()) : null;
  };
  const logicalCaret = (x, y) => {
    const point = doc.caretPositionFromPoint?.(x, y);
    if (point) return {node: point.offsetNode, offset: point.offset};
    const range = doc.caretRangeFromPoint?.(x, y);
    return range && {node: range.startContainer, offset: range.startOffset};
  };
  const activate = (rendered, offset, view, head = offset) => {
    // Map targets carry rendered-snapshot offsets. Unchanged runs stay editable while a newer frame loads.
    const map = inputSourceMap();
    const target = map && htmlPreviewCurrentTarget(map, view.state.doc, rendered);
    if (!target) {
      // Expected while a newer page loads after this text changed. It is logged, not flagged.
      if (map) reportHtmlViewEditProblem(panel, 'clicked text changed since the page was drawn', {}, {flag: false});
      return;
    }
    const anchorPosition = sourceOffset(target, offset), headPosition = sourceOffset(target, head);
    if (!Number.isInteger(anchorPosition) || !Number.isInteger(headPosition)) return;
    const host = doc.createElement('span');
    host.setAttribute('data-html-viewedit', '');
    host.style.setProperty('all', 'unset', 'important');
    host.style.setProperty('white-space', 'inherit', 'important');
    // Chromium forces pre-wrap on plaintext-only hosts. Source transactions and the paste
    // handler already enforce text input, so use an editing host that respects authored CSS.
    if (!input) host.contentEditable = 'true';
    const text = target.node?.data || '';
    host.append(doc.createTextNode(text));
    if (target.node) target.node.replaceWith(host);
    else if (target.sibling) target.sibling[target.before ? 'before' : 'after'](host);
    else target.element.append(host);
    active = {host, target, text, sourceDoc: view.state.doc, previewStale: Boolean(map.snapshot.changes)};
    frame._htmlViewEditHost = host;
    traceHtmlViewEdit(panel, 'editing started', {source: anchorPosition, editsSinceDrawn: active.previewStale});
    selectSource(view, headPosition, headPosition);
    place(host, offset, head);
  };
  const activateSourceCaret = (view, map, offset, defaultToVisible = false, anchor = offset) => {
    let first = null, visible = null;
    const head = map.toSnapshot(offset), tail = map.toSnapshot(anchor);
    for (const target of htmlViewEditTextTargets(frame, map).values()) {
      const positionInRun = source => {
        const relative = source - target.from;
        return relative < 0 || relative > target.raw.length ? -1
          : target.offsets ? target.offsets.indexOf(relative) : relative;
      };
      const position = positionInRun(head);
      if (position >= 0) {
        const start = positionInRun(tail);
        activate(target, start < 0 ? position : start, view, position);
        return true;
      }
      if (!defaultToVisible || !target.node.data.trim()) continue;
      first ??= target;
      const rect = target.node.parentElement.getBoundingClientRect();
      if (!visible && rect.bottom > 0 && rect.top < doc.defaultView.innerHeight) visible = target;
    }
    if (!visible && !first) return false;
    activate(visible || first, 0, view);
    return true;
  };
  const render = (view, caret) => {
    const source = view.state.doc.toString();
    rememberHtmlViewEditRestore(panel, path, source, {anchor: caret, head: caret});
    dissolve();
    renderFileEditorPreviewSurface(panel, pane, path, source, {force: true, context: fileEditorPanelMode(panel), state: fileEditorPanelState(panel)});
  };
  const apply = (range, value, eventType, prior = range) => {
    const view = documentView();
    if (!range || !active || !view || view.state.doc !== active.sourceDoc || range.from === range.to && !value) return;
    if (range.from < 0 || range.to > active.text.length) return;
    const edit = htmlViewEditPatch(active.target, range.from, range.to, value, Boolean(active.host.closest('pre')));
    if (!edit) return;
    const {host, target, text} = active;
    // Native selectionchange can lag behind the first key. History needs the pre-edit selection.
    selectSource(view, sourceOffset(target, prior.anchor), sourceOffset(target, prior.head));
    applyingOwnEdit = true;
    try { view.dispatch({changes: edit.changes, selection: {anchor: edit.caret}, userEvent: eventType}); }
    finally { applyingOwnEdit = false; }
    const insertedBreak = edit.structural && !target.wrap && edit.text === '\n';
    if (edit.structural && !insertedBreak) {
      render(view, edit.caret);
      return;
    }
    const source = view.state.doc.toString();
    const end = target.from + target.raw.length + edit.changes.insert.length - (edit.changes.to - edit.changes.from);
    const prefix = text.slice(0, range.from), suffix = text.slice(range.to);
    const from = insertedBreak ? edit.caret : target.from;
    const next = insertedBreak ? suffix : prefix + edit.text + suffix;
    const raw = source.slice(from, end);
    const offsets = htmlViewEditTextOffsets(doc, raw, next);
    if (offsets === false) { render(view, edit.caret); return; }
    if (insertedBreak) {
      // Keep the native input focused so the next key can arrive before an iframe load.
      const lineBreak = doc.createElement('br');
      if (prefix) host.before(doc.createTextNode(prefix));
      host.before(lineBreak);
      const elements = frame._htmlPreviewElements;
      const following = elements.findIndex(element => lineBreak.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
      const at = following < 0 ? elements.length : following;
      elements.splice(at, 0, lineBreak);
      frame._htmlPreviewElementKeys.splice(at, 0, htmlElementAlignmentKey(lineBreak));
      active.target = {node: doc.createTextNode(suffix), from, raw, offsets};
    } else Object.assign(target, {raw, offsets});
    active.text = next;
    active.sourceDoc = view.state.doc;
    // A current frame now shows this source. A stale one keeps its snapshot and maps this edit too.
    if (!active.previewStale) {
      frame._htmlPreviewSource = source;
      frame._htmlSplitMap = frame._htmlSourceSnapshot = null;
    }
    host.replaceChildren(doc.createTextNode(next));
    if (!active.previewStale) refreshHtmlPreviewElementKey(frame, host.parentElement);
    place(host, insertedBreak ? 0 : range.from + edit.text.length);
    if (!receivesEvents) repaintWebKitHtmlPreview(frame);
  };
  const alignSelection = () => {
    if (composition) return;
    scheduleSplitEditorCaret(panel);
    const view = documentView(), range = selection();
    if (!view || !range || view.state.doc !== active.sourceDoc) return;
    const head = sourceOffset(active.target, range.head);
    selectSource(view, head, head);
    if (input) paintBridgedSelection(range);
  };
  const rebaseActiveText = transaction => {
    if (!hasEditFocus()) return false;
    const {target} = active;
    const range = !composition && selection();
    if (!range || !target.raw.length) return false;
    const start = target.from, end = start + target.raw.length;
    const outerEnd = transaction.changes.mapPos(end, 1);
    const tree = panel._cmApi.ensureSyntaxTree(panel._cmView.state, outerEnd, 20);
    const matchingRun = (from, to) => {
      if (from >= to) return null;
      const token = tree?.resolveInner(from, 1);
      if (!htmlSourceTextToken(token) || token.from !== from) return null;
      const tag = token.parent?.firstChild?.getChild('TagName');
      if (tag && transaction.newDoc.sliceString(tag.from, tag.to).toLowerCase() !== active.host.parentElement.localName) return null;
      let last = token;
      while (htmlSourceTextToken(last.nextSibling) && last.to === last.nextSibling.from) last = last.nextSibling;
      return last.to === to ? {from, to} : null;
    };
    // Text inserted at a boundary joins this run. Structural markup can instead leave it intact.
    const run = matchingRun(transaction.changes.mapPos(start, -1), outerEnd) ||
      matchingRun(transaction.changes.mapPos(start, 1), transaction.changes.mapPos(end, -1));
    if (!run) return false;
    const {from, to} = run;
    const raw = transaction.newDoc.sliceString(from, to);
    const changed = raw !== target.raw;
    let rendered, offsets, anchor, head;
    if (changed) {
      const sourceAnchor = sourceOffset(target, range.anchor), sourceHead = sourceOffset(target, range.head);
      if (!Number.isInteger(sourceAnchor) || !Number.isInteger(sourceHead)) return false;
      rendered = htmlDecodedText(doc.createElement('textarea'), raw);
      offsets = htmlViewEditTextOffsets(doc, raw, rendered);
      if (offsets === false) return false;
      const mappedRange = panel._cmApi.EditorSelection.range(sourceAnchor, sourceHead).map(transaction.changes);
      const position = source => {
        const mapped = source - from;
        return offsets ? offsets.findLastIndex(boundary => boundary <= mapped) : mapped;
      };
      anchor = position(mappedRange.anchor);
      head = position(mappedRange.head);
      if (Math.min(anchor, head) < 0 || Math.max(anchor, head) > rendered.length) return false;
    }
    // Use the accepted offsets for the live text run while the rest of the iframe catches up.
    target.from = from;
    active.sourceDoc = transaction.newDoc;
    active.previewStale = true;
    if (changed) {
      Object.assign(target, {raw, offsets});
      active.text = rendered;
      active.host.replaceChildren(doc.createTextNode(rendered));
      place(active.host, anchor, head);
    }
    return true;
  };
  // Any outside edit (TextEdit, a peer, a reload) either moves the live text run or retires it.
  // A stale editing host would otherwise keep accepting clicks while dropping every keystroke.
  frame._htmlRebaseActiveText = transaction => {
    if (!active || applyingOwnEdit || active.sourceDoc !== transaction.startState.doc) return;
    if (!rebaseActiveText(transaction)) dissolve();
  };
  frame._htmlViewEditRestoreSelection = () => {
    const range = active?.previewStale && hasEditFocus() && selection();
    if (!range || documentView()?.state.doc !== active.sourceDoc) return null;
    const anchor = sourceOffset(active.target, range.anchor), head = sourceOffset(active.target, range.head);
    return Number.isInteger(anchor) && Number.isInteger(head) ? {anchor, head} : null;
  };
  const history = redo => {
    const view = documentView();
    if (!view || view.state.doc !== active?.sourceDoc) return;
    const binding = panel._cmApi.historyKeymap.find(item => item.key === (redo ? 'Mod-y' : 'Mod-z'));
    if (binding?.run(view)) render(view, view.state.selection.main.head);
  };
  const moveAcrossTextRuns = (view, key) => {
    const range = selection();
    if (!range || range.from !== range.to || view.state.doc !== active.sourceDoc) return false;
    const rtl = doc.defaultView.getComputedStyle(active.host).direction === 'rtl';
    const backward = (key === 'arrowleft') !== rtl;
    if (range.head !== (backward ? 0 : active.text.length)) return false;
    const map = inputSourceMap();
    if (!map) return false;
    const direction = backward ? Node.DOCUMENT_POSITION_PRECEDING : Node.DOCUMENT_POSITION_FOLLOWING;
    const candidates = Array.from(htmlViewEditTextTargets(frame, map, active.host).values())
      .filter(({node}) => !active.host.contains(node) && node.data.trim() &&
        (active.host.compareDocumentPosition(node) & direction));
    candidates.sort((a, b) => a.node.compareDocumentPosition(b.node) & direction ? -1 : 1);
    const target = candidates[0];
    if (!target) return false;
    dissolve();
    activate(target, backward ? target.node.length : 0, view);
    active?.host.scrollIntoView({block: 'nearest', inline: 'nearest'});
    return true;
  };
  const moveVertically = (view, direction) => {
    const current = selection();
    if (!current || view.state.doc !== active.sourceDoc) return;
    const node = active.host.firstChild;
    const offset = current.head;
    const origin = (node?.nodeType === 3 && htmlSplitTextNodeCaretRect(node, Math.min(offset, node.length)))
      || active.host.getClientRects()[0];
    if (!origin) return;
    const map = inputSourceMap();
    if (!map) return;
    const targets = htmlViewEditTextTargets(frame, map, active.host);
    const row = (origin.top + origin.bottom) / 2;
    verticalCaretX ??= origin.left;
    const goal = verticalCaretX;
    const candidates = [];
    const measure = doc.createRange();
    for (const target of targets.values()) {
      measure.selectNodeContents(target.node);
      for (const rect of measure.getClientRects()) {
        const y = (rect.top + rect.bottom) / 2;
        const distance = (y - row) * direction;
        if (rect.width <= 0 || rect.height <= 0 || distance < Math.min(rect.height, origin.height) / 2) continue;
        const x = Math.max(rect.left, Math.min(goal, rect.right));
        candidates.push({target, rect, distance, x, y});
      }
    }
    // A visual line can contain several inline runs. Use distance from the preferred column
    // to choose between runs on the same line.
    candidates.sort((a, b) => Math.abs(a.distance - b.distance) > 2
      ? a.distance - b.distance : Math.abs(a.x - goal) - Math.abs(b.x - goal));
    const destination = candidates[0];
    if (!destination) return;
    const {target, rect, x, y} = destination;
    const scroller = doc.scrollingElement;
    const previous = scroller.scrollTop;
    const height = doc.defaultView.innerHeight;
    if (y < 0) scroller.scrollTop += rect.top;
    else if (y >= height) scroller.scrollTop += rect.bottom - height;
    const point = logicalCaret(Math.max(rect.left + 0.1, Math.min(x, rect.right - 0.1)),
      y + previous - scroller.scrollTop);
    if (point?.node !== target.node || !Number.isInteger(sourceOffset(target, point.offset))) return;
    if (active.host.contains(target.node)) {
      const position = sourceOffset(active.target, point.offset);
      place(active.host, point.offset);
      selectSource(view, position, position);
    } else {
      dissolve();
      activate(target, point.offset, view);
    }
  };
  const activateSelection = allowCaret => {
    const selected = doc.getSelection(), view = documentView();
    if (!view) return false;
    if (!selected?.rangeCount || (!allowCaret && (selected.isCollapsed || selection()))) return true;
    const endpoint = (node, offset) => {
      if (node?.nodeType === 3) return {node, offset};
      if (node?.childNodes.length !== 1) return null;
      const child = node.firstChild === active?.host ? active.host.firstChild : node.firstChild;
      return child?.nodeType === 3 ? {node: child, offset: offset ? child.length : 0} : null;
    };
    const anchor = endpoint(selected.anchorNode, selected.anchorOffset);
    const head = endpoint(selected.focusNode, selected.focusOffset);
    if (!anchor || anchor.node !== head?.node) return true;
    if (active?.host.contains(anchor.node)) {
      if (view.state.doc !== active.sourceDoc) return false;
      place(active.host, anchor.offset, head.offset);
      alignSelection();
      return true;
    }
    const map = inputSourceMap();
    if (!map) return false;
    const target = htmlViewEditTextTargets(frame, map, active?.host).get(anchor.node);
    if (!target) return true;
    dissolve();
    activate(target, anchor.offset, view, head.offset);
    return true;
  };
  const activateSelectedText = event => {
    if (event.button === 0 && !event.ctrlKey && !event.metaKey) activateSelection(false);
  };
  const revealGraphic = graphic => {
    if (htmlSplitPreviewFrame(panel) !== frame || panel._cmPath !== path) return false;
    const element = graphic.closest('picture') || graphic;
    const map = inputSourceMap();
    const record = map?.records.find(entry => entry.element === element && entry.exact);
    if (!record) return false;
    dissolve();
    revealSplitEditorSourceSelection(panel, map.toCurrent(record.from, 1), map.toCurrent(record.to));
    return true;
  };
  const activateEmptyBlock = (target, view, map = inputSourceMap()) => {
    const element = target?.closest(htmlViewEditElements);
    if (!element || element.closest(htmlViewEditProtected)) return false;
    const sibling = element.firstChild;
    if (sibling && (sibling.nextSibling || !sibling.matches?.('br'))) return false;
    const record = map?.records.find(entry => entry.element === element && entry.exact);
    const opening = record?.syntax.firstChild;
    if (opening?.name !== 'OpenTag') return false;
    dissolve();
    activate({element, sibling, before: true, from: opening.to, raw: ''}, 0, view);
    return true;
  };
  let pendingClick = null;
  const clickPreview = (event, savedPoint = null) => {
    pendingClick = null;
    const clicked = {target: htmlViewEditNodeLabel(event.target), x: Math.round(event.clientX), y: Math.round(event.clientY)};
    if (active?.host.contains(event.target)) return traceHtmlViewEdit(panel, 'click inside the editing text', clicked);
    if (event.button !== 0 || event.ctrlKey || event.metaKey) return;
    const graphic = event.target.closest?.(htmlViewEditGraphic);
    if (graphic && revealGraphic(graphic)) return traceHtmlViewEdit(panel, 'click on graphic', clicked);
    if (event.target.closest?.(htmlViewEditProtected) || !doc.getSelection()?.isCollapsed) {
      return traceHtmlViewEdit(panel, 'click skipped: protected element or text selection', clicked);
    }
    const view = documentView();
    if (!view) {
      traceHtmlViewEdit(panel, 'click: no source editor', clicked);
      // The rendered page can receive input before its source editor finishes loading.
      if (pane.querySelector('[data-html-viewedit-status="loading"]')) {
        pendingClick = {event, point: logicalCaret(event.clientX, event.clientY)};
      } else if (pane.querySelector('[data-html-viewedit-status="ready"]')) {
        // Only a page that claims to be editable fails silently. Other states already say why.
        const state = fileEditorPanelState(panel);
        reportHtmlViewEditProblem(panel, 'click ignored: no editable source', {
          sameFile: panel._cmPath === path,
          shared: Boolean(state?._shareActive), connected: Boolean(panel._shareConnection),
          currentPage: frame.contentDocument === doc, attached: pane.contains(frame),
        });
      }
      return;
    }
    dissolve();
    const map = inputSourceMap();
    if (!map) return traceHtmlViewEdit(panel, 'click: no source map', clicked);
    const point = savedPoint || logicalCaret(event.clientX, event.clientY);
    const target = htmlViewEditTextTargets(frame, map).get(point?.node);
    clicked.point = htmlViewEditNodeLabel(point?.node);
    clicked.offset = point?.offset;
    if (target && (point.node.parentElement.contains(event.target) ||
        (event.target.matches?.(htmlViewEditElements) && event.target.contains(point.node)))) {
      traceHtmlViewEdit(panel, 'click on text', {...clicked, source: target.from});
      activate(target, point.offset, view);
      return;
    }
    if (activateEmptyBlock(event.target, view, map)) return traceHtmlViewEdit(panel, 'click on empty block', clicked);
    traceHtmlViewEdit(panel, 'click not on editable text', {...clicked, mappedText: Boolean(target)});
    const element = event.target.closest?.(`hr,br,${htmlViewEditElements}`);
    const record = map.records.find(entry => entry.element === element && entry.exact);
    if (!record) {
      if ((event.target === doc.body || event.target === doc.documentElement) && !doc.body.childElementCount && !doc.body.textContent.trim()) {
        const last = map.body.syntax.lastChild;
        const from = last?.name === 'CloseTag' ? last.from : map.body.to;
        activate({element: doc.body, from, raw: '', wrap: true}, 0, view);
      }
      return;
    }
    if (element.matches('hr,br')) {
      const box = element.getBoundingClientRect();
      const before = event.clientY < (box.top + box.bottom) / 2;
      activate({sibling: element, before, from: before ? record.from : record.to, raw: '', wrap: element.matches('hr')}, 0, view);
    }
  };
  if (!input) {
    frame._htmlViewEditInputDocument = doc;
    // A tap made before the source editor is ready replays once it is ready and the page has focus.
    // Starting the editor can move focus briefly, so a blur keeps the tap and the next focus replays it.
    frame._wakeHtmlViewEditInput = () => {
      const click = pendingClick;
      if (!click) return;
      if (!click.event.target.isConnected) {
        pendingClick = null;
        return;
      }
      if (document.activeElement === frame && doc.hasFocus()) clickPreview(click.event, click.point);
    };
    doc.addEventListener('selectionchange', alignSelection);
    doc.addEventListener('pointerdown', event => {
      verticalCaretX = null;
      if (event.isTrusted) clearHtmlViewEditRestore(panel);
    }, true);
    doc.addEventListener('mouseup', activateSelectedText);
    doc.addEventListener('dblclick', activateSelectedText);
    doc.addEventListener('click', clickPreview, true);
    doc.defaultView.addEventListener('blur', () => scheduleSplitEditorCaret(panel));
    doc.defaultView.addEventListener('focus', () => {
      if (documentView()) frame._wakeHtmlViewEditInput();
      scheduleSplitEditorCaret(panel);
    });
  }
  const editInput = (type, data, event) => {
    const range = selection();
    if (!range) return;
    if (type === 'insertText' || type === 'insertReplacementText') apply(range, data || '', 'input.type');
    if (type === 'insertParagraph' || type === 'insertLineBreak') apply(range, '\n', 'input.type');
    if (type === 'historyUndo' || type === 'historyRedo') history(type === 'historyRedo');
    if (/^delete(?:Content|Word|SoftLine|HardLine)(Backward|Forward)$/.test(type)) {
      const prior = {...range};
      if (range.from === range.to) {
        const target = event?.getTargetRanges?.()[0];
        if (type === 'deleteContentBackward') {
          range.from = htmlViewEditDeletionBoundary(active.text, range.from, true, active.target.offsets);
        } else if (type === 'deleteContentForward') {
          range.to = htmlViewEditDeletionBoundary(active.text, range.to, false, active.target.offsets);
        } else if (target &&
            target.startContainer === inputHost().firstChild && target.endContainer === inputHost().firstChild) {
          range.from = target.startOffset;
          range.to = target.endOffset;
        } else if (type.startsWith('deleteWord')) {
          const current = inputDocument.getSelection();
          current.modify('extend', type.endsWith('Backward') ? 'backward' : 'forward', 'word');
          const word = selection();
          current.collapse(inputHost().firstChild, prior.head);
          if (word) Object.assign(range, word);
        }
      }
      apply(range, '', type.endsWith('Backward') ? 'delete.backward' : 'delete.forward', prior);
    }
  };
  inputSurface.addEventListener('beforeinput', event => {
    if (!ownsInput(event) || (event.isComposing && composition) || !event.cancelable) return;
    event.preventDefault();
    editInput(event.inputType, event.data, event);
  });
  const resetInput = () => inputHost().replaceChildren(inputDocument.createTextNode(active.text));
  inputSurface.addEventListener('input', event => {
    if (ownsInput(event) && !composition) {
      resetInput();
      place(active.host, active.text.length);
    }
  });
  inputSurface.addEventListener('compositionstart', event => {
    if (ownsInput(event) && documentView()?.state.doc === active.sourceDoc) composition = selection();
  });
  inputSurface.addEventListener('compositionend', event => {
    if (!composition || !active) return;
    const range = composition;
    composition = null;
    resetInput();
    place(active.host, range.from);
    if (documentView()?.state.doc === active.sourceDoc && event.data) apply(range, event.data, 'input.type.compose');
  });
  inputSurface.addEventListener('paste', event => {
    if (!ownsInput(event)) return;
    event.preventDefault();
    apply(selection(), event.clipboardData?.getData('text/plain') || '', 'input.paste');
  });
  inputSurface.addEventListener('cut', event => {
    if (!ownsInput(event)) return;
    const range = selection();
    event.preventDefault();
    if (!range || !event.clipboardData) return;
    event.clipboardData.setData('text/plain', active.text.slice(range.from, range.to));
    apply(range, '', 'delete.cut');
  });
  inputSurface.addEventListener('drop', event => {
    if (ownsInput(event)) event.preventDefault();
  });
  const handleKeydown = event => {
    const view = documentView(), key = event.key.toLowerCase();
    if (!view) { event.preventDefault(); dissolve(); return; }
    if ((key === 'arrowup' || key === 'arrowdown') && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      moveVertically(view, key === 'arrowup' ? -1 : 1);
      return;
    }
    if (!['shift', 'alt', 'control', 'meta'].includes(key)) verticalCaretX = null;
    if ((key === 'arrowleft' || key === 'arrowright') && !event.shiftKey && !event.altKey && !event.ctrlKey &&
        !event.metaKey && moveAcrossTextRuns(view, key)) {
      event.preventDefault();
      return;
    }
    if (key === 'enter' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      apply(selection(), '\n', 'input.type');
    }
    if ((event.metaKey || event.ctrlKey) && ['s', 'z', 'y'].includes(key)) {
      event.preventDefault();
      if (key === 's') { void saveFileEditor(path, panel); return; }
      history(key === 'y' || event.shiftKey);
    }
    if (key === 'escape') { event.preventDefault(); dissolve(); }
  };
  inputSurface.addEventListener('keydown', event => {
    if (ownsInput(event) && !event.isComposing) handleKeydown(event);
  });
  frame._htmlViewEditMobileKey = (action, key, shiftKey) => {
    const view = documentView(), range = selection();
    if (!range || !hasEditFocus() || composition || view?.state.doc !== active.sourceDoc) return false;
    const owner = active, revision = active.sourceDoc;
    if (fileEditorMobileClipboard(action, active.text.slice(range.from, range.to), value => {
      const current = selection();
      if (active === owner && documentView()?.state.doc === revision && hasEditFocus() &&
          current?.anchor === range.anchor && current?.head === range.head) apply(range, value, 'input.paste');
    })) return true;
    if (!key) return false;
    const event = new KeyboardEvent('keydown', {key, shiftKey, cancelable: true});
    handleKeydown(event);
    if (event.defaultPrevented) return true;
    if (key === 'Backspace' || key === 'Delete') {
      editInput(key === 'Backspace' ? 'deleteContentBackward' : 'deleteContentForward');
    } else if (key === 'Tab') {
      if (!shiftKey) editInput('insertText', '\t');
    } else {
      const line = key === 'Home' || key === 'End';
      const vertical = key === 'ArrowUp' || key === 'ArrowDown';
      const direction = {Home: 'backward', End: 'forward', ArrowUp: 'backward', ArrowDown: 'forward',
        ArrowLeft: 'left', ArrowRight: 'right'}[key];
      if (direction) {
        inputDocument.getSelection().modify(shiftKey ? 'extend' : 'move', direction,
          line ? 'lineboundary' : vertical ? 'line' : 'character');
        if (selection()) alignSelection();
        else place(active.host, range.anchor, range.head);
      }
    }
    return true;
  };
  frame._focusHtmlViewEditMobile = () => {
    if (hasEditFocus()) return;
    const view = documentView(), map = view && inputSourceMap();
    if (!map) return;
    dissolve();
    if (activateSourceCaret(view, map, view.state.selection.main.head, true)) {
      active?.host.scrollIntoView({block: 'nearest', inline: 'nearest'});
    }
  };
  inputSurface.addEventListener('focusout', () => {
    const checkFocus = () => {
      if (active && !hasEditFocus() && (!input || document.activeElement !== frame)) dissolve();
    };
    // WebKit runs focusout microtasks while focus is between the bridge and iframe.
    // Keep the rendered text node alive until the native click establishes its selection.
    if (input) {
      caret.hidden = true;
      requestAnimationFrame(checkFocus);
    } else queueMicrotask(checkFocus);
  });
  if (input) {
    const events = new AbortController();
    const options = {signal: events.signal};
    if (browserUsesCoarsePointer()) {
      // WebKit only transfers touch focus from a script-disabled frame for focusable content.
      // Give rendered text owners a focus target without adding them to the Tab order.
      const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const element = node.parentElement;
        if (node.data.trim() && !element.hasAttribute('tabindex') && !element.closest(`${htmlViewEditProtected},label,summary`)) {
          element.tabIndex = -1;
        }
      }
    }
    input.contentEditable = 'plaintext-only';
    input.tabIndex = -1;
    input.className = 'html-viewedit-input';
    input.hidden = true;
    input.setAttribute('role', 'textbox');
    input.setAttribute('aria-multiline', 'true');
    input.setAttribute('aria-label', t('editor.surface.viewEditor'));
    caret = document.createElement('span');
    caret.className = 'html-viewedit-caret';
    caret.hidden = true;
    caret.setAttribute('aria-hidden', 'true');
    pane.append(input, caret);
    let pending = 0;
    let pressedTarget = null, lastSelection = [];
    const activeTarget = () => Array.from(doc.querySelectorAll(':active')).at(-1);
    const schedule = () => {
      if (!pending) pending = requestAnimationFrame(checkFocus);
    };
    frame._wakeHtmlViewEditInput = schedule;
    const checkFocus = () => {
      pending = 0;
      if (!frame.isConnected || frame.contentDocument !== doc || !document.hasFocus()) return;
      if (document.activeElement === frame) {
        // The browser still tracks :active while sandboxed pointer callbacks are suppressed.
        if (doc.documentElement.matches(':active')) {
          clearHtmlViewEditRestore(panel);
          pressedTarget ||= activeTarget();
          schedule();
          return;
        }
        const selected = doc.getSelection();
        const leftControl = doc.activeElement === doc.body && lastSelection[0]?.closest(htmlViewEditProtected);
        const target = pressedTarget?.matches(':hover') ? pressedTarget
          : leftControl ? Array.from(doc.querySelectorAll(':hover')).at(-1) : null;
        pressedTarget = null;
        const graphic = target?.closest(htmlViewEditGraphic);
        if (graphic && revealGraphic(graphic)) {
          frame.blur();
          return;
        }
        const view = documentView();
        if (target && view && activateEmptyBlock(target, view)) return;
        const snapshot = [doc.activeElement, selected?.anchorNode, selected?.anchorOffset,
          selected?.focusNode, selected?.focusOffset];
        if (!view) {
          // A tap before the source editor arrives must still be eligible when it becomes ready.
          lastSelection = [];
        } else if (snapshot.some((value, index) => value !== lastSelection[index])) {
          verticalCaretX = null;
          if (doc.activeElement?.closest(htmlViewEditProtected) || activateSelection(true)) lastSelection = snapshot;
          scheduleSplitEditorCaret(panel);
        }
      }
      if (hasEditFocus()) {
        lastSelection = [];
        if (active?.sourceDoc !== documentView()?.state.doc) dissolve();
        else if (!composition) paintBridgedSelection(selection());
      }
      if (hasEditFocus() || document.activeElement === frame) schedule();
    };
    window.addEventListener('blur', () => {
      if (document.activeElement === frame) {
        pressedTarget = activeTarget();
        lastSelection = [];
      }
      schedule();
    }, options);
    window.addEventListener('focus', schedule, options);
    input.addEventListener('focus', schedule, options);
    document.addEventListener('selectionchange', alignSelection, options);
    frame._disposeHtmlViewEdit = () => {
      events.abort();
      cancelAnimationFrame(pending);
      input.remove();
      caret.remove();
      delete frame._htmlSplitViewGuide;
      delete frame._wakeHtmlViewEditInput;
      delete frame._htmlViewEditMobileKey;
      delete frame._focusHtmlViewEditMobile;
      delete frame._disposeHtmlViewEdit;
    };
  }
  const restore = panel._htmlViewEditRestore;
  if (restore?.path === path && restore.source === frame._htmlPreviewSource) {
    const restoreCaret = () => {
      if (panel._htmlViewEditRestore !== restore || !pane.contains(frame) || frame.contentDocument !== doc) return;
      if (active || (document.activeElement === frame && doc.activeElement?.closest(htmlViewEditProtected))) {
        clearHtmlViewEditRestore(panel, restore);
        return;
      }
      const view = documentView(), map = view && inputSourceMap();
      if (!map) { requestAnimationFrame(restoreCaret); return; }
      clearHtmlViewEditRestore(panel, restore);
      // The restore offsets belong to this frame's source. The caret helper expects current offsets.
      if (activateSourceCaret(view, map, map.toCurrent(restore.head), false, map.toCurrent(restore.anchor))) return;
      const leaf = map.records.find(record => record.exact && record.element.matches('br') && record.to === restore.head);
      if (leaf) activate({sibling: leaf.element, from: leaf.to, raw: ''}, 0, view);
    };
    requestAnimationFrame(restoreCaret);
  }
}

const PREVIEW_SURFACE_CLASSES = Object.freeze([
  'markdown-body', 'html-preview-body', 'image-preview-body', 'pdf-preview-body',
  'data-preview-body', 'media-preview-body', 'code-preview-body', 'office-preview-body',
]);

function cleanupStandardPreviewStrategy(container) {
  releaseRawFileMediaSources(container);
  container._previewPath = null;
  container._previewText = null;
  container._previewDisplayMode = null;
  container._previewContext = null;
  container._mermaidSig = null;
}

function cleanupMarkdownPreviewStrategy(container) {
  container._mermaidSig = null;
}

// Retained Preview strategies decide whether resources are stale after comparing the new signature.
// Cleaning them before that comparison would destroy the exact iframe, zoom viewport, or media node
// whose native scroll/playback state an unchanged passive refresh must retain.
function cleanupRetainedPreviewStrategy() {}

function disconnectPreviewZoomSurfaces(root) {
  const surfaces = Array.from(root?.querySelectorAll?.('.file-editor-preview-zoom-shell') || []);
  if (root?.classList?.contains?.('file-editor-preview-zoom-shell')) surfaces.unshift(root);
  for (const surface of surfaces) disconnectPreviewZoomSurface(surface, {resetClasses: true});
}

function releasePreviewSurfaceResources(container, {retainPdf = false} = {}) {
  retireStagedHtmlPreview(container);
  const frame = container?.querySelector?.('.file-editor-html-preview');
  frame?._disposeHtmlViewEdit?.();
  frame?._disposeHtmlPreviewScroll?.();
  container?._htmlPreviewLinks?.();
  if (container) container._htmlPreviewLinks = null;
  container?._officePreviewController?.abort();
  if (container) container._officePreviewController = null;
  container?._texPreviewSyncCleanup?.();
  if (container) {
    container._texPreviewSyncCleanup = null;
    container._texPreviewSyncBind = null;
    cleanupTexProgressJob(container._texPreviewSyncJobID);
    container._texPreviewSyncJobID = '';
  }
  container?._texPreviewController?.abort();
  if (container) container._texPreviewController = null;
  if (container?._graphvizPreviewController || container?._sqlitePreviewController) container._retainedPreviewSignature = null;
  container?._graphvizPreviewController?.abort();
  container?._sqlitePreviewController?.abort();
  if (container?._graphvizImageUrl?.startsWith('blob:')) URL.revokeObjectURL(container._graphvizImageUrl);
  if (container) {
    container._graphvizPreviewController = null;
    container._sqlitePreviewController = null;
    container._graphvizImageUrl = '';
  }
  if (retainPdf) retireStagedPdfPreview(container);
  else releaseRawFileMediaSources(container);
  disconnectPreviewZoomSurfaces(container);
}

function prepareRetainedPreviewStrategy(container, signature, selector, options) {
  if (container._retainedPreviewSignature === signature && container.querySelector(selector)) return false;
  releasePreviewSurfaceResources(container, options);
  container._retainedPreviewSignature = signature;
  return true;
}

function cleanupMermaidPreviewStrategy(container) {
  container._previewPath = null;
  container._previewText = null;
  container._previewDisplayMode = null;
  container._previewContext = null;
}

function markdownPreviewStrategySignature({path, text, context}) {
  return JSON.stringify([path, text, fileEditorPreviewDisplayMode, context]);
}

function htmlPreviewStrategySignature({path, text, context}) {
  return JSON.stringify([path, text, context]);
}

function texPreviewStrategySignature({path, text}) {
  return JSON.stringify([path, text]);
}

function rawMediaPreviewStrategySignature({path, state, context}) {
  return JSON.stringify([path, rawFileMediaVersion(state), context]);
}

function mermaidPreviewStrategySignature({path, text, context}) {
  return JSON.stringify([path, text, typeof editorPreviewThemeState === 'function' ? editorPreviewThemeState() : '', context]);
}

function renderMarkdownPreviewStrategy({container, path, text, state, context, signature, snapshot}) {
  const currentSignature = JSON.stringify([container._previewPath, container._previewText, container._previewDisplayMode, container._previewContext]);
  if (currentSignature === signature) return false;
  container._previewPath = path;
  container._previewText = text;
  container._previewDisplayMode = fileEditorPreviewDisplayMode;
  container._previewContext = context;
  renderMarkdownPreviewInto(container, text, path, {context, readOnly: state ? state._shareActive === true || !fileEditorStateWritable(state) : false, snapshot});
  return true;
}

function renderMermaidPreviewStrategy({container, path, text, context, signature}) {
  if (container._mermaidSig === signature && container.querySelector('img.mermaid-preview-image, .mermaid-preview-error')) return false;
  container._mermaidSig = signature;
  container._previewAsync = renderMermaidSourceInto(container, text, {path, zoomKey: 'mermaid', context});
  return true;
}

function graphvizWorker() {
  const source = `onmessage = async ({data: {bundle, text}}) => {
    try {
      const {instance} = await import(bundle);
      const viz = await instance();
      const svg = viz.renderString(text, {format: 'svg'});
      if (svg.length > 4 * 1024 * 1024) throw new Error('Graphviz output exceeds 4 MiB');
      postMessage({svg});
    } catch (error) {
      postMessage({error: String(error.message || error).slice(0, 2000)});
    }
  };`;
  const url = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
  try {
    return new Worker(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function graphvizPreviewSvg(text, signal) {
  if (new Blob([text]).size > 128 * 1024) throw new Error('Graphviz source exceeds 128 KiB');
  const worker = graphvizWorker();
  const stop = () => worker.terminate();
  let timer;
  let cancel;
  try {
    return await new Promise((resolve, reject) => {
      cancel = () => reject(new DOMException('Preview superseded', 'AbortError'));
      signal.addEventListener('abort', cancel, {once: true});
      timer = setTimeout(() => reject(new Error('Graphviz rendering timed out')), 10000);
      worker.onmessage = ({data}) => data.svg ? resolve(data.svg) : reject(new Error(data.error));
      worker.onerror = event => reject(new Error(event.message));
      worker.postMessage({bundle: new URL('/static/vendor/viz.js', window.location.href).href, text});
    });
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', cancel);
    stop();
  }
}

function renderGraphvizPreviewStrategy({container, path, text, context, signature, snapshot}) {
  if (!prepareRetainedPreviewStrategy(container, signature, '.graphviz-preview-result')) return false;
  const host = document.createElement('div');
  host.className = 'graphviz-preview-result';
  host.replaceChildren(fileEditorEmptyState(t('common.loading')));
  container.replaceChildren(host);
  const controller = new AbortController();
  container._graphvizPreviewController = controller;
  const current = () => !controller.signal.aborted && container.contains(host);
  container._previewAsync = (async () => {
    try {
      const svg = await graphvizPreviewSvg(text, controller.signal);
      if (!current()) return false;
      const image = document.createElement('img');
      image.alt = t('preview.structured.title', {format: 'Graphviz'});
      image.className = 'mermaid-preview-image';
      // SVG image context disables links, scripts and external resources.
      if (snapshot) {
        image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
        host.replaceChildren(image);
      } else {
        container._graphvizImageUrl = image.src = svgImageUrl(svg);
        installPreviewZoomSurface(host, image, previewZoomOptionsForKind('mermaidFull', {path, zoomKey: 'graphviz', context}));
      }
      return true;
    } catch (error) {
      if (current()) host.replaceChildren(fileEditorEmptyState(t('preview.structured.parseError', {format: 'Graphviz'}), error.message));
      return false;
    }
  })();
  return true;
}

function renderSQLitePreviewStrategy({container, path, signature}) {
  const previous = container.querySelector('.sqlite-preview-result');
  if (!prepareRetainedPreviewStrategy(container, signature, '.sqlite-preview-result')) return false;
  if (previous?.dataset.path === path) {
    container._previewAsync = previous._refreshSQLitePreview();
    return true;
  }
  const host = document.createElement('div');
  host.className = 'file-editor-data-preview sqlite-preview-result';
  host.dataset.path = path;
  const heading = document.createElement('div');
  heading.className = 'file-editor-data-preview-header';
  heading.textContent = t('preview.structured.title', {format: 'SQLite'});
  const refresh = makeButton({label: t('common.reload')});
  const selector = document.createElement('select');
  selector.setAttribute('aria-label', t('preview.sqlite.table'));
  selector.hidden = true;
  const content = document.createElement('div');
  host.append(heading, selector, refresh, content);
  container.replaceChildren(host);
  let requestedTable = '', requestedPage = 0;
  async function load(table = '', page = 0) {
    requestedTable = table;
    requestedPage = page;
    container._sqlitePreviewController?.abort();
    const controller = new AbortController();
    container._sqlitePreviewController = controller;
    const current = () => !controller.signal.aborted && container.contains(host);
    selector.disabled = refresh.disabled = true;
    content.replaceChildren(fileEditorEmptyState(t('common.loading')));
    const query = new URLSearchParams({path, table, page, recover_table: '1'});
    try {
      const result = await apiFetchJson(`/api/fs/sqlite-preview?${query}`, {signal: controller.signal}, {abortRetirementReason: 'sqlite_preview_replaced'});
      if (!current()) return;
      requestedTable = result.table;
      requestedPage = result.page;
      selector.replaceChildren(...result.tables.map(name => new Option(name, name)));
      selector.value = result.table;
      selector.hidden = result.tables.length === 0;
      selector.disabled = refresh.disabled = false;
      if (!result.columns?.length) {
        content.replaceChildren(fileEditorEmptyState(t('preview.sqlite.empty')));
        return;
      }
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = t('preview.sqlite.schema');
      const schema = document.createElement('pre');
      schema.textContent = result.schema + (result.schema_truncated ? '…' : '');
      details.append(summary, schema);
      const tableGrid = document.createElement('div');
      tableGrid.className = 'file-editor-table-preview';
      const rows = document.createElement('table');
      const row = rows.createTHead().insertRow();
      for (const name of result.columns) appendJsonLinesTableCell(row, 'th', {text: name});
      const body = rows.createTBody();
      for (const values of result.rows) {
        const row = body.insertRow();
        for (const value of values) appendJsonLinesTableCell(row, 'td', {text: value ?? 'NULL'});
      }
      tableGrid.append(rows);
      const pages = document.createElement('div');
      pages.className = 'sqlite-preview-pages';
      const pageButton = (label, next, enabled) => makeButton({
        label, disabled: !enabled,
        onClick: () => { container._previewAsync = load(result.table, next); },
      });
      pages.append(
        pageButton(t('preview.sqlite.previous'), result.page - 1, result.page > 0),
        document.createTextNode(t('preview.sqlite.page', {page: result.page + 1})),
        pageButton(t('preview.sqlite.next'), result.page + 1, result.more),
      );
      content.replaceChildren(details, tableGrid, pages);
    } catch (error) {
      if (current()) {
        selector.disabled = refresh.disabled = false;
        const message = previewActionFallbackNode(t('preview.structured.parseError', {format: 'SQLite'}), error.message, path);
        const retry = makeButton({
          label: t('common.retry'),
          onClick: () => { container._previewAsync = load(table, page); },
        });
        message.append(retry);
        content.replaceChildren(message);
      }
    }
  }
  selector.addEventListener('change', () => { container._previewAsync = load(selector.value); });
  refresh.addEventListener('click', () => { container._previewAsync = load(); });
  host._refreshSQLitePreview = () => load(requestedTable, requestedPage);
  container._previewAsync = load();
  return true;
}

function retireStagedHtmlPreview(container) {
  const frame = container?._htmlStagedFrame;
  if (!frame) return;
  delete container._htmlStagedFrame;
  container._retainedPreviewSignature = null;
  frame.remove();
}

// The restore offsets belong to `source`. toSource maps current editor offsets into it.
function rememberSharedHtmlPreviewSelection(container, path, source, toSource = pos => pos) {
  const panel = container.closest('.file-editor-panel');
  if (!panel?._shareConnection || panel._htmlViewEditRestore) return;
  const selection = htmlEditorPreviewFrame(panel)?._htmlViewEditRestoreSelection?.();
  if (selection) rememberHtmlViewEditRestore(panel, path, source, {anchor: toSource(selection.anchor), head: toSource(selection.head)});
}

function renderHtmlPreviewStrategy({container, path, text, context, signature}) {
  const frame = container.querySelector('.file-editor-html-preview');
  // The frame keeps its source with \n line endings. Compare the same form, or a saved file
  // with \r\n endings reloads the page on every render.
  const source = String(text ?? '').replace(/\r\n?/g, '\n');
  const retain = frame?._htmlPreviewDocument && frame._htmlViewEditContext === context &&
    frame._htmlViewEditPath === path && frame._htmlPreviewDocument === frame.contentDocument;
  if (retain && frame._htmlPreviewSource === source) {
    retireStagedHtmlPreview(container);
    container._retainedPreviewSignature = signature;
    return false;
  }
  if (retain) {
    if (container._htmlStagedFrame && container._retainedPreviewSignature === signature) return false;
    retireStagedHtmlPreview(container);
    container._retainedPreviewSignature = signature;
  } else {
    if (frame?._htmlPreviewDocument) container._retainedPreviewSignature = null;
    rememberSharedHtmlPreviewSelection(container, path, source);
    if (!prepareRetainedPreviewStrategy(container, signature, '.file-editor-html-preview')) return false;
  }
  renderHtmlPreviewInto(container, path, text, retain);
  const current = container._htmlStagedFrame || container.querySelector('.file-editor-html-preview');
  current._htmlViewEditPath = path;
  current._htmlViewEditContext = context;
  return true;
}
function renderImagePreviewStrategy({container, path, state, context, signature, snapshot}) {
  if (!prepareRetainedPreviewStrategy(container, signature, '.file-editor-preview-image')) return false;
  renderRawImagePreviewInto(container, path, state, {context, snapshot});
  return true;
}
function renderPdfPreviewStrategy({container, path, signature, snapshot}) {
  if (snapshot) return false;
  if (!prepareRetainedPreviewStrategy(container, signature, '.file-editor-pdf-preview')) return false;
  renderPdfPreviewInto(container, path);
  return true;
}
function renderStructuredPreviewStrategy({container, path, text, renderer}) { renderStructuredPreviewInto(container, path, text, renderer); }
function renderJsonLinesPreviewStrategy({container, path, text}) { renderJsonLinesTablePreviewInto(container, path, text); }
function renderDelimitedPreviewStrategy({container, path, text, renderer}) { renderDelimitedPreviewInto(container, path, text, renderer); }
function renderNativeMediaPreviewStrategy({container, path, state, renderer, signature, snapshot}) {
  if (!prepareRetainedPreviewStrategy(container, signature, `.file-editor-native-${renderer.kind}`)) return false;
  renderNativeMediaPreviewInto(container, path, state, renderer.kind, {snapshot});
  return true;
}
function renderUnsupportedPreviewStrategy({container, path, state}) { renderUnsupportedPreviewInto(container, path, state); }
function renderCodePreviewStrategy({container, path, text}) { renderEditorCodePreviewInto(container, path, text); }

function renderPreviewDescriptor(renderer, context) {
  if (context.snapshot && renderer.snapshot === false) {
    releasePreviewSurfaceResources(context.container);
    context.container.replaceChildren(fileEditorEmptyState(t('preview.unsupported.default')));
    return false;
  }
  if (context.container._previewRendererId && context.container._previewRendererId !== renderer.id) {
    releasePreviewSurfaceResources(context.container);
  }
  context.container._previewRendererId = renderer.id;
  renderer.cleanup(context.container, context);
  const signature = typeof renderer.signature === 'function' ? renderer.signature(context) : null;
  return renderer.render({...context, renderer, signature});
}

const previewDeferredWorkUserOwnershipRetryMs = 100;

function trackPreviewAsyncCompletion(container, completion) {
  if (!container || !completion || typeof completion.then !== 'function') return completion;
  const previous = container._previewAsync;
  container._previewAsync = previous && typeof previous.then === 'function' && previous !== completion
    ? Promise.all([previous, completion]).then(([, value]) => value)
    : completion;
  return completion;
}

function previewDeferredWorkScrollOwner(container) {
  return container?.closest?.('.file-editor-preview-pane-panel') || container;
}

function schedulePreviewDeferredWorkAfterUserScroll(container, key, work) {
  if (!container || typeof work !== 'function') return Promise.resolve(false);
  const ownerWindow = container.ownerDocument?.defaultView || window;
  const owner = previewDeferredWorkScrollOwner(container);
  let pendingByKey = container._previewDeferredWorkByKey;
  if (!(pendingByKey instanceof Map)) {
    pendingByKey = new Map();
    container._previewDeferredWorkByKey = pendingByKey;
  }
  const normalizedKey = String(key || 'default');
  let pending = pendingByKey.get(normalizedKey);
  if (pending) {
    pending.work = work;
    return pending.promise;
  }
  if (!previewScrollUserOwnsElementNow(owner)) {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error);
    }
  }
  pending = {timer: 0, work, promise: null, resolve: null, reject: null};
  pending.promise = new Promise((resolve, reject) => {
    pending.resolve = resolve;
    pending.reject = reject;
  });
  pendingByKey.set(normalizedKey, pending);
  const retry = () => {
    pending.timer = 0;
    if (pendingByKey.get(normalizedKey) !== pending) return;
    if (container.isConnected === false) {
      pendingByKey.delete(normalizedKey);
      pending.resolve(false);
      return;
    }
    if (previewScrollUserOwnsElementNow(owner)) {
      pending.timer = ownerWindow.setTimeout(retry, previewDeferredWorkUserOwnershipRetryMs);
      return;
    }
    pendingByKey.delete(normalizedKey);
    Promise.resolve().then(() => pending.work()).then(pending.resolve, pending.reject);
  };
  pending.timer = ownerWindow.setTimeout(retry, previewDeferredWorkUserOwnershipRetryMs);
  return pending.promise;
}

function cancelPreviewDeferredWorkAfterUserScroll(container, key) {
  const pendingByKey = container?._previewDeferredWorkByKey;
  const normalizedKey = String(key || 'default');
  const pending = pendingByKey instanceof Map ? pendingByKey.get(normalizedKey) : null;
  if (!pending) return false;
  const ownerWindow = container.ownerDocument?.defaultView || window;
  if (pending.timer) ownerWindow.clearTimeout?.(pending.timer);
  pendingByKey.delete(normalizedKey);
  pending.resolve(false);
  return true;
}

function scheduleEditorPreviewRenderAfterUserScroll(container, path, text, options = {}) {
  void schedulePreviewDeferredWorkAfterUserScroll(container, 'editor-render', () => (
    renderEditorPreviewPane(container, path, text, options)
  ));
  return true;
}

function cancelEditorPreviewRenderAfterUserScroll(container) {
  return cancelPreviewDeferredWorkAfterUserScroll(container, 'editor-render');
}

function renderEditorPreviewPane(container, path, text, options = {}) {
  if (!container) return;
  if (options.force !== true && previewScrollUserOwnsElementNow(container)) {
    scheduleEditorPreviewRenderAfterUserScroll(container, path, text, options);
    return false;
  }
  cancelEditorPreviewRenderAfterUserScroll(container);
  const renderGeneration = debugModeExplicitUrlEnabled === true
    ? Number(container._previewTraceRenderGeneration || 0) + 1
    : 0;
  if (renderGeneration) container._previewTraceRenderGeneration = renderGeneration;
  const previousAsync = container._previewAsync;
  container._previewAsync = null;
  const scrollTop = container.scrollTop || 0;
  const scrollLeft = container.scrollLeft || 0;
  const scrollOwner = createPassiveDeferredElementScrollOwner(container);
  const state = options.state || fileState.get(path) || null;
  const renderer = previewRendererForPath(path, state);
  const previewContext = previewContextId(options.context || 'preview');
  for (const className of PREVIEW_SURFACE_CLASSES) container.classList.toggle(className, renderer.surfaceClasses.includes(className));
  const vanilla = fileEditorPreviewDisplayMode === 'vanilla';
  container.classList.toggle('vanilla-preview-body', vanilla);
  container.classList.toggle('editor-preview-vanilla', vanilla);
  const rendered = renderPreviewDescriptor(renderer, {container, path, text, state, context: previewContext, snapshot: options.snapshot === true});
  refreshMarkdownFenceHighlights(container);
  if (rendered === false) container._previewAsync = previousAsync;
  restoreElementScrollPosition(container, scrollTop, scrollLeft, {
    owner: 'preview-render-restore', previewSurface: renderer.id, renderContext: previewContext, renderGeneration,
    deferredOwner: scrollOwner,
  });
  return rendered;
}

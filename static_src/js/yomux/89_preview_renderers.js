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

function previewZoomStagePadding(stage) {
  const ownerWindow = previewZoomOwnerWindow(stage);
  const style = ownerWindow?.getComputedStyle?.(stage) || (typeof getComputedStyle === 'function' ? getComputedStyle(stage) : null);
  const px = name => Number.parseFloat(style?.getPropertyValue?.(name) || '') || 0;
  return {
    x: px('padding-left') + px('padding-right'),
    y: px('padding-top') + px('padding-bottom'),
  };
}

function previewZoomFitScale(viewport, content, options = {}) {
  const size = previewZoomContentSize(content);
  const availableWidth = Math.max(1, (viewport?.clientWidth || 1) - previewZoomPolicy.fitPaddingPx);
  const availableHeight = Math.max(1, (viewport?.clientHeight || 1) - previewZoomPolicy.fitPaddingPx);
  const fitScale = Math.min(availableWidth / size.width, availableHeight / size.height);
  const maxFitScale = Number.isFinite(options.fitMaxScale) ? options.fitMaxScale : Number.POSITIVE_INFINITY;
  return clampPreviewFitScale(Math.min(maxFitScale, fitScale));
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
  const scale = state.mode === 'fit' ? previewZoomFitScale(viewport, content, options) : clampPreviewZoomScale(state.scale);
  const size = previewZoomContentSize(content);
  const scaledWidth = Math.max(1, Math.round(size.width * scale));
  const scaledHeight = Math.max(1, Math.round(size.height * scale));
  content.style.width = `${scaledWidth}px`;
  content.style.height = `${scaledHeight}px`;
  content.classList.add('file-editor-preview-zoom-content');
  const stage = content.closest?.('.file-editor-preview-zoom-stage') || null;
  if (stage) {
    const padding = previewZoomStagePadding(stage);
    stage.style.width = `${Math.max(viewport.clientWidth, scaledWidth + padding.x)}px`;
    stage.style.height = `${Math.max(viewport.clientHeight, scaledHeight + padding.y)}px`;
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
  if (ResizeObserverCtor && resolvedOptions.controlsOnly !== true) {
    const resizeObserver = new ResizeObserverCtor(() => {
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
    });
    lifecycleScope.ownObserver('resize-observer', resizeObserver);
    resizeObserver.observe(viewport);
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
  if (options.controlsOnly === true) viewport.dataset.previewZoomControlsOnly = '1';
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
       if (options.controlsOnly === true) {
         installPreviewZoomSurface(container, img, previewZoomOptionsForKind(fullPreview ? 'mermaidFull' : 'mermaidInline', {
           ...options,
           path: options.path || '',
           full: fullPreview,
         }));
       } else if (options.interactive === false) {
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
  if (bootstrap.guestWorkspace) return renderSharePublishedPDF(container, path);
  if (fileState.get(path)?._shareActive && !container._sharePublishRequested) {
    container.replaceChildren(document.createTextNode('Shared TeX source. Use Publish PDF in YO!share to compile and publish.'));
    return true;
  }
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

function renderHtmlPreviewInto(container, path, text) {
  const embeddedScrollPosition = previewEmbeddedScrollPosition(container);
  const children = [];
  if (htmlPreviewHasDisabledJavaScript(text)) {
    const notice = document.createElement('div');
    notice.className = 'file-editor-html-js-notice';
    const message = document.createElement('span');
    message.textContent = t('preview.jsDisabled');
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
    notice.append(message, link);
    children.push(notice);
  }
  const frame = document.createElement('iframe');
  frame.className = 'file-editor-html-preview';
  // Scripts remain disabled. Same-origin access is needed only so the shared Preview owner can
  // observe the iframe document's native touch/scroll state instead of treating it as a second,
  // invisible scroll surface.
  frame.setAttribute('sandbox', 'allow-same-origin');
  frame.setAttribute('title', t('preview.htmlTitle'));
  frame.addEventListener('load', () => {
    restorePreviewEmbeddedScrollPosition(container, frame, embeddedScrollPosition);
    const frameDocument = frame.contentDocument;
    const mathStylesheet = document.querySelector('link[data-katex-stylesheet]');
    if (frameDocument && mathStylesheet && !frameDocument.querySelector('link[data-katex-stylesheet]')) {
      const stylesheet = frameDocument.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = mathStylesheet.href;
      stylesheet.dataset.katexStylesheet = '1';
      frameDocument.head?.append(stylesheet);
    }
    renderTexMathInElement(frameDocument?.body);
  });
  frame.srcdoc = String(text ?? '');
  children.push(frame);
  container.replaceChildren(...children);
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

function renderHtmlPreviewStrategy({container, path, text, context, signature}) {
  if (!prepareRetainedPreviewStrategy(container, signature, '.file-editor-html-preview')) return false;
  renderHtmlPreviewInto(container, path, text);
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

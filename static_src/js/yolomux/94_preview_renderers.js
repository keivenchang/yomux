// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// File preview renderers and zoom controls split from 95_codemirror_editor.js.

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
  imagePane: Object.freeze({zoomKey: 'image-pane', fitMaxScale: previewZoomPolicy.fitScaleCaps.image, full: true, wheelZoom: true, panDrag: true}),
  imagePreview: Object.freeze({zoomKey: 'image-preview', fitMaxScale: previewZoomPolicy.fitScaleCaps.image, full: true, wheelZoom: true, panDrag: true}),
  mermaidFull: Object.freeze({zoomKey: 'mermaid', fitMaxScale: previewZoomPolicy.fitScaleCaps.mermaidFull, full: true, wheelZoom: true, panDrag: true}),
  mermaidInline: Object.freeze({zoomKey: 'mermaid', fitMaxScale: previewZoomPolicy.fitScaleCaps.mermaidInline, full: false, wheelZoom: true, panDrag: true}),
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
  if (!Object.prototype.hasOwnProperty.call(options, 'wheelZoom')) result.wheelZoom = defaults.wheelZoom === true;
  if (!Object.prototype.hasOwnProperty.call(options, 'panDrag')) result.panDrag = defaults.panDrag === true;
  return result;
}

function previewZoomStateForAction(actionId, currentScale) {
  return previewZoomActionById.get(actionId)?.zoomState?.(currentScale) || null;
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
  if (typeof shell?._previewZoomControlsCleanup === 'function') {
    shell._previewZoomControlsCleanup();
    shell._previewZoomControlsCleanup = null;
  }
  if (shell?._previewZoomResizeObserver) {
    shell._previewZoomResizeObserver.disconnect();
    shell._previewZoomResizeObserver = null;
  }
  if (shell?._previewZoomResizeFrame) {
    previewZoomOwnerWindow(shell)?.cancelAnimationFrame?.(shell._previewZoomResizeFrame);
    shell._previewZoomResizeFrame = 0;
  }
  if (shell?._previewZoomRevealTimer) {
    previewZoomOwnerWindow(shell)?.clearTimeout?.(shell._previewZoomRevealTimer);
    shell._previewZoomRevealTimer = 0;
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
  shell.dataset.previewZoomWheel = options.wheelZoom === true ? '1' : '0';
  shell.dataset.previewZoomPan = options.panDrag === true ? '1' : '0';
  if (Number.isFinite(options.fitMaxScale)) shell.dataset.previewZoomFitMaxScale = String(options.fitMaxScale);
  else delete shell.dataset.previewZoomFitMaxScale;
}

function previewZoomOptionsFromSurface(shell) {
  const fitMaxScale = Number.parseFloat(shell?.dataset?.previewZoomFitMaxScale || '');
  return {
    path: shell?.dataset?.previewZoomPath || '',
    zoomKey: shell?.dataset?.previewZoomKey || 'default',
    full: shell?.dataset?.previewZoomFull !== '0',
    wheelZoom: shell?.dataset?.previewZoomWheel === '1',
    panDrag: shell?.dataset?.previewZoomPan === '1',
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
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.previewZoomAction = action.id;
  button.textContent = action.labelKey ? t(action.labelKey) : action.label;
  const title = action.titleKey ? t(action.titleKey) : action.title;
  button.title = title;
  button.setAttribute('aria-label', title);
  return button;
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
  if (!viewport || !content) return;
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
  const state = previewZoomReadState({...options, shell});
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
  schedulePreviewZoomFrame(shell, () => {
    if (state.mode === 'fit') {
      viewport.scrollLeft = 0;
      viewport.scrollTop = 0;
      return;
    }
    if (applyOptions.centerIfUnfocused === true && !hasFocusPoint) {
      viewport.scrollLeft = Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2);
      viewport.scrollTop = Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2);
      return;
    }
    viewport.scrollLeft = Math.max(0, (focusX * scale) - focusOffsetX);
    viewport.scrollTop = Math.max(0, (focusY * scale) - focusOffsetY);
  });
}

function setPreviewZoomSurfaceState(shell, content, options = {}, zoomState = {}, applyOptions = {}) {
  previewZoomWriteState(shell, options, zoomState);
  applyPreviewZoomSurface(shell, content, options, applyOptions);
}

function bindPreviewZoomDragPan(shell, viewport, bind) {
  let drag = null;
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
  }, {passive: false});
  bind(viewport, 'pointermove', event => {
    if (!drag || (event.pointerId !== undefined && event.pointerId !== drag.pointerId)) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (Math.abs(dx) > previewZoomPolicy.panThresholdPx || Math.abs(dy) > previewZoomPolicy.panThresholdPx) event.preventDefault();
    viewport.scrollLeft = Math.max(0, drag.scrollLeft - dx);
    viewport.scrollTop = Math.max(0, drag.scrollTop - dy);
  }, {passive: false});
  bind(viewport, 'pointerup', finish);
  bind(viewport, 'pointercancel', finish);
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
  const cleanup = [];
  const bind = (target, type, handler, listenerOptions = false) => {
    if (!target?.addEventListener) return;
    target.addEventListener(type, handler, listenerOptions);
    cleanup.push(() => target.removeEventListener?.(type, handler, listenerOptions));
  };
  shell._previewZoomControlsCleanup = () => {
    while (cleanup.length) cleanup.pop()();
  };
  bind(toolbar, 'click', event => {
    const button = event.target?.closest?.('[data-preview-zoom-action]');
    if (!button || !toolbar.contains(button) || button.disabled) return;
    const current = Number.parseFloat(shell.dataset.previewZoomScale || '1') || 1;
    const zoomState = previewZoomStateForAction(button.dataset.previewZoomAction, current);
    if (zoomState) setPreviewZoomSurfaceState(shell, resolvedContent, resolvedOptions, zoomState, {centerIfUnfocused: true});
  });
  bind(viewport, 'wheel', event => {
    if (resolvedOptions.wheelZoom !== true && !event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const current = Number.parseFloat(shell.dataset.previewZoomScale || '1') || 1;
    const zoomState = previewZoomStateForAction(event.deltaY < 0 ? 'in' : 'out', current);
    if (zoomState) setPreviewZoomSurfaceState(shell, resolvedContent, resolvedOptions, zoomState, {
      focusClientX: event.clientX,
      focusClientY: event.clientY,
    });
  }, {passive: false});
  if (resolvedOptions.panDrag === true) bindPreviewZoomDragPan(shell, viewport, bind);
  const ownerWindow = previewZoomOwnerWindow(shell);
  // Hide the diagram until its viewport size has settled, then reveal. A file editor pane opens at a
  // transient height and Dockview re-lays-it-out ~150ms later (and a hover that triggers a relayout
  // does the same), so fitting against the transient size and then re-fitting makes the diagram
  // visibly jump/resize. `visibility:hidden` keeps the viewport measurable while hidden, and a
  // debounce after the last apply reveals it once at the settled size.
  shell.classList.add('file-editor-preview-zoom-measuring');
  const scheduleReveal = () => {
    if (!shell.classList.contains('file-editor-preview-zoom-measuring')) return;
    if (shell._previewZoomRevealTimer) ownerWindow?.clearTimeout?.(shell._previewZoomRevealTimer);
    shell._previewZoomRevealTimer = ownerWindow?.setTimeout?.(() => {
      shell._previewZoomRevealTimer = 0;
      shell.classList.remove('file-editor-preview-zoom-measuring');
    }, 150);
  };
  const applyAndScheduleReveal = applyOptions => {
    applyPreviewZoomSurface(shell, resolvedContent, resolvedOptions, applyOptions);
    scheduleReveal();
  };
  const ResizeObserverCtor = ownerWindow?.ResizeObserver || (typeof ResizeObserver === 'function' ? ResizeObserver : null);
  if (ResizeObserverCtor) {
    const resizeObserver = new ResizeObserverCtor(() => {
      // Coalesce to one apply per frame. applyPreviewZoomSurface resizes the content inside the
      // observed viewport (and can toggle a scrollbar, which changes the viewport content-box),
      // so applying synchronously here would re-trigger this observer and emit the noisy
      // "ResizeObserver loop completed with undelivered notifications" warning.
      const ownerWin = previewZoomOwnerWindow(shell);
      if (shell._previewZoomResizeFrame) ownerWin?.cancelAnimationFrame?.(shell._previewZoomResizeFrame);
      shell._previewZoomResizeFrame = schedulePreviewZoomFrame(shell, () => {
        shell._previewZoomResizeFrame = 0;
        applyAndScheduleReveal();
      });
    });
    shell._previewZoomResizeObserver = resizeObserver;
    resizeObserver.observe(viewport);
  }
  bind(resolvedContent, 'load', () => applyAndScheduleReveal({centerIfUnfocused: true}), {once: true});
  schedulePreviewZoomFrame(shell, () => applyAndScheduleReveal({centerIfUnfocused: true}));
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

async function renderMermaidSourceInto(container, source, options = {}) {
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
    if (container.dataset.mermaidRenderSeq !== String(seq)) return false;
    const id = `yolomux-mermaid-${Date.now()}-${seq}`;
    const result = await api.render(id, text);
    if (container.dataset.mermaidRenderSeq !== String(seq)) return false;
    const rawSvg = typeof result === 'string' ? result : result?.svg;
    const svg = sanitizeStandaloneSvg(rawSvg);
    if (!svg) throw new Error(t('preview.mermaid.noSvg'));
    const img = document.createElement('img');
    img.className = 'mermaid-preview-image';
    img.alt = t('preview.mermaid.alt');
    img.src = svgImageUrl(svg);
    const fullPreview = Object.prototype.hasOwnProperty.call(options, 'full')
      ? options.full !== false
      : container.classList.contains('file-editor-preview-pane-panel');
    installPreviewZoomSurface(container, img, previewZoomOptionsForKind(fullPreview ? 'mermaidFull' : 'mermaidInline', {
      ...options,
      path: options.path || '',
      full: fullPreview,
    }));
    return true;
  } catch (error) {
    disconnectPreviewZoomSurface(container, {resetClasses: true});
    if (container.dataset.mermaidRenderSeq === String(seq)) container.replaceChildren(mermaidErrorNode(text, error));
    return false;
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

function safeDecodePathComponent(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function localPathFromFileHref(href) {
  const raw = String(href || '').trim();
  if (!/^file:/i.test(raw)) return '';
  try {
    const base = globalThis.location?.href || 'http://localhost/';
    const url = new URL(raw, base);
    if (url.protocol !== 'file:') return '';
    return safeDecodePathComponent(url.pathname || '');
  } catch (_) {
    const match = raw.match(/^file:\/\/(?:localhost)?(\/[^?#]*)/i);
    return match ? safeDecodePathComponent(match[1]) : '';
  }
}

function openMarkdownPreviewPathLink(container, resolved) {
  const owner = openFileOwnerSessionsForPath(container?.dataset?.mdPath || '')[0] || undefined;
  return Promise.resolve(openFileInEditor(resolved, basenameOf(resolved), {
    viewMode: editorPreviewModeAvailable(resolved) ? 'preview' : 'edit',
    ownerSession: owner,
  })).catch(() => showToast(t('preview.openFailed', {path: resolved}), '', {level: 'error'}));
}

// in the file-editor markdown preview, route link clicks: in-page #anchors keep default;
// file:// server paths and relative file links open through the YOLOmux editor, while external links
// open in a new browser tab. The server's read endpoint still rejects paths outside allowed roots.
function handleMarkdownPreviewLinkClick(event) {
  const a = event.target.closest?.('a');
  if (!a) return;
  const container = event.currentTarget;
  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#')) return;
  if (/^file:/i.test(href)) {
    event.preventDefault();
    const resolved = localPathFromFileHref(href);
    if (resolved) openMarkdownPreviewPathLink(container, resolved);
    return;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) {
    event.preventDefault();
    window.open(a.href, '_blank', 'noopener,noreferrer');
    return;
  }
  event.preventDefault();
  const clean = href.split('#')[0].split('?')[0];
  if (!clean) return;
  const basePath = container?.dataset?.basePath || '/';
  const resolved = joinAndNormalize(clean.startsWith('/') ? '/' : basePath, clean);
  openMarkdownPreviewPathLink(container, resolved);
}

function isMarkdownPath(path) {
  const lower = String(path || '').toLowerCase();
  return lower.endsWith('.md') || lower.endsWith('.markdown');
}

function isHtmlPath(path) {
  const lower = String(path || '').toLowerCase();
  return lower.endsWith('.html') || lower.endsWith('.htm');
}

function editorPreviewModeAvailable(path, state = null) {
  return previewPathIsPreviewable(path, state || openFiles.get(path));
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

function jsonLinesStructuredPreview(path, source) {
  const ext = fileExtensionOf(path);
  const lines = String(source ?? '').split(/\r?\n/);
  const records = [];
  const errors = [];
  lines.forEach((line, index) => {
    if (!line.trim()) return;
    try {
      records.push(JSON.parse(line));
    } catch (error) {
      errors.push(t('preview.parse.lineError', {line: index + 1, error: String(error?.message || error)}));
    }
  });
  if (errors.length) {
    return {
      label: t('preview.structured.parseError', {format: ext === '.ndjson' ? 'NDJSON' : 'JSONL'}),
      text: source,
      language: 'json',
      error: errors.slice(0, 5).join('\n'),
    };
  }
  return {
    label: t('preview.structured.records', {format: ext === '.ndjson' ? 'NDJSON' : 'JSONL', count: records.length}),
    text: records.map(record => JSON.stringify(record)).join('\n'),
    language: 'json',
    error: '',
  };
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

function structuredPreviewValue(path, text) {
  const ext = fileExtensionOf(path);
  const source = String(text ?? '');
  if (ext === '.json') return jsonStructuredPreview(t('preview.structured.title', {format: 'JSON'}), source, t('preview.structured.parseError', {format: 'JSON'}));
  if (ext === '.geojson') return jsonStructuredPreview(t('preview.structured.title', {format: 'GeoJSON'}), source, t('preview.structured.parseError', {format: 'GeoJSON'}));
  if (ext === '.excalidraw') return jsonStructuredPreview(t('preview.structured.title', {format: 'Excalidraw JSON'}), source, t('preview.structured.parseError', {format: 'Excalidraw'}));
  if (ext === '.jsonl' || ext === '.ndjson') return jsonLinesStructuredPreview(path, source);
  if (ext === '.ipynb') return notebookStructuredPreview(source);
  if (ext === '.toml') return {label: t('preview.structured.title', {format: 'TOML'}), text: source, language: 'ini', error: ''};
  if (['.xml', '.drawio', '.dio'].includes(ext)) return {label: t('preview.structured.title', {format: ext === '.xml' ? 'XML' : 'Draw.io XML'}), text: source, language: 'xml', error: ''};
  if (['.ini', '.cfg', '.conf', '.env', '.properties', '.props'].includes(ext)) return {label: t('preview.structured.title', {format: t('preview.format.config')}), text: source, language: 'ini', error: ''};
  return {label: t('preview.structured.title', {format: 'YAML'}), text: source, language: 'yaml', error: ''};
}

function renderStructuredPreviewInto(container, path, text) {
  const value = structuredPreviewValue(path, text);
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

function renderTablePreviewInto(container, path, text) {
  const delimiter = fileExtensionOf(path) === '.tsv' ? '\t' : ',';
  const maxRows = 200;
  const maxCols = 50;
  const lines = String(text ?? '').split(/\r?\n/).filter(line => line.length > 0);
  let truncatedColumns = false;
  const rows = lines.slice(0, maxRows).map(line => {
    const cells = splitDelimitedPreviewLine(line, delimiter);
    if (cells.length > maxCols) truncatedColumns = true;
    return cells.slice(0, maxCols);
  });
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
  return `/api/fs/html-preview?path=${encodeURIComponent(path)}`;
}

function renderRawImagePreviewInto(container, path, state = null, options = {}) {
  const version = String(state?.mtime || state?.size || 0);
  const img = document.createElement('img');
  img.className = 'file-editor-preview-image';
  img.src = rawFileUrl(path, version ? {v: version} : {});
  img.alt = basenameOf(path);
  img.loading = 'eager';
  img.decoding = 'async';
  img.addEventListener('error', () => {
    container.replaceChildren(previewActionFallbackNode(t('preview.image.loadFailed'), `${previewMimeForPath(path) || 'image'}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`, path));
  }, {once: true});
  container.replaceChildren(previewZoomSurfaceNode(img, previewZoomOptionsForKind('imagePreview', {
    path,
    context: options.context || '',
  })));
}

function renderPdfPreviewInto(container, path) {
  const frame = document.createElement('iframe');
  frame.className = 'file-editor-pdf-preview';
  frame.setAttribute('sandbox', '');
  frame.setAttribute('title', t('preview.pdf.frameTitle', {name: basenameOf(path)}));
  frame.src = rawFileUrl(path);
  const fallback = document.createElement('div');
  fallback.className = 'file-editor-preview-fallback';
  const title = document.createElement('div');
  title.className = 'file-editor-empty-title';
  title.textContent = t('preview.pdf.title');
  const detail = document.createElement('div');
  detail.className = 'file-editor-empty-detail';
  const open = document.createElement('a');
  open.href = rawFileUrl(path);
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  open.textContent = t('preview.action.open');
  const download = document.createElement('a');
  download.href = rawFileDownloadUrl(path);
  download.textContent = t('preview.action.download');
  detail.append(open, document.createTextNode(' · '), download);
  fallback.append(title, detail);
  container.replaceChildren(frame, fallback);
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
  if (path) {
    const open = document.createElement('a');
    open.href = rawFileUrl(path);
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    open.textContent = t('preview.action.open');
    const download = document.createElement('a');
    download.href = rawFileDownloadUrl(path);
    download.textContent = t('preview.action.download');
    detail.append(document.createTextNode(detailText ? ' · ' : ''), open, document.createTextNode(' · '), download);
  }
  fallback.append(title, detail);
  return fallback;
}

function renderNativeMediaPreviewInto(container, path, state = null, kind = 'audio') {
  const media = document.createElement(kind === 'video' ? 'video' : 'audio');
  media.className = `file-editor-native-media file-editor-native-${kind}`;
  media.controls = true;
  media.preload = 'metadata';
  media.src = rawFileUrl(path, state?.mtime ? {v: state.mtime} : {});
  media.addEventListener('error', () => {
    container.replaceChildren(previewActionFallbackNode(t(kind === 'video' ? 'preview.video.loadFailed' : 'preview.audio.loadFailed'), `${previewMimeForPath(path) || kind}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`, path));
  }, {once: true});
  container.replaceChildren(media, previewActionFallbackNode(t(kind === 'video' ? 'preview.video.title' : 'preview.audio.title'), `${previewMimeForPath(path) || kind}${state?.size ? ` · ${formatFileSize(state.size)}` : ''}`, path));
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
  frame.setAttribute('sandbox', '');
  frame.setAttribute('title', t('preview.htmlTitle'));
  frame.srcdoc = String(text ?? '');
  children.push(frame);
  container.replaceChildren(...children);
}

function renderEditorPreviewPane(container, path, text, options = {}) {
  if (!container) return;
  container._previewAsync = null;
  const scrollTop = container.scrollTop || 0;
  const scrollLeft = container.scrollLeft || 0;
  const state = openFiles.get(path) || null;
  const previewKind = previewKindForPath(path, state);
  const previewContext = previewContextId(options.context || 'preview');
  container.classList.toggle('markdown-body', previewKind === 'markdown');
  container.classList.toggle('html-preview-body', previewKind === 'html');
  container.classList.toggle('image-preview-body', previewKind === 'image');
  container.classList.toggle('pdf-preview-body', previewKind === 'pdf');
  container.classList.toggle('data-preview-body', previewKind === 'structured' || previewKind === 'table');
  container.classList.toggle('media-preview-body', previewKind === 'audio' || previewKind === 'video');
  container.classList.toggle('code-preview-body', previewKind === 'text' || previewKind === 'mermaid');
  container.classList.toggle('vanilla-preview-body', fileEditorPreviewDisplayMode === 'vanilla');
  if (previewKind === 'markdown') {
    container._mermaidSig = null;
    // fix 6: skip the expensive markdown render (marked.parse + recursive sanitize + per-block
    // hljs) when the path + content are unchanged from the last render — mirrors CodeMirror's
    // _cmSignature short-circuit. Prevents a multi-second stall re-rendering a large .md when an
    // unrelated panel render fires (off the reorder hot path once S2 lands, but a latent cost).
    if (container._previewPath !== path || container._previewText !== text || container._previewDisplayMode !== fileEditorPreviewDisplayMode || container._previewContext !== previewContext) {
      container._previewPath = path;
      container._previewText = text;
      container._previewDisplayMode = fileEditorPreviewDisplayMode;
      container._previewContext = previewContext;
      renderMarkdownPreviewInto(container, text, path, {context: previewContext});
    }
  } else if (previewKind === 'mermaid') {
    // Idempotent: a periodic pane refresh re-runs this with identical source; re-rendering rebuilds
    // the SVG and (with the reveal gate) FLASHES the diagram on every refresh tick. Skip when the
    // source, preview theme, and context are unchanged AND a rendered diagram (or error) is already
    // present. editorPreviewThemeState() is in the signature so a Bright/Dark/Vanilla toggle still
    // re-renders with the new palette.
    container._previewPath = null;
    container._previewText = null;
    container._previewDisplayMode = null;
    container._previewContext = null;
    const mermaidSig = `${path} ${text} ${typeof editorPreviewThemeState === 'function' ? editorPreviewThemeState() : ''} ${previewContext}`;
    if (container._mermaidSig !== mermaidSig || !container.querySelector('img.mermaid-preview-image, .mermaid-preview-error')) {
      container._mermaidSig = mermaidSig;
      container._previewAsync = renderMermaidSourceInto(container, text, {path, zoomKey: 'mermaid', context: previewContext});
    }
  } else {
    container._previewPath = null;
    container._previewText = null;
    container._previewDisplayMode = null;
    container._previewContext = null;
    container._mermaidSig = null;
    if (previewKind === 'html') renderHtmlPreviewInto(container, path, text);
    else if (previewKind === 'image') renderRawImagePreviewInto(container, path, state, {context: previewContext});
    else if (previewKind === 'pdf') renderPdfPreviewInto(container, path);
    else if (previewKind === 'structured') renderStructuredPreviewInto(container, path, text);
    else if (previewKind === 'table') renderTablePreviewInto(container, path, text);
    else if (previewKind === 'audio' || previewKind === 'video') renderNativeMediaPreviewInto(container, path, state, previewKind);
    else if (previewKind === 'unsupported') renderUnsupportedPreviewInto(container, path, state);
    else renderEditorCodePreviewInto(container, path, text);
  }
  restoreElementScrollPosition(container, scrollTop, scrollLeft);
}

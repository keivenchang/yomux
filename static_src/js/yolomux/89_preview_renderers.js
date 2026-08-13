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
  if (shell) shell._previewZoomLifecycleScope = null;
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
    if (zoomState) setPreviewZoomSurfaceState(shell, resolvedContent, resolvedOptions, zoomState, {centerIfUnfocused: true});
  });
  if (resolvedOptions.panDrag === true) bindPreviewZoomDragPan(shell, viewport, bind);
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
      lifecycleScope.release('resize-frame');
      let frame = 0;
      frame = schedulePreviewZoomFrame(shell, () => {
        lifecycleScope.release('resize-frame', frame);
        if (!lifecycleScope.current()) return;
        applyAndScheduleReveal();
      });
      lifecycleScope.ownTimer('resize-frame', frame, value => ownerWin?.cancelAnimationFrame?.(value));
    });
    lifecycleScope.ownObserver('resize-observer', resizeObserver);
    resizeObserver.observe(viewport);
  }
  bind(resolvedContent, 'load', () => applyAndScheduleReveal({centerIfUnfocused: true}), {once: true});
  let initialFrame = 0;
  initialFrame = schedulePreviewZoomFrame(shell, () => {
    lifecycleScope.release('initial-frame', initialFrame);
    if (lifecycleScope.current()) applyAndScheduleReveal({centerIfUnfocused: true});
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
    const id = `yolomux-mermaid-${Date.now()}-${seq}`;
    const result = await api.render(id, text);
    if (!isCurrent() || container.dataset.mermaidRenderSeq !== String(seq)) return false;
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
    if (isCurrent() && container.dataset.mermaidRenderSeq === String(seq)) container.replaceChildren(mermaidErrorNode(text, error));
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

function openMarkdownPreviewPathLink(container, resolved) {
  const owner = openFileOwnerSessionsForPath(container?.dataset?.mdPath || '')[0] || undefined;
  return Promise.resolve(openFileInEditor(resolved, basenameOf(resolved), {
    viewMode: editorPreviewModeAvailable(resolved) ? 'preview' : 'edit',
    ownerSession: owner,
  })).catch(() => emitNotification('previewOpen', {item: fileEditorItemFor(container?.dataset?.mdPath || ''), title: t('preview.openFailed', {path: resolved}), className: 'attention-alert toast'}));
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
    const cells = columns.map(key => compactJsonLinesCell(row.record[key], maxCellChars));
    const overflow = {};
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
  detail.append(...previewFileActionLinks(path));
  fallback.append(title, detail);
  container.replaceChildren(frame, fallback);
}

function previewFileActionLinks(path, {separator = ' · ', leadingSeparator = '', target = '_blank'} = {}) {
  if (!path) return [];
  const open = document.createElement('a');
  open.href = rawFileUrl(path);
  open.target = target;
  open.rel = 'noopener noreferrer';
  open.textContent = t('common.open');
  const download = document.createElement('a');
  download.href = rawFileDownloadUrl(path);
  download.textContent = t('common.download');
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

const PREVIEW_SURFACE_CLASSES = Object.freeze([
  'markdown-body', 'html-preview-body', 'image-preview-body', 'pdf-preview-body',
  'data-preview-body', 'media-preview-body', 'code-preview-body',
]);

function cleanupStandardPreviewStrategy(container) {
  container._previewPath = null;
  container._previewText = null;
  container._previewDisplayMode = null;
  container._previewContext = null;
  container._mermaidSig = null;
}

function cleanupMarkdownPreviewStrategy(container) {
  container._mermaidSig = null;
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

function mermaidPreviewStrategySignature({path, text, context}) {
  return JSON.stringify([path, text, typeof editorPreviewThemeState === 'function' ? editorPreviewThemeState() : '', context]);
}

function renderMarkdownPreviewStrategy({container, path, text, context, signature}) {
  const currentSignature = JSON.stringify([container._previewPath, container._previewText, container._previewDisplayMode, container._previewContext]);
  if (currentSignature === signature) return;
  container._previewPath = path;
  container._previewText = text;
  container._previewDisplayMode = fileEditorPreviewDisplayMode;
  container._previewContext = context;
  renderMarkdownPreviewInto(container, text, path, {context});
}

function renderMermaidPreviewStrategy({container, path, text, context, signature}) {
  if (container._mermaidSig === signature && container.querySelector('img.mermaid-preview-image, .mermaid-preview-error')) return;
  container._mermaidSig = signature;
  container._previewAsync = renderMermaidSourceInto(container, text, {path, zoomKey: 'mermaid', context});
}

function renderHtmlPreviewStrategy({container, path, text}) { renderHtmlPreviewInto(container, path, text); }
function renderImagePreviewStrategy({container, path, state, context}) { renderRawImagePreviewInto(container, path, state, {context}); }
function renderPdfPreviewStrategy({container, path}) { renderPdfPreviewInto(container, path); }
function renderStructuredPreviewStrategy({container, path, text, renderer}) { renderStructuredPreviewInto(container, path, text, renderer); }
function renderJsonLinesPreviewStrategy({container, path, text}) { renderJsonLinesTablePreviewInto(container, path, text); }
function renderDelimitedPreviewStrategy({container, path, text, renderer}) { renderDelimitedPreviewInto(container, path, text, renderer); }
function renderNativeMediaPreviewStrategy({container, path, state, renderer}) { renderNativeMediaPreviewInto(container, path, state, renderer.kind); }
function renderUnsupportedPreviewStrategy({container, path, state}) { renderUnsupportedPreviewInto(container, path, state); }
function renderCodePreviewStrategy({container, path, text}) { renderEditorCodePreviewInto(container, path, text); }

function renderPreviewDescriptor(renderer, context) {
  renderer.cleanup(context.container, context);
  const signature = typeof renderer.signature === 'function' ? renderer.signature(context) : null;
  return renderer.render({...context, renderer, signature});
}

function renderEditorPreviewPane(container, path, text, options = {}) {
  if (!container) return;
  container._previewAsync = null;
  const scrollTop = container.scrollTop || 0;
  const scrollLeft = container.scrollLeft || 0;
  const state = fileState.get(path) || null;
  const renderer = previewRendererForPath(path, state);
  const previewContext = previewContextId(options.context || 'preview');
  for (const className of PREVIEW_SURFACE_CLASSES) container.classList.toggle(className, renderer.surfaceClasses.includes(className));
  container.classList.toggle('vanilla-preview-body', fileEditorPreviewDisplayMode === 'vanilla');
  renderPreviewDescriptor(renderer, {container, path, text, state, context: previewContext});
  restoreElementScrollPosition(container, scrollTop, scrollLeft);
}

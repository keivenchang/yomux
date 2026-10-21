// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// File preview popout window helpers split from 92_codemirror_editor.js.

const filePreviewPopouts = panePopoutNamespaceMap('file-preview');

function filePreviewPopoutsForPath(path) {
  const record = filePreviewPopouts.get(path);
  return record ? [record] : [];
}

function closeFilePreviewPopout(path) {
  const record = filePreviewPopouts.get(path);
  retireFilePreviewPopoutSnapshotApply(record);
  retireFilePreviewPopoutNavigationWrite(record);
  filePreviewPopouts.delete(path);
  const previewWindow = record?.window;
  if (!previewWindow || previewWindow.closed) return false;
  try { previewWindow.close?.(); } catch (_) {}
  return true;
}

function bumpFilePreviewPopoutGeneration(path) {
  const record = filePreviewPopouts.get(path);
  if (!record) return 0;
  retireFilePreviewPopoutSnapshotApply(record);
  retireFilePreviewPopoutNavigationWrite(record);
  record.previewGeneration = Number(record.previewGeneration || 0) + 1;
  return record.previewGeneration;
}

function filePreviewPopoutGenerationMatches(path, previewWindow, generation) {
  const record = filePreviewPopouts.get(path);
  return Boolean(record && record.window === previewWindow && record.previewGeneration === generation);
}

function filePreviewPopoutDocument(previewWindow) {
  try { return previewWindow?.document || null; } catch (_) { return null; }
}

function filePreviewPopoutScrollElement(previewWindow) {
  const doc = filePreviewPopoutDocument(previewWindow);
  return doc?.scrollingElement || doc?.documentElement || doc?.body || null;
}

function filePreviewPopoutPreviewRoot(previewWindow) {
  return filePreviewPopoutDocument(previewWindow)?.querySelector?.('[data-preview-root]') || null;
}

function filePreviewPopoutCanDrive(previewWindow) {
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  return elementCanScroll(scroller);
}

function scrollSyncTargetPosition(from, to, axis = 'top') {
  const scrollKey = axis === 'left' ? 'scrollLeft' : 'scrollTop';
  const sizeKey = axis === 'left' ? 'scrollWidth' : 'scrollHeight';
  const clientKey = axis === 'left' ? 'clientWidth' : 'clientHeight';
  const sourceSize = Math.max(0, Number(from?.[sizeKey] || 0));
  const targetSize = Math.max(0, Number(to?.[sizeKey] || 0));
  const sourceClient = Math.max(0, Number(from?.[clientKey] || 0));
  const targetClient = Math.max(0, Number(to?.[clientKey] || 0));
  const maxFrom = Math.max(0, sourceSize - sourceClient);
  const maxTo = Math.max(0, targetSize - targetClient);
  const current = Math.max(0, Number(from?.[scrollKey] || 0));
  const edgeSnap = Math.max(2, Math.ceil(sourceClient * 0.01));
  if (maxTo <= 0 || current <= edgeSnap) return 0;
  if (maxFrom <= edgeSnap || current >= maxFrom - edgeSnap) return maxTo;
  const sourceCenter = Math.min(maxFrom, current) + (sourceClient / 2);
  const centerRatio = sourceSize > 0 ? sourceCenter / sourceSize : 0;
  const target = (centerRatio * targetSize) - (targetClient / 2);
  return Math.min(maxTo, Math.max(0, target));
}

function syncScrollPositionByRatio(from, to, owner = 'preview-scroll-ratio', details = {}, deferredOwner = null) {
  if (!from || !to) return false;
  const coordinates = {
    top: scrollSyncTargetPosition(from, to, 'top'),
    left: scrollSyncTargetPosition(from, to, 'left'),
  };
  return deferredOwner
    ? writeDeferredElementScrollIfOwned(deferredOwner, to, coordinates, owner, details)
    : writeOwnedElementScroll(to, coordinates, owner, details);
}

function filePreviewPopoutScrollEdge(element) {
  const maxTop = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  const current = Math.max(0, Number(element?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(element?.clientHeight || 0) * 0.01));
  if (current <= edgeSnap) return 'top';
  return maxTop > 0 && current >= maxTop - edgeSnap ? 'bottom' : '';
}

function filePreviewPopoutScrollCoordinates(scroller) {
  return {
    top: Number(scroller?.scrollTop || 0),
    left: Number(scroller?.scrollLeft || 0),
  };
}

function beginFilePreviewPopoutScrollOwner(record, owner) {
  const generation = Number(record?.scrollSyncGeneration || 0) + 1;
  record.scrollSyncGeneration = generation;
  record.scrollSyncOwner = {...owner, generation};
  record.scrollConvergence = null;
  record.scrollConvergenceCompletedGeneration = 0;
  record.scrollConvergenceStableSnapshots = 0;
  if (owner.kind === 'panel') record.popupScrollInputUntil = 0;
  return generation;
}

function rememberFilePreviewPopoutReflectedScroll(record, scroller, generation) {
  if (!record || !scroller) return false;
  record.scrollReflection = {generation, ...filePreviewPopoutScrollCoordinates(scroller)};
  return true;
}

function filePreviewPopoutReflectedScrollMatches(record, scroller) {
  const reflected = record?.scrollReflection;
  if (!reflected || reflected.generation !== Number(record?.scrollSyncGeneration || 0)) return false;
  const current = filePreviewPopoutScrollCoordinates(scroller);
  return current.top === reflected.top && current.left === reflected.left;
}

function rememberFilePreviewPopoutPanelReflection(record, panel, source, element) {
  if (!record || !panel || !source || !element) return false;
  if (!record.panelScrollReflections) record.panelScrollReflections = new WeakMap();
  const reflections = record.panelScrollReflections.get(panel) || Object.create(null);
  reflections[source] = {
    generation: Number(record.scrollSyncGeneration || 0),
    ...filePreviewPopoutScrollCoordinates(element),
  };
  record.panelScrollReflections.set(panel, reflections);
  return true;
}

function filePreviewPopoutPanelReflectionMatches(record, panel, source, element) {
  const reflections = record?.panelScrollReflections?.get?.(panel);
  const reflected = reflections?.[source];
  if (!reflected || reflected.generation !== Number(record?.scrollSyncGeneration || 0)) return false;
  const current = filePreviewPopoutScrollCoordinates(element);
  if (current.top === reflected.top && current.left === reflected.left) return true;
  delete reflections[source];
  return false;
}

function filePreviewPopoutScrollSyncReady(record) {
  return !record?.scrollSyncFrame
    && !record?.scrollConvergenceFrame
    && !record?.scrollConvergence
    && !record?.previewAsync
    && !record?.snapshotApply
    && !record?.navigationWrite;
}

const filePreviewPopoutInputGraceMs = 500;
const filePreviewPopoutActiveInputMaxMs = 30_000;

function markFilePreviewPopoutScrollInput(record) {
  if (!record) return false;
  const current = nowMs();
  record.popupScrollInputUntil = current + filePreviewPopoutInputGraceMs;
  if (record.popupScrollActiveInputs?.size) {
    record.popupScrollActiveUntil = current + filePreviewPopoutActiveInputMaxMs;
  }
  return true;
}

function beginFilePreviewPopoutScrollInput(record, kind) {
  if (!record) return false;
  if (!(record.popupScrollActiveInputs instanceof Set)) record.popupScrollActiveInputs = new Set();
  record.popupScrollActiveInputs.add(String(kind || 'unknown'));
  markFilePreviewPopoutScrollInput(record);
  return true;
}

function endFilePreviewPopoutScrollInput(record, kind) {
  if (!record) return false;
  record.popupScrollActiveInputs?.delete?.(String(kind || 'unknown'));
  if (!record.popupScrollActiveInputs?.size) record.popupScrollActiveUntil = 0;
  markFilePreviewPopoutScrollInput(record);
  return true;
}

function claimFilePreviewPopoutUserScroll(record, previewWindow) {
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  if (!record || !scroller) return false;
  claimPreviewScrollUserOwnership(scroller);
  beginFilePreviewPopoutScrollOwner(record, {
    kind: 'popout',
    ...filePreviewPopoutScrollCoordinates(scroller),
  });
  return true;
}

function filePreviewPopoutScrollInputActive(record) {
  const current = nowMs();
  const active = Boolean(record?.popupScrollActiveInputs?.size)
    && Number(record?.popupScrollActiveUntil || 0) >= current;
  if (!active && record?.popupScrollActiveInputs?.size) record.popupScrollActiveInputs.clear();
  return active || Number(record?.popupScrollInputUntil || 0) >= current;
}

const filePreviewPopoutSnapshotRetryMs = 100;

function settleFilePreviewPopoutSnapshotApply(record, job, applied) {
  if (!job || job.settled) return false;
  job.settled = true;
  if (job.timer) job.clearTimeout?.(job.timer);
  job.timer = 0;
  if (record?.snapshotApply === job) record.snapshotApply = null;
  job.resolve(Boolean(applied));
  return true;
}

function retireFilePreviewPopoutSnapshotApply(record) {
  const job = record?.snapshotApply;
  return job ? settleFilePreviewPopoutSnapshotApply(record, job, false) : false;
}

function scheduleFilePreviewPopoutApplyWhenIdle(record, apply) {
  if (!record || typeof apply !== 'function') return Promise.resolve(false);
  retireFilePreviewPopoutSnapshotApply(record);
  const previewWindow = record.window;
  const schedule = typeof previewWindow?.setTimeout === 'function'
    ? previewWindow.setTimeout.bind(previewWindow)
    : window.setTimeout.bind(window);
  const clear = typeof previewWindow?.clearTimeout === 'function'
    ? previewWindow.clearTimeout.bind(previewWindow)
    : window.clearTimeout.bind(window);
  let resolveCompletion;
  const completion = new Promise(resolve => { resolveCompletion = resolve; });
  const job = {
    timer: 0,
    clearTimeout: clear,
    resolve: resolveCompletion,
    settled: false,
  };
  const run = () => {
    job.timer = 0;
    if (job.settled || record.snapshotApply !== job) return;
    if (filePreviewPopoutScrollInputActive(record)) {
      job.timer = schedule(run, filePreviewPopoutSnapshotRetryMs);
      return;
    }
    let applied = false;
    try {
      applied = apply();
    } catch (error) {
      console.warn('preview popout snapshot apply failed', error);
      settleFilePreviewPopoutSnapshotApply(record, job, false);
      return;
    }
    Promise.resolve(applied).then(
      value => settleFilePreviewPopoutSnapshotApply(record, job, value),
      error => {
        console.warn('preview popout snapshot apply failed', error);
        settleFilePreviewPopoutSnapshotApply(record, job, false);
      },
    );
  };
  record.snapshotApply = job;
  run();
  return completion;
}

function requestFilePreviewPopoutFrame(previewWindow, callback) {
  if (typeof previewWindow?.requestAnimationFrame === 'function') return previewWindow.requestAnimationFrame(callback);
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(callback);
  return setTimeout(callback, 0);
}

function scheduleFilePreviewPopoutEdgeConvergence(path, record) {
  const owner = record?.scrollSyncOwner;
  if (!owner || owner.kind !== 'panel' || !owner.edge || record.scrollConvergenceFrame) return false;
  const state = record.scrollConvergence?.generation === owner.generation
    ? record.scrollConvergence
    : {generation: owner.generation, lastTop: null, lastMax: null, stableSnapshots: 0};
  record.scrollConvergence = state;
  const run = () => {
    record.scrollConvergenceFrame = 0;
    const current = filePreviewPopouts.get(path);
    if (current !== record) return;
    if (record.scrollSyncOwner?.generation !== state.generation || record.scrollConvergence !== state) {
      scheduleFilePreviewPopoutEdgeConvergence(path, record);
      return;
    }
    const previewWindow = record.window;
    const scroller = filePreviewPopoutScrollElement(previewWindow);
    if (!scroller || previewWindow?.closed) {
      record.scrollConvergence = null;
      return;
    }
    const maxTop = Math.max(0, Number(scroller.scrollHeight || 0) - Number(scroller.clientHeight || 0));
    writeOwnedElementScroll(scroller, {top: owner.edge === 'bottom' ? maxTop : 0}, 'preview-popout-edge-convergence', {previewSurface: 'popout'});
    const top = Number(scroller.scrollTop || 0);
    const measuredMax = Math.max(0, Number(scroller.scrollHeight || 0) - Number(scroller.clientHeight || 0));
    rememberFilePreviewPopoutReflectedScroll(record, scroller, state.generation);
    state.stableSnapshots = state.lastTop === top && state.lastMax === measuredMax
      ? state.stableSnapshots + 1
      : 1;
    state.lastTop = top;
    state.lastMax = measuredMax;
    if (state.stableSnapshots >= 2) {
      if (record.previewAsync) return;
      record.scrollConvergenceCompletedGeneration = state.generation;
      record.scrollConvergenceStableSnapshots = state.stableSnapshots;
      record.scrollConvergence = null;
      return;
    }
    record.scrollConvergenceFrame = requestFilePreviewPopoutFrame(previewWindow, run);
  };
  record.scrollConvergenceFrame = requestFilePreviewPopoutFrame(record.window, run);
  return true;
}

function syncFilePreviewPopoutFromPanel(path, record, panel, source) {
  if (!record) return false;
  const previewWindow = record.window;
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  const root = filePreviewPopoutPreviewRoot(previewWindow);
  const from = fileEditorSourceElement(panel, source);
  if (!scroller || !root || !from || !elementCanScroll(scroller)) return false;
  if (record.scrollSyncOwner?.kind === 'popout' && filePreviewPopoutPanelReflectionMatches(record, panel, source, from)) return false;
  const edge = filePreviewPopoutScrollEdge(from);
  const generation = beginFilePreviewPopoutScrollOwner(record, {kind: 'panel', panel, source, edge});
  setFileEditorScrollSyncGuard(record);
  const synced = syncScrollPositionByRatio(from, scroller, 'preview-popout-panel-reflection', {previewSurface: 'popout'});
  if (!synced) return false;
  rememberFilePreviewPopoutReflectedScroll(record, scroller, generation);
  if (edge) scheduleFilePreviewPopoutEdgeConvergence(path, record);
  return true;
}

function syncFilePreviewPopoutsFromPanel(panel, source) {
  const path = fileEditorPanelPath(panel);
  if (!path || fileEditorPanelState(panel)?.historical === true || !fileEditorSourceCanDrive(panel, source)) return false;
  let synced = false;
  for (const record of filePreviewPopoutsForPath(path)) {
    synced = syncFilePreviewPopoutFromPanel(path, record, panel, source) || synced;
  }
  return synced;
}

function syncFilePreviewPopoutScroll(path, previewWindow, options = {}) {
  const record = filePreviewPopouts.get(path);
  if (!record || !filePreviewPopoutCanDrive(previewWindow)) return false;
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  if (filePreviewPopoutReflectedScrollMatches(record, scroller)) return false;
  const coordinates = filePreviewPopoutScrollCoordinates(scroller);
  const owner = record.scrollSyncOwner;
  if (owner?.kind !== 'popout' && options.userIntent !== true) return false;
  if (owner?.kind !== 'popout' || owner.top !== coordinates.top || owner.left !== coordinates.left) {
    beginFilePreviewPopoutScrollOwner(record, {kind: 'popout', ...coordinates});
  }
  let synced = false;
  for (const panel of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    setFileEditorScrollSyncGuard(panel);
    const mode = fileEditorPanelMode(panel);
    const previewPane = fileEditorPanelPreviewPane(panel);
    const editorScroller = fileEditorPanelScroller(panel);
    const panelGeneration = Number(panel._splitScrollGeneration || 0);
    if (mode !== 'diff' && editorScroller && elementCanScroll(editorScroller) && syncScrollPositionByRatio(scroller, editorScroller, 'preview-popout-editor-reflection')) {
      rememberFileEditorReflectedScroll(panel, 'editor', 'popout', panelGeneration);
      rememberFilePreviewPopoutPanelReflection(record, panel, 'editor', editorScroller);
      synced = true;
    }
    if ((mode === 'preview' || mode === 'split') && previewPane && elementCanScroll(previewPane) && syncScrollPositionByRatio(scroller, previewPane, 'preview-popout-preview-reflection')) {
      rememberFileEditorReflectedScroll(panel, 'preview', 'popout', panelGeneration);
      rememberFilePreviewPopoutPanelReflection(record, panel, 'preview', previewPane);
      synced = true;
    }
  }
  return synced;
}

function scheduleFilePreviewPopoutScrollSync(path, previewWindow, options = {}) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  if (record.scrollSyncFrame) return true;
  const generation = Number(record.scrollSyncGeneration || 0);
  const run = () => {
    record.scrollSyncFrame = 0;
    if (Number(record.scrollSyncGeneration || 0) !== generation) return;
    syncFilePreviewPopoutScroll(path, previewWindow, options);
  };
  record.scrollSyncFrame = requestFilePreviewPopoutFrame(previewWindow, run);
  return true;
}

function previewPopoutBodyClassName() {
  const classes = PREVIEW_POPOUT_BODY_CLASSES.filter(name => (
    document.body?.classList?.contains(name)
    || (name === EDITOR_PREVIEW_VANILLA_CLASS && fileEditorPreviewDisplayMode === 'vanilla')
  ));
  classes.push('file-preview-popout-window');
  return classes.join(' ');
}

function previewPopoutVariableStyle() {
  const root = getComputedStyle(document.documentElement);
  const aliases = [
    ['--editor-scheme-bg', '--popout-editor-scheme-bg'],
    ['--editor-scheme-fg', '--popout-editor-scheme-fg'],
    ['--editor-scheme-muted', '--popout-editor-scheme-muted'],
    ['--editor-scheme-line', '--popout-editor-scheme-line'],
    ['--editor-scheme-panel', '--popout-editor-scheme-panel'],
    ['--editor-scheme-panel2', '--popout-editor-scheme-panel2'],
    ['--editor-scheme-preview-bg', '--popout-editor-scheme-preview-bg'],
  ];
  const copied = [panePopoutVariableStyle()];
  const aliased = aliases.map(([source, target]) => {
    const value = root.getPropertyValue(source).trim();
    return value ? `${target}: ${value}; ${source}: var(${target})` : '';
  });
  // The host transfers app tokens inline; preview shell aliases must follow them.
  aliased.push(
    '--bg: var(--editor-scheme-bg)', '--panel: var(--editor-scheme-panel)',
    '--panel2: var(--editor-scheme-panel2)', '--text: var(--editor-scheme-fg)',
    '--muted: var(--editor-scheme-muted)', '--line: var(--editor-scheme-line)',
    '--editor-preview-bg: var(--editor-scheme-preview-bg)',
  );
  if (fileEditorPreviewDisplayMode === 'vanilla') {
    aliased.push('--editor-preview-bg: var(--paint-white)', '--editor-scheme-fg: var(--markdown-html-light-text)');
  }
  return [...copied, ...aliased].filter(Boolean)
    .join('; ');
}

function previewPopoutToolbarHtml() {
  const themeState = editorPreviewThemeState();
  const themeShort = editorPreviewThemeShortLabel(themeState);
  const themeNext = editorPreviewThemeShortLabel(themeState === 'dark' ? 'light' : (themeState === 'light' ? 'vanilla' : 'dark'));
  return `
      <span class="file-editor-preview-font-panel" role="group" aria-label="${esc(t('common.previewFontSize'))}">
        <button type="button" data-editor-preview-font-step="-1" title="${esc(t('editor.previewFont.decrease'))}" aria-label="${esc(t('editor.previewFont.decrease'))}">A-</button>
        <span class="file-editor-preview-font-value" aria-live="polite">${esc(String(editorPreviewFontSize))}</span>
        <button type="button" data-editor-preview-font-step="1" title="${esc(t('editor.previewFont.increase'))}" aria-label="${esc(t('editor.previewFont.increase'))}">A+</button>
      </span>
      <button type="button" class="file-editor-theme-panel theme-with-label" data-preview-popout-theme data-editor-theme="${esc(themeState)}" data-editor-theme-short="${esc(themeShort)}" data-editor-theme-next="${esc(themeNext)}" title="${esc(editorThemeLabel())}" aria-label="${esc(editorThemeLabel())}"><span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span><span class="file-editor-theme-label">${esc(themeShort)}</span></button>`;
}

function snapshotRenderedPreviewContainer(scratch) {
  return {
    className: scratch.className,
    html: scratch.innerHTML,
    dataAttributes: previewSnapshotDataAttributes(scratch),
  };
}

function previewSnapshotDataAttributes(scratch) {
  const attributes = {};
  for (const name of scratch?.getAttributeNames?.() || []) {
    if (name.startsWith('data-')) attributes[name] = scratch.getAttribute(name) || '';
  }
  return attributes;
}

function previewSnapshotDataAttributesHtml(snapshot) {
  return Object.entries(snapshot?.dataAttributes || {})
    .map(([name, value]) => ` ${name}="${esc(value)}"`)
    .join('');
}

function applyPreviewSnapshotRoot(root, snapshot) {
  if (!root || !snapshot) return false;
  root.className = snapshot.className;
  for (const name of Array.from(root.getAttributeNames?.() || [])) {
    if (name.startsWith('data-') && name !== 'data-preview-root') root.removeAttribute(name);
  }
  for (const [name, value] of Object.entries(snapshot.dataAttributes || {})) {
    root.setAttribute(name, value);
  }
  root.innerHTML = snapshot.html;
  return true;
}

function previewSnapshotScratch(path, text, options = {}) {
  const scratch = document.createElement('div');
  scratch.className = 'file-editor-preview-pane-panel';
  if (options.attach === true && document.body) {
    scratch.dataset.previewSnapshotScratch = '1';
    Object.assign(scratch.style, {
      position: 'fixed',
      left: '-100000px',
      top: '0',
      width: '1px',
      height: '1px',
      overflow: 'hidden',
      visibility: 'hidden',
      pointerEvents: 'none',
    });
    document.body.appendChild(scratch);
  }
  renderEditorPreviewPane(scratch, path, text, {context: options.context || 'popout', snapshot: true});
  scratch.hidden = false;
  return scratch;
}

function renderedPreviewSnapshot(path, text) {
  return snapshotRenderedPreviewContainer(previewSnapshotScratch(path, text, {context: 'popout'}));
}

async function renderedPreviewSnapshotAsync(path, text) {
  const scratch = previewSnapshotScratch(path, text, {context: 'popout', attach: true});
  try {
    if (scratch._previewAsync && typeof scratch._previewAsync.then === 'function') {
      await scratch._previewAsync;
    }
    return snapshotRenderedPreviewContainer(scratch);
  } finally {
    releasePreviewSurfaceResources(scratch);
    scratch.remove();
  }
}

function applyFilePreviewPopoutAsync(path, previewWindow, previewGeneration, promise, apply) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  const pending = {previewGeneration, promise: null};
  record.previewAsync = pending;
  const completion = promise.then(snapshot => {
    if (!filePreviewPopoutGenerationMatches(path, previewWindow, previewGeneration) || previewWindow.closed) return false;
    return apply(snapshot);
  }).catch(() => false);
  pending.promise = completion;
  const finish = () => {
    if (record.previewAsync !== pending) return;
    record.previewAsync = null;
    scheduleFilePreviewPopoutEdgeConvergence(path, record);
  };
  completion.then(finish, finish);
  return true;
}

function filePreviewPopoutDocumentStyle() {
  return `
    html {
      min-height: 100%;
      margin: 0;
      overflow: auto;
    }
    body.file-preview-popout-window {
      min-height: 100%;
      height: auto;
      margin: 0;
      display: block !important;
      grid-template-rows: none !important;
      overflow: auto;
    }
    body.file-preview-popout-window {
      background: var(--editor-preview-bg, var(--bg, #ffffff));
      color: var(--text, #111827);
    }
    .file-preview-popout-shell {
      box-sizing: border-box;
      width: 100%;
      margin: 0 auto;
      padding: 64px 24px 36px;
    }
    .file-preview-popout-title {
      position: fixed;
      top: 0;
      left: 50%;
      z-index: var(--z-topbar);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      box-sizing: border-box;
      width: calc(100% - 48px);
      min-height: 32px;
      padding: 8px 0 12px;
      margin-bottom: 12px;
      transform: translateX(-50%);
      border-bottom: 1px solid var(--border, #d1d5db);
      background: var(--editor-preview-bg, var(--bg, #ffffff));
      color: var(--text, #111827);
      font: 600 13px/1.3 var(--font, system-ui, sans-serif);
      box-shadow: 0 1px 0 var(--editor-preview-bg, var(--bg, #ffffff));
    }
    .file-preview-popout-title-path {
      grid-column: 1;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      justify-self: start;
    }
    .file-preview-popout-title .file-editor-preview-font-panel {
      grid-column: 2;
      display: inline-flex;
      align-items: center;
      justify-self: center;
    }
    .file-preview-popout-title .file-editor-theme-panel {
      grid-column: 3;
      justify-self: end;
      min-width: 66px;
      width: auto;
      height: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      padding: 0 7px 0 4px;
    }
    .file-preview-popout-window .file-editor-preview-pane-panel {
      position: static !important;
      inset: auto !important;
      left: auto !important;
      right: auto !important;
      top: auto !important;
      bottom: auto !important;
      display: block !important;
      box-sizing: border-box;
      width: 100% !important;
      max-width: 100%;
      min-height: auto;
      max-height: none;
      height: auto;
      overflow: visible;
      padding: 0;
      border: 0;
      background: transparent;
    }
	    .file-preview-popout-window .file-editor-preview-pane-panel.file-editor-preview-zoom-shell {
	      display: grid !important;
	      grid-template-rows: auto minmax(0, 1fr);
	      height: clamp(360px, calc(100vh - 132px), 900px);
	      min-height: 360px;
	      overflow: hidden;
	      padding: 8px;
	    }
	    .file-preview-popout-window .file-editor-preview-zoom-viewport {
	      box-sizing: border-box;
	      width: 100%;
	      height: 100%;
	      max-width: 100%;
	      max-height: 100%;
	      min-width: 0;
	      min-height: 0;
	      overflow: auto;
	      overscroll-behavior: contain;
	    }
    .file-preview-popout-window:not(.editor-theme-light):not(.editor-preview-vanilla),
    .file-preview-popout-window:not(.editor-theme-light):not(.editor-preview-vanilla) .file-preview-popout-title {
      background: var(--markdown-preview-bg);
    }
    .file-preview-popout-window .markdown-source-anchor {
      display: none;
    }
    @media (max-width: 640px) {
      .file-preview-popout-shell { padding: 64px 14px 28px; }
      .file-preview-popout-title { width: calc(100% - 28px); }
    }
  `;
}

function filePreviewPopoutBodyHtml(path, snapshot) {
  return `<main class="file-preview-popout-shell">
    <header class="file-preview-popout-title" role="toolbar" aria-label="${esc(t('editor.toolbar.aria'))}">
      <span class="file-preview-popout-title-path">${esc(compactHomePath(path))}</span>
      ${previewPopoutToolbarHtml()}
    </header>
    <article data-preview-root${previewSnapshotDataAttributesHtml(snapshot)} class="${esc(snapshot.className)}">${snapshot.html}</article>
  </main>`;
}

function writeFilePreviewPopoutDocument(path, previewWindow, snapshot) {
  const doc = previewWindow?.document;
  if (!doc) return false;
  const written = writePanePopoutDocument(previewWindow, {
    title: t('preview.popout.title', {name: basenameOf(path)}),
    bodyClass: previewPopoutBodyClassName(),
    bodyStyle: previewPopoutVariableStyle(),
    style: filePreviewPopoutDocumentStyle(),
    bodyHtml: filePreviewPopoutBodyHtml(path, snapshot),
  });
  if (!written) return false;
  bindFilePreviewPopoutControls(path, previewWindow);
  return true;
}

function updateFilePreviewPopoutControls(path, previewWindow) {
  const doc = previewWindow?.document;
  if (!doc) return;
  doc.body?.setAttribute('style', previewPopoutVariableStyle());
  const themeButton = doc.querySelector('[data-preview-popout-theme]');
  if (themeButton) updateEditorThemeButton(themeButton, {includeVanilla: true});
  updateEditorPreviewFontControls(doc);
  hydratePreviewZoomSurfaces(doc.querySelector('[data-preview-root]') || doc);
}

function bindFilePreviewPopoutControls(path, previewWindow) {
  const doc = previewWindow?.document;
  if (!doc) return null;
  if (typeof previewWindow._yomuxPreviewControlsCleanup === 'function') {
    previewWindow._yomuxPreviewControlsCleanup();
  }
  const dispose = bindScopedOnce(doc, 'file-preview-popout-controls', scope => {
  const bind = (target, type, handler) => {
    if (!target?.addEventListener) return;
    const listenerOptions = type === 'scroll' || type === 'wheel' || type.startsWith('touch') ? {passive: true} : undefined;
    scope.ownEvent(`${type}-${String(scope.value('event-index') || 0)}`, target, type, handler, listenerOptions);
    scope.replace('event-index', Number(scope.value('event-index') || 0) + 1);
  };
  bind(doc.querySelector('[data-preview-popout-theme]'), 'click', event => {
    event.preventDefault();
    cycleEditorThemeMode();
  });
  bind(doc.querySelector('.file-editor-preview-font-panel'), 'click', event => {
    const button = event.target?.closest?.('[data-editor-preview-font-step]');
    if (!button) return;
    event.preventDefault();
    setEditorPreviewFontSize(editorPreviewFontSize + Number(button.dataset.editorPreviewFontStep || 0));
  });
  const record = filePreviewPopouts.get(path);
  const syncScroll = () => {
    const userIntent = filePreviewPopoutScrollInputActive(record);
    syncFilePreviewPopoutScroll(path, previewWindow, {forceEdges: true, userIntent});
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true, userIntent});
  };
  const scheduleUserScrollSync = () => {
    markFilePreviewPopoutScrollInput(record);
    claimFilePreviewPopoutUserScroll(record, previewWindow);
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true, userIntent: true});
  };
  const beginUserScroll = kind => () => {
    beginFilePreviewPopoutScrollInput(record, kind);
    claimFilePreviewPopoutUserScroll(record, previewWindow);
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true, userIntent: true});
  };
  const endUserScroll = kind => () => {
    endFilePreviewPopoutScrollInput(record, kind);
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true, userIntent: true});
  };
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  bind(previewWindow, 'scroll', syncScroll);
  bind(previewWindow, 'wheel', scheduleUserScrollSync);
  bind(previewWindow, 'pointerdown', beginUserScroll('pointer'));
  bind(previewWindow, 'pointerup', endUserScroll('pointer'));
  bind(previewWindow, 'pointercancel', endUserScroll('pointer'));
  bind(previewWindow, 'touchstart', beginUserScroll('touch'));
  bind(previewWindow, 'touchmove', scheduleUserScrollSync);
  bind(previewWindow, 'touchend', endUserScroll('touch'));
  bind(previewWindow, 'touchcancel', endUserScroll('touch'));
  bind(previewWindow, 'blur', () => {
    endFilePreviewPopoutScrollInput(record, 'pointer');
    endFilePreviewPopoutScrollInput(record, 'touch');
  });
  bind(previewWindow, 'keydown', scheduleUserScrollSync);
  bind(doc, 'scroll', syncScroll);
  bind(doc, 'wheel', scheduleUserScrollSync);
  bind(doc, 'pointerdown', beginUserScroll('pointer'));
  bind(doc, 'pointerup', endUserScroll('pointer'));
  bind(doc, 'pointercancel', endUserScroll('pointer'));
  bind(doc, 'touchstart', beginUserScroll('touch'));
  bind(doc, 'touchmove', scheduleUserScrollSync);
  bind(doc, 'touchend', endUserScroll('touch'));
  bind(doc, 'touchcancel', endUserScroll('touch'));
  bind(doc, 'keydown', scheduleUserScrollSync);
  bind(scroller, 'scroll', syncScroll);
  bind(scroller, 'wheel', scheduleUserScrollSync);
  updateFilePreviewPopoutControls(path, previewWindow);
  });
  previewWindow._yomuxPreviewControlsCleanup = dispose;
  return dispose;
}

function applyFilePreviewPopoutSnapshot(path, previewWindow, generation, snapshot) {
  const record = filePreviewPopouts.get(path);
  if (!record || !filePreviewPopoutGenerationMatches(path, previewWindow, generation)) return Promise.resolve(false);
  return scheduleFilePreviewPopoutApplyWhenIdle(record, () => {
    if (!filePreviewPopoutGenerationMatches(path, previewWindow, generation) || previewWindow.closed) return false;
    const doc = filePreviewPopoutDocument(previewWindow);
    const root = doc?.querySelector?.('[data-preview-root]');
    if (!root) return writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    const scroller = filePreviewPopoutScrollElement(previewWindow);
    const coordinates = filePreviewPopoutScrollCoordinates(scroller);
    const scrollOwner = createPassiveDeferredElementScrollOwner(scroller);
    applyPreviewSnapshotRoot(root, snapshot);
    doc.body.className = previewPopoutBodyClassName();
    updateFilePreviewPopoutControls(path, previewWindow);
    doc.title = t('preview.popout.title', {name: basenameOf(path)});
    restoreElementScrollPosition(scroller, coordinates.top, coordinates.left, {
      owner: 'preview-popout-snapshot-restore',
      previewSurface: 'popout',
      deferredOwner: scrollOwner,
    });
    scheduleFilePreviewPopoutEdgeConvergence(path, record);
    return true;
  });
}

function updateFilePreviewPopout(path, text) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  const previewWindow = record.window;
  const generation = bumpFilePreviewPopoutGeneration(path);
  if (!previewWindow || previewWindow.closed) {
    filePreviewPopouts.delete(path);
    return false;
  }
  const snapshot = renderedPreviewSnapshot(path, text);
  try {
    applyFilePreviewPopoutSnapshot(path, previewWindow, generation, snapshot);
    applyFilePreviewPopoutAsync(path, previewWindow, generation, renderedPreviewSnapshotAsync(path, text), asyncSnapshot => {
      return applyFilePreviewPopoutSnapshot(path, previewWindow, generation, asyncSnapshot);
    });
    return true;
  } catch (_) {
    filePreviewPopouts.delete(path);
    return false;
  }
}

function refreshFilePreviewPopouts() {
  for (const path of Array.from(filePreviewPopouts.keys())) {
    const state = fileState.get(path);
    if (state?.kind && editorPreviewModeAvailable(path, state)) updateFilePreviewPopout(path, state.content || '');
    else filePreviewPopouts.delete(path);
  }
}

function settleFilePreviewPopoutNavigationWrite(record, job, written) {
  if (!job || job.settled) return false;
  job.settled = true;
  if (job.timer) job.clearTimeout?.(job.timer);
  job.timer = 0;
  if (record?.navigationWrite === job) record.navigationWrite = null;
  job.resolve(Boolean(written));
  return true;
}

function retireFilePreviewPopoutNavigationWrite(record) {
  const job = record?.navigationWrite;
  return job ? settleFilePreviewPopoutNavigationWrite(record, job, false) : false;
}

function writeFilePreviewPopoutAfterNavigation(path, previewWindow, snapshot, previewGeneration) {
  const record = filePreviewPopouts.get(path);
  if (!record || !filePreviewPopoutGenerationMatches(path, previewWindow, previewGeneration)) return Promise.resolve(false);
  retireFilePreviewPopoutNavigationWrite(record);
  let resolveCompletion;
  const completion = new Promise(resolve => { resolveCompletion = resolve; });
  const job = {
    previewWindow,
    previewGeneration,
    resolve: resolveCompletion,
    settled: false,
    timer: 0,
    clearTimeout: typeof previewWindow.clearTimeout === 'function'
      ? previewWindow.clearTimeout.bind(previewWindow)
      : window.clearTimeout.bind(window),
  };
  record.navigationWrite = job;
  const write = () => {
    if (job.settled) return;
    if (record.navigationWrite !== job || !filePreviewPopoutGenerationMatches(path, previewWindow, previewGeneration) || previewWindow.closed) {
      settleFilePreviewPopoutNavigationWrite(record, job, false);
      return;
    }
    if (filePreviewPopoutScrollInputActive(record)) {
      const schedule = typeof previewWindow.setTimeout === 'function'
        ? previewWindow.setTimeout.bind(previewWindow)
        : window.setTimeout.bind(window);
      if (!job.timer) {
        job.timer = schedule(() => {
          job.timer = 0;
          write();
        }, filePreviewPopoutSnapshotRetryMs);
      }
      return;
    }
    const written = writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    if (written) previewWindow.focus?.();
    settleFilePreviewPopoutNavigationWrite(record, job, written);
  };
  try {
    if (previewWindow.location?.pathname === '/preview-popout' && previewWindow.document?.readyState === 'complete') {
      write();
      return completion;
    }
    previewWindow.addEventListener?.('load', write, {once: true});
    const schedule = typeof previewWindow.setTimeout === 'function'
      ? previewWindow.setTimeout.bind(previewWindow)
      : window.setTimeout.bind(window);
    schedule(write, 1000);
  } catch (_) {
    write();
  }
  return completion;
}

function writeFilePreviewPopoutWhenReady(path, previewWindow, text) {
  const generation = bumpFilePreviewPopoutGeneration(path);
  writeFilePreviewPopoutAfterNavigation(path, previewWindow, renderedPreviewSnapshot(path, text), generation);
  applyFilePreviewPopoutAsync(path, previewWindow, generation, renderedPreviewSnapshotAsync(path, text), snapshot => {
    return writeFilePreviewPopoutAfterNavigation(path, previewWindow, snapshot, generation);
  });
}

function openFilePreviewPopout(path, panel = null) {
  if (panel && fileEditorPanelState(panel)?.historical === true) return false;
  if (!path || !editorPreviewModeAvailable(path)) return false;
  const initialState = fileState.get(path);
  if (initialState?.kind === 'text') syncOpenFileContentFromPanels(path, panel);
  const state = fileState.get(path);
  if (!state || !editorPreviewModeAvailable(path, state)) return false;
  const existing = filePreviewPopouts.get(path)?.window;
  if (existing && !existing.closed) {
    updateFilePreviewPopout(path, state.content || '');
    existing.focus?.();
    return true;
  }
  const previewWindow = window.open(`/preview-popout?path=${encodeURIComponent(path)}`, `yomux-preview-${encodeURIComponent(path)}`, 'popup,width=980,height=900');
  if (!previewWindow) {
    statusErr(localizedHtml('status.previewPopoutBlocked'));
    return false;
  }
  try {
    filePreviewPopouts.set(path, {window: previewWindow});
    writeFilePreviewPopoutWhenReady(path, previewWindow, state.content || '');
    return true;
  } catch (error) {
    filePreviewPopouts.delete(path);
    try { previewWindow.close(); } catch (_) {}
    statusErr(localizedHtml('status.previewPopoutFailed', {error}));
    return false;
  }
}

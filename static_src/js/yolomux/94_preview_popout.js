// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// File preview popout window helpers split from 95_codemirror_editor.js.

const filePreviewPopouts = panePopoutNamespaceMap('file-preview');

function filePreviewPopoutsForPath(path) {
  const record = filePreviewPopouts.get(path);
  return record ? [record] : [];
}

function closeFilePreviewPopout(path) {
  const record = filePreviewPopouts.get(path);
  filePreviewPopouts.delete(path);
  const previewWindow = record?.window;
  if (!previewWindow || previewWindow.closed) return false;
  try { previewWindow.close?.(); } catch (_) {}
  return true;
}

function bumpFilePreviewPopoutGeneration(path) {
  const record = filePreviewPopouts.get(path);
  if (!record) return 0;
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

function syncScrollPositionByRatio(from, to) {
  if (!from || !to) return false;
  to.scrollTop = scrollSyncTargetPosition(from, to, 'top');
  to.scrollLeft = scrollSyncTargetPosition(from, to, 'left');
  return true;
}

function scrollElementAtVerticalEdge(element) {
  const maxTop = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  const current = Math.max(0, Number(element?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(element?.clientHeight || 0) * 0.01));
  return current <= edgeSnap || current >= maxTop - edgeSnap;
}

function syncFilePreviewPopoutFromPanel(path, record, panel, source) {
  if (!record) return false;
  const previewWindow = record.window;
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  const root = filePreviewPopoutPreviewRoot(previewWindow);
  const from = fileEditorSourceElement(panel, source);
  if (!scroller || !root || !from || !elementCanScroll(scroller)) return false;
  setFileEditorScrollSyncGuard(record);
  return syncScrollPositionByRatio(from, scroller);
}

function syncFilePreviewPopoutsFromPanel(panel, source) {
  const path = fileEditorPanelPath(panel);
  if (!path || !fileEditorSourceCanDrive(panel, source)) return false;
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
  const forceEdge = options.forceEdges === true && scrollElementAtVerticalEdge(scroller);
  if (!forceEdge && fileEditorScrollSyncBlocked(record)) return false;
  let synced = false;
  for (const panel of fileEditorPanelsForPath(path)) {
    setFileEditorScrollSyncGuard(panel);
    const mode = fileEditorPanelMode(panel);
    const previewPane = fileEditorPanelPreviewPane(panel);
    const editorScroller = fileEditorPanelScroller(panel);
    if (mode !== 'diff' && editorScroller && elementCanScroll(editorScroller)) synced = syncScrollPositionByRatio(scroller, editorScroller) || synced;
    if ((mode === 'preview' || mode === 'split') && previewPane && elementCanScroll(previewPane)) synced = syncScrollPositionByRatio(scroller, previewPane) || synced;
  }
  return synced;
}

function scheduleFilePreviewPopoutScrollSync(path, previewWindow, options = {}) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  if (record.scrollSyncFrame) return true;
  const run = () => {
    record.scrollSyncFrame = 0;
    syncFilePreviewPopoutScroll(path, previewWindow, options);
  };
  if (typeof previewWindow?.requestAnimationFrame === 'function') record.scrollSyncFrame = previewWindow.requestAnimationFrame(run);
  else if (typeof requestAnimationFrame === 'function') record.scrollSyncFrame = requestAnimationFrame(run);
  else record.scrollSyncFrame = setTimeout(run, 0);
  return true;
}

function previewPopoutBodyClassName() {
  const classes = PREVIEW_POPOUT_BODY_CLASSES.filter(name => document.body?.classList?.contains(name));
  classes.push('file-preview-popout-window');
  return classes.join(' ');
}

function previewPopoutVariableStyle() {
  const root = getComputedStyle(document.documentElement);
  const names = [
    '--active-accent', '--active-accent-rgb', '--active-accent-bright', '--active-accent-text',
    '--active-control-bg', '--active-control-border', '--active-control-text',
    '--editor-preview-font-size', '--editor-line-height',
    '--lt-editor-bg', '--lt-editor-preview-bg', '--lt-text', '--lt-muted', '--lt-line',
    '--lt-panel', '--lt-panel2', '--lt-markdown-heading', '--lt-markdown-heading-bg',
    '--lt-markdown-link', '--lt-markdown-strong', '--lt-markdown-emphasis',
    '--lt-code-inline', '--lt-code-inline-bg', '--lt-code-inline-border',
    '--markdown-preview-bg', '--markdown-code-block-bg',
    '--markdown-alert-caution-light-bg', '--markdown-alert-caution-dark-bg',
    '--markdown-html-dark-bg', '--markdown-html-dark-border', '--markdown-html-dark-text',
    '--markdown-html-dark-link', '--markdown-html-dark-code',
    '--markdown-html-dark-code-bg', '--markdown-html-dark-code-border',
    '--markdown-heading', '--markdown-heading-bg', '--markdown-link', '--markdown-strong',
    '--markdown-emphasis', '--code-inline', '--code-inline-bg', '--code-inline-border',
    '--code-keyword', '--code-control', '--code-atom', '--code-string', '--code-number', '--code-variable',
    '--code-function', '--code-type', '--code-property', '--code-tag', '--code-comment',
    '--code-invalid',
  ];
  const aliases = [
    ['--editor-scheme-bg', '--popout-editor-scheme-bg'],
    ['--editor-scheme-fg', '--popout-editor-scheme-fg'],
    ['--editor-scheme-muted', '--popout-editor-scheme-muted'],
    ['--editor-scheme-line', '--popout-editor-scheme-line'],
    ['--editor-scheme-panel', '--popout-editor-scheme-panel'],
    ['--editor-scheme-panel2', '--popout-editor-scheme-panel2'],
    ['--editor-scheme-preview-bg', '--popout-editor-scheme-preview-bg'],
  ];
  const copied = names
    .map(name => {
      const value = root.getPropertyValue(name).trim();
      return value ? `${name}: ${value}` : '';
    });
  const aliased = aliases.map(([source, target]) => {
    const value = root.getPropertyValue(source).trim();
    return value ? `${target}: ${value}` : '';
  });
  return [...copied, ...aliased].filter(Boolean)
    .join('; ');
}

function previewPopoutToolbarHtml() {
  return `
      <span class="file-editor-preview-font-panel" role="group" aria-label="${esc(t('editor.previewFont.aria'))}">
        <button type="button" data-editor-preview-font-step="-1" title="${esc(t('editor.previewFont.decrease'))}" aria-label="${esc(t('editor.previewFont.decrease'))}">A-</button>
        <span class="file-editor-preview-font-value" aria-live="polite">${esc(String(editorPreviewFontSize))}</span>
        <button type="button" data-editor-preview-font-step="1" title="${esc(t('editor.previewFont.increase'))}" aria-label="${esc(t('editor.previewFont.increase'))}">A+</button>
      </span>
      <button type="button" class="file-editor-theme-panel" data-preview-popout-theme title="${esc(editorThemeLabel())}" aria-label="${esc(editorThemeLabel())}"><span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span></button>`;
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
  renderEditorPreviewPane(scratch, path, text, {context: options.context || 'popout'});
  scratch.hidden = false;
  return scratch;
}

function renderedPreviewSnapshot(path, text) {
  return snapshotRenderedPreviewContainer(previewSnapshotScratch(path, text, {context: 'popout'}));
}

async function renderedPreviewSnapshotAsync(path, text) {
  const scratch = previewSnapshotScratch(path, text, {context: 'popout'});
  if (scratch._previewAsync && typeof scratch._previewAsync.then === 'function') {
    await scratch._previewAsync;
  }
  return snapshotRenderedPreviewContainer(scratch);
}

function writeFilePreviewPopoutDocument(path, previewWindow, snapshot) {
  const doc = previewWindow?.document;
  if (!doc) return false;
  const cssHref = currentStylesheetHref('yolomux.css') || '/static/yolomux.css';
  doc.open();
  doc.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title></title>
  <link rel="stylesheet" href="${esc(cssHref)}">
  <style>
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
      z-index: 1000;
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
    .file-preview-popout-window {
      --editor-scheme-bg: var(--popout-editor-scheme-bg);
      --editor-scheme-fg: var(--popout-editor-scheme-fg);
      --editor-scheme-muted: var(--popout-editor-scheme-muted);
      --editor-scheme-line: var(--popout-editor-scheme-line);
      --editor-scheme-panel: var(--popout-editor-scheme-panel);
      --editor-scheme-panel2: var(--popout-editor-scheme-panel2);
      --editor-scheme-preview-bg: var(--popout-editor-scheme-preview-bg);
      --bg: var(--editor-scheme-bg, #0f131a);
      --panel: var(--editor-scheme-panel, #151b24);
      --panel2: var(--editor-scheme-panel2, #1b2432);
      --text: var(--editor-scheme-fg, #e4e8ee);
      --muted: var(--editor-scheme-muted, #8b95a5);
      --line: var(--editor-scheme-line, #2a3444);
      --editor-preview-bg: var(--editor-scheme-preview-bg, var(--bg));
    }
    .file-preview-popout-window .markdown-body {
      color: var(--text, #111827);
      background: transparent;
    }
    .file-preview-popout-window .markdown-source-anchor {
      display: none;
      width: 0;
      height: 0;
      overflow: hidden;
    }
    .file-preview-popout-window:not(.editor-theme-light) {
      background: var(--markdown-preview-bg, #000000);
    }
    .file-preview-popout-window:not(.editor-theme-light) .file-preview-popout-title {
      background: var(--markdown-preview-bg, #000000);
      box-shadow: 0 1px 0 var(--markdown-preview-bg, #000000);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body {
      background: var(--markdown-preview-bg, #000000);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body pre {
      background: var(--markdown-code-block-bg);
    }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert {
      --markdown-heading: var(--markdown-html-light-text);
      --markdown-strong: var(--markdown-html-light-text);
      --markdown-emphasis: var(--markdown-html-light-text);
      --markdown-link: var(--markdown-html-light-link);
      --code-inline: var(--markdown-html-light-code);
      --code-inline-bg: var(--markdown-html-light-code-bg);
      --code-inline-border: var(--markdown-html-light-code-border);
      margin: 6px 0;
      padding: 8px 10px;
      color: var(--markdown-html-light-text);
      background: #fff8cc;
      border: 0;
      border-radius: var(--radius-control);
    }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert > :first-child { margin-top: 0; }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert > :last-child { margin-bottom: 0; }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert :is(p, ul, ol, pre) {
      margin-block: 2px;
    }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert :is(strong, em) {
      color: var(--markdown-html-light-text);
    }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert code {
      color: var(--markdown-html-light-code);
      background: var(--markdown-html-light-code-bg);
      border-color: var(--markdown-html-light-code-border);
    }
    .file-preview-popout-window .markdown-body blockquote.markdown-alert-caution {
      background: var(--markdown-alert-caution-light-bg);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body blockquote:is(.markdown-alert-warning, .markdown-alert-caution) {
      --markdown-heading: var(--markdown-html-dark-text);
      --markdown-strong: var(--markdown-html-dark-text);
      --markdown-emphasis: var(--markdown-html-dark-text);
      --markdown-link: var(--markdown-html-dark-link);
      --code-inline: var(--markdown-html-dark-code);
      --code-inline-bg: var(--markdown-html-dark-code-bg);
      --code-inline-border: var(--markdown-html-dark-code-border);
      color: var(--markdown-html-dark-text);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body blockquote.markdown-alert-warning {
      background: var(--markdown-html-dark-bg);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body blockquote.markdown-alert-caution {
      background: var(--markdown-alert-caution-dark-bg);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body blockquote:is(.markdown-alert-warning, .markdown-alert-caution) :is(strong, em) {
      color: var(--markdown-html-dark-text);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body blockquote:is(.markdown-alert-warning, .markdown-alert-caution) code {
      color: var(--markdown-html-dark-code);
      background: var(--markdown-html-dark-code-bg);
      border-color: var(--markdown-html-dark-code-border);
    }
    .file-preview-popout-window .markdown-body .markdown-html-light-bg,
    .file-preview-popout-window .markdown-body table.markdown-html-light-bg :is(th, td),
    .file-preview-popout-window .markdown-body table[bgcolor],
    .file-preview-popout-window .markdown-body table[bgcolor] :is(th, td),
    .file-preview-popout-window .markdown-body :is(th, td)[bgcolor] {
      --markdown-heading: var(--markdown-html-light-text);
      --markdown-strong: var(--markdown-html-light-text);
      --markdown-emphasis: var(--markdown-html-light-text);
      --markdown-link: var(--markdown-html-light-link);
      --code-inline: var(--markdown-html-light-code);
      --code-inline-bg: var(--markdown-html-light-code-bg);
      --code-inline-border: var(--markdown-html-light-code-border);
      color: var(--markdown-html-light-text) !important;
      border-color: transparent;
    }
    .file-preview-popout-window .markdown-body :is(th, td).markdown-html-light-bg,
    .file-preview-popout-window .markdown-body table.markdown-html-light-bg :is(th, td),
    .file-preview-popout-window .markdown-body table[bgcolor] :is(th, td),
    .file-preview-popout-window .markdown-body :is(th, td)[bgcolor] {
      padding: 10px;
    }
    .file-preview-popout-window .markdown-body .markdown-html-light-bg :is(strong, em),
    .file-preview-popout-window .markdown-body table.markdown-html-light-bg :is(strong, em),
    .file-preview-popout-window .markdown-body table[bgcolor] :is(strong, em),
    .file-preview-popout-window .markdown-body :is(th, td)[bgcolor] :is(strong, em) {
      color: var(--markdown-html-light-text);
    }
    .file-preview-popout-window .markdown-body .markdown-html-light-bg code,
    .file-preview-popout-window .markdown-body table.markdown-html-light-bg code,
    .file-preview-popout-window .markdown-body table[bgcolor] code,
    .file-preview-popout-window .markdown-body :is(th, td)[bgcolor] code {
      color: var(--markdown-html-light-code);
      background: var(--markdown-html-light-code-bg);
      border-color: var(--markdown-html-light-code-border);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body .markdown-html-light-bg,
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table.markdown-html-light-bg :is(th, td),
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table[bgcolor],
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table[bgcolor] :is(th, td),
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body :is(th, td)[bgcolor] {
      --markdown-heading: var(--markdown-html-dark-text);
      --markdown-strong: var(--markdown-html-dark-text);
      --markdown-emphasis: var(--markdown-html-dark-text);
      --markdown-link: var(--markdown-html-dark-link);
      --code-inline: var(--markdown-html-dark-code);
      --code-inline-bg: var(--markdown-html-dark-code-bg);
      --code-inline-border: var(--markdown-html-dark-code-border);
      color: var(--markdown-html-dark-text) !important;
      background: var(--markdown-html-dark-bg) !important;
      border-color: var(--markdown-html-dark-border);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body .markdown-html-light-bg :is(strong, em),
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table.markdown-html-light-bg :is(strong, em),
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table[bgcolor] :is(strong, em),
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body :is(th, td)[bgcolor] :is(strong, em) {
      color: var(--markdown-html-dark-text);
    }
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body .markdown-html-light-bg code,
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table.markdown-html-light-bg code,
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body table[bgcolor] code,
    .file-preview-popout-window:not(.editor-theme-light) .markdown-body :is(th, td)[bgcolor] code {
      color: var(--markdown-html-dark-code);
      background: var(--markdown-html-dark-code-bg);
      border-color: var(--markdown-html-dark-code-border);
    }
    .file-preview-popout-window .markdown-body pre code.hljs {
      color: var(--editor-scheme-fg, inherit) !important;
      background: transparent !important;
      border: 0;
      display: inline;
      overflow: visible;
      padding: 0;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-comment,
    .file-preview-popout-window .markdown-body pre code .hljs-quote {
      color: var(--code-comment) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-keyword,
    .file-preview-popout-window .markdown-body pre code .hljs-selector-tag,
    .file-preview-popout-window .markdown-body pre code .hljs-literal,
    .file-preview-popout-window .markdown-body pre code .hljs-section,
    .file-preview-popout-window .markdown-body pre code .hljs-doctag {
      color: var(--code-keyword) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-string,
    .file-preview-popout-window .markdown-body pre code .hljs-regexp,
    .file-preview-popout-window .markdown-body pre code .hljs-addition,
    .file-preview-popout-window .markdown-body pre code .hljs-template-variable {
      color: var(--code-string) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-number,
    .file-preview-popout-window .markdown-body pre code .hljs-symbol,
    .file-preview-popout-window .markdown-body pre code .hljs-bullet,
    .file-preview-popout-window .markdown-body pre code .hljs-attr {
      color: var(--code-number) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-title,
    .file-preview-popout-window .markdown-body pre code .hljs-title.function_,
    .file-preview-popout-window .markdown-body pre code .hljs-function .hljs-title {
      color: var(--code-function) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-built_in,
    .file-preview-popout-window .markdown-body pre code .hljs-type,
    .file-preview-popout-window .markdown-body pre code .hljs-class .hljs-title {
      color: var(--code-type) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-variable,
    .file-preview-popout-window .markdown-body pre code .hljs-params,
    .file-preview-popout-window .markdown-body pre code .hljs-name {
      color: var(--code-variable) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-property,
    .file-preview-popout-window .markdown-body pre code .hljs-attribute {
      color: var(--code-property) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-tag {
      color: var(--code-tag) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-deletion {
      color: var(--code-invalid) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-meta {
      color: var(--code-atom) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-keyword {
      color: var(--code-keyword) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-control {
      color: var(--code-control) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-string {
      color: var(--code-string) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-comment {
      color: var(--code-comment) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-number {
      color: var(--code-number) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-constant {
      color: var(--code-atom) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-builtin,
    .file-preview-popout-window .markdown-body pre code .code-function {
      color: var(--code-function) !important;
      font-weight: 700;
    }
    .file-preview-popout-window .markdown-body pre code .code-type,
    .file-preview-popout-window .markdown-body pre code .code-attr {
      color: var(--code-type) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-variable {
      color: var(--code-variable) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-property {
      color: var(--code-property) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-tag {
      color: var(--code-tag) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-invalid {
      color: var(--code-invalid) !important;
    }
    .file-preview-popout-window.editor-theme-light {
      --editor-scheme-bg: var(--lt-editor-bg);
      --editor-scheme-fg: var(--lt-text);
      --editor-scheme-muted: var(--lt-muted);
      --editor-scheme-line: var(--lt-line);
      --editor-scheme-panel: var(--lt-panel);
      --editor-scheme-panel2: var(--lt-panel2);
      --editor-scheme-preview-bg: var(--lt-editor-preview-bg);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body {
      --markdown-heading: var(--lt-markdown-heading);
      --markdown-heading-bg: var(--lt-markdown-heading-bg);
      --markdown-link: var(--lt-markdown-link);
      --markdown-strong: var(--lt-markdown-strong);
      --markdown-emphasis: var(--lt-markdown-emphasis);
      --code-inline: var(--lt-code-inline);
      --code-inline-bg: var(--lt-code-inline-bg);
      --code-inline-border: var(--lt-code-inline-border);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body pre {
      color: var(--lt-text);
      background: var(--lt-panel);
      border-color: var(--lt-line);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body pre code {
      color: inherit;
      background: transparent;
      border: 0;
    }
    @media (max-width: 640px) {
      .file-preview-popout-shell { padding: 64px 14px 28px; }
      .file-preview-popout-title { width: calc(100% - 28px); }
    }
  </style>
</head>
<body class="${esc(previewPopoutBodyClassName())}" style="${esc(previewPopoutVariableStyle())}">
  <main class="file-preview-popout-shell">
    <header class="file-preview-popout-title" role="toolbar" aria-label="${esc(t('editor.toolbar.aria'))}">
      <span class="file-preview-popout-title-path">${esc(compactHomePath(path))}</span>
      ${previewPopoutToolbarHtml()}
    </header>
    <article data-preview-root${previewSnapshotDataAttributesHtml(snapshot)} class="${esc(snapshot.className)}">${snapshot.html}</article>
  </main>
</body>
</html>`);
  doc.close();
  doc.title = t('preview.popout.title', {name: basenameOf(path)});
  doc._yolomuxPreviewControlsBound = false;
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
  if (!doc || doc._yolomuxPreviewControlsBound) return;
  doc._yolomuxPreviewControlsBound = true;
  if (typeof previewWindow._yolomuxPreviewControlsCleanup === 'function') {
    previewWindow._yolomuxPreviewControlsCleanup();
  }
  const cleanup = [];
  const bind = (target, type, handler) => {
    if (!target?.addEventListener) return;
    target.addEventListener(type, handler, {passive: true});
    cleanup.push(() => target.removeEventListener?.(type, handler));
  };
  previewWindow._yolomuxPreviewControlsCleanup = () => {
    while (cleanup.length) {
      try { cleanup.pop()(); } catch (_) {}
    }
  };
  doc.querySelector('[data-preview-popout-theme]')?.addEventListener('click', event => {
    event.preventDefault();
    cycleEditorThemeMode({includeVanilla: true});
  });
  doc.querySelector('.file-editor-preview-font-panel')?.addEventListener('click', event => {
    const button = event.target?.closest?.('[data-editor-preview-font-step]');
    if (!button) return;
    event.preventDefault();
    setEditorPreviewFontSize(editorPreviewFontSize + Number(button.dataset.editorPreviewFontStep || 0));
  });
  const syncScroll = () => {
    syncFilePreviewPopoutScroll(path, previewWindow, {forceEdges: true});
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true});
  };
  const scheduleScrollSync = () => scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true});
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  bind(previewWindow, 'scroll', syncScroll);
  bind(previewWindow, 'wheel', scheduleScrollSync);
  bind(doc, 'scroll', syncScroll);
  bind(doc, 'wheel', scheduleScrollSync);
  bind(scroller, 'scroll', syncScroll);
  bind(scroller, 'wheel', scheduleScrollSync);
  updateFilePreviewPopoutControls(path, previewWindow);
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
    const doc = previewWindow.document;
    const scroller = filePreviewPopoutScrollElement(previewWindow);
    const scrollTop = scroller?.scrollTop || 0;
    const scrollLeft = scroller?.scrollLeft || 0;
    const root = doc?.querySelector?.('[data-preview-root]');
    if (!root) return writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    applyPreviewSnapshotRoot(root, snapshot);
    doc.body.className = previewPopoutBodyClassName();
    updateFilePreviewPopoutControls(path, previewWindow);
    doc.title = t('preview.popout.title', {name: basenameOf(path)});
    restoreElementScrollPosition(scroller, scrollTop, scrollLeft);
    renderedPreviewSnapshotAsync(path, text).then(asyncSnapshot => {
      if (!filePreviewPopoutGenerationMatches(path, previewWindow, generation) || previewWindow.closed) return;
      const currentRoot = previewWindow.document?.querySelector?.('[data-preview-root]');
      if (!currentRoot) return;
      applyPreviewSnapshotRoot(currentRoot, asyncSnapshot);
      updateFilePreviewPopoutControls(path, previewWindow);
    }).catch(() => {});
    return true;
  } catch (_) {
    filePreviewPopouts.delete(path);
    return false;
  }
}

function refreshFilePreviewPopouts() {
  for (const path of Array.from(filePreviewPopouts.keys())) {
    const state = openFiles.get(path);
    if (state?.kind && editorPreviewModeAvailable(path, state)) updateFilePreviewPopout(path, state.content || '');
    else filePreviewPopouts.delete(path);
  }
}

function writeFilePreviewPopoutAfterNavigation(path, previewWindow, snapshot) {
  let written = false;
  const write = () => {
    if (written || !previewWindow || previewWindow.closed) return;
    written = true;
    writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    previewWindow.focus?.();
  };
  try {
    if (previewWindow.location?.pathname === '/preview-popout' && previewWindow.document?.readyState === 'complete') {
      write();
      return;
    }
    previewWindow.addEventListener?.('load', write, {once: true});
    window.setTimeout(write, 1000);
  } catch (_) {
    write();
  }
}

function writeFilePreviewPopoutWhenReady(path, previewWindow, text) {
  const generation = bumpFilePreviewPopoutGeneration(path);
  writeFilePreviewPopoutAfterNavigation(path, previewWindow, renderedPreviewSnapshot(path, text));
  renderedPreviewSnapshotAsync(path, text).then(snapshot => {
    if (!filePreviewPopoutGenerationMatches(path, previewWindow, generation) || previewWindow.closed) return;
    writeFilePreviewPopoutAfterNavigation(path, previewWindow, snapshot);
  }).catch(() => {});
}

function openFilePreviewPopout(path, panel = null) {
  if (!path || !editorPreviewModeAvailable(path)) return false;
  const initialState = openFiles.get(path);
  if (initialState?.kind === 'text') syncOpenFileContentFromPanels(path, panel);
  const state = openFiles.get(path);
  if (!state || !editorPreviewModeAvailable(path, state)) return false;
  const existing = filePreviewPopouts.get(path)?.window;
  if (existing && !existing.closed) {
    updateFilePreviewPopout(path, state.content || '');
    existing.focus?.();
    return true;
  }
  const previewWindow = window.open(`/preview-popout?path=${encodeURIComponent(path)}`, `yolomux-preview-${encodeURIComponent(path)}`, 'popup,width=980,height=900');
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

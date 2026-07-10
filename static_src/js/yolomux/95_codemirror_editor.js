// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Continuation of the editor module (split from 90_changes_editor.js to keep each partial under a
// readable size): the CodeMirror panel lifecycle, extensions, theming, and diff/preview rendering.
// Concatenated immediately after 90 by tools/static_build.py, so it shares the same bundle scope.

function destroyCodeMirrorPanel(panel) {
  panel?._cmResizeObserver?.disconnect?.();
  if (panel) panel._cmResizeObserver = null;
  panel?._diffOverviewViewportCleanup?.();
  if (panel) panel._diffOverviewWaitingForDeletedRows = false;
  // Clear the diff scrollbar overview so its red/green rail doesn't linger after switching to
  // edit/normal mode (only the diff build re-adds it via updateCodeMirrorDiffOverview).
  panel?.querySelector?.('.cm-diff-overview')?.remove();
  if (panel?._cmMergeView) {
    panel._cmMergeView.destroy();
    panel._cmMergeView = null;
  }
  if (panel?._cmView) {
    panel._cmView.destroy();
    panel._cmView = null;
  }
  if (panel) {
    panel._cmApi = null;
    panel._cmThemeCompartment = null;
    panel._cmEditorOptionCompartment = null;
    panel._cmEditorOptionConfig = null;
    panel._cmLocaleCompartment = null;
    panel._cmViews = [];
    panel._cmPath = '';
    panel._cmSignature = '';
    panel._cmMode = '';
    panel._cmPlainFallback = false;
  }
}

function codeMirrorPanelContent(panel) {
  return panel?._cmView?.state?.doc?.toString?.() ?? null;
}

function textFingerprint(text) {
  const source = String(text || '');
  let hash = 0;
  for (let index = 0; index < source.length; index += 1) {
    hash = ((hash << 5) - hash + source.charCodeAt(index)) | 0;
  }
  return `${source.length}:${hash}`;
}

function codeMirrorConfigSignature(path, options = {}) {
  return JSON.stringify({
    mode: options.mode || 'edit',
    layout: options.layout || '',
    original: options.original ? textFingerprint(options.original) : '',
    from: options.from || '',
    to: options.to || '',
    expand: options.expand === true,
    language: codeMirrorLanguageName(path),
    readOnly: readOnlyMode,
    // fix: the blame ViewPlugin is added/removed only at editor build time, so blame state must
    // be in the signature — otherwise toggling blame OFF reuses the existing view and the annotations
    // linger (and toggling ON wouldn't add them without an unrelated rebuild).
    blame: fileEditorBlameEnabled,
    blameAllLines: fileEditorBlameAllLines,
  });
}

function codeMirrorDiffLayout(_container) {
  // Always use the unified (inline) merge view at every pane width: deleted rows render as read-only
  // widgets with NO line number. The side-by-side MergeView numbers the old file (including deleted
  // rows), and @codemirror/merge exposes no public chunk access to suppress only those numbers, so a
  // wide pane previously showed numbered red lines (image 075). Unified guarantees the user's "no
  // number on red lines in ANY layout" requirement.
  return 'inline';
}

function codeMirrorReadOnlyExtensions(api, path, panel = null, options = {}) {
  return [
    codeMirrorLocaleExtensions(api, panel),
    api.drawSelection(),
    codeMirrorContextMenuSelectionExtension(api),
    api.highlightActiveLine(),
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    api.EditorState.readOnly.of(true),
    api.EditorView.editable.of(false),
    ...(options.plain ? [codeMirrorThemeOnlyExtensions(api, panel)] : [codeMirrorLanguageExtension(api, path), codeMirrorThemedExtensions(api, panel, path)]),
  ];
}

function captureCodeMirrorPanelViewState(panel, path) {
  const item = fileEditorPanelItem(panel) || fileEditorItemFor(path);
  if (item) captureFileEditorPanelViewState(item, panel);
}

function codeMirrorWorkingUpdateExtension(api, panel, path) {
  return api.EditorView.updateListener.of(update => {
    if (update.docChanged || update.selectionSet) {
      updateCodeMirrorCursorStatus(panel);
      captureCodeMirrorPanelViewState(panel, path);
    }
    if (update.selectionSet) scheduleShareScrollPublishForElement(update.view?.scrollDOM || panel);
    if (update.docChanged) {
      handleFileEditorContentChanged(panel, path, update.state.doc.toString(), {syntax: false});
    }
  });
}

function codeMirrorContextMenuSelectionExtension(api) {
  return safeCodeMirrorExtension('context menu selection', () => {
    let pending = null;
    let clearTimer = null;
    const handlers = api.EditorView.domEventHandlers({
      contextmenu(event, view) {
        // Chrome may collapse contenteditable's native DOM selection before opening its context menu.
        // CodeMirror then observes that DOM change and replaces its own selection, which is especially
        // visible in unified diffs where folded/deleted rows make the native selection discontinuous.
        const selection = view.state.selection;
        const position = view.posAtCoords({x: event.clientX, y: event.clientY});
        const clickedSelection = selection.ranges.some(range => !range.empty && position >= range.from && position <= range.to);
        if (!clickedSelection) return false;
        const captured = {selection};
        pending = captured;
        clearTimeout(clearTimer);
        clearTimer = setTimeout(() => {
          if (pending === captured) pending = null;
        }, 250);
        return false;
      },
    });
    const restore = api.EditorView.updateListener.of(update => {
      const captured = pending;
      if (!captured || !update.selectionSet || update.state.selection.eq(captured.selection)) return;
      pending = null;
      clearTimeout(clearTimer);
      queueMicrotask(() => {
        if (!update.view.dom?.isConnected) return;
        update.view.dispatch({selection: captured.selection});
        updateCodeMirrorCursorStatus(update.view.dom.closest('.file-editor-panel'));
      });
    });
    return [handlers, restore];
  });
}

function updateCodeMirrorViewPreservingState(view, update, options = {}) {
  if (!view || typeof update !== 'function') return false;
  const scrollDOM = view.scrollDOM;
  const scrollTop = scrollDOM?.scrollTop || 0;
  const scrollLeft = scrollDOM?.scrollLeft || 0;
  const selection = options.preserveSelection === false ? null : view.state.selection;
  update(selection);
  const currentSelection = view.state.selection;
  const selectionPreserved = !selection || currentSelection === selection || currentSelection?.eq?.(selection) === true;
  if (!selectionPreserved) {
    try { view.dispatch({selection}); } catch (_) {}
  }
  const restoreScroll = () => {
    if (!scrollDOM) return;
    scrollDOM.scrollTop = scrollTop;
    scrollDOM.scrollLeft = scrollLeft;
  };
  if (typeof view.requestMeasure === 'function') {
    view.requestMeasure({write: restoreScroll});
  } else {
    requestAnimationFrame(restoreScroll);
  }
  requestAnimationFrame(() => {
    restoreScroll();
    requestAnimationFrame(restoreScroll);
  });
  return true;
}

function syncCodeMirrorDocument(view, text, options = {}) {
  if (!view) return;
  const next = String(text || '');
  if (view.state.doc.toString() === next) return;
  if (options.cleanOnly && openFiles.get(options.path)?.dirty) return;
  const selection = view.state.selection;
  const selectionFits = selection?.ranges?.every(range => (
    range.anchor <= next.length && range.head <= next.length
  ));
  updateCodeMirrorViewPreservingState(view, preservedSelection => {
    view.dispatch({
      changes: {from: 0, to: view.state.doc.length, insert: next},
      ...(preservedSelection ? {selection: preservedSelection} : {}),
    });
  }, {preserveSelection: selectionFits});
}

function codeMirrorThemeExtensions(api, path) {
  return [
    codeMirrorHighlightExtension(api),
    codeMirrorHtmlSemanticEmphasisExtension(api, path),
    codeMirrorMarkdownStrongExtension(api, path),
    codeMirrorMarkdownFallbackSyntaxExtension(api, path),
    codeMirrorThemeExtension(api),
  ];
}

function codeMirrorThemedExtensions(api, panel, path) {
  const extensions = codeMirrorThemeExtensions(api, path);
  if (!panel || !api.Compartment) return extensions;
  panel._cmThemeCompartment = panel._cmThemeCompartment || new api.Compartment();
  return panel._cmThemeCompartment.of(extensions);
}

function codeMirrorThemeOnlyExtensions(api, panel) {
  const extensions = [codeMirrorThemeExtension(api)];
  if (!panel || !api.Compartment) return extensions;
  panel._cmThemeCompartment = panel._cmThemeCompartment || new api.Compartment();
  return panel._cmThemeCompartment.of(extensions);
}

function codeMirrorPlainEditableExtensions(api, panel, path, options = {}) {
  const save = options.save || (() => saveFileEditor(path, panel));
  const saveKeymap = safeCodeMirrorExtension('save keymap', () => api.keymap.of([{
    key: 'Mod-s',
    run() {
      save();
      return true;
    },
  }]));
  const findKeymap = safeCodeMirrorExtension('find keymap', () => (api.openSearchPanel ? api.keymap.of([{
    key: 'Mod-f',
    run(view) {
      return openCodeMirrorFindForView(api, view);
    },
  }]) : []));
  const defaultKeymap = safeCodeMirrorExtension('default keymap', () => api.keymap.of([
    api.indentWithTab,
    ...(Array.isArray(api.defaultKeymap) ? api.defaultKeymap : []),
    ...(Array.isArray(api.historyKeymap) ? api.historyKeymap : []),
    ...(Array.isArray(api.searchKeymap) ? api.searchKeymap : []),
  ].filter(Boolean)));
  return [
    codeMirrorLocaleExtensions(api, panel),
    safeCodeMirrorExtension('history', () => api.history?.()),
    safeCodeMirrorExtension('selection drawing', () => api.drawSelection()),
    codeMirrorContextMenuSelectionExtension(api),
    safeCodeMirrorExtension('drop cursor', () => api.dropCursor?.()),
    safeCodeMirrorExtension('active line', () => api.highlightActiveLine()),
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    safeCodeMirrorExtension('search', () => api.search({top: true})),
    codeMirrorSearchPanelEnhancementExtension(api),
    safeCodeMirrorExtension('search matches', () => api.highlightSelectionMatches?.()),
    saveKeymap,
    findKeymap,
    defaultKeymap,
    safeCodeMirrorExtension('read only', () => api.EditorState.readOnly.of(readOnlyMode)),
    safeCodeMirrorExtension('editable', () => api.EditorView.editable.of(!readOnlyMode)),
    codeMirrorThemedExtensions(api, panel, path),
    codeMirrorWorkingUpdateExtension(api, panel, path),
  ];
}

function codeMirrorEditorOptionExtensions(api, options = {}) {
  const extensions = [];
  if (options.lineNumbers !== false && fileEditorLineNumbersEnabled) {
    const lineNumbers = safeCodeMirrorExtension('line numbers', () => api.lineNumbers?.());
    const activeLineGutter = safeCodeMirrorExtension('active line gutter', () => api.highlightActiveLineGutter?.());
    extensions.push(...[lineNumbers, activeLineGutter].flat().filter(Boolean));
  }
  if (options.wrap !== false && fileEditorWrapEnabled) {
    extensions.push(...[codeMirrorLineWrappingExtension(api), codeMirrorWrapMarkerExtension(api)].flat().filter(Boolean));
  }
  return extensions;
}

function codeMirrorLineWrappingExtension(api) {
  if (api.EditorView?.lineWrapping) return api.EditorView.lineWrapping;
  return [
    safeCodeMirrorExtension('line wrapping content attributes', () => api.EditorView?.contentAttributes?.of?.({class: 'cm-lineWrapping'})),
    safeCodeMirrorExtension('line wrapping theme', () => api.EditorView?.theme?.({
      '.cm-content.cm-lineWrapping': {
        whiteSpace: 'break-spaces',
        wordBreak: 'break-word',
        overflowWrap: 'anywhere',
        flexShrink: '1',
      },
    })),
  ].flat().filter(Boolean);
}

function codeMirrorEditorOptionCompartmentExtensions(api, panel, options = {}) {
  const extensions = codeMirrorEditorOptionExtensions(api, options);
  if (!panel || !api.Compartment) return extensions;
  panel._cmEditorOptionCompartment = panel._cmEditorOptionCompartment || new api.Compartment();
  panel._cmEditorOptionConfig = {
    wrap: options.wrap !== false,
    lineNumbers: options.lineNumbers !== false,
  };
  return panel._cmEditorOptionCompartment.of(extensions);
}

function createEditableCodeMirrorState(api, panel, path, doc) {
  try {
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorExtensions(api, panel, path),
      }),
      plain: false,
    };
  } catch (error) {
    console.warn('CodeMirror language parser failed; retrying plain editable editor', error);
    if (panel) panel._cmThemeCompartment = null;
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorPlainEditableExtensions(api, panel, path),
      }),
      plain: true,
      error,
    };
  }
}

function trackCodeMirrorViews(panel, api, views) {
  if (!panel) return;
  panel._cmApi = api;
  panel._cmViews = views.filter(Boolean);
}

function reconfigureCodeMirrorPanelLocale(panel) {
  const api = panel?._cmApi;
  const compartment = panel?._cmLocaleCompartment;
  const views = Array.isArray(panel?._cmViews) ? panel._cmViews : [];
  if (!api || !compartment || !views.length) return false;
  const effect = compartment.reconfigure(codeMirrorLocaleExtensions(api, null));
  for (const view of views) {
    try {
      updateCodeMirrorViewPreservingState(view, selection => view.dispatch({effects: effect, selection}));
    } catch (_) {}
  }
  return true;
}

function reconfigureCodeMirrorPanelTheme(panel) {
  const api = panel?._cmApi;
  const path = panel?._cmPath;
  const compartment = panel?._cmThemeCompartment;
  const views = Array.isArray(panel?._cmViews) ? panel._cmViews : [];
  if (!api || !path || !compartment || !views.length) return false;
  const extensions = codeMirrorThemeExtensions(api, path);
  const effect = compartment.reconfigure(extensions);
  for (const view of views) {
    try { view.dispatch({effects: effect}); } catch (_) {}
  }
  return true;
}

function reconfigureCodeMirrorPanelEditorOptions(panel) {
  const api = panel?._cmApi;
  const compartment = panel?._cmEditorOptionCompartment;
  const views = Array.isArray(panel?._cmViews) ? panel._cmViews : [];
  if (!api || !compartment || !views.length) return false;
  const effect = compartment.reconfigure(codeMirrorEditorOptionExtensions(api, panel._cmEditorOptionConfig || {}));
  for (const view of views) {
    try { view.dispatch({effects: effect}); } catch (_) {}
  }
  updateCodeMirrorCursorStatus(panel);
  if (panel?._cmMode === 'diff') scheduleDiffOverviewRebuild(panel);
  return true;
}

function diffOverviewPercent(lineIndex, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const value = Math.max(0, Math.min(100, (Number(lineIndex || 0) / total) * 100));
  return value.toFixed(3);
}

function diffOverviewRemovedLineCount(diff) {
  let count = 0;
  let hasHunk = false;
  for (const line of String(diff || '').split('\n')) {
    if (/^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@/.test(line)) {
      hasHunk = true;
      continue;
    }
    if (!hasHunk || line.startsWith('+++') || line.startsWith('---') || line.startsWith('\\')) continue;
    if (line.startsWith('-')) count += 1;
  }
  return count;
}

function diffOverviewLineStarts(text) {
  const value = String(text ?? '');
  const starts = [0];
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) === 10) starts.push(index + 1);
  }
  return starts;
}

function diffOverviewLineNumberAt(starts, position) {
  const lineStarts = Array.isArray(starts) && starts.length ? starts : [0];
  const pos = Math.max(0, Number(position || 0));
  let lo = 0;
  let hi = lineStarts.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lineStarts[mid] <= pos) lo = mid + 1;
    else hi = mid - 1;
  }
  return Math.max(1, hi + 1);
}

function diffOverviewLineCountForRange(starts, from, end) {
  const start = Number(from);
  const finish = Number(end);
  if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return 0;
  return Math.max(0, diffOverviewLineNumberAt(starts, finish) - diffOverviewLineNumberAt(starts, start) + 1);
}

function diffOverviewChunkEnd(chunk, side) {
  const explicit = Number(chunk?.[`end${side}`]);
  if (Number.isFinite(explicit)) return explicit;
  const from = Number(chunk?.[`from${side}`]);
  const to = Number(chunk?.[`to${side}`]);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return from;
  return Math.max(from, to - 1);
}

function diffOverviewIsCodeMirrorChunk(chunk) {
  return chunk
    && Number.isFinite(Number(chunk.fromA))
    && Number.isFinite(Number(chunk.toA))
    && Number.isFinite(Number(chunk.fromB))
    && Number.isFinite(Number(chunk.toB));
}

function diffOverviewCodeMirrorChunks(view, panel = null) {
  const mergeChunks = panel?._cmMergeView?.chunks;
  if (Array.isArray(mergeChunks) && mergeChunks.length && mergeChunks.every(diffOverviewIsCodeMirrorChunk)) {
    return mergeChunks;
  }
  const values = view?.state?.values;
  if (!Array.isArray(values)) return null;
  for (const value of values) {
    if (Array.isArray(value) && value.length && value.every(diffOverviewIsCodeMirrorChunk)) return value;
  }
  return null;
}

function diffOverviewSortedCodeMirrorChunks(chunks) {
  return Array.isArray(chunks)
    ? chunks.filter(diffOverviewIsCodeMirrorChunk).sort((a, b) => Number(a.fromB) - Number(b.fromB) || Number(a.fromA) - Number(b.fromA))
    : [];
}

function mergeDiffOverviewBand(bands, kind, start, end) {
  if (end <= start) return;
  const last = bands[bands.length - 1];
  if (last && last.kind === kind && last.end === start) {
    last.end = end;
  } else {
    bands.push({kind, start, end});
  }
}

function diffOverviewLineHeight(view, container) {
  const measured = Number(view?.defaultLineHeight || 0);
  if (Number.isFinite(measured) && measured > 0) return measured;
  const content = view?.contentDOM || container?.querySelector?.('.cm-content');
  if (content && typeof getComputedStyle === 'function') {
    const lineHeight = Number.parseFloat(getComputedStyle(content).lineHeight || '');
    if (Number.isFinite(lineHeight) && lineHeight > 0) return lineHeight;
  }
  return 20;
}

function diffOverviewPrefixWeights(weights) {
  const prefix = [0];
  for (let index = 1; index < weights.length; index += 1) {
    prefix[index] = prefix[index - 1] + Math.max(1, Number(weights[index] || 1));
  }
  return prefix;
}

function diffOverviewRangeWeight(prefixWeights, startLine, endLine) {
  const lastLine = Math.max(0, prefixWeights.length - 1);
  const start = Math.max(1, Math.min(lastLine, Number(startLine || 1)));
  const end = Math.max(0, Math.min(lastLine, Number(endLine || 0)));
  if (end < start) return 0;
  return prefixWeights[end] - prefixWeights[start - 1];
}

function diffOverviewLineModel(text, view = null, container = null, lineCountOverride = null) {
  const value = String(text ?? '');
  const starts = diffOverviewLineStarts(value);
  const lines = value.split('\n');
  const lineCount = Math.max(1, Number(lineCountOverride || starts.length || lines.length || 1));
  const weights = [0];
  for (let lineNumber = 1; lineNumber <= lineCount; lineNumber += 1) {
    weights.push(diffOverviewEstimatedTextLineWeight(lines[lineNumber - 1] || '', view, container));
  }
  const prefixWeights = diffOverviewPrefixWeights(weights);
  return {
    starts,
    lineCount,
    prefixWeights,
    rows: prefixWeights[lineCount] || lineCount,
  };
}

function diffOverviewLineModelRangeWeight(model, startLine, endLine) {
  return model ? diffOverviewRangeWeight(model.prefixWeights, startLine, endLine) : 0;
}

function diffOverviewWrappingEnabled(view, container) {
  return view?.contentDOM?.classList?.contains?.('cm-lineWrapping')
    || Boolean(container?.querySelector?.('.cm-content.cm-lineWrapping'));
}

function diffOverviewContentWidth(view, container) {
  const contentWidth = Number(view?.contentDOM?.getBoundingClientRect?.().width || view?.contentDOM?.clientWidth || 0);
  if (Number.isFinite(contentWidth) && contentWidth > 0) return contentWidth;
  const content = container?.querySelector?.('.cm-content');
  const fallbackWidth = Number(content?.getBoundingClientRect?.().width || content?.clientWidth || 0);
  if (Number.isFinite(fallbackWidth) && fallbackWidth > 0) return fallbackWidth;
  const scrollerWidth = Number(view?.scrollDOM?.clientWidth || container?.querySelector?.('.cm-scroller')?.clientWidth || 0);
  return Number.isFinite(scrollerWidth) && scrollerWidth > 0 ? scrollerWidth : 1;
}

function diffOverviewCharacterWidth(view) {
  const measured = Number(view?.defaultCharacterWidth || 0);
  return Number.isFinite(measured) && measured > 0 ? measured : 8;
}

function diffOverviewEstimatedTextLineWeight(text, view, container) {
  if (!diffOverviewWrappingEnabled(view, container)) return 1;
  const width = Math.max(1, diffOverviewContentWidth(view, container));
  const charWidth = Math.max(1, diffOverviewCharacterWidth(view));
  const columns = Math.max(1, Math.floor(width / charWidth));
  const visualColumns = Math.max(1, String(text ?? '').replace(/\t/g, '    ').length);
  return Math.max(1, Math.ceil(visualColumns / columns));
}

function diffOverviewRowsFromCodeMirrorLineModels(chunks, currentModel, originalModel, options = {}) {
  const validChunks = diffOverviewSortedCodeMirrorChunks(chunks);
  if (!validChunks.length || !currentModel) return null;
  const includeRemoved = options.includeRemoved !== false;
  const bands = [];
  let row = 0;
  let currentLine = 1;
  let deletedRows = 0;

  for (const chunk of validChunks) {
    const fromA = Number(chunk.fromA);
    const toA = Number(chunk.toA);
    const fromB = Number(chunk.fromB);
    const toB = Number(chunk.toB);
    const startCurrentLine = Math.min(currentModel.lineCount, Math.max(1, diffOverviewLineNumberAt(currentModel.starts, fromB)));
    if (startCurrentLine > currentLine) {
      row += diffOverviewLineModelRangeWeight(currentModel, currentLine, startCurrentLine - 1);
      currentLine = startCurrentLine;
    }

    if (includeRemoved && originalModel && toA > fromA) {
      const removedCount = diffOverviewLineCountForRange(originalModel.starts, fromA, diffOverviewChunkEnd(chunk, 'A'));
      const removedStartLine = diffOverviewLineNumberAt(originalModel.starts, fromA);
      const removedWeight = diffOverviewLineModelRangeWeight(originalModel, removedStartLine, removedStartLine + removedCount - 1);
      mergeDiffOverviewBand(bands, 'remove', row, row + removedWeight);
      row += removedWeight;
      deletedRows += removedWeight;
    }

    if (toB > fromB) {
      const insertedEnd = diffOverviewChunkEnd(chunk, 'B');
      const insertedEndLine = Math.min(currentModel.lineCount, Math.max(startCurrentLine, diffOverviewLineNumberAt(currentModel.starts, insertedEnd)));
      const insertedWeight = diffOverviewLineModelRangeWeight(currentModel, startCurrentLine, insertedEndLine);
      mergeDiffOverviewBand(bands, 'add', row, row + insertedWeight);
      row += insertedWeight;
      currentLine = Math.max(currentLine, insertedEndLine + 1);
    }
  }

  if (currentLine <= currentModel.lineCount) row += diffOverviewLineModelRangeWeight(currentModel, currentLine, currentModel.lineCount);
  return {
    bands,
    currentLineCount: currentModel.lineCount,
    currentRows: currentModel.rows,
    deletedRows,
    totalRows: Math.max(row, currentModel.rows + deletedRows, 1),
  };
}

function diffOverviewRowsFromCodeMirrorChunks(chunks, currentText, originalText) {
  return diffOverviewRowsFromCodeMirrorLineModels(
    chunks,
    diffOverviewLineModel(currentText),
    diffOverviewLineModel(originalText),
  );
}

function diffOverviewRowsFromCodeMirrorRenderedWeights(view, chunks, currentText, originalText, container = null) {
  const doc = view?.state?.doc;
  const currentLineCount = Math.max(1, Number(doc?.lines || diffOverviewLineStarts(currentText).length || 1));
  const rows = diffOverviewRowsFromCodeMirrorLineModels(
    chunks,
    diffOverviewLineModel(currentText, view, container, currentLineCount),
    diffOverviewLineModel(originalText, view, container),
  );
  if (rows) rows.renderedWeights = true;
  return rows;
}

function diffOverviewScrollLooksCurrentOnly(rows, scrollTarget, view, container) {
  const deletedRows = Number(rows?.deletedRows || 0);
  if (deletedRows <= 0 || !scrollTarget) return false;
  const totalRows = Number(rows?.totalRows || 0);
  const currentRows = Number(rows?.currentRows || rows?.currentLineCount || 0);
  if (!Number.isFinite(totalRows) || !Number.isFinite(currentRows) || totalRows <= currentRows) return false;
  const scrollHeight = Number(scrollTarget.scrollHeight || 0);
  if (!Number.isFinite(scrollHeight) || scrollHeight <= 0) return false;
  const scrollRows = scrollHeight / diffOverviewLineHeight(view, container);
  const threshold = currentRows + (totalRows - currentRows) * 0.5;
  return scrollRows < threshold;
}

function diffOverviewBandsFromUnifiedDiff(diff, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const bands = [];
  let lineIndex = 0;
  let newLine = 1;
  let hasHunk = false;
  const appendLine = kind => {
    const start = Math.max(0, Math.min(total, lineIndex));
    const end = Math.max(start, Math.min(total, lineIndex + 1));
    if (end <= start) return;
    mergeDiffOverviewBand(bands, kind, start, end);
  };
  for (const line of String(diff || '').split('\n')) {
    const hunk = /^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
    if (hunk) {
      const nextNewLine = Math.max(1, Number(hunk[1]) || 1);
      lineIndex += Math.max(0, nextNewLine - newLine);
      newLine = nextNewLine;
      hasHunk = true;
      continue;
    }
    if (!hasHunk || line.startsWith('+++') || line.startsWith('---') || line.startsWith('\\')) continue;
    if (line.startsWith('+')) {
      appendLine('add');
      newLine += 1;
      lineIndex += 1;
    } else if (line.startsWith('-')) {
      appendLine('remove');
      lineIndex += 1;
    } else {
      newLine += 1;
      lineIndex += 1;
    }
  }
  return bands;
}

function buildDiffOverviewGradientFromBands(bands, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const stops = [];
  let lastStop = 0;
  const appendTransparent = until => {
    if (until > lastStop) stops.push(`transparent ${diffOverviewPercent(lastStop, total)}% ${diffOverviewPercent(until, total)}%`);
  };
  for (const band of Array.isArray(bands) ? bands : []) {
    const start = Math.max(0, Math.min(total, Number(band?.start || 0)));
    const end = Math.max(start, Math.min(total, Number(band?.end || 0)));
    if (end <= start) continue;
    appendTransparent(start);
    const color = band.kind === 'add' ? '#38d878' : '#ff5d6c';
    stops.push(`${color} ${diffOverviewPercent(start, total)}% ${diffOverviewPercent(end, total)}%`);
    lastStop = end;
  }
  if (!stops.some(stop => !stop.startsWith('transparent'))) return null;
  appendTransparent(total);
  return `linear-gradient(to bottom, ${stops.join(', ')})`;
}

function buildDiffOverviewGradient(diff, totalLines) {
  return buildDiffOverviewGradientFromBands(diffOverviewBandsFromUnifiedDiff(diff, totalLines), totalLines);
}

function codeMirrorDiffOverviewScrollTarget(view, container) {
  return view?.scrollDOM || container?.querySelector?.('.cm-scroller') || container?.querySelector?.('.cm-mergeView') || null;
}

function updateCodeMirrorDiffOverviewGeometry(overview, scrollTarget) {
  if (!overview || !scrollTarget?.getBoundingClientRect || !overview.parentElement?.getBoundingClientRect) return;
  const containerRect = overview.parentElement.getBoundingClientRect();
  const scrollRect = scrollTarget.getBoundingClientRect();
  const top = Math.max(0, scrollRect.top - containerRect.top);
  const height = Math.max(1, Number(scrollTarget.clientHeight || 0));
  overview.style.top = `${top}px`;
  overview.style.bottom = 'auto';
  overview.style.height = `${height}px`;
}

function updateCodeMirrorDiffOverviewViewport(viewport, scrollTarget) {
  if (!viewport || !scrollTarget) return;
  updateCodeMirrorDiffOverviewGeometry(viewport.parentElement, scrollTarget);
  const scrollHeight = Math.max(1, Number(scrollTarget.scrollHeight || 0));
  const clientHeight = Math.max(1, Number(scrollTarget.clientHeight || 0));
  const scrollTop = Math.max(0, Number(scrollTarget.scrollTop || 0));
  const top = Math.max(0, Math.min(100, (scrollTop / scrollHeight) * 100));
  const height = Math.max(2, Math.min(100 - top, (clientHeight / scrollHeight) * 100));
  viewport.style.top = `${top}%`;
  viewport.style.height = `${height}%`;
}

function installCodeMirrorDiffOverviewViewport(panel, overview, scrollTarget) {
  panel?._diffOverviewViewportCleanup?.();
  if (!overview || !scrollTarget) return;
  const viewport = document.createElement('div');
  viewport.className = 'cm-diff-overview-viewport';
  overview.appendChild(viewport);
  const update = () => updateCodeMirrorDiffOverviewViewport(viewport, scrollTarget);
  update();
  scrollTarget.addEventListener?.('scroll', update, {passive: true});
  if (panel) {
    panel._diffOverviewViewportCleanup = () => {
      scrollTarget.removeEventListener?.('scroll', update);
      if (viewport.parentElement) viewport.remove();
      panel._diffOverviewViewportCleanup = null;
    };
  }
}

function updateCodeMirrorDiffOverview(panel, container, state, currentText, original) {
  // Remember the inputs so a fold expand/collapse can rebuild the viewport indicator against the
  // current scroll surface; the red/green rows themselves are a single linear-gradient.
  if (panel) panel._diffOverviewCtx = {container, state, currentText, original};
  container?.querySelector?.('.cm-diff-overview')?.remove();
  panel?._diffOverviewViewportCleanup?.();
  if (!fileEditorDiffExpandUnchangedForItem(fileEditorPanelItem(panel))) return;
  const view = panel?._cmView;
  const scrollTarget = codeMirrorDiffOverviewScrollTarget(view, container);
  const currentLineCount = Math.max(String(currentText || '').split('\n').length, 1);
  const chunks = diffOverviewCodeMirrorChunks(view, panel);
  let chunkRows = diffOverviewRowsFromCodeMirrorRenderedWeights(view, chunks, currentText, original, container)
    || diffOverviewRowsFromCodeMirrorChunks(chunks, currentText, original);
  if (!chunkRows && view) {
    scheduleDiffOverviewReadinessRebuild(panel);
    return;
  }
  if (diffOverviewScrollLooksCurrentOnly(chunkRows, scrollTarget, view, container)) {
    if (panel && !panel._diffOverviewWaitingForDeletedRows) {
      panel._diffOverviewWaitingForDeletedRows = true;
      scheduleDiffOverviewSettledRebuild(panel);
    }
    return;
  }
  if (panel) panel._diffOverviewWaitingForDeletedRows = false;
  if (panel) panel._diffOverviewReadinessRetries = 0;
  const fallbackRows = {
    bands: diffOverviewBandsFromUnifiedDiff(state?.diff || '', currentLineCount + diffOverviewRemovedLineCount(state?.diff || '')),
    totalRows: Math.max(currentLineCount + diffOverviewRemovedLineCount(state?.diff || ''), 1),
  };
  const rows = chunkRows || fallbackRows;
  const gradient = buildDiffOverviewGradientFromBands(rows.bands, rows.totalRows);
  if (!gradient || !container) return;
  const overview = document.createElement('div');
  overview.className = 'cm-diff-overview';
  overview.setAttribute('aria-hidden', 'true');
  overview.style.background = gradient;
  container.appendChild(overview);
  installCodeMirrorDiffOverviewViewport(panel, overview, scrollTarget);
}

function scheduleDiffOverviewReadinessRebuild(panel) {
  if (!panel || panel._diffOverviewReadinessQueued) return;
  const attempts = Number(panel._diffOverviewReadinessRetries || 0);
  if (attempts >= 6) return;
  panel._diffOverviewReadinessRetries = attempts + 1;
  panel._diffOverviewReadinessQueued = true;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      panel._diffOverviewReadinessQueued = false;
      scheduleDiffOverviewRebuild(panel);
    });
  });
}

function scheduleDiffOverviewSettledRebuild(panel) {
  if (!panel || panel._diffOverviewSettledQueued) return;
  panel._diffOverviewSettledQueued = true;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      panel._diffOverviewSettledQueued = false;
      scheduleDiffOverviewRebuild(panel);
    });
  });
}

// B3: rebuild the diff overview whenever the editor's rendered geometry changes (a fold expanding/
// collapsing fires heightChanged). Debounced via rAF.
function scheduleDiffOverviewRebuild(panel) {
  const ctx = panel?._diffOverviewCtx;
  if (!ctx || panel._diffOverviewRebuildQueued) return;
  panel._diffOverviewRebuildQueued = true;
  requestAnimationFrame(() => {
    panel._diffOverviewRebuildQueued = false;
    if (panel._diffOverviewCtx && panel._cmMode === 'diff') {
      const c = panel._diffOverviewCtx;
      updateCodeMirrorDiffOverview(panel, c.container, c.state, c.currentText, c.original);
    }
  });
}

function codeMirrorDiffOverviewListener(api, panel) {
  return api.EditorView.updateListener.of(update => {
    if (update.geometryChanged || update.heightChanged) scheduleDiffOverviewRebuild(panel);
  });
}

function installCodeMirrorDiffCollapsedScrollGuard(panel, container) {
  if (!container || container.dataset.diffCollapsedScrollGuard === 'true') return;
  container.dataset.diffCollapsedScrollGuard = 'true';
  container.addEventListener('wheel', event => {
    if (!event.target?.closest?.('.cm-collapsedLines')) return;
    const scrollTarget = event.target.closest('.cm-scroller') || event.target.closest('.cm-mergeView') || panel._cmView?.scrollDOM;
    if (!scrollTarget) return;
    const before = scrollTarget.scrollTop;
    scrollTarget.scrollTop += event.deltaY;
    if (scrollTarget.scrollTop !== before) event.preventDefault();
  }, {passive: false});
}

function installCodeMirrorDiffResizeObserver(panel, item, path, container) {
  if (!window.ResizeObserver || panel._cmResizeObserver) return;
  let frame = 0;
  panel._cmResizeObserver = new ResizeObserver(() => {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (editorViewModeFor(path, item) !== 'diff') return;
      const nextLayout = codeMirrorDiffLayout(container);
      if (nextLayout !== panel._cmDiffLayout) renderFileEditorPanel(panel, item);
    });
  });
  panel._cmResizeObserver.observe(container);
}

async function ensureCodeMirrorDiffPanel(panel, item, path, state) {
  const container = panel.querySelector('.file-editor-codemirror-panel');
  if (!container) return false;
  const generation = (panel._cmGeneration || 0) + 1;
  panel._cmGeneration = generation;
  container.hidden = false;
  container.classList.add('file-editor-diff-codemirror');
  // Await the deduped diff load in this generation. Falling back to a temporary edit view here creates
  // a second render path that can overwrite the diff view after the payload arrives.
  if (state.diffLoading && state._diffLoadingPromise) {
    setFileEditorPanelStatus(panel, t('editor.diffLoading'), '');
    await state._diffLoadingPromise;
    if (panel._cmGeneration !== generation) return null;
  } else if (!state.diffLoaded && !state.diffUnavailable) {
    setFileEditorPanelStatus(panel, t('editor.diffLoading'), '');
    await refreshOpenFileDiff(path, {silent: true, renderOnComplete: false});
    if (panel._cmGeneration !== generation) return null;
  }
  if (!fileStateCanRenderDiffView(path, state)) {
    if (state.diffUnavailable) {
      const msg = t('editor.diffUnavailable', {error: state.diffError || t('common.unknown')});
      setFileEditorPanelStatus(panel, msg, 'warn');
      return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
    }
    return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
  }
  const original = String(state.diffOriginal || '');
  try {
    const api = await loadCodeMirrorApi();
    if (panel._cmGeneration !== generation) return null;
    if (!api.MergeView || !api.unifiedMergeView) {
      setFileEditorPanelStatus(panel, t('editor.codemirrorMergeUnavailable'), 'error');
      return false;
    }
    const layout = codeMirrorDiffLayout(container);
    const expandUnchanged = fileEditorDiffExpandUnchangedForItem(item);
    const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';
    const currentText = diffTargetIsCurrent ? String(state.content || '') : String(state.diffWorking || '');
    const diffEditsAllowed = diffTargetIsCurrent;
    const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef, expand: expandUnchanged});
    installCodeMirrorDiffCollapsedScrollGuard(panel, container);
    if (panel._cmView && panel._cmMode === 'diff' && panel._cmSignature === signature) {
      installCodeMirrorDiffResizeObserver(panel, item, path, container);
      if (layout === 'side') {
        syncCodeMirrorDocument(panel._cmMergeView?.a, original);
        syncCodeMirrorDocument(panel._cmMergeView?.b, currentText, {cleanOnly: true, path});
      } else {
        syncCodeMirrorDocument(panel._cmView, currentText, {cleanOnly: true, path});
      }
      updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
      scheduleDiffOverviewSettledRebuild(panel);
      restoreFileEditorPanelViewState(item, panel);
      updateCodeMirrorCursorStatus(panel);
      focusFileEditorPanelIfReady(panel, item);
      return true;
    }
    captureFileEditorPanelViewState(item, panel);
    destroyCodeMirrorPanel(panel);
    container.replaceChildren();
    panel._cmDiffLayout = layout;
    installCodeMirrorDiffResizeObserver(panel, item, path, container);
    installCodeMirrorDiffCollapsedScrollGuard(panel, container);
    if (layout === 'side') {
      panel._cmMergeView = new api.MergeView({
        a: {
          doc: original,
          extensions: [
            api.drawSelection(),
            codeMirrorContextMenuSelectionExtension(api),
            api.highlightActiveLine(),
            ...(fileEditorLineNumbersEnabled ? [api.lineNumbers(), api.highlightActiveLineGutter()] : []),
            api.EditorState.readOnly.of(true),
            api.EditorView.editable.of(false),
            codeMirrorLanguageExtension(api, path),
            codeMirrorThemedExtensions(api, panel, path),
          ],
        },
        b: {
          doc: currentText,
          extensions: [
            ...(diffEditsAllowed
              ? [
                  ...codeMirrorExtensions(api, panel, path),
                  codeMirrorWorkingUpdateExtension(api, panel, path),
                ]
              : codeMirrorReadOnlyExtensions(api, path, panel)),
            // B3: panel._cmView is this `b` editor (side-by-side), so the overview rebuild listener lives here.
            codeMirrorDiffOverviewListener(api, panel),
          ],
        },
        parent: container,
        revertControls: 'a-to-b',
        // show each change as TWO uniform full lines (old solid red + new solid green) with
        // NO intra-line word/token highlight — even a 1-char edit shows whole-line red + whole-line green.
        highlightChanges: false,
        gutter: true,
        // B4: expand-all omits collapseUnchanged so every unchanged line shows; else collapse the runs.
        ...(expandUnchanged ? {} : {collapseUnchanged: {margin: 3, minSize: 8}}),
      });
      panel._cmView = panel._cmMergeView.b;
      trackCodeMirrorViews(panel, api, [panel._cmMergeView.a, panel._cmMergeView.b]);
      panel._cmMergeView.b.scrollDOM?.addEventListener('scroll', () => {
        scheduleFileEditorSplitScrollSync(panel, 'editor');
        scheduleFileEditorPanelViewStateCapture(item, panel);
      });
    } else {
      const unifiedMergeOptions = {
        original,
        // full-line red/green only, no intra-line token highlight (see MergeView above).
        highlightChanges: false,
        gutter: true,
        mergeControls: !readOnlyMode && diffEditsAllowed,
        // B4: expand-all omits collapseUnchanged so every unchanged line shows; else collapse the runs.
        ...(expandUnchanged ? {} : {collapseUnchanged: {margin: 3, minSize: 8}}),
      };
      const unifiedDiffExtensions = (plain = false) => [
        api.unifiedMergeView(unifiedMergeOptions),
        ...(diffEditsAllowed ? codeMirrorExtensions(api, panel, path, {plain}) : codeMirrorReadOnlyExtensions(api, path, panel, {plain})),
        codeMirrorDiffOverviewListener(api, panel),  // B3: rebuild overview on fold/geometry change
      ];
      let cmState;
      try {
        cmState = api.EditorState.create({
          doc: currentText,
          extensions: unifiedDiffExtensions(false),
        });
        panel._cmPlainFallback = false;
      } catch (error) {
        console.warn('CodeMirror diff language parser failed; retrying plain diff editor', error);
        panel._cmThemeCompartment = null;
        panel._cmEditorOptionCompartment = null;
        panel._cmLocaleCompartment = null;
        cmState = api.EditorState.create({
          doc: currentText,
          extensions: unifiedDiffExtensions(true),
        });
        panel._cmPlainFallback = true;
      }
      panel._cmView = new api.EditorView({
        state: cmState,
        parent: container,
        dispatch(transaction) {
          panel._cmView.update([transaction]);
          if (transaction.docChanged || transaction.selectionSet) {
            updateCodeMirrorCursorStatus(panel);
            captureCodeMirrorPanelViewState(panel, path);
          }
          if (transaction.selectionSet) scheduleShareScrollPublishForElement(panel._cmView?.scrollDOM || panel);
          if (transaction.docChanged) {
            handleFileEditorContentChanged(panel, path, panel._cmView.state.doc.toString(), {syntax: false});
          }
        },
      });
      panel._cmView.scrollDOM?.addEventListener('scroll', () => {
        scheduleFileEditorSplitScrollSync(panel, 'editor');
        scheduleFileEditorPanelViewStateCapture(item, panel);
      });
      trackCodeMirrorViews(panel, api, [panel._cmView]);
    }
    panel._cmPath = path;
    panel._cmSignature = signature;
    panel._cmMode = 'diff';
    updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
    scheduleDiffOverviewSettledRebuild(panel);
    restoreFileEditorPanelViewState(item, panel);
    updateCodeMirrorCursorStatus(panel);
    focusFileEditorPanelIfReady(panel, item);
    return true;
  } catch (error) {
    if (panel._cmGeneration !== generation) return null;
    console.warn('CodeMirror diff editor unavailable; showing read-only raw text', error);
    destroyCodeMirrorPanel(panel);
    container.hidden = true;
    setFileEditorPanelStatus(panel, t('editor.codemirrorDiffUnavailable', {error}), 'error');
    return false;
  }
}

async function ensureCodeMirrorPanel(panel, item, path, state, options = {}) {
  const container = panel.querySelector('.file-editor-codemirror-panel');
  if (!container) return false;
  if (options.forceMode !== 'edit' && editorViewModeFor(path, item) === 'diff') return ensureCodeMirrorDiffPanel(panel, item, path, state);
  const generation = (panel._cmGeneration || 0) + 1;
  panel._cmGeneration = generation;
  container.hidden = false;
  container.classList.remove('file-editor-diff-codemirror');
  const currentText = String(state.content || '');
  const signature = codeMirrorConfigSignature(path, {mode: 'edit'});
  if (!panel._cmView || panel._cmPath !== path || panel._cmSignature !== signature) {
    captureFileEditorPanelViewState(item, panel);
    destroyCodeMirrorPanel(panel);
    container.textContent = t('editor.codemirrorLoading');
  }
  try {
    const api = await loadCodeMirrorApi();
    if (panel._cmGeneration !== generation) return null;
    if (!panel._cmView) {
      container.replaceChildren();
      const createdState = createEditableCodeMirrorState(api, panel, path, currentText);
      panel._cmView = new api.EditorView({
        state: createdState.state,
        parent: container,
        dispatch(transaction) {
          panel._cmView.update([transaction]);
          if (transaction.docChanged || transaction.selectionSet) {
            updateCodeMirrorCursorStatus(panel);
            captureCodeMirrorPanelViewState(panel, path);
          }
          if (transaction.selectionSet) scheduleShareScrollPublishForElement(panel._cmView?.scrollDOM || panel);
          if (transaction.docChanged) {
            handleFileEditorContentChanged(panel, path, panel._cmView.state.doc.toString(), {syntax: false});
          }
        },
      });
      panel._cmPath = path;
      panel._cmSignature = signature;
      panel._cmMode = 'edit';
      panel._cmPlainFallback = Boolean(createdState.plain);
      panel._cmView.scrollDOM?.addEventListener('scroll', () => {
        scheduleFileEditorSplitScrollSync(panel, 'editor');
        scheduleFileEditorPanelViewStateCapture(item, panel);
      });
      trackCodeMirrorViews(panel, api, [panel._cmView]);
      updateCodeMirrorCursorStatus(panel);
      if (createdState.plain) {
        setFileEditorPanelStatus(panel, t('editor.codemirrorPlainText'), 'warn');
      }
    } else if (panel._cmView.state.doc.toString() !== currentText && !state.dirty) {
      panel._cmView.dispatch({
        changes: {from: 0, to: panel._cmView.state.doc.length, insert: currentText},
      });
      updateCodeMirrorCursorStatus(panel);
    }
    restoreFileEditorPanelViewState(item, panel);
    focusFileEditorPanelIfReady(panel, item);
    applyPendingFileEditorLineTarget(item, panel);
    // if blame is on but this path isn't cached yet (file opened after the toggle), fetch it
    // and nudge the editor so the annotation appears without a manual toggle. Deduped; no-op otherwise.
    ensureEditorBlameForPath(path);
    return true;
  } catch (error) {
    if (panel._cmGeneration !== generation) return null;
    destroyCodeMirrorPanel(panel);
    container.hidden = true;
    setFileEditorPanelStatus(panel, t('editor.codemirrorUnavailable', {error}), 'error');
    return false;
  }
}

function renderFileEditorRawPane(rawPane, path, content) {
  if (!rawPane) return;
  const code = rawPane.querySelector('code');
  if (!code) return;
  const language = syntaxLanguageForPath(path);
  rawPane.hidden = false;
  rawPane.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
  rawPane.classList.toggle('editor-wrap', fileEditorWrapEnabled);
  code.className = `language-${language || 'text'}`;
  code.innerHTML = editorVisualHighlightHtml(language, content, {
    wrap: fileEditorWrapEnabled,
    lineNumbers: fileEditorLineNumbersEnabled,
  });
}

// should an in-diff-mode editor fall back to edit because the loaded diff has nothing to show?
// YES only when the file has NO useful git history — those files genuinely can't be diffed. A file WITH
// useful history (e.g. README.md with many commits but a clean working tree → an empty HEAD-vs-working
// diff) must STAY in diff mode so the FROM/TO sha picker stays reachable and the user can compare
// ARBITRARY refs. Force-exiting on an empty default diff (the old behavior) hid the picker entirely on
// clean files — the recurring "press DIFF, no FROM/TO menu" bug.
function diffModeShouldFallBackToEdit(path, state, item = null) {
  return state?.kind === 'text'
    && editorViewModeFor(path, item) === 'diff'
    && (!fileStateHasRepo(path, state)
      || (state.diffLoaded === true
        && !state.diffLoading
        && !fileStateCanRenderDiffView(path, state)));
}

function renderFileEditorPanelShouldCaptureViewState(options = {}) {
  return options.captureViewState !== false;
}

function applyPendingFileEditorLineTarget(item, panel) {
  const line = pendingFileEditorLineTargets.get(item);
  const view = panel?._cmView;
  if (!line || !view) return false;
  try {
    const docLine = view.state.doc.line(Math.min(Math.max(1, line), view.state.doc.lines));
    const scrollEffect = view.constructor?.scrollIntoView?.(docLine.from, {y: 'start'});
    view.dispatch({
      selection: {anchor: docLine.from, head: docLine.from},
      ...(scrollEffect ? {effects: scrollEffect} : {}),
    });
    pendingFileEditorLineTargets.delete(item);
    return true;
  } catch (_) {
    return false;
  }
}

function scheduleShareFileEditorScrollRestore(item, path) {
  if (!shareViewMode || typeof scheduleShareScrollRestoreByKey !== 'function') return;
  const key = item || path || '';
  if (!key) return;
  scheduleShareScrollRestoreByKey(`editor:${key}:editor`);
  scheduleShareScrollRestoreByKey(`editor:${key}:preview`);
}

function editorPanelParts(panel) {
  const parts = {
    codeMirrorPane: panel.querySelector('.file-editor-codemirror-panel'),
    rawPane: panel.querySelector('.file-editor-raw-panel'),
    previewPane: panel.querySelector('.file-editor-preview-pane-panel'),
    imagePane: panel.querySelector('.file-editor-image-panel'),
    modeControl: panel.querySelector('.file-editor-mode-control-panel'),
    previewFontPanel: panel.querySelector('.file-editor-preview-font-panel'),
    gutterButton: panel.querySelector('.file-editor-gutter-panel'),
    wrapButton: panel.querySelector('.file-editor-wrap-panel'),
    findButton: panel.querySelector('.file-editor-find-panel'),
    diffButton: panel.querySelector('.file-editor-diff-panel'),
    diffRefPanel: panel.querySelector('.file-editor-diff-ref-panel'),
    diffExpandButton: panel.querySelector('.file-editor-diff-expand-panel'),
    popoutPreviewButton: panel.querySelector('.file-editor-popout-preview-panel'),
    reloadButton: panel.querySelector('.file-editor-reload-panel'),
    themeButton: panel.querySelector('.file-editor-theme-panel'),
    blameButton: panel.querySelector('.file-editor-blame-panel'),
    saveButton: panel.querySelector('.file-editor-save-panel'),
    content: panel.querySelector('.file-editor-content'),
  };
  parts.textControls = [
    parts.modeControl,
    parts.previewFontPanel,
    parts.gutterButton,
    parts.wrapButton,
    parts.findButton,
    parts.blameButton,
    parts.diffButton,
    parts.diffExpandButton,
    parts.diffRefPanel,
    parts.popoutPreviewButton,
    parts.reloadButton,
  ];
  return parts;
}

function hideTextEditorPanes(parts) {
  hideFileEditorContent(parts.rawPane, parts.previewPane, parts.imagePane, parts.codeMirrorPane);
}

function destroyEditorAndShowStatus(panel, parts, message, level = '') {
  setElementsHidden(parts.textControls, true);
  updateFileEditorToolbarSeparators(panel);
  panel.classList.remove('syntax-highlighted');
  destroyCodeMirrorPanel(panel);
  hideTextEditorPanes(parts);
  setFileEditorPanelStatus(panel, message, level);
}

function renderClosedEditor(panel, parts) {
  destroyEditorAndShowStatus(panel, parts, t('editor.fileClosed'), '');
}

function renderLoadingEditor(panel, item, path, parts) {
  destroyEditorAndShowStatus(panel, parts, t('common.loading'), '');
  loadFileEditorState(path, panel, item);
}

function renderErrorEditor(panel, path, state, parts) {
  setElementsHidden(parts.textControls, true);
  updateFileEditorToolbarSeparators(panel);
  panel.classList.remove('syntax-highlighted');
  destroyCodeMirrorPanel(panel);
  if (parts.rawPane) parts.rawPane.hidden = true;
  if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
  if (parts.previewPane) parts.previewPane.hidden = true;
  if (parts.imagePane) {
    parts.imagePane.hidden = false;
    const limit = formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES);
    const size = formatFileSize(state.size);
    const title = state.kind === 'too-large' ? t('editor.fileTooLargeTitle') : t('editor.fileOpenFailedTitle');
    const detail = state.kind === 'too-large'
      ? fileErrorText(state.error, 'editor.fileTooLargeDetail', {size: size || '', limit})
      : fileErrorText(state.error, 'editor.fileLoadFailed');
    parts.imagePane.replaceChildren(fileEditorEmptyState(title, detail));
  }
  const status = state.kind === 'too-large'
    ? t('editor.fileTooLargeStatus', {limit: formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES)})
    : fileErrorText(state.error, 'editor.fileLoadFailed');
  setFileEditorPanelStatus(panel, status, 'error');
}

function syncEditorDiffRefPanel(path, state, item, parts, mode) {
  const diffRefPanel = parts.diffRefPanel;
  if (!diffRefPanel) return;
  diffRefPanel.hidden = mode !== 'diff' || state.kind !== 'text';
  // C6: scope the editor's own FROM/TO controls to THIS file's repo, so they match the repo header and
  // drive the file's diff. Re-render only when the repo actually changed and the picker isn't focused.
  const diffRepo = fileRepoForPath(path);
  const historySignature = fileDiffRefHistorySignature(path);
  if (!diffRefPanel.hidden
    && (diffRefPanel.dataset.diffRefRepoRendered !== diffRepo
      || diffRefPanel.dataset.diffRefPathRendered !== path
      || diffRefPanel.dataset.diffRefHistoryRendered !== historySignature)
    && !diffRefPanel.contains(document.activeElement)) {
    diffRefPanel.innerHTML = diffRefControlsHtml({compact: true, repo: diffRepo, path});
    diffRefPanel.dataset.diffRefRepoRendered = diffRepo;
    diffRefPanel.dataset.diffRefPathRendered = path;
    diffRefPanel.dataset.diffRefHistoryRendered = historySignature;
  }
  syncDiffRefControlValues(diffRefPanel);
}

function syncTextEditorControls(panel, path, state, item, parts, mode) {
  const diffExpandButton = parts.diffExpandButton;
  const popoutPreviewButton = parts.popoutPreviewButton;
  const previewable = editorPreviewModeAvailable(path, state);
  updateEditorThemeButton(parts.themeButton, {includeVanilla: true});
  updateEditorModeControl(parts.modeControl, path, state, item);
  if (parts.previewFontPanel) {
    parts.previewFontPanel.hidden = state.kind !== 'text' || !previewable || (mode !== 'preview' && mode !== 'split');
    updateEditorPreviewFontControls(parts.previewFontPanel);
  }
  if (parts.gutterButton) {
    parts.gutterButton.hidden = state.kind !== 'text' || mode === 'preview';
    updateEditorGutterButton(parts.gutterButton);
  }
  if (parts.wrapButton) {
    parts.wrapButton.hidden = state.kind !== 'text' || mode === 'preview';
    updateEditorWrapButton(parts.wrapButton);
  }
  updateEditorFindButton(parts.findButton, state, panel);
  if (mode !== 'preview') closePreviewFind(panel);
  // Git-backed controls share file-history gating, but Diff also depends on the loaded diff state while
  // Blame stays available in normal edit mode for clean files with useful history.
  updateFileEditorBlameButton(parts.blameButton, path, state, item);
  updateFileEditorDiffButton(parts.diffButton, path, state, item);
  updateFileEditorDiffExpandButton(diffExpandButton, path, state, item);
  if (popoutPreviewButton) popoutPreviewButton.hidden = !previewable;
  syncEditorDiffRefPanel(path, state, item, parts, mode);
  updateFileEditorToolbarSeparators(panel);
  return {previewable};
}

function renderImageEditor(panel, path, state, parts) {
  updateImageViewerThemeButton(parts.themeButton);
  setEditorContentMode(parts.content, 'preview');
  destroyCodeMirrorPanel(panel);
  if (parts.rawPane) parts.rawPane.hidden = true;
  if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
  if (parts.previewPane) parts.previewPane.hidden = true;
  panel.classList.remove('syntax-highlighted');
  if (parts.imagePane) {
    parts.imagePane.hidden = false;
    renderFileEditorImagePane(parts.imagePane, path, state, (message, level) => setFileEditorPanelStatus(panel, message, level));
  }
}

function renderMediaEditor(panel, path, state, parts) {
  updateImageViewerThemeButton(parts.themeButton);
  setEditorContentMode(parts.content, 'preview');
  destroyCodeMirrorPanel(panel);
  if (parts.rawPane) parts.rawPane.hidden = true;
  if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
  if (parts.imagePane) {
    disconnectFileEditorImageObserver(parts.imagePane);
    parts.imagePane.hidden = true;
    parts.imagePane.replaceChildren();
  }
  panel.classList.remove('syntax-highlighted');
  if (parts.previewPane) {
    parts.previewPane.hidden = false;
    renderEditorPreviewPane(parts.previewPane, path, '', {context: 'preview'});
  }
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
}

function resetImagePreviewPane(parts) {
  if (!parts.imagePane) return;
  disconnectFileEditorImageObserver(parts.imagePane);
  parts.imagePane.hidden = true;
  parts.imagePane.replaceChildren();
}

function renderTextPreviewMode(panel, item, path, state, parts) {
  destroyCodeMirrorPanel(panel);
  if (parts.rawPane) parts.rawPane.hidden = true;
  if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
  panel.classList.remove('syntax-highlighted');
  if (parts.previewPane) {
    parts.previewPane.hidden = false;
    renderEditorPreviewPane(parts.previewPane, path, state.content, {context: 'preview'});
  }
  refreshPreviewFind(panel);
  scheduleShareFileEditorScrollRestore(item, path);
}

function renderTextCodeMode(panel, item, path, state, parts, mode) {
  const rawPane = parts.rawPane;
  const previewPane = parts.previewPane;
  if (rawPane) rawPane.hidden = true;
  if (previewPane) {
    previewPane.hidden = mode !== 'split';
    if (mode === 'split') renderEditorPreviewPane(previewPane, path, state.content, {context: 'split'});
  }
  panel.classList.remove('syntax-highlighted');
  ensureCodeMirrorPanel(panel, item, path, state).then(loaded => {
    if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);
    else scheduleShareFileEditorScrollRestore(item, path);
  }).catch(error => {
    if (panel.dataset.filePath !== path) return;
    console.warn('CodeMirror editor unavailable; showing read-only raw text', error);
    destroyCodeMirrorPanel(panel);
    if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
    setFileEditorPanelStatus(panel, t('editor.codemirrorUnavailable', {error}), 'error');
    renderFileEditorRawPane(rawPane, path, state.content);
  });
}

function renderTextEditorMode(panel, item, path, state, parts, mode) {
  resetImagePreviewPane(parts);
  setEditorContentMode(parts.content, mode);
  panel.classList.toggle('editor-wrap', fileEditorWrapEnabled);
  panel.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
  if (mode === 'preview') renderTextPreviewMode(panel, item, path, state, parts);
  else renderTextCodeMode(panel, item, path, state, parts, mode);
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
  focusFileEditorPanelIfReady(panel, item);
  scheduleShareFileEditorScrollRestore(item, path);
}

function renderFileEditorPanel(panel, item, options = {}) {
  const path = fileItemPath(item);
  if (renderFileEditorPanelShouldCaptureViewState(options)) capturePaneViewState(item, panel);
  const shouldUpdateActiveFile = options.updateActiveFile !== false
    && (!dockviewLayoutActive() || focusedPanelItem === item || options.forceActiveFile === true);
  if (shouldUpdateActiveFile) {
    const previousActiveFile = activeFile;
    activeFile = path;
    if (previousActiveFile !== path) scheduleFileExplorerActiveFileReveal(path);
    else updateFileExplorerCurrentFileHighlight();
  } else if (activeFile === path) {
    updateFileExplorerCurrentFileHighlight();
  }
  const state = openFiles.get(path);
  updateFileEditorPanelChrome(panel, path);
  const parts = editorPanelParts(panel);
  updateEditorThemeButton(parts.themeButton, {includeVanilla: true});
  if (!state) {
    renderClosedEditor(panel, parts);
    return;
  }
  if (state.loading) {
    renderLoadingEditor(panel, item, path, parts);
    return;
  }
  if (state.kind === 'error' || state.kind === 'too-large') {
    renderErrorEditor(panel, path, state, parts);
    return;
  }
  // do NOT auto-load the diff when a file opens/renders. The diff loads only on explicit
  // diff-mode entry (the Diff button + the Modified-files menu both open in diff view and load there),
  // so opening/editing a file does zero diff work (one fewer network round-trip + re-render; ties to ).
  if (diffModeShouldFallBackToEdit(path, state, item)) {
    setFileEditorViewMode(path, 'edit', item);
  }
  const mode = editorViewModeFor(path, item);
  syncTextEditorControls(panel, path, state, item, parts, mode);
  if (state.kind === 'image') {
    renderImageEditor(panel, path, state, parts);
    return;
  }
  if (state.kind === 'media') {
    renderMediaEditor(panel, path, state, parts);
    return;
  }
  renderTextEditorMode(panel, item, path, state, parts, mode);
}

function loadFileEditorState(path, panel, item) {
  const state = openFiles.get(path);
  if (!state || state.loadingPromise) return;
  const loadingPromise = (async () => {
    const kind = openFileKindForPreviewPath(basenameOf(path));
    if (kind === 'image' || kind === 'media') {
      const fetched = await fetchFileEntryStatus(path);
      const entry = fetched.entry;
      if (!entry) {
        if (fetched.missing) markOpenFileMissing(path);
        else setFileState(path, fileErrorState(fetched.error));
        renderSessionButtons();
        renderPaneTabStrips();
        return;
      }
      if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
        const state = tooLargeFileState(Number(entry.size));
        state.mtime = fileEntryMtime(entry);
        setFileState(path, state);
      } else if (kind === 'image') {
        setFileState(path, {mtime: fileEntryMtime(entry), kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null});
      } else {
        setFileState(path, rawPreviewFileState(path, entry));
      }
      if (panel) renderFileEditorPanel(panel, item);
      renderSessionButtons();
      renderPaneTabStrips();
      return;
    }
    try {
      const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
      setFileState(path, applyFileGitMetadata({
        mtime: filePayloadMtime(payload),
        size: payload.size,
        kind: 'text',
        original: payload.content,
        content: payload.content,
        dirty: false,
      }, payload));
    } catch (err) {
      const status = Number(err?.status) || 0;
      if (status) {
        const sniffed = status === 415 ? await sniffedRawPreviewFileState(path) : null;
        setFileState(path, sniffed || (status === 413
          ? tooLargeFileState(null, err)
          : status === 404
            ? missingFileState(err)
            : fileErrorState(err)));
      } else {
        setFileState(path, fileErrorState(err));
      }
    }
    if (panel) renderFileEditorPanel(panel, item);
    renderSessionButtons();
    renderPaneTabStrips();
  })().finally(() => {
    const current = openFiles.get(path);
    if (current?.loadingPromise === loadingPromise) delete current.loadingPromise;
  });
  state.loadingPromise = loadingPromise;
}

function updateFileEditorPanelChrome(panel, path) {
  const state = openFiles.get(path);
  const item = panel?.dataset?.layoutItem || '';
  const previewOnly = false;
  panel.classList.toggle('dirty', !!state?.dirty);
  const dirtyDot = panel.querySelector('.file-editor-title .file-tab-dirty');
  if (dirtyDot) dirtyDot.hidden = !state?.dirty;
  const nameNode = panel.querySelector('.file-editor-title-name');
  if (nameNode) nameNode.textContent = basenameOf(path);
  const pathNode = panel.querySelector('.file-editor-path');
  if (pathNode) {
    pathNode.textContent = compactHomePath(path) || path;
    pathNode.title = path;
  }
  const saveButton = panel.querySelector('.file-editor-save-panel');
  if (saveButton) {
    saveButton.hidden = previewOnly || readOnlyMode || state?.kind !== 'text';
    saveButton.disabled = !state?.dirty;
  }
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  if (reloadButton) {
    reloadButton.hidden = previewOnly || state?.kind !== 'text';
  }
  updateFileEditorToolbarSeparators(panel);
}

function fileEditorToolbarControlVisible(panel, selector) {
  const node = panel?.querySelector(selector);
  return !!node && node.hidden !== true;
}

function setFileEditorToolbarSeparator(panel, key, visible) {
  const separator = panel?.querySelector(`[data-editor-toolbar-separator="${key}"]`);
  if (separator) separator.hidden = !visible;
}


function updateFileEditorToolbarSeparators(panel) {
  const mode = fileEditorToolbarControlVisible(panel, '.file-editor-mode-control-panel');
  const theme = fileEditorToolbarControlVisible(panel, '.file-editor-theme-panel');
  const tools = [
    '.file-editor-preview-font-panel',
    '.file-editor-gutter-panel',
    '.file-editor-wrap-panel',
    '.file-editor-find-panel',
    '.file-editor-blame-panel',
    '.file-editor-diff-panel',
    '.file-editor-diff-expand-panel',
    '.file-editor-diff-ref-panel',
  ].some(selector => fileEditorToolbarControlVisible(panel, selector));
  const reload = fileEditorToolbarControlVisible(panel, '.file-editor-reload-panel');
  const save = fileEditorToolbarControlVisible(panel, '.file-editor-save-panel');
  // #42: the editor controls now live on their own toolbar row below the tab strip (no frame
  // controls sit beside them), so separators only sit between adjacent visible control groups.
  setFileEditorToolbarSeparator(panel, 'mode', (theme || mode) && (tools || reload || save));
  setFileEditorToolbarSeparator(panel, 'tools', tools && (reload || save));
  setFileEditorToolbarSeparator(panel, 'theme', reload && save);
  const toolbar = panel?.querySelector?.('.file-editor-toolbar');
  if (toolbar) toolbar.hidden = !(theme || mode || tools || reload || save);
}

function setFileEditorPanelStatus(panel, message, level) {
  const status = panel?.querySelector?.('.file-editor-status-panel');
  if (!status) return;
  let messageNode = status.querySelector('.file-editor-status-message');
  let countNode = status.querySelector('.file-editor-count-status');
  let cursorNode = status.querySelector('.file-editor-cursor-status');
  if (!messageNode) {
    status.textContent = '';
    messageNode = document.createElement('span');
    messageNode.className = 'file-editor-status-message';
    countNode = document.createElement('span');
    countNode.className = 'file-editor-count-status';
    cursorNode = document.createElement('span');
    cursorNode.className = 'file-editor-cursor-status';
    status.append(messageNode, countNode, cursorNode);
  } else {
    if (!countNode) {
      countNode = document.createElement('span');
      countNode.className = 'file-editor-count-status';
      if (cursorNode) status.insertBefore(countNode, cursorNode);
      else status.appendChild(countNode);
    }
    if (!cursorNode) {
      cursorNode = document.createElement('span');
      cursorNode.className = 'file-editor-cursor-status';
      status.appendChild(cursorNode);
    }
  }
  messageNode.textContent = message || '';
  status.dataset.level = level || '';
  updateCodeMirrorCursorStatus(panel);
}

function markdownInlineHighlightHtml(escaped) {
  return escaped
    .replace(/(`[^`]+`)/g, '<span class="md-code">$1</span>')
    .replace(/(\*\*[^*]+\*\*|__[^_]+__)/g, '<span class="md-bold">$1</span>')
    .replace(/(\[[^\]]+\]\([^)]+\))/g, '<span class="md-link">$1</span>')
    .replace(/(&lt;\/?[A-Za-z][^&]*?&gt;)/g, '<span class="md-html">$1</span>')
    .replace(/(^|[^\w*])(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g, '$1<span class="md-italic">$2</span>');
}

function markdownSyntaxHtml(text) {
  let inFence = false;
  return String(text || '').split('\n').map(line => {
    const escaped = esc(line);
    const fence = /^\s*(```|~~~)/.test(line);
    if (fence) {
      inFence = !inFence;
      return `<span class="md-fence">${escaped}</span>`;
    }
    if (inFence) return `<span class="md-codeblock">${escaped}</span>`;
    const heading = line.match(/^(\s{0,3})(#{1,6})(\s+.*)$/);
    if (heading) return `<span class="md-heading md-heading-${heading[2].length}">${escaped}</span>`;
    const quote = escaped.match(/^(\s*&gt;\s?)(.*)$/);
    if (quote) return `<span class="md-blockquote">${escaped}</span>`;
    const list = escaped.match(/^(\s*(?:[-*+]|\d+\.)\s+)(.*)$/);
    if (list) return `<span class="md-list-marker">${list[1]}</span>${markdownInlineHighlightHtml(list[2])}`;
    return markdownInlineHighlightHtml(escaped);
  }).join('\n');
}

function simpleTokenHighlightTokens(raw, rules) {
  const text = String(raw || '');
  let index = 0;
  const tokens = [];
  while (index < text.length) {
    let best = null;
    for (let ruleIndex = 0; ruleIndex < rules.length; ruleIndex += 1) {
      const rule = rules[ruleIndex];
      rule.regex.lastIndex = index;
      const match = rule.regex.exec(text);
      if (!match || !match[0]) continue;
      const candidate = {rule, ruleIndex, index: match.index, text: match[0]};
      if (!best
        || candidate.index < best.index
        || (candidate.index === best.index && candidate.ruleIndex < best.ruleIndex)) {
        best = candidate;
      }
    }
    if (!best) {
      break;
    }
    if (best.index > index) index = best.index;
    tokens.push({
      from: best.index,
      to: best.index + best.text.length,
      text: best.text,
      className: best.rule.className,
    });
    index = best.index + best.text.length;
  }
  return tokens;
}

function simpleTokenHighlightHtml(raw, rules) {
  const text = String(raw || '');
  const tokens = simpleTokenHighlightTokens(text, rules);
  let html = '';
  let index = 0;
  for (const token of tokens) {
    html += esc(text.slice(index, token.from));
    html += `<span class="${token.className}">${esc(token.text)}</span>`;
    index = token.to;
  }
  html += esc(text.slice(index));
  return html;
}

function normalizeSimpleCodeSyntaxLanguage(language) {
  const normalized = String(language || '').trim().toLowerCase();
  if (normalized === 'py') return 'python';
  if (normalized === 'rs') return 'rust';
  if (normalized === 'sh' || normalized === 'shell' || normalized === 'zsh') return 'bash';
  if (normalized === 'js' || normalized === 'jsx') return 'javascript';
  if (normalized === 'ts' || normalized === 'tsx') return 'typescript';
  if (normalized === 'yml') return 'yaml';
  return normalized;
}

function simpleCodeSyntaxRules(language) {
  const normalized = normalizeSimpleCodeSyntaxLanguage(language);
  const stringRule = {regex: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, className: 'code-string'};
  const numberRule = {regex: /\b\d+(?:\.\d+)?\b/g, className: 'code-number'};
  const shellRules = [
    stringRule,
    {regex: /#[^\n]*/g, className: 'code-comment'},
    {regex: /\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$[0-9@#?*!-]/g, className: 'code-variable'},
    {regex: /\b(?:if|then|else|elif|fi|for|in|do|done|while|case|esac|function|export|local|readonly|return|exit|set|unset|trap|source)\b/g, className: 'code-keyword'},
    {regex: /\b(?:awk|cat|cd|chmod|chown|cp|curl|docker|echo|find|git|grep|head|jq|ls|mkdir|mv|node|npm|python|python3|rg|rm|sed|ssh|tail|tar|tee|test|touch|xargs)\b/g, className: 'code-builtin'},
    numberRule,
  ];
  const pythonRules = [
    stringRule,
    {regex: /#[^\n]*/g, className: 'code-comment'},
    {regex: /@\w+/g, className: 'code-function'},
    {regex: /\b(?:Any|BaseModel|Callable|DeltaFunctionCall|DeltaMessage|DeltaToolCall|Dict|ExtractedToolCallInformation|Iterable|Iterator|List|Literal|Mapping|NoneType|OpenAIBaseModel|Optional|Sequence|Set|Self|ToolParser|Tuple|Type|Union|bool|bytes|dict|float|int|list|set|str|tuple)\b/g, className: 'code-type'},
    {regex: /\b(?:False|None|True)\b/g, className: 'code-constant'},
    {regex: /\b[A-Z][A-Za-z0-9_]*(?=[\[\]|,):]|\s*$)/g, className: 'code-type'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*\()/g, className: 'code-function'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*:)/g, className: 'code-property'},
    {regex: /\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:cls|self)\b/g, className: 'code-variable'},
    {regex: /\b[a-z_][A-Za-z0-9_]*\b/g, className: 'code-variable'},
    numberRule,
  ];
  const jsRules = [
    {regex: /`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, className: 'code-string'},
    {regex: /\/\/[^\n]*|\/\*.*?\*\//g, className: 'code-comment'},
    {regex: /\b(?:async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|finally|for|from|function|if|import|in|instanceof|let|new|of|return|switch|throw|try|typeof|var|void|while|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:false|null|true|undefined|this|super)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const rustRules = [
    stringRule,
    {regex: /\/\/[^\n]*|\/\*.*?\*\//g, className: 'code-comment'},
    {regex: /\b(?:fn|pub|where)\b/g, className: 'code-control'},
    {regex: /\b[A-Za-z_][A-Za-z0-9_]*(?=\s*:)/g, className: 'code-property'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*\()/g, className: 'code-function'},
    {regex: /\b(?:Box|Option|Result|Send|String|Tool|ToolCallDelta|ToolParser|ToolParserOutput|Value|Vec|bool|char|dyn|f32|f64|i8|i16|i32|i64|i128|isize|str|u8|u16|u32|u64|u128|usize)\b/g, className: 'code-type'},
    {regex: /\b[A-Z][A-Za-z0-9_]*\b/g, className: 'code-type'},
    {regex: /'[A-Za-z_][A-Za-z0-9_]*/g, className: 'code-type'},
    {regex: /\b(?:as|async|await|break|const|continue|crate|else|enum|extern|false|for|if|impl|in|let|loop|match|mod|move|mut|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|while)\b/g, className: 'code-keyword'},
    {regex: /\b[A-Za-z_][A-Za-z0-9_]*!/g, className: 'code-function'},
    numberRule,
  ];
  const xmlRules = [
    {regex: /<!--.*?-->/g, className: 'code-comment'},
    {regex: /<\/?[A-Za-z][^>]*?>/g, className: 'code-tag'},
    stringRule,
  ];
  const jsonRules = [
    {regex: /"(?:\\.|[^"\\])*"(?=\s*:)/g, className: 'code-attr'},
    stringRule,
    {regex: /\b(?:false|null|true)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const cssRules = [
    {regex: /\/\*.*?\*\//g, className: 'code-comment'},
    stringRule,
    {regex: /#[0-9A-Fa-f]{3,8}\b/g, className: 'code-number'},
    {regex: /[A-Za-z-]+(?=\s*:)/g, className: 'code-attr'},
    {regex: /\b(?:auto|block|flex|grid|hidden|inline|none|relative|absolute|fixed|solid|transparent|inherit|initial|unset)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const yamlRules = [
    {regex: /#[^\n]*/g, className: 'code-comment'},
    stringRule,
    {regex: /^[\s-]*[A-Za-z0-9_.-]+(?=\s*:)/gm, className: 'code-attr'},
    {regex: /\b(?:false|null|true|yes|no|on|off)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const rulesByLanguage = new Map([
    ['bash', shellRules],
    ['css', cssRules],
    ['ini', yamlRules],
    ['javascript', jsRules],
    ['json', jsonRules],
    ['python', pythonRules],
    ['rust', rustRules],
    ['typescript', jsRules],
    ['xml', xmlRules],
    ['yaml', yamlRules],
  ]);
  return rulesByLanguage.get(normalized) || null;
}

function simpleCodeSyntaxTokens(language, text) {
  const rules = simpleCodeSyntaxRules(language);
  if (!rules) return [];
  return simpleTokenHighlightTokens(text, rules);
}

function simpleCodeSyntaxHtml(language, text) {
  const normalized = normalizeSimpleCodeSyntaxLanguage(language);
  if (normalized === 'markdown') return markdownSyntaxHtml(text);
  const rules = simpleCodeSyntaxRules(normalized);
  if (!rules) return null;
  return String(text || '').split('\n').map(line => simpleTokenHighlightHtml(line, rules)).join('\n');
}

function syntaxLanguageForPath(path) {
  const name = basenameOf(path).toLowerCase();
  const dot = name.lastIndexOf('.');
  const ext = dot === -1 ? '' : name.slice(dot);
  if (syntaxLanguageByExtension.has(ext)) return syntaxLanguageByExtension.get(ext);
  if (name === 'dockerfile') return 'dockerfile';
  if (name === 'makefile') return 'makefile';
  return '';
}

function previewSourceLineAnchors(previewPane) {
  return Array.from(previewPane?.querySelectorAll?.('[data-source-line]') || [])
    .map(element => ({element, line: Number(element.dataset.sourceLine)}))
    .filter(item => Number.isFinite(item.line) && item.line > 0 && previewSourceAnchorIsRendered(item.element));
}

function detailsSummaryElement(details) {
  return Array.from(details?.children || []).find(child => String(child?.tagName || '').toLowerCase() === 'summary') || null;
}

function elementHiddenByClosedDetails(element) {
  for (let details = element?.closest?.('details'); details; details = details.parentElement?.closest?.('details')) {
    if (details.open) continue;
    const summary = detailsSummaryElement(details);
    if (!summary || !summary.contains(element)) return true;
  }
  return false;
}

function previewSourceAnchorIsRendered(element) {
  if (!element || elementHiddenByClosedDetails(element)) return false;
  const rects = element.getClientRects?.();
  if (rects && rects.length > 0) return true;
  const rect = element.getBoundingClientRect?.();
  return Boolean(rect && (rect.width > 0 || rect.height > 0));
}

function previewAnchorForSourceLine(previewPane, sourceLine) {
  const target = Math.max(1, Math.floor(Number(sourceLine) || 1));
  const anchors = previewSourceLineAnchors(previewPane);
  let best = null;
  for (const item of anchors) {
    if (item.line > target) break;
    best = item;
  }
  return best || anchors[0] || null;
}

function scrollPreviewToSourceLine(previewPane, sourceLine) {
  const anchor = previewAnchorForSourceLine(previewPane, sourceLine);
  if (!anchor) return false;
  previewPane.scrollTop = Math.max(0, anchor.element.offsetTop - 4);
  return true;
}

function scrollViewportTopForElement(scroller) {
  if (!scroller?.getBoundingClientRect) return 0;
  const doc = scroller.ownerDocument || null;
  if (doc && (doc.scrollingElement === scroller || doc.documentElement === scroller || doc.body === scroller)) return 0;
  return scroller.getBoundingClientRect().top || 0;
}

function scrollTopForPreviewElement(scroller, element) {
  if (!scroller || !element?.getBoundingClientRect) return Number(element?.offsetTop || 0);
  return Math.max(0, Number(scroller.scrollTop || 0) + element.getBoundingClientRect().top - scrollViewportTopForElement(scroller));
}

function previewSourceLineForScroller(previewRoot, scroller) {
  const anchors = previewSourceLineAnchors(previewRoot);
  if (!anchors.length || !scroller) return null;
  const top = Number(scroller.scrollTop || 0) + 6;
  let best = anchors[0];
  for (const item of anchors) {
    if (scrollTopForPreviewElement(scroller, item.element) > top) break;
    best = item;
  }
  return best.line;
}

function scrollPreviewScrollerToSourceLine(previewRoot, scroller, sourceLine) {
  const anchor = previewAnchorForSourceLine(previewRoot, sourceLine);
  if (!anchor || !scroller) return false;
  scroller.scrollTop = Math.max(0, scrollTopForPreviewElement(scroller, anchor.element) - 4);
  return true;
}

function previewSourceLineForScroll(previewPane) {
  const anchors = previewSourceLineAnchors(previewPane);
  if (!anchors.length) return null;
  const top = (previewPane?.scrollTop || 0) + 6;
  let best = anchors[0];
  for (const item of anchors) {
    if (item.element.offsetTop > top) break;
    best = item;
  }
  return best.line;
}

function clampScrollTop(element, value) {
  const max = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  return Math.min(max, Math.max(0, Number(value || 0)));
}

const fileEditorSplitScrollFocusRatio = 0.5;

function editorScrollEdgeTarget(from, to) {
  const maxFrom = Math.max(0, Number(from?.scrollHeight || 0) - Number(from?.clientHeight || 0));
  const maxTo = Math.max(0, Number(to?.scrollHeight || 0) - Number(to?.clientHeight || 0));
  const current = Math.max(0, Number(from?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(from?.clientHeight || 0) * 0.01));
  if (maxTo <= 0 || current <= edgeSnap) return 0;
  if (maxFrom <= edgeSnap || current >= maxFrom - edgeSnap) return maxTo;
  return null;
}

function previewScrollAnchors(previewPane) {
  return previewSourceLineAnchors(previewPane)
    .map(item => ({...item, top: scrollTopForPreviewElement(previewPane, item.element)}))
    .sort((a, b) => a.line - b.line || a.top - b.top);
}

function sourcePositionForEditorScroll(cmView) {
  if (!cmView?.scrollDOM || !cmView.state?.doc) return null;
  try {
    const y = Math.max(0, Number(cmView.scrollDOM.scrollTop || 0)) + (Math.max(1, Number(cmView.scrollDOM.clientHeight || 0)) * fileEditorSplitScrollFocusRatio);
    const block = cmView.lineBlockAtHeight(y);
    const line = cmView.state.doc.lineAt(block.from).number;
    const height = Math.max(1, Number(block.height || 0));
    const fraction = Math.min(1, Math.max(0, (y - Number(block.top || 0)) / height));
    return {line: line + fraction};
  } catch (_) {
    return null;
  }
}

function sourcePositionForPreviewScroll(previewPane) {
  const anchors = previewScrollAnchors(previewPane);
  if (!anchors.length) return null;
  const y = Math.max(0, Number(previewPane?.scrollTop || 0)) + (Math.max(1, Number(previewPane?.clientHeight || 0)) * fileEditorSplitScrollFocusRatio);
  let previous = anchors[0];
  for (let index = 1; index < anchors.length; index += 1) {
    const next = anchors[index];
    if (next.top > y) {
      const span = Math.max(1, next.top - previous.top);
      const fraction = Math.min(1, Math.max(0, (y - previous.top) / span));
      return {line: previous.line + ((next.line - previous.line) * fraction)};
    }
    previous = next;
  }
  return {line: previous.line};
}

function previewScrollTopForSourcePosition(previewPane, position) {
  const anchors = previewScrollAnchors(previewPane);
  if (!anchors.length || !position) return null;
  const targetOffset = Math.max(1, Number(previewPane?.clientHeight || 0)) * fileEditorSplitScrollFocusRatio;
  const line = Math.max(1, Number(position.line || 1));
  if (line <= anchors[0].line) return clampScrollTop(previewPane, anchors[0].top - targetOffset);
  let previous = anchors[0];
  for (let index = 1; index < anchors.length; index += 1) {
    const next = anchors[index];
    if (line <= next.line) {
      const span = Math.max(1, next.line - previous.line);
      const fraction = Math.min(1, Math.max(0, (line - previous.line) / span));
      return clampScrollTop(previewPane, previous.top + ((next.top - previous.top) * fraction) - targetOffset);
    }
    previous = next;
  }
  return null;
}

function editorScrollTopForSourcePosition(cmView, position) {
  if (!cmView?.state?.doc || !position) return null;
  try {
    const targetOffset = Math.max(1, Number(cmView.scrollDOM?.clientHeight || 0)) * fileEditorSplitScrollFocusRatio;
    const line = Math.max(1, Math.min(Number(position.line || 1), cmView.state.doc.lines));
    const beforeLine = Math.max(1, Math.min(Math.floor(line), cmView.state.doc.lines));
    const afterLine = Math.max(1, Math.min(Math.ceil(line), cmView.state.doc.lines));
    const beforeBlock = cmView.lineBlockAt(cmView.state.doc.line(beforeLine).from);
    const beforeTop = Number(beforeBlock?.top || 0);
    if (afterLine === beforeLine) return clampScrollTop(cmView.scrollDOM, beforeTop - targetOffset);
    const afterBlock = cmView.lineBlockAt(cmView.state.doc.line(afterLine).from);
    const afterTop = Number(afterBlock?.top || beforeTop);
    return clampScrollTop(cmView.scrollDOM, beforeTop + ((afterTop - beforeTop) * (line - beforeLine)) - targetOffset);
  } catch (_) {
    return null;
  }
}

function previewPaneNeedsSourceAnchorScroll(previewPane) {
  return Boolean(previewPane?.querySelector?.('details, img.markdown-preview-image, .mermaid-preview-host, .file-editor-preview-zoom-shell'));
}

function syncFileEditorSplitScrollBySourceAnchors(host, source, editorScroller, previewPane) {
  if (!previewPaneNeedsSourceAnchorScroll(previewPane)) return false;
  const from = source === 'preview' ? previewPane : editorScroller;
  const to = source === 'preview' ? editorScroller : previewPane;
  to.scrollLeft = scrollSyncTargetPosition(from, to, 'left');
  const edgeTarget = editorScrollEdgeTarget(source === 'preview' ? previewPane : editorScroller, source === 'preview' ? editorScroller : previewPane);
  if (edgeTarget !== null) {
    if (source === 'preview') editorScroller.scrollTop = edgeTarget;
    else previewPane.scrollTop = edgeTarget;
    return true;
  }
  const position = source === 'preview' ? sourcePositionForPreviewScroll(previewPane) : sourcePositionForEditorScroll(host?._cmView);
  if (!position) return false;
  if (source === 'preview') {
    const target = editorScrollTopForSourcePosition(host?._cmView, position);
    if (target === null) return false;
    editorScroller.scrollTop = target;
    return true;
  }
  const target = previewScrollTopForSourcePosition(previewPane, position);
  if (target === null) return false;
  previewPane.scrollTop = target;
  return true;
}

function nowMs() {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

function fileEditorScrollSyncBlocked(panel, source = '') {
  const suppressed = panel?._splitScrollSyncing || Number(panel?._splitScrollSuppressUntil || 0) > nowMs();
  if (!suppressed) return false;
  return !source || panel?._splitScrollSource !== source;
}

function setFileEditorScrollSyncGuardForSource(source, ...panels) {
  const until = nowMs() + fileEditorScrollSyncSuppressMs;
  for (const panel of panels) {
    if (!panel) continue;
    panel._splitScrollSyncing = true;
    panel._splitScrollSource = source || '';
    panel._splitScrollSuppressUntil = Math.max(Number(panel._splitScrollSuppressUntil || 0), until);
    const release = () => {
      panel._splitScrollSyncing = false;
      if (Number(panel._splitScrollSuppressUntil || 0) <= nowMs()) panel._splitScrollSource = '';
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(release, 0));
    else setTimeout(release, 0);
  }
}

function setFileEditorScrollSyncGuard(...panels) {
  setFileEditorScrollSyncGuardForSource('', ...panels);
}

function elementCanScroll(element) {
  return Boolean(element && Math.max(0, element.scrollHeight - element.clientHeight) > 1);
}

function fileEditorPanelItem(panel) {
  return panel?.dataset?.layoutItem || '';
}

function fileEditorPanelPath(panel) {
  return panel?.dataset?.filePath || '';
}

function fileEditorPanelMode(panel) {
  const path = fileEditorPanelPath(panel);
  return editorViewModeFor(path, fileEditorPanelItem(panel));
}

function fileEditorPanelScroller(panel) {
  if (panel?._cmView?.scrollDOM) return panel._cmView.scrollDOM;
  return null;
}

function fileEditorPanelPreviewPane(panel) {
  const previewPane = panel?.querySelector?.('.file-editor-preview-pane-panel');
  return previewPane && !previewPane.hidden ? previewPane : null;
}

function fileEditorSourceElement(panel, source) {
  if (fileEditorPanelMode(panel) === 'diff') return null;
  return source === 'preview' ? fileEditorPanelPreviewPane(panel) : fileEditorPanelScroller(panel);
}

function fileEditorSourceCanDrive(panel, source) {
  return elementCanScroll(fileEditorSourceElement(panel, source));
}

function fileEditorSourceLineForScroll(panel, source) {
  if (source === 'preview') return previewSourceLineForScroll(fileEditorPanelPreviewPane(panel));
  if (panel?._cmView) {
    try {
      const block = panel._cmView.lineBlockAtHeight(panel._cmView.scrollDOM.scrollTop);
      return panel._cmView.state.doc.lineAt(block.from).number;
    } catch (_) {
      return null;
    }
  }
  return null;
}

function scrollFileEditorPanelToSourceLine(panel, source, line) {
  if (!panel || !line) return false;
  setFileEditorScrollSyncGuard(panel);
  if (source === 'preview') {
    const previewPane = fileEditorPanelPreviewPane(panel);
    return previewPane ? scrollPreviewToSourceLine(previewPane, line) : false;
  }
  if (panel._cmView) {
    try {
      const docLine = panel._cmView.state.doc.line(Math.min(line, panel._cmView.state.doc.lines));
      const scrollEffect = panel._cmView.constructor?.scrollIntoView?.(docLine.from, {y: 'start'});
      if (scrollEffect) panel._cmView.dispatch({effects: scrollEffect});
      else return false;
      return true;
    } catch (_) {
      return false;
    }
  }
  return false;
}

function fileEditorPanelsForPath(path) {
  return filePanelItemsForPath(path)
    .map(item => panelNodes.get(item))
    .filter(panel => panel && panel.isConnected !== false);
}

function renderLinkedFilePreviewPanels(sourcePanel, path, content) {
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel === sourcePanel) continue;
    const mode = fileEditorPanelMode(panel);
    if (mode !== 'preview' && mode !== 'split') continue;
    renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, content, {context: mode});
  }
}

function syncFileEditorInPaneSplitScroll(host, source) {
  const content = host.querySelector?.('.file-editor-content');
  if (!content?.classList?.contains('split-preview')) return false;
  const cmView = host._cmView || null;
  const editorScroller = cmView?.scrollDOM || null;
  const previewPane = host.querySelector?.('.file-editor-preview-pane-panel');
  if (!editorScroller || !previewPane || previewPane.hidden) return false;
  if (!fileEditorSourceCanDrive(host, source)) return false;
  const from = source === 'preview' ? previewPane : editorScroller;
  const to = source === 'preview' ? editorScroller : previewPane;
  setFileEditorScrollSyncGuardForSource(source, host);
  if (syncFileEditorSplitScrollBySourceAnchors(host, source, editorScroller, previewPane)) return true;
  return syncScrollPositionByRatio(from, to);
}

function syncFileEditorSplitScroll(host, source) {
  if (!host || fileEditorScrollSyncBlocked(host, source)) return;
  const canDrive = fileEditorSourceCanDrive(host, source);
  if (!canDrive) return;
  syncFileEditorInPaneSplitScroll(host, source);
  syncFilePreviewPopoutsFromPanel(host, source);
}

function scheduleFileEditorSplitScrollSync(host, source) {
  if (!host) return false;
  host._splitScrollPendingSource = source;
  if (host._splitScrollFrame) return true;
  const run = () => {
    host._splitScrollFrame = 0;
    const pendingSource = host._splitScrollPendingSource || source;
    host._splitScrollPendingSource = '';
    syncFileEditorSplitScroll(host, pendingSource);
  };
  if (typeof requestAnimationFrame === 'function') host._splitScrollFrame = requestAnimationFrame(run);
  else host._splitScrollFrame = setTimeout(run, 0);
  return true;
}

const fileEditorPreviewLayoutScrollSyncMs = 400;

function fileEditorPreviewScrollSyncSource(panel) {
  const layoutSyncActive = Number(panel?._previewLayoutScrollUntil || 0) > nowMs();
  return layoutSyncActive && fileEditorPanelMode(panel) === 'split' && fileEditorSourceCanDrive(panel, 'editor')
    ? 'editor'
    : 'preview';
}

function scheduleFileEditorPreviewLayoutSync(panel) {
  if (!panel) return false;
  panel._previewLayoutScrollUntil = nowMs() + fileEditorPreviewLayoutScrollSyncMs;
  return scheduleFileEditorSplitScrollSync(panel, 'editor');
}

function refreshEditorPreviews() {
  for (const [item, panel] of panelNodes.entries()) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    if (state?.kind && editorPreviewModeAvailable(path, state)) {
      renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content || '', {context: fileEditorPanelMode(panel)});
      updateFilePreviewPopout(path, state.content || '');
    }
  }
}

window.addEventListener('load', refreshEditorPreviews);

function fileEditorSaveHygieneOptions() {
  return {
    trimTrailingWhitespace: boolSetting('editor.trim_trailing_whitespace_on_save'),
    ensureFinalNewline: boolSetting('editor.ensure_final_newline_on_save'),
  };
}

function normalizeFileEditorSaveContent(content, options = fileEditorSaveHygieneOptions()) {
  let text = String(content ?? '');
  if (options.trimTrailingWhitespace === true) text = text.replace(/[ \t]+(?=\r?\n|$)/g, '');
  if (options.ensureFinalNewline === true && text && !text.endsWith('\n')) text += '\n';
  return text;
}

function syncFileEditorNormalizedContentToPanels(path, content) {
  for (const openPanel of fileEditorPanelsForPath(path)) {
    if (openPanel?._cmView) syncCodeMirrorDocument(openPanel._cmView, content, {path});
    const rawCode = openPanel?.querySelector?.('.file-editor-raw-panel code');
    if (rawCode) rawCode.textContent = content;
    const mode = fileEditorPanelMode(openPanel);
    if (mode === 'preview' || mode === 'split') {
      renderEditorPreviewPane(openPanel.querySelector('.file-editor-preview-pane-panel'), path, content, {context: mode});
    }
    const status = openFileStatus(openFiles.get(path));
    setFileEditorPanelStatus(openPanel, status.message, status.level);
  }
  renderLinkedFilePreviewPanels(null, path, content);
  updateFilePreviewPopout(path, content);
}

function applyFileEditorSaveHygiene(path) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  const nextContent = normalizeFileEditorSaveContent(state.content);
  if (nextContent === state.content) return false;
  state.content = nextContent;
  updateOpenFileDirtyFlag(path);
  syncFileEditorNormalizedContentToPanels(path, state.content);
  return true;
}

async function saveFileEditor(path, panel, options = {}) {
  if (readOnlyMode) return false;
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  syncOpenFileContentFromPanels(path, panel);
  if (!options.force && (state.externalChanged || state.externalMissing)) {
    if (!state.dirty) return reloadOpenFileFromDisk(path, {force: true});
    clearFileAutosaveTimer(path);
    return showFileSaveConflictDialog(path, panel);
  }
  applyFileEditorSaveHygiene(path);
  if (!state.dirty && options.force !== true) return true;
  setFileEditorPanelStatus(panel, t(options.autosave ? 'editor.autoSaving' : 'editor.saving'), '');
  try {
    const body = {
      path,
      content: state.content,
    };
    if (options.force !== true) body.expected_mtime = state.mtime;
    const response = await apiFetch('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const errorText = userMessageText(payload, response.statusText || String(response.status));
      if (response.status === 409) {
        setFileEditorPanelStatus(panel, t('dialog.conflictTitle'), 'warn');
        return showFileSaveConflictDialog(path, panel, {message: errorText});
      }
      setFileEditorPanelStatus(panel, t('editor.saveFailed', {error: errorText}), 'error');
      return false;
    }
    const payload = await response.json();
    applyFileIdentityMetadata(state, payload);
    registerFileIdentityForPath(path, state);
    state.mtime = filePayloadMtime(payload);
    state.size = payload.size;
    state.original = state.content;
    state.dirty = false;
    state.lastCleanAt = Date.now();
    clearFileAutosaveTimer(path);
    clearOpenFileExternalState(state);
    if (payload.yolo_rules) {
      yoloRulesPayload = payload.yolo_rules;
      renderPreferencesPanels();
    }
    for (const openPanel of fileEditorPanelsForPath(path)) {
      updateFileEditorPanelChrome(openPanel, path);
      setFileEditorPanelStatus(openPanel, t(options.autosave ? 'editor.autoSaved' : 'editor.saved', {size: formatFileSize(payload.size)}), 'ok');
    }
    renderSessionButtons();
    renderPaneTabStrips();
    sharePublishFileVersion(path, {mtime: state.mtime, size: state.size});
    return true;
  } catch (err) {
    setFileEditorPanelStatus(panel, t('editor.saveFailed', {error: err}), 'error');
    return false;
  }
}

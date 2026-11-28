// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Continuation of the editor module (split from 86_changes_editor.js to keep each partial under a
// readable size): the CodeMirror panel lifecycle, extensions, theming, and diff/preview rendering.
// Concatenated immediately after 90 by tools/static_build.py, so it shares the same bundle scope.

function destroyCodeMirrorPanel(panel, {invalidateEnsure = true} = {}) {
  if (panel && invalidateEnsure) panel._cmGeneration = Number(panel._cmGeneration || 0) + 1;
  panel?._cmResizeObserver?.disconnect?.();
  if (panel) panel._cmResizeObserver = null;
  panel?._diffOverviewViewportCleanup?.();
  if (panel) panel._diffOverviewWaitingForDeletedRows = false;
  // Clear the diff scrollbar overview so its red/green rail doesn't linger after switching to
  // edit/normal mode (only the diff build re-adds it via updateCodeMirrorDiffOverview).
  panel?.querySelector?.('.cm-diff-overview')?.remove();
  if (panel?._shareConnection) disconnectShareEditor(panel);
  if (panel?._cmView) {
    panel._cmView.destroy();
    panel._cmView = null;
  }
  if (panel) {
    delete panel._htmlSharePeers;
    delete panel._htmlShareSelection;
    clearTimeout(panel._cmSplitPreviewTimer);
    panel._cmSplitPreviewTimer = null;
    panel._cmSplitPreviewDraft = null;
    panel._cmSplitPreviewFinishedAt = null;
    panel._cmReviewCompartment = null;
    panel._cmReviewOriginal = null;
    panel._cmApi = null;
    panel._cmThemeCompartment = null;
    panel._cmBlameCompartment = null;
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

function destroyFileEditorPanel(panel) {
  removeHtmlEditorMobileAccessory(panel);
  const preview = panel?.querySelector?.('.file-editor-preview-pane-panel');
  if (preview) releasePreviewSurfaceResources(preview);
  destroyCodeMirrorPanel(panel);
  destroyProseMirrorPanel(panel);
}

function removeHtmlEditorMobileAccessory(panel) {
  const surface = panel?._htmlEditorMobileAccessory;
  if (!surface) return;
  const item = fileEditorPanelItem(panel);
  const state = terminalMobileAccessoryState(item);
  const press = terminalMobileAccessoryKeyPresses.get(item);
  clearTimeout(state?.launcherPress?.timer);
  clearTimeout(press?.delayTimer);
  clearTimeout(press?.repeatTimer);
  terminalMobileAccessoryKeyPresses.delete(item);
  terminalMobileAccessorySuppressedClicks.delete(item);
  terminalMobileAccessoryStates.delete(item);
  terminalMobileAccessoryObservePane(item, null);
  surface.remove();
  delete panel._htmlEditorMobileAccessory;
}

function syncHtmlEditorMobileAccessory(panel, state) {
  const item = fileEditorPanelItem(panel), path = fileEditorPanelPath(panel);
  if (state?.kind !== 'text' || state.loading || fileEditorPanelMode(panel) === 'diff' || previewKindForPath(path) !== 'html' ||
      !fileEditorStateWritable(state) || !terminalMobileAccessoryEnabled()) {
    removeHtmlEditorMobileAccessory(panel);
    return;
  }
  if (panel._htmlEditorMobileAccessory) return;
  const surface = document.createElement('div');
  surface.className = 'html-editor-mobile-keyboard';
  surface.innerHTML = terminalMobileAccessoryHtml(item);
  bindMobileAccessoryControls(surface, item);
  panel.append(surface);
  panel._htmlEditorMobileAccessory = surface;
}

function fileEditorMobileClipboard(action, text, paste) {
  if (action === 'copy') {
    if (text) void copyTextWithFeedback(text);
    return true;
  }
  if (action !== 'command-v') return false;
  navigator.clipboard?.readText?.().then(text => {
    if (text) paste(text);
  }).catch(error => statusErr(userMessageText(error, t('common.requestFailed'))));
  return true;
}

function focusHtmlEditorMobileAccessory(item) {
  const panel = panelNodes.get(item);
  if (!panel?._htmlEditorMobileAccessory || panel._cmView?.hasFocus) return;
  if (fileEditorPanelMode(panel) === 'edit') panel._cmView?.focus();
  else fileEditorPanelPreviewPane(panel)?.querySelector('.file-editor-html-preview')?._focusHtmlViewEditMobile?.();
}

function sendHtmlEditorMobileAccessoryInput(item, action, shiftKey) {
  const panel = panelNodes.get(item);
  if (!panel?.isConnected || !panel._htmlEditorMobileAccessory || !fileEditorStateWritable(fileEditorPanelState(panel))) return false;
  const mode = fileEditorPanelMode(panel);
  const key = htmlEditorMobileKeys[action];
  if (mode === 'split' || mode === 'preview') {
    const frame = fileEditorPanelPreviewPane(panel)?.querySelector('.file-editor-html-preview');
    if (frame?._htmlViewEditMobileKey?.(action, key, shiftKey)) return true;
  }
  const view = panel._cmView;
  if (!view?.hasFocus || (mode !== 'split' && mode !== 'edit')) return false;
  const {state} = view;
  if (fileEditorMobileClipboard(action, state.sliceDoc(state.selection.main.from, state.selection.main.to), text => {
    if (panel._cmView === view && view.hasFocus && view.state.doc === state.doc &&
        view.state.selection.eq(state.selection) && fileEditorStateWritable(fileEditorPanelState(panel))) {
      view.dispatch({...view.state.replaceSelection(text), userEvent: 'input.paste', scrollIntoView: true});
    }
  })) return true;
  if (!key) return false;
  view.contentDOM.dispatchEvent(new KeyboardEvent('keydown', {key, shiftKey, bubbles: true, cancelable: true}));
  return true;
}

function codeMirrorPanelContent(panel) {
  return panel?._cmView?.state?.doc?.toString?.() ?? null;
}

function codeMirrorSplitPreviewDelay(doc) {
  if (doc.length <= 12 * 1024 && doc.lines <= 400) return 0;
  if (doc.length <= 64 * 1024 && doc.lines <= 1600) return 60;
  if (doc.length <= 384 * 1024 && doc.lines <= 4500) return 125;
  if (doc.length <= 768 * 1024 && doc.lines <= 12000) return 250;
  return 400;
}

function codeMirrorSplitPreviewText(panel, path, state) {
  if (panel._cmPath !== path) return state.content;
  // HTML ViewEdit maps the new iframe to the source editor before deferred serialization.
  if (panel._cmView && previewKindForPath(path) === 'html' && fileEditorStateWritable(state)) {
    return panel._cmView.state.doc.toString();
  }
  const draft = panel._cmSplitPreviewDraft;
  return draft && draft.state === state && draft.baseline === state.content
    ? draft.text : state.content;
}

function scheduleCodeMirrorSplitPreview(panel, path) {
  const view = panel._cmView;
  const kind = previewKindForPath(path);
  const mode = fileEditorPanelMode(panel);
  if (!view || panel._cmPath !== path || (kind !== 'markdown' && kind !== 'html') ||
      (mode !== 'split' && !(kind === 'html' && mode === 'preview')) || !fileEditorPanelPreviewPane(panel)) return false;
  if (panel._cmSplitPreviewTimer != null || panel._cmSplitPreviewDraft?.doc === view.state.doc) return true;
  // Coalesce rapid input before asking the browser to navigate another sandboxed iframe.
  const delay = Math.max(kind === 'html' ? 50 : 0, codeMirrorSplitPreviewDelay(view.state.doc));
  const remaining = panel._cmSplitPreviewFinishedAt == null ? 0 : panel._cmSplitPreviewFinishedAt + delay - performance.now();
  const refresh = () => {
    panel._cmSplitPreviewTimer = null;
    const pane = fileEditorPanelPreviewPane(panel);
    if (!pane || panel.isConnected === false || panel._cmView !== view || panel._cmPath !== path ||
        fileEditorPanelMode(panel) !== mode ||
        panel._pmDocumentChanged || panel._pmError) return;
    if (view.composing || previewScrollUserOwnsElementNow(pane)) {
      panel._cmSplitPreviewTimer = setTimeout(refresh, Math.max(50, delay));
      return;
    }
    const state = fileEditorPanelState(panel);
    if (state?.kind !== 'text') return;
    const doc = view.state.doc;
    const text = doc.toString();
    const applied = panel._pmView
      ? syncProseMirrorPanelSource(panel, path, state, {previewText: text})
      : renderFileEditorPreviewSurface(panel, pane, path, text, {context: mode, state});
    panel._cmSplitPreviewFinishedAt = performance.now();
    if (applied !== false) panel._cmSplitPreviewDraft = {doc, text, state, baseline: state.content};
  };
  panel._cmSplitPreviewTimer = setTimeout(refresh, remaining > 0 ? remaining : delay);
  return true;
}

function commitCodeMirrorSource(panel, path) {
  if (!panel?._cmView || panel._cmPath !== path || panel._cmSyncing) return false;
  const state = fileEditorPanelState(panel);
  if (!state || state.historical === true) return false;
  if (state._shareActive) { shareHostLocalChanged(panel, path); return true; }
  const next = panel._cmView.state.doc.toString();
  const preview = previewKindForPath(path) === 'html' && scheduleCodeMirrorSplitPreview(panel, path)
    ? fileEditorPanelPreviewPane(panel) : null;
  if (next === state.content) {
    if (panel._cmSplitPreviewDraft && panel._cmSplitPreviewDraft.text !== next) {
      panel._cmSplitPreviewDraft = null;
      if (panel._pmView && !panel._pmDocumentChanged) syncProseMirrorPanelSource(panel, path, state);
    }
    return true;
  }
  handleFileEditorContentChanged(panel, path, next, {syntax: false, sourceSurface: 'text-editor', skipPreviewPanel: preview});
  return true;
}

function scheduleCodeMirrorSource(panel, path) {
  if (panel._cmSerializeTimer) clearTimeout(panel._cmSerializeTimer);
  const generation = Number(panel._cmSerializeGeneration || 0) + 1;
  panel._cmSerializeGeneration = generation;
  panel._cmSerializeTimer = setTimeout(() => {
    if (generation !== panel._cmSerializeGeneration || panel._cmPath !== path || !panel._cmView?.dom?.isConnected) return;
    panel._cmSerializeTimer = null;
    commitCodeMirrorSource(panel, path);
  }, PROSEMIRROR_SERIALIZE_DELAY_MS);
}

function flushCodeMirrorSource(panel, path) {
  if (!panel?._cmView || panel._cmPath !== path) return false;
  if (panel._cmSerializeTimer) clearTimeout(panel._cmSerializeTimer);
  panel._cmSerializeTimer = null;
  panel._cmSerializeGeneration = Number(panel._cmSerializeGeneration || 0) + 1;
  return commitCodeMirrorSource(panel, path);
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
    codeMirrorFileAccessExtensions(api, panel, path, {readOnly: true}),
    ...(options.plain ? [codeMirrorThemeOnlyExtensions(api, panel)] : [codeMirrorLanguageExtension(api, path), codeMirrorThemedExtensions(api, panel, path)]),
  ];
}

function captureCodeMirrorPanelViewState(panel, path) {
  const item = fileEditorPanelItem(panel) || fileEditorItemFor(path);
  if (item) captureFileEditorPanelViewState(item, panel);
}

function codeMirrorSplitSyncExtension(api, panel, path) {
  if (typeof api.EditorView?.domEventHandlers !== 'function') return [];
  return api.EditorView.domEventHandlers({
    blur(_event, view) {
      flushCodeMirrorSource(panel, path);
      return false;
    },
  });
}

function codeMirrorDeletedSelectionText(view) {
  const document = view.dom.ownerDocument;
  const selection = document?.getSelection?.();
  if (!selection || selection.isCollapsed || !view.contentDOM.contains(selection.anchorNode)
      || !view.contentDOM.contains(selection.focusNode)) return '';
  const active = document.activeElement;
  if (active && active !== document.body && active !== document.documentElement && !view.dom.contains(active)) return '';
  const inDeletion = node => Boolean((node?.nodeType === 1 ? node : node?.parentElement)?.closest('.cm-deletedChunk'));
  if (!inDeletion(selection.anchorNode) && !inDeletion(selection.focusNode)) {
    // A drag starting or ending at a widget boundary can report the content container as its endpoint.
    // For an ordinary CodeMirror selection that spans widgets, leave copying to CodeMirror.
    if (!view.state.selection.main.empty || !selection.rangeCount) return '';
    const range = selection.getRangeAt(0);
    if (!Array.from(view.contentDOM.querySelectorAll('.cm-deletedChunk')).some(chunk => range.intersectsNode(chunk))) return '';
  }
  return selection.toString();
}

function codeMirrorContextMenuSelectionExtension(api) {
  return safeCodeMirrorExtension('context menu selection', () => {
    if (!api.ViewPlugin) return [];
    return api.ViewPlugin.fromClass(class {
      constructor(view) {
        this.view = view;
        this.pending = null;
        this.clearTimer = 0;
        this.copyDeletedSelection = event => {
          const selected = codeMirrorDeletedSelectionText(view);
          if (!selected || !event.clipboardData) return;
          event.clipboardData.setData('text/plain', selected);
          event.preventDefault();
          event.stopPropagation();
        };
        // Let native Copy reach the clipboard event even if async clipboard writes are blocked.
        this.cutDeletedSelectionShortcut = event => {
          if (event.key?.toLowerCase() !== 'x' || event.altKey || event.shiftKey || event.ctrlKey === event.metaKey) return;
          const selected = codeMirrorDeletedSelectionText(view);
          if (!selected) return;
          event.preventDefault();
          event.stopPropagation();
          void copyTextWithFeedback(selected);
        };
        this.captureSelectionAtRightMouseDown = event => {
          if (event.button !== 2 || !this.view.dom.contains(event.target) || event.target.closest?.('.cm-deletedChunk')) return;
          const selection = this.view.state.selection;
          const position = this.view.posAtCoords({x: event.clientX, y: event.clientY});
          if (!Number.isFinite(position) || !selection.ranges.some(range => !range.empty && position >= range.from && position <= range.to)) return;
          // Capture the selection before CodeMirror/contenteditable processes the native right
          // mousedown. The later contextmenu coordinates can map to the collapsed cursor, so the
          // captured in-selection decision is the authoritative gesture state.
          this.pending = {selection, position};
          clearTimeout(this.clearTimer);
          this.clearTimer = setTimeout(() => {
            if (this.pending?.selection === selection) this.pending = null;
          }, 250);
        };
        this.handleContextMenu = event => {
          if (!this.view.dom.contains(event.target) || event.target.closest?.('.cm-deletedChunk')) return;
          // Capture-phase listeners run before CodeMirror's own contextmenu handling. Chrome can
          // otherwise collapse contenteditable's selection before CodeMirror observes the event.
          const captured = this.pending;
          this.pending = null;
          clearTimeout(this.clearTimer);
          this.clearTimer = 0;
          const selection = captured?.selection || this.view.state.selection;
          const clickedSelection = captured !== null && captured !== undefined
            || selection.ranges.some(range => !range.empty);
          if (!clickedSelection) return;
          const panel = this.view.dom.closest('.file-editor-panel');
          const path = panel?.dataset?.filePath || '';
          // Restore before opening any editor menu. The native right-click transaction may have
          // collapsed the selection, and the Markdown menu reads the live CodeMirror selection
          // when its formatting actions are invoked.
          const restoreSelection = () => {
            if (!this.view.dom?.isConnected || this.view.state.selection.eq(selection)) return;
            this.view.dispatch({selection});
            updateCodeMirrorCursorStatus(panel);
          };
          restoreSelection();
          queueMicrotask(restoreSelection);
          requestAnimationFrame(restoreSelection);
          if (previewKindForPath(path) === 'markdown') {
            const range = selection.main;
            const line = this.view.state.doc.lineAt(range.from);
            const selectedText = this.view.state.sliceDoc(range.from, range.to);
            if (selectedText.trim()) {
              event.preventDefault();
              event.stopPropagation();
              markdownEditorContextMenu(this.view, panel, path, event, {
                selectedText,
                sourceLine: line.number,
              });
              return;
            }
          }
          // Restore synchronously while handling contextmenu. Waiting for an update listener loses the
          // race when the native right-mousedown does not produce a CodeMirror selection transaction.
        };
        // The W3C pointer path used by Chromium/Selenium can deliver pointerdown before the
        // synthesized mousedown has a chance to run; both events must feed the same capture owner.
        document.addEventListener('pointerdown', this.captureSelectionAtRightMouseDown, true);
        document.addEventListener('mousedown', this.captureSelectionAtRightMouseDown, true);
        document.addEventListener('contextmenu', this.handleContextMenu, true);
        document.addEventListener('copy', this.copyDeletedSelection, true);
        document.addEventListener('cut', this.copyDeletedSelection, true);
        document.addEventListener('keydown', this.cutDeletedSelectionShortcut, true);
      }

      destroy() {
        document.removeEventListener('pointerdown', this.captureSelectionAtRightMouseDown, true);
        document.removeEventListener('mousedown', this.captureSelectionAtRightMouseDown, true);
        document.removeEventListener('contextmenu', this.handleContextMenu, true);
        document.removeEventListener('copy', this.copyDeletedSelection, true);
        document.removeEventListener('cut', this.copyDeletedSelection, true);
        document.removeEventListener('keydown', this.cutDeletedSelectionShortcut, true);
        clearTimeout(this.clearTimer);
        this.pending = null;
      }
    });
  });
}

function updateCodeMirrorViewPreservingState(view, update, options = {}) {
  if (!view || typeof update !== 'function') return false;
  const selection = options.preserveSelection === false ? null : view.state.selection;
  const scrollSnapshot = typeof view.scrollSnapshot === 'function' ? view.scrollSnapshot() : null;
  update(selection, scrollSnapshot);
  const currentSelection = view.state.selection;
  const selectionPreserved = !selection || currentSelection === selection || currentSelection?.eq?.(selection) === true;
  if (!selectionPreserved) {
    try { view.dispatch({selection, ...(scrollSnapshot ? {effects: scrollSnapshot} : {})}); } catch (_) {}
  }
  return true;
}

// Replaces only the differing middle, so carets, scroll, and preview offsets outside it survive.
function codeMirrorTextReplacement(doc, next) {
  const current = doc.toString(), shorter = Math.min(current.length, next.length);
  let from = 0, suffix = 0;
  while (from < shorter && current.charCodeAt(from) === next.charCodeAt(from)) from++;
  while (suffix < shorter - from &&
      current.charCodeAt(current.length - 1 - suffix) === next.charCodeAt(next.length - 1 - suffix)) suffix++;
  return {from, to: current.length - suffix, insert: next.slice(from, next.length - suffix)};
}

function syncCodeMirrorDocument(view, text, options = {}) {
  if (!view) return;
  const next = String(text || '');
  if (view.state.doc.toString() === next) return;
  if (options.cleanOnly && (options.state || fileState.get(options.path))?.dirty) return;
  const selection = options.preserveSelection === false ? null : view.state.selection;
  const cursorAtEndBeforeFinalNewline = options.selection === 'end-before-final-newline';
  const selectionFits = selection?.ranges?.every(range => (
    range.anchor <= next.length && range.head <= next.length
  ));
  const panel = view.dom?.closest?.('.file-editor-panel');
  if (panel) panel._cmSyncing = true;
  try {
    updateCodeMirrorViewPreservingState(view, (preservedSelection, scrollSnapshot) => {
      view.dispatch({
        changes: codeMirrorTextReplacement(view.state.doc, next),
        ...(preservedSelection ? {selection: preservedSelection} : {}),
        ...(scrollSnapshot ? {effects: scrollSnapshot} : {}),
      });
    }, {preserveSelection: cursorAtEndBeforeFinalNewline ? false : selectionFits});
    if (cursorAtEndBeforeFinalNewline) {
      const position = next.endsWith('\n') ? Math.max(0, next.length - 1) : next.length;
      view.dispatch({selection: {anchor: position, head: position}});
    }
  } finally {
    if (panel) panel._cmSyncing = false;
  }
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
    codeMirrorRecentReviewExtension(api, panel),
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
    codeMirrorBlameCompartmentExtensions(api,panel,path),
    codeMirrorFileAccessExtensions(api, panel, path),
    codeMirrorThemedExtensions(api, panel, path),
    codeMirrorSplitSyncExtension(api, panel, path),
  ];
}

function codeMirrorFileAccessExtensions(api, panel, path, options = {}) {
  const writable = options.readOnly !== true && fileEditorStateWritable(fileEditorPanelState(panel) || fileState.get(path));
  const extensions = [api.EditorState.readOnly.of(!writable), api.EditorView.editable.of(writable)];
  if (!panel || !api.Compartment) return extensions;
  panel._cmAccessCompartment = panel._cmAccessCompartment || new api.Compartment();
  panel._cmAccessReadOnly = options.readOnly === true;
  panel._cmAccessWritable = writable;
  return panel._cmAccessCompartment.of(extensions);
}

function refreshFileEditorWriteAccess(panel, state) {
  syncHtmlEditorMobileAccessory(panel, state);
  syncHtmlViewEditStatus(fileEditorPanelPreviewPane(panel));
  const writable = fileEditorStateWritable(state);
  panel.classList.toggle('file-editor-readonly', !writable);
  const cmWritable = writable && panel._cmAccessReadOnly !== true;
  if (panel._cmView && panel._cmAccessCompartment && panel._cmAccessWritable !== cmWritable) {
    const api = panel._cmApi;
    panel._cmAccessWritable = cmWritable;
    panel._cmView.dispatch({effects: panel._cmAccessCompartment.reconfigure([
      api.EditorState.readOnly.of(!cmWritable), api.EditorView.editable.of(cmWritable),
    ])});
  }
  if (panel._pmView && panel._pmAccessWritable !== writable) {
    panel._pmAccessWritable = writable;
    panel._pmView.setProps({editable: () => prosemirrorPanelEditable(panel)});
  }
}

function codeMirrorEditorOptionExtensions(api, options = {}) {
  const extensions = [];
  if (options.lineNumbers !== false && fileEditorLineNumbersEnabled) {
    const lineNumbers = safeCodeMirrorExtension('line numbers', () => api.lineNumbers?.());
    const activeLineGutter = safeCodeMirrorExtension('active line gutter', () => api.highlightActiveLineGutter?.());
    extensions.push(...[lineNumbers, activeLineGutter].flat().filter(Boolean));
  }
  if (options.wrap !== false && fileEditorWrapForPath(options.path || '', options.state || null)) {
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
  const path = options.path || panel?.dataset?.filePath || '';
  const state = options.state || (path ? fileState.get(path) : null);
  const extensions = codeMirrorEditorOptionExtensions(api, {...options, path, state});
  if (!panel || !api.Compartment) return extensions;
  panel._cmEditorOptionCompartment = panel._cmEditorOptionCompartment || new api.Compartment();
  panel._cmEditorOptionConfig = {
    wrap: options.wrap !== false,
    path,
    lineNumbers: options.lineNumbers !== false,
  };
  return panel._cmEditorOptionCompartment.of(extensions);
}

function createEditableCodeMirrorState(api, panel, path, doc) {
  try {
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorExtensions(api, panel, path, {path, state: fileState.get(path)}),
      }),
      plain: false,
    };
  } catch (error) {
    console.warn('CodeMirror language parser failed; retrying plain editable editor', error);
    if (panel) {panel._cmThemeCompartment = null;panel._cmBlameCompartment=null;}
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorPlainEditableExtensions(api, panel, path, {path, state: fileState.get(path)}),
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

function codeMirrorBlameCompartmentExtensions(api,panel,path) {
  const extensions=codeMirrorBlameExtension(api,path);
  if(!panel || !api.Compartment)return extensions;
  panel._cmBlameCompartment=panel._cmBlameCompartment || new api.Compartment();
  return panel._cmBlameCompartment.of(extensions);
}

function reconfigureCodeMirrorPanelBlame(panel) {
  const api=panel?._cmApi,compartment=panel?._cmBlameCompartment;
  const views=Array.isArray(panel?._cmViews)?panel._cmViews:[];
  if(!api || !compartment || !views.length)return false;
  const effect=compartment.reconfigure(codeMirrorBlameExtension(api,panel._cmPath));
  for(const view of views)updateCodeMirrorViewPreservingState(view,(selection,scrollSnapshot)=>view.dispatch({effects:scrollSnapshot?[effect,scrollSnapshot]:effect,...(selection?{selection}:{})}));
  return true;
}

function reconfigureCodeMirrorPanelLocale(panel) {
  const api = panel?._cmApi;
  const compartment = panel?._cmLocaleCompartment;
  const views = Array.isArray(panel?._cmViews) ? panel._cmViews : [];
  if (!api || !compartment || !views.length) return false;
  const effect = compartment.reconfigure(codeMirrorLocaleExtensions(api, null));
  for (const view of views) {
    try {
      updateCodeMirrorViewPreservingState(view, (selection, scrollSnapshot) => view.dispatch({
        effects: scrollSnapshot ? [effect, scrollSnapshot] : effect,
        ...(selection ? {selection} : {}),
      }));
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

function diffOverviewCodeMirrorChunks(view) {
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
  const chunks = diffOverviewCodeMirrorChunks(view);
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
    await refreshOpenFileDiff(path, {item, state, silent: true, renderOnComplete: false});
    if (panel._cmGeneration !== generation) return null;
  }
  if (!fileStateCanRenderDiffView(path, state)) {
    if (state.diffUnavailable) {
      const msg = t('editor.diffUnavailable', {error: state.diffError || t('common.unknown')});
      setFileEditorPanelStatus(panel, msg, 'warn');
      if (state.historical === true) return false;
      return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
    }
    if (state.historical === true) return false;
    return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
  }
  const original = String(state.diffOriginal || '');
  try {
    const api = await loadCodeMirrorApi();
    if (panel._cmGeneration !== generation) return null;
    if (!api.unifiedMergeView) {
      setFileEditorPanelStatus(panel, t('editor.codemirrorMergeUnavailable'), 'error');
      return false;
    }
    const layout = codeMirrorDiffLayout(container);
    const expandUnchanged = fileEditorDiffExpandUnchangedForItem(item);
    const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';
    const currentText = diffTargetIsCurrent ? String(state.content || '') : String(state.diffWorking || '');
    const diffEditsAllowed = diffTargetIsCurrent && state.historical !== true;
    const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef, expand: expandUnchanged});
    installCodeMirrorDiffCollapsedScrollGuard(panel, container);
    if (panel._cmView && panel._cmMode === 'diff' && panel._cmSignature === signature) {
      installCodeMirrorDiffResizeObserver(panel, item, path, container);
      syncCodeMirrorDocument(panel._cmView, currentText, {cleanOnly: true, path, state});
      updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
      scheduleDiffOverviewSettledRebuild(panel);
      restoreFileEditorPanelViewState(item, panel);
      updateCodeMirrorCursorStatus(panel);
      focusFileEditorPanelIfReady(panel, item);
      return true;
    }
    captureFileEditorPanelViewState(item, panel);
    destroyCodeMirrorPanel(panel, {invalidateEnsure: false});
    container.replaceChildren();
    panel._cmDiffLayout = layout;
    installCodeMirrorDiffResizeObserver(panel, item, path, container);
    installCodeMirrorDiffCollapsedScrollGuard(panel, container);
    const unifiedMergeOptions = codeMirrorUnifiedMergeOptions(original, {
      mergeControls: !readOnlyMode && diffEditsAllowed,
      // B4: expand-all omits collapseUnchanged so every unchanged line shows; else collapse the runs.
      ...(expandUnchanged ? {} : {collapseUnchanged: {margin: 3, minSize: 8}}),
    });
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
        mapMarkdownTypingPair(panel, transaction);
        panel._cmView.update([transaction]);
        refreshMarkdownFormattingToolbar(panel);
        if (transaction.docChanged || transaction.selection) {
          updateCodeMirrorCursorStatus(panel, transaction.docChanged);
          captureCodeMirrorPanelViewState(panel, path);
          if (transaction.selection && !transaction.docChanged && typeof selectMarkdownPreviewSourceRange === 'function') {
            const selection = transaction.newSelection.main;
            const line = transaction.newDoc.lineAt(selection.from);
            const text = transaction.newDoc.sliceString(selection.from, selection.to);
            selectMarkdownPreviewSourceRange(path, line.number, text);
          }
        }
        if (transaction.docChanged) {
          if (!panel._cmSyncing) scheduleCodeMirrorSource(panel, path);
        }
      },
    });
    panel._cmView.scrollDOM?.addEventListener('scroll', () => {
      scheduleFileEditorSplitScrollSync(panel, 'editor');
      scheduleFileEditorPanelViewStateCapture(item, panel);
    });
    trackCodeMirrorViews(panel, api, [panel._cmView]);
    panel._cmPath = path;
    panel._cmSignature = signature;
    panel._cmMode = 'diff';
    updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
    scheduleDiffOverviewSettledRebuild(panel);
    restoreFileEditorPanelViewState(item, panel, {restoreFresh: true});
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
  container.hidden = options.hidden === true;
  container.classList.remove('file-editor-diff-codemirror');
  const currentText = String(state.content || '');
  const signature = codeMirrorConfigSignature(path, {mode: 'edit'});
  if (!panel._cmView || panel._cmPath !== path || panel._cmSignature !== signature) {
    captureFileEditorPanelViewState(item, panel);
    destroyCodeMirrorPanel(panel, {invalidateEnsure: false});
    container.textContent = t('editor.codemirrorLoading');
  }
  try {
    const api = await loadCodeMirrorApi();
    if (panel._cmGeneration !== generation) return null;
    const created = !panel._cmView;
    if (created) {
      container.replaceChildren();
      const createdState = createEditableCodeMirrorState(api, panel, path, currentText);
      panel._cmView = new api.EditorView({
        state: createdState.state,
        parent: container,
        dispatch(transaction) {
          mapMarkdownTypingPair(panel, transaction);
          if (transaction.docChanged && panel._htmlSharePeers) mapSharedHtmlPresence(panel, transaction);
          if (transaction.docChanged) mapHtmlPreviewSources(panel, transaction);
          panel._cmView.update([transaction]);
          if (transaction.docChanged) htmlEditorPreviewFrame(panel)?._htmlRebaseActiveText?.(transaction);
          const editorChanged = transaction.docChanged || transaction.selection;
          if (editorChanged) scheduleSplitEditorCaret(panel, Boolean(transaction.selection && !transaction.docChanged));
          const currentState = fileEditorPanelState(panel);
          const changed = transaction.docChanged && !panel._cmSyncing;
          const localPreview = changed && scheduleCodeMirrorSplitPreview(panel, path);
          if (currentState?._shareActive && transaction.docChanged) {
            if (changed && !panel._pmApplyingSharedEdit && panel._pmView?.hasFocus()) {
              syncProseMirrorPanelSource(panel, path, currentState);
            }
            if (changed) markSharedEditorPending(panel, currentState);
            scheduleSharedEditorPresentation(panel, path, currentState, changed && !localPreview,
              !panel._cmSyncing && !transaction.annotation(api.Transaction.remote));
            return;
          }
          if (editorChanged || transaction.reconfigured) refreshMarkdownFormattingToolbar(panel);
          if (editorChanged) {
            updateCodeMirrorCursorStatus(panel, transaction.docChanged);
            captureCodeMirrorPanelViewState(panel, path);
          }
          if (changed) scheduleCodeMirrorSource(panel, path);
        },
      });
      panel._cmPath = path;
      panel._cmSignature = signature;
      panel._cmMode = 'edit';
      panel._cmPlainFallback = Boolean(createdState.plain);
      panel._cmView.contentDOM.addEventListener('focus', () => {
        panel._shareCaretSurface = 'text';
        delete panel._htmlShareSelection;
        scheduleSplitEditorCaret(panel);
      });
      panel._cmView.contentDOM.addEventListener('blur', () => scheduleSplitEditorCaret(panel));
      panel._cmView.contentDOM.addEventListener('click', event => {
        if (!event.ctrlKey && !event.metaKey) scheduleSplitEditorCaret(panel, true);
      });
      panel._cmView.scrollDOM?.addEventListener('scroll', () => {
        scheduleFileEditorSplitScrollSync(panel, 'editor');
        scheduleFileEditorPanelViewStateCapture(item, panel);
      });
      trackCodeMirrorViews(panel, api, [panel._cmView]);
      scheduleSplitEditorCaret(panel);
      updateCodeMirrorCursorStatus(panel);
      if (createdState.plain) {
        setFileEditorPanelStatus(panel, t('editor.codemirrorPlainText'), 'warn');
      }
    } else if (!state._shareActive && panel._cmView.state.doc.toString() !== currentText && !state.dirty && !panel._cmSerializeTimer) {
      const changes = codeMirrorTextReplacement(panel._cmView.state.doc, currentText);
      // A redraw overwrote TextEdit from the saved copy. Typing can be lost here, so keep evidence.
      recordJsDebugEvent('editor', {category: 'editor', ok: false, message: `TextEdit replaced from saved copy ${JSON.stringify({
        path, focused: panel._cmView.hasFocus, docLength: panel._cmView.state.doc.length, savedLength: currentText.length,
        from: changes.from, to: changes.to, inserted: changes.insert.length,
      })}`});
      panel._cmView.dispatch({changes});
      updateCodeMirrorCursorStatus(panel);
    }
    syncCodeMirrorRecentReview(panel, state);
    restoreFileEditorPanelViewState(item, panel, {restoreFresh: created});
    focusFileEditorPanelIfReady(panel, item);
    applyPendingFileEditorLineTarget(item, panel);
    bindTexPreviewScrollSync(panel);
    if (state._shareActive) await shareAttachHostPanel(panel, path);
    ensureMarkdownFormattingToolbar(panel);
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
  rawPane.classList.toggle('editor-wrap', fileEditorWrapForPath(path));
  code.className = `language-${language || 'text'}`;
  code.innerHTML = editorVisualHighlightHtml(language, content, {
    wrap: fileEditorWrapForPath(path),
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
  return state?.historical !== true
    && state?.kind === 'text'
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
    reviewButton: panel.querySelector('.file-editor-review-panel'),
    reviewToolbar: panel.querySelector('.file-editor-review-toolbar'),
    diffButton: panel.querySelector('.file-editor-diff-panel'),
    diffRefPanel: panel.querySelector('.file-editor-diff-ref-panel'),
    diffExpandButton: panel.querySelector('.file-editor-diff-expand-panel'),
    reloadButton: panel.querySelector('.file-editor-reload-panel'),
    uploadButton: panel.querySelector('.file-editor-upload-panel'),
    themeButton: panel.querySelector('.file-editor-theme-panel'),
    blameButton: panel.querySelector('.file-editor-blame-panel'),
    saveButton: panel.querySelector('.file-editor-save-panel'),
    content: panel.querySelector('.file-editor-content'),
  };
  parts.textControls = [
    parts.reviewButton,
    parts.reviewToolbar,
    parts.modeControl,
    parts.previewFontPanel,
    parts.gutterButton,
    parts.wrapButton,
    parts.findButton,
    parts.blameButton,
    parts.diffButton,
    parts.diffExpandButton,
    parts.diffRefPanel,
    parts.reloadButton,
    parts.uploadButton,
  ];
  return parts;
}

function labelSplitEditorSurfaces(panel) {
  const textPane = panel?.querySelector?.('[data-editor-surface="text-editor"]');
  const viewPane = panel?.querySelector?.('[data-editor-surface="view-editor"]');
  if (textPane) textPane.setAttribute('aria-label', t('editor.surface.textEditor'));
  if (viewPane) viewPane.setAttribute('aria-label', t('editor.surface.viewEditor'));
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
  if (state.historical === true) {
    const historySignature = JSON.stringify([state.diffPinnedFromRef, state.diffPinnedToRef, state.historicalComparisonKind]);
    if (!diffRefPanel.hidden && diffRefPanel.dataset.diffRefHistoryRendered !== historySignature) {
      diffRefPanel.innerHTML = historicalDiffRefControlsHtml(state);
      diffRefPanel.dataset.diffRefRepoRendered = state.gitRoot || state.diffRepo || '';
      diffRefPanel.dataset.diffRefPathRendered = path;
      diffRefPanel.dataset.diffRefHistoryRendered = historySignature;
    }
    return;
  }
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
  syncRecentReviewControls(panel, path, state, mode);
  const diffExpandButton = parts.diffExpandButton;
  const previewable = editorPreviewModeAvailable(path, state);
  const renderer = previewRendererForPath(path, state);
  updateEditorThemeButton(parts.themeButton, {includeVanilla: true});
  updateEditorModeControl(parts.modeControl, path, state, item);
  if (parts.previewFontPanel) {
    parts.previewFontPanel.hidden = state.kind !== 'text' || !previewable || renderer.id === 'tex' || (mode !== 'preview' && mode !== 'split');
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
  if (mode !== 'preview' && mode !== 'split') closePreviewFind(panel);
  // Git-backed controls share file-history gating, but Diff also depends on the loaded diff state while
  // Blame stays available in normal edit mode for clean files with useful history.
  updateFileEditorBlameButton(parts.blameButton, path, state, item);
  updateFileEditorDiffButton(parts.diffButton, path, state, item);
  updateFileEditorDiffExpandButton(diffExpandButton, path, state, item);
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
    renderFileEditorPreviewSurface(panel, parts.previewPane, path, '', {context: 'preview'});
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

// A busy server can time out the editor bundle. Retry on a backoff instead of waiting for a reload.
const htmlViewEditLoadRetryDelaysMs = [3000, 10000, 30000];

function scheduleHtmlViewEditLoadRetry(panel, pane, attempt) {
  if (!attempt.failed) {
    delete pane._htmlViewEditLoadRetries;
    return;
  }
  const retries = Number(pane._htmlViewEditLoadRetries || 0);
  if (retries >= htmlViewEditLoadRetryDelaysMs.length) return;
  const delay = htmlViewEditLoadRetryDelaysMs[retries];
  pane._htmlViewEditLoadRetries = retries + 1;
  recordJsDebugEvent('editor', {category: 'editor', ok: false,
    message: `ViewEdit editor load failed for ${attempt.path}; retry ${retries + 1} in ${delay / 1000}s`});
  setTimeout(() => {
    // A newer load, a Retry click, or a closed pane already owns this pane.
    if (pane._htmlViewEditLoad !== attempt || pane.hidden || !panel.isConnected) return;
    retryHtmlViewEditLoad(panel, pane);
  }, delay);
}

async function renderHtmlViewEditWhenReady(panel, pane, path, state, mode, ensureSource) {
  const render = current => renderFileEditorPreviewSurface(panel, pane, path,
    codeMirrorSplitPreviewText(panel, path, current), {context: mode, state: current});
  const previous = pane._htmlViewEditLoad;
  if (previous?.path === path && previous.mode === mode && previous.failed) {
    render(state);
    syncHtmlViewEditStatus(pane);
    return false;
  }
  const attempt = {path, mode, failed: false};
  pane._htmlViewEditLoad = attempt;
  const sourceReady = ensureSource();
  render(state);
  syncHtmlViewEditStatus(pane);
  let loaded = false;
  try {
    loaded = await sourceReady;
    return loaded;
  } finally {
    if (pane._htmlViewEditLoad === attempt && !pane.hidden &&
        fileEditorPanelPath(panel) === path && fileEditorPanelMode(panel) === mode) {
      attempt.failed = loaded === false;
      const current = fileEditorPanelState(panel);
      if (loaded && panel.isConnected && current?.kind === 'text' && !current.loading) render(current);
      syncHtmlViewEditStatus(pane);
      scheduleHtmlViewEditLoadRetry(panel, pane, attempt);
    }
  }
}

function renderTextPreviewMode(panel, item, path, state, parts) {
  const htmlEditor = previewKindForPath(path) === 'html' && fileEditorStateWritable(state);
  if (!state._shareActive && !htmlEditor) destroyCodeMirrorPanel(panel);
  if (parts.rawPane) parts.rawPane.hidden = true;
  if (parts.codeMirrorPane) parts.codeMirrorPane.hidden = true;
  panel.classList.remove('syntax-highlighted');
  if (parts.previewPane) {
    parts.previewPane.hidden = false;
    if (prosemirrorSupportedSource(path, state)) {
      renderProseMirrorPreviewMode(panel, item, path, state, parts);
      if (state._shareActive) void ensureCodeMirrorPanel(panel, item, path, state, {hidden: true});
    } else {
      destroyProseMirrorPanel(panel);
      delete panel._pmRequired;
      if (htmlEditor) {
        void renderHtmlViewEditWhenReady(panel, parts.previewPane, path, state, 'preview',
          () => ensureCodeMirrorPanel(panel, item, path, state, {hidden: true})).catch(error => {
          if (fileEditorPanelPath(panel) !== path || fileEditorPanelMode(panel) !== 'preview') return;
          setFileEditorPanelStatus(panel, t('editor.codemirrorUnavailable', {error}), 'error');
        });
      } else {
        renderFileEditorPreviewSurface(panel, parts.previewPane, path, state.content, {context: 'preview', state});
        if (state._shareActive && previewKindForPath(path) === 'html') {
          void ensureCodeMirrorPanel(panel, item, path, state, {hidden: true});
        }
      }
    }
  }
}

function renderTextCodeMode(panel, item, path, state, parts, mode) {
  if (mode !== 'split') {
    destroyProseMirrorPanel(panel);
    delete panel._pmRequired;
  }
  const rawPane = parts.rawPane;
  const previewPane = parts.previewPane;
  let sourceReady;
  if (rawPane) rawPane.hidden = true;
  if (previewPane) {
    previewPane.hidden = mode !== 'split';
    if (mode === 'split') {
      if (prosemirrorSupportedSource(path, state)) {
        renderProseMirrorPreviewMode(panel, item, path, state, parts);
      } else {
        destroyProseMirrorPanel(panel);
        delete panel._pmRequired;
        if (previewKindForPath(path) === 'html' && fileEditorStateWritable(state)) {
          sourceReady = renderHtmlViewEditWhenReady(panel, previewPane, path, state, mode,
            () => ensureCodeMirrorPanel(panel, item, path, state));
        } else {
          renderFileEditorPreviewSurface(panel, previewPane, path, codeMirrorSplitPreviewText(panel, path, state), {context: mode, state});
        }
      }
    }
  }
  panel.classList.remove('syntax-highlighted');
  (sourceReady || ensureCodeMirrorPanel(panel, item, path, state)).then(loaded => {
    if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);
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
  labelSplitEditorSurfaces(panel);
  resetImagePreviewPane(parts);
  setEditorContentMode(parts.content, mode);
  panel.classList.toggle('editor-wrap', fileEditorWrapForPath(path, state));
  panel.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
  if (mode === 'preview') renderTextPreviewMode(panel, item, path, state, parts);
  else renderTextCodeMode(panel, item, path, state, parts, mode);
  scheduleSplitEditorCaret(panel);
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
  focusFileEditorPanelIfReady(panel, item);
}

function renderFileEditorPanel(panel, item, options = {}) {
  if (panel._guestTerminal) return;
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
  const state = fileEditorStateForItem(path, item);
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
  const state = fileEditorStateForItem(path, item);
  if (!state || state.loadingPromise) return;
  if (state.historical === true) {
    const identity = historicalFileEditorIdentity(item);
    if (!identity) return;
    const loadingPromise = refreshOpenFileDiff(path, {
      item,
      state,
      fromRef: identity.fromRef,
      toRef: identity.toRef,
      silent: true,
      renderOnComplete: false,
      updateControlsOnComplete: false,
    }).finally(() => {
      state.loading = false;
      if (state.loadingPromise === loadingPromise) delete state.loadingPromise;
      if (panel) renderFileEditorPanel(panel, item);
      renderSessionButtons();
      renderPaneTabStrips();
    });
    state.loadingPromise = loadingPromise;
    return;
  }
  const load = captureOpenFileLoad(path, state);
  const loadingPromise = (async () => {
    const kind = openFileKindForPreviewPath(basenameOf(path));
    let next;
    if (kind === 'image' || kind === 'media') {
      const loaded = await openFileStateFromDisk(path);
      if (loaded.superseded || !openFileLoadStillCurrent(path, load)) return;
      if (loaded.missing) {
        const verdict = await resolveOpenMediaMissingVerdict(path);
        if (!verdict || !openFileLoadStillCurrent(path, load)) return;
        next = verdict.state || (verdict.missing ? missingFileState() : fileErrorState(verdict.error));
      } else next = loaded.state;
    } else next = await readTextFileState(path, null, {previous: state});
    if (!completeOpenFileLoad(path, load, next)) return;
    if (panel) renderFileEditorPanel(panel, item);
    renderSessionButtons();
    renderPaneTabStrips();
  })().finally(() => {
    if (state.loadingPromise === loadingPromise) delete state.loadingPromise;
  });
  state.loadingPromise = loadingPromise;
  return loadingPromise;
}

function updateFileEditorPanelChrome(panel, path) {
  const item = panel?.dataset?.layoutItem || '';
  const state = fileEditorStateForItem(path, item);
  const previewOnly = state?.historical === true;
  const writable = fileEditorStateWritable(state);
  refreshFileEditorWriteAccess(panel, state);
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
    saveButton.hidden = previewOnly || !writable || state?.kind !== 'text';
    saveButton.disabled = !state?.dirty;
  }
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  if (reloadButton) {
    reloadButton.hidden = previewOnly || state?.kind !== 'text';
  }
  const themeButton = panel.querySelector('.file-editor-theme-panel');
  if (themeButton) themeButton.hidden = previewKindForPath(path) === 'html';
  const uploadButton = panel.querySelector('.file-editor-upload-panel');
  if (uploadButton) {
    const viewMode = editorViewModeFor(path, item);
    uploadButton.hidden = !writable || state?.kind !== 'text' || previewRendererForPath(path)?.id !== 'markdown' || !['edit', 'split'].includes(viewMode);
  }
  updateFileEditorToolbarSeparators(panel);
  ensureMarkdownFormattingToolbar(panel);
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
    '.file-editor-upload-panel',
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
  if (messageNode.textContent !== (message || '')) messageNode.textContent = message || '';
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
  const explicit = Array.from(previewPane?.querySelectorAll?.('[data-source-line]') || [])
    .map(element => ({element, line: Number(element.dataset.sourceLine)}))
    .filter(item => Number.isFinite(item.line) && item.line > 0 && previewSourceAnchorIsRendered(item.element));
  if (explicit.length) return explicit;
  const panel = previewPane?.closest?.('.file-editor-panel');
  const sourceLines = panel?._pmSourceLines || [];
  return Array.from(panel?._pmView?.dom?.children || [])
    .map((element, index) => ({element, line: Number(sourceLines[index])}))
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
  writeOwnedElementScroll(previewPane, {top: Math.max(0, anchor.element.offsetTop - 4)}, 'preview-find-reveal');
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

function clampScrollTop(element, value) {
  const max = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  return Math.min(max, Math.max(0, Number(value || 0)));
}

const fileEditorSplitScrollFocusRatio = 1 / 4;
const fileEditorHtmlSplitScrollFocusRatio = 1 / 2;
const fileEditorSplitScrollAnchorSnapPx = 1;

function fileEditorSplitScrollRatio(previewPane, scroller) {
  return scroller === previewPane ? fileEditorSplitScrollFocusRatio : fileEditorHtmlSplitScrollFocusRatio;
}

function editorScrollEdgeTarget(from, to) {
  const maxFrom = Math.max(0, Number(from?.scrollHeight || 0) - Number(from?.clientHeight || 0));
  const maxTo = Math.max(0, Number(to?.scrollHeight || 0) - Number(to?.clientHeight || 0));
  const current = Math.max(0, Number(from?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(from?.clientHeight || 0) * 0.01));
  if (maxTo <= 0 || current <= edgeSnap) return 0;
  if (maxFrom <= edgeSnap || current >= maxFrom - edgeSnap) return maxTo;
  return null;
}

function previewScrollAnchors(previewPane, scroller = previewPane) {
  if (scroller !== previewPane) {
    const panel = previewPane.closest('.file-editor-panel');
    const frame = htmlSplitPreviewFrame(panel);
    const map = frame && htmlSplitSourceMap(panel, frame);
    const anchors = [];
    for (const {element, from} of map?.records || []) {
      if (!previewSourceAnchorIsRendered(element)) continue;
      const line = panel._cmView.state.doc.lineAt(map.toCurrent(from)).number;
      const top = scrollTopForPreviewElement(scroller, element);
      const previous = anchors.at(-1);
      if (!previous || (line > previous.line && top > previous.top)) anchors.push({line, top});
    }
    return anchors;
  }
  return previewSourceLineAnchors(previewPane)
    .map(item => ({...item, top: scrollTopForPreviewElement(previewPane, item.element)}))
    .sort((a, b) => a.line - b.line || a.top - b.top);
}

function sourceSpanIsIgnoredComment(previewPane, fromLine, toLine) {
  const panel = previewPane?.closest?.('.file-editor-panel');
  return (panel?._pmIgnoredCommentRanges || []).some(range => (
    Number(range?.from) <= Number(fromLine) + 1 && Number(range?.to) >= Number(toLine) - 1
  ));
}

function semanticSourceLineAnchors(previewPane, scroller) {
  const anchors = previewScrollAnchors(previewPane, scroller);
  const panel = previewPane?.closest?.('.file-editor-panel');
  const comments = panel?._pmIgnoredCommentRanges || [];
  return anchors.filter(anchor => !comments.some(range => Number(anchor.line) >= Number(range.from) && Number(anchor.line) < Number(range.to)));
}

function codeMirrorSourceTextBlock(block) {
  return Array.isArray(block?.type) ? block.type.find(part => part.type === 0) || block : block;
}

function sourcePositionForEditorScroll(cmView, ratio = fileEditorSplitScrollFocusRatio) {
  if (!cmView?.scrollDOM || !cmView.state?.doc) return null;
  try {
    const y = Math.max(0, Number(cmView.scrollDOM.scrollTop || 0)) + Math.max(1, Number(cmView.scrollDOM.clientHeight || 0)) * ratio;
    const measured = cmView.lineBlockAtHeight(y);
    const sourceLine = cmView.state.doc.lineAt(measured.from);
    const block = codeMirrorSourceTextBlock(cmView.lineBlockAt(sourceLine.from));
    const line = sourceLine.number;
    const height = Math.max(1, Number(block.height || 0));
    const fraction = Math.min(1, Math.max(0, (y - Number(block.top || 0)) / height));
    return {line: line + fraction};
  } catch (_) {
    return null;
  }
}

function sourcePositionForPreviewScroll(previewPane, scroller = previewPane) {
  const anchors = semanticSourceLineAnchors(previewPane, scroller);
  if (!anchors.length) return null;
  const y = Math.max(0, Number(scroller?.scrollTop || 0)) + Math.max(1, Number(scroller?.clientHeight || 0)) * fileEditorSplitScrollRatio(previewPane, scroller);
  let previous = anchors[0];
  for (const next of anchors.slice(1)) {
    if (next.top > y) {
      if (next.top - y <= fileEditorSplitScrollAnchorSnapPx) return {line: next.line};
      if (sourceSpanIsIgnoredComment(previewPane, previous.line, next.line)) return {line: previous.line};
      const span = Math.max(1, next.top - previous.top);
      const fraction = Math.min(1, Math.max(0, (y - previous.top) / span));
      return {line: previous.line + ((next.line - previous.line) * fraction)};
    }
    previous = next;
  }
  return {line: previous.line};
}

function previewScrollTopForSourcePosition(previewPane, position, scroller = previewPane) {
  const anchors = semanticSourceLineAnchors(previewPane, scroller);
  if (!anchors.length || !position) return null;
  const targetOffset = Math.max(1, Number(scroller?.clientHeight || 0)) * fileEditorSplitScrollRatio(previewPane, scroller);
  const line = Math.max(1, Number(position.line || 1));
  if (line <= anchors[0].line) return clampScrollTop(scroller, anchors[0].top - targetOffset);
  let previous = anchors[0];
  for (let index = 1; index < anchors.length; index += 1) {
    const next = anchors[index];
    if (line <= next.line) {
      // Source-only spans such as HTML comments have no rendered height. Keep the rendered pane
      // pinned to the preceding semantic block until the editor reaches the next rendered block.
      if (sourceSpanIsIgnoredComment(previewPane, previous.line, next.line)) {
        const anchor = line < next.line ? previous : next;
        return clampScrollTop(scroller, anchor.top - targetOffset);
      }
      const span = Math.max(1, next.line - previous.line);
      const fraction = Math.min(1, Math.max(0, (line - previous.line) / span));
      return clampScrollTop(scroller, previous.top + ((next.top - previous.top) * fraction) - targetOffset);
    }
    previous = next;
  }
  return null;
}

function editorScrollTopForSourcePosition(cmView, position, ratio = fileEditorSplitScrollFocusRatio) {
  if (!cmView?.state?.doc || !position) return null;
  try {
    const targetOffset = Math.max(1, Number(cmView.scrollDOM?.clientHeight || 0)) * ratio;
    const line = Math.max(1, Math.min(Number(position.line || 1), cmView.state.doc.lines));
    const beforeLine = Math.max(1, Math.min(Math.floor(line), cmView.state.doc.lines));
    const afterLine = Math.max(1, Math.min(Math.ceil(line), cmView.state.doc.lines));
    const beforeBlock = codeMirrorSourceTextBlock(cmView.lineBlockAt(cmView.state.doc.line(beforeLine).from));
    const beforeTop = Number(beforeBlock?.top || 0);
    if (afterLine === beforeLine) {
      // CodeMirror may classify the exact top boundary as the preceding visual block. Keep an integer
      // source position just inside its target block so preview -> editor sync cannot land one line early.
      const interiorInset = Math.min(1, Math.max(0, Number(beforeBlock?.height || 0)) / 2);
      return clampScrollTop(cmView.scrollDOM, beforeTop + interiorInset - targetOffset);
    }
    const afterBlock = codeMirrorSourceTextBlock(cmView.lineBlockAt(cmView.state.doc.line(afterLine).from));
    const afterTop = Number(afterBlock?.top || beforeTop);
    return clampScrollTop(cmView.scrollDOM, beforeTop + ((afterTop - beforeTop) * (line - beforeLine)) - targetOffset);
  } catch (_) {
    return null;
  }
}

function previewPaneNeedsSourceAnchorScroll(previewPane) {
  return previewSourceLineAnchors(previewPane).length > 0;
}

function writeFileEditorSplitScrollIfOwned(deferredOwner, element, coordinates) {
  return deferredOwner
    ? writeDeferredElementScrollIfOwned(deferredOwner, element, coordinates, 'preview-split-reflection')
    : writeOwnedElementScroll(element, coordinates, 'preview-split-reflection');
}

function syncFileEditorSplitScrollBySourceAnchors(host, source, editorScroller, previewPane, deferredOwner = null) {
  const previewScroller = fileEditorSourceElement(host, 'preview');
  if (previewScroller === previewPane && !previewPaneNeedsSourceAnchorScroll(previewPane)) return false;
  const from = source === 'preview' ? previewScroller : editorScroller;
  const to = source === 'preview' ? editorScroller : previewScroller;
  const ratio = fileEditorSplitScrollRatio(previewPane, previewScroller);
  const target = editorScrollEdgeTarget(from, to) ?? (source === 'preview'
    ? editorScrollTopForSourcePosition(host._cmView, sourcePositionForPreviewScroll(previewPane, previewScroller), ratio)
    : previewScrollTopForSourcePosition(previewPane, sourcePositionForEditorScroll(host._cmView, ratio), previewScroller));
  if (target === null) return false;
  return writeFileEditorSplitScrollIfOwned(deferredOwner, to, {top: target, left: scrollSyncTargetPosition(from, to, 'left')});
}

function nowMs() {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

function fileEditorScrollCoordinates(panel, source) {
  const element = fileEditorSourceElement(panel, source);
  if (!element) return null;
  return {
    top: Number(element.scrollTop || 0),
    left: Number(element.scrollLeft || 0),
    userGeneration: previewScrollUserOwnershipGeneration(element),
  };
}

function rememberFileEditorReflectedScroll(panel, targetSource, ownerSource, generation) {
  const coordinates = fileEditorScrollCoordinates(panel, targetSource);
  if (!panel || !coordinates) return false;
  if (!panel._splitScrollReflections) panel._splitScrollReflections = Object.create(null);
  panel._splitScrollReflections[targetSource] = {
    generation,
    ownerSource,
    ...coordinates,
  };
  return true;
}

function fileEditorReflectedScrollMatches(panel, source) {
  // A programmatic target event can arrive after the time guard expires. Keep it attached to
  // the source generation only while the target still has the exact coordinates we wrote.
  const record = panel?._splitScrollReflections?.[source];
  if (!record || record.generation !== Number(panel?._splitScrollGeneration || 0)) return false;
  const coordinates = fileEditorScrollCoordinates(panel, source);
  if (coordinates && coordinates.top === record.top && coordinates.left === record.left
      && coordinates.userGeneration === record.userGeneration) return true;
  delete panel._splitScrollReflections[source];
  return false;
}

function promoteFileEditorReflectedScrollOwner(panel, ownerSource, generation) {
  for (const [targetSource, record] of Object.entries(panel?._splitScrollReflections || {})) {
    if (record?.ownerSource !== ownerSource) continue;
    if (fileEditorReflectedScrollMatches(panel, targetSource)) record.generation = generation;
    else delete panel._splitScrollReflections[targetSource];
  }
}

function fileEditorScrollAtBottom(element) {
  const maxTop = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  const current = Math.max(0, Number(element?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(element?.clientHeight || 0) * 0.01));
  return maxTop > 0 && current >= maxTop - edgeSnap;
}

function scheduleFileEditorCodeMirrorBottomConvergence(panel, ownerSource, generation) {
  const cmView = panel?._cmView;
  if (!cmView?.scrollDOM || typeof cmView.requestMeasure !== 'function') return false;
  const state = {ownerSource, generation, lastTop: null, lastMax: null, stableSnapshots: 0};
  panel._splitScrollBottomConvergence = state;
  // CodeMirror can discover more document height after the first scroll assignment. Its measure
  // queue is the authoritative geometry boundary, so require two identical measured bottoms.
  const request = () => cmView.requestMeasure({
    read(view) {
      const scroller = view.scrollDOM;
      return {
        top: Number(scroller?.scrollTop || 0),
        max: Math.max(0, Number(scroller?.scrollHeight || 0) - Number(scroller?.clientHeight || 0)),
      };
    },
    write(measure, view) {
      if (panel._splitScrollBottomConvergence !== state || Number(panel._splitScrollGeneration || 0) !== generation) return;
      const scroller = view.scrollDOM;
      writeOwnedElementScroll(scroller, {top: measure.max}, 'preview-split-bottom-convergence');
      const top = Number(scroller.scrollTop || 0);
      const max = Math.max(0, Number(scroller.scrollHeight || 0) - Number(scroller.clientHeight || 0));
      rememberFileEditorReflectedScroll(panel, 'editor', ownerSource, generation);
      state.stableSnapshots = state.lastTop === top && state.lastMax === max
        ? state.stableSnapshots + 1
        : 1;
      state.lastTop = top;
      state.lastMax = max;
      if (state.stableSnapshots >= 2) {
        panel._splitScrollBottomConvergence = null;
        return;
      }
      request();
    },
  });
  request();
  return true;
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

function revealSplitEditorSourceSelection(panel, from, to) {
  const view = panel._cmView;
  if (!view || fileEditorPanelMode(panel) !== 'split') return;
  setFileEditorScrollSyncGuardForSource('preview', panel);
  const effect = panel._cmApi.EditorView.scrollIntoView(from);
  view.dispatch({selection: {anchor: from, head: to}, effects: effect});
}

// Scrolls the rendered side of a split so its caret, now `top` pixels below the top of its
// viewport, sits at `targetTop`, level with the TextEdit caret.
function revealSplitCaret(panel, scroller, top, targetTop) {
  if (!scroller) return false;
  const generation = Number(panel._splitScrollGeneration || 0);
  // A caret move can also scroll TextEdit. This exact source viewport already has a target.
  rememberFileEditorReflectedScroll(panel, 'editor', 'caret', generation);
  const target = clampScrollTop(scroller, scroller.scrollTop + top - targetTop);
  if (Math.abs(scroller.scrollTop - target) < 1) return false;
  setFileEditorScrollSyncGuardForSource('editor', panel);
  writeOwnedElementScroll(scroller, {top: target}, 'split-caret');
  rememberFileEditorReflectedScroll(panel, 'preview', 'editor', generation);
  return true;
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

function fileEditorPanelState(panel) {
  const path = fileEditorPanelPath(panel);
  const item = fileEditorPanelItem(panel) || fileEditorItemFor(path);
  return fileEditorStateForItem(path, item);
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
  if (source !== 'preview') return fileEditorPanelScroller(panel);
  return previewEmbeddedScrollElement(htmlSplitPreviewFrame(panel)) || fileEditorPanelPreviewPane(panel);
}

function fileEditorSourceCanDrive(panel, source) {
  return elementCanScroll(fileEditorSourceElement(panel, source));
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
    const state = fileEditorPanelState(panel);
    const panelContent = state?.kind === 'text' ? state.content : content;
    if (panel?._pmView) syncProseMirrorPanelSource(panel, path, state);
    else renderFileEditorPreviewSurface(panel, panel.querySelector('.file-editor-preview-pane-panel'), path, panelContent, {context: mode});
  }
}

function syncFileEditorInPaneSplitScroll(host, source, generation, deferredOwner = null) {
  const content = host.querySelector?.('.file-editor-content');
  if (!content?.classList?.contains('split-preview')) return false;
  const cmView = host._cmView || null;
  const editorScroller = cmView?.scrollDOM || null;
  const previewPane = host.querySelector?.('.file-editor-preview-pane-panel');
  if (!editorScroller || !previewPane || previewPane.hidden) return false;
  // The TeX PDF scrolls inside PDF.js and has its own source map.
  if (previewPane.querySelector?.('[data-tex-preview]')) return false;
  if (!fileEditorSourceCanDrive(host, source)) return false;
  const previewScroller = fileEditorSourceElement(host, 'preview');
  const from = source === 'preview' ? previewScroller : editorScroller;
  const to = source === 'preview' ? editorScroller : previewScroller;
  const previewDrivesBottom = source === 'preview' && fileEditorScrollAtBottom(previewScroller);
  setFileEditorScrollSyncGuardForSource(source, host);
  const synced = syncFileEditorSplitScrollBySourceAnchors(host, source, editorScroller, previewPane, deferredOwner)
    || syncScrollPositionByRatio(from, to, 'preview-split-reflection', {}, deferredOwner);
  if (!synced) return false;
  rememberFileEditorReflectedScroll(host, source === 'preview' ? 'editor' : 'preview', source, generation);
  if (previewDrivesBottom) scheduleFileEditorCodeMirrorBottomConvergence(host, source, generation);
  return true;
}

function syncFileEditorSplitScroll(host, source, generation = Number(host?._splitScrollGeneration || 0), deferredOwner = null, observedPreviewScroll = false) {
  if (!host) return;
  // Native input in either HTML pane can supersede a previous reflection.
  if (fileEditorScrollSyncBlocked(host, source) && !(htmlSplitPreviewFrame(host)
      && (observedPreviewScroll || previewScrollUserOwnsElementNow(fileEditorSourceElement(host, source))))) return;
  const canDrive = fileEditorSourceCanDrive(host, source);
  if (!canDrive) return;
  syncFileEditorInPaneSplitScroll(host, source, generation, deferredOwner);
  syncFilePreviewPopoutsFromPanel(host, source);
}

function scheduleFileEditorSplitScrollSync(host, source, options = {}) {
  if (!host) return false;
  if (options.forceSource !== true && fileEditorReflectedScrollMatches(host, source)) return true;
  const generation = Number(host._splitScrollGeneration || 0) + 1;
  promoteFileEditorReflectedScrollOwner(host, source, generation);
  host._splitScrollGeneration = generation;
  host._splitScrollBottomConvergence = null;
  host._splitScrollPendingGeneration = generation;
  host._splitScrollPendingSource = source;
  host._splitScrollPendingObservedPreview = options.observedPreviewScroll === true;
  host._splitScrollDeferredOwner = createPassiveDeferredElementScrollOwner(
    fileEditorPanelScroller(host),
    fileEditorSourceElement(host, 'preview'),
  );
  if (host._splitScrollFrame) return true;
  const run = () => {
    host._splitScrollFrame = 0;
    const pendingSource = host._splitScrollPendingSource || source;
    const pendingGeneration = Number(host._splitScrollPendingGeneration || generation);
    const pendingObservedPreview = host._splitScrollPendingObservedPreview;
    const deferredOwner = host._splitScrollDeferredOwner;
    host._splitScrollPendingSource = '';
    host._splitScrollPendingGeneration = 0;
    host._splitScrollPendingObservedPreview = false;
    host._splitScrollDeferredOwner = null;
    if (pendingGeneration !== Number(host._splitScrollGeneration || 0)) return;
    if (pendingSource === 'editor' && host._splitScrollReflections?.editor?.ownerSource === 'caret' &&
        fileEditorReflectedScrollMatches(host, 'editor')) return;
    syncFileEditorSplitScroll(host, pendingSource, pendingGeneration, deferredOwner, pendingObservedPreview);
  };
  if (typeof requestAnimationFrame === 'function') host._splitScrollFrame = requestAnimationFrame(run);
  else host._splitScrollFrame = setTimeout(run, 0);
  return true;
}

const fileEditorPreviewLayoutScrollSyncMs = 400;

function fileEditorPreviewScrollSyncSource(panel) {
  const previewPane = fileEditorSourceElement(panel, 'preview');
  // Current native input outranks layout refits. Historical invalidation generations must not
  // turn a later details-collapse clamp into a new Preview gesture.
  if (previewScrollUserOwnsElementNow(previewPane)) return 'preview';
  const layoutSyncActive = Number(panel?._previewLayoutScrollUntil || 0) > nowMs();
  return layoutSyncActive && fileEditorPanelMode(panel) === 'split' && fileEditorSourceCanDrive(panel, 'editor')
    ? 'editor'
    : 'preview';
}

function scheduleFileEditorPreviewLayoutSync(panel) {
  if (!panel) return false;
  panel._previewLayoutScrollUntil = nowMs() + fileEditorPreviewLayoutScrollSyncMs;
  const scheduled = scheduleFileEditorSplitScrollSync(panel, 'editor', {forceSource: true});
  // A collapsed layout can emit its clamp scroll before the editor refit frame runs.
  // Keep that exact position with the pending editor owner; later input supersedes it.
  rememberFileEditorReflectedScroll(panel, 'preview', 'editor', Number(panel._splitScrollGeneration || 0));
  return scheduled;
}

function refreshEditorPreviews() {
  for (const [item, panel] of panelNodes.entries()) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    const state = fileEditorStateForItem(path, item);
    if (state?.kind && editorPreviewModeAvailable(path, state)) {
      renderFileEditorPreviewSurface(panel, panel.querySelector('.file-editor-preview-pane-panel'), path, state.content || '', {context: fileEditorPanelMode(panel)});
      if (state.historical !== true) updateFilePreviewPopout(path, state.content || '');
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
    const panelState = fileEditorPanelState(openPanel);
    if (panelState?.historical === true) continue;
    if (openPanel?._cmView) syncCodeMirrorDocument(openPanel._cmView, content, {path});
    const rawCode = openPanel?.querySelector?.('.file-editor-raw-panel code');
    if (rawCode) rawCode.textContent = content;
    const mode = fileEditorPanelMode(openPanel);
    if (mode === 'preview' || mode === 'split') {
      if (openPanel?._pmView) syncProseMirrorPanelSource(openPanel, path, panelState);
      else renderFileEditorPreviewSurface(openPanel, openPanel.querySelector('.file-editor-preview-pane-panel'), path, content, {context: mode});
    }
    const status = openFileStatus(panelState);
    setFileEditorPanelStatus(openPanel, status.message, status.level);
  }
  renderLinkedFilePreviewPanels(null, path, content);
  updateFilePreviewPopout(path, content);
}

function applyFileEditorSaveHygiene(path) {
  const state = fileState.get(path);
  if (!state || state.kind !== 'text') return false;
  const nextContent = normalizeFileEditorSaveContent(state.content);
  if (nextContent === state.content) return false;
  state.content = nextContent;
  updateOpenFileDirtyFlag(path);
  syncFileEditorNormalizedContentToPanels(path, state.content);
  return true;
}

function fileEditorSaveRequest(path, panel, options, resolve = null) {
  return {
    path,
    panel,
    state: fileEditorStateForItem(options.statePath || path, fileEditorPanelItem(panel) || options.item || fileEditorItemFor(path)),
    options: {
      ...options,
      autosave: options.autosave === true,
      closing: options.closing === true,
      force: options.force === true,
    },
    resolvers: resolve ? [resolve] : [],
  };
}

function mergeFileEditorSaveRequest(request, panel, options, resolve) {
  if (panel) request.panel = panel;
  if (options.item) request.options.item = options.item;
  request.options.force = request.options.force || options.force === true;
  request.options.closing = request.options.closing || options.closing === true;
  request.options.autosave = request.options.autosave && options.autosave === true;
  request.resolvers.push(resolve);
}

function resolveFileEditorSaveRequest(request, result) {
  for (const resolve of request?.resolvers || []) resolve(result, request.completedState || request.state);
}

function queueFileEditorSave(path, owner, panel, options) {
  return new Promise(resolve => {
    const complete = options.returnState === true ? (saved, state) => resolve({saved, state}) : resolve;
    const request = fileEditorSaveRequest(path, panel, options, complete);
    if (owner.active || owner.conflict) {
      if (owner.pending?.state === request.state) mergeFileEditorSaveRequest(owner.pending, panel, options, complete);
      else {
        resolveFileEditorSaveRequest(owner.pending, false);
        owner.pending = request;
      }
      return;
    }
    owner.active = request;
    runFileEditorSaveOwner(path, owner);
  });
}

async function performFileEditorSave(path, panel, options = {}, expectedState = null) {
  if (readOnlyMode) return false;
  const statePath = options.statePath || path;
  const item = fileEditorPanelItem(panel) || options.item || fileEditorItemFor(path);
  const state = fileEditorStateForItem(statePath, item);
  if (state?._shareActive) {
    const outcomes=await Promise.all(fileEditorPanelsForPath(statePath).filter(owner=>owner._shareConnection).map(owner=>owner._shareConnection.flush()));
    shareHostFileState(statePath);
    return outcomes.every(Boolean) && !state._sharePaused && !state.dirty;
  }
  if (!fileEditorStateWritable(state) || state.kind !== 'text' || (expectedState && state !== expectedState)) return false;
  const contentPanel = panel || state.contentOwnerPanel || null;
  flushProseMirrorSource(contentPanel, statePath, {preserveTerminalNewline: true});
  syncOpenFileContentFromPanels(statePath, contentPanel);
  if (!options.force && ((state.externalChanged && !fileEditorSelfWritePending(statePath, state)) || state.externalMissing)) {
    if (!state.dirty) return reloadOpenFileFromDisk(path, {force: true});
    clearFileAutosaveTimer(path);
    return {conflict: true, mergeEligible: !state.externalMissing, message: ''};
  }
  applyFileEditorSaveHygiene(path);
  if (!state.dirty && options.force !== true) return true;
  const savedContent = state.content;
  const relocationRevision = Number(state.relocationRevision || 0);
  setFileEditorPanelStatus(contentPanel, t(options.autosave ? 'editor.autoSaving' : 'editor.saving'), '');
  let selfWrite = null;
  const clearSelfWrite = () => {
    for (const ackPath of [path, statePath, options.statePath || statePath]) {
      if (selfWrite && fileEditorSelfWriteAcks.get(ackPath) === selfWrite) fileEditorSelfWriteAcks.delete(ackPath);
    }
  };
  try {
    const body = {
      path,
      content: savedContent,
    };
    if (state.fileId) body.expected_file_id = state.fileId;
    if (options.force !== true) {
      if (state.diskMtimeExact && state.fileId) body.expected_mtime_ns_exact = state.diskMtimeExact;
      else body.expected_mtime = state.mtime;
    }
    // write_file truncates in place before writing the replacement bytes. Register the expected
    // self-write before admission so watchd's zero-byte observation cannot be mistaken for an
    // external edit while the filesystem operation is still queued/in flight.
    selfWrite = {
      state,
      mtime: state.mtime,
      size: savedContent.length,
      pending: true,
      expiresAt: Date.now() + 10000,
    };
    fileEditorSelfWriteAcks.set(path, selfWrite);
    const payload = await apiFetchJson('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const currentStatePath = options.statePath || statePath;
    if (!fileStateIsCurrent(currentStatePath, state)) {
      clearSelfWrite();
      // A completed local save may outlive its tab. Refresh a clean reopened owner even
      // while focused; ordinary watcher reloads defer there to protect active editing.
      const reopened = fileState.get(currentStatePath);
      if (reopened && !reopened.dirty) await replaceOpenFileStateFromDisk(currentStatePath, payload);
      return true;
    }
    if (Number(state.relocationRevision || 0) === relocationRevision) applyFileIdentityMetadata(state, payload);
    state.mtime = filePayloadMtime(payload);
    state.size = payload.size;
    clearSelfWrite();
    fileEditorSelfWriteAcks.set(currentStatePath, {
      state,
      mtime: state.mtime,
      size: state.size,
      fileId: state.fileId,
      expiresAt: Date.now() + 5000,
    });
    state.original = savedContent;
    state.lastSaveStatus = {content: savedContent, key: options.autosave ? 'editor.autoSaved' : 'editor.saved',
      params: {size: formatFileSize(payload.size)}};
    state.diskMtimeExact = payload.mtime_ns_exact;
    state.dirty = state.content !== savedContent;
    if (state.dirty) delete state.lastCleanAt;
    else state.lastCleanAt = Date.now();
    clearFileAutosaveTimer(currentStatePath);
    clearOpenFileExternalState(state);
    if (payload.yolo_rules) {
      yoloRulesPayload = payload.yolo_rules;
      renderPreferencesPanels();
    }
    for (const openPanel of fileEditorPanelsForPath(currentStatePath)) {
      updateFileEditorPanelChrome(openPanel, currentStatePath);
      const status = openFileStatus(state);
      setFileEditorPanelStatus(openPanel, status.message, status.level);
    }
    renderSessionButtons();
    renderPaneTabStrips();
    return true;
  } catch (err) {
    clearSelfWrite();
    if (!fileStateIsCurrent(options.statePath || statePath, state)) return false;
    // A failed write admitted for the old pathname cannot decide conflicts for the relocated document.
    if (Number(state.relocationRevision || 0) !== relocationRevision) return false;
    if (err?.status === 409) {
      setFileEditorPanelStatus(panel, t('dialog.conflictTitle'), 'warn');
      return {conflict: true, mergeEligible: err.code === 'changed_on_disk', message: userMessageText(err, err.message)};
    }
    setFileEditorPanelStatus(panel, t('editor.saveFailed', {error: err}), 'error');
    return false;
  }
}

async function runFileEditorSaveOwner(path, owner) {
  while (owner.active) {
    const request = owner.active;
    const requestPath = request.path || owner.path || path;
    const result = await performFileEditorSave(requestPath, request.panel, request.options, request.state);
    if (result?.conflict === true) {
      owner.active = null;
      owner.conflict = true;
      const conflictPath = owner.path || requestPath;
      if (!fileStateIsCurrent(conflictPath, request.state)) {
        owner.conflict = false;
        resolveFileEditorSaveRequest(request, false);
        owner.active = owner.pending;
        owner.pending = null;
        continue;
      }
      const state = request.state;
      if (!state.externalMissing) state.externalChanged ||= {mtime: 0, size: null};
      const observation = state.externalChanged;
      const merge = !request.options.force && result.mergeEligible
        ? await reconcileOpenFileEdits(conflictPath, state) : null;
      if (merge?.merged || (merge?.disk && await pickOpenFileMerge(conflictPath, state, merge))) {
        owner.conflict = false;
        // A busy writer can invalidate the new disk version again. Keep the merged draft in that
        // case so one Save cannot spin indefinitely while another process is editing.
        if (!request.mergeRetried && fileStateIsCurrent(conflictPath, state)) {
          request.mergeRetried = true;
          request.path = conflictPath;
          owner.active = request;
          continue;
        }
        if (fileStateIsCurrent(conflictPath, state)) setFileEditorPanelStatus(request.panel, t('dialog.staleStatus'), 'warn');
      }
      const conflict = !merge && state.externalChanged === observation && fileStateIsCurrent(conflictPath, state)
        ? await showFileSaveConflictDialog(conflictPath, request.panel, {
          message: result.message,
          returnAction: true,
          deferSave: true,
        }) : null;
      owner.conflict = false;
      if (!fileStateIsCurrent(owner.path || requestPath, conflict?.state || request.state)) {
        resolveFileEditorSaveRequest(request, false);
        owner.active = owner.pending;
        owner.pending = null;
        continue;
      }
      if (conflict?.result === true && (conflict.action === 'reload' || conflict.nestedAction === 'reload')) request.completedState = conflict.state;
      const pending = owner.pending;
      owner.pending = null;
      const overwriteSelected = (
        conflict?.action === 'overwrite' || conflict?.nestedAction === 'overwrite'
      ) && conflict?.result === true;
      if (overwriteSelected) {
        request.options.force = true;
        request.path = owner.path || requestPath;
        if (pending) {
          request.panel = pending.panel || request.panel;
          if (pending.options.item) request.options.item = pending.options.item;
          request.options.closing = request.options.closing || pending.options.closing;
          request.resolvers.push(...pending.resolvers);
        }
        const contentOwner = fileState.get(conflictPath)?.contentOwnerPanel;
        if (contentOwner?.isConnected !== false && fileEditorPanelState(contentOwner) === fileState.get(conflictPath)) {
          request.panel = contentOwner;
        }
        owner.active = request;
        continue;
      }
      resolveFileEditorSaveRequest(request, conflict?.result === true);
      resolveFileEditorSaveRequest(pending, false);
      if (fileEditorSaveOwners.get(owner.path || path) === owner) fileEditorSaveOwners.delete(owner.path || path);
      return;
    }
    resolveFileEditorSaveRequest(request, result);
    owner.active = owner.pending;
    owner.pending = null;
    const currentPath = owner.path || requestPath;
    const state = fileState.get(currentPath);
    if (!owner.active && result === true && fileStateIsCurrent(currentPath, request.state) && openFileAutosaveReady(currentPath, state)) {
      clearFileAutosaveTimer(currentPath);
      const contentOwner = state.contentOwnerPanel;
      const trailingPanel = contentOwner?.isConnected !== false && fileEditorPanelState(contentOwner) === state
        ? contentOwner
        : request.panel;
      owner.active = fileEditorSaveRequest(currentPath, trailingPanel, {autosave: true});
    }
  }
  if (fileEditorSaveOwners.get(owner.path || path) === owner) fileEditorSaveOwners.delete(owner.path || path);
}

function saveFileEditor(path, panel, options = {}) {
  if (fileTreeMovePendingForPath(path)) return Promise.resolve(false);
  if (readOnlyMode) return Promise.resolve(false);
  const item = fileEditorPanelItem(panel) || options.item || fileEditorItemFor(path);
  const state = fileEditorStateForItem(path, item);
  if (!fileEditorStateWritable(state) || state.kind !== 'text') return Promise.resolve(false);
  let owner = fileEditorSaveOwners.get(path);
  if (!owner) {
    owner = {active: null, pending: null, path};
    fileEditorSaveOwners.set(path, owner);
  }
  return queueFileEditorSave(path, owner, panel, options);
}

function codeMirrorRecentReviewExtension(api, panel) {
  panel._cmReviewCompartment = new api.Compartment();
  return panel._cmReviewCompartment.of([]);
}

function syncCodeMirrorRecentReview(panel, state) {
  const view = panel._cmView;
  const api = panel._cmApi;
  if (!view || !api || panel._cmMode !== 'edit' || !panel._cmReviewCompartment) return;
  const review = state?.recentReview;
  const original = review?.id && typeof review.original === 'string' ? review.original : null;
  panel.querySelector('.file-editor-codemirror-panel')?.classList.toggle('file-editor-diff-codemirror', original !== null);
  if (panel._cmReviewOriginal === original) return;
  const top = view.scrollDOM.scrollTop;
  const left = view.scrollDOM.scrollLeft;
  if (original === null || panel._cmReviewOriginal == null) {
    view.dispatch({effects: panel._cmReviewCompartment.reconfigure(original === null ? [] : api.unifiedMergeView(codeMirrorUnifiedMergeOptions(original, {mergeControls: false})))});
  } else {
    const old = api.getOriginalDoc(view.state);
    view.dispatch({effects: api.originalDocChangeEffect(view.state, api.EditorState.create({doc: old}).changes({from: 0, to: old.length, insert: original}))});
  }
  panel._cmReviewOriginal = original;
  view.scrollDOM.scrollTop = top;
  view.scrollDOM.scrollLeft = left;
}

function recentReviewStorageKey(path) {
  return `yomux.recentReview.${browserClientId}.${path}`;
}

function saveRecentFileReview(path, review) {
  if (review?.id) sessionStorage.setItem(recentReviewStorageKey(path), JSON.stringify({id: review.id, tracking: review.tracking, minutes: review.minutes || 0}));
  else sessionStorage.removeItem(recentReviewStorageKey(path));
}

function syncRecentReviewControls(panel, path, state, mode) {
  const button = panel.querySelector('.file-editor-review-panel');
  if (!button) return;
  const allowed = clientCanUseUnscopedHostRequests() && !readOnlyMode && state.kind === 'text' && state.historical !== true;
  if (allowed && !state.recentReview) {
    const stored = sessionStorage.getItem(recentReviewStorageKey(path));
    if (stored) {
      try {
        const saved = JSON.parse(stored);
        if (typeof saved.id === 'string' && typeof saved.tracking === 'string') {
          state.recentReview = saved;
          state.trackingId ||= saved.tracking;
          void refreshRecentFileReview(path);
        }
      } catch (error) {
        sessionStorage.removeItem(recentReviewStorageKey(path));
        setFileEditorPanelStatus(panel, `Recent changes could not reconnect: ${error.message}`, 'warn');
      }
    }
  }
  const review = state.recentReview;
  button.hidden = !allowed || mode === 'preview' || mode === 'diff';
  button.setAttribute('aria-pressed', String(Boolean(review?.id)));
  button.disabled = Boolean(review?.busy);
  const toolbar = panel.querySelector('.file-editor-review-toolbar');
  if (toolbar) {
    toolbar.hidden = button.hidden || !(review?.id || review?.error);
    for (const control of toolbar.querySelectorAll('button, select')) {
      control.hidden = !review?.id;
      control.disabled = Boolean(review?.busy);
    }
  }
  const select = panel.querySelector('.file-editor-review-range');
  if (select) {
    select.value = String(review?.minutes || 0);
    select.onchange = () => {
      const current = fileState.get(path)?.recentReview;
      if (!current) return;
      current.minutes = Number(select.value);
      saveRecentFileReview(path, current);
      void refreshRecentFileReview(path);
    };
  }
  const status = panel.querySelector('.file-editor-review-status');
  if (status) {
    const detail = review?.error || review?.problem;
    const incomplete = review?.complete === false;
    status.textContent = detail || (incomplete ? 'Partial history' : 'Watching saves');
    status.title = detail || (incomplete
      ? `History available since ${new Date(review.earliest_at).toLocaleTimeString()}`
      : 'Compares saved file versions observed while this file is open.');
  }
}

let recentFileReviewTimer = null;
function scheduleRecentFileReviews() {
  if (recentFileReviewTimer) return;
  if (!Array.from(fileState.values()).some(state => state.recentReview?.id)) return;
  recentFileReviewTimer = setTimeout(() => {
    recentFileReviewTimer = null;
    for (const [path, state] of fileState) if (state.recentReview?.id) void refreshRecentFileReview(path);
    scheduleRecentFileReviews();
  }, 5003);
}

async function requestRecentFileReview(path, review, action) {
  const state = fileState.get(path);
  const sequence = review.sequence = (review.sequence || 0) + 1;
  const tracking = state.trackingId || review.tracking;
  const minutes = review.minutes || 0;
  const id = review.id || '';
  const payload = await apiFetchJson('/api/fs/review', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
    client_id: browserClientId, tracking_id: tracking, review_id: id, action, minutes,
  })});
  const currentPath = action === 'read' ? path : Array.from(fileState.keys()).find(candidate => {
    const value = fileState.get(candidate);
    return value.recentReview === review && (value.trackingId || review.tracking) === tracking;
  });
  const current = fileState.get(currentPath);
  if (current?.recentReview !== review || review.sequence !== sequence || (current.trackingId && current.trackingId !== tracking)
      || (action === 'read' && (review.minutes || 0) !== minutes) || (review.id || '') !== id) {
    if (action === 'start' || action === 'reset') await apiFetchJson('/api/fs/review', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({client_id: browserClientId, tracking_id: tracking, review_id: payload.review_id, action: 'end'})});
    return false;
  }
  if (action !== 'end') {
    Object.assign(review, payload, {id: payload.review_id, tracking, path: currentPath, error: ''});
    saveRecentFileReview(currentPath, review);
  }
  return true;
}

async function refreshRecentFileReview(path) {
  const review = fileState.get(path)?.recentReview;
  if (!review?.id || review.busy) return;
  if (!serverWatchRootsState.registered || !serverWatchRootsState.openFileTrackingIds?.has(fileState.get(path).trackingId || review.tracking)) {
    syncServerWatchRoots({immediate: true});
    scheduleRecentFileReviews();
    return;
  }
  const reading = {minutes: review.minutes || 0, id: review.id};
  if (review.reading?.minutes === reading.minutes && review.reading.id === reading.id) return;
  review.reading = reading;
  const sequence = (review.sequence || 0) + 1;
  try {
    if (!await requestRecentFileReview(path, review, 'read')) return;
  } catch (error) {
    if (fileState.get(path)?.recentReview !== review || review.sequence !== sequence) return;
    if (Number(error.status) === 410) {
      delete review.id;
      review.original = null;
      saveRecentFileReview(path, null);
    }
    review.error = `Recent changes: ${error.message}`;
  } finally {
    if (review.reading === reading) delete review.reading;
  }
  for (const panel of fileEditorPanelsForPath(path)) {
    const state = fileState.get(path);
    syncCodeMirrorRecentReview(panel, state);
    syncRecentReviewControls(panel, path, state, editorViewModeFor(path, fileEditorPanelItem(panel)));
  }
  scheduleRecentFileReviews();
}

async function changeRecentFileReview(panel, path, action) {
  const state = fileState.get(path);
  if (!state || state.recentReview?.busy) return;
  const review = state.recentReview || {minutes: 0};
  state.recentReview = review;
  review.busy = true;
  syncRecentReviewControls(panel, path, state, editorViewModeFor(path, fileEditorPanelItem(panel)));
  try {
    await syncServerWatchRootsNow({force: true});
    const operation = action === 'toggle' ? (review.id ? 'end' : 'start') : action;
    if (!await requestRecentFileReview(path, review, operation)) return;
    if (operation === 'end') {
      delete fileState.get(review.path || path).recentReview;
      saveRecentFileReview(review.path || path, null);
    }
  } catch (error) {
    if (Number(error.status) === 410) { delete review.id; review.original = null; saveRecentFileReview(path, null); }
    review.error = `Recent changes: ${error.message}`;
    setFileEditorPanelStatus(panel, review.error, 'error');
  } finally {
    review.busy = false;
    renderOpenFilePath(review.path || path);
    scheduleRecentFileReviews();
  }
}

function navigateRecentFileReview(panel, forward) {
  if (!panel._cmView || panel._cmReviewOriginal == null) return;
  const api = panel._cmApi;
  (forward ? api.goToNextChunk : api.goToPreviousChunk)(panel._cmView);
  panel._cmView.focus();
}

function codeMirrorUnifiedMergeOptions(original, options = {}) {
  return {original, highlightChanges: false, gutter: true, ...options};
}

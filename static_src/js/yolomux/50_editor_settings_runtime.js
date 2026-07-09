function editorViewModeKey(path, item = null) {
  return item && isFileEditorItem(item) ? item : path;
}

function editorViewModeFor(path, item = null) {
  const modes = fileEditorViewModesForPath(path);
  const mode = modes.get(editorViewModeKey(path, item)) || modes.get(path);
  if (mode === 'diff') return 'diff';
  if (!editorPreviewModeAvailable(path)) return 'edit';
  const state = openFiles.get(path);
  if (state?.kind && state.kind !== 'text') return 'preview';
  if (editorViewModes.has(mode)) return mode;
  return 'edit';
}

function setFileEditorViewMode(path, mode, item = null) {
  if (!path || !editorViewModes.has(mode)) return;
  if (mode !== 'edit' && mode !== 'diff' && !editorPreviewModeAvailable(path)) mode = 'edit';
  const previousMode = editorViewModeFor(path, item);
  if ((mode === 'preview' || mode === 'split') && typeof closeFilePreviewPopout === 'function') closeFilePreviewPopout(path);
  if (mode === 'split' && previousMode !== 'split' && ['markdown', 'mermaid'].includes(previewKindForPath(path, openFiles.get(path)))) {
    resetFileEditorPreviewZoomStateForPath(path, 'split:mermaid');
  }
  fileEditorViewModesForPath(path, true).set(editorViewModeKey(path, item), mode);
  scheduleShareTopologySnapshot('editor-mode');
}

function updateEditorModeControl(control, path, state, item = null) {
  if (!control) return;
  const visible = Boolean(state?.kind) && editorPreviewModeAvailable(path, state);
  control.hidden = !visible;
  if (!visible) return;
  const mode = editorViewModeFor(path, item);
  control.querySelectorAll('[data-editor-mode]').forEach(button => {
    const nextMode = button.dataset.editorMode;
    button.hidden = state?.kind !== 'text' && nextMode !== 'preview';
    const label = editorModeLabel(nextMode);
    const active = nextMode === mode || (state?.kind !== 'text' && nextMode === 'preview');
    syncPressedButton(button, active, {labelOn: label, labelOff: label});
    setFileEditorIcon(button, editorModeIconClass(nextMode));
  });
}

function editorModeLabel(mode) {
  if (mode === 'diff') return t('common.diff');
  if (mode === 'preview') return t('common.preview');
  if (mode === 'split') return t('editor.mode.split');
  return t('common.edit');
}

function editorModeIconClass(mode) {
  if (mode === 'diff') return 'file-editor-icon-diff';
  if (mode === 'preview') return 'file-editor-icon-eye';
  if (mode === 'split') return 'file-editor-icon-split';
  return 'file-editor-icon-edit';
}

function updateEditorGutterButton(button) {
  if (!button) return;
  syncPressedButton(button, fileEditorLineNumbersEnabled, {
    labelOn: t('editor.hideLineNumbers'),
    labelOff: t('editor.showLineNumbers'),
  });
}

function setEditorContentMode(content, mode) {
  if (!content) return;
  content.classList.toggle('edit-mode', mode === 'edit');
  content.classList.toggle('preview-mode', mode === 'preview');
  content.classList.toggle('split-preview', mode === 'split');
  content.classList.toggle('diff-mode', mode === 'diff');
}

function editorWrapValue(enabled = fileEditorWrapEnabled) {
  return enabled ? 'soft' : 'off';
}

function setFileEditorIcon(button, iconClass) {
  if (!button || button.querySelector(`.${iconClass}`)) return;
  button.innerHTML = `<span class="file-editor-icon ${iconClass}" aria-hidden="true"></span>`;
}

function editorThemeLabel(mode = fileEditorThemeMode) {
  if (fileEditorPreviewDisplayMode === 'vanilla') return t('editor.previewVanilla');
  const scheme = mode === editorThemeInheritMode ? activeEditorScheme() : (EDITOR_SCHEMES[normalizeEditorSchemeId(mode)] || EDITOR_SCHEMES.dark);
  if (mode === editorThemeInheritMode) return t('editor.inheritGlobalTheme', {scheme: scheme.label});
  return t('editor.editorSchemeLabel', {scheme: scheme.label});
}

function editorPreviewThemeState() {
  if (fileEditorPreviewDisplayMode === 'vanilla') return 'vanilla';
  return activeEditorScheme().dark ? 'dark' : 'light';
}

function editorPreviewThemeStateLabel(state = editorPreviewThemeState()) {
  if (state === 'vanilla') return t('editor.previewVanilla');
  return state === 'light' ? t('editor.previewBright') : t('editor.previewDark');
}

function editorPreviewThemeShortLabel(state = editorPreviewThemeState()) {
  if (state === 'vanilla') return t('editor.previewVanillaShort');
  return state === 'light' ? t('editor.previewBrightShort') : t('common.theme.dark');
}

function editorSchemeCssVariables(scheme = activeEditorScheme()) {
  const syntax = scheme.syntax || {};
  const diff = scheme.diff || {};
  return {
    '--editor-scheme-bg': scheme.bg,
    '--editor-scheme-fg': scheme.fg,
    '--editor-scheme-muted': syntax.comment || scheme.lineNo,
    '--editor-scheme-line': scheme.line,
    '--editor-scheme-panel': scheme.panel,
    '--editor-scheme-panel2': scheme.panel2,
    '--editor-scheme-gutter-bg': scheme.gutterBg,
    '--editor-scheme-preview-bg': scheme.previewBg,
    '--editor-cursor': editorCursorColorForScheme(scheme),
    '--editor-selection': scheme.selection,
    '--editor-active-line': scheme.activeLine,
    '--editor-line-number': scheme.lineNo,
    '--markdown-heading': 'var(--active-accent)',
    '--markdown-heading-bg': 'transparent',
    '--markdown-link': syntax.link,
    '--markdown-strong': syntax.strong,
    '--markdown-emphasis': syntax.emphasis,
    '--code-keyword': syntax.keyword,
    '--code-control': syntax.control || syntax.keyword,
    '--code-atom': syntax.atom,
    '--code-string': syntax.string,
    '--code-number': syntax.number,
    '--code-variable': syntax.variable,
    '--code-function': syntax.function,
    '--code-type': syntax.type,
    '--code-property': syntax.property,
    '--code-tag': syntax.tag,
    '--code-comment': syntax.comment,
    '--code-inline': syntax.inlineCode,
    '--code-inline-bg': syntax.inlineCodeBg,
    '--code-inline-border': syntax.inlineCodeBorder,
    '--code-invalid': syntax.invalid,
    '--code-diff-add': diff.addFg,
    '--code-diff-remove': diff.removeFg,
    '--lt-bg': scheme.bg,
    '--lt-panel': scheme.panel,
    '--lt-panel2': scheme.panel2,
    '--lt-text': scheme.fg,
    '--lt-muted': syntax.comment || scheme.lineNo,
    '--lt-line': scheme.line,
    '--lt-editor-bg': scheme.bg,
    '--lt-editor-gutter-bg': scheme.gutterBg,
    '--lt-editor-preview-bg': scheme.previewBg,
    '--lt-markdown-heading': 'var(--active-accent)',
    '--lt-markdown-heading-bg': 'transparent',
    '--lt-markdown-link': syntax.link,
    '--lt-markdown-strong': syntax.strong,
    '--lt-markdown-emphasis': syntax.emphasis,
    '--lt-code-keyword': syntax.keyword,
    '--lt-code-control': syntax.control || syntax.keyword,
    '--lt-code-atom': syntax.atom,
    '--lt-code-string': syntax.string,
    '--lt-code-number': syntax.number,
    '--lt-code-variable': syntax.variable,
    '--lt-code-function': syntax.function,
    '--lt-code-type': syntax.type,
    '--lt-code-property': syntax.property,
    '--lt-code-tag': syntax.tag,
    '--lt-code-comment': syntax.comment,
    '--lt-code-inline': syntax.inlineCode,
    '--lt-code-inline-bg': syntax.inlineCodeBg,
    '--lt-code-inline-border': syntax.inlineCodeBorder,
    '--lt-code-invalid': syntax.invalid,
  };
}

function applyEditorSchemeCssVariables(scheme = activeEditorScheme()) {
  const style = document.documentElement?.style;
  if (!style) return;
  for (const [name, value] of Object.entries(editorSchemeCssVariables(scheme))) {
    if (value !== undefined && value !== null && value !== '') style.setProperty(name, String(value));
  }
}

function updateEditorThemeButton(button, options = {}) {
  if (!button) return;
  const includeVanilla = options.includeVanilla !== false;
  const scheme = activeEditorScheme();
  const previewState = editorPreviewThemeState();
  const nextState = previewState === 'dark' ? 'light' : (previewState === 'light' && includeVanilla ? 'vanilla' : 'dark');
  button.classList.toggle(themeBodyClass('dark'), previewState === 'dark');
  button.classList.toggle(themeBodyClass('light'), previewState === 'light');
  button.classList.toggle('theme-vanilla', previewState === 'vanilla');
  button.classList.toggle('theme-with-label', includeVanilla);
  button.dataset.editorTheme = previewState === 'vanilla' ? 'vanilla' : scheme.id;
  button.dataset.editorThemeShort = includeVanilla ? editorPreviewThemeShortLabel(previewState) : '';
  button.dataset.editorThemeNext = includeVanilla ? editorPreviewThemeShortLabel(nextState) : '';
  button.setAttribute('aria-pressed', previewState === 'dark' ? 'false' : 'true');
  button.title = t('editor.themeButtonTitle', {current: editorThemeLabel(), next: editorPreviewThemeStateLabel(nextState)});
  button.setAttribute('aria-label', editorThemeLabel());
  setFileEditorIcon(button, 'file-editor-icon-theme');
}

function updateImageViewerThemeButton(button) {
  updateEditorThemeButton(button);
  if (!button) return;
  button.title = t('editor.toggleImageBackgroundWithScheme', {scheme: activeEditorScheme().label});
  button.setAttribute('aria-label', t('editor.toggleImageBackground'));
}

function refreshOpenEditorThemePanels() {
  document.querySelectorAll('.file-editor-panel').forEach(panel => {
    const item = panel.dataset.layoutItem || fileEditorItemFor(panel.dataset.filePath || '');
    const path = fileItemPath(item);
    if (!path || openFiles.get(path)?.kind !== 'text') return;
    const state = openFiles.get(path);
    const reconfigured = typeof reconfigureCodeMirrorPanelTheme === 'function' && reconfigureCodeMirrorPanelTheme(panel);
    renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
    if (!reconfigured) {
      capturePaneViewState(item, panel);
      renderFileEditorPanel(panel, item);
    }
  });
  if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();
}

function applyEditorThemeMode(options = {}) {
  const scheme = activeEditorScheme();
  applyEditorSchemeCssVariables(scheme);
  document.body?.classList.remove(...EDITOR_THEME_BODY_CLASSES, EDITOR_PREVIEW_VANILLA_CLASS);
  EDITOR_SCHEME_IDS.forEach(id => document.body?.classList.remove(`editor-scheme-${id}`));
  document.body?.classList.add(editorThemeBodyClass(scheme.dark ? 'dark' : 'light'));
  document.body?.classList.add(`editor-scheme-${scheme.id}`);
  document.body?.classList.toggle(EDITOR_PREVIEW_VANILLA_CLASS, fileEditorPreviewDisplayMode === 'vanilla');
  document.querySelectorAll('.file-editor-theme-panel').forEach(updateEditorThemeButton);
  if (options.refreshEditors) refreshOpenEditorThemePanels();
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts();
}

function setFileEditorThemeMode(mode) {
  fileEditorThemeMode = normalizeEditorThemeMode(mode);
  writeStoredEditorThemeMode(fileEditorThemeMode);
  if (fileEditorPreviewDisplayMode !== 'theme') {
    fileEditorPreviewDisplayMode = 'theme';
    writeStoredEditorPreviewDisplayMode(fileEditorPreviewDisplayMode);
  }
  applyEditorThemeMode({refreshEditors: true});
  scheduleShareTopologySnapshot('editor-theme');
}

function setFileEditorPreviewDisplayMode(mode) {
  fileEditorPreviewDisplayMode = normalizeEditorPreviewDisplayMode(mode);
  writeStoredEditorPreviewDisplayMode(fileEditorPreviewDisplayMode);
  applyEditorThemeMode({refreshEditors: true});
  scheduleShareTopologySnapshot('editor-preview-display');
}

function cycleEditorThemeMode(options = {}) {
  const includeVanilla = options.includeVanilla !== false;
  const previewState = editorPreviewThemeState();
  if (previewState === 'dark') {
    setFileEditorThemeMode(configuredEditorSchemeForMode(false));
    return;
  }
  if (previewState === 'light' && includeVanilla) {
    setFileEditorPreviewDisplayMode('vanilla');
    return;
  }
  fileEditorPreviewDisplayMode = 'theme';
  writeStoredEditorPreviewDisplayMode(fileEditorPreviewDisplayMode);
  setFileEditorThemeMode(configuredEditorSchemeForMode(true));
}

function updateEditorWrapButton(button) {
  if (!button) return;
  syncPressedButton(button, fileEditorWrapEnabled, {
    labelOn: t('editor.disableWordWrap'),
    labelOff: t('editor.enableWordWrap'),
  });
  setFileEditorIcon(button, 'file-editor-icon-wrap');
}

function previewFindPanelForHost(host = null) {
  return host?.querySelector?.('.file-editor-preview-find-panel') || null;
}

function fileEditorFindOverviewForHost(host = null) {
  return host?.querySelector?.('.file-editor-find-overview') || null;
}

function clearFileEditorFindOverview(host = null) {
  const overview = fileEditorFindOverviewForHost(host);
  if (!overview) return;
  overview.replaceChildren();
  overview.hidden = true;
}

function updateFileEditorFindOverview(host = null, positions = [], activeIndex = -1) {
  const overview = fileEditorFindOverviewForHost(host);
  if (!overview || !positions.length) {
    clearFileEditorFindOverview(host);
    return;
  }
  const fragment = document.createDocumentFragment();
  positions.forEach((position, index) => {
    const tick = document.createElement('span');
    tick.className = 'file-editor-find-overview-tick';
    tick.classList.toggle(CLS.active, index === activeIndex);
    tick.style.top = `${Math.max(0, Math.min(100, Number(position) || 0))}%`;
    fragment.append(tick);
  });
  overview.replaceChildren(fragment);
  overview.hidden = false;
}

function previewFindStateForHost(host = null, create = false) {
  if (!host) return null;
  if (!host._previewFindState && create) host._previewFindState = {query: '', matches: [], index: -1};
  return host._previewFindState || null;
}

function previewFindClearMatches(host = null) {
  const preview = host?.querySelector?.('.file-editor-preview-pane-panel');
  if (!preview) return;
  for (const match of preview.querySelectorAll('.file-editor-preview-find-match')) {
    const parent = match.parentNode;
    match.replaceWith(document.createTextNode(match.textContent || ''));
    parent?.normalize?.();
  }
  clearFileEditorFindOverview(host);
}

function previewFindUpdateOverview(host = null) {
  const preview = host?.querySelector?.('.file-editor-preview-pane-panel');
  const state = previewFindStateForHost(host);
  if (!preview || !state?.matches.length) {
    clearFileEditorFindOverview(host);
    return;
  }
  const previewRect = preview.getBoundingClientRect();
  const scrollHeight = Math.max(1, Number(preview.scrollHeight || 0));
  const positions = state.matches.map(match => {
    const offset = preview.scrollTop + match.getBoundingClientRect().top - previewRect.top;
    return (offset / scrollHeight) * 100;
  });
  updateFileEditorFindOverview(host, positions, state.index);
}

function refreshCodeMirrorFindOverview(host = null) {
  if (!host || fileEditorPanelMode(host) === 'preview') return;
  const view = host._cmView;
  const panel = codeMirrorSearchPanelForHost(host);
  const query = panel?.querySelector?.('input[name="search"]')?.value || '';
  const text = view?.state?.doc?.toString?.() || '';
  const matches = codeMirrorSearchMatches(text, query, codeMirrorSearchCheckboxState(panel));
  if (!matches.length || !view?.state?.doc) {
    clearFileEditorFindOverview(host);
    return;
  }
  const totalLines = Math.max(1, Number(view.state.doc.lines || 1));
  const positions = matches.map(match => ((view.state.doc.lineAt(match.from).number - 1) / totalLines) * 100);
  const summary = codeMirrorSearchMatchSummary(text, query, view.state.selection.main || {}, codeMirrorSearchCheckboxState(panel));
  updateFileEditorFindOverview(host, positions, summary.current - 1);
}

function previewFindUpdatePanel(host = null) {
  const panel = previewFindPanelForHost(host);
  const state = previewFindStateForHost(host);
  if (!panel || !state) return;
  const count = state.matches.length;
  const current = count && state.index >= 0 ? state.index + 1 : 0;
  const countNode = panel.querySelector('.file-editor-preview-find-count');
  if (countNode) countNode.textContent = state.query ? `${current}/${count}` : '';
  panel.querySelectorAll('[data-preview-find-move]').forEach(button => { button.disabled = count === 0; });
}

function previewFindSelectMatch(host = null, index = 0) {
  const state = previewFindStateForHost(host);
  if (!state?.matches.length) return false;
  state.index = (index + state.matches.length) % state.matches.length;
  state.matches.forEach((match, matchIndex) => match.classList.toggle(CLS.active, matchIndex === state.index));
  state.matches[state.index].scrollIntoView?.({block: 'center', inline: 'nearest'});
  previewFindUpdateOverview(host);
  previewFindUpdatePanel(host);
  return true;
}

function previewFindApplyQuery(host = null, query = '') {
  const preview = host?.querySelector?.('.file-editor-preview-pane-panel');
  const state = previewFindStateForHost(host, true);
  if (!preview || !state) return false;
  previewFindClearMatches(host);
  state.query = String(query || '');
  state.matches = [];
  state.index = -1;
  const needle = state.query.toLocaleLowerCase();
  if (needle) {
    const walker = document.createTreeWalker(preview, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue?.trim() || node.parentElement?.closest('script, style, .file-editor-preview-find-match')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
    for (const node of nodes) {
      const text = node.nodeValue || '';
      const folded = text.toLocaleLowerCase();
      let from = 0;
      let index = folded.indexOf(needle, from);
      if (index < 0) continue;
      const fragment = document.createDocumentFragment();
      while (index >= 0) {
        fragment.append(document.createTextNode(text.slice(from, index)));
        const match = document.createElement('mark');
        match.className = 'file-editor-preview-find-match';
        match.textContent = text.slice(index, index + needle.length);
        fragment.append(match);
        state.matches.push(match);
        from = index + needle.length;
        index = folded.indexOf(needle, from);
      }
      fragment.append(document.createTextNode(text.slice(from)));
      node.replaceWith(fragment);
    }
  }
  if (state.matches.length) previewFindSelectMatch(host, 0);
  else previewFindUpdatePanel(host);
  return true;
}

function previewFindOpenForHost(host = null) {
  return previewFindPanelForHost(host)?.hidden === false;
}

function openPreviewFind(host = null) {
  const panel = previewFindPanelForHost(host);
  if (!panel) return false;
  panel.hidden = false;
  const input = panel.querySelector('input');
  previewFindApplyQuery(host, input?.value || '');
  input?.focus();
  input?.select();
  return true;
}

function closePreviewFind(host = null) {
  const panel = previewFindPanelForHost(host);
  if (!panel) return false;
  previewFindClearMatches(host);
  const state = previewFindStateForHost(host, true);
  state.matches = [];
  state.index = -1;
  panel.hidden = true;
  previewFindUpdatePanel(host);
  return true;
}

function refreshPreviewFind(host = null) {
  if (!previewFindOpenForHost(host)) return;
  previewFindApplyQuery(host, previewFindPanelForHost(host)?.querySelector('input')?.value || '');
}

function updateEditorFindButton(button, state, host = null) {
  if (!button) return;
  const visible = state?.kind === 'text';
  button.hidden = !visible;
  button.disabled = false;
  const label = t('editor.findInFile', {shortcut: appShortcutText('F')});
  button.title = label;
  button.setAttribute('aria-label', label);
  const previewMode = fileEditorPanelMode(host) === 'preview';
  button.setAttribute('aria-pressed', previewMode ? String(previewFindOpenForHost(host)) : String(codeMirrorSearchPanelOpenForHost(host)));
  if (!previewMode) refreshCodeMirrorFindOverview(host);
  setFileEditorIcon(button, 'file-editor-icon-find');
}

function fileEditorGitActionControlsVisible(path, state, item = null) {
  if (state?.kind !== 'text' || !fileStateHasRepo(path, state) || !fileStateHasUsefulGitHistory(state)) return false;
  const active = editorViewModeFor(path, item) === 'diff';
  const confirmedNoDiff = state?.diffLoaded === true && !openFileDiffAvailable(state);
  return active || !confirmedNoDiff;
}

function fileEditorBlameControlsVisible(path, state, item = null) {
  return state?.kind === 'text' && fileStateHasRepo(path, state) && fileStateHasUsefulGitHistory(state);
}

function updateFileEditorBlameButton(button, path, state, item = null) {
  if (!button) return;
  const visible = fileEditorBlameControlsVisible(path, state, item);
  const editable = editorViewModeFor(path, item) === 'edit';
  button.hidden = !visible;
  button.disabled = !visible || !editable;
  syncPressedButton(button, fileEditorBlameEnabled, {
    labelOn: t('editor.blame.toggle'),
    labelOff: t('editor.blame.toggle'),
  });
  setFileEditorIcon(button, 'file-editor-icon-blame');
}

function updateFileEditorDiffButton(button, path, state, item = null) {
  if (!button) return;
  const active = editorViewModeFor(path, item) === 'diff';
  const loading = state?.diffLoading === true;
  // Keep the diff toggle on ANY text file (not just md/html) so .py/.js/.rs can switch edit<->diff.
  // The diff loads lazily on click (refreshOpenFileDiff); only hide the button once a load has
  // confirmed there is nothing to diff, and never while in diff mode.
  // Hide the git action pair for files with no useful file history (outside git, untracked, or only
  // the creation commit): there is no meaningful older file version to diff/blame against.
  const visible = fileEditorGitActionControlsVisible(path, state, item);
  button.hidden = !visible;
  button.disabled = !visible || (!active && loading);
  const label = !active && loading ? t('editor.diffLoading') : (active ? t('editor.diffExit') : t('common.diff'));
  syncPressedButton(button, active, {labelOn: label, labelOff: label});
  button.textContent = t('brand.tab.changes');
}

function updateFileEditorDiffExpandButton(button, path, state, item = null) {
  if (!button) return;
  const activeDiff = editorViewModeFor(path, item) === 'diff';
  button.hidden = state?.kind !== 'text' || !activeDiff || !openFileDiffAvailable(state);
  button.disabled = button.hidden || state?.diffLoading === true;
  button.setAttribute('aria-pressed', fileEditorDiffExpandUnchangedForItem(item) ? 'true' : 'false');
}

async function openEditorFind(host = null) {
  const view = host?._cmView || null;
  const status = host
    ? (msg, level) => setFileEditorPanelStatus(host, msg, level)
    : () => {};
  if (!view) {
    status(t('editor.findLoading'), 'warn');
    return false;
  }
  try {
    const api = await loadCodeMirrorApi();
    if (openCodeMirrorFindForView(api, view)) {
      syncCodeMirrorFindButtonForView(view);
      return true;
    }
  } catch (error) {
    status(t('editor.findUnavailable', {error}), 'error');
  }
  return false;
}

async function closeEditorFind(host = null) {
  const view = host?._cmView || null;
  if (!view) return false;
  try {
    const api = await loadCodeMirrorApi();
    if (api?.closeSearchPanel) {
      api.closeSearchPanel(view);
    } else {
      codeMirrorSearchPanelForHost(host)?.querySelector?.('.cm-dialog-close')?.click?.();
    }
    syncCodeMirrorFindButtonForView(view);
    return true;
  } catch (error) {
    setFileEditorPanelStatus(host, t('editor.findUnavailable', {error}), 'error');
  }
  return false;
}

async function toggleEditorFind(host = null) {
  if (fileEditorPanelMode(host) === 'preview') return previewFindOpenForHost(host) ? closePreviewFind(host) : openPreviewFind(host);
  return codeMirrorSearchPanelOpenForHost(host) ? closeEditorFind(host) : openEditorFind(host);
}

async function openEditorFindShortcut(host = null) {
  if (fileEditorPanelMode(host) === 'preview') return openPreviewFind(host);
  return openEditorFind(host);
}

async function focusFileEditorSearch(panel = null) {
  const opened = await openEditorFindShortcut(panel);
  if (panel) updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), openFiles.get(fileEditorPanelPath(panel)), panel);
  return opened;
}

function applyEditorWrapPreference() {
  document.querySelectorAll('.file-editor-panel').forEach(panel => {
    panel.classList.toggle('editor-wrap', fileEditorWrapEnabled);
    panel.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
    updateEditorWrapButton(panel.querySelector('.file-editor-wrap-panel'));
    updateEditorGutterButton(panel.querySelector('.file-editor-gutter-panel'));
    const path = panel.dataset.filePath;
    const state = openFiles.get(path);
    if (path && state?.kind === 'text') {
      const liveText = typeof codeMirrorCurrentText === 'function' ? codeMirrorCurrentText(panel) : null;
      if (liveText !== null && state.content !== liveText) state.content = liveText;
      renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
      if (typeof reconfigureCodeMirrorPanelEditorOptions === 'function' && reconfigureCodeMirrorPanelEditorOptions(panel)) {
        return;
      }
      // Re-render each panel with its OWN layout item: passing the editor item flipped
      // a preview/diff pane into an editor on any appearance change (e.g. font-size). This is a fallback
      // for raw/preview panels or browsers without CodeMirror compartments.
      renderFileEditorPanel(panel, panel.dataset.layoutItem || fileEditorItemFor(path));
    }
  });
}

function setEditorWrapEnabled(enabled) {
  fileEditorWrapEnabled = enabled === true;
  writeStoredEditorWrap(fileEditorWrapEnabled);
  applyEditorWrapPreference();
  scheduleShareUiStatePublish();
}

// toggle inline git blame. Fetch the blame payload for each open text file first (so the
// re-rendered editor's blame ViewPlugin has data), then re-render with each panel's OWN item.
async function applyEditorBlamePreference() {
  for (const panel of document.querySelectorAll('.file-editor-panel')) {
    const blameButton = panel.querySelector('.file-editor-blame-panel');
    const path = panel.dataset.filePath;
    const state = openFiles.get(path);
    const item = panel.dataset.layoutItem || fileEditorItemFor(path);
    updateFileEditorBlameButton(blameButton, path, state, item);
    if (!path || state?.kind !== 'text') continue;
    if (fileEditorBlameEnabled && editorViewModeFor(path, item) === 'edit' && fileEditorBlameControlsVisible(path, state, item) && !hasEditorBlameForPath(path)) await fetchEditorBlame(path);
    renderFileEditorPanel(panel, item);
  }
}

function setFileEditorBlameEnabled(enabled) {
  fileEditorBlameEnabled = enabled === true;
  storageSet('yolomux.editorBlame', fileEditorBlameEnabled ? '1' : '0');
  applyEditorBlamePreference();
  scheduleShareUiStatePublish();
}

function toggleFileEditorBlame() {
  setFileEditorBlameEnabled(!fileEditorBlameEnabled);
}

function toggleEditorWrap() {
  const enabled = !fileEditorWrapEnabled;
  setEditorWrapEnabled(enabled);
}

// B4: toggle showing ALL diff context vs collapsing unchanged regions. Persisted; re-renders
// every open editor in diff mode (the signature includes `expand`, so the diff view rebuilds).
function setDiffExpandUnchanged(enabled) {
  diffExpandUnchanged = enabled === true;
  storageSet('yolomux.diffExpandUnchanged', diffExpandUnchanged ? '1' : '0');
  fileEditorDiffExpandOverrides.clear();
  document.querySelectorAll('.file-editor-panel').forEach(panel => {
    const item = panel.dataset.layoutItem || fileEditorItemFor(panel.dataset.filePath || '');
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    updateFileEditorDiffExpandButton(panel.querySelector('.file-editor-diff-expand-panel'), path, state, item);
    if (path && state?.kind === 'text' && editorViewModeFor(path, item) === 'diff' && openFileDiffAvailable(state)) {
      renderFileEditorPanel(panel, item);
    }
  });
  scheduleShareUiStatePublish();
}

function toggleDiffExpandUnchanged() {
  setDiffExpandUnchanged(!diffExpandUnchanged);
}

function fileEditorDiffExpandUnchangedForItem(item = null) {
  if (item && fileEditorDiffExpandOverrides.has(item)) return fileEditorDiffExpandOverrides.get(item) === true;
  return diffExpandUnchanged;
}

function setFileEditorDiffExpandUnchangedForItem(path, item, enabled) {
  if (!isFileEditorItem(item)) return;
  fileEditorDiffExpandOverrides.set(item, enabled === true);
  const panel = panelNodes.get(item);
  const state = openFiles.get(path);
  if (panel) updateFileEditorDiffExpandButton(panel.querySelector('.file-editor-diff-expand-panel'), path, state, item);
  if (panel && state?.kind === 'text' && editorViewModeFor(path, item) === 'diff' && openFileDiffAvailable(state)) {
    renderFileEditorPanel(panel, item);
  }
  scheduleShareUiStatePublish();
}

function toggleFileEditorDiffExpandUnchangedForItem(path, item) {
  setFileEditorDiffExpandUnchangedForItem(path, item, !fileEditorDiffExpandUnchangedForItem(item));
}

function setEditorLineNumbersEnabled(enabled) {
  fileEditorLineNumbersEnabled = enabled === true;
  writeStoredEditorLineNumbers(fileEditorLineNumbersEnabled);
  applyEditorWrapPreference();
  scheduleShareUiStatePublish();
}

function toggleEditorLineNumbers() {
  const enabled = !fileEditorLineNumbersEnabled;
  setEditorLineNumbersEnabled(enabled);
}

function numberSetting(path, fallback) {
  const defaultValue = arguments.length >= 2 ? fallback : settingFallback(path);
  const value = Number(initialSetting(path, defaultValue));
  return Number.isFinite(value) ? value : defaultValue;
}

function boolSetting(path, fallback) {
  const defaultValue = arguments.length >= 2 ? fallback : settingFallback(path);
  const value = initialSetting(path, defaultValue);
  return value === true || value === 'true' || value === 1;
}

const UPDATE_NOTIFICATION_LEVELS = ['major', 'minor', 'patch', 'none'];

function normalizeUpdateNotificationLevel(value) {
  if (value === true || value === 'true' || value === 1) return 'patch';
  if (value === false || value === 'false' || value === 0) return 'none';
  const clean = String(value || '').trim().toLowerCase();
  return UPDATE_NOTIFICATION_LEVELS.includes(clean) ? clean : 'patch';
}

function updateNotificationLevelSetting() {
  return normalizeUpdateNotificationLevel(initialSetting('general.reload_on_update', false));
}

function semanticVersionParts(value) {
  const match = String(value || '').trim().match(/^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?/i);
  if (!match) return null;
  return [
    Number.parseInt(match[1] || '0', 10),
    Number.parseInt(match[2] || '0', 10),
    Number.parseInt(match[3] || '0', 10),
  ];
}

function updateNotificationAllowsVersion(currentVersion, targetVersion, level = updateNotificationLevelSetting()) {
  const cleanLevel = normalizeUpdateNotificationLevel(level);
  if (cleanLevel === 'none') return false;
  const currentParts = semanticVersionParts(currentVersion);
  const targetParts = semanticVersionParts(targetVersion);
  if (!currentParts || !targetParts) {
    return cleanLevel === 'patch' && String(targetVersion || '') !== String(currentVersion || '');
  }
  if (targetParts[0] !== currentParts[0]) return cleanLevel !== 'none';
  if (targetParts[1] !== currentParts[1]) return cleanLevel === 'minor' || cleanLevel === 'patch';
  if (targetParts[2] !== currentParts[2]) return cleanLevel === 'patch';
  return false;
}

function normalizeEditorCursorStyle(value) {
  return value === 'block' ? 'block' : 'line';
}

const UI_COLOR_CHOICES = ['green', 'blue', 'orange', 'yellow', 'purple', 'white'];
const DEFAULT_CURSOR_COLOR = 'yellow';
const BAD_CONNECTION_BODY_CLASS = 'bad-connection';
const BAD_CONNECTION_CURSOR_COLORS = Object.freeze({dark: '#ff6673', light: '#b91c1c'});
const BAD_CONNECTION_CURSOR_ACCENT = '#fff3f4';
const SEPARATOR_COLOR_CHOICES = ['theme', ...UI_COLOR_CHOICES];
const NEON_CURSOR_COLOR_CHOICES = ['laser-lime', 'neon-green', 'neon-cyan', 'neon-magenta', 'neon-orange'];
const CURSOR_COLOR_CHOICES = [...UI_COLOR_CHOICES, ...NEON_CURSOR_COLOR_CHOICES, 'theme'];
// One parent for the named UI colors. Active color and cursor color must both derive from this map so
// labels, swatches, and palette membership cannot drift.
const UI_COLOR_PRESETS = {
  green:  {labelKey: 'pref.appearance.active_color.green', cursorLabelKey: 'pref.appearance.editor_cursor_color.green', cursor: {dark: '#76b900', light: '#4f7f00'}, active: null},
  blue:   {labelKey: 'pref.appearance.active_color.blue', cursorLabelKey: 'pref.appearance.editor_cursor_color.blue', cursor: {dark: '#00b7ff', light: '#0069b8'}, active: {dark: {accent: '#3b82f6', bright: '#3b82f6', text: '#ffffff'}, light: {accent: '#2563eb', bright: '#2563eb', text: '#ffffff'}}},
  orange: {labelKey: 'pref.appearance.active_color.orange', cursorLabelKey: 'pref.appearance.editor_cursor_color.orange', cursor: {dark: '#ff7a00', light: '#b91c1c'}, active: {dark: {accent: '#f97316', bright: '#f97316', text: '#1a0c00'}, light: {accent: '#b91c1c', bright: '#b91c1c', text: '#ffffff'}}},
  yellow: {labelKey: 'pref.appearance.active_color.yellow', cursorLabelKey: 'pref.appearance.editor_cursor_color.yellow', cursor: {dark: '#ffea00', light: '#9a6700'}, active: {dark: {accent: '#eab308', bright: '#eab308', text: '#1a1500'}, light: {accent: '#d6a400', bright: '#d6a400', text: '#1a1500'}}},
  purple: {labelKey: 'pref.appearance.active_color.purple', cursorLabelKey: 'pref.appearance.editor_cursor_color.purple', cursor: {dark: '#d946ef', light: '#7c3aed'}, active: {dark: {accent: '#a855f7', bright: '#a855f7', text: '#ffffff'}, light: {accent: '#7c3aed', bright: '#7c3aed', text: '#ffffff'}}},
  white:  {labelKey: 'pref.appearance.active_color.white', cursorLabelKey: 'pref.appearance.editor_cursor_color.white', cursor: {dark: '#ffffff', light: '#6b7280'}, active: {dark: {accent: '#e8edf2', bright: '#e8edf2', text: '#0b0e14'}, light: {accent: '#9aa5b3', bright: '#dfe5ec', text: '#0b0e14'}}},
  'laser-lime':   {cursorLabelKey: 'pref.appearance.editor_cursor_color.laser-lime', cursor: {dark: '#ccff00', light: '#6b8f00'}},
  'neon-green':   {cursorLabelKey: 'pref.appearance.editor_cursor_color.neon-green', cursor: {dark: '#39ff14', light: '#16825d'}},
  'neon-cyan':    {cursorLabelKey: 'pref.appearance.editor_cursor_color.neon-cyan', cursor: {dark: '#00ffff', light: '#0e7490'}},
  'neon-magenta': {cursorLabelKey: 'pref.appearance.editor_cursor_color.neon-magenta', cursor: {dark: '#ff00ff', light: '#a21caf'}},
  'neon-orange':  {cursorLabelKey: 'pref.appearance.editor_cursor_color.neon-orange', cursor: {dark: '#ff9f0a', light: '#b45309'}},
};

const ACTIVE_COLOR_PRESETS = Object.fromEntries(
  UI_COLOR_CHOICES
    .map(value => [value, UI_COLOR_PRESETS[value]?.active])
    .filter(([, active]) => active)
);

function normalizeEditorCursorColor(value) {
  return value === 'theme' || UI_COLOR_PRESETS[value]?.cursor ? value : DEFAULT_CURSOR_COLOR;
}

function cursorColorForPreset(value, light = false) {
  const cursor = UI_COLOR_PRESETS[value]?.cursor;
  if (typeof cursor === 'string') return cursor;
  return light ? cursor?.light : cursor?.dark;
}

function editorCursorColorForScheme(scheme = activeEditorScheme()) {
  const value = normalizeEditorCursorColor(fileEditorCursorColor);
  return value === 'theme' ? scheme.cursor : cursorColorForPreset(value, scheme?.dark === false);
}

function activeTerminalCursorColorForTheme(baseTheme = terminalThemeForGlobalTheme()) {
  const value = normalizeEditorCursorColor(fileEditorCursorColor);
  return value === 'theme' ? baseTheme.cursor : cursorColorForPreset(value, resolvedTerminalThemeMode() === 'light');
}

function badConnectionCursorStateActive() {
  return document.body?.classList?.contains(BAD_CONNECTION_BODY_CLASS) === true;
}

function badConnectionTerminalCursorColor() {
  return resolvedTerminalThemeMode() === 'light' ? BAD_CONNECTION_CURSOR_COLORS.light : BAD_CONNECTION_CURSOR_COLORS.dark;
}

function terminalCursorBlinkEnabled() {
  return !badConnectionCursorStateActive();
}

function terminalThemeWithBadConnectionCursor(theme) {
  return {...theme, cursor: badConnectionTerminalCursorColor(), cursorAccent: BAD_CONNECTION_CURSOR_ACCENT};
}

function setBadConnectionCursorState(active) {
  document.body?.classList?.toggle(BAD_CONNECTION_BODY_CLASS, active === true);
  refreshActiveTerminalCursor();
}

function applyCursorColorSetting() {
  const style = document.documentElement?.style;
  if (!style) return;
  style.setProperty('--active-terminal-cursor-rgb', hexToRgbTriple(activeTerminalCursorColorForTheme()));
}

function applyEditorCursorStyle() {
  fileEditorCursorStyle = normalizeEditorCursorStyle(fileEditorCursorStyle);
  document.body?.classList.remove('editor-cursor-line', 'editor-cursor-block');
  document.body?.classList.add(`editor-cursor-${fileEditorCursorStyle}`);
}

function applyInactivePaneOpacity(value) {
  const number = Number(value);
  const percent = Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : 60;
  document.documentElement?.style.setProperty('--inactive-pane-opacity-scale', String(percent / 100));
}

function applyPaneRingOpacity(value) {
  const number = Number(value);
  const percent = Number.isFinite(number) ? Math.max(5, Math.min(100, number)) : 75;
  const root = document.documentElement?.style;
  if (!root) return;
  root.setProperty('--pane-ring-opacity', `${percent}%`);
  root.setProperty('--pane-active-ring-opacity', `${percent}%`);
}

function hexToRgbTriple(hex) {
  const h = String(hex || '').replace('#', '');
  if (h.length !== 6) return '118 185 0';
  return `${parseInt(h.slice(0, 2), 16)} ${parseInt(h.slice(2, 4), 16)} ${parseInt(h.slice(4, 6), 16)}`;
}

function uiColorVisualPreset(value, light = false) {
  if (value === 'green') {
    return light
      ? {accent: '#5f9800', bright: '#4f9e3a', text: '#071000'}
      : {accent: '#76b900', bright: '#86d600', text: '#071000'};
  }
  const preset = ACTIVE_COLOR_PRESETS[value];
  return light ? preset?.light : preset?.dark;
}

// Set the active-accent source vars on both roots: documentElement covers surfaces that resolve tokens
// there, while body inline beats body.theme-light's class-level defaults for normal descendants.
// Green/unknown clears both so the CSS defaults stand. Re-run on theme switch since the per-preset values
// differ per resolved theme.
function applyActiveColor(value) {
  const styles = [document.documentElement?.style, document.body?.style].filter(Boolean);
  if (!styles.length) return;
  const vars = ['--active-accent', '--active-accent-rgb', '--active-accent-bright', '--active-accent-text', '--active-accent-dim', '--active-accent-soft'];
  const preset = ACTIVE_COLOR_PRESETS[value];
  if (!preset) {
    styles.forEach(style => vars.forEach(v => style.removeProperty(v)));
    updateBrowserFavicon({force: true});
    return;
  }
  const light = document.body.classList.contains(themeResolvedBodyClass('light'));
  const p = light ? preset.light : preset.dark;
  const rgb = hexToRgbTriple(p.accent);
  for (const style of styles) {
    style.setProperty('--active-accent', p.accent);
    style.setProperty('--active-accent-rgb', rgb);
    style.setProperty('--active-accent-bright', p.bright);
    style.setProperty('--active-accent-text', p.text);
    style.setProperty('--active-accent-dim', `color-mix(in srgb, ${p.accent} 26%, var(--panel))`);
    style.setProperty('--active-accent-soft', `rgb(${rgb} / 0.12)`);
  }
  // keep the browser-tab favicon background/glyph in sync with the chosen accent + theme
  updateBrowserFavicon({force: true});
}

function applySeparatorColor(value) {
  const styles = [document.documentElement?.style, document.body?.style].filter(Boolean);
  if (!styles.length) return;
  const vars = ['--pane-resizer-bg', '--pane-resizer-hover-bg', '--pane-resizer-shadow'];
  const preset = String(value || 'theme') === 'theme'
    ? null
    : uiColorVisualPreset(value, document.body?.classList?.contains('theme-resolved-light'));
  if (!preset) {
    styles.forEach(style => vars.forEach(v => style.removeProperty(v)));
    return;
  }
  const rgb = hexToRgbTriple(preset.bright || preset.accent);
  for (const style of styles) {
    style.setProperty('--pane-resizer-bg', `rgb(${rgb} / 0.72)`);
    style.setProperty('--pane-resizer-hover-bg', `rgb(${rgb} / 0.96)`);
    style.setProperty('--pane-resizer-shadow', `rgb(${rgb} / 0.72)`);
  }
}

function applyStatusPulseModeClass() {
  const enabled = typeof statusPulseAnimationEnabled === 'function' ? statusPulseAnimationEnabled() : false;
  document.documentElement?.classList.toggle('status-pulse-disabled', !enabled);
}

function applyCssSettings() {
  applyStatusPulseModeClass();
  const root = document.documentElement?.style;
  if (!root) return;
  const uiFontSize = numberSetting('appearance.ui_font_size', 13);
  root.setProperty('--ui-font-size', `${uiFontSize}px`);
  root.setProperty('--tab-label-size', `${uiFontSize}px`);
  root.setProperty('--terminal-font-size', `${terminalFontSize}px`);
  root.setProperty('--editor-font-size', `${editorFontSize}px`);
  root.setProperty('--editor-preview-font-size', `${editorPreviewFontSize}px`);
  root.setProperty('--file-explorer-font-size', `${fileExplorerFontSize}px`);
  root.setProperty('--pane-tab-width', `${numberSetting('appearance.tab_width', 172)}px`);
  // #261: pane spacing (0-20px) = the gap on each side of the separator AND the width of the active
  // pane's green "border" (which fills its side of that gap up to the line). At 0: no gap, no green —
  // panes sit flush to the 1px separator. The red needs-* attention ring keeps its own constant width
  // (--pane-tab-panel-ring-width, unchanged) so it stays visible even at spacing 0.
  const paneSpacing = Math.max(0, Math.min(20, numberSetting('appearance.pane_spacing', 3)));
  root.setProperty('--pane-split-gap', `${paneSpacing}px`);
  // Opacity (5-100%) of the translucent pane ring. The active ring follows the same setting; otherwise
  // low values appear to save correctly while the visible focused pane still stays prominent.
  const paneRingOpacity = Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75)));
  applyPaneRingOpacity(paneRingOpacity);
  applyInactivePaneOpacity(numberSetting('appearance.inactive_pane_opacity', 60));
  applyActiveColor(initialSetting('appearance.active_color', 'green'));
  applySeparatorColor(initialSetting('appearance.separator_color', 'theme'));
  applyCursorColorSetting();
  const statusPulsePeriodMs = Math.max(1, agentStatusPulsePeriodMs);
  root.setProperty('--pulse-duration', `${statusPulsePeriodMs / 1000}s`);
  root.setProperty('--red-reminder-duration', `${statusPulsePeriodMs / 1000}s`);
  // Quantize the opacity pulse into ~125ms steps (steps(N) timing): step size = period / N with
  // N = round(period / 125). 125ms (~8 opacity updates/sec at the 1.55s default) was chosen over:
  //   - 250ms (the old value): ~4/sec read as a perceptible staircase on the 0.16->1 ramp;
  //   - 50ms: ~20/sec is near-continuous but pays close to full 60fps repaint cost on EVERY status
  //     ball (per-window, tabs, YO!info, topbar) -- the stepping exists to avoid exactly that;
  //   - 500ms: ~3 samples per cycle looks like a strobe, not a breathing pulse.
  // 125ms is the smoothness/cost midpoint: visibly smoother than 250ms yet still far fewer repaints
  // than a per-frame animation. We pin the STEP size (not a fixed step count) so repaint rate and
  // perceived smoothness stay ~constant across the whole 250-10000ms period preference; a fixed
  // count would over-repaint short periods and look chunky on long ones.
  root.setProperty('--status-pulse-step-count', String(Math.max(1, Math.round(statusPulsePeriodMs / 125))));
  if (typeof setAttentionAnimationClockDelay === 'function') setAttentionAnimationClockDelay();
  root.setProperty('--popover-show-delay', `${popoverShowDelayMs}ms`);
  root.setProperty('--popover-hide-delay', `${popoverHideDelayMs}ms`);
  root.setProperty('--file-image-preview-max-size', `${Math.max(1, fileExplorerImagePreviewMaxPx)}px`);
}

function applyGlobalThemeMode(options = {}) {
  globalThemeMode = normalizeGlobalThemeMode(globalThemeMode);
  const resolved = resolvedGlobalThemeMode();
  document.body?.classList.remove(...THEME_BODY_CLASSES);
  document.body?.classList.add(themeBodyClass(resolved), themeResolvedBodyClass(resolved));
  if (globalThemeMode === 'system') document.body?.classList.add(themeBodyClass('system'));
  if (document.documentElement?.style) document.documentElement.style.colorScheme = resolved;
  if (options.updateEditor !== false) applyEditorThemeMode({refreshEditors: options.refreshEditors !== false});
  if (options.updateTerminals) applyTerminalRuntimeSettings({fit: false});
  // the active-color presets are theme-specific, so re-apply on every theme switch.
  applyActiveColor(initialSetting('appearance.active_color', 'green'));
  applySeparatorColor(initialSetting('appearance.separator_color', 'theme'));
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts();
  scheduleShareTopologySnapshot('theme');
  if (typeof scheduleShareAppearancePublish === 'function') {
    scheduleShareAppearancePublish({reason: options.reason || 'theme', topology: false});
  }
}

let globalThemeMediaListenerInstalled = false;

function installGlobalThemeMediaListener() {
  if (globalThemeMediaListenerInstalled) return;
  const query = globalThemeMediaQuery();
  if (!query) return;
  const handler = () => {
    if (normalizeGlobalThemeMode(globalThemeMode) !== 'system') return;
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true, reason: 'theme-system'});
    renderSessionButtons();
    renderPaneTabStrips();
  };
  if (typeof query.addEventListener === 'function') query.addEventListener('change', handler);
  else if (typeof query.addListener === 'function') query.addListener(handler);
  globalThemeMediaListenerInstalled = true;
}

// The ACTIVE pane's terminal gets the configured cursor color so it's obvious which terminal you're
// typing into; every other terminal keeps its theme's default cursor color.

function terminalThemeForSession(session, baseTheme) {
  const theme = baseTheme || terminalThemeForGlobalTheme();
  if (badConnectionCursorStateActive()) return terminalThemeWithBadConnectionCursor(theme);
  return session === focusedPanelItem ? {...theme, cursor: activeTerminalCursorColorForTheme(theme)} : theme;
}

function applyTerminalContainerTheme(container, theme = terminalThemeForGlobalTheme(), mode = globalThemeMode) {
  if (!container) return;
  container.dataset.terminalTheme = resolvedTerminalThemeMode(terminalThemeMode, mode);
  if (container.style) container.style.background = theme.background;
}

function applyTerminalRuntimeSettings(options = {}) {
  // one theme source for every terminal AND its container, so all panes share the same
  // white in light mode (no pane-level tint showing a different white); + minimumContrastRatio so
  // faint 24-bit agent output stays legible on white.
  const theme = terminalThemeForGlobalTheme();
  const minContrast = terminalMinimumContrastRatio();
  for (const [session, item] of terminals.entries()) {
    if (!item?.term) continue;
    item.term.options.fontFamily = terminalFontFamily;
    item.term.options.fontSize = terminalFontSize;
    item.term.options.scrollback = terminalScrollback;
    item.term.options.cursorBlink = terminalCursorBlinkEnabled();
    item.term.options.theme = terminalThemeForSession(session, theme);
    item.term.options.minimumContrastRatio = minContrast;
    item.term.clearTextureAtlas?.();
    refreshTerminal(session);
    applyTerminalContainerTheme(item.container, theme);
    if (options.fit !== false) scheduleFit(session);
  }
}

// Lightweight cursor-only refresh for focus changes: re-color just the cursor so the active pane's
// terminal blinks yellow and the rest revert to their theme default, without re-fitting every pane.
function refreshActiveTerminalCursor() {
  const base = terminalThemeForGlobalTheme();
  const badConnection = badConnectionCursorStateActive();
  for (const [session, item] of terminals.entries()) {
    if (!item?.term?.options) continue;
    item.term.options.cursorBlink = !badConnection;
    const cursor = badConnection
      ? badConnectionTerminalCursorColor()
      : (session === focusedPanelItem ? activeTerminalCursorColorForTheme(base) : base.cursor);
    const cursorAccent = badConnection ? BAD_CONNECTION_CURSOR_ACCENT : base.cursorAccent;
    const current = item.term.options.theme || base;
    if (current.cursor !== cursor || current.cursorAccent !== cursorAccent) {
      item.term.options.theme = {...current, cursor, cursorAccent};
    }
  }
}

function refreshMetaButtonChrome() {
  if (!refreshMeta) return;
  const loading = transcriptMetadataState.loading === true;
  const seconds = ms => `${Math.round(ms / 1000)}s`;
  refreshMeta.textContent = t('common.refresh');
  refreshMeta.title = loading
    ? t('info.loadingRepo')
    : t('meta.refreshTitle', {ping: seconds(latencyRefreshMs), openLogs: seconds(eventLogRefreshMs), tabber: seconds(tabberActivityRefreshMs)});
  refreshMeta.setAttribute('aria-label', loading ? t('info.loadingRepo') : t('meta.refreshAria'));
}

function applySettingsPayload(payload, options = {}) {
  if (!payload?.settings) return false;
  const nextMtime = Number(payload.mtime_ns || 0);
  if (!options.force && nextMtime && nextMtime === clientSettingsMtimeNs) return false;
  const previousLocale = i18nActiveLocaleId();
  const previousDateTimeHourCycle = dateTimeHourCycle;
  const previousAgentStatusPulsePeriodMs = agentStatusPulsePeriodMs;
  clientSettingsPayload = payload;
  clientSettingsMetadataDeferred = payload.deferred_metadata === true;
  clientSettingsDefaults = payload.defaults || clientSettingsDefaults;
  clientSettings = mergeSettingObjects(clientSettingsDefaults, payload.settings || {});
  clientSettingsMtimeNs = nextMtime;
  remoteResizeDelayMs = numberSetting('performance.remote_resize_delay_ms');
  latencyRefreshMs = numberSetting('performance.latency_refresh_ms');
  eventLogRefreshMs = numberSetting('performance.event_log_refresh_ms');
  tabberActivityRefreshMs = numberSetting('performance.tabber_activity_refresh_ms');
  agentStatusPulsePeriodMs = numberSetting('performance.agent_status_pulse_period_ms');
  workflowTransitionGlowSeconds = numberSetting('performance.workflow_transition_glow_seconds');
  toastDurationMs = numberSetting('notifications.toast_duration_ms');
  popoverShowDelayMs = numberSetting('performance.popover_show_delay_ms');
  hoverCloseDelayMs = numberSetting('performance.popover_hide_delay_ms');
  popoverHideDelayMs = hoverCloseDelayMs;
  menuHoverOpenDelayMs = numberSetting('performance.menu_hover_open_delay_ms');
  menuHoverCloseDelayMs = hoverCloseDelayMs;
  tabPopoverShowDelayMs = numberSetting('performance.tab_popover_show_delay_ms');
  tabPopoverFollowDelayMs = numberSetting('performance.tab_popover_follow_delay_ms');
  fileExplorerIndexRefreshSeconds = numberSetting('file_explorer.index_refresh_seconds');
  fileExplorerNewEntryHighlightMs = numberSetting('file_explorer.new_entry_highlight_ms');
  fileExplorerImagePreviewMaxPx = numberSetting('file_explorer.image_preview_max_px');
  fileExplorerImageOpenMode = normalizedImageOpenMode(initialSetting('file_explorer.image_open_mode'));
  reconcileIndexedDirsFromSetting({initial: options.initial === true});
  reconcileIndexExcludePathsFromSetting();
  uploadMaxBytes = numberSetting('uploads.max_bytes');
  shareDefaultTtlSeconds = numberSetting('share.ttl_seconds', 600);
  shareDefaultMaxViewers = numberSetting('share.max_viewers', 2);
  shareDefaultReadOnly = boolSetting('share.read_only', true);
  shareDefaultScheme = initialSetting('share.scheme', 'http') === 'https' ? 'https' : 'http';
  shareViewFit = normalizeShareViewFit(storageGet(shareViewFitStorageKey) || initialSetting('share.view_fit', shareViewFit));
  terminalFontSize = numberSetting('appearance.terminal_font_size');
  editorFontSize = numberSetting('appearance.editor_font_size');
  editorPreviewFontSize = numberSetting('appearance.preview_font_size', editorFontSize + 1);
  fileExplorerFontSize = numberSetting('appearance.file_explorer_font_size');
  terminalScrollback = numberSetting('terminal_editor.scrollback');
  fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
  fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds');
  const previousBlameAllLines = fileEditorBlameAllLines;
  fileEditorBlameAllLines = boolSetting('editor.blame_all_lines', false);
  autoFocusEnabled = boolSetting('general.auto_focus');
  startupHelpersEnabled = boolSetting('general.startup_tips');
  const previousEditorSchemeId = activeEditorScheme().id;
  const previousCursorColor = fileEditorCursorColor;
  globalThemeMode = normalizeGlobalThemeMode(initialSetting('appearance.theme', defaultGlobalTheme));
  terminalThemeMode = normalizeTerminalThemeMode(initialSetting('appearance.terminal_theme', defaultTerminalTheme));
  dateTimeHourCycle = normalizeDateTimeHourCycle(initialSetting('appearance.date_time_hour_cycle'));
  fileEditorCursorStyle = normalizeEditorCursorStyle(initialSetting('appearance.editor_cursor_style', 'block'));
  fileEditorCursorColor = normalizeEditorCursorColor(initialSetting('appearance.editor_cursor_color', DEFAULT_CURSOR_COLOR));
  fileEditorThemeMode = readConfiguredEditorScheme();
  if (options.initial || options.applyEditorDefaults) {
    fileEditorWrapEnabled = boolSetting('terminal_editor.word_wrap', fileEditorWrapEnabled);
    fileEditorLineNumbersEnabled = boolSetting('terminal_editor.line_numbers', fileEditorLineNumbersEnabled);
  }
  fileExplorerRootMode = initialSetting('file_explorer.root_mode', fileExplorerRootMode) === 'sync' ? 'sync' : 'fixed';
  applyCssSettings();
  if (previousAgentStatusPulsePeriodMs !== agentStatusPulsePeriodMs) {
    // Retiming an existing stepped CSS animation is browser-dependent. Reuse the status owner to
    // clear phase state and restart only these animations after the new duration is on :root.
    if (typeof restartAgentWindowActivityPulseAnimations === 'function') restartAgentWindowActivityPulseAnimations();
    else if (typeof scheduleAgentWindowActivityAnimationSync === 'function') scheduleAgentWindowActivityAnimationSync();
  }
  if (typeof updateEditorPreviewFontControls === 'function') updateEditorPreviewFontControls();
  if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();
  applyGlobalThemeMode({updateEditor: false, updateTerminals: false});
  applyEditorThemeMode({refreshEditors: false});
  applyEditorCursorStyle();
  applyTerminalRuntimeSettings();
  applyEditorWrapPreference();
  renderFileExplorerRootModeControls();
  refreshMetaButtonChrome();
  renderPreferencesPanels();
  renderSessionButtons();
  renderPaneTabStrips();
  rescheduleAllFileAutosaves();
  if (previousDateTimeHourCycle !== dateTimeHourCycle) {
    rerenderDateTimeFormatSurfaces();
  }
  if (previousEditorSchemeId !== activeEditorScheme().id || previousCursorColor !== fileEditorCursorColor) {
    // re-theme LIVE editors via the compartment swap (preserves scroll/selection). A plain
    // renderFileEditorPanel short-circuits because codeMirrorConfigSignature omits the scheme, so the
    // CM view would keep its old theme; refreshOpenEditorThemePanels reconfigures the theme directly.
    refreshOpenEditorThemePanels();
  }
  // the blame ViewPlugin decorates per fileEditorBlameAllLines at build time + the editor
  // config signature carries it, so re-render open editors when the toggle changes (only matters while
  // blame is on).
  if (previousBlameAllLines !== fileEditorBlameAllLines && fileEditorBlameEnabled) applyEditorBlamePreference();
  // i18n: when general.language changes, load the new catalog and re-render localized surfaces.
  const nextLocale = resolveLocalePref(initialSetting('general.language', 'system'));
  if (nextLocale !== previousLocale) applyLocale(nextLocale);
  if (!options.initial) {
    installRuntimeIntervals();
    scheduleShareAppearancePublish();
  }
  return true;
}

function scheduleDeferredSettingsMetadataRefresh() {
  if (!clientSettingsMetadataDeferred || shareViewMode) return null;
  if (clientSettingsMetadataRefreshPromise) return clientSettingsMetadataRefreshPromise;
  if (clientSettingsMetadataRefreshTimer) return null;
  clientSettingsMetadataRefreshTimer = setTimeout(() => {
    clientSettingsMetadataRefreshTimer = null;
    clientSettingsMetadataRefreshPromise = refreshSettings({force: true, silent: true})
      .finally(() => { clientSettingsMetadataRefreshPromise = null; });
  }, 0);
  return null;
}

async function refreshSettings(options = {}) {
  try {
    const payload = await apiFetchJson('/api/settings', {cache: 'no-store'});
    const changed = applySettingsPayload(payload, {force: options.force === true});
    if (changed) refreshYoloRulesStatus({silent: true});
    if (changed && !options.silent) statusEl.textContent = t('status.settingsReloaded');
  } catch (error) {
    if (!options.silent) {
      statusErr(localizedHtml('status.settingsReloadFailed', {
        error: userMessageText(error, t('common.requestFailed')),
      }));
    }
  }
}

const runtimeIntervals = new Map();

function runtimeIntervalDelay(baseDelay) {
  const base = Math.max(1, Math.round(Number(baseDelay) || 1));
  return base;
}

function resetRuntimeInterval(name, callback, delay) {
  const normalizedDelay = Math.max(1, Math.round(Number(delay) || 1));
  const existing = runtimeIntervals.get(name);
  if (existing?.delay === normalizedDelay) {
    existing.callback = callback;
    return existing;
  }
  if (existing) {
    existing.active = false;
    clearTimeout(existing.timer);
  }
  const state = {active: true, timer: null, delay: normalizedDelay, callback};
  const scheduleNext = () => {
    if (!state.active) return;
    state.timer = setTimeout(run, runtimeIntervalDelay(state.delay));
  };
  const run = () => {
    if (!state.active) return;
    try {
      Promise.resolve(state.callback())
        .catch(error => console.warn('runtime interval failed', name, error))
        .finally(scheduleNext);
    } catch (error) {
      console.warn('runtime interval failed', name, error);
      scheduleNext();
    }
  };
  scheduleNext();
  runtimeIntervals.set(name, state);
  return state;
}

function clearRuntimeInterval(name) {
  const existing = runtimeIntervals.get(name);
  if (!existing) return false;
  existing.active = false;
  clearTimeout(existing.timer);
  runtimeIntervals.delete(name);
  return true;
}

function runtimeIntervalActive(name) {
  return runtimeIntervals.get(name)?.active === true;
}

function renewServerWatchRootsFromRuntime() {
  if (clientPushCanSupplyData() && typeof syncServerWatchRoots === 'function') {
    syncServerWatchRoots({renew: true});
  }
}

function installRuntimeIntervals() {
  resetRuntimeInterval('latency', updateLatency, latencyRefreshMs);
  resetRuntimeInterval('events', refreshOpenEventLogs, eventLogRefreshMs);
  resetRuntimeInterval('auto-approve', () => {
    if (document.visibilityState === 'hidden') return null;
    if (clientEventTransportState.connected === true) return null;
    return refreshAutoStatuses();
  }, autoApproveDisconnectedPollMs);
  resetRuntimeInterval('server-watch-renew', renewServerWatchRootsFromRuntime, serverWatchRenewMs);
  if (fileExplorerMode === 'tabber') {
    resetRuntimeInterval('tabber-activity', () => { if (fileExplorerMode === 'tabber') fetchTabberActivity(); }, tabberActivityRefreshMs);
  }
  if (fileExplorerIndexRefreshSeconds > 0) {
    resetRuntimeInterval('file-index-refresh', refreshAllIndexedDirsStatus, fileExplorerIndexRefreshSeconds * 1000);
  } else {
    clearRuntimeInterval('file-index-refresh');
  }
}

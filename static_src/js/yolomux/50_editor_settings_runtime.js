function editorViewModeKey(path, item = null) {
  return item && isFileEditorItem(item) ? item : path;
}

function editorViewModeFor(path, item = null) {
  const modes = fileEditorViewModesForPath(path);
  const mode = modes.get(editorViewModeKey(path, item)) || modes.get(path);
  if (mode === 'diff') return 'diff';
  if (!editorPreviewModeAvailable(path)) return 'edit';
  if (editorViewModes.has(mode)) return mode;
  return 'edit';
}

function setFileEditorViewMode(path, mode, item = null) {
  if (!path || !editorViewModes.has(mode)) return;
  if (mode !== 'edit' && mode !== 'diff' && !editorPreviewModeAvailable(path)) mode = 'edit';
  if ((mode === 'preview' || mode === 'split') && typeof closeFilePreviewPopout === 'function') closeFilePreviewPopout(path);
  fileEditorViewModesForPath(path, true).set(editorViewModeKey(path, item), mode);
}

function updateEditorModeControl(control, path, state, item = null) {
  if (!control) return;
  const visible = state?.kind === 'text' && editorPreviewModeAvailable(path);
  control.hidden = !visible;
  if (!visible) return;
  const mode = editorViewModeFor(path, item);
  control.querySelectorAll('[data-editor-mode]').forEach(button => {
    const nextMode = button.dataset.editorMode;
    const label = editorModeLabel(nextMode);
    const active = button.dataset.editorMode === mode;
    syncPressedButton(button, active, {labelOn: label, labelOff: label});
    setFileEditorIcon(button, editorModeIconClass(nextMode));
  });
}

function editorModeLabel(mode) {
  if (mode === 'diff') return 'Diff';
  if (mode === 'preview') return 'Preview';
  if (mode === 'split') return 'Split view';
  return 'Edit';
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
    labelOn: 'Hide line numbers',
    labelOff: 'Show line numbers',
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
  if (fileEditorPreviewDisplayMode === 'vanilla') return 'Vanilla preview';
  const scheme = mode === editorThemeInheritMode ? activeEditorScheme() : (EDITOR_SCHEMES[normalizeEditorSchemeId(mode)] || EDITOR_SCHEMES.dark);
  if (mode === editorThemeInheritMode) return `Inherit global theme (${scheme.label})`;
  return `${scheme.label} editor scheme`;
}

function editorPreviewThemeState() {
  if (fileEditorPreviewDisplayMode === 'vanilla') return 'vanilla';
  return activeEditorScheme().dark ? 'dark' : 'light';
}

function editorPreviewThemeStateLabel(state = editorPreviewThemeState()) {
  if (state === 'vanilla') return 'Vanilla preview';
  return state === 'light' ? 'Bright preview' : 'Dark preview';
}

function editorPreviewThemeShortLabel(state = editorPreviewThemeState()) {
  if (state === 'vanilla') return 'Vanilla';
  return state === 'light' ? 'Bright' : 'Dark';
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
    // 'yellow' matches the active terminal's caret (#ffd000) for a consistent typing cursor across
    // terminal + editor; 'theme' keeps the per-scheme caret. Drives both the line and block cursor CSS.
    '--editor-cursor': fileEditorCursorColor === 'theme' ? scheme.cursor : activeTerminalCursorColor,
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
  button.classList.toggle('theme-dark', previewState === 'dark');
  button.classList.toggle('theme-light', previewState === 'light');
  button.classList.toggle('theme-vanilla', previewState === 'vanilla');
  button.classList.toggle('theme-with-label', includeVanilla);
  button.dataset.editorTheme = previewState === 'vanilla' ? 'vanilla' : scheme.id;
  button.dataset.editorThemeShort = includeVanilla ? editorPreviewThemeShortLabel(previewState) : '';
  button.dataset.editorThemeNext = includeVanilla ? editorPreviewThemeShortLabel(nextState) : '';
  button.setAttribute('aria-pressed', previewState === 'dark' ? 'false' : 'true');
  button.title = `${editorThemeLabel()}; next: ${editorPreviewThemeStateLabel(nextState)}`;
  button.setAttribute('aria-label', editorThemeLabel());
  setFileEditorIcon(button, 'file-editor-icon-theme');
}

function updateImageViewerThemeButton(button) {
  updateEditorThemeButton(button);
  if (!button) return;
  button.title = `Toggle image background (${activeEditorScheme().label})`;
  button.setAttribute('aria-label', 'Toggle image background');
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
      captureFileEditorPanelViewState(item, panel);
      renderFileEditorPanel(panel, item);
    }
  });
  if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();
}

function applyEditorThemeMode(options = {}) {
  const scheme = activeEditorScheme();
  applyEditorSchemeCssVariables(scheme);
  document.body?.classList.remove('editor-theme-system', 'editor-theme-dark', 'editor-theme-light', 'editor-preview-vanilla');
  EDITOR_SCHEME_IDS.forEach(id => document.body?.classList.remove(`editor-scheme-${id}`));
  document.body?.classList.add(scheme.dark ? 'editor-theme-dark' : 'editor-theme-light');
  document.body?.classList.add(`editor-scheme-${scheme.id}`);
  document.body?.classList.toggle('editor-preview-vanilla', fileEditorPreviewDisplayMode === 'vanilla');
  document.querySelectorAll('.file-editor-theme-panel').forEach(updateEditorThemeButton);
  if (options.refreshEditors) refreshOpenEditorThemePanels();
}

function setFileEditorThemeMode(mode) {
  fileEditorThemeMode = normalizeEditorThemeMode(mode);
  writeStoredEditorThemeMode(fileEditorThemeMode);
  if (fileEditorPreviewDisplayMode !== 'theme') {
    fileEditorPreviewDisplayMode = 'theme';
    writeStoredEditorPreviewDisplayMode(fileEditorPreviewDisplayMode);
  }
  applyEditorThemeMode({refreshEditors: true});
}

function setFileEditorPreviewDisplayMode(mode) {
  fileEditorPreviewDisplayMode = normalizeEditorPreviewDisplayMode(mode);
  writeStoredEditorPreviewDisplayMode(fileEditorPreviewDisplayMode);
  applyEditorThemeMode({refreshEditors: true});
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
    labelOn: 'Disable word wrap',
    labelOff: 'Enable word wrap',
  });
  setFileEditorIcon(button, 'file-editor-icon-wrap');
}

function updateEditorFindButton(button, state, host = null) {
  if (!button) return;
  const visible = state?.kind === 'text';
  button.hidden = !visible;
  button.disabled = false;
  const label = `Find in file (${appShortcutText('F')})`;
  button.title = label;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', codeMirrorSearchPanelOpenForHost(host) ? 'true' : 'false');
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
  const label = !active && loading ? t('editor.diffLoading') : (active ? t('editor.diffExit') : t('editor.diff'));
  syncPressedButton(button, active, {labelOn: label, labelOff: label});
  button.textContent = 'Differ';
}

function updateFileEditorDiffExpandButton(button, path, state, item = null) {
  if (!button) return;
  const activeDiff = editorViewModeFor(path, item) === 'diff';
  button.hidden = state?.kind !== 'text' || !activeDiff || !openFileDiffAvailable(state);
  button.disabled = button.hidden || state?.diffLoading === true;
  button.setAttribute('aria-pressed', diffExpandUnchanged ? 'true' : 'false');
}

async function openEditorFind(host = null) {
  const view = host?._cmView || null;
  const status = host
    ? (msg, level) => setFileEditorPanelStatus(host, msg, level)
    : () => {};
  if (!view) {
    status('Find is available after CodeMirror finishes loading.', 'warn');
    return false;
  }
  try {
    const api = await loadCodeMirrorApi();
    if (openCodeMirrorFindForView(api, view)) {
      syncCodeMirrorFindButtonForView(view);
      return true;
    }
  } catch (error) {
    status(`Find unavailable: ${error}`, 'error');
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
    setFileEditorPanelStatus(host, `Find unavailable: ${error}`, 'error');
  }
  return false;
}

async function toggleEditorFind(host = null) {
  return codeMirrorSearchPanelOpenForHost(host) ? closeEditorFind(host) : openEditorFind(host);
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
      // Re-render each panel with its OWN layout item (DOIT.16 C1): passing the editor item flipped
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
}

// DOIT.26: toggle inline git blame. Fetch the blame payload for each open text file first (so the
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
}

function toggleFileEditorBlame() {
  setFileEditorBlameEnabled(!fileEditorBlameEnabled);
}

function toggleEditorWrap() {
  const enabled = !fileEditorWrapEnabled;
  setEditorWrapEnabled(enabled);
}

// B4 (DOIT.12): toggle showing ALL diff context vs collapsing unchanged regions. Persisted; re-renders
// every open editor in diff mode (the signature includes `expand`, so the diff view rebuilds).
function setDiffExpandUnchanged(enabled) {
  diffExpandUnchanged = enabled === true;
  storageSet('yolomux.diffExpandUnchanged', diffExpandUnchanged ? '1' : '0');
  document.querySelectorAll('.file-editor-panel').forEach(panel => {
    const item = panel.dataset.layoutItem || fileEditorItemFor(panel.dataset.filePath || '');
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    updateFileEditorDiffExpandButton(panel.querySelector('.file-editor-diff-expand-panel'), path, state, item);
    if (path && state?.kind === 'text' && editorViewModeFor(path, item) === 'diff' && openFileDiffAvailable(state)) {
      renderFileEditorPanel(panel, item);
    }
  });
}

function toggleDiffExpandUnchanged() {
  setDiffExpandUnchanged(!diffExpandUnchanged);
}

function setEditorLineNumbersEnabled(enabled) {
  fileEditorLineNumbersEnabled = enabled === true;
  writeStoredEditorLineNumbers(fileEditorLineNumbersEnabled);
  applyEditorWrapPreference();
}

function toggleEditorLineNumbers() {
  const enabled = !fileEditorLineNumbersEnabled;
  setEditorLineNumbersEnabled(enabled);
}

function numberSetting(path, fallback) {
  const value = Number(initialSetting(path, fallback));
  return Number.isFinite(value) ? value : fallback;
}

function fileExplorerRefreshMsFromSettings() {
  return fileExplorerRefreshMsFromValues(
    numberSetting('file_explorer.refresh_seconds', Number.NaN),
    numberSetting('file_explorer.refresh_ms', 15001),
  );
}

function boolSetting(path, fallback) {
  const value = initialSetting(path, fallback);
  return value === true || value === 'true' || value === 1;
}

function normalizeEditorCursorStyle(value) {
  return value === 'block' ? 'block' : 'line';
}

function normalizeEditorCursorColor(value) {
  return value === 'theme' ? 'theme' : 'yellow';
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

// DOIT.41: the appearance.active_color presets. Green is NOT listed — it is the default and maps to the
// CSS token defaults (so picking Green clears the inline overrides = a no-op). Each preset supplies, per
// theme, the solid accent (ring/glow/strip/tints), the tab fill (bright), and the on-accent text. White
// in light mode uses a light-neutral fill with a darker-neutral ring so the active pane still reads.
const ACTIVE_COLOR_PRESETS = {
  blue:   {dark: {accent: '#3b82f6', bright: '#3b82f6', text: '#ffffff'}, light: {accent: '#2563eb', bright: '#2563eb', text: '#ffffff'}},
  orange: {dark: {accent: '#f97316', bright: '#f97316', text: '#1a0c00'}, light: {accent: '#c2570a', bright: '#c2570a', text: '#ffffff'}},
  yellow: {dark: {accent: '#eab308', bright: '#eab308', text: '#1a1500'}, light: {accent: '#a16207', bright: '#a16207', text: '#ffffff'}},
  purple: {dark: {accent: '#a855f7', bright: '#a855f7', text: '#ffffff'}, light: {accent: '#7c3aed', bright: '#7c3aed', text: '#ffffff'}},
  white:  {dark: {accent: '#e8edf2', bright: '#e8edf2', text: '#0b0e14'}, light: {accent: '#9aa5b3', bright: '#dfe5ec', text: '#0b0e14'}},
};

function hexToRgbTriple(hex) {
  const h = String(hex || '').replace('#', '');
  if (h.length !== 6) return '118 185 0';
  return `${parseInt(h.slice(0, 2), 16)} ${parseInt(h.slice(2, 4), 16)} ${parseInt(h.slice(4, 6), 16)}`;
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
    return;
  }
  const light = document.body.classList.contains('theme-resolved-light');
  const p = light ? preset.light : preset.dark;
  const rgb = hexToRgbTriple(p.accent);
  for (const style of styles) {
    style.setProperty('--active-accent', p.accent);
    style.setProperty('--active-accent-rgb', rgb);
    style.setProperty('--active-accent-bright', p.bright);
    style.setProperty('--active-accent-text', p.text);
    style.setProperty('--active-accent-dim', `color-mix(in srgb, ${p.accent} 22%, var(--panel))`);
    style.setProperty('--active-accent-soft', `rgb(${rgb} / 0.12)`);
  }
}

function applyCssSettings() {
  const root = document.documentElement?.style;
  if (!root) return;
  const uiFontSize = numberSetting('appearance.ui_font_size', 13);
  root.setProperty('--ui-font-size', `${uiFontSize}px`);
  root.setProperty('--tab-label-size', `${uiFontSize}px`);
  root.setProperty('--editor-font-size', `${editorFontSize}px`);
  root.setProperty('--editor-preview-font-size', `${editorPreviewFontSize}px`);
  root.setProperty('--file-explorer-font-size', `${fileExplorerFontSize}px`);
  root.setProperty('--pane-tab-width', `${numberSetting('appearance.tab_width', 180)}px`);
  // #261: pane spacing (0-20px) = the gap on each side of the separator AND the width of the active
  // pane's green "border" (which fills its side of that gap up to the line). At 0: no gap, no green —
  // panes sit flush to the 1px separator. The red needs-* attention ring keeps its own constant width
  // (--pane-tab-panel-ring-width, unchanged) so it stays visible even at spacing 0.
  const paneSpacing = Math.max(0, Math.min(20, numberSetting('appearance.pane_spacing', 4)));
  root.setProperty('--pane-split-gap', `${paneSpacing}px`);
  // Opacity (5-100%) of the translucent pane ring. The active ring follows the same setting; otherwise
  // low values appear to save correctly while the visible focused pane still stays prominent.
  const paneRingOpacity = Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75)));
  applyPaneRingOpacity(paneRingOpacity);
  applyInactivePaneOpacity(numberSetting('appearance.inactive_pane_opacity', 60));
  applyActiveColor(initialSetting('appearance.active_color', 'green'));
  root.setProperty('--red-reminder-duration', `${Math.max(0, redReminderMs) / 1000}s`);
  root.setProperty('--yolo-rotation-duration', `${Math.max(0, yoloRotateMs) / 1000}s`);
  root.setProperty('--popover-show-delay', `${popoverShowDelayMs}ms`);
  root.setProperty('--popover-hide-delay', `${popoverHideDelayMs}ms`);
  root.setProperty('--file-image-preview-max-size', `${Math.max(1, fileExplorerImagePreviewMaxPx)}px`);
}

function applyGlobalThemeMode(options = {}) {
  globalThemeMode = normalizeGlobalThemeMode(globalThemeMode);
  const resolved = resolvedGlobalThemeMode();
  document.body?.classList.remove('theme-system', 'theme-dark', 'theme-light', 'theme-resolved-dark', 'theme-resolved-light');
  document.body?.classList.add(`theme-${globalThemeMode}`, `theme-resolved-${resolved}`);
  if (document.documentElement?.style) document.documentElement.style.colorScheme = resolved;
  if (options.updateEditor !== false) applyEditorThemeMode({refreshEditors: options.refreshEditors !== false});
  if (options.updateTerminals) applyTerminalRuntimeSettings({fit: false});
  // DOIT.41: the active-color presets are theme-specific, so re-apply on every theme switch.
  applyActiveColor(initialSetting('appearance.active_color', 'green'));
}

let globalThemeMediaListenerInstalled = false;

function installGlobalThemeMediaListener() {
  if (globalThemeMediaListenerInstalled) return;
  const query = globalThemeMediaQuery();
  if (!query) return;
  const handler = () => {
    if (normalizeGlobalThemeMode(globalThemeMode) !== 'system') return;
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true});
    renderSessionButtons();
    renderPaneTabStrips();
  };
  if (typeof query.addEventListener === 'function') query.addEventListener('change', handler);
  else if (typeof query.addListener === 'function') query.addListener(handler);
  globalThemeMediaListenerInstalled = true;
}

// The ACTIVE pane's terminal gets a blinking yellow cursor so it's obvious which terminal you're
// typing into; every other terminal keeps its theme's default cursor color.
const activeTerminalCursorColor = '#ffd000';

function terminalThemeForSession(session, baseTheme) {
  const theme = baseTheme || terminalThemeForGlobalTheme();
  return session === focusedPanelItem ? {...theme, cursor: activeTerminalCursorColor} : theme;
}

function applyTerminalRuntimeSettings(options = {}) {
  // DOIT.6 #32: one theme source for every terminal AND its container, so all panes share the same
  // white in light mode (no pane-level tint showing a different white); + minimumContrastRatio so
  // faint 24-bit agent output stays legible on white.
  const theme = terminalThemeForGlobalTheme();
  const minContrast = terminalMinimumContrastRatio();
  for (const [session, item] of terminals.entries()) {
    if (!item?.term) continue;
    item.term.options.fontSize = terminalFontSize;
    item.term.options.scrollback = terminalScrollback;
    item.term.options.theme = terminalThemeForSession(session, theme);
    item.term.options.minimumContrastRatio = minContrast;
    if (item.container?.style) item.container.style.background = theme.background;
    if (options.fit !== false) scheduleFit(session);
  }
}

// Lightweight cursor-only refresh for focus changes: re-color just the cursor so the active pane's
// terminal blinks yellow and the rest revert to their theme default, without re-fitting every pane.
function refreshActiveTerminalCursor() {
  const base = terminalThemeForGlobalTheme();
  for (const [session, item] of terminals.entries()) {
    if (!item?.term?.options) continue;
    const cursor = session === focusedPanelItem ? activeTerminalCursorColor : base.cursor;
    const current = item.term.options.theme || base;
    if (current.cursor !== cursor) item.term.options.theme = {...current, cursor};
  }
}

function refreshMetaButtonTitle() {
  if (!refreshMeta) return;
  const seconds = ms => `${Math.round(ms / 1000)}s`;
  refreshMeta.title = [
    'Refresh session state',
    'Re-list tmux sessions.',
    'Refresh git, PR, Linear, and agent metadata.',
    'Refresh YOLO status and open event logs.',
    'Refresh active transcript previews.',
    `Auto-refresh: YOLO ${seconds(paneStateRefreshMs)}, metadata ${seconds(metadataRefreshMs)}, ping ${seconds(latencyRefreshMs)}, open logs ${seconds(eventLogRefreshMs)}.`,
    'Does not reload the page or reconnect terminals.',
  ].join('\n');
}

function applySettingsPayload(payload, options = {}) {
  if (!payload?.settings) return false;
  const nextMtime = Number(payload.mtime_ns || 0);
  if (!options.force && nextMtime && nextMtime === clientSettingsMtimeNs) return false;
  const previousLocale = i18nActiveLocaleId();
  const previousDateTimeHourCycle = dateTimeHourCycle;
  clientSettingsPayload = payload;
  clientSettingsDefaults = payload.defaults || clientSettingsDefaults;
  clientSettings = mergeSettingObjects(clientSettingsDefaults, payload.settings || {});
  clientSettingsMtimeNs = nextMtime;
  remoteResizeDelayMs = numberSetting('performance.remote_resize_delay_ms', 200);
  metadataRefreshMs = numberSetting('performance.metadata_refresh_ms', 15001);
  watchedPrRefreshMs = numberSetting('performance.watched_pr_refresh_ms', 60001);
  paneStateRefreshMs = numberSetting('performance.pane_state_refresh_ms', 1253);
  latencyRefreshMs = numberSetting('performance.latency_refresh_ms', 3001);
  eventLogRefreshMs = numberSetting('performance.event_log_refresh_ms', 5003);
  redReminderMs = numberSetting('appearance.red_reminder_ms', 1550);
  yoloRotateMs = numberSetting('appearance.yolo_rotate_ms', 20000);
  toastDurationMs = numberSetting('notifications.toast_duration_ms', 10000);
  popoverShowDelayMs = numberSetting('performance.popover_show_delay_ms', 1000);
  hoverCloseDelayMs = numberSetting('performance.popover_hide_delay_ms', 300);
  popoverHideDelayMs = hoverCloseDelayMs;
  menuHoverOpenDelayMs = numberSetting('performance.menu_hover_open_delay_ms', 800);
  menuHoverCloseDelayMs = hoverCloseDelayMs;
  tabPopoverShowDelayMs = numberSetting('performance.tab_popover_show_delay_ms', 1000);
  tabPopoverFollowDelayMs = numberSetting('performance.tab_popover_follow_delay_ms', 120);
  fileExplorerRefreshMs = fileExplorerRefreshMsFromSettings();
  fileExplorerIndexRefreshSeconds = numberSetting('file_explorer.index_refresh_seconds', 120);
  fileExplorerNewEntryHighlightMs = numberSetting('file_explorer.new_entry_highlight_ms', 60000);
  fileExplorerImagePreviewMaxPx = numberSetting('file_explorer.image_preview_max_px', 320);
  fileExplorerImageOpenMode = normalizedImageOpenMode(initialSetting('file_explorer.image_open_mode', 'same-tab'));
  reconcileIndexedDirsFromSetting({initial: options.initial === true});
  uploadMaxBytes = numberSetting('uploads.max_bytes', 20 * 1024 * 1024);
  terminalFontSize = numberSetting('appearance.terminal_font_size', 13);
  editorFontSize = numberSetting('appearance.editor_font_size', 13);
  editorPreviewFontSize = numberSetting('appearance.preview_font_size', editorFontSize + 1);
  fileExplorerFontSize = numberSetting('appearance.file_explorer_font_size', 13);
  terminalScrollback = numberSetting('terminal_editor.scrollback', 5000);
  fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
  fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds', 2.5);
  const previousBlameAllLines = fileEditorBlameAllLines;
  fileEditorBlameAllLines = boolSetting('editor.blame_all_lines', false);
  autoFocusEnabled = boolSetting('general.auto_focus', false);
  startupHelpersEnabled = boolSetting('general.startup_tips', true);
  const previousEditorSchemeId = activeEditorScheme().id;
  globalThemeMode = normalizeGlobalThemeMode(initialSetting('appearance.theme', defaultGlobalTheme));
  terminalThemeMode = normalizeTerminalThemeMode(initialSetting('appearance.terminal_theme', defaultTerminalTheme));
  dateTimeHourCycle = normalizeDateTimeHourCycle(initialSetting('appearance.date_time_hour_cycle', '24'));
  fileEditorCursorStyle = normalizeEditorCursorStyle(initialSetting('appearance.editor_cursor_style', 'block'));
  fileEditorCursorColor = normalizeEditorCursorColor(initialSetting('appearance.editor_cursor_color', 'yellow'));
  fileEditorThemeMode = readConfiguredEditorScheme();
  if (options.initial || options.applyEditorDefaults) {
    fileEditorWrapEnabled = boolSetting('terminal_editor.word_wrap', fileEditorWrapEnabled);
    fileEditorLineNumbersEnabled = boolSetting('terminal_editor.line_numbers', fileEditorLineNumbersEnabled);
  }
  fileExplorerRootMode = initialSetting('file_explorer.root_mode', fileExplorerRootMode) === 'sync' ? 'sync' : 'fixed';
  applyCssSettings();
  if (typeof updateEditorPreviewFontControls === 'function') updateEditorPreviewFontControls();
  if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();
  applyGlobalThemeMode({updateEditor: false, updateTerminals: false});
  applyEditorThemeMode({refreshEditors: false});
  applyEditorCursorStyle();
  applyTerminalRuntimeSettings();
  applyEditorWrapPreference();
  renderFileExplorerRootModeControls();
  refreshMetaButtonTitle();
  renderPreferencesPanels();
  renderSessionButtons();
  renderPaneTabStrips();
  rescheduleAllFileAutosaves();
  if (previousDateTimeHourCycle !== dateTimeHourCycle) {
    if (typeof renderFileExplorerChangesPanels === 'function') renderFileExplorerChangesPanels({force: true});
    if (typeof relocalizeFileExplorerPanels === 'function') relocalizeFileExplorerPanels();
  }
  if (previousEditorSchemeId !== activeEditorScheme().id) {
    // DOIT.6: re-theme LIVE editors via the compartment swap (preserves scroll/selection). A plain
    // renderFileEditorPanel short-circuits because codeMirrorConfigSignature omits the scheme, so the
    // CM view would keep its old theme; refreshOpenEditorThemePanels reconfigures the theme directly.
    refreshOpenEditorThemePanels();
  }
  // DOIT.26: the blame ViewPlugin decorates per fileEditorBlameAllLines at build time + the editor
  // config signature carries it, so re-render open editors when the toggle changes (only matters while
  // blame is on).
  if (previousBlameAllLines !== fileEditorBlameAllLines && fileEditorBlameEnabled) applyEditorBlamePreference();
  // i18n (DOIT.8): when general.language changes, load the new catalog and re-render localized surfaces.
  const nextLocale = resolveLocalePref(initialSetting('general.language', 'system'));
  if (nextLocale !== previousLocale) applyLocale(nextLocale);
  if (!options.initial) installRuntimeIntervals();
  return true;
}

async function refreshSettings(options = {}) {
  try {
    const response = await apiFetch('/api/settings', {cache: 'no-store'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    const changed = applySettingsPayload(payload, {force: options.force === true});
    if (changed) refreshYoloRulesStatus({silent: true});
    if (changed && !options.silent) statusEl.textContent = 'settings reloaded';
  } catch (error) {
    if (!options.silent) statusErr(`settings reload failed: ${esc(error)}`);
  }
}

const runtimeIntervals = new Map();

function runtimeJitteredDelay(baseDelay, randomValue = Math.random()) {
  const base = Math.max(1, Math.round(Number(baseDelay) || 1));
  const normalizedRandom = Math.min(1, Math.max(0, Number(randomValue) || 0));
  return Math.max(1, Math.round(base * (1.01 + normalizedRandom * 0.09)));
}

function resetRuntimeInterval(name, callback, delay) {
  const normalizedDelay = Math.max(1, Math.round(Number(delay) || 1));
  const existing = runtimeIntervals.get(name);
  if (existing?.delay === normalizedDelay) {
    existing.callback = callback;
    return;
  }
  if (existing) {
    existing.active = false;
    clearTimeout(existing.timer);
  }
  const state = {active: true, timer: null, delay: normalizedDelay, callback};
  const scheduleNext = () => {
    if (!state.active) return;
    state.timer = setTimeout(run, runtimeJitteredDelay(state.delay));
  };
  const run = () => {
    if (!state.active) return;
    state.callback();
    scheduleNext();
  };
  scheduleNext();
  runtimeIntervals.set(name, state);
}

function clearRuntimeInterval(name) {
  const existing = runtimeIntervals.get(name);
  if (!existing) return;
  existing.active = false;
  clearTimeout(existing.timer);
  runtimeIntervals.delete(name);
}

function installRuntimeIntervals() {
  resetRuntimeInterval('auto', refreshAutoStatuses, paneStateRefreshMs);
  resetRuntimeInterval('metadata', refreshTranscripts, metadataRefreshMs);
  resetRuntimeInterval('watched-prs', refreshWatchedPrs, watchedPrRefreshMs);
  resetRuntimeInterval('latency', updateLatency, latencyRefreshMs);
  resetRuntimeInterval('events', refreshOpenEventLogs, eventLogRefreshMs);
  resetRuntimeInterval('filesystem', refreshWatchedFilesystem, fileExplorerRefreshMs);
  resetRuntimeInterval('settings', () => refreshSettings({silent: true}), Math.max(1000, Math.min(10000, eventLogRefreshMs)));
  if (fileExplorerIndexRefreshSeconds > 0) {
    resetRuntimeInterval('file-index-refresh', refreshAllIndexedDirsStatus, fileExplorerIndexRefreshSeconds * 1000);
  } else {
    clearRuntimeInterval('file-index-refresh');
  }
}

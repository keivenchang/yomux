function editorViewModeKey(path, item = null) {
  return item && isFileEditorItem(item) ? item : path;
}

function editorViewModeFor(path, item = null) {
  if (isFilePreviewItem(item)) return 'preview';
  const mode = fileEditorViewMode.get(editorViewModeKey(path, item)) || fileEditorViewMode.get(path);
  if (mode === 'diff') return 'diff';
  if (!editorPreviewModeAvailable(path)) return 'edit';
  if (editorViewModes.has(mode)) return mode;
  return 'edit';
}

function setFileEditorViewMode(path, mode, item = null) {
  if (!path || !editorViewModes.has(mode)) return;
  if (isFilePreviewItem(item)) mode = 'preview';
  if (mode !== 'edit' && mode !== 'diff' && !editorPreviewModeAvailable(path)) mode = 'edit';
  fileEditorViewMode.set(editorViewModeKey(path, item), mode);
}

function updateEditorModeControl(control, path, state, item = null) {
  if (!control) return;
  const visible = !isFilePreviewItem(item) && state?.kind === 'text' && editorPreviewModeAvailable(path);
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
  const scheme = mode === editorThemeInheritMode ? activeEditorScheme() : (EDITOR_SCHEMES[normalizeEditorSchemeId(mode)] || EDITOR_SCHEMES.dark);
  if (mode === editorThemeInheritMode) return `Inherit global theme (${scheme.label})`;
  return `${scheme.label} editor scheme`;
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
    '--markdown-heading': syntax.heading,
    '--markdown-heading-bg': syntax.headingBg || scheme.panel2,
    '--markdown-link': syntax.link,
    '--markdown-strong': syntax.strong,
    '--markdown-emphasis': syntax.emphasis,
    '--code-keyword': syntax.keyword,
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
    '--lt-markdown-heading': syntax.heading,
    '--lt-markdown-heading-bg': syntax.headingBg || scheme.panel2,
    '--lt-markdown-link': syntax.link,
    '--lt-markdown-strong': syntax.strong,
    '--lt-markdown-emphasis': syntax.emphasis,
    '--lt-code-keyword': syntax.keyword,
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

function updateEditorThemeButton(button) {
  if (!button) return;
  const scheme = activeEditorScheme();
  const nextScheme = EDITOR_SCHEMES[configuredEditorSchemeForMode(!scheme.dark)] || scheme;
  button.classList.toggle('theme-dark', scheme.dark);
  button.classList.toggle('theme-light', !scheme.dark);
  button.dataset.editorTheme = scheme.id;
  button.setAttribute('aria-pressed', scheme.dark ? 'false' : 'true');
  const nextAction = fileEditorThemeMode === editorThemeInheritMode ? `override to ${nextScheme.label}` : 'return to global theme';
  button.title = `${editorThemeLabel()}; ${nextAction}`;
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
}

function applyEditorThemeMode(options = {}) {
  const scheme = activeEditorScheme();
  applyEditorSchemeCssVariables(scheme);
  document.body?.classList.remove('editor-theme-system', 'editor-theme-dark', 'editor-theme-light');
  EDITOR_SCHEME_IDS.forEach(id => document.body?.classList.remove(`editor-scheme-${id}`));
  document.body?.classList.add(scheme.dark ? 'editor-theme-dark' : 'editor-theme-light');
  document.body?.classList.add(`editor-scheme-${scheme.id}`);
  document.querySelectorAll('.file-editor-theme-panel').forEach(updateEditorThemeButton);
  if (options.refreshEditors) refreshOpenEditorThemePanels();
}

function setFileEditorThemeMode(mode) {
  fileEditorThemeMode = normalizeEditorThemeMode(mode);
  writeStoredEditorThemeMode(fileEditorThemeMode);
  applyEditorThemeMode({refreshEditors: true});
}

function cycleEditorThemeMode() {
  if (fileEditorThemeMode === editorThemeInheritMode) {
    setFileEditorThemeMode(configuredEditorSchemeForMode(!activeEditorScheme().dark));
  } else {
    setFileEditorThemeMode(editorThemeInheritMode);
  }
}

function updateEditorWrapButton(button) {
  if (!button) return;
  syncPressedButton(button, fileEditorWrapEnabled, {
    labelOn: 'Disable word wrap',
    labelOff: 'Enable word wrap',
  });
  setFileEditorIcon(button, 'file-editor-icon-wrap');
}

function updateEditorFindButton(button, state) {
  if (!button) return;
  const visible = state?.kind === 'text';
  button.hidden = !visible;
  button.disabled = false;
  const label = `Find in file (${appShortcutText('F')})`;
  button.title = label;
  button.setAttribute('aria-label', label);
  setFileEditorIcon(button, 'file-editor-icon-find');
}

function updateFileEditorDiffButton(button, path, state, item = null) {
  if (!button) return;
  const active = editorViewModeFor(path, item) === 'diff';
  const available = openFileDiffAvailable(state);
  const loading = state?.diffLoading === true;
  // Keep the diff toggle on ANY text file (not just md/html) so .py/.js/.rs can switch edit<->diff.
  // The diff loads lazily on click (refreshOpenFileDiff); only hide the button once a load has
  // confirmed there is nothing to diff (diffLoaded && !available), and never while in diff mode.
  const confirmedNoDiff = state?.diffLoaded === true && !available;
  // Hide the diff toggle for files git doesn't track (untracked / outside any repo): there is no
  // committed version to diff against. A deleted tracked file keeps gitTracked=true so it still shows.
  button.hidden = isFilePreviewItem(item) || state?.kind !== 'text' || state?.gitTracked !== true || (confirmedNoDiff && !active);
  button.disabled = !active && loading;
  const label = !active && loading ? 'Loading diff' : (active ? 'Exit diff' : 'Diff');
  syncPressedButton(button, active, {labelOn: label, labelOff: label});
  setFileEditorIcon(button, 'file-editor-icon-diff');
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
    if (openCodeMirrorFindForView(api, view)) return true;
  } catch (error) {
    status(`Find unavailable: ${error}`, 'error');
  }
  return false;
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
      renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
      // Re-render each panel with its OWN layout item (DOIT.16 C1): passing the editor item flipped
      // a preview/diff pane into an editor on any appearance change (e.g. font-size).
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
    if (blameButton) blameButton.setAttribute('aria-pressed', fileEditorBlameEnabled ? 'true' : 'false');
    const path = panel.dataset.filePath;
    const state = openFiles.get(path);
    if (!path || state?.kind !== 'text') continue;
    if (fileEditorBlameEnabled && !editorBlameByPath.has(path)) await fetchEditorBlame(path);
    renderFileEditorPanel(panel, panel.dataset.layoutItem || fileEditorItemFor(path));
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
    const path = panel.dataset.filePath;
    if (path) renderFileEditorPanel(panel, panel.dataset.layoutItem || fileEditorItemFor(path));
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

function applyCssSettings() {
  const root = document.documentElement?.style;
  if (!root) return;
  const uiFontSize = numberSetting('appearance.ui_font_size', 13);
  root.setProperty('--ui-font-size', `${uiFontSize}px`);
  root.setProperty('--tab-label-size', `${uiFontSize}px`);
  root.setProperty('--editor-font-size', `${editorFontSize}px`);
  root.setProperty('--file-explorer-font-size', `${fileExplorerFontSize}px`);
  root.setProperty('--pane-tab-width', `${numberSetting('appearance.tab_width', 180)}px`);
  // #261: pane spacing (0-20px) = the gap on each side of the separator AND the width of the active
  // pane's green "border" (which fills its side of that gap up to the line). At 0: no gap, no green —
  // panes sit flush to the 1px separator. The red needs-* attention ring keeps its own constant width
  // (--pane-tab-panel-ring-width, unchanged) so it stays visible even at spacing 0.
  const paneSpacing = Math.max(0, Math.min(20, numberSetting('appearance.pane_spacing', 4)));
  root.setProperty('--pane-split-gap', `${paneSpacing}px`);
  // DOIT.20: opacity (20-100%) of the translucent green/red pane ring drawn over the content edge.
  const paneRingOpacity = Math.max(20, Math.min(100, numberSetting('appearance.pane_ring_opacity', 50)));
  root.setProperty('--pane-ring-opacity', `${paneRingOpacity}%`);
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
  clientSettingsPayload = payload;
  clientSettingsDefaults = payload.defaults || clientSettingsDefaults;
  clientSettings = mergeSettingObjects(clientSettingsDefaults, payload.settings || {});
  clientSettingsMtimeNs = nextMtime;
  remoteResizeDelayMs = numberSetting('performance.remote_resize_delay_ms', 220);
  metadataRefreshMs = numberSetting('performance.metadata_refresh_ms', 15000);
  watchedPrRefreshMs = numberSetting('performance.watched_pr_refresh_ms', 60000);
  paneStateRefreshMs = numberSetting('performance.pane_state_refresh_ms', 1250);
  latencyRefreshMs = numberSetting('performance.latency_refresh_ms', 3000);
  eventLogRefreshMs = numberSetting('performance.event_log_refresh_ms', 5000);
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
  fileExplorerRefreshMs = numberSetting('file_explorer.refresh_ms', 3000);
  fileExplorerNewEntryHighlightMs = numberSetting('file_explorer.new_entry_highlight_ms', 60000);
  fileExplorerImagePreviewMaxPx = numberSetting('file_explorer.image_preview_max_px', 320);
  fileExplorerImageOpenMode = normalizedImageOpenMode(initialSetting('file_explorer.image_open_mode', 'same-tab'));
  reconcileIndexedDirsFromSetting({initial: options.initial === true});
  uploadMaxBytes = numberSetting('uploads.max_bytes', 20 * 1024 * 1024);
  terminalFontSize = numberSetting('appearance.terminal_font_size', 13);
  editorFontSize = numberSetting('appearance.editor_font_size', 13);
  fileExplorerFontSize = numberSetting('appearance.file_explorer_font_size', 13);
  terminalScrollback = numberSetting('terminal_editor.scrollback', 5000);
  fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
  fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds', 2.5);
  const previousBlameAllLines = fileEditorBlameAllLines;
  fileEditorBlameAllLines = boolSetting('editor.blame_all_lines', false);
  autoFocusEnabled = boolSetting('general.auto_focus', false);
  const previousEditorSchemeId = activeEditorScheme().id;
  globalThemeMode = normalizeGlobalThemeMode(initialSetting('appearance.theme', defaultGlobalTheme));
  terminalThemeMode = normalizeTerminalThemeMode(initialSetting('appearance.terminal_theme', defaultTerminalTheme));
  fileEditorCursorStyle = normalizeEditorCursorStyle(initialSetting('appearance.editor_cursor_style', 'block'));
  fileEditorCursorColor = normalizeEditorCursorColor(initialSetting('appearance.editor_cursor_color', 'yellow'));
  fileEditorThemeMode = readConfiguredEditorScheme();
  if (options.initial || options.applyEditorDefaults) {
    fileEditorWrapEnabled = boolSetting('terminal_editor.word_wrap', fileEditorWrapEnabled);
    fileEditorLineNumbersEnabled = boolSetting('terminal_editor.line_numbers', fileEditorLineNumbersEnabled);
  }
  fileExplorerRootMode = initialSetting('file_explorer.root_mode', fileExplorerRootMode) === 'sync' ? 'sync' : 'fixed';
  applyCssSettings();
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

function installRuntimeIntervals() {
  resetRuntimeInterval('auto', refreshAutoStatuses, paneStateRefreshMs);
  resetRuntimeInterval('metadata', refreshTranscripts, metadataRefreshMs);
  resetRuntimeInterval('watched-prs', refreshWatchedPrs, watchedPrRefreshMs);
  resetRuntimeInterval('latency', updateLatency, latencyRefreshMs);
  resetRuntimeInterval('events', refreshOpenEventLogs, eventLogRefreshMs);
  resetRuntimeInterval('filesystem', refreshWatchedFilesystem, fileExplorerRefreshMs);
  resetRuntimeInterval('settings', () => refreshSettings({silent: true}), Math.max(1000, Math.min(10000, eventLogRefreshMs)));
}

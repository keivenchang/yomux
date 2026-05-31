const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
const accessRole = bootstrap.accessRole || 'admin';
const readOnlyMode = accessRole !== 'admin';
const homePath = bootstrap.homePath;
const repoRoot = bootstrap.repoRoot || '';
const serverHostname = bootstrap.serverHostname;
const grid = document.getElementById('grid');
const panelPool = document.getElementById('panelPool');
const sessionButtons = document.getElementById('sessionButtons');
const topbar = sessionButtons?.closest?.('.topbar') || null;
const statusEl = document.getElementById('status');
const attentionAlerts = document.getElementById('attentionAlerts');
const latencyMeter = document.getElementById('latencyMeter');
const latencyLine = document.getElementById('latencyLine');
const latencyNumber = document.getElementById('latencyNumber');
const notifyToggle = document.getElementById('notifyToggle');
const refreshMeta = document.getElementById('refreshMeta');
const tabMetaToggle = (() => {
  const button = document.createElement('button');
  button.id = 'tabMetaToggle';
  button.className = 'tab-meta-toggle';
  button.type = 'button';
  button.textContent = '#';
  button.title = 'Hide tab metadata';
  button.setAttribute('aria-label', 'Hide tab metadata');
  button.setAttribute('aria-pressed', 'true');
  return button;
})();
const logoutButton = document.getElementById('logoutButton');
const httpsWarning = document.getElementById('httpsWarning');
const fileExplorer = document.getElementById('fileExplorer');
const fileExplorerTree = document.getElementById('fileExplorerTree');
const fileExplorerPath = document.getElementById('fileExplorerPath');
const fileExplorerPathCopy = document.getElementById('fileExplorerPathCopy');
const fileExplorerClose = document.getElementById('fileExplorerClose');
const fileExplorerHiddenToggle = document.getElementById('fileExplorerHiddenToggle');
const fileExplorerRootModeButton = document.getElementById('fileExplorerRootMode');
const fileExplorerQuickAccess = document.getElementById('fileExplorerQuickAccess');
const fileExplorerExpanded = new Set();
const fileExplorerHiddenStorageKey = 'yolomux.fileExplorer.showHidden';
const fileExplorerRootModeStorageKey = 'yolomux.fileExplorer.rootMode';
const fileEditorWrapStorageKey = 'yolomux.editorWrap';
const fileEditorLineNumbersStorageKey = 'yolomux.editorLineNumbers';
const preferencesCollapsedStorageKey = 'yolomux.preferences.collapsedSections.v1';
const diffRefFromStorageKey = 'yolomux.diffRefFrom';
const diffRefToStorageKey = 'yolomux.diffRefTo';
const editorViewModes = new Set(['edit', 'preview', 'split', 'diff']);
const defaultEditorScheme = 'dark';
const defaultLightEditorScheme = 'yolomux-light';
const EDITOR_SCHEMES = {
  dark: {
    id: 'dark', label: 'YOLOmux Dark', dark: true,
    bg: '#0f1115', fg: '#ffffff', cursor: '#ffffff', selection: 'rgba(118, 185, 0, 0.30)', activeLine: 'rgba(118, 185, 0, 0.10)',
    gutterBg: '#151922', lineNo: '#9aa5b1', panel: '#151922', panel2: '#1e2430', line: '#303948', previewBg: '#151922',
    syntax: {comment: '#8b95a5', keyword: '#c792ea', string: '#86efac', number: '#f8dfa3', function: '#93c5fd', type: '#67e8f9', variable: '#f5f7fb', tag: '#f0abfc', heading: '#76b900', link: '#7ee9ff', inlineCode: '#9aa5b1', inlineCodeBg: 'rgba(154, 165, 177, 0.14)', inlineCodeBorder: 'rgba(154, 165, 177, 0.24)', atom: '#ffd36b', property: '#96d6ff', strong: '#ff5c5c', emphasis: '#ffffff', invalid: '#ff6673'},
    diff: {addFg: '#98c379', removeFg: '#e06c75'},
  },
  'one-dark': {
    id: 'one-dark', label: 'One Dark', dark: true,
    bg: '#282c34', fg: '#abb2bf', cursor: '#528bff', selection: '#3e4451', activeLine: '#2c313c',
    gutterBg: '#282c34', lineNo: '#636d83', panel: '#282c34', panel2: '#2c313c', line: '#3e4451', previewBg: '#30343d',
    syntax: {comment: '#5c6370', keyword: '#c678dd', string: '#98c379', number: '#d19a66', function: '#61afef', type: '#e5c07b', variable: '#e06c75', tag: '#e06c75', heading: '#e06c75', link: '#61afef', inlineCode: '#98c379', inlineCodeBg: 'rgba(152, 195, 121, 0.14)', inlineCodeBorder: 'rgba(152, 195, 121, 0.32)', atom: '#56b6c2', property: '#61afef', strong: '#e5c07b', emphasis: '#d19a66', invalid: '#e06c75'},
    diff: {addFg: '#98c379', removeFg: '#e06c75'},
  },
  dracula: {
    id: 'dracula', label: 'Dracula', dark: true,
    bg: '#282a36', fg: '#f8f8f2', cursor: '#f8f8f0', selection: '#44475a', activeLine: '#44475a',
    gutterBg: '#282a36', lineNo: '#6272a4', panel: '#282a36', panel2: '#343746', line: '#44475a', previewBg: '#333645',
    syntax: {comment: '#6272a4', keyword: '#ff79c6', string: '#f1fa8c', number: '#bd93f9', function: '#50fa7b', type: '#8be9fd', variable: '#f8f8f2', tag: '#ff79c6', heading: '#bd93f9', link: '#8be9fd', inlineCode: '#50fa7b', inlineCodeBg: 'rgba(80, 250, 123, 0.14)', inlineCodeBorder: 'rgba(80, 250, 123, 0.34)', atom: '#bd93f9', property: '#8be9fd', strong: '#f1fa8c', emphasis: '#ffb86c', invalid: '#ff5555'},
    diff: {addFg: '#50fa7b', removeFg: '#ff5555'},
  },
  monokai: {
    id: 'monokai', label: 'Monokai', dark: true,
    bg: '#272822', fg: '#f8f8f2', cursor: '#f8f8f0', selection: '#49483e', activeLine: '#3e3d32',
    gutterBg: '#272822', lineNo: '#90908a', panel: '#272822', panel2: '#34352d', line: '#49483e', previewBg: '#333329',
    syntax: {comment: '#75715e', keyword: '#f92672', string: '#e6db74', number: '#ae81ff', function: '#a6e22e', type: '#66d9ef', variable: '#f8f8f2', tag: '#f92672', heading: '#a6e22e', link: '#66d9ef', inlineCode: '#e6db74', inlineCodeBg: 'rgba(230, 219, 116, 0.14)', inlineCodeBorder: 'rgba(230, 219, 116, 0.32)', atom: '#ae81ff', property: '#66d9ef', strong: '#fd971f', emphasis: '#fd971f', invalid: '#f92672'},
    diff: {addFg: '#a6e22e', removeFg: '#f92672'},
  },
  'vscode-dark-plus': {
    id: 'vscode-dark-plus', label: 'VS Code Dark+', dark: true,
    bg: '#1e1e1e', fg: '#d4d4d4', cursor: '#aeafad', selection: '#264f78', activeLine: '#2a2d2e',
    gutterBg: '#1e1e1e', lineNo: '#858585', panel: '#1e1e1e', panel2: '#252526', line: '#3c3c3c', previewBg: '#252526',
    syntax: {comment: '#6a9955', keyword: '#569cd6', string: '#ce9178', number: '#b5cea8', function: '#dcdcaa', type: '#4ec9b0', variable: '#9cdcfe', tag: '#569cd6', heading: '#4fc1ff', headingBg: '#263342', link: '#3794ff', inlineCode: '#ffb86c', inlineCodeBg: 'rgba(255, 184, 108, 0.16)', inlineCodeBorder: 'rgba(255, 184, 108, 0.36)', atom: '#c586c0', property: '#9cdcfe', strong: '#ffd866', emphasis: '#c586c0', invalid: '#f14c4c'},
    diff: {addFg: '#6a9955', removeFg: '#f14c4c'},
  },
  nord: {
    id: 'nord', label: 'Nord', dark: true,
    bg: '#2e3440', fg: '#d8dee9', cursor: '#d8dee9', selection: '#434c5e', activeLine: '#3b4252',
    gutterBg: '#2e3440', lineNo: '#4c566a', panel: '#2e3440', panel2: '#3b4252', line: '#4c566a', previewBg: '#343b49',
    syntax: {comment: '#616e88', keyword: '#81a1c1', string: '#a3be8c', number: '#b48ead', function: '#88c0d0', type: '#8fbcbb', variable: '#d8dee9', tag: '#81a1c1', heading: '#88c0d0', link: '#88c0d0', inlineCode: '#a3be8c', inlineCodeBg: 'rgba(163, 190, 140, 0.14)', inlineCodeBorder: 'rgba(163, 190, 140, 0.32)', atom: '#b48ead', property: '#8fbcbb', strong: '#ebcb8b', emphasis: '#d08770', invalid: '#bf616a'},
    diff: {addFg: '#a3be8c', removeFg: '#bf616a'},
  },
  'github-light': {
    id: 'github-light', label: 'GitHub Light', dark: false,
    bg: '#ffffff', fg: '#1f2328', cursor: '#0969da', selection: '#0969da33', activeLine: '#f4f6f9',
    gutterBg: '#ffffff', lineNo: '#8c959f', panel: '#f6f8fa', panel2: '#eef2f6', line: '#d0d7de', previewBg: '#fff6df',
    syntax: {comment: '#57606a', keyword: '#cf222e', string: '#116329', number: '#0550ae', function: '#8250df', type: '#953800', variable: '#24292f', tag: '#116329', heading: '#6f42c1', headingBg: '#f1eafe', link: '#0969da', inlineCode: '#a40e26', inlineCodeBg: '#fff1d6', inlineCodeBorder: '#d8a657', atom: '#0550ae', property: '#0969da', strong: '#0f172a', emphasis: '#953800', invalid: '#82071e'},
    diff: {addFg: '#116329', removeFg: '#82071e'},
  },
  'yolomux-light': {
    id: 'yolomux-light', label: 'YOLOmux Light', dark: false,
    bg: '#fbf9f4', fg: '#2b2b2b', cursor: '#14532d', selection: 'rgba(118, 185, 0, 0.24)', activeLine: '#f2ede1',
    gutterBg: '#f4efe6', lineNo: '#6b7280', panel: '#f4efe6', panel2: '#ebe3d4', line: '#d4c7ad', previewBg: '#fff8e8',
    syntax: {comment: '#6b7280', keyword: '#7c3aed', string: '#166534', number: '#b45309', function: '#075985', type: '#0f766e', variable: '#1f2937', tag: '#9f1239', heading: '#14532d', link: '#0369a1', inlineCode: '#6b7280', inlineCodeBg: '#e6dfcf', inlineCodeBorder: '#c8bfa8', atom: '#9d174d', property: '#2563eb', strong: '#c0392b', emphasis: '#2b2b2b', invalid: '#b91c1c'},
    diff: {addFg: '#15803d', removeFg: '#b91c1c'},
  },
  'vscode-light-plus': {
    id: 'vscode-light-plus', label: 'VS Code Light+', dark: false,
    bg: '#ffffff', fg: '#000000', cursor: '#000000', selection: '#add6ff', activeLine: '#f0f0f0',
    gutterBg: '#ffffff', lineNo: '#237893', panel: '#f3f3f3', panel2: '#e9e9e9', line: '#d4d4d4', previewBg: '#fff6df',
    syntax: {comment: '#008000', keyword: '#0000ff', string: '#a31515', number: '#098658', function: '#795e26', type: '#267f99', variable: '#001080', tag: '#800000', heading: '#800000', link: '#0451a5', inlineCode: '#800000', inlineCodeBg: '#fff1d6', inlineCodeBorder: '#e0b45f', atom: '#0000ff', property: '#001080', strong: '#000000', emphasis: '#795e26', invalid: '#a31515'},
    diff: {addFg: '#098658', removeFg: '#a31515'},
  },
  'one-light': {
    id: 'one-light', label: 'One Light', dark: false,
    bg: '#fafafa', fg: '#383a42', cursor: '#526fff', selection: '#e5e5e6', activeLine: '#f0f0f0',
    gutterBg: '#fafafa', lineNo: '#9d9d9f', panel: '#f3f3f3', panel2: '#ececec', line: '#d8d8d8', previewBg: '#fff6df',
    syntax: {comment: '#8a8c93', keyword: '#a626a4', string: '#50a14f', number: '#986801', function: '#4078f2', type: '#c18401', variable: '#e45649', tag: '#e45649', heading: '#e45649', link: '#4078f2', inlineCode: '#50a14f', inlineCodeBg: '#edf7ed', inlineCodeBorder: '#9cd29a', atom: '#986801', property: '#4078f2', strong: '#383a42', emphasis: '#986801', invalid: '#ff1414'},
    diff: {addFg: '#2db448', removeFg: '#ff1414'},
  },
  'solarized-light': {
    id: 'solarized-light', label: 'Solarized Light', dark: false,
    bg: '#fdf6e3', fg: '#657b83', cursor: '#657b83', selection: '#eee8d5', activeLine: '#eee8d5',
    gutterBg: '#eee8d5', lineNo: '#93a1a1', panel: '#f7efd8', panel2: '#eee8d5', line: '#d9d2bd', previewBg: '#f7efd8',
    syntax: {comment: '#93a1a1', keyword: '#859900', string: '#2aa198', number: '#d33682', function: '#268bd2', type: '#b58900', variable: '#268bd2', tag: '#268bd2', heading: '#cb4b16', link: '#268bd2', inlineCode: '#2aa198', inlineCodeBg: '#eee8d5', inlineCodeBorder: '#d9d2bd', atom: '#d33682', property: '#268bd2', strong: '#586e75', emphasis: '#b58900', invalid: '#dc322f'},
    diff: {addFg: '#859900', removeFg: '#dc322f'},
  },
};
const EDITOR_SCHEME_IDS = Object.keys(EDITOR_SCHEMES);
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp']);
const MAX_FILE_PREVIEW_BYTES = 20 * 1024 * 1024;
const HIGHLIGHTABLE_EXTENSIONS = {
  '.md': 'markdown', '.markdown': 'markdown',
  '.html': 'xml', '.htm': 'xml', '.xml': 'xml', '.svg': 'xml',
  '.py': 'python', '.pyw': 'python',
  '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.jsx': 'javascript',
  '.ts': 'typescript', '.tsx': 'typescript',
  '.json': 'json', '.css': 'css', '.scss': 'scss',
  '.rs': 'rust', '.go': 'go', '.c': 'c', '.h': 'c',
  '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.yaml': 'yaml', '.yml': 'yaml',
  '.toml': 'ini', '.ini': 'ini', '.cfg': 'ini',
  '.sql': 'sql', '.rb': 'ruby', '.lua': 'lua', '.pl': 'perl',
};
const openFiles = new Map();  // path -> {mtime, size, kind, original, content, dirty}
const fileEditorTabPaths = new Set();
const filePreviewTabPaths = new Set();
const openFileOwnerSessions = new Map();  // path -> Set<tmux session>
const fileExplorerDirectorySignatures = new Map();
const fileExplorerKnownEntryNames = new Map();
const fileExplorerNewEntryUntil = new Map();
const fileExplorerRepoInfoCache = new Map();
const fileExplorerSessionFilesCache = new Map();
const pendingFileEditorFocus = new Set();
const fileEditorViewState = new Map();  // layout item -> CodeMirror scroll/selection state
let activeFile = null;
let sharedImageViewerPath = null;
let fileExplorerRoot = null;
let filesystemRefreshInFlight = false;
let fileExplorerRootMode = readStoredFileExplorerRootMode();
let fileExplorerShowHidden = (() => {
  try { return window.localStorage?.getItem(fileExplorerHiddenStorageKey) === '1'; }
  catch (_) { return false; }
})();
const fileEditorViewMode = new Map();  // layout item or path -> "edit" | "preview" | "split"
const fileEditorThemeModeStorageKey = 'yolomux.fileEditorThemeMode.v1';
const fileEditorImageMode = new Map();  // path -> "original" when zoomed to natural image size
let fileEditorWrapEnabled = readStoredEditorWrap();
let fileEditorLineNumbersEnabled = readStoredEditorLineNumbers();
let fileEditorThemeMode = readStoredEditorThemeMode();
let fileEditorAutosaveEnabled = false;
let fileEditorAutosaveDelaySeconds = 2.5;
const fileEditorAutosaveTimers = new Map();
const fileEditorConflictDialogs = new Set();
let codeMirrorApiPromise = null;
let codeMirrorBundlePromise = null;
let preferencesSearchText = '';
let preferencesResetConfirmVisible = false;
let preferencesSearchFresh = true;
let collapsedPreferenceSections = readStoredCollapsedPreferenceSections();
let sessionFilesPayload = {session: '', files: [], repos: [], errors: []};
let fileExplorerSessionFilesPayload = {session: '', files: [], repos: [], errors: []};
let sessionFilesPayloadSignature = '';
let fileExplorerSessionFilesPayloadSignature = '';
let sessionFilesLoading = false;
let fileExplorerSessionFilesLoading = false;
let fileExplorerSessionFilesRequestId = 0;
let sessionFilesSortMode = 'mtime';
let sessionFilesSelectedSession = '';
let diffRefFrom = readStoredDiffRef(diffRefFromStorageKey, 'current');
let diffRefTo = readStoredDiffRef(diffRefToStorageKey, 'HEAD');
let fileExplorerChangesDisplayMode = 'detailed';
let commandPaletteNode = null;
let commandPaletteMode = 'command';
let commandPaletteQuery = '';
let commandPaletteIndex = 0;
let commandPaletteItemsCache = [];
const commandPaletteRecentKeys = new Map();
let commandPaletteRecentSequence = 0;
let fileQuickOpenRoot = '';
let fileQuickOpenCandidates = [];
let fileQuickOpenLoading = false;
let fileQuickOpenError = '';
let fileQuickOpenRequestId = 0;
let fileQuickOpenDebounce = null;
let tabsMenuSearchText = '';
let fileExplorerShortcutRestoreSlots = null;
let clientSettingsPayload = bootstrap.settingsPayload || {};
let clientSettings = clientSettingsPayload.settings || {};
let clientSettingsDefaults = clientSettingsPayload.defaults || {};
let clientSettingsMtimeNs = Number(clientSettingsPayload.mtime_ns || 0);
fileEditorThemeMode = readConfiguredEditorScheme();
fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds', 2.5);
let yoloRulesPayload = bootstrap.yoloRulesPayload || {};
const terminals = new Map();
const panelNodes = new Map();
const resizeObservers = new Map();
const transcriptStreams = new Map();
const summaryStreams = new Map();
const autoApproveStates = new Map();
const uploadResultsBySession = new Map();
const uploadCleanupTimers = new Map();
let uploadResultSequence = 0;
const pasteCounters = new Map();
const pasteCountersStorageKey = 'yolomux.pasteCounters.v1';
const pasteLockStorageKey = 'yolomux.pasteUploadLock.v1';
const tabMetaStorageKey = 'yolomux.showTabMeta.v1';
const transcriptPreviewMessages = 200;
let remoteResizeDelayMs = initialSetting('performance.remote_resize_delay_ms', 220);
let metadataRefreshMs = initialSetting('performance.metadata_refresh_ms', 15000);
let paneStateRefreshMs = initialSetting('performance.pane_state_refresh_ms', 1250);
let latencyRefreshMs = initialSetting('performance.latency_refresh_ms', 3000);
let eventLogRefreshMs = initialSetting('performance.event_log_refresh_ms', 5000);
let redReminderMs = initialSetting('appearance.red_reminder_ms', 1550);
let yoloRotateMs = initialSetting('appearance.yolo_rotate_ms', 20000);
const latencySamplesMax = 24;
let toastDurationMs = initialSetting('notifications.toast_duration_ms', 10000);
const toastMaxLines = 3;
const toastMaxLineChars = 180;
let popoverShowDelayMs = initialSetting('performance.popover_show_delay_ms', 1000);
let hoverCloseDelayMs = initialSetting('performance.popover_hide_delay_ms', 300);
let popoverHideDelayMs = hoverCloseDelayMs;
let menuHoverOpenDelayMs = initialSetting('performance.menu_hover_open_delay_ms', 800);
let menuHoverCloseDelayMs = hoverCloseDelayMs;
let tabPopoverShowDelayMs = initialSetting('performance.tab_popover_show_delay_ms', 1000);
let tabPopoverFollowDelayMs = initialSetting('performance.tab_popover_follow_delay_ms', 120);
const fileImagePreviewMinShowDelayMs = 800;
const fileEditorScrollSyncSuppressMs = 150;
let fileExplorerRefreshMs = initialSetting('file_explorer.refresh_ms', 3000);
let fileExplorerNewEntryHighlightMs = initialSetting('file_explorer.new_entry_highlight_ms', 60000);
let fileExplorerImagePreviewMaxPx = initialSetting('file_explorer.image_preview_max_px', 320);
let fileExplorerImageOpenMode = initialSetting('file_explorer.image_open_mode', 'same-tab');
let terminalFontSize = initialSetting('appearance.terminal_font_size', 13);
let editorFontSize = initialSetting('appearance.editor_font_size', 13);
let fileExplorerFontSize = initialSetting('appearance.file_explorer_font_size', 13);
let terminalScrollback = initialSetting('terminal_editor.scrollback', 5000);
let autoFocusEnabled = initialSetting('general.auto_focus', false);
const menuClickCloseGraceMs = 2000;
const terminalFitBottomReservePx = 2;
const terminalWheelPageFraction = 0.85;
const terminalWheelPixelLinePx = 35;
const terminalWheelMaxLinesPerEvent = 12;
const maxSessionTabs = bootstrap.maxSessionTabs;
const basePaneKeys = ['left', 'right'];
const splitPaneKeys = ['leftTop', 'leftBottom', 'rightTop', 'rightBottom'];
const paneKeys = [...basePaneKeys, ...splitPaneKeys];
const layoutTreeKey = '__tree';
const layoutTreeParamPrefix = 'tree:';
const minSplitPaneWidthPx = 320;
const minSplitPaneHeightPx = 220;
const defaultSplitPercent = 50;
const fileExplorerSplitPercent = 22;
const minSplitPercent = 5;
const maxSplitPercent = 95;
const infoItemId = '__info__';
const infoTabLabel = 'Branch Info';
const yosupItemId = '__yosup__';
const yosupTabLabel = "YO'sup";
const fileExplorerItemId = '__files__';
const prefsItemId = '__prefs__';
const changesItemId = '__changes__';
const emptyPaneParam = '__empty_pane__';
const fileEditorItemPrefix = 'file:';
const filePreviewItemPrefix = 'file-preview:';
const imageViewerItemPrefix = 'image:';
function fileEditorItemFor(path) { return fileEditorItemPrefix + path; }
function filePreviewItemFor(path) { return filePreviewItemPrefix + path; }
function imageViewerItemFor(path) { return imageViewerItemPrefix + path; }
const TAB_TYPES = [
  {
    key: 'info',
    id: infoItemId,
    aliases: ['info', infoItemId],
    match: item => item === infoItemId,
    label: () => infoTabLabel,
    shortLabel: () => 'Term',
    terminalTitle: () => `unavailable for ${infoTabLabel}`,
    sortRank: 0,
    param: () => 'info',
    detail: () => 'Repo metadata, branches, PRs, and CI',
    rowHtml: (item, options) => paneInfoTabHtml(item, options),
    createPanel: () => createInfoPanel(),
    className: () => 'info',
    icon: 'branch-info',
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 0,
  },
  {
    key: 'yosup',
    id: yosupItemId,
    aliases: ['yosup', 'yo', 'sup', yosupItemId],
    match: item => item === yosupItemId,
    label: () => yosupTabLabel,
    shortLabel: () => 'YO',
    terminalTitle: () => `unavailable for ${yosupTabLabel}`,
    sortRank: 0.25,
    param: () => 'yosup',
    detail: () => 'Casual AI activity summary',
    rowHtml: (item, options) => paneInfoTabHtml(item, options),
    createPanel: () => createYosupPanel(),
    className: () => 'info yosup-item',
    icon: 'yosup',
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 0,
  },
  {
    key: 'files',
    id: fileExplorerItemId,
    aliases: ['files', fileExplorerItemId],
    match: item => item === fileExplorerItemId,
    label: () => fileExplorerLabel(),
    shortLabel: () => fileExplorerLabel(),
    terminalTitle: () => `unavailable for ${fileExplorerLabel()}`,
    sortRank: 0.5,
    param: () => 'files',
    detail: () => compactHomePath(fileExplorerRoot || homePath || '/'),
    rowHtml: (item, options) => fileExplorerPaneTabHtml(item, options),
    createPanel: () => createFileExplorerPanel(),
    className: () => 'file-explorer',
    icon: 'finder',
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 0,
  },
  {
    key: 'preferences',
    id: prefsItemId,
    aliases: ['prefs', 'preferences', prefsItemId],
    match: item => item === prefsItemId,
    label: () => 'Preferences',
    shortLabel: () => 'Prefs',
    terminalTitle: () => 'unavailable for Preferences',
    sortRank: 0.65,
    param: () => 'prefs',
    detail: () => compactHomePath(settingsConfigPath()),
    rowHtml: (item, options) => preferencesPaneTabHtml(item, options),
    createPanel: () => createPreferencesPanel(),
    className: () => 'preferences-item',
    icon: 'gear',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 0,
  },
  {
    key: 'changes',
    id: changesItemId,
    aliases: ['changes', changesItemId],
    match: item => item === changesItemId,
    label: () => 'Changes',
    shortLabel: () => 'Changes',
    terminalTitle: () => 'unavailable for Changes',
    sortRank: 0.7,
    param: () => 'changes',
    detail: () => changesTabDetail(),
    rowHtml: (item, options) => changesPaneTabHtml(item, options),
    createPanel: () => createChangesPanel(),
    className: () => 'changes-item',
    icon: 'changes',
    minWidth: () => rootCssLengthPx('--changes-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 0,
  },
  {
    key: 'image-viewer',
    prefix: imageViewerItemPrefix,
    match: item => typeof item === 'string' && item.startsWith(imageViewerItemPrefix),
    label: item => basenameOf(fileItemPath(item)),
    shortLabel: () => 'Image',
    terminalTitle: () => 'unavailable for image viewer',
    sortRank: 0.74,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    className: () => 'file-editor-item image-viewer-item',
    icon: 'document',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 1,
  },
  {
    key: 'file-editor',
    prefix: fileEditorItemPrefix,
    match: item => typeof item === 'string' && item.startsWith(fileEditorItemPrefix),
    label: item => basenameOf(fileItemPath(item)),
    shortLabel: () => 'Edit',
    terminalTitle: () => 'unavailable for file editor',
    sortRank: 0.75,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    className: () => 'file-editor-item',
    icon: 'document',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 1,
  },
  {
    key: 'file-preview',
    prefix: filePreviewItemPrefix,
    match: item => typeof item === 'string' && item.startsWith(filePreviewItemPrefix),
    label: item => basenameOf(fileItemPath(item)),
    shortLabel: () => 'Preview',
    terminalTitle: () => 'unavailable for file preview',
    sortRank: 0.76,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    className: () => 'file-editor-item file-preview-item',
    icon: 'document',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx,
    prunePriority: () => 1,
  },
];
function tabTypeForItem(item) { return TAB_TYPES.find(type => type.match(item)) || null; }
function tabTypeForParam(value) {
  const text = String(value || '');
  return TAB_TYPES.find(type => (type.aliases || []).includes(text) || (type.prefix && text.startsWith(type.prefix))) || null;
}
function tabTypeParam(type, item) { return typeof type?.param === 'function' ? type.param(item) : type?.param; }
function isFileExplorerItem(item) { return tabTypeForItem(item)?.key === 'files'; }
function isPreferencesItem(item) { return tabTypeForItem(item)?.key === 'preferences'; }
function isChangesItem(item) { return tabTypeForItem(item)?.key === 'changes'; }
function isImageViewerItem(item) { return tabTypeForItem(item)?.key === 'image-viewer'; }
function isFilePreviewItem(item) { return tabTypeForItem(item)?.key === 'file-preview'; }
function isFileEditorItem(item) {
  const key = tabTypeForItem(item)?.key;
  return key === 'file-editor' || key === 'image-viewer' || key === 'file-preview';
}
function fileItemPath(item) {
  if (isImageViewerItem(item)) return item.slice(imageViewerItemPrefix.length);
  if (isFilePreviewItem(item)) return item.slice(filePreviewItemPrefix.length);
  return tabTypeForItem(item)?.key === 'file-editor' ? item.slice(fileEditorItemPrefix.length) : null;
}
function normalizedImageOpenMode(mode = fileExplorerImageOpenMode) {
  return mode === 'new-tab' ? 'new-tab' : 'same-tab';
}
function imageOpenUsesSharedViewer(options = {}) {
  return normalizedImageOpenMode() === 'same-tab'
    && options.forceNewTab !== true
    && !options.targetSlot;
}
function browserPlatformText() {
  if (typeof navigator === 'undefined') return '';
  return [
    navigator.userAgentData?.platform,
    navigator.platform,
    navigator.userAgent,
  ].filter(Boolean).join(' ');
}

const platformOverrideParamNames = ['platform', 'uiPlatform', 'ui_platform'];
const pcPlatformOverrideValues = new Set(['pc', 'win', 'windows', 'linux']);
const macPlatformOverrideValues = new Set(['mac', 'macos', 'darwin']);
const platformWindowControlClasses = {
  pc: {
    close: 'pc-window-control pc-close',
    minimize: 'pc-window-control pc-minimize',
    zoom: 'pc-window-control pc-zoom',
  },
};

function platformOverride() {
  const params = new URLSearchParams(location.search || '');
  const value = String(platformOverrideParamNames.map(name => params.get(name)).find(Boolean) || '').toLowerCase();
  if (pcPlatformOverrideValues.has(value)) return 'pc';
  if (macPlatformOverrideValues.has(value)) return 'mac';
  return '';
}

function isMacPlatform() {
  const override = platformOverride();
  if (override) return override === 'mac';
  return /(Macintosh|MacIntel|Mac OS|macOS|\bMac\b)/i.test(browserPlatformText());
}

function appModifier(event) {
  if (!event || event.altKey) return false;
  return isMacPlatform()
    ? event.metaKey === true && event.ctrlKey !== true
    : event.ctrlKey === true && event.metaKey !== true;
}

function appShortcutModifierLabel() {
  return isMacPlatform() ? '⌘' : 'Ctrl';
}

function appShortcutText(key, options = {}) {
  return `${options.shift ? 'Shift+' : ''}${appShortcutModifierLabel()}+${key}`;
}

function platformWindowControlClass(kind) {
  const classes = platformWindowControlClasses.pc;
  return classes[kind] || classes.minimize;
}

function platformCloseButtonClass(baseClass) {
  return `${baseClass} ${platformWindowControlClass('close')}`;
}

function fileExplorerPanelCloseClass() {
  return platformCloseButtonClass('file-explorer-panel-close');
}

function fileEditorPanelCloseClass() {
  return platformCloseButtonClass('file-editor-panel-close');
}

function applyPlatformControlClass(element, kind) {
  if (!element) return;
  element.classList.add(...platformWindowControlClass(kind).split(' '));
}

function fileExplorerLabel() {
  return isMacPlatform() ? 'Finder' : 'File Explorer';
}

function applyFileExplorerStaticLabels() {
  const label = fileExplorerLabel();
  fileExplorer?.setAttribute('aria-label', label);
  fileExplorerClose?.setAttribute('title', `Close ${label}`);
  applyPlatformControlClass(fileExplorerClose, 'close');
}
const syntaxLanguageByExtension = new Map(Object.entries(HIGHLIGHTABLE_EXTENSIONS));
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [infoItemId, yosupItemId, fileExplorerItemId, prefsItemId, ...visibleSessions];
let layoutSlots = initialLayoutSlots();
let activeSessions = sessionsFromLayout();
let transcriptMeta = {};
let activitySummaryPayload = {sessions: {}, global: {lines: []}, session_order: []};
let notificationsEnabled = false;
const sessionStateKeys = new Map();
const notificationLastSent = new Map();
const attentionAlertTimers = new Map();
const metadataBadgePulseUntil = new Map();
let infoBranchSort = {key: 'updated', dir: 'desc'};
let attentionAlertSequence = 0;
let stateTrackingReady = false;
let focusedTerminal = null;
let focusedPanelItem = null;
let lastFocusedTmuxSession = null;
let dragSession = null;
let dragSourceSlot = null;
let dragFilePayloadState = null;
let customDragPreview = null;
let customDragPreviewOffset = {x: 0, y: 0};
let transparentDragImage = null;
const terminalContextMenu = createContextMenuController();
const fileContextMenu = createContextMenuController();
const sessionContextMenu = createContextMenuController();
let sessionRenameDialog = null;
const fileExplorerSelectedPaths = new Set();
let fileExplorerSelectionAnchor = null;
let fileExplorerManualSelectionActive = false;
let fileTreeRenamePath = null;
let fileExplorerPathError = '';
let fileExplorerLastListError = null;
let fileImagePreviewPopover = null;
let fileImagePreviewController = null;
let fileExplorerInteractionGeneration = 0;
let clipboardPasteBound = false;
let pasteUploadInFlight = false;
let layoutResizeState = null;
let responsiveLayoutPruneTimer = null;
let topbarResizeObserver = null;
let latencySamples = [];
let tabMetaVisible = readStoredTabMetaVisible();
let authRedirectStarted = false;
let openAppMenuId = null;
let openAppMenuPinned = false;
let openAppMenuOpenedAt = 0;
let fileExplorerSyncPathInFlight = '';
let fileExplorerSyncGeneration = 0;

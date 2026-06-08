const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
// The exact launch command per agent (with --dangerously-* flags in YOLO mode) for the new-session menu.
const agentLaunchCommands = bootstrap.agentLaunchCommands || {};
// DOIT.6 #39: per-agent {installed, logged_in} login status (probed + cached server-side). Used to
// grey an installed-but-logged-out agent in the new-session picker. Refreshed by metadata polls.
let agentAuth = bootstrap.agentAuth || {};
const agentLoginCommands = {claude: 'claude auth login', codex: 'codex login'};
function agentLoggedIn(agent) {
  const entry = agentAuth[agent];
  // Unknown (term, or no status yet) counts as logged-in so we never block a usable agent.
  return !entry || !entry.installed || entry.logged_in === true;
}
function agentLoginCommand(agent) {
  return agentLoginCommands[agent] || '';
}
const accessRole = bootstrap.accessRole || 'admin';
const readOnlyMode = accessRole !== 'admin';
const devMode = bootstrap.dev === true;   // dev-velocity #1b: subscribe to /api/dev-reload + auto-reload
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
const fileExplorerTreeShowDatesStorageKey = 'yolomux.fileExplorer.treeShowDates.v1';
const fileExplorerTreeDateModeStorageKey = 'yolomux.fileExplorer.treeDateMode.v1';
const fileExplorerTreeDateModes = ['none', 'date', 'relative'];
const fileExplorerTreeSortStorageKey = 'yolomux.fileExplorer.treeSort.v1';
const fileExplorerRepoInfoStorageKey = 'yolomux.fileExplorer.repoInfo.v1';
const fileExplorerIndexedDirsStorageKey = 'yolomux.fileExplorer.indexedDirs.v1';
const fileExplorerIndexedDirsMigratedKey = 'yolomux.fileExplorer.indexedDirs.migrated.v1';  // C11 #3
const fileExplorerModeStorageKey = 'yolomux.fileExplorerMode.v1';
const legacyFileExplorerChangesHiddenStorageKey = 'yolomux.fileExplorerChangesHidden';
const uploadedFilesCollapsedStorageKey = 'yolomux.modifiedFiles.uploadedCollapsed.v1';
const changesFolderCollapsedStorageKey = 'yolomux.modifiedFiles.folderCollapsed.v1';
const changesRepoCollapsedStorageKey = 'yolomux.modifiedFiles.repoCollapsed.v1';
const fileEditorWrapStorageKey = 'yolomux.editorWrap';
const fileEditorLineNumbersStorageKey = 'yolomux.editorLineNumbers';
const preferencesCollapsedStorageKey = 'yolomux.preferences.collapsedSections.v1';
const diffRefFromStorageKey = 'yolomux.diffRefFrom';
const diffRefToStorageKey = 'yolomux.diffRefTo';
const diffRefsByRepoStorageKey = 'yolomux.diffRefsByRepo.v1';  // C6: per-repo {repo: {from,to}} overrides
const editorViewModes = new Set(['edit', 'preview', 'split', 'diff']);
const defaultGlobalTheme = 'dark';
const defaultTerminalTheme = 'follow-app';
const defaultEditorScheme = 'dark';
const defaultLightEditorScheme = 'yolomux-light';
const editorThemeInheritMode = 'inherit';
const TERMINAL_THEMES = {
  dark: {
    background: '#11151d',
    foreground: '#dfe6ef',
    cursor: '#f5f7fb',
    cursorAccent: '#11151d',
    selectionBackground: '#2563eb',
    selectionForeground: '#ffffff',
    black: '#0f1115',
    red: '#ff6673',
    green: '#76b900',
    yellow: '#f5c542',
    blue: '#70a7ff',
    magenta: '#d8a3ff',
    cyan: '#7ee9ff',
    white: '#e4e8ee',
    brightBlack: '#667286',
    brightRed: '#ff8a94',
    brightGreen: '#9be33d',
    brightYellow: '#ffe08a',
    brightBlue: '#93c5fd',
    brightMagenta: '#f0abfc',
    brightCyan: '#a5f3fc',
    brightWhite: '#ffffff',
  },
  light: {
    background: '#ffffff',
    foreground: '#111827',
    cursor: '#0f172a',
    cursorAccent: '#ffffff',
    selectionBackground: '#93c5fd',
    selectionForeground: '#071327',
    black: '#1f2328',
    red: '#a31515',
    green: '#008000',
    yellow: '#795e26',
    blue: '#0451a5',
    magenta: '#af00db',
    cyan: '#267f99',
    white: '#e5e7eb',
    brightBlack: '#57606a',
    brightRed: '#c42b1c',
    brightGreen: '#16825d',
    brightYellow: '#9a6700',
    brightBlue: '#0969da',
    brightMagenta: '#8250df',
    brightCyan: '#0e7490',
    brightWhite: '#ffffff',
  },
};
const EDITOR_SCHEMES = {
  dark: {
    id: 'dark', label: 'YOLOmux Dark', dark: true,
    bg: '#0f1115', fg: '#cfd3dc', cursor: '#ffffff', selection: 'rgba(96, 165, 250, 0.38)', activeLine: 'rgba(255, 255, 255, 0.04)',
    gutterBg: '#151922', lineNo: '#9aa5b1', panel: '#151922', panel2: '#1e2430', line: '#303948', previewBg: '#151922',
    syntax: {comment: '#8b95a5', keyword: '#c792ea', string: '#86efac', number: '#f8dfa3', function: '#93c5fd', type: '#67e8f9', variable: '#f5f7fb', tag: '#f0abfc', heading: '#76b900', link: '#7ee9ff', inlineCode: '#9aa5b1', inlineCodeBg: 'rgba(154, 165, 177, 0.14)', inlineCodeBorder: 'rgba(154, 165, 177, 0.24)', atom: '#ffd36b', property: '#96d6ff', strong: '#ffffff', emphasis: '#ffffff', invalid: '#ff6673'},
    diff: {addFg: '#56d364', removeFg: '#ff7b72'},
  },
  'one-dark': {
    id: 'one-dark', label: 'One Dark', dark: true,
    bg: '#282c34', fg: '#abb2bf', cursor: '#528bff', selection: 'rgba(96, 165, 250, 0.38)', activeLine: '#2c313c',
    gutterBg: '#282c34', lineNo: '#636d83', panel: '#282c34', panel2: '#2c313c', line: '#3e4451', previewBg: '#30343d',
    syntax: {comment: '#5c6370', keyword: '#c678dd', string: '#98c379', number: '#d19a66', function: '#61afef', type: '#e5c07b', variable: '#e06c75', tag: '#e06c75', heading: '#e06c75', link: '#61afef', inlineCode: '#98c379', inlineCodeBg: 'rgba(152, 195, 121, 0.14)', inlineCodeBorder: 'rgba(152, 195, 121, 0.32)', atom: '#56b6c2', property: '#61afef', strong: '#e5c07b', emphasis: '#d19a66', invalid: '#e06c75'},
    diff: {addFg: '#98c379', removeFg: '#e06c75'},
  },
  dracula: {
    id: 'dracula', label: 'Dracula', dark: true,
    bg: '#282a36', fg: '#f8f8f2', cursor: '#f8f8f0', selection: 'rgba(96, 165, 250, 0.38)', activeLine: '#44475a',
    gutterBg: '#282a36', lineNo: '#6272a4', panel: '#282a36', panel2: '#343746', line: '#44475a', previewBg: '#333645',
    syntax: {comment: '#6272a4', keyword: '#ff79c6', string: '#f1fa8c', number: '#bd93f9', function: '#50fa7b', type: '#8be9fd', variable: '#f8f8f2', tag: '#ff79c6', heading: '#bd93f9', link: '#8be9fd', inlineCode: '#50fa7b', inlineCodeBg: 'rgba(80, 250, 123, 0.14)', inlineCodeBorder: 'rgba(80, 250, 123, 0.34)', atom: '#bd93f9', property: '#8be9fd', strong: '#f1fa8c', emphasis: '#ffb86c', invalid: '#ff5555'},
    diff: {addFg: '#50fa7b', removeFg: '#ff5555'},
  },
  monokai: {
    id: 'monokai', label: 'Monokai', dark: true,
    bg: '#272822', fg: '#f8f8f2', cursor: '#f8f8f0', selection: 'rgba(96, 165, 250, 0.38)', activeLine: '#3e3d32',
    gutterBg: '#272822', lineNo: '#90908a', panel: '#272822', panel2: '#34352d', line: '#49483e', previewBg: '#333329',
    syntax: {comment: '#75715e', keyword: '#f92672', string: '#e6db74', number: '#ae81ff', function: '#a6e22e', type: '#66d9ef', variable: '#f8f8f2', tag: '#f92672', heading: '#a6e22e', link: '#66d9ef', inlineCode: '#e6db74', inlineCodeBg: 'rgba(230, 219, 116, 0.14)', inlineCodeBorder: 'rgba(230, 219, 116, 0.32)', atom: '#ae81ff', property: '#66d9ef', strong: '#fd971f', emphasis: '#fd971f', invalid: '#f92672'},
    diff: {addFg: '#a6e22e', removeFg: '#f92672'},
  },
  'vscode-dark-plus': {
    id: 'vscode-dark-plus', label: 'VS Code Dark+', dark: true,
    bg: '#1e1e1e', fg: '#d4d4d4', cursor: '#aeafad', selection: 'rgba(96, 165, 250, 0.38)', activeLine: '#2a2d2e',
    gutterBg: '#1e1e1e', lineNo: '#858585', panel: '#1e1e1e', panel2: '#252526', line: '#3c3c3c', previewBg: '#252526',
    syntax: {comment: '#6a9955', keyword: '#569cd6', string: '#ce9178', number: '#b5cea8', function: '#dcdcaa', type: '#4ec9b0', variable: '#9cdcfe', tag: '#569cd6', heading: '#4fc1ff', headingBg: '#263342', link: '#3794ff', inlineCode: '#ffb86c', inlineCodeBg: 'rgba(255, 184, 108, 0.16)', inlineCodeBorder: 'rgba(255, 184, 108, 0.36)', atom: '#c586c0', property: '#9cdcfe', strong: '#ffd866', emphasis: '#c586c0', invalid: '#f14c4c'},
    diff: {addFg: '#6a9955', removeFg: '#f14c4c'},
  },
  nord: {
    id: 'nord', label: 'Nord', dark: true,
    bg: '#2e3440', fg: '#d8dee9', cursor: '#d8dee9', selection: 'rgba(96, 165, 250, 0.38)', activeLine: '#3b4252',
    gutterBg: '#2e3440', lineNo: '#4c566a', panel: '#2e3440', panel2: '#3b4252', line: '#4c566a', previewBg: '#343b49',
    syntax: {comment: '#616e88', keyword: '#81a1c1', string: '#a3be8c', number: '#b48ead', function: '#88c0d0', type: '#8fbcbb', variable: '#d8dee9', tag: '#81a1c1', heading: '#88c0d0', link: '#88c0d0', inlineCode: '#a3be8c', inlineCodeBg: 'rgba(163, 190, 140, 0.14)', inlineCodeBorder: 'rgba(163, 190, 140, 0.32)', atom: '#b48ead', property: '#8fbcbb', strong: '#ebcb8b', emphasis: '#d08770', invalid: '#bf616a'},
    diff: {addFg: '#a3be8c', removeFg: '#bf616a'},
  },
  'github-light': {
    id: 'github-light', label: 'GitHub Light', dark: false,
    bg: '#ffffff', fg: '#1f2328', cursor: '#0969da', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#f4f6f9',
    gutterBg: '#ffffff', lineNo: '#8c959f', panel: '#f6f8fa', panel2: '#eef2f6', line: '#d0d7de', previewBg: '#fff6df',
    syntax: {comment: '#57606a', keyword: '#cf222e', string: '#116329', number: '#0550ae', function: '#8250df', type: '#953800', variable: '#24292f', tag: '#116329', heading: '#6f42c1', headingBg: '#f1eafe', link: '#0969da', inlineCode: '#a40e26', inlineCodeBg: '#fff1d6', inlineCodeBorder: '#d8a657', atom: '#0550ae', property: '#0969da', strong: '#0f172a', emphasis: '#953800', invalid: '#82071e'},
    diff: {addFg: '#116329', removeFg: '#82071e'},
  },
  'yolomux-light': {
    id: 'yolomux-light', label: 'YOLOmux Light', dark: false,
    bg: '#ffffff', fg: '#1f2937', cursor: '#0f3d22', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#f4f7fb',
    gutterBg: '#f6f8fa', lineNo: '#64748b', panel: '#f6f8fa', panel2: '#eef2f7', line: '#d0d7de', previewBg: '#ffffff',
    syntax: {comment: '#64748b', keyword: '#6d28d9', string: '#00843d', number: '#a16207', function: '#075985', type: '#0f766e', variable: '#1f2937', tag: '#9f1239', heading: '#0f3d22', headingBg: '#ffffff', link: '#075985', inlineCode: '#0f4c81', inlineCodeBg: '#eef6ff', inlineCodeBorder: '#8ab4f8', atom: '#9d174d', property: '#1d4ed8', strong: '#a11b1b', emphasis: '#2b2b2b', invalid: '#b91c1c'},
    diff: {addFg: '#15803d', removeFg: '#b91c1c'},
  },
  'vscode-light-plus': {
    id: 'vscode-light-plus', label: 'VS Code Light+', dark: false,
    bg: '#ffffff', fg: '#1f1f1f', cursor: '#000000', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#f5f5f5',
    gutterBg: '#ffffff', lineNo: '#6e7681', panel: '#f3f3f3', panel2: '#e9e9e9', line: '#d4d4d4', previewBg: '#ffffff',
    syntax: {comment: '#008000', keyword: '#0000ff', control: '#af00db', string: '#a31515', number: '#098658', function: '#795e26', type: '#267f99', variable: '#1f1f1f', tag: '#800000', heading: '#800000', link: '#0451a5', inlineCode: '#800000', inlineCodeBg: '#fff1d6', inlineCodeBorder: '#e0b45f', atom: '#0000ff', property: '#001080', strong: '#000000', emphasis: '#795e26', invalid: '#a31515'},
    diff: {addFg: '#098658', removeFg: '#a31515'},
  },
  'one-light': {
    id: 'one-light', label: 'One Light', dark: false,
    bg: '#fafafa', fg: '#383a42', cursor: '#526fff', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#f0f0f0',
    gutterBg: '#fafafa', lineNo: '#9d9d9f', panel: '#f3f3f3', panel2: '#ececec', line: '#d8d8d8', previewBg: '#fff6df',
    syntax: {comment: '#8a8c93', keyword: '#a626a4', string: '#50a14f', number: '#986801', function: '#4078f2', type: '#c18401', variable: '#e45649', tag: '#e45649', heading: '#e45649', link: '#4078f2', inlineCode: '#50a14f', inlineCodeBg: '#edf7ed', inlineCodeBorder: '#9cd29a', atom: '#986801', property: '#4078f2', strong: '#383a42', emphasis: '#986801', invalid: '#ff1414'},
    diff: {addFg: '#2db448', removeFg: '#ff1414'},
  },
  'solarized-light': {
    id: 'solarized-light', label: 'Solarized Light', dark: false,
    bg: '#fdf6e3', fg: '#657b83', cursor: '#657b83', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#eee8d5',
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
const fileState = new Map();  // path -> open-file content plus editor tab/preview/owner/mode/blame state
const openFiles = fileState;  // compatibility alias during the file-state migration
const fileExplorerDirectorySignatures = new Map();
const fileExplorerKnownEntryNames = new Map();
const fileExplorerNewEntryUntil = new Map();
const fileExplorerRepoInfoCache = new Map();
const fileExplorerSessionFilesCache = new Map();
const fileExplorerMemoryCacheLimit = 512;
const fileExplorerRefreshIdleMs = 1500;
const commandPaletteRecentKeyLimit = 100;
const notificationLastSentLimit = 512;
const pendingFileEditorFocus = new Set();
const fileEditorViewState = new Map();  // layout item -> CodeMirror scroll/selection state
let activeFile = null;
let sharedImageViewerPath = null;
let fileExplorerRoot = null;
let filesystemRefreshInFlight = false;
let fileExplorerRepoInfoCacheLoaded = false;
let fileExplorerRootMode = readStoredFileExplorerRootMode();
let fileExplorerShowHidden = storageGet(fileExplorerHiddenStorageKey) === '1';
const fileEditorThemeModeStorageKey = 'yolomux.fileEditorThemeMode.v1';
let fileEditorWrapEnabled = readStoredEditorWrap();
// DOIT.26: inline git blame (Cursor-style). Persisted toggle + a per-path cache of the /api/blame payload.
let fileEditorBlameEnabled = storageGet('yolomux.editorBlame') === '1';
const editorBlameFetches = new Map();  // DOIT.34 #3: in-flight /api/blame fetch per path (dedup concurrent panels)
let fileEditorBlameAllLines = false;  // DOIT.26: annotate every line vs current-line only (set from settings in applySettingsPayload)
let fileEditorLineNumbersEnabled = readStoredEditorLineNumbers();
// B4 (DOIT.12): when true the diff shows ALL context (no collapsed "N unchanged lines" folds). Persisted.
let diffExpandUnchanged = storageGet('yolomux.diffExpandUnchanged') === '1';
let fileEditorThemeMode = readStoredEditorThemeMode();
let fileEditorCursorStyle = 'block';  // C3: default caret is block; saved 'line' choices round-trip via settings
let fileEditorCursorColor = 'yellow';  // 'yellow' (match the active terminal cursor) | 'theme' (per-scheme caret)
let fileEditorAutosaveEnabled = false;
let fileEditorAutosaveDelaySeconds = 2.5;
const fileEditorAutosaveTimers = new Map();
let codeMirrorApiPromise = null;
let codeMirrorBundlePromise = null;
let preferencesSearchText = '';
let preferencesResetConfirmVisible = false;
const preferencesScrollRenderDeferMs = 200;
let preferencesScrollActiveUntil = 0;
let preferencesScrollFlushTimer = null;
let collapsedPreferenceSections = readStoredCollapsedPreferenceSections();
let uploadedFilesCollapsed = (() => {
  try {
    const value = window.localStorage?.getItem(uploadedFilesCollapsedStorageKey);
    return value == null ? true : value !== '0';
  } catch (_) {
    return true;
  }
})();
let changesFolderCollapsed = readStoredSet(changesFolderCollapsedStorageKey);
// DOIT.23: per-repo collapse state for the Modified-files panel repo headers (keyed by repo path).
let changesRepoCollapsed = readStoredSet(changesRepoCollapsedStorageKey);
let fileExplorerSessionFilesPayload = {session: '', files: [], repos: [], errors: []};
let fileExplorerSessionFilesPayloadSignature = '';
let fileExplorerSessionFilesLoading = false;
let fileExplorerSessionFilesRequestId = 0;
let fileExplorerExplicitSyncSession = '';
const fileExplorerExpandedBySyncTarget = new Map();
let fileExplorerSyncManualCollapseTargetKey = '';
const fileExplorerSyncManualCollapsedByTarget = new Map();
let fileExplorerSyncManualCollapsedPaths = new Set();
let fileExplorerVisibleSyncSession = '';
let fileExplorerVisibleSyncRoot = '';
let fileExplorerLastInteractionAt = 0;
let fileExplorerRefreshDeferred = false;
let sessionFilesSortMode = 'newest';
let changesSelectedPath = '';  // C5: the currently highlighted Modified-files row (persists across re-renders)
let fileExplorerTreeDateMode = readStoredFileExplorerTreeDateMode();
let fileExplorerTreeSortMode = readStoredFileExplorerTreeSortMode();
let fileExplorerIndexedDirs = readStoredFileExplorerIndexedDirs();
const fileExplorerIndexStatus = new Map();  // normalized indexed root -> 'building' | 'ready'
const fileIndexStatusTimers = new Map();  // normalized indexed root -> poll timer while building
let applyingIndexedDirsSetting = false;  // guard: reconciling the set FROM the setting must not write it back
const tabLastActivatedAt = new Map();  // layout item -> last-activated timestamp (ms) for per-pane LRU tab eviction
let fileTreeRepoPopoverPath = null;  // normalized path of the repo dir whose hover popover is showing
let diffRefFrom = readStoredDiffRef(diffRefFromStorageKey, 'HEAD');  // C6: global default FROM (per-repo fallback)
let diffRefTo = readStoredDiffRef(diffRefToStorageKey, 'current');   // C6: global default TO (per-repo fallback)
let diffRefsByRepo = readStoredDiffRefsByRepo();  // C6: {repoPath: {from, to}} — per-repo overrides
let fileExplorerMode = readStoredFileExplorerMode();
let commandPaletteNode = null;
let keyboardShortcutsNode = null;
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
let fileQuickOpenAbortController = null;
let tabsMenuSearchText = '';
// P0 menu-bar: how the Tabs ▾ navigator orders its list — 'default' (tmux/editors/other), 'attention'
// (needs-* sessions first, the "Needs me" view), or 'name'. Persisted; set from View → Sort tab list.
const tabsMenuSortModes = ['default', 'attention', 'name'];
let tabsMenuSortMode = tabsMenuSortModes.includes(storageGet('yolomux.tabsMenuSort.v1')) ? storageGet('yolomux.tabsMenuSort.v1') : 'default';
let fileExplorerShortcutRestoreSlots = null;
let clientSettingsPayload = bootstrap.settingsPayload || {};
let clientSettings = clientSettingsPayload.settings || {};
let clientSettingsDefaults = clientSettingsPayload.defaults || {};
let clientSettingsMtimeNs = Number(clientSettingsPayload.mtime_ns || 0);
let globalThemeMode = initialSetting('appearance.theme', defaultGlobalTheme);
let terminalThemeMode = initialSetting('appearance.terminal_theme', defaultTerminalTheme);
let dateTimeHourCycle = initialSetting('appearance.date_time_hour_cycle', '24') === '12' ? '12' : '24';
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
const documentTitleIdleThresholdMs = 120000;
let documentTitleIdleSinceMs = null;
const uploadResultsBySession = new Map();
const uploadCleanupTimers = new Map();
let uploadResultSequence = 0;
const pasteCounters = new Map();
const pasteCountersStorageKey = 'yolomux.pasteCounters.v1';
const pasteLockStorageKey = 'yolomux.pasteUploadLock.v1';
const tabMetaStorageKey = 'yolomux.showTabMeta.v1';
// DOIT.6 #40: YO!info and YO!agent are merged into one pane with an in-pane sub-tab toggle; the chosen
// sub-tab is remembered across reloads.
const infoSubTabStorageKey = 'yolomux.infoPanel.activeSubTab.v1';
const transcriptPreviewMessages = 200;
let remoteResizeDelayMs = initialSetting('performance.remote_resize_delay_ms', 200);
let metadataRefreshMs = initialSetting('performance.metadata_refresh_ms', 15001);
// DOIT.29: watched PRs poll on their own (longer) cadence; the latest payload + last-seen status per
// PR ref (for notify-on-transition diffing) live here.
let watchedPrRefreshMs = initialSetting('performance.watched_pr_refresh_ms', 60001);
let watchedPrsData = {watched_prs: [], truncated: 0, invalid: [], refresh_ms: watchedPrRefreshMs};
const watchedPrLastStatus = new Map();
let paneStateRefreshMs = initialSetting('performance.pane_state_refresh_ms', 1253);
let latencyRefreshMs = initialSetting('performance.latency_refresh_ms', 3001);
let eventLogRefreshMs = initialSetting('performance.event_log_refresh_ms', 5003);
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
function fileExplorerRefreshMsFromValues(secondsValue, legacyMsValue = 15001) {
  const seconds = Number(secondsValue);
  if (Number.isFinite(seconds)) return Math.max(1, Math.min(60, seconds)) * 1000 + 1;
  const legacyMs = Number(legacyMsValue);
  return Math.max(1000, Math.min(60000, Number.isFinite(legacyMs) ? legacyMs : 15001));
}
let fileExplorerRefreshMs = fileExplorerRefreshMsFromValues(
  initialSetting('file_explorer.refresh_seconds', 15),
  initialSetting('file_explorer.refresh_ms', 15001),
);
let fileExplorerIndexRefreshSeconds = initialSetting('file_explorer.index_refresh_seconds', 120);
let fileExplorerNewEntryHighlightMs = initialSetting('file_explorer.new_entry_highlight_ms', 60000);
let fileExplorerImagePreviewMaxPx = initialSetting('file_explorer.image_preview_max_px', 320);
let fileExplorerImageOpenMode = initialSetting('file_explorer.image_open_mode', 'same-tab');
let uploadMaxBytes = initialSetting('uploads.max_bytes', 20 * 1024 * 1024);
const uploadRsyncRecommendationBytes = 50 * 1024 * 1024;
let terminalFontSize = initialSetting('appearance.terminal_font_size', 13);
let editorFontSize = initialSetting('appearance.editor_font_size', 13);
let editorPreviewFontSize = initialSetting('appearance.preview_font_size', editorFontSize + 1);
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

const defaultSplitPercent = 50;
const fileExplorerSplitPercent = 22;
const layoutBoundaryDropFraction = 0.08;
const layoutBoundaryDropMinPx = 28;
const layoutBoundaryDropMaxPx = 64;
const minSplitPercent = 5;
const maxSplitPercent = 95;
const infoItemId = '__info__';
// Localized brand tab labels — functions (not consts) so a runtime language switch repaints them via
// rerenderForLocale(); `t` resolves lazily at call time (it's defined in 05_i18n, loaded after this).
function infoTabLabel() { return t('brand.tab.info'); }
const yoagentItemId = '__yoagent__';
const legacyYosupItemId = '__yosup__';
function yoagentTabLabel() { return t('brand.tab.agent'); }
// DOIT.6 #40: the active sub-tab within the merged YO!info pane ('info' | 'yoagent'), remembered.
let infoPanelSubTab = readStoredInfoSubTab();
const fileExplorerItemId = '__files__';
const prefsItemId = '__prefs__';
const emptyPaneParam = '__empty_pane__';
const fileEditorItemPrefix = 'file:';
const filePreviewItemPrefix = 'file-preview:';
const imageViewerItemPrefix = 'image:';
function fileEditorItemFor(path) { return fileEditorItemPrefix + path; }
function filePreviewItemFor(path) { return filePreviewItemPrefix + path; }
function imageViewerItemFor(path) { return imageViewerItemPrefix + path; }
const TAB_TYPES = [
  {
    // DOIT.6 #40: YO!info and YO!agent are ONE item now — the panel hosts both via an in-pane sub-tab
    // toggle. The legacy yoagent/yosup aliases resolve here so saved layouts and bookmarked
    // ?…=yoagent URLs open the merged pane (the boot deep-link scan pre-selects the YO!agent sub-tab).
    key: 'info',
    id: infoItemId,
    aliases: ['info', infoItemId, 'yoagent', 'yo!agent', 'yo-agent', 'yosup', 'yo', 'sup', yoagentItemId, legacyYosupItemId],
    match: item => item === infoItemId || item === yoagentItemId || item === legacyYosupItemId,
    label: () => infoTabLabel(),
    shortLabel: () => infoTabLabel(),
    terminalTitle: () => t('tab.unavailableFor', {name: infoTabLabel()}),
    sortRank: 0,
    param: () => 'info',
    detail: () => t('info.subtitle'),
    rowHtml: (item, options) => paneInfoTabHtml(item, options),
    createPanel: () => createInfoPanel(),
    className: () => 'info',
    icon: 'branch-info',
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
    prunePriority: () => 0,
  },
  {
    key: 'files',
    id: fileExplorerItemId,
    aliases: ['files', fileExplorerItemId],
    match: item => item === fileExplorerItemId,
    label: () => fileExplorerLabel(),
    shortLabel: () => fileExplorerLabel(),
    terminalTitle: () => t('tab.unavailableFor', {name: fileExplorerLabel()}),
    sortRank: 0.5,
    param: () => 'files',
    detail: () => compactHomePath(fileExplorerRoot || homePath || '/'),
    rowHtml: (item, options) => fileExplorerPaneTabHtml(item, options),
    createPanel: () => createFileExplorerPanel(),
    className: () => 'file-explorer',
    icon: 'finder',
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
    prunePriority: () => 0,
  },
  {
    key: 'preferences',
    id: prefsItemId,
    aliases: ['prefs', 'preferences', prefsItemId],
    match: item => item === prefsItemId,
    label: () => t('tab.preferences'),
    shortLabel: () => t('tab.preferences.short'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('tab.preferences')}),
    sortRank: 0.65,
    param: () => 'prefs',
    detail: () => compactHomePath(settingsConfigPath()),
    rowHtml: (item, options) => preferencesPaneTabHtml(item, options),
    createPanel: () => createPreferencesPanel(),
    className: () => 'preferences-item',
    icon: 'gear',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
    prunePriority: () => 0,
  },
  {
    key: 'image-viewer',
    prefix: imageViewerItemPrefix,
    match: item => typeof item === 'string' && item.startsWith(imageViewerItemPrefix),
    label: item => basenameOf(fileItemPath(item)),
    shortLabel: () => t('popover.kind.image'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('popover.kind.image')}),
    sortRank: 0.74,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    className: () => 'file-editor-item image-viewer-item',
    icon: 'document',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
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
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
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
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || rootCssLengthPx('--min-split-pane-width') || 320,
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
  const alt = options.alt ? `${isMacPlatform() ? '⌥' : 'Alt'}+` : '';
  return `${options.shift ? 'Shift+' : ''}${appShortcutModifierLabel()}+${alt}${key}`;
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
  return isMacPlatform() ? t('finder.label.finder') : t('finder.label.explorer');
}

function applyFileExplorerStaticLabels() {
  const label = fileExplorerLabel();
  fileExplorer?.setAttribute('aria-label', label);
  fileExplorerClose?.setAttribute('title', t('finder.close', {name: label}));
  applyPlatformControlClass(fileExplorerClose, 'close');
}
const syntaxLanguageByExtension = new Map(Object.entries(HIGHLIGHTABLE_EXTENSIONS));
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [infoItemId, fileExplorerItemId, prefsItemId, ...visibleSessions];
let layoutSlots = initialLayoutSlots();
let activeSessions = sessionsFromLayout();
let transcriptMeta = {};
let transcriptMetaLoading = false;
let transcriptMetaLoaded = false;
let transcriptMetaLoadError = '';
let transcriptMetaRefreshPromise = null;
let serverVersionReloadHandled = '';
let activitySummaryPayload = {sessions: {}, global: {lines: []}, session_order: []};
let activitySummaryRefreshing = false;
let activitySummaryRequestId = 0;
let yoagentMessages = [];
let yoagentBusy = false;
let yoagentPrewarming = false;
let yoagentPrewarmStarted = false;
let yoagentError = '';
let yoagentDraft = '';
let yoagentNotice = null;
let notificationsEnabled = false;
let fileExplorerChangesSelectedSession = '';
const sessionStateKeys = new Map();
const notificationLastSent = new Map();
const attentionAlertTimers = new Map();
const metadataBadgePulseUntil = new Map();

function setLimitedMapEntry(map, key, value, limit) {
  if (!map || !key) return;
  if (map.has(key)) map.delete(key);
  map.set(key, value);
  while (map.size > limit) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) break;
    map.delete(oldest);
  }
}

let infoBranchSort = {key: 'updated', dir: 'desc'};
let attentionAlertSequence = 0;
let stateTrackingReady = false;
let focusedTerminal = null;
let focusedPanelItem = null;
let lastActivePaneItem = null;
let lastFocusedTmuxSession = null;
let dragSession = null;
let dragSourceSlot = null;
// While a tab drag is in flight, tab/preferences re-renders are deferred so they don't replace the
// dragged DOM node mid-drag (which aborts the native HTML5 drag). endSessionDrag flushes these.
let pendingTabStripRender = false;
let pendingPreferencesRender = false;
// DOIT.6 #114: renderPanels() pools every panel and clears the grid (grid.innerHTML='').
// If it fires mid-drag (e.g. a metadata poll), it detaches the dragged node and the native
// HTML5 drag aborts. renderPanels defers to this flag while dragging; endSessionDrag flushes it.
let pendingPanelsRender = false;
let dragFilePayloadState = null;
let customDragPreview = null;
let customDragPreviewOffset = {x: 0, y: 0};
let transparentDragImage = null;
// #47: tab rects measured once per strip at drag time and reused for every dragover (tabs don't move
// mid-drag — renders are deferred), so the drop-placement path doesn't force sync layout on each move.
let dragTabRectCache = null;
// DOIT.21: one global editor navigation history (Cursor-style back/forward through visited files).
// stack holds file paths; index points at the current entry; `navigating` suppresses recording while a
// back/forward re-open is in flight (so it doesn't push a new entry).
const editorNav = {stack: [], index: -1, navigating: false};
const terminalContextMenu = createContextMenuController();
const fileContextMenu = createContextMenuController();
const sessionContextMenu = createContextMenuController();
const linkContextMenu = createContextMenuController();
const watchedPrContextMenu = createContextMenuController();   // DOIT.29: "Watch this PR" on YO!info PR cells
const repoChipContextMenu = createContextMenuController();     // C9: per-pane "+N repos" detail-bar popover
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

const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
// The exact launch command per agent (with --dangerously-* flags in YOLO mode) for the new-session menu.
const agentLaunchCommands = bootstrap.agentLaunchCommands || {};
// per-agent {installed, logged_in} login status (probed + cached server-side). Used to
// grey an installed-but-logged-out agent in the new-session picker. Refreshed by metadata polls.
let agentAuth = bootstrap.agentAuth || {};
const agentLoginCommands = {claude: 'claude auth login', codex: 'codex login'};
function agentLoggedIn(agent) {
  const entry = agentAuth[agent];
  // Unknown (term, or no status yet) counts as logged-in so we never block a usable agent.
  return !entry || !entry.installed || entry.logged_in !== false;
}
function agentLoginCommand(agent) {
  return agentLoginCommands[agent] || '';
}
function agentUnavailableReason(agent) {
  const entry = agentAuth[agent];
  return entry?.unavailable_reason || '';
}
function applyAgentAvailabilityPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  if (payload.agentAuth && typeof payload.agentAuth === 'object') agentAuth = payload.agentAuth;
  if (Array.isArray(payload.availableAgents)) {
    availableAgents.clear();
    payload.availableAgents.forEach(agent => {
      const name = String(agent || '').trim();
      if (name) availableAgents.add(name);
    });
  }
  return true;
}
const accessRole = bootstrap.accessRole || 'admin';
const readOnlyMode = accessRole !== 'admin';
const devMode = bootstrap.dev === true;   // dev-velocity #1b: subscribe to /api/dev-reload + auto-reload
const shareBootstrap = bootstrap.share && typeof bootstrap.share === 'object' ? bootstrap.share : null;
const shareViewMode = shareBootstrap?.view === true;
const shareWriteMode = shareViewMode && shareBootstrap?.mode === 'rw';
const shareToken = (() => {
  if (!shareViewMode) return '';
  try {
    return new URLSearchParams(String(location.hash || '').replace(/^#/, '')).get('t') || '';
  } catch (_) {
    return '';
  }
})();
const shareDebugEnabled = (() => {
  if (!shareViewMode) return false;
  try {
    const query = new URLSearchParams(location.search || '');
    if (query.get('shareDebug') === '1') return true;
  } catch (_) {}
  return storageGet('yolomux.shareDebug') === '1';
})();
const shareReplaySemanticEscapeEnabled = (() => {
  if (!shareViewMode) return false;
  if (bootstrap.shareReplay === false || shareBootstrap?.replay === false) return true;
  try {
    const query = new URLSearchParams(location.search || '');
    if (query.get('shareReplay') === '0' || query.get('shareSemantic') === '1') return true;
  } catch (_) {}
  return storageGet('yolomux.shareReplaySemantic') === '1';
})();
const shareReplayEnabled = (() => {
  if (shareReplaySemanticEscapeEnabled) return false;
  if (bootstrap.shareReplay === true || shareBootstrap?.replay === true) return true;
  try {
    const query = new URLSearchParams(location.search || '');
    if (query.get('shareReplay') === '1') return true;
  } catch (_) {}
  if (shareViewMode) return true;
  return storageGet('yolomux.shareReplay') === '1';
})();
function randomShareViewerId() {
  try {
    if (crypto?.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto?.getRandomValues?.(bytes);
    const encoded = Array.from(bytes).map(value => value.toString(16).padStart(2, '0')).join('');
    if (encoded) return encoded;
  } catch (_) {}
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
const shareViewerId = (() => {
  if (!shareViewMode) return '';
  const key = `yolomux.share.viewer.${shareBootstrap?.id || 'current'}`;
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const created = randomShareViewerId();
    sessionStorage.setItem(key, created);
    return created;
  } catch (_) {
    return randomShareViewerId();
  }
})();
const shareClientId = (() => {
  if (shareViewMode && shareViewerId) return shareViewerId;
  const key = shareViewMode
    ? `yolomux.share.client.${shareBootstrap?.id || 'current'}`
    : 'yolomux.share.hostClient';
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const created = randomShareViewerId();
    sessionStorage.setItem(key, created);
    return created;
  } catch (_) {
    return randomShareViewerId();
  }
})();
function shareBootstrapFinderState() {
  return shareViewMode && shareBootstrap?.finder && typeof shareBootstrap.finder === 'object'
    ? shareBootstrap.finder
    : {};
}
function shareBootstrapFinderRoot() {
  const root = shareBootstrapFinderState().root;
  return typeof root === 'string' && root.trim() ? root.trim() : '';
}
function shareBootstrapFinderRootMode(fallback = 'sync') {
  const mode = shareBootstrapFinderState().rootMode;
  if (mode === 'fixed' || mode === 'sync') return mode;
  return fallback === 'fixed' ? 'fixed' : 'sync';
}
function shareBootstrapFinderMode(fallback = 'files') {
  const mode = shareBootstrapFinderState().mode;
  if (mode === 'diff' || mode === 'tabber' || mode === 'files') return mode;
  return fallback;
}
function shareBootstrapFinderSession() {
  const session = String(shareBootstrapFinderState().session || '').trim();
  return session && sessions.includes(session) ? session : '';
}
const shareHostDimensions = new Map();
if (shareViewMode && shareBootstrap?.session && shareBootstrap?.hostDims) {
  shareHostDimensions.set(String(shareBootstrap.session), {
    rows: Number(shareBootstrap.hostDims.rows) || 0,
    cols: Number(shareBootstrap.hostDims.cols) || 0,
  });
}
if (shareViewMode && shareBootstrap?.hostDimsBySession && typeof shareBootstrap.hostDimsBySession === 'object') {
  for (const [session, dims] of Object.entries(shareBootstrap.hostDimsBySession)) {
    shareHostDimensions.set(String(session), {
      rows: Number(dims?.rows) || 0,
      cols: Number(dims?.cols) || 0,
    });
  }
}
let activeShare = null;
let activeShares = [];
let shareCreateErrorPayload = null;
let shareHostSockets = new Map();
let shareHostQueues = new Map();
let shareMirrorEpoch = 1;
let shareMirrorSequence = 0;
let shareReplayMirrorEpoch = 1;
let shareReplayMirrorSequence = 0;
const shareMirrorLastFrameBySender = new Map();
let shareUiStatePublishTimer = null;
let shareViewportPublishTimer = null;
let shareAppearancePublishTimer = null;
let sharePointerFramePending = false;
let sharePointerLastEvent = null;
let sharePointerLastPublishedAt = -Infinity;
const shareScrollPublishTimers = new Map();
const shareScrollRestoreFrameTimers = new Map();
let shareGeometryDigestTimer = null;
let shareDebugSequence = 0;
let shareDebugReports = [];
const shareDebugReportLimit = 20;
let shareDebugProfileLastUploadAtByKind = new Map();
let shareLastGeometryDigest = null;
let shareLastGeometryDigestResult = null;
let shareGeometryResyncInFlight = false;
let shareGeometryResyncLastStartedAt = 0;
let shareGeometryRepairInFlight = false;
const shareAppliedTextWrapMetricsByKey = new Map();
const sharePointerRecords = new Map();
let applyingShareRemoteScroll = false;
let applyingShareRemoteUiState = 0;
let sharePopupLayerNode = null;
let sharePopupLayerPublishTimer = null;
let sharePopupLayerObserver = null;
let sharePopupLayerSequence = 0;
let shareReplayHostNodeIds = new WeakMap();
let shareReplayHostMirroredNodes = new WeakSet();
let shareReplayHostNextNodeId = 1;
let shareReplayMutationObserver = null;
let shareReplayPendingMutations = [];
let shareReplayPendingTerminalPlaceholders = [];
let shareReplayDeltaFramePending = false;
let shareReplayMutationPublisherPaused = false;
let shareReplayLastDeltaBatch = null;
let shareReplayLastReplayError = null;
let shareReplayCurrentEpoch = 0;
let shareReplayLastSequence = 0;
let shareReplayDroppedFrames = 0;
let shareReplayStaleFrames = 0;
let shareReplayKeyframeRequestCount = 0;
let shareReplayKeyframeRequestSuppressedCount = 0;
let shareReplayKeyframeLastRequestAt = 0;
let shareReplayKeyframeBackoffMs = 0;
let shareReplayKeyframeInFlight = false;
let shareReplayHostKeyframeTimer = null;
let shareReplayHostKeyframePendingReason = '';
let shareReplayHostLastKeyframeAt = 0;
let shareReplayHostLastKeyframeReason = '';
let shareReplayHostKeyframeSuppressedCount = 0;
let shareReplayTopologyKeyframeTimer = null;
let shareReplayTopologyKeyframeQueuedAt = 0;
let shareReplayTopologyMutationPauseTimer = null;
const sharePopupLayerLastSeqBySender = new Map();
const shareLastAppliedScrollByTarget = new Map();
const homePath = bootstrap.homePath;
const repoRoot = bootstrap.repoRoot || '';
const serverHostname = bootstrap.serverHostname;
const appRoot = document.getElementById('appRoot') || document.body;
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
const fileExplorerPendingExpansions = new Set();
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
const fileExplorerOpenIntentStorageKey = 'yolomux.fileExplorerOpen.v1';
const fileExplorerTabberCollapsedStorageKey = 'yolomux.fileExplorer.tabberCollapsed.v1';
const fileExplorerTabberExpandedStorageKey = 'yolomux.fileExplorer.tabberExpanded.v1';
const fileExplorerTabberLookbackHoursStorageKey = 'yolomux.fileExplorer.tabberLookbackHours.v1';
const legacyFileExplorerChangesHiddenStorageKey = 'yolomux.fileExplorerChangesHidden';
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
function yolomuxEditorSchemeLabel(mode) {
  return `${t('app.documentTitle')} ${t(`pref.appearance.theme.${mode}`)}`;
}

const EDITOR_SCHEMES = {
  dark: {
    id: 'dark', get label() { return yolomuxEditorSchemeLabel('dark'); }, dark: true,
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
  'popular-ide-dark-plus': {
    id: 'popular-ide-dark-plus', label: 'Popular IDE Dark+', dark: true,
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
    id: 'yolomux-light', get label() { return yolomuxEditorSchemeLabel('light'); }, dark: false,
    bg: '#ffffff', fg: '#000000', cursor: '#000000', selection: 'rgba(37, 99, 235, 0.34)', activeLine: '#f4f7fb',
    gutterBg: '#f6f8fa', lineNo: '#64748b', panel: '#f6f8fa', panel2: '#eef2f7', line: '#d0d7de', previewBg: '#ffffff',
    syntax: {comment: '#008000', keyword: '#0000ff', control: '#af00db', string: '#0451a5', number: '#098658', function: '#267f2e', type: '#008080', variable: '#5f3b00', tag: '#800000', heading: '#000000', headingBg: '#ffffff', link: '#0451a5', inlineCode: '#a31515', inlineCodeBg: '#f3f3f3', inlineCodeBorder: '#d4d4d4', atom: '#0000ff', property: '#5f3b00', strong: '#000000', emphasis: '#795e26', invalid: '#a31515'},
    diff: {addFg: '#15803d', removeFg: '#b91c1c'},
  },
  'popular-ide-light-plus': {
    id: 'popular-ide-light-plus', label: 'Popular IDE Light+', dark: false,
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
const PREVIEW_RENDERERS = Object.freeze([
  {id: 'markdown', kind: 'markdown', extensions: ['.md', '.markdown'], textBacked: true, defaultMode: 'edit', language: 'markdown'},
  {id: 'html', kind: 'html', extensions: ['.html', '.htm'], textBacked: true, defaultMode: 'edit', language: 'xml', sandbox: true},
  {id: 'image', kind: 'image', mediaKind: 'image', extensions: ['.png', '.apng', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.avif'], textBacked: false, defaultMode: 'preview', raw: true, mimeByExtension: {
    '.png': 'image/png',
    '.apng': 'image/apng',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.bmp': 'image/bmp',
    '.avif': 'image/avif',
  }},
  {id: 'pdf', kind: 'pdf', mediaKind: 'pdf', extensions: ['.pdf'], textBacked: false, defaultMode: 'preview', raw: true, sandbox: true, mimeByExtension: {'.pdf': 'application/pdf'}},
  {id: 'mermaid', kind: 'mermaid', mediaKind: 'mermaid', extensions: ['.mmd', '.mermaid'], textBacked: true, defaultMode: 'preview', language: 'mermaid'},
  {id: 'structured', kind: 'structured', extensions: ['.json', '.jsonl', '.ndjson', '.geojson', '.ipynb', '.yaml', '.yml', '.toml', '.xml', '.drawio', '.dio', '.excalidraw', '.ini', '.cfg', '.conf', '.env', '.properties', '.props'], textBacked: true, defaultMode: 'edit', languageByExtension: {
    '.json': 'json',
    '.jsonl': 'json',
    '.ndjson': 'json',
    '.geojson': 'json',
    '.ipynb': 'json',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'ini',
    '.xml': 'xml',
    '.drawio': 'xml',
    '.dio': 'xml',
    '.excalidraw': 'json',
    '.ini': 'ini',
    '.cfg': 'ini',
    '.conf': 'ini',
    '.env': 'ini',
    '.properties': 'ini',
    '.props': 'ini',
  }},
  {id: 'table', kind: 'table', extensions: ['.csv', '.tsv'], textBacked: true, defaultMode: 'edit', language: 'text'},
  {id: 'audio', kind: 'audio', mediaKind: 'audio', extensions: ['.mp3', '.wav', '.ogg', '.oga', '.flac', '.m4a', '.aac', '.opus'], textBacked: false, defaultMode: 'preview', raw: true, mimeByExtension: {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.opus': 'audio/opus',
  }},
  {id: 'video', kind: 'video', mediaKind: 'video', extensions: ['.mp4', '.m4v', '.webm', '.mov', '.mkv', '.ogv', '.3gp'], textBacked: false, defaultMode: 'preview', raw: true, mimeByExtension: {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.mkv': 'video/x-matroska',
    '.ogv': 'video/ogg',
    '.3gp': 'video/3gpp',
  }},
  // Generic text/code preview is the same syntax-highlighted text the editor already shows. Keep the
  // renderer for language/fallback routing, but do not expose Preview until a distinct renderer exists.
  {id: 'text', kind: 'text', extensions: ['.txt', '.log', '.trace', '.out', '.rst', '.adoc', '.asciidoc', '.diff', '.patch', '.dot', '.gv', '.puml', '.plantuml', '.srt', '.vtt'], textBacked: true, previewable: false, defaultMode: 'edit', languageByExtension: {
    '.txt': 'text',
    '.log': 'text',
    '.trace': 'text',
    '.out': 'text',
    '.rst': 'text',
    '.adoc': 'text',
    '.asciidoc': 'text',
    '.diff': 'diff',
    '.patch': 'diff',
    '.dot': 'text',
    '.gv': 'text',
    '.puml': 'text',
    '.plantuml': 'text',
    '.srt': 'text',
    '.vtt': 'text',
  }},
  {id: 'unsupported-image', kind: 'unsupported', extensions: ['.tif', '.tiff', '.heic', '.heif'], textBacked: false, defaultMode: 'preview', raw: true, fallbackTitleKey: 'preview.unsupported.image', mimeByExtension: {
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.heic': 'image/heic',
    '.heif': 'image/heif',
  }},
  {id: 'unsupported-document', kind: 'unsupported', extensions: ['.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx'], textBacked: false, defaultMode: 'preview', raw: true, fallbackTitleKey: 'preview.unsupported.document', mimeByExtension: {
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  }},
  {id: 'unsupported-data', kind: 'unsupported', extensions: ['.sqlite', '.sqlite3', '.db', '.parquet', '.arrow', '.feather'], textBacked: false, defaultMode: 'preview', raw: true, fallbackTitleKey: 'preview.unsupported.data', mimeByExtension: {
    '.sqlite': 'application/vnd.sqlite3',
    '.sqlite3': 'application/vnd.sqlite3',
    '.db': 'application/vnd.sqlite3',
    '.parquet': 'application/vnd.apache.parquet',
    '.arrow': 'application/vnd.apache.arrow.file',
    '.feather': 'application/vnd.apache.arrow.file',
  }},
  {id: 'unsupported-archive', kind: 'unsupported', extensions: ['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.rar'], textBacked: false, defaultMode: 'preview', raw: true, fallbackTitleKey: 'preview.unsupported.archive', mimeByExtension: {
    '.zip': 'application/zip',
    '.tar': 'application/x-tar',
    '.gz': 'application/gzip',
    '.tgz': 'application/gzip',
    '.bz2': 'application/x-bzip2',
    '.xz': 'application/x-xz',
    '.7z': 'application/x-7z-compressed',
    '.rar': 'application/vnd.rar',
  }},
  {id: 'unsupported', kind: 'unsupported', extensions: [], textBacked: false, defaultMode: 'preview'},
]);
const PREVIEW_RENDERER_BY_ID = new Map(PREVIEW_RENDERERS.map(renderer => [renderer.id, renderer]));
const PREVIEW_RENDERER_BY_EXTENSION = new Map();
const PREVIEW_MIME_BY_EXTENSION = new Map();
const PREVIEW_RENDERER_BY_MIME = new Map();
for (const renderer of PREVIEW_RENDERERS) {
  for (const ext of renderer.extensions || []) {
    PREVIEW_RENDERER_BY_EXTENSION.set(ext, renderer);
    const mime = renderer.mimeByExtension?.[ext] || renderer.mime || '';
    if (mime) {
      PREVIEW_MIME_BY_EXTENSION.set(ext, mime);
      if (!PREVIEW_RENDERER_BY_MIME.has(mime)) PREVIEW_RENDERER_BY_MIME.set(mime, renderer);
    }
  }
}
const MAX_FILE_PREVIEW_BYTES = 20 * 1024 * 1024;
const HIGHLIGHTABLE_EXTENSIONS = {
  '.md': 'markdown', '.markdown': 'markdown',
  '.html': 'xml', '.htm': 'xml', '.xml': 'xml', '.svg': 'xml',
  '.py': 'python', '.pyw': 'python',
  '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.jsx': 'javascript',
  '.ts': 'typescript', '.tsx': 'typescript',
  '.json': 'json', '.jsonl': 'json', '.ndjson': 'json', '.geojson': 'json', '.ipynb': 'json', '.excalidraw': 'json',
  '.css': 'css', '.scss': 'scss',
  '.rs': 'rust', '.go': 'go', '.c': 'c', '.h': 'c',
  '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.yaml': 'yaml', '.yml': 'yaml',
  '.toml': 'ini', '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini', '.env': 'ini', '.properties': 'ini', '.props': 'ini',
  '.drawio': 'xml', '.dio': 'xml',
  '.sql': 'sql', '.rb': 'ruby', '.lua': 'lua', '.pl': 'perl',
};
const fileState = new Map();  // path -> open-file content plus editor tab/owner/mode/blame state
const openFiles = fileState;  // compatibility alias during the file-state migration
const fileIdentityByPath = new Map();  // display path -> backend physical-file identity
const openFilePathByIdentity = new Map();  // backend physical-file identity -> primary open display path
const fileOpenPromisesByPath = new Map();  // display path -> in-flight text-editor open promise
const fileExplorerDirectorySignatures = new Map();
const fileExplorerKnownEntryNames = new Map();
const fileExplorerNewEntryUntil = new Map();
const fileExplorerRepoInfoCache = new Map();
const fileExplorerSessionFilesCache = new Map();
const terminalFileReferenceTargetCache = new Map();
const fileExplorerMemoryCacheLimit = 512;
const fileExplorerRefreshIdleMs = 1500;
const commandPaletteRecentKeyLimit = 100;
const notificationLastSentLimit = 512;
const pendingFileEditorFocus = new Set();
const paneViewState = new Map();  // layout item -> generic pane scroll state
const pendingPaneViewStateCaptures = new Set();
const fileEditorViewState = new Map();  // layout item -> CodeMirror scroll/selection state
const pendingFileEditorLineTargets = new Map();  // layout item -> line target to apply after async CodeMirror load
const fileEditorDiffExpandOverrides = new Map();  // layout item -> per-editor diff context expansion
let layoutUrlStateFromQuery = null;
let layoutUrlStateApplied = false;
let layoutUrlStateRefreshTimer = null;
let activeFile = null;
let sharedImageViewerPath = null;
let fileExplorerRoot = shareBootstrapFinderRoot() || null;
let filesystemRefreshInFlight = false;
let fileExplorerRepoInfoCacheLoaded = false;
let fileExplorerRootMode = shareBootstrapFinderRootMode(readStoredFileExplorerRootMode());
let fileExplorerShowHidden = storageGet(fileExplorerHiddenStorageKey) === '1';
const fileEditorThemeModeStorageKey = 'yolomux.fileEditorThemeMode.v1';
const fileEditorPreviewDisplayModeStorageKey = 'yolomux.fileEditorPreviewDisplayMode.v1';
let fileEditorWrapEnabled = readStoredEditorWrap();
// inline git blame (Popular IDE-style). Persisted toggle + a per-path cache of the /api/blame payload.
let fileEditorBlameEnabled = storageGet('yolomux.editorBlame') === '1';
const editorBlameFetches = new Map();  // in-flight /api/blame fetch per path (dedup concurrent panels)
let fileEditorBlameAllLines = false;  // annotate every line vs current-line only (set from settings in applySettingsPayload)
let fileEditorLineNumbersEnabled = readStoredEditorLineNumbers();
// B4: when true the diff shows ALL context (no collapsed "N unchanged lines" folds). Persisted.
let diffExpandUnchanged = storageGet('yolomux.diffExpandUnchanged') === '1';
let fileEditorThemeMode = readStoredEditorThemeMode();
let fileEditorPreviewDisplayMode = readStoredEditorPreviewDisplayMode();
let fileEditorCursorStyle = 'block';  // C3: default caret is block; saved 'line' choices round-trip via settings
let fileEditorCursorColor = 'yellow';  // 'yellow' default; 'theme' uses the editor/terminal scheme cursor
let fileEditorAutosaveEnabled = false;
let fileEditorAutosaveDelaySeconds = 2.5;
const fileEditorAutosaveTimers = new Map();
const openFileBackgroundReloadDeferMs = 2000;
let codeMirrorApiPromise = null;
let codeMirrorBundlePromise = null;
let mermaidApiPromise = null;
let mermaidBundlePromise = null;
let preferencesSearchText = '';
let preferencesResetConfirmVisible = false;
const preferencesScrollRenderDeferMs = 200;
let preferencesScrollActiveUntil = 0;
let preferencesScrollFlushTimer = null;
const PREFERENCE_SECTION_IDS = Object.freeze({
  general: 'general',
  appearance: 'appearance',
  terminalEditor: 'terminal_editor',
  notifications: 'notifications',
  fileExplorer: 'file_explorer',
  uploads: 'uploads',
  performance: 'performance',
  github: 'github',
  yoagent: 'yoagent',
  share: 'share',
  yolo: 'yolo',
});
const DEFAULT_COLLAPSED_PREFERENCE_SECTION_IDS = Object.freeze([
  PREFERENCE_SECTION_IDS.general,
  PREFERENCE_SECTION_IDS.appearance,
  PREFERENCE_SECTION_IDS.performance,
  PREFERENCE_SECTION_IDS.notifications,
  PREFERENCE_SECTION_IDS.terminalEditor,
  PREFERENCE_SECTION_IDS.fileExplorer,
  PREFERENCE_SECTION_IDS.uploads,
]);
const LEGACY_PREFERENCE_SECTION_IDS_BY_ENGLISH_TITLE = Object.freeze({
  General: PREFERENCE_SECTION_IDS.general,
  Appearance: PREFERENCE_SECTION_IDS.appearance,
  Performance: PREFERENCE_SECTION_IDS.performance,
  Notifications: PREFERENCE_SECTION_IDS.notifications,
  'Terminal / Editor': PREFERENCE_SECTION_IDS.terminalEditor,
  'File Explorer': PREFERENCE_SECTION_IDS.fileExplorer,
  Finder: PREFERENCE_SECTION_IDS.fileExplorer,
  'Uploads/Downloads': PREFERENCE_SECTION_IDS.uploads,
  GitHub: PREFERENCE_SECTION_IDS.github,
  'YO!agent': PREFERENCE_SECTION_IDS.yoagent,
  'YO!share': PREFERENCE_SECTION_IDS.share,
  YOLO: PREFERENCE_SECTION_IDS.yolo,
});
let collapsedPreferenceSections = readStoredCollapsedPreferenceSections();
let changesFolderCollapsed = readStoredSet(changesFolderCollapsedStorageKey);
const changesFolderAutoCollapsed = new Set();
// Tabber session rows start expanded while each sub-window's directory branch starts collapsed.
// Persist both explicit choices, so a refresh cannot undo a user's disclosure click.
const fileExplorerTabberCollapsed = readStoredSet(fileExplorerTabberCollapsedStorageKey);
const fileExplorerTabberExpanded = readStoredSet(fileExplorerTabberExpandedStorageKey);
// Tabber activity ledger snapshot (GET /api/activity): {activity: {sessionKey|session:window: ActivityRecord}}.
// Drives per-row recency timestamps + most-recent-first sort. Refreshed only while the Tabber is open.
let tabberActivityPayload = {activity: {}, agents: []};
let tabberActivityRefreshMs;
let tabberLaunchWarmupStarted = false;
let tabberActivityRequestGeneration = 0;
let tabberActivityAppliedRequestGeneration = 0;
let tabberActivityLoaded = false;
let tabberActivityFetchPromise = null;
// per-repo collapse state for the Modified-files panel repo headers (keyed by repo path).
let changesRepoCollapsed = readStoredSet(changesRepoCollapsedStorageKey);
let fileExplorerSessionFilesPayload = {session: '', files: [], repos: [], errors: []};
let fileExplorerSessionFilesPayloadSignature = '';
let fileExplorerSessionFilesLoading = false;
const fileExplorerSessionFilesGuard = makeGenerationGuard();
let fileExplorerExplicitSyncSession = shareBootstrapFinderSession();
let fileExplorerChangesSelectedSession = shareBootstrapFinderSession();
const fileExplorerExpandedBySyncTarget = new Map();
let fileExplorerSyncManualCollapseTargetKey = '';
const fileExplorerSyncManualCollapsedByTarget = new Map();
let fileExplorerSyncManualCollapsedPaths = new Set();
let fileExplorerVisibleSyncSession = '';
let fileExplorerVisibleSyncRoot = '';
let fileExplorerLastInteractionAt = 0;
let fileExplorerRefreshDeferred = false;
const fileExplorerSelectedPaths = new Set();
let fileExplorerSelectionAnchor = null;
let fileExplorerSelectionLead = null;   // keyboard cursor (File-Explorer "lead" item); arrows move it, Shift+arrow extends anchor->lead
let sessionFilesSortMode = 'newest';
let fileExplorerTreeDateMode = readStoredFileExplorerTreeDateMode();
let fileExplorerTreeSortMode = readStoredFileExplorerTreeSortMode();
let fileExplorerIndexedDirs = readStoredFileExplorerIndexedDirs();
const fileExplorerIndexStatus = new Map();  // normalized indexed root -> 'building' | 'ready'
const fileIndexStatusPollRoots = new Set();  // normalized indexed roots still building
let applyingIndexedDirsSetting = false;  // guard: reconciling the set FROM the setting must not write it back
const tabLastActivatedAt = new Map();  // layout item -> last-activated timestamp (ms) for per-pane LRU tab eviction
let fileTreeRepoPopoverPath = null;  // normalized path of the repo dir whose hover popover is showing
let diffRefFrom = readStoredDiffRef(diffRefFromStorageKey, 'HEAD');  // C6: global default FROM (per-repo fallback)
let diffRefTo = readStoredDiffRef(diffRefToStorageKey, 'current');   // C6: global default TO (per-repo fallback)
let diffRefsByRepo = readStoredDiffRefsByRepo();  // C6: {repoPath: {from, to}} — per-repo overrides
let fileExplorerMode = shareBootstrapFinderMode(readStoredFileExplorerMode());
let commandPaletteNode = null;
let keyboardShortcutsNode = null;
let pendingGlobalShortcutChord = null;
let pendingGlobalShortcutChordTimer = null;
const globalShortcutChordTimeoutMs = 4000;
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
let clientSettingsMetadataDeferred = clientSettingsPayload.deferred_metadata === true;
let clientSettingsMetadataRefreshPromise = null;
let clientSettingsMetadataRefreshTimer = null;
const SETTING_FALLBACKS = Object.freeze({
  'appearance.date_time_hour_cycle': '24',
  'appearance.editor_font_size': 13,
  'appearance.file_explorer_font_size': 13,
  'appearance.terminal_font_size': 13,
  'editor.autosave_delay_seconds': 2.5,
  'file_explorer.image_open_mode': 'same-tab',
  'file_explorer.image_preview_max_px': 320,
  'general.auto_focus': false,
  'general.startup_tips': true,
  'terminal_editor.scrollback': 5000,
  'uploads.max_bytes': 300 * 1024 * 1024,
});
let globalThemeMode = initialSetting('appearance.theme', defaultGlobalTheme);
let shareResolvedGlobalThemeMode = '';
let terminalThemeMode = initialSetting('appearance.terminal_theme', defaultTerminalTheme);
let dateTimeHourCycle = initialSetting('appearance.date_time_hour_cycle') === '12' ? '12' : '24';
fileEditorThemeMode = readConfiguredEditorScheme();
fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds');
let yoloRulesPayload = bootstrap.yoloRulesPayload || {};
const terminals = new Map();
const ensureSessionPromises = new Map();
const terminalStartupPromises = new Map();
const pendingTmuxSessions = new Map();
const panelNodes = new Map();
const resizeObservers = new Map();
const transcriptStreams = new Map();
const summaryStreams = new Map();
const autoApproveStates = new Map();
const attentionAcknowledgementRecords = new Map();
const attentionAcknowledgementRecordLimit = 1024;
const documentTitleIdleThresholdMs = 120000;
const tmuxSignalActivityWindowMs = documentTitleIdleThresholdMs;
let documentTitleIdleSinceMs = null;
const uploadResultRecords = new Map();
let uploadResultSequence = 0;
const pasteCounters = new Map();
const pasteCountersStorageKey = 'yolomux.pasteCounters.v1';
const pasteLockStorageKey = 'yolomux.pasteUploadLock.v1';
const tabMetaStorageKey = 'yolomux.showTabMeta.v1';
const pinnedTabsStorageKey = 'yolomux.pinnedTabs.v1';
const shareViewFitStorageKey = 'yolomux.share.viewFit.v1';
const startupHelperIndexStorageKey = 'yolomux.startupHelper.index.v1';
// Legacy merged-pane sub-tab compatibility only. YO!info and YO!agent now have separate virtual tabs.
const infoSubTabStorageKey = 'yolomux.infoPanel.activeSubTab.v1';
const infoLookbackHoursStorageKey = 'yolomux.infoPanel.lookbackHours.v1';
const transcriptPreviewMessages = 200;
let remoteResizeDelayMs = initialSetting('performance.remote_resize_delay_ms');
// The latest watched-PR payload + last-seen status per PR ref (for notify-on-transition diffing) live here.
let watchedPrsData = {watched_prs: [], truncated: 0, invalid: []};
const watchedPrLastStatus = new Map();
let latencyRefreshMs = initialSetting('performance.latency_refresh_ms');
let eventLogRefreshMs = initialSetting('performance.event_log_refresh_ms');
let tmuxSignalState = null;
tabberActivityRefreshMs = initialSetting('performance.tabber_activity_refresh_ms');
let agentStatusPulsePeriodMs = initialSetting('performance.agent_status_pulse_period_ms');
let workflowTransitionGlowSeconds = initialSetting('performance.workflow_transition_glow_seconds');
const latencySamplesMax = 24;
let toastDurationMs = initialSetting('notifications.toast_duration_ms');
const toastMaxLines = 3;
const toastMaxLineChars = 180;
let pinnedTabItems = readStoredPinnedTabs();
let popoverShowDelayMs = initialSetting('performance.popover_show_delay_ms');
let hoverCloseDelayMs = initialSetting('performance.popover_hide_delay_ms');
let popoverHideDelayMs = hoverCloseDelayMs;
let menuHoverOpenDelayMs = initialSetting('performance.menu_hover_open_delay_ms');
let menuHoverCloseDelayMs = hoverCloseDelayMs;
let tabPopoverShowDelayMs = initialSetting('performance.tab_popover_show_delay_ms');
let tabPopoverFollowDelayMs = initialSetting('performance.tab_popover_follow_delay_ms');
const fileImagePreviewMinShowDelayMs = 800;
const fileEditorScrollSyncSuppressMs = 150;
let serverWatchRootsSignature = '';
let serverWatchRootsInFlight = false;
let serverWatchRootsSyncedAt = 0;
let serverWatchRootsTimer = null;
let serverWatchRootsPendingOptions = {};
let fileExplorerFilesystemWatchToken = '';
let fileExplorerFilesystemLastFullAt = 0;
const fileExplorerFilesystemKeyframeMs = 60001;
let fileExplorerIndexRefreshSeconds = initialSetting('file_explorer.index_refresh_seconds');
let fileExplorerNewEntryHighlightMs = initialSetting('file_explorer.new_entry_highlight_ms');
let fileExplorerImagePreviewMaxPx = initialSetting('file_explorer.image_preview_max_px');
let fileExplorerImageOpenMode = initialSetting('file_explorer.image_open_mode');
let uploadMaxBytes = initialSetting('uploads.max_bytes');
const uploadRsyncRecommendationBytes = 50 * 1024 * 1024;
let terminalFontSize = initialSetting('appearance.terminal_font_size');
const terminalFontFamily = '"YOLOmux Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
let editorFontSize = initialSetting('appearance.editor_font_size');
let editorPreviewFontSize = initialSetting('appearance.preview_font_size', editorFontSize + 1);
let fileExplorerFontSize = initialSetting('appearance.file_explorer_font_size');
let terminalScrollback = initialSetting('terminal_editor.scrollback');
let autoFocusEnabled = initialSetting('general.auto_focus');
let startupHelpersEnabled = initialSetting('general.startup_tips') !== false;
const menuClickCloseGraceMs = 2000;
const terminalFitBottomReservePx = 2;
const terminalWheelPageFraction = 0.85;
const terminalWheelPixelLinePx = 35;
const terminalWheelMaxLinesPerEvent = 12;
const maxSessionTabs = bootstrap.maxSessionTabs;
const linearIssueBaseUrl = String(bootstrap.linearIssueBaseUrl || 'https://linear.app/issue').replace(/\/+$/, '');
const basePaneKeys = ['left', 'right'];
const splitPaneKeys = ['leftTop', 'leftBottom', 'rightTop', 'rightBottom'];
const paneKeys = [...basePaneKeys, ...splitPaneKeys];
const layoutTreeKey = '__tree';
const layoutTreeParamPrefix = 'tree:';

const defaultSplitPercent = 50;
const fileExplorerSplitPercent = 22;
const minNonFileExplorerSplitPercent = 30;
const defaultLayoutMode = 'split';
const layoutModeValues = ['single', 'split', 'grid'];
const legacyLayoutModeValues = [...layoutModeValues, 'wall'];
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
// Legacy share/deeplink compatibility only. YO!info and YO!agent are separate tabs.
let infoPanelSubTab = readStoredInfoSubTab();
const fileExplorerItemId = '__files__';
const searchHistoryItemId = '__search_history__';
function searchHistoryTabLabel() { return t('tab.searchHistory'); }
const prefsItemId = '__prefs__';
const debugPaneItemId = '__debug__';
const emptyPaneParam = '__empty_pane__';
const fileEditorItemPrefix = 'file:';
const fileEditorCopyItemPrefix = 'filecopy:';
const fileEditorDiffPreviewItemPrefix = 'filediff:';
const imageViewerItemPrefix = 'image:';
let fileEditorCopyItemSeq = 0;
function urlFlagEnabled(name) {
  try {
    return new URLSearchParams(location.search || '').get(name) === '1';
  } catch (_) {
    return false;
  }
}
const jsDebugCollectionEnabled = true;
const debugModeExplicitUrlEnabled = urlFlagEnabled('debug');
let debugModeEnabled = debugModeExplicitUrlEnabled;
const jsDebugEventLimit = 200;
const jsDebugRenderDebounceMs = 500;
let jsDebugEventSeq = 0;
let jsDebugEvents = [];
let jsDebugEventCaptureInstalled = false;
let jsDebugRenderTimer = null;
let jsDebugRenderForce = false;
const clientPerfCounterLimit = 80;
const clientPerfLongTaskSampleLimit = 40;
const clientPerfCounters = new Map();
let clientPerfLongTaskSamples = [];
let clientPerfLongTaskObserverInstalled = false;
const terminalRemovalLatencyPending = new Map();
let terminalRemovalLatencySamples = [];
const terminalRemovalLatencySampleLimit = 40;
const CLS = Object.freeze({
  active: 'active',
  collapsed: 'collapsed',
  dragOver: 'drag-over',
  dropPreview: 'drop-preview',
  fileDragOver: 'file-drag-over',
  open: 'open',
  pathDragOver: 'path-drag-over',
  selected: 'selected',
  tabDragOver: 'tab-drag-over',
  tabDropPreview: 'tab-drop-preview',
});
const THEME_CLASS_BY_MODE = Object.freeze({
  system: 'theme-system',
  dark: 'theme-dark',
  light: 'theme-light',
});
const THEME_RESOLVED_CLASS_BY_MODE = Object.freeze({
  dark: 'theme-resolved-dark',
  light: 'theme-resolved-light',
});
const THEME_BODY_CLASSES = Object.freeze([
  ...Object.values(THEME_CLASS_BY_MODE),
  ...Object.values(THEME_RESOLVED_CLASS_BY_MODE),
]);
const EDITOR_THEME_CLASS_BY_MODE = Object.freeze({
  system: 'editor-theme-system',
  dark: 'editor-theme-dark',
  light: 'editor-theme-light',
});
const EDITOR_THEME_BODY_CLASSES = Object.freeze(Object.values(EDITOR_THEME_CLASS_BY_MODE));
const EDITOR_PREVIEW_VANILLA_CLASS = 'editor-preview-vanilla';
const PREVIEW_POPOUT_BODY_CLASSES = Object.freeze([
  THEME_CLASS_BY_MODE.light,
  THEME_CLASS_BY_MODE.dark,
  EDITOR_THEME_CLASS_BY_MODE.light,
  EDITOR_THEME_CLASS_BY_MODE.dark,
  EDITOR_PREVIEW_VANILLA_CLASS,
]);
const STATE_KEY = Object.freeze({
  approval: 'approval',
  blocked: 'blocked',
  interrupted: 'interrupted',
  needsApproval: 'needs-approval',
  needsInput: 'needs-input',
  working: 'working',
  idle: 'idle',
});
const STATE_CLASS = Object.freeze({
  needsAttention: 'needs-attention',
  needsInput: STATE_KEY.needsInput,
  needsExec: 'needs-exec',
  needsBlocked: 'needs-blocked',
  needsInputPane: `${STATE_KEY.needsInput}-pane`,
  needsExecPane: 'needs-exec-pane',
  needsBlockedPane: 'needs-blocked-pane',
});
const DROP_PREVIEW_CLASSES = Object.freeze([
  CLS.dragOver,
  CLS.tabDragOver,
  CLS.tabDropPreview,
  CLS.dropPreview,
  'drop-preview-top',
  'drop-preview-bottom',
  'drop-preview-left',
  'drop-preview-right',
  'drop-preview-middle',
  'drop-preview-root',
  'drop-preview-gutter',
]);
function makeGenerationGuard() {
  let generation = 0;
  return Object.freeze({
    begin() {
      const current = ++generation;
      return () => current === generation;
    },
    invalidate() {
      generation += 1;
    },
  });
}
function fileEditorItemFor(path) { return fileEditorItemPrefix + path; }
function fileEditorDiffPreviewItemFor(path) { return fileEditorDiffPreviewItemPrefix + path; }
function isFileEditorDiffPreviewItem(item) {
  return typeof item === 'string' && item.startsWith(fileEditorDiffPreviewItemPrefix);
}
function fileEditorDiffPreviewItemPath(item) {
  const text = String(item || '');
  if (!text.startsWith(fileEditorDiffPreviewItemPrefix)) return null;
  const path = text.slice(fileEditorDiffPreviewItemPrefix.length);
  return path.startsWith('/') ? path : null;
}
function fileEditorCopyItemFor(path) {
  fileEditorCopyItemSeq += 1;
  return `${fileEditorCopyItemPrefix}${Date.now().toString(36)}-${fileEditorCopyItemSeq.toString(36)}:${path}`;
}
function fileEditorCopyItemPath(item) {
  const text = String(item || '');
  if (!text.startsWith(fileEditorCopyItemPrefix)) return null;
  const rest = text.slice(fileEditorCopyItemPrefix.length);
  const separator = rest.indexOf(':');
  const path = separator >= 0 ? rest.slice(separator + 1) : '';
  return path.startsWith('/') ? path : null;
}
function imageViewerItemFor(path) { return imageViewerItemPrefix + path; }
function filePanelTabType({key, prefix, prefixes = null, shortLabel, terminalTitle, className, sortRank, focusSearch = null}) {
  const matchPrefixes = Array.isArray(prefixes) && prefixes.length ? prefixes : [prefix];
  return {
    key,
    prefix,
    prefixes: matchPrefixes,
    match: item => typeof item === 'string' && matchPrefixes.some(itemPrefix => item.startsWith(itemPrefix)),
    label: item => basenameOf(fileItemPath(item)),
    shortLabel,
    terminalTitle,
    sortRank,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    relocalize: (item, panel) => relocalizeFileEditorPanel(panel, item),
    canPopout: item => {
      const path = fileItemPath(item);
      return Boolean(path && editorPreviewModeAvailable(path, openFiles.get(path)));
    },
    popoutDisabledReason: item => t(fileItemPath(item)
      ? 'pane.popout.filePreviewRequired'
      : 'pane.popout.filePathRequired'),
    openPopout: item => {
      const path = fileItemPath(item);
      return Boolean(path && openFilePreviewPopout(path, document.getElementById(panelDomId(item))));
    },
    focusSearch,
    className,
    icon: 'document',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 1,
  };
}
const TAB_TYPES = [
  {
    // YO!info and YO!agent are independent virtual tabs. Legacy yoagent/yosup aliases
    // resolve to the standalone YO!agent item below.
    key: 'info',
    id: infoItemId,
    aliases: ['info', 'info2', 'yo-info2', 'yoinfo2', infoItemId, '__info2__'],
    match: item => item === infoItemId || item === '__info2__',
    label: () => infoTabLabel(),
    shortLabel: () => infoTabLabel(),
    terminalTitle: () => t('tab.unavailableFor', {name: infoTabLabel()}),
    sortRank: 0,
    param: () => 'info',
    detail: () => t('menu.file.info.detail'),
    rowHtml: (item, options) => paneInfoTabHtml(item, options),
    createPanel: () => createInfoPanel(),
    canPopout: true,
    popoutRenderer: item => panePopoutPanelSnapshot(item),
    renderAttached: () => {
      renderInfoPanel();
    },
    relocalize: (_item, panel) => {
      renderInfoPanel({force: true});
      relocalizeInfoPanelChrome(panel);
    },
    focusSearch: (_item, panel) => focusPanelSearchInput(panel, '[data-info-search]', {panelSelector: '.info-tree-panel', select: true}),
    className: () => 'info',
    icon: 'branch-info',
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 0,
  },
  {
    key: 'yoagent',
    id: yoagentItemId,
    aliases: ['yoagent', 'yo!agent', 'yo-agent', 'yosup', 'yo', 'sup', yoagentItemId, legacyYosupItemId],
    match: item => item === yoagentItemId || item === legacyYosupItemId,
    label: () => yoagentTabLabel(),
    shortLabel: () => yoagentTabLabel(),
    terminalTitle: () => t('tab.unavailableFor', {name: yoagentTabLabel()}),
    sortRank: 0.1,
    param: () => 'yoagent',
    detail: () => t('menu.file.yoagent.detail'),
    rowHtml: (item, options) => paneInfoTabHtml(item, options),
    createPanel: () => createYoagentPanel(),
    renderAttached: () => {
      renderYoagentPanel({preserveDraft: true, scrollBottom: true});
      showYoagentStartupInfoOnce();
      loadYoagentConversation({silent: true, scrollBottom: true});
      loadYoagentJobs({silent: true, scrollBottom: true});
      prewarmYoagent({scrollBottom: true});
    },
    relocalize: (_item, panel, options = {}) => {
      renderYoagentPanel({preserveDraft: true, allowBusyRebuild: options.localeChange === true});
      relocalizeYoagentPanelChrome(panel);
    },
    className: () => 'yoagent',
    icon: 'yoagent',
    popoutDisabledReason: () => t('pane.popout.interactiveDisabled', {name: yoagentTabLabel()}),
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx(),
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
    relocalize: () => relocalizeFileExplorerPanels(),
    className: () => 'file-explorer',
    icon: 'finder',
    popoutDisabledReason: () => t('pane.popout.interactiveDisabled', {name: fileExplorerLabel()}),
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 0,
  },
  {
    key: 'search-history',
    id: searchHistoryItemId,
    aliases: ['search', 'history', 'run-history', 'search-history', searchHistoryItemId],
    match: item => item === searchHistoryItemId,
    label: () => searchHistoryTabLabel(),
    shortLabel: () => t('common.search'),
    terminalTitle: () => t('tab.unavailableFor', {name: searchHistoryTabLabel()}),
    sortRank: 0.6,
    param: () => 'search-history',
    detail: () => t('searchHistory.detail'),
    rowHtml: (item, options) => searchHistoryPaneTabHtml(item, options),
    createPanel: () => createSearchHistoryPanel(),
    renderAttached: () => loadSearchHistoryPanelData({silent: true}),
    relocalize: (_item, panel) => renderSearchHistoryPanel(panel),
    focusSearch: (_item, panel) => focusPanelSearchInput(panel, '[data-search-history-query]', {panelSelector: '.search-history-panel', select: true}),
    className: () => 'search-history-item',
    icon: 'document',
    popoutDisabledReason: () => t('pane.popout.interactiveDisabled', {name: searchHistoryTabLabel()}),
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 0,
  },
  {
    key: 'preferences',
    id: prefsItemId,
    aliases: ['prefs', 'preferences', prefsItemId],
    match: item => item === prefsItemId,
    label: () => t('common.preferences'),
    shortLabel: () => t('tab.preferences.short'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('common.preferences')}),
    sortRank: 0.65,
    param: () => 'prefs',
    detail: () => compactHomePath(settingsConfigPath()),
    rowHtml: (item, options) => preferencesPaneTabHtml(item, options),
    createPanel: () => createPreferencesPanel(),
    relocalize: () => renderPreferencesPanels({force: true}),
    focusSearch: (_item, panel) => focusPreferencesSearch(panel, {select: true}),
    popoutDisabledReason: () => t('pane.popout.interactiveDisabled', {name: t('common.preferences')}),
    className: () => 'preferences-item',
    icon: 'gear',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 0,
  },
  {
    key: 'debug',
    id: debugPaneItemId,
    aliases: ['debug', 'js-debug', 'jsdebug', debugPaneItemId],
    match: item => item === debugPaneItemId,
    label: () => t('tab.debug'),
    shortLabel: () => t('tab.debug.short'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('tab.debug')}),
    sortRank: 0.7,
    param: () => 'debug',
    detail: () => t('menu.file.debug.detail'),
    rowHtml: (item, options) => debugPaneTabHtml(item, options),
    createPanel: () => createDebugPanel(),
    canPopout: true,
    popoutRenderer: item => panePopoutPanelSnapshot(item),
    renderAttached: () => {
      enableDebugMode();
      renderDebugPanels();
    },
    relocalize: (_item, panel) => {
      renderDebugPanels({force: true});
      relocalizeDebugPanelChrome(panel);
    },
    className: () => 'debug-item',
    icon: 'tab-meta',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 0,
  },
  filePanelTabType({
    key: 'image-viewer',
    prefix: imageViewerItemPrefix,
    shortLabel: () => t('popover.kind.image'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('popover.kind.image')}),
    sortRank: 0.74,
    className: () => 'file-editor-item image-viewer-item',
  }),
  filePanelTabType({
    key: 'file-editor',
    prefix: fileEditorItemPrefix,
    prefixes: [fileEditorItemPrefix, fileEditorCopyItemPrefix, fileEditorDiffPreviewItemPrefix],
    shortLabel: () => t('common.edit'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('popover.kind.text')}),
    sortRank: 0.75,
    className: () => 'file-editor-item',
    focusSearch: (_item, panel) => focusFileEditorSearch(panel),
  }),
];
function tabTypeForItem(item) { return TAB_TYPES.find(type => type.match(item)) || null; }
function tabTypeForParam(value) {
  const text = String(value || '');
  return TAB_TYPES.find(type => {
    if ((type.aliases || []).includes(text)) return true;
    const prefixes = Array.isArray(type.prefixes) && type.prefixes.length ? type.prefixes : [type.prefix].filter(Boolean);
    return prefixes.some(prefix => text.startsWith(prefix));
  }) || null;
}
function tabTypeParam(type, item) { return typeof type?.param === 'function' ? type.param(item) : type?.param; }
function isFileExplorerItem(item) { return tabTypeForItem(item)?.key === 'files'; }
function isYoagentItem(item) { return tabTypeForItem(item)?.key === 'yoagent'; }
function isPreferencesItem(item) { return tabTypeForItem(item)?.key === 'preferences'; }
function isDebugItem(item) { return tabTypeForItem(item)?.key === 'debug'; }
function isImageViewerItem(item) { return tabTypeForItem(item)?.key === 'image-viewer'; }
function isFileEditorItem(item) {
  const key = tabTypeForItem(item)?.key;
  return key === 'file-editor' || key === 'image-viewer';
}
function fileItemPath(item) {
  if (isImageViewerItem(item)) return item.slice(imageViewerItemPrefix.length);
  if (typeof item === 'string' && item.startsWith(fileEditorCopyItemPrefix)) return fileEditorCopyItemPath(item);
  if (typeof item === 'string' && item.startsWith(fileEditorDiffPreviewItemPrefix)) return fileEditorDiffPreviewItemPath(item);
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

function metaShortcutText(key) {
  return `${isMacPlatform() ? '⌘' : 'Meta'}+${key}`;
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
function virtualTabItems() {
  return [infoItemId, yoagentItemId, fileExplorerItemId, searchHistoryItemId, prefsItemId, debugPaneItemId];
}
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [...virtualTabItems(), ...visibleSessions];
let layoutSlots = initialLayoutSlots();
let activeSessions = sessionsFromLayout();
let transcriptMeta = {};
let transcriptMetaLoading = false;
let transcriptMetaLoaded = false;
let transcriptMetaLoadError = null;
let transcriptMetaRefreshPromise = null;
let infoPanelRenderPending = false;
let infoPanelLastRenderSignature = '';
let infoPanelLastRenderHtml = '';
let clientEventsSource = null;
let clientEventsConnected = false;
const clientPushEventQueue = new Map();
let clientPushEventFrame = 0;
let reconnectResyncTimer = null;
const reconnectResyncDebounceMs = 751;
let serverVersionReloadHandled = '';
let activitySummaryPayload = {sessions: {}, global: {lines: []}, session_order: []};
let activitySummaryRefreshing = false;
let activitySummaryLastRefreshTs = 0;
const activitySummaryGuard = makeGenerationGuard();
let backgroundOwnerStatusPayload = null;
let backgroundOwnerStatusLoading = false;
let backgroundOwnerStatusLoaded = false;
let backgroundOwnerStatusError = '';
let backgroundOwnerStatusRefreshPromise = null;
let yoagentStartupActivitySummaryPayload = null;
let yoagentMessages = [];
let yoagentPendingWaits = [];
let yoagentJobs = [];
let yoagentJobsLoading = false;
let yoagentConversationLoaded = false;
let yoagentConversationLoading = false;
let yoagentConversationPath = '';
let yoagentConversationDisplayPath = '';
let yoagentBusy = false;
let yoagentPrewarming = false;
let yoagentPrewarmStarted = false;
let yoagentStartupLlmRequested = false;
let yoagentStreamingMessages = new Map();
let yoagentActiveChatRequest = null;
let yoagentChatQueue = [];
let yoagentChatQueueSerial = 0;
let yoagentError = null;
let yoagentDraft = '';
let yoagentHistoryCursor = null;
let yoagentHistoryDraft = '';
let yoagentNotice = null;
let yoagentScrollbackLocked = false;
let yoagentStartupInfoShown = false;
let yoagentStartupInfoVisible = false;
let searchHistoryQuery = '';
let searchHistoryPayload = {query: '', results: []};
let searchHistoryLoading = false;
let searchHistoryError = null;
let runHistoryPayload = {runs: []};
let runHistoryLoading = false;
let runHistoryError = null;
const notificationDeliveryStorageKey = 'yolomux.notificationDelivery.v1';
const notificationDeliveryDefaults = Object.freeze({inApp: true, system: false});
let notificationDelivery = {...notificationDeliveryDefaults};
const sessionStatusRecords = new Map();
const watchedPrNotificationLastSent = new Map();
const toastRecords = new Map();
const sessionRepoDisplayRoot = new Map();

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

function sessionStatusRecord(session, create = false) {
  const key = String(session || '').trim();
  if (!key) return null;
  let record = sessionStatusRecords.get(key) || null;
  if (!record && create) {
    record = {
      state: null,
      notificationLastSent: new Map(),
      workingAgentNotificationTones: new Map(),
      metadataBadgePulseUntil: new Map(),
    };
    sessionStatusRecords.set(key, record);
  }
  return record;
}

function sessionNotificationLastSentAt(session, key) {
  return Number(sessionStatusRecord(session)?.notificationLastSent.get(key) || 0);
}

function recordSessionNotificationSent(session, key, sentAt) {
  const record = sessionStatusRecord(session, true);
  if (!record) return;
  setLimitedMapEntry(record.notificationLastSent, key, sentAt, notificationLastSentLimit);
}

let shareInfoBranchRowsOverride = null;
let attentionAlertSequence = 0;
let stateTrackingReady = false;
let focusedTerminal = null;
let focusedPanelItem = null;
let lastActivePaneItem = null;
let lastActiveNonFileExplorerPaneItem = null;
let lastFocusedTmuxSession = null;
let dragSession = null;
let dragSourceSlot = null;
let dragPaneSlot = null;
// While a tab drag is in flight, tab/preferences re-renders are deferred so they don't replace the
// dragged DOM node mid-drag (which aborts the native HTML5 drag). endSessionDrag flushes these.
let pendingTabStripRender = false;
let pendingSessionButtonsRender = false;
let pendingPreferencesRender = false;
// panel renders deferred during tab drag keep the cheap/full render decision that was made
// while the layout model changed. A boolean loses the pre-change shape and forces a full rebuild on drop.
let pendingLayoutRender = null;
let dragFilePayloadState = null;
let customDragPreview = null;
let customDragPreviewOffset = {x: 0, y: 0};
let nativeDragImagePreview = null;
let transparentDragImage = null;
// #47: tab rects measured once per strip at drag time and reused for every dragover (tabs don't move
// mid-drag — renders are deferred), so the drop-placement path doesn't force sync layout on each move.
let dragTabRectCache = null;
// one global editor navigation history (Popular IDE-style back/forward through visited files).
// stack holds file paths; index points at the current entry; `navigating` suppresses recording while a
// back/forward re-open is in flight (so it doesn't push a new entry).
const editorNav = {stack: [], index: -1, navigating: false};
const terminalContextMenu = createContextMenuController();
const fileContextMenu = createContextMenuController();
const sessionContextMenu = createContextMenuController();
const linkContextMenu = createContextMenuController();
const repoChipContextMenu = createContextMenuController();     // C9: per-pane "+N repos" detail-bar popover
const backgroundOwnerContextMenu = createContextMenuController();
let sessionRenameDialog = null;
let fileExplorerManualSelectionActive = false;
let fileTreeRenamePath = null;
let fileExplorerPathError = '';
let fileExplorerLastListError = null;
let fileImagePreviewPopover = null;
let fileImagePreviewController = null;
let fileExplorerInteractionGeneration = 0;
let fileExplorerOpenGeneration = 0;
let clipboardPasteBound = false;
let pasteUploadInFlight = false;
let layoutResizeState = null;
let responsiveLayoutPruneTimer = null;
let topbarResizeObserver = null;
const tabStripOverflowCheckSet = new Set();
let tabStripOverflowCheckFrame = null;
let latencySamples = [];
let tabMetaVisible = readStoredTabMetaVisible();
let authRedirectStarted = false;
let openAppMenuId = null;
let openAppMenuPinned = false;
let openAppMenuOpenedAt = 0;
let fileExplorerSyncPathInFlight = '';
let fileExplorerLastAppliedSyncPlanKey = '';
let fileExplorerSyncGeneration = 0;

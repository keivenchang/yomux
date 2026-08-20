const pageLoadBundleEvalStartedAt = Number(globalThis.performance?.now?.()) || 0;
const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
const statsWriterFence = (() => {
  const value = bootstrap.statsWriterFence;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const protocolVersion = Number(value.protocol_version);
  const schemaGeneration = Number(value.schema_generation);
  if (!Number.isSafeInteger(protocolVersion) || protocolVersion < 1 || !Number.isSafeInteger(schemaGeneration) || schemaGeneration < 1) return null;
  return Object.freeze({protocolVersion, schemaGeneration});
})();
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
const terminalCommands = Array.isArray(bootstrap.terminalCommands) ? bootstrap.terminalCommands : [];
const fullAccessAgentLaunchesEnabled = bootstrap.dangerouslyYolo === true;
// The exact normal/full-access launch commands per agent for the new-session menu.
const agentLaunchCommands = bootstrap.agentLaunchCommands || {};
// per-agent {installed, logged_in} login status (probed + cached server-side). Used to
// grey an installed-but-logged-out agent in the new-session picker. Refreshed by metadata polls.
let agentAuth = bootstrap.agentAuth || {};
const agentLoginCommands = {claude: 'claude auth login', codex: 'codex login'};
function agentLoggedIn(agent) {
  const entry = agentAuth[agent];
  // The server owns the tri-state decision; absent status remains usable during startup.
  return !entry || entry.available !== false;
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
const authUsername = String(bootstrap.authUsername || '');
const readOnlyMode = accessRole !== 'admin';
const devMode = bootstrap.dev === true;   // dev-velocity #1b: subscribe to /api/dev-reload + auto-reload
const clientCapabilityState = Object.freeze({
  unscopedHostRequests: true,
});
function clientCanUseUnscopedHostRequests() {
  return clientCapabilityState.unscopedHostRequests === true;
}
function randomBrowserInstanceId() {
  try {
    if (crypto?.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto?.getRandomValues?.(bytes);
    const encoded = Array.from(bytes).map(value => value.toString(16).padStart(2, '0')).join('');
    if (encoded) return encoded;
  } catch (_) {}
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
const browserClientId = sessionScopedId('yolomux.browserClient', randomBrowserInstanceId);
let yolomuxFontsReadyPromise = null;
const homePath = bootstrap.homePath;
const repoRoot = bootstrap.repoRoot || '';
const serverHostname = bootstrap.serverHostname;
const cpuTopology = Object.freeze({
  logicalCpus: Math.max(0, Number(bootstrap.cpuTopology?.logical_cpus) || 0),
  physicalCores: Math.max(0, Number(bootstrap.cpuTopology?.physical_cores) || 0),
});
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
const tabMetaToggle = makeButton({id: 'tabMetaToggle', className: 'tab-meta-toggle', label: '#', pressed: true});
const logoutButton = document.getElementById('logoutButton');
const httpsWarning = document.getElementById('httpsWarning');
const fileExplorer = document.getElementById('fileExplorer');
const fileExplorerTree = document.getElementById('fileExplorerTree');
const fileExplorerPath = document.getElementById('fileExplorerPath');
const fileExplorerPathCopy = document.getElementById('fileExplorerPathCopy');
const fileExplorerClose = document.getElementById('fileExplorerClose');
const fileExplorerHiddenToggle = document.getElementById('fileExplorerHiddenToggle');
const fileExplorerRootModeButton = document.getElementById('fileExplorerRootMode');
const fileExplorerExpanded = new Set();
const fileExplorerPendingExpansions = new Set();
const fileExplorerHiddenStorageKey = 'yolomux.fileExplorer.showHidden';
const fileExplorerRootModeStorageKey = 'yolomux.fileExplorer.rootMode';
const fileExplorerTreeShowDatesStorageKey = 'yolomux.fileExplorer.treeShowDates.v1';
const fileExplorerTreeDateModeStorageKey = 'yolomux.fileExplorer.treeDateMode.v1';
const fileExplorerTreeDateModes = ['none', 'date', 'relative'];
const fileExplorerTreeSortStorageKey = 'yolomux.fileExplorer.treeSort.v1';
// v2 keeps one common schema while each fixed file surface owns its own choices. The v1
// Finder keys remain read-only migration inputs so existing browser preferences survive.
const fileExplorerViewSettingsStorageKey = 'yolomux.fileExplorer.viewSettings.v2';
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
function previewRendererStrategy(specification) {
  return Object.freeze({
    surfaceClasses: [],
    cleanup: cleanupStandardPreviewStrategy,
    signature: null,
    parse: null,
    ...specification,
  });
}
const PREVIEW_RENDERERS = Object.freeze([
  previewRendererStrategy({id: 'markdown', kind: 'markdown', extensions: ['.md', '.markdown'], textBacked: true, defaultMode: 'edit', language: 'markdown', surfaceClasses: ['markdown-body'], cleanup: cleanupMarkdownPreviewStrategy, signature: markdownPreviewStrategySignature, render: renderMarkdownPreviewStrategy}),
  previewRendererStrategy({id: 'html', kind: 'html', extensions: ['.html', '.htm'], textBacked: true, defaultMode: 'edit', language: 'xml', sandbox: true, surfaceClasses: ['html-preview-body'], cleanup: cleanupRetainedPreviewStrategy, signature: htmlPreviewStrategySignature, render: renderHtmlPreviewStrategy}),
  previewRendererStrategy({id: 'image', kind: 'image', mediaKind: 'image', extensions: ['.png', '.apng', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.avif'], textBacked: false, defaultMode: 'preview', raw: true, surfaceClasses: ['image-preview-body'], cleanup: cleanupRetainedPreviewStrategy, signature: rawMediaPreviewStrategySignature, render: renderImagePreviewStrategy, mimeByExtension: {
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
  }}),
  previewRendererStrategy({id: 'pdf', kind: 'pdf', mediaKind: 'pdf', extensions: ['.pdf'], textBacked: false, defaultMode: 'preview', raw: true, sandbox: true, surfaceClasses: ['pdf-preview-body'], cleanup: cleanupRetainedPreviewStrategy, signature: rawMediaPreviewStrategySignature, render: renderPdfPreviewStrategy, mimeByExtension: {'.pdf': 'application/pdf'}}),
  previewRendererStrategy({id: 'mermaid', kind: 'mermaid', mediaKind: 'mermaid', extensions: ['.mmd', '.mermaid'], textBacked: true, defaultMode: 'preview', language: 'mermaid', surfaceClasses: ['code-preview-body'], cleanup: cleanupMermaidPreviewStrategy, signature: mermaidPreviewStrategySignature, render: renderMermaidPreviewStrategy}),
  previewRendererStrategy({id: 'json-lines-table', kind: 'table', extensions: ['.jsonl', '.ndjson'], textBacked: true, defaultMode: 'preview', language: 'json', surfaceClasses: ['data-preview-body'], render: renderJsonLinesPreviewStrategy}),
  previewRendererStrategy({id: 'structured', kind: 'structured', extensions: ['.json', '.geojson', '.ipynb', '.yaml', '.yml', '.toml', '.xml', '.drawio', '.dio', '.excalidraw', '.ini', '.cfg', '.conf', '.env', '.properties', '.props'], textBacked: true, defaultMode: 'edit', surfaceClasses: ['data-preview-body'], parse: parseStructuredPreviewStrategy, parseByExtension: {
    '.json': parseJsonStructuredPreviewStrategy,
    '.geojson': parseGeoJsonStructuredPreviewStrategy,
    '.ipynb': parseNotebookStructuredPreviewStrategy,
    '.toml': parseTomlStructuredPreviewStrategy,
    '.xml': parseXmlStructuredPreviewStrategy,
    '.drawio': parseDrawioStructuredPreviewStrategy,
    '.dio': parseDrawioStructuredPreviewStrategy,
    '.excalidraw': parseExcalidrawStructuredPreviewStrategy,
  }, render: renderStructuredPreviewStrategy, languageByExtension: {
    '.json': 'json',
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
  }}),
  previewRendererStrategy({id: 'table', kind: 'table', extensions: ['.csv', '.tsv'], textBacked: true, defaultMode: 'edit', language: 'text', surfaceClasses: ['data-preview-body'], parse: parseDelimitedPreviewStrategy, delimiterByExtension: {'.csv': ',', '.tsv': '\t'}, render: renderDelimitedPreviewStrategy}),
  previewRendererStrategy({id: 'audio', kind: 'audio', mediaKind: 'audio', extensions: ['.mp3', '.wav', '.ogg', '.oga', '.flac', '.m4a', '.aac', '.opus'], textBacked: false, defaultMode: 'preview', raw: true, surfaceClasses: ['media-preview-body'], cleanup: cleanupRetainedPreviewStrategy, signature: rawMediaPreviewStrategySignature, render: renderNativeMediaPreviewStrategy, mimeByExtension: {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.opus': 'audio/opus',
  }}),
  previewRendererStrategy({id: 'video', kind: 'video', mediaKind: 'video', extensions: ['.mp4', '.m4v', '.webm', '.mov', '.mkv', '.ogv', '.3gp'], textBacked: false, defaultMode: 'preview', raw: true, surfaceClasses: ['media-preview-body'], cleanup: cleanupRetainedPreviewStrategy, signature: rawMediaPreviewStrategySignature, render: renderNativeMediaPreviewStrategy, mimeByExtension: {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.mkv': 'video/x-matroska',
    '.ogv': 'video/ogg',
    '.3gp': 'video/3gpp',
  }}),
  // Finder exposes Edit, Preview, and Diff as modes of one file tab, so generic text/code files need
  // the existing syntax-highlighted Preview surface too; commit snapshots reuse this same renderer.
  previewRendererStrategy({id: 'text', kind: 'text', extensions: ['.txt', '.log', '.trace', '.out', '.rst', '.adoc', '.asciidoc', '.diff', '.patch', '.dot', '.gv', '.puml', '.plantuml', '.srt', '.vtt'], textBacked: true, previewable: true, defaultMode: 'edit', surfaceClasses: ['code-preview-body'], render: renderCodePreviewStrategy, languageByExtension: {
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
  }}),
  previewRendererStrategy({id: 'unsupported-image', kind: 'unsupported', extensions: ['.tif', '.tiff', '.heic', '.heif'], textBacked: false, defaultMode: 'preview', raw: true, render: renderUnsupportedPreviewStrategy, fallbackTitleKey: 'preview.unsupported.image', mimeByExtension: {
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.heic': 'image/heic',
    '.heif': 'image/heif',
  }}),
  previewRendererStrategy({id: 'unsupported-document', kind: 'unsupported', extensions: ['.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx'], textBacked: false, defaultMode: 'preview', raw: true, render: renderUnsupportedPreviewStrategy, fallbackTitleKey: 'preview.unsupported.document', mimeByExtension: {
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  }}),
  previewRendererStrategy({id: 'unsupported-data', kind: 'unsupported', extensions: ['.sqlite', '.sqlite3', '.db', '.parquet', '.arrow', '.feather'], textBacked: false, defaultMode: 'preview', raw: true, render: renderUnsupportedPreviewStrategy, fallbackTitleKey: 'preview.unsupported.data', mimeByExtension: {
    '.sqlite': 'application/vnd.sqlite3',
    '.sqlite3': 'application/vnd.sqlite3',
    '.db': 'application/vnd.sqlite3',
    '.parquet': 'application/vnd.apache.parquet',
    '.arrow': 'application/vnd.apache.arrow.file',
    '.feather': 'application/vnd.apache.arrow.file',
  }}),
  previewRendererStrategy({id: 'unsupported-archive', kind: 'unsupported', extensions: ['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.rar'], textBacked: false, defaultMode: 'preview', raw: true, render: renderUnsupportedPreviewStrategy, fallbackTitleKey: 'preview.unsupported.archive', mimeByExtension: {
    '.zip': 'application/zip',
    '.tar': 'application/x-tar',
    '.gz': 'application/gzip',
    '.tgz': 'application/gzip',
    '.bz2': 'application/x-bzip2',
    '.xz': 'application/x-xz',
    '.7z': 'application/x-7z-compressed',
    '.rar': 'application/vnd.rar',
  }}),
  previewRendererStrategy({id: 'unsupported', kind: 'unsupported', extensions: [], textBacked: false, defaultMode: 'preview', render: renderUnsupportedPreviewStrategy}),
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
const fileState = new Map();  // path -> open-file content plus editor tab/owner/mode/blame/identity/open-promise state
const historicalFileState = new Map();  // exact filehistory item -> immutable commit-side editor state
const gitDiffTabState = new Map();  // exact gitdiff item -> frozen history snapshot, disclosures, and requests
const fileExplorerDirectoryRecords = new Map();  // normalized directory -> {signature, knownEntryNames}
const fileExplorerNewEntryUntil = new Map();
const fileExplorerRepoInfoCache = new Map();
const fileExplorerSessionFilesCache = new Map();
const fileExplorerFinderSessionFilesCache = new Map();
const terminalFileReferenceTargetCache = new Map();
const fileExplorerMemoryCacheLimit = 512;
const fileExplorerRefreshIdleMs = 1501;
const commandPaletteRecentKeyLimit = 100;
const notificationLastSentLimit = 512;
const pendingFileEditorFocus = new Set();
const paneViewState = new Map();  // layout item -> generic pane scroll state
const pendingPaneViewStateCaptures = new Set();
const fileEditorViewState = new Map();  // layout item -> CodeMirror scroll/selection state
const pendingFileEditorLineTargets = new Map();  // layout item -> line target to apply after async CodeMirror load
const fileEditorDiffExpandOverrides = new Map();  // layout item -> per-editor diff context expansion
const layoutUrlState = {
  pending: null,
  applied: false,
  refreshTimer: null,
  finderRootKindUnverified: false,
};
let activeFile = null;
let sharedImageViewerPath = null;
let fileExplorerRoot = null;
let filesystemRefreshInFlight = false;
let fileExplorerRepoInfoCacheLoaded = false;
let fileExplorerRootMode = readStoredFileExplorerRootMode();
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
  chat: 'chat',
  fileExplorer: 'file_explorer',
  uploads: 'uploads',
  performance: 'performance',
  cost: 'cost',
  github: 'github',
  yoagent: 'yoagent',
  yolo: 'yolo',
});
const DEFAULT_COLLAPSED_PREFERENCE_SECTION_IDS = Object.freeze([
  PREFERENCE_SECTION_IDS.general,
  PREFERENCE_SECTION_IDS.appearance,
  PREFERENCE_SECTION_IDS.performance,
  PREFERENCE_SECTION_IDS.cost,
  PREFERENCE_SECTION_IDS.notifications,
  PREFERENCE_SECTION_IDS.terminalEditor,
  PREFERENCE_SECTION_IDS.fileExplorer,
  PREFERENCE_SECTION_IDS.uploads,
]);
const LEGACY_PREFERENCE_SECTION_IDS_BY_ENGLISH_TITLE = Object.freeze({
  General: PREFERENCE_SECTION_IDS.general,
  Appearance: PREFERENCE_SECTION_IDS.appearance,
  Performance: PREFERENCE_SECTION_IDS.performance,
  'YO!cost': PREFERENCE_SECTION_IDS.cost,
  Notifications: PREFERENCE_SECTION_IDS.notifications,
  'Terminal / Editor': PREFERENCE_SECTION_IDS.terminalEditor,
  'File Explorer': PREFERENCE_SECTION_IDS.fileExplorer,
  Finder: PREFERENCE_SECTION_IDS.fileExplorer,
  'Uploads/Downloads': PREFERENCE_SECTION_IDS.uploads,
  GitHub: PREFERENCE_SECTION_IDS.github,
  'YO!agent': PREFERENCE_SECTION_IDS.yoagent,
  'YO!chat': PREFERENCE_SECTION_IDS.chat,
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
const tabberActivityState = {
  requestGeneration: 0,
  appliedGeneration: 0,
  loaded: false,
  request: null,
};
// One per-session owner for event-log HTTP repairs. SSE invalidations may arrive
// in bursts; retain at most one follow-up fetch after the readable log changes.
const eventLogRefreshRecords = new Map();
// per-repo collapse state for the Modified-files panel repo headers (keyed by repo path).
let changesRepoCollapsed = readStoredSet(changesRepoCollapsedStorageKey);
// Differ owns arbitrary selected refs. Finder has a separate fixed HEAD/current record below so a
// historical comparison can never repaint live working-tree annotations.
const fileExplorerSessionFilesState = {
  payload: {session: '', files: [], repos: [], errors: []},
  signature: '',
  loading: false,
  guard: makeGenerationGuard(),
  abortController: null,
};
const fileExplorerFinderSessionFilesState = {
  payload: {session: '', files: [], repos: [], errors: [], from_ref: 'HEAD', to_ref: 'current'},
  signature: '',
  loading: false,
  guard: makeGenerationGuard(),
  abortController: null,
};
// One program-wide owner for the pane the user explicitly clicked or typed in. Passive hover and
// auto-focus never change this; Finder, Differ, Tabber, and tmux menus consume this same state.
// A Finder/Differ click changes `item` but preserves the terminal context it is inspecting.
const explicitPaneFocusState = {
  item: '',
  tmuxSession: '',
};
// Finder's root synchronization and Differ's changed-files query are independent surfaces. Keep
// their explicit selections separate so choosing a Finder root never silently changes the Differ.
let fileExplorerFinderSelectedSession = '';
let fileExplorerChangesSelectedSession = '';
const fileExplorerSyncTargetRecords = new Map();
// The one user-owned disclosure record for Finder Sync. `true` means the user
// explicitly expanded the path; `false` means they explicitly collapsed it.
// Automatic sync expansion is deliberately not stored here.
const fileExplorerSyncUserExpansionState = new Map();
let fileExplorerSyncManualCollapseTargetKey = '';
let fileExplorerSyncManualCollapsedPaths = new Set();
let fileExplorerVisibleSyncSession = '';
let fileExplorerVisibleSyncRoot = '';
let fileExplorerLastInteractionAt = 0;
let fileExplorerRefreshDeferred = false;
const fileExplorerSelectedPaths = new Set();
let fileExplorerSelectionAnchor = null;
let fileExplorerSelectionLead = null;   // keyboard cursor (File-Explorer "lead" item); arrows move it, Shift+arrow extends anchor->lead
let fileExplorerViewSettings = readStoredFileExplorerViewSettings();
let fileExplorerIndexedDirs = readStoredFileExplorerIndexedDirs();
let fileExplorerIndexExcludePaths = new Set();
const fileExplorerIndexStatus = new Map();  // normalized indexed root -> 'building' | 'ready' | 'stale' | 'too_large' | 'error'
const fileExplorerIndexGeneration = new Map();  // normalized indexed root -> accepted backend lifecycle generation
const fileExplorerIndexPublishedGeneration = new Map();  // normalized indexed root -> last-seen progressive published generation (drives Quick Open re-query as BFS publishes rows)
const fileIndexStatusPollRoots = new Set();  // normalized indexed roots still building
const fileIndexPartialWarningRoots = new Set();  // warned once per root until it regains full coverage
let applyingIndexedDirsSetting = false;  // guard: reconciling the set FROM the setting must not write it back
const tabLastActivatedAt = new Map();  // layout item -> last-activated timestamp (ms) for per-pane LRU tab eviction
let diffRefFrom = readStoredDiffRef(diffRefFromStorageKey, 'HEAD');  // C6: global default FROM (per-repo fallback)
let diffRefTo = readStoredDiffRef(diffRefToStorageKey, 'current');   // C6: global default TO (per-repo fallback)
let diffRefsByRepo = readStoredDiffRefsByRepo();  // C6: {repoPath: {from, to}} — per-repo overrides
// Legacy URL compatibility value while old clients are still in circulation. Live panels are
// identified by one of the fixed triplet item IDs below; do not use this to render a panel.
let fileExplorerMode = readStoredFileExplorerMode();
let sidePaneLayoutWasConstrained = false;
const commandPaletteState = {
  node: null,
  query: '',
  index: 0,
  items: [],
  // An empty peer pane can request that its next quick-open lands in that exact slot. The palette
  // remains the one chooser; this is only its transient placement context, never a second opener.
  targetSlot: '',
};
let keyboardShortcutsNode = null;
let pendingGlobalShortcutChord = null;
let pendingGlobalShortcutChordTimer = null;
const globalShortcutChordTimeoutMs = 4000;
let commandPaletteMode = 'command';
const commandPaletteRecentKeys = new Map();
let commandPaletteRecentSequence = 0;
// Fill workspace is a temporary pane-layout view. Keep the complete pre-fill tree here so Restore
// returns every tab, split, and placeholder exactly as the user left it.
let filledWorkspaceLayout = null;
const fileQuickOpenState = {
  root: '',
  candidates: [],
  loading: false,
  indexWarming: false,
  // The worst freshness record any answering search root returned, from the one derivation in
  // fileIndexFreshnessFromPayload(). Null means every answering root vouched for its snapshot.
  freshness: null,
  error: '',
  requestId: 0,
  debounce: null,
  abortController: null,
  // The active search text the delta cursors below belong to, and the per-root incremental-read state
  // for the CURRENT requestId. Each searched root keeps one opaque cursor + `more` flag + its opaque
  // scope digest (so a path-free search_progress signal correlates back to the root it names) so the
  // server can stream committed match deltas instead of the client re-issuing the whole query.
  deltaQuery: '',
  deltaRoots: new Map(),
};
let tabsMenuSearchText = '';
let fileExplorerShortcutRestoreSlots = null;
let clientSettingsPayload = bootstrap.settingsPayload || {};
let clientSettings = clientSettingsPayload.settings || {};
let clientSettingsDefaults = clientSettingsPayload.defaults || {};
let clientSettingsMtimeNs = Number(clientSettingsPayload.mtime_ns || 0);
let clientSettingsMetadataDeferred = clientSettingsPayload.deferred_metadata === true;
let clientSettingsMetadataRefreshPromise = null;
let clientSettingsMetadataRefreshTimer = null;
const activitySummaryEnabled = bootstrap.activitySummary?.enabled === true;
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
let terminalThemeMode = initialSetting('appearance.terminal_theme', defaultTerminalTheme);
let dateTimeHourCycle = initialSetting('appearance.date_time_hour_cycle') === '12' ? '12' : '24';
fileEditorThemeMode = readConfiguredEditorScheme();
fileEditorAutosaveEnabled = boolSetting('editor.autosave', true);
fileEditorAutosaveDelaySeconds = numberSetting('editor.autosave_delay_seconds');
let yoloRulesPayload = bootstrap.yoloRulesPayload || {};
const terminals = new Map();
const ensureSessionPromises = new Map();
const terminalStartupPromises = new Map();
const tmuxSessionLifecycleRecords = new Map();
let tmuxSessionLifecycleGeneration = 0;
let tmuxTopologyEpoch = 0;
const pendingTmuxSessionGraceMs = 30000;
const tmuxSessionLifecyclePendingPhases = new Set(['creating', 'renaming-in']);
const tmuxSessionLifecycleBlockedPhases = new Set(['renaming-out', 'killing', 'retired']);
let tmuxSessionMutationSerial = 0;
let tmuxSessionMutationCurrent = null;
const panelNodes = new Map();
const resizeObservers = new Map();
const transcriptLifecycleScopes = new Map();
const summaryLifecycleScopes = new Map();
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
const startupHelperIndexStorageKey = 'yolomux.startupHelper.index.v1';
// Legacy merged-pane sub-tab compatibility only. YO!info and YO!agent now have separate virtual tabs.
const infoSubTabStorageKey = 'yolomux.infoPanel.activeSubTab.v1';
const infoLookbackHoursStorageKey = 'yolomux.infoPanel.lookbackHours.v1';
const transcriptPreviewMessages = 200;
let remoteResizeDelayMs = initialSetting('performance.remote_resize_delay_ms');
// The latest watched-PR payload lives here; transition status and notification throttles share one PR-keyed record owner.
let watchedPrsData = {watched_prs: [], truncated: 0, invalid: []};
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
let popoverHideDelayMs = initialSetting('performance.popover_hide_delay_ms');
const fileEditorScrollSyncSuppressMs = 150;
const serverWatchRootsState = {
  signature: '',
  inFlight: false,
  request: null,
  activeKey: '',
  scheduledKey: '',
  completedForceKeys: new Map(),
  registrationPending: false,
  registered: false,
  syncedAt: 0,
  watchDiffPromise: null,
  watchDiffTrailing: null,
  timer: null,
  timerDelay: null,
  pendingOptions: {},
};
let fileExplorerFilesystemWatchToken = '';
let fileExplorerFilesystemPushToken = '';
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
const sidePaneDefaultWidthPercent = 22;
const sidePaneMaxWidthPercent = 100 / 3;
const minNonFileExplorerSplitPercent = 30;
// Phone layout is deliberately a touch-device policy: a resized desktop must preserve its layout.
// 760px covers portrait phones and narrow iPad split view; 960x520 catches phone landscape without
// collapsing a full-size tablet.
const mobileSinglePaneMaxWidthPx = 760;
const mobileSinglePaneLandscapeMaxWidthPx = 960;
const mobileSinglePaneLandscapeMaxHeightPx = 520;
const mobileSinglePaneTabLimit = 3;
// This is a conservative touch breakpoint: narrow tablets use one column before a two-pane
// layout becomes cramped, even though desktop users may manually create 300px panes.
const narrowTouchSingleColumnMaxWidthPx = 680;
const tabletDesktopLayoutMinWidthPx = 900;
const defaultLayoutMode = 'split';
const layoutModeValues = ['single', 'split', 'grid'];
const legacyLayoutModeValues = [...layoutModeValues, 'wall'];
const layoutBoundaryDropFraction = 0.08;
const layoutBoundaryDropMinPx = 28;
const layoutBoundaryDropMaxPx = 64;
// Dockview keeps a small gutter between neighboring leaf groups. Directional tab actions compare
// their rendered edges with this shared allowance instead of assuming touching DOM rectangles.
const directionalPaneAdjacencyTolerancePx = 8;
const MIN_SPLIT_PANE_WIDTH_FALLBACK_PX = 300;
const MIN_SPLIT_PANE_HEIGHT_FALLBACK_PX = 220;
const minSplitPercent = 5;
const maxSplitPercent = 95;
const infoItemId = '__info__';
// Localized brand tab labels — functions (not consts) so a runtime language switch repaints them via
// rerenderForLocale(); `t` resolves lazily at call time (it's defined in 05_i18n, loaded after this).
function infoTabLabel() { return t('brand.tab.info'); }
const yoagentItemId = '__yoagent__';
const legacyYosupItemId = '__yosup__';
function yoagentTabLabel() { return t('brand.tab.agent'); }
const chatItemId = '__chat__';
function chatTabLabel() { return t('brand.tab.chat'); }
// Legacy share/deeplink compatibility only. YO!info and YO!agent are separate tabs.
let infoPanelSubTab = readStoredInfoSubTab();
const finderItemId = '__finder__';
const differItemId = '__differ__';
const tabberItemId = '__tabber__';
const fileExplorerItemIds = Object.freeze([finderItemId, differItemId, tabberItemId]);
const fileSurfaceItems = fileExplorerItemIds;
const paneRoleGeneric = 'generic';
const paneRoleSide = 'side';
const paneSideLeft = 'left';
const paneSideRight = 'right';
const paneSideValues = Object.freeze([paneSideLeft, paneSideRight]);
const panePlacementGenericOnly = 'generic-only';
const panePlacementSideAllowed = 'side-allowed';
const panePlacementSideRequired = 'side-required';
const paneRoleParamPrefix = '@side-';
const genericPaneRoleDefinition = Object.freeze({
  kind: paneRoleGeneric,
  side: null,
  controls: 'standard',
  tabSizing: 'preference',
  preserveWidth: false,
  maxViewportFraction: 1,
  outermost: false,
});
// A Side Pane is deliberately a specialization of the generic pane definition. Keep every shared
// behavior inherited here; consumers ask for a role definition instead of restating side cases.
const sidePaneRoleDefinition = Object.freeze({
  ...genericPaneRoleDefinition,
  kind: paneRoleSide,
  controls: 'minimize-only',
  tabSizing: 'intrinsic',
  preserveWidth: true,
  maxViewportFraction: 1 / 3,
  outermost: true,
});
const paneRoleDefinitions = Object.freeze({
  [paneRoleGeneric]: genericPaneRoleDefinition,
  [paneRoleSide]: sidePaneRoleDefinition,
});
const legacyFileExplorerItemId = '__files__';
// Compatibility for older helpers while their callers migrate. This is a live Finder identity,
// never the legacy __files__ layout item.
const fileExplorerItemId = finderItemId;
const fileExplorerTripletRegistry = Object.freeze({
  [finderItemId]: Object.freeze({view: 'finder', key: 'finder', icon: 'finder'}),
  [differItemId]: Object.freeze({view: 'differ', key: 'differ', icon: 'changes'}),
  [tabberItemId]: Object.freeze({view: 'tabber', key: 'tabber', icon: 'tab-meta'}),
});
function fileExplorerViewForItem(item) {
  return fileExplorerTripletRegistry[item]?.view || '';
}
function fileExplorerItemForView(view) {
  const normalized = String(view || '').toLowerCase();
  if (normalized === 'files' || normalized === 'finder') return finderItemId;
  if (normalized === 'diff' || normalized === 'differ' || normalized === 'changes') return differItemId;
  if (normalized === 'tabber') return tabberItemId;
  return '';
}
function isFileExplorerItem(item) {
  return fileExplorerItemIds.includes(item);
}
const isFileSurfaceItem = isFileExplorerItem;
function fileExplorerItemLabel(item) {
  const view = fileExplorerViewForItem(item);
  if (view === 'differ') return t('brand.tab.changes');
  if (view === 'tabber') return t('tabber.title');
  return fileExplorerLabel();
}
const searchHistoryItemId = '__search_history__';
function searchHistoryTabLabel() { return t('tab.searchHistory'); }
const prefsItemId = '__prefs__';
const debugPaneItemId = '__debug__';
const yocostItemId = '__yocost__';
const legacyYoCostItemAliases = Object.freeze(['cost', 'yocost', 'yo!cost', 'yo-cost', yocostItemId]);
let legacyYoCostMigrationRequested = false;
function isLegacyYoCostItemParam(item) { return legacyYoCostItemAliases.includes(String(item || '')); }
const FILE_MENU_PANEL_DEFINITIONS = Object.freeze([
  {itemId: finderItemId, preferenceSectionId: PREFERENCE_SECTION_IDS.fileExplorer},
  {itemId: searchHistoryItemId},
  {itemId: infoItemId},
  {itemId: yoagentItemId, preferenceSectionId: PREFERENCE_SECTION_IDS.yoagent},
  {itemId: debugPaneItemId, preferenceSectionId: PREFERENCE_SECTION_IDS.performance},
  {itemId: chatItemId, preferenceSectionId: PREFERENCE_SECTION_IDS.chat},
]);
const FILE_MENU_PREFERENCE_SECTION_ORDER = Object.freeze([
  ...FILE_MENU_PANEL_DEFINITIONS.map(item => item.preferenceSectionId).filter(Boolean),
]);
const emptyPaneParam = '__empty_pane__';
const intentionalEmptyPaneParam = '__empty_pane_v2__';
const fileEditorItemPrefix = 'file:';
const fileEditorCopyItemPrefix = 'filecopy:';
const fileEditorDiffPreviewItemPrefix = 'filediff:';
const historicalFileEditorItemPrefix = 'filehistory:';
const gitDiffItemPrefix = 'gitdiff:';
const gitDiffHistoryPageSize = 50;
const imageViewerItemPrefix = 'image:';
const chatMediaItemPrefix = 'chat-media:';
let fileEditorCopyItemSeq = 0;
function urlFlagEnabled(name) {
  try {
    return new URLSearchParams(location.search || '').get(name) === '1';
  } catch (_) {
    return false;
  }
}

function browserUsesCoarsePointer() {
  if (urlFlagEnabled('mobile')) return true;
  const media = typeof window.matchMedia === 'function' ? window.matchMedia('(pointer: coarse)') : null;
  if (media?.matches === true) return true;
  const navigatorValue = globalThis.navigator || {};
  return Number(navigatorValue.maxTouchPoints || 0) > 0
    && /Android|iPad|iPhone|iPod|Mobile/i.test(String(navigatorValue.userAgent || navigatorValue.platform || ''));
}

let browserCursorHoverObserved = false;

function browserHasCursorHover(event = null) {
  const pointerType = String(event?.pointerType || '');
  if (pointerType === 'mouse' || pointerType === 'pen') {
    // Some tablet browsers emit the real mouse/trackpad event before `any-hover` updates.
    // Remembering that capability keeps the follow-on focus work on the same gesture consistent.
    browserCursorHoverObserved = true;
    return true;
  }
  if (pointerType === 'touch') return false;
  const media = typeof window.matchMedia === 'function' ? window.matchMedia('(any-hover: hover)') : null;
  if (media?.matches === true) return true;
  if (browserCursorHoverObserved) return true;
  // Keep desktop/fallback browsers functional when the media feature is unavailable. A touch-first
  // browser with no hover-capable pointer must not leave a hover popup permanently open.
  return !browserUsesCoarsePointer();
}

function autoFocusCanFollowCursor(event = null) {
  return autoFocusEnabled && browserHasCursorHover(event);
}

function browserUsesTabletViewport() {
  const navigatorValue = globalThis.navigator || {};
  const userAgent = String(navigatorValue.userAgent || '');
  const platform = String(navigatorValue.platform || '');
  const touchPoints = Number(navigatorValue.maxTouchPoints || 0);
  return /iPad|Tablet/i.test(userAgent)
    || (/Macintosh|MacIntel/i.test(platform) && touchPoints > 1)
    || (/Android/i.test(userAgent) && !/Mobile/i.test(userAgent));
}

function tabletUsesDesktopLayout(viewport = nativeViewport()) {
  if (!browserUsesCoarsePointer() || !browserUsesTabletViewport()) return false;
  const width = Math.max(0, Number(viewport?.width ?? viewport?.w) || 0);
  return width >= tabletDesktopLayoutMinWidthPx;
}

function fileExplorerUsesNormalTabMovement() {
  return !sidePanesAvailable();
}

function phoneLikeMobileViewport(viewport = nativeViewport()) {
  if (!browserUsesCoarsePointer() || browserUsesTabletViewport()) return false;
  const width = Math.max(0, Number(viewport?.width ?? viewport?.w) || 0);
  const height = Math.max(0, Number(viewport?.height ?? viewport?.h) || 0);
  return width <= mobileSinglePaneMaxWidthPx
    || (width <= mobileSinglePaneLandscapeMaxWidthPx && height <= mobileSinglePaneLandscapeMaxHeightPx);
}

function mobileSinglePaneMode(viewport = nativeViewport()) {
  return phoneLikeMobileViewport(viewport);
}

function narrowTouchSingleColumnViewport(viewport = nativeViewport()) {
  if (!browserUsesCoarsePointer()) return false;
  const width = Math.max(0, Number(viewport?.width ?? viewport?.w) || 0);
  const tablet = browserUsesTabletViewport();
  return phoneLikeMobileViewport(viewport)
    || (tablet && width < minSplitPaneWidthPx() * 2)
    || (!tablet && width <= narrowTouchSingleColumnMaxWidthPx);
}

function narrowSingleColumnMode(viewport = nativeViewport()) {
  return narrowTouchSingleColumnViewport(viewport);
}
const debugModeExplicitUrlEnabled = urlFlagEnabled('debug');
let debugModeEnabled = debugModeExplicitUrlEnabled;
const jsDebugEventLimit = 200;
const jsDebugRenderDebounceMs = 500;
let jsDebugEventSeq = 0;
let jsDebugEvents = [];
let apiDebugRequestSequence = 0;
function newClientJourneyId(kind = 'journey') {
  const prefix = String(kind || 'journey').replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 24) || 'journey';
  const identity = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `j-${prefix}-${identity}`.slice(0, 96);
}
const reloadClientJourneyId = newClientJourneyId('reload');
const pageLoadProfileState = {
  bundleEvalStartedAt: pageLoadBundleEvalStartedAt,
  bundleEvalEndedAt: 0,
  firstApiStartedAt: null,
  lastApiStartedAt: null,
  apiCount: 0,
  activeApiCount: 0,
  maxConcurrency: 0,
  emitted: false,
};
let jsDebugEventCaptureInstalled = false;
let jsDebugRenderTimer = null;
let jsDebugRenderForce = false;
let jsDebugRenderDragDeferred = false;
const clientPerfCounterLimit = 80;
const clientPerfLongTaskSampleLimit = 40;
const clientPerfDurableExemplarLimit = 20;
const clientPerfCounters = new Map();
let clientPerfLongTaskSamples = [];
let clientPerfLongTaskObserverInstalled = false;
let clientPerfLongTaskDurableCount = 0;
let clientPerfInteractionObserverInstalled = false;
let clientPerfInteractionDurableCount = 0;
let clientPerfInteractionMaximumMs = 0;
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
function gitDiffItemFor(path) {
  const normalized = normalizeDirectoryPath(String(path || ''));
  return normalized ? `${gitDiffItemPrefix}${encodeURIComponent(normalized)}` : '';
}
function gitDiffItemPath(item) {
  const text = String(item || '');
  if (!text.startsWith(gitDiffItemPrefix)) return null;
  const path = safeDecodeURIComponent(text.slice(gitDiffItemPrefix.length));
  return path.startsWith('/') ? normalizeDirectoryPath(path) : null;
}
function historicalFileEditorItemFor(path, fromRef, toRef) {
  const normalizedPath = String(path || '').trim();
  const normalizedFrom = String(fromRef || '').trim();
  const normalizedTo = String(toRef || '').trim();
  if (!normalizedPath.startsWith('/') || !normalizedFrom || !normalizedTo) return '';
  return `${historicalFileEditorItemPrefix}${encodeURIComponent(JSON.stringify([normalizedPath, normalizedFrom, normalizedTo]))}`;
}
function historicalFileEditorIdentity(item) {
  const text = String(item || '');
  if (!text.startsWith(historicalFileEditorItemPrefix)) return null;
  try {
    const parsed = JSON.parse(decodeURIComponent(text.slice(historicalFileEditorItemPrefix.length)));
    if (!Array.isArray(parsed) || parsed.length !== 3) return null;
    const [path, fromRef, toRef] = parsed.map(value => String(value || '').trim());
    if (!path.startsWith('/') || !fromRef || !toRef) return null;
    return {path, fromRef, toRef};
  } catch (_error) {
    return null;
  }
}
function isHistoricalFileEditorItem(item) { return historicalFileEditorIdentity(item) !== null; }
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
const virtualPanelTabDefaults = Object.freeze({
  aliases: [], sortRank: 0, detail: () => '', rowHtml: (item, options) => paneInfoTabHtml(item, options), canPopout: false, popoutRenderer: null, openPopout: null, renderAttached: null,
  cleanup: null, relocalize: null, focusSearch: null, icon: 'document', minWidth: () => minSplitPaneWidthPx(), prunePriority: () => 0});
function virtualPanelTabType(spec) {
  const prefixes = Array.isArray(spec.prefixes) && spec.prefixes.length ? spec.prefixes : [spec.prefix].filter(Boolean), aliases = spec.aliases || (prefixes.length ? [] : [spec.key, ...(spec.id ? [spec.id] : [])]), label = spec.label;
  return {...virtualPanelTabDefaults, ...spec, aliases, prefixes,
    match: spec.match || (spec.id ? item => item === spec.id : item => typeof item === 'string' && prefixes.some(prefix => item.startsWith(prefix))), shortLabel: spec.shortLabel || label,
    terminalTitle: spec.terminalTitle || (() => t('tab.unavailableFor', {name: label()})), param: spec.param || (() => spec.key),
    popoutDisabledReason: spec.popoutDisabledReason || (() => t('pane.popout.interactiveDisabled', {name: label()})), className: spec.className || (() => spec.key)};
}
function diffFileTabLabel(item, path) {
  if (!isHistoricalFileEditorItem(item) && editorViewModeFor(path, item) !== 'diff') return basenameOf(path);
  const state = fileEditorTabState(item);
  const repo = normalizeDirectoryPath(state?.gitRoot || state?.diffRepo || '');
  const relativePath = repo && pathIsInsideDirectory(path, repo) ? pathRelativeToDirectory(path, repo) : '';
  const parent = relativePath.includes('/') ? dirnameOf(relativePath) : '';
  const scope = repo ? [basenameOf(repo), parent].filter(Boolean).join('/') : compactHomePath(dirnameOf(path));
  return `Δ${basenameOf(path)}${scope ? `;${scope}` : ''}`;
}

function filePanelTabType({key, prefix, prefixes = null, shortLabel, terminalTitle, className, sortRank, focusSearch = null}) {
  const itemPrefixes = Array.isArray(prefixes) && prefixes.length ? prefixes : [prefix];
  return virtualPanelTabType({
    key,
    prefix,
    prefixes: itemPrefixes,
    match: item => typeof item === 'string' && itemPrefixes.some(itemPrefix => item.startsWith(itemPrefix)),
    label: item => diffFileTabLabel(item, fileItemPath(item)),
    shortLabel,
    terminalTitle,
    sortRank,
    param: item => item,
    detail: item => compactHomePath(fileItemPath(item)),
    rowHtml: (item, options) => fileEditorPaneTabHtml(item, options),
    createPanel: item => createFileEditorPanel(item),
    relocalize: (item, panel) => relocalizeFileEditorPanel(panel, item),
    canPopout: item => {
      if (isHistoricalFileEditorItem(item)) return false;
      const path = fileItemPath(item);
      return Boolean(path && editorPreviewModeAvailable(path, fileEditorStateForItem(path, item)));
    },
    popoutDisabledReason: item => t(isHistoricalFileEditorItem(item)
      ? 'editor.historicalReadOnly'
      : fileItemPath(item)
        ? 'pane.popout.filePreviewRequired'
        : 'pane.popout.filePathRequired'),
    openPopout: item => {
      if (isHistoricalFileEditorItem(item)) return false;
      const path = fileItemPath(item);
      return Boolean(path && openFilePreviewPopout(path, document.getElementById(panelDomId(item))));
    },
    focusSearch,
    className,
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx(),
    prunePriority: () => 1,
  });
}
const TAB_TYPES = [
  virtualPanelTabType({
    // YO!info and YO!agent are independent virtual tabs. Legacy yoagent/yosup aliases
    // resolve to the standalone YO!agent item below.
    key: 'info',
    id: infoItemId,
    aliases: ['info', 'info2', 'yo-info2', 'yoinfo2', infoItemId, '__info2__'],
    match: item => item === infoItemId || item === '__info2__',
    label: () => infoTabLabel(),
    sortRank: 0,
    detail: () => t('menu.file.info.detail'),
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
    panePlacement: panePlacementSideAllowed,
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'yoagent',
    id: yoagentItemId,
    aliases: ['yoagent', 'yo!agent', 'yo-agent', 'yosup', 'yo', 'sup', yoagentItemId, legacyYosupItemId],
    match: item => item === yoagentItemId || item === legacyYosupItemId,
    label: () => yoagentTabLabel(),
    sortRank: 0.1,
    detail: () => t('menu.file.yoagent.detail'),
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
    icon: 'robot',
    panePlacement: panePlacementSideAllowed,
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'chat',
    id: chatItemId,
    aliases: ['chat', 'yochat', 'yo!chat', 'yo-chat', chatItemId],
    label: () => chatTabLabel(),
    sortRank: 0.2,
    detail: () => t('menu.file.chat.detail'),
    createPanel: () => createChatPanel(),
    renderAttached: () => mountChatPanel(),
    cleanup: () => clearChatLifecycle({destroy: true, keepalive: true}),
    relocalize: (_item, panel) => relocalizeChatPanel(panel),
    focusSearch: (_item, panel) => openChatSearch(panel),
    className: () => 'chat',
    icon: 'chat-bubble',
    panePlacement: panePlacementSideAllowed,
    minWidth: () => rootCssLengthPx('--info-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'chat-media',
    prefix: chatMediaItemPrefix,
    label: item => chatMediaLabel(chatMediaUrlForItem(item)),
    shortLabel: () => t('popover.kind.image'),
    sortRank: 0.21,
    param: item => item,
    detail: item => chatMediaUrlForItem(item),
    createPanel: item => createChatMediaPanel(item),
    relocalize: (item, panel) => relocalizeChatMediaPanel(panel, item),
    className: () => 'chat-media',
    icon: 'image',
    minWidth: () => rootCssLengthPx('--file-editor-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'finder',
    id: finderItemId,
    aliases: [legacyFileExplorerItemId, 'files', 'finder', finderItemId],
    label: () => fileExplorerItemLabel(finderItemId),
    sortRank: 0.5,
    detail: () => compactHomePath(fileExplorerRoot || homePath || '/'),
    rowHtml: (item, options) => fileExplorerPaneTabHtml(item, options),
    createPanel: item => createFileExplorerPanel(item),
    relocalize: () => relocalizeFileExplorerPanels(),
    className: () => 'file-explorer file-explorer-finder',
    icon: 'finder',
    panePlacement: panePlacementSideRequired,
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'differ',
    id: differItemId,
    aliases: ['changes', '__changes__', 'diff', 'differ', differItemId],
    label: () => fileExplorerItemLabel(differItemId),
    sortRank: 0.51,
    detail: () => t('brand.tab.changes'),
    createPanel: item => createFileExplorerPanel(item),
    relocalize: () => relocalizeFileExplorerPanels(),
    className: () => 'file-explorer file-explorer-differ',
    icon: 'changes',
    panePlacement: panePlacementSideRequired,
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'tabber',
    id: tabberItemId,
    aliases: ['tabber', tabberItemId],
    label: () => fileExplorerItemLabel(tabberItemId),
    sortRank: 0.52,
    detail: () => t('tabber.description'),
    createPanel: item => createFileExplorerPanel(item),
    relocalize: () => relocalizeFileExplorerPanels(),
    className: () => 'file-explorer file-explorer-tabber',
    icon: 'tab-meta',
    panePlacement: panePlacementSideRequired,
    minWidth: () => rootCssLengthPx('--file-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'search-history',
    id: searchHistoryItemId,
    aliases: ['search', 'history', 'run-history', 'search-history', searchHistoryItemId],
    label: () => searchHistoryTabLabel(),
    shortLabel: () => t('common.search'),
    sortRank: 0.6,
    detail: () => t('searchHistory.detail'),
    rowHtml: (item, options) => searchHistoryPaneTabHtml(item, options),
    createPanel: () => createSearchHistoryPanel(),
    renderAttached: () => loadSearchHistoryPanelData({silent: true}),
    relocalize: (_item, panel) => renderSearchHistoryPanel(panel),
    focusSearch: (_item, panel) => focusPanelSearchInput(panel, '[data-search-history-query]', {panelSelector: '.search-history-panel', select: true}),
    className: () => 'search-history-item',
    icon: 'document',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'preferences',
    id: prefsItemId,
    aliases: ['prefs', 'preferences', prefsItemId],
    label: () => t('common.preferences'),
    shortLabel: () => t('tab.preferences.short'),
    sortRank: 0.65,
    param: () => 'prefs',
    detail: () => compactHomePath(settingsConfigPath()),
    rowHtml: (item, options) => preferencesPaneTabHtml(item, options),
    createPanel: () => createPreferencesPanel(),
    relocalize: () => renderPreferencesPanels({force: true}),
    focusSearch: (_item, panel) => focusPreferencesSearch(panel, {select: true}),
    className: () => 'preferences-item',
    icon: 'gear',
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'debug',
    id: debugPaneItemId,
    aliases: ['debug', 'js-debug', 'jsdebug', debugPaneItemId],
    label: () => t('tab.debug'),
    shortLabel: () => t('tab.debug.short'),
    sortRank: 0.7,
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
    icon: 'chart',
    panePlacement: panePlacementSideAllowed,
    minWidth: () => rootCssLengthPx('--preferences-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
  virtualPanelTabType({
    key: 'git-diff',
    prefix: gitDiffItemPrefix,
    label: item => gitDiffTabLabel(item),
    shortLabel: () => t('contextmenu.showDiff'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('contextmenu.showDiff')}),
    sortRank: 0.73,
    param: item => item,
    detail: item => compactHomePath(gitDiffItemPath(item) || ''),
    createPanel: item => createGitDiffPanel(item),
    renderAttached: item => renderGitDiffPanel(item),
    cleanup: item => cleanupGitDiffTab(item),
    relocalize: (item, panel) => relocalizeGitDiffPanel(item, panel),
    className: () => 'git-diff-item',
    icon: 'changes',
    minWidth: () => rootCssLengthPx('--changes-pane-min-inline-size') || minSplitPaneWidthPx(),
  }),
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
    prefixes: [fileEditorItemPrefix, fileEditorCopyItemPrefix, fileEditorDiffPreviewItemPrefix, historicalFileEditorItemPrefix],
    shortLabel: () => t('common.edit'),
    terminalTitle: () => t('tab.unavailableFor', {name: t('popover.kind.text')}),
    sortRank: 0.75,
    className: () => 'file-editor-item',
    focusSearch: (_item, panel) => focusFileEditorSearch(panel),
  }),
];
function tabTypeForItem(item) { return TAB_TYPES.find(type => type.match(item)) || null; }
function paneRoleDefinition(kind = paneRoleGeneric, side = null) {
  const normalizedKind = kind === paneRoleSide && paneSideValues.includes(side) ? paneRoleSide : paneRoleGeneric;
  const definition = paneRoleDefinitions[normalizedKind] || genericPaneRoleDefinition;
  return normalizedKind === paneRoleSide ? Object.freeze({...definition, side}) : definition;
}
function panePlacementForItem(item) {
  return tabTypeForItem(item)?.panePlacement || panePlacementGenericOnly;
}
function paneRoleAllowsItem(role, item, options = {}) {
  if (!isLayoutItem(item) && !(options.allowCandidate === true && tabTypeForItem(item))) return false;
  const definition = paneRoleDefinition(role?.kind || role?.paneRole, role?.side);
  const placement = panePlacementForItem(item);
  if (definition.kind === paneRoleSide) return placement !== panePlacementGenericOnly;
  if (placement !== panePlacementSideRequired) return true;
  return options.allowRequiredInGeneric === true;
}
function tabTypeForParam(value) {
  const text = String(value || '');
  return TAB_TYPES.find(type => {
    if ((type.aliases || []).includes(text)) return true;
    const prefixes = Array.isArray(type.prefixes) && type.prefixes.length ? type.prefixes : [type.prefix].filter(Boolean);
    return prefixes.some(prefix => text.startsWith(prefix));
  }) || null;
}
function tabTypeParam(type, item) { return typeof type?.param === 'function' ? type.param(item) : type?.param; }
function isYoagentItem(item) { return tabTypeForItem(item)?.key === 'yoagent'; }
function isChatMediaItem(item) { return tabTypeForItem(item)?.key === 'chat-media'; }
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
  if (typeof item === 'string' && item.startsWith(historicalFileEditorItemPrefix)) return historicalFileEditorIdentity(item)?.path || null;
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
  return /(Macintosh|MacIntel|Mac OS|macOS|\bMac\b|iPad|iPhone|iPod)/i.test(browserPlatformText());
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

// Test/bootstrap compatibility only. File surfaces no longer render this bespoke close class;
// their pane headers use the shared minimize/expand controls.
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
const dynamicVirtualLayoutItems = new Set();
function virtualTabItems() {
  return [infoItemId, yoagentItemId, chatItemId, ...fileExplorerItemIds, searchHistoryItemId, prefsItemId, debugPaneItemId, ...dynamicVirtualLayoutItems];
}
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [...virtualTabItems(), ...visibleSessions];
let layoutSlots = initialLayoutSlots();
let activeSessions = sessionsFromLayout();
const transcriptMetadataState = {
  payload: {},
  loading: false,
  loaded: false,
  error: null,
  request: null,
  // Server-stamped identity of the build the rendered model came from, and the highest generation
  // the server has told us to expect. A forced refresh is answered from the server's cache, so the
  // bytes it returns are always older than the request; `pendingGeneration` names the build that
  // will actually observe the state the caller asked about, and it arrives over client-events.
  // Without this pair the only way to know whether a render reflected a fact was to wait and hope.
  //
  // Both numbers count builds inside ONE server process and restart at zero in its replacement, so
  // neither means anything without `epoch`. Retaining a bare 50 across a server swap made the
  // replacement's cache -- generation 0, built before the request -- look like an already-observed
  // build, and a forced post-mutation refresh resolved as success without ever reading the
  // generation it was promised.
  //
  // The epoch is an opaque equality partition, never an ordering: two generations are comparable
  // only when their epochs are equal, and nothing here may infer that one epoch came after another.
  epoch: '',
  previousEpoch: '',
  generation: 0,
  pendingGeneration: 0,
  // Every non-apply outcome, with a machine-readable reason. A dropped payload used to be a bare
  // `false` that no caller read, so a metadata refresh that silently declined to apply looked
  // exactly like one that had nothing to say.
  lastApply: null,
  guard: makeGenerationGuard(),
};
// One metadata build's identity, read as ONE object. A generation without its epoch is an index
// into a sequence that may already be gone, so an identity is never reassembled from independent
// fields: either the server shipped both together or this client has no identity for the payload.
function sessionMetadataIdentity(value) {
  const epoch = String(value?.epoch || '');
  const generation = Number(value?.generation);
  if (!epoch || !Number.isSafeInteger(generation) || generation < 0) return null;
  return {epoch, generation};
}
function sessionMetadataPayloadIdentity(payload) {
  return sessionMetadataIdentity(payload?.metadata_identity);
}
// The build a forced read must wait for. Generation zero is not a build identity -- every payload
// already satisfies it -- so a force that is offered zero has been told no build was accepted.
function forcedSessionMetadataTarget(payload) {
  const pending = sessionMetadataIdentity(payload?.cache?.pending_identity);
  return pending && pending.generation > 0 ? pending : null;
}
function noteSessionMetadataPendingIdentity(payload) {
  const pending = sessionMetadataIdentity(payload?.cache?.pending_identity);
  if (pending && pending.epoch === transcriptMetadataState.epoch && pending.generation > transcriptMetadataState.pendingGeneration) {
    transcriptMetadataState.pendingGeneration = pending.generation;
  }
  return pending;
}
function noteSessionMetadataApply(applied, reason, payload, details = {}) {
  const identity = sessionMetadataPayloadIdentity(payload);
  // A build generation is comparable only inside its own epoch. A payload with no identity, or one
  // stamped by a process this client is not currently tracking, may still be RENDERED -- refusing
  // it would leave the pane on bytes from a dead server -- but it can never advance the applied
  // generation, because it is not evidence about the build anyone is waiting for.
  const comparable = identity !== null && identity.epoch === transcriptMetadataState.epoch;
  if (applied && comparable && identity.generation > transcriptMetadataState.generation) {
    transcriptMetadataState.generation = identity.generation;
  }
  transcriptMetadataState.lastApply = {
    applied: applied === true,
    reason: String(reason || ''),
    epoch: transcriptMetadataState.epoch,
    previousEpoch: transcriptMetadataState.previousEpoch,
    payloadEpoch: identity ? identity.epoch : '',
    payloadGeneration: identity ? identity.generation : 0,
    appliedGeneration: transcriptMetadataState.generation,
    pendingGeneration: transcriptMetadataState.pendingGeneration,
    at: Date.now(),
    ...details,
  };
  return applied === true;
}
// One shape for every session-metadata outcome a caller can act on, so a convergence verdict is
// never a bare boolean that the next function up quietly drops.
function sessionMetadataResult(ok, reason, details = {}) {
  return {
    ok: ok === true,
    reason: String(reason || ''),
    epoch: transcriptMetadataState.epoch,
    generation: Number(transcriptMetadataState.generation || 0),
    pendingGeneration: Number(transcriptMetadataState.pendingGeneration || 0),
    apply: transcriptMetadataState.lastApply,
    ...details,
  };
}
function setTranscriptMetadataPayload(payload, options = {}) {
  if (options.invalidateRequest !== false) transcriptMetadataState.guard.invalidate();
  transcriptMetadataState.payload = payload && typeof payload === 'object' ? payload : {};
  return transcriptMetadataState.payload;
}
const infoPanelRenderCache = {signature: '', html: ''};
const clientEventTransportState = {
  source: null,
  replacementSource: null,
  connected: false,
  reconnectPending: false,
  disconnectTimer: null,
  disconnectEpisode: null,
  nextDisconnectEpisode: 1,
  enabled: false,
  demand: null,
  demandSignature: '',
  demandTimer: null,
  // The client-event EventSource is modelled as three explicit roles, not one socket. `demand` is the
  // REQUESTED state (the channels/operations the page currently wants). `source` is the ACTIVE stream:
  // the one that has fired `ready` and is serving delivered frames. `replacementSource` is the
  // CANDIDATE: a newly opened stream for a changed demand that has NOT yet fired `ready`, so it is not
  // yet allowed to serve. `candidateEpisode` is the ONE bounded retry episode governing that candidate
  // between open and ready; a pre-ready candidate failure that exhausts it must re-drive demand and
  // demote the active stream rather than strand demand or let the old stream claim to serve the new one.
  candidateEpisode: null,
  queue: new Map(),
  resourceEpoch: '',
  resourceRevisions: new Map(),
  resourceRepairs: new Map(),
  frame: 0,
  resyncTimer: null,
};
// A candidate stream may error transiently before it is ever ready; the browser EventSource
// auto-reconnects the same URL, so a small bound tolerates those retries within ONE episode before
// the candidate is abandoned and demand is re-driven. Keep it small so a persistently rejected demand
// falls back to an HTTP resync quickly instead of holding a stale active stream indefinitely.
const clientEventCandidateRetryLimit = 3;
// One server process = one epoch = one sequence for every counter this client retains about that
// server: client-event resource revisions AND the session-metadata build generation. They all
// restart at zero in a replacement process, so they reset together, here, once.
//
// Two call sites in the client-event transport used to inline the transport half of this reset and
// nothing reset the metadata half, which is how a browser kept claiming applied generation 50 while
// talking to a server whose highest build was 0.
//
// Adoption is ATOMIC and resets only what the epoch owns. Everything scoped to the browser rather
// than to the server process -- the request guard, the in-flight request handle, tmuxTopologyEpoch,
// tmux lifecycle transactions and leases, layout, terminals, settings, statusd's own revisions --
// is deliberately untouched: a server restart is not a reason to discard the user's work.
function adoptServerEpoch(epoch) {
  const next = String(epoch || '');
  if (!next || clientEventTransportState.resourceEpoch === next) return false;
  transcriptMetadataState.previousEpoch = transcriptMetadataState.epoch;
  clientEventTransportState.resourceEpoch = next;
  clientEventTransportState.resourceRevisions.clear();
  clientEventTransportState.resourceRepairs.clear();
  transcriptMetadataState.epoch = next;
  // Reset to zero BEFORE the incoming generation is considered, so nothing can carry a number from
  // the previous process into a comparison against this one.
  transcriptMetadataState.generation = 0;
  transcriptMetadataState.pendingGeneration = 0;
  return true;
}
const clientEventDisconnectGraceMs = 15000;
const apiOperationState = {
  records: new Map(),
  pending: new Map(),
  terminal: new Map(),
  waiters: new Map(),
};
const apiOperationReplayLimit = 128;
const operationTerminalAckDelayMs = 25;
const operationTerminalAckRetryMs = 250;
const operationTerminalAckLimit = 64;
const operationTerminalAckState = {
  pending: new Map(),
  timer: null,
  request: null,
};
const reconnectResyncDebounceMs = 751;
let serverVersionReloadHandled = '';
const activitySummaryState = {
  payload: activitySummaryEnabled
    ? {sessions: {}, global: {lines: []}, session_order: []}
    : {sessions: {}, global: {lines: []}, session_order: [], status: 'feature_disabled', reason: 'async_replacement_required'},
  refreshing: false,
  guard: makeGenerationGuard(),
};
window.__yolomuxFixtureLifecycle = Object.freeze({
  diagnosticMode: 'retained-js',
  operationState() {
    const finderVisible = typeof fileExplorerTreePaneIsVisible === 'function'
      && fileExplorerTreePaneIsVisible();
    const watchRootsTimerPending = Boolean(serverWatchRootsState.timer);
    const watchRootsRegistrationPending = serverWatchRootsState.registrationPending === true;
    const watchRootsInFlight = serverWatchRootsState.inFlight === true;
    const watchRootsBaselinePending = serverWatchRootsState.watchDiffPromise !== null;
    // The full watch-diff baseline parks its own operation record in apiOperationState.pending while
    // it awaits a 202 result (refreshFileExplorerFromWatchDiffOnce marks that record
    // terminalOwner='filesystem-watch-diff-refresh' in 40_file_explorer_files.js). Expose exactly
    // which pending IDs the baseline owns so the teardown quiescence gate can tell the baseline's own
    // in-flight operation apart from unrelated work instead of rejecting on "a pending op exists".
    const pendingEntries = Array.from(apiOperationState.pending.entries())
      .sort(([left], [right]) => String(left).localeCompare(String(right)));
    const pending = pendingEntries.map(([operationId]) => operationId);
    // Keep teardown failures bounded but attributable. IDs alone forced a rerun with speculative
    // owner guesses; these fields identify the producer, request and waiter without retaining bodies.
    const pendingDetails = pendingEntries.slice(0, apiOperationReplayLimit).map(([operationId, record]) => ({
      id: operationId,
      kind: String(record?.kind || ''),
      contextOperation: String(record?.context?.operation || ''),
      contextSession: String(record?.context?.session || ''),
      contextPath: String(record?.context?.path || '').slice(0, 512),
      requestId: String(record?.request?.id || ''),
      requestMethod: String(record?.request?.method || ''),
      requestPath: String(record?.request?.path || record?.request?.url || '').slice(0, 512),
      phase: String(record?.phase || ''),
      terminalOwner: String(record?.terminalOwner || ''),
      terminalOwners: Array.from(record?.terminalOwners || []).map(String).sort(),
      waiterCount: apiOperationState.waiters.get(operationId)?.size || 0,
    }));
    const watchDiffPendingOperationIds = pendingEntries
      .filter(([, record]) => record && (
        record.terminalOwner === 'filesystem-watch-diff-refresh'
        || record.terminalOwners?.has('filesystem-watch-diff-refresh') === true
      ))
      .map(([operationId]) => operationId)
      .sort();
    const watchDiffBatch = typeof fileExplorerFsBatchOwnershipState === 'function'
      ? fileExplorerFsBatchOwnershipState('filesystem-watch-diff-refresh')
      : {queued: 0, pending: 0, operations: 0, operationIds: []};
    return {
      pending,
      pendingDetails,
      pendingDetailsTruncated: pendingEntries.length > pendingDetails.length,
      watchDiffPendingOperationIds,
      watchDiffBatchQueued: watchDiffBatch.queued,
      watchDiffBatchPending: watchDiffBatch.pending,
      watchDiffBatchOperations: watchDiffBatch.operations,
      watchDiffBatchOperationIds: watchDiffBatch.operationIds,
      batchQueued: typeof fileExplorerFsBatchQueue === 'undefined' ? 0 : fileExplorerFsBatchQueue.length,
      batchPending: typeof fileExplorerFsBatchPending === 'undefined' ? 0 : fileExplorerFsBatchPending.size,
      batchOperations: typeof fileExplorerFsBatchOperations === 'undefined' ? 0 : fileExplorerFsBatchOperations.size,
      startupActive: typeof startupRefreshApiCoordinator === 'undefined' ? 0 : startupRefreshApiCoordinator.active,
      startupQueued: typeof startupRefreshApiCoordinator === 'undefined' ? 0 : startupRefreshApiCoordinator.queue.length,
      activityRefreshing: activitySummaryState.refreshing === true,
      watchRootsPending: watchRootsTimerPending || watchRootsRegistrationPending || watchRootsInFlight || watchRootsBaselinePending,
      watchRootsTimerPending,
      watchRootsRegistrationPending,
      watchRootsInFlight,
      watchRootsBaselinePending,
      finderVisible,
      finderWatchReady: !finderVisible || Boolean(fileExplorerFilesystemWatchToken),
    };
  },
});
const backgroundOwnerStatusState = {
  payload: null,
  loading: false,
  error: '',
  request: null,
  updatedAt: 0,
  resource: null,
};
const yoagentStartupState = {
  activityPayload: null,
  prewarming: false,
  prewarmStarted: false,
  llmRequested: false,
  infoShown: false,
  infoVisible: false,
};
const yoagentConversationState = {
  messages: [],
  pendingWaits: [],
  loaded: false,
  loading: false,
  path: '',
  displayPath: '',
  streamingMessages: new Map(),
  request: null,
  resource: null,
};
const yoagentJobsState = {
  items: [],
  loading: false,
  request: null,
  resource: null,
};
const yoagentChatState = {
  busy: false,
  activeRequest: null,
  queue: [],
  queueSerial: 0,
  error: null,
  draft: '',
  historyCursor: null,
  historyDraft: '',
  notice: null,
};
let yoagentScrollbackLocked = false;
const searchHistoryState = {
  query: '',
  payload: {query: '', results: []},
  loading: false,
  error: null,
  request: null,
  guard: makeGenerationGuard(),
};
const runHistoryState = {
  payload: {runs: []},
  loading: false,
  error: null,
  request: null,
  guard: makeGenerationGuard(),
};
const notificationDeliveryStorageKey = 'yolomux.notificationDelivery.v1';
const notificationDeliveryDefaults = Object.freeze({inApp: true, system: false});
let notificationDelivery = {...notificationDeliveryDefaults};
const sessionStatusRecords = new Map();
const watchedPrRecords = new Map();
const toastRecords = new Map();
const browserNotificationsByTarget = new Map();
const browserNotificationLifecycleKeys = new WeakMap();
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

function watchedPrRecord(ref, create = false) {
  const key = String(ref || '').trim();
  if (!key) return null;
  let record = watchedPrRecords.get(key) || null;
  if (!record && create) {
    record = {lastStatus: null, notificationLastSent: new Map()};
    setLimitedMapEntry(watchedPrRecords, key, record, notificationLastSentLimit);
  }
  return record;
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
      workingAgentTransitionNotificationPending: new Map(),
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

let attentionAlertSequence = 0;
let stateTrackingReady = false;
let focusedTerminal = null;
let focusedPanelItem = null;
let lastActivePaneItem = null;
let lastActiveNonFileExplorerPaneItem = null;
let lastFocusedTmuxSession = null;
const dragState = {
  item: null,
  sourceSlot: null,
  paneSlot: null,
  filePayload: null,
  customPreview: null,
  customPreviewOffset: {x: 0, y: 0},
  nativePreview: null,
  transparentImage: null,
  tabRectCache: null,
};
// While a tab drag is in flight, tab/preferences re-renders are deferred so they don't replace the
// dragged DOM node mid-drag (which aborts the native HTML5 drag). endSessionDrag flushes these.
let pendingTabStripRender = false;
let pendingSessionButtonsRender = false;
let pendingPreferencesRender = false;
// panel renders deferred during tab drag keep the cheap/full render decision that was made
// while the layout model changed. A boolean loses the pre-change shape and forces a full rebuild on drop.
let pendingLayoutRender = null;
let pendingLayoutRenderFrame = 0;
class RuntimeState {
  constructor() {
    this.layoutMutation = {generation: 0, completed: 0, pending: 0};
  }

  layoutMutationSnapshot() { return {...this.layoutMutation}; }
  get layoutMutationGeneration() { return this.layoutMutation.generation; }
  get layoutMutationCompletedGeneration() { return this.layoutMutation.completed; }
  get pendingLayoutMutationGeneration() { return this.layoutMutation.pending; }
  beginLayoutMutation() {
    this.layoutMutation.generation += 1;
    this.layoutMutation.pending = this.layoutMutation.generation;
    return this.layoutMutation.generation;
  }
  consumePendingLayoutMutation(generation) {
    if (generation === this.layoutMutation.pending) this.layoutMutation.pending = 0;
  }
  completeLayoutMutation(generation) {
    if (!Number.isSafeInteger(generation) || generation <= this.layoutMutation.completed) return false;
    this.layoutMutation.completed = generation;
    return true;
  }
}
const runtimeState = new RuntimeState();
// #47: tab rects measured once per strip at drag time and reused for every dragover (tabs don't move
// mid-drag — renders are deferred), so the drop-placement path doesn't force sync layout on each move.
// one global editor navigation history (Popular IDE-style back/forward through visited files).
// stack holds file paths; index points at the current entry; `navigating` suppresses recording while a
// back/forward re-open is in flight (so it doesn't push a new entry).
const editorNav = {stack: [], index: -1, navigating: false};
// One interaction record owns hover detail, context actions, and touch long-press for every tab
// surface.  Keeping these together prevents a mobile action gesture from drifting into a tab click.
let tabInteractionController = null;
const tabTouchLongPressDelayMs = 500;
const tabTouchLongPressMoveThresholdPx = 10;
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
class FileWorkspaceState {
  constructor() {
    this.generations = {interaction: 0, open: 0};
  }

  beginOpen() { this.generations.open += 1; return this.generations.open; }
  get fileExplorerOpenGeneration() { return this.generations.open; }
  get fileExplorerInteractionGeneration() { return this.generations.interaction; }
  openIsCurrent(generation) { return generation === this.generations.open; }
  interactionGeneration() { return this.generations.interaction; }
  interactionIsCurrent(generation) { return generation === this.generations.interaction; }
  invalidateInteraction({invalidateOpen = true} = {}) {
    this.generations.interaction += 1;
    if (invalidateOpen) this.generations.open += 1;
  }
}
const fileWorkspaceState = new FileWorkspaceState();
let pasteUploadInFlight = false;
let layoutResizeState = null;
let responsiveLayoutPruneTimer = null;
let topbarResizeObserver = null;
const tabStripOverflowCheckSet = new Set();
let tabStripOverflowCheckFrame = null;
let latencySamples = [];
let tabMetaVisible = readStoredTabMetaVisible();
// Authentication expiry is one one-way browser transition: retain its terminal outcome, redirect
// claim, visible state, and transport retirements together so no poller invents a local retry policy.
const authRedirectStarted = {
  redirect: false,
  terminalError: null,
  loginUrl: '',
  retirements: new Map(),
};
let devAutoReloadSource = null;
let openAppMenuId = null;
let openAppMenuPinned = false;
let openAppMenuOpenedAt = 0;
const fileExplorerSyncState = {
  inFlightSignature: '',
  appliedPlanKey: '',
  generation: 0,
};

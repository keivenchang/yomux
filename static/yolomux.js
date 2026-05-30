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
const fileEditor = document.getElementById('fileEditor');
const fileEditorPath = document.getElementById('fileEditorPath');
const fileEditorTextarea = document.getElementById('fileEditorTextarea');
const fileEditorPreviewBtn = document.getElementById('fileEditorPreview');
const fileEditorWrapBtn = document.getElementById('fileEditorWrap');
const fileEditorPreviewPane = document.getElementById('fileEditorPreviewPane');
const fileEditorHighlight = document.getElementById('fileEditorHighlight');
const fileEditorHighlightCode = document.getElementById('fileEditorHighlightCode');
const fileEditorSave = document.getElementById('fileEditorSave');
const fileEditorClose = document.getElementById('fileEditorClose');
const fileEditorStatus = document.getElementById('fileEditorStatus');
const fileExplorerExpanded = new Set();
const fileExplorerHiddenStorageKey = 'yolomux.fileExplorer.showHidden';
const fileExplorerRootModeStorageKey = 'yolomux.fileExplorer.rootMode';
const fileEditorWrapStorageKey = 'yolomux.editorWrap';
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp']);
const MAX_FILE_PREVIEW_BYTES = 20 * 1024 * 1024;
const HIGHLIGHTABLE_EXTENSIONS = {
  '.md': 'markdown', '.markdown': 'markdown',
  '.html': 'xml', '.htm': 'xml', '.xml': 'xml', '.svg': 'xml',
  '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
  '.json': 'json', '.css': 'css', '.scss': 'scss',
  '.rs': 'rust', '.go': 'go', '.c': 'c', '.h': 'c',
  '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.yaml': 'yaml', '.yml': 'yaml',
  '.toml': 'ini', '.ini': 'ini', '.cfg': 'ini',
  '.sql': 'sql', '.rb': 'ruby', '.lua': 'lua', '.pl': 'perl',
};
const openFiles = new Map();  // path -> {mtime, size, kind, original, content, dirty}
const fileExplorerDirectorySignatures = new Map();
let activeFile = null;
let fileExplorerRoot = null;
let filesystemRefreshInFlight = false;
let fileExplorerRootMode = readStoredFileExplorerRootMode();
let fileExplorerShowHidden = (() => {
  try { return window.localStorage?.getItem(fileExplorerHiddenStorageKey) === '1'; }
  catch (_) { return false; }
})();
const fileEditorPreviewMode = new Map();  // path -> true when previewing markdown
const fileEditorImageMode = new Map();  // path -> "original" when zoomed to natural image size
let fileEditorWrapEnabled = readStoredEditorWrap();
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
const remoteResizeDelayMs = 220;
const metadataRefreshMs = 15001;
const paneStateRefreshMs = 1257;
const latencyRefreshMs = 3001;
const eventLogRefreshMs = 5003;
const redReminderMs = 1550;
const yoloRotateMs = 20000;
const latencySamplesMax = 24;
const toastDurationMs = 10000;
const toastMaxLines = 3;
const toastMaxLineChars = 180;
const popoverShowDelayMs = 300;
const hoverCloseDelayMs = 300;
const popoverHideDelayMs = hoverCloseDelayMs;
const menuHoverOpenDelayMs = 300;
const menuHoverCloseDelayMs = hoverCloseDelayMs;
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
const fileExplorerItemId = '__files__';
const emptyPaneParam = '__empty_pane__';
const fileEditorItemPrefix = 'file:';
function fileEditorItemFor(path) { return fileEditorItemPrefix + path; }
function isFileExplorerItem(item) { return item === fileExplorerItemId; }
function isFileEditorItem(item) { return typeof item === 'string' && item.startsWith(fileEditorItemPrefix); }
function fileItemPath(item) { return isFileEditorItem(item) ? item.slice(fileEditorItemPrefix.length) : null; }
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
  mac: {
    close: 'mac-window-control mac-minimize',
    minimize: 'mac-window-control mac-minimize',
    zoom: 'mac-window-control mac-zoom',
  },
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

function platformWindowControlClass(kind) {
  const platform = isMacPlatform() ? 'mac' : 'pc';
  const classes = platformWindowControlClasses[platform];
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
  applyPlatformControlClass(fileEditorClose, 'close');
}
const syntaxLanguageByExtension = new Map([
  ['.cjs', 'javascript'],
  ['.css', 'css'],
  ['.html', 'xml'],
  ['.htm', 'xml'],
  ['.js', 'javascript'],
  ['.json', 'json'],
  ['.jsx', 'javascript'],
  ['.md', 'markdown'],
  ['.markdown', 'markdown'],
  ['.mjs', 'javascript'],
  ['.py', 'python'],
  ['.pyw', 'python'],
  ['.rs', 'rust'],
  ['.sh', 'bash'],
  ['.svg', 'xml'],
  ['.toml', 'ini'],
  ['.ts', 'typescript'],
  ['.tsx', 'typescript'],
  ['.xml', 'xml'],
  ['.yaml', 'yaml'],
  ['.yml', 'yaml'],
]);
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [infoItemId, fileExplorerItemId, ...visibleSessions];
let layoutSlots = initialLayoutSlots();
let activeSessions = sessionsFromLayout();
let transcriptMeta = {};
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
let customDragPreview = null;
let customDragPreviewOffset = {x: 0, y: 0};
let transparentDragImage = null;
const terminalContextMenu = createContextMenuController();
const fileContextMenu = createContextMenuController();
const sessionContextMenu = createContextMenuController();
let sessionRenameDialog = null;
const fileExplorerSelectedPaths = new Set();
let fileExplorerSelectionAnchor = null;
let fileTreeRenamePath = null;
let fileExplorerPathError = '';
let fileImagePreviewShowTimer = null;
let fileImagePreviewHideTimer = null;
let fileImagePreviewPopover = null;
const panelPopoverHideTimers = new WeakMap();
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
let appMenuHoverTimer = null;
let appMenuCloseTimer = null;
let fileExplorerSyncPathInFlight = '';
let fileExplorerSyncGeneration = 0;

async function apiFetch(url, options = {}) {
  const requestOptions = {...options};
  if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
  const response = await fetch(url, requestOptions);
  if (response.status === 401) {
    await redirectToLogin(response);
    throw new Error('authentication required');
  }
  return response;
}

async function redirectToLogin(response) {
  if (authRedirectStarted) return;
  authRedirectStarted = true;
  const nextPath = `${window.location.pathname}${window.location.search}`;
  let loginUrl = `/login?next=${encodeURIComponent(nextPath || '/')}`;
  try {
    const payload = await response.clone().json();
    if (payload?.login_url) loginUrl = payload.login_url;
  } catch (_) {}
  window.location.assign(loginUrl);
}

function readStoredTabMetaVisible() {
  try {
    const stored = window.localStorage?.getItem(tabMetaStorageKey);
    return stored === null || stored === undefined ? true : stored !== '0';
  } catch (_) {
    return true;
  }
}

function writeStoredTabMetaVisible(value) {
  try {
    window.localStorage?.setItem(tabMetaStorageKey, value ? '1' : '0');
  } catch (_) {
    // The toggle is still useful for the current page when storage is blocked.
  }
}

function readStoredEditorWrap() {
  try {
    return window.localStorage?.getItem(fileEditorWrapStorageKey) === '1';
  } catch (_) {
    return false;
  }
}

function writeStoredEditorWrap(value) {
  try {
    window.localStorage?.setItem(fileEditorWrapStorageKey, value ? '1' : '0');
  } catch (_) {}
}

function readStoredFileExplorerRootMode() {
  try {
    return window.localStorage?.getItem(fileExplorerRootModeStorageKey) === 'sync' ? 'sync' : 'fixed';
  } catch (_) {
    return 'fixed';
  }
}

function writeStoredFileExplorerRootMode(mode) {
  try {
    window.localStorage?.setItem(fileExplorerRootModeStorageKey, mode === 'sync' ? 'sync' : 'fixed');
  } catch (_) {}
}

function renderTabMetaToggle() {
  document.body?.classList.toggle('tab-meta-hidden', !tabMetaVisible);
  if (!tabMetaToggle) return;
  tabMetaToggle.classList.toggle('active', tabMetaVisible);
  tabMetaToggle.setAttribute('aria-pressed', tabMetaVisible ? 'true' : 'false');
  const label = tabMetaVisible ? 'Hide tab metadata' : 'Show tab metadata';
  tabMetaToggle.setAttribute('aria-label', label);
  tabMetaToggle.title = label;
}

function toggleTabMetadata() {
  tabMetaVisible = !tabMetaVisible;
  writeStoredTabMetaVisible(tabMetaVisible);
  renderTabMetaToggle();
  renderSessionButtons();
  scheduleTopbarMetricsUpdate();
}

function setFocusedTerminal(session) {
  focusedTerminal = session;
  focusedPanelItem = session;
  if (isTmuxSession(session)) lastFocusedTmuxSession = session;
  dismissAttentionAlertsForSession(session);
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  scheduleFileExplorerActiveTabSync(session);
}

function clearFocusedTerminal(session) {
  if (focusedTerminal !== session) return;
  focusedTerminal = null;
  focusedPanelItem = null;
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
}

function setFocusedPanelItem(item) {
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  if (isTmuxSession(item)) {
    lastFocusedTmuxSession = item;
    dismissAttentionAlertsForSession(item);
  }
  updateSessionButtonStates();
  updatePanelInactiveOverlays();
  scheduleFileExplorerActiveTabSync(item);
}

function clearFocusForInactiveLayout() {
  if (focusedTerminal && !activeSessions.includes(focusedTerminal)) focusedTerminal = null;
  if (focusedPanelItem && !activeSessions.includes(focusedPanelItem)) focusedPanelItem = null;
  if (lastFocusedTmuxSession && !activeSessions.includes(lastFocusedTmuxSession)) lastFocusedTmuxSession = null;
}

function terminalPaneIsActive(session) {
  return document.getElementById(`terminal-pane-${session}`)?.classList.contains('active') === true;
}

function selectPanelOnHover(item) {
  if (!item) return;
  if (isTmuxSession(item) && terminalPaneIsActive(item)) {
    setFocusedTerminal(item);
    scheduleFit(item);
    setTimeout(() => terminals.get(item)?.term?.focus?.(), 0);
    return;
  }
  if (focusedPanelItem === item) return;
  setFocusedPanelItem(item);
}

function updatePanelInactiveOverlays() {
  for (const [item, panel] of panelNodes.entries()) {
    panel.classList.toggle('focused-pane', item === focusedPanelItem);
    panel.classList.toggle('active-pane', item === focusedPanelItem);
  }
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function wsUrl(session) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${location.host}/ws?session=${encodeURIComponent(session)}`;
}

function renderTransportWarning() {
  if (!httpsWarning) return;
  const secure = location.protocol === 'https:';
  httpsWarning.hidden = secure;
  if (secure) return;
  const port = location.port || '9998';
  const selfSigned = `python3 yolomux.py --port ${port} --self-signed`;
  const cert = `python3 yolomux.py --port ${port} --cert /path/fullchain.pem --key /path/privkey.pem`;
  httpsWarning.dataset.tip = `No HTTPS. Highly recommend that you restart with ${selfSigned}. Or use ${cert}.`;
  httpsWarning.setAttribute('aria-label', httpsWarning.dataset.tip);
  httpsWarning.tabIndex = 0;
}

function stripTerminalQueryResponses(data) {
  return String(data)
    .replace(/\x1b\[[?>]?[0-9;]*c/g, '')
    .replace(/\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c)/g, '');
}

const terminalLinkPattern = /(?:https?:\/\/|file:\/\/|www\.)[^\s<>"'`]+/gi;
const terminalLinkClosePairs = [
  [')', '('],
  [']', '['],
  ['}', '{'],
];

function countChar(value, char) {
  let count = 0;
  for (const item of value) {
    if (item === char) count += 1;
  }
  return count;
}

function trimTerminalLinkCandidate(value) {
  let text = String(value || '').replace(/^[<("'`]+/, '');
  let changed = true;
  while (changed && text) {
    changed = false;
    const trimmed = text.replace(/[.,;:!?"'`>]+$/, '');
    if (trimmed !== text) {
      text = trimmed;
      changed = true;
    }
    for (const [closeChar, openChar] of terminalLinkClosePairs) {
      if (text.endsWith(closeChar) && countChar(text, closeChar) > countChar(text, openChar)) {
        text = text.slice(0, -1);
        changed = true;
      }
    }
  }
  return text;
}

function normalizeTerminalLink(value) {
  const text = trimTerminalLinkCandidate(value);
  if (!text) return '';
  if (/^www\./i.test(text)) return `https://${text}`;
  return text;
}

function openTerminalLink(rawLink) {
  const link = normalizeTerminalLink(rawLink);
  if (!link) return;
  try {
    const opened = window.open(link, '_blank', 'noopener,noreferrer');
    if (!opened) statusEl.innerHTML = `<span class="err">browser blocked link: ${esc(link)}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">could not open link: ${esc(error)}</span>`;
  }
}

function terminalTextLinks(lineText, rangeForOffsets, y = null) {
  const links = [];
  terminalLinkPattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalLinkPattern)) {
    const raw = match[0] || '';
    const text = trimTerminalLinkCandidate(raw);
    if (!text) continue;
    const startIndex = (match.index || 0) + raw.indexOf(text);
    const endIndex = startIndex + text.length;
    const range = rangeForOffsets(startIndex, endIndex);
    if (!range) continue;
    if (Number.isFinite(y) && (range.start.y > y || range.end.y < y)) continue;
    links.push({
      text,
      range,
      activate: () => openTerminalLink(text),
    });
  }
  return links;
}

function terminalLineLinks(lineText, y) {
  return terminalTextLinks(lineText, (startIndex, endIndex) => ({
    start: {x: startIndex + 1, y},
    end: {x: endIndex, y},
  }));
}

function terminalBufferLineText(line) {
  return line?.translateToString?.(true) || '';
}

function terminalWrappedLineGroup(term, y) {
  const buffer = term.buffer?.active;
  if (!buffer?.getLine) return null;
  const requested = Math.max(0, y - 1);
  if (!buffer.getLine(requested)) return null;
  let start = requested;
  while (start > 0 && buffer.getLine(start)?.isWrapped === true) start -= 1;
  let end = requested;
  while (buffer.getLine(end + 1)?.isWrapped === true) end += 1;
  const rows = [];
  let offset = 0;
  for (let index = start; index <= end; index += 1) {
    const text = terminalBufferLineText(buffer.getLine(index));
    rows.push({y: index + 1, text, start: offset, end: offset + text.length});
    offset += text.length;
  }
  return {text: rows.map(row => row.text).join(''), rows};
}

function terminalWrappedOffsetPosition(group, offset, endPosition = false) {
  const target = endPosition ? Math.max(0, offset - 1) : offset;
  const row = group.rows.find(candidate => target >= candidate.start && target < candidate.end) || group.rows[group.rows.length - 1];
  if (!row) return null;
  return {x: Math.max(1, target - row.start + 1), y: row.y};
}

function terminalWrappedRange(group, startIndex, endIndex) {
  const start = terminalWrappedOffsetPosition(group, startIndex, false);
  const end = terminalWrappedOffsetPosition(group, endIndex, true);
  if (!start || !end) return null;
  return {start, end};
}

function terminalWrappedLineLinks(term, y) {
  const group = terminalWrappedLineGroup(term, y);
  if (!group) return [];
  if (group.rows.length === 1) return terminalLineLinks(group.text, y);
  return terminalTextLinks(group.text, (startIndex, endIndex) => terminalWrappedRange(group, startIndex, endIndex), y);
}

function installTerminalLinkProvider(term) {
  if (typeof term.registerLinkProvider !== 'function') return;
  term.registerLinkProvider({
    provideLinks: (y, callback) => {
      try {
        callback(terminalWrappedLineLinks(term, y));
      } catch (_) {
        callback([]);
      }
    },
  });
}

function dedentSelectionText(value) {
  const text = String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = text.split('\n');
  const indents = lines
    .filter(line => line.trim().length > 0 && /^[ \t]+/.test(line))
    .map(line => (line.match(/^[ \t]+/) || [''])[0].length);
  const stripBullet = line => line.replace(/^[ \t]*[●•]\s*/, '');
  if (!indents.length) return lines.map(stripBullet).join('\n');
  const commonIndent = Math.min(...indents);
  return lines
    .map(line => line.trim().length > 0 && /^[ \t]+/.test(line) ? line.slice(commonIndent) : line)
    .map(stripBullet)
    .join('\n');
}

async function copyTextToClipboard(text) {
  const clipboard = globalThis.navigator?.clipboard;
  const value = String(text ?? '');
  if (clipboard?.writeText) {
    await clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-10000px';
  textarea.style.top = '-10000px';
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand?.('copy') === true;
  textarea.remove();
  if (!copied) throw new Error('clipboard copy is unavailable');
}

function createContextMenuController() {
  let menu = null;
  const close = () => {
    if (!menu) return;
    menu.remove();
    menu = null;
    document.removeEventListener('pointerdown', pointerdown, true);
    document.removeEventListener('keydown', keydown, true);
    window.removeEventListener('blur', close);
  };
  const pointerdown = event => {
    if (menu?.contains(event.target)) return;
    close();
  };
  const keydown = event => {
    if (event.key === 'Escape') close();
  };
  return {
    close,
    open(nextMenu, x, y) {
      close();
      menu = nextMenu;
      menu.addEventListener('pointerdown', event => event.stopPropagation());
      document.body.appendChild(menu);
      positionContextMenu(menu, x, y);
      document.addEventListener('pointerdown', pointerdown, true);
      document.addEventListener('keydown', keydown, true);
      window.addEventListener('blur', close);
    },
  };
}

function appendContextMenuButton(menu, label, handler, closeMenu, options = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('role', 'menuitem');
  button.textContent = label;
  button.disabled = options.disabled === true;
  if (options.checked === true) button.dataset.checked = 'true';
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (!button.disabled) handler();
    closeMenu();
  });
  menu.appendChild(button);
  return button;
}

function appendContextMenuSeparator(menu) {
  const separator = document.createElement('div');
  separator.className = 'terminal-context-menu-separator';
  separator.role = 'separator';
  menu.appendChild(separator);
  return separator;
}

function rootCssLengthPx(name) {
  if (!document.body || typeof window.getComputedStyle !== 'function') return 0;
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value) return 0;
  const probe = document.createElement('div');
  probe.style.position = 'fixed';
  probe.style.visibility = 'hidden';
  probe.style.pointerEvents = 'none';
  probe.style.width = value;
  probe.style.height = '0';
  document.body.appendChild(probe);
  const width = probe.getBoundingClientRect().width || 0;
  probe.remove();
  return Math.max(0, width);
}

function popoverEdgeGapPx() {
  return rootCssLengthPx('--popover-edge-gap');
}

function positionContextMenu(menu, x, y) {
  const rect = menu.getBoundingClientRect();
  const edgeGap = popoverEdgeGapPx();
  const left = Math.min(Math.max(edgeGap, x), Math.max(edgeGap, window.innerWidth - rect.width - edgeGap));
  const top = Math.min(Math.max(edgeGap, y), Math.max(edgeGap, window.innerHeight - rect.height - edgeGap));
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function closeTerminalContextMenu() {
  terminalContextMenu.close();
}

function closeFileContextMenu() {
  fileContextMenu.close();
}

function closeSessionContextMenu() {
  sessionContextMenu.close();
}

function closeContextMenus() {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
}

async function copyTerminalSelection(session, term, options = {}) {
  const selected = term.getSelection?.() || '';
  if (!selected) {
    statusEl.textContent = 'nothing selected';
    return;
  }
  const text = options.dedent ? dedentSelectionText(selected) : selected;
  try {
    await copyTextToClipboard(text);
    statusEl.textContent = options.dedent ? 'copied without indent' : 'copied';
  } catch (error) {
    statusEl.innerHTML = `<span class="err">copy failed: ${esc(error)}</span>`;
  }
}

function showTerminalContextMenu(session, term, x, y) {
  closeFileContextMenu();
  closeSessionContextMenu();
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu';
  menu.setAttribute('role', 'menu');
  const items = [
    ['Copy', false],
    ['Copy without indent', true],
  ];
  for (const [label, dedent] of items) {
    appendContextMenuButton(menu, label, () => copyTerminalSelection(session, term, {dedent}), closeTerminalContextMenu);
  }
  terminalContextMenu.open(menu, x, y);
}

function installTerminalContextMenu(session, term, container) {
  container.addEventListener('contextmenu', event => {
    const selected = term.getSelection?.() || '';
    if (!selected) return;
    event.preventDefault();
    event.stopPropagation();
    showTerminalContextMenu(session, term, event.clientX, event.clientY);
  });
}

function showSessionContextMenu(session, x, y, options = {}) {
  if (!isTmuxSession(session)) return;
  closeAppMenus();
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeOtherSessionPopovers(null);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu session-context-menu';
  menu.setAttribute('role', 'menu');
  const renameAction = options.tab ? () => beginPaneTabRename(options.tab, session) : () => renameTmuxSession(session);
  for (const item of tmuxSessionViewCommands(session)) {
    appendContextMenuButton(menu, item.label, item.action, closeSessionContextMenu, {disabled: item.disabled, checked: item.checked});
  }
  appendContextMenuSeparator(menu);
  for (const item of tmuxSessionActionCommands(session, {renameAction})) {
    appendContextMenuButton(menu, item.label, item.action, closeSessionContextMenu, {disabled: item.disabled, checked: item.checked});
  }
  sessionContextMenu.open(menu, x, y);
}

function emptyLayoutSlots() {
  return {[layoutTreeKey]: null};
}

function emptyPaneState() {
  return {active: null, tabs: []};
}

function emptyPlaceholderPaneState() {
  return {active: null, tabs: [], placeholder: true};
}

function emptyPlaceholderLayoutSlots(slot = 'left') {
  const next = emptyLayoutSlots();
  next[slot] = emptyPlaceholderPaneState();
  next[layoutTreeKey] = leafNode(slot);
  return next;
}

function leafNode(slot) {
  return {slot};
}

function splitNode(direction, first, second, pct = defaultSplitPercent) {
  return {split: direction, pct: splitPercent(pct), children: [first, second]};
}

function splitPercent(value) {
  if (value === null || value === undefined || value === '') return defaultSplitPercent;
  const number = Number(value);
  if (!Number.isFinite(number)) return defaultSplitPercent;
  return Math.min(maxSplitPercent, Math.max(minSplitPercent, number));
}

function splitPercentForDisplay(value) {
  const rounded = Math.round(splitPercent(value) * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded);
}

function layoutLeafSlots(node) {
  if (!node) return [];
  if (node.slot) return [node.slot];
  return (node.children || []).flatMap(layoutLeafSlots);
}

function layoutSlotKeys(slots = layoutSlots) {
  const treeSlots = layoutLeafSlots(slots?.[layoutTreeKey]);
  if (treeSlots.length) return treeSlots;
  return Object.keys(slots || {}).filter(key => key !== layoutTreeKey && paneHasLayoutContent(key, slots));
}

function nextLayoutSlot(slots = layoutSlots) {
  const used = new Set(Object.keys(slots || {}));
  let index = 1;
  while (used.has(`slot${index}`)) index += 1;
  return `slot${index}`;
}

function normalizePaneState(raw, seen, options = {}) {
  const state = emptyPaneState();
  const items = Array.isArray(raw) ? raw : Array.isArray(raw?.tabs) ? raw.tabs : [];
  const preserveRemovedItems = new Set(options.preserveRemovedItems || []);
  let hadPreservedRemovedItem = false;
  for (const value of items) {
    if (preserveRemovedItems.has(String(value))) hadPreservedRemovedItem = true;
    const item = resolveLayoutItem(value);
    if (preserveRemovedItems.has(item)) hadPreservedRemovedItem = true;
    if (isLayoutItem(item) && !seen.has(item)) {
      state.tabs.push(item);
      seen.add(item);
    }
  }
  const active = resolveLayoutItem(raw?.active);
  state.active = state.tabs.includes(active) ? active : state.tabs[0] || null;
  if (!state.tabs.length && !Array.isArray(raw) && raw?.placeholder === true) state.placeholder = true;
  if (!state.tabs.length && options.preserveRemovedSlots === true && hadPreservedRemovedItem) state.placeholder = true;
  return state;
}

function normalizeLayoutSlots(value, options = {}) {
  let normalized;
  if (!value || typeof value !== 'object') normalized = emptyPlaceholderLayoutSlots();
  else if (value[layoutTreeKey]) normalized = normalizeTreeLayout(value, options);
  else normalized = normalizeLegacyLayoutSlots(value, options);
  return normalizeFileExplorerDock(normalized);
}

function normalizeTreeLayout(value, options = {}) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  next[layoutTreeKey] = normalizeLayoutNode(value[layoutTreeKey], value, next, seen, options);
  return compactLayoutSlots(next);
}

function normalizeLayoutNode(node, value, next, seen, options = {}) {
  if (!node || typeof node !== 'object') return null;
  if (typeof node.slot === 'string') {
    next[node.slot] = normalizePaneState(value[node.slot], seen, options);
    return leafNode(node.slot);
  }
  const direction = node.split === 'column' ? 'column' : 'row';
  const children = (node.children || []).map(child => normalizeLayoutNode(child, value, next, seen, options)).filter(Boolean);
  if (children.length >= 2) return splitNode(direction, children[0], children[1], node.pct);
  return children[0] || null;
}

function normalizeLegacyLayoutSlots(value, options = {}) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (const side of paneKeys) {
    next[side] = normalizePaneState(value[side], seen, options);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function legacyLayoutTree(slots) {
  const columns = basePaneKeys.map(column => legacyColumnTree(column, slots)).filter(Boolean);
  if (columns.length >= 2) return splitNode('row', columns[0], columns[1]);
  return columns[0] || null;
}

function legacyColumnTree(column, slots) {
  if (paneHasLayoutContent(column, slots)) return leafNode(column);
  const top = verticalSplitSlot(column, 'top');
  const bottom = verticalSplitSlot(column, 'bottom');
  const topNode = paneHasLayoutContent(top, slots) ? leafNode(top) : null;
  const bottomNode = paneHasLayoutContent(bottom, slots) ? leafNode(bottom) : null;
  if (topNode && bottomNode) return splitNode('column', topNode, bottomNode);
  return topNode || bottomNode;
}

function compactLayoutSlots(slots) {
  const next = emptyLayoutSlots();
  for (const key of layoutSlotKeys(slots)) next[key] = paneStateForLayoutSlot(key, slots);
  next[layoutTreeKey] = compactLayoutNode(slots[layoutTreeKey], next);
  const keys = layoutSlotKeys(next);
  if (keys.length && keys.every(key => paneIsPlaceholder(key, next))) return emptyPlaceholderLayoutSlots(keys[0] || 'left');
  return next[layoutTreeKey] ? next : emptyPlaceholderLayoutSlots();
}

function compactLayoutNode(node, slots) {
  return compactLayoutNodeInfo(node, slots)?.node || null;
}

function compactLayoutNodeInfo(node, slots) {
  if (!node) return null;
  if (node.slot) {
    if (!paneHasLayoutContent(node.slot, slots)) return null;
    const placeholderOnly = paneIsPlaceholder(node.slot, slots);
    return {
      node: leafNode(node.slot),
      containsFileExplorer: paneTabs(node.slot, slots).includes(fileExplorerItemId),
      placeholderOnly,
    };
  }
  const direction = node.split === 'column' ? 'column' : 'row';
  const children = (node.children || []).map(child => compactLayoutNodeInfo(child, slots)).filter(Boolean);
  if (!children.length) return null;
  if (children.length === 1) return children[0];
  const hasFileExplorer = children.some(child => child.containsFileExplorer);
  const kept = direction === 'row' && hasFileExplorer
    ? children
    : children.filter(child => !child.placeholderOnly);
  const compacted = kept.length ? kept : [children[0]];
  if (compacted.length < 2) return compacted[0];
  const nextNode = splitNode(direction, compacted[0].node, compacted[1].node, node.pct);
  return {
    node: nextNode,
    containsFileExplorer: compacted.some(child => child.containsFileExplorer),
    placeholderOnly: compacted.every(child => child.placeholderOnly),
  };
}

function layoutNodeHasContent(node, slots = layoutSlots) {
  if (!node) return false;
  if (node.slot) return paneHasLayoutContent(node.slot, slots);
  return (node.children || []).some(child => layoutNodeHasContent(child, slots));
}

function layoutNodeContainsItem(node, item, slots = layoutSlots) {
  if (!node) return false;
  if (node.slot) return paneTabs(node.slot, slots).includes(item);
  return (node.children || []).some(child => layoutNodeContainsItem(child, item, slots));
}

function layoutHasHorizontalContentBeforeItem(node, item, slots = layoutSlots) {
  if (!node || node.slot) return false;
  const children = node.children || [];
  if (node.split === 'row') {
    let hasContentBefore = false;
    for (const child of children) {
      if (layoutNodeContainsItem(child, item, slots)) {
        return hasContentBefore || layoutHasHorizontalContentBeforeItem(child, item, slots);
      }
      if (layoutNodeHasContent(child, slots)) hasContentBefore = true;
    }
    return false;
  }
  return children.some(child => (
    layoutNodeContainsItem(child, item, slots)
    && layoutHasHorizontalContentBeforeItem(child, item, slots)
  ));
}

function fileExplorerNeedsLeftDock(slots = layoutSlots) {
  return layoutHasHorizontalContentBeforeItem(slots?.[layoutTreeKey], fileExplorerItemId, slots);
}

function normalizeFileExplorerDock(slots) {
  return fileExplorerNeedsLeftDock(slots) ? layoutWithFileExplorerDockedLeft(slots) : slots;
}

function layoutFromSessionList(values) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (const raw of values) {
    const item = resolveLayoutItem(raw);
    if (!isLayoutItem(item) || seen.has(item)) continue;
    const state = next.left || emptyPaneState();
    state.tabs.push(item);
    if (!state.active) state.active = item;
    next.left = state;
    seen.add(item);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutFromParam(raw, tabsRaw = '') {
  const text = String(raw || '').trim();
  if (!text) return null;
  if (text.toLowerCase() === 'empty') return emptyPlaceholderLayoutSlots();
  if (text.startsWith(layoutTreeParamPrefix)) return treeLayoutFromParam(text.slice(layoutTreeParamPrefix.length));
  if (compactLayoutParamLooksLikeTree(text)) return compactTreeLayoutFromParam(text, tabsRaw);
  const namedSlotLayout = namedSlotLayoutFromParam(text, tabsRaw);
  if (namedSlotLayout) return namedSlotLayout;
  const sides = text.split(',');
  if (!sides.some(value => value.trim())) return null;
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (let index = 0; index < basePaneKeys.length; index += 1) {
    const side = basePaneKeys[index];
    for (const value of (sides[index] || '').split('+')) {
      if (!value.trim()) continue;
      const item = resolveLayoutItem(value.trim());
      if (isLayoutItem(item) && !seen.has(item)) {
        if (!next[side]) next[side] = emptyPaneState();
        next[side].tabs.push(item);
        if (!next[side].active) next[side].active = item;
        seen.add(item);
      }
    }
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return sessionsFromSlots(next).length ? next : null;
}

function namedSlotLayoutFromParam(raw, tabsRaw) {
  const tabStates = layoutTabStatesFromParam(tabsRaw);
  if (!tabStates.size) return null;
  const slotNames = String(raw || '')
    .split(',')
    .map(value => layoutSlotName(readableParamComponentDecode(value.trim())))
    .filter(Boolean);
  if (!slotNames.length) return null;
  const next = emptyLayoutSlots();
  const leaves = [];
  const seen = new Set();
  for (const slot of slotNames) {
    if (seen.has(slot)) continue;
    seen.add(slot);
    const state = tabStates.get(slot);
    if (!state || !paneHasLayoutContent(slot, {[slot]: state})) continue;
    next[slot] = state;
    leaves.push(leafNode(slot));
  }
  if (!leaves.length) return null;
  next[layoutTreeKey] = leaves.reduce((tree, leaf) => (tree ? splitNode('row', tree, leaf) : leaf), null);
  const normalized = normalizeLayoutSlots(next);
  return sessionsFromSlots(normalized).length || layoutSlotKeys(normalized).some(slot => paneIsPlaceholder(slot, normalized)) ? normalized : null;
}

function treeLayoutFromParam(raw) {
  try {
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== 'object') return null;
    const tree = layoutTreeFromParamNode(payload.tree);
    const slots = payload.slots && typeof payload.slots === 'object' ? payload.slots : {};
    const next = emptyLayoutSlots();
    next[layoutTreeKey] = tree;
    for (const slot of layoutLeafSlots(tree)) {
      const rawState = slots[slot];
      const tabs = Array.isArray(rawState?.tabs) ? rawState.tabs : Array.isArray(rawState) ? rawState : [];
      const active = resolveLayoutItem(rawState?.active);
      next[slot] = rawState?.placeholder === true && !tabs.length
        ? emptyPlaceholderPaneState()
        : paneStateWithTabs(tabs.map(resolveLayoutItem), active);
    }
    const normalized = normalizeLayoutSlots(next);
    return sessionsFromSlots(normalized).length ? normalized : null;
  } catch (_) {
    return null;
  }
}

function compactLayoutParamLooksLikeTree(text) {
  return /^(row|col|column)(?:@\d+(?:\.\d+)?)?\(/.test(text);
}

function compactTreeLayoutFromParam(raw, tabsRaw) {
  const parser = {text: String(raw || ''), index: 0};
  const tree = parseCompactLayoutNode(parser);
  skipCompactLayoutWhitespace(parser);
  if (!tree || parser.index !== parser.text.length) return null;
  const tabStates = layoutTabStatesFromParam(tabsRaw);
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = tree;
  for (const slot of layoutLeafSlots(tree)) {
    next[slot] = tabStates.get(slot) || emptyPlaceholderPaneState();
  }
  const normalized = normalizeLayoutSlots(next);
  return sessionsFromSlots(normalized).length ? normalized : null;
}

function parseCompactLayoutNode(parser) {
  skipCompactLayoutWhitespace(parser);
  const name = readCompactLayoutToken(parser);
  if (!name) return null;
  const splitMatch = name.toLowerCase().match(/^(row|col|column)(?:@(\d+(?:\.\d+)?))?$/);
  skipCompactLayoutWhitespace(parser);
  if (splitMatch && parser.text[parser.index] === '(') {
    parser.index += 1;
    const children = [];
    while (parser.index < parser.text.length) {
      const child = parseCompactLayoutNode(parser);
      if (!child) return null;
      children.push(child);
      skipCompactLayoutWhitespace(parser);
      if (parser.text[parser.index] === ',') {
        parser.index += 1;
        continue;
      }
      if (parser.text[parser.index] === ')') {
        parser.index += 1;
        break;
      }
      return null;
    }
    if (children.length < 2) return children[0] || null;
    return splitNode(splitMatch[1] === 'row' ? 'row' : 'column', children[0], children[1], splitMatch[2]);
  }
  const slot = layoutSlotName(readableParamComponentDecode(name));
  return slot ? leafNode(slot) : null;
}

function readCompactLayoutToken(parser) {
  const start = parser.index;
  while (parser.index < parser.text.length && !/[(),\s]/.test(parser.text[parser.index])) parser.index += 1;
  return parser.text.slice(start, parser.index);
}

function skipCompactLayoutWhitespace(parser) {
  while (/\s/.test(parser.text[parser.index] || '')) parser.index += 1;
}

function layoutTreeFromParamNode(node) {
  if (!node || typeof node !== 'object') return null;
  const slot = layoutSlotName(node.slot);
  if (slot) return leafNode(slot);
  const children = (node.children || []).map(layoutTreeFromParamNode).filter(Boolean);
  if (children.length >= 2) return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
  return children[0] || null;
}

function layoutSlotName(value) {
  const slot = String(value || '').trim();
  return slot && slot !== layoutTreeKey ? slot : null;
}

function layoutParamValue(slots) {
  const tree = slots?.[layoutTreeKey];
  if (tree) return compactLayoutTreeParam(tree);
  const keys = layoutSlotKeys(slots);
  if (!keys.length) return 'empty';
  return keys.map(side => paneTabs(side, slots).map(readableItemParam).join('+')).join(',');
}

function compactLayoutTreeParam(node) {
  if (!node) return '';
  if (node.slot) return readableParamComponent(node.slot);
  const name = node.split === 'column' ? 'col' : 'row';
  return `${name}@${splitPercentForDisplay(node.pct)}(${(node.children || []).map(compactLayoutTreeParam).filter(Boolean).join(',')})`;
}

function layoutTabsParamValue(slots) {
  const slotValues = [];
  for (const slot of layoutSlotKeys(slots)) {
    if (paneIsPlaceholder(slot, slots)) {
      slotValues.push(`${readableParamComponent(slot)}:${emptyPaneParam}`);
      continue;
    }
    const active = activeItemForSide(slot, slots);
    const tabs = paneTabs(slot, slots).map((item, index) => {
      const marker = item === active && index > 0 ? '*' : '';
      return `${readableItemParam(item)}${marker}`;
    });
    if (tabs.length) slotValues.push(`${readableParamComponent(slot)}:${tabs.join(',')}`);
  }
  return slotValues.join(';');
}

function layoutTabStatesFromParam(raw) {
  const result = new Map();
  for (const part of String(raw || '').split(';')) {
    if (!part.trim()) continue;
    const separator = part.indexOf(':');
    if (separator <= 0) continue;
    const slot = layoutSlotName(readableParamComponentDecode(part.slice(0, separator)));
    if (!slot) continue;
    const tabs = [];
    let active = null;
    let placeholder = false;
    for (const rawItem of part.slice(separator + 1).split(',')) {
      let token = rawItem.trim();
      if (!token) continue;
      const activeToken = token.endsWith('*');
      if (activeToken) token = token.slice(0, -1);
      const decoded = readableParamComponentDecode(token);
      if (decoded === emptyPaneParam) {
        placeholder = true;
        continue;
      }
      const item = resolveLayoutItem(decoded);
      if (isLayoutItem(item) && !tabs.includes(item)) {
        tabs.push(item);
        if (activeToken) active = item;
      }
    }
    result.set(slot, placeholder && !tabs.length ? emptyPlaceholderPaneState() : paneStateWithTabs(tabs, active));
  }
  return result;
}

function readableItemParam(item) {
  return readableParamComponent(itemParam(item));
}

function readableParamComponent(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

function readableParamComponentDecode(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function initialLayoutSlots() {
  const params = new URLSearchParams(location.search);
  const layoutFromUrl = layoutFromParam(params.get('layout') || '', params.get('tabs') || '');
  if (layoutFromUrl) return layoutFromUrl;
  const raw = params.get('sessions') || params.get('active') || '';
  const selected = [];
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !selected.includes(item)) selected.push(item);
  }
  if (selected.length) return layoutFromSessionList(selected);
  return defaultLayoutSlots();
}

function defaultLayoutSlots() {
  const sorted = visibleSessions.slice().sort((left, right) => String(left).localeCompare(String(right)));
  const next = emptyLayoutSlots();
  if (!sorted.length) {
    next.left = emptyPlaceholderPaneState();
  } else {
    next.left = paneStateWithTabs(sorted, sorted[0]);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutWithItems(value, items, preferredSlot = null) {
  const next = normalizeLayoutSlots(value);
  const present = new Set(paneItems(next));
  const missing = items.filter(item => isLayoutItem(item) && !present.has(item));
  if (!missing.length) return next;
  let slot = preferredSlot && layoutSlotKeys(next).includes(preferredSlot) ? preferredSlot : firstEmptyPane(next) || layoutSlotKeys(next)[0];
  if (!slot) {
    slot = 'left';
    next[layoutTreeKey] = leafNode(slot);
    next[slot] = emptyPlaceholderPaneState();
  }
  if (!preferredSlot
    && activeItemForSide(slot, next) === fileExplorerItemId
    && paneTabs(slot, next).length === 1
    && missing.some(isTmuxSession)) {
    const sessionTabs = missing.filter(isTmuxSession);
    const otherTabs = missing.filter(item => !isTmuxSession(item));
    const newSlot = nextLayoutSlot(next);
    next[newSlot] = paneStateWithTabs(sessionTabs, sessionTabs[0] || null);
    next[slot] = paneStateWithTabs([...paneTabs(slot, next), ...otherTabs], activeItemForSide(slot, next));
    next[layoutTreeKey] = splitNode('row', leafNode(slot), leafNode(newSlot), fileExplorerSplitPercent);
    return compactLayoutSlots(next);
  }
  const tabs = [...paneTabs(slot, next), ...missing];
  const active = activeItemForSide(slot, next) || tabs.find(isTmuxSession) || tabs[0] || null;
  next[slot] = paneStateWithTabs(tabs, active);
  return compactLayoutSlots(next);
}

function paneTabs(side, slots = layoutSlots) {
  const state = slots?.[side];
  if (Array.isArray(state)) return state;
  return Array.isArray(state?.tabs) ? state.tabs : [];
}

function paneIsPlaceholder(side, slots = layoutSlots) {
  const state = slots?.[side];
  return Boolean(!Array.isArray(state) && state?.placeholder === true && !paneTabs(side, slots).length);
}

function paneHasLayoutContent(side, slots = layoutSlots) {
  return paneTabs(side, slots).length > 0 || paneIsPlaceholder(side, slots);
}

function paneStateForLayoutSlot(side, slots = layoutSlots) {
  return paneIsPlaceholder(side, slots)
    ? emptyPlaceholderPaneState()
    : paneStateWithTabs(paneTabs(side, slots), activeItemForSide(side, slots));
}

function slotColumn(slot) {
  if (String(slot).startsWith('right')) return 'right';
  return 'left';
}

function verticalSplitSlot(column, position) {
  return `${column}${position === 'top' ? 'Top' : 'Bottom'}`;
}

function activeItemForSide(side, slots = layoutSlots) {
  if (paneIsPlaceholder(side, slots)) return null;
  const stack = paneTabs(side, slots);
  const state = slots?.[side];
  const active = !Array.isArray(state) ? state?.active : null;
  return stack.includes(active) ? active : stack[0] || null;
}

function paneStateWithTabs(tabs, active = null) {
  const unique = [];
  for (const item of tabs) {
    if (isLayoutItem(item) && !unique.includes(item)) unique.push(item);
  }
  return {tabs: unique, active: unique.includes(active) ? active : unique[0] || null};
}

function paneItems(slots = layoutSlots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    for (const item of paneTabs(side, slots)) {
      if (!result.includes(item)) result.push(item);
    }
  }
  return result;
}

function activePaneItems(slots = layoutSlots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    const item = activeItemForSide(side, slots);
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function itemInLayout(item, slots = layoutSlots) {
  return paneItems(slots).includes(item);
}

function itemIsActivePaneTab(item, slots = layoutSlots) {
  return activePaneItems(slots).includes(item);
}

function itemIsBackgroundPaneTab(item, slots = layoutSlots) {
  return itemInLayout(item, slots) && !itemIsActivePaneTab(item, slots);
}

function allTabItems() {
  return [infoItemId, ...openFileEditorItems(), ...visibleSessions];
}

function sortTabItems(items) {
  return items
    .slice()
    .sort((left, right) => itemSortNumber(left) - itemSortNumber(right) || itemLabel(left).localeCompare(itemLabel(right)));
}

function backgroundTabItems(slots = layoutSlots) {
  const activeItems = new Set(activePaneItems(slots));
  return sortTabItems(paneItems(slots).filter(item => !activeItems.has(item)));
}

function sessionsFromSlots(slots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    const session = activeItemForSide(side, slots);
    if (session && !result.includes(session)) result.push(session);
  }
  return result;
}

function sessionsFromLayout() {
  return sessionsFromSlots(layoutSlots);
}

function isInfoItem(item) {
  return item === infoItemId;
}

function isVirtualItem(item) {
  return isInfoItem(item) || isFileExplorerItem(item) || isFileEditorItem(item);
}

function openFileEditorItems() {
  return Array.from(openFiles.keys()).map(fileEditorItemFor);
}

function computeLayoutItems() {
  return [infoItemId, fileExplorerItemId, ...openFileEditorItems(), ...visibleSessions];
}

function isTmuxSession(item) {
  return sessions.includes(item);
}

function isLayoutItem(item) {
  return layoutItems.includes(item);
}

function registerFileEditorLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  if (!openFiles.has(path)) {
    openFiles.set(path, {
      mtime: 0,
      kind: 'loading',
      original: '',
      content: '',
      dirty: false,
      loading: true,
    });
  }
  syncFileLayoutItems();
  return fileEditorItemFor(path);
}

function resolveLayoutItem(value) {
  if (value === 'info') return infoItemId;
  if (value === 'files' || value === fileExplorerItemId) return fileExplorerItemId;
  const text = String(value || '');
  if (isFileEditorItem(text)) return registerFileEditorLayoutItem(fileItemPath(text)) || text;
  if (sessions.includes(text)) return text;
  const ordinal = Number(text);
  if (Number.isInteger(ordinal) && ordinal > 0) return sessionForLabel(String(ordinal));
  return text;
}

function itemLabel(item) {
  if (isInfoItem(item)) return 'Branch Info';
  if (isFileExplorerItem(item)) return fileExplorerLabel();
  if (isFileEditorItem(item)) return basenameOf(fileItemPath(item));
  return sessionLabel(item);
}

function itemSortNumber(item) {
  if (isInfoItem(item)) return 0;
  if (isFileExplorerItem(item)) return 0.5;
  if (isFileEditorItem(item)) return 0.75;
  const label = Number(sessionLabel(item));
  return Number.isFinite(label) ? label : Number.MAX_SAFE_INTEGER;
}

function itemParam(item) {
  if (isInfoItem(item)) return 'info';
  if (isFileExplorerItem(item)) return 'files';
  if (isFileEditorItem(item)) return item;
  return String(item);
}

const stateDefs = {
  'needs-approval': {label: 'Needs approval', short: 'EXEC?', priority: 0, attention: true},
  'yolo-approval': {label: 'YOLO pending approval', short: 'YOLO?', priority: 0, attention: false},
  'needs-input': {label: 'Needs input', short: 'QUES?', priority: 1, attention: true},
  blocked: {label: 'Blocked', short: 'BLK', priority: 2, attention: true},
  disconnected: {label: 'Disconnected', short: 'OFF', priority: 3, attention: true},
  'tests-running': {label: 'Tests running', short: 'TEST', priority: 4, attention: false},
  'ready-review': {label: 'Ready for review', short: 'PR', priority: 5, attention: false},
  working: {label: 'Working', short: 'RUN', priority: 6, attention: false},
  idle: {label: 'Idle', short: 'IDLE', priority: 7, attention: false},
  done: {label: 'Done', short: 'DONE', priority: 8, attention: false},
};

function stateDef(key) {
  return stateDefs[key] || stateDefs.idle;
}

function terminalDisconnected(session) {
  if (!activeSessions.includes(session)) return false;
  const item = terminals.get(session);
  if (!item) return false;
  return item.socket?.readyState === WebSocket.CLOSED || item.socket?.readyState === WebSocket.CLOSING;
}

function sessionState(session, info = transcriptMeta.sessions?.[session]) {
  if (!isTmuxSession(session)) return {key: 'idle', ...stateDefs.idle, reason: 'not a tmux session'};
  const auto = autoApproveStates.get(session) || {};
  const autoEnabled = autoApproveEnabledForSession(auto);
  const approvalPrompt = auto.prompt || {};
  const screen = auto.screen || {};
  const lastAction = String(auto.last_action || '').toLowerCase();
  const approvalPromptVisible = approvalPrompt.visible === true;
  const approvalYesSelected = approvalPrompt.yes_selected === true;
  const approvalPromptText = String(approvalPrompt.text || 'approval prompt is visible');
  const screenKey = String(screen.key || '');
  const screenText = String(screen.text || '');
  const agents = Array.isArray(info?.agents) ? info.agents : [];
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const agentText = agents
    .map(agent => `${agent.kind || ''} ${agent.status || ''} ${agent.error || ''}`)
    .join(' ')
    .toLowerCase();
  const paneText = panes
    .map(pane => `${pane.command || ''} ${pane.title || ''}`)
    .join(' ')
    .toLowerCase();
  const pr = info?.project?.pull_request;
  const prStatus = pullRequestStatusLabel(pr).toLowerCase();
  const checksState = String(pr?.checks?.state || '').toLowerCase();

  if (terminalDisconnected(session) || (!info && terminals.has(session))) {
    return stateValue('disconnected', 'terminal connection is closed');
  }
  if (screenKey === 'disconnected') {
    return stateValue('disconnected', screenText || 'terminal screen unavailable');
  }
  if (/blocked|denied|rejected/.test(lastAction)) {
    return stateValue('blocked', 'YOLO blocked an approval prompt');
  }
  if (approvalPromptVisible && approvalYesSelected && autoEnabled) {
    return stateValue('yolo-approval', 'YOLO sees the prompt and will press Enter');
  }
  if (approvalPromptVisible && approvalYesSelected) {
    return stateValue('needs-approval', approvalPromptText || 'approval prompt is visible');
  }
  if (approvalPromptVisible) {
    return stateValue('needs-input', 'approval prompt is visible but Yes is not selected');
  }
  if (!autoEnabled && /permission|approval|approve|confirm/.test(agentText)) {
    return stateValue('needs-approval', approvalPromptText || 'approval prompt is visible');
  }
  if (screenKey === 'working') {
    return stateValue('working', screenText || 'agent is working');
  }
  if (screenKey === 'needs-input') {
    return stateValue('needs-input', screenText || 'agent is waiting for input');
  }
  if (screenKey === 'error') {
    return stateValue('blocked', screenText || 'agent screen detection failed');
  }
  if (/needs input|waiting for input|awaiting input|user input|input required|waiting for user|paused/.test(agentText)) {
    return stateValue('needs-input', 'agent is waiting for input');
  }
  if (agents.some(agent => agentErrorIsBlocking(agent.error)) || /blocked|error|failed|failure|stuck/.test(agentText)) {
    return stateValue('blocked', 'agent reported an error or blocker');
  }
  if (/pytest|cargo test|npm test|pnpm test|yarn test|vitest|jest|ctest|go test|python3 -m pytest|python -m pytest|ruff|mypy|pre-commit/.test(paneText)) {
    return stateValue('tests-running', 'test command is active');
  }
  if (pr?.number && !pr.draft && prStatus !== 'closed' && prStatus !== 'merged' && (prStatus.includes('passing') || checksState === 'success')) {
    return stateValue('ready-review', 'PR checks are passing');
  }
  if (/done|completed|complete|finished|success/.test(agentText)) {
    return stateValue('done', 'agent status is complete');
  }
  if (agents.length || panes.some(pane => pane.active) || terminals.get(session)?.socket?.readyState === WebSocket.OPEN) {
    return stateValue('working', 'agent or active pane detected');
  }
  return stateValue('idle', 'no active agent state detected');
}

function agentErrorIsBlocking(error) {
  const text = String(error || '').toLowerCase();
  if (!text) return false;
  return !(/transcript not found/.test(text) || /^missing /.test(text));
}

function stateValue(key, reason, extra = {}) {
  const def = stateDef(key);
  return {key, ...def, reason, ...extra};
}

function autoApproveEnabledHere(payload) {
  return payload?.enabled === true;
}

function autoApproveEnabledElsewhere(payload) {
  return payload?.enabled_elsewhere === true || (payload?.locked === true && payload?.enabled !== true);
}

function autoApproveEnabledForSession(payload) {
  return autoApproveEnabledHere(payload) || autoApproveEnabledElsewhere(payload);
}

function yoloEnabledSessions() {
  return sessions.filter(session => autoApproveEnabledHere(autoApproveStates.get(session)));
}

function autoApproveScreenIsWorking(payload) {
  return String(payload?.screen?.key || '') === 'working';
}

function sessionYoloIsWorking(session, payload = autoApproveStates.get(session)) {
  return autoApproveEnabledHere(payload) && autoApproveScreenIsWorking(payload);
}

function yoloRotationDelay(now = Date.now()) {
  return `${-((now % yoloRotateMs) / 1000).toFixed(3)}s`;
}

function attentionAnimationDelay(now = Date.now()) {
  return `${-((now % redReminderMs) / 1000).toFixed(3)}s`;
}

function attentionAnimationStyle() {
  return `--attention-animation-delay: ${attentionAnimationDelay()}`;
}

function syncAttentionAnimation(node, active) {
  if (!node?.style) return;
  if (active) {
    if (!node.style.getPropertyValue('--attention-animation-delay')) {
      node.style.setProperty('--attention-animation-delay', attentionAnimationDelay());
    }
  } else {
    node.style.removeProperty('--attention-animation-delay');
  }
}

function stateBadgeHtml(key, short, title) {
  const classes = ['session-state-badge', 'tab-symbol', `session-state-${key}`];
  const attention = stateDef(key).attention;
  if (attention) classes.push('session-state-reminder');
  const style = attention ? ` style="${attentionAnimationStyle()}"` : '';
  return `<span class="${esc(classes.join(' '))}"${style} title="${esc(title)}">${esc(short)}</span>`;
}

function sessionStateHtml(state) {
  if (!state || ['working', 'tests-running', 'done', 'disconnected', 'yolo-approval'].includes(state.key)) return '';
  return stateBadgeHtml(state.key, state.short, `${state.label}: ${state.reason}`);
}

function inactiveTabItems() {
  const inPane = new Set(paneItems());
  return sortTabItems(allTabItems().filter(item => !inPane.has(item)));
}

function renderNotifyToggle() {
  if (!notifyToggle) return;
  const supported = 'Notification' in window;
  notifyToggle.disabled = readOnlyMode;
  notifyToggle.classList.toggle('active', notificationsEnabled);
  notifyToggle.setAttribute('aria-pressed', notificationsEnabled ? 'true' : 'false');
  notifyToggle.setAttribute('aria-label', 'Notify');
  const browserState = supported ? Notification.permission : 'unsupported';
  notifyToggle.title = readOnlyMode
    ? 'Notify is admin-only'
    : `notify when a session needs attention; browser notifications: ${browserState}`;
}

async function toggleNotifications() {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change Notify</span>';
    return;
  }
  const nextEnabled = !notificationsEnabled;
  let browserPermission = 'unsupported';
  if (nextEnabled && 'Notification' in window && Notification.permission === 'default') {
    const permission = await Notification.requestPermission();
    browserPermission = permission;
  } else if ('Notification' in window) {
    browserPermission = Notification.permission;
  }
  try {
    const response = await apiFetch(`/api/notify?enabled=${nextEnabled ? '1' : '0'}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText || `HTTP ${response.status}`);
    notificationsEnabled = payload.enabled === true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">Notify request failed: ${esc(error)}</span>`;
    return;
  }
  renderNotifyToggle();
  renderSessionButtons();
  if (notificationsEnabled) {
    if (browserPermission !== 'granted') {
      statusEl.innerHTML = `<span class="ok">in-page alerts on; browser notifications ${esc(browserPermission)}</span>`;
    }
    sendTestNotification();
    notifyCurrentAttentionStates();
  } else {
    statusEl.innerHTML = '<span class="ok">Notify off</span>';
  }
}

async function loadNotifyStatus() {
  try {
    const response = await apiFetch('/api/notify', {cache: 'no-store'});
    const payload = await response.json();
    notificationsEnabled = response.ok && payload.enabled === true;
  } catch (_) {
    notificationsEnabled = false;
  }
  renderNotifyToggle();
}

function logOut() {
  window.location.href = '/logout';
}

function appMenuUiIcon(kind, active = false) {
  return `<span class="app-menu-ui-icon app-menu-ui-icon-${esc(kind)} ${active ? 'active' : ''}" aria-hidden="true"></span>`;
}

function projectReadmePath() {
  const root = String(repoRoot || '').replace(/\/+$/, '');
  return root ? `${root}/README.md` : '';
}

async function openProjectReadme() {
  const path = projectReadmePath();
  if (!path) {
    statusEl.innerHTML = '<span class="err">README path is unavailable</span>';
    return;
  }
  await openFileInEditor(path, 'README.md');
}

function keyboardShortcutItems() {
  return [
    menuCommand('Save active editor', null, {disabled: true, detail: 'Ctrl/Cmd+S'}),
    menuCommand('Close menu or dialog', null, {disabled: true, detail: 'Esc'}),
    menuCommand('Session actions', null, {disabled: true, detail: 'Right-click a tmux tab'}),
    menuCommand('Move or split tab', null, {disabled: true, detail: 'Drag a tab'}),
  ];
}

function shouldNotifyState(state) {
  return ['needs-approval', 'needs-input', 'blocked', 'ready-review'].includes(state.key);
}

function sendBrowserNotification(title, options = {}) {
  const notification = new Notification(title, options);
  notification.onclick = () => {
    window.focus();
    if (options.session) selectSession(options.session);
  };
  return notification;
}

function setToastCountdown(node, durationMs) {
  if (!node) return;
  if (!Number.isFinite(durationMs)) {
    node.style.removeProperty('--toast-countdown-duration');
    return;
  }
  node.style.setProperty('--toast-countdown-duration', `${Math.max(1, durationMs)}ms`);
}

// Upload and attention/status messages share this renderer. Keep visual differences out of call sites.
function ensureToastShell(node, options = {}) {
  let bodyNode = node.querySelector('.toast-body');
  if (!bodyNode) {
    node.innerHTML = `
      <div class="toast-header">
        <div class="toast-title"></div>
        <div class="toast-control-row">
          <button type="button" class="toast-keep" data-toast-keep aria-label="${esc(options.keepLabel || 'Keep alert visible')}">Keep</button>
          <button type="button" class="toast-close" data-toast-close aria-label="${esc(options.closeLabel || 'Close alert')}">x</button>
        </div>
      </div>
      <div class="toast-body"></div>
      <div class="toast-actions"></div>`;
    bodyNode = node.querySelector('.toast-body');
  }
  const titleNode = node.querySelector('.toast-title');
  if (titleNode) titleNode.textContent = options.title || '';
  const actionsNode = node.querySelector('.toast-actions');
  if (actionsNode) {
    actionsNode.replaceChildren(...(options.actions || []));
    actionsNode.hidden = !actionsNode.children.length;
  }
  const closeButton = node.querySelector('[data-toast-close]');
  if (closeButton) {
    closeButton.onclick = event => {
      event.stopPropagation();
      options.onClose?.();
    };
  }
  const keepButton = node.querySelector('[data-toast-keep]');
  if (keepButton) {
    keepButton.onclick = event => {
      event.stopPropagation();
      node.classList.add('kept');
      keepButton.hidden = true;
      options.onKeep?.();
    };
  }
  return bodyNode;
}

function renderToastLines(bodyNode, lines, options = {}) {
  bodyNode.replaceChildren();
  for (const item of summarizeToastLines(lines, options)) {
    const lineText = typeof item === 'object' && item !== null ? item.text : item;
    const countdownMs = typeof item === 'object' && item !== null ? item.countdownMs : options.countdownMs;
    const line = document.createElement('div');
    line.className = 'toast-line';
    setToastCountdown(line, countdownMs || toastDurationMs);
    line.textContent = lineText;
    bodyNode.appendChild(line);
  }
}

function normalizeToastLine(item, options = {}) {
  const objectItem = typeof item === 'object' && item !== null;
  const text = objectItem ? item.text : item;
  return {
    text: compactToastText(text),
    countdownMs: objectItem ? item.countdownMs : options.countdownMs,
  };
}

function compactToastText(text) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (value.length <= toastMaxLineChars) return value;
  return `${value.slice(0, toastMaxLineChars - 3)}...`;
}

function summarizeToastLines(lines, options = {}) {
  const normalized = (Array.isArray(lines) ? lines : toastTextLines(lines)).map(item => normalizeToastLine(item, options));
  if (normalized.length <= toastMaxLines) return normalized;
  const visible = normalized.slice(0, toastMaxLines - 1);
  const hidden = normalized.slice(toastMaxLines - 1);
  const countdownValues = hidden.map(item => item.countdownMs).filter(Number.isFinite);
  visible.push({
    text: `+${hidden.length} more`,
    countdownMs: countdownValues.length ? Math.max(...countdownValues) : options.countdownMs,
  });
  return visible;
}

function toastTextLines(text) {
  const lines = String(text || '').split('\n').map(line => line.trim()).filter(Boolean);
  return lines.length ? lines : [''];
}

function showToast(title, lines, options = {}) {
  const container = options.container || attentionAlerts;
  if (!container) return null;
  const id = ++attentionAlertSequence;
  const node = document.createElement('div');
  node.className = options.className || 'attention-alert toast';
  node.dataset.alertId = String(id);
  const bodyNode = ensureToastShell(node, {
    title,
    closeLabel: options.closeLabel,
    keepLabel: options.keepLabel,
    actions: options.actions,
    onKeep: () => {
      if (attentionAlertTimers.has(id)) {
        clearTimeout(attentionAlertTimers.get(id));
        attentionAlertTimers.delete(id);
      }
      options.onKeep?.();
    },
    onClose: () => {
      options.onClose?.();
      removeAttentionAlert(id);
    },
  });
  renderToastLines(bodyNode, Array.isArray(lines) ? lines : toastTextLines(lines), {
    countdownMs: options.countdownMs || toastDurationMs,
  });
  node.addEventListener('click', event => {
    if (event.target.closest('[data-toast-close], .toast-actions')) return;
    options.onClick?.();
  });
  container.appendChild(node);
  while (container.children.length > 5) {
    const first = container.firstElementChild;
    if (!first) break;
    removeAttentionAlert(Number(first.dataset.alertId || 0));
  }
  attentionAlertTimers.set(id, window.setTimeout(() => removeAttentionAlert(id), toastDurationMs));
  return node;
}

function displayToastContainer(session) {
  const sessionContainer = session ? document.getElementById(`panel-toasts-${session}`) : null;
  if (sessionContainer && sessionContainer.isConnected !== false) return sessionContainer;
  const candidates = [focusedPanelItem, ...activeSessions];
  for (const item of candidates) {
    const node = item ? document.getElementById(`panel-toasts-${item}`) : null;
    if (node && node.isConnected !== false) return node;
  }
  return document.querySelector('.panel-toast-stack') || attentionAlerts;
}

function showAttentionAlert(session, state) {
  const node = showToast(
    `YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`,
    state.reason,
    {
      container: displayToastContainer(session),
      onClick: () => selectSession(session),
    },
  );
  if (node) {
    node.dataset.toastSession = session;
    node.dataset.toastKind = 'attention';
  }
}

function dismissAttentionAlertsForSession(session) {
  for (const node of document.querySelectorAll('.toast[data-toast-kind="attention"]')) {
    if (node.dataset.toastSession !== session) continue;
    removeAttentionAlert(Number(node.dataset.alertId || 0));
  }
}

function attentionAlreadyVisible(session) {
  if (document.visibilityState !== 'visible') return false;
  if (!activeSessions.includes(session)) return false;
  const panel = document.getElementById(`panel-${session}`);
  if (!panel || !panel.isConnected) return false;
  return focusedPanelItem === session || focusedTerminal === session || activeSessions.length === 1;
}

function removeAttentionAlert(id) {
  if (attentionAlertTimers.has(id)) {
    clearTimeout(attentionAlertTimers.get(id));
    attentionAlertTimers.delete(id);
  }
  document.querySelector(`[data-alert-id="${id}"]`)?.remove();
}

function sendTestNotification() {
  showToast(`YOLOmux - ${serverHostname}: notifications enabled`, 'YOLOmux in-page alerts are enabled.', {
    container: displayToastContainer(focusedPanelItem),
  });
  if (!notificationsEnabled || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(`YOLOmux - ${serverHostname}: notifications enabled`, {
      body: 'YOLOmux can send browser notifications from this server.',
      tag: `yolomux:test:${Date.now()}`,
    });
    postEvent(null, 'notification_test_sent', 'notification test sent', {hostname: serverHostname});
  } catch (error) {
    statusEl.innerHTML = `<span class="err">notification failed: ${esc(error)}</span>`;
    postEvent(null, 'notification_error', `notification test failed: ${error}`, {hostname: serverHostname});
  }
}

function notifyCurrentAttentionStates() {
  for (const session of sessions.filter(isTmuxSession)) {
    const state = sessionState(session, transcriptMeta.sessions?.[session]);
    if (shouldNotifyState(state)) maybeNotifyState(session, state, {force: true});
  }
}

function eventMessageForState(session, state) {
  return `${sessionLabel(session)} ${state.label}: ${state.reason}`;
}

function stateSignature(state) {
  return `${state.key}:${state.reason || ''}`;
}

function trackSessionStateChanges() {
  for (const session of sessions.filter(isTmuxSession)) {
    const state = sessionState(session, transcriptMeta.sessions?.[session]);
    const previous = sessionStateKeys.get(session);
    const signature = stateSignature(state);
    sessionStateKeys.set(session, {key: state.key, reason: state.reason, signature});
    if (!stateTrackingReady || previous == null || previous.signature === signature) continue;
    postEvent(session, 'state_changed', eventMessageForState(session, state), {
      from: previous.key,
      from_reason: previous.reason,
      to: state.key,
      reason: state.reason,
    });
    maybeNotifyState(session, state);
  }
  stateTrackingReady = true;
}

function maybeNotifyState(session, state, options = {}) {
  if (!notificationsEnabled) return;
  if (!shouldNotifyState(state)) return;
  const key = `${session}:${stateSignature(state)}`;
  const now = Date.now();
  if (attentionAlreadyVisible(session)) {
    notificationLastSent.set(key, now);
    dismissAttentionAlertsForSession(session);
    postEvent(session, 'alert_suppressed_visible', eventMessageForState(session, state), {
      state: state.key,
      reason: state.reason,
    });
    return;
  }
  const lastSent = notificationLastSent.get(key) || 0;
  if (options.force !== true && now - lastSent < 60_000) return;
  notificationLastSent.set(key, now);
  const body = `${state.reason} · ${projectDirName(session, transcriptMeta.sessions?.[session])}`;
  showAttentionAlert(session, state);
  postEvent(session, 'alert_shown', eventMessageForState(session, state), {
    state: state.key,
    reason: state.reason,
  });
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(`YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`, {
      body,
      tag: key,
      renotify: true,
      session,
    });
    postEvent(session, 'notification_sent', eventMessageForState(session, state), {
      state: state.key,
      reason: state.reason,
    });
  } catch (error) {
    postEvent(session, 'notification_error', `notification failed: ${error}`, {
      state: state.key,
    });
  }
}

function updateTopbarMetrics() {
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height || 38);
  document.documentElement?.style?.setProperty('--topbar-height', `${height}px`);
}

function scheduleTopbarMetricsUpdate() {
  requestAnimationFrame(updateTopbarMetrics);
}

function bindTopbarMetrics() {
  updateTopbarMetrics();
  if (topbarResizeObserver || !topbar || !window.ResizeObserver) return;
  topbarResizeObserver = new ResizeObserver(updateTopbarMetrics);
  topbarResizeObserver.observe(topbar);
}

function scheduleTabStripOverflowCheck(strip) {
  if (!strip) return;
  if (strip === sessionButtons || strip.classList?.contains('pane-tabs')) {
    strip.classList.remove('tabs-overflowing');
    scheduleTopbarMetricsUpdate();
    return;
  }
  strip.classList.remove('tabs-overflowing');
  requestAnimationFrame(() => {
    strip.classList.toggle('tabs-overflowing', strip.scrollWidth > strip.clientWidth + 1);
  });
}

function scheduleAllTabStripOverflowChecks() {
  scheduleTabStripOverflowCheck(sessionButtons);
  for (const strip of document.querySelectorAll('.pane-tabs')) {
    scheduleTabStripOverflowCheck(strip);
  }
}

function normalizedSessionOrder(nextSessions) {
  if (!Array.isArray(nextSessions)) return null;
  const next = [];
  for (const session of nextSessions) {
    if (typeof session === 'string' && session && !next.includes(session)) next.push(session);
  }
  return next;
}

function setSessionOrder(next) {
  sessions = next;
  visibleSessions = sessions.slice(0, maxSessionTabs);
  layoutItems = computeLayoutItems();
}

function updateSessionList(nextSessions) {
  const next = normalizedSessionOrder(nextSessions);
  if (!next) return false;
  const changed = next.length !== sessions.length || next.some((session, index) => session !== sessions[index]);
  if (!changed) return false;
  const removedSessions = visibleSessions.filter(session => !next.includes(session));
  setSessionOrder(next);
  layoutSlots = normalizeLayoutSlots(layoutSlots, {
    preserveRemovedItems: removedSessions,
    preserveRemovedSlots: true,
  });
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  return true;
}

function applyLayoutSlots(nextSlots, options = {}) {
  const previousActive = activeSessions.slice();
  layoutSlots = normalizeLayoutSlots(nextSlots);
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  renderSessionButtons();
  renderPanels(previousActive, {prune: options.prune});
  for (const session of activeSessions.filter(isTmuxSession)) ensureTerminalRunning(session);
  refreshTranscripts();
  renderAutoApproveButtons();
  if (options.focusSession && activeSessions.includes(options.focusSession)) {
    setTimeout(() => focusPanel(options.focusSession), 80);
  } else if (options.message && activeSessions.length) {
    statusEl.textContent = options.message;
  } else {
    updateStatus();
  }
}

function updateActiveSessionParam() {
  const params = new URLSearchParams(location.search);
  params.delete('active');
  params.delete('sessions');
  params.delete('layout');
  params.delete('tabs');
  const queryParts = [];
  const inactiveItems = inactiveTabItems();
  if (activeSessions.length || inactiveItems.length) {
    if (activeSessions.length) {
      queryParts.push(`sessions=${activeSessions.map(readableItemParam).join(',')}`);
    }
    queryParts.push(`layout=${layoutParamValue(layoutSlots)}`);
    const tabs = layoutTabsParamValue(layoutSlots);
    if (tabs) queryParts.push(`tabs=${tabs}`);
  }
  const remaining = params.toString();
  if (remaining) queryParts.push(remaining);
  const query = queryParts.join('&');
  history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
}

function syncInitialLayoutUrl() {
  updateActiveSessionParam();
}

function menuCommand(label, action, options = {}) {
  return {type: 'command', label, action, ...options};
}

function menuSubmenu(label, items, options = {}) {
  return {type: 'submenu', label, items, ...options};
}

function menuSection(label) {
  return {type: 'section', label};
}

function menuSeparator() {
  return {type: 'separator'};
}

function currentActiveMenuItem() {
  if (focusedPanelItem && itemIsActivePaneTab(focusedPanelItem)) return focusedPanelItem;
  if (focusedTerminal && itemIsActivePaneTab(focusedTerminal)) return focusedTerminal;
  return activePaneItems()[0] || null;
}

function currentSessionActionTarget() {
  const current = currentActiveMenuItem();
  if (isTmuxSession(current)) return current;
  if (isTmuxSession(lastFocusedTmuxSession) && activeSessions.includes(lastFocusedTmuxSession)) return lastFocusedTmuxSession;
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  return activeTmuxSessions.length === 1 ? activeTmuxSessions[0] : null;
}

function orderedPaneItems(items = activePaneItems()) {
  const unique = [];
  for (const group of [
    items.filter(isTmuxSession),
    items.filter(isFileEditorItem),
    items.filter(item => !isTmuxSession(item) && !isFileEditorItem(item)),
  ]) {
    for (const item of group) {
      if (!unique.includes(item)) unique.push(item);
    }
  }
  return unique;
}

function menuTabDetail(item) {
  if (isFileEditorItem(item)) return compactHomePath(fileItemPath(item));
  if (isFileExplorerItem(item)) return compactHomePath(fileExplorerRoot || homePath || '/');
  if (isInfoItem(item)) return 'Branch and repo metadata';
  return tabMenuDetailText(item, transcriptMeta.sessions?.[item]);
}

function menuTabRowHtml(item, options = {}) {
  if (isInfoItem(item)) return paneInfoTabHtml();
  if (isFileExplorerItem(item)) return fileExplorerPaneTabHtml();
  if (isFileEditorItem(item)) return fileEditorPaneTabHtml(item);
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true;
  const state = sessionState(item, info);
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(item, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="pane-tab-core">${yoloMarkerHtml(item, auto, {enabledOnly: false, toggle: options.toggleYolo === true, yoloWorking: sessionYoloIsWorking(item)})}<span class="session-button-prefix">${sessionNumberNameHtml(item)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(item, info)}${pullRequestCompactBadgesHtml(item, pr)}${detailHtml}</span></span>`;
}

function menuTabCommand(item, options = {}) {
  const slot = slotForSession(item);
  const visible = itemIsActivePaneTab(item);
  const active = item === currentActiveMenuItem();
  const detail = options.detail || (visible ? menuTabDetail(item) : (itemIsBackgroundPaneTab(item) ? 'Minimized: in a pane, not shown' : 'Inactive: not in a pane'));
  return menuCommand(itemLabel(item), () => {
    if (slot && visible && !options.openAsPane) return activatePaneTab(slot, item);
    return selectSession(item);
  }, {
    checked: options.checked ?? active,
    detail: '',
    ariaLabel: [itemLabel(item), detail].filter(Boolean).join(' - '),
    html: stripTitleAttrs(menuTabRowHtml(item)),
    className: 'app-menu-tab-command',
  });
}

function tmuxSessionActionCommands(session, options = {}) {
  const hasSession = isTmuxSession(session);
  const autoPayload = hasSession ? autoApproveStates.get(session) : null;
  const autoHere = hasSession ? autoApproveEnabledHere(autoPayload) : false;
  const readonlyDetail = 'Admin only';
  const focusDetail = hasSession ? menuTabDetail(session) : 'Focus a tmux session first';
  const visibleDetail = readOnlyMode ? readonlyDetail : (hasSession ? '' : 'No tmux tab focused');
  const yoloLabel = `${autoHere ? 'Disable' : 'Enable'} YOLO for Tmux Session${hasSession ? ` '${session}'` : ''}`;
  const renameAction = options.renameAction || (() => renameTmuxSession(session));
  const commands = [
    menuCommand('Rename session', renameAction, {
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: ['Rename session', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Kill session', () => killTmuxSession(session), {
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: ['Kill session', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand(yoloLabel, async () => {
      if (!hasSession) return;
      await toggleAutoApprove(session);
      renderSessionButtons();
      renderPaneTabStrips();
    }, {
      checked: autoHere,
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: [yoloLabel, hasSession ? focusDetail : 'Focus a tmux session first'].filter(Boolean).join(' - '),
    }),
  ];
  return commands;
}

function tmuxYoloSessionCommands() {
  const ordered = [
    ...sessions.filter(session => autoApproveEnabledHere(autoApproveStates.get(session))),
    ...sessions.filter(session => !autoApproveEnabledHere(autoApproveStates.get(session))),
  ];
  if (!ordered.length) return [menuCommand('No tmux sessions', null, {disabled: true})];
  return ordered.map(session => {
    const payload = autoApproveStates.get(session);
    const enabled = autoApproveEnabledHere(payload);
    const elsewhere = autoApproveEnabledElsewhere(payload);
    const label = `${sessionLabel(session)}${enabled ? ' on' : ''}`;
    return menuCommand(label, async () => {
      await setAutoApprove(session, !enabled);
      renderSessionButtons({force: true});
      renderPaneTabStrips();
    }, {
      checked: enabled,
      disabled: readOnlyMode,
      detail: elsewhere ? 'owned by another server' : (enabled ? 'YOLO enabled here' : 'YOLO off'),
      ariaLabel: `${enabled ? 'Disable' : 'Enable'} YOLO for ${sessionLabel(session)}`,
    });
  });
}

function tmuxSessionViewCommands(session) {
  const hasSession = isTmuxSession(session);
  const active = hasSession && activeSessions.includes(session);
  const focusDetail = hasSession ? menuTabDetail(session) : 'Focus a tmux session first';
  const disabledDetail = hasSession ? 'Open the tab in a pane first' : 'No tmux tab focused';
  return [
    menuCommand('Transcript', () => {
      if (active) activateTab(session, 'transcript');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      ariaLabel: ['Transcript', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('AI summary', () => {
      if (active) activateTab(session, 'summary');
    }, {
      disabled: readOnlyMode || !active,
      detail: readOnlyMode ? 'Admin only' : (active ? '' : disabledDetail),
      ariaLabel: ['AI summary', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Event log', () => {
      if (active) activateTab(session, 'events');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      ariaLabel: ['Event log', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Branch Info', () => {
      if (!active) return;
      const panel = document.getElementById(`panel-${session}`);
      if (panel) setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      ariaLabel: ['Branch Info', focusDetail].filter(Boolean).join(' - '),
    }),
  ];
}

function newTmuxSessionItems() {
  return ['claude', 'codex', 'term'].map(agent => {
    const available = availableAgents.has(agent);
    const capped = visibleSessions.length >= maxSessionTabs;
    return menuCommand(`+ ${agentName(agent)}`, () => createNextSession(agent), {
      iconHtml: agentIcon(agent),
      disabled: readOnlyMode || !available || capped,
      detail: readOnlyMode
        ? 'Admin only'
        : (!available ? `${agentName(agent)} unavailable` : (capped ? 'Limit reached' : 'Create tmux session')),
    });
  });
}

function tabCommandsForItems(items, options) {
  return items.map(item => menuTabCommand(item, options));
}

function backgroundTabMenuItems() {
  return tabCommandsForItems(backgroundTabItems(), {
    checked: false,
    detail: 'Minimized',
    openAsPane: true,
  });
}

function inactiveTabMenuItems() {
  return tabCommandsForItems(inactiveTabItems(), {
    checked: false,
    detail: 'Not in a pane',
    openAsPane: true,
  });
}

function tabMenuItems(openItems = orderedPaneItems(activePaneItems())) {
  const groups = [
    ['Active', openItems.map(item => menuTabCommand(item))],
    ['Minimized', backgroundTabMenuItems()],
    ['Inactive', inactiveTabMenuItems()],
  ].filter(([, items]) => items.length);
  const items = [];
  for (const [index, [label, commands]] of groups.entries()) {
    if (index) items.push(menuSeparator());
    items.push(menuSection(label), ...commands);
  }
  return items;
}

function appMenuTree() {
  const activeTmux = currentSessionActionTarget();
  const openItems = orderedPaneItems(activePaneItems());
  const yoloCount = yoloEnabledSessions().length;
  return [
    {
      id: 'file',
      label: 'File',
      items: [
        menuCommand(fileExplorerLabel(), () => selectSession(fileExplorerItemId), {
          checked: itemInLayout(fileExplorerItemId),
          detail: 'Browse files',
        }),
        menuCommand('Open file', null, {
          disabled: true,
          detail: `Use ${fileExplorerLabel()} for now`,
        }),
        menuSeparator(),
        menuCommand('Log out', logOut, {
          detail: 'End this browser session',
        }),
      ],
    },
    {
      id: 'view',
      label: 'View',
      items: [
        menuCommand(tabMetaVisible ? 'Hide tab metadata' : 'Show tab metadata', toggleTabMetadata, {
          checked: tabMetaVisible,
          detail: 'Branch, state, PR, cwd',
        }),
        menuTabCommand(infoItemId, {
          checked: itemIsActivePaneTab(infoItemId),
          detail: 'Open the repository overview panel',
        }),
        menuSubmenu('Layout', [
          menuCommand('Single pane', null, {disabled: true, detail: 'Coming soon'}),
          menuCommand('Grid', null, {disabled: true, detail: 'Drag panes for now'}),
          menuCommand('Wall', null, {disabled: true}),
        ]),
      ],
    },
    {
      id: 'tmux',
      label: 'Tmux',
      badgeText: yoloCount ? String(yoloCount) : '',
      badgeTitle: yoloCount ? `${yoloCount} tmux session${yoloCount === 1 ? '' : 's'} with YOLO enabled` : '',
      items: [
        menuSubmenu('New tmux session', newTmuxSessionItems()),
        menuSubmenu(`YOLO sessions${yoloCount ? ` (${yoloCount})` : ''}`, tmuxYoloSessionCommands()),
        menuSeparator(),
        ...tmuxSessionViewCommands(activeTmux),
        menuSeparator(),
        ...tmuxSessionActionCommands(activeTmux),
        menuCommand('Resume session', null, {
          disabled: true,
          detail: 'Coming soon',
        }),
      ],
    },
    {
      id: 'tab',
      label: 'Tab',
      items: tabMenuItems(openItems),
    },
    {
      id: 'settings',
      label: 'Settings',
      items: [
        menuCommand('Tab metadata', toggleTabMetadata, {
          iconHtml: appMenuUiIcon('tab-meta', tabMetaVisible),
        }),
        menuCommand('Notify', toggleNotifications, {
          disabled: readOnlyMode,
          detail: readOnlyMode ? 'Requires admin access' : '',
          iconHtml: appMenuUiIcon('notify', notificationsEnabled),
        }),
        menuCommand('Refresh', refreshAll, {
          iconHtml: appMenuUiIcon('refresh'),
        }),
      ],
    },
    {
      id: 'help',
      label: 'Help',
      items: [
        menuCommand(`YOLOmux ${bootstrap.version || ''}`.trim(), null, {
          disabled: true,
          detail: bootstrap.versionCommitTime ? `Last commit: ${bootstrap.versionCommitTime}` : '',
        }),
        menuSubmenu('Keyboard shortcuts', keyboardShortcutItems()),
        menuCommand('Open README', openProjectReadme, {
          disabled: !projectReadmePath(),
          detail: 'Local README',
        }),
      ],
    },
  ];
}

function appMenuIsOpen() {
  return Boolean(sessionButtons?.querySelector('.app-menu.open'));
}

function renderSessionButtons(options = {}) {
  if (!sessionButtons) return;
  if (!options.force && appMenuIsOpen()) {
    scheduleTopbarMetricsUpdate();
    return;
  }
  const openMenu = sessionButtons.querySelector('.app-menu.open');
  if (openMenu) {
    openAppMenuId = openMenu.dataset.appMenu || null;
  } else {
    openAppMenuId = null;
    openAppMenuPinned = false;
    openAppMenuOpenedAt = 0;
  }
  appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  appMenuCloseTimer = clearTimer(appMenuCloseTimer);
  sessionButtons.innerHTML = '';
  sessionButtons.ondragover = event => {
    const payload = dragPayload(event);
    if (!payload?.session || !itemInLayout(payload.session)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    clearDropPreview();
    sessionButtons.classList.add('drag-over');
  };
  sessionButtons.ondragleave = event => {
    if (!sessionButtons.contains(event.relatedTarget)) sessionButtons.classList.remove('drag-over');
  };
  sessionButtons.ondrop = event => {
    const payload = dragPayload(event);
    sessionButtons.classList.remove('drag-over');
    if (!payload?.session) return;
    event.preventDefault();
    event.stopPropagation();
    removeSessionFromLayout(payload.session);
  };
  sessionButtons.classList.remove('drag-over');
  sessionButtons.appendChild(createAppMenuBar());
  scheduleTopbarMetricsUpdate();
}

function createAppMenuBar() {
  const bar = document.createElement('nav');
  bar.className = 'app-menu-bar';
  bar.setAttribute('aria-label', 'Application menu');
  bar.setAttribute('role', 'menubar');
  for (const menu of appMenuTree()) bar.appendChild(createAppMenu(menu));
  return bar;
}

function appMenuViewportInlineSize() {
  return Math.max(0, document.documentElement?.clientWidth || window.innerWidth || 0);
}

function appMenuAnchorInlineSize(popover) {
  const anchor = popover?.parentElement?.querySelector?.(':scope > .app-menu-button, :scope > .app-menu-command');
  return Math.ceil(anchor?.getBoundingClientRect?.().width || 0);
}

function measureAppMenuContentWidth(popover) {
  if (!popover?.cloneNode || !document.body) return null;
  const clone = popover.cloneNode(true);
  clone.style.position = 'fixed';
  clone.style.insetInlineStart = '0';
  clone.style.insetBlockStart = '0';
  clone.style.transform = 'translateX(-100%)';
  clone.style.visibility = 'hidden';
  clone.style.pointerEvents = 'none';
  clone.style.opacity = '0';
  clone.style.width = 'max-content';
  clone.style.minWidth = '0';
  clone.style.maxWidth = 'none';
  clone.style.maxHeight = 'none';
  clone.style.removeProperty('--app-menu-fit-width');
  clone.style.removeProperty('--app-menu-fit-offset');
  clone.querySelectorAll('.app-menu-command').forEach(command => {
    command.style.width = 'max-content';
    command.style.minWidth = '0';
    command.style.maxWidth = 'none';
  });
  clone.querySelectorAll('.app-menu-rich, .pane-tab-core, .session-button-text, .session-button-name, .session-button-dir, .session-button-detail, .tab-inline-detail, .pane-tab-info-label').forEach(node => {
    node.style.maxWidth = 'none';
    node.style.overflow = 'visible';
    node.style.textOverflow = 'clip';
    node.style.whiteSpace = 'nowrap';
  });
  document.body.appendChild(clone);
  const width = Math.ceil(clone.getBoundingClientRect().width || clone.scrollWidth || 0);
  clone.remove();
  return width || null;
}

function fitAppMenuPopover(popover) {
  if (!popover) return;
  popover.style.setProperty('--app-menu-fit-offset', '0px');
  popover.style.removeProperty('--app-menu-fit-width');
  const measured = measureAppMenuContentWidth(popover);
  const anchorWidth = appMenuAnchorInlineSize(popover);
  const desiredWidth = Math.max(anchorWidth, measured || 0);
  if (desiredWidth > 0) popover.style.setProperty('--app-menu-fit-width', `${desiredWidth}px`);

  const rect = popover.getBoundingClientRect();
  const viewportRight = Math.max(0, appMenuViewportInlineSize() - popoverEdgeGapPx());
  if (!rect.width || !viewportRight) return;
  const overflow = Math.max(0, rect.right - viewportRight);
  if (!overflow) return;
  const maxShift = Math.max(0, rect.width - anchorWidth);
  popover.style.setProperty('--app-menu-fit-offset', `${-Math.min(overflow, maxShift)}px`);
}

function createAppMenu(menu) {
  const wrapper = document.createElement('div');
  wrapper.className = 'app-menu';
  wrapper.dataset.appMenu = menu.id;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'app-menu-button';
  button.setAttribute('aria-haspopup', 'true');
  button.setAttribute('aria-expanded', openAppMenuId === menu.id ? 'true' : 'false');
  button.setAttribute('role', 'menuitem');
  button.innerHTML = `${esc(menu.label)}${menu.badgeText ? `<span class="app-menu-button-badge" title="${esc(menu.badgeTitle || '')}">${esc(menu.badgeText)}</span>` : ''}`;
  const popover = document.createElement('div');
  popover.className = 'app-menu-popover';
  popover.setAttribute('role', 'menu');
  popover.setAttribute('aria-label', menu.label);
  for (const item of menu.items) popover.appendChild(createAppMenuItem(item));
  fitAppMenuPopover(popover);
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (wrapper.classList.contains('open')) {
      const openMs = Date.now() - openAppMenuOpenedAt;
      if (openAppMenuPinned && openMs >= menuClickCloseGraceMs) closeAppMenus();
      else openAppMenu(wrapper, {focusFirst: false, pinned: true});
      return;
    }
    openAppMenu(wrapper, {focusFirst: false, pinned: true});
  });
  button.addEventListener('keydown', event => handleAppMenuButtonKeydown(event, wrapper));
  wrapper.addEventListener('pointerenter', () => {
    appMenuCloseTimer = clearTimer(appMenuCloseTimer);
    queueAppMenuHoverOpen(wrapper);
  });
  wrapper.addEventListener('pointerleave', () => {
    if (wrapper.classList.contains('open')) queueAppMenuHoverClose(wrapper);
    else appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  });
  wrapper.append(button, popover);
  if (openAppMenuId === menu.id) wrapper.classList.add('open');
  return wrapper;
}

function createAppMenuItem(item) {
  if (item.type === 'separator') {
    const node = document.createElement('div');
    node.className = 'app-menu-separator';
    node.role = 'separator';
    return node;
  }
  if (item.type === 'section') {
    const node = document.createElement('div');
    node.className = 'app-menu-section';
    node.textContent = item.label;
    return node;
  }
  if (item.type === 'submenu') return createAppSubmenu(item);
  return createAppMenuCommand(item);
}

function createAppSubmenu(item) {
  const wrapper = document.createElement('div');
  wrapper.className = 'app-menu-submenu-wrap open';
  const button = createAppMenuCommand({
    label: item.label,
    disabled: item.disabled,
    detail: item.detail,
    className: 'app-menu-submenu-button',
  }, {asSubmenu: true});
  const submenu = document.createElement('div');
  submenu.className = 'app-submenu-popover';
  submenu.setAttribute('role', 'menu');
  submenu.setAttribute('aria-label', item.label);
  for (const child of item.items || []) submenu.appendChild(createAppMenuItem(child));
  fitAppMenuPopover(submenu);
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) return;
    wrapper.classList.add('open');
  });
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      wrapper.classList.add('open');
      focusFirstAppMenuCommand(submenu);
    }
  });
  wrapper.append(button, submenu);
  return wrapper;
}

function createAppMenuCommand(item, options = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = ['app-menu-command', item.className || '', options.asSubmenu ? 'has-submenu' : '', item.checked ? 'has-check' : ''].filter(Boolean).join(' ');
  button.setAttribute('role', 'menuitem');
  if (item.checked) button.dataset.checked = 'true';
  if (item.disabled) button.disabled = true;
  const ariaLabel = item.ariaLabel || [item.label, item.detail].filter(Boolean).join(' - ');
  if (ariaLabel) button.setAttribute('aria-label', ariaLabel);
  const richHtml = item.html ? stripTitleAttrs(item.html) : '';
  const iconHtml = item.iconHtml ? stripTitleAttrs(item.iconHtml) : '';
  const contentHtml = richHtml
    ? `<span class="app-menu-rich">${richHtml}</span>`
    : `<span class="app-menu-line">${iconHtml ? `<span class="app-menu-icon">${iconHtml}</span>` : ''}<span class="app-menu-label">${esc(item.label)}</span></span>`;
  const detailHtml = item.detail ? `<span class="app-menu-detail">${esc(item.detail)}</span>` : '';
  button.innerHTML = `<span class="app-menu-check" aria-hidden="true"></span><span class="app-menu-content">${contentHtml}${detailHtml}</span>${options.asSubmenu ? '<span class="app-menu-submenu-arrow" aria-hidden="true">&gt;</span>' : ''}`;
  if (!options.asSubmenu) {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      runAppMenuCommand(item);
    });
  }
  button.addEventListener('keydown', event => handleAppMenuCommandKeydown(event, button, item, options));
  return button;
}

function runAppMenuCommand(item) {
  if (item.disabled || typeof item.action !== 'function') return;
  closeAppMenus();
  try {
    Promise.resolve(item.action()).catch(error => {
      statusEl.innerHTML = `<span class="err">menu command failed: ${esc(error)}</span>`;
    });
  } catch (error) {
    statusEl.innerHTML = `<span class="err">menu command failed: ${esc(error)}</span>`;
  }
}

function appMenuCommands(container) {
  const scope = container.classList?.contains('app-menu')
    ? container.querySelector(':scope > .app-menu-popover')
    : container;
  if (!scope) return [];
  return Array.from(scope.querySelectorAll('.app-menu-command'))
    .filter(button => !button.disabled && button.closest('.app-menu-popover, .app-submenu-popover') === scope);
}

function focusFirstAppMenuCommand(container) {
  appMenuCommands(container)[0]?.focus();
}

function focusAdjacentAppMenuCommand(button, direction) {
  const popover = button.closest('.app-menu-popover, .app-submenu-popover');
  if (!popover) return;
  const commands = appMenuCommands(popover);
  const index = commands.indexOf(button);
  if (!commands.length || index < 0) return;
  const next = commands[(index + direction + commands.length) % commands.length];
  next.focus();
}

function focusAdjacentTopMenu(wrapper, direction) {
  const menus = Array.from(sessionButtons.querySelectorAll('.app-menu'));
  const index = menus.indexOf(wrapper);
  if (index < 0 || !menus.length) return;
  const next = menus[(index + direction + menus.length) % menus.length];
  openAppMenu(next, {focusFirst: false});
  next.querySelector('.app-menu-button')?.focus();
}

function handleAppMenuButtonKeydown(event, wrapper) {
  if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    openAppMenu(wrapper, {focusFirst: true, pinned: true});
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    focusAdjacentTopMenu(wrapper, 1);
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    focusAdjacentTopMenu(wrapper, -1);
  } else if (event.key === 'Escape') {
    closeAppMenus();
  }
}

function handleAppMenuCommandKeydown(event, button, item, options = {}) {
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    focusAdjacentAppMenuCommand(button, 1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    focusAdjacentAppMenuCommand(button, -1);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    closeAppMenus();
    button.closest('.app-menu')?.querySelector('.app-menu-button')?.focus();
  } else if (!options.asSubmenu && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    runAppMenuCommand(item);
  }
}

function queueAppMenuHoverOpen(wrapper) {
  appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  appMenuCloseTimer = clearTimer(appMenuCloseTimer);
  if (!wrapper || wrapper.classList.contains('open')) return;
  const menuId = wrapper.dataset.appMenu || '';
  const currentWrapper = () => document.querySelector(`.app-menu[data-app-menu="${cssEscape(menuId)}"]`);
  if (appMenuIsOpen()) {
    openAppMenu(currentWrapper() || wrapper, {focusFirst: false, pinned: false});
    return;
  }
  appMenuHoverTimer = setTimeout(() => {
    appMenuHoverTimer = null;
    const target = currentWrapper() || wrapper;
    if (!target.classList.contains('open')) openAppMenu(target, {focusFirst: false, pinned: false});
  }, menuHoverOpenDelayMs);
}

function queueAppMenuHoverClose(wrapper) {
  appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  appMenuCloseTimer = clearTimer(appMenuCloseTimer);
  appMenuCloseTimer = setTimeout(() => {
    appMenuCloseTimer = null;
    if (!wrapper?.matches?.(':hover')) closeAppMenus();
  }, menuHoverCloseDelayMs);
}

function openAppMenu(wrapper, options = {}) {
  if (!wrapper) return;
  appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  appMenuCloseTimer = clearTimer(appMenuCloseTimer);
  closeContextMenus();
  closeOtherSessionPopovers(null);
  closeAppMenus(wrapper);
  fitAppMenuPopover(wrapper.querySelector(':scope > .app-menu-popover'));
  wrapper.querySelectorAll(':scope > .app-menu-popover .app-submenu-popover').forEach(fitAppMenuPopover);
  wrapper.classList.add('open');
  wrapper.querySelectorAll('.app-menu-submenu-wrap').forEach(submenu => submenu.classList.add('open'));
  openAppMenuId = wrapper.dataset.appMenu || null;
  openAppMenuPinned = options.pinned === true;
  openAppMenuOpenedAt = Date.now();
  wrapper.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'true');
  if (options.focusFirst) requestAnimationFrame(() => focusFirstAppMenuCommand(wrapper));
}

function closeAppMenus(keepOpen = null) {
  appMenuHoverTimer = clearTimer(appMenuHoverTimer);
  appMenuCloseTimer = clearTimer(appMenuCloseTimer);
  for (const menu of document.querySelectorAll('.app-menu.open')) {
    if (menu === keepOpen) continue;
    menu.classList.remove('open');
    menu.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'false');
  }
  for (const submenu of document.querySelectorAll('.app-menu-submenu-wrap.open')) {
    if (keepOpen?.contains(submenu)) continue;
    submenu.classList.remove('open');
  }
  openAppMenuId = keepOpen?.dataset?.appMenu || null;
  if (!openAppMenuId) {
    openAppMenuPinned = false;
    openAppMenuOpenedAt = 0;
  }
}

function toggleFileExplorer() {
  if (!fileExplorer) return;
  const opening = fileExplorer.hasAttribute('hidden');
  if (opening) {
    fileExplorer.removeAttribute('hidden');
    document.body.classList.add('file-explorer-open');
    openFileExplorerAt(fileExplorerRootForOpen());
  } else {
    fileExplorer.setAttribute('hidden', '');
    document.body.classList.remove('file-explorer-open');
  }
}

async function openFileExplorerAt(path, options = {}) {
  const root = normalizeDirectoryPath(expandUserPath(path));
  const entries = await fetchDirectory(root);
  if (!entries) {
    setFileExplorerPathDisplay(root, {error: fileExplorerPathError || `Cannot open ${root}`});
    return false;
  }
  const previousExpanded = options.preserveExpanded ? Array.from(fileExplorerExpanded) : [];
  const scrollPositions = options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  fileExplorerRoot = root;
  setFileExplorerPathDisplay(fileExplorerRoot);
  renderFileExplorerRootModeControls();
  fileExplorerExpanded.clear();
  if (fileExplorerTree) {
    fileExplorerTree.replaceChildren();
    renderTreeChildren(fileExplorerTree, fileExplorerRoot, entries, 0);
  }
  if (options.refreshPanels !== false) {
    await refreshFileExplorerPanelTrees({root: fileExplorerRoot, entries, restoreState: false});
  }
  if (previousExpanded.length) await restoreFileExplorerExpandedPaths(previousExpanded, fileExplorerRoot);
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
  updateFileExplorerCurrentFileHighlight();
  return true;
}

async function fetchDirectory(path, options = {}) {
  const root = normalizeDirectoryPath(path);
  try {
    const response = await apiFetch(`/api/fs/list?path=${encodeURIComponent(root)}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      fileExplorerPathError = payload.error || `Cannot open ${root} (${response.status})`;
      console.warn('fs list failed', root, response.status, fileExplorerPathError);
      return null;
    }
    const payload = await response.json();
    const entries = payload.entries || [];
    fileExplorerPathError = '';
    if (options.recordSignature !== false) recordDirectorySignature(root, entries);
    return entries;
  } catch (err) {
    fileExplorerPathError = `Cannot open ${root}: ${err}`;
    console.warn('fs list error', root, err);
    return null;
  }
}

function expandUserPath(path) {
  const text = String(path || '').trim();
  if (text === '~') return homePath || text;
  if (text.startsWith('~/')) return homePath ? `${homePath}${text.slice(1)}` : text;
  return text;
}

function directoryEntriesSignature(entries) {
  if (!Array.isArray(entries)) return '';
  return entries
    .map(entry => [
      entry?.name || '',
      entry?.kind || '',
      Number.isFinite(Number(entry?.size)) ? Number(entry.size) : '',
      Number.isFinite(Number(entry?.mtime)) ? Number(entry.mtime) : '',
      entry?.is_symlink ? '1' : '0',
    ].join('\x1f'))
    .sort()
    .join('\x1e');
}

function recordDirectorySignature(path, entries) {
  fileExplorerDirectorySignatures.set(normalizeDirectoryPath(path), directoryEntriesSignature(entries));
}

function normalizeDirectoryPath(path) {
  const text = String(path || '').replace(/\/+$/, '');
  return text || '/';
}

function currentFileExplorerRoot() {
  return normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
}

function fileExplorerIsOpen() {
  return fileExplorerPaneIsOpen() || (fileExplorer && !fileExplorer.hasAttribute('hidden'));
}

function fileExplorerRootModeButtons() {
  return [
    fileExplorerRootModeButton,
    ...Array.from(document.querySelectorAll('.file-explorer-root-mode-toggle-panel')),
  ].filter(Boolean);
}

function renderFileExplorerRootModeControls() {
  const sync = fileExplorerRootMode === 'sync';
  const label = sync ? 'Sync' : 'Root';
  const title = sync ? 'Root mode: sync to focused tmux session' : 'Root mode: fixed';
  for (const button of fileExplorerRootModeButtons()) {
    button.textContent = label;
    button.title = title;
    button.setAttribute('aria-pressed', sync ? 'true' : 'false');
    button.classList.toggle('active', sync);
  }
}

function setFileExplorerRootMode(mode, options = {}) {
  fileExplorerRootMode = mode === 'sync' ? 'sync' : 'fixed';
  writeStoredFileExplorerRootMode(fileExplorerRootMode);
  renderFileExplorerRootModeControls();
  if (fileExplorerRootMode === 'sync' && options.sync !== false) scheduleFileExplorerActiveTabSync();
}

function fileExplorerRootModeValue() {
  return fileExplorerRootMode;
}

function toggleFileExplorerRootMode() {
  setFileExplorerRootMode(fileExplorerRootMode === 'sync' ? 'fixed' : 'sync');
}

function tmuxDirectoryForItem(item) {
  if (!isTmuxSession(item)) return '';
  const path = terminalCurrentPath(item);
  return path ? normalizeDirectoryPath(path) : '';
}

function activeTmuxDirectoryPath(preferredItem = null) {
  if (preferredItem && !isTmuxSession(preferredItem)) return '';
  for (const item of finderCandidateItems(preferredItem)) {
    const path = tmuxDirectoryForItem(item);
    if (path) return path;
  }
  return '';
}

function fileExplorerRootForOpen(preferredItem = null) {
  if (fileExplorerRootMode === 'sync') {
    const tmuxPath = activeTmuxDirectoryPath(preferredItem);
    if (tmuxPath) return tmuxPath;
  }
  return fileExplorerRoot || homePath || '/';
}

function fileExplorerPathInputs() {
  return [
    fileExplorerPath,
    ...Array.from(document.querySelectorAll('.file-explorer-path-inline')),
  ].filter(Boolean);
}

function setFileExplorerPathElementValue(node, path) {
  if (!node) return;
  if ('value' in node) node.value = path;
  else node.textContent = path;
}

function setFileExplorerPathError(node, message = '') {
  if (!node) return;
  node.classList.toggle('invalid', Boolean(message));
  if (message) node.title = message;
  else node.removeAttribute('title');
}

function setFileExplorerPathDisplay(path = currentFileExplorerRoot(), options = {}) {
  const normalized = normalizeDirectoryPath(path);
  const error = options.error || '';
  for (const input of fileExplorerPathInputs()) {
    setFileExplorerPathElementValue(input, normalized);
    setFileExplorerPathError(input, error);
  }
}

async function commitFileExplorerPathInput(input) {
  const raw = 'value' in input ? input.value : input.textContent;
  const target = expandUserPath(raw);
  if (!target) return false;
  const opened = await openFileExplorerAt(target);
  if (!opened) setFileExplorerPathError(input, fileExplorerPathError || `Cannot open ${target}`);
  return opened;
}

function bindFileExplorerPathInput(input) {
  if (!input || input.dataset.pathInputBound === 'true') return;
  input.dataset.pathInputBound = 'true';
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      commitFileExplorerPathInput(input);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerPathDisplay();
      input.blur?.();
    }
  });
  input.addEventListener('focus', () => input.select?.());
}

function pathIsInsideDirectory(path, root) {
  const child = normalizeDirectoryPath(path);
  const parent = normalizeDirectoryPath(root);
  if (parent === '/') return child.startsWith('/');
  return child === parent || child.startsWith(`${parent}/`);
}

function childPathParts(root, path) {
  const parent = normalizeDirectoryPath(root);
  const child = normalizeDirectoryPath(path);
  if (!pathIsInsideDirectory(child, parent) || child === parent) return [];
  const relative = parent === '/' ? child.slice(1) : child.slice(parent.length + 1);
  return relative.split('/').filter(Boolean);
}

function finderDirectoryForItem(item) {
  if (isFileEditorItem(item)) return normalizeDirectoryPath(dirnameOf(fileItemPath(item)));
  return '';
}

function finderTargetPathForItem(item) {
  if (isFileEditorItem(item)) return fileItemPath(item) || '';
  return '';
}

function finderCandidateItems(preferredItem = null) {
  const candidates = [];
  for (const item of [preferredItem, focusedPanelItem, focusedTerminal, lastFocusedTmuxSession, currentActiveMenuItem(), ...activePaneItems()]) {
    if (item && !candidates.includes(item)) candidates.push(item);
  }
  return candidates.filter(item => !isFileExplorerItem(item) && !isInfoItem(item));
}

function firstFinderPath(preferredItem, pathForItem, options = {}) {
  const normalize = options.normalize === true;
  for (const item of finderCandidateItems(preferredItem)) {
    const path = pathForItem(item);
    if (path) return normalize ? normalizeDirectoryPath(path) : path;
  }
  return '';
}

function activeFinderDirectoryPath(preferredItem = null) {
  return firstFinderPath(preferredItem, finderDirectoryForItem);
}

function activeFinderTargetPath(preferredItem = null) {
  return firstFinderPath(preferredItem, finderTargetPathForItem, {normalize: true});
}

function fileExplorerPaneIsOpen() {
  return itemInLayout(fileExplorerItemId);
}

function scheduleFileExplorerActiveTabSync(preferredItem = null) {
  if (!fileExplorerIsOpen()) return;
  if ((preferredItem === null || isTmuxSession(preferredItem)) && fileExplorerRootMode === 'sync') {
    const syncRoot = activeTmuxDirectoryPath(preferredItem);
    if (syncRoot && syncRoot !== currentFileExplorerRoot() && fileExplorerSyncPathInFlight !== syncRoot) {
      requestAnimationFrame(() => {
        syncFileExplorerRootToActiveTmux(preferredItem).catch(error => {
          console.warn('Finder root sync failed', error);
        });
      });
      return;
    }
  }
  const path = activeFinderTargetPath(preferredItem);
  if (!path || fileExplorerSyncPathInFlight === path) return;
  requestAnimationFrame(() => {
    syncFileExplorerToActiveTab(preferredItem).catch(error => {
      console.warn('Finder sync failed', error);
    });
  });
}

async function syncFileExplorerRootToActiveTmux(preferredItem = null) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  const root = activeTmuxDirectoryPath(preferredItem);
  if (!root || root === currentFileExplorerRoot()) return false;
  fileExplorerSyncPathInFlight = root;
  try {
    return await openFileExplorerAt(root, {preserveExpanded: false, preserveScroll: false});
  } finally {
    if (fileExplorerSyncPathInFlight === root) fileExplorerSyncPathInFlight = '';
  }
}

async function syncFileExplorerToActiveTab(preferredItem = null) {
  if (!fileExplorerIsOpen()) return false;
  const path = activeFinderTargetPath(preferredItem);
  if (!path || fileExplorerSyncPathInFlight === path) return false;
  const root = currentFileExplorerRoot();
  if (!pathIsInsideDirectory(path, root) || path === root) return false;
  fileExplorerSyncPathInFlight = path;
  const generation = ++fileExplorerSyncGeneration;
  try {
    return await expandFileExplorerTreesToPath(path, root, generation);
  } finally {
    if (fileExplorerSyncPathInFlight === path) fileExplorerSyncPathInFlight = '';
  }
}

function fileExplorerTreeContainers() {
  const containers = [];
  if (fileExplorerTree) containers.push(fileExplorerTree);
  document.querySelectorAll('.file-explorer-tree-panel').forEach(tree => {
    if (!containers.includes(tree)) containers.push(tree);
  });
  return containers;
}

function captureFileExplorerScrollPositions() {
  return fileExplorerTreeContainers().map(container => container.scrollTop || 0);
}

function restoreFileExplorerScrollPositions(positions) {
  requestAnimationFrame(() => {
    fileExplorerTreeContainers().forEach((container, index) => {
      if (positions[index] != null) container.scrollTop = positions[index];
    });
  });
}

function directFileTreeRow(container, fullPath) {
  return Array.from(container?.children || []).find(node => (
    node.classList?.contains('file-tree-row') && node.dataset?.path === fullPath
  )) || null;
}

function childContainerForRow(row, fullPath) {
  const next = row?.nextElementSibling;
  return next?.classList?.contains('file-tree-children') && next.dataset?.parent === fullPath ? next : null;
}

async function ensureFileTreeRootRendered(container, root) {
  if (!container) return false;
  const firstRow = container.querySelector?.('.file-tree-row[data-path]');
  if (firstRow) {
    if (pathIsInsideDirectory(firstRow.dataset.path, root) && childPathParts(root, firstRow.dataset.path).length === 1) return true;
    container.replaceChildren();
  }
  const entries = await fetchDirectory(root);
  if (!entries) return false;
  container.replaceChildren();
  renderTreeChildren(container, root, entries, 0);
  return true;
}

function childPath(parent, name) {
  return parent === '/' ? `/${name}` : `${parent}/${name}`;
}

async function ensureDirectoryRowExpanded(row, fullPath) {
  if (!row || row.dataset?.kind !== 'dir') return null;
  const existing = childContainerForRow(row, fullPath);
  if (existing) return existing;
  await expandDirectoryRow(row, fullPath);
  return childContainerForRow(row, fullPath);
}

function nextAnimationFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function scrollFileTreeRowIntoView(container, row) {
  if (!container || !row || container.isConnected === false || row.isConnected === false) return false;
  const containerRect = container.getBoundingClientRect?.();
  const rowRect = row.getBoundingClientRect?.();
  const containerHeight = container.clientHeight || containerRect?.height || 0;
  if (!containerRect || !rowRect || !containerHeight || !rowRect.height) return false;
  const currentTop = container.scrollTop || 0;
  const rowTop = currentTop + rowRect.top - containerRect.top;
  const rowBottom = rowTop + rowRect.height;
  const margin = 8;
  const visibleTop = currentTop + margin;
  const visibleBottom = currentTop + containerHeight - margin;
  if (rowTop >= visibleTop && rowBottom <= visibleBottom) return true;
  const centeredTop = rowTop - Math.max(0, (containerHeight - rowRect.height) / 2);
  container.scrollTop = Math.max(0, centeredTop);
  return true;
}

async function expandFileTreeContainerToPath(container, root, path, generation = fileExplorerSyncGeneration, options = {}) {
  const parts = childPathParts(root, path);
  if (!parts.length) return false;
  const rendered = await ensureFileTreeRootRendered(container, root);
  if (!rendered) return false;
  if (generation !== fileExplorerSyncGeneration) return false;
  let scope = container;
  let parent = root;
  let row = null;
  for (const part of parts) {
    const fullPath = childPath(parent, part);
    row = directFileTreeRow(scope, fullPath);
    if (!row) return false;
    if (row.dataset?.kind === 'dir') {
      const childScope = await ensureDirectoryRowExpanded(row, fullPath);
      if (generation !== fileExplorerSyncGeneration) return false;
      if (fullPath !== path) {
        if (!childScope) return false;
        scope = childScope;
      }
    } else if (fullPath !== path) {
      return false;
    }
    parent = fullPath;
  }
  await nextAnimationFrame();
  if (generation !== fileExplorerSyncGeneration) return false;
  if (options.scrollIntoView !== false) scrollFileTreeRowIntoView(container, row);
  updateFileExplorerCurrentFileHighlight();
  return Boolean(row);
}

async function expandFileExplorerTreesToPath(path, root = currentFileExplorerRoot(), generation = fileExplorerSyncGeneration, options = {}) {
  let expanded = false;
  if (!fileExplorerRoot) fileExplorerRoot = root;
  setFileExplorerPathDisplay(root);
  for (const container of fileExplorerTreeContainers()) {
    if (generation !== fileExplorerSyncGeneration) return false;
    expanded = await expandFileTreeContainerToPath(container, root, path, generation, options) || expanded;
  }
  return expanded;
}

async function restoreFileExplorerExpandedPaths(paths, root = currentFileExplorerRoot()) {
  const expandedPaths = Array.from(new Set(paths))
    .filter(path => pathIsInsideDirectory(path, root) && path !== root)
    .sort((left, right) => childPathParts(root, left).length - childPathParts(root, right).length);
  const generation = ++fileExplorerSyncGeneration;
  for (const path of expandedPaths) {
    if (generation !== fileExplorerSyncGeneration) return false;
    await expandFileExplorerTreesToPath(path, root, generation, {scrollIntoView: false});
  }
  return true;
}

function renderTreeChildren(container, parentPath, entries, depth) {
  if (!container) return;
  const visible = entries.filter(e => fileExplorerShowHidden || !e.name.startsWith('.'));
  const currentDirectory = activeFinderDirectoryPath();
  for (const entry of visible) {
    const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
    const row = document.createElement('div');
    row.className = `file-tree-row kind-${entry.kind}`;
    row.dataset.path = fullPath;
    row.dataset.kind = entry.kind;
    row.style.paddingLeft = `${8 + depth * 14}px`;
    row.setAttribute('role', 'treeitem');
    row.setAttribute('aria-selected', fileExplorerSelectedPaths.has(fullPath) ? 'true' : 'false');
    row.draggable = entry.kind === 'file' || entry.kind === 'dir';
    row.classList.toggle('selected', fileExplorerSelectedPaths.has(fullPath));
    row.classList.toggle('is-repo', entry.kind === 'dir' && entry.is_repo === true);
    const currentFile = entry.kind === 'file' && fullPath === activeFile;
    const currentDirectoryRow = entry.kind === 'dir' && fullPath === currentDirectory;
    row.classList.toggle('current-file', currentFile);
    row.classList.toggle('current-directory', currentDirectoryRow);
    if (currentFile || currentDirectoryRow) row.setAttribute('aria-current', 'true');
    const icon = entry.kind === 'dir' ? '▸' : (entry.kind === 'file' ? fileIconFor(entry.name) : '·');
    row.innerHTML = `<span class="file-tree-icon">${icon}</span><span class="file-tree-name">${esc(entry.name)}</span>`;
    if (entry.kind === 'file' && IMAGE_EXTENSIONS.has(fileExtensionOf(entry.name)) && Number(entry.size || 0) <= MAX_FILE_PREVIEW_BYTES) {
      bindFileImagePreview(row.querySelector('.file-tree-icon'), fullPath, entry);
    }
    row.addEventListener('click', event => {
      event.stopPropagation();
      if (event.detail > 1) return;
      onFileTreeRowClick(row, fullPath, entry, event);
    });
    row.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      beginFileTreeRename(row, fullPath, entry);
    });
    row.addEventListener('contextmenu', event => {
      event.preventDefault();
      event.stopPropagation();
      showFileTreeContextMenu(row, fullPath, entry, event.clientX, event.clientY);
    });
    row.addEventListener('dragstart', event => startFileTreeDrag(event, row, fullPath, entry));
    container.appendChild(row);
  }
}

function rawFileUrl(path, params = {}) {
  const queryParts = [`path=${encodeURIComponent(path)}`];
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return `/api/fs/raw?${queryParts.join('&')}`;
}

function closeFileImagePreview() {
  fileImagePreviewShowTimer = clearTimer(fileImagePreviewShowTimer);
  fileImagePreviewHideTimer = clearTimer(fileImagePreviewHideTimer);
  fileImagePreviewPopover?.remove();
  fileImagePreviewPopover = null;
}

function positionFileImagePreview(anchor, popover) {
  const anchorRect = anchor.getBoundingClientRect();
  const rect = popover.getBoundingClientRect();
  const edgeGap = popoverEdgeGapPx();
  const desiredLeft = anchorRect.right + 10;
  const desiredTop = anchorRect.top - 8;
  const left = Math.min(Math.max(edgeGap, desiredLeft), Math.max(edgeGap, window.innerWidth - rect.width - edgeGap));
  const top = Math.min(Math.max(edgeGap, desiredTop), Math.max(edgeGap, window.innerHeight - rect.height - edgeGap));
  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function openFileImagePreview(anchor, path, entry) {
  closeFileImagePreview();
  if (!anchor || !document.body) return;
  const popover = document.createElement('div');
  popover.className = 'file-image-preview-popover';
  const img = document.createElement('img');
  img.src = rawFileUrl(path);
  img.alt = entry?.name || basenameOf(path);
  popover.appendChild(img);
  popover.addEventListener('pointerenter', () => {
    fileImagePreviewHideTimer = clearTimer(fileImagePreviewHideTimer);
  });
  popover.addEventListener('pointerleave', closeFileImagePreviewSoon);
  document.body.appendChild(popover);
  fileImagePreviewPopover = popover;
  positionFileImagePreview(anchor, popover);
}

function closeFileImagePreviewSoon() {
  fileImagePreviewShowTimer = clearTimer(fileImagePreviewShowTimer);
  fileImagePreviewHideTimer = clearTimer(fileImagePreviewHideTimer);
  fileImagePreviewHideTimer = setTimeout(closeFileImagePreview, popoverHideDelayMs);
}

function bindFileImagePreview(icon, path, entry) {
  if (!icon || icon.dataset.imagePreviewBound === 'true') return;
  icon.dataset.imagePreviewBound = 'true';
  icon.addEventListener('pointerenter', () => {
    fileImagePreviewHideTimer = clearTimer(fileImagePreviewHideTimer);
    fileImagePreviewShowTimer = clearTimer(fileImagePreviewShowTimer);
    fileImagePreviewShowTimer = setTimeout(() => {
      fileImagePreviewShowTimer = null;
      openFileImagePreview(icon, path, entry);
    }, popoverShowDelayMs);
  });
  icon.addEventListener('pointerleave', closeFileImagePreviewSoon);
}

function selectableFileTreeRows(container = document) {
  return Array.from(container.querySelectorAll('.file-tree-row[data-path]'))
    .filter(row => row.dataset.kind === 'file' || row.dataset.kind === 'dir');
}

function updateFileExplorerCurrentFileHighlight() {
  const currentDirectory = activeFinderDirectoryPath();
  document.querySelectorAll('.file-tree-row').forEach(row => {
    const selected = fileExplorerSelectedPaths.has(row.dataset.path);
    const currentFile = row.dataset.kind === 'file' && row.dataset.path === activeFile;
    const currentDirectoryRow = !currentFile && row.dataset.kind === 'dir' && row.dataset.path === currentDirectory;
    row.classList.toggle('selected', selected);
    row.classList.toggle('current-file', currentFile);
    row.classList.toggle('current-directory', currentDirectoryRow);
    row.setAttribute('aria-selected', selected ? 'true' : 'false');
    if (currentFile || currentDirectoryRow) row.setAttribute('aria-current', 'true');
    else row.removeAttribute('aria-current');
  });
}

function selectFileTreePath(fullPath, options = {}) {
  if (options.clear !== false) fileExplorerSelectedPaths.clear();
  if (options.toggle) {
    if (fileExplorerSelectedPaths.has(fullPath)) fileExplorerSelectedPaths.delete(fullPath);
    else fileExplorerSelectedPaths.add(fullPath);
  } else {
    fileExplorerSelectedPaths.add(fullPath);
  }
  if (options.anchor !== false) fileExplorerSelectionAnchor = fullPath;
  updateFileExplorerCurrentFileHighlight();
}

function selectFileTreeRange(row, fullPath, options = {}) {
  const rows = selectableFileTreeRows(row.closest('[role="tree"]') || document);
  const targetIndex = rows.findIndex(item => item.dataset.path === fullPath);
  const anchorIndex = rows.findIndex(item => item.dataset.path === fileExplorerSelectionAnchor);
  if (options.clear !== false) fileExplorerSelectedPaths.clear();
  if (targetIndex < 0) {
    selectFileTreePath(fullPath, {clear: false});
    return;
  }
  if (anchorIndex < 0) {
    fileExplorerSelectedPaths.add(fullPath);
    fileExplorerSelectionAnchor = fullPath;
    updateFileExplorerCurrentFileHighlight();
    return;
  }
  const start = Math.min(anchorIndex, targetIndex);
  const end = Math.max(anchorIndex, targetIndex);
  for (const selectedRow of rows.slice(start, end + 1)) {
    fileExplorerSelectedPaths.add(selectedRow.dataset.path);
  }
  updateFileExplorerCurrentFileHighlight();
}

function updateFileTreeSelectionFromClick(row, fullPath, event) {
  if (event.shiftKey) {
    selectFileTreeRange(row, fullPath, {clear: !(event.metaKey || event.ctrlKey)});
    return true;
  }
  if (event.metaKey || event.ctrlKey) {
    selectFileTreePath(fullPath, {clear: false, toggle: true});
    return true;
  }
  selectFileTreePath(fullPath);
  return false;
}

function fileTreeActionPaths(fullPath) {
  if (fileExplorerSelectedPaths.has(fullPath)) return Array.from(fileExplorerSelectedPaths);
  return [fullPath];
}

function compactNestedPaths(paths) {
  const sorted = Array.from(new Set(paths)).sort((left, right) => left.localeCompare(right));
  return sorted.filter((path, index) => !sorted.some((other, otherIndex) => otherIndex !== index && path.startsWith(`${other}/`)));
}

async function onFileTreeRowClick(row, fullPath, entry, event) {
  const selectionOnly = updateFileTreeSelectionFromClick(row, fullPath, event);
  if (selectionOnly) return;
  if (entry.kind === 'dir') {
    if (fileExplorerExpanded.has(fullPath)) {
      collapseDirectoryRow(row, fullPath);
    } else {
      await expandDirectoryRow(row, fullPath);
    }
    return;
  }
  if (entry.kind === 'file') {
    activeFile = fullPath;
    updateFileExplorerCurrentFileHighlight();
    await openFileInEditor(fullPath, entry);
  }
}

function startFileTreeDrag(event, row, fullPath, entry) {
  if (!fileExplorerSelectedPaths.has(fullPath)) selectFileTreePath(fullPath);
  const paths = fileTreeActionPaths(fullPath);
  const payload = JSON.stringify({path: fullPath, paths, kind: entry.kind, name: entry.name});
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('application/x-yolomux-file', payload);
  event.dataTransfer.setData('text/plain', paths.join('\n'));
  startFileDragPreview(event, paths, entry);
}

async function fetchFilePathInfo(path) {
  const response = await apiFetch(`/api/fs/info?path=${encodeURIComponent(path)}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText || response.status);
  return payload;
}

async function showFileTreeContextMenu(row, fullPath, entry, x, y) {
  closeFileContextMenu();
  closeTerminalContextMenu();
  closeSessionContextMenu();
  if (!fileExplorerSelectedPaths.has(fullPath)) selectFileTreePath(fullPath);
  const selectedPaths = fileTreeActionPaths(fullPath);
  const infos = await Promise.all(selectedPaths.map(path => fetchFilePathInfo(path).catch(error => {
    console.warn('fs info failed', path, error);
    return null;
  })));
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu file-context-menu';
  menu.setAttribute('role', 'menu');
  const relativePaths = infos.map(info => info?.relative_path || '').filter(Boolean);
  const menuState = fileContextMenuState(entry, selectedPaths, relativePaths);
  const multiple = selectedPaths.length > 1;
  appendContextMenuButton(menu, multiple ? 'Copy full paths' : 'Copy full path', () => copyFilePath(selectedPaths.join('\n'), 'full'), closeFileContextMenu);
  appendContextMenuButton(menu, multiple ? 'Copy raw paths' : 'Copy raw path', () => copyFilePath(selectedPaths.join('\n'), 'full', {raw: true}), closeFileContextMenu);
  appendContextMenuButton(menu, multiple ? 'Copy relative paths' : 'Copy relative path', () => copyFilePath(relativePaths.join('\n'), 'relative'), closeFileContextMenu, {disabled: menuState.copyRelativeDisabled});
  appendContextMenuButton(menu, 'Download', () => triggerFileDownload(fullPath), closeFileContextMenu, {disabled: menuState.downloadDisabled});
  appendContextMenuButton(menu, 'Rename', () => beginFileTreeRename(row, selectedPaths[0], entry), closeFileContextMenu, {disabled: menuState.renameDisabled});
  appendContextMenuButton(menu, multiple ? 'Delete selected' : 'Delete', () => deleteFileTreePath(fullPath, entry, selectedPaths), closeFileContextMenu, {disabled: menuState.deleteDisabled});
  fileContextMenu.open(menu, x, y);
}

function fileContextMenuState(entry, selectedPaths, relativePaths) {
  const multiple = selectedPaths.length > 1;
  return {
    copyRelativeDisabled: relativePaths.length !== selectedPaths.length,
    downloadDisabled: multiple || entry?.kind !== 'file',
    renameDisabled: readOnlyMode || multiple,
    deleteDisabled: readOnlyMode,
  };
}

function shellQuotePathText(pathText) {
  return String(pathText || '')
    .split('\n')
    .filter(path => path.length > 0)
    .map(shellQuote)
    .join('\n');
}

async function copyFilePath(path, label, options = {}) {
  const text = options.raw === true ? path : shellQuotePathText(path);
  try {
    await copyTextToClipboard(text);
    const prefix = options.raw === true ? 'raw ' : '';
    statusEl.textContent = label === 'relative' ? `copied ${prefix}relative path` : `copied ${prefix}full path`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">copy failed: ${esc(error)}</span>`;
  }
}

function rawFileDownloadUrl(path) {
  return rawFileUrl(path, {download: 1});
}

function triggerFileDownload(path) {
  if (!path) return;
  const link = document.createElement('a');
  link.href = rawFileDownloadUrl(path);
  link.download = basenameOf(path) || 'download';
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  statusEl.textContent = `download started: ${basenameOf(path) || path}`;
}

function copyCurrentFileExplorerPath() {
  copyFilePath(fileExplorerRoot || homePath || '/', 'full');
}

async function deleteFileTreePath(fullPath, entry, paths = null) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot delete files</span>';
    return;
  }
  const deletePaths = compactNestedPaths(paths || fileTreeActionPaths(fullPath));
  const kind = entry.kind === 'dir' ? 'directory and all contents' : 'file';
  const confirmText = deletePaths.length === 1
    ? `Delete ${kind}?\n${deletePaths[0]}`
    : `Delete ${deletePaths.length} selected items?\n${deletePaths.join('\n')}`;
  if (!window.confirm(confirmText)) return;
  try {
    for (const path of deletePaths) {
      const response = await apiFetch('/api/fs/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || response.statusText || response.status);
    }
    for (const path of Array.from(openFiles.keys())) {
      if (deletePaths.some(deletedPath => path === deletedPath || path.startsWith(`${deletedPath}/`))) {
        removeOpenFile(path, {confirmDirty: false, render: false});
      }
    }
    for (const path of deletePaths) fileExplorerSelectedPaths.delete(path);
    if (deletePaths.includes(fileExplorerSelectionAnchor)) fileExplorerSelectionAnchor = null;
    statusEl.textContent = deletePaths.length === 1 ? `deleted ${basenameOf(deletePaths[0])}` : `deleted ${deletePaths.length} items`;
    await refreshFileExplorerTrees();
    renderSessionButtons();
    renderPaneTabStrips();
  } catch (error) {
    statusEl.innerHTML = `<span class="err">delete failed: ${esc(error)}</span>`;
  }
}

function beginFileTreeRename(row, fullPath, entry) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot rename files</span>';
    return;
  }
  closeFileContextMenu();
  const targetRow = row || document.querySelector(`.file-tree-row[data-path="${cssEscape(fullPath)}"]`);
  const nameNode = targetRow?.querySelector('.file-tree-name');
  if (!targetRow || !nameNode) return;
  fileTreeRenamePath = fullPath;
  selectFileTreePath(fullPath);
  targetRow.classList.add('renaming');
  targetRow.draggable = false;
  const currentName = entry?.name || basenameOf(fullPath);
  const input = document.createElement('input');
  input.className = 'file-tree-rename-input';
  input.value = currentName;
  input.setAttribute('aria-label', `Rename ${currentName}`);
  nameNode.replaceChildren(input);
  let finished = false;
  let commitInFlight = false;
  const finish = async commit => {
    if (finished || commitInFlight) return;
    if (!commit) {
      finished = true;
      fileTreeRenamePath = null;
      targetRow.classList.remove('renaming');
      targetRow.draggable = entry.kind === 'file' || entry.kind === 'dir';
      nameNode.textContent = currentName;
      return;
    }
    const nextName = input.value.trim();
    if (!nextName || nextName === currentName) {
      finish(false);
      return;
    }
    commitInFlight = true;
    const renamed = await renameFileTreePath(fullPath, entry, nextName);
    if (renamed) finished = true;
    else {
      commitInFlight = false;
      input.focus();
    }
  };
  input.addEventListener('click', event => event.stopPropagation());
  input.addEventListener('dblclick', event => event.stopPropagation());
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      finish(true);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      finish(false);
    }
  });
  input.addEventListener('blur', () => finish(true));
  setTimeout(() => {
    input.focus();
    const dot = currentName.lastIndexOf('.');
    const selectEnd = dot > 0 ? dot : currentName.length;
    input.setSelectionRange(0, selectEnd);
  }, 0);
}

async function renameFileTreePath(fullPath, entry, newName) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot rename files</span>';
    return false;
  }
  const currentName = entry?.name || basenameOf(fullPath);
  const trimmed = newName.trim();
  if (!trimmed || trimmed === currentName) return false;
  try {
    const response = await apiFetch('/api/fs/rename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: fullPath, new_name: trimmed}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText || response.status);
    const newPath = payload.path;
    if (fileExplorerSelectedPaths.delete(fullPath)) fileExplorerSelectedPaths.add(newPath);
    if (fileExplorerSelectionAnchor === fullPath) fileExplorerSelectionAnchor = newPath;
    if (fileTreeRenamePath === fullPath) fileTreeRenamePath = null;
    for (const path of Array.from(openFiles.keys())) {
      if (path === fullPath) renameOpenFilePath(path, newPath);
      else if (path.startsWith(`${fullPath}/`)) renameOpenFilePath(path, `${newPath}${path.slice(fullPath.length)}`);
    }
    statusEl.textContent = `renamed ${currentName} to ${trimmed}`;
    await refreshFileExplorerTrees();
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">rename failed: ${esc(error)}</span>`;
    return false;
  }
}

async function refreshFileExplorerTrees(options = {}) {
  const refreshOptions = {preserveExpanded: true, preserveScroll: true, ...options};
  if (fileExplorerRoot) await openFileExplorerAt(fileExplorerRoot, refreshOptions);
  else await refreshFileExplorerPanelTrees(refreshOptions);
}

async function refreshFileExplorerPanelTrees(options = {}) {
  const panels = Array.from(document.querySelectorAll('.panel.file-explorer-panel'));
  if (!panels.length) return;
  const shouldRestore = options.restoreState !== false && (options.preserveExpanded || options.preserveScroll);
  const previousExpanded = shouldRestore && options.preserveExpanded ? Array.from(fileExplorerExpanded) : [];
  const scrollPositions = shouldRestore && options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  await Promise.all(panels.map(panel => refreshFileExplorerPanelTree(panel, options)));
  if (previousExpanded.length) await restoreFileExplorerExpandedPaths(previousExpanded, currentFileExplorerRoot());
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
}

function fileExtensionOf(name) {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot).toLowerCase();
}

function fileIconFor(name) {
  const lowerName = String(name || '').toLowerCase();
  const ext = fileExtensionOf(lowerName);
  if (IMAGE_EXTENSIONS.has(ext)) return '🖼';
  if (ext === '.log') return '🪵';
  if (['.md', '.txt', '.rst'].includes(ext)) return '📝';
  if (['.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env'].includes(ext)) return '⚙';
  if (['.sh', '.bash', '.zsh', '.fish'].includes(ext)) return '🐚';
  if (['.py', '.js', '.ts', '.tsx', '.jsx', '.rs', '.go', '.c', '.h', '.cpp', '.hpp', '.rb', '.lua', '.sql'].includes(ext)) return '🧩';
  if (['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz'].includes(ext)) return '🗜';
  if (['dockerfile', 'makefile'].includes(lowerName)) return '⚙';
  if (['.gitignore', 'license', 'readme'].includes(lowerName)) return '📝';
  return '📄';
}

function basenameOf(path) {
  if (!path) return '';
  const idx = path.lastIndexOf('/');
  return idx === -1 ? path : path.slice(idx + 1) || '/';
}

function dirnameOf(path) {
  if (!path || path === '/') return '/';
  const idx = path.lastIndexOf('/');
  if (idx <= 0) return '/';
  return path.slice(0, idx);
}

async function fetchFileEntry(path) {
  const entries = await fetchDirectory(dirnameOf(path));
  if (!Array.isArray(entries)) return null;
  const name = basenameOf(path);
  return entries.find(entry => entry.name === name) || null;
}

function fileEntryChanged(state, entry) {
  if (!state || !entry) return true;
  const stateMtime = Number(state.mtime || 0);
  const entryMtime = Number(entry.mtime || 0);
  if (stateMtime !== entryMtime) return true;
  if (state.size == null || entry.size == null) return false;
  return Number(state.size) !== Number(entry.size);
}

function syncFileLayoutItems() {
  layoutItems = computeLayoutItems();
}

function showFileOpenError(path, message) {
  showToast('File open failed', `${path}\n${message}`, {
    container: displayToastContainer(fileExplorerItemId),
    className: 'attention-alert toast',
  });
}

function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  const decimals = amount >= 10 || index === 0 ? 0 : 1;
  return `${amount.toFixed(decimals)} ${units[index]}`;
}

function tooLargeFileState(size = null, message = '') {
  return {
    mtime: 0,
    kind: 'too-large',
    original: '',
    content: '',
    dirty: false,
    size,
    maxBytes: MAX_FILE_PREVIEW_BYTES,
    error: message,
  };
}

function fileErrorState(message) {
  return {
    mtime: 0,
    kind: 'error',
    original: '',
    content: '',
    dirty: false,
    error: String(message || 'failed to load file'),
  };
}

function clearOpenFileExternalState(state) {
  if (!state) return state;
  delete state.externalChanged;
  delete state.externalMissing;
  return state;
}

function openFileStatus(state) {
  if (!state) return {message: '', level: ''};
  if (state.externalMissing) return {message: 'deleted on disk; unsaved edits kept', level: 'warn'};
  if (state.externalChanged) return {message: 'changed on disk; unsaved edits kept', level: 'warn'};
  if (state.dirty) return {message: 'modified', level: ''};
  if (state.kind === 'text') return {message: `${state.original.length} chars`, level: ''};
  return {message: '', level: ''};
}

function renderOpenFilePath(path) {
  const item = fileEditorItemFor(path);
  const slot = slotForSession(item);
  const panel = panelNodes.get(item);
  if (panel && slot && activeItemForSide(slot) === item) renderFileEditorPanel(panel, item);
  if (activeFile === path) renderEditorForActive();
  renderSessionButtons();
  renderPaneTabStrips();
}

function showFileEditorPaneForPath(path) {
  activeFile = path;
  syncFileLayoutItems();
  updateFileExplorerCurrentFileHighlight();
  return openFileEditorPane(path);
}

function openFilesSetAndShow(path, state) {
  openFiles.set(path, state);
  return showFileEditorPaneForPath(path);
}

async function openFileInEditor(fullPath, entryOrName) {
  const entry = typeof entryOrName === 'object' && entryOrName ? entryOrName : null;
  const name = entry?.name || String(entryOrName || basenameOf(fullPath));
  const ext = fileExtensionOf(name);
  const kind = IMAGE_EXTENSIONS.has(ext) ? 'image' : 'text';
  if (openFiles.has(fullPath)) {
    await showFileEditorPaneForPath(fullPath);
    return;
  }
  if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(entry.size));
    state.mtime = entry?.mtime || 0;
    await openFilesSetAndShow(fullPath, state);
    return;
  }
  if (kind === 'image') {
    await openFilesSetAndShow(fullPath, {mtime: entry?.mtime || 0, kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null});
    return;
  }
  try {
    const response = await apiFetch(`/api/fs/read?path=${encodeURIComponent(fullPath)}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const message = payload.error || response.status;
      const state = response.status === 413 ? tooLargeFileState(entry?.size ?? null, String(message)) : fileErrorState(message);
      await openFilesSetAndShow(fullPath, state);
      return;
    }
    const payload = await response.json();
    await openFilesSetAndShow(fullPath, {
      mtime: payload.mtime,
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    });
  } catch (err) {
    showFileOpenError(fullPath, err);
  }
}

async function openFileStateFromDisk(path, entry = null) {
  const fileEntry = entry || await fetchFileEntry(path);
  if (!fileEntry) return {missing: true};
  if (Number(fileEntry.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(fileEntry.size));
    state.mtime = fileEntry.mtime || 0;
    return {state};
  }
  const ext = fileExtensionOf(fileEntry.name || basenameOf(path));
  if (IMAGE_EXTENSIONS.has(ext)) {
    return {state: {
      mtime: fileEntry.mtime || 0,
      size: fileEntry.size ?? null,
      kind: 'image',
      original: '',
      content: '',
      dirty: false,
    }};
  }
  try {
    const response = await apiFetch(`/api/fs/read?path=${encodeURIComponent(path)}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const message = String(payload.error || response.status);
      const state = response.status === 413 ? tooLargeFileState(fileEntry.size ?? null, message) : fileErrorState(message);
      state.mtime = fileEntry.mtime || 0;
      state.size = fileEntry.size ?? null;
      return {state};
    }
    const payload = await response.json();
    return {state: {
      mtime: payload.mtime,
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    }};
  } catch (error) {
    return {state: fileErrorState(error)};
  }
}

function markOpenFileMissing(path) {
  const state = openFiles.get(path);
  if (!state) return;
  if (state.dirty) {
    state.externalMissing = true;
    delete state.externalChanged;
  } else {
    openFiles.set(path, fileErrorState('file deleted or moved on disk'));
  }
  renderOpenFilePath(path);
}

async function replaceOpenFileStateFromDisk(path, entry = null) {
  const loaded = await openFileStateFromDisk(path, entry);
  if (loaded.missing) {
    markOpenFileMissing(path);
    return false;
  }
  openFiles.set(path, clearOpenFileExternalState(loaded.state));
  renderOpenFilePath(path);
  return true;
}

async function reloadOpenFileFromDisk(path, options = {}) {
  const state = openFiles.get(path);
  if (!state) return false;
  if (state.dirty && options.force !== true) {
    const confirmed = window.confirm(`Reload ${basenameOf(path)} from disk and discard unsaved changes?`);
    if (!confirmed) return false;
  }
  return replaceOpenFileStateFromDisk(path);
}

async function refreshOpenFilesIfChanged() {
  for (const [path, state] of Array.from(openFiles.entries())) {
    if (!state || state.loading) continue;
    const entry = await fetchFileEntry(path);
    if (!entry) {
      if (state.externalMissing) continue;
      markOpenFileMissing(path);
      continue;
    }
    if (!fileEntryChanged(state, entry)) {
      if (state.externalChanged || state.externalMissing) {
        delete state.externalMissing;
        if (!state.dirty) delete state.externalChanged;
        renderOpenFilePath(path);
      }
      continue;
    }
    if (state.dirty) {
      const externalChanged = {mtime: entry.mtime || 0, size: entry.size ?? null};
      if (state.externalChanged
        && state.externalChanged.mtime === externalChanged.mtime
        && state.externalChanged.size === externalChanged.size) {
        continue;
      }
      state.externalChanged = externalChanged;
      delete state.externalMissing;
      renderOpenFilePath(path);
      continue;
    }
    await replaceOpenFileStateFromDisk(path, entry);
  }
}

function watchedFileExplorerDirectories() {
  const root = currentFileExplorerRoot();
  const directories = new Set();
  if (fileExplorerRoot || fileExplorerPaneIsOpen()) directories.add(root);
  for (const path of fileExplorerExpanded) {
    if (pathIsInsideDirectory(path, root)) directories.add(normalizeDirectoryPath(path));
  }
  return Array.from(directories);
}

async function refreshFileExplorerIfChanged() {
  const directories = watchedFileExplorerDirectories();
  let changed = false;
  for (const directory of directories) {
    const entries = await fetchDirectory(directory, {recordSignature: false});
    if (!entries) continue;
    const signature = directoryEntriesSignature(entries);
    const previous = fileExplorerDirectorySignatures.get(normalizeDirectoryPath(directory));
    fileExplorerDirectorySignatures.set(normalizeDirectoryPath(directory), signature);
    if (previous !== undefined && previous !== signature) changed = true;
  }
  if (changed) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
}

async function refreshWatchedFilesystem() {
  if (filesystemRefreshInFlight) return;
  filesystemRefreshInFlight = true;
  try {
    await refreshFileExplorerIfChanged();
    await refreshOpenFilesIfChanged();
  } finally {
    filesystemRefreshInFlight = false;
  }
}

function slotForNewFileEditorTab() {
  const focusedSlot = isFileEditorItem(focusedPanelItem) ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return layoutSlotKeys().find(slot => paneTabs(slot).some(isFileEditorItem)) || null;
}

function layoutColumnNode(slot) {
  return grid?.querySelector(`.layout-column[data-slot="${cssEscape(slot)}"]`) || null;
}

function largestPaneSlot(exclude = new Set()) {
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (exclude.has(slot) || !paneTabs(slot).length) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    if (!best || area > best.area) best = {slot, area};
  }
  return best?.slot || null;
}

function largestNonFileExplorerPaneSlot(exclude = new Set()) {
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (exclude.has(slot) || !paneTabs(slot).length || isFileExplorerItem(activeItemForSide(slot))) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    if (!best || area > best.area) best = {slot, area};
  }
  return best?.slot || null;
}

function largestPaneSlotForFileEditor() {
  const filesSlot = slotForSession(fileExplorerItemId);
  const filesRect = filesSlot ? layoutColumnNode(filesSlot)?.getBoundingClientRect() : null;
  let fallback = null;
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (slot === filesSlot || !paneTabs(slot).length) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    const candidate = {slot, area};
    if (!fallback || area > fallback.area) fallback = candidate;
    if (filesRect && rect && rect.left < filesRect.right - 1) continue;
    if (!best || area > best.area) best = candidate;
  }
  return (best || fallback)?.slot || null;
}

async function openFileEditorPane(path) {
  const item = fileEditorItemFor(path);
  syncFileLayoutItems();
  renderSessionButtons();
  const existingSlot = slotForSession(item);
  if (existingSlot) {
    activatePaneTab(existingSlot, item);
    return;
  }
  const editorSlot = slotForNewFileEditorTab();
  if (editorSlot) {
    await moveSessionToSlot(item, editorSlot, null, paneTabs(editorSlot).length);
    return;
  }
  const largestSlot = largestPaneSlotForFileEditor();
  if (largestSlot) {
    await moveSessionToSlot(item, largestSlot, null, paneTabs(largestSlot).length);
    return;
  }
  const filesSlot = slotForSession(fileExplorerItemId);
  const targetSlot = layoutSlotKeys().find(slot => slot !== filesSlot) || filesSlot;
  if (targetSlot) {
    const zone = targetSlot === filesSlot ? 'right' : 'left';
    const pct = targetSlot === filesSlot ? fileExplorerSplitPercent : defaultSplitPercent;
    await splitSessionAtSlot(item, targetSlot, zone, null, pct);
    return;
  }
  await moveSessionToSlot(item, slotForNewSession(), null);
}

function ensureEditorVisible() {
  if (!fileEditor) return;
  fileEditor.removeAttribute('hidden');
  document.body.classList.add('file-editor-open');
}

function hideEditor() {
  if (!fileEditor) return;
  fileEditor.setAttribute('hidden', '');
  document.body.classList.remove('file-editor-open');
  activeFile = null;
  if (fileEditorTextarea) { fileEditorTextarea.value = ''; fileEditorTextarea.hidden = false; }
  if (fileEditorPreviewPane) { fileEditorPreviewPane.hidden = true; fileEditorPreviewPane.innerHTML = ''; }
  if (fileEditorHighlight) fileEditorHighlight.hidden = true;
  const img = fileEditor.querySelector('.file-editor-image');
  if (img) img.remove();
  setEditorStatus('');
  updateFileExplorerCurrentFileHighlight();
}

function setEditorStatus(msg, level) {
  if (!fileEditorStatus) return;
  fileEditorStatus.textContent = msg || '';
  fileEditorStatus.dataset.level = level || '';
}

function removeOpenFile(path, options = {}) {
  const confirmDirty = options.confirmDirty !== false;
  const shouldRender = options.render !== false;
  if (!path || !openFiles.has(path)) return;
  const state = openFiles.get(path);
  if (confirmDirty && state?.dirty && !window.confirm(`Discard unsaved changes to ${basenameOf(path)}?`)) return false;
  const item = fileEditorItemFor(path);
  const nextSlots = layoutWithoutItem(item, {preserveRemovedSlot: true});
  const wasInLayout = itemInLayout(item);
  const panel = panelNodes.get(item);
  openFiles.delete(path);
  fileEditorPreviewMode.delete(path);
  fileEditorImageMode.delete(path);
  syncFileLayoutItems();
  if (panel) {
    panel.remove();
    panelNodes.delete(item);
  }
  if (activeFile === path) {
    const remaining = Array.from(openFiles.keys());
    activeFile = remaining[remaining.length - 1] || null;
  }
  updateFileExplorerCurrentFileHighlight();
  if (!activeFile) hideEditor();
  if (wasInLayout) applyLayoutSlots(nextSlots);
  if (shouldRender) renderSessionButtons();
  return true;
}

function closeFileTab(path) {
  return removeOpenFile(path);
}

function layoutWithReplacedItem(oldItem, newItem) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (paneIsPlaceholder(side)) {
      next[side] = emptyPlaceholderPaneState();
      continue;
    }
    const tabs = paneTabs(side).map(item => item === oldItem ? newItem : item);
    const active = activeItemForSide(side) === oldItem ? newItem : activeItemForSide(side);
    next[side] = paneStateWithTabs(tabs, active);
  }
  return compactLayoutSlots(next);
}

function renameOpenFilePath(oldPath, newPath) {
  if (!oldPath || !newPath || oldPath === newPath || !openFiles.has(oldPath)) return;
  const oldItem = fileEditorItemFor(oldPath);
  const newItem = fileEditorItemFor(newPath);
  const state = openFiles.get(oldPath);
  const wasInLayout = itemInLayout(oldItem);
  const panel = panelNodes.get(oldItem);
  openFiles.delete(oldPath);
  openFiles.set(newPath, state);
  if (fileEditorPreviewMode.has(oldPath)) {
    fileEditorPreviewMode.set(newPath, fileEditorPreviewMode.get(oldPath));
    fileEditorPreviewMode.delete(oldPath);
  }
  if (fileEditorImageMode.has(oldPath)) {
    fileEditorImageMode.set(newPath, fileEditorImageMode.get(oldPath));
    fileEditorImageMode.delete(oldPath);
  }
  if (panel) {
    panel.remove();
    panelNodes.delete(oldItem);
  }
  if (activeFile === oldPath) activeFile = newPath;
  syncFileLayoutItems();
  if (wasInLayout) applyLayoutSlots(layoutWithReplacedItem(oldItem, newItem), {focusSession: newItem});
  else {
    renderSessionButtons();
    renderPaneTabStrips();
    updateFileExplorerCurrentFileHighlight();
  }
}

function renderEditorForActive() {
  if (!activeFile || !openFiles.has(activeFile)) {
    hideEditor();
    return;
  }
  ensureEditorVisible();
  const state = openFiles.get(activeFile);
  if (fileEditorPath) fileEditorPath.textContent = activeFile;
  const isMarkdown = activeFile.toLowerCase().endsWith('.md') || activeFile.toLowerCase().endsWith('.markdown');
  if (fileEditorPreviewBtn) {
    fileEditorPreviewBtn.hidden = !(isMarkdown && state.kind === 'text');
    fileEditorPreviewBtn.classList.toggle('active', fileEditorPreviewMode.get(activeFile) === true);
    fileEditorPreviewBtn.textContent = fileEditorPreviewMode.get(activeFile) ? 'Edit' : 'Preview';
  }
  if (fileEditorWrapBtn) {
    fileEditorWrapBtn.hidden = state.kind !== 'text';
    updateEditorWrapButton(fileEditorWrapBtn);
  }
  // Clear any image from a previous tab
  const oldImg = fileEditor.querySelector('.file-editor-image');
  if (oldImg) oldImg.remove();
  if (state.kind === 'image') {
    if (fileEditorTextarea) fileEditorTextarea.hidden = true;
    if (fileEditorPreviewPane) fileEditorPreviewPane.hidden = true;
    if (fileEditorHighlight) fileEditorHighlight.hidden = true;
    if (fileEditorSave) fileEditorSave.hidden = true;
    const img = document.createElement('img');
    img.className = 'file-editor-image';
    const version = state.mtime || state.size || 0;
    img.src = rawFileUrl(activeFile, {v: version});
    img.alt = activeFile;
    img.onload = () => setEditorStatus(`${img.naturalWidth}×${img.naturalHeight}`, '');
    img.onerror = () => setEditorStatus('failed to load image', 'error');
    fileEditor.insertBefore(img, fileEditorStatus);
    setEditorStatus('loading…', '');
    return;
  }
  // text mode
  if (fileEditorSave) fileEditorSave.hidden = readOnlyMode;
  const ext = activeFile.slice(activeFile.lastIndexOf('.')).toLowerCase();
  const previewing = fileEditorPreviewMode.get(activeFile) === true;
  if (previewing && isMarkdown) {
    renderMarkdownPreview(state.content);
    if (fileEditorTextarea) fileEditorTextarea.hidden = true;
    if (fileEditorHighlight) fileEditorHighlight.hidden = true;
    if (fileEditorPreviewPane) fileEditorPreviewPane.hidden = false;
  } else {
    if (fileEditorTextarea) {
      fileEditorTextarea.hidden = false;
      fileEditorTextarea.value = state.content;
      fileEditorTextarea.readOnly = readOnlyMode;
      applyEditorWrapToTextarea(fileEditorTextarea);
    }
    if (fileEditorPreviewPane) fileEditorPreviewPane.hidden = true;
    if (fileEditorHighlight) fileEditorHighlight.hidden = true;
  }
  const status = openFileStatus(state);
  setEditorStatus(status.message, status.level);
}

function renderMarkdownPreview(text) {
  if (!fileEditorPreviewPane) return;
  renderMarkdownPreviewInto(fileEditorPreviewPane, text);
}

function editorWrapValue(enabled = fileEditorWrapEnabled) {
  return enabled ? 'soft' : 'off';
}

function applyEditorWrapToTextarea(textarea) {
  if (!textarea) return;
  const value = editorWrapValue();
  textarea.wrap = value;
  textarea.setAttribute('wrap', value);
  textarea.classList.toggle('editor-wrap', fileEditorWrapEnabled);
}

function setFileEditorIcon(button, iconClass) {
  if (!button || button.querySelector(`.${iconClass}`)) return;
  button.innerHTML = `<span class="file-editor-icon ${iconClass}" aria-hidden="true"></span>`;
}

function updateEditorWrapButton(button) {
  if (!button) return;
  button.classList.toggle('active', fileEditorWrapEnabled);
  button.setAttribute('aria-pressed', fileEditorWrapEnabled ? 'true' : 'false');
  const label = fileEditorWrapEnabled ? 'Disable word wrap' : 'Enable word wrap';
  button.title = label;
  button.setAttribute('aria-label', label);
  setFileEditorIcon(button, 'file-editor-icon-wrap');
}

function applyEditorWrapPreference() {
  applyEditorWrapToTextarea(fileEditorTextarea);
  updateEditorWrapButton(fileEditorWrapBtn);
  document.querySelectorAll('.file-editor-panel').forEach(panel => {
    panel.classList.toggle('editor-wrap', fileEditorWrapEnabled);
    applyEditorWrapToTextarea(panel.querySelector('.file-editor-textarea-panel'));
    updateEditorWrapButton(panel.querySelector('.file-editor-wrap-panel'));
    syncSyntaxHighlightScroll(panel);
  });
}

function setEditorWrapEnabled(enabled) {
  fileEditorWrapEnabled = enabled === true;
  writeStoredEditorWrap(fileEditorWrapEnabled);
  applyEditorWrapPreference();
}

function toggleEditorWrap() {
  setEditorWrapEnabled(!fileEditorWrapEnabled);
}

function togglePreview() {
  if (!activeFile) return;
  const wasPreviewing = fileEditorPreviewMode.get(activeFile) === true;
  fileEditorPreviewMode.set(activeFile, !wasPreviewing);
  renderEditorForActive();
}

async function saveCurrentEditor() {
  if (!activeFile || readOnlyMode) return;
  const state = openFiles.get(activeFile);
  if (!state || state.kind !== 'text') return;
  setEditorStatus('saving…', '');
  try {
    const body = JSON.stringify({
      path: activeFile,
      content: fileEditorTextarea.value,
      expected_mtime: state.mtime,
    });
    const response = await apiFetch('/api/fs/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setEditorStatus(`save failed: ${payload.error || response.status}`, 'error');
      return;
    }
    const payload = await response.json();
    state.mtime = payload.mtime;
    state.size = payload.size;
    state.original = fileEditorTextarea.value;
    state.content = fileEditorTextarea.value;
    state.dirty = false;
    clearOpenFileExternalState(state);
    setEditorStatus(`saved (${payload.size} bytes)`, 'ok');
    renderSessionButtons();
  } catch (err) {
    setEditorStatus(`save failed: ${err}`, 'error');
  }
}

function toggleHiddenFiles() {
  fileExplorerShowHidden = !fileExplorerShowHidden;
  try { window.localStorage?.setItem(fileExplorerHiddenStorageKey, fileExplorerShowHidden ? '1' : '0'); }
  catch (_) {}
  if (fileExplorerHiddenToggle) {
    fileExplorerHiddenToggle.setAttribute('aria-pressed', fileExplorerShowHidden ? 'true' : 'false');
    fileExplorerHiddenToggle.classList.toggle('active', fileExplorerShowHidden);
    fileExplorerHiddenToggle.title = fileExplorerShowHidden ? 'Hide dotfiles (.*)' : 'Show hidden files (dotfiles)';
  }
  if (fileExplorerRoot) refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
}

async function expandDirectoryRow(row, fullPath) {
  const entries = await fetchDirectory(fullPath);
  if (!entries) return;
  fileExplorerExpanded.add(fullPath);
  row.classList.add('expanded');
  row.querySelector('.file-tree-icon').textContent = '▾';
  const children = document.createElement('div');
  children.className = 'file-tree-children';
  children.dataset.parent = fullPath;
  const depth = parseInt(row.style.paddingLeft, 10);
  const nextDepth = Math.round((depth - 8) / 14) + 1;
  renderTreeChildren(children, fullPath, entries, nextDepth);
  row.insertAdjacentElement('afterend', children);
}

function collapseDirectoryRow(row, fullPath) {
  fileExplorerExpanded.delete(fullPath);
  row.classList.remove('expanded');
  row.querySelector('.file-tree-icon').textContent = '▸';
  const next = row.nextElementSibling;
  if (next && next.classList.contains('file-tree-children') && next.dataset.parent === fullPath) {
    next.remove();
  }
}

if (fileExplorerClose) fileExplorerClose.addEventListener('click', () => toggleFileExplorer());
if (fileExplorerPathCopy) fileExplorerPathCopy.addEventListener('click', copyCurrentFileExplorerPath);
bindFileExplorerPathInput(fileExplorerPath);
if (fileExplorerRootModeButton) {
  fileExplorerRootModeButton.addEventListener('click', toggleFileExplorerRootMode);
}
renderFileExplorerRootModeControls();
if (fileExplorerHiddenToggle) {
  fileExplorerHiddenToggle.setAttribute('aria-pressed', fileExplorerShowHidden ? 'true' : 'false');
  fileExplorerHiddenToggle.classList.toggle('active', fileExplorerShowHidden);
  fileExplorerHiddenToggle.title = fileExplorerShowHidden ? 'Hide dotfiles (.*)' : 'Show hidden files (dotfiles)';
  fileExplorerHiddenToggle.addEventListener('click', toggleHiddenFiles);
}
if (fileEditorClose) fileEditorClose.addEventListener('click', () => {
  if (activeFile) closeFileTab(activeFile);
});
if (fileEditorSave) fileEditorSave.addEventListener('click', saveCurrentEditor);
if (fileEditorPreviewBtn) fileEditorPreviewBtn.addEventListener('click', togglePreview);
if (fileEditorWrapBtn) fileEditorWrapBtn.addEventListener('click', toggleEditorWrap);
if (fileEditorTextarea) {
  fileEditorTextarea.addEventListener('input', () => {
    if (!activeFile) return;
    const state = openFiles.get(activeFile);
    if (!state || state.kind !== 'text') return;
    state.content = fileEditorTextarea.value;
    const wasDirty = state.dirty;
    state.dirty = state.content !== state.original;
    const status = openFileStatus(state);
    setEditorStatus(status.message, status.level);
    if (wasDirty !== state.dirty) renderSessionButtons();
  });
  fileEditorTextarea.addEventListener('keydown', event => {
    const isSave = (event.ctrlKey || event.metaKey) && event.key === 's' && !event.shiftKey;
    if (isSave) {
      event.preventDefault();
      saveCurrentEditor();
    }
  });
}

function updateSessionButtonStates() {
  // Top navigation is menu-based now; per-session state lives in pane tabs
  // and menu rows, which are rebuilt by their normal render paths.
}

function bindFilePopoverActions(container) {
  container.querySelectorAll('[data-copy-popover-path]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyFilePath(button.dataset.copyPopoverPath || '', 'full');
    });
  });
}

function clearTimer(timer) {
  if (timer) clearTimeout(timer);
  return null;
}

function stopPopoverEvent(event) {
  event.stopPropagation();
}

function closeOtherSessionPopovers(current) {
  for (const other of document.querySelectorAll('.pane-tab.popover-open')) {
    if (other !== current) other.classList.remove('popover-open');
  }
}

function popoverStillActive(anchor, popover) {
  const focused = document.activeElement;
  return Boolean(
    anchor.matches(':hover')
      || popover?.matches(':hover')
      || (focused && (anchor.contains(focused) || popover?.contains(focused)))
  );
}

function bindPopoverHover(anchor, popover, handlers) {
  const queueOpen = handlers.queueOpen || handlers.keepOpen;
  const keepOpen = handlers.keepOpen || queueOpen;
  const closeSoon = handlers.closeSoon;
  const closeIfOutside = event => {
    const next = event?.relatedTarget;
    if (next && (anchor.contains(next) || popover?.contains(next))) return;
    closeSoon(event);
  };

  anchor.addEventListener('pointerenter', queueOpen);
  anchor.addEventListener('pointerleave', closeIfOutside);
  anchor.addEventListener('focusin', queueOpen);
  anchor.addEventListener('focusout', closeIfOutside);
  if (!popover) return;
  popover.addEventListener('pointerenter', keepOpen);
  popover.addEventListener('pointerleave', closeIfOutside);
  popover.addEventListener('click', stopPopoverEvent);
  popover.addEventListener('dragstart', stopPopoverEvent);
  popover.querySelectorAll('a').forEach(link => {
    link.addEventListener('pointerenter', keepOpen);
    link.addEventListener('click', stopPopoverEvent);
  });
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

// Tab buttons are also drag handles, so activation waits until pointer release proves it was a click.
function bindTabActivation(node, activate, options = {}) {
  let pointerCandidate = false;
  let pointerActivated = false;
  let dragged = false;
  let startX = 0;
  let startY = 0;
  const stop = event => {
    if (options.stopPropagation) event.stopPropagation();
  };
  const ignored = event => options.ignore?.(event) === true;
  const resetPointer = () => {
    pointerCandidate = false;
    dragged = false;
  };

  node.addEventListener('pointerdown', event => {
    if (event.button !== 0 || ignored(event)) return;
    pointerCandidate = true;
    pointerActivated = false;
    dragged = false;
    startX = event.clientX;
    startY = event.clientY;
  });
  node.addEventListener('pointerup', event => {
    if (!pointerCandidate || event.button !== 0 || ignored(event)) {
      resetPointer();
      return;
    }
    const moved = Math.abs(event.clientX - startX) > 4 || Math.abs(event.clientY - startY) > 4;
    const wasDragged = dragged;
    resetPointer();
    if (wasDragged || moved) return;
    event.preventDefault();
    stop(event);
    pointerActivated = true;
    activate(event);
  });
  node.addEventListener('click', event => {
    if (ignored(event)) return;
    if (pointerActivated) {
      pointerActivated = false;
      event.preventDefault();
      stop(event);
      return;
    }
    event.preventDefault();
    stop(event);
    activate(event);
  });
  node.addEventListener('dragstart', () => {
    dragged = true;
    pointerCandidate = false;
    pointerActivated = false;
  });
  node.addEventListener('dragend', resetPointer);
}

// Tabs, headers, and popovers all use these helpers so badge precedence stays consistent.
function metaJoin(parts) {
  return parts.filter(Boolean).join('<span class="meta-sep"> · </span>');
}

function sessionNumberNameHtml(session) {
  const label = sessionLabel(session);
  const name = String(session);
  const nameHtml = name && name !== label ? `<span class="session-button-name">${esc(name)}</span>` : '';
  return `<span class="session-button-number">${esc(label)}</span>${nameHtml}`;
}

function yoloMarkerHtml(session, auto, options = {}) {
  const payload = options.payload || autoApproveStates.get(session);
  const locked = !auto && (options.locked === true || (options.locked !== false && autoApproveEnabledElsewhere(payload)));
  if (!auto && !locked && options.enabledOnly !== false) return '';
  const classes = ['session-yolo-marker'];
  if (auto) classes.push('active');
  else if (locked) classes.push('locked');
  else classes.push('inactive');
  if (auto && options.yoloWorking) classes.push('working');
  if (readOnlyMode) classes.push('readonly');
  const yoloAttr = ` data-yolo-session="${esc(session)}"`;
  const toggleAttr = options.toggle && !readOnlyMode ? ` data-auto-session="${esc(session)}"` : '';
  const rotationStyle = auto && options.yoloWorking ? ` style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}"` : '';
  const stateText = auto ? 'on here' : (locked ? 'on elsewhere' : 'off');
  const title = options.toggle && readOnlyMode
    ? `YOLO ${stateText} for ${sessionLabel(session)}; readonly access`
    : (options.toggle ? `YOLO ${stateText} for ${sessionLabel(session)}` : `YOLO ${stateText}`);
  return `<span class="${esc(classes.join(' '))}"${yoloAttr}${toggleAttr}${rotationStyle} title="${esc(title)}">YO</span>`;
}

function pullRequestCompactBadgesHtml(session, pr) {
  const statusHtml = pullRequestStatusIndicatorHtml(session, pr);
  const ciHtml = pullRequestCiIndicatorHtml(session, pr);
  const prHtml = pullRequestPrIndicatorHtml(session, pr);
  return [prHtml, statusHtml, ciHtml].filter(Boolean).join('');
}

function applySessionStateClasses(node, state) {
  node.classList.toggle('needs-attention', state?.attention === true);
  node.classList.toggle('needs-input', state?.key === 'needs-input');
  node.classList.toggle('needs-exec', state?.key === 'needs-approval');
  node.classList.toggle('needs-blocked', state?.key === 'blocked');
  syncAttentionAnimation(node, state?.attention === true);
}

function panelHeaderStateHtml(state) {
  return state ? sessionStateHtml(state) : '';
}

function currentBranchSubject(git) {
  const branches = git?.other_branches?.branches || [];
  const current = branches.find(branch => branch.current);
  return current?.subject || '';
}

function isDefaultBranch(git) {
  return ['main', 'master'].includes(String(git?.branch || ''));
}

function gitHeadSubject(git) {
  return String(git?.head || '').replace(/^[0-9a-f]{7,40}\s+/, '');
}

function pullRequestNumberFromSubject(subject) {
  const match = String(subject || '').match(/\(#(\d+)\)\s*$/);
  return match ? Number(match[1]) : null;
}

function subjectWithoutPullRequestNumber(subject) {
  return String(subject || '').replace(/\s*\(#\d+\)\s*$/, '').trim();
}

function githubPullRequestUrlFromGit(git, number) {
  const repoUrl = git?.github_repo?.url;
  return repoUrl && number ? `${repoUrl}/pull/${number}` : '';
}

function defaultBranchHeadPullRequest(info) {
  const project = info?.project || {};
  const git = project.git;
  if (!isDefaultBranch(git)) return null;
  const subject = gitHeadSubject(git);
  const number = pullRequestNumberFromSubject(subject);
  if (!number) return null;
  const existing = project.pull_request?.number === number ? project.pull_request : {};
  const title = subjectWithoutPullRequestNumber(existing.title || subject);
  const description = subjectWithoutPullRequestNumber(existing.description || subject);
  return {
    ...existing,
    number,
    title,
    description,
    url: existing.url || githubPullRequestUrlFromGit(git, number),
    checks: existing.checks || {state: 'unknown'},
    status_label: '',
    source_only: true,
  };
}

function displayPullRequest(info) {
  return defaultBranchHeadPullRequest(info) || info?.project?.pull_request || null;
}

function metadataBadgeKey(session, badge) {
  return `${session}:${badge}`;
}

function metadataBadgePulseClass(session, badge) {
  if (!session) return '';
  const until = metadataBadgePulseUntil.get(metadataBadgeKey(session, badge));
  if (!until || until <= Date.now()) return '';
  return ' metadata-pulse';
}

function metadataBadgeClasses(session, badge, classes) {
  return `${classes}${metadataBadgePulseClass(session, badge)}`;
}

function updateMetadataBadgePulses(meta) {
  const now = Date.now();
  for (const [key, until] of metadataBadgePulseUntil.entries()) {
    if (until <= now) metadataBadgePulseUntil.delete(key);
  }
  for (const [session, info] of Object.entries(meta?.sessions || {})) {
    const pulses = info?.metadata_badge_pulse_remaining_ms || {};
    for (const badge of ['main', 'pr', 'status', 'ci']) {
      const remaining = Number(pulses[badge] || 0);
      if (remaining > 0) {
        metadataBadgePulseUntil.set(metadataBadgeKey(session, badge), now + remaining);
      }
    }
  }
}

function defaultBranchBadgeHtml(session, info) {
  if (!isDefaultBranch(info?.project?.git)) return '';
  return `<span class="${metadataBadgeClasses(session, 'main', 'ci-indicator tab-symbol branch-indicator')}">MAIN</span>`;
}

function sessionWorkDescription(session, info, limit = 96) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const status = pullRequestStatusLabel(pr);
    const title = pr.title || pr.description || '';
    const prefix = `#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`;
    return shortText(title ? `${prefix}: ${title}` : prefix, limit);
  }
  const linear = project.linear || [];
  const issue = linear.find(item => item.title);
  if (issue) return shortText(`${issue.identifier}: ${issue.title}`, limit);
  const subject = currentBranchSubject(git);
  if (subject) return shortText(subject, limit);
  if (git?.branch) return shortText(shortBranch(git.branch), limit);
  return shortText(projectDirName(session, info), limit);
}

function sessionTabDescription(session, info) {
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const title = pr.title || pr.description || '';
    if (title) return shortText(title, 72);
  }
  return sessionWorkDescription(session, info, 72);
}

function tabMenuDetailText(item, info = transcriptMeta.sessions?.[item]) {
  if (isInfoItem(item)) return 'all branches sorted by recent activity';
  const project = info?.project || {};
  const git = project.git;
  const parts = [];
  if (git?.branch) parts.push(git.branch);
  const path = panelFullPath(item, info);
  if (path) parts.push(compactHomePath(path));
  const pr = displayPullRequest(info);
  const linear = (project.linear || []).map(issue => issue.identifier).filter(Boolean).join(', ');
  if (linear) parts.push(linear);
  if (pr?.number) {
    const status = pullRequestStatusLabel(pr);
    parts.push(`#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`);
  }
  const desc = sessionWorkDescription(item, info, 180);
  if (desc && !parts.includes(desc)) parts.push(desc);
  return parts.join(' · ') || itemLabel(item);
}

function stripPullRequestSuffixText(value) {
  return String(value || '').replace(/\s+\(#\d+\)\s*$/, '').trim();
}

function projectDirName(session, info) {
  if (!info) return 'loading';
  const project = info?.project || {};
  const git = project.git;
  const path = git?.root || git?.cwd || info?.selected_pane?.current_path || '';
  return pathBasename(path) || 'no path';
}

function pathBasename(path) {
  const text = String(path || '').replace(/\/+$/, '');
  if (!text) return '';
  const parts = text.split('/');
  return parts[parts.length - 1] || '';
}

function filePopoverHtml(item) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const rows = filePopoverRows(path, state);
  return `<div class="session-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(basenameOf(path))}</div>
      </div>
    </div>
    ${rows.join('')}
  </div>`;
}

function filePopoverRows(path, state = {}) {
  const kind = state.kind === 'image' ? 'image viewer' : state.kind === 'text' ? 'file editor' : state.kind || 'file';
  const status = state.dirty ? 'modified' : state.loading ? 'loading' : state.error ? String(state.error) : kind;
  const rows = [
    popoverRow('path', filePopoverPathHtml(path)),
  ];
  if (status && status !== kind) rows.push(popoverPairRow('type', esc(kind), 'status', esc(status)));
  else rows.push(popoverRow('type', esc(kind)));
  if (Number.isFinite(state.size)) rows.push(popoverRow('size', formatFileSize(state.size)));
  return rows;
}

function filePopoverPathHtml(path) {
  return `<span class="popover-copy-value">${esc(path)}</span><button type="button" class="path-copy-button popover-copy-button" data-copy-popover-path="${esc(path)}" title="Copy path" aria-label="Copy path"></button>`;
}

function sessionPopoverHtml(session, info, agentKind, autoEnabled, state = sessionState(session, info)) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  const linear = project.linear || [];
  const pane = info?.selected_pane;
  const description = sessionWorkDescription(session, info, 220);
  const title = `${sessionLabel(session)} · ${projectDirName(session, info)}`;
  const subtitle = description || git?.branch || pane?.current_path || 'no checkout detected';
  const rows = [];
  const stateValue = `${sessionStateHtml(state)} <span class="meta-muted">${esc(state.reason)}</span>`;
  const autoPayload = autoApproveStates.get(session);
  const autoElsewhere = autoApproveEnabledElsewhere(autoPayload);
  const autoText = autoEnabled ? 'YOLO on' : (autoElsewhere ? 'YOLO elsewhere' : '');
  const agentValue = agentKind ? `${agentName(agentKind)}${autoText ? ` · ${autoText}` : ''}` : (autoText || 'not detected');
  const displayPath = panelFullPath(session, info) || pane?.current_path || 'not available';
  rows.push(popoverPairRow('state', stateValue, 'agent', agentValue));
  rows.push(popoverRow('path', displayPath));
  if (git?.branch) rows.push(popoverRow('branch', `${branchLinkHtml(git, git.branch)}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`));
  if (Number.isFinite(git?.dirty_count) || Number.isFinite(git?.ahead) || Number.isFinite(git?.behind)) {
    rows.push(popoverRow('git', gitStatusText(git)));
  }
  let linearValue = '';
  let linearDesc = '';
  if (linear.length) {
    linearValue = linearInlineHtml(linear);
    linearDesc = linearDescriptionsInlineHtml(linear);
    if (linearValue) rows.push(popoverRow('Linear', linearValue));
    if (linearDesc) rows.push(popoverRow('details', linearDesc));
  }
  let prDesc = '';
  if (pr?.number) {
    const prParts = [pullRequestLinkHtml(pr), pullRequestAuthorHtml(pr)].filter(Boolean);
    const checks = pullRequestChecksHtml(pr);
    if (checks) prParts.push(checks);
    rows.push(popoverRow('PR', metaJoin(prParts)));
    prDesc = pullRequestDescriptionInlineHtml(pr);
  }
  if (prDesc) {
    rows.push(popoverRow('desc', prDesc));
  }
  const subject = currentBranchSubject(git);
  if (subject && !pr?.number) rows.push(popoverRow('desc', `<div class="popover-desc">${esc(subject)}</div>`));
  if (git?.root && git.root !== displayPath) rows.push(popoverRow('repo', git.root));
  if (git?.head) rows.push(popoverRow('HEAD', git.head));
  return `<div class="session-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(title)}</div>
        <div class="popover-subtitle">${esc(subtitle)}</div>
      </div>
    </div>
    ${rows.join('')}
    ${otherBranchesHtml(git)}
  </div>`;
}

function popoverRow(label, valueHtml) {
  return `<div class="popover-row"><div class="popover-label">${esc(label)}</div><div class="popover-value">${stripTitleAttrs(valueHtml)}</div></div>`;
}

function popoverPairRow(leftLabel, leftValueHtml, rightLabel, rightValueHtml) {
  return `<div class="popover-row compact">
    <div class="popover-label">${esc(leftLabel)}</div><div class="popover-value">${stripTitleAttrs(leftValueHtml)}</div>
    <div class="popover-label">${esc(rightLabel)}</div><div class="popover-value">${stripTitleAttrs(rightValueHtml)}</div>
  </div>`;
}

function stripTitleAttrs(html) {
  return String(html || '').replace(/\s+title="[^"]*"/g, '');
}

function pullRequestDescriptionInlineHtml(pr) {
  const title = String(pr?.title || '').trim();
  const description = String(pr?.description || '').trim();
  const body = description && description !== title ? description.replace(/^#+\s*Overview:\s*/i, '').trim() : '';
  const text = [title, body].filter(Boolean).join(' · ');
  return text ? esc(shortText(text, 180)) : '';
}

function linearInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    const label = issue.identifier || '';
    if (!label) continue;
    const link = linkHtml(issue.url, label, issue.title || '');
    if (!link) continue;
    const state = issue.state ? `<span class="meta-muted"> ${esc(issue.state)}</span>` : '';
    parts.push(`${link}${state}`);
  }
  return metaJoin(parts);
}

function linearDescriptionsInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    if (!issue?.title) continue;
    const prefix = issue.identifier ? `${issue.identifier} ` : '';
    parts.push(`${prefix}${issue.title}`);
  }
  return parts.length ? esc(shortText(parts.join(' · '), 180)) : '';
}

function gitStatusText(git) {
  const parts = [];
  if (Number.isFinite(git.dirty_count)) parts.push(`${git.dirty_count} dirty`);
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(`${git.ahead} ahead`);
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(`${git.behind} behind`);
  return esc(parts.length ? parts.join(' · ') : 'clean');
}

function branchLinkHtml(git, branchName) {
  return esc(branchName || '');
}

function linearIssueHtml(issue) {
  const label = `${issue.identifier}${issue.state ? ` ${issue.state}` : ''}`;
  return linkHtml(issue.url, label, issue.title || '');
}

function linearIssueLinkHtml(identifier) {
  if (!identifier) return '';
  return linkHtml(`https://linear.app/nv/issue/${encodeURIComponent(identifier)}`, identifier, identifier);
}

function pullRequestLinkForBranch(git, branch) {
  const pr = branch?.pull_request;
  const repoUrl = git?.github_repo?.url;
  if (!pr?.number) return '';
  const url = pr.url || (repoUrl ? `${repoUrl}/pull/${pr.number}` : '');
  const status = pullRequestStatusDisplay(pr);
  const label = `#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`;
  return linkHtml(url, label, pr.title || pr.description || branch.subject || '', pullRequestStatusClass(pr));
}

function pullRequestTextForBranch(pr, fallback = '') {
  if (!pr?.number) return '';
  const status = pullRequestStatusDisplay(pr);
  return [`#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`, pr.title || pr.description || fallback].filter(Boolean).join(' ');
}

function otherBranchesHtml(git) {
  const inventory = git?.other_branches || {};
  const branches = inventory.branches || [];
  if (!branches.length) {
    return `<div class="branch-list"><div class="branch-list-title">All branches</div><div class="meta-muted">none found in this checkout</div></div>`;
  }
  const items = branches.map(branch => {
    const branchLink = branchLinkHtml(git, branch.name);
    const prLink = pullRequestLinkForBranch(git, branch);
    const linearLinks = (branch.linear_ids || []).map(linearIssueLinkHtml).filter(Boolean).join(' ');
    const meta = [prLink, linearLinks, esc(branch.updated || '')].filter(Boolean).join(' ');
    return `<div class="branch-item">
      <div class="branch-name">${branch.current ? '<span class="info-branch-current">current</span> ' : ''}${branchLink}</div>
      <div class="branch-meta">${meta}</div>
      <div class="branch-subject">${esc(shortText(branch.subject || '', 240))}</div>
    </div>`;
  }).join('');
  const hidden = Number(inventory.hidden_count || 0) > 0
    ? `<div class="meta-muted">+ ${inventory.hidden_count} more</div>`
    : '';
  return `<div class="branch-list"><div class="branch-list-title">All branches</div>${items}${hidden}</div>`;
}

function dragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-session') || '';
  if (!raw && dragSession) return {session: dragSession, sourceSlot: dragSourceSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isLayoutItem(parsed.session) ? parsed : null;
  } catch (_) {
    return isLayoutItem(raw) ? {session: raw, sourceSlot: null} : null;
  }
}

function fileDragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-file') || '';
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed?.path && !Array.isArray(parsed?.paths)) return null;
    const paths = Array.isArray(parsed.paths) ? parsed.paths.filter(Boolean) : [parsed.path].filter(Boolean);
    return paths.length ? {...parsed, path: parsed.path || paths[0], paths} : null;
  } catch (_) {
    return null;
  }
}

function hasYolomuxFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes('application/x-yolomux-file');
}

function terminalCurrentPath(session) {
  const info = transcriptMeta.sessions?.[session];
  return terminalDisplayPane(info)?.current_path || info?.selected_pane?.current_path || '';
}

function pathRelativeToDirectory(path, directory) {
  const fullPath = String(path || '');
  const rawBase = String(directory || '');
  const base = rawBase === '/' ? '/' : rawBase.replace(/\/+$/, '');
  if (!fullPath || !base || !fullPath.startsWith('/')) return fullPath;
  if (fullPath === base) return '.';
  if (base === '/') return fullPath.slice(1);
  if (!fullPath.startsWith(`${base}/`)) return fullPath;
  return fullPath.slice(base.length + 1);
}

function terminalFileReference(session, path) {
  return pathRelativeToDirectory(path, terminalCurrentPath(session));
}

function terminalFileReferences(session, payload) {
  const paths = Array.isArray(payload?.paths) ? payload.paths : [payload?.path].filter(Boolean);
  return paths.map(path => terminalFileReference(session, path));
}

function transparentNativeDragImage() {
  if (transparentDragImage) return transparentDragImage;
  const node = document.createElement('div');
  node.className = 'transparent-drag-image';
  node.style.position = 'fixed';
  node.style.left = '-10000px';
  node.style.top = '-10000px';
  node.style.width = '1px';
  node.style.height = '1px';
  node.style.opacity = '0';
  node.style.pointerEvents = 'none';
  document.body.appendChild(node);
  transparentDragImage = node;
  return node;
}

function moveCustomDragPreview(event) {
  if (!customDragPreview || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
  customDragPreview.style.left = `${Math.round(event.clientX - customDragPreviewOffset.x)}px`;
  customDragPreview.style.top = `${Math.round(event.clientY - customDragPreviewOffset.y)}px`;
}

function stopCustomDragPreview() {
  document.removeEventListener?.('dragover', moveCustomDragPreview, true);
  document.removeEventListener?.('drag', moveCustomDragPreview, true);
  document.removeEventListener?.('drop', stopCustomDragPreview, true);
  customDragPreview?.remove();
  customDragPreview = null;
}

function startCustomDragPreview(event) {
  const source = event.currentTarget;
  if (!source || !source.cloneNode) return;
  stopCustomDragPreview();
  const rect = source.getBoundingClientRect();
  const clone = source.cloneNode(true);
  clone.classList?.remove('popover-open', 'dragging');
  clone.classList?.add('drag-image');
  clone.querySelectorAll?.('.session-popover').forEach(node => node.remove());
  clone.style.position = 'fixed';
  clone.style.width = `${Math.max(1, Math.round(rect.width || 1))}px`;
  clone.style.height = `${Math.max(1, Math.round(rect.height || 1))}px`;
  clone.style.opacity = '0.50';
  clone.style.pointerEvents = 'none';
  clone.style.zIndex = '99999';
  document.body.appendChild(clone);
  customDragPreview = clone;
  const offsetX = Math.max(0, Math.min(rect.width || 0, event.clientX - rect.left)) || Math.max(1, (rect.width || 1) / 2);
  const offsetY = Math.max(0, Math.min(rect.height || 0, event.clientY - rect.top)) || Math.max(1, (rect.height || 1) / 2);
  customDragPreviewOffset = {x: offsetX, y: offsetY};
  moveCustomDragPreview(event);
  document.addEventListener?.('dragover', moveCustomDragPreview, true);
  document.addEventListener?.('drag', moveCustomDragPreview, true);
  document.addEventListener?.('drop', stopCustomDragPreview, true);
  event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
}

function startFileDragPreview(event, paths, entry) {
  stopCustomDragPreview();
  const normalizedPaths = Array.from(new Set((paths || []).filter(Boolean)));
  const firstPath = normalizedPaths[0] || '';
  const preview = document.createElement('div');
  preview.className = 'file-drag-image drag-image';
  const title = normalizedPaths.length === 1 ? basenameOf(firstPath) : `${normalizedPaths.length} items`;
  const pathRows = normalizedPaths.slice(0, 4)
    .map(path => `<div class="file-drag-path">${esc(path)}</div>`)
    .join('');
  const more = normalizedPaths.length > 4 ? `<div class="file-drag-more">+ ${normalizedPaths.length - 4} more</div>` : '';
  preview.innerHTML = `
    <div class="file-drag-main">
      ${fileDragPreviewMedia(firstPath, entry)}
      <div class="file-drag-copy">
        <div class="file-drag-title">${esc(title)}</div>
        ${pathRows}${more}
      </div>
    </div>`;
  preview.style.position = 'fixed';
  preview.style.pointerEvents = 'none';
  preview.style.zIndex = '99999';
  document.body.appendChild(preview);
  customDragPreview = preview;
  customDragPreviewOffset = {x: -14, y: -14};
  moveCustomDragPreview(event);
  document.addEventListener?.('dragover', moveCustomDragPreview, true);
  document.addEventListener?.('drag', moveCustomDragPreview, true);
  document.addEventListener?.('drop', stopCustomDragPreview, true);
  preview.getBoundingClientRect();
  event.dataTransfer?.setDragImage?.(preview, 18, 18);
}

function fileDragPreviewMedia(path, entry) {
  const kind = entry?.kind || 'file';
  if (kind === 'file' && IMAGE_EXTENSIONS.has(fileExtensionOf(path))) {
    return `<img class="file-drag-thumb" src="${rawFileUrl(path)}" alt="">`;
  }
  const icon = kind === 'dir' ? '▸' : '📄';
  return `<span class="file-drag-thumb file-drag-icon" aria-hidden="true">${icon}</span>`;
}

function startSessionDrag(event, session, sourceSlot = null) {
  dragSession = session;
  dragSourceSlot = sourceSlot;
  const payload = JSON.stringify({session, sourceSlot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-session', payload);
  event.dataTransfer.setData('text/plain', session);
  startCustomDragPreview(event);
}

function endSessionDrag(event) {
  dragSession = null;
  dragSourceSlot = null;
  stopCustomDragPreview();
  sessionButtons.classList.remove('drag-over');
  clearDropPreview();
}

function layoutWithoutItemFromSlots(item, slots = layoutSlots, options = {}) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = slots?.[layoutTreeKey] || null;
  const preserveEmptySlot = options.preserveEmptySlot || null;
  const preserveRemovedSlot = options.preserveRemovedSlot === true;
  const preservePlaceholders = options.preservePlaceholders !== false;
  for (const side of layoutSlotKeys(slots)) {
    if (paneIsPlaceholder(side, slots)) {
      if (preservePlaceholders) next[side] = emptyPlaceholderPaneState();
      continue;
    }
    const hadItem = paneTabs(side, slots).includes(item);
    const active = activeItemForSide(side, slots);
    const tabs = paneTabs(side, slots).filter(value => value !== item);
    next[side] = !tabs.length && (side === preserveEmptySlot || (preserveRemovedSlot && hadItem))
      ? emptyPlaceholderPaneState()
      : paneStateWithTabs(tabs, active === item ? null : active);
  }
  return next;
}

function layoutWithoutItem(item, options = {}) {
  return layoutWithoutItemFromSlots(item, layoutSlots, options);
}

function removeSessionFromLayout(item) {
  if (!itemInLayout(item)) return;
  const isFiles = isFileExplorerItem(item);
  applyLayoutSlots(layoutWithoutItem(item, {
    preserveRemovedSlot: !isFiles,
    preservePlaceholders: !isFiles,
  }), {
    message: `${itemLabel(item)} hidden from layout`,
  });
}

function removePaneFromLayout(item) {
  const slot = slotForSession(item);
  if (!slot) return;
  const moved = paneTabs(slot);
  applyLayoutSlots(layoutWithoutSlot(slot, {preserveRemovedSlot: shouldPreserveClosedPaneSlot(slot)}), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} hidden from layout` : '',
  });
}

function shouldPreserveClosedPaneSlot(slot) {
  if (!slot || isFileExplorerItem(activeItemForSide(slot))) return false;
  return layoutSlotKeys().some(side => side !== slot && isFileExplorerItem(activeItemForSide(side)));
}

function layoutWithoutSlot(slot, options = {}) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (side === slot) {
      if (options.preserveRemovedSlot === true) next[side] = emptyPlaceholderPaneState();
      continue;
    }
    next[side] = paneStateForLayoutSlot(side);
  }
  return next;
}

function appendUniqueItems(target, items) {
  for (const item of items) {
    if (isLayoutItem(item) && !target.includes(item)) target.push(item);
  }
  return target;
}

function paneTabsWithoutFinder(slot, slots = layoutSlots) {
  return paneTabs(slot, slots).filter(item => !isFileExplorerItem(item));
}

function canPaneExpand(item, slots = layoutSlots) {
  const targetSlot = slotForItem(item, slots);
  if (!targetSlot || isFileExplorerItem(activeItemForSide(targetSlot, slots))) return false;
  if (!activeItemForSide(targetSlot, slots)) return false;
  return layoutSlotKeys(slots).some(slot => (
    slot !== targetSlot
    && !isFileExplorerItem(activeItemForSide(slot, slots))
    && paneTabsWithoutFinder(slot, slots).length > 0
  ));
}

function minimizePaneFromLayout(item) {
  const sourceSlot = slotForSession(item);
  if (!sourceSlot) return;
  if (isFileExplorerItem(activeItemForSide(sourceSlot))) {
    removePaneFromLayout(item);
    return;
  }
  const minimizedTabs = paneTabsWithoutFinder(sourceSlot);
  const targetSlot = largestNonFileExplorerPaneSlot(new Set([sourceSlot]));
  if (!targetSlot || !minimizedTabs.length) {
    removePaneFromLayout(item);
    return;
  }
  const targetActive = activeItemForSide(targetSlot);
  const next = layoutWithoutSlot(sourceSlot, {preserveRemovedSlot: shouldPreserveClosedPaneSlot(sourceSlot)});
  const targetTabs = appendUniqueItems(paneTabsWithoutFinder(targetSlot, next), minimizedTabs);
  next[targetSlot] = paneStateWithTabs(targetTabs, targetActive);
  applyLayoutSlots(next, {
    focusSession: targetActive || targetTabs[0],
    prune: false,
    message: `${minimizedTabs.map(itemLabel).join(', ')} minimized`,
  });
}

function finderLeadsExpandedPane(finderSlot, targetSlot) {
  const finderRect = layoutColumnNode(finderSlot)?.getBoundingClientRect();
  const targetRect = layoutColumnNode(targetSlot)?.getBoundingClientRect();
  if (finderRect && targetRect && Math.abs(finderRect.left - targetRect.left) > 1) return finderRect.left < targetRect.left;
  const leaves = layoutLeafSlots(layoutSlots[layoutTreeKey]);
  const finderIndex = leaves.indexOf(finderSlot);
  const targetIndex = leaves.indexOf(targetSlot);
  if (finderIndex !== -1 && targetIndex !== -1) return finderIndex < targetIndex;
  return true;
}

function expandPaneFromLayout(item) {
  const targetSlot = slotForSession(item);
  if (!targetSlot || !canPaneExpand(item)) return;
  const active = activeItemForSide(targetSlot);
  if (!active) return;
  const finderSlot = slotForSession(fileExplorerItemId);
  const targetTabs = appendUniqueItems([], paneTabsWithoutFinder(targetSlot));
  for (const slot of layoutSlotKeys()) {
    if (slot === targetSlot) continue;
    appendUniqueItems(targetTabs, paneTabsWithoutFinder(slot));
  }
  const next = emptyLayoutSlots();
  next[targetSlot] = paneStateWithTabs(targetTabs, active);
  if (finderSlot && finderSlot !== targetSlot) {
    next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
    const finderFirst = finderLeadsExpandedPane(finderSlot, targetSlot);
    next[layoutTreeKey] = finderFirst
      ? splitNode('row', leafNode(finderSlot), leafNode(targetSlot), fileExplorerSplitPercent)
      : splitNode('row', leafNode(targetSlot), leafNode(finderSlot), 100 - fileExplorerSplitPercent);
  } else {
    next[layoutTreeKey] = leafNode(targetSlot);
  }
  applyLayoutSlots(next, {
    focusSession: active,
    prune: false,
    message: `${itemLabel(active)} expanded`,
  });
}

function layoutWithFileExplorerDockedLeft(slots = layoutSlots) {
  const right = compactLayoutSlots(layoutWithoutItemFromSlots(fileExplorerItemId, slots, {preservePlaceholders: true}));
  const rightSlots = layoutSlotKeys(right).filter(slot => paneHasLayoutContent(slot, right));
  const next = emptyLayoutSlots();
  for (const slot of rightSlots) next[slot] = paneStateForLayoutSlot(slot, right);
  const used = new Set(rightSlots);
  const currentSlot = slotForItem(fileExplorerItemId, slots);
  let finderSlot = currentSlot && !used.has(currentSlot) ? currentSlot : null;
  if (!finderSlot) finderSlot = !used.has('left') ? 'left' : nextLayoutSlot(next);
  next[finderSlot] = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
  next[layoutTreeKey] = rightSlots.length
    ? splitNode('row', leafNode(finderSlot), right[layoutTreeKey], fileExplorerSplitPercent)
    : leafNode(finderSlot);
  return compactLayoutSlots(next);
}

function dockFileExplorerPane() {
  applyLayoutSlots(layoutWithFileExplorerDockedLeft(), {
    focusSession: fileExplorerItemId,
    prune: false,
  });
}

function firstEmptyPane(slots = layoutSlots) {
  const placeholderSlot = layoutSlotKeys(slots).find(slot => paneIsPlaceholder(slot, slots));
  if (placeholderSlot) return placeholderSlot;
  return layoutSlotKeys(slots).length ? null : 'left';
}

function slotForNewSession() {
  const empty = firstEmptyPane();
  if (empty) return empty;
  const focusedSlot = focusedPanelItem ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return 'left';
}

function slotForTabActivation(item) {
  const currentSlot = slotForSession(item);
  if (currentSlot) return currentSlot;
  return largestNonFileExplorerPaneSlot() || firstEmptyPane() || largestPaneSlot() || slotForNewSession();
}

async function activateTabInExistingPane(item) {
  if (!isLayoutItem(item)) return;
  if (isTmuxSession(item)) {
    const ensured = await ensureSession(item);
    if (!ensured) return;
  }
  const targetSlot = slotForTabActivation(item);
  if (!targetSlot) return;
  const currentSlot = slotForSession(item);
  if (currentSlot === targetSlot) {
    activatePaneTab(targetSlot, item);
    return;
  }
  await moveSessionToSlot(item, targetSlot, currentSlot, paneTabs(targetSlot).length);
}

function filesOnlySlotForSession(session) {
  const filesSlot = slotForSession(fileExplorerItemId);
  if (!filesSlot) return null;
  const stack = paneTabs(filesSlot).filter(item => item !== session);
  return stack.length === 1 && stack[0] === fileExplorerItemId ? filesSlot : null;
}

function slotForNewTmuxSession(session) {
  const currentSlot = slotForSession(session);
  if (currentSlot) return currentSlot;
  const empty = firstEmptyPane();
  if (empty) return empty;
  const targetSlot = largestNonFileExplorerPaneSlot();
  if (targetSlot) return targetSlot;
  return filesOnlySlotForSession(session) || largestPaneSlot() || slotForNewSession();
}

async function placeTmuxSession(session) {
  const currentSlot = slotForSession(session);
  if (currentSlot) {
    activatePaneTab(currentSlot, session);
    return;
  }
  const targetSlot = slotForNewTmuxSession(session);
  if (!targetSlot) return;
  if (paneIsPlaceholder(targetSlot) || !paneTabs(targetSlot).length) {
    await moveSessionToSlot(session, targetSlot, null);
    return;
  }
  if (isFileExplorerItem(activeItemForSide(targetSlot))) {
    await splitSessionAtSlot(session, targetSlot, 'right', null, fileExplorerSplitPercent);
    return;
  }
  await moveSessionToSlot(session, targetSlot, null, paneTabs(targetSlot).length);
}

async function openFileExplorerPane() {
  const currentSlot = slotForSession(fileExplorerItemId);
  if (currentSlot) {
    if (paneTabs(currentSlot).length === 1 && !fileExplorerNeedsLeftDock()) {
      activatePaneTab(currentSlot, fileExplorerItemId);
      return;
    }
    dockFileExplorerPane();
    return;
  }
  const empty = firstEmptyPane();
  if (empty) {
    if (layoutSlotKeys().includes(empty) && paneIsPlaceholder(empty)) {
      await splitSessionBesidePlaceholder(fileExplorerItemId, empty, 'left', fileExplorerSplitPercent);
    } else {
      await moveSessionToSlot(fileExplorerItemId, empty, null);
    }
    return;
  }
  const targetSlot = largestPaneSlot();
  if (targetSlot && paneTabs(targetSlot).length) {
    dockFileExplorerPane();
    return;
  }
  await moveSessionToSlot(fileExplorerItemId, slotForNewSession(), null);
}

async function splitSessionBesidePlaceholder(session, targetSlot, zone, pct = defaultSplitPercent) {
  if (!isLayoutItem(session) || !targetSlot || !['top', 'bottom', 'left', 'right'].includes(zone)) return;
  if (!paneIsPlaceholder(targetSlot)) {
    await splitSessionAtSlot(session, targetSlot, zone, null, pct);
    return;
  }
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyPlaceholderPaneState();
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const existingNode = leafNode(targetSlot);
  const newNode = leafNode(newSlot);
  const splitPct = splitPercentForNewItem(session, zone, pct);
  const replacement = zone === 'right' || zone === 'bottom'
    ? splitNode(direction, existingNode, newNode, splitPct)
    : splitNode(direction, newNode, existingNode, splitPct);
  next[layoutTreeKey] = replaceLayoutLeaf(next[layoutTreeKey], targetSlot, replacement);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

async function moveSessionToSlot(session, targetSlot, sourceSlot = null, insertIndex = 0) {
  if (!isLayoutItem(session) || !targetSlot) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyPaneState();
  const tabs = next[targetSlot].tabs;
  const index = Math.max(0, Math.min(Number.isFinite(insertIndex) ? insertIndex : 0, tabs.length));
  tabs.splice(index, 0, session);
  next[targetSlot] = paneStateWithTabs(tabs, session);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

async function dropSessionWithIntent(session, intent, sourceSlot = null) {
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
    return;
  }
  if (!intent?.targetSlot || intent.zone === 'middle') {
    await moveSessionToSlot(session, intent?.targetSlot || slotForNewSession(), sourceSlot);
    return;
  }
  await splitSessionAtSlot(session, intent.targetSlot, intent.zone, sourceSlot);
}

function splitPercentForNewItem(session, zone, pct = null) {
  if (pct !== null && pct !== undefined && pct !== '' && Number.isFinite(Number(pct))) return Number(pct);
  if (isFileExplorerItem(session) && (zone === 'left' || zone === 'right')) {
    return zone === 'left' ? fileExplorerSplitPercent : 100 - fileExplorerSplitPercent;
  }
  return defaultSplitPercent;
}

function shouldPreserveSourceSlotForSplit(sourceSlot, targetSlot) {
  return Boolean(sourceSlot && sourceSlot !== targetSlot && isFileExplorerItem(activeItemForSide(targetSlot)));
}

async function splitSessionAtSlot(session, targetSlot, zone, sourceSlot = null, pct = null) {
  if (!isLayoutItem(session) || !targetSlot || !['top', 'bottom', 'left', 'right'].includes(zone)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session, {preserveEmptySlot: shouldPreserveSourceSlotForSplit(sourceSlot, targetSlot) ? sourceSlot : null});
  const targetTabs = paneTabs(targetSlot, next);
  if (!targetTabs.length) {
    await moveSessionToSlot(session, targetSlot, sourceSlot);
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = paneStateWithTabs([session], session);
  const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const existingNode = leafNode(targetSlot);
  const newNode = leafNode(newSlot);
  const splitPct = splitPercentForNewItem(session, zone, pct);
  const replacement = zone === 'right' || zone === 'bottom'
    ? splitNode(direction, existingNode, newNode, splitPct)
    : splitNode(direction, newNode, existingNode, splitPct);
  next[layoutTreeKey] = replaceLayoutLeaf(next[layoutTreeKey], targetSlot, replacement);
  applyLayoutSlots(next, {focusSession: session, prune: false});
}

function replaceLayoutLeaf(node, slot, replacement) {
  if (!node) return replacement;
  if (node.slot) return node.slot === slot ? replacement : node;
  const children = (node.children || []).map(child => replaceLayoutLeaf(child, slot, replacement));
  return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
}

function activatePaneTab(side, session) {
  if (!layoutSlotKeys().includes(side) || !itemInLayout(session)) return;
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  setFocusedPanelItem(session);
  if (activeItemForSide(side) === session) {
    focusPanel(session);
    return;
  }
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const key of layoutSlotKeys()) next[key] = paneStateForLayoutSlot(key);
  next[side].active = session;
  applyLayoutSlots(next, {focusSession: session});
}

async function selectSession(session) {
  if (isFileEditorItem(session)) {
    activeFile = fileItemPath(session);
    updateFileExplorerCurrentFileHighlight();
  }
  if (isFileExplorerItem(session)) {
    await openFileExplorerPane();
    scheduleFileExplorerActiveTabSync();
    return;
  }
  if (activeSessions.includes(session)) {
    focusPanel(session);
    return;
  }
  if (isTmuxSession(session) && filesOnlySlotForSession(session)) {
    await placeTmuxSession(session);
    return;
  }
  await activateTabInExistingPane(session);
}

function sessionAgentKind(session) {
  const info = transcriptMeta.sessions?.[session];
  const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
  const kind = String(agent?.kind || '').toLowerCase();
  return kind === 'claude' || kind === 'codex' ? kind : '';
}

function agentIcon(kind) {
  if (kind === 'codex') {
    return `<span class="agent-icon codex" aria-label="Codex" title="Codex">${codexIcon()}</span>`;
  }
  if (kind === 'claude') {
    return `<span class="agent-icon claude" aria-label="Claude" title="Claude">${claudeIcon()}</span>`;
  }
  return '';
}

function codexIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <path fill="#667ef8" d="M7.3 20.8c-3.1 0-5.7-2.4-5.9-5.5-.2-2.4 1.1-4.6 3.1-5.7C4.8 5.9 7.9 3 11.8 3c3.3 0 6.2 2.2 7 5.4 2.4.7 4 2.8 4 5.4 0 3.2-2.6 5.8-5.8 5.8-.9 1.1-2.2 1.8-3.8 1.8-1.2 0-2.3-.4-3.1-1.1-.8.3-1.8.5-2.8.5z"/>
    <path fill="#fff" d="M6.4 8.2c.5-.5 1.2-.5 1.7 0l2.8 2.8c.5.5.5 1.2 0 1.7l-2.8 2.8c-.5.5-1.2.5-1.7 0s-.5-1.2 0-1.7l1.9-1.9-1.9-1.9c-.5-.5-.5-1.3 0-1.8zM13 13.2h5.1c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2H13c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2z"/>
  </svg>`;
}

function claudeIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <rect width="24" height="24" rx="5.5" fill="#cf7554"/>
    <g fill="#fff7f1">
      <path d="M11.1 2.4h1.8l1.1 7.9-2 .6-2-.6 1.1-7.9z"/>
      <path d="m17.8 4.3 1.4 1.1-4.3 6.7-2.1-1.3 5-6.5z"/>
      <path d="m21.5 10.2.3 1.8-8.2 2-1-2.3 8.9-1.5z"/>
      <path d="m20.2 16.8-1.1 1.4-6.7-4.3 1.3-2.1 6.5 5z"/>
      <path d="m13.8 21.5-1.8.3-2-8.2 2.3-1 1.5 8.9z"/>
      <path d="m6.2 19.7-1.4-1.1 4.3-6.7 2.1 1.3-5 6.5z"/>
      <path d="m2.5 13.8-.3-1.8 8.2-2 1 2.3-8.9 1.5z"/>
      <path d="m3.8 7.2 1.1-1.4 6.7 4.3-1.3 2.1-6.5-5z"/>
      <circle cx="12" cy="12" r="2.2"/>
    </g>
  </svg>`;
}

function agentName(kind) {
  return kind === 'codex' ? 'Codex' : kind === 'claude' ? 'Claude' : kind === 'term' ? 'Term' : '';
}

function numericSessionName(session) {
  const match = String(session).match(/^[1-9]\d*$/);
  return match ? Number(match[0]) : null;
}

function sessionLabelAssignments() {
  const assigned = new Map();
  const used = new Set();
  for (const session of visibleSessions) {
    const numeric = numericSessionName(session);
    if (numeric !== null) {
      assigned.set(session, String(numeric));
      used.add(numeric);
    }
  }

  const backfill = [];
  for (let value = 9; value >= 1; value -= 1) {
    if (!used.has(value)) backfill.push(value);
  }

  let overflow = 10;
  for (const session of visibleSessions) {
    if (assigned.has(session)) continue;
    let label = backfill.length ? backfill.shift() : overflow;
    while (used.has(label)) label += 1;
    assigned.set(session, String(label));
    used.add(label);
    if (label >= overflow) overflow = label + 1;
  }
  return assigned;
}

function sessionForLabel(label) {
  const text = String(label);
  for (const [session, assignedLabel] of sessionLabelAssignments()) {
    if (assignedLabel === text) return session;
  }
  return null;
}

function sessionLabel(session) {
  const assigned = sessionLabelAssignments().get(session);
  if (assigned) return assigned;
  const numeric = numericSessionName(session);
  if (numeric !== null) return String(numeric);
  return String(session);
}

function shortText(value, limit = 96) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 3))}...`;
}

function shortBranch(value) {
  const text = String(value || '');
  if (text.length <= 46) return text;
  return `${text.slice(0, 18)}...${text.slice(-25)}`;
}

function linkHtml(url, label, title = '', className = '') {
  if (!url) return `<span>${esc(label)}</span>`;
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const classAttr = className ? ` class="${esc(className)}"` : '';
  return `<a href="${esc(url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr}${classAttr}>${esc(label)}</a>`;
}

function pullRequestStatusLabel(pr) {
  if (!pr) return '';
  if (pr.source_only) return '';
  if (pr.status_label) return pr.status_label;
  if (pr.draft) return 'draft';
  if (pr.merged || pr.merged_at) return 'merged';
  return pr.state || '';
}

function pullRequestStatusDisplay(pr) {
  const status = pullRequestStatusLabel(pr);
  if (!status) return '';
  const key = status.toLowerCase();
  if (key === 'unknown') return '';
  if (key === 'merged') return 'MERGED';
  if (key === 'draft') return 'DRAFT';
  if (key === 'closed') return 'CLOSED';
  if (key === 'open') return 'OPEN';
  return status.replace(/\bci\b/gi, 'CI').toUpperCase();
}

function pullRequestLinkLabel(pr) {
  const status = pullRequestStatusDisplay(pr);
  return `PR #${pr.number}${status ? ` ${status}` : ''}`;
}

function pullRequestStatusClass(pr) {
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (status.includes('failing')) return 'pr-status-failing';
  if (status.includes('pending')) return 'pr-status-pending';
  if (status.includes('passing')) return 'pr-status-passing';
  if (status.includes('merged')) return 'pr-status-merged';
  if (status.includes('draft')) return 'pr-status-draft';
  if (status.includes('closed')) return 'pr-status-closed';
  return 'pr-status-unknown';
}

function pullRequestStatusIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (!['merged', 'draft', 'closed'].includes(status)) return '';
  return `<span class="${metadataBadgeClasses(session, 'status', `ci-indicator tab-symbol ${pullRequestStatusClass(pr)}`)}">${pullRequestStatusDisplay(pr)}</span>`;
}

function pullRequestPrIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  return `<span class="${metadataBadgeClasses(session, 'pr', `ci-indicator tab-symbol pr-indicator ${pullRequestStatusClass(pr)}`)}">#${esc(pr.number)}</span>`;
}

function pullRequestCiIndicatorHtml(session, pr) {
  if (pullRequestStatusLabel(pr).toLowerCase() === 'merged') return '';
  const state = pr?.checks?.state;
  if (!state || state === 'unknown') return '';
  return `<span class="${metadataBadgeClasses(session, 'ci', `ci-indicator tab-symbol ${pullRequestStatusClass(pr)}`)}">CI</span>`;
}

function pullRequestLinkHtml(pr) {
  return linkHtml(pr.url, pullRequestLinkLabel(pr), pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestAuthorHtml(pr) {
  const author = String(pr?.author_login || '').trim();
  return author ? `<span class="meta-muted">by ${esc(author)}</span>` : '';
}

function pullRequestColumnLinkHtml(pr) {
  const status = pullRequestStatusDisplay(pr);
  const label = `#${pr.number}${status ? ` ${status}` : ''}`;
  return linkHtml(pr.url, label, pr.title || pr.description || '', pullRequestStatusClass(pr));
}

function pullRequestChecksHtml(pr) {
  const checks = pr?.checks;
  if (!checks || !checks.state || checks.state === 'unknown') return '';
  const cls = pullRequestStatusClass(pr);
  const parts = [`<span class="meta-pr-status ${cls}">${esc(checks.summary || `CI ${checks.state}`)}</span>`];
  const failing = (checks.failing || []).map(item => item.name).filter(Boolean);
  const pending = (checks.pending || []).map(item => item.name).filter(Boolean);
  if (failing.length) parts.push(`<span class="meta-muted">failing: ${esc(shortText(failing.join(', '), 180))}</span>`);
  if (pending.length) parts.push(`<span class="meta-muted">pending: ${esc(shortText(pending.join(', '), 180))}</span>`);
  if (Number.isFinite(checks.total)) parts.push(`<span class="meta-muted">${checks.total} checks</span>`);
  return metaJoin(parts);
}

function panelFullPath(session, info) {
  const project = info?.project || {};
  const git = project.git;
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const nonHomePane = panes.find(pane => pane?.current_path && pane.current_path !== homePath && !['claude', 'codex'].includes(String(pane.command || '').toLowerCase()));
  if (nonHomePane?.current_path) return nonHomePane.current_path;
  if (git?.cwd) return git.cwd;
  if (git?.root) return git.root;
  if (info?.selected_pane?.current_path) return info.selected_pane.current_path;
  return '';
}

function compactHomePath(path) {
  const text = String(path || '').replace(/\/+$/, '');
  const home = String(homePath || '').replace(/\/+$/, '');
  if (!text || !home) return text;
  if (text === home) return '~';
  if (text.startsWith(`${home}/`)) return `~/${text.slice(home.length + 1)}`;
  return text;
}

function projectMetaHtml(session, info) {
  const project = info?.project || {};
  const git = project.git;
  const parts = [];
  const fullPath = panelFullPath(session, info);
  if (!git) {
    if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
    parts.push('<span class="meta-muted">no git checkout detected</span>');
    return metaJoin(parts);
  }
  const pr = displayPullRequest(info);
  if (pr?.number) parts.push(pullRequestLinkHtml(pr));
  if (git.branch) parts.push(`<span class="meta-branch">${esc(shortBranch(git.branch))}</span>`);
  if (fullPath) parts.push(`<span class="meta-path">${esc(compactHomePath(fullPath))}</span>`);
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(`<span class="meta-muted">behind ${git.behind}</span>`);
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(`<span class="meta-muted">ahead ${git.ahead}</span>`);
  if (Number.isFinite(git.dirty_count) && git.dirty_count > 0) parts.push(`<span class="meta-muted">dirty ${git.dirty_count}</span>`);
  if (pr?.number) {
    if (pr.checks?.state && pr.checks.state !== 'unknown') {
      parts.push(`<span class="meta-pr-status ${pullRequestStatusClass(pr)}">${esc(pr.checks.summary || pullRequestStatusLabel(pr))}</span>`);
    }
  }
  for (const issue of project.linear || []) {
    const state = issue.state ? ` ${issue.state}` : '';
    parts.push(linkHtml(issue.url, `${issue.identifier}${state}`, issue.title || ''));
  }
  const desc = pr?.title || pr?.description || (project.linear || []).find(issue => issue.title)?.title || '';
  if (desc) parts.push(`<span class="meta-desc">${esc(shortText(desc, 160))}</span>`);
  return parts.length ? metaJoin(parts) : '<span class="meta-muted">git checkout detected</span>';
}

function summaryContextHtml(session, info, agent) {
  const lines = [];
  const pane = info?.selected_pane;
  if (agent) {
    lines.push(summaryContextLine('agent', `${agent.kind || 'agent'} pid=${agent.pid || ''}${agent.status ? ` status=${agent.status}` : ''}`));
    if (agent.transcript) lines.push(summaryContextLine('transcript', agent.transcript));
    if (agent.error && !agent.transcript) lines.push(summaryContextLine('transcript', agent.error));
  } else {
    lines.push(summaryContextLine('agent', 'not detected'));
  }
  if (pane) lines.push(summaryContextLine('pane', `${pane.command || 'tmux'} ${pane.target || session} in ${pane.current_path || ''}`));

  const project = info?.project || {};
  const git = project.git;
  if (git) {
    lines.push(summaryContextLine('branch', `${git.branch || 'unknown'}${git.upstream ? ` -> ${git.upstream}` : ''}`));
    if (git.root) lines.push(summaryContextLine('repo', git.root));
    if (git.head) lines.push(summaryContextLine('head', git.head));
  } else {
    lines.push(summaryContextLine('repo', 'no git checkout detected'));
  }
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const label = pullRequestLinkLabel(pr);
    lines.push(summaryContextLine('github', `${label} ${pr.title || pr.description || ''}`, pr.url, label, pullRequestStatusClass(pr)));
  }
  for (const issue of project.linear || []) {
    const label = `${issue.identifier}${issue.state ? ` ${issue.state}` : ''}`;
    lines.push(summaryContextLine('linear', `${label} ${issue.title || ''}`, issue.url, issue.identifier));
  }
  return lines.join('');
}

function summaryContextLine(label, text, url = '', linkLabel = '', linkClass = '') {
  const value = url && linkLabel
    ? `${linkHtml(url, linkLabel, text, linkClass)} ${esc(text.replace(linkLabel, '').trim())}`
    : esc(text);
  return `<div class="summary-context-line"><span class="summary-context-label">${esc(label)}:</span> ${value}</div>`;
}

async function ensureSession(session) {
  if (readOnlyMode) return true;
  try {
    const response = await apiFetch(`/api/ensure-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session create failed')}</span>`;
      return false;
    }
    statusEl.innerHTML = payload.created
      ? `<span class="ok">created ${esc(sessionLabel(session))} with Claude</span>`
      : `<span class="ok">${esc(sessionLabel(session))} ready</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session check failed: ${esc(error)}</span>`;
    return false;
  }
}

async function createNextSession(agent) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot create sessions</span>';
    return;
  }
  const agentLabel = agentName(agent) || 'agent';
  statusEl.textContent = `creating ${agentLabel} session...`;
  try {
    const response = await apiFetch(`/api/create-session?agent=${encodeURIComponent(agent)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session create failed')}</span>`;
      return;
    }
    const previousActive = activeSessions.slice();
    updateSessionList(payload.sessions || []);
    renderSessionButtons();
    renderPanels(previousActive);
    await placeTmuxSession(payload.session);
    await ensureTerminalRunning(payload.session);
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">created ${esc(sessionLabel(payload.session))} (${esc(payload.session)}) with ${esc(agentName(payload.agent) || agentLabel)}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session create failed: ${esc(error)}</span>`;
  }
}

function tmuxSessionNameError(name) {
  const text = String(name || '').trim();
  if (!text) return 'session name is required';
  if (text.length > 64) return 'session name must be 64 characters or fewer';
  // Keep in sync with TMUX_SESSION_NAME_RE in yolomux_lib/app.py.
  if (!/^[A-Za-z0-9_. -]+$/.test(text)) return 'session name may contain only letters, numbers, spaces, dot, dash, and underscore';
  return '';
}

function rekeyMap(map, oldKey, newKey) {
  if (!map.has(oldKey) || oldKey === newKey) return;
  if (!map.has(newKey)) map.set(newKey, map.get(oldKey));
  map.delete(oldKey);
}

function stopSessionUi(session) {
  const item = terminals.get(session);
  if (item) closeTerminalItem(session, item);
  terminals.delete(session);
  stopTranscriptStream(session);
  stopSummaryStream(session);
  const panel = panelNodes.get(session);
  if (panel) panel.remove();
  panelNodes.delete(session);
}

function replaceSessionMetadata(oldSession, newSession) {
  for (const map of [
    autoApproveStates,
    sessionStateKeys,
    notificationLastSent,
    attentionAlertTimers,
    metadataBadgePulseUntil,
    uploadResultsBySession,
    uploadCleanupTimers,
    pasteCounters,
  ]) {
    rekeyMap(map, oldSession, newSession);
  }
  if (transcriptMeta.sessions?.[oldSession]) {
    transcriptMeta.sessions = {
      ...(transcriptMeta.sessions || {}),
      [newSession]: transcriptMeta.sessions[newSession] || transcriptMeta.sessions[oldSession],
    };
    delete transcriptMeta.sessions[oldSession];
  }
  if (Array.isArray(transcriptMeta.session_order)) {
    transcriptMeta.session_order = transcriptMeta.session_order.map(item => item === oldSession ? newSession : item);
  }
}

function replaceTmuxSessionInClient(oldSession, newSession, nextSessions) {
  const next = normalizedSessionOrder(nextSessions) || sessions.map(item => item === oldSession ? newSession : item);
  stopSessionUi(oldSession);
  replaceSessionMetadata(oldSession, newSession);
  setSessionOrder(next);
  if (focusedTerminal === oldSession) focusedTerminal = newSession;
  if (focusedPanelItem === oldSession) focusedPanelItem = newSession;
  if (lastFocusedTmuxSession === oldSession) lastFocusedTmuxSession = newSession;
  applyLayoutSlots(layoutWithReplacedItem(oldSession, newSession), {focusSession: newSession, prune: false});
}

function closeSessionRenameDialog() {
  if (!sessionRenameDialog) return;
  sessionRenameDialog.remove();
  sessionRenameDialog = null;
  document.removeEventListener('keydown', sessionRenameDialogKeydown, true);
}

function sessionRenameDialogKeydown(event) {
  if (event.key === 'Escape') closeSessionRenameDialog();
}

function showSessionRenameDialog(session) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot rename sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  closeContextMenus();
  closeAppMenus();
  closeSessionRenameDialog();
  const overlay = document.createElement('div');
  overlay.className = 'session-rename-backdrop';
  overlay.setAttribute('role', 'presentation');
  overlay.innerHTML = `
    <form class="session-rename-dialog" role="dialog" aria-modal="true" aria-label="Rename tmux session">
      <div class="session-rename-title">Rename ${esc(sessionLabel(session))} ${esc(session)}</div>
      <input class="session-rename-input" name="sessionName" value="${esc(session)}" aria-label="New session name" autocomplete="off">
      <div class="session-rename-error" hidden></div>
      <div class="session-rename-actions">
        <button type="button" class="session-rename-cancel">Cancel</button>
        <button type="submit" class="session-rename-submit">Rename</button>
      </div>
    </form>`;
  const form = overlay.querySelector('form');
  const input = overlay.querySelector('.session-rename-input');
  const errorNode = overlay.querySelector('.session-rename-error');
  const cancel = overlay.querySelector('.session-rename-cancel');
  const showError = message => {
    errorNode.textContent = message;
    errorNode.hidden = false;
  };
  overlay.addEventListener('pointerdown', event => {
    if (event.target === overlay) closeSessionRenameDialog();
  });
  cancel.addEventListener('click', closeSessionRenameDialog);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const nextName = input.value.trim();
    const nameError = tmuxSessionNameError(nextName);
    if (nameError) {
      showError(nameError);
      input.focus();
      return;
    }
    errorNode.hidden = true;
    const renamed = await renameTmuxSession(session, nextName);
    if (!renamed) {
      showError('rename failed; see status line');
      input.focus();
    }
  });
  document.body.appendChild(overlay);
  sessionRenameDialog = overlay;
  document.addEventListener('keydown', sessionRenameDialogKeydown, true);
  setTimeout(() => {
    input.focus();
    input.select();
  }, 0);
  return true;
}

async function renameTmuxSession(session, proposedName) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot rename sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (proposedName === undefined) return showSessionRenameDialog(session);
  const rawName = proposedName;
  const newName = String(rawName || '').trim();
  const nameError = tmuxSessionNameError(newName);
  if (nameError) {
    statusEl.innerHTML = `<span class="err">${esc(nameError)}</span>`;
    return false;
  }
  if (newName === session) {
    closeSessionRenameDialog();
    return true;
  }
  statusEl.textContent = `renaming ${sessionLabel(session)}...`;
  try {
    const response = await apiFetch(`/api/rename-session?session=${encodeURIComponent(session)}&new_name=${encodeURIComponent(newName)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session rename failed')}</span>`;
      return false;
    }
    const renamed = payload.new_session || newName;
    replaceTmuxSessionInClient(session, renamed, payload.sessions);
    closeSessionRenameDialog();
    await ensureTerminalRunning(renamed);
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">renamed ${esc(session)} to ${esc(renamed)}</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session rename failed: ${esc(error)}</span>`;
    return false;
  }
}

async function killTmuxSession(session) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot kill sessions</span>';
    return false;
  }
  if (!isTmuxSession(session)) return false;
  if (!window.confirm(`Kill tmux session ${sessionLabel(session)}?`)) return false;
  statusEl.textContent = `killing ${sessionLabel(session)}...`;
  try {
    const response = await apiFetch(`/api/kill-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session kill failed')}</span>`;
      return false;
    }
    const previousActive = activeSessions.slice();
    stopSessionUi(session);
    const sessionsChanged = updateSessionList(payload.sessions || []);
    autoApproveStates.delete(session);
    renderSessionButtons();
    renderPanels(previousActive);
    if (sessionsChanged) renderPaneTabStrips();
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">killed ${esc(sessionLabel(session))}</span>`;
    return true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session kill failed: ${esc(error)}</span>`;
    return false;
  }
}

function focusPanel(session) {
  const panel = document.getElementById(`panel-${session}`);
  if (!panel) return;
  panel.scrollIntoView({block: 'nearest', inline: 'nearest'});
  if (isVirtualItem(session)) {
    focusedTerminal = null;
    setFocusedPanelItem(session);
    return;
  }
  activateTab(session, 'terminal');
  setFocusedTerminal(session);
  setTimeout(() => terminals.get(session)?.term?.focus?.(), 25);
}

function fitTerminal(session) {
  const item = terminals.get(session);
  if (!item || !item.term || !item.container) return;
  if (!terminalIsVisible(session, item.container)) return;
  const size = estimateTerminalSize(item.container, item.term);
  const changed = item.term.cols !== size.cols || item.term.rows !== size.rows;
  item.term.resize(size.cols, size.rows);
  if (changed) scheduleRemoteResize(session);
  refreshTerminal(session);
}

function sendRemoteResize(session) {
  const item = terminals.get(session);
  if (!item?.term || item?.socket?.readyState !== WebSocket.OPEN) return;
  item.socket.send(JSON.stringify({type: 'resize', cols: item.term.cols, rows: item.term.rows}));
}

function scheduleRemoteResize(session, delay = remoteResizeDelayMs) {
  const item = terminals.get(session);
  if (!item) return;
  if (item.resizeTimer) clearTimeout(item.resizeTimer);
  item.resizeTimer = setTimeout(() => {
    item.resizeTimer = null;
    sendRemoteResize(session);
  }, delay);
}

function refreshTerminal(session) {
  const item = terminals.get(session);
  if (!item?.term) return;
  requestAnimationFrame(() => {
    try { item.term.refresh(0, Math.max(0, item.term.rows - 1)); } catch (_) {}
  });
}

function terminalIsVisible(session, container) {
  const pane = document.getElementById(`terminal-pane-${session}`);
  return Boolean(
    pane?.classList.contains('active')
    && container.clientWidth > 40
    && container.clientHeight > 40
  );
}

function scheduleFit(session) {
  requestAnimationFrame(() => fitTerminal(session));
  setTimeout(() => fitTerminal(session), 80);
  setTimeout(() => fitTerminal(session), 250);
}

function observeTerminalResize(session, container) {
  const oldObserver = resizeObservers.get(session);
  if (oldObserver) oldObserver.disconnect();
  if (!window.ResizeObserver) return;
  const observer = new ResizeObserver(() => scheduleFit(session));
  observer.observe(container);
  resizeObservers.set(session, observer);
}

function enableTerminalScroll(session, term, container) {
  container.addEventListener('wheel', event => {
    if (event.ctrlKey && event.deltaY !== 0) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const signedLines = terminalWheelSignedLines(event, term.rows);
    if (!signedLines) return;
    event.preventDefault();
    event.stopPropagation();
    const item = terminals.get(session);
    if (!readOnlyMode && item?.socket?.readyState === WebSocket.OPEN) {
      queueTmuxScroll(item, signedLines);
      return;
    }
    queueLocalTerminalScroll(term, signedLines);
  }, {capture: true, passive: false});
}

function terminalWheelSignedLines(event, rows = 0) {
  const deltaY = Number(event?.deltaY);
  if (!Number.isFinite(deltaY) || deltaY === 0 || event?.ctrlKey) return 0;
  const direction = deltaY < 0 ? -1 : 1;
  const pageLines = Math.max(1, Math.floor((Number(rows) || 0) * terminalWheelPageFraction));
  if (event?.shiftKey) return direction * pageLines;
  const magnitude = Math.abs(deltaY);
  let lines;
  if (event?.deltaMode === 1) lines = magnitude;
  else if (event?.deltaMode === 2) lines = magnitude * pageLines;
  else lines = magnitude / terminalWheelPixelLinePx;
  return direction * Math.min(terminalWheelMaxLinesPerEvent, lines);
}

function queueTmuxScroll(item, signedLines) {
  item.pendingScrollLines = (item.pendingScrollLines || 0) + signedLines;
  if (item.scrollTimer) return;
  item.scrollTimer = setTimeout(() => {
    item.scrollTimer = null;
    const signed = item.pendingScrollLines || 0;
    item.pendingScrollLines = 0;
    if (!signed || item.socket.readyState !== WebSocket.OPEN) return;
    const direction = signed < 0 ? 'up' : 'down';
    const lines = Math.max(1, Math.min(80, Math.ceil(Math.abs(signed))));
    item.socket.send(JSON.stringify({type: 'tmux-scroll', direction, lines}));
  }, 30);
}

function queueLocalTerminalScroll(term, signedLines) {
  term.pendingWheelScrollLines = (term.pendingWheelScrollLines || 0) + signedLines;
  if (term.wheelScrollTimer) return;
  term.wheelScrollTimer = setTimeout(() => {
    term.wheelScrollTimer = null;
    const signed = term.pendingWheelScrollLines || 0;
    term.pendingWheelScrollLines = 0;
    if (!signed) return;
    const direction = signed < 0 ? -1 : 1;
    const lines = Math.max(1, Math.min(80, Math.ceil(Math.abs(signed))));
    term.scrollLines(direction * lines);
  }, 30);
}

function closeTerminalItem(session, item) {
  item.manualClose = true;
  if (item.reconnectTimer) clearTimeout(item.reconnectTimer);
  if (item.resizeTimer) clearTimeout(item.resizeTimer);
  if (item.scrollTimer) clearTimeout(item.scrollTimer);
  const observer = resizeObservers.get(session);
  if (observer) {
    observer.disconnect();
    resizeObservers.delete(session);
  }
  try { item.socket.close(); } catch (_) {}
  try { item.term.dispose(); } catch (_) {}
}

function scheduleTerminalReconnect(session, item) {
  if (item.manualClose || terminals.get(session) !== item || !activeSessions.includes(session)) return;
  const delay = Math.min(8000, 1000 * 2 ** item.reconnectAttempt);
  item.reconnectAttempt += 1;
  if (item.reconnectTimer) clearTimeout(item.reconnectTimer);
  statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} disconnected; reconnecting in ${Math.round(delay / 1000)}s</span>`;
  item.reconnectTimer = setTimeout(() => {
    if (item.manualClose || terminals.get(session) !== item || !activeSessions.includes(session)) return;
    startTerminal(session);
  }, delay);
}

function estimateTerminalSize(container, term = null) {
  const content = terminalContentSize(container);
  const measured = term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || term?._core?._renderService?.dimensions?.css?.cell
    || null;
  if (measured?.width && measured?.height) {
    return {
      cols: Math.max(40, Math.floor((content.width - 2) / measured.width)),
      rows: Math.max(10, Math.floor((content.height - terminalFitBottomReservePx) / measured.height)),
    };
  }
  const probe = document.createElement('span');
  probe.textContent = 'W';
  probe.style.position = 'absolute';
  probe.style.visibility = 'hidden';
  probe.style.font = '13px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
  document.body.appendChild(probe);
  const rect = probe.getBoundingClientRect();
  probe.remove();
  const charWidth = Math.max(7, rect.width || 8);
  const charHeight = Math.max(14, rect.height || 16);
  return {
    cols: Math.max(40, Math.floor((content.width - 2) / charWidth)),
    rows: Math.max(10, Math.floor((content.height - terminalFitBottomReservePx) / charHeight)),
  };
}

function terminalContentSize(container) {
  const style = getComputedStyle(container);
  const horizontalPadding = px(style.paddingLeft) + px(style.paddingRight);
  const verticalPadding = px(style.paddingTop) + px(style.paddingBottom);
  return {
    width: Math.max(0, container.clientWidth - horizontalPadding),
    height: Math.max(0, container.clientHeight - verticalPadding),
  };
}

function px(value) {
  const number = Number.parseFloat(value);
  return Number.isFinite(number) ? number : 0;
}

function slotSide(slot) {
  return slotColumn(slot);
}

function slotForItem(item, slots = layoutSlots) {
  return layoutSlotKeys(slots).find(side => paneTabs(side, slots).includes(item)) || null;
}

function slotForSession(session) {
  return slotForItem(session);
}

function slotForDropEvent(event) {
  const rect = grid.getBoundingClientRect();
  return event.clientX < rect.left + rect.width / 2 ? 'left' : 'right';
}

function dropZoneForRect(event, rect) {
  if (!rect.width || !rect.height) return 'middle';
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (y < 0.24) return rect.height / 2 >= minSplitPaneHeightPx ? 'top' : 'middle';
  if (y > 0.76) return rect.height / 2 >= minSplitPaneHeightPx ? 'bottom' : 'middle';
  if (x < 0.24) return rect.width / 2 >= minSplitPaneWidthPx ? 'left' : 'middle';
  if (x > 0.76) return rect.width / 2 >= minSplitPaneWidthPx ? 'right' : 'middle';
  return 'middle';
}

function dropIntentForEvent(event) {
  const slotNode = event.target.closest('.drop-slot');
  if (slotNode?.dataset.slot) {
    const targetSlot = slotNode.dataset.slot;
    return {targetSlot, zone: dropZoneForRect(event, slotNode.getBoundingClientRect()), previewNode: slotNode};
  }
  return {targetSlot: slotForDropEvent(event), zone: 'middle', previewNode: null};
}

function slotIsFileExplorerPane(slot) {
  return isFileExplorerItem(activeItemForSide(slot));
}

function dropIntentAllowsSession(session, intent) {
  if (!isLayoutItem(session) || !intent?.targetSlot) return false;
  if (!slotIsFileExplorerPane(intent.targetSlot)) return true;
  return intent.zone === 'top' || intent.zone === 'bottom';
}

function clearDropPreview() {
  grid.querySelectorAll('.drag-over, .tab-drag-over, .tab-drop-preview, .drop-preview, .drop-preview-top, .drop-preview-bottom, .drop-preview-left, .drop-preview-right, .drop-preview-middle').forEach(node => {
    node.classList.remove('drag-over', 'tab-drag-over', 'tab-drop-preview', 'drop-preview', 'drop-preview-top', 'drop-preview-bottom', 'drop-preview-left', 'drop-preview-right', 'drop-preview-middle');
    node.style?.removeProperty('--tab-drop-x');
    node.style?.removeProperty('--tab-drop-y');
    node.style?.removeProperty('--tab-drop-height');
    if (node.dataset) delete node.dataset.dropLabel;
  });
}

function showDropPreview(intent) {
  clearDropPreview();
  const node = intent?.previewNode;
  if (!node) return;
  const zone = intent.zone || 'middle';
  node.classList.add('drag-over', 'drop-preview', `drop-preview-${zone}`);
  node.dataset.dropLabel = zone === 'middle' ? 'take over' : zone;
}

function dropSessionAtEvent(event) {
  const payload = dragPayload(event);
  if (!payload?.session) return;
  if (event.target.closest('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    clearDropPreview();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const intent = dropIntentForEvent(event);
  clearDropPreview();
  if (!dropIntentAllowsSession(payload.session, intent)) return;
  dropSessionWithIntent(payload.session, intent, payload.sourceSlot || slotForSession(payload.session));
}

function handleDropDragOver(event) {
  const payload = dragPayload(event);
  if (!payload?.session) return;
  if (event.target.closest('.panel-head')) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  const intent = dropIntentForEvent(event);
  event.preventDefault();
  event.stopPropagation();
  if (!dropIntentAllowsSession(payload.session, intent)) {
    event.dataTransfer.dropEffect = 'none';
    clearDropPreview();
    return;
  }
  event.dataTransfer.dropEffect = 'move';
  showDropPreview(intent);
}

function handleDropDragLeave(event) {
  const current = event.currentTarget;
  if (current?.contains(event.relatedTarget)) return;
  clearDropPreview();
}

function renderPanels(previousActive = [], options = {}) {
  movePanelsToPool();
  const activePaneCount = layoutSlotKeys().filter(side => activeItemForSide(side) || paneIsPlaceholder(side)).length;
  grid.className = `grid ${activePaneCount === 1 ? 'full' : ''} ${activePaneCount === 0 ? 'empty' : ''}`.trim();
  grid.innerHTML = '';
  const tree = layoutSlots[layoutTreeKey];
  if (tree) grid.appendChild(renderLayoutRoot(tree));

  bindDropTargets();
  syncPanelVisibility(previousActive);
  renderAutoApproveButtons();
  if (options.prune === false) {
    if (responsiveLayoutPruneTimer) {
      clearTimeout(responsiveLayoutPruneTimer);
      responsiveLayoutPruneTimer = null;
    }
  } else {
    scheduleResponsiveLayoutPrune();
  }
}

function movePanelsToPool() {
  for (const panel of panelNodes.values()) {
    panel.classList.remove('active-pane');
    panel.dataset.slot = '';
    panelPool.appendChild(panel);
  }
}

function bindDropTargets() {
  grid.ondragover = handleDropDragOver;
  grid.ondragleave = handleDropDragLeave;
  grid.ondrop = dropSessionAtEvent;
  grid.querySelectorAll('[data-side], [data-slot]').forEach(node => {
    node.addEventListener('dragover', handleDropDragOver);
    node.addEventListener('dragleave', handleDropDragLeave);
    node.addEventListener('drop', dropSessionAtEvent);
  });
}

function renderLayoutRoot(node) {
  const section = document.createElement('section');
  section.className = 'layout-root';
  section.appendChild(renderLayoutNode(node, ''));
  return section;
}

function renderLayoutNode(node, path) {
  if (node.slot) return renderLayoutColumn(node.slot);
  const section = document.createElement('section');
  section.className = `layout-split ${node.split === 'column' ? 'split-column' : 'split-row'}`;
  section.dataset.splitPath = path;
  const children = node.children || [];
  const first = renderLayoutNode(children[0], layoutChildPath(path, 0));
  const second = renderLayoutNode(children[1], layoutChildPath(path, 1));
  const handle = document.createElement('div');
  handle.className = `layout-resizer ${node.split === 'column' ? 'resizer-column' : 'resizer-row'}`;
  handle.role = 'separator';
  handle.tabIndex = 0;
  handle.dataset.splitPath = path;
  handle.setAttribute('aria-orientation', node.split === 'column' ? 'horizontal' : 'vertical');
  handle.setAttribute('aria-label', 'Resize panes');
  section.append(first, handle, second);
  applySplitPercentToSection(section, node.pct);
  bindLayoutResizer(handle, section, path);
  return section;
}

function layoutChildPath(path, index) {
  return path ? `${path}.${index}` : String(index);
}

function layoutNodeAtPath(path, root = layoutSlots[layoutTreeKey]) {
  let node = root;
  if (!path) return node;
  for (const part of String(path).split('.')) {
    const index = Number(part);
    if (!node?.children || !Number.isInteger(index)) return null;
    node = node.children[index];
  }
  return node || null;
}

function applySplitPercentToSection(section, pct) {
  const first = section.children[0];
  const second = section.children[2];
  if (!first || !second) return;
  const value = splitPercent(pct);
  first.style.flex = `0 1 ${value}%`;
  second.style.flex = `1 1 ${100 - value}%`;
  const handle = section.children[1];
  if (handle?.style) handle.style.setProperty('--split-percent', `${value}%`);
}

function bindLayoutResizer(handle, section, path) {
  handle.addEventListener('pointerdown', event => {
    const node = layoutNodeAtPath(path);
    if (!node || !node.children) return;
    event.preventDefault();
    event.stopPropagation();
    layoutResizeState = {section, path, pointerId: event.pointerId};
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add('layout-resizing', node.split === 'column' ? 'layout-resizing-column' : 'layout-resizing-row');
    window.addEventListener('pointermove', onLayoutResizeMove, {capture: true});
    window.addEventListener('pointerup', onLayoutResizeEnd, {capture: true});
    onLayoutResizeMove(event);
  });
}

function onLayoutResizeMove(event) {
  const state = layoutResizeState;
  if (!state) return;
  event.preventDefault();
  const node = layoutNodeAtPath(state.path);
  if (!node || !node.children) return;
  const pct = splitPercentForPointer(state.section, node.split, event);
  node.pct = pct;
  applySplitPercentToSection(state.section, pct);
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
}

function onLayoutResizeEnd(event) {
  if (!layoutResizeState) return;
  event.preventDefault();
  const state = layoutResizeState;
  const handle = state.section.querySelector(`:scope > .layout-resizer[data-split-path="${cssEscape(state.path)}"]`);
  try { handle?.releasePointerCapture?.(state.pointerId); } catch (_) {}
  layoutResizeState = null;
  document.body.classList.remove('layout-resizing', 'layout-resizing-column', 'layout-resizing-row');
  window.removeEventListener('pointermove', onLayoutResizeMove, {capture: true});
  window.removeEventListener('pointerup', onLayoutResizeEnd, {capture: true});
  updateActiveSessionParam();
  scheduleResponsiveLayoutPrune();
}

function splitPercentForPointer(section, direction, event) {
  const rect = section.getBoundingClientRect();
  const size = direction === 'column' ? rect.height : rect.width;
  if (!size) return defaultSplitPercent;
  const offset = direction === 'column' ? event.clientY - rect.top : event.clientX - rect.left;
  const raw = (offset / size) * 100;
  const minPx = direction === 'column' ? minSplitPaneHeightPx : minSplitPaneWidthPx;
  if (size >= minPx * 2) {
    const minPct = (minPx / size) * 100;
    return Math.min(100 - minPct, Math.max(minPct, raw));
  }
  return splitPercent(raw);
}

function scheduleResponsiveLayoutPrune() {
  if (layoutResizeState || responsiveLayoutPruneTimer) return;
  responsiveLayoutPruneTimer = setTimeout(() => {
    responsiveLayoutPruneTimer = null;
    requestAnimationFrame(pruneSmallLayoutSlots);
  }, 80);
}

function pruneSmallLayoutSlots() {
  if (layoutResizeState || activeSessions.length <= 1) return;
  const candidate = smallLayoutSlotCandidate();
  if (!candidate) return;
  const moved = paneTabs(candidate.slot);
  applyLayoutSlots(layoutWithoutSlot(candidate.slot), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} hidden from layout: not enough room` : '',
  });
}

function minWidthForLayoutSlot(slot) {
  const item = activeItemForSide(slot);
  if (isFileExplorerItem(item)) return 260;
  if (isFileEditorItem(item)) return 320;
  return minSplitPaneWidthPx;
}

function prunePriorityForLayoutSlot(slot) {
  const item = activeItemForSide(slot);
  if (isFileExplorerItem(item) || isInfoItem(item)) return 0;
  if (isFileEditorItem(item)) return 1;
  return 2;
}

function slotCanAutoPrune(slot) {
  return !isFileExplorerItem(activeItemForSide(slot));
}

function smallLayoutSlotCandidate() {
  let candidate = null;
  let virtualCandidate = null;
  for (const column of grid.querySelectorAll('.layout-column[data-slot]')) {
    const slot = column.dataset.slot;
    if (!slot || !paneTabs(slot).length) continue;
    if (!slotCanAutoPrune(slot)) continue;
    const rect = column.getBoundingClientRect();
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    const priority = prunePriorityForLayoutSlot(slot);
    const item = activeItemForSide(slot);
    if (isVirtualItem(item) && (!virtualCandidate || area < virtualCandidate.area)) {
      virtualCandidate = {slot, area, priority};
    }
    const tooSmall = rect.width < minWidthForLayoutSlot(slot) || rect.height < minSplitPaneHeightPx;
    if (!tooSmall) continue;
    const nextCandidate = {slot, area, priority};
    if (!candidate || priority < candidate.priority || (priority === candidate.priority && area < candidate.area)) {
      candidate = nextCandidate;
    }
  }
  if (candidate && prunePriorityForLayoutSlot(candidate.slot) >= 2 && virtualCandidate) return virtualCandidate;
  return candidate;
}

function renderLayoutColumn(side) {
  const column = document.createElement('section');
  const session = activeItemForSide(side);
  column.className = 'layout-column';
  if (isFileExplorerItem(session)) column.classList.add('file-explorer-column');
  if (isFileEditorItem(session)) column.classList.add('file-editor-column');
  if (!session) column.classList.add('empty-pane-column');
  column.dataset.slot = side;
  column.dataset.side = slotSide(side);
  column.appendChild(renderDropSlot(side, session));
  return column;
}

function renderDropSlot(slot, session) {
  const node = document.createElement('section');
  node.className = 'drop-slot';
  node.dataset.slot = slot;
  node.dataset.side = slotSide(slot);
  if (!session) {
    node.appendChild(renderEmptyPane(slot));
    return node;
  }
  const panel = getOrCreatePanel(session);
  updatePanelSlot(panel, session, slot);
  node.appendChild(panel);
  return node;
}

function renderEmptyPane(slot) {
  const panel = document.createElement('article');
  panel.className = 'panel empty-pane-panel';
  panel.dataset.slot = slot;
  panel.setAttribute('aria-label', 'Empty pane');
  panel.appendChild(document.createElement('div'));
  panel.children[0].className = 'empty-pane-fill';
  return panel;
}

function renderPaneTabStrips() {
  for (const side of layoutSlotKeys()) {
    const session = activeItemForSide(side);
    if (!session) continue;
    const panel = panelNodes.get(session);
    if (panel) {
      updatePaneExpandButton(panel, session);
      updatePaneTabStrip(panel, side);
    }
  }
}

function updatePaneTabStrip(panel, side) {
  const strip = panel.querySelector('.pane-tabs');
  if (!strip) return;
  const stack = paneTabs(side);
  strip.dataset.side = side;
  if (isFileExplorerItem(activeItemForSide(side))) {
    strip.hidden = true;
    strip.replaceChildren();
    return;
  }
  strip.hidden = false;
  const restorePopoverItem = paneTabPopoverItemToRestore(strip);
  const activeItem = activeItemForSide(side);
  const children = stack.map(item => createPaneTab(side, item));
  if (activeItem && !stack.includes(activeItem)) children.push(createPaneTab(side, activeItem));
  strip.replaceChildren(...children);
  bindPaneTabStrip(strip, side);
  restorePaneTabPopover(strip, restorePopoverItem);
  scheduleTabStripOverflowCheck(strip);
}

function paneTabPopoverItemToRestore(strip) {
  for (const tab of strip.querySelectorAll(':scope > .pane-tab')) {
    const popover = tab.querySelector(':scope > .session-popover');
    const openOrHovered = tab.classList.contains('popover-open') || tab.matches(':hover');
    if (openOrHovered && popoverStillActive(tab, popover)) return tab.dataset.paneTab || null;
  }
  return null;
}

function restorePaneTabPopover(strip, item) {
  if (!item) return;
  const tab = strip.querySelector(`:scope > .pane-tab[data-pane-tab="${cssEscape(item)}"]`);
  const popover = tab?.querySelector(':scope > .session-popover');
  if (!tab || !popover) return;
  positionPaneTabPopover(tab);
  closeOtherSessionPopovers(tab);
  tab.classList.add('popover-open');
}

function createPaneTab(side, item) {
  const isInfo = isInfoItem(item);
  const isFiles = isFileExplorerItem(item);
  const isEditor = isFileEditorItem(item);
  const isVirtual = isInfo || isFiles || isEditor;
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true && !isVirtual;
  const state = isVirtual ? null : sessionState(item, info);
  const agentKind = isVirtual ? '' : sessionAgentKind(item);
  const active = item === activeItemForSide(side);
  const tab = document.createElement('div');
  tab.role = 'button';
  tab.tabIndex = 0;
  const virtualClass = isInfo ? 'info' : isFiles ? 'file-explorer' : isEditor ? 'file-editor-item' : '';
  tab.className = `pane-tab ${virtualClass} ${active ? 'active' : ''}`;
  applySessionStateClasses(tab, state);
  tab.draggable = true;
  tab.dataset.paneTab = item;
  if (isInfo) tab.innerHTML = paneInfoTabHtml();
  else if (isFiles) tab.innerHTML = fileExplorerPaneTabHtml();
  else if (isEditor) tab.innerHTML = fileEditorPaneTabHtml(item);
  else tab.innerHTML = tmuxPaneTabHtml(item, info, state, auto);
  if (!isFiles) {
    const closeTitle = isEditor ? `Close ${itemLabel(item)}` : `hide ${itemLabel(item)} from layout`;
    const closeLabel = isEditor ? `Close ${itemLabel(item)}` : `Hide ${itemLabel(item)} from layout`;
    const controlKind = isEditor ? 'close' : 'minimize';
    tab.insertAdjacentHTML('beforeend', `<button type="button" class="pane-tab-close ${platformWindowControlClass(controlKind)}" data-pane-tab-close title="${esc(closeTitle)}" aria-label="${esc(closeLabel)}"></button>`);
  }
  if (isEditor) {
    tab.insertAdjacentHTML('beforeend', filePopoverHtml(item));
    bindFilePopoverActions(tab);
    bindPaneTabPopover(tab, item);
  } else if (!isVirtual) {
    tab.insertAdjacentHTML('beforeend', sessionPopoverHtml(item, info, agentKind, auto, state));
    bindPaneTabPopover(tab, item);
  }
  tab.setAttribute('aria-label', isInfo ? 'Branch Info' : isFiles ? fileExplorerLabel() : isEditor ? itemLabel(item) : `${sessionLabel(item)} ${sessionWorkDescription(item, info, 140)}`.trim());
  tab.addEventListener('pointerdown', event => {
    if (event.target.closest('[data-pane-tab-close]')) {
      event.stopPropagation();
      return;
    }
    const autoTarget = event.target.closest('[data-auto-session]');
    if (!autoTarget) return;
    event.preventDefault();
    event.stopPropagation();
    if (item === activeItemForSide(side)) setFocusedPanelItem(item);
  });
  tab.addEventListener('click', async event => {
    if (event.target.closest('[data-pane-tab-close]')) {
      event.preventDefault();
      event.stopPropagation();
      if (isEditor) closeFileTab(fileItemPath(item));
      else removeSessionFromLayout(item);
      return;
    }
    const autoTarget = event.target.closest('[data-auto-session]');
    if (autoTarget) {
      event.preventDefault();
      event.stopPropagation();
      const shouldRefocus = item === activeItemForSide(side);
      await toggleAutoApprove(autoTarget.dataset.autoSession);
      if (shouldRefocus) focusPanel(item);
      return;
    }
  });
  tab.addEventListener('keydown', event => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    activatePaneTab(side, item);
  });
  bindTabActivation(tab, () => activatePaneTab(side, item), {
    stopPropagation: true,
    ignore: event => Boolean(event.target.closest('[data-auto-session], [data-pane-tab-close]')),
  });
  if (!isVirtual) {
    tab.addEventListener('dblclick', event => {
      if (event.target.closest('[data-auto-session], [data-pane-tab-close]')) return;
      event.preventDefault();
      event.stopPropagation();
      beginPaneTabRename(tab, item);
    });
    tab.addEventListener('contextmenu', event => {
      event.preventDefault();
      event.stopPropagation();
      showSessionContextMenu(item, event.clientX, event.clientY, {tab});
    });
  }
  tab.addEventListener('dragstart', event => {
    event.stopPropagation();
    startSessionDrag(event, item, side);
  });
  tab.addEventListener('dragend', endSessionDrag);
  return tab;
}

function beginPaneTabRename(tab, session) {
  renameTmuxSession(session);
}

function bindPaneTabPopover(tab, session) {
  const popover = tab.querySelector?.(':scope > .session-popover');
  if (!popover) return;
  bindDelayedSessionPopover(tab, popover, () => positionPaneTabPopover(tab));
}

function bindDelayedSessionPopover(anchor, popover, position) {
  let showTimer = null;
  let hideTimer = null;
  const clearShowTimer = () => {
    showTimer = clearTimer(showTimer);
  };
  const clearHideTimer = () => {
    hideTimer = clearTimer(hideTimer);
  };
  const openNow = () => {
    clearShowTimer();
    clearHideTimer();
    if (anchor.isConnected === false) return;
    position();
    closeOtherSessionPopovers(anchor);
    anchor.classList.add('popover-open');
  };
  const queueOpen = () => {
    clearHideTimer();
    if (anchor.isConnected === false) return;
    if (anchor.classList.contains('popover-open')) return;
    clearShowTimer();
    position();
    showTimer = setTimeout(openNow, popoverShowDelayMs);
  };
  const closeSoon = () => {
    clearShowTimer();
    clearHideTimer();
    hideTimer = setTimeout(() => {
      if (anchor.isConnected === false) {
        hideTimer = null;
        return;
      }
      if (popoverStillActive(anchor, popover)) {
        hideTimer = null;
        return;
      }
      anchor.classList.remove('popover-open');
      hideTimer = null;
    }, popoverHideDelayMs);
  };
  bindPopoverHover(anchor, popover, {queueOpen, keepOpen: openNow, closeSoon});
}

function positionPaneTabPopover(tab) {
  const rect = tab.getBoundingClientRect();
  const popover = tab.querySelector?.(':scope > .session-popover');
  const viewportWidth = Math.max(0, window.innerWidth || document.documentElement?.clientWidth || 0);
  const edgeGap = popoverEdgeGapPx();
  const viewportLeft = edgeGap;
  const viewportRight = Math.max(viewportLeft, viewportWidth - edgeGap);
  const width = Math.ceil(popover?.getBoundingClientRect?.().width || rect.width || 0);
  const maxLeft = Math.max(viewportLeft, viewportRight - width);
  const left = Math.min(Math.max(viewportLeft, Math.floor(rect.left)), maxLeft);
  document.documentElement.style.setProperty('--pane-tab-popover-top', `${Math.ceil(rect.bottom)}px`);
  document.documentElement.style.setProperty('--pane-tab-popover-left', `${left}px`);
}

function paneInfoTabHtml() {
  return '<span class="pane-tab-core"><span class="pane-tab-info-label">Branch Info</span></span>';
}

function fileExplorerPaneTabHtml() {
  return `<span class="pane-tab-core"><span class="session-button-dir">${esc(fileExplorerLabel())}</span></span>`;
}

function fileEditorPaneTabHtml(item) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const dirty = state.dirty ? '<span class="file-tab-dirty" title="modified" aria-label="modified"></span>' : '';
  return `<span class="pane-tab-core"><span class="session-button-text">${dirty}<span class="session-button-dir">${esc(basenameOf(path))}</span></span></span>`;
}

function tmuxPaneTabHtml(session, info, state, auto) {
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(session, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="pane-tab-core">${yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: true, yoloWorking: sessionYoloIsWorking(session)})}<span class="session-button-prefix">${sessionNumberNameHtml(session)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(session, info)}${pullRequestCompactBadgesHtml(session, pr)}${detailHtml}</span></span>`;
}

function bindPaneTabStrip(strip, side) {
  strip.ondragover = event => {
    const payload = dragPayload(event);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (slotIsFileExplorerPane(side)) {
      event.dataTransfer.dropEffect = 'none';
      clearPaneTabDropPreview(strip);
      return;
    }
    event.dataTransfer.dropEffect = 'move';
    clearDropPreview();
    showPaneTabDropPreview(strip, event, payload.session);
  };
  strip.ondragleave = event => {
    event.stopImmediatePropagation();
    if (!strip.contains(event.relatedTarget)) clearPaneTabDropPreview(strip);
  };
  strip.ondrop = event => {
    const payload = dragPayload(event);
    clearPaneTabDropPreview(strip);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (slotIsFileExplorerPane(side)) return;
    moveSessionToSlot(payload.session, side, payload.sourceSlot || slotForSession(payload.session), paneTabDropIndex(strip, event, payload.session));
  };
}

function showPaneTabDropPreview(strip, event, movingSession) {
  const placement = paneTabDropPlacement(strip, event, movingSession);
  strip.style.setProperty('--tab-drop-x', `${Math.round(placement.x)}px`);
  strip.style.setProperty('--tab-drop-y', `${Math.round(placement.y)}px`);
  strip.style.setProperty('--tab-drop-height', `${Math.round(placement.height)}px`);
  strip.classList.add('drag-over', 'tab-drop-preview');
}

function clearPaneTabDropPreview(strip) {
  strip.classList.remove('drag-over', 'tab-drop-preview');
  strip.style.removeProperty('--tab-drop-x');
  strip.style.removeProperty('--tab-drop-y');
  strip.style.removeProperty('--tab-drop-height');
}

function paneTabDropPlacement(strip, event, movingSession) {
  const tabs = Array.from(strip.querySelectorAll('.pane-tab'))
    .filter(tab => tab.dataset.paneTab !== movingSession);
  const stripRect = strip.getBoundingClientRect();
  const clampX = value => Math.max(2, Math.min(stripRect.width - 2, value));
  const clampY = (value, height) => Math.max(0, Math.min(Math.max(0, stripRect.height - height), value));
  const defaultHeight = Math.min(32, Math.max(24, stripRect.height || 27));
  if (!tabs.length) {
    return {
      index: 0,
      x: clampX(event.clientX - stripRect.left),
      y: clampY(event.clientY - stripRect.top - defaultHeight / 2, defaultHeight),
      height: defaultHeight,
    };
  }
  const rows = paneTabRows(tabs);
  const row = rows.reduce((best, item) => {
    const distance = Math.abs(event.clientY - item.centerY);
    return !best || distance < best.distance ? {row: item, distance} : best;
  }, null)?.row || rows[0];
  const rowTabs = row.items.slice().sort((left, right) => left.rect.left - right.rect.left);
  for (const item of rowTabs) {
    const rect = item.rect;
    if (event.clientX < rect.left + rect.width / 2) {
      return {
        index: item.index,
        x: clampX(rect.left - stripRect.left),
        y: clampY(row.top - stripRect.top, row.height),
        height: row.height,
      };
    }
  }
  const last = rowTabs[rowTabs.length - 1];
  return {
    index: last.index + 1,
    x: clampX(last.rect.right - stripRect.left),
    y: clampY(row.top - stripRect.top, row.height),
    height: row.height,
  };
}

function paneTabRows(tabs) {
  const rows = [];
  tabs.forEach((tab, index) => {
    const rect = tab.getBoundingClientRect();
    const centerY = rect.top + rect.height / 2;
    const row = rows.find(item => Math.abs(centerY - item.centerY) <= Math.max(4, item.height / 2));
    const target = row || {items: [], top: rect.top, bottom: rect.bottom, centerY, height: rect.height};
    target.items.push({index, rect});
    target.top = Math.min(target.top, rect.top);
    target.bottom = Math.max(target.bottom, rect.bottom);
    target.height = Math.max(1, target.bottom - target.top);
    target.centerY = target.top + target.height / 2;
    if (!row) rows.push(target);
  });
  return rows.sort((left, right) => left.top - right.top);
}

function paneTabDropIndex(strip, event, movingSession) {
  return paneTabDropPlacement(strip, event, movingSession).index;
}

function getOrCreatePanel(session) {
  let panel = panelNodes.get(session);
  if (panel) return panel;
  if (isInfoItem(session)) panel = createInfoPanel();
  else if (isFileExplorerItem(session)) panel = createFileExplorerPanel();
  else if (isFileEditorItem(session)) panel = createFileEditorPanel(session);
  else panel = createPanel(session);
  panelNodes.set(session, panel);
  panelPool.appendChild(panel);
  return panel;
}

function bindPanelShell(panel, session) {
  installPanelInactiveOverlays(panel, session);
  bindPanelPopover(panel);
  panel.addEventListener('pointerenter', () => selectPanelOnHover(session));
  const head = panel.querySelector('.panel-head');
  if (head) {
    head.draggable = true;
    head.dataset.dragSession = session;
    head.addEventListener('dragstart', event => startSessionDrag(event, session, head.dataset.dragSlot || null));
    head.addEventListener('dragend', endSessionDrag);
    head.addEventListener('dragover', event => {
      const payload = dragPayload(event);
      if (!payload?.session) return;
      event.preventDefault();
      event.stopPropagation();
      clearDropPreview();
      if (event.target.closest('.pane-tabs')) return;
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (slotIsFileExplorerPane(targetSlot)) {
        event.dataTransfer.dropEffect = 'none';
        return;
      }
      event.dataTransfer.dropEffect = 'move';
      head.classList.add('tab-drag-over');
    });
    head.addEventListener('dragleave', event => {
      if (!head.contains(event.relatedTarget)) head.classList.remove('tab-drag-over');
    });
    head.addEventListener('drop', event => {
      const payload = dragPayload(event);
      head.classList.remove('tab-drag-over');
      if (!payload?.session || event.target.closest('.pane-tabs')) return;
      event.preventDefault();
      event.stopPropagation();
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (!targetSlot) return;
      if (slotIsFileExplorerPane(targetSlot)) return;
      if (isFileExplorerItem(payload.session)) {
        dockFileExplorerPane();
        return;
      }
      moveSessionToSlot(payload.session, targetSlot, payload.sourceSlot || slotForSession(payload.session), paneTabs(targetSlot).length);
    });
  }
  panel.querySelector('[data-detail-toggle]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
  });
}

function bindPanelPopover(panel) {
  const zone = panel.querySelector('.panel-popover-zone');
  if (!zone || zone.dataset.popoverBound === 'true') return;
  zone.dataset.popoverBound = 'true';
  bindPopoverHover(zone, zone.querySelector(':scope > .session-popover'), {
    queueOpen: () => keepPanelPopoverOpen(zone),
    keepOpen: () => keepPanelPopoverOpen(zone),
    closeSoon: () => closePanelPopoverSoon(zone),
  });
}

function keepPanelPopoverOpen(zone) {
  const timer = panelPopoverHideTimers.get(zone);
  clearTimer(timer);
  panelPopoverHideTimers.delete(zone);
  zone.classList.add('popover-open');
}

function closePanelPopoverSoon(zone) {
  const existing = panelPopoverHideTimers.get(zone);
  clearTimer(existing);
  const timer = setTimeout(() => {
    if (popoverStillActive(zone, zone.querySelector(':scope > .session-popover'))) {
      panelPopoverHideTimers.delete(zone);
      return;
    }
    zone.classList.remove('popover-open');
    panelPopoverHideTimers.delete(zone);
  }, popoverHideDelayMs);
  panelPopoverHideTimers.set(zone, timer);
}

function setPanelDetailsCollapsed(panel, collapsed) {
  panel.classList.toggle('details-collapsed', collapsed);
  const button = panel.querySelector('[data-detail-toggle]');
  if (button) {
    button.classList.toggle('active', !collapsed);
    button.title = collapsed ? 'show details' : 'hide details';
    button.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
  }
}

function terminalTabLabel(session, info) {
  if (isInfoItem(session)) return 'Term';
  if (isFileExplorerItem(session)) return fileExplorerLabel();
  if (isFileEditorItem(session)) return 'Edit';
  const label = terminalProcessLabel(info);
  return shortText(label || 'Term', 16);
}

function terminalTabTitle(session, info) {
  if (isInfoItem(session)) return 'unavailable for Branch Info';
  if (isFileExplorerItem(session)) return `unavailable for ${fileExplorerLabel()}`;
  if (isFileEditorItem(session)) return 'unavailable for file editor';
  return `terminal: ${terminalProcessLabel(info) || 'Term'}`;
}

function terminalProcessLabel(info) {
  const pane = terminalDisplayPane(info);
  if (pane?.process_label) return pane.process_label;
  const agent = agentForPane(info, pane) || info?.agents?.[0];
  if (agent?.command) return processLabelFromCommand(agent.command);
  if (agent?.kind) return agent.kind;
  if (pane?.command) return pane.command;
  return 'Term';
}

function terminalDisplayPane(info) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  return panes.find(pane => pane.window_active && pane.active)
    || panes.find(pane => pane.window_active)
    || info?.selected_pane
    || panes[0]
    || null;
}

function tmuxWindowNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function tmuxWindowIndices(panes) {
  const windows = [];
  const seen = new Set();
  for (const pane of Array.isArray(panes) ? panes : []) {
    const index = tmuxWindowNumber(pane.window);
    if (index === null || seen.has(index)) continue;
    seen.add(index);
    windows.push(index);
  }
  return windows.sort((left, right) => left - right);
}

function windowStepVisibility(panes) {
  const windows = tmuxWindowIndices(panes);
  if (windows.length <= 1) return {prev: false, next: false};
  const activeIndex = tmuxWindowNumber((Array.isArray(panes) ? panes : []).find(pane => pane.window_active)?.window) ?? windows[0];
  return {
    prev: windows.some(index => index < activeIndex),
    next: windows.some(index => index > activeIndex),
  };
}

function previewTmuxWindowInfo(info, key) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const windows = tmuxWindowIndices(panes);
  if (windows.length < 2) return null;
  const activePane = terminalDisplayPane(info);
  const activeIndex = tmuxWindowNumber(activePane?.window);
  const current = Math.max(0, windows.findIndex(index => index === activeIndex));
  const delta = key === 'p' ? -1 : 1;
  const target = windows[(current + delta + windows.length) % windows.length];
  const nextPanes = panes.map(pane => ({...pane, window_active: tmuxWindowNumber(pane.window) === target}));
  return {
    ...info,
    panes: nextPanes,
    selected_pane: nextPanes.find(pane => pane.window_active && pane.active)
      || nextPanes.find(pane => pane.window_active)
      || info?.selected_pane
      || null,
  };
}

function previewTmuxWindowLabel(session, key) {
  const info = transcriptMeta.sessions?.[session];
  const nextInfo = previewTmuxWindowInfo(info, key);
  if (!nextInfo) return;
  transcriptMeta = {
    ...transcriptMeta,
    sessions: {
      ...(transcriptMeta.sessions || {}),
      [session]: nextInfo,
    },
  };
  updatePanelControlLabels(session, nextInfo);
}

function handleWindowStepButtonClick(event) {
  const button = event.currentTarget;
  const key = button.dataset.windowDir === 'prev' ? 'p' : 'n';
  const label = button.dataset.windowDir === 'prev' ? 'previous window' : 'next window';
  tmuxWindow(button.dataset.windowSession, key, label);
}

function createWindowStepButton(session, dir) {
  const label = windowStepButtonLabel(dir);
  const button = document.createElement('button');
  button.className = 'tab tmux-window-step';
  button.dataset.windowStepButton = dir;
  button.textContent = dir === 'prev' ? '<' : '>';
  if (readOnlyMode) {
    button.type = 'button';
    button.disabled = true;
    button.title = `${label} tmux window requires admin access`;
    return button;
  }
  button.dataset.windowDir = dir;
  button.dataset.windowSession = session;
  button.title = `${label} tmux window`;
  button.addEventListener('click', handleWindowStepButtonClick);
  return button;
}

function syncWindowStepButton(controls, terminalButton, session, dir, visible) {
  const selector = `[data-window-step-button="${dir}"]`;
  const existing = controls.querySelector(selector);
  if (!visible) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const button = createWindowStepButton(session, dir);
  if (dir === 'prev') controls.insertBefore(button, terminalButton);
  else controls.insertBefore(button, terminalButton.nextSibling || null);
}

function updatePanelWindowStepButtons(session, info) {
  const controls = document.getElementById(`panel-${session}`)?.querySelector('.tabs');
  const terminalButton = controls?.querySelector('.terminal-tab');
  if (!controls || !terminalButton) return;
  const steps = windowStepVisibility(info?.panes);
  syncWindowStepButton(controls, terminalButton, session, 'prev', steps.prev);
  syncWindowStepButton(controls, terminalButton, session, 'next', steps.next);
}

function agentForPane(info, pane) {
  if (!pane || !Array.isArray(info?.agents)) return null;
  return info.agents.find(agent => agent.pane_target === pane.target) || null;
}

function processLabelFromCommand(command) {
  const tokens = String(command || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return '';
  const base = pathBasename(tokens[0]) || tokens[0];
  const lower = base.toLowerCase();
  if (lower.startsWith('python') && tokens[1] && !tokens[1].startsWith('-')) return pathBasename(tokens[1]) || tokens[1];
  if (lower === 'node' && tokens[1] && !tokens[1].startsWith('-')) return pathBasename(tokens[1]) || tokens[1];
  return base;
}

function updatePanelControlLabels(session, info) {
  const button = document.querySelector(`[data-tab="${cssEscape(session)}"][data-tab-name="terminal"]`);
  updatePanelWindowStepButtons(session, info);
  if (button) {
    button.textContent = terminalTabLabel(session, info);
    button.title = terminalTabTitle(session, info);
  }
}

function installPanelInactiveOverlays(panel, session) {
  if (isVirtualItem(session)) {
    panel.querySelectorAll('.panel-inactive-overlay').forEach(node => node.remove());
    return;
  }
  for (const root of panel.querySelectorAll('.panel-overlay-root')) {
    if (root.querySelector(':scope > .panel-inactive-overlay')) continue;
    const overlay = document.createElement('div');
    overlay.className = 'panel-inactive-overlay';
    overlay.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      focusPanel(session);
    });
    root.appendChild(overlay);
  }
}

function createInfoPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel';
  panel.id = `panel-${infoItemId}`;
  panel.innerHTML = `
      <div class="panel-head">
        ${panelControlsHtml(infoItemId, {disabled: true, unavailableLabel: 'Branch Info'})}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${infoItemId}" class="panel-session-label"><span class="session-button-dir">Branch Info</span></div>
          <div id="meta-${infoItemId}" class="meta">all branches sorted by recent activity</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(infoItemId)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div class="info-pane panel-overlay-root">
        <div id="panel-toasts-${infoItemId}" class="panel-toast-stack"></div>
        <div class="transcript-head">Branch Info</div>
        <div id="info-content" class="info-list"></div>
      </div>`;
  bindPanelShell(panel, infoItemId);
  renderInfoPanel();
  return panel;
}

// File Explorer pane content is self-contained so layout panes do not depend on
// the older left-edge overlay tree.
function createFileExplorerPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel file-explorer-panel';
  panel.id = `panel-${fileExplorerItemId}`;
  const initialPath = fileExplorerRoot || homePath || '/';
  const label = fileExplorerLabel();
  panel.innerHTML = `
      <div class="panel-head file-explorer-head">
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
        <div class="file-explorer-toolbar">
          <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel" title="Show hidden files (dotfiles)" aria-pressed="${fileExplorerShowHidden ? 'true' : 'false'}">.*</button>
          <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel" title="Root mode: fixed" aria-pressed="false">Root</button>
          <input class="file-explorer-path-inline" type="text" value="${esc(initialPath)}" spellcheck="false" aria-label="${esc(label)} root path">
          <button type="button" class="path-copy-button file-explorer-path-copy-panel" title="Copy current path" aria-label="Copy current path"></button>
          <button type="button" class="${fileExplorerPanelCloseClass()}" title="Hide ${esc(label)} from layout" aria-label="Hide ${esc(label)} from layout"></button>
        </div>
      </div>
      <div class="file-explorer-pane panel-overlay-root">
        <div id="panel-toasts-${fileExplorerItemId}" class="panel-toast-stack"></div>
        <div class="file-explorer-tree-panel" role="tree" tabindex="0"></div>
      </div>`;
  bindPanelShell(panel, fileExplorerItemId);
  const hiddenBtn = panel.querySelector('.file-explorer-hidden-toggle-panel');
  const rootModeBtn = panel.querySelector('.file-explorer-root-mode-toggle-panel');
  if (hiddenBtn) {
    hiddenBtn.classList.toggle('active', fileExplorerShowHidden);
    hiddenBtn.title = fileExplorerShowHidden ? 'Hide dotfiles (.*)' : 'Show hidden files (dotfiles)';
    hiddenBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      toggleHiddenFiles();
    });
  }
  if (rootModeBtn) {
    rootModeBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      toggleFileExplorerRootMode();
    });
  }
  const closeBtn = panel.querySelector('.file-explorer-panel-close');
  panel.querySelector('.file-explorer-path-copy-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    copyCurrentFileExplorerPath();
  });
  bindFileExplorerPathInput(panel.querySelector('.file-explorer-path-inline'));
  renderFileExplorerRootModeControls();
  if (closeBtn) {
    closeBtn.addEventListener('pointerdown', event => event.stopPropagation());
    closeBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      removeSessionFromLayout(fileExplorerItemId);
    });
  }
  refreshFileExplorerPanelTree(panel);
  return panel;
}

async function refreshFileExplorerPanelTree(panel, options = {}) {
  const treeEl = panel.querySelector('.file-explorer-tree-panel');
  const pathEl = panel.querySelector('.file-explorer-path-inline');
  const hiddenBtn = panel.querySelector('.file-explorer-hidden-toggle-panel');
  if (!treeEl) return;
  const root = normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
  setFileExplorerPathElementValue(pathEl, root);
  setFileExplorerPathError(pathEl);
  renderFileExplorerRootModeControls();
  if (hiddenBtn) {
    hiddenBtn.setAttribute('aria-pressed', fileExplorerShowHidden ? 'true' : 'false');
    hiddenBtn.classList.toggle('active', fileExplorerShowHidden);
    hiddenBtn.title = fileExplorerShowHidden ? 'Hide dotfiles (.*)' : 'Show hidden files (dotfiles)';
  }
  treeEl.replaceChildren();
  const entries = options.root === root && Array.isArray(options.entries)
    ? options.entries
    : await fetchDirectory(root);
  if (!entries) return;
  renderTreeChildren(treeEl, root, entries, 0);
  updateFileExplorerCurrentFileHighlight();
}

function createFileEditorPanel(item) {
  const path = fileItemPath(item);
  const panel = document.createElement('article');
  panel.className = 'panel file-editor-panel';
  panel.dataset.filePath = path;
  panel.innerHTML = `
      <div class="panel-head file-editor-panel-head">
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
        <div class="file-editor-panel-actions">
          <button type="button" class="file-editor-preview-panel" title="Toggle Markdown preview" hidden>Preview</button>
          <button type="button" class="file-editor-wrap-panel" title="Toggle word wrap" aria-label="Toggle word wrap" hidden><span class="file-editor-icon file-editor-icon-wrap" aria-hidden="true"></span></button>
          <button type="button" class="file-editor-reload-panel" title="Reload from disk" hidden>Reload</button>
          <button type="button" class="file-editor-save-panel" title="Save" aria-label="Save file" ${readOnlyMode ? 'hidden' : ''}><span class="file-editor-icon file-editor-icon-save" aria-hidden="true"></span></button>
          <button type="button" class="${fileEditorPanelCloseClass()}" title="Close" aria-label="Close"></button>
        </div>
      </div>
      <div class="file-editor-panel-body panel-overlay-root">
        <div id="panel-toasts-${item}" class="panel-toast-stack"></div>
        <div class="file-editor-content">
          <pre class="file-editor-highlight-panel" aria-hidden="true" hidden><code></code></pre>
          <textarea class="file-editor-textarea-panel" spellcheck="false" wrap="off"></textarea>
          <div class="file-editor-preview-pane-panel markdown-body" hidden></div>
          <div class="file-editor-image-panel" hidden></div>
        </div>
        <div class="file-editor-status-panel"></div>
      </div>`;
  bindPanelShell(panel, item);
  panel.querySelectorAll('button').forEach(button => {
    button.addEventListener('pointerdown', event => event.stopPropagation());
  });
  panel.querySelector('.file-editor-panel-close')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    closeFileTab(path);
  });
  panel.querySelector('.file-editor-save-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    saveFileEditor(path, panel);
  });
  panel.querySelector('.file-editor-reload-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    reloadOpenFileFromDisk(path);
  });
  panel.querySelector('.file-editor-preview-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    fileEditorPreviewMode.set(path, fileEditorPreviewMode.get(path) !== true);
    renderFileEditorPanel(panel, item);
  });
  panel.querySelector('.file-editor-wrap-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorWrap();
  });
  const textarea = panel.querySelector('.file-editor-textarea-panel');
  textarea?.addEventListener('input', () => {
    const state = openFiles.get(path);
    if (!state || state.kind !== 'text') return;
    state.content = textarea.value;
    const dirty = state.content !== state.original;
    const dirtyChanged = dirty !== state.dirty;
    state.dirty = dirty;
    updateFileEditorPanelChrome(panel, path);
    const status = openFileStatus(state);
    setFileEditorPanelStatus(panel, status.message, status.level);
    renderSyntaxHighlight(panel, path, state.content);
    syncSyntaxHighlightScroll(panel);
    if (dirtyChanged) {
      renderSessionButtons();
      renderPaneTabStrips();
    }
  });
  textarea?.addEventListener('scroll', () => syncSyntaxHighlightScroll(panel));
  textarea?.addEventListener('keydown', event => {
    const isSave = (event.ctrlKey || event.metaKey) && event.key === 's' && !event.shiftKey;
    if (!isSave) return;
    event.preventDefault();
    saveFileEditor(path, panel);
  });
  renderFileEditorPanel(panel, item);
  return panel;
}

function hideFileEditorContent(textarea, highlightPane, previewPane, imagePane) {
  if (textarea) textarea.hidden = true;
  if (highlightPane) highlightPane.hidden = true;
  if (previewPane) previewPane.hidden = true;
  if (imagePane) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.hidden = true;
    imagePane.replaceChildren();
  }
}

function fileEditorEmptyState(title, detail = '') {
  const node = document.createElement('div');
  node.className = 'file-editor-empty-state';
  const titleNode = document.createElement('div');
  titleNode.className = 'file-editor-empty-title';
  titleNode.textContent = title;
  node.appendChild(titleNode);
  if (detail) {
    const detailNode = document.createElement('div');
    detailNode.className = 'file-editor-empty-detail';
    detailNode.textContent = detail;
    node.appendChild(detailNode);
  }
  return node;
}

function disconnectFileEditorImageObserver(imagePane) {
  if (imagePane?._imageResizeObserver) {
    imagePane._imageResizeObserver.disconnect();
    imagePane._imageResizeObserver = null;
  }
}

function fittedFileEditorImageSize(imagePane, img) {
  const naturalWidth = img.naturalWidth || 0;
  const naturalHeight = img.naturalHeight || 0;
  if (!naturalWidth || !naturalHeight) return null;
  const availableWidth = Math.max(1, imagePane.clientWidth - 20);
  const availableHeight = Math.max(1, imagePane.clientHeight - 20);
  const scale = Math.min(1, availableWidth / naturalWidth, availableHeight / naturalHeight);
  return {
    width: Math.max(1, Math.floor(naturalWidth * scale)),
    height: Math.max(1, Math.floor(naturalHeight * scale)),
  };
}

function applyFileEditorImageMode(imagePane, img, path) {
  const original = fileEditorImageMode.get(path) === 'original';
  imagePane.classList.toggle('original-size', original);
  imagePane.classList.toggle('fit-size', !original);
  img.classList.toggle('original-size', original);
  img.classList.toggle('fit-size', !original);
  const size = original
    ? {width: img.naturalWidth || img.width, height: img.naturalHeight || img.height}
    : fittedFileEditorImageSize(imagePane, img);
  if (size?.width && size?.height) {
    img.style.width = `${size.width}px`;
    img.style.height = `${size.height}px`;
  }
  img.title = original ? 'Click to fit image' : 'Click to view original size';
}

function renderFileEditorPanel(panel, item) {
  const path = fileItemPath(item);
  activeFile = path;
  updateFileExplorerCurrentFileHighlight();
  const state = openFiles.get(path);
  updateFileEditorPanelChrome(panel, path);
  const textarea = panel.querySelector('.file-editor-textarea-panel');
  const highlightPane = panel.querySelector('.file-editor-highlight-panel');
  const previewPane = panel.querySelector('.file-editor-preview-pane-panel');
  const imagePane = panel.querySelector('.file-editor-image-panel');
  const previewButton = panel.querySelector('.file-editor-preview-panel');
  const wrapButton = panel.querySelector('.file-editor-wrap-panel');
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  if (!state) {
    if (previewButton) previewButton.hidden = true;
    if (wrapButton) wrapButton.hidden = true;
    if (reloadButton) reloadButton.hidden = true;
    panel.classList.remove('syntax-highlighted');
    hideFileEditorContent(textarea, highlightPane, previewPane, imagePane);
    setFileEditorPanelStatus(panel, 'file closed', '');
    return;
  }
  if (state.loading) {
    if (previewButton) previewButton.hidden = true;
    if (wrapButton) wrapButton.hidden = true;
    if (reloadButton) reloadButton.hidden = true;
    panel.classList.remove('syntax-highlighted');
    hideFileEditorContent(textarea, highlightPane, previewPane, imagePane);
    setFileEditorPanelStatus(panel, 'loading...', '');
    loadFileEditorState(path, panel, item);
    return;
  }
  if (state.kind === 'error' || state.kind === 'too-large') {
    if (previewButton) previewButton.hidden = true;
    if (wrapButton) wrapButton.hidden = true;
    if (reloadButton) reloadButton.hidden = true;
    panel.classList.remove('syntax-highlighted');
    if (textarea) textarea.hidden = true;
    if (highlightPane) highlightPane.hidden = true;
    if (previewPane) previewPane.hidden = true;
    if (imagePane) {
      imagePane.hidden = false;
      const limit = formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES);
      const size = formatFileSize(state.size);
      const title = state.kind === 'too-large' ? 'File is too large to preview' : 'File could not be opened';
      const detail = state.kind === 'too-large'
        ? (state.error || `${size ? `${size}; ` : ''}limit is ${limit}`)
        : String(state.error || 'failed to load file');
      imagePane.replaceChildren(fileEditorEmptyState(title, detail));
    }
    const status = state.kind === 'too-large' ? `too large; limit ${formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES)}` : state.error || 'failed to load file';
    setFileEditorPanelStatus(panel, status, 'error');
    return;
  }
  const isMarkdown = path.toLowerCase().endsWith('.md') || path.toLowerCase().endsWith('.markdown');
  if (previewButton) {
    previewButton.hidden = !(state.kind === 'text' && isMarkdown);
    previewButton.classList.toggle('active', fileEditorPreviewMode.get(path) === true);
    previewButton.textContent = fileEditorPreviewMode.get(path) ? 'Edit' : 'Preview';
  }
  if (wrapButton) {
    wrapButton.hidden = state.kind !== 'text';
    updateEditorWrapButton(wrapButton);
  }
  if (state.kind === 'image') {
    if (textarea) textarea.hidden = true;
    if (highlightPane) highlightPane.hidden = true;
    if (previewPane) previewPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    if (imagePane) {
      imagePane.hidden = false;
      disconnectFileEditorImageObserver(imagePane);
      imagePane.replaceChildren();
      const img = document.createElement('img');
      img.className = 'file-editor-image';
      const version = state.mtime || state.size || 0;
      img.src = rawFileUrl(path, {v: version});
      img.alt = path;
      applyFileEditorImageMode(imagePane, img, path);
      img.addEventListener('click', () => {
        const nextMode = fileEditorImageMode.get(path) === 'original' ? 'fit' : 'original';
        fileEditorImageMode.set(path, nextMode);
        applyFileEditorImageMode(imagePane, img, path);
      });
      const resizeObserver = new ResizeObserver(() => applyFileEditorImageMode(imagePane, img, path));
      imagePane._imageResizeObserver = resizeObserver;
      resizeObserver.observe(imagePane);
      img.onload = () => {
        applyFileEditorImageMode(imagePane, img, path);
        setFileEditorPanelStatus(panel, `${img.naturalWidth}x${img.naturalHeight}`, '');
      };
      img.onerror = () => {
        disconnectFileEditorImageObserver(imagePane);
        imagePane.replaceChildren(fileEditorEmptyState('Image could not be loaded', `The file may be unreadable, unsupported, or over ${formatFileSize(MAX_FILE_PREVIEW_BYTES)}.`));
        setFileEditorPanelStatus(panel, 'failed to load image', 'error');
      };
      imagePane.appendChild(img);
      setFileEditorPanelStatus(panel, 'loading...', '');
    }
    return;
  }
  if (imagePane) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.hidden = true;
    imagePane.replaceChildren();
  }
  const previewing = isMarkdown && fileEditorPreviewMode.get(path) === true;
  if (previewing) {
    if (textarea) textarea.hidden = true;
    if (highlightPane) highlightPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    if (previewPane) {
      previewPane.hidden = false;
      renderMarkdownPreviewInto(previewPane, state.content);
    }
  } else {
    if (previewPane) previewPane.hidden = true;
    if (textarea) {
      textarea.hidden = false;
      textarea.readOnly = readOnlyMode;
      if (textarea.value !== state.content) textarea.value = state.content;
      panel.classList.toggle('editor-wrap', fileEditorWrapEnabled);
      applyEditorWrapToTextarea(textarea);
      renderSyntaxHighlight(panel, path, state.content);
      syncSyntaxHighlightScroll(panel);
      textarea.focus({preventScroll: true});
    }
  }
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
}

function loadFileEditorState(path, panel, item) {
  const state = openFiles.get(path);
  if (!state || state.loadingPromise) return;
  state.loadingPromise = (async () => {
    const ext = fileExtensionOf(basenameOf(path));
    if (IMAGE_EXTENSIONS.has(ext)) {
      const entry = await fetchFileEntry(path);
      if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
        const state = tooLargeFileState(Number(entry.size));
        state.mtime = entry?.mtime || 0;
        openFiles.set(path, state);
      } else {
        openFiles.set(path, {mtime: entry?.mtime || 0, kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null});
      }
      renderFileEditorPanel(panel, item);
      renderSessionButtons();
      renderPaneTabStrips();
      return;
    }
    try {
      const response = await apiFetch(`/api/fs/read?path=${encodeURIComponent(path)}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message = String(payload.error || response.status);
        openFiles.set(path, response.status === 413 ? tooLargeFileState(null, message) : fileErrorState(message));
      } else {
        const payload = await response.json();
        openFiles.set(path, {
          mtime: payload.mtime,
          size: payload.size,
          kind: 'text',
          original: payload.content,
          content: payload.content,
          dirty: false,
        });
      }
    } catch (err) {
      openFiles.set(path, fileErrorState(err));
    }
    renderFileEditorPanel(panel, item);
    renderSessionButtons();
    renderPaneTabStrips();
  })();
}

function updateFileEditorPanelChrome(panel, path) {
  const state = openFiles.get(path);
  panel.classList.toggle('dirty', !!state?.dirty);
  const dirtyDot = panel.querySelector('.file-editor-title .file-tab-dirty');
  if (dirtyDot) dirtyDot.hidden = !state?.dirty;
  const nameNode = panel.querySelector('.file-editor-title-name');
  if (nameNode) nameNode.textContent = basenameOf(path);
  const saveButton = panel.querySelector('.file-editor-save-panel');
  if (saveButton) {
    saveButton.hidden = readOnlyMode || state?.kind !== 'text';
    saveButton.disabled = !state?.dirty;
  }
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  if (reloadButton) {
    reloadButton.hidden = !(state?.externalChanged || state?.externalMissing);
  }
}

function setFileEditorPanelStatus(panel, message, level) {
  const status = panel.querySelector('.file-editor-status-panel');
  if (!status) return;
  status.textContent = message || '';
  status.dataset.level = level || '';
}

function renderMarkdownPreviewInto(container, text) {
  if (typeof window.marked === 'undefined') {
    container.textContent = 'marked.js not loaded (offline CDN?)';
    return;
  }
  container.innerHTML = window.marked.parse(text, {gfm: true, breaks: true});
  if (typeof window.hljs !== 'undefined') {
    container.querySelectorAll('pre code').forEach(block => {
      try { window.hljs.highlightElement(block); } catch (_) {}
    });
  }
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

function simpleTokenHighlightHtml(raw, rules) {
  const text = String(raw || '');
  let html = '';
  let index = 0;
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
      html += esc(text.slice(index));
      break;
    }
    html += esc(text.slice(index, best.index));
    html += `<span class="${best.rule.className}">${esc(best.text)}</span>`;
    index = best.index + best.text.length;
  }
  return html;
}

function simpleCodeSyntaxHtml(language, text) {
  if (language === 'markdown') return markdownSyntaxHtml(text);
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
    {regex: /\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:False|None|True|self|cls)\b/g, className: 'code-constant'},
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
    {regex: /\b(?:as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while)\b/g, className: 'code-keyword'},
    {regex: /\b(?:bool|char|f32|f64|i8|i16|i32|i64|i128|isize|str|u8|u16|u32|u64|u128|usize|String|Vec|Option|Result)\b/g, className: 'code-type'},
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
  const rules = rulesByLanguage.get(language);
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

function highlightLanguageAvailable(language) {
  if (!language || typeof window.hljs === 'undefined') return false;
  if (typeof window.hljs.getLanguage !== 'function') return true;
  return Boolean(window.hljs.getLanguage(language));
}

function renderSyntaxHighlight(panel, path, content) {
  const highlightPane = panel.querySelector('.file-editor-highlight-panel');
  const code = highlightPane?.querySelector('code');
  if (!highlightPane || !code) return false;
  const language = syntaxLanguageForPath(path);
  const simpleHtml = simpleCodeSyntaxHtml(language, content);
  if (simpleHtml !== null) {
    code.className = `language-${language || 'text'}`;
    code.innerHTML = simpleHtml;
    highlightPane.hidden = false;
    panel.classList.add('syntax-highlighted');
    return true;
  }
  if (!highlightLanguageAvailable(language)) {
    highlightPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    code.textContent = '';
    code.className = '';
    return false;
  }
  try {
    code.className = `language-${language}`;
    code.innerHTML = window.hljs.highlight(String(content || ''), {language, ignoreIllegals: true}).value || '\n';
    highlightPane.hidden = false;
    panel.classList.add('syntax-highlighted');
    return true;
  } catch (_) {
    highlightPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    code.textContent = '';
    code.className = '';
    return false;
  }
}

function syncSyntaxHighlightScroll(panel) {
  const textarea = panel.querySelector('.file-editor-textarea-panel');
  const highlightPane = panel.querySelector('.file-editor-highlight-panel');
  if (!textarea || !highlightPane || highlightPane.hidden) return;
  highlightPane.scrollTop = textarea.scrollTop;
  highlightPane.scrollLeft = textarea.scrollLeft;
}

function refreshEditorSyntaxHighlights() {
  for (const [item, panel] of panelNodes.entries()) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    if (state?.kind === 'text') {
      renderSyntaxHighlight(panel, path, state.content);
      syncSyntaxHighlightScroll(panel);
    }
  }
}

window.addEventListener('load', refreshEditorSyntaxHighlights);

async function saveFileEditor(path, panel) {
  if (readOnlyMode) return;
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return;
  const textarea = panel.querySelector('.file-editor-textarea-panel');
  if (textarea) state.content = textarea.value;
  setFileEditorPanelStatus(panel, 'saving...', '');
  try {
    const response = await apiFetch('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        path,
        content: state.content,
        expected_mtime: state.mtime,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setFileEditorPanelStatus(panel, `save failed: ${payload.error || response.status}`, 'error');
      return;
    }
    const payload = await response.json();
    state.mtime = payload.mtime;
    state.size = payload.size;
    state.original = state.content;
    state.dirty = false;
    clearOpenFileExternalState(state);
    updateFileEditorPanelChrome(panel, path);
    setFileEditorPanelStatus(panel, `saved (${payload.size} bytes)`, 'ok');
    renderSessionButtons();
    renderPaneTabStrips();
  } catch (err) {
    setFileEditorPanelStatus(panel, `save failed: ${err}`, 'error');
  }
}

function windowStepButtonLabel(dir) {
  return dir === 'prev' ? 'previous' : 'next';
}

function windowStepButtonHtml(session, dir, visible, disabled) {
  if (!visible) return '';
  const label = windowStepButtonLabel(dir);
  const glyph = dir === 'prev' ? '&lt;' : '&gt;';
  if (disabled) {
    return `<button type="button" class="tab tmux-window-step" data-window-step-button="${dir}" disabled title="unavailable for ${esc(itemLabel(session))}">${glyph}</button>`;
  }
  if (readOnlyMode) {
    return `<button type="button" class="tab tmux-window-step" data-window-step-button="${dir}" disabled title="${label} tmux window requires admin access">${glyph}</button>`;
  }
  return `<button class="tab tmux-window-step" data-window-step-button="${dir}" data-window-dir="${dir}" data-window-session="${esc(session)}" title="${label} tmux window">${glyph}</button>`;
}

function panelControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => disabled ? ` type="button" disabled title="unavailable for ${esc(unavailableLabel)}" aria-label="${esc(label)}"` : '';
  const readonlyAttrs = label => ` type="button" disabled title="${esc(label)} requires admin access" aria-label="${esc(label)}"`;
  const tabAttrs = (name, label = '') => {
    if (disabled) return disabledAttrs(label || name);
    if (readOnlyMode && name === 'summary') return readonlyAttrs('AI summary');
    const labelAttrs = label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : '';
    return ` type="button" data-tab="${esc(session)}" data-tab-name="${name}"${labelAttrs}`;
  };
  const infoAttrs = disabled ? disabledAttrs('Branch Info') : ` type="button" data-detail-toggle="${esc(session)}" title="Branch Info" aria-label="Branch Info"`;
  const info = transcriptMeta.sessions?.[session];
  const terminalTitle = terminalTabTitle(session, info);
  const terminalAttrs = disabled ? disabledAttrs(terminalTitle) : `${tabAttrs('terminal')} title="${esc(terminalTitle)}" aria-label="${esc(terminalTitle)}"`;
  const terminalLabel = disabled ? 'Term' : terminalTabLabel(session, info);
  const steps = disabled ? {prev: false, next: false} : windowStepVisibility(info?.panes);
  const isFiles = isFileExplorerItem(session);
  const minimizeAttrs = !isFiles
    ? ` type="button" data-pane-minimize="${esc(session)}" title="minimize pane" aria-label="Minimize pane"`
    : ` type="button" data-pane-close="${esc(session)}" title="close ${esc(fileExplorerLabel())}" aria-label="Close ${esc(fileExplorerLabel())}"`;
  const minimizeClass = isFiles
    ? `tab pane-close ${platformWindowControlClass('close')}`
    : `tab pane-minimize ${platformWindowControlClass('minimize')}`;
  const expandAttrs = `${canPaneExpand(session) ? '' : ' hidden'} type="button" data-pane-expand="${esc(session)}" title="expand pane" aria-label="Expand pane"`;
  const expandHtml = isFiles || disabled
    ? ''
    : `<button class="tab pane-expand ${platformWindowControlClass('zoom')}" ${expandAttrs}></button>`;
  const actionsHtml = !disabled && !isFiles && isTmuxSession(session)
    ? `<button type="button" class="tab pane-actions" data-pane-actions="${esc(session)}" title="session actions" aria-label="Session actions">...</button>`
    : '';
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          ${windowStepButtonHtml(session, 'prev', steps.prev, disabled)}
          <button class="tab active terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>
          ${windowStepButtonHtml(session, 'next', steps.next, disabled)}
          <button class="tab" ${tabAttrs('transcript', 'Transcript')}>Tx</button>
          <button class="tab" ${tabAttrs('summary', 'AI summary')}>AI</button>
          <button class="tab" ${tabAttrs('events', 'Event log')}>Log</button>
          <button class="tab panel-detail-toggle active" ${infoAttrs}>Info</button>
          ${actionsHtml}
          <button class="${minimizeClass}" ${minimizeAttrs}></button>
          ${expandHtml}
        </div>`;
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = `panel-${session}`;
  panel.innerHTML = `
      <div class="panel-head">
        ${panelControlsHtml(session)}
        <div class="pane-tabs" role="tablist" aria-label="Tabs"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-popover-zone">
          <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(sessionState(session, transcriptMeta.sessions?.[session]))}</div>
          <div id="meta-${session}" class="meta">finding branch...</div>
          ${sessionPopoverHtml(session, transcriptMeta.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMeta.sessions?.[session]))}
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(session)}" title="hide details" aria-label="hide details"></button>
      </div>
      <div id="terminal-pane-${session}" class="tab-pane active panel-overlay-root">
        <div id="term-${session}" class="terminal"></div>
        <div id="panel-toasts-${session}" class="panel-toast-stack">
          <div id="upload-${session}" class="upload-result toast" hidden></div>
        </div>
      </div>
      <div id="transcript-pane-${session}" class="tab-pane">
        <div class="transcript">
          <div class="transcript-head">Transcript</div>
          <div id="transcript-path-${session}" class="transcript-path-row">finding transcript...</div>
          <div id="transcript-${session}" class="transcript-preview">finding transcript...</div>
        </div>
      </div>
      <div id="summary-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">AI summary</div>
          <div id="summary-context-${session}" class="summary-context">loading session context...</div>
          <pre id="summary-${session}" class="summary-preview">click AI summary to generate a Codex summary of the last hour</pre>
        </div>
      </div>
      <div id="events-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">YOLO log</div>
          <div id="events-${session}" class="event-list">loading events...</div>
        </div>
      </div>`;
  bindPanelShell(panel, session);
  installFilePathDropTarget(session, panel);
  bindPanelControls(panel, session);
  return panel;
}

function renderInfoPanel() {
  const node = document.getElementById('info-content');
  if (!node) return;
  const rows = infoBranchRows();
  if (!rows.length) {
    node.innerHTML = '<div class="info-empty">No branch metadata loaded yet.</div>';
    return;
  }
  const headerCell = (key, label) => {
    const active = infoBranchSort.key === key;
    const dirLabel = active ? (infoBranchSort.dir === 'asc' ? 'ascending' : 'descending') : 'unsorted';
    const marker = active ? (infoBranchSort.dir === 'asc' ? 'A-Z' : 'Z-A') : '';
    return `<button type="button" class="info-sort-button${active ? ' active' : ''}" data-info-sort="${esc(key)}" aria-label="sort ${esc(label)} ${esc(dirLabel)}"><span>${esc(label)}</span>${marker ? `<span class="info-sort-marker">${marker}</span>` : ''}</button>`;
  };
  const header = `<div class="info-row header">
    <div class="info-cell">${headerCell('session', 'session-name')}</div>
    <div class="info-cell">${headerCell('path', 'path')}</div>
    <div class="info-cell">${headerCell('branch', 'branch')}</div>
    <div class="info-cell">${headerCell('pr', 'PR')}</div>
    <div class="info-cell">${headerCell('linear', 'Linear')}</div>
    <div class="info-cell">${headerCell('desc', 'desc')}</div>
    <div class="info-cell">${headerCell('updated', 'updated')}</div>
  </div>`;
  const body = rows.map(row => `<div class="info-row${row.current ? ' current' : ''}">
    <div class="info-cell" title="${esc(row.session)}">${esc(row.session)}</div>
    <div class="info-cell" title="${esc(row.path)}">${esc(pathBasename(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${row.branchHtml}</div>
    <div class="info-cell" title="${esc(row.prTitle)}">${row.prHtml}</div>
    <div class="info-cell" title="${esc(row.linearTitle)}">${row.linearHtml}</div>
    <div class="info-cell" title="${esc(row.desc)}">${esc(row.desc)}</div>
    <div class="info-cell" title="${esc(row.updated)}">${esc(row.updated)}</div>
  </div>`).join('');
  node.innerHTML = header + body;
  node.querySelectorAll('[data-info-sort]').forEach(button => {
    button.addEventListener('click', () => {
      setInfoBranchSort(button.dataset.infoSort);
      renderInfoPanel();
    });
  });
}

function infoBranchRows() {
  return sortedInfoBranchRows(rawInfoBranchRows(), infoBranchSort);
}

function rawInfoBranchRows() {
  const rows = [];
  const seen = new Set();
  for (const session of sessions) {
    const info = transcriptMeta.sessions?.[session];
    const project = info?.project || {};
    const git = project.git;
    const branches = git?.other_branches?.branches || [];
    for (const branch of branches) {
      const key = `${git?.root || ''}\n${branch.name || ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const current = branch.current === true;
      const currentPr = current ? displayPullRequest(info) : null;
      const currentLinear = current ? project.linear || [] : [];
      const linearIds = currentLinear.length
        ? currentLinear.map(issue => issue.identifier).filter(Boolean)
        : branch.linear_ids || [];
      const linearHtml = currentLinear.length
        ? currentLinear.map(issue => linearIssueHtml(issue)).join(' ')
        : linearIds.map(linearIssueLinkHtml).filter(Boolean).join(' ');
      const prHtml = currentPr?.number ? pullRequestColumnLinkHtml(currentPr) : pullRequestLinkForBranch(git, branch);
      const prValue = currentPr?.number ? currentPr : branch.pull_request;
      const prTitle = pullRequestTextForBranch(prValue, branch.subject || '');
      const linearTitle = currentLinear.length
        ? currentLinear.map(issue => [issue.identifier, issue.state, issue.title].filter(Boolean).join(' ')).filter(Boolean).join(' · ')
        : linearIds.join(' ');
      const desc = shortText(
        currentPr?.title
          || currentPr?.description
          || currentLinear.find(issue => issue.title)?.title
          || branch.subject
          || '',
        180,
      );
      rows.push({
        session,
        path: git?.root || git?.cwd || '',
        branch: branch.name || '',
        branchHtml: branchLinkHtml(git, branch.name),
        desc,
        updated: branch.updated || '',
        updatedTs: Number.isFinite(branch.updated_ts) ? branch.updated_ts : 0,
        prHtml: prHtml || '',
        prTitle,
        prSort: prTitle || (prValue?.number ? String(prValue.number) : ''),
        linearHtml,
        linearTitle,
        current,
      });
    }
  }
  return rows;
}

function setInfoBranchSort(key) {
  if (!infoBranchSortColumns.has(key)) return;
  if (infoBranchSort.key === key) {
    infoBranchSort = {key, dir: infoBranchSort.dir === 'asc' ? 'desc' : 'asc'};
  } else {
    infoBranchSort = {key, dir: 'asc'};
  }
}

const infoBranchSortColumns = new Set(['session', 'path', 'branch', 'pr', 'linear', 'desc', 'updated']);

function infoBranchSortValue(row, key) {
  if (key === 'updated') return Number.isFinite(row.updatedTs) ? row.updatedTs : 0;
  if (key === 'pr') return row.prSort || row.prTitle || '';
  if (key === 'linear') return row.linearTitle || '';
  return row[key] || '';
}

function compareInfoBranchRows(left, right, sortState) {
  const key = infoBranchSortColumns.has(sortState?.key) ? sortState.key : 'updated';
  const direction = sortState?.dir === 'asc' ? 1 : -1;
  const leftValue = infoBranchSortValue(left, key);
  const rightValue = infoBranchSortValue(right, key);
  let result = 0;
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    result = leftValue - rightValue;
  } else {
    result = String(leftValue).localeCompare(String(rightValue), undefined, {numeric: true, sensitivity: 'base'});
  }
  if (result !== 0) return result * direction;
  return (right.updatedTs - left.updatedTs)
    || String(left.session).localeCompare(String(right.session), undefined, {numeric: true, sensitivity: 'base'})
    || String(left.path).localeCompare(String(right.path), undefined, {numeric: true, sensitivity: 'base'})
    || String(left.branch).localeCompare(String(right.branch), undefined, {numeric: true, sensitivity: 'base'});
}

function sortedInfoBranchRows(rows, sortState = infoBranchSort) {
  return rows.slice().sort((left, right) => compareInfoBranchRows(left, right, sortState));
}

function bindPanelControls(panel, session) {
  panel.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click', () => {
      const currentName = button.dataset.tabName;
      const nextName = currentName !== 'terminal' && button.classList.contains('active') ? 'terminal' : currentName;
      activateTab(button.dataset.tab, nextName);
    });
  });
  panel.querySelectorAll('[data-window-dir]').forEach(button => {
    button.addEventListener('click', handleWindowStepButtonClick);
  });
  panel.querySelector('[data-pane-close]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    removePaneFromLayout(event.currentTarget.dataset.paneClose);
  });
  panel.querySelector('[data-pane-minimize]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    minimizePaneFromLayout(event.currentTarget.dataset.paneMinimize);
  });
  panel.querySelector('[data-pane-expand]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    expandPaneFromLayout(event.currentTarget.dataset.paneExpand);
  });
  panel.querySelector('[data-pane-actions]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const button = event.currentTarget;
    const rect = button.getBoundingClientRect();
    showSessionContextMenu(button.dataset.paneActions || session, rect.left, rect.bottom + 4);
  });
  if (isTmuxSession(session)) {
    panel.querySelector('.panel-head')?.addEventListener('contextmenu', event => {
      if (event.target.closest('button, input')) return;
      event.preventDefault();
      event.stopPropagation();
      showSessionContextMenu(session, event.clientX, event.clientY);
    });
  }
  panel.querySelector('[data-context]')?.addEventListener('click', () => showContext(session));
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-auto-session]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    toggleAutoApprove(target.dataset.autoSession || session);
  });
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-copy-transcript-path]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    const path = target.dataset.copyTranscriptPath || '';
    if (!path) return;
    copyTextToClipboard(path)
      .then(() => { statusEl.textContent = 'copied transcript path'; })
      .catch(error => { statusEl.innerHTML = `<span class="err">copy failed: ${esc(error)}</span>`; });
  });
  panel.querySelector('.meta')?.addEventListener('click', event => event.stopPropagation());
  panel.querySelector('.meta')?.addEventListener('dragstart', event => event.stopPropagation());
  bindFileUpload(panel, session);
}

function hasFileDrag(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes('Files') || Boolean(event.dataTransfer?.files?.length);
}

function bindFileUpload(panel, session) {
  if (readOnlyMode) return;
  panel.addEventListener('dragenter', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.add('file-drag-over');
  });
  panel.addEventListener('dragover', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    panel.classList.add('file-drag-over');
  });
  panel.addEventListener('dragleave', event => {
    if (!hasFileDrag(event)) return;
    if (panel.contains(event.relatedTarget)) return;
    panel.classList.remove('file-drag-over');
  });
  panel.addEventListener('drop', event => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.remove('file-drag-over');
    uploadFiles(session, event.dataTransfer?.files || []);
  });
}

function insertFileDragPayloadIntoTerminal(session, payload) {
  const references = terminalFileReferences(session, payload);
  if (!references.length) return;
  const inserted = insertIntoTerminal(session, `${references.map(shellQuote).join(' ')} `);
  const label = references.length === 1 ? references[0] : `${references.length} paths`;
  statusEl.innerHTML = inserted
    ? `<span class="ok">inserted ${esc(label)} into ${esc(sessionLabel(session))}</span>`
    : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
}

function installFilePathDropTarget(session, target) {
  if (readOnlyMode) return;
  target.addEventListener('dragover', event => {
    if (!hasYolomuxFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    target.classList.add('path-drag-over');
  });
  target.addEventListener('dragleave', event => {
    if (target.contains(event.relatedTarget)) return;
    target.classList.remove('path-drag-over');
  });
  target.addEventListener('drop', event => {
    const payload = fileDragPayload(event);
    if (!payload?.path) return;
    event.preventDefault();
    event.stopPropagation();
    target.classList.remove('path-drag-over');
    insertFileDragPayloadIntoTerminal(session, payload);
  });
}

function installTerminalFileDrop(session, container) {
  installFilePathDropTarget(session, container);
}

function bindClipboardPaste() {
  if (readOnlyMode) return;
  if (clipboardPasteBound) return;
  clipboardPasteBound = true;
  document.addEventListener('paste', event => {
    const file = pastedImageFile(event);
    if (!file) return;
    const session = pasteTargetSession(event);
    if (!session) {
      statusEl.innerHTML = '<span class="err">select a YOLOmux pane before pasting an image</span>';
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (!beginPasteUpload(session)) return;
    uploadFiles(session, [file], {source: 'paste'}).finally(() => {
      pasteUploadInFlight = false;
    });
  }, {capture: true});
}

function pastedImageFile(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItems = items.filter(item => item.kind === 'file' && String(item.type || '').startsWith('image/'));
  const item = imageItems.find(candidate => candidate.type === 'image/png') || imageItems[0];
  if (!item) return null;
  const file = item.getAsFile();
  if (!file) return null;
  const type = file.type || item.type || 'image/png';
  return new File([file], pastedImageFilename(file.name, type), {type});
}

function beginPasteUpload(session) {
  const now = Date.now();
  if (pasteUploadInFlight) return false;
  try {
    const existing = JSON.parse(localStorage.getItem(pasteLockStorageKey) || 'null');
    if (existing?.expiresAt && existing.expiresAt > now) return false;
    localStorage.setItem(pasteLockStorageKey, JSON.stringify({session, expiresAt: now + 1500}));
  } catch (_) {
    // Clipboard events can arrive as a burst; the in-memory flag is the fallback.
  }
  pasteUploadInFlight = true;
  return true;
}

function pasteTargetSession(event) {
  const panel = event.target?.closest?.('.panel');
  const panelSession = panel?.id?.startsWith('panel-') ? panel.id.slice('panel-'.length) : '';
  if (sessions.includes(panelSession) && activeSessions.includes(panelSession)) return panelSession;
  if (focusedTerminal && activeSessions.includes(focusedTerminal)) return focusedTerminal;
  if (focusedPanelItem && sessions.includes(focusedPanelItem) && activeSessions.includes(focusedPanelItem)) return focusedPanelItem;
  if (lastFocusedTmuxSession && activeSessions.includes(lastFocusedTmuxSession)) return lastFocusedTmuxSession;
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  return activeTmuxSessions.length === 1 ? activeTmuxSessions[0] : null;
}

function nextPasteFilename(mimeType) {
  const stamp = pacificDateStamp();
  const suffix = imageSuffix(mimeType);
  const key = `${stamp}:${suffix}`;
  const next = nextPasteCounter(key);
  return `${stamp}-${String(next).padStart(3, '0')}${suffix}`;
}

function pastedImageFilename(originalName, mimeType) {
  const suffix = imageSuffixFromFilename(originalName) || imageSuffix(mimeType);
  const imageNumber = imageNumberFromFilename(originalName);
  if (Number.isFinite(imageNumber)) {
    return `${pacificDateStamp()}-${String(imageNumber).padStart(3, '0')}${suffix}`;
  }
  return nextPasteFilename(mimeType);
}

function nextPasteCounter(key) {
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key)) + 1;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
  return next;
}

function readPasteCounters() {
  try {
    const counters = JSON.parse(localStorage.getItem(pasteCountersStorageKey) || '{}');
    return counters && typeof counters === 'object' ? counters : {};
  } catch (_) {
    return {};
  }
}

function writePasteCounters(counters) {
  try {
    localStorage.setItem(pasteCountersStorageKey, JSON.stringify(counters));
  } catch (_) {}
}

function pasteCounterValue(counters, key) {
  return Number(counters?.[key]) || 0;
}

function imageNumberFromFilename(filename) {
  const name = pathBasename(filename || '').replace(/\.[A-Za-z0-9]{1,8}$/, '');
  const match = name.match(/(?:^|[^A-Za-z])image[^0-9]*(\d+)(?:[^0-9]|$)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function pasteUploadIndexFromPath(path) {
  const match = pathBasename(path || '').match(/^\d{8}-(\d{3})(?:\.[A-Za-z0-9]{1,8})$/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function imageSuffixFromFilename(filename) {
  const match = String(filename || '').match(/(\.[A-Za-z0-9]{1,8})$/);
  if (!match) return '';
  const suffix = match[1].toLowerCase();
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'].includes(suffix) ? (suffix === '.jpeg' ? '.jpg' : suffix) : '';
}

function pacificDateStamp() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}${values.month}${values.day}`;
}

function imageSuffix(mimeType) {
  const value = String(mimeType || '').toLowerCase();
  if (value.includes('jpeg') || value.includes('jpg')) return '.jpg';
  if (value.includes('gif')) return '.gif';
  if (value.includes('webp')) return '.webp';
  if (value.includes('bmp')) return '.bmp';
  return '.png';
}

async function uploadFiles(session, fileList, options = {}) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot upload files</span>';
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const response = await apiFetch(`/api/upload?session=${encodeURIComponent(session)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">upload failed: ${esc(payload.error || response.statusText)}</span>`;
      return;
    }
    const paths = (payload.files || []).map(file => file.path).filter(Boolean);
    if (options.source === 'paste') syncPasteCountersFromPayload(payload);
    activateTab(session, 'terminal');
    const inserted = options.source === 'paste'
      ? insertPasteUploadReferences(session, payload.files || [], {silent: true})
      : insertUploadPaths(session, paths, {silent: true});
    showUploadResult(session, payload, inserted);
    refreshOpenEventLogs();
    refreshTranscripts();
  } catch (error) {
    statusEl.innerHTML = `<span class="err">upload failed: ${esc(error)}</span>`;
  }
}

function insertUploadPaths(session, paths, options = {}) {
  if (!paths.length) return false;
  const inserted = insertIntoTerminal(session, `${paths.map(shellQuote).join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">inserted upload path into ${esc(sessionLabel(session))}</span>`
      : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
  }
  return inserted;
}

function insertPasteUploadReferences(session, files, options = {}) {
  const references = pasteUploadReferences(files);
  if (!references.length) return insertUploadPaths(session, files.map(file => file.path).filter(Boolean), options);
  const inserted = insertIntoTerminal(session, `${references.join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">inserted pasted image into ${esc(sessionLabel(session))}</span>`
      : `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
  }
  return inserted;
}

function pasteUploadReferences(files) {
  return (files || []).map((file, index) => {
    const path = file.path || '';
    if (!path) return '';
    const number = pasteUploadIndexFromPath(path) || index + 1;
    return `[Image #${number}] ${shellQuote(path)}`;
  }).filter(Boolean);
}

function syncPasteCountersFromPayload(payload) {
  const files = payload?.files || [];
  for (const file of files) syncPasteCounterFromPath(file.path || file.saved_name || '');
}

function syncPasteCounterFromPath(path) {
  const index = pasteUploadIndexFromPath(path);
  if (!Number.isFinite(index)) return;
  const suffix = imageSuffixFromFilename(path) || imageSuffix('');
  const stampMatch = pathBasename(path).match(/^(\d{8})-/);
  const stamp = stampMatch?.[1] || pacificDateStamp();
  const key = `${stamp}:${suffix}`;
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key), index);
  if (next <= localValue) return;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
}

function insertIntoTerminal(session, text) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot type into terminal sessions</span>';
    return false;
  }
  const item = terminals.get(session);
  if (!item || item.socket.readyState !== WebSocket.OPEN) return false;
  const filtered = stripTerminalQueryResponses(text);
  if (!filtered) return false;
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  item.term?.focus?.();
  setFocusedTerminal(session);
  return true;
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function showUploadResult(session, payload, inserted) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
  const files = payload.files || [];
  const paths = files.map(file => file.path).filter(Boolean);
  const label = files.length === 1 ? (files[0].saved_name || files[0].name || 'file') : `${files.length} files`;
  const target = payload.target_dir || '';
  const insertedText = inserted ? '; path inserted' : '; terminal not connected';
  const expiresAt = Date.now() + toastDurationMs;
  const newEntries = files.length
    ? files.map(file => {
      const name = file.saved_name || file.name || 'file';
      const destination = pathBasename(file.path || target) || target;
      return {
        id: ++uploadResultSequence,
        text: `uploaded ${name} to ${destination}${insertedText}`,
        path: file.path || '',
        expiresAt,
      };
    })
    : [{
      id: ++uploadResultSequence,
      text: `uploaded ${label} to ${pathBasename(target) || target}${insertedText}`,
      path: target,
      expiresAt,
    }];
  const existing = uploadResultsBySession.get(session) || [];
  const active = [...existing.filter(entry => entry.expiresAt > Date.now()), ...newEntries].slice(-8);
  uploadResultsBySession.set(session, active);
  renderUploadResult(session);
}

function ensureUploadResultShell(session, node) {
  return ensureToastShell(node, {
    title: `YOLOmux - ${serverHostname}: ${sessionLabel(session)} upload`,
    closeLabel: 'Hide upload status',
    keepLabel: 'Keep upload status visible',
    onKeep: () => keepUploadResult(session),
    onClose: () => hideUploadResult(session),
  });
}

function keepUploadResult(session) {
  const entries = uploadResultsBySession.get(session) || [];
  for (const entry of entries) entry.expiresAt = Number.POSITIVE_INFINITY;
  uploadResultsBySession.set(session, entries);
  if (uploadCleanupTimers.has(session)) {
    clearTimeout(uploadCleanupTimers.get(session));
    uploadCleanupTimers.delete(session);
  }
}

function scheduleUploadResultCleanup(session, active, now) {
  if (uploadCleanupTimers.has(session)) clearTimeout(uploadCleanupTimers.get(session));
  const delay = Math.max(1, Math.min(...active.map(entry => entry.expiresAt - now)));
  uploadCleanupTimers.set(session, window.setTimeout(() => {
    uploadCleanupTimers.delete(session);
    renderUploadResult(session);
  }, delay));
}

function renderUploadResult(session) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
  const now = Date.now();
  const active = (uploadResultsBySession.get(session) || []).filter(entry => entry.expiresAt > now).slice(-8);
  uploadResultsBySession.set(session, active);
  if (!active.length) {
    node.hidden = true;
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    if (uploadCleanupTimers.has(session)) {
      clearTimeout(uploadCleanupTimers.get(session));
      uploadCleanupTimers.delete(session);
    }
    return;
  }
  const textNode = ensureUploadResultShell(session, node);
  if (!textNode) return;
  const paths = active.map(entry => entry.path).filter(Boolean);
  node.hidden = false;
  textNode.title = paths.join('\n');
  renderToastLines(textNode, active.map(entry => ({
    text: entry.text,
    countdownMs: entry.expiresAt - now,
  })));
  scheduleUploadResultCleanup(session, active, now);
}

function hideUploadResult(session) {
  uploadResultsBySession.delete(session);
  if (uploadCleanupTimers.has(session)) {
    clearTimeout(uploadCleanupTimers.get(session));
    uploadCleanupTimers.delete(session);
  }
  const node = document.getElementById(`upload-${session}`);
  if (node) {
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    node.hidden = true;
  }
}

function updatePanelSlot(panel, session, slot) {
  panel.dataset.slot = slot;
  const head = panel.querySelector('.panel-head');
  if (head) head.dataset.dragSlot = slot;
  if (isFileEditorItem(session)) renderFileEditorPanel(panel, session);
  updatePaneExpandButton(panel, session);
  updatePaneTabStrip(panel, slot);
  updatePanelInactiveOverlays();
}

function updatePaneExpandButton(panel, session) {
  const button = panel.querySelector('[data-pane-expand]');
  if (button) button.hidden = !canPaneExpand(session);
}

function syncPanelVisibility(previousActive = []) {
  const visible = new Set(activeSessions);
  for (const session of sessions) {
    if (!visible.has(session)) {
      stopTranscriptStream(session);
      stopSummaryStream(session);
      if (focusedTerminal === session) focusedTerminal = null;
    }
    updateTypingIndicator(session);
  }
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`terminal-pane-${session}`);
    if (pane?.classList.contains('active')) scheduleFit(session);
  }
}

function activateTab(session, name) {
  setFocusedPanelItem(session);
  if (name !== 'transcript') stopTranscriptStream(session);
  if (name !== 'summary') stopSummaryStream(session);
  document.querySelectorAll(`[data-tab="${session}"]`).forEach(button => {
    button.classList.toggle('active', button.dataset.tabName === name);
  });
  for (const tabName of ['terminal', 'transcript', 'summary', 'events']) {
    const pane = document.getElementById(`${tabName}-pane-${session}`);
    if (pane) pane.classList.toggle('active', tabName === name);
  }
  updateTypingIndicator(session);
  if (name === 'terminal') {
    scheduleFit(session);
    setTimeout(() => refreshTerminal(session), 120);
    setTimeout(() => terminals.get(session)?.term?.focus(), 25);
  } else {
    clearFocusedTerminal(session);
  }
  if (name === 'transcript') {
    startTranscriptStream(session, {scrollBottom: true});
  }
  if (name === 'summary') startSummaryStream(session);
  if (name === 'events') refreshEventLog(session);
}

function tmuxWindow(session, key, label) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot switch tmux windows</span>';
    return;
  }
  const item = terminals.get(session);
  if (!item || item.socket.readyState !== WebSocket.OPEN) {
    statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
    return;
  }
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  previewTmuxWindowLabel(session, key);
  statusEl.innerHTML = `<span class="ok">${esc(label)}: ${esc(sessionLabel(session))}</span>`;
  scheduleFit(session);
  setTimeout(() => terminals.get(session)?.term?.focus(), 75);
  setTimeout(refreshTranscripts, 250);
}

async function ensureTerminalRunning(session) {
  const item = terminals.get(session);
  if (item && item.socket.readyState !== WebSocket.CLOSING && item.socket.readyState !== WebSocket.CLOSED) return;
  if (readOnlyMode) {
    startTerminal(session);
    return;
  }
  const ensured = await ensureSession(session);
  if (!ensured) {
    const container = document.getElementById(`term-${session}`);
    if (container) container.innerHTML = `<pre class="terminal-error">Session ${esc(sessionLabel(session))} is not available. Click or drag it again to retry.</pre>`;
    return;
  }
  startTerminal(session);
}

function startTerminal(session) {
  const existing = terminals.get(session);
  const reconnectAttempt = existing?.reconnectAttempt || 0;
  if (existing) {
    closeTerminalItem(session, existing);
    terminals.delete(session);
  }
  const container = document.getElementById(`term-${session}`);
  if (!container) return;
  const TerminalCtor = window.Terminal?.Terminal || window.Terminal;
  if (!TerminalCtor) {
    container.innerHTML = '<pre class="terminal-error">xterm.js failed to load from /static/xterm.js. Terminal cannot attach.</pre>';
    statusEl.innerHTML = '<span class="err">xterm unavailable</span>';
    return;
  }
  container.innerHTML = '';
  const size = estimateTerminalSize(container);
  const term = new TerminalCtor({
    cols: size.cols,
    rows: size.rows,
    cursorBlink: true,
    convertEol: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: 13,
    letterSpacing: 0,
    lineHeight: 1.0,
    scrollback: 5000,
    disableStdin: readOnlyMode,
    theme: {
      background: '#11151d',
      foreground: '#dfe6ef',
      cursor: '#f5f7fb',
      selectionBackground: '#3a4b64'
    }
  });
  term.open(container);
  installTerminalLinkProvider(term);
  installTerminalContextMenu(session, term, container);
  installTerminalFileDrop(session, container);
  const openedSize = estimateTerminalSize(container, term);
  if (term.cols !== openedSize.cols || term.rows !== openedSize.rows) {
    term.resize(openedSize.cols, openedSize.rows);
  }
  const socket = new WebSocket(wsUrl(session));
  socket.binaryType = 'arraybuffer';
  const item = {term, socket, container, manualClose: false, reconnectAttempt, reconnectTimer: null, resizeTimer: null, scrollTimer: null, pendingScrollLines: 0};
  terminals.set(session, item);
  enableTerminalScroll(session, term, container);
  observeTerminalResize(session, container);

  socket.onopen = () => {
    item.reconnectAttempt = 0;
    if (terminalIsVisible(session, container)) {
      scheduleFit(session);
      scheduleRemoteResize(session, 50);
    }
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (event.data instanceof ArrayBuffer) {
      term.write(new Uint8Array(event.data));
    } else {
      term.write(String(event.data));
    }
  };
  socket.onclose = () => {
    if (item.manualClose || terminals.get(session) !== item) return;
    term.writeln(`\r\n\x1b[31mdisconnected from ${session}\x1b[0m`);
    postEvent(session, 'terminal_disconnected', `terminal disconnected from ${session}`, {});
    clearFocusedTerminal(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
    scheduleTerminalReconnect(session, item);
  };
  socket.onerror = () => {
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  term.onFocus?.(() => {
    setFocusedTerminal(session);
  });
  term.onBlur?.(() => {
    clearFocusedTerminal(session);
  });
  container.addEventListener('focusin', () => {
    setFocusedTerminal(session);
  });
  container.addEventListener('focusout', () => {
    clearFocusedTerminal(session);
  });
  term.onData(data => {
    if (readOnlyMode) return;
    if (socket.readyState === WebSocket.OPEN) {
      const filtered = stripTerminalQueryResponses(data);
      if (filtered) socket.send(JSON.stringify({type: 'input', data: filtered}));
    }
  });
}

function updateTypingIndicator(session) {
  const item = terminals.get(session);
  const container = item?.container || document.getElementById(`term-${session}`);
  const pane = document.getElementById(`terminal-pane-${session}`);
  const panel = document.getElementById(`panel-${session}`);
  const ready = Boolean(
    item?.socket?.readyState === WebSocket.OPEN
    && focusedTerminal === session
    && pane?.classList.contains('active')
  );
  container?.classList.toggle('typing-ready', ready);
  panel?.classList.toggle('typing-ready-pane', ready);
  panel?.classList.toggle('yolo-ready-pane', ready && autoApproveStates.get(session)?.enabled === true);
}

function updateStatus() {
  if (activeSessions.length === 0) {
    statusEl.textContent = 'no session selected';
    statusEl.removeAttribute('title');
    return;
  }
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  if (!activeTmuxSessions.length) {
    statusEl.textContent = 'Branch Info shown';
    statusEl.removeAttribute('title');
    return;
  }
  let open = 0;
  for (const session of activeTmuxSessions) {
    const item = terminals.get(session);
    if (item?.socket?.readyState === WebSocket.OPEN) open += 1;
  }
  const total = activeTmuxSessions.length;
  statusEl.textContent = open === total ? '' : `${open}/${total} conn`;
  statusEl.title = open === total ? '' : `${open}/${total} terminal sockets connected`;
}

async function toggleAutoApprove(session) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change YOLO</span>';
    return;
  }
  const state = autoApproveStates.get(session) || {};
  const current = state.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change YOLO</span>';
    return;
  }
  try {
    const response = await apiFetch(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      if (payload?.target || payload?.session) {
        autoApproveStates.set(session, payload);
        updateSessionButtonStates();
        renderAutoApproveButton(session, payload);
      }
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'YOLO approval failed')}</span>`;
      return;
    }
    autoApproveStates.set(session, payload);
    updateSessionButtonStates();
    renderAutoApproveButton(session, payload);
    statusEl.innerHTML = payload.enabled
      ? `<span class="ok">enabled YOLO for ${esc(sessionLabel(session))}</span>`
      : `<span class="ok">disabled YOLO for ${esc(sessionLabel(session))}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">YOLO request failed: ${esc(error)}</span>`;
  }
}

async function refreshAutoStatuses() {
  await loadAutoStatuses();
  bindClipboardPaste();
  renderAutoApproveButtons();
  updateSessionButtonStates();
  refreshActivePanelHeaders();
  trackSessionStateChanges();
  refreshOpenEventLogs();
}

async function loadAutoStatuses() {
  try {
    const response = await apiFetch('/api/auto-approve');
    const payload = await response.json();
    const previousActive = activeSessions.slice();
    const sessionsChanged = Array.isArray(payload.session_order) ? updateSessionList(payload.session_order) : false;
    for (const session of sessions) {
      const state = payload.sessions?.[session] || {target: session, enabled: false, last_action: 'off'};
      autoApproveStates.set(session, state);
    }
    if (sessionsChanged) renderPanels(previousActive);
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const response = await apiFetch(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        const payload = await response.json();
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
  }
}

function autoApproveOwnerLabel(payload) {
  const owner = payload?.lock_owner || {};
  const pid = owner.pid ? `pid ${owner.pid}` : '';
  const root = owner.project_root || '';
  return [pid, root].filter(Boolean).join(' ') || payload?.last_action || 'another YOLOmux';
}

function renderAutoApproveButtons() {
  for (const session of sessions) {
    const state = autoApproveStates.get(session) || {target: session, enabled: false, last_action: 'off'};
    renderAutoApproveButton(session, state);
  }
}

function renderAutoApproveButton(session, payload) {
  const buttons = document.querySelectorAll(`[data-yolo-session="${cssEscape(session)}"]`);
  const enabled = payload?.enabled === true;
  const locked = payload?.locked === true && !enabled;
  const working = sessionYoloIsWorking(session, payload);
  for (const button of buttons) {
    const wasWorking = button.classList.contains('working');
    button.classList.toggle('active', enabled);
    button.classList.toggle('inactive', !enabled && !locked);
    button.classList.toggle('locked', locked);
    button.classList.toggle('working', working);
    if (working) {
      if (!wasWorking || !button.style.getPropertyValue('--yolo-rotate-delay')) {
        button.style.setProperty('--yolo-rotate-delay', yoloRotationDelay());
      }
    } else {
      button.style.removeProperty('--yolo-rotate-delay');
    }
    button.closest('.pane-tab')?.classList.remove('is-working');
    button.textContent = 'YO';
    const action = payload?.last_action ? `; ${payload.last_action}` : '';
    button.title = enabled
      ? `YOLO on for ${sessionLabel(session)}${action}${readOnlyMode ? '; readonly access' : ''}`
      : locked
        ? `YOLO owned by ${autoApproveOwnerLabel(payload)}`
      : `YOLO off for ${sessionLabel(session)}${readOnlyMode ? '; readonly access' : ''}`;
  }
  updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  updateTypingIndicator(session);
}

function startSummaryStream(session) {
  stopSummaryStream(session);
  const node = document.getElementById(`summary-${session}`);
  if (!node) return;
  if (readOnlyMode) {
    node.textContent = 'AI summary requires admin access.';
    statusEl.innerHTML = '<span class="err">readonly access cannot run AI summary</span>';
    return;
  }
  node.textContent = 'starting structured Codex summary for the last hour...\n\n';
  const source = new EventSource(`/api/summary-stream?session=${encodeURIComponent(session)}&lookback=${60 * 60}`);
  summaryStreams.set(session, source);
  source.addEventListener('meta', event => {
    const payload = JSON.parse(event.data);
    const fallback = payload.fallback ? 'recent transcript tail' : 'last hour';
    const projectCount = Array.isArray(payload.projects) ? payload.projects.length : 0;
    node.textContent += `[codex] summarizing ${fallback} for ${payload.focus_root || session}\n`;
    if (payload.summary_model) node.textContent += `[codex] model: ${payload.summary_model}; effort: ${payload.summary_effort || 'default'}\n`;
    node.textContent += `[codex] project inventory: ${projectCount} sessions\n\n`;
    node.scrollTop = node.scrollHeight;
  });
  source.addEventListener('log', event => {
    const payload = JSON.parse(event.data);
    if (payload.text) {
      node.textContent += `[codex] ${payload.text}\n`;
      node.scrollTop = node.scrollHeight;
    }
  });
  source.addEventListener('delta', event => {
    const payload = JSON.parse(event.data);
    if (payload.text) {
      node.textContent += payload.text;
      node.scrollTop = node.scrollHeight;
    }
  });
  source.addEventListener('summary_error', event => {
    const payload = JSON.parse(event.data);
    node.textContent += `\n[error] ${payload.error || 'summary failed'}\n`;
    node.scrollTop = node.scrollHeight;
    stopSummaryStream(session);
  });
  source.addEventListener('done', event => {
    const payload = JSON.parse(event.data);
    if (payload.return_code && payload.return_code !== 0) {
      node.textContent += `\n[codex exited ${payload.return_code}]\n`;
    }
    stopSummaryStream(session);
  });
  source.onerror = () => {
    if (summaryStreams.get(session) !== source) return;
    node.textContent += '\n[error] summary stream disconnected\n';
    stopSummaryStream(session);
  };
}

function stopSummaryStream(session) {
  const source = summaryStreams.get(session);
  if (!source) return;
  source.close();
  summaryStreams.delete(session);
}

async function refreshTranscripts() {
  try {
    const response = await apiFetch('/api/transcripts');
    transcriptMeta = await response.json();
    updateMetadataBadgePulses(transcriptMeta);
    const previousActive = activeSessions.slice();
    const sessionsChanged = updateSessionList(transcriptMeta.session_order || []);
    await loadAutoStatuses();
    if (sessionsChanged) renderPanels(previousActive);
    renderSessionButtons();
    renderInfoPanel();
    for (const session of activeSessions.filter(isTmuxSession)) {
      const meta = document.getElementById(`meta-${session}`);
      const preview = document.getElementById(`transcript-${session}`);
      const info = transcriptMeta.sessions?.[session];
      const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
      updatePanelHeader(session, info);
      if (meta) {
        meta.innerHTML = stripTitleAttrs(projectMetaHtml(session, info));
        meta.removeAttribute('title');
      }
      renderSummaryContext(session, info, agent);
      if (agent?.transcript) {
        updateTranscriptPathRow(session, agent.transcript);
        preview.textContent = `session_id: ${agent.session_id || ''}\nstatus: ${agent.status || ''}\n\nloading recent transcript context...`;
        refreshTranscriptPreview(session, preview, {preserveScroll: false});
      } else if (agent?.error) {
        updateTranscriptPathRow(session, '', agent.error);
        preview.textContent = agent.error;
      } else {
        updateTranscriptPathRow(session, '', 'no agent transcript found');
        preview.textContent = 'no agent transcript found';
      }
    }
    renderPaneTabStrips();
    scheduleFileExplorerActiveTabSync();
    refreshWatchedFilesystem();
    trackSessionStateChanges();
    refreshOpenEventLogs();
  } catch (error) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      const meta = document.getElementById(`meta-${session}`);
      const preview = document.getElementById(`transcript-${session}`);
      if (meta) meta.innerHTML = `<span class="err">transcript lookup failed</span>`;
      updateTranscriptPathRow(session, '', 'transcript lookup failed');
      if (preview) preview.textContent = `transcript lookup failed: ${error}`;
    }
  }
}

function updatePanelHeader(session, info) {
  const tab = document.getElementById(`panel-tab-${session}`);
  const panel = document.getElementById(`panel-${session}`);
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  updatePanelControlLabels(session, info);
  syncAttentionAnimation(panel, state.attention === true);
  if (tab) {
    tab.className = `panel-session-label ${auto ? 'auto' : ''} ${state.attention ? 'needs-attention' : ''}`;
    syncAttentionAnimation(tab, state.attention === true);
    tab.innerHTML = panelHeaderStateHtml(state);
    tab.removeAttribute('title');
  }
  const popover = panel?.querySelector(':scope .panel-popover-zone > .session-popover');
  if (popover) {
    const agentKind = sessionAgentKind(session);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = sessionPopoverHtml(session, info, agentKind, auto, state);
    popover.replaceWith(wrapper.firstElementChild);
  }
  panel?.classList.toggle('needs-input-pane', state.key === 'needs-input');
  panel?.classList.toggle('needs-exec-pane', state.key === 'needs-approval');
  panel?.classList.toggle('needs-blocked-pane', state.key === 'blocked');
}

function refreshSessionChrome(session) {
  updateSessionButtonStates();
  updatePanelHeader(session, transcriptMeta.sessions?.[session]);
}

function refreshTrackedSessionChrome(session) {
  refreshSessionChrome(session);
  trackSessionStateChanges();
}

function refreshActivePanelHeaders() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  }
}

function renderSummaryContext(session, info, agent) {
  const node = document.getElementById(`summary-context-${session}`);
  if (!node) return;
  node.innerHTML = summaryContextHtml(session, info, agent);
}

function transcriptPathRowHtml(path, fallback = 'no transcript path') {
  if (!path) return `<span class="transcript-path-missing">${esc(fallback)}</span>`;
  return `<span class="transcript-path-label">path</span><span class="transcript-path-value">${esc(path)}</span><button type="button" class="path-copy-button transcript-path-copy" data-copy-transcript-path="${esc(path)}" title="Copy transcript path" aria-label="Copy transcript path"></button>`;
}

function updateTranscriptPathRow(session, path, fallback = 'no transcript path') {
  const row = document.getElementById(`transcript-path-${session}`);
  if (!row) return;
  row.innerHTML = transcriptPathRowHtml(path, fallback);
}

async function refreshTranscriptPreview(session, preview, options = {}) {
  try {
    const response = await apiFetch(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    const payload = await response.json();
    if (payload.items) {
      updateTranscriptPathRow(session, payload.path);
      renderTranscriptItems(preview, payload.path, payload.items, options);
    } else {
      preview.textContent = JSON.stringify(payload, null, 2);
    }
  } catch (error) {
    preview.textContent += `\n\ncontext load failed: ${error}`;
  }
}

function startTranscriptStream(session, options = {}) {
  stopTranscriptStream(session);
  const preview = document.getElementById(`transcript-${session}`);
  if (!preview) return;
  const url = `/api/context-stream?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`;
  const source = new EventSource(url);
  transcriptStreams.set(session, source);
  source.addEventListener('reset', event => {
    const payload = JSON.parse(event.data);
    updateTranscriptPathRow(session, payload.path);
    renderTranscriptItems(preview, payload.path, payload.items || [], {scrollBottom: options.scrollBottom === true});
  });
  source.addEventListener('items', event => {
    const payload = JSON.parse(event.data);
    appendTranscriptItems(preview, payload.items || []);
  });
  source.addEventListener('ping', () => {});
  source.onerror = () => {
    stopTranscriptStream(session);
    const pane = document.getElementById(`transcript-pane-${session}`);
    if (pane?.classList.contains('active')) {
      statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} transcript stream disconnected</span>`;
      setTimeout(() => {
        if (document.getElementById(`transcript-pane-${session}`)?.classList.contains('active')) {
          startTranscriptStream(session, {scrollBottom: false});
        }
      }, 1500);
    }
  };
}

function stopTranscriptStream(session) {
  const source = transcriptStreams.get(session);
  if (source) {
    source.close();
    transcriptStreams.delete(session);
  }
}

function renderTranscriptItems(container, path, items, options = {}) {
  const shouldScrollBottom = options.scrollBottom === true;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  const oldTop = container.scrollTop;
  const oldHeight = container.scrollHeight;
  const blocks = items.map(item => transcriptItemHtml(item));
  container.innerHTML = blocks.join('');
  if (shouldScrollBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  } else if (options.preserveScroll) {
    if (wasNearBottom) {
      container.scrollTop = container.scrollHeight;
    } else {
      container.scrollTop = Math.max(0, oldTop + container.scrollHeight - oldHeight);
    }
  } else {
    container.scrollTop = container.scrollHeight;
  }
}

function appendTranscriptItems(container, items) {
  if (!items.length) return;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  container.insertAdjacentHTML('beforeend', items.map(item => transcriptItemHtml(item)).join(''));
  const rendered = Array.from(container.querySelectorAll('.transcript-item:not(.system)'));
  const extra = rendered.length - transcriptPreviewMessages;
  for (const item of rendered.slice(0, Math.max(0, extra))) item.remove();
  if (wasNearBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }
}

function transcriptItemHtml(item) {
  const role = normalizeRole(item.role);
  return `<div class="transcript-item ${role}">
    <div class="transcript-role">${esc(item.header || role)}</div>
    <div class="transcript-text">${esc(item.text || '')}</div>
  </div>`;
}

function eventItemHtml(event) {
  const details = event.details && typeof event.details === 'object' ? event.details : {};
  const detailText = Object.entries(details)
    .filter(([, value]) => value != null && value !== '')
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : value}`)
    .join(' · ');
  const title = detailText ? `${event.message || ''}\n${detailText}` : event.message || '';
  return `<div class="event-item" title="${esc(title)}">
    <span class="event-time">${esc(formatEventTime(event.time))}</span>
    <span class="event-type">${esc(event.type || 'event')}</span>
    <span class="event-message">${esc(event.message || '')}${detailText ? ` · ${esc(detailText)}` : ''}</span>
  </div>`;
}

function formatEventTime(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

async function refreshEventLog(session) {
  const node = document.getElementById(`events-${session}`);
  if (!node) return;
  try {
    const response = await apiFetch(`/api/events?session=${encodeURIComponent(session)}&limit=120`);
    const payload = await response.json();
    if (!response.ok) {
      node.innerHTML = `<div class="event-empty">${esc(payload.error || 'failed to load events')}</div>`;
      return;
    }
    const events = Array.isArray(payload.events) ? payload.events : [];
    node.innerHTML = events.length
      ? events.slice().reverse().map(eventItemHtml).join('')
      : '<div class="event-empty">no events yet</div>';
  } catch (error) {
    node.innerHTML = `<div class="event-empty">failed to load events: ${esc(error)}</div>`;
  }
}

function refreshOpenEventLogs() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`events-pane-${session}`);
    if (pane?.classList.contains('active')) refreshEventLog(session);
  }
}

function postEvent(session, type, message, details = {}) {
  apiFetch('/api/event', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session, type, message, details}),
  }).then(() => {
    refreshOpenEventLogs();
  }).catch(() => {});
}

function normalizeRole(role) {
  const value = String(role || 'message').toLowerCase();
  if (value.includes('tool_use')) return 'tool_use';
  if (value.includes('tool_result')) return 'tool_result';
  if (value.includes('assistant')) return 'assistant';
  if (value.includes('user')) return 'user';
  if (value.includes('summary')) return 'summary';
  if (value.includes('system')) return 'system';
  return 'system';
}

function renderLatency(latestMs) {
  const samples = latencySamples.slice(-latencySamplesMax);
  if (samples.length === 0) {
    latencyLine.setAttribute('points', '');
  } else {
    const maxMs = Math.max(100, ...samples);
    const width = 44;
    const height = 18;
    const points = samples.map((value, index) => {
      const x = samples.length === 1 ? width : (index / (samples.length - 1)) * width;
      const y = height - 1 - (Math.min(value, maxMs) / maxMs) * (height - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    latencyLine.setAttribute('points', points.join(' '));
  }

  latencyMeter.classList.remove('good', 'warn', 'bad');
  if (latestMs == null) {
    latencyMeter.classList.add('bad');
    latencyNumber.textContent = '-- ms';
    return;
  }
  latencyNumber.textContent = `${latestMs} ms`;
  if (latestMs <= 80) {
    latencyMeter.classList.add('good');
  } else if (latestMs <= 200) {
    latencyMeter.classList.add('warn');
  } else {
    latencyMeter.classList.add('bad');
  }
}

async function updateLatency() {
  const startedAt = performance.now();
  try {
    const response = await apiFetch(`/api/ping?t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(response.statusText || `HTTP ${response.status}`);
    await response.json();
    const elapsedMs = Math.max(1, Math.round(performance.now() - startedAt));
    latencySamples = [...latencySamples, elapsedMs].slice(-latencySamplesMax);
    renderLatency(elapsedMs);
  } catch (_) {
    renderLatency(null);
  }
}

function refreshAll() {
  refreshTranscripts();
  refreshAutoStatuses();
  refreshWatchedFilesystem();
}

async function boot() {
  applyFileExplorerStaticLabels();
  renderTransportWarning();
  renderTabMetaToggle();
  bindTopbarMetrics();
  syncInitialLayoutUrl();
  statusEl.textContent = 'loading YOLO status...';
  await loadNotifyStatus();
  await loadAutoStatuses();
  renderSessionButtons();
  renderPanels([], {prune: false});
  await Promise.all(activeSessions.filter(isTmuxSession).map(session => ensureTerminalRunning(session)));
  refreshTranscripts();
  renderAutoApproveButtons();
  updateLatency();
  setInterval(refreshAutoStatuses, paneStateRefreshMs);
  setInterval(refreshTranscripts, metadataRefreshMs);
  setInterval(updateLatency, latencyRefreshMs);
  setInterval(refreshOpenEventLogs, eventLogRefreshMs);
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  title.textContent = `${sessionLabel(session)} transcript tail`;
  body.textContent = 'loading...';
  modal.classList.add('open');
  const response = await apiFetch(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  const payload = await response.json();
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
}

if (refreshMeta) {
  refreshMeta.textContent = 'Refresh';
  refreshMeta.setAttribute('aria-label', 'Refresh session state');
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
  refreshMeta.onclick = refreshAll;
}
if (tabMetaToggle) {
  tabMetaToggle.onclick = toggleTabMetadata;
  // Restore the `#` tab-metadata toggle to the top-right cluster, just left of Notify.
  notifyToggle?.parentElement?.insertBefore(tabMetaToggle, notifyToggle);
}
if (logoutButton) logoutButton.onclick = () => { window.location.href = '/logout'; };
notifyToggle.onclick = toggleNotifications;
document.getElementById('closeModal').onclick = () => document.getElementById('modal').classList.remove('open');
document.addEventListener('click', event => {
  if (event.target?.closest?.('.app-menu')) return;
  closeAppMenus();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeAppMenus();
});
window.addEventListener('resize', () => {
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

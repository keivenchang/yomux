const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
const accessRole = bootstrap.accessRole || 'admin';
const readOnlyMode = accessRole !== 'admin';
const homePath = bootstrap.homePath;
const serverHostname = bootstrap.serverHostname;
const grid = document.getElementById('grid');
const panelPool = document.getElementById('panelPool');
const sessionButtons = document.getElementById('sessionButtons');
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
const fileExplorerClose = document.getElementById('fileExplorerClose');
const fileExplorerExpanded = new Set();
let fileExplorerRoot = null;
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
const popoverHideDelayMs = 1000;
const terminalFitBottomReservePx = 2;
const terminalWheelScrollLines = 3;
const terminalWheelPageFraction = 0.85;
const maxSessionTabs = bootstrap.maxSessionTabs;
const baseWindowKeys = ['left', 'right'];
const splitWindowKeys = ['leftTop', 'leftBottom', 'rightTop', 'rightBottom'];
const windowKeys = [...baseWindowKeys, ...splitWindowKeys];
const layoutTreeKey = '__tree';
const layoutTreeParamPrefix = 'tree:';
const minSplitPaneWidthPx = 320;
const minSplitPaneHeightPx = 220;
const defaultSplitPercent = 50;
const minSplitPercent = 5;
const maxSplitPercent = 95;
const infoItemId = '__info__';
let visibleSessions = sessions.slice(0, maxSessionTabs);
let layoutItems = [infoItemId, ...visibleSessions];
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
let dragSession = null;
let dragSourceSlot = null;
let customDragPreview = null;
let customDragPreviewOffset = {x: 0, y: 0};
let transparentDragImage = null;
let terminalContextMenu = null;
const panelPopoverHideTimers = new WeakMap();
let clipboardPasteBound = false;
let pasteUploadInFlight = false;
let layoutResizeState = null;
let responsiveLayoutPruneTimer = null;
let latencySamples = [];
let tabMetaVisible = readStoredTabMetaVisible();
let authRedirectStarted = false;

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
}

function setFocusedTerminal(session) {
  focusedTerminal = session;
  focusedPanelItem = session;
  dismissAttentionAlertsForSession(session);
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
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
  if (isTmuxSession(item)) dismissAttentionAlertsForSession(item);
  updateSessionButtonStates();
  updatePanelInactiveOverlays();
}

function clearFocusForInactiveLayout() {
  if (focusedTerminal && !activeSessions.includes(focusedTerminal)) focusedTerminal = null;
  if (focusedPanelItem && !activeSessions.includes(focusedPanelItem)) focusedPanelItem = null;
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
    panel.classList.toggle('focused-window', item === focusedPanelItem);
    panel.classList.toggle('active-window', item === focusedPanelItem);
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
  if (clipboard?.writeText) {
    await clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
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

function closeTerminalContextMenu() {
  if (!terminalContextMenu) return;
  terminalContextMenu.remove();
  terminalContextMenu = null;
  document.removeEventListener('pointerdown', terminalContextMenuPointerdown, true);
  document.removeEventListener('keydown', terminalContextMenuKeydown, true);
  window.removeEventListener('blur', closeTerminalContextMenu);
}

function terminalContextMenuPointerdown(event) {
  if (terminalContextMenu?.contains(event.target)) return;
  closeTerminalContextMenu();
}

function terminalContextMenuKeydown(event) {
  if (event.key === 'Escape') closeTerminalContextMenu();
}

function positionTerminalContextMenu(menu, x, y) {
  const rect = menu.getBoundingClientRect();
  const left = Math.min(Math.max(6, x), Math.max(6, window.innerWidth - rect.width - 6));
  const top = Math.min(Math.max(6, y), Math.max(6, window.innerHeight - rect.height - 6));
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
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
  closeTerminalContextMenu();
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu';
  menu.setAttribute('role', 'menu');
  const items = [
    ['Copy', false],
    ['Copy without indent', true],
  ];
  for (const [label, dedent] of items) {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.textContent = label;
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyTerminalSelection(session, term, {dedent});
      closeTerminalContextMenu();
    });
    menu.appendChild(button);
  }
  menu.addEventListener('pointerdown', event => event.stopPropagation());
  document.body.appendChild(menu);
  terminalContextMenu = menu;
  positionTerminalContextMenu(menu, x, y);
  document.addEventListener('pointerdown', terminalContextMenuPointerdown, true);
  document.addEventListener('keydown', terminalContextMenuKeydown, true);
  window.addEventListener('blur', closeTerminalContextMenu);
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

function emptyLayoutSlots() {
  return {[layoutTreeKey]: null};
}

function emptyWindowState() {
  return {active: null, tabs: []};
}

function leafNode(slot) {
  return {slot};
}

function splitNode(direction, first, second, pct = defaultSplitPercent) {
  return {split: direction, pct: splitPercent(pct), children: [first, second]};
}

function splitPercent(value) {
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
  return Object.keys(slots || {}).filter(key => key !== layoutTreeKey && windowStack(key, slots).length);
}

function nextLayoutSlot(slots = layoutSlots) {
  const used = new Set(Object.keys(slots || {}));
  let index = 1;
  while (used.has(`slot${index}`)) index += 1;
  return `slot${index}`;
}

function normalizeWindowState(raw, seen) {
  const state = emptyWindowState();
  const items = Array.isArray(raw) ? raw : Array.isArray(raw?.tabs) ? raw.tabs : [];
  for (const value of items) {
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !seen.has(item)) {
      state.tabs.push(item);
      seen.add(item);
    }
  }
  const active = resolveLayoutItem(raw?.active);
  state.active = state.tabs.includes(active) ? active : state.tabs[0] || null;
  return state;
}

function normalizeLayoutSlots(value) {
  if (!value || typeof value !== 'object') return emptyLayoutSlots();
  if (value[layoutTreeKey]) return normalizeTreeLayout(value);
  return normalizeLegacyLayoutSlots(value);
}

function normalizeTreeLayout(value) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  next[layoutTreeKey] = normalizeLayoutNode(value[layoutTreeKey], value, next, seen);
  return compactLayoutSlots(next);
}

function normalizeLayoutNode(node, value, next, seen) {
  if (!node || typeof node !== 'object') return null;
  if (typeof node.slot === 'string') {
    next[node.slot] = normalizeWindowState(value[node.slot], seen);
    return leafNode(node.slot);
  }
  const direction = node.split === 'column' ? 'column' : 'row';
  const children = (node.children || []).map(child => normalizeLayoutNode(child, value, next, seen)).filter(Boolean);
  if (children.length >= 2) return splitNode(direction, children[0], children[1], node.pct);
  return children[0] || null;
}

function normalizeLegacyLayoutSlots(value) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (const side of windowKeys) {
    next[side] = normalizeWindowState(value[side], seen);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function legacyLayoutTree(slots) {
  const columns = baseWindowKeys.map(column => legacyColumnTree(column, slots)).filter(Boolean);
  if (columns.length >= 2) return splitNode('row', columns[0], columns[1]);
  return columns[0] || null;
}

function legacyColumnTree(column, slots) {
  if (windowStack(column, slots).length) return leafNode(column);
  const top = verticalSplitSlot(column, 'top');
  const bottom = verticalSplitSlot(column, 'bottom');
  const topNode = windowStack(top, slots).length ? leafNode(top) : null;
  const bottomNode = windowStack(bottom, slots).length ? leafNode(bottom) : null;
  if (topNode && bottomNode) return splitNode('column', topNode, bottomNode);
  return topNode || bottomNode;
}

function compactLayoutSlots(slots) {
  const next = emptyLayoutSlots();
  for (const key of layoutSlotKeys(slots)) next[key] = windowStateWithTabs(windowStack(key, slots), activeItemForSide(key, slots));
  next[layoutTreeKey] = compactLayoutNode(slots[layoutTreeKey], next);
  return next;
}

function compactLayoutNode(node, slots) {
  if (!node) return null;
  if (node.slot) return windowStack(node.slot, slots).length ? leafNode(node.slot) : null;
  const children = (node.children || []).map(child => compactLayoutNode(child, slots)).filter(Boolean);
  if (children.length >= 2) return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
  return children[0] || null;
}

function layoutFromSessionList(values) {
  const next = emptyLayoutSlots();
  let index = 0;
  const seen = new Set();
  for (const raw of values) {
    const item = resolveLayoutItem(raw);
    if (!isLayoutItem(item) || seen.has(item) || index >= baseWindowKeys.length) continue;
    const state = next[baseWindowKeys[index]] || emptyWindowState();
    state.tabs.push(item);
    state.active = item;
    next[baseWindowKeys[index]] = state;
    seen.add(item);
    index += 1;
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutFromParam(raw, tabsRaw = '') {
  const text = String(raw || '').trim();
  if (!text) return null;
  if (text.toLowerCase() === 'empty') return emptyLayoutSlots();
  if (text.startsWith(layoutTreeParamPrefix)) return treeLayoutFromParam(text.slice(layoutTreeParamPrefix.length));
  if (compactLayoutParamLooksLikeTree(text)) return compactTreeLayoutFromParam(text, tabsRaw);
  const namedSlotLayout = namedSlotLayoutFromParam(text, tabsRaw);
  if (namedSlotLayout) return namedSlotLayout;
  const sides = text.split(',');
  if (!sides.some(value => value.trim())) return null;
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (let index = 0; index < baseWindowKeys.length; index += 1) {
    const side = baseWindowKeys[index];
    for (const value of (sides[index] || '').split('+')) {
      if (!value.trim()) continue;
      const item = resolveLayoutItem(value.trim());
      if (isLayoutItem(item) && !seen.has(item)) {
        if (!next[side]) next[side] = emptyWindowState();
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
    if (!state || !windowStack(slot, {[slot]: state}).length) continue;
    next[slot] = state;
    leaves.push(leafNode(slot));
  }
  if (!leaves.length) return null;
  next[layoutTreeKey] = leaves.reduce((tree, leaf) => (tree ? splitNode('row', tree, leaf) : leaf), null);
  const normalized = normalizeLayoutSlots(next);
  return sessionsFromSlots(normalized).length ? normalized : null;
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
      next[slot] = windowStateWithTabs(tabs.map(resolveLayoutItem), active);
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
    next[slot] = tabStates.get(slot) || emptyWindowState();
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
  return keys.map(side => windowStack(side, slots).map(readableItemParam).join('+')).join(',');
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
    const active = activeItemForSide(slot, slots);
    const tabs = windowStack(slot, slots).map((item, index) => {
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
    for (const rawItem of part.slice(separator + 1).split(',')) {
      let token = rawItem.trim();
      if (!token) continue;
      const activeToken = token.endsWith('*');
      if (activeToken) token = token.slice(0, -1);
      const item = resolveLayoutItem(readableParamComponentDecode(token));
      if (isLayoutItem(item) && !tabs.includes(item)) {
        tabs.push(item);
        if (activeToken) active = item;
      }
    }
    result.set(slot, windowStateWithTabs(tabs, active));
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
    if (selected.length >= baseWindowKeys.length) break;
  }
  if (selected.length) return layoutFromSessionList(selected);
  return defaultLayoutSlots();
}

function defaultLayoutSlots() {
  const sorted = visibleSessions.slice().sort((left, right) => String(left).localeCompare(String(right)));
  const next = emptyLayoutSlots();
  if (!sorted.length) {
    next.left = windowStateWithTabs([infoItemId], infoItemId);
  } else {
    next.left = windowStateWithTabs(sorted, sorted[0]);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutWithItems(value, items, preferredSlot = null) {
  const next = normalizeLayoutSlots(value);
  const present = new Set(windowedItems(next));
  const missing = items.filter(item => isLayoutItem(item) && !present.has(item));
  if (!missing.length) return next;
  let slot = preferredSlot && layoutSlotKeys(next).includes(preferredSlot) ? preferredSlot : layoutSlotKeys(next)[0];
  if (!slot) {
    slot = 'left';
    next[layoutTreeKey] = leafNode(slot);
    next[slot] = emptyWindowState();
  }
  const tabs = [...windowStack(slot, next), ...missing];
  const active = activeItemForSide(slot, next) || tabs.find(isTmuxSession) || tabs[0] || null;
  next[slot] = windowStateWithTabs(tabs, active);
  return compactLayoutSlots(next);
}

function windowStack(side, slots = layoutSlots) {
  const state = slots?.[side];
  if (Array.isArray(state)) return state;
  return Array.isArray(state?.tabs) ? state.tabs : [];
}

function slotColumn(slot) {
  if (String(slot).startsWith('right')) return 'right';
  return 'left';
}

function verticalSplitSlot(column, position) {
  return `${column}${position === 'top' ? 'Top' : 'Bottom'}`;
}

function activeItemForSide(side, slots = layoutSlots) {
  const stack = windowStack(side, slots);
  const state = slots?.[side];
  const active = !Array.isArray(state) ? state?.active : null;
  return stack.includes(active) ? active : stack[0] || null;
}

function windowStateWithTabs(tabs, active = null) {
  const unique = [];
  for (const item of tabs) {
    if (isLayoutItem(item) && !unique.includes(item)) unique.push(item);
  }
  return {tabs: unique, active: unique.includes(active) ? active : unique[0] || null};
}

function windowedItems(slots = layoutSlots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    for (const item of windowStack(side, slots)) {
      if (!result.includes(item)) result.push(item);
    }
  }
  return result;
}

function itemInLayout(item, slots = layoutSlots) {
  return windowedItems(slots).includes(item);
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

function isTmuxSession(item) {
  return sessions.includes(item);
}

function isLayoutItem(item) {
  return layoutItems.includes(item);
}

function resolveLayoutItem(value) {
  if (value === 'info') return infoItemId;
  const text = String(value || '');
  if (sessions.includes(text)) return text;
  const ordinal = Number(text);
  if (Number.isInteger(ordinal) && ordinal > 0) return sessionForLabel(String(ordinal));
  return text;
}

function itemLabel(item) {
  return isInfoItem(item) ? 'Branch Info' : sessionLabel(item);
}

function itemSortNumber(item) {
  if (isInfoItem(item)) return 0;
  const label = Number(sessionLabel(item));
  return Number.isFinite(label) ? label : Number.MAX_SAFE_INTEGER;
}

function itemParam(item) {
  if (isInfoItem(item)) return 'info';
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
  if (agents.some(agent => agent.error) || /blocked|error|failed|failure|stuck/.test(agentText)) {
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
  const classes = ['session-state-badge', `session-state-${key}`];
  const attention = stateDef(key).attention;
  if (attention) classes.push('session-state-reminder');
  const style = attention ? ` style="${attentionAnimationStyle()}"` : '';
  return `<span class="${esc(classes.join(' '))}"${style} title="${esc(title)}">${esc(short)}</span>`;
}

function sessionStateHtml(state) {
  if (!state || ['working', 'tests-running', 'done', 'disconnected', 'yolo-approval'].includes(state.key)) return '';
  return stateBadgeHtml(state.key, state.short, `${state.label}: ${state.reason}`);
}

function sessionTrayItems() {
  const inWindow = new Set(windowedItems());
  return [infoItemId, ...visibleSessions]
    .filter(item => !inWindow.has(item))
    .sort((left, right) => itemSortNumber(left) - itemSortNumber(right) || itemLabel(left).localeCompare(itemLabel(right)));
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

function scheduleTabStripOverflowCheck(strip) {
  if (!strip) return;
  strip.classList.remove('tabs-overflowing');
  requestAnimationFrame(() => {
    strip.classList.toggle('tabs-overflowing', strip.scrollWidth > strip.clientWidth + 1);
  });
}

function scheduleAllTabStripOverflowChecks() {
  scheduleTabStripOverflowCheck(sessionButtons);
  for (const strip of document.querySelectorAll('.window-session-tabs')) {
    scheduleTabStripOverflowCheck(strip);
  }
}

function updateSessionList(nextSessions) {
  if (!Array.isArray(nextSessions)) return false;
  const previousItems = new Set(layoutItems);
  const next = [];
  for (const session of nextSessions) {
    if (typeof session === 'string' && session && !next.includes(session)) next.push(session);
  }
  const changed = next.length !== sessions.length || next.some((session, index) => session !== sessions[index]);
  if (!changed) return false;
  sessions = next;
  visibleSessions = sessions.slice(0, maxSessionTabs);
  layoutItems = [infoItemId, ...visibleSessions];
  const newItems = layoutItems.filter(item => !previousItems.has(item));
  layoutSlots = newItems.length ? layoutWithItems(layoutSlots, newItems) : normalizeLayoutSlots(layoutSlots);
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
  renderPanels(previousActive);
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
  const minimizedItems = sessionTrayItems();
  if (activeSessions.length || minimizedItems.length) {
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

function renderSessionButtons() {
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
  const trayItems = sessionTrayItems();
  if (shouldShowTabListMenu(trayItems)) sessionButtons.appendChild(createTabListMenu(trayItems, {kind: 'tray'}));
  if (tabMetaToggle) sessionButtons.appendChild(tabMetaToggle);
  for (const item of trayItems) {
    sessionButtons.appendChild(createTopSessionButton(item));
  }
  if (!readOnlyMode && visibleSessions.length < maxSessionTabs) {
    for (const agent of ['claude', 'codex', 'term']) {
      if (availableAgents.has(agent)) sessionButtons.appendChild(createAddSessionButton(agent));
    }
  }
  sessionButtons.appendChild(createFileExplorerButton());
  scheduleTabStripOverflowCheck(sessionButtons);
}

function createFileExplorerButton() {
  const wrapper = document.createElement('div');
  wrapper.className = 'session-button-wrap add-session';
  const button = document.createElement('button');
  button.className = 'session-button add-session file';
  button.type = 'button';
  button.title = 'Toggle File Explorer';
  button.innerHTML = '<span class="add-plus">+</span><span class="agent-icon file" aria-hidden="true">📁</span><span>File</span>';
  button.addEventListener('click', () => toggleFileExplorer());
  wrapper.appendChild(button);
  return wrapper;
}

function toggleFileExplorer() {
  if (!fileExplorer) return;
  const opening = fileExplorer.hasAttribute('hidden');
  if (opening) {
    fileExplorer.removeAttribute('hidden');
    document.body.classList.add('file-explorer-open');
    if (!fileExplorerRoot) openFileExplorerAt(homePath || '/');
  } else {
    fileExplorer.setAttribute('hidden', '');
    document.body.classList.remove('file-explorer-open');
  }
}

async function openFileExplorerAt(path) {
  fileExplorerRoot = path;
  if (fileExplorerPath) fileExplorerPath.textContent = path;
  if (fileExplorerTree) fileExplorerTree.replaceChildren();
  fileExplorerExpanded.clear();
  const entries = await fetchDirectory(path);
  if (!entries) return;
  renderTreeChildren(fileExplorerTree, path, entries, 0);
}

async function fetchDirectory(path) {
  try {
    const response = await apiFetch(`/api/fs/list?path=${encodeURIComponent(path)}`);
    if (!response.ok) {
      console.warn('fs list failed', path, response.status);
      return null;
    }
    const payload = await response.json();
    return payload.entries || [];
  } catch (err) {
    console.warn('fs list error', path, err);
    return null;
  }
}

function renderTreeChildren(container, parentPath, entries, depth) {
  for (const entry of entries) {
    const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
    const row = document.createElement('div');
    row.className = `file-tree-row kind-${entry.kind}`;
    row.dataset.path = fullPath;
    row.dataset.kind = entry.kind;
    row.style.paddingLeft = `${8 + depth * 14}px`;
    row.setAttribute('role', 'treeitem');
    const icon = entry.kind === 'dir' ? '▸' : (entry.kind === 'file' ? '📄' : '·');
    row.innerHTML = `<span class="file-tree-icon">${icon}</span><span class="file-tree-name">${esc(entry.name)}</span>`;
    row.addEventListener('click', event => {
      event.stopPropagation();
      onFileTreeRowClick(row, fullPath, entry);
    });
    container.appendChild(row);
  }
}

async function onFileTreeRowClick(row, fullPath, entry) {
  if (entry.kind === 'dir') {
    if (fileExplorerExpanded.has(fullPath)) {
      collapseDirectoryRow(row, fullPath);
    } else {
      await expandDirectoryRow(row, fullPath);
    }
    return;
  }
  if (entry.kind === 'file') {
    document.querySelectorAll('.file-tree-row.selected').forEach(el => el.classList.remove('selected'));
    row.classList.add('selected');
    // Phase 3 will open the CodeMirror editor here. For now, just log.
    console.log('file selected:', fullPath, entry);
  }
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

function shouldShowTabListMenu(items) {
  return Array.isArray(items) && items.length > 1;
}

function createTabListMenu(items, options = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tab-list-menu-wrap';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'tab-list-menu-button';
  button.setAttribute('aria-label', options.kind === 'tray' ? 'show inactive tabs' : 'show all tabs');
  wrapper.appendChild(button);
  const popover = document.createElement('div');
  popover.className = 'tab-list-menu-popover';
  popover.setAttribute('role', 'menu');
  popover.innerHTML = options.kind === 'tray' ? '<div class="tab-list-menu-title">Inactive tabs</div>' : '';
  for (const item of items) {
    popover.appendChild(createTabListEntry(item, options));
  }
  wrapper.appendChild(popover);
  bindTabListMenuPopover(wrapper);
  return wrapper;
}

function createTabListEntry(item, options = {}) {
  const side = options.side || null;
  const active = side ? item === activeItemForSide(side) : false;
  const entry = document.createElement('div');
  entry.role = 'button';
  entry.tabIndex = 0;
  entry.draggable = true;
  entry.className = `tab-list-entry ${active ? 'active' : ''}`;
  entry.dataset.tabListItem = item;
  entry.innerHTML = tabListEntryBodyHtml(item);
  entry.setAttribute('aria-label', `${itemLabel(item)} ${tabListDetailText(item)}`.trim());
  const activate = () => {
    closeOtherSessionPopovers(null);
    if (side) {
      activateWindowSession(side, item);
    } else {
      selectSession(item);
    }
  };
  entry.addEventListener('pointerdown', event => {
    const autoTarget = event.target.closest('[data-auto-session]');
    if (!autoTarget) return;
    event.preventDefault();
    event.stopPropagation();
  });
  entry.addEventListener('click', async event => {
    const autoTarget = event.target.closest('[data-auto-session]');
    if (!autoTarget) return;
    event.preventDefault();
    event.stopPropagation();
    await toggleAutoApprove(autoTarget.dataset.autoSession);
    entry.closest('.tab-list-menu-wrap')?.classList.add('popover-open');
  });
  bindTabActivation(entry, activate, {
    stopPropagation: true,
    ignore: event => Boolean(event.target.closest('[data-auto-session]')),
  });
  entry.addEventListener('dragstart', event => {
    event.stopPropagation();
    startSessionDrag(event, item, side);
  });
  entry.addEventListener('dragend', endSessionDrag);
  return entry;
}

function bindTabListMenuPopover(wrapper) {
  let showTimer = null;
  let hideTimer = null;
  const popover = wrapper.querySelector(':scope > .tab-list-menu-popover');
  if (!popover) return;
  const clearShowTimer = () => {
    showTimer = clearTimer(showTimer);
  };
  const clearHideTimer = () => {
    hideTimer = clearTimer(hideTimer);
  };
  const openNow = () => {
    clearShowTimer();
    clearHideTimer();
    positionTabListPopover(wrapper);
    closeOtherSessionPopovers(wrapper);
    wrapper.classList.add('popover-open');
  };
  const queueOpen = () => {
    clearHideTimer();
    if (wrapper.classList.contains('popover-open')) return;
    clearShowTimer();
    positionTabListPopover(wrapper);
    showTimer = setTimeout(openNow, popoverShowDelayMs);
  };
  const closeSoon = () => {
    clearShowTimer();
    clearHideTimer();
    hideTimer = setTimeout(() => {
      if (popoverStillActive(wrapper, popover)) {
        hideTimer = null;
        return;
      }
      wrapper.classList.remove('popover-open');
      hideTimer = null;
    }, popoverHideDelayMs);
  };
  bindPopoverHover(wrapper, popover, {queueOpen, keepOpen: openNow, closeSoon});
}

function positionTabListPopover(wrapper) {
  const rect = wrapper.getBoundingClientRect();
  const panel = wrapper.closest('.panel');
  const container = panel || wrapper.closest('.session-buttons') || wrapper.parentElement || wrapper;
  const containerRect = container.getBoundingClientRect();
  const width = Math.min(Math.max(320, containerRect.width), window.innerWidth - 16);
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const left = Math.min(Math.max(8, Math.floor(containerRect.left)), maxLeft);
  const panelHead = panel?.querySelector('.panel-head');
  const topBase = panelHead ? panelHead.getBoundingClientRect().bottom : rect.bottom;
  const top = Math.min(Math.ceil(topBase + 1), Math.max(8, window.innerHeight - 80));
  const maxHeight = Math.max(120, window.innerHeight - top - 8);
  document.documentElement.style.setProperty('--tab-list-popover-top', `${top}px`);
  document.documentElement.style.setProperty('--tab-list-popover-left', `${left}px`);
  document.documentElement.style.setProperty('--tab-list-popover-width', `${Math.floor(width)}px`);
  document.documentElement.style.setProperty('--tab-list-popover-max-height', `${Math.floor(maxHeight)}px`);
}

function updateSessionButtonStates() {
  for (const wrapper of sessionButtons.querySelectorAll('.session-button-wrap[data-session]')) {
    const item = wrapper.dataset.session;
    const button = wrapper.querySelector('.session-button');
    if (!button) continue;
    const isInfo = isInfoItem(item);
    const info = transcriptMeta.sessions?.[item];
    const state = isInfo ? null : sessionState(item, info);
    const autoPayload = autoApproveStates.get(item);
    const auto = autoApproveEnabledHere(autoPayload);
    const locked = autoApproveEnabledElsewhere(autoPayload);
    button.classList.toggle('auto', auto);
    button.classList.toggle('auto-elsewhere', locked);
    applySessionStateClasses(button, state);
  }
}

function createTopSessionButton(item) {
  const isInfo = isInfoItem(item);
  const info = transcriptMeta.sessions?.[item];
  const autoPayload = autoApproveStates.get(item);
  const auto = autoApproveEnabledHere(autoPayload);
  const locked = autoApproveEnabledElsewhere(autoPayload);
  const state = isInfo ? null : sessionState(item, info);
  const agentKind = isInfo ? '' : sessionAgentKind(item);
  const wrapper = document.createElement('div');
  wrapper.className = `session-button-wrap ${isInfo ? 'info' : ''}`;
  wrapper.dataset.session = item;
  const button = document.createElement('button');
  button.className = `session-button ${isInfo ? 'info' : ''} ${auto ? 'auto' : ''} ${locked ? 'auto-elsewhere' : ''}`;
  button.type = 'button';
  button.draggable = true;
  applySessionStateClasses(button, state);
  button.innerHTML = isInfo ? infoButtonHtml() : sessionButtonHtml(item, info, state, auto);
  button.removeAttribute('title');
  button.addEventListener('click', async event => {
    const autoTarget = event.target.closest('[data-auto-session]');
    if (!autoTarget) {
      event.preventDefault();
      await selectSession(item);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    await toggleAutoApprove(autoTarget.dataset.autoSession);
  });
  button.addEventListener('dragstart', event => startSessionDrag(event, item, null));
  button.addEventListener('dragend', endSessionDrag);
  wrapper.appendChild(button);
  if (!isInfo) {
    wrapper.insertAdjacentHTML('beforeend', sessionPopoverHtml(item, info, agentKind, auto, state));
    bindTopSessionPopover(wrapper);
  }
  return wrapper;
}

function createAddSessionButton(agent) {
  const wrapper = document.createElement('div');
  wrapper.className = 'session-button-wrap add-session';
  const button = document.createElement('button');
  button.className = `session-button add-session ${agent}`;
  button.type = 'button';
  button.innerHTML = `<span class="add-plus">+</span>${agentIcon(agent)}<span>${esc(agentName(agent))}</span>`;
  button.title = `create next numbered tmux session with ${agentName(agent)}`;
  button.addEventListener('click', () => createNextSession(agent));
  wrapper.appendChild(button);
  return wrapper;
}

function clearTimer(timer) {
  if (timer) clearTimeout(timer);
  return null;
}

function stopPopoverEvent(event) {
  event.stopPropagation();
}

function closeOtherSessionPopovers(current) {
  for (const other of document.querySelectorAll('.session-button-wrap.popover-open, .window-session-tab.popover-open, .tab-list-menu-wrap.popover-open')) {
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
  return `<span class="${metadataBadgeClasses(session, 'main', 'ci-indicator branch-indicator')}">MAIN</span>`;
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

function tabListDetailText(item, info = transcriptMeta.sessions?.[item]) {
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

function tabListEntryLineText(item, info = transcriptMeta.sessions?.[item]) {
  if (isInfoItem(item)) return itemLabel(item);
  const project = info?.project || {};
  const git = project.git || {};
  const pr = displayPullRequest(info);
  const parts = [];
  const title = pr?.title || pr?.description || '';
  if (title) parts.push(stripPullRequestSuffixText(title));
  else {
    const issue = (project.linear || []).find(value => value.title);
    const subject = currentBranchSubject(git);
    if (issue?.title) parts.push(`${issue.identifier}: ${issue.title}`);
    else if (subject) parts.push(stripPullRequestSuffixText(subject));
  }
  if (git.branch) parts.push(shortBranch(git.branch));
  const path = panelFullPath(item, info);
  if (path) parts.push(compactHomePath(path));
  const linear = project.linear || [];
  const linearText = linear
    .map(issue => [issue.identifier, issue.state].filter(Boolean).join(' '))
    .filter(Boolean)
    .join(', ');
  if (linearText) parts.push(linearText);
  const gitText = gitStatusText(git);
  if (gitText && gitText !== 'clean') parts.push(gitText);
  if (!parts.length && git.branch) parts.push(shortBranch(git.branch));
  if (!parts.length) parts.push(projectDirName(item, info));
  return shortText(parts.filter(Boolean).join(' · '), 220);
}

function tabListEntryBodyHtml(item) {
  if (isInfoItem(item)) {
    return `<span class="tab-list-entry-main">${windowInfoTabHtml()}</span>`;
  }
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true;
  const state = sessionState(item, info);
  const pr = displayPullRequest(info);
  const desc = tabListEntryLineText(item, info);
  const descHtml = desc ? `<span class="session-button-dir">${esc(desc)}</span>` : '';
  return `<span class="tab-list-entry-main">
    ${yoloMarkerHtml(item, auto, {enabledOnly: false, toggle: true, yoloWorking: sessionYoloIsWorking(item)})}
    <span class="session-button-prefix">${sessionNumberNameHtml(item)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(item, info)}${pullRequestCompactBadgesHtml(item, pr)}${descHtml}</span>
  </span>`;
}

function infoButtonHtml() {
  return '<span class="session-button-text"><span class="session-button-dir">Branch Info</span></span>';
}

function sessionButtonHtml(session, info, state, auto) {
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(session, info);
  const detailHtml = desc ? `<span class="session-button-dir">${esc(desc)}</span>` : '';
  return `${yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: true, yoloWorking: sessionYoloIsWorking(session)})}<span class="session-button-prefix">${sessionNumberNameHtml(session)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(session, info)}${pullRequestCompactBadgesHtml(session, pr)}${detailHtml}</span>`;
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
  const raw = event.dataTransfer?.getData('application/x-yolomux-session')
    || event.dataTransfer?.getData('text/plain')
    || '';
  if (!raw && dragSession) return {session: dragSession, sourceSlot: dragSourceSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isLayoutItem(parsed.session) ? parsed : null;
  } catch (_) {
    return isLayoutItem(raw) ? {session: raw, sourceSlot: null} : null;
  }
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
  clone.querySelectorAll?.('.session-popover, .tab-list-menu-popover').forEach(node => node.remove());
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

function layoutWithoutItem(item) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    const active = activeItemForSide(side);
    next[side] = windowStateWithTabs(windowStack(side).filter(value => value !== item), active === item ? null : active);
  }
  return next;
}

function removeSessionFromLayout(item) {
  if (!itemInLayout(item)) return;
  applyLayoutSlots(layoutWithoutItem(item), {
    message: `${itemLabel(item)} moved to top strip`,
  });
}

function removeWindowFromLayout(item) {
  const slot = slotForSession(item);
  if (!slot) return;
  const moved = windowStack(slot);
  applyLayoutSlots(layoutWithoutSlot(slot), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} moved to top strip` : '',
  });
}

function layoutWithoutSlot(slot) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (side === slot) continue;
    next[side] = windowStateWithTabs(windowStack(side), activeItemForSide(side));
  }
  return next;
}

function firstEmptyWindow() {
  return layoutSlotKeys().length ? null : 'left';
}

function slotForNewSession() {
  const empty = firstEmptyWindow();
  if (empty) return empty;
  const focusedSlot = focusedPanelItem ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return 'left';
}

async function moveSessionToSlot(session, targetSlot, sourceSlot = null, insertIndex = 0) {
  if (!isLayoutItem(session) || !targetSlot) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(targetSlot);
  if (!next[targetSlot]) next[targetSlot] = emptyWindowState();
  const tabs = next[targetSlot].tabs;
  const index = Math.max(0, Math.min(Number.isFinite(insertIndex) ? insertIndex : 0, tabs.length));
  tabs.splice(index, 0, session);
  next[targetSlot] = windowStateWithTabs(tabs, session);
  applyLayoutSlots(next, {focusSession: session});
}

async function dropSessionWithIntent(session, intent, sourceSlot = null) {
  if (!intent?.targetSlot || intent.zone === 'middle') {
    await moveSessionToSlot(session, intent?.targetSlot || slotForNewSession(), sourceSlot);
    return;
  }
  await splitSessionAtSlot(session, intent.targetSlot, intent.zone, sourceSlot);
}

async function splitSessionAtSlot(session, targetSlot, zone, sourceSlot = null) {
  if (!isLayoutItem(session) || !targetSlot || !['top', 'bottom', 'left', 'right'].includes(zone)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = layoutWithoutItem(session);
  const targetTabs = windowStack(targetSlot, next);
  if (!targetTabs.length) {
    await moveSessionToSlot(session, targetSlot, sourceSlot);
    return;
  }
  const newSlot = nextLayoutSlot(next);
  next[newSlot] = windowStateWithTabs([session], session);
  const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const existingNode = leafNode(targetSlot);
  const newNode = leafNode(newSlot);
  const replacement = zone === 'right' || zone === 'bottom'
    ? splitNode(direction, existingNode, newNode)
    : splitNode(direction, newNode, existingNode);
  next[layoutTreeKey] = replaceLayoutLeaf(next[layoutTreeKey], targetSlot, replacement);
  applyLayoutSlots(next, {focusSession: session});
}

function replaceLayoutLeaf(node, slot, replacement) {
  if (!node) return replacement;
  if (node.slot) return node.slot === slot ? replacement : node;
  const children = (node.children || []).map(child => replaceLayoutLeaf(child, slot, replacement));
  return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
}

function activateWindowSession(side, session) {
  if (!layoutSlotKeys().includes(side) || !itemInLayout(session)) return;
  if (activeItemForSide(side) === session) {
    focusPanel(session);
    return;
  }
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const key of layoutSlotKeys()) next[key] = windowStateWithTabs(windowStack(key), activeItemForSide(key));
  next[side].active = session;
  applyLayoutSlots(next, {focusSession: session});
}

async function selectSession(session) {
  if (activeSessions.includes(session)) {
    focusPanel(session);
    return;
  }
  const windowSlot = slotForSession(session);
  if (windowSlot) {
    activateWindowSession(windowSlot, session);
    return;
  }
  await moveSessionToSlot(session, slotForNewSession(), null);
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
  return `<span class="${metadataBadgeClasses(session, 'status', `ci-indicator ${pullRequestStatusClass(pr)}`)}">${pullRequestStatusDisplay(pr)}</span>`;
}

function pullRequestPrIndicatorHtml(session, pr) {
  if (!pr?.number) return '';
  return `<span class="${metadataBadgeClasses(session, 'pr', `ci-indicator pr-indicator ${pullRequestStatusClass(pr)}`)}">#${esc(pr.number)}</span>`;
}

function pullRequestCiIndicatorHtml(session, pr) {
  if (pullRequestStatusLabel(pr).toLowerCase() === 'merged') return '';
  const state = pr?.checks?.state;
  if (!state || state === 'unknown') return '';
  return `<span class="${metadataBadgeClasses(session, 'ci', `ci-indicator ${pullRequestStatusClass(pr)}`)}">CI</span>`;
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
    await moveSessionToSlot(payload.session, slotForNewSession(), null);
    await ensureTerminalRunning(payload.session);
    refreshTranscripts();
    renderAutoApproveButtons();
    statusEl.innerHTML = `<span class="ok">created ${esc(sessionLabel(payload.session))} (${esc(payload.session)}) with ${esc(agentName(payload.agent) || agentLabel)}</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">session create failed: ${esc(error)}</span>`;
  }
}

function focusPanel(session) {
  const panel = document.getElementById(`panel-${session}`);
  if (!panel) return;
  panel.scrollIntoView({block: 'nearest', inline: 'nearest'});
  if (isInfoItem(session)) {
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
    if (event.deltaY === 0) return;
    event.preventDefault();
    event.stopPropagation();
    const direction = event.deltaY < 0 ? -1 : 1;
    const amount = event.shiftKey
      ? Math.max(1, Math.floor(term.rows * terminalWheelPageFraction))
      : terminalWheelScrollLines;
    const item = terminals.get(session);
    if (!readOnlyMode && item?.socket?.readyState === WebSocket.OPEN) {
      queueTmuxScroll(item, direction * amount);
      return;
    }
    term.scrollLines(direction * amount);
  }, {capture: true, passive: false});
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

function slotForSession(session) {
  return layoutSlotKeys().find(side => windowStack(side).includes(session)) || null;
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

function clearDropPreview() {
  grid.querySelectorAll('.drag-over, .tab-drag-over, .tab-drop-preview, .drop-preview, .drop-preview-top, .drop-preview-bottom, .drop-preview-left, .drop-preview-right, .drop-preview-middle').forEach(node => {
    node.classList.remove('drag-over', 'tab-drag-over', 'tab-drop-preview', 'drop-preview', 'drop-preview-top', 'drop-preview-bottom', 'drop-preview-left', 'drop-preview-right', 'drop-preview-middle');
    node.style?.removeProperty('--tab-drop-x');
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
  dropSessionWithIntent(payload.session, intent, payload.sourceSlot || slotForSession(payload.session));
}

function handleDropDragOver(event) {
  const payload = dragPayload(event);
  if (!payload?.session) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = 'move';
  if (event.target.closest('.panel-head')) {
    clearDropPreview();
    return;
  }
  showDropPreview(dropIntentForEvent(event));
}

function handleDropDragLeave(event) {
  const current = event.currentTarget;
  if (current?.contains(event.relatedTarget)) return;
  clearDropPreview();
}

function renderPanels(previousActive = [], options = {}) {
  movePanelsToPool();
  const activeWindowCount = layoutSlotKeys().filter(side => activeItemForSide(side)).length;
  grid.className = `grid ${activeWindowCount === 1 ? 'full' : ''} ${activeWindowCount === 0 ? 'empty' : ''}`.trim();
  grid.innerHTML = '';
  const tree = layoutSlots[layoutTreeKey];
  if (tree) grid.appendChild(renderLayoutRoot(tree));

  bindDropTargets();
  syncPanelVisibility(previousActive);
  renderAutoApproveButtons();
  if (options.prune !== false) scheduleResponsiveLayoutPrune();
}

function movePanelsToPool() {
  for (const panel of panelNodes.values()) {
    panel.classList.remove('active-window');
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
  const moved = windowStack(candidate.slot);
  applyLayoutSlots(layoutWithoutSlot(candidate.slot), {
    message: moved.length ? `${moved.map(itemLabel).join(', ')} moved to top strip: not enough room` : '',
  });
}

function smallLayoutSlotCandidate() {
  let candidate = null;
  for (const column of grid.querySelectorAll('.layout-column[data-slot]')) {
    const slot = column.dataset.slot;
    if (!slot || !windowStack(slot).length) continue;
    const rect = column.getBoundingClientRect();
    const tooSmall = rect.width < minSplitPaneWidthPx || rect.height < minSplitPaneHeightPx;
    if (!tooSmall) continue;
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    if (!candidate || area < candidate.area) candidate = {slot, area};
  }
  return candidate;
}

function renderLayoutColumn(side) {
  const column = document.createElement('section');
  const session = activeItemForSide(side);
  column.className = 'layout-column';
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
  const panel = getOrCreatePanel(session);
  updatePanelSlot(panel, session, slot);
  node.appendChild(panel);
  return node;
}

function renderWindowTabStrips() {
  for (const side of layoutSlotKeys()) {
    const session = activeItemForSide(side);
    if (!session) continue;
    const panel = panelNodes.get(session);
    if (panel) updateWindowTabStrip(panel, side);
  }
}

function updateWindowTabStrip(panel, side) {
  const strip = panel.querySelector('.window-session-tabs');
  if (!strip) return;
  const stack = windowStack(side);
  strip.dataset.side = side;
  const children = shouldShowTabListMenu(stack) ? [createTabListMenu(stack, {kind: 'window', side})] : [];
  strip.replaceChildren(...children, ...stack.map(item => createWindowSessionTab(side, item)));
  bindWindowTabStrip(strip, side);
  scheduleTabStripOverflowCheck(strip);
}

function createWindowSessionTab(side, item) {
  const isInfo = isInfoItem(item);
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true;
  const state = isInfo ? null : sessionState(item, info);
  const agentKind = isInfo ? '' : sessionAgentKind(item);
  const active = item === activeItemForSide(side);
  const tab = document.createElement('div');
  tab.role = 'button';
  tab.tabIndex = 0;
  tab.className = `window-session-tab ${active ? 'active' : ''}`;
  applySessionStateClasses(tab, state);
  tab.draggable = true;
  tab.dataset.windowSessionTab = item;
  tab.innerHTML = isInfo ? windowInfoTabHtml() : windowSessionTabHtml(item, info, state, auto);
  tab.insertAdjacentHTML('beforeend', `<button type="button" class="window-tab-close" data-window-tab-close title="move ${esc(itemLabel(item))} to top strip" aria-label="Move ${esc(itemLabel(item))} to top strip"></button>`);
  if (!isInfo) {
    tab.insertAdjacentHTML('beforeend', sessionPopoverHtml(item, info, agentKind, auto, state));
    bindWindowSessionPopover(tab, item);
  }
  tab.setAttribute('aria-label', isInfo ? 'Branch Info' : `${sessionLabel(item)} ${sessionWorkDescription(item, info, 140)}`.trim());
  tab.addEventListener('pointerdown', event => {
    if (event.target.closest('[data-window-tab-close]')) {
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
    if (event.target.closest('[data-window-tab-close]')) {
      event.preventDefault();
      event.stopPropagation();
      removeSessionFromLayout(item);
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
    activateWindowSession(side, item);
  });
  bindTabActivation(tab, () => activateWindowSession(side, item), {
    stopPropagation: true,
    ignore: event => Boolean(event.target.closest('[data-auto-session], [data-window-tab-close]')),
  });
  tab.addEventListener('dragstart', event => {
    event.stopPropagation();
    startSessionDrag(event, item, side);
  });
  tab.addEventListener('dragend', endSessionDrag);
  return tab;
}

function bindWindowSessionPopover(tab, session) {
  let showTimer = null;
  let hideTimer = null;
  const popover = tab.querySelector(':scope > .session-popover');
  if (!popover) return;
  const clearShowTimer = () => {
    showTimer = clearTimer(showTimer);
  };
  const clearHideTimer = () => {
    hideTimer = clearTimer(hideTimer);
  };
  const openNow = () => {
    clearShowTimer();
    clearHideTimer();
    positionWindowSessionPopover(tab);
    closeOtherSessionPopovers(tab);
    tab.classList.add('popover-open');
  };
  const queueOpen = () => {
    clearHideTimer();
    if (tab.classList.contains('popover-open')) return;
    clearShowTimer();
    positionWindowSessionPopover(tab);
    showTimer = setTimeout(openNow, popoverShowDelayMs);
  };
  const keepOpen = () => {
    openNow();
  };
  const closeSoon = () => {
    clearShowTimer();
    clearHideTimer();
    hideTimer = setTimeout(() => {
      if (popoverStillActive(tab, popover)) {
        hideTimer = null;
        return;
      }
      tab.classList.remove('popover-open');
      hideTimer = null;
    }, popoverHideDelayMs);
  };
  bindPopoverHover(tab, popover, {queueOpen, keepOpen, closeSoon});
}

function bindTopSessionPopover(wrapper) {
  let showTimer = null;
  let hideTimer = null;
  const popover = wrapper.querySelector(':scope > .session-popover');
  if (!popover) return;
  const clearShowTimer = () => {
    showTimer = clearTimer(showTimer);
  };
  const clearHideTimer = () => {
    hideTimer = clearTimer(hideTimer);
  };
  const openNow = () => {
    clearShowTimer();
    clearHideTimer();
    positionTopSessionPopover(wrapper);
    closeOtherSessionPopovers(wrapper);
    wrapper.classList.add('popover-open');
  };
  const queueOpen = () => {
    clearHideTimer();
    if (wrapper.classList.contains('popover-open')) return;
    clearShowTimer();
    positionTopSessionPopover(wrapper);
    showTimer = setTimeout(openNow, popoverShowDelayMs);
  };
  const closeSoon = () => {
    clearShowTimer();
    clearHideTimer();
    hideTimer = setTimeout(() => {
      if (popoverStillActive(wrapper, popover)) {
        hideTimer = null;
        return;
      }
      wrapper.classList.remove('popover-open');
      hideTimer = null;
    }, popoverHideDelayMs);
  };
  bindPopoverHover(wrapper, popover, {queueOpen, keepOpen: openNow, closeSoon});
}

function positionWindowSessionPopover(tab) {
  const rect = tab.getBoundingClientRect();
  const width = Math.min(640, Math.max(320, window.innerWidth - 16));
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const left = Math.min(Math.max(8, Math.floor(rect.left)), maxLeft);
  document.documentElement.style.setProperty('--window-tab-popover-top', `${Math.ceil(rect.bottom + 1)}px`);
  document.documentElement.style.setProperty('--window-tab-popover-left', `${left}px`);
}

function positionTopSessionPopover(wrapper) {
  const rect = wrapper.getBoundingClientRect();
  const width = Math.min(640, Math.max(320, window.innerWidth - 16));
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const left = Math.min(Math.max(8, Math.floor(rect.left)), maxLeft);
  document.documentElement.style.setProperty('--top-button-popover-top', `${Math.ceil(rect.bottom + 1)}px`);
  document.documentElement.style.setProperty('--top-button-popover-left', `${left}px`);
}

function windowInfoTabHtml() {
  return '<span class="window-tab-core"><span class="window-tab-info-label">Branch Info</span></span>';
}

function windowSessionTabHtml(session, info, state, auto) {
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(session, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="window-tab-core">${yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: true, yoloWorking: sessionYoloIsWorking(session)})}<span class="session-button-prefix">${sessionNumberNameHtml(session)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(session, info)}${pullRequestCompactBadgesHtml(session, pr)}${detailHtml}</span></span>`;
}

function bindWindowTabStrip(strip, side) {
  strip.ondragover = event => {
    const payload = dragPayload(event);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    event.dataTransfer.dropEffect = 'move';
    clearDropPreview();
    showWindowTabDropPreview(strip, event, payload.session);
  };
  strip.ondragleave = event => {
    event.stopImmediatePropagation();
    if (!strip.contains(event.relatedTarget)) clearWindowTabDropPreview(strip);
  };
  strip.ondrop = event => {
    const payload = dragPayload(event);
    clearWindowTabDropPreview(strip);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    moveSessionToSlot(payload.session, side, payload.sourceSlot || slotForSession(payload.session), windowTabDropIndex(strip, event, payload.session));
  };
}

function showWindowTabDropPreview(strip, event, movingSession) {
  const placement = windowTabDropPlacement(strip, event, movingSession);
  strip.style.setProperty('--tab-drop-x', `${Math.round(placement.x)}px`);
  strip.classList.add('drag-over', 'tab-drop-preview');
}

function clearWindowTabDropPreview(strip) {
  strip.classList.remove('drag-over', 'tab-drop-preview');
  strip.style.removeProperty('--tab-drop-x');
}

function windowTabDropPlacement(strip, event, movingSession) {
  const tabs = Array.from(strip.querySelectorAll('.window-session-tab'))
    .filter(tab => tab.dataset.windowSessionTab !== movingSession);
  const stripRect = strip.getBoundingClientRect();
  const clampX = value => Math.max(2, Math.min(stripRect.width - 2, value));
  if (!tabs.length) return {index: 0, x: clampX(event.clientX - stripRect.left)};
  for (let index = 0; index < tabs.length; index += 1) {
    const rect = tabs[index].getBoundingClientRect();
    if (event.clientX < rect.left + rect.width / 2) {
      return {index, x: clampX(rect.left - stripRect.left)};
    }
  }
  const lastRect = tabs[tabs.length - 1].getBoundingClientRect();
  return {index: tabs.length, x: clampX(lastRect.right - stripRect.left)};
}

function windowTabDropIndex(strip, event, movingSession) {
  return windowTabDropPlacement(strip, event, movingSession).index;
}

function getOrCreatePanel(session) {
  let panel = panelNodes.get(session);
  if (panel) return panel;
  panel = isInfoItem(session) ? createInfoPanel() : createPanel(session);
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
      if (event.target.closest('.window-session-tabs')) return;
      event.dataTransfer.dropEffect = 'move';
      head.classList.add('tab-drag-over');
    });
    head.addEventListener('dragleave', event => {
      if (!head.contains(event.relatedTarget)) head.classList.remove('tab-drag-over');
    });
    head.addEventListener('drop', event => {
      const payload = dragPayload(event);
      head.classList.remove('tab-drag-over');
      if (!payload?.session || event.target.closest('.window-session-tabs')) return;
      event.preventDefault();
      event.stopPropagation();
      const targetSlot = head.dataset.dragSlot || slotForSession(session);
      if (!targetSlot) return;
      moveSessionToSlot(payload.session, targetSlot, payload.sourceSlot || slotForSession(payload.session), windowStack(targetSlot).length);
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
  const label = terminalProcessLabel(info);
  return shortText(label || 'Term', 16);
}

function terminalTabTitle(session, info) {
  if (isInfoItem(session)) return 'unavailable for Branch Info';
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

function previewTmuxWindowInfo(info, key) {
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  if (panes.length < 2) return null;
  const windows = [];
  const seen = new Set();
  for (const pane of panes) {
    const index = tmuxWindowNumber(pane.window);
    if (index === null || seen.has(index)) continue;
    seen.add(index);
    windows.push({index, pane});
  }
  windows.sort((left, right) => left.index - right.index);
  if (windows.length < 2) return null;
  const activePane = terminalDisplayPane(info);
  const activeIndex = tmuxWindowNumber(activePane?.window);
  const current = Math.max(0, windows.findIndex(item => item.index === activeIndex));
  const delta = key === 'p' ? -1 : 1;
  const target = windows[(current + delta + windows.length) % windows.length];
  const nextPanes = panes.map(pane => ({...pane, window_active: tmuxWindowNumber(pane.window) === target.index}));
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
  if (!button) return;
  button.textContent = terminalTabLabel(session, info);
  button.title = terminalTabTitle(session, info);
}

function installPanelInactiveOverlays(panel, session) {
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
        <div class="window-session-tabs" role="tablist" aria-label="Tabs"></div>
        ${panelControlsHtml(infoItemId, {disabled: true, unavailableLabel: 'Branch Info'})}
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

function panelControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = disabled ? ` type="button" disabled title="unavailable for ${esc(unavailableLabel)}"` : '';
  const readonlyAttrs = label => ` type="button" disabled title="${esc(label)} requires admin access"`;
  const stepAttrs = dir => {
    if (disabled) return disabledAttrs;
    if (readOnlyMode) return readonlyAttrs(`${dir === 'prev' ? 'previous' : 'next'} tmux window`);
    return ` data-window-dir="${dir}" data-window-session="${esc(session)}" title="${dir === 'prev' ? 'previous' : 'next'} tmux window"`;
  };
  const tabAttrs = name => {
    if (disabled) return disabledAttrs;
    if (readOnlyMode && name === 'summary') return readonlyAttrs('AI summary');
    return ` data-tab="${esc(session)}" data-tab-name="${name}"`;
  };
  const infoAttrs = disabled ? disabledAttrs : ` data-detail-toggle="${esc(session)}" title="hide details"`;
  const terminalAttrs = disabled ? disabledAttrs : `${tabAttrs('terminal')} title="${esc(terminalTabTitle(session, transcriptMeta.sessions?.[session]))}"`;
  const terminalLabel = disabled ? 'Term' : terminalTabLabel(session, transcriptMeta.sessions?.[session]);
  const closeAttrs = ` type="button" data-window-close="${esc(session)}" title="move all tabs in this window to top strip" aria-label="Move all tabs in this window to top strip"`;
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          <button class="tab window-step" ${stepAttrs('prev')}>&lt;</button>
          <button class="tab active terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>
          <button class="tab window-step" ${stepAttrs('next')}>&gt;</button>
          <button class="tab" ${tabAttrs('transcript')}>Tx</button>
          <button class="tab" ${tabAttrs('summary')}>AI</button>
          <button class="tab" ${tabAttrs('events')}>Log</button>
          <button class="tab panel-detail-toggle active" ${infoAttrs}>Info</button>
          <button class="tab window-close" ${closeAttrs}></button>
        </div>`;
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = `panel-${session}`;
  panel.innerHTML = `
      <div class="panel-head">
        <div class="window-session-tabs" role="tablist" aria-label="Tabs"></div>
        ${panelControlsHtml(session)}
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
    button.addEventListener('click', () => {
      const key = button.dataset.windowDir === 'prev' ? 'p' : 'n';
      const label = button.dataset.windowDir === 'prev' ? 'previous window' : 'next window';
      tmuxWindow(button.dataset.windowSession, key, label);
    });
  });
  panel.querySelector('[data-window-close]')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    removeWindowFromLayout(event.currentTarget.dataset.windowClose);
  });
  panel.querySelector('[data-context]')?.addEventListener('click', () => showContext(session));
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-auto-session]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    toggleAutoApprove(target.dataset.autoSession || session);
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
  updateWindowTabStrip(panel, slot);
  updatePanelInactiveOverlays();
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
  panel?.classList.toggle('typing-ready-window', ready);
  panel?.classList.toggle('yolo-ready-window', ready && autoApproveStates.get(session)?.enabled === true);
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
      ? `<span class="err">YOLO on: ${esc(sessionLabel(session))}</span>`
      : `<span class="ok">YOLO off: ${esc(sessionLabel(session))}</span>`;
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
    for (const session of sessions) {
      const state = payload.sessions?.[session] || {target: session, enabled: false, last_action: 'off'};
      autoApproveStates.set(session, state);
    }
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
    button.closest('.session-button, .window-session-tab')?.classList.remove('is-working');
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
        preview.textContent = `path: ${agent.transcript}\nsession_id: ${agent.session_id || ''}\nstatus: ${agent.status || ''}\n\nloading recent transcript context...`;
        refreshTranscriptPreview(session, preview, {preserveScroll: false});
      } else if (agent?.error) {
        preview.textContent = agent.error;
      } else {
        preview.textContent = 'no agent transcript found';
      }
    }
    renderWindowTabStrips();
    trackSessionStateChanges();
    refreshOpenEventLogs();
  } catch (error) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      const meta = document.getElementById(`meta-${session}`);
      const preview = document.getElementById(`transcript-${session}`);
      if (meta) meta.innerHTML = `<span class="err">transcript lookup failed</span>`;
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
  panel?.classList.toggle('needs-input-window', state.key === 'needs-input');
  panel?.classList.toggle('needs-exec-window', state.key === 'needs-approval');
  panel?.classList.toggle('needs-blocked-window', state.key === 'blocked');
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

async function refreshTranscriptPreview(session, preview, options = {}) {
  try {
    const response = await apiFetch(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    const payload = await response.json();
    if (payload.items) {
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
  const pathBlock = `<div class="transcript-item system"><div class="transcript-role">transcript</div><div class="transcript-text">${esc(path)}</div></div>`;
  const blocks = items.map(item => transcriptItemHtml(item));
  container.innerHTML = pathBlock + blocks.join('');
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
}

async function boot() {
  renderTransportWarning();
  renderTabMetaToggle();
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
if (tabMetaToggle) tabMetaToggle.onclick = toggleTabMetadata;
if (logoutButton) logoutButton.onclick = () => { window.location.href = '/logout'; };
notifyToggle.onclick = toggleNotifications;
document.getElementById('closeModal').onclick = () => document.getElementById('modal').classList.remove('open');
window.addEventListener('resize', () => {
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

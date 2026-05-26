const bootstrap = JSON.parse(document.getElementById('yolomux-bootstrap').textContent);
let sessions = bootstrap.sessions;
const availableAgents = new Set(bootstrap.availableAgents);
const homePath = bootstrap.homePath;
const serverHostname = bootstrap.serverHostname;
const grid = document.getElementById('grid');
const panelPool = document.getElementById('panelPool');
const topbar = document.querySelector('.topbar');
const sessionButtons = document.getElementById('sessionButtons');
const statusEl = document.getElementById('status');
const attentionAlerts = document.getElementById('attentionAlerts');
const latencyMeter = document.getElementById('latencyMeter');
const latencyLine = document.getElementById('latencyLine');
const latencyNumber = document.getElementById('latencyNumber');
const notifyToggle = document.getElementById('notifyToggle');
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
const pasteLockStorageKey = 'yolomux.pasteUploadLock.v1';
const transcriptPreviewMessages = 200;
const remoteResizeDelayMs = 220;
const metadataRefreshMs = 15000;
const paneStateRefreshMs = 2000;
const latencyRefreshMs = 3000;
const latencySamplesMax = 24;
const toastDurationMs = 10000;
const toastMaxLines = 3;
const toastMaxLineChars = 180;
const popoverShowDelayMs = 1600;
const popoverHideDelayMs = 300;
const terminalFitBottomReservePx = 2;
const terminalWheelScrollLines = 3;
const terminalWheelPageFraction = 0.85;
const maxSessionTabs = bootstrap.maxSessionTabs;
const layoutStorageKey = 'yolomux.layoutSlots.v1';
const layoutSlotKeys = ['leftTop', 'rightTop', 'leftBottom', 'rightBottom'];
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
let attentionAlertSequence = 0;
let stateTrackingReady = false;
let focusedTerminal = null;
let focusedPanelItem = null;
let dragSession = null;
let dragSourceSlot = null;
let openPopoverSession = null;
let pendingPopoverSession = null;
let popoverShowTimer = null;
let popoverHideTimer = null;
const panelPopoverHideTimers = new WeakMap();
let sessionButtonsRenderDeferred = false;
let clipboardPasteBound = false;
let pasteUploadInFlight = false;
let latencySamples = [];

function setFocusedTerminal(session) {
  focusedTerminal = session;
  focusedPanelItem = session;
  dismissAttentionAlertsForSession(session);
  renderSessionButtons();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
}

function clearFocusedTerminal(session) {
  if (focusedTerminal !== session) return;
  focusedTerminal = null;
  focusedPanelItem = null;
  renderSessionButtons();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
}

function setFocusedPanelItem(item) {
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  if (isTmuxSession(item)) dismissAttentionAlertsForSession(item);
  renderSessionButtons();
  updatePanelInactiveOverlays();
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

function terminalLineLinks(lineText, y) {
  const links = [];
  terminalLinkPattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalLinkPattern)) {
    const raw = match[0] || '';
    const text = trimTerminalLinkCandidate(raw);
    if (!text) continue;
    const startIndex = (match.index || 0) + raw.indexOf(text);
    const endIndex = startIndex + text.length;
    links.push({
      text,
      range: {
        start: {x: startIndex + 1, y},
        end: {x: endIndex, y},
      },
      activate: () => openTerminalLink(text),
    });
  }
  return links;
}

function installTerminalLinkProvider(term) {
  if (typeof term.registerLinkProvider !== 'function') return;
  term.registerLinkProvider({
    provideLinks: (y, callback) => {
      try {
        const line = term.buffer?.active?.getLine(y - 1);
        if (!line) {
          callback([]);
          return;
        }
        callback(terminalLineLinks(line.translateToString(true), y));
      } catch (_) {
        callback([]);
      }
    },
  });
}

function emptyLayoutSlots() {
  return {leftTop: null, leftBottom: null, rightTop: null, rightBottom: null};
}

function normalizeLayoutSlots(value) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  if (!value || typeof value !== 'object') return next;
  for (const slot of layoutSlotKeys) {
    const item = resolveLayoutItem(value[slot]);
    if (isLayoutItem(item) && !seen.has(item)) {
      next[slot] = item;
      seen.add(item);
    }
  }
  return next;
}

function layoutFromSessionList(values) {
  const next = emptyLayoutSlots();
  const slots = ['leftTop', 'rightTop', 'leftBottom', 'rightBottom'];
  let index = 0;
  for (const item of values) {
    if (isLayoutItem(item) && !Object.values(next).includes(item) && index < slots.length) {
      next[slots[index]] = item;
      index += 1;
    }
  }
  return next;
}

function layoutFromParam(raw) {
  const values = String(raw || '').split(',');
  if (!values.some(value => value.trim())) return null;
  const next = emptyLayoutSlots();
  for (let index = 0; index < layoutSlotKeys.length; index += 1) {
    const value = values[index]?.trim() || '';
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !Object.values(next).includes(item)) next[layoutSlotKeys[index]] = item;
  }
  return sessionsFromSlots(next).length ? next : null;
}

function layoutParamValue(slots) {
  return layoutSlotKeys.map(slot => slots[slot] ? itemParam(slots[slot]) : '').join(',');
}

function initialLayoutSlots() {
  const params = new URLSearchParams(location.search);
  const layoutFromUrl = layoutFromParam(params.get('layout') || '');
  if (layoutFromUrl) return layoutFromUrl;
  const raw = params.get('sessions') || params.get('active') || '';
  const selected = [];
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !selected.includes(item)) selected.push(item);
    if (selected.length >= layoutSlotKeys.length) break;
  }
  if (selected.length) return layoutFromSessionList(selected);
  try {
    const stored = JSON.parse(localStorage.getItem(layoutStorageKey) || 'null');
    const normalized = normalizeLayoutSlots(stored);
    if (sessionsFromSlots(normalized).length) return normalized;
  } catch (_) {}
  return layoutFromSessionList(sessions.slice(0, 2));
}

function sessionsFromSlots(slots) {
  const result = [];
  for (const slot of layoutSlotKeys) {
    const session = slots[slot];
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
  return isInfoItem(item) ? 'Branches' : sessionLabel(item);
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
  const autoEnabled = auto.enabled === true;
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

function stateValue(key, reason) {
  const def = stateDef(key);
  return {key, ...def, reason};
}

function stateBadgeHtml(key, short, title) {
  return `<span class="session-state-badge session-state-${esc(key)}" title="${esc(title)}">${esc(short)}</span>`;
}

function sessionStateHtml(state) {
  if (!state || ['working', 'tests-running', 'done', 'disconnected', 'yolo-approval'].includes(state.key)) return '';
  return stateBadgeHtml(state.key, state.short, `${state.label}: ${state.reason}`);
}

function sessionTrayItems() {
  return [infoItemId, ...visibleSessions].sort((left, right) => itemSortNumber(left) - itemSortNumber(right) || itemLabel(left).localeCompare(itemLabel(right)));
}

function renderNotifyToggle() {
  if (!notifyToggle) return;
  const supported = 'Notification' in window;
  notifyToggle.disabled = false;
  notifyToggle.classList.toggle('active', notificationsEnabled);
  notifyToggle.setAttribute('aria-pressed', notificationsEnabled ? 'true' : 'false');
  const browserState = supported ? Notification.permission : 'unsupported';
  notifyToggle.title = `notify when a session needs attention; browser notifications: ${browserState}`;
}

async function toggleNotifications() {
  const nextEnabled = !notificationsEnabled;
  let browserPermission = 'unsupported';
  if (nextEnabled && 'Notification' in window && Notification.permission === 'default') {
    const permission = await Notification.requestPermission();
    browserPermission = permission;
  } else if ('Notification' in window) {
    browserPermission = Notification.permission;
  }
  try {
    const response = await fetch(`/api/notify?enabled=${nextEnabled ? '1' : '0'}`, {method: 'POST'});
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
    const response = await fetch('/api/notify', {cache: 'no-store'});
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

function showAttentionAlert(session, state) {
  const panelContainer = document.getElementById(`panel-toasts-${session}`);
  const node = showToast(
    `YOLOMux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`,
    state.reason,
    {
      container: panelContainer || attentionAlerts,
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
  return focusedPanelItem === session || focusedTerminal === session || expandedPanelItem() === session || activeSessions.length === 1;
}

function removeAttentionAlert(id) {
  if (attentionAlertTimers.has(id)) {
    clearTimeout(attentionAlertTimers.get(id));
    attentionAlertTimers.delete(id);
  }
  document.querySelector(`[data-alert-id="${id}"]`)?.remove();
}

function sendTestNotification() {
  showToast(`YOLOMux - ${serverHostname}: notifications enabled`, 'YOLOMux in-page alerts are enabled.');
  if (!notificationsEnabled || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(`YOLOMux - ${serverHostname}: notifications enabled`, {
      body: 'YOLOMux can send browser notifications from this server.',
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
    sendBrowserNotification(`YOLOMux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`, {
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

function updateSessionList(nextSessions) {
  if (!Array.isArray(nextSessions)) return false;
  const next = [];
  for (const session of nextSessions) {
    if (typeof session === 'string' && session && !next.includes(session)) next.push(session);
  }
  const changed = next.length !== sessions.length || next.some((session, index) => session !== sessions[index]);
  if (!changed) return false;
  sessions = next;
  visibleSessions = sessions.slice(0, maxSessionTabs);
  layoutItems = [infoItemId, ...visibleSessions];
  layoutSlots = normalizeLayoutSlots(layoutSlots);
  activeSessions = sessionsFromLayout();
  saveLayoutSlots();
  updateActiveSessionParam();
  return true;
}

function saveLayoutSlots() {
  try {
    localStorage.setItem(layoutStorageKey, JSON.stringify(layoutSlots));
  } catch (_) {}
}

function applyLayoutSlots(nextSlots, options = {}) {
  closeOpenSessionPopover({renderDeferred: false});
  const previousActive = activeSessions.slice();
  layoutSlots = normalizeLayoutSlots(nextSlots);
  activeSessions = sessionsFromLayout();
  saveLayoutSlots();
  updateActiveSessionParam();
  renderSessionButtons();
  renderPanels(previousActive);
  for (const session of activeSessions.filter(isTmuxSession)) ensureTerminalRunning(session);
  refreshTranscripts();
  renderAutoApproveButtons();
  if (options.focusSession && activeSessions.includes(options.focusSession)) {
    setTimeout(() => focusPanel(options.focusSession), 80);
  } else {
    updateStatus();
  }
}

function updateActiveSessionParam() {
  const params = new URLSearchParams(location.search);
  if (activeSessions.length) {
    params.set('sessions', activeSessions.map(itemParam).join(','));
    params.set('layout', layoutParamValue(layoutSlots));
  } else {
    params.delete('sessions');
    params.delete('layout');
  }
  params.delete('active');
  const query = params.toString();
  history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
}

function renderSessionButtons() {
  if (openPopoverSession) {
    sessionButtonsRenderDeferred = true;
    return;
  }
  sessionButtons.innerHTML = '';
  sessionButtons.ondragover = event => {
    const payload = dragPayload(event);
    if (!payload?.session || !activeSessions.includes(payload.session)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
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
  for (const session of sessionTrayItems()) {
    const isInfo = isInfoItem(session);
    const active = topTabIsActive(session);
    const shown = activeSessions.includes(session);
    const auto = autoApproveStates.get(session)?.enabled === true;
    const info = transcriptMeta.sessions?.[session];
    const agentKind = sessionAgentKind(session);
    const state = isInfo ? null : sessionState(session, info);
    const wrapper = document.createElement('div');
    wrapper.className = `session-button-wrap ${isInfo ? 'info' : ''}`;
    wrapper.dataset.session = session;
    const button = document.createElement('button');
    button.className = `session-button ${isInfo ? 'info' : ''} ${active ? 'active' : ''} ${shown ? 'shown' : ''} ${auto ? 'auto' : ''} ${state?.attention ? 'needs-attention' : ''}`;
    button.classList.toggle('needs-input', state?.key === 'needs-input');
    button.classList.toggle('needs-exec', state?.key === 'needs-approval');
    button.classList.toggle('needs-blocked', state?.key === 'blocked');
    button.draggable = true;
    button.innerHTML = isInfo ? infoButtonHtml() : sessionButtonHtml(session, info, agentKind, state, auto);
    button.removeAttribute('title');
    let handledOnPointerDown = false;
    button.addEventListener('pointerdown', event => {
      if (active) return;
      event.preventDefault();
      handledOnPointerDown = true;
      selectSession(session);
    });
    button.addEventListener('click', event => {
      if (handledOnPointerDown) {
        handledOnPointerDown = false;
        event.preventDefault();
        return;
      }
      selectSession(session);
    });
    button.addEventListener('dragstart', event => startSessionDrag(event, session, null));
    button.addEventListener('dragend', endSessionDrag);
    wrapper.appendChild(button);
    if (!isInfo) {
      wrapper.insertAdjacentHTML('beforeend', sessionPopoverHtml(session, info, agentKind, auto, state));
      bindSessionPopover(wrapper);
    } else {
      wrapper.addEventListener('pointerenter', () => closeOpenSessionPopover({renderDeferred: false}));
    }
    sessionButtons.appendChild(wrapper);
  }
  if (visibleSessions.length < maxSessionTabs) {
    for (const agent of ['claude', 'codex', 'term']) {
      if (availableAgents.has(agent)) sessionButtons.appendChild(createAddSessionButton(agent));
    }
  }
  updateTopbarPopoverGeometry();
}

function expandedPanelItem() {
  const panel = document.querySelector('.panel.expanded');
  if (!panel?.id?.startsWith('panel-')) return null;
  return panel.id.slice('panel-'.length);
}

function topTabIsActive(session) {
  const expanded = expandedPanelItem();
  if (expanded) return session === expanded;
  return session === focusedPanelItem || session === focusedTerminal;
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

function bindSessionPopover(wrapper) {
  const session = wrapper.dataset.session;
  wrapper.addEventListener('pointerenter', () => queueSessionPopover(session));
  wrapper.addEventListener('pointerleave', () => closeSessionPopoverSoon(session));
  wrapper.addEventListener('focusin', () => queueSessionPopover(session));
  wrapper.addEventListener('focusout', event => {
    if (wrapper.contains(event.relatedTarget)) return;
    closeSessionPopoverSoon(session);
  });
  const popover = wrapper.querySelector('.session-popover');
  popover?.addEventListener('pointerenter', () => keepSessionPopoverOpen(session));
  popover?.addEventListener('pointerleave', () => closeSessionPopoverSoon(session));
  popover?.querySelectorAll('a').forEach(link => {
    link.addEventListener('pointerenter', () => keepSessionPopoverOpen(session));
    link.addEventListener('click', event => event.stopPropagation());
  });
}

function updateTopbarPopoverGeometry(session = '') {
  const bottom = topbar?.getBoundingClientRect?.().bottom;
  if (Number.isFinite(bottom)) {
    document.documentElement.style.setProperty('--topbar-popover-top', `${Math.ceil(bottom + 4)}px`);
  }
  const wrapper = session ? sessionButtons.querySelector(`.session-button-wrap[data-session="${cssEscape(session)}"]`) : null;
  const rect = wrapper?.getBoundingClientRect?.();
  if (!rect) return;
  const width = Math.min(640, Math.max(320, window.innerWidth - 16));
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const left = Math.min(Math.max(8, Math.floor(rect.left)), maxLeft);
  document.documentElement.style.setProperty('--topbar-popover-left', `${left}px`);
}

function queueSessionPopover(session) {
  if (!session) return;
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = null;
  if (openPopoverSession === session) return;
  if (popoverShowTimer) clearTimeout(popoverShowTimer);
  pendingPopoverSession = session;
  updateTopbarPopoverGeometry(session);
  popoverShowTimer = setTimeout(() => {
    popoverShowTimer = null;
    if (pendingPopoverSession === session) openSessionPopoverNow(session);
  }, popoverShowDelayMs);
}

function keepSessionPopoverOpen(session) {
  if (!session) return;
  if (popoverShowTimer) clearTimeout(popoverShowTimer);
  popoverShowTimer = null;
  pendingPopoverSession = session;
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = null;
  if (openPopoverSession !== session) openSessionPopoverNow(session);
}

function openSessionPopoverNow(session) {
  if (!session) return;
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = null;
  pendingPopoverSession = session;
  updateTopbarPopoverGeometry(session);
  const targetSelector = `.session-button-wrap[data-session="${cssEscape(session)}"]`;
  for (const node of sessionButtons.querySelectorAll('.session-button-wrap')) {
    const isTarget = node.dataset.session === session;
    node.classList.toggle('popover-open', isTarget);
    if (isTarget) {
      node.classList.remove('popover-hide-now');
    } else if (node.classList.contains('popover-open') || node.querySelector('.session-popover')) {
      node.classList.add('popover-hide-now');
      window.setTimeout(() => node.classList.remove('popover-hide-now'), 120);
    }
  }
  openPopoverSession = session;
  sessionButtons.querySelector(targetSelector)?.classList.add('popover-open');
}

function closeSessionPopoverSoon(session) {
  if (!session) return;
  if (pendingPopoverSession === session) {
    if (popoverShowTimer) clearTimeout(popoverShowTimer);
    popoverShowTimer = null;
    pendingPopoverSession = null;
  }
  if (openPopoverSession !== session) return;
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = setTimeout(() => closeOpenSessionPopover(), popoverHideDelayMs);
}

function closeOpenSessionPopover(options = {}) {
  if (popoverShowTimer) clearTimeout(popoverShowTimer);
  popoverShowTimer = null;
  pendingPopoverSession = null;
  if (popoverHideTimer) clearTimeout(popoverHideTimer);
  popoverHideTimer = null;
  const session = openPopoverSession;
  openPopoverSession = null;
  for (const node of sessionButtons.querySelectorAll('.session-button-wrap.popover-open')) {
    node.classList.remove('popover-open');
  }
  if (options.renderDeferred === false) return;
  if (session || sessionButtonsRenderDeferred) {
    const shouldRender = sessionButtonsRenderDeferred;
    sessionButtonsRenderDeferred = false;
    if (shouldRender) {
      renderSessionButtons();
      renderAutoApproveButtons();
    }
  }
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
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
  if (!auto && options.enabledOnly !== false) return '';
  const classes = ['session-yolo-marker'];
  if (!auto) classes.push('inactive');
  const toggleAttr = options.toggle ? ` data-auto-session="${esc(session)}"` : '';
  const title = options.toggle ? `YOLO ${auto ? 'on' : 'off'} for ${sessionLabel(session)}` : 'YOLO enabled';
  return `<span class="${esc(classes.join(' '))}"${toggleAttr} title="${esc(title)}">YO</span>`;
}

function pullRequestCompactBadgesHtml(pr) {
  const statusHtml = pullRequestStatusIndicatorHtml(pr);
  const ciHtml = pullRequestCiIndicatorHtml(pr);
  const prHtml = statusHtml || ciHtml ? '' : pullRequestPrIndicatorHtml(pr);
  return [statusHtml, prHtml, ciHtml].filter(Boolean).join('');
}

function sessionButtonHtml(session, info, agentKind, state = sessionState(session, info), auto = false) {
  const stateHtml = state ? sessionStateHtml(state) : '';
  const pr = info?.project?.pull_request;
  const desc = sessionTabDescription(session, info);
  const detailHtml = desc ? `<span class="session-button-dir">${esc(desc)}</span>` : '';
  return `<span class="session-button-prefix">${sessionNumberNameHtml(session)}${yoloMarkerHtml(session, auto)}</span>
    <span class="session-button-text">${stateHtml}${pullRequestCompactBadgesHtml(pr)}${detailHtml}</span>`;
}

function infoButtonHtml() {
  return '<span class="session-button-prefix"><span class="session-button-number">0</span></span><span class="session-button-text"><span class="session-button-dir">Branches</span></span>';
}

function panelHeaderStateHtml(session, state, info = null, auto = false) {
  const pr = info?.project?.pull_request;
  return `${sessionNumberNameHtml(session)}${yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: true})}${state ? sessionStateHtml(state) : ''}${pullRequestCompactBadgesHtml(pr)}`;
}

function currentBranchSubject(git) {
  const branches = git?.other_branches?.branches || [];
  const current = branches.find(branch => branch.current);
  return current?.subject || '';
}

function sessionWorkDescription(session, info, limit = 96) {
  const project = info?.project || {};
  const git = project.git;
  const pr = project.pull_request;
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
  return sessionWorkDescription(session, info, 72);
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
  const pr = project.pull_request;
  const linear = project.linear || [];
  const pane = info?.selected_pane;
  const description = sessionWorkDescription(session, info, 220);
  const title = `${sessionLabel(session)} · ${projectDirName(session, info)}`;
  const subtitle = description || git?.branch || pane?.current_path || 'no checkout detected';
  const rows = [];
  const stateValue = `${sessionStateHtml(state)} <span class="meta-muted">${esc(state.reason)}</span>`;
  const agentValue = agentKind ? `${agentName(agentKind)}${autoEnabled ? ' · YOLO on' : ''}` : `${autoEnabled ? 'YOLO on' : 'not detected'}`;
  const displayPath = panelFullPath(session, info) || pane?.current_path || 'not available';
  rows.push(popoverPairRow('state', stateValue, 'agent', agentValue));
  rows.push(popoverRow('path', displayPath));
  if (git?.branch) rows.push(popoverRow('branch', `${branchLinkHtml(git, git.branch)}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`));
  if (Number.isFinite(git?.dirty_count) || Number.isFinite(git?.ahead) || Number.isFinite(git?.behind)) {
    rows.push(popoverRow('git', gitStatusText(git)));
  }
  let prDesc = '';
  if (pr?.number) {
    const prParts = [pullRequestLinkHtml(pr), pullRequestAuthorHtml(pr)].filter(Boolean);
    const checks = pullRequestChecksHtml(pr);
    if (checks) prParts.push(checks);
    rows.push(popoverRow('PR', metaJoin(prParts)));
    prDesc = pullRequestDescriptionInlineHtml(pr);
  }
  let linearValue = '';
  let linearDesc = '';
  if (linear.length) {
    linearValue = linearInlineHtml(linear);
    linearDesc = linearDescriptionsInlineHtml(linear);
    if (prDesc && linearValue) rows.push(popoverPairRow('desc', prDesc, 'Linear', linearValue));
    else if (prDesc) rows.push(popoverRow('desc', prDesc));
    else if (linearValue) rows.push(popoverRow('Linear', linearValue));
    if (linearDesc) rows.push(popoverRow('details', linearDesc));
  } else if (prDesc) {
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
      <div class="popover-badge">${esc(sessionLabel(session))}</div>
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
  return linkHtml(`https://linear.app/GPU/issue/${encodeURIComponent(identifier)}`, identifier, identifier);
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

function startSessionDrag(event, session, sourceSlot = null) {
  dragSession = session;
  dragSourceSlot = sourceSlot;
  const payload = JSON.stringify({session, sourceSlot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-session', payload);
  event.dataTransfer.setData('text/plain', session);
  event.currentTarget?.classList.add('dragging');
}

function endSessionDrag(event) {
  dragSession = null;
  dragSourceSlot = null;
  event.currentTarget?.classList.remove('dragging');
  sessionButtons.classList.remove('drag-over');
  grid.querySelectorAll('.drag-over,.drag-replace,.drag-stack-top,.drag-stack-bottom').forEach(node => node.classList.remove('drag-over', 'drag-replace', 'drag-stack-top', 'drag-stack-bottom'));
}

function removeSessionFromLayout(session) {
  const next = {...layoutSlots};
  for (const slot of layoutSlotKeys) {
    if (next[slot] === session) next[slot] = null;
  }
  applyLayoutSlots(next, {message: `${itemLabel(session)} removed`});
}

function firstEmptySlot() {
  return layoutSlotKeys.find(slot => !layoutSlots[slot]) || 'leftTop';
}

function slotForNewSession() {
  const empty = layoutSlotKeys.find(slot => !layoutSlots[slot]);
  if (empty) return empty;
  const focusedSlot = focusedPanelItem ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return 'leftTop';
}

async function moveSessionToSlot(session, targetSlot, sourceSlot = null, mode = 'stack') {
  if (!isLayoutItem(session) || !layoutSlotKeys.includes(targetSlot)) return;
  if (isTmuxSession(session)) {
    const ensured = await ensureSession(session);
    if (!ensured) return;
  }
  const next = {...layoutSlots};
  const targetSession = next[targetSlot];
  const currentSlot = slotForSession(session);
  const resolvedSourceSlot = sourceSlot || currentSlot;
  if (currentSlot === targetSlot) {
    focusPanel(session);
    return;
  }
  if (mode === 'swap' && resolvedSourceSlot && targetSession && targetSession !== session) {
    next[resolvedSourceSlot] = targetSession;
    next[targetSlot] = session;
    applyLayoutSlots(next, {focusSession: session});
    return;
  }
  if (mode === 'stack' && targetSession && targetSession !== session) {
    const alternate = alternateSlot(targetSlot);
    for (const slot of layoutSlotKeys) {
      if (next[slot] === session) next[slot] = null;
    }
    if (alternate && !next[alternate]) {
      next[alternate] = targetSession;
      next[targetSlot] = session;
      applyLayoutSlots(next, {focusSession: session});
      return;
    }
    if (currentSlot) {
      next[currentSlot] = targetSession;
      next[targetSlot] = session;
      applyLayoutSlots(next, {focusSession: session});
      return;
    }
  }
  if (mode !== 'replace' && mode !== 'stack' && currentSlot && targetSession && targetSession !== session) {
    next[currentSlot] = targetSession;
    next[targetSlot] = session;
    applyLayoutSlots(next, {focusSession: session});
    return;
  }
  for (const slot of layoutSlotKeys) {
    if (next[slot] === session) next[slot] = null;
  }
  next[targetSlot] = session;
  applyLayoutSlots(next, {focusSession: session});
}

function alternateSlot(slot) {
  if (slot === 'leftTop') return 'leftBottom';
  if (slot === 'leftBottom') return 'leftTop';
  if (slot === 'rightTop') return 'rightBottom';
  if (slot === 'rightBottom') return 'rightTop';
  return null;
}

async function selectSession(session) {
  if (activeSessions.includes(session)) {
    closeOpenSessionPopover({renderDeferred: false});
    focusPanel(session);
    return;
  }
  await moveSessionToSlot(session, slotForNewSession(), null, 'replace');
}

function sessionAgentKind(session) {
  const info = transcriptMeta.sessions?.[session];
  const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
  const kind = String(agent?.kind || '').toLowerCase();
  return kind === 'claude' || kind === 'codex' ? kind : '';
}

function agentIcon(kind) {
  if (kind === 'codex') {
    return `<span class="agent-icon codex" aria-label="Codex" title="Codex">${terminalIcon()}</span>`;
  }
  if (kind === 'claude') {
    return `<span class="agent-icon claude" aria-label="Claude" title="Claude">${sparkIcon()}</span>`;
  }
  return '';
}

function terminalIcon() {
  return '<svg viewBox="0 0 16 16" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 3.5h11v9h-11z"/><path d="M5 6.2 6.8 8 5 9.8"/><path d="M8.5 10h2.5"/></svg>';
}

function sparkIcon() {
  return '<svg viewBox="0 0 16 16" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5 9.2 6.8 13.5 8 9.2 9.2 8 13.5 6.8 9.2 2.5 8 6.8 6.8 8 2.5z"/></svg>';
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

function pullRequestStatusIndicatorHtml(pr) {
  if (!pr?.number) return '';
  const status = pullRequestStatusLabel(pr).toLowerCase();
  if (!['merged', 'draft', 'closed'].includes(status)) return '';
  return `<span class="ci-indicator ${pullRequestStatusClass(pr)}">${pullRequestStatusDisplay(pr)}</span>`;
}

function pullRequestPrIndicatorHtml(pr) {
  if (!pr?.number) return '';
  return '<span class="ci-indicator pr-indicator">PR</span>';
}

function pullRequestCiIndicatorHtml(pr) {
  if (pullRequestStatusLabel(pr).toLowerCase() === 'merged') return '';
  const state = pr?.checks?.state;
  if (!state || state === 'unknown') return '';
  return `<span class="ci-indicator ${pullRequestStatusClass(pr)}">CI</span>`;
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
  const pr = project.pull_request;
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
  const pr = project.pull_request;
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
  try {
    const response = await fetch(`/api/ensure-session?session=${encodeURIComponent(session)}`, {method: 'POST'});
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
  const agentLabel = agentName(agent) || 'agent';
  statusEl.textContent = `creating ${agentLabel} session...`;
  try {
    const response = await fetch(`/api/create-session?agent=${encodeURIComponent(agent)}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'session create failed')}</span>`;
      return;
    }
    const previousActive = activeSessions.slice();
    updateSessionList(payload.sessions || []);
    renderSessionButtons();
    renderPanels(previousActive);
    await moveSessionToSlot(payload.session, firstEmptySlot(), null);
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
    if (item?.socket?.readyState === WebSocket.OPEN) {
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

function sideSlotKeys(side) {
  return side === 'left' ? ['leftTop', 'leftBottom'] : ['rightTop', 'rightBottom'];
}

function slotSide(slot) {
  return slot.startsWith('left') ? 'left' : 'right';
}

function occupiedSlotsForSide(side) {
  return sideSlotKeys(side).filter(slot => layoutSlots[slot]);
}

function slotForSession(session) {
  return layoutSlotKeys.find(slot => layoutSlots[slot] === session) || null;
}

function slotForDropEvent(event) {
  const rect = grid.getBoundingClientRect();
  const side = event.clientX < rect.left + rect.width / 2 ? 'left' : 'right';
  return slotForSideDrop(side, event);
}

function dropIntentForEvent(event) {
  const slotNode = event.target.closest('.drop-slot');
  if (!slotNode) return {slot: slotForDropEvent(event), mode: 'stack'};
  const slot = slotNode.dataset.slot;
  if (!layoutSlots[slot]) return {slot, mode: 'replace'};
  const rect = slotNode.getBoundingClientRect();
  const ratio = (event.clientY - rect.top) / Math.max(1, rect.height);
  if (ratio < 0.28) return {slot: sideSlotKeys(slotSide(slot))[0], mode: 'stack', zone: 'top'};
  if (ratio > 0.72) return {slot: sideSlotKeys(slotSide(slot))[1], mode: 'stack', zone: 'bottom'};
  return {slot, mode: 'replace', zone: 'middle'};
}

function slotForSideDrop(side, event) {
  const [topSlot, bottomSlot] = sideSlotKeys(side);
  const topSession = layoutSlots[topSlot];
  const bottomSession = layoutSlots[bottomSlot];
  if (!topSession && !bottomSession) return topSlot;
  const column = document.querySelector(`[data-side="${side}"]`);
  const rect = column?.getBoundingClientRect() || grid.getBoundingClientRect();
  const topHalf = event.clientY < rect.top + rect.height / 2;
  if (topSession && bottomSession) return topHalf ? topSlot : bottomSlot;
  if (topSession && !bottomSession) return topHalf ? topSlot : bottomSlot;
  if (!topSession && bottomSession) return topHalf ? topSlot : bottomSlot;
  return topSlot;
}

function dropSessionAtEvent(event) {
  const payload = dragPayload(event);
  if (!payload?.session) return;
  event.preventDefault();
  event.stopPropagation();
  grid.querySelectorAll('.drag-over').forEach(node => node.classList.remove('drag-over'));
  grid.querySelectorAll('.drag-replace,.drag-stack-top,.drag-stack-bottom').forEach(node => node.classList.remove('drag-replace', 'drag-stack-top', 'drag-stack-bottom'));
  const intent = dropIntentForEvent(event);
  const sourceSlot = payload.sourceSlot || slotForSession(payload.session);
  const mode = sourceSlot && intent.zone === 'middle' ? 'swap' : intent.mode;
  moveSessionToSlot(payload.session, intent.slot, sourceSlot, mode);
}

function handleDropDragOver(event) {
  const payload = dragPayload(event);
  if (!payload?.session) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = 'move';
  grid.querySelectorAll('.drag-over,.drag-replace,.drag-stack-top,.drag-stack-bottom').forEach(node => node.classList.remove('drag-over', 'drag-replace', 'drag-stack-top', 'drag-stack-bottom'));
  const column = event.target.closest('[data-side]');
  const slot = event.target.closest('.drop-slot');
  column?.classList.add('drag-over');
  slot?.classList.add('drag-over');
  if (slot) {
    const intent = dropIntentForEvent(event);
    if (intent.mode === 'replace') {
      slot.classList.add('drag-replace');
    } else if (intent.zone === 'top') {
      slot.classList.add('drag-stack-top');
    } else if (intent.zone === 'bottom') {
      slot.classList.add('drag-stack-bottom');
    }
  }
}

function handleDropDragLeave(event) {
  const current = event.currentTarget;
  if (current?.contains(event.relatedTarget)) return;
  current?.classList.remove('drag-over', 'drag-replace', 'drag-stack-top', 'drag-stack-bottom');
}

function renderPanels(previousActive = []) {
  movePanelsToPool();
  grid.className = 'grid';
  grid.innerHTML = '';
  grid.appendChild(renderLayoutColumn('left'));
  grid.appendChild(renderLayoutColumn('right'));

  bindDropTargets();
  syncPanelVisibility(previousActive);
  renderAutoApproveButtons();
}

function movePanelsToPool() {
  for (const session of layoutItems) {
    const panel = getOrCreatePanel(session);
    panel.classList.remove('expanded');
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

function renderLayoutColumn(side) {
  const column = document.createElement('section');
  const occupied = occupiedSlotsForSide(side);
  column.className = `layout-column ${occupied.length > 1 ? 'split' : ''}`;
  column.dataset.side = side;
  if (occupied.length === 0) {
    column.appendChild(renderDropSlot(sideSlotKeys(side)[0], null, `Drop ${side}`));
    return column;
  }
  for (const slot of occupied) {
    column.appendChild(renderDropSlot(slot, layoutSlots[slot], `Drop ${slotLabel(slot)}`));
  }
  return column;
}

function renderDropSlot(slot, session, label) {
  const node = document.createElement('section');
  node.className = `drop-slot ${session ? '' : 'empty'}`;
  node.dataset.slot = slot;
  node.dataset.side = slotSide(slot);
  if (!session) {
    node.innerHTML = `<div class="drop-label">${esc(label)}</div>`;
    return node;
  }
  const panel = getOrCreatePanel(session);
  updatePanelSlot(panel, session, slot);
  node.appendChild(panel);
  return node;
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
  }
  panel.querySelector('[data-remove]')?.addEventListener('click', () => removeSessionFromLayout(session));
  panel.querySelector('[data-expand]')?.addEventListener('click', buttonEvent => {
    const button = buttonEvent.currentTarget;
    const expanded = !panel.classList.contains('expanded');
    setPanelExpanded(panel, session, expanded);
    setTimeout(() => {
      if (isTmuxSession(session)) fitTerminal(session);
    }, 80);
  });
}

function bindPanelPopover(panel) {
  const zone = panel.querySelector('.panel-popover-zone');
  if (!zone || zone.dataset.popoverBound === 'true') return;
  zone.dataset.popoverBound = 'true';
  zone.addEventListener('pointerover', () => keepPanelPopoverOpen(zone));
  zone.addEventListener('pointerout', event => {
    if (event.relatedTarget && zone.contains(event.relatedTarget)) return;
    closePanelPopoverSoon(zone);
  });
  zone.addEventListener('focusin', () => keepPanelPopoverOpen(zone));
  zone.addEventListener('focusout', event => {
    if (event.relatedTarget && zone.contains(event.relatedTarget)) return;
    closePanelPopoverSoon(zone);
  });
}

function keepPanelPopoverOpen(zone) {
  const timer = panelPopoverHideTimers.get(zone);
  if (timer) clearTimeout(timer);
  panelPopoverHideTimers.delete(zone);
  zone.classList.add('popover-open');
}

function closePanelPopoverSoon(zone) {
  const existing = panelPopoverHideTimers.get(zone);
  if (existing) clearTimeout(existing);
  const timer = setTimeout(() => {
    zone.classList.remove('popover-open');
    panelPopoverHideTimers.delete(zone);
  }, popoverHideDelayMs);
  panelPopoverHideTimers.set(zone, timer);
}

function setPanelExpanded(panel, session, expanded) {
  if (expanded) {
    for (const other of panelNodes.values()) {
      if (other !== panel) other.classList.remove('expanded');
    }
  }
  panel.classList.toggle('expanded', expanded);
  const button = panel.querySelector('[data-expand]');
  if (button) {
    button.title = expanded ? 'collapse' : 'expand';
    button.setAttribute('aria-label', `${expanded ? 'Collapse' : 'Expand'} ${itemLabel(session)}`);
    if (!button.classList.contains('traffic-light')) button.textContent = expanded ? 'Collapse' : 'Expand';
  }
  if (expanded) {
    if (isTmuxSession(session)) {
      activateTab(session, 'terminal');
      setFocusedTerminal(session);
      setTimeout(() => terminals.get(session)?.term?.focus?.(), 25);
    } else {
      focusedTerminal = null;
      setFocusedPanelItem(session);
    }
  }
  renderSessionButtons();
  for (const activeSession of activeSessions.filter(isTmuxSession)) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
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
        <div class="panel-buttons traffic-controls">
          <button class="traffic-light close" data-remove="${esc(infoItemId)}" title="minimize Branches" aria-label="Minimize Branches"></button>
          <button class="traffic-light zoom" data-expand="${esc(infoItemId)}" title="expand" aria-label="Expand Branches"></button>
        </div>
        <div class="panel-copy">
          <div id="panel-tab-${infoItemId}" class="panel-session-label"><span class="session-button-dir">Branches</span></div>
          <div id="meta-${infoItemId}" class="meta">all branches sorted by recent activity</div>
        </div>
      </div>
      <div class="info-pane panel-overlay-root">
        <div class="transcript-head">All branches</div>
        <div id="info-content" class="info-list"></div>
      </div>`;
  bindPanelShell(panel, infoItemId);
  renderInfoPanel();
  return panel;
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = `panel-${session}`;
  panel.innerHTML = `
      <div class="panel-head">
        <div class="panel-buttons traffic-controls">
          <button class="traffic-light close" data-remove="${esc(session)}" title="minimize this session" aria-label="Minimize ${esc(sessionLabel(session))}"></button>
          <button class="traffic-light zoom" data-expand="${esc(session)}" title="expand" aria-label="Expand ${esc(sessionLabel(session))}"></button>
        </div>
        <div class="panel-copy">
          <div class="panel-popover-zone">
            <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(session, sessionState(session, transcriptMeta.sessions?.[session]), transcriptMeta.sessions?.[session], autoApproveStates.get(session)?.enabled === true)}</div>
            <div id="meta-${session}" class="meta">finding branch...</div>
            ${sessionPopoverHtml(session, transcriptMeta.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMeta.sessions?.[session]))}
          </div>
        </div>
      <div class="tabs" role="tablist">
        <button class="tab window-step" data-window-dir="prev" data-window-session="${esc(session)}" title="previous tmux window">&lt;</button>
        <button class="tab active" data-tab="${esc(session)}" data-tab-name="terminal">Term</button>
        <button class="tab window-step" data-window-dir="next" data-window-session="${esc(session)}" title="next tmux window">&gt;</button>
        <button class="tab" data-tab="${esc(session)}" data-tab-name="transcript">Tx</button>
        <button class="tab" data-tab="${esc(session)}" data-tab-name="summary">AI</button>
        <button class="tab" data-tab="${esc(session)}" data-tab-name="events">Log</button>
      </div>
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
  const header = `<div class="info-row header">
    <div class="info-cell">path</div>
    <div class="info-cell">branch</div>
    <div class="info-cell">desc</div>
    <div class="info-cell">updated</div>
    <div class="info-cell">PR</div>
    <div class="info-cell">Linear</div>
  </div>`;
  const body = rows.map(row => `<div class="info-row${row.current ? ' current' : ''}">
    <div class="info-cell" title="${esc(row.path)}">${esc(pathBasename(row.path) || row.session || '')}</div>
    <div class="info-cell" title="${esc(row.branch)}">${row.current ? '<span class="info-branch-current">*</span> ' : ''}${row.branchHtml}</div>
    <div class="info-cell" title="${esc(row.desc)}">${esc(row.desc)}</div>
    <div class="info-cell" title="${esc(row.updated)}">${esc(row.updated)}</div>
    <div class="info-cell">${row.prHtml}</div>
    <div class="info-cell">${row.linearHtml}</div>
  </div>`).join('');
  node.innerHTML = header + body;
}

function infoBranchRows() {
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
      const currentPr = current ? project.pull_request : null;
      const currentLinear = current ? project.linear || [] : [];
      const linearIds = currentLinear.length
        ? currentLinear.map(issue => issue.identifier).filter(Boolean)
        : branch.linear_ids || [];
      const linearHtml = currentLinear.length
        ? currentLinear.map(issue => linearIssueHtml(issue)).join(' ')
        : linearIds.map(linearIssueLinkHtml).filter(Boolean).join(' ');
      const prHtml = currentPr?.number ? pullRequestColumnLinkHtml(currentPr) : pullRequestLinkForBranch(git, branch);
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
        linearHtml,
        current,
      });
    }
  }
  rows.sort((a, b) => b.updatedTs - a.updatedTs || a.path.localeCompare(b.path) || a.branch.localeCompare(b.branch));
  return rows;
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
  panel.querySelector('[data-context]')?.addEventListener('click', () => showContext(session));
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-auto-session]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    toggleAutoApprove(session);
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
  if (clipboardPasteBound) return;
  clipboardPasteBound = true;
  document.addEventListener('paste', event => {
    const file = pastedImageFile(event);
    if (!file) return;
    const session = pasteTargetSession(event);
    if (!session) {
      statusEl.innerHTML = '<span class="err">select a YOLOMux pane before pasting an image</span>';
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
  return new File([file], nextPasteFilename(file.type || item.type || 'image/png'), {type: file.type || item.type || 'image/png'});
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
  const next = (pasteCounters.get(key) || 0) + 1;
  pasteCounters.set(key, next);
  return `${stamp}-${String(next).padStart(3, '0')}${suffix}`;
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
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const response = await fetch(`/api/upload?session=${encodeURIComponent(session)}`, {
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
    activateTab(session, 'terminal');
    const inserted = insertUploadPaths(session, paths, {silent: true});
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

function insertIntoTerminal(session, text) {
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
    title: `YOLOMux - ${serverHostname}: ${sessionLabel(session)} upload`,
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

function slotLabel(slot) {
  return slot
    .replace('left', 'left ')
    .replace('right', 'right ')
    .replace('Top', 'top')
    .replace('Bottom', 'bottom');
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
  const item = terminals.get(session);
  if (!item || item.socket.readyState !== WebSocket.OPEN) {
    statusEl.innerHTML = `<span class="err">${esc(sessionLabel(session))} terminal is not connected</span>`;
    return;
  }
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  statusEl.innerHTML = `<span class="ok">${esc(label)}: ${esc(sessionLabel(session))}</span>`;
  scheduleFit(session);
  setTimeout(() => terminals.get(session)?.term?.focus(), 75);
}

async function ensureTerminalRunning(session) {
  const item = terminals.get(session);
  if (item && item.socket.readyState !== WebSocket.CLOSING && item.socket.readyState !== WebSocket.CLOSED) return;
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
    theme: {
      background: '#11151d',
      foreground: '#dfe6ef',
      cursor: '#f5f7fb',
      selectionBackground: '#3a4b64'
    }
  });
  term.open(container);
  installTerminalLinkProvider(term);
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
    renderSessionButtons();
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
    trackSessionStateChanges();
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
    renderSessionButtons();
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
    trackSessionStateChanges();
    scheduleTerminalReconnect(session, item);
  };
  socket.onerror = () => {
    updateTypingIndicator(session);
    updateStatus();
    renderSessionButtons();
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
    trackSessionStateChanges();
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
    return;
  }
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  if (!activeTmuxSessions.length) {
    statusEl.textContent = 'Branches shown';
    return;
  }
  let open = 0;
  for (const session of activeTmuxSessions) {
    const item = terminals.get(session);
    if (item?.socket?.readyState === WebSocket.OPEN) open += 1;
  }
  statusEl.innerHTML = open === activeTmuxSessions.length ? '<span class="ok">all connected</span>' : `${open}/${activeTmuxSessions.length} connected`;
}

async function toggleAutoApprove(session) {
  const current = autoApproveStates.get(session)?.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  try {
    const response = await fetch(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      statusEl.innerHTML = `<span class="err">${esc(payload.error || 'YOLO approval failed')}</span>`;
      return;
    }
    autoApproveStates.set(session, payload);
    renderSessionButtons();
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
  renderSessionButtons();
  renderAutoApproveButtons();
  for (const session of activeSessions.filter(isTmuxSession)) {
    updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  }
  trackSessionStateChanges();
  refreshOpenEventLogs();
}

async function loadAutoStatuses() {
  try {
    const response = await fetch('/api/auto-approve');
    const payload = await response.json();
    for (const session of sessions) {
      const state = payload.sessions?.[session] || {target: session, enabled: false, last_action: 'off'};
      autoApproveStates.set(session, state);
    }
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const response = await fetch(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        const payload = await response.json();
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
  }
}

function renderAutoApproveButtons() {
  for (const session of sessions) {
    const state = autoApproveStates.get(session) || {target: session, enabled: false, last_action: 'off'};
    renderAutoApproveButton(session, state);
  }
}

function renderAutoApproveButton(session, payload) {
  const button = document.querySelector(`[data-auto-session="${session}"]`);
  const enabled = payload?.enabled === true;
  if (button) {
    button.classList.toggle('active', enabled);
    button.textContent = 'YO';
    const action = payload?.last_action ? `; ${payload.last_action}` : '';
    button.title = enabled
      ? `YOLO on for ${sessionLabel(session)}${action}`
      : `YOLO off for ${sessionLabel(session)}`;
  }
  updatePanelHeader(session, transcriptMeta.sessions?.[session]);
  updateTypingIndicator(session);
}

function startSummaryStream(session) {
  stopSummaryStream(session);
  const node = document.getElementById(`summary-${session}`);
  if (!node) return;
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
    const response = await fetch('/api/transcripts');
    transcriptMeta = await response.json();
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
  if (!tab) return;
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  tab.className = `panel-session-label ${auto ? 'auto' : ''} ${state.attention ? 'needs-attention' : ''}`;
  tab.innerHTML = panelHeaderStateHtml(session, state, info, auto);
  tab.removeAttribute('title');
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

function renderSummaryContext(session, info, agent) {
  const node = document.getElementById(`summary-context-${session}`);
  if (!node) return;
  node.innerHTML = summaryContextHtml(session, info, agent);
}

async function refreshTranscriptPreview(session, preview, options = {}) {
  try {
    const response = await fetch(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
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
    const response = await fetch(`/api/events?session=${encodeURIComponent(session)}&limit=120`);
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
  fetch('/api/event', {
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
    const response = await fetch(`/api/ping?t=${Date.now()}`, {cache: 'no-store'});
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
  closeOpenSessionPopover({renderDeferred: false});
  sessionButtonsRenderDeferred = false;
  refreshTranscripts();
  refreshAutoStatuses();
}

async function boot() {
  statusEl.textContent = 'loading YOLO status...';
  await loadNotifyStatus();
  await loadAutoStatuses();
  renderSessionButtons();
  renderPanels();
  await Promise.all(visibleSessions.map(session => ensureTerminalRunning(session)));
  refreshTranscripts();
  renderAutoApproveButtons();
  updateLatency();
  setInterval(refreshAutoStatuses, paneStateRefreshMs);
  setInterval(refreshTranscripts, metadataRefreshMs);
  setInterval(updateLatency, latencyRefreshMs);
  setInterval(refreshOpenEventLogs, 5000);
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  title.textContent = `${sessionLabel(session)} transcript tail`;
  body.textContent = 'loading...';
  modal.classList.add('open');
  const response = await fetch(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  const payload = await response.json();
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
}

document.getElementById('refreshMeta').onclick = refreshAll;
notifyToggle.onclick = toggleNotifications;
document.getElementById('closeModal').onclick = () => document.getElementById('modal').classList.remove('open');
window.addEventListener('resize', () => {
  updateTopbarPopoverGeometry();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();

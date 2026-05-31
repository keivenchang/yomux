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

function readStoredEditorLineNumbers() {
  try {
    return window.localStorage?.getItem(fileEditorLineNumbersStorageKey) === '1';
  } catch (_) {
    return false;
  }
}

function writeStoredEditorLineNumbers(value) {
  try {
    window.localStorage?.setItem(fileEditorLineNumbersStorageKey, value ? '1' : '0');
  } catch (_) {}
}

function defaultCollapsedPreferenceSections() {
  return new Set(['General', 'Appearance', 'Performance', 'Notifications', 'Terminal / Editor', 'File Explorer', 'Finder']);
}

function readStoredCollapsedPreferenceSections() {
  try {
    const raw = window.localStorage?.getItem(preferencesCollapsedStorageKey);
    if (!raw) return defaultCollapsedPreferenceSections();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return defaultCollapsedPreferenceSections();
    return new Set(parsed.filter(item => typeof item === 'string' && item));
  } catch (_) {
    return defaultCollapsedPreferenceSections();
  }
}

function writeStoredCollapsedPreferenceSections() {
  try {
    window.localStorage?.setItem(preferencesCollapsedStorageKey, JSON.stringify(Array.from(collapsedPreferenceSections)));
  } catch (_) {}
}

function nestedSetting(source, path, fallback) {
  let current = source;
  for (const part of String(path || '').split('.')) {
    if (!part) continue;
    if (!current || typeof current !== 'object' || !(part in current)) return fallback;
    current = current[part];
  }
  return current === undefined || current === null ? fallback : current;
}

function initialSetting(path, fallback) {
  return nestedSetting(clientSettings, path, nestedSetting(clientSettingsDefaults, path, fallback));
}

function mergeSettingObjects(base, patch) {
  const result = Array.isArray(base) ? base.slice() : {...(base || {})};
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return result;
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
      result[key] = mergeSettingObjects(result[key], value);
    } else {
      result[key] = Array.isArray(value) ? value.slice() : value;
    }
  }
  return result;
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

function normalizeEditorSchemeId(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'light' || normalized === 'white') return defaultLightEditorScheme;
  return EDITOR_SCHEMES[normalized] ? normalized : defaultEditorScheme;
}

function normalizeEditorSchemeForMode(value, dark) {
  const id = normalizeEditorSchemeId(value);
  const scheme = EDITOR_SCHEMES[id];
  if (scheme && scheme.dark === dark) return id;
  return dark ? defaultEditorScheme : defaultLightEditorScheme;
}

function activeEditorScheme() {
  return EDITOR_SCHEMES[normalizeEditorSchemeId(fileEditorThemeMode)] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
}

function configuredEditorSchemeForMode(dark) {
  const path = dark ? 'appearance.editor_dark_color_scheme' : 'appearance.editor_light_color_scheme';
  const fallback = dark ? defaultEditorScheme : defaultLightEditorScheme;
  return normalizeEditorSchemeForMode(initialSetting(path, fallback), dark);
}

function readStoredEditorThemeMode() {
  try {
    const value = window.localStorage?.getItem(fileEditorThemeModeStorageKey) || defaultEditorScheme;
    return normalizeEditorSchemeId(value);
  } catch (_) {
    return defaultEditorScheme;
  }
}

function writeStoredEditorThemeMode(mode) {
  try {
    window.localStorage?.setItem(fileEditorThemeModeStorageKey, normalizeEditorSchemeId(mode));
  } catch (_) {}
}

function readConfiguredEditorScheme() {
  return normalizeEditorSchemeId(readStoredEditorThemeMode());
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
  clearPendingFileEditorFocusExcept(session);
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

function setFocusedPanelItem(item, options = {}) {
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  clearPendingFileEditorFocusExcept(item);
  if (isTmuxSession(item)) {
    lastFocusedTmuxSession = item;
    dismissAttentionAlertsForSession(item);
  }
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item);
  if (isTmuxSession(item) && document.querySelector('.file-explorer-changes-panel')) {
    switchFileExplorerChangesSession(item);
  }
  if (isPreferencesItem(item) && options.focusPreferencesSearch !== false) {
    focusFreshPreferencesSearchSoon();
  }
}

function clearPendingFileEditorFocusExcept(item) {
  for (const pendingItem of Array.from(pendingFileEditorFocus)) {
    if (pendingItem !== item) pendingFileEditorFocus.delete(pendingItem);
  }
}

function focusTerminalWhenAutoFocus(session, delay = 0) {
  if (!autoFocusEnabled) return;
  setTimeout(() => terminals.get(session)?.term?.focus?.(), delay);
}

function focusTerminalFromUserAction(session, delay = 0) {
  setFocusedTerminal(session);
  const run = () => terminals.get(session)?.term?.focus?.();
  if (delay > 0) setTimeout(run, delay);
  else run();
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
  if (!autoFocusEnabled) return;
  if (isTmuxSession(item) && terminalPaneIsActive(item)) {
    setFocusedTerminal(item);
    scheduleFit(item);
    focusTerminalWhenAutoFocus(item, 0);
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

function fuzzySubsequenceMatch(query, text) {
  const needle = String(query || '').toLowerCase().replace(/\s+/g, '');
  const haystack = String(text || '').toLowerCase();
  if (!needle) return {score: 0, indexes: []};
  let position = 0;
  let previousIndex = -1;
  let score = 0;
  const indexes = [];
  for (const char of needle) {
    const index = haystack.indexOf(char, position);
    if (index < 0) return null;
    const previousChar = haystack[index - 1] || '';
    const contiguous = previousIndex >= 0 && index === previousIndex + 1;
    const wordStart = index === 0 || /[\s/_:.-]/.test(previousChar);
    score += 8;
    if (contiguous) score += 10;
    if (wordStart) score += 6;
    score -= Math.max(0, index - position) * 0.2;
    previousIndex = index;
    position = index + 1;
    indexes.push(index);
  }
  return {score: score - Math.max(0, haystack.length - needle.length) * 0.01, indexes};
}

function fuzzySubsequenceScore(query, text) {
  const match = fuzzySubsequenceMatch(query, text);
  return match ? match.score : Number.NEGATIVE_INFINITY;
}

function fuzzySearchScore(query, fields) {
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  const values = (Array.isArray(fields) ? fields : [fields]).map(value => String(value || '')).filter(Boolean);
  if (!values.length) return Number.NEGATIVE_INFINITY;
  let total = 0;
  for (const token of tokens) {
    let best = Number.NEGATIVE_INFINITY;
    for (const [index, value] of values.entries()) {
      const fieldScore = fuzzySubsequenceScore(token, value);
      if (Number.isFinite(fieldScore)) best = Math.max(best, fieldScore - index * 2);
    }
    if (!Number.isFinite(best)) return Number.NEGATIVE_INFINITY;
    total += best;
  }
  return total;
}

function fuzzyHighlightHtml(query, text) {
  const value = String(text ?? '');
  const token = String(query || '').trim().split(/\s+/).filter(Boolean)[0] || '';
  const match = fuzzySubsequenceMatch(token, value);
  if (!match || !match.indexes.length) return esc(value);
  const indexes = new Set(match.indexes);
  return Array.from(value).map((char, index) => indexes.has(index)
    ? `<mark class="fuzzy-match">${esc(char)}</mark>`
    : esc(char)).join('');
}

function replaceHtmlPreservingScroll(element, html) {
  if (!element) return;
  const scrollTop = element.scrollTop || 0;
  const scrollLeft = element.scrollLeft || 0;
  element.innerHTML = html;
  element.scrollTop = scrollTop;
  element.scrollLeft = scrollLeft;
  requestAnimationFrame(() => {
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  });
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
    isOpen: () => Boolean(menu),
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

function contextMenuIsOpen() {
  return terminalContextMenu.isOpen() || fileContextMenu.isOpen() || sessionContextMenu.isOpen();
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

function installTerminalCopyShortcut(session, term) {
  // Ctrl-C / Cmd-C copy the terminal selection. Plain Ctrl-C with NO selection
  // must still send SIGINT to the PTY, so only swallow the keystroke when there
  // is something selected. xterm paints the selection on a canvas, so the
  // browser's native copy can't grab it — we copy explicitly.
  term.attachCustomKeyEventHandler?.(event => {
    if (event.type !== 'keydown') return true;
    if (event.code !== 'KeyC' && event.key?.toLowerCase() !== 'c') return true;
    const isCmdC = event.metaKey && !event.ctrlKey && !event.altKey;
    const isCtrlC = event.ctrlKey && !event.metaKey && !event.altKey;
    if (!isCmdC && !isCtrlC) return true;
    const selected = term.getSelection?.() || '';
    if (!selected) return true; // no selection: let Ctrl-C through as SIGINT
    event.preventDefault();
    copyTerminalSelection(session, term);
    term.clearSelection?.(); // so a second Ctrl-C falls through to SIGINT
    return false; // swallow: do not forward ^C to the PTY
  });
}

function showTerminalContextMenu(session, term, x, y) {
  closeFileContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
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
  for (const item of tmuxSessionActionCommands(session, {renameAction})) {
    appendContextMenuButton(menu, item.label, item.action, closeSessionContextMenu, {disabled: item.disabled, checked: item.checked});
  }
  sessionContextMenu.open(menu, x, y);
}

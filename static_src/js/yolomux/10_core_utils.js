async function apiFetch(url, options = {}) {
  const requestOptions = {...options};
  if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
  const startedAt = jsDebugPerformanceNow();
  const method = jsDebugRequestMethod(requestOptions);
  const requestBytes = jsDebugRequestBytes(url, requestOptions);
  let response;
  try {
    response = await fetch(url, requestOptions);
  } catch (error) {
    recordApiDebugEvent(url, method, startedAt, {error, requestBytes});
    throw error;
  }
  const event = recordApiDebugEvent(url, method, startedAt, {status: response.status, ok: response.ok, requestBytes});
  recordApiDebugResponseBytes(event, response);
  if (response.status === 401) {
    await redirectToLogin(response);
    throw new Error('authentication required');
  }
  return response;
}

async function apiFetchJson(url, options = {}) {
  const response = await apiFetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error || response.statusText || `HTTP ${response.status}`);
    error.status = response.status;
    error.statusText = response.statusText || '';
    error.payload = payload || {};
    error.response = response;
    throw error;
  }
  return payload;
}

function clientPushCanSupplyData() {
  return Boolean(clientEventsSource && location.protocol !== 'file:');
}

function clientPushConnectedForData() {
  return clientPushCanSupplyData() && clientEventsConnected === true;
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

function jsDebugPerformanceNow() {
  if (!debugModeEnabled) return 0;
  const value = globalThis.performance?.now?.();
  return Number.isFinite(value) ? value : Date.now();
}

function jsDebugRequestMethod(options = {}) {
  return String(options?.method || 'GET').toUpperCase();
}

function jsDebugByteLength(text) {
  const value = String(text || '');
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
  return value.length;
}

function jsDebugRequestBytes(url, options = {}) {
  if (!debugModeEnabled) return 0;
  let bytes = jsDebugByteLength(jsDebugUrlText(url));
  const body = options?.body;
  if (typeof body === 'string') bytes += jsDebugByteLength(body);
  else if (body instanceof ArrayBuffer) bytes += body.byteLength;
  else if (body?.byteLength) bytes += Number(body.byteLength) || 0;
  return bytes;
}

function jsDebugDurationMs(startedAt) {
  if (!debugModeEnabled || !Number.isFinite(startedAt)) return null;
  const duration = jsDebugPerformanceNow() - startedAt;
  return Number.isFinite(duration) ? Number(duration.toFixed(1)) : null;
}

function jsDebugUrlText(url) {
  const value = String(url || '');
  try {
    const parsed = new URL(value, window.location.origin);
    return `${parsed.pathname}${parsed.search}`;
  } catch (_) {
    return value.slice(0, 240);
  }
}

function jsDebugErrorText(error) {
  return String(error?.message || error || '').slice(0, 500);
}

function recordApiDebugEvent(url, method, startedAt, result = {}) {
  if (!debugModeEnabled) return null;
  const payload = {
    method,
    url: jsDebugUrlText(url),
    durationMs: jsDebugDurationMs(startedAt),
  };
  if (Number.isFinite(result.requestBytes)) payload.requestBytes = result.requestBytes;
  if (Number.isFinite(result.status)) payload.status = result.status;
  if (typeof result.ok === 'boolean') payload.ok = result.ok;
  if (result.error) payload.error = jsDebugErrorText(result.error);
  return recordJsDebugEvent('api', payload);
}

function recordApiDebugResponseBytes(event, response) {
  if (!debugModeEnabled || !event || !response) return;
  const headerBytes = Number(response.headers?.get?.('Content-Length') || NaN);
  if (Number.isFinite(headerBytes) && headerBytes >= 0) {
    event.responseBytes = headerBytes;
    scheduleJsDebugPanelRefresh();
    return;
  }
  response.clone().arrayBuffer().then(buffer => {
    event.responseBytes = buffer.byteLength;
    scheduleJsDebugPanelRefresh();
  }).catch(() => {});
}

function recordJsDebugEvent(type, payload = {}) {
  if (!debugModeEnabled) return null;
  const event = {
    id: ++jsDebugEventSeq,
    ts: new Date().toISOString(),
    type: String(type || 'event'),
    ...payload,
  };
  jsDebugEvents.push(event);
  if (jsDebugEvents.length > jsDebugEventLimit) {
    jsDebugEvents.splice(0, jsDebugEvents.length - jsDebugEventLimit);
  }
  scheduleJsDebugPanelRefresh();
  return event;
}

function clearJsDebugEvents() {
  jsDebugEvents = [];
  jsDebugEventSeq = 0;
  if (jsDebugRenderTimer) {
    clearTimeout(jsDebugRenderTimer);
    jsDebugRenderTimer = null;
  }
  if (typeof renderDebugPanels === 'function') renderDebugPanels({force: true});
}

function scheduleJsDebugPanelRefresh() {
  if (!debugModeEnabled || typeof refreshDebugPanelsFromEvents !== 'function') return;
  if (jsDebugRenderTimer) return;
  jsDebugRenderTimer = setTimeout(() => {
    jsDebugRenderTimer = null;
    refreshDebugPanelsFromEvents();
  }, jsDebugRenderDebounceMs);
}

function installJsDebugEventCapture() {
  if (!debugModeEnabled || jsDebugEventCaptureInstalled || !window?.addEventListener) return;
  jsDebugEventCaptureInstalled = true;
  window.addEventListener('error', event => {
    recordJsDebugEvent('error', {
      message: jsDebugErrorText(event.error || event.message),
      source: jsDebugUrlText(event.filename || ''),
      line: Number(event.lineno || 0),
      column: Number(event.colno || 0),
    });
  });
  window.addEventListener('unhandledrejection', event => {
    recordJsDebugEvent('unhandledrejection', {
      message: jsDebugErrorText(event.reason),
    });
  });
}

installJsDebugEventCapture();

function agentLabel(kind) {
  const key = String(kind || '').toLowerCase();
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return String(kind || '');
}

// localStorage can throw (privacy mode, blocked, quota) — these swallow failures so a blocked store
// never breaks the page. storageGet returns the raw string (or `fallback` when absent/blocked);
// storageSet coerces to string and no-ops on failure. Every readStored*/writeStored* builds on these.
function storageGet(key, fallback = null) {
  try {
    const value = window.localStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage?.setItem(key, String(value));
  } catch (_) {}
}

function sessionStorageGet(key, fallback = null) {
  try {
    const value = window.sessionStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function sessionStorageSet(key, value) {
  try {
    window.sessionStorage?.setItem(key, String(value));
  } catch (_) {}
}

function fileExplorerClosedByUser() {
  return sessionStorageGet(fileExplorerOpenIntentStorageKey) === '0';
}

function rememberFileExplorerOpenIntent(open) {
  sessionStorageSet(fileExplorerOpenIntentStorageKey, open ? '1' : '0');
}

function safeJsonParse(raw, fallback = null) {
  try {
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function readStoredSet(key) {
  const parsed = safeJsonParse(storageGet(key), []);
  return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
}

function readStoredJson(key, fallback = null) {
  return safeJsonParse(storageGet(key), fallback);
}

function readStoredPinnedTabs() {
  const parsed = readStoredJson(pinnedTabsStorageKey, []);
  if (!Array.isArray(parsed)) return [];
  const result = [];
  for (const raw of parsed) {
    const item = String(raw || '').trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function writeStoredPinnedTabs() {
  storageSet(pinnedTabsStorageKey, JSON.stringify(pinnedTabItems));
}

function normalizeFileStateRecord(state) {
  if (!state || typeof state !== 'object') state = {};
  if (!(state.editorTabItems instanceof Set)) state.editorTabItems = new Set();
  if (!(state.ownerSessions instanceof Set)) state.ownerSessions = new Set();
  if (!(state.viewMode instanceof Map)) state.viewMode = new Map();
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = null;
  if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = false;
  return state;
}

function physicalFileIdentityFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const explicit = String(payload.file_identity || payload.fileIdentity || '').trim();
  if (explicit) return explicit;
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  if (fileId) return `id:${fileId}`;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  return realpath ? `realpath:${realpath}` : '';
}

function applyFileIdentityMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (realpath) state.realpath = realpath;
  if (fileId) state.fileId = fileId;
  if (identity) state.fileIdentity = identity;
  return state;
}

function registerFileIdentityForPath(path, payload) {
  const normalized = String(path || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (!normalized || !identity) return '';
  fileIdentityByPath.set(normalized, identity);
  if (!openFilePathByIdentity.has(identity)) openFilePathByIdentity.set(identity, normalized);
  return identity;
}

function primaryOpenPathForFileIdentity(identity) {
  const text = String(identity || '').trim();
  if (!text) return '';
  const mapped = openFilePathByIdentity.get(text);
  if (mapped && openFiles.has(mapped)) return mapped;
  for (const [path, state] of openFiles.entries()) {
    if (physicalFileIdentityFromPayload(state) === text) {
      openFilePathByIdentity.set(text, path);
      return path;
    }
  }
  openFilePathByIdentity.delete(text);
  return '';
}

function openPathForPhysicalFile(path, payload = null) {
  const identity = registerFileIdentityForPath(path, payload) || fileIdentityByPath.get(path) || physicalFileIdentityFromPayload(payload);
  return primaryOpenPathForFileIdentity(identity);
}

function reassignOpenFileIdentityPath(oldPath, newPath) {
  const identity = fileIdentityByPath.get(oldPath) || physicalFileIdentityFromPayload(fileStateFor(oldPath));
  if (!identity) return;
  fileIdentityByPath.delete(oldPath);
  fileIdentityByPath.set(newPath, identity);
  if (openFilePathByIdentity.get(identity) === oldPath) openFilePathByIdentity.set(identity, newPath);
}

function clearOpenFileIdentityPath(path) {
  const identity = fileIdentityByPath.get(path) || physicalFileIdentityFromPayload(fileStateFor(path));
  fileIdentityByPath.delete(path);
  if (identity && openFilePathByIdentity.get(identity) === path) openFilePathByIdentity.delete(identity);
}

function normalizedFileGitHistory(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && item.ref) : [];
}

function applyFileGitMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  applyFileIdentityMetadata(state, payload);
  const gitHistory = normalizedFileGitHistory(payload.git_history);
  state.gitRoot = payload.git_root ? normalizeDirectoryPath(payload.git_root) : '';
  state.gitTracked = payload.git_tracked === true;
  state.gitHistory = gitHistory;
  state.gitHasHistory = payload.git_has_history === true && gitHistory.length > 1;
  return state;
}

function fileStateHasRepo(path, state) {
  const root = state?.gitRoot ? normalizeDirectoryPath(state.gitRoot) : '';
  const normalized = normalizeDirectoryPath(path || '');
  return Boolean(root && normalized && pathIsInsideDirectory(normalized, root));
}

function fileStateHasUsefulGitHistory(state) {
  return state?.gitTracked === true
    && state?.gitHasHistory === true
    && Array.isArray(state.gitHistory)
    && state.gitHistory.length > 1;
}

function ensureFileState(path, defaults = null) {
  if (!path) return null;
  let state = fileState.get(path);
  if (!state) {
    state = defaults && typeof defaults === 'object' ? defaults : {};
    fileState.set(path, state);
  } else if (defaults && typeof defaults === 'object' && state !== defaults) {
    Object.assign(state, defaults);
  }
  return normalizeFileStateRecord(state);
}

function fileStateFor(path) {
  const state = path ? fileState.get(path) : null;
  return state ? normalizeFileStateRecord(state) : null;
}

function setFileState(path, state) {
  if (!path) return null;
  const previous = fileStateFor(path);
  if (previous && previous !== state && state && typeof state === 'object') {
    if (!(state.editorTabItems instanceof Set)) state.editorTabItems = previous.editorTabItems;
    if (!(state.ownerSessions instanceof Set)) state.ownerSessions = previous.ownerSessions;
    if (!(state.viewMode instanceof Map)) state.viewMode = previous.viewMode;
    if (!(state.previewZoom instanceof Map)) state.previewZoom = previous.previewZoom;
    if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = previous.blame;
    if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = previous.conflictDialogOpen;
    if (!Object.prototype.hasOwnProperty.call(state, 'realpath')) state.realpath = previous.realpath;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileId')) state.fileId = previous.fileId;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileIdentity')) state.fileIdentity = previous.fileIdentity;
  }
  const normalized = normalizeFileStateRecord(state);
  fileState.set(path, normalized);
  registerFileIdentityForPath(path, normalized);
  return normalized;
}

function deleteFileState(path) {
  if (!path) return false;
  clearOpenFileIdentityPath(path);
  return fileState.delete(path);
}

function fileEditorTabItemsForPath(path) {
  return Array.from(fileStateFor(path)?.editorTabItems || []);
}

function fileHasEditorTab(path) {
  return fileEditorTabItemsForPath(path).length > 0;
}

function addFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  const state = ensureFileState(path);
  if (state && item) state.editorTabItems.add(item);
}

function removeFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  fileStateFor(path)?.editorTabItems.delete(item);
}

function fileEditorViewModesForPath(path, create = false) {
  const state = create ? ensureFileState(path) : fileStateFor(path);
  return state?.viewMode || new Map();
}

function normalizedPreviewZoomKey(key) {
  return String(key || 'default');
}

function normalizePreviewZoomState(value) {
  const mode = value?.mode === 'manual' || value?.mode === 'actual' ? value.mode : 'fit';
  const scale = Number.parseFloat(String(value?.scale || ''));
  return {
    mode,
    scale: Number.isFinite(scale) && scale > 0 ? scale : 1,
  };
}

function fileEditorPreviewZoomStateForPath(path, key = 'default') {
  const state = fileStateFor(path);
  return normalizePreviewZoomState(state?.previewZoom?.get(normalizedPreviewZoomKey(key)));
}

function setFileEditorPreviewZoomStateForPath(path, key, zoomState) {
  const state = ensureFileState(path);
  if (!state) return;
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  state.previewZoom.set(normalizedPreviewZoomKey(key), normalizePreviewZoomState(zoomState));
}

function resetFileEditorPreviewZoomStateForPath(path, keyPrefix = '') {
  const state = fileStateFor(path);
  if (!(state?.previewZoom instanceof Map)) return false;
  const prefix = normalizedPreviewZoomKey(keyPrefix);
  let changed = false;
  for (const key of Array.from(state.previewZoom.keys())) {
    if (!prefix || key === prefix || key.startsWith(`${prefix}:`)) {
      state.previewZoom.delete(key);
      changed = true;
    }
  }
  return changed;
}

function editorBlameForPath(path) {
  return fileStateFor(path)?.blame || null;
}

function setEditorBlameForPath(path, blame) {
  const state = ensureFileState(path);
  if (state) state.blame = blame || null;
}

function hasEditorBlameForPath(path) {
  return Boolean(editorBlameForPath(path));
}

function fileConflictDialogOpen(path) {
  return fileStateFor(path)?.conflictDialogOpen === true;
}

function setFileConflictDialogOpen(path, open) {
  const state = ensureFileState(path);
  if (state) state.conflictDialogOpen = open === true;
}

// Normalized view of a session's transcript metadata — prevents each call site from re-implementing
// the same `?.[session]?.project?.git?.root` chain differently.
function sessionTranscriptInfo(session) {
  const info = transcriptMeta.sessions?.[session] || {};
  const git = info.project?.git || {};
  return {
    gitRoot: git.root || '',
    gitCwd: git.cwd || '',
    gitBranch: git.branch || '',
    selectedPath: info.selected_pane?.current_path || '',
    info,
  };
}

// Centralized status-line writers: the err/ok pill markup is defined here, not re-inlined at the ~55
// call sites that report a result. Both take already-built (and esc'd) inner HTML.
function statusErr(html) {
  statusEl.innerHTML = `<span class="err">${html}</span>`;
}

function statusOk(html) {
  statusEl.innerHTML = `<span class="ok">${html}</span>`;
}

function localizedHtml(key, params) {
  return esc(t(key, params));
}

function terminalNotConnectedText(session) {
  return t('terminal.connection.notConnected', {session: sessionLabel(session)});
}

function terminalNotConnectedHtml(session) {
  return esc(terminalNotConnectedText(session));
}

function readStoredTabMetaVisible() {
  return storageGet(tabMetaStorageKey) !== '0';  // absent (null) or anything but '0' => visible
}

function writeStoredTabMetaVisible(value) {
  storageSet(tabMetaStorageKey, value ? '1' : '0');
}

// persist the merged YO!info pane's active sub-tab ('info' | 'yoagent'), default 'info'.
function normalizedInfoSubTab(value) {
  return value === 'yoagent' ? 'yoagent' : 'info';
}

function readStoredInfoSubTab() {
  return normalizedInfoSubTab(storageGet(infoSubTabStorageKey));
}

function writeStoredInfoSubTab(value) {
  storageSet(infoSubTabStorageKey, normalizedInfoSubTab(value));
}

function readStoredEditorWrap() {
  return storageGet(fileEditorWrapStorageKey) === '1';
}

function writeStoredEditorWrap(value) {
  storageSet(fileEditorWrapStorageKey, value ? '1' : '0');
}

function readStoredEditorLineNumbers() {
  return storageGet(fileEditorLineNumbersStorageKey) === '1';
}

function writeStoredEditorLineNumbers(value) {
  storageSet(fileEditorLineNumbersStorageKey, value ? '1' : '0');
}

function defaultCollapsedPreferenceSections() {
  return new Set(['General', 'Appearance', 'Performance', 'Notifications', 'Terminal / Editor', 'File Explorer', 'Finder', 'Uploads']);
}

function readStoredCollapsedPreferenceSections() {
  const raw = storageGet(preferencesCollapsedStorageKey);
  if (!raw) return defaultCollapsedPreferenceSections();
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return defaultCollapsedPreferenceSections();
    return new Set(parsed.filter(item => typeof item === 'string' && item));
  } catch (_) {
    return defaultCollapsedPreferenceSections();
  }
}

function writeStoredCollapsedPreferenceSections() {
  storageSet(preferencesCollapsedStorageKey, JSON.stringify(Array.from(collapsedPreferenceSections)));
}

function cleanDiffRef(value, fallback = '') {
  const text = String(value || '').trim();
  if (!text) return fallback;
  if (/[\x00\r\n]/.test(text)) return fallback;
  return text;
}

function readStoredDiffRef(key, fallback) {
  return cleanDiffRef(storageGet(key), fallback);
}

function writeStoredDiffRefs() {
  storageSet(diffRefFromStorageKey, diffRefFrom);
  storageSet(diffRefToStorageKey, diffRefTo);
  // C6: persist the per-repo overrides alongside the global default.
  try {
    storageSet(diffRefsByRepoStorageKey, JSON.stringify(diffRefsByRepo || {}));
  } catch (_error) {
    storageSet(diffRefsByRepoStorageKey, '{}');
  }
}

function readStoredDiffRefsByRepo() {
  // C6: restore {repoPath: {from, to}}; tolerate corrupt/absent storage by returning an empty map.
  try {
    const parsed = JSON.parse(storageGet(diffRefsByRepoStorageKey) || '{}');
    if (!parsed || typeof parsed !== 'object') return {};
    const result = {};
    for (const [repo, refs] of Object.entries(parsed)) {
      if (typeof repo !== 'string' || !refs || typeof refs !== 'object') continue;
      const from = cleanDiffRef(refs.from, '');
      const to = cleanDiffRef(refs.to, '');
      if (from || to) result[repo] = {from: from || 'HEAD', to: to || 'current'};
    }
    return result;
  } catch (_error) {
    return {};
  }
}

function normalizeFileExplorerTreeDateMode(value) {
  return fileExplorerTreeDateModes.includes(value) ? value : 'none';
}

function readStoredFileExplorerTreeDateMode() {
  const value = storageGet(fileExplorerTreeDateModeStorageKey);
  if (value !== null) return normalizeFileExplorerTreeDateMode(value);
  return storageGet(fileExplorerTreeShowDatesStorageKey) === '1' ? 'date' : 'none';
}

function writeStoredFileExplorerTreeDateMode(value) {
  storageSet(fileExplorerTreeDateModeStorageKey, normalizeFileExplorerTreeDateMode(value));
}

function normalizeSessionFilesSortMode(value) {
  if (value === 'mtime') return 'newest';
  if (value === 'name') return 'az';
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'newest';
}

function readStoredFileExplorerTreeSortMode() {
  const value = storageGet(fileExplorerTreeSortStorageKey);
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az';
}

function writeStoredFileExplorerTreeSortMode(value) {
  storageSet(fileExplorerTreeSortStorageKey, ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az');
}

function normalizeStoredFileExplorerIndexedDir(path) {
  const normalized = normalizeDirectoryPath(expandUserPath(path));
  return normalized.startsWith('/') ? normalized : '';
}

function readStoredFileExplorerIndexedDirs() {
  const paths = readStoredJson(fileExplorerIndexedDirsStorageKey, []);
  return new Set((Array.isArray(paths) ? paths : []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean));
}

function writeStoredFileExplorerIndexedDirs() {
  const paths = Array.from(fileExplorerIndexedDirs || [])
    .map(normalizeStoredFileExplorerIndexedDir)
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right));
  storageSet(fileExplorerIndexedDirsStorageKey, JSON.stringify(Array.from(new Set(paths))));
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
  return storageGet(fileExplorerRootModeStorageKey) === 'fixed' ? 'fixed' : 'sync';
}

function writeStoredFileExplorerRootMode(mode) {
  storageSet(fileExplorerRootModeStorageKey, mode === 'sync' ? 'sync' : 'fixed');
}

function normalizeFileExplorerMode(mode) {
  return mode === 'diff' || mode === 'tabber' ? mode : 'files';
}

function readStoredFileExplorerMode() {
  const stored = storageGet(fileExplorerModeStorageKey);
  if (stored === 'diff' || stored === 'files') return stored;
  return storageGet(legacyFileExplorerChangesHiddenStorageKey) === '0' ? 'diff' : 'files';
}

function writeStoredFileExplorerMode(mode) {
  storageSet(fileExplorerModeStorageKey, normalizeFileExplorerMode(mode));
}

function normalizeEditorSchemeId(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'light' || normalized === 'white') return defaultLightEditorScheme;
  const legacySchemePrefix = ['vs', 'code'].join('');
  const aliases = {
    [`${legacySchemePrefix}-dark-plus`]: 'popular-ide-dark-plus',
    [`${legacySchemePrefix}-light-plus`]: 'popular-ide-light-plus',
  };
  const id = aliases[normalized] || normalized;
  return EDITOR_SCHEMES[id] ? id : defaultEditorScheme;
}

function normalizeGlobalThemeMode(value = globalThemeMode) {
  // Default to the LIVE globalThemeMode (like resolvedGlobalThemeMode) so a no-arg call reflects the
  // current theme — calling it with no argument used to fall through to defaultGlobalTheme ('dark'),
  // which made the View -> Theme menu always mark Dark as active regardless of the real theme.
  const normalized = String(value || '').trim().toLowerCase();
  return ['system', 'dark', 'light'].includes(normalized) ? normalized : defaultGlobalTheme;
}

function globalThemeMediaQuery() {
  try { return window.matchMedia?.('(prefers-color-scheme: dark)') || null; }
  catch (_) { return null; }
}

function systemPrefersDarkTheme() {
  const query = globalThemeMediaQuery();
  return query ? query.matches === true : true;
}

function resolvedGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return systemPrefersDarkTheme() ? 'dark' : 'light';
  return normalized;
}

function globalThemeIsDark(mode = globalThemeMode) {
  return resolvedGlobalThemeMode(mode) === 'dark';
}

function globalThemeLabel(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return `System (${resolvedGlobalThemeMode(mode)})`;
  return normalized === 'dark' ? 'Dark' : 'Light';
}

function nextGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return 'dark';
  if (normalized === 'dark') return 'light';
  return 'system';
}

function normalizeTerminalThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return ['dark', 'light', 'follow-app'].includes(normalized) ? normalized : defaultTerminalTheme;
}

function resolvedTerminalThemeMode(mode = terminalThemeMode, appMode = globalThemeMode) {
  const normalized = normalizeTerminalThemeMode(mode);
  return normalized === 'follow-app' ? resolvedGlobalThemeMode(appMode) : normalized;
}

function terminalThemeForGlobalTheme(mode = globalThemeMode) {
  const theme = TERMINAL_THEMES[resolvedTerminalThemeMode(terminalThemeMode, mode)] || TERMINAL_THEMES.dark;
  return {...theme};
}

// on a WHITE (light) terminal, agents emit 24-bit truecolor escapes tuned for a dark
// terminal that render faint on white. xterm's minimumContrastRatio auto-darkens ANY text color
// (including app 24-bit colors) against the bg.
// the DARK terminal used to keep 1 (no adjustment), which left low-contrast cells alone — so
// an agent composer that draws light text on an ANSI-white box (Codex's input, ~contrast 1) was
// white-on-white. Use a moderate 3 for dark: enough to force that composer to a readable foreground,
// low enough that intentionally-dim dark-palette text (already at/above 3:1) is mostly untouched. Light
// stays at the stricter WCAG-AA 4.5 (faint colors on white need more help).
function terminalMinimumContrastRatio(mode = globalThemeMode) {
  return resolvedTerminalThemeMode(terminalThemeMode, mode) === 'light' ? 4.5 : 3;
}

function normalizeEditorThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (['inherit', 'system', 'global', 'auto', ''].includes(normalized)) return editorThemeInheritMode;
  return normalizeEditorSchemeId(normalized);
}

function normalizeEditorPreviewDisplayMode(value) {
  return String(value || '').trim().toLowerCase() === 'vanilla' ? 'vanilla' : 'theme';
}

function normalizeEditorSchemeForMode(value, dark) {
  const id = normalizeEditorSchemeId(value);
  const scheme = EDITOR_SCHEMES[id];
  if (scheme && scheme.dark === dark) return id;
  return dark ? defaultEditorScheme : defaultLightEditorScheme;
}

function activeEditorScheme() {
  if (fileEditorThemeMode === editorThemeInheritMode) {
    const inherited = configuredEditorSchemeForMode(globalThemeIsDark());
    return EDITOR_SCHEMES[inherited] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
  }
  return EDITOR_SCHEMES[normalizeEditorSchemeId(fileEditorThemeMode)] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
}

function configuredEditorSchemeForMode(dark) {
  const path = dark ? 'appearance.editor_dark_color_scheme' : 'appearance.editor_light_color_scheme';
  const fallback = dark ? defaultEditorScheme : defaultLightEditorScheme;
  return normalizeEditorSchemeForMode(initialSetting(path, fallback), dark);
}

function readStoredEditorThemeMode() {
  return normalizeEditorThemeMode(storageGet(fileEditorThemeModeStorageKey) || editorThemeInheritMode);
}

function writeStoredEditorThemeMode(mode) {
  storageSet(fileEditorThemeModeStorageKey, normalizeEditorThemeMode(mode));
}

function readStoredEditorPreviewDisplayMode() {
  return normalizeEditorPreviewDisplayMode(storageGet(fileEditorPreviewDisplayModeStorageKey) || 'theme');
}

function writeStoredEditorPreviewDisplayMode(mode) {
  storageSet(fileEditorPreviewDisplayModeStorageKey, normalizeEditorPreviewDisplayMode(mode));
}

function readConfiguredEditorScheme() {
  return normalizeEditorThemeMode(readStoredEditorThemeMode());
}

function syncPressedButton(button, active, options = {}) {
  if (!button) return;
  const activeClass = options.activeClass || 'active';
  button.classList.toggle(activeClass, active);
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  const label = active ? options.labelOn : options.labelOff;
  if (label) {
    button.title = label;
    button.setAttribute('aria-label', label);
  }
}

function syncFileExplorerHiddenButton(button) {
  syncPressedButton(button, fileExplorerShowHidden, {
    labelOn: 'Hide dotfiles (.*)',
    labelOff: 'Show hidden files (dotfiles)',
  });
}

function fileExplorerTreeDateModeLabel(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return t(`finder.dateMode.${normalized}`);
}

function fileExplorerTreeDateModeButtonLabel(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return normalized === 'none' ? t('finder.dateMode.date') : fileExplorerTreeDateModeLabel(normalized);
}

function fileExplorerTreeDateModeTitle(mode = fileExplorerTreeDateMode) {
  return t('finder.dateMode.title', {
    mode: fileExplorerTreeDateModeLabel(mode),
    none: fileExplorerTreeDateModeLabel('none'),
    date: fileExplorerTreeDateModeLabel('date'),
    relative: fileExplorerTreeDateModeLabel('relative'),
  });
}

function syncFileExplorerTreeDateButton(button) {
  if (!button) return;
  const mode = normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode);
  const active = mode !== 'none';
  button.classList.toggle('active', active);
  button.dataset.dateMode = mode;
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  button.textContent = fileExplorerTreeDateModeButtonLabel(mode);
  const label = fileExplorerTreeDateModeTitle(mode);
  button.title = label;
  button.setAttribute('aria-label', label);
}

function syncFileExplorerTreeDateButtons(scope = document) {
  for (const button of scope.querySelectorAll?.('[data-file-explorer-tree-dates]') || []) {
    syncFileExplorerTreeDateButton(button);
  }
}

function nextFileExplorerTreeDateMode(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  const index = fileExplorerTreeDateModes.indexOf(normalized);
  return fileExplorerTreeDateModes[(index + 1) % fileExplorerTreeDateModes.length];
}

function refreshFileExplorerTreeDateModeSurfaces() {
  syncFileExplorerTreeDateButtons();
  if (typeof refreshFileExplorerTrees === 'function') {
    void refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  }
  if (typeof renderFileExplorerChangesPanels === 'function') renderFileExplorerChangesPanels({force: true});
}

function setFileExplorerTreeDateMode(mode) {
  const next = normalizeFileExplorerTreeDateMode(mode);
  if (next === fileExplorerTreeDateMode) return;
  fileExplorerTreeDateMode = next;
  writeStoredFileExplorerTreeDateMode(fileExplorerTreeDateMode);
  refreshFileExplorerTreeDateModeSurfaces();
}

function cycleFileExplorerTreeDateMode() {
  setFileExplorerTreeDateMode(nextFileExplorerTreeDateMode());
}

function renderTabMetaToggle() {
  document.body?.classList.toggle('tab-meta-hidden', !tabMetaVisible);
  if (!tabMetaToggle) return;
  syncPressedButton(tabMetaToggle, tabMetaVisible, {
    labelOn: 'Hide tab metadata',
    labelOff: 'Show tab metadata',
  });
}

function toggleTabMetadata() {
  tabMetaVisible = !tabMetaVisible;
  writeStoredTabMetaVisible(tabMetaVisible);
  renderTabMetaToggle();
  renderSessionButtons();
  scheduleTopbarMetricsUpdate();
}

function recordFocusNavTransition(previousItem, nextItem) {
  if (!nextItem) return;
  if (previousItem && previousItem !== nextItem) recordEditorNav(previousItem);
  recordEditorNav(nextItem);
}

function rememberActivePaneItem(item) {
  if (!item || !itemIsActivePaneTab(item)) return;
  lastActivePaneItem = item;
  if (!isFileExplorerItem(item)) lastActiveNonFileExplorerPaneItem = item;
}

function visualActivePaneItem() {
  if (focusedPanelItem && itemIsActivePaneTab(focusedPanelItem)) return focusedPanelItem;
  if (lastActivePaneItem && itemIsActivePaneTab(lastActivePaneItem)) return lastActivePaneItem;
  return null;
}

function seedVisualActivePaneItem(preferredItems = []) {
  const candidates = [
    ...preferredItems,
    focusedPanelItem,
    focusedTerminal,
    lastFocusedTmuxSession,
    ...activePaneItems(),
  ];
  const item = candidates.find(candidate => candidate && itemIsActivePaneTab(candidate));
  if (item) lastActivePaneItem = item;
  return item || null;
}

function setFocusedTerminal(session, options = {}) {
  const previousItem = focusedPanelItem;
  if (previousItem !== session) capturePaneViewStateForItemIfPresent(previousItem);
  focusedTerminal = session;
  focusedPanelItem = session;
  rememberActivePaneItem(session);
  clearPendingFileEditorFocusExcept(session);
  if (isTmuxSession(session)) lastFocusedTmuxSession = session;
  dismissAttentionAlertsForSession(session);
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  if (options.userInitiated === true) {
    rememberFileExplorerExplicitSyncSession(session);
    scheduleFileExplorerActiveTabSync(session, {explicit: true});
    recordFocusNavTransition(previousItem, session);
  }
  else recordAutoFocusNav(session, previousItem);
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
  const previousItem = focusedPanelItem;
  if (previousItem !== item) capturePaneViewStateForItemIfPresent(previousItem);
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  rememberActivePaneItem(item);
  clearPendingFileEditorFocusExcept(item);
  if (isTmuxSession(item)) {
    lastFocusedTmuxSession = item;
    dismissAttentionAlertsForSession(item);
  }
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  if (options.userInitiated === true) {
    if (isTmuxSession(item)) rememberFileExplorerExplicitSyncSession(item);
    if (isFileEditorItem(item)) {
      activeFile = fileItemPath(item);
      scheduleFileExplorerActiveFileReveal(activeFile);
    }
    const explicitFinderSync = isTmuxSession(item) || isFileEditorItem(item);
    if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item, {explicit: explicitFinderSync});
    recordFocusNavTransition(previousItem, item);
  }
  else recordAutoFocusNav(item, previousItem);
}

let autoFocusNavTimer = null;
// an AUTO-FOCUS-driven focus change records back/forward nav "as if clicked", so Back
// returns to where you were. Debounced by a short dwell so rapid auto-focus flapping (focus chasing
// needs-attention) records only the focus that LANDS, not every transient flip. User clicks already
// record immediately (activatePaneTab userInitiated); a back/forward re-activation lands on the item
// already at the stack head, so recordEditorNav's consecutive-dedupe makes this a no-op there.
function recordAutoFocusNav(item, previousItem = null) {
  if (!autoFocusEnabled || !item) return;
  if (autoFocusNavTimer) clearTimeout(autoFocusNavTimer);
  autoFocusNavTimer = setTimeout(() => {
    autoFocusNavTimer = null;
    if (focusedPanelItem === item) recordFocusNavTransition(previousItem, item);
  }, 500);
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
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
  const run = () => terminals.get(session)?.term?.focus?.();
  if (delay > 0) setTimeout(run, delay);
  else run();
}

function clearFocusForInactiveLayout() {
  if (focusedTerminal && !activeSessions.includes(focusedTerminal)) focusedTerminal = null;
  if (focusedPanelItem && !activeSessions.includes(focusedPanelItem)) focusedPanelItem = null;
  if (lastActivePaneItem && !itemIsActivePaneTab(lastActivePaneItem)) lastActivePaneItem = null;
  if (lastActiveNonFileExplorerPaneItem && !itemIsActivePaneTab(lastActiveNonFileExplorerPaneItem)) lastActiveNonFileExplorerPaneItem = null;
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
  const activeItem = visualActivePaneItem() || seedVisualActivePaneItem();
  for (const [item, panel] of panelNodes.entries()) {
    panel.classList.toggle('focused-pane', item === activeItem);
    panel.classList.toggle('active-pane', item === activeItem);
  }
  // Re-color the active terminal's cursor yellow (and revert the rest) whenever focus moves.
  if (typeof refreshActiveTerminalCursor === 'function') refreshActiveTerminalCursor();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function stripTrailingEllipsisText(value) {
  return String(value ?? '').replace(/\s*(?:\.{3}|…)+\s*$/u, '').trimEnd();
}

function movingEllipsisHtml(className = '') {
  const classes = ['moving-ellipsis', className].filter(Boolean).join(' ');
  return `<span class="${esc(classes)}" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`;
}

function textWithMovingEllipsisHtml(value, className = '') {
  return `${esc(stripTrailingEllipsisText(value))}${movingEllipsisHtml(className)}`;
}

const searchRankWeights = Object.freeze({
  perChar: 8,
  contiguous: 10,
  contiguousSubstring: 30000,
  wordStart: 6,
  gapPenalty: 0.2,
  haystackLengthPenalty: 0.01,
  anchorPrimary: 20000,
  anchorSecondary: 12000,
  fieldIndexPenalty: 20,
  domainPrior: {
    files: {file: 6000, pane: 3000, command: 0, setting: 0},
    command: {pane: 6000, command: 3000, setting: 3000, file: 0},
  },
  fileNamePrefix: 3500,
  fileNameContains: 1800,
  fileNameSubsequence: 600,
  finderAlias: 25000,
  finderAliasFilesMode: 2500,
  paneExactIdentifier: 30000,
  recentSelectionBase: 1000,
  recencyCap: 900,
  recencyHalfLifeSeconds: 7 * 24 * 60 * 60,
  repoAffinity: 400,
  mixWindow: 8,
  mixSecondarySlots: 2,
  mixFirstSecondaryIndex: 2,
  mixSecondaryStep: 3,
});

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
    score += searchRankWeights.perChar;
    if (contiguous) score += searchRankWeights.contiguous;
    if (wordStart) score += searchRankWeights.wordStart;
    score -= Math.max(0, index - position) * searchRankWeights.gapPenalty;
    previousIndex = index;
    position = index + 1;
    indexes.push(index);
  }
  if (needle.length >= 3 && haystack.includes(needle)) score += searchRankWeights.contiguousSubstring;
  return {score: score - Math.max(0, haystack.length - needle.length) * searchRankWeights.haystackLengthPenalty, indexes};
}

function fuzzySubsequenceScore(query, text) {
  const match = fuzzySubsequenceMatch(query, text);
  return match ? match.score : Number.NEGATIVE_INFINITY;
}

function fuzzyCanonicalPrefixText(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function fuzzyFieldStartsWithQuery(query, text) {
  const needle = fuzzyCanonicalPrefixText(query);
  return Boolean(needle) && fuzzyCanonicalPrefixText(text).startsWith(needle);
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
      let fieldScore = fuzzySubsequenceScore(token, value);
      if (Number.isFinite(fieldScore) && fuzzyFieldStartsWithQuery(token, value)) {
        fieldScore += index === 0 ? searchRankWeights.anchorPrimary : searchRankWeights.anchorSecondary;
      }
      if (Number.isFinite(fieldScore)) best = Math.max(best, fieldScore - index * searchRankWeights.fieldIndexPenalty);
    }
    if (!Number.isFinite(best)) return Number.NEGATIVE_INFINITY;
    total += best;
  }
  return total;
}

function fuzzyHighlightHtml(query, text) {
  const value = String(text ?? '');
  // Highlight EVERY query token's subsequence match, not just the first — mirrors fuzzySearchScore, which
  // scores all tokens. So "pa exploration" highlights both "PA" and "exploration", not only "pa".
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  const indexes = new Set();
  for (const token of tokens) {
    const match = fuzzySubsequenceMatch(token, value);
    if (match) for (const matchIndex of match.indexes) indexes.add(matchIndex);
  }
  if (!indexes.size) return esc(value);
  const chars = Array.from(value);
  const parts = [];
  let index = 0;
  while (index < chars.length) {
    if (!indexes.has(index)) {
      parts.push(esc(chars[index]));
      index += 1;
      continue;
    }
    const start = index;
    while (index < chars.length && indexes.has(index)) index += 1;
    parts.push(`<mark class="fuzzy-match">${esc(chars.slice(start, index).join(''))}</mark>`);
  }
  return parts.join('');
}

function restoreElementScrollPosition(element, scrollTop, scrollLeft) {
  if (!element) return;
  element.scrollTop = scrollTop;
  element.scrollLeft = scrollLeft;
  requestAnimationFrame(() => {
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  });
}

function replaceHtmlPreservingScroll(element, html) {
  if (!element) return;
  const scrollTop = element.scrollTop || 0;
  const scrollLeft = element.scrollLeft || 0;
  element.innerHTML = html;
  restoreElementScrollPosition(element, scrollTop, scrollLeft);
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
const terminalWrappedUrlMaxRows = 8;
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
    if (!opened) statusErr(localizedHtml('status.browserBlockedLink', {link}));
  } catch (error) {
    statusErr(localizedHtml('status.openLinkFailed', {error}));
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

// did `line` fill the terminal to its right edge? translateToString(true) trims trailing
// blanks, so a row whose printed text reaches `cols` had a non-blank last cell — evidence the content
// was CLIPPED at the edge and wrapped, not that it merely happened to end at the row. Used to gate the
// hanging-URL stitch: a complete URL ending well short of the edge (e.g. `See https://x.com`) is NOT a
// clipped URL and must not absorb the indented next row. cols<=0 (unknown width) → treat as not clipped.
function terminalRowReachesRightEdge(line, cols) {
  if (!Number.isFinite(cols) || cols <= 0) return false;
  return terminalBufferLineText(line).length >= Math.max(1, cols - 1);
}

// does the joined group text end mid-URL? True when the LAST url token reaches the very end of
// the string (no trailing whitespace/terminator). Used to decide whether to stitch a hanging-indent
// continuation row onto the group — only EXTEND a url token that runs off the row's right edge.
function terminalTailIsUnterminatedUrl(text) {
  if (!text) return false;
  terminalLinkPattern.lastIndex = 0;
  let last = null;
  for (const match of text.matchAll(terminalLinkPattern)) last = match;
  if (!last) return false;
  return last.index + last[0].length === text.length;
}

function terminalRowStartsNewUrlToken(text) {
  return /^(?:https?:\/\/|file:\/\/|www\.)/i.test(String(text || ''));
}

// a row shaped like a hanging-indent continuation — optional leading whitespace, then a URL-valid char
// (not a quote/bracket). Returns {indent, text} with the indent stripped, or null. isWrapped rows are
// not hanging continuations (they are real terminal soft-wraps and are handled by the isWrapped sweep).
function terminalRowHangingShape(buffer, index) {
  const line = buffer.getLine(index);
  if (!line || line.isWrapped === true) return null;
  const raw = terminalBufferLineText(line);
  const match = /^(\s*)([^\s<>"'`])/.exec(raw);
  if (!match) return null;
  const text = raw.slice(match[1].length);
  if (!text || terminalRowStartsNewUrlToken(text)) return null;
  return {indent: match[1].length, text};
}

// row `index` continues the URL printed on row `index - 1` — its own row shape is a hanging
// indent AND the previous row's tail is an unterminated url token. Gates tightly so ordinary indented
// prose under a line that merely happens to end at a URL is not merged.
// ALSO require the previous row to reach the terminal's right edge, proving the URL was
// clipped/hard-wrapped. Without this, a complete URL at end-of-line falsely swallows the next row.
function terminalRowIsHangingUrlContinuation(buffer, index, cols, depth = 0) {
  if (depth >= terminalWrappedUrlMaxRows) return false;
  const shape = terminalRowHangingShape(buffer, index);
  if (!shape) return false;
  const prev = buffer.getLine(index - 1);
  if (!prev) return false;
  if (!terminalRowReachesRightEdge(prev, cols)) return false;
  return terminalTailIsUnterminatedUrl(terminalBufferLineText(prev))
    || terminalRowIsHangingUrlContinuation(buffer, index - 1, cols, depth + 1);
}

function terminalWrappedLineGroup(term, y) {
  const buffer = term.buffer?.active;
  if (!buffer?.getLine) return null;
  // terminal width gates the hanging-URL stitch (a clipped URL fills to the right edge).
  const cols = Number(term.cols) || 0;
  const requested = Math.max(0, y - 1);
  if (!buffer.getLine(requested)) return null;
  // Walk back to the logical line's first row: over terminal soft-wraps (isWrapped) AND over
  // hanging-indent URL continuations (agent-hard-wrapped URLs whose continuation is its own
  // non-wrapped, indented row). So querying ANY row of the wrapped URL yields the same full group.
  let start = requested;
  for (;;) {
    if (start > 0 && buffer.getLine(start)?.isWrapped === true) { start -= 1; continue; }
    if (start > 0 && terminalRowIsHangingUrlContinuation(buffer, start, cols)) { start -= 1; continue; }
    break;
  }
  // Forward pass from start. Include soft-wrap rows (indent 0) and, while the joined text still ends
  // mid-URL, hanging-indent continuation rows (indent stripped for link matching, but recorded so the
  // underline maps back to the row's REAL columns). Stop at the first row that is neither.
  const rows = [];
  let offset = 0;
  let joined = '';
  let index = start;
  for (;;) {
    let text;
    let indent = 0;
    if (index === start) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (buffer.getLine(index)?.isWrapped === true) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (terminalTailIsUnterminatedUrl(joined) && terminalRowReachesRightEdge(buffer.getLine(index - 1), cols)) {
      const shape = terminalRowHangingShape(buffer, index);
      if (!shape) break;
      indent = shape.indent;
      text = shape.text;
    } else {
      break;
    }
    rows.push({y: index + 1, text, indent, start: offset, end: offset + text.length});
    offset += text.length;
    joined += text;
    index += 1;
    if (rows.length >= terminalWrappedUrlMaxRows) break;
    if (!buffer.getLine(index)) break;
  }
  return {text: joined, rows};
}

function terminalWrappedOffsetPosition(group, offset, endPosition = false) {
  const target = endPosition ? Math.max(0, offset - 1) : offset;
  const row = group.rows.find(candidate => target >= candidate.start && target < candidate.end) || group.rows[group.rows.length - 1];
  if (!row) return null;
  // A stitched continuation row had `indent` leading spaces stripped before joining, so its real
  // terminal column is shifted right by that indent.
  return {x: Math.max(1, target - row.start + 1 + (row.indent || 0)), y: row.y};
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
    try {
      await clipboard.writeText(value);
      return;
    } catch (_) {
      // Fall through to execCommand. Some browsers expose navigator.clipboard
      // but reject writes on self-signed or permission-limited pages.
    }
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

function copyTextToClipboardViaCopyEvent(text) {
  const value = String(text ?? '');
  let copied = false;
  const onCopy = event => {
    if (!event?.clipboardData) return;
    event.clipboardData.setData('text/plain', value);
    event.preventDefault();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
    copied = true;
  };
  document.addEventListener?.('copy', onCopy, true);
  try {
    return document.execCommand?.('copy') === true && copied;
  } finally {
    document.removeEventListener?.('copy', onCopy, true);
  }
}

// ONE clipboard-write chain for terminal-initiated copies (shortcut copy AND the OSC 52
// bridge): synchronous copy-event first — it stays inside any live user activation — then the async
// navigator.clipboard path as fallback. Status text reports success/failure either way.
function writeTerminalTextToClipboard(text, label = 'copied') {
  if (copyTextToClipboardViaCopyEvent(text)) {
    copyDebug('clipboard', {via: 'copy-event', chars: String(text ?? '').length, ok: true});
    statusEl.textContent = label;
    return;
  }
  copyTextToClipboard(text)
    .then(() => {
      copyDebug('clipboard', {via: 'async', chars: String(text ?? '').length, ok: true});
      statusEl.textContent = label;
    })
    .catch(error => {
      copyDebug('clipboard', {via: 'async', chars: String(text ?? '').length, ok: false, error: String(error)});
      statusErr(localizedHtml('status.copyFailed', {error}));
    });
}

// opt-in live instrumentation for the copy path. Set storage key 'yolomux.debugCopy' to '1'
// and every copy decision logs ONE compact console line — enough to see which link breaks without
// changing behavior.
function copyDebugEnabled() {
  return storageGet('yolomux.debugCopy') === '1';
}

function copyDebug(stage, fields = {}) {
  if (!copyDebugEnabled()) return;
  const parts = Object.entries(fields).map(([key, value]) => `${key}=${value}`).join(' ');
  console.log(`[copy-debug] ${stage} ${parts}`);
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

function makeButton(options = {}) {
  const button = document.createElement('button');
  button.type = options.type || 'button';
  if (options.id) button.id = options.id;
  if (options.className) button.className = options.className;
  if (options.role) button.setAttribute('role', options.role);
  if (options.html !== undefined) button.innerHTML = options.html;
  else if (options.label !== undefined) button.textContent = options.label;
  button.disabled = options.disabled === true;
  if (options.title) button.title = options.title;
  if (options.ariaLabel) button.setAttribute('aria-label', options.ariaLabel);
  if (options.pressed !== undefined) button.setAttribute('aria-pressed', options.pressed ? 'true' : 'false');
  if (options.checked !== undefined) {
    button.setAttribute('aria-checked', options.checked ? 'true' : 'false');
    if (options.checked === true) button.dataset.checked = 'true';
  }
  if (options.dataset) {
    for (const [key, value] of Object.entries(options.dataset)) {
      if (value !== undefined && value !== null) button.dataset[key] = String(value);
    }
  }
  if (typeof options.onClick === 'function') button.addEventListener('click', options.onClick);
  return button;
}

function delegate(parent, type, selector, handler, options = {}) {
  if (!parent || typeof handler !== 'function') return null;
  const listener = event => {
    const target = event.target?.closest?.(selector);
    if (!target || !parent.contains(target)) return;
    handler(event, target);
  };
  parent.addEventListener(type, listener, options);
  return listener;
}

// One owner for the per-session/-item DOM id scheme. Both the element that sets the id and every
// getElementById/querySelector that looks it up route through these, so the prefix lives in one place
// (the ids are produced + consumed across 7 partials). Changing a prefix is then a one-line edit.
const panelDomId = item => `panel-${item}`;
const paneTabDomId = session => `panel-tab-${session}`;
const terminalDomId = session => `term-${session}`;
const transcriptDomId = session => `transcript-${session}`;
const summaryDomId = session => `summary-${session}`;

// One inflight-dedup wrapper: run makeRequest() at most once per key while a call is outstanding, so
// concurrent callers for the same key share the single in-flight promise and clean up after it settles.
// When canReuse is false the caller wants an untracked fresh fetch, so it runs without registering. The
// TTL cache-hit check and any resource-specific guards stay at the call site; this owns only the inflight
// Map bookkeeping that was hand-rolled identically per filesystem resource (dir listing, path info, blame).
function dedupeInflight(inflight, key, canReuse, makeRequest) {
  if (canReuse) {
    const existing = inflight.get(key);
    if (existing) return existing;
  }
  const request = makeRequest();
  if (!canReuse) return request;
  inflight.set(key, request);
  return (async () => {
    try {
      return await request;
    } finally {
      if (inflight.get(key) === request) inflight.delete(key);
    }
  })();
}

function appendContextMenuButton(menu, label, handler, closeMenu, options = {}) {
  const iconHtml = options.iconHtml ? stripTitleAttrs(options.iconHtml) : '';
  const shortcutHtml = options.shortcut ? `<span class="context-menu-shortcut">${esc(options.shortcut)}</span>` : '';
  const buttonHtml = iconHtml || shortcutHtml
    ? `<span class="context-menu-line">${iconHtml ? `<span class="context-menu-icon">${iconHtml}</span>` : ''}<span class="context-menu-label">${esc(label)}</span>${shortcutHtml}</span>`
    : undefined;
  const button = makeButton({
    ...options,
    html: buttonHtml,
    label: buttonHtml ? undefined : label,
    ariaLabel: options.ariaLabel || label,
    role: options.checked !== undefined ? 'menuitemcheckbox' : 'menuitem',
  });
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
  return terminalContextMenu.isOpen() || fileContextMenu.isOpen() || sessionContextMenu.isOpen() || linkContextMenu.isOpen();
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

function closeLinkContextMenu() {
  linkContextMenu.close();
}

function closeContextMenus() {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeLinkContextMenu();
}

// right-click menu for links in AI/markdown content — Copy URL / Open URL. Bound on the
// YO!agent body and markdown previews via installLinkContextMenu(container).
function showLinkContextMenu(anchor, x, y) {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeOtherSessionPopovers(null);
  const href = anchor?.href || '';
  if (!href) return;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu link-context-menu';
  menu.setAttribute('role', 'menu');
  appendContextMenuButton(menu, t('contextmenu.copyUrl'), () => copyTextToClipboard(href), closeLinkContextMenu);
  appendContextMenuButton(menu, t('contextmenu.openUrl'), () => window.open(href, '_blank', 'noopener,noreferrer'), closeLinkContextMenu);
  linkContextMenu.open(menu, x, y);
}

function installLinkContextMenu(container) {
  if (!container || container.dataset.linkContextMenuBound === '1') return;
  container.dataset.linkContextMenuBound = '1';
  container.addEventListener('contextmenu', event => {
    const anchor = event.target?.closest?.('a[href]');
    if (!anchor || !container.contains(anchor)) return;
    event.preventDefault();
    event.stopPropagation();
    showLinkContextMenu(anchor, event.clientX, event.clientY);
  });
}

function nodeInsideElement(element, node) {
  if (!element || !node) return false;
  if (element === node) return true;
  if (element.contains?.(node)) return true;
  let current = node.parentElement || node.parentNode || null;
  while (current) {
    if (current === element) return true;
    current = current.parentElement || current.parentNode || null;
  }
  return false;
}

function browserSelectionTextInside(container) {
  if (!container) return '';
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  const text = String(selection?.toString?.() || '');
  if (!text) return '';
  const anchorNode = selection.anchorNode || null;
  const focusNode = selection.focusNode || null;
  if (!anchorNode && !focusNode) return '';
  return nodeInsideElement(container, anchorNode) || nodeInsideElement(container, focusNode) ? text : '';
}

function terminalSelectedText(term, container = null) {
  return term.getSelection?.() || browserSelectionTextInside(container);
}

async function copyTerminalSelection(session, term, options = {}, container = null) {
  // N7: the context menu passes the selection captured at right-click time, because by the time the user
  // clicks the menu the live selection may be gone (focus moved to the menu).
  const selected = options.selectionText != null ? options.selectionText : terminalSelectedText(term, container);
  if (!selected) {
    statusEl.textContent = 'nothing selected';
    return;
  }
  const text = options.dedent ? dedentSelectionText(selected) : selected;
  try {
    await copyTextToClipboard(text);
    statusEl.textContent = options.dedent ? 'copied without indent' : 'copied';
  } catch (error) {
    statusErr(localizedHtml('status.copyFailed', {error}));
  }
}

function copyTerminalSelectionFromShortcut(session, term, options = {}, container = null) {
  const selected = terminalSelectedText(term, container);
  if (!selected) {
    statusEl.textContent = 'nothing selected';
    return false;
  }
  const text = options.dedent ? dedentSelectionText(selected) : selected;
  writeTerminalTextToClipboard(text, options.dedent ? 'copied without indent' : 'copied');
  return true;
}

function copyTerminalSelectionToClipboardEvent(session, term, event, container = null) {
  const selected = terminalSelectedText(term, container);
  if (!selected || !event?.clipboardData) return false;
  event.clipboardData.setData('text/plain', selected);
  event.preventDefault();
  event.stopPropagation();
  statusEl.textContent = 'copied';
  term.clearSelection?.();
  return true;
}

async function copyTmuxSelectionToClipboard(session) {
  const payloadPromise = fetchTmuxSelectionText(session);
  try {
    const {payload, text} = await copyDeferredTextToClipboard(payloadPromise);
    const chars = Number.isFinite(Number(payload.chars)) ? Number(payload.chars) : text.length;
    statusEl.textContent = `copied ${chars} chars from tmux`;
    return true;
  } catch (error) {
    if (error?.noClipboardText) {
      statusEl.textContent = error.message || 'nothing selected';
      return false;
    }
    statusErr(localizedHtml('status.copyFailed', {error}));
    return false;
  }
}

async function fetchTmuxSelectionText(session) {
  const payload = await apiFetchJson(`/api/tmux-copy-selection?session=${encodeURIComponent(session)}`, {method: 'POST'});
  const text = payload?.copied ? String(payload.text || '') : '';
  if (!text) {
    const error = new Error(payload?.error === 'tmux copy mode is not active' ? 'nothing selected' : (payload?.error || 'nothing selected'));
    error.noClipboardText = true;
    throw error;
  }
  return {payload, text};
}

async function copyDeferredTextToClipboard(payloadPromise) {
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard?.write && typeof globalThis.ClipboardItem === 'function' && typeof globalThis.Blob === 'function') {
    const textBlob = payloadPromise.then(({text}) => new Blob([text], {type: 'text/plain'}));
    try {
      await clipboard.write([new ClipboardItem({'text/plain': textBlob})]);
      return await payloadPromise;
    } catch (error) {
      if (error?.noClipboardText) throw error;
      const result = await payloadPromise;
      await copyTextToClipboard(result.text);
      return result;
    }
  }
  const result = await payloadPromise;
  await copyTextToClipboard(result.text);
  return result;
}

// ROOT CAUSE: while Claude (or any TUI) owns the mouse inside tmux, the visible selection is the
// APP's — a plain drag never creates an xterm selection, and the copied text instead arrives as an
// OSC 52 clipboard escape (app -> tmux `set-clipboard` passthrough -> our PTY -> xterm.js). xterm.js
// DROPS OSC 52 unless a handler is registered, so those copies silently vanished. This bridge decodes
// the escape and writes the browser clipboard. It also catches tmux copy-mode `copy-pipe` buffers.
// Payload format: "Pc;Pd" — Pc selects clipboard(s) (c/p/s/q...), Pd is base64 text or '?' (a READ
// request, which we never answer so apps cannot exfiltrate the browser clipboard).
function osc52ClipboardText(data) {
  const raw = String(data ?? '');
  const semi = raw.indexOf(';');
  if (semi < 0) return null;
  const payload = raw.slice(semi + 1);
  if (!payload || payload === '?') return null;
  try {
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const decoded = new TextDecoder('utf-8', {fatal: false}).decode(bytes);
    return decoded || null;
  } catch (_error) {
    return null; // not valid base64: ignore rather than copy garbage
  }
}

function installTerminalOsc52Bridge(session, term) {
  if (!term?.parser?.registerOscHandler) return false;
  term.parser.registerOscHandler(52, data => {
    const text = osc52ClipboardText(data);
    copyDebug('osc52', {session, payloadChars: String(data ?? '').length, textChars: text ? text.length : 0});
    if (text) writeTerminalTextToClipboard(text, `copied ${text.length} chars`);
    return true; // consumed either way; '?' queries get no reply
  });
  return true;
}

function handleTerminalCopyShortcutKeydown(session, term, container, event) {
  if (event.type !== 'keydown') return false;
  if (event.code !== 'KeyC' && event.key?.toLowerCase() !== 'c') return false;
  const isTmuxCopyShortcut = event.altKey
    && !event.shiftKey
    && ((isMacPlatform() && event.metaKey && !event.ctrlKey)
      || (!isMacPlatform() && event.ctrlKey && !event.metaKey));
  if (isTmuxCopyShortcut) {
    event.preventDefault();
    copyTmuxSelectionToClipboard(session);
    return true;
  }
  const isCmdC = event.metaKey && !event.ctrlKey && !event.altKey;
  const isCtrlC = event.ctrlKey && !event.metaKey && !event.altKey;
  if (!isCmdC && !isCtrlC) return false;
  const xtermSelected = term.getSelection?.() || '';
  const browserSelected = browserSelectionTextInside(container);
  const selected = xtermSelected || browserSelected;
  copyDebug('shortcut', {
    session,
    combo: isCmdC ? 'cmd-c' : 'ctrl-c',
    xtermSel: xtermSelected.length,
    browserSel: browserSelected.length,
    branch: selected ? 'copy' : (isCmdC ? 'no-selection' : 'sigint'),
  });
  if (!selected) {
    if (isCmdC) {
      event.preventDefault();
      // in a Claude/tmux pane the APP owns the mouse, so a plain drag never creates an xterm
      // selection — tell the user the working gestures instead of dead-ending.
      statusEl.textContent = isMacPlatform()
        ? 'nothing selected — Option-drag selects while Claude/tmux owns the mouse; Cmd-Option-C copies the tmux selection'
        : 'nothing selected — Shift-drag selects while the app owns the mouse; Ctrl-Alt-C copies the tmux selection';
      return true;
    }
    return false; // no selection: let Ctrl-C through as SIGINT
  }
  event.preventDefault();
  copyTerminalSelectionFromShortcut(session, term, {}, container);
  term.clearSelection?.(); // so a second Ctrl-C falls through to SIGINT
  return true;
}

function installTerminalCopyShortcut(session, term, container = null) {
  // Ctrl-C / Cmd-C copy the xterm selection. Plain Ctrl-C with NO selection
  // must still send SIGINT to the PTY, and Cmd-C must stay browser/xterm copy
  // only. Tmux copy-mode text has a separate explicit shortcut/menu action.
  container?.addEventListener?.('keydown', event => {
    if (!handleTerminalCopyShortcutKeydown(session, term, container, event)) return;
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }, {capture: true});
  term.attachCustomKeyEventHandler?.(event => {
    return handleTerminalCopyShortcutKeydown(session, term, container, event) ? false : true;
  });
}

function showTerminalContextMenu(session, term, x, y, container = null, presetSelection = '') {
  closeFileContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu';
  menu.setAttribute('role', 'menu');
  // N7: prefer the selection captured at right-click time over a live re-read (which can be empty by now).
  const selected = presetSelection || terminalSelectedText(term, container);
  const items = [
    ['Copy', false],
    ['Copy without indent', true],
  ];
  for (const [label, dedent] of items) {
    appendContextMenuButton(menu, label, () => copyTerminalSelection(session, term, {dedent, selectionText: selected}, container), closeTerminalContextMenu, {disabled: !selected});
  }
  appendContextMenuSeparator(menu);
  appendContextMenuButton(menu, t('terminal.copyTmuxSelection'), () => copyTmuxSelectionToClipboard(session), closeTerminalContextMenu);
  terminalContextMenu.open(menu, x, y);
}

function installTerminalContextMenu(session, term, container) {
  // N7: right-click must NOT clear the highlight. xterm clears its selection on mousedown, so capture the
  // selected text on the right-mousedown (capture phase, before xterm's handler) and stopPropagation so
  // xterm never processes that mousedown — the highlight stays visible AND the menu has the text even if
  // focus moves to the menu. No preventDefault, so the contextmenu event still fires normally.
  let rightClickSelection = '';
  container.addEventListener('mousedown', event => {
    if (event.button !== 2) return;
    rightClickSelection = terminalSelectedText(term, container);
    event.stopPropagation();
  }, {capture: true});
  container.addEventListener('contextmenu', event => {
    event.preventDefault();
    event.stopPropagation();
    showTerminalContextMenu(session, term, event.clientX, event.clientY, container, rightClickSelection);
    rightClickSelection = '';
  });
}

function showTabContextMenu(item, x, y, options = {}) {
  if (!isPinnableTab(item) && !isTmuxSession(item)) return;
  closeAppMenus();
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeOtherSessionPopovers(null);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu session-context-menu';
  menu.setAttribute('role', 'menu');
  if (isPinnableTab(item)) {
    const pinned = tabIsPinned(item);
    appendContextMenuButton(
      menu,
      pinned ? t('tab.unpin') : t('tab.pin'),
      () => setTabPinned(item, !pinned),
      closeSessionContextMenu,
      {
        checked: pinned,
        iconHtml: appMenuUiIcon('pin', pinned),
        shortcut: `${appShortcutText('K')} Enter`,
      },
    );
  }
  if (isTmuxSession(item)) {
    if (isPinnableTab(item)) appendContextMenuSeparator(menu);
    const renameAction = options.tab ? () => beginPaneTabRename(options.tab, item) : () => renameTmuxSession(item);
    for (const command of tmuxSessionActionCommands(item, {renameAction, includeKill: false})) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {disabled: command.disabled, checked: command.checked});
    }
    const viewItems = tmuxSessionViewCommands(item).filter(command => command.label !== 'Pane details');
    for (const command of viewItems) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {
        disabled: command.disabled,
        checked: command.checked,
        title: command.detail || '',
      });
    }
    appendContextMenuSeparator(menu);
    const killItem = tmuxSessionKillCommand(item);
    appendContextMenuButton(menu, killItem.label, killItem.action, closeSessionContextMenu, {disabled: killItem.disabled, className: 'danger'});
  }
  sessionContextMenu.open(menu, x, y);
}

function showSessionContextMenu(session, x, y, options = {}) {
  showTabContextMenu(session, x, y, options);
}

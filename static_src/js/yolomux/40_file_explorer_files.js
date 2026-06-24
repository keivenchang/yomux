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

function shareReadOnlyFinderStateIsHostOwned() {
  return shareViewMode && !shareWriteMode && !applyingShareRemoteUiState;
}

async function openFileExplorerAt(path, options = {}) {
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  const root = normalizeDirectoryPath(expandUserPath(path));
  if (options.manualSelection === true) {
    cancelPendingFileExplorerActiveSync();
  }
  const openGeneration = ++fileExplorerOpenGeneration;
  const openStillCurrent = () => openGeneration === fileExplorerOpenGeneration;
  const showPendingRoot = options.manualSelection === true || options.showPending === true;
  if (showPendingRoot) {
    fileExplorerManualSelectionActive = options.manualSelection === true;
    setFileExplorerPathDisplay(root);
    renderFileExplorerRootModeControls();
    renderFileExplorerTreeSearching(root);
  } else if (options.syncSelection === true) {
    fileExplorerManualSelectionActive = false;
  }
  const entries = await fetchDirectory(root, {user: options.user === true || options.manualSelection === true});
  if (!openStillCurrent()) return false;
  if (!entries) {
    const error = currentFileExplorerListError(root);
    if (error) setFileExplorerPathDisplay(root, {error});
    if (showPendingRoot) renderFileExplorerTreeStatus(error || `Cannot open ${root}`, {root, error: true});
    return false;
  }
  const previousExpanded = options.preserveExpanded ? Array.from(fileExplorerExpanded) : [];
  const scrollPositions = options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  fileExplorerRoot = root;
  pruneFileExplorerSelectionForRoot(fileExplorerRoot);
  fileExplorerManualSelectionActive = options.manualSelection === true;
  if (options.syncSelection !== true) cancelPendingFileExplorerActiveSync({invalidateOpen: false});
  setFileExplorerPathDisplay(fileExplorerRoot);
  renderFileExplorerRootModeControls();
  fileExplorerExpanded.clear();
  if (fileExplorerTree) {
    renderTreeChildren(fileExplorerTree, fileExplorerRoot, entries, 0);
  }
  if (options.refreshPanels !== false) {
    await refreshFileExplorerPanelTrees({root: fileExplorerRoot, entries, restoreState: false});
    if (!openStillCurrent()) return false;
  }
  if (previousExpanded.length) {
    await restoreFileExplorerExpandedPaths(previousExpanded, fileExplorerRoot);
    if (!openStillCurrent()) return false;
  }
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
  updateFileExplorerCurrentFileHighlight();
  scheduleShareTopologySnapshot('finder-root');
  return true;
}

function resetFileExplorerAppliedSyncPlan() {
  fileExplorerLastAppliedSyncPlanKey = '';
}

async function saveFileExplorerRootMode(mode) {
  if (readOnlyMode) return;
  try {
    await saveSettingsPatch(settingPatch('file_explorer.root_mode', mode));
  } catch (_) {}
}

// Short-TTL cache of directory listings. A live Differ/Finder of a busy session re-renders many times a
// second and each render walks every expanded dir, which without this fans out into a /api/fs/list storm
// (one request per dir per render — the cause of the 8001 fs/list loop). Repeated fetches of the same dir
// within the TTL reuse the listing; the change-detection sweep and explicit reloads pass {fresh:true}.
const fileExplorerDirListingCache = new Map();
const fileExplorerDirListingInflight = new Map();
const fileExplorerPathInfoCache = new Map();
const fileExplorerPathInfoInflight = new Map();
const fileExplorerFsBatchQueue = [];
const fileExplorerFsBatchPending = new Map();
const fileExplorerFsBatchDelayMs = 8;
let fileExplorerFsBatchSeq = 0;
let fileExplorerFsBatchTimer = null;
let fileExplorerPushRefreshDepth = 0;
const FILE_TREE_BASE_PAD_PX = 8;
const FILE_TREE_COMPACT_PAD_PX = 4;
const FILE_TREE_INDENT_PX = 14;
const FILE_TREE_ROW_HANDLERS = Object.freeze([
  'onpointerdown',
  'onpointerup',
  'onclick',
  'ondblclick',
  'oncontextmenu',
  'ondragstart',
  'ondragend',
]);

function fileTreeRowPadding(depth, compact = false) {
  const safeDepth = Math.max(0, Number(depth) || 0);
  const base = compact ? FILE_TREE_COMPACT_PAD_PX : FILE_TREE_BASE_PAD_PX;
  return `${base + safeDepth * FILE_TREE_INDENT_PX}px`;
}

function fileTreeRowDepth(row, compact = false) {
  const value = parseInt(row?.style?.paddingLeft || '', 10);
  const base = compact ? FILE_TREE_COMPACT_PAD_PX : FILE_TREE_BASE_PAD_PX;
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.round((value - base) / FILE_TREE_INDENT_PX));
}

function setRowDataset(row, key, value) {
  if (!row?.dataset || !key) return;
  if (value === undefined || value === null || value === '') delete row.dataset[key];
  else row.dataset[key] = String(value);
}

function clearFileTreeRowHandlers(row) {
  for (const key of FILE_TREE_ROW_HANDLERS) row[key] = null;
}

function setTreeItemAria(row, {selected = false, expandable = false, expanded = false} = {}) {
  row.setAttribute('role', 'treeitem');
  row.setAttribute('aria-selected', selected ? 'true' : 'false');
  if (expandable) row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  else row.removeAttribute('aria-expanded');
}

function clearFileExplorerListError(path = '') {
  const root = normalizeDirectoryPath(path || '');
  if (!root || !fileExplorerLastListError || normalizeDirectoryPath(fileExplorerLastListError.path) === root) {
    fileExplorerPathError = '';
    fileExplorerLastListError = null;
  }
}

function setFileExplorerListError(path, error, status = 0) {
  const root = normalizeDirectoryPath(path || '');
  fileExplorerPathError = error || '';
  fileExplorerLastListError = root && error ? {path: root, status, error, network: !status} : null;
}

function currentFileExplorerListError(path) {
  const root = normalizeDirectoryPath(path || '');
  return root && normalizeDirectoryPath(fileExplorerLastListError?.path || '') === root
    ? fileExplorerLastListError.error || ''
    : '';
}

function fileExplorerFsCacheTtlMs() {
  return Math.max(0, numberSetting('file_explorer.dir_cache_ms', 1500) || 0);
}

function fileExplorerFsBatchKey(type, path) {
  return `${type}\x1f${normalizeDirectoryPath(path)}`;
}

function fileExplorerFsBatchSingleUrl(type, path) {
  const normalized = normalizeDirectoryPath(path);
  return type === 'list'
    ? `/api/fs/list?path=${encodeURIComponent(normalized)}`
    : `/api/fs/info?path=${encodeURIComponent(normalized)}`;
}

function suppressBackgroundFilesystemFetch(options = {}) {
  if (options.force === true || options.user === true) return false;
  return clientPushConnectedForData() && !fileExplorerUserIsActive();
}

function scheduleFileExplorerFsBatchFlush() {
  if (fileExplorerFsBatchTimer) return;
  fileExplorerFsBatchTimer = setTimeout(flushFileExplorerFsBatch, fileExplorerFsBatchDelayMs);
}

function fetchFilesystemBatchItem(type, path, options = {}) {
  const normalized = normalizeDirectoryPath(path);
  const key = fileExplorerFsBatchKey(type, normalized);
  if (options.dedupe !== false) {
    const existing = fileExplorerFsBatchPending.get(key);
    if (existing) return existing.promise;
  }
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  const item = {id: ++fileExplorerFsBatchSeq, type, path: normalized, key, resolve, reject};
  if (options.dedupe !== false) fileExplorerFsBatchPending.set(key, {promise, item});
  fileExplorerFsBatchQueue.push(item);
  scheduleFileExplorerFsBatchFlush();
  return promise;
}

async function settleFileExplorerFsBatchItemFallback(item) {
  try {
    item.resolve(await apiFetchJson(fileExplorerFsBatchSingleUrl(item.type, item.path)));
  } catch (error) {
    item.reject(error);
  } finally {
    if (fileExplorerFsBatchPending.get(item.key)?.item === item) {
      fileExplorerFsBatchPending.delete(item.key);
    }
  }
}

function settleFileExplorerFsBatchItem(item, response) {
  if (fileExplorerFsBatchPending.get(item.key)?.item === item) {
    fileExplorerFsBatchPending.delete(item.key);
  }
  if (response?.ok) {
    item.resolve(response.payload || {});
    return;
  }
  const error = new Error(response?.error || `fs ${item.type} failed`);
  error.status = Number(response?.status) || 0;
  error.payload = response || {};
  item.reject(error);
}

async function flushFileExplorerFsBatch() {
  fileExplorerFsBatchTimer = null;
  const items = fileExplorerFsBatchQueue.splice(0);
  if (!items.length) return;
  const requests = items.map(item => ({id: item.id, type: item.type, path: item.path}));
  try {
    const payload = await apiFetchJson('/api/fs/batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({requests}),
    });
    const responses = new Map((payload.responses || []).map(response => [response.id, response]));
    for (const item of items) {
      settleFileExplorerFsBatchItem(item, responses.get(item.id) || {ok: false, status: 500, error: 'missing fs batch response'});
    }
  } catch (_) {
    await Promise.all(items.map(settleFileExplorerFsBatchItemFallback));
  }
}

async function fetchDirectory(path, options = {}) {
  const root = normalizeDirectoryPath(path);
  const canReuse = options.fresh !== true;
  const dirListingTtlMs = fileExplorerFsCacheTtlMs();
  if (canReuse && dirListingTtlMs > 0) {
    const cached = fileExplorerDirListingCache.get(root);
    if (cached && Date.now() - cached.at < dirListingTtlMs) {
      clearFileExplorerListError(root);
      return cached.entries;
    }
  }
  if (fileExplorerPushRefreshDepth > 0) {
    clearFileExplorerListError(root);
    return null;
  }
  if (suppressBackgroundFilesystemFetch(options)) {
    clearFileExplorerListError(root);
    return null;
  }
  return dedupeInflight(fileExplorerDirListingInflight, root, canReuse, () => (async () => {
    try {
      hydrateFileExplorerRepoInfoCache();
      const payload = await fetchFilesystemBatchItem('list', root, {dedupe: canReuse});
      const entries = payload.entries || [];
      clearFileExplorerListError(root);
      cacheFileExplorerRepoInfoEntries(root, entries);
      markNewDirectoryEntries(root, entries);
      if (options.recordSignature !== false) recordDirectorySignature(root, entries);
      setLimitedMapEntry(fileExplorerDirListingCache, root, {entries, at: Date.now()}, fileExplorerMemoryCacheLimit);
      return entries;
    } catch (err) {
      const status = Number(err?.status) || 0;
      fileExplorerPathError = status
        ? err.message || `Cannot open ${root} (${status})`
        : `Cannot open ${root}: ${err}`;
      setFileExplorerListError(root, fileExplorerPathError, status);
      console.warn(status ? 'fs list failed' : 'fs list error', root, status || err, fileExplorerPathError);
      return null;
    }
  })());
}

function invalidateFileExplorerFsCaches() {
  fileExplorerDirListingCache.clear();
  fileExplorerPathInfoCache.clear();
  fileExplorerDirectorySignatures.clear();
}

function entriesByDirFromFilesystemPush(payload = {}) {
  const entriesByDir = new Map();
  const directories = Array.isArray(payload.directories) ? payload.directories : [];
  for (const item of directories) {
    const path = normalizeDirectoryPath(item?.path || item?.data?.path || '');
    const data = item?.data && typeof item.data === 'object' ? item.data : {};
    const entries = Array.isArray(data.entries) ? data.entries : null;
    if (!path || !entries || item.ok === false || Number(item.status || 200) >= 400) continue;
    entriesByDir.set(path, entries);
    cacheFileExplorerRepoInfoEntries(path, entries);
    markNewDirectoryEntries(path, entries);
    recordDirectorySignature(path, entries);
    setLimitedMapEntry(fileExplorerDirListingCache, path, {entries, at: Date.now()}, fileExplorerMemoryCacheLimit);
  }
  return entriesByDir;
}

function fileEntryStatusFromWatchFilePayload(item) {
  const signature = Array.isArray(item?.signature) ? item.signature : [];
  const path = String(item?.path || signature[0] || '');
  if (!path) return {path: '', entry: null, missing: false, error: '', network: false};
  const kind = String(signature[1] || '');
  if (kind === 'missing') return {path, entry: null, missing: true, error: `path not found: ${path}`, network: false};
  if (kind !== 'file' && kind !== 'dir') return {path, entry: null, missing: false, error: `invalid file signature: ${path}`, network: false};
  const mtime = Number(signature[2]) || 0;
  const size = Number(signature[3]);
  return {
    path,
    entry: {
      name: basenameOf(path),
      kind,
      mtime_ns: mtime,
      mtime,
      size: Number.isFinite(size) ? size : null,
    },
    missing: false,
    error: '',
    network: false,
  };
}

async function refreshOpenFilesFromPush(payload = {}) {
  const files = Array.isArray(payload.files) ? payload.files : [];
  for (const item of files) {
    const fetched = fileEntryStatusFromWatchFilePayload(item);
    if (!fetched.path) continue;
    const state = openFiles.get(fetched.path);
    if (!state || state.loading) continue;
    await refreshOpenFileFromFetchedStatus(fetched.path, state, fetched);
  }
}

async function refreshFileExplorerFromPush(payload = {}) {
  const entriesByDir = entriesByDirFromFilesystemPush(payload);
  if (!entriesByDir.size) return;
  fileExplorerPushRefreshDepth += 1;
  try {
    const root = normalizeDirectoryPath(currentFileExplorerRoot());
    if (fileExplorerPaneIsOpen() && entriesByDir.has(root)) {
      await refreshFileExplorerTreesInPlace({
        root,
        entries: entriesByDir.get(root),
        preserveExpanded: true,
        preserveScroll: true,
        entriesByDir,
      });
    }
    const openFileDirs = Array.from(openFiles.keys()).map(path => normalizeDirectoryPath(dirnameOf(path)));
    if (openFileDirs.every(path => fileExplorerDirListingCache.has(path))) await refreshOpenFilesIfChanged();
  } finally {
    fileExplorerPushRefreshDepth = Math.max(0, fileExplorerPushRefreshDepth - 1);
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
  setLimitedMapEntry(fileExplorerDirectorySignatures, normalizeDirectoryPath(path), directoryEntriesSignature(entries), fileExplorerMemoryCacheLimit);
}

function pruneExpiredNewFileEntries(now = Date.now()) {
  for (const [path, until] of fileExplorerNewEntryUntil.entries()) {
    if (!until || until <= now) fileExplorerNewEntryUntil.delete(path);
  }
}

function markNewDirectoryEntries(path, entries) {
  const root = normalizeDirectoryPath(path);
  const names = new Set((Array.isArray(entries) ? entries : []).map(entry => entry?.name).filter(Boolean));
  const previous = fileExplorerKnownEntryNames.get(root);
  const now = Date.now();
  pruneExpiredNewFileEntries(now);
  if (previous && fileExplorerNewEntryHighlightMs > 0) {
    for (const name of names) {
      if (!previous.has(name)) setLimitedMapEntry(fileExplorerNewEntryUntil, childPath(root, name), now + fileExplorerNewEntryHighlightMs, fileExplorerMemoryCacheLimit);
    }
  }
  setLimitedMapEntry(fileExplorerKnownEntryNames, root, names, fileExplorerMemoryCacheLimit);
}

function fileExplorerEntryIsNew(path) {
  const until = fileExplorerNewEntryUntil.get(path);
  if (!until) return false;
  if (until <= Date.now()) {
    fileExplorerNewEntryUntil.delete(path);
    return false;
  }
  return true;
}

function scheduleNewEntryClassRemoval(row, path) {
  const until = fileExplorerNewEntryUntil.get(path);
  if (!row || !until) return;
  const delay = Math.max(0, until - Date.now());
  setTimeout(() => {
    if (row.isConnected && fileExplorerEntryIsNew(path) === false) row.classList.remove('new-entry');
    else if (row.isConnected && fileExplorerNewEntryUntil.get(path) <= Date.now()) row.classList.remove('new-entry');
  }, delay + 50);
}

function normalizeDirectoryPath(path) {
  const text = String(path || '').replace(/\/+$/, '');
  return text || '/';
}

function normalizeFileExplorerRepoInfo(repo, fallbackRoot = '') {
  if (!repo || typeof repo !== 'object') return null;
  const root = normalizeDirectoryPath(repo.root || fallbackRoot);
  if (!root) return null;
  const dirtyCount = Number(repo.dirty_count);
  const ahead = Number(repo.ahead);
  const behind = Number(repo.behind);
  return {
    root,
    name: String(repo.name || basenameOf(root) || ''),
    branch: String(repo.branch || ''),
    dirty_count: Number.isFinite(dirtyCount) ? dirtyCount : null,
    upstream: String(repo.upstream || ''),
    ahead: Number.isFinite(ahead) ? ahead : 0,
    behind: Number.isFinite(behind) ? behind : 0,
  };
}

function hydrateFileExplorerRepoInfoCache() {
  if (fileExplorerRepoInfoCacheLoaded) return;
  fileExplorerRepoInfoCacheLoaded = true;
  try {
    const raw = window.localStorage?.getItem(fileExplorerRepoInfoStorageKey);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const repos = Array.isArray(parsed?.repos) ? parsed.repos : [];
    for (const item of repos) {
      const path = normalizeDirectoryPath(item?.path || item?.repo?.root || '');
      const repo = normalizeFileExplorerRepoInfo(item?.repo, path);
      if (path && repo) setLimitedMapEntry(fileExplorerRepoInfoCache, path, repo, fileExplorerMemoryCacheLimit);
    }
  } catch (_) {}
}

function persistFileExplorerRepoInfoCache() {
  try {
    const repos = Array.from(fileExplorerRepoInfoCache.entries())
      .filter(([path, repo]) => path && repo?.root && normalizeDirectoryPath(repo.root) === path)
      .sort(([leftPath], [rightPath]) => leftPath.localeCompare(rightPath))
      .slice(-200)
      .map(([path, repo]) => ({path, repo}));
    window.localStorage?.setItem(fileExplorerRepoInfoStorageKey, JSON.stringify({repos}));
  } catch (_) {}
}

function cacheFileExplorerRepoInfo(path, repo, options = {}) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path || repo?.root || '');
  const info = normalizeFileExplorerRepoInfo(repo, normalized);
  if (!normalized || !info) return false;
  const repoRoot = normalizeDirectoryPath(info.root);
  setLimitedMapEntry(fileExplorerRepoInfoCache, normalized, info, fileExplorerMemoryCacheLimit);
  if (repoRoot && repoRoot !== normalized) setLimitedMapEntry(fileExplorerRepoInfoCache, repoRoot, info, fileExplorerMemoryCacheLimit);
  if (options.persist !== false) persistFileExplorerRepoInfoCache();
  return true;
}

function cacheFileExplorerRepoInfoEntries(parentPath, entries) {
  if (!Array.isArray(entries)) return;
  hydrateFileExplorerRepoInfoCache();
  let changed = false;
  for (const entry of entries) {
    if (entry?.kind !== 'dir' || entry.is_repo !== true || !entry.repo) continue;
    const fullPath = childPath(parentPath, entry.name);
    changed = cacheFileExplorerRepoInfo(fullPath, entry.repo, {persist: false}) || changed;
  }
  if (changed) persistFileExplorerRepoInfoCache();
}

function currentFileExplorerRoot() {
  return normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
}

function pruneFileExplorerSelectionForRoot(root) {
  const normalizedRoot = normalizeDirectoryPath(root);
  for (const selectedPath of Array.from(fileExplorerSelectedPaths)) {
    if (!pathIsInsideDirectory(selectedPath, normalizedRoot) || selectedPath === normalizedRoot) {
      fileExplorerSelectedPaths.delete(selectedPath);
    }
  }
  if (
    fileExplorerSelectionAnchor
    && (!pathIsInsideDirectory(fileExplorerSelectionAnchor, normalizedRoot) || fileExplorerSelectionAnchor === normalizedRoot)
  ) {
    fileExplorerSelectionAnchor = null;
    fileExplorerSelectionLead = null;
  }
  if (!fileExplorerSelectedPaths.size) { fileExplorerSelectionAnchor = null; fileExplorerSelectionLead = null; }
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

function syncFileExplorerRootModeButton(button) {
  const label = t('finder.toolbar.syncLabel');
  const title = t('finder.toolbar.syncTitle');
  syncPressedButton(button, fileExplorerRootMode === 'sync', {labelOn: title, labelOff: title});
  if (button) button.textContent = label;
}

function renderFileExplorerRootModeControls() {
  for (const button of fileExplorerRootModeButtons()) {
    syncFileExplorerRootModeButton(button);
  }
  renderFileExplorerQuickAccessControls();
}

function fileExplorerQuickAccessPaths() {
  const paths = initialSetting('file_explorer.quick_access_paths', ['~', '/', '/tmp']);
  return Array.isArray(paths) ? paths.filter(path => typeof path === 'string' && path.trim()) : ['~', '/', '/tmp'];
}

function displayQuickAccessPath(path) {
  const value = String(path || '').trim();
  if (value === '~') return '~';
  if (value === '/' || value === '/*') return '/*';
  if (value.startsWith('/')) return normalizeDirectoryPath(value);
  return value;
}

function expandQuickAccessPath(path) {
  const value = String(path || '').trim();
  if (value === '~') return homePath || '/';
  if (value === '/' || value === '/*') return '/';
  if (value.startsWith('~/')) return normalizeDirectoryPath(`${homePath || ''}/${value.slice(2)}`);
  return value;
}

function renderQuickAccessInto(container) {
  if (!container) return;
  const currentRoot = normalizeDirectoryPath(currentFileExplorerRoot());
  const sync = fileExplorerRootMode === 'sync';
  container.replaceChildren(...fileExplorerQuickAccessPaths().map(path => {
    const expanded = normalizeDirectoryPath(expandQuickAccessPath(path));
    const active = !sync && expanded === currentRoot;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'file-explorer-quick-access-button';
    button.textContent = displayQuickAccessPath(path);
    button.title = `Open ${path}`;
    button.dataset.quickPath = path;
    syncPressedButton(button, active, {labelOn: button.title, labelOff: button.title});
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      openFileExplorerQuickAccessPath(path);
    });
    return button;
  }));
}

function renderFileExplorerQuickAccessControls() {
  fileExplorerQuickAccess?.replaceChildren?.();
  document.querySelectorAll('.file-explorer-quick-access-panel').forEach(container => container.replaceChildren());
}

function setFileExplorerRootMode(mode, options = {}) {
  resetFileExplorerAppliedSyncPlan();
  fileExplorerRootMode = mode === 'sync' ? 'sync' : 'fixed';
  writeStoredFileExplorerRootMode(fileExplorerRootMode);
  renderFileExplorerRootModeControls();
  updateFileExplorerSessionHighlightRows();
  if (options.persist === true) saveFileExplorerRootMode(fileExplorerRootMode);
  if (fileExplorerRootMode === 'sync' && options.sync !== false) {
    scheduleFileExplorerActiveTabSync(fileExplorerExplicitSyncSessionTarget(), {explicit: true});
  }
  scheduleShareTopologySnapshot('finder-root-mode');
}

function fileExplorerRootModeValue() {
  return fileExplorerRootMode;
}

function toggleFileExplorerRootMode() {
  const mode = fileExplorerRootMode === 'sync' ? 'fixed' : 'sync';
  setFileExplorerRootMode(mode, {persist: true});
}

function setFileExplorerManualRootMode() {
  cancelPendingFileExplorerActiveSync();
  if (fileExplorerRootMode !== 'fixed') {
    setFileExplorerRootMode('fixed', {sync: false, persist: true});
  } else {
    renderFileExplorerRootModeControls();
  }
}

function openFileExplorerManualRoot(path) {
  setFileExplorerManualRootMode();
  return openFileExplorerAt(path, {manualSelection: true});
}

function openFileExplorerQuickAccessPath(path) {
  openFileExplorerManualRoot(expandQuickAccessPath(path));
}

function tmuxDirectoryForItem(item) {
  if (!isTmuxSession(item)) return '';
  const path = terminalCurrentPath(item);
  return path ? normalizeDirectoryPath(path) : '';
}

function tmuxGitRootForItem(item) {
  if (!isTmuxSession(item)) return '';
  const root = transcriptMeta.sessions?.[item]?.project?.git?.root || '';
  return root ? normalizeDirectoryPath(root) : '';
}

function activeTmuxDirectoryPath(preferredItem = null) {
  if (preferredItem && !isTmuxSession(preferredItem)) return '';
  for (const item of finderCandidateItems(preferredItem)) {
    const path = tmuxDirectoryForItem(item);
    if (path) return path;
  }
  return '';
}

function activeTmuxGitRootPath(preferredItem = null) {
  if (preferredItem && !isTmuxSession(preferredItem)) return '';
  for (const item of finderCandidateItems(preferredItem)) {
    const root = tmuxGitRootForItem(item);
    if (root) return root;
  }
  return '';
}

function activeTmuxSessionForFinder(preferredItem = null) {
  if (preferredItem) return isTmuxSession(preferredItem) ? preferredItem : '';
  for (const item of finderCandidateItems(preferredItem)) {
    if (isTmuxSession(item)) return item;
  }
  return '';
}

function fileExplorerExplicitSyncSessionTarget() {
  return fileExplorerExplicitSyncSession && isTmuxSession(fileExplorerExplicitSyncSession) && activeSessions.includes(fileExplorerExplicitSyncSession)
    ? fileExplorerExplicitSyncSession
    : '';
}

function fileExplorerSyncCommandSessionTarget() {
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  if (explicitSession) return explicitSession;
  const payloadSession = String(fileExplorerSessionFilesPayload?.session || '');
  if (isTmuxSession(payloadSession) && activeSessions.includes(payloadSession)) return payloadSession;
  return activeTmuxSessionForFinder();
}

function rememberFileExplorerExplicitSyncSession(session) {
  const normalizedSession = String(session || '');
  if (!isTmuxSession(normalizedSession) || !activeSessions.includes(normalizedSession)) return false;
  if (fileExplorerExplicitSyncSession !== normalizedSession) {
    if (fileExplorerExplicitSyncSession) restoreCommittedFileExplorerRootDisplay();
    fileExplorerExplicitSyncSession = normalizedSession;
    cancelPendingFileExplorerActiveSync();
    return true;
  }
  fileExplorerExplicitSyncSession = normalizedSession;
  return true;
}

function fileExplorerRootForOpen(preferredItem = null) {
  if (fileExplorerRootMode === 'sync') {
    const syncPlan = typeof fileExplorerSyncPlan === 'function' ? fileExplorerSyncPlan(preferredItem) : null;
    if (syncPlan?.root) return syncPlan.root;
    return normalizeDirectoryPath(homePath || '/');
  }
  return fileExplorerRoot || homePath || '/';
}

function fileExplorerSyncTargetKey(session, root) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const normalizedSession = String(session || '');
  return normalizedSession && normalizedRoot ? `${normalizedSession}\x1f${normalizedRoot}` : '';
}

function fileExplorerExpandedPathsForRoot(root, paths = Array.from(fileExplorerExpanded)) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  return Array.from(new Set((paths || [])
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot))))
    .sort((left, right) => (
      childPathParts(normalizedRoot, left).length - childPathParts(normalizedRoot, right).length
      || left.localeCompare(right)
    ));
}

function rememberFileExplorerSyncExpandedState(session = fileExplorerVisibleSyncSession, root = fileExplorerVisibleSyncRoot) {
  if (fileExplorerRootMode !== 'sync') return;
  const key = fileExplorerSyncTargetKey(session, root);
  if (!key) return;
  setLimitedMapEntry(fileExplorerExpandedBySyncTarget, key, fileExplorerExpandedPathsForRoot(root), fileExplorerMemoryCacheLimit);
}

function rememberedFileExplorerSyncExpandedPaths(session, root) {
  const key = fileExplorerSyncTargetKey(session, root);
  const paths = key ? fileExplorerExpandedBySyncTarget.get(key) : null;
  return Array.isArray(paths) ? fileExplorerExpandedPathsForRoot(root, paths) : [];
}

function setFileExplorerVisibleSyncTarget(session, root) {
  fileExplorerVisibleSyncSession = String(session || '');
  fileExplorerVisibleSyncRoot = normalizeDirectoryPath(root || '');
}

function sessionFilesRepoRoots(payload = fileExplorerSessionFilesPayload) {
  return Array.from(new Set((Array.isArray(payload?.repos) ? payload.repos : [])
    .map(repo => normalizeDirectoryPath(repo?.repo || repo?.root || ''))
    .filter(path => path && path.startsWith('/'))));
}

function sessionFileAbsolutePath(file) {
  const raw = String(file?.abs_path || file?.path || '');
  if (raw.startsWith('/')) return normalizeDirectoryPath(raw);
  const repo = normalizeDirectoryPath(file?.repo || '');
  return repo && raw ? normalizeDirectoryPath(childPath(repo, raw)) : '';
}

function sessionFileDirectory(file) {
  const path = sessionFileAbsolutePath(file);
  return path ? normalizeDirectoryPath(dirnameOf(path)) : '';
}

function sessionFilesAffectedDirs(payload = fileExplorerSessionFilesPayload) {
  const dirs = new Set(sessionFilesRepoRoots(payload));
  for (const file of Array.isArray(payload?.files) ? payload.files : []) {
    const dir = sessionFileDirectory(file);
    if (dir) dirs.add(dir);
  }
  return Array.from(dirs);
}

function commonAncestorPath(paths) {
  const normalized = Array.from(new Set((paths || [])
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path.startsWith('/'))));
  if (!normalized.length) return '';
  if (normalized.length === 1) return normalized[0];
  const partsList = normalized.map(path => path === '/' ? [] : path.slice(1).split('/').filter(Boolean));
  const common = [];
  for (let index = 0; ; index += 1) {
    const part = partsList[0]?.[index];
    if (!part || !partsList.every(parts => parts[index] === part)) break;
    common.push(part);
  }
  return common.length ? `/${common.join('/')}` : '/';
}

function pathIsShallowerThanHome(path) {
  const root = normalizeDirectoryPath(path);
  const home = normalizeDirectoryPath(homePath || '');
  return Boolean(root && home && root !== home && pathIsInsideDirectory(home, root));
}

function focusedRepoRootForSync(focusedDir, repoRoots = sessionFilesRepoRoots()) {
  const normalizedFocusedDir = normalizeDirectoryPath(focusedDir || '');
  if (!normalizedFocusedDir) return '';
  return repoRoots
    .filter(repo => repo && pathIsInsideDirectory(normalizedFocusedDir, repo))
    .sort((left, right) => right.length - left.length)[0] || normalizedFocusedDir;
}

function sessionFilesPayloadOverlapsFocusedRoot(payload, focusedGitRoot) {
  const root = normalizeDirectoryPath(focusedGitRoot || '');
  if (!root) return true;
  const paths = Array.from(new Set([
    ...sessionFilesRepoRoots(payload),
    ...sessionFilesAffectedDirs(payload),
  ]));
  if (!paths.length) return true;
  return paths.some(path => pathIsInsideDirectory(path, root) || pathIsInsideDirectory(root, path));
}

function firstChildPathUnderRoot(root, path) {
  const parts = childPathParts(root, path);
  return parts.length ? childPath(root, parts[0]) : '';
}

function fileExplorerSyncExpansionTargets(root, affectedDirs = [], repoRoots = []) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  const normalizedRepoRoots = Array.from(new Set(repoRoots
    .map(path => normalizeDirectoryPath(path))
    .filter(Boolean)));
  const repoTargets = normalizedRepoRoots
    .filter(path => path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot));
  const candidates = normalizedRepoRoots.length
    ? repoTargets
    : affectedDirs.map(path => firstChildPathUnderRoot(normalizedRoot, path));
  return Array.from(new Set(candidates
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot))))
    .sort((left, right) => (
      childPathParts(normalizedRoot, left).length - childPathParts(normalizedRoot, right).length
      || left.localeCompare(right)
    ));
}

function fileExplorerSyncPlan(preferredItem = null) {
  const session = isTmuxSession(preferredItem) ? preferredItem : fileExplorerExplicitSyncSessionTarget();
  if (!session) return {session: '', root: normalizeDirectoryPath(homePath || '/'), expandPaths: [], affectedDirs: []};
  const focusedDir = tmuxDirectoryForItem(session);
  const focusedGitRoot = tmuxGitRootForItem(session);
  const payload = fileExplorerSessionFilesPayload;
  const payloadUsable = (!session || !payload?.session || String(payload.session) === String(session))
    && sessionFilesPayloadOverlapsFocusedRoot(payload, focusedGitRoot);
  const affectedDirs = payloadUsable ? sessionFilesAffectedDirs(payload) : [];
  if (!affectedDirs.length && focusedGitRoot && (!focusedDir || pathIsInsideDirectory(focusedDir, focusedGitRoot))) affectedDirs.push(focusedGitRoot);
  if (!affectedDirs.length && focusedDir) affectedDirs.push(focusedDir);
  const repoRoots = payloadUsable ? sessionFilesRepoRoots(payload) : [];
  if (focusedGitRoot && !repoRoots.includes(focusedGitRoot)) repoRoots.push(focusedGitRoot);
  let root = commonAncestorPath(affectedDirs);
  if (!root) root = normalizeDirectoryPath(homePath || '/');
  if (root && pathIsShallowerThanHome(root)) {
    root = focusedRepoRootForSync(focusedDir, repoRoots) || normalizeDirectoryPath(homePath || '/');
  }
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const expandPaths = fileExplorerSyncExpansionTargets(normalizedRoot, affectedDirs, repoRoots);
  return {session, root: normalizedRoot, expandPaths, affectedDirs: Array.from(new Set(affectedDirs.map(path => normalizeDirectoryPath(path))))};
}

function fileExplorerSyncPlanForFile(path) {
  const target = normalizeDirectoryPath(path || '');
  if (!target) return {session: '', root: '', expandPaths: [], affectedDirs: []};
  const repo = typeof fileRepoForPath === 'function' ? normalizeDirectoryPath(fileRepoForPath(target)) : '';
  const currentRoot = normalizeDirectoryPath(currentFileExplorerRoot());
  let root = repo && pathIsInsideDirectory(target, repo) ? repo : '';
  if (!root && currentRoot && pathIsInsideDirectory(target, currentRoot) && target !== currentRoot) root = currentRoot;
  if (!root) root = normalizeDirectoryPath(dirnameOf(target));
  const session = typeof sessionForFileRepo === 'function' ? sessionForFileRepo(target) : '';
  const expandPaths = root && target !== root && pathIsInsideDirectory(target, root) ? [target] : [];
  return {
    session,
    root,
    expandPaths,
    affectedDirs: [normalizeDirectoryPath(dirnameOf(target))].filter(Boolean),
  };
}

function fileExplorerSyncPlanSignature(plan) {
  if (!plan?.root) return '';
  return [plan.session || '', plan.root, ...(plan.expandPaths || [])].join('\x1f');
}

function fileExplorerSyncPlanKey(plan) {
  if (!plan?.root) return '';
  const expandPaths = fileExplorerSyncExpansionPaths(plan)
    .map(path => normalizeDirectoryPath(path))
    .filter(Boolean)
    .sort();
  return [String(plan.session || ''), normalizeDirectoryPath(plan.root), ...expandPaths].join('\x1f');
}

function fileExplorerSyncPlanAlreadyApplied(plan) {
  const key = fileExplorerSyncPlanKey(plan);
  return Boolean(key)
    && key === fileExplorerLastAppliedSyncPlanKey
    && normalizeDirectoryPath(plan.root) === normalizeDirectoryPath(currentFileExplorerRoot());
}

function markFileExplorerSyncPlanApplied(plan) {
  fileExplorerLastAppliedSyncPlanKey = fileExplorerSyncPlanKey(plan);
}

function fileExplorerSyncManualCollapseKey(plan) {
  return plan?.root ? fileExplorerSyncTargetKey(plan.session, plan.root) : '';
}

function rememberFileExplorerSyncManualCollapseState(targetKey = fileExplorerSyncManualCollapseTargetKey) {
  if (!targetKey) return;
  setLimitedMapEntry(fileExplorerSyncManualCollapsedByTarget, targetKey, Array.from(fileExplorerSyncManualCollapsedPaths), fileExplorerMemoryCacheLimit);
}

function resetFileExplorerSyncManualCollapsesIfNeeded(plan) {
  const targetKey = fileExplorerSyncManualCollapseKey(plan);
  if (targetKey !== fileExplorerSyncManualCollapseTargetKey) {
    rememberFileExplorerSyncManualCollapseState();
    fileExplorerSyncManualCollapseTargetKey = targetKey;
    fileExplorerSyncManualCollapsedPaths = new Set(fileExplorerSyncManualCollapsedByTarget.get(targetKey) || []);
  }
  return targetKey;
}

function fileExplorerSyncManualCollapsePlanForPath(path) {
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized) return null;
  const visibleRoot = normalizeDirectoryPath(fileExplorerVisibleSyncRoot || currentFileExplorerRoot());
  if (
    fileExplorerVisibleSyncSession
    && visibleRoot
    && pathIsInsideDirectory(normalized, visibleRoot)
  ) {
    return {session: fileExplorerVisibleSyncSession, root: visibleRoot};
  }
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const explicitPlan = fileExplorerSyncPlan(explicitSession);
  if (explicitPlan?.root && pathIsInsideDirectory(normalized, explicitPlan.root)) return explicitPlan;
  const currentRoot = normalizeDirectoryPath(currentFileExplorerRoot());
  const fallbackSession = explicitSession || fileExplorerVisibleSyncSession || '';
  if (fallbackSession && currentRoot && pathIsInsideDirectory(normalized, currentRoot)) {
    return {session: fallbackSession, root: currentRoot};
  }
  return null;
}

function fileExplorerSyncPathSuppressed(path) {
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized) return false;
  for (const collapsedPath of fileExplorerSyncManualCollapsedPaths) {
    if (pathIsInsideDirectory(normalized, collapsedPath)) return true;
  }
  return false;
}

function fileExplorerSyncExpansionPaths(plan) {
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  return (plan?.expandPaths || []).filter(path => !fileExplorerSyncPathSuppressed(path));
}

function fileExplorerSyncExplicitExpansionTargets(plan, root = plan?.root || currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  return Array.from(new Set([
    ...(plan?.expandPaths || []),
    ...(plan?.affectedDirs || []),
  ]
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot))))
    .sort((left, right) => (
      childPathParts(normalizedRoot, left).length - childPathParts(normalizedRoot, right).length
      || left.localeCompare(right)
    ));
}

function fileExplorerSyncHighlightedRepoRoots(plan, repoRoots) {
  const expansionPaths = new Set(fileExplorerSyncExpansionPaths(plan));
  const root = normalizeDirectoryPath(plan?.root || '');
  return Array.from(repoRoots || [])
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && root && path !== root && pathIsInsideDirectory(path, root) && expansionPaths.has(path));
}

function rememberFileExplorerSyncManualCollapse(path) {
  if (fileExplorerRootMode !== 'sync') return;
  const plan = fileExplorerSyncManualCollapsePlanForPath(path);
  if (!plan?.root || !pathIsInsideDirectory(path, plan.root)) return;
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  fileExplorerSyncManualCollapsedPaths.add(normalizeDirectoryPath(path));
  rememberFileExplorerSyncManualCollapseState();
  resetFileExplorerAppliedSyncPlan();
}

function forgetFileExplorerSyncManualCollapse(path) {
  if (!path) return;
  if (fileExplorerRootMode === 'sync') {
    const plan = fileExplorerSyncManualCollapsePlanForPath(path);
    if (plan) resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  }
  const normalized = normalizeDirectoryPath(path);
  let changed = false;
  for (const collapsedPath of Array.from(fileExplorerSyncManualCollapsedPaths)) {
    if (pathIsInsideDirectory(collapsedPath, normalized) || pathIsInsideDirectory(normalized, collapsedPath)) {
      fileExplorerSyncManualCollapsedPaths.delete(collapsedPath);
      changed = true;
    }
  }
  if (changed) rememberFileExplorerSyncManualCollapseState();
  if (changed) resetFileExplorerAppliedSyncPlan();
}

function emptyFileExplorerSessionHighlightSets() {
  return {repoRoots: new Set(), touchedDirs: new Set(), expandedDirs: new Set()};
}

function fileExplorerSessionHighlightSets(preferredItem = null) {
  if (fileExplorerRootMode !== 'sync') return emptyFileExplorerSessionHighlightSets();
  const targetSession = isTmuxSession(preferredItem) ? preferredItem : fileExplorerExplicitSyncSessionTarget();
  if (!targetSession) return emptyFileExplorerSessionHighlightSets();
  const focusedGitRoot = tmuxGitRootForItem(targetSession);
  const payload = fileExplorerSessionFilesPayload;
  if (
    !payload?.session
    || (targetSession && String(payload.session) !== String(targetSession))
    || !sessionFilesPayloadOverlapsFocusedRoot(payload, focusedGitRoot)
  ) {
    return emptyFileExplorerSessionHighlightSets();
  }
  const repoRoots = new Set(sessionFilesRepoRoots(payload));
  const expandedDirs = new Set(fileExplorerSyncHighlightedRepoRoots(fileExplorerSyncPlan(targetSession), repoRoots));
  return {repoRoots: new Set(), touchedDirs: new Set(), expandedDirs};
}

function fileExplorerSessionHighlightClassForPath(path, kind = 'dir', options = {}) {
  if (options.differMode === true || kind !== 'dir') return '';
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized) return '';
  const sets = options.sessionHighlightSets || fileExplorerSessionHighlightSets();
  if (sets.expandedDirs?.has(normalized)) return 'file-tree-row--sync-expanded';
  if (sets.repoRoots?.has(normalized)) return 'file-tree-row--session-repo';
  if (sets.touchedDirs?.has(normalized)) return 'file-tree-row--session-touched';
  return '';
}

function applyFileExplorerSessionHighlightRow(row, sets = fileExplorerSessionHighlightSets()) {
  if (!row) return;
  const highlightClass = fileExplorerSessionHighlightClassForPath(row.dataset?.path || '', row.dataset?.kind || '', {sessionHighlightSets: sets});
  applySessionHighlightRowClass(row, highlightClass);
}

function updateFileExplorerSessionHighlightRows(preferredItem = null) {
  const sets = fileExplorerSessionHighlightSets(preferredItem);
  for (const row of document.querySelectorAll('.file-tree-row[data-path]:not([data-tabber-type])')) {
    applyFileExplorerSessionHighlightRow(row, sets);
  }
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
  document.querySelectorAll('.file-explorer-head, .file-explorer-toolbar').forEach(node => {
    node.title = error || normalized;
  });
  refreshFileExplorerRepoDisplay(normalized, {error});
}

function displayedFileExplorerRoot() {
  const input = fileExplorerPathInputs()[0];
  const value = input ? ('value' in input ? input.value : input.textContent) : '';
  return normalizeDirectoryPath(value || currentFileExplorerRoot());
}

function restoreCommittedFileExplorerRootDisplay() {
  const root = normalizeDirectoryPath(currentFileExplorerRoot());
  if (!root) return;
  const displayedRoot = displayedFileExplorerRoot();
  const pendingRoot = normalizeDirectoryPath(document.querySelector('.file-explorer-tree-panel .file-tree-status-row')?.dataset?.root || '');
  if (displayedRoot === root && (!pendingRoot || pendingRoot === root)) return;
  setFileExplorerPathDisplay(root);
  renderFileExplorerRootModeControls();
  if (typeof refreshFileExplorerTreesInPlace === 'function') {
    refreshFileExplorerTreesInPlace({root, preserveExpanded: true, preserveScroll: true}).catch(error => {
      console.warn('Finder root display restore failed', error);
    });
  }
}

function repoInfoSummary(repo) {
  if (!repo?.root) return '';
  const dirty = Number.isFinite(Number(repo.dirty_count)) && Number(repo.dirty_count) > 0 ? `, ${Number(repo.dirty_count)} dirty` : '';
  const ahead = Number.isFinite(Number(repo.ahead)) && Number(repo.ahead) > 0 ? `, ${Number(repo.ahead)} ahead` : '';
  const behind = Number.isFinite(Number(repo.behind)) && Number(repo.behind) > 0 ? `, ${Number(repo.behind)} behind` : '';
  const branch = repo.branch || 'detached';
  return `${repo.name || basenameOf(repo.root)} (${branch}${dirty}${ahead}${behind})`;
}

function setFileExplorerRepoSummary(path, repo, error = '') {
  const summary = error ? '' : repoInfoSummary(repo);
  const title = error || (repo?.root ? [`repo: ${repo.root}`, `branch: ${repo.branch || 'unknown'}`, repo.upstream ? `upstream: ${repo.upstream}` : '', Number.isFinite(Number(repo.dirty_count)) ? `dirty files: ${repo.dirty_count}` : '', Number(repo.ahead) ? `ahead: ${repo.ahead}` : '', Number(repo.behind) ? `behind: ${repo.behind}` : ''].filter(Boolean).join('\n') : '');
  for (const node of document.querySelectorAll('.file-explorer-repo-summary')) {
    node.textContent = summary;
    node.hidden = !summary;
    if (title) node.title = title;
    else node.removeAttribute('title');
  }
  for (const input of fileExplorerPathInputs()) {
    if (title) input.title = title;
    else if (!error) input.removeAttribute('title');
  }
}

async function refreshFileExplorerRepoDisplay(path, options = {}) {
  if (options.error) {
    setFileExplorerRepoSummary(path, null, options.error);
    return;
  }
  const normalized = normalizeDirectoryPath(path);
  const cached = fileExplorerRepoInfoCache.get(normalized);
  if (cached) {
    setFileExplorerRepoSummary(normalized, cached);
    return;
  }
  try {
    const info = await fetchFilePathInfo(normalized);
    if (normalizeDirectoryPath(fileExplorerRoot || normalized) !== normalized) return;
    const repo = info.repo || null;
    if (repo) cacheFileExplorerRepoInfo(normalized, repo);
    setFileExplorerRepoSummary(normalized, repo);
  } catch (_) {
    setFileExplorerRepoSummary(normalized, null);
  }
}

// Rich git info shown in a styled hover popover for a repo dir (replaces the native title tooltip).
function repoInfoPopoverHtml(repo) {
  if (!repo?.root) return '';
  const rows = [`<div class="file-tree-repo-popover-title">${esc(repo.name || basenameOf(repo.root))}</div>`];
  rows.push(`<div class="file-tree-repo-popover-branch">⎇ ${esc(repo.branch || 'detached')}</div>`);
  if (repo.upstream) rows.push(`<div class="meta-muted">↗ ${esc(repo.upstream)}</div>`);
  const stat = [];
  if (Number(repo.ahead) > 0) stat.push(`${Number(repo.ahead)} ahead`);
  if (Number(repo.behind) > 0) stat.push(`${Number(repo.behind)} behind`);
  if (Number.isFinite(Number(repo.dirty_count))) stat.push(`${Number(repo.dirty_count)} dirty`);
  if (stat.length) rows.push(`<div class="file-tree-repo-popover-stat">${esc(stat.join(' · '))}</div>`);
  rows.push(`<div class="file-tree-repo-popover-path">${esc(repo.root)}</div>`);
  return rows.join('');
}

function fileTreeRepoPopoverNode() {
  let node = document.getElementById('fileTreeRepoPopover');
  if (!node) {
    node = document.createElement('div');
    node.id = 'fileTreeRepoPopover';
    node.className = 'file-tree-repo-popover';
    node.hidden = true;
    appOverlayRootElement().appendChild(node);
  }
  return node;
}

let fileTreeRepoPopoverTimer = null;
let fileTreeRepoPopoverHoverToken = 0;
let fileTreeRepoPopoverCursor = {x: 0, y: 0};   // last pointer pos over the hovered repo row; popover anchors to its RIGHT

function cancelFileTreeRepoPopoverTimer() {
  if (fileTreeRepoPopoverTimer) {
    clearTimeout(fileTreeRepoPopoverTimer);
    fileTreeRepoPopoverTimer = null;
  }
  fileTreeRepoPopoverHoverToken += 1;
}

function showFileTreeRepoPopover(row, repo) {
  if (!repo?.root) return;
  const node = fileTreeRepoPopoverNode();
  node.innerHTML = repoInfoPopoverHtml(repo);
  node.hidden = false;
  // Anchor to the RIGHT of the cursor (like a tooltip following the pointer), not centered under the row.
  // Clamp to the viewport so it stays fully on-screen near the right/bottom edge.
  const popRect = node.getBoundingClientRect?.();
  const pos = clampToViewport(
    Math.round(fileTreeRepoPopoverCursor.x + 14),
    Math.round(fileTreeRepoPopoverCursor.y + 4),
    Math.ceil(popRect?.width || 0),
    Math.ceil(popRect?.height || 0),
  );
  node.style.left = `${Math.round(pos.left)}px`;
  node.style.top = `${Math.round(pos.top)}px`;
}

function hideFileTreeRepoPopover() {
  cancelFileTreeRepoPopoverTimer();
  fileTreeRepoPopoverPath = null;
  const node = document.getElementById('fileTreeRepoPopover');
  if (node) node.hidden = true;
}

function scheduleRepoRowHoverPopover(row, path) {
  cancelFileTreeRepoPopoverTimer();
  const token = fileTreeRepoPopoverHoverToken;
  const delay = Math.max(0, Number(popoverShowDelayMs) || 0);
  fileTreeRepoPopoverTimer = setTimeout(() => {
    fileTreeRepoPopoverTimer = null;
    if (token !== fileTreeRepoPopoverHoverToken || !row?.isConnected) return;
    showRepoRowHoverPopover(row, path);
  }, delay);
}

async function showRepoRowHoverPopover(row, path) {
  const normalized = normalizeDirectoryPath(path);
  fileTreeRepoPopoverPath = normalized;
  // Show immediately from cache (branch/ahead/behind), then lazily fetch full status (incl dirty).
  showFileTreeRepoPopover(row, fileExplorerRepoInfoCache.get(normalized));
  const cached = fileExplorerRepoInfoCache.get(normalized);
  if (cached && Number.isFinite(Number(cached.dirty_count))) return;
  if (row.dataset.repoTitleLoaded === 'true') return;
  row.dataset.repoTitleLoaded = 'true';
  try {
    const info = await fetchFilePathInfo(normalized);
    if (info.repo) {
      cacheFileExplorerRepoInfo(normalized, info.repo);
      updateFileTreeGitStatusRows();
      if (fileTreeRepoPopoverPath === normalized) showFileTreeRepoPopover(row, info.repo);
    }
  } catch (_) {}
}

async function commitFileExplorerPathInput(input) {
  const raw = 'value' in input ? input.value : input.textContent;
  const target = expandUserPath(raw);
  if (!target) return false;
  const opened = await openFileExplorerManualRoot(target);
  if (!opened) {
    const error = currentFileExplorerListError(target);
    if (error) setFileExplorerPathError(input, error);
  }
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
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const candidates = [];
  // Finder follows the last explicit click/type target. Visual focus may move on hover when auto-focus
  // is enabled, but passive focus must not move the Finder path, root, current-directory mark, or diff
  // target.
  for (const item of [preferredItem, explicitSession]) {
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

function explicitFinderTargetPath(preferredItem = null) {
  const path = finderTargetPathForItem(preferredItem);
  return path ? normalizeDirectoryPath(path) : '';
}

function fileExplorerPaneIsOpen() {
  return itemInLayout(fileExplorerItemId);
}

function scheduleFileExplorerActiveTabSync(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen()) return;
  if (fileExplorerRootMode !== 'sync') return;
  if (shareReadOnlyFinderStateIsHostOwned()) return;
  const explicit = options.explicit === true;
  if (fileExplorerManualSelectionActive && !explicit) return;
  if (explicit) fileExplorerManualSelectionActive = false;
  if (options.explicit === true && isTmuxSession(preferredItem)) rememberFileExplorerExplicitSyncSession(preferredItem);
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const fileSyncPath = explicit && isFileEditorItem(preferredItem) ? fileItemPath(preferredItem) : '';
  if (fileSyncPath) forgetFileExplorerSyncManualCollapse(fileSyncPath);
  const syncItem = fileSyncPath ? preferredItem : (isTmuxSession(preferredItem) ? preferredItem : explicitSession);
  if (!syncItem || (!fileSyncPath && syncItem !== explicitSession)) return;
  const syncPlan = fileSyncPath ? fileExplorerSyncPlanForFile(fileSyncPath) : fileExplorerSyncPlan(syncItem);
  const expandPaths = fileExplorerSyncExpansionPaths(syncPlan);
  const syncSignature = fileExplorerSyncPlanSignature(syncPlan);
  const staleInFlightSync = Boolean(fileExplorerSyncPathInFlight && fileExplorerSyncPathInFlight !== syncSignature);
  if (explicit && staleInFlightSync) cancelPendingFileExplorerActiveSync();
  if (
    syncPlan.root
    && (syncPlan.root !== currentFileExplorerRoot() || expandPaths.length || (explicit && staleInFlightSync))
    && fileExplorerSyncPathInFlight !== syncSignature
    && (explicit || !fileExplorerSyncPlanAlreadyApplied(syncPlan))
  ) {
    const interactionGeneration = fileExplorerInteractionGeneration;
    requestAnimationFrame(() => {
      if (interactionGeneration !== fileExplorerInteractionGeneration) return;
      const syncPromise = fileSyncPath
        ? syncFileExplorerRootToActiveFile(fileSyncPath, {force: explicit})
        : syncFileExplorerRootToActiveTmux(syncItem, {force: explicit});
      syncPromise.catch(error => {
        console.warn('Finder root sync failed', error);
      });
    });
    return;
  }
}

function fileExplorerSyncPlanTargetStillCurrent(plan, options = {}) {
  if (!plan?.root || fileExplorerRootMode !== 'sync') return false;
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  if (options.guardExplicitTarget === true && isTmuxSession(plan.session)) {
    return fileExplorerExplicitSyncSessionTarget() === String(plan.session);
  }
  return true;
}

function cancelPendingFileExplorerActiveSync(options = {}) {
  fileExplorerInteractionGeneration += 1;
  if (options.invalidateOpen !== false) fileExplorerOpenGeneration += 1;
  fileExplorerSyncGeneration += 1;
  fileExplorerSyncPathInFlight = '';
  resetFileExplorerAppliedSyncPlan();
}

async function syncFileExplorerRootToActiveTmux(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  return syncFileExplorerRootToPlan(fileExplorerSyncPlan(preferredItem), preferredItem, {
    ...options,
    guardExplicitTarget: options.force === true,
  });
}

async function syncFileExplorerRootToActiveFile(path, options = {}) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  forgetFileExplorerSyncManualCollapse(path);
  return syncFileExplorerRootToPlan(fileExplorerSyncPlanForFile(path), fileEditorItemFor(path), options);
}

async function syncFileExplorerRootToPlan(plan, preferredItem = null, options = {}) {
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  const signature = fileExplorerSyncPlanSignature(plan);
  const expandPaths = fileExplorerSyncExpansionPaths(plan);
  if (!plan.root || fileExplorerSyncPathInFlight === signature) return false;
  if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
  if (options.force !== true && fileExplorerSyncPlanAlreadyApplied(plan)) {
    setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    updateFileExplorerSessionHighlightRows(preferredItem);
    return false;
  }
  fileExplorerSyncPathInFlight = signature;
  try {
    let changed = false;
    const previousTargetKey = fileExplorerSyncTargetKey(fileExplorerVisibleSyncSession, fileExplorerVisibleSyncRoot);
    const nextTargetKey = fileExplorerSyncTargetKey(plan.session, plan.root);
    if (nextTargetKey && nextTargetKey !== previousTargetKey) {
      rememberFileExplorerSyncExpandedState();
    }
    if (plan.root !== currentFileExplorerRoot()) {
      changed = await openFileExplorerAt(plan.root, {
        preserveExpanded: false,
        preserveScroll: false,
        syncSelection: true,
        user: options.force === true,
        showPending: options.force === true,
      });
      if (!changed) return false;
      if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
    }
    setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    const rememberedExpandedPaths = rememberedFileExplorerSyncExpandedPaths(plan.session, plan.root);
    if (rememberedExpandedPaths.length) {
      changed = await restoreFileExplorerExpandedPaths(rememberedExpandedPaths, plan.root) || changed;
      if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
    }
    if (expandPaths.length) {
      const generation = ++fileExplorerSyncGeneration;
      for (const path of expandPaths) {
        if (generation !== fileExplorerSyncGeneration) return changed;
        changed = await expandFileExplorerTreesToPath(path, plan.root, generation, {scrollIntoView: false, auto: true}) || changed;
        if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
      }
    }
    if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
    rememberFileExplorerSyncExpandedState(plan.session, plan.root);
    markFileExplorerSyncPlanApplied(plan);
    updateFileExplorerSessionHighlightRows(preferredItem);
    return changed;
  } finally {
    if (fileExplorerSyncPathInFlight === signature) fileExplorerSyncPathInFlight = '';
  }
}

async function syncFileExplorerToActiveTab(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen()) return false;
  if (fileExplorerRootMode !== 'sync' && options.explicit !== true) return false;
  if (shareReadOnlyFinderStateIsHostOwned()) return false;
  const path = options.explicit === true ? explicitFinderTargetPath(preferredItem) : activeFinderTargetPath(preferredItem);
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

function renderFileExplorerTreeStatus(message, options = {}) {
  const root = normalizeDirectoryPath(options.root || '');
  const status = options.status || (options.error === true ? 'error' : 'message');
  for (const container of fileExplorerTreeContainers()) {
    container.style.setProperty('--tree-depth', '0');
    const row = document.createElement('div');
    row.className = ['file-tree-row', 'file-tree-status-row', `file-tree-status-${status}`, options.error === true ? 'file-tree-status-error' : ''].filter(Boolean).join(' ');
    row.dataset.status = status;
    if (root) row.dataset.root = root;
    row.draggable = false;
    row.style.paddingLeft = fileTreeRowPadding(0);
    clearFileTreeRowHandlers(row);
    setTreeItemAria(row, {selected: false, expandable: false});
    updateFileTreeRowContents(row, options.iconText || '', '', {nameHtml: options.nameHtml || esc(message)});
    container.replaceChildren(row);
    container.scrollTop = 0;
  }
}

function renderFileExplorerTreeSearching(root) {
  renderFileExplorerTreeStatus('searching...', {
    root,
    status: 'searching',
    nameHtml: textWithMovingEllipsisHtml('searching...', 'file-tree-searching-dots'),
  });
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

function markFileExplorerInteraction() {
  fileExplorerLastInteractionAt = Date.now();
}

function eventTargetIsFileExplorerSurface(target) {
  return Boolean(target?.closest?.('#fileExplorer, .panel.file-explorer-panel, .file-explorer-tree-panel'));
}

function fileExplorerUserIsActive() {
  if (isFileExplorerItem(focusedPanelItem)) return true;
  if (Date.now() - fileExplorerLastInteractionAt < fileExplorerRefreshIdleMs) return true;
  if (fileExplorer?.matches?.(':hover')) return true;
  return Array.from(document.querySelectorAll('.panel.file-explorer-panel, .file-explorer-tree-panel')).some(node => node.matches?.(':hover'));
}

function deferFileExplorerRefresh() {
  if (fileExplorerRefreshDeferred) return;
  fileExplorerRefreshDeferred = true;
  setTimeout(() => {
    fileExplorerRefreshDeferred = false;
    refreshFileExplorerIfChanged().catch(error => console.warn('deferred file explorer refresh failed', error));
  }, fileExplorerRefreshIdleMs);
}

document.addEventListener('pointerdown', event => {
  if (eventTargetIsFileExplorerSurface(event.target)) markFileExplorerInteraction();
}, true);
document.addEventListener('scroll', event => {
  if (eventTargetIsFileExplorerSurface(event.target)) markFileExplorerInteraction();
}, true);

function directFileTreeRow(container, fullPath) {
  return Array.from(container?.children || []).find(node => (
    node.classList?.contains('file-tree-row') && node.dataset?.path === fullPath
  )) || null;
}

function childContainerForRow(row, fullPath) {
  const next = row?.nextElementSibling;
  return next?.classList?.contains('file-tree-children') && next.dataset?.parent === fullPath ? next : null;
}

function createFileTreeChildContainer(fullPath) {
  const children = document.createElement('div');
  children.className = 'file-tree-children';
  children.dataset.parent = fullPath;
  return children;
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

async function ensureDirectoryRowExpanded(row, fullPath, options = {}) {
  if (!row || row.dataset?.kind !== 'dir') return null;
  const existing = childContainerForRow(row, fullPath);
  if (existing) return existing;
  await expandDirectoryRow(row, fullPath, options);
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
      // An automatic reveal (following the active tab/file) must not resurrect an ancestor directory
      // the user manually collapsed in sync mode -- the active path is often inside a repo the user
      // just collapsed, and the deferred reveal would re-expand it, fighting the user. Stop at the
      // collapsed ancestor. Route through the same fileExplorerSyncPathSuppressed predicate the sync
      // expand-loop and remembered-state restore use, so all three honor one source of truth.
      // Explicit reveals (auto !== true, e.g. the user clicked the file) still expand through.
      if (options.auto === true && fullPath !== path && fileExplorerRootMode === 'sync' && fileExplorerSyncPathSuppressed(fullPath)) {
        return false;
      }
      const childScope = await ensureDirectoryRowExpanded(row, fullPath, {auto: options.auto === true});
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
    .filter(path => fileExplorerRootMode !== 'sync' || !fileExplorerSyncPathSuppressed(path))
    .sort((left, right) => childPathParts(root, left).length - childPathParts(root, right).length);
  const generation = ++fileExplorerSyncGeneration;
  for (const path of expandedPaths) {
    if (generation !== fileExplorerSyncGeneration) return false;
    await expandFileExplorerTreesToPath(path, root, generation, {scrollIntoView: false});
  }
  return true;
}

function fileTreeDirectRows(container) {
  return Array.from(container?.children || []).filter(node => node.classList?.contains('file-tree-row'));
}

function fileTreeChangedFile(path) {
  const files = Array.isArray(fileExplorerSessionFilesPayload?.files) ? fileExplorerSessionFilesPayload.files : [];
  return files.find(item => item?.abs_path === path) || null;
}

function sessionFileAgentKinds(item) {
  const raw = Array.isArray(item?.agents) ? item.agents : (item?.agent ? [item.agent] : []);
  const order = {claude: 0, codex: 1};
  const seen = new Set();
  return raw
    .map(value => String(value || '').toLowerCase())
    .filter(value => value && !seen.has(value) && seen.add(value))
    .sort((a, b) => (order[a] ?? 2) - (order[b] ?? 2) || a.localeCompare(b));
}

function fileTreeChangedAncestorStats(payload = fileExplorerSessionFilesPayload) {
  const stats = new Map();
  const seen = new Set();
  for (const file of Array.isArray(payload?.files) ? payload.files : []) {
    if (!sessionFileIsDifferVisible(file)) continue;
    const absPath = sessionFileAbsolutePath(file);
    if (!absPath) continue;
    const agents = sessionFileAgentKinds(file);
    let dir = normalizeDirectoryPath(dirnameOf(absPath));
    while (dir && dir !== '/') {
      const key = `${dir}\x1f${absPath}`;
      const current = stats.get(dir) || {count: 0, agents: [], mtime: 0, added: 0, removed: 0};
      if (!seen.has(key)) {
        seen.add(key);
        current.count += 1;
        const added = Number(file.added);
        const removed = Number(file.removed);
        if (Number.isFinite(added)) current.added += added;
        if (Number.isFinite(removed)) current.removed += removed;
      }
      const mtime = Number(file.mtime || 0);
      if (Number.isFinite(mtime) && mtime > Number(current.mtime || 0)) current.mtime = mtime;
      for (const agent of agents) {
        if (!current.agents.includes(agent)) current.agents.push(agent);
      }
      stats.set(dir, current);
      const parent = normalizeDirectoryPath(dirnameOf(dir));
      if (!parent || parent === dir) break;
      dir = parent;
    }
  }
  return stats;
}

function normalizeGitStatus(status) {
  return String(status || '').trim().toUpperCase();
}

function fileTreeGitStatus(path) {
  return normalizeGitStatus(fileTreeChangedFile(path)?.status);
}

function fileTreeRepoBranch(path) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path);
  const repo = fileExplorerRepoInfoCache.get(normalized);
  if (!repo?.root || normalizeDirectoryPath(repo.root) !== normalized) return '';
  return repo.branch || 'detached';
}

// Compact ahead/behind/dirty markers for a repo dir's inline annotation, read from cached repo info.
function fileTreeRepoSyncMeta(path) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path);
  const repo = fileExplorerRepoInfoCache.get(normalized);
  if (!repo?.root || normalizeDirectoryPath(repo.root) !== normalized) return [];
  const parts = [];
  const ahead = Number(repo.ahead);
  const behind = Number(repo.behind);
  const dirty = Number(repo.dirty_count);
  if (Number.isFinite(ahead) && ahead > 0) parts.push({cls: 'file-tree-repo-ahead', text: `↑${ahead}`});
  if (Number.isFinite(behind) && behind > 0) parts.push({cls: 'file-tree-repo-behind', text: `↓${behind}`});
  if (Number.isFinite(dirty) && dirty > 0) parts.push({cls: 'file-tree-repo-dirty', text: `●${dirty}`});
  return parts;
}

function fileTreeRepoDiffParts(path) {
  const normalized = normalizeDirectoryPath(path);
  const repos = Array.isArray(fileExplorerSessionFilesPayload?.repos) ? fileExplorerSessionFilesPayload.repos : [];
  const repo = repos.find(item => normalizeDirectoryPath(item?.repo || '') === normalized);
  let added = Number(repo?.added);
  let removed = Number(repo?.removed);
  if (!Number.isFinite(added) || !Number.isFinite(removed)) {
    const files = Array.isArray(fileExplorerSessionFilesPayload?.files) ? fileExplorerSessionFilesPayload.files : [];
    const repoFiles = files.filter(item => normalizeDirectoryPath(item?.repo || '') === normalized);
    added = repoFiles.reduce((sum, item) => sum + (Number.isFinite(Number(item.added)) ? Number(item.added) : 0), 0);
    removed = repoFiles.reduce((sum, item) => sum + (Number.isFinite(Number(item.removed)) ? Number(item.removed) : 0), 0);
  }
  if (!Number.isFinite(added) || !Number.isFinite(removed)) return [];
  return sessionFileDiffText({added, removed});
}

function fileTreeRepoBranchIsNonMain(path) {
  const branch = fileTreeRepoBranch(path);
  return Boolean(branch && !['main', 'master'].includes(branch));
}

function fileTreeDisplayParts(path, entry) {
  // a symlink shows where it points inline — "name → target" (the raw link text, rel or abs).
  const linkTarget = entry.is_symlink === true && entry.symlink_target ? String(entry.symlink_target) : '';
  const linkSuffixText = linkTarget ? ` → ${linkTarget}` : '';
  const linkSuffixHtml = linkTarget ? ` <span class="file-tree-symlink-target">→ ${esc(linkTarget)}</span>` : '';
  if (entry.kind === 'dir' && entry.is_repo === true) {
    const branch = fileTreeRepoBranch(path);
    const sync = fileTreeRepoSyncMeta(path);
    const textParts = [branch, ...sync.map(part => part.text)].filter(Boolean);
    const htmlParts = [];
    if (branch) htmlParts.push(`<span class="file-tree-repo-branch">${esc(branch)}</span>`);
    for (const part of sync) htmlParts.push(`<span class="${part.cls}">${esc(part.text)}</span>`);
    return {
      text: (textParts.length ? `${entry.name} [${textParts.join(' ')}]` : entry.name) + linkSuffixText,
      html: (htmlParts.length
        ? `${esc(entry.name)} <span class="file-tree-repo-meta">[${htmlParts.join(' ')}]</span>`
        : esc(entry.name)) + linkSuffixHtml,
    };
  }
  const baseText = entry.name;
  if (linkTarget) return {text: baseText + linkSuffixText, html: esc(baseText) + linkSuffixHtml};
  return {text: baseText, html: ''};
}

function fileTreeMtimeText(entry) {
  return sessionFileDisplayTimeTextForEntry(entry);
}

const FILE_TREE_RECENCY_THRESHOLDS = Object.freeze([
  {key: 'just-updated', maxAgeSeconds: FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS, colorVar: 'var(--file-tree-recency-hot)', pulseEligible: true},
  {key: 'hot', maxAgeSeconds: 60, colorVar: 'var(--file-tree-recency-hot)'},
  {key: 'fresh', maxAgeSeconds: 5 * 60, colorVar: 'var(--file-tree-recency-fresh)'},
  {key: 'recent', maxAgeSeconds: 60 * 60, colorVar: 'var(--file-tree-recency-recent)'},
  {key: 'warm', maxAgeSeconds: 24 * 60 * 60, colorVar: 'var(--file-tree-recency-warm)'},
]);
const FILE_TREE_RECENCY_OLD_STATE = Object.freeze({
  key: 'old',
  className: 'file-tree-recency-old',
  colorVar: 'var(--file-tree-recency-old)',
  pulseEligible: false,
});
const FILE_TREE_RECENCY_CLASSES = Object.freeze([
  ...FILE_TREE_RECENCY_THRESHOLDS.map(item => `file-tree-recency-${item.key}`),
  FILE_TREE_RECENCY_OLD_STATE.className,
]);

function fileTreeRecencyNowMs() {
  const value = Number(globalThis.__yolomuxFileTreeRecencyNowMs);
  return Number.isFinite(value) ? value : Date.now();
}

function fileTreeRecencyStateForMtime(mtime, nowMs = fileTreeRecencyNowMs()) {
  const value = Number(mtime || 0);
  const now = Number(nowMs);
  if (!value || !Number.isFinite(value) || !Number.isFinite(now)) return null;
  const ageSeconds = Math.max(0, (now / 1000) - value);
  const threshold = FILE_TREE_RECENCY_THRESHOLDS.find(item => ageSeconds <= item.maxAgeSeconds);
  const key = threshold?.key || FILE_TREE_RECENCY_OLD_STATE.key;
  return {
    key,
    className: `file-tree-recency-${key}`,
    colorVar: threshold?.colorVar || FILE_TREE_RECENCY_OLD_STATE.colorVar,
    mtimeKey: String(value),
    ageSeconds,
    pulseEligible: threshold?.pulseEligible === true,
  };
}

function fileTreeRecencyDateCell(row) {
  return row?.querySelector?.(':scope > .file-tree-date') || null;
}

function clearFileTreeRecencyAttentionTimer(row) {
  if (row?.__fileTreeRecencyAttentionTimer) clearTimeout(row.__fileTreeRecencyAttentionTimer);
  if (row) {
    row.__fileTreeRecencyAttentionTimer = null;
    row.__fileTreeRecencyAttentionTimerUntilMs = 0;
  }
}

function setFileTreeRecencyAttentionClass(row, enabled, nowMs = fileTreeRecencyNowMs()) {
  const date = fileTreeRecencyDateCell(row);
  if (!row?.classList || !date?.classList) return;
  if (!enabled) {
    date.classList.remove('attention-pulse');
    date.classList.remove('heartbeat-pulse');
    date.style?.removeProperty('--attention-animation-delay');
    return;
  }
  date.classList.add('attention-pulse');
  date.classList.add('heartbeat-pulse');
  date.style?.setProperty('--attention-animation-delay', attentionAnimationDelay(nowMs));
}

function scheduleFileTreeRecencyAttentionStop(row, untilMs) {
  if (!row) return;
  const until = Number(untilMs) || 0;
  const delay = until - fileTreeRecencyNowMs();
  if (delay <= 0) {
    setFileTreeRecencyAttentionClass(row, false);
    clearFileTreeRecencyAttentionTimer(row);
    return;
  }
  if (row.__fileTreeRecencyAttentionTimerUntilMs === until) return;
  clearFileTreeRecencyAttentionTimer(row);
  row.__fileTreeRecencyAttentionTimerUntilMs = until;
  row.__fileTreeRecencyAttentionTimer = setTimeout(() => {
    if ((Number(row.__fileTreeRecencyAttentionUntilMs) || 0) <= fileTreeRecencyNowMs()) {
      setFileTreeRecencyAttentionClass(row, false);
      clearFileTreeRecencyAttentionTimer(row);
    }
  }, delay);
}

function clearFileTreeRowRecency(row) {
  if (!row) return;
  for (const className of FILE_TREE_RECENCY_CLASSES) row.classList.remove(className);
  const date = fileTreeRecencyDateCell(row);
  date?.classList?.remove?.('attention-pulse');
  date?.classList?.remove?.('heartbeat-pulse');
  date?.style?.removeProperty?.('--attention-animation-delay');
  setRowDataset(row, 'recency', '');
  row.style?.removeProperty('--file-tree-recency-date-color');
}

function applyFileTreeRowRecency(row, entry, options = {}) {
  if (!row || fileExplorerTreeDateMode === 'none') {
    clearFileTreeRowRecency(row);
    return;
  }
  const nowMs = fileTreeRecencyNowMs();
  const state = fileTreeRecencyStateForMtime(entry?.mtime, nowMs);
  if (!state) {
    clearFileTreeRowRecency(row);
    clearFileTreeRecencyAttentionTimer(row);
    row.__fileTreeRecencyAttentionMtimeKey = '';
    row.__fileTreeRecencyAttentionUntilMs = 0;
    return;
  }
  for (const className of FILE_TREE_RECENCY_CLASSES) row.classList.toggle(className, className === state.className);
  setRowDataset(row, 'recency', state.key);
  row.style?.setProperty('--file-tree-recency-date-color', state.colorVar);
  if (!state.pulseEligible) {
    setFileTreeRecencyAttentionClass(row, false);
    clearFileTreeRecencyAttentionTimer(row);
    row.__fileTreeRecencyAttentionUntilMs = 0;
    return;
  }
  row.__fileTreeRecencyAttentionMtimeKey = state.mtimeKey;
  row.__fileTreeRecencyAttentionUntilMs = (Number(state.mtimeKey) * 1000) + (FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS * 1000);
  const attentionUntilMs = Number(row.__fileTreeRecencyAttentionUntilMs) || 0;
  const rowSuppressesAttention = row.classList?.contains('selected') || row.classList?.contains('current-file');
  const attentionActive = !rowSuppressesAttention && attentionUntilMs > nowMs && row.__fileTreeRecencyAttentionMtimeKey === state.mtimeKey;
  setFileTreeRecencyAttentionClass(row, attentionActive, nowMs);
  if (attentionActive) scheduleFileTreeRecencyAttentionStop(row, attentionUntilMs);
  else clearFileTreeRecencyAttentionTimer(row);
}

function sortedFileTreeEntries(entries, sortMode = fileExplorerTreeSortMode, options = {}) {
  const includeHidden = options.includeHidden === true;
  const visible = entries.filter(entry => includeHidden || fileExplorerShowHidden || !entry.name.startsWith('.'));
  if (options.tabberWindowOrder === true) {
    return visible.sort((left, right) => {
      const leftIndex = Number(left?.tabber?.windowIndex);
      const rightIndex = Number(right?.tabber?.windowIndex);
      if (Number.isFinite(leftIndex) && Number.isFinite(rightIndex) && leftIndex !== rightIndex) return leftIndex - rightIndex;
      return String(left?.sortName || left?.name || '').localeCompare(String(right?.sortName || right?.name || ''), undefined, {numeric: true, sensitivity: 'base'});
    });
  }
  const mode = ['az', 'za', 'newest', 'oldest'].includes(sortMode) ? sortMode : 'az';
  const direction = mode === 'za' ? -1 : 1;
  return visible.sort((left, right) => {
    const leftKind = left.kind === 'dir' ? 0 : 1;
    const rightKind = right.kind === 'dir' ? 0 : 1;
    if (leftKind !== rightKind) return leftKind - rightKind;
    if (mode === 'newest' || mode === 'oldest') {
      const mtimeResult = Number(right.mtime || 0) - Number(left.mtime || 0);
      if (mtimeResult !== 0) return mode === 'newest' ? mtimeResult : -mtimeResult;
    }
    // sortName lets a caller (the Tabber) sort A-Z/Z-A by the human label while the row's name stays a
    // stable synthetic path key; Finder/Differ entries have no sortName and fall back to name unchanged.
    const leftKey = left.sortName != null ? String(left.sortName) : String(left.name || '');
    const rightKey = right.sortName != null ? String(right.sortName) : String(right.name || '');
    return leftKey.localeCompare(rightKey, undefined, {numeric: true, sensitivity: 'base'}) * direction;
  });
}

// one source for the git-status row classes (the toggle loop hardcoded this 5-element list in
// two places — updateFileTreeRow + updateFileTreeGitStatusRows — so a status that maps elsewhere or a row
// that changes status could leave a stale class behind on one path but not the other). applyGitStatusRowClass
// toggles exactly this set so the stale class is always cleared.
const GIT_STATUS_ROW_CLASSES = Object.freeze(['git-modified', 'git-untracked', 'git-deleted', 'git-staged', 'git-transcript']);
// The session-highlight row classes (sync-expanded / session-repo / session-touched), likewise toggled in
// two places (applyFileExplorerSessionHighlightRow + updateFileTreeRow).
const SESSION_HIGHLIGHT_ROW_CLASSES = Object.freeze(['file-tree-row--sync-expanded', 'file-tree-row--session-repo', 'file-tree-row--session-touched']);

function applyGitStatusRowClass(row, gitClass) {
  for (const className of GIT_STATUS_ROW_CLASSES) {
    row.classList.toggle(className, className === gitClass);
  }
}

function applySessionHighlightRowClass(row, highlightClass) {
  for (const className of SESSION_HIGHLIGHT_ROW_CLASSES) {
    row.classList.toggle(className, className === highlightClass);
  }
}

function gitStatusRowClass(status) {
  const key = normalizeGitStatus(status);
  if (key === 'A' || key === 'U' || key === '?') return 'git-untracked';
  if (key === 'D') return 'git-deleted';
  if (key === 'S') return 'git-staged';
  if (key === 'M') return 'git-modified';
  if (key === 'T') return 'git-transcript';
  const fallback = key.toLowerCase();
  return /^[a-z0-9_-]+$/.test(fallback) ? `git-${fallback}` : '';
}

function fileTreeGitStatusClass(status) {
  return gitStatusRowClass(status);
}

function fileTreeGitStatusBadgeClass(status) {
  return normalizeGitStatus(status) === '?' ? 'file-tree-git-status-unknown' : '';
}

function gitStatusBadgeTitle(status) {
  const key = normalizeGitStatus(status);
  const labels = {
    M: 'modified',
    A: 'added',
    D: 'deleted',
    T: 'touched by AI transcript',
    '?': 'untracked',
    U: 'untracked',
    S: 'staged',
    R: 'renamed',
    C: 'copied',
  };
  return labels[key] ? `${key}: ${labels[key]}` : '';
}

function fileIconClassFor(name, kind = 'file') {
  if (kind === 'dir') return 'file-icon-dir';
  const lowerName = String(name || '').toLowerCase();
  const ext = fileExtensionOf(lowerName);
  if (previewMediaKindForPath(name) === 'image') return 'file-icon-image';
  if (['.md', '.markdown', '.txt', '.rst', 'readme', 'license'].includes(ext || lowerName)) return 'file-icon-doc';
  if (['.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env', 'dockerfile', 'makefile'].includes(ext || lowerName)) return 'file-icon-config';
  if (['.py', '.js', '.ts', '.tsx', '.jsx', '.rs', '.go', '.c', '.h', '.cpp', '.hpp', '.rb', '.lua', '.sql', '.sh', '.bash', '.zsh'].includes(ext)) return 'file-icon-code';
  if (['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz'].includes(ext)) return 'file-icon-archive';
  return 'file-icon-generic';
}

function reconcileChildNodes(parent, nextNodes, options = {}) {
  if (!parent) return;
  const lockedNodes = new Set(options.lockedNodes || []);
  const arranged = nextNodes.slice();
  if (lockedNodes.size) {
    Array.from(parent.children || []).forEach((child, index) => {
      if (!lockedNodes.has(child)) return;
      const desiredIndex = arranged.indexOf(child);
      if (desiredIndex < 0 || desiredIndex === index || index >= arranged.length) return;
      arranged.splice(desiredIndex, 1);
      arranged.splice(index, 0, child);
    });
  }
  arranged.forEach((node, index) => {
    if (parent.children[index] !== node) parent.insertBefore(node, parent.children[index] || null);
  });
  while (parent.children.length > arranged.length) parent.lastElementChild?.remove();
}

function setClassNameIfChanged(node, className) {
  if (node && node.className !== className) node.className = className;
}

function setHiddenIfChanged(node, hidden) {
  if (node && node.hidden !== hidden) node.hidden = hidden;
}

function syncFileTreeRowKindClass(row, kind) {
  row.classList.add('file-tree-row');
  const nextKindClass = `kind-${kind}`;
  if (row.dataset.kind === kind && row.classList.contains(nextKindClass)) return;
  for (const className of Array.from(row.classList || [])) {
    if (className.startsWith('kind-')) row.classList.remove(className);
  }
  row.classList.add(nextKindClass);
}

function updateFileTreeRowContents(row, iconText, nameText, options = {}) {
  let icon = row.querySelector(':scope > .file-tree-icon');
  if (!icon) {
    icon = document.createElement('span');
    icon.className = 'file-tree-icon';
    row.appendChild(icon);
  }
  let name = row.querySelector(':scope > .file-tree-name');
  if (!name) {
    name = document.createElement('span');
    name.className = 'file-tree-name';
    row.appendChild(name);
  }
  let dirCount = row.querySelector(':scope > .file-tree-dir-count');
  if (!dirCount) {
    dirCount = document.createElement('span');
    dirCount.className = 'file-tree-dir-count';
    row.appendChild(dirCount);
  }
  let agent = row.querySelector(':scope > .file-tree-agent');
  if (!agent) {
    agent = document.createElement('span');
    agent.className = 'file-tree-agent';
    row.appendChild(agent);
  }
  let diff = row.querySelector(':scope > .file-tree-diff');
  if (!diff) {
    diff = document.createElement('span');
    diff.className = 'file-tree-diff';
    row.appendChild(diff);
  }
  let status = row.querySelector(':scope > .file-tree-git-status');
  if (!status) {
    status = document.createElement('span');
    status.className = 'file-tree-git-status';
    row.appendChild(status);
  }
  let date = row.querySelector(':scope > .file-tree-date');
  if (!date) {
    date = document.createElement('span');
    date.className = 'file-tree-date';
    row.appendChild(date);
  }
  // Keep DOM order: icon → name → agent → diff → dir-count → status → date.
  if (name.nextElementSibling !== agent) row.insertBefore(agent, name.nextElementSibling);
  if (agent.nextElementSibling !== diff) row.insertBefore(diff, agent.nextElementSibling);
  if (diff.nextElementSibling !== dirCount) row.insertBefore(dirCount, diff.nextElementSibling);
  if (dirCount.nextElementSibling !== status) row.insertBefore(status, dirCount.nextElementSibling);
  if (status.nextElementSibling !== date) row.insertBefore(date, status.nextElementSibling);
  setClassNameIfChanged(icon, ['file-tree-icon', options.iconClass || ''].filter(Boolean).join(' '));
  if (icon.textContent !== iconText) icon.textContent = iconText;
  if (options.nameHtml) {
    if (name.innerHTML !== options.nameHtml) name.innerHTML = options.nameHtml;
    if (!name.children?.length && name.textContent !== nameText) name.textContent = nameText;
  } else if (name.textContent !== nameText || name.innerHTML) {
    name.innerHTML = '';
    name.textContent = nameText;
  }
  const agentHtml = options.agentHtml || '';
  if (agent.innerHTML !== agentHtml) agent.innerHTML = agentHtml;
  setHiddenIfChanged(agent, !agentHtml);
  row.classList.toggle('has-agent', Boolean(agentHtml));
  if (options.preserveDirCount !== true) {
    const dirCountText = options.dirCountText || '';
    if (dirCount.textContent !== dirCountText) dirCount.textContent = dirCountText;
    setHiddenIfChanged(dirCount, !dirCountText);
  }
  if (options.preserveDiff !== true) {
    const diffParts = options.diffParts || [];
    const diffHtml = diffParts.map(p => `<span class="changes-diff-${esc(p.kind)}">${esc(p.text)}</span>`).join(' ');
    if (diff.innerHTML !== diffHtml) diff.innerHTML = diffHtml;
    setHiddenIfChanged(diff, !diffParts.length);
  }
  const statusText = options.gitStatus || '';
  setClassNameIfChanged(status, ['file-tree-git-status', fileTreeGitStatusBadgeClass(statusText)].filter(Boolean).join(' '));
  if (status.textContent !== statusText) status.textContent = statusText;
  const statusTitle = options.gitStatusTitle || '';
  if (statusTitle) {
    status.setAttribute('title', statusTitle);
    status.setAttribute('aria-label', statusTitle);
  } else {
    status.removeAttribute('title');
    status.removeAttribute('aria-label');
  }
  setHiddenIfChanged(status, !statusText);
  if (options.preserveDate !== true) {
    const dateHtml = options.dateHtml || '';
    if (dateHtml) {
      if (date.innerHTML !== dateHtml) date.innerHTML = dateHtml;
      setHiddenIfChanged(date, false);
    } else {
      const dateText = options.dateText || '';
      if (date.innerHTML || date.textContent !== dateText) {
        date.innerHTML = '';
        date.textContent = dateText;
      }
      setHiddenIfChanged(date, !dateText);
    }
  }
}

function fileTreeDirCountText(count) {
  const normalized = Number(count || 0);
  if (!normalized) return '';
  return String(normalized);
}

function fileTreeRowDerivedState(fullPath, entry, options = {}) {
  const differMode = options.differMode === true;
  const indexedDirectory = !differMode && entry.kind === 'dir' && fileExplorerDirectoryIsIndexed(fullPath);
  const changedAncestor = !differMode && entry.kind === 'dir' && options.changedAncestorStats instanceof Map
    ? (options.changedAncestorStats.get(fullPath) || null)
    : null;
  const changedFile = entry.kind === 'file'
    ? (options.sessionFilesMap ? (options.sessionFilesMap.get(fullPath) || null) : fileTreeChangedFile(fullPath))
    : null;
  const changedFileStatus = changedFile ? sessionFileDisplayStatus(changedFile) : '';
  const gitStatus = entry.kind === 'file'
    ? (options.sessionFilesMap ? changedFileStatus : fileTreeGitStatus(fullPath))
    : (differMode ? '' : fileExplorerIndexBadgeText(fullPath));
  const displayName = differMode ? {text: entry.name, html: null} : fileTreeDisplayParts(fullPath, entry);
  const dirCountText = entry.kind === 'dir'
    ? (differMode
      ? fileTreeDirCountText(countChangedFilesInDir(fullPath, options.entriesByDir, options.sessionFilesMap))
      : fileTreeDirCountText(changedAncestor?.count))
    : '';
  const directoryDiffParts = entry.kind === 'dir'
    ? (entry.is_repo === true ? fileTreeRepoDiffParts(fullPath) : sessionFileDiffText(changedAncestor || {}))
    : [];
  const icon = options.iconText != null
    ? String(options.iconText)
    : (entry.kind === 'dir' ? (options.expanded === true ? '▾' : '▸') : (entry.kind === 'file' ? fileIconFor(entry.name) : '·'));
  return {
    changedAncestor,
    changedFile,
    changedFileStatus,
    gitClass: fileTreeGitStatusClass(gitStatus),
    repoNonMain: entry.kind === 'dir' && entry.is_repo === true && fileTreeRepoBranchIsNonMain(fullPath),
    icon,
    displayName,
    contentOptions: {
      gitStatus,
      gitStatusTitle: entry.kind === 'dir' && !differMode ? fileExplorerIndexBadgeTitle(fullPath) : gitStatusBadgeTitle(gitStatus),
      iconClass: [fileIconClassFor(entry.name, entry.kind), indexedDirectory ? 'file-icon-dir-indexed' : ''].filter(Boolean).join(' '),
      nameHtml: differMode ? null : displayName.html,
      dateText: options.dateText || '',
      diffParts: changedFile ? sessionFileDiffText(changedFile) : directoryDiffParts,
      agentHtml: changedFile ? changeFileAgentsHtml(changedFile) : (changedAncestor ? changeFileAgentsHtml(changedAncestor) : ''),
      dirCountText,
      preserveDirCount: options.preserveDirCount === true,
      preserveDate: options.preserveDate === true,
      preserveDiff: options.preserveDiff === true,
    },
  };
}

function applyFileTreeRowDerivedState(row, state) {
  applyGitStatusRowClass(row, state.gitClass);
  row.classList.toggle('repo-non-main', state.repoNonMain === true);
  row.classList.toggle('file-tree-row--changed-ancestor', Boolean(state.changedAncestor?.count));
  updateFileTreeRowContents(row, state.icon, state.displayName.text, state.contentOptions);
}

function buildFileTreeRowState(fullPath, entry, depth, options = {}) {
  const differMode = options.differMode === true;
  const compact = options.compact === true;
  const currentDirectory = activeFinderDirectoryPath();
  const expanded = entry.kind === 'dir' && (differMode
    ? !changesFolderCollapsed.has(fullPath)
    : (options.autoExpand === true || fileExplorerExpanded.has(fullPath)));
  const indexedDirectory = !differMode && entry.kind === 'dir' && fileExplorerDirectoryIsIndexed(fullPath);
  const indexedDescendantDirectory = !differMode && entry.kind === 'dir' && !indexedDirectory && Boolean(fileExplorerIndexedAncestor(fullPath));
  const derivedState = fileTreeRowDerivedState(fullPath, entry, {
    ...options,
    expanded,
    dateText: fileTreeMtimeText(entry),
  });
  const changedFile = derivedState.changedFile;
  const changedFileStatus = derivedState.changedFileStatus;
  const repoRoot = differMode && entry.kind === 'dir' ? (options.repoForDiffer || '') : '';
  const relDir = repoRoot && fullPath.startsWith(repoRoot + '/') ? fullPath.slice(repoRoot.length + 1) : '';
  return {
    fullPath,
    entry,
    depth,
    options,
    differMode,
    compact,
    expanded,
    indexedDirectory,
    indexedDescendantDirectory,
    paddingLeft: fileTreeRowPadding(depth, compact),
    selected: fileExplorerSelectedPaths.has(fullPath),
    sessionHighlightClass: fileExplorerSessionHighlightClassForPath(fullPath, entry.kind, {
      differMode,
      sessionHighlightSets: options.sessionHighlightSets,
    }),
    derivedState,
    changedFile,
    changedFileStatus,
    newEntry: !differMode && fileExplorerEntryIsNew(fullPath),
    currentFile: !differMode && entry.kind === 'file' && fullPath === activeFile,
    currentDirectoryRow: !differMode && entry.kind === 'dir' && fullPath === currentDirectory,
    relDir,
    imagePreviewEligible: entry.kind === 'file' && previewMediaKindForPath(entry.name) === 'image' && Number(entry.size || 0) <= MAX_FILE_PREVIEW_BYTES,
  };
}

function applyFileTreeRowDataset(row, state) {
  const {entry, fullPath} = state;
  syncFileTreeRowKindClass(row, entry.kind);
  row.classList.toggle('compact', state.compact);
  setRowDataset(row, 'path', fullPath);
  setRowDataset(row, 'kind', entry.kind);
  setRowDataset(row, 'name', entry.name);
  setRowDataset(row, 'isRepo', entry.is_repo === true ? 'true' : 'false');
  setRowDataset(row, 'isSymlink', entry.is_symlink === true ? 'true' : 'false');
  setRowDataset(row, 'symlinkTarget', entry.symlink_target || '');
  setRowDataset(row, 'indexed', state.indexedDirectory ? 'true' : 'false');
  setRowDataset(row, 'tabberType', '');
  setRowDataset(row, 'tabberSession', '');
  setRowDataset(row, 'tabberWindow', '');
  setRowDataset(row, 'tabberRepoRoot', '');
  setRowDataset(row, 'tabberItem', '');
  setRowDataset(row, 'tabberBranch', '');
  if (row.style.paddingLeft !== state.paddingLeft) row.style.paddingLeft = state.paddingLeft;
  setTreeItemAria(row, {selected: state.selected, expandable: entry.kind === 'dir', expanded: state.expanded});
  row.draggable = entry.kind === 'file' || entry.kind === 'dir';
  row.classList.toggle('selected', state.selected);
  row.classList.toggle('expanded', state.expanded);
  row.classList.toggle('collapsed', entry.kind === 'dir' && !state.expanded);
  row.classList.toggle('is-repo', entry.kind === 'dir' && entry.is_repo === true);
  row.classList.toggle('indexed-directory', state.indexedDirectory);
  row.classList.toggle('indexed-descendant-directory', state.indexedDescendantDirectory);
  applySessionHighlightRowClass(row, state.sessionHighlightClass);
  // flag symlinks so the icon gets an arrow-badge overlay (target-type icon is kept); a broken
  // link gets a red badge + struck-through name. The backend sets is_symlink + kind=symlink-broken.
  row.classList.toggle('is-symlink', entry.is_symlink === true);
  row.classList.toggle('symlink-broken', entry.kind === 'symlink-broken');
}

function bindFinderRowHandlers(row, state) {
  const {entry, fullPath, differMode} = state;
  if (!differMode && entry.kind === 'dir' && entry.is_repo === true) {
    row.removeAttribute('title');
    row.onmouseenter = event => { fileTreeRepoPopoverCursor = {x: event.clientX, y: event.clientY}; scheduleRepoRowHoverPopover(row, fullPath); };
    row.onmousemove = event => { fileTreeRepoPopoverCursor = {x: event.clientX, y: event.clientY}; };
    row.onmouseleave = () => hideFileTreeRepoPopover();
  } else if (!differMode) {
    cancelFileTreeRepoPopoverTimer();
    row.onmouseenter = null;
    row.onmousemove = null;
    row.onmouseleave = null;
    if (row.dataset.repoTitleLoaded) delete row.dataset.repoTitleLoaded;
    if (entry.is_symlink === true && entry.symlink_target) {
      const broken = entry.kind === 'symlink-broken';
      row.title = `${entry.name} → ${entry.symlink_target}${broken ? ` ${t('finder.symlink.broken')}` : ''}`;
    } else {
      row.removeAttribute('title');
    }
  }
  if (!differMode) {
    row.classList.toggle('new-entry', state.newEntry);
    if (state.newEntry) scheduleNewEntryClassRemoval(row, fullPath);
    row.classList.toggle('current-file', state.currentFile);
    row.classList.toggle('current-directory', state.currentDirectoryRow);
    if (state.currentFile || state.currentDirectoryRow) row.setAttribute('aria-current', 'true');
    else row.removeAttribute('aria-current');
  }
  if (state.imagePreviewEligible) bindFileImagePreview(row, fullPath, entry);
  if (differMode) {
    // In differMode, event handling is via delegation in bindChangesPanel; clear Finder handlers
    clearFileTreeRowHandlers(row);
    return;
  }
  row.onpointerdown = event => {
    if (event.button != null && event.button !== 0) return;
    cancelPendingFileExplorerActiveSync();
    row.__fileTreePointerDown = {x: event.clientX || 0, y: event.clientY || 0};
  };
  row.onpointerup = event => {
    if (event.button != null && event.button !== 0) return;
    const start = row.__fileTreePointerDown;
    row.__fileTreePointerDown = null;
    if (row.__fileTreeDragging) return;
    const dx = Math.abs((event.clientX || 0) - (start?.x || 0));
    const dy = Math.abs((event.clientY || 0) - (start?.y || 0));
    if (start && Math.max(dx, dy) > 4) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.detail > 1) return;
    row.__fileTreePointerActivated = true;
    setTimeout(() => { row.__fileTreePointerActivated = false; }, 0);
    onFileTreeRowClick(row, fullPath, entry, event);
  };
  row.onclick = event => {
    if (row.__fileTreePointerActivated) {
      row.__fileTreePointerActivated = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    event.stopPropagation();
    if (event.detail > 1) return;
    onFileTreeRowClick(row, fullPath, entry, event);
  };
  row.ondblclick = event => {
    event.preventDefault();
    event.stopPropagation();
    if (entry.kind === 'dir') openFileExplorerManualRoot(fullPath);
    else openFileInEditor(fullPath, entry);
  };
  row.oncontextmenu = event => {
    event.preventDefault();
    event.stopPropagation();
    closeFileImagePreview();
    showFileTreeContextMenu(row, fullPath, entry, event.clientX, event.clientY);
  };
  row.ondragstart = event => {
    row.__fileTreeDragging = true;
    startFileTreeDrag(event, row, fullPath, entry);
  };
  row.ondragend = () => {
    row.__fileTreeDragging = false;
    dragFilePayloadState = null;
    stopCustomDragPreview();
    clearDropPreview();
  };
}

function bindDifferRowData(row, state) {
  const {entry, fullPath, differMode, changedFile, changedFileStatus} = state;
  // Set data attributes so Differ event delegation (click/drag/contextmenu) can find these rows
  setRowDataset(row, 'openChangeFile', changedFile?.abs_path || '');
  setRowDataset(row, 'openChangeSession', changedFile?.abs_path ? (changedFile.session || '') : '');
  setRowDataset(row, 'openChangeStatus', changedFile?.abs_path ? changedFileStatus : '');
  setRowDataset(row, 'changeRel', changedFile?.abs_path ? (changedFile.path || '') : '');
  setRowDataset(row, 'openChangeRepo', changedFile?.abs_path ? (changedFile.repo || '') : '');
  setRowDataset(row, 'changeSize', changedFile?.abs_path && changedFile.size !== null && changedFile.size !== undefined ? changedFile.size : '');
  if (differMode && entry.kind === 'dir') {
    setRowDataset(row, 'changesFolderToggle', fullPath);
    setRowDataset(row, 'openChangeDirectory', fullPath);
    setRowDataset(row, 'changeRel', state.relDir);
  } else if (!differMode) {
    setRowDataset(row, 'changesFolderToggle', '');
    setRowDataset(row, 'openChangeDirectory', '');
  }
}

function updateFileTreeRow(row, parentPath, entry, depth, options = {}) {
  const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
  // Tabber rows are heterogeneous (session/window/pane/repo/file), so the file-specific git/popover/index
  // logic below does not apply. Render them via the shared column filler (updateFileTreeRowContents) +
  // .file-tree-row DOM, with all display values precomputed in the entry as data — B3's mode:'tabber'.
  if (options.mode === 'tabber') return updateTabberRow(row, fullPath, entry, depth, options);
  const rowState = buildFileTreeRowState(fullPath, entry, depth, options);
  applyFileTreeRowDataset(row, rowState);
  applyFileTreeRowDerivedState(row, rowState.derivedState);
  applyFileTreeRowRecency(row, entry, {differMode: rowState.differMode});
  bindDifferRowData(row, rowState);
  bindFinderRowHandlers(row, rowState);
  return fullPath;
}

function renderTreeChildren(container, parentPath, entries, depth, options = {}) {
  if (!container) return;
  // Step the .file-tree-children::before guide line per nesting level: rows indent 14px/level
  // (updateFileTreeRow padding-left = base + depth*14), and the children wrappers are NOT physically
  // indented, so without this the fixed-left guide stacks every level at one x and only level 1 shows.
  container.style.setProperty('--tree-depth', String(depth));
  cacheFileExplorerRepoInfoEntries(parentPath, entries);
  const renderOptions = {
    ...options,
    sessionHighlightSets: options.sessionHighlightSets || fileExplorerSessionHighlightSets(),
    changedAncestorStats: options.changedAncestorStats instanceof Map ? options.changedAncestorStats : fileTreeChangedAncestorStats(),
  };
  const entriesByDir = renderOptions.entriesByDir instanceof Map ? renderOptions.entriesByDir : null;
  const tabberWindowOrder = renderOptions.mode === 'tabber' && entries.length > 0 && entries.every(entry => entry?.tabber?.type === 'window');
  const visible = sortedFileTreeEntries(entries, renderOptions.treeSortMode, {includeHidden: renderOptions.includeHidden === true, tabberWindowOrder});
  const existingRows = new Map(fileTreeDirectRows(container).map(row => [row.dataset.path, row]));
  const nextNodes = [];
  for (const entry of visible) {
    const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
    const row = existingRows.get(fullPath) || document.createElement('div');
    updateFileTreeRow(row, parentPath, entry, depth, renderOptions);
    nextNodes.push(row);
    const isDifferDir = renderOptions.differMode === true && entry.kind === 'dir';
    // collapsedSet (the Tabber) = default-expanded, collapse to opt out; expandedSet = default-collapsed,
    // expand to opt in (fixed-root Finder uses fileExplorerExpanded). Differences as data, no mode branch.
    const collapseSet = renderOptions.collapsedSet instanceof Set ? renderOptions.collapsedSet : null;
    const expandSet = renderOptions.expandedSet instanceof Set ? renderOptions.expandedSet : fileExplorerExpanded;
    const dirExpanded = collapseSet ? !collapseSet.has(fullPath)
      : (isDifferDir ? !changesFolderCollapsed.has(fullPath) : (renderOptions.autoExpand === true || expandSet.has(fullPath)));
    if (entry.kind === 'dir' && dirExpanded) {
      const childEntries = entriesByDir?.get(normalizeDirectoryPath(fullPath));
      const existingChildContainer = childContainerForRow(row, fullPath);
      const childContainer = existingChildContainer || (Array.isArray(childEntries) ? createFileTreeChildContainer(fullPath) : null);
      if (childContainer) {
        if (Array.isArray(childEntries)) {
          renderTreeChildren(childContainer, fullPath, childEntries, depth + 1, renderOptions);
        }
        nextNodes.push(childContainer);
      }
    }
  }
  reconcileChildNodes(container, nextNodes);
}

function rawFileUrl(path, params = {}) {
  const queryParts = [`path=${encodeURIComponent(path)}`];
  if (shareToken) queryParts.push(`token=${encodeURIComponent(shareToken)}`);
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return `/api/fs/raw?${queryParts.join('&')}`;
}

function closeFileImagePreview(options = {}) {
  if (!options.fromController) fileImagePreviewController?.cancelTimers?.();
  fileImagePreviewPopover?.remove();
  fileImagePreviewPopover = null;
}

function positionFileImagePreview(anchor, popover, point = null) {
  const anchorRect = anchor.getBoundingClientRect();
  const rect = popover.getBoundingClientRect();
  const edgeGap = popoverEdgeGapPx();
  const desiredLeft = point ? point.x + 14 : anchorRect.right + 10;
  const desiredTop = point ? point.y + 14 : anchorRect.top - 8;
  const {left, top} = clampToViewport(desiredLeft, desiredTop, rect.width, rect.height, {edgeGap});
  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function openFileImagePreview(anchor, path, entry, point = null) {
  closeFileImagePreview();
  if (!anchor || !document.body) return;
  const popover = document.createElement('div');
  popover.className = 'file-image-preview-popover';
  popover.dataset.previewPath = path;
  const img = document.createElement('img');
  img.src = rawFileUrl(path);
  img.alt = entry?.name || basenameOf(path);
  popover.appendChild(img);
  appOverlayRootElement().appendChild(popover);
  fileImagePreviewPopover = popover;
  positionFileImagePreview(anchor, popover, point);
}

function bindFileImagePreview(anchor, path, entry) {
  if (!anchor || anchor.dataset.imagePreviewBound === 'true') return;
  anchor.dataset.imagePreviewBound = 'true';
  let pointer = null;
  const updatePointer = event => {
    pointer = {x: event.clientX, y: event.clientY};
    if (fileImagePreviewPopover?.dataset.previewPath === path) positionFileImagePreview(anchor, fileImagePreviewPopover, pointer);
  };
  const controller = createHoverPopover({
    anchor,
    popover: () => fileImagePreviewPopover?.dataset.previewPath === path ? fileImagePreviewPopover : null,
    stateClass: '',
    showDelay: () => Math.max(fileImagePreviewMinShowDelayMs, tabPopoverShowDelayMs),
    hideDelay: () => popoverHideDelayMs,
    canOpen: () => !appMenuIsOpen() && !contextMenuIsOpen() && !topbar?.matches?.(':hover'),
    stillActive: () => popoverStillActive(anchor, fileImagePreviewPopover?.dataset.previewPath === path ? fileImagePreviewPopover : null),
    onQueue: updatePointer,
    onOpen: event => {
      updatePointer(event);
      fileImagePreviewController = controller;
      openFileImagePreview(anchor, path, entry, pointer);
    },
    onClose: () => {
      if (fileImagePreviewPopover?.dataset.previewPath === path) closeFileImagePreview({fromController: true});
      if (fileImagePreviewController === controller) fileImagePreviewController = null;
    },
  });
  anchor.addEventListener('pointerenter', event => {
    fileImagePreviewController = controller;
    updatePointer(event);
  });
  anchor.addEventListener('pointermove', updatePointer);
}

function selectableFileTreeRows(container = document) {
  return Array.from(container.querySelectorAll('.file-tree-row[data-path]'))
    .filter(row => !row.dataset.tabberType && (row.dataset.kind === 'file' || row.dataset.kind === 'dir'));
}

function updateFileExplorerCurrentFileHighlight() {
  const currentDirectory = activeFinderDirectoryPath();
  document.querySelectorAll('.file-tree-row').forEach(row => {
    if (row.dataset.tabberType) return;
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

function scheduleFileExplorerActiveFileReveal(path = activeFile) {
  if (shareReadOnlyFinderStateIsHostOwned()) return;
  if (!path) {
    updateFileExplorerCurrentFileHighlight();
    return;
  }
  const target = normalizeDirectoryPath(path);
  const root = normalizeDirectoryPath(currentFileExplorerRoot());
  updateFileExplorerCurrentFileHighlight();
  if (!fileExplorerIsOpen() || !pathIsInsideDirectory(target, root)) return;
  if (!fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'))) return;
  const generation = ++fileExplorerSyncGeneration;
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
  schedule(() => {
    if (generation !== fileExplorerSyncGeneration) return;
    expandFileExplorerTreesToPath(target, root, generation, {auto: true}).catch(error => {
      console.warn('Finder active file reveal failed', error);
    });
  });
}

function updateFileTreeGitStatusRows() {
  const changedAncestorStats = fileTreeChangedAncestorStats();
  // Exclude Tabber rows: their data-path is a synthetic node path (/s_<id>...), so the finder's
  // git-status/name refresh would rewrite the label to the path basename (s_1/w_0/r_00000) and clobber
  // the Tabber's own render. The Tabber owns its rows via updateTabberRow / refreshTabberPanels.
  document.querySelectorAll('.file-tree-row[data-path]:not([data-tabber-type])').forEach(row => {
    const fullPath = row.dataset.path || '';
    const entry = {
      kind: row.dataset.kind,
      name: row.dataset.name || basenameOf(fullPath),
      is_repo: row.dataset.isRepo === 'true',
      is_symlink: row.dataset.isSymlink === 'true',
      symlink_target: row.dataset.symlinkTarget || '',
    };
    const inDiffer = Boolean(row.closest?.('.file-explorer-changes-panel') || row.dataset.changesFolderToggle || row.dataset.openChangeFile || row.dataset.openChangeDirectory);
    applyFileTreeRowDerivedState(row, fileTreeRowDerivedState(fullPath, entry, {
      changedAncestorStats,
      differMode: inDiffer,
      iconText: row.querySelector(':scope > .file-tree-icon')?.textContent || '',
      preserveDate: true,
      preserveDiff: true,
      preserveDirCount: inDiffer,
    }));
  });
  updateFileExplorerSessionHighlightRows();
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
  fileExplorerSelectionLead = fullPath;   // the keyboard cursor follows the single/clicked selection
  updateFileExplorerCurrentFileHighlight();
}

function selectFileTreeRange(row, fullPath, options = {}) {
  const rows = selectableFileTreeRows(row.closest('[role="tree"]') || document);
  fileExplorerSelectionLead = fullPath;   // a range extend leaves the anchor put but moves the lead to the target
  const scrollContainer = row.closest('.file-explorer-tree-panel');
  const restoreScrollTop = options.preserveScroll !== false && scrollContainer ? scrollContainer.scrollTop : null;
  const finish = () => {
    updateFileExplorerCurrentFileHighlight();
    if (restoreScrollTop !== null) {
      scrollContainer.scrollTop = restoreScrollTop;
      requestAnimationFrame(() => {
        scrollContainer.scrollTop = restoreScrollTop;
      });
    }
  };
  const targetIndex = rows.findIndex(item => item.dataset.path === fullPath);
  let anchorIndex = rows.findIndex(item => item.dataset.path === fileExplorerSelectionAnchor);
  if (anchorIndex < 0) {
    anchorIndex = rows.findIndex(item => fileExplorerSelectedPaths.has(item.dataset.path));
    if (anchorIndex >= 0) fileExplorerSelectionAnchor = rows[anchorIndex].dataset.path;
  }
  if (options.clear !== false) fileExplorerSelectedPaths.clear();
  if (targetIndex < 0) {
    selectFileTreePath(fullPath, {clear: false});
    if (restoreScrollTop !== null) scrollContainer.scrollTop = restoreScrollTop;
    return;
  }
  if (anchorIndex < 0) {
    fileExplorerSelectedPaths.add(fullPath);
    fileExplorerSelectionAnchor = fullPath;
    finish();
    return;
  }
  const start = Math.min(anchorIndex, targetIndex);
  const end = Math.max(anchorIndex, targetIndex);
  for (const selectedRow of rows.slice(start, end + 1)) {
    fileExplorerSelectedPaths.add(selectedRow.dataset.path);
  }
  finish();
}

function updateFileTreeSelectionFromClick(row, fullPath, event) {
  const toggleModifier = appModifier(event);
  if (event.shiftKey) {
    fileExplorerManualSelectionActive = true;
    selectFileTreeRange(row, fullPath, {clear: !toggleModifier});
    return true;
  }
  if (toggleModifier) {
    fileExplorerManualSelectionActive = true;
    selectFileTreePath(fullPath, {clear: false, toggle: true});
    return true;
  }
  fileExplorerManualSelectionActive = false;
  selectFileTreePath(fullPath);
  return false;
}

function sharedTreeRowId(row) {
  return String(row?.dataset?.path || '');
}

function sharedTreeRows(panel, options = {}) {
  const selector = options.rowSelector || '.file-tree-row[data-path]';
  const rows = Array.from(panel?.querySelectorAll?.(selector) || []);
  return rows.filter(row => {
    const id = sharedTreeRowId(row);
    if (!id) return false;
    return typeof options.isRowSelectable === 'function' ? options.isRowSelectable(row) : true;
  });
}

function sharedTreeEventAllowed(event, options = {}) {
  if (typeof options.shouldIgnoreEvent === 'function' && options.shouldIgnoreEvent(event)) return false;
  if (typeof globalShortcutTargetAllowsAppAction === 'function' && !globalShortcutTargetAllowsAppAction(event?.target)) return false;
  return true;
}

const sharedTreeInteractionControllers = new Map();

function registerSharedTreeInteractionController(controller) {
  const name = String(controller?.name || '');
  if (name) sharedTreeInteractionControllers.set(name, controller);
  return controller;
}

function sharedTreeInteractionControllerNames() {
  return Array.from(sharedTreeInteractionControllers.keys()).sort();
}

function sharedTreeKeyIntent(event, options = {}) {
  if (!event || event.altKey) return null;
  const mod = event.metaKey === true || event.ctrlKey === true;
  const shift = event.shiftKey === true;
  if (mod && options.allowSelectAll === true && String(event.key || '').toLowerCase() === 'a') return 'select-all';
  if (mod) return null;
  switch (event.key) {
    case 'ArrowDown': return shift && options.allowRange === true ? 'extend-down' : 'move-down';
    case 'ArrowUp': return shift && options.allowRange === true ? 'extend-up' : 'move-up';
    case 'Home': return shift && options.allowRange === true ? 'extend-home' : 'move-home';
    case 'End': return shift && options.allowRange === true ? 'extend-end' : 'move-end';
    case 'ArrowRight': return shift ? null : 'expand';
    case 'ArrowLeft': return shift ? null : 'collapse';
    case 'Enter': return shift ? null : 'activate';
    default: return null;
  }
}

function consumeSharedTreeEvent(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  event?.stopImmediatePropagation?.();
  if (event) event.__sharedTreeInteractionHandled = true;
}

function sharedTreeScrollRowIntoView(panel, row, options = {}) {
  const container = typeof options.scrollContainer === 'function'
    ? options.scrollContainer(panel, row)
    : (row?.closest?.(options.scrollContainerSelector || '[role="tree"]') || panel);
  if (!scrollFileTreeRowIntoView(container, row)) row?.scrollIntoView?.({block: 'nearest'});
}

function sharedTreeParentRow(rows, row) {
  const id = sharedTreeRowId(row);
  const parent = id ? dirnameOf(id) : '';
  if (!parent || parent === id || parent === '/') return null;
  return rows.find(item => sharedTreeRowId(item) === parent) || null;
}

function sharedTreeChildRow(rows, row) {
  const id = sharedTreeRowId(row);
  const index = rows.indexOf(row);
  if (!id || index < 0) return null;
  const child = rows[index + 1] || null;
  const childId = sharedTreeRowId(child);
  return childId && childId !== id && pathIsInsideDirectory(childId, id) ? child : null;
}

function sharedTreeSelectionApi(controller, state, options = {}) {
  return {
    selectedIds() {
      if (typeof options.selectedIds === 'function') {
        const selected = options.selectedIds();
        if (selected instanceof Set) return selected;
        if (Array.isArray(selected)) return new Set(selected.map(String));
      }
      return state.selectedIds;
    },
    leadId() {
      return typeof options.getLeadId === 'function' ? String(options.getLeadId() || '') : state.leadId;
    },
    setLeadId(id) {
      const value = String(id || '');
      state.leadId = value;
      if (typeof options.setLeadId === 'function') options.setLeadId(value);
    },
    currentId() {
      return typeof options.currentRowId === 'function' ? String(options.currentRowId() || '') : '';
    },
    rowIsCurrent(row) {
      const id = sharedTreeRowId(row);
      return Boolean(id && id === controller.currentId());
    },
    applyState(panel, stateOptions = {}) {
      let currentRow = null;
      const selectedIds = controller.selectedIds();
      for (const row of controller.rows(panel)) {
        const id = sharedTreeRowId(row);
        const selected = selectedIds.has(id);
        const current = controller.rowIsCurrent(row);
        row.classList.toggle('selected', selected);
        row.setAttribute('aria-selected', selected ? 'true' : 'false');
        if (options.applyCurrentClasses !== false) {
          row.classList.toggle('current-file', current && row.dataset.kind !== 'dir');
          row.classList.toggle('current-directory', current && row.dataset.kind === 'dir');
          if (current) row.setAttribute('aria-current', 'true');
          else if (options.preserveExistingAriaCurrent !== true) row.removeAttribute('aria-current');
        }
        if (current) currentRow = row;
      }
      if (stateOptions.scrollCurrent === true && currentRow) sharedTreeScrollRowIntoView(panel, currentRow, options);
    },
    selectRow(panel, row, event = null, selectOptions = {}) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id);
      if (typeof options.selectRow === 'function') {
        options.selectRow(row, id, event, selectOptions);
      } else {
        const selectedIds = controller.selectedIds();
        selectedIds.clear();
        selectedIds.add(id);
      }
      controller.applyState(panel);
      sharedTreeScrollRowIntoView(panel, row, options);
      return true;
    },
    selectRange(panel, row, event = null) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id);
      if (typeof options.selectRange === 'function') {
        options.selectRange(row, id, event);
      } else {
        const rows = controller.rows(panel);
        const selectedIds = controller.selectedIds();
        const anchorId = selectedIds.values().next().value || controller.leadId() || id;
        const anchorIndex = rows.findIndex(item => sharedTreeRowId(item) === anchorId);
        const targetIndex = rows.indexOf(row);
        selectedIds.clear();
        if (anchorIndex >= 0 && targetIndex >= 0) {
          const start = Math.min(anchorIndex, targetIndex);
          const end = Math.max(anchorIndex, targetIndex);
          for (const item of rows.slice(start, end + 1)) selectedIds.add(sharedTreeRowId(item));
        } else {
          selectedIds.add(id);
        }
      }
      controller.applyState(panel);
      sharedTreeScrollRowIntoView(panel, row, options);
      return true;
    },
    selectFromClick(panel, row, event) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id);
      const selectionOnly = typeof options.selectFromClick === 'function'
        ? options.selectFromClick(row, id, event)
        : (controller.selectRow(panel, row, event), false);
      controller.applyState(panel);
      return selectionOnly === true;
    },
    leadRow(panel) {
      const rows = controller.rows(panel);
      const leadId = controller.leadId();
      return rows.find(row => sharedTreeRowId(row) === leadId)
        || rows.find(row => controller.selectedIds().has(sharedTreeRowId(row)))
        || rows.find(row => controller.rowIsCurrent(row))
        || rows[0]
        || null;
    },
    syncCurrent(panel, syncOptions = {}) {
      const currentId = controller.currentId();
      if (currentId && typeof options.syncCurrentSelection === 'function') options.syncCurrentSelection(currentId);
      controller.setLeadId(currentId || controller.leadId());
      controller.applyState(panel, {scrollCurrent: syncOptions.scrollIntoView === true});
      return Boolean(currentId);
    },
  };
}

function sharedTreeExpansionApi(controller, options = {}) {
  return {
    isExpanded(row) {
      if (typeof options.isExpanded === 'function') return options.isExpanded(row) === true;
      return row?.getAttribute?.('aria-expanded') === 'true';
    },
    setExpanded(panel, row, expanded) {
      if (typeof options.setExpanded === 'function') {
        options.setExpanded(row, expanded === true);
        return true;
      }
      return false;
    },
  };
}

function sharedTreeClickHandler(controller, options = {}) {
  return function handleSharedTreeClick(event, panel, clickOptions = {}) {
    const row = clickOptions.row || event?.target?.closest?.(options.rowSelector || '.file-tree-row[data-path]');
    if (!row || !panel?.contains?.(row) || !controller.rows(panel).includes(row)) return false;
    if (typeof options.shouldIgnoreEvent === 'function' && options.shouldIgnoreEvent(event)) return false;
    if (typeof globalShortcutTargetAllowsAppAction === 'function' && !globalShortcutTargetAllowsAppAction(event?.target) && !clickOptions.row) return false;
    const onDisclosure = Boolean(event?.target?.closest?.('.file-tree-icon'));
    consumeSharedTreeEvent(event);
    const selectionOnly = controller.selectFromClick(panel, row, event);
    if (row.dataset.kind === 'dir' && onDisclosure) {
      controller.setExpanded(panel, row, !controller.isExpanded(row));
      return true;
    }
    if (!selectionOnly && options.activateOnClick !== false) controller.activateRow(panel, row, event);
    return true;
  };
}

function sharedTreeKeyboardHandler(controller, options = {}) {
  return function handleSharedTreeKeydown(event, panel) {
    if (typeof options.shouldIgnoreEvent === 'function' && options.shouldIgnoreEvent(event)) return false;
    const intent = sharedTreeKeyIntent(event, options);
    if (!intent) return false;
    const rows = controller.rows(panel);
    if (!rows.length) return false;
    const eventTargetsOwnedPanel = panel?.contains?.(event?.target) || event?.target === panel;
    if (typeof globalShortcutTargetAllowsAppAction === 'function' && !globalShortcutTargetAllowsAppAction(event?.target) && !eventTargetsOwnedPanel) return false;
    const lead = controller.leadRow(panel);
    let leadIndex = lead ? rows.indexOf(lead) : -1;
    if (intent === 'select-all' && options.allowSelectAll === true) {
      consumeSharedTreeEvent(event);
      const selectedIds = controller.selectedIds();
      selectedIds.clear();
      for (const row of rows) selectedIds.add(sharedTreeRowId(row));
      controller.setLeadId(sharedTreeRowId(rows[rows.length - 1]));
      if (typeof options.afterSelectAll === 'function') options.afterSelectAll(rows, event);
      controller.applyState(panel);
      return true;
    }
    if (intent === 'activate') {
      if (!lead) return false;
      consumeSharedTreeEvent(event);
      controller.selectRow(panel, lead, event);
      controller.activateRow(panel, lead, event);
      return true;
    }
    if (intent === 'expand' || intent === 'collapse') {
      if (!lead) return false;
      consumeSharedTreeEvent(event);
      const expanded = controller.isExpanded(lead);
      if (intent === 'expand') {
        if (lead.dataset.kind === 'dir' && !expanded) controller.setExpanded(panel, lead, true);
        else {
          const child = sharedTreeChildRow(rows, lead);
          if (child) controller.selectRow(panel, child, event);
        }
        return true;
      }
      if (lead.dataset.kind === 'dir' && expanded) controller.setExpanded(panel, lead, false);
      else {
        const parent = sharedTreeParentRow(rows, lead);
        if (parent) controller.selectRow(panel, parent, event);
      }
      return true;
    }
    if (leadIndex < 0) leadIndex = 0;
    const lastIndex = rows.length - 1;
    let nextIndex = leadIndex;
    if (intent === 'move-home' || intent === 'extend-home') nextIndex = 0;
    else if (intent === 'move-end' || intent === 'extend-end') nextIndex = lastIndex;
    else if (intent === 'move-down' || intent === 'extend-down') nextIndex = Math.min(lastIndex, leadIndex + 1);
    else if (intent === 'move-up' || intent === 'extend-up') nextIndex = Math.max(0, leadIndex - 1);
    consumeSharedTreeEvent(event);
    const nextRow = rows[nextIndex];
    if (intent.startsWith('extend')) controller.selectRange(panel, nextRow, event);
    else controller.selectRow(panel, nextRow, event);
    return true;
  };
}

function createSharedTreeInteractionController(options = {}) {
  const state = {
    selectedIds: new Set(),
    leadId: '',
  };
  const controller = {
    name: options.name || 'tree',
    rows(panel) {
      return sharedTreeRows(panel, options);
    },
    activateRow(panel, row, event = null) {
      if (typeof options.activateRow === 'function') {
        options.activateRow(row, event);
        controller.applyState(panel);
        return true;
      }
      return false;
    },
  };
  Object.assign(controller, sharedTreeSelectionApi(controller, state, options));
  Object.assign(controller, sharedTreeExpansionApi(controller, options));
  controller.handleClick = sharedTreeClickHandler(controller, options);
  controller.handleKeydown = sharedTreeKeyboardHandler(controller, options);
  return registerSharedTreeInteractionController(controller);
}

function fileTreeActionPaths(fullPath) {
  if (fileExplorerSelectedPaths.has(fullPath)) return Array.from(fileExplorerSelectedPaths);
  return [fullPath];
}

function compactNestedPaths(paths) {
  const sorted = Array.from(new Set(paths)).sort((left, right) => left.localeCompare(right));
  return sorted.filter((path, index) => !sorted.some((other, otherIndex) => otherIndex !== index && path.startsWith(`${other}/`)));
}

function fileExplorerDirectoryIsIndexed(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  return Boolean(normalized && fileExplorerIndexedDirs.has(normalized));
}

// Compact badge text for an indexed directory. When Date/Ago is visible, the directory icon is the only
// index marker; the status column must stay empty so it cannot compete with the date column.
function fileExplorerIndexBadgeText(path) {
  if (fileExplorerTreeDateMode !== 'none') return '';
  if (fileExplorerDirectoryIsIndexed(path)) {
    const normalized = normalizeStoredFileExplorerIndexedDir(path);
    return fileExplorerIndexStatus.get(normalized) === 'building' ? '…' : 'I';
  }
  if (fileExplorerIndexedAncestor(path)) return '';
  return '';
}

function fileExplorerIndexBadgeTitle(path) {
  if (!fileExplorerDirectoryIsIndexed(path)) return '';
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  return fileExplorerIndexStatus.get(normalized) === 'building' ? 'indexing…' : 'indexed';
}

// Warm the backend index for a root (kicks the build) and track building/ready; polls while
// building so the badge title transitions indexing… -> indexed exactly once.
async function refreshFileIndexStatus(root) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return;
  let payload;
  try {
    payload = await apiFetchJson(`/api/fs/index-status?root=${encodeURIComponent(normalized)}`);
  } catch (error) {
    return;  // transient error: keep the prior badge, don't flip it
  }
  if (!fileExplorerIndexedDirs.has(normalized)) return;  // un-indexed while the request was in flight
  const status = payload && payload.ready ? 'ready' : 'building';
  const previous = fileExplorerIndexStatus.get(normalized);
  fileExplorerIndexStatus.set(normalized, status);
  if (status === 'building') fileIndexStatusPollRoots.add(normalized);
  else fileIndexStatusPollRoots.delete(normalized);
  syncFileIndexStatusPollInterval();
  if (previous !== status) updateFileExplorerIndexedDirectoryRows();
}

function refreshBuildingFileIndexStatuses() {
  for (const root of Array.from(fileIndexStatusPollRoots)) {
    if (!fileExplorerIndexedDirs.has(root)) {
      fileIndexStatusPollRoots.delete(root);
      continue;
    }
    refreshFileIndexStatus(root);
  }
  syncFileIndexStatusPollInterval();
}

function syncFileIndexStatusPollInterval() {
  if (!fileIndexStatusPollRoots.size) {
    clearRuntimeInterval('file-index-building');
    return;
  }
  const proactiveMs = Math.max(1, fileExplorerIndexRefreshSeconds * 1000);
  // Backend-facing poll intervals stay odd-numbered to avoid synchronized timer collisions.
  resetRuntimeInterval('file-index-building', refreshBuildingFileIndexStatuses, Math.min(1501, proactiveMs));
}

// Lazily warm/poll an indexed root's status, without re-fetching once it is known ready or while a
// poll is already pending (so per-row render calls don't spam the endpoint).
function ensureFileIndexStatus(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return;
  if (fileExplorerIndexStatus.get(normalized) === 'ready' || fileIndexStatusPollRoots.has(normalized)) return;
  refreshFileIndexStatus(normalized);
}

function clearFileIndexStatus(root) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized) return;
  fileExplorerIndexStatus.delete(normalized);
  fileIndexStatusPollRoots.delete(normalized);
  syncFileIndexStatusPollInterval();
}

// Proactive periodic re-check: re-fetches index-status for every indexed root even if already
// 'ready', so stale indexes (TTL expired server-side) get rebuilt without waiting for a search.
function refreshAllIndexedDirsStatus() {
  for (const root of fileExplorerIndexedDirs) {
    refreshFileIndexStatus(root);
  }
}

function fileExplorerIndexedRootList() {
  return compactNestedPaths(Array.from(fileExplorerIndexedDirs || [])
    .map(normalizeStoredFileExplorerIndexedDir)
    .filter(Boolean));
}

function fileExplorerIndexedAncestor(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized) return '';
  return fileExplorerIndexedRootList()
    .filter(candidate => candidate !== normalized && pathIsInsideDirectory(normalized, candidate))
    .sort((left, right) => right.length - left.length)[0] || '';
}

function setFileExplorerIndexedDirs(paths) {
  const normalized = (paths || []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean);
  fileExplorerIndexedDirs = new Set(compactNestedPaths(normalized));
  writeStoredFileExplorerIndexedDirs();
}

function setFileExplorerDirectoryIndexed(path, indexed) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized) return;
  if (indexed) {
    const ancestor = fileExplorerIndexedAncestor(normalized);
    if (ancestor) {
      if (statusEl) statusEl.textContent = `${compactHomePath(normalized)} is already indexed by ${compactHomePath(ancestor)}`;
      return;
    }
    fileExplorerIndexedDirs.add(normalized);
    fileExplorerIndexedDirs = new Set(compactNestedPaths(Array.from(fileExplorerIndexedDirs)));
    // Eagerly build the backend index now (warm) so the first quick-open query hits a warm index,
    // and expose the building title immediately until the build reports ready.
    fileExplorerIndexStatus.set(normalized, 'building');
    refreshFileIndexStatus(normalized);
  } else {
    fileExplorerIndexedDirs.delete(normalized);
    clearFileIndexStatus(normalized);
    abortFileQuickOpenSearch();
    if (!applyingIndexedDirsSetting) {
      apiFetch(`/api/fs/unindex?root=${encodeURIComponent(normalized)}`, {method: 'POST', body: JSON.stringify({root: normalized})}).catch(() => {});
    }
  }
  writeStoredFileExplorerIndexedDirs();
  // Mirror the set into the file_explorer.indexed_dirs setting so the Preferences list stays in sync
  // (skip when this change WAS driven by the setting, to avoid a write-back loop). C11: pass the single
  // add/remove so the save MERGES into the shared list instead of overwriting it with this page's set.
  if (!applyingIndexedDirsSetting) persistIndexedDirsSetting(indexed ? {add: normalized} : {remove: normalized});
  updateFileExplorerIndexedDirectoryRows();
  if (statusEl) {
    statusEl.textContent = indexed ? `indexed ${compactHomePath(normalized)}` : `removed index ${compactHomePath(normalized)}`;
  }
}

function persistIndexedDirsSetting(op = {}) {
  // C11: MERGE the change into the current shared file_explorer.indexed_dirs rather than overwriting it
  // with this page's whole set. Two browser origins (:7777 vs :8001) do NOT share localStorage, so a
  // whole-list save from one would drop the other's dirs and make rows flip indexed/un-indexed on the
  // next settings poll. An explicit {add}/{remove} applies just that one op to the shared list; a bare
  // save (initial localStorage->setting migration) only UNIONS this page's dirs in, never removing.
  const current = initialSetting('file_explorer.indexed_dirs', []);
  const currentNorm = (Array.isArray(current) ? current : []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean);
  const set = new Set(currentNorm);
  if (op.add) set.add(op.add);
  if (op.remove) set.delete(op.remove);
  if (!op.add && !op.remove) {
    for (const dir of fileExplorerIndexedRootList()) set.add(dir);
  }
  const dirs = compactNestedPaths(Array.from(set));
  const currentList = compactNestedPaths(currentNorm);
  if (currentList.length === dirs.length && currentList.every((value, index) => value === dirs[index])) return;
  saveSettingsPatch({file_explorer: {indexed_dirs: dirs}}, {silent: true}).catch(() => {});
}

// C11 #3: the shared file_explorer.indexed_dirs SETTING is the durable source of truth for which dirs are
// indexed; per-origin localStorage is only a cache + a ONE-TIME migration seed. This reconciles the
// in-memory set FROM the setting (so the setting is authoritative — local-only dirs not in the setting are
// dropped), and on the very first load migrates any pre-existing localStorage dirs INTO the setting exactly
// once (guarded by a migration marker so a stale per-origin cache can never re-seed the setting later).
function reconcileIndexedDirsFromSetting(options = {}) {
  const raw = initialSetting('file_explorer.indexed_dirs', []);
  const list = Array.isArray(raw) ? raw : [];
  const migrated = storageGet(fileExplorerIndexedDirsMigratedKey) === '1';
  if (options.initial && !migrated) {
    // One-time migration: if the setting is empty but this origin's localStorage already has indexed dirs,
    // seed the durable setting from them (don't wipe the user's existing indexed directories). Either way,
    // record that migration is done so localStorage is never treated as a desired-root peer again.
    if (!list.length && fileExplorerIndexedDirs.size) {
      persistIndexedDirsSetting();
      storageSet(fileExplorerIndexedDirsMigratedKey, '1');
      return;
    }
    storageSet(fileExplorerIndexedDirsMigratedKey, '1');
  }
  const desired = new Set(compactNestedPaths(list.map(normalizeStoredFileExplorerIndexedDir).filter(Boolean)));
  const current = new Set(fileExplorerIndexedDirs);
  const adds = Array.from(desired).filter(dir => !current.has(dir));
  const removes = Array.from(current).filter(dir => !desired.has(dir));
  if (!adds.length && !removes.length) return;
  applyingIndexedDirsSetting = true;
  try {
    for (const dir of adds) setFileExplorerDirectoryIndexed(dir, true);
    for (const dir of removes) {
      setFileExplorerDirectoryIndexed(dir, false);
      apiFetch(`/api/fs/unindex?root=${encodeURIComponent(dir)}`, {method: 'POST', body: JSON.stringify({root: dir})}).catch(() => {});
    }
  } finally {
    applyingIndexedDirsSetting = false;
  }
}

function toggleFileExplorerDirectoryIndexed(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized) return;
  setFileExplorerDirectoryIndexed(normalized, !fileExplorerDirectoryIsIndexed(normalized));
}

function updateFileExplorerIndexedDirectoryRows() {
  document.querySelectorAll('.file-tree-row.kind-dir[data-path]').forEach(row => {
    const path = row.dataset.path || '';
    const indexed = fileExplorerDirectoryIsIndexed(path);
    const indexedDescendant = !indexed && Boolean(fileExplorerIndexedAncestor(path));
    row.dataset.indexed = indexed ? 'true' : 'false';
    row.classList.toggle('indexed-directory', indexed);
    row.classList.toggle('indexed-descendant-directory', indexedDescendant);
    if (indexed) ensureFileIndexStatus(path);  // warm + learn building/ready for the badge
    const icon = row.querySelector(':scope > .file-tree-icon');
    if (icon) {
      setClassNameIfChanged(icon, ['file-tree-icon', 'file-icon-dir', indexed ? 'file-icon-dir-indexed' : ''].filter(Boolean).join(' '));
    }
    const status = row.querySelector(':scope > .file-tree-git-status');
    if (status) {
      const badge = fileExplorerIndexBadgeText(path);
      if (status.textContent !== badge) status.textContent = badge;
      const title = fileExplorerIndexBadgeTitle(path);
      if (title) status.setAttribute('title', title);
      else status.removeAttribute('title');
      setHiddenIfChanged(status, !badge);
    }
  });
}

function fileExplorerIndexedSearchRoots(defaultRoot = fileQuickOpenRootForSearch(), extraRoots = []) {
  const defaultPath = normalizeStoredFileExplorerIndexedDir(defaultRoot);
  const indexedRoots = fileExplorerIndexedRootList();
  const defaultCoveredByIndexed = defaultPath && indexedRoots.some(root => root !== defaultPath && pathIsInsideDirectory(defaultPath, root));
  const defaultHasIndexedDescendant = defaultPath && indexedRoots.some(root => root !== defaultPath && pathIsInsideDirectory(root, defaultPath));
  const roots = defaultPath && !defaultCoveredByIndexed && !defaultHasIndexedDescendant ? [defaultPath] : [];
  for (const indexedRoot of indexedRoots) {
    if (!roots.includes(indexedRoot)) roots.push(indexedRoot);
  }
  for (const extraRoot of compactNestedPaths((extraRoots || []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean))) {
    if (!roots.some(root => pathIsInsideDirectory(extraRoot, root))) roots.push(extraRoot);
  }
  return roots;
}

// ---------------------------------------------------------------------------
// Tabber — the Finder pane's third mode (Finder / Differ / Tabber). A live, default-expanded tree:
// tmux sessions (level 0) -> their tmux windows (level 1, index:process, the current window marked) -> for
// claude/codex windows, the paths that agent touched grouped by repo (level 2/3, from /api/session-files).
// Rows render through the SHARED row pipeline (renderTreeChildren -> updateFileTreeRow ->
// updateFileTreeRowContents) via a mode:'tabber' option whose display values are precomputed as data; the
// collapse state is a persisted COLLAPSED set (default expanded), times come from the activity ledger with
// parent = max(child), and sort honors the active Finder sort (label for A-Z, mtime for recent).
// ---------------------------------------------------------------------------
let tabberRefreshDeferredTimer = null;

function tabberPad(value) {
  return String(value).padStart(5, '0');
}

function tabberPathToken(value) {
  return String(value).replace(/[^A-Za-z0-9._-]/g, '_');
}

function persistTabberCollapsed() {
  storageSet(fileExplorerTabberCollapsedStorageKey, JSON.stringify(Array.from(fileExplorerTabberCollapsed).sort()));
}

// Recency for a ledger key (session "6" or session:window "6:1") is derived by the backend once as active_recency_ts.
function tabberRecency(key) {
  const rec = tabberActivityPayload?.activity?.[key];
  if (!rec) return 0;
  const derived = Number(rec.active_recency_ts || 0);
  if (Number.isFinite(derived) && derived > 0) return derived;
  return Math.max(Number(rec.last_user_input_ts || 0), Number(rec.last_agent_active_ts || 0), Number(rec.last_output_ts || 0));
}

function tabberRecentAgents(payload = tabberActivityPayload) {
  return Array.isArray(payload?.agents) ? payload.agents.filter(item => item && typeof item === 'object') : [];
}

function tabberAgentForWindow(session, windowIndex, agentKey = '') {
  const sessionText = String(session || '');
  const windowText = String(windowIndex ?? '');
  const kindText = String(agentKey || '').toLowerCase();
  let best = null;
  for (const agent of tabberRecentAgents()) {
    if (String(agent.session || '') !== sessionText) continue;
    if (String(agent.window ?? '') !== windowText) continue;
    if (kindText && String(agent.agent_kind || '').toLowerCase() !== kindText) continue;
    if (!best || Number(agent.sort_ts || agent.last_used_ts || 0) > Number(best.sort_ts || best.last_used_ts || 0)) best = agent;
  }
  return best;
}

function tabberAgentRecency(agent) {
  if (!agent) return 0;
  return Math.max(Number(agent.sort_ts || 0), Number(agent.last_used_ts || 0));
}

function tabberAgentDateText(agent) {
  if (!agent) return '';
  if (agent.running === true) return t('yoagent.agent.running');
  const ts = Number(agent.last_used_ts || 0);
  if (!Number.isFinite(ts) || ts <= 0) return '';
  return sessionFileDisplayTimeText(ts);
}

async function fetchTabberActivity() {
  try {
    const params = new URLSearchParams();
    params.set('hours', String(normalizeSessionFileLookbackHours(tabberSessionFileLookbackHours)));
    const payload = await apiFetchJson(`/api/activity?${params.toString()}`, {cache: 'no-store'});
    if (payload && typeof payload === 'object' && payload.activity && typeof payload.activity === 'object') {
      tabberActivityPayload = payload;
    }
  } catch (_) {
    // keep the last snapshot; recency just goes stale until the next tick
  }
  if (fileExplorerMode === 'tabber') refreshTabberPanels();
}

function warmTabberDataOnLaunch() {
  if (tabberLaunchWarmupStarted || !transcriptMetaLoaded) return false;
  tabberLaunchWarmupStarted = true;
  fetchTabberActivity();
  return true;
}

function tabberLookbackControlHtml() {
  const options = sessionFileLookbackOptions()
    .map(option => `<option value="${esc(option.hours)}"${option.hours === tabberSessionFileLookbackHours ? ' selected' : ''}>${esc(option.label)}</option>`)
    .join('');
  return `<label class="info-lookback-control tabber-lookback-control">${esc(t('info.lookback'))}<select data-tabber-lookback>${options}</select></label>`;
}

function setTabberSessionFileLookbackHours(hours, options = {}) {
  const previous = tabberSessionFileLookbackHours;
  tabberSessionFileLookbackHours = writeStoredTabberLookbackHours(hours);
  if (tabberSessionFileLookbackHours !== previous) {
    clearTabberSessionFilesStates();
    if (options.refresh !== false) {
      fetchTabberActivity();
      if (fileExplorerMode === 'tabber') refreshTabberPanels();
    }
  }
  return tabberSessionFileLookbackHours;
}

function tabberSessionFilesStateKey(session, hours = tabberSessionFileLookbackHours) {
  const boundedHours = normalizeSessionFileLookbackHours(hours);
  return `${String(session || '')}\u0000${boundedHours}`;
}

// Touched-path cache, keyed by session plus lookback (lazily fetched; never disturbs the Differ target).
const tabberSessionFilesStates = new Map();
const tabberTreeSelectedPaths = new Set();
let tabberTreeSelectionLead = '';

function tabberSessionFilesState(session, hours = tabberSessionFileLookbackHours) {
  const boundedHours = normalizeSessionFileLookbackHours(hours);
  const key = tabberSessionFilesStateKey(session, boundedHours);
  let state = tabberSessionFilesStates.get(key);
  if (!state) {
    state = {session: String(session || ''), hours: boundedHours, files: [], loaded: false, loading: false};
    tabberSessionFilesStates.set(key, state);
  }
  return state;
}

function clearTabberSessionFilesStates() {
  tabberSessionFilesStates.clear();
}

async function fetchTabberSessionFiles(session, options = {}) {
  if (!session) return;
  const hours = normalizeSessionFileLookbackHours(options.hours ?? tabberSessionFileLookbackHours);
  const state = tabberSessionFilesState(session, hours);
  if (!options.force && (state.loaded || state.loading)) return;
  state.loading = true;
  if (fileExplorerMode === 'tabber') refreshTabberPanels();
  try {
    const payload = await apiFetchJson(`/api/session-files?session=${encodeURIComponent(session)}&hours=${encodeURIComponent(String(hours))}`, {cache: 'no-store'});
    state.files = Array.isArray(payload?.files) ? payload.files : [];
    state.loaded = true;
  } catch (_) {
    state.files = [];
    state.loaded = true;
  } finally {
    state.loading = false;
  }
  if (fileExplorerMode === 'tabber') refreshTabberPanels();
}

async function fetchTabberSessionFilesBatch(sessions, options = {}) {
  const hours = normalizeSessionFileLookbackHours(options.hours ?? tabberSessionFileLookbackHours);
  const targets = [];
  const seen = new Set();
  for (const rawSession of sessions || []) {
    const session = String(rawSession || '').trim();
    if (!session || seen.has(session)) continue;
    seen.add(session);
    const state = tabberSessionFilesState(session, hours);
    if (!options.force && (state.loaded || state.loading)) continue;
    targets.push(session);
  }
  if (!targets.length) return;
  for (const session of targets) {
    const state = tabberSessionFilesState(session, hours);
    state.loading = true;
  }
  if (fileExplorerMode === 'tabber') refreshTabberPanels();
  try {
    const params = new URLSearchParams();
    for (const session of targets) params.append('session', session);
    params.set('hours', String(hours));
    const payload = await apiFetchJson(`/api/session-files-batch?${params.toString()}`, {cache: 'no-store'});
    const payloads = payload?.sessions && typeof payload.sessions === 'object' ? payload.sessions : {};
    for (const session of targets) {
      const state = tabberSessionFilesState(session, hours);
      const sessionPayload = payloads[session] || {};
      state.files = Array.isArray(sessionPayload.files) ? sessionPayload.files : [];
      state.loaded = true;
    }
  } catch (_) {
    for (const session of targets) {
      const state = tabberSessionFilesState(session, hours);
      state.files = [];
      state.loaded = true;
    }
  } finally {
    for (const session of targets) tabberSessionFilesState(session, hours).loading = false;
  }
  if (fileExplorerMode === 'tabber') refreshTabberPanels();
}

// Tabber level 0: session_order first, then any remaining live tmux sessions with panes.
function tabberOrderedSessions() {
  const order = Array.isArray(transcriptMeta.session_order) ? transcriptMeta.session_order : [];
  const all = transcriptMeta.sessions && typeof transcriptMeta.sessions === 'object' ? Object.keys(transcriptMeta.sessions) : [];
  const seen = new Set();
  const ordered = [];
  for (const session of [...order, ...all]) {
    if (seen.has(session)) continue;
    seen.add(session);
    if (!isTmuxSession(session)) continue;
    const info = transcriptMeta.sessions?.[session];
    if (!Array.isArray(info?.panes) || !info.panes.length) continue;
    ordered.push(session);
  }
  return ordered;
}

function tabberWindowIsAgent(name) {
  const key = tmuxWindowAgentKey(name);
  return key === 'claude' || key === 'codex';
}

function tabberAgentSessions() {
  const sessions = [];
  for (const session of tabberOrderedSessions()) {
    const info = transcriptMeta.sessions?.[session];
    if (tmuxWindowRecords(info?.panes).some(record => tabberWindowIsAgent(record.name))) sessions.push(session);
  }
  return sessions;
}

function ensureTabberSessionFilesFetches() {
  // Agent-window paths now come from the cached /api/activity agent_windows payload.
  // The session-files fetchers remain for the modified-files UI and focused tests.
  if (!tabberActivityPayload?.agent_windows) fetchTabberActivity();
}

function tabberRepoEntriesForAgentWindow(agent, session, windowIndex) {
  const pathEntries = agentWindowPathEntries(agent);
  return pathEntries
    .map((item, pathPos) => {
      const git = item.git && typeof item.git === 'object' ? item.git : agentWindowPrimaryGit(agent);
      const branchText = git?.branch ? shortBranch(git.branch) : '';
      return {
        name: `r_${tabberPad(pathPos)}`, kind: 'file', mtime: item.mtime, sortName: item.path,
        tabber: {type: 'repo', session, windowIndex, repoRoot: item.path, label: item.path, icon: '📁', branchText},
      };
    });
}

function tmuxWindowAgentStatus(session, record, info = null) {
  const status = agentWindowStatusForRecord(session, record, info);
  const statusKind = agentWindowKind(status?.kind);
  const recordKind = agentWindowKind(tmuxWindowAgentKey(record?.name));
  const agentKey = statusKind || recordKind;
  return {status, agentKey};
}

function tmuxWindowCanonicalLabel(session, record, fallback = '', info = null) {
  const {agentKey} = tmuxWindowAgentStatus(session, record, info);
  return agentWindowCanonicalLabel(record?.indexText ?? record?.index, agentKey, fallback);
}

function buildTabberTree() {
  const entriesByDir = new Map();
  const topEntries = [];
  const activeSession = currentSessionActionTarget();
  tabberOrderedSessions().forEach(session => {
    const info = transcriptMeta.sessions?.[session] || {};
    const sessionName = `s_${tabberPathToken(session)}`;
    const sessionPath = `/${sessionName}`;
    const git = info?.project?.git;
    const branch = git?.branch ? shortBranch(git.branch) : '';
    const fallbackSessionRecency = tabberRecency(session);
    const sessionWork = sessionWorkDescription(session, info, 200);
    const sessionNameLabel = sessionLabel(session) || session;
    const sessionDisplay = sessionWork ? `${sessionNameLabel}  ${sessionWork}` : sessionNameLabel;
    const nowSeconds = Date.now() / 1000;
    const sessionEntry = {
      name: sessionName, kind: 'dir', mtime: 0, sortName: sessionDisplay,
      tabber: {type: 'session', session, label: sessionNameLabel, description: sessionWork, icon: '●', branchText: branch, active: session === activeSession},
    };
    topEntries.push(sessionEntry);
    const activeIndexOverride = tmuxWindowDisplayActiveIndex(session);
    const activeIndexOverlay = activeIndexOverride === tmuxWindowPendingActiveIndex ? null : activeIndexOverride;
    const windowEntries = tmuxWindowRecords(info.panes).map(record => {
      const windowName = `w_${tabberPathToken(record.index)}`;
      const windowPath = `${sessionPath}/${windowName}`;
      const {status: agentStatus, agentKey} = tmuxWindowAgentStatus(session, record, info);
      const isAgent = Boolean(agentKey) || tabberWindowIsAgent(record.name);
      const agentActivity = isAgent ? tabberAgentForWindow(session, record.index, agentKey) : null;
      const repoEntries = isAgent && agentStatus ? tabberRepoEntriesForAgentWindow(agentStatus, session, record.index) : [];
      const childMtime = repoEntries.reduce((max, entry) => Math.max(max, Number(entry.mtime || 0)), 0);
      const ledgerMtime = isAgent ? 0 : tabberRecency(`${session}:${record.index}`);
      // Agent windows use transcript activity only. Their touched repo rows may have newer file mtimes, but
      // parent window/session recency must still answer "when was this agent last used?"
      const windowMtime = isAgent
        ? tabberAgentRecency(agentActivity)
        : Math.max(ledgerMtime, childMtime, fallbackSessionRecency);
      const agentCurrent = agentWindowPayloadCurrent(agentStatus);
      const recordActive = agentCurrent === null ? record.active === true : agentCurrent === true;
      const active = activeIndexOverride === undefined ? recordActive : (activeIndexOverlay !== null && String(record.index) === activeIndexOverlay);
      const label = tmuxWindowCanonicalLabel(session, record, record.indexedButtonLabel || `${record.indexText}:${record.buttonNameLabel || record.name}`, info);
      const agentStatusForDisplay = agentStatus ? {...agentStatus, current: active, window_active: active} : null;
      const agentStatusForIcon = agentStatusForDisplay || (active && ['claude', 'codex'].includes(agentKey)
        ? {kind: agentKey, state: 'idle', window: record.indexText, window_index: record.index, current: true, window_active: true}
        : agentStatus);
      const activityIconHtml = agentWindowActivityIconHtmlForStatus(agentStatusForIcon, agentKey, session);
      const dateText = agentStatusForDisplay ? sessionPopoverAgentStateText(agentStatusForDisplay, nowSeconds) : tabberAgentDateText(agentActivity);
      const dateHtml = agentStatusForDisplay && agentWindowIsAttentionState(agentStatusForDisplay.state)
        ? sessionPopoverAgentStatusHtml(agentStatusForDisplay, nowSeconds, 'tabber-agent-status')
        : '';
      if (repoEntries.length) entriesByDir.set(normalizeDirectoryPath(windowPath), repoEntries);
      return {
        name: windowName, kind: repoEntries.length ? 'dir' : 'file', mtime: windowMtime,
        sortName: label,
        tabber: {type: 'window', session, windowIndex: record.index, label, pid: record.pid, icon: '', active, current: session === activeSession && active, agentKey, activityIconHtml, dateText, dateHtml},
      };
    });
    entriesByDir.set(normalizeDirectoryPath(sessionPath), windowEntries);
    const maxChild = windowEntries.reduce((max, entry) => Math.max(max, Number(entry.mtime || 0)), 0);
    sessionEntry.mtime = Math.max(maxChild, windowEntries.length ? 0 : fallbackSessionRecency);
    sessionEntry.tabber.current = session === activeSession && !windowEntries.some(entry => entry.tabber?.current === true);
  });
  // The other open (non-tmux) tabs — Preferences, YO!info/YO!agent, file editors — as leaf rows after the
  // sessions. They are kind:'file', so the shared dirs-before-files sort always keeps them below sessions.
  for (const item of allTabItems()) {
    if (isTmuxSession(item) || isFileExplorerItem(item)) continue; // tmux sessions are shown above; skip the Finder/Tabber pane itself
    topEntries.push({
      name: `t_${tabberPathToken(item)}`, kind: 'file', mtime: 0, sortName: itemLabel(item),
      tabber: {type: 'tab', item, label: itemLabel(item), icon: '◷'},
    });
  }
  return {entries: topEntries, entriesByDir};
}

function tabberSortMode() {
  return ['az', 'za', 'newest', 'oldest'].includes(fileExplorerTreeSortMode) ? fileExplorerTreeSortMode : 'newest';
}

function renderTabberTree(groupsEl) {
  if (!groupsEl) return;
  ensureTabberSessionFilesFetches();
  const {entries, entriesByDir} = buildTabberTree();
  const collapsedSet = new Set(fileExplorerTabberCollapsed);
  if (!entries.length) {
    groupsEl.innerHTML = '<div class="changes-empty">No open tmux sessions</div>';
    return;
  }
  let container = groupsEl.querySelector(':scope > .file-tree[data-tabber-tree]');
  if (!container) {
    groupsEl.innerHTML = '';
    container = document.createElement('div');
    container.className = 'file-tree';
    container.dataset.tabberTree = 'true';
    container.setAttribute('role', 'tree');
    groupsEl.appendChild(container);
  }
  renderTreeChildren(container, '/', entries, 0, {
    mode: 'tabber',
    collapsedSet,
    entriesByDir,
    treeSortMode: tabberSortMode(),
    includeHidden: true,
  });
  syncTabberTreeActiveSelection(container);
}

function tabberSessionPopoverRefreshIsUnsafe() {
  const selector = [
    '.tabber-session-tab.popover-open',
    '.tabber-session-tab[data-popover-hover-state="pending"]',
    '.tabber-session-tab[data-popover-hover-state="open"]',
    '.tabber-session-tab[data-popover-hover-state="closing"]',
  ].join(', ');
  for (const tab of document.querySelectorAll(selector)) {
    const popover = tab.querySelector?.(':scope > .session-popover') || tab.__yolomuxDetachedPopover;
    if (typeof popoverLifecycleActive === 'function' && typeof popoverStillActive === 'function') {
      if (popoverLifecycleActive(tab, popover) || popoverStillActive(tab, popover)) return true;
    } else {
      return true;
    }
  }
  return false;
}

function scheduleDeferredTabberRefresh() {
  if (tabberRefreshDeferredTimer) clearTimeout(tabberRefreshDeferredTimer);
  const delay = Math.max(
    Number(popoverHideDelayMs) || 0,
    Number(tabPopoverShowDelayMs) || 0,
    Number(tabPopoverFollowDelayMs) || 0,
    160,
  );
  tabberRefreshDeferredTimer = setTimeout(() => {
    tabberRefreshDeferredTimer = null;
    if (fileExplorerMode === 'tabber') refreshTabberPanels();
  }, delay);
}

function refreshTabberPanels() {
  if (tabberSessionPopoverRefreshIsUnsafe()) {
    scheduleDeferredTabberRefresh();
    return;
  }
  if (tabberRefreshDeferredTimer) {
    clearTimeout(tabberRefreshDeferredTimer);
    tabberRefreshDeferredTimer = null;
  }
  for (const panel of document.querySelectorAll('.file-explorer-panel')) {
    const groups = panel.querySelector('[data-file-explorer-changes] .changes-groups');
    if (groups) renderTabberTree(groups);
  }
}

function tabberWindowLabelHtml(label, iconHtml, options = {}) {
  const text = String(label || '');
  const pid = Number(options.pid);
  const nameText = text;
  const pidText = Number.isFinite(pid) && pid > 0 ? ` (pid=${Math.floor(pid)})` : '';
  return `<span class="tabber-window-label">${stripTitleAttrs(iconHtml)}<span class="tabber-window-text">${esc(nameText)}</span>${pidText ? `<span class="tabber-window-pid">${esc(pidText)}</span>` : ''}</span>`;
}

function tabberSessionChromeHtml(data) {
  const classes = ['tabber-session-tab', 'session-popover-host', data.active === true ? 'active' : ''].filter(Boolean).join(' ');
  const session = String(data.session || '').trim();
  const info = transcriptMeta.sessions?.[session] || {};
  const state = sessionState(session, info);
  const auto = autoApproveStates.get(session)?.enabled === true;
  const agentKind = sessionAgentKind(session);
  const tabHtml = stripTitleAttrs(tmuxPaneTabHtml(session, info, state, auto));
  return `<span class="${classes}" data-tabber-session-chrome="shared">${tabHtml}${sessionPopoverHtml(session, info, agentKind, auto, state)}</span>`;
}

function bindTabberSessionChrome(row, session) {
  const tab = row?.querySelector?.('.tabber-session-tab');
  if (!tab || tab.dataset.tabberChromeBound === 'true') return;
  tab.dataset.tabberChromeBound = 'true';
  const info = transcriptMeta.sessions?.[session] || {};
  const state = sessionState(session, info);
  applySessionStateClasses(tab, state);
  bindPaneTabPopover(tab, session);
  tab.addEventListener('pointerdown', event => {
    const autoTarget = event.target.closest?.('[data-auto-session]');
    if (!autoTarget) return;
    event.preventDefault();
    event.stopPropagation();
    if (session === currentSessionActionTarget()) setFocusedPanelItem(session);
  });
  bindActionDispatcher(tab, {
    'pane-tab-auto-approve': async (_event, autoTarget) => {
      await toggleAutoApprove(autoTarget.dataset.autoSession);
      if (session === currentSessionActionTarget()) focusPanel(session);
    },
  });
}

// Shared-pipeline row updater for Tabber nodes (same .file-tree-row DOM + updateFileTreeRowContents).
function updateTabberRow(row, fullPath, entry, depth, options = {}) {
  const data = entry.tabber || {};
  const expandable = entry.kind === 'dir';
  const collapsedSet = options.collapsedSet instanceof Set ? options.collapsedSet : fileExplorerTabberCollapsed;
  const expanded = expandable && !collapsedSet.has(fullPath);
  syncFileTreeRowKindClass(row, entry.kind);
  setRowDataset(row, 'path', fullPath);
  setRowDataset(row, 'kind', entry.kind);
  setRowDataset(row, 'name', entry.name);
  setRowDataset(row, 'tabberType', data.type || '');
  setRowDataset(row, 'tabberSession', data.session || '');
  setRowDataset(row, 'tabberWindow', data.windowIndex !== null && data.windowIndex !== undefined ? data.windowIndex : '');
  setRowDataset(row, 'tabberRepoRoot', data.repoRoot || '');
  setRowDataset(row, 'tabberItem', data.item || '');
  setRowDataset(row, 'tabberBranch', data.branchText || '');
  setRowDataset(row, 'openChangeFile', '');
  setRowDataset(row, 'openChangeSession', '');
  setRowDataset(row, 'openChangeStatus', '');
  setRowDataset(row, 'openChangeRepo', '');
  setRowDataset(row, 'openChangeDirectory', '');
  setRowDataset(row, 'changesFolderToggle', '');
  setRowDataset(row, 'changeRel', '');
  setRowDataset(row, 'changeSize', '');
  const paddingLeft = fileTreeRowPadding(depth);
  if (row.style.paddingLeft !== paddingLeft) row.style.paddingLeft = paddingLeft;
  const selected = tabberTreeSelectedPaths.has(fullPath);
  const current = data.current === true;
  setTreeItemAria(row, {selected, expandable, expanded});
  row.draggable = false;
  row.classList.toggle('selected', selected);
  row.classList.toggle('expanded', expanded);
  row.classList.toggle('collapsed', expandable && !expanded);
  row.classList.add('tabber-row');
  row.classList.toggle('tabber-active-window', data.type === 'window' && data.active === true);
  row.classList.toggle('tabber-active-session', data.type === 'session' && data.active === true);
  row.classList.toggle('tabber-status-long', data.type === 'window' && /^(working for|ASK\?)/.test(String(data.dateText || '')));
  row.classList.toggle('current-file', current && row.dataset.kind !== 'dir');
  row.classList.toggle('current-directory', current && row.dataset.kind === 'dir');
  if (current || (data.type === 'session' && data.active === true)) row.setAttribute('aria-current', 'true');
  else row.removeAttribute('aria-current');
  const icon = expandable ? (expanded ? '▾' : '▸') : (data.icon || '');
  const rawLabel = data.label || entry.name;
  const label = compactHomePath(rawLabel);
  const description = data.description ? compactHomePath(data.description) : '';
  const renderData = (label !== rawLabel || description !== (data.description || ''))
    ? {...data, label, description}
    : data;
  const titleParts = [
    label,
    description && description !== label ? description : '',
    data.branchText ? `branch: ${data.branchText}` : '',
    data.repoRoot && data.repoRoot !== rawLabel ? compactHomePath(data.repoRoot) : '',
  ].filter(Boolean);
  const titleText = titleParts.join('\n');
  if (titleText) row.dataset.tabberTitle = titleText;
  else delete row.dataset.tabberTitle;
  row.removeAttribute('title');
  const windowAgentIconHtml = data.type === 'window' && ['claude', 'codex'].includes(data.agentKey)
    ? (data.activityIconHtml || agentIcon(data.agentKey, {label: agentLabel(data.agentKey)}))
    : '';
  const nameHtml = data.type === 'session'
    ? tabberSessionChromeHtml(renderData)
    : data.type === 'window'
      ? tabberWindowLabelHtml(label, windowAgentIconHtml, {active: data.active === true, pid: data.pid})
    : data.type === 'loading'
      ? `<span class="tabber-loading-label">${esc(data.label || 'Fetching')}</span>${movingEllipsisHtml('tabber-loading-dots')}`
    : '';
  updateFileTreeRowContents(row, icon, label, {
    iconClass: 'tabber-icon',
    nameHtml,
    dateText: data.dateText || (entry.mtime ? fileTreeMtimeText(entry) : ''),
    dateHtml: data.dateHtml || '',
  });
  if (data.type === 'session' && data.session) bindTabberSessionChrome(row, data.session);
  applyFileTreeRowRecency(row, entry, options);
  // Tabber rows use delegation (bindTabberPanel) like the Differ; clear any stale Finder per-row handlers.
  clearFileTreeRowHandlers(row);
  return fullPath;
}

function toggleTabberCollapsed(fullPath) {
  if (fileExplorerTabberCollapsed.has(fullPath)) fileExplorerTabberCollapsed.delete(fullPath);
  else fileExplorerTabberCollapsed.add(fullPath);
  persistTabberCollapsed();
  scheduleShareUiStatePublish();
}

function expandTabberPath(fullPath) {
  if (!fileExplorerTabberCollapsed.has(fullPath)) return;
  fileExplorerTabberCollapsed.delete(fullPath);
  persistTabberCollapsed();
  scheduleShareUiStatePublish();
}

// Expand/collapse ALL Tabber nodes (the toolbar Expand all / Collapse all). Collapse-all records every
// current dir node path; expand-all clears the collapsed set.
function setAllTabberCollapsed(collapsed) {
  if (!collapsed) {
    fileExplorerTabberCollapsed.clear();
  } else {
    const {entries, entriesByDir} = buildTabberTree();
    const walk = (list, parent) => {
      for (const entry of list || []) {
        if (entry.kind !== 'dir') continue;
        const path = parent === '/' ? `/${entry.name}` : `${parent}/${entry.name}`;
        fileExplorerTabberCollapsed.add(path);
        walk(entriesByDir.get(normalizeDirectoryPath(path)), path);
      }
    };
    walk(entries, '/');
  }
  persistTabberCollapsed();
  refreshTabberPanels();
  scheduleShareUiStatePublish();
}

function tabberSessionPath(session) {
  const value = String(session || '').trim();
  return value ? `/s_${tabberPathToken(value)}` : '';
}

function tabberWindowPath(session, windowIndex) {
  const sessionPath = tabberSessionPath(session);
  const index = tmuxWindowNumber(windowIndex);
  return sessionPath && index !== null ? `${sessionPath}/w_${tabberPathToken(index)}` : '';
}

function activeTabberRowPath() {
  const session = currentSessionActionTarget();
  if (!session) return '';
  const info = transcriptMeta.sessions?.[session] || {};
  const override = tmuxWindowDisplayActiveIndex(session);
  if (override !== undefined) {
    return override === tmuxWindowPendingActiveIndex ? tabberSessionPath(session) : tabberWindowPath(session, override);
  }
  const activeWindow = tmuxWindowRecords(info.panes).find(record => record.active === true);
  return activeWindow ? tabberWindowPath(session, activeWindow.index) : tabberSessionPath(session);
}

function tabberSessionForNumericKey(key) {
  const value = String(key || '').trim();
  if (!/^[1-9]$/.test(value)) return '';
  return tabberOrderedSessions().includes(value) ? value : '';
}

function openTabberSession(session, options = {}) {
  const target = String(session || '').trim();
  if (!target) return false;
  const sessionPath = tabberSessionPath(target);
  if (sessionPath && fileExplorerTabberCollapsed.has(sessionPath)) {
    fileExplorerTabberCollapsed.delete(sessionPath);
    persistTabberCollapsed();
    if (options.refresh !== false) refreshTabberPanels();
    scheduleShareUiStatePublish();
  }
  selectSession(target, {userInitiated: true});
  return true;
}

// Delegated activation for Tabber rows. Clicking the disclosure icon toggles a node; clicking the row body
// acts: session -> open the session's tab; window -> open the tab + switch the tmux window; repo root ->
// point the Finder at it + open the tab + switch the window.
function handleTabberRowActivate(row, event) {
  const fullPath = row.dataset.path;
  const type = row.dataset.tabberType;
  const session = row.dataset.tabberSession || '';
  const windowIndex = row.dataset.tabberWindow !== undefined ? tmuxWindowNumber(row.dataset.tabberWindow) : null;
  const onDisclosure = Boolean(event?.target?.closest?.('.file-tree-icon'));
  if (row.dataset.kind === 'dir' && fullPath && onDisclosure) {
    toggleTabberCollapsed(fullPath);
    refreshTabberPanels();
    return;
  }
  const switchWindow = () => {
    if (session && windowIndex !== null) {
      tmuxWindow(session, {windowIndex}, row.querySelector('.file-tree-name')?.textContent || session);
      return true;
    }
    return false;
  };
  if (type === 'tab' && row.dataset.tabberItem) {
    if (row.dataset.tabberItem === infoItemId) openInfoSubTab('info');
    else selectSession(row.dataset.tabberItem, {userInitiated: true});
  } else if (type === 'session' && session) {
    openTabberSession(session);
  } else if (type === 'window' && session) {
    switchWindow();
    selectSession(session, {userInitiated: true});
  } else if (type === 'repo' && row.dataset.tabberRepoRoot) {
    switchWindow();
    setFileExplorerMode('files');
    openFileExplorerManualRoot(row.dataset.tabberRepoRoot);
    if (session) selectSession(session, {userInitiated: true});
  } else if (row.dataset.kind === 'dir' && fullPath) {
    toggleTabberCollapsed(fullPath);
    refreshTabberPanels();
  }
}

const tabberTreeInteractionController = createSharedTreeInteractionController({
  name: 'tabber',
  rowSelector: '.file-tree-row[data-tabber-type]',
  preserveExistingAriaCurrent: true,
  isRowSelectable: row => Boolean(row?.dataset?.tabberType && row.dataset.tabberType !== 'loading'),
  selectedIds: () => tabberTreeSelectedPaths,
  getLeadId: () => tabberTreeSelectionLead,
  setLeadId: id => { tabberTreeSelectionLead = id; },
  currentRowId: activeTabberRowPath,
  syncCurrentSelection(currentId) {
    tabberTreeSelectedPaths.clear();
    if (currentId) tabberTreeSelectedPaths.add(currentId);
  },
  isExpanded: row => row?.getAttribute?.('aria-expanded') === 'true',
  setExpanded(row, expanded) {
    const fullPath = row?.dataset?.path || '';
    if (!fullPath || row?.dataset?.kind !== 'dir') return;
    if (expanded) fileExplorerTabberCollapsed.delete(fullPath);
    else fileExplorerTabberCollapsed.add(fullPath);
    persistTabberCollapsed();
    refreshTabberPanels();
    scheduleShareUiStatePublish();
  },
  activateRow(row, event) {
    handleTabberRowActivate(row, event);
    syncTabberTreeActiveSelection(document, {scrollIntoView: true});
  },
});

function syncTabberTreeActiveSelection(panel = document, options = {}) {
  return tabberTreeInteractionController.syncCurrent(panel, options);
}

function bindTabberPanel(panel) {
  if (!panel || panel.dataset.tabberBound === 'true') return;
  panel.dataset.tabberBound = 'true';
  panel.addEventListener('click', event => {
    if (fileExplorerMode !== 'tabber') return;
    const row = event.target.closest?.('.file-tree-row[data-tabber-type]');
    if (!row || !panel.contains(row)) return;
    tabberTreeInteractionController.handleClick(event, panel, {row});
  });
  panel.addEventListener('keydown', event => {
    if (fileExplorerMode !== 'tabber') return;
    if (tabberTreeInteractionController.handleKeydown(event, panel)) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    const session = tabberSessionForNumericKey(event.key);
    if (!session) return;
    if (!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)) return;
    event.preventDefault();
    event.stopPropagation();
    openTabberSession(session);
  });
  panel.addEventListener('change', event => {
    if (fileExplorerMode !== 'tabber') return;
    const lookback = event.target.closest?.('[data-tabber-lookback]');
    if (!lookback || !panel.contains(lookback)) return;
    setTabberSessionFileLookbackHours(lookback.value);
  });
  panel.addEventListener('contextmenu', event => {
    if (fileExplorerMode !== 'tabber') return;
    const tabRow = event.target.closest?.('.file-tree-row[data-tabber-type="session"], .file-tree-row[data-tabber-type="window"], .file-tree-row[data-tabber-type="tab"]');
    const tabItem = tabRow?.dataset.tabberType === 'tab' ? tabRow.dataset.tabberItem : tabRow?.dataset.tabberSession;
    if (tabRow && panel.contains(tabRow) && tabItem && (isPinnableTab(tabItem) || isTmuxSession(tabItem))) {
      event.preventDefault();
      event.stopPropagation();
      showTabContextMenu(tabItem, event.clientX, event.clientY, {tab: tabRow.querySelector?.('.tabber-session-tab') || tabRow});
      return;
    }
    const row = event.target.closest?.('.file-tree-row[data-tabber-type="repo"]');
    const abs = row?.dataset.tabberRepoRoot;
    if (!row || !panel.contains(row) || !abs) return;
    event.preventDefault();
    event.stopPropagation();
    showFileTreeContextMenu(row, abs, {name: basenameOf(abs), kind: 'dir'}, event.clientX, event.clientY);
  });
}

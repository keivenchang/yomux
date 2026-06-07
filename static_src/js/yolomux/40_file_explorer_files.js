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
  pruneFileExplorerSelectionForRoot(fileExplorerRoot);
  fileExplorerManualSelectionActive = options.manualSelection === true;
  cancelPendingFileExplorerActiveSync();
  setFileExplorerPathDisplay(fileExplorerRoot);
  renderFileExplorerRootModeControls();
  fileExplorerExpanded.clear();
  if (fileExplorerTree) {
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

async function saveFileExplorerRootMode(mode) {
  if (readOnlyMode) return;
  try {
    await saveSettingsPatch(settingPatch('file_explorer.root_mode', mode));
  } catch (_) {}
}

async function fetchDirectory(path, options = {}) {
  const root = normalizeDirectoryPath(path);
  try {
    hydrateFileExplorerRepoInfoCache();
    const payload = await apiFetchJson(`/api/fs/list?path=${encodeURIComponent(root)}`);
    const entries = payload.entries || [];
    fileExplorerPathError = '';
    fileExplorerLastListError = null;
    cacheFileExplorerRepoInfoEntries(root, entries);
    markNewDirectoryEntries(root, entries);
    if (options.recordSignature !== false) recordDirectorySignature(root, entries);
    return entries;
  } catch (err) {
    const status = Number(err?.status) || 0;
    fileExplorerPathError = status
      ? err.message || `Cannot open ${root} (${status})`
      : `Cannot open ${root}: ${err}`;
    fileExplorerLastListError = {path: root, status, error: fileExplorerPathError, network: !status};
    console.warn(status ? 'fs list failed' : 'fs list error', root, status || err, fileExplorerPathError);
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
  }
  if (!fileExplorerSelectedPaths.size) fileExplorerSelectionAnchor = null;
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
  syncPressedButton(button, fileExplorerRootMode === 'sync', {labelOn: label, labelOff: label});
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
  renderQuickAccessInto(fileExplorerQuickAccess);
  document.querySelectorAll('.file-explorer-quick-access-panel').forEach(renderQuickAccessInto);
}

function setFileExplorerRootMode(mode, options = {}) {
  fileExplorerRootMode = mode === 'sync' ? 'sync' : 'fixed';
  writeStoredFileExplorerRootMode(fileExplorerRootMode);
  renderFileExplorerRootModeControls();
  updateFileExplorerSessionHighlightRows();
  if (options.persist === true) saveFileExplorerRootMode(fileExplorerRootMode);
  if (fileExplorerRootMode === 'sync' && options.sync !== false) {
    scheduleFileExplorerActiveTabSync(fileExplorerExplicitSyncSessionTarget(), {explicit: true});
  }
}

function fileExplorerRootModeValue() {
  return fileExplorerRootMode;
}

function toggleFileExplorerRootMode() {
  const mode = fileExplorerRootMode === 'sync' ? 'fixed' : 'sync';
  setFileExplorerRootMode(mode, {persist: true});
}

function setFileExplorerManualRootMode() {
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

function rememberFileExplorerExplicitSyncSession(session) {
  if (!isTmuxSession(session) || !activeSessions.includes(session)) return false;
  fileExplorerExplicitSyncSession = session;
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

function fileExplorerSyncPlanSignature(plan) {
  if (!plan?.root) return '';
  return [plan.session || '', plan.root, ...(plan.expandPaths || [])].join('\x1f');
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
  row.classList.toggle('file-tree-row--sync-expanded', highlightClass === 'file-tree-row--sync-expanded');
  row.classList.toggle('file-tree-row--session-repo', highlightClass === 'file-tree-row--session-repo');
  row.classList.toggle('file-tree-row--session-touched', highlightClass === 'file-tree-row--session-touched');
}

function updateFileExplorerSessionHighlightRows(preferredItem = null) {
  const sets = fileExplorerSessionHighlightSets(preferredItem);
  for (const row of document.querySelectorAll('.file-tree-row[data-path]')) {
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
    document.body.appendChild(node);
  }
  return node;
}

let fileTreeRepoPopoverTimer = null;
let fileTreeRepoPopoverHoverToken = 0;

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
  const rect = row?.getBoundingClientRect?.();
  if (rect) {
    // DOIT.6 #87: clamp to the viewport so the popover stays fully on-screen near the right/bottom edge
    // (it was placed at the row's left/bottom with no clamp and overflowed off-screen).
    const popRect = node.getBoundingClientRect?.();
    const pos = clampToViewport(Math.floor(rect.left), Math.ceil(rect.bottom + 4), Math.ceil(popRect?.width || 0), Math.ceil(popRect?.height || 0));
    node.style.left = `${Math.round(pos.left)}px`;
    node.style.top = `${Math.round(pos.top)}px`;
  }
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
  const explicit = options.explicit === true;
  if (fileExplorerManualSelectionActive && !explicit) return;
  if (explicit) fileExplorerManualSelectionActive = false;
  if (options.explicit === true && isTmuxSession(preferredItem)) rememberFileExplorerExplicitSyncSession(preferredItem);
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const syncItem = isTmuxSession(preferredItem) ? preferredItem : explicitSession;
  if (!syncItem || syncItem !== explicitSession) return;
  const syncPlan = fileExplorerSyncPlan(syncItem);
  const expandPaths = fileExplorerSyncExpansionPaths(syncPlan);
  const syncSignature = fileExplorerSyncPlanSignature(syncPlan);
  if (
    syncPlan.root
    && (syncPlan.root !== currentFileExplorerRoot() || expandPaths.length)
    && fileExplorerSyncPathInFlight !== syncSignature
  ) {
    const interactionGeneration = fileExplorerInteractionGeneration;
    requestAnimationFrame(() => {
      if (interactionGeneration !== fileExplorerInteractionGeneration) return;
      syncFileExplorerRootToActiveTmux(syncItem).catch(error => {
        console.warn('Finder root sync failed', error);
      });
    });
    return;
  }
}

function cancelPendingFileExplorerActiveSync() {
  fileExplorerInteractionGeneration += 1;
}

async function syncFileExplorerRootToActiveTmux(preferredItem = null) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  const plan = fileExplorerSyncPlan(preferredItem);
  const signature = fileExplorerSyncPlanSignature(plan);
  const expandPaths = fileExplorerSyncExpansionPaths(plan);
  if (!plan.root || fileExplorerSyncPathInFlight === signature) return false;
  fileExplorerSyncPathInFlight = signature;
  try {
    let changed = false;
    const previousTargetKey = fileExplorerSyncTargetKey(fileExplorerVisibleSyncSession, fileExplorerVisibleSyncRoot);
    const nextTargetKey = fileExplorerSyncTargetKey(plan.session, plan.root);
    if (nextTargetKey && nextTargetKey !== previousTargetKey) {
      rememberFileExplorerSyncExpandedState();
    }
    if (plan.root !== currentFileExplorerRoot()) {
      changed = await openFileExplorerAt(plan.root, {preserveExpanded: false, preserveScroll: false});
      if (!changed) return false;
    }
    const rememberedExpandedPaths = rememberedFileExplorerSyncExpandedPaths(plan.session, plan.root);
    if (rememberedExpandedPaths.length) {
      changed = await restoreFileExplorerExpandedPaths(rememberedExpandedPaths, plan.root) || changed;
    }
    if (expandPaths.length) {
      const generation = ++fileExplorerSyncGeneration;
      for (const path of expandPaths) {
        if (generation !== fileExplorerSyncGeneration) return changed;
        changed = await expandFileExplorerTreesToPath(path, plan.root, generation, {scrollIntoView: false, auto: true}) || changed;
      }
    }
    setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    rememberFileExplorerSyncExpandedState(plan.session, plan.root);
    updateFileExplorerSessionHighlightRows(preferredItem);
    return changed;
  } finally {
    if (fileExplorerSyncPathInFlight === signature) fileExplorerSyncPathInFlight = '';
  }
}

async function syncFileExplorerToActiveTab(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen()) return false;
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
    const absPath = sessionFileAbsolutePath(file);
    if (!absPath) continue;
    const agents = sessionFileAgentKinds(file);
    let dir = normalizeDirectoryPath(dirnameOf(absPath));
    while (dir && dir !== '/') {
      const key = `${dir}\x1f${absPath}`;
      const current = stats.get(dir) || {count: 0, agents: [], mtime: 0};
      if (!seen.has(key)) {
        seen.add(key);
        current.count += 1;
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

function fileTreeGitStatus(path) {
  return String(fileTreeChangedFile(path)?.status || '').toUpperCase();
}

function fileTreeNumstatText(path) {
  const match = fileTreeChangedFile(path);
  if (!match) return '';
  const added = Number(match.added || 0);
  const removed = Number(match.removed || 0);
  if (!Number.isFinite(added) || !Number.isFinite(removed)) return '';
  if (added === 0 && removed === 0) return '';
  return ` (+${added}/-${removed})`;
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

function fileTreeRepoNumstat(path) {
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
  if (!Number.isFinite(added) || !Number.isFinite(removed)) return '';
  if (added === 0 && removed === 0) return '';
  return `+${added}/-${removed}`;
}

function fileTreeRepoBranchIsNonMain(path) {
  const branch = fileTreeRepoBranch(path);
  return Boolean(branch && !['main', 'master'].includes(branch));
}

function fileTreeDisplayParts(path, entry) {
  // DOIT.31: a symlink shows where it points inline — "name → target" (the raw link text, rel or abs).
  const linkTarget = entry.is_symlink === true && entry.symlink_target ? String(entry.symlink_target) : '';
  const linkSuffixText = linkTarget ? ` → ${linkTarget}` : '';
  const linkSuffixHtml = linkTarget ? ` <span class="file-tree-symlink-target">→ ${esc(linkTarget)}</span>` : '';
  if (entry.kind === 'dir' && entry.is_repo === true) {
    const branch = fileTreeRepoBranch(path);
    const numstat = fileTreeRepoNumstat(path);
    const sync = fileTreeRepoSyncMeta(path);
    const textParts = [branch, ...sync.map(part => part.text), numstat].filter(Boolean);
    const htmlParts = [];
    if (branch) htmlParts.push(`<span class="file-tree-repo-branch">${esc(branch)}</span>`);
    for (const part of sync) htmlParts.push(`<span class="${part.cls}">${esc(part.text)}</span>`);
    if (numstat) htmlParts.push(`<span class="file-tree-repo-delta">${esc(numstat)}</span>`);
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
  return sessionFileDisplayTimeText(entry?.mtime);
}

function sortedFileTreeEntries(entries, sortMode = fileExplorerTreeSortMode) {
  const visible = entries.filter(entry => fileExplorerShowHidden || !entry.name.startsWith('.'));
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
    return String(left.name || '').localeCompare(String(right.name || ''), undefined, {numeric: true, sensitivity: 'base'}) * direction;
  });
}

function fileTreeGitStatusClass(status) {
  const key = String(status || '').toUpperCase();
  if (key === 'A' || key === 'U' || key === '?') return 'git-untracked';
  if (key === 'D') return 'git-deleted';
  if (key === 'S') return 'git-staged';
  if (key === 'M') return 'git-modified';
  if (key === 'T') return 'git-transcript';
  return '';
}

function fileTreeGitStatusBadgeClass(status) {
  return String(status || '').toUpperCase() === '?' ? 'file-tree-git-status-unknown' : '';
}

function gitStatusBadgeTitle(status) {
  const key = String(status || '').toUpperCase();
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
  if (IMAGE_EXTENSIONS.has(ext)) return 'file-icon-image';
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
  // Keep DOM order: icon → name → agent → dir-count → diff → status → date.
  if (name.nextElementSibling !== agent) row.insertBefore(agent, name.nextElementSibling);
  if (agent.nextElementSibling !== dirCount) row.insertBefore(dirCount, agent.nextElementSibling);
  if (dirCount.nextElementSibling !== diff) row.insertBefore(diff, dirCount.nextElementSibling);
  if (diff.nextElementSibling !== status) row.insertBefore(status, diff.nextElementSibling);
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
  const dirCountText = options.dirCountText || '';
  if (dirCount.textContent !== dirCountText) dirCount.textContent = dirCountText;
  setHiddenIfChanged(dirCount, !dirCountText);
  const diffParts = options.diffParts || [];
  const diffHtml = diffParts.map(p => `<span class="changes-diff-${esc(p.kind)}">${esc(p.text)}</span>`).join(' ');
  if (diff.innerHTML !== diffHtml) diff.innerHTML = diffHtml;
  setHiddenIfChanged(diff, !diffParts.length);
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
  const dateText = options.dateText || '';
  if (date.textContent !== dateText) date.textContent = dateText;
  setHiddenIfChanged(date, !dateText);
}

function updateFileTreeRow(row, parentPath, entry, depth, options = {}) {
  const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
  const differMode = options.differMode === true;
  const compact = options.compact === true;
  const currentDirectory = activeFinderDirectoryPath();
  const expanded = entry.kind === 'dir' && (differMode
    ? !changesFolderCollapsed.has(fullPath)
    : (options.autoExpand === true || fileExplorerExpanded.has(fullPath)));
  const indexedDirectory = !differMode && entry.kind === 'dir' && fileExplorerDirectoryIsIndexed(fullPath);
  const indexedDescendantDirectory = !differMode && entry.kind === 'dir' && !indexedDirectory && Boolean(fileExplorerIndexedAncestor(fullPath));
  syncFileTreeRowKindClass(row, entry.kind);
  row.classList.toggle('compact', compact);
  row.dataset.path = fullPath;
  row.dataset.kind = entry.kind;
  row.dataset.name = entry.name;
  row.dataset.isRepo = entry.is_repo === true ? 'true' : 'false';
  row.dataset.isSymlink = entry.is_symlink === true ? 'true' : 'false';
  if (entry.symlink_target) row.dataset.symlinkTarget = String(entry.symlink_target);
  else delete row.dataset.symlinkTarget;
  row.dataset.indexed = indexedDirectory ? 'true' : 'false';
  const paddingLeft = `${(compact ? 4 : 8) + depth * 14}px`;
  if (row.style.paddingLeft !== paddingLeft) row.style.paddingLeft = paddingLeft;
  row.setAttribute('role', 'treeitem');
  row.setAttribute('aria-selected', fileExplorerSelectedPaths.has(fullPath) ? 'true' : 'false');
  if (entry.kind === 'dir') row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  else row.removeAttribute('aria-expanded');
  row.draggable = entry.kind === 'file' || entry.kind === 'dir';
  row.classList.toggle('selected', fileExplorerSelectedPaths.has(fullPath));
  row.classList.toggle('expanded', expanded);
  row.classList.toggle('collapsed', entry.kind === 'dir' && !expanded);
  row.classList.toggle('is-repo', entry.kind === 'dir' && entry.is_repo === true);
  row.classList.toggle('repo-non-main', entry.kind === 'dir' && entry.is_repo === true && fileTreeRepoBranchIsNonMain(fullPath));
  row.classList.toggle('indexed-directory', indexedDirectory);
  row.classList.toggle('indexed-descendant-directory', indexedDescendantDirectory);
  const sessionHighlightClass = fileExplorerSessionHighlightClassForPath(fullPath, entry.kind, {
    differMode,
    sessionHighlightSets: options.sessionHighlightSets,
  });
  row.classList.toggle('file-tree-row--sync-expanded', sessionHighlightClass === 'file-tree-row--sync-expanded');
  row.classList.toggle('file-tree-row--session-repo', sessionHighlightClass === 'file-tree-row--session-repo');
  row.classList.toggle('file-tree-row--session-touched', sessionHighlightClass === 'file-tree-row--session-touched');
  const changedAncestor = !differMode && entry.kind === 'dir' && options.changedAncestorStats instanceof Map
    ? (options.changedAncestorStats.get(fullPath) || null)
    : null;
  row.classList.toggle('file-tree-row--changed-ancestor', Boolean(changedAncestor?.count));
  // DOIT.31: flag symlinks so the icon gets an arrow-badge overlay (target-type icon is kept); a broken
  // link gets a red badge + struck-through name. The backend sets is_symlink + kind=symlink-broken.
  row.classList.toggle('is-symlink', entry.is_symlink === true);
  row.classList.toggle('symlink-broken', entry.kind === 'symlink-broken');
  const changedFile = entry.kind === 'file'
    ? (options.sessionFilesMap ? (options.sessionFilesMap.get(fullPath) || null) : fileTreeChangedFile(fullPath))
    : null;
  const gitStatus = entry.kind === 'file'
    ? (options.sessionFilesMap ? (changedFile ? String(changedFile.status || 'M').toUpperCase() : '') : fileTreeGitStatus(fullPath))
    : (differMode ? '' : fileExplorerIndexBadgeText(fullPath));
  const gitStatusTitle = entry.kind === 'dir' && !differMode ? fileExplorerIndexBadgeTitle(fullPath) : gitStatusBadgeTitle(gitStatus);
  const gitClass = fileTreeGitStatusClass(gitStatus);
  for (const className of ['git-modified', 'git-untracked', 'git-deleted', 'git-staged', 'git-transcript']) {
    row.classList.toggle(className, className === gitClass);
  }
  if (!differMode && entry.kind === 'dir' && entry.is_repo === true) {
    row.removeAttribute('title');
    row.onmouseenter = () => scheduleRepoRowHoverPopover(row, fullPath);
    row.onmouseleave = () => hideFileTreeRepoPopover();
  } else if (!differMode) {
    cancelFileTreeRepoPopoverTimer();
    row.onmouseenter = null;
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
    const newEntry = fileExplorerEntryIsNew(fullPath);
    row.classList.toggle('new-entry', newEntry);
    if (newEntry) scheduleNewEntryClassRemoval(row, fullPath);
    const currentFile = entry.kind === 'file' && fullPath === activeFile;
    const currentDirectoryRow = entry.kind === 'dir' && fullPath === currentDirectory;
    row.classList.toggle('current-file', currentFile);
    row.classList.toggle('current-directory', currentDirectoryRow);
    if (currentFile || currentDirectoryRow) row.setAttribute('aria-current', 'true');
    else row.removeAttribute('aria-current');
  }
  const icon = entry.kind === 'dir' ? (expanded ? '▾' : '▸') : (entry.kind === 'file' ? fileIconFor(entry.name) : '·');
  const displayName = differMode ? {text: entry.name, html: null} : fileTreeDisplayParts(fullPath, entry);
  const dirCountText = entry.kind === 'dir'
    ? (differMode
      ? String(countChangedFilesInDir(fullPath, options.entriesByDir, options.sessionFilesMap) || '')
      : (changedAncestor?.count ? String(changedAncestor.count) : ''))
    : '';
  // Set data attributes so Differ event delegation (click/drag/contextmenu) can find these rows
  if (changedFile?.abs_path) {
    row.dataset.openChangeFile = changedFile.abs_path;
    row.dataset.openChangeSession = changedFile.session || '';
    row.dataset.openChangeStatus = (changedFile.status || 'M').toUpperCase();
    row.dataset.changeRel = changedFile.path || '';
    if (changedFile.repo) row.dataset.openChangeRepo = changedFile.repo;
    else delete row.dataset.openChangeRepo;
    if (changedFile.size !== null && changedFile.size !== undefined) row.dataset.changeSize = String(changedFile.size);
    else delete row.dataset.changeSize;
  } else {
    delete row.dataset.openChangeFile;
    delete row.dataset.openChangeSession;
    delete row.dataset.openChangeStatus;
    delete row.dataset.changeRel;
    delete row.dataset.openChangeRepo;
    delete row.dataset.changeSize;
  }
  if (differMode && entry.kind === 'dir') {
    const repoRoot = options.repoForDiffer || '';
    const relDir = repoRoot && fullPath.startsWith(repoRoot + '/') ? fullPath.slice(repoRoot.length + 1) : '';
    row.dataset.changesFolderToggle = fullPath;
    row.dataset.openChangeDirectory = fullPath;
    row.dataset.changeRel = relDir;
  } else if (!differMode) {
    delete row.dataset.changesFolderToggle;
    delete row.dataset.openChangeDirectory;
  }
  updateFileTreeRowContents(row, icon, displayName.text, {
    gitStatus,
    gitStatusTitle,
    iconClass: [fileIconClassFor(entry.name, entry.kind), indexedDirectory ? 'file-icon-dir-indexed' : ''].filter(Boolean).join(' '),
    nameHtml: differMode ? null : displayName.html,
    dateText: fileTreeMtimeText(entry),
    diffParts: changedFile ? sessionFileDiffText(changedFile) : [],
    agentHtml: changedFile ? changeFileAgentsHtml(changedFile) : (changedAncestor ? changeFileAgentsHtml(changedAncestor) : ''),
    dirCountText,
  });
  if (entry.kind === 'file' && IMAGE_EXTENSIONS.has(fileExtensionOf(entry.name)) && Number(entry.size || 0) <= MAX_FILE_PREVIEW_BYTES) {
    bindFileImagePreview(row, fullPath, entry);
  }
  if (differMode) {
    // In differMode, event handling is via delegation in bindChangesPanel; clear Finder handlers
    row.onpointerdown = null;
    row.onpointerup = null;
    row.onclick = null;
    row.ondblclick = null;
    row.oncontextmenu = null;
    row.ondragstart = null;
    row.ondragend = null;
  } else {
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
  const visible = sortedFileTreeEntries(entries, renderOptions.treeSortMode);
  const existingRows = new Map(fileTreeDirectRows(container).map(row => [row.dataset.path, row]));
  const nextNodes = [];
  for (const entry of visible) {
    const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
    const row = existingRows.get(fullPath) || document.createElement('div');
    updateFileTreeRow(row, parentPath, entry, depth, renderOptions);
    nextNodes.push(row);
    const isDifferDir = renderOptions.differMode === true && entry.kind === 'dir';
    if (entry.kind === 'dir' && (isDifferDir ? !changesFolderCollapsed.has(fullPath) : (renderOptions.autoExpand === true || fileExplorerExpanded.has(fullPath)))) {
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
  document.body.appendChild(popover);
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

function updateFileTreeGitStatusRows() {
  const changedAncestorStats = fileTreeChangedAncestorStats();
  document.querySelectorAll('.file-tree-row[data-path]').forEach(row => {
    const gitStatus = row.dataset.kind === 'file' ? fileTreeGitStatus(row.dataset.path) : '';
    const gitClass = fileTreeGitStatusClass(gitStatus);
    for (const className of ['git-modified', 'git-untracked', 'git-deleted', 'git-staged', 'git-transcript']) {
      row.classList.toggle(className, className === gitClass);
    }
    const status = row.querySelector(':scope > .file-tree-git-status');
    if (status) {
      setClassNameIfChanged(status, ['file-tree-git-status', fileTreeGitStatusBadgeClass(gitStatus)].filter(Boolean).join(' '));
      status.textContent = gitStatus;
      const title = gitStatusBadgeTitle(gitStatus);
      if (title) {
        status.setAttribute('title', title);
        status.setAttribute('aria-label', title);
      } else {
        status.removeAttribute('title');
        status.removeAttribute('aria-label');
      }
      status.hidden = !gitStatus;
    }
    const entry = {
      kind: row.dataset.kind,
      name: row.dataset.name || basenameOf(row.dataset.path),
      is_repo: row.dataset.isRepo === 'true',
      is_symlink: row.dataset.isSymlink === 'true',
      symlink_target: row.dataset.symlinkTarget || '',
    };
    row.classList.toggle('repo-non-main', entry.kind === 'dir' && entry.is_repo === true && fileTreeRepoBranchIsNonMain(row.dataset.path));
    const changedAncestor = entry.kind === 'dir' ? (changedAncestorStats.get(row.dataset.path) || null) : null;
    row.classList.toggle('file-tree-row--changed-ancestor', Boolean(changedAncestor?.count));
    const changedFile = entry.kind === 'file' ? fileTreeChangedFile(row.dataset.path) : null;
    const agentHtml = changedFile ? changeFileAgentsHtml(changedFile) : (changedAncestor ? changeFileAgentsHtml(changedAncestor) : '');
    const agent = row.querySelector(':scope > .file-tree-agent');
    if (agent) {
      if (agent.innerHTML !== agentHtml) agent.innerHTML = agentHtml;
      agent.hidden = !agentHtml;
    }
    row.classList.toggle('has-agent', Boolean(agentHtml));
    const dirCount = row.querySelector(':scope > .file-tree-dir-count');
    if (dirCount) {
      const dirCountText = changedAncestor?.count ? String(changedAncestor.count) : '';
      dirCount.textContent = dirCountText;
      dirCount.hidden = !dirCountText;
    }
    const name = row.querySelector(':scope > .file-tree-name');
    const nextName = fileTreeDisplayParts(row.dataset.path, entry);
    if (name && name.textContent !== nextName.text) {
      if (nextName.html) name.innerHTML = nextName.html;
      if (!name.children?.length) name.textContent = nextName.text;
    }
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
  updateFileExplorerCurrentFileHighlight();
}

function selectFileTreeRange(row, fullPath, options = {}) {
  const rows = selectableFileTreeRows(row.closest('[role="tree"]') || document);
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
  clearTimeout(fileIndexStatusTimers.get(normalized));
  fileIndexStatusTimers.delete(normalized);
  if (status === 'building') {
    fileIndexStatusTimers.set(normalized, setTimeout(() => refreshFileIndexStatus(normalized), 1500));
  }
  if (previous !== status) updateFileExplorerIndexedDirectoryRows();
}

// Lazily warm/poll an indexed root's status, without re-fetching once it is known ready or while a
// poll is already pending (so per-row render calls don't spam the endpoint).
function ensureFileIndexStatus(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return;
  if (fileExplorerIndexStatus.get(normalized) === 'ready' || fileIndexStatusTimers.has(normalized)) return;
  refreshFileIndexStatus(normalized);
}

function clearFileIndexStatus(root) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized) return;
  fileExplorerIndexStatus.delete(normalized);
  clearTimeout(fileIndexStatusTimers.get(normalized));
  fileIndexStatusTimers.delete(normalized);
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
  // with this page's whole set. Two browser origins (:7777 vs :7778) do NOT share localStorage, so a
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

function fileExplorerIndexedSearchRoots(defaultRoot = fileQuickOpenRootForSearch()) {
  const defaultPath = normalizeStoredFileExplorerIndexedDir(defaultRoot);
  const indexedRoots = fileExplorerIndexedRootList();
  const defaultCoveredByIndexed = defaultPath && indexedRoots.some(root => root !== defaultPath && pathIsInsideDirectory(defaultPath, root));
  const roots = defaultPath && !defaultCoveredByIndexed ? [defaultPath] : [];
  for (const indexedRoot of indexedRoots) {
    if (!roots.includes(indexedRoot)) roots.push(indexedRoot);
  }
  return roots;
}

async function onFileTreeRowClick(row, fullPath, entry, event) {
  closeFileImagePreview();
  const selectionOnly = updateFileTreeSelectionFromClick(row, fullPath, event);
  if (selectionOnly) return;
  if (entry.kind === 'dir') {
    if (fileExplorerExpanded.has(fullPath)) {
      collapseDirectoryRow(row, fullPath, {manual: true});
    } else {
      await expandDirectoryRow(row, fullPath, {manual: true});
    }
    return;
  }
  if (entry.kind === 'file') {
    updateFileExplorerCurrentFileHighlight();
  }
}

function startFileTreeDrag(event, row, fullPath, entry) {
  closeFileImagePreview();
  if (!fileExplorerSelectedPaths.has(fullPath)) selectFileTreePath(fullPath);
  const paths = fileTreeActionPaths(fullPath);
  const payloadObject = {path: fullPath, paths, kind: entry.kind, name: entry.name};
  dragFilePayloadState = normalizeFileDragPayload(payloadObject);
  const payload = JSON.stringify(payloadObject);
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('application/x-yolomux-file', payload);
  event.dataTransfer.setData('text/plain', paths.join('\n'));
  startFileDragPreview(event, paths, entry);
}

async function fetchFilePathInfo(path) {
  return apiFetchJson(`/api/fs/info?path=${encodeURIComponent(path)}`);
}

async function showFileTreeContextMenu(row, fullPath, entry, x, y) {
  closeFileContextMenu();
  closeTerminalContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
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
  appendContextMenuButton(menu, multiple ? 'Copy relative paths' : 'Copy relative path', () => copyFilePath(relativePaths.join('\n'), 'relative'), closeFileContextMenu, {disabled: menuState.copyRelativeDisabled});
  appendContextMenuButton(menu, multiple ? 'Copy full paths' : 'Copy full path', () => copyFilePath(selectedPaths.join('\n'), 'path'), closeFileContextMenu);
  appendContextMenuButton(menu, 'Open in new tab', () => openFileInEditor(fullPath, entry, {forceNewTab: true}), closeFileContextMenu, {disabled: menuState.openInNewTabDisabled});
  appendContextMenuButton(menu, 'Download', () => triggerFileDownload(fullPath), closeFileContextMenu, {disabled: menuState.downloadDisabled});
  appendContextMenuButton(menu, fileExplorerDirectoryIsIndexed(fullPath) ? 'Disallow index' : 'Allow index', () => toggleFileExplorerDirectoryIndexed(fullPath), closeFileContextMenu, {disabled: menuState.indexToggleDisabled, checked: entry?.kind === 'dir' ? fileExplorerDirectoryIsIndexed(fullPath) : undefined});
  appendContextMenuButton(menu, 'Rename', () => beginFileTreeRename(row, selectedPaths[0], entry), closeFileContextMenu, {disabled: menuState.renameDisabled});
  appendContextMenuButton(menu, multiple ? 'Delete selected' : 'Delete', () => deleteFileTreePath(fullPath, entry, selectedPaths), closeFileContextMenu, {disabled: menuState.deleteDisabled});
  fileContextMenu.open(menu, x, y);
}

function fileContextMenuState(entry, selectedPaths, relativePaths) {
  const multiple = selectedPaths.length > 1;
  return {
    copyRelativeDisabled: relativePaths.length !== selectedPaths.length,
    // DOIT.34 #2: readonly is terminal-only — the server forbids every /api/fs/* read (raw/list/...),
    // so the file-read affordances (open image in a tab via /api/fs/raw, Download via /api/fs/raw) are
    // disabled in readonly to match the server, instead of offering a command that 403s.
    openInNewTabDisabled: multiple || !entryIsImageFile(entry) || readOnlyMode,
    downloadDisabled: multiple || entry?.kind !== 'file' || readOnlyMode,
    indexToggleDisabled: multiple || entry?.kind !== 'dir' || (!fileExplorerDirectoryIsIndexed(selectedPaths[0]) && Boolean(fileExplorerIndexedAncestor(selectedPaths[0]))),
    renameDisabled: readOnlyMode || multiple,
    deleteDisabled: readOnlyMode,
  };
}

function entryIsImageFile(entry) {
  return entry?.kind === 'file' && IMAGE_EXTENSIONS.has(fileExtensionOf(entry.name || entry.path || ''));
}

async function copyFilePath(path, label) {
  const text = String(path || '');
  try {
    await copyTextToClipboard(text);
    statusEl.textContent = label === 'relative' ? 'copied relative path' : 'copied path';
  } catch (error) {
    statusErr(`copy failed: ${esc(error)}`);
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
  copyFilePath(fileExplorerRoot || homePath || '/', 'path');
}

function childNameToPath(root, name) {
  const trimmed = String(name || '').trim();
  if (!trimmed || /[/\x00\r\n]/.test(trimmed)) return '';
  return childPath(normalizeDirectoryPath(root), trimmed);
}

async function createFileExplorerFile() {
  if (readOnlyMode) {
    statusErr('readonly access cannot create files');
    return;
  }
  const name = window.prompt('New file name');
  const path = childNameToPath(currentFileExplorerRoot(), name);
  if (!path) return;
  try {
    await apiFetchJson('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, content: ''}),
    });
    statusEl.textContent = `created ${basenameOf(path)}`;
    await refreshFileExplorerTrees();
    await openFileInEditor(path, {name: basenameOf(path)});
  } catch (error) {
    statusErr(`new file failed: ${esc(error)}`);
  }
}

async function createFileExplorerFolder() {
  if (readOnlyMode) {
    statusErr('readonly access cannot create folders');
    return;
  }
  const name = window.prompt('New folder name');
  const path = childNameToPath(currentFileExplorerRoot(), name);
  if (!path) return;
  try {
    await apiFetchJson('/api/fs/mkdir', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path}),
    });
    statusEl.textContent = `created ${basenameOf(path)}`;
    await refreshFileExplorerTrees();
  } catch (error) {
    statusErr(`new folder failed: ${esc(error)}`);
  }
}

function collapseAllFileExplorerDirectories() {
  if (fileExplorerRootMode === 'sync') {
    const plan = fileExplorerSyncPlan(fileExplorerExplicitSyncSessionTarget());
    for (const path of fileExplorerSyncExpansionPaths(plan)) rememberFileExplorerSyncManualCollapse(path);
  }
  fileExplorerExpanded.clear();
  rememberFileExplorerSyncExpandedState();
  refreshFileExplorerTrees({preserveExpanded: false, preserveScroll: true});
}

function bindFileExplorerHeaderActions(container = document) {
  if (!container || container.dataset?.fileExplorerHeaderActionsBound === 'true') return;
  if (container.dataset) container.dataset.fileExplorerHeaderActionsBound = 'true';
  container.addEventListener('click', event => {
    const action = event.target.closest('[data-file-explorer-new-file], [data-file-explorer-new-folder], [data-file-explorer-refresh], [data-file-explorer-collapse], [data-file-explorer-tree-dates]');
    if (!action || !container.contains(action)) return;
    event.preventDefault();
    event.stopPropagation();
    if (action.matches('[data-file-explorer-new-file]')) createFileExplorerFile();
    else if (action.matches('[data-file-explorer-new-folder]')) createFileExplorerFolder();
    else if (action.matches('[data-file-explorer-refresh]')) {
      if (fileExplorerMode === 'diff') fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), force: true});
      else refreshFileExplorerTrees();
    } else if (action.matches('[data-file-explorer-collapse]')) {
      collapseAllFileExplorerDirectories();
    }
    else if (action.matches('[data-file-explorer-tree-dates]')) {
      cycleFileExplorerTreeDateMode();
    }
  });
  container.addEventListener('change', event => {
    const select = event.target.closest('[data-file-explorer-tree-sort]');
    if (!select || !container.contains(select)) return;
    event.preventDefault();
    event.stopPropagation();
    fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(select.value) ? select.value : 'az';
    writeStoredFileExplorerTreeSortMode(fileExplorerTreeSortMode);
    refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  });
}

async function deleteFileTreePath(fullPath, entry, paths = null) {
  if (readOnlyMode) {
    statusErr('readonly access cannot delete files');
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
      await apiFetchJson('/api/fs/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path}),
      });
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
    statusErr(`delete failed: ${esc(error)}`);
  }
}

// C10: keyboard delete for the Finder tree. macOS Finder deletes the selection with Command-Delete (plain
// Delete is a text-edit key on Mac), so on a Mac UI require metaKey+Backspace/Delete; on PC/File Explorer a
// plain Delete works. Returns true once it has consumed the event so the global tab-close shortcut (which
// also binds Mod+Delete) does NOT also fire. Guarded against text/rename inputs, readonly, and no selection.
function handleFileExplorerDeleteShortcut(event) {
  const key = String(event.key || '').toLowerCase();
  if (key !== 'backspace' && key !== 'delete') return false;
  if (isMacPlatform()) {
    if (!(event.metaKey === true && event.ctrlKey !== true && event.altKey !== true)) return false;
  } else if (key !== 'delete' || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) {
    return false;
  }
  // Only when the Finder is the active surface and not while editing text (rename input, etc.).
  if (!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)) return false;
  if (!globalShortcutTargetAllowsAppAction(event.target)) return false;
  const paths = compactNestedPaths(Array.from(fileExplorerSelectedPaths));
  if (!paths.length) return false;
  event.preventDefault();
  event.stopPropagation();
  if (readOnlyMode) {
    statusErr('readonly access cannot delete files');
    return true;
  }
  const primary = paths[0];
  const row = document.querySelector(`.file-tree-row[data-path="${cssEscape(primary)}"]`);
  const entry = row
    ? {kind: row.dataset.kind, name: row.dataset.name || basenameOf(primary), is_repo: row.dataset.isRepo === 'true'}
    : {kind: 'file', name: basenameOf(primary)};
  deleteFileTreePath(primary, entry, paths);
  return true;
}

function beginFileTreeRename(row, fullPath, entry) {
  if (readOnlyMode) {
    statusErr('readonly access cannot rename files');
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
    statusErr('readonly access cannot rename files');
    return false;
  }
  const currentName = entry?.name || basenameOf(fullPath);
  const trimmed = newName.trim();
  if (!trimmed || trimmed === currentName) return false;
  try {
    const payload = await apiFetchJson('/api/fs/rename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: fullPath, new_name: trimmed}),
    });
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
    statusErr(`rename failed: ${esc(error)}`);
    return false;
  }
}

async function refreshFileExplorerTrees(options = {}) {
  const refreshOptions = {preserveExpanded: true, preserveScroll: true, ...options};
  if (await refreshFileExplorerTreesInPlace(refreshOptions)) return;
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

async function fileExplorerEntriesByWatchedDirectory(root = currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root);
  const entriesByDir = new Map();
  const directories = new Set([normalizedRoot]);
  for (const path of fileExplorerExpanded) {
    if (pathIsInsideDirectory(path, normalizedRoot) && path !== normalizedRoot) directories.add(normalizeDirectoryPath(path));
  }
  for (const directory of directories) {
    const entries = await fetchDirectory(directory);
    if (entries) entriesByDir.set(normalizeDirectoryPath(directory), entries);
  }
  return entriesByDir;
}

async function refreshFileExplorerTreesInPlace(options = {}) {
  const root = normalizeDirectoryPath(options.root || currentFileExplorerRoot());
  const entriesByDir = options.entriesByDir instanceof Map
    ? options.entriesByDir
    : await fileExplorerEntriesByWatchedDirectory(root);
  const rootEntries = Array.isArray(options.entries) ? options.entries : entriesByDir.get(root);
  if (!rootEntries) return false;
  const scrollPositions = options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  if (fileExplorerTree) {
    setFileExplorerPathDisplay(root);
    renderTreeChildren(fileExplorerTree, root, rootEntries, 0, {entriesByDir});
  }
  await refreshFileExplorerPanelTrees({
    ...options,
    root,
    entries: rootEntries,
    entriesByDir,
    restoreState: false,
  });
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
  updateFileExplorerCurrentFileHighlight();
  return true;
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

// DOIT.6 #133: resolve a relative href against a base dir, collapsing '.'/'..' segments. An absolute
// `rel` (leading '/') ignores the base. Used to open relative markdown-preview links in the editor.
function joinAndNormalize(base, rel) {
  const relStr = String(rel || '');
  const combined = relStr.startsWith('/') ? relStr : `${String(base || '/')}/${relStr}`;
  const isAbs = combined.startsWith('/');
  const out = [];
  for (const seg of combined.split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') { out.pop(); continue; }
    out.push(seg);
  }
  return (isAbs ? '/' : '') + out.join('/');
}

async function fetchFileEntry(path) {
  const result = await fetchFileEntryStatus(path);
  return result.entry;
}

async function fetchFileEntryStatus(path) {
  const entries = await fetchDirectory(dirnameOf(path));
  if (!Array.isArray(entries)) {
    const error = fileExplorerLastListError;
    return {
      entry: null,
      missing: error?.status === 404,
      error: error?.error || `Cannot inspect ${path}`,
      network: error?.network === true,
    };
  }
  const name = basenameOf(path);
  const entry = entries.find(entry => entry.name === name) || null;
  return {entry, missing: !entry, error: entry ? '' : `path not found: ${path}`, network: false};
}

function fileEntryMtime(entry) {
  return Number(entry?.mtime_ns || entry?.mtime || 0);
}

function filePayloadMtime(payload) {
  return Number(payload?.mtime_ns || payload?.mtime || 0);
}

function fileEntryChanged(state, entry) {
  if (!state || !entry) return true;
  const stateMtime = Number(state.mtime || 0);
  const entryMtime = fileEntryMtime(entry);
  if (stateMtime !== entryMtime) return true;
  // DOIT.6 #88: equal mtimes but an UNKNOWN size on either side — do NOT assert "unchanged" (an
  // equal-mtime content change with no size would be missed); treat it as changed so the caller re-stats.
  if (state.size == null || entry.size == null) return true;
  return Number(state.size) !== Number(entry.size);
}

function filePanelItemsForPath(path) {
  const items = [];
  if (sharedImageViewerPath === path) items.push(imageViewerItemFor(path));
  items.push(...fileEditorTabItemsForPath(path));
  items.push(...filePreviewTabItemsForPath(path));
  return items;
}

function openFilePathHasOwner(path) {
  return filePanelItemsForPath(path).length > 0;
}

function removeFilePanelOwner(path, item) {
  if (isImageViewerItem(item) && sharedImageViewerPath === path) sharedImageViewerPath = null;
  else if (isFilePreviewItem(item)) removeFilePreviewTabItem(path, item);
  else removeFileEditorTabItem(path, item);
  fileEditorViewModesForPath(path).delete(item);
  // DOIT.6 #73: also drop the per-item CodeMirror scroll/selection state and the LRU timestamp on close
  // so these item-keyed maps don't grow unbounded as editor tabs open and close.
  fileEditorViewState.delete(item);
  tabLastActivatedAt.delete(item);
  if (!openFilePathHasOwner(path)) fileStateFor(path)?.ownerSessions.clear();
}

function normalizedOpenFileOwnerSession(value) {
  const session = String(value || '').trim();
  return session && sessions.includes(session) ? session : '';
}

function rememberOpenFileOwner(path, ownerSession) {
  const session = normalizedOpenFileOwnerSession(ownerSession);
  if (!path || !session) return;
  ensureFileState(path)?.ownerSessions.add(session);
}

function openFileOwnerSessionsForPath(path) {
  return Array.from(fileStateFor(path)?.ownerSessions || [])
    .filter(session => sessions.includes(session))
    .sort((left, right) => sessions.indexOf(left) - sessions.indexOf(right) || left.localeCompare(right));
}

function removePanelForItem(item) {
  const panel = panelNodes.get(item);
  if (!panel) return;
  panel.remove();
  panelNodes.delete(item);
}

// A language switch must repaint the Finder's toolbar chrome (root/dates/sort/new-file… labels), which
// createFileExplorerPanel() bakes in at creation time and caches in panelNodes. Re-rendering only the
// panel BODIES (rerenderForLocale's other calls) leaves that chrome stale, and the toolbar mixes direct
// and delegated click handlers — so relabel-and-rebind would be fragile. Instead evict the cached Finder
// panel and let renderPanels() rebuild it from the single source of truth, then repopulate the tree and
// quick-access (state-bearing toggles read live globals / localStorage, so they survive the rebuild).
function relocalizeFileExplorerPanels() {
  if (!panelNodes.has(fileExplorerItemId)) return;
  removePanelForItem(fileExplorerItemId);
  renderPanels(activePaneItems());
  refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  renderFileExplorerQuickAccessControls();
}

function setOpenFileOwner(path, item, options = {}) {
  let replacementSlots = null;
  rememberOpenFileOwner(path, options.ownerSession);
  if (isImageViewerItem(item)) {
    replacementSlots = replaceSharedImageViewerPath(path);
  } else if (isFilePreviewItem(item)) {
    addFilePreviewTabItem(path, item);
  } else {
    addFileEditorTabItem(path, item);
  }
  syncFileLayoutItems();
  return replacementSlots;
}

function replaceSharedImageViewerPath(path) {
  if (!path || sharedImageViewerPath === path) {
    sharedImageViewerPath = path || sharedImageViewerPath;
    return null;
  }
  const previousPath = sharedImageViewerPath;
  const previousItem = previousPath ? imageViewerItemFor(previousPath) : null;
  const nextItem = imageViewerItemFor(path);
  const nextSlots = previousItem && itemInLayout(previousItem)
    ? layoutWithReplacedItem(previousItem, nextItem)
    : null;
  if (previousItem) {
    removePanelForItem(previousItem);
    sharedImageViewerPath = null;
    if (!openFilePathHasOwner(previousPath)) {
      deleteFileState(previousPath);
    }
  }
  sharedImageViewerPath = path;
  return nextSlots;
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

function missingFileState(message = 'file deleted or moved on disk') {
  const state = fileErrorState(message);
  state.externalMissing = true;
  return state;
}

function openFileIsMissing(path) {
  return openFiles.get(path)?.externalMissing === true;
}

function clearOpenFileExternalState(state) {
  if (!state) return state;
  delete state.externalChanged;
  delete state.externalMissing;
  delete state.externalError;
  delete state.externalChangeEditPrompted;
  return state;
}

function openFileStatus(state) {
  if (!state) return {message: '', level: ''};
  if (state.externalMissing) return {message: 'deleted on disk; unsaved edits kept', level: 'warn'};
  if (state.externalError) return {message: `refresh failed; file state unknown: ${state.externalError}`, level: 'warn'};
  if (state.externalChanged) return {message: state.dirty ? 'changed on disk; unsaved edits kept' : 'changed on disk; reload available', level: 'warn'};
  if (state.dirty) return {message: t('filetab.modified'), level: ''};
  if (state.kind === 'text') return {message: `${state.original.length} chars`, level: ''};
  return {message: '', level: ''};
}

function fileEditorAutosaveDelayMs() {
  const seconds = Number(fileEditorAutosaveDelaySeconds || 2.5);
  const clamped = Math.max(0.5, Math.min(60, Number.isFinite(seconds) ? seconds : 2.5));
  return Math.round(clamped * 1000);
}

function clearFileAutosaveTimer(path) {
  const timer = fileEditorAutosaveTimers.get(path);
  if (timer) clearTimeout(timer);
  fileEditorAutosaveTimers.delete(path);
}

function updateOpenFileDirtyFlag(path) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  state.dirty = state.content !== state.original;
  return state.dirty;
}

function syncOpenFileContentFromPanel(path, panel) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text' || !panel) return false;
  const cmContent = codeMirrorPanelContent(panel);
  if (cmContent === null) return false;
  state.content = cmContent;
  updateOpenFileDirtyFlag(path);
  return true;
}

function syncOpenFileContentFromPanels(path, preferredPanel = null) {
  if (syncOpenFileContentFromPanel(path, preferredPanel)) return true;
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel === preferredPanel) continue;
    if (syncOpenFileContentFromPanel(path, panel)) return true;
  }
  updateOpenFileDirtyFlag(path);
  return false;
}

function openFileAutosaveReady(path, state = openFiles.get(path)) {
  return !readOnlyMode
    && fileEditorAutosaveEnabled
    && state?.kind === 'text'
    && state.dirty
    && !state.externalChanged
    && !state.externalMissing
    && !state.externalError;
}

function scheduleFileAutosave(path) {
  clearFileAutosaveTimer(path);
  const state = openFiles.get(path);
  if (!openFileAutosaveReady(path, state)) return false;
  const timer = setTimeout(() => {
    fileEditorAutosaveTimers.delete(path);
    autoSaveFileEditor(path);
  }, fileEditorAutosaveDelayMs());
  fileEditorAutosaveTimers.set(path, timer);
  return true;
}

function rescheduleAllFileAutosaves() {
  for (const path of Array.from(fileEditorAutosaveTimers.keys())) clearFileAutosaveTimer(path);
  for (const [path, state] of openFiles.entries()) {
    if (openFileAutosaveReady(path, state)) scheduleFileAutosave(path);
  }
}

async function autoSaveFileEditor(path) {
  const state = openFiles.get(path);
  if (!openFileAutosaveReady(path, state)) return false;
  const panel = fileEditorPanelsForPath(path).find(candidate => candidate?._cmView) || fileEditorPanelsForPath(path)[0] || null;
  syncOpenFileContentFromPanels(path, panel);
  if (!openFileAutosaveReady(path, state)) return false;
  return saveFileEditor(path, panel, {autosave: true});
}

function truncateDialogText(text, maxChars = 20000) {
  const value = String(text || '');
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars)}\n\n... truncated ${value.length - maxChars} chars ...`;
}

function diffDialogLines(text) {
  const lines = String(text ?? '').split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines.length ? lines : [''];
}

function lineDiffRows(leftText, rightText) {
  const left = diffDialogLines(leftText);
  const right = diffDialogLines(rightText);
  const cellCount = left.length * right.length;
  if (cellCount > 1_000_000) {
    const max = Math.max(left.length, right.length);
    return Array.from({length: max}, (_, index) => {
      const same = left[index] === right[index];
      return {
        left: left[index] ?? '',
        right: right[index] ?? '',
        leftKind: same ? 'same' : (index < left.length ? 'added' : 'blank'),
        rightKind: same ? 'same' : (index < right.length ? 'removed' : 'blank'),
      };
    });
  }
  const dp = Array.from({length: left.length + 1}, () => new Uint16Array(right.length + 1));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({left: left[i], right: right[j], leftKind: 'same', rightKind: 'same'});
      i += 1;
      j += 1;
    } else if (j >= right.length || (i < left.length && dp[i + 1][j] >= dp[i][j + 1])) {
      rows.push({left: left[i], right: '', leftKind: 'added', rightKind: 'blank'});
      i += 1;
    } else {
      rows.push({left: '', right: right[j], leftKind: 'blank', rightKind: 'removed'});
      j += 1;
    }
  }
  return rows;
}

function fileCompareLineHtml(kind, text) {
  return `<span class="file-compare-line ${esc(kind)}">${esc(text || ' ')}</span>`;
}

function fileConflictCompareHtml(editorText, diskText) {
  const rows = lineDiffRows(truncateDialogText(editorText), truncateDialogText(diskText));
  const editorHtml = rows.map(row => fileCompareLineHtml(row.leftKind, row.left)).join('');
  const diskHtml = rows.map(row => fileCompareLineHtml(row.rightKind, row.right)).join('');
  return `
    <div class="file-editor-conflict-compare">
      <section>
        <h4>${esc(t('conflict.unsaved'))}</h4>
        <pre data-file-compare-scroll>${editorHtml}</pre>
      </section>
      <section>
        <h4>${esc(t('conflict.onDisk'))}</h4>
        <pre data-file-compare-scroll>${diskHtml}</pre>
      </section>
    </div>`;
}

function bindFileConflictCompareScroll(root) {
  const panes = Array.from(root?.querySelectorAll?.('[data-file-compare-scroll]') || []);
  let syncing = false;
  for (const pane of panes) {
    pane.addEventListener('scroll', () => {
      if (syncing) return;
      syncing = true;
      for (const other of panes) {
        if (other === pane) continue;
        other.scrollTop = pane.scrollTop;
        other.scrollLeft = pane.scrollLeft;
      }
      syncing = false;
    });
  }
}

function showFileEditorDecisionDialog(options = {}) {
  const actions = Array.isArray(options.actions) && options.actions.length
    ? options.actions
    : [{id: 'cancel', label: t('dialog.cancel')}];
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = `file-editor-dialog-backdrop ${options.className || ''}`.trim();
    const actionButtons = actions.map(action => (
      `<button type="button" class="file-editor-dialog-action ${esc(action.variant || '')}" data-dialog-action="${esc(action.id)}">${esc(action.label || action.id)}</button>`
    )).join('');
    const body = options.bodyHtml
      ? `<div class="file-editor-dialog-body custom">${options.bodyHtml}</div>`
      : `<div class="file-editor-dialog-body">${esc(options.message || '')}</div>`;
    backdrop.innerHTML = `
      <section class="file-editor-dialog" role="dialog" aria-modal="true" aria-label="${esc(options.title || t('dialog.defaultTitle'))}">
        <div class="file-editor-dialog-title">${esc(options.title || t('dialog.defaultTitle'))}</div>
        ${body}
        <div class="file-editor-dialog-actions">${actionButtons}</div>
      </section>`;
    const finish = action => {
      document.removeEventListener('keydown', onKeydown, true);
      backdrop.remove();
      resolve(action);
    };
    const onKeydown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finish('cancel');
      }
    };
    backdrop.addEventListener('click', event => {
      const button = event.target?.closest?.('[data-dialog-action]');
      if (button && backdrop.contains(button)) {
        event.preventDefault();
        finish(button.dataset.dialogAction || 'cancel');
        return;
      }
      if (event.target === backdrop) finish('cancel');
    });
    document.addEventListener('keydown', onKeydown, true);
    document.body.appendChild(backdrop);
    options.onMount?.(backdrop);
    const preferred = backdrop.querySelector('[data-dialog-action]:not(.danger)') || backdrop.querySelector('[data-dialog-action]');
    preferred?.focus?.({preventScroll: true});
  });
}

async function showFileConflictCompareDialog(path, panel = null) {
  const state = openFiles.get(path);
  const loaded = await openFileStateFromDisk(path);
  const diskState = loaded.state;
  const diskText = diskState?.kind === 'text' ? diskState.content : loaded.missing ? t('dialog.missingOnDisk') : String(diskState?.error || t('dialog.unableLoadDisk'));
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.compareTitle', {name: basenameOf(path)}),
    bodyHtml: fileConflictCompareHtml(state?.content || '', diskText),
    actions: [
      {id: 'overwrite', label: t('dialog.overwriteDisk'), variant: 'danger'},
      {id: 'reload', label: t('dialog.keepDisk')},
      {id: 'cancel', label: t('dialog.cancel')},
    ],
    className: 'file-editor-compare-dialog',
    onMount: bindFileConflictCompareScroll,
  });
  if (action === 'overwrite') return saveFileEditor(path, panel, {force: true});
  if (action === 'reload') return reloadOpenFileFromDisk(path, {force: true});
  return false;
}

async function showFileSaveConflictDialog(path, panel = null, options = {}) {
  if (fileConflictDialogOpen(path)) return false;
  setFileConflictDialogOpen(path, true);
  try {
    const state = openFiles.get(path);
    if (state) {
      if (!state.externalChanged && !state.externalMissing) {
        state.externalChanged = {mtime: 0, size: null};
      }
      renderOpenFilePath(path);
    }
    const detail = options.message ? `\n\n${options.message}` : '';
    const action = await showFileEditorDecisionDialog({
      title: t('dialog.conflictTitle'),
      message: `${t('dialog.conflictMessage', {name: basenameOf(path)})}${detail}`,
      actions: [
        {id: 'overwrite', label: t('dialog.overwriteDisk'), variant: 'danger'},
        {id: 'reload', label: t('dialog.keepDisk')},
        {id: 'compare', label: t('dialog.compare')},
        {id: 'cancel', label: t('dialog.cancel')},
      ],
      className: 'file-editor-conflict-dialog',
    });
    if (action === 'overwrite') return saveFileEditor(path, panel, {force: true});
    if (action === 'reload') return reloadOpenFileFromDisk(path, {force: true});
    if (action === 'compare') return showFileConflictCompareDialog(path, panel);
    return false;
  } finally {
    setFileConflictDialogOpen(path, false);
  }
}

async function promptExternalChangeBeforeEditing(path, panel = null) {
  const state = openFiles.get(path);
  if (!state?.externalChanged || state.externalChangeEditPrompted) return false;
  // If the editor has NO unsaved changes, just reload the new disk content silently — never ask. The
  // reload dialog is only for the genuine conflict case (the editor has unsaved edits AND disk changed).
  if (!state.dirty) {
    await reloadOpenFileFromDisk(path, {force: true});
    return true;
  }
  state.externalChangeEditPrompted = true;
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.externalTitle'),
    message: t('dialog.externalMessage', {name: basenameOf(path)}),
    actions: [
      {id: 'reload', label: t('dialog.reload')},
      {id: 'dismiss', label: t('dialog.keepEditing')},
    ],
    className: 'file-editor-external-change-dialog',
  });
  if (action === 'reload') {
    await reloadOpenFileFromDisk(path, {force: true});
    return true;
  }
  setFileEditorPanelStatus(panel, t('dialog.staleStatus'), 'warn');
  renderOpenFilePath(path);
  return false;
}

async function confirmDirtyFileClose(path, panel = null) {
  const state = openFiles.get(path);
  if (!state?.dirty) return true;
  syncOpenFileContentFromPanels(path, panel);
  if (!state.dirty) return true;
  if (fileEditorAutosaveEnabled) {
    if (await saveFileEditor(path, panel, {autosave: true, closing: true})) return true;
    // DOIT.6 #81: autosave-on-close failed (transient error / on-disk conflict). Don't silently abort
    // the close with only a status line — fall through to the explicit save/discard/cancel dialog so
    // the user can retry, discard, or cancel.
  }
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.closeTitle', {name: basenameOf(path)}),
    message: fileEditorAutosaveEnabled ? t('dialog.autosaveFailed') : t('dialog.unsavedChanges'),
    actions: [
      {id: 'save', label: t('dialog.save')},
      {id: 'discard', label: t('dialog.discard'), variant: 'danger'},
      {id: 'cancel', label: t('dialog.cancel')},
    ],
    className: 'file-editor-close-dialog',
  });
  if (action === 'save') return saveFileEditor(path, panel, {closing: true});
  if (action === 'discard') return true;
  return false;
}

function renderOpenFilePath(path) {
  for (const item of filePanelItemsForPath(path)) {
    const slot = slotForSession(item);
    const panel = panelNodes.get(item);
    if (panel && slot && activeItemForSide(slot) === item) renderFileEditorPanel(panel, item);
  }
  renderSessionButtons();
  renderPaneTabStrips();
}

function showFileEditorPaneForPath(path, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  activeFile = path;
  const replacementSlots = setOpenFileOwner(path, item, options);
  syncFileLayoutItems();
  updateFileExplorerCurrentFileHighlight();
  if (replacementSlots) applyLayoutSlots(replacementSlots, {focusSession: item, prune: false});
  return openFileEditorPane(path, {...options, item});
}

function openFilesSetAndShow(path, state, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  const replacementSlots = setOpenFileOwner(path, item, options);
  setFileState(path, state);
  syncFileLayoutItems();
  if (replacementSlots) applyLayoutSlots(replacementSlots, {focusSession: item, prune: false});
  return showFileEditorPaneForPath(path, {...options, item});
}

function refreshOpenFileDiffDecorations(path) {
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel._cmView) panel._cmView.dispatch({});
  }
}

function openFileDiffAvailable(state) {
  if (!state?.diffLoaded) return false;
  const diff = String(state.diff || '');
  if (state.untracked || /^---\s+\/dev\/null/m.test(diff) || (!state.diffOriginal && !state.diffWorkingMissing && !diff)) return false;
  return Boolean(state.diff || state.diffWorkingMissing);
}

function applyOpenFileDiffPayload(state, payload) {
  state.diff = payload.diff || '';
  state.diffOriginal = payload.original || '';
  state.diffOriginalError = payload.original_error || '';
  state.diffWorking = payload.working || '';
  state.diffWorkingError = payload.working_error || '';
  state.diffRepo = payload.repo || '';
  state.diffRelativePath = payload.relative_path || '';
  state.diffFromRef = payload.from_ref || '';
  state.diffToRef = payload.to_ref || '';
  state.diffWorkingMissing = payload.working_missing === true;
  state.untracked = payload.untracked === true;
  state.diffLoaded = true;
  state.diffUnavailable = false;
  state.diffError = '';
}

function markOpenFileDiffUnavailable(state, error) {
  state.diff = '';
  state.diffOriginal = '';
  state.diffWorking = '';
  state.diffLoaded = true;
  state.diffUnavailable = true;
  state.diffError = String(error || 'diff unavailable');
}

async function refreshOpenFileDiff(path, options = {}) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  // Dedup concurrent triggers (renderFileEditorPanel + ensureCodeMirrorDiffPanel both ask): a second
  // caller awaits the SAME in-flight load instead of returning early, so the diff panel never renders
  // a MergeView against an un-loaded (empty) original (which showed an untracked file all-green).
  if (state.diffLoading && state._diffLoadingPromise) return state._diffLoadingPromise;
  state.diffLoading = true;
  state._diffLoadingPromise = (async () => {
    try {
      // C6: diff the file against ITS repo's FROM/TO (not a global pair), so a per-repo selection applies.
      // When called from the Modified-files panel, explicit fromRef/toRef override the lookup so the diff
      // always matches exactly what the panel showed — even for repos not in diffRefsByRepo.
      const explicitFromRef = options.fromRef || '';
      const explicitToRef = options.toRef || '';
      if (explicitFromRef || explicitToRef) {
        state.diffPinnedFromRef = explicitFromRef || 'HEAD';
        state.diffPinnedToRef = explicitToRef || 'current';
      }
      const refString = (explicitFromRef || explicitToRef)
        ? `from=${encodeURIComponent(explicitFromRef || 'HEAD')}&to=${encodeURIComponent(explicitToRef || 'current')}`
        : (state.diffPinnedFromRef || state.diffPinnedToRef)
          ? `from=${encodeURIComponent(state.diffPinnedFromRef || 'HEAD')}&to=${encodeURIComponent(state.diffPinnedToRef || 'current')}`
          : diffRefQueryString(fileRepoForPath(path));
      const payload = await apiFetchJson(`/api/fs/diff?path=${encodeURIComponent(path)}&${refString}`);
      applyOpenFileDiffPayload(state, payload);
      refreshOpenFileDiffDecorations(path);
      return true;
    } catch (error) {
      markOpenFileDiffUnavailable(state, error);
      if (!options.silent) {
        for (const panel of fileEditorPanelsForPath(path)) setFileEditorPanelStatus(panel, `diff unavailable: ${String(error)}`, 'warn');
      }
      return false;
    } finally {
      state.diffLoading = false;
      state._diffLoadingPromise = null;
      // Repaint after clearing diffLoading. Rendering while it is still true leaves the diff toolbar
      // disabled even though the MergeView has already been built, so the expand/collapse context button
      // ignores clicks until some unrelated render happens.
      for (const panel of fileEditorPanelsForPath(path)) {
        const item = panel.dataset.layoutItem || fileEditorItemFor(path);
        updateFileEditorDiffButton(panel.querySelector('.file-editor-diff-panel'), path, state, item);
        updateFileEditorDiffExpandButton(panel.querySelector('.file-editor-diff-expand-panel'), path, state, item);
        if (options.renderOnComplete !== false && editorViewModeFor(path, item) === 'diff') renderFileEditorPanel(panel, item);
      }
    }
  })();
  return state._diffLoadingPromise;
}

async function refreshOpenFileGitMetadata(path) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  try {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
    const current = openFiles.get(path);
    if (!current || current.kind !== 'text') return false;
    applyFileGitMetadata(current, payload);
    return true;
  } catch (_error) {
    const current = openFiles.get(path);
    if (current && current.kind === 'text') {
      current.gitTracked = false;
      current.gitHistory = [];
      current.gitHasHistory = false;
    }
    return false;
  }
}

async function openFileInEditor(fullPath, entryOrName, options = {}) {
  const entry = typeof entryOrName === 'object' && entryOrName ? entryOrName : null;
  const name = entry?.name || String(entryOrName || basenameOf(fullPath));
  const ext = fileExtensionOf(name);
  const kind = IMAGE_EXTENSIONS.has(ext) ? 'image' : 'text';
  const defaultItem = kind === 'image' && imageOpenUsesSharedViewer(options)
    ? imageViewerItemFor(fullPath)
    : fileEditorItemFor(fullPath);
  const item = options.item || defaultItem;
  const openOptions = {
    ...options,
    item,
    ownerSession: options.ownerSession || normalizedOpenFileOwnerSession(entry?.session),
  };
  const alreadyOpen = Boolean(openFiles.get(fullPath)?.kind);
  if (options.viewMode) setFileEditorViewMode(fullPath, options.viewMode, item);
  else if (!isFilePreviewItem(item)) setFileEditorViewMode(fullPath, 'edit', item);
  recordEditorNav(item);   // DOIT.21: push this tab to the back/forward history (no-op while navigating)
  if (alreadyOpen) {
    await refreshOpenFileGitMetadata(fullPath);
    await showFileEditorPaneForPath(fullPath, openOptions);
    renderOpenFilePath(fullPath);
    return item;
  }
  if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(entry.size));
    state.mtime = fileEntryMtime(entry);
    await openFilesSetAndShow(fullPath, state, openOptions);
    return item;
  }
  if (kind === 'image') {
    await openFilesSetAndShow(fullPath, {mtime: fileEntryMtime(entry), kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null}, openOptions);
    return item;
  }
  try {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(fullPath)}`);
    const state = applyFileGitMetadata({
      mtime: filePayloadMtime(payload),
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    }, payload);
    await openFilesSetAndShow(fullPath, state, openOptions);
    return item;
  } catch (err) {
    const status = Number(err?.status) || 0;
    if (status) {
      const message = err?.payload?.error || status;
      const state = status === 413
        ? tooLargeFileState(entry?.size ?? null, String(message))
        : status === 404
          ? missingFileState(String(message))
          : fileErrorState(message);
      await openFilesSetAndShow(fullPath, state, openOptions);
      return item;
    }
    showFileOpenError(fullPath, err);
    return null;
  }
}

async function openFileCrossPaneSplit(path) {
  if (!path || !editorPreviewModeAvailable(path)) return;
  const editorItem = fileEditorItemFor(path);
  const previewItem = filePreviewItemFor(path);
  setFileEditorViewMode(path, 'edit', editorItem);
  setFileEditorViewMode(path, 'preview', previewItem);
  if (!openFiles.get(path)?.kind || !fileHasEditorTab(path)) {
    await openFileInEditor(path, {name: basenameOf(path)}, {item: editorItem});
  } else {
    setOpenFileOwner(path, editorItem);
    await openFileEditorPane(path, {item: editorItem});
  }
  const editorSlot = slotForSession(editorItem) || largestPaneSlotForFileEditor() || slotForNewSession();
  const previewSlot = largestPaneSlotForFileEditor(new Set([editorSlot]));
  setOpenFileOwner(path, previewItem);
  if (slotForSession(previewItem)) {
    activatePaneTab(slotForSession(previewItem), previewItem);
  } else if (previewSlot) {
    await openFileEditorPane(path, {
      item: previewItem,
      targetSlot: previewSlot,
    });
  } else {
    await openFileEditorPane(path, {
      item: previewItem,
      targetSlot: editorSlot,
      targetZone: 'right',
      pct: defaultSplitPercent,
    });
  }
  const finalEditorSlot = slotForSession(editorItem);
  if (finalEditorSlot) activatePaneTab(finalEditorSlot, editorItem);
  renderSessionButtons();
  renderPaneTabStrips();
}

async function openFileStateFromDisk(path, entry = null) {
  const fetched = entry ? {entry, missing: false, error: '', network: false} : await fetchFileEntryStatus(path);
  const fileEntry = fetched.entry;
  if (!fileEntry) {
    if (fetched.missing) return {missing: true};
    return {state: fileErrorState(fetched.error || 'failed to inspect file')};
  }
  if (Number(fileEntry.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(fileEntry.size));
    state.mtime = fileEntryMtime(fileEntry);
    return {state};
  }
  const ext = fileExtensionOf(fileEntry.name || basenameOf(path));
  if (IMAGE_EXTENSIONS.has(ext)) {
    return {state: {
      mtime: fileEntryMtime(fileEntry),
      size: fileEntry.size ?? null,
      kind: 'image',
      original: '',
      content: '',
      dirty: false,
    }};
  }
  try {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
    return {state: applyFileGitMetadata({
      mtime: filePayloadMtime(payload),
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    }, payload)};
  } catch (error) {
    const status = Number(error?.status) || 0;
    if (status) {
      const message = String(error?.payload?.error || status);
      const state = status === 413
        ? tooLargeFileState(fileEntry.size ?? null, message)
        : status === 404
          ? missingFileState(message)
          : fileErrorState(message);
      state.mtime = fileEntryMtime(fileEntry);
      state.size = fileEntry.size ?? null;
      return {state};
    }
    return {state: fileErrorState(error)};
  }
}

function markOpenFileMissing(path) {
  const state = openFiles.get(path);
  if (!state) return;
  if (state.dirty) {
    state.externalMissing = true;
    delete state.externalChanged;
    delete state.externalError;
  } else {
    setFileState(path, missingFileState());
  }
  renderOpenFilePath(path);
}

function markOpenFileExternalError(path, error) {
  const state = openFiles.get(path);
  if (!state) return;
  clearFileAutosaveTimer(path);
  state.externalError = String(error || 'refresh failed');
  renderOpenFilePath(path);
}

async function replaceOpenFileStateFromDisk(path, entry = null) {
  const previous = openFiles.get(path);
  const loaded = await openFileStateFromDisk(path, entry);
  if (loaded.missing) {
    markOpenFileMissing(path);
    return false;
  }
  clearFileAutosaveTimer(path);
  setFileState(path, clearOpenFileExternalState(loaded.state));
  renderOpenFilePath(path);
  if (previous?.diff !== undefined) refreshOpenFileDiff(path);
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
    const fetched = await fetchFileEntryStatus(path);
    const entry = fetched.entry;
    if (!entry) {
      if (fetched.missing) {
        if (state.externalMissing) continue;
        clearFileAutosaveTimer(path);
        markOpenFileMissing(path);
      } else {
        markOpenFileExternalError(path, fetched.error);
      }
      continue;
    }
    if (!fileEntryChanged(state, entry)) {
      if (state.externalChanged || state.externalMissing || state.externalError) {
        delete state.externalMissing;
        delete state.externalError;
        if (!state.dirty) delete state.externalChanged;
        if (!state.dirty) delete state.externalChangeEditPrompted;
        renderOpenFilePath(path);
      }
      if (state.dirty) scheduleFileAutosave(path);
      continue;
    }
    if (state.dirty) {
      const externalChanged = {mtime: fileEntryMtime(entry), size: entry.size ?? null};
      if (state.externalChanged
        && state.externalChanged.mtime === externalChanged.mtime
        && state.externalChanged.size === externalChanged.size) {
        continue;
      }
      state.externalChanged = externalChanged;
      delete state.externalChangeEditPrompted;
      delete state.externalMissing;
      delete state.externalError;
      clearFileAutosaveTimer(path);
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
  const entriesByDir = new Map();
  const signaturesByDir = new Map();
  for (const directory of directories) {
    const entries = await fetchDirectory(directory, {recordSignature: false});
    if (!entries) continue;
    const normalizedDirectory = normalizeDirectoryPath(directory);
    entriesByDir.set(normalizedDirectory, entries);
    const signature = directoryEntriesSignature(entries);
    signaturesByDir.set(normalizedDirectory, signature);
    const previous = fileExplorerDirectorySignatures.get(normalizedDirectory);
    if (previous !== undefined && previous !== signature) changed = true;
  }
  if (changed && fileExplorerUserIsActive()) {
    deferFileExplorerRefresh();
    return;
  }
  for (const [directory, signature] of signaturesByDir.entries()) {
    setLimitedMapEntry(fileExplorerDirectorySignatures, directory, signature, fileExplorerMemoryCacheLimit);
  }
  if (changed) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true, entriesByDir});
}

async function refreshWatchedFilesystem() {
  if (filesystemRefreshInFlight) return;
  filesystemRefreshInFlight = true;
  try {
    await refreshFileExplorerIfChanged();
    await refreshOpenFilesIfChanged();
    if (document.querySelector('.file-explorer-changes-panel')) {
      fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true});
    }
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

function largestPaneSlotForFileEditor(exclude = new Set()) {
  const excludedSlots = exclude instanceof Set ? exclude : new Set(Array.isArray(exclude) ? exclude : [exclude]);
  const filesSlot = slotForSession(fileExplorerItemId);
  const filesRect = filesSlot ? layoutColumnNode(filesSlot)?.getBoundingClientRect() : null;
  let fallback = null;
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (excludedSlots.has(slot) || slot === filesSlot || !paneTabs(slot).length) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    const candidate = {slot, area};
    if (!fallback || area > fallback.area) fallback = candidate;
    if (filesRect && rect && rect.left < filesRect.right - 1) continue;
    if (!best || area > best.area) best = candidate;
  }
  return (best || fallback)?.slot || null;
}

async function openFileEditorPane(path, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  syncFileLayoutItems();
  compactCurrentLayoutSlots({focusSession: item});
  renderSessionButtons();
  const existingSlot = slotForSession(item);
  const targetSlot = options.targetSlot && layoutSlotKeys().includes(options.targetSlot) ? options.targetSlot : null;
  const targetZone = options.targetZone || 'middle';
  const targetIndex = Number.isFinite(Number(options.targetIndex)) ? Number(options.targetIndex) : null;
  if (targetSlot && targetZone !== 'middle') {
    await splitSessionAtSlot(item, targetSlot, targetZone, existingSlot, options.pct || null);
    return;
  }
  if (targetSlot && !slotIsFileExplorerPane(targetSlot)) {
    await moveSessionToSlot(item, targetSlot, existingSlot, targetIndex ?? paneTabs(targetSlot).length);
    return;
  }
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
  const fallbackSlot = layoutSlotKeys().find(slot => slot !== filesSlot) || filesSlot;
  if (fallbackSlot) {
    const zone = fallbackSlot === filesSlot ? 'right' : 'left';
    const pct = fallbackSlot === filesSlot ? fileExplorerSplitPercent : defaultSplitPercent;
    await splitSessionAtSlot(item, fallbackSlot, zone, null, pct);
    return;
  }
  await moveSessionToSlot(item, slotForNewSession(), null);
}

function codeMirrorLanguageName(path) {
  const ext = fileExtensionOf(path);
  if (ext === '.html' || ext === '.htm') return 'html';
  const language = syntaxLanguageForPath(path);
  if (language === 'bash') return 'shell';
  if (language === 'ini') return 'toml';
  if (language === 'javascript' || language === 'typescript') return language;
  return language || '';
}

function codeMirrorApiIsUsable(api) {
  return Boolean(
    api?.EditorState?.create
    && api?.Compartment
    && api?.EditorState?.readOnly?.of
    && api?.EditorView?.theme
    && api?.EditorView?.editable?.of
    && (api?.EditorView?.lineWrapping || api?.EditorView?.contentAttributes?.of)
    && api?.keymap?.of
    && api?.drawSelection
    && api?.highlightActiveLine
    && api?.search
    && api?.openSearchPanel
  );
}

function loadCodeMirrorBundleScript(options = {}) {
  if (!options.force && codeMirrorApiIsUsable(window.YOLOmuxCodeMirror)) return Promise.resolve(window.YOLOmuxCodeMirror);
  if (options.force) codeMirrorBundlePromise = null;
  if (!codeMirrorBundlePromise) {
    codeMirrorBundlePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const assetUrl = bootstrap.codeMirrorAssetUrl || '/static/codemirror.js';
      const separator = assetUrl.includes('?') ? '&' : '?';
      script.src = options.force ? `${assetUrl}${separator}retry=${Date.now()}` : assetUrl;
      script.async = true;
      script.onload = () => resolve(window.YOLOmuxCodeMirror || null);
      script.onerror = () => reject(new Error(`CodeMirror bundle failed to load: ${script.src}`));
      document.head.appendChild(script);
    });
  }
  return codeMirrorBundlePromise;
}

async function loadCodeMirrorApi() {
  if (codeMirrorApiIsUsable(window.YOLOmuxCodeMirror)) return window.YOLOmuxCodeMirror;
  if (!codeMirrorApiPromise) {
    codeMirrorApiPromise = (async () => {
      let bundleError = null;
      try {
        let bundledApi = await loadCodeMirrorBundleScript();
        if (codeMirrorApiIsUsable(bundledApi)) return bundledApi;
        bundledApi = await loadCodeMirrorBundleScript({force: true});
        if (codeMirrorApiIsUsable(bundledApi)) return bundledApi;
        bundleError = new Error('CodeMirror bundle missing critical exports');
      } catch (err) {
        bundleError = err;
      }
      const suffix = bundleError ? `: ${bundleError}` : '';
      throw new Error(`CodeMirror local bundle is unavailable or incomplete${suffix}. Check /static/codemirror.js.`);
    })();
  }
  try {
    return await codeMirrorApiPromise;
  } catch (error) {
    codeMirrorApiPromise = null;
    throw error;
  }
}

function codeMirrorMarkdownCodeLanguages(api) {
  if (!api?.LanguageDescription) return null;
  const stream = mode => safeCodeMirrorExtension('stream language', () => (mode && api.StreamLanguage ? api.StreamLanguage.define(mode) : null));
  const languageEntries = [
    {name: 'JavaScript', alias: ['js', 'jsx', 'node'], support: () => api.javascript?.({jsx: true})},
    {name: 'TypeScript', alias: ['ts', 'tsx'], support: () => api.javascript?.({typescript: true, jsx: true})},
    {name: 'Python', alias: ['py'], support: () => api.python?.()},
    {name: 'Rust', alias: ['rs'], support: () => api.rust?.()},
    {name: 'JSON', alias: ['jsonc'], support: () => api.json?.()},
    {name: 'HTML', alias: ['htm'], support: () => api.html?.()},
    {name: 'CSS', alias: ['scss'], support: () => api.css?.()},
    {name: 'XML', alias: ['svg'], support: () => api.xml?.()},
    {name: 'YAML', alias: ['yml'], support: () => api.yaml?.()},
    {name: 'Shell', alias: ['sh', 'bash', 'zsh'], support: () => stream(api.shell)},
    {name: 'TOML', alias: ['ini'], support: () => stream(api.toml)},
  ];
  return languageEntries.flatMap(entry => {
    const support = safeCodeMirrorExtension(entry.name, () => entry.support?.());
    if (codeMirrorExtensionIsEmpty(support)) return [];
    const description = safeCodeMirrorExtension(entry.name, () => api.LanguageDescription.of({
        name: entry.name,
        alias: entry.alias,
        support,
      }));
    return codeMirrorExtensionIsEmpty(description) ? [] : [description];
  });
}

function codeMirrorExtensionIsEmpty(extension) {
  return !extension || (Array.isArray(extension) && extension.length === 0);
}

function safeCodeMirrorExtension(label, factory) {
  try {
    return factory?.() || [];
  } catch (error) {
    console.warn(`CodeMirror ${label} extension unavailable; using plain text`, error);
    return [];
  }
}

function codeMirrorLanguageExtension(api, path) {
  const language = codeMirrorLanguageName(path);
  if (language === 'javascript') return safeCodeMirrorExtension(language, () => api.javascript?.({jsx: true}));
  if (language === 'typescript') return safeCodeMirrorExtension(language, () => api.javascript?.({typescript: true, jsx: true}));
  if (language === 'python') return safeCodeMirrorExtension(language, () => api.python?.());
  if (language === 'rust') return safeCodeMirrorExtension(language, () => api.rust?.());
  if (language === 'json') return safeCodeMirrorExtension(language, () => api.json?.());
  if (language === 'html') return safeCodeMirrorExtension(language, () => api.html?.());
  if (language === 'xml') return safeCodeMirrorExtension(language, () => api.xml?.());
  if (language === 'css') return safeCodeMirrorExtension(language, () => api.css?.());
  if (language === 'markdown') {
    const codeLanguages = codeMirrorMarkdownCodeLanguages(api);
    return safeCodeMirrorExtension(language, () => (codeLanguages?.length ? api.markdown?.({codeLanguages}) : api.markdown?.()));
  }
  if (language === 'yaml') return safeCodeMirrorExtension(language, () => api.yaml?.());
  if (language === 'shell' && api.shell) return safeCodeMirrorExtension(language, () => api.StreamLanguage?.define(api.shell));
  if (language === 'toml' && api.toml) return safeCodeMirrorExtension(language, () => api.StreamLanguage?.define(api.toml));
  return [];
}

function codeMirrorHighlightExtension(api) {
  if (!api?.syntaxHighlighting) return [];
  if (!api.HighlightStyle || !api.tags) {
    return api.defaultHighlightStyle ? safeCodeMirrorExtension('default highlight', () => api.syntaxHighlighting(api.defaultHighlightStyle, {fallback: true})) : [];
  }
  const t = api.tags;
  const scheme = activeEditorScheme();
  const palette = {
    ...scheme.syntax,
    text: scheme.fg,
    muted: scheme.syntax.comment,
    headingBg: scheme.syntax.headingBg,
  };
  const tags = (...items) => items.filter(Boolean);
  const headingStyle = {tag: tags(t.heading, t.heading1, t.heading2), color: palette.heading, fontWeight: '700'};
  if (palette.headingBg) headingStyle.backgroundColor = palette.headingBg;
  return safeCodeMirrorExtension('highlight', () => api.syntaxHighlighting(api.HighlightStyle.define([
    {tag: t.keyword, color: palette.keyword},
    {tag: tags(t.controlKeyword, t.operatorKeyword), color: palette.control || palette.keyword},
    {tag: tags(t.atom, t.bool, t.null), color: palette.atom},
    {tag: tags(t.string, t.special(t.string), t.regexp), color: palette.string},
    {tag: tags(t.number, t.integer, t.float), color: palette.number},
    {tag: tags(t.variableName, t.self, t.definition(t.variableName)), color: palette.variable},
    {tag: tags(t.function(t.variableName), t.function(t.propertyName)), color: palette.function},
    {tag: tags(t.typeName, t.className, t.namespace), color: palette.type},
    {tag: tags(t.propertyName, t.attributeName), color: palette.property},
    {tag: tags(t.tagName, t.angleBracket), color: palette.tag},
    {tag: tags(t.comment, t.meta), color: palette.muted},
    headingStyle,
    {tag: t.strong, fontWeight: '700', color: palette.strong},
    {tag: t.emphasis, fontStyle: 'italic', color: palette.emphasis},
    {tag: tags(t.link, t.url), color: palette.link, textDecoration: 'underline'},
    {tag: tags(t.monospace, t.processingInstruction), color: palette.inlineCode, backgroundColor: palette.inlineCodeBg},
    {tag: t.invalid, color: palette.invalid},
  ]), {fallback: true}));
}

function codeMirrorThemeExtension(api) {
  const scheme = activeEditorScheme();
  return api.EditorView.theme({
    '&': {
      height: '100%',
      color: scheme.fg,
      backgroundColor: scheme.bg,
    },
    '.cm-scroller': {
      fontFamily: 'var(--editor-font)',
      fontSize: 'var(--editor-font-size)',
      lineHeight: 'var(--editor-line-height)',
    },
    '.cm-content': {
      caretColor: scheme.cursor,
      padding: '8px 10px',
    },
    '.cm-cursor': {
      borderLeftColor: scheme.cursor,
      borderLeftWidth: '2px',
    },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground': {
      backgroundColor: scheme.selection,
      boxShadow: scheme.dark ? 'inset 0 0 0 1px rgba(191, 219, 254, 0.42)' : 'inset 0 0 0 1px rgba(29, 78, 216, 0.24)',
    },
    '.cm-content ::selection': {
      backgroundColor: 'transparent !important',
      color: 'inherit !important',
    },
    '.cm-content ::-moz-selection': {
      backgroundColor: 'transparent !important',
      color: 'inherit !important',
    },
    '.cm-activeLine': {
      backgroundColor: scheme.activeLine,
    },
    '.cm-activeLineGutter': {
      backgroundColor: scheme.activeLine,
      color: scheme.fg,
    },
    '.cm-gutters': {
      color: scheme.lineNo,
      backgroundColor: scheme.gutterBg,
      borderRightColor: scheme.line,
    },
    '.cm-panels': {
      // shared pane-chrome bar: the find/replace panel matches the tab-strip bar (bright when this pane
      // is focused, neutral gray when not) instead of a fixed gray — see --pane-bar-bg.
      color: scheme.fg,
      backgroundColor: 'var(--pane-bar-bg)',
    },
    '.cm-searchMatch': {
      backgroundColor: scheme.dark ? 'rgba(245, 197, 66, 0.62)' : 'rgba(255, 204, 0, 0.78)',
      outline: `1px solid ${scheme.dark ? 'rgba(255, 224, 92, 0.76)' : 'rgba(132, 83, 0, 0.58)'}`,
    },
    '.cm-searchMatch-selected': {
      backgroundColor: scheme.dark ? 'rgba(118, 185, 0, 0.84)' : 'rgba(255, 125, 0, 0.86)',
      outline: `2px solid ${scheme.dark ? 'rgba(210, 255, 110, 0.92)' : 'rgba(116, 59, 0, 0.76)'}`,
    },
  }, {dark: scheme.dark});
}

function codeMirrorWrapMarkerExtension(api) {
  if (!api.ViewPlugin) return [];
  const scheme = activeEditorScheme();
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.frame = 0;
      this.layer = document.createElement('div');
      this.layer.className = 'cm-wrap-marker-layer';
      view.scrollDOM.appendChild(this.layer);
      this.queue();
    }

    update(update) {
      if (update.docChanged || update.viewportChanged || update.geometryChanged) this.queue();
    }

    queue() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.render();
      });
    }

    render() {
      const view = this.view;
      const lineHeight = view.defaultLineHeight || Number.parseFloat(getComputedStyle(view.contentDOM).lineHeight) || editorFontSize;
      const line = view.contentDOM.querySelector('.cm-line') || view.contentDOM;
      const lineStyle = getComputedStyle(line);
      const paddingLeft = Number.parseFloat(lineStyle.paddingLeft) || 0;
      const markerWidth = Math.max(12, Math.min(28, lineHeight * 0.9));
      const markerLeft = Math.max(0, view.contentDOM.offsetLeft + Math.max(0, paddingLeft - markerWidth));
      const blocks = Array.from(view.viewportLineBlocks || []);
      const nodes = [];
      for (const block of blocks) {
        const rows = Math.max(1, Math.round((block.height || lineHeight) / Math.max(1, lineHeight)));
        for (let row = 1; row < rows; row += 1) {
          const marker = document.createElement('span');
          marker.className = 'cm-wrap-marker';
          marker.textContent = '↪';
          marker.style.left = `${markerLeft}px`;
          marker.style.top = `${Math.round(block.top + lineHeight * row)}px`;
          marker.style.height = `${Math.round(lineHeight)}px`;
          marker.style.width = `${Math.round(markerWidth)}px`;
          nodes.push(marker);
        }
      }
      this.layer.replaceChildren(...nodes);
    }

    destroy() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.layer.remove();
    }
  }, {dark: scheme.dark});
}

// DOIT.26: inline git blame. Lazily fetch the /api/blame payload into the path's fileState record.
// DOIT.34 #3: dedup concurrent fetches (multiple open panels for one path share a single request).
async function fetchEditorBlame(path) {
  if (editorBlameFetches.has(path)) return editorBlameFetches.get(path);
  const promise = (async () => {
    try {
      const data = await apiFetchJson(`/api/blame?path=${encodeURIComponent(path)}`);
      setEditorBlameForPath(path, data);
      return data;
    } catch (_error) {
      return null;
    } finally {
      editorBlameFetches.delete(path);
    }
  })();
  editorBlameFetches.set(path, promise);
  return promise;
}

// DOIT.34 #3: when a text editor opens/renders with blame already ON but its blame isn't cached yet
// (the common case: enable blame, THEN open a new file), fetch it and nudge the open editors so the
// blame ViewPlugin recomputes its decoration against the now-populated cache — no manual toggle needed.
async function ensureEditorBlameForPath(path) {
  if (!fileEditorBlameEnabled || !path || hasEditorBlameForPath(path)) return;
  await fetchEditorBlame(path);
  if (!fileEditorBlameEnabled || !hasEditorBlameForPath(path)) return;
  for (const panel of fileEditorPanelsForPath(path)) {
    const view = panel?._cmView;
    if (!view) continue;
    // A selection-preserving transaction has selectionSet=true, which is what the blame ViewPlugin
    // listens for — so it rebuilds the current-line annotation now that the data is present.
    try { view.dispatch({selection: view.state.selection}); } catch (_) {}
  }
}

function blameAnnotationText(info) {
  const author = info.author || '';
  const rel = info.time ? relativeTimeFormat(Math.max(0, Math.floor(Date.now() / 1000) - info.time)) : '';
  const pr = info.pr ? ` (#${info.pr})` : '';
  return `${author}, ${rel} • ${info.summary || ''}${pr}`.trim();
}

function blameHoverText(info) {
  const author = info.author || '';
  const abs = info.time ? new Date(info.time * 1000).toLocaleString() : '';
  const sha = info.sha && !/^0+$/.test(info.sha) ? ` [${info.sha.slice(0, 8)}]` : '';
  return `${author} • ${abs}${sha}\n${info.summary || ''}`.trim();
}

// Decorate the CURSOR's current line with the dim blame annotation (rendered via a CSS ::after that
// flows after the line text — the bundle exposes no WidgetType). The dim color comes from the editor
// scheme's --code-comment token (theme-aware), and the full commit is the line's native title tooltip.
function codeMirrorBlameExtension(api, path) {
  if (!fileEditorBlameEnabled || !api.ViewPlugin || !api.Decoration) return [];
  if (!fileStateHasUsefulGitHistory(fileStateFor(path))) return [];
  const lineDeco = info => api.Decoration.line({attributes: {'data-blame': blameAnnotationText(info), title: blameHoverText(info)}});
  const build = view => {
    const blame = editorBlameForPath(path);
    if (!blame || !blame.lines) return api.Decoration.none;
    // DOIT.26: annotate EVERY visible line when fileEditorBlameAllLines is on, else just the cursor line
    // (the Cursor default). Viewport-scoped so a huge file only decorates what's on screen.
    if (fileEditorBlameAllLines) {
      const ranges = [];
      const visible = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const {from, to} of visible) {
        let pos = from;
        while (pos <= to) {
          const line = view.state.doc.lineAt(pos);
          const info = blame.lines[String(line.number)];
          if (info) ranges.push(lineDeco(info).range(line.from));
          if (line.to + 1 <= pos) break;   // guard against a zero-length advance
          pos = line.to + 1;
        }
      }
      return ranges.length ? api.Decoration.set(ranges, true) : api.Decoration.none;
    }
    const line = view.state.doc.lineAt(view.state.selection.main.head);
    const info = blame.lines[String(line.number)];
    if (!info) return api.Decoration.none;
    return api.Decoration.set([lineDeco(info).range(line.from)]);
  };
  return api.ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = build(view); }
    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged) this.decorations = build(update.view);
    }
  }, {decorations: plugin => plugin.decorations});
}

function codeMirrorHtmlSemanticEmphasisExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'html' || !api.ViewPlugin || !api.Decoration) return [];
  const palette = activeEditorScheme().syntax;
  const strongMark = api.Decoration.mark({
    attributes: {style: `font-weight:700;color:${palette.strong}`},
  });
  const emphasisMark = api.Decoration.mark({
    attributes: {style: `font-style:italic;color:${palette.emphasis}`},
  });
  const semanticTagPattern = /<(strong|b|em|i)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi;
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const visible of visibleRanges) {
        const text = view.state.doc.sliceString(visible.from, visible.to);
        semanticTagPattern.lastIndex = 0;
        let match;
        while ((match = semanticTagPattern.exec(text))) {
          const openTagEnd = match[0].indexOf('>') + 1;
          if (openTagEnd <= 0) continue;
          const from = visible.from + match.index + openTagEnd;
          const to = from + match[2].length;
          if (to <= from) continue;
          const mark = /^(?:strong|b)$/i.test(match[1]) ? strongMark : emphasisMark;
          ranges.push(mark.range(from, to));
        }
      }
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

function codeMirrorMarkdownStrongExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'markdown' || !api.ViewPlugin || !api.Decoration) return [];
  const palette = activeEditorScheme().syntax;
  const strongMark = api.Decoration.mark({
    attributes: {style: `font-weight:700;color:${palette.strong}`},
  });
  const strongPattern = /(\*\*[^*\n](?:.|\n)*?\*\*|__[^_\n](?:.|\n)*?__)/g;
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const visible of visibleRanges) {
        const text = view.state.doc.sliceString(visible.from, visible.to);
        strongPattern.lastIndex = 0;
        let match;
        while ((match = strongPattern.exec(text))) {
          const from = visible.from + match.index;
          const to = from + match[0].length;
          if (to > from) ranges.push(strongMark.range(from, to));
        }
      }
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

function codeMirrorMarkdownFallbackSyntaxExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'markdown' || !api.ViewPlugin || !api.Decoration) return [];
  const markByClass = new Map();
  const mark = className => {
    if (!markByClass.has(className)) markByClass.set(className, api.Decoration.mark({class: className}));
    return markByClass.get(className);
  };
  const addInlineMarks = (ranges, lineText, lineFrom) => {
    const inlinePatterns = [
      ['md-code', /`[^`\n]+`/g],
      ['md-bold', /\*\*[^*\n]+\*\*|__[^_\n]+__/g],
      ['md-link', /\[[^\]\n]+\]\([^)]+\)/g],
      ['md-html', /<\/?[A-Za-z][^>\n]*?>/g],
      ['md-italic', /(^|[^\w*])(\*[^*\s\n][^*\n]*\*|_[^_\s\n][^_\n]*_)/g, 2],
    ];
    for (const [className, pattern, groupIndex] of inlinePatterns) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(lineText))) {
        const text = groupIndex ? match[groupIndex] : match[0];
        if (!text) continue;
        const groupOffset = groupIndex ? match[0].indexOf(text) : 0;
        const from = lineFrom + match.index + groupOffset;
        const to = from + text.length;
        if (to > from) ranges.push(mark(className).range(from, to));
      }
    }
  };
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      let inFence = false;
      const doc = view.state.doc;
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: doc.length}];
      for (const visible of visibleRanges) {
        const startLine = doc.lineAt(visible.from).number;
        const endLine = doc.lineAt(Math.max(visible.from, visible.to)).number;
        for (let lineNo = startLine; lineNo <= endLine; lineNo += 1) {
          const line = doc.line(lineNo);
          const text = line.text;
          const fence = /^\s*(```|~~~)/.test(text);
          if (fence) {
            ranges.push(mark('md-fence').range(line.from, line.to));
            inFence = !inFence;
            continue;
          }
          if (inFence) {
            ranges.push(mark('md-codeblock').range(line.from, line.to));
            continue;
          }
          const heading = text.match(/^(\s{0,3})(#{1,6})(\s+.*)$/);
          if (heading) {
            ranges.push(mark(`md-heading md-heading-${heading[2].length}`).range(line.from, line.to));
            continue;
          }
          if (/^\s*>\s?/.test(text)) ranges.push(mark('md-blockquote').range(line.from, line.to));
          const list = text.match(/^\s*(?:[-*+]|\d+\.)\s+/);
          if (list) ranges.push(mark('md-list-marker').range(line.from, line.from + list[0].length));
          addInlineMarks(ranges, text, line.from);
        }
      }
      ranges.sort((left, right) => left.from - right.from || (left.startSide || 0) - (right.startSide || 0) || left.to - right.to);
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

// DOIT.6 #150: parseUnifiedDiffLineClasses + codeMirrorDiffLineExtension were removed. The edit view no
// longer paints inline diff decorations (cm-yolomux-diff-add / deletion markers); changes are shown
// ONLY in the explicit diff VIEW (the MergeView built by ensureCodeMirrorDiffPanel, with its own
// .cm-changedLine/.cm-insertedChunk styling). The per-file cached diff-line class map is gone too.

function escapeRegExpLiteral(text) {
  return String(text || '').replace(/[\\^$.*+?()[\]{}|]/g, '\\$&');
}

function codeMirrorSearchCheckboxState(panel) {
  const checkboxes = Array.from(panel?.querySelectorAll?.('input[type="checkbox"]') || []);
  return {
    caseSensitive: Boolean(checkboxes[0]?.checked),
    regexp: Boolean(checkboxes[1]?.checked),
    wholeWord: Boolean(checkboxes[2]?.checked),
  };
}

function isSearchWordChar(char) {
  return /[A-Za-z0-9_]/.test(char || '');
}

function codeMirrorSearchMatches(text, query, options = {}) {
  if (!query) return [];
  const source = String(text || '');
  const flags = options.caseSensitive ? 'g' : 'gi';
  let pattern;
  try {
    pattern = options.regexp ? new RegExp(query, flags) : new RegExp(escapeRegExpLiteral(query), flags);
  } catch (_) {
    return [];
  }
  const matches = [];
  let match;
  while ((match = pattern.exec(source))) {
    const from = match.index;
    const to = from + match[0].length;
    if (to === from) {
      pattern.lastIndex += 1;
      continue;
    }
    if (options.wholeWord && (isSearchWordChar(source[from - 1]) || isSearchWordChar(source[to]))) continue;
    matches.push({from, to});
  }
  return matches;
}

function codeMirrorSearchMatchSummary(text, query, selection = {}, options = {}) {
  const matches = codeMirrorSearchMatches(text, query, options);
  if (!query) return {current: 0, total: 0, text: ''};
  if (!matches.length) return {current: 0, total: 0, text: '0/0'};
  const from = Number(selection.from);
  const to = Number(selection.to);
  const head = Number.isFinite(Number(selection.head)) ? Number(selection.head) : from;
  let index = matches.findIndex(match => match.from === from && match.to === to);
  if (index < 0) {
    index = matches.findIndex(match => match.from <= head && match.to >= head);
  }
  if (index < 0) {
    index = matches.findIndex(match => match.from >= head);
  }
  if (index < 0) index = matches.length - 1;
  return {current: index + 1, total: matches.length, text: `${index + 1}/${matches.length}`};
}

// When you navigate search matches, CodeMirror's default scrollIntoView lands on a far-right horizontal
// position if the document has any long line (e.g. a padded locale JSON, 276-char line): a match on a
// SHORT line then sits off-screen left and the editor looks "scrolled all the way to the right" (blank).
// Re-center the match horizontally on every search-driven selection change — for a short line that
// reveals the line start; for a long line it keeps the match comfortably in view. (Deferred to a rAF
// because dispatch() is not allowed inside an update listener.)
function codeMirrorSearchScrollFix(api) {
  if (!api.EditorView?.updateListener || !api.EditorView?.scrollIntoView) return [];
  return api.EditorView.updateListener.of(update => {
    if (!update.selectionSet) return;
    if (!update.transactions.some(tr => tr.isUserEvent?.('select.search'))) return;
    const head = update.state.selection.main.head;
    const view = update.view;
    requestAnimationFrame(() => {
      try {
        view.dispatch({effects: api.EditorView.scrollIntoView(head, {x: 'center', y: 'nearest'})});
      } catch (_) {}
    });
  });
}

function codeMirrorSearchPanelEnhancementExtension(api) {
  if (!api.ViewPlugin) return [];
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.frame = 0;
      this.panel = null;
      this.onPanelEvent = () => this.queue();
      this.queue();
    }

    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged || update.geometryChanged) this.queue();
    }

    queue() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.render();
      });
    }

    bindPanel(panel) {
      if (panel === this.panel) return;
      this.panel?.removeEventListener?.('input', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('change', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('click', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('keyup', this.onPanelEvent, true);
      this.panel = panel;
      panel?.addEventListener?.('input', this.onPanelEvent, true);
      panel?.addEventListener?.('change', this.onPanelEvent, true);
      panel?.addEventListener?.('click', this.onPanelEvent, true);
      panel?.addEventListener?.('keyup', this.onPanelEvent, true);
    }

    render() {
      const panel = this.view.dom?.querySelector?.('.cm-search');
      this.bindPanel(panel);
      if (!panel) return;
      const next = panel.querySelector?.('.cm-button[name="next"]');
      const previous = panel.querySelector?.('.cm-button[name="prev"]');
      if (next) {
        next.title = 'Next match (Enter)';
        next.setAttribute('aria-label', 'Next match (Enter)');
      }
      if (previous) {
        previous.title = 'Previous match (Shift+Enter)';
        previous.setAttribute('aria-label', 'Previous match (Shift+Enter)');
      }
      let count = panel.querySelector?.('.cm-search-count');
      if (!count) {
        count = document.createElement('span');
        count.className = 'cm-search-count';
        count.setAttribute('aria-live', 'polite');
        const anchor = panel.querySelector?.('.cm-button[name="replaceAll"]') || panel.querySelector?.('.cm-button[name="select"]');
        anchor?.insertAdjacentElement?.('afterend', count) || panel.appendChild(count);
      }
      const query = panel.querySelector?.('input[name="search"]')?.value || '';
      const selection = this.view.state?.selection?.main || {};
      count.textContent = codeMirrorSearchMatchSummary(
        this.view.state?.doc?.toString?.() || '',
        query,
        selection,
        codeMirrorSearchCheckboxState(panel),
      ).text;
    }

    destroy() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.bindPanel(null);
    }
  });
}

function openCodeMirrorFindForView(api, view) {
  if (!api?.openSearchPanel || !view) return false;
  view.focus?.();
  return api.openSearchPanel(view);
}

function updateCodeMirrorCursorStatus(panel) {
  const view = panel?._cmView;
  const status = panel?.querySelector?.('.file-editor-cursor-status');
  if (!view || !status) return;
  const main = view.state.selection.main;
  const line = view.state.doc.lineAt(main.head);
  const column = main.head - line.from + 1;
  const selectedChars = view.state.selection.ranges.reduce((sum, range) => sum + Math.abs(range.to - range.from), 0);
  const selections = view.state.selection.ranges.length;
  const selectionText = selectedChars ? ` · ${selections} sel · ${selectedChars} chars` : '';
  status.textContent = `${line.number}:${column}${selectionText}`;
}

function codeMirrorExtensions(api, panel, path, options = {}) {
  const save = options.save || (() => saveFileEditor(path, panel));
  const saveKeymap = api.keymap.of([{
    key: 'Mod-s',
    run() {
      save();
      return true;
    },
  }]);
  const findKeymap = api.openSearchPanel ? api.keymap.of([{
    key: 'Mod-f',
    run(view) {
      return openCodeMirrorFindForView(api, view);
    },
  }]) : [];
  const powerKeymap = api.keymap.of([
    api.openSearchPanel ? {
      key: 'Mod-h',
      run(view) {
        return openCodeMirrorFindForView(api, view);
      },
    } : null,
    api.gotoLine ? {
      key: 'Mod-g',
      run(view) {
        return api.gotoLine(view);
      },
    } : null,
    api.toggleComment ? {
      key: 'Mod-/',
      run(view) {
        return api.toggleComment(view);
      },
    } : null,
    api.indentLess ? {
      key: 'Shift-Tab',
      run(view) {
        return api.indentLess(view);
      },
    } : null,
    api.indentMore ? {
      key: 'Tab',
      run(view) {
        return api.indentMore(view);
      },
    } : null,
  ].filter(Boolean));
  return [
    api.history(),
    api.drawSelection(),
    api.dropCursor(),
    api.rectangularSelection(),
    api.crosshairCursor(),
    api.indentOnInput(),
    api.bracketMatching(),
    api.foldGutter(),
    api.highlightActiveLine(),
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    api.search({top: true}),
    codeMirrorSearchPanelEnhancementExtension(api),
    codeMirrorSearchScrollFix(api),
    api.highlightSelectionMatches(),
    saveKeymap,
    findKeymap,
    powerKeymap,
    api.keymap.of([api.indentWithTab, ...api.defaultKeymap, ...api.historyKeymap, ...api.searchKeymap]),
    codeMirrorBlameExtension(api, path),
    api.EditorState.readOnly.of(readOnlyMode),
    api.EditorView.editable.of(!readOnlyMode),
    ...(options.plain ? [codeMirrorThemeOnlyExtensions(api, panel)] : [codeMirrorLanguageExtension(api, path), codeMirrorThemedExtensions(api, panel, path)]),
  ];
}

async function removeOpenFile(path, options = {}) {
  const confirmDirty = options.confirmDirty !== false;
  const shouldRender = options.render !== false;
  if (!path || !openFiles.has(path)) return;
  const state = openFiles.get(path);
  const requestedItem = options.item && fileItemPath(options.item) === path ? options.item : null;
  const closePanel = requestedItem ? panelNodes.get(requestedItem) : fileEditorPanelsForPath(path)[0];
  if (confirmDirty && state?.dirty && !(await confirmDirtyFileClose(path, closePanel))) return false;
  const items = requestedItem ? [requestedItem] : filePanelItemsForPath(path);
  if (!items.length) return false;
  clearFileAutosaveTimer(path);
  let nextSlots = layoutSlots;
  let wasInLayout = false;
  for (const item of items) {
    if (itemInLayout(item, nextSlots)) {
      nextSlots = layoutWithoutItemFromSlots(item, nextSlots, {preserveRemovedSlot: true});
      wasInLayout = true;
    }
    removeFilePanelOwner(path, item);
    removePanelForItem(item);
  }
  if (!openFilePathHasOwner(path)) {
    deleteFileState(path);
  }
  syncFileLayoutItems();
  if (activeFile === path && !openFilePathHasOwner(path)) {
    const remaining = Array.from(openFiles.keys());
    activeFile = remaining[remaining.length - 1] || null;
  }
  updateFileExplorerCurrentFileHighlight();
  if (wasInLayout) applyLayoutSlots(nextSlots);
  if (shouldRender) renderSessionButtons();
  return true;
}

function closeFileTab(path, options = {}) {
  return removeOpenFile(path, options);
}

function layoutWithReplacedItem(oldItem, newItem) {
  return layoutWithReplacedItems(new Map([[oldItem, newItem]]));
}

function layoutWithReplacedItems(replacements) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (paneIsPlaceholder(side)) {
      next[side] = emptyPlaceholderPaneState();
      continue;
    }
    const tabs = paneTabs(side).map(item => replacements.get(item) || item);
    const activeItem = activeItemForSide(side);
    const active = replacements.get(activeItem) || activeItem;
    next[side] = paneStateWithTabs(tabs, active);
  }
  return compactLayoutSlots(next);
}

function renameOpenFilePath(oldPath, newPath) {
  if (!oldPath || !newPath || oldPath === newPath || !openFiles.has(oldPath)) return;
  const oldItem = fileEditorItemFor(oldPath);
  const newItem = fileEditorItemFor(newPath);
  const oldPreviewItem = filePreviewItemFor(oldPath);
  const newPreviewItem = filePreviewItemFor(newPath);
  const state = fileStateFor(oldPath);
  const wasInLayout = itemInLayout(oldItem) || itemInLayout(oldPreviewItem);
  const panelItems = [oldItem, oldPreviewItem].filter(item => panelNodes.has(item));
  deleteFileState(oldPath);
  setFileState(newPath, state);
  if (state.editorTabItems.delete(oldItem)) state.editorTabItems.add(newItem);
  if (state.previewTabItems.delete(oldPreviewItem)) state.previewTabItems.add(newPreviewItem);
  const viewModes = state.viewMode;
  for (const [oldKey, newKey] of [[oldItem, newItem], [oldPreviewItem, newPreviewItem]]) {
    if (viewModes.has(oldKey)) {
      viewModes.set(newKey, viewModes.get(oldKey));
      viewModes.delete(oldKey);
    }
    // DOIT.6 #73: migrate the item-keyed scroll/selection state and the LRU timestamp on rename too,
    // so they don't leak under the old item id (and the LRU ordering survives the rename).
    if (fileEditorViewState.has(oldKey)) {
      fileEditorViewState.set(newKey, fileEditorViewState.get(oldKey));
      fileEditorViewState.delete(oldKey);
    }
    if (tabLastActivatedAt.has(oldKey)) {
      tabLastActivatedAt.set(newKey, tabLastActivatedAt.get(oldKey));
      tabLastActivatedAt.delete(oldKey);
    }
  }
  if (viewModes.has(oldPath)) {
    viewModes.set(newPath, viewModes.get(oldPath));
    viewModes.delete(oldPath);
  }
  for (const item of panelItems) {
    const panel = panelNodes.get(item);
    if (panel) panel.remove();
    panelNodes.delete(item);
  }
  if (activeFile === oldPath) activeFile = newPath;
  syncFileLayoutItems();
  if (wasInLayout) applyLayoutSlots(layoutWithReplacedItems(new Map([[oldItem, newItem], [oldPreviewItem, newPreviewItem]])), {focusSession: newItem});
  else {
    renderSessionButtons();
    renderPaneTabStrips();
    updateFileExplorerCurrentFileHighlight();
  }
}

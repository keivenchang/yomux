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

async function resolveFileExplorerDirectoryRoot(path, options = {}) {
  const requested = normalizeDirectoryPath(expandUserPath(path));
  try {
    // A directory listing is the fastest useful answer for an unverified root. The old order
    // performed a full INFO batch first, which made opening a cold repository wait for Git-aware
    // metadata before the user saw even its plain directory rows. A file path cannot be listed;
    // only that case falls through to INFO so it can resolve to the containing directory.
    const entries = await fetchDirectory(requested, {
      fresh: true,
      user: options.user === true || options.manualSelection === true,
      enrich: false,
      trigger: options.trigger,
    });
    if (entries) return {root: requested, entries};
    const info = await fetchFilePathInfo(requested, {fresh: true, force: true, user: options.user === true || options.manualSelection === true});
    if (info?.kind === 'dir') {
      const key = fileExplorerFsBatchKey('list', requested);
      const record = fileExplorerFsResourceRecords.get(key);
      if (record?.failureStatus) invalidateFileExplorerFsResourceRecord(key, record);
      return {root: requested, entries: null};
    }
    if (info?.kind === 'file') {
      markFileExplorerDirectoryDemandNegative(requested, 400);
      retireFileExplorerDirectoryDemand(requested);
      return {root: dirnameOf(requested), entries: null};
    }
    const error = new Error(t('fs.error.notDirectory', {path: requested}));
    error.status = 400;
    error.payload = {user_message: {key: 'fs.error.notDirectory', params: {path: requested}, fallback: error.message}};
    setFileExplorerListError(requested, error, 400);
    return {root: '', entries: null};
  } catch (error) {
    setFileExplorerListError(requested, error, Number(error?.status) || 0);
    return {root: '', entries: null};
  }
}

async function openFileExplorerAt(path, options = {}) {
  const observabilityJourney = options.observabilityJourney || newFinderUsableJourney();
  if (options.manualSelection === true) {
    cancelPendingFileExplorerActiveSync();
  }
  const syncGeneration = fileExplorerSyncState.generation;
  const openGeneration = fileWorkspaceState.beginOpen();
  const openStillCurrent = () => fileWorkspaceState.openIsCurrent(openGeneration);
  const requestedRoot = normalizeDirectoryPath(expandUserPath(path));
  const showPendingRoot = options.manualSelection === true || options.showPending === true;
  // Claim an explicit path before the kind probe. The probe is the fastest validation available,
  // but it is still asynchronous; leaving the previous tree mounted until it completes makes a
  // slow path look like the typed request was ignored.
  if (showPendingRoot) {
    setFileExplorerSelectionPin(options.manualSelection === true);
    setFileExplorerPathDisplay(requestedRoot, {deferRepoInfo: true});
    renderFileExplorerRootModeControls();
    renderFileExplorerTreeSearching(requestedRoot);
  }
  const validateKind = options.validateKind === true
    || (layoutUrlState.finderRootKindUnverified && requestedRoot === normalizeDirectoryPath(fileExplorerRoot || ''));
  const resolvedRoot = validateKind
    ? await resolveFileExplorerDirectoryRoot(requestedRoot, options)
    : {root: requestedRoot, entries: null};
  const root = resolvedRoot.root;
  if (!root || !openStillCurrent()) return false;
  if (!showPendingRoot && options.syncSelection === true) {
    setFileExplorerSelectionPin(false);
  }
  const entries = Array.isArray(resolvedRoot.entries)
    ? resolvedRoot.entries
    : await fetchDirectory(root, {
      user: options.user === true || options.manualSelection === true,
      trigger: options.trigger,
      enrichRoot: true,
    });
  if (!openStillCurrent()) return false;
  if (options.manualSelection !== true && !showPendingRoot
      && syncGeneration !== fileExplorerSyncState.generation) return false;
  if (!entries) {
    const error = currentFileExplorerListError(root);
    if (error) setFileExplorerPathDisplay(root, {error});
    if (showPendingRoot) renderFileExplorerTreeStatus(error || t('preview.openFailed', {path: root}), {root, error: true});
    return false;
  }
  // Passive startup listings join the current target instead of replacing its render owner.
  if (options.manualSelection !== true && !showPendingRoot && root === currentFileExplorerRoot()) {
    const publication = activeFileExplorerSyncPublication(root);
    if (publication) return publication.publish(new Map([[root, entries]]));
  }
  const fastPaintGeneration = beginFinderSessionFilesDiffBackfill();
  const previousExpanded = options.preserveExpanded ? Array.from(fileExplorerExpanded) : [];
  const scrollPositions = options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  const rootChanged = root !== currentFileExplorerRoot();
  fileExplorerRoot = root;
  layoutUrlState.finderRootKindUnverified = false;
  if (rootChanged) pruneFileExplorerSelectionForRoot(fileExplorerRoot);
  if (options.manualSelection === true) setFileExplorerSelectionPin(true);
  if (options.syncSelection !== true) cancelPendingFileExplorerActiveSync({invalidateOpen: false});
  setFileExplorerPathDisplay(fileExplorerRoot, {deferRepoInfo: true});
  renderFileExplorerRootModeControls();
  fileExplorerExpanded.clear();
  if (fileExplorerTree) {
    renderTreeChildren(fileExplorerTree, fileExplorerRoot, entries, 0, {view: 'finder', deferSessionFilesDiff: true});
    scheduleFinderUsableObservation(fileExplorerTree, entries, observabilityJourney);
  }
  if (Array.isArray(resolvedRoot.entries)) {
    scheduleFileExplorerRepoInfoEnrichment(fileExplorerRoot, entries, {includeRoot: true});
  }
  if (options.refreshPanels !== false) {
    await refreshFileExplorerPanelTrees({root: fileExplorerRoot, entries, restoreState: false, observabilityJourney, deferSessionFilesDiff: true});
    if (!openStillCurrent()) return false;
    scheduleAfterFileExplorerFirstPaint(() => {
      if (!openStillCurrent()) return;
      finishFinderFastPaint(fastPaintGeneration);
    });
  }
  if (previousExpanded.length) {
    await restoreFileExplorerExpandedPaths(previousExpanded, fileExplorerRoot);
    if (!openStillCurrent()) return false;
  }
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
  updateFileExplorerCurrentFileHighlight({yieldToInput: true});
  return true;
}

function resetFileExplorerAppliedSyncPlan() {
  fileExplorerSyncState.appliedPlanKey = '';
}

async function saveFileExplorerRootMode(mode) {
  if (readOnlyMode) return;
  try {
    await saveSettingsPatch(settingPatch('file_explorer.root_mode', mode));
  } catch (_) {}
}

// Short-TTL cache of directory listings. A live Differ/Finder of a busy session re-renders many times a
// second and each render walks every expanded dir, which without this fans out into a /api/fs/fast/list storm
// (one request per dir per render — the cause of the 8001 fs/list loop). Repeated fetches of the same dir
// within the TTL reuse the listing; the change-detection sweep and explicit reloads pass {fresh:true}.
const fileExplorerFsResourceRecords = new Map();
let fileExplorerFsResourceRevision = 0;
const fileExplorerFsNegativeBackoffBaseMs = 30_000;
const fileExplorerFsNegativeBackoffMaxMs = 5 * 60_000;
const fileExplorerFsBatchQueue = [];
const fileExplorerFsBatchPending = new Map();
const fileExplorerFsBatchOperations = new Map();
const fileExplorerDeletedPathTombstones = new Set();
const fileExplorerDeletedPathRevisions = new Map();
const fileExplorerRepoInfoEnrichmentState = {
  pending: new Set(),
  inFlight: new Set(),
  resolved: new Set(),
  watchDiffOwned: new Set(),
  globalGeneration: 0,
  pathGenerations: new Map(),
  nextGeneration: 1,
  frame: null,
};
// Git/path INFO can spend real subprocess time per directory. Keep each deferred wave small enough
// to finish inside the browser operation watchdog, then schedule the remaining visible rows in later
// frames. This is a UI-work budget, distinct from the server's larger request-refusal ceiling.
const fileExplorerRepoInfoEnrichmentChunkLimit = 8;
// A generation can publish before its matching 202 reaches the browser. Keep
// the bounded completion so the queued item can repair immediately on arrival.
const fileExplorerFsBatchReadyGenerations = new Map();
const fileExplorerFsBatchDelayMs = 8;
// The bounds /api/fs/batch refuses above are SERVER facts, and the server states them in the boot
// payload (`filesystemBatchLimits` in yomux_lib/web.py) exactly the way it states maxSessionTabs.
// `filesystem.MAX_BATCH_REQUESTS` stays the one copy: the server refuses a body above it with a 400
// invalid_request, its own watch-batch producer chunks at it (`submit_filesystem_watch_batches`),
// and this flush splits at it. A literal here would be a fourth copy free to drift from all three.
// A payload that does not state a bound is a server this bundle cannot make a bounded promise
// about, so it falls back to the only size no server can refuse for being too large: one item.
function fileExplorerServerStatedLimit(value, unstated) {
  return Number.isSafeInteger(value) && value > 0 ? value : unstated;
}
const fileExplorerFsBatchLimits = (typeof bootstrap === 'object' && bootstrap?.filesystemBatchLimits) || {};
const fileExplorerFsBatchRequestLimit = fileExplorerServerStatedLimit(fileExplorerFsBatchLimits.maxRequests, 1);
const fileExplorerFsBatchTriggerCountLimit = fileExplorerServerStatedLimit(fileExplorerFsBatchLimits.triggerCountLimit, 1);
let fileExplorerFsBatchSeq = 0;
let fileExplorerFsBatchTimer = null;
const FILE_EXPLORER_FS_BATCH_TRIGGERS = new Set([
  'tree-render', 'explicit-user', 'fresh-repair', 'watch-diff-fallback', 'deferred-interaction', 'sync-revalidation', 'repo-enrichment',
]);
const FILE_EXPLORER_FS_BATCH_TERMINAL_OWNERS = new Set(['filesystem-watch-diff-refresh']);
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
const FILE_TREE_TOUCH_CLICK_DEDUP_MS = 1000;

function armFileTreeTouchClickSuppression(row) {
  row.__fileTreeTouchClickSuppressions = (Number(row.__fileTreeTouchClickSuppressions) || 0) + 1;
  setTimeout(() => {
    row.__fileTreeTouchClickSuppressions = Math.max(0, (Number(row.__fileTreeTouchClickSuppressions) || 0) - 1);
  }, FILE_TREE_TOUCH_CLICK_DEDUP_MS);
}

function consumeFileTreeTouchClickSuppression(row, event) {
  const generatedByTouch = event?.sourceCapabilities?.firesTouchEvents === true
    || (row.__fileTreeLastPointerType === 'touch' && event?.detail === 1);
  const pending = Number(row.__fileTreeTouchClickSuppressions) || 0;
  if (!generatedByTouch || pending <= 0) return false;
  row.__fileTreeTouchClickSuppressions = pending - 1;
  return true;
}

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
  else {
    const text = String(value);
    // Keep metadata refreshes from generating redundant DOM mutations.
    if (row.dataset[key] !== text) row.dataset[key] = text;
  }
}

function clearFileTreeRowHandlers(row) {
  for (const key of FILE_TREE_ROW_HANDLERS) row[key] = null;
}

function setTreeItemAria(row, {selected = false, expandable = false, expanded = false} = {}) {
  setAttributeIfChanged(row, 'role', 'treeitem');
  setAttributeIfChanged(row, 'aria-selected', selected ? 'true' : 'false');
  setAttributeIfChanged(row, 'aria-expanded', expandable ? (expanded ? 'true' : 'false') : null);
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
  const source = error?.payload && typeof error.payload === 'object' ? error.payload : error;
  const fallback = String(error?.message || error || '');
  fileExplorerPathError = userMessageText(source, fallback);
  fileExplorerLastListError = root && fileExplorerPathError
    ? {path: root, status, source, fallback, network: !status}
    : null;
}

function handleFileExplorerDirectoryListFailure(path, error, status = Number(error?.status) || 0) {
  const root = normalizeDirectoryPath(path || '');
  if (!root) return;
  const openFailed = t('preview.openFailed', {path: root});
  fileExplorerPathError = status
    ? error?.message || `${openFailed} (${status})`
    : `${openFailed}: ${error}`;
  setFileExplorerListError(root, error, status);
  if (fileExplorerDirectoryDemandTerminalError(error)) retireFileExplorerDirectoryDemand(root);
}

function currentFileExplorerListError(path) {
  const root = normalizeDirectoryPath(path || '');
  return root && fileExplorerLastListError && normalizeDirectoryPath(fileExplorerLastListError.path || '') === root
    ? userMessageText(fileExplorerLastListError.source, fileExplorerLastListError.fallback)
    : '';
}

function fileExplorerFsCacheTtlMs() {
  return Math.max(0, numberSetting('file_explorer.dir_cache_ms', 5000) || 0);
}

function fileExplorerFsBatchKey(type, path) {
  return `${type}\x1f${normalizeDirectoryPath(path)}`;
}

function fileExplorerFsResourceRecord(type, path) {
  const key = fileExplorerFsBatchKey(type, path);
  let record = fileExplorerFsResourceRecords.get(key);
  if (!record) {
    record = {
      value: undefined,
      storedAt: 0,
      revision: 0,
      request: null,
      requestUserInitiated: false,
      generation: 0,
      failureCount: 0,
      failureStatus: 0,
      retryAt: 0,
    };
    setLimitedMapEntry(fileExplorerFsResourceRecords, key, record, fileExplorerMemoryCacheLimit);
  }
  return {key, record};
}

function clearFileExplorerFsResourceFailure(record) {
  record.failureCount = 0;
  record.failureStatus = 0;
  record.retryAt = 0;
}

function recordFileExplorerFsResourceFailure(record, status) {
  record.failureCount = Math.min(32, Math.max(0, Number(record.failureCount) || 0) + 1);
  record.failureStatus = Number(status) || 0;
  const exponent = Math.min(4, record.failureCount - 1);
  record.retryAt = Date.now() + Math.min(fileExplorerFsNegativeBackoffMaxMs, fileExplorerFsNegativeBackoffBaseMs * (2 ** exponent));
}

function markFileExplorerDirectoryDemandNegative(path, status) {
  const {record} = fileExplorerFsResourceRecord('list', path);
  record.generation += 1;
  record.value = undefined;
  record.storedAt = 0;
  record.request = null;
  recordFileExplorerFsResourceFailure(record, status);
}

function fileExplorerFsResourceCurrent(key, record, generation) {
  return fileExplorerFsResourceRecords.get(key) === record && record.generation === generation;
}

function setFileExplorerFsResourceValue(type, path, value) {
  const {record} = fileExplorerFsResourceRecord(type, path);
  record.generation += 1;
  record.request = null;
  record.value = value;
  record.storedAt = Date.now();
  record.revision = ++fileExplorerFsResourceRevision;
  clearFileExplorerFsResourceFailure(record);
  if (type === 'list') clearFileExplorerListError(path);
  return value;
}

function bumpFileExplorerDeletedPathRevision(path) {
  setLimitedMapEntry(fileExplorerDeletedPathRevisions, path, ++fileExplorerFsResourceRevision, fileExplorerMemoryCacheLimit);
}

function markFileExplorerDeletedPaths(paths = []) {
  for (const path of paths) {
    const normalized = normalizeDirectoryPath(path);
    if (!normalized) continue;
    fileExplorerDeletedPathTombstones.add(normalized);
    bumpFileExplorerDeletedPathRevision(normalized);
  }
}

function reconcileFileExplorerDeletedPathTombstones(directory, entries, authoritative = false) {
  if (!authoritative || !Array.isArray(entries)) return;
  const normalizedDirectory = normalizeDirectoryPath(directory);
  const listed = new Set(entries.map(entry => childPath(normalizedDirectory, entry?.name)));
  for (const path of Array.from(fileExplorerDeletedPathTombstones)) {
    if (dirnameOf(path) === normalizedDirectory && !listed.has(path) && fileExplorerDeletedPathTombstones.delete(path)) {
      bumpFileExplorerDeletedPathRevision(path);
    }
  }
}

function clearFileExplorerDeletedPaths(paths = []) {
  for (const path of paths) {
    const normalized = normalizeDirectoryPath(path);
    if (normalized && fileExplorerDeletedPathTombstones.delete(normalized)) bumpFileExplorerDeletedPathRevision(normalized);
  }
}

function filterFileExplorerDeletedPathTombstones(directory, entries) {
  return drainFileTreeRenderSteps(filterFileExplorerDeletedPathTombstonesSteps(directory, entries));
}

function* filterFileExplorerDeletedPathTombstonesSteps(directory, entries, options = {}) {
  if (!Array.isArray(entries) || !fileExplorerDeletedPathTombstones.size) return entries;
  const normalizedDirectory = normalizeDirectoryPath(directory);
  const visible = [];
  for (const entry of entries) {
    if (options.isCurrent && !options.isCurrent()) return visible;
    if (!fileExplorerDeletedPathTombstones.has(childPath(normalizedDirectory, entry?.name))) visible.push(entry);
    yield;
  }
  return visible;
}

function removeFileTreePathsImmediately(paths = []) {
  const deleted = paths.map(path => normalizeDirectoryPath(path)).filter(Boolean);
  if (!deleted.length) return;
  // Child wrappers are siblings of their rows; detach both so yielded renders lose their owner.
  for (const node of document.querySelectorAll('.file-tree-row[data-path], .file-tree-children[data-parent]')) {
    const path = normalizeDirectoryPath(node.dataset.path || node.dataset.parent || '');
    if (deleted.some(root => path === root || pathIsInsideDirectory(path, root))) node.remove();
  }
}

function fileExplorerFsResourceHasValue(type, path) {
  const record = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey(type, path));
  return record?.value !== undefined;
}

function cachedFileExplorerFsResourceValue(type, path) {
  const key = fileExplorerFsBatchKey(type, path);
  const record = fileExplorerFsResourceRecords.get(key);
  if (!record || record.value === undefined) return undefined;
  // Cache reads on the session-switch hot path count as use for the existing bounded LRU.
  setLimitedMapEntry(fileExplorerFsResourceRecords, key, record, fileExplorerMemoryCacheLimit);
  return record.value;
}

function fileExplorerDirectoryListingFailed(path) {
  const record = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', path));
  return Number(record?.failureCount) > 0 || Boolean(currentFileExplorerListError(path));
}

function fileExplorerListingFailureIsCurrent(path, generation) {
  const record = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', path));
  return record ? record.generation === generation && !record.request : generation === undefined;
}

function invalidateFileExplorerFsResourceRecord(key, record) {
  record.generation += 1;
  record.value = undefined;
  record.storedAt = 0;
  record.request = null;
  clearFileExplorerFsResourceFailure(record);
  if (fileExplorerFsResourceRecords.get(key) === record) fileExplorerFsResourceRecords.delete(key);
}

function requestFileExplorerFsResource(type, path, options, makeRequest, lifecycle = {}) {
  const normalized = normalizeDirectoryPath(path);
  const canReuse = options.fresh !== true;
  const {key, record} = fileExplorerFsResourceRecord(type, normalized);
  const cacheTtlMs = fileExplorerFsCacheTtlMs();
  if (canReuse && cacheTtlMs > 0 && record.value !== undefined && Date.now() - record.storedAt < cacheTtlMs) {
    lifecycle.onReuse?.(record.value);
    return Promise.resolve(record.value);
  }
  if (options.user !== true && record.retryAt > Date.now()) {
    lifecycle.onNegativeReuse?.(record);
    return Promise.resolve(Object.prototype.hasOwnProperty.call(lifecycle, 'errorValue') ? lifecycle.errorValue : null);
  }
  // A Finder click must not wait behind an unrelated bootstrap/refresh request for the same
  // directory.  Give it one successor batch; later clicks share that user-owned request so this
  // is a priority handoff, not an unbounded duplicate-request path.
  const supersedeBackgroundRequest = options.user === true && record.request && record.requestUserInitiated !== true;
  if ((canReuse || lifecycle.coalesceFresh === true) && record.request && !supersedeBackgroundRequest) {
    lifecycle.onCoalesce?.();
    return record.request;
  }
  if (lifecycle.skipRequest?.() === true) {
    lifecycle.onSkip?.();
    return Promise.resolve(lifecycle.skipValue);
  }
  if (type === 'list' && options.user === true) {
    clearFileExplorerFsResourceFailure(record);
    clearFileExplorerListError(normalized);
  }
  record.generation += 1;
  const generation = record.generation;
  let request = null;
  request = (async () => {
    try {
      const value = await makeRequest(supersedeBackgroundRequest ? {...options, dedupe: false} : options);
      if (fileExplorerFsResourceCurrent(key, record, generation) && value !== undefined && value !== null) {
        record.value = value;
        record.storedAt = Date.now();
        record.revision = ++fileExplorerFsResourceRevision;
        clearFileExplorerFsResourceFailure(record);
        lifecycle.onPublish?.(value);
      }
      return value;
    } catch (error) {
      if (fileExplorerFsResourceCurrent(key, record, generation)) {
        const negativeStatus = Number(lifecycle.negativeStatus?.(error)) || 0;
        if (type === 'list') recordFileExplorerFsResourceFailure(record, negativeStatus || Number(error?.status) || 0);
        lifecycle.onError?.(error);
      }
      if (Object.prototype.hasOwnProperty.call(lifecycle, 'errorValue')) return lifecycle.errorValue;
      throw error;
    } finally {
      if (fileExplorerFsResourceCurrent(key, record, generation) && record.request === request) record.request = null;
    }
  })();
  record.request = request;
  record.requestUserInitiated = options.user === true;
  return request;
}

function fileExplorerFsBatchSingleUrl(type, path) {
  const normalized = normalizeDirectoryPath(path);
  return type === 'list'
    ? `/api/fs/list?path=${encodeURIComponent(normalized)}`
    : `/api/fs/info?path=${encodeURIComponent(normalized)}`;
}

function suppressBackgroundFilesystemFetch(options = {}) {
  if (options.force === true || options.user === true) return false;
  if (document.visibilityState === 'hidden') return true;
  if (!fileExplorerIsOpen()) return true;
  return clientPushConnectedForData() && !fileExplorerUserIsActive();
}

function scheduleFileExplorerFsBatchFlush() {
  if (fileExplorerFsBatchTimer) return;
  fileExplorerFsBatchTimer = setTimeout(flushFileExplorerFsBatch, fileExplorerFsBatchDelayMs);
}

function fileExplorerFsBatchTrigger(options = {}) {
  const requested = String(options.trigger || '');
  if (FILE_EXPLORER_FS_BATCH_TRIGGERS.has(requested)) return requested;
  if (options.user === true) return 'explicit-user';
  return options.fresh === true ? 'fresh-repair' : 'tree-render';
}

function recordFileExplorerFsBatchTrigger(item, trigger) {
  const count = Number(item.triggerCounts[trigger]) || 0;
  item.triggerCounts[trigger] = Math.min(fileExplorerFsBatchTriggerCountLimit, count + 1);
}

function recordFileExplorerFsBatchTerminalOwner(item, owner) {
  const normalized = String(owner || '');
  if (!FILE_EXPLORER_FS_BATCH_TERMINAL_OWNERS.has(normalized)) return;
  item.terminalOwners.add(normalized);
}

function fileExplorerFsBatchTerminalOwners(items) {
  const owners = new Set();
  for (const item of items) {
    for (const owner of item?.terminalOwners || []) owners.add(owner);
  }
  return owners;
}

function fileExplorerFsBatchOwnershipState(owner) {
  const normalized = String(owner || '');
  const owned = item => item?.terminalOwners?.has(normalized) === true;
  const operations = Array.from(fileExplorerFsBatchOperations.entries())
    .filter(([, items]) => Array.isArray(items) && items.some(owned));
  return {
    queued: fileExplorerFsBatchQueue.filter(owned).length,
    pending: Array.from(fileExplorerFsBatchPending.values()).filter(value => owned(value?.item)).length,
    operations: operations.length,
    operationIds: operations.map(([operationId]) => operationId).sort(),
  };
}

function recordPendingFileExplorerFsBatchTrigger(type, path, options = {}) {
  const pending = fileExplorerFsBatchPending.get(fileExplorerFsBatchKey(type, normalizeDirectoryPath(path)));
  if (pending?.item && pending.item.sent !== true) recordFileExplorerFsBatchTrigger(pending.item, fileExplorerFsBatchTrigger(options));
}

function fileExplorerFsBatchClientMetadata() {
  const revision = String((typeof bootstrap === 'object' && bootstrap?.clientRevision) || '');
  const watchToken = String(fileExplorerFilesystemWatchToken || '');
  return {
    client_revision: /^[A-Za-z0-9._-]{1,80}$/.test(revision) ? revision : '',
    client_scope: 'browser',
    watch_token: watchToken.slice(0, 128),
  };
}

function fetchFilesystemBatchItem(type, path, options = {}) {
  const normalized = normalizeDirectoryPath(path);
  const key = fileExplorerFsBatchKey(type, normalized);
  if (options.dedupe !== false) {
    const existing = fileExplorerFsBatchPending.get(key);
    if (existing) {
      if (existing.item.sent !== true) recordFileExplorerFsBatchTrigger(existing.item, fileExplorerFsBatchTrigger(options));
      recordFileExplorerFsBatchTerminalOwner(existing.item, options.terminalOwner);
      return existing.promise;
    }
  }
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  const trigger = fileExplorerFsBatchTrigger(options);
  const item = {id: ++fileExplorerFsBatchSeq, type, path: normalized, triggerCounts: {[trigger]: 1}, terminalOwners: new Set(), key, promise, resolve, reject, sent: false};
  recordFileExplorerFsBatchTerminalOwner(item, options.terminalOwner);
  if (options.dedupe !== false) fileExplorerFsBatchPending.set(key, {promise, item});
  fileExplorerFsBatchQueue.push(item);
  if (options.immediate === true) void flushFileExplorerFsBatch();
  else scheduleFileExplorerFsBatchFlush();
  return promise;
}

function fileExplorerFsBatchQueuedProduct(response = {}) {
  const pending = apiPendingResponseFromNestedEnvelope(response);
  return pending ? {ticket: pending.ticket, key: pending.key, generation: 0} : null;
}

function applyFileExplorerFsBatchOperationResult(record, result = {}) {
  const operationId = String(record?.id || '');
  const items = fileExplorerFsBatchOperations.get(operationId);
  if (!operationId || !Array.isArray(items)) return false;
  fileExplorerFsBatchOperations.delete(operationId);
  if (result.state === 'ready' && Array.isArray(result.data?.responses)) {
    const responses = new Map(result.data.responses.map(response => [response?.id, response]));
    for (const item of items) {
      settleFileExplorerFsBatchItem(item, responses.get(item.id) || {ok: false, status: 500, error: t('common.requestFailed')});
    }
    return true;
  }
  const error = new Error(userMessageText(result, t('common.requestFailed')));
  error.status = Number(result?.error?.status || 0);
  error.payload = result;
  for (const item of items) rejectFileExplorerFsBatchItem(item, error);
  return true;
}

function acceptFileExplorerFsBatchOperation(items, error) {
  if (!isApiPendingResponse(error) || !error.operationId) return false;
  fileExplorerFsBatchOperations.set(error.operationId, items);
  const record = apiOperationState.records.get(error.operationId);
  const terminalOwners = fileExplorerFsBatchTerminalOwners(items);
  if (record && terminalOwners.size) {
    record.terminalOwners = terminalOwners;
    if (terminalOwners.size === 1) record.terminalOwner = terminalOwners.values().next().value;
  }
  const terminal = apiOperationState.terminal.get(error.operationId);
  if (terminal?.result) {
    applyFileExplorerFsBatchOperationResult({id: error.operationId, kind: 'fs_batch'}, terminal.result);
  }
  return true;
}

function settleFileExplorerFsBatchItem(item, response) {
  const queuedProduct = fileExplorerFsBatchQueuedProduct(response);
  if (queuedProduct) {
    const previousQueuedProduct = item.queuedProduct;
    if (previousQueuedProduct && apiGenerationReadyMatchesKey(previousQueuedProduct.key, queuedProduct)) {
      queuedProduct.generation = previousQueuedProduct.generation;
    }
    item.queuedProduct = queuedProduct;
    item.repairing = false;
    if (!fileExplorerFsBatchPending.has(item.key)) fileExplorerFsBatchPending.set(item.key, {promise: item.promise, item});
    const generation = fileExplorerFsBatchReadyGenerations.get(apiGenerationReadyKey(queuedProduct.key));
    if (Number.isSafeInteger(generation) && generation > 0) {
      handleFileExplorerFsBatchGenerationReady({...queuedProduct, generation});
    }
    return;
  }
  if (fileExplorerFsBatchPending.get(item.key)?.item === item) {
    fileExplorerFsBatchPending.delete(item.key);
  }
  item.queuedProduct = null;
  item.repairing = false;
  if (response?.ok) {
    item.resolve(response.payload || {});
    return;
  }
  const error = new Error(userMessageText(response, t('common.requestFailed')));
  error.status = Number(response?.status) || 0;
  error.payload = response || {};
  item.reject(error);
}

function rejectFileExplorerFsBatchItem(item, error) {
  if (fileExplorerFsBatchPending.get(item.key)?.item === item) fileExplorerFsBatchPending.delete(item.key);
  item.queuedProduct = null;
  item.repairing = false;
  item.reject(error);
}

async function fetchFileExplorerFsBatchSingleItem(item) {
  item.sent = true;
  try {
    const payload = await apiFetchJson(fileExplorerFsBatchSingleUrl(item.type, item.path));
    settleFileExplorerFsBatchItem(item, {ok: true, status: 200, payload});
    return {ok: true};
  } catch (error) {
    if (isApiPendingResponse(error)) {
      settleFileExplorerFsBatchItem(item, {
        ok: false,
        pending: true,
        status: error.status,
        payload: {status: 'QUEUED', ticket: error.ticket, key: error.key},
      });
      return {ok: true, pending: true};
    }
    rejectFileExplorerFsBatchItem(item, error);
    return {ok: false, error};
  }
}

async function drainFileExplorerFsBatchWithoutPush() {
  if (fileExplorerFsBatchTimer) clearTimeout(fileExplorerFsBatchTimer);
  fileExplorerFsBatchTimer = null;
  const queuedItems = fileExplorerFsBatchQueue.splice(0);
  const admittedItems = Array.from(fileExplorerFsBatchPending.values())
    .map(pending => pending?.item)
    .filter(item => item?.sent === true && item.queuedProduct && item.repairing !== true);
  for (const item of admittedItems) item.repairing = true;
  const items = [...queuedItems, ...admittedItems];
  if (!items.length) return {ok: true, failures: []};
  const results = await Promise.all(items.map(fetchFileExplorerFsBatchSingleItem));
  const failures = results.filter(result => result.ok !== true).map(result => result.error);
  return {ok: failures.length === 0, failures};
}

async function repairPendingFileExplorerFsBatchItem(item) {
  try {
    const request = {
      id: item.id,
      type: item.type,
      path: item.path,
      trigger_counts: {[fileExplorerFsBatchTrigger({fresh: true})]: 1},
    };
    const payload = await apiFetchJson('/api/fs/batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({requests: [request], ...fileExplorerFsBatchClientMetadata()}),
    });
    const response = Array.isArray(payload.responses) ? payload.responses.find(candidate => candidate?.id === item.id) : null;
    settleFileExplorerFsBatchItem(item, response || {ok: false, status: 500, error: t('common.requestFailed')});
  } catch (error) {
    if (acceptFileExplorerFsBatchOperation([item], error)) return;
    rejectFileExplorerFsBatchItem(item, error);
  } finally {
    if (fileExplorerFsBatchPending.get(item.key)?.item === item) item.repairing = false;
  }
}

function handleFileExplorerFsBatchGenerationReady(payload = {}) {
  const key = String(payload?.key || '');
  const generation = Number(payload?.generation);
  if (!key || !Number.isSafeInteger(generation) || generation < 1) return false;
  setLimitedMapEntry(fileExplorerFsBatchReadyGenerations, apiGenerationReadyKey(key), generation, fileExplorerMemoryCacheLimit);
  let handled = false;
  for (const pending of fileExplorerFsBatchPending.values()) {
    const item = pending?.item;
    const queued = item?.queuedProduct;
    if (!queued || !apiGenerationReadyMatchesKey(queued.key, payload) || generation <= queued.generation || item.repairing === true) continue;
    queued.generation = generation;
    item.repairing = true;
    handled = true;
    void repairPendingFileExplorerFsBatchItem(item);
  }
  return handled;
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('yomux:generation-ready', event => {
    handleFileExplorerFsBatchGenerationReady(event?.detail);
  });
}

function fileExplorerFsBatchChunks(items) {
  // Consecutive slices in queue order, like the server's own root partitioner: chunk n owns items
  // [offset, offset + size), nothing is reordered and nothing is dropped.
  const size = fileExplorerFsBatchRequestLimit;
  const chunks = [];
  for (let offset = 0; offset < items.length; offset += size) chunks.push(items.slice(offset, offset + size));
  return chunks;
}

async function postFileExplorerFsBatchChunk(items) {
  // One chunk settles its own items and NEVER throws: a chunk that fails must not decide anything
  // for the chunks around it, and every item it owns must end resolved, rejected, or admitted as a
  // queued operation. A promise left unsettled here hangs whatever awaited that directory listing.
  const requests = items.map(item => {
    item.sent = true;
    return {id: item.id, type: item.type, path: item.path, trigger_counts: item.triggerCounts};
  });
  try {
    const payload = await apiFetchJson('/api/fs/batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({requests, ...fileExplorerFsBatchClientMetadata()}),
    });
    const responses = new Map((payload.responses || []).map(response => [response.id, response]));
    for (const item of items) {
      settleFileExplorerFsBatchItem(item, responses.get(item.id) || {ok: false, status: 500, error: t('common.requestFailed')});
    }
    return {ok: true};
  } catch (error) {
    if (acceptFileExplorerFsBatchOperation(items, error)) return {ok: true, pending: true};
    for (const item of items) rejectFileExplorerFsBatchItem(item, error);
    return {ok: false};
  }
}

async function flushFileExplorerFsBatch() {
  fileExplorerFsBatchTimer = null;
  const items = fileExplorerFsBatchQueue.splice(0);
  if (!items.length) return {ok: true, chunks: 0};
  // The whole queue used to go into ONE body. Deferred Git enrichment can inspect more visible
  // paths than the server accepts in one batch; the server then refused the entire body with a 400
  // invalid_request. Chunks are posted in order, one at a time, so detail backfill cannot become an
  // unbounded simultaneous fan-out either.
  const results = [];
  for (const chunk of fileExplorerFsBatchChunks(items)) results.push(await postFileExplorerFsBatchChunk(chunk));
  return {
    ok: results.every(result => result.ok === true),
    chunks: results.length,
    ...(results.some(result => result.pending === true) ? {pending: true} : {}),
  };
}

async function fetchDirectory(path, options = {}) {
  const root = normalizeDirectoryPath(path);
  let listedEntries = [];
  let authoritative = false;
  return requestFileExplorerFsResource('list', root, options, async () => {
      if (options.enrich !== false) hydrateFileExplorerRepoInfoCache();
      // `fresh` bypasses the completed-value TTL; it must not multiply an
      // identical in-flight list request when several UI refresh owners fire
      // in the same render window. LIST is intentionally a one-level direct
      // request; only deferred detailed INFO work enters /api/fs/batch.
      const payload = await apiFetchJson(`/api/fs/fast/list?path=${encodeURIComponent(root)}`);
      const entries = payload.entries || [];
      listedEntries = entries;
      authoritative = payload.truncated !== true && (options.fresh === true || options.user === true);
      return filterFileExplorerDeletedPathTombstones(root, entries);
    }, {
      onReuse: entries => {
        clearFileExplorerListError(root);
        if (options.enrich !== false) scheduleFileExplorerRepoInfoEnrichment(root, entries, {
          includeRoot: options.enrichRoot === true,
          refresh: options.fresh === true,
        });
      },
      coalesceFresh: true,
      skipRequest: () => fileExplorerPushRefreshDepth > 0 || suppressBackgroundFilesystemFetch(options),
      onSkip: () => clearFileExplorerListError(root),
      skipValue: null,
      onPublish: entries => {
        reconcileFileExplorerDeletedPathTombstones(root, listedEntries, authoritative);
        clearFileExplorerListError(root);
        cacheFileExplorerRepoInfoEntries(root, entries);
        markNewDirectoryEntries(root, entries);
        if (options.recordSignature !== false) recordDirectorySignature(root, entries);
        if (options.enrich !== false) scheduleFileExplorerRepoInfoEnrichment(root, entries, {
          includeRoot: options.enrichRoot === true,
          refresh: options.fresh === true,
        });
      },
      onError: err => {
        const status = Number(err?.status) || 0;
        handleFileExplorerDirectoryListFailure(root, err, status);
        console.warn(status ? 'fs list failed' : 'fs list error', root, status || err, fileExplorerPathError);
      },
      negativeStatus: err => fileExplorerDirectoryDemandTerminalError(err) ? Number(err?.status) || 400 : 0,
      errorValue: null,
    });
}

function invalidateFileExplorerFsCaches() {
  const enrichmentState = fileExplorerRepoInfoEnrichmentState;
  enrichmentState.globalGeneration += 1;
  enrichmentState.pathGenerations.clear();
  enrichmentState.resolved.clear();
  for (const path of enrichmentState.inFlight) enrichmentState.pending.add(path);
  for (const [key, record] of Array.from(fileExplorerFsResourceRecords.entries())) {
    invalidateFileExplorerFsResourceRecord(key, record);
  }
  for (const [root, record] of Array.from(fileExplorerDirectoryRecords.entries())) {
    updateFileExplorerDirectoryRecord(root, {signature: undefined}, record);
  }
}

function invalidateFileExplorerRoots(roots = []) {
  const normalizedRoots = (Array.isArray(roots) ? roots : [])
    .map(root => String(root || '').trim())
    .filter(Boolean)
    .map(root => normalizeDirectoryPath(root));
  if (!normalizedRoots.length) return false;
  const shouldDrop = path => normalizedRoots.some(root => pathIsInsideDirectory(path, root));
  const enrichmentState = fileExplorerRepoInfoEnrichmentState;
  const enrichmentPaths = new Set([
    ...enrichmentState.pending,
    ...enrichmentState.inFlight,
    ...enrichmentState.resolved,
    ...enrichmentState.pathGenerations.keys(),
  ]);
  for (const path of enrichmentPaths) {
    if (!shouldDrop(path)) continue;
    enrichmentState.pathGenerations.set(path, enrichmentState.nextGeneration++);
    enrichmentState.resolved.delete(path);
    if (enrichmentState.inFlight.has(path)) enrichmentState.pending.add(path);
  }
  for (const [key, record] of Array.from(fileExplorerFsResourceRecords.entries())) {
    if (shouldDrop(normalizeDirectoryPath(key.split('\x1f').at(-1)))) invalidateFileExplorerFsResourceRecord(key, record);
  }
  for (const cache of [fileExplorerDirectoryRecords, fileExplorerNewEntryUntil]) {
    for (const key of Array.from(cache.keys())) if (shouldDrop(normalizeDirectoryPath(key))) cache.delete(key);
  }
  if (typeof invalidateTerminalFileReferenceTargets === 'function') invalidateTerminalFileReferenceTargets(normalizedRoots);
  return normalizedRoots.some(root => pathIsInsideDirectory(currentFileExplorerRoot(), root));
}

function entriesByDirFromFilesystemPush(payload = {}, options = {}) {
  const entriesByDir = new Map();
  const changedDirectories = [];
  const failedDirectories = new Map();
  const directories = Array.isArray(payload.directories) ? payload.directories : [];
  for (const item of directories) {
    const path = normalizeDirectoryPath(item?.path || item?.data?.path || '');
    const data = item?.data && typeof item.data === 'object' ? item.data : {};
    const entries = Array.isArray(data.entries) ? data.entries : null;
    reconcileFileExplorerDeletedPathTombstones(path, entries, data.truncated !== true && item.ok !== false && Number(item.status || 200) < 400);
    const visibleEntries = entries ? filterFileExplorerDeletedPathTombstones(path, entries) : null;
    if (!path) continue;
    if (item.ok === false || Number(item.status || 200) >= 400) {
      const status = Number(item.status) || Number(data.status) || 0;
      const error = item.error && typeof item.error === 'object'
        ? {...item.error, status: Number(item.error.status) || status}
        : {status, message: String(item.error || data.error || data.message || '')};
      markFileExplorerDirectoryDemandNegative(path, status || 0);
      handleFileExplorerDirectoryListFailure(path, error, status);
      failedDirectories.set(path, fileExplorerFsResourceRecord('list', path).record.generation);
      continue;
    }
    if (!visibleEntries) continue;
    const previousSignature = fileExplorerDirectoryRecord(path)?.signature;
    const nextSignature = directoryEntriesSignature(visibleEntries);
    if (previousSignature !== undefined && previousSignature !== nextSignature) changedDirectories.push(path);
    entriesByDir.set(path, visibleEntries);
    cacheFileExplorerRepoInfoEntries(path, visibleEntries);
    markNewDirectoryEntries(path, visibleEntries);
    recordDirectorySignature(path, visibleEntries);
    setFileExplorerFsResourceValue('list', path, visibleEntries);
    scheduleFileExplorerRepoInfoEnrichment(path, visibleEntries, {
      includeRoot: path === currentFileExplorerRoot(),
      watchDiffOwned: options.fromWatchDiff === true,
    });
  }
  return {entriesByDir, changedDirectories, failedDirectories};
}

async function fetchFilesystemWatchDiff(options = {}) {
  const params = new URLSearchParams();
  if (options.full === true) params.set('full', '1');
  const since = String((options.since ?? fileExplorerFilesystemWatchToken) || '').trim();
  if (since) params.set('since', since);
  return apiFetchJson(`/api/fs/watch-diff?${params.toString()}`);
}

function queueFileExplorerWatchDiffRefresh(payload = {}, options = {}) {
  const previous = serverWatchRootsState.watchDiffTrailing;
  serverWatchRootsState.watchDiffTrailing = {
    payload,
    options: {
      ...options,
      full: previous?.options?.full === true || options.full === true,
    },
  };
}

async function refreshFileExplorerFromWatchDiffOnce(payload = {}, options = {}) {
  const watchDemand = clientServerWatchState();
  const watchedFiles = [...watchDemand.files, ...watchDemand.background_files];
  if (!fileExplorerTreePaneIsVisible() && !watchedFiles.length) {
    recordClientPerfCounter('finderRefresh', 0, {skipped: 1});
    return;
  }
  const fullDue = Date.now() - fileExplorerFilesystemLastFullAt >= fileExplorerFilesystemKeyframeMs;
  const previousToken = fileExplorerFilesystemWatchToken;
  const previousPushToken = fileExplorerFilesystemPushToken;
  const requestedFull = options.full === true || payload.full === true || fullDue || !previousToken;
  const params = new URLSearchParams();
  if (requestedFull) params.set('full', '1');
  if (previousToken) params.set('since', previousToken);
  const requestUrl = `/api/fs/watch-diff?${params.toString()}`;
  try {
    let diffPayload;
    try {
      diffPayload = await fetchFilesystemWatchDiff({since: previousToken, full: requestedFull});
    } catch (error) {
      if (!isApiPendingResponse(error) || !error.operationId) throw error;
      const record = apiOperationState.records.get(error.operationId);
      if (record) record.terminalOwner = 'filesystem-watch-diff-refresh';
      diffPayload = await waitForApiOperationResult(error, {
        kind: 'fs_watch_diff',
        url: requestUrl,
        method: 'GET',
      });
    }
    if (fileExplorerFilesystemWatchToken !== previousToken || fileExplorerFilesystemPushToken !== previousPushToken) return;
    await refreshFileExplorerFromPush(diffPayload, {fromWatchDiff: true});
    if (diffPayload.token && fileExplorerFilesystemPushToken !== String(diffPayload.token)) return;
    await refreshOpenFilesFromPush(diffPayload);
  } catch (error) {
    console.warn('client fs watch diff refresh failed', error);
    if (!fileExplorerTreePaneIsVisible()) await refreshOpenFilesIfChanged({paths: watchedFiles});
    else if (requestedFull) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true, trigger: 'watch-diff-fallback'});
    else await refreshFileExplorerIfChanged({trigger: 'watch-diff-fallback'});
  }
}

function refreshFileExplorerFromWatchDiff(payload = {}, options = {}) {
  if (serverWatchRootsState.retired) return Promise.resolve({retired: true});
  queueFileExplorerWatchDiffRefresh(payload, options);
  if (serverWatchRootsState.watchDiffPromise) return serverWatchRootsState.watchDiffPromise;
  const owned = (async () => {
    while (serverWatchRootsState.watchDiffTrailing) {
      const next = serverWatchRootsState.watchDiffTrailing;
      serverWatchRootsState.watchDiffTrailing = null;
      await refreshFileExplorerFromWatchDiffOnce(next.payload, next.options);
    }
  })().finally(() => {
    if (serverWatchRootsState.watchDiffPromise === owned) serverWatchRootsState.watchDiffPromise = null;
  });
  serverWatchRootsState.watchDiffPromise = owned;
  return owned;
}

function fileEntryStatusFromWatchFilePayload(item) {
  const signature = Array.isArray(item?.signature) ? item.signature : [];
  const path = String(item?.path || signature[0] || '');
  if (!path) return {path: '', entry: null, missing: false, error: '', network: false};
  const kind = String(signature[1] || '');
  if (kind === 'missing') return {path, entry: null, missing: true, error: t('common.pathNotFound', {path}), network: false};
  if (kind !== 'file' && kind !== 'dir') return {path, entry: null, missing: false, error: t('finder.invalidFileSignature', {path}), network: false};
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
  // Native invalidations carry paths, while watch-diff readback owns complete file signatures.
  if (Array.isArray(payload.paths) && !Array.isArray(payload.files)) return refreshFileExplorerFromPush(payload);
  const files = Array.isArray(payload.files) ? payload.files : [];
  for (const item of files) {
    const fetched = fileEntryStatusFromWatchFilePayload(item);
    if (!fetched.path) continue;
    const state = fileState.get(fetched.path);
    if (!state || state.loading) continue;
    await refreshOpenFileFromFetchedStatus(fetched.path, state, fetched);
  }
}

async function refreshFileExplorerFromPush(payload = {}, options = {}) {
  const nextToken = payload?.token ? String(payload.token || '') : '';
  if (payload?.mode === 'full' && nextToken) fileExplorerFilesystemWatchToken = nextToken;
  if (options.fromWatchDiff !== true && nextToken) {
    if (fileExplorerFilesystemPushToken === nextToken) return;
    fileExplorerFilesystemPushToken = nextToken;
  }
  const treeVisible = fileExplorerTreePaneIsVisible();
  const changedPaths = [
    ...(Array.isArray(payload?.paths) ? payload.paths : []),
    ...(Array.isArray(payload?.directories) ? payload.directories.map(item => item?.path || item?.data?.path || '') : []),
    ...(Array.isArray(payload?.removed_roots) ? payload.removed_roots : []),
  ];
  const removedPaths = Array.isArray(payload?.removed_paths) ? payload.removed_paths : [];
  if (removedPaths.length) {
    markFileExplorerDeletedPaths(removedPaths);
    removeFileTreePathsImmediately(removedPaths);
    invalidateFileExplorerRoots(removedPaths.map(dirnameOf));
  }
  // File links are a consumer of the same filesystem truth even when Finder is hidden.
  // Invalidate only affected entries so a create/rename can become clickable immediately.
  if (typeof invalidateTerminalFileReferenceTargets === 'function') invalidateTerminalFileReferenceTargets(changedPaths);
  const changedRoots = Array.isArray(payload?.directories)
    ? payload.directories.length
    : Math.max(0, Number(payload?.change_summary?.roots_changed || 0));
  let renderedRows = 0;
  let skipped = 0;
  const perf = clientPerfStart('finderRefresh');
  try {
    if (options.fromWatchDiff === true && nextToken) {
      fileExplorerFilesystemWatchToken = nextToken;
      fileExplorerFilesystemPushToken = nextToken;
    }
    const pushed = treeVisible ? entriesByDirFromFilesystemPush(payload, options) : {entriesByDir: new Map(), changedDirectories: [], failedDirectories: new Map()};
    const entriesByDir = pushed.entriesByDir;
    const changedDirectories = pushed.changedDirectories;
    const failedDirectories = pushed.failedDirectories;
    renderedRows = Array.from(entriesByDir.values()).reduce((total, entries) => total + (Array.isArray(entries) ? entries.length : 0), 0);
    const visibleRootRemoved = treeVisible ? invalidateFileExplorerRoots(payload?.removed_roots) : false;
    if (!entriesByDir.size && (payload?.refresh === true || Array.isArray(payload?.paths)) && options.fromWatchDiff !== true) {
      await refreshFileExplorerFromWatchDiff(payload);
      return;
    }
    if (options.fromWatchDiff === true && nextToken) fileExplorerFilesystemWatchToken = nextToken;
    if (payload?.mode === 'full' || (Array.isArray(payload?.directories) && payload.directories.length && payload.refresh !== true)) {
      fileExplorerFilesystemLastFullAt = Date.now();
    }
    if (!treeVisible) {
      skipped = 1;
      return;
    }
    if (visibleRootRemoved) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
    if (!entriesByDir.size && !failedDirectories.size) return;
    const finderBackfillSession = fileExplorerFinderTargetSession();
    const currentRoot = normalizeDirectoryPath(currentFileExplorerRoot());
    const watchDiffChangedVisibleTree = changedDirectories.some(path => (
      pathIsInsideDirectory(path, currentRoot) || pathIsInsideDirectory(currentRoot, path)
    ));
    const finderHasLoadedMetadata = finderBackfillSession
      && sessionFilesPayloadIsLoadedForSession(fileExplorerFinderSessionFilesState.payload, finderBackfillSession)
      && (options.fromWatchDiff !== true || watchDiffChangedVisibleTree);
    const fastPaintGeneration = finderHasLoadedMetadata ? beginFinderSessionFilesDiffBackfill() : null;
    fileExplorerPushRefreshDepth += 1;
    try {
      const root = normalizeDirectoryPath(currentFileExplorerRoot());
      if (fileExplorerPaneIsOpen() && (entriesByDir.has(root) || failedDirectories.size)) {
        const rootEntries = entriesByDir.get(root) || cachedFileExplorerFsResourceValue('list', root);
        if (Array.isArray(rootEntries)) {
          await refreshFileExplorerTreesInPlace({
            root,
            entries: rootEntries,
            preserveExpanded: true,
            preserveScroll: true,
            entriesByDir,
            deferSessionFilesDiff: finderHasLoadedMetadata,
          });
        }
      }
      await settleFileExplorerDirectoryFailures(
        failedDirectories,
        (failedPath, generation) => fileExplorerListingFailureIsCurrent(failedPath, generation),
      );
      const openFileDirs = Array.from(fileState.keys()).map(path => normalizeDirectoryPath(dirnameOf(path)));
      if (openFileDirs.every(path => fileExplorerFsResourceHasValue('list', path))) await refreshOpenFilesIfChanged();
    } finally {
      fileExplorerPushRefreshDepth = Math.max(0, fileExplorerPushRefreshDepth - 1);
    }
    if (finderHasLoadedMetadata) scheduleFinderSessionFilesBackfillAfterFastPaint(finderBackfillSession, fastPaintGeneration);
  } finally {
    clientPerfEnd(perf, {nodes: changedRoots, rows: renderedRows, skipped});
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

function fileExplorerDirectoryRecord(path) {
  return fileExplorerDirectoryRecords.get(normalizeDirectoryPath(path)) || null;
}

function updateFileExplorerDirectoryRecord(path, patch = {}, existing = fileExplorerDirectoryRecord(path)) {
  const root = normalizeDirectoryPath(path);
  const next = {
    signature: existing?.signature,
    knownEntryNames: existing?.knownEntryNames || null,
    ...patch,
  };
  setLimitedMapEntry(fileExplorerDirectoryRecords, root, next, fileExplorerMemoryCacheLimit);
  return next;
}

function recordDirectorySignature(path, entries) {
  updateFileExplorerDirectoryRecord(path, {signature: directoryEntriesSignature(entries)});
}

function pruneExpiredNewFileEntries(now = Date.now()) {
  for (const [path, until] of fileExplorerNewEntryUntil.entries()) {
    if (!until || until <= now) fileExplorerNewEntryUntil.delete(path);
  }
}

function markNewDirectoryEntries(path, entries) {
  const root = normalizeDirectoryPath(path);
  const names = new Set((Array.isArray(entries) ? entries : []).map(entry => entry?.name).filter(Boolean));
  const record = fileExplorerDirectoryRecord(root);
  const previous = record?.knownEntryNames;
  const now = Date.now();
  pruneExpiredNewFileEntries(now);
  if (previous && fileExplorerNewEntryHighlightMs > 0) {
    for (const name of names) {
      if (!previous.has(name)) setLimitedMapEntry(fileExplorerNewEntryUntil, childPath(root, name), now + fileExplorerNewEntryHighlightMs, fileExplorerMemoryCacheLimit);
    }
  }
  updateFileExplorerDirectoryRecord(root, {knownEntryNames: names}, record);
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
  const cachePath = normalizeDirectoryPath(repo.cache_path || fallbackRoot || root);
  const dirtyCount = Number(repo.dirty_count);
  const ahead = Number(repo.ahead);
  const behind = Number(repo.behind);
  return {
    root,
    cache_path: cachePath,
    name: String(repo.name || basenameOf(root) || ''),
    branch: String(repo.branch || ''),
    detached: repo.detached === true,
    head_sha: repo.head_sha == null ? null : String(repo.head_sha).trim(),
    dirty_count: Number.isFinite(dirtyCount) ? dirtyCount : null,
    upstream: String(repo.upstream || ''),
    ahead: Number.isFinite(ahead) ? ahead : 0,
    behind: Number.isFinite(behind) ? behind : 0,
  };
}

function hydrateFileExplorerRepoInfoCache() {
  if (fileExplorerRepoInfoCacheLoaded) return;
  fileExplorerRepoInfoCacheLoaded = true;
  const parsed = safeJsonParse(storageGet(fileExplorerRepoInfoStorageKey), null);
  const repos = Array.isArray(parsed?.repos) ? parsed.repos : [];
  for (const item of repos) {
    const path = normalizeDirectoryPath(item?.path || item?.repo?.root || '');
    const repo = normalizeFileExplorerRepoInfo(item?.repo, path);
    if (!path || !repo) continue;
    // Drop a persisted cache_path that points INSIDE its own repo root. Records written before the
    // alias fix stamped the queried descendant, which made every child of a repo satisfy the
    // root-identity guard and inherit that repo's branch label.
    const root = normalizeDirectoryPath(repo.root);
    const stale = root && repo.cache_path && normalizeDirectoryPath(repo.cache_path).startsWith(`${root}/`);
    setLimitedMapEntry(
      fileExplorerRepoInfoCache,
      path,
      stale ? {...repo, cache_path: root} : repo,
      fileExplorerMemoryCacheLimit,
    );
  }
}

function persistFileExplorerRepoInfoCache() {
  try {
    const repos = Array.from(fileExplorerRepoInfoCache.entries())
      .filter(([path, repo]) => path && repo?.root && normalizeDirectoryPath(repo.cache_path || repo.root) === path)
      .sort(([leftPath], [rightPath]) => leftPath.localeCompare(rightPath))
      .slice(-200)
      .map(([path, repo]) => ({path, repo}));
    storageSet(fileExplorerRepoInfoStorageKey, JSON.stringify({repos}));
  } catch (_) {}
}

function cacheFileExplorerRepoInfo(path, repo, options = {}) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path || repo?.root || '');
  const info = normalizeFileExplorerRepoInfo(repo, normalized);
  if (!normalized || !info) return false;
  const repoRoot = normalizeDirectoryPath(info.root);
  // Finder preserves the path the user opened while macOS APIs can return its physical alias
  // (/tmp versus /private/tmp). Keep both exact cache identities without rewriting the Git root.
  // A cache_path is an ALIAS OF the repo root, never a path inside it: stamping it on a descendant
  // made every child of a repo satisfy the root-identity guard in fileTreeRepoBranch(), so every
  // entry under an ancestor repo inherited that repo's branch label.
  const aliasesRepoRoot = !repoRoot || repoRoot === normalized || !normalized.startsWith(`${repoRoot}/`);
  setLimitedMapEntry(
    fileExplorerRepoInfoCache,
    normalized,
    aliasesRepoRoot ? {...info, cache_path: normalized} : info,
    fileExplorerMemoryCacheLimit,
  );
  if (repoRoot && repoRoot !== normalized) {
    setLimitedMapEntry(fileExplorerRepoInfoCache, repoRoot, {...info, cache_path: repoRoot}, fileExplorerMemoryCacheLimit);
  }
  if (options.persist !== false) persistFileExplorerRepoInfoCache();
  return true;
}

function clearFileExplorerRepoInfo(path, options = {}) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path);
  if (!normalized) return false;
  let changed = false;
  for (const [cachedPath, repo] of Array.from(fileExplorerRepoInfoCache.entries())) {
    if (cachedPath !== normalized
        && normalizeDirectoryPath(repo?.cache_path || '') !== normalized
        && normalizeDirectoryPath(repo?.root || '') !== normalized) continue;
    fileExplorerRepoInfoCache.delete(cachedPath);
    changed = true;
  }
  if (changed && options.persist !== false) persistFileExplorerRepoInfoCache();
  return changed;
}

function exactFileExplorerRepoInfo(path) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path);
  const repo = fileExplorerRepoInfoCache.get(normalized);
  return normalizeDirectoryPath(repo?.cache_path || repo?.root || '') === normalized ? repo : null;
}

function cacheFileExplorerRepoInfoEntries(parentPath, entries) {
  return drainFileTreeRenderSteps(cacheFileExplorerRepoInfoEntrySteps(parentPath, entries));
}

function* cacheFileExplorerRepoInfoEntrySteps(parentPath, entries, options = {}) {
  if (!Array.isArray(entries)) return;
  hydrateFileExplorerRepoInfoCache();
  let changed = false;
  for (const entry of entries) {
    if (options.isCurrent && !options.isCurrent()) return;
    if (entry?.kind === 'dir' && entry.is_repo === true && entry.repo) {
      const fullPath = childPath(parentPath, entry.name);
      changed = cacheFileExplorerRepoInfo(fullPath, entry.repo, {persist: false}) || changed;
    }
    yield;
  }
  if (changed) persistFileExplorerRepoInfoCache();
}

function scheduleFileExplorerRepoInfoEnrichment(parentPath, entries, options = {}) {
  const root = normalizeDirectoryPath(parentPath);
  const state = fileExplorerRepoInfoEnrichmentState;
  const candidates = [];
  if (options.includeRoot === true && root) candidates.push(root);
  for (const entry of Array.isArray(entries) ? entries : []) {
    // A filesystem push can carry the full listing's cheap is_repo marker without the detailed
    // repo payload. Treat that shape like FastList's explicit deferred marker; otherwise a push
    // that overwrites the fast resource can suppress the INFO backfill permanently.
    if (entry?.kind !== 'dir' || entry.repo || (entry.repo_info_deferred !== true && entry.is_repo !== true)) continue;
    candidates.push(childPath(root, entry.name));
  }
  for (const path of candidates) {
    if (options.watchDiffOwned === true) state.watchDiffOwned.add(path);
    if (options.refresh === true) {
      state.pathGenerations.set(path, state.nextGeneration++);
      state.resolved.delete(path);
    }
    if (!state.resolved.has(path) && (!state.inFlight.has(path) || options.refresh === true)) state.pending.add(path);
  }
  if (!state.pending.size || state.frame !== null || state.inFlight.size) return false;
  if (options.immediate === true) {
    void enrichFileExplorerRepoInfoEntries();
    return true;
  }
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
  state.frame = schedule(() => {
    state.frame = null;
    void enrichFileExplorerRepoInfoEntries();
  });
  return true;
}

async function enrichFileExplorerRepoInfoEntries() {
  const state = fileExplorerRepoInfoEnrichmentState;
  const requests = [];
  for (const path of Array.from(state.pending)) {
    if (requests.length >= fileExplorerRepoInfoEnrichmentChunkLimit) break;
    if (state.inFlight.has(path)) continue;
    state.pending.delete(path);
    state.inFlight.add(path);
    requests.push({
      path,
      globalGeneration: state.globalGeneration,
      pathGeneration: state.pathGenerations.get(path) || 0,
      terminalOwner: state.watchDiffOwned.has(path) ? 'filesystem-watch-diff-refresh' : '',
    });
  }
  if (!requests.length) return;
  const results = await Promise.allSettled(requests.map(async request => ({
    ...request,
    info: await fetchFilePathInfo(request.path, {
      fresh: true,
      force: true,
      trigger: 'repo-enrichment',
      terminalOwner: request.terminalOwner,
    }),
  })));
  let changed = false;
  const enrichedPaths = new Set();
  for (let index = 0; index < requests.length; index += 1) {
    const request = requests[index];
    state.inFlight.delete(request.path);
    if (!state.pending.has(request.path)) state.watchDiffOwned.delete(request.path);
    const result = results[index];
    if (result?.status !== 'fulfilled') continue;
    if (request.globalGeneration !== state.globalGeneration
        || request.pathGeneration !== (state.pathGenerations.get(request.path) || 0)) continue;
    const path = normalizeDirectoryPath(result.value.path);
    state.resolved.add(path);
    enrichedPaths.add(path);
    changed = result.value.info?.repo
      ? cacheFileExplorerRepoInfo(path, result.value.info.repo, {persist: false}) || changed
      : clearFileExplorerRepoInfo(path, {persist: false}) || changed;
  }
  if (changed || enrichedPaths.size) {
    if (changed) persistFileExplorerRepoInfoCache();
    const mountedRows = fileExplorerTreeContainers().flatMap(container => Array.from(
      container.querySelectorAll?.('.file-tree-row[data-path]') || [],
    )).filter(row => !row.dataset.tabberType && enrichedPaths.has(normalizeDirectoryPath(row.dataset.path || '')));
    for (const row of mountedRows) row.dataset.repoInfoResolved = 'true';
    updateFileTreeGitStatusRows(mountedRows);
    const root = currentFileExplorerRoot();
    if (enrichedPaths.has(root) && !currentFileExplorerListError(root)) {
      setFileExplorerRepoSummary(root, fileExplorerRepoInfoCache.get(root) || null);
    }
  }
  if (state.pending.size) scheduleFileExplorerRepoInfoEnrichment('', []);
}

function currentFileExplorerRoot() {
  return normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
}

// Finder sync is allowed to follow only an explicit user intent. Keep that
// decision in one place because tree mouse, keyboard, and refresh paths share
// the same selection state.
function setFileExplorerSelectionPin(pinned) {
  fileExplorerManualSelectionActive = pinned === true;
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
  const finderIsSynced = fileExplorerRootMode === 'sync';
  for (const panel of document.querySelectorAll('.file-explorer-panel[data-file-explorer-view="finder"]')) {
    panel.classList.toggle('file-explorer-root-mode-sync', finderIsSynced);
    panel.classList.toggle('file-explorer-root-mode-fixed', !finderIsSynced);
  }
  if (typeof syncFileExplorerSessionControlVisibility === 'function') {
    syncFileExplorerSessionControlVisibility();
  }
}

function setFileExplorerRootMode(mode, options = {}) {
  resetFileExplorerAppliedSyncPlan();
  fileExplorerRootMode = mode === 'sync' ? 'sync' : 'fixed';
  writeStoredFileExplorerRootMode(fileExplorerRootMode);
  renderFileExplorerRootModeControls();
  scheduleFileTreeMetadataRefresh({highlightsOnly: true});
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
  cancelPendingFileExplorerActiveSync();
  if (fileExplorerRootMode !== 'fixed') {
    setFileExplorerRootMode('fixed', {sync: false, persist: true});
  } else {
    renderFileExplorerRootModeControls();
  }
}

function openFileExplorerManualRoot(path) {
  setFileExplorerManualRootMode();
  return openFileExplorerAt(path, {manualSelection: true, validateKind: true});
}

function tmuxDirectoryForItem(item) {
  if (!isTmuxSession(item)) return '';
  const path = terminalCurrentPath(item);
  return path ? normalizeDirectoryPath(path) : '';
}

function tmuxGitRootForItem(item) {
  if (!isTmuxSession(item)) return '';
  const info = transcriptMetadataState.payload.sessions?.[item] || {};
  const root = sessionWorkSummary(item, info).git?.root || '';
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

function activeTmuxSessionForFinder(preferredItem = null) {
  if (preferredItem) return isTmuxSession(preferredItem) ? preferredItem : '';
  for (const item of finderCandidateItems(preferredItem)) {
    if (isTmuxSession(item)) return item;
  }
  return '';
}

function fileExplorerExplicitSyncSessionTarget() {
  return explicitTmuxPaneFocusSession();
}

function fileExplorerSyncCommandSessionTarget() {
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  if (explicitSession) return explicitSession;
  const payloadSession = String(fileExplorerFinderSessionFilesState.payload?.session || '');
  if (isTmuxSession(payloadSession) && activeSessions.includes(payloadSession)) return payloadSession;
  return activeTmuxSessionForFinder();
}

function setFileExplorerFinderSessionSelection(session) {
  if (fileExplorerFinderSelectedSession === session) return false;
  fileExplorerFinderSelectedSession = session;
  syncFileExplorerDiffSessionControls();
  return true;
}

function rememberFileExplorerExplicitSyncSession(session) {
  const normalizedSession = String(session || '');
  if (!isTmuxSession(normalizedSession) || !activeSessions.includes(normalizedSession)) return false;
  const finderSelectionChanged = setFileExplorerFinderSessionSelection(normalizedSession);
  const previous = fileExplorerExplicitSyncSessionTarget();
  const changed = setExplicitPaneFocusItem(normalizedSession);
  if (changed) {
    if (previous) restoreCommittedFileExplorerRootDisplay();
    cancelPendingFileExplorerActiveSync();
    scheduleFileTreeMetadataRefresh({pendingOnly: true});
  }
  return changed || finderSelectionChanged;
}

function fileExplorerRootForOpen(preferredItem = null) {
  if (fileExplorerRootMode === 'sync') {
    const syncPlan = typeof fileExplorerSyncPlan === 'function' ? fileExplorerSyncPlan(preferredItem) : null;
    if (syncPlan?.root) return syncPlan.root;
    return normalizeDirectoryPath(homePath || '/');
  }
  return fileExplorerRoot || homePath || '/';
}

function fileExplorerSyncSessionContext(session) {
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const pinned = sessionRepoDisplayRoot.has(session) ? selectedSessionRepo(session, info) : null;
  return pinned && repoRootKey(pinned.root) === repoRootKey(sessionRepoDisplayRoot.get(session))
    ? {repository: normalizeDirectoryPath(pinned.root), cwd: normalizeDirectoryPath(pinned.cwd || pinned.root)}
    : {repository: tmuxGitRootForItem(session), cwd: tmuxDirectoryForItem(session)};
}

function fileExplorerSyncTargetKey(session, root) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const normalizedSession = String(session || '');
  const {repository} = fileExplorerSyncSessionContext(normalizedSession);
  return normalizedSession && normalizedRoot ? `${normalizedSession}\x1f${normalizedRoot}${repository ? '\x1f' + repository : ''}` : '';
}

function fileExplorerExpandedPathsForRoot(root, paths = Array.from(fileExplorerExpanded)) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  return fileExplorerPathsSortedByDepth(normalizedRoot, Array.from(new Set((paths || [])
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot)))));
}

function fileExplorerEffectiveExpandedPaths(root, paths = null) {
  if (paths === null) {
    if (fileExplorerRootMode === 'sync' && fileExplorerSyncManualCollapseTargetKey
      && fileExplorerSyncManualCollapseTargetKey !== fileExplorerVisibleSyncTargetKey) return [];
    paths = Array.from(fileExplorerExpanded);
  }
  const expanded = new Set(fileExplorerExpandedPathsForRoot(root, paths));
  return Array.from(expanded).filter(path => (
    (fileExplorerRootMode !== 'sync' || !fileExplorerSyncPathSuppressed(path))
    && ancestorPathsUnderRoot(root, path).every(ancestor => ancestor === path || expanded.has(ancestor))
  ));
}

// Expansion ordering is transaction-local: derive depths once instead of normalizing
// and splitting both paths at every comparison. Restore preserves same-depth input order.
function fileExplorerPathsSortedByDepth(root, paths, lexicalTies = true) {
  return paths.map(path => ({path, depth: childPathParts(root, path).length}))
    .sort((left, right) => left.depth - right.depth || (lexicalTies ? left.path.localeCompare(right.path) : 0))
    .map(entry => entry.path);
}

function fileExplorerDirectoryDemandTerminalError(error) {
  const status = Number(error?.status) || 0;
  const messageKey = String(
    error?.payload?.user_message?.key
    || error?.payload?.error?.message?.key
    || error?.payload?.message?.key
    || '',
  );
  return status === 404 || (status === 400 && messageKey === 'fs.error.notDirectory');
}

function retireFileExplorerDirectoryDemand(path, options = {}) {
  const roots = new Set((Array.isArray(path) ? path : [path]).map(value => normalizeDirectoryPath(value || '')).filter(Boolean));
  if (!roots.size) return false;
  const retirementMatches = new Map();
  const retires = candidate => {
    if (retirementMatches.has(candidate)) return retirementMatches.get(candidate);
    let value = normalizeDirectoryPath(candidate || '');
    while (value) {
      if (roots.has(value)) {
        retirementMatches.set(candidate, true);
        return true;
      }
      const parent = dirnameOf(value);
      if (parent === value) break;
      value = parent;
    }
    retirementMatches.set(candidate, false);
    return false;
  };
  let changed = false;
  const pendingPathsToReconcile = [];
  for (const candidate of Array.from(fileExplorerExpanded)) {
    if (retires(candidate)) changed = fileExplorerExpanded.delete(candidate) || changed;
  }
  for (const candidate of Array.from(fileExplorerPendingExpansions)) {
    if (retires(candidate)) {
      fileExplorerPendingExpansionOwners.delete(candidate);
      changed = fileExplorerPendingExpansions.delete(candidate) || changed;
      pendingPathsToReconcile.push(candidate);
    }
  }
  for (const candidate of Array.from(fileExplorerSyncUserExpansionState.keys())) {
    if (retires(candidate)) changed = fileExplorerSyncUserExpansionState.delete(candidate) || changed;
  }
  for (const record of fileExplorerSyncTargetRecords.values()) {
    const previousExpanded = Array.isArray(record.expandedPaths) ? record.expandedPaths : [];
    const nextExpanded = previousExpanded.filter(candidate => !retires(candidate));
    if (nextExpanded.length !== previousExpanded.length) {
      record.expandedPaths = nextExpanded;
      changed = true;
    }
    for (const candidate of Array.from(record.userExpansion?.keys() || [])) {
      if (retires(candidate)) changed = record.userExpansion.delete(candidate) || changed;
    }
    if (record.manualCollapsedPaths instanceof Set) {
      for (const candidate of Array.from(record.manualCollapsedPaths)) {
        if (retires(candidate)) changed = record.manualCollapsedPaths.delete(candidate) || changed;
      }
    }
  }
  for (const candidate of Array.from(fileExplorerSyncManualCollapsedPaths)) {
    if (retires(candidate)) changed = fileExplorerSyncManualCollapsedPaths.delete(candidate) || changed;
  }
  for (const [key, record] of Array.from(fileExplorerFsResourceRecords.entries())) {
    const [type, candidate] = key.split('\x1f');
    if (type === 'list' && !roots.has(candidate) && retires(candidate)) {
      invalidateFileExplorerFsResourceRecord(key, record);
      changed = true;
    }
  }
  for (const [candidate, record] of Array.from(fileExplorerDirectoryRecords.entries())) {
    if (!roots.has(candidate) && retires(candidate)) {
      fileExplorerDirectoryRecords.delete(candidate);
      record.knownEntryNames?.clear?.();
      changed = true;
    }
  }
  if (!changed) return false;
  if (pendingPathsToReconcile.length) {
    void settleFileExplorerDirectoryFailures(pendingPathsToReconcile.map(candidate => [candidate, null]), () => true, {publish: false});
  }
  if (options.preserveActiveSync !== true) cancelPendingFileExplorerActiveSync({invalidateOpen: false});
  refreshLayoutUrlStateSoon();
  syncServerWatchRoots();
  return true;
}

function qualifyFileExplorerDirectoryPaths(root, paths, entriesByDir, context = {}) {
  const normalizedRoot = normalizeDirectoryPath(root);
  if (context.root !== normalizedRoot || context.paths !== paths) {
    context.root = normalizedRoot;
    context.paths = paths;
    context.candidates = fileExplorerExpandedPathsForRoot(normalizedRoot, paths)
      .map(path => ({path, parent: dirnameOf(path), name: basenameOf(path), ancestors: ancestorPathsUnderRoot(normalizedRoot, path)}));
    context.entriesByName = new WeakMap();
  }
  const qualified = [];
  const retired = new Set();
  for (const {path, parent, name, ancestors} of context.candidates) {
    if (ancestors.some(ancestor => retired.has(ancestor))) continue;
    const parentEntries = entriesByDir.get(parent);
    if (!Array.isArray(parentEntries)) continue;
    let entriesByName = context.entriesByName.get(parentEntries);
    if (!entriesByName) {
      entriesByName = new Map();
      for (const candidate of parentEntries) {
        const candidateName = String(candidate?.name || '');
        if (!entriesByName.has(candidateName)) entriesByName.set(candidateName, candidate);
      }
      context.entriesByName.set(parentEntries, entriesByName);
    }
    const entry = entriesByName.get(name);
    if (entry?.kind === 'dir') {
      const listingRecord = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', path));
      const priorFailureStatus = Number(listingRecord?.failureStatus) || 0;
      if (priorFailureStatus === 400 || priorFailureStatus === 404) {
        clearFileExplorerFsResourceFailure(listingRecord);
        if (Number(fileExplorerLastListError?.status) === priorFailureStatus) clearFileExplorerListError(path);
      }
      qualified.push(path);
      continue;
    }
    retired.add(path);
    markFileExplorerDirectoryDemandNegative(path, entry ? 400 : 404);
  }
  if (retired.size) retireFileExplorerDirectoryDemand(Array.from(retired), context);
  return qualified;
}

function fileExplorerSyncUserExpansionEntries() {
  return Array.from(fileExplorerSyncUserExpansionState.entries())
    .filter(([path, expanded]) => normalizeDirectoryPath(path) && typeof expanded === 'boolean')
    .sort(([left], [right]) => left.localeCompare(right));
}

function fileExplorerSyncTargetExpansionSnapshot() {
  return {version: 2, activeTargetKey: fileExplorerVisibleSyncTargetKey || fileExplorerSyncManualCollapseTargetKey, selectedRepositories: Array.from(sessionRepoDisplayRoot), targets: Array.from(fileExplorerSyncTargetRecords, ([key, record]) => ({
    key, expandedPaths: record.expandedPaths, userExpansion: Array.from(record.userExpansion || []),
    manualCollapsedPaths: Array.from(record.manualCollapsedPaths || []),
  }))};
}

function applyFileExplorerSyncUserExpansionState(saved) {
  fileExplorerSyncTargetRecords.clear();
  fileExplorerVisibleSyncSession = '';
  fileExplorerVisibleSyncRoot = '';
  fileExplorerVisibleSyncTargetKey = '';
  if (fileExplorerRootMode === 'sync') fileExplorerExpanded.clear();
  fileExplorerSyncManualCollapseTargetKey = '';
  fileExplorerSyncUserExpansionState = new Map();
  fileExplorerSyncLegacyExpansionEntries = null;
  if (saved?.version === 2 && Array.isArray(saved.targets)) {
    sessionRepoDisplayRoot.clear();
    for (const [session, repository] of (saved.selectedRepositories || [])) {
      if (typeof session === 'string' && typeof repository === 'string' && repository.startsWith('/')) sessionRepoDisplayRoot.set(session, repository);
    }
    for (const target of saved.targets) {
      if (typeof target?.key !== 'string') continue;
      const record = fileExplorerSyncTargetRecord(target.key, true);
      record.userExpansion = new Map((target.userExpansion || []).filter(entry => Array.isArray(entry) && typeof entry[0] === 'string' && typeof entry[1] === 'boolean'));
      record.expandedPaths = fileExplorerSyncUserExpandedPathsForRoot(target.key.split('\x1f')[1], record.userExpansion, true);
      record.manualCollapsedPaths = new Set((target.manualCollapsedPaths || []).filter(path => typeof path === 'string'));
    }
    const [session, root] = String(saved.activeTargetKey || '').split('\x1f');
    const record = fileExplorerSyncTargetRecord(saved.activeTargetKey);
    if (fileExplorerRootMode === 'sync' && record && session === fileExplorerFinderTargetSession()
      && root === currentFileExplorerRoot()) {
      resetFileExplorerSyncManualCollapsesIfNeeded({session, root, targetKey: saved.activeTargetKey});
      setFileExplorerVisibleSyncTarget(session, root, saved.activeTargetKey);
      for (const path of fileExplorerExpandedPathsForRoot(root, [...record.expandedPaths,
        ...Array.from(record.userExpansion).filter(([, open]) => open).map(([path]) => path)])) {
        if (!record.manualCollapsedPaths.has(path) && record.userExpansion.get(path) !== false) fileExplorerExpanded.add(path);
      }
    }
    return;
  }
  fileExplorerSyncLegacyExpansionEntries = (Array.isArray(saved) ? saved : [])
    .filter(entry => Array.isArray(entry) && entry.length === 2 && typeof entry[1] === 'boolean');
}

function setFileExplorerSyncUserExpansion(path, expanded) {
  if (fileExplorerRootMode !== 'sync') return;
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized) return;
  const plan = !fileExplorerSyncManualCollapseTargetKey ? fileExplorerSyncManualCollapsePlanForPath(normalized) : null;
  if (plan) {
    resetFileExplorerSyncManualCollapsesIfNeeded(plan);
    if (!fileExplorerVisibleSyncTargetKey && !fileExplorerSyncState.inFlightSignature) {
      setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    }
  }
  fileExplorerSyncUserExpansionState.set(normalized, expanded === true);
}

function fileExplorerSyncUserExpandedPathsForRoot(root, userExpansion = fileExplorerSyncUserExpansionState, includeSuppressed = false) {
  const paths = fileExplorerExpandedPathsForRoot(root, Array.from(userExpansion)
    .filter(([, expanded]) => expanded === true)
    .flatMap(([path]) => ancestorPathsUnderRoot(root, path)));
  return includeSuppressed ? paths : paths.filter(path => !fileExplorerSyncPathSuppressed(path));
}

function fileExplorerSyncTargetRecord(targetKey, create = false, touch = false) {
  const key = String(targetKey || '');
  if (!key) return null;
  let record = fileExplorerSyncTargetRecords.get(key) || null;
  if (!record && create) {
    record = {
      expandedPaths: [],
      userExpansion: new Map(),
      manualCollapsedPaths: new Set(),
      cursorPath: '',
      selectedPaths: [],
      anchorPath: '',
    };
  }
  if (record && (touch || !fileExplorerSyncTargetRecords.has(key))) {
    setLimitedMapEntry(fileExplorerSyncTargetRecords, key, record, fileExplorerMemoryCacheLimit);
  }
  return record;
}

function rememberFileExplorerSyncExpandedState(session = fileExplorerVisibleSyncSession, root = fileExplorerVisibleSyncRoot) {
  if (fileExplorerRootMode !== 'sync') return;
  const key = session === fileExplorerVisibleSyncSession && root === fileExplorerVisibleSyncRoot
    ? fileExplorerVisibleSyncTargetKey || fileExplorerSyncTargetKey(session, root) : fileExplorerSyncTargetKey(session, root);
  if (!key) return;
  const record = fileExplorerSyncTargetRecord(key, true, true);
  record.expandedPaths = fileExplorerSyncUserExpandedPathsForRoot(root, record.userExpansion, true);
  // This is deliberately one target record with disclosure state: a same-root session must retain
  // its own keyboard cursor rather than borrowing the other session's global Finder selection.
  record.cursorPath = fileExplorerSyncCursorPathForRoot(root, fileExplorerSelectionLead);
  record.selectedPaths = fileExplorerSyncPathsForRoot(root, fileExplorerSelectedPaths);
  record.anchorPath = fileExplorerSyncCursorPathForRoot(root, fileExplorerSelectionAnchor);
}

function rememberedFileExplorerSyncExpandedPaths(session, root) {
  const key = fileExplorerSyncTargetKey(session, root);
  const record = fileExplorerSyncTargetRecord(key);
  return record ? fileExplorerSyncUserExpandedPathsForRoot(root, record.userExpansion, true) : [];
}

function fileExplorerSyncCursorPathForRoot(root, path) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const normalizedPath = normalizeDirectoryPath(path || '');
  return normalizedRoot && normalizedPath && normalizedPath !== normalizedRoot
    && pathIsInsideDirectory(normalizedPath, normalizedRoot) ? normalizedPath : '';
}

function fileExplorerSyncPathsForRoot(root, paths) {
  return Array.from(new Set(Array.from(paths || [])
    .map(path => fileExplorerSyncCursorPathForRoot(root, path))
    .filter(Boolean))).sort();
}

function fileExplorerVisibleSyncCursorPath(root, path) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  let candidate = fileExplorerSyncCursorPathForRoot(normalizedRoot, path);
  while (candidate) {
    for (const container of fileExplorerTreeContainers()) {
      if (selectableFileTreeRows(container).some(row => row.dataset.path === candidate)) return candidate;
    }
    const parent = normalizeDirectoryPath(dirnameOf(candidate));
    candidate = parent && parent !== candidate && parent !== normalizedRoot ? parent : '';
  }
  return '';
}

function fileExplorerTreeActiveDescendantId(container, path) {
  const containers = fileExplorerTreeContainers();
  const index = Math.max(0, containers.indexOf(container));
  return `file-explorer-tree-${index}-${encodeURIComponent(path)}`;
}

function syncFileExplorerTreeActiveDescendant(cursorPath = '', options = {}) {
  for (const container of fileExplorerTreeContainers()) {
    if (!cursorPath) {
      container.removeAttribute?.('aria-activedescendant');
      continue;
    }
    const id = fileExplorerTreeActiveDescendantId(container, cursorPath);
    if (options.progressive === true && container.getAttribute?.('aria-activedescendant') === id
      && container.querySelector?.(`[id="${id}"]`)) continue;
    const row = selectableFileTreeRows(container).find(item => item.dataset.path === cursorPath) || null;
    if (!row) {
      container.removeAttribute?.('aria-activedescendant');
      continue;
    }
    if (row.id !== id) row.id = id;
    container.setAttribute?.('aria-activedescendant', id);
    if (options.scrollOwner && (
      !options.scrollOwner.expected.has(container)
      || !deferredElementScrollOwnerOwnsElement(options.scrollOwner, container)
    )) continue;
    // Deliberately do not call focus(): Finder Sync must not take keyboard ownership away from a terminal.
    if (!scrollFileTreeRowIntoView(container, row)) row.scrollIntoView?.({block: 'nearest'});
  }
}

function createFileExplorerSyncSelectionOwner() {
  return {
    paths: new Set(fileExplorerSelectedPaths),
    anchor: fileExplorerSelectionAnchor,
    lead: fileExplorerSelectionLead,
    manual: fileExplorerManualSelectionActive,
    retired: false,
  };
}

function restoreFileExplorerSyncCursorState(session, root, options = {}) {
  const selectionOwner = options.selectionOwner;
  if (selectionOwner && (
    selectionOwner.retired
    || selectionOwner.anchor !== fileExplorerSelectionAnchor
    || selectionOwner.lead !== fileExplorerSelectionLead
    || selectionOwner.manual !== fileExplorerManualSelectionActive
    || selectionOwner.paths.size !== fileExplorerSelectedPaths.size
    || Array.from(selectionOwner.paths).some(path => !fileExplorerSelectedPaths.has(path))
  )) {
    // A user selection made between publication slices owns every later settlement too.
    selectionOwner.retired = true;
    return fileExplorerSelectionLead || '';
  }
  const key = fileExplorerSyncTargetKey(session, root);
  const record = fileExplorerSyncTargetRecord(key, false, true);
  const rememberedSelection = fileExplorerSyncPathsForRoot(root, record?.selectedPaths);
  const requestedCursor = fileExplorerSyncCursorPathForRoot(root, record?.cursorPath);
  let cursorPath = fileExplorerVisibleSyncCursorPath(root, requestedCursor);
  if (!cursorPath) {
    cursorPath = rememberedSelection.map(path => fileExplorerVisibleSyncCursorPath(root, path)).find(Boolean) || '';
  }
  const visibleSelection = rememberedSelection.filter(path => fileExplorerVisibleSyncCursorPath(root, path) === path);
  const nextSelection = new Set(visibleSelection);
  if (cursorPath) nextSelection.add(cursorPath);
  const selectionChanged = nextSelection.size !== fileExplorerSelectedPaths.size
    || Array.from(nextSelection).some(path => !fileExplorerSelectedPaths.has(path));
  fileExplorerSelectedPaths.clear();
  for (const path of visibleSelection) fileExplorerSelectedPaths.add(path);
  if (cursorPath) fileExplorerSelectedPaths.add(cursorPath);
  fileExplorerSelectionLead = cursorPath || null;
  const anchorPath = fileExplorerVisibleSyncCursorPath(root, record?.anchorPath);
  fileExplorerSelectionAnchor = anchorPath || cursorPath || null;
  if (options.progressive !== true || selectionChanged) updateFileExplorerCurrentFileHighlight({yieldToInput: true});
  syncFileExplorerTreeActiveDescendant(cursorPath, options);
  if (selectionOwner) Object.assign(selectionOwner, createFileExplorerSyncSelectionOwner());
  return cursorPath;
}

function setFileExplorerVisibleSyncTarget(session, root, targetKey = fileExplorerSyncTargetKey(session, root)) {
  fileExplorerVisibleSyncTargetKey = targetKey;
  fileExplorerVisibleSyncSession = String(session || '');
  fileExplorerVisibleSyncRoot = normalizeDirectoryPath(root || '');
}

function sessionFilesRepoRoots(payload = fileExplorerFinderSessionFilesState.payload) {
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

function sessionFilesAffectedDirs(payload = fileExplorerFinderSessionFilesState.payload) {
  const dirs = new Set(sessionFilesRepoRoots(payload));
  const directoriesByRepo = new Map();
  for (const file of Array.isArray(payload?.files) ? payload.files : []) {
    const dir = sessionFileDirectory(file);
    if (!dir) continue;
    const rawRepo = String(file?.repo || '').trim();
    const repo = rawRepo ? normalizeDirectoryPath(rawRepo) : '';
    const repoDirs = directoriesByRepo.get(repo) || new Set();
    if (repoDirs.has(dir)) continue;
    repoDirs.add(dir);
    directoriesByRepo.set(repo, repoDirs);
    if (repo && pathIsInsideDirectory(dir, repo)) {
      dirs.add(repo);
      for (const ancestor of ancestorPathsUnderRoot(repo, dir)) dirs.add(ancestor);
    } else {
      dirs.add(dir);
    }
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
  const repoRoots = sessionFilesRepoRoots(payload);
  if (repoRoots.some(path => pathIsInsideDirectory(path, root) || pathIsInsideDirectory(root, path))) return true;
  const paths = sessionFilesAffectedDirs(payload);
  if (!paths.length) return true;
  return paths.some(path => pathIsInsideDirectory(path, root) || pathIsInsideDirectory(root, path));
}

function firstChildPathUnderRoot(root, path) {
  const parts = childPathParts(root, path);
  return parts.length ? childPath(root, parts[0]) : '';
}

function ancestorPathsUnderRoot(root, path) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const normalizedPath = normalizeDirectoryPath(path || '');
  if (!normalizedRoot || !normalizedPath || normalizedPath === normalizedRoot || !pathIsInsideDirectory(normalizedPath, normalizedRoot)) return [];
  const parts = childPathParts(normalizedRoot, normalizedPath);
  const result = [];
  for (let index = 1; index <= parts.length; index += 1) {
    result.push(childPath(normalizedRoot, parts.slice(0, index).join('/')));
  }
  return result;
}

function fileExplorerSyncExpansionTargets(root, affectedDirs = [], repoRoots = []) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  const normalizedRepoRoots = Array.from(new Set(repoRoots
    .map(path => normalizeDirectoryPath(path))
    .filter(Boolean)));
  const normalizedAffectedDirs = Array.from(new Set(affectedDirs
    .map(path => normalizeDirectoryPath(path))
    .filter(Boolean)));
  const commonRoot = commonAncestorPath([...normalizedRepoRoots, ...normalizedAffectedDirs]);
  const candidates = ancestorPathsUnderRoot(normalizedRoot, commonRoot);
  return fileExplorerPathsSortedByDepth(normalizedRoot, Array.from(new Set(candidates
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path !== normalizedRoot && pathIsInsideDirectory(path, normalizedRoot)))));
}

function fileExplorerSyncPlan(preferredItem = null) {
  const session = isTmuxSession(preferredItem) ? preferredItem : fileExplorerExplicitSyncSessionTarget();
  if (!session) return {session: '', root: normalizeDirectoryPath(homePath || '/'), expandPaths: [], affectedDirs: []};
  const {cwd: focusedDir, repository: focusedGitRoot} = fileExplorerSyncSessionContext(session);
  const payload = fileExplorerFinderSessionFilesState.payload;
  // One automatic boundary avoids turning a large change set into a directory traversal.
  const boundary = focusedGitRoot
    || (payload?.session === session ? commonAncestorPath(sessionFilesAffectedDirs(payload)) : '')
    || focusedDir;
  const affectedDirs = [boundary].filter(Boolean);
  const repoRoots = [];
  if (focusedGitRoot && !repoRoots.includes(focusedGitRoot)) repoRoots.push(focusedGitRoot);
  let root = commonAncestorPath(affectedDirs);
  if (!root) root = normalizeDirectoryPath(homePath || '/');
  if (root && pathIsShallowerThanHome(root)) {
    root = focusedRepoRootForSync(focusedDir, repoRoots) || normalizeDirectoryPath(homePath || '/');
  }
  const normalizedHome = normalizeDirectoryPath(homePath || '');
  const rootBeforeHomeLift = normalizeDirectoryPath(root || '');
  if (normalizedHome && rootBeforeHomeLift && rootBeforeHomeLift !== normalizedHome && pathIsInsideDirectory(rootBeforeHomeLift, normalizedHome)) {
    root = normalizedHome;
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
  const targetDir = normalizeDirectoryPath(dirnameOf(target));
  let root = repo && pathIsInsideDirectory(target, repo) ? repo : '';
  if (!root && currentRoot && pathIsInsideDirectory(target, currentRoot) && target !== currentRoot) root = currentRoot;
  if (!root) root = targetDir;
  const normalizedHome = normalizeDirectoryPath(homePath || '');
  const rootBeforeHomeLift = normalizeDirectoryPath(root || '');
  if (normalizedHome && rootBeforeHomeLift && rootBeforeHomeLift !== normalizedHome && pathIsInsideDirectory(rootBeforeHomeLift, normalizedHome)) {
    root = normalizedHome;
  }
  const session = typeof sessionForFileRepo === 'function' ? sessionForFileRepo(target) : '';
  const expandPaths = root && targetDir !== root && pathIsInsideDirectory(targetDir, root) ? [targetDir] : [];
  return {
    session,
    root,
    expandPaths,
    affectedDirs: [targetDir].filter(Boolean),
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
    && key === fileExplorerSyncState.appliedPlanKey
    && normalizeDirectoryPath(plan.root) === normalizeDirectoryPath(currentFileExplorerRoot());
}

function markFileExplorerSyncPlanApplied(plan) {
  fileExplorerSyncState.appliedPlanKey = fileExplorerSyncPlanKey(plan);
}

function fileExplorerSyncManualCollapseKey(plan) {
  return plan?.root ? plan.targetKey || fileExplorerSyncTargetKey(plan.session, plan.root) : '';
}

function rememberFileExplorerSyncManualCollapseState(targetKey = fileExplorerSyncManualCollapseTargetKey) {
  if (!targetKey) return;
  const record = fileExplorerSyncTargetRecord(targetKey, true, true);
  record.manualCollapsedPaths = fileExplorerSyncManualCollapsedPaths;
}

function resetFileExplorerSyncManualCollapsesIfNeeded(plan) {
  const targetKey = fileExplorerSyncManualCollapseKey(plan);
  if (targetKey !== fileExplorerSyncManualCollapseTargetKey) {
    let record = fileExplorerSyncTargetRecord(targetKey);
    rememberFileExplorerSyncManualCollapseState();
    fileExplorerSyncManualCollapseTargetKey = targetKey;
    if (!record && targetKey) record = fileExplorerSyncTargetRecord(targetKey, true, false);
    if (record) setLimitedMapEntry(fileExplorerSyncTargetRecords, targetKey, record, fileExplorerMemoryCacheLimit);
    fileExplorerSyncManualCollapsedPaths = record?.manualCollapsedPaths || new Set();
    fileExplorerSyncUserExpansionState = record?.userExpansion || new Map();
  }
  const record = fileExplorerSyncTargetRecord(targetKey);
  const context = record && fileExplorerSyncLegacyExpansionEntries ? fileExplorerSyncSessionContext(plan.session) : null;
  const repository = context?.repository || context?.cwd;
  if (record && fileExplorerSyncLegacyExpansionEntries && repository) {
    const chain = new Set(plan.expandPaths || []);
    for (const [rawPath, expanded] of fileExplorerSyncLegacyExpansionEntries) {
      const path = normalizeDirectoryPath(rawPath);
      if (path && !record.userExpansion.has(path) && (chain.has(path) || pathIsInsideDirectory(path, repository))) record.userExpansion.set(path, expanded);
    }
    record.expandedPaths = Array.from(record.userExpansion).filter(([, open]) => open).map(([path]) => path);
    fileExplorerSyncLegacyExpansionEntries = null;
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
    return {session: fileExplorerVisibleSyncSession, root: visibleRoot, targetKey: fileExplorerVisibleSyncTargetKey};
  }
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const explicitPlan = fileExplorerSyncPlan(explicitSession);
  if (explicitSession && explicitPlan?.root && pathIsInsideDirectory(normalized, explicitPlan.root)) return explicitPlan;
  const currentRoot = normalizeDirectoryPath(currentFileExplorerRoot());
  const fallbackSession = explicitSession || fileExplorerVisibleSyncSession || fileExplorerFinderTargetSession();
  if (fallbackSession && currentRoot && pathIsInsideDirectory(normalized, currentRoot)) {
    return {session: fallbackSession, root: currentRoot};
  }
  return null;
}

function fileExplorerSyncPathSuppressed(path) {
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized) return false;
  for (const [userPath, expanded] of fileExplorerSyncUserExpansionState) {
    if (expanded === false && pathIsInsideDirectory(normalized, userPath)) return true;
  }
  for (const collapsedPath of fileExplorerSyncManualCollapsedPaths) {
    if (pathIsInsideDirectory(normalized, collapsedPath)) return true;
  }
  return false;
}

function fileExplorerSyncPathRetiredByParent(path) {
  const record = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', path));
  return record?.failureStatus === 400 || record?.failureStatus === 404;
}

function fileExplorerSyncExpansionPaths(plan) {
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  return (plan?.expandPaths || []).filter(path => (
    !fileExplorerSyncPathSuppressed(path)
    && !fileExplorerSyncPathRetiredByParent(path)
  ));
}

function fileExplorerSyncExplicitExpansionTargets(plan, root = plan?.root || currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  if (!normalizedRoot) return [];
  const expansionPaths = fileExplorerSyncExpansionPaths(plan);
  return fileExplorerPathsSortedByDepth(normalizedRoot, Array.from(new Set([
    ...expansionPaths,
    ...(plan?.affectedDirs || []),
  ]
    .map(path => normalizeDirectoryPath(path))
    .filter(path => (
      path
      && path !== normalizedRoot
      && pathIsInsideDirectory(path, normalizedRoot)
      && !fileExplorerSyncPathSuppressed(path)
    )))));
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
  setFileExplorerSyncUserExpansion(path, false);
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
    if (collapsedPath === normalized || pathIsInsideDirectory(normalized, collapsedPath)) {
      fileExplorerSyncManualCollapsedPaths.delete(collapsedPath);
      fileExplorerSyncUserExpansionState.delete(collapsedPath);
      changed = true;
    }
  }
  if (fileExplorerRootMode === 'sync' && normalized) setFileExplorerSyncUserExpansion(normalized, true);
  if (changed) rememberFileExplorerSyncManualCollapseState();
  if (changed) resetFileExplorerAppliedSyncPlan();
  return changed;
}

function emptyFileExplorerSessionHighlightSets() {
  return {repoRoots: new Set(), touchedDirs: new Set(), expandedDirs: new Set(), syncTargetDirs: new Set(), syncTargetTitle: ''};
}

function fileExplorerSyncTargetDirs(plan) {
  const root = normalizeDirectoryPath(plan?.root || '');
  return Array.from(new Set((plan?.affectedDirs || [])
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && root && pathIsInsideDirectory(path, root))));
}

function fileExplorerSessionHighlightSets(preferredItem = null, context = {}) {
  if (fileExplorerRootMode !== 'sync') return emptyFileExplorerSessionHighlightSets();
  const targetSession = isTmuxSession(preferredItem) ? preferredItem : fileExplorerExplicitSyncSessionTarget();
  if (!targetSession) return emptyFileExplorerSessionHighlightSets();
  const {repository: focusedGitRoot, cwd: focusedDir} = fileExplorerSyncSessionContext(targetSession);
  const payload = fileExplorerFinderSessionFilesState.payload;
  if (
    !payload?.session
    || (targetSession && String(payload.session) !== String(targetSession))
    || !sessionFilesPayloadOverlapsFocusedRoot(payload, focusedGitRoot)
  ) {
    return emptyFileExplorerSessionHighlightSets();
  }
  const repoRoots = new Set(sessionFilesRepoRoots(payload));
  if (context.payload !== payload || context.targetSession !== targetSession
    || context.focusedDir !== focusedDir || context.focusedGitRoot !== focusedGitRoot) {
    context.payload = payload;
    context.targetSession = targetSession;
    context.focusedDir = focusedDir;
    context.focusedGitRoot = focusedGitRoot;
    context.plan = fileExplorerSyncPlan(targetSession);
  }
  const plan = context.plan;
  const expandedDirs = new Set(fileExplorerSyncHighlightedRepoRoots(plan, repoRoots));
  const syncTargetDirs = new Set(fileExplorerSyncTargetDirs(plan));
  const sessionLabel = targetSession || plan.session || '';
  return {
    repoRoots: new Set(),
    touchedDirs: new Set(),
    expandedDirs,
    syncTargetDirs,
    syncTargetTitle: t('finder.syncTarget.title', {session: sessionLabel}),
  };
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
  if (!row || fileTreeRowIsDiffer(row)) return;
  const path = normalizeDirectoryPath(row.dataset?.path || '');
  const kind = row.dataset?.kind || '';
  const highlightClass = fileExplorerSessionHighlightClassForPath(path, kind, {sessionHighlightSets: sets});
  applySessionHighlightRowClass(row, highlightClass);
  const isSyncTarget = kind === 'dir' && Boolean(path && sets.syncTargetDirs?.has(path));
  row.classList.toggle('file-tree-row--sync-target', isSyncTarget);
  if (isSyncTarget) row.dataset.syncTargetTitle = sets.syncTargetTitle || '';
  else delete row.dataset.syncTargetTitle;
  updateFileTreeSyncTargetMarker(row, isSyncTarget, sets.syncTargetTitle);
}

function fileExplorerPathInputs() {
  return [
    fileExplorerPath,
    ...Array.from(document.querySelectorAll('.file-explorer-path-inline')),
  ].filter(Boolean);
}

function setFileExplorerPathElementValue(node, path) {
  if (!node) return;
  const display = typeof compactHomePath === 'function' ? (compactHomePath(path) || path) : path;
  if ('value' in node) node.value = display;
  else node.textContent = display;
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
  refreshFileExplorerRepoDisplay(normalized, {error, cacheOnly: options.deferRepoInfo === true});
}

function displayedFileExplorerRoot() {
  const inputs = fileExplorerPathInputs();
  const valueFor = node => ('value' in node ? node.value : node.textContent) || '';
  // Passive activation normally sees identical committed paths. Only a divergent
  // pending display needs visibility geometry to choose its current surface.
  if (inputs.length && inputs.every(node => valueFor(node) === valueFor(inputs[0]))) {
    return normalizeDirectoryPath(expandUserPath(valueFor(inputs[0]) || currentFileExplorerRoot()));
  }
  const input = inputs.find(node => typeof node.getClientRects !== 'function' || node.getClientRects().length > 0) || inputs[0];
  const value = input ? ('value' in input ? input.value : input.textContent) : '';
  return normalizeDirectoryPath(expandUserPath(value || currentFileExplorerRoot()));
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

function repoBranchDisplayText(repo) {
  return repo?.branch || (repo?.detached === true ? t('git.detached') : '');
}

function repoInfoSummary(repo) {
  if (!repo?.root) return '';
  const details = [
    repoBranchDisplayText(repo),
    Number.isFinite(Number(repo.dirty_count)) && Number(repo.dirty_count) > 0 ? t('git.dirty', {count: Number(repo.dirty_count)}) : '',
    Number.isFinite(Number(repo.ahead)) && Number(repo.ahead) > 0 ? t('git.ahead', {count: Number(repo.ahead)}) : '',
    Number.isFinite(Number(repo.behind)) && Number(repo.behind) > 0 ? t('git.behind', {count: Number(repo.behind)}) : '',
  ].filter(Boolean);
  const name = repo.name || basenameOf(repo.root);
  return details.length ? `${name} (${details.join(', ')})` : name;
}

function setFileExplorerRepoSummary(path, repo, error = '') {
  const summary = error ? '' : repoInfoSummary(repo);
  const branch = repoBranchDisplayText(repo);
  const title = error || (repo?.root ? [
    `${t('popover.repo')}: ${repo.root}`,
    branch ? `${t('common.field.branch')}: ${branch}` : '',
    repo.upstream ? `↗ ${repo.upstream}` : '',
    Number.isFinite(Number(repo.dirty_count)) ? t('git.dirty', {count: repo.dirty_count}) : '',
    Number(repo.ahead) ? t('git.ahead', {count: repo.ahead}) : '',
    Number(repo.behind) ? t('git.behind', {count: repo.behind}) : '',
  ].filter(Boolean).join('\n') : '');
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
  if (options.cacheOnly === true) {
    setFileExplorerRepoSummary(normalized, null);
    return;
  }
  setFileExplorerRepoSummary(normalized, null);
  scheduleFileExplorerRepoInfoEnrichment(normalized, [], {includeRoot: true, immediate: true});
}

// Rich git info shown in a styled hover popover for a repo dir (replaces the native title tooltip).
function repoInfoPopoverHtml(repo) {
  if (!repo?.root) return '';
  const rows = [`<div class="file-tree-repo-popover-title">${esc(repo.name || basenameOf(repo.root))}</div>`];
  const branch = repoBranchDisplayText(repo);
  if (branch) rows.push(`<div class="file-tree-repo-popover-branch">⎇ ${esc(branch)}</div>`);
  if (repo.head_sha) rows.push(`<div class="file-tree-repo-popover-sha">${esc(t('menu.help.about.sha', {sha: repo.head_sha}))}</div>`);
  if (repo.upstream) rows.push(`<div class="meta-muted">↗ ${esc(repo.upstream)}</div>`);
  const stat = [];
  if (Number(repo.ahead) > 0) stat.push(t('git.ahead', {count: Number(repo.ahead)}));
  if (Number(repo.behind) > 0) stat.push(t('git.behind', {count: Number(repo.behind)}));
  if (Number.isFinite(Number(repo.dirty_count))) stat.push(t('git.dirty', {count: Number(repo.dirty_count)}));
  if (stat.length) rows.push(`<div class="file-tree-repo-popover-stat">${esc(stat.join(' · '))}</div>`);
  rows.push(`<div class="file-tree-repo-popover-path">${esc(repo.root)}</div>`);
  return rows.join('');
}

function fileTreeSyncTargetPopoverHtml(row) {
  const title = String(row?.dataset?.syncTargetTitle || '').trim();
  return title ? `<div class="file-tree-repo-popover-title">★ ${esc(title)}</div>` : '';
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

function showFileTreeRepoPopover(row, repo) {
  const content = repo?.root ? repoInfoPopoverHtml(repo) : fileTreeSyncTargetPopoverHtml(row);
  if (!content) return;
  const node = fileTreeRepoPopoverNode();
  node.innerHTML = content;
  node.hidden = false;
  const popRect = node.getBoundingClientRect?.(), pos = clampToViewport(
    Math.round(Number(row.dataset.repoPopoverX || 0) + 14), Math.round(Number(row.dataset.repoPopoverY || 0) + 4),
    Math.ceil(popRect?.width || 0), Math.ceil(popRect?.height || 0),
  );
  Object.assign(node.style, {left: `${Math.round(pos.left)}px`, top: `${Math.round(pos.top)}px`});
}

function hideFileTreeRepoPopover() { document.getElementById('fileTreeRepoPopover')?.setAttribute('hidden', ''); }

async function showRepoRowHoverPopover(row, path) {
  const normalized = normalizeDirectoryPath(path), cached = fileExplorerRepoInfoCache.get(normalized);
  // Show immediately from cache (branch/ahead/behind), then lazily fetch full status (incl dirty).
  showFileTreeRepoPopover(row, cached);
  if (cached && cached.head_sha !== null && Number.isFinite(Number(cached.dirty_count))) return;
  if (row.dataset.repoTitleLoaded === 'true') return;
  row.dataset.repoTitleLoaded = 'true';
  try {
    const info = await fetchFilePathInfo(normalized);
    if (info.repo) {
      cacheFileExplorerRepoInfo(normalized, info.repo);
      scheduleFileTreeMetadataRefresh();
      if (row.dataset.popoverHoverState === 'open') showFileTreeRepoPopover(row, info.repo);
    }
  } catch (_) {}
}

function fileTreeRepoHoverController(row, path) {
  if (row.__yomuxRepoHoverController) return row.__yomuxRepoHoverController;
  const updatePointer = event => Object.assign(row.dataset, {repoPopoverX: String(event?.clientX || row.dataset.repoPopoverX || 0), repoPopoverY: String(event?.clientY || row.dataset.repoPopoverY || 0)});
  return row.__yomuxRepoHoverController = createHoverPopover({
    anchor: row,
    popover: fileTreeRepoPopoverNode,
    stateClass: null,
    canOpen: () => row.dataset.isRepo === 'true' || Boolean(row.dataset.syncTargetTitle),
    position: event => {
      updatePointer(event);
      const repo = fileExplorerRepoInfoCache.get(normalizeDirectoryPath(path));
      showFileTreeRepoPopover(row, repo);
    },
    onPointerMove: updatePointer,
    onOpen: () => { if (row.dataset.isRepo === 'true') void showRepoRowHoverPopover(row, path); },
    onClose: hideFileTreeRepoPopover,
  });
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
  if (!input) return null;
  return bindScopedOnce(input, 'file-explorer-path-input', scope => {
  scope.ownEvent('keydown', input, 'keydown', event => {
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
  scope.ownEvent('focus', input, 'focus', () => input.select?.());
  });
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
  return fileExplorerItemIds.some(item => itemInLayout(item));
}

function fileExplorerTreePaneIsVisible() {
  return itemIsActivePaneTab(finderItemId);
}

function fileExplorerSessionFilesPaneIsVisible() {
  return itemIsActivePaneTab(differItemId);
}

function scheduleFileExplorerActiveTabSync(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen()) return;
  if (fileExplorerRootMode !== 'sync') return;
  const explicit = options.explicit === true;
  if (fileExplorerManualSelectionActive && !explicit) return;
  // A startup root open can still be pending without an in-flight Sync signature.
  if (explicit) fileWorkspaceState.invalidateInteraction();
  if (explicit) setFileExplorerSelectionPin(false);
  if (options.explicit === true && isTmuxSession(preferredItem)) rememberFileExplorerExplicitSyncSession(preferredItem);
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  const fileSyncPath = explicit && isFileEditorItem(preferredItem) ? fileItemPath(preferredItem) : '';
  const disclosureChanged = fileSyncPath ? forgetFileExplorerSyncManualCollapse(dirnameOf(fileSyncPath)) : false;
  const syncItem = fileSyncPath ? preferredItem : (isTmuxSession(preferredItem) ? preferredItem : explicitSession);
  if (!syncItem || (!fileSyncPath && syncItem !== explicitSession)) return;
  const syncPlan = fileSyncPath ? fileExplorerSyncPlanForFile(fileSyncPath) : fileExplorerSyncPlan(syncItem);
  const expandPaths = fileExplorerSyncExpansionPaths(syncPlan);
  const syncSignature = fileExplorerSyncPlanSignature(syncPlan);
  const staleInFlightSync = cancelSupersededFileExplorerSyncPlan(syncPlan, {invalidateOpen: explicit, force: disclosureChanged});
  const syncTargetChanged = explicit && Boolean(fileExplorerVisibleSyncSession)
    && String(syncPlan.session || '') !== String(fileExplorerVisibleSyncSession || '');
  if (
    syncPlan.root
    && (syncPlan.root !== currentFileExplorerRoot() || expandPaths.length || syncTargetChanged || (explicit && staleInFlightSync))
    && fileExplorerSyncState.inFlightSignature !== syncSignature
    && (explicit || !fileExplorerSyncPlanAlreadyApplied(syncPlan))
  ) {
    const interactionGeneration = fileWorkspaceState.interactionGeneration();
    requestAnimationFrame(() => {
      if (!fileWorkspaceState.interactionIsCurrent(interactionGeneration)) return;
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
  if (options.guardExplicitTarget === true && isTmuxSession(plan.session)) {
    return fileExplorerExplicitSyncSessionTarget() === String(plan.session);
  }
  return true;
}

function fileExplorerSyncTransactionStillCurrent(plan, signature, options = {}) {
  return fileExplorerSyncState.inFlightSignature === signature
    && fileExplorerSyncPlanTargetStillCurrent(plan, options);
}

function beginFileExplorerSync(signature, renderTransaction = null) {
  fileExplorerSyncState.inFlightSignature = signature;
  fileExplorerSyncState.renderTransaction = renderTransaction;
  return ++fileExplorerSyncState.generation;
}

function finishFileExplorerSync(signature, generation) {
  if (fileExplorerSyncState.generation !== generation || fileExplorerSyncState.inFlightSignature !== signature) return;
  fileExplorerSyncState.inFlightSignature = '';
  fileExplorerSyncState.renderTransaction = null;
}

function cancelPendingFileExplorerActiveSync(options = {}) {
  fileWorkspaceState.invalidateInteraction({invalidateOpen: options.invalidateOpen !== false});
  finishFileExplorerSync(fileExplorerSyncState.inFlightSignature, fileExplorerSyncState.generation);
  fileExplorerSyncState.generation += 1;
  fileExplorerSyncState.inFlightSignature = '';
  fileExplorerSyncState.publication = null;
  resetFileExplorerAppliedSyncPlan();
}

function cancelSupersededFileExplorerSyncPlan(plan, options = {}) {
  const signature = fileExplorerSyncPlanSignature(plan);
  if (!signature || !fileExplorerSyncState.inFlightSignature
    || (fileExplorerSyncState.inFlightSignature === signature && options.force !== true)) return false;
  const alreadyApplied = fileExplorerSyncPlanAlreadyApplied(plan);
  cancelPendingFileExplorerActiveSync(options);
  // Returning to the visible plan still retires a different pending target.
  if (alreadyApplied) markFileExplorerSyncPlanApplied(plan);
  return true;
}

async function syncFileExplorerRootToActiveTmux(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  return syncFileExplorerRootToPlan(fileExplorerSyncPlan(preferredItem), preferredItem, {
    ...options,
    guardExplicitTarget: options.force === true,
  });
}

async function syncFileExplorerRootToActiveFile(path, options = {}) {
  if (!fileExplorerIsOpen() || fileExplorerRootMode !== 'sync') return false;
  return syncFileExplorerRootToPlan(fileExplorerSyncPlanForFile(path), fileEditorItemFor(path), options);
}

function fileExplorerSyncRenderPaths(plan, rememberedExpandedPaths = []) {
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  const root = normalizeDirectoryPath(plan?.root || '');
  if (!root) return [];
  return fileExplorerEffectiveExpandedPaths(root, [
    ...rememberedExpandedPaths,
    ...fileExplorerSyncUserExpandedPathsForRoot(root),
    ...fileExplorerSyncExpansionPaths(plan).flatMap(path => ancestorPathsUnderRoot(root, path)),
  ]).filter(path => !fileExplorerSyncPathSuppressed(path));
}

function fileExplorerSyncListingDirectories(plan, renderPaths = []) {
  const root = normalizeDirectoryPath(plan?.root || '');
  const directories = new Set(root ? [root] : []);
  for (const path of renderPaths) {
    for (const ancestor of ancestorPathsUnderRoot(root, path)) directories.add(ancestor);
  }
  return Array.from(directories);
}

function cachedFileExplorerSyncListings(directories = []) {
  const entriesByDir = new Map();
  for (const directory of directories) {
    const entries = cachedFileExplorerFsResourceValue('list', directory);
    if (!Array.isArray(entries)) return null;
    entriesByDir.set(normalizeDirectoryPath(directory), entries);
  }
  return entriesByDir;
}

function activeFileExplorerSyncPublication(root) {
  const publication = fileExplorerSyncState.publication;
  return publication && publication.root === normalizeDirectoryPath(root) && publication.isCurrent() ? publication : null;
}

function renderCachedFileExplorerSyncPlan(plan, renderPaths, entriesByDir, options = {}) {
  options = {...options, renderTransaction: options.renderTransaction || {epoch: ++fileTreeRenderEpoch, completed: true}};
  const steps = renderCachedFileExplorerSyncPlanSteps(plan, renderPaths, entriesByDir, options);
  return options.yieldToInput ? drainFileTreeRenderStepsWithInput(steps, options) : drainFileTreeRenderSteps(steps);
}

function* renderCachedFileExplorerSyncPlanSteps(plan, renderPaths, entriesByDir, options = {}) {
  const root = normalizeDirectoryPath(plan?.root || '');
  const rootEntries = entriesByDir?.get(root);
  if (!root || !Array.isArray(rootEntries)) return false;
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  const qualifiedRenderPaths = qualifyFileExplorerDirectoryPaths(root, renderPaths, entriesByDir, options.qualificationContext)
    .filter(path => !fileExplorerSyncPathSuppressed(path));
  const qualifiedExpansionPaths = new Set();
  for (const path of qualifiedRenderPaths) {
    if (fileExplorerSyncPathSuppressed(path)) continue;
    qualifiedExpansionPaths.add(path);
    for (const ancestor of ancestorPathsUnderRoot(root, path)) {
      if (!fileExplorerSyncPathSuppressed(ancestor)) qualifiedExpansionPaths.add(ancestor);
    }
  }
  const rootChanged = root !== currentFileExplorerRoot();
  const containers = fileExplorerTreeContainers();
  const finderTrees = containers.filter(tree => tree === fileExplorerTree || tree.closest?.('.file-explorer-panel'));
  const firstPaint = rootChanged || !finderTrees.some(tree => tree.querySelector?.('.file-tree-row[data-path]'));
  if (firstPaint && typeof beginFinderSessionFilesDiffBackfill === 'function') {
    const fastPaintGeneration = beginFinderSessionFilesDiffBackfill();
    if (options.renderTransaction) options.renderTransaction.fastPaintGeneration = fastPaintGeneration;
  }
  const preserveState = options.preserveState === true && !rootChanged;
  const scrollPositions = preserveState ? captureFileExplorerScrollPositions({deferred: options.yieldToInput === true, containers}) : null;
  if (options.yieldToInput && scrollPositions) {
    options.afterSlice = () => {
      if (options.isCurrent && !options.isCurrent()) return;
      for (const {element, position, deferredOwner} of scrollPositions) {
        if (
          element.isConnected !== false
          && (element.scrollTop !== position.top || element.scrollLeft !== position.left)
        ) writeDeferredElementScrollIfOwned(deferredOwner, element, position, 'finder-tree-render-restore');
      }
    };
  }
  fileExplorerRoot = root;
  if (rootChanged) pruneFileExplorerSelectionForRoot(root);
  options.onPrepared?.();
  // Listing arrivals update the tree, not the already-published target controls.
  if (firstPaint || !(options.listingDirectories instanceof Set)) {
    setFileExplorerPathDisplay(root);
    renderFileExplorerRootModeControls();
  }
  fileExplorerExpanded.clear();
  const targetRecord = fileExplorerSyncTargetRecord(fileExplorerSyncManualCollapseTargetKey);
  for (const path of targetRecord?.expandedPaths || []) {
    if (!fileExplorerSyncManualCollapsedPaths.has(path) && fileExplorerSyncUserExpansionState.get(path) !== false) fileExplorerExpanded.add(path);
  }
  for (const path of fileExplorerSyncUserExpandedPathsForRoot(root)) {
    if (!fileExplorerSyncPathSuppressed(path)) fileExplorerExpanded.add(path);
  }
  for (const path of qualifiedExpansionPaths) fileExplorerExpanded.add(path);
  const payloadContext = options.payloadContext || fileTreeSessionFilesRenderContext();
  const renderOptions = {
    view: 'finder', entriesByDir, deferSessionFilesDiff: firstPaint,
    yieldToInput: options.yieldToInput === true,
    reconcileDirectories: firstPaint ? null : options.reconcileDirectories,
    listingDirectories: firstPaint ? null : options.listingDirectories,
    sessionFilesMap: payloadContext.sessionFilesMap,
    sessionHighlightSets: options.sessionHighlightSets || fileExplorerSessionHighlightSets(),
    changedAncestorStats: payloadContext.changedAncestorStats,
    isCurrent: options.isCurrent, refreshContext: options.refreshContext, renderTransaction: options.renderTransaction,
  };
  for (const tree of containers) {
    if (tree === fileExplorerTree || tree.closest?.('.panel.file-explorer-panel')) {
      if (options.isCurrent && !options.isCurrent()) return false;
      if ((yield* renderTreeChildrenSteps(tree, root, rootEntries, 0, renderOptions)) === false) {
        if (!options.isCurrent || options.isCurrent()) resetFileExplorerAppliedSyncPlan();
        return false;
      }
    }
  }
  if (options.isCurrent && !options.isCurrent()) return false;
  if (!(renderOptions.reconcileDirectories instanceof Set)) updateFileExplorerCurrentFileHighlight({yieldToInput: true});
  if (options.afterSlice) options.afterSlice();
  else if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions, {containers});
  if (!options.renderTransaction.completed) {
    if (!options.isCurrent || options.isCurrent()) resetFileExplorerAppliedSyncPlan();
    return false;
  }
  return true;
}

async function traverseFileExplorerDirectoryQueue(queue, visit, isCurrent = () => true) {
  const active = new Set();
  let nextIndex = 0;
  try {
    while (isCurrent() && (nextIndex < queue.length || active.size)) {
      while (isCurrent() && nextIndex < queue.length && active.size < 8) {
        const directory = queue[nextIndex++];
        const work = (async () => {
          // Register the task before a synchronous visitor failure can retire it.
          await Promise.resolve();
          try {
            await visit(directory);
          } finally {
            active.delete(work);
          }
        })();
        active.add(work);
      }
      if (active.size) await Promise.race(active);
    }
  } finally {
    // Supersession stops admission; requests already admitted keep their existing deadlines.
    await Promise.allSettled(active);
  }
}

async function fetchFileExplorerSyncListings(directories = [], options = {}, onListing = null, seededEntries = null, onFailure = null) {
  const {isCurrent = () => true, qualificationContext = {}, onIncomplete = () => {}, ...fetchOptions} = options;
  const normalizedDirectories = Array.from(new Set(directories.map(directory => normalizeDirectoryPath(directory)).filter(Boolean)));
  const root = normalizedDirectories[0] || '';
  const entriesByDir = seededEntries instanceof Map ? new Map(seededEntries) : new Map();
  if (!root || !isCurrent()) return entriesByDir;
  const publicationOwners = new Map();
  const rememberPublication = directory => {
    const key = fileExplorerFsBatchKey('list', directory);
    const record = fileExplorerFsResourceRecords.get(key);
    publicationOwners.set(directory, {key, record, hadRecord: fileExplorerFsResourceRecords.has(key), generation: record?.generation, revision: Number(record?.revision) || 0});
  };
  for (const directory of entriesByDir.keys()) {
    const record = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', directory));
    if (record && !record.failureStatus && Array.isArray(record.value)) entriesByDir.set(directory, record.value);
    rememberPublication(directory);
  }
  const reconcilePublication = directory => {
    const owner = publicationOwners.get(directory);
    if (!owner) return entriesByDir.has(directory);
    const publication = fileExplorerDirectoryListingPublication(owner.key, owner.record, owner.generation,
      owner.revision, entriesByDir.get(directory), owner.hadRecord);
    if (!publication.current || !Array.isArray(publication.entries)) {
      entriesByDir.delete(directory);
      onIncomplete(directory);
      return false;
    }
    entriesByDir.set(directory, publication.entries);
    rememberPublication(directory);
    return true;
  };
  const listDirectory = async directory => {
    const {entries, generation, current} = await fetchAuthoritativeFileExplorerDirectoryListing(directory, fetchOptions);
    if (!isCurrent()) return false;
    if (!current) {onIncomplete(directory); return false;}
    if (Array.isArray(entries)) {
      entriesByDir.set(directory, entries);
      rememberPublication(directory);
      onListing?.(directory, entries, entriesByDir);
      return true;
    }
    onFailure?.(directory, generation);
    onIncomplete(directory);
    return false;
  };
  const useDirectory = directory => entriesByDir.has(directory)
    ? reconcilePublication(directory) : listDirectory(directory);
  if (normalizedDirectories.slice(1).some(directory => !pathIsInsideDirectory(directory, root))) {
    await traverseFileExplorerDirectoryQueue(normalizedDirectories, useDirectory, isCurrent);
    return entriesByDir;
  }
  if (!await useDirectory(root) || !isCurrent()) return entriesByDir;
  const candidatePaths = normalizedDirectories.slice(1);
  const candidatesByParent = new Map();
  for (const path of candidatePaths) {
    const parent = normalizeDirectoryPath(dirnameOf(path));
    const siblings = candidatesByParent.get(parent) || [];
    siblings.push(path);
    candidatesByParent.set(parent, siblings);
  }
  const queue = [];
  const queued = new Set();
  const enqueueChildren = parent => {
    if (!reconcilePublication(parent)) return;
    const candidates = candidatesByParent.get(parent) || [];
    for (const path of qualifyFileExplorerDirectoryPaths(root, candidates, entriesByDir, qualificationContext)) {
      if (!queued.has(path)) {
        queued.add(path);
        queue.push(path);
      }
    }
  };
  enqueueChildren(root);
  await traverseFileExplorerDirectoryQueue(queue, async directory => {
    const ancestry = ancestorPathsUnderRoot(root, directory);
    if (ancestry.some(path => !reconcilePublication(normalizeDirectoryPath(dirnameOf(path))))) return;
    if (!qualifyFileExplorerDirectoryPaths(root, ancestry, entriesByDir, qualificationContext).includes(directory)) return;
    if (await useDirectory(directory) && isCurrent()) enqueueChildren(directory);
  }, isCurrent);
  return entriesByDir;
}

async function fetchAuthoritativeFileExplorerDirectoryListing(directory, options = {}) {
  const key = fileExplorerFsBatchKey('list', directory);
  const hadRecord = fileExplorerFsResourceRecords.has(key);
  const request = fetchDirectory(directory, options);
  const record = fileExplorerFsResourceRecords.get(key);
  const generation = record?.generation;
  const revision = Number(record?.revision) || 0;
  const entries = await request;
  return fileExplorerDirectoryListingPublication(key, record, generation, revision, entries, hadRecord);
}

function fileExplorerDirectoryListingPublication(key, record, generation, revision, entries, hadRecord) {
  const current = record
    ? fileExplorerFsResourceCurrent(key, record, generation) && (entries === null || (!record.failureStatus && record.value === entries))
    : !hadRecord && !fileExplorerFsResourceRecords.has(key);
  // A newer accepted push owns this directory even while the older read settles. Continue
  // traversal through that publication, without adopting a pre-request cache or a new owner.
  const newerPublication = !current && record && fileExplorerFsResourceRecords.get(key) === record
    && Number(record.revision) > revision && !record.failureStatus && Array.isArray(record.value);
  return {
    entries: newerPublication ? record.value : entries,
    generation: newerPublication ? record.generation : generation,
    current: current || Boolean(newerPublication),
  };
}

function scheduleFileExplorerSyncRevalidation(plan, renderPaths, signature, options = {}) {
  const generation = fileExplorerSyncState.generation;
  const fastPaintGeneration = options.renderTransaction.fastPaintGeneration;
  const scrollOwner = createInputOwnedDeferredElementScrollOwner(...fileExplorerTreeContainers());
  const selectionOwner = options.selectionOwner || createFileExplorerSyncSelectionOwner();
  const isCurrent = () => fileExplorerSyncState.generation === generation
    && fileExplorerSyncPlanSignature(plan) === signature
    && fileExplorerSyncTargetKey(plan.session, plan.root) === fileExplorerSyncTargetKey(fileExplorerVisibleSyncSession, fileExplorerVisibleSyncRoot)
    && fileExplorerSyncPlanTargetStillCurrent(plan);
  requestAnimationFrame(() => {
    if (!isCurrent()) return;
    if (typeof finishFinderFastPaint === 'function') finishFinderFastPaint(fastPaintGeneration);
    const directories = fileExplorerSyncListingDirectories(plan, renderPaths);
    const previousSignatures = new Map(directories.map(path => [path, fileExplorerDirectoryRecord(path)?.signature]));
    const previousUserExpansion = new Map(directories.map(path => [path, fileExplorerSyncUserExpansionState.get(path)]));
    const failedDirectories = new Map();
    void fetchFileExplorerSyncListings(directories, {
      fresh: true, force: true, trigger: 'sync-revalidation',
      isCurrent,
      onIncomplete: () => { if (isCurrent()) resetFileExplorerAppliedSyncPlan(); },
    }, null, null, (directory, failedGeneration) => failedDirectories.set(normalizeDirectoryPath(directory), failedGeneration)).then(async entriesByDir => {
      if (!isCurrent()) return;
      const changed = directories.some(path => previousSignatures.get(path) !== fileExplorerDirectoryRecord(path)?.signature);
      if (changed) {
        const currentRenderPaths = fileExplorerSyncRenderPaths(
          plan,
          rememberedFileExplorerSyncExpandedPaths(plan.session, plan.root),
        );
        const currentDirectories = fileExplorerSyncListingDirectories(plan, currentRenderPaths);
        const completeEntriesByDir = cachedFileExplorerSyncListings(currentDirectories) || entriesByDir;
        const context = options.refreshContext?.();
        const publication = activeFileExplorerSyncPublication(plan.root);
        const rendered = publication
          ? await publication.publish(completeEntriesByDir)
          : await renderCachedFileExplorerSyncPlan(plan, currentRenderPaths, completeEntriesByDir, {
            preserveState: true, yieldToInput: true, isCurrent,
            payloadContext: context, sessionHighlightSets: context?.sessionHighlightSets,
            refreshContext: options.refreshContext, renderTransaction: options.renderTransaction,
          });
        if (!rendered) return;
      }
      if (!isCurrent()) return;
      await settleFileExplorerDirectoryFailures(failedDirectories, (directory, failedGeneration) => (
        fileExplorerSyncUserExpansionState.get(directory) === previousUserExpansion.get(directory)
        && isCurrent()
        && fileExplorerListingFailureIsCurrent(directory, failedGeneration)
      ));
      if (!changed) return;
      restoreFileExplorerSyncCursorState(plan.session, plan.root, {progressive: true, scrollOwner, selectionOwner});
      scheduleFileTreeMetadataRefresh({highlightsOnly: true});
    }).catch(error => console.warn('Finder sync background revalidation failed', error));
  });
}

async function syncFileExplorerRootToPlan(plan, preferredItem = null, options = {}) {
  rememberFileExplorerSyncExpandedState();
  const signature = fileExplorerSyncPlanSignature(plan);
  if (!plan.root || fileExplorerSyncState.inFlightSignature === signature) return false;
  if (!fileExplorerSyncPlanTargetStillCurrent(plan, options)) return false;
  if (options.force !== true && fileExplorerSyncPlanAlreadyApplied(plan)) {
    cancelSupersededFileExplorerSyncPlan(plan, {invalidateOpen: false});
    setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    scheduleFileTreeMetadataRefresh({highlightsOnly: true});
    return false;
  }
  const renderTransaction = {epoch: ++fileTreeRenderEpoch, completed: true,
    fastPaintGeneration: fileExplorerFinderSessionFilesState.fastPaintGeneration};
  const generation = beginFileExplorerSync(signature, renderTransaction);
  const isCurrent = () => fileExplorerSyncState.generation === generation
    && fileExplorerSyncTransactionStillCurrent(plan, signature, options);
  const scrollOwner = createInputOwnedDeferredElementScrollOwner(...fileExplorerTreeContainers());
  let selectionOwner = null;
  let publicationPromise = null;
  let publicationStart = null;
  let publicationError = null;
  let publicationOwner = null;
  let publicationQueue = null;
  let publicationCompleted = true;
  let retainPublication = false;
  const publicationIsCurrent = () => fileExplorerSyncState.generation === generation
    && fileExplorerSyncPlanTargetStillCurrent(plan, options)
    && (fileExplorerSyncState.inFlightSignature === signature
      || fileExplorerSyncTargetKey(plan.session, plan.root) === fileExplorerSyncTargetKey(fileExplorerVisibleSyncSession, fileExplorerVisibleSyncRoot));
  const runPlan = async () => {
    let changed = false;
    const previousTargetKey = fileExplorerVisibleSyncTargetKey;
    const nextTargetKey = fileExplorerSyncTargetKey(plan.session, plan.root);
    const targetChanged = Boolean(nextTargetKey && nextTargetKey !== previousTargetKey);
    if (targetChanged) {
      rememberFileExplorerSyncExpandedState();
    }
    const rememberedExpandedPaths = rememberedFileExplorerSyncExpandedPaths(plan.session, plan.root);
    const renderPaths = fileExplorerSyncRenderPaths(plan, rememberedExpandedPaths);
    const qualificationContext = {};
    const highlightContext = {};
    let renderPayload = null;
    let payloadContext = null;
    let rowContext = null;
    const refreshContext = () => {
      const payload = fileExplorerFinderSessionFilesState.payload;
      // The session-files producer replaces payloads, including stream chunks. Keep this reuse local to this transaction.
      if (!payloadContext || renderPayload !== payload) {
        renderPayload = payload;
        payloadContext = fileTreeSessionFilesRenderContext(payload);
        rowContext = {...payloadContext, sessionHighlightSets: fileExplorerSessionHighlightSets(preferredItem, highlightContext)};
      }
      return rowContext;
    };
    const normalizedRoot = normalizeDirectoryPath(plan.root);
    const fetchedListings = new Map();
    const renderListings = (entriesByDir, renderOptions) => {
      // A push can advance a parent while this transaction still awaits a deeper listing.
      if (entriesByDir === fetchedListings) entriesByDir = cachedFileExplorerSyncListings(entriesByDir.keys()) || entriesByDir;
      if (entriesByDir !== fetchedListings) {
        for (const [directory, entries] of entriesByDir) fetchedListings.set(directory, entries);
      }
      const cachedAtAdmission = new Map([...fetchedListings.keys()].map(directory => [directory, cachedFileExplorerFsResourceValue('list', directory)]));
      const previous = publicationQueue;
      publicationQueue = (async () => {
        try {
          if (previous) await previous;
          if (!publicationIsCurrent()) { publicationCompleted = false; return false; }
          for (const directory of fetchedListings.keys()) {
            const cached = cachedFileExplorerFsResourceValue('list', directory);
            if (Array.isArray(cached) && cached !== cachedAtAdmission.get(directory)) fetchedListings.set(directory, cached);
          }
          const context = refreshContext();
          const completed = await renderCachedFileExplorerSyncPlan(plan, renderPaths, new Map(fetchedListings), {
            ...renderOptions, payloadContext: context, qualificationContext,
            sessionHighlightSets: context.sessionHighlightSets,
            yieldToInput: true, isCurrent: publicationIsCurrent, refreshContext, renderTransaction,
            onPrepared: () => { if (!selectionOwner) selectionOwner = createFileExplorerSyncSelectionOwner(); },
          });
          publicationCompleted = publicationCompleted && completed === true;
          return completed;
        } catch (error) {
          publicationCompleted = false;
          if (publicationIsCurrent()) resetFileExplorerAppliedSyncPlan();
          throw error;
        }
      })();
      return publicationQueue;
    };
    // Watch updates and admitted listings publish through the same operation owner.
    publicationOwner = {root: normalizedRoot, isCurrent: () => publicationCompleted && publicationIsCurrent(), publish(entriesByDir) {
      const listingDirectories = new Set(entriesByDir.keys());
      const reconcileDirectories = new Set(listingDirectories);
      for (const directory of listingDirectories) {
        for (const ancestor of ancestorPathsUnderRoot(normalizedRoot, directory)) reconcileDirectories.add(ancestor);
      }
      return renderListings(entriesByDir, {preserveState: true, listingDirectories, reconcileDirectories});
    }};
    fileExplorerSyncState.publication = publicationOwner;
    const listingDirectories = fileExplorerSyncListingDirectories(plan, renderPaths);
    const cachedListings = cachedFileExplorerSyncListings(listingDirectories);
    if (cachedListings) {
      changed = await renderListings(cachedListings, {preserveState: !targetChanged}) || changed;
      if (!isCurrent()) return changed;
      restoreFileExplorerSyncCursorState(plan.session, plan.root, {scrollOwner, selectionOwner});
      setFileExplorerVisibleSyncTarget(plan.session, plan.root);
      rememberFileExplorerSyncExpandedState(plan.session, plan.root);
      scheduleFileTreeMetadataRefresh({highlightsOnly: true});
      scheduleFileExplorerSyncRevalidation(plan, renderPaths, signature, {selectionOwner, refreshContext, renderTransaction});
      return changed;
    }
    const rootEntries = await fetchDirectory(normalizedRoot, {force: true, user: options.force === true});
    if (!isCurrent()) return false;
    if (!Array.isArray(rootEntries)) return false;
    fetchedListings.set(normalizedRoot, rootEntries);
    const firstPaint = normalizedRoot !== currentFileExplorerRoot()
      || ![fileExplorerTree, ...Array.from(document.querySelectorAll('.file-explorer-panel .file-explorer-tree-panel'))]
        .filter(Boolean)
        .some(tree => tree.querySelector?.('.file-tree-row[data-path]'));
    changed = await renderListings(fetchedListings, {preserveState: !targetChanged}) || changed;
    if (firstPaint) {
      const fastPaintGeneration = renderTransaction.fastPaintGeneration;
      scheduleAfterFileExplorerFirstPaint(() => {
        if (!isCurrent()) return;
        if (typeof finishFinderFastPaint === 'function') finishFinderFastPaint(fastPaintGeneration);
      });
    }
    if (!isCurrent()) return changed;
    restoreFileExplorerSyncCursorState(plan.session, plan.root, {scrollOwner, selectionOwner});
    setFileExplorerVisibleSyncTarget(plan.session, plan.root);
    rememberFileExplorerSyncExpandedState(plan.session, plan.root);
    scheduleFileTreeMetadataRefresh({highlightsOnly: true});

    const pendingDirectories = new Set();
    const failedDirectories = new Map();
    const publishListings = async () => {
      if (!isCurrent()) return;
      const reconcileDirectories = new Set(pendingDirectories);
      const listingDirectories = new Set(pendingDirectories);
      for (const directory of pendingDirectories) {
        for (const ancestor of ancestorPathsUnderRoot(normalizedRoot, directory)) reconcileDirectories.add(ancestor);
      }
      pendingDirectories.clear();
      changed = await renderListings(fetchedListings, {
        preserveState: true,
        listingDirectories,
        // Settlement publishes the last listings; payload producers own global metadata refreshes.
        reconcileDirectories,
      }) || changed;
      if (!isCurrent()) return;
      restoreFileExplorerSyncCursorState(plan.session, plan.root, {progressive: true, scrollOwner, selectionOwner});
    };
    const startPublication = () => {
      if (publicationPromise || !isCurrent()) return;
      const ready = new Promise(resolve => {
        publicationStart = () => { publicationStart = null; resolve(); };
        requestAnimationFrame(publicationStart);
      });
      publicationPromise = (async () => {
        await ready;
        while (pendingDirectories.size && isCurrent()) await publishListings();
      })();
      publicationPromise.then(() => { publicationPromise = null; }, error => {
        publicationError = error;
        publicationPromise = null;
      });
    };
    await fetchFileExplorerSyncListings(
      listingDirectories,
      {force: true, user: options.force === true, isCurrent,
        onIncomplete: () => { if (isCurrent()) publicationCompleted = false; }},
      (directory, entries) => {
        if (!isCurrent()) return;
        fetchedListings.set(normalizeDirectoryPath(directory), entries);
        pendingDirectories.add(normalizeDirectoryPath(directory));
        startPublication();
      },
      fetchedListings,
      (directory, failedGeneration) => {
        const path = normalizeDirectoryPath(directory);
        // Sibling listings may keep Sync open long enough for a manual retry to replace this failure.
        failedDirectories.set(path, {
          owner: fileExplorerPendingExpansionOwners.get(path),
          generation: failedGeneration,
        });
      },
    );
    publicationStart?.();
    if (publicationPromise) await publicationPromise;
    if (publicationError) throw publicationError;
    if (pendingDirectories.size && isCurrent()) await publishListings();
    await settleFileExplorerDirectoryFailures(failedDirectories, (directory, failure) => (
      isCurrent()
      && fileExplorerPendingExpansionOwners.get(directory) === failure.owner
      && fileExplorerListingFailureIsCurrent(directory, failure.generation)
      && failure.owner?.manual !== true
      && (!failure.owner || failure.owner.generation === generation)
    ));
    return changed;
  };
  try {
    const changed = await runPlan();
    let pending;
    do { pending = publicationQueue; await pending; } while (pending !== publicationQueue);
    const completed = changed && publicationCompleted && renderTransaction.completed && isCurrent();
    if (completed) markFileExplorerSyncPlanApplied(plan);
    else if (isCurrent()) resetFileExplorerAppliedSyncPlan();
    retainPublication = completed;
    return completed;
  } finally {
    if (!retainPublication && fileExplorerSyncState.publication === publicationOwner) fileExplorerSyncState.publication = null;
    finishFileExplorerSync(signature, generation);
  }
}

async function syncFileExplorerToActiveTab(preferredItem = null, options = {}) {
  if (!fileExplorerIsOpen()) return false;
  if (fileExplorerRootMode !== 'sync' && options.explicit !== true) return false;
  const path = options.explicit === true ? explicitFinderTargetPath(preferredItem) : activeFinderTargetPath(preferredItem);
  if (!path || fileExplorerSyncState.inFlightSignature === path) return false;
  const root = currentFileExplorerRoot();
  if (!pathIsInsideDirectory(path, root) || path === root) return false;
  const generation = beginFileExplorerSync(path);
  try {
    return await expandFileExplorerTreesToPath(path, root, generation);
  } finally {
    finishFileExplorerSync(path, generation);
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

function indexFileExplorerRowsByPath(containers = fileExplorerTreeContainers()) {
  const rowsByPath = new Map();
  for (const container of containers) {
    container.querySelectorAll?.('.file-tree-row[data-path]').forEach(row => {
      const path = String(row.dataset?.path || '');
      if (!path) return;
      const rows = rowsByPath.get(path) || [];
      rows.push(row);
      rowsByPath.set(path, rows);
    });
  }
  return rowsByPath;
}

// The first Finder listing is intentionally only one level deep. Once those rows are mounted,
// expose the visible repository paths to the later session-files stream so the Go backend can
// enrich each repository independently, including child repositories under a non-Git parent.
function fileExplorerVisibleRepositoryPaths() {
  // The scoped repository candidates are consumed by the Go per-repository stream. Python keeps
  // its established aggregate session-files request and must not acquire a Finder-only request
  // identity that would prevent valid Finder/Differ peer sharing.
  if (typeof sessionFilesStreamSupported === 'function' && !sessionFilesStreamSupported('finder')) return [];
  const paths = [];
  const seen = new Set();
  const add = rawPath => {
    const path = normalizeDirectoryPath(rawPath || '');
    if (!path || seen.has(path)) return;
    seen.add(path);
    paths.push(path);
  };
  // The opened Finder root is itself a repository candidate. It is not rendered as a child row,
  // so looking only at mounted repository rows would omit a root repo when the active tmux CWD is
  // elsewhere. This runs only while constructing the later session-files request; the first
  // /api/fs/fast/list request remains a direct one-level filesystem listing.
  add(currentFileExplorerRoot());
  for (const container of fileExplorerTreeContainers()) {
    for (const row of container.querySelectorAll?.('.file-tree-row[data-path]') || []) {
      if (row.dataset.tabberType || row.dataset.kind !== 'dir' || row.dataset.isRepo !== 'true') continue;
      add(row.dataset.path);
    }
  }
  return paths.slice(0, 128);
}

function renderFileExplorerTreeStatus(message, options = {}) {
  const root = normalizeDirectoryPath(options.root || '');
  const status = options.status || (options.error === true ? 'error' : 'message');
  for (const container of fileExplorerTreeContainers()) {
    setStylePropertyIfChanged(container.style, '--tree-depth', '0');
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

function captureFileExplorerScrollPositions({deferred = true, containers = fileExplorerTreeContainers()} = {}) {
  return containers.map(element => {
    const deferredOwner = deferred ? createInputOwnedDeferredElementScrollOwner(element) : null;
    const position = deferredOwner?.expected.get(element) || ownedElementScrollPosition(element);
    return {element, position, deferredOwner};
  });
}

function restoreFileExplorerScrollPositions(positions, {containers = null} = {}) {
  const restore = records => {
    if (!records.length) return;
    const currentContainers = new Set(containers && records.every(record => !record.deferredOwner)
      ? containers.filter(element => element.isConnected !== false)
      : fileExplorerTreeContainers());
    for (const {element, position, deferredOwner} of records) {
      if (!currentContainers.has(element)) continue;
      if (deferredOwner) {
        writeDeferredElementScrollIfOwned(deferredOwner, element, position, 'finder-tree-render-restore');
      } else {
        writeOwnedElementScroll(element, position, 'finder-tree-render-restore');
      }
    }
  };
  // Progressive listings finish their DOM transaction synchronously. Async expansion
  // instead retains the original element and yields to any newer scroll ownership.
  restore(positions.filter(record => !record.deferredOwner));
  const deferred = positions.filter(record => record.deferredOwner);
  if (deferred.length) requestAnimationFrame(() => restore(deferred));
}

function markFileExplorerInteraction() {
  fileExplorerLastInteractionAt = Date.now();
}

function eventTargetIsFileExplorerSurface(target) {
  return Boolean(
    target?.closest?.('#fileExplorer')
    || target?.closest?.('.panel.file-explorer-panel')
    || target?.closest?.('.file-explorer-tree-panel')
    || target?.closest?.('.file-explorer-changes-panel')
  );
}

// Finder, Differ, and Tabber share one layout item and one tree-key dispatcher. Normally the
// browser focus is already inside that item. With Auto-focus, the hovered pane owns keyboard
// navigation even though xterm may still be the document's active element; text and popup
// controls always retain their own keys.
function fileExplorerHasAutoFocusedKeyboardOwnership() {
  return autoFocusCanFollowCursor() && isFileExplorerItem(focusedPanelItem);
}

function fileExplorerKeyboardOwnsEvent(event) {
  if (eventTargetIsFileExplorerSurface(event?.target)) return true;
  return fileExplorerHasAutoFocusedKeyboardOwnership();
}

function fileExplorerKeyboardEventAllowsAction(event) {
  if (!fileExplorerKeyboardOwnsEvent(event)) return false;
  if (globalShortcutTargetAllowsAppAction(event?.target)) return true;
  // Auto-focus may intentionally leave xterm as the DOM focus target. That is the sole blocked
  // surface it may override; editors, inputs, menus, popovers, and dialogs retain their keys.
  return fileExplorerHasAutoFocusedKeyboardOwnership() && globalShortcutTargetIsTerminalSurface(event?.target);
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
    refreshFileExplorerIfChanged({trigger: 'deferred-interaction'}).catch(error => console.warn('deferred file explorer refresh failed', error));
  }, fileExplorerRefreshIdleMs);
}

function claimFileExplorerScrollInputOwnership(event) {
  if (event?.type === 'pointermove' && !event.buttons) return;
  const element = event?.target?.closest?.('#fileExplorerTree, .file-explorer-tree-panel');
  claimElementScrollInputEvent(element, event);
}

document.addEventListener('pointerdown', event => {
  if (eventTargetIsFileExplorerSurface(event.target)) markFileExplorerInteraction();
  claimFileExplorerScrollInputOwnership(event);
}, true);
for (const type of ['wheel', 'pointermove', 'pointerup', 'pointercancel', 'touchstart', 'touchmove', 'touchend', 'touchcancel', 'keydown']) {
  document.addEventListener(type, claimFileExplorerScrollInputOwnership, {capture: true, passive: true});
}
for (const type of ['blur', 'pagehide']) {
  window.addEventListener(type, () => releaseActiveElementScrollInputs(document));
}
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

async function ensureFileTreeRootRendered(container, root, options = {}) {
  if (!container) return false;
  const firstRow = container.querySelector?.('.file-tree-row[data-path]');
  if (firstRow) {
    if (pathIsInsideDirectory(firstRow.dataset.path, root) && childPathParts(root, firstRow.dataset.path).length === 1) return true;
  }
  const entries = await fetchDirectory(root, {user: options.user === true});
  if (!entries) return false;
  if (options.isCurrent && !options.isCurrent()) return false;
  container.replaceChildren();
  const rendered = await renderTreeChildren(container, root, entries, 0, {
    view: 'finder',
    yieldToInput: options.yieldToInput !== false,
    isCurrent: options.isCurrent,
  });
  return rendered !== false && (!options.isCurrent || options.isCurrent());
}

function childPath(parent, name) {
  return parent === '/' ? `/${name}` : `${parent}/${name}`;
}

async function ensureDirectoryRowExpanded(row, fullPath, options = {}) {
  if (!row || row.dataset?.kind !== 'dir') return null;
  const existing = childContainerForRow(row, fullPath);
  // Painted children may still belong to an older pending expansion. Join or replace it
  // through the shared request owner before reusing those rows.
  if (existing && fileTreeDirectRows(existing).length && !fileExplorerPendingExpansionOwners.has(fullPath)) {
    const wasExpanded = fileExplorerExpanded.has(fullPath);
    fileExplorerExpanded.add(fullPath);
    if (!wasExpanded && options.publicationState) options.publicationState.changed = true;
    syncDirectoryRowExpansionVisual(row, true, false);
    if (options.rowsByPath instanceof Map) indexFileTreeRowsByPath(existing, options.rowsByPath);
    return existing;
  }
  await expandDirectoryRow(row, fullPath, options);
  return childContainerForRow(row, fullPath);
}

function nextAnimationFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function scheduleAfterFileExplorerFirstPaint(callback, {waitForPaint = true} = {}) {
  // Background priority lets pending input win before another tree slice invalidates layout.
  const scheduleTask = () => {
    const resume = function resumeFileExplorerAfterPaint() { callback(); };
    if (globalThis.scheduler?.postTask) globalThis.scheduler.postTask(resume, {priority: 'background'});
    else setTimeout(resume, 0);
  };
  if (!waitForPaint) { scheduleTask(); return; }
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : handler => setTimeout(handler, 0);
  schedule(scheduleTask);
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

async function expandFileTreeContainerToPath(container, root, path, generation = fileExplorerSyncState.generation, options = {}) {
  const parts = childPathParts(root, path);
  if (!parts.length) return false;
  const rendered = await ensureFileTreeRootRendered(container, root, options);
  if (!rendered) return false;
  if (generation !== fileExplorerSyncState.generation) return false;
  let scope = container;
  let parent = root;
  let row = null;
  for (const part of parts) {
    const fullPath = childPath(parent, part);
    row = directFileTreeRow(scope, fullPath);
    if (!row) return false;
    if (row.dataset?.kind === 'dir') {
      // Automatic reveals honor sync-mode manual collapse; explicit reveals still expand through.
      if (options.auto === true && fullPath !== path && fileExplorerRootMode === 'sync' && fileExplorerSyncPathSuppressed(fullPath)) {
        return false;
      }
      const childScope = await ensureDirectoryRowExpanded(row, fullPath, {
        auto: options.auto === true,
        generation,
        user: options.user === true,
      });
      if (generation !== fileExplorerSyncState.generation) return false;
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
  if (generation !== fileExplorerSyncState.generation) return false;
  if (options.scrollIntoView !== false) scrollFileTreeRowIntoView(container, row);
  updateFileExplorerCurrentFileHighlight();
  return Boolean(row);
}

async function expandFileExplorerTreesToPath(path, root = currentFileExplorerRoot(), generation = fileExplorerSyncState.generation, options = {}) {
  let expanded = false;
  if (!fileExplorerRoot) fileExplorerRoot = root;
  setFileExplorerPathDisplay(root);
  if (generation !== fileExplorerSyncState.generation) return false;
  const results = await Promise.all(fileExplorerTreeContainers().map(container => (
    expandFileTreeContainerToPath(container, root, path, generation, options)
  )));
  return results.some(Boolean);
}

async function restoreFileExplorerExpandedPaths(paths, root = currentFileExplorerRoot(), options = {}) {
  const effectivePaths = fileExplorerEffectiveExpandedPaths(root, paths);
  const publication = activeFileExplorerSyncPublication(root);
  if (publication) {
    const isCurrent = () => publication.isCurrent() && (!options.isCurrent || options.isCurrent())
      && (options.generation === undefined || options.generation === fileExplorerSyncState.generation);
    if (!isCurrent()) return false;
    const directories = new Set([normalizeDirectoryPath(root)]);
    for (const path of effectivePaths) {
      if (!pathIsInsideDirectory(path, root) || fileExplorerSyncPathSuppressed(path)) continue;
      directories.add(path);
      for (const ancestor of ancestorPathsUnderRoot(root, path)) directories.add(ancestor);
    }
    let incomplete = false;
    const listings = await fetchFileExplorerSyncListings([...directories], {isCurrent, onIncomplete: () => {
      if (!isCurrent()) return;
      incomplete = true;
      resetFileExplorerAppliedSyncPlan();
    }});
    if (!isCurrent() || incomplete) return false;
    for (const path of paths) {
      if (path !== root && pathIsInsideDirectory(path, root) && !fileExplorerSyncPathSuppressed(path)) fileExplorerExpanded.add(path);
    }
    return publication.publish(listings);
  }
  const expandedPaths = fileExplorerPathsSortedByDepth(root, Array.from(new Set(paths))
    .filter(path => pathIsInsideDirectory(path, root) && path !== root)
    .filter(path => fileExplorerRootMode !== 'sync' || !fileExplorerSyncPathSuppressed(path)), false);
  const generation = options.generation ?? ++fileExplorerSyncState.generation;
  const isCurrent = () => generation === fileExplorerSyncState.generation
    && (!options.isCurrent || options.isCurrent());
  const containers = options.containers || fileExplorerTreeContainers();
  await Promise.all(containers.map(container => ensureFileTreeRootRendered(container, root, {
    yieldToInput: true,
    isCurrent,
  })));
  if (!isCurrent()) return false;
  // Collapsing a parent retains its descendants' intent. Keep that latent state through reload,
  // while the queue below only restores branches whose complete ancestor chain is open.
  for (const path of expandedPaths) fileExplorerExpanded.add(path);
  const byParent = new Map();
  for (const path of expandedPaths) {
    const parent = normalizeDirectoryPath(dirnameOf(path));
    const children = byParent.get(parent) || [];
    children.push(path);
    byParent.set(parent, children);
  }
  const rowsByPath = indexFileExplorerRowsByPath(containers);
  const publicationState = {changed: false};
  const queue = [...(byParent.get(normalizeDirectoryPath(root)) || [])];
  await traverseFileExplorerDirectoryQueue(queue, async path => {
    if (!fileExplorerExpanded.has(path)) return;
    const rows = rowsByPath.get(path) || [];
    const directoryRows = rows.filter(candidate => candidate.isConnected !== false && candidate.dataset?.kind === 'dir');
    if (!directoryRows.length) return;
    // A saved expansion is visible UI state, so idle push suppression must not leave its row open without children.
    await Promise.all(directoryRows.map(row => ensureDirectoryRowExpanded(
      row, path, {generation, rowsByPath, publicationState, force: true,
        isCurrent: () => isCurrent() && fileExplorerExpanded.has(path)},
    )));
    if (!isCurrent()) return;
    if (!fileExplorerExpanded.has(path)) return;
    for (const row of directoryRows) indexFileTreeRowsByPath(childContainerForRow(row, path), rowsByPath);
    queue.push(...(byParent.get(path) || []));
  }, isCurrent);
  if (!isCurrent()) return false;
  if (publicationState.changed) {
    rememberFileExplorerSyncExpandedState();
    refreshLayoutUrlStateSoon();
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  }
  return true;
}

function fileTreeDirectRows(container) {
  return Array.from(container?.children || []).filter(node => node.classList?.contains('file-tree-row'));
}

function indexFileTreeRowsByPath(container, rowsByPath) {
  for (const row of fileTreeDirectRows(container)) {
    const path = String(row.dataset?.path || '');
    if (!path) continue;
    const rows = rowsByPath.get(path) || [];
    if (!rows.includes(row)) rows.push(row);
    rowsByPath.set(path, rows);
  }
}

function indexSessionFileIntoPathMap(byPath, item, path, {firstWins = true, preferNewest = false} = {}) {
  if (!path) return;
  const current = byPath.get(path);
  if (!current || (!firstWins && !preferNewest) || (preferNewest && sessionFileRecordIsPreferred(item, current))) byPath.set(path, item);
}

function indexSessionFilesByPath(files, pathForItem = item => item?.abs_path, options = {}) {
  const byPath = new Map();
  if (!Array.isArray(files)) return byPath;
  for (const item of files) indexSessionFileIntoPathMap(byPath, item, pathForItem(item), options);
  return byPath;
}

function fileTreeSessionFilesRenderContext(payload = fileExplorerFinderSessionFilesState.payload) {
  return {
    sessionFilesMap: indexSessionFilesByPath(payload?.files || [], item => item?.abs_path, {preferNewest: true}),
    changedAncestorStats: fileTreeChangedAncestorStats(payload),
  };
}

function fileTreeChangedFile(path) {
  const files = Array.isArray(fileExplorerFinderSessionFilesState.payload?.files) ? fileExplorerFinderSessionFilesState.payload.files : [];
  return files.find(item => item?.abs_path === path) || null;
}

function sessionFileAgentKinds(item) {
  const raw = Array.isArray(item?.agents) ? item.agents : (item?.agent ? [item.agent] : []);
  const order = {claude: 0, codex: 1, opencode: 2};
  const seen = new Set();
  return raw
    .map(value => String(value || '').toLowerCase())
    .filter(value => value && !seen.has(value) && seen.add(value))
    .sort((a, b) => (order[a] ?? 3) - (order[b] ?? 3) || a.localeCompare(b));
}

function fileTreeChangedAncestorStats(payload = fileExplorerFinderSessionFilesState.payload) {
  const stats = new Map();
  const filesByPath = new Map();
  const ancestorsByDirectory = new Map();
  for (const file of Array.isArray(payload?.files) ? payload.files : []) {
    if (!sessionFileIsDifferVisible(file)) continue;
    const absPath = sessionFileAbsolutePath(file);
    if (!absPath) continue;
    let aggregate = filesByPath.get(absPath);
    if (!aggregate) {
      aggregate = {file, agents: [], mtime: 0};
      filesByPath.set(absPath, aggregate);
    } else if (sessionFileRecordIsPreferred(file, aggregate.file)) {
      aggregate.file = file;
    }
    const agents = sessionFileAgentKinds(file);
    for (const agent of agents) {
      if (!aggregate.agents.includes(agent)) aggregate.agents.push(agent);
    }
    const mtime = Number(file.mtime || 0);
    if (Number.isFinite(mtime) && mtime > Number(aggregate.mtime || 0)) aggregate.mtime = mtime;
  }
  for (const [absPath, aggregate] of filesByPath) {
    const file = aggregate.file;
    aggregate.agents.sort((left, right) => left.localeCompare(right));
    const containingDirectory = dirnameOf(absPath);
    let ancestors = ancestorsByDirectory.get(containingDirectory);
    if (!ancestors) {
      ancestors = [];
      let dir = normalizeDirectoryPath(containingDirectory);
      while (dir && dir !== '/') {
        ancestors.push(dir);
        const parent = normalizeDirectoryPath(dirnameOf(dir));
        if (!parent || parent === dir) break;
        dir = parent;
      }
      ancestorsByDirectory.set(containingDirectory, ancestors);
    }
    for (const dir of ancestors) {
      const current = stats.get(dir) || {count: 0, agents: [], mtime: 0, added: 0, removed: 0};
      current.count += 1;
      if (sessionFileCountsAreTracked(file)) {
        const added = Number(file.added);
        const removed = Number(file.removed);
        if (Number.isFinite(added)) current.added += added;
        if (Number.isFinite(removed)) current.removed += removed;
      }
      current.mtime = Math.max(Number(current.mtime || 0), aggregate.mtime);
      for (const agent of aggregate.agents) {
        if (!current.agents.includes(agent)) current.agents.push(agent);
      }
      stats.set(dir, current);
    }
  }
  return stats;
}

function sessionFileRecordIsPreferred(candidate, current) {
  const candidateMtime = Number(candidate?.mtime || 0);
  const currentMtime = Number(current?.mtime || 0);
  if (candidateMtime !== currentMtime) return candidateMtime > currentMtime;
  const candidateSession = String(candidate?.session || '');
  const currentSession = String(current?.session || '');
  return candidateSession > currentSession;
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
  if (!repo?.root || normalizeDirectoryPath(repo.cache_path || repo.root) !== normalized) return '';
  return repoBranchDisplayText(repo);
}

// Compact ahead/behind/dirty markers for a repo dir's inline annotation, read from cached repo info.
function fileTreeRepoSyncMeta(path) {
  hydrateFileExplorerRepoInfoCache();
  const normalized = normalizeDirectoryPath(path);
  const repo = fileExplorerRepoInfoCache.get(normalized);
  if (!repo?.root || normalizeDirectoryPath(repo.cache_path || repo.root) !== normalized) return [];
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
  const repos = Array.isArray(fileExplorerFinderSessionFilesState.payload?.repos) ? fileExplorerFinderSessionFilesState.payload.repos : [];
  const repo = repos.find(item => normalizeDirectoryPath(item?.repo || '') === normalized);
  let added = Number(repo?.added);
  let removed = Number(repo?.removed);
  if (!Number.isFinite(added) || !Number.isFinite(removed)) {
    const files = Array.isArray(fileExplorerFinderSessionFilesState.payload?.files) ? fileExplorerFinderSessionFilesState.payload.files : [];
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

function fileTreeMtimeText(entry, options = {}) {
  return sessionFileDisplayTimeTextForEntry(entry, options);
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
  const value = Number(globalThis.__yomuxFileTreeRecencyNowMs);
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
    date.classList.toggle('attention-pulse', false);
    date.classList.toggle('heartbeat-pulse', false);
    removeStylePropertyIfPresent(date.style, '--attention-animation-delay');
    return;
  }
  const alreadyPulsing = date.classList.contains('attention-pulse');
  date.classList.toggle('attention-pulse', true);
  date.classList.toggle('heartbeat-pulse', true);
  if (!alreadyPulsing) setStylePropertyIfChanged(date.style, '--attention-animation-delay', attentionAnimationDelay(nowMs));
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
  for (const className of FILE_TREE_RECENCY_CLASSES) row.classList.toggle(className, false);
  const date = fileTreeRecencyDateCell(row);
  date?.classList?.toggle?.('attention-pulse', false);
  date?.classList?.toggle?.('heartbeat-pulse', false);
  removeStylePropertyIfPresent(date?.style, '--attention-animation-delay');
  setRowDataset(row, 'recency', '');
  removeStylePropertyIfPresent(row.style, '--file-tree-recency-date-color');
}

function applyFileTreeRowRecency(row, entry, options = {}) {
  if (!row || fileExplorerTreeDateModeForView(options.view || (options.differMode ? 'differ' : 'finder')) === 'none') {
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
  setStylePropertyIfChanged(row.style, '--file-tree-recency-date-color', state.colorVar);
  if (!state.pulseEligible) {
    setFileTreeRecencyAttentionClass(row, false);
    clearFileTreeRecencyAttentionTimer(row);
    row.__fileTreeRecencyAttentionUntilMs = 0;
    return;
  }
  row.__fileTreeRecencyAttentionMtimeKey = state.mtimeKey;
  row.__fileTreeRecencyAttentionUntilMs = (Number(state.mtimeKey) * 1000) + (FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS * 1000);
  const attentionUntilMs = Number(row.__fileTreeRecencyAttentionUntilMs) || 0;
  const rowSuppressesAttention = row.classList?.contains(CLS.selected) || row.classList?.contains('current-file');
  const attentionActive = !rowSuppressesAttention && attentionUntilMs > nowMs && row.__fileTreeRecencyAttentionMtimeKey === state.mtimeKey;
  setFileTreeRecencyAttentionClass(row, attentionActive, nowMs);
  if (attentionActive) scheduleFileTreeRecencyAttentionStop(row, attentionUntilMs);
  else clearFileTreeRecencyAttentionTimer(row);
}

const FILE_TREE_NAME_COLLATOR = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});

function sortedFileTreeEntries(entries, sortMode = fileExplorerTreeSortModeForView('finder'), options = {}) {
  return drainFileTreeRenderSteps(sortedFileTreeEntrySteps(entries, sortMode, options));
}

function tabberUtilityEntryCompare(left, right) {
  const leftKey = left.sortName != null ? String(left.sortName) : String(left.name || '');
  const rightKey = right.sortName != null ? String(right.sortName) : String(right.name || '');
  return FILE_TREE_NAME_COLLATOR.compare(leftKey, rightKey);
}

function* sortedFileTreeEntrySteps(entries, sortMode, options = {}) {
  const includeHidden = options.includeHidden === true;
  let visible = [];
  for (const entry of entries) {
    if (includeHidden || fileExplorerShowHidden || !entry.name.startsWith('.')) visible.push(entry);
    yield;
  }
  const compare = fileTreeEntryComparator(sortMode, options);
  if (!options.yieldToInput) return visible.sort(compare);
  // Merge passes bound comparison work between the renderer's existing input yields.
  let merged = new Array(visible.length);
  for (let width = 1; width < visible.length; width *= 2) {
    for (let start = 0; start < visible.length; start += width * 2) {
      const middle = Math.min(start + width, visible.length);
      const end = Math.min(start + width * 2, visible.length);
      let left = start;
      let right = middle;
      for (let index = start; index < end; index += 1) {
        merged[index] = left < middle && (right >= end || compare(visible[left], visible[right]) <= 0)
          ? visible[left++] : visible[right++];
        yield;
      }
    }
    [visible, merged] = [merged, visible];
  }
  return visible;
}

function fileTreeEntryComparator(sortMode, options = {}) {
  const mode = ['az', 'za', 'newest', 'oldest'].includes(sortMode) ? sortMode : 'az';
  const direction = mode === 'za' ? -1 : 1;
  // Differ only. A deleted file is a real diff result and stays a visible child, but a run of them
  // interleaved with live edits is what a reader has to skip past to find the file they can still
  // open. Ranking deletion ahead of every other key groups deleted children at the bottom of each
  // directory. Directories are not ranked: they carry no `deleted` flag and keep their existing
  // dirs-before-files order. Finder listings carry no `deleted` either, so passing this option
  // there is a no-op rather than a second ordering rule.
  const deletedRank = entry => options.deletedLast === true && entry?.deleted === true ? 1 : 0;
  return (left, right) => {
    if (options.tabberNormalOrder === true) {
      const utilityRank = entry => entry?.tabber?.type === 'tab' ? 1 : 0;
      const rankResult = utilityRank(left) - utilityRank(right);
      if (rankResult !== 0) return rankResult;
      if (utilityRank(left) === 1) return tabberUtilityEntryCompare(left, right);
    }
    const deletedResult = deletedRank(left) - deletedRank(right);
    if (deletedResult !== 0) return deletedResult;
    const leftKind = left.kind === 'dir' ? 0 : 1;
    const rightKind = right.kind === 'dir' ? 0 : 1;
    if (leftKind !== rightKind) return leftKind - rightKind;
    if (mode === 'newest' || mode === 'oldest') {
      const mtimeResult = Number(right?.tabber?.normalSortMtime ?? right.mtime ?? 0) - Number(left?.tabber?.normalSortMtime ?? left.mtime ?? 0);
      if (mtimeResult !== 0) return mode === 'newest' ? mtimeResult : -mtimeResult;
    }
    // sortName lets a caller (the Tabber) sort A-Z/Z-A by the human label while the row's name stays a
    // stable synthetic path key; Finder/Differ entries have no sortName and fall back to name unchanged.
    const leftKey = left.sortName != null ? String(left.sortName) : String(left.name || '');
    const rightKey = right.sortName != null ? String(right.sortName) : String(right.name || '');
    return FILE_TREE_NAME_COLLATOR.compare(leftKey, rightKey) * direction;
  };
}

// One source for the git-status row classes; applyGitStatusRowClass clears stale classes on both paths.
const GIT_STATUS_ROW_CLASSES = Object.freeze(['git-modified', 'git-untracked', 'git-deleted', 'git-staged', 'git-transcript']);
// Session-highlight row classes are likewise toggled through one helper.
const SESSION_HIGHLIGHT_ROW_CLASSES = Object.freeze(['file-tree-row--sync-expanded', 'file-tree-row--session-repo', 'file-tree-row--session-touched']);

function updateFileTreeSyncTargetMarker(row, active, title = '') {
  if (!row) return;
  const enabled = active === true;
  let marker = row.querySelector(':scope > .file-tree-sync-target');
  if (!enabled) {
    marker?.remove();
    return;
  }
  if (!marker) {
    marker = document.createElement('span');
    marker.className = 'file-tree-sync-target';
  }
  const anchor = row.querySelector(':scope > .file-tree-agent') || row.querySelector(':scope > .file-tree-name');
  const before = anchor?.nextElementSibling || null;
  if (marker.parentElement !== row || marker.previousElementSibling !== anchor) {
    row.insertBefore(marker, before);
  }
  marker.hidden = false;
  if (marker.textContent !== '★') marker.textContent = '★';
  marker.setAttribute('aria-label', title);
  marker.setAttribute('title', title);
}

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
    M: t('state.modified'),
    A: t('git.status.added'),
    D: t('git.status.deleted'),
    T: t('git.status.transcriptTouched'),
    '?': t('git.status.untracked'),
    U: t('git.status.untracked'),
    S: t('git.status.staged'),
    R: t('git.status.renamed'),
    C: t('git.status.copied'),
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
  row.classList.toggle('file-tree-row', true);
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
  // Keep DOM order: icon → name → agent → optional sync star → diff → dir-count → status → date.
  // updateFileTreeSyncTargetMarker inserts a star only for starred sync rows, so ordinary rows
  // keep the established Finder/Differ column ownership with no hidden spacer.
  if (name.nextElementSibling !== agent) row.insertBefore(agent, name.nextElementSibling);
  const syncTarget = row.querySelector(':scope > .file-tree-sync-target');
  if (syncTarget && agent.nextElementSibling !== syncTarget) row.insertBefore(syncTarget, agent.nextElementSibling);
  const metadataAnchor = syncTarget || agent;
  if (metadataAnchor.nextElementSibling !== diff) row.insertBefore(diff, metadataAnchor.nextElementSibling);
  if (diff.nextElementSibling !== dirCount) row.insertBefore(dirCount, diff.nextElementSibling);
  if (dirCount.nextElementSibling !== status) row.insertBefore(status, dirCount.nextElementSibling);
  if (status.nextElementSibling !== date) row.insertBefore(date, status.nextElementSibling);
  setClassNameIfChanged(icon, ['file-tree-icon', options.iconClass || ''].filter(Boolean).join(' '));
  if (options.disclosureExpanded === true || options.disclosureExpanded === false) {
    setDisclosureTriangleElement(icon, options.disclosureExpanded === true);
  } else {
    delete icon.dataset.disclosureExpanded;
    if (icon.textContent !== iconText) icon.textContent = iconText;
  }
  if (options.preserveNameHtml === true) {
    // Keep an active session popover attached to its existing name/anchor subtree.
  } else if (options.nameHtml) {
    setInnerHtmlIfChanged(name, options.nameHtml, cleanupDetachedPopoversWithin);
    if (!name.children?.length && name.textContent !== nameText) name.textContent = nameText;
  } else if (name.textContent !== nameText || name.children.length > 0) {
    name.innerHTML = '';
    name.textContent = nameText;
  }
  const agentHtml = options.agentHtml || '';
  setInnerHtmlIfChanged(agent, agentHtml);
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
    setInnerHtmlIfChanged(diff, diffHtml);
    setHiddenIfChanged(diff, !diffParts.length);
  }
  const statusText = options.gitStatus || '';
  setClassNameIfChanged(status, ['file-tree-git-status', fileTreeGitStatusBadgeClass(statusText)].filter(Boolean).join(' '));
  if (status.textContent !== statusText) status.textContent = statusText;
  const statusTitle = options.gitStatusTitle || '';
  setAttributeIfChanged(status, 'title', statusTitle || null);
  setAttributeIfChanged(status, 'aria-label', statusTitle || null);
  setHiddenIfChanged(status, !statusText);
  if (options.preserveDate !== true) {
    const dateHtml = options.dateHtml || '';
    if (dateHtml) {
      setInnerHtmlIfChanged(date, dateHtml);
      setHiddenIfChanged(date, false);
    } else {
      const dateText = options.dateText || '';
      if (date.children.length > 0 || date.textContent !== dateText) {
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
  const deferSessionFilesDiff = options.deferSessionFilesDiff === true;
  const indexExcludedEntry = !differMode && fileExplorerPathIsExcluded(fullPath);
  const indexedDirectory = !differMode && entry.kind === 'dir' && fileExplorerDirectoryIsIndexed(fullPath);
  const changedAncestor = !deferSessionFilesDiff && !differMode && entry.kind === 'dir' && options.changedAncestorStats instanceof Map
    ? (options.changedAncestorStats.get(fullPath) || null)
    : null;
  const changedFile = !deferSessionFilesDiff && entry.kind === 'file'
    ? (options.sessionFilesMap ? (options.sessionFilesMap.get(fullPath) || null) : fileTreeChangedFile(fullPath))
    : null;
  const changedFileStatus = changedFile ? sessionFileDisplayStatus(changedFile) : '';
  const gitStatus = indexExcludedEntry
    ? t('finder.index.excluded')
    : entry.kind === 'file'
    ? (deferSessionFilesDiff ? '' : (options.sessionFilesMap ? (differMode ? changedFileStatus : normalizeGitStatus(changedFile?.status)) : fileTreeGitStatus(fullPath)))
    : (differMode ? '' : fileExplorerIndexBadgeText(fullPath));
  const displayName = differMode ? {text: entry.display_name || entry.name, html: null} : fileTreeDisplayParts(fullPath, entry);
  const directoryDiffParts = entry.kind === 'dir'
    ? (differMode
      ? sessionFileStatusCountParts(options.directoryStatusCounts?.get(fullPath) || {})
      : (deferSessionFilesDiff
        ? []
        : (entry.is_repo === true ? fileTreeRepoDiffParts(fullPath) : sessionFileDiffText(changedAncestor || {}))))
    : [];
  const dirCountText = entry.kind === 'dir'
    ? (differMode && directoryDiffParts.length
      ? ''
      : (differMode
        ? fileTreeDirCountText(countChangedFilesInDir(fullPath, options.entriesByDir, options.sessionFilesMap))
        : (deferSessionFilesDiff ? '' : fileTreeDirCountText(changedAncestor?.count))))
    : '';
  const icon = options.iconText != null
    ? String(options.iconText)
    : (entry.kind === 'dir' ? disclosureTriangleGlyph(options.expanded === true) : (entry.kind === 'file' ? fileIconFor(entry.name) : '·'));
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
      gitStatusTitle: indexExcludedEntry
        ? t('finder.index.excluded')
        : (entry.kind === 'dir' && !differMode ? fileExplorerIndexBadgeTitle(fullPath) : gitStatusBadgeTitle(gitStatus)),
      iconClass: [fileIconClassFor(entry.name, entry.kind), entry.kind === 'dir' ? 'ui-disclosure-triangle' : '', indexedDirectory ? 'file-icon-dir-indexed' : ''].filter(Boolean).join(' '),
      disclosureExpanded: entry.kind === 'dir' ? options.expanded === true : undefined,
      nameHtml: differMode ? null : displayName.html,
      dateText: options.dateText || '',
      diffParts: changedFile && !deferSessionFilesDiff ? sessionFileDiffText(changedFile) : directoryDiffParts,
      agentHtml: deferSessionFilesDiff ? '' : (changedFile ? changeFileAgentsHtml(changedFile) : (changedAncestor ? changeFileAgentsHtml(changedAncestor) : '')),
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
  const cachedRepo = entry.kind === 'dir' ? exactFileExplorerRepoInfo(fullPath) : null;
  if (cachedRepo && entry.is_repo !== true) entry = {...entry, is_repo: true, repo: entry.repo || cachedRepo};
  const differMode = options.differMode === true;
  const compact = options.compact === true;
  const currentDirectory = activeFinderDirectoryPath();
  // Row disclosure reuses the expansion value that drives child recursion.
  const expanded = entry.kind === 'dir' && (
    typeof options.dirExpanded === 'boolean'
      ? options.dirExpanded
      : fileTreeDirectoryExpanded(fullPath, options)
  );
  const loadedChildListing = options.entriesByDir instanceof Map
    && options.entriesByDir.has(normalizeDirectoryPath(fullPath));
  const pendingExpansion = !differMode && entry.kind === 'dir' && (
    fileExplorerPendingExpansions.has(fullPath)
    || (expanded && options.hasRenderedChildren !== true && !loadedChildListing && !fileExplorerDirectoryListingFailed(fullPath))
  );
  const indexedDirectory = !differMode && entry.kind === 'dir' && fileExplorerDirectoryIsIndexed(fullPath);
  const indexExcludedEntry = !differMode && fileExplorerPathIsExcluded(fullPath);
  const indexedDescendantDirectory = !differMode && entry.kind === 'dir' && !indexedDirectory && !indexExcludedEntry && Boolean(fileExplorerIndexedAncestor(fullPath));
  const derivedState = fileTreeRowDerivedState(fullPath, entry, {
    ...options,
    expanded,
    dateText: fileTreeMtimeText(entry, options),
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
    pendingExpansion,
    indexedDirectory,
    indexExcludedEntry,
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

function scheduleFileTreeRowActivation(row, fullPath, entry, event) {
  const token = {};
  row.__fileTreePendingActivation = token;
  setTimeout(() => {
    if (row.__fileTreePendingActivation !== token) return;
    row.__fileTreePendingActivation = null;
    onFileTreeRowClick(row, fullPath, entry, event);
  }, 0);
}

function bindFinderRowHandlers(row, state) {
  const {entry, fullPath, differMode} = state;
  if (!differMode && entry.kind === 'dir' && (entry.is_repo === true || Boolean(row.dataset.syncTargetTitle))) {
    row.removeAttribute('title');
    fileTreeRepoHoverController(row, fullPath);
  } else if (!differMode) {
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
    row.classList.toggle('new-entry', state.newEntry);
    if (state.newEntry) scheduleNewEntryClassRemoval(row, fullPath);
    row.classList.toggle('current-file', state.currentFile);
    row.classList.toggle('current-directory', state.currentDirectoryRow);
    setAttributeIfChanged(row, 'aria-current', state.currentFile || state.currentDirectoryRow ? 'true' : null);
  }
  if (state.imagePreviewEligible) bindFileImagePreview(row, fullPath, entry);
  if (differMode) {
    // In differMode, event handling is via delegation in bindChangesPanel; clear Finder handlers
    clearFileTreeRowHandlers(row);
    return;
  }
  row.onpointerdown = event => {
    if (event.button != null && event.button !== 0) return;
    row.__fileTreeLastPointerType = event.pointerType || '';
    cancelPendingFileExplorerActiveSync();
    row.__fileTreePointerDown = {x: event.clientX || 0, y: event.clientY || 0};
  };
  row.onpointerup = event => {
    if (event.button != null && event.button !== 0) return;
    if (typeof dockviewTabContentInteractionSuppressed === 'function' && dockviewTabContentInteractionSuppressed()) {
      row.__fileTreePointerDown = null;
      event.preventDefault();
      return;
    }
    const start = row.__fileTreePointerDown;
    row.__fileTreePointerDown = null;
    // A release from a tab/pane drag can land over a Finder row even though that row never
    // received pointer-down. Never turn that unmatched release (or its synthetic click) into a
    // file/directory activation.
    if (!start) {
      row.__fileTreeSuppressClick = true;
      event.preventDefault();
      return;
    }
    if (row.__fileTreeDragging) return;
    const dx = Math.abs((event.clientX || 0) - (start?.x || 0));
    const dy = Math.abs((event.clientY || 0) - (start?.y || 0));
    if (start && Math.max(dx, dy) > 4) return;
    event.preventDefault();
    event.stopPropagation();
    // Desktop double-click keeps its dedicated ondblclick route, but touch browsers report the
    // second intentional tap with detail=2. Dropping that pointerup makes a touch collapse look
    // dead because the first tap expanded the directory and the second tap is the collapse.
    if (event.pointerType !== 'touch' && event.detail > 1) return;
    if (event.pointerType === 'touch') armFileTreeTouchClickSuppression(row);
    row.__fileTreePointerActivated = true;
    setTimeout(() => { row.__fileTreePointerActivated = false; }, 0);
    scheduleFileTreeRowActivation(row, fullPath, entry, event);
  };
  row.onclick = event => {
    if (row.__fileTreeSuppressClick) {
      row.__fileTreeSuppressClick = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (typeof dockviewTabContentInteractionSuppressed === 'function' && dockviewTabContentInteractionSuppressed()) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (consumeFileTreeTouchClickSuppression(row, event)) {
      row.__fileTreePointerActivated = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (row.__fileTreePointerActivated) {
      row.__fileTreePointerActivated = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    event.stopPropagation();
    if (event.detail > 1) return;
    void onFileTreeRowClick(row, fullPath, entry, event);
  };
  row.ondblclick = event => {
    event.preventDefault();
    event.stopPropagation();
    // Chromium can synthesize a dblclick after two touch taps. Touch already uses each tap for
    // expand/collapse in the pointerup path; letting this desktop-only route run would navigate
    // into the directory and make the original row disappear instead of collapsing it.
    if (row.__fileTreeLastPointerType === 'touch' || event.sourceCapabilities?.firesTouchEvents === true) return;
    if (entry.kind === 'dir') openFileExplorerManualRoot(fullPath);
    else openFileInEditor(fullPath, entry);
  };
  row.oncontextmenu = event => {
    event.preventDefault();
    event.stopPropagation();
    // Observed on macOS: a trackpad/Control-click secondary click can report `button: 0` on the
    // pointerdown/pointerup pair even though `contextmenu` also fires correctly. That false
    // button-0 reading lets onpointerup's own activation call run BEFORE this handler ever sees
    // the gesture, so a same-tick suppression flag here is too late. onpointerup instead defers
    // its activation by one macrotask via scheduleFileTreeRowActivation; cancel that here, since
    // contextmenu always dispatches synchronously before that deferred tick can run.
    row.__fileTreeSuppressClick = true;
    setTimeout(() => { row.__fileTreeSuppressClick = false; }, 0);
    row.__fileTreePendingActivation = null;
    closeFileImagePreview();
    showFileTreeContextMenu(row, fullPath, entry, event.clientX, event.clientY, {surface: 'finder'});
  };
  row.ondragstart = event => {
    row.__fileTreeDragging = true;
    startFileTreeDrag(event, row, fullPath, entry);
  };
  row.ondragend = () => {
    row.__fileTreeDragging = false;
    cancelDragOperationState();
  };
}

function TreeRowViewModel(options = {}) {
  return Object.freeze({
    path: String(options.path || ''),
    kind: String(options.kind || ''),
    name: String(options.name || ''),
    paddingLeft: String(options.paddingLeft || ''),
    dataset: Object.freeze({...options.dataset}),
    classes: Object.freeze({...options.classes}),
    selected: options.selected === true,
    expandable: options.expandable === true,
    expanded: options.expanded === true,
    current: options.current === true,
    draggable: options.draggable === true,
    icon: String(options.icon || ''),
    label: String(options.label || ''),
    content: Object.freeze({...options.content}),
    gitClass: String(options.gitClass || ''),
    sessionHighlightClass: String(options.sessionHighlightClass || ''),
    recencyEntry: options.recencyEntry || null,
    recencyOptions: Object.freeze({...options.recencyOptions}),
  });
}

const TREE_ROW_MANAGED_CLASSES = Object.freeze([
  'compact', CLS.selected, 'expanded', CLS.collapsed, 'loading-children', 'is-repo',
  'indexed-directory', 'index-excluded-entry', 'indexed-descendant-directory', 'is-symlink',
  'symlink-broken', 'new-entry', 'current-file', 'current-directory', 'repo-non-main',
  'file-tree-row--changed-ancestor', 'tabber-row', 'tabber-normal-row', 'tabber-active-window',
  'tabber-active-normal',
]);

function patchTreeRow(row, model) {
  syncFileTreeRowKindClass(row, model.kind);
  for (const [key, value] of Object.entries(model.dataset)) setRowDataset(row, key, value);
  if (row.style.paddingLeft !== model.paddingLeft) row.style.paddingLeft = model.paddingLeft;
  setTreeItemAria(row, {selected: model.selected, expandable: model.expandable, expanded: model.expanded});
  if (row.draggable !== model.draggable) row.draggable = model.draggable;
  if (!model.draggable) row.removeAttribute('draggable');
  for (const className of TREE_ROW_MANAGED_CLASSES) row.classList.toggle(className, model.classes[className] === true);
  setAttributeIfChanged(row, 'aria-current', model.current ? 'true' : null);
  applyGitStatusRowClass(row, model.gitClass);
  applySessionHighlightRowClass(row, model.sessionHighlightClass);
  updateFileTreeRowContents(row, model.icon, model.label, model.content);
  if (model.recencyEntry) applyFileTreeRowRecency(row, model.recencyEntry, model.recencyOptions);
  return row;
}

function fileTreeRowViewModel(state) {
  const {entry, fullPath, derivedState} = state;
  const differData = state.differMode && entry.kind === 'dir'
    ? {changesFolderToggle: fullPath, openChangeDirectory: fullPath, changeRel: state.relDir}
    : {changesFolderToggle: '', openChangeDirectory: '', changeRel: derivedState.changedFile?.abs_path ? (derivedState.changedFile.path || '') : ''};
  return TreeRowViewModel({
    path: fullPath,
    kind: entry.kind,
    name: entry.name,
    paddingLeft: state.paddingLeft,
    selected: state.selected,
    expandable: entry.kind === 'dir',
    expanded: state.expanded,
    current: state.currentFile || state.currentDirectoryRow,
    draggable: entry.kind === 'file' || entry.kind === 'dir',
    icon: derivedState.icon,
    label: derivedState.displayName.text,
    content: derivedState.contentOptions,
    gitClass: derivedState.gitClass,
    sessionHighlightClass: state.sessionHighlightClass,
    recencyEntry: entry,
    recencyOptions: {...state.options, differMode: state.differMode},
    dataset: {
      path: fullPath,
      kind: entry.kind,
      name: entry.name,
      isRepo: entry.is_repo === true ? 'true' : 'false',
      repoInfoResolved: entry.kind === 'dir'
        ? (entry.repo_info_deferred === true || (state.options.deferSessionFilesDiff === true && entry.is_repo === true && !entry.repo) ? 'false' : 'true')
        : '',
      isSymlink: entry.is_symlink === true ? 'true' : 'false',
      symlinkTarget: entry.symlink_target || '',
      indexed: state.indexedDirectory ? 'true' : 'false',
      indexExcluded: state.indexExcludedEntry ? 'true' : 'false',
      tabberType: '', tabberSession: '', tabberWindow: '', tabberRepoRoot: '', tabberItem: '', tabberBranch: '',
      openChangeFile: derivedState.changedFile?.abs_path || '',
      openChangeSession: derivedState.changedFile?.abs_path ? (derivedState.changedFile.session || '') : '',
      openChangeStatus: derivedState.changedFile?.abs_path ? derivedState.changedFileStatus : '',
      openChangeRepo: derivedState.changedFile?.abs_path ? (derivedState.changedFile.repo || '') : '',
      changeSize: derivedState.changedFile?.abs_path && derivedState.changedFile.size !== null && derivedState.changedFile.size !== undefined ? derivedState.changedFile.size : '',
      ...differData,
    },
    classes: {
      compact: state.compact,
      [CLS.selected]: state.selected,
      expanded: state.expanded,
      [CLS.collapsed]: entry.kind === 'dir' && !state.expanded,
      'loading-children': state.pendingExpansion,
      'is-repo': entry.kind === 'dir' && entry.is_repo === true,
      'indexed-directory': state.indexedDirectory,
      'index-excluded-entry': state.indexExcludedEntry,
      'indexed-descendant-directory': state.indexedDescendantDirectory,
      'is-symlink': entry.is_symlink === true,
      'symlink-broken': entry.kind === 'symlink-broken',
      'new-entry': state.newEntry,
      'current-file': state.currentFile,
      'current-directory': state.currentDirectoryRow,
      'repo-non-main': derivedState.repoNonMain === true,
      'file-tree-row--changed-ancestor': Boolean(derivedState.changedAncestor?.count),
    },
  });
}

function updateFileTreeRow(row, parentPath, entry, depth, options = {}) {
  const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
  // Tabber precomputes window and utility labels; Finder-specific Git and index logic does not apply.
  if (options.mode === 'tabber') return updateTabberRow(row, fullPath, entry, depth, options);
  const rowState = buildFileTreeRowState(fullPath, entry, depth, options);
  patchTreeRow(row, fileTreeRowViewModel(rowState));
  applyFileExplorerSessionHighlightRow(row, options.sessionHighlightSets || fileExplorerSessionHighlightSets());
  if (typeof options.rowBinding === 'function') options.rowBinding(row, rowState);
  else bindFinderRowHandlers(row, rowState);
  return fullPath;
}

const fileTreeInputSliceMs = 1;
const fileTreeRenderOwners = new WeakMap();
let fileTreeRenderEpoch = 0;
let fileTreeRenderStepCount = 0;
const fileTreeRenderHistory = [];
function recordFileTreeRender(event, container, parentPath, transaction, options) {
  if (typeof fileTreeRenderHistory === 'undefined') return;
  fileTreeRenderHistory.push({event, producer: options.producer || new Error().stack,
    target: container?.id || container?.dataset?.parent || parentPath,
    parentPath, generation: fileExplorerSyncState.generation, epoch: transaction.epoch,
    connected: container?.isConnected, at: performance.now()});
  if (fileTreeRenderHistory.length > 160) fileTreeRenderHistory.splice(0, fileTreeRenderHistory.length - 160);
}

function drainFileTreeRenderSteps(steps) {
  let next = steps.next();
  if (typeof fileTreeRenderStepCount !== 'undefined') fileTreeRenderStepCount += 1;
  while (!next.done) { next = steps.next(); if (typeof fileTreeRenderStepCount !== 'undefined') fileTreeRenderStepCount += 1; }
  return next.value;
}

async function drainFileTreeRenderStepsWithInput(steps, options = {}) {
  let waitForPaint = true;
  const paintedContainers = new WeakSet();
  while (!options.isCurrent || options.isCurrent()) {
    const started = performance.now();
    let next;
    do {
      if (options.isCurrent && !options.isCurrent()) return false;
      next = steps.next();
      if (typeof fileTreeRenderStepCount !== 'undefined') fileTreeRenderStepCount += 1;
      if (next.done) return next.value;
      // Section chrome needs its own paint; repeated rows share their container's first-paint boundary.
      if (next.value === true) waitForPaint = true;
      else if (next.value && !paintedContainers.has(next.value)) {
        waitForPaint = true;
        paintedContainers.add(next.value);
      }
    } while (performance.now() - started < fileTreeInputSliceMs);
    options.afterSlice?.();
    // Paint the first visible rows, then let background task priority yield to input and rendering.
    // A frame per tiny work slice makes large listings take thousands of frames under load.
    await new Promise(resolve => scheduleAfterFileExplorerFirstPaint(resolve, {waitForPaint}));
    waitForPaint = false;
  }
  return false;
}

function renderTreeChildren(container, parentPath, entries, depth, options = {}) {
  const renderOptions = {...options, renderTransaction: options.renderTransaction || {epoch: ++fileTreeRenderEpoch, completed: true}};
  const steps = renderTreeChildrenSteps(container, parentPath, entries, depth, renderOptions);
  return options.yieldToInput ? drainFileTreeRenderStepsWithInput(steps, options) : drainFileTreeRenderSteps(steps);
}

function fileTreeNodeAfterYield(container, next) {
  // Watcher deletions can detach the saved sibling without replacing this renderer's owner.
  return next && next.parentElement !== container ? container.firstElementChild : next;
}

function* renderTreeChildrenSteps(container, parentPath, entries, depth, options = {}) {
  if (!container) return;
  const transaction = options.renderTransaction || {epoch: ++fileTreeRenderEpoch, completed: true};
  const epoch = transaction.epoch;
  const superseded = () => { recordFileTreeRender('superseded', container, parentPath, transaction, options); transaction.completed = false; return false; };
  if ((fileTreeRenderOwners.get(container)?.epoch || 0) > epoch) return superseded();
  recordFileTreeRender('start', container, parentPath, transaction, options);
  const owner = {epoch, renderOwner: options.renderOwner};
  fileTreeRenderOwners.set(container, owner);
  const parent = container.parentElement;
  const wasConnected = container.isConnected;
  const isCurrent = () => fileTreeRenderOwners.get(container) === owner
    && container.parentElement === parent && (!wasConnected || container.isConnected !== false)
    && (!options.isCurrent || options.isCurrent());
  entries = yield* filterFileExplorerDeletedPathTombstonesSteps(parentPath, entries, {isCurrent});
  if (!isCurrent()) return superseded();
  // Step the .file-tree-children::before guide line per nesting level: rows indent 14px/level
  // (updateFileTreeRow padding-left = base + depth*14), and the children wrappers are NOT physically
  // indented, so without this the fixed-left guide stacks every level at one x and only level 1 shows.
  setStylePropertyIfChanged(container.style, '--tree-depth', String(depth));
  yield clientElementIsVisible(container);
  yield* cacheFileExplorerRepoInfoEntrySteps(parentPath, entries, {isCurrent});
  if (!isCurrent()) return superseded();
  const renderOptions = {
    ...options,
    // Share the join across recursive rows; scanning the changed-file payload
    // for every row blocks input when a large tree is refreshed.
    sessionFilesMap: options.sessionFilesMap || indexSessionFilesByPath(fileExplorerFinderSessionFilesState.payload?.files || [], item => item?.abs_path, {preferNewest: true}),
    sessionHighlightSets: options.sessionHighlightSets || fileExplorerSessionHighlightSets(),
    changedAncestorStats: options.changedAncestorStats instanceof Map ? options.changedAncestorStats
      : (options.differMode === true ? null : fileTreeChangedAncestorStats()),
  };
  // Every Finder tree renderer, including a direct user expansion, must honor the same first-paint
  // boundary. The root open path passes this flag explicitly, but expansion and sync renderers do not
  // share that call site; derive it here so a pending session-files payload cannot leak stale +/- rows.
  if (
    renderOptions.view === 'finder'
    && typeof finderSessionFilesDiffIsDeferred === 'function'
    && finderSessionFilesDiffIsDeferred()
  ) {
    renderOptions.deferSessionFilesDiff = true;
  }
  const entriesByDir = renderOptions.entriesByDir instanceof Map ? renderOptions.entriesByDir : null;
  let visible = yield* sortedFileTreeEntrySteps(entries, renderOptions.treeSortMode, {...renderOptions, deletedLast: renderOptions.differMode === true});
  if (renderOptions.preserveDisplayedOrder === true) {
    // Retain each branch's surviving siblings; new rows follow them in the latest sort order.
    const remaining = new Map(visible.map(entry => [childPath(parentPath, entry.name), entry]));
    const retained = [];
    for (const row of container.children) {
      const entry = row.classList.contains('file-tree-row') && remaining.get(row.dataset.path);
      if (!entry) continue;
      retained.push(entry);
      remaining.delete(row.dataset.path);
    }
    visible = [...retained, ...remaining.values()];
    if (renderOptions.tabberNormalOrder === true) {
      const conversations = visible.filter(entry => entry?.tabber?.type !== 'tab');
      const utilityTabs = visible.filter(entry => entry?.tabber?.type === 'tab').sort(tabberUtilityEntryCompare);
      visible = [...conversations, ...utilityTabs];
    }
  }
  const visiblePaths = new Map();
  for (const entry of visible) {
    visiblePaths.set(childPath(parentPath, entry.name), entry);
    yield;
    if (!isCurrent()) return superseded();
  }
  const existingRows = new Map();
  // Retire the old snapshot before any replacement row can paint beside it.
  let existingNode = container.firstElementChild;
  while (existingNode) {
    if (!isCurrent()) return superseded();
    const row = existingNode;
    const path = row.dataset.path;
    const isRow = row.classList?.contains('file-tree-row');
    const entry = isRow ? visiblePaths.get(path) : null;
    const child = isRow ? childContainerForRow(row, path) : null;
    const next = (child || row).nextElementSibling;
    const keepChild = entry?.kind === 'dir' && (
      fileTreeDirectoryExpanded(path, renderOptions) || fileExplorerDirectoryListingFailed(path)
    );
    let changed = false;
    if (child && !keepChild) {
      child.remove();
      changed = true;
    }
    if (!entry) {
      row.remove();
      changed = true;
    } else existingRows.set(path, row);
    yield changed && clientElementIsVisible(container) ? container : false;
    existingNode = fileTreeNodeAfterYield(container, next);
  }
  const retainedRows = new Set();
  let previousRow = null;
  for (const entry of visible) {
    if (!isCurrent()) return superseded();
    if (!filterFileExplorerDeletedPathTombstones(parentPath, [entry]).length) continue;
    if (typeof renderOptions.refreshContext === 'function') Object.assign(renderOptions, renderOptions.refreshContext());
    const fullPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`;
    const row = existingRows.get(fullPath) || document.createElement('div');
    // collapsedSet (the Tabber) = default-expanded, collapse to opt out; expandedSet = default-collapsed,
    // expand to opt in (fixed-root Finder uses fileExplorerExpanded). Differences as data, no mode branch.
    const dirExpanded = entry.kind === 'dir' && fileTreeDirectoryExpanded(fullPath, renderOptions);
    const normalizedFullPath = normalizeDirectoryPath(fullPath);
    const suppliedChildEntries = entriesByDir?.get(normalizedFullPath);
    const childEntries = Array.isArray(suppliedChildEntries)
      ? suppliedChildEntries
      : (entry.kind === 'dir' && dirExpanded && renderOptions.view === 'finder'
        ? cachedFileExplorerFsResourceValue('list', normalizedFullPath)
        : undefined);
    const hasRenderedChildren = entry.kind === 'dir' && (
      Boolean(childContainerForRow(row, fullPath)) || Array.isArray(childEntries)
    );
    if (
      !(renderOptions.listingDirectories instanceof Set)
      || renderOptions.listingDirectories.has(parentPath)
      || !existingRows.has(fullPath)
      || (entry.kind === 'dir' && renderOptions.reconcileDirectories?.has(normalizedFullPath))
    ) {
      updateFileTreeRow(row, parentPath, entry, depth, {...renderOptions, hasRenderedChildren, dirExpanded});
    }
    // Capture adjacency before moving an existing row; its wrapper may otherwise be left behind.
    const existingChildContainer = childContainerForRow(row, fullPath);
    const childContainer = entry.kind === 'dir' && dirExpanded
      ? existingChildContainer || (Array.isArray(childEntries) ? createFileTreeChildContainer(fullPath) : null)
      : (entry.kind === 'dir' && fileExplorerDirectoryListingFailed(fullPath) ? existingChildContainer : null);
    const previousChild = previousRow && childContainerForRow(previousRow, previousRow.dataset.path);
    const anchor = previousChild || previousRow;
    const nextNode = anchor ? anchor.nextElementSibling : container.firstElementChild;
    if (nextNode !== row) container.insertBefore(row, nextNode || null);
    retainedRows.add(row);
    previousRow = row;
    if (childContainer && row.nextElementSibling !== childContainer) container.insertBefore(childContainer, row.nextElementSibling || null);
    yield clientElementIsVisible(container) ? container : false;
    if (!isCurrent()) return superseded();
    const stillExpanded = entry.kind === 'dir' && fileTreeDirectoryExpanded(fullPath, renderOptions);
    if (childContainer && childContainer.parentElement === container && stillExpanded && Array.isArray(childEntries) && (
      !existingChildContainer || !(renderOptions.reconcileDirectories instanceof Set)
      || renderOptions.reconcileDirectories.has(normalizedFullPath)
    )) {
      if ((yield* renderTreeChildrenSteps(childContainer, fullPath, childEntries, depth + 1, {...renderOptions, renderTransaction: transaction, isCurrent})) === false) transaction.completed = false;
    }
  }
  // Explicit expansion/collapse can add or remove a wrapper while this iterator yields.
  // Failed listings close the disclosure but keep a visible error until retry. Root metadata
  // reconciliation must retain that wrapper as well as the children of still-expanded rows.
  let retainedParent = null;
  let node = container.firstElementChild;
  while (node) {
    if (!isCurrent()) return superseded();
    const nextNode = node.nextElementSibling;
    if (retainedRows.has(node)) {
      retainedParent = node;
    } else {
      const fullPath = retainedParent?.dataset.path;
      const directory = retainedParent?.dataset.kind === 'dir' && fullPath;
      const expanded = directory && fileTreeDirectoryExpanded(fullPath, renderOptions);
      const failed = directory && fileExplorerDirectoryListingFailed(fullPath);
      if (!((expanded || failed) && childContainerForRow(retainedParent, fullPath) === node)) {
        node.remove();
        yield clientElementIsVisible(container) ? container : false;
      }
      retainedParent = null;
    }
    yield;
    node = fileTreeNodeAfterYield(container, nextNode);
  }
  recordFileTreeRender('complete', container, parentPath, transaction, options);
  return transaction.completed;
}

function zipFileDownloadUrl(path) {
  return `/api/batch/zip?path=${encodeURIComponent(path)}`;
}

function closeFileImagePreview(options = {}) {
  if (!options.fromController) fileImagePreviewController?.cancelTimers?.();
  releaseRawFileMediaSources(fileImagePreviewPopover);
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

function openFileImagePreview(anchor, path, entry, point = null, options = {}) {
  closeFileImagePreview();
  if (!anchor || !document.body) return;
  const popover = document.createElement('div');
  popover.className = ['file-image-preview-popover', options.className || ''].filter(Boolean).join(' ');
  popover.dataset.previewPath = path;
  const mediaKind = options.mediaKind === 'video' ? 'video' : 'image';
  const media = document.createElement(mediaKind === 'video' ? 'video' : 'img');
  if (mediaKind === 'video') {
    media.autoplay = true;
    media.loop = true;
    media.muted = true;
    media.playsInline = true;
  } else {
    media.alt = entry?.name || basenameOf(path);
  }
  popover.appendChild(media);
  appOverlayRootElement().appendChild(popover);
  fileImagePreviewPopover = popover;
  positionFileImagePreview(anchor, popover, point);
  if (options.sourceUrl) {
    media.src = options.sourceUrl;
  } else {
    void installRawFileMediaSource(media, path, {
      onFailure: error => {
        if (fileImagePreviewPopover !== popover) return;
        const failure = document.createElement('div');
        failure.className = 'file-image-preview-error';
        failure.textContent = userMessageText(error, t('preview.image.loadFailed'));
        popover.replaceChildren(failure);
        positionFileImagePreview(anchor, popover, point);
      },
      isCurrent: () => fileImagePreviewPopover === popover,
    });
  }
}

function bindFileImagePreview(anchor, path, entry, options = {}) {
  if (!anchor) return null;
  return bindScopedOnce(anchor, 'file-image-preview', scope => {
  let pointer = null;
  const updatePointer = event => {
    pointer = {x: event.clientX, y: event.clientY};
    if (fileImagePreviewPopover?.dataset.previewPath === path) positionFileImagePreview(anchor, fileImagePreviewPopover, pointer);
  };
  const controller = createHoverPopover({
    anchor,
    popover: () => fileImagePreviewPopover?.dataset.previewPath === path ? fileImagePreviewPopover : null,
    stateClass: '',
    showDelay: () => popoverShowDelayMs,
    hideDelay: () => popoverHideDelayMs,
    canOpen: () => !appMenuIsOpen() && !contextMenuIsOpen() && !topbar?.matches?.(':hover'),
    stillActive: () => popoverStillActive(anchor, fileImagePreviewPopover?.dataset.previewPath === path ? fileImagePreviewPopover : null),
    onQueue: updatePointer,
    onOpen: event => {
      updatePointer(event);
      fileImagePreviewController = controller;
      openFileImagePreview(anchor, path, entry, pointer, options);
    },
    onClose: () => {
      if (fileImagePreviewPopover?.dataset.previewPath === path) closeFileImagePreview({fromController: true});
      if (fileImagePreviewController === controller) fileImagePreviewController = null;
    },
  });
  scope.ownEvent('pointerenter', anchor, 'pointerenter', event => {
    fileImagePreviewController = controller;
    updatePointer(event);
  });
  scope.ownEvent('pointermove', anchor, 'pointermove', updatePointer);
  return () => controller?.dispose?.();
  });
}

function selectableFileTreeRows(container = document) {
  return Array.from(container.querySelectorAll('.file-tree-row[data-path]'))
    .filter(row => !row.dataset.tabberType && (row.dataset.kind === 'file' || row.dataset.kind === 'dir'));
}

function updateFileExplorerCurrentFileHighlight(options = {}) {
  if (options.yieldToInput === true) return scheduleFileTreeMetadataRefresh({highlightsOnly: true});
  updateFileExplorerCurrentFileHighlightRows(document.querySelectorAll('.file-tree-row'));
}

function updateFileExplorerCurrentFileHighlightRows(rows) {
  const currentDirectory = activeFinderDirectoryPath();
  rows.forEach(row => {
    if (row.dataset.tabberType || !clientElementIsVisible(row)
        || (row.closest?.('.file-explorer-changes-panel') && !fileExplorerSessionFilesPaneIsVisible())) return;
    const selected = fileExplorerSelectedPaths.has(row.dataset.path);
    const currentFile = row.dataset.kind === 'file' && row.dataset.path === activeFile;
    const currentDirectoryRow = !currentFile && row.dataset.kind === 'dir' && row.dataset.path === currentDirectory;
    row.classList.toggle(CLS.selected, selected);
    row.classList.toggle('current-file', currentFile);
    row.classList.toggle('current-directory', currentDirectoryRow);
    setAttributeIfChanged(row, 'aria-selected', selected ? 'true' : 'false');
    setAttributeIfChanged(row, 'aria-current', currentFile || currentDirectoryRow ? 'true' : null);
  });
}

function scheduleFileExplorerActiveFileReveal(path = activeFile, options = {}) {
  if (!path) {
    updateFileExplorerCurrentFileHighlight();
    return;
  }
  const target = normalizeDirectoryPath(path);
  updateFileExplorerCurrentFileHighlight();
  if (
    fileExplorerRootMode === 'sync'
    && typeof syncFileExplorerRootToActiveFile === 'function'
  ) {
    if (options.explicit === true) {
      scheduleFileExplorerActiveTabSync(fileEditorItemFor(path), {explicit: true});
      return;
    }
    const explicitSession = fileExplorerExplicitSyncSessionTarget();
    const fileSyncPlan = fileExplorerSyncPlanForFile(path);
    if (explicitSession && fileSyncPlan.session !== explicitSession) return;
    syncFileExplorerRootToActiveFile(path, {force: true, guardExplicitTarget: true}).catch(error => {
      console.warn('Finder active file sync failed', error);
    });
    return;
  }
  const root = normalizeDirectoryPath(currentFileExplorerRoot());
  if (!fileExplorerIsOpen() || !pathIsInsideDirectory(target, root)) return;
  if (!fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'))) return;
  if (options.explicit === true) fileWorkspaceState.invalidateInteraction();
  const generation = ++fileExplorerSyncState.generation;
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
  schedule(() => {
    if (generation !== fileExplorerSyncState.generation) return;
    expandFileExplorerTreesToPath(target, root, generation, options.explicit === true ? {user: true} : {auto: true}).catch(error => {
      console.warn('Finder active file reveal failed', error);
    });
  });
}

function fileTreeRowIsDiffer(row) {
  // A changed Finder file shares its open action with Differ, but retains Finder's payload owner.
  return Boolean(row.closest?.('.file-explorer-changes-panel, .git-diff-file-tree')
    || row.dataset.changesFolderToggle || row.dataset.openChangeDirectory);
}

const fileTreeMetadataRefreshState = {generation: 0, timer: 0, needsMetadata: false, nextRow: null};

function scheduleFileTreeMetadataRefresh({highlightsOnly = false, pendingOnly = false} = {}) {
  const state = fileTreeMetadataRefreshState;
  // Target changes retire captured highlights, but must finish outstanding metadata for retained rows.
  if (pendingOnly && !state.needsMetadata) return;
  const generation = ++state.generation;
  state.needsMetadata = state.needsMetadata || !highlightsOnly;
  const paintMetadata = state.needsMetadata;
  if (state.timer) clearTimeout(state.timer);
  state.timer = 0;
  const payload = fileExplorerFinderSessionFilesState.payload;
  const root = currentFileExplorerRoot();
  const rootMode = fileExplorerRootMode;
  const syncTarget = fileExplorerExplicitSyncSessionTarget();
  state.timer = setTimeout(function flushFileTreeMetadataRefresh() {
    if (generation !== state.generation) return;
    state.timer = 0;
    if (payload !== fileExplorerFinderSessionFilesState.payload || root !== currentFileExplorerRoot()
        || rootMode !== fileExplorerRootMode || syncTarget !== fileExplorerExplicitSyncSessionTarget()) return;
    const allRows = Array.from(document.querySelectorAll('.file-tree-row[data-path]:not([data-tabber-type])')).filter(row => {
      if (!fileTreeRowIsDiffer(row)) return true;
      if (row.closest?.('.file-explorer-changes-panel') && !fileExplorerSessionFilesPaneIsVisible()) return false;
      return clientElementIsVisible(row);
    });
    const resumeIndex = Math.max(0, allRows.indexOf(state.nextRow));
    const rows = [...allRows.slice(resumeIndex), ...allRows.slice(0, resumeIndex)];
    const context = paintMetadata ? fileTreeSessionFilesRenderContext(payload) : null;
    const sets = fileExplorerSessionHighlightSets();
    let index = 0;
    const paint = function paintFileTreeMetadataBatch() {
      if (generation !== state.generation) return;
      state.timer = 0;
      if (payload !== fileExplorerFinderSessionFilesState.payload || root !== currentFileExplorerRoot()
          || rootMode !== fileExplorerRootMode || syncTarget !== fileExplorerExplicitSyncSessionTarget()) return;
      const started = performance.now();
      // Keep producer work interruptible without changing synchronous user-action owners.
      do {
        const batch = rows.slice(index, index + 32).filter(row => row.isConnected !== false);
        index += 32;
        state.nextRow = rows[index % rows.length] || null;
        if (paintMetadata) updateFileTreeGitStatusRows(batch, {context, skipHighlights: true});
        updateFileExplorerCurrentFileHighlightRows(batch);
        for (const row of batch) applyFileExplorerSessionHighlightRow(row, sets);
      } while (index < rows.length && performance.now() - started < fileTreeInputSliceMs);
      if (index < rows.length) state.timer = setTimeout(paint, 0);
      else {
        state.needsMetadata = false;
        state.nextRow = null;
      }
    };
    paint();
  }, 0);
}

function updateFileTreeGitStatusRows(targetRows = null, options = {}) {
  const {changedAncestorStats, sessionFilesMap} = options.context || fileTreeSessionFilesRenderContext();
  // Tabber rows have synthetic paths; its own renderer owns their labels and status.
  const rows = targetRows === null
    ? document.querySelectorAll('.file-tree-row[data-path]:not([data-tabber-type])')
    : targetRows;
  for (const row of rows) {
    if (!row?.dataset?.path || row.dataset.tabberType || fileTreeRowIsDiffer(row)) continue;
    const fullPath = row.dataset.path || '';
    const isRepo = Boolean(exactFileExplorerRepoInfo(fullPath))
      || (row.dataset.repoInfoResolved !== 'true' && row.dataset.isRepo === 'true');
    row.dataset.isRepo = isRepo ? 'true' : 'false';
    const entry = {
      kind: row.dataset.kind,
      name: row.dataset.name || basenameOf(fullPath),
      is_repo: isRepo,
      is_symlink: row.dataset.isSymlink === 'true',
      symlink_target: row.dataset.symlinkTarget || '',
    };
    applyFileTreeRowDerivedState(row, fileTreeRowDerivedState(fullPath, entry, {
      changedAncestorStats,
      sessionFilesMap,
      differMode: false,
      expanded: entry.kind === 'dir' && fileTreeDirectoryExpanded(fullPath),
      iconText: row.querySelector(':scope > .file-tree-icon')?.textContent || '',
      preserveDate: true,
      deferSessionFilesDiff: typeof finderSessionFilesDiffIsDeferred === 'function' && finderSessionFilesDiffIsDeferred(),
    }));
    if (isRepo) {
      row.removeAttribute('title');
      fileTreeRepoHoverController(row, fullPath);
    } else if (row.__yomuxRepoHoverController) {
      row.__yomuxRepoHoverController.dispose();
      delete row.__yomuxRepoHoverController;
      if (row.dataset.repoTitleLoaded) delete row.dataset.repoTitleLoaded;
    }
  }
  if (!options.skipHighlights) {
    const sets = fileExplorerSessionHighlightSets();
    for (const row of rows) applyFileExplorerSessionHighlightRow(row, sets);
  }
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
      restoreElementScrollPosition(scrollContainer, restoreScrollTop, scrollContainer.scrollLeft, {
        deferredOwner: createInputOwnedDeferredElementScrollOwner(scrollContainer),
        owner: 'finder-selection-render-restore',
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
    setFileExplorerSelectionPin(true);
    selectFileTreeRange(row, fullPath, {clear: !toggleModifier});
    return true;
  }
  if (toggleModifier) {
    setFileExplorerSelectionPin(true);
    selectFileTreePath(fullPath, {clear: false, toggle: true});
    return true;
  }
  // A normal Finder file open is just as explicit as a modifier selection.
  // Directory clicks only expand/collapse and must not change its sync target.
  setFileExplorerSelectionPin(row?.dataset?.kind === 'file' && Boolean(row?.closest?.('.file-explorer-panel')));
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
    selectedIds(panel = null) {
      if (typeof options.selectedIds === 'function') {
        const selected = options.selectedIds(panel);
        if (selected instanceof Set) return selected;
        if (Array.isArray(selected)) return new Set(selected.map(String));
      }
      return state.selectedIds;
    },
    leadId(panel = null) {
      return typeof options.getLeadId === 'function' ? String(options.getLeadId(panel) || '') : state.leadId;
    },
    setLeadId(id, panel = null) {
      const value = String(id || '');
      state.leadId = value;
      if (typeof options.setLeadId === 'function') options.setLeadId(value, panel);
    },
    currentId(panel = null) {
      return typeof options.currentRowId === 'function' ? String(options.currentRowId(panel) || '') : '';
    },
    rowIsCurrent(row, panel = null) {
      const id = sharedTreeRowId(row);
      return Boolean(id && id === controller.currentId(panel));
    },
    applyState(panel, stateOptions = {}) {
      let currentRow = null;
      const rows = controller.rows(panel);
      const selectedIds = controller.selectedIds(panel);
      const lead = controller.leadRow(panel);
      for (const row of rows) {
        const id = sharedTreeRowId(row);
        const selected = selectedIds.has(id);
        const current = controller.rowIsCurrent(row, panel);
        row.classList.toggle(CLS.selected, selected);
        setAttributeIfChanged(row, 'aria-selected', selected ? 'true' : 'false');
        if (options.rovingFocus === true) row.tabIndex = row === lead ? 0 : -1;
        if (options.applyCurrentClasses !== false) {
          row.classList.toggle('current-file', current && row.dataset.kind !== 'dir');
          row.classList.toggle('current-directory', current && row.dataset.kind === 'dir');
          if (current || options.preserveExistingAriaCurrent !== true) {
            setAttributeIfChanged(row, 'aria-current', current ? 'true' : null);
          }
        }
        if (current) currentRow = row;
      }
      if (stateOptions.scrollCurrent === true && currentRow) sharedTreeScrollRowIntoView(panel, currentRow, options);
      if (stateOptions.focusLead === true && lead) lead.focus?.({preventScroll: true});
    },
    selectRow(panel, row, event = null, selectOptions = {}) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id, panel);
      if (typeof options.selectRow === 'function') {
        options.selectRow(row, id, event, selectOptions, panel);
      } else {
        const selectedIds = controller.selectedIds(panel);
        selectedIds.clear();
        selectedIds.add(id);
      }
      controller.applyState(panel, {focusLead: options.rovingFocus === true});
      if (selectOptions.scrollIntoView !== false) sharedTreeScrollRowIntoView(panel, row, options);
      return true;
    },
    selectRange(panel, row, event = null) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id, panel);
      if (typeof options.selectRange === 'function') {
        options.selectRange(row, id, event, panel);
      } else {
        const rows = controller.rows(panel);
        const selectedIds = controller.selectedIds(panel);
        const anchorId = selectedIds.values().next().value || controller.leadId(panel) || id;
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
      controller.applyState(panel, {focusLead: options.rovingFocus === true});
      sharedTreeScrollRowIntoView(panel, row, options);
      return true;
    },
    selectFromClick(panel, row, event) {
      const id = sharedTreeRowId(row);
      if (!id) return false;
      controller.setLeadId(id, panel);
      const selectionOnly = typeof options.selectFromClick === 'function'
        ? options.selectFromClick(row, id, event, panel)
        : (controller.selectRow(panel, row, event, {scrollIntoView: options.scrollOnClick !== false}), false);
      controller.applyState(panel, {focusLead: options.rovingFocus === true});
      return selectionOnly === true;
    },
    leadRow(panel) {
      const rows = controller.rows(panel);
      const leadId = controller.leadId(panel);
      return rows.find(row => sharedTreeRowId(row) === leadId)
        || rows.find(row => controller.selectedIds(panel).has(sharedTreeRowId(row)))
        || rows.find(row => controller.rowIsCurrent(row, panel))
        || rows[0]
        || null;
    },
    syncCurrent(panel, syncOptions = {}) {
      const currentId = controller.currentId(panel);
      if (currentId && typeof options.syncCurrentSelection === 'function') options.syncCurrentSelection(currentId);
      controller.setLeadId(currentId || controller.leadId(panel), panel);
      if (syncOptions.modelOnly !== true) controller.applyState(panel, {scrollCurrent: syncOptions.scrollIntoView === true});
      return Boolean(currentId);
    },
  };
}

function sharedTreeExpansionApi(controller, options = {}) {
  return {
    isExpanded(row, panel = null) {
      if (typeof options.isExpanded === 'function') return options.isExpanded(row, panel) === true;
      return row?.getAttribute?.('aria-expanded') === 'true';
    },
    setExpanded(panel, row, expanded) {
      if (typeof options.setExpanded === 'function') {
        options.setExpanded(row, expanded === true, panel);
        return true;
      }
      return false;
    },
  };
}

function sharedTreeClickHandler(controller, options = {}) {
  return function handleSharedTreeClick(event, panel, clickOptions = {}) {
    if (typeof dockviewTabContentInteractionSuppressed === 'function' && dockviewTabContentInteractionSuppressed()) {
      consumeSharedTreeEvent(event);
      return false;
    }
    const row = clickOptions.row || event?.target?.closest?.(options.rowSelector || '.file-tree-row[data-path]');
    if (!row || !panel?.contains?.(row) || !controller.rows(panel).includes(row)) return false;
    if (typeof options.shouldIgnoreEvent === 'function' && options.shouldIgnoreEvent(event)) return false;
    if (typeof globalShortcutTargetAllowsAppAction === 'function' && !globalShortcutTargetAllowsAppAction(event?.target) && !clickOptions.row) return false;
    const onDisclosure = Boolean(event?.target?.closest?.('.file-tree-icon'));
    consumeSharedTreeEvent(event);
    const selectionOnly = controller.selectFromClick(panel, row, event);
    if (row.dataset.kind === 'dir' && onDisclosure) {
      controller.setExpanded(panel, row, !controller.isExpanded(row, panel));
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
    if (!eventTargetsOwnedPanel && !fileExplorerKeyboardEventAllowsAction(event)) return false;
    claimFileExplorerScrollInputOwnership({target: rows[0]});
    const lead = controller.leadRow(panel);
    let leadIndex = lead ? rows.indexOf(lead) : -1;
    if (intent === 'select-all' && options.allowSelectAll === true) {
      consumeSharedTreeEvent(event);
      const selectedIds = controller.selectedIds(panel);
      selectedIds.clear();
      for (const row of rows) selectedIds.add(sharedTreeRowId(row));
      controller.setLeadId(sharedTreeRowId(rows[rows.length - 1]), panel);
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
      const expanded = controller.isExpanded(lead, panel);
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
        options.activateRow(row, event, panel);
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

function fileExplorerIndexedRootForPath(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized) return '';
  return fileExplorerIndexedRootList()
    .filter(candidate => candidate === normalized || pathIsInsideDirectory(normalized, candidate))
    .sort((left, right) => right.length - left.length)[0] || '';
}

function fileExplorerIndexExclusionForPath(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized || !fileExplorerIndexedRootForPath(normalized)) return '';
  return Array.from(fileExplorerIndexExcludePaths || [])
    .filter(candidate => candidate === normalized || pathIsInsideDirectory(normalized, candidate))
    .sort((left, right) => right.length - left.length)[0] || '';
}

function fileExplorerPathIsExcluded(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  return Boolean(normalized && fileExplorerIndexExclusionForPath(normalized) === normalized);
}

// Readable index-state text for a path. Keep it visible beside Date/Ago so index coverage never relies
// on the directory icon's small gold dot alone.
function fileExplorerIndexBadgeText(path) {
  if (fileExplorerPathIsExcluded(path)) return t('finder.index.excluded');
  if (fileExplorerDirectoryIsIndexed(path)) {
    const normalized = normalizeStoredFileExplorerIndexedDir(path);
  const status = fileExplorerIndexStatus.get(normalized);
  return status === 'building' ? '…' : (status === 'too_large' ? '!' : (status === 'error' ? '×' : (status === 'stale' ? t('finder.index.staleBadge') : t('finder.index.indexed'))));
  }
  if (fileExplorerIndexedAncestor(path)) return '';
  return '';
}

function fileExplorerIndexBadgeTitle(path) {
  if (fileExplorerPathIsExcluded(path)) return t('finder.index.excluded');
  if (!fileExplorerDirectoryIsIndexed(path)) return '';
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  const status = fileExplorerIndexStatus.get(normalized);
  if (status === 'too_large') {
    const payload = fileExplorerIndexStatusPayload.get(normalized) || {};
    return [t('finder.index.partial'), fileExplorerIndexPartialDetails(payload)].join('\n');
  }
  if (status === 'error') {
    const payload = fileExplorerIndexStatusPayload.get(normalized) || {};
    const detail = String(payload.error || payload.coverage_reason || '').trim() || t('common.searchFailed');
    return t('common.errorDetail', {error: detail});
  }
  if (status === 'stale') return t('finder.index.staleTitle');
  return t(status === 'building' ? 'finder.index.indexing' : 'finder.index.indexed');
}

function fileExplorerIndexPartialDetails(payload = {}) {
  const progressiveCount = Number(fileIndexProgressiveCoverage(payload)?.entry_count);
  const count = Math.max(0, Number.isFinite(progressiveCount) && progressiveCount > 0 ? progressiveCount : (Number(payload.count) || 0));
  const configuredFileLimit = Number(payload.max_files) || Number(initialSetting('file_explorer.index_max_files')) || 0;
  const limit = Math.max(0, configuredFileLimit || count);
  const reason = String(payload.coverage_reason || payload.error || '');
  const reasonLower = reason.toLowerCase();
  const hitFileLimit = reasonLower.includes('file limit') || (!reason && limit > 0 && count >= limit);
  const storageLimitMb = Math.floor((Number(payload.max_bytes) / (1024 * 1024)) || Number(initialSetting('file_explorer.index_persist_max_mb')) || 0);
  let summary;
  if (hitFileLimit) summary = `Hit the per-root limit of ${limit.toLocaleString()} files after indexing ${count.toLocaleString()}.`;
  else if ((reasonLower.includes('storage') || reasonLower.includes('budget')) && storageLimitMb > 0) {
    summary = `Hit the ${storageLimitMb.toLocaleString()} MiB snapshot storage budget after indexing ${count.toLocaleString()} files.`;
  } else summary = `Stopped after indexing ${count.toLocaleString()} files; the per-root limit is ${limit.toLocaleString()}.`;
  return `${summary}\nDetails: ${reason || 'eligible paths could not be traversed'}.`;
}

// The ONE file-index freshness derivation for every surface. `/api/fs/search` and
// `/api/fs/index-status` both spread `SnapshotFreshness.payload_fields()`
// (yomux_lib/search/file_index.py), so the Finder index badge and Quick Open read this record and
// nothing else. A second copy of this judgement is exactly how the live incident shipped: a snapshot
// whose producer was dead degraded to "building" and the user was told nothing at all.
const FILE_INDEX_STALE_FRESHNESS_STATES = ['stale', 'orphaned'];

function fileIndexSearchIsWarming(payload) {
  return payload?.index_state !== 'error'
    && (payload?.index_state === 'warming' || payload?.index_coverage === 'pending');
}

function fileIndexFreshnessFromPayload(payload) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const state = String(source.freshness || '');
  const rawAge = Number(source.snapshot_age_seconds);
  return {
    state,
    partial: source.index_coverage === 'partial',
    failed: source.index_state === 'error',
    missing: source.indexed === false || source.generation === 0,
    reason: String(source.freshness_reason || source.coverage_reason || ''),
    producerState: String(source.producer_state || ''),
    ageSeconds: Number.isFinite(rawAge) && rawAge >= 0 ? rawAge : null,
    // `stale`, `freshness`, `index_state` and `index_coverage` are four projections of ONE backend
    // verdict. Read them together here so no caller can re-derive a friendlier answer from one of
    // them; `missing` is deliberately not stale, because nothing was served to mislabel.
    stale: source.stale === true
      || FILE_INDEX_STALE_FRESHNESS_STATES.includes(state)
      || String(source.index_state || '') === 'stale'
      || String(source.index_state || '') === 'error'
      || String(source.index_coverage || '') === 'unverified',
    refreshing: source.refreshing === true,
  };
}

// Words a non-engineer can act on, never a raw field dump. Rendered (not derived) so a locale change
// re-renders the same record in the new language.
function fileIndexFreshnessMessage(freshness) {
  if (!freshness) return '';
  if (freshness.stale !== true) return freshness.partial === true ? t('finder.index.partialCoverage') : '';
  if (freshness.failed && freshness.missing) return t('common.searchFailed');
  const lines = [freshness.ageSeconds === null
    ? t('finder.index.staleResultsUnknownAge')
    : t('finder.index.staleResults', {age: relativeTimeFormat(freshness.ageSeconds)})];
  if (freshness.failed) lines.unshift(t('common.searchFailed'));
  if (freshness.producerState === 'not_running') lines.push(t('finder.index.staleProducerNotRunning'));
  else if (freshness.producerState === 'unrecorded') lines.push(t('finder.index.staleProducerUnrecorded'));
  else if (freshness.reason === 'producer_vouch_expired') lines.push(t('finder.index.staleProducerBehind'));
  if (freshness.refreshing) lines.push(t('finder.index.staleRefreshRunning'));
  return lines.join(' ');
}

// A live progressive BFS crawl publishes usable rows while it keeps walking. Its authoritative live
// state is `progressive_coverage`, not the top-level `coverage`/`too_large` fields, which can still
// carry a superseded generation's terminal verdict. While the crawl is actively walking and has
// neither been truncated nor reached full coverage, the root is BUILDING (partial-but-growing), not
// the terminal "file limit reached" state — so results keep flowing and no capped-index warning fires.
function fileIndexProgressiveCoverage(payload) {
  const coverage = payload && typeof payload.progressive_coverage === 'object' ? payload.progressive_coverage : null;
  return coverage;
}

function fileIndexProgressiveStillBuilding(payload) {
  const coverage = fileIndexProgressiveCoverage(payload);
  return Boolean(coverage && coverage.truncated === false && coverage.full_coverage === false);
}

function fileIndexPublishedGeneration(payload) {
  const coverage = fileIndexProgressiveCoverage(payload);
  const published = coverage ? Number(coverage.published_generation) : NaN;
  if (Number.isFinite(published) && published > 0) return published;
  const generation = Number(payload?.generation || payload?.completed_generation || 0);
  return Number.isFinite(generation) && generation > 0 ? generation : 0;
}

// Warm the backend index for a root (kicks the build) and preserve partial coverage as a distinct,
// user-visible terminal state instead of silently presenting a capped index as complete.
function fileIndexStatusFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return 'building';
  const state = String(payload.state || payload.index_state || '');
  const freshness = fileIndexFreshnessFromPayload(payload);
  // A live progressive crawl that has not been truncated and has not reached full coverage is still
  // building: it is not the terminal capped state, even if an older persisted snapshot for this root
  // was truncated. This must be read before the too_large/partial derivation below so an in-progress
  // BFS is never mislabelled "file limit reached".
  if (fileIndexProgressiveStillBuilding(payload)) return 'building';
  if (state === 'error') return 'error';
  if (payload.too_large === true || payload.coverage === 'partial' || payload.index_coverage === 'partial' || state === 'too_large') return 'too_large';
  if (payload.error && !payload.index_coverage) return 'error';
  // A served-but-unvouched snapshot is neither ready nor building: it answers, and it says so.
  if (freshness.stale) return 'stale';
  if (payload.ready === true || state === 'ready') return 'ready';
  return 'building';
}

function openFileIndexCoveragePreferences() {
  preferencesSearchText = '';
  collapsedPreferenceSections.delete(PREFERENCE_SECTION_IDS.fileExplorer);
  writeStoredCollapsedPreferenceSections();
  selectSession(prefsItemId, {userInitiated: true});
  requestAnimationFrame(() => {
    renderPreferencesPanels({force: true});
    const control = document.querySelector('[data-setting-path="file_explorer.index_exclude_paths"]');
    control?.focus?.({preventScroll: false});
    control?.scrollIntoView?.({block: 'center', inline: 'nearest'});
  });
}

function showFileIndexPartialCoverageWarning(root, payload = {}) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized || fileIndexPartialWarningRoots.has(normalized)) return false;
  fileIndexPartialWarningRoots.add(normalized);
  emitNotification('indexCoverage', {
    key: normalized,
    coalesceKey: `indexCoverage:${normalized}`,
    title: t('finder.index.partial'),
    lines: [`${normalized}\n${fileExplorerIndexPartialDetails(payload)}`],
    onClick: openFileIndexCoveragePreferences,
  });
  return true;
}

function applyFileIndexStatusPayload(root, payload) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return false;
  const workerIncarnation = String(payload?.worker_incarnation || '');
  const acceptedIncarnation = String(fileExplorerIndexWorkerIncarnation.get(normalized) || '');
  const retiredIncarnations = fileExplorerIndexRetiredWorkerIncarnations.get(normalized) || new Set();
  if (workerIncarnation && acceptedIncarnation && fileSearchWorkerIncarnationIsOlder(workerIncarnation, acceptedIncarnation)) return false;
  if (workerIncarnation && workerIncarnation !== acceptedIncarnation && retiredIncarnations.has(workerIncarnation)) return false;
  if (workerIncarnation && acceptedIncarnation && workerIncarnation !== acceptedIncarnation) {
    retiredIncarnations.add(acceptedIncarnation);
    if (retiredIncarnations.size > 8) retiredIncarnations.delete(retiredIncarnations.values().next().value);
    fileExplorerIndexRetiredWorkerIncarnations.set(normalized, retiredIncarnations);
    fileExplorerIndexGeneration.delete(normalized);
    fileExplorerIndexPublishedGeneration.delete(normalized);
    fileIndexPartialWarningRoots.delete(normalized);
  }
  if (workerIncarnation) fileExplorerIndexWorkerIncarnation.set(normalized, workerIncarnation);
  const generation = Number(payload?.generation || payload?.completed_generation || 0);
  const accepted = Number(fileExplorerIndexGeneration.get(normalized) || 0);
  if (Number.isSafeInteger(generation) && generation > 0 && generation < accepted) return false;
  if (Number.isSafeInteger(generation) && generation > 0) fileExplorerIndexGeneration.set(normalized, generation);
  const status = fileIndexStatusFromPayload(payload);
  const previous = fileExplorerIndexStatus.get(normalized);
  const previousTitle = fileExplorerIndexBadgeTitle(normalized);
  fileExplorerIndexStatus.set(normalized, status);
  fileExplorerIndexStatusPayload.set(normalized, payload);
  if (status === 'too_large') showFileIndexPartialCoverageWarning(normalized, payload);
  // Any non-capped status clears a prior "file limit reached" warning: a superseded truncated
  // generation must not keep a stale banner (with its stale count) on screen once the live crawl is
  // partial-but-growing, ready, or stale again.
  else fileIndexPartialWarningRoots.delete(normalized);
  if (status === 'building') fileIndexStatusPollRoots.add(normalized);
  else fileIndexStatusPollRoots.delete(normalized);
  syncFileIndexStatusPollInterval();
  // Track the progressive published generation so newly-published BFS rows re-surface in an open
  // Quick Open palette without a retype (below), independently of the top-level lifecycle generation.
  const publishedGeneration = fileIndexPublishedGeneration(payload);
  const previousPublished = Number(fileExplorerIndexPublishedGeneration.get(normalized) || 0);
  if (publishedGeneration > previousPublished) fileExplorerIndexPublishedGeneration.set(normalized, publishedGeneration);
  if (previous !== status || previousTitle !== fileExplorerIndexBadgeTitle(normalized)) updateFileExplorerIndexedDirectoryRows();
  // An open Quick Open palette no longer re-issues the WHOLE query on every index-status poll or
  // published-generation tick: the server-pushed search_progress signal now streams the newly-committed
  // matches by cursor (steps 6-8), so the redundant full re-query per tick is retired here. The one
  // explicit full-snapshot repair remains for the cases a cursor cannot bridge (rebase_required, a root
  // with no committed baseline yet, or an authoritative background-refresh completion).
  return true;
}

async function refreshFileIndexStatus(root) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return;
  let payload;
  try {
    payload = await apiFetchJson(`/api/fs/index-status?root=${encodeURIComponent(normalized)}`);
  } catch (error) {
    return;  // transient error: keep the prior badge, don't flip it
  }
  applyFileIndexStatusPayload(normalized, payload);
}

function fileIndexStatusConsumerVisible() {
  return !terminalAuthenticationActive() && document.visibilityState !== 'hidden' && (fileExplorerTreePaneIsVisible()
    || fileExplorerSessionFilesPaneIsVisible() || commandPaletteState.node?.hidden === false);
}

async function refreshBuildingFileIndexStatuses() {
  for (const root of Array.from(fileIndexStatusPollRoots)) {
    if (!fileIndexStatusConsumerVisible()) break;
    if (!fileExplorerIndexedDirs.has(root)) {
      fileIndexStatusPollRoots.delete(root);
      continue;
    }
    await refreshFileIndexStatus(root);
  }
  syncFileIndexStatusPollInterval();
}

function syncFileIndexStatusPollInterval() {
  if (!fileIndexStatusConsumerVisible()) {
    clearRuntimeInterval('file-index-refresh');
    clearRuntimeInterval('file-index-building');
    return;
  }
  if (clientEventTransportState.connected === true || fileExplorerIndexRefreshSeconds <= 0) {
    clearRuntimeInterval('file-index-refresh');
  } else {
    // Status polling is a disconnected-transport repair path. Do not leave a timer alive that
    // wakes up and returns null on every tick while SSE is already authoritative.
    resetRuntimeInterval('file-index-refresh', refreshAllIndexedDirsStatus, fileExplorerIndexRefreshSeconds * 1000);
  }
  if (!fileIndexStatusPollRoots.size) {
    clearRuntimeInterval('file-index-building');
    return;
  }
  const statusIntervalMs = fileExplorerIndexRefreshSeconds > 0 ? fileExplorerIndexRefreshSeconds * 1000 : 1501;
  // Push events advance Quick Open cursors, but only this targeted status read can settle a directory badge.
  // Backend-facing poll intervals stay odd-numbered to avoid synchronized timer collisions.
  resetRuntimeInterval('file-index-building', refreshBuildingFileIndexStatuses, Math.min(1501, statusIntervalMs));
}

// Lazily warm/poll an indexed root's status, without re-fetching once it is known ready or while a
// poll is already pending (so per-row render calls don't spam the endpoint).
function ensureFileIndexStatus(path) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  if (!normalized || !fileExplorerIndexedDirs.has(normalized)) return;
  // `stale` is a settled answer, not progress: polling it per rendered row cannot revive a dead
  // producer, it only spams the endpoint.
  if (['ready', 'too_large', 'stale'].includes(fileExplorerIndexStatus.get(normalized)) || fileIndexStatusPollRoots.has(normalized)) return;
  refreshFileIndexStatus(normalized);
}

function clearFileIndexStatus(root) {
  const normalized = normalizeStoredFileExplorerIndexedDir(root);
  if (!normalized) return;
  fileExplorerIndexStatus.delete(normalized);
  fileExplorerIndexStatusPayload.delete(normalized);
  fileExplorerIndexGeneration.delete(normalized);
  fileExplorerIndexWorkerIncarnation.delete(normalized);
  fileExplorerIndexRetiredWorkerIncarnations.delete(normalized);
  fileExplorerIndexPublishedGeneration.delete(normalized);
  fileIndexStatusPollRoots.delete(normalized);
  fileIndexPartialWarningRoots.delete(normalized);
  syncFileIndexStatusPollInterval();
}

function markFileIndexRootsRefreshing(roots = []) {
  let changed = false;
  for (const root of Array.isArray(roots) ? roots : []) {
    const normalized = normalizeStoredFileExplorerIndexedDir(root);
    if (!normalized || !fileExplorerIndexedDirs.has(normalized)) continue;
    fileExplorerIndexStatus.set(normalized, 'building');
    fileIndexStatusPollRoots.add(normalized);
    refreshFileIndexStatus(normalized);
    changed = true;
  }
  syncFileIndexStatusPollInterval();
  if (changed) updateFileExplorerIndexedDirectoryRows();
}

function refreshAllIndexedDirsStatus() {
  if (clientEventTransportState.connected === true) return null; // SSE is authoritative; repair only while disconnected.
  const finderVisible = document.visibilityState !== 'hidden'
    && itemIsActivePaneTab(finderItemId);
  const fileSearchVisible = document.visibilityState !== 'hidden'
    && commandPaletteState.node
    && !commandPaletteState.node.hidden
    && commandPaletteEffectiveMode() === 'files';
  if (!finderVisible && !fileSearchVisible) return;
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
  const root = fileExplorerIndexedRootForPath(normalized);
  return root && root !== normalized ? root : '';
}

function reconcileIndexExcludePathsFromSetting() {
  const raw = initialSetting('file_explorer.index_exclude_paths', []);
  const next = new Set(compactNestedPaths((Array.isArray(raw) ? raw : [])
    .map(normalizeStoredFileExplorerIndexedDir).filter(Boolean)));
  const previous = Array.from(fileExplorerIndexExcludePaths || []).sort();
  const current = Array.from(next).sort();
  fileExplorerIndexExcludePaths = next;
  if (previous.length === current.length && previous.every((value, index) => value === current[index])) return false;
  abortFileQuickOpenSearch();
  updateFileExplorerIndexedDirectoryRows();
  updateFileTreeGitStatusRows();
  return true;
}

async function setFileExplorerPathExcluded(path, excluded) {
  const normalized = normalizeStoredFileExplorerIndexedDir(path);
  const indexedRoot = fileExplorerIndexedRootForPath(normalized);
  if (!normalized || !indexedRoot || readOnlyMode) return false;
  const raw = initialSetting('file_explorer.index_exclude_paths', []);
  const paths = new Set((Array.isArray(raw) ? raw : [])
    .map(normalizeStoredFileExplorerIndexedDir).filter(Boolean));
  if (excluded) paths.add(normalized);
  else paths.delete(normalized);
  try {
    await saveSettingsPatch({file_explorer: {index_exclude_paths: compactNestedPaths(Array.from(paths))}});
    abortFileQuickOpenSearch();
    markFileIndexRootsRefreshing([indexedRoot]);
    updateFileExplorerIndexedDirectoryRows();
    updateFileTreeGitStatusRows();
    showTimedStatusNotice(excluded
      ? `${t('finder.index.excluded')}: ${compactHomePath(normalized)}`
      : t('common.indexedPath', {path: compactHomePath(normalized)}));
    return true;
  } catch (error) {
    statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))}));
    refreshSettings({force: true});
    return false;
  }
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
      if (!applyingIndexedDirsSetting) showTimedStatusNotice(t('finder.index.alreadyIndexedBy', {
        path: compactHomePath(normalized),
        ancestor: compactHomePath(ancestor),
      }));
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
      apiFetchJson(`/api/fs/unindex?root=${encodeURIComponent(normalized)}`, {method: 'POST', body: JSON.stringify({root: normalized})}).catch(() => {});
    }
  }
  writeStoredFileExplorerIndexedDirs();
  // Mirror the set into the file_explorer.indexed_dirs setting so the Preferences list stays in sync
  // (skip when this change WAS driven by the setting, to avoid a write-back loop). C11: pass the single
  // add/remove so the save MERGES into the shared list instead of overwriting it with this page's set.
  if (!applyingIndexedDirsSetting) persistIndexedDirsSetting(indexed ? {add: normalized} : {remove: normalized});
  updateFileExplorerIndexedDirectoryRows();
  if (!applyingIndexedDirsSetting) {
    showTimedStatusNotice(t(indexed ? 'common.indexedPath' : 'finder.index.removed', {path: compactHomePath(normalized)}));
  }
}

function persistIndexedDirsSetting(op = {}) {
  // C11: MERGE the change into the current shared file_explorer.indexed_dirs rather than overwriting it
  // with this page's whole set. Two browser origins (:7770 vs :7771) do NOT share localStorage, so a
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
      for (const root of fileExplorerIndexedDirs) refreshFileIndexStatus(root);
      return;
    }
    storageSet(fileExplorerIndexedDirsMigratedKey, '1');
  }
  const desired = new Set(compactNestedPaths(list.map(normalizeStoredFileExplorerIndexedDir).filter(Boolean)));
  const current = new Set(fileExplorerIndexedDirs);
  const adds = Array.from(desired).filter(dir => !current.has(dir));
  const removes = Array.from(current).filter(dir => !desired.has(dir));
  if (!adds.length && !removes.length) {
    if (options.initial) {
      for (const root of desired) refreshFileIndexStatus(root);
    }
    return;
  }
  applyingIndexedDirsSetting = true;
  try {
    for (const dir of adds) setFileExplorerDirectoryIndexed(dir, true);
    for (const dir of removes) {
      setFileExplorerDirectoryIndexed(dir, false);
      apiFetchJson(`/api/fs/unindex?root=${encodeURIComponent(dir)}`, {method: 'POST', commandRouteId: 'settings-unindex', body: JSON.stringify({root: dir})}).catch(() => {});
    }
  } finally {
    applyingIndexedDirsSetting = false;
  }
  if (options.initial) {
    for (const root of desired) {
      if (!adds.includes(root)) refreshFileIndexStatus(root);
    }
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
    const excluded = fileExplorerPathIsExcluded(path);
    const indexedDescendant = !indexed && !excluded && Boolean(fileExplorerIndexedAncestor(path));
    row.dataset.indexed = indexed ? 'true' : 'false';
    row.dataset.indexExcluded = excluded ? 'true' : 'false';
    row.classList.toggle('indexed-directory', indexed);
    row.classList.toggle('index-excluded-entry', excluded);
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
// tmux sessions (level 0) -> their tmux sub-windows (level 1, index:process, the current window marked) -> for
// visible agent windows, the paths that agent touched grouped by repo (level 2/3, from /api/session-files).
// Rows render through the SHARED row pipeline (renderTreeChildren -> updateFileTreeRow ->
// updateFileTreeRowContents) via a mode:'tabber' option whose display values are precomputed as data; the
// collapse state is a persisted COLLAPSED set (default expanded), window times come from one semantic
// recency helper (agent transcript events or non-agent activity ledger) with parent = max(child), and sort
// honors the active Finder sort (label for A-Z, mtime for recent).
// ---------------------------------------------------------------------------
let tabberRefreshDeferredTimer = null;
let tabberLayoutStateSignature = '';
let tabberLayoutStateFrame = 0;
let tabberLayoutStateScrollIntoView = false;
let tabberLayoutStateRefreshTree = false;
let tabberModeRevision = 0;
function normalizeTabberMode(value) {
  return value === 'advanced' ? 'advanced' : 'normal';
}

function saveTabberModePreference(value) {
  if (readOnlyMode) return Promise.resolve(false);
  return saveSettingsPatch(settingPatch('general.default_tabber_mode', normalizeTabberMode(value)), {preserveLayout: false});
}

function readTabberNormalSelections() {
  try {
    const stored = storageGet(tabberNormalSelectionsStorageKey);
    if (stored == null) return null;
    const parsed = JSON.parse(stored);
    return Array.isArray(parsed) ? parsed.map(value => String(value || '').trim()).filter(Boolean).slice(-1) : [];
  } catch (_) {
    return [];
  }
}

function persistTabberViewSessions() {
  storageSet(tabberViewSessionsStorageKey, JSON.stringify(Object.fromEntries(tabberViewSessions)));
  if (typeof refreshJsDebugSessionAliases === 'function') void refreshJsDebugSessionAliases();
}

function tabberWindowMetadataById(session, windowId) {
  const metadata = transcriptMetadataState.payload.sessions?.[session]?.window_metadata;
  if (!Array.isArray(metadata)) return undefined;
  return metadata.find(window => String(window.window_id || '') === String(windowId || '')) || null;
}

function renameTabberSessionsInClient(replacements) {
  let viewsChanged = false;
  for (const [item, descriptor] of tabberViewSessions) {
    const sourceSession = replacements.get(descriptor.sourceSession);
    if (!sourceSession) continue;
    const previousLabel = String(descriptor.label || '');
    tabberViewSessions.set(item, {
      ...descriptor,
      sourceSession,
      label: previousLabel.startsWith(descriptor.sourceSession)
        ? `${sourceSession}${previousLabel.slice(descriptor.sourceSession.length)}`
        : previousLabel,
    });
    // A readiness POST still names the old source; the renamed alias must claim a fresh request.
    tabberViewSessionPromises.delete(item);
    viewsChanged = true;
  }
  for (const [pendingKey] of tabberViewSessionPromises) {
    if (!pendingKey.startsWith('window:')) continue;
    const sourceSession = pendingKey.slice('window:'.length).split('\u001f', 1)[0];
    if (replacements.has(sourceSession)) tabberViewSessionPromises.delete(pendingKey);
  }
  const selections = tabberNormalSelections === null
    ? null
    : tabberNormalSelections.map(item => replacements.get(item) || item);
  const selectionsChanged = selections !== null
    && selections.some((item, index) => item !== tabberNormalSelections[index]);
  if (viewsChanged) persistTabberViewSessions();
  if (selectionsChanged) storeTabberNormalSelections(selections);
  else if (viewsChanged) syncFileLayoutItems();
  return viewsChanged || selectionsChanged;
}

function reconcileTabberViewSessionsForRoster(sessionNames) {
  const liveSessions = new Set(sessionNames || []);
  let viewsChanged = false;
  for (const [item, descriptor] of tabberViewSessions) {
    const source = transcriptMetadataState.payload.sessions?.[descriptor.sourceSession];
    const metadata = Array.isArray(source?.window_metadata) ? source.window_metadata : null;
    const liveWindowIds = metadata ? new Set(metadata.map(window => String(window.window_id || '')).filter(Boolean)) : null;
    if (liveSessions.has(descriptor.sourceSession)
      && (!liveWindowIds || liveWindowIds.has(String(descriptor.windowId || '')))) {
      const window = tabberWindowMetadataById(descriptor.sourceSession, descriptor.windowId);
      if (window && Number(window.window_index) !== Number(descriptor.windowIndex)) {
        tabberViewSessions.set(item, {...descriptor, windowIndex: Number(window.window_index)});
        viewsChanged = true;
      }
      continue;
    }
    tabberViewSessions.delete(item);
    tabberViewSessionReady.delete(item);
    tabberViewSessionPromises.delete(item);
    viewsChanged = true;
  }
  for (const [pendingKey] of tabberViewSessionPromises) {
    if (!pendingKey.startsWith('window:')) continue;
    const [sourceSession, windowId] = pendingKey.slice('window:'.length).split('\u001f', 2);
    if (!liveSessions.has(sourceSession) || tabberWindowMetadataById(sourceSession, windowId) === null) {
      tabberViewSessionPromises.delete(pendingKey);
    }
  }
  if (viewsChanged) persistTabberViewSessions();
  const registeredItems = new Set(allTabItems());
  const selections = tabberNormalSelections === null
    ? null
    : tabberNormalSelections.filter(item => {
      const view = tabberViewSessions.get(item);
      if (liveSessions.has(view?.sourceSession || item)) return true;
      return !isTmuxSession(item) && registeredItems.has(item);
    });
  const selectionsChanged = selections !== null
    && (selections.length !== tabberNormalSelections.length
      || selections.some((item, index) => item !== tabberNormalSelections[index]));
  if (selectionsChanged) storeTabberNormalSelections(selections);
  else if (viewsChanged) syncFileLayoutItems();
  return viewsChanged || selectionsChanged;
}

function tabberViewDescriptorForSession(item) {
  const descriptor = tabberViewSessions.get(String(item || ''));
  if (!descriptor) return null;
  const window = tabberWindowMetadataById(descriptor.sourceSession, descriptor.windowId);
  // An ordinal is a current address, never a window identity. Unknown metadata
  // must not redirect attention or pane selection to a different window.
  return {...descriptor, windowIndex: window ? Number(window.window_index) : -1};
}

function tabberViewSessionForWindow(session, windowId) {
  const source = String(session || '');
  const stableId = String(windowId || '');
  if (!stableId) return '';
  for (const [item, descriptor] of tabberViewSessions) {
    if (descriptor.sourceSession === source && String(descriptor.windowId || '') === stableId) return item;
  }
  return '';
}

function storeTabberViewSession(item, descriptor) {
  const key = String(item || '').trim();
  const sourceSession = String(descriptor?.sourceSession || '').trim();
  const windowIndex = Number(descriptor?.windowIndex);
  const windowId = String(descriptor?.windowId || '').trim();
  if (!key.startsWith(tabberViewSessionPrefix) || !sourceSession || !windowId || !Number.isInteger(windowIndex) || windowIndex < 0) return '';
  const sourceLabel = sessionLabel(sourceSession);
  const storedLabel = String(descriptor.label || '').trim();
  const windowLabel = String(descriptor.windowName || '').trim()
    || (storedLabel.startsWith(sourceLabel) ? storedLabel.slice(sourceLabel.length).trim() : '')
    || (storedLabel.startsWith(tabberViewSessionPrefix) ? '' : storedLabel)
    || `window ${windowIndex}`;
  tabberViewSessions.set(key, {
    ...descriptor,
    sourceSession,
    windowIndex,
    windowId,
    label: `${sourceLabel} ${windowLabel}`.trim(),
  });
  syncFileLayoutItems();
  persistTabberViewSessions();
  return key;
}

async function ensureTabberViewSessionReady(item, options = {}) {
  const key = String(item || '').trim();
  const descriptor = tabberViewSessions.get(key);
  if (!descriptor) return true;
  if (tabberViewSessionReady.has(key) && options.reselect !== true) return true;
  const pending = tabberViewSessionPromises.get(key);
  if (pending) return pending;
  const promise = (async () => {
    try {
      const payload = await apiFetchJson('/api/tabber-view-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session: descriptor.sourceSession, window: String(descriptor.windowIndex), window_id: descriptor.windowId}),
      });
      if (payload.session !== key) throw new Error('Tabber window identity changed');
      const currentDescriptor = tabberViewSessions.get(key);
      if (tabberViewSessionPromises.get(key) !== promise
        || currentDescriptor?.sourceSession !== descriptor.sourceSession
        || Number(currentDescriptor?.windowIndex) !== Number(descriptor.windowIndex)
        || currentDescriptor?.windowId !== descriptor.windowId
        || !isTmuxSession(currentDescriptor.sourceSession)) return false;
      tabberViewSessionReady.add(key);
      return true;
    } catch (error) {
      statusErr(esc(userMessageText(error, t('status.sessionCreateFailedDefault'))));
      return false;
    }
  })();
  tabberViewSessionPromises.set(key, promise);
  try {
    return await promise;
  } finally {
    if (tabberViewSessionPromises.get(key) === promise) tabberViewSessionPromises.delete(key);
  }
}

async function ensureTabberViewSessionForWindow(session, windowIndex, details = {}, options = {}) {
  const sourceSession = String(session || '').trim();
  const index = Number(windowIndex);
  const windowId = String(details.windowId || '').trim();
  if (!sourceSession || !windowId || !Number.isInteger(index) || index < 0) return '';
  const existingItem = tabberViewSessionForWindow(sourceSession, windowId);
  if (existingItem) {
    storeTabberViewSession(existingItem, {...tabberViewSessions.get(existingItem), ...details, sourceSession, windowIndex: index, windowId});
    return await ensureTabberViewSessionReady(existingItem, options) ? existingItem : '';
  }
  const pendingKey = `window:${sourceSession}\u001f${windowId}`;
  const pending = tabberViewSessionPromises.get(pendingKey);
  if (pending) return pending;
  const promise = (async () => {
    try {
      const payload = await apiFetchJson('/api/tabber-view-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session: sourceSession, window: String(index), window_id: windowId}),
      });
      // Retirement drops this owner; an older same-name source response must not revive its alias.
      if (tabberViewSessionPromises.get(pendingKey) !== promise || !isTmuxSession(sourceSession)) return '';
      const currentWindow = tabberWindowMetadataById(sourceSession, windowId);
      if (currentWindow === null) return '';
      const currentIndex = currentWindow ? Number(currentWindow.window_index) : index;
      if (!Number.isInteger(currentIndex) || currentIndex < 0) return '';
      const item = storeTabberViewSession(payload.session, {
        ...details,
        sourceSession,
        windowIndex: currentIndex,
        windowName: currentWindow?.window_name || details.windowName,
        windowId,
      });
      if (item) tabberViewSessionReady.add(item);
      return item;
    } catch (error) {
      statusErr(esc(userMessageText(error, t('status.sessionCreateFailedDefault'))));
      return '';
    }
  })();
  tabberViewSessionPromises.set(pendingKey, promise);
  try {
    return await promise;
  } finally {
    if (tabberViewSessionPromises.get(pendingKey) === promise) tabberViewSessionPromises.delete(pendingKey);
  }
}

function storeTabberNormalSelections(items, options = {}) {
  tabberNormalPointerPreviewState.pendingWindowActivation = null;
  tabberNormalPointerPreviewState.defaultSelectionPending = false;
  tabberNormalSelections = Array.from(new Set((items || []).map(value => String(value || '').trim()).filter(Boolean))).slice(-1);
  storageSet(tabberNormalSelectionsStorageKey, JSON.stringify(tabberNormalSelections));
  if (options.syncLayout !== false) {
    syncFileLayoutItems();
    if (tabberNormalPointerPreviewState.preview) restoreTabberNormalPointerPreview();
    else syncTabberNormalSelectionHighlight();
  }
  return tabberNormalSelections.slice();
}

function syncTabberNormalSelectionHighlight(items = tabberNormalSelections) {
  if (typeof document === 'undefined') return false;
  const panel = panelNodes.get(tabberItemId) || document.querySelector('#panel-__tabber__');
  if (!panel) return false;
  const selected = new Set(items || []);
  for (const row of panel.querySelectorAll('.file-tree-row[data-tabber-type="normal"]')) {
    row.classList.toggle('tabber-active-normal', selected.has(row.dataset.tabberPaneItem || ''));
  }
  return true;
}

function nextTabberNormalSelectionsFor(item, current = tabberNormalSelections) {
  const target = String(item || '').trim();
  if (!target) return Array.isArray(current) ? current.slice(-1) : [];
  const selected = Array.isArray(current)
    ? current.slice()
    : activePaneItems().filter(candidate => !isFileExplorerItem(candidate)).slice(-1);
  return selected.includes(target) ? [] : [target];
}

function restoreTabberNormalPointerPreview() {
  if (!tabberNormalPointerPreviewState.preview) return false;
  if (tabberNormalPointerPreviewState.frame) cancelAnimationFrame(tabberNormalPointerPreviewState.frame);
  tabberNormalPointerPreviewState.preview = null;
  tabberNormalPointerPreviewState.frame = 0;
  syncTabberNormalSelectionHighlight();
  return true;
}

function tabberNormalPaneActivationRow(event) {
  if (tabberIsAdvanced() || event?.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
      || event.target?.closest?.('.file-tree-icon')) return null;
  return event.target?.closest?.('.file-tree-row[data-tabber-type="normal"], .file-tree-row[data-tabber-type="tab"]') || null;
}

function previewTabberNormalPaneSelection(row, event) {
  if (tabberNormalPaneActivationRow(event) !== row) return false;
  const item = row?.dataset?.tabberPaneItem || row?.dataset?.tabberItem || '';
  if (!item || (!isLayoutItem(item) && !isTmuxSession(item))) return false;
  const preview = {item, selected: nextTabberNormalSelectionsFor(item)};
  tabberNormalPointerPreviewState.preview = preview;
  syncTabberNormalSelectionHighlight(preview.selected);
  return true;
}

function scheduleTabberNormalPointerPreviewRestore() {
  const preview = tabberNormalPointerPreviewState.preview;
  if (!preview || tabberNormalPointerPreviewState.frame) return false;
  tabberNormalPointerPreviewState.frame = requestAnimationFrame(() => {
    tabberNormalPointerPreviewState.frame = 0;
    if (tabberNormalPointerPreviewState.preview === preview) restoreTabberNormalPointerPreview();
  });
  return true;
}

function normalTabberContentLayout(selected, focusSession, sourceSlots) {
  if (tabberSidebarSinglePane) return layoutSlotsForItems([tabberItemId], 'single', {active: tabberItemId});
  const content = layoutSlotsForItems(selected, 'split', {
    active: focusSession, preferredSlots: ['right'],
  });
  if (!tabberSidebarVisible) return content;
  return layoutWithSidePaneItems(content, [tabberItemId], {
    side: paneSideLeft, forceCreate: true, active: tabberItemId,
    widthPercent: sidePaneWidthPercent(paneSideLeft, sourceSlots),
  });
}

function normalTabberPaneLayout(slots, options = {}) {
  if (tabberIsAdvanced() || tabberSidebarSinglePane || statsOnlyBootEnabled()) return slots;
  const source = cloneLayoutSlots(slots);
  source[layoutTreeKey] ||= legacyLayoutTree(source);
  const sideSlots = new Set(layoutSlotKeys(source).filter(slot => slotIsSidePane(slot, source)));
  const contentTree = layoutTreeWithoutSlots(source[layoutTreeKey], sideSlots);
  const items = paneItems(source).filter(item => item !== tabberItemId);
  const normalSidebar = sideSlots.size === 1 && Array.from(sideSlots).every(slot => (
    paneTabs(slot, source).length === 1 && activeItemForSide(slot, source) === tabberItemId
  ));
  const contentLeaves = layoutLeafSlots(contentTree);
  const selectedLeaves = items.length === 0
    ? contentLeaves.length <= 1
    : contentLeaves.length === 1 && Boolean(activeItemForSide(contentLeaves[0], source));
  if (normalSidebar && items.length <= 1 && selectedLeaves && layoutSlotKeys(source).every(slot => paneTabs(slot, source).length <= 1)) return slots;
  // Visibility is separate from open-item ownership. All discarded editors/diffs remain
  // registered and pooled, so their sidebar rows can reopen the same buffers and views.
  const selected = [options.focusSession, tabberNormalSelections?.[0], ...activePaneItems(source).reverse(), ...items]
    .find(item => item && items.includes(item));
  return normalTabberLayoutSlotsForSelections(selected ? [selected] : [], selected, source);
}

function normalTabberPaneHeaderHidden(slot, slots = layoutSlots) {
  return !tabberIsAdvanced() && !paneTabs(slot, slots).includes(tabberItemId);
}

function initialTabberLayoutSlots(sourceSlots, options = {}) {
  if (tabberIsAdvanced() || statsOnlyBootEnabled()) return sourceSlots;
  const explicitSurfaces = paneItems(sourceSlots).filter(item => item !== tabberItemId && !isTmuxSession(item));
  const chats = paneItems(sourceSlots).filter(isTmuxSession);
  const items = [...explicitSurfaces, ...chats];
  const requested = options.focusSession || explicitSurfaces.at(-1) || chats.at(-1) || '';
  const selected = requested && items.includes(requested) ? [requested] : [];
  storeTabberNormalSelections(selected, {syncLayout: false});
  const activeSurface = options.surfaceSelection || (options.preserveSurfaceSelection ? null : tabberItemId);
  const withTabber = layoutWithSidePaneItems(sourceSlots, [tabberItemId], {
    side: paneSideLeft, forceCreate: true, active: activeSurface,
  });
  const sidebar = sidePaneSlot(paneSideLeft, withTabber);
  if (sidebar && activeSurface) withTabber[sidebar] = paneStateWithTabsForSlot(sidebar, paneTabs(sidebar, withTabber), activeSurface, withTabber);
  return normalTabberLayoutSlotsForSelections(selected, selected.at(-1) || tabberItemId, withTabber);
}

function normalTabberLayoutSlotsForSelections(selected, focusSession, sourceSlots = layoutSlots) {
  const item = (selected || []).find(Boolean);
  return normalTabberContentLayout(item ? [item] : [], item || focusSession || tabberItemId, sourceSlots);
}

function adoptNormalTabberLayoutSelections(slots = layoutSlots) {
  if (tabberIsAdvanced() || tabberSidebarSinglePane || tabberNormalSelections === null) return;
  const items = activePaneItems(slots).filter(item => item !== tabberItemId);
  const next = [...tabberNormalSelections.filter(item => items.includes(item)), ...items.filter(item => !tabberNormalSelections.includes(item))];
  if (JSON.stringify(next) !== JSON.stringify(tabberNormalSelections)) storeTabberNormalSelections(next, {syncLayout: false});
}

async function applyTabberNormalSelections(active = '', revision = tabberModeRevision, activationRevision = tabberNormalPointerPreviewState.activationRevision) {
  if (tabberIsAdvanced() || revision !== tabberModeRevision) return false;
  if (activationRevision !== tabberNormalPointerPreviewState.activationRevision) return false;
  if (active && active !== tabberItemId) tabberSidebarSinglePane = false;
  installTabberSidebarResizeObserver();
  if (!itemInLayout(tabberItemId) && typeof openFileSurfacePane === 'function') await openFileSurfacePane(tabberItemId);
  if (tabberIsAdvanced() || revision !== tabberModeRevision || activationRevision !== tabberNormalPointerPreviewState.activationRevision) return false;
  const selected = (tabberNormalSelections || []).filter(isLayoutItem);
  const focusSession = selected.includes(active) ? active : selected[selected.length - 1] || tabberItemId;
  const next = normalTabberLayoutSlotsForSelections(selected, focusSession);
  applyLayoutSlots(next, {focusSession: tabberSidebarSinglePane ? tabberItemId : focusSession, prune: false, sessionButtons: false, preservePlaceholderSlots: true, tabberNormalSelection: true});
  if (!tabberSidebarSinglePane && focusSession !== tabberItemId) {
    const slot = slotForSession(focusSession);
    if (slot) activatePaneTab(slot, focusSession, {userInitiated: true});
  }
  syncTabberSidebarToggle();
  return true;
}

function tabberSidebarHostWidth() {
  const host = document.querySelector('.yomux-dockview') || document.querySelector('#grid');
  const hostWidth = Math.max(0, Number(host?.getBoundingClientRect?.().width) || 0);
  const viewportWidth = Math.max(0, Number(nativeViewport({widthOnly: true}).width) || 0);
  return hostWidth > 0 ? Math.min(hostWidth, viewportWidth) : viewportWidth;
}

function tabberSidebarMinimumWidth(selected = (tabberNormalSelections || []).filter(isLayoutItem), sourceSlots = layoutSlots) {
  const content = layoutSlotsForItems(selected, 'split', {active: selected.at(-1) || null, preferredSlots: ['right']});
  const withTabber = layoutWithSidePaneItems(content, [tabberItemId], {
    side: paneSideLeft, forceCreate: true, active: tabberItemId,
    widthPercent: sidePaneWidthPercent(paneSideLeft, sourceSlots),
  });
  return layoutNodeMinWidth(withTabber[layoutTreeKey], withTabber);
}

function tabberSidebarCanFit() {
  const width = tabberSidebarHostWidth();
  return width > 0 && width >= tabberSidebarMinimumWidth() + 8;
}

function syncTabberSidebarToggle() {
  installTabberSidebarResizeObserver();
  document.body?.classList.toggle('tabber-mode-normal', !tabberIsAdvanced());
  const button = document.querySelector('#topbarTabberToggle');
  if (!button) return false;
  const advanced = tabberIsAdvanced();
  button.hidden = advanced;
  button.setAttribute('aria-pressed', String(!advanced && (tabberSidebarSinglePane || tabberSidebarVisible && itemInLayout(tabberItemId))));
  button.title = t('tabber.title');
  button.setAttribute('aria-label', button.title);
  return true;
}

function applyTabberSidebarVisibility(visible, options = {}) {
  const next = Boolean(visible);
  if (next === tabberSidebarVisible) {
    syncTabberSidebarToggle();
    return false;
  }
  tabberSidebarVisible = next;
  if (next) tabberSidebarSinglePane = false;
  if (options.manual) {
    tabberSidebarRequestedVisible = next;
    tabberSidebarNarrowOverride = next && !tabberSidebarCanFit();
    storageSet(tabberSidebarPreferenceStorageKey, String(next));
  }
  if (!tabberIsAdvanced()) void applyTabberNormalSelections();
  syncTabberSidebarToggle();
  return true;
}

function toggleTabberSidebar() {
  if (tabberIsAdvanced()) return false;
  if (!tabberSidebarCanFit()) {
    tabberSidebarSinglePane = !tabberSidebarSinglePane;
    tabberSidebarVisible = false;
    tabberSidebarRequestedVisible = true;
    if (!tabberSidebarSinglePane) tabberSidebarNarrowOverride = false;
    storageSet(tabberSidebarPreferenceStorageKey, 'true');
    void applyTabberNormalSelections();
    syncTabberSidebarToggle();
    return true;
  }
  tabberSidebarSinglePane = false;
  return applyTabberSidebarVisibility(!tabberSidebarVisible, {manual: true});
}

function scheduleTabberSidebarViewportSync() {
  if (tabberSidebarResizeFrame) return;
  tabberSidebarResizeFrame = requestAnimationFrame(() => {
    tabberSidebarResizeFrame = 0;
    if (tabberIsAdvanced() || !tabberSidebarRequestedVisible) return;
    if (!tabberSidebarHostWidth()) return;
    const fits = tabberSidebarCanFit();
    if (fits) tabberSidebarSinglePane = false;
    if (tabberSidebarNarrowOverride && fits) tabberSidebarNarrowOverride = false;
    const shouldShow = fits || tabberSidebarNarrowOverride;
    applyTabberSidebarVisibility(shouldShow);
  });
}

function installTabberSidebarResizeObserver() {
  if (tabberSidebarResizeObserver) return;
  const host = document.querySelector('#grid') || document.querySelector('.yomux-dockview');
  if (host && typeof ResizeObserver === 'function') {
    tabberSidebarResizeObserver = new ResizeObserver(scheduleTabberSidebarViewportSync);
    tabberSidebarResizeObserver.observe(host);
  }
  window.addEventListener('resize', scheduleTabberSidebarViewportSync, {passive: true});
}

async function toggleTabberNormalPane(item, activationRevision = null) {
  const target = String(item || '').trim();
  if (tabberIsAdvanced() || !target || (!isLayoutItem(target) && !isTmuxSession(target))) return false;
  const nextActivationRevision = Number.isSafeInteger(activationRevision)
    ? activationRevision
    : ++tabberNormalPointerPreviewState.activationRevision;
  if (nextActivationRevision !== tabberNormalPointerPreviewState.activationRevision) return false;
  const revision = tabberModeRevision;
  storeTabberNormalSelections(nextTabberNormalSelectionsFor(target));
  return applyTabberNormalSelections(target, revision, nextActivationRevision);
}

function advancedTabberLayoutSlots(active = visualActivePaneItem(), sourceSlots = layoutSlots) {
  const recency = new Map();
  for (const row of buildTabberNormalRows()) {
    if (row.tabber?.type === 'normal') recency.set(row.tabber.session, Math.max(recency.get(row.tabber.session) || 0, Number(row.mtime || 0)));
  }
  const roster = [...new Set([...tabberOrderedSessions(), ...sessions])]
    .filter(session => isTmuxSession(session) && !tabberViewDescriptorForSession(session) && !session.startsWith('__yomux_view_'))
    .sort((a, b) => (recency.get(b) ?? tabberRecency(b)) - (recency.get(a) ?? tabberRecency(a)));
  // A selected window view keeps its existing terminal instead of reopening its source session.
  const source = tabberViewDescriptorForSession(active)?.sourceSession;
  const selected = source && roster.includes(source) ? roster.map(item => item === source ? active : item) : roster;
  const homeActive = [finderItemId, differItemId, tabberItemId].includes(active) ? active : finderItemId;
  const withHome = mode => {
    const next = layoutWithSidePaneItems(layoutSlotsForItems(selected, mode, {active, preferredSlots: ['right']}),
    [finderItemId, differItemId, tabberItemId], {
      side: paneSideLeft, forceCreate: true, active: homeActive,
      widthPercent: sidePaneWidthPercent(paneSideLeft, sourceSlots),
    });
    const home = sidePaneSlot(paneSideLeft, next);
    if (home) next[home] = paneStateWithTabsForSlot(home, [finderItemId, differItemId, tabberItemId], homeActive, next);
    return next;
  };
  const split = withHome('split');
  return selected.length >= 2 && tabberSidebarHostWidth() >= layoutNodeMinWidth(split[layoutTreeKey], split) + 8
    ? split : withHome('single');
}

function setTabberMode(value, options = {}) {
  const next = normalizeTabberMode(value);
  if (next === tabberMode) return false;
  tabberNormalPointerPreviewState.pendingWindowActivation = null;
  tabberNormalPointerPreviewState.defaultSelectionPending = false;
  tabberModeRevision += 1;
  tabberNormalPointerPreviewState.activationRevision += 1;
  if (next === 'normal' && tabberNormalSelections === null) {
    const focused = visualActivePaneItem();
    const candidates = activePaneItems().filter(item => !isFileExplorerItem(item));
    const ordered = candidates.filter(item => item !== focused);
    if (focused && candidates.includes(focused)) ordered.push(focused);
    storeTabberNormalSelections(ordered.slice(-1));
  }
  const active = visualActivePaneItem();
  tabberMode = next;
  if (next === 'normal' && tabberSidebarRequestedVisible) scheduleTabberSidebarViewportSync();
  dockviewLayoutState.api?.updateOptions?.({disableDnd: !tabDragAllowed()});
  storageSet(tabberModeStorageKey, next);
  syncEmptyPaneDropPromptVisibility();
  if (options.refresh !== false) {
    refreshTabberPanels();
    if (next === 'normal') void applyTabberNormalSelections();
    else applyLayoutSlots(advancedTabberLayoutSlots(active), {focusSession: active, prune: false, preservePlaceholderSlots: true});
    renderPaneTabStrips();
  }
  syncTabberSidebarToggle();
  return true;
}

function tabberIsAdvanced() {
  return tabberMode === 'advanced';
}

function defaultNormalTabberLayoutSlots() {
  let selected = (tabberNormalSelections || []).filter(isLayoutItem);
  if (tabberNormalSelections === null) {
    tabberNormalPointerPreviewState.defaultSelectionPending = true;
    const mostRecent = tabberMostRecentSession() || visibleSessions[0] || '';
    selected = mostRecent && isLayoutItem(mostRecent) ? [mostRecent] : [];
  } else if (selected.length !== tabberNormalSelections.length) {
    storeTabberNormalSelections(selected);
  }
  const content = layoutSlotsForItems(selected, 'split', {active: selected.at(-1) || null});
  return layoutWithSidePaneItems(content, [tabberItemId], {
    side: paneSideLeft,
    forceCreate: true,
    active: tabberItemId,
  });
}

function tabberMostRecentSession() {
  if (!tabberNormalPointerPreviewState.activityReady) return '';
  const ordered = sortedFileTreeEntries(buildTabberNormalRows(), 'newest', {includeHidden: true});
  const mostRecent = ordered.find(entry => entry?.tabber?.type === 'normal' && Number(entry.mtime || 0) > 0);
  return mostRecent?.tabber?.session || '';
}

function initializePendingDefaultTabberSelection() {
  if (!tabberNormalPointerPreviewState.defaultSelectionPending || tabberIsAdvanced() || tabberNormalSelections !== null) return false;
  const rows = sortedFileTreeEntries(buildTabberNormalRows(), 'newest', {includeHidden: true, tabberNormalOrder: true});
  const newest = rows.find(row => row.tabber?.type === 'normal');
  if (newest?.tabber?.normalWindowView) {
    const revision = ++tabberNormalPointerPreviewState.activationRevision;
    tabberNormalPointerPreviewState.defaultSelectionPending = false;
    void (async () => {
      const data = newest.tabber;
      const item = await ensureTabberViewSessionForWindow(data.session, data.windowIndex, {label: data.label, agentKey: data.agentKey, windowId: data.windowId});
      if (!item || tabberIsAdvanced() || revision !== tabberNormalPointerPreviewState.activationRevision || tabberNormalSelections !== null) return;
      storeTabberNormalSelections([item]);
      await applyTabberNormalSelections(item, tabberModeRevision, revision);
    })();
    return true;
  }
  const mostRecent = newest?.tabber?.session || tabberMostRecentSession() || visibleSessions[0] || '';
  if (!mostRecent || !isLayoutItem(mostRecent)) return false;
  storeTabberNormalSelections([mostRecent]);
  void applyTabberNormalSelections(mostRecent);
  return true;
}

function claimPendingDefaultTabberSelection() {
  if (!tabberNormalPointerPreviewState.defaultSelectionPending || tabberNormalSelections !== null) return false;
  const current = tabberMostRecentSession() || visibleSessions[0] || '';
  storeTabberNormalSelections(current && isLayoutItem(current) ? [current] : []);
  return true;
}

function tabberPathToken(value) {
  return readableParamComponent(value);
}

const tabberPendingInput = new Map();
const tabberItemActivityStorageKey = 'yomux-tabber-item-activity';
const tabberStoredItemActivity = safeJsonParse(sessionStorageGet(tabberItemActivityStorageKey), []);
const tabberLocalItemActivity = new Map((Array.isArray(tabberStoredItemActivity) ? tabberStoredItemActivity : [])
  .filter(entry => Array.isArray(entry) && entry.length === 2 && typeof entry[0] === 'string'
    && /^(file|gitdiff):./.test(entry[0]) && Number.isFinite(entry[1]) && entry[1] > 0).slice(-256));
const tabberItemActivityTouchSentAt = new Map();

function sendTabberItemActivityTouch(item) {
  const key = String(item || '');
  if (!key || typeof apiFetchJson !== 'function' || !/^go(?:\s|$)/i.test(String(bootstrap?.backendLabel || bootstrap?.backend || '').trim())) return false;
  const now = Date.now();
  const lastSentAt = Number(tabberItemActivityTouchSentAt.get(key) || 0);
  if (lastSentAt > 0 && now - lastSentAt < 5000) return false;
  tabberItemActivityTouchSentAt.set(key, now);
  while (tabberItemActivityTouchSentAt.size > 256) tabberItemActivityTouchSentAt.delete(tabberItemActivityTouchSentAt.keys().next().value);
  void apiFetchJson('/api/activity/touch', {method: 'POST', body: JSON.stringify({item: key}), startupImmediate: true}).catch(() => {
    if (tabberItemActivityTouchSentAt.get(key) === now) tabberItemActivityTouchSentAt.delete(key);
  });
  return true;
}

function noteTabberItemActivity(item) {
  const activityItem = String(item || '');
  if (!activityItem || (!isFileEditorItem(activityItem) && !gitDiffItemPath(activityItem))) return false;
  tabberLocalItemActivity.set(activityItem, Date.now() / 1000);
  while (tabberLocalItemActivity.size > 256) tabberLocalItemActivity.delete(tabberLocalItemActivity.keys().next().value);
  // Retain the latest local edit even when the bounded server touch is throttled or still in flight.
  sessionStorageSet(tabberItemActivityStorageKey, JSON.stringify([...tabberLocalItemActivity]));
  sendTabberItemActivityTouch(activityItem);
  if (itemInLayout(tabberItemId)) refreshTabberPanels();
  return true;
}

// Keep an immediate input clock until the server acknowledges it. An HTTP snapshot
// already in flight when the user types must not roll the displayed date back.
function tabberRecency(key) {
  const rec = tabberActivityPayload?.activity?.[key];
  const pending = tabberPendingInput.get(key);
  if (pending && (Date.now() > pending.expiresAt || pending.acknowledged)) {
    tabberPendingInput.delete(key);
  }
  const localTs = tabberPendingInput.get(key)?.timestamp || 0;
  if (!rec) return localTs;
  const derived = Number(rec.active_recency_ts);
  if (rec.active_recency_ts !== null && rec.active_recency_ts !== undefined && Number.isFinite(derived) && derived >= 0) return Math.max(localTs, derived);
  return Math.max(localTs, Number(rec.last_user_input_ts || 0), Number(rec.last_agent_active_ts || 0), Number(rec.last_output_ts || 0));
}

function noteTabberTerminalInput(item) {
  const view = tabberViewDescriptorForSession(item);
  const session = view?.sourceSession || item;
  const info = transcriptMetadataState.payload.sessions?.[session];
  const windows = tmuxWindowRecords(info?.panes);
  const active = windows.filter(record => tmuxWindowRecordIsActive(session, record));
  const windowIndex = view ? view.windowIndex : active.length === 1 ? active[0].index : null;
  const timestamp = Date.now() / 1000;
  const keys = [session];
  if (windowIndex !== null && windowIndex >= 0) keys.push(`${session}:${windowIndex}`);
  for (const [key, pending] of tabberPendingInput) {
    if (Date.now() > pending.expiresAt) tabberPendingInput.delete(key);
  }
  for (const key of keys) tabberPendingInput.set(key, {timestamp, expiresAt: Date.now() + 10_000,
    minGeneration: tabberActivityState.requestGeneration + 1});
  // Transport delivery stays ahead of row rendering on the key/paste path.
  scheduleTabberTreeLayoutStateSync({refreshTree: true});
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

// Window rows have one semantic recency clock: this exact timestamp feeds row mtime,
// visible date text, and parent session bubbling.
function tabberWindowRecency(row, nowSeconds = Date.now() / 1000) {
  const isAgent = row?.isAgent === true || ['claude', 'codex', 'opencode'].includes(String(row?.agentKey || '').toLowerCase());
  if (isAgent) {
    const workingTs = agentWindowWorkingRecencyTs(row?.agentStatus, nowSeconds);
    const activityTs = tabberAgentRecency(row?.agentActivity);
    const statusTs = Number(row?.agentStatus?.idle_since || row?.agentStatus?.last_active_ts || 0);
    const stoppedTs = Number(row?.agentStatus?.working_stopped_ts || 0);
    const session = String(row?.session || '').trim();
    const windowIndex = row?.windowIndex ?? row?.record?.index;
    const windowActivityTs = session && windowIndex !== null && windowIndex !== undefined && windowIndex !== ''
      ? tabberRecency(`${session}:${windowIndex}`)
      : 0;
    const idleStatusTs = String(row?.agentStatus?.state || STATE_KEY.idle) === STATE_KEY.idle
      && Number.isFinite(statusTs) && statusTs > 0 ? statusTs : 0;
    return Math.max(workingTs, activityTs, idleStatusTs, Number.isFinite(stoppedTs) ? stoppedTs : 0, windowActivityTs);
  }
  const session = String(row?.session || '').trim();
  const windowIndex = row?.windowIndex ?? row?.record?.index;
  if (!session || windowIndex === null || windowIndex === undefined || windowIndex === '') return 0;
  return tabberRecency(`${session}:${windowIndex}`);
}

function tabberWindowDateDisplay(recencyTs, agentStatus = null, nowSeconds = Date.now() / 1000) {
  if (fileExplorerTreeDateModeForView('tabber') === 'none') return {text: '', html: ''};
  const state = agentWindowStateKey(agentStatus?.state || STATE_KEY.idle);
  const text = sessionFileDisplayTimeText(recencyTs, {nowSeconds, view: 'tabber'});
  if (!text) return {text: '', html: ''};
  if (agentWindowIsAttentionState(state)) {
    return {text: '', html: statusIndicatorLabelHtml(text, state === STATE_KEY.blocked ? 'blocked' : 'attention', 'tabber-agent-status', 'agent-status-attention')};
  }
  return {text, html: ''};
}

function tabberActivityVisibleConsumer() {
  // Legacy single-surface layouts set `fileExplorerMode`; Dockview owns three
  // independent surfaces, where the active Tabber tab is the visibility truth.
  return itemInLayout(tabberItemId) && (fileExplorerMode === 'tabber' || itemIsActivePaneTab(tabberItemId)) && document.visibilityState !== 'hidden';
}

function tabberActivityPayloadHasUsefulData(payload) {
  if (!payload || typeof payload !== 'object') return false;
  if (Object.keys(payload.activity && typeof payload.activity === 'object' ? payload.activity : {}).length) return true;
  if (Array.isArray(payload.agents) && payload.agents.length) return true;
  return Object.keys(payload.agent_windows && typeof payload.agent_windows === 'object' ? payload.agent_windows : {}).length > 0;
}

function tabberActivityPayloadIsRefreshPlaceholder(payload) {
  return payload?.cache?.refreshing === true && !tabberActivityPayloadHasUsefulData(payload);
}

function mergeTabberActivitySessionMetadata(payload) {
  const sessionPayloads = payload?.sessions && typeof payload.sessions === 'object' ? payload.sessions : null;
  const order = Array.isArray(payload?.session_order) ? payload.session_order : null;
  if (!sessionPayloads && !order) return false;
  const prior = transcriptMetadataState.payload && typeof transcriptMetadataState.payload === 'object'
    ? transcriptMetadataState.payload
    : {};
  const priorSessions = prior.sessions && typeof prior.sessions === 'object' ? prior.sessions : {};
  // A session order supplied by the global tmux inventory is authoritative.  Do not retain a
  // disappeared session in the metadata map, because tabberOrderedSessions() also considers map
  // keys that are not present in the current order.
  const sessions = order
    ? Object.fromEntries(order.filter(session => priorSessions[session] && typeof priorSessions[session] === 'object').map(session => [session, priorSessions[session]]))
    : {...priorSessions};
  for (const [session, info] of Object.entries(sessionPayloads || {})) {
    if (!info || typeof info !== 'object') continue;
    // When the backend supplies an authoritative global roster, metadata keys outside that
    // roster are stale cross-route enrichments and must not create a synthetic Tabber row.
    if (order && !order.includes(session)) continue;
    sessions[session] = {...(sessions[session] || {}), ...info};
  }
  const nextOrder = order || Object.keys(sessions);
  setTranscriptMetadataPayload({...prior, sessions, session_order: nextOrder}, {invalidateRequest: false});
  const changed = updateSessionList(nextOrder);
  if (changed) {
    renderSessionButtons();
    if (typeof refreshOpenTabsMenuRows === 'function') refreshOpenTabsMenuRows();
  }
  return changed;
}

function applyTabberActivityPayload(payload, requestGeneration = 0) {
  if (!payload || typeof payload !== 'object') return false;
  const payloadTopologyGeneration = Number(payload.topology_generation || 0);
  const currentTopologyGeneration = Math.max(Number(tmuxTopologyGeneration || 0), Number(transcriptMetadataState.rosterGeneration || 0));
  if (!tmuxTopologyPayloadIsCurrent(payload)) return false;
  const hasActivity = payload.activity && typeof payload.activity === 'object';
  const hasTopology = Array.isArray(payload.session_order)
    || (payload.sessions && typeof payload.sessions === 'object');
  if (!hasActivity && !hasTopology) return false;
  // Do not advance the shared roster clock from a response that has no usable activity or
  // topology. Such a placeholder must not poison the generation guard for the next valid read.
  if (Number.isSafeInteger(payloadTopologyGeneration) && payloadTopologyGeneration >= currentTopologyGeneration
      && typeof adoptTopologyGeneration === 'function') adoptTopologyGeneration(payload);
  if (Number.isSafeInteger(payloadTopologyGeneration) && payloadTopologyGeneration > transcriptMetadataState.rosterGeneration) {
    transcriptMetadataState.rosterGeneration = payloadTopologyGeneration;
  }
  const hasVersionedTopology = Object.prototype.hasOwnProperty.call(payload, 'topology_generation')
    && Number.isSafeInteger(payloadTopologyGeneration)
    && payloadTopologyGeneration > 0;
  // Python's legacy activity/roster responses predate topology generations, so they remain
  // valid on a cold boot. Once a versioned Go metadata/activity response has established a
  // roster, an unversioned response is only safe if it contains every known session. Otherwise
  // it is an older cross-route read and must not make a live Tabber row disappear.
  if (!hasVersionedTopology && currentTopologyGeneration > 0) {
    const currentOrder = Array.isArray(transcriptMetadataState.payload.session_order)
      ? transcriptMetadataState.payload.session_order.map(session => String(session))
      : [];
    const incomingOrder = Array.isArray(payload.session_order)
      ? payload.session_order.map(session => String(session))
      : Object.keys(payload.sessions && typeof payload.sessions === 'object' ? payload.sessions : {});
    const incomingSessions = new Set(incomingOrder);
    if (currentOrder.some(session => !incomingSessions.has(session))) return false;
  }
  // Session metadata is a valid topology-only repair when the activity product is unavailable.
  // Keep the shared payload shape explicit so renderers never interpret missing activity as a
  // useful empty snapshot.
  if (!hasActivity) payload = {...payload, activity: {}};
  // Generation zero denotes a direct/pushed snapshot rather than an HTTP completion. Give it a new
  // generation so any request started before that snapshot cannot roll the cache back afterward.
  const generation = Number(requestGeneration) || ++tabberActivityState.requestGeneration;
  if (generation > 0 && generation < tabberActivityState.appliedGeneration) return false;
  const refreshPlaceholder = tabberActivityPayloadIsRefreshPlaceholder(payload);
  if (refreshPlaceholder && tabberActivityPayloadHasUsefulData(tabberActivityPayload)) return false;
  if (typeof applyTmuxRosterRenames === 'function') applyTmuxRosterRenames(payload.renames, payload.session_order);
  mergeTabberActivitySessionMetadata(payload);
  tabberActivityPayload = payload;
  for (const [key, pending] of tabberPendingInput) {
    pending.acknowledged = generation >= pending.minGeneration
      && Number(payload.activity?.[key]?.last_user_input_ts || 0) >= pending.timestamp;
  }
  tabberActivityState.loaded = true;
  if (hasActivity && !refreshPlaceholder) tabberNormalPointerPreviewState.activityReady = true;
  // A refresh placeholder is not an authoritative snapshot. Let an older in-flight full response
  // replace it, while an accepted full response prevents older requests from rolling data back.
  if (!refreshPlaceholder && generation > tabberActivityState.appliedGeneration) {
    tabberActivityState.appliedGeneration = generation;
  }
  if (hasActivity && !refreshPlaceholder) initializePendingDefaultTabberSelection();
  return true;
}

async function fetchTabberActivity(options = {}) {
  const visible = options.visible !== undefined ? Boolean(options.visible) : tabberActivityVisibleConsumer();
  if (!visible && options.allowHidden !== true) return false;
  if (tabberActivityState.request) return tabberActivityState.request;
  const requestHours = normalizeSessionFileLookbackHours(tabberSessionFileLookbackHours);
  const rangeIsCurrent = () => requestHours === normalizeSessionFileLookbackHours(tabberSessionFileLookbackHours);
  const requestGeneration = ++tabberActivityState.requestGeneration;
  const request = (async () => {
    let adoptedStatus = 0;
    let accepted = false;
    try {
      const params = new URLSearchParams();
      params.set('hours', String(requestHours));
      params.set('visible', visible ? '1' : '0');
      // Tabber is the machine-wide tmux view. Python's activity API only includes
      // that roster when the caller opts into the global scope; keep the Go and
      // Python backends on the same one-session-per-row contract.
      params.set('scope', 'all');
      const payload = await apiFetchJson(`/api/activity?${params.toString()}`, {cache: 'no-store'});
      const applied = rangeIsCurrent() && applyTabberActivityPayload(payload, requestGeneration);
      accepted = applied;
      // Go owns live agent status in the activity product.  Keep the shared
      // auto-status model in step with every accepted activity refresh, not
      // only the initial stats-only boot and explicit status repair path; the
      // Tabber and pane-tab renderers read that shared model for the green
      // working marker.
      if (applied
          && typeof goAgentStatusUsesActivityFallback === 'function'
          && goAgentStatusUsesActivityFallback()
          && typeof adoptTabberActivityAsAutoStatusFallback === 'function') {
        adoptedStatus = adoptTabberActivityAsAutoStatusFallback();
      }
    } catch (_) {
      // The activity product is optional for topology. Python's session-metadata path still owns
      // the live roster, so use it as a topology-only repair instead of hiding every tmux session
      // when activity collection is temporarily unavailable.
      try {
        const metadata = await apiFetchJson('/api/session-metadata', {cache: 'no-store'});
        if (rangeIsCurrent()) accepted = applyTabberActivityPayload({...metadata, activity: {}}, requestGeneration);
      } catch (_) {
        // Older Go/compatibility servers may expose the lighter roster through /api/tmux even
        // when their full metadata build is unavailable.  It carries the same authoritative
        // session_order, so preserve one Tabber row per live session instead of clearing the tree.
        try {
          const roster = await apiFetchJson('/api/tmux', {cache: 'no-store'});
          if (rangeIsCurrent()) accepted = applyTabberActivityPayload({...roster, activity: {}}, requestGeneration);
        } catch (_) {
          // Keep the last snapshot; the interval retries without a render-triggered request loop.
        }
      }
    } finally {
      if (tabberActivityState.request === request) {
        tabberActivityState.loaded = true;
        tabberActivityState.request = null;
      }
    }
    if (!rangeIsCurrent()) return fetchTabberActivity(options);
    if (accepted) {
      // Compare the accepted product, including activity and roster changes independent of status.
      const payload = tabberActivityPayload;
      const signature = tabberActivityPublicationSignature(payload);
      const publish = signature !== tabberActivityState.publishedSignature;
      const refreshOptions = {producer: 'activity-response', requestGeneration,
        revision: payload.agent_window_snapshot_revision, refreshTabber: publish};
      if (adoptedStatus > 0) refreshAgentWindowActivityDisplays(sessions, refreshOptions);
      else if (publish) refreshTabberPanelsForAgentWindowPublication(refreshOptions);
      tabberActivityState.publishedSignature = signature;
    }
    return true;
  })();
  tabberActivityState.request = request;
  return request;
}

function warmTabberDataOnLaunch() {
  if (tabberLaunchWarmupStarted || !transcriptMetadataState.loaded || !tabberActivityVisibleConsumer()) return false;
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
    if (options.refresh !== false) {
      fetchTabberActivity();
      if (itemInLayout(tabberItemId)) refreshTabberPanels();
    }
  }
  return tabberSessionFileLookbackHours;
}

const tabberTreeSelectedPaths = new Set();
let tabberTreeSelectionLead = '';

// The authoritative tmux roster remains visible before window metadata arrives.
function tabberOrderedSessions() {
  const order = Array.isArray(transcriptMetadataState.payload.session_order) ? transcriptMetadataState.payload.session_order : [];
  const all = transcriptMetadataState.payload.sessions && typeof transcriptMetadataState.payload.sessions === 'object' ? Object.keys(transcriptMetadataState.payload.sessions) : [];
  const seen = new Set();
  const ordered = [];
  // `session_order` is the backend's authoritative global tmux roster.  It can arrive before the
  // older top-level `sessions` list has converged (notably on a Go stats-only boot), so do not
  // discard a valid roster entry merely because `isTmuxSession()` still reflects that old list.
  // When no roster order exists, retain the legacy guard for metadata-only callers.
  const hasAuthoritativeRoster = order.length > 0;
  for (const session of [...order, ...all]) {
    if (seen.has(session)) continue;
    seen.add(session);
    if (!hasAuthoritativeRoster && !isTmuxSession(session)) continue;
    ordered.push(session);
  }
  return ordered;
}

function tabberWindowIsAgent(name) {
  const key = tmuxWindowAgentKey(name);
  return typeof agentWindowKind === 'function' ? Boolean(agentWindowKind(key)) : key === 'claude' || key === 'codex' || key === 'opencode';
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

function buildTabberNormalRows() {
  const rows = [];
  const advanced = tabberIsAdvanced();
  const visibleItems = new Set(activePaneItems());
  const focusedItem = visualActivePaneItem();
  const nowSeconds = fileTreeRecencyNowMs() / 1000;
  for (const session of tabberOrderedSessions()) {
    const info = transcriptMetadataState.payload.sessions?.[session] || {};
    const windows = tmuxWindowRecords(info.panes).map(record => {
      const {status: agentStatus, agentKey} = tmuxWindowAgentStatus(session, record, info);
      const isAgent = Boolean(agentKey) || tabberWindowIsAgent(record.name);
      const agentActivity = isAgent ? tabberAgentForWindow(session, record.index, agentKey) : null;
      const windowMtime = tabberWindowRecency({session, windowIndex: record.index, record, isAgent, agentKey, agentActivity, agentStatus}, nowSeconds);
      const active = tmuxWindowRecordIsActive(session, record);
      const label = tmuxWindowCanonicalLabel(session, record, record.indexedButtonLabel || `${record.indexText}:${record.buttonNameLabel || record.name}`, info);
      const agentStatusForDisplay = agentStatus ? {...agentStatus, current: active, window_active: active} : null;
      const agentStatusForIcon = agentStatusForDisplay || (active && ['claude', 'codex', 'opencode'].includes(agentKey)
        ? {kind: agentKey, state: 'idle', window: record.indexText, window_index: record.index, current: true, window_active: true}
        : agentStatus);
      // Keep the Tabber window marker on the shared live-status pulse path.  A working Codex/Claude
      // pane must remain visibly green while the tree is open; disabling animation here made the
      // status look idle even though the backend had classified the pane as working.
      const activityIconHtml = agentWindowActivityIconHtmlForStatus(agentStatusForIcon, agentKey, session);
      const dateDisplay = tabberWindowDateDisplay(windowMtime, agentStatusForDisplay, nowSeconds);
      return {
        name: tabberWindowPath(session, record.index).slice(1), kind: 'file', mtime: windowMtime,
        sortName: label,
        tabber: {type: 'window', session, windowIndex: record.index, windowId: record.windowId, label, pid: record.pid, icon: '', active, current: focusedItem === session && active, agentKey, agentStatus: agentStatusForIcon, activityIconHtml, dateText: dateDisplay.text, dateHtml: dateDisplay.html},
      };
    });
    if (!windows.length) {
      rows.push({
        name: tabberSessionPath(session).slice(1), kind: 'file', mtime: tabberRecency(session), sortName: sessionLabel(session) || session,
        tabber: {type: 'normal', session, label: sessionLabel(session) || session, normalPaneItem: session,
          active: visibleItems.has(session), current: focusedItem === session,
          normalNameHtml: tabberNormalRowNameHtml(session, '', '')},
      });
      continue;
    }
    const windowRecencies = windows.map(window => Number(window?.mtime || 0));
    const hasDistinctWindowActivity = windowRecencies.every(timestamp => Number.isFinite(timestamp) && timestamp > 0)
      && new Set(windowRecencies).size === windowRecencies.length;
    const normalSortMtime = windows.length > 1 && !hasDistinctWindowActivity
      ? Math.max(...windowRecencies)
      : null;
    for (const window of windows) {
      const windowData = window?.tabber || {};
      if (windowData.type !== 'window') continue;
      const sessionName = sessionLabel(session) || session;
      const icon = tabberRowActivityHtml(windowData);
      const description = tabberWindowDescription(session, windowData.windowIndex);
      const visibleSession = windows.length === 1 ? sessionName : `${sessionName} `;
      const windowIdentity = windows.length > 1 ? String(windowData.label || '') : '';
      const path = tabberWindowPath(session, windowData.windowIndex);
      const windowView = windows.length > 1;
      const viewItem = windowView ? tabberViewSessionForWindow(session, windowData.windowId) : '';
      const visiblePaneItems = activePaneItems();
      const normalPaneItem = viewItem && visiblePaneItems.includes(viewItem) ? viewItem : (windowView
        ? windowData.active === true && visiblePaneItems.includes(session) ? session : viewItem
        : session);
      rows.push({
        ...window,
        name: path.slice(1),
        kind: 'file',
        sortName: `${visibleSession} ${windowIdentity} ${description}`,
        tabber: {
          ...windowData,
          type: 'normal',
          ...(normalSortMtime === null ? {} : {normalSortMtime}),
          normalSuppressMtimeDate: normalSortMtime !== null && !windowData.dateText && !windowData.dateHtml && !window.mtime,
          normalPaneItem,
          normalWindowView: windowView,
          label: `${visibleSession} ${description}`.trim(),
          normalNameHtml: tabberNormalRowNameHtml(session, icon, description, windowIdentity),
        },
      });
    }
  }
  for (const item of allTabItems()) {
    if (isTmuxSession(item) || item === tabberItemId || (advanced && isFileExplorerItem(item))) continue;
    if (!advanced && [differItemId, chatItemId, yoagentItemId].includes(item)) continue;
    const label = itemLabel(item);
    rows.push({
      name: tabberTabPath(item).slice(1), kind: 'file', mtime: Math.max(Number(tabberLocalItemActivity.get(item) || 0), tabberRecency(item)), sortName: label,
      tabber: {type: 'tab', item, label, icon: '◷', active: visibleItems.has(item), current: focusedItem === item,
        normalNameHtml: `<span class="tabber-normal-primary">${esc(label)}</span><span class="tabber-normal-description">&nbsp;</span>`},
    });
  }
  return rows;
}

function tabberSortMode() {
  return fileExplorerTreeSortModeForView('tabber');
}

function renderTabberTree(groupsEl, options = {}) {
  if (!groupsEl) return;
  if (!tabberActivityState.loaded && !tabberActivityState.request) fetchTabberActivity();
  const rows = buildTabberNormalRows();
  if (!rows.length) {
    groupsEl.innerHTML = `<div class="changes-empty">${esc(t('tabber.empty'))}</div>`;
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
  const advanced = tabberIsAdvanced();
  groupsEl.dataset.tabberMode = advanced ? 'advanced' : 'normal';
  const sortMode = tabberSortMode();
  const previousSort = container.__tabberSortState;
  const nowMs = fileTreeRecencyNowMs();
  const activityReady = tabberActivityState.loaded;
  const explicitSort = previousSort && previousSort.mode !== sortMode;
  const initialActivity = activityReady && previousSort && !previousSort.activityReady;
  const sortDue = !previousSort || explicitSort || initialActivity || sortMode !== 'newest'
    || nowMs - previousSort.sortedAt >= 10_000;
  const interactionBlocked = Boolean(previousSort) && (options.preserveDisplayedOrder === true
    || (options.reorder !== true && groupsEl.closest('.file-explorer-panel')?.__tabberPointerInside === true));
  const preserveDisplayedOrder = !explicitSort && (!sortDue || interactionBlocked);
  // Dates and status keep consuming fresh activity even when the row positions are retained.
  // Hover defers a due sort without consuming its interval; leaving can apply the latest data.
  if (!preserveDisplayedOrder) container.__tabberSortState = {mode: sortMode, sortedAt: nowMs, activityReady};
  const signature = JSON.stringify([tabberSortMode(), preserveDisplayedOrder, rows.map(entry => {
    const model = tabberTreeRowViewModel(null, `/${entry.name}`, entry, 0, {view: 'tabber'});
    return [model.path, model.kind, model.icon, model.label, model.paddingLeft, model.dataset, model.content, entry.mtime, entry.tabber?.normalSortMtime];
  })]);
  // Metadata generations can advance without changing a conversation's display. Selection and
  // recency still converge below, while unchanged rows keep their DOM and animation owners.
  if (signature !== null && container.__tabberNormalDisplaySignature === signature) {
    syncTabberTreeLayoutState(container, {force: true});
    const byName = new Map(rows.map(entry => [entry.name, entry]));
    for (const row of container.querySelectorAll('.file-tree-row[data-tabber-type]')) {
      const entry = byName.get(row.dataset.name);
      if (entry) applyFileTreeRowRecency(row, entry, {view: 'tabber'});
    }
    return;
  }
  renderTreeChildren(container, '/', rows, 0, {
    mode: 'tabber',
    collapsedSet: new Set(),
    entriesByDir: new Map(),
    treeSortMode: tabberSortMode(),
    preserveDisplayedOrder,
    tabberNormalOrder: !advanced,
    view: 'tabber',
    includeHidden: true,
  });
  container.__tabberNormalDisplaySignature = signature;
  syncTabberTreeLayoutState(container, {force: true});
}

function tabberWindowDescription(session, windowIndex) {
  const info = transcriptMetadataState.payload.sessions?.[session];
  return info?.window_metadata?.find(window => Number(window.window_index) === Number(windowIndex))?.description || '';
}

async function renameTabberDescription(session, windowIndex, windowId, description) {
  try {
    const payload = await apiFetchJson('/api/tabber-description', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session, window: String(windowIndex), window_id: String(windowId || ''), description}),
    });
    const pending = noteSessionMetadataPendingIdentity(payload);
    if (pending?.epoch === transcriptMetadataState.epoch) {
      transcriptMetadataState.mutationGeneration = Math.max(transcriptMetadataState.mutationGeneration, pending.generation);
    }
    const info = transcriptMetadataState.payload.sessions?.[session];
    const window = info?.window_metadata?.find(item => item.window_id === payload.window_id);
    if (window) { window.description = payload.description; window.description_manual = true; }
    refreshTabberPanels();
    closeSessionRenameDialog();
    return true;
  } catch (error) {
    return {error: userMessageText(error, 'Could not rename description')};
  }
}

function tabberNormalRowNameHtml(session, leadingHtml, description, windowIdentity = '') {
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  const decorations = sessionTabStatusDecorations(session, info, auto, {state, toggle: false});
  const title = tmuxPaneTabHtml(session, info, state, auto, {
    leadingHtml: `${leadingHtml || ''}${decorations.stateMarker}${decorations.yoloHtml}`,
    showState: true, showBadges: true, showDetail: false,
    windowIdentityHtml: windowIdentity ? `<span class="tabber-normal-window-identity">${esc(windowIdentity)}</span>` : '',
  });
  return tabberRowNameHtml(stripTitleAttrs(title), description);
}

function tabberRowNameHtml(titleHtml, description) {
  return `<span class="tabber-normal-primary">${titleHtml}</span><span class="tabber-normal-description">${description ? esc(description) : '&nbsp;'}</span>`;
}

function tabberRowActivityHtml(data) {
  return data.agentKey
    ? agentWindowActivityIconHtmlForStatus(data.agentStatus, data.agentKey, data.session, {statusBeforeAgent: true})
      || agentIcon(data.agentKey, {label: agentLabel(data.agentKey)})
    : '';
}

function scheduleDeferredTabberRefresh(options = {}) {
  if (tabberRefreshDeferredTimer) clearTimeout(tabberRefreshDeferredTimer);
  const delay = Math.max(Number(popoverShowDelayMs) || 0, Number(popoverHideDelayMs) || 0, 160);
  tabberRefreshDeferredTimer = setTimeout(() => {
    tabberRefreshDeferredTimer = null;
    if (itemInLayout(tabberItemId)) refreshTabberPanels(options);
  }, delay);
  return tabberRefreshDeferredTimer;
}

function refreshTabberPanels(options = {}) {
  const perf = clientPerfStart('tabberFullRefresh');
  if (tabberRefreshDeferredTimer) {
    clearTimeout(tabberRefreshDeferredTimer);
    tabberRefreshDeferredTimer = null;
  }
  let panels = 0;
  for (const panel of document.querySelectorAll('.file-explorer-panel[data-file-explorer-view="tabber"]')) {
    const changes = panel.querySelector('[data-file-explorer-changes]');
    if (changes) {
      const staticHtml = fileExplorerChangesPanelStaticHtml({view: 'tabber'});
      if (changes.__changesStaticHtml !== staticHtml) replaceChangesStaticHtml(changes, staticHtml);
    }
    const groups = changes?.querySelector('.changes-groups');
    if (groups) {
      panels += 1;
      renderTabberTree(groups, options);
    }
  }
  clientPerfEnd(perf, {nodes: panels});
}

function refreshTabberPanelsForFocusChange() {
  if (!itemInLayout(tabberItemId)) return;
  scheduleTabberTreeLayoutStateSync();
}

function tabberDragPreviewRows(row) {
  if (!row) return [];
  const path = String(row.dataset?.path || '');
  const parent = row.parentElement;
  const candidates = Array.from(parent?.querySelectorAll?.('.file-tree-row[data-tabber-type]') || [row]);
  if (!path) return [row];
  const childPrefix = `${path.replace(/\/+$/, '')}/`;
  return candidates.filter(candidate => {
    if (candidate === row) return true;
    const candidatePath = String(candidate.dataset?.path || '');
    return candidatePath.startsWith(childPrefix);
  });
}

function cloneTabberDragPreviewRow(row) {
  const clone = row.cloneNode?.(true) || row;
  clone.id = '';
  clone.className = row.className || clone.className || '';
  clone.draggable = false;
  clone.removeAttribute?.('title');
  clone.removeAttribute?.('draggable');
  clone.querySelectorAll?.('.session-popover, [role="tooltip"]').forEach(node => node.remove?.());
  const paddingLeft = row.style?.getPropertyValue?.('padding-left') || row.style?.paddingLeft || '';
  if (paddingLeft) {
    if (typeof clone.style?.setProperty === 'function') clone.style.setProperty('padding-left', paddingLeft);
    else if (clone.style) clone.style.paddingLeft = paddingLeft;
  }
  return clone;
}

function tabberNativeDragImageForRow(row) {
  const sourceRow = row?.closest?.('.file-tree-row[data-tabber-type]') || row;
  const rows = tabberDragPreviewRows(sourceRow);
  if (!rows.length) return sourceRow;
  const preview = document.createElement('div');
  preview.className = 'tabber-drag-image drag-image';
  preview.dataset.tabberDragPreview = 'subtree';
  preview.dataset.tabberDragRoot = String(sourceRow.dataset?.path || '');
  preview.style.position = 'fixed';
  preview.style.left = '-10000px';
  preview.style.top = '-10000px';
  preview.style.pointerEvents = 'none';
  const rect = sourceRow.getBoundingClientRect?.();
  const width = Math.max(220, Math.min(620, Math.round(Number(rect?.width) || 360)));
  if (typeof preview.style?.setProperty === 'function') preview.style.setProperty('width', `${width}px`);
  else preview.style.width = `${width}px`;
  for (const item of rows) preview.appendChild(cloneTabberDragPreviewRow(item));
  document.body.appendChild(preview);
  dragState.nativePreview = preview;
  return preview;
}

function tabberRowDragItem(row) {
  if (!tabberIsAdvanced()) return '';
  const type = row?.dataset?.tabberType || '';
  if (type === 'normal') return row.dataset.tabberSession || '';
  if (type === 'tab') return row.dataset.tabberItem || '';
  return '';
}

function tabberRowIsTabDragSource(row) {
  const item = tabberRowDragItem(row);
  return Boolean(item && isLayoutItem(item) && !isFileExplorerItem(item));
}

function tabberRowDragIgnore(event) {
  return Boolean(event.target.closest?.('[data-auto-session], [data-pane-tab-close], button, input, textarea, select, a, .file-tree-icon'));
}

function bindTabberRowDragSource(row) {
  bindPaneTabNativeDragSource(row, () => tabberRowDragItem(row), item => slotForItem(item), {
    ignore: tabberRowDragIgnore,
    dragImage: () => tabberNativeDragImageForRow(row),
  });
}

// Shared-pipeline row updater for Tabber nodes (same .file-tree-row DOM + updateFileTreeRowContents).
function tabberTreeRowViewModel(row, fullPath, entry, depth, options = {}) {
  const data = entry.tabber || {};
  const expandable = false;
  const expanded = false;
  const selected = tabberTreeSelectedPaths.has(fullPath);
  const current = data.current === true;
  const normalPaneItem = data.normalPaneItem || (data.type === 'tab' ? data.item : '');
  const icon = expandable ? disclosureTriangleGlyph(expanded) : (data.icon || '');
  const rawLabel = data.label || entry.name;
  const label = compactHomePath(rawLabel);
  const description = data.description ? compactHomePath(data.description) : '';
  const titleParts = [
    label,
    description && description !== label ? description : '',
    data.branchText ? `${t('common.field.branch')}: ${data.branchText}` : '',
    data.repoRoot && data.repoRoot !== rawLabel ? compactHomePath(data.repoRoot) : '',
  ].filter(Boolean);
  const titleText = titleParts.join('\n');
  const nameHtml = data.normalNameHtml || '';
  const paneTab = data.type === 'normal' ? (data.session || '') : (data.type === 'tab' ? (data.item || '') : '');
  return TreeRowViewModel({
    path: fullPath,
    kind: entry.kind,
    name: entry.name,
    paddingLeft: fileTreeRowPadding(depth),
    selected,
    expandable,
    expanded,
    current,
    draggable: tabberIsAdvanced() && Boolean(paneTab && isLayoutItem(paneTab) && !isFileExplorerItem(paneTab)),
    icon,
    label,
    content: {
      iconClass: ['tabber-icon', expandable ? 'ui-disclosure-triangle' : ''].filter(Boolean).join(' '),
      disclosureExpanded: expandable ? expanded : undefined,
      nameHtml,
      dateText: (data.normalSuppressMtimeDate === true ? '' : (data.dateText || (entry.mtime ? fileTreeMtimeText(entry, {view: 'tabber'}) : ''))),
      dateHtml: data.dateHtml || '',
    },
    recencyEntry: entry,
    recencyOptions: options,
    dataset: {
      tabberTitle: titleText,
      path: fullPath, kind: entry.kind, name: entry.name,
      isRepo: '', isSymlink: '', symlinkTarget: '', indexed: '', indexExcluded: '',
      tabberType: data.type || '', tabberSession: data.session || '',
      tabberWindow: data.windowIndex !== null && data.windowIndex !== undefined ? data.windowIndex : '',
      tabberWindowId: data.windowId || '',
      tabberRepoRoot: data.repoRoot || '', tabberItem: data.item || '', tabberPaneItem: normalPaneItem,
      tabberNormalWindowView: data.normalWindowView === true ? 'true' : '', tabberBranch: data.branchText || '', paneTab,
      openChangeFile: '', openChangeSession: '', openChangeStatus: '', openChangeRepo: '',
      openChangeDirectory: '', changesFolderToggle: '', changeRel: '', changeSize: '',
    },
    classes: {
      [CLS.selected]: selected,
      expanded,
      [CLS.collapsed]: expandable && !expanded,
      'tabber-row': true,
      'tabber-normal-row': true,
      'tabber-active-window': data.type === 'normal' && data.active === true,
      'tabber-active-normal': data.type === 'normal' && Boolean(normalPaneItem) && activePaneItems().includes(normalPaneItem),
      'current-file': current && entry.kind !== 'dir',
      'current-directory': current && entry.kind === 'dir',
    },
  });
}

function updateTabberRow(row, fullPath, entry, depth, options = {}) {
  const data = entry.tabber || {};
  const model = tabberTreeRowViewModel(row, fullPath, entry, depth, options);
  patchTreeRow(row, model);
  row.removeAttribute('title');
  if (tabberRowIsTabDragSource(row)) bindTabberRowDragSource(row);
  // Tabber rows use delegation (bindTabberPanel) like the Differ; clear any stale Finder per-row handlers.
  clearFileTreeRowHandlers(row);
  return fullPath;
}

async function openTabberActivityOverview() {
  if (typeof openFileSurface === 'function') await openFileSurface(tabberItemId);
  else if (typeof openFileExplorerPane === 'function') await openFileExplorerPane(tabberItemId);
  else selectSession(tabberItemId);
  return true;
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

function tabberTabPath(item) {
  const value = String(item || '').trim();
  return value ? `/t_${tabberPathToken(value)}` : '';
}

function activeTabberRowPath() {
  const item = visualActivePaneItem();
  if (!item || isFileExplorerItem(item)) return '';
  if (!isTmuxSession(item)) return tabberTabPath(item);
  const view = tabberViewDescriptorForSession(item);
  if (view) return tabberWindowPath(view.sourceSession, view.windowIndex);
  const session = item;
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const activeWindow = tmuxWindowActiveRecord(session, info);
  return activeWindow ? tabberWindowPath(session, activeWindow.index) : tabberSessionPath(session);
}

function tabberSessionForNumericKey(key) {
  const value = String(key || '').trim();
  if (!/^[1-9]$/.test(value)) return '';
  return tabberOrderedSessions().includes(value) ? value : '';
}

function tabberNumericSessionKeyEventIsOwned(event) {
  if (!tabberSessionForNumericKey(event?.key)) return false;
  const panel = panelNodes.get(tabberItemId);
  return Boolean(panel && eventTargetIsFileExplorerSurface(event.target)
    && (panel === event.target || panel.contains(event.target)));
}

function tabberNormalRowForSession(panel, session) {
  const rows = Array.from(panel?.querySelectorAll?.('.file-tree-row[data-tabber-type="normal"]') || [])
    .filter(row => row.dataset.tabberSession === session);
  if (!rows.length) return null;
  const selected = rows.find(row => tabberNormalSelections?.includes(row.dataset.tabberPaneItem || row.dataset.tabberSession));
  if (selected) return selected;
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const activeWindow = tmuxWindowActiveRecord(session, info);
  return rows.find(row => row.dataset.tabberWindow !== undefined
    && tmuxWindowNumber(row.dataset.tabberWindow) === tmuxWindowNumber(activeWindow?.index)) || rows[0];
}

function openTabberSession(session, options = {}) {
  const target = String(session || '').trim();
  if (!target) return false;
  if (!tabberIsAdvanced()) { void toggleTabberNormalPane(target); return true; }
  selectSession(target, {userInitiated: true});
  return true;
}

async function activateNormalTabberRow(row, session, windowIndex) {
  const type = row?.dataset?.tabberType || '';
  const modeRevision = tabberModeRevision;
  if (tabberIsAdvanced()) return false;
  const activationRevision = ++tabberNormalPointerPreviewState.activationRevision;
  claimPendingDefaultTabberSelection();
  let item = type === 'tab' ? row.dataset.tabberItem || '' : row.dataset.tabberPaneItem || '';
  if (type === 'normal' && row.dataset.tabberNormalWindowView === 'true' && windowIndex !== null) {
    const pending = tabberNormalPointerPreviewState.pendingWindowActivation;
    const entry = buildTabberNormalRows().find(candidate => candidate.tabber?.session === session && Number(candidate.tabber.windowIndex) === windowIndex);
    const data = {...(entry?.tabber || {}), windowId: row.dataset.tabberWindowId || entry?.tabber?.windowId || tabberViewSessions.get(item)?.windowId || ''};
    const intentKey = `${String(session || '').trim()}\u001f${String(data.windowId || '')}`;
    const knownItem = item || tabberViewSessionForWindow(session, data.windowId);
    const selected = pending?.key === intentKey
      ? pending.selected
      : Boolean(knownItem && tabberNormalSelections?.includes(knownItem));
    if (selected) {
      const next = pending?.key === intentKey
        ? []
        : knownItem ? nextTabberNormalSelectionsFor(knownItem) : [];
      storeTabberNormalSelections(next);
      await applyTabberNormalSelections(next.at(-1) || tabberItemId, modeRevision, activationRevision);
      return Boolean(knownItem || pending);
    }
    const previousSelections = pending?.key === intentKey
      ? pending.previousSelections
      : (tabberNormalSelections || []).slice();
    tabberNormalPointerPreviewState.pendingWindowActivation = {
      key: intentKey,
      selected: true,
      previousSelections,
      activationRevision,
    };
    item = await ensureTabberViewSessionForWindow(session, windowIndex, {
      label: data.label || `${sessionLabel(session)} window ${windowIndex}`,
      windowName: data.label || '',
      agentKey: data.agentKey || '',
      windowId: data.windowId || '',
    }, {reselect: true});
    if (modeRevision !== tabberModeRevision || activationRevision !== tabberNormalPointerPreviewState.activationRevision || tabberIsAdvanced()) return false;
    if (!item) {
      if (tabberNormalPointerPreviewState.pendingWindowActivation?.activationRevision === activationRevision) {
        tabberNormalPointerPreviewState.pendingWindowActivation = null;
      }
      return false;
    }
    if (tabberNormalPointerPreviewState.pendingWindowActivation?.activationRevision !== activationRevision) return false;
    if (item !== knownItem) {
      row.dataset.tabberPaneItem = item;
      scheduleTabberTreeLayoutStateSync({refreshTree: true});
    }
    storeTabberNormalSelections([item]);
    await applyTabberNormalSelections(item, modeRevision, activationRevision);
    return true;
  }
  if (modeRevision !== tabberModeRevision || activationRevision !== tabberNormalPointerPreviewState.activationRevision || tabberIsAdvanced()) return false;
  if (!item && type === 'normal' && row.dataset.tabberNormalWindowView === 'true') return false;
  if (!item && type === 'normal') item = session;
  if (item) void toggleTabberNormalPane(item, activationRevision);
  return Boolean(item);
}

// Both modes activate the same flat window rows.
function handleTabberRowActivate(row, event) {
  const type = row.dataset.tabberType;
  const session = row.dataset.tabberSession || '';
  const windowIndex = row.dataset.tabberWindow !== undefined ? tmuxWindowNumber(row.dataset.tabberWindow) : null;
  if (!tabberIsAdvanced() && (type === 'normal' || type === 'tab')) {
    void activateNormalTabberRow(row, session, windowIndex);
    return;
  }
  const switchWindow = () => {
    if (session && windowIndex !== null) {
      return activateTmuxWindowFromUserAction(session, windowIndex, row.querySelector('.file-tree-name')?.textContent || session);
    }
    return false;
  };
  if (type === 'tab' && row.dataset.tabberItem) {
    selectSession(row.dataset.tabberItem, {userInitiated: true});
  } else if (type === 'normal' && session) {
    if (windowIndex !== null) switchWindow();
    selectSession(session, {userInitiated: true});
  }
}

const tabberTreeInteractionController = createSharedTreeInteractionController({
  name: 'tabber',
  scrollOnClick: false,
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
  activateRow(row, event) {
    handleTabberRowActivate(row, event);
    scheduleTabberTreeLayoutStateSync({scrollIntoView: true});
  },
});

function syncTabberTreeActiveSelection(panel = document, options = {}) {
  const currentPath = activeTabberRowPath();
  // Hovering Tabber with Auto-focus makes it the keyboard owner. Keep its existing cursor while
  // it owns that focus; otherwise the focus refresh would erase the cursor before ArrowUp/Down
  // can advance it. A normal focus move into Tabber still clears stale selection as before.
  if (!currentPath && fileExplorerHasAutoFocusedKeyboardOwnership() && tabberTreeSelectedPaths.size) {
    if (options.modelOnly !== true) tabberTreeInteractionController.applyState(panel, options);
    return true;
  }
  if (!currentPath) {
    tabberTreeSelectedPaths.clear();
    tabberTreeSelectionLead = '';
    if (options.modelOnly !== true) tabberTreeInteractionController.applyState(panel, options);
    return false;
  }
  return tabberTreeInteractionController.syncCurrent(panel, options);
}

function syncTabberTreeLayoutState(panel = document, options = {}) {
  if (!itemInLayout(tabberItemId)) return false;
  const currentPath = activeTabberRowPath();
  const rows = Array.from(panel.querySelectorAll?.('.file-tree-row[data-tabber-type]') || []);
  if (!rows.length) return syncTabberTreeActiveSelection(panel, options);
  const visibleItems = new Set(activePaneItems());
  const focusedItem = visualActivePaneItem();
  const signature = `${Array.from(visibleItems).sort().join('\u001f')}\u001e${focusedItem || ''}\u001e${currentPath}`;
  if (options.force !== true && tabberLayoutStateSignature === signature) {
    // Selection can change without changing layout; its deferred DOM update still belongs here.
    return syncTabberTreeActiveSelection(panel, options);
  }
  tabberLayoutStateSignature = signature;
  const perf = clientPerfStart('tabberLayoutSync');
  for (const row of rows) {
    const type = row.dataset.tabberType || '';
    const session = row.dataset.tabberSession || '';
    const item = row.dataset.tabberItem || '';
    const paneItem = row.dataset.tabberPaneItem || (type === 'tab' ? item : '');
    const activeNormal = type === 'normal' && Boolean(paneItem) && visibleItems.has(paneItem);
    const activeTab = type === 'tab' && visibleItems.has(item);
    const current = !tabberIsAdvanced() && paneItem
      ? focusedItem === paneItem
      : Boolean(currentPath && row.dataset.path === currentPath);
    row.classList.toggle('current-file', current && row.dataset.kind !== 'dir');
    row.classList.toggle('current-directory', current && row.dataset.kind === 'dir');
    row.classList.toggle('tabber-active-normal', activeNormal);
    row.classList.toggle('tabber-active-tab', activeTab);
    setAttributeIfChanged(row, 'aria-current', current ? 'true' : null);
  }
  const synced = syncTabberTreeActiveSelection(panel, options);
  clientPerfEnd(perf, {rows: rows.length});
  return synced;
}

function scheduleTabberTreeLayoutStateSync(options = {}) {
  if (!itemInLayout(tabberItemId)) return false;
  // Keep keyboard selection synchronous while folding all DOM state changes from one Dockview
  // transaction after the terminal's first paint.
  syncTabberTreeActiveSelection(document, {modelOnly: true});
  if (options.refreshTree === true && typeof dockviewLayoutState !== 'undefined' && dockviewLayoutState.tabActivationPerf) {
    dockviewLayoutState.deferredMetadataTabberTreeRefresh = true;
    return false;
  }
  tabberLayoutStateScrollIntoView ||= options.scrollIntoView === true;
  tabberLayoutStateRefreshTree ||= options.refreshTree === true;
  if (tabberLayoutStateFrame) return false;
  tabberLayoutStateFrame = requestAnimationFrame(() => {
    tabberLayoutStateFrame = requestAnimationFrame(() => {
      tabberLayoutStateFrame = 0;
      const scrollIntoView = tabberLayoutStateScrollIntoView;
      const refreshTree = tabberLayoutStateRefreshTree;
      tabberLayoutStateScrollIntoView = false;
      tabberLayoutStateRefreshTree = false;
      if (refreshTree && typeof dockviewLayoutState !== 'undefined' && dockviewLayoutState.tabActivationPerf) {
        dockviewLayoutState.deferredMetadataTabberTreeRefresh = true;
        flushDeferredMetadataTabStripRefresh();
        return;
      }
      if (refreshTree && itemInLayout(tabberItemId)) refreshTabberPanels();
      syncTabberTreeLayoutState(document, {scrollIntoView});
    });
  });
  return true;
}

function bindTabberPanel(panel) {
  if (!panel) return null;
  return bindScopedOnce(panel, 'tabber-panel', scope => {
  normalizeAppOwnedControls(panel);
  const cancelExitRefresh = () => scope.release('hover-exit-refresh');
  scope.ownEvent('pointerenter', panel, 'pointerenter', event => {
    if (event.pointerType === 'touch') return;
    panel.__tabberPointerInside = true;
    cancelExitRefresh();
  });
  scope.ownEvent('pointerleave', panel, 'pointerleave', event => {
    if (event.pointerType === 'touch') return;
    panel.__tabberPointerInside = false;
    const timer = scheduleDeferredTabberRefresh();
    scope.ownTimer('hover-exit-refresh', timer, value => {
      if (tabberRefreshDeferredTimer !== value) return;
      clearTimeout(value);
      tabberRefreshDeferredTimer = null;
    });
  });
  scope.replace('hover-state', panel, () => { delete panel.__tabberPointerInside; });
  let pointerActivatedPath = '';
  scope.ownEvent('pointerdown', panel, 'pointerdown', event => {
    pointerActivatedPath = '';
    if (!itemInLayout(tabberItemId)) return;
    const row = tabberNormalPaneActivationRow(event);
    if (!row || !panel.contains(row)) return;
    const previewed = previewTabberNormalPaneSelection(row, event);
    // Normal has no mouse drag gesture; commit through the same row owner before mouse-up.
    // Touch waits for click so scrolling the roster does not open a conversation.
    if (previewed && event.pointerType === 'mouse') {
      pointerActivatedPath = row.dataset.path;
      tabberTreeInteractionController.handleClick(event, panel, {row});
    }
  });
  scope.ownEvent('pointerup', document, 'pointerup', scheduleTabberNormalPointerPreviewRestore);
  scope.ownEvent('pointercancel', document, 'pointercancel', () => {
    pointerActivatedPath = '';
    restoreTabberNormalPointerPreview();
  });
  scope.ownEvent('keydown', panel, 'keydown', event => {
    const newChat = event.target.closest?.('[data-tabber-new-chat]');
    if (newChat && panel.contains(newChat) && event.key === 'ArrowDown') {
      event.preventDefault();
      newChat.click();
    }
  });
  scope.ownEvent('click', panel, 'click', event => {
    if (!itemInLayout(tabberItemId)) return;
    const newChat = event.target.closest?.('[data-tabber-new-chat]');
    if (newChat && panel.contains(newChat)) {
      event.preventDefault();
      const wrapper = newChat.closest('.tabber-new-chat-menu');
      const popover = wrapper?.querySelector(':scope > .tabber-new-chat-popover');
      if (wrapper && popover) {
        popover.replaceChildren(...newTmuxSessionItems().filter(Boolean).map(createAppMenuItem));
        openAppMenu(wrapper, {
        focusFirst: true,
        pinned: true,
          returnFocus: () => Array.from(document.querySelectorAll('[data-tabber-new-chat]')).find(appMenuFocusTargetIsAvailable)
            || document.querySelector('#topbarTabberToggle'),
        });
      }
      return;
    }
    const search = event.target.closest?.('[data-tabber-search]');
    if (search && panel.contains(search)) {
      event.preventDefault();
      openFileQuickOpen();
      return;
    }
    const row = event.target.closest?.('.file-tree-row[data-tabber-type]');
    if (!row || !panel.contains(row)) return;
    if (event.detail > 0 && pointerActivatedPath && row.dataset.path === pointerActivatedPath) {
      pointerActivatedPath = '';
      consumeSharedTreeEvent(event);
      return;
    }
    pointerActivatedPath = '';
    tabberTreeInteractionController.handleClick(event, panel, {row});
  });
  scope.ownEvent('keydown', panel, 'keydown', event => {
    if (!itemInLayout(tabberItemId)) return;
    if (tabberTreeInteractionController.handleKeydown(event, panel)) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    const session = tabberSessionForNumericKey(event.key);
    if (!session) return;
    if (!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)) return;
    event.preventDefault();
    event.stopPropagation();
    if (!tabberIsAdvanced()) {
      const row = tabberNormalRowForSession(panel, session);
      if (row) void activateNormalTabberRow(row, session, row.dataset.tabberWindow === undefined ? null : tmuxWindowNumber(row.dataset.tabberWindow));
      return;
    }
    openTabberSession(session);
  });
  scope.ownEvent('change', panel, 'change', event => {
    if (!itemInLayout(tabberItemId)) return;
    const lookback = event.target.closest?.('[data-tabber-lookback]');
    if (!lookback || !panel.contains(lookback)) return;
    setTabberSessionFileLookbackHours(lookback.value);
  });
  scope.ownEvent('contextmenu', panel, 'contextmenu', event => {
    if (!itemInLayout(tabberItemId)) return;
    const tabRow = event.target.closest?.('.file-tree-row[data-tabber-type="session"], .file-tree-row[data-tabber-type="window"], .file-tree-row[data-tabber-type="normal"], .file-tree-row[data-tabber-type="tab"]');
    const tabItem = tabRow?.dataset.tabberType === 'tab' ? tabRow.dataset.tabberItem : tabRow?.dataset.tabberSession;
    if (tabRow && panel.contains(tabRow) && tabItem && (isPinnableTab(tabItem) || isTmuxSession(tabItem))) {
      event.preventDefault();
      event.stopPropagation();
      tabInteractionControllerForApp().showActions({
        anchor: tabRow,
        item: tabItem,
        sourceSlot: () => slotForItem(tabItem),
        descriptionWindow: tabRow.dataset.tabberWindow === undefined ? null : Number(tabRow.dataset.tabberWindow),
        descriptionWindowId: tabRow.dataset.tabberWindowId || '',
      }, event);
      return;
    }

  });
  });
}

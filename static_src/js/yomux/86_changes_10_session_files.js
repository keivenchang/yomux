function fileExplorerSelectedSessionForView(view) {
  const surface = normalizeFileExplorerView(view);
  const selected = surface === 'finder' ? fileExplorerFinderSelectedSession : fileExplorerChangesSelectedSession;
  if (selected && sessions.includes(selected)) return selected;
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  if (explicitSession) return explicitSession;
  return '';
}

function fileExplorerFinderTargetSession() {
  const selected = fileExplorerSelectedSessionForView('finder');
  if (selected) return selected;
  const payloadSession = String(fileExplorerFinderSessionFilesState.payload?.session || '');
  if (payloadSession && sessions.includes(payloadSession)) return payloadSession;
  return sessions[0] || '';
}

function fileExplorerSessionFilesTargetSession() {
  const selected = fileExplorerSelectedSessionForView('differ');
  if (selected) return selected;
  const payloadSession = String(fileExplorerSessionFilesState.payload?.session || '');
  if (payloadSession && sessions.includes(payloadSession)) return payloadSession;
  return sessions[0] || '';
}

function sessionFilesSurfaceDescriptor(destination, options = {}) {
  const normalizedDestination = destination === 'finder' ? 'finder' : 'differ';
  const state = sessionFilesStateForDestination(normalizedDestination);
  const session = options.session || (normalizedDestination === 'finder'
    ? fileExplorerFinderTargetSession()
    : fileExplorerSessionFilesTargetSession());
  return {
    destination: normalizedDestination,
    state,
    session,
    visible: normalizedDestination === 'finder' ? fileExplorerTreePaneIsVisible() : fileExplorerSessionFilesPaneIsVisible(),
    cache: sessionFilesCacheForDestination(normalizedDestination),
  };
}

function sessionFilesSurfaceNeedsFetch(view, state, session) {
  if (view === 'finder') return !sessionFilesPayloadIsLoadedForSession(fileExplorerFinderSessionFilesState.payload, session);
  return !sessionFilesPayloadIsLoadedForSession(state.payload, session);
}

function ensureSessionFilesSurfaceLoaded(view, options = {}) {
  if (view === 'tabber') {
    fetchTabberActivity();
    return;
  }
  const destination = view === 'finder' ? 'finder' : 'differ';
  const surface = sessionFilesSurfaceDescriptor(destination, options);
  if (!surface.session || sessionFilesSurfaceNeedsFetch(view, surface.state, surface.session) === false) return;
  if (clientPushCanSupplyData() && options.fetchEvenWhenPush !== true) {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  } else {
    fetchSessionFiles({destination, session: surface.session, silent: true});
  }
}

function emptySessionFilesPayload(session = '', loaded = true, destination = 'differ') {
  const refs = destination === 'finder' ? {from: 'HEAD', to: 'current'} : {from: diffRefFrom, to: diffRefTo};
  return {session, files: [], repos: [], refs_by_repo: {}, errors: [], from_ref: refs.from, to_ref: refs.to, loaded};
}

function normalizedSessionFilesPayload(payload = {}, defaults = {}) {
  return {
    session: payload.session || defaults.session || '',
    files: Array.isArray(payload.files) ? payload.files : [],
    repos: Array.isArray(payload.repos) ? payload.repos : [],
    refs_by_repo: payload.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {},
    errors: Array.isArray(payload.errors) ? payload.errors : [],
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    cache: payload.cache && typeof payload.cache === 'object' ? {...payload.cache} : {},
    from_ref: payload.from_ref || defaults.from_ref || diffRefFrom,
    to_ref: payload.to_ref || defaults.to_ref || diffRefTo,
    refreshing: payload.refreshing === true,
    loaded: defaults.loaded === false ? false : true,
  };
}

function sessionFilesPayloadIsRefreshing(payload) {
  return payload?.refreshing === true;
}

function sessionFilesPayloadIsFinderWorktree(payload, session = '') {
  if (!payload || payload.loaded !== true) return false;
  if (session && String(payload.session || '') !== String(session)) return false;
  return (payload.from_ref || 'HEAD') === 'HEAD' && (payload.to_ref || 'current') === 'current';
}

function sessionFilesPayloadIsLoadedForSession(payload, session = '') {
  if (!payload || payload.loaded !== true) return false;
  return !session || String(payload.session || '') === String(session);
}

function sessionFilesPayloadIsRootlessEmpty(payload) {
  if (!payload || payload.loaded !== true) return false;
  if ((Array.isArray(payload.files) ? payload.files : []).length) return false;
  if ((Array.isArray(payload.repos) ? payload.repos : []).length) return false;
  if ((Array.isArray(payload.errors) ? payload.errors : []).length) return false;
  return true;
}

function sessionFilesPayloadHasVisibleDifferResult(payload, files = null) {
  if (!payload || payload.loaded !== true) return false;
  const visibleFiles = Array.isArray(files) ? files : (Array.isArray(payload.files) ? payload.files : []);
  if (visibleFiles.length) return true;
  if ((Array.isArray(payload.errors) ? payload.errors : []).length) return true;
  if ((Array.isArray(payload.warnings) ? payload.warnings : []).length) return true;
  if (sessionFilesPayloadIsRefreshing(payload)) return false;
  if (sessionFilesRepoRoots(payload).length > 0) return true;
  return !sessionFilesPayloadIsRefreshing(payload) && sessionFilesPayloadIsRootlessEmpty(payload);
}

function sessionFilesPanelIsLoading(payload, files = null) {
  if (fileExplorerSessionFilesState.loading) return true;
  if (!sessionFilesPayloadIsRefreshing(payload)) return false;
  return !sessionFilesPayloadHasVisibleDifferResult(payload, files);
}

function sessionFilesPayloadShouldPreserveCurrent(nextPayload, destination = 'differ') {
  const session = String(nextPayload?.session || '');
  const current = sessionFilesPayloadForDestination(destination);
  if (!session) return false;
  if (!sessionFilesPayloadIsLoadedForSession(current, session)) return false;
  if (sessionFilesPayloadIsRefreshing(nextPayload)) return sessionFilesRepoRoots(current).length > 0;
  if (!sessionFilesPayloadIsRootlessEmpty(nextPayload)) return false;
  return sessionFilesRepoRoots(current).length > 0;
}

function switchFileExplorerChangesSession(session) {
  if (!session || !document.querySelector('.file-explorer-changes-panel')) return;
  if (!isTmuxSession(session) || !sessions.includes(session)) return;
  rememberFileExplorerExplicitSyncSession(session);
  fileExplorerChangesSelectedSession = session;
  scheduleFileExplorerActiveTabSync(session, {explicit: true});
  // Tabber is backed by transcript/activity data, so a pane-tab click changes only its current and
  // active row state. Preparing Differ payloads, rebuilding the tree, and forcing a session-files
  // request here made one tab activation perform the same Tabber state sync twice.
  if (itemInLayout(tabberItemId) && focusedPanelItem === tabberItemId) {
    scheduleTabberTreeLayoutStateSync();
    return;
  }
  const cached = fileExplorerSessionFilesCache.get(sessionFilesCacheKey(session, 'differ'));
  const cachedPayloadIsLoaded = sessionFilesPayloadIsLoadedForSession(cached?.payload, session);
  if (cachedPayloadIsLoaded) {
    setSessionFilesPayloadForDestination('differ', cached.payload);
    fileExplorerSessionFilesState.signature = cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload);
  } else {
    const pendingPayload = emptySessionFilesPayload(session, false, 'differ');
    setSessionFilesPayloadForDestination('differ', pendingPayload);
    fileExplorerSessionFilesState.signature = sessionFilesPayloadSignatureForPayload(pendingPayload);
  }
  setSessionFilesLoadingForDestination('differ', !cachedPayloadIsLoaded);
  renderFileExplorerChangesPanel(panelNodes.get(differItemId));
  // A cached session switch already has visible last-known-good rows. Let the server decide
  // whether its entry is stale and coalesce one background refresh; forcing here used to bypass
  // that parent and submit a full session-files job for every tab switch.
  fetchSessionFiles({destination: 'differ', session, silent: true, force: !cachedPayloadIsLoaded, background: cachedPayloadIsLoaded});
}

function switchFileExplorerFinderSession(session) {
  if (!isTmuxSession(session) || !sessions.includes(session)) return false;
  if (fileExplorerFinderSelectedSession === session) return false;
  fileExplorerFinderSelectedSession = session;
  rememberFileExplorerExplicitSyncSession(session);
  scheduleFileExplorerActiveTabSync(session, {explicit: true});
  const cached = fileExplorerFinderSessionFilesCache.get(sessionFilesCacheKey(session, 'finder'));
  const cachedPayloadIsLoaded = sessionFilesPayloadIsLoadedForSession(cached?.payload, session);
  if (cachedPayloadIsLoaded) {
    setSessionFilesPayloadForDestination('finder', cached.payload);
    setSessionFilesSignatureForDestination('finder', cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload));
    // The directory rows are already mounted when a cached session is selected. Complete the
    // same first-paint transition as a fresh listing so a prior filesystem refresh cannot leave
    // the Finder diff gate held forever.
    finishFinderFastPaint();
  } else {
    // A session switch without a cache must not keep decorating Finder with the previous
    // session's +/- rows while the target's slower Git/transcript backfill runs.
    beginFinderSessionFilesDiffBackfill();
    const pendingPayload = emptySessionFilesPayload(session, false, 'finder');
    setSessionFilesPayloadForDestination('finder', pendingPayload);
    setSessionFilesSignatureForDestination('finder', sessionFilesPayloadSignatureForPayload(pendingPayload));
    setSessionFilesLoadingForDestination('finder', true);
    renderSessionFilesDestination('finder', {force: true});
  }
  fetchSessionFiles({destination: 'finder', session, silent: true, force: !cachedPayloadIsLoaded, background: cachedPayloadIsLoaded});
  return true;
}

function fileExplorerChangesSessionInteractionIsCurrent(session) {
  return fileExplorerChangesSelectedSession === session
    && fileExplorerExplicitSyncSessionTarget() === session;
}

function noteFileExplorerChangesSessionInteraction(session) {
  if (!isTmuxSession(session) || !sessions.includes(session)) return false;
  if (fileExplorerChangesSessionInteractionIsCurrent(session)) return false;
  rememberFileExplorerExplicitSyncSession(session);
  fileExplorerChangesSelectedSession = session;
  if (document.querySelector('.file-explorer-changes-panel')) {
    switchFileExplorerChangesSession(session);
  }
  return true;
}

function sessionFilesPayloadForDestination(destination) {
  return sessionFilesStateForDestination(destination).payload;
}

function sessionFilesStateForDestination(destination = 'differ') {
  return destination === 'finder' ? fileExplorerFinderSessionFilesState : fileExplorerSessionFilesState;
}

const sessionFilesProducerDeadlineMs = 5000;
// A redacted background completion is a cache-view hint, not a command to refresh every
// visible surface. This bounded destination map owns replay acknowledgement and serialization
// of genuinely newer completions; it replaces a separate unbounded acknowledgement cache.
const sessionFilesCompletionRevalidations = new Map();

function scheduleSessionFilesProducerDeadline(destination, payload) {
  if (!sessionFilesPayloadIsRefreshing(payload)) return;
  if (payload?.pending_operation_id) return;
  setTimeout(() => {
    if (sessionFilesPayloadForDestination(destination) !== payload) return;
    if (sessionFilesLoadingForDestination(destination)) return;
    const deadline = apiFetchDeadlineError(sessionFilesProducerDeadlineMs, 'session-files producer');
    const nextPayload = {
      ...payload,
      refreshing: false,
      errors: [...(Array.isArray(payload.errors) ? payload.errors : []), deadline.message],
      loaded: true,
    };
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
    sessionFilesCacheForDestination(destination).set(sessionFilesCacheKey(nextPayload.session, destination), {payload: nextPayload, signature});
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
    renderSessionButtons();
  }, sessionFilesProducerDeadlineMs);
}

function retireSessionFilesResponseOwner(destination = 'differ') {
  const state = sessionFilesStateForDestination(destination);
  state.guard.invalidate();
  clearSessionFilesStreamState(state);
  const controller = state.abortController;
  state.abortController = null;
  return controller;
}

function clearSessionFilesStreamState(state) {
  state.streamRepos = null;
  state.streamSession = '';
}

function retireSessionFilesRequest(destination = 'differ', reason = 'session-files request superseded') {
  const controller = retireSessionFilesResponseOwner(destination);
  if (!controller || controller.signal.aborted) return;
  const error = new Error(reason);
  error.name = 'AbortError';
  controller.abort(error);
}

function sessionFilesRequestIsActiveForDestination(destination = 'differ') {
  const controller = sessionFilesStateForDestination(destination).abortController;
  return Boolean(controller && !controller.signal.aborted);
}

function setSessionFilesPayloadForDestination(destination, payload, options = {}) {
  const state = sessionFilesStateForDestination(destination);
  if (options.invalidateRequest !== false) retireSessionFilesRequest(destination, options.retirementReason);
  state.payload = payload;
  scheduleSessionFilesProducerDeadline(destination, payload);
  if (destination === 'finder') {
    scheduleFileTreeMetadataRefresh({highlightsOnly: true});
  }
  if (
    destination === 'finder'
    && fileExplorerRootMode === 'sync'
    && payload?.loaded === true
  ) {
    scheduleFileExplorerActiveTabSync(payload.session || null);
  }
}

function sessionFilesPayloadSignatureForPayload(payload) {
  const files = Array.isArray(payload?.files) ? payload.files : [];
  const repos = Array.isArray(payload?.repos) ? payload.repos : [];
  return JSON.stringify(canonicalJsonValue({
    session: payload?.session || '',
    loaded: payload?.loaded === true,
    refreshing: sessionFilesPayloadIsRefreshing(payload),
    streaming: payload?.streaming === true,
    from: payload?.from_ref || '',
    to: payload?.to_ref || '',
    errors: Array.isArray(payload?.errors) ? payload.errors : [],
    warnings: Array.isArray(payload?.warnings) ? payload.warnings : [],
    cache_request_descriptor: String(payload?.cache?.request_descriptor || ''),
    pending_key: String(payload?.pending_key || ''),
    pending_epoch: payload?.pending_epoch ?? null,
    pending_operation_id: String(payload?.pending_operation_id || ''),
    repos,
    files,
    refs_by_repo: payload?.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {},
  }));
}

function sessionFilesSignatureForDestination(destination) {
  return sessionFilesStateForDestination(destination).signature;
}

function setSessionFilesSignatureForDestination(destination, signature) {
  sessionFilesStateForDestination(destination).signature = signature;
}

function sessionFilesLoadingForDestination(destination) {
  return sessionFilesStateForDestination(destination).loading;
}

function setSessionFilesLoadingForDestination(destination, loading) {
  sessionFilesStateForDestination(destination).loading = loading;
}

function finderSessionFilesDiffIsDeferred() {
  return fileExplorerFinderSessionFilesState.diffBackfillPending === true
    && fileExplorerFinderSessionFilesState.partialBackfillPainted !== true;
}

function beginFinderSessionFilesDiffBackfill() {
  fileExplorerFinderSessionFilesState.fastPaintGeneration += 1;
  fileExplorerFinderSessionFilesState.diffBackfillPending = true;
  fileExplorerFinderSessionFilesState.partialBackfillPainted = false;
  fileExplorerFinderSessionFilesState.fastListingPainted = false;
  return fileExplorerFinderSessionFilesState.fastPaintGeneration;
}

function markFinderFastListingPainted(generation = fileExplorerFinderSessionFilesState.fastPaintGeneration) {
  if (generation !== fileExplorerFinderSessionFilesState.fastPaintGeneration) return false;
  fileExplorerFinderSessionFilesState.fastListingPainted = true;
  return true;
}

function releaseFinderSessionFilesDiffBackfillIfReady() {
  if (
    fileExplorerFinderSessionFilesState.diffBackfillPending !== true
    || fileExplorerFinderSessionFilesState.fastListingPainted !== true
    || sessionFilesRequestIsActiveForDestination('finder')
    || !sessionFilesPayloadIsLoadedForSession(fileExplorerFinderSessionFilesState.payload, fileExplorerFinderTargetSession())
  ) return false;
  fileExplorerFinderSessionFilesState.diffBackfillPending = false;
  return true;
}

function finishFinderFastPaint(generation = fileExplorerFinderSessionFilesState.fastPaintGeneration) {
  if (!markFinderFastListingPainted(generation)) return false;
  ensureSessionFilesSurfaceLoaded('finder', {fetchEvenWhenPush: true});
  releaseFinderSessionFilesDiffBackfillIfReady();
  scheduleFileTreeMetadataRefresh();
}

function scheduleFinderSessionFilesBackfillAfterFastPaint(session, generation) {
  const expectedSession = String(session || '');
  if (!expectedSession) return;
  scheduleAfterFileExplorerFirstPaint(() => {
    if (fileExplorerFinderTargetSession() !== expectedSession || !markFinderFastListingPainted(generation)) return;
    if (!sessionFilesRequestIsActiveForDestination('finder')) {
      void fetchSessionFiles({destination: 'finder', session: expectedSession, silent: true, force: true});
    }
    releaseFinderSessionFilesDiffBackfillIfReady();
    scheduleFileTreeMetadataRefresh();
  });
}

function sessionFilesRenderOptions(options = {}) {
  return options.force === true || options.silent !== true ? {force: true} : {};
}

function sessionFilesPerfDetails(payload = {}, extra = {}) {
  const files = Array.isArray(payload?.files) ? payload.files.length : 0;
  return {nodes: sessionFilesRepoRoots(payload).length, rows: files, ...extra};
}

function sessionFilesStreamSupported(destination) {
  return (destination === 'finder' || destination === 'differ') && /^Go(?:\s|$)/.test(String(bootstrap.backendLabel || ''));
}

function sessionFilesStreamRows(payload, key) {
  return Array.isArray(payload?.[key]) ? payload[key].filter(row => row && typeof row === 'object') : [];
}

function mergeSessionFilesStreamChunk(destination, session, chunk, requestIsCurrent) {
  if (!requestIsCurrent() || !chunk || typeof chunk !== 'object') return false;
  const state = sessionFilesStateForDestination(destination);
  const current = state.payload?.session === session ? state.payload : emptySessionFilesPayload(session, false, destination);
  const replaceRepo = chunk.replace_repo === true;
  const chunkRepos = sessionFilesStreamRows(chunk, 'repos');
  const chunkRepoPaths = new Set(chunkRepos.map(row => String(row.repo || '')).filter(Boolean));
  const chunkFiles = sessionFilesStreamRows(chunk, 'files');
  let nextFiles;
  let nextRepos;
  let refsByRepo;
  if (destination === 'differ' && chunkRepoPaths.size === 1 && chunkRepos.length === 1) {
    let repoRows;
    if (!(state.streamRepos instanceof Map) || state.streamSession !== session) {
      repoRows = sessionFilesStreamRows(current, 'repos').slice();
      const filesByRepo = new Map();
      for (const file of sessionFilesStreamRows(current, 'files')) {
        const path = String(file.repo || '');
        const files = filesByRepo.get(path) || [];
        files.push(file);
        filesByRepo.set(path, files);
      }
      state.streamRepos = new Map(repoRows.map((repo, index) => [String(repo.repo || ''), {
        repo, files: filesByRepo.get(String(repo.repo || '')) || [], refs: current.refs_by_repo?.[repo.repo], index,
      }]));
      state.streamSession = session;
      refsByRepo = {...(current.refs_by_repo || {})};
    } else {
      repoRows = Array.isArray(current.repos) ? current.repos : [];
      refsByRepo = current.refs_by_repo || {};
    }
    const repo = chunkRepos[0];
    const repoPath = String(repo.repo || '');
    const existing = state.streamRepos.get(repoPath);
    const index = existing ? existing.index : repoRows.length;
    const refs = chunk.refs_by_repo?.[repoPath] ?? existing?.refs;
    state.streamRepos.set(repoPath, {
      repo,
      files: replaceRepo ? chunkFiles : [...(existing?.files || []), ...chunkFiles],
      refs,
      index,
    });
    if (existing) repoRows[index] = repo;
    else repoRows.push(repo);
    Object.assign(refsByRepo, chunk.refs_by_repo || {});
    if (refs !== undefined) refsByRepo[repoPath] = refs;
    nextFiles = [];
    nextRepos = repoRows;
  } else {
    const priorRecords = state.streamRepos instanceof Map && state.streamSession === session
      ? [...state.streamRepos.values()]
      : null;
    const currentFiles = priorRecords
      ? priorRecords.flatMap(record => record.files)
      : sessionFilesStreamRows(current, 'files');
    const currentRepos = sessionFilesStreamRows(current, 'repos');
    const currentRefs = current.refs_by_repo || {};
    clearSessionFilesStreamState(state);
    nextFiles = replaceRepo && chunkRepoPaths.size
      ? currentFiles.filter(file => !chunkRepoPaths.has(String(file.repo || '')))
      : currentFiles.slice();
    nextFiles.push(...chunkFiles);
    nextRepos = replaceRepo && chunkRepoPaths.size
      ? currentRepos.filter(repo => !chunkRepoPaths.has(String(repo.repo || '')))
      : currentRepos.slice();
    nextRepos.push(...chunkRepos);
    refsByRepo = {...currentRefs, ...(chunk.refs_by_repo || {})};
  }
  const warnings = [...(Array.isArray(current.warnings) ? current.warnings : []), ...(Array.isArray(chunk.warnings) ? chunk.warnings : [])];
  const nextPayload = {};
  for (const key of Object.keys(current)) {
    if (key !== 'files') nextPayload[key] = current[key];
  }
  Object.assign(nextPayload, {
    session,
    streamRevision: (Number(current.streamRevision) || 0) + 1,
    hours: chunk.hours ?? current.hours,
    files: destination === 'differ' && state.streamRepos instanceof Map ? [] : nextFiles,
    repos: nextRepos,
    refs_by_repo: refsByRepo,
    from_ref: chunk.from_ref || current.from_ref,
    to_ref: chunk.to_ref || current.to_ref,
    warnings: [...new Set(warnings.map(value => String(value)))],
    errors: [],
    loaded: false,
    streaming: true,
  });
  if (destination === 'differ' && state.streamRepos instanceof Map) {
    Object.defineProperty(nextPayload, 'files', {
      enumerable: true,
      get: () => [...state.streamRepos.values()].flatMap(record => record.files),
    });
  }
  setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
  setSessionFilesSignatureForDestination(destination, `stream:${session}:${nextPayload.streamRevision}`);
  if (destination === 'finder') fileExplorerFinderSessionFilesState.partialBackfillPainted = true;
  let changedRepo = null;
  if (chunkRepoPaths.size === 1 && chunkRepos.length === 1) {
    const repoFiles = state.streamRepos instanceof Map && destination === 'differ'
      ? state.streamRepos.get(String(chunkRepos[0].repo || ''))?.files || chunkFiles
      : chunkFiles;
    changedRepo = {
      repo: chunkRepos[0].repo,
      files: repoFiles,
      repos: chunkRepos,
    };
  }
  renderSessionFilesDestination(destination, changedRepo ? {changedRepo} : {force: true});
  return true;
}

function finalizeSessionFilesStreamPayload(state, payload = {}, requestIsCurrent = () => true) {
  if (!requestIsCurrent()) return null;
  if (!(state.streamRepos instanceof Map)) return {...state.payload, ...payload};
  const streamRepos = state.streamRepos;
  const records = [...streamRepos.values()];
  const result = {};
  for (const key of Object.keys(state.payload || {})) {
    if (key !== 'files') result[key] = state.payload[key];
  }
  Object.assign(result, payload);
  result.files = records.flatMap(record => record.files);
  result.repos = sessionFilesStreamRows(state.payload, 'repos').slice();
  result.refs_by_repo = {...(state.payload?.refs_by_repo || {})};
    clearSessionFilesStreamState(state);
  return result;
}

async function fetchSessionFilesStream(url, state, requestIsCurrent, onRepo) {
  const controller = state.abortController;
  const response = await apiFetch(url, {
    cache: 'no-store',
    headers: {Accept: 'text/event-stream'},
    ...(controller ? {signal: controller.signal} : {}),
  }, {recordDebug: true, abortRetirementReason: 'session_files_request_superseded'});
  if (!response.ok) {
    const error = new Error(response.statusText || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  const reader = response.body?.getReader?.();
  if (!reader) {
    const error = new Error('session-files stream is unavailable');
    error.status = 501;
    throw error;
  }
  const decoder = new TextDecoder();
  let streamError = null;
  let donePayload = {};
  const parser = createFileQuickOpenSseParser((event, payload) => {
    if (event === 'repo') onRepo(payload);
    else if (event === 'done') donePayload = payload || {};
    else if (event === 'error') {
      streamError = new Error(String(payload?.message || payload?.error || 'Finder session-files stream failed'));
      streamError.payload = payload;
    }
  });
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    parser.feed(decoder.decode(result.value, {stream: true}));
  }
  parser.feed(decoder.decode());
  parser.finish();
  if (streamError) throw streamError;
  if (!requestIsCurrent()) return {};
  return donePayload;
}

function renderSessionFilesDestination(destination, options = {}) {
  const visible = sessionFilesSurfaceDescriptor(destination).visible;
  if (!visible) {
    recordClientPerfCounter('sessionFilesRender', 0, {skipped: 1});
    return;
  }
  if (destination === 'finder') {
    scheduleFileTreeMetadataRefresh();
  } else {
    renderFileExplorerChangesPanels({...options, view: 'differ'});
  }
}

async function fetchSessionFiles(options = {}) {
  const surface = sessionFilesSurfaceDescriptor(options.destination, options);
  const destination = surface.destination;
  const forceRefresh = options.force === true;
  const freshGit = options.freshGit === true;
  const backgroundRefresh = options.background === true;
  const cacheOnly = options.cacheOnly === true;
  const cacheView = String(options.cacheView || '');
  const visible = surface.visible;
  if (!visible) {
    recordClientPerfCounter('sessionFilesRefresh', 0, {skipped: 1});
    return false;
  }
  if (sessionFilesLoadingForDestination(destination) && !forceRefresh) return;
  const session = surface.session;
  const state = surface.state;
  let shouldRender = options.silent !== true;
  if (!session) {
    const emptyPayload = emptySessionFilesPayload('', true, destination);
    const signature = sessionFilesPayloadSignatureForPayload(emptyPayload);
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, emptyPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(emptyPayload));
    if (shouldRender) renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
    return;
  }
  retireSessionFilesRequest(destination, 'session-files request replaced');
  const requestIsCurrent = state.guard.begin();
  const requestController = typeof AbortController === 'function' ? new AbortController() : null;
  let releaseCompletionWaiter = null;
  const completionWaiter = !backgroundRefresh ? new Promise(resolve => { releaseCompletionWaiter = resolve; }) : null;
  if (completionWaiter) state.completionWaiter = completionWaiter;
  state.abortController = requestController;
  if (!backgroundRefresh) setSessionFilesLoadingForDestination(destination, true);
  if (!options.silent) statusEl.textContent = t('status.changedFilesLoading');
  if (!options.silent) {
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
  }
  let streamMode = !cacheOnly && sessionFilesStreamSupported(destination);
  try {
    const request = sessionFilesRequestForDestination(destination, session);
    const params = new URLSearchParams(sessionFilesRequestQueryString(destination));
    params.set('session', session);
    params.set('hours', '24');
    if (forceRefresh) params.set('force', '1');
    if (freshGit) params.set('fresh_git', '1');
    if (cacheOnly) params.set('cache_only', '1');
    if (cacheOnly && /^[0-9a-f]{64}$/.test(cacheView)) params.set('cache_view', cacheView);
    const requestUrl = `/api/session-files?${params.toString()}`;
    let payload;
    try {
      if (streamMode) {
        if (destination === 'finder') fileExplorerFinderSessionFilesState.partialBackfillPainted = false;
        const retainedPayload = backgroundRefresh && sessionFilesPayloadIsLoadedForSession(state.payload, session) ? state.payload : null;
        const initializeStreamPayload = () => {
          const partialPayload = {...emptySessionFilesPayload(session, false, destination), streaming: true};
          setSessionFilesPayloadForDestination(destination, partialPayload, {invalidateRequest: false});
          setSessionFilesSignatureForDestination(destination, sessionFilesPayloadSignatureForPayload(partialPayload));
        };
        if (!retainedPayload) initializeStreamPayload();
        try {
          const donePayload = await fetchSessionFilesStream(
            `/api/session-files-stream?${params.toString()}`,
            state,
            requestIsCurrent,
            chunk => {
              if (!requestIsCurrent()) return false;
              // The first repository starts a fresh aggregate; cached files must not be appended twice.
              if (state.payload === retainedPayload) initializeStreamPayload();
              return mergeSessionFilesStreamChunk(destination, session, chunk, requestIsCurrent);
            },
          );
          if (!requestIsCurrent()) return;
          payload = finalizeSessionFilesStreamPayload(state, {
            ...donePayload,
            session,
            loaded: true,
            streaming: false,
          }, requestIsCurrent);
          if (!payload) return;
        } catch (streamError) {
          if (!requestIsCurrent()) return;
          if (![404, 501].includes(Number(streamError?.status))) throw streamError;
          streamMode = false;
          clearSessionFilesStreamState(state);
          if (state.payload !== retainedPayload) {
            const fallbackPayload = emptySessionFilesPayload(session, false, destination);
            setSessionFilesPayloadForDestination(destination, fallbackPayload, {invalidateRequest: false});
            setSessionFilesSignatureForDestination(destination, sessionFilesPayloadSignatureForPayload(fallbackPayload));
          }
          payload = await apiFetchJson(
            requestUrl,
            {deadlineMs: 5000, ...(requestController ? {signal: requestController.signal} : {})},
            {abortRetirementReason: 'session_files_request_superseded'},
          );
        }
      } else {
        payload = await apiFetchJson(
          requestUrl,
          {deadlineMs: 5000, ...(requestController ? {signal: requestController.signal} : {})},
          {abortRetirementReason: 'session_files_request_superseded'},
        );
      }
  } catch (err) {
    if (!requestIsCurrent()) return;
    // A producer can return the same accepted receipt again while its terminal result is still
      // retained locally. Reuse that exact result instead of painting a terminal Differ as queued.
      if (!isApiPendingResponse(err) || !err.operationId || !apiOperationState.terminal.has(err.operationId)) throw err;
      payload = await waitForApiOperationResult(err, {
        kind: 'session_files',
        deadlineMs: 5000,
        url: requestUrl,
        method: 'GET',
      });
    }
    const nextPayload = normalizedSessionFilesPayload(payload, {session, from_ref: request.from_ref, to_ref: request.to_ref});
    if (!requestIsCurrent()) return;
    if (backgroundRefresh && sessionFilesPayloadShouldPreserveCurrent(nextPayload, destination)) return;
    if (cacheOnly && sessionFilesPayloadShouldPreserveCurrent(nextPayload, destination)) return true;
    const applied = applySessionFilesPayloadToDestination(
      destination,
      nextPayload,
      request,
      session,
      {clearLoading: false, recordPerf: false, render: false, retireRequest: false},
    );
    shouldRender = applied || shouldRender;
    for (const mirrorDestination of sessionFilesDestinationsForRequest(request, session)) {
      if (mirrorDestination === destination) continue;
      applySessionFilesPeerPayloadToDestination(mirrorDestination, nextPayload, request, session);
    }
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    if (!options.silent) statusOk(esc(tPlural('status.changedFilesLoaded', nextPayload.files.length)));
    return true;
  } catch (err) {
    if (!requestIsCurrent()) return;
    if (cacheOnly && isApiPendingResponse(err)) return false;
    if (isApiPendingResponse(err)) {
      const nextPayload = {
        ...emptySessionFilesPayload(session, false, destination),
        refreshing: true,
        pending_key: err.key,
        pending_epoch: err.epoch,
        pending_operation_id: err.operationId,
      };
      const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
      if (!requestIsCurrent()) return;
      shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
      setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
      setSessionFilesSignatureForDestination(destination, signature);
      recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload, {queued: 1}));
      return;
    }
    const issue = userMessageSnapshot(err, String(err?.message || err)).user_message;
    const failedRefs = sessionFilesRequestForDestination(destination, session);
    const partialPayload = streamMode && state.payload?.streaming === true && state.payload?.session === session;
    const nextPayload = partialPayload
      ? {...state.payload, streaming: false, loaded: true, errors: [issue]}
      : {session, files: [], repos: [], refs_by_repo: {}, errors: [issue], from_ref: failedRefs.from_ref, to_ref: failedRefs.to_ref, loaded: true};
    if (partialPayload) {
      clearSessionFilesStreamState(state);
    }
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
    if (!options.silent) statusErr(localizedHtml('status.changedFilesFailed', {error: userMessageText(err?.payload, String(err))}));
  } finally {
    const current = requestIsCurrent();
    if (state.abortController === requestController) {
      state.abortController = null;
    }
    const wasLoading = current && sessionFilesLoadingForDestination(destination);
    if (current && !backgroundRefresh) setSessionFilesLoadingForDestination(destination, false);
    // The request owner must be cleared before releasing the Finder's first-paint gate. Otherwise
    // the completion path sees its own controller as active, leaves the gate set forever, and the
    // late session-files +/- metadata can never repaint the already-mounted fast rows.
    const finderBackfillReleased = current
      && destination === 'finder'
      && releaseFinderSessionFilesDiffBackfillIfReady();
    if (state.completionWaiter === completionWaiter) {
      state.completionWaiter = null;
      releaseCompletionWaiter?.();
    }
    if (current && (shouldRender || wasLoading || finderBackfillReleased) && visible) {
      renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
      renderPaneTabStrips();
      renderSessionButtons();
    }
  }
}

async function refreshVisibleSessionFilesSurfaces(options = {}) {
  const destinations = [];
  if (fileExplorerTreePaneIsVisible()) destinations.push('finder');
  if (fileExplorerSessionFilesPaneIsVisible()) destinations.push('differ');
  const seen = new Set();
  const requests = [];
  for (const destination of destinations) {
    const request = sessionFilesRequestForDestination(destination);
    const key = sessionFilesRequestKey(request, request.session);
    if (!request.session || seen.has(key)) continue;
    seen.add(key);
    requests.push(fetchSessionFiles({
      destination,
      session: request.session,
      silent: options.silent !== false,
      force: options.force === true,
      cacheOnly: options.cacheOnly === true,
      cacheView: options.cacheView,
    }));
  }
  await Promise.all(requests);
}

function sessionFilesCompletionIdentity(completion = {}) {
  const cacheView = String(completion.cache_view_id || '');
  const generation = String(completion.completion_generation || completion.cache_key_hash || cacheView);
  return cacheView && generation ? `${generation}\x1f${cacheView}` : '';
}

function sessionFilesCompletionCandidates(completion = {}) {
  const completedSession = String(completion.session || '');
  const requestDescriptor = String(completion.request_descriptor || '');
  if (!/^[0-9a-f]{64}$/.test(requestDescriptor)) return [];
  const destinations = [];
  if (fileExplorerTreePaneIsVisible()) destinations.push('finder');
  if (fileExplorerSessionFilesPaneIsVisible()) destinations.push('differ');
  const seen = new Set();
  return destinations.map(destination => ({destination, request: sessionFilesRequestForDestination(destination)}))
    .filter(candidate => {
      const requestKey = sessionFilesRequestKey(candidate.request, candidate.request.session);
      if (!candidate.request.session || (completedSession && candidate.request.session !== completedSession) || requestDescriptor !== sessionFilesDescriptorForDestination(candidate.destination, candidate.request) || seen.has(requestKey)) return false;
      seen.add(requestKey);
      return true;
    });
}

async function refreshSessionFilesCompletionSurfaces(completion = {}) {
  const cacheView = String(completion.cache_view_id || '');
  if (!/^[0-9a-f]{64}$/.test(cacheView)) return false;
  const completionIdentity = sessionFilesCompletionIdentity(completion);
  const attempts = [];
  for (const candidate of sessionFilesCompletionCandidates(completion)) {
    const stateKey = `${candidate.destination}\x1f${candidate.request.session}`;
    const existing = sessionFilesCompletionRevalidations.get(stateKey);
    if (existing?.identity === completionIdentity) {
      attempts.push(existing.promise || Promise.resolve(existing.acknowledged));
      continue;
    }
    const run = async () => {
      // A completion cannot be acknowledged while an ordinary request owns this destination:
      // that response may be older than the opaque view. Wait for it to settle, then read the
      // completion's single cache view without aborting either transport.
      const ordinaryRequest = sessionFilesStateForDestination(candidate.destination).completionWaiter;
      if (ordinaryRequest) await ordinaryRequest;
      const applied = await fetchSessionFiles({
        destination: candidate.destination,
        session: candidate.request.session,
        silent: true,
        cacheOnly: true,
        cacheView,
      });
      const current = sessionFilesCompletionRevalidations.get(stateKey);
      if (current?.identity !== completionIdentity) return false;
      // Both a ready response and the bounded cache-only pending response consume this
      // completion. Retrying the same opaque view on replay adds disk reads but cannot produce
      // newer data; a later producer completion has its own identity and remains eligible.
      current.acknowledged = true;
      return true;
    };
    // A newer completion is serialized after the destination's finite older cache read. Starting
    // both would abort the old response and turn normal EventSource ordering into a timing race.
    const promise = (existing?.promise ? existing.promise.then(run, run) : run());
    sessionFilesCompletionRevalidations.set(stateKey, {identity: completionIdentity, acknowledged: false, promise});
    attempts.push(promise);
  }
  const outcomes = await Promise.all(attempts);
  return outcomes.some(Boolean);
}

function applySessionFilesPayloadToDestination(destination, payload, request, session, options = {}) {
  const nextPayload = normalizedSessionFilesPayload(payload, {session, from_ref: request.from_ref, to_ref: request.to_ref});
  if (sessionFilesPayloadShouldPreserveCurrent(nextPayload, destination)) return false;
  const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
  const wasLoading = sessionFilesLoadingForDestination(destination);
  const shouldApply = signature !== sessionFilesSignatureForDestination(destination);
  const shouldRender = shouldApply || (options.clearLoading !== false && wasLoading);
  if (options.clearLoading !== false && wasLoading) setSessionFilesLoadingForDestination(destination, false);
  // The accepted payload owns the visible state, but aborting an already-dispatched finite
  // request turns a successful server response into a client-side status 0. Fence its stale commit
  // with the existing generation guard and let the transport settle normally.
  if (options.retireRequest !== false) retireSessionFilesResponseOwner(destination);
  if (shouldApply) {
    setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
  }
  sessionFilesCacheForDestination(destination).set(sessionFilesCacheKey(session, destination), {payload: nextPayload, signature});
  if (shouldApply && options.recordPerf !== false) recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
  if (shouldRender && options.render !== false) {
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
    renderSessionButtons();
  }
  return shouldApply;
}

function applySessionFilesPeerPayloadToDestination(destination, payload, request, session) {
  // Equal request parameters let an idle destination reuse this payload; they do not transfer
  // ownership away from a destination whose own request is still in flight.
  if (sessionFilesRequestIsActiveForDestination(destination)) return false;
  return applySessionFilesPayloadToDestination(destination, payload, request, session);
}

function applySessionFilesPayloadFromPush(payload = {}, request = {}) {
  const session = String(payload.session || request.session || '');
  if (!session) return false;
  const destinations = sessionFilesDestinationsForRequest(request, session);
  if (!destinations.length) return false;
  let applied = false;
  for (const destination of destinations) {
    applied = applySessionFilesPayloadToDestination(destination, payload, request, session) || applied;
  }
  if (applied && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  return applied;
}

function applySessionFilesOperationFailureToDestination(destination, result, context, session) {
  const error = result.error && typeof result.error === 'object' ? result.error : {};
  const issue = error.message && typeof error.message === 'object'
    ? {...error.message}
    : userMessageSnapshot(result, 'session-files request failed').user_message;
  const nextPayload = {
    ...emptySessionFilesPayload(session, true, destination),
    refreshing: false,
    errors: [issue],
    operation_error: error,
  };
  const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
  setSessionFilesLoadingForDestination(destination, false);
  retireSessionFilesResponseOwner(destination);
  setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
  setSessionFilesSignatureForDestination(destination, signature);
  sessionFilesCacheForDestination(destination).set(sessionFilesCacheKey(session, destination), {payload: nextPayload, signature});
  renderSessionFilesDestination(destination, {force: true});
  renderPaneTabStrips();
  renderSessionButtons();
  return true;
}

function applySessionFilesOperationFailure(result = {}, context = {}) {
  const session = String(context.session || '');
  if (!session) return false;
  const destinations = sessionFilesDestinationsForRequest(context, session);
  if (!destinations.length) return false;
  for (const destination of destinations) {
    applySessionFilesOperationFailureToDestination(destination, result, context, session);
  }
  statusErr(localizedHtml('status.changedFilesFailed', {error: userMessageText(result, 'session-files request failed')}));
  return true;
}

function handleApiOperationTerminalResult(record, result = {}) {
  if (record?.kind === 'fs_batch') {
    return typeof applyFileExplorerFsBatchOperationResult === 'function'
      && applyFileExplorerFsBatchOperationResult(record, result);
  }
  if (record?.kind === 'session_files') {
    if (result.state === 'ready' && result.data && typeof result.data === 'object') {
      return applySessionFilesPayloadFromPush(result.data, record.context || {});
    }
    if (result.state === 'failed') {
      return applySessionFilesOperationFailure(result, record.context || {});
    }
    return false;
  }
  if (record?.kind === 'context_items' || record?.kind === 'context_tail') {
    return typeof applyContextProductOperationResult === 'function'
      && applyContextProductOperationResult(record, result);
  }
  if (record?.kind === 'fs_watch_diff') {
    if (record.terminalOwner === 'filesystem-watch-diff-refresh') return true;
    if (result.state === 'ready' && result.data && typeof result.data === 'object') {
      void refreshFileExplorerFromPush(result.data, {fromWatchDiff: true});
      return true;
    }
    if (result.state === 'failed') {
      console.warn('filesystem watch diff operation failed', userMessageText(result, t('common.requestFailed')));
      return true;
    }
    return false;
  }
  return false;
}

function sessionFileTimeText(mtime) {
  const value = Number(mtime || 0);
  if (!value) return '';
  return localizedDateTimeFormat(value, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}

function compactAgeNumber(value) {
  const rounded = Math.round(Number(value || 0) * 10) / 10;
  if (!Number.isFinite(rounded)) return '0';
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

function compactElapsedDurationText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  if (total < 3600) {
    const minutes = Math.floor(total / 60);
    const remainder = total % 60;
    return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}

function compactRelativeFileTimeText(unit, countText) {
  return tPlural(unit === 'hour' ? 'relative.hour' : `relative.compact.${unit}`, Number(countText));
}

function sessionFileRelativeTimeText(mtime, nowSeconds = fileTreeRecencyNowMs() / 1000) {
  const value = Number(mtime || 0);
  if (!value) return '';
  const now = Number(nowSeconds);
  if (!Number.isFinite(now)) return '';
  const age = now - value;
  if (age <= 0) return t('relative.compact.now');
  if (age < FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS) return t('relative.compact.lessThan15Sec');
  if (age < 60) return t('relative.compact.lessThanMinute');
  if (age < 3600) {
    return compactRelativeFileTimeText('minute', String(Math.max(1, Math.round(age / 60))));
  }
  if (age < 86400) {
    const hoursText = compactAgeNumber(age / 3600);
    return compactRelativeFileTimeText('hour', hoursText);
  }
  if (age >= 365 * 86400) {
    const yearsText = Number(compactAgeNumber(age / (365 * 86400))).toFixed(1);
    return t('relative.year', {count: yearsText});
  }
  const daysText = compactAgeNumber(age / 86400);
  return compactRelativeFileTimeText('day', daysText);
}

function sessionFileMissingTimeText() {
  return '—';
}

function sessionFileDisplayTimeText(mtime, options = {}) {
  const mode = fileExplorerTreeDateModeForView(options.view || (options.differMode ? 'differ' : 'finder'));
  if (mode === 'none') return '';
  const text = mode === 'date' ? sessionFileTimeText(mtime)
    : mode === 'relative' ? sessionFileRelativeTimeText(mtime, options.nowSeconds)
      : '';
  if (!text && options.placeholderForMissingTime === true) return sessionFileMissingTimeText();
  return text;
}

function sessionFileDisplayStatus(item) {
  const status = normalizeGitStatus(item?.status || 'M');
  return item?.missing === true && !['A', '?'].includes(status) ? 'D' : status;
}

function sessionFileHasMissingTime(item) {
  return Number(item?.mtime || 0) <= 0;
}

function sessionFileDatePlaceholderNeeded(item) {
  return Boolean(item && sessionFileHasMissingTime(item));
}

function sessionFileDisplayTimeTextForEntry(entry, options = {}) {
  return sessionFileDisplayTimeText(entry?.mtime, {...options, placeholderForMissingTime: entry?.changedFileMissingTime === true});
}

function sessionFileLineCountUnit(added, removed) {
  const largest = Math.max(Number.isFinite(added) ? added : 0, Number.isFinite(removed) ? removed : 0);
  if (largest >= 999_500) return 'M';
  if (largest >= 200_000) return 'K';
  return '';
}

function sessionFileLineCountText(count, unit) {
  if (unit === 'K') return count < 1_000 ? '<1K' : `${Math.round(count / 1_000)}K`;
  if (unit === 'M') {
    const millions = Math.round(count / 100_000) / 10;
    return millions === 0 ? '<0.1M' : `${Number(millions.toFixed(1))}M`;
  }
  return String(count);
}

function sessionFileCountsAreTracked(item) {
  return item?.diff_tracked === true;
}

function sessionFileDiffText(item) {
  if (item?.binary === true) return [{kind: 'file-label', text: t('gitDiff.binary')}];
  if (item?.counts_available === false) return [{kind: 'file-label', text: t('common.notAvailable')}];
  const added = Number(item?.added);
  const removed = Number(item?.removed);
  const unit = sessionFileLineCountUnit(added, removed);
  const addKind = item?.diff_tracked === false ? 'add-neutral' : 'add';
  return [
    Number.isFinite(added) && added !== 0 ? {kind: addKind, text: `+${sessionFileLineCountText(added, unit)}`} : null,
    Number.isFinite(removed) && removed !== 0 ? {kind: 'remove', text: `-${sessionFileLineCountText(removed, unit)}`} : null,
  ].filter(Boolean);
}

// Directory status counts intentionally differ from diff numstat: +N/-N here means added/deleted FILES,
// while a leaf's existing +N/-N still means added/removed LINES.
function sessionFileStatusCountParts(counts = {}) {
  const added = Number(counts.added || 0);
  const deleted = Number(counts.deleted || 0);
  const parts = [
    Number.isFinite(added) && added > 0 ? {kind: 'add', text: `+${added}`} : null,
    Number.isFinite(deleted) && deleted > 0 ? {kind: 'remove', text: `-${deleted}`} : null,
  ].filter(Boolean);
  if (parts.length) parts.push({kind: 'file-label', text: t('common.files')});
  return parts;
}

function sortedSessionFiles(files, view = 'differ') {
  const items = Array.isArray(files) ? files.slice() : [];
  return items.sort(sessionFilesSortComparator(view));
}

function sessionFilesSortComparator(view = 'differ') {
  const uploadOrder = item => item?.uploaded === true ? 1 : 0;
  const mode = fileExplorerTreeSortModeForView(view);
  const nameCompare = (left, right) => FILE_TREE_NAME_COLLATOR.compare(String(left.path || ''), String(right.path || ''))
    || FILE_TREE_NAME_COLLATOR.compare(String(left.repo || ''), String(right.repo || ''));
  if (mode === 'az' || mode === 'za') {
    return (left, right) => uploadOrder(left) - uploadOrder(right)
      || nameCompare(left, right) * (mode === 'za' ? -1 : 1);
  }
  return (left, right) => {
    const uploadResult = uploadOrder(left) - uploadOrder(right);
    if (uploadResult !== 0) return uploadResult;
    const leftMtime = Number(left.mtime || 0);
    const rightMtime = Number(right.mtime || 0);
    const mtimeResult = mode === 'oldest' ? leftMtime - rightMtime : rightMtime - leftMtime;
    return mtimeResult || nameCompare(left, right);
  };
}

function groupedSessionFiles(files) {
  const groups = new Map();
  for (const item of files) {
    const repo = item.repo || changesOutsideRepoKey;
    if (!groups.has(repo)) groups.set(repo, []);
    groups.get(repo).push(item);
  }
  return Array.from(groups.entries());
}

function writeStoredChangesFolderCollapsed() {
  storageSet(changesFolderCollapsedStorageKey, JSON.stringify(Array.from(changesFolderCollapsed).sort()));
}

function writeStoredChangesRepoCollapsed() {
  storageSet(changesRepoCollapsedStorageKey, JSON.stringify(Array.from(changesRepoCollapsed).sort()));
}

function fileExplorerChangesRepoKeys(payload = fileExplorerSessionFilesState.payload) {
  const repos = new Set();
  for (const repo of Array.isArray(payload?.repos) ? payload.repos : []) {
    const path = normalizeDirectoryPath(repo?.repo || '');
    if (path) repos.add(path);
  }
  if (repos.size) return Array.from(repos);
  const visibleFiles = fileExplorerDifferFiles(payload);
  for (const item of visibleFiles) {
    if (item?.repo) repos.add(item.repo);
  }
  if (!repos.size && visibleFiles.length && payload?.session) repos.add(payload.session);
  return Array.from(repos);
}

function fileExplorerChangesFolderKeys(payload = fileExplorerSessionFilesState.payload) {
  const folders = new Set();
  for (const item of fileExplorerDifferFiles(payload)) {
    const repoRoot = item?.repo && item.repo !== changesOutsideRepoKey ? normalizeDirectoryPath(item.repo) : '/';
    const absPath = item?.abs_path || (item?.repo && item?.path ? `${item.repo}/${item.path}` : item?.path || '');
    if (!absPath) continue;
    let directory = normalizeDirectoryPath(dirnameOf(absPath));
    while (directory && directory !== repoRoot && pathIsInsideDirectory(directory, repoRoot)) {
      folders.add(directory);
      directory = dirnameOf(directory);
    }
  }
  return Array.from(folders).sort((left, right) => left.localeCompare(right));
}

function fileExplorerChangesAllReposCollapsed(payload = fileExplorerSessionFilesState.payload) {
  const repos = fileExplorerChangesRepoKeys(payload);
  return Boolean(repos.length) && repos.every(repo => changesRepoCollapsed.has(repo));
}

function fileExplorerChangesCollapseToggleState() {
  const collapsed = fileExplorerChangesAllReposCollapsed();
  return {
    collapsed,
    title: collapsed ? t('changes.expandAll') : t('common.collapseAll'),
    icon: collapsed ? '▾' : '▴',
  };
}

function fileExplorerChangesCollapseToggleHtml() {
  const state = fileExplorerChangesCollapseToggleState();
  return `<button type="button" class="file-explorer-header-action file-explorer-changes-collapse-toggle file-explorer-mode-diff-only" data-session-files-collapse-toggle title="${esc(state.title)}" aria-label="${esc(state.title)}" aria-pressed="${state.collapsed ? 'true' : 'false'}">${esc(state.icon)}</button>`;
}

function syncFileExplorerChangesCollapseButtons() {
  const state = fileExplorerChangesCollapseToggleState();
  for (const button of document.querySelectorAll('[data-session-files-collapse-toggle]')) {
    button.title = state.title;
    button.setAttribute('aria-label', state.title);
    button.setAttribute('aria-pressed', state.collapsed ? 'true' : 'false');
    button.textContent = state.icon;
  }
}

function setAllFileExplorerChangesCollapsed(collapsed) {
  changesRepoCollapsed = collapsed ? new Set(fileExplorerChangesRepoKeys()) : new Set();
  writeStoredChangesRepoCollapsed();
  renderFileExplorerChangesPanels({force: true});
  syncFileExplorerChangesCollapseButtons();
}

function toggleAllFileExplorerChanges() {
  setAllFileExplorerChangesCollapsed(!fileExplorerChangesAllReposCollapsed());
}

function setAllFileExplorerChangesDirectoriesExpanded(expand) {
  if (expand) {
    changesRepoCollapsed.clear();
    changesFolderCollapsed.clear();
  } else {
    changesRepoCollapsed = new Set(fileExplorerChangesRepoKeys());
    changesFolderCollapsed = new Set(fileExplorerChangesFolderKeys());
  }
  writeStoredChangesRepoCollapsed();
  writeStoredChangesFolderCollapsed();
  renderFileExplorerChangesPanels({force: true});
  syncFileExplorerChangesCollapseButtons();
}

function sessionFileIsDifferVisible(item) {
  return String(item?.status || 'M').toUpperCase() !== 'T';
}

function fileExplorerDifferFiles(payload = fileExplorerSessionFilesState.payload) {
  return (Array.isArray(payload?.files) ? payload.files : []).filter(sessionFileIsDifferVisible);
}

function changeFileTotals(files) {
  return drainFileTreeRenderSteps(changeFileTotalsSteps(files));
}

function* changeFileTotalsSteps(files) {
  let added = 0;
  let removed = 0;
  for (const item of Array.isArray(files) ? files : []) {
    yield;
    if (!sessionFileCountsAreTracked(item)) continue;
    const add = Number(item?.added);
    const remove = Number(item?.removed);
    if (Number.isFinite(add)) added += add;
    if (Number.isFinite(remove)) removed += remove;
  }
  return {added, removed};
}

function repoAggregateNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function changesRepoTotals(repoInfo, entries) {
  const fallback = Number.isFinite(Number(repoInfo?.added)) && Number.isFinite(Number(repoInfo?.removed))
    ? {added: 0, removed: 0}
    : changeFileTotals(entries);
  return {
    added: repoAggregateNumber(repoInfo?.added, fallback.added),
    removed: repoAggregateNumber(repoInfo?.removed, fallback.removed),
    count: repoAggregateNumber(repoInfo?.count, Array.isArray(entries) ? entries.length : 0),
  };
}

function changesRepoTotalsHtml(repoInfo, entries) {
  // A repo that is gone has no diff to total. State what it DID hold -- the deduped count of
  // remembered files -- so one collapsed row never stands in silently for hundreds of them.
  if (repoInfo?.missing === true) {
    const remembered = Number(repoInfo.touched_count || 0);
    return `<span class="changes-repo-totals" title="${esc(t('filetab.missingTitle'))}"><span class="changes-repo-count">${esc(tPlural('changes.fileCount', remembered))}</span></span>`;
  }
  const totals = changesRepoTotals(repoInfo, entries);
  const diffParts = sessionFileDiffText(totals);
  const diffText = diffParts.length ? diffParts.map(part => part.text).join(' ') : '+0 -0';
  const diffHtml = diffParts.map(part => `<span class="changes-diff-${part.kind}">${esc(part.text)}</span>`).join('');
  const title = `${diffText} ${tPlural('changes.fileCount', totals.count)}`;
  return `<span class="changes-repo-totals" title="${esc(title)}">${diffHtml}<span class="changes-repo-count">${totals.count}</span></span>`;
}

function diffRefDisplayText(ref) {
  const value = cleanDiffRef(ref, '');
  if (!value || value === 'default') return t('diff.ref.defaultBase');
  if (value === 'base') return t('diff.ref.base');
  if (value === 'current') return t('diff.ref.workingTree');
  return value.length > 12 && /^[0-9a-f]{13,40}$/i.test(value) ? value.slice(0, 9) : value;
}

// C15 follow-up: the compact comparison line IS the FROM/TO control — render the localized
// "Comparing {from} to {to}" sentence with the actual SHA text inputs injected in place of {from}/{to}.
// Splitting on placeholders (not hardcoding "Comparing"/"with") preserves each locale's word order.
function diffRefComparisonLineHtml(repo) {
  const refs = repoDiffRefs(repo);
  const fromInput = diffRefInputHtml({repo, compact: true, side: 'from', value: refs.from, suggestions: diffRefFromSuggestions(repo), aria: t('diff.ref.from.aria')});
  const toInput = diffRefInputHtml({repo, compact: true, side: 'to', value: refs.to, suggestions: diffRefToSuggestions(refs.from, repo), aria: t('diff.ref.to.aria')});
  // esc() leaves the {{FROM}}/{{TO}} placeholders intact (no HTML-special chars), then we swap in the
  // raw input markup — so the surrounding localized text stays escaped.
  const body = esc(t('diff.comparing', {from: '{{FROM}}', to: '{{TO}}'}))
    .replace('{{FROM}}', fromInput)
    .replace('{{TO}}', toInput);
  return `<span class="changes-repo-compare-title diff-ref-controls compact diff-ref-inline" data-diff-ref-controls data-diff-ref-repo="${esc(repo)}">${body}</span>${diffRefResetButtonHtml(refs, 'diff-ref-inline-reset')}`;
}

// C6: per-repo comparison title (from the repo payload's own effective refs), shown beside that repo's
// FROM/TO controls. Surfaces any per-repo ref fallback so the user sees why a repo defaulted.
function repoComparisonTitleHtml(repoInfo) {
  const from = diffRefDisplayText(repoInfo?.from_ref || diffRefFrom);
  const to = diffRefDisplayText(repoInfo?.to_ref || diffRefTo);
  const title = `<span class="changes-repo-compare-title">${t('diff.comparing', {from: esc(from), to: esc(to)})}</span>`;
  const errorText = messageDescriptorText(repoInfo?.error_message, repoInfo?.error || '');
  const error = errorText ? `<span class="changes-repo-refs-error">${esc(errorText)}</span>` : '';
  return `${title}${error}`;
}

function repoComparisonErrorHtml(repoInfo) {
  const error = messageDescriptorText(repoInfo?.error_message, repoInfo?.error || '');
  return error ? `<span class="changes-repo-refs-error">${esc(error)}</span>` : '';
}

function changesRepoCount(payload, files) {
  const repos = new Set();
  for (const file of Array.isArray(files) ? files : []) {
    const path = normalizeDirectoryPath(file?.repo || '');
    if (path) repos.add(path);
  }
  if (!repos.size) {
    for (const file of fileExplorerDifferFiles(payload)) {
      const path = normalizeDirectoryPath(file?.repo || '');
      if (path) repos.add(path);
    }
  }
  if (!repos.size) {
    for (const repoInfo of Array.isArray(payload?.repos) ? payload.repos : []) {
      const path = normalizeDirectoryPath(repoInfo?.repo || '');
      if (path && repoPayloadHasRenderableSection(repoInfo)) repos.add(path);
    }
  }
  return repos.size;
}

function changesSummaryHtml(payload, files, session, loading, loaded, counts = null) {
  if (loading) return changesLoadingHtml(session);
  if (!loaded) return t('state.notLoaded');
  const fileCount = Number.isInteger(counts?.fileCount) ? counts.fileCount : Array.isArray(files) ? files.length : 0;
  const repoCount = Number.isInteger(counts?.repoCount) ? counts.repoCount : changesRepoCount(payload, files);
  const repos = tPlural('changes.repoCount', repoCount);
  const count = tPlural('changes.fileCount', fileCount);
  const scope = session ? t('changes.inSession', {session: sessionLabel(session)}) : '';
  return `<span class="changes-summary-label">${esc(repos)}, ${esc(count)}${esc(scope)}</span>`;
}

function changesLoadingHtml(session = '') {
  const base = t('common.loading');
  const label = session ? sessionLabel(session) : '';
  const loadingText = label ? `${stripTrailingEllipsisText(base)} ${label}` : base;
  return `<span class="changes-loading" aria-live="polite" aria-busy="true">
    <span>${textWithMovingEllipsisHtml(loadingText, 'changes-loading-dots')}</span>
  </span>`;
}

function changesRepoMetaHtml(repoInfo, options = {}) {
  // C15 (compact header): when hideZero is set (the embedded popover), omit 0-commit ahead/behind so a
  // clean repo prints nothing instead of the redundant "Behind 0 / Ahead 0".
  const hideZero = options.hideZero === true;
  const pieces = [];
  const branch = repoBranchDisplayText(repoInfo);
  if (branch) pieces.push(`<span class="file-tree-repo-branch">${esc(branch)}</span>`);
  const behind = Number(repoInfo?.behind);
  const ahead = Number(repoInfo?.ahead);
  if (Number.isFinite(behind) && (!hideZero || behind > 0)) pieces.push(`<span>${esc(tPlural('changes.behind', behind))}</span>`);
  if (Number.isFinite(ahead) && (!hideZero || ahead > 0)) pieces.push(`<span>${esc(tPlural('changes.ahead', ahead))}</span>`);
  return pieces.length ? `<span class="changes-repo-compare-meta">${pieces.join('')}</span>` : '';
}

function repoPayloadByPath(payload) {
  const map = new Map();
  for (const repo of Array.isArray(payload?.repos) ? payload.repos : []) map.set(repo.repo || changesOutsideRepoKey, repo);
  return map;
}

function repoHasExplicitComparison(repoInfo) {
  const from = cleanDiffRef(repoInfo?.from_ref, 'default');
  const to = cleanDiffRef(repoInfo?.to_ref, 'base');
  return from !== 'default' || to !== 'base';
}

function repoPayloadHasRenderableSection(repoInfo) {
  const repo = normalizeDirectoryPath(repoInfo?.repo || '');
  return Boolean(repo && repo !== changesOutsideRepoKey);
}

function payloadHasRenderableRepoSections(payload) {
  return Array.isArray(payload?.repos) && payload.repos.some(repoPayloadHasRenderableSection);
}

function changesComparisonHeaderHtml(payload, files, options = {}) {
  const loaded = payload?.loaded === true;
  const loading = options.loading === true;
  if (loading && options.inlineLoading === true) return '';
  if (options.compact) {
    if (loading) return `<section class="changes-comparison-head compact">${changesLoadingHtml(payload?.session || '')}</section>`;
    if (!loaded) return `<section class="changes-comparison-head compact">${esc(t('state.notLoaded'))}</section>`;
    return '';
  }
  const summary = options.deferSummary === true ? '' : changesSummaryHtml(payload, files, payload?.session || '', loading, loaded);
  return `<section class="changes-comparison-head">
    <div class="changes-comparison-summary">${summary}</div>
  </section>`;
}

// Build a synthetic file-tree entry structure from flat session file items so renderTreeChildren
// can render the Differ using the same DOM-mutation logic as the Finder.

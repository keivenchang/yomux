// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Finder repository-history tab. Historical files continue through the existing Editor renderer.

function gitDiffTabLabel(item) {
  const path = gitDiffItemPath(item);
  const state = gitDiffTabState.get(item);
  // normalizeDirectoryPath('/') is '/', so normalizing an empty async repo first would
  // incorrectly hide the item's already-known repository path behind the filesystem root.
  const rawRepo = String(state?.repo || '').trim();
  const repo = rawRepo ? normalizeDirectoryPath(rawRepo) : '';
  const name = basenameOf(repo || path);
  const relativePath = String(state?.relativePath || '');
  return name ? `Δ${name}${relativePath ? `;${relativePath}` : ''}` : 'Δ';
}

function newGitDiffTabState(item, defaults = {}) {
  const path = gitDiffItemPath(item);
  return {
    item,
    path,
    repo: '',
    branch: '',
    repositoryIdentity: '',
    shallowBoundary: '',
    relativePath: '',
    hostedRemote: null,
    detached: false,
    dirtyCount: null,
    dirtyEntries: [],
    dirtyEntriesTruncated: false,
    worktreeStatus: null,
    worktreeStatusExpanded: false,
    head: '',
    snapshotCursor: '',
    commits: [],
    visibleCommitCount: 0,
    nextCursor: '',
    truncated: false,
    truncationReason: '',
    loaded: false,
    loadAttempted: false,
    loading: false,
    loadingOlder: false,
    error: null,
    expanded: new Set(),
    commitRecords: new Map(),
    worktreeTreeState: {scopeKey: null, collapsedDirectories: null, focusedFilePath: null},
    historyGuard: makeGenerationGuard(),
    historyController: null,
    worktreeController: null,
    viewportObserver: null,
    statsGuard: makeGenerationGuard(),
    statsController: null,
    statsControllers: new Set(),
    statsLoading: false,
    statsExhausted: false,
    statsStage: 0,
    statsError: null,
    statsBackfillTimer: null,
    statsBySha: new Map(),
    statsViewportStart: -1,
    statsViewportEnd: -1,
    statsFailedRanges: new Set(),
    historyRefreshTimer: null,
    focusedSha: '',
    ...defaults,
  };
}

function ensureGitDiffTabState(item, defaults = null) {
  const path = gitDiffItemPath(item);
  if (!path) return null;
  let state = gitDiffTabState.get(item);
  if (!state) {
    state = newGitDiffTabState(item, defaults || {});
    gitDiffTabState.set(item, state);
  } else if (defaults && typeof defaults === 'object') {
    Object.assign(state, defaults);
  }
  return state;
}

function invalidateGitDiffDetailRequests(state) {
  for (const record of state?.commitRecords?.values?.() || []) {
    cancelGitDiffCommitRecordLoad(record);
  }
}

function gitDiffCommitDetailKey(state, sha) {
  return JSON.stringify([
    String(state?.repositoryIdentity || ''),
    String(state?.repo || ''),
    String(state?.relativePath || ''),
    String(sha || ''),
    String(state?.shallowBoundary || ''),
  ]);
}

function gitDiffCommitScopeResolved(state) {
  return Boolean(state?.repositoryIdentity || state?.repo);
}

function gitDiffCommitScopeCanRefine(previousKey, nextKey) {
  try {
    const previous = JSON.parse(previousKey);
    const next = JSON.parse(nextKey);
    return Array.isArray(previous) && Array.isArray(next)
      && previous.length === 5 && next.length === 5
      && previous[0] === ''
      && previous[3] === next[3]
      && (previous[4] === next[4] || (!previous[1] && !previous[2] && !previous[4]))
      && ((previous[1] === next[1] && previous[2] === next[2] && next[0] !== '')
        || (!previous[1] && !previous[2] && Boolean(next[0] || next[1] || next[2])));
  } catch {
    return false;
  }
}

function newGitDiffCommitRecord(detailKey, scopeResolved) {
  return {
    detailKey,
    scopeResolved,
    detail: null,
    error: null,
    loading: null,
    guard: makeGenerationGuard(),
    controller: null,
    collapsedDirectories: null,
    focusedFilePath: null,
  };
}

function clearGitDiffCommitRecord(state, sha) {
  const record = state?.commitRecords?.get?.(sha);
  if (!record) return;
  cancelGitDiffCommitRecordLoad(record);
  state.commitRecords.delete(sha);
}

function cancelGitDiffCommitRecordLoad(record) {
  record.controller?.abort?.();
  record.controller = null;
  record.guard?.invalidate?.();
  record.loading = null;
}

function resetGitDiffCommitRecordDetail(record) {
  cancelGitDiffCommitRecordLoad(record);
  record.detail = null;
  record.error = null;
}

function reconcileGitDiffCommitRecordScope(state, sha, record) {
  const key = gitDiffCommitDetailKey(state, sha);
  const scopeResolved = gitDiffCommitScopeResolved(state);
  if (record.detailKey === key) {
    if (scopeResolved) record.scopeResolved = true;
    return true;
  }
  if (gitDiffCommitScopeCanRefine(record.detailKey, key)) {
    record.detailKey = key;
    resetGitDiffCommitRecordDetail(record);
    record.scopeResolved = scopeResolved;
    return true;
  }
  clearGitDiffCommitRecord(state, sha);
  return false;
}

function gitDiffCommitRecordFor(state, sha) {
  if (!state || !sha) return null;
  const key = gitDiffCommitDetailKey(state, sha);
  const scopeResolved = gitDiffCommitScopeResolved(state);
  let record = state.commitRecords.get(sha);
  if (record && !reconcileGitDiffCommitRecordScope(state, sha, record)) record = null;
  if (!record) {
    record = newGitDiffCommitRecord(key, scopeResolved);
    state.commitRecords.set(sha, record);
  }
  return record;
}

function gitDiffWorktreeTreeScopeKey(state) {
  if (!gitDiffCommitScopeResolved(state)) return null;
  return JSON.stringify([
    String(state?.repositoryIdentity || ''),
    String(state?.repo || ''),
    String(state?.relativePath || ''),
  ]);
}

function resetGitDiffWorktreeTreeState(state, scopeKey = null) {
  state.worktreeTreeState = {scopeKey, collapsedDirectories: null, focusedFilePath: null};
}

function syncGitDiffWorktreeTreeScope(state) {
  const scopeKey = gitDiffWorktreeTreeScopeKey(state);
  if (scopeKey === null) return;
  const treeState = state.worktreeTreeState;
  if (treeState.scopeKey && treeState.scopeKey !== scopeKey) {
    const prior = JSON.parse(treeState.scopeKey);
    const next = JSON.parse(scopeKey);
    const identityRefinement = prior[0] === '' && next[0] !== '' && prior[1] === next[1] && prior[2] === next[2];
    if (identityRefinement) treeState.scopeKey = scopeKey;
    else resetGitDiffWorktreeTreeState(state, scopeKey);
  } else if (!treeState.scopeKey) {
    treeState.scopeKey = scopeKey;
  }
}

function cleanupGitDiffTab(item) {
  const state = gitDiffTabState.get(item);
  if (state?.historyRefreshTimer) clearTimeout(state.historyRefreshTimer);
  if (state?.statsBackfillTimer) clearTimeout(state.statsBackfillTimer);
  state?.historyController?.abort?.();
  state?.worktreeController?.abort?.();
  state?.viewportObserver?.disconnect?.();
  state?.historyGuard?.invalidate?.();
  state?.statsController?.abort?.();
  for (const controller of state?.statsControllers || []) controller.abort?.();
  state?.statsGuard?.invalidate?.();
  invalidateGitDiffDetailRequests(state);
  gitDiffTabState.delete(item);
}

const gitDiffHistoryRefreshIntervalMs = 10000;
const gitDiffHistoryStatsSchedules = [[1], [1, 2], [4, 4, 4, 4]];

function gitDiffProgressiveHistorySupported() {
  return [bootstrap.backendLabel, bootstrap.backend].some(value => /^(?:Go|Python)(?:\s|$)/.test(String(value || '').trim()));
}

function scheduleGitDiffHistoryRefresh(item) {
  const state = ensureGitDiffTabState(item);
  if (!state) return;
  if (state.historyRefreshTimer) clearTimeout(state.historyRefreshTimer);
  state.historyRefreshTimer = setTimeout(() => {
    state.historyRefreshTimer = null;
    const panel = panelNodes.get(item);
    if (!panel?.isConnected || !state.loaded || state.loading || state.loadingOlder) {
      if (panel?.isConnected && state.loaded) scheduleGitDiffHistoryRefresh(item);
      return;
    }
    if (state.nextCursor || state.visibleCommitCount < state.commits.length) {
      scheduleGitDiffHistoryRefresh(item);
      return;
    }
    void refreshGitDiffHistory(item, {refresh: true});
  }, gitDiffHistoryRefreshIntervalMs);
}

function gitDiffHistoryPageSize(body) {
  const row = body?.querySelector?.('.git-diff-commit-row');
  const rowHeight = row?.getBoundingClientRect?.().height || 24;
  const availableHeight = body?.clientHeight || 0;
  if (!rowHeight || !availableHeight) return gitDiffHistoryMinimumPageSize;
  return Math.max(gitDiffHistoryMinimumPageSize, Math.ceil(availableHeight / rowHeight));
}

function gitDiffHistoryUrl(path, cursor = '', limit = gitDiffHistoryMinimumPageSize, includeStats = false, statsOffset = null, startOffset = null) {
  const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
  const statsSuffix = includeStats ? '&stats=1' : (gitDiffProgressiveHistorySupported() ? '&worktree=0' : '');
  const offsetSuffix = Number.isSafeInteger(statsOffset) && statsOffset >= 0 ? `&stats_offset=${statsOffset}` : '';
  const startSuffix = Number.isSafeInteger(startOffset) && startOffset >= 0 ? `&start_offset=${startOffset}` : '';
  return `/api/fs/git-history?path=${encodeURIComponent(path)}&limit=${Math.max(gitDiffHistoryMinimumPageSize, Math.floor(limit))}${suffix}${statsSuffix}${offsetSuffix}${startSuffix}`;
}

function gitDiffCommitUrl(path, sha, head, repositoryIdentity = '') {
  const suffix = repositoryIdentity ? `&repository_identity=${encodeURIComponent(repositoryIdentity)}` : '';
  return `/api/fs/git-commit?path=${encodeURIComponent(path)}&commit=${encodeURIComponent(sha)}&head=${encodeURIComponent(head)}${suffix}`;
}

function gitDiffErrorSnapshot(error) {
  return userMessageSnapshot(error, {key: 'common.requestFailed', params: {}, fallback: t('common.requestFailed')});
}

function gitDiffHistoryCursorIsInvalid(error) {
  return String(error?.payload?.user_message?.key || '') === 'fs.error.gitHistoryCursor';
}

function gitDiffHistorySnapshotIsStale(error) {
  return error?.code === 'git_history_stale'
    || error?.payload?.error_code === 'git_history_stale'
    || String(error?.payload?.message_key || error?.payload?.user_message?.key || '') === 'fs.error.gitHistoryStale';
}

function gitDiffHistoryPayloadIsValid(payload) {
  return Boolean(payload && typeof payload === 'object'
    && typeof payload.path === 'string'
    && typeof payload.repo === 'string'
    && typeof payload.relative_path === 'string'
    && typeof payload.shallow_boundary === 'string' && payload.shallow_boundary.length > 0
    && (payload.branch === undefined || typeof payload.branch === 'string')
    && typeof payload.head === 'string'
    && (payload.hosted_remote === undefined || payload.hosted_remote === null || (
      typeof payload.hosted_remote === 'object'
      && ['github', 'gitlab'].includes(payload.hosted_remote.provider)
      && typeof payload.hosted_remote.base_url === 'string'
    ))
    && (payload.snapshot_cursor === undefined || typeof payload.snapshot_cursor === 'string')
    && (payload.branch === undefined || typeof payload.branch === 'string')
    && (payload.detached === undefined || typeof payload.detached === 'boolean')
    && (payload.dirty_count === undefined || payload.dirty_count === null || (Number.isSafeInteger(payload.dirty_count) && payload.dirty_count >= 0))
    && (payload.dirty_entries === undefined || (Array.isArray(payload.dirty_entries) && payload.dirty_entries.every(entry => entry && typeof entry.status === 'string' && typeof entry.path === 'string' && (entry.old_path === undefined || typeof entry.old_path === 'string'))))
    && (payload.dirty_entries_truncated === undefined || typeof payload.dirty_entries_truncated === 'boolean')
    && Array.isArray(payload.commits)
    && typeof payload.next_cursor === 'string'
    && (payload.worktree_status === undefined || gitDiffWorktreeStatusIsValid(payload.worktree_status)));
}

function gitDiffWorktreeStatusIsValid(status) {
  return Boolean(status && typeof status === 'object'
    && typeof status.available === 'boolean'
    && typeof status.dirty === 'boolean'
    && Array.isArray(status.entries)
    && status.entries.every(entry => entry && typeof entry === 'object'
      && typeof entry.status === 'string' && typeof entry.path === 'string')
    && (status.status_counts === undefined || (status.status_counts && typeof status.status_counts === 'object')));
}

function gitDiffCommitPayloadIsValid(payload, sha) {
  return Boolean(payload && typeof payload === 'object'
    && payload.sha === sha
    && typeof payload.repo === 'string'
    && typeof payload.shallow_boundary === 'string' && payload.shallow_boundary.length > 0
    && typeof payload.from_ref === 'string'
    && typeof payload.to_ref === 'string'
    && Array.isArray(payload.parents)
    && Array.isArray(payload.files)
    && typeof payload.message === 'string');
}

function gitDiffInvalidResponseError() {
  const error = new Error('invalid_response_contract');
  error.code = 'invalid_response_contract';
  return error;
}

function requireGitDiffProductBoundary(state, payload) {
  if (typeof payload?.shallow_boundary === 'string' && payload.shallow_boundary.length > 0
      && payload.shallow_boundary === state.shallowBoundary) return;
  const error = new Error(t('gitDiff.staleSnapshot'));
  error.code = 'git_history_stale';
  throw error;
}

function mergeGitDiffCommits(current, incoming) {
  const seen = new Set();
  return [...(current || []), ...(incoming || [])].filter(commit => {
    const sha = String(commit?.sha || '');
    if (!sha || seen.has(sha)) return false;
    seen.add(sha);
    return true;
  });
}

function gitDiffCommitStatsComplete(commit) {
  return ['files', 'added', 'removed', 'binary_files'].every(key => Number.isSafeInteger(commit?.[key]) && commit[key] >= 0);
}

function gitDiffCommitEnrichmentComplete(commit) {
  const goBackend = /^Go(?:\s|$)/.test(String(bootstrap.backendLabel || bootstrap.backend || '').trim());
  return gitDiffCommitStatsComplete(commit) && (!goBackend || (typeof commit?.signature_status === 'string' && commit.signature_status.length > 0));
}

function mergeGitDiffHistoryCommits(current, incoming) {
  const prior = new Map((current || []).map(commit => [String(commit?.sha || ''), commit]));
  return mergeGitDiffCommits([], incoming).map(commit => {
    const old = prior.get(String(commit?.sha || ''));
    if (!old || gitDiffCommitStatsComplete(commit)) return commit;
    return {...commit, ...Object.fromEntries(['files', 'added', 'removed', 'binary_files', 'signature_status']
      .filter(key => Object.prototype.hasOwnProperty.call(old, key)).map(key => [key, old[key]]))};
  });
}

function gitDiffHistoryNeedsStats(state) {
  return Boolean(state?.commits?.some(commit => !gitDiffCommitEnrichmentComplete(commit)));
}

function applyGitDiffStatsCache(state) {
  if (!state?.statsBySha?.size) return;
  state.commits = state.commits.map(commit => {
    if (gitDiffCommitStatsComplete(commit)) return commit;
    const stats = state.statsBySha.get(String(commit?.sha || ''));
    if (!stats || !gitDiffCommitStatsComplete(stats)) return commit;
    return {...commit, files: stats.files, added: stats.added, removed: stats.removed, binary_files: stats.binary_files, signature_status: stats.signature_status};
  });
}

function pruneGitDiffShaState(state, shas) {
  for (const sha of [...state.expanded]) if (!shas.has(sha)) state.expanded.delete(sha);
  for (const [sha, record] of [...state.commitRecords]) {
    if (!shas.has(sha)) {
      clearGitDiffCommitRecord(state, sha);
      continue;
    }
    reconcileGitDiffCommitRecordScope(state, sha, record);
  }
  syncGitDiffWorktreeTreeScope(state);
  if (state.focusedSha && !shas.has(state.focusedSha)) state.focusedSha = '';
}

async function refreshGitDiffHistory(item, options = {}) {
  const state = ensureGitDiffTabState(item);
  if (!state) return false;
  const append = options.append === true;
  if (append && (!state.nextCursor || state.loading || state.loadingOlder)) return false;
  state.historyController?.abort?.();
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  state.historyController = controller;
  const isCurrent = state.historyGuard.begin();
  const frozenHead = state.head;
  const cursor = append ? state.nextCursor : (!options.refresh && !state.loaded ? state.snapshotCursor : '');
  if (!append) {
    state.worktreeController?.abort?.();
    state.worktreeController = null;
    invalidateGitDiffDetailRequests(state);
    state.statsController?.abort?.();
    for (const controller of state.statsControllers) controller.abort?.();
    state.statsControllers.clear();
    state.statsGuard.invalidate();
    state.statsController = null;
    state.statsLoading = false;
    if (state.statsBackfillTimer) clearTimeout(state.statsBackfillTimer);
    state.statsBackfillTimer = null;
    state.statsExhausted = false;
    state.statsStage = 0;
    state.statsError = null;
    state.statsBySha.clear();
    state.statsViewportStart = -1;
    state.statsViewportEnd = -1;
    state.statsFailedRanges.clear();
    state.loadAttempted = true;
    state.loading = true;
  } else {
    state.loadingOlder = true;
  }
  state.error = null;
  renderGitDiffPanel(item);
  try {
    const panel = panelNodes.get(item);
    const body = panel?.querySelector?.('.git-diff-panel-body');
    const pageSize = gitDiffHistoryPageSize(body);
    const payload = await apiFetchJson(gitDiffHistoryUrl(state.path, cursor, pageSize), {
      cache: 'no-store',
      ...(controller ? {signal: controller.signal} : {}),
    });
    if (!isCurrent()) return false;
    if (!gitDiffHistoryPayloadIsValid(payload)) throw gitDiffInvalidResponseError();
    if (append && payload.head !== frozenHead) {
      const error = new Error(t('gitDiff.staleSnapshot'));
      error.code = 'git_history_stale';
      throw error;
    }
    if (append) requireGitDiffProductBoundary(state, payload);
    const previousScope = gitDiffCommitDetailKey(state, '');
    state.path = normalizeDirectoryPath(payload.path) || state.path;
    state.repo = normalizeDirectoryPath(payload.repo);
    state.branch = String(payload.branch || '');
    state.repositoryIdentity = String(payload.repository_identity || '');
    state.shallowBoundary = String(payload.shallow_boundary || '');
    state.relativePath = payload.relative_path;
    if (previousScope !== gitDiffCommitDetailKey(state, '')) {
      state.commits = [];
      state.statsBySha.clear();
    }
    state.hostedRemote = payload.hosted_remote || null;
    state.branch = typeof payload.branch === 'string' ? payload.branch : '';
    state.detached = payload.detached === true;
    state.dirtyCount = Number.isSafeInteger(payload.dirty_count) && payload.dirty_count >= 0 ? payload.dirty_count : null;
    state.dirtyEntries = Array.isArray(payload.dirty_entries) ? payload.dirty_entries : [];
    state.dirtyEntriesTruncated = payload.dirty_entries_truncated === true;
    if (gitDiffWorktreeStatusIsValid(payload.worktree_status)) state.worktreeStatus = payload.worktree_status;
    if (state.head && state.head !== payload.head) invalidateGitDiffDetailRequests(state);
    state.head = payload.head;
    state.commits = append ? mergeGitDiffCommits(state.commits, payload.commits) : mergeGitDiffHistoryCommits(state.commits, payload.commits);
    applyGitDiffStatsCache(state);
    if (append) state.visibleCommitCount = Math.min(state.commits.length, state.visibleCommitCount + pageSize);
    else state.visibleCommitCount = Math.min(state.commits.length, pageSize);
    state.snapshotCursor = String(payload.snapshot_cursor || (!append ? cursor : state.snapshotCursor) || '');
    state.nextCursor = payload.next_cursor;
    state.truncated = payload.truncated === true;
    state.truncationReason = String(payload.truncation_reason || '');
    state.loaded = true;
    state.error = null;
    scheduleGitDiffHistoryRefresh(item);
    renderPaneTabStrips();
    refreshPaneTabLabel(item);
    if (itemInLayout(tabberItemId)) refreshTabberPanels();
    if (!append) pruneGitDiffShaState(state, new Set(state.commits.map(commit => String(commit?.sha || '')).filter(Boolean)));
    refreshLayoutUrlStateSoon();
    return true;
  } catch (error) {
    if (!isCurrent() || error?.name === 'AbortError') return false;
    if (append && gitDiffHistorySnapshotIsStale(error)) {
      state.nextCursor = '';
      state.snapshotCursor = '';
      state.error = null;
      return refreshGitDiffHistory(item, {refresh: true});
    }
    if (!append && cursor && (gitDiffHistoryCursorIsInvalid(error) || gitDiffHistorySnapshotIsStale(error))) {
      // A saved layout can outlive the server's cursor format or Git history. Drop that opaque
      // cursor once and reload the current snapshot instead of leaving the restored Diff tab failed.
      state.snapshotCursor = '';
      state.nextCursor = '';
      state.error = null;
      return refreshGitDiffHistory(item, {refresh: true});
    }
    state.error = gitDiffErrorSnapshot(error);
    if (state.loaded) scheduleGitDiffHistoryRefresh(item);
    return false;
  } finally {
    if (isCurrent()) {
      state.loading = false;
      state.loadingOlder = false;
      if (state.historyController === controller) state.historyController = null;
      renderGitDiffPanel(item);
      if (state.loaded && !state.error) backfillGitDiffVisibleCommitDetails(item);
      if (state.loaded && !state.error && gitDiffHistoryNeedsStats(state)) scheduleGitDiffHistoryStatsBackfill(item);
      if (!append && state.loaded && !state.error && gitDiffProgressiveHistorySupported()) void refreshGitDiffWorktree(item);
    }
  }
}

async function refreshGitDiffWorktree(item) {
  const state = gitDiffTabState.get(item);
  if (!state) return;
  state.worktreeController?.abort?.();
  const controller = new AbortController();
  state.worktreeController = controller;
  const identity = state.repositoryIdentity;
  const isCurrent = () => gitDiffTabState.get(item) === state && state.worktreeController === controller && state.repositoryIdentity === identity;
  try {
    const payload = await apiFetchJson(`/api/fs/git-history?path=${encodeURIComponent(state.path)}&worktree=only`, {cache: 'no-store', signal: controller.signal});
    if (!isCurrent()) return;
    if (payload.repository_identity !== identity) throw gitDiffInvalidResponseError();
    if (gitDiffWorktreeStatusIsValid(payload.worktree_status)) state.worktreeStatus = payload.worktree_status;
    else if (Number.isSafeInteger(payload.dirty_count) && payload.dirty_count >= 0 && Array.isArray(payload.dirty_entries)) {
      state.dirtyCount = payload.dirty_count;
      state.dirtyEntries = payload.dirty_entries;
      state.dirtyEntriesTruncated = payload.dirty_entries_truncated === true;
    } else throw gitDiffInvalidResponseError();
  } catch (error) {
    if (!isCurrent() || error?.name === 'AbortError') return;
    state.error = gitDiffErrorSnapshot(error);
  } finally {
    if (isCurrent()) {
      state.worktreeController = null;
      renderGitDiffPanel(item);
    }
  }
}

function applyGitDiffStatsPayload(item, state, payload) {
  requireGitDiffProductBoundary(state, payload);
  for (const commit of payload.commits || []) {
    const sha = String(commit?.sha || '');
    if (sha && gitDiffCommitStatsComplete(commit)) state.statsBySha.set(sha, commit);
  }
  const changedCommits = [];
  state.commits = state.commits.map(commit => {
    const stats = state.statsBySha.get(String(commit?.sha || ''));
    if (!stats || !gitDiffCommitStatsComplete(stats)) return commit;
    const changed = !gitDiffCommitStatsComplete(commit)
      || ['files', 'added', 'removed', 'binary_files', 'signature_status'].some(key => commit[key] !== stats[key]);
    if (!changed) return commit;
    const updated = {...commit, files: stats.files, added: stats.added, removed: stats.removed, binary_files: stats.binary_files, signature_status: stats.signature_status};
    changedCommits.push(updated);
    return updated;
  });
  patchGitDiffCommitStatsRows(item, changedCommits);
  return changedCommits.map(commit => String(commit.sha || '')).filter(Boolean);
}

function gitDiffStatsRequest(state, url) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  if (controller) {
    state.statsControllers.add(controller);
    if (!state.statsController) state.statsController = controller;
  }
  return apiFetchJson(url, {
    cache: 'no-store',
    ...(controller ? {signal: controller.signal} : {}),
  }).finally(() => {
    if (controller) {
      state.statsControllers.delete(controller);
      if (state.statsController === controller) state.statsController = [...state.statsControllers][0] || null;
    }
  });
}

function gitDiffStatsStreamRequest(state, url, onChunk) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  if (controller) {
    state.statsControllers.add(controller);
    state.statsController = controller;
  }
  const fetchOptions = {
    cache: 'no-store',
    headers: {Accept: 'text/event-stream'},
    ...(controller ? {signal: controller.signal} : {}),
  };
  return (async () => {
    const response = await apiFetch(url, fetchOptions, {recordDebug: true});
    if (!response.ok) {
      const error = new Error(response.statusText || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    const reader = response.body?.getReader?.();
    if (!reader) return response.json();
    const decoder = new TextDecoder();
    let streamError = null;
    let donePayload = {};
    const parser = createFileQuickOpenSseParser((event, payload) => {
      if (event === 'chunk') onChunk(payload);
      else if (event === 'done') donePayload = payload || {};
      else if (event === 'error') {
        streamError = new Error(String(payload?.message || payload?.error || 'Git history stats stream failed'));
        streamError.payload = payload;
        streamError.code = String(payload?.error_code || '');
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
    return donePayload;
  })().finally(() => {
    if (controller) {
      state.statsControllers.delete(controller);
      if (state.statsController === controller) state.statsController = null;
    }
  });
}

function gitDiffHistoryViewportIndices(item, state) {
  const body = panelNodes.get(item)?.querySelector?.('.git-diff-panel-body');
  if (!body?.isConnected || !body.clientHeight) return null;
  const viewport = body.getBoundingClientRect();
  const indices = new Map(state.commits.map((commit, index) => [String(commit?.sha || ''), index]));
  let first = -1;
  let last = -1;
  for (const group of body.querySelectorAll('.git-diff-commit[data-git-diff-commit-group]')) {
    const index = indices.get(group.dataset.gitDiffCommitGroup);
    if (index === undefined) continue;
    const rect = group.getBoundingClientRect();
    if (rect.bottom <= viewport.top || rect.top >= viewport.bottom) continue;
    if (first < 0) first = index;
    last = index;
  }
  if (first < 0) return null;
  return {first: Math.max(0, first - 2), visibleFirst: first, visibleLast: last, last: Math.min(state.commits.length - 1, last + 2)};
}

function gitDiffVisibleCommitShas(item, state) {
  const viewport = gitDiffHistoryViewportIndices(item, state);
  if (!viewport) return new Set();
  return new Set(state.commits.slice(viewport.visibleFirst, viewport.visibleLast + 1)
    .map(commit => String(commit?.sha || '')).filter(Boolean));
}

function backfillGitDiffVisibleCommitDetails(item) {
  const state = ensureGitDiffTabState(item);
  if (!state?.loaded || !state.head) return false;
  const visibleCommits = gitDiffVisibleCommitShas(item, state);
  const visible = new Set();
  for (const sha of visibleCommits) if (state.expanded.has(sha)) visible.add(sha);
  for (const [sha, record] of state.commitRecords) {
    if (record.loading && !visible.has(sha)) cancelGitDiffCommitRecordLoad(record);
  }
  for (const sha of visible) {
    const record = gitDiffCommitRecordFor(state, sha);
    if (!record.detail && !record.loading) void loadGitDiffCommitDetail(item, sha);
  }
  return visible.size > 0;
}

async function backfillGitDiffHistoryViewportStats(item) {
  const state = ensureGitDiffTabState(item);
  if (!state?.loaded || state.loading || !state.head || !state.snapshotCursor) return false;
  const viewport = gitDiffHistoryViewportIndices(item, state);
  if (!viewport) {
    state.statsController?.abort?.();
    state.statsGuard.invalidate();
    state.statsLoading = false;
    return false;
  }
  const missing = index => !state.statsFailedRanges.has(index) && !gitDiffCommitEnrichmentComplete(state.commits[index]);
  let target = -1;
  for (let index = viewport.visibleFirst; index <= viewport.visibleLast; index++) {
    if (missing(index)) { target = index; break; }
  }
  if (target < 0) {
    for (let index = viewport.first; index <= viewport.last; index++) {
      if (missing(index)) { target = index; break; }
    }
  }
  if (target < 0) {
    state.statsController?.abort?.();
    state.statsGuard.invalidate();
    state.statsLoading = false;
    state.statsViewportStart = -1;
    return false;
  }
  if (state.statsLoading && target >= state.statsViewportStart && target < state.statsViewportEnd) return false;
  const start = target;
  const batchSizes = [];
  let end = start;
  for (const size of gitDiffHistoryStatsSchedules[Math.min(state.statsStage, 2)]) {
    let count = 0;
    while (count < size && end <= viewport.last && missing(end)) { count++; end++; }
    if (count) batchSizes.push(count);
    if (count < size) break;
  }
  const batches = new Map();
  let offset = start;
  for (const size of batchSizes) { batches.set(offset, size); offset += size; }
  state.statsController?.abort?.();
  const isCurrent = state.statsGuard.begin();
  const requestedHead = state.head;
  const identity = state.repositoryIdentity;
  const expected = state.commits.slice(start, end).map(commit => String(commit?.sha || ''));
  state.statsLoading = true;
  state.statsViewportStart = start;
  state.statsViewportEnd = end;
  let received = false;
  try {
    await gitDiffStatsStreamRequest(
      state,
      `/api/fs/git-history-stats-stream?path=${encodeURIComponent(state.path)}&cursor=${encodeURIComponent(state.snapshotCursor)}&offset=${start}&batch_sizes=${batchSizes.join(',')}`,
      payload => {
        if (!isCurrent() || state.head !== requestedHead) return;
        const commits = payload?.commits;
        if (!gitDiffHistoryPayloadIsValid(payload) || payload.head !== requestedHead || payload.repository_identity !== identity
          || !batches.has(payload.start_offset) || !Array.isArray(commits) || commits.length !== batches.get(payload.start_offset)
          || commits.some((commit, index) => !gitDiffCommitEnrichmentComplete(commit) || commit?.sha !== expected[payload.start_offset - start + index])) throw gitDiffInvalidResponseError();
        received = true;
        applyGitDiffStatsPayload(item, state, payload);
      },
    );
    if (isCurrent()) {
      if (expected.some((sha, index) => !gitDiffCommitEnrichmentComplete(state.commits[start + index]))) throw gitDiffInvalidResponseError();
      state.statsStage++;
    }
    return received;
  } catch (error) {
    if (isCurrent() && gitDiffHistorySnapshotIsStale(error)) return refreshGitDiffHistory(item, {refresh: true});
    if (isCurrent() && error?.name !== 'AbortError') {
      for (let index = start; index < end; index++) if (!gitDiffCommitEnrichmentComplete(state.commits[index])) state.statsFailedRanges.add(index);
      state.statsError = gitDiffErrorSnapshot(error);
      renderGitDiffPanel(item);
    }
    return false;
  } finally {
    if (isCurrent()) {
      state.statsLoading = false;
      state.statsViewportStart = -1;
      state.statsViewportEnd = -1;
      scheduleGitDiffHistoryStatsBackfill(item);
    }
  }
}

async function backfillGitDiffHistoryStats(item) {
  const state = ensureGitDiffTabState(item);
  if (!state || !state.loaded || !state.head || state.statsExhausted || !gitDiffHistoryNeedsStats(state) || state.statsLoading) return false;
  const isCurrent = state.statsGuard.begin();
  const requestedHead = state.head;
  let changed = false;
  state.statsLoading = true;
  try {
    const payload = await gitDiffStatsRequest(state, gitDiffHistoryUrl(state.path, '', 200, true));
    if (!isCurrent() || state.head !== requestedHead) return false;
    if (!gitDiffHistoryPayloadIsValid(payload) || payload.head !== requestedHead) throw gitDiffInvalidResponseError();
    changed = applyGitDiffStatsPayload(item, state, payload).length > 0;
    state.statsExhausted = true;
    return changed;
  } catch (error) {
    if (!isCurrent() || error?.name === 'AbortError') return false;
    if (gitDiffHistorySnapshotIsStale(error)) return refreshGitDiffHistory(item, {refresh: true});
    return false;
  } finally {
    if (isCurrent()) {
      state.statsLoading = false;
      state.statsController = null;
      renderGitDiffPanel(item);
    }
  }
}

function scheduleGitDiffHistoryStatsBackfill(item) {
  const state = ensureGitDiffTabState(item);
  if (!state || state.loading || state.statsBackfillTimer || !gitDiffHistoryNeedsStats(state)) return false;
  if (!gitDiffProgressiveHistorySupported() && (state.statsLoading || state.statsExhausted)) return false;
  state.statsBackfillTimer = setTimeout(() => {
    state.statsBackfillTimer = null;
    if (gitDiffProgressiveHistorySupported()) void backfillGitDiffHistoryViewportStats(item);
    else void backfillGitDiffHistoryStats(item);
  }, 0);
  return true;
}

function loadOlderGitDiffHistory(item) {
  const state = ensureGitDiffTabState(item);
  if (!state) return false;
  const pageSize = gitDiffHistoryPageSize(panelNodes.get(item)?.querySelector?.('.git-diff-panel-body'));
  // Expose the bounded reserve synchronously. Only after it is exhausted do we submit the next page walk.
  if (state.visibleCommitCount < state.commits.length) {
    state.visibleCommitCount = Math.min(state.commits.length, state.visibleCommitCount + pageSize);
    renderGitDiffPanel(item);
    backfillGitDiffVisibleCommitDetails(item);
    return true;
  }
  return refreshGitDiffHistory(item, {append: true});
}

function gitDiffHistoryNearEnd(body) {
  if (!body) return false;
  return body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
}

function bindGitDiffHistoryInfiniteScroll(item, body) {
  bindScopedOnce(body, 'git-diff-history-infinite-scroll', scope => {
    scope.ownEvent('scroll', body, 'scroll', () => {
      backfillGitDiffVisibleCommitDetails(item);
      if (gitDiffProgressiveHistorySupported()) scheduleGitDiffHistoryStatsBackfill(item);
      if (!gitDiffHistoryNearEnd(body)) return;
      void loadOlderGitDiffHistory(item);
    }, {passive: true});
  });
}

function scheduleGitDiffHistoryInfiniteScroll(item, body) {
  if (!body || body.dataset.gitDiffAutoLoadScheduled === 'true') return;
  body.dataset.gitDiffAutoLoadScheduled = 'true';
  const run = () => {
    delete body.dataset.gitDiffAutoLoadScheduled;
    if (!body.isConnected || !body.clientHeight || !body.scrollHeight || body.scrollHeight > body.clientHeight) return;
    void loadOlderGitDiffHistory(item);
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
  else setTimeout(run, 0);
}

async function loadGitDiffCommitDetail(item, sha, options = {}) {
  const state = ensureGitDiffTabState(item);
  if (!state || !state.head || !sha) return false;
  const record = gitDiffCommitRecordFor(state, sha);
  if (record.detail) return true;
  if (record.loading) return record.loading;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const requestedHead = state.head;
  const requestedPath = state.path;
  const requestedRepositoryIdentity = state.repositoryIdentity;
  const requestedDetailKey = gitDiffCommitDetailKey(state, sha);
  const isCurrent = record.guard.begin();
  record.controller = controller;
  record.error = null;
  let request = null;
  const requestOwnsSlot = () => state.commitRecords.get(sha) === record && record.loading === request;
  const detailRequestIsCurrent = () => isCurrent()
    && requestOwnsSlot()
    && state.head === requestedHead
    && gitDiffCommitDetailKey(state, sha) === requestedDetailKey;
  request = Promise.resolve().then(async () => {
    try {
      const payload = await apiFetchJson(gitDiffCommitUrl(requestedPath, sha, requestedHead, requestedRepositoryIdentity), {
        cache: 'no-store',
        ...(controller ? {signal: controller.signal} : {}),
      });
      if (!detailRequestIsCurrent()) return false;
      if (!gitDiffCommitPayloadIsValid(payload, sha)) throw gitDiffInvalidResponseError();
      requireGitDiffProductBoundary(state, payload);
      record.detail = payload;
      record.error = null;
      return true;
    } catch (error) {
      if (!detailRequestIsCurrent() || error?.name === 'AbortError') return false;
      if (options.retryOnStale !== false && gitDiffHistorySnapshotIsStale(error)) {
        const refreshed = await refreshGitDiffHistory(item, {refresh: true});
        const current = ensureGitDiffTabState(item);
        if (refreshed && current?.expanded?.has(sha) && gitDiffVisibleCommitShas(item, current).has(sha)) {
          return loadGitDiffCommitDetail(item, sha, {retryOnStale: false});
        }
        return false;
      }
      record.error = gitDiffErrorSnapshot(error);
      return false;
    } finally {
      if (requestOwnsSlot()) {
        if (gitDiffCommitDetailKey(state, sha) !== requestedDetailKey) {
          reconcileGitDiffCommitRecordScope(state, sha, record);
          if (state.expanded.has(sha) && state.commits.some(commit => String(commit?.sha || '') === sha)) {
            backfillGitDiffVisibleCommitDetails(item);
          }
        } else {
          record.loading = null;
          if (record.controller === controller) record.controller = null;
        }
        renderGitDiffPanel(item);
      }
    }
  });
  record.loading = request;
  renderGitDiffPanel(item);
  return request;
}

function setGitDiffCommitExpanded(item, sha, expanded) {
  const state = ensureGitDiffTabState(item);
  if (!state || !sha) return Promise.resolve(false);
  if (expanded) state.expanded.add(sha);
  else state.expanded.delete(sha);
  state.focusedSha = sha;
  refreshLayoutUrlStateSoon();
  renderGitDiffPanel(item);
  backfillGitDiffVisibleCommitDetails(item);
  if (!expanded || gitDiffCommitRecordFor(state, sha)?.detail) return Promise.resolve(true);
  return loadGitDiffCommitDetail(item, sha);
}

function toggleGitDiffCommit(item, sha) {
  const state = ensureGitDiffTabState(item);
  return setGitDiffCommitExpanded(item, sha, !state?.expanded?.has(sha));
}

function gitDiffTextNode(className, text = '') {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = String(text || '');
  return node;
}

function gitDiffCommitChangesNode(commit) {
  const files = Number.isSafeInteger(commit?.files) && commit.files >= 0 ? commit.files : null;
  const added = Number.isSafeInteger(commit?.added) && commit.added >= 0 ? commit.added : null;
  const removed = Number.isSafeInteger(commit?.removed) && commit.removed >= 0 ? commit.removed : null;
  const binary = Number.isSafeInteger(commit?.binary_files) && commit.binary_files >= 0 ? commit.binary_files : null;
  const node = gitDiffTextNode('git-diff-commit-changes');
  if (files === null || added === null || removed === null || binary === null) {
    node.setAttribute('aria-hidden', 'true');
    return node;
  }
  node.append(
    document.createTextNode(`${files} ${t('common.files')} `),
    gitDiffTextNode('git-diff-commit-added', `+${added}`),
    document.createTextNode(' '),
    gitDiffTextNode('git-diff-commit-removed', `-${removed}`),
  );
  if (binary > 0) node.append(document.createTextNode(` · ${binary} ${t('gitDiff.binary')}`));
  node.setAttribute('aria-label', `${files} ${t('common.files')} +${added} -${removed}${binary ? ` · ${binary} ${t('gitDiff.binary')}` : ''}`);
  return node;
}

function gitDiffHostedLink(remote, kind, value) {
  if (!remote || !['github', 'gitlab'].includes(remote.provider)) return '';
  let base;
  try {
    base = new URL(String(remote.base_url || ''));
  } catch (_error) {
    return '';
  }
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash) return '';
  const identifier = String(value || '');
  if (kind === 'commit' && !/^[0-9a-f]{40,64}$/.test(identifier)) return '';
  if (kind === 'change' && !/^[1-9][0-9]*$/.test(identifier)) return '';
  const suffix = kind === 'commit'
    ? (remote.provider === 'gitlab' ? `/-/commit/${identifier}` : `/commit/${identifier}`)
    : (remote.provider === 'gitlab' ? `/-/merge_requests/${identifier}` : `/pull/${identifier}`);
  return `${base.origin}${base.pathname.replace(/\/$/, '')}${suffix}`;
}

function gitDiffHostedAnchor(className, text, href) {
  if (!href) return gitDiffTextNode(className, text);
  const link = document.createElement('a');
  link.className = className;
  link.textContent = String(text || '');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function gitDiffCommitSubjectNode(commit, remote) {
  const node = gitDiffTextNode('git-diff-commit-description');
  const decorations = Array.isArray(commit?.decorations)
    ? commit.decorations.map(value => String(value || '').trim()).filter(Boolean)
    : String(commit?.decorations || '').trim() ? [String(commit.decorations).trim()] : [];
  if (decorations.length) {
    const refs = document.createElement('span');
    refs.className = 'git-diff-commit-decorations';
    for (const decoration of decorations) refs.append(gitDiffTextNode('git-diff-commit-decoration', decoration));
    node.append(refs, document.createTextNode(' '));
  }
  const subject = String(commit?.subject || '');
  let offset = 0;
  for (const match of subject.matchAll(/#([1-9][0-9]*)\b/g)) {
    if (match.index > offset) node.append(document.createTextNode(subject.slice(offset, match.index)));
    node.append(gitDiffHostedAnchor(
      'git-diff-change-link',
      match[0],
      gitDiffHostedLink(remote, 'change', match[1]),
    ));
    offset = match.index + match[0].length;
  }
  if (offset < subject.length) node.append(document.createTextNode(subject.slice(offset)));
  return node;
}

function gitDiffCommitDateText(commit) {
  return localizedDateTimeFormat(commit?.authored_at, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  });
}

function gitDiffCommitRowAriaLabel(commit, changes, date, author) {
  const sha = String(commit?.sha || '');
  const shortShaText = commit?.short || sha.slice(0, 9);
  const signatureStatus = String(commit?.signature_status || '').trim();
  const decorationLabels = Array.isArray(commit?.decorations)
    ? commit.decorations.map(value => String(value || '').trim()).filter(Boolean)
    : [];
  return [shortShaText, signatureStatus, date?.textContent, changes?.getAttribute?.('aria-label'), author?.textContent, decorationLabels.join(' '), commit?.subject || ''].filter(Boolean).join(' ');
}

function gitDiffHistoryRow(item, rowData, row = null) {
  const state = ensureGitDiffTabState(item);
  const sha = String(rowData?.sha || '');
  const elementName = String(rowData?.elementName || 'div');
  const control = row?.localName === elementName ? row : document.createElement(elementName);
  control.className = String(rowData?.className || 'git-diff-commit-row');
  if (sha) {
    control.dataset.gitDiffCommit = sha;
    control.dataset.path = `/commit/${sha}`;
    control.dataset.kind = 'dir';
    control.dataset.name = String(rowData?.name || rowData?.description || sha);
  } else {
    delete control.dataset.gitDiffCommit;
    delete control.dataset.path;
    delete control.dataset.kind;
    delete control.dataset.name;
  }
  control.setAttribute('role', String(rowData?.role || 'treeitem'));
  control.setAttribute('aria-level', '1');
  control.setAttribute('aria-expanded', rowData?.expanded === true ? 'true' : 'false');
  const caret = gitDiffTextNode('git-diff-commit-caret ui-disclosure-triangle', disclosureTriangleGlyph(rowData?.expanded === true));
  caret.dataset.disclosureExpanded = rowData?.expanded === true ? 'true' : 'false';
  caret.setAttribute('aria-hidden', 'true');
  const shortShaText = String(rowData?.shortSha ?? (sha ? sha.slice(0, 9) : ''));
  const shortSha = sha
    ? gitDiffHostedAnchor('git-diff-commit-sha', shortShaText, gitDiffHostedLink(state?.hostedRemote, 'commit', sha))
    : gitDiffTextNode('git-diff-commit-sha', shortShaText);
  shortSha.title = sha || shortShaText;
  const signatureStatus = String(rowData?.signatureStatus || '').trim();
  if (signatureStatus) {
    const signature = gitDiffTextNode('git-diff-commit-signature', signatureStatus);
    shortSha.append(document.createTextNode(' '), signature);
  }
  const date = gitDiffTextNode('git-diff-commit-date', gitDiffCommitDateText({authored_at: rowData?.authoredAt}));
  date.title = localizedExactDateTimeFormat(rowData?.authoredAt);
  const changes = gitDiffCommitChangesNode(rowData);
  const author = gitDiffTextNode('git-diff-commit-author', rowData?.author || '');
  const description = rowData?.descriptionNode || gitDiffTextNode('git-diff-commit-description', rowData?.description || '');
  control.setAttribute('aria-label', [shortShaText, signatureStatus, date.textContent, changes.getAttribute('aria-label'), author.textContent, rowData?.description || description.textContent].filter(Boolean).join(' '));
  control.replaceChildren(caret, shortSha, date, changes, author, description);
  return control;
}

function gitDiffCommitRow(item, commit, row = null, options = {}) {
  const state = ensureGitDiffTabState(item);
  const sha = String(commit?.sha || '');
  const expanded = state?.expanded?.has(sha) === true;
  const control = row?.localName === 'div' ? row : document.createElement('div');
  if (options.statsOnly === true && control === row) {
    const changes = control.querySelector?.(':scope > .git-diff-commit-changes');
    const date = control.querySelector?.(':scope > .git-diff-commit-date');
    const author = control.querySelector?.(':scope > .git-diff-commit-author');
    const shortSha = control.querySelector?.(':scope > .git-diff-commit-sha');
    if (changes) changes.replaceWith(gitDiffCommitChangesNode(commit));
    if (shortSha) {
      const existingSignature = shortSha.querySelector?.(':scope > .git-diff-commit-signature');
      const signatureStatus = String(commit?.signature_status || '').trim();
      if (existingSignature && !signatureStatus) existingSignature.remove();
      else if (signatureStatus && existingSignature) existingSignature.textContent = signatureStatus;
      else if (signatureStatus) shortSha.append(document.createTextNode(' '), gitDiffTextNode('git-diff-commit-signature', signatureStatus));
    }
    control.setAttribute('aria-label', gitDiffCommitRowAriaLabel(commit, changes ? control.querySelector?.(':scope > .git-diff-commit-changes') : null, date, author));
    return control;
  }
  control.className = 'git-diff-commit-row';
  control.dataset.gitDiffCommit = sha;
  control.dataset.path = `/commit/${sha}`;
  control.dataset.kind = 'dir';
  control.dataset.name = String(commit?.subject || sha);
  control.setAttribute('role', 'treeitem');
  control.setAttribute('aria-level', '1');
  control.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  const caret = gitDiffTextNode('git-diff-commit-caret ui-disclosure-triangle', disclosureTriangleGlyph(expanded));
  caret.dataset.disclosureExpanded = expanded ? 'true' : 'false';
  caret.setAttribute('aria-hidden', 'true');
  const shortShaText = commit?.short || sha.slice(0, 9);
  const shortSha = gitDiffHostedAnchor(
    'git-diff-commit-sha',
    shortShaText,
    gitDiffHostedLink(state?.hostedRemote, 'commit', sha),
  );
  const signatureStatus = String(commit?.signature_status || '').trim();
  if (signatureStatus) {
    const signature = gitDiffTextNode('git-diff-commit-signature', signatureStatus);
    shortSha.append(document.createTextNode(' '), signature);
  }
  const date = gitDiffTextNode('git-diff-commit-date', gitDiffCommitDateText(commit));
  date.title = localizedExactDateTimeFormat(commit?.authored_at);
  const changes = gitDiffCommitChangesNode(commit);
  const author = gitDiffTextNode('git-diff-commit-author', commit?.author || '');
  const description = gitDiffCommitSubjectNode(commit, state?.hostedRemote);
  control.setAttribute('aria-label', gitDiffCommitRowAriaLabel(commit, changes, date, author));
  control.replaceChildren(caret, shortSha, date, changes, author, description);
  return control;
}

function patchGitDiffCommitStatsRows(item, commits) {
  const panel = panelNodes.get(item);
  if (!panel || !commits.length) return;
  const groups = new Map(Array.from(panel.querySelectorAll('.git-diff-commit[data-git-diff-commit-group]') || [])
    .map(group => [group.dataset.gitDiffCommitGroup, group]));
  for (const commit of commits) {
    const group = groups.get(String(commit?.sha || ''));
    const row = group?.querySelector?.(':scope > .git-diff-commit-row');
    if (row) gitDiffCommitRow(item, commit, row, {statsOnly: true});
  }
}

function gitDiffTreeItem(tree) {
  return String(tree?.closest?.('.git-diff-panel')?.dataset?.layoutItem || '');
}

function gitDiffCommitShaFromRow(row) {
  return String(row?.dataset?.gitDiffCommit || '');
}

const gitDiffCommitTreeInteractionController = createSharedTreeInteractionController({
  name: 'git-diff-commits',
  rowSelector: '.git-diff-commit-row[data-path]',
  shouldIgnoreEvent: event => Boolean(event?.target?.closest?.('a[href]')),
  rovingFocus: true,
  applyCurrentClasses: false,
  selectedIds: tree => {
    const state = ensureGitDiffTabState(gitDiffTreeItem(tree));
    return new Set(state?.focusedSha ? [`/commit/${state.focusedSha}`] : []);
  },
  getLeadId: tree => {
    const state = ensureGitDiffTabState(gitDiffTreeItem(tree));
    return state?.focusedSha ? `/commit/${state.focusedSha}` : '';
  },
  setLeadId(id, tree) {
    const state = ensureGitDiffTabState(gitDiffTreeItem(tree));
    if (!state) return;
    state.focusedSha = String(id || '').replace(/^\/commit\//, '');
    refreshLayoutUrlStateSoon();
  },
  isExpanded: row => row?.getAttribute?.('aria-expanded') === 'true',
  setExpanded(row, expanded, tree) {
    const item = gitDiffTreeItem(tree);
    const sha = gitDiffCommitShaFromRow(row);
    if (item && sha) void setGitDiffCommitExpanded(item, sha, expanded);
  },
  activateRow(row, _event, tree) {
    const item = gitDiffTreeItem(tree);
    const sha = gitDiffCommitShaFromRow(row);
    if (item && sha) void toggleGitDiffCommit(item, sha);
  },
});

function bindGitDiffCommitTree(tree) {
  bindScopedOnce(tree, 'git-diff-commit-tree', scope => {
    scope.ownEvent('click', tree, 'click', event => gitDiffCommitTreeInteractionController.handleClick(event, tree));
    scope.ownEvent('keydown', tree, 'keydown', event => gitDiffCommitTreeInteractionController.handleKeydown(event, tree));
    scope.ownEvent('focusin', tree, 'focusin', event => {
      const row = event.target?.closest?.('.git-diff-commit-row[data-path]');
      if (row && tree.contains(row)) gitDiffCommitTreeInteractionController.selectRow(tree, row, event);
    });
  });
}

function gitDiffCommitMessage(detail) {
  const message = document.createElement('pre');
  message.className = 'git-diff-commit-message';
  message.textContent = String(detail?.message || '').replace(/\\n/g, '\n');
  return message;
}

function gitDiffCommitFilesTree(detail) {
  const repo = normalizeDirectoryPath(detail?.repo || '');
  const files = (Array.isArray(detail?.files) ? detail.files : []).map(file => ({
    ...file,
    path: String(file?.path || ''),
    old_path: String(file?.old_path || ''),
    abs_path: normalizeDirectoryPath(`${repo}/${String(file?.path || '')}`),
    repo,
    mtime: Number(detail?.authored_at || 0),
    missing: false,
  }));
  return buildSessionFileTree(repo, files);
}

function gitDiffHistoricalFileItem(detail, file) {
  const path = normalizeDirectoryPath(file?.abs_path || `${detail?.repo || ''}/${file?.path || ''}`);
  return historicalFileEditorItemFor(path, detail?.from_ref || '', detail?.to_ref || '');
}

function gitDiffHistoricalComparisonKind(detail) {
  const parents = Array.isArray(detail?.parents) ? detail.parents : [];
  if (!parents.length) return 'root-empty-tree';
  return parents.length > 1 ? 'merge-first-parent' : 'parent';
}

function openGitDiffHistoricalFile(detail, file, options = {}) {
  const item = gitDiffHistoricalFileItem(detail, file);
  const identity = historicalFileEditorIdentity(item);
  if (!identity) return null;
  const openOptions = {
    item,
    repo: detail.repo,
    returnToItem: options.returnToItem,
    historicalComparisonKind: gitDiffHistoricalComparisonKind(detail),
    userInitiated: options.userInitiated !== false,
  };
  // Install and select the exact historical tab before waiting for its bounded comparison read.
  // The editor owns the loading/error state; return the tab identity now so a slow Git comparison
  // cannot make a deliberate file click appear to do nothing.
  void openHistoricalFileInEditor(identity.path, identity.fromRef, identity.toRef, openOptions);
  return item;
}

function gitDiffDetailCollapsedDirectories(state, sha) {
  if (sha === '__worktree__') {
    syncGitDiffWorktreeTreeScope(state);
    let collapsed = state.worktreeTreeState.collapsedDirectories;
    if (!collapsed) {
      collapsed = new Set();
      state.worktreeTreeState.collapsedDirectories = collapsed;
    }
    return collapsed;
  }
  const record = gitDiffCommitRecordFor(state, sha);
  if (!record.collapsedDirectories) record.collapsedDirectories = new Set();
  return record.collapsedDirectories;
}

function bindGitDiffFileTreeRow(row, rowState, item, sha, detail) {
  row.dataset.gitDiffCommitSha = sha;
  row.dataset.gitDiffCommitPath = rowState.fullPath;
  row.dataset.gitDiffItem = item;
  row.dataset.gitDiffDetailRepo = String(detail?.repo || '');
}

function gitDiffFileTreeContext(tree) {
  const item = gitDiffTreeItem(tree);
  const sha = String(tree?.dataset?.gitDiffCommitSha || '');
  const state = ensureGitDiffTabState(item);
  return {item, sha, state, detail: gitDiffCommitRecordFor(state, sha)?.detail || null};
}

function gitDiffWorktreeDetail(state) {
  const repo = normalizeDirectoryPath(state?.repo || '');
  if (!repo || !String(state?.head || '')) return null;
  return {repo, from_ref: 'HEAD', to_ref: 'current', parents: [String(state.head)]};
}

function gitDiffWorktreeEntryForPath(state, rowPath) {
  const repo = normalizeDirectoryPath(state?.repo || '');
  const requested = String(rowPath || '');
  if (!repo || !requested) return null;
  return (state?.worktreeStatus?.entries || []).find(entry => {
    const relative = String(entry?.path || '');
    return relative && (requested === relative || normalizeDirectoryPath(`${repo}/${relative}`) === normalizeDirectoryPath(requested));
  }) || null;
}

function gitDiffWorktreeEntryFile(state, entry) {
  const repo = normalizeDirectoryPath(state?.repo || '');
  const path = String(entry?.path || '');
  if (!repo || !path) return null;
  const status = String(entry?.status || 'M');
  return {
    ...entry,
    path,
    old_path: String(entry?.old_path || ''),
    abs_path: normalizeDirectoryPath(`${repo}/${path}`),
    repo,
    mtime: Number(state?.worktreeStatus?.modified_at || 0),
    missing: status === 'D',
    diff_tracked: status !== '??',
  };
}

function gitDiffWorktreeFilesTree(state) {
  const repo = normalizeDirectoryPath(state?.repo || '');
  const status = state?.worktreeStatus;
  if (!repo || !status) return {entries: [], entriesByDir: new Map(), sessionFilesMap: new Map(), directoryStatusCounts: new Map()};
  const files = (Array.isArray(status.entries) ? status.entries : []).map(entry => {
    const file = gitDiffWorktreeEntryFile(state, entry);
    if (!file) return null;
    return {...file, status: file.status === '??' ? '?' : file.status};
  }).filter(Boolean);
  return buildSessionFileTree(repo, files);
}

const gitDiffFileTreeInteractionController = createSharedTreeInteractionController({
  name: 'git-diff-files',
  rowSelector: '.file-tree-row[data-path]',
  rovingFocus: true,
  applyCurrentClasses: false,
  selectedIds: tree => {
    const {state, sha} = gitDiffFileTreeContext(tree);
    const path = sha === '__worktree__'
      ? state?.worktreeTreeState?.focusedFilePath || ''
      : gitDiffCommitRecordFor(state, sha)?.focusedFilePath || '';
    return new Set(path ? [path] : []);
  },
  getLeadId: tree => {
    const {state, sha} = gitDiffFileTreeContext(tree);
    return sha === '__worktree__'
      ? state?.worktreeTreeState?.focusedFilePath || ''
      : gitDiffCommitRecordFor(state, sha)?.focusedFilePath || '';
  },
  setLeadId(id, tree) {
    const {state, sha} = gitDiffFileTreeContext(tree);
    if (!state || !sha) return;
    const path = String(id || '');
    if (sha === '__worktree__') {
      syncGitDiffWorktreeTreeScope(state);
      state.worktreeTreeState.focusedFilePath = path;
    } else {
      gitDiffCommitRecordFor(state, sha).focusedFilePath = path;
    }
    refreshLayoutUrlStateSoon();
  },
  isExpanded: row => row?.dataset?.kind === 'dir' && row.getAttribute?.('aria-expanded') === 'true',
  setExpanded(row, expanded, tree) {
    const {item, sha, state} = gitDiffFileTreeContext(tree);
    if (!item || !sha || !state || row?.dataset?.kind !== 'dir') return;
    const collapsed = gitDiffDetailCollapsedDirectories(state, sha);
    if (expanded) collapsed.delete(row.dataset.path);
    else collapsed.add(row.dataset.path);
    refreshLayoutUrlStateSoon();
    renderGitDiffPanel(item);
  },
  activateRow(row, _event, tree) {
    const context = gitDiffFileTreeContext(tree);
    if (row?.dataset?.kind === 'dir') {
      gitDiffFileTreeInteractionController.setExpanded(tree, row, !gitDiffFileTreeInteractionController.isExpanded(row, tree));
      return;
    }
    const file = context.detail
      ? gitDiffCommitFilesTree(context.detail).sessionFilesMap.get(row?.dataset?.path || '')
      : context.sha === '__worktree__'
        ? gitDiffWorktreeEntryFile(context.state, gitDiffWorktreeEntryForPath(context.state, row?.dataset?.path))
        : null;
    const detail = context.detail || (context.sha === '__worktree__' ? gitDiffWorktreeDetail(context.state) : null);
    if (file && detail) void openGitDiffHistoricalFile(detail, file, {returnToItem: context.item});
  },
});

function bindGitDiffFileTree(tree) {
  bindScopedOnce(tree, 'git-diff-file-tree', scope => {
    scope.ownEvent('click', tree, 'click', event => gitDiffFileTreeInteractionController.handleClick(event, tree));
    scope.ownEvent('keydown', tree, 'keydown', event => gitDiffFileTreeInteractionController.handleKeydown(event, tree));
    scope.ownEvent('focusin', tree, 'focusin', event => {
      const row = event.target?.closest?.('.file-tree-row[data-path]');
      if (row && tree.contains(row)) gitDiffFileTreeInteractionController.selectRow(tree, row, event);
    });
  });
}

function gitDiffStatusNode(className, text, role = '') {
  const node = document.createElement('div');
  node.className = className;
  node.textContent = String(text || '');
  if (role) node.setAttribute('role', role);
  return node;
}

function gitDiffDirtySummaryNode(state) {
  const summary = document.createElement('section');
  summary.className = 'git-diff-dirty-summary';
  const dirtyCount = state?.dirtyCount;
  const summaryText = dirtyCount === 0
    ? t('git.clean')
    : Number.isSafeInteger(dirtyCount) && dirtyCount >= 0
      ? t('git.dirty', {count: dirtyCount})
      : t('common.notAvailable');
  summary.setAttribute('aria-label', summaryText);
  summary.append(gitDiffTextNode('git-diff-dirty-count', summaryText));
  const entries = Array.isArray(state?.dirtyEntries) ? state.dirtyEntries : [];
  if (entries.length) {
    const list = document.createElement('div');
    list.className = 'git-diff-dirty-entries';
    for (const entry of entries) {
      const row = document.createElement('div');
      row.className = 'git-diff-dirty-entry';
      row.append(
        gitDiffTextNode('git-diff-dirty-status', `[${String(entry?.status || '')}]`),
        gitDiffTextNode('git-diff-dirty-path', String(entry?.path || '')),
      );
      if (entry?.old_path !== undefined) {
        row.append(
          gitDiffTextNode('git-diff-dirty-rename-arrow', ' ← '),
          gitDiffTextNode('git-diff-dirty-old-path', String(entry.old_path || '')),
        );
      }
      list.append(row);
    }
    summary.append(list);
  }
  return summary;
}

function gitDiffLoadingStatusNode(className = 'git-diff-state git-diff-state-loading') {
  const node = gitDiffStatusNode(className, '', 'status');
  node.innerHTML = textWithMovingEllipsisHtml(t('common.loading'), 'git-diff-loading-dots');
  return node;
}

function gitDiffWorktreeStatusCountsNode(status) {
  const counts = new Map();
  const rawCounts = status?.status_counts && typeof status.status_counts === 'object' ? status.status_counts : null;
  if (rawCounts) {
    for (const [code, count] of Object.entries(rawCounts)) {
      if (Number.isSafeInteger(count) && count > 0) counts.set(String(code), count);
    }
  }
  if (!counts.size) {
    for (const entry of status?.entries || []) {
      const code = String(entry?.status || 'M');
      counts.set(code, (counts.get(code) || 0) + 1);
    }
  }
  const order = ['??', 'M', 'A', 'D', 'R', 'C', 'T', 'U'];
  const entries = [...counts.entries()].sort(([left], [right]) => {
    const leftIndex = order.indexOf(left);
    const rightIndex = order.indexOf(right);
    if (leftIndex >= 0 || rightIndex >= 0) return (leftIndex < 0 ? order.length : leftIndex) - (rightIndex < 0 ? order.length : rightIndex);
    return left.localeCompare(right);
  });
  const node = gitDiffTextNode('git-diff-commit-description');
  node.append(...entries.flatMap(([code, count], index) => [
    ...(index ? [document.createTextNode(' · ')] : []),
    gitDiffTextNode('git-diff-worktree-count', `${count} ${code}`),
  ]));
  node.setAttribute('aria-label', entries.map(([code, count]) => `${count} ${code}`).join(', '));
  return node;
}

function gitDiffWorktreeStatusNode(item, state) {
  const status = state?.worktreeStatus;
  if (!gitDiffWorktreeStatusIsValid(status) || status.available !== true || status.dirty !== true) return null;
  const details = document.createElement('details');
  details.className = 'git-diff-commit git-diff-worktree-status';
  details.dataset.gitDiffCommitGroup = '__worktree__';
  details.open = state.worktreeStatusExpanded === true;
  const summary = gitDiffHistoryRow(item, {
    elementName: 'summary',
    className: 'git-diff-commit-row git-diff-worktree-summary',
    expanded: details.open,
    shortSha: t('gitDiff.dirty'),
    authoredAt: status.modified_at,
    files: status.files,
    added: status.added,
    removed: status.removed,
    binary_files: status.binary_files,
    author: '',
    descriptionNode: gitDiffWorktreeStatusCountsNode(status),
  });
  summary.title = t('common.details');
  details.addEventListener('toggle', () => {
    const current = ensureGitDiffTabState(item);
    if (current) current.worktreeStatusExpanded = details.open;
    scheduleGitDiffHistoryStatsBackfill(item);
    summary.setAttribute('aria-expanded', details.open ? 'true' : 'false');
    const caret = summary.querySelector('.git-diff-commit-caret');
    if (caret) {
      caret.textContent = disclosureTriangleGlyph(details.open);
      caret.dataset.disclosureExpanded = details.open ? 'true' : 'false';
    }
  });
  const model = gitDiffWorktreeFilesTree(state);
  const list = document.createElement('div');
  list.className = 'git-diff-worktree-entries git-diff-file-tree file-tree';
  list.dataset.gitDiffCommitSha = '__worktree__';
  list.setAttribute('role', 'tree');
  list.setAttribute('aria-label', t('gitDiff.changedFiles'));
  renderTreeChildren(list, normalizeDirectoryPath(state.repo), model.entries, 0, {
    entriesByDir: model.entriesByDir,
    sessionFilesMap: model.sessionFilesMap,
    directoryStatusCounts: model.directoryStatusCounts,
    differMode: true,
    compact: true,
    repoForDiffer: normalizeDirectoryPath(state.repo),
    view: 'differ',
    treeSortMode: 'az',
    includeHidden: true,
    collapsedSet: gitDiffDetailCollapsedDirectories(state, '__worktree__'),
    rowBinding: (row, rowState) => bindGitDiffFileTreeRow(row, rowState, item, '__worktree__', gitDiffWorktreeDetail(state)),
  });
  bindGitDiffFileTree(list);
  gitDiffFileTreeInteractionController.applyState(list);
  if (status.truncated === true) list.append(gitDiffStatusNode('git-diff-worktree-truncated', t('gitDiff.historyTruncated'), 'status'));
  details.append(summary, list);
  return details;
}

function renderGitDiffCommitDetail(item, sha, detailNode) {
  const state = ensureGitDiffTabState(item);
  const record = gitDiffCommitRecordFor(state, sha);
  const detail = record?.detail || null;
  const error = record?.error || null;
  const existingTree = detailNode.querySelector?.(':scope > .git-diff-file-tree');
  const restoreFileFocus = Boolean(existingTree?.contains?.(document.activeElement));
  detailNode.className = 'git-diff-commit-detail';
  detailNode.dataset.gitDiffCommitDetail = sha;
  detailNode.setAttribute('role', 'group');
  if (!detail) {
    detailNode.replaceChildren(gitDiffStatusNode(
      error ? 'git-diff-state git-diff-state-error' : 'git-diff-state git-diff-state-loading',
      error ? userMessageText(error, t('common.requestFailed')) : t('common.loading'),
      error ? 'alert' : 'status',
    ));
    return detailNode;
  }
  const nodes = [];
  const refs = gitDiffStatusNode('git-diff-commit-refs', `${t('diff.ref.from')} ${String(detail.from_ref).slice(0, 9)} → ${t('diff.ref.to')} ${String(detail.to_ref).slice(0, 9)}`);
  if (detail.parents.length > 1) refs.textContent += ` · ${t('gitDiff.firstParent')}`;
  nodes.push(refs, gitDiffCommitMessage(detail));
  if (detail.message_truncated === true) nodes.push(gitDiffStatusNode('git-diff-truncated', t('gitDiff.messageTruncated'), 'status'));
  const model = gitDiffCommitFilesTree(detail);
  const tree = existingTree || document.createElement('div');
  tree.className = 'git-diff-file-tree file-tree';
  tree.dataset.gitDiffCommitSha = sha;
  tree.setAttribute('role', 'tree');
  tree.setAttribute('aria-label', t('gitDiff.changedFiles'));
  renderTreeChildren(tree, normalizeDirectoryPath(detail.repo), model.entries, 0, {
    entriesByDir: model.entriesByDir,
    sessionFilesMap: model.sessionFilesMap,
    directoryStatusCounts: model.directoryStatusCounts,
    differMode: true,
    compact: true,
    repoForDiffer: normalizeDirectoryPath(detail.repo),
    view: 'differ',
    treeSortMode: 'az',
    includeHidden: true,
    collapsedSet: gitDiffDetailCollapsedDirectories(state, sha),
    rowBinding: (row, rowState) => bindGitDiffFileTreeRow(row, rowState, item, sha, detail),
  });
  bindGitDiffFileTree(tree);
  nodes.push(tree);
  if (detail.files_truncated === true) nodes.push(gitDiffStatusNode('git-diff-truncated', t('gitDiff.filesTruncated'), 'status'));
  detailNode.replaceChildren(...nodes);
  gitDiffFileTreeInteractionController.applyState(tree, {focusLead: restoreFileFocus});
  return detailNode;
}

function renderGitDiffCommitList(item, list, state) {
  const restoreCommitFocus = Boolean(list.contains?.(document.activeElement));
  const existing = new Map(Array.from(list.children || []).map(group => [group.dataset?.gitDiffCommitGroup || '', group]));
  const groups = [];
  const worktreeStatus = gitDiffWorktreeStatusNode(item, state);
  if (worktreeStatus) groups.push(worktreeStatus);
  for (const commit of state.commits.slice(0, state.visibleCommitCount)) {
    const sha = String(commit?.sha || '');
    if (!sha) continue;
    const group = existing.get(sha) || document.createElement('section');
    group.className = 'git-diff-commit';
    group.dataset.gitDiffCommitGroup = sha;
    group.setAttribute('role', 'none');
    let row = group.querySelector?.(':scope > .git-diff-commit-row');
    row = gitDiffCommitRow(item, commit, row);
    const nodes = [row];
    if (state.expanded.has(sha)) {
      const detail = group.querySelector?.(':scope > .git-diff-commit-detail') || document.createElement('div');
      nodes.push(renderGitDiffCommitDetail(item, sha, detail));
    }
    group.replaceChildren(...nodes);
    groups.push(group);
  }
  reconcileChildNodes(list, groups);
  list.setAttribute('role', 'tree');
  bindGitDiffCommitTree(list);
  gitDiffCommitTreeInteractionController.applyState(list, {focusLead: restoreCommitFocus});
}

function createGitDiffPanel(item) {
  const panel = document.createElement('section');
  panel.className = 'git-diff-panel';
  panel.dataset.layoutItem = item;
  panel.setAttribute('aria-label', gitDiffTabLabel(item));
  const toolbar = document.createElement('header');
  toolbar.className = 'git-diff-toolbar';
  const heading = gitDiffTextNode('git-diff-heading', t('contextmenu.showDiff'));
  const path = gitDiffTextNode('git-diff-path');
  path.replaceChildren(gitDiffTextNode('git-diff-pathname'));
  const refresh = makeButton({className: 'git-diff-refresh', label: t('common.refresh'), ariaLabel: t('common.refresh'), onClick: () => void refreshGitDiffHistory(item, {refresh: true})});
  toolbar.append(heading, path, refresh);
  const meta = document.createElement('div');
  meta.className = 'git-diff-meta';
  const body = document.createElement('div');
  body.className = 'git-diff-panel-body';
  body.dataset.dockviewRegion = 'content';
  body.setAttribute('aria-label', gitDiffTabLabel(item));
  bindGitDiffHistoryInfiniteScroll(item, body);
  panel.append(toolbar, meta, body);
  const state = ensureGitDiffTabState(item);
  if (typeof ResizeObserver === 'function') {
    state.viewportObserver?.disconnect?.();
    state.viewportObserver = new ResizeObserver(() => {
      backfillGitDiffVisibleCommitDetails(item);
      scheduleGitDiffHistoryStatsBackfill(item);
    });
    state.viewportObserver.observe(body);
  }
  return panel;
}

function renderGitDiffPanel(item, options = {}) {
  const panel = panelNodes.get(item) || options.panel || null;
  const state = ensureGitDiffTabState(item);
  if (!panel || !state) return state;
  panel.setAttribute('aria-label', gitDiffTabLabel(item));
  const path = panel.querySelector?.('.git-diff-path');
  if (path) {
    const branch = String(state.branch || '').trim();
    const pathNodes = [gitDiffTextNode('git-diff-pathname', compactHomePath(state.path))];
    if (branch) pathNodes.push(gitDiffTextNode('git-diff-branch', `[${branch}]`));
    path.replaceChildren(...pathNodes);
    path.title = `${state.path}${branch ? ` [${branch}]` : ''}`;
  }
  const refresh = panel.querySelector?.('.git-diff-refresh');
  if (refresh) {
    refresh.textContent = t('common.refresh');
    refresh.setAttribute('aria-label', t('common.refresh'));
    refresh.disabled = state.loading === true;
  }
  const meta = panel.querySelector?.('.git-diff-meta');
  if (meta) {
    const scope = state.relativePath ? state.relativePath : t('gitDiff.repositoryRoot');
    const branch = state.detached ? t('git.detached') : (state.branch || t('common.notAvailable'));
    const branchPrefix = state.detached || state.branch ? `${t('common.branchLabel')}: ${branch} · ` : '';
    meta.textContent = `${branchPrefix}${t('gitDiff.scope', {scope})} · ${t('gitDiff.newestCommits', {count: state.visibleCommitCount || gitDiffHistoryMinimumPageSize})}`;
  }
  const body = panel.querySelector?.('.git-diff-panel-body');
  if (!body) return state;
  body.setAttribute('aria-label', gitDiffTabLabel(item));
  const list = body.querySelector?.(':scope > .git-diff-commits') || document.createElement('div');
  const restoreCommitFocus = Boolean(list.contains?.(document.activeElement));
  list.className = 'git-diff-commits';
  renderGitDiffCommitList(item, list, state);
  const nodes = [];
  if (state.loading) nodes.push(gitDiffLoadingStatusNode());
  if (state.error) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-error', userMessageText(state.error, t('common.requestFailed')), 'alert'));
  if (state.statsError) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-error', userMessageText(state.statsError, t('common.requestFailed')), 'alert'));
  if (state.loaded && !gitDiffWorktreeStatusIsValid(state.worktreeStatus)) nodes.push(gitDiffDirtySummaryNode(state));
  if (state.commits.length || gitDiffWorktreeStatusIsValid(state.worktreeStatus) && state.worktreeStatus.dirty === true) nodes.push(list);
  else if (state.loaded && !state.loading) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-empty', t('gitDiff.empty'), 'status'));
  if (state.loadingOlder) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-loading', t('common.loading'), 'status', {movingEllipsis: true}));
  else if (state.truncated && state.visibleCommitCount >= state.commits.length && !state.nextCursor) {
    nodes.push(gitDiffStatusNode('git-diff-truncated', t('gitDiff.historyTruncated'), 'status'));
  }
  body.replaceChildren(...nodes);
  scheduleGitDiffHistoryInfiniteScroll(item, body);
  if (state.loaded && !state.statsLoading && gitDiffProgressiveHistorySupported()) scheduleGitDiffHistoryStatsBackfill(item);
  if (restoreCommitFocus && body.contains(list)) gitDiffCommitTreeInteractionController.applyState(list, {focusLead: true});
  if (!state.loadAttempted) void refreshGitDiffHistory(item);
  return state;
}

function relocalizeGitDiffPanel(item, panel) {
  panel?.setAttribute?.('aria-label', gitDiffTabLabel(item));
  const heading = panel?.querySelector?.('.git-diff-heading');
  if (heading) heading.textContent = t('contextmenu.showDiff');
  renderGitDiffPanel(item, {panel});
}

function gitDiffLayoutOid(value) {
  const oid = String(value || '').trim();
  return /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/i.test(oid) ? oid : '';
}

function layoutUrlGitDiffStateSnapshot() {
  const result = [];
  for (const item of paneItems(layoutSlots)) {
    if (!gitDiffItemPath(item) || result.some(entry => entry.item === item)) continue;
    const state = gitDiffTabState.get(item);
    if (!state?.head || !state.snapshotCursor) continue;
    const collapsed = [...state.commitRecords].flatMap(([sha, record]) => record.collapsedDirectories
      ? [[sha, Array.from(record.collapsedDirectories).slice(0, 500)]] : []);
    if (state.worktreeTreeState.collapsedDirectories) {
      collapsed.push(['__worktree__', Array.from(state.worktreeTreeState.collapsedDirectories).slice(0, 500)]);
    }
    const focusedFiles = [...state.commitRecords].flatMap(([sha, record]) => record.focusedFilePath
      ? [[sha, record.focusedFilePath]] : []);
    if (state.worktreeTreeState.focusedFilePath) focusedFiles.push(['__worktree__', state.worktreeTreeState.focusedFilePath]);
    result.push({
      item,
      head: state.head,
      snapshotCursor: state.snapshotCursor,
      expanded: Array.from(state.expanded).slice(0, gitDiffHistoryStateLimit),
      focusedSha: state.focusedSha || '',
      collapsed: collapsed.slice(0, gitDiffHistoryStateLimit),
      focusedFiles: focusedFiles.slice(0, gitDiffHistoryStateLimit),
    });
    if (result.length >= 32) break;
  }
  return result;
}

function applyLayoutUrlGitDiffState(entries) {
  if (!Array.isArray(entries)) return 0;
  let applied = 0;
  for (const entry of entries.slice(0, 32)) {
    if (!entry || typeof entry !== 'object') continue;
    const item = String(entry.item || '');
    const head = gitDiffLayoutOid(entry.head);
    const snapshotCursor = String(entry.snapshotCursor || '').slice(0, 4096);
    if (!gitDiffItemPath(item) || !head || !snapshotCursor) continue;
    const state = ensureGitDiffTabState(item);
    state.historyController?.abort?.();
    state.historyGuard.invalidate();
    invalidateGitDiffDetailRequests(state);
    state.head = head;
    state.snapshotCursor = snapshotCursor;
    state.commits = [];
    state.visibleCommitCount = 0;
    state.nextCursor = '';
    state.loaded = false;
    state.loadAttempted = false;
    state.error = null;
    state.expanded = new Set((Array.isArray(entry.expanded) ? entry.expanded : []).map(gitDiffLayoutOid).filter(Boolean).slice(0, gitDiffHistoryStateLimit));
    state.focusedSha = gitDiffLayoutOid(entry.focusedSha);
    for (const sha of [...state.commitRecords.keys()]) clearGitDiffCommitRecord(state, sha);
    resetGitDiffWorktreeTreeState(state);
    for (const pair of (Array.isArray(entry.collapsed) ? entry.collapsed : []).slice(0, gitDiffHistoryStateLimit)) {
      const sha = gitDiffLayoutOid(pair?.[0]);
      const paths = Array.isArray(pair?.[1]) ? pair[1].map(path => normalizeDirectoryPath(String(path || ''))).filter(Boolean).slice(0, 500) : [];
      if (pair?.[0] === '__worktree__') state.worktreeTreeState.collapsedDirectories = new Set(paths);
      else if (sha) gitDiffCommitRecordFor(state, sha).collapsedDirectories = new Set(paths);
    }
    for (const pair of (Array.isArray(entry.focusedFiles) ? entry.focusedFiles : []).slice(0, gitDiffHistoryStateLimit)) {
      const sha = gitDiffLayoutOid(pair?.[0]);
      const path = normalizeDirectoryPath(String(pair?.[1] || ''));
      if (pair?.[0] === '__worktree__') state.worktreeTreeState.focusedFilePath = path;
      else if (sha && path) gitDiffCommitRecordFor(state, sha).focusedFilePath = path;
    }
    applied += 1;
  }
  return applied;
}

function openGitDiffTab(path, options = {}) {
  const item = resolveLayoutItem(options.item || gitDiffItemFor(path));
  if (!item) return null;
  ensureGitDiffTabState(item, {path: gitDiffItemPath(item)});
  recordEditorNav(item);
  void Promise.resolve(selectSession(item, {userInitiated: options.userInitiated === true})).then(() => renderGitDiffPanel(item));
  return item;
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Finder repository-history tab. Historical files continue through the existing Editor renderer.

function gitDiffTabLabel(item) {
  const path = gitDiffItemPath(item);
  const state = gitDiffTabState.get(item);
  const repo = normalizeDirectoryPath(state?.repo || '');
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
    relativePath: '',
    hostedRemote: null,
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
    details: new Map(),
    detailErrors: new Map(),
    detailLoading: new Map(),
    detailGuards: new Map(),
    detailControllers: new Map(),
    detailCollapsedDirectories: new Map(),
    focusedFilePaths: new Map(),
    historyGuard: makeGenerationGuard(),
    historyController: null,
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
  for (const controller of state?.detailControllers?.values?.() || []) controller?.abort?.();
  for (const guard of state?.detailGuards?.values?.() || []) guard?.invalidate?.();
  state?.detailControllers?.clear?.();
  state?.detailLoading?.clear?.();
}

function cleanupGitDiffTab(item) {
  const state = gitDiffTabState.get(item);
  state?.historyController?.abort?.();
  state?.historyGuard?.invalidate?.();
  invalidateGitDiffDetailRequests(state);
  gitDiffTabState.delete(item);
}

function gitDiffHistoryPageSize(body) {
  const row = body?.querySelector?.('.git-diff-commit-row');
  const rowHeight = row?.getBoundingClientRect?.().height || 24;
  const availableHeight = body?.clientHeight || 0;
  if (!rowHeight || !availableHeight) return gitDiffHistoryMinimumPageSize;
  return Math.max(gitDiffHistoryMinimumPageSize, Math.ceil(availableHeight / rowHeight));
}

function gitDiffHistoryUrl(path, cursor = '', limit = gitDiffHistoryMinimumPageSize) {
  const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
  return `/api/fs/git-history?path=${encodeURIComponent(path)}&limit=${Math.max(gitDiffHistoryMinimumPageSize, Math.floor(limit))}${suffix}`;
}

function gitDiffCommitUrl(path, sha, head) {
  return `/api/fs/git-commit?path=${encodeURIComponent(path)}&commit=${encodeURIComponent(sha)}&head=${encodeURIComponent(head)}`;
}

function gitDiffErrorSnapshot(error) {
  return userMessageSnapshot(error, {key: 'common.requestFailed', params: {}, fallback: t('common.requestFailed')});
}

function gitDiffHistoryCursorIsInvalid(error) {
  return String(error?.payload?.user_message?.key || '') === 'fs.error.gitHistoryCursor';
}

function gitDiffHistoryPayloadIsValid(payload) {
  return Boolean(payload && typeof payload === 'object'
    && typeof payload.path === 'string'
    && typeof payload.repo === 'string'
    && typeof payload.relative_path === 'string'
    && typeof payload.head === 'string'
    && (payload.hosted_remote === undefined || payload.hosted_remote === null || (
      typeof payload.hosted_remote === 'object'
      && ['github', 'gitlab'].includes(payload.hosted_remote.provider)
      && typeof payload.hosted_remote.base_url === 'string'
    ))
    && (payload.snapshot_cursor === undefined || typeof payload.snapshot_cursor === 'string')
    && Array.isArray(payload.commits)
    && typeof payload.next_cursor === 'string');
}

function gitDiffCommitPayloadIsValid(payload, sha) {
  return Boolean(payload && typeof payload === 'object'
    && payload.sha === sha
    && typeof payload.repo === 'string'
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

function mergeGitDiffCommits(current, incoming) {
  const seen = new Set();
  return [...(current || []), ...(incoming || [])].filter(commit => {
    const sha = String(commit?.sha || '');
    if (!sha || seen.has(sha)) return false;
    seen.add(sha);
    return true;
  });
}

function pruneGitDiffShaState(state, shas) {
  for (const sha of [...state.expanded]) if (!shas.has(sha)) state.expanded.delete(sha);
  for (const map of [state.details, state.detailErrors, state.detailLoading, state.detailGuards, state.detailControllers, state.detailCollapsedDirectories, state.focusedFilePaths]) {
    for (const sha of [...map.keys()]) if (!shas.has(sha)) map.delete(sha);
  }
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
    invalidateGitDiffDetailRequests(state);
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
    const payload = await apiFetchJson(gitDiffHistoryUrl(state.path, cursor, pageSize * (gitDiffHistoryPagesPrefetched + 1)), {
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
    state.path = normalizeDirectoryPath(payload.path) || state.path;
    state.repo = normalizeDirectoryPath(payload.repo);
    state.relativePath = payload.relative_path;
    state.hostedRemote = payload.hosted_remote || null;
    state.head = payload.head;
    state.commits = append ? mergeGitDiffCommits(state.commits, payload.commits) : mergeGitDiffCommits([], payload.commits);
    if (append) state.visibleCommitCount = Math.min(state.commits.length, state.visibleCommitCount + pageSize);
    else state.visibleCommitCount = Math.min(state.commits.length, pageSize);
    state.snapshotCursor = String(payload.snapshot_cursor || (!append ? cursor : state.snapshotCursor) || '');
    state.nextCursor = payload.next_cursor;
    state.truncated = payload.truncated === true;
    state.truncationReason = String(payload.truncation_reason || '');
    state.loaded = true;
    state.error = null;
    renderPaneTabStrips();
    refreshPaneTabLabel(item);
    if (itemInLayout(tabberItemId)) refreshTabberPanels();
    if (!append) pruneGitDiffShaState(state, new Set(state.commits.map(commit => String(commit?.sha || '')).filter(Boolean)));
    for (const sha of state.expanded) if (!state.details.has(sha) && !state.detailLoading.has(sha)) void loadGitDiffCommitDetail(item, sha);
    refreshLayoutUrlStateSoon();
    return true;
  } catch (error) {
    if (!isCurrent() || error?.name === 'AbortError') return false;
    if (!append && cursor && gitDiffHistoryCursorIsInvalid(error)) {
      // A saved layout can outlive the server's cursor format. Drop that opaque cursor once and
      // reload the current snapshot instead of leaving the restored Diff tab permanently failed.
      state.snapshotCursor = '';
      state.nextCursor = '';
      state.error = null;
      return refreshGitDiffHistory(item, {refresh: true});
    }
    state.error = gitDiffErrorSnapshot(error);
    return false;
  } finally {
    if (isCurrent()) {
      state.loading = false;
      state.loadingOlder = false;
      if (state.historyController === controller) state.historyController = null;
      renderGitDiffPanel(item);
    }
  }
}

function loadOlderGitDiffHistory(item) {
  const state = ensureGitDiffTabState(item);
  if (!state) return false;
  const pageSize = gitDiffHistoryPageSize(panelNodes.get(item)?.querySelector?.('.git-diff-panel-body'));
  // Expose the bounded reserve synchronously. Only after it is exhausted do we submit the next page walk.
  if (state.visibleCommitCount < state.commits.length) {
    state.visibleCommitCount = Math.min(state.commits.length, state.visibleCommitCount + pageSize);
    renderGitDiffPanel(item);
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
      if (!gitDiffHistoryNearEnd(body)) return;
      void loadOlderGitDiffHistory(item);
    }, {passive: true});
  });
}

function gitDiffDetailGuard(state, sha) {
  let guard = state.detailGuards.get(sha);
  if (!guard) {
    guard = makeGenerationGuard();
    state.detailGuards.set(sha, guard);
  }
  return guard;
}

async function loadGitDiffCommitDetail(item, sha) {
  const state = ensureGitDiffTabState(item);
  if (!state || !state.head || !sha) return false;
  if (state.details.has(sha)) return true;
  if (state.detailLoading.has(sha)) return state.detailLoading.get(sha);
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const requestedHead = state.head;
  const isCurrent = gitDiffDetailGuard(state, sha).begin();
  if (controller) state.detailControllers.set(sha, controller);
  state.detailErrors.delete(sha);
  const request = (async () => {
    try {
      const payload = await apiFetchJson(gitDiffCommitUrl(state.path, sha, requestedHead), {
        cache: 'no-store',
        ...(controller ? {signal: controller.signal} : {}),
      });
      if (!isCurrent() || state.head !== requestedHead) return false;
      if (!gitDiffCommitPayloadIsValid(payload, sha)) throw gitDiffInvalidResponseError();
      state.details.set(sha, payload);
      state.detailErrors.delete(sha);
      return true;
    } catch (error) {
      if (!isCurrent() || state.head !== requestedHead || error?.name === 'AbortError') return false;
      state.detailErrors.set(sha, gitDiffErrorSnapshot(error));
      return false;
    } finally {
      if (isCurrent() && state.head === requestedHead) {
        state.detailLoading.delete(sha);
        if (state.detailControllers.get(sha) === controller) state.detailControllers.delete(sha);
        renderGitDiffPanel(item);
      }
    }
  })();
  state.detailLoading.set(sha, request);
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
  if (!expanded || state.details.has(sha)) return Promise.resolve(true);
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
    node.setAttribute('aria-label', t('common.notAvailable'));
    node.title = t('common.notAvailable');
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

function gitDiffCommitRow(item, commit, row = null) {
  const state = ensureGitDiffTabState(item);
  const sha = String(commit?.sha || '');
  const expanded = state?.expanded?.has(sha) === true;
  const control = row?.localName === 'div' ? row : document.createElement('div');
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
  const date = gitDiffTextNode('git-diff-commit-date', gitDiffCommitDateText(commit));
  date.title = localizedExactDateTimeFormat(commit?.authored_at);
  const changes = gitDiffCommitChangesNode(commit);
  const author = gitDiffTextNode('git-diff-commit-author', commit?.author || '');
  const description = gitDiffCommitSubjectNode(commit, state?.hostedRemote);
  control.setAttribute('aria-label', [shortShaText, date.textContent, changes.getAttribute('aria-label'), author.textContent, commit?.subject || ''].filter(Boolean).join(' '));
  control.replaceChildren(caret, shortSha, date, changes, author, description);
  return control;
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
  message.textContent = String(detail?.message || '');
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
  let collapsed = state.detailCollapsedDirectories.get(sha);
  if (!collapsed) {
    collapsed = new Set();
    state.detailCollapsedDirectories.set(sha, collapsed);
  }
  return collapsed;
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
  return {item, sha, state, detail: state?.details?.get(sha) || null};
}

const gitDiffFileTreeInteractionController = createSharedTreeInteractionController({
  name: 'git-diff-files',
  rowSelector: '.file-tree-row[data-path]',
  rovingFocus: true,
  applyCurrentClasses: false,
  selectedIds: tree => {
    const {state, sha} = gitDiffFileTreeContext(tree);
    const path = state?.focusedFilePaths?.get(sha) || '';
    return new Set(path ? [path] : []);
  },
  getLeadId: tree => {
    const {state, sha} = gitDiffFileTreeContext(tree);
    return state?.focusedFilePaths?.get(sha) || '';
  },
  setLeadId(id, tree) {
    const {state, sha} = gitDiffFileTreeContext(tree);
    if (!state || !sha) return;
    state.focusedFilePaths.set(sha, String(id || ''));
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
    const file = context.detail ? gitDiffCommitFilesTree(context.detail).sessionFilesMap.get(row?.dataset?.path || '') : null;
    if (file) void openGitDiffHistoricalFile(context.detail, file, {returnToItem: context.item});
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

function gitDiffLoadingStatusNode(className = 'git-diff-state git-diff-state-loading') {
  const node = gitDiffStatusNode(className, '', 'status');
  node.innerHTML = textWithMovingEllipsisHtml(t('common.loading'), 'git-diff-loading-dots');
  return node;
}

function renderGitDiffCommitDetail(item, sha, detailNode) {
  const state = ensureGitDiffTabState(item);
  const detail = state.details.get(sha);
  const error = state.detailErrors.get(sha);
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
  const refresh = makeButton({className: 'git-diff-refresh', label: t('common.refresh'), ariaLabel: t('common.refresh'), onClick: () => void refreshGitDiffHistory(item, {refresh: true})});
  toolbar.append(heading, path, refresh);
  const meta = document.createElement('div');
  meta.className = 'git-diff-meta';
  const body = document.createElement('div');
  body.className = 'git-diff-panel-body';
  body.setAttribute('aria-label', gitDiffTabLabel(item));
  bindGitDiffHistoryInfiniteScroll(item, body);
  panel.append(toolbar, meta, body);
  ensureGitDiffTabState(item);
  return panel;
}

function renderGitDiffPanel(item, options = {}) {
  const panel = panelNodes.get(item) || options.panel || null;
  const state = ensureGitDiffTabState(item);
  if (!panel || !state) return state;
  panel.setAttribute('aria-label', gitDiffTabLabel(item));
  const path = panel.querySelector?.('.git-diff-path');
  if (path) {
    path.textContent = compactHomePath(state.path);
    path.title = state.path;
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
    meta.textContent = `${t('gitDiff.scope', {scope})} · ${t('gitDiff.newestCommits', {count: state.visibleCommitCount || gitDiffHistoryMinimumPageSize})}`;
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
  if (state.commits.length) nodes.push(list);
  else if (state.loaded && !state.loading) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-empty', t('gitDiff.empty'), 'status'));
  if (state.loadingOlder) nodes.push(gitDiffStatusNode('git-diff-state git-diff-state-loading', t('common.loading'), 'status', {movingEllipsis: true}));
  else if (state.truncated && state.visibleCommitCount >= state.commits.length && !state.nextCursor) {
    nodes.push(gitDiffStatusNode('git-diff-truncated', t('gitDiff.historyTruncated'), 'status'));
  }
  body.replaceChildren(...nodes);
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
    result.push({
      item,
      head: state.head,
      snapshotCursor: state.snapshotCursor,
      expanded: Array.from(state.expanded).slice(0, gitDiffHistoryStateLimit),
      focusedSha: state.focusedSha || '',
      collapsed: Array.from(state.detailCollapsedDirectories, ([sha, paths]) => [sha, Array.from(paths).slice(0, 500)]).slice(0, gitDiffHistoryStateLimit),
      focusedFiles: Array.from(state.focusedFilePaths).slice(0, gitDiffHistoryStateLimit),
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
    state.details.clear();
    state.detailErrors.clear();
    state.detailCollapsedDirectories = new Map((Array.isArray(entry.collapsed) ? entry.collapsed : []).slice(0, gitDiffHistoryStateLimit).flatMap(pair => {
      const sha = gitDiffLayoutOid(pair?.[0]);
      const paths = Array.isArray(pair?.[1]) ? pair[1].map(path => normalizeDirectoryPath(String(path || ''))).filter(Boolean).slice(0, 500) : [];
      return sha ? [[sha, new Set(paths)]] : [];
    }));
    state.focusedFilePaths = new Map((Array.isArray(entry.focusedFiles) ? entry.focusedFiles : []).slice(0, gitDiffHistoryStateLimit).flatMap(pair => {
      const sha = gitDiffLayoutOid(pair?.[0]);
      const path = normalizeDirectoryPath(String(pair?.[1] || ''));
      return sha && path ? [[sha, path]] : [];
    }));
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

function sessionForFileRepo(path) {
  const normalized = String(path || '');
  if (!normalized) return '';
  const matches = sessions
    .map(session => {
      const root = normalizeDirectoryPath(transcriptMeta.sessions?.[session]?.project?.git?.root || '');
      const containsPath = root && (normalized === root || normalized.startsWith(`${root}/`));
      return containsPath ? {session, root} : null;
    })
    .filter(Boolean)
    .sort((left, right) => right.root.length - left.root.length);
  return matches[0]?.session || '';
}

function sessionFilesTargetSession(options = {}) {
  if (!options.followActive && sessionFilesSelectedSession && sessions.includes(sessionFilesSelectedSession)) return sessionFilesSelectedSession;
  if (options.followActive) {
    const activeItem = currentActiveMenuItem();
    const activePath = isFileEditorItem(activeItem) || isFilePreviewItem(activeItem) || isImageViewerItem(activeItem)
      ? fileItemPath(activeItem)
      : '';
    const fileSession = sessionForFileRepo(activePath || '');
    if (fileSession) return fileSession;
  }
  const current = currentSessionActionTarget();
  if (current && sessions.includes(current)) return current;
  return sessions[0] || '';
}

function diffRefParams() {
  return {
    from: cleanDiffRef(diffRefFrom, 'HEAD'),
    to: cleanDiffRef(diffRefTo, 'current'),
  };
}

function diffRefQueryString() {
  const refs = diffRefParams();
  return `from=${encodeURIComponent(refs.from)}&to=${encodeURIComponent(refs.to)}`;
}

function diffRefSuggestions() {
  const suggestions = [
    {ref: 'HEAD', short: 'HEAD', subject: 'base commit'},
    {ref: 'current', short: 'current', subject: 'working tree'},
  ];
  const seen = new Set(suggestions.map(item => item.ref));
  for (const payload of [sessionFilesPayload, fileExplorerSessionFilesPayload]) {
    const refsByRepo = payload?.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {};
    for (const refs of Object.values(refsByRepo)) {
      if (!Array.isArray(refs)) continue;
      for (const item of refs) {
        const ref = cleanDiffRef(item?.ref || '', '');
        if (!ref || seen.has(ref)) continue;
        suggestions.push({ref, short: item?.short || ref.slice(0, 9), subject: item?.subject || ''});
        seen.add(ref);
        if (suggestions.length >= 60) return suggestions;
      }
    }
  }
  return suggestions;
}

function diffRefSuggestionsHtml(listId) {
  return `<datalist id="${esc(listId)}">${diffRefSuggestions().map(item => {
    const label = [item.short, item.subject].filter(Boolean).join(' ');
    return `<option value="${esc(item.ref)}" label="${esc(label)}"></option>`;
  }).join('')}</datalist>`;
}

function diffRefShaLike(value) {
  return /^[0-9a-f]{7,40}$/i.test(String(value || '').trim());
}

function diffRefSameCommit(value, candidate) {
  const left = cleanDiffRef(value, '');
  const right = cleanDiffRef(candidate, '');
  if (!left || !right) return false;
  if (left === right) return true;
  return diffRefShaLike(left) && diffRefShaLike(right) && (left.startsWith(right) || right.startsWith(left));
}

function diffRefOptionMatches(value, item) {
  return diffRefSameCommit(value, item?.ref) || diffRefSameCommit(value, item?.short);
}

function canonicalDiffRefValue(value, suggestions) {
  const ref = cleanDiffRef(value, '');
  if (!ref) return '';
  return suggestions.find(item => diffRefOptionMatches(ref, item))?.ref || ref;
}

function diffRefSelectOptionsHtml(value, options = {}) {
  const maxItems = options.compact ? 30 : 100;
  const suggestions = Array.isArray(options.suggestions) ? options.suggestions : diffRefSuggestions();
  const items = suggestions.slice(0, maxItems);
  if (value && !items.some(item => diffRefOptionMatches(value, item))) {
    items.unshift({ref: value, short: value.slice(0, 9), subject: 'selected ref'});
  }
  return items.map(item => {
    const label = [item.short, item.subject].filter(Boolean).join(' - ') || item.ref;
    return `<option value="${esc(item.ref)}"${diffRefOptionMatches(value, item) ? ' selected' : ''}>${esc(label)}</option>`;
  }).join('');
}

function diffRefFromSuggestions() {
  return diffRefSuggestions().filter(item => item.ref !== 'current');
}

function diffRefToSuggestions(fromRef = diffRefFrom) {
  const suggestions = diffRefSuggestions();
  const current = suggestions.find(item => item.ref === 'current') || {ref: 'current', short: 'current', subject: 'working tree'};
  const ordered = [current, ...suggestions.filter(item => item.ref !== 'current')];
  const from = cleanDiffRef(fromRef, '');
  const fromIndex = ordered.findIndex(item => diffRefOptionMatches(from, item));
  if (fromIndex < 0) return [current];
  return ordered.slice(0, Math.max(1, fromIndex));
}

function diffRefControlsHtml(options = {}) {
  const compact = options.compact === true;
  const className = compact ? 'diff-ref-controls compact' : 'diff-ref-controls';
  return `<span class="${className}" data-diff-ref-controls>
    <label class="diff-ref-control">${esc(t('diff.ref.from'))} <select class="diff-ref-select" data-diff-ref-from data-diff-ref-from-select aria-label="${esc(t('diff.ref.from.aria'))}">${diffRefSelectOptionsHtml(diffRefFrom, {compact, suggestions: diffRefFromSuggestions()})}</select></label>
    <label class="diff-ref-control">${esc(t('diff.ref.to'))} <select class="diff-ref-select" data-diff-ref-to data-diff-ref-to-select aria-label="${esc(t('diff.ref.to.aria'))}">${diffRefSelectOptionsHtml(diffRefTo, {compact, suggestions: diffRefToSuggestions(diffRefFrom)})}</select></label>
  </span>`;
}

function setDiffRefs(fromRef, toRef, options = {}) {
  const nextFrom = canonicalDiffRefValue(cleanDiffRef(fromRef, 'HEAD'), diffRefFromSuggestions()) || 'HEAD';
  const toSuggestions = diffRefToSuggestions(nextFrom);
  let nextTo = canonicalDiffRefValue(cleanDiffRef(toRef, 'current'), toSuggestions) || 'current';
  if (!toSuggestions.some(item => diffRefOptionMatches(nextTo, item))) nextTo = 'current';
  if (nextFrom === diffRefFrom && nextTo === diffRefTo && options.force !== true) return false;
  diffRefFrom = nextFrom;
  diffRefTo = nextTo;
  writeStoredDiffRefs();
  fileExplorerSessionFilesCache.clear();
  for (const state of openFiles.values()) {
    if (!state || state.kind !== 'text') continue;
    state.diffLoaded = false;
    state.diffUnavailable = false;
    state.diffError = '';
  }
  renderChangesPanels({force: true});
  renderFileExplorerChangesPanels({force: true});
  fetchSessionFiles({session: sessionFilesTargetSession(), force: true});
  fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
  for (const path of openFiles.keys()) renderOpenFilePath(path);
  return true;
}

function commitDiffRefControls(container) {
  const fromInput = container?.querySelector?.('[data-diff-ref-from]');
  const toInput = container?.querySelector?.('[data-diff-ref-to]');
  return setDiffRefs(fromInput?.value, toInput?.value);
}

function syncDiffRefControlValues(container) {
  if (!container) return;
  const active = document.activeElement;
  const fromInput = container.querySelector?.('[data-diff-ref-from]');
  const toInput = container.querySelector?.('[data-diff-ref-to]');
  const fromSelect = container.querySelector?.('[data-diff-ref-from-select]');
  const toSelect = container.querySelector?.('[data-diff-ref-to-select]');
  if (fromInput && fromInput !== active) fromInput.value = diffRefFrom;
  if (toInput && toInput !== active) toInput.value = diffRefTo;
  if (fromSelect && fromSelect !== active) fromSelect.value = canonicalDiffRefValue(diffRefFrom, diffRefFromSuggestions()) || diffRefFrom;
  if (toSelect && toSelect !== active) toSelect.value = canonicalDiffRefValue(diffRefTo, diffRefToSuggestions(diffRefFrom)) || diffRefTo;
}

function fileExplorerSessionFilesTargetSession() {
  return sessionFilesTargetSession({followActive: true});
}

function emptySessionFilesPayload(session = '', loaded = true) {
  return {session, files: [], repos: [], refs_by_repo: {}, errors: [], from_ref: diffRefFrom, to_ref: diffRefTo, loaded};
}

function switchFileExplorerChangesSession(session) {
  if (!session || !document.querySelector('.file-explorer-changes-panel')) return;
  const cached = fileExplorerSessionFilesCache.get(session);
  if (cached?.payload) {
    fileExplorerSessionFilesPayload = cached.payload;
    fileExplorerSessionFilesPayloadSignature = cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload);
  } else {
    const pendingPayload = emptySessionFilesPayload(session, false);
    fileExplorerSessionFilesPayload = pendingPayload;
    fileExplorerSessionFilesPayloadSignature = sessionFilesPayloadSignatureForPayload(pendingPayload);
  }
  renderFileExplorerChangesPanels();
  fetchSessionFiles({destination: 'finder', session, silent: true, force: true});
}

function sessionFilesPayloadForDestination(destination) {
  return destination === 'finder' ? fileExplorerSessionFilesPayload : sessionFilesPayload;
}

function setSessionFilesPayloadForDestination(destination, payload) {
  if (destination === 'finder') fileExplorerSessionFilesPayload = payload;
  else sessionFilesPayload = payload;
}

function sessionFilesPayloadSignatureForPayload(payload) {
  const files = (Array.isArray(payload?.files) ? payload.files : []).map(item => [
    item.session || '',
    item.agent || '',
    item.status || '',
    item.repo || '',
    item.path || '',
    item.abs_path || '',
    Number(item.mtime || 0),
    Number(item.added || 0),
    Number(item.removed || 0),
    item.uploaded === true ? 1 : 0,
  ]);
  const repos = (Array.isArray(payload?.repos) ? payload.repos : []).map(item => [
    item.repo || '',
    Number(item.count || 0),
    Number(item.touched_count || 0),
    Number(item.added || 0),
    Number(item.removed || 0),
    Number.isFinite(Number(item.behind)) ? Number(item.behind) : null,
    Number.isFinite(Number(item.ahead)) ? Number(item.ahead) : null,
  ]);
  return JSON.stringify({
    session: payload?.session || '',
    loaded: payload?.loaded === true,
    from: payload?.from_ref || '',
    to: payload?.to_ref || '',
    errors: Array.isArray(payload?.errors) ? payload.errors : [],
    repos,
    files,
  });
}

function sessionFilesSignatureForDestination(destination) {
  return destination === 'finder' ? fileExplorerSessionFilesPayloadSignature : sessionFilesPayloadSignature;
}

function setSessionFilesSignatureForDestination(destination, signature) {
  if (destination === 'finder') fileExplorerSessionFilesPayloadSignature = signature;
  else sessionFilesPayloadSignature = signature;
}

function sessionFilesLoadingForDestination(destination) {
  return destination === 'finder' ? fileExplorerSessionFilesLoading : sessionFilesLoading;
}

function setSessionFilesLoadingForDestination(destination, loading) {
  if (destination === 'finder') fileExplorerSessionFilesLoading = loading;
  else sessionFilesLoading = loading;
}

function sessionFilesRenderOptions(options = {}) {
  return options.force === true || options.silent !== true ? {force: true} : {};
}

function renderSessionFilesDestination(destination, options = {}) {
  if (destination === 'finder') renderFileExplorerChangesPanels(options);
  else renderChangesPanels(options);
}

async function fetchSessionFiles(options = {}) {
  const destination = options.destination === 'finder' ? 'finder' : 'changes';
  const forceRefresh = options.force === true;
  if (sessionFilesLoadingForDestination(destination) && !forceRefresh) return;
  const requestId = destination === 'finder' ? ++fileExplorerSessionFilesRequestId : ++sessionFilesRequestId;
  const requestIsCurrent = () => (
    destination === 'finder'
      ? requestId === fileExplorerSessionFilesRequestId
      : requestId === sessionFilesRequestId
  );
  const session = options.session || (destination === 'finder' ? fileExplorerSessionFilesTargetSession() : sessionFilesTargetSession());
  let shouldRender = options.silent !== true;
  if (!session) {
    const emptyPayload = emptySessionFilesPayload('', true);
    const signature = sessionFilesPayloadSignatureForPayload(emptyPayload);
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, emptyPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    if (shouldRender) renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
    return;
  }
  if (destination !== 'finder') sessionFilesSelectedSession = session;
  setSessionFilesLoadingForDestination(destination, true);
  if (!options.silent) statusEl.textContent = 'loading changed files...';
  if (!options.silent) {
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
  }
  try {
    const response = await apiFetch(`/api/session-files?session=${encodeURIComponent(session)}&hours=24&${diffRefQueryString()}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.status);
    const nextPayload = {
      session: payload.session || session,
      files: Array.isArray(payload.files) ? payload.files : [],
      repos: Array.isArray(payload.repos) ? payload.repos : [],
      refs_by_repo: payload.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {},
      errors: Array.isArray(payload.errors) ? payload.errors : [],
      from_ref: payload.from_ref || diffRefFrom,
      to_ref: payload.to_ref || diffRefTo,
      loaded: true,
    };
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    if (destination === 'finder') fileExplorerSessionFilesCache.set(session, {payload: nextPayload, signature});
    if (!options.silent) statusEl.innerHTML = `<span class="ok">loaded ${nextPayload.files.length} changed file${nextPayload.files.length === 1 ? '' : 's'}</span>`;
  } catch (err) {
    const nextPayload = {session, files: [], repos: [], refs_by_repo: {}, errors: [String(err)], from_ref: diffRefFrom, to_ref: diffRefTo, loaded: true};
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    if (!options.silent) statusEl.innerHTML = `<span class="err">changed files failed: ${esc(err)}</span>`;
  } finally {
    if (requestIsCurrent()) setSessionFilesLoadingForDestination(destination, false);
    if (requestIsCurrent() && shouldRender) {
      renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
      if (destination === 'finder') updateFileTreeGitStatusRows();
      renderPaneTabStrips();
      renderSessionButtons();
    }
  }
}

function sessionFileTimeText(mtime) {
  const value = Number(mtime || 0);
  if (!value) return '';
  try {
    return new Date(value * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  } catch (_) {
    return '';
  }
}

function sessionFileDiffText(item) {
  return [
    Number.isFinite(Number(item?.added)) && Number(item.added) !== 0 ? {kind: 'add', text: `+${Number(item.added)}`} : null,
    Number.isFinite(Number(item?.removed)) && Number(item.removed) !== 0 ? {kind: 'remove', text: `-${Number(item.removed)}`} : null,
  ].filter(Boolean);
}

function sortedSessionFiles(files) {
  const items = Array.isArray(files) ? files.slice() : [];
  const uploadOrder = item => item?.uploaded === true ? 1 : 0;
  if (sessionFilesSortMode === 'name') {
    return items.sort((left, right) => uploadOrder(left) - uploadOrder(right)
      || String(left.path || '').localeCompare(String(right.path || ''))
      || String(left.repo || '').localeCompare(String(right.repo || '')));
  }
  return items.sort((left, right) => {
    const uploadResult = uploadOrder(left) - uploadOrder(right);
    if (uploadResult !== 0) return uploadResult;
    const leftMtime = Number(left.mtime || 0);
    const rightMtime = Number(right.mtime || 0);
    const mtimeResult = left?.uploaded === true ? leftMtime - rightMtime : rightMtime - leftMtime;
    return mtimeResult || String(left.path || '').localeCompare(String(right.path || ''));
  });
}

function groupedSessionFiles(files) {
  const groups = new Map();
  for (const item of files) {
    const repo = item.repo || 'Outside repo';
    if (!groups.has(repo)) groups.set(repo, []);
    groups.get(repo).push(item);
  }
  return Array.from(groups.entries());
}

function splitUploadedSessionFiles(files) {
  const regular = [];
  const uploaded = [];
  for (const item of sortedSessionFiles(files)) {
    if (item?.uploaded === true) uploaded.push(item);
    else regular.push(item);
  }
  return {regular, uploaded};
}

function writeStoredUploadedFilesCollapsed() {
  try { window.localStorage?.setItem(uploadedFilesCollapsedStorageKey, uploadedFilesCollapsed ? '1' : '0'); }
  catch (_) {}
}

function writeStoredChangesFolderCollapsed() {
  try { window.localStorage?.setItem(changesFolderCollapsedStorageKey, JSON.stringify(Array.from(changesFolderCollapsed).sort())); }
  catch (_) {}
}

function changeStatusClassKey(statusKey) {
  const key = String(statusKey || 'M').toLowerCase();
  return /^[a-z0-9_-]+$/.test(key) ? key : 'unknown';
}

function changeFileParentLabel(relPath) {
  const rel = String(relPath || '');
  const index = rel.lastIndexOf('/');
  return index > 0 ? rel.slice(0, index) : '';
}

function changeFileTotals(files) {
  let added = 0;
  let removed = 0;
  for (const item of Array.isArray(files) ? files : []) {
    const add = Number(item?.added);
    const remove = Number(item?.removed);
    if (Number.isFinite(add)) added += add;
    if (Number.isFinite(remove)) removed += remove;
  }
  return {added, removed};
}

function diffRefDisplayText(ref) {
  const value = cleanDiffRef(ref, '');
  if (!value || value === 'default') return t('diff.ref.defaultBase');
  if (value === 'base') return t('diff.ref.base');
  if (value === 'current') return t('diff.ref.workingTree');
  return value.length > 12 && /^[0-9a-f]{13,40}$/i.test(value) ? value.slice(0, 9) : value;
}

function comparisonTitleHtml(payload) {
  const from = diffRefDisplayText(payload?.from_ref || diffRefFrom);
  const to = diffRefDisplayText(payload?.to_ref || diffRefTo);
  return t('diff.comparing', {from: esc(from), to: esc(to)});
}

function changesSummaryHtml(files, session, loading, loaded) {
  if (loading) return t('changes.loading');
  if (!loaded) return t('changes.notLoaded');
  const count = tPlural('changes.fileCount', files.length);
  const {added, removed} = changeFileTotals(files);
  const scope = session ? t('changes.inSession', {session: sessionLabel(session)}) : '';
  return `${esc(count)}${esc(scope)} <span class="changes-summary-separator">·</span> <span class="changes-diff-add">+${added}</span> <span class="changes-diff-remove">-${removed}</span>`;
}

function changesRepoMetaHtml(repoInfo) {
  const pieces = [];
  if (Number.isFinite(Number(repoInfo?.behind))) pieces.push(`<span>${esc(tPlural('changes.behind', Number(repoInfo.behind)))}</span>`);
  if (Number.isFinite(Number(repoInfo?.ahead))) pieces.push(`<span>${esc(tPlural('changes.ahead', Number(repoInfo.ahead)))}</span>`);
  return pieces.length ? `<span class="changes-repo-compare-meta">${pieces.join('')}</span>` : '';
}

function repoPayloadByPath(payload) {
  const map = new Map();
  for (const repo of Array.isArray(payload?.repos) ? payload.repos : []) map.set(repo.repo || 'Outside repo', repo);
  return map;
}

function changesComparisonHeaderHtml(payload, files, options = {}) {
  const loaded = payload?.loaded === true;
  const loading = options.loading === true;
  const summary = changesSummaryHtml(files, payload?.session || '', loading, loaded);
  return `<section class="changes-comparison-head">
    <div class="changes-comparison-title">${comparisonTitleHtml(payload || {})}</div>
    <div class="changes-comparison-summary">${summary}</div>
  </section>`;
}

function changeTreeNode(name, path) {
  return {name, path, children: new Map(), files: []};
}

function changeTreeForEntries(entries) {
  const root = changeTreeNode('', '');
  for (const item of entries) {
    const rel = String(item?.path || item?.abs_path || '').split('/').filter(Boolean);
    const fileName = rel.pop() || basenameOf(item?.abs_path || item?.path || '');
    let node = root;
    let currentPath = '';
    for (const part of rel) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      if (!node.children.has(part)) node.children.set(part, changeTreeNode(part, currentPath));
      node = node.children.get(part);
    }
    node.files.push({...item, __displayName: fileName});
  }
  return root;
}

function changeTreeFileCount(node) {
  let count = node.files.length;
  for (const child of node.children.values()) count += changeTreeFileCount(child);
  return count;
}

function compressedChangeFolder(node) {
  const names = [node.name];
  let current = node;
  while (!current.files.length && current.children.size === 1) {
    const child = Array.from(current.children.values())[0];
    names.push(child.name);
    current = child;
  }
  return {label: names.join('/'), node: current, path: current.path};
}

function changesFolderCollapseKey(context, path) {
  return [context.session || '', context.repo || '', path || ''].join('|');
}

function changesTreeChildrenHtml(node, context, depth) {
  const folders = Array.from(node.children.values()).sort((left, right) => left.name.localeCompare(right.name, undefined, {numeric: true, sensitivity: 'base'}));
  const folderHtml = folders.map(child => changesFolderHtml(child, context, depth)).join('');
  const fileHtml = node.files.map(item => changeFileRowHtml(item, {...context, depth})).join('');
  return `${folderHtml}${fileHtml}`;
}

function changesFolderHtml(node, context, depth) {
  const folder = compressedChangeFolder(node);
  const key = changesFolderCollapseKey(context, folder.path);
  const collapsed = changesFolderCollapsed.has(key);
  const count = changeTreeFileCount(folder.node);
  const children = collapsed ? '' : changesTreeChildrenHtml(folder.node, context, depth + 1);
  return `<div class="changes-tree-folder${collapsed ? ' collapsed' : ''}" style="--changes-tree-depth:${depth}">
    <button type="button" class="changes-tree-folder-row" data-changes-folder-toggle="${esc(key)}" aria-expanded="${collapsed ? 'false' : 'true'}">
      <span class="changes-tree-caret">${collapsed ? '▸' : '▾'}</span>
      <span class="changes-tree-folder-icon">▸</span>
      <span class="changes-tree-folder-name">${esc(folder.label)}/</span>
      <span class="changes-tree-folder-count">${count}</span>
    </button>
    ${children ? `<div class="changes-tree-children">${children}</div>` : ''}
  </div>`;
}

function changeFileRowHtml(item, options = {}) {
  const statusKey = String(item.status || 'M').toUpperCase();
  const statusClass = changeStatusClassKey(statusKey);
  const absPath = item.abs_path || item.path || '';
  const name = item.__displayName || basenameOf(absPath || item.path || '');
  const rel = item.path || absPath;
  const parentLabel = changeFileParentLabel(rel);
  const timeText = sessionFileTimeText(item.mtime);
  const diffHtml = sessionFileDiffText(item).map(part => `<span class="changes-diff-${part.kind}">${esc(part.text)}</span>`).join(' ');
  const agentHtml = agentIcon(String(item.agent || '').toLowerCase());
  const agentSlotHtml = agentHtml ? `<span class="changes-file-agent">${agentHtml}</span>` : '';
  const dateHtml = timeText ? `<span class="changes-file-date">${esc(timeText)}</span>` : '';
  const metaHtml = [diffHtml, dateHtml].filter(Boolean).join('');
  const compactClass = options.compact ? ' compact' : ' detailed';
  const depth = Math.max(0, Number(options.depth) || 0);
  const icon = fileIconFor(name);
  const iconClass = fileIconClassFor(name, 'file');
  const actionAttr = absPath
    ? ` draggable="true" data-open-change-file="${esc(absPath)}" data-open-change-session="${esc(item.session || '')}" data-open-change-status="${esc(statusKey)}" title="${esc(absPath)}"`
    : ' disabled';
  return `<button type="button" class="changes-file-row${compactClass}" style="--changes-tree-depth:${depth}"${actionAttr}>
    <span class="changes-status changes-status-${esc(statusClass)}">${esc(statusKey)}</span>
    <span class="changes-file-icon ${esc(iconClass)}" aria-hidden="true">${esc(icon)}</span>
    <span class="changes-file-main"><span class="changes-file-title"><span class="changes-file-name">${esc(name)}</span>${agentSlotHtml}</span><span class="changes-file-path">${esc(parentLabel || rel)}</span></span>
    <span class="changes-file-meta">${metaHtml}</span>
  </button>`;
}

function changesRepoGroupsHtml(files, options = {}) {
  const {regular, uploaded} = splitUploadedSessionFiles(files);
  const payload = options.payload || {};
  const repoMap = repoPayloadByPath(payload);
  const repoHtml = groupedSessionFiles(regular).map(([repo, entries]) => {
    const repoLabel = repo === 'Outside repo' ? repo : compactHomePath(repo);
    const repoInfo = repoMap.get(repo) || {};
    const tree = changeTreeForEntries(entries);
    const rows = changesTreeChildrenHtml(tree, {session: payload.session || '', repo, compact: options.compact === true}, 0);
    return `<section class="changes-repo-group">
      <div class="changes-repo-head"><span class="changes-repo-title">${esc(repoLabel)}</span>${changesRepoMetaHtml(repoInfo)}<span class="changes-repo-count">${entries.length}</span></div>
      <div class="changes-file-list changes-tree">${rows}</div>
    </section>`;
  }).join('');
  if (!uploaded.length) return repoHtml;
  const uploadedRows = uploadedFilesCollapsed ? '' : uploaded.map(item => changeFileRowHtml(item, options)).join('');
  const uploadedHtml = `<section class="changes-repo-group changes-uploaded-group${uploadedFilesCollapsed ? ' collapsed' : ''}">
      <button type="button" class="changes-repo-head changes-uploaded-toggle" data-uploaded-files-toggle aria-expanded="${uploadedFilesCollapsed ? 'false' : 'true'}">
        <span><span class="changes-uploaded-caret">${uploadedFilesCollapsed ? '▸' : '▾'}</span> ${esc(t('changes.uploaded', {count: uploaded.length}))}</span><span>${uploadedFilesCollapsed ? esc(t('changes.collapsed')) : uploaded.length}</span>
      </button>
      ${uploadedFilesCollapsed ? '' : `<div class="changes-file-list">${uploadedRows}</div>`}
    </section>`;
  return `${repoHtml}${uploadedHtml}`;
}

function changesPanelHtml() {
  const target = sessionFilesPayload.session || sessionFilesTargetSession();
  const files = sessionFilesPayload.files || [];
  const loaded = sessionFilesPayload.loaded === true;
  const options = sessions.map(session => `<option value="${esc(session)}"${session === target ? ' selected' : ''}>${esc(sessionLabel(session))} ${esc(session)}</option>`).join('');
  const errorHtml = (sessionFilesPayload.errors || []).map(error => `<div class="changes-error">${esc(error)}</div>`).join('');
  const comparison = changesComparisonHeaderHtml(sessionFilesPayload, files, {loading: sessionFilesLoading});
  const groups = changesRepoGroupsHtml(files, {payload: sessionFilesPayload});
  const empty = !sessionFilesLoading && loaded && !files.length ? `<div class="changes-empty">${esc(t('changes.emptyAi'))}</div>` : '';
  return `
    <div class="changes-toolbar">
      <label class="changes-control">${esc(t('changes.session'))} <select data-session-files-session>${options}</select></label>
      <label class="changes-control">${esc(t('changes.sort'))} <select data-session-files-sort>
        <option value="mtime"${sessionFilesSortMode === 'mtime' ? ' selected' : ''}>${esc(t('changes.sort.recent'))}</option>
        <option value="name"${sessionFilesSortMode === 'name' ? ' selected' : ''}>${esc(t('changes.sort.name'))}</option>
      </select></label>
      ${diffRefControlsHtml()}
      <button type="button" class="changes-refresh" data-session-files-refresh>${esc(t('changes.refresh'))}</button>
    </div>
    ${comparison}
    ${errorHtml}
    ${empty || groups}`;
}

function fileExplorerChangesPanelHtml() {
  const payload = fileExplorerSessionFilesPayload;
  const loading = fileExplorerSessionFilesLoading;
  const files = payload.files || [];
  const loaded = payload.loaded === true;
  const session = payload.session || fileExplorerSessionFilesTargetSession();
  const errorHtml = (payload.errors || []).map(error => `<div class="changes-error">${esc(error)}</div>`).join('');
  const empty = !loading && loaded && !files.length ? `<div class="changes-empty">${esc(t('changes.emptyModified'))}</div>` : '';
  return `
    <div class="file-explorer-changes-head">
      <span class="changes-title">${esc(t('changes.title'))}</span>
      ${diffRefControlsHtml({compact: true})}
      <button type="button" class="changes-refresh" data-session-files-refresh title="${esc(t('changes.refresh.title'))}">${esc(t('changes.refresh'))}</button>
      <button type="button" class="changes-close" data-file-explorer-changes-close title="${esc(t('changes.hide'))}" aria-label="${esc(t('changes.hide'))}">×</button>
    </div>
    ${changesComparisonHeaderHtml(payload, files, {loading})}
    ${errorHtml}
    ${empty || changesRepoGroupsHtml(files, {compact: true, payload})}`;
}

function applyFileExplorerChangesHidden() {
  document.body.classList.toggle('file-explorer-changes-hidden', fileExplorerChangesHidden);
  document.querySelectorAll('[data-file-explorer-changes-toggle]').forEach(btn => {
    btn.setAttribute('aria-pressed', fileExplorerChangesHidden ? 'false' : 'true');
    btn.title = fileExplorerChangesHidden ? 'Show modified files' : 'Hide modified files';
  });
}

function setFileExplorerChangesHidden(hidden) {
  fileExplorerChangesHidden = Boolean(hidden);
  try { localStorage.setItem('yolomux.fileExplorerChangesHidden', fileExplorerChangesHidden ? '1' : '0'); } catch (_) {}
  applyFileExplorerChangesHidden();
}

function createChangesPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel changes-panel';
  panel.id = `panel-${changesItemId}`;
  panel.innerHTML = `
      <div class="panel-head changes-panel-head">
        ${virtualPanelControlsHtml(changesItemId, 'Changes')}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="panel-detail-row">
        <div class="panel-copy">
          <div id="panel-tab-${changesItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.changes'))}</span></div>
          <div id="meta-${changesItemId}" class="meta">${esc(changesTabDetail())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(changesItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="changes-body panel-overlay-root">
        <div id="panel-toasts-${changesItemId}" class="panel-toast-stack"></div>
        ${changesPanelHtml()}
      </div>`;
  bindPanelShell(panel, changesItemId);
  bindChangesPanel(panel);
  if (!sessionFilesPayload.loaded || sessionFilesPayload.session !== sessionFilesTargetSession()) {
    fetchSessionFiles({destination: 'changes', silent: true});
  }
  return panel;
}

function activeChangesControl(panel) {
  const active = document.activeElement;
  if (!active || !panel?.contains(active)) return null;
  return active.closest?.('[data-session-files-session], [data-session-files-sort], [data-diff-ref-from], [data-diff-ref-to], [data-session-files-refresh], [data-uploaded-files-toggle], [data-changes-folder-toggle]') || null;
}

function renderChangesPanels(options = {}) {
  for (const panel of document.querySelectorAll('.changes-panel')) {
    const body = panel.querySelector('.changes-body');
    const meta = panel.querySelector(`#meta-${cssEscape(changesItemId)}`);
    if (meta) meta.textContent = changesTabDetail();
    if (body && (options.force === true || !activeChangesControl(panel))) {
      replaceHtmlPreservingScroll(body, `<div id="panel-toasts-${changesItemId}" class="panel-toast-stack"></div>${changesPanelHtml()}`);
    }
    bindChangesPanel(panel);
  }
}

async function openChangedFileInDiff(path, ownerSession = '', status = '') {
  const item = fileEditorItemFor(path);
  const normalizedStatus = String(status || '').toUpperCase();
  const isAddedChange = normalizedStatus === 'A' || normalizedStatus === 'U' || normalizedStatus === '?';
  setFileEditorViewMode(path, isAddedChange ? 'edit' : 'diff', item);
  if (normalizedStatus === 'D') {
    await openFilesSetAndShow(path, {
      mtime: 0,
      size: 0,
      kind: 'text',
      original: '',
      content: '',
      dirty: false,
      deleted: true,
    }, {item, ownerSession});
  } else {
    await openFileInEditor(path, {name: basenameOf(path), session: ownerSession}, {item, ownerSession, viewMode: isAddedChange ? 'edit' : 'diff'});
  }
  const diffReady = await refreshOpenFileDiff(path, {silent: true});
  if (diffReady && !isAddedChange) setFileEditorViewMode(path, 'diff', item);
  if (!diffReady && isAddedChange) setFileEditorViewMode(path, 'edit', item);
  renderOpenFilePath(path);
}

function bindChangesPanel(panel) {
  if (!panel || panel.dataset.changesBound === 'true') return;
  panel.dataset.changesBound = 'true';
  panel.addEventListener('change', event => {
    const sessionSelect = event.target.closest('[data-session-files-session]');
    if (sessionSelect && panel.contains(sessionSelect)) {
      sessionFilesSelectedSession = sessionSelect.value;
      fetchSessionFiles({session: sessionFilesSelectedSession});
      return;
    }
    const sortSelect = event.target.closest('[data-session-files-sort]');
    if (sortSelect && panel.contains(sortSelect)) {
      sessionFilesSortMode = sortSelect.value === 'name' ? 'name' : 'mtime';
      renderChangesPanels({force: true});
      renderFileExplorerChangesPanels({force: true});
      return;
    }
    const diffRefInput = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (diffRefInput && panel.contains(diffRefInput)) {
      event.preventDefault();
      event.stopPropagation();
      commitDiffRefControls(diffRefInput.closest('[data-diff-ref-controls]') || panel);
      return;
    }
  });
  panel.addEventListener('keydown', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      commitDiffRefControls(diffRefInput.closest('[data-diff-ref-controls]') || panel);
      diffRefInput.blur?.();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      diffRefInput.value = diffRefInput.matches('[data-diff-ref-from]') ? diffRefFrom : diffRefTo;
      diffRefInput.blur?.();
    }
  });
  panel.addEventListener('click', async event => {
    const uploadedToggle = event.target.closest('[data-uploaded-files-toggle]');
    if (uploadedToggle && panel.contains(uploadedToggle)) {
      event.preventDefault();
      uploadedFilesCollapsed = !uploadedFilesCollapsed;
      writeStoredUploadedFilesCollapsed();
      renderChangesPanels({force: true});
      renderFileExplorerChangesPanels({force: true});
      return;
    }
    const folderToggle = event.target.closest('[data-changes-folder-toggle]');
    if (folderToggle && panel.contains(folderToggle)) {
      event.preventDefault();
      const key = folderToggle.dataset.changesFolderToggle || '';
      if (changesFolderCollapsed.has(key)) changesFolderCollapsed.delete(key);
      else changesFolderCollapsed.add(key);
      writeStoredChangesFolderCollapsed();
      renderChangesPanels({force: true});
      renderFileExplorerChangesPanels({force: true});
      return;
    }
    const refresh = event.target.closest('[data-session-files-refresh]');
    if (refresh && panel.contains(refresh)) {
      event.preventDefault();
      const destination = refresh.closest('[data-file-explorer-changes]') ? 'finder' : 'changes';
      fetchSessionFiles({
        destination,
        session: destination === 'finder' ? fileExplorerSessionFilesTargetSession() : sessionFilesTargetSession(),
      });
      return;
    }
  });
  panel.addEventListener('dragstart', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    if (!event.dataTransfer) return;
    const path = fileRow.dataset.openChangeFile || '';
    if (!path) return;
    closeFileImagePreview();
    const payloadObject = {path, paths: [path], kind: 'file', name: basenameOf(path)};
    dragFilePayloadState = normalizeFileDragPayload(payloadObject);
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('application/x-yolomux-file', JSON.stringify(payloadObject));
    event.dataTransfer.setData('text/plain', path);
    startFileDragPreview(event, [path], {kind: 'file', name: basenameOf(path)});
  });
  panel.addEventListener('dragend', () => {
    dragFilePayloadState = null;
    stopCustomDragPreview();
    clearDropPreview();
  });
  panel.addEventListener('dblclick', async event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    event.preventDefault();
    const path = fileRow.dataset.openChangeFile;
    if (path) {
      const ownerSession = fileRow.dataset.openChangeSession || '';
      await openChangedFileInDiff(path, ownerSession, fileRow.dataset.openChangeStatus || '');
    }
  });
}

function activePreferenceControl(panel) {
  const active = document.activeElement;
  if (!active || !panel?.contains(active)) return null;
  return active.closest?.('[data-setting-path], [data-preferences-search], [data-preferences-search-action], [data-preference-section-toggle], [data-preferences-reset-all], [data-preferences-reset-confirm], [data-preferences-reset-cancel]') || null;
}

function preferenceFocusTargetIsInteractive(target) {
  // DOIT.6 #53: `.panel-head` is the draggable drag handle (chrome, not content). A pointerdown there
  // must NOT synchronously focus the Preferences search — that focus steal aborts the native tab drag,
  // so the Preferences tab "can't be dragged". Body clicks still focus the search.
  return Boolean(target?.closest?.('.panel-head, input, textarea, select, button, a, [contenteditable="true"], [data-setting-path], [data-preferences-search], [data-preferences-search-action], [data-preference-section-toggle], [data-preferences-reset-all], [data-preferences-reset-confirm], [data-preferences-reset-cancel]'));
}

function clampPreferenceNumber(item, value) {
  // item.scale lets a field display a human unit (e.g. MB) while storing raw (bytes); min/max are in
  // display units, so the stored default is divided by scale to compare in the same space.
  const scale = Number(item.scale) || 1;
  const fallback = Number(preferenceDefault(item.path)) / scale;
  const parsed = Number(value);
  let number = Number.isFinite(parsed) ? parsed : fallback;
  if (Number.isFinite(Number(item.min))) number = Math.max(Number(item.min), number);
  if (Number.isFinite(Number(item.max))) number = Math.min(Number(item.max), number);
  if (!Number.isFinite(number)) return '';
  return Number.isInteger(number) ? String(number) : String(number);
}

function validatePreferenceNumberControl(control) {
  const path = control.dataset.settingPath || '';
  const item = preferenceItemByPath(path);
  if (!item || item.type !== 'number') return true;
  const value = Number(control.value);
  const min = Number(item.min);
  const max = Number(item.max);
  let message = '';
  if (!Number.isFinite(value)) message = 'Enter a number';
  else if (Number.isFinite(min) && value < min) message = `Minimum ${min}`;
  else if (Number.isFinite(max) && value > max) message = `Maximum ${max}`;
  control.setCustomValidity(message);
  return !message;
}

function settingPatch(path, value) {
  const parts = String(path || '').split('.').filter(Boolean);
  const root = {};
  let current = root;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) current[part] = value;
    else {
      current[part] = {};
      current = current[part];
    }
  });
  return root;
}

function valueFromPreferenceControl(control) {
  const type = control.dataset.settingType || 'text';
  if (type === 'boolean') return control.checked === true;
  if (type === 'number') {
    const item = preferenceItemByPath(control.dataset.settingPath || '');
    if (!item) return Number(control.value);
    const clamped = clampPreferenceNumber(item, control.value);
    control.value = clamped;
    validatePreferenceNumberControl(control);
    const scale = Number(item.scale) || 1;
    return Number(clamped) * scale;
  }
  if (type === 'list') return String(control.value || '').split('\n').map(line => line.trim()).filter(Boolean);
  return control.value;
}

async function saveSettingsPatch(patch, options = {}) {
  if (readOnlyMode) return;
  const preservedLayout = options.preserveLayout === false ? null : cloneLayoutSlots();
  const preservedLayoutSignature = preservedLayout ? layoutSlotsSignature(preservedLayout) : '';
  const preservedFocus = focusedPanelItem;
  const response = await apiFetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({settings: patch}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  applySettingsPayload(payload, {force: true, applyEditorDefaults: options.applyEditorDefaults === true});
  if (preservedLayout && layoutSlotsSignature() !== preservedLayoutSignature) {
    applyLayoutSlots(preservedLayout, {
      focusSession: preservedFocus && itemInLayout(preservedFocus, preservedLayout) ? preservedFocus : undefined,
      prune: false,
    });
  }
  refreshYoloRulesStatus({silent: true});
}

function showUploadRsyncRecommendation(options = {}) {
  const command = uploadRsyncExampleCommand();
  const action = document.createElement('button');
  action.type = 'button';
  action.textContent = t('pref.advisory.copyRsync');
  action.addEventListener('click', event => {
    event.stopPropagation();
    copyTextToClipboard(command)
      .then(() => { statusEl.textContent = t('upload.copiedRsync'); })
      .catch(error => { statusEl.innerHTML = `<span class="err">${esc(t('upload.copyFailed', {error}))}</span>`; });
  });
  const sizeText = options.sizeBytes ? t('upload.sizeText', {size: formatFileSize(options.sizeBytes)}) : '';
  return showToast(t('upload.toastTitle'), [
    t('upload.toastBody', {sizeText, cap: formatFileSize(uploadMaxBytes)}),
    command,
  ], {
    container: displayToastContainer(options.session || prefsItemId),
    actions: [action],
    countdownMs: 20000,
  });
}

function savePreferenceControl(control) {
  const path = control.dataset.settingPath || '';
  if (control.dataset.settingType === 'number' && !validatePreferenceNumberControl(control)) {
    control.reportValidity?.();
    return;
  }
  const previousValue = preferenceValue(path);
  const value = valueFromPreferenceControl(control);
  // #50: switch the UI language OPTIMISTICALLY on the select change — don't wait for the settings-poll
  // round-trip (same lesson as the theme toggle). applyLocale is async + re-renders every surface.
  if (path === 'general.language') applyLocale(resolveLocalePref(value));
  saveSettingsPatch(settingPatch(path, value), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => {
      const scheme = activeEditorScheme();
      if ((path === 'appearance.editor_dark_color_scheme' && scheme.dark)
        || (path === 'appearance.editor_light_color_scheme' && !scheme.dark)) {
        setFileEditorThemeMode(value);
      }
      if (path === 'uploads.max_bytes' && Number(value) > uploadRsyncRecommendationBytes && Number(previousValue) <= uploadRsyncRecommendationBytes) {
        showUploadRsyncRecommendation({sizeBytes: Number(value)});
      }
      statusEl.textContent = `saved ${path}`;
    })
    .catch(error => { statusEl.innerHTML = `<span class="err">settings save failed: ${esc(error)}</span>`; refreshSettings({force: true}); });
}

function resetPreference(path) {
  const item = preferenceItemByPath(path);
  if (!item) return;
  saveSettingsPatch(settingPatch(path, preferenceDefault(path)), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => { statusEl.textContent = `reset ${path}`; })
    .catch(error => { statusEl.innerHTML = `<span class="err">settings reset failed: ${esc(error)}</span>`; });
}

function resetAllPreferences() {
  if (readOnlyMode) return;
  saveSettingsPatch(mergeSettingObjects({}, clientSettingsDefaults), {applyEditorDefaults: true})
    .then(() => {
      preferencesSearchText = '';
      preferencesResetConfirmVisible = false;
      collapsedPreferenceSections = defaultCollapsedPreferenceSections();
      writeStoredCollapsedPreferenceSections();
      setFileEditorThemeMode(editorThemeInheritMode);
      renderPreferencesPanels({force: true});
      statusEl.textContent = 'reset all preferences';
    })
    .catch(error => { statusEl.innerHTML = `<span class="err">settings reset failed: ${esc(error)}</span>`; });
}

// File Explorer pane content is self-contained so layout panes do not depend on
// the older left-edge overlay tree.
function createFileExplorerPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel file-explorer-panel';
  panel.id = `panel-${fileExplorerItemId}`;
  const initialPath = fileExplorerRoot || homePath || '/';
  const label = fileExplorerLabel();
  panel.innerHTML = `
      <div class="panel-head file-explorer-head">
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
        <div class="file-explorer-toolbar">
          <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel" title="${esc(t('finder.toolbar.hidden'))}" aria-pressed="${fileExplorerShowHidden ? 'true' : 'false'}">.*</button>
          <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel" title="${esc(t('finder.rootMode.fixed'))}" aria-pressed="false">${esc(t('finder.toolbar.rootLabel'))}</button>
          <div class="file-explorer-quick-access-panel" aria-label="${esc(t('finder.toolbar.quickPaths'))}"></div>
          <button type="button" class="file-explorer-header-action" data-file-explorer-new-file title="${esc(t('finder.toolbar.newFile'))}" aria-label="${esc(t('finder.toolbar.newFile'))}">+</button>
          <button type="button" class="file-explorer-header-action" data-file-explorer-new-folder title="${esc(t('finder.toolbar.newFolder'))}" aria-label="${esc(t('finder.toolbar.newFolder'))}">▣</button>
          <button type="button" class="file-explorer-header-action" data-file-explorer-refresh title="${esc(t('finder.toolbar.refresh'))}" aria-label="${esc(t('finder.toolbar.refresh'))}">↻</button>
          <button type="button" class="file-explorer-header-action" data-file-explorer-collapse title="${esc(t('finder.toolbar.collapseAll'))}" aria-label="${esc(t('finder.toolbar.collapseAll'))}">▤</button>
          <button type="button" class="file-explorer-header-action file-explorer-date-toggle" data-file-explorer-tree-dates title="${esc(t('finder.toolbar.dates'))}" aria-pressed="${fileExplorerTreeShowDates ? 'true' : 'false'}">${esc(t('finder.toolbar.datesLabel'))}</button>
          <button type="button" class="file-explorer-header-action file-explorer-changes-toggle" data-file-explorer-changes-toggle title="${esc(fileExplorerChangesHidden ? t('changes.show') : t('changes.hide'))}" aria-pressed="${fileExplorerChangesHidden ? 'false' : 'true'}">Δ</button>
          <select class="file-explorer-sort-select" data-file-explorer-tree-sort title="${esc(t('finder.toolbar.sort'))}" aria-label="${esc(t('finder.toolbar.sort'))}">
            <option value="az"${fileExplorerTreeSortMode === 'az' ? ' selected' : ''}>${esc(t('finder.sort.az'))}</option>
            <option value="za"${fileExplorerTreeSortMode === 'za' ? ' selected' : ''}>${esc(t('finder.sort.za'))}</option>
            <option value="newest"${fileExplorerTreeSortMode === 'newest' ? ' selected' : ''}>${esc(t('finder.sort.newest'))}</option>
            <option value="oldest"${fileExplorerTreeSortMode === 'oldest' ? ' selected' : ''}>${esc(t('finder.sort.oldest'))}</option>
          </select>
          <input class="file-explorer-path-inline" type="text" value="${esc(initialPath)}" spellcheck="false" aria-label="${esc(t('finder.toolbar.rootPath', {name: label}))}">
          <span class="file-explorer-repo-summary" hidden></span>
          <button type="button" class="path-copy-button file-explorer-path-copy-panel" title="${esc(t('finder.toolbar.copyPath'))}" aria-label="${esc(t('finder.toolbar.copyPath'))}"></button>
          ${paneFrameControlsGroupHtml(fileExplorerItemId, {
            groupClass: 'file-explorer-frame-controls',
            actions: false,
            minimize: false,
            expand: false,
            close: true,
            closeClass: 'file-explorer-panel-close',
            closeTitle: t('finder.hideFromLayout', {name: label}),
            closeLabel: t('finder.hideFromLayout', {name: label}),
          })}
        </div>
      </div>
      <div class="file-explorer-pane panel-overlay-root">
        <div id="panel-toasts-${fileExplorerItemId}" class="panel-toast-stack"></div>
        <div class="file-explorer-tree-panel" role="tree" tabindex="0"></div>
        <div class="file-explorer-changes-resizer" data-file-explorer-changes-resizer title="${esc(t('finder.toolbar.resize'))}"></div>
        <div class="file-explorer-changes-panel" data-file-explorer-changes></div>
      </div>`;
  bindPanelShell(panel, fileExplorerItemId);
  bindChangesPanel(panel);
  const hiddenBtn = panel.querySelector('.file-explorer-hidden-toggle-panel');
  const rootModeBtn = panel.querySelector('.file-explorer-root-mode-toggle-panel');
  const dateBtn = panel.querySelector('[data-file-explorer-tree-dates]');
  if (hiddenBtn) {
    syncFileExplorerHiddenButton(hiddenBtn);
    hiddenBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      toggleHiddenFiles();
    });
  }
  if (rootModeBtn) {
    rootModeBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      toggleFileExplorerRootMode();
    });
  }
  if (dateBtn) {
    syncFileExplorerTreeDateButton(dateBtn);
  }
  const closeBtn = panel.querySelector('.file-explorer-panel-close');
  panel.querySelector('.file-explorer-path-copy-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    copyCurrentFileExplorerPath();
  });
  bindFileExplorerPathInput(panel.querySelector('.file-explorer-path-inline'));
  bindFileExplorerHeaderActions(panel);
  bindFileExplorerChangesResizer(panel);
  // #44: the panel-head toggle (Δ) and the Modified-files header X show/hide the section. Delegated on
  // the panel so it survives changes-panel re-renders; persisted so the choice sticks across reloads.
  panel.addEventListener('click', event => {
    if (event.target.closest?.('[data-file-explorer-changes-toggle]')) {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerChangesHidden(!fileExplorerChangesHidden);
    } else if (event.target.closest?.('[data-file-explorer-changes-close]')) {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerChangesHidden(true);
    }
  });
  applyFileExplorerChangesHidden();
  renderFileExplorerRootModeControls();
  if (closeBtn) {
    closeBtn.addEventListener('pointerdown', event => event.stopPropagation());
    closeBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      removeSessionFromLayout(fileExplorerItemId);
    });
  }
  refreshFileExplorerPanelTree(panel);
  renderFileExplorerChangesPanel(panel);
  if (!fileExplorerSessionFilesPayload.loaded || fileExplorerSessionFilesPayload.session !== fileExplorerSessionFilesTargetSession()) {
    fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true});
  }
  return panel;
}

async function refreshFileExplorerPanelTree(panel, options = {}) {
  const treeEl = panel.querySelector('.file-explorer-tree-panel');
  const pathEl = panel.querySelector('.file-explorer-path-inline');
  const hiddenBtn = panel.querySelector('.file-explorer-hidden-toggle-panel');
  const dateBtn = panel.querySelector('[data-file-explorer-tree-dates]');
  const sortSelect = panel.querySelector('[data-file-explorer-tree-sort]');
  if (!treeEl) return;
  const root = normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
  setFileExplorerPathElementValue(pathEl, root);
  setFileExplorerPathError(pathEl);
  renderFileExplorerRootModeControls();
  syncFileExplorerHiddenButton(hiddenBtn);
  syncFileExplorerTreeDateButton(dateBtn);
  if (sortSelect && sortSelect.value !== fileExplorerTreeSortMode) sortSelect.value = fileExplorerTreeSortMode;
  const entries = options.root === root && Array.isArray(options.entries)
    ? options.entries
    : await fetchDirectory(root);
  if (!entries) return;
  renderTreeChildren(treeEl, root, entries, 0, {entriesByDir: options.entriesByDir});
  updateFileExplorerCurrentFileHighlight();
}

function renderFileExplorerChangesPanel(panel, options = {}) {
  const changes = panel?.querySelector?.('[data-file-explorer-changes]');
  if (!changes) return;
  if (options.force === true || !activeChangesControl(panel)) {
    replaceHtmlPreservingScroll(changes, fileExplorerChangesPanelHtml());
  }
  bindChangesPanel(panel);
}

function renderFileExplorerChangesPanels(options = {}) {
  for (const panel of document.querySelectorAll('.file-explorer-panel')) {
    renderFileExplorerChangesPanel(panel, options);
  }
}

function bindFileExplorerChangesResizer(panel) {
  const handle = panel?.querySelector?.('[data-file-explorer-changes-resizer]');
  const pane = panel?.querySelector?.('.file-explorer-pane');
  if (!handle || !pane || handle.dataset.bound === 'true') return;
  handle.dataset.bound = 'true';
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    event.stopPropagation();
    const pointerId = event.pointerId;
    handle.setPointerCapture?.(pointerId);
    pane.classList.add('resizing-changes');
    document.body?.classList.add('resizing-file-explorer-changes');
    const move = moveEvent => {
      const rect = pane.getBoundingClientRect();
      const height = Math.max(1, rect.height);
      const styles = getComputedStyle(pane);
      const minBlock = Number.parseFloat(styles.getPropertyValue('--file-explorer-changes-min-block-size')) || 96;
      const bottomHeight = Math.max(minBlock, rect.bottom - moveEvent.clientY);
      const minFraction = Math.min(0.68, minBlock / height);
      const fraction = Math.max(minFraction, Math.min(0.68, bottomHeight / height));
      pane.style.setProperty('--file-explorer-changes-size', `${Math.round(fraction * 1000) / 10}%`);
    };
    const done = () => {
      pane.classList.remove('resizing-changes');
      document.body?.classList.remove('resizing-file-explorer-changes');
      try { handle.releasePointerCapture?.(pointerId); } catch (_) {}
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', done);
      window.removeEventListener('pointercancel', done);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', done);
    window.addEventListener('pointercancel', done);
  });
}

function handleFileEditorContentChanged(panel, path, content, options = {}) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return;
  state.content = String(content ?? '');
  const dirty = state.content !== state.original;
  const dirtyChanged = dirty !== state.dirty;
  state.dirty = dirty;
  updateFileEditorPanelChrome(panel, path);
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
  renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
  renderLinkedFilePreviewPanels(panel, path, state.content);
  syncFileEditorSplitScroll(panel, 'editor');
  if (state.externalChanged && !state.externalChangeEditPrompted) {
    promptExternalChangeBeforeEditing(path, panel);
  }
  if (state.dirty) scheduleFileAutosave(path);
  else clearFileAutosaveTimer(path);
  if (dirtyChanged) {
    renderSessionButtons();
    renderPaneTabStrips();
  }
}

function createFileEditorPanel(item) {
  const path = fileItemPath(item);
  const panel = document.createElement('article');
  panel.className = 'panel file-editor-panel';
  panel.dataset.filePath = path;
  panel.dataset.layoutItem = item;
  panel.innerHTML = `
      <div class="panel-head file-editor-panel-head">
        <div class="file-editor-panel-actions file-editor-frame-actions">
          ${paneFrameControlsGroupHtml(item, {
            groupClass: 'file-editor-frame-controls',
            actions: false,
            minimize: true,
            expand: true,
            close: true,
            closeClass: 'file-editor-panel-close',
            closeTitle: t('editor.closePane'),
            closeLabel: t('editor.closePane'),
          })}
        </div>
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="file-editor-toolbar" role="toolbar" aria-label="${esc(t('editor.toolbar.aria'))}" hidden>
        <div class="file-editor-mode-control file-editor-mode-control-panel" role="group" aria-label="${esc(t('editor.mode.aria'))}" hidden>
          <button type="button" data-editor-mode="edit" title="${esc(t('editor.mode.edit'))}" aria-label="${esc(t('editor.mode.edit'))}"><span class="file-editor-icon file-editor-icon-edit" aria-hidden="true"></span></button>
          <button type="button" data-editor-mode="preview" title="${esc(t('editor.mode.preview'))}" aria-label="${esc(t('editor.mode.preview'))}"><span class="file-editor-icon file-editor-icon-eye" aria-hidden="true"></span></button>
          <button type="button" data-editor-mode="split" title="${esc(t('editor.mode.split'))}" aria-label="${esc(t('editor.mode.split'))}"><span class="file-editor-icon file-editor-icon-split" aria-hidden="true"></span></button>
          <button type="button" class="file-editor-cross-split-panel" title="${esc(t('editor.sidePreview'))}" aria-label="${esc(t('editor.sidePreview'))}" hidden><span class="file-editor-icon file-editor-icon-side-split" aria-hidden="true"></span></button>
        </div>
        <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="mode" aria-hidden="true" hidden></span>
        <button type="button" class="file-editor-gutter-panel" title="${esc(t('editor.toggleLineNumbers'))}" aria-label="${esc(t('editor.toggleLineNumbers'))}" hidden>#</button>
        <button type="button" class="file-editor-wrap-panel" title="${esc(t('editor.toggleWordWrap'))}" aria-label="${esc(t('editor.toggleWordWrap'))}" hidden><span class="file-editor-icon file-editor-icon-wrap" aria-hidden="true"></span></button>
        <button type="button" class="file-editor-find-panel" title="${esc(t('editor.findInFile', {shortcut: appShortcutText('F')}))}" aria-label="${esc(t('editor.findInFileAria'))}" hidden><span class="file-editor-icon file-editor-icon-find" aria-hidden="true"></span></button>
        <button type="button" class="file-editor-diff-panel" title="${esc(t('editor.diff'))}" aria-label="${esc(t('editor.diff'))}" hidden><span class="file-editor-icon file-editor-icon-diff" aria-hidden="true"></span></button>
        <span class="file-editor-diff-ref-panel" hidden>${diffRefControlsHtml({compact: true})}</span>
        <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="tools" aria-hidden="true" hidden></span>
        <button type="button" class="file-editor-theme-panel" title="${esc(t('editor.theme'))}" aria-label="${esc(t('editor.theme'))}"><span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span></button>
        <button type="button" class="file-editor-reload-panel" title="${esc(t('editor.reloadFromDisk'))}" hidden>${esc(t('editor.reload'))}</button>
        <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="theme" aria-hidden="true" hidden></span>
        <button type="button" class="file-editor-save-panel" title="${esc(t('editor.save'))}" aria-label="${esc(t('editor.saveFile'))}" ${readOnlyMode ? 'hidden' : ''}><span class="file-editor-icon file-editor-icon-save" aria-hidden="true"></span></button>
      </div>
      <div class="file-editor-panel-body panel-overlay-root">
        <div id="panel-toasts-${item}" class="panel-toast-stack"></div>
        <div class="file-editor-content">
          <div class="file-editor-codemirror-panel" hidden></div>
          <pre class="file-editor-raw-panel" hidden><code></code></pre>
          <div class="file-editor-preview-pane-panel markdown-body" hidden></div>
          <div class="file-editor-image-panel" hidden></div>
        </div>
        <div class="file-editor-status-panel"><span class="file-editor-status-message"></span><span class="file-editor-cursor-status"></span></div>
      </div>`;
  bindPanelShell(panel, item);
  panel.querySelectorAll('button').forEach(button => {
    button.addEventListener('pointerdown', event => event.stopPropagation());
  });
  panel.querySelector('.file-editor-save-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    saveFileEditor(path, panel);
  });
  panel.querySelector('.file-editor-reload-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    reloadOpenFileFromDisk(path);
  });
  panel.querySelector('.file-editor-mode-control-panel')?.addEventListener('click', event => {
    const mode = event.target?.closest?.('[data-editor-mode]')?.dataset?.editorMode;
    if (!editorViewModes.has(mode)) return;
    event.preventDefault();
    event.stopPropagation();
    setFileEditorViewMode(path, mode, item);
    renderFileEditorPanel(panel, item);
  });
  panel.querySelector('.file-editor-gutter-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorLineNumbers();
  });
  panel.querySelector('.file-editor-wrap-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorWrap();
  });
  panel.querySelector('.file-editor-find-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    openEditorFind(panel);
  });
  panel.querySelector('.file-editor-diff-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const nextMode = editorViewModeFor(path, item) === 'diff' ? 'edit' : 'diff';
    setFileEditorViewMode(path, nextMode, item);
    if (nextMode === 'diff') refreshOpenFileDiff(path, {silent: true});
    renderFileEditorPanel(panel, item);
  });
  const diffRefPanel = panel.querySelector('.file-editor-diff-ref-panel');
  diffRefPanel?.addEventListener('change', event => {
    const input = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (input) {
      event.preventDefault();
      event.stopPropagation();
      commitDiffRefControls(diffRefPanel);
    }
  });
  diffRefPanel?.addEventListener('keydown', event => {
    const input = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!input) return;
    event.stopPropagation();
    if (event.key === 'Enter') {
      event.preventDefault();
      commitDiffRefControls(diffRefPanel);
      input.blur?.();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      input.value = input.matches('[data-diff-ref-from]') ? diffRefFrom : diffRefTo;
      input.blur?.();
    }
  });
  panel.querySelector('.file-editor-cross-split-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    openFileCrossPaneSplit(path);
  });
  panel.querySelector('.file-editor-theme-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cycleEditorThemeMode();
  });
  panel.querySelector('.file-editor-preview-pane-panel')?.addEventListener('scroll', () => syncFileEditorSplitScroll(panel, 'preview'));
  renderFileEditorPanel(panel, item);
  return panel;
}

function hideFileEditorContent(rawPane, previewPane, imagePane, codeMirrorPane = null) {
  if (rawPane) rawPane.hidden = true;
  if (previewPane) previewPane.hidden = true;
  if (codeMirrorPane) codeMirrorPane.hidden = true;
  if (imagePane) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.hidden = true;
    imagePane.replaceChildren();
  }
}

function setElementsHidden(elements, hidden) {
  for (const element of elements) {
    if (element) element.hidden = hidden;
  }
}

function fileEditorEmptyState(title, detail = '') {
  const node = document.createElement('div');
  node.className = 'file-editor-empty-state';
  const titleNode = document.createElement('div');
  titleNode.className = 'file-editor-empty-title';
  titleNode.textContent = title;
  node.appendChild(titleNode);
  if (detail) {
    const detailNode = document.createElement('div');
    detailNode.className = 'file-editor-empty-detail';
    detailNode.textContent = detail;
    node.appendChild(detailNode);
  }
  return node;
}

function disconnectFileEditorImageObserver(imagePane) {
  if (imagePane?._imageResizeObserver) {
    imagePane._imageResizeObserver.disconnect();
    imagePane._imageResizeObserver = null;
  }
}

function fittedFileEditorImageSize(imagePane, img) {
  const naturalWidth = img.naturalWidth || 0;
  const naturalHeight = img.naturalHeight || 0;
  if (!naturalWidth || !naturalHeight) return null;
  const availableWidth = Math.max(1, imagePane.clientWidth - 20);
  const availableHeight = Math.max(1, imagePane.clientHeight - 20);
  const scale = Math.min(1, availableWidth / naturalWidth, availableHeight / naturalHeight);
  return {
    width: Math.max(1, Math.floor(naturalWidth * scale)),
    height: Math.max(1, Math.floor(naturalHeight * scale)),
  };
}

function applyFileEditorImageMode(imagePane, img, path, options = {}) {
  const preserveScroll = options.preserveScroll === true;
  const scrollLeft = preserveScroll ? imagePane.scrollLeft : 0;
  const scrollTop = preserveScroll ? imagePane.scrollTop : 0;
  const original = fileEditorImageMode.get(path) === 'original';
  imagePane.classList.toggle('original-size', original);
  imagePane.classList.toggle('fit-size', !original);
  img.classList.toggle('original-size', original);
  img.classList.toggle('fit-size', !original);
  const size = original
    ? {width: img.naturalWidth || img.width, height: img.naturalHeight || img.height}
    : fittedFileEditorImageSize(imagePane, img);
  if (size?.width && size?.height) {
    img.style.width = `${size.width}px`;
    img.style.height = `${size.height}px`;
  }
  img.title = original ? 'Click to fit image' : 'Click to view original size';
  if (preserveScroll) {
    imagePane.scrollLeft = scrollLeft;
    imagePane.scrollTop = scrollTop;
    requestAnimationFrame(() => {
      imagePane.scrollLeft = scrollLeft;
      imagePane.scrollTop = scrollTop;
    });
  }
}

function renderFileEditorImagePane(imagePane, path, state, status) {
  if (!imagePane) return;
  const version = String(state.mtime || state.size || 0);
  const sameImage = imagePane.dataset.imagePath === path && imagePane.dataset.imageVersion === version;
  let img = sameImage ? imagePane.querySelector(':scope > .file-editor-image') : null;
  if (!img) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.replaceChildren();
    img = document.createElement('img');
    img.className = 'file-editor-image';
    img.src = rawFileUrl(path, {v: version});
    img.alt = path;
    img.addEventListener('click', () => {
      const nextMode = fileEditorImageMode.get(path) === 'original' ? 'fit' : 'original';
      fileEditorImageMode.set(path, nextMode);
      applyFileEditorImageMode(imagePane, img, path, {preserveScroll: true});
    });
    if (typeof ResizeObserver === 'function') {
      const resizeObserver = new ResizeObserver(() => applyFileEditorImageMode(imagePane, img, path, {preserveScroll: true}));
      imagePane._imageResizeObserver = resizeObserver;
      resizeObserver.observe(imagePane);
    }
    img.onload = () => {
      applyFileEditorImageMode(imagePane, img, path, {preserveScroll: true});
      status(`${img.naturalWidth}x${img.naturalHeight}`, '');
    };
    img.onerror = () => {
      disconnectFileEditorImageObserver(imagePane);
      imagePane.replaceChildren(fileEditorEmptyState('Image could not be loaded', `The file may be unreadable, unsupported, or over ${formatFileSize(MAX_FILE_PREVIEW_BYTES)}.`));
      status('failed to load image', 'error');
    };
    imagePane.dataset.imagePath = path;
    imagePane.dataset.imageVersion = version;
    imagePane.appendChild(img);
    status(t('common.loading'), '');
  }
  applyFileEditorImageMode(imagePane, img, path, {preserveScroll: sameImage});
}

function requestFileEditorPanelFocus(item) {
  if (!autoFocusEnabled) return;
  if (isFileEditorItem(item)) pendingFileEditorFocus.add(item);
}

function focusFileEditorPanelIfReady(panel, item) {
  if (!autoFocusEnabled) {
    pendingFileEditorFocus.delete(item);
    return false;
  }
  if (!pendingFileEditorFocus.has(item) || focusedPanelItem !== item) return false;
  if (panel?._cmView) {
    panel._cmView.focus?.();
    pendingFileEditorFocus.delete(item);
    return true;
  }
  return false;
}

function captureFileEditorPanelViewState(item, panel) {
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!isFileEditorItem(item) || !view || !scrollDOM) return;
  const selection = view.state?.selection?.main;
  fileEditorViewState.set(item, {
    scrollTop: scrollDOM.scrollTop || 0,
    scrollLeft: scrollDOM.scrollLeft || 0,
    anchor: Number(selection?.anchor || 0),
    head: Number(selection?.head || selection?.anchor || 0),
  });
}

function captureFileEditorPanelViewStateForItem(item) {
  if (!isFileEditorItem(item)) return false;
  const panel = panelNodes.get(item);
  if (!panel) return false;
  captureFileEditorPanelViewState(item, panel);
  return true;
}

function restoreFileEditorPanelViewState(item, panel) {
  const state = fileEditorViewState.get(item);
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!state || !view || !scrollDOM) return;
  const docLength = view.state?.doc?.length || 0;
  const anchor = Math.max(0, Math.min(docLength, Number(state.anchor || 0)));
  const head = Math.max(0, Math.min(docLength, Number(state.head || anchor)));
  try {
    view.dispatch({selection: {anchor, head}});
  } catch (_) {
    // View state restoration is best-effort; scroll preservation is the critical part.
  }
  const restore = () => {
    scrollDOM.scrollTop = Number(state.scrollTop || 0);
    scrollDOM.scrollLeft = Number(state.scrollLeft || 0);
  };
  const measuredRestore = () => {
    if (typeof view.requestMeasure === 'function') {
      view.requestMeasure({
        read: () => null,
        write: restore,
      });
      return;
    }
    restore();
  };
  restore();
  measuredRestore();
  requestAnimationFrame(measuredRestore);
  requestAnimationFrame(() => requestAnimationFrame(measuredRestore));
}

function destroyCodeMirrorPanel(panel) {
  panel?._cmResizeObserver?.disconnect?.();
  if (panel) panel._cmResizeObserver = null;
  // Clear the diff scrollbar overview so its red/green ticks don't linger after switching to
  // edit/normal mode (only the diff build re-adds it via updateCodeMirrorDiffOverview).
  panel?.querySelector?.('.cm-diff-overview')?.remove();
  if (panel?._cmMergeView) {
    panel._cmMergeView.destroy();
    panel._cmMergeView = null;
  }
  if (panel?._cmView) {
    panel._cmView.destroy();
    panel._cmView = null;
  }
  if (panel) {
    panel._cmApi = null;
    panel._cmThemeCompartment = null;
    panel._cmThemeViews = [];
    panel._cmPath = '';
    panel._cmSignature = '';
    panel._cmMode = '';
    panel._cmPlainFallback = false;
  }
}

function codeMirrorPanelContent(panel) {
  return panel?._cmView?.state?.doc?.toString?.() ?? null;
}

function textFingerprint(text) {
  const source = String(text || '');
  let hash = 0;
  for (let index = 0; index < source.length; index += 1) {
    hash = ((hash << 5) - hash + source.charCodeAt(index)) | 0;
  }
  return `${source.length}:${hash}`;
}

function codeMirrorConfigSignature(path, options = {}) {
  return JSON.stringify({
    mode: options.mode || 'edit',
    layout: options.layout || '',
    original: options.original ? textFingerprint(options.original) : '',
    from: options.from || '',
    to: options.to || '',
    language: codeMirrorLanguageName(path),
    wrap: fileEditorWrapEnabled,
    lineNumbers: fileEditorLineNumbersEnabled,
    readOnly: readOnlyMode,
  });
}

function codeMirrorDiffLayout(_container) {
  // Always use the unified (inline) merge view at every pane width: deleted rows render as read-only
  // widgets with NO line number. The side-by-side MergeView numbers the old file (including deleted
  // rows), and @codemirror/merge exposes no public chunk access to suppress only those numbers, so a
  // wide pane previously showed numbered red lines (image 075). Unified guarantees the user's "no
  // number on red lines in ANY layout" requirement.
  return 'inline';
}

function codeMirrorReadOnlyExtensions(api, path, panel = null, options = {}) {
  const wrapEnabled = options.wrap !== false && fileEditorWrapEnabled;
  return [
    api.drawSelection(),
    api.highlightActiveLine(),
    ...(fileEditorLineNumbersEnabled ? [api.lineNumbers(), api.highlightActiveLineGutter()] : []),
    ...(wrapEnabled ? [api.EditorView.lineWrapping, codeMirrorWrapMarkerExtension(api)] : []),
    api.EditorState.readOnly.of(true),
    api.EditorView.editable.of(false),
    codeMirrorLanguageExtension(api, path),
    codeMirrorThemedExtensions(api, panel, path),
  ];
}

function codeMirrorWorkingUpdateExtension(api, panel, path) {
  return api.EditorView.updateListener.of(update => {
    if (update.docChanged || update.selectionSet) updateCodeMirrorCursorStatus(panel);
    if (update.docChanged) {
      handleFileEditorContentChanged(panel, path, update.state.doc.toString(), {syntax: false});
    }
  });
}

function syncCodeMirrorDocument(view, text, options = {}) {
  if (!view) return;
  const next = String(text || '');
  if (view.state.doc.toString() === next) return;
  if (options.cleanOnly && openFiles.get(options.path)?.dirty) return;
  const scrollDOM = view.scrollDOM;
  const scrollTop = scrollDOM?.scrollTop || 0;
  const scrollLeft = scrollDOM?.scrollLeft || 0;
  const selection = view.state.selection;
  const selectionFits = selection?.ranges?.every(range => (
    range.anchor <= next.length && range.head <= next.length
  ));
  view.dispatch({
    changes: {from: 0, to: view.state.doc.length, insert: next},
    ...(selectionFits ? {selection} : {}),
  });
  const restoreScroll = () => {
    if (!scrollDOM) return;
    scrollDOM.scrollTop = scrollTop;
    scrollDOM.scrollLeft = scrollLeft;
  };
  if (typeof view.requestMeasure === 'function') {
    view.requestMeasure({write: restoreScroll});
  } else {
    requestAnimationFrame(restoreScroll);
  }
  requestAnimationFrame(restoreScroll);
}

function codeMirrorThemeExtensions(api, path) {
  return [
    codeMirrorHighlightExtension(api),
    codeMirrorHtmlSemanticEmphasisExtension(api, path),
    codeMirrorMarkdownStrongExtension(api, path),
    codeMirrorThemeExtension(api),
  ];
}

function codeMirrorThemedExtensions(api, panel, path) {
  const extensions = codeMirrorThemeExtensions(api, path);
  if (!panel || !api.Compartment) return extensions;
  panel._cmThemeCompartment = panel._cmThemeCompartment || new api.Compartment();
  return panel._cmThemeCompartment.of(extensions);
}

function codeMirrorThemeOnlyExtensions(api, panel) {
  const extensions = [codeMirrorThemeExtension(api)];
  if (!panel || !api.Compartment) return extensions;
  panel._cmThemeCompartment = panel._cmThemeCompartment || new api.Compartment();
  return panel._cmThemeCompartment.of(extensions);
}

function codeMirrorPlainEditableExtensions(api, panel, path, options = {}) {
  const save = options.save || (() => saveFileEditor(path, panel));
  const saveKeymap = safeCodeMirrorExtension('save keymap', () => api.keymap.of([{
    key: 'Mod-s',
    run() {
      save();
      return true;
    },
  }]));
  const findKeymap = safeCodeMirrorExtension('find keymap', () => (api.openSearchPanel ? api.keymap.of([{
    key: 'Mod-f',
    run(view) {
      return openCodeMirrorFindForView(api, view);
    },
  }]) : []));
  const defaultKeymap = safeCodeMirrorExtension('default keymap', () => api.keymap.of([
    api.indentWithTab,
    ...(Array.isArray(api.defaultKeymap) ? api.defaultKeymap : []),
    ...(Array.isArray(api.historyKeymap) ? api.historyKeymap : []),
    ...(Array.isArray(api.searchKeymap) ? api.searchKeymap : []),
  ].filter(Boolean)));
  return [
    safeCodeMirrorExtension('history', () => api.history?.()),
    safeCodeMirrorExtension('selection drawing', () => api.drawSelection()),
    safeCodeMirrorExtension('drop cursor', () => api.dropCursor?.()),
    safeCodeMirrorExtension('active line', () => api.highlightActiveLine()),
    ...(fileEditorLineNumbersEnabled ? [
      safeCodeMirrorExtension('line numbers', () => api.lineNumbers?.()),
      safeCodeMirrorExtension('active line gutter', () => api.highlightActiveLineGutter?.()),
    ] : []),
    safeCodeMirrorExtension('search', () => api.search({top: true})),
    codeMirrorSearchPanelEnhancementExtension(api),
    safeCodeMirrorExtension('search matches', () => api.highlightSelectionMatches?.()),
    saveKeymap,
    findKeymap,
    defaultKeymap,
    ...(fileEditorWrapEnabled ? [api.EditorView.lineWrapping, codeMirrorWrapMarkerExtension(api)] : []),
    safeCodeMirrorExtension('read only', () => api.EditorState.readOnly.of(readOnlyMode)),
    safeCodeMirrorExtension('editable', () => api.EditorView.editable.of(!readOnlyMode)),
    codeMirrorThemeOnlyExtensions(api, panel),
    codeMirrorWorkingUpdateExtension(api, panel, path),
  ];
}

function createEditableCodeMirrorState(api, panel, path, doc) {
  try {
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorExtensions(api, panel, path),
      }),
      plain: false,
    };
  } catch (error) {
    console.warn('CodeMirror language parser failed; retrying plain editable editor', error);
    if (panel) panel._cmThemeCompartment = null;
    return {
      state: api.EditorState.create({
        doc,
        extensions: codeMirrorPlainEditableExtensions(api, panel, path),
      }),
      plain: true,
      error,
    };
  }
}

function trackCodeMirrorThemeViews(panel, api, views) {
  if (!panel) return;
  panel._cmApi = api;
  panel._cmThemeViews = views.filter(Boolean);
}

function reconfigureCodeMirrorPanelTheme(panel) {
  const api = panel?._cmApi;
  const path = panel?._cmPath;
  const compartment = panel?._cmThemeCompartment;
  const views = Array.isArray(panel?._cmThemeViews) ? panel._cmThemeViews : [];
  if (!api || !path || !compartment || !views.length) return false;
  const extensions = panel?._cmPlainFallback ? [codeMirrorThemeExtension(api)] : codeMirrorThemeExtensions(api, path);
  const effect = compartment.reconfigure(extensions);
  for (const view of views) {
    try { view.dispatch({effects: effect}); } catch (_) {}
  }
  return true;
}

function diffOverviewCollector() {
  const chunks = [];
  let current = null;
  const pushCurrent = () => {
    if (current) chunks.push(current);
    current = null;
  };
  return {
    add(kind, line) {
      const start = Math.max(1, Number(line || 1));
      if (current && current.kind === kind && start <= current.end + 1) {
        current.end = Math.max(current.end, start);
        return;
      }
      pushCurrent();
      current = {kind, start, end: start};
    },
    flush() {
      pushCurrent();
    },
    finish() {
      pushCurrent();
      return chunks;
    },
  };
}

function diffOverviewChunkPercentages(chunks, totalLines) {
  return chunks.map(chunk => ({
    ...chunk,
    top: Math.max(0, Math.min(100, ((chunk.start - 1) / Math.max(1, totalLines)) * 100)),
    height: Math.max(0.8, ((chunk.end - chunk.start + 1) / Math.max(1, totalLines)) * 100),
  }));
}

function diffOverviewChunks(diff, totalLines) {
  const collector = diffOverviewCollector();
  let oldLine = 0;
  let newLine = 0;
  for (const line of String(diff || '').split('\n')) {
    const hunk = /^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
    if (hunk) {
      collector.flush();
      oldLine = Math.max(1, Number(hunk[1]) || 1);
      newLine = Math.max(1, Number(hunk[2]) || 1);
      continue;
    }
    if (!newLine || line.startsWith('+++') || line.startsWith('---')) continue;
    if (line.startsWith('+')) {
      collector.add('add', newLine);
      newLine += 1;
    } else if (line.startsWith('-')) {
      collector.add('remove', newLine || oldLine);
      oldLine += 1;
    } else {
      collector.flush();
      oldLine += 1;
      newLine += 1;
    }
  }
  return diffOverviewChunkPercentages(collector.finish(), totalLines);
}

function diffOverviewChunksFromDocuments(original, current, totalLines) {
  const left = String(original || '').split('\n');
  const right = String(current || '').split('\n');
  const collector = diffOverviewCollector();
  const max = Math.max(left.length, right.length);
  for (let index = 0; index < max; index += 1) {
    const leftLine = left[index];
    const rightLine = right[index];
    if (leftLine === rightLine) {
      collector.flush();
      continue;
    }
    if (rightLine !== undefined) collector.add('add', index + 1);
    if (leftLine !== undefined) collector.add('remove', Math.min(index + 1, totalLines));
  }
  return diffOverviewChunkPercentages(collector.finish(), totalLines);
}

function updateCodeMirrorDiffOverview(panel, container, state, currentText, original) {
  container?.querySelector?.('.cm-diff-overview')?.remove();
  const totalLines = Math.max(String(currentText || '').split('\n').length, String(original || '').split('\n').length, 1);
  const parsedChunks = diffOverviewChunks(state?.diff || '', totalLines);
  const chunks = parsedChunks.length ? parsedChunks : diffOverviewChunksFromDocuments(original, currentText, totalLines);
  if (!container || !chunks.length) return;
  const overview = document.createElement('div');
  overview.className = 'cm-diff-overview';
  overview.setAttribute('aria-hidden', 'true');
  for (const chunk of chunks) {
    const tick = document.createElement('button');
    tick.type = 'button';
    tick.className = `cm-diff-overview-tick ${chunk.kind}`;
    tick.style.top = `${chunk.top}%`;
    tick.style.height = `${chunk.height}%`;
    tick.title = `${chunk.kind === 'add' ? 'Added' : 'Removed'} lines ${chunk.start}-${chunk.end}`;
    tick.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const scrollTarget = panel._cmView?.scrollDOM || container.querySelector('.cm-scroller') || container.querySelector('.cm-mergeView');
      if (!scrollTarget) return;
      const maxTop = Math.max(0, scrollTarget.scrollHeight - scrollTarget.clientHeight);
      scrollTarget.scrollTop = Math.round(maxTop * (chunk.top / 100));
    });
    overview.appendChild(tick);
  }
  container.appendChild(overview);
}

function installCodeMirrorDiffCollapsedScrollGuard(panel, container) {
  if (!container || container.dataset.diffCollapsedScrollGuard === 'true') return;
  container.dataset.diffCollapsedScrollGuard = 'true';
  container.addEventListener('wheel', event => {
    if (!event.target?.closest?.('.cm-collapsedLines')) return;
    const scrollTarget = event.target.closest('.cm-scroller') || event.target.closest('.cm-mergeView') || panel._cmView?.scrollDOM;
    if (!scrollTarget) return;
    const before = scrollTarget.scrollTop;
    scrollTarget.scrollTop += event.deltaY;
    if (scrollTarget.scrollTop !== before) event.preventDefault();
  }, {passive: false});
}

function installCodeMirrorDiffResizeObserver(panel, item, path, container) {
  if (!window.ResizeObserver || panel._cmResizeObserver) return;
  let frame = 0;
  panel._cmResizeObserver = new ResizeObserver(() => {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (editorViewModeFor(path, item) !== 'diff') return;
      const nextLayout = codeMirrorDiffLayout(container);
      if (nextLayout !== panel._cmDiffLayout) renderFileEditorPanel(panel, item);
    });
  });
  panel._cmResizeObserver.observe(container);
}

async function ensureCodeMirrorDiffPanel(panel, item, path, state) {
  const container = panel.querySelector('.file-editor-codemirror-panel');
  if (!container) return false;
  const generation = (panel._cmGeneration || 0) + 1;
  panel._cmGeneration = generation;
  container.hidden = false;
  container.classList.add('file-editor-diff-codemirror');
  // Always await the diff load (the dedup'd in-flight promise) before rendering, so we never build a
  // MergeView against an un-loaded/empty original — an untracked/all-added file then resolves to
  // diffUnavailable and falls back to the plain editor below instead of flashing all-green.
  if (!state.diffLoaded && !state.diffUnavailable) await refreshOpenFileDiff(path, {silent: true});
  if (panel._cmGeneration !== generation) return null;
  if (!openFileDiffAvailable(state)) {
    setFileEditorPanelStatus(panel, state.diffError ? `diff unavailable: ${state.diffError}` : 'No diff for this file', 'warn');
    return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
  }
  const original = String(state.diffOriginal || '');
  const api = await loadCodeMirrorApi();
  if (panel._cmGeneration !== generation) return null;
  if (!api.MergeView || !api.unifiedMergeView) {
    setFileEditorPanelStatus(panel, 'CodeMirror merge view is unavailable', 'error');
    return false;
  }
  const layout = codeMirrorDiffLayout(container);
  const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';
  const currentText = diffTargetIsCurrent ? String(state.content || '') : String(state.diffWorking || '');
  const diffEditsAllowed = diffTargetIsCurrent;
  const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef});
  installCodeMirrorDiffCollapsedScrollGuard(panel, container);
  if (panel._cmView && panel._cmMode === 'diff' && panel._cmSignature === signature) {
    installCodeMirrorDiffResizeObserver(panel, item, path, container);
    if (layout === 'side') {
      syncCodeMirrorDocument(panel._cmMergeView?.a, original);
      syncCodeMirrorDocument(panel._cmMergeView?.b, currentText, {cleanOnly: true, path});
    } else {
      syncCodeMirrorDocument(panel._cmView, currentText, {cleanOnly: true, path});
    }
    updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
    restoreFileEditorPanelViewState(item, panel);
    updateCodeMirrorCursorStatus(panel);
    focusFileEditorPanelIfReady(panel, item);
    return true;
  }
  captureFileEditorPanelViewState(item, panel);
  destroyCodeMirrorPanel(panel);
  container.replaceChildren();
  panel._cmDiffLayout = layout;
  installCodeMirrorDiffResizeObserver(panel, item, path, container);
  installCodeMirrorDiffCollapsedScrollGuard(panel, container);
  if (layout === 'side') {
    panel._cmMergeView = new api.MergeView({
      a: {
        doc: original,
        extensions: [
          api.drawSelection(),
          api.highlightActiveLine(),
          ...(fileEditorLineNumbersEnabled ? [api.lineNumbers(), api.highlightActiveLineGutter()] : []),
          api.EditorState.readOnly.of(true),
          api.EditorView.editable.of(false),
          codeMirrorLanguageExtension(api, path),
          codeMirrorThemedExtensions(api, panel, path),
        ],
      },
      b: {
        doc: currentText,
        extensions: diffEditsAllowed
          ? [
              ...codeMirrorExtensions(api, panel, path, {wrap: false}),
              codeMirrorWorkingUpdateExtension(api, panel, path),
            ]
          : codeMirrorReadOnlyExtensions(api, path, panel, {wrap: false}),
      },
      parent: container,
      revertControls: 'a-to-b',
      // DOIT.6 #44: show each change as TWO uniform full lines (old solid red + new solid green) with
      // NO intra-line word/token highlight — even a 1-char edit shows whole-line red + whole-line green.
      highlightChanges: false,
      gutter: true,
      collapseUnchanged: {margin: 3, minSize: 8},
    });
    panel._cmView = panel._cmMergeView.b;
    trackCodeMirrorThemeViews(panel, api, [panel._cmMergeView.a, panel._cmMergeView.b]);
    panel._cmMergeView.b.scrollDOM?.addEventListener('scroll', () => syncFileEditorSplitScroll(panel, 'editor'));
  } else {
    const cmState = api.EditorState.create({
      doc: currentText,
      extensions: [
        api.unifiedMergeView({
          original,
          // DOIT.6 #44: full-line red/green only, no intra-line token highlight (see MergeView above).
          highlightChanges: false,
          gutter: true,
          mergeControls: !readOnlyMode && diffEditsAllowed,
          collapseUnchanged: {margin: 3, minSize: 8},
        }),
        ...(diffEditsAllowed ? codeMirrorExtensions(api, panel, path) : codeMirrorReadOnlyExtensions(api, path, panel)),
      ],
    });
    panel._cmView = new api.EditorView({
      state: cmState,
      parent: container,
      dispatch(transaction) {
        panel._cmView.update([transaction]);
        if (transaction.docChanged || transaction.selectionSet) updateCodeMirrorCursorStatus(panel);
        if (transaction.docChanged) {
          handleFileEditorContentChanged(panel, path, panel._cmView.state.doc.toString(), {syntax: false});
        }
      },
    });
    panel._cmView.scrollDOM?.addEventListener('scroll', () => syncFileEditorSplitScroll(panel, 'editor'));
    trackCodeMirrorThemeViews(panel, api, [panel._cmView]);
  }
  panel._cmPath = path;
  panel._cmSignature = signature;
  panel._cmMode = 'diff';
  updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
  restoreFileEditorPanelViewState(item, panel);
  updateCodeMirrorCursorStatus(panel);
  focusFileEditorPanelIfReady(panel, item);
  return true;
}

async function ensureCodeMirrorPanel(panel, item, path, state, options = {}) {
  const container = panel.querySelector('.file-editor-codemirror-panel');
  if (!container) return false;
  if (options.forceMode !== 'edit' && editorViewModeFor(path, item) === 'diff') return ensureCodeMirrorDiffPanel(panel, item, path, state);
  const generation = (panel._cmGeneration || 0) + 1;
  panel._cmGeneration = generation;
  container.hidden = false;
  container.classList.remove('file-editor-diff-codemirror');
  const currentText = String(state.content || '');
  const signature = codeMirrorConfigSignature(path, {mode: 'edit'});
  if (!panel._cmView || panel._cmPath !== path || panel._cmSignature !== signature) {
    captureFileEditorPanelViewState(item, panel);
    destroyCodeMirrorPanel(panel);
    container.textContent = 'loading CodeMirror...';
  }
  try {
    const api = await loadCodeMirrorApi();
    if (panel._cmGeneration !== generation) return null;
    if (!panel._cmView) {
      container.replaceChildren();
      const createdState = createEditableCodeMirrorState(api, panel, path, currentText);
      panel._cmView = new api.EditorView({
        state: createdState.state,
        parent: container,
        dispatch(transaction) {
          panel._cmView.update([transaction]);
          if (transaction.docChanged || transaction.selectionSet) updateCodeMirrorCursorStatus(panel);
          if (transaction.docChanged) {
            handleFileEditorContentChanged(panel, path, panel._cmView.state.doc.toString(), {syntax: false});
          }
        },
      });
      panel._cmPath = path;
      panel._cmSignature = signature;
      panel._cmPlainFallback = Boolean(createdState.plain);
      panel._cmView.scrollDOM?.addEventListener('scroll', () => syncFileEditorSplitScroll(panel, 'editor'));
      trackCodeMirrorThemeViews(panel, api, [panel._cmView]);
      updateCodeMirrorCursorStatus(panel);
      if (createdState.plain) {
        setFileEditorPanelStatus(panel, 'CodeMirror language parser failed; editing as plain text', 'warn');
      }
    } else if (panel._cmView.state.doc.toString() !== currentText && !state.dirty) {
      panel._cmView.dispatch({
        changes: {from: 0, to: panel._cmView.state.doc.length, insert: currentText},
      });
      updateCodeMirrorCursorStatus(panel);
    }
    restoreFileEditorPanelViewState(item, panel);
    focusFileEditorPanelIfReady(panel, item);
    return true;
  } catch (error) {
    if (panel._cmGeneration !== generation) return null;
    destroyCodeMirrorPanel(panel);
    container.hidden = true;
    setFileEditorPanelStatus(panel, `CodeMirror unavailable; showing read-only raw text (${error})`, 'error');
    return false;
  }
}

function renderFileEditorRawPane(rawPane, path, content) {
  if (!rawPane) return;
  const code = rawPane.querySelector('code');
  if (!code) return;
  const language = syntaxLanguageForPath(path);
  rawPane.hidden = false;
  rawPane.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
  rawPane.classList.toggle('editor-wrap', fileEditorWrapEnabled);
  code.className = `language-${language || 'text'}`;
  code.innerHTML = editorVisualHighlightHtml(language, content, {
    wrap: fileEditorWrapEnabled,
    lineNumbers: fileEditorLineNumbersEnabled,
  });
}

function renderFileEditorPanel(panel, item) {
  const path = fileItemPath(item);
  captureFileEditorPanelViewState(item, panel);
  activeFile = path;
  updateFileExplorerCurrentFileHighlight();
  const state = openFiles.get(path);
  updateFileEditorPanelChrome(panel, path);
  const codeMirrorPane = panel.querySelector('.file-editor-codemirror-panel');
  const rawPane = panel.querySelector('.file-editor-raw-panel');
  const previewPane = panel.querySelector('.file-editor-preview-pane-panel');
  const imagePane = panel.querySelector('.file-editor-image-panel');
  const modeControl = panel.querySelector('.file-editor-mode-control-panel');
  const gutterButton = panel.querySelector('.file-editor-gutter-panel');
  const wrapButton = panel.querySelector('.file-editor-wrap-panel');
  const findButton = panel.querySelector('.file-editor-find-panel');
  const diffButton = panel.querySelector('.file-editor-diff-panel');
  const diffRefPanel = panel.querySelector('.file-editor-diff-ref-panel');
  const crossSplitButton = panel.querySelector('.file-editor-cross-split-panel');
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  const themeButton = panel.querySelector('.file-editor-theme-panel');
  const content = panel.querySelector('.file-editor-content');
  const textControls = [modeControl, gutterButton, wrapButton, findButton, diffButton, diffRefPanel, crossSplitButton, reloadButton];
  updateEditorThemeButton(themeButton);
  if (!state) {
    setElementsHidden(textControls, true);
    updateFileEditorToolbarSeparators(panel);
    panel.classList.remove('syntax-highlighted');
    destroyCodeMirrorPanel(panel);
    hideFileEditorContent(rawPane, previewPane, imagePane, codeMirrorPane);
    setFileEditorPanelStatus(panel, 'file closed', '');
    return;
  }
  if (state.loading) {
    setElementsHidden(textControls, true);
    updateFileEditorToolbarSeparators(panel);
    panel.classList.remove('syntax-highlighted');
    destroyCodeMirrorPanel(panel);
    hideFileEditorContent(rawPane, previewPane, imagePane, codeMirrorPane);
    setFileEditorPanelStatus(panel, t('common.loading'), '');
    loadFileEditorState(path, panel, item);
    return;
  }
  if (state.kind === 'error' || state.kind === 'too-large') {
    setElementsHidden(textControls, true);
    updateFileEditorToolbarSeparators(panel);
    panel.classList.remove('syntax-highlighted');
    destroyCodeMirrorPanel(panel);
    if (rawPane) rawPane.hidden = true;
    if (codeMirrorPane) codeMirrorPane.hidden = true;
    if (previewPane) previewPane.hidden = true;
    if (imagePane) {
      imagePane.hidden = false;
      const limit = formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES);
      const size = formatFileSize(state.size);
      const title = state.kind === 'too-large' ? 'File is too large to preview' : 'File could not be opened';
      const detail = state.kind === 'too-large'
        ? (state.error || `${size ? `${size}; ` : ''}limit is ${limit}`)
        : String(state.error || 'failed to load file');
      imagePane.replaceChildren(fileEditorEmptyState(title, detail));
    }
    const status = state.kind === 'too-large' ? `too large; limit ${formatFileSize(state.maxBytes || MAX_FILE_PREVIEW_BYTES)}` : state.error || 'failed to load file';
    setFileEditorPanelStatus(panel, status, 'error');
    return;
  }
  if (state.kind === 'text' && !state.diffLoaded && !state.diffLoading && !state.diffUnavailable) {
    refreshOpenFileDiff(path, {silent: true});
  }
  if (state.kind === 'text' && editorViewModeFor(path, item) === 'diff' && state.diffLoaded && !state.diffLoading && !openFileDiffAvailable(state)) {
    setFileEditorViewMode(path, 'edit', item);
  }
  const mode = editorViewModeFor(path, item);
  updateEditorModeControl(modeControl, path, state, item);
  if (gutterButton) {
    gutterButton.hidden = isFilePreviewItem(item) || state.kind !== 'text';
    updateEditorGutterButton(gutterButton);
  }
  if (wrapButton) {
    wrapButton.hidden = isFilePreviewItem(item) || state.kind !== 'text';
    updateEditorWrapButton(wrapButton);
  }
  updateEditorFindButton(findButton, state);
  if (findButton && isFilePreviewItem(item)) findButton.hidden = true;
  updateFileEditorDiffButton(diffButton, path, state, item);
  if (crossSplitButton) {
    crossSplitButton.hidden = isFilePreviewItem(item) || state.kind !== 'text' || !editorPreviewModeAvailable(path);
  }
  if (diffRefPanel) {
    diffRefPanel.hidden = mode !== 'diff' || state.kind !== 'text';
    syncDiffRefControlValues(diffRefPanel);
  }
  updateFileEditorToolbarSeparators(panel);
  if (state.kind === 'image') {
    updateImageViewerThemeButton(themeButton);
    setEditorContentMode(content, 'edit');
    destroyCodeMirrorPanel(panel);
    if (rawPane) rawPane.hidden = true;
    if (codeMirrorPane) codeMirrorPane.hidden = true;
    if (previewPane) previewPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    if (imagePane) {
      imagePane.hidden = false;
      renderFileEditorImagePane(imagePane, path, state, (message, level) => setFileEditorPanelStatus(panel, message, level));
    }
    return;
  }
  if (imagePane) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.hidden = true;
    imagePane.replaceChildren();
  }
  setEditorContentMode(content, mode);
  panel.classList.toggle('editor-wrap', fileEditorWrapEnabled);
  panel.classList.toggle('editor-line-numbers', fileEditorLineNumbersEnabled);
  if (mode === 'preview') {
    destroyCodeMirrorPanel(panel);
    if (rawPane) rawPane.hidden = true;
    if (codeMirrorPane) codeMirrorPane.hidden = true;
    panel.classList.remove('syntax-highlighted');
    if (previewPane) {
      previewPane.hidden = false;
      renderEditorPreviewPane(previewPane, path, state.content);
    }
  } else {
    if (rawPane) rawPane.hidden = true;
    if (previewPane) {
      previewPane.hidden = mode !== 'split';
      if (mode === 'split') renderEditorPreviewPane(previewPane, path, state.content);
    }
    panel.classList.remove('syntax-highlighted');
    ensureCodeMirrorPanel(panel, item, path, state).then(loaded => {
      if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);
    });
  }
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
  focusFileEditorPanelIfReady(panel, item);
}

function loadFileEditorState(path, panel, item) {
  const state = openFiles.get(path);
  if (!state || state.loadingPromise) return;
  const loadingPromise = (async () => {
    const ext = fileExtensionOf(basenameOf(path));
    if (IMAGE_EXTENSIONS.has(ext)) {
      const fetched = await fetchFileEntryStatus(path);
      const entry = fetched.entry;
      if (!entry) {
        if (fetched.missing) markOpenFileMissing(path);
        else openFiles.set(path, fileErrorState(fetched.error || 'failed to inspect image'));
        renderSessionButtons();
        renderPaneTabStrips();
        return;
      }
      if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
        const state = tooLargeFileState(Number(entry.size));
        state.mtime = fileEntryMtime(entry);
        openFiles.set(path, state);
      } else {
        openFiles.set(path, {mtime: fileEntryMtime(entry), kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null});
      }
      renderFileEditorPanel(panel, item);
      renderSessionButtons();
      renderPaneTabStrips();
      return;
    }
    try {
      const response = await apiFetch(`/api/fs/read?path=${encodeURIComponent(path)}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message = String(payload.error || response.status);
        openFiles.set(path, response.status === 413
          ? tooLargeFileState(null, message)
          : response.status === 404
            ? missingFileState(message)
            : fileErrorState(message));
      } else {
        const payload = await response.json();
        openFiles.set(path, {
          mtime: filePayloadMtime(payload),
          size: payload.size,
          kind: 'text',
          original: payload.content,
          content: payload.content,
          dirty: false,
        });
      }
    } catch (err) {
      openFiles.set(path, fileErrorState(err));
    }
    renderFileEditorPanel(panel, item);
    renderSessionButtons();
    renderPaneTabStrips();
  })().finally(() => {
    const current = openFiles.get(path);
    if (current?.loadingPromise === loadingPromise) delete current.loadingPromise;
  });
  state.loadingPromise = loadingPromise;
}

function updateFileEditorPanelChrome(panel, path) {
  const state = openFiles.get(path);
  const item = panel?.dataset?.layoutItem || '';
  const previewOnly = isFilePreviewItem(item);
  panel.classList.toggle('dirty', !!state?.dirty);
  const dirtyDot = panel.querySelector('.file-editor-title .file-tab-dirty');
  if (dirtyDot) dirtyDot.hidden = !state?.dirty;
  const nameNode = panel.querySelector('.file-editor-title-name');
  if (nameNode) nameNode.textContent = basenameOf(path);
  const saveButton = panel.querySelector('.file-editor-save-panel');
  if (saveButton) {
    saveButton.hidden = previewOnly || readOnlyMode || state?.kind !== 'text';
    saveButton.disabled = !state?.dirty;
  }
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  if (reloadButton) {
    reloadButton.hidden = previewOnly || !(state?.externalChanged || state?.externalMissing || state?.externalError);
  }
  updateFileEditorToolbarSeparators(panel);
}

function fileEditorToolbarControlVisible(panel, selector) {
  const node = panel?.querySelector(selector);
  return !!node && node.hidden !== true;
}

function setFileEditorToolbarSeparator(panel, key, visible) {
  const separator = panel?.querySelector(`[data-editor-toolbar-separator="${key}"]`);
  if (separator) separator.hidden = !visible;
}

function updateFileEditorToolbarSeparators(panel) {
  const mode = fileEditorToolbarControlVisible(panel, '.file-editor-mode-control-panel');
  const tools = [
    '.file-editor-gutter-panel',
    '.file-editor-wrap-panel',
    '.file-editor-find-panel',
    '.file-editor-diff-panel',
    '.file-editor-diff-ref-panel',
  ].some(selector => fileEditorToolbarControlVisible(panel, selector));
  const theme = fileEditorToolbarControlVisible(panel, '.file-editor-theme-panel')
    || fileEditorToolbarControlVisible(panel, '.file-editor-reload-panel');
  const save = fileEditorToolbarControlVisible(panel, '.file-editor-save-panel');
  // #42: the editor controls now live on their own toolbar row below the tab strip (no frame
  // controls sit beside them), so separators only sit between adjacent visible control groups.
  setFileEditorToolbarSeparator(panel, 'mode', mode && (tools || theme || save));
  setFileEditorToolbarSeparator(panel, 'tools', tools && (theme || save));
  setFileEditorToolbarSeparator(panel, 'theme', theme && save);
  const toolbar = panel?.querySelector?.('.file-editor-toolbar');
  if (toolbar) toolbar.hidden = !(mode || tools || theme || save);
}

function setFileEditorPanelStatus(panel, message, level) {
  const status = panel?.querySelector?.('.file-editor-status-panel');
  if (!status) return;
  let messageNode = status.querySelector('.file-editor-status-message');
  if (!messageNode) {
    status.textContent = '';
    messageNode = document.createElement('span');
    messageNode.className = 'file-editor-status-message';
    const cursorNode = document.createElement('span');
    cursorNode.className = 'file-editor-cursor-status';
    status.append(messageNode, cursorNode);
  }
  messageNode.textContent = message || '';
  status.dataset.level = level || '';
  updateCodeMirrorCursorStatus(panel);
}

function markdownTextWithSourceAnchors(text) {
  return String(text || '');
}

function applyMarkdownSourceLines(container, source) {
  const lines = String(source || '').split('\n');
  let searchFrom = 0;
  const blocks = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6,p,blockquote,pre,ul,ol,table,hr'));
  for (const block of blocks) {
    const text = String(block.textContent || '').trim();
    let lineIndex = -1;
    for (let index = searchFrom; index < lines.length; index += 1) {
      const trimmed = lines[index].trim();
      if (!trimmed) continue;
      if (block.tagName === 'HR' && /^-{3,}$/.test(trimmed)) {
        lineIndex = index;
        break;
      }
      if (block.tagName === 'TABLE' && trimmed.startsWith('|')) {
        lineIndex = index;
        break;
      }
      if (text && trimmed.includes(text.slice(0, Math.min(text.length, 40)))) {
        lineIndex = index;
        break;
      }
    }
    if (lineIndex >= 0) {
      block.dataset.sourceLine = String(lineIndex + 1);
      const anchor = document.createElement('span');
      anchor.className = 'markdown-source-anchor';
      anchor.dataset.sourceLine = String(lineIndex + 1);
      block.appendChild(anchor);
      searchFrom = lineIndex + 1;
    }
  }
}

const MARKDOWN_PREVIEW_BLOCKED_TAGS = new Set([
  'applet',
  'audio',
  'base',
  'button',
  'canvas',
  'embed',
  'form',
  'iframe',
  'input',
  'link',
  'math',
  'meta',
  'object',
  'option',
  'script',
  'select',
  'source',
  'style',
  'svg',
  'textarea',
  'track',
  'video',
]);
const MARKDOWN_PREVIEW_URL_ATTRS = new Set(['href', 'src', 'poster', 'xlink:href']);
const MARKDOWN_PREVIEW_SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);
const MARKDOWN_PREVIEW_SAFE_IMAGE_DATA = /^data:image\/(?:png|gif|jpe?g|webp);/i;

function markdownPreviewUrlAllowed(value, tagName) {
  const raw = String(value || '').trim();
  if (!raw) return true;
  if (raw.startsWith('#') || raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../')) return true;
  try {
    const base = globalThis.location?.href || 'http://localhost/';
    const url = new URL(raw, base);
    if (MARKDOWN_PREVIEW_SAFE_PROTOCOLS.has(url.protocol.toLowerCase())) return true;
    return tagName === 'img' && url.protocol.toLowerCase() === 'data:' && MARKDOWN_PREVIEW_SAFE_IMAGE_DATA.test(raw);
  } catch (_) {
    return false;
  }
}

function sanitizeMarkdownPreviewAttribute(element, attr) {
  const name = String(attr?.name || '').toLowerCase();
  if (!name) return;
  const tagName = String(element.tagName || '').toLowerCase();
  if (name.startsWith('on') || name === 'style' || name === 'srcdoc' || name === 'srcset' || name === 'formaction') {
    element.removeAttribute(attr.name);
    return;
  }
  if (name.includes(':') && name !== 'xlink:href') {
    element.removeAttribute(attr.name);
    return;
  }
  if (MARKDOWN_PREVIEW_URL_ATTRS.has(name) && !markdownPreviewUrlAllowed(attr.value, tagName)) {
    element.removeAttribute(attr.name);
    return;
  }
  if (name === 'target' && element.getAttribute('target') === '_blank') {
    element.setAttribute('rel', 'noopener noreferrer');
  }
}

function sanitizeMarkdownPreviewNode(root) {
  const elementNode = globalThis.Node?.ELEMENT_NODE || 1;
  const commentNode = globalThis.Node?.COMMENT_NODE || 8;
  for (const child of Array.from(root?.childNodes || [])) {
    if (child.nodeType === commentNode) {
      child.remove();
      continue;
    }
    if (child.nodeType !== elementNode) continue;
    const tagName = String(child.tagName || '').toLowerCase();
    if (MARKDOWN_PREVIEW_BLOCKED_TAGS.has(tagName)) {
      child.remove();
      continue;
    }
    for (const attr of Array.from(child.attributes || [])) {
      sanitizeMarkdownPreviewAttribute(child, attr);
    }
    sanitizeMarkdownPreviewNode(child);
  }
}

function sanitizeMarkdownPreviewHtml(html) {
  const template = document.createElement('template');
  if (!template.content) {
    const fallback = document.createElement('div');
    fallback.textContent = String(html ?? '');
    return fallback;
  }
  template.innerHTML = String(html ?? '');
  sanitizeMarkdownPreviewNode(template.content);
  return template.content;
}

function renderMarkdownPreviewInto(container, text, markdownPath) {
  if (typeof window.marked === 'undefined') {
    container.textContent = 'marked.js not loaded (offline CDN?)';
    return;
  }
  const html = window.marked.parse(markdownTextWithSourceAnchors(text), {gfm: true, breaks: true});
  container.replaceChildren(sanitizeMarkdownPreviewHtml(html));
  applyMarkdownSourceLines(container, text);
  // DOIT.6 #133: when this preview belongs to an on-disk file (file-editor preview, NOT a yoagent body),
  // remember the owning file's dir so relative links resolve, and bind the in-pane link handler once.
  if (markdownPath) {
    container.dataset.mdPath = markdownPath;
    container.dataset.basePath = dirnameOf(markdownPath);
    if (!container.dataset.mdLinkBound) {
      container.dataset.mdLinkBound = '1';
      container.addEventListener('click', handleMarkdownPreviewLinkClick);
    }
  }
  if (typeof window.hljs !== 'undefined') {
    container.querySelectorAll('pre code').forEach(block => {
      try { window.hljs.highlightElement(block); } catch (_) {}
    });
  }
}

// DOIT.6 #133: in the file-editor markdown preview, route link clicks: in-page #anchors keep default;
// external/other-scheme links open in a new tab (instead of blowing away the SPA); relative file links
// resolve against the owning file's dir and open in the editor (markdown -> rendered preview, else code).
// The server's read endpoint already rejects out-of-root paths, so a `../../etc/passwd` link just toasts.
function handleMarkdownPreviewLinkClick(event) {
  const a = event.target.closest?.('a');
  if (!a) return;
  const container = event.currentTarget;
  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#')) return;
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) {
    event.preventDefault();
    window.open(a.href, '_blank', 'noopener,noreferrer');
    return;
  }
  event.preventDefault();
  const clean = href.split('#')[0].split('?')[0];
  if (!clean) return;
  const basePath = container?.dataset?.basePath || '/';
  const resolved = joinAndNormalize(clean.startsWith('/') ? '/' : basePath, clean);
  const owner = openFileOwnerSessionsForPath(container?.dataset?.mdPath || '')[0] || undefined;
  Promise.resolve(openFileInEditor(resolved, basenameOf(resolved), {
    viewMode: isMarkdownPath(resolved) ? 'preview' : 'edit',
    ownerSession: owner,
  })).catch(() => showToast(t('preview.openFailed', {path: resolved}), '', {level: 'error'}));
}

function isMarkdownPath(path) {
  const lower = String(path || '').toLowerCase();
  return lower.endsWith('.md') || lower.endsWith('.markdown');
}

function isHtmlPath(path) {
  const lower = String(path || '').toLowerCase();
  return lower.endsWith('.html') || lower.endsWith('.htm');
}

function editorPreviewModeAvailable(path) {
  return isMarkdownPath(path) || isHtmlPath(path);
}

function editorVisualLineFragments(line, columnCount, wrapEnabled = fileEditorWrapEnabled) {
  const text = String(line ?? '');
  const width = Math.floor(Number(columnCount) || 0);
  if (!wrapEnabled || width <= 0 || text.length <= width) return [text];
  const fragments = [];
  for (let index = 0; index < text.length; index += width) {
    fragments.push(text.slice(index, index + width));
  }
  return fragments.length ? fragments : [''];
}

function simpleLineSyntaxHtml(language, line) {
  const highlighted = simpleCodeSyntaxHtml(language, line);
  return highlighted === null ? esc(line) : highlighted;
}

function editorVisualHighlightHtml(language, text, options = {}) {
  const source = String(text ?? '');
  const wrapEnabled = options.wrap === true;
  const lineNumbers = options.lineNumbers === true;
  const columnCount = options.columnCount || 88;
  const measuredRows = Array.isArray(options.visualRows) ? options.visualRows : null;
  const rows = source.split('\n');
  return rows.map((line, lineIndex) => {
    const fragments = measuredRows?.[lineIndex] || editorVisualLineFragments(line, columnCount, wrapEnabled);
    return fragments.map((fragment, fragmentIndex) => {
      const sourceLine = lineIndex + 1;
      const continuation = fragmentIndex > 0;
      const rowClass = continuation ? 'editor-visual-line continuation' : 'editor-visual-line';
      const lineNumber = lineNumbers && !continuation ? String(sourceLine) : '';
      const marker = wrapEnabled && continuation ? '↪' : '';
      const code = simpleLineSyntaxHtml(language, fragment);
      return `<span class="${rowClass}" data-source-line="${sourceLine}"><span class="editor-line-number">${esc(lineNumber)}</span><span class="editor-soft-wrap-marker">${esc(marker)}</span><span class="editor-line-code">${code}</span></span>`;
    }).join('');
  }).join('') || '<span class="editor-visual-line" data-source-line="1"><span class="editor-line-number">1</span><span class="editor-soft-wrap-marker"></span><span class="editor-line-code"></span></span>';
}

function renderEditorCodePreviewInto(container, path, text) {
  const language = syntaxLanguageForPath(path);
  const pre = document.createElement('pre');
  pre.className = ['file-editor-code-preview', 'editor-wrap', fileEditorLineNumbersEnabled ? 'editor-line-numbers' : ''].filter(Boolean).join(' ');
  const code = document.createElement('code');
  code.className = `language-${language || 'text'} editor-highlight-code`;
  code.innerHTML = editorVisualHighlightHtml(language, text, {
    wrap: true,
    lineNumbers: fileEditorLineNumbersEnabled,
    columnCount: 96,
  });
  pre.appendChild(code);
  container.replaceChildren(pre);
}

function htmlPreviewHasDisabledJavaScript(text) {
  const source = String(text ?? '');
  return /<script\b/i.test(source) || /\son[a-z]+\s*=/i.test(source);
}

function htmlPreviewUrl(path) {
  return `/api/fs/html-preview?path=${encodeURIComponent(path)}`;
}

async function openHtmlPreviewWithAuth(path) {
  const previewWindow = window.open('about:blank', '_blank');
  if (previewWindow) previewWindow.opener = null;
  try {
    const response = await apiFetch(htmlPreviewUrl(path));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const source = await response.text();
    const blobUrl = URL.createObjectURL(new Blob([source], {type: 'text/html'}));
    if (previewWindow) {
      previewWindow.location.href = blobUrl;
    } else {
      window.open(blobUrl, '_blank', 'noopener,noreferrer');
    }
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  } catch (error) {
    if (previewWindow) previewWindow.close();
    statusEl.innerHTML = `<span class="err">HTML preview failed: ${esc(error)}</span>`;
  }
}

function renderHtmlPreviewInto(container, path, text) {
  const children = [];
  if (htmlPreviewHasDisabledJavaScript(text)) {
    const notice = document.createElement('div');
    notice.className = 'file-editor-html-js-notice';
    const message = document.createElement('span');
    message.textContent = t('preview.jsDisabled');
    const link = document.createElement('a');
    link.href = htmlPreviewUrl(path);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.dataset.htmlPreviewAuth = '1';
    link.addEventListener('click', event => {
      event.preventDefault();
      openHtmlPreviewWithAuth(path);
    });
    link.textContent = t('preview.openWithJs');
    notice.append(message, link);
    children.push(notice);
  }
  const frame = document.createElement('iframe');
  frame.className = 'file-editor-html-preview';
  frame.setAttribute('sandbox', '');
  frame.setAttribute('title', 'HTML preview');
  frame.srcdoc = String(text ?? '');
  children.push(frame);
  container.replaceChildren(...children);
}

function renderEditorPreviewPane(container, path, text) {
  if (!container) return;
  const scrollTop = container.scrollTop || 0;
  const scrollLeft = container.scrollLeft || 0;
  container.classList.toggle('markdown-body', isMarkdownPath(path));
  container.classList.toggle('html-preview-body', isHtmlPath(path));
  container.classList.toggle('code-preview-body', !isMarkdownPath(path) && !isHtmlPath(path));
  if (isMarkdownPath(path)) renderMarkdownPreviewInto(container, text, path);
  else if (isHtmlPath(path)) renderHtmlPreviewInto(container, path, text);
  else renderEditorCodePreviewInto(container, path, text);
  restoreElementScrollPosition(container, scrollTop, scrollLeft);
}

function markdownInlineHighlightHtml(escaped) {
  return escaped
    .replace(/(`[^`]+`)/g, '<span class="md-code">$1</span>')
    .replace(/(\*\*[^*]+\*\*|__[^_]+__)/g, '<span class="md-bold">$1</span>')
    .replace(/(\[[^\]]+\]\([^)]+\))/g, '<span class="md-link">$1</span>')
    .replace(/(&lt;\/?[A-Za-z][^&]*?&gt;)/g, '<span class="md-html">$1</span>')
    .replace(/(^|[^\w*])(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g, '$1<span class="md-italic">$2</span>');
}

function markdownSyntaxHtml(text) {
  let inFence = false;
  return String(text || '').split('\n').map(line => {
    const escaped = esc(line);
    const fence = /^\s*(```|~~~)/.test(line);
    if (fence) {
      inFence = !inFence;
      return `<span class="md-fence">${escaped}</span>`;
    }
    if (inFence) return `<span class="md-codeblock">${escaped}</span>`;
    const heading = line.match(/^(\s{0,3})(#{1,6})(\s+.*)$/);
    if (heading) return `<span class="md-heading md-heading-${heading[2].length}">${escaped}</span>`;
    const quote = escaped.match(/^(\s*&gt;\s?)(.*)$/);
    if (quote) return `<span class="md-blockquote">${escaped}</span>`;
    const list = escaped.match(/^(\s*(?:[-*+]|\d+\.)\s+)(.*)$/);
    if (list) return `<span class="md-list-marker">${list[1]}</span>${markdownInlineHighlightHtml(list[2])}`;
    return markdownInlineHighlightHtml(escaped);
  }).join('\n');
}

function simpleTokenHighlightHtml(raw, rules) {
  const text = String(raw || '');
  let html = '';
  let index = 0;
  while (index < text.length) {
    let best = null;
    for (let ruleIndex = 0; ruleIndex < rules.length; ruleIndex += 1) {
      const rule = rules[ruleIndex];
      rule.regex.lastIndex = index;
      const match = rule.regex.exec(text);
      if (!match || !match[0]) continue;
      const candidate = {rule, ruleIndex, index: match.index, text: match[0]};
      if (!best
        || candidate.index < best.index
        || (candidate.index === best.index && candidate.ruleIndex < best.ruleIndex)) {
        best = candidate;
      }
    }
    if (!best) {
      html += esc(text.slice(index));
      break;
    }
    html += esc(text.slice(index, best.index));
    html += `<span class="${best.rule.className}">${esc(best.text)}</span>`;
    index = best.index + best.text.length;
  }
  return html;
}

function simpleCodeSyntaxHtml(language, text) {
  if (language === 'markdown') return markdownSyntaxHtml(text);
  const stringRule = {regex: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, className: 'code-string'};
  const numberRule = {regex: /\b\d+(?:\.\d+)?\b/g, className: 'code-number'};
  const shellRules = [
    stringRule,
    {regex: /#[^\n]*/g, className: 'code-comment'},
    {regex: /\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$[0-9@#?*!-]/g, className: 'code-variable'},
    {regex: /\b(?:if|then|else|elif|fi|for|in|do|done|while|case|esac|function|export|local|readonly|return|exit|set|unset|trap|source)\b/g, className: 'code-keyword'},
    {regex: /\b(?:awk|cat|cd|chmod|chown|cp|curl|docker|echo|find|git|grep|head|jq|ls|mkdir|mv|node|npm|python|python3|rg|rm|sed|ssh|tail|tar|tee|test|touch|xargs)\b/g, className: 'code-builtin'},
    numberRule,
  ];
  const pythonRules = [
    stringRule,
    {regex: /#[^\n]*/g, className: 'code-comment'},
    {regex: /@\w+/g, className: 'code-function'},
    {regex: /\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:False|None|True|self|cls)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const jsRules = [
    {regex: /`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, className: 'code-string'},
    {regex: /\/\/[^\n]*|\/\*.*?\*\//g, className: 'code-comment'},
    {regex: /\b(?:async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|finally|for|from|function|if|import|in|instanceof|let|new|of|return|switch|throw|try|typeof|var|void|while|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:false|null|true|undefined|this|super)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const rustRules = [
    stringRule,
    {regex: /\/\/[^\n]*|\/\*.*?\*\//g, className: 'code-comment'},
    {regex: /\b(?:as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while)\b/g, className: 'code-keyword'},
    {regex: /\b(?:bool|char|f32|f64|i8|i16|i32|i64|i128|isize|str|u8|u16|u32|u64|u128|usize|String|Vec|Option|Result)\b/g, className: 'code-type'},
    {regex: /\b[A-Za-z_][A-Za-z0-9_]*!/g, className: 'code-function'},
    numberRule,
  ];
  const xmlRules = [
    {regex: /<!--.*?-->/g, className: 'code-comment'},
    {regex: /<\/?[A-Za-z][^>]*?>/g, className: 'code-tag'},
    stringRule,
  ];
  const jsonRules = [
    {regex: /"(?:\\.|[^"\\])*"(?=\s*:)/g, className: 'code-attr'},
    stringRule,
    {regex: /\b(?:false|null|true)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const cssRules = [
    {regex: /\/\*.*?\*\//g, className: 'code-comment'},
    stringRule,
    {regex: /#[0-9A-Fa-f]{3,8}\b/g, className: 'code-number'},
    {regex: /[A-Za-z-]+(?=\s*:)/g, className: 'code-attr'},
    {regex: /\b(?:auto|block|flex|grid|hidden|inline|none|relative|absolute|fixed|solid|transparent|inherit|initial|unset)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const yamlRules = [
    {regex: /#[^\n]*/g, className: 'code-comment'},
    stringRule,
    {regex: /^[\s-]*[A-Za-z0-9_.-]+(?=\s*:)/gm, className: 'code-attr'},
    {regex: /\b(?:false|null|true|yes|no|on|off)\b/g, className: 'code-constant'},
    numberRule,
  ];
  const rulesByLanguage = new Map([
    ['bash', shellRules],
    ['css', cssRules],
    ['ini', yamlRules],
    ['javascript', jsRules],
    ['json', jsonRules],
    ['python', pythonRules],
    ['rust', rustRules],
    ['typescript', jsRules],
    ['xml', xmlRules],
    ['yaml', yamlRules],
  ]);
  const rules = rulesByLanguage.get(language);
  if (!rules) return null;
  return String(text || '').split('\n').map(line => simpleTokenHighlightHtml(line, rules)).join('\n');
}

function syntaxLanguageForPath(path) {
  const name = basenameOf(path).toLowerCase();
  const dot = name.lastIndexOf('.');
  const ext = dot === -1 ? '' : name.slice(dot);
  if (syntaxLanguageByExtension.has(ext)) return syntaxLanguageByExtension.get(ext);
  if (name === 'dockerfile') return 'dockerfile';
  if (name === 'makefile') return 'makefile';
  return '';
}

function previewSourceLineAnchors(previewPane) {
  return Array.from(previewPane?.querySelectorAll?.('[data-source-line]') || [])
    .map(element => ({element, line: Number(element.dataset.sourceLine)}))
    .filter(item => Number.isFinite(item.line) && item.line > 0);
}

function previewAnchorForSourceLine(previewPane, sourceLine) {
  const target = Math.max(1, Math.floor(Number(sourceLine) || 1));
  const anchors = previewSourceLineAnchors(previewPane);
  let best = null;
  for (const item of anchors) {
    if (item.line > target) break;
    best = item;
  }
  return best || anchors[0] || null;
}

function scrollPreviewToSourceLine(previewPane, sourceLine) {
  const anchor = previewAnchorForSourceLine(previewPane, sourceLine);
  if (!anchor) return false;
  previewPane.scrollTop = Math.max(0, anchor.element.offsetTop - 4);
  return true;
}

function previewSourceLineForScroll(previewPane) {
  const anchors = previewSourceLineAnchors(previewPane);
  if (!anchors.length) return null;
  const top = (previewPane?.scrollTop || 0) + 6;
  let best = anchors[0];
  for (const item of anchors) {
    if (item.element.offsetTop > top) break;
    best = item;
  }
  return best.line;
}

function nowMs() {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

function fileEditorScrollSyncBlocked(panel) {
  return Boolean(panel?._splitScrollSyncing || Number(panel?._splitScrollSuppressUntil || 0) > nowMs());
}

function setFileEditorScrollSyncGuard(...panels) {
  const until = nowMs() + fileEditorScrollSyncSuppressMs;
  for (const panel of panels) {
    if (!panel) continue;
    panel._splitScrollSyncing = true;
    panel._splitScrollSuppressUntil = Math.max(Number(panel._splitScrollSuppressUntil || 0), until);
    const release = () => {
      panel._splitScrollSyncing = false;
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(release, 0));
    else setTimeout(release, 0);
  }
}

function elementCanScroll(element) {
  return Boolean(element && Math.max(0, element.scrollHeight - element.clientHeight) > 1);
}

function fileEditorPanelItem(panel) {
  return panel?.dataset?.layoutItem || '';
}

function fileEditorPanelPath(panel) {
  return panel?.dataset?.filePath || '';
}

function fileEditorPanelMode(panel) {
  const path = fileEditorPanelPath(panel);
  return editorViewModeFor(path, fileEditorPanelItem(panel));
}

function fileEditorPanelScroller(panel) {
  if (panel?._cmView?.scrollDOM) return panel._cmView.scrollDOM;
  return null;
}

function fileEditorPanelPreviewPane(panel) {
  const previewPane = panel?.querySelector?.('.file-editor-preview-pane-panel');
  return previewPane && !previewPane.hidden ? previewPane : null;
}

function fileEditorSourceElement(panel, source) {
  return source === 'preview' ? fileEditorPanelPreviewPane(panel) : fileEditorPanelScroller(panel);
}

function fileEditorSourceCanDrive(panel, source) {
  return elementCanScroll(fileEditorSourceElement(panel, source));
}

function fileEditorSourceLineForScroll(panel, source) {
  if (source === 'preview') return previewSourceLineForScroll(fileEditorPanelPreviewPane(panel));
  if (panel?._cmView) {
    try {
      const block = panel._cmView.lineBlockAtHeight(panel._cmView.scrollDOM.scrollTop);
      return panel._cmView.state.doc.lineAt(block.from).number;
    } catch (_) {
      return null;
    }
  }
  return null;
}

function scrollFileEditorPanelToSourceLine(panel, source, line) {
  if (!panel || !line) return false;
  setFileEditorScrollSyncGuard(panel);
  if (source === 'preview') {
    const previewPane = fileEditorPanelPreviewPane(panel);
    return previewPane ? scrollPreviewToSourceLine(previewPane, line) : false;
  }
  if (panel._cmView) {
    try {
      const docLine = panel._cmView.state.doc.line(Math.min(line, panel._cmView.state.doc.lines));
      const scrollEffect = panel._cmView.constructor?.scrollIntoView?.(docLine.from, {y: 'start'});
      if (scrollEffect) panel._cmView.dispatch({effects: scrollEffect});
      else return false;
      return true;
    } catch (_) {
      return false;
    }
  }
  return false;
}

function fileEditorPanelsForPath(path) {
  return filePanelItemsForPath(path)
    .map(item => panelNodes.get(item))
    .filter(panel => panel && panel.isConnected !== false);
}

function renderLinkedFilePreviewPanels(sourcePanel, path, content) {
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel === sourcePanel) continue;
    const mode = fileEditorPanelMode(panel);
    if (mode !== 'preview' && mode !== 'split') continue;
    renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, content);
  }
}

function updateLinkedFilePreviewRings() {
  for (const panel of panelNodes.values()) panel.classList.remove('preview-linked');
  if (!focusedPanelItem || isFilePreviewItem(focusedPanelItem)) return;
  const path = isFileEditorItem(focusedPanelItem) ? fileItemPath(focusedPanelItem) : '';
  if (!path) return;
  const previewItem = filePreviewItemFor(path);
  if (!itemIsActivePaneTab(previewItem)) return;
  const previewPanel = panelNodes.get(previewItem);
  if (previewPanel) previewPanel.classList.add('preview-linked');
}

function syncCrossPaneFileEditorScroll(host, source) {
  const path = fileEditorPanelPath(host);
  if (!path || !fileEditorSourceCanDrive(host, source)) return;
  const line = fileEditorSourceLineForScroll(host, source);
  if (!line) return;
  const preferredTarget = source === 'preview' ? 'editor' : 'preview';
  const fallbackTarget = preferredTarget === 'editor' ? 'preview' : 'editor';
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel === host || fileEditorScrollSyncBlocked(panel)) continue;
    if (scrollFileEditorPanelToSourceLine(panel, preferredTarget, line)) continue;
    scrollFileEditorPanelToSourceLine(panel, fallbackTarget, line);
  }
}

function syncFileEditorInPaneSplitScroll(host, source) {
  const content = host.querySelector?.('.file-editor-content');
  if (!content?.classList?.contains('split-preview')) return false;
  const cmView = host._cmView || null;
  const editorScroller = cmView?.scrollDOM || null;
  const previewPane = host.querySelector?.('.file-editor-preview-pane-panel');
  if (!editorScroller || !previewPane || previewPane.hidden) return false;
  if (!cmView) return false;
  if (!fileEditorSourceCanDrive(host, source)) return false;
  const from = source === 'preview' ? previewPane : editorScroller;
  const to = source === 'preview' ? editorScroller : previewPane;
  const maxFromTop = Math.max(1, from.scrollHeight - from.clientHeight);
  const maxToTop = Math.max(0, to.scrollHeight - to.clientHeight);
  const maxFromLeft = Math.max(1, from.scrollWidth - from.clientWidth);
  const maxToLeft = Math.max(0, to.scrollWidth - to.clientWidth);
  setFileEditorScrollSyncGuard(host);
  try {
    if (source === 'preview') {
      const line = previewSourceLineForScroll(previewPane);
      if (line && cmView) {
        const docLine = cmView.state.doc.line(Math.min(line, cmView.state.doc.lines));
        const scrollEffect = cmView.constructor?.scrollIntoView?.(docLine.from, {y: 'start'});
        if (scrollEffect) cmView.dispatch({effects: scrollEffect});
        else to.scrollTop = Math.round((from.scrollTop / maxFromTop) * maxToTop);
      } else {
        to.scrollTop = Math.round((from.scrollTop / maxFromTop) * maxToTop);
      }
    } else if (cmView) {
      let line = null;
      try {
        const block = cmView.lineBlockAtHeight(cmView.scrollDOM.scrollTop);
        line = cmView.state.doc.lineAt(block.from).number;
      } catch (_) {}
      if (!line || !scrollPreviewToSourceLine(previewPane, line)) {
        to.scrollTop = Math.round((from.scrollTop / maxFromTop) * maxToTop);
      }
    }
    to.scrollLeft = Math.round((from.scrollLeft / maxFromLeft) * maxToLeft);
  } finally {}
  return true;
}

function syncFileEditorSplitScroll(host, source) {
  if (!host || fileEditorScrollSyncBlocked(host)) return;
  const canDrive = fileEditorSourceCanDrive(host, source);
  if (!canDrive) return;
  syncFileEditorInPaneSplitScroll(host, source);
  syncCrossPaneFileEditorScroll(host, source);
}

function refreshEditorPreviews() {
  for (const [item, panel] of panelNodes.entries()) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    if (state?.kind === 'text') {
      renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
    }
  }
}

window.addEventListener('load', refreshEditorPreviews);

async function saveFileEditor(path, panel, options = {}) {
  if (readOnlyMode) return false;
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  syncOpenFileContentFromPanels(path, panel);
  if (!options.force && (state.externalChanged || state.externalMissing)) {
    clearFileAutosaveTimer(path);
    return showFileSaveConflictDialog(path, panel);
  }
  if (!state.dirty && options.force !== true) return true;
  setFileEditorPanelStatus(panel, options.autosave ? 'auto-saving...' : 'saving...', '');
  try {
    const body = {
      path,
      content: state.content,
    };
    if (options.force !== true) body.expected_mtime = state.mtime;
    const response = await apiFetch('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      if (response.status === 409) {
        setFileEditorPanelStatus(panel, 'save conflict: file changed on disk', 'warn');
        return showFileSaveConflictDialog(path, panel, {message: payload.error || ''});
      }
      setFileEditorPanelStatus(panel, `save failed: ${payload.error || response.status}`, 'error');
      return false;
    }
    const payload = await response.json();
    state.mtime = filePayloadMtime(payload);
    state.size = payload.size;
    state.original = state.content;
    state.dirty = false;
    clearFileAutosaveTimer(path);
    clearOpenFileExternalState(state);
    if (payload.yolo_rules) {
      yoloRulesPayload = payload.yolo_rules;
      renderPreferencesPanels();
    }
    for (const openPanel of fileEditorPanelsForPath(path)) {
      updateFileEditorPanelChrome(openPanel, path);
      setFileEditorPanelStatus(openPanel, `${options.autosave ? 'auto-saved' : 'saved'} (${payload.size} bytes)`, 'ok');
    }
    renderSessionButtons();
    renderPaneTabStrips();
    return true;
  } catch (err) {
    setFileEditorPanelStatus(panel, `save failed: ${err}`, 'error');
    return false;
  }
}

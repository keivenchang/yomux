function sessionForFileRepo(path) {
  const normalized = String(path || '');
  if (!normalized) return '';
  const matches = sessions
    .map(session => {
      const root = normalizeDirectoryPath(sessionTranscriptInfo(session).gitRoot);
      const containsPath = root && (normalized === root || normalized.startsWith(`${root}/`));
      return containsPath ? {session, root} : null;
    })
    .filter(Boolean)
    .sort((left, right) => right.root.length - left.root.length);
  return matches[0]?.session || '';
}

function sessionFilesTargetSession(options = {}) {
  if (options.followActive) {
    const activeItem = currentActiveMenuItem();
    const activePath = isFileEditorItem(activeItem) || isImageViewerItem(activeItem)
      ? fileItemPath(activeItem)
      : '';
    const fileSession = sessionForFileRepo(activePath || '');
    if (fileSession) return fileSession;
  }
  const current = currentSessionActionTarget();
  if (current && sessions.includes(current)) return current;
  return sessions[0] || '';
}

// C6: the effective FROM/TO for one repo — its own override if set, else the global default. With no
// repo (legacy/zero-arg callers and files outside any tracked repo) this is the global default pair.
function repoDiffRefs(repo) {
  const entry = repo ? diffRefsByRepo[repo] : null;
  return {
    from: cleanDiffRef(entry?.from, diffRefFrom),
    to: cleanDiffRef(entry?.to, diffRefTo),
  };
}

// C6: which repo a given absolute file path belongs to, from the loaded Modified-files payloads, so a
// file's diff uses ITS repo's FROM/TO. Empty when the file isn't in a known changed repo (-> global default).
function fileRepoForPath(path) {
  const normalized = normalizeDirectoryPath(path);
  if (!path || !normalized) return '';
  const roots = [];
  const addRoot = root => {
    const repo = normalizeDirectoryPath(root || '');
    if (repo && repo !== '/' && pathIsInsideDirectory(normalized, repo)) roots.push(repo);
  };
  for (const file of fileExplorerSessionFilesPayload?.files || []) {
    if (file?.abs_path === path && file.repo) return file.repo;
  }
  for (const repoInfo of fileExplorerSessionFilesPayload?.repos || []) {
    addRoot(repoInfo?.repo);
  }
  addRoot(openFiles.get(path)?.gitRoot);
  addRoot(openFiles.get(path)?.diffRepo);
  for (const session of sessions) {
    addRoot(sessionTranscriptInfo(session).gitRoot);
  }
  addRoot(repoRoot);
  return roots.sort((left, right) => right.length - left.length)[0] || '';
}

function diffRefParams(repo) {
  const refs = repoDiffRefs(repo);
  return {
    from: cleanDiffRef(refs.from, 'HEAD'),
    to: cleanDiffRef(refs.to, 'current'),
  };
}

function diffRefQueryString(repo) {
  const refs = diffRefParams(repo);
  return `from=${encodeURIComponent(refs.from)}&to=${encodeURIComponent(refs.to)}`;
}

// C6: the per-repo override map encoded for /api/session-files (one request covers several repos). Only
// repos with a non-default selection are sent; an empty map yields '' so the request stays unchanged.
function sessionFilesRefsQuery() {
  const map = {};
  for (const [repo, refs] of Object.entries(diffRefsByRepo || {})) {
    const from = cleanDiffRef(refs?.from, '');
    const to = cleanDiffRef(refs?.to, '');
    if (!from && !to) continue;
    map[repo] = {from: from || 'HEAD', to: to || 'current'};
  }
  return Object.keys(map).length ? `&refs=${encodeURIComponent(JSON.stringify(map))}` : '';
}

function sessionFilesRequestQueryString() {
  if (fileExplorerMode !== 'diff') return 'from=HEAD&to=current';
  return `${diffRefQueryString()}${sessionFilesRefsQuery()}`;
}

function clientSessionFilesWatchRequests() {
  const params = new URLSearchParams(sessionFilesRequestQueryString());
  let repoRefs = null;
  const refs = params.get('refs');
  if (refs) {
    try {
      const parsed = JSON.parse(refs);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) repoRefs = parsed;
    } catch (_error) {}
  }
  return [{
    session: fileExplorerSessionFilesTargetSession(),
    hours: 24,
    from_ref: params.get('from') || 'HEAD',
    to_ref: params.get('to') || 'current',
    repo_refs: repoRefs,
  }];
}

function normalizedSessionFilesRepoRefs(repoRefs) {
  if (!repoRefs || typeof repoRefs !== 'object' || Array.isArray(repoRefs)) return {};
  const normalized = {};
  for (const repo of Object.keys(repoRefs).sort()) {
    const refs = repoRefs[repo];
    if (!refs || typeof refs !== 'object' || Array.isArray(refs)) continue;
    const from = cleanDiffRef(refs.from || refs.from_ref || '', '');
    const to = cleanDiffRef(refs.to || refs.to_ref || '', '');
    if (!from && !to) continue;
    normalized[repo] = {from: from || 'HEAD', to: to || 'current'};
  }
  return normalized;
}

function normalizedSessionFilesRequest(request = {}, sessionFallback = '') {
  const from = cleanDiffRef(request.from_ref || request.from || '', 'HEAD');
  const to = cleanDiffRef(request.to_ref || request.to || '', 'current');
  const hoursValue = Number(request.hours || 24);
  return {
    session: String(request.session || sessionFallback || ''),
    hours: Number.isFinite(hoursValue) ? hoursValue : 24,
    from_ref: from,
    to_ref: to,
    repo_refs: normalizedSessionFilesRepoRefs(request.repo_refs),
  };
}

function sessionFilesRequestKey(request = {}, sessionFallback = '') {
  const normalized = normalizedSessionFilesRequest(request, sessionFallback);
  return JSON.stringify(normalized);
}

function sessionFilesPushRequestMatchesCurrent(request = {}, session = '') {
  if (!request || typeof request !== 'object') return false;
  const current = clientSessionFilesWatchRequests()[0] || {};
  return sessionFilesRequestKey(request, session) === sessionFilesRequestKey(current, session);
}

function sessionFilesCacheKey(session) {
  return `${String(session || '')}\x1f${sessionFilesRequestQueryString()}`;
}

function sessionFilesPayloadHasDifferPath(payload, path) {
  const normalized = normalizeDirectoryPath(path);
  if (!normalized) return false;
  return (Array.isArray(payload?.files) ? payload.files : [])
    .some(file => sessionFileIsDifferVisible(file) && normalizeDirectoryPath(file?.abs_path || sessionFileAbsolutePath(file)) === normalized);
}

function changedFileOwnerSessionForPath(path, options = {}) {
  const owners = Array.isArray(options.owners) ? options.owners.filter(session => sessions.includes(session)) : [];
  const ownerSet = owners.length ? new Set(owners) : null;
  const matches = new Set();
  const considerPayload = payload => {
    const session = String(payload?.session || '');
    if (!session || !sessions.includes(session)) return;
    if (ownerSet && !ownerSet.has(session)) return;
    if (sessionFilesPayloadHasDifferPath(payload, path)) matches.add(session);
  };
  considerPayload(fileExplorerSessionFilesPayload);
  for (const cached of fileExplorerSessionFilesCache.values()) {
    considerPayload(cached?.payload || cached);
  }
  return matches.size === 1 ? Array.from(matches)[0] : '';
}

const diffRefSuggestionLimit = 60;
const diffRefPopoverCompactLimit = 12;
const diffRefPopoverFullLimit = 18;
let diffRefPopover = null;
let diffRefPopoverInput = null;
let diffRefPopoverItemsCurrent = [];
let diffRefPopoverActiveIndex = -1;
let diffRefPopoverListenersInstalled = false;

// C6: commit suggestions. With a `repo`, draw only from THAT repo's refs_by_repo so a picker never offers
// a SHA from a sibling repo. With no repo (legacy/global callers), flatten every repo's refs as before.
function diffRefSuggestions(repo) {
  const suggestions = [
    {ref: 'HEAD', short: 'HEAD', subject: 'base commit'},
    {ref: 'current', short: 'current', subject: 'working tree'},
  ];
  const seen = new Set(suggestions.map(item => item.ref));
  const addRefs = refs => {
    if (!Array.isArray(refs)) return;
    for (const item of refs) {
      const ref = cleanDiffRef(item?.ref || '', '');
      if (!ref || seen.has(ref)) continue;
      suggestions.push({ref, short: item?.short || ref.slice(0, 9), subject: item?.subject || '', date: item?.date || '', author: item?.author || ''});
      seen.add(ref);
      if (suggestions.length >= diffRefSuggestionLimit) return;
    }
  };
  const refsByRepo = fileExplorerSessionFilesPayload?.refs_by_repo && typeof fileExplorerSessionFilesPayload.refs_by_repo === 'object'
    ? fileExplorerSessionFilesPayload.refs_by_repo
    : {};
  if (repo) {
    addRefs(refsByRepo[repo]);
  } else {
    for (const refs of Object.values(refsByRepo)) addRefs(refs);
  }
  return suggestions;
}

function fileDiffRefHistoryItems(path) {
  const state = openFiles.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return [];
  const suggestions = [
    {ref: 'HEAD', short: 'HEAD', subject: 'base commit'},
    {ref: 'current', short: 'current', subject: 'working tree'},
  ];
  const seen = new Set(suggestions.map(item => item.ref));
  for (const item of state.gitHistory) {
    const ref = cleanDiffRef(item?.ref || '', '');
    if (!ref || seen.has(ref)) continue;
    suggestions.push({ref, short: item?.short || ref.slice(0, 9), subject: item?.subject || '', date: item?.date || '', author: item?.author || ''});
    seen.add(ref);
    if (suggestions.length >= diffRefSuggestionLimit) break;
  }
  return suggestions;
}

function scopedDiffRefSuggestions(repo, path) {
  return path ? fileDiffRefHistoryItems(path) : diffRefSuggestions(repo);
}

function fileDiffRefHistorySignature(path) {
  const state = openFiles.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return 'none';
  return state.gitHistory.map(item => `${item?.ref || ''}:${item?.date || ''}`).join('|');
}

function diffRefOptionLabel(item, separator = ' - ') {
  return [item?.short || '', item?.subject || ''].filter(Boolean).join(separator) || item?.ref || '';
}

function diffRefItemMetaText(item) {
  const ts = Number(item?.date || 0);
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  const dateStr = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  const author = String(item?.author || '').trim().split(/\s+/)[0] || '';
  return author ? `${dateStr} ${author}` : dateStr;
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
  const normalized = cleanDiffRef(value, '');
  return diffRefSameCommit(normalized, item?.ref)
    || diffRefSameCommit(normalized, item?.short)
    || normalized === diffRefOptionLabel(item)
    || normalized === diffRefOptionLabel(item, ' ');
}

function canonicalDiffRefValue(value, suggestions) {
  const ref = cleanDiffRef(value, '');
  if (!ref) return '';
  return suggestions.find(item => diffRefOptionMatches(ref, item))?.ref || ref;
}

function diffRefOptionItems(value, options = {}) {
  const rawLimit = Number(options.maxItems);
  const defaultLimit = options.compact ? diffRefPopoverCompactLimit : diffRefPopoverFullLimit;
  const maxItems = Math.max(1, Math.min(Number.isFinite(rawLimit) ? rawLimit : defaultLimit, diffRefSuggestionLimit));
  const suggestions = Array.isArray(options.suggestions) ? options.suggestions : diffRefSuggestions();
  const items = suggestions.slice(0, maxItems);
  if (value && !items.some(item => diffRefOptionMatches(value, item))) {
    items.unshift({ref: value, short: value.slice(0, 9), subject: 'selected ref'});
  }
  return items;
}

function diffRefSelectOptionsHtml(value, options = {}) {
  return diffRefOptionItems(value, options).map(item => {
    const label = diffRefOptionLabel(item);
    return `<option value="${esc(item.ref)}"${diffRefOptionMatches(value, item) ? ' selected' : ''}>${esc(label)}</option>`;
  }).join('');
}

function diffRefPopoverItems(value, options = {}) {
  const rawLimit = Number(options.maxItems);
  const defaultLimit = options.compact ? diffRefPopoverCompactLimit : diffRefPopoverFullLimit;
  const maxItems = Math.max(1, Math.min(Number.isFinite(rawLimit) ? rawLimit : defaultLimit, diffRefSuggestionLimit));
  const suggestions = Array.isArray(options.suggestions) ? options.suggestions : diffRefSuggestions();
  const query = cleanDiffRef(value, '').toLowerCase();
  const showAll = options.showAll === true || !query;
  const matches = showAll
    ? suggestions
    : suggestions.filter(item => {
      const label = diffRefOptionLabel(item).toLowerCase();
      const search = [item?.ref, item?.short, item?.subject, label].filter(Boolean).join(' ').toLowerCase();
      return diffRefOptionMatches(query, item) || search.includes(query);
    });
  return matches.slice(0, maxItems);
}

function diffRefFromSuggestions(repo, path = '') {
  return scopedDiffRefSuggestions(repo, path).filter(item => item.ref !== 'current');
}

function diffRefToSuggestions(fromRef = diffRefFrom, repo, path = '') {
  const suggestions = scopedDiffRefSuggestions(repo, path);
  const current = suggestions.find(item => item.ref === 'current') || {ref: 'current', short: 'current', subject: 'working tree'};
  const ordered = [current, ...suggestions.filter(item => item.ref !== 'current')];
  const from = cleanDiffRef(fromRef, '');
  const fromIndex = ordered.findIndex(item => diffRefOptionMatches(from, item));
  if (fromIndex < 0) return [current];
  return ordered.slice(0, Math.max(1, fromIndex));
}

function diffRefInputDisplayValue(value, suggestions) {
  const ref = cleanDiffRef(value, '');
  const match = (Array.isArray(suggestions) ? suggestions : []).find(item => diffRefOptionMatches(ref, item));
  return match?.short || ref;
}

function diffRefInputHtml(options = {}) {
  const repo = options.repo || '';
  const path = options.path || '';
  const side = options.side === 'to' ? 'to' : 'from';
  const compact = options.compact === true;
  const fallback = side === 'to' ? 'current' : 'HEAD';
  const value = cleanDiffRef(options.value, fallback);
  const suggestions = Array.isArray(options.suggestions) ? options.suggestions : (side === 'to' ? diffRefToSuggestions(diffRefFrom, repo, path) : diffRefFromSuggestions(repo, path));
  const dataAttr = side === 'to' ? 'data-diff-ref-to' : 'data-diff-ref-from';
  const aria = options.aria || (side === 'to' ? t('diff.ref.to.aria') : t('diff.ref.from.aria'));
  return `<input class="diff-ref-input" type="text" value="${esc(diffRefInputDisplayValue(value, suggestions))}" ${dataAttr} data-diff-ref-input autocomplete="off" autocapitalize="off" spellcheck="false" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(aria)}">`;
}

function diffRefInputContext(input) {
  const controls = input?.closest?.('[data-diff-ref-controls]');
  const repo = controls?.dataset?.diffRefRepo || '';
  const path = controls?.dataset?.diffRefPath || '';
  const compact = controls?.classList?.contains('compact') === true;
  const side = input?.matches?.('[data-diff-ref-to]') ? 'to' : 'from';
  const fromInput = controls?.querySelector?.('[data-diff-ref-from]');
  const fromValue = side === 'to'
    ? (canonicalDiffRefValue(fromInput?.value, diffRefFromSuggestions(repo, path)) || fromInput?.value || repoDiffRefs(repo).from)
    : repoDiffRefs(repo).from;
  const suggestions = side === 'to' ? diffRefToSuggestions(fromValue, repo, path) : diffRefFromSuggestions(repo, path);
  return {controls, repo, path, compact, side, suggestions};
}

function ensureDiffRefPopover() {
  if (diffRefPopover) return diffRefPopover;
  diffRefPopover = document.createElement('div');
  diffRefPopover.className = 'diff-ref-suggestion-popover';
  diffRefPopover.id = 'diff-ref-suggestion-popover';
  diffRefPopover.role = 'listbox';
  diffRefPopover.hidden = true;
  diffRefPopover.addEventListener('pointerdown', event => {
    event.preventDefault();
  });
  diffRefPopover.addEventListener('click', event => {
    const option = event.target.closest?.('[data-diff-ref-option-index]');
    if (!option || !diffRefPopover.contains(option)) return;
    event.preventDefault();
    chooseDiffRefPopoverOption(Number(option.dataset.diffRefOptionIndex));
  });
  document.body?.appendChild(diffRefPopover);
  installDiffRefPopoverListeners();
  return diffRefPopover;
}

function installDiffRefPopoverListeners() {
  if (diffRefPopoverListenersInstalled) return;
  document.addEventListener('pointerdown', event => {
    const target = event.target;
    if (target?.closest?.('[data-diff-ref-input]') || target?.closest?.('#diff-ref-suggestion-popover')) return;
    hideDiffRefPopover();
  });
  window.addEventListener('resize', () => hideDiffRefPopover());
  document.addEventListener('scroll', event => {
    const target = event.target;
    if (diffRefPopover && (target === diffRefPopover || diffRefPopover.contains(target))) return;
    if (!diffRefPopoverInput || !diffRefPopover || diffRefPopover.hidden) return;
    if (!diffRefPopoverInput.isConnected) {
      hideDiffRefPopover();
      return;
    }
    const context = diffRefInputContext(diffRefPopoverInput);
    positionDiffRefPopover(diffRefPopoverInput, context.compact);
  }, true);
  diffRefPopoverListenersInstalled = true;
}

function positionDiffRefPopover(input, compact) {
  const popover = ensureDiffRefPopover();
  const rect = input?.getBoundingClientRect?.();
  if (!rect) return;
  const viewportWidth = Math.max(320, window.innerWidth || document.documentElement?.clientWidth || 1024);
  const viewportHeight = Math.max(240, window.innerHeight || document.documentElement?.clientHeight || 720);
  const minWidth = Math.min(compact ? 880 : 960, viewportWidth - 16);
  const maxWidth = compact ? 1040 : 1120;
  const width = Math.min(maxWidth, viewportWidth - 16, Math.max(minWidth, rect.width));
  const left = Math.max(8, Math.min(rect.left, viewportWidth - width - 8));
  const top = Math.min(rect.bottom + 4, viewportHeight - 48);
  popover.style.width = `${Math.round(width)}px`;
  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function renderDiffRefPopover(input, options = {}) {
  if (!input?.matches?.('[data-diff-ref-input]') || !document.body) return;
  const popover = ensureDiffRefPopover();
  const context = diffRefInputContext(input);
  const items = diffRefPopoverItems(input.value, {
    compact: context.compact,
    suggestions: context.suggestions,
    showAll: options.showAll === true,
  });
  diffRefPopoverInput = input;
  diffRefPopoverItemsCurrent = items;
  diffRefPopoverActiveIndex = items.findIndex(item => diffRefOptionMatches(input.value, item));
  popover.classList.toggle('compact', context.compact);
  if (!items.length) {
    hideDiffRefPopover();
    return;
  }
  popover.innerHTML = items.map((item, index) => {
    const active = index === diffRefPopoverActiveIndex;
    const ref = item?.short || item?.ref || '';
    const subject = item?.subject || item?.ref || '';
    const label = diffRefOptionLabel(item);
    const metaText = diffRefItemMetaText(item);
    const metaHtml = metaText ? `<span class="diff-ref-suggestion-meta">${esc(metaText)}</span>` : '';
    return `<button type="button" class="diff-ref-suggestion-option${active ? ' active' : ''}" role="option" aria-selected="${active ? 'true' : 'false'}" data-diff-ref-option-index="${index}" data-diff-ref-value="${esc(item?.ref || '')}" title="${esc(label)}"><span class="diff-ref-suggestion-ref">${esc(ref)}</span><span class="diff-ref-suggestion-subject">${esc(subject)}</span>${metaHtml}</button>`;
  }).join('');
  positionDiffRefPopover(input, context.compact);
  popover.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-controls', popover.id);
}

function hideDiffRefPopover() {
  if (diffRefPopover) diffRefPopover.hidden = true;
  if (diffRefPopoverInput) {
    diffRefPopoverInput.setAttribute('aria-expanded', 'false');
    diffRefPopoverInput.removeAttribute('aria-controls');
  }
  diffRefPopoverInput = null;
  diffRefPopoverItemsCurrent = [];
  diffRefPopoverActiveIndex = -1;
}

function syncDiffRefPopoverActiveOption() {
  if (!diffRefPopover || diffRefPopover.hidden) return;
  diffRefPopover.querySelectorAll?.('[data-diff-ref-option-index]')?.forEach((button, index) => {
    const active = index === diffRefPopoverActiveIndex;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    if (active) button.scrollIntoView?.({block: 'nearest'});
  });
}

function chooseDiffRefPopoverOption(index) {
  const input = diffRefPopoverInput;
  const item = diffRefPopoverItemsCurrent[index];
  if (!input || !item) return false;
  const context = diffRefInputContext(input);
  input.value = diffRefInputDisplayValue(item.ref, context.suggestions);
  hideDiffRefPopover();
  commitDiffRefControls(context.controls || input.closest('[data-diff-ref-controls]'));
  input.focus?.();
  return true;
}

function handleDiffRefPopoverKeydown(event, input) {
  if (!input?.matches?.('[data-diff-ref-input]')) return false;
  const open = diffRefPopover && !diffRefPopover.hidden && diffRefPopoverInput === input;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (!open) renderDiffRefPopover(input, {showAll: true});
    if (!diffRefPopoverItemsCurrent.length) return true;
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    diffRefPopoverActiveIndex = diffRefPopoverActiveIndex < 0
      ? (delta > 0 ? 0 : diffRefPopoverItemsCurrent.length - 1)
      : (diffRefPopoverActiveIndex + delta + diffRefPopoverItemsCurrent.length) % diffRefPopoverItemsCurrent.length;
    syncDiffRefPopoverActiveOption();
    return true;
  }
  if (event.key === 'Enter' && open && diffRefPopoverActiveIndex >= 0) {
    event.preventDefault();
    chooseDiffRefPopoverOption(diffRefPopoverActiveIndex);
    return true;
  }
  return false;
}

function showDiffRefPicker(input, options = {}) {
  if (!input?.matches?.('[data-diff-ref-input]')) return;
  renderDiffRefPopover(input, {showAll: options.showAll !== false});
}

function refreshDiffRefToDatalist(controls) {
  if (!controls) return;
  if (diffRefPopoverInput && controls.contains(diffRefPopoverInput)) {
    renderDiffRefPopover(diffRefPopoverInput, {showAll: false});
  }
}

// C6: FROM/TO controls scoped to one repo (data-diff-ref-repo). Options/selection come from that repo's
// own commit graph and override. With no repo it renders the global default (legacy single-pair shape).
function diffRefControlsHtml(options = {}) {
  const compact = options.compact === true;
  const repo = options.repo || '';
  const path = options.path || '';
  const refs = repoDiffRefs(repo);
  const className = compact ? 'diff-ref-controls compact' : 'diff-ref-controls';
  const repoAttr = repo ? ` data-diff-ref-repo="${esc(repo)}"` : '';
  const pathAttr = path ? ` data-diff-ref-path="${esc(path)}"` : '';
  const fromInput = diffRefInputHtml({repo, path, compact, side: 'from', value: refs.from, suggestions: diffRefFromSuggestions(repo, path), aria: t('diff.ref.from.aria')});
  const toInput = diffRefInputHtml({repo, path, compact, side: 'to', value: refs.to, suggestions: diffRefToSuggestions(refs.from, repo, path), aria: t('diff.ref.to.aria')});
  return `<span class="${className}" data-diff-ref-controls${repoAttr}${pathAttr}>
    <label class="diff-ref-control">${esc(t('diff.ref.from'))} ${fromInput}</label>
    <label class="diff-ref-control">${esc(t('diff.ref.to'))} ${toInput}</label>
    ${diffRefResetButtonHtml(refs)}
  </span>`;
}

function diffRefResetButtonHtml(refs = repoDiffRefs('')) {
  const isDefault = refs.from === 'HEAD' && refs.to === 'current';
  const resetHidden = isDefault ? ' hidden' : '';
  const label = esc(t('diff.ref.reset'));
  return `<button type="button" class="diff-ref-reset" data-diff-ref-reset${resetHidden} title="${label}" aria-label="${label}">${esc(t('pref.reset.row'))}</button>`;
}

function invalidateSessionFilesCaches() {
  fileExplorerSessionFilesCache.clear();
}

// C6: set the FROM/TO for ONE repo (or the global default when repo is empty), then refresh. The diff-ref
// state for other repos is untouched, so picking a SHA for repo A never disturbs repo B.
function setRepoDiffRefs(repo, fromRef, toRef, options = {}) {
  const path = options.path || '';
  const nextFrom = canonicalDiffRefValue(cleanDiffRef(fromRef, 'HEAD'), diffRefFromSuggestions(repo, path)) || 'HEAD';
  const toSuggestions = diffRefToSuggestions(nextFrom, repo, path);
  let nextTo = canonicalDiffRefValue(cleanDiffRef(toRef, 'current'), toSuggestions) || 'current';
  if (!toSuggestions.some(item => diffRefOptionMatches(nextTo, item))) nextTo = 'current';
  const current = repoDiffRefs(repo);
  if (nextFrom === current.from && nextTo === current.to && options.force !== true) return false;
  if (repo) {
    diffRefsByRepo[repo] = {from: nextFrom, to: nextTo};
  } else {
    diffRefFrom = nextFrom;
    diffRefTo = nextTo;
  }
  writeStoredDiffRefs();
  invalidateSessionFilesCaches();
  for (const state of openFiles.values()) {
    if (!state || state.kind !== 'text') continue;
    state.diffLoaded = false;
    state.diffUnavailable = false;
    state.diffError = '';
    state.diffPinnedFromRef = '';
    state.diffPinnedToRef = '';
  }
  renderFileExplorerChangesPanels({force: true});
  fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
  for (const path of openFiles.keys()) renderOpenFilePath(path);
  return true;
}

function commitDiffRefControls(container) {
  const controls = container?.matches?.('[data-diff-ref-controls]') ? container : container?.querySelector?.('[data-diff-ref-controls]');
  const repo = controls?.dataset?.diffRefRepo || '';
  const path = controls?.dataset?.diffRefPath || '';
  const fromInput = container?.querySelector?.('[data-diff-ref-from]');
  const toInput = container?.querySelector?.('[data-diff-ref-to]');
  return setRepoDiffRefs(repo, fromInput?.value, toInput?.value, {path});
}

function syncDiffRefControlValues(container) {
  if (!container) return;
  const active = document.activeElement;
  const controls = container.matches?.('[data-diff-ref-controls]') ? container : container.querySelector?.('[data-diff-ref-controls]');
  const repo = controls?.dataset?.diffRefRepo || '';
  const path = controls?.dataset?.diffRefPath || '';
  const refs = repoDiffRefs(repo);
  const fromInput = container.querySelector?.('[data-diff-ref-from]');
  const toInput = container.querySelector?.('[data-diff-ref-to]');
  if (fromInput && fromInput !== active) fromInput.value = diffRefInputDisplayValue(refs.from, diffRefFromSuggestions(repo, path));
  if (toInput && toInput !== active) toInput.value = diffRefInputDisplayValue(refs.to, diffRefToSuggestions(refs.from, repo, path));
  refreshDiffRefToDatalist(controls);
  const resetBtn = controls?.querySelector?.('[data-diff-ref-reset]');
  if (resetBtn) resetBtn.hidden = refs.from === 'HEAD' && refs.to === 'current';
}

function fileExplorerSessionFilesTargetSession() {
  if (fileExplorerChangesSelectedSession && sessions.includes(fileExplorerChangesSelectedSession)) {
    return fileExplorerChangesSelectedSession;
  }
  if (fileExplorerExplicitSyncSession && sessions.includes(fileExplorerExplicitSyncSession)) {
    fileExplorerChangesSelectedSession = fileExplorerExplicitSyncSession;
    return fileExplorerExplicitSyncSession;
  }
  const payloadSession = String(fileExplorerSessionFilesPayload?.session || '');
  if (payloadSession && sessions.includes(payloadSession)) return payloadSession;
  return sessions[0] || '';
}

function emptySessionFilesPayload(session = '', loaded = true) {
  return {session, files: [], repos: [], refs_by_repo: {}, errors: [], from_ref: diffRefFrom, to_ref: diffRefTo, loaded};
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

function switchFileExplorerChangesSession(session) {
  if (!session || !document.querySelector('.file-explorer-changes-panel')) return;
  rememberFileExplorerExplicitSyncSession(session);
  fileExplorerChangesSelectedSession = session;
  const cached = fileExplorerSessionFilesCache.get(sessionFilesCacheKey(session));
  const cachedPayloadIsLoaded = sessionFilesPayloadIsLoadedForSession(cached?.payload, session);
  if (fileExplorerMode === 'diff' && cachedPayloadIsLoaded) {
    setSessionFilesPayloadForDestination('finder', cached.payload);
    fileExplorerSessionFilesPayloadSignature = cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload);
  } else if (fileExplorerMode !== 'diff' && sessionFilesPayloadIsFinderWorktree(fileExplorerSessionFilesPayload, session)) {
    fileExplorerSessionFilesPayloadSignature = sessionFilesPayloadSignatureForPayload(fileExplorerSessionFilesPayload);
  } else {
    const pendingPayload = emptySessionFilesPayload(session, false);
    setSessionFilesPayloadForDestination('finder', pendingPayload);
    fileExplorerSessionFilesPayloadSignature = sessionFilesPayloadSignatureForPayload(pendingPayload);
  }
  setSessionFilesLoadingForDestination('finder', !cachedPayloadIsLoaded);
  renderFileExplorerChangesPanels();
  fetchSessionFiles({destination: 'finder', session, silent: true, force: true, background: cachedPayloadIsLoaded});
}

function noteFileExplorerChangesSessionInteraction(session) {
  if (!isTmuxSession(session) || !sessions.includes(session)) return false;
  rememberFileExplorerExplicitSyncSession(session);
  if (fileExplorerChangesSelectedSession === session) return false;
  fileExplorerChangesSelectedSession = session;
  if (document.querySelector('.file-explorer-changes-panel')) {
    switchFileExplorerChangesSession(session);
  }
  return true;
}

function sessionFilesPayloadForDestination(destination) {
  return fileExplorerSessionFilesPayload;
}

function setSessionFilesPayloadForDestination(destination, payload) {
  fileExplorerSessionFilesPayload = payload;
  updateFileExplorerSessionHighlightRows();
  if (
    destination === 'finder'
    && fileExplorerRootMode === 'sync'
    && fileExplorerMode !== 'diff'
    && payload?.loaded === true
  ) {
    scheduleFileExplorerActiveTabSync(payload.session || null);
  }
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
  return fileExplorerSessionFilesPayloadSignature;
}

function setSessionFilesSignatureForDestination(destination, signature) {
  fileExplorerSessionFilesPayloadSignature = signature;
}

function sessionFilesLoadingForDestination(destination) {
  return fileExplorerSessionFilesLoading;
}

function setSessionFilesLoadingForDestination(destination, loading) {
  fileExplorerSessionFilesLoading = loading;
}

function sessionFilesRenderOptions(options = {}) {
  return options.force === true || options.silent !== true ? {force: true} : {};
}

function renderSessionFilesDestination(destination, options = {}) {
  renderFileExplorerChangesPanels(options);
}

async function fetchSessionFiles(options = {}) {
  const destination = 'finder';
  const forceRefresh = options.force === true;
  const backgroundRefresh = options.background === true;
  if (sessionFilesLoadingForDestination(destination) && !forceRefresh) return;
  const requestIsCurrent = fileExplorerSessionFilesGuard.begin();
  const session = options.session || fileExplorerSessionFilesTargetSession();
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
  if (!backgroundRefresh) setSessionFilesLoadingForDestination(destination, true);
  if (!options.silent) statusEl.textContent = 'loading changed files...';
  if (!options.silent) {
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
  }
  try {
    // C6: Differ follows selected refs; Finder file mode must stay tied to the current worktree so it
    // does not paint historical diff badges after the repo is clean.
    const params = new URLSearchParams(sessionFilesRequestQueryString());
    params.set('session', session);
    params.set('hours', '24');
    if (forceRefresh) params.set('force', '1');
    const response = await apiFetch(`/api/session-files?${params.toString()}`);
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
    fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {payload: nextPayload, signature});
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    if (!options.silent) statusOk(esc(tPlural('status.changedFilesLoaded', nextPayload.files.length)));
  } catch (err) {
    const nextPayload = {session, files: [], repos: [], refs_by_repo: {}, errors: [String(err)], from_ref: diffRefFrom, to_ref: diffRefTo, loaded: true};
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    if (!options.silent) statusErr(localizedHtml('status.changedFilesFailed', {error: err}));
  } finally {
    const current = requestIsCurrent();
    const wasLoading = current && sessionFilesLoadingForDestination(destination);
    if (current && !backgroundRefresh) setSessionFilesLoadingForDestination(destination, false);
    if (current && (shouldRender || wasLoading)) {
      renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
      if (destination === 'finder') updateFileTreeGitStatusRows();
      renderPaneTabStrips();
      renderSessionButtons();
    }
  }
}

function applySessionFilesPayloadFromPush(payload = {}, request = {}) {
  const destination = 'finder';
  const session = payload.session || request.session || fileExplorerSessionFilesTargetSession();
  if (!session || session !== fileExplorerSessionFilesTargetSession()) return false;
  if (!sessionFilesPushRequestMatchesCurrent(request, session)) return false;
  const nextPayload = {
    session,
    files: Array.isArray(payload.files) ? payload.files : [],
    repos: Array.isArray(payload.repos) ? payload.repos : [],
    refs_by_repo: payload.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {},
    errors: Array.isArray(payload.errors) ? payload.errors : [],
    from_ref: payload.from_ref || request.from_ref || diffRefFrom,
    to_ref: payload.to_ref || request.to_ref || diffRefTo,
    loaded: true,
  };
  const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
  const shouldRender = signature !== sessionFilesSignatureForDestination(destination);
  setSessionFilesPayloadForDestination(destination, nextPayload);
  setSessionFilesSignatureForDestination(destination, signature);
  fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {payload: nextPayload, signature});
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  if (shouldRender) {
    renderSessionFilesDestination(destination, {force: true});
    updateFileTreeGitStatusRows();
    renderPaneTabStrips();
    renderSessionButtons();
  }
  return true;
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

function compactRelativeFileTimeText(unit, countText) {
  const category = countText === '1' ? 'one' : 'other';
  return t(`relative.compact.${unit}.${category}`, {count: countText});
}

function sessionFileRelativeTimeText(mtime, nowSeconds = Date.now() / 1000) {
  const value = Number(mtime || 0);
  if (!value) return '';
  const now = Number(nowSeconds);
  if (!Number.isFinite(now)) return '';
  const age = now - value;
  if (age <= 0) return t('relative.compact.now');
  if (age < 60) return t('relative.compact.lessThanMinute');
  if (age < 3600) {
    return compactRelativeFileTimeText('minute', String(Math.max(1, Math.round(age / 60))));
  }
  if (age < 86400) {
    const hoursText = compactAgeNumber(age / 3600);
    return compactRelativeFileTimeText('hour', hoursText);
  }
  const daysText = compactAgeNumber(age / 86400);
  return compactRelativeFileTimeText('day', daysText);
}

function sessionFileDisplayTimeText(mtime) {
  if (fileExplorerTreeDateMode === 'date') return sessionFileTimeText(mtime);
  if (fileExplorerTreeDateMode === 'relative') return sessionFileRelativeTimeText(mtime);
  return '';
}

function sessionFileDiffText(item) {
  const addKind = item?.diff_tracked === false ? 'add-neutral' : 'add';
  return [
    Number.isFinite(Number(item?.added)) && Number(item.added) !== 0 ? {kind: addKind, text: `+${Number(item.added)}`} : null,
    Number.isFinite(Number(item?.removed)) && Number(item.removed) !== 0 ? {kind: 'remove', text: `-${Number(item.removed)}`} : null,
  ].filter(Boolean);
}

function sortedSessionFiles(files) {
  const items = Array.isArray(files) ? files.slice() : [];
  const uploadOrder = item => item?.uploaded === true ? 1 : 0;
  const mode = normalizeSessionFilesSortMode(sessionFilesSortMode);
  const nameCompare = (left, right) => String(left.path || '').localeCompare(String(right.path || ''), undefined, {numeric: true, sensitivity: 'base'})
    || String(left.repo || '').localeCompare(String(right.repo || ''), undefined, {numeric: true, sensitivity: 'base'});
  if (mode === 'az' || mode === 'za') {
    return items.sort((left, right) => uploadOrder(left) - uploadOrder(right)
      || nameCompare(left, right) * (mode === 'za' ? -1 : 1));
  }
  return items.sort((left, right) => {
    const uploadResult = uploadOrder(left) - uploadOrder(right);
    if (uploadResult !== 0) return uploadResult;
    const leftMtime = Number(left.mtime || 0);
    const rightMtime = Number(right.mtime || 0);
    const mtimeResult = mode === 'oldest' ? leftMtime - rightMtime : rightMtime - leftMtime;
    return mtimeResult || nameCompare(left, right);
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

function writeStoredChangesFolderCollapsed() {
  storageSet(changesFolderCollapsedStorageKey, JSON.stringify(Array.from(changesFolderCollapsed).sort()));
}

function writeStoredChangesRepoCollapsed() {
  storageSet(changesRepoCollapsedStorageKey, JSON.stringify(Array.from(changesRepoCollapsed).sort()));
}

function fileExplorerChangesRepoKeys(payload = fileExplorerSessionFilesPayload) {
  const repos = new Set();
  const visibleFiles = fileExplorerDifferFiles(payload);
  for (const item of visibleFiles) {
    if (item?.repo) repos.add(item.repo);
  }
  if (!repos.size && visibleFiles.length && payload?.session) repos.add(payload.session);
  return Array.from(repos).sort();
}

function fileExplorerChangesFolderKeys(payload = fileExplorerSessionFilesPayload) {
  const folders = new Set();
  for (const item of fileExplorerDifferFiles(payload)) {
    const repoRoot = item?.repo && item.repo !== 'Outside repo' ? normalizeDirectoryPath(item.repo) : '/';
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

function fileExplorerChangesAllReposCollapsed(payload = fileExplorerSessionFilesPayload) {
  const repos = fileExplorerChangesRepoKeys(payload);
  return Boolean(repos.length) && repos.every(repo => changesRepoCollapsed.has(repo));
}

function fileExplorerChangesCollapseToggleTitle() {
  return fileExplorerChangesAllReposCollapsed() ? t('changes.expandAll') : t('changes.collapseAll');
}

function fileExplorerChangesCollapseToggleIcon() {
  return fileExplorerChangesAllReposCollapsed() ? '▾' : '▴';
}

function fileExplorerChangesCollapseToggleHtml() {
  const collapsed = fileExplorerChangesAllReposCollapsed();
  const title = fileExplorerChangesCollapseToggleTitle();
  return `<button type="button" class="file-explorer-header-action file-explorer-changes-collapse-toggle file-explorer-mode-diff-only" data-session-files-collapse-toggle title="${esc(title)}" aria-label="${esc(title)}" aria-pressed="${collapsed ? 'true' : 'false'}">${esc(fileExplorerChangesCollapseToggleIcon())}</button>`;
}

function syncFileExplorerChangesCollapseButtons() {
  const collapsed = fileExplorerChangesAllReposCollapsed();
  const title = fileExplorerChangesCollapseToggleTitle();
  for (const button of document.querySelectorAll('[data-session-files-collapse-toggle]')) {
    button.title = title;
    button.setAttribute('aria-label', title);
    button.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
    button.textContent = fileExplorerChangesCollapseToggleIcon();
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

function fileExplorerDifferFiles(payload = fileExplorerSessionFilesPayload) {
  return (Array.isArray(payload?.files) ? payload.files : []).filter(sessionFileIsDifferVisible);
}

function changeFileTotals(files) {
  let added = 0;
  let removed = 0;
  for (const item of Array.isArray(files) ? files : []) {
    if (item?.diff_tracked !== true) continue;
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
  const fallback = changeFileTotals(entries);
  return {
    added: repoAggregateNumber(repoInfo?.added, fallback.added),
    removed: repoAggregateNumber(repoInfo?.removed, fallback.removed),
    count: repoAggregateNumber(repoInfo?.count, Array.isArray(entries) ? entries.length : 0),
  };
}

function changesRepoTotalsHtml(repoInfo, entries) {
  const totals = changesRepoTotals(repoInfo, entries);
  const title = `+${totals.added} -${totals.removed} ${tPlural('changes.fileCount', totals.count)}`;
  return `<span class="changes-repo-totals" title="${esc(title)}"><span class="changes-diff-add">+${totals.added}</span><span class="changes-diff-remove">-${totals.removed}</span><span class="changes-repo-count">${totals.count}</span></span>`;
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
  return `<span class="changes-repo-compare-title diff-ref-controls compact diff-ref-inline" data-diff-ref-controls data-diff-ref-repo="${esc(repo)}">${body}${diffRefResetButtonHtml(refs)}</span>`;
}

// C6: per-repo comparison title (from the repo payload's own effective refs), shown beside that repo's
// FROM/TO controls. Surfaces any per-repo ref fallback so the user sees why a repo defaulted.
function repoComparisonTitleHtml(repoInfo) {
  const from = diffRefDisplayText(repoInfo?.from_ref || diffRefFrom);
  const to = diffRefDisplayText(repoInfo?.to_ref || diffRefTo);
  const title = `<span class="changes-repo-compare-title">${t('diff.comparing', {from: esc(from), to: esc(to)})}</span>`;
  const error = repoInfo?.error ? `<span class="changes-repo-refs-error">${esc(repoInfo.error)}</span>` : '';
  return `${title}${error}`;
}

function repoComparisonErrorHtml(repoInfo) {
  return repoInfo?.error ? `<span class="changes-repo-refs-error">${esc(repoInfo.error)}</span>` : '';
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
      if (path && repoHasExplicitComparison(repoInfo)) repos.add(path);
    }
  }
  return repos.size;
}

function changesSummaryHtml(payload, files, session, loading, loaded) {
  if (loading) return changesLoadingHtml(session);
  if (!loaded) return t('changes.notLoaded');
  const fileCount = Array.isArray(files) ? files.length : 0;
  const repoCount = changesRepoCount(payload, files);
  const repos = tPlural('changes.repoCount', repoCount);
  const count = tPlural('changes.fileCount', fileCount);
  const scope = session ? t('changes.inSession', {session: sessionLabel(session)}) : '';
  return `<span class="changes-summary-label">${esc(repos)}, ${esc(count)}${esc(scope)}</span>`;
}

function changesLoadingHtml(session = '') {
  const base = t('changes.loading');
  const label = session ? sessionLabel(session) : '';
  const loadingText = label ? `${stripTrailingEllipsisText(base)} ${label}` : base;
  return `<span class="changes-loading" aria-live="polite" aria-busy="true">
    <span class="session-yolo-marker active working changes-loading-yolo" style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}" aria-hidden="true">${esc(t('brand.marker'))}</span>
    <span>${textWithMovingEllipsisHtml(loadingText, 'changes-loading-dots')}</span>
  </span>`;
}

function changesRepoMetaHtml(repoInfo, options = {}) {
  // C15 (compact header): when hideZero is set (the embedded popover), omit 0-commit ahead/behind so a
  // clean repo prints nothing instead of the redundant "Behind 0 / Ahead 0".
  const hideZero = options.hideZero === true;
  const pieces = [];
  const behind = Number(repoInfo?.behind);
  const ahead = Number(repoInfo?.ahead);
  if (Number.isFinite(behind) && (!hideZero || behind > 0)) pieces.push(`<span>${esc(tPlural('changes.behind', behind))}</span>`);
  if (Number.isFinite(ahead) && (!hideZero || ahead > 0)) pieces.push(`<span>${esc(tPlural('changes.ahead', ahead))}</span>`);
  return pieces.length ? `<span class="changes-repo-compare-meta">${pieces.join('')}</span>` : '';
}

function repoPayloadByPath(payload) {
  const map = new Map();
  for (const repo of Array.isArray(payload?.repos) ? payload.repos : []) map.set(repo.repo || 'Outside repo', repo);
  return map;
}

function repoHasExplicitComparison(repoInfo) {
  const from = cleanDiffRef(repoInfo?.from_ref, 'default');
  const to = cleanDiffRef(repoInfo?.to_ref, 'base');
  return from !== 'default' || to !== 'base';
}

function payloadHasExplicitRepoSections(payload) {
  return Array.isArray(payload?.repos) && payload.repos.some(repoHasExplicitComparison);
}

function changesComparisonHeaderHtml(payload, files, options = {}) {
  const loaded = payload?.loaded === true;
  const loading = options.loading === true;
  if (options.compact) {
    if (loading) return `<section class="changes-comparison-head compact">${changesLoadingHtml(payload?.session || '')}</section>`;
    if (!loaded) return `<section class="changes-comparison-head compact">${esc(t('changes.notLoaded'))}</section>`;
    return '';
  }
  const summary = changesSummaryHtml(payload, files, payload?.session || '', loading, loaded);
  return `<section class="changes-comparison-head">
    <div class="changes-comparison-summary">${summary}</div>
  </section>`;
}

// Build a synthetic file-tree entry structure from flat session file items so renderTreeChildren
// can render the Differ using the same DOM-mutation logic as the Finder.
function updateSyntheticTreeEntryMtime(entry, mtime) {
  const value = Number(mtime || 0);
  if (!Number.isFinite(value) || value <= 0) return;
  const current = Number(entry.mtime || 0);
  if (!Number.isFinite(current) || value > current) entry.mtime = value;
}

function buildSessionFileTree(repoPath, sessionFiles) {
  const entriesByDir = new Map(); // normalizedDirPath → [{name, kind, mtime?, size?}]
  const sessionFilesMap = new Map(); // absPath → sessionFileItem
  for (const item of sessionFiles) {
    const absPath = item.abs_path || (repoPath && item.path ? `${repoPath}/${item.path}` : item.path || '');
    if (!absPath) continue;
    sessionFilesMap.set(absPath, item);
    const relPath = item.path || (absPath.startsWith(repoPath + '/') ? absPath.slice(repoPath.length + 1) : basenameOf(absPath));
    const parts = relPath.split('/').filter(Boolean);
    if (!parts.length) continue;
    // Ensure all ancestor directory entries exist in the map
    for (let i = 1; i < parts.length; i++) {
      const parentAbsPath = i === 1 ? repoPath : `${repoPath}/${parts.slice(0, i - 1).join('/')}`;
      const key = normalizeDirectoryPath(parentAbsPath);
      if (!entriesByDir.has(key)) entriesByDir.set(key, []);
      const siblings = entriesByDir.get(key);
      let dirEntry = siblings.find(e => e.kind === 'dir' && e.name === parts[i - 1]);
      if (!dirEntry) {
        dirEntry = {name: parts[i - 1], kind: 'dir'};
        siblings.push(dirEntry);
      }
      updateSyntheticTreeEntryMtime(dirEntry, item.mtime);
    }
    // File leaf
    const fileName = parts[parts.length - 1];
    const fileParentAbsPath = parts.length === 1 ? repoPath : `${repoPath}/${parts.slice(0, -1).join('/')}`;
    const key = normalizeDirectoryPath(fileParentAbsPath);
    if (!entriesByDir.has(key)) entriesByDir.set(key, []);
    const siblings = entriesByDir.get(key);
    if (!siblings.some(e => e.name === fileName)) {
      siblings.push({name: fileName, kind: 'file', mtime: item.mtime, size: item.size});
    }
  }
  const topLevel = entriesByDir.get(normalizeDirectoryPath(repoPath)) || [];
  return {entries: topLevel, entriesByDir, sessionFilesMap};
}

function initializeDefaultCollapsedChangesFolders(entriesByDir) {
  for (const [directory, entries] of entriesByDir.entries()) {
    for (const entry of entries || []) {
      if (entry?.kind !== 'dir' || entry.name !== '.uploads') continue;
      const fullPath = normalizeDirectoryPath(directory === '/' ? `/${entry.name}` : `${directory}/${entry.name}`);
      if (!fullPath || changesFolderAutoCollapsed.has(fullPath)) continue;
      changesFolderAutoCollapsed.add(fullPath);
      changesFolderCollapsed.add(fullPath);
    }
  }
}

// Render changed files for one repo section using the shared file-tree renderer.
function renderChangedFileList(container, repoPath, sessionFiles, options = {}) {
  const treeRoot = repoPath === 'Outside repo' ? '/' : repoPath;
  const {entries, entriesByDir, sessionFilesMap} = buildSessionFileTree(treeRoot, sessionFiles);
  initializeDefaultCollapsedChangesFolders(entriesByDir);
  renderTreeChildren(container, treeRoot, entries, 0, {
    entriesByDir,
    sessionFilesMap,
    differMode: true,
    compact: options.compact,
    repoForDiffer: treeRoot,
    treeSortMode: normalizeSessionFilesSortMode(sessionFilesSortMode),
    includeHidden: true,
  });
}

// C5: a changed file can be touched by 0, 1, or several agents. Render an icon per agent from item.agents
// (Claude, then Codex, then any others alphabetically), falling back to the legacy scalar item.agent. When
// more than one agent appears, label the slot so screen readers announce all of them.
function changedFileAgentTitle(kind, item) {
  const name = agentLabel(kind);
  if (!name) return '';
  const timeText = sessionFileRelativeTimeText(item?.mtime);
  return timeText ? `modified by ${name} ${timeText}` : `modified by ${name}`;
}

function changeFileAgentsHtml(item) {
  const ordered = sessionFileAgentKinds(item);
  const icons = ordered
    .map(kind => agentIcon(kind, {label: changedFileAgentTitle(kind, item)}))
    .filter(Boolean);
  if (!icons.length) return '';
  const label = ordered.map(kind => changedFileAgentTitle(kind, item)).filter(Boolean).join(', ');
  const labelAttr = icons.length > 1 && label ? ` aria-label="${esc(label)}"` : '';
  return `<span class="changes-file-agent"${labelAttr}>${icons.join('')}</span>`;
}

// DOM-mutation renderer for all repo sections in a Changes/Differ panel scroll area.
// Replaces changesRepoGroupsHtml — incrementally updates sections rather than replacing innerHTML.
function renderChangesGroups(groupsEl, files, options = {}) {
  if (!groupsEl) return;
  const regular = sortedSessionFiles(files);
  const payload = options.payload || {};
  const repoMap = repoPayloadByPath(payload);
  const groups = new Map(groupedSessionFiles(regular));
  if (options.includeEmptyRepoSections === true) {
    for (const repoInfo of Array.isArray(payload?.repos) ? payload.repos : []) {
      const repo = repoInfo?.repo || 'Outside repo';
      if (repo && repo !== 'Outside repo' && !groups.has(repo) && repoHasExplicitComparison(repoInfo)) {
        groups.set(repo, []);
      }
    }
  }
  const compact = options.compact === true;
  // Index existing sections by repo key so we can reuse DOM nodes
  const existing = new Map();
  for (const child of groupsEl.children) {
    const key = child.dataset.changesRepo;
    if (key) existing.set(key, child);
  }
  const nextNodes = [];
  for (const [repo, repoFiles] of groups.entries()) {
    let section = existing.get(repo);
    if (!section) {
      section = document.createElement('section');
      section.className = 'changes-repo-group';
      section.dataset.changesRepo = repo;
    }
    const collapsed = changesRepoCollapsed.has(repo);
    section.classList.toggle('collapsed', collapsed);
    const repoInfo = repoMap.get(repo) || {};
    const repoLabel = repo === 'Outside repo' ? repo : compactHomePath(repo);
    const hasGit = repo && repo !== 'Outside repo';
    // Update repo header button (small HTML string — not performance sensitive)
    let head = section.querySelector(':scope > .changes-repo-head');
    if (!head) {
      head = document.createElement('button');
      head.type = 'button';
      head.className = 'changes-repo-head';
      section.prepend(head);
    }
    head.dataset.changesRepoToggle = repo;
    head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    head.innerHTML = `<span class="changes-repo-caret">${collapsed ? '▸' : '▾'}</span><span class="changes-repo-title">${esc(repoLabel)}</span>${changesRepoTotalsHtml(repoInfo, repoFiles)}`;
    // Update "Comparing FROM TO" refs row (per-repo, only for git repos)
    let refsRow = section.querySelector(':scope > .changes-repo-refs');
    if (hasGit && !collapsed) {
      if (!refsRow) {
        refsRow = document.createElement('div');
        refsRow.className = compact ? 'changes-repo-refs compact' : 'changes-repo-refs';
        head.after(refsRow);
      }
      refsRow.innerHTML = `${diffRefComparisonLineHtml(repo)}${repoComparisonErrorHtml(repoInfo)}${changesRepoMetaHtml(repoInfo, {hideZero: compact})}`;
      refsRow.hidden = false;
    } else if (refsRow) {
      refsRow.hidden = true;
    }
    // Update file list using the unified tree renderer
    let fileList = section.querySelector(':scope > .changes-file-list');
    if (!collapsed) {
      if (!fileList) {
        fileList = document.createElement('div');
        fileList.className = 'changes-file-list';
        section.append(fileList);
      }
      fileList.hidden = false;
      if (repoFiles.length) {
        renderChangedFileList(fileList, repo, repoFiles, {compact});
      } else {
        fileList.innerHTML = `<div class="changes-empty">${esc(t('changes.emptyModified'))}</div>`;
      }
    } else if (fileList) {
      fileList.hidden = true;
    }
    nextNodes.push(section);
  }
  reconcileChildNodes(groupsEl, nextNodes);
}

// Returns the static toolbar/header HTML for the main Changes panel.
// The .changes-groups div is filled by renderChangesGroups separately.
function fileExplorerTreeDateButtonHtml(extraClass = '') {
  const mode = normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode);
  const active = mode !== 'none';
  const classes = ['file-explorer-header-action', 'file-explorer-date-toggle', extraClass, active ? 'active' : ''].filter(Boolean).join(' ');
  return `<button type="button" class="${esc(classes)}" data-file-explorer-tree-dates data-date-mode="${esc(mode)}" title="${esc(fileExplorerTreeDateModeTitle(mode))}" aria-label="${esc(fileExplorerTreeDateModeTitle(mode))}" aria-pressed="${active ? 'true' : 'false'}">${esc(fileExplorerTreeDateModeButtonLabel(mode))}</button>`;
}

function sessionFilesSortSelectHtml(extraClass = '') {
  const classes = ['file-explorer-sort-select', 'changes-sort-select', extraClass].filter(Boolean).join(' ');
  const mode = normalizeSessionFilesSortMode(sessionFilesSortMode);
  return `<select class="${esc(classes)}" data-session-files-sort title="${esc(t('changes.sort'))}" aria-label="${esc(t('changes.sort'))}">
        <option value="az"${mode === 'az' ? ' selected' : ''}>${esc(t('finder.sort.az'))}</option>
        <option value="za"${mode === 'za' ? ' selected' : ''}>${esc(t('finder.sort.za'))}</option>
        <option value="newest"${mode === 'newest' ? ' selected' : ''}>${esc(t('finder.sort.newest'))}</option>
        <option value="oldest"${mode === 'oldest' ? ' selected' : ''}>${esc(t('finder.sort.oldest'))}</option>
      </select>`;
}

function sessionFilesSessionSelectHtml(target, options = {}) {
  const classes = options.className ? ` class="${esc(options.className)}"` : '';
  const selectOptions = sessions.map(session => {
    const label = sessionLabel(session) || session;
    const suffix = label === session ? '' : ` ${session}`;
    return `<option value="${esc(session)}"${session === target ? ' selected' : ''}>${esc(`${label}${suffix}`)}</option>`;
  }).join('');
  return `<select${classes} data-session-files-session>${selectOptions}</select>`;
}

function fileExplorerDiffSessionControlHtml(session) {
  return `<label class="file-explorer-diff-session-control file-explorer-mode-diff-only changes-control">${esc(t('changes.session'))}: ${sessionFilesSessionSelectHtml(session, {className: 'file-explorer-diff-session-select'})}</label>`;
}

function syncFileExplorerDiffSessionControls() {
  const session = fileExplorerSessionFilesTargetSession();
  for (const select of document.querySelectorAll('.file-explorer-diff-session-control [data-session-files-session]')) {
    const options = Array.from(select.options || []);
    if (options.length !== sessions.length || !options.some(option => option.value === session)) {
      select.outerHTML = sessionFilesSessionSelectHtml(session, {className: 'file-explorer-diff-session-select'});
    } else if (select.value !== session) {
      select.value = session;
    }
  }
}

// Returns the static toolbar/header HTML for the embedded Finder Differ panel.
function fileExplorerChangesPanelStaticHtml(options = {}) {
  if (fileExplorerMode === 'tabber') {
    // Minimal Tabber chrome; the tree itself mounts into .changes-groups via renderTabberTree.
    return `
    <div class="file-explorer-changes-head">
      <span class="changes-title">Tabber</span>
    </div>
    <div class="changes-groups"></div>`;
  }
  const payload = fileExplorerSessionFilesPayload;
  const loading = fileExplorerSessionFilesLoading;
  const files = fileExplorerDifferFiles(payload);
  const loaded = payload.loaded === true;
  const session = payload.session || fileExplorerSessionFilesTargetSession();
  const errorHtml = (payload.errors || []).map(error => `<div class="changes-error">${esc(error)}</div>`).join('');
  const full = options.full === true || fileExplorerMode === 'diff';
  const showEmptyRepoSections = full && !files.length && payloadHasExplicitRepoSections(payload);
  const empty = !loading && loaded && !files.length && !showEmptyRepoSections ? `<div class="changes-empty">${esc(t('changes.emptyModified'))}</div>` : '';
  if (full) {
    return `
      <div class="changes-toolbar file-explorer-diff-toolbar">
        <label class="changes-control">${esc(t('changes.sort'))} ${sessionFilesSortSelectHtml()}</label>
        ${fileExplorerTreeDateButtonHtml('changes-date-toggle')}
        ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
        <button type="button" class="changes-refresh" data-session-files-refresh title="${esc(t('changes.refresh.title'))}" aria-label="${esc(t('changes.refresh.title'))}">${esc(t('changes.refresh'))}</button>
      </div>
      ${changesComparisonHeaderHtml(payload, files, {loading})}
      ${errorHtml}
      ${empty ? empty : '<div class="changes-groups"></div>'}`;
  }
  const titleText = session ? t('changes.titleForSession', {session: sessionLabel(session) || session}) : t('changes.title');
  return `
    <div class="file-explorer-changes-head">
      <span class="changes-title">${esc(titleText)}</span>
      ${sessionFilesSortSelectHtml('changes-sort-select-compact')}
      ${fileExplorerTreeDateButtonHtml('changes-date-toggle')}
      ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
      <button type="button" class="changes-refresh" data-session-files-refresh title="${esc(t('changes.refresh.title'))}" aria-label="${esc(t('changes.refresh.title'))}">${esc(t('changes.refresh'))}</button>
      <button type="button" class="changes-close" data-file-explorer-changes-close title="${esc(t('changes.hide'))}" aria-label="${esc(t('changes.hide'))}">×</button>
    </div>
    ${changesComparisonHeaderHtml(payload, files, {loading, compact: true})}
    ${errorHtml}
    ${empty ? empty : '<div class="changes-groups"></div>'}`;
}

// Recursively count changed files in a directory subtree for Differ dir badge.
function countChangedFilesInDir(dirPath, entriesByDir, sessionFilesMap) {
  const children = entriesByDir ? (entriesByDir.get(normalizeDirectoryPath(dirPath)) || []) : [];
  let count = 0;
  for (const child of children) {
    const childPath = dirPath === '/' ? `/${child.name}` : `${dirPath}/${child.name}`;
    if (child.kind === 'file') {
      if (sessionFilesMap && sessionFilesMap.has(childPath)) count++;
    } else if (child.kind === 'dir') {
      count += countChangedFilesInDir(childPath, entriesByDir, sessionFilesMap);
    }
  }
  return count;
}

function dataAttributeName(key) {
  return `data-${String(key).replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)}`;
}

function serializedElementAttributes(element) {
  const attrs = [];
  const attrMap = element?.attributes || {};
  const existing = new Set();
  const add = (name, value) => {
    if (!name || value === null || value === undefined || value === false || existing.has(name)) return;
    existing.add(name);
    if (value === true) attrs.push(` ${name}`);
    else attrs.push(` ${name}="${esc(value)}"`);
  };
  const className = typeof element.className === 'string' ? element.className : '';
  if (className) add('class', className);
  for (const [name, value] of Object.entries(attrMap)) add(name, value);
  if (element?.dataset && typeof element.dataset === 'object') {
    for (const [key, value] of Object.entries(element.dataset)) add(dataAttributeName(key), value);
  }
  if (element?.hidden === true) add('hidden', true);
  if (element?.draggable === true) add('draggable', 'true');
  return attrs.join('');
}

function serializeElementHtml(element) {
  if (!element) return '';
  if (typeof element.outerHTML === 'string' && element.outerHTML) return element.outerHTML;
  const tagName = String(element.localName || element.tagName || element.nodeName || 'div').toLowerCase();
  const childHtml = Array.from(element.children || []).map(child => serializeElementHtml(child)).join('');
  const body = childHtml || element.innerHTML || esc(element.textContent || '');
  return `<${tagName}${serializedElementAttributes(element)}>${body}</${tagName}>`;
}

function changesGroupsSnapshotHtml(files, options = {}) {
  const groupsEl = document.createElement('div');
  groupsEl.className = 'changes-groups';
  renderChangesGroups(groupsEl, files, options);
  return serializeElementHtml(groupsEl);
}

function fileExplorerChangesPanelHtml() {
  const staticHtml = fileExplorerChangesPanelStaticHtml();
  const groupsHtml = changesGroupsSnapshotHtml(fileExplorerDifferFiles(), {
    payload: fileExplorerSessionFilesPayload,
    compact: fileExplorerMode !== 'diff',
    includeEmptyRepoSections: fileExplorerMode === 'diff',
  });
  return staticHtml.replace('<div class="changes-groups"></div>', groupsHtml);
}

function fileExplorerModeTitle() {
  return fileExplorerMode === 'diff' ? t('changes.hide') : t('changes.show');
}

function fileExplorerModeButtonTitle(mode) {
  // 'Tabber' tooltip is a brand-literal for now (no locale key) to avoid a 14-catalog change while the
  // co-agent is editing locales; the localized title lands with the B6 docs pass.
  if (mode === 'tabber') return 'Tabber: open tabs, tmux windows, and the paths each agent touched';
  return mode === 'diff' ? t('changes.show') : t('changes.hide');
}

function fileExplorerModeButtonLabel(mode) {
  // 'Differ' and 'Tabber' are brand-literal UI labels (like 'Differ' has always been); only 'Finder'
  // is localized because it predates the brand naming.
  if (mode === 'diff') return 'Differ';
  if (mode === 'tabber') return 'Tabber';
  return t('finder.label.finder');
}

function fileExplorerModeSwitcherHtml() {
  const modes = [
    {mode: 'files', label: fileExplorerModeButtonLabel('files')},
    {mode: 'diff', label: fileExplorerModeButtonLabel('diff')},
    {mode: 'tabber', label: fileExplorerModeButtonLabel('tabber')},
  ];
  const aria = modes.map(item => item.label).join(' / ');
  return `<span class="file-explorer-mode-switcher" role="group" aria-label="${esc(aria)}">${modes.map(item => `
              <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="${esc(item.mode)}" title="${esc(fileExplorerModeButtonTitle(item.mode))}" aria-label="${esc(item.label)}" aria-pressed="${fileExplorerMode === item.mode ? 'true' : 'false'}"><span class="file-explorer-mode-label">${esc(item.label)}</span></button>`).join('')}</span>`;
}

function applyFileExplorerMode(panel = null) {
  fileExplorerMode = normalizeFileExplorerMode(fileExplorerMode);
  // Three exclusive body classes drive the pane layout: files = file-tree only (changes panel hidden);
  // diff and tabber both take over the pane (tree hidden, changes panel full) — tabber renders the
  // session/window tree into the same changes container instead of the diff groups.
  document.body.classList.toggle('file-explorer-mode-diff', fileExplorerMode === 'diff');
  document.body.classList.toggle('file-explorer-mode-files', fileExplorerMode === 'files');
  document.body.classList.toggle('file-explorer-mode-tabber', fileExplorerMode === 'tabber');
  const panels = new Set(document.querySelectorAll('.file-explorer-panel'));
  if (panel) panels.add(panel);
  panels.forEach(node => {
    node.dataset.fileExplorerMode = fileExplorerMode;
  });
  document.querySelectorAll('[data-file-explorer-mode-set]').forEach(btn => {
    const mode = normalizeFileExplorerMode(btn.dataset.fileExplorerModeSet);
    const active = mode === fileExplorerMode;
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    btn.title = fileExplorerModeButtonTitle(mode);
    btn.setAttribute('aria-label', fileExplorerModeButtonLabel(mode));
  });
  document.querySelectorAll('[data-file-explorer-mode-toggle]').forEach(btn => {
    btn.setAttribute('aria-pressed', fileExplorerMode === 'diff' ? 'true' : 'false');
    btn.title = fileExplorerModeTitle();
    btn.setAttribute('aria-label', fileExplorerModeTitle());
  });
  syncFileExplorerDiffSessionControls();
  syncFileExplorerChangesCollapseButtons();
}

function setFileExplorerMode(mode, options = {}) {
  const nextMode = normalizeFileExplorerMode(mode);
  if (fileExplorerMode === nextMode && options.force !== true) return false;
  fileExplorerMode = nextMode;
  writeStoredFileExplorerMode(fileExplorerMode);
  applyFileExplorerMode();
  renderFileExplorerChangesPanels({force: true});
  // Tabber renders from the already-polled transcriptMeta + the activity ledger (recency sort), so it
  // needs no Differ changed-files fetch — instead it polls /api/activity while it's the active mode.
  if (fileExplorerMode === 'tabber') {
    fetchTabberActivity();
    resetRuntimeInterval('tabber-activity', () => { if (fileExplorerMode === 'tabber') fetchTabberActivity(); }, tabberActivityRefreshMs);
  } else {
    fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
  }
  return true;
}

function replaceChangesStaticHtml(root, html, options = {}) {
  const scrollElement = options.scrollSelector ? root.querySelector(options.scrollSelector) : root;
  const scrollTop = scrollElement ? scrollElement.scrollTop : 0;
  const scrollLeft = scrollElement ? scrollElement.scrollLeft : 0;
  root.innerHTML = html;
  root.__changesStaticHtml = html;
  const nextScrollElement = options.scrollSelector ? root.querySelector(options.scrollSelector) : root;
  if (nextScrollElement) restoreElementScrollPosition(nextScrollElement, scrollTop, scrollLeft);
}

function renderChangesRoot(root, staticHtml, files, groupOptions = {}, options = {}) {
  if (!root) return;
  const groupsMissing = !root.querySelector?.('.changes-groups');
  const staticChanged = root.__changesStaticHtml !== staticHtml;
  if (options.force === true || staticChanged || groupsMissing) {
    replaceChangesStaticHtml(root, staticHtml, options);
  }
  const groups = root.querySelector?.('.changes-groups');
  if (groups) renderChangesGroups(groups, files, groupOptions);
}

function activeChangesControl(panel) {
  const active = document.activeElement;
  if (!active || !panel?.contains(active)) return null;
  return active.closest?.('[data-session-files-session], [data-session-files-sort], [data-diff-ref-from], [data-diff-ref-to], [data-session-files-refresh], [data-file-explorer-tree-dates], [data-file-tree-expand-collapse-all], [data-changes-folder-toggle], [data-changes-repo-toggle]') || null;
}

async function openChangedFileInDiff(path, ownerSession = '', status = '', repo = '', options = {}) {
  const item = options.item
    || (options.forceNewTab === true ? fileEditorCopyItemFor(path) : reusableFileEditorDiffPreviewItem(path));
  const normalizedStatus = String(status || '').toUpperCase();
  const openDiffMode = options.openMode !== 'edit';
  if (openDiffMode) setFileEditorDiffExpandUnchangedForItem(path, item, false);
  const initialMode = openDiffMode ? 'diff' : 'edit';
  setFileEditorViewMode(path, initialMode, item);
  // Use the payload's own FROM/TO for this file's repo so the diff matches what the panel shows,
  // even when the repo is not in diffRefsByRepo (e.g. a repo outside the active session's checkout).
  const payloadRepoRefs = (() => {
    const refsMap = fileExplorerSessionFilesPayload?.refs_by_repo;
    const key = repo || fileRepoForPath(path);
    const refs = refsMap && typeof refsMap === 'object' ? refsMap[key] : null;
    if (refs?.from_ref || refs?.to_ref) return {fromRef: refs.from_ref, toRef: refs.to_ref};
    return {};
  })();
  noteFileExplorerChangesSessionInteraction(ownerSession);
  const openOptions = {
    item,
    ownerSession,
    viewMode: initialMode,
    forceNewTab: options.forceNewTab === true,
    userInitiated: options.userInitiated !== false,
  };
  const targetSlot = options.targetSlot || (options.userInitiated === false ? '' : fileEditorActivationSlot());
  if (targetSlot) openOptions.targetSlot = targetSlot;
  if (options.targetZone) openOptions.targetZone = options.targetZone;
  if (normalizedStatus === 'D') {
    await openFilesSetAndShow(path, {
      mtime: 0,
      size: 0,
      kind: 'text',
      original: '',
      content: '',
      dirty: false,
      deleted: true,
      gitTracked: true,
    }, openOptions);
  } else {
    await openFileInEditor(path, {name: basenameOf(path), session: ownerSession}, openOptions);
  }
  if (!openDiffMode) {
    renderOpenFilePath(path);
    void refreshOpenFileDiff(path, {silent: true, renderOnComplete: false, ...payloadRepoRefs});
    return;
  }
  const diffReady = await refreshOpenFileDiff(path, {silent: true, renderOnComplete: false, ...payloadRepoRefs});
  const current = openFiles.get(path);
  if (diffReady && fileStateCanRenderDiffView(path, current)) {
    setFileEditorViewMode(path, 'diff', item);
  } else {
    setFileEditorViewMode(path, 'edit', item);
  }
  renderOpenFilePath(path);
  if (!diffReady || !fileStateCanRenderDiffView(path, current)) {
    const reason = current?.diffError || (current?.kind !== 'text' ? 'not a text file' : 'no git diff or useful history for this file');
    const panel = panelNodes.get(item);
    if (panel) setFileEditorPanelStatus(panel, `diff unavailable: ${reason}`, 'warn');
  }
}

// the diff-ref Escape-revert and picker-open were written twice — once for the changes panel
// (bindChangesPanel) and once for the file-editor toolbar (createFileEditorPanel). The revert carries the
// C6 per-repo fix and had already diverged on which dataset key it reads; keeping the value computation in
// one place stops the two copies restoring different refs. Each call site still owns its listener wiring
// and its own repo/path SOURCE (changes reads the controls element's dataset; the editor reads the rendered
// diff-ref panel's dataset) — only the shared body lives here.
function revertDiffRefInputToRepo(input, repo, path) {
  if (!input) return;
  const escRefs = repoDiffRefs(repo);
  input.value = input.matches('[data-diff-ref-from]')
    ? diffRefInputDisplayValue(escRefs.from, diffRefFromSuggestions(repo, path))
    : diffRefInputDisplayValue(escRefs.to, diffRefToSuggestions(escRefs.from, repo, path));
  input.blur?.();
}

function openDiffRefPickerForInput(input, controls) {
  refreshDiffRefToDatalist(controls);
  showDiffRefPicker(input, {showAll: true});
}

function bindChangesPanel(panel) {
  if (!panel || panel.dataset.changesBound === 'true') return;
  panel.dataset.changesBound = 'true';
  panel.addEventListener('change', event => {
    const sessionSelect = event.target.closest('[data-session-files-session]');
    if (sessionSelect && panel.contains(sessionSelect)) {
      fileExplorerChangesSelectedSession = sessionSelect.value;
      rememberFileExplorerExplicitSyncSession(fileExplorerChangesSelectedSession);
      switchFileExplorerChangesSession(fileExplorerChangesSelectedSession);
      return;
    }
    const sortSelect = event.target.closest('[data-session-files-sort]');
    if (sortSelect && panel.contains(sortSelect)) {
      sessionFilesSortMode = normalizeSessionFilesSortMode(sortSelect.value);
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
  panel.addEventListener('input', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    refreshDiffRefToDatalist(diffRefInput.closest('[data-diff-ref-controls]'));
    renderDiffRefPopover(diffRefInput, {showAll: false});
  });
  panel.addEventListener('focusin', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-input]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('[data-diff-ref-controls]'));
  });
  panel.addEventListener('pointerdown', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-input]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('[data-diff-ref-controls]'));
  });
  panel.addEventListener('keydown', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    if (handleDiffRefPopoverKeydown(event, diffRefInput)) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      hideDiffRefPopover();
      commitDiffRefControls(diffRefInput.closest('[data-diff-ref-controls]') || panel);
      diffRefInput.blur?.();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      hideDiffRefPopover();
      // C6: revert to THIS repo's current ref, not the global default.
      const controls = diffRefInput.closest('[data-diff-ref-controls]');
      revertDiffRefInputToRepo(diffRefInput, controls?.dataset?.diffRefRepo || '', controls?.dataset?.diffRefPath || '');
    }
  });
  panel.addEventListener('click', async event => {
    const treeExpandCollapseAll = event.target.closest('[data-file-tree-expand-collapse-all]');
    if (treeExpandCollapseAll && panel.contains(treeExpandCollapseAll)) {
      event.preventDefault();
      event.stopPropagation();
      await setAllFileTreeDirectoriesExpanded(treeExpandCollapseAll, treeExpandCollapseAll.dataset.fileTreeExpandCollapseAll === 'expand');
      return;
    }
    const collapseToggle = event.target.closest('[data-session-files-collapse-toggle]');
    if (collapseToggle && panel.contains(collapseToggle)) {
      event.preventDefault();
      event.stopPropagation();
      toggleAllFileExplorerChanges();
      return;
    }
    const repoToggle = event.target.closest('[data-changes-repo-toggle]');
    if (repoToggle && panel.contains(repoToggle)) {
      event.preventDefault();
      const repo = repoToggle.dataset.changesRepoToggle || '';
      if (changesRepoCollapsed.has(repo)) changesRepoCollapsed.delete(repo);
      else changesRepoCollapsed.add(repo);
      writeStoredChangesRepoCollapsed();
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
      renderFileExplorerChangesPanels({force: true});
      return;
    }
    const diffRefReset = event.target.closest('[data-diff-ref-reset]');
    if (diffRefReset && panel.contains(diffRefReset)) {
      event.preventDefault();
      const controls = diffRefReset.closest('[data-diff-ref-controls]');
      const repo = controls?.dataset?.diffRefRepo || '';
      const path = controls?.dataset?.diffRefPath || '';
      setRepoDiffRefs(repo, 'HEAD', 'current', {path});
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
  // Single-click opens the row in the active editor pane. Modifier clicks keep the shared Finder-style
  // multi-select behavior without opening a diff.
  panel.addEventListener('click', async event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    const selectionOnly = updateFileTreeSelectionFromClick(fileRow, fileRow.dataset.path || fileRow.dataset.openChangeFile || '', event);
    if (selectionOnly) return;
    const path = fileRow.dataset.openChangeFile;
    if (!path) return;
    event.preventDefault();
    await openChangedFileInDiff(path, fileRow.dataset.openChangeSession || '', fileRow.dataset.openChangeStatus || '', fileRow.dataset.openChangeRepo || '', {userInitiated: true});
  });
  panel.addEventListener('contextmenu', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (fileRow && panel.contains(fileRow)) {
      event.preventDefault();
      const path = fileRow.dataset.path || fileRow.dataset.openChangeFile || '';
      showFileTreeContextMenu(fileRow, path, changedFileRowEntry(fileRow), event.clientX, event.clientY, {
        openInNewTabActions: [
          {
            label: t('contextmenu.openNewDiffEditor'),
            action: () => openChangedFileInDiff(
              path,
              fileRow.dataset.openChangeSession || '',
              fileRow.dataset.openChangeStatus || '',
              fileRow.dataset.openChangeRepo || '',
              {forceNewTab: true, userInitiated: true, openMode: 'diff'},
            ),
          },
          {
            label: t('contextmenu.openNewEditor'),
            action: () => openFileInAdditionalEditorTab(path, changedFileRowEntry(fileRow), {
              targetSlot: fileEditorActivationSlot(),
              userInitiated: true,
              viewMode: 'edit',
            }),
          },
        ],
      });
      return;
    }
    const directoryRow = event.target.closest('[data-open-change-directory]');
    if (!directoryRow || !panel.contains(directoryRow)) return;
    event.preventDefault();
    showChangedDirectoryContextMenu(directoryRow, event.clientX, event.clientY);
  });
}

function changedFileRowEntry(row) {
  const path = row?.dataset?.openChangeFile || row?.dataset?.path || '';
  return {
    kind: row?.dataset?.kind || 'file',
    name: row?.dataset?.name || basenameOf(path),
    path,
  };
}

function showChangedDirectoryContextMenu(row, x, y) {
  closeFileContextMenu();
  closeFileImagePreview();
  const path = row.dataset.openChangeDirectory || '';
  if (!path) return;
  const rel = row.dataset.changeRel || '';
  const displayName = rel || basenameOf(path) || path;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu file-context-menu';
  menu.setAttribute('role', 'menu');
  appendContextMenuButton(menu, 'Copy relative path', () => copyChangedPath(rel || path, 'relative path'), closeFileContextMenu);
  appendContextMenuButton(menu, 'Copy full path', () => copyChangedPath(path, 'full path'), closeFileContextMenu);
  appendContextMenuButton(menu, `Expand ${displayName} in ${fileExplorerLabel()}`, () => openChangedDirectoryInFinder(path), closeFileContextMenu);
  fileContextMenu.open(menu, x, y);
}

async function copyChangedPath(path, label) {
  try {
    await copyTextToClipboard(path);
    statusEl.textContent = `copied ${label}`;
  } catch (error) {
    statusErr(localizedHtml('status.copyFailed', {error}));
  }
}

async function openChangedDirectoryInFinder(path) {
  try {
    await openFileExplorerPane();
    setFileExplorerMode('files');
    const root = currentFileExplorerRoot();
    if (root && pathIsInsideDirectory(path, root)) {
      const expanded = await expandFileExplorerTreesToPath(path);
      if (expanded) {
        selectFileTreePath(path);
        statusEl.textContent = `expanded ${path} in ${fileExplorerLabel()}`;
        return;
      }
    }
    const opened = await openFileExplorerAt(path);
    if (!opened) return;
    selectFileTreePath(path);
    statusEl.textContent = `expanded ${path} in ${fileExplorerLabel()}`;
  } catch (error) {
    statusErr(localizedHtml('status.expandDirectoryFailed', {error}));
  }
}

// C5: per-render binding for Modified-files rows (rows are recreated each render). Binds the Finder image
// hover preview on image rows under the preview cap (unknown size -> bind and let /api/fs/raw fail
// gracefully, like Finder).
function bindChangedFileRowBehaviors(panel) {
  if (!panel) return;
  panel.querySelectorAll('[data-open-change-file]').forEach(row => {
    const path = row.dataset.openChangeFile || '';
    if (!path) return;
    const name = basenameOf(path);
    if (IMAGE_EXTENSIONS.has(fileExtensionOf(name))) {
      const sizeText = row.dataset.changeSize;
      const size = sizeText === undefined || sizeText === '' ? null : Number(sizeText);
      if (size === null || !Number.isFinite(size) || size <= MAX_FILE_PREVIEW_BYTES) {
        bindFileImagePreview(row, path, {kind: 'file', name, size});
      }
    }
  });
}

function activePreferenceControl(panel) {
  const active = document.activeElement;
  if (!active || !panel?.contains(active)) return null;
  return active.closest?.('[data-setting-path], [data-preferences-search], [data-preferences-search-action], [data-preference-section-toggle], [data-preferences-reset-all], [data-preferences-reset-confirm], [data-preferences-reset-cancel]') || null;
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
  if (type === 'number' || type === 'range') {
    const item = preferenceItemByPath(control.dataset.settingPath || '');
    if (!item) return Number(control.value);
    const clamped = clampPreferenceNumber(item, control.value);
    control.value = clamped;
    validatePreferenceNumberControl(control);
    const scale = Number(item.scale) || 1;
    return Number(clamped) * scale;
  }
  if (type === 'list') {
    const maxItems = Number(control.dataset.settingMaxItems || 0);
    const items = String(control.value || '').split('\n').map(line => line.trim()).filter(Boolean);
    return Number.isFinite(maxItems) && maxItems > 0 ? items.slice(0, maxItems) : items;
  }
  return control.value;
}

async function saveSettingsPatch(patch, options = {}) {
  if (readOnlyMode) return;
  const preservedLayout = options.preserveLayout === false ? null : cloneLayoutSlots();
  const preservedLayoutSignature = preservedLayout ? layoutSlotsSignature(preservedLayout) : '';
  const preservedFocus = focusedPanelItem;
  const payload = await apiFetchJson('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({settings: patch}),
  });
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
      .catch(error => { statusErr(`${esc(t('upload.copyFailed', {error}))}`); });
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
  // #260: apply the global theme live the moment the radio changes (the old theme-cards relied on a
  // re-render; the radio routes through here, so flip body.theme-* + the editor/terminal palettes now).
  if (path === 'appearance.theme') {
    globalThemeMode = normalizeGlobalThemeMode(value);
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true});
    renderSessionButtons();  // keep the View -> Theme active marker in sync with the Preferences radio
  }
  if (path === 'appearance.inactive_pane_opacity') {
    applyInactivePaneOpacity(value);
  }
  if (path === 'appearance.pane_ring_opacity') {
    applyPaneRingOpacity(value);
  }
  if (path === 'appearance.active_color') {
    applyActiveColor(value);
  }
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
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
}

function resetPreference(path) {
  const item = preferenceItemByPath(path);
  if (!item) return;
  saveSettingsPatch(settingPatch(path, preferenceDefault(path)), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => { statusEl.textContent = `reset ${path}`; })
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error})); });
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
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error})); });
}

function clampEditorPreviewFontSize(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return Math.max(8, Math.min(32, editorFontSize + 1));
  return Math.max(8, Math.min(32, Math.round(number)));
}

function updateEditorPreviewFontControls(scope = document) {
  const value = clampEditorPreviewFontSize(editorPreviewFontSize);
  scope.querySelectorAll?.('.file-editor-preview-font-value')?.forEach(node => {
    node.textContent = `${value}`;
  });
  scope.querySelectorAll?.('[data-editor-preview-font-step]')?.forEach(button => {
    const step = Number(button.dataset.editorPreviewFontStep || 0);
    button.disabled = readOnlyMode || !step || (step < 0 && value <= 8) || (step > 0 && value >= 32);
    button.setAttribute('aria-disabled', button.disabled ? 'true' : 'false');
  });
}

function setEditorPreviewFontSize(value) {
  if (readOnlyMode) return;
  const next = clampEditorPreviewFontSize(value);
  if (next === editorPreviewFontSize) {
    updateEditorPreviewFontControls();
    return;
  }
  editorPreviewFontSize = next;
  applyCssSettings();
  updateEditorPreviewFontControls();
  refreshFilePreviewPopouts();
  saveSettingsPatch(settingPatch('appearance.preview_font_size', next))
    .then(() => { statusEl.textContent = 'saved appearance.preview_font_size'; })
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
}

// File Explorer pane content is self-contained so layout panes do not depend on
// the older left-edge overlay tree.
function createFileExplorerPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel file-explorer-panel';
  panel.id = panelDomId(fileExplorerItemId);
  const initialPath = fileExplorerRoot || homePath || '/';
  const label = fileExplorerLabel();
  panel.innerHTML = `
      <div class="panel-head file-explorer-head">
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
        <div class="file-explorer-toolbar">
          <div class="file-explorer-toolbar-row file-explorer-primary-row">
            ${fileExplorerModeSwitcherHtml()}
            ${fileExplorerDiffSessionControlHtml(fileExplorerSessionFilesTargetSession())}
            <input class="file-explorer-path-inline file-explorer-mode-files-only" type="text" value="${esc(initialPath)}" spellcheck="false" aria-label="${esc(t('finder.toolbar.rootPath', {name: label}))}">
            <button type="button" class="path-copy-button file-explorer-path-copy-panel file-explorer-mode-files-only" title="${esc(t('finder.toolbar.copyPath'))}" aria-label="${esc(t('finder.toolbar.copyPath'))}"></button>
            <span class="file-explorer-toolbar-spacer"></span>
            ${fileExplorerChangesCollapseToggleHtml()}
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
          <div class="file-explorer-toolbar-row file-explorer-scope-row file-explorer-mode-files-only">
            <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel file-explorer-mode-files-only" title="${esc(t('finder.toolbar.hidden'))}" aria-pressed="${fileExplorerShowHidden ? 'true' : 'false'}">.*</button>
            <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel file-explorer-mode-files-only" title="${esc(t('finder.toolbar.syncTitle'))}" aria-label="${esc(t('finder.toolbar.syncTitle'))}" aria-pressed="true">${esc(t('finder.toolbar.syncLabel'))}</button>
            <div class="file-explorer-quick-access-panel file-explorer-mode-files-only" aria-label="${esc(t('finder.toolbar.quickPaths'))}"></div>
            <span class="file-explorer-toolbar-spacer"></span>
          </div>
          <div class="file-explorer-toolbar-row file-explorer-actions-row file-explorer-mode-files-only">
            <button type="button" class="file-explorer-header-action file-explorer-mode-files-only" data-file-explorer-new-file title="${esc(t('finder.toolbar.newFile'))}" aria-label="${esc(t('finder.toolbar.newFile'))}">+</button>
            <button type="button" class="file-explorer-header-action file-explorer-folder-action file-explorer-mode-files-only" data-file-explorer-new-folder title="${esc(t('finder.toolbar.newFolder'))}" aria-label="${esc(t('finder.toolbar.newFolder'))}"><span class="file-explorer-folder-icon" aria-hidden="true"></span></button>
            <span class="file-explorer-toolbar-spacer"></span>
            <select class="file-explorer-sort-select file-explorer-mode-files-only" data-file-explorer-tree-sort title="${esc(t('finder.toolbar.sort'))}" aria-label="${esc(t('finder.toolbar.sort'))}">
              <option value="az"${fileExplorerTreeSortMode === 'az' ? ' selected' : ''}>${esc(t('finder.sort.az'))}</option>
              <option value="za"${fileExplorerTreeSortMode === 'za' ? ' selected' : ''}>${esc(t('finder.sort.za'))}</option>
              <option value="newest"${fileExplorerTreeSortMode === 'newest' ? ' selected' : ''}>${esc(t('finder.sort.newest'))}</option>
              <option value="oldest"${fileExplorerTreeSortMode === 'oldest' ? ' selected' : ''}>${esc(t('finder.sort.oldest'))}</option>
            </select>
            <span class="file-explorer-date-reload-cluster file-explorer-mode-files-only">
              ${fileExplorerTreeDateButtonHtml('changes-date-toggle')}
              ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
              <button type="button" class="changes-refresh file-explorer-refresh-cluster" data-file-explorer-refresh title="${esc(t('finder.toolbar.refresh'))}" aria-label="${esc(t('finder.toolbar.refresh'))}">${esc(t('changes.refresh'))}</button>
            </span>
          </div>
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
    syncFileExplorerRootModeButton(rootModeBtn);
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
  // The panel-head mode button switches the same Finder pane between files and diff.
  panel.addEventListener('click', event => {
    const modeSet = event.target.closest?.('[data-file-explorer-mode-set]');
    if (modeSet) {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerMode(modeSet.dataset.fileExplorerModeSet);
    } else if (event.target.closest?.('[data-file-explorer-mode-toggle]')) {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerMode(fileExplorerMode === 'diff' ? 'files' : 'diff');
    } else if (event.target.closest?.('[data-file-explorer-changes-close]')) {
      event.preventDefault();
      event.stopPropagation();
      setFileExplorerMode('files');
    }
  });
  applyFileExplorerMode(panel);
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
    if (clientPushCanSupplyData()) {
      if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    } else {
      fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true});
    }
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
  if (clientPushCanSupplyData() && !options.entries && options.force !== true) {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    return;
  }
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
  if (fileExplorerMode === 'tabber') {
    if (options.force === true || !changes.querySelector('.changes-groups')) {
      replaceChangesStaticHtml(changes, fileExplorerChangesPanelStaticHtml());
    }
    renderTabberTree(changes.querySelector('.changes-groups'));
    bindTabberPanel(panel);
    return;
  }
  if (options.force === true || !activeChangesControl(panel)) {
    renderChangesRoot(
      changes,
      fileExplorerChangesPanelStaticHtml({full: fileExplorerMode === 'diff'}),
      fileExplorerDifferFiles(),
      {
        payload: fileExplorerSessionFilesPayload,
        compact: fileExplorerMode !== 'diff',
        includeEmptyRepoSections: fileExplorerMode === 'diff',
      },
      {force: options.force === true},
    );
  }
  syncFileExplorerDiffSessionControls();
  bindChangesPanel(panel);
  bindChangedFileRowBehaviors(panel);
  syncFileExplorerChangesCollapseButtons();
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
  if (dirtyChanged) {
    if (dirty) delete state.lastCleanAt;
    else state.lastCleanAt = Date.now();
  }
  updateFileEditorPanelChrome(panel, path);
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
  renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
  renderLinkedFilePreviewPanels(panel, path, state.content);
  updateFilePreviewPopout(path, state.content);
  scheduleFileEditorSplitScrollSync(panel, 'editor');
  const item = fileEditorPanelItem(panel);
  if (item && panel?.contains?.(document.activeElement)) {
    scheduleFileExplorerActiveTabSync(item, {explicit: true});
  }
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

async function enterFileEditorDiffMode(path, panel, item) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return;
  if (!fileStateHasRepo(path, state)) {
    setFileEditorViewMode(path, 'edit', item);
    renderFileEditorPanel(panel, item);
    return;
  }
  if (openFileDiffAvailable(state)) {
    setFileEditorViewMode(path, 'diff', item);
    renderFileEditorPanel(panel, item);
    return;
  }
  const loadPromise = refreshOpenFileDiff(path, {silent: true, renderOnComplete: false});
  renderFileEditorPanel(panel, item);
  await loadPromise;
  const current = openFiles.get(path);
  if (!current || current.kind !== 'text' || panel.dataset.filePath !== path) return;
  if (fileStateHasRepo(path, current) && (openFileDiffAvailable(current) || fileStateHasUsefulGitHistory(current))) {
    setFileEditorViewMode(path, 'diff', item);
  } else {
    setFileEditorViewMode(path, 'edit', item);
  }
  renderFileEditorPanel(panel, item);
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
        <div class="file-editor-toolbar-zone file-editor-toolbar-left">
          <button type="button" class="file-editor-gutter-panel" title="${esc(t('editor.toggleLineNumbers'))}" aria-label="${esc(t('editor.toggleLineNumbers'))}" hidden>#</button>
          <button type="button" class="file-editor-diff-panel" title="${esc(t('editor.diff'))}" aria-label="${esc(t('editor.diff'))}" hidden>Differ</button>
          <button type="button" class="file-editor-diff-expand-panel" title="${esc(t('editor.diffExpand'))}" aria-label="${esc(t('editor.diffExpand'))}" aria-pressed="${fileEditorDiffExpandUnchangedForItem(item) ? 'true' : 'false'}" hidden>↕</button>
          <span class="file-editor-diff-ref-panel" hidden>${diffRefControlsHtml({compact: true})}</span>
        </div>
        <div class="file-editor-toolbar-zone file-editor-toolbar-center">
          <span class="file-editor-preview-font-panel" role="group" aria-label="${esc(t('editor.previewFont.aria'))}" hidden>
            <button type="button" data-editor-preview-font-step="-1" title="${esc(t('editor.previewFont.decrease'))}" aria-label="${esc(t('editor.previewFont.decrease'))}">A-</button>
            <span class="file-editor-preview-font-value" aria-live="polite">${esc(String(editorPreviewFontSize))}</span>
            <button type="button" data-editor-preview-font-step="1" title="${esc(t('editor.previewFont.increase'))}" aria-label="${esc(t('editor.previewFont.increase'))}">A+</button>
          </span>
        </div>
        <div class="file-editor-toolbar-zone file-editor-toolbar-right">
          <button type="button" class="file-editor-theme-panel" title="${esc(t('editor.theme'))}" aria-label="${esc(t('editor.theme'))}"><span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span></button>
          <div class="file-editor-mode-control file-editor-mode-control-panel" role="group" aria-label="${esc(t('editor.mode.aria'))}" hidden>
            <button type="button" data-editor-mode="edit" title="${esc(t('editor.mode.edit'))}" aria-label="${esc(t('editor.mode.edit'))}"><span class="file-editor-icon file-editor-icon-edit" aria-hidden="true"></span></button>
            <button type="button" data-editor-mode="preview" title="${esc(t('editor.mode.preview'))}" aria-label="${esc(t('editor.mode.preview'))}"><span class="file-editor-icon file-editor-icon-eye" aria-hidden="true"></span></button>
            <button type="button" data-editor-mode="split" title="${esc(t('editor.mode.split'))}" aria-label="${esc(t('editor.mode.split'))}"><span class="file-editor-icon file-editor-icon-split" aria-hidden="true"></span></button>
            <button type="button" class="file-editor-popout-preview-panel" title="${esc(t('editor.popoutPreview'))}" aria-label="${esc(t('editor.popoutPreview'))}" hidden><span class="file-editor-icon file-editor-icon-popout-preview" aria-hidden="true"></span></button>
          </div>
          <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="mode" aria-hidden="true" hidden></span>
          <button type="button" class="file-editor-wrap-panel" title="${esc(t('editor.toggleWordWrap'))}" aria-label="${esc(t('editor.toggleWordWrap'))}" hidden><span class="file-editor-icon file-editor-icon-wrap" aria-hidden="true"></span></button>
          <button type="button" class="file-editor-find-panel" title="${esc(t('editor.findInFile', {shortcut: appShortcutText('F')}))}" aria-label="${esc(t('editor.findInFileAria'))}" aria-pressed="false" hidden><span class="file-editor-icon file-editor-icon-find" aria-hidden="true"></span></button>
          <button type="button" class="file-editor-blame-panel" title="${esc(t('editor.blame.toggle'))}" aria-label="${esc(t('editor.blame.toggle'))}" aria-pressed="${fileEditorBlameEnabled ? 'true' : 'false'}" hidden><span class="file-editor-icon file-editor-icon-blame" aria-hidden="true"></span></button>
          <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="tools" aria-hidden="true" hidden></span>
          <button type="button" class="file-editor-reload-panel" title="${esc(t('editor.reloadFromDisk'))}" aria-label="${esc(t('editor.reloadFromDisk'))}" hidden>${esc(t('editor.reload'))}</button>
          <span class="file-editor-toolbar-separator" data-editor-toolbar-separator="theme" aria-hidden="true" hidden></span>
          <button type="button" class="file-editor-save-panel" title="${esc(t('editor.save'))}" aria-label="${esc(t('editor.saveFile'))}" ${readOnlyMode ? 'hidden' : ''}><span class="file-editor-icon file-editor-icon-save" aria-hidden="true"></span></button>
        </div>
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
  panel.addEventListener('click', event => {
    if (event.defaultPrevented) return;
    if (event.target?.closest?.('button, a, input, textarea, select, [data-diff-ref-input]')) return;
    scheduleFileExplorerActiveFileReveal(path);
  });
  delegate(panel, 'pointerdown', 'button', event => event.stopPropagation());
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
    if (mode === 'diff' && editorViewModeFor(path, item) !== 'diff') {
      enterFileEditorDiffMode(path, panel, item);
      return;
    }
    setFileEditorViewMode(path, mode, item);
    renderFileEditorPanel(panel, item);
  });
  panel.querySelector('.file-editor-gutter-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorLineNumbers();
  });
  panel.querySelector('.file-editor-preview-font-panel')?.addEventListener('click', event => {
    const button = event.target?.closest?.('[data-editor-preview-font-step]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    setEditorPreviewFontSize(editorPreviewFontSize + Number(button.dataset.editorPreviewFontStep || 0));
  });
  panel.querySelector('.file-editor-wrap-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorWrap();
  });
  panel.querySelector('.file-editor-find-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleEditorFind(panel);
  });
  panel.querySelector('.file-editor-blame-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget?.disabled) return;
    toggleFileEditorBlame();  // inline git blame on/off (persisted, fetches + re-renders editors)
  });
  panel.querySelector('.file-editor-diff-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget?.disabled || event.currentTarget?.hidden) return;
    const nextMode = editorViewModeFor(path, item) === 'diff' ? 'edit' : 'diff';
    if (nextMode === 'diff') {
      enterFileEditorDiffMode(path, panel, item);
      return;
    }
    setFileEditorViewMode(path, nextMode, item);
    renderFileEditorPanel(panel, item);
  });
  panel.querySelector('.file-editor-diff-expand-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    toggleFileEditorDiffExpandUnchangedForItem(path, item);  // show all context vs collapse unchanged runs for this editor
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
  diffRefPanel?.addEventListener('input', event => {
    const input = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!input) return;
    refreshDiffRefToDatalist(diffRefPanel);
    renderDiffRefPopover(input, {showAll: false});
  });
  diffRefPanel?.addEventListener('focusin', event => {
    const input = event.target.closest('[data-diff-ref-input]');
    if (!input) return;
    openDiffRefPickerForInput(input, diffRefPanel);
  });
  diffRefPanel?.addEventListener('pointerdown', event => {
    const input = event.target.closest('[data-diff-ref-input]');
    if (!input) return;
    openDiffRefPickerForInput(input, diffRefPanel);
  });
  diffRefPanel?.addEventListener('keydown', event => {
    const input = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!input) return;
    event.stopPropagation();
    if (handleDiffRefPopoverKeydown(event, input)) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      hideDiffRefPopover();
      commitDiffRefControls(diffRefPanel);
      input.blur?.();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      hideDiffRefPopover();
      // C6: revert to this file's repo refs, not the global default.
      revertDiffRefInputToRepo(input, diffRefPanel?.dataset?.diffRefRepoRendered || '', diffRefPanel?.dataset?.diffRefPathRendered || '');
    }
  });
  diffRefPanel?.addEventListener('click', event => {
    if (!event.target.closest('[data-diff-ref-reset]')) return;
    event.preventDefault();
    event.stopPropagation();
    const controls = event.target.closest('[data-diff-ref-controls]');
    const repo = controls?.dataset?.diffRefRepo || '';
    const path = controls?.dataset?.diffRefPath || '';
    setRepoDiffRefs(repo, 'HEAD', 'current', {path});
  });
  panel.querySelector('.file-editor-popout-preview-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (openFilePreviewPopout(path, panel)) {
      setFileEditorViewMode(path, 'edit', item);
      renderFileEditorPanel(panel, item);
    }
  });
  panel.querySelector('.file-editor-theme-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cycleEditorThemeMode({includeVanilla: true});
  });
  panel.querySelector('.file-editor-preview-pane-panel')?.addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'preview'));
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
  const original = fileEditorImageModeForPath(path) === 'original';
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
      const nextMode = fileEditorImageModeForPath(path) === 'original' ? 'fit' : 'original';
      setFileEditorImageModeForPath(path, nextMode);
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

function scheduleFileEditorPanelViewStateCapture(item, panel) {
  schedulePaneViewStateCapture(item, panel);
}

function focusFileEditorPanelIfReady(panel, item) {
  if (!autoFocusEnabled) {
    pendingFileEditorFocus.delete(item);
    return false;
  }
  if (!pendingFileEditorFocus.has(item) || focusedPanelItem !== item) return false;
  if (panel?._cmView) {
    panel._cmView.focus?.();
    // CodeMirror focus can scroll the cursor into view. Re-apply the saved viewport after focus so
    // a long file that was only scrolled, not cursor-moved, does not jump back to the cursor line.
    restorePaneViewState(item, panel);
    pendingFileEditorFocus.delete(item);
    return true;
  }
  return false;
}

function fileEditorPanelViewStateCaptureHasLayout(panel, scrollDOM) {
  // Pooled or hidden CodeMirror panes report a zero viewport and would erase the last visible scroll.
  if (panel && typeof panel.isConnected === 'boolean' && !panel.isConnected) return false;
  if (scrollDOM && typeof scrollDOM.clientHeight === 'number' && scrollDOM.clientHeight <= 0) return false;
  return true;
}

function captureFileEditorPanelViewState(item, panel) {
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!isFileEditorItem(item) || !view || !scrollDOM) return;
  if (fileEditorViewState.has(item) && !fileEditorPanelViewStateCaptureHasLayout(panel, scrollDOM)) return;
  const selection = view.state?.selection?.main;
  fileEditorViewState.set(item, {
    scrollTop: scrollDOM.scrollTop || 0,
    scrollLeft: scrollDOM.scrollLeft || 0,
    anchor: Number(selection?.anchor || 0),
    head: Number(selection?.head || selection?.anchor || 0),
    scrollSnapshot: typeof view.scrollSnapshot === 'function' ? view.scrollSnapshot() : null,
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
    view.dispatch({
      selection: {anchor, head},
      ...(state.scrollSnapshot ? {effects: state.scrollSnapshot} : {}),
    });
  } catch (_) {
    try { view.dispatch({selection: {anchor, head}}); } catch (_) {}
  }
  const targetTop = Number(state.scrollTop || 0);
  const targetLeft = Number(state.scrollLeft || 0);
  const scrollStillAtTarget = () => (
    Math.abs(Number(scrollDOM.scrollTop || 0) - targetTop) <= 1
    && Math.abs(Number(scrollDOM.scrollLeft || 0) - targetLeft) <= 1
  );
  const restore = () => {
    scrollDOM.scrollTop = targetTop;
    scrollDOM.scrollLeft = targetLeft;
  };
  const measuredRestore = (guardUserScroll = false) => {
    if (guardUserScroll && !scrollStillAtTarget()) return;
    if (typeof view.requestMeasure === 'function') {
      view.requestMeasure({
        read: () => null,
        write: () => {
          if (!guardUserScroll || scrollStillAtTarget()) restore();
        },
      });
      return;
    }
    restore();
  };
  restore();
  measuredRestore();
  requestAnimationFrame(() => measuredRestore(true));
  requestAnimationFrame(() => requestAnimationFrame(() => measuredRestore(true)));
}

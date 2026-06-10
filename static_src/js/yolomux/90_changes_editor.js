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
  fileExplorerSessionFilesCache.clear();
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

function switchFileExplorerChangesSession(session) {
  if (!session || !document.querySelector('.file-explorer-changes-panel')) return;
  rememberFileExplorerExplicitSyncSession(session);
  fileExplorerChangesSelectedSession = session;
  const cached = fileExplorerSessionFilesCache.get(sessionFilesCacheKey(session));
  if (fileExplorerMode === 'diff' && cached?.payload) {
    setSessionFilesPayloadForDestination('finder', cached.payload);
    fileExplorerSessionFilesPayloadSignature = cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload);
  } else if (fileExplorerMode !== 'diff' && sessionFilesPayloadIsFinderWorktree(fileExplorerSessionFilesPayload, session)) {
    fileExplorerSessionFilesPayloadSignature = sessionFilesPayloadSignatureForPayload(fileExplorerSessionFilesPayload);
  } else {
    const pendingPayload = emptySessionFilesPayload(session, false);
    setSessionFilesPayloadForDestination('finder', pendingPayload);
    fileExplorerSessionFilesPayloadSignature = sessionFilesPayloadSignatureForPayload(pendingPayload);
  }
  renderFileExplorerChangesPanels();
  fetchSessionFiles({destination: 'finder', session, silent: true, force: true});
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
  setSessionFilesLoadingForDestination(destination, true);
  if (!options.silent) statusEl.textContent = 'loading changed files...';
  if (!options.silent) {
    renderSessionFilesDestination(destination, {force: true});
    renderPaneTabStrips();
  }
  try {
    // C6: Differ follows selected refs; Finder file mode must stay tied to the current worktree so it
    // does not paint historical diff badges after the repo is clean.
    const response = await apiFetch(`/api/session-files?session=${encodeURIComponent(session)}&hours=24&${sessionFilesRequestQueryString()}`);
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
    if (!options.silent) statusOk(`loaded ${nextPayload.files.length} changed file${nextPayload.files.length === 1 ? '' : 's'}`);
  } catch (err) {
    const nextPayload = {session, files: [], repos: [], refs_by_repo: {}, errors: [String(err)], from_ref: diffRefFrom, to_ref: diffRefTo, loaded: true};
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload);
    setSessionFilesSignatureForDestination(destination, signature);
    if (!options.silent) statusErr(`changed files failed: ${esc(err)}`);
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
  storageSet(uploadedFilesCollapsedStorageKey, uploadedFilesCollapsed ? '1' : '0');
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
  if (loading) return t('changes.loading');
  if (!loaded) return t('changes.notLoaded');
  const fileCount = Array.isArray(files) ? files.length : 0;
  const repoCount = changesRepoCount(payload, files);
  const repos = tPlural('changes.repoCount', repoCount);
  const count = tPlural('changes.fileCount', fileCount);
  const scope = session ? t('changes.inSession', {session: sessionLabel(session)}) : '';
  return `<span class="changes-summary-label">${esc(repos)}, ${esc(count)}${esc(scope)}</span>`;
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
    if (loading) return `<section class="changes-comparison-head compact">${esc(t('changes.loading'))}</section>`;
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

// Render changed files for one repo section using the shared file-tree renderer.
function renderChangedFileList(container, repoPath, sessionFiles, options = {}) {
  const treeRoot = repoPath === 'Outside repo' ? '/' : repoPath;
  const {entries, entriesByDir, sessionFilesMap} = buildSessionFileTree(treeRoot, sessionFiles);
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

function uploadedFileRepoRoot(item) {
  const repo = item?.repo || '';
  if (repo && repo !== 'Outside repo') return repo;
  const absPath = item?.abs_path || item?.path || '';
  return dirnameOf(absPath);
}

function uploadedFileForRepoRoot(item, repoPath) {
  const absPath = item?.abs_path || (repoPath && item?.path ? `${repoPath}/${item.path}` : item?.path || '');
  const relPath = item?.path || (absPath && repoPath && absPath.startsWith(repoPath + '/') ? absPath.slice(repoPath.length + 1) : basenameOf(absPath));
  return {
    ...item,
    repo: repoPath,
    path: relPath,
    abs_path: absPath,
  };
}

function renderUploadedFileList(container, uploadedFiles, options = {}) {
  const groups = new Map();
  for (const item of uploadedFiles || []) {
    const repoPath = uploadedFileRepoRoot(item);
    if (!groups.has(repoPath)) groups.set(repoPath, []);
    groups.get(repoPath).push(uploadedFileForRepoRoot(item, repoPath));
  }
  const groupedFiles = Array.from(groups.entries());
  if (groupedFiles.length <= 1) {
    const [repoPath, files] = groupedFiles[0] || ['', []];
    renderChangedFileList(container, repoPath, files, options);
    return;
  }
  const existing = new Map();
  for (const child of container.children || []) {
    const key = child.dataset?.uploadedRepo;
    if (key) existing.set(key, child);
  }
  const nextNodes = [];
  for (const [repoPath, files] of groupedFiles) {
    let repoList = existing.get(repoPath);
    if (!repoList) {
      repoList = document.createElement('div');
      repoList.className = 'changes-uploaded-repo-list';
      repoList.dataset.uploadedRepo = repoPath;
    }
    renderChangedFileList(repoList, repoPath, files, options);
    nextNodes.push(repoList);
  }
  reconcileChildNodes(container, nextNodes);
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
  const {regular, uploaded} = splitUploadedSessionFiles(files);
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
  if (uploaded.length) {
    let uploadedSection = existing.get('__uploaded__');
    if (!uploadedSection) {
      uploadedSection = document.createElement('section');
      uploadedSection.className = 'changes-repo-group changes-uploaded-group';
      uploadedSection.dataset.changesRepo = '__uploaded__';
    }
    let head = uploadedSection.querySelector(':scope > .changes-uploaded-toggle');
    if (!head) {
      head = document.createElement('button');
      head.type = 'button';
      head.className = 'changes-repo-head changes-uploaded-toggle';
      head.dataset.uploadedFilesToggle = '';
      uploadedSection.prepend(head);
    }
    head.setAttribute('aria-expanded', uploadedFilesCollapsed ? 'false' : 'true');
    head.innerHTML = `<span><span class="changes-uploaded-caret">${uploadedFilesCollapsed ? '▸' : '▾'}</span> ${esc(t('changes.uploaded', {count: uploaded.length}))}</span><span>${uploadedFilesCollapsed ? esc(t('changes.collapsed')) : uploaded.length}</span>`;
    let fileList = uploadedSection.querySelector(':scope > .changes-file-list');
    if (!uploadedFilesCollapsed) {
      if (!fileList) {
        fileList = document.createElement('div');
        fileList.className = 'changes-file-list';
        uploadedSection.append(fileList);
      }
      fileList.hidden = false;
      renderUploadedFileList(fileList, uploaded, {compact});
    } else if (fileList) {
      fileList.hidden = true;
    }
    uploadedSection.classList.toggle('collapsed', uploadedFilesCollapsed);
    nextNodes.push(uploadedSection);
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
  return mode === 'diff' ? t('changes.show') : t('changes.hide');
}

function fileExplorerModeButtonLabel(mode) {
  return mode === 'diff' ? 'Differ' : t('finder.label.finder');
}

function fileExplorerModeSwitcherHtml() {
  const modes = [
    {mode: 'files', label: fileExplorerModeButtonLabel('files')},
    {mode: 'diff', label: fileExplorerModeButtonLabel('diff')},
  ];
  const aria = `${fileExplorerModeButtonLabel('files')} / ${fileExplorerModeButtonLabel('diff')}`;
  return `<span class="file-explorer-mode-switcher" role="group" aria-label="${esc(aria)}">${modes.map(item => `
              <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="${esc(item.mode)}" title="${esc(fileExplorerModeButtonTitle(item.mode))}" aria-label="${esc(item.label)}" aria-pressed="${fileExplorerMode === item.mode ? 'true' : 'false'}"><span class="file-explorer-mode-label">${esc(item.label)}</span></button>`).join('')}</span>`;
}

function applyFileExplorerMode(panel = null) {
  fileExplorerMode = normalizeFileExplorerMode(fileExplorerMode);
  document.body.classList.toggle('file-explorer-mode-diff', fileExplorerMode === 'diff');
  document.body.classList.toggle('file-explorer-mode-files', fileExplorerMode !== 'diff');
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
  fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
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
  return active.closest?.('[data-session-files-session], [data-session-files-sort], [data-diff-ref-from], [data-diff-ref-to], [data-session-files-refresh], [data-file-explorer-tree-dates], [data-uploaded-files-toggle], [data-changes-folder-toggle], [data-changes-repo-toggle]') || null;
}

async function openChangedFileInDiff(path, ownerSession = '', status = '', repo = '') {
  const item = fileEditorItemFor(path);
  const normalizedStatus = String(status || '').toUpperCase();
  const isAddedChange = normalizedStatus === 'A' || normalizedStatus === 'U' || normalizedStatus === '?';
  const isTouchedOnly = normalizedStatus === 'T';
  const initialMode = isAddedChange || isTouchedOnly ? 'edit' : 'diff';
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
    }, {item, ownerSession});
  } else {
    await openFileInEditor(path, {name: basenameOf(path), session: ownerSession}, {item, ownerSession, viewMode: initialMode});
  }
  const diffReady = await refreshOpenFileDiff(path, {silent: true, ...payloadRepoRefs});
  if (diffReady && !isAddedChange && !isTouchedOnly) setFileEditorViewMode(path, 'diff', item);
  if (!diffReady && (isAddedChange || isTouchedOnly)) setFileEditorViewMode(path, 'edit', item);
  renderOpenFilePath(path);
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
    refreshDiffRefToDatalist(diffRefInput.closest('[data-diff-ref-controls]'));
    showDiffRefPicker(diffRefInput, {showAll: true});
  });
  panel.addEventListener('pointerdown', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-input]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    refreshDiffRefToDatalist(diffRefInput.closest('[data-diff-ref-controls]'));
    showDiffRefPicker(diffRefInput, {showAll: true});
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
      const repo = controls?.dataset?.diffRefRepo || '';
      const path = controls?.dataset?.diffRefPath || '';
      const escRefs = repoDiffRefs(repo);
      diffRefInput.value = diffRefInput.matches('[data-diff-ref-from]')
        ? diffRefInputDisplayValue(escRefs.from, diffRefFromSuggestions(repo, path))
        : diffRefInputDisplayValue(escRefs.to, diffRefToSuggestions(escRefs.from, repo, path));
      diffRefInput.blur?.();
    }
  });
  panel.addEventListener('click', async event => {
    const collapseToggle = event.target.closest('[data-session-files-collapse-toggle]');
    if (collapseToggle && panel.contains(collapseToggle)) {
      event.preventDefault();
      event.stopPropagation();
      toggleAllFileExplorerChanges();
      return;
    }
    const uploadedToggle = event.target.closest('[data-uploaded-files-toggle]');
    if (uploadedToggle && panel.contains(uploadedToggle)) {
      event.preventDefault();
      uploadedFilesCollapsed = !uploadedFilesCollapsed;
      writeStoredUploadedFilesCollapsed();
      renderFileExplorerChangesPanels({force: true});
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
  panel.addEventListener('dblclick', async event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    event.preventDefault();
    const path = fileRow.dataset.openChangeFile;
    if (path) {
      const ownerSession = fileRow.dataset.openChangeSession || '';
      await openChangedFileInDiff(path, ownerSession, fileRow.dataset.openChangeStatus || '', fileRow.dataset.openChangeRepo || '');
    }
  });
  // C5: single-click selects/highlights a Modified-files row (Finder-like), without opening it — the
  // toggle/refresh handler above runs first and returns on its own targets, so this only fires for rows.
  panel.addEventListener('click', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    updateFileTreeSelectionFromClick(fileRow, fileRow.dataset.path || fileRow.dataset.openChangeFile || '', event);
  });
  panel.addEventListener('contextmenu', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (fileRow && panel.contains(fileRow)) {
      event.preventDefault();
      const path = fileRow.dataset.path || fileRow.dataset.openChangeFile || '';
      showFileTreeContextMenu(fileRow, path, changedFileRowEntry(fileRow), event.clientX, event.clientY);
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
    statusErr(`copy failed: ${esc(error)}`);
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
    statusErr(`expand directory failed: ${esc(error)}`);
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
  if (type === 'list') return String(control.value || '').split('\n').map(line => line.trim()).filter(Boolean);
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
    .catch(error => { statusErr(`settings save failed: ${esc(error)}`); refreshSettings({force: true}); });
}

function resetPreference(path) {
  const item = preferenceItemByPath(path);
  if (!item) return;
  saveSettingsPatch(settingPatch(path, preferenceDefault(path)), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => { statusEl.textContent = `reset ${path}`; })
    .catch(error => { statusErr(`settings reset failed: ${esc(error)}`); });
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
    .catch(error => { statusErr(`settings reset failed: ${esc(error)}`); });
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
    .catch(error => { statusErr(`settings save failed: ${esc(error)}`); refreshSettings({force: true}); });
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
            <button type="button" class="file-explorer-header-action file-explorer-mode-files-only" data-file-explorer-collapse title="${esc(t('finder.toolbar.collapseAll'))}" aria-label="${esc(t('finder.toolbar.collapseAll'))}">▤</button>
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
          <button type="button" class="file-editor-diff-expand-panel" title="${esc(t('editor.diffExpand'))}" aria-label="${esc(t('editor.diffExpand'))}" aria-pressed="${diffExpandUnchanged ? 'true' : 'false'}" hidden>↕</button>
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
    toggleFileEditorBlame();  // DOIT.26: inline git blame on/off (persisted, fetches + re-renders editors)
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
    toggleDiffExpandUnchanged();  // B4: show all context vs collapse unchanged runs (persisted, rebuilds the diff)
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
    refreshDiffRefToDatalist(diffRefPanel);
    showDiffRefPicker(input, {showAll: true});
  });
  diffRefPanel?.addEventListener('pointerdown', event => {
    const input = event.target.closest('[data-diff-ref-input]');
    if (!input) return;
    refreshDiffRefToDatalist(diffRefPanel);
    showDiffRefPicker(input, {showAll: true});
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
      const repo = diffRefPanel?.dataset?.diffRefRepoRendered || '';
      const path = diffRefPanel?.dataset?.diffRefPathRendered || '';
      const escRefs = repoDiffRefs(repo);
      input.value = input.matches('[data-diff-ref-from]')
        ? diffRefInputDisplayValue(escRefs.from, diffRefFromSuggestions(repo, path))
        : diffRefInputDisplayValue(escRefs.to, diffRefToSuggestions(escRefs.from, repo, path));
      input.blur?.();
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
  panel?._diffOverviewViewportCleanup?.();
  if (panel) panel._diffOverviewWaitingForDeletedRows = false;
  // Clear the diff scrollbar overview so its red/green rail doesn't linger after switching to
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
    panel._cmEditorOptionCompartment = null;
    panel._cmEditorOptionViews = [];
    panel._cmEditorOptionConfig = null;
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
    expand: options.expand === true,
    language: codeMirrorLanguageName(path),
    readOnly: readOnlyMode,
    // DOIT.26 fix: the blame ViewPlugin is added/removed only at editor build time, so blame state must
    // be in the signature — otherwise toggling blame OFF reuses the existing view and the annotations
    // linger (and toggling ON wouldn't add them without an unrelated rebuild).
    blame: fileEditorBlameEnabled,
    blameAllLines: fileEditorBlameAllLines,
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
  return [
    api.drawSelection(),
    api.highlightActiveLine(),
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    api.EditorState.readOnly.of(true),
    api.EditorView.editable.of(false),
    ...(options.plain ? [codeMirrorThemeOnlyExtensions(api, panel)] : [codeMirrorLanguageExtension(api, path), codeMirrorThemedExtensions(api, panel, path)]),
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
    codeMirrorMarkdownFallbackSyntaxExtension(api, path),
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
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    safeCodeMirrorExtension('search', () => api.search({top: true})),
    codeMirrorSearchPanelEnhancementExtension(api),
    safeCodeMirrorExtension('search matches', () => api.highlightSelectionMatches?.()),
    saveKeymap,
    findKeymap,
    defaultKeymap,
    safeCodeMirrorExtension('read only', () => api.EditorState.readOnly.of(readOnlyMode)),
    safeCodeMirrorExtension('editable', () => api.EditorView.editable.of(!readOnlyMode)),
    codeMirrorThemedExtensions(api, panel, path),
    codeMirrorWorkingUpdateExtension(api, panel, path),
  ];
}

function codeMirrorEditorOptionExtensions(api, options = {}) {
  const extensions = [];
  if (options.lineNumbers !== false && fileEditorLineNumbersEnabled) {
    const lineNumbers = safeCodeMirrorExtension('line numbers', () => api.lineNumbers?.());
    const activeLineGutter = safeCodeMirrorExtension('active line gutter', () => api.highlightActiveLineGutter?.());
    extensions.push(...[lineNumbers, activeLineGutter].flat().filter(Boolean));
  }
  if (options.wrap !== false && fileEditorWrapEnabled) {
    extensions.push(...[codeMirrorLineWrappingExtension(api), codeMirrorWrapMarkerExtension(api)].flat().filter(Boolean));
  }
  return extensions;
}

function codeMirrorLineWrappingExtension(api) {
  if (api.EditorView?.lineWrapping) return api.EditorView.lineWrapping;
  return [
    safeCodeMirrorExtension('line wrapping content attributes', () => api.EditorView?.contentAttributes?.of?.({class: 'cm-lineWrapping'})),
    safeCodeMirrorExtension('line wrapping theme', () => api.EditorView?.theme?.({
      '.cm-content.cm-lineWrapping': {
        whiteSpace: 'break-spaces',
        wordBreak: 'break-word',
        overflowWrap: 'anywhere',
        flexShrink: '1',
      },
    })),
  ].flat().filter(Boolean);
}

function codeMirrorEditorOptionCompartmentExtensions(api, panel, options = {}) {
  const extensions = codeMirrorEditorOptionExtensions(api, options);
  if (!panel || !api.Compartment) return extensions;
  panel._cmEditorOptionCompartment = panel._cmEditorOptionCompartment || new api.Compartment();
  panel._cmEditorOptionConfig = {
    wrap: options.wrap !== false,
    lineNumbers: options.lineNumbers !== false,
  };
  return panel._cmEditorOptionCompartment.of(extensions);
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
  const liveViews = views.filter(Boolean);
  panel._cmThemeViews = liveViews;
  panel._cmEditorOptionViews = liveViews;
}

function reconfigureCodeMirrorPanelTheme(panel) {
  const api = panel?._cmApi;
  const path = panel?._cmPath;
  const compartment = panel?._cmThemeCompartment;
  const views = Array.isArray(panel?._cmThemeViews) ? panel._cmThemeViews : [];
  if (!api || !path || !compartment || !views.length) return false;
  const extensions = codeMirrorThemeExtensions(api, path);
  const effect = compartment.reconfigure(extensions);
  for (const view of views) {
    try { view.dispatch({effects: effect}); } catch (_) {}
  }
  return true;
}

function reconfigureCodeMirrorPanelEditorOptions(panel) {
  const api = panel?._cmApi;
  const compartment = panel?._cmEditorOptionCompartment;
  const views = Array.isArray(panel?._cmEditorOptionViews) ? panel._cmEditorOptionViews : [];
  if (!api || !compartment || !views.length) return false;
  const effect = compartment.reconfigure(codeMirrorEditorOptionExtensions(api, panel._cmEditorOptionConfig || {}));
  for (const view of views) {
    try { view.dispatch({effects: effect}); } catch (_) {}
  }
  updateCodeMirrorCursorStatus(panel);
  if (panel?._cmMode === 'diff') scheduleDiffOverviewRebuild(panel);
  return true;
}

function diffOverviewPercent(lineIndex, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const value = Math.max(0, Math.min(100, (Number(lineIndex || 0) / total) * 100));
  return value.toFixed(3);
}

function diffOverviewRemovedLineCount(diff) {
  let count = 0;
  let hasHunk = false;
  for (const line of String(diff || '').split('\n')) {
    if (/^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@/.test(line)) {
      hasHunk = true;
      continue;
    }
    if (!hasHunk || line.startsWith('+++') || line.startsWith('---') || line.startsWith('\\')) continue;
    if (line.startsWith('-')) count += 1;
  }
  return count;
}

function diffOverviewLineStarts(text) {
  const value = String(text ?? '');
  const starts = [0];
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) === 10) starts.push(index + 1);
  }
  return starts;
}

function diffOverviewLineNumberAt(starts, position) {
  const lineStarts = Array.isArray(starts) && starts.length ? starts : [0];
  const pos = Math.max(0, Number(position || 0));
  let lo = 0;
  let hi = lineStarts.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lineStarts[mid] <= pos) lo = mid + 1;
    else hi = mid - 1;
  }
  return Math.max(1, hi + 1);
}

function diffOverviewLineCountForRange(starts, from, end) {
  const start = Number(from);
  const finish = Number(end);
  if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return 0;
  return Math.max(0, diffOverviewLineNumberAt(starts, finish) - diffOverviewLineNumberAt(starts, start) + 1);
}

function diffOverviewChunkEnd(chunk, side) {
  const explicit = Number(chunk?.[`end${side}`]);
  if (Number.isFinite(explicit)) return explicit;
  const from = Number(chunk?.[`from${side}`]);
  const to = Number(chunk?.[`to${side}`]);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return from;
  return Math.max(from, to - 1);
}

function diffOverviewIsCodeMirrorChunk(chunk) {
  return chunk
    && Number.isFinite(Number(chunk.fromA))
    && Number.isFinite(Number(chunk.toA))
    && Number.isFinite(Number(chunk.fromB))
    && Number.isFinite(Number(chunk.toB));
}

function diffOverviewCodeMirrorChunks(view, panel = null) {
  const mergeChunks = panel?._cmMergeView?.chunks;
  if (Array.isArray(mergeChunks) && mergeChunks.length && mergeChunks.every(diffOverviewIsCodeMirrorChunk)) {
    return mergeChunks;
  }
  const values = view?.state?.values;
  if (!Array.isArray(values)) return null;
  for (const value of values) {
    if (Array.isArray(value) && value.length && value.every(diffOverviewIsCodeMirrorChunk)) return value;
  }
  return null;
}

function diffOverviewSortedCodeMirrorChunks(chunks) {
  return Array.isArray(chunks)
    ? chunks.filter(diffOverviewIsCodeMirrorChunk).sort((a, b) => Number(a.fromB) - Number(b.fromB) || Number(a.fromA) - Number(b.fromA))
    : [];
}

function mergeDiffOverviewBand(bands, kind, start, end) {
  if (end <= start) return;
  const last = bands[bands.length - 1];
  if (last && last.kind === kind && last.end === start) {
    last.end = end;
  } else {
    bands.push({kind, start, end});
  }
}

function diffOverviewLineHeight(view, container) {
  const measured = Number(view?.defaultLineHeight || 0);
  if (Number.isFinite(measured) && measured > 0) return measured;
  const content = view?.contentDOM || container?.querySelector?.('.cm-content');
  if (content && typeof getComputedStyle === 'function') {
    const lineHeight = Number.parseFloat(getComputedStyle(content).lineHeight || '');
    if (Number.isFinite(lineHeight) && lineHeight > 0) return lineHeight;
  }
  return 20;
}

function diffOverviewPrefixWeights(weights) {
  const prefix = [0];
  for (let index = 1; index < weights.length; index += 1) {
    prefix[index] = prefix[index - 1] + Math.max(1, Number(weights[index] || 1));
  }
  return prefix;
}

function diffOverviewRangeWeight(prefixWeights, startLine, endLine) {
  const lastLine = Math.max(0, prefixWeights.length - 1);
  const start = Math.max(1, Math.min(lastLine, Number(startLine || 1)));
  const end = Math.max(0, Math.min(lastLine, Number(endLine || 0)));
  if (end < start) return 0;
  return prefixWeights[end] - prefixWeights[start - 1];
}

function diffOverviewLineModel(text, view = null, container = null, lineCountOverride = null) {
  const value = String(text ?? '');
  const starts = diffOverviewLineStarts(value);
  const lines = value.split('\n');
  const lineCount = Math.max(1, Number(lineCountOverride || starts.length || lines.length || 1));
  const weights = [0];
  for (let lineNumber = 1; lineNumber <= lineCount; lineNumber += 1) {
    weights.push(diffOverviewEstimatedTextLineWeight(lines[lineNumber - 1] || '', view, container));
  }
  const prefixWeights = diffOverviewPrefixWeights(weights);
  return {
    starts,
    lineCount,
    prefixWeights,
    rows: prefixWeights[lineCount] || lineCount,
  };
}

function diffOverviewLineModelRangeWeight(model, startLine, endLine) {
  return model ? diffOverviewRangeWeight(model.prefixWeights, startLine, endLine) : 0;
}

function diffOverviewWrappingEnabled(view, container) {
  return view?.contentDOM?.classList?.contains?.('cm-lineWrapping')
    || Boolean(container?.querySelector?.('.cm-content.cm-lineWrapping'));
}

function diffOverviewContentWidth(view, container) {
  const contentWidth = Number(view?.contentDOM?.getBoundingClientRect?.().width || view?.contentDOM?.clientWidth || 0);
  if (Number.isFinite(contentWidth) && contentWidth > 0) return contentWidth;
  const content = container?.querySelector?.('.cm-content');
  const fallbackWidth = Number(content?.getBoundingClientRect?.().width || content?.clientWidth || 0);
  if (Number.isFinite(fallbackWidth) && fallbackWidth > 0) return fallbackWidth;
  const scrollerWidth = Number(view?.scrollDOM?.clientWidth || container?.querySelector?.('.cm-scroller')?.clientWidth || 0);
  return Number.isFinite(scrollerWidth) && scrollerWidth > 0 ? scrollerWidth : 1;
}

function diffOverviewCharacterWidth(view) {
  const measured = Number(view?.defaultCharacterWidth || 0);
  return Number.isFinite(measured) && measured > 0 ? measured : 8;
}

function diffOverviewEstimatedTextLineWeight(text, view, container) {
  if (!diffOverviewWrappingEnabled(view, container)) return 1;
  const width = Math.max(1, diffOverviewContentWidth(view, container));
  const charWidth = Math.max(1, diffOverviewCharacterWidth(view));
  const columns = Math.max(1, Math.floor(width / charWidth));
  const visualColumns = Math.max(1, String(text ?? '').replace(/\t/g, '    ').length);
  return Math.max(1, Math.ceil(visualColumns / columns));
}

function diffOverviewRowsFromCodeMirrorLineModels(chunks, currentModel, originalModel, options = {}) {
  const validChunks = diffOverviewSortedCodeMirrorChunks(chunks);
  if (!validChunks.length || !currentModel) return null;
  const includeRemoved = options.includeRemoved !== false;
  const bands = [];
  let row = 0;
  let currentLine = 1;
  let deletedRows = 0;

  for (const chunk of validChunks) {
    const fromA = Number(chunk.fromA);
    const toA = Number(chunk.toA);
    const fromB = Number(chunk.fromB);
    const toB = Number(chunk.toB);
    const startCurrentLine = Math.min(currentModel.lineCount, Math.max(1, diffOverviewLineNumberAt(currentModel.starts, fromB)));
    if (startCurrentLine > currentLine) {
      row += diffOverviewLineModelRangeWeight(currentModel, currentLine, startCurrentLine - 1);
      currentLine = startCurrentLine;
    }

    if (includeRemoved && originalModel && toA > fromA) {
      const removedCount = diffOverviewLineCountForRange(originalModel.starts, fromA, diffOverviewChunkEnd(chunk, 'A'));
      const removedStartLine = diffOverviewLineNumberAt(originalModel.starts, fromA);
      const removedWeight = diffOverviewLineModelRangeWeight(originalModel, removedStartLine, removedStartLine + removedCount - 1);
      mergeDiffOverviewBand(bands, 'remove', row, row + removedWeight);
      row += removedWeight;
      deletedRows += removedWeight;
    }

    if (toB > fromB) {
      const insertedEnd = diffOverviewChunkEnd(chunk, 'B');
      const insertedEndLine = Math.min(currentModel.lineCount, Math.max(startCurrentLine, diffOverviewLineNumberAt(currentModel.starts, insertedEnd)));
      const insertedWeight = diffOverviewLineModelRangeWeight(currentModel, startCurrentLine, insertedEndLine);
      mergeDiffOverviewBand(bands, 'add', row, row + insertedWeight);
      row += insertedWeight;
      currentLine = Math.max(currentLine, insertedEndLine + 1);
    }
  }

  if (currentLine <= currentModel.lineCount) row += diffOverviewLineModelRangeWeight(currentModel, currentLine, currentModel.lineCount);
  return {
    bands,
    currentLineCount: currentModel.lineCount,
    currentRows: currentModel.rows,
    deletedRows,
    totalRows: Math.max(row, currentModel.rows + deletedRows, 1),
  };
}

function diffOverviewRowsFromCodeMirrorChunks(chunks, currentText, originalText) {
  return diffOverviewRowsFromCodeMirrorLineModels(
    chunks,
    diffOverviewLineModel(currentText),
    diffOverviewLineModel(originalText),
  );
}

function diffOverviewRowsFromCodeMirrorRenderedWeights(view, chunks, currentText, originalText, container = null) {
  const doc = view?.state?.doc;
  const currentLineCount = Math.max(1, Number(doc?.lines || diffOverviewLineStarts(currentText).length || 1));
  const rows = diffOverviewRowsFromCodeMirrorLineModels(
    chunks,
    diffOverviewLineModel(currentText, view, container, currentLineCount),
    diffOverviewLineModel(originalText, view, container),
  );
  if (rows) rows.renderedWeights = true;
  return rows;
}

function diffOverviewScrollLooksCurrentOnly(rows, scrollTarget, view, container) {
  const deletedRows = Number(rows?.deletedRows || 0);
  if (deletedRows <= 0 || !scrollTarget) return false;
  const totalRows = Number(rows?.totalRows || 0);
  const currentRows = Number(rows?.currentRows || rows?.currentLineCount || 0);
  if (!Number.isFinite(totalRows) || !Number.isFinite(currentRows) || totalRows <= currentRows) return false;
  const scrollHeight = Number(scrollTarget.scrollHeight || 0);
  if (!Number.isFinite(scrollHeight) || scrollHeight <= 0) return false;
  const scrollRows = scrollHeight / diffOverviewLineHeight(view, container);
  const threshold = currentRows + (totalRows - currentRows) * 0.5;
  return scrollRows < threshold;
}

function diffOverviewBandsFromUnifiedDiff(diff, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const bands = [];
  let lineIndex = 0;
  let newLine = 1;
  let hasHunk = false;
  const appendLine = kind => {
    const start = Math.max(0, Math.min(total, lineIndex));
    const end = Math.max(start, Math.min(total, lineIndex + 1));
    if (end <= start) return;
    mergeDiffOverviewBand(bands, kind, start, end);
  };
  for (const line of String(diff || '').split('\n')) {
    const hunk = /^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
    if (hunk) {
      const nextNewLine = Math.max(1, Number(hunk[1]) || 1);
      lineIndex += Math.max(0, nextNewLine - newLine);
      newLine = nextNewLine;
      hasHunk = true;
      continue;
    }
    if (!hasHunk || line.startsWith('+++') || line.startsWith('---') || line.startsWith('\\')) continue;
    if (line.startsWith('+')) {
      appendLine('add');
      newLine += 1;
      lineIndex += 1;
    } else if (line.startsWith('-')) {
      appendLine('remove');
      lineIndex += 1;
    } else {
      newLine += 1;
      lineIndex += 1;
    }
  }
  return bands;
}

function buildDiffOverviewGradientFromBands(bands, totalLines) {
  const total = Math.max(1, Number(totalLines || 0));
  const stops = [];
  let lastStop = 0;
  const appendTransparent = until => {
    if (until > lastStop) stops.push(`transparent ${diffOverviewPercent(lastStop, total)}% ${diffOverviewPercent(until, total)}%`);
  };
  for (const band of Array.isArray(bands) ? bands : []) {
    const start = Math.max(0, Math.min(total, Number(band?.start || 0)));
    const end = Math.max(start, Math.min(total, Number(band?.end || 0)));
    if (end <= start) continue;
    appendTransparent(start);
    const color = band.kind === 'add' ? '#38d878' : '#ff5d6c';
    stops.push(`${color} ${diffOverviewPercent(start, total)}% ${diffOverviewPercent(end, total)}%`);
    lastStop = end;
  }
  if (!stops.some(stop => !stop.startsWith('transparent'))) return null;
  appendTransparent(total);
  return `linear-gradient(to bottom, ${stops.join(', ')})`;
}

function buildDiffOverviewGradient(diff, totalLines) {
  return buildDiffOverviewGradientFromBands(diffOverviewBandsFromUnifiedDiff(diff, totalLines), totalLines);
}

function codeMirrorDiffOverviewScrollTarget(view, container) {
  return view?.scrollDOM || container?.querySelector?.('.cm-scroller') || container?.querySelector?.('.cm-mergeView') || null;
}

function updateCodeMirrorDiffOverviewGeometry(overview, scrollTarget) {
  if (!overview || !scrollTarget?.getBoundingClientRect || !overview.parentElement?.getBoundingClientRect) return;
  const containerRect = overview.parentElement.getBoundingClientRect();
  const scrollRect = scrollTarget.getBoundingClientRect();
  const top = Math.max(0, scrollRect.top - containerRect.top);
  const height = Math.max(1, Number(scrollTarget.clientHeight || 0));
  overview.style.top = `${top}px`;
  overview.style.bottom = 'auto';
  overview.style.height = `${height}px`;
}

function updateCodeMirrorDiffOverviewViewport(viewport, scrollTarget) {
  if (!viewport || !scrollTarget) return;
  updateCodeMirrorDiffOverviewGeometry(viewport.parentElement, scrollTarget);
  const scrollHeight = Math.max(1, Number(scrollTarget.scrollHeight || 0));
  const clientHeight = Math.max(1, Number(scrollTarget.clientHeight || 0));
  const scrollTop = Math.max(0, Number(scrollTarget.scrollTop || 0));
  const top = Math.max(0, Math.min(100, (scrollTop / scrollHeight) * 100));
  const height = Math.max(2, Math.min(100 - top, (clientHeight / scrollHeight) * 100));
  viewport.style.top = `${top}%`;
  viewport.style.height = `${height}%`;
}

function installCodeMirrorDiffOverviewViewport(panel, overview, scrollTarget) {
  panel?._diffOverviewViewportCleanup?.();
  if (!overview || !scrollTarget) return;
  const viewport = document.createElement('div');
  viewport.className = 'cm-diff-overview-viewport';
  overview.appendChild(viewport);
  const update = () => updateCodeMirrorDiffOverviewViewport(viewport, scrollTarget);
  update();
  scrollTarget.addEventListener?.('scroll', update, {passive: true});
  if (panel) {
    panel._diffOverviewViewportCleanup = () => {
      scrollTarget.removeEventListener?.('scroll', update);
      if (viewport.parentElement) viewport.remove();
      panel._diffOverviewViewportCleanup = null;
    };
  }
}

function updateCodeMirrorDiffOverview(panel, container, state, currentText, original) {
  // Remember the inputs so a fold expand/collapse can rebuild the viewport indicator against the
  // current scroll surface; the red/green rows themselves are a single linear-gradient.
  if (panel) panel._diffOverviewCtx = {container, state, currentText, original};
  container?.querySelector?.('.cm-diff-overview')?.remove();
  panel?._diffOverviewViewportCleanup?.();
  if (!diffExpandUnchanged) return;
  const view = panel?._cmView;
  const scrollTarget = codeMirrorDiffOverviewScrollTarget(view, container);
  const currentLineCount = Math.max(String(currentText || '').split('\n').length, 1);
  const chunks = diffOverviewCodeMirrorChunks(view, panel);
  let chunkRows = diffOverviewRowsFromCodeMirrorRenderedWeights(view, chunks, currentText, original, container)
    || diffOverviewRowsFromCodeMirrorChunks(chunks, currentText, original);
  if (!chunkRows && view) {
    scheduleDiffOverviewReadinessRebuild(panel);
    return;
  }
  if (diffOverviewScrollLooksCurrentOnly(chunkRows, scrollTarget, view, container)) {
    if (panel && !panel._diffOverviewWaitingForDeletedRows) {
      panel._diffOverviewWaitingForDeletedRows = true;
      scheduleDiffOverviewSettledRebuild(panel);
    }
    return;
  }
  if (panel) panel._diffOverviewWaitingForDeletedRows = false;
  if (panel) panel._diffOverviewReadinessRetries = 0;
  const fallbackRows = {
    bands: diffOverviewBandsFromUnifiedDiff(state?.diff || '', currentLineCount + diffOverviewRemovedLineCount(state?.diff || '')),
    totalRows: Math.max(currentLineCount + diffOverviewRemovedLineCount(state?.diff || ''), 1),
  };
  const rows = chunkRows || fallbackRows;
  const gradient = buildDiffOverviewGradientFromBands(rows.bands, rows.totalRows);
  if (!gradient || !container) return;
  const overview = document.createElement('div');
  overview.className = 'cm-diff-overview';
  overview.setAttribute('aria-hidden', 'true');
  overview.style.background = gradient;
  container.appendChild(overview);
  installCodeMirrorDiffOverviewViewport(panel, overview, scrollTarget);
}

function scheduleDiffOverviewReadinessRebuild(panel) {
  if (!panel || panel._diffOverviewReadinessQueued) return;
  const attempts = Number(panel._diffOverviewReadinessRetries || 0);
  if (attempts >= 6) return;
  panel._diffOverviewReadinessRetries = attempts + 1;
  panel._diffOverviewReadinessQueued = true;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      panel._diffOverviewReadinessQueued = false;
      scheduleDiffOverviewRebuild(panel);
    });
  });
}

function scheduleDiffOverviewSettledRebuild(panel) {
  if (!panel || panel._diffOverviewSettledQueued) return;
  panel._diffOverviewSettledQueued = true;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      panel._diffOverviewSettledQueued = false;
      scheduleDiffOverviewRebuild(panel);
    });
  });
}

// B3: rebuild the diff overview whenever the editor's rendered geometry changes (a fold expanding/
// collapsing fires heightChanged). Debounced via rAF.
function scheduleDiffOverviewRebuild(panel) {
  const ctx = panel?._diffOverviewCtx;
  if (!ctx || panel._diffOverviewRebuildQueued) return;
  panel._diffOverviewRebuildQueued = true;
  requestAnimationFrame(() => {
    panel._diffOverviewRebuildQueued = false;
    if (panel._diffOverviewCtx && panel._cmMode === 'diff') {
      const c = panel._diffOverviewCtx;
      updateCodeMirrorDiffOverview(panel, c.container, c.state, c.currentText, c.original);
    }
  });
}

function codeMirrorDiffOverviewListener(api, panel) {
  return api.EditorView.updateListener.of(update => {
    if (update.geometryChanged || update.heightChanged) scheduleDiffOverviewRebuild(panel);
  });
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
  // Await the deduped diff load in this generation. Falling back to a temporary edit view here creates
  // a second render path that can overwrite the diff view after the payload arrives.
  if (state.diffLoading && state._diffLoadingPromise) {
    setFileEditorPanelStatus(panel, t('editor.diffLoading'), '');
    await state._diffLoadingPromise;
    if (panel._cmGeneration !== generation) return null;
  } else if (!state.diffLoaded && !state.diffUnavailable) {
    setFileEditorPanelStatus(panel, t('editor.diffLoading'), '');
    await refreshOpenFileDiff(path, {silent: true, renderOnComplete: false});
    if (panel._cmGeneration !== generation) return null;
  }
  if (!openFileDiffAvailable(state)) {
    if (state.diffUnavailable) {
      const msg = `diff unavailable: ${state.diffError || 'unknown error'}`;
      setFileEditorPanelStatus(panel, msg, 'warn');
      return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
    }
    return ensureCodeMirrorPanel(panel, item, path, state, {forceMode: 'edit'});
  }
  const original = String(state.diffOriginal || '');
  try {
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
    const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef, expand: diffExpandUnchanged});
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
      scheduleDiffOverviewSettledRebuild(panel);
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
          extensions: [
            ...(diffEditsAllowed
              ? [
                  ...codeMirrorExtensions(api, panel, path),
                  codeMirrorWorkingUpdateExtension(api, panel, path),
                ]
              : codeMirrorReadOnlyExtensions(api, path, panel)),
            // B3: panel._cmView is this `b` editor (side-by-side), so the overview rebuild listener lives here.
            codeMirrorDiffOverviewListener(api, panel),
          ],
        },
        parent: container,
        revertControls: 'a-to-b',
        // DOIT.6 #44: show each change as TWO uniform full lines (old solid red + new solid green) with
        // NO intra-line word/token highlight — even a 1-char edit shows whole-line red + whole-line green.
        highlightChanges: false,
        gutter: true,
        // B4: expand-all omits collapseUnchanged so every unchanged line shows; else collapse the runs.
        ...(diffExpandUnchanged ? {} : {collapseUnchanged: {margin: 3, minSize: 8}}),
      });
      panel._cmView = panel._cmMergeView.b;
      trackCodeMirrorThemeViews(panel, api, [panel._cmMergeView.a, panel._cmMergeView.b]);
      panel._cmMergeView.b.scrollDOM?.addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'editor'));
    } else {
      const unifiedMergeOptions = {
        original,
        // DOIT.6 #44: full-line red/green only, no intra-line token highlight (see MergeView above).
        highlightChanges: false,
        gutter: true,
        mergeControls: !readOnlyMode && diffEditsAllowed,
        // B4: expand-all omits collapseUnchanged so every unchanged line shows; else collapse the runs.
        ...(diffExpandUnchanged ? {} : {collapseUnchanged: {margin: 3, minSize: 8}}),
      };
      const unifiedDiffExtensions = (plain = false) => [
        api.unifiedMergeView(unifiedMergeOptions),
        ...(diffEditsAllowed ? codeMirrorExtensions(api, panel, path, {plain}) : codeMirrorReadOnlyExtensions(api, path, panel, {plain})),
        codeMirrorDiffOverviewListener(api, panel),  // B3: rebuild overview on fold/geometry change
      ];
      let cmState;
      try {
        cmState = api.EditorState.create({
          doc: currentText,
          extensions: unifiedDiffExtensions(false),
        });
        panel._cmPlainFallback = false;
      } catch (error) {
        console.warn('CodeMirror diff language parser failed; retrying plain diff editor', error);
        panel._cmThemeCompartment = null;
        panel._cmEditorOptionCompartment = null;
        cmState = api.EditorState.create({
          doc: currentText,
          extensions: unifiedDiffExtensions(true),
        });
        panel._cmPlainFallback = true;
      }
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
      panel._cmView.scrollDOM?.addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'editor'));
      trackCodeMirrorThemeViews(panel, api, [panel._cmView]);
    }
    panel._cmPath = path;
    panel._cmSignature = signature;
    panel._cmMode = 'diff';
    updateCodeMirrorDiffOverview(panel, container, state, currentText, original);
    scheduleDiffOverviewSettledRebuild(panel);
    restoreFileEditorPanelViewState(item, panel);
    updateCodeMirrorCursorStatus(panel);
    focusFileEditorPanelIfReady(panel, item);
    return true;
  } catch (error) {
    if (panel._cmGeneration !== generation) return null;
    console.warn('CodeMirror diff editor unavailable; showing read-only raw text', error);
    destroyCodeMirrorPanel(panel);
    container.hidden = true;
    setFileEditorPanelStatus(panel, `CodeMirror diff unavailable; showing read-only raw text (${error})`, 'error');
    return false;
  }
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
      panel._cmMode = 'edit';
      panel._cmPlainFallback = Boolean(createdState.plain);
      panel._cmView.scrollDOM?.addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'editor'));
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
    // DOIT.34 #3: if blame is on but this path isn't cached yet (file opened after the toggle), fetch it
    // and nudge the editor so the annotation appears without a manual toggle. Deduped; no-op otherwise.
    ensureEditorBlameForPath(path);
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

// DOIT.45: should an in-diff-mode editor fall back to edit because the loaded diff has nothing to show?
// YES only when the file has NO useful git history — those files genuinely can't be diffed. A file WITH
// useful history (e.g. README.md with many commits but a clean working tree → an empty HEAD-vs-working
// diff) must STAY in diff mode so the FROM/TO sha picker stays reachable and the user can compare
// ARBITRARY refs. Force-exiting on an empty default diff (the old behavior) hid the picker entirely on
// clean files — the recurring "press DIFF, no FROM/TO menu" bug.
function diffModeShouldFallBackToEdit(path, state, item = null) {
  return state?.kind === 'text'
    && editorViewModeFor(path, item) === 'diff'
    && (!fileStateHasRepo(path, state)
      || (state.diffLoaded === true
        && !state.diffLoading
        && !openFileDiffAvailable(state)
        && !fileStateHasUsefulGitHistory(state)));
}

function renderFileEditorPanel(panel, item) {
  const path = fileItemPath(item);
  captureFileEditorPanelViewState(item, panel);
  const previousActiveFile = activeFile;
  activeFile = path;
  if (previousActiveFile !== path) scheduleFileExplorerActiveFileReveal(path);
  else updateFileExplorerCurrentFileHighlight();
  const state = openFiles.get(path);
  updateFileEditorPanelChrome(panel, path);
  const codeMirrorPane = panel.querySelector('.file-editor-codemirror-panel');
  const rawPane = panel.querySelector('.file-editor-raw-panel');
  const previewPane = panel.querySelector('.file-editor-preview-pane-panel');
  const imagePane = panel.querySelector('.file-editor-image-panel');
  const modeControl = panel.querySelector('.file-editor-mode-control-panel');
  const previewFontPanel = panel.querySelector('.file-editor-preview-font-panel');
  const gutterButton = panel.querySelector('.file-editor-gutter-panel');
  const wrapButton = panel.querySelector('.file-editor-wrap-panel');
  const findButton = panel.querySelector('.file-editor-find-panel');
  const diffButton = panel.querySelector('.file-editor-diff-panel');
  const diffRefPanel = panel.querySelector('.file-editor-diff-ref-panel');
  const diffExpandButton = panel.querySelector('.file-editor-diff-expand-panel');
  const popoutPreviewButton = panel.querySelector('.file-editor-popout-preview-panel');
  const reloadButton = panel.querySelector('.file-editor-reload-panel');
  const themeButton = panel.querySelector('.file-editor-theme-panel');
  const blameButton = panel.querySelector('.file-editor-blame-panel');
  const saveButton = panel.querySelector('.file-editor-save-panel');
  const content = panel.querySelector('.file-editor-content');
  const textControls = [modeControl, previewFontPanel, gutterButton, wrapButton, findButton, blameButton, diffButton, diffExpandButton, diffRefPanel, popoutPreviewButton, reloadButton];
  let mode = editorViewModeFor(path, item);
  updateEditorThemeButton(themeButton, {includeVanilla: true});
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
  // DOIT.6 #149: do NOT auto-load the diff when a file opens/renders. The diff loads only on explicit
  // diff-mode entry (the Diff button + the Modified-files menu both open in diff view and load there),
  // so opening/editing a file does zero diff work (one fewer network round-trip + re-render; ties to DOIT.9).
  if (diffModeShouldFallBackToEdit(path, state, item)) {
    setFileEditorViewMode(path, 'edit', item);
  }
  mode = editorViewModeFor(path, item);
  updateEditorThemeButton(themeButton, {includeVanilla: true});
  updateEditorModeControl(modeControl, path, state, item);
  if (previewFontPanel) {
    previewFontPanel.hidden = state.kind !== 'text' || !editorPreviewModeAvailable(path) || (mode !== 'preview' && mode !== 'split');
    updateEditorPreviewFontControls(previewFontPanel);
  }
  if (gutterButton) {
    gutterButton.hidden = state.kind !== 'text' || mode === 'preview';
    updateEditorGutterButton(gutterButton);
  }
  if (wrapButton) {
    wrapButton.hidden = state.kind !== 'text' || mode === 'preview';
    updateEditorWrapButton(wrapButton);
  }
  updateEditorFindButton(findButton, state, panel);
  if (findButton && mode === 'preview') findButton.hidden = true;
  // Git-backed controls share file-history gating, but Diff also depends on the loaded diff state while
  // Blame stays available in normal edit mode for clean files with useful history.
  updateFileEditorBlameButton(blameButton, path, state, item);
  updateFileEditorDiffButton(diffButton, path, state, item);
  updateFileEditorDiffExpandButton(diffExpandButton, path, state, item);
  if (popoutPreviewButton) {
    popoutPreviewButton.hidden = state.kind !== 'text' || !editorPreviewModeAvailable(path);
  }
  if (diffRefPanel) {
    diffRefPanel.hidden = mode !== 'diff' || state.kind !== 'text';
    // C6: scope the editor's own FROM/TO controls to THIS file's repo, so they match the repo header and
    // drive the file's diff. Re-render only when the repo actually changed and the picker isn't focused.
    const diffRepo = fileRepoForPath(path);
    const historySignature = fileDiffRefHistorySignature(path);
    if (!diffRefPanel.hidden
      && (diffRefPanel.dataset.diffRefRepoRendered !== diffRepo
        || diffRefPanel.dataset.diffRefPathRendered !== path
        || diffRefPanel.dataset.diffRefHistoryRendered !== historySignature)
      && !diffRefPanel.contains(document.activeElement)) {
      diffRefPanel.innerHTML = diffRefControlsHtml({compact: true, repo: diffRepo, path});
      diffRefPanel.dataset.diffRefRepoRendered = diffRepo;
      diffRefPanel.dataset.diffRefPathRendered = path;
      diffRefPanel.dataset.diffRefHistoryRendered = historySignature;
    }
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
    }).catch(error => {
      if (panel.dataset.filePath !== path) return;
      console.warn('CodeMirror editor unavailable; showing read-only raw text', error);
      destroyCodeMirrorPanel(panel);
      if (codeMirrorPane) codeMirrorPane.hidden = true;
      setFileEditorPanelStatus(panel, `CodeMirror unavailable; showing read-only raw text (${error})`, 'error');
      renderFileEditorRawPane(rawPane, path, state.content);
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
        else setFileState(path, fileErrorState(fetched.error || 'failed to inspect image'));
        renderSessionButtons();
        renderPaneTabStrips();
        return;
      }
      if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
        const state = tooLargeFileState(Number(entry.size));
        state.mtime = fileEntryMtime(entry);
        setFileState(path, state);
      } else {
        setFileState(path, {mtime: fileEntryMtime(entry), kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null});
      }
      if (panel) renderFileEditorPanel(panel, item);
      renderSessionButtons();
      renderPaneTabStrips();
      return;
    }
    try {
      const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
      setFileState(path, applyFileGitMetadata({
        mtime: filePayloadMtime(payload),
        size: payload.size,
        kind: 'text',
        original: payload.content,
        content: payload.content,
        dirty: false,
      }, payload));
    } catch (err) {
      const status = Number(err?.status) || 0;
      if (status) {
        const message = String(err?.payload?.error || status);
        setFileState(path, status === 413
          ? tooLargeFileState(null, message)
          : status === 404
            ? missingFileState(message)
            : fileErrorState(message));
      } else {
        setFileState(path, fileErrorState(err));
      }
    }
    if (panel) renderFileEditorPanel(panel, item);
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
  const previewOnly = false;
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

// DOIT.21 — back/forward navigation history. The stack holds the layout ITEM ids of visited tabs (any
// kind: file editors/previews, terminals, Finder, Prefs, …), so Back returns to the previous tab worked
// on — not just files. recordEditorNav pushes a user-initiated activation; back/forward re-activate the
// item (re-opening a since-closed file from its path-encoded id). Bounded so the history can't grow
// without limit — the oldest entries drop past the cap.
const NAV_STACK_LIMIT = 50;
function recordEditorNav(item) {
  if (editorNav.navigating || !item) return;
  if (editorNav.stack[editorNav.index] === item) return;   // dedupe consecutive same-tab activations
  editorNav.stack = editorNav.stack.slice(0, editorNav.index + 1);   // a new activation after Back drops the forward tail
  editorNav.stack.push(item);
  collapseEditorNavPingPong();
  if (editorNav.stack.length > NAV_STACK_LIMIT) {
    editorNav.stack = editorNav.stack.slice(editorNav.stack.length - NAV_STACK_LIMIT);
  }
  editorNav.index = editorNav.stack.length - 1;
  updateEditorNavButtons();
}

function collapseEditorNavPingPong() {
  while (editorNav.stack.length >= 4) {
    const end = editorNav.stack.length;
    const first = editorNav.stack[end - 4];
    const second = editorNav.stack[end - 3];
    if (first !== editorNav.stack[end - 2] || second !== editorNav.stack[end - 1]) return;
    editorNav.stack.splice(end - 2, 2);
  }
}

// Re-activate a history item: focus its tab if still open; if it's a closed file editor/preview, re-open
// it from the path encoded in its id. Returns false when the item is gone and can't be restored (a
// closed terminal/Finder/etc.) so the caller can skip it.
async function activateNavItem(item) {
  const side = slotForItem(item);
  if (side) {
    activatePaneTab(side, item);   // userInitiated defaults falsey → does not re-record
    return true;
  }
  if (isFileEditorItem(item)) {
    const path = fileItemPath(item);
    if (path) {
      await openFileInEditor(path, basenameOf(path), {item});
      return true;
    }
  }
  return false;
}

async function editorNavGo(delta) {
  // Walk in `delta` direction, skipping entries that can't be re-activated (closed non-file tabs), so a
  // stale entry never dead-ends the history. The first activatable entry becomes the new position.
  let idx = editorNav.index + delta;
  while (idx >= 0 && idx < editorNav.stack.length) {
    const item = editorNav.stack[idx];
    editorNav.navigating = true;   // re-activation must NOT record a new entry
    let activated = false;
    try {
      activated = await activateNavItem(item);
    } finally {
      editorNav.navigating = false;
    }
    if (activated) {
      editorNav.index = idx;
      updateEditorNavButtons();
      return;
    }
    idx += delta;
  }
  updateEditorNavButtons();
}

function editorNavBack() { return editorNavGo(-1); }
function editorNavForward() { return editorNavGo(1); }

// The back/forward control lives in the GLOBAL TOPBAR (left of the search bar), not per editor pane —
// it's one global file-history control, like a browser's. Always visible; disabled at the ends.
function updateEditorNavButtons() {
  const back = document.getElementById('topbarNavBack');
  const forward = document.getElementById('topbarNavForward');
  if (back) back.disabled = editorNav.index <= 0;
  if (forward) forward.disabled = editorNav.index >= editorNav.stack.length - 1;
}

function updateFileEditorToolbarSeparators(panel) {
  const mode = fileEditorToolbarControlVisible(panel, '.file-editor-mode-control-panel');
  const theme = fileEditorToolbarControlVisible(panel, '.file-editor-theme-panel');
  const tools = [
    '.file-editor-preview-font-panel',
    '.file-editor-gutter-panel',
    '.file-editor-wrap-panel',
    '.file-editor-find-panel',
    '.file-editor-blame-panel',
    '.file-editor-diff-panel',
    '.file-editor-diff-expand-panel',
    '.file-editor-diff-ref-panel',
  ].some(selector => fileEditorToolbarControlVisible(panel, selector));
  const reload = fileEditorToolbarControlVisible(panel, '.file-editor-reload-panel');
  const save = fileEditorToolbarControlVisible(panel, '.file-editor-save-panel');
  // #42: the editor controls now live on their own toolbar row below the tab strip (no frame
  // controls sit beside them), so separators only sit between adjacent visible control groups.
  setFileEditorToolbarSeparator(panel, 'mode', (theme || mode) && (tools || reload || save));
  setFileEditorToolbarSeparator(panel, 'tools', tools && (reload || save));
  setFileEditorToolbarSeparator(panel, 'theme', reload && save);
  const toolbar = panel?.querySelector?.('.file-editor-toolbar');
  if (toolbar) toolbar.hidden = !(theme || mode || tools || reload || save);
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
const MARKDOWN_PREVIEW_INPUT_ATTRS = new Set(['type', 'checked', 'disabled', 'aria-label', 'class']);

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
  if (tagName === 'input' && !MARKDOWN_PREVIEW_INPUT_ATTRS.has(name)) {
    element.removeAttribute(attr.name);
    return;
  }
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

function markdownPreviewInputAllowed(element) {
  return String(element?.getAttribute?.('type') || '').toLowerCase() === 'checkbox';
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
    if (tagName === 'input') {
      if (!markdownPreviewInputAllowed(child)) {
        child.remove();
        continue;
      }
      child.setAttribute('type', 'checkbox');
      child.setAttribute('disabled', '');
    }
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

// DOIT.15: turn bare http(s) URLs in rendered markdown into real <a> links — version-proof against
// marked's GFM autolink missing them (e.g. when per-line source anchors are interleaved). Skips text
// already inside <a>/<code>/<pre> so existing links and code samples are untouched. Reuses
// markdownPreviewUrlAllowed so only safe schemes link; mirrors the app's safe-link attributes.
function linkifyBareUrls(root) {
  if (!root || typeof document.createTreeWalker !== 'function') return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      for (let el = node.parentElement; el; el = el.parentElement) {
        const tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (tag === 'a' || tag === 'code' || tag === 'pre') return NodeFilter.FILTER_REJECT;
      }
      return /\bhttps?:\/\/\S/.test(node.nodeValue || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  const targets = [];
  while (walker.nextNode()) targets.push(walker.currentNode);
  const urlRe = /\bhttps?:\/\/[^\s<>"')\]}]+/g;
  for (const textNode of targets) {
    const text = textNode.nodeValue;
    let last = 0;
    let match;
    const frag = document.createDocumentFragment();
    urlRe.lastIndex = 0;
    while ((match = urlRe.exec(text))) {
      const url = match[0].replace(/[.,;:!?]+$/, '');   // drop trailing sentence punctuation
      const start = match.index;
      const end = start + url.length;
      if (start > last) frag.appendChild(document.createTextNode(text.slice(last, start)));
      if (markdownPreviewUrlAllowed(url, 'a')) {
        const a = document.createElement('a');
        a.href = url;
        a.textContent = url;
        a.target = '_blank';
        a.rel = 'noreferrer noopener';
        frag.appendChild(a);
      } else {
        frag.appendChild(document.createTextNode(url));
      }
      last = end;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.replaceWith(frag);
  }
}

function renderMarkdownPreviewInto(container, text, markdownPath) {
  if (typeof window.marked === 'undefined') {
    container.textContent = 'marked.js not loaded (offline CDN?)';
    return;
  }
  const html = window.marked.parse(markdownTextWithSourceAnchors(text), {gfm: true, breaks: true});
  const frag = sanitizeMarkdownPreviewHtml(html);
  linkifyBareUrls(frag);
  container.replaceChildren(frag);
  applyMarkdownSourceLines(container, text);
  installLinkContextMenu(container);   // DOIT.15: right-click Copy URL / Open URL on rendered links
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
  if (fileEditorPreviewDisplayMode !== 'vanilla') {
    container.querySelectorAll('pre code').forEach(block => {
      if (typeof window.hljs !== 'undefined') {
        try { window.hljs.highlightElement(block); } catch (_) {}
      }
      applyMarkdownFenceFallbackHighlight(block);
    });
  }
}

function markdownFenceLanguage(block) {
  const classes = Array.from(block?.classList || []);
  for (const className of classes) {
    const match = String(className || '').match(/^(?:language|lang)-(.+)$/);
    if (match) return match[1].toLowerCase();
  }
  return '';
}

function applyMarkdownFenceFallbackHighlight(block) {
  const language = markdownFenceLanguage(block);
  if (!language) return;
  const html = simpleCodeSyntaxHtml(language, block.textContent || '');
  if (html === null) return;
  block.innerHTML = html;
  block.classList.add('editor-highlight-code');
}

function safeDecodePathComponent(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function localPathFromFileHref(href) {
  const raw = String(href || '').trim();
  if (!/^file:/i.test(raw)) return '';
  try {
    const base = globalThis.location?.href || 'http://localhost/';
    const url = new URL(raw, base);
    if (url.protocol !== 'file:') return '';
    return safeDecodePathComponent(url.pathname || '');
  } catch (_) {
    const match = raw.match(/^file:\/\/(?:localhost)?(\/[^?#]*)/i);
    return match ? safeDecodePathComponent(match[1]) : '';
  }
}

function openMarkdownPreviewPathLink(container, resolved) {
  const owner = openFileOwnerSessionsForPath(container?.dataset?.mdPath || '')[0] || undefined;
  return Promise.resolve(openFileInEditor(resolved, basenameOf(resolved), {
    viewMode: editorPreviewModeAvailable(resolved) ? 'preview' : 'edit',
    ownerSession: owner,
  })).catch(() => showToast(t('preview.openFailed', {path: resolved}), '', {level: 'error'}));
}

// DOIT.6 #133: in the file-editor markdown preview, route link clicks: in-page #anchors keep default;
// file:// server paths and relative file links open through the YOLOmux editor, while external links
// open in a new browser tab. The server's read endpoint still rejects paths outside allowed roots.
function handleMarkdownPreviewLinkClick(event) {
  const a = event.target.closest?.('a');
  if (!a) return;
  const container = event.currentTarget;
  const href = a.getAttribute('href') || '';
  if (!href || href.startsWith('#')) return;
  if (/^file:/i.test(href)) {
    event.preventDefault();
    const resolved = localPathFromFileHref(href);
    if (resolved) openMarkdownPreviewPathLink(container, resolved);
    return;
  }
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
  openMarkdownPreviewPathLink(container, resolved);
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
    statusErr(`HTML preview failed: ${esc(error)}`);
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
  container.classList.toggle('vanilla-preview-body', fileEditorPreviewDisplayMode === 'vanilla');
  if (isMarkdownPath(path)) {
    // DOIT.9 fix 6: skip the expensive markdown render (marked.parse + recursive sanitize + per-block
    // hljs) when the path + content are unchanged from the last render — mirrors CodeMirror's
    // _cmSignature short-circuit. Prevents a multi-second stall re-rendering a large .md when an
    // unrelated panel render fires (off the reorder hot path once S2 lands, but a latent cost).
    if (container._previewPath !== path || container._previewText !== text || container._previewDisplayMode !== fileEditorPreviewDisplayMode) {
      container._previewPath = path;
      container._previewText = text;
      container._previewDisplayMode = fileEditorPreviewDisplayMode;
      renderMarkdownPreviewInto(container, text, path);
    }
  } else {
    container._previewPath = null;
    container._previewText = null;
    container._previewDisplayMode = null;
    if (isHtmlPath(path)) renderHtmlPreviewInto(container, path, text);
    else renderEditorCodePreviewInto(container, path, text);
  }
  restoreElementScrollPosition(container, scrollTop, scrollLeft);
}

const filePreviewPopouts = new Map();

function filePreviewPopoutsForPath(path) {
  const record = filePreviewPopouts.get(path);
  return record ? [record] : [];
}

function closeFilePreviewPopout(path) {
  const record = filePreviewPopouts.get(path);
  filePreviewPopouts.delete(path);
  const previewWindow = record?.window;
  if (!previewWindow || previewWindow.closed) return false;
  try { previewWindow.close?.(); } catch (_) {}
  return true;
}

function filePreviewPopoutDocument(previewWindow) {
  try { return previewWindow?.document || null; } catch (_) { return null; }
}

function filePreviewPopoutScrollElement(previewWindow) {
  const doc = filePreviewPopoutDocument(previewWindow);
  return doc?.scrollingElement || doc?.documentElement || doc?.body || null;
}

function filePreviewPopoutPreviewRoot(previewWindow) {
  return filePreviewPopoutDocument(previewWindow)?.querySelector?.('[data-preview-root]') || null;
}

function filePreviewPopoutCanDrive(previewWindow) {
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  return elementCanScroll(scroller);
}

function scrollSyncTargetPosition(from, to, axis = 'top') {
  const scrollKey = axis === 'left' ? 'scrollLeft' : 'scrollTop';
  const sizeKey = axis === 'left' ? 'scrollWidth' : 'scrollHeight';
  const clientKey = axis === 'left' ? 'clientWidth' : 'clientHeight';
  const sourceSize = Math.max(0, Number(from?.[sizeKey] || 0));
  const targetSize = Math.max(0, Number(to?.[sizeKey] || 0));
  const sourceClient = Math.max(0, Number(from?.[clientKey] || 0));
  const targetClient = Math.max(0, Number(to?.[clientKey] || 0));
  const maxFrom = Math.max(0, sourceSize - sourceClient);
  const maxTo = Math.max(0, targetSize - targetClient);
  const current = Math.max(0, Number(from?.[scrollKey] || 0));
  const edgeSnap = Math.max(2, Math.ceil(sourceClient * 0.01));
  if (maxTo <= 0 || current <= edgeSnap) return 0;
  if (maxFrom <= edgeSnap || current >= maxFrom - edgeSnap) return maxTo;
  const sourceCenter = Math.min(maxFrom, current) + (sourceClient / 2);
  const centerRatio = sourceSize > 0 ? sourceCenter / sourceSize : 0;
  const target = (centerRatio * targetSize) - (targetClient / 2);
  return Math.min(maxTo, Math.max(0, target));
}

function syncScrollPositionByRatio(from, to) {
  if (!from || !to) return false;
  to.scrollTop = scrollSyncTargetPosition(from, to, 'top');
  to.scrollLeft = scrollSyncTargetPosition(from, to, 'left');
  return true;
}

function scrollElementAtVerticalEdge(element) {
  const maxTop = Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
  const current = Math.max(0, Number(element?.scrollTop || 0));
  const edgeSnap = Math.max(2, Math.ceil(Number(element?.clientHeight || 0) * 0.01));
  return current <= edgeSnap || current >= maxTop - edgeSnap;
}

function syncFilePreviewPopoutFromPanel(path, record, panel, source) {
  if (!record) return false;
  const previewWindow = record.window;
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  const root = filePreviewPopoutPreviewRoot(previewWindow);
  const from = fileEditorSourceElement(panel, source);
  if (!scroller || !root || !from || !elementCanScroll(scroller)) return false;
  setFileEditorScrollSyncGuard(record);
  return syncScrollPositionByRatio(from, scroller);
}

function syncFilePreviewPopoutsFromPanel(panel, source) {
  const path = fileEditorPanelPath(panel);
  if (!path || !fileEditorSourceCanDrive(panel, source)) return false;
  let synced = false;
  for (const record of filePreviewPopoutsForPath(path)) {
    synced = syncFilePreviewPopoutFromPanel(path, record, panel, source) || synced;
  }
  return synced;
}

function syncFilePreviewPopoutScroll(path, previewWindow, options = {}) {
  const record = filePreviewPopouts.get(path);
  if (!record || !filePreviewPopoutCanDrive(previewWindow)) return false;
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  const forceEdge = options.forceEdges === true && scrollElementAtVerticalEdge(scroller);
  if (!forceEdge && fileEditorScrollSyncBlocked(record)) return false;
  let synced = false;
  for (const panel of fileEditorPanelsForPath(path)) {
    setFileEditorScrollSyncGuard(panel);
    const mode = fileEditorPanelMode(panel);
    const previewPane = fileEditorPanelPreviewPane(panel);
    const editorScroller = fileEditorPanelScroller(panel);
    if (mode !== 'diff' && editorScroller && elementCanScroll(editorScroller)) synced = syncScrollPositionByRatio(scroller, editorScroller) || synced;
    if ((mode === 'preview' || mode === 'split') && previewPane && elementCanScroll(previewPane)) synced = syncScrollPositionByRatio(scroller, previewPane) || synced;
  }
  return synced;
}

function scheduleFilePreviewPopoutScrollSync(path, previewWindow, options = {}) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  if (record.scrollSyncFrame) return true;
  const run = () => {
    record.scrollSyncFrame = 0;
    syncFilePreviewPopoutScroll(path, previewWindow, options);
  };
  if (typeof previewWindow?.requestAnimationFrame === 'function') record.scrollSyncFrame = previewWindow.requestAnimationFrame(run);
  else if (typeof requestAnimationFrame === 'function') record.scrollSyncFrame = requestAnimationFrame(run);
  else record.scrollSyncFrame = setTimeout(run, 0);
  return true;
}

function currentStylesheetHref(match) {
  const link = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
    .find(item => String(item.getAttribute('href') || '').includes(match));
  return link ? link.href : '';
}

function previewPopoutBodyClassName() {
  const keep = ['theme-light', 'theme-dark', 'editor-theme-light', 'editor-theme-dark', 'editor-preview-vanilla'];
  const classes = keep.filter(name => document.body?.classList?.contains(name));
  classes.push('file-preview-popout-window');
  return classes.join(' ');
}

function previewPopoutVariableStyle() {
  const root = getComputedStyle(document.documentElement);
  const names = [
    '--active-accent', '--active-accent-rgb', '--active-accent-bright', '--active-accent-text',
    '--active-control-bg', '--active-control-border', '--active-control-text',
    '--editor-preview-font-size', '--editor-line-height',
    '--lt-editor-bg', '--lt-editor-preview-bg', '--lt-text', '--lt-muted', '--lt-line',
    '--lt-panel', '--lt-panel2', '--lt-markdown-heading', '--lt-markdown-heading-bg',
    '--lt-markdown-link', '--lt-markdown-strong', '--lt-markdown-emphasis',
    '--lt-code-inline', '--lt-code-inline-bg', '--lt-code-inline-border',
    '--markdown-heading', '--markdown-heading-bg', '--markdown-link', '--markdown-strong',
    '--markdown-emphasis', '--code-inline', '--code-inline-bg', '--code-inline-border',
    '--code-keyword', '--code-control', '--code-atom', '--code-string', '--code-number', '--code-variable',
    '--code-function', '--code-type', '--code-property', '--code-tag', '--code-comment',
    '--code-invalid',
  ];
  const aliases = [
    ['--editor-scheme-bg', '--popout-editor-scheme-bg'],
    ['--editor-scheme-fg', '--popout-editor-scheme-fg'],
    ['--editor-scheme-muted', '--popout-editor-scheme-muted'],
    ['--editor-scheme-line', '--popout-editor-scheme-line'],
    ['--editor-scheme-panel', '--popout-editor-scheme-panel'],
    ['--editor-scheme-panel2', '--popout-editor-scheme-panel2'],
    ['--editor-scheme-preview-bg', '--popout-editor-scheme-preview-bg'],
  ];
  const copied = names
    .map(name => {
      const value = root.getPropertyValue(name).trim();
      return value ? `${name}: ${value}` : '';
    });
  const aliased = aliases.map(([source, target]) => {
    const value = root.getPropertyValue(source).trim();
    return value ? `${target}: ${value}` : '';
  });
  return [...copied, ...aliased].filter(Boolean)
    .join('; ');
}

function previewPopoutToolbarHtml() {
  return `
      <span class="file-editor-preview-font-panel" role="group" aria-label="${esc(t('editor.previewFont.aria'))}">
        <button type="button" data-editor-preview-font-step="-1" title="${esc(t('editor.previewFont.decrease'))}" aria-label="${esc(t('editor.previewFont.decrease'))}">A-</button>
        <span class="file-editor-preview-font-value" aria-live="polite">${esc(String(editorPreviewFontSize))}</span>
        <button type="button" data-editor-preview-font-step="1" title="${esc(t('editor.previewFont.increase'))}" aria-label="${esc(t('editor.previewFont.increase'))}">A+</button>
      </span>
      <button type="button" class="file-editor-theme-panel" data-preview-popout-theme title="${esc(editorThemeLabel())}" aria-label="${esc(editorThemeLabel())}"><span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span></button>`;
}

function renderedPreviewSnapshot(path, text) {
  const scratch = document.createElement('div');
  scratch.className = 'file-editor-preview-pane-panel';
  renderEditorPreviewPane(scratch, path, text);
  scratch.hidden = false;
  return {
    className: scratch.className,
    html: scratch.innerHTML,
  };
}

function writeFilePreviewPopoutDocument(path, previewWindow, snapshot) {
  const doc = previewWindow?.document;
  if (!doc) return false;
  const title = `${basenameOf(path)} preview`;
  const cssHref = currentStylesheetHref('yolomux.css') || '/static/yolomux.css';
  doc.open();
  doc.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(title)}</title>
  <link rel="stylesheet" href="${esc(cssHref)}">
  <style>
    html {
      min-height: 100%;
      margin: 0;
      overflow: auto;
    }
    body.file-preview-popout-window {
      min-height: 100%;
      height: auto;
      margin: 0;
      display: block !important;
      grid-template-rows: none !important;
      overflow: auto;
    }
    body.file-preview-popout-window {
      background: var(--editor-preview-bg, var(--bg, #ffffff));
      color: var(--text, #111827);
    }
    .file-preview-popout-shell {
      box-sizing: border-box;
      width: 100%;
      margin: 0 auto;
      padding: 64px 24px 36px;
    }
    .file-preview-popout-title {
      position: fixed;
      top: 0;
      left: 50%;
      z-index: 1000;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      box-sizing: border-box;
      width: calc(100% - 48px);
      min-height: 32px;
      padding: 8px 0 12px;
      margin-bottom: 12px;
      transform: translateX(-50%);
      border-bottom: 1px solid var(--border, #d1d5db);
      background: var(--editor-preview-bg, var(--bg, #ffffff));
      color: var(--text, #111827);
      font: 600 13px/1.3 var(--font, system-ui, sans-serif);
      box-shadow: 0 1px 0 var(--editor-preview-bg, var(--bg, #ffffff));
    }
    .file-preview-popout-title-path {
      grid-column: 1;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      justify-self: start;
    }
    .file-preview-popout-title .file-editor-preview-font-panel {
      grid-column: 2;
      display: inline-flex;
      align-items: center;
      justify-self: center;
    }
    .file-preview-popout-title .file-editor-theme-panel {
      grid-column: 3;
      justify-self: end;
      min-width: 66px;
      width: auto;
      height: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      padding: 0 7px 0 4px;
    }
    .file-preview-popout-window .file-editor-preview-pane-panel {
      position: static !important;
      inset: auto !important;
      left: auto !important;
      right: auto !important;
      top: auto !important;
      bottom: auto !important;
      display: block !important;
      box-sizing: border-box;
      width: 100% !important;
      max-width: 100%;
      min-height: auto;
      max-height: none;
      height: auto;
      overflow: visible;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .file-preview-popout-window {
      --editor-scheme-bg: var(--popout-editor-scheme-bg);
      --editor-scheme-fg: var(--popout-editor-scheme-fg);
      --editor-scheme-muted: var(--popout-editor-scheme-muted);
      --editor-scheme-line: var(--popout-editor-scheme-line);
      --editor-scheme-panel: var(--popout-editor-scheme-panel);
      --editor-scheme-panel2: var(--popout-editor-scheme-panel2);
      --editor-scheme-preview-bg: var(--popout-editor-scheme-preview-bg);
      --bg: var(--editor-scheme-bg, #0f131a);
      --panel: var(--editor-scheme-panel, #151b24);
      --panel2: var(--editor-scheme-panel2, #1b2432);
      --text: var(--editor-scheme-fg, #e4e8ee);
      --muted: var(--editor-scheme-muted, #8b95a5);
      --line: var(--editor-scheme-line, #2a3444);
      --editor-preview-bg: var(--editor-scheme-preview-bg, var(--bg));
    }
    .file-preview-popout-window .markdown-body {
      color: var(--text, #111827);
      background: transparent;
    }
    .file-preview-popout-window .markdown-body pre code.hljs {
      color: var(--editor-scheme-fg, inherit) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-comment,
    .file-preview-popout-window .markdown-body pre code .hljs-quote {
      color: var(--code-comment) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-keyword,
    .file-preview-popout-window .markdown-body pre code .hljs-selector-tag,
    .file-preview-popout-window .markdown-body pre code .hljs-literal,
    .file-preview-popout-window .markdown-body pre code .hljs-section,
    .file-preview-popout-window .markdown-body pre code .hljs-doctag {
      color: var(--code-keyword) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-string,
    .file-preview-popout-window .markdown-body pre code .hljs-regexp,
    .file-preview-popout-window .markdown-body pre code .hljs-addition,
    .file-preview-popout-window .markdown-body pre code .hljs-template-variable {
      color: var(--code-string) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-number,
    .file-preview-popout-window .markdown-body pre code .hljs-symbol,
    .file-preview-popout-window .markdown-body pre code .hljs-bullet,
    .file-preview-popout-window .markdown-body pre code .hljs-attr {
      color: var(--code-number) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-title,
    .file-preview-popout-window .markdown-body pre code .hljs-title.function_,
    .file-preview-popout-window .markdown-body pre code .hljs-function .hljs-title {
      color: var(--code-function) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-built_in,
    .file-preview-popout-window .markdown-body pre code .hljs-type,
    .file-preview-popout-window .markdown-body pre code .hljs-class .hljs-title {
      color: var(--code-type) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-variable,
    .file-preview-popout-window .markdown-body pre code .hljs-params,
    .file-preview-popout-window .markdown-body pre code .hljs-name {
      color: var(--code-variable) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-property,
    .file-preview-popout-window .markdown-body pre code .hljs-attribute {
      color: var(--code-property) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-tag {
      color: var(--code-tag) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-deletion {
      color: var(--code-invalid) !important;
    }
    .file-preview-popout-window .markdown-body pre code .hljs-meta {
      color: var(--code-atom) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-keyword {
      color: var(--code-keyword) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-control {
      color: var(--code-control) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-string {
      color: var(--code-string) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-comment {
      color: var(--code-comment) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-number {
      color: var(--code-number) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-constant {
      color: var(--code-atom) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-builtin,
    .file-preview-popout-window .markdown-body pre code .code-function {
      color: var(--code-function) !important;
      font-weight: 700;
    }
    .file-preview-popout-window .markdown-body pre code .code-type,
    .file-preview-popout-window .markdown-body pre code .code-attr {
      color: var(--code-type) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-variable {
      color: var(--code-variable) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-property {
      color: var(--code-property) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-tag {
      color: var(--code-tag) !important;
    }
    .file-preview-popout-window .markdown-body pre code .code-invalid {
      color: var(--code-invalid) !important;
    }
    .file-preview-popout-window.editor-theme-light {
      --editor-scheme-bg: var(--lt-editor-bg);
      --editor-scheme-fg: var(--lt-text);
      --editor-scheme-muted: var(--lt-muted);
      --editor-scheme-line: var(--lt-line);
      --editor-scheme-panel: var(--lt-panel);
      --editor-scheme-panel2: var(--lt-panel2);
      --editor-scheme-preview-bg: var(--lt-editor-preview-bg);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body {
      --markdown-heading: var(--lt-markdown-heading);
      --markdown-heading-bg: var(--lt-markdown-heading-bg);
      --markdown-link: var(--lt-markdown-link);
      --markdown-strong: var(--lt-markdown-strong);
      --markdown-emphasis: var(--lt-markdown-emphasis);
      --code-inline: var(--lt-code-inline);
      --code-inline-bg: var(--lt-code-inline-bg);
      --code-inline-border: var(--lt-code-inline-border);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body pre {
      color: var(--lt-text);
      background: var(--lt-panel);
      border-color: var(--lt-line);
    }
    .file-preview-popout-window.editor-theme-light .markdown-body pre code {
      color: inherit;
      background: transparent;
      border: 0;
    }
    @media (max-width: 640px) {
      .file-preview-popout-shell { padding: 64px 14px 28px; }
      .file-preview-popout-title { width: calc(100% - 28px); }
    }
  </style>
</head>
<body class="${esc(previewPopoutBodyClassName())}" style="${esc(previewPopoutVariableStyle())}">
  <main class="file-preview-popout-shell">
    <header class="file-preview-popout-title" role="toolbar" aria-label="${esc(t('editor.toolbar.aria'))}">
      <span class="file-preview-popout-title-path">${esc(compactHomePath(path))}</span>
      ${previewPopoutToolbarHtml()}
    </header>
    <article data-preview-root class="${esc(snapshot.className)}">${snapshot.html}</article>
  </main>
</body>
</html>`);
  doc.close();
  doc._yolomuxPreviewControlsBound = false;
  bindFilePreviewPopoutControls(path, previewWindow);
  return true;
}

function updateFilePreviewPopoutControls(path, previewWindow) {
  const doc = previewWindow?.document;
  if (!doc) return;
  doc.body?.setAttribute('style', previewPopoutVariableStyle());
  const themeButton = doc.querySelector('[data-preview-popout-theme]');
  if (themeButton) updateEditorThemeButton(themeButton, {includeVanilla: true});
  updateEditorPreviewFontControls(doc);
}

function bindFilePreviewPopoutControls(path, previewWindow) {
  const doc = previewWindow?.document;
  if (!doc || doc._yolomuxPreviewControlsBound) return;
  doc._yolomuxPreviewControlsBound = true;
  if (typeof previewWindow._yolomuxPreviewControlsCleanup === 'function') {
    previewWindow._yolomuxPreviewControlsCleanup();
  }
  const cleanup = [];
  const bind = (target, type, handler) => {
    if (!target?.addEventListener) return;
    target.addEventListener(type, handler, {passive: true});
    cleanup.push(() => target.removeEventListener?.(type, handler));
  };
  previewWindow._yolomuxPreviewControlsCleanup = () => {
    while (cleanup.length) {
      try { cleanup.pop()(); } catch (_) {}
    }
  };
  doc.querySelector('[data-preview-popout-theme]')?.addEventListener('click', event => {
    event.preventDefault();
    cycleEditorThemeMode({includeVanilla: true});
  });
  doc.querySelector('.file-editor-preview-font-panel')?.addEventListener('click', event => {
    const button = event.target?.closest?.('[data-editor-preview-font-step]');
    if (!button) return;
    event.preventDefault();
    setEditorPreviewFontSize(editorPreviewFontSize + Number(button.dataset.editorPreviewFontStep || 0));
  });
  const syncScroll = () => {
    syncFilePreviewPopoutScroll(path, previewWindow, {forceEdges: true});
    scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true});
  };
  const scheduleScrollSync = () => scheduleFilePreviewPopoutScrollSync(path, previewWindow, {forceEdges: true});
  const scroller = filePreviewPopoutScrollElement(previewWindow);
  bind(previewWindow, 'scroll', syncScroll);
  bind(previewWindow, 'wheel', scheduleScrollSync);
  bind(doc, 'scroll', syncScroll);
  bind(doc, 'wheel', scheduleScrollSync);
  bind(scroller, 'scroll', syncScroll);
  bind(scroller, 'wheel', scheduleScrollSync);
  updateFilePreviewPopoutControls(path, previewWindow);
}

function updateFilePreviewPopout(path, text) {
  const record = filePreviewPopouts.get(path);
  if (!record) return false;
  const previewWindow = record.window;
  if (!previewWindow || previewWindow.closed) {
    filePreviewPopouts.delete(path);
    return false;
  }
  const snapshot = renderedPreviewSnapshot(path, text);
  try {
    const doc = previewWindow.document;
    const scroller = filePreviewPopoutScrollElement(previewWindow);
    const scrollTop = scroller?.scrollTop || 0;
    const scrollLeft = scroller?.scrollLeft || 0;
    const root = doc?.querySelector?.('[data-preview-root]');
    if (!root) return writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    root.className = snapshot.className;
    root.innerHTML = snapshot.html;
    doc.body.className = previewPopoutBodyClassName();
    updateFilePreviewPopoutControls(path, previewWindow);
    doc.title = `${basenameOf(path)} preview`;
    restoreElementScrollPosition(scroller, scrollTop, scrollLeft);
    return true;
  } catch (_) {
    filePreviewPopouts.delete(path);
    return false;
  }
}

function refreshFilePreviewPopouts() {
  for (const path of Array.from(filePreviewPopouts.keys())) {
    const state = openFiles.get(path);
    if (state?.kind === 'text') updateFilePreviewPopout(path, state.content);
    else filePreviewPopouts.delete(path);
  }
}

function writeFilePreviewPopoutAfterNavigation(path, previewWindow, snapshot) {
  let written = false;
  const write = () => {
    if (written || !previewWindow || previewWindow.closed) return;
    written = true;
    writeFilePreviewPopoutDocument(path, previewWindow, snapshot);
    previewWindow.focus?.();
  };
  try {
    if (previewWindow.location?.pathname === '/preview-popout' && previewWindow.document?.readyState === 'complete') {
      write();
      return;
    }
    previewWindow.addEventListener?.('load', write, {once: true});
    window.setTimeout(write, 1000);
  } catch (_) {
    write();
  }
}

function openFilePreviewPopout(path, panel = null) {
  if (!path || !editorPreviewModeAvailable(path)) return false;
  syncOpenFileContentFromPanels(path, panel);
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  const existing = filePreviewPopouts.get(path)?.window;
  if (existing && !existing.closed) {
    updateFilePreviewPopout(path, state.content);
    existing.focus?.();
    return true;
  }
  const previewWindow = window.open(`/preview-popout?path=${encodeURIComponent(path)}`, `yolomux-preview-${encodeURIComponent(path)}`, 'popup,width=980,height=900');
  if (!previewWindow) {
    statusErr('preview pop-out was blocked by the browser');
    return false;
  }
  try {
    filePreviewPopouts.set(path, {window: previewWindow});
    writeFilePreviewPopoutAfterNavigation(path, previewWindow, renderedPreviewSnapshot(path, state.content));
    return true;
  } catch (error) {
    filePreviewPopouts.delete(path);
    try { previewWindow.close(); } catch (_) {}
    statusErr(`preview pop-out failed: ${esc(error)}`);
    return false;
  }
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

function simpleTokenHighlightTokens(raw, rules) {
  const text = String(raw || '');
  let index = 0;
  const tokens = [];
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
      break;
    }
    if (best.index > index) index = best.index;
    tokens.push({
      from: best.index,
      to: best.index + best.text.length,
      text: best.text,
      className: best.rule.className,
    });
    index = best.index + best.text.length;
  }
  return tokens;
}

function simpleTokenHighlightHtml(raw, rules) {
  const text = String(raw || '');
  const tokens = simpleTokenHighlightTokens(text, rules);
  let html = '';
  let index = 0;
  for (const token of tokens) {
    html += esc(text.slice(index, token.from));
    html += `<span class="${token.className}">${esc(token.text)}</span>`;
    index = token.to;
  }
  html += esc(text.slice(index));
  return html;
}

function normalizeSimpleCodeSyntaxLanguage(language) {
  const normalized = String(language || '').trim().toLowerCase();
  if (normalized === 'py') return 'python';
  if (normalized === 'rs') return 'rust';
  if (normalized === 'sh' || normalized === 'shell' || normalized === 'zsh') return 'bash';
  if (normalized === 'js' || normalized === 'jsx') return 'javascript';
  if (normalized === 'ts' || normalized === 'tsx') return 'typescript';
  if (normalized === 'yml') return 'yaml';
  return normalized;
}

function simpleCodeSyntaxRules(language) {
  const normalized = normalizeSimpleCodeSyntaxLanguage(language);
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
    {regex: /\b(?:Any|BaseModel|Callable|DeltaFunctionCall|DeltaMessage|DeltaToolCall|Dict|ExtractedToolCallInformation|Iterable|Iterator|List|Literal|Mapping|NoneType|OpenAIBaseModel|Optional|Sequence|Set|Self|ToolParser|Tuple|Type|Union|bool|bytes|dict|float|int|list|set|str|tuple)\b/g, className: 'code-type'},
    {regex: /\b(?:False|None|True)\b/g, className: 'code-constant'},
    {regex: /\b[A-Z][A-Za-z0-9_]*(?=[\[\]|,):]|\s*$)/g, className: 'code-type'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*\()/g, className: 'code-function'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*:)/g, className: 'code-property'},
    {regex: /\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g, className: 'code-keyword'},
    {regex: /\b(?:cls|self)\b/g, className: 'code-variable'},
    {regex: /\b[a-z_][A-Za-z0-9_]*\b/g, className: 'code-variable'},
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
    {regex: /\b(?:fn|pub|where)\b/g, className: 'code-control'},
    {regex: /\b[A-Za-z_][A-Za-z0-9_]*(?=\s*:)/g, className: 'code-property'},
    {regex: /\b[a-z_][A-Za-z0-9_]*(?=\s*\()/g, className: 'code-function'},
    {regex: /\b(?:Box|Option|Result|Send|String|Tool|ToolCallDelta|ToolParser|ToolParserOutput|Value|Vec|bool|char|dyn|f32|f64|i8|i16|i32|i64|i128|isize|str|u8|u16|u32|u64|u128|usize)\b/g, className: 'code-type'},
    {regex: /\b[A-Z][A-Za-z0-9_]*\b/g, className: 'code-type'},
    {regex: /'[A-Za-z_][A-Za-z0-9_]*/g, className: 'code-type'},
    {regex: /\b(?:as|async|await|break|const|continue|crate|else|enum|extern|false|for|if|impl|in|let|loop|match|mod|move|mut|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|while)\b/g, className: 'code-keyword'},
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
  return rulesByLanguage.get(normalized) || null;
}

function simpleCodeSyntaxTokens(language, text) {
  const rules = simpleCodeSyntaxRules(language);
  if (!rules) return [];
  return simpleTokenHighlightTokens(text, rules);
}

function simpleCodeSyntaxHtml(language, text) {
  const normalized = normalizeSimpleCodeSyntaxLanguage(language);
  if (normalized === 'markdown') return markdownSyntaxHtml(text);
  const rules = simpleCodeSyntaxRules(normalized);
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

function scrollViewportTopForElement(scroller) {
  if (!scroller?.getBoundingClientRect) return 0;
  const doc = scroller.ownerDocument || null;
  if (doc && (doc.scrollingElement === scroller || doc.documentElement === scroller || doc.body === scroller)) return 0;
  return scroller.getBoundingClientRect().top || 0;
}

function scrollTopForPreviewElement(scroller, element) {
  if (!scroller || !element?.getBoundingClientRect) return Number(element?.offsetTop || 0);
  return Math.max(0, Number(scroller.scrollTop || 0) + element.getBoundingClientRect().top - scrollViewportTopForElement(scroller));
}

function previewSourceLineForScroller(previewRoot, scroller) {
  const anchors = previewSourceLineAnchors(previewRoot);
  if (!anchors.length || !scroller) return null;
  const top = Number(scroller.scrollTop || 0) + 6;
  let best = anchors[0];
  for (const item of anchors) {
    if (scrollTopForPreviewElement(scroller, item.element) > top) break;
    best = item;
  }
  return best.line;
}

function scrollPreviewScrollerToSourceLine(previewRoot, scroller, sourceLine) {
  const anchor = previewAnchorForSourceLine(previewRoot, sourceLine);
  if (!anchor || !scroller) return false;
  scroller.scrollTop = Math.max(0, scrollTopForPreviewElement(scroller, anchor.element) - 4);
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

function fileEditorScrollSyncBlocked(panel, source = '') {
  const suppressed = panel?._splitScrollSyncing || Number(panel?._splitScrollSuppressUntil || 0) > nowMs();
  if (!suppressed) return false;
  return !source || panel?._splitScrollSource !== source;
}

function setFileEditorScrollSyncGuardForSource(source, ...panels) {
  const until = nowMs() + fileEditorScrollSyncSuppressMs;
  for (const panel of panels) {
    if (!panel) continue;
    panel._splitScrollSyncing = true;
    panel._splitScrollSource = source || '';
    panel._splitScrollSuppressUntil = Math.max(Number(panel._splitScrollSuppressUntil || 0), until);
    const release = () => {
      panel._splitScrollSyncing = false;
      if (Number(panel._splitScrollSuppressUntil || 0) <= nowMs()) panel._splitScrollSource = '';
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(release, 0));
    else setTimeout(release, 0);
  }
}

function setFileEditorScrollSyncGuard(...panels) {
  setFileEditorScrollSyncGuardForSource('', ...panels);
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
  if (fileEditorPanelMode(panel) === 'diff') return null;
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

function syncFileEditorInPaneSplitScroll(host, source) {
  const content = host.querySelector?.('.file-editor-content');
  if (!content?.classList?.contains('split-preview')) return false;
  const cmView = host._cmView || null;
  const editorScroller = cmView?.scrollDOM || null;
  const previewPane = host.querySelector?.('.file-editor-preview-pane-panel');
  if (!editorScroller || !previewPane || previewPane.hidden) return false;
  if (!fileEditorSourceCanDrive(host, source)) return false;
  const from = source === 'preview' ? previewPane : editorScroller;
  const to = source === 'preview' ? editorScroller : previewPane;
  setFileEditorScrollSyncGuardForSource(source, host);
  return syncScrollPositionByRatio(from, to);
}

function syncFileEditorSplitScroll(host, source) {
  if (!host || fileEditorScrollSyncBlocked(host, source)) return;
  const canDrive = fileEditorSourceCanDrive(host, source);
  if (!canDrive) return;
  syncFileEditorInPaneSplitScroll(host, source);
  syncFilePreviewPopoutsFromPanel(host, source);
}

function scheduleFileEditorSplitScrollSync(host, source) {
  if (!host) return false;
  host._splitScrollPendingSource = source;
  if (host._splitScrollFrame) return true;
  const run = () => {
    host._splitScrollFrame = 0;
    const pendingSource = host._splitScrollPendingSource || source;
    host._splitScrollPendingSource = '';
    syncFileEditorSplitScroll(host, pendingSource);
  };
  if (typeof requestAnimationFrame === 'function') host._splitScrollFrame = requestAnimationFrame(run);
  else host._splitScrollFrame = setTimeout(run, 0);
  return true;
}

function refreshEditorPreviews() {
  for (const [item, panel] of panelNodes.entries()) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    const state = openFiles.get(path);
    if (state?.kind === 'text') {
      renderEditorPreviewPane(panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
      updateFilePreviewPopout(path, state.content);
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
    if (!state.dirty) return reloadOpenFileFromDisk(path, {force: true});
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

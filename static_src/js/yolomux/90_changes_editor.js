const changesOutsideRepoKey = 'Outside repo';

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
  for (const file of fileExplorerSessionFilesState.payload?.files || []) {
    if (file?.abs_path === path && file.repo) return file.repo;
  }
  for (const repoInfo of fileExplorerSessionFilesState.payload?.repos || []) {
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

function sessionFilesRelevantDiffRefRepos() {
  const session = fileExplorerSessionFilesTargetSession();
  const payload = fileExplorerSessionFilesState.payload;
  if (!sessionFilesPayloadIsLoadedForSession(payload, session)) return new Set();
  return new Set(sessionFilesRepoRoots(payload));
}

// C6: the per-repo override map encoded for /api/session-files. Keep this scoped to the current loaded
// Differ payload so stale repo refs from old sessions do not bloat forced refreshes or fragment cache keys.
function sessionFilesRefsQuery() {
  const map = {};
  const relevantRepos = sessionFilesRelevantDiffRefRepos();
  const globalRefs = diffRefParams();
  for (const [repo, refs] of Object.entries(diffRefsByRepo || {})) {
    const normalizedRepo = normalizeDirectoryPath(repo);
    if (!normalizedRepo || !relevantRepos.has(normalizedRepo)) continue;
    const from = cleanDiffRef(refs?.from, '');
    const to = cleanDiffRef(refs?.to, '');
    if (!from && !to) continue;
    const nextRefs = {from: from || 'HEAD', to: to || 'current'};
    if (nextRefs.from === globalRefs.from && nextRefs.to === globalRefs.to) continue;
    map[normalizedRepo] = nextRefs;
  }
  return Object.keys(map).length ? `&refs=${encodeURIComponent(JSON.stringify(map))}` : '';
}

function sessionFilesRequestQueryString() {
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
  considerPayload(fileExplorerSessionFilesState.payload);
  for (const cached of fileExplorerSessionFilesCache.values()) {
    considerPayload(cached?.payload || cached);
  }
  return matches.size === 1 ? Array.from(matches)[0] : '';
}

const diffRefSuggestionLimit = 60;
const diffRefPopoverCompactLimit = 12;
const diffRefPopoverFullLimit = 18;
const diffRefPopoverState = {
  node: null,
  input: null,
  items: [],
  activeIndex: -1,
  listenersInstalled: false,
};

// C6: commit suggestions. With a `repo`, draw only from THAT repo's refs_by_repo so a picker never offers
// a SHA from a sibling repo. With no repo (legacy/global callers), flatten every repo's refs as before.
function diffRefRepoRefs(refsByRepo, repo) {
  if (!repo || !refsByRepo || typeof refsByRepo !== 'object') return null;
  if (Array.isArray(refsByRepo[repo])) return refsByRepo[repo];
  const normalizedRepo = normalizeDirectoryPath(expandUserPath(repo));
  for (const [key, refs] of Object.entries(refsByRepo)) {
    if (normalizeDirectoryPath(expandUserPath(key)) === normalizedRepo && Array.isArray(refs)) return refs;
  }
  return null;
}

function localizedDiffRefSubject(ref, subject = '') {
  const value = String(subject || '');
  if (ref === 'HEAD' && (!value || value === 'base commit')) return t('diff.ref.base');
  if (ref === 'current' && (!value || value === 'working tree')) return t('diff.ref.workingTree');
  return value;
}

function defaultDiffRefSuggestions() {
  return [
    {ref: 'HEAD', short: 'HEAD', subject: localizedDiffRefSubject('HEAD')},
    {ref: 'current', short: 'current', subject: localizedDiffRefSubject('current')},
  ];
}

function diffRefSuggestions(repo) {
  const suggestions = defaultDiffRefSuggestions();
  const seen = new Set(suggestions.map(item => item.ref));
  const addRefs = refs => {
    if (!Array.isArray(refs)) return;
    for (const item of refs) {
      const ref = cleanDiffRef(item?.ref || '', '');
      if (!ref) continue;
      if (seen.has(ref)) {
        const existing = suggestions.find(candidate => candidate.ref === ref);
        if (existing) {
          if (item?.short) existing.short = item.short;
          if (item?.subject) existing.subject = localizedDiffRefSubject(ref, item.subject);
          if (item?.date) existing.date = item.date;
          if (item?.author) existing.author = item.author;
          if (item?.commit) existing.commit = item.commit;
          if (Array.isArray(item?.aliases)) existing.aliases = item.aliases.slice();
        }
        continue;
      }
      suggestions.push({ref, short: item?.short || ref.slice(0, 9), subject: localizedDiffRefSubject(ref, item?.subject), date: item?.date || '', author: item?.author || '', commit: item?.commit || '', aliases: Array.isArray(item?.aliases) ? item.aliases.slice() : []});
      seen.add(ref);
      if (suggestions.length >= diffRefSuggestionLimit) return;
    }
  };
  const refsByRepo = fileExplorerSessionFilesState.payload?.refs_by_repo && typeof fileExplorerSessionFilesState.payload.refs_by_repo === 'object'
    ? fileExplorerSessionFilesState.payload.refs_by_repo
    : {};
  if (repo) {
    addRefs(diffRefRepoRefs(refsByRepo, repo));
  } else {
    for (const refs of Object.values(refsByRepo)) addRefs(refs);
  }
  return coalescedDiffRefSuggestions(suggestions);
}

function fileDiffRefHistoryItems(path) {
  const state = openFiles.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return [];
  const suggestions = defaultDiffRefSuggestions();
  const seen = new Set(suggestions.map(item => item.ref));
  for (const item of state.gitHistory) {
    const ref = cleanDiffRef(item?.ref || '', '');
    if (!ref) continue;
    if (seen.has(ref)) {
      const existing = suggestions.find(candidate => candidate.ref === ref);
      if (existing) {
        if (item?.short) existing.short = item.short;
        if (item?.subject) existing.subject = localizedDiffRefSubject(ref, item.subject);
        if (item?.date) existing.date = item.date;
        if (item?.author) existing.author = item.author;
        if (item?.commit) existing.commit = item.commit;
        if (Array.isArray(item?.aliases)) existing.aliases = item.aliases.slice();
      }
      continue;
    }
    suggestions.push({ref, short: item?.short || ref.slice(0, 9), subject: localizedDiffRefSubject(ref, item?.subject), date: item?.date || '', author: item?.author || '', commit: item?.commit || '', aliases: Array.isArray(item?.aliases) ? item.aliases.slice() : []});
    seen.add(ref);
    if (suggestions.length >= diffRefSuggestionLimit) break;
  }
  return coalescedDiffRefSuggestions(suggestions);
}

function scopedDiffRefSuggestions(repo, path) {
  return path ? fileDiffRefHistoryItems(path) : diffRefSuggestions(repo);
}

function fileDiffRefHistorySignature(path) {
  const state = openFiles.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return 'none';
  return state.gitHistory.map(item => `${item?.ref || ''}:${item?.date || ''}`).join('|');
}

function diffRefItemDateText(item) {
  const ts = Number(item?.date || 0);
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function diffRefItemAuthorText(item) {
  return String(item?.author || '').trim().split(/\s+/)[0] || '';
}

function diffRefShaLike(value) {
  return /^[0-9a-f]{7,40}$/i.test(String(value || '').trim());
}

function diffRefItemCommitId(item) {
  const commit = cleanDiffRef(item?.commit || item?.sha || '', '');
  if (diffRefShaLike(commit)) return commit.toLowerCase();
  const ref = cleanDiffRef(item?.ref || '', '');
  return diffRefShaLike(ref) ? ref.toLowerCase() : '';
}

function diffRefDisplayAliases(item) {
  const aliases = [];
  const addAlias = value => {
    const alias = cleanDiffRef(value, '');
    if (!alias || alias === 'current' || diffRefShaLike(alias) || aliases.includes(alias)) return;
    aliases.push(alias);
  };
  if (item?.ref === 'HEAD') addAlias('HEAD');
  else addAlias(item?.ref);
  const shortParts = cleanDiffRef(item?.short, '').split(/\s+/);
  if (shortParts[0]?.includes('/')) {
    addAlias(shortParts[0].slice(shortParts[0].indexOf('/') + 1));
  }
  shortParts.slice(1).forEach(addAlias);
  (Array.isArray(item?.aliases) ? item.aliases : []).forEach(addAlias);
  return aliases;
}

function diffRefDisplayShort(item) {
  const short = cleanDiffRef(item?.short, '') || cleanDiffRef(item?.ref, '');
  const aliases = diffRefDisplayAliases(item);
  if (!aliases.length) return short;
  let base = short.split(/\s+/)[0].split('/')[0];
  if (!diffRefShaLike(base)) {
    const commit = diffRefItemCommitId(item);
    base = commit ? commit.slice(0, 7) : base;
  }
  if (!diffRefShaLike(base)) return short;
  return `${base}/${aliases[0]}${aliases.length > 1 ? ` ${aliases.slice(1).join(' ')}` : ''}`;
}

// Keep the selected control to a stable short-SHA width; aliases are available in the popup.
function diffRefCompactDisplay(item) {
  const ref = cleanDiffRef(item?.ref, '');
  if (ref === 'HEAD' || ref === 'current') return ref;
  const commit = diffRefItemCommitId(item);
  if (commit) return commit.slice(0, 7);
  const short = cleanDiffRef(item?.short, '') || ref;
  const sha = short.match(/\b[0-9a-f]{7,40}\b/i);
  return sha ? sha[0].slice(0, 7) : short.split(/[\s/]/)[0];
}

function diffRefOptionLabel(item, separator = ' - ') {
  return [diffRefDisplayShort(item), item?.subject || ''].filter(Boolean).join(separator) || item?.ref || '';
}

function mergeDiffRefSameCommitAlias(primary, duplicate) {
  const aliases = new Set(diffRefDisplayAliases(primary));
  for (const value of diffRefDisplayAliases(duplicate)) {
    const alias = cleanDiffRef(value, '');
    if (alias && alias !== primary.ref && alias !== primary.short) aliases.add(alias);
  }
  const primaryShort = cleanDiffRef(primary.short, '');
  const duplicateShort = cleanDiffRef(duplicate?.short, '');
  if (primary.ref === 'HEAD' && duplicateShort && !primaryShort.includes('/HEAD')) {
    primary.short = `${duplicateShort}/HEAD`;
  }
  const defaultSubject = localizedDiffRefSubject(primary.ref);
  const duplicateSubject = localizedDiffRefSubject(duplicate?.ref, duplicate?.subject);
  if ((!primary.subject || primary.subject === defaultSubject) && duplicateSubject) primary.subject = duplicateSubject;
  if (!primary.date && duplicate?.date) primary.date = duplicate.date;
  if (!primary.author && duplicate?.author) primary.author = duplicate.author;
  if (!primary.commit) primary.commit = diffRefItemCommitId(primary) || diffRefItemCommitId(duplicate);
  primary.aliases = Array.from(aliases);
  return primary;
}

function coalescedDiffRefSuggestions(items) {
  const out = [];
  const commitIndexes = new Map();
  for (const rawItem of Array.isArray(items) ? items : []) {
    const item = {...rawItem};
    const commit = diffRefItemCommitId(item);
    const existingIndex = commit ? commitIndexes.get(commit) : undefined;
    if (existingIndex !== undefined) {
      const existing = out[existingIndex];
      if (item.ref === 'HEAD' && existing.ref !== 'HEAD') {
        out[existingIndex] = mergeDiffRefSameCommitAlias(item, existing);
      } else {
        mergeDiffRefSameCommitAlias(existing, item);
      }
      continue;
    }
    if (commit) commitIndexes.set(commit, out.length);
    out.push(item);
  }
  return out;
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
  const short = cleanDiffRef(item?.short, '');
  const displayShort = cleanDiffRef(diffRefDisplayShort(item), '');
  const aliases = diffRefDisplayAliases(item);
  return diffRefSameCommit(normalized, item?.ref)
    || diffRefSameCommit(normalized, item?.short)
    || normalized === displayShort
    || normalized === short
    || aliases.some(alias => diffRefSameCommit(normalized, alias) || normalized === cleanDiffRef(alias, ''))
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
    items.unshift({ref: value, short: value.slice(0, 9), subject: t('diff.ref.selected')});
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
      const search = [item?.ref, item?.short, diffRefDisplayShort(item), item?.subject, label, ...diffRefDisplayAliases(item)].filter(Boolean).join(' ').toLowerCase();
      return diffRefOptionMatches(query, item) || search.includes(query);
    });
  return matches.slice(0, maxItems);
}

function diffRefFromSuggestions(repo, path = '') {
  return scopedDiffRefSuggestions(repo, path).filter(item => item.ref !== 'current');
}

function diffRefToSuggestions(fromRef = diffRefFrom, repo, path = '') {
  const suggestions = scopedDiffRefSuggestions(repo, path);
  const current = suggestions.find(item => item.ref === 'current') || defaultDiffRefSuggestions()[1];
  const ordered = [current, ...suggestions.filter(item => item.ref !== 'current')];
  const from = cleanDiffRef(fromRef, '');
  const fromIndex = ordered.findIndex(item => diffRefOptionMatches(from, item));
  if (fromIndex < 0) return [current];
  return ordered.slice(0, Math.max(1, fromIndex));
}

function diffRefInputDisplayValue(value, suggestions) {
  const ref = cleanDiffRef(value, '');
  const match = (Array.isArray(suggestions) ? suggestions : []).find(item => diffRefOptionMatches(ref, item));
  return match ? diffRefCompactDisplay(match) : ref;
}

function diffRefPopoverSubjectParts(item) {
  const subject = String(item?.subject || item?.ref || '').trim();
  const explicitNumber = Number(item?.pr_number || item?.prNumber || 0);
  const match = subject.match(/^(.*?)(?:\s*\(\s*(?:PR\s*)?#(\d+)\s*\)|\s+(?:PR\s*)?#(\d+))\s*$/i);
  const number = explicitNumber > 0 ? explicitNumber : Number(match?.[2] || match?.[3] || 0);
  const commitDescription = match?.[1].trim() || subject;
  const branchAliases = item?.ref === 'HEAD'
    ? diffRefDisplayAliases(item).filter(alias => alias !== 'HEAD')
    : [];
  const aliasesText = branchAliases.map(alias => `[${alias}]`).join(' ');
  const description = [aliasesText, commitDescription].filter(Boolean).join(' ');
  return {description, pr: number > 0 ? `(#${number})` : ''};
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
  if (diffRefPopoverState.node) return diffRefPopoverState.node;
  const popover = document.createElement('div');
  popover.className = 'diff-ref-suggestion-popover';
  popover.id = 'diff-ref-suggestion-popover';
  popover.role = 'listbox';
  popover.hidden = true;
  popover.addEventListener('pointerdown', event => {
    event.preventDefault();
  });
  popover.addEventListener('click', event => {
    const option = event.target.closest?.('[data-diff-ref-option-index]');
    if (!option || !popover.contains(option)) return;
    event.preventDefault();
    chooseDiffRefPopoverOption(Number(option.dataset.diffRefOptionIndex));
  });
  diffRefPopoverState.node = popover;
  appOverlayRootElement()?.appendChild(popover);
  installDiffRefPopoverListeners();
  return popover;
}

function installDiffRefPopoverListeners() {
  if (diffRefPopoverState.listenersInstalled) return;
  document.addEventListener('pointerdown', event => {
    const target = event.target;
    if (target?.closest?.('[data-diff-ref-input]') || target?.closest?.('#diff-ref-suggestion-popover')) return;
    hideDiffRefPopover();
  });
  window.addEventListener('resize', () => hideDiffRefPopover());
  document.addEventListener('scroll', event => {
    const target = event.target;
    const {node, input} = diffRefPopoverState;
    if (node && (target === node || node.contains(target))) return;
    if (!input || !node || node.hidden) return;
    if (!input.isConnected) {
      hideDiffRefPopover();
      return;
    }
    const context = diffRefInputContext(input);
    positionDiffRefPopover(input, context.compact);
  }, true);
  diffRefPopoverState.listenersInstalled = true;
}

function positionDiffRefPopover(input, compact) {
  const popover = ensureDiffRefPopover();
  const rect = input?.getBoundingClientRect?.();
  if (!rect) return;
  const viewport = appViewport();
  const viewportWidth = effectiveViewportWidth(viewport);
  const viewportHeight = Math.max(240, viewport.height || 720);
  const edgePadding = 24;
  const availableWidth = Math.max(280, viewportWidth - edgePadding * 2);
  // Both Differ and editor ref pickers need enough room for real commit descriptions, but must leave
  // substantial browser context visible. One responsive width owner keeps the two surfaces in sync.
  const width = Math.min(availableWidth, Math.round(viewportWidth * (2 / 3)));
  const left = Math.max(edgePadding, Math.min(rect.left, viewportWidth - width - edgePadding));
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
  diffRefPopoverState.input = input;
  diffRefPopoverState.items = items;
  diffRefPopoverState.activeIndex = items.findIndex(item => diffRefOptionMatches(input.value, item));
  popover.classList.toggle('compact', context.compact);
  if (!items.length) {
    hideDiffRefPopover();
    return;
  }
  popover.innerHTML = items.map((item, index) => {
    const active = index === diffRefPopoverState.activeIndex;
    const ref = diffRefCompactDisplay(item) || item?.ref || '';
    const subject = diffRefPopoverSubjectParts(item);
    const subjectText = [subject.description, subject.pr].filter(Boolean).join(' ');
    const label = [diffRefDisplayShort(item), subjectText].filter(Boolean).join(' - ') || item?.ref || '';
    const dateText = diffRefItemDateText(item);
    const authorText = diffRefItemAuthorText(item);
    const pr = subject.pr ? `<span class="diff-ref-suggestion-pr">${esc(subject.pr)}</span>` : '';
    return `<button type="button" class="diff-ref-suggestion-option${active ? ' active' : ''}" role="option" aria-selected="${active ? 'true' : 'false'}" data-diff-ref-option-index="${index}" data-diff-ref-value="${esc(item?.ref || '')}" title="${esc(label)}"><span class="diff-ref-suggestion-ref">${esc(ref)}</span><span class="diff-ref-suggestion-subject" title="${esc(subjectText)}"><span class="diff-ref-suggestion-description">${esc(subject.description)}</span>${pr}</span><span class="diff-ref-suggestion-date">${esc(dateText)}</span><span class="diff-ref-suggestion-author">${esc(authorText)}</span></button>`;
  }).join('');
  positionDiffRefPopover(input, context.compact);
  popover.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-controls', popover.id);
}

function hideDiffRefPopover() {
  if (diffRefPopoverState.node) diffRefPopoverState.node.hidden = true;
  if (diffRefPopoverState.input) {
    diffRefPopoverState.input.setAttribute('aria-expanded', 'false');
    diffRefPopoverState.input.removeAttribute('aria-controls');
  }
  diffRefPopoverState.input = null;
  diffRefPopoverState.items = [];
  diffRefPopoverState.activeIndex = -1;
}

function syncDiffRefPopoverActiveOption() {
  const popover = diffRefPopoverState.node;
  if (!popover || popover.hidden) return;
  popover.querySelectorAll?.('[data-diff-ref-option-index]')?.forEach((button, index) => {
    const active = index === diffRefPopoverState.activeIndex;
    button.classList.toggle(CLS.active, active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    if (active) button.scrollIntoView?.({block: 'nearest'});
  });
}

function chooseDiffRefPopoverOption(index) {
  const input = diffRefPopoverState.input;
  const item = diffRefPopoverState.items[index];
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
  const open = diffRefPopoverState.node && !diffRefPopoverState.node.hidden && diffRefPopoverState.input === input;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (!open) renderDiffRefPopover(input, {showAll: true});
    if (!diffRefPopoverState.items.length) return true;
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    diffRefPopoverState.activeIndex = diffRefPopoverState.activeIndex < 0
      ? (delta > 0 ? 0 : diffRefPopoverState.items.length - 1)
      : (diffRefPopoverState.activeIndex + delta + diffRefPopoverState.items.length) % diffRefPopoverState.items.length;
    syncDiffRefPopoverActiveOption();
    return true;
  }
  if (event.key === 'Enter' && open && diffRefPopoverState.activeIndex >= 0) {
    event.preventDefault();
    chooseDiffRefPopoverOption(diffRefPopoverState.activeIndex);
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
  if (diffRefPopoverState.input && controls.contains(diffRefPopoverState.input)) {
    renderDiffRefPopover(diffRefPopoverState.input, {showAll: false});
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

function diffRefResetButtonHtml(refs = repoDiffRefs(''), extraClass = '') {
  const isDefault = refs.from === 'HEAD' && refs.to === 'current';
  const resetHidden = isDefault ? ' hidden' : '';
  const label = esc(t('diff.ref.reset'));
  const className = `diff-ref-reset${extraClass ? ` ${extraClass}` : ''}`;
  return `<button type="button" class="${className}" data-diff-ref-reset${resetHidden} title="${label}" aria-label="${label}">${esc(t('common.reset'))}</button>`;
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
  scheduleShareTopologySnapshot('differ-refs');
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
  if (shareViewMode && !shareWriteMode) {
    if (fileExplorerChangesSelectedSession && sessions.includes(fileExplorerChangesSelectedSession)) {
      return fileExplorerChangesSelectedSession;
    }
    const explicitSession = fileExplorerExplicitSyncSessionTarget();
    if (explicitSession) {
      return explicitSession;
    }
    return '';
  }
  if (fileExplorerChangesSelectedSession && sessions.includes(fileExplorerChangesSelectedSession)) {
    return fileExplorerChangesSelectedSession;
  }
  const explicitSession = fileExplorerExplicitSyncSessionTarget();
  if (explicitSession) {
    fileExplorerChangesSelectedSession = explicitSession;
    return explicitSession;
  }
  const payloadSession = String(fileExplorerSessionFilesState.payload?.session || '');
  if (payloadSession && sessions.includes(payloadSession)) return payloadSession;
  return sessions[0] || '';
}

function emptySessionFilesPayload(session = '', loaded = true) {
  return {session, files: [], repos: [], refs_by_repo: {}, errors: [], from_ref: diffRefFrom, to_ref: diffRefTo, loaded};
}

function normalizedSessionFilesPayload(payload = {}, defaults = {}) {
  return {
    session: payload.session || defaults.session || '',
    files: Array.isArray(payload.files) ? payload.files : [],
    repos: Array.isArray(payload.repos) ? payload.repos : [],
    refs_by_repo: payload.refs_by_repo && typeof payload.refs_by_repo === 'object' ? payload.refs_by_repo : {},
    errors: Array.isArray(payload.errors) ? payload.errors : [],
    warnings: Array.isArray(payload.warnings) ? payload.warnings : [],
    from_ref: payload.from_ref || defaults.from_ref || diffRefFrom,
    to_ref: payload.to_ref || defaults.to_ref || diffRefTo,
    refreshing_elsewhere: payload.refreshing_elsewhere === true,
    loaded: defaults.loaded === false ? false : true,
  };
}

function sessionFilesPayloadIsRefreshingElsewhere(payload) {
  return payload?.refreshing_elsewhere === true;
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
  if (sessionFilesPayloadIsRefreshingElsewhere(payload)) return false;
  if (sessionFilesRepoRoots(payload).length > 0) return true;
  return !sessionFilesPayloadIsRefreshingElsewhere(payload) && sessionFilesPayloadIsRootlessEmpty(payload);
}

function sessionFilesPanelIsLoading(payload, files = null) {
  if (fileExplorerSessionFilesState.loading) return true;
  if (!sessionFilesPayloadIsRefreshingElsewhere(payload)) return false;
  return !sessionFilesPayloadHasVisibleDifferResult(payload, files);
}

function sessionFilesPayloadShouldPreserveCurrent(nextPayload) {
  const session = String(nextPayload?.session || '');
  const current = sessionFilesPayloadForDestination('finder');
  if (!session) return false;
  if (!sessionFilesPayloadIsLoadedForSession(current, session)) return false;
  if (sessionFilesPayloadIsRefreshingElsewhere(nextPayload)) return sessionFilesRepoRoots(current).length > 0;
  if (!sessionFilesPayloadIsRootlessEmpty(nextPayload)) return false;
  return sessionFilesRepoRoots(current).length > 0;
}

function switchFileExplorerChangesSession(session) {
  if (!session || !document.querySelector('.file-explorer-changes-panel')) return;
  rememberFileExplorerExplicitSyncSession(session);
  fileExplorerChangesSelectedSession = session;
  // Session selection belongs to Differ now. Keep the old semantic shape only for older share
  // viewers; it no longer drives any live panel mode.
  sharePublish('finder-mode', {mode: 'diff', session});
  scheduleFileExplorerActiveTabSync(session, {explicit: true});
  // Tabber is backed by transcript/activity data, so a pane-tab click changes only its current and
  // active row state. Preparing Differ payloads, rebuilding the tree, and forcing a session-files
  // request here made one tab activation perform the same Tabber state sync twice.
  if (itemInLayout(tabberItemId) && focusedPanelItem === tabberItemId) {
    scheduleTabberTreeLayoutStateSync();
    scheduleShareTopologySnapshot('finder-session');
    return;
  }
  const cached = fileExplorerSessionFilesCache.get(sessionFilesCacheKey(session));
  const cachedPayloadIsLoaded = sessionFilesPayloadIsLoadedForSession(cached?.payload, session);
  if (cachedPayloadIsLoaded) {
    setSessionFilesPayloadForDestination('finder', cached.payload);
    fileExplorerSessionFilesState.signature = cached.signature || sessionFilesPayloadSignatureForPayload(cached.payload);
  } else {
    const pendingPayload = emptySessionFilesPayload(session, false);
    setSessionFilesPayloadForDestination('finder', pendingPayload);
    fileExplorerSessionFilesState.signature = sessionFilesPayloadSignatureForPayload(pendingPayload);
  }
  setSessionFilesLoadingForDestination('finder', !cachedPayloadIsLoaded);
  renderFileExplorerChangesPanel(panelNodes.get(differItemId));
  fetchSessionFiles({destination: 'finder', session, silent: true, force: true, background: cachedPayloadIsLoaded});
  scheduleShareTopologySnapshot('finder-session');
}

function noteFileExplorerChangesSessionInteraction(session) {
  if (!isTmuxSession(session) || !sessions.includes(session)) return false;
  if (shareViewMode && !shareWriteMode && !applyingShareRemoteUiState) return false;
  rememberFileExplorerExplicitSyncSession(session);
  if (fileExplorerChangesSelectedSession === session) return false;
  fileExplorerChangesSelectedSession = session;
  if (document.querySelector('.file-explorer-changes-panel')) {
    switchFileExplorerChangesSession(session);
  }
  return true;
}

function sessionFilesPayloadForDestination(destination) {
  return fileExplorerSessionFilesState.payload;
}

function setSessionFilesPayloadForDestination(destination, payload, options = {}) {
  if (options.invalidateRequest !== false) fileExplorerSessionFilesState.guard.invalidate();
  fileExplorerSessionFilesState.payload = payload;
  updateFileExplorerSessionHighlightRows();
  if (
    destination === 'finder'
    && fileExplorerRootMode === 'sync'
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
    refreshing_elsewhere: sessionFilesPayloadIsRefreshingElsewhere(payload),
    from: payload?.from_ref || '',
    to: payload?.to_ref || '',
    errors: Array.isArray(payload?.errors) ? payload.errors : [],
    warnings: Array.isArray(payload?.warnings) ? payload.warnings : [],
    repos,
    files,
  });
}

function sessionFilesSignatureForDestination(destination) {
  return fileExplorerSessionFilesState.signature;
}

function setSessionFilesSignatureForDestination(destination, signature) {
  fileExplorerSessionFilesState.signature = signature;
}

function sessionFilesLoadingForDestination(destination) {
  return fileExplorerSessionFilesState.loading;
}

function setSessionFilesLoadingForDestination(destination, loading) {
  fileExplorerSessionFilesState.loading = loading;
}

function sessionFilesRenderOptions(options = {}) {
  return options.force === true || options.silent !== true ? {force: true} : {};
}

function sessionFilesPerfDetails(payload = {}, extra = {}) {
  const files = Array.isArray(payload?.files) ? payload.files.length : 0;
  return {nodes: sessionFilesRepoRoots(payload).length, rows: files, ...extra};
}

function renderSessionFilesDestination(destination, options = {}) {
  if (!fileExplorerSessionFilesPaneIsVisible()) {
    recordClientPerfCounter('sessionFilesRender', 0, {skipped: 1});
    return;
  }
  renderFileExplorerChangesPanels(options);
}

async function fetchSessionFiles(options = {}) {
  const destination = 'finder';
  const forceRefresh = options.force === true;
  const backgroundRefresh = options.background === true;
  if (!fileExplorerSessionFilesPaneIsVisible()) {
    recordClientPerfCounter('sessionFilesRefresh', 0, {skipped: 1});
    return false;
  }
  if (sessionFilesLoadingForDestination(destination) && !forceRefresh) return;
  const requestIsCurrent = fileExplorerSessionFilesState.guard.begin();
  const session = options.session || fileExplorerSessionFilesTargetSession();
  let shouldRender = options.silent !== true;
  if (!session) {
    const emptyPayload = emptySessionFilesPayload('', true);
    const signature = sessionFilesPayloadSignatureForPayload(emptyPayload);
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, emptyPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(emptyPayload));
    if (shouldRender) renderSessionFilesDestination(destination, sessionFilesRenderOptions(options));
    return;
  }
  if (!backgroundRefresh) setSessionFilesLoadingForDestination(destination, true);
  if (!options.silent) statusEl.textContent = t('status.changedFilesLoading');
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
    const payload = await apiFetchJson(`/api/session-files?${params.toString()}`);
    const nextPayload = normalizedSessionFilesPayload(payload, {session});
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    if (backgroundRefresh && sessionFilesPayloadShouldPreserveCurrent(nextPayload)) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
    fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {payload: nextPayload, signature});
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    if (!options.silent) statusOk(esc(tPlural('status.changedFilesLoaded', nextPayload.files.length)));
  } catch (err) {
    const issue = userMessageSnapshot(err, String(err?.message || err)).user_message;
    const nextPayload = {session, files: [], repos: [], refs_by_repo: {}, errors: [issue], from_ref: diffRefFrom, to_ref: diffRefTo, loaded: true};
    const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
    if (!requestIsCurrent()) return;
    shouldRender = shouldRender || signature !== sessionFilesSignatureForDestination(destination);
    setSessionFilesPayloadForDestination(destination, nextPayload, {invalidateRequest: false});
    setSessionFilesSignatureForDestination(destination, signature);
    recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
    if (!options.silent) statusErr(localizedHtml('status.changedFilesFailed', {error: userMessageText(err?.payload, String(err))}));
  } finally {
    const current = requestIsCurrent();
    const wasLoading = current && sessionFilesLoadingForDestination(destination);
    if (current && !backgroundRefresh) setSessionFilesLoadingForDestination(destination, false);
    if (current && (shouldRender || wasLoading) && fileExplorerSessionFilesPaneIsVisible()) {
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
  if (!fileExplorerSessionFilesPaneIsVisible()) {
    if (sessionFilesLoadingForDestination(destination)) setSessionFilesLoadingForDestination(destination, false);
    recordClientPerfCounter('sessionFilesRefresh', 0, {skipped: 1});
    return false;
  }
  const nextPayload = normalizedSessionFilesPayload(payload, {session, from_ref: request.from_ref, to_ref: request.to_ref});
  if (sessionFilesPayloadShouldPreserveCurrent(nextPayload)) return false;
  const signature = sessionFilesPayloadSignatureForPayload(nextPayload);
  const wasLoading = sessionFilesLoadingForDestination(destination);
  const shouldRender = wasLoading || signature !== sessionFilesSignatureForDestination(destination);
  if (wasLoading) setSessionFilesLoadingForDestination(destination, false);
  setSessionFilesPayloadForDestination(destination, nextPayload);
  setSessionFilesSignatureForDestination(destination, signature);
  fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {payload: nextPayload, signature});
  recordClientPerfCounter('sessionFilesRefresh', 0, sessionFilesPerfDetails(nextPayload));
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
  return tPlural(`relative.compact.${unit}`, Number(countText));
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

function sessionFileDiffText(item) {
  const addKind = item?.diff_tracked === false ? 'add-neutral' : 'add';
  return [
    Number.isFinite(Number(item?.added)) && Number(item.added) !== 0 ? {kind: addKind, text: `+${Number(item.added)}`} : null,
    Number.isFinite(Number(item?.removed)) && Number(item.removed) !== 0 ? {kind: 'remove', text: `-${Number(item.removed)}`} : null,
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
  const uploadOrder = item => item?.uploaded === true ? 1 : 0;
  const mode = fileExplorerTreeSortModeForView(view);
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
  const visibleFiles = fileExplorerDifferFiles(payload);
  for (const item of visibleFiles) {
    if (item?.repo) repos.add(item.repo);
  }
  if (!repos.size && visibleFiles.length && payload?.session) repos.add(payload.session);
  return Array.from(repos).sort();
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

function fileExplorerChangesCollapseToggleTitle() {
  return fileExplorerChangesAllReposCollapsed() ? t('changes.expandAll') : t('common.collapseAll');
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
  scheduleShareUiStatePublish();
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
  scheduleShareUiStatePublish();
}

function sessionFileIsDifferVisible(item) {
  return String(item?.status || 'M').toUpperCase() !== 'T';
}

function fileExplorerDifferFiles(payload = fileExplorerSessionFilesState.payload) {
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

function changesSummaryHtml(payload, files, session, loading, loaded) {
  if (loading) return changesLoadingHtml(session);
  if (!loaded) return t('state.notLoaded');
  const fileCount = Array.isArray(files) ? files.length : 0;
  const repoCount = changesRepoCount(payload, files);
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
  const directoryStatusCounts = new Map(); // normalizedDirPath → {added, deleted} file counts
  const countedStatusPaths = new Set();
  for (const item of sessionFiles) {
    const absPath = item.abs_path || (repoPath && item.path ? `${repoPath}/${item.path}` : item.path || '');
    if (!absPath) continue;
    sessionFilesMap.set(absPath, item);
    const status = sessionFileDisplayStatus(item);
    if ((status === 'A' || status === 'U' || status === '?' || status === 'D') && !countedStatusPaths.has(absPath)) {
      countedStatusPaths.add(absPath);
      let directory = normalizeDirectoryPath(dirnameOf(absPath));
      while (directory && pathIsInsideDirectory(directory, repoPath)) {
        const counts = directoryStatusCounts.get(directory) || {added: 0, deleted: 0};
        if (status === 'D') counts.deleted += 1;
        else counts.added += 1;
        directoryStatusCounts.set(directory, counts);
        if (directory === normalizeDirectoryPath(repoPath)) break;
        const parent = normalizeDirectoryPath(dirnameOf(directory));
        if (!parent || parent === directory) break;
        directory = parent;
      }
    }
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
      siblings.push({name: fileName, kind: 'file', mtime: item.mtime, size: item.size, missing: item.missing === true, changedFileMissingTime: sessionFileDatePlaceholderNeeded(item)});
    }
  }
  const topLevel = entriesByDir.get(normalizeDirectoryPath(repoPath)) || [];
  return {entries: topLevel, entriesByDir, sessionFilesMap, directoryStatusCounts};
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
  const treeRoot = repoPath === changesOutsideRepoKey ? '/' : repoPath;
  const {entries, entriesByDir, sessionFilesMap, directoryStatusCounts} = buildSessionFileTree(treeRoot, sessionFiles);
  const renderedRows = entries.length + Array.from(entriesByDir.values()).reduce((total, childEntries) => total + (Array.isArray(childEntries) ? childEntries.length : 0), 0);
  recordClientPerfCounter('sessionFilesRender', 0, {nodes: entriesByDir.size, rows: renderedRows});
  initializeDefaultCollapsedChangesFolders(entriesByDir);
  renderTreeChildren(container, treeRoot, entries, 0, {
    entriesByDir,
    sessionFilesMap,
    directoryStatusCounts,
    differMode: true,
    compact: options.compact,
    repoForDiffer: treeRoot,
    view: options.view || 'differ',
    treeSortMode: fileExplorerTreeSortModeForView(options.view || 'differ'),
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
  const prefix = t('state.modified');
  return timeText ? `${prefix}: ${name} ${timeText}` : `${prefix}: ${name}`;
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
  const view = normalizeFileExplorerView(options.view || 'differ');
  const regular = sortedSessionFiles(files, view);
  const payload = options.payload || {};
  const repoMap = repoPayloadByPath(payload);
  const groups = new Map(groupedSessionFiles(regular));
  if (options.includeEmptyRepoSections === true) {
    for (const repoInfo of Array.isArray(payload?.repos) ? payload.repos : []) {
      const repo = repoInfo?.repo || changesOutsideRepoKey;
      if (repoPayloadHasRenderableSection(repoInfo) && !groups.has(repo)) {
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
    section.classList.toggle(CLS.collapsed, collapsed);
    const repoInfo = repoMap.get(repo) || {};
    const repoLabel = repo === changesOutsideRepoKey ? t('changes.outsideRepo') : compactHomePath(repo);
    const hasGit = repo && repo !== changesOutsideRepoKey;
    // Update repo header button (small HTML string — not performance sensitive)
    let head = section.querySelector(':scope > .changes-repo-head');
    if (!head) {
      head = makeButton({className: 'changes-repo-head'});
      section.prepend(head);
    }
    head.dataset.changesRepoToggle = repo;
    head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    head.innerHTML = `${disclosureTriangleHtml(!collapsed, 'changes-repo-caret')}<span class="changes-repo-title">${esc(repoLabel)}</span>${changesRepoTotalsHtml(repoInfo, repoFiles)}`;
    // Update "Comparing FROM TO" refs row (per-repo, only for git repos)
    let refsRow = section.querySelector(':scope > .changes-repo-refs');
    if (hasGit && !collapsed) {
      if (!refsRow) {
        refsRow = document.createElement('div');
        refsRow.className = compact ? 'changes-repo-refs compact' : 'changes-repo-refs';
        head.after(refsRow);
      }
      const details = [
        options.loading === true ? `<span class="changes-repo-inline-loading">${changesLoadingHtml(payload.session || '')}</span>` : '',
        repoComparisonErrorHtml(repoInfo),
        changesRepoMetaHtml(repoInfo, {hideZero: compact}),
      ].filter(Boolean).join('');
      refsRow.innerHTML = `<div class="changes-repo-refs-main">${diffRefComparisonLineHtml(repo)}</div>${details ? `<div class="changes-repo-refs-detail">${details}</div>` : ''}`;
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
        renderChangedFileList(fileList, repo, repoFiles, {compact, view});
      } else if (options.loading === true) {
        fileList.innerHTML = '';
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
function fileExplorerTreeDateButtonHtml(extraClass = '', view = 'finder') {
  const surface = normalizeFileExplorerView(view);
  const mode = fileExplorerTreeDateModeForView(surface);
  const active = mode !== 'none';
  const classes = ['file-explorer-header-action', 'file-explorer-date-toggle', extraClass, active ? 'active' : ''].filter(Boolean).join(' ');
  return `<button type="button" class="${esc(classes)}" data-file-explorer-tree-dates data-file-explorer-view="${esc(surface)}" data-date-mode="${esc(mode)}" title="${esc(fileExplorerTreeDateModeTitle(mode))}" aria-label="${esc(fileExplorerTreeDateModeTitle(mode))}" aria-pressed="${active ? 'true' : 'false'}">${esc(fileExplorerTreeDateModeButtonLabel(mode))}</button>`;
}

function fileExplorerTreeSortSelectHtml(extraClass = '', view = 'finder') {
  const classes = ['file-explorer-sort-select', extraClass].filter(Boolean).join(' ');
  const surface = normalizeFileExplorerView(view);
  const mode = fileExplorerTreeSortModeForView(surface);
  return `<select class="${esc(classes)}" data-file-explorer-tree-sort data-file-explorer-view="${esc(surface)}" title="${esc(t('finder.toolbar.sort'))}" aria-label="${esc(t('finder.toolbar.sort'))}">
              <option value="az"${mode === 'az' ? ' selected' : ''}>${esc(t('finder.sort.az'))}</option>
              <option value="za"${mode === 'za' ? ' selected' : ''}>${esc(t('finder.sort.za'))}</option>
              <option value="newest"${mode === 'newest' ? ' selected' : ''}>${esc(t('common.sort.recent'))}</option>
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
  return `<label class="file-explorer-diff-session-control file-explorer-mode-files-diff-only changes-control">${esc(t('common.sessionLabel'))}: ${sessionFilesSessionSelectHtml(session, {className: 'file-explorer-diff-session-select'})}</label>`;
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
  // Legacy/test callers without an item still receive their requested compatibility view; live
  // panels always pass their fixed `view` explicitly and never render from this global value.
  const view = options.view || (fileExplorerMode === 'tabber' ? 'tabber' : 'differ');
  if (view === 'tabber') {
    return `
      <div class="changes-toolbar tabber-toolbar">
        ${tabberLookbackControlHtml()}
        ${fileExplorerTreeSortSelectHtml('changes-sort-select-compact', 'tabber')}
        ${fileExplorerTreeDateButtonHtml('changes-date-toggle', 'tabber')}
        ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
      </div>
      <div class="changes-groups"></div>`;
  }
  const payload = fileExplorerSessionFilesState.payload;
  const files = fileExplorerDifferFiles(payload);
  const loading = sessionFilesPanelIsLoading(payload, files);
  const loaded = payload.loaded === true;
  const session = payload.session || fileExplorerSessionFilesTargetSession();
  const errorHtml = (payload.errors || []).map(error => `<div class="changes-error">${esc(messageDescriptorText(error, String(error || '')))}</div>`).join('');
  const warningHtml = (payload.warnings || []).map(warning => `<div class="changes-warning">${esc(messageDescriptorText(warning, String(warning || '')))}</div>`).join('');
  const full = options.full === true || view === 'differ';
  const showEmptyRepoSections = full && !loading && !files.length && payloadHasRenderableRepoSections(payload);
  const empty = !loading && loaded && !files.length && !showEmptyRepoSections ? `<div class="changes-empty">${esc(t('changes.emptyModified'))}</div>` : '';
  if (full) {
    return `
      <div class="changes-toolbar file-explorer-diff-toolbar">
        <label class="changes-control">${esc(t('changes.sort'))} ${fileExplorerTreeSortSelectHtml('changes-sort-select', 'differ')}</label>
        ${fileExplorerTreeDateButtonHtml('changes-date-toggle', 'differ')}
        ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
        <button type="button" class="changes-refresh" data-session-files-refresh title="${esc(t('changes.refresh.title'))}" aria-label="${esc(t('changes.refresh.title'))}">${esc(t('common.reload'))}</button>
      </div>
      ${changesComparisonHeaderHtml(payload, files, {loading, inlineLoading: loading && payloadHasRenderableRepoSections(payload)})}
      ${errorHtml}
      ${warningHtml}
      ${empty ? empty : '<div class="changes-groups"></div>'}`;
  }
  const titleText = session ? t('changes.titleForSession', {session: sessionLabel(session) || session}) : t('brand.tab.changes');
  return `
    <div class="file-explorer-changes-head">
      <span class="changes-title">${esc(titleText)}</span>
      ${fileExplorerTreeSortSelectHtml('changes-sort-select changes-sort-select-compact', 'differ')}
      ${fileExplorerTreeDateButtonHtml('changes-date-toggle', 'differ')}
      ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
      <button type="button" class="changes-refresh" data-session-files-refresh title="${esc(t('changes.refresh.title'))}" aria-label="${esc(t('changes.refresh.title'))}">${esc(t('common.reload'))}</button>
      <button type="button" class="changes-close" data-file-explorer-changes-close title="${esc(t('changes.hide'))}" aria-label="${esc(t('changes.hide'))}">×</button>
    </div>
    ${changesComparisonHeaderHtml(payload, files, {loading, compact: true, inlineLoading: loading && payloadHasRenderableRepoSections(payload)})}
    ${errorHtml}
    ${warningHtml}
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
    for (const [key, value] of Object.entries(element.dataset)) add(domDataAttributeName(key), value);
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

function fileExplorerChangesPanelHtml(options = {}) {
  const view = options.view || 'differ';
  const staticHtml = fileExplorerChangesPanelStaticHtml({view});
  const files = fileExplorerDifferFiles();
  const loading = sessionFilesPanelIsLoading(fileExplorerSessionFilesState.payload, files);
  const groupsHtml = changesGroupsSnapshotHtml(files, {
    payload: fileExplorerSessionFilesState.payload,
    compact: view !== 'differ',
    loading,
    includeEmptyRepoSections: view === 'differ' && (!loading || payloadHasRenderableRepoSections(fileExplorerSessionFilesState.payload)),
    view,
  });
  return staticHtml.replace('<div class="changes-groups"></div>', groupsHtml);
}

// Compatibility hook for older fixtures and replay callers. The old in-panel three-way switch
// is intentionally gone: all live selection goes through independent layout items.
function fileExplorerModeSwitcherHtml() {
  return '';
}

// Compatibility entry point for older call sites and replay frames. New UI actions call the
// layout-owned openFileSurface transaction; this helper never changes a live global panel mode.
function setFileExplorerMode(mode) {
  const item = fileExplorerItemForView(mode);
  if (!item) return false;
  fileExplorerMode = normalizeFileExplorerMode(mode);
  writeStoredFileExplorerMode(fileExplorerMode);
  if (typeof openFileSurface === 'function') return openFileSurface(item) !== false;
  if (typeof openFileExplorerPane === 'function') return openFileExplorerPane(item) !== false;
  selectSession(item);
  return true;
}

function applyFileExplorerPanelView(panel, item = panel?.dataset?.panelItem) {
  const view = fileExplorerViewForItem(item) || 'finder';
  if (!panel) return view;
  // View is per-panel: a Finder, Differ, and Tabber can all exist at once without one global
  // mode class hiding or rebuilding the other two.
  panel.dataset.fileExplorerMode = view === 'finder' ? 'files' : view === 'differ' ? 'diff' : 'tabber';
  panel.dataset.fileExplorerView = view;
  syncFileExplorerDiffSessionControls();
  syncFileExplorerChangesCollapseButtons();
  return view;
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
  return active.closest?.('[data-session-files-session], [data-diff-ref-from], [data-diff-ref-to], [data-session-files-refresh], [data-file-explorer-tree-sort], [data-file-explorer-tree-dates], [data-file-tree-expand-collapse-all], [data-changes-folder-toggle], [data-changes-repo-toggle]') || null;
}

async function openChangedFileInDiff(path, ownerSession = '', status = '', repo = '', options = {}) {
  let item = options.item
    || (options.forceNewTab === true ? fileEditorCopyItemFor(path) : reusableFileEditorDiffPreviewItem(path));
  const normalizedStatus = String(status || '').toUpperCase();
  const openDiffMode = options.openMode !== 'edit';
  if (openDiffMode) setFileEditorDiffExpandUnchangedForItem(path, item, false);
  const initialMode = openDiffMode ? 'diff' : 'edit';
  setFileEditorViewMode(path, initialMode, item);
  // Use the payload's own FROM/TO for this file's repo so the diff matches what the panel shows,
  // even when the repo is not in diffRefsByRepo (e.g. a repo outside the active session's checkout).
  const payloadRepoRefs = (() => {
    const refsMap = fileExplorerSessionFilesState.payload?.refs_by_repo;
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
    const openedItem = await openFileInEditor(path, {name: basenameOf(path), session: ownerSession}, openOptions);
    if (openedItem) item = openedItem;
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
    const reason = current?.diffError || t(current?.kind !== 'text' ? 'editor.notTextFile' : 'editor.noGitDiffHistory');
    const panel = panelNodes.get(item);
    if (panel) setFileEditorPanelStatus(panel, t('editor.diffUnavailable', {error: reason}), 'warn');
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
    if (handleFileExplorerTreeToolbarChange(panel, event)) return;
    const sessionSelect = event.target.closest('[data-session-files-session]');
    if (sessionSelect && panel.contains(sessionSelect)) {
      fileExplorerChangesSelectedSession = sessionSelect.value;
      rememberFileExplorerExplicitSyncSession(fileExplorerChangesSelectedSession);
      switchFileExplorerChangesSession(fileExplorerChangesSelectedSession);
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
    if (diffRefInput && panel.contains(diffRefInput)) {
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
      return;
    }
    differTreeInteractionController.handleKeydown(event, panel);
  });
  panel.addEventListener('click', async event => {
    if (handleFileExplorerTreeToolbarAction(panel, event)) return;
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
      const controls = diffRefReset.closest('[data-diff-ref-controls]')
        || diffRefReset.parentElement?.querySelector?.('[data-diff-ref-controls]');
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
    beginFileDrag(payloadObject);
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('application/x-yolomux-file', JSON.stringify(payloadObject));
    event.dataTransfer.setData('text/plain', path);
    startFileDragPreview(event, [path], {kind: 'file', name: basenameOf(path)});
  });
  panel.addEventListener('dragend', () => {
    cancelDragOperationState();
  });
  // Single-clicks route through the shared tree controller. Modifier clicks keep Finder-style
  // multi-select without opening a diff; disclosure clicks toggle folders without opening files.
  panel.addEventListener('click', event => {
    if (event.__sharedTreeInteractionHandled) return;
    const row = event.target.closest('.file-tree-row[data-path]');
    if (!row || !panel.contains(row)) return;
    differTreeInteractionController.handleClick(event, panel, {row});
  });
  panel.addEventListener('contextmenu', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (fileRow && panel.contains(fileRow)) {
      event.preventDefault();
      const path = fileRow.dataset.path || fileRow.dataset.openChangeFile || '';
      showFileTreeContextMenu(fileRow, path, changedFileRowEntry(fileRow), event.clientX, event.clientY, {
        openInNewTabActions: [
          {
            label: t('contextmenu.openInDiffer'),
            action: () => openChangedFileInDiff(
              path,
              fileRow.dataset.openChangeSession || '',
              fileRow.dataset.openChangeStatus || '',
              fileRow.dataset.openChangeRepo || '',
              {userInitiated: true, openMode: 'diff'},
            ),
          },
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

function differTreeEventTargetsControl(event) {
  return Boolean(event?.target?.closest?.('[data-diff-ref-controls], [data-session-files-session], [data-file-explorer-tree-sort], [data-session-files-refresh], [data-file-explorer-tree-dates], [data-file-tree-expand-collapse-all], [data-changes-repo-toggle], input, select, textarea, button'));
}

function differTreeRowPath(row) {
  return row?.dataset?.path || row?.dataset?.openChangeFile || row?.dataset?.openChangeDirectory || '';
}

const differTreeInteractionController = createSharedTreeInteractionController({
  name: 'differ',
  rowSelector: '.file-tree-row[data-path]',
  allowRange: true,
  allowSelectAll: true,
  applyCurrentClasses: false,
  shouldIgnoreEvent: differTreeEventTargetsControl,
  isRowSelectable: row => Boolean(
    row?.closest?.('.file-explorer-changes-panel')
    && !row.dataset.tabberType
    && (row.dataset.openChangeFile || row.dataset.openChangeDirectory || row.dataset.changesFolderToggle),
  ),
  selectedIds: () => fileExplorerSelectedPaths,
  getLeadId: () => fileExplorerSelectionLead,
  setLeadId: id => { fileExplorerSelectionLead = id; },
  selectRow(row, id) {
    setFileExplorerSelectionPin(true);
    selectFileTreePath(id || differTreeRowPath(row));
  },
  selectRange(row, id) {
    setFileExplorerSelectionPin(true);
    selectFileTreeRange(row, id || differTreeRowPath(row), {clear: true});
  },
  selectFromClick(row, id, event) {
    return updateFileTreeSelectionFromClick(row, id || differTreeRowPath(row), event);
  },
  isExpanded: row => row?.dataset?.kind === 'dir' && row.getAttribute?.('aria-expanded') === 'true',
  setExpanded(row, expanded) {
    const key = row?.dataset?.changesFolderToggle || row?.dataset?.openChangeDirectory || row?.dataset?.path || '';
    if (!key || row?.dataset?.kind !== 'dir') return;
    if (expanded) changesFolderCollapsed.delete(key);
    else changesFolderCollapsed.add(key);
    writeStoredChangesFolderCollapsed();
    renderFileExplorerChangesPanels({force: true});
    scheduleShareUiStatePublish();
  },
  activateRow(row, event) {
    if (row?.dataset?.openChangeFile) {
      void openChangedFileInDiff(
        row.dataset.openChangeFile,
        row.dataset.openChangeSession || '',
        row.dataset.openChangeStatus || '',
        row.dataset.openChangeRepo || '',
        {userInitiated: true},
      );
      return;
    }
    if (row?.dataset?.kind === 'dir') {
      differTreeInteractionController.setExpanded(row.closest?.('.file-explorer-changes-panel') || document, row, !differTreeInteractionController.isExpanded(row));
    }
  },
});

const originalFileExplorerArrowNavForSharedTree = handleFileExplorerArrowNav;
handleFileExplorerArrowNav = event => {
  if (!fileExplorerKeyboardEventAllowsAction(event)) return false;
  const panel = event?.target?.closest?.('.file-explorer-panel')
    || event?.target?.closest?.('.file-explorer-changes-panel')
    || document.querySelector('.file-explorer-panel')
    || document;
  const view = fileExplorerViewForItem(panel?.dataset?.panelItem);
  if (view === 'tabber' && tabberTreeInteractionController.handleKeydown(event, panel)) return true;
  if (view === 'differ' && differTreeInteractionController.handleKeydown(event, panel)) return true;
  return originalFileExplorerArrowNavForSharedTree(event);
};

const originalSelectSessionForTabberTree = selectSession;
selectSession = async (session, options = {}) => {
  const result = await originalSelectSessionForTabberTree(session, options);
  if (itemInLayout(tabberItemId)) syncTabberTreeActiveSelection(document, {scrollIntoView: true});
  return result;
};

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
  appendContextMenuButton(menu, t('contextmenu.copyRelativePath'), () => copyChangedPath(rel || path, 'status.copiedRelativePath'), closeFileContextMenu);
  appendContextMenuButton(menu, t('contextmenu.copyFullPath'), () => copyChangedPath(path, 'status.copiedPath'), closeFileContextMenu);
  appendContextMenuButton(menu, t('contextmenu.expandInFinder', {name: displayName, finder: fileExplorerLabel()}), () => openChangedDirectoryInFinder(path), closeFileContextMenu);
  fileContextMenu.open(menu, x, y);
}

async function copyChangedPath(path, statusKey) {
  try {
    await copyTextToClipboard(path);
    statusEl.textContent = t(statusKey);
  } catch (error) {
    statusErr(localizedHtml('common.copyFailed', {error}));
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
        statusEl.textContent = t('status.expandedIn', {path, finder: fileExplorerLabel()});
        return;
      }
    }
    const opened = await openFileExplorerAt(path);
    if (!opened) return;
    selectFileTreePath(path);
    statusEl.textContent = t('status.expandedIn', {path, finder: fileExplorerLabel()});
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
    if (previewMediaKindForPath(name) === 'image') {
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
  if (!Number.isFinite(value)) message = t('validation.enterNumber');
  else if (Number.isFinite(min) && value < min) message = t('validation.minimum', {min});
  else if (Number.isFinite(max) && value > max) message = t('validation.maximum', {max});
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

function codexModelDefaultEffort(model) {
  const metadata = typeof settingChoiceMetadata === 'function' ? settingChoiceMetadata('yoagent.codex_model') : {};
  const entry = metadata[String(model || '')] || {};
  const effort = String(entry.default_effort || '').trim();
  return effort || '';
}

function settingPatchForPath(path, value) {
  const patch = settingPatch(path, value);
  if (path === 'yoagent.codex_model') {
    const defaultEffort = codexModelDefaultEffort(value);
    if (defaultEffort) patch.yoagent = {...(patch.yoagent || {}), codex_effort: defaultEffort};
  }
  return patch;
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
  const action = makeButton({
    label: t('pref.advisory.copyRsync'),
    onClick: event => {
      event.stopPropagation();
      copyTextToClipboard(command)
        .then(() => { statusEl.textContent = t('upload.copiedRsync'); })
        .catch(error => { statusErr(`${esc(t('common.copyFailed', {error}))}`); });
    },
  });
  const sizeText = options.sizeBytes ? t('upload.sizeText', {size: formatFileSize(options.sizeBytes)}) : '';
  return emitNotification('fileTransfer', {
    session: options.session, item: options.item || focusedPanelItem || fileExplorerItemId,
    title: t('common.rsyncLargeFiles'), lines: [
      t('upload.toastBody', {sizeText, cap: formatFileSize(uploadMaxBytes)}), command,
    ],
    actions: [action],
    countdownMs: 20000,
  }).inApp;
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
  saveSettingsPatch(settingPatchForPath(path, value), {
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
      statusEl.textContent = t('status.settingSaved', {path});
    })
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
}

function resetPreference(path) {
  const item = preferenceItemByPath(path);
  if (!item) return;
  saveSettingsPatch(settingPatch(path, preferenceDefault(path)), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => { statusEl.textContent = t('status.settingReset', {path}); })
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error: userMessageText(error, t('common.requestFailed'))})); });
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
      statusEl.textContent = t('status.preferencesResetAll');
    })
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error: userMessageText(error, t('common.requestFailed'))})); });
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
  scheduleShareUiStatePublish();
  saveSettingsPatch(settingPatch('appearance.preview_font_size', next))
    .then(() => { statusEl.textContent = t('status.previewFontSizeSaved'); })
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
}

// Finder, Differ, and Tabber share this one panel builder. The item determines the fixed view so
// they may coexist in separate panes without a global mode switch rebuilding one another.
function createFileExplorerPanel(item = finderItemId) {
  const view = fileExplorerViewForItem(item) || 'finder';
  const panel = document.createElement('article');
  panel.className = `panel file-explorer-panel file-explorer-${view}`;
  panel.id = panelDomId(item);
  panel.dataset.panelItem = item;
  // Refresh paths select panels by their fixed view. Keep this identity alongside the
  // item identity so Finder, Differ, and Tabber can coexist without global-mode drift.
  panel.dataset.fileExplorerView = view;
  const initialPath = fileExplorerRoot || homePath || '/';
  const label = fileExplorerItemLabel(item);
  const reloadButtonHtml = `<button type="button" class="changes-refresh file-explorer-refresh-cluster" data-file-explorer-refresh title="${esc(t('common.refresh'))}" aria-label="${esc(t('common.refresh'))}">${esc(t('common.reload'))}</button>`;
  const finderToolbarHtml = view === 'finder' ? `<div class="file-explorer-toolbar">
          <div class="file-explorer-toolbar-row file-explorer-primary-row">
            ${fileExplorerDiffSessionControlHtml(fileExplorerSessionFilesTargetSession())}
            <span class="file-explorer-toolbar-spacer"></span>
            ${reloadButtonHtml}
          </div>
          <div class="file-explorer-toolbar-row file-explorer-path-row">
            <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel" title="${esc(t('finder.toolbar.syncTitle'))}" aria-label="${esc(t('finder.toolbar.syncTitle'))}" aria-pressed="true">${esc(t('finder.toolbar.syncLabel'))}</button>
            <input class="file-explorer-path-inline" type="text" value="${esc(initialPath)}" spellcheck="false" aria-label="${esc(t('finder.toolbar.rootPath', {name: label}))}">
            <button type="button" class="path-copy-button file-explorer-path-copy-panel" title="${esc(t('finder.toolbar.copyPath'))}" aria-label="${esc(t('finder.toolbar.copyPath'))}"></button>
          </div>
          <div class="file-explorer-toolbar-row file-explorer-actions-row">
            <button type="button" class="file-explorer-header-action" data-file-explorer-new-file title="${esc(t('finder.toolbar.newFile'))}" aria-label="${esc(t('finder.toolbar.newFile'))}">+</button>
            <button type="button" class="file-explorer-header-action file-explorer-folder-action" data-file-explorer-new-folder title="${esc(t('finder.toolbar.newFolder'))}" aria-label="${esc(t('finder.toolbar.newFolder'))}"><span class="file-explorer-folder-icon" aria-hidden="true"></span></button>
            <span class="file-explorer-toolbar-spacer"></span>
            <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel" title="${esc(t('finder.toolbar.hidden'))}" aria-pressed="${fileExplorerShowHidden ? 'true' : 'false'}">.*</button>
            ${fileExplorerTreeSortSelectHtml('', 'finder')}
            <span class="file-explorer-date-controls">
              ${fileExplorerTreeDateButtonHtml('changes-date-toggle', 'finder')}
              ${fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle')}
            </span>
          </div>
        </div>` : view === 'differ' ? `<div class="file-explorer-toolbar"><div class="file-explorer-toolbar-row file-explorer-primary-row">${fileExplorerDiffSessionControlHtml(fileExplorerSessionFilesTargetSession())}</div></div>` : '';
  panel.innerHTML = panelFrameHtml({
    item,
    headClass: 'file-explorer-head',
    controlsHtml: virtualPanelInnerControlsHtml(item),
    headAfterTabsHtml: finderToolbarHtml,
    bodyClass: 'file-explorer-pane',
    bodyHtml: `<div class="file-explorer-tree-panel" role="tree" tabindex="0"></div>
        <div class="file-explorer-changes-resizer" data-file-explorer-changes-resizer title="${esc(t('finder.toolbar.resize'))}"></div>
        <div class="file-explorer-changes-panel" data-file-explorer-changes></div>`,
  });
  bindPanelShell(panel, item);
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
  panel.querySelector('.file-explorer-path-copy-panel')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    copyCurrentFileExplorerPath();
  });
  bindFileExplorerHeaderActions(panel);
  if (view === 'finder') {
    bindFileExplorerPathInput(panel.querySelector('.file-explorer-path-inline'));
  }
  if (view !== 'finder') bindFileExplorerChangesResizer(panel);
  applyFileExplorerPanelView(panel, item);
  if (view === 'finder') {
    renderFileExplorerRootModeControls();
    refreshFileExplorerPanelTree(panel);
  } else {
    renderFileExplorerChangesPanel(panel);
  }
  if (view === 'differ' && (!fileExplorerSessionFilesState.payload.loaded || fileExplorerSessionFilesState.payload.session !== fileExplorerSessionFilesTargetSession())) {
    if (clientPushCanSupplyData()) {
      if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    } else {
      fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true});
    }
  } else if (view === 'tabber') fetchTabberActivity();
  return panel;
}

async function refreshFileExplorerPanelTree(panel, options = {}) {
  const view = fileExplorerViewForItem(panel?.dataset?.panelItem) || 'finder';
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
  if (sortSelect && sortSelect.value !== fileExplorerTreeSortModeForView(view)) sortSelect.value = fileExplorerTreeSortModeForView(view);
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
  const view = fileExplorerViewForItem(panel.dataset.panelItem) || 'finder';
  if (view === 'finder') return;
  if (view === 'tabber') {
    if (options.force === true || !changes.querySelector('.changes-groups')) {
      replaceChangesStaticHtml(changes, fileExplorerChangesPanelStaticHtml({view}));
    }
    renderTabberTree(changes.querySelector('.changes-groups'));
    bindTabberPanel(panel);
    return;
  }
  if (options.force === true || !activeChangesControl(panel)) {
    renderChangesRoot(
      changes,
      fileExplorerChangesPanelStaticHtml({view, full: true}),
      fileExplorerDifferFiles(),
      {
        payload: fileExplorerSessionFilesState.payload,
        compact: false,
        loading: sessionFilesPanelIsLoading(fileExplorerSessionFilesState.payload, fileExplorerDifferFiles()),
        includeEmptyRepoSections: true,
        view,
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
  if (!itemInLayout(differItemId) && !itemInLayout(tabberItemId)) return;
  const requestedView = options.view ? normalizeFileExplorerView(options.view) : '';
  for (const panel of document.querySelectorAll('.file-explorer-panel')) {
    if (requestedView && fileExplorerViewForItem(panel.dataset.panelItem) !== requestedView) continue;
    renderFileExplorerChangesPanel(panel, options);
  }
  if (shareViewMode && typeof scheduleShareScrollRestoreByKey === 'function') {
    scheduleShareScrollRestoreByKey('finder:differ');
  }
}

// Fixed file surfaces may mount before their asynchronous payload arrives and later become active
// through an active-only Dockview update. That update deliberately skips the general attached-panel
// render pass, so refresh the selected surface here instead of depending on the retired global mode
// switch to rebuild all three panels.
function activateFileExplorerSurface(item) {
  const view = fileExplorerViewForItem(item);
  const panel = panelNodes.get(item);
  if (!view || !panel) return false;
  if (view === 'finder') {
    refreshFileExplorerPanelTree(panel, {preserveExpanded: true, preserveScroll: true});
    return true;
  }
  renderFileExplorerChangesPanel(panel, {force: true});
  if (view === 'tabber') {
    fetchTabberActivity();
    return true;
  }
  const session = fileExplorerSessionFilesTargetSession();
  if (!sessionFilesPayloadIsLoadedForSession(fileExplorerSessionFilesState.payload, session)) {
    fetchSessionFiles({destination: 'finder', session, silent: true});
  }
  return true;
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

function fileEditorToolbarHtml(item) {
  return `
      <div class="file-editor-toolbar" role="toolbar" aria-label="${esc(t('editor.toolbar.aria'))}" hidden>
        ${actionRowHtml({
          className: 'file-editor-toolbar-zone file-editor-toolbar-left',
          actions: [
            {
              className: 'file-editor-gutter-panel',
              action: 'editor-toggle-gutter',
              label: '#',
              title: t('editor.toggleLineNumbers'),
              ariaLabel: t('editor.toggleLineNumbers'),
              hidden: true,
            },
            {
              className: 'file-editor-wrap-panel',
              action: 'editor-toggle-wrap',
              html: '<span class="file-editor-icon file-editor-icon-wrap" aria-hidden="true"></span>',
              title: t('editor.toggleWordWrap'),
              ariaLabel: t('editor.toggleWordWrap'),
              hidden: true,
            },
            {
              className: 'file-editor-diff-panel',
              action: 'editor-diff',
              label: t('brand.tab.changes'),
              title: t('common.diff'),
              ariaLabel: t('common.diff'),
              hidden: true,
            },
            {
              className: 'file-editor-diff-expand-panel',
              action: 'editor-diff-expand',
              label: '↕',
              title: t('editor.diffExpand'),
              ariaLabel: t('editor.diffExpand'),
              pressed: fileEditorDiffExpandUnchangedForItem(item),
              hidden: true,
            },
            {
              kind: 'custom',
              tagName: 'span',
              className: 'file-editor-diff-ref-panel',
              hidden: true,
              html: diffRefControlsHtml({compact: true}),
            },
            {
              kind: 'custom',
              tagName: 'span',
              className: 'file-editor-path',
              attributes: {dir: 'ltr'},
            },
          ],
        })}
        <div class="file-editor-toolbar-zone file-editor-toolbar-center">
          ${segmentedControlHtml({
            className: 'file-editor-preview-font-panel',
            role: 'group',
            ariaLabel: t('common.previewFontSize'),
            hidden: true,
            items: [
              {
                action: 'editor-preview-font-step',
                dataset: {editorPreviewFontStep: '-1'},
                label: 'A-',
                title: t('editor.previewFont.decrease'),
                ariaLabel: t('editor.previewFont.decrease'),
              },
              {
                kind: 'custom',
                tagName: 'span',
                className: 'file-editor-preview-font-value',
                attributes: {'aria-live': 'polite'},
                label: String(editorPreviewFontSize),
              },
              {
                action: 'editor-preview-font-step',
                dataset: {editorPreviewFontStep: '1'},
                label: 'A+',
                title: t('editor.previewFont.increase'),
                ariaLabel: t('editor.previewFont.increase'),
              },
            ],
          })}
        </div>
        ${actionRowHtml({
          className: 'file-editor-toolbar-zone file-editor-toolbar-right',
          actions: [
            {
              className: 'file-editor-theme-panel',
              action: 'editor-theme',
              html: '<span class="file-editor-icon file-editor-icon-theme" aria-hidden="true"></span>',
              title: t('editor.theme'),
              ariaLabel: t('editor.theme'),
            },
            {
              kind: 'custom',
              tagName: 'span',
              className: 'file-editor-mode-control file-editor-mode-control-panel',
              role: 'group',
              ariaLabel: t('editor.mode.aria'),
              hidden: true,
              html: [
                toolbarButtonHtml({
                  action: 'editor-mode',
                  dataset: {editorMode: 'edit'},
                  html: '<span class="file-editor-icon file-editor-icon-edit" aria-hidden="true"></span>',
                  title: t('common.edit'),
                  ariaLabel: t('common.edit'),
                }),
                toolbarButtonHtml({
                  action: 'editor-mode',
                  dataset: {editorMode: 'preview'},
                  html: '<span class="file-editor-icon file-editor-icon-eye" aria-hidden="true"></span>',
                  title: t('common.preview'),
                  ariaLabel: t('common.preview'),
                }),
                toolbarButtonHtml({
                  action: 'editor-mode',
                  dataset: {editorMode: 'split'},
                  html: '<span class="file-editor-icon file-editor-icon-split" aria-hidden="true"></span>',
                  title: t('editor.mode.split'),
                  ariaLabel: t('editor.mode.split'),
                }),
                toolbarButtonHtml({
                  className: 'file-editor-popout-preview-panel',
                  action: 'editor-popout-preview',
                  html: '<span class="file-editor-icon file-editor-icon-popout-preview" aria-hidden="true"></span>',
                  title: t('editor.popoutPreview'),
                  ariaLabel: t('editor.popoutPreview'),
                  hidden: true,
                }),
              ].join(''),
            },
            {
              kind: 'separator',
              className: 'file-editor-toolbar-separator',
              dataset: {editorToolbarSeparator: 'mode'},
              hidden: true,
            },
            {
              className: 'file-editor-find-panel',
              action: 'editor-find',
              html: '<span class="file-editor-icon file-editor-icon-find" aria-hidden="true"></span>',
              title: t('editor.findInFile', {shortcut: appShortcutText('F')}),
              ariaLabel: t('editor.findInFileAria'),
              pressed: false,
              hidden: true,
            },
            {
              className: 'file-editor-blame-panel',
              action: 'editor-blame',
              html: '<span class="file-editor-icon file-editor-icon-blame" aria-hidden="true"></span>',
              title: t('editor.blame.toggle'),
              ariaLabel: t('editor.blame.toggle'),
              pressed: fileEditorBlameEnabled,
              hidden: true,
            },
            {
              kind: 'separator',
              className: 'file-editor-toolbar-separator',
              dataset: {editorToolbarSeparator: 'tools'},
              hidden: true,
            },
            {
              className: 'file-editor-reload-panel',
              action: 'editor-reload',
              label: t('common.reload'),
              title: t('editor.reloadFromDisk'),
              ariaLabel: t('editor.reloadFromDisk'),
              hidden: true,
            },
            {
              kind: 'separator',
              className: 'file-editor-toolbar-separator',
              dataset: {editorToolbarSeparator: 'theme'},
              hidden: true,
            },
            {
              className: 'file-editor-save-panel',
              action: 'editor-save',
              html: '<span class="file-editor-icon file-editor-icon-save" aria-hidden="true"></span>',
              title: t('common.save'),
              ariaLabel: t('editor.saveFile'),
              hidden: readOnlyMode,
            },
          ],
        })}
      </div>`;
}

function setFileEditorLocalizedLabel(panel, selector, key, options = {}) {
  const node = panel?.querySelector?.(selector);
  if (!node) return;
  const label = t(key);
  if (options.text === true) node.textContent = label;
  if (options.title !== false) node.title = label;
  if (options.aria !== false) node.setAttribute('aria-label', label);
}

function relocalizeFileEditorPanel(panel, item) {
  if (!panel) return false;
  const diffRefPanel = panel.querySelector('.file-editor-diff-ref-panel');
  if (diffRefPanel) {
    delete diffRefPanel.dataset.diffRefRepoRendered;
    delete diffRefPanel.dataset.diffRefPathRendered;
    delete diffRefPanel.dataset.diffRefHistoryRendered;
  }
  renderFileEditorPanel(panel, item, {updateActiveFile: false, captureViewState: false});
  reconfigureCodeMirrorPanelLocale(panel);
  relocalizeVirtualPanelChrome(panel);
  setFileEditorLocalizedLabel(panel, '.file-editor-panel-close', 'editor.closePane');
  setFileEditorLocalizedLabel(panel, '.file-editor-toolbar', 'editor.toolbar.aria', {title: false});
  setFileEditorLocalizedLabel(panel, '.file-editor-mode-control-panel', 'editor.mode.aria', {title: false});
  setFileEditorLocalizedLabel(panel, '.file-editor-preview-font-panel', 'common.previewFontSize', {title: false});
  setFileEditorLocalizedLabel(panel, '.file-editor-popout-preview-panel', 'editor.popoutPreview');
  setFileEditorLocalizedLabel(panel, '.file-editor-reload-panel', 'editor.reloadFromDisk');
  setFileEditorLocalizedLabel(panel, '.file-editor-reload-panel', 'common.reload', {text: true, title: false, aria: false});
  setFileEditorLocalizedLabel(panel, '.file-editor-diff-expand-panel', 'editor.diffExpand');
  setFileEditorLocalizedLabel(panel, '.file-editor-save-panel', 'common.save', {aria: false});
  setFileEditorLocalizedLabel(panel, '.file-editor-save-panel', 'editor.saveFile', {text: false, title: false});
  const findPanel = panel.querySelector('.file-editor-preview-find-panel');
  if (findPanel) {
    findPanel.setAttribute('aria-label', t('preview.find'));
    const input = findPanel.querySelector('input');
    if (input) {
      input.placeholder = t('preview.find');
      input.setAttribute('aria-label', t('preview.find'));
    }
    findPanel.querySelector('[data-preview-find-move="-1"]')?.setAttribute('aria-label', t('preview.find.previous'));
    findPanel.querySelector('[data-preview-find-move="1"]')?.setAttribute('aria-label', t('preview.find.next'));
    findPanel.querySelector('[data-preview-find-close]')?.setAttribute('aria-label', t('preview.find.close'));
  }
  return true;
}

function createFileEditorPanel(item) {
  const path = fileItemPath(item);
  const panel = document.createElement('article');
  panel.className = 'panel file-editor-panel';
  panel.dataset.filePath = path;
  panel.dataset.layoutItem = item;
  panel.innerHTML = panelFrameHtml({
    item,
    headClass: 'file-editor-panel-head',
    controlsHtml: `<div class="file-editor-panel-actions file-editor-frame-actions">
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
        </div>`,
    afterHeadHtml: fileEditorToolbarHtml(item),
    bodyClass: 'file-editor-panel-body',
    bodyHtml: `<div class="file-editor-content">
          <div class="file-editor-codemirror-panel" hidden></div>
          <pre class="file-editor-raw-panel" hidden><code></code></pre>
          <div class="file-editor-preview-pane-panel markdown-body" hidden></div>
          <div class="file-editor-find-overview" hidden aria-hidden="true"></div>
          <form class="file-editor-preview-find-panel" hidden role="search" aria-label="${esc(t('preview.find'))}">
            <input type="search" placeholder="${esc(t('preview.find'))}" aria-label="${esc(t('preview.find'))}" autocomplete="off">
            <span class="file-editor-preview-find-count" aria-live="polite"></span>
            <button type="button" data-preview-find-move="-1" aria-label="${esc(t('preview.find.previous'))}">↑</button>
            <button type="button" data-preview-find-move="1" aria-label="${esc(t('preview.find.next'))}">↓</button>
            <button type="button" data-preview-find-close aria-label="${esc(t('preview.find.close'))}">×</button>
          </form>
          <div class="file-editor-image-panel" hidden></div>
        </div>
        <div class="file-editor-status-panel"><span class="file-editor-status-message"></span><span class="file-editor-count-status"></span><span class="file-editor-cursor-status"></span></div>`,
  });
  bindPanelShell(panel, item);
  panel.addEventListener('click', event => {
    if (event.defaultPrevented) return;
    if (event.target?.closest?.('button, a, input, textarea, select, [data-diff-ref-input]')) return;
    scheduleFileExplorerActiveFileReveal(path);
  });
  delegate(panel, 'pointerdown', 'button', event => event.stopPropagation());
  bindActionDispatcher(panel, {
    'editor-save': () => saveFileEditor(path, panel),
    'editor-reload': () => reloadOpenFileFromDisk(path),
    'editor-mode': (_event, target) => {
      const mode = target?.dataset?.editorMode;
      if (!editorViewModes.has(mode)) return;
      if (mode === 'diff' && editorViewModeFor(path, item) !== 'diff') {
        enterFileEditorDiffMode(path, panel, item);
        return;
      }
      setFileEditorViewMode(path, mode, item);
      renderFileEditorPanel(panel, item);
    },
    'editor-toggle-gutter': () => toggleEditorLineNumbers(),
    'editor-preview-font-step': (_event, target) => {
      setEditorPreviewFontSize(editorPreviewFontSize + Number(target?.dataset?.editorPreviewFontStep || 0));
    },
    'editor-toggle-wrap': () => toggleEditorWrap(),
    'editor-find': async () => {
      await toggleEditorFind(panel);
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), openFiles.get(path), panel);
    },
    'editor-blame': (_event, target) => {
      if (target?.disabled) return;
      toggleFileEditorBlame();  // inline git blame on/off (persisted, fetches + re-renders editors)
    },
    'editor-diff': (_event, target) => {
      if (target?.disabled || target?.hidden) return;
      const nextMode = editorViewModeFor(path, item) === 'diff' ? 'edit' : 'diff';
      if (nextMode === 'diff') {
        enterFileEditorDiffMode(path, panel, item);
        return;
      }
      setFileEditorViewMode(path, nextMode, item);
      renderFileEditorPanel(panel, item);
    },
    'editor-diff-expand': () => {
      toggleFileEditorDiffExpandUnchangedForItem(path, item);  // show all context vs collapse unchanged runs for this editor
    },
    'editor-popout-preview': () => {
      if (openFilePreviewPopout(path, panel)) {
        setFileEditorViewMode(path, 'edit', item);
        renderFileEditorPanel(panel, item);
      }
    },
    'editor-theme': () => cycleEditorThemeMode({includeVanilla: true}),
  }, {skipDisabled: false});
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
  const previewPane = panel.querySelector('.file-editor-preview-pane-panel');
  const previewFindPanel = panel.querySelector('.file-editor-preview-find-panel');
  previewFindPanel?.addEventListener('submit', event => event.preventDefault());
  previewFindPanel?.addEventListener('input', event => {
    if (event.target.matches('input')) previewFindApplyQuery(panel, event.target.value);
  });
  previewFindPanel?.addEventListener('click', event => {
    const move = event.target.closest('[data-preview-find-move]');
    if (move) {
      const state = previewFindStateForHost(panel, true);
      previewFindSelectMatch(panel, state.index + Number(move.dataset.previewFindMove || 0));
      return;
    }
    if (event.target.closest('[data-preview-find-close]')) {
      closePreviewFind(panel);
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), openFiles.get(path), panel);
    }
  });
  previewFindPanel?.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closePreviewFind(panel);
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), openFiles.get(path), panel);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const state = previewFindStateForHost(panel, true);
      previewFindSelectMatch(panel, state.index + (event.shiftKey ? -1 : 1));
    }
  });
  previewPane?.addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, fileEditorPreviewScrollSyncSource(panel)));
  previewPane?.addEventListener('toggle', event => {
    if (event.target?.matches?.('details')) scheduleFileEditorPreviewLayoutSync(panel);
  }, true);
  previewPane?.addEventListener('load', event => {
    if (event.target?.matches?.('img.markdown-preview-image, img.mermaid-preview-image')) scheduleFileEditorPreviewLayoutSync(panel);
  }, true);
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
  if (typeof disconnectPreviewZoomSurface === 'function') disconnectPreviewZoomSurface(imagePane, {resetClasses: true});
}

function fileEditorImageVersion(state) {
  return String(state?.mtime_ns || state?.mtime || state?.size || 0);
}

function renderFileEditorImagePane(imagePane, path, state, status) {
  if (!imagePane) return;
  const version = fileEditorImageVersion(state);
  const sameImage = imagePane.dataset.imagePath === path && imagePane.dataset.imageVersion === version;
  const zoomOptions = previewZoomOptionsForKind('imagePane', {path});
  let img = sameImage ? imagePane.querySelector('img.file-editor-image') : null;
  if (!img) {
    disconnectFileEditorImageObserver(imagePane);
    imagePane.replaceChildren();
    img = document.createElement('img');
    img.className = 'file-editor-image';
    img.alt = path;
    img.loading = 'eager';
    img.decoding = 'async';
    img.onload = () => {
      applyPreviewZoomSurface(imagePane, img, zoomOptions);
      status(`${img.naturalWidth}x${img.naturalHeight}`, '');
    };
    img.onerror = () => {
      disconnectFileEditorImageObserver(imagePane);
      imagePane.replaceChildren(fileEditorEmptyState(
        t('preview.image.loadFailed'),
        t('preview.image.loadFailedDetail', {size: formatFileSize(MAX_FILE_PREVIEW_BYTES)}),
      ));
      status(t('preview.image.loadFailedStatus'), 'error');
    };
    img.src = rawFileUrl(path, {v: version});
    imagePane.dataset.imagePath = path;
    imagePane.dataset.imageVersion = version;
    installPreviewZoomSurface(imagePane, img, zoomOptions);
    status(t('common.loading'), '');
  }
  if (!imagePane.querySelector(':scope > .file-editor-preview-zoom-viewport')) installPreviewZoomSurface(imagePane, img, zoomOptions);
  else applyPreviewZoomSurface(imagePane, img, zoomOptions);
}

function requestFileEditorPanelFocus(item) {
  if (!autoFocusCanFollowCursor()) return;
  if (isFileEditorItem(item)) pendingFileEditorFocus.add(item);
}

function scheduleFileEditorPanelViewStateCapture(item, panel) {
  schedulePaneViewStateCapture(item, panel);
}

function focusFileEditorPanelIfReady(panel, item) {
  if (!autoFocusCanFollowCursor()) {
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

function fileEditorVisibleLineNumber(view) {
  const doc = view?.state?.doc;
  if (!doc || typeof doc.lineAt !== 'function') return 0;
  const ranges = Array.isArray(view.visibleRanges) ? view.visibleRanges : [];
  const visibleFrom = Number(ranges[0]?.from);
  if (Number.isFinite(visibleFrom)) {
    try { return Math.max(1, Math.floor(Number(doc.lineAt(visibleFrom)?.number) || 0)); } catch (_) {}
  }
  const blocks = Array.from(view.viewportLineBlocks || []);
  const blockFrom = Number(blocks[0]?.from);
  if (Number.isFinite(blockFrom)) {
    try { return Math.max(1, Math.floor(Number(doc.lineAt(blockFrom)?.number) || 0)); } catch (_) {}
  }
  const head = Number(view.state?.selection?.main?.head);
  if (Number.isFinite(head)) {
    try { return Math.max(1, Math.floor(Number(doc.lineAt(head)?.number) || 0)); } catch (_) {}
  }
  return 0;
}

function captureFileEditorPanelViewState(item, panel) {
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!isFileEditorItem(item) || !view || !scrollDOM) return;
  if (shareViewMode && !shareWriteMode && !applyingShareRemoteScroll && !applyingShareRemoteUiState) return;
  if (fileEditorViewState.has(item) && !fileEditorPanelViewStateCaptureHasLayout(panel, scrollDOM)) return;
  const selection = view.state?.selection?.main;
  fileEditorViewState.set(item, {
    scrollTop: scrollDOM.scrollTop || 0,
    scrollLeft: scrollDOM.scrollLeft || 0,
    anchor: Number(selection?.anchor || 0),
    head: Number(selection?.head || selection?.anchor || 0),
    line: fileEditorVisibleLineNumber(view),
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

function restoreFileEditorPanelViewState(item, panel, options = {}) {
  const state = fileEditorViewState.get(item);
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!state || !view || !scrollDOM) return;
  // An ordinary rerender (autosave chrome, watch/SSE refresh, session metadata) can finish after
  // CodeMirror has already advanced a Shift+Arrow selection and scrolled to follow it. The focused
  // live view is newer than the cached tab-switch snapshot, so refresh that shared snapshot instead
  // of replaying stale cursor/scroll state into the editor. Explicit disk replacement captures a
  // pre-reload snapshot and opts back into restoration with restoreFocused.
  if (options.restoreFocused !== true && view.hasFocus && panel?.isConnected) {
    captureFileEditorPanelViewState(item, panel);
    return;
  }
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

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
  addRoot(fileState.get(path)?.gitRoot);
  addRoot(fileState.get(path)?.diffRepo);
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

function sessionFilesRequestQueryString(destination = 'differ') {
  return destination === 'finder'
    ? `from=HEAD&to=current${(typeof fileExplorerVisibleRepositoryPaths === 'function' ? fileExplorerVisibleRepositoryPaths() : [])
      .map(path => `&repo_path=${encodeURIComponent(path)}`).join('')}`
    : `${diffRefQueryString()}${sessionFilesRefsQuery()}`;
}

function sessionFilesRequestForDestination(destination, sessionOverride = '') {
  const params = new URLSearchParams(sessionFilesRequestQueryString(destination));
  let repoRefs = null;
  const refs = params.get('refs');
  if (refs) {
    const parsed = safeJsonParse(refs, null);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) repoRefs = parsed;
  }
  const request = {
    session: sessionOverride || (destination === 'finder' ? fileExplorerFinderTargetSession() : fileExplorerSessionFilesTargetSession()),
    hours: 24,
    from_ref: params.get('from') || 'HEAD',
    to_ref: params.get('to') || 'current',
    repo_refs: repoRefs,
  };
  const repoPaths = destination === 'finder' ? params.getAll('repo_path') : [];
  if (repoPaths.length) request.repo_paths = repoPaths;
  return request;
}

function clientSessionFilesWatchRequests() {
  const candidates = [];
  if (fileExplorerTreePaneIsVisible()) candidates.push(sessionFilesRequestForDestination('finder'));
  if (fileExplorerSessionFilesPaneIsVisible()) candidates.push(sessionFilesRequestForDestination('differ'));
  const requests = [];
  const seen = new Set();
  for (const request of candidates) {
    if (!request.session) continue;
    const key = sessionFilesRequestKey(request, request.session);
    if (seen.has(key)) continue;
    seen.add(key);
    requests.push(request);
  }
  return requests;
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

function normalizedSessionFilesRepoPaths(repoPaths) {
  if (!Array.isArray(repoPaths)) return [];
  return [...new Set(repoPaths.map(path => normalizeDirectoryPath(path || '')).filter(Boolean))].sort();
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
    repo_paths: normalizedSessionFilesRepoPaths(request.repo_paths),
  };
}

function sessionFilesRequestKey(request = {}, sessionFallback = '') {
  const normalized = normalizedSessionFilesRequest(request, sessionFallback);
  return JSON.stringify(normalized);
}

function sessionFilesRequestDescriptor(request = {}, sessionFallback = '') {
  const normalized = normalizedSessionFilesRequest(request, sessionFallback);
  const repositoryRefs = Object.entries(normalized.repo_refs)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([repo, refs]) => [repo, String(refs.from || ''), String(refs.to || '')]);
  return sha256HexOfString(JSON.stringify([
    'session-files-request', normalized.session, normalized.hours,
    normalized.from_ref, normalized.to_ref, repositoryRefs,
  ]));
}

function sessionFilesDescriptorForDestination(destination, request) {
  const descriptor = String(sessionFilesPayloadForDestination(destination)?.cache?.request_descriptor || '');
  return /^[0-9a-f]{64}$/.test(descriptor)
    ? descriptor
    : sessionFilesRequestDescriptor(request, request.session);
}

function sessionFilesRequestMatchesDestination(request = {}, session = '', destination = 'differ') {
  if (!request || typeof request !== 'object') return false;
  if (destination === 'finder' && !fileExplorerTreePaneIsVisible()) return false;
  if (destination === 'differ' && !fileExplorerSessionFilesPaneIsVisible()) return false;
  const current = sessionFilesRequestForDestination(destination);
  return sessionFilesRequestKey(request, session) === sessionFilesRequestKey(current, session);
}

function sessionFilesPushRequestMatchesCurrent(request = {}, session = '') {
  return ['finder', 'differ'].some(destination => sessionFilesRequestMatchesDestination(request, session, destination));
}

function sessionFilesDestinationsForRequest(request = {}, session = '') {
  return ['finder', 'differ'].filter(destination => sessionFilesRequestMatchesDestination(request, session, destination));
}

function sessionFilesCacheKey(session, destination = 'differ') {
  return `${String(session || '')}\x1f${sessionFilesRequestQueryString(destination)}`;
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
  const state = fileState.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return [];
  const suggestions = defaultDiffRefSuggestions();
  const seen = new Set(suggestions.map(item => item.ref));
  for (const item of normalizedFileGitHistory(state.gitHistory)) {
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
  const state = fileState.get(path);
  if (!path || !fileStateHasUsefulGitHistory(state)) return 'none';
  return normalizedFileGitHistory(state.gitHistory).map(item => `${item.ref}:${item.date || ''}`).join('|');
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

function historicalDiffRefControlsHtml(state) {
  const fromRef = String(state?.diffPinnedFromRef || state?.diffFromRef || '');
  const toRef = String(state?.diffPinnedToRef || state?.diffToRef || '');
  const comparison = state?.historicalComparisonKind === 'merge-first-parent'
    ? `<span class="diff-ref-historical-note">${esc(t('gitDiff.firstParent'))}</span>`
    : '';
  return `<span class="diff-ref-controls compact historical" data-diff-ref-controls data-diff-ref-historical>
    <span class="diff-ref-control">${esc(t('diff.ref.from'))} <span class="diff-ref-static-value" title="${esc(fromRef)}">${esc(fromRef.slice(0, 9))}</span></span>
    <span class="diff-ref-control">${esc(t('diff.ref.to'))} <span class="diff-ref-static-value" title="${esc(toRef)}">${esc(toRef.slice(0, 9))}</span></span>
    ${comparison}
  </span>`;
}

function diffRefResetButtonHtml(refs = repoDiffRefs(''), extraClass = '') {
  const isDefault = refs.from === 'HEAD' && refs.to === 'current';
  const resetHidden = isDefault ? ' hidden' : '';
  const label = esc(t('diff.ref.reset'));
  const className = `diff-ref-reset${extraClass ? ` ${extraClass}` : ''}`;
  return `<button type="button" class="${className}" data-diff-ref-reset${resetHidden} title="${label}" aria-label="${label}">${esc(t('common.reset'))}</button>`;
}

function sessionFilesCacheForDestination(destination = 'differ') {
  return destination === 'finder' ? fileExplorerFinderSessionFilesCache : fileExplorerSessionFilesCache;
}

function invalidateSessionFilesCaches(destination = 'differ') {
  sessionFilesCacheForDestination(destination).clear();
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
  invalidateSessionFilesCaches('differ');
  for (const state of fileState.values()) {
    if (!state || state.kind !== 'text') continue;
    state.diffLoaded = false;
    state.diffUnavailable = false;
    state.diffError = '';
    state.diffPinnedFromRef = '';
    state.diffPinnedToRef = '';
  }
  renderFileExplorerChangesPanels({force: true, view: 'differ'});
  fetchSessionFiles({destination: 'differ', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true, freshGit: true});
  for (const path of fileState.keys()) renderOpenFilePath(path);
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

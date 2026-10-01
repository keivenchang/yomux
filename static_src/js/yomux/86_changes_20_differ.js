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
    const absPath = normalizeDirectoryPath(item.abs_path || (repoPath && item.path ? `${repoPath}/${item.path}` : item.path || ''));
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
      // `deleted` is the DISPLAY classification from `sessionFileDisplayStatus`, kept under its own
      // name so it cannot be confused with `missing` ("not on disk") elsewhere in the payload: a
      // file created and then removed is missing but still displays A.
      siblings.push({
        name: fileName,
        display_name: item.old_path ? `${item.old_path} → ${item.path}` : '',
        kind: 'file',
        mtime: item.mtime,
        size: item.size,
        deleted: status === 'D',
        changedFileMissingTime: sessionFileDatePlaceholderNeeded(item),
      });
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
const changesRepoSectionsByGroups = new WeakMap();

function renderChangesGroups(groupsEl, files, options = {}) {
  if (!groupsEl) return;
  if (options.changedRepo) return renderChangedChangesRepo(groupsEl, options.changedRepo, options);
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
    const repoInfo = repoMap.get(repo) || {};
    renderChangesRepoSection(section, repo, repoFiles, repoInfo, options);
    nextNodes.push(section);
  }
  reconcileChildNodes(groupsEl, nextNodes);
  changesRepoSectionsByGroups.set(groupsEl, new Map(nextNodes.map(section => [section.dataset.changesRepo, section])));
}

function renderChangedChangesRepo(groupsEl, changedRepo, options) {
  let sections = changesRepoSectionsByGroups.get(groupsEl);
  if (!sections) {
    sections = new Map(Array.from(groupsEl.children, child => [child.dataset.changesRepo, child]).filter(([repo]) => repo));
    changesRepoSectionsByGroups.set(groupsEl, sections);
  }
  const repo = String(changedRepo.repo || '');
  let section = sections.get(repo);
  if (!section) {
    section = document.createElement('section');
    section.className = 'changes-repo-group';
    section.dataset.changesRepo = repo;
    sections.set(repo, section);
  }
  const repoInfo = (changedRepo.repos || []).find(row => row?.repo === repo) || {};
  renderChangesRepoSection(section, repo, changedRepo.files || [], repoInfo, options);
  if (section.parentElement !== groupsEl) groupsEl.append(section);
  return section;
}

function renderChangesRepoSection(section, repo, repoFiles, repoInfo, options) {
  const view = normalizeFileExplorerView(options.view || 'differ');
  const compact = options.compact === true;
  const payload = options.payload || {};
  // A gone repo is never collapsible, so it never joins the collapsed set.
  const repoIsMissing = repoInfo?.missing === true;
  const collapsed = !repoIsMissing && changesRepoCollapsed.has(repo);
  section.classList.toggle(CLS.collapsed, collapsed);
  const repoLabel = repo === changesOutsideRepoKey ? t('changes.outsideRepo') : compactHomePath(repo);
  // A missing repo has no working tree, so it gets no FROM/TO comparison controls either.
  const hasGit = repo && repo !== changesOutsideRepoKey && !repoIsMissing;
  section.dataset.changesRepoMissing = repoIsMissing ? 'true' : 'false';
  // Update repo header button (small HTML string — not performance sensitive)
  let head = section.querySelector(':scope > .changes-repo-head');
  if (!head) {
    head = makeButton({className: 'changes-repo-head'});
    section.prepend(head);
  }
  // A gone repo has no children, no refs and no open action, so it gets no disclosure control
  // and no child list -- one header row saying it is missing on disk, and nothing that offers to
  // reveal content that cannot exist.
  if (repoIsMissing) delete head.dataset.changesRepoToggle;
  else head.dataset.changesRepoToggle = repo;
  if (repoIsMissing) head.removeAttribute('aria-expanded');
  else head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  head.disabled = repoIsMissing;
  head.innerHTML = `${repoIsMissing ? '' : disclosureTriangleHtml(!collapsed, 'changes-repo-caret')}<span class="changes-repo-title">${esc(repoLabel)}</span>${repoIsMissing ? `<span class="changes-repo-missing">${esc(t('filetab.missingTitle'))}</span>` : ''}${changesRepoTotalsHtml(repoInfo, repoFiles)}`;
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
    const active = document.activeElement;
    const editingRef = refsRow.contains(active) && active.matches?.('[data-diff-ref-from], [data-diff-ref-to]');
    if (editingRef) {
      let detail = refsRow.querySelector(':scope > .changes-repo-refs-detail');
      if (details) {
        if (!detail) {
          detail = document.createElement('div');
          detail.className = 'changes-repo-refs-detail';
          refsRow.append(detail);
        }
        detail.innerHTML = details;
      } else {
        detail?.remove();
      }
    } else {
      refsRow.innerHTML = `<div class="changes-repo-refs-main">${diffRefComparisonLineHtml(repo)}</div>${details ? `<div class="changes-repo-refs-detail">${details}</div>` : ''}`;
    }
    refsRow.hidden = false;
  } else if (refsRow) {
    refsRow.hidden = true;
  }
  // Update file list using the unified tree renderer
  let fileList = section.querySelector(':scope > .changes-file-list');
  if (repoIsMissing) {
    fileList?.remove();
  } else if (!collapsed) {
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

function fileExplorerDiffSessionControlHtml(session, view = 'differ') {
  const surface = normalizeFileExplorerView(view);
  return `<label class="file-explorer-diff-session-control file-explorer-mode-files-diff-only changes-control" data-file-explorer-session-surface="${esc(surface)}">${esc(t('common.sessionLabel'))}: ${sessionFilesSessionSelectHtml(session, {className: 'file-explorer-diff-session-select'})}</label>`;
}

function syncFileExplorerDiffSessionControls() {
  for (const select of document.querySelectorAll('.file-explorer-diff-session-control [data-session-files-session]')) {
    const surface = select.closest('[data-file-explorer-session-surface]')?.dataset.fileExplorerSessionSurface || 'differ';
    const session = surface === 'finder' ? fileExplorerFinderTargetSession() : fileExplorerSessionFilesTargetSession();
    const options = Array.from(select.options || []);
    if (options.length !== sessions.length || !options.some(option => option.value === session)) {
      select.outerHTML = sessionFilesSessionSelectHtml(session, {className: 'file-explorer-diff-session-select'});
    } else if (select.value !== session) {
      select.value = session;
    }
  }
}

function syncFileExplorerSessionControlVisibility(scope = document) {
  for (const control of scope.querySelectorAll('.file-explorer-diff-session-control[data-file-explorer-session-surface="finder"]')) {
    control.hidden = false;
    control.setAttribute('aria-hidden', 'false');
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
  const editingControl = activeChangesControl(root);
  // Stream chunks can change summary HTML while a user is editing FROM/TO; keep that input node alive.
  if (options.force === true || groupsMissing || (staticChanged && !editingControl)) {
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
  const existingItem = options.forceNewTab === true ? null : existingPrimaryEditorItemForPath(path);
  let item = options.item
    || (options.forceNewTab === true ? fileEditorCopyItemFor(path) : existingItem || reusableFileEditorDiffPreviewItem(path));
  let resolvedPath = path;
  const normalizedStatus = String(status || '').toUpperCase();
  const openDiffMode = options.openMode !== 'edit';
  if (openDiffMode) setFileEditorDiffExpandUnchangedForItem(path, item, false);
  const initialMode = openDiffMode ? 'diff' : 'edit';
  setFileEditorViewMode(path, initialMode, item);
  // Use the payload's own FROM/TO for this file's repo so the diff matches what the panel shows,
  // even when the repo is not in diffRefsByRepo (e.g. a repo outside the active session's checkout).
  const payloadRepoRefs = (() => {
    const repos = repoPayloadByPath(fileExplorerSessionFilesState.payload);
    const key = repo || fileRepoForPath(path);
    const repoInfo = repos.get(key);
    const comparisonFrom = cleanDiffRef(repoInfo?.comparison_from_ref, '');
    const comparisonTo = cleanDiffRef(repoInfo?.comparison_to_ref, '');
    if (comparisonFrom && comparisonTo) return {fromRef: comparisonFrom, toRef: comparisonTo};
    const from = cleanDiffRef(repoInfo?.from_ref, '');
    const to = cleanDiffRef(repoInfo?.to_ref, '');
    if (from && to && from !== 'default' && to !== 'base') return {fromRef: from, toRef: to};
    const selected = repoDiffRefs(key);
    return {fromRef: selected.from, toRef: selected.to};
  })();
  noteFileExplorerChangesSessionInteraction(ownerSession);
  const openOptions = {
    item,
    ownerSession,
    includeGit: openDiffMode,
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
    if (openedItem) {
      if (openedItem !== item) {
        releaseFileEditorItem(path, item);
      }
      item = openedItem;
      resolvedPath = fileItemPath(item) || path;
    }
    const openedState = fileState.get(resolvedPath);
    if (openedState?.externalMissing === true && !['A', '?'].includes(normalizedStatus)) {
      // A tracked Differ row can vanish after the listing snapshot. Keep the missing warning, but
      // retain a text surface so /api/fs/diff can recover the committed side from Git.
      openedState.kind = 'text';
      openedState.gitRoot = normalizeDirectoryPath(repo || openedState.gitRoot || '');
      openedState.gitTracked = true;
    }
  }
  if (!openDiffMode) {
    renderOpenFilePath(resolvedPath);
    void refreshOpenFileDiff(resolvedPath, {silent: true, renderOnComplete: false, ...payloadRepoRefs});
    return;
  }
  const diffReady = await refreshOpenFileDiff(resolvedPath, {
    silent: true,
    renderOnComplete: false,
    updateControlsOnComplete: false,
    ...payloadRepoRefs,
  });
  const current = fileState.get(resolvedPath);
  if (diffReady && fileStateCanRenderDiffView(resolvedPath, current) && current?.externalMissing !== true) {
    setFileEditorViewMode(resolvedPath, 'diff', item);
  } else {
    setFileEditorViewMode(resolvedPath, 'edit', item);
  }
  renderOpenFilePath(resolvedPath);
  if (!diffReady || !fileStateCanRenderDiffView(resolvedPath, current)) {
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
  if (!panel) return null;
  return bindScopedOnce(panel, 'changes-panel', scope => {
  normalizeAppOwnedControls(panel);
  scope.ownEvent('change', panel, 'change', event => {
    if (handleFileExplorerTreeToolbarChange(panel, event)) return;
    const sessionSelect = event.target.closest('[data-session-files-session]');
    if (sessionSelect && panel.contains(sessionSelect)) {
      const view = fileExplorerViewForItem(panel.dataset.panelItem) || 'finder';
      if (view === 'finder') switchFileExplorerFinderSession(sessionSelect.value);
      else switchFileExplorerChangesSession(sessionSelect.value);
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
  scope.ownEvent('input', panel, 'input', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-from], [data-diff-ref-to]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    refreshDiffRefToDatalist(diffRefInput.closest('[data-diff-ref-controls]'));
    renderDiffRefPopover(diffRefInput, {showAll: false});
  });
  scope.ownEvent('focusin', panel, 'focusin', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-input]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('[data-diff-ref-controls]'));
  });
  scope.ownEvent('pointerdown', panel, 'pointerdown', event => {
    const diffRefInput = event.target.closest('[data-diff-ref-input]');
    if (!diffRefInput || !panel.contains(diffRefInput)) return;
    openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('[data-diff-ref-controls]'));
  });
  scope.ownEvent('keydown', panel, 'keydown', event => {
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
  scope.ownEvent('actions-click', panel, 'click', async event => {
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
      const destination = fileExplorerViewForItem(panel.dataset.panelItem) === 'finder' ? 'finder' : 'differ';
      fetchSessionFiles({
        destination,
        session: destination === 'finder' ? fileExplorerFinderTargetSession() : fileExplorerSessionFilesTargetSession(),
      });
      return;
    }
  });
  scope.ownEvent('dragstart', panel, 'dragstart', event => {
    const fileRow = event.target.closest('[data-open-change-file]');
    if (!fileRow || !panel.contains(fileRow)) return;
    if (!event.dataTransfer) return;
    const path = fileRow.dataset.openChangeFile || '';
    if (!path) return;
    closeFileImagePreview();
    const payloadObject = {path, paths: [path], kind: 'file', name: basenameOf(path)};
    beginFileDrag(payloadObject);
    event.dataTransfer.effectAllowed = 'copy';
    setYomuxDragData(event.dataTransfer, 'file', payloadObject);
    event.dataTransfer.setData('text/plain', path);
    startFileDragPreview(event, [path], {kind: 'file', name: basenameOf(path)});
  });
  scope.ownEvent('dragend', panel, 'dragend', () => {
    cancelDragOperationState();
  });
  // Single-clicks route through the shared tree controller. Modifier clicks keep Finder-style
  // multi-select without opening a diff; disclosure clicks toggle folders without opening files.
  scope.ownEvent('tree-click', panel, 'click', event => {
    if (event.__sharedTreeInteractionHandled) return;
    const row = event.target.closest('.file-tree-row[data-path]');
    if (!row || !panel.contains(row)) return;
    differTreeInteractionController.handleClick(event, panel, {row});
  });
  scope.ownEvent('contextmenu', panel, 'contextmenu', event => {
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
  appendContextMenuButton(menu, t('contextmenu.copyRelativePath'), button => copyChangedPath(rel || path, 'status.copiedRelativePath', {button}), closeFileContextMenu);
  appendContextMenuButton(menu, t('contextmenu.copyFullPath'), button => copyChangedPath(path, 'status.copiedPath', {button}), closeFileContextMenu);
  appendContextMenuButton(menu, t('contextmenu.expandInFinder', {name: displayName, finder: fileExplorerLabel()}), () => openChangedDirectoryInFinder(path), closeFileContextMenu);
  fileContextMenu.open(menu, x, y);
}

async function copyChangedPath(path, statusKey, options = {}) {
  await copyTextWithFeedback(path, {...options, statusText: t(statusKey)});
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
    const opened = await openFileExplorerAt(path, {validateKind: true});
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

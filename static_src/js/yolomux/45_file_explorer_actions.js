// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Continuation of the Finder/file-explorer module (split from 40_file_explorer_files.js to keep each
// partial under a readable size): per-row click/drag, the file context menu, and file CRUD actions.
// Concatenated immediately after 40 by tools/static_build.py, so it shares the same bundle scope.

async function onFileTreeRowClick(row, fullPath, entry, event) {
  closeFileImagePreview();
  const selectionOnly = updateFileTreeSelectionFromClick(row, fullPath, event);
  if (selectionOnly) return;
  if (entry.kind === 'dir') {
    if (fileExplorerExpanded.has(fullPath)) {
      collapseDirectoryRow(row, fullPath, {manual: true});
    } else {
      await expandDirectoryRow(row, fullPath, {manual: true});
    }
    return;
  }
  if (entry.kind === 'file') {
    updateFileExplorerCurrentFileHighlight();
  }
}

function startFileTreeDrag(event, row, fullPath, entry) {
  closeFileImagePreview();
  if (!fileExplorerSelectedPaths.has(fullPath)) selectFileTreePath(fullPath);
  const paths = fileTreeActionPaths(fullPath);
  const payloadObject = {path: fullPath, paths, kind: entry.kind, name: entry.name};
  dragFilePayloadState = normalizeFileDragPayload(payloadObject);
  const payload = JSON.stringify(payloadObject);
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('application/x-yolomux-file', payload);
  event.dataTransfer.setData('text/plain', paths.join('\n'));
  startFileDragPreview(event, paths, entry);
}

async function fetchFilePathInfo(path, options = {}) {
  const normalized = normalizeDirectoryPath(path);
  const canReuse = options.fresh !== true;
  const pathInfoTtlMs = fileExplorerFsCacheTtlMs();
  if (canReuse && pathInfoTtlMs > 0) {
    const cached = fileExplorerPathInfoCache.get(normalized);
    if (cached && Date.now() - cached.at < pathInfoTtlMs) return cached.payload;
  }
  if (suppressBackgroundFilesystemFetch(options)) return null;
  return dedupeInflight(fileExplorerPathInfoInflight, normalized, canReuse, () => (async () => {
    const payload = await fetchFilesystemBatchItem('info', normalized, {dedupe: canReuse});
    setLimitedMapEntry(fileExplorerPathInfoCache, normalized, {payload, at: Date.now()}, fileExplorerMemoryCacheLimit);
    return payload;
  })());
}

async function fetchDirectoryFileCount(path) {
  const normalized = normalizeDirectoryPath(path);
  return apiFetchJson(`/api/fs/count?path=${encodeURIComponent(normalized)}`);
}

async function showFileTreeContextMenu(row, fullPath, entry, x, y, options = {}) {
  closeFileContextMenu();
  closeTerminalContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
  if (!fileExplorerSelectedPaths.has(fullPath)) selectFileTreePath(fullPath);
  const selectedPaths = fileTreeActionPaths(fullPath);
  const infos = await Promise.all(selectedPaths.map(path => fetchFilePathInfo(path).catch(error => {
    console.warn('fs info failed', path, error);
    return null;
  })));
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu file-context-menu';
  menu.setAttribute('role', 'menu');
  const relativePaths = infos.map(info => info?.relative_path || '').filter(Boolean);
  const menuState = fileContextMenuState(entry, selectedPaths, relativePaths);
  const multiple = selectedPaths.length > 1;
  const openInNewTab = typeof options.openInNewTab === 'function'
    ? options.openInNewTab
    : () => openFileInAdditionalEditorTab(fullPath, entry, {userInitiated: true});
  const openInNewTabActions = Array.isArray(options.openInNewTabActions) && options.openInNewTabActions.length
    ? options.openInNewTabActions
    : [{label: options.openInNewTabLabel || t('contextmenu.openNewTab'), action: openInNewTab}];
  const actionContext = {fullPath, entry, selectedPaths, infos, primaryInfo: infos[0] || null, menuState};
  for (const action of openInNewTabActions) {
    const label = typeof action.label === 'function' ? action.label(actionContext) : action.label;
    appendContextMenuButton(menu, label || t('contextmenu.openNewTab'), action.action, closeFileContextMenu, {disabled: action.disabled ?? menuState.openInNewTabDisabled});
  }
  appendContextMenuButton(menu, t(multiple ? 'contextmenu.copyRelativePaths' : 'contextmenu.copyRelativePath'), () => copyFilePath(relativePaths.join('\n'), 'relative'), closeFileContextMenu, {disabled: menuState.copyRelativeDisabled});
  appendContextMenuButton(menu, t(multiple ? 'contextmenu.copyFullPaths' : 'contextmenu.copyFullPath'), () => copyFilePath(selectedPaths.join('\n'), 'path'), closeFileContextMenu);
  appendContextMenuButton(menu, t('contextmenu.copyImage'), () => copyImageFileToClipboard(selectedPaths[0]), closeFileContextMenu, {disabled: menuState.copyImageDisabled});
  appendContextMenuButton(menu, t('common.download'), () => triggerFileDownload(fullPath), closeFileContextMenu, {disabled: menuState.downloadDisabled});
  if (entry?.kind === 'dir') {
    appendContextMenuButton(menu, t('contextmenu.zipDownload'), () => triggerFolderZipDownload(fullPath), closeFileContextMenu, {disabled: menuState.zipDownloadDisabled});
  }
  appendContextMenuButton(menu, t(fileExplorerDirectoryIsIndexed(fullPath) ? 'contextmenu.disallowIndex' : 'contextmenu.allowIndex'), () => toggleFileExplorerDirectoryIndexed(fullPath), closeFileContextMenu, {disabled: menuState.indexToggleDisabled, checked: entry?.kind === 'dir' ? fileExplorerDirectoryIsIndexed(fullPath) : undefined});
  appendContextMenuButton(menu, t('common.rename'), () => beginFileTreeRename(row, selectedPaths[0], entry), closeFileContextMenu, {disabled: menuState.renameDisabled});
  appendContextMenuButton(menu, t(multiple ? 'contextmenu.deleteSelected' : 'contextmenu.delete'), () => deleteFileTreePath(fullPath, entry, selectedPaths), closeFileContextMenu, {disabled: menuState.deleteDisabled});
  fileContextMenu.open(menu, x, y);
}

function fileContextMenuState(entry, selectedPaths, relativePaths) {
  const multiple = selectedPaths.length > 1;
  return {
    copyRelativeDisabled: relativePaths.length !== selectedPaths.length,
    // readonly is terminal-only — the server forbids every /api/fs/* read (raw/list/...),
    // so the file-read affordances (open a file in a tab, Download via /api/fs/raw) are
    // disabled in readonly to match the server, instead of offering a command that 403s.
    openInNewTabDisabled: multiple || entry?.kind !== 'file' || readOnlyMode,
    copyImageDisabled: multiple || !entryIsImageFile(entry) || readOnlyMode,
    downloadDisabled: multiple || entry?.kind !== 'file' || readOnlyMode,
    zipDownloadDisabled: multiple || entry?.kind !== 'dir' || readOnlyMode,
    indexToggleDisabled: multiple || entry?.kind !== 'dir' || (!fileExplorerDirectoryIsIndexed(selectedPaths[0]) && Boolean(fileExplorerIndexedAncestor(selectedPaths[0]))),
    renameDisabled: readOnlyMode || multiple,
    deleteDisabled: readOnlyMode,
  };
}

function entryIsImageFile(entry) {
  return entry?.kind === 'file' && previewMediaKindForPath(entry.name || entry.path || '') === 'image';
}

async function copyFilePath(path, label) {
  const text = String(path || '');
  try {
    await copyTextToClipboard(text);
    statusEl.textContent = t(label === 'relative' ? 'status.copiedRelativePath' : 'status.copiedPath');
  } catch (error) {
    statusErr(localizedHtml('common.copyFailed', {error}));
  }
}

async function copyImageFileToClipboard(path) {
  if (!globalThis.ClipboardItem || !navigator?.clipboard?.write) {
    await copyFilePath(path, 'path');
    statusEl.textContent = t('status.imageClipboardUnavailable');
    return;
  }
  try {
    const response = await apiFetch(rawFileUrl(path), {cache: 'no-store'});
    if (!response.ok) {
      await showFileTransferResponseError(response, t('common.copyFailed', {error: t('common.requestFailed')}));
      return;
    }
    const blob = await response.blob();
    const type = blob.type || 'image/png';
    await navigator.clipboard.write([new ClipboardItem({[type]: blob})]);
    statusEl.textContent = t('status.copiedImage', {name: basenameOf(path)});
  } catch (error) {
    showFileTransferError(error, {fallback: t('common.copyFailed', {error: userMessageText(error, t('common.requestFailed'))})});
  }
}

function rawFileDownloadUrl(path) {
  return rawFileUrl(path, {download: 1});
}

function downloadFilenameFromContentDisposition(header, fallback = 'download') {
  const text = String(header || '');
  const starMatch = text.match(/filename\*=UTF-8''([^;]+)/i);
  if (starMatch) {
    try {
      return decodeURIComponent(starMatch[1]).trim() || fallback;
    } catch (_error) {
      return fallback;
    }
  }
  const quotedMatch = text.match(/filename="([^"]+)"/i);
  if (quotedMatch) return quotedMatch[1].trim() || fallback;
  const bareMatch = text.match(/filename=([^;]+)/i);
  return bareMatch ? bareMatch[1].trim() || fallback : fallback;
}

function saveBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'download';
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function fileTransferToastContainer(options = {}) {
  return displayToastContainer(options.session || options.item || focusedPanelItem || fileExplorerItemId);
}

function showFileTransferError(error, options = {}) {
  const primitiveFallback = typeof error === 'string' || typeof error === 'number' ? String(error) : '';
  const text = userMessageText(error, primitiveFallback || options.fallback || t('fileTransfer.failed')).trim();
  statusErr(esc(text));
  showToast(t('fileTransfer.failedTitle'), [text], {
    container: fileTransferToastContainer(options),
    countdownMs: 20000,
  });
}

async function showFileTransferResponseError(response, fallback = '', options = {}) {
  const payload = await response.json().catch(() => ({}));
  showFileTransferError(payload, {...options, fallback: fallback || response.statusText || `HTTP ${response.status}`});
}

async function triggerFileDownload(path) {
  if (!path) return;
  const label = basenameOf(path) || path;
  statusEl.textContent = t('fileTransfer.downloading', {name: label});
  try {
    const response = await apiFetch(rawFileDownloadUrl(path), {cache: 'no-store'});
    if (!response.ok) {
      await showFileTransferResponseError(response, t('fileTransfer.downloadFailed', {name: label}));
      return;
    }
    const filename = downloadFilenameFromContentDisposition(response.headers.get('Content-Disposition'), basenameOf(path) || 'download');
    const blob = await response.blob();
    saveBlobDownload(blob, filename);
    statusEl.textContent = t('fileTransfer.downloadStarted', {name: filename});
  } catch (error) {
    showFileTransferError(error, {fallback: t('fileTransfer.downloadFailed', {name: label})});
  }
}

async function triggerFolderZipDownload(path) {
  if (!path) return;
  const label = basenameOf(path) || path;
  statusEl.textContent = t('fileTransfer.zipping', {name: label});
  try {
    const response = await apiFetch(zipFileDownloadUrl(path), {cache: 'no-store'});
    if (!response.ok) {
      await showFileTransferResponseError(response, t('fileTransfer.downloadFailed', {name: label}));
      return;
    }
    const fallbackName = `${basenameOf(path) || 'folder'}.zip`;
    const filename = downloadFilenameFromContentDisposition(response.headers.get('Content-Disposition'), fallbackName);
    const blob = await response.blob();
    saveBlobDownload(blob, filename);
    statusEl.textContent = t('fileTransfer.downloadStarted', {name: filename});
  } catch (error) {
    showFileTransferError(error, {fallback: t('fileTransfer.downloadFailed', {name: label})});
  }
}

function copyCurrentFileExplorerPath() {
  copyFilePath(fileExplorerRoot || homePath || '/', 'path');
}

function childNameToPath(root, name) {
  const trimmed = String(name || '').trim();
  if (!trimmed || /[/\x00\r\n]/.test(trimmed)) return '';
  return childPath(normalizeDirectoryPath(root), trimmed);
}

async function createFileExplorerFile() {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyCreateFiles'));
    return;
  }
  const name = window.prompt(t('dialog.newFileName'));
  const path = childNameToPath(currentFileExplorerRoot(), name);
  if (!path) return;
  try {
    await apiFetchJson('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, content: ''}),
    });
    statusEl.textContent = t('status.created', {name: basenameOf(path)});
    await refreshFileExplorerTrees();
    await openFileInEditor(path, {name: basenameOf(path)});
  } catch (error) {
    statusErr(localizedHtml('status.newFileFailed', {error}));
  }
}

async function createFileExplorerFolder() {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyCreateFolders'));
    return;
  }
  const name = window.prompt(t('dialog.newFolderName'));
  const path = childNameToPath(currentFileExplorerRoot(), name);
  if (!path) return;
  try {
    await apiFetchJson('/api/fs/mkdir', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path}),
    });
    statusEl.textContent = t('status.created', {name: basenameOf(path)});
    await refreshFileExplorerTrees();
  } catch (error) {
    statusErr(localizedHtml('status.newFolderFailed', {error}));
  }
}

function fileTreeExpandCollapseAllButtonHtml(action, extraClass = '') {
  const expand = action === 'expand';
  const title = t(expand ? 'changes.expandAll' : 'common.collapseAll');
  const classes = ['file-explorer-header-action', 'file-tree-expand-collapse-all', extraClass].filter(Boolean).join(' ');
  return `<button type="button" class="${esc(classes)}" data-file-tree-expand-collapse-all="${expand ? 'expand' : 'collapse'}" title="${esc(title)}" aria-label="${esc(title)}">${fileTreeExpandCollapseAllIconHtml(action)}</button>`;
}

function fileTreeExpandCollapseAllIconHtml(action) {
  const expand = action === 'expand';
  const arrows = expand
    ? '<path d="M6.2 6.2 3 3"/><path d="M3 3h3"/><path d="M3 3v3"/><path d="M9.8 6.2 13 3"/><path d="M13 3h-3"/><path d="M13 3v3"/><path d="M6.2 9.8 3 13"/><path d="M3 13h3"/><path d="M3 13v-3"/><path d="M9.8 9.8 13 13"/><path d="M13 13h-3"/><path d="M13 13v-3"/>'
    : '<path d="M3 3 6.2 6.2"/><path d="M6.2 6.2h-3"/><path d="M6.2 6.2v-3"/><path d="M13 3 9.8 6.2"/><path d="M9.8 6.2h3"/><path d="M9.8 6.2v-3"/><path d="M3 13l3.2-3.2"/><path d="M6.2 9.8h-3"/><path d="M6.2 9.8v3"/><path d="M13 13 9.8 9.8"/><path d="M9.8 9.8h3"/><path d="M9.8 9.8v3"/>';
  return `<svg class="file-tree-expand-collapse-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${arrows}</svg>`;
}

function fileTreeExpandCollapseAllButtonsHtml(extraClass = '') {
  return `${fileTreeExpandCollapseAllButtonHtml('expand', extraClass)}${fileTreeExpandCollapseAllButtonHtml('collapse', extraClass)}`;
}

async function fileExplorerDirectoryPathsForRoot(root = currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root);
  const seen = new Set([normalizedRoot]);
  const result = [];
  const queue = [normalizedRoot];
  while (queue.length) {
    const directory = queue.shift();
    const entries = await fetchDirectory(directory);
    if (!Array.isArray(entries)) continue;
    for (const entry of sortedFileTreeEntries(entries, fileExplorerTreeSortMode, {includeHidden: fileExplorerShowHidden})) {
      if (entry?.kind !== 'dir') continue;
      const child = childPath(directory, entry.name);
      if (seen.has(child)) continue;
      seen.add(child);
      result.push(child);
      queue.push(child);
    }
  }
  return result;
}

function addFileExplorerExpandedPathAncestors(path, root = currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root || '');
  const target = normalizeDirectoryPath(path || '');
  if (!normalizedRoot || !target || target === normalizedRoot || !pathIsInsideDirectory(target, normalizedRoot)) return false;
  let changed = false;
  let parent = normalizedRoot;
  for (const part of childPathParts(normalizedRoot, target)) {
    const nextPath = childPath(parent, part);
    if (!fileExplorerExpanded.has(nextPath)) {
      fileExplorerExpanded.add(nextPath);
      changed = true;
    }
    parent = nextPath;
  }
  return changed;
}

async function expandSyncFileExplorerAffectedDirectories() {
  const plan = fileExplorerSyncPlan(fileExplorerSyncCommandSessionTarget());
  const root = normalizeDirectoryPath(plan.root || currentFileExplorerRoot());
  if (!root) return false;
  resetFileExplorerSyncManualCollapsesIfNeeded(plan);
  const paths = fileExplorerSyncExplicitExpansionTargets(plan, root);
  for (const path of paths) forgetFileExplorerSyncManualCollapse(path);
  resetFileExplorerAppliedSyncPlan();
  if (root !== currentFileExplorerRoot()) {
    const opened = await openFileExplorerAt(root, {preserveExpanded: false, preserveScroll: false});
    if (!opened) return false;
  }
  const generation = ++fileExplorerSyncGeneration;
  let changed = false;
  for (const path of paths) {
    changed = addFileExplorerExpandedPathAncestors(path, root) || changed;
  }
  if (changed) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  for (const path of paths) {
    if (generation !== fileExplorerSyncGeneration) return changed;
    changed = await expandFileExplorerTreesToPath(path, root, generation, {scrollIntoView: false, auto: true}) || changed;
  }
  setFileExplorerVisibleSyncTarget(plan.session, root);
  rememberFileExplorerSyncExpandedState(plan.session, root);
  markFileExplorerSyncPlanApplied(plan);
  updateFileExplorerSessionHighlightRows(plan.session);
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  return changed;
}

async function expandAllFileExplorerDirectories() {
  if (fileExplorerRootMode === 'sync') {
    await expandSyncFileExplorerAffectedDirectories();
    return;
  }
  const paths = await fileExplorerDirectoryPathsForRoot(currentFileExplorerRoot());
  fileExplorerExpanded.clear();
  for (const path of paths) fileExplorerExpanded.add(path);
  rememberFileExplorerSyncExpandedState();
  await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

async function collapseAllFileExplorerDirectories() {
  if (fileExplorerRootMode === 'sync') {
    const plan = fileExplorerSyncPlan(fileExplorerSyncCommandSessionTarget());
    for (const path of fileExplorerSyncExpansionPaths(plan)) rememberFileExplorerSyncManualCollapse(path);
  }
  fileExplorerExpanded.clear();
  rememberFileExplorerSyncExpandedState();
  await refreshFileExplorerTrees({preserveExpanded: false, preserveScroll: true});
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

async function setAllFileTreeDirectoriesExpanded(source, expand) {
  if (fileExplorerMode === 'tabber') {
    setAllTabberCollapsed(!expand);
    scheduleShareUiStatePublish();
    return;
  }
  if (source?.closest?.('.file-explorer-changes-panel')) {
    setAllFileExplorerChangesDirectoriesExpanded(expand);
    scheduleShareUiStatePublish();
    return;
  }
  if (expand) await expandAllFileExplorerDirectories();
  else await collapseAllFileExplorerDirectories();
  scheduleShareUiStatePublish();
}

function bindFileExplorerHeaderActions(container = document) {
  if (!container || container.dataset?.fileExplorerHeaderActionsBound === 'true') return;
  if (container.dataset) container.dataset.fileExplorerHeaderActionsBound = 'true';
  container.addEventListener('click', event => {
    const action = event.target.closest('[data-file-explorer-new-file], [data-file-explorer-new-folder], [data-file-explorer-refresh], [data-file-explorer-collapse], [data-file-explorer-tree-dates], [data-file-tree-expand-collapse-all]');
    if (!action || !container.contains(action)) return;
    event.preventDefault();
    event.stopPropagation();
    if (action.matches('[data-file-explorer-new-file]')) createFileExplorerFile();
    else if (action.matches('[data-file-explorer-new-folder]')) createFileExplorerFolder();
    else if (action.matches('[data-file-explorer-refresh]')) {
      if (fileExplorerMode === 'tabber') {
        clearTabberSessionFilesStates();
        fetchTabberActivity();
        refreshTabberPanels();
      } else if (fileExplorerMode === 'diff') fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), force: true});
      else {
        refreshFileExplorerTrees();
        fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
      }
    } else if (action.matches('[data-file-explorer-collapse]')) {
      collapseAllFileExplorerDirectories().catch(error => statusErr(localizedHtml('status.collapseFailed', {error})));
    } else if (action.matches('[data-file-tree-expand-collapse-all]')) {
      setAllFileTreeDirectoriesExpanded(action, action.dataset.fileTreeExpandCollapseAll === 'expand')
        .catch(error => statusErr(localizedHtml('status.treeActionFailed', {error})));
    } else if (action.matches('[data-file-explorer-tree-dates]')) {
      cycleFileExplorerTreeDateMode();
      if (fileExplorerMode === 'tabber') refreshTabberPanels();
    }
  });
  container.addEventListener('change', event => {
    const select = event.target.closest('[data-file-explorer-tree-sort]');
    if (!select || !container.contains(select)) return;
    event.preventDefault();
    event.stopPropagation();
    fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(select.value) ? select.value : 'az';
    writeStoredFileExplorerTreeSortMode(fileExplorerTreeSortMode);
    if (fileExplorerMode === 'tabber') refreshTabberPanels();
    else refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
    scheduleShareUiStatePublish();
  });
}

async function deleteFileTreePath(fullPath, entry, paths = null) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyDeleteFiles'));
    return;
  }
  const deletePaths = compactNestedPaths(paths || fileTreeActionPaths(fullPath));
  const kind = t(entry.kind === 'dir' ? 'dialog.delete.kindDirectory' : 'dialog.delete.kindFile');
  const confirmText = tPlural('dialog.delete', deletePaths.length, {
    kind,
    path: deletePaths[0],
    paths: deletePaths.join('\n'),
  });
  if (!window.confirm(confirmText)) return;
  if (!await confirmLargeDirectoryDeletes(deletePaths, fullPath, entry)) return;
  try {
    for (const path of deletePaths) {
      await apiFetchJson('/api/fs/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path}),
      });
    }
    for (const path of Array.from(openFiles.keys())) {
      if (deletePaths.some(deletedPath => path === deletedPath || path.startsWith(`${deletedPath}/`))) {
        removeOpenFile(path, {confirmDirty: false, render: false});
      }
    }
    for (const path of deletePaths) fileExplorerSelectedPaths.delete(path);
    if (deletePaths.includes(fileExplorerSelectionAnchor)) fileExplorerSelectionAnchor = null;
    statusEl.textContent = tPlural('status.deleted', deletePaths.length, {name: basenameOf(deletePaths[0])});
    await refreshFileExplorerTrees();
    if (typeof fetchSessionFiles === 'function') {
      await fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true, force: true});
    }
    renderSessionButtons();
    renderPaneTabStrips();
  } catch (error) {
    statusErr(localizedHtml('status.deleteFailed', {error}));
  }
}

function fileTreeEntryFromRow(path) {
  const row = document.querySelector(`.file-tree-row[data-path="${cssEscape(path)}"]`);
  if (!row) return null;
  return {
    kind: row.dataset.kind || '',
    name: row.dataset.name || basenameOf(path),
    is_repo: row.dataset.isRepo === 'true',
  };
}

async function fileTreeDeleteEntryForPath(path, primaryPath, primaryEntry = null) {
  if (path === primaryPath && primaryEntry?.kind) return primaryEntry;
  const rowEntry = fileTreeEntryFromRow(path);
  if (rowEntry?.kind) return rowEntry;
  try {
    return await fetchFilePathInfo(path, {fresh: true});
  } catch (error) {
    console.warn('delete path info failed', path, error);
    return null;
  }
}

async function confirmLargeDirectoryDeletes(deletePaths, primaryPath, primaryEntry = null) {
  for (const path of deletePaths) {
    const entry = await fileTreeDeleteEntryForPath(path, primaryPath, primaryEntry);
    if (entry?.kind !== 'dir') continue;
    let count = null;
    try {
      const payload = await fetchDirectoryFileCount(path);
      count = Number(payload?.files);
    } catch (error) {
      console.warn('directory delete count failed', path, error);
      if (!window.confirm(t('dialog.delete.countFailed', {path}))) return false;
      continue;
    }
    if (!Number.isFinite(count) || count <= 5) continue;
    if (!window.confirm(t('dialog.delete.largeDirectory', {count: Math.round(count), path}))) return false;
  }
  return true;
}

// C10: keyboard delete for the Finder tree. macOS Finder deletes the selection with Command-Delete (plain
// Delete is a text-edit key on Mac), so on a Mac UI require metaKey+Backspace/Delete; on PC/File Explorer a
// plain Delete works. Returns true once it has consumed the event so the global tab-close shortcut (which
// also binds Mod+Delete) does NOT also fire. Guarded against text/rename inputs, readonly, and no selection.
// File Explorer / Finder-style keyboard navigation over the SHARED selection. Works for the Finder AND the
// Differ (both render `.file-tree-row`). Arrow Up/Down move the lead (cursor) and single-select it; Shift+
// Arrow (and Shift+Home/End) extend the range from the anchor; Home/End jump; Mod+A selects all. Same
// surface gating as handleFileExplorerDeleteShortcut so it only fires when the Finder/Differ is active and
// not while typing in a rename input. Returns true if it handled the key.
// macOS Finder list-view keyboard PARITY. This is the PURE key->intent map (unit-tested in
// layout_url.test.js) so the bindings are verifiable without a DOM. mods: {shift, mod (Cmd on Mac /
// Ctrl on PC), alt}. Returns an intent string or null (key not claimed).
function fileExplorerKeyIntent(key, mods) {
  if (mods.alt) return null;
  const shift = mods.shift === true;
  const mod = mods.mod === true;
  switch (key) {
    case 'Enter': return (mod || shift) ? null : 'rename';                          // Finder Return = rename
    case 'ArrowDown': return mod ? 'open' : (shift ? 'extend-down' : 'move-down');  // Cmd-Down = open
    case 'ArrowUp': return mod ? 'enclosing' : (shift ? 'extend-up' : 'move-up');   // Cmd-Up = enclosing folder
    case 'ArrowRight': return (mod || shift) ? null : 'expand';                     // expand / step into first child
    case 'ArrowLeft': return (mod || shift) ? null : 'collapse';                    // collapse / step to parent
    case 'Home': return mod ? null : (shift ? 'extend-home' : 'move-home');
    case 'End': return mod ? null : (shift ? 'extend-end' : 'move-end');
    case ' ': return (mod || shift) ? null : 'preview';                             // Space = Quick Look (preview)
    default: break;
  }
  if (mod && (key === 'o' || key === 'O')) return 'open';                           // Cmd-O = open
  if (mod && (key === 'a' || key === 'A')) return 'select-all';                     // Cmd-A = select all
  if (!mod && typeof key === 'string' && key.length === 1 && key !== ' ') return 'typeahead';   // type-to-select
  return null;
}

let fileExplorerTypeaheadBuffer = '';
let fileExplorerTypeaheadTimer = null;

// macOS Finder type-ahead: accumulate typed chars (buffer resets after a pause); pressing the SAME single
// char again cycles to the next match. Selects the first row whose name starts with the buffer, searching
// forward from the lead (wrapping).
function fileExplorerTypeaheadSelect(rows, leadIndex, char, selectLead) {
  if (fileExplorerTypeaheadTimer) clearTimeout(fileExplorerTypeaheadTimer);
  fileExplorerTypeaheadTimer = setTimeout(() => { fileExplorerTypeaheadBuffer = ''; }, fileExplorerTypeaheadClearMs);
  const lower = char.toLowerCase();
  const cycling = fileExplorerTypeaheadBuffer === lower;
  fileExplorerTypeaheadBuffer = cycling ? lower : (fileExplorerTypeaheadBuffer + lower);
  const prefix = fileExplorerTypeaheadBuffer;
  const nameOf = row => String(row.dataset.name || basenameOf(row.dataset.path)).toLowerCase();
  const start = leadIndex < 0 ? 0 : (cycling ? leadIndex + 1 : leadIndex);
  for (let i = 0; i < rows.length; i++) {
    const row = rows[(start + i) % rows.length];
    if (nameOf(row).startsWith(prefix)) { selectLead(row); return; }
  }
}

// The tree row's aria-expanded state and its descendants must always use this one state calculation. Finder
// opts into expansion, while Differ defaults to expanded and persists only folders the user collapsed.
function fileTreeDirectoryExpanded(fullPath, options = {}) {
  if (options.differMode === true || options.row?.closest?.('.file-explorer-changes-panel')) {
    return !changesFolderCollapsed.has(fullPath);
  }
  return fileExplorerPendingExpansions.has(fullPath)
    || options.autoExpand === true
    || (options.expandedSet instanceof Set ? options.expandedSet : fileExplorerExpanded).has(fullPath);
}
function setFileTreeDirectoryExpanded(row, fullPath, expand) {
  if (row.closest('.file-explorer-changes-panel')) {
    if (expand) changesFolderCollapsed.delete(fullPath);
    else changesFolderCollapsed.add(fullPath);
    writeStoredChangesFolderCollapsed();
    renderFileExplorerChangesPanels({force: true});
    scheduleShareUiStatePublish();
    return;
  }
  if (expand) expandDirectoryRow(row, fullPath, {manual: true});
  else collapseDirectoryRow(row, fullPath, {manual: true});
}

const finderTreeInteractionController = createSharedTreeInteractionController({
  name: 'finder',
  rowSelector: '.file-tree-row[data-path]',
  allowRange: true,
  allowSelectAll: true,
  applyCurrentClasses: false,
  isRowSelectable: row => Boolean(
    row
    && !row.dataset.tabberType
    && !row.closest?.('.file-explorer-changes-panel')
    && (row.dataset.kind === 'file' || row.dataset.kind === 'dir'),
  ),
  selectedIds: () => fileExplorerSelectedPaths,
  getLeadId: () => fileExplorerSelectionLead,
  setLeadId: id => { fileExplorerSelectionLead = id; },
  selectRow(row, id) {
    fileExplorerManualSelectionActive = true;
    selectFileTreePath(id || row?.dataset?.path || '');
  },
  selectRange(row, id) {
    fileExplorerManualSelectionActive = true;
    selectFileTreeRange(row, id || row?.dataset?.path || '', {clear: true});
  },
  afterSelectAll(rows) {
    fileExplorerManualSelectionActive = true;
    fileExplorerSelectionAnchor = rows[0]?.dataset?.path || null;
  },
  isExpanded: row => row?.dataset?.kind === 'dir' && fileTreeDirectoryExpanded(row.dataset.path || '', {row}),
  setExpanded(row, expanded) {
    const path = row?.dataset?.path || '';
    if (!path || row?.dataset?.kind !== 'dir') return;
    setFileTreeDirectoryExpanded(row, path, expanded === true);
  },
});

function finderTreeContainerForEvent(event) {
  const treeSel = '.file-explorer-tree-panel';
  return event?.target?.closest?.(treeSel)
    || (fileExplorerSelectionLead && document.querySelector(`.file-tree-row[data-path="${cssEscape(fileExplorerSelectionLead)}"]`)?.closest?.(treeSel))
    || document.querySelector(`.panel.file-explorer-panel ${treeSel}`)
    || document.querySelector(treeSel)
    || document;
}

function finderTreeLeadRow(container) {
  const rows = finderTreeInteractionController.rows(container);
  const leadIndex = rows.findIndex(item => item.dataset.path === fileExplorerSelectionLead);
  return {
    rows,
    leadIndex: leadIndex >= 0 ? leadIndex : rows.findIndex(item => fileExplorerSelectedPaths.has(item.dataset.path)),
  };
}

function finderTreeRowEntry(row, path) {
  return {
    kind: row?.dataset?.kind,
    name: row?.dataset?.name || basenameOf(path),
    is_repo: row?.dataset?.isRepo === 'true',
  };
}

// File Explorer / Finder-style keyboard nav over the SHARED selection (Finder AND Differ render
// .file-tree-row). Dispatches the PURE fileExplorerKeyIntent map onto the live tree. Same surface gating
// as the delete shortcut. Returns true if it handled the key.
function handleFileExplorerArrowNav(event) {
  const intent = fileExplorerKeyIntent(event.key, {shift: event.shiftKey, mod: event.metaKey || event.ctrlKey, alt: event.altKey});
  if (!intent) return false;
  if (!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)) return false;
  if (!globalShortcutTargetAllowsAppAction(event.target)) return false;
  const consume = () => { event.preventDefault(); event.stopPropagation(); };
  // Cmd-Up opens the enclosing folder (move the Finder root up a level) — independent of the row list.
  if (intent === 'enclosing') {
    consume();
    const root = currentFileExplorerRoot();
    const parent = dirnameOf(root);
    if (parent && parent !== root) openFileExplorerManualRoot(parent);
    return true;
  }
  const container = finderTreeContainerForEvent(event);
  const {rows, leadIndex} = finderTreeLeadRow(container);
  if (!rows.length) return false;
  const selectLead = row => finderTreeInteractionController.selectRow(container, row, event);
  if (intent === 'typeahead') { consume(); fileExplorerTypeaheadSelect(rows, leadIndex, event.key, selectLead); return true; }
  // Intents that act on the current lead row.
  if (intent === 'rename' || intent === 'open' || intent === 'preview') {
    if (leadIndex < 0) return false;
    const leadRow = rows[leadIndex];
    const leadPath = leadRow.dataset.path;
    const isDir = leadRow.dataset.kind === 'dir';
    const entry = finderTreeRowEntry(leadRow, leadPath);
    if (intent === 'rename') {                     // Finder Return = rename; tracked files move via git mv (backend)
      consume();
      beginFileTreeRename(leadRow, leadPath, entry);
      return true;
    }
    if (intent === 'open') {                       // Cmd-O / Cmd-Down: open
      consume();
      if (!isDir) openFileInEditor(leadPath, entry);
      else openFileExplorerManualRoot(leadPath);   // Finder: open a folder = descend into it
      return true;
    }
    if (intent === 'preview') {                    // Space = Quick Look-style preview (image popover) of a file
      consume();
      if (document.querySelector('.file-image-preview-popover')) closeFileImagePreview();
      else if (!isDir) openFileImagePreview(leadRow, leadPath, entry);
      return true;
    }
  }
  return finderTreeInteractionController.handleKeydown(event, container);
}

function handleFileExplorerDeleteShortcut(event) {
  const key = String(event.key || '').toLowerCase();
  if (key !== 'backspace' && key !== 'delete') return false;
  if (isMacPlatform()) {
    if (!(event.metaKey === true && event.ctrlKey !== true && event.altKey !== true)) return false;
  } else if (key !== 'delete' || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) {
    return false;
  }
  // Only when the Finder is the active surface and not while editing text (rename input, etc.).
  if (!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)) return false;
  if (!globalShortcutTargetAllowsAppAction(event.target)) return false;
  const paths = compactNestedPaths(Array.from(fileExplorerSelectedPaths));
  if (!paths.length) return false;
  event.preventDefault();
  event.stopPropagation();
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyDeleteFiles'));
    return true;
  }
  const primary = paths[0];
  const row = document.querySelector(`.file-tree-row[data-path="${cssEscape(primary)}"]`);
  const entry = row
    ? {kind: row.dataset.kind, name: row.dataset.name || basenameOf(primary), is_repo: row.dataset.isRepo === 'true'}
    : {kind: 'file', name: basenameOf(primary)};
  deleteFileTreePath(primary, entry, paths);
  return true;
}

function beginFileTreeRename(row, fullPath, entry) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyRenameFiles'));
    return;
  }
  closeFileContextMenu();
  const targetRow = row || document.querySelector(`.file-tree-row[data-path="${cssEscape(fullPath)}"]`);
  const nameNode = targetRow?.querySelector('.file-tree-name');
  if (!targetRow || !nameNode) return;
  fileTreeRenamePath = fullPath;
  selectFileTreePath(fullPath);
  targetRow.classList.add('renaming');
  targetRow.draggable = false;
  const currentName = entry?.name || basenameOf(fullPath);
  const input = document.createElement('input');
  input.className = 'file-tree-rename-input';
  input.value = currentName;
  input.setAttribute('aria-label', t('common.renameNamed', {name: currentName}));
  nameNode.replaceChildren(input);
  let finished = false;
  let commitInFlight = false;
  const finish = async commit => {
    if (finished || commitInFlight) return;
    if (!commit) {
      finished = true;
      fileTreeRenamePath = null;
      targetRow.classList.remove('renaming');
      targetRow.draggable = entry.kind === 'file' || entry.kind === 'dir';
      nameNode.textContent = currentName;
      return;
    }
    const nextName = input.value.trim();
    if (!nextName || nextName === currentName) {
      finish(false);
      return;
    }
    commitInFlight = true;
    const renamed = await renameFileTreePath(fullPath, entry, nextName);
    if (renamed) finished = true;
    else {
      commitInFlight = false;
      input.focus();
    }
  };
  input.addEventListener('click', event => event.stopPropagation());
  input.addEventListener('dblclick', event => event.stopPropagation());
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      finish(true);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      finish(false);
    }
  });
  input.addEventListener('blur', () => finish(true));
  setTimeout(() => {
    input.focus();
    const dot = currentName.lastIndexOf('.');
    const selectEnd = dot > 0 ? dot : currentName.length;
    input.setSelectionRange(0, selectEnd);
  }, 0);
}

async function renameFileTreePath(fullPath, entry, newName) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyRenameFiles'));
    return false;
  }
  const currentName = entry?.name || basenameOf(fullPath);
  const trimmed = newName.trim();
  if (!trimmed || trimmed === currentName) return false;
  try {
    const payload = await apiFetchJson('/api/fs/rename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: fullPath, new_name: trimmed}),
    });
    const newPath = payload.path;
    markFileIndexRootsRefreshing(payload.reindex_roots);
    if (fileExplorerSelectedPaths.delete(fullPath)) fileExplorerSelectedPaths.add(newPath);
    if (fileExplorerSelectionAnchor === fullPath) fileExplorerSelectionAnchor = newPath;
    if (fileTreeRenamePath === fullPath) fileTreeRenamePath = null;
    for (const path of Array.from(openFiles.keys())) {
      if (path === fullPath) renameOpenFilePath(path, newPath);
      else if (path.startsWith(`${fullPath}/`)) renameOpenFilePath(path, `${newPath}${path.slice(fullPath.length)}`);
    }
    statusEl.textContent = t('common.renamed', {oldName: currentName, newName: trimmed});
    await refreshFileExplorerTrees();
    return true;
  } catch (error) {
    statusErr(localizedHtml('status.renameFailed', {error}));
    return false;
  }
}

async function refreshFileExplorerTrees(options = {}) {
  const refreshOptions = {preserveExpanded: true, preserveScroll: true, ...options};
  if (await refreshFileExplorerTreesInPlace(refreshOptions)) return;
  if (fileExplorerRoot) await openFileExplorerAt(fileExplorerRoot, refreshOptions);
  else await refreshFileExplorerPanelTrees(refreshOptions);
}

async function refreshFileExplorerPanelTrees(options = {}) {
  const panels = Array.from(document.querySelectorAll('.panel.file-explorer-panel'));
  if (!panels.length) return;
  const shouldRestore = options.restoreState !== false && (options.preserveExpanded || options.preserveScroll);
  const previousExpanded = shouldRestore && options.preserveExpanded ? Array.from(fileExplorerExpanded) : [];
  const scrollPositions = shouldRestore && options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  await Promise.all(panels.map(panel => refreshFileExplorerPanelTree(panel, options)));
  if (previousExpanded.length) await restoreFileExplorerExpandedPaths(previousExpanded, currentFileExplorerRoot());
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
}

async function fileExplorerEntriesByWatchedDirectory(root = currentFileExplorerRoot()) {
  const normalizedRoot = normalizeDirectoryPath(root);
  const entriesByDir = new Map();
  const directories = new Set([normalizedRoot]);
  for (const path of fileExplorerExpanded) {
    if (pathIsInsideDirectory(path, normalizedRoot) && path !== normalizedRoot) directories.add(normalizeDirectoryPath(path));
  }
  const listings = await Promise.all(Array.from(directories).map(async directory => ({
    directory,
    entries: await fetchDirectory(directory),
  })));
  for (const {directory, entries} of listings) {
    if (entries) entriesByDir.set(normalizeDirectoryPath(directory), entries);
  }
  return entriesByDir;
}

async function refreshFileExplorerTreesInPlace(options = {}) {
  const root = normalizeDirectoryPath(options.root || currentFileExplorerRoot());
  const entriesByDir = options.entriesByDir instanceof Map
    ? options.entriesByDir
    : await fileExplorerEntriesByWatchedDirectory(root);
  const rootEntries = Array.isArray(options.entries) ? options.entries : entriesByDir.get(root);
  if (!rootEntries) return false;
  const scrollPositions = options.preserveScroll ? captureFileExplorerScrollPositions() : null;
  if (fileExplorerTree) {
    setFileExplorerPathDisplay(root);
    renderTreeChildren(fileExplorerTree, root, rootEntries, 0, {entriesByDir});
  }
  await refreshFileExplorerPanelTrees({
    ...options,
    root,
    entries: rootEntries,
    entriesByDir,
    restoreState: false,
  });
  if (scrollPositions) restoreFileExplorerScrollPositions(scrollPositions);
  updateFileExplorerCurrentFileHighlight();
  return true;
}

function fileExtensionOf(name) {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot).toLowerCase();
}

function previewMimeForPath(path) {
  return PREVIEW_MIME_BY_EXTENSION.get(fileExtensionOf(path)) || '';
}

function previewRendererForMime(mime) {
  return PREVIEW_RENDERER_BY_MIME.get(String(mime || '').toLowerCase()) || null;
}

function previewRendererForPath(path, state = null) {
  if (state?.kind === 'image') return PREVIEW_RENDERER_BY_ID.get('image') || PREVIEW_RENDERER_BY_ID.get('unsupported');
  if (state?.kind === 'media' && state.mediaKind) return PREVIEW_RENDERER_BY_ID.get(String(state.mediaKind)) || PREVIEW_RENDERER_BY_ID.get('unsupported');
  if (state?.kind === 'too-large' || state?.kind === 'error') return PREVIEW_RENDERER_BY_ID.get('unsupported');
  const ext = fileExtensionOf(path);
  const renderer = PREVIEW_RENDERER_BY_EXTENSION.get(ext);
  if (renderer) return renderer;
  if (HIGHLIGHTABLE_EXTENSIONS[ext]) return PREVIEW_RENDERER_BY_ID.get('text');
  if (state?.kind === 'text') return PREVIEW_RENDERER_BY_ID.get('text');
  return PREVIEW_RENDERER_BY_ID.get('unsupported');
}

function previewMediaKindForPath(path, state = null) {
  return previewRendererForPath(path, state)?.mediaKind || '';
}

function previewKindForPath(path, state = null) {
  return previewRendererForPath(path, state)?.kind || 'unsupported';
}

function previewRendererIsPreviewable(renderer) {
  return Boolean(renderer) && renderer.kind !== 'unsupported' && renderer.previewable !== false;
}

function previewPathIsPreviewable(path, state = null) {
  return previewRendererIsPreviewable(previewRendererForPath(path, state));
}

function previewKindIsTextBacked(kind) {
  return PREVIEW_RENDERERS.some(renderer => renderer.kind === kind && renderer.textBacked);
}

function defaultFileEditorViewModeForPath(path, kind) {
  if (kind !== 'text') return 'preview';
  return previewRendererForPath(path)?.defaultMode || 'edit';
}

function openFileKindForPreviewPath(path) {
  const renderer = previewRendererForPath(path);
  const mediaKind = renderer?.mediaKind || '';
  if (mediaKind === 'image') return 'image';
  if (renderer?.raw && !renderer?.textBacked) return 'media';
  return 'text';
}

function fileIconFor(name) {
  const lowerName = String(name || '').toLowerCase();
  const ext = fileExtensionOf(lowerName);
  if (previewMediaKindForPath(lowerName) === 'image') return '🖼';
  if (ext === '.log') return '🪵';
  if (['.md', '.txt', '.rst'].includes(ext)) return '📝';
  if (['.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env'].includes(ext)) return '⚙';
  if (['.sh', '.bash', '.zsh', '.fish'].includes(ext)) return '🐚';
  if (['.py', '.js', '.ts', '.tsx', '.jsx', '.rs', '.go', '.c', '.h', '.cpp', '.hpp', '.rb', '.lua', '.sql'].includes(ext)) return '🧩';
  if (['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz'].includes(ext)) return '🗜';
  if (['dockerfile', 'makefile'].includes(lowerName)) return '⚙';
  if (['.gitignore', 'license', 'readme'].includes(lowerName)) return '📝';
  return '📄';
}

function basenameOf(path) {
  if (!path) return '';
  const idx = path.lastIndexOf('/');
  return idx === -1 ? path : path.slice(idx + 1) || '/';
}

function dirnameOf(path) {
  if (!path || path === '/') return '/';
  const idx = path.lastIndexOf('/');
  if (idx <= 0) return '/';
  return path.slice(0, idx);
}

// resolve a relative href against a base dir, collapsing '.'/'..' segments. An absolute
// `rel` (leading '/') ignores the base. Used to open relative markdown-preview links in the editor.
function joinAndNormalize(base, rel) {
  const relStr = String(rel || '');
  const combined = relStr.startsWith('/') ? relStr : `${String(base || '/')}/${relStr}`;
  const isAbs = combined.startsWith('/');
  const out = [];
  for (const seg of combined.split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') { out.pop(); continue; }
    out.push(seg);
  }
  return (isAbs ? '/' : '') + out.join('/');
}

function fileInspectionErrorMessage(error, path) {
  return userMessageText(error, t('preview.openFailed', {path}));
}

async function fetchFileEntry(path) {
  const result = await fetchFileEntryStatus(path);
  return result.entry;
}

async function fetchFileEntryStatus(path) {
  const entries = await fetchDirectory(dirnameOf(path));
  if (!Array.isArray(entries)) {
    const error = fileExplorerLastListError;
    return {
      entry: null,
      missing: error?.status === 404,
      error: fileErrorMessageSnapshot(error?.source || error, 'preview.openFailed', {path}),
      network: error?.network === true,
    };
  }
  const name = basenameOf(path);
  const entry = entries.find(entry => entry.name === name) || null;
  return {entry, missing: !entry, error: entry ? null : fileErrorMessageSnapshot(null, 'common.pathNotFound', {path}), network: false};
}

async function fetchFileInfoStatus(path) {
  try {
    return {info: await apiFetchJson(`/api/fs/info?path=${encodeURIComponent(path)}`), error: null};
  } catch (error) {
    return {info: null, error: fileErrorMessageSnapshot(error, 'editor.fileLoadFailed')};
  }
}

function fileEntryMtime(entry) {
  return Number(entry?.mtime_ns || entry?.mtime || 0);
}

function filePayloadMtime(payload) {
  return Number(payload?.mtime_ns || payload?.mtime || 0);
}

// Keep this in sync with yolomux_lib.filesystem.MTIME_NS_CONFLICT_TOLERANCE. The file watcher compares
// mtimes returned by /api/fs/read and /api/fs/list, then decides whether to mark an open editor as stale.
// Current epoch nanosecond mtimes are larger than Number.MAX_SAFE_INTEGER, so the browser can round them;
// remote/synced filesystems can also report tiny mtime drift for unchanged content. A 10 ms window covers
// those precision/transport edge cases without turning the watcher into a broad race window where a real
// external write shortly after open is silently ignored.
const FILE_MTIME_NS_CHANGE_TOLERANCE = 10000000;
const FILE_MTIME_NS_MIN_VALUE = 1000000000000000;

function fileMtimesMatch(left, right) {
  const leftMtime = Number(left || 0);
  const rightMtime = Number(right || 0);
  if (leftMtime === rightMtime) return true;
  if (Math.max(Math.abs(leftMtime), Math.abs(rightMtime)) < FILE_MTIME_NS_MIN_VALUE) return false;
  return Math.abs(leftMtime - rightMtime) <= FILE_MTIME_NS_CHANGE_TOLERANCE;
}

function fileEntryChanged(state, entry) {
  if (!state || !entry) return true;
  const stateMtime = Number(state.mtime || 0);
  const entryMtime = fileEntryMtime(entry);
  if (!fileMtimesMatch(stateMtime, entryMtime)) return true;
  // equal mtimes but an UNKNOWN size on either side — do NOT assert "unchanged" (an
  // equal-mtime content change with no size would be missed); treat it as changed so the caller re-stats.
  if (state.size == null || entry.size == null) return true;
  return Number(state.size) !== Number(entry.size);
}

function filePanelItemsForPath(path) {
  const items = [];
  if (sharedImageViewerPath === path) items.push(imageViewerItemFor(path));
  items.push(...fileEditorTabItemsForPath(path));
  return items;
}

function fileEditorDiffPreviewItems() {
  const items = new Set();
  for (const state of openFiles.values()) {
    normalizeFileStateRecord(state);
    for (const item of state.editorTabItems) {
      if (isFileEditorDiffPreviewItem(item)) items.add(item);
    }
  }
  for (const item of paneItems()) {
    if (isFileEditorDiffPreviewItem(item)) items.add(item);
  }
  return Array.from(items);
}

function reusableFileEditorDiffPreviewItem(path) {
  const nextItem = fileEditorDiffPreviewItemFor(path);
  addFileEditorTabItem(path, nextItem);
  const replacements = new Map();
  for (const oldItem of fileEditorDiffPreviewItems()) {
    if (oldItem === nextItem) continue;
    const oldPath = fileItemPath(oldItem);
    if (!oldPath) continue;
    const oldState = fileStateFor(oldPath);
    if (oldState?.dirty && fileEditorTabItemsForPath(oldPath).length <= 1) continue;
    replacements.set(oldItem, nextItem);
    removeFileEditorTabItem(oldPath, oldItem);
    fileEditorViewModesForPath(oldPath).delete(oldItem);
    fileEditorViewState.delete(oldItem);
    fileEditorDiffExpandOverrides.delete(oldItem);
    tabLastActivatedAt.delete(oldItem);
    removePanelForItem(oldItem);
    if (!openFilePathHasOwner(oldPath) && !oldState?.dirty) deleteFileState(oldPath);
  }
  syncFileLayoutItems();
  if (replacements.size) {
    applyLayoutSlots(layoutWithReplacedItems(replacements), {focusSession: nextItem, prune: false});
  }
  return nextItem;
}

function openFilePathHasOwner(path) {
  return filePanelItemsForPath(path).length > 0;
}

function removeFilePanelOwner(path, item) {
  if (isImageViewerItem(item) && sharedImageViewerPath === path) sharedImageViewerPath = null;
  else removeFileEditorTabItem(path, item);
  fileEditorViewModesForPath(path).delete(item);
  // also drop the per-item CodeMirror scroll/selection state and the LRU timestamp on close
  // so these item-keyed maps don't grow unbounded as editor tabs open and close.
  fileEditorViewState.delete(item);
  fileEditorDiffExpandOverrides.delete(item);
  tabLastActivatedAt.delete(item);
  if (!openFilePathHasOwner(path)) fileStateFor(path)?.ownerSessions.clear();
}

function normalizedOpenFileOwnerSession(value) {
  const session = String(value || '').trim();
  return session && sessions.includes(session) ? session : '';
}

function rememberOpenFileOwner(path, ownerSession) {
  const session = normalizedOpenFileOwnerSession(ownerSession);
  if (!path || !session) return;
  ensureFileState(path)?.ownerSessions.add(session);
}

function openFileOwnerSessionsForPath(path) {
  return Array.from(fileStateFor(path)?.ownerSessions || [])
    .filter(session => sessions.includes(session))
    .sort((left, right) => sessions.indexOf(left) - sessions.indexOf(right) || left.localeCompare(right));
}

function removePanelForItem(item) {
  const panel = panelNodes.get(item);
  if (!panel) return;
  panel.remove();
  panelNodes.delete(item);
}

// A language switch must repaint the Finder's toolbar chrome (root/dates/sort/new-file… labels), which
// createFileExplorerPanel() bakes in at creation time and caches in panelNodes. Re-rendering only the
// panel BODIES (rerenderForLocale's other calls) leaves that chrome stale, and the toolbar mixes direct
// and delegated click handlers — so relabel-and-rebind would be fragile. Instead evict the cached Finder
// panel and let renderPanels() rebuild it from the single source of truth, then repopulate the tree.
function relocalizeFileExplorerPanels() {
  if (!panelNodes.has(fileExplorerItemId)) return;
  removePanelForItem(fileExplorerItemId);
  const remounted = typeof dockviewRemountPanel === 'function' && dockviewRemountPanel(fileExplorerItemId);
  if (!remounted) renderPanels(activePaneItems());
  refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  renderFileExplorerQuickAccessControls();
}

function setOpenFileOwner(path, item, options = {}) {
  let replacementSlots = null;
  rememberOpenFileOwner(path, options.ownerSession);
  if (isImageViewerItem(item)) {
    replacementSlots = replaceSharedImageViewerPath(path);
  } else {
    addFileEditorTabItem(path, item);
  }
  syncFileLayoutItems();
  return replacementSlots;
}

function replaceSharedImageViewerPath(path) {
  if (!path || sharedImageViewerPath === path) {
    sharedImageViewerPath = path || sharedImageViewerPath;
    return null;
  }
  const previousPath = sharedImageViewerPath;
  const previousItem = previousPath ? imageViewerItemFor(previousPath) : null;
  const nextItem = imageViewerItemFor(path);
  const nextSlots = previousItem && itemInLayout(previousItem)
    ? layoutWithReplacedItem(previousItem, nextItem)
    : null;
  if (previousItem) {
    removePanelForItem(previousItem);
    sharedImageViewerPath = null;
    if (!openFilePathHasOwner(previousPath)) {
      deleteFileState(previousPath);
    }
  }
  sharedImageViewerPath = path;
  return nextSlots;
}

function syncFileLayoutItems() {
  layoutItems = computeLayoutItems();
}

function showFileOpenError(path, message) {
  showToast(t('editor.fileOpenFailedTitle'), `${path}\n${message}`, {
    container: displayToastContainer(fileExplorerItemId),
    className: 'attention-alert toast',
  });
}

function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  const decimals = amount >= 10 || index === 0 ? 0 : 1;
  return `${amount.toFixed(decimals)} ${units[index]}`;
}

function fileErrorMessageSnapshot(message, fallbackKey, fallbackParams = {}) {
  const source = message && typeof message === 'object' ? message : null;
  const fallback = source ? '' : String(message || '');
  return userMessageSnapshot(source, {key: fallbackKey, params: {...fallbackParams}, fallback});
}

function fileErrorText(error, fallbackKey, fallbackParams = {}) {
  const fallback = t(fallbackKey, fallbackParams);
  return error && typeof error === 'object' ? userMessageText(error, fallback) : String(error || fallback);
}

function tooLargeFileState(size = null, message = null) {
  return {
    mtime: 0,
    kind: 'too-large',
    original: '',
    content: '',
    dirty: false,
    size,
    maxBytes: MAX_FILE_PREVIEW_BYTES,
    error: message ? fileErrorMessageSnapshot(message, 'editor.fileTooLargeDetail') : null,
  };
}

function rawPreviewFileState(path, entry = null, options = {}) {
  const renderer = options.renderer || previewRendererForPath(path);
  const mime = options.mime || previewMimeForPath(path);
  const mediaKind = renderer?.mediaKind || renderer?.id || 'unsupported';
  return {
    mtime: fileEntryMtime(entry),
    size: entry?.size ?? null,
    kind: 'media',
    mediaKind,
    mime,
    original: '',
    content: '',
    dirty: false,
  };
}

function rawPreviewFileStateFromMime(path, entry = null, mime = '') {
  const renderer = previewRendererForMime(mime);
  if (!renderer?.raw || renderer.textBacked) return null;
  return rawPreviewFileState(path, entry, {renderer, mime});
}

async function sniffedRawPreviewFileState(path, entry = null) {
  const fetched = await fetchFileInfoStatus(path);
  const info = fetched.info || {};
  const mime = info.preview_mime || info.mime || '';
  const state = rawPreviewFileStateFromMime(path, entry || info, mime);
  if (!state) return null;
  state.mtime = fileEntryMtime(entry || info);
  state.size = entry?.size ?? info.size ?? null;
  applyFileIdentityMetadata(state, info);
  return state;
}

function fileErrorState(message = null, fallbackKey = 'editor.fileLoadFailed', fallbackParams = {}) {
  return {
    mtime: 0,
    kind: 'error',
    original: '',
    content: '',
    dirty: false,
    error: fileErrorMessageSnapshot(message, fallbackKey, fallbackParams),
  };
}

function missingFileState(message = null) {
  const state = fileErrorState(message, 'dialog.missingOnDisk');
  state.externalMissing = true;
  return state;
}

function openFileIsMissing(path) {
  return openFiles.get(path)?.externalMissing === true;
}

function clearOpenFileExternalState(state) {
  if (!state) return state;
  delete state.externalChanged;
  delete state.externalMissing;
  delete state.externalError;
  delete state.externalChangeEditPrompted;
  delete state.externalReloadDeferred;
  return state;
}

function openFileStatus(state) {
  if (!state) return {message: '', level: ''};
  if (state.externalMissing) return {message: state.dirty ? `${t('dialog.missingOnDisk')} · ${t('dialog.unsavedChanges')}` : t('dialog.missingOnDisk'), level: 'warn'};
  if (state.externalError) return {message: `${t('dialog.unableLoadDisk')}: ${fileErrorText(state.externalError, 'editor.refreshFailed')}`, level: 'warn'};
  if (state.externalChanged) return {message: state.dirty ? t('dialog.staleStatus') : t('dialog.externalTitle'), level: 'warn'};
  if (state.dirty) return {message: t('state.modified'), level: ''};
  if (state.kind === 'text') {
    const count = String(state.original ?? '').length;
    return {message: tPlural('editor.status.characters', count), level: ''};
  }
  if (state.kind === 'image') return {message: state.size ? formatFileSize(state.size) : '', level: ''};
  if (state.kind === 'media') return {message: [state.mime || state.mediaKind, state.size ? formatFileSize(state.size) : ''].filter(Boolean).join(' · '), level: ''};
  return {message: '', level: ''};
}

function fileEditorAutosaveDelayMs() {
  const seconds = Number(fileEditorAutosaveDelaySeconds || 2.5);
  const clamped = Math.max(0.5, Math.min(60, Number.isFinite(seconds) ? seconds : 2.5));
  return Math.round(clamped * 1000);
}

function clearFileAutosaveTimer(path) {
  const timer = fileEditorAutosaveTimers.get(path);
  if (timer) clearTimeout(timer);
  fileEditorAutosaveTimers.delete(path);
}

function updateOpenFileDirtyFlag(path) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  state.dirty = state.content !== state.original;
  return state.dirty;
}

function syncOpenFileContentFromPanel(path, panel) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text' || !panel) return false;
  const cmContent = codeMirrorPanelContent(panel);
  if (cmContent === null) return false;
  state.content = cmContent;
  updateOpenFileDirtyFlag(path);
  return true;
}

function syncOpenFileContentFromPanels(path, preferredPanel = null) {
  if (syncOpenFileContentFromPanel(path, preferredPanel)) return true;
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel === preferredPanel) continue;
    if (syncOpenFileContentFromPanel(path, panel)) return true;
  }
  updateOpenFileDirtyFlag(path);
  return false;
}

function openFileAutosaveReady(path, state = openFiles.get(path)) {
  return !readOnlyMode
    && fileEditorAutosaveEnabled
    && state?.kind === 'text'
    && state.dirty
    && !state.externalChanged
    && !state.externalMissing
    && !state.externalError;
}

function scheduleFileAutosave(path) {
  clearFileAutosaveTimer(path);
  const state = openFiles.get(path);
  if (!openFileAutosaveReady(path, state)) return false;
  const timer = setTimeout(() => {
    fileEditorAutosaveTimers.delete(path);
    autoSaveFileEditor(path);
  }, fileEditorAutosaveDelayMs());
  fileEditorAutosaveTimers.set(path, timer);
  return true;
}

function rescheduleAllFileAutosaves() {
  for (const path of Array.from(fileEditorAutosaveTimers.keys())) clearFileAutosaveTimer(path);
  for (const [path, state] of openFiles.entries()) {
    if (openFileAutosaveReady(path, state)) scheduleFileAutosave(path);
  }
}

async function autoSaveFileEditor(path) {
  const state = openFiles.get(path);
  if (!openFileAutosaveReady(path, state)) return false;
  const panel = fileEditorPanelsForPath(path).find(candidate => candidate?._cmView) || fileEditorPanelsForPath(path)[0] || null;
  syncOpenFileContentFromPanels(path, panel);
  if (!openFileAutosaveReady(path, state)) return false;
  return saveFileEditor(path, panel, {autosave: true});
}

function truncateDialogText(text, maxChars = 20000) {
  const value = String(text || '');
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars)}\n\n${t('fileCompare.truncated', {count: value.length - maxChars})}`;
}

function diffDialogLines(text) {
  const lines = String(text ?? '').split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines.length ? lines : [''];
}

function lineDiffRows(leftText, rightText) {
  const left = diffDialogLines(leftText);
  const right = diffDialogLines(rightText);
  const cellCount = left.length * right.length;
  if (cellCount > 1_000_000) {
    const max = Math.max(left.length, right.length);
    return Array.from({length: max}, (_, index) => {
      const same = left[index] === right[index];
      return {
        left: left[index] ?? '',
        right: right[index] ?? '',
        leftKind: same ? 'same' : (index < left.length ? 'added' : 'blank'),
        rightKind: same ? 'same' : (index < right.length ? 'removed' : 'blank'),
      };
    });
  }
  const dp = Array.from({length: left.length + 1}, () => new Uint16Array(right.length + 1));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({left: left[i], right: right[j], leftKind: 'same', rightKind: 'same'});
      i += 1;
      j += 1;
    } else if (j >= right.length || (i < left.length && dp[i + 1][j] >= dp[i][j + 1])) {
      rows.push({left: left[i], right: '', leftKind: 'added', rightKind: 'blank'});
      i += 1;
    } else {
      rows.push({left: '', right: right[j], leftKind: 'blank', rightKind: 'removed'});
      j += 1;
    }
  }
  return rows;
}

function fileCompareLineHtml(kind, text) {
  return `<span class="file-compare-line ${esc(kind)}">${esc(text || ' ')}</span>`;
}

function fileConflictCompareHtml(editorText, diskText) {
  const rows = lineDiffRows(truncateDialogText(editorText), truncateDialogText(diskText));
  const editorHtml = rows.map(row => fileCompareLineHtml(row.leftKind, row.left)).join('');
  const diskHtml = rows.map(row => fileCompareLineHtml(row.rightKind, row.right)).join('');
  return `
    <div class="file-editor-conflict-compare">
      <section>
        <h4>${esc(t('conflict.unsaved'))}</h4>
        <pre data-file-compare-scroll>${editorHtml}</pre>
      </section>
      <section>
        <h4>${esc(t('conflict.onDisk'))}</h4>
        <pre data-file-compare-scroll>${diskHtml}</pre>
      </section>
    </div>`;
}

function bindFileConflictCompareScroll(root) {
  const panes = Array.from(root?.querySelectorAll?.('[data-file-compare-scroll]') || []);
  let syncing = false;
  for (const pane of panes) {
    pane.addEventListener('scroll', () => {
      if (syncing) return;
      syncing = true;
      for (const other of panes) {
        if (other === pane) continue;
        other.scrollTop = pane.scrollTop;
        other.scrollLeft = pane.scrollLeft;
      }
      syncing = false;
    });
  }
}

function showFileEditorDecisionDialog(options = {}) {
  const actions = Array.isArray(options.actions) && options.actions.length
    ? options.actions
    : [{id: 'cancel', label: t('common.cancel')}];
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = `app-modal-overlay file-editor-dialog-backdrop ${options.className || ''}`.trim();
    const actionButtons = actions.map(action => (
      `<button type="button" class="file-editor-dialog-action ${esc(action.variant || '')}" data-dialog-action="${esc(action.id)}">${esc(action.label || action.id)}</button>`
    )).join('');
    const body = options.bodyHtml
      ? `<div class="file-editor-dialog-body custom">${options.bodyHtml}</div>`
      : `<div class="file-editor-dialog-body">${esc(options.message || '')}</div>`;
    backdrop.innerHTML = `
      <section class="file-editor-dialog" role="dialog" aria-modal="true" aria-label="${esc(options.title || t('dialog.defaultTitle'))}">
        <div class="file-editor-dialog-title">${esc(options.title || t('dialog.defaultTitle'))}</div>
        ${body}
        <div class="file-editor-dialog-actions">${actionButtons}</div>
      </section>`;
    const finish = action => {
      document.removeEventListener('keydown', onKeydown, true);
      backdrop.remove();
      resolve(action);
    };
    const onKeydown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finish('cancel');
      }
    };
    backdrop.addEventListener('click', event => {
      const button = event.target?.closest?.('[data-dialog-action]');
      if (button && backdrop.contains(button)) {
        event.preventDefault();
        finish(button.dataset.dialogAction || 'cancel');
        return;
      }
      if (event.target === backdrop) finish('cancel');
    });
    document.addEventListener('keydown', onKeydown, true);
    appOverlayRootElement().appendChild(backdrop);
    options.onMount?.(backdrop);
    const preferred = backdrop.querySelector('[data-dialog-action]:not(.danger)') || backdrop.querySelector('[data-dialog-action]');
    preferred?.focus?.({preventScroll: true});
  });
}

async function showFileConflictCompareDialog(path, panel = null) {
  const state = openFiles.get(path);
  const loaded = await openFileStateFromDisk(path);
  const diskState = loaded.state;
  const diskText = diskState?.kind === 'text' ? diskState.content : loaded.missing ? t('dialog.missingOnDisk') : fileErrorText(diskState?.error, 'dialog.unableLoadDisk');
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.compareTitle', {name: basenameOf(path)}),
    bodyHtml: fileConflictCompareHtml(state?.content || '', diskText),
    actions: [
      {id: 'overwrite', label: t('dialog.overwriteDisk'), variant: 'danger'},
      {id: 'reload', label: t('dialog.keepDisk')},
      {id: 'cancel', label: t('common.cancel')},
    ],
    className: 'file-editor-compare-dialog',
    onMount: bindFileConflictCompareScroll,
  });
  if (action === 'overwrite') return saveFileEditor(path, panel, {force: true});
  if (action === 'reload') return reloadOpenFileFromDisk(path, {force: true});
  return false;
}

async function showFileSaveConflictDialog(path, panel = null, options = {}) {
  if (fileConflictDialogOpen(path)) return false;
  setFileConflictDialogOpen(path, true);
  try {
    const state = openFiles.get(path);
    if (state) {
      if (!state.externalChanged && !state.externalMissing) {
        state.externalChanged = {mtime: 0, size: null};
      }
      renderOpenFilePath(path);
    }
    const detail = options.message ? `\n\n${options.message}` : '';
    const action = await showFileEditorDecisionDialog({
      title: t('dialog.conflictTitle'),
      message: `${t('dialog.conflictMessage', {name: basenameOf(path)})}${detail}`,
      actions: [
        {id: 'overwrite', label: t('dialog.overwriteDisk'), variant: 'danger'},
        {id: 'reload', label: t('dialog.keepDisk')},
        {id: 'compare', label: t('dialog.compare')},
        {id: 'cancel', label: t('common.cancel')},
      ],
      className: 'file-editor-conflict-dialog',
    });
    if (action === 'overwrite') return saveFileEditor(path, panel, {force: true});
    if (action === 'reload') return reloadOpenFileFromDisk(path, {force: true});
    if (action === 'compare') return showFileConflictCompareDialog(path, panel);
    return false;
  } finally {
    setFileConflictDialogOpen(path, false);
  }
}

async function promptExternalChangeBeforeEditing(path, panel = null) {
  const state = openFiles.get(path);
  if (!state?.externalChanged || state.externalChangeEditPrompted) return false;
  // If the editor has NO unsaved changes, just reload the new disk content silently — never ask. The
  // reload dialog is only for the genuine conflict case (the editor has unsaved edits AND disk changed).
  if (!state.dirty) {
    await reloadOpenFileFromDisk(path, {force: true});
    return true;
  }
  state.externalChangeEditPrompted = true;
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.externalTitle'),
    message: t('dialog.externalMessage', {name: basenameOf(path)}),
    actions: [
      {id: 'reload', label: t('common.reload')},
      {id: 'dismiss', label: t('dialog.keepEditing')},
    ],
    className: 'file-editor-external-change-dialog',
  });
  if (action === 'reload') {
    await reloadOpenFileFromDisk(path, {force: true});
    return true;
  }
  setFileEditorPanelStatus(panel, t('dialog.staleStatus'), 'warn');
  renderOpenFilePath(path);
  return false;
}

async function confirmDirtyFileClose(path, panel = null) {
  const state = openFiles.get(path);
  if (!state?.dirty) return true;
  syncOpenFileContentFromPanels(path, panel);
  if (!state.dirty) return true;
  if (fileEditorAutosaveEnabled) {
    if (await saveFileEditor(path, panel, {autosave: true, closing: true})) return true;
    // autosave-on-close failed (transient error / on-disk conflict). Don't silently abort
    // the close with only a status line — fall through to the explicit save/discard/cancel dialog so
    // the user can retry, discard, or cancel.
  }
  const action = await showFileEditorDecisionDialog({
    title: t('dialog.closeTitle', {name: basenameOf(path)}),
    message: fileEditorAutosaveEnabled ? t('dialog.autosaveFailed') : t('dialog.unsavedChanges'),
    actions: [
      {id: 'save', label: t('common.save')},
      {id: 'discard', label: t('dialog.discard'), variant: 'danger'},
      {id: 'cancel', label: t('common.cancel')},
    ],
    className: 'file-editor-close-dialog',
  });
  if (action === 'save') return saveFileEditor(path, panel, {closing: true});
  if (action === 'discard') return true;
  return false;
}

function renderOpenFilePath(path) {
  for (const item of filePanelItemsForPath(path)) {
    const slot = slotForSession(item);
    const panel = panelNodes.get(item);
    if (panel && slot && activeItemForSide(slot) === item) renderFileEditorPanel(panel, item);
  }
  renderSessionButtons();
  renderPaneTabStrips();
}

function showFileEditorPaneForPath(path, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  activeFile = path;
  const replacementSlots = setOpenFileOwner(path, item, options);
  syncFileLayoutItems();
  scheduleFileExplorerActiveFileReveal(path);
  if (replacementSlots) applyLayoutSlots(replacementSlots, {focusSession: item, prune: false});
  return openFileEditorPane(path, {...options, item});
}

function openFilesSetAndShow(path, state, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  const replacementSlots = setOpenFileOwner(path, item, options);
  setFileState(path, state);
  syncFileLayoutItems();
  if (replacementSlots) applyLayoutSlots(replacementSlots, {focusSession: item, prune: false});
  return showFileEditorPaneForPath(path, {...options, item});
}

function openFileStateHasLoadedEditorPayload(state) {
  return Boolean(state?.kind && state.loading !== true && state.kind !== 'file');
}

function refreshOpenFileDiffDecorations(path) {
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel._cmView) panel._cmView.dispatch({});
  }
}

function primaryEditorItemForPath(path, fallbackItem = null) {
  const items = fileEditorTabItemsForPath(path).filter(item => !isImageViewerItem(item));
  const activeItem = items.find(item => itemIsActivePaneTab(item)) || items.find(item => item === focusedPanelItem);
  return activeItem || items[0] || fallbackItem || fileEditorItemFor(path);
}

function foldDuplicateEditorItemsForPath(path, keepItem = null) {
  const items = fileEditorTabItemsForPath(path).filter(item => !isImageViewerItem(item));
  if (items.length <= 1) return null;
  const keeper = items.includes(keepItem) ? keepItem : primaryEditorItemForPath(path);
  let nextSlots = layoutSlots;
  let layoutChanged = false;
  for (const item of items) {
    if (item === keeper) continue;
    if (itemInLayout(item, nextSlots)) {
      nextSlots = layoutWithoutItemFromSlots(item, nextSlots, {preserveRemovedSlot: true});
      layoutChanged = true;
    }
    removeFilePanelOwner(path, item);
    removePanelForItem(item);
  }
  syncFileLayoutItems();
  if (layoutChanged) applyLayoutSlots(nextSlots, {focusSession: keeper, prune: false});
  return keeper;
}

async function focusExistingPhysicalFileEditor(requestedPath, existingPath, options = {}) {
  if (!requestedPath || !existingPath) return null;
  const item = primaryEditorItemForPath(existingPath, options.item || null);
  foldDuplicateEditorItemsForPath(existingPath, item);
  if (options.viewMode) setFileEditorViewMode(existingPath, options.viewMode, item);
  else setFileEditorViewMode(existingPath, 'edit', item);
  recordEditorNav(item);
  const openOptions = {
    ...options,
    item,
    targetSlot: options.rehomeExisting === true ? options.targetSlot : null,
    targetZone: options.rehomeExisting === true ? options.targetZone : 'middle',
  };
  await showFileEditorPaneForPath(existingPath, openOptions);
  renderOpenFilePath(existingPath);
  const panel = panelNodes.get(item);
  if (panel && requestedPath !== existingPath) {
    setFileEditorPanelStatus(panel, t('editor.alreadyOpenAs', {name: basenameOf(existingPath)}), 'ok');
  }
  return item;
}

function openFileDiffAvailable(state) {
  if (!state?.diffLoaded) return false;
  const diff = String(state.diff || '');
  if (!diff && !state.diffWorkingMissing) return false;
  return Boolean(state.diff || state.diffWorkingMissing);
}

function fileStateCanRenderDiffView(path, state) {
  if (!state?.diffLoaded || state.diffUnavailable) return false;
  if (openFileDiffAvailable(state)) return true;
  return fileStateHasRepo(path, state) && fileStateHasUsefulGitHistory(state);
}

function applyOpenFileDiffPayload(state, payload) {
  state.diff = payload.diff || '';
  state.diffOriginal = payload.original || '';
  state.diffOriginalError = payload.original_error || '';
  state.diffWorking = payload.working || '';
  state.diffWorkingError = payload.working_error || '';
  state.diffRepo = payload.repo || '';
  if (!state.gitRoot && payload.repo) state.gitRoot = normalizeDirectoryPath(payload.repo);
  state.diffRelativePath = payload.relative_path || '';
  state.diffFromRef = payload.from_ref || '';
  state.diffToRef = payload.to_ref || '';
  state.diffWorkingMissing = payload.working_missing === true;
  state.untracked = payload.untracked === true;
  state.diffLoaded = true;
  state.diffUnavailable = false;
  state.diffError = '';
}

function markOpenFileDiffUnavailable(state, error) {
  state.diff = '';
  state.diffOriginal = '';
  state.diffWorking = '';
  state.diffLoaded = true;
  state.diffUnavailable = true;
  state.diffError = String(error || t('common.notAvailable'));
}

async function refreshOpenFileDiff(path, options = {}) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  // Dedup concurrent triggers (renderFileEditorPanel + ensureCodeMirrorDiffPanel both ask): a second
  // caller awaits the SAME in-flight load instead of returning early, so the diff panel never renders
  // a MergeView against an un-loaded (empty) original (which showed an untracked file all-green).
  if (state.diffLoading && state._diffLoadingPromise) return state._diffLoadingPromise;
  state.diffLoading = true;
  state._diffLoadingPromise = (async () => {
    try {
      // C6: diff the file against ITS repo's FROM/TO (not a global pair), so a per-repo selection applies.
      // When called from the Modified-files panel, explicit fromRef/toRef override the lookup so the diff
      // always matches exactly what the panel showed — even for repos not in diffRefsByRepo.
      const explicitFromRef = options.fromRef || '';
      const explicitToRef = options.toRef || '';
      if (explicitFromRef || explicitToRef) {
        state.diffPinnedFromRef = explicitFromRef || 'HEAD';
        state.diffPinnedToRef = explicitToRef || 'current';
      }
      const refString = (explicitFromRef || explicitToRef)
        ? `from=${encodeURIComponent(explicitFromRef || 'HEAD')}&to=${encodeURIComponent(explicitToRef || 'current')}`
        : (state.diffPinnedFromRef || state.diffPinnedToRef)
          ? `from=${encodeURIComponent(state.diffPinnedFromRef || 'HEAD')}&to=${encodeURIComponent(state.diffPinnedToRef || 'current')}`
          : diffRefQueryString(fileRepoForPath(path));
      const payload = await apiFetchJson(`/api/fs/diff?path=${encodeURIComponent(path)}&${refString}`);
      applyOpenFileDiffPayload(state, payload);
      refreshOpenFileDiffDecorations(path);
      return true;
    } catch (error) {
      markOpenFileDiffUnavailable(state, error);
      if (!options.silent) {
        for (const panel of fileEditorPanelsForPath(path)) setFileEditorPanelStatus(panel, t('editor.diffUnavailable', {error: String(error)}), 'warn');
      }
      return false;
    } finally {
      state.diffLoading = false;
      state._diffLoadingPromise = null;
      // Repaint after clearing diffLoading. Rendering while it is still true leaves the diff toolbar
      // disabled even though the MergeView has already been built, so the expand/collapse context button
      // ignores clicks until some unrelated render happens.
      for (const panel of fileEditorPanelsForPath(path)) {
        const item = panel.dataset.layoutItem || fileEditorItemFor(path);
        updateFileEditorDiffButton(panel.querySelector('.file-editor-diff-panel'), path, state, item);
        updateFileEditorDiffExpandButton(panel.querySelector('.file-editor-diff-expand-panel'), path, state, item);
        if (options.renderOnComplete !== false && editorViewModeFor(path, item) === 'diff') renderFileEditorPanel(panel, item);
      }
    }
  })();
  return state._diffLoadingPromise;
}

async function refreshOpenFileGitMetadata(path) {
  const state = openFiles.get(path);
  if (!state || state.kind !== 'text') return false;
  try {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
    const current = openFiles.get(path);
    if (!current || current.kind !== 'text') return false;
    applyFileGitMetadata(current, payload);
    return true;
  } catch (_error) {
    const current = openFiles.get(path);
    if (current && current.kind === 'text') {
      current.gitRoot = '';
      current.gitTracked = false;
      current.gitHistory = [];
      current.gitHasHistory = false;
    }
    return false;
  }
}

async function openFileInEditor(fullPath, entryOrName, options = {}) {
  const entry = typeof entryOrName === 'object' && entryOrName ? entryOrName : null;
  const name = entry?.name || String(entryOrName || basenameOf(fullPath));
  const kind = openFileKindForPreviewPath(name);
  const identityDedupe = kind === 'text';
  if (identityDedupe) {
    const pendingOpen = fileOpenPromisesByPath.get(fullPath);
    if (pendingOpen) {
      await pendingOpen.catch(() => null);
      const existingAfterPending = openPathForPhysicalFile(fullPath, entry) || (openFiles.has(fullPath) ? fullPath : '');
      if (existingAfterPending) return focusExistingPhysicalFileEditor(fullPath, existingAfterPending, options);
    }
    const existingIdentityPath = openPathForPhysicalFile(fullPath, entry);
    if (existingIdentityPath && existingIdentityPath !== fullPath) {
      return focusExistingPhysicalFileEditor(fullPath, existingIdentityPath, options);
    }
  }
  const defaultItem = kind === 'image' && imageOpenUsesSharedViewer(options)
    ? imageViewerItemFor(fullPath)
    : fileEditorItemFor(fullPath);
  const alreadyOpen = openFileStateHasLoadedEditorPayload(openFiles.get(fullPath));
  const item = identityDedupe && alreadyOpen
    ? primaryEditorItemForPath(fullPath, options.item || defaultItem)
    : (options.item || defaultItem);
  const openOptions = {
    ...options,
    item,
    ownerSession: options.ownerSession || normalizedOpenFileOwnerSession(entry?.session),
  };
  if (options.viewMode) setFileEditorViewMode(fullPath, options.viewMode, item);
  else setFileEditorViewMode(fullPath, defaultFileEditorViewModeForPath(fullPath, kind), item);
  recordEditorNav(item);   // push this tab to the back/forward history (no-op while navigating)
  if (alreadyOpen) {
    foldDuplicateEditorItemsForPath(fullPath, item);
    await refreshOpenFileGitMetadata(fullPath);
    await showFileEditorPaneForPath(fullPath, openOptions);
    renderOpenFilePath(fullPath);
    return item;
  }
  if (Number(entry?.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(entry.size));
    state.mtime = fileEntryMtime(entry);
    applyFileIdentityMetadata(state, entry);
    await openFilesSetAndShow(fullPath, state, openOptions);
    return item;
  }
  if (kind === 'image') {
    await openFilesSetAndShow(fullPath, applyFileIdentityMetadata({mtime: fileEntryMtime(entry), kind: 'image', original: '', content: '', dirty: false, size: entry?.size ?? null}, entry), openOptions);
    return item;
  }
  if (kind === 'media') {
    await openFilesSetAndShow(fullPath, applyFileIdentityMetadata(rawPreviewFileState(fullPath, entry), entry), openOptions);
    return item;
  }
  const openPromise = (async () => {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(fullPath)}`);
    if (identityDedupe) {
      const existingIdentityPath = openPathForPhysicalFile(fullPath, payload);
      if (existingIdentityPath && existingIdentityPath !== fullPath) {
        return focusExistingPhysicalFileEditor(fullPath, existingIdentityPath, options);
      }
    }
    const state = applyFileGitMetadata({
      mtime: filePayloadMtime(payload),
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    }, payload);
    await openFilesSetAndShow(fullPath, state, openOptions);
    return item;
  })();
  if (identityDedupe) fileOpenPromisesByPath.set(fullPath, openPromise);
  try {
    return await openPromise;
  } catch (err) {
    const status = Number(err?.status) || 0;
    if (status) {
      let state = status === 415 ? await sniffedRawPreviewFileState(fullPath, entry) : null;
      if (!state) {
        state = status === 413
          ? tooLargeFileState(entry?.size ?? null, err)
          : status === 404
            ? missingFileState(err)
            : fileErrorState(err);
      }
      await openFilesSetAndShow(fullPath, state, openOptions);
      return item;
    }
    showFileOpenError(fullPath, err);
    return null;
  } finally {
    if (fileOpenPromisesByPath.get(fullPath) === openPromise) fileOpenPromisesByPath.delete(fullPath);
  }
}

async function openFileInAdditionalEditorTab(fullPath, entryOrName, options = {}) {
  const item = options.item || fileEditorCopyItemFor(fullPath);
  return openFileInEditor(fullPath, entryOrName, {...options, item, forceNewTab: true});
}

async function openFileStateFromDisk(path, entry = null) {
  const fetched = entry ? {entry, missing: false, error: '', network: false} : await fetchFileEntryStatus(path);
  const fileEntry = fetched.entry;
  if (!fileEntry) {
    if (fetched.missing) return {missing: true};
    return {state: fileErrorState(fetched.error)};
  }
  if (Number(fileEntry.size) > MAX_FILE_PREVIEW_BYTES) {
    const state = tooLargeFileState(Number(fileEntry.size));
    state.mtime = fileEntryMtime(fileEntry);
    return {state};
  }
  const kind = openFileKindForPreviewPath(fileEntry.name || basenameOf(path));
  if (kind === 'image') {
    return {state: {
      mtime: fileEntryMtime(fileEntry),
      size: fileEntry.size ?? null,
      kind: 'image',
      original: '',
      content: '',
      dirty: false,
    }};
  }
  if (kind === 'media') {
    return {state: rawPreviewFileState(path, fileEntry)};
  }
  try {
    const payload = await apiFetchJson(`/api/fs/read?path=${encodeURIComponent(path)}`);
    return {state: applyFileGitMetadata({
      mtime: filePayloadMtime(payload),
      size: payload.size,
      kind: 'text',
      original: payload.content,
      content: payload.content,
      dirty: false,
    }, payload)};
  } catch (error) {
    const status = Number(error?.status) || 0;
    if (status) {
      let state = status === 415 ? await sniffedRawPreviewFileState(path, fileEntry) : null;
      if (!state) {
        state = status === 413
          ? tooLargeFileState(fileEntry.size ?? null, error)
          : status === 404
            ? missingFileState(error)
            : fileErrorState(error);
      }
      state.mtime = fileEntryMtime(fileEntry);
      state.size = fileEntry.size ?? null;
      return {state};
    }
    return {state: fileErrorState(error)};
  }
}

function markOpenFileMissing(path) {
  const state = openFiles.get(path);
  if (!state) return;
  if (state.dirty) {
    state.externalMissing = true;
    delete state.externalChanged;
    delete state.externalError;
  } else {
    setFileState(path, missingFileState());
  }
  renderOpenFilePath(path);
}

function markOpenFileExternalError(path, error) {
  const state = openFiles.get(path);
  if (!state) return;
  clearFileAutosaveTimer(path);
  state.externalError = fileErrorMessageSnapshot(error, 'editor.refreshFailed');
  renderOpenFilePath(path);
}

async function replaceOpenFileStateFromDisk(path, entry = null) {
  const previous = openFiles.get(path);
  const viewStates = fileEditorTabItemsForPath(path).map(item => {
    const panel = panelNodes.get(item);
    if (panel) captureFileEditorPanelViewState(item, panel);
    return {item, panel};
  });
  const loaded = await openFileStateFromDisk(path, entry);
  if (loaded.missing) {
    markOpenFileMissing(path);
    return false;
  }
  clearFileAutosaveTimer(path);
  setFileState(path, clearOpenFileExternalState(loaded.state));
  renderOpenFilePath(path);
  for (const {item} of viewStates) {
    const panel = panelNodes.get(item);
    if (panel) restoreFileEditorPanelViewState(item, panel);
  }
  requestAnimationFrame(() => {
    for (const {item} of viewStates) {
      const panel = panelNodes.get(item);
      if (panel) restoreFileEditorPanelViewState(item, panel);
    }
  });
  if (loaded.state?.kind && typeof updateFilePreviewPopout === 'function' && editorPreviewModeAvailable(path, loaded.state)) {
    updateFilePreviewPopout(path, loaded.state.content || '');
  }
  if (previous?.diff !== undefined) refreshOpenFileDiff(path);
  return true;
}

function fileEditorPathHasFocus(path) {
  const active = document.activeElement;
  if (!active) return false;
  return fileEditorPanelsForPath(path).some(panel => panel?.contains?.(active));
}

function openFileBackgroundReloadShouldDefer(path, state) {
  if (fileEditorPathHasFocus(path)) return true;
  const lastCleanAt = Number(state?.lastCleanAt || 0);
  return Number.isFinite(lastCleanAt)
    && lastCleanAt > 0
    && Date.now() - lastCleanAt < openFileBackgroundReloadDeferMs;
}

function markOpenFileReloadDeferred(path, state, entry) {
  if (!state) return;
  state.externalChanged = {mtime: fileEntryMtime(entry), size: entry?.size ?? null};
  state.externalReloadDeferred = {mtime: state.externalChanged.mtime, size: state.externalChanged.size, at: Date.now()};
  delete state.externalChangeEditPrompted;
  delete state.externalMissing;
  delete state.externalError;
  for (const panel of fileEditorPanelsForPath(path)) {
    updateFileEditorPanelChrome(panel, path);
    const status = openFileStatus(state);
    setFileEditorPanelStatus(panel, status.message, status.level);
  }
}

async function reloadOpenFileFromDisk(path, options = {}) {
  const state = openFiles.get(path);
  if (!state) return false;
  if (state.dirty && options.force !== true) {
    const confirmed = window.confirm(t('dialog.externalMessage', {name: basenameOf(path)}));
    if (!confirmed) return false;
  }
  return replaceOpenFileStateFromDisk(path);
}

async function refreshOpenFileFromFetchedStatus(path, state, fetched) {
  const entry = fetched.entry;
  if (!entry) {
    if (fetched.missing) {
      if (state.externalMissing) return;
      clearFileAutosaveTimer(path);
      markOpenFileMissing(path);
    } else {
      markOpenFileExternalError(path, fetched.error);
    }
    return;
  }
  if (!fileEntryChanged(state, entry)) {
    if (state.externalChanged || state.externalMissing || state.externalError) {
      delete state.externalMissing;
      delete state.externalError;
      if (!state.dirty) delete state.externalChanged;
      if (!state.dirty) delete state.externalReloadDeferred;
      if (!state.dirty) delete state.externalChangeEditPrompted;
      renderOpenFilePath(path);
    }
    if (state.dirty) scheduleFileAutosave(path);
    return;
  }
  if (state.dirty) {
    const externalChanged = {mtime: fileEntryMtime(entry), size: entry.size ?? null};
    if (state.externalChanged
      && state.externalChanged.mtime === externalChanged.mtime
      && state.externalChanged.size === externalChanged.size) {
      return;
    }
    state.externalChanged = externalChanged;
    delete state.externalReloadDeferred;
    delete state.externalChangeEditPrompted;
    delete state.externalMissing;
    delete state.externalError;
    clearFileAutosaveTimer(path);
    renderOpenFilePath(path);
    return;
  }
  if (openFileBackgroundReloadShouldDefer(path, state)) {
    markOpenFileReloadDeferred(path, state, entry);
    return;
  }
  await replaceOpenFileStateFromDisk(path, entry);
}

async function refreshOpenFilesIfChanged(options = {}) {
  const requestedPaths = new Set((Array.isArray(options.paths) ? options.paths : [])
    .map(path => String(path || ''))
    .filter(Boolean));
  for (const [path, state] of Array.from(openFiles.entries())) {
    const requested = requestedPaths.has(path);
    if (requestedPaths.size && !requested) continue;
    if (!state || state.loading) continue;
    // Poll on-disk staleness only for the file editor that is the visible/active tab; a file sitting in a
    // background tab does not need ~1/sec polling (it is re-checked when activated). Dirty files are always
    // polled — external-change conflict detection and autosave must never be skipped just because hidden.
    if (!requested && !state.dirty && !itemIsActivePaneTab(fileEditorItemPrefix + path)) continue;
    const fetched = await fetchFileEntryStatus(path);
    await refreshOpenFileFromFetchedStatus(path, state, fetched);
  }
}

function watchedFileExplorerDirectories() {
  const root = currentFileExplorerRoot();
  const directories = new Set();
  if (!fileExplorerTreePaneIsVisible()) return [];
  directories.add(root);
  for (const path of fileExplorerExpanded) {
    if (pathIsInsideDirectory(path, root)) directories.add(normalizeDirectoryPath(path));
  }
  return Array.from(directories);
}

function visibleFileEditorWatchFiles() {
  return Array.from(new Set(activePaneItems()
    .filter(isFileEditorItem)
    .map(item => fileItemPath(item))
    .filter(path => path && path.startsWith('/'))))
    .sort();
}

function backgroundFileEditorWatchFiles() {
  const visible = new Set(visibleFileEditorWatchFiles());
  return Array.from(new Set(paneItems()
    .filter(isFileEditorItem)
    .map(item => fileItemPath(item))
    .filter(path => path && path.startsWith('/') && !visible.has(path))))
    .sort();
}

function clientServerWatchRoots() {
  const roots = new Set(watchedFileExplorerDirectories());
  if (fileExplorerSessionFilesPaneIsVisible()) {
    for (const repo of fileExplorerSessionFilesPayload?.repos || []) {
      const path = normalizeDirectoryPath(repo?.repo || repo?.root || '');
      if (path && path !== '/') roots.add(path);
    }
    for (const file of fileExplorerSessionFilesPayload?.files || []) {
      const path = normalizeDirectoryPath(file?.abs_path || sessionFileAbsolutePath(file));
      if (path && path !== '/') roots.add(dirnameOf(path));
    }
  }
  return Array.from(roots)
    .map(path => normalizeDirectoryPath(path))
    .filter(path => path && path.startsWith('/'))
    .sort();
}

function transcriptPreviewPaneIsActive(session) {
  if (!isTmuxSession(session)) return false;
  const pane = document.getElementById(`transcript-pane-${session}`);
  const preview = document.getElementById(transcriptDomId(session));
  return Boolean(pane?.classList?.contains(CLS.active) && preview?.isConnected);
}

function transcriptContextWatchRequests() {
  return activeSessions
    .filter(transcriptPreviewPaneIsActive)
    .map(session => ({session, messages: transcriptPreviewMessages}));
}

function clientServerWatchState() {
  const state = {
    roots: clientServerWatchRoots(),
    files: visibleFileEditorWatchFiles(),
    background_files: backgroundFileEditorWatchFiles(),
    context_items: transcriptContextWatchRequests(),
  };
  if (typeof activitySummaryIsVisible === 'function') {
    state.activity_summary = {
      visible: activitySummaryIsVisible(),
      locale: typeof i18nActiveLocaleId === 'function' ? i18nActiveLocaleId() : 'en',
      scope: 'all',
      hours: typeof infoSessionFileLookbackHours === 'number' ? infoSessionFileLookbackHours : 24,
    };
  }
  if (fileExplorerSessionFilesPaneIsVisible() && typeof clientSessionFilesWatchRequests === 'function') {
    state.session_files = clientSessionFilesWatchRequests();
  }
  return state;
}

function syncServerWatchRootsNow(options = {}) {
  if (readOnlyMode || !clientPushCanSupplyData() || serverWatchRootsInFlight) return;
  const state = clientServerWatchState();
  const signature = JSON.stringify(state);
  const renewDue = options.renew === true && Date.now() - serverWatchRootsSyncedAt >= 240000;
  if (signature === serverWatchRootsSignature && !renewDue) return;
  serverWatchRootsSignature = signature;
  serverWatchRootsInFlight = true;
  apiFetch('/api/watch/roots', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(state),
  }).then(() => {
    serverWatchRootsSyncedAt = Date.now();
  }).catch(() => {
    serverWatchRootsSignature = '';
  }).finally(() => {
    serverWatchRootsInFlight = false;
  });
}

function syncServerWatchRoots(options = {}) {
  serverWatchRootsPendingOptions = {
    ...serverWatchRootsPendingOptions,
    ...options,
    renew: serverWatchRootsPendingOptions.renew === true || options.renew === true,
  };
  if (serverWatchRootsTimer) clearTimeout(serverWatchRootsTimer);
  const delay = options.immediate === true ? 0 : serverWatchDebounceMs;
  serverWatchRootsTimer = setTimeout(() => {
    serverWatchRootsTimer = null;
    const pending = serverWatchRootsPendingOptions;
    serverWatchRootsPendingOptions = {};
    syncServerWatchRootsNow(pending);
  }, delay);
}

async function refreshFileExplorerIfChanged() {
  if (!fileExplorerTreePaneIsVisible()) return;
  const directories = watchedFileExplorerDirectories();
  if (!directories.length) return;
  let changed = false;
  const entriesByDir = new Map();
  const signaturesByDir = new Map();
  const listings = await Promise.all(directories.map(async directory => ({
    directory,
    entries: await fetchDirectory(directory, {recordSignature: false, fresh: true}),
  })));
  for (const {directory, entries} of listings) {
    if (!entries) continue;
    const normalizedDirectory = normalizeDirectoryPath(directory);
    entriesByDir.set(normalizedDirectory, entries);
    const signature = directoryEntriesSignature(entries);
    signaturesByDir.set(normalizedDirectory, signature);
    const previous = fileExplorerDirectorySignatures.get(normalizedDirectory);
    if (previous !== undefined && previous !== signature) changed = true;
  }
  if (changed && fileExplorerUserIsActive()) {
    deferFileExplorerRefresh();
    return;
  }
  for (const [directory, signature] of signaturesByDir.entries()) {
    setLimitedMapEntry(fileExplorerDirectorySignatures, directory, signature, fileExplorerMemoryCacheLimit);
  }
  if (changed) await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true, entriesByDir});
}

async function refreshWatchedFilesystem(options = {}) {
  if (filesystemRefreshInFlight) return;
  filesystemRefreshInFlight = true;
  try {
    if (fileExplorerTreePaneIsVisible()) {
      if (options.full === true && typeof refreshFileExplorerFromWatchDiff === 'function') {
        await refreshFileExplorerFromWatchDiff({full: true}, {full: true});
      } else {
        await refreshFileExplorerIfChanged();
      }
    }
    await refreshOpenFilesIfChanged();
    if (fileExplorerSessionFilesPaneIsVisible()) {
      fetchSessionFiles({destination: 'finder', session: fileExplorerSessionFilesTargetSession(), silent: true});
    }
    syncServerWatchRoots();
  } finally {
    filesystemRefreshInFlight = false;
  }
}

function slotForNewFileEditorTab() {
  const focusedSlot = isFileEditorItem(focusedPanelItem) ? slotForSession(focusedPanelItem) : null;
  if (focusedSlot) return focusedSlot;
  return layoutSlotKeys().find(slot => paneTabs(slot).some(isFileEditorItem)) || null;
}

function layoutColumnNode(slot) {
  return grid?.querySelector(`.layout-column[data-slot="${cssEscape(slot)}"]`) || null;
}

function largestPaneSlot(exclude = new Set()) {
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (exclude.has(slot) || !paneTabs(slot).length) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    if (!best || area > best.area) best = {slot, area};
  }
  return best?.slot || null;
}

function largestNonFileExplorerPaneSlot(exclude = new Set()) {
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (exclude.has(slot) || !paneTabs(slot).length || isFileExplorerItem(activeItemForSide(slot))) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    if (!best || area > best.area) best = {slot, area};
  }
  return best?.slot || null;
}

function largestPaneSlotForFileEditor(exclude = new Set()) {
  const excludedSlots = exclude instanceof Set ? exclude : new Set(Array.isArray(exclude) ? exclude : [exclude]);
  const filesSlot = slotForSession(fileExplorerItemId);
  const filesRect = filesSlot ? layoutColumnNode(filesSlot)?.getBoundingClientRect() : null;
  let fallback = null;
  let best = null;
  for (const slot of layoutSlotKeys()) {
    if (excludedSlots.has(slot) || slot === filesSlot || !paneTabs(slot).length) continue;
    const rect = layoutColumnNode(slot)?.getBoundingClientRect();
    const area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
    const candidate = {slot, area};
    if (!fallback || area > fallback.area) fallback = candidate;
    if (filesRect && rect && rect.left < filesRect.right - 1) continue;
    if (!best || area > best.area) best = candidate;
  }
  return (best || fallback)?.slot || null;
}

async function openFileEditorPane(path, options = {}) {
  const item = options.item || fileEditorItemFor(path);
  syncFileLayoutItems();
  compactCurrentLayoutSlots({focusSession: item});
  renderSessionButtons();
  const existingSlot = slotForSession(item);
  const targetSlot = options.targetSlot && layoutSlotKeys().includes(options.targetSlot) ? options.targetSlot : null;
  const targetZone = options.targetZone || 'middle';
  const targetIndex = Number.isFinite(Number(options.targetIndex)) ? Number(options.targetIndex) : null;
  if (existingSlot && options.rehomeExisting !== true) {
    activatePaneTab(existingSlot, item);
    return;
  }
  if (targetSlot && targetZone !== 'middle') {
    await splitSessionAtSlot(item, targetSlot, targetZone, existingSlot, options.pct || null);
    return;
  }
  if (targetSlot && !slotIsFileExplorerPane(targetSlot)) {
    await moveSessionToSlot(item, targetSlot, existingSlot, targetIndex ?? paneTabs(targetSlot).length);
    return;
  }
  if (existingSlot) {
    activatePaneTab(existingSlot, item);
    return;
  }
  const activationSlot = slotForTabActivation(item);
  if (activationSlot && !slotIsFileExplorerPane(activationSlot)) {
    await moveSessionToSlot(item, activationSlot, null, paneTabs(activationSlot).length);
    return;
  }
  const editorSlot = slotForNewFileEditorTab();
  if (editorSlot) {
    await moveSessionToSlot(item, editorSlot, null, paneTabs(editorSlot).length);
    return;
  }
  const largestSlot = largestPaneSlotForFileEditor();
  if (largestSlot) {
    await moveSessionToSlot(item, largestSlot, null, paneTabs(largestSlot).length);
    return;
  }
  const filesSlot = slotForSession(fileExplorerItemId);
  const fallbackSlot = layoutSlotKeys().find(slot => slot !== filesSlot) || filesSlot;
  if (fallbackSlot) {
    const zone = fallbackSlot === filesSlot ? 'right' : 'left';
    const pct = fallbackSlot === filesSlot ? fileExplorerSplitPercent : defaultSplitPercent;
    await splitSessionAtSlot(item, fallbackSlot, zone, null, pct);
    return;
  }
  await moveSessionToSlot(item, slotForNewSession(), null);
}

function codeMirrorLanguageName(path) {
  const ext = fileExtensionOf(path);
  if (ext === '.html' || ext === '.htm') return 'html';
  const language = syntaxLanguageForPath(path);
  if (language === 'bash') return 'shell';
  if (language === 'ini') return 'toml';
  if (language === 'javascript' || language === 'typescript') return language;
  return language || '';
}

function codeMirrorApiIsUsable(api) {
  return Boolean(
    api?.EditorState?.create
    && api?.Compartment
    && api?.EditorState?.readOnly?.of
    && api?.EditorView?.theme
    && api?.EditorView?.editable?.of
    && (api?.EditorView?.lineWrapping || api?.EditorView?.contentAttributes?.of)
    && api?.keymap?.of
    && api?.drawSelection
    && api?.highlightActiveLine
    && api?.search
    && api?.openSearchPanel
  );
}

function loadCodeMirrorBundleScript(options = {}) {
  if (!options.force && codeMirrorApiIsUsable(window.YOLOmuxCodeMirror)) return Promise.resolve(window.YOLOmuxCodeMirror);
  if (options.force) codeMirrorBundlePromise = null;
  if (!codeMirrorBundlePromise) {
    codeMirrorBundlePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const assetUrl = bootstrap.codeMirrorAssetUrl || '/static/codemirror.js';
      const separator = assetUrl.includes('?') ? '&' : '?';
      script.src = options.force ? `${assetUrl}${separator}retry=${Date.now()}` : assetUrl;
      script.async = true;
      script.onload = () => resolve(window.YOLOmuxCodeMirror || null);
      script.onerror = () => reject(new Error(t('editor.codemirrorBundleLoadFailed', {url: script.src})));
      document.head.appendChild(script);
    });
  }
  return codeMirrorBundlePromise;
}

async function loadCodeMirrorApi() {
  if (codeMirrorApiIsUsable(window.YOLOmuxCodeMirror)) return window.YOLOmuxCodeMirror;
  if (!codeMirrorApiPromise) {
    codeMirrorApiPromise = (async () => {
      let bundleError = null;
      try {
        let bundledApi = await loadCodeMirrorBundleScript();
        if (codeMirrorApiIsUsable(bundledApi)) return bundledApi;
        bundledApi = await loadCodeMirrorBundleScript({force: true});
        if (codeMirrorApiIsUsable(bundledApi)) return bundledApi;
        bundleError = new Error(t('editor.codemirrorBundleMissingExports'));
      } catch (err) {
        bundleError = err;
      }
      const detail = bundleError?.message || t('common.notAvailable');
      throw new Error(t('editor.codemirrorBundleUnavailable', {detail, path: '/static/codemirror.js'}));
    })();
  }
  try {
    return await codeMirrorApiPromise;
  } catch (error) {
    codeMirrorApiPromise = null;
    throw error;
  }
}

function codeMirrorMarkdownCodeLanguages(api) {
  if (!api?.LanguageDescription) return null;
  const stream = mode => safeCodeMirrorExtension('stream language', () => (mode && api.StreamLanguage ? api.StreamLanguage.define(mode) : null));
  const languageEntries = [
    {name: 'JavaScript', alias: ['js', 'jsx', 'node'], support: () => api.javascript?.({jsx: true})},
    {name: 'TypeScript', alias: ['ts', 'tsx'], support: () => api.javascript?.({typescript: true, jsx: true})},
    {name: 'Python', alias: ['py'], support: () => api.python?.()},
    {name: 'Rust', alias: ['rs'], support: () => api.rust?.()},
    {name: 'JSON', alias: ['jsonc'], support: () => api.json?.()},
    {name: 'HTML', alias: ['htm'], support: () => api.html?.()},
    {name: 'CSS', alias: ['scss'], support: () => api.css?.()},
    {name: 'XML', alias: ['svg'], support: () => api.xml?.()},
    {name: 'YAML', alias: ['yml'], support: () => api.yaml?.()},
    {name: 'Shell', alias: ['sh', 'bash', 'zsh'], support: () => stream(api.shell)},
    {name: 'TOML', alias: ['ini'], support: () => stream(api.toml)},
  ];
  return languageEntries.flatMap(entry => {
    const support = safeCodeMirrorExtension(entry.name, () => entry.support?.());
    if (codeMirrorExtensionIsEmpty(support)) return [];
    const description = safeCodeMirrorExtension(entry.name, () => api.LanguageDescription.of({
        name: entry.name,
        alias: entry.alias,
        support,
      }));
    return codeMirrorExtensionIsEmpty(description) ? [] : [description];
  });
}

function codeMirrorExtensionIsEmpty(extension) {
  return !extension || (Array.isArray(extension) && extension.length === 0);
}

function safeCodeMirrorExtension(label, factory) {
  try {
    return factory?.() || [];
  } catch (error) {
    console.warn(`CodeMirror ${label} extension unavailable; using plain text`, error);
    return [];
  }
}

function codeMirrorLanguageExtension(api, path) {
  const language = codeMirrorLanguageName(path);
  if (language === 'javascript') return safeCodeMirrorExtension(language, () => api.javascript?.({jsx: true}));
  if (language === 'typescript') return safeCodeMirrorExtension(language, () => api.javascript?.({typescript: true, jsx: true}));
  if (language === 'python') return safeCodeMirrorExtension(language, () => api.python?.());
  if (language === 'rust') return safeCodeMirrorExtension(language, () => api.rust?.());
  if (language === 'json') return safeCodeMirrorExtension(language, () => api.json?.());
  if (language === 'html') return safeCodeMirrorExtension(language, () => api.html?.());
  if (language === 'xml') return safeCodeMirrorExtension(language, () => api.xml?.());
  if (language === 'css') return safeCodeMirrorExtension(language, () => api.css?.());
  if (language === 'markdown') {
    const codeLanguages = codeMirrorMarkdownCodeLanguages(api);
    return safeCodeMirrorExtension(language, () => (codeLanguages?.length ? api.markdown?.({codeLanguages}) : api.markdown?.()));
  }
  if (language === 'yaml') return safeCodeMirrorExtension(language, () => api.yaml?.());
  if (language === 'shell' && api.shell) return safeCodeMirrorExtension(language, () => api.StreamLanguage?.define(api.shell));
  if (language === 'toml' && api.toml) return safeCodeMirrorExtension(language, () => api.StreamLanguage?.define(api.toml));
  return [];
}

function codeMirrorHighlightExtension(api) {
  if (!api?.syntaxHighlighting) return [];
  if (!api.HighlightStyle || !api.tags) {
    return api.defaultHighlightStyle ? safeCodeMirrorExtension('default highlight', () => api.syntaxHighlighting(api.defaultHighlightStyle, {fallback: true})) : [];
  }
  const t = api.tags;
  const palette = {
    text: 'var(--editor-scheme-fg)',
    muted: 'var(--code-comment)',
    keyword: 'var(--code-keyword)',
    control: 'var(--code-control)',
    atom: 'var(--code-atom)',
    string: 'var(--code-string)',
    number: 'var(--code-number)',
    variable: 'var(--code-variable)',
    function: 'var(--code-function)',
    type: 'var(--code-type)',
    property: 'var(--code-property)',
    tag: 'var(--code-tag)',
    heading: 'var(--markdown-heading)',
    headingBg: 'var(--markdown-heading-bg)',
    strong: 'var(--markdown-strong)',
    emphasis: 'var(--markdown-emphasis)',
    link: 'var(--markdown-link)',
    inlineCode: 'var(--code-inline)',
    inlineCodeBg: 'var(--code-inline-bg)',
    invalid: 'var(--code-invalid)',
  };
  const tags = (...items) => items.filter(Boolean);
  const headingStyle = {tag: tags(t.heading, t.heading1, t.heading2), color: palette.heading, fontWeight: '700'};
  if (palette.headingBg) headingStyle.backgroundColor = palette.headingBg;
  return safeCodeMirrorExtension('highlight', () => api.syntaxHighlighting(api.HighlightStyle.define([
    {tag: t.keyword, color: palette.keyword},
    {tag: tags(t.controlKeyword, t.operatorKeyword), color: palette.control || palette.keyword},
    {tag: tags(t.atom, t.bool, t.null), color: palette.atom},
    {tag: tags(t.string, t.special(t.string), t.regexp), color: palette.string},
    {tag: tags(t.number, t.integer, t.float), color: palette.number},
    {tag: tags(t.function(t.variableName), t.function(t.propertyName)), color: palette.function},
    {tag: tags(t.typeName, t.className, t.namespace), color: palette.type},
    {tag: tags(t.propertyName, t.attributeName), color: palette.property},
    {tag: tags(t.variableName, t.self, t.definition(t.variableName)), color: palette.variable},
    {tag: tags(t.tagName, t.angleBracket), color: palette.tag},
    {tag: tags(t.comment, t.meta), color: palette.muted},
    headingStyle,
    {tag: t.strong, fontWeight: '700', color: palette.strong},
    {tag: t.emphasis, fontStyle: 'italic', color: palette.emphasis},
    {tag: tags(t.link, t.url), color: palette.link, textDecoration: 'underline'},
    {tag: tags(t.monospace, t.processingInstruction), color: palette.inlineCode, backgroundColor: palette.inlineCodeBg},
    {tag: t.invalid, color: palette.invalid},
  ]), {fallback: true}));
}

function codeMirrorThemeExtension(api) {
  const scheme = activeEditorScheme();
  const cursorColor = editorCursorColorForScheme(scheme);
  return api.EditorView.theme({
    '&': {
      height: '100%',
      color: scheme.fg,
      backgroundColor: scheme.bg,
    },
    '.cm-scroller': {
      fontFamily: 'var(--editor-font)',
      fontSize: 'var(--editor-font-size)',
      lineHeight: 'var(--editor-line-height)',
    },
    '.cm-content': {
      caretColor: cursorColor,
      padding: '8px 10px',
    },
    '.cm-cursor': {
      borderLeftColor: cursorColor,
      borderLeftWidth: '2px',
    },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground': {
      backgroundColor: scheme.selection,
      boxShadow: scheme.dark ? 'inset 0 0 0 1px rgba(191, 219, 254, 0.42)' : 'inset 0 0 0 1px rgba(29, 78, 216, 0.24)',
    },
    '.cm-content ::selection': {
      backgroundColor: 'transparent !important',
      color: 'inherit !important',
    },
    '.cm-content ::-moz-selection': {
      backgroundColor: 'transparent !important',
      color: 'inherit !important',
    },
    '.cm-activeLine': {
      backgroundColor: scheme.activeLine,
    },
    '.cm-activeLineGutter': {
      backgroundColor: scheme.activeLine,
      color: scheme.fg,
    },
    '.cm-gutters': {
      color: scheme.lineNo,
      backgroundColor: scheme.gutterBg,
      borderRightColor: scheme.line,
    },
    '.cm-panels': {
      // shared pane-chrome bar: the find/replace panel matches the tab-strip bar (bright when this pane
      // is focused, neutral gray when not) instead of a fixed gray — see --pane-bar-bg.
      color: scheme.fg,
      backgroundColor: 'var(--pane-bar-bg)',
    },
    '.cm-searchMatch': {
      color: scheme.dark ? '#0b1020' : '#111827',
      backgroundColor: scheme.dark ? 'rgba(255, 213, 74, 0.86)' : 'rgba(255, 204, 0, 0.86)',
      boxShadow: `inset 0 0 0 1px ${scheme.dark ? 'rgba(255, 244, 184, 0.95)' : 'rgba(132, 83, 0, 0.72)'}`,
      borderRadius: '2px',
      fontWeight: '700',
    },
    '.cm-searchMatch-selected': {
      color: scheme.dark ? '#111827' : '#111827',
      backgroundColor: scheme.dark ? '#ffd166' : '#ff9f1c',
      boxShadow: `inset 0 0 0 2px ${scheme.dark ? '#fff2a8' : '#7c2d12'}, 0 0 0 1px ${scheme.dark ? 'rgba(0, 0, 0, 0.46)' : 'rgba(255, 255, 255, 0.68)'}`,
      borderRadius: '2px',
      fontWeight: '800',
    },
  }, {dark: scheme.dark});
}

function codeMirrorWrapMarkerRowsForBlock(block, lineHeight, textBlockType = 0) {
  if (!block) return 1;
  if (block.type !== undefined && block.type !== textBlockType) return 1;
  if (block.widget) return 1;
  const rowHeight = Math.max(1, Number(lineHeight) || 1);
  const height = Number(block.height);
  const measuredHeight = Number.isFinite(height) && height > 0 ? height : rowHeight;
  return Math.max(1, Math.round(measuredHeight / rowHeight));
}

function codeMirrorWrapMarkerExtension(api) {
  if (!api.ViewPlugin) return [];
  const scheme = activeEditorScheme();
  const textBlockType = Number.isFinite(Number(api.BlockType?.Text)) ? Number(api.BlockType.Text) : 0;
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.frame = 0;
      this.layer = document.createElement('div');
      this.layer.className = 'cm-wrap-marker-layer';
      view.scrollDOM.appendChild(this.layer);
      this.queue();
    }

    update(update) {
      if (update.docChanged || update.viewportChanged || update.geometryChanged) this.queue();
    }

    queue() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.render();
      });
    }

    render() {
      const view = this.view;
      const lineHeight = view.defaultLineHeight || Number.parseFloat(getComputedStyle(view.contentDOM).lineHeight) || editorFontSize;
      const line = view.contentDOM.querySelector('.cm-line') || view.contentDOM;
      const lineStyle = getComputedStyle(line);
      const paddingLeft = Number.parseFloat(lineStyle.paddingLeft) || 0;
      const markerWidth = Math.max(12, Math.min(28, lineHeight * 0.9));
      const markerLeft = Math.max(0, view.contentDOM.offsetLeft + Math.max(0, paddingLeft - markerWidth));
      const blocks = Array.from(view.viewportLineBlocks || []);
      const nodes = [];
      for (const block of blocks) {
        const rows = codeMirrorWrapMarkerRowsForBlock(block, lineHeight, textBlockType);
        for (let row = 1; row < rows; row += 1) {
          const marker = document.createElement('span');
          marker.className = 'cm-wrap-marker';
          marker.textContent = '↪';
          marker.style.left = `${markerLeft}px`;
          marker.style.top = `${Math.round(block.top + lineHeight * row)}px`;
          marker.style.height = `${Math.round(lineHeight)}px`;
          marker.style.width = `${Math.round(markerWidth)}px`;
          nodes.push(marker);
        }
      }
      this.layer.replaceChildren(...nodes);
    }

    destroy() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.layer.remove();
    }
  }, {dark: scheme.dark});
}

// inline git blame. Lazily fetch the /api/blame payload into the path's fileState record.
// dedup concurrent fetches (multiple open panels for one path share a single request).
async function fetchEditorBlame(path) {
  return dedupeInflight(editorBlameFetches, path, true, () => (async () => {
    try {
      const data = await apiFetchJson(`/api/blame?path=${encodeURIComponent(path)}`);
      setEditorBlameForPath(path, data);
      return data;
    } catch (_error) {
      return null;
    }
  })());
}

// when a text editor opens/renders with blame already ON but its blame isn't cached yet
// (the common case: enable blame, THEN open a new file), fetch it and nudge the open editors so the
// blame ViewPlugin recomputes its decoration against the now-populated cache — no manual toggle needed.
async function ensureEditorBlameForPath(path) {
  if (!fileEditorBlameEnabled || !path || hasEditorBlameForPath(path)) return;
  await fetchEditorBlame(path);
  if (!fileEditorBlameEnabled || !hasEditorBlameForPath(path)) return;
  for (const panel of fileEditorPanelsForPath(path)) {
    const view = panel?._cmView;
    if (!view) continue;
    // A selection-preserving transaction has selectionSet=true, which is what the blame ViewPlugin
    // listens for — so it rebuilds the current-line annotation now that the data is present.
    try { view.dispatch({selection: view.state.selection}); } catch (_) {}
  }
}

function blameAnnotationText(info) {
  const author = info.author || '';
  const rel = info.time ? relativeTimeFormat(Math.max(0, Math.floor(Date.now() / 1000) - info.time)) : '';
  const pr = info.pr ? ` (#${info.pr})` : '';
  return `${author}, ${rel} • ${info.summary || ''}${pr}`.trim();
}

function blameHoverText(info) {
  const author = info.author || '';
  const abs = info.time ? new Date(info.time * 1000).toLocaleString() : '';
  const sha = info.sha && !/^0+$/.test(info.sha) ? ` [${info.sha.slice(0, 8)}]` : '';
  return `${author} • ${abs}${sha}\n${info.summary || ''}`.trim();
}

// Decorate the CURSOR's current line with the dim blame annotation (rendered via a CSS ::after that
// flows after the line text — the bundle exposes no WidgetType). The dim color comes from the editor
// scheme's --code-comment token (theme-aware), and the full commit is the line's native title tooltip.
function codeMirrorBlameExtension(api, path) {
  if (!fileEditorBlameEnabled || !api.ViewPlugin || !api.Decoration) return [];
  if (!fileStateHasUsefulGitHistory(fileStateFor(path))) return [];
  const lineDeco = info => api.Decoration.line({attributes: {'data-blame': blameAnnotationText(info), title: blameHoverText(info)}});
  const build = view => {
    const blame = editorBlameForPath(path);
    if (!blame || !blame.lines) return api.Decoration.none;
    // annotate EVERY visible line when fileEditorBlameAllLines is on, else just the cursor line
    // (the Popular IDE default). Viewport-scoped so a huge file only decorates what's on screen.
    if (fileEditorBlameAllLines) {
      const ranges = [];
      const visible = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const {from, to} of visible) {
        let pos = from;
        while (pos <= to) {
          const line = view.state.doc.lineAt(pos);
          const info = blame.lines[String(line.number)];
          if (info) ranges.push(lineDeco(info).range(line.from));
          if (line.to + 1 <= pos) break;   // guard against a zero-length advance
          pos = line.to + 1;
        }
      }
      return ranges.length ? api.Decoration.set(ranges, true) : api.Decoration.none;
    }
    const line = view.state.doc.lineAt(view.state.selection.main.head);
    const info = blame.lines[String(line.number)];
    if (!info) return api.Decoration.none;
    return api.Decoration.set([lineDeco(info).range(line.from)]);
  };
  return api.ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = build(view); }
    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged) this.decorations = build(update.view);
    }
  }, {decorations: plugin => plugin.decorations});
}

function codeMirrorHtmlSemanticEmphasisExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'html' || !api.ViewPlugin || !api.Decoration) return [];
  const palette = activeEditorScheme().syntax;
  const strongMark = api.Decoration.mark({
    attributes: {style: `font-weight:700;color:${palette.strong}`},
  });
  const emphasisMark = api.Decoration.mark({
    attributes: {style: `font-style:italic;color:${palette.emphasis}`},
  });
  const semanticTagPattern = /<(strong|b|em|i)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi;
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const visible of visibleRanges) {
        const text = view.state.doc.sliceString(visible.from, visible.to);
        semanticTagPattern.lastIndex = 0;
        let match;
        while ((match = semanticTagPattern.exec(text))) {
          const openTagEnd = match[0].indexOf('>') + 1;
          if (openTagEnd <= 0) continue;
          const from = visible.from + match.index + openTagEnd;
          const to = from + match[2].length;
          if (to <= from) continue;
          const mark = /^(?:strong|b)$/i.test(match[1]) ? strongMark : emphasisMark;
          ranges.push(mark.range(from, to));
        }
      }
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

function codeMirrorMarkdownStrongExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'markdown' || !api.ViewPlugin || !api.Decoration) return [];
  const palette = activeEditorScheme().syntax;
  const strongMark = api.Decoration.mark({
    attributes: {style: `font-weight:700;color:${palette.strong}`},
  });
  const strongPattern = /(\*\*[^*\n](?:.|\n)*?\*\*|__[^_\n](?:.|\n)*?__)/g;
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: view.state.doc.length}];
      for (const visible of visibleRanges) {
        const text = view.state.doc.sliceString(visible.from, visible.to);
        strongPattern.lastIndex = 0;
        let match;
        while ((match = strongPattern.exec(text))) {
          const from = visible.from + match.index;
          const to = from + match[0].length;
          if (to > from) ranges.push(strongMark.range(from, to));
        }
      }
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

function codeMirrorMarkdownFallbackSyntaxExtension(api, path) {
  if (codeMirrorLanguageName(path) !== 'markdown' || !api.ViewPlugin || !api.Decoration) return [];
  const markByClass = new Map();
  const mark = className => {
    if (!markByClass.has(className)) markByClass.set(className, api.Decoration.mark({class: className}));
    return markByClass.get(className);
  };
  const addMarkRange = (ranges, className, from, to) => {
    if (to > from) ranges.push(mark(className).range(from, to));
  };
  const fenceMatch = lineText => String(lineText || '').match(/^\s*(```|~~~)\s*([A-Za-z0-9_+#.-]*)/);
  const fenceStateBeforeLine = (doc, lineNo) => {
    let inFence = false;
    let language = '';
    for (let scanLineNo = 1; scanLineNo < lineNo; scanLineNo += 1) {
      const match = fenceMatch(doc.line(scanLineNo).text);
      if (!match) continue;
      if (inFence) {
        inFence = false;
        language = '';
      } else {
        inFence = true;
        language = match[2] || '';
      }
    }
    return {inFence, language};
  };
  const addFenceTokenMarks = (ranges, language, lineText, lineFrom) => {
    if (typeof simpleCodeSyntaxTokens !== 'function') return;
    for (const token of simpleCodeSyntaxTokens(language, lineText)) {
      if (!token?.className || token.to <= token.from) continue;
      addMarkRange(ranges, token.className, lineFrom + token.from, lineFrom + token.to);
    }
  };
  const addInlineMarks = (ranges, lineText, lineFrom) => {
    const inlinePatterns = [
      ['md-code', /`[^`\n]+`/g],
      ['md-bold', /\*\*[^*\n]+\*\*|__[^_\n]+__/g],
      ['md-link', /\[[^\]\n]+\]\([^)]+\)/g],
      ['md-html', /<\/?[A-Za-z][^>\n]*?>/g],
      ['md-italic', /(^|[^\w*])(\*[^*\s\n][^*\n]*\*|_[^_\s\n][^_\n]*_)/g, 2],
    ];
    for (const [className, pattern, groupIndex] of inlinePatterns) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(lineText))) {
        const text = groupIndex ? match[groupIndex] : match[0];
        if (!text) continue;
        const groupOffset = groupIndex ? match[0].indexOf(text) : 0;
        const from = lineFrom + match.index + groupOffset;
        const to = from + text.length;
        addMarkRange(ranges, className, from, to);
      }
    }
  };
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.decorations = this.build(view);
    }

    update(update) {
      if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view);
    }

    build(view) {
      const ranges = [];
      const doc = view.state.doc;
      const visibleRanges = view.visibleRanges?.length ? view.visibleRanges : [{from: 0, to: doc.length}];
      for (const visible of visibleRanges) {
        const startLine = doc.lineAt(visible.from).number;
        const endLine = doc.lineAt(Math.max(visible.from, visible.to)).number;
        let {inFence, language: fenceLanguage} = fenceStateBeforeLine(doc, startLine);
        for (let lineNo = startLine; lineNo <= endLine; lineNo += 1) {
          const line = doc.line(lineNo);
          const text = line.text;
          const fence = fenceMatch(text);
          if (fence) {
            addMarkRange(ranges, 'md-fence', line.from, line.to);
            if (inFence) {
              inFence = false;
              fenceLanguage = '';
            } else {
              inFence = true;
              fenceLanguage = fence[2] || '';
            }
            continue;
          }
          if (inFence) {
            addMarkRange(ranges, 'md-codeblock', line.from, line.to);
            addFenceTokenMarks(ranges, fenceLanguage, text, line.from);
            continue;
          }
          const heading = text.match(/^(\s{0,3})(#{1,6})(\s+.*)$/);
          if (heading) {
            addMarkRange(ranges, `md-heading md-heading-${heading[2].length}`, line.from, line.to);
            continue;
          }
          if (/^\s*>\s?/.test(text)) addMarkRange(ranges, 'md-blockquote', line.from, line.to);
          const list = text.match(/^\s*(?:[-*+]|\d+\.)\s+/);
          if (list) addMarkRange(ranges, 'md-list-marker', line.from, line.from + list[0].length);
          addInlineMarks(ranges, text, line.from);
        }
      }
      ranges.sort((left, right) => left.from - right.from || (left.startSide || 0) - (right.startSide || 0) || left.to - right.to);
      return api.Decoration.set(ranges, true);
    }
  }, {
    decorations: plugin => plugin.decorations,
  });
}

// parseUnifiedDiffLineClasses + codeMirrorDiffLineExtension were removed. The edit view no
// longer paints inline diff decorations (cm-yolomux-diff-add / deletion markers); changes are shown
// ONLY in the explicit diff VIEW (the MergeView built by ensureCodeMirrorDiffPanel, with its own
// .cm-changedLine/.cm-insertedChunk styling). The per-file cached diff-line class map is gone too.

function escapeRegExpLiteral(text) {
  return String(text || '').replace(/[\\^$.*+?()[\]{}|]/g, '\\$&');
}

function codeMirrorSearchCheckboxState(panel) {
  const checkboxes = Array.from(panel?.querySelectorAll?.('input[type="checkbox"]') || []);
  return {
    caseSensitive: Boolean(checkboxes[0]?.checked),
    regexp: Boolean(checkboxes[1]?.checked),
    wholeWord: Boolean(checkboxes[2]?.checked),
  };
}

function isSearchWordChar(char) {
  return /[A-Za-z0-9_]/.test(char || '');
}

function codeMirrorSearchMatches(text, query, options = {}) {
  if (!query) return [];
  const source = String(text || '');
  const flags = options.caseSensitive ? 'g' : 'gi';
  let pattern;
  try {
    pattern = options.regexp ? new RegExp(query, flags) : new RegExp(escapeRegExpLiteral(query), flags);
  } catch (_) {
    return [];
  }
  const matches = [];
  let match;
  while ((match = pattern.exec(source))) {
    const from = match.index;
    const to = from + match[0].length;
    if (to === from) {
      pattern.lastIndex += 1;
      continue;
    }
    if (options.wholeWord && (isSearchWordChar(source[from - 1]) || isSearchWordChar(source[to]))) continue;
    matches.push({from, to});
  }
  return matches;
}

function codeMirrorSearchMatchSummary(text, query, selection = {}, options = {}) {
  const matches = codeMirrorSearchMatches(text, query, options);
  if (!query) return {current: 0, total: 0, text: ''};
  if (!matches.length) return {current: 0, total: 0, text: '0/0'};
  const from = Number(selection.from);
  const to = Number(selection.to);
  const head = Number.isFinite(Number(selection.head)) ? Number(selection.head) : from;
  let index = matches.findIndex(match => match.from === from && match.to === to);
  if (index < 0) {
    index = matches.findIndex(match => match.from <= head && match.to >= head);
  }
  if (index < 0) {
    index = matches.findIndex(match => match.from >= head);
  }
  if (index < 0) index = matches.length - 1;
  return {current: index + 1, total: matches.length, text: `${index + 1}/${matches.length}`};
}

function codeMirrorPhraseValues() {
  return {
    Find: t('common.find'),
    Replace: t('common.replace'),
    next: t('preview.find.next'),
    previous: t('preview.find.previous'),
    all: t('editor.search.all'),
    'match case': t('editor.search.matchCase'),
    regexp: t('editor.search.regexp'),
    'by word': t('editor.search.wholeWord'),
    'whole word short': t('editor.search.wholeWordShort'),
    replace: t('common.replace'),
    'replace all': t('editor.search.replaceAll'),
    close: t('preview.find.close'),
  };
}

function codeMirrorLocaleExtensions(api, panel) {
  const extension = safeCodeMirrorExtension('localized phrases', () => api.EditorState.phrases.of(codeMirrorPhraseValues()));
  if (!panel || !api.Compartment) return extension;
  panel._cmLocaleCompartment = panel._cmLocaleCompartment || new api.Compartment();
  return panel._cmLocaleCompartment.of(extension);
}

// When you navigate search matches, CodeMirror's default scrollIntoView lands on a far-right horizontal
// position if the document has any long line (e.g. a padded locale JSON, 276-char line): a match on a
// SHORT line then sits off-screen left and the editor looks "scrolled all the way to the right" (blank).
// Re-center the match horizontally on every search-driven selection change — for a short line that
// reveals the line start; for a long line it keeps the match comfortably in view. (Deferred to a rAF
// because dispatch() is not allowed inside an update listener.)
function codeMirrorSearchScrollFix(api) {
  if (!api.EditorView?.updateListener || !api.EditorView?.scrollIntoView) return [];
  return api.EditorView.updateListener.of(update => {
    if (!update.selectionSet) return;
    if (!update.transactions.some(tr => tr.isUserEvent?.('select.search'))) return;
    const head = update.state.selection.main.head;
    const view = update.view;
    requestAnimationFrame(() => {
      try {
        view.dispatch({effects: api.EditorView.scrollIntoView(head, {x: 'center', y: 'nearest'})});
      } catch (_) {}
    });
  });
}

function codeMirrorSearchPanelEnhancementExtension(api) {
  if (!api.ViewPlugin) return [];
  return api.ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.frame = 0;
      this.panel = null;
      this.onPanelEvent = () => this.queue();
      this.queue();
    }

    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged || update.geometryChanged) this.queue();
    }

    queue() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.render();
      });
    }

    bindPanel(panel) {
      if (panel === this.panel) return;
      this.panel?.removeEventListener?.('input', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('change', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('click', this.onPanelEvent, true);
      this.panel?.removeEventListener?.('keyup', this.onPanelEvent, true);
      this.panel = panel;
      panel?.addEventListener?.('input', this.onPanelEvent, true);
      panel?.addEventListener?.('change', this.onPanelEvent, true);
      panel?.addEventListener?.('click', this.onPanelEvent, true);
      panel?.addEventListener?.('keyup', this.onPanelEvent, true);
    }

    render() {
      const panel = this.view.dom?.querySelector?.('.cm-search');
      this.bindPanel(panel);
      syncCodeMirrorFindButtonForView(this.view);
      refreshCodeMirrorFindOverview(this.view.dom?.closest?.('.file-editor-panel'));
      if (!panel) return;
      const phrases = codeMirrorPhraseValues();
      const next = panel.querySelector?.('.cm-button[name="next"]');
      const previous = panel.querySelector?.('.cm-button[name="prev"]');
      if (next) {
        const title = `${phrases.next} (Enter)`;
        next.title = title;
        next.setAttribute('aria-label', title);
      }
      if (previous) {
        const title = `${phrases.previous} (Shift+Enter)`;
        previous.title = title;
        previous.setAttribute('aria-label', title);
      }
      for (const button of panel.querySelectorAll?.('.cm-button[name="select"], .cm-button[name="replaceAll"]') || []) {
        button.dataset.searchLabel = phrases.all;
      }
      const wordLabel = panel.querySelector?.('label:has(input[name="word"])');
      if (wordLabel) wordLabel.dataset.searchLabel = phrases['whole word short'];
      let count = panel.querySelector?.('.cm-search-count');
      if (!count) {
        count = document.createElement('span');
        count.className = 'cm-search-count';
        count.setAttribute('aria-live', 'polite');
        const anchor = panel.querySelector?.('.cm-button[name="replaceAll"]') || panel.querySelector?.('.cm-button[name="select"]');
        anchor?.insertAdjacentElement?.('afterend', count) || panel.appendChild(count);
      }
      const query = panel.querySelector?.('input[name="search"]')?.value || '';
      const selection = this.view.state?.selection?.main || {};
      count.textContent = codeMirrorSearchMatchSummary(
        this.view.state?.doc?.toString?.() || '',
        query,
        selection,
        codeMirrorSearchCheckboxState(panel),
      ).text;
    }

    destroy() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.bindPanel(null);
    }
  });
}

function codeMirrorSearchPanelForHost(host = null) {
  if (!host) return null;
  return host._cmView?.dom?.querySelector?.('.cm-search') || host.querySelector?.('.cm-search') || null;
}

function codeMirrorSearchPanelOpenForHost(host = null) {
  return Boolean(codeMirrorSearchPanelForHost(host));
}

function syncCodeMirrorFindButtonForView(view) {
  const panel = view?.dom?.closest?.('.file-editor-panel');
  const button = panel?.querySelector?.('.file-editor-find-panel');
  if (button) button.setAttribute('aria-pressed', codeMirrorSearchPanelOpenForHost(panel) ? 'true' : 'false');
}

function openCodeMirrorFindForView(api, view) {
  if (!api?.openSearchPanel || !view) return false;
  view.focus?.();
  return api.openSearchPanel(view);
}

function updateCodeMirrorCursorStatus(panel) {
  const view = panel?._cmView;
  const status = panel?.querySelector?.('.file-editor-cursor-status');
  updateFileEditorCountStatus(panel);
  if (!status) return;
  if (!view) {
    status.textContent = '';
    return;
  }
  const main = view.state.selection.main;
  const line = view.state.doc.lineAt(main.head);
  const column = main.head - line.from + 1;
  const selectedChars = view.state.selection.ranges.reduce((sum, range) => sum + Math.abs(range.to - range.from), 0);
  const selections = view.state.selection.ranges.length;
  const selectionText = selectedChars
    ? ` · ${tPlural('editor.status.selections', selections)} · ${tPlural('editor.status.selectedChars', selectedChars)}`
    : '';
  status.textContent = `${line.number}:${column}${selectionText}`;
}

function fileEditorTextMetrics(text) {
  const value = String(text ?? '');
  const words = (value.trim().match(/\S+/g) || []).length;
  return {
    lines: value ? value.split('\n').length : 1,
    words,
    characters: value.length,
  };
}

function fileEditorCountStatusText(text) {
  return t('editor.status.counts', fileEditorTextMetrics(text));
}

function fileEditorStatusSourceText(panel) {
  const viewText = panel?._cmView?.state?.doc?.toString?.();
  if (viewText !== undefined && viewText !== null) return viewText;
  const path = panel?.dataset?.filePath || '';
  const state = openFiles.get(path);
  return state?.kind === 'text' ? state.content || '' : '';
}

function updateFileEditorCountStatus(panel) {
  const status = panel?.querySelector?.('.file-editor-count-status');
  if (!status) return;
  const path = panel?.dataset?.filePath || '';
  const state = path ? openFiles.get(path) : null;
  const text = fileEditorStatusSourceText(panel);
  status.textContent = state?.kind === 'text' || panel?._cmView ? fileEditorCountStatusText(text) : '';
}

function codeMirrorExtensions(api, panel, path, options = {}) {
  const save = options.save || (() => saveFileEditor(path, panel));
  const saveKeymap = api.keymap.of([{
    key: 'Mod-s',
    run() {
      save();
      return true;
    },
  }]);
  const findKeymap = api.openSearchPanel ? api.keymap.of([{
    key: 'Mod-f',
    run(view) {
      return openCodeMirrorFindForView(api, view);
    },
  }]) : [];
  const powerKeymap = api.keymap.of([
    api.openSearchPanel ? {
      key: 'Mod-h',
      run(view) {
        return openCodeMirrorFindForView(api, view);
      },
    } : null,
    api.gotoLine ? {
      key: 'Mod-g',
      run(view) {
        return api.gotoLine(view);
      },
    } : null,
    api.toggleComment ? {
      key: 'Mod-/',
      run(view) {
        return api.toggleComment(view);
      },
    } : null,
    api.indentLess ? {
      key: 'Shift-Tab',
      run(view) {
        return api.indentLess(view);
      },
    } : null,
    api.indentMore ? {
      key: 'Tab',
      run(view) {
        return api.indentMore(view);
      },
    } : null,
  ].filter(Boolean));
  return [
    codeMirrorLocaleExtensions(api, panel),
    api.history(),
    api.drawSelection(),
    codeMirrorContextMenuSelectionExtension(api),
    api.dropCursor(),
    api.rectangularSelection(),
    api.crosshairCursor(),
    api.indentOnInput(),
    api.bracketMatching(),
    api.foldGutter(),
    api.highlightActiveLine(),
    codeMirrorEditorOptionCompartmentExtensions(api, panel, options),
    api.search({top: true}),
    codeMirrorSearchPanelEnhancementExtension(api),
    codeMirrorSearchScrollFix(api),
    api.highlightSelectionMatches(),
    saveKeymap,
    findKeymap,
    powerKeymap,
    api.keymap.of([api.indentWithTab, ...api.defaultKeymap, ...api.historyKeymap, ...api.searchKeymap]),
    codeMirrorBlameExtension(api, path),
    api.EditorState.readOnly.of(readOnlyMode),
    api.EditorView.editable.of(!readOnlyMode),
    ...(options.plain ? [codeMirrorThemeOnlyExtensions(api, panel)] : [codeMirrorLanguageExtension(api, path), codeMirrorThemedExtensions(api, panel, path)]),
  ];
}

async function removeOpenFile(path, options = {}) {
  const confirmDirty = options.confirmDirty !== false;
  const shouldRender = options.render !== false;
  if (!path || !openFiles.has(path)) return;
  const state = openFiles.get(path);
  const requestedItem = options.item && fileItemPath(options.item) === path ? options.item : null;
  const closePanel = requestedItem ? panelNodes.get(requestedItem) : fileEditorPanelsForPath(path)[0];
  if (confirmDirty && state?.dirty && !(await confirmDirtyFileClose(path, closePanel))) return false;
  const items = requestedItem ? [requestedItem] : filePanelItemsForPath(path);
  if (!items.length) return false;
  clearFileAutosaveTimer(path);
  let nextSlots = layoutSlots;
  let wasInLayout = false;
  for (const item of items) {
    if (itemInLayout(item, nextSlots)) {
      nextSlots = layoutWithoutItemFromSlots(item, nextSlots, {preserveRemovedSlot: true});
      wasInLayout = true;
    }
    removeFilePanelOwner(path, item);
    removePanelForItem(item);
  }
  if (!openFilePathHasOwner(path)) {
    deleteFileState(path);
  }
  syncFileLayoutItems();
  if (activeFile === path && !openFilePathHasOwner(path)) {
    const remaining = Array.from(openFiles.keys());
    activeFile = remaining[remaining.length - 1] || null;
  }
  updateFileExplorerCurrentFileHighlight();
  if (wasInLayout) applyLayoutSlots(nextSlots);
  if (shouldRender) renderSessionButtons();
  return true;
}

function closeFileTab(path, options = {}) {
  return removeOpenFile(path, options);
}

function layoutWithReplacedItem(oldItem, newItem) {
  return layoutWithReplacedItems(new Map([[oldItem, newItem]]));
}

function layoutWithReplacedItems(replacements) {
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = layoutSlots[layoutTreeKey];
  for (const side of layoutSlotKeys()) {
    if (paneIsPlaceholder(side)) {
      next[side] = emptyPlaceholderPaneState();
      continue;
    }
    const tabs = paneTabs(side).map(item => replacements.get(item) || item);
    const activeItem = activeItemForSide(side);
    const active = replacements.get(activeItem) || activeItem;
    next[side] = paneStateWithTabs(tabs, active);
  }
  return compactLayoutSlots(next);
}

function renameOpenFilePath(oldPath, newPath) {
  if (!oldPath || !newPath || oldPath === newPath || !openFiles.has(oldPath)) return;
  const oldItem = fileEditorItemFor(oldPath);
  const newItem = fileEditorItemFor(newPath);
  const state = fileStateFor(oldPath);
  const wasInLayout = itemInLayout(oldItem);
  const panelItems = [oldItem].filter(item => panelNodes.has(item));
  deleteFileState(oldPath);
  setFileState(newPath, state);
  if (state.editorTabItems.delete(oldItem)) state.editorTabItems.add(newItem);
  const viewModes = state.viewMode;
  for (const [oldKey, newKey] of [[oldItem, newItem]]) {
    if (viewModes.has(oldKey)) {
      viewModes.set(newKey, viewModes.get(oldKey));
      viewModes.delete(oldKey);
    }
    // migrate the item-keyed scroll/selection state and the LRU timestamp on rename too,
    // so they don't leak under the old item id (and the LRU ordering survives the rename).
    if (fileEditorViewState.has(oldKey)) {
      fileEditorViewState.set(newKey, fileEditorViewState.get(oldKey));
      fileEditorViewState.delete(oldKey);
    }
    if (fileEditorDiffExpandOverrides.has(oldKey)) {
      fileEditorDiffExpandOverrides.set(newKey, fileEditorDiffExpandOverrides.get(oldKey));
      fileEditorDiffExpandOverrides.delete(oldKey);
    }
    if (tabLastActivatedAt.has(oldKey)) {
      tabLastActivatedAt.set(newKey, tabLastActivatedAt.get(oldKey));
      tabLastActivatedAt.delete(oldKey);
    }
  }
  if (viewModes.has(oldPath)) {
    viewModes.set(newPath, viewModes.get(oldPath));
    viewModes.delete(oldPath);
  }
  for (const item of panelItems) {
    const panel = panelNodes.get(item);
    if (panel) panel.remove();
    panelNodes.delete(item);
  }
  if (activeFile === oldPath) activeFile = newPath;
  syncFileLayoutItems();
  if (wasInLayout) applyLayoutSlots(layoutWithReplacedItems(new Map([[oldItem, newItem]])), {focusSession: newItem});
  else {
    renderSessionButtons();
    renderPaneTabStrips();
    updateFileExplorerCurrentFileHighlight();
  }
}

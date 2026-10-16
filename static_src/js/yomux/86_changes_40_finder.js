function createFileExplorerPanel(item = finderItemId) {
  const view = fileExplorerViewForItem(item) || 'finder';
  const initialPath = fileExplorerRoot || homePath || '/';
  const label = fileExplorerItemLabel(item);
  const reloadButtonHtml = `<button type="button" class="changes-refresh file-explorer-refresh-cluster" data-file-explorer-refresh title="${esc(t('common.refresh'))}" aria-label="${esc(t('common.refresh'))}">${esc(t('common.reload'))}</button>`;
  const finderToolbarHtml = view === 'finder' ? `<div class="file-explorer-toolbar">
          <div class="file-explorer-toolbar-row file-explorer-path-row">
            <input class="file-explorer-path-inline" type="text" value="${esc(initialPath)}" spellcheck="false" aria-label="${esc(t('finder.toolbar.rootPath', {name: label}))}">
            <button type="button" class="path-copy-button file-explorer-path-copy-panel" title="${esc(t('finder.toolbar.copyPath'))}" aria-label="${esc(t('finder.toolbar.copyPath'))}"></button>
            ${reloadButtonHtml}
          </div>
          <div class="file-explorer-toolbar-row file-explorer-primary-row">
            <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel" title="${esc(t('finder.toolbar.syncTitle'))}" aria-label="${esc(t('finder.toolbar.syncTitle'))}" aria-pressed="true">${esc(t('finder.toolbar.syncLabel'))}</button>
            ${fileExplorerDiffSessionControlHtml(fileExplorerFinderTargetSession(), 'finder')}
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
        </div>` : view === 'differ' ? `<div class="file-explorer-toolbar"><div class="file-explorer-toolbar-row file-explorer-primary-row">${fileExplorerDiffSessionControlHtml(fileExplorerSessionFilesTargetSession(), 'differ')}</div></div>` : '';
  const panel = createFramedPanel({
    item,
    className: `panel file-explorer-panel file-explorer-${view}`,
    dataset: {panelItem: item, fileExplorerView: view},
    frame: {
      headClass: 'file-explorer-head',
      controlsHtml: virtualPanelInnerControlsHtml(item),
      headAfterTabsHtml: finderToolbarHtml,
      bodyClass: 'file-explorer-pane',
      bodyHtml: `<div class="file-explorer-tree-panel" role="tree" tabindex="0"></div>
        <div class="file-explorer-changes-resizer" data-file-explorer-changes-resizer title="${esc(t('finder.toolbar.resize'))}"></div>
        <div class="file-explorer-changes-panel" data-file-explorer-changes></div>`,
    },
    bind: bindChangesPanel,
  });
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
    copyCurrentFileExplorerPath({button: event.currentTarget});
  });
  bindFileExplorerHeaderActions(panel);
  if (view === 'finder') {
    bindFileExplorerPathInput(panel.querySelector('.file-explorer-path-inline'));
  }
  if (view !== 'finder') bindFileExplorerChangesResizer(panel);
  applyFileExplorerPanelView(panel, item);
  if (view === 'finder') {
    panel.classList.toggle('file-explorer-root-mode-sync', fileExplorerRootMode === 'sync');
    panel.classList.toggle('file-explorer-root-mode-fixed', fileExplorerRootMode !== 'sync');
    syncFileExplorerSessionControlVisibility(panel);
    renderFileExplorerRootModeControls();
    startFinderPanelListing(panel);
  } else {
    renderFileExplorerChangesPanel(panel);
  }
  if (view !== 'finder') ensureSessionFilesSurfaceLoaded(view, {fetchEvenWhenPush: true});
  return panel;
}

// Finder's direct listing is the first-paint owner. Session-files can run Git history, status, and
// numstat work for every repository in a session; starting it beside a cold listing can occupy both
// Finder workers and leave the user staring at an empty tree. Start that backfill only after the
// listing has mounted, while still starting it after a failed listing so the session surface reports
// its own result independently.
function startFinderPanelListing(panel, options = {}) {
  const firstPaint = !panel?.querySelector?.('.file-tree-row[data-path]');
  if (firstPaint) beginFinderSessionFilesDiffBackfill();
  const listing = refreshFileExplorerPanelTree(panel, {...options, deferSessionFilesDiff: firstPaint});
  void listing.then(async () => {
      // Keep the fast directory rows on screen for one complete browser paint. This also applies
      // to a warm session-files payload: cached +/- totals are slower metadata, so they must not
      // change the first-paint result merely because another owner finished earlier.
      scheduleAfterFileExplorerFirstPaint(() => {
        finishFinderFastPaint();
      });
      if (options.preserveExpanded !== false) {
        const root = currentFileExplorerRoot();
        const expandedPaths = fileExplorerExpandedPathsForRoot(root);
        if (expandedPaths.length) {
          const syncGeneration = fileExplorerRootMode === 'sync' ? fileExplorerSyncState.generation : undefined;
          await restoreFileExplorerExpandedPaths(expandedPaths, root, {
            generation: syncGeneration,
            isCurrent: () => currentFileExplorerRoot() === root,
          });
        }
      }
    }).catch(error => {
      scheduleAfterFileExplorerFirstPaint(() => {
        finishFinderFastPaint();
      });
      console.warn('Finder initial listing failed', error);
    },
  );
  return listing;
}

async function refreshFileExplorerPanelTree(panel, options = {}) {
  const observabilityJourney = options.observabilityJourney || newFinderUsableJourney();
  const view = fileExplorerViewForItem(panel?.dataset?.panelItem) || 'finder';
  const treeEl = panel.querySelector('.file-explorer-tree-panel');
  const pathEl = panel.querySelector('.file-explorer-path-inline');
  const hiddenBtn = panel.querySelector('.file-explorer-hidden-toggle-panel');
  const dateBtn = panel.querySelector('[data-file-explorer-tree-dates]');
  const sortSelect = panel.querySelector('[data-file-explorer-tree-sort]');
  if (!treeEl) return;
  const syncGeneration = fileExplorerSyncState.generation;
  const root = normalizeDirectoryPath(fileExplorerRoot || homePath || '/');
  setFileExplorerPathElementValue(pathEl, root);
  setFileExplorerPathError(pathEl);
  renderFileExplorerRootModeControls();
  syncFileExplorerHiddenButton(hiddenBtn);
  syncFileExplorerTreeDateButton(dateBtn);
  if (sortSelect && sortSelect.value !== fileExplorerTreeSortModeForView(view)) sortSelect.value = fileExplorerTreeSortModeForView(view);
  const pushCanSupply = clientPushCanSupplyData();
  // Under the live push transport an ALREADY-PAINTED tree is kept current by incremental
  // filesystem-push deltas (which preserve scroll and expansion), so we must not re-fetch and
  // re-render the whole tree here — that is the reset-to-top full rebuild. But the FIRST paint
  // must list the current root's directory directly, regardless of Sync or of whether any push
  // has arrived yet, so the Finder is never empty on open. `root` is the current path
  // (`fileExplorerRoot || homePath`), never a session-derived root, so this does not follow a
  // session when Sync is off.
  const treeAlreadyPainted = treeEl.childElementCount > 0;
  if (pushCanSupply && !options.entries && options.force !== true && treeAlreadyPainted) {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    return;
  }
  const entries = options.root === root && Array.isArray(options.entries)
    ? options.entries
    : await fetchDirectory(root);
  if (!entries) return;
  if (root !== normalizeDirectoryPath(currentFileExplorerRoot())
      || (fileExplorerRootMode === 'sync' && syncGeneration !== fileExplorerSyncState.generation)) return;
  const publication = activeFileExplorerSyncPublication(root);
  if (publication) {
    await publication.publish(new Map(options.entriesByDir || []).set(root, entries));
    return;
  }
  renderTreeChildren(treeEl, root, entries, 0, {
    entriesByDir: options.entriesByDir,
    renderTransaction: options.renderTransaction || fileExplorerSyncState.renderTransaction,
    view: 'finder',
    deferSessionFilesDiff: options.deferSessionFilesDiff === true,
  });
  scheduleFinderUsableObservation(
    treeEl,
    entries,
    observabilityJourney,
  );
  updateFileExplorerCurrentFileHighlight({yieldToInput: true});
  if (pushCanSupply && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function renderFileExplorerChangesPanel(panel, options = {}) {
  const changes = panel?.querySelector?.('[data-file-explorer-changes]');
  if (!changes) return;
  const view = fileExplorerViewForItem(panel.dataset.panelItem) || 'finder';
  if (view === 'finder') return;
  if (view === 'differ' && !fileExplorerSessionFilesPaneIsVisible()) return;
  if (view === 'tabber') {
    if (options.force === true || !changes.querySelector('.changes-groups')) {
      replaceChangesStaticHtml(changes, fileExplorerChangesPanelStaticHtml({view}));
    }
    renderTabberTree(changes.querySelector('.changes-groups'));
    bindTabberPanel(panel);
    return;
  }
  const payload = fileExplorerSessionFilesState.payload;
  const streamedPayload = payload === fileExplorerSessionFilesState.payload
    && fileExplorerSessionFilesState.streamRepos instanceof Map;
  const files = options.changedRepo || streamedPayload
    ? []
    : (Array.isArray(payload?.files) ? payload.files : []);
  const loading = sessionFilesPanelIsLoading(payload, []);
  const groups = changes.querySelector('.changes-groups');
  if (options.changedRepo && groups) {
    renderChangesGroups(groups, [], {
      payload: fileExplorerSessionFilesState.payload,
      compact: false,
      loading,
      includeEmptyRepoSections: true,
      view,
      changedRepo: options.changedRepo,
      yieldToInput: true,
    });
  } else if (options.force === true || !activeChangesControl(panel)) {
    renderChangesRoot(
      changes,
      fileExplorerChangesPanelStaticHtml({view, full: true, deferPreparation: true}),
      files,
      {
        payload: fileExplorerSessionFilesState.payload,
        compact: false,
        loading,
        includeEmptyRepoSections: true,
        view,
        changedRepo: options.changedRepo,
        yieldToInput: true,
        deferPreparation: true,
      },
      {force: options.force === true},
    );
  }
  syncFileExplorerDiffSessionControls();
  bindChangesPanel(panel);
  syncFileExplorerChangesCollapseButtons();
}

function renderFileExplorerChangesPanels(options = {}) {
  if (!itemInLayout(differItemId) && !itemInLayout(tabberItemId)) return;
  const requestedView = options.view ? normalizeFileExplorerView(options.view) : '';
  for (const panel of document.querySelectorAll('.file-explorer-panel')) {
    if (requestedView && fileExplorerViewForItem(panel.dataset.panelItem) !== requestedView) continue;
    renderFileExplorerChangesPanel(panel, options);
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
    startFinderPanelListing(panel, {preserveExpanded: true, preserveScroll: true});
    return true;
  }
  renderFileExplorerChangesPanel(panel, {force: true});
  if (view === 'tabber') {
    fetchTabberActivity();
    return true;
  }
  ensureSessionFilesSurfaceLoaded(view, {fetchEvenWhenPush: true});
  return true;
}

function bindFileExplorerChangesResizer(panel) {
  const handle = panel?.querySelector?.('[data-file-explorer-changes-resizer]');
  const pane = panel?.querySelector?.('.file-explorer-pane');
  if (!handle || !pane) return null;
  return bindScopedOnce(handle, 'file-explorer-changes-resizer', scope => {
  scope.ownEvent('pointerdown', handle, 'pointerdown', event => {
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
  });
}

const fileEditorPreviewPropagationFrames = new Map();

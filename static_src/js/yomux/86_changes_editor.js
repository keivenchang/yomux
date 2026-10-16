function scheduleFileEditorPreviewPropagation(panel, path) {
  const key = String(path || '');
  if (!key || fileEditorPreviewPropagationFrames.has(key)) return;
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
  const frame = schedule(() => {
    fileEditorPreviewPropagationFrames.delete(key);
    const state = fileEditorStateForItem(path, fileEditorItemFor(path));
    if (!state || state.kind !== 'text') return;
    renderLinkedFilePreviewPanels(panel, path, state.content);
    updateFilePreviewPopout(path, state.content);
  });
  fileEditorPreviewPropagationFrames.set(key, frame);
}

function handleFileEditorContentChanged(panel, path, content, options = {}) {
  const state = fileEditorPanelState(panel);
  if (!state || state.kind !== 'text' || state.historical === true) return;
  const nextContent = String(content ?? '');
  if (nextContent !== state.content) noteTabberItemActivity(fileEditorPanelItem(panel));
  state.content = nextContent;
  state.contentOwnerPanel = panel;
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
  if (options.skipPreviewPanel !== panel?.querySelector?.('.file-editor-preview-pane-panel')) {
    if (panel?._pmView && options.sourceSurface !== 'view-editor') syncProseMirrorPanelSource(panel, path, state);
    else if (options.sourceSurface !== 'view-editor') renderFileEditorPreviewSurface(panel, panel.querySelector('.file-editor-preview-pane-panel'), path, state.content);
  }
  if (panel?._pmView && options.sourceSurface !== 'view-editor') syncProseMirrorPanelSource(panel, path, state);
  if (options.sourceSurface === 'view-editor') syncCodeMirrorToCanonicalPanels(path, state.content, panel, 'view-editor');
  else if (panel?._cmView && options.previewEdit !== true) syncCodeMirrorToCanonicalPanels(path, state.content, panel, 'text-editor');
  if (options.previewEdit === true) scheduleFileEditorPreviewPropagation(panel, path);
  else {
    renderLinkedFilePreviewPanels(panel, path, state.content);
    updateFilePreviewPopout(path, state.content);
  }
  scheduleFileEditorSplitScrollSync(panel, 'editor');
  const item = fileEditorPanelItem(panel);
  if (item && panel?.contains?.(document.activeElement)) {
    scheduleFileExplorerActiveTabSync(item, {explicit: true});
  }
  if (state.externalChanged && !state.externalChangeEditPrompted && !fileEditorSelfWritePending(path)) {
    promptExternalChangeBeforeEditing(path, panel);
  }
  if (state.dirty && options.deferAutosave !== true) scheduleFileAutosave(path);
  else clearFileAutosaveTimer(path);
  if (dirtyChanged) {
    renderSessionButtons();
    renderPaneTabStrips();
  }
}

async function enterFileEditorDiffMode(path, panel, item) {
  const state = fileEditorStateForItem(path, item);
  if (!state || state.kind !== 'text') return;
  if (panel?._pmView) flushProseMirrorSource(panel, path, {preserveTerminalNewline: true});
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
  const loadPromise = refreshOpenFileDiff(path, {item, state, silent: true, renderOnComplete: false});
  renderFileEditorPanel(panel, item);
  await loadPromise;
  const current = fileEditorStateForItem(path, item);
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
                  title: t('editor.mode.textEdit'),
                  ariaLabel: t('editor.mode.textEdit'),
                }),
                toolbarButtonHtml({
                  action: 'editor-mode',
                  dataset: {editorMode: 'preview'},
                  html: '<span class="file-editor-icon file-editor-icon-eye" aria-hidden="true"></span>',
                  title: t('editor.mode.viewEdit'),
                  ariaLabel: t('editor.mode.viewEdit'),
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
              className: 'file-editor-upload-panel',
              action: 'editor-upload',
              label: '↑',
              title: t('pref.section.uploads'),
              ariaLabel: t('pref.section.uploads'),
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
  setFileEditorLocalizedLabel(panel, '.file-editor-upload-panel', 'pref.section.uploads');
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
  const panel = createFramedPanel({
    item,
    className: 'panel file-editor-panel',
    dataset: {filePath: path, layoutItem: item},
    frame: {
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
           <div class="file-editor-codemirror-panel" data-editor-surface="text-editor" hidden></div>
          <pre class="file-editor-raw-panel" hidden><code></code></pre>
           <div class="file-editor-preview-pane-panel markdown-body" data-editor-surface="view-editor" hidden></div>
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
    },
  });
  panel.addEventListener('click', event => {
    if (event.target?.closest?.('[data-editor-surface], .file-editor-image-panel')) noteTabberItemActivity(item);
    if (event.defaultPrevented) return;
    if (event.target?.closest?.('button, a, input, textarea, select, [data-diff-ref-input]')) return;
    scheduleFileExplorerActiveFileReveal(path, {explicit: true});
  });
  delegate(panel, 'pointerdown', 'button', event => event.stopPropagation());
  bindActionDispatcher(panel, {
    'editor-save': () => saveFileEditor(path, panel),
    'editor-reload': () => {
      if (fileEditorPanelState(panel)?.historical === true) return false;
      noteTabberItemActivity(item);
      return reloadOpenFileFromDisk(path);
    },
    'editor-upload': () => openFileUploadChooserForEditor(panel, path),
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
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), fileEditorPanelState(panel), panel);
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
      if (fileEditorPanelState(panel)?.historical === true) return;
      if (openFilePreviewPopout(path, panel)) {
        setFileEditorViewMode(path, 'edit', item);
        renderFileEditorPanel(panel, item);
      }
    },
    'editor-theme': () => cycleEditorThemeMode({includeVanilla: fileEditorPanelState(panel)?.kind === 'text'}),
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
  panel.addEventListener('pointerdown', event => {
    if (event.target.closest?.('.file-editor-preview-pane-panel')) rememberFileEditorSearchSurface(panel, 'preview');
    else if (event.target.closest?.('.file-editor-codemirror-panel')) rememberFileEditorSearchSurface(panel, 'editor');
  });
  panel.addEventListener('focusin', event => {
    if (event.target.closest?.('.file-editor-preview-pane-panel')) rememberFileEditorSearchSurface(panel, 'preview');
    else if (event.target.closest?.('.file-editor-codemirror-panel')) rememberFileEditorSearchSurface(panel, 'editor');
  });
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
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), fileEditorPanelState(panel), panel);
    }
  });
  previewFindPanel?.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closePreviewFind(panel);
      updateEditorFindButton(panel.querySelector('.file-editor-find-panel'), fileEditorPanelState(panel), panel);
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
  releaseRawFileMediaSources(imagePane);
  if (imagePane?._imageResizeObserver) {
    imagePane._imageResizeObserver.disconnect();
    imagePane._imageResizeObserver = null;
  }
  if (typeof disconnectPreviewZoomSurface === 'function') disconnectPreviewZoomSurface(imagePane, {resetClasses: true});
}

function renderFileEditorImagePane(imagePane, path, state, status) {
  if (!imagePane) return;
  const version = rawFileMediaVersion(state);
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
      status(`${img.naturalWidth}x${img.naturalHeight}`, '');
    };
    imagePane.dataset.imagePath = path;
    imagePane.dataset.imageVersion = version;
    installPreviewZoomSurface(imagePane, img, zoomOptions);
    status(t('common.loading'), '');
    const showFailureAfterUserScroll = async (key, detail, statusMessage) => {
      const applied = await scheduleRawMediaFallbackAfterUserScroll(imagePane, img, key, () => (
        fileEditorEmptyState(t('preview.image.loadFailed'), detail)
      ));
      if (applied) status(statusMessage, 'error');
      return applied;
    };
    imagePane._previewAsync = installRawFileMediaSource(img, path, {
      params: {v: version},
      isCurrent: () => imagePane.dataset.imagePath === path && imagePane.dataset.imageVersion === version && imagePane.contains(img),
      onFailure: error => showFailureAfterUserScroll(
        'image-pane-failure',
          userMessageText(error, t('preview.image.loadFailedDetail', {size: formatFileSize(MAX_FILE_PREVIEW_BYTES)})),
        userMessageText(error, t('preview.image.loadFailedStatus')),
      ),
      onDecodeFailure: () => showFailureAfterUserScroll(
        'image-pane-decode-failure',
        t('preview.image.loadFailedDetail', {size: formatFileSize(MAX_FILE_PREVIEW_BYTES)}),
        t('preview.image.loadFailedStatus'),
      ),
    });
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

// One passive-update owner for a live CodeMirror pane. A refresh or focus may
// observe a saved search selection, but it must keep the viewport the user has
// already scrolled to; only explicit navigation is allowed to reveal a target.
function preserveFileEditorPanelPassiveViewState(item, panel, options = {}) {
  const view = panel?._cmView;
  const scrollDOM = view?.scrollDOM;
  if (!view) return false;
  if (!scrollDOM) {
    if (options.focus === true) view.focus?.();
    return true;
  }
  const update = () => {
    if (options.focus === true) view.focus?.();
  };
  if (typeof updateCodeMirrorViewPreservingState === 'function') {
    updateCodeMirrorViewPreservingState(view, (selection, scrollSnapshot) => {
      update();
      if (scrollSnapshot) view.dispatch({effects: scrollSnapshot});
      return selection;
    }, {preserveSelection: false});
  } else {
    const scrollTop = scrollDOM.scrollTop;
    const scrollLeft = scrollDOM.scrollLeft;
    update();
    scrollDOM.scrollTop = scrollTop;
    scrollDOM.scrollLeft = scrollLeft;
  }
  captureFileEditorPanelViewState(item, panel);
  return true;
}

function focusFileEditorPanelIfReady(panel, item) {
  if (!autoFocusCanFollowCursor()) {
    pendingFileEditorFocus.delete(item);
    return false;
  }
  if (!pendingFileEditorFocus.has(item) || focusedPanelItem !== item) return false;
  if (panel?._cmView) {
    preserveFileEditorPanelPassiveViewState(item, panel, {focus: true});
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
  if (!fileEditorPanelViewStateCaptureHasLayout(panel, scrollDOM)) return;
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
  // A live pane owns its current viewport even when it is not focused: the user can scroll it and
  // a queued capture has not necessarily reached fileEditorViewState yet. Only a just-created view
  // or explicit disk replacement may replay saved selection/scroll state.
  if (options.restoreFocused !== true && options.restoreFresh !== true) {
    preserveFileEditorPanelPassiveViewState(item, panel);
    return;
  }
  const docLength = view.state?.doc?.length || 0;
  const anchor = Math.max(0, Math.min(docLength, Number(state.anchor || 0)));
  const head = Math.max(0, Math.min(docLength, Number(state.head || anchor)));
  scrollDOM.scrollTop = Number(state.scrollTop || 0);
  scrollDOM.scrollLeft = Number(state.scrollLeft || 0);
  const scrollSnapshot = state.scrollSnapshot || (typeof view.scrollSnapshot === 'function' ? view.scrollSnapshot() : null);
  try {
    view.dispatch({
      selection: {anchor, head},
      ...(scrollSnapshot ? {effects: scrollSnapshot} : {}),
    });
  } catch (_) {
    try { view.dispatch({selection: {anchor, head}}); } catch (_) {}
  }
}
